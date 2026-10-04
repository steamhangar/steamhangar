"""Steam tool apps are never prefilled (WP API-FIX-4).

Production, 2026-10-04: every Windows agent reports app 228980 ("Steamworks
Common Redistributables") as installed. The scheduler enqueued a prefill for
it on every sweep, SteamPrefill cannot prefill it, and the library showed
"App 228980 / Failed / Retry download". User decision (Weg A): vault-api
keeps a fixed tool-app list (``vault_api/tool_apps.py``) and never prefills
those apps; agents keep reporting the truth.

Guarantees pinned here, each by name:

1. the list itself: 228980 with its display name, nothing else;
2. the scheduler drops tool apps from the installed source and still keeps
   every real game;
3. the scheduler drops tool apps from the cached source (WP 4d sweep);
4. ``maybe_sweep`` enqueues no job for a tool app and logs that it skipped it;
5. the miss trigger skips a tool app without using up its per-sweep cap;
6. ``POST /v1/prefill/cached`` leaves tool apps out;
7. ``POST /v1/prefill`` answers ``422`` with a string detail for a body naming
   a tool app and queues nothing, not even the ordinary apps in the body;
8. ``jobs.enqueue_prefill`` refuses a tool app and writes nothing;
9. ``GET /v1/games`` / ``GET /v1/games/{appid}`` carry ``tool_app`` and
   ``tool_app_name``, additive, with ``status`` left as the raw truth.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from tests.test_event_sweep import event_line, write_log
from tests.test_event_sweep import make_settings as make_sweep_settings
from tests.test_event_sweep import moment
from tests.test_scheduler import (
    insert_report,
    local,
    make_settings,
    utc_iso,
    write_cached_depot,
)
from vault_api import event_sweep, jobs, tool_apps
from vault_api.db import get_connection, init_db
from vault_api.mapping import upsert_mapping
from vault_api.scheduler import compute_targets, maybe_sweep

AUTH = {"X-Api-Key": TEST_API_KEY}

#: Literal, not read from the module under test (docs/LEARNINGS.md: pins
#: compare against fixed points, never against the constant they guard).
REDIST = 228980
REDIST_NAME = "Steamworks Common Redistributables"


@pytest.fixture
def conn(tmp_path: Path):
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    connection = get_connection(db_path)
    try:
        yield connection
    finally:
        connection.close()


def _job_appids(conn: sqlite3.Connection) -> list[int]:
    return [
        int(row["appid"])
        for row in conn.execute("SELECT appid FROM jobs WHERE type = 'prefill' ORDER BY id")
    ]


# ---------------------------------------------------------------------------
# 1. The list
# ---------------------------------------------------------------------------


def test_the_tool_app_list_is_exactly_the_steamworks_redistributables() -> None:
    assert set(tool_apps.TOOL_APPS) == {228980}
    entry = tool_apps.get_tool_app(228980)
    assert entry is not None
    assert entry.appid == 228980
    assert entry.name == "Steamworks Common Redistributables"
    assert entry.reason  # stated, for logs and docs


def test_ordinary_apps_are_not_tool_apps() -> None:
    for appid in (440, 730, 107100, 228981, 228979, 0):
        assert tool_apps.is_tool_app(appid) is False
        assert tool_apps.get_tool_app(appid) is None
    assert tool_apps.is_tool_app(228980) is True


def test_the_list_cannot_be_changed_at_runtime() -> None:
    with pytest.raises(TypeError):
        tool_apps.TOOL_APPS[440] = tool_apps.ToolApp(440, "TF2", "no")  # type: ignore[index]


def test_split_tool_apps_separates_and_sorts() -> None:
    ordinary, tools = tool_apps.split_tool_apps([730, 228980, 440, 228980])
    assert ordinary == {440, 730}
    assert tools == (228980,)


def test_the_reject_detail_names_the_app_and_why() -> None:
    detail = tool_apps.reject_detail(228980)
    assert detail == (
        "App 228980 (Steamworks Common Redistributables) is a Steam tool "
        "package, it is cached together with the games that use it. It is "
        "never prefilled on its own."
    )


# ---------------------------------------------------------------------------
# 2./3. The scheduler's target set
# ---------------------------------------------------------------------------


def test_the_scheduler_skips_an_installed_tool_app_and_keeps_real_games(
    conn: sqlite3.Connection,
) -> None:
    """MUTATION TARGET: drop the installed-source filter in compute_targets
    and 228980 comes back as a target."""
    insert_report(conn, "gaming-pc", [440, REDIST, 730], utc_iso(9))
    insert_report(conn, "deck", [REDIST], utc_iso(9))

    result = compute_targets(conn, local(10), stale_after_days=7)

    assert result.appids == [440, 730]
    assert result.skipped_tool_appids == (REDIST,)
    assert sorted(result.included_clients) == ["deck", "gaming-pc"]


def test_a_vault_without_tool_apps_reports_none_skipped(conn: sqlite3.Connection) -> None:
    insert_report(conn, "gaming-pc", [440], utc_iso(9))

    result = compute_targets(conn, local(10), stale_after_days=7)

    assert result.appids == [440]
    assert result.skipped_tool_appids == ()


def test_the_cached_sweep_skips_a_tool_app_with_its_own_cache_content(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    """MUTATION TARGET: drop the cached-source filter and 228980 comes back
    as a cached-only target. 228990 is a depot only 228980 maps, so the
    shared-depot rules alone would count it as 228980's cache content."""
    cache_root = tmp_path / "cache"
    write_cached_depot(cache_root, 228990)
    write_cached_depot(cache_root, 441)
    upsert_mapping(conn, depotid=228990, appid=REDIST, name=None)
    upsert_mapping(conn, depotid=441, appid=440, name="TF2")

    result = compute_targets(
        conn, local(10), stale_after_days=7,
        include_cached=True, cache_root=str(cache_root),
    )

    assert result.appids == [440]
    assert result.cached_only_appids == (440,)
    assert result.skipped_tool_appids == (REDIST,)


