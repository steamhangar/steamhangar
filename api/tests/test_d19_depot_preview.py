"""WP D19-1a DEPOT-PREVIEW-1a: expected depots and size on the card (schema v32).

No test touches the network: the oracle answer is a synthetic fixture injected
through the service's ``fetch`` parameter, and every fetch is counted.

Pinned by name:

1. **Off unless both switches are on.** The oracle is ON by default, so the
   own switch must be what keeps a default install silent: no fetch happens
   with the oracle alone, nor with the own switch alone.
2. **Asked at most once per TTL.** A repeat open inside 24 h, a concurrent open
   and a repeat after a failure inside the back-off ask nothing; a burst of
   different apps is rate limited.
3. **An expectation.** The answer never reaches ``depot_app_map`` or the GC
   keep-set table, and an app with observed depots is never queried.
4. **The migration is additive.** A populated v31 file gains the tables.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from tests.test_oracle import enabled_settings, off_settings
from vault_api import depot_preview, oracle
from vault_api.db import SCHEMA_VERSION, get_connection, init_db
from vault_api.depot_preview import DepotPreviewService
from vault_api.main import create_app
from vault_api.mapping import upsert_mapping

AUTH = {"X-Api-Key": TEST_API_KEY}



def answer(appid: int = 1366540) -> bytes:
    document = {
        "status": "success",
        "data": {
            str(appid): {
                "appid": str(appid),
                "common": {"name": "Some Game", "type": "Game"},
                "depots": {
                    "branches": {"public": {"buildid": "99"}},
                    str(appid + 1): {
                        "manifests": {
                            "public": {"gid": "111", "size": "4680000000", "download": "1820000000"},
                            "beta": {"gid": "112", "size": "9", "download": "9"},
                        }
                    },
                    str(appid + 2): {"dlcappid": "777"},
                    str(appid + 3): {
                        "config": {"oslist": "macos"},
                        "manifests": {"public": {"gid": "113", "size": "5000", "download": "5000"}},
                    },
                    str(appid + 4): {
                        "config": {"oslist": "windows,linux"},
                        "manifests": {"public": {"gid": "114", "size": "1000", "download": "400"}},
                    },
                },
            }
        },
    }
    return json.dumps(document).encode()


class Counter:
    def __init__(self, payload: bytes | Exception | None = None) -> None:
        self.urls: list[str] = []
        self.payload: bytes | Exception = answer() if payload is None else payload

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class Clock:
    def __init__(self) -> None:
        self.now = datetime.now(timezone.utc)  # real time: stored rows carry the real store time
        self.t0 = self.now

    def __call__(self) -> datetime:
        return self.now


def make_client(tmp_path: Path, settings, fetch: Counter, clock: Clock | None = None):
    init_db(settings.db_path)
    app = create_app(settings)
    app.state.depot_preview = DepotPreviewService(
        run=lambda job: job(), fetch=fetch, clock=clock or Clock()
    )
    return TestClient(app)


def on(tmp_path: Path, **overrides):
    return enabled_settings(tmp_path, depot_preview_enabled=True, **overrides)


def preview(client: TestClient, appid: int = 1366540) -> dict:
    response = client.get(f"/v1/oracle/{appid}/preview", headers=AUTH)
    assert response.status_code == 200
    return response.json()


# --------------------------------------------------------------------------
# Gating: the privacy property
# --------------------------------------------------------------------------


def test_default_settings_ask_nothing_even_with_the_oracle_on(tmp_path: Path) -> None:
    fetch = Counter()
    client = make_client(tmp_path, enabled_settings(tmp_path), fetch)  # oracle on, own switch off
    body = preview(client)
    assert body["state"] == "off"
    assert fetch.urls == []


def test_the_own_switch_without_the_oracle_asks_nothing(tmp_path: Path) -> None:
    fetch = Counter()
    settings = dataclasses.replace(off_settings(tmp_path), depot_preview_enabled=True)
    client = make_client(tmp_path, settings, fetch)
    assert preview(client)["state"] == "off"
    assert fetch.urls == []


def test_the_shipped_default_is_off() -> None:
    from vault_api import config

    assert config.DEFAULT_DEPOT_PREVIEW is False
    assert config.Settings.__dataclass_fields__["depot_preview_enabled"].default is False


def test_the_preview_endpoint_requires_the_api_key(tmp_path: Path) -> None:
    client = make_client(tmp_path, on(tmp_path), Counter())
    assert client.get("/v1/oracle/1366540/preview").status_code in (401, 403)


# --------------------------------------------------------------------------
# Behaviour
# --------------------------------------------------------------------------


def test_opening_a_card_queries_once_and_returns_expected_depots(tmp_path: Path) -> None:
    fetch = Counter()
    client = make_client(tmp_path, on(tmp_path), fetch)
    body = preview(client)
    assert body["state"] == "ready"
    assert body["expected"] is True
    # Public branch only; the macOS-only depot is left out, the windows+linux one counts.
    assert [d["depotid"] for d in body["depots"]] == [1366541, 1366544]
    assert body["total_size_bytes"] == 4_680_000_000 + 1_000
    assert body["total_download_bytes"] == 1_820_000_000 + 400
    assert body["dlc_depots"] == [{"depotid": 1366542, "dlcappid": 777}]
    assert len(fetch.urls) == 1
    assert fetch.urls[0].endswith("/1366540")


def test_a_repeat_open_inside_the_ttl_asks_nothing(tmp_path: Path) -> None:
    fetch, clock = Counter(), Clock()
    client = make_client(tmp_path, on(tmp_path), fetch, clock)
    preview(client)
    clock.now = clock.t0 + timedelta(hours=23)
    again = preview(client)
    assert again["state"] == "ready"
    assert len(fetch.urls) == 1


def test_after_the_ttl_it_asks_again(tmp_path: Path) -> None:
    fetch, clock = Counter(), Clock()
    client = make_client(tmp_path, on(tmp_path), fetch, clock)
    preview(client)
    clock.now = clock.t0 + timedelta(hours=25)
    preview(client)
    assert len(fetch.urls) == 2


def test_a_failure_is_remembered_and_backs_off(tmp_path: Path) -> None:
    fetch = Counter(oracle.OracleError("boom"))
    clock = Clock()
    client = make_client(tmp_path, on(tmp_path), fetch, clock)
    first = preview(client)
    assert first["state"] == "unavailable"
    assert first["depots"] == [] and first["total_size_bytes"] is None
    clock.now = clock.t0 + timedelta(minutes=5)
    assert preview(client)["state"] == "unavailable"
    assert len(fetch.urls) == 1  # inside the back-off: no second query
    clock.now = clock.t0 + timedelta(minutes=31)
    fetch.payload = answer()
    assert preview(client)["state"] == "ready"
    assert len(fetch.urls) == 2


def test_an_unexpected_exception_in_the_fetcher_is_contained(tmp_path: Path) -> None:
    fetch = Counter(RuntimeError("weird"))
    client = make_client(tmp_path, on(tmp_path), fetch)
    assert preview(client)["state"] == "unavailable"


def test_garbage_answers_are_unavailable_not_errors(tmp_path: Path) -> None:
    garbage_answers = (
        b"not json",
        b"[]",
        json.dumps({"status": "success", "data": {}}).encode(),
        json.dumps({"status": "success", "data": {"1366540": {"appid": "1366540"}}}).encode(),
    )
    for index, garbage in enumerate(garbage_answers):
        directory = tmp_path / f"g{index}"
        directory.mkdir()
        client = make_client(directory, on(directory), Counter(garbage))
        assert preview(client)["state"] == "unavailable", garbage


def test_a_game_with_observed_depots_is_never_queried(tmp_path: Path) -> None:
    fetch = Counter()
    settings = on(tmp_path)
    client = make_client(tmp_path, settings, fetch)
    conn = get_connection(settings.db_path)
    try:
        upsert_mapping(conn, 441, 440, "Team Fortress 2")
        conn.commit()
    finally:
        conn.close()
    assert preview(client, 440)["state"] == "cached"
    assert fetch.urls == []


def test_the_answer_is_an_expectation_and_never_written_as_observed(tmp_path: Path) -> None:
    fetch = Counter()
    settings = on(tmp_path)
    client = make_client(tmp_path, settings, fetch)
    preview(client)
    conn = get_connection(settings.db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM depot_app_map").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM oracle_branch_manifests").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM apps").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM depot_preview").fetchone()[0] == 3
    finally:
        conn.close()
    # The oracle's own view of that app is still "never asked".
    view = client.get("/v1/oracle/1366540", headers=AUTH).json()
    assert view["checked_at"] is None


def test_forgetting_the_oracle_also_forgets_the_preview(tmp_path: Path) -> None:
    fetch = Counter()
    client = make_client(tmp_path, on(tmp_path), fetch)
    preview(client)
    assert client.delete("/v1/oracle/1366540", headers=AUTH).status_code == 204
    preview(client)
    assert len(fetch.urls) == 2


def test_a_burst_of_different_apps_is_rate_limited(tmp_path: Path) -> None:
    fetch = Counter()
    client = make_client(tmp_path, on(tmp_path), fetch)
    # Appid-specific answers are not needed: the parser checks the id, so use
    # a fetcher that echoes the requested id.
    def echo(url: str) -> bytes:
        fetch.urls.append(url)
        return answer(int(url.rsplit("/", 1)[1]))

    client.app.state.depot_preview = DepotPreviewService(
        run=lambda job: job(), fetch=echo, monotonic=lambda: 1000.0
    )
    states = [preview(client, 5000 + i)["state"] for i in range(depot_preview.RATE_LIMIT + 3)]
    assert states[: depot_preview.RATE_LIMIT] == ["ready"] * depot_preview.RATE_LIMIT
    assert states[depot_preview.RATE_LIMIT :] == ["unavailable"] * 3
    assert len(fetch.urls) == depot_preview.RATE_LIMIT


def test_a_concurrent_open_shares_the_running_query(tmp_path: Path) -> None:
    fetch = Counter()
    settings = on(tmp_path)
    init_db(settings.db_path)
    started: list = []
    service = DepotPreviewService(run=started.append, fetch=fetch)  # deferred runner
    conn = get_connection(settings.db_path)
    try:
        first = service.lookup(conn, 1366540, settings)
        second = service.lookup(conn, 1366540, settings)
        assert first.state == second.state == depot_preview.STATE_PENDING
        assert len(started) == 1
        started[0]()  # the background job finishes
        assert service.lookup(conn, 1366540, settings).state == depot_preview.STATE_READY
    finally:
        conn.close()
    assert len(fetch.urls) == 1


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------


def test_schema_is_at_least_v32() -> None:
    assert SCHEMA_VERSION >= 32


def _make_v31_database(db_path: str) -> None:
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        conn.executescript(
            """
            DROP TABLE depot_preview;
            DROP TABLE depot_preview_state;
            UPDATE schema_version SET version = 31;
            """
        )
        upsert_mapping(conn, 441, 440, "Team Fortress 2")
        conn.commit()
    finally:
        conn.close()


def test_a_copy_of_a_populated_v31_database_migrates_and_keeps_its_data(tmp_path: Path) -> None:
    original = str(tmp_path / "vault.db")
    _make_v31_database(original)
    copy = str(tmp_path / "copy.db")
    shutil.copy(original, copy)

    init_db(copy)
    init_db(copy)  # idempotent

    conn = get_connection(copy)
    try:
        (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
        app = conn.execute("SELECT name FROM apps WHERE appid = 440").fetchone()
        depot = conn.execute("SELECT depotid FROM depot_app_map WHERE appid = 440").fetchone()
        assert conn.execute("SELECT COUNT(*) FROM depot_preview_state").fetchone()[0] == 0
        depot_preview.store_outcome(
            conn,
            1366540,
            depot_preview.OUTCOME_OK,
            info=depot_preview.parse_preview(1366540, answer()),
        )
        stored = depot_preview.load_preview(conn, 1366540)
    finally:
        conn.close()
    assert version == SCHEMA_VERSION
    assert app["name"] == "Team Fortress 2"
    assert depot["depotid"] == 441
    assert stored is not None and stored.total_size_bytes == 4_680_001_000

    raw = sqlite3.connect(original)  # the untouched original is still v31
    try:
        assert raw.execute("SELECT version FROM schema_version").fetchone()[0] == 31
    finally:
        raw.close()


def test_an_older_build_refuses_a_newer_file(tmp_path: Path) -> None:
    path = str(tmp_path / "vault.db")
    init_db(path)
    conn = get_connection(path)
    conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION + 1,))
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="newer version"):
        init_db(path)
