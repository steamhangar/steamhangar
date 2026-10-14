"""WP TOOL-APPS-2 (ADR-0022 §2): ``group_size_bytes`` on the game endpoints.

The union of the depots of a top-level row and every row grouped under it,
each depot counted once. Real depot directories on disk, no network.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from vault_api.config import Settings
from vault_api.main import create_app

AUTH = {"X-Api-Key": TEST_API_KEY}

HD2, PKG_A, PKG_B, OUTSIDE, TOOL, UNMAPPED = 553850, 2506210, 2506220, 730, 228980, 999
GAME_CHILD = 4000001


def _make(tmp_path: Path, *, oracle: bool = False) -> tuple[TestClient, Path, Settings]:
    root = tmp_path / "cache"
    (root / "depot").mkdir(parents=True)
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(root),
        log_level="INFO",
        manifest_oracle="steamcmd_api" if oracle else "",
    )
    return TestClient(create_app(settings)), root, settings


def _depot(root: Path, depotid: int, size: int) -> None:
    chunk = root / "depot" / str(depotid) / "chunk" / "aa"
    chunk.parent.mkdir(parents=True, exist_ok=True)
    chunk.write_bytes(b"1" * size)


def _map(client: TestClient, depotid: int, appid: int) -> None:
    response = client.put(f"/v1/mapping/{depotid}", json={"appid": appid, "app_name": None}, headers=AUTH)
    assert response.status_code == 200, response.text


def _classify(settings: Settings, appid: int, app_type: str | None, parent: int | None,
              source: str = "store") -> None:
    conn = sqlite3.connect(settings.db_path)
    try:
        conn.execute(
            "INSERT INTO app_classification (appid, source, app_type, parent_appid, linked, "
            "name, checked_at) VALUES (?, ?, ?, ?, 0, NULL, 'x')",
            (appid, source, app_type, parent),
        )
        conn.commit()
    finally:
        conn.close()


def _library(tmp_path: Path, *, oracle: bool = False):
    client, root, settings = _make(tmp_path, oracle=oracle)
    layout = {
        553851: ([HD2], 100),
        553852: ([HD2, PKG_A], 200),  # shared inside the group: counted once
        2506211: ([PKG_A], 50),
        2506221: ([PKG_B], None),  # mapped, nothing on disk
        553853: ([HD2, GAME_CHILD], 7),
        4000003: ([GAME_CHILD], 3),  # a game-typed child is still grouped (display)
        731: ([OUTSIDE], 10),
        228982: ([TOOL], 1000),
    }
    for depotid, (owners, size) in layout.items():
        if size is not None:
            _depot(root, depotid, size)
        for owner in owners:
            _map(client, depotid, owner)
    conn = sqlite3.connect(settings.db_path)
    try:
        conn.execute("INSERT INTO apps (appid, status) VALUES (?, 'idle')", (UNMAPPED,))
        conn.commit()
    finally:
        conn.close()
    _classify(settings, HD2, "game", None)
    _classify(settings, PKG_A, "dlc", HD2)
    _classify(settings, PKG_B, "dlc", HD2)
    _classify(settings, GAME_CHILD, "game", HD2)
    _classify(settings, TOOL, "dlc", HD2)  # fixed tool list: never grouped
    return client, root, settings


def _games(client: TestClient) -> dict[int, dict]:
    return {g["appid"]: g for g in client.get("/v1/games", headers=AUTH).json()}


def test_group_size_is_the_union_of_member_depots_each_once(tmp_path: Path) -> None:
    client, _root, _settings = _library(tmp_path)
    games = _games(client)
    # 553851 + 553852 (once) + 2506211 + 553853 + 4000003; the tool app's depot is not in it.
    assert games[HD2]["group_size_bytes"] == 100 + 200 + 50 + 7 + 3
    # Never the sum of the members' sizes (that would count 553852 and 553853 twice).
    member_sum = sum(games[a]["size_bytes"] or 0 for a in (HD2, PKG_A, PKG_B, GAME_CHILD))
    assert member_sum > games[HD2]["group_size_bytes"]


def test_grouped_rows_have_no_group_size_and_ungrouped_rows_equal_size_bytes(tmp_path: Path) -> None:
    client, _root, _settings = _library(tmp_path)
    games = _games(client)
    for child in (PKG_A, PKG_B, GAME_CHILD):
        assert games[child]["parent_appid"] == HD2
        assert games[child]["group_size_bytes"] is None
    assert games[OUTSIDE]["group_size_bytes"] == games[OUTSIDE]["size_bytes"] == 10
    assert games[TOOL]["group_size_bytes"] == games[TOOL]["size_bytes"] == 1000
    assert games[UNMAPPED]["group_size_bytes"] is None and games[UNMAPPED]["size_bytes"] is None


def test_detail_endpoint_agrees_with_the_list(tmp_path: Path) -> None:
    client, _root, _settings = _library(tmp_path)
    games = _games(client)
    for appid in (HD2, PKG_A, OUTSIDE, TOOL, UNMAPPED):
        detail = client.get(f"/v1/games/{appid}", headers=AUTH).json()
        assert detail["group_size_bytes"] == games[appid]["group_size_bytes"], appid


def test_group_of_unwritten_depots_is_null(tmp_path: Path) -> None:
    client, _root, settings = _make(tmp_path)
    _map(client, 11, 10)
    _map(client, 21, 20)
    _classify(settings, 20, "dlc", 10)
    games = _games(client)
    assert games[10]["group_size_bytes"] is None


def test_oracle_children_count_only_while_the_oracle_is_on(tmp_path: Path) -> None:
    client, root, settings = _make(tmp_path)
    _depot(root, 11, 5)
    _depot(root, 21, 6)
    _map(client, 11, 10)
    _map(client, 21, 20)
    _classify(settings, 20, "dlc", 10, source="steamcmd_api")
    assert _games(client)[10]["group_size_bytes"] == 5
    on = Settings(
        vault_api_key=TEST_API_KEY, db_path=settings.db_path, cache_root=settings.cache_root,
        log_level="INFO", manifest_oracle="steamcmd_api",
    )
    on_client = TestClient(create_app(on))
    assert _games(on_client)[10]["group_size_bytes"] == 11
    assert on_client.get("/v1/games/10", headers=AUTH).json()["group_size_bytes"] == 11