def test_a_tool_app_named_by_both_sources_is_reported_once(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    cache_root = tmp_path / "cache"
    write_cached_depot(cache_root, 228990)
    upsert_mapping(conn, depotid=228990, appid=REDIST, name=None)
    insert_report(conn, "gaming-pc", [REDIST, 440], utc_iso(9))

    result = compute_targets(
        conn, local(10), stale_after_days=7,
        include_cached=True, cache_root=str(cache_root),
    )

    assert result.appids == [440]
    assert result.cached_only_appids == ()
    assert result.skipped_tool_appids == (REDIST,)


# ---------------------------------------------------------------------------
# 4. The sweep end to end
# ---------------------------------------------------------------------------


def test_a_sweep_enqueues_no_job_for_a_tool_app(
    tmp_path: Path, conn: sqlite3.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    """The production scenario: the agent reports a game and 228980, the
    cached sweep is on (the shipped default) and 228980 has cache content."""
    cache_root = tmp_path / "cache"
    write_cached_depot(cache_root, 228990)
    upsert_mapping(conn, depotid=228990, appid=REDIST, name=None)
    insert_report(conn, "gaming-pc", [440, REDIST], utc_iso(9))
    settings = replace(
        make_settings(tmp_path, cache_root=cache_root), sweep_include_cached=True
    )

    with caplog.at_level(logging.INFO, logger="vault_api.scheduler"):
        result = maybe_sweep(conn, settings, local(10))

    assert result.swept is True
    assert result.targets == (440,)
    assert result.enqueued == (440,)
    assert result.skipped_tool_appids == (REDIST,)
    assert _job_appids(conn) == [440]
    assert "Steam tool apps never prefilled, cached with their games: 228980" in caplog.text


# ---------------------------------------------------------------------------
# 5. The miss trigger
# ---------------------------------------------------------------------------


def test_the_miss_trigger_skips_a_tool_app_without_using_the_cap(
    conn: sqlite3.Connection, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """MUTATION TARGET: drop the tool-app skip and 228980 is enqueued (and,
    with a cap of 1, 440 is then dropped by the cap)."""
    settings = make_sweep_settings(tmp_path, cap=1)
    upsert_mapping(conn, depotid=228990, appid=REDIST, name=None)
    upsert_mapping(conn, depotid=70403, appid=440, name=None)
    write_log(
        Path(settings.event_log_path),
        event_line(depot="228990"),
        event_line(depot="70403"),
    )

    with caplog.at_level(logging.INFO, logger="vault_api.event_sweep"):
        outcome = event_sweep.sweep_once(conn, settings, moment())

    assert outcome.skipped_tool == (REDIST,)
    assert "miss trigger skipped 1 Steam tool app(s)" in caplog.text
    assert "228980" in caplog.text
    assert outcome.enqueued == (440,)
    assert outcome.dropped_by_cap == ()
    assert _job_appids(conn) == [440]
    # No cooldown row either: the app was never triggered.
    assert conn.execute(
        "SELECT COUNT(*) FROM miss_trigger_state WHERE appid = ?", (REDIST,)
    ).fetchone()[0] == 0


# ---------------------------------------------------------------------------
# 6./7. The endpoints
# ---------------------------------------------------------------------------


def _seed_mapping(client: TestClient, depotid: int, appid: int) -> None:
    response = client.put(
        f"/v1/mapping/{depotid}", json={"appid": appid, "app_name": None}, headers=AUTH
    )
    assert response.status_code == 200, response.text


def _seed_cache_bytes(client: TestClient, depotid: int) -> None:
    path = Path(client.app.state.settings.cache_root) / "depot" / str(depotid) / "chunk" / "a"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 32)


def _jobs(client: TestClient) -> list[dict]:
    return client.get("/v1/jobs", headers=AUTH).json()


def test_cached_prefill_leaves_a_tool_app_out(client: TestClient) -> None:
    """MUTATION TARGET: drop the filter in _select_appids_with_cache_content
    and 228980 is queued."""
    _seed_mapping(client, 228990, REDIST)
    _seed_cache_bytes(client, 228990)
    _seed_mapping(client, 441, 440)
    _seed_cache_bytes(client, 441)

    response = client.post("/v1/prefill/cached", headers=AUTH)

    assert response.status_code == 202
    assert [entry["appid"] for entry in response.json()] == [440]
    assert [job["appid"] for job in _jobs(client)] == [440]


def test_manual_prefill_of_a_tool_app_is_rejected_with_a_string_detail(
    client: TestClient,
) -> None:
    response = client.post("/v1/prefill", json={"appids": [REDIST]}, headers=AUTH)

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, str)  # same shape as PATCH /v1/settings's 422s
    assert "228980" in detail
    assert REDIST_NAME in detail
    assert "Steam tool package, it is cached together with the games that use it" in detail
    assert _jobs(client) == []


def test_a_bulk_body_naming_a_tool_app_queues_nothing(client: TestClient) -> None:
    """All or nothing: the ordinary app before the tool app in the body is
    not queued either (MUTATION TARGET: check inside the enqueue loop)."""
    response = client.post("/v1/prefill", json={"appids": [440, REDIST]}, headers=AUTH)

    assert response.status_code == 422
    assert _jobs(client) == []


def test_manual_prefill_of_an_ordinary_app_still_works(client: TestClient) -> None:
    response = client.post("/v1/prefill", json={"appids": [440]}, headers=AUTH)

    assert response.status_code == 202
    assert response.json()[0]["appid"] == 440
    assert response.json()[0]["deduplicated"] is False


# ---------------------------------------------------------------------------
# 8. The enqueue backstop
# ---------------------------------------------------------------------------


def test_enqueue_prefill_refuses_a_tool_app_and_writes_nothing(
    conn: sqlite3.Connection,
) -> None:
    """MUTATION TARGET: drop the backstop in jobs.enqueue_prefill."""
    with pytest.raises(tool_apps.ToolAppNotPrefillable) as raised:
        jobs.enqueue_prefill(conn, REDIST)

    assert raised.value.appid == REDIST
    assert isinstance(raised.value, ValueError)
    assert _job_appids(conn) == []
    assert conn.execute(
        "SELECT COUNT(*) FROM apps WHERE appid = ?", (REDIST,)
    ).fetchone()[0] == 0


# ---------------------------------------------------------------------------
# 9. The game endpoints
# ---------------------------------------------------------------------------


def test_games_flag_a_tool_app_and_keep_its_raw_status(client: TestClient) -> None:
    """The production card: an apps row left at ``error`` by the old failed
    jobs, reported installed. ``status`` stays the raw truth; the flag and
    the name are what the UIs render from."""
    _seed_mapping(client, 441, 440)
    conn = get_connection(client.app.state.settings.db_path)
    try:
        conn.execute(
            "INSERT INTO apps (appid, name, status) VALUES (?, NULL, 'error')", (REDIST,)
        )
        conn.commit()
    finally:
        conn.close()
    response = client.post(
        "/v1/agent/installed",
        json={"client_id": "gaming-pc", "appids": [440, REDIST]},
        headers=AUTH,
    )
    assert response.status_code == 200, response.text

    games = {g["appid"]: g for g in client.get("/v1/games", headers=AUTH).json()}

    redist = games[REDIST]
    assert redist["tool_app"] is True
    assert redist["tool_app_name"] == REDIST_NAME
    assert redist["name"] is None  # untouched apps-table value
    assert redist["status"] == "error"  # raw truth, the UIs decide
    assert [e["client_id"] for e in redist["installed_on"]] == ["gaming-pc"]

    game = games[440]
    assert game["tool_app"] is False
    assert game["tool_app_name"] is None

    detail = client.get(f"/v1/games/{REDIST}", headers=AUTH).json()
    assert detail["tool_app"] is True
    assert detail["tool_app_name"] == REDIST_NAME

    detail_440 = client.get("/v1/games/440", headers=AUTH).json()
    assert detail_440["tool_app"] is False
    assert detail_440["tool_app_name"] is None
