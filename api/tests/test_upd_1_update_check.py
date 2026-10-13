"""WP UPD-1: the always-on update check.

Guarantees pinned here, by name:

1. **Independent of the schedule window.** The check runs with no window, with
   a closed window and with an open one; only the oracle switch and the
   interval gate it.
2. **It never downloads.** No code path of the check enqueues a job; the
   windowed sweep alone does that, unchanged.
3. **Cached games only.** Installed-but-uncached apps, apps with no content on
   disk and Steam tool packages are never asked about.
4. **Claim-then-work.** The interval is stamped before any request, so a crash
   mid-check does not re-check on restart.
5. **No network, ever.** Every test injects a fake fetcher; an autouse guard
   makes ``oracle.http_fetch`` and any socket connect fail loudly.
6. **The library listing carries the flag** (``update_available``), from stored
   rows only.
"""

from __future__ import annotations

import socket
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from tests.test_oracle import app_info, enabled_settings, one_depot
from vault_api import depot_manifests, oracle, scheduler, update_check
from vault_api import jobs as jobs_queue
from vault_api.config import Settings
from vault_api.db import SCHEMA_VERSION, get_connection, init_db
from vault_api.main import create_app
from vault_api.mapping import upsert_mapping
from vault_api.schedule_window import parse_window
from vault_api.scheduler import PrefillScheduler, maybe_sweep

AUTH = {"X-Api-Key": TEST_API_KEY}
TZ = timezone(timedelta(hours=2))
NOW = datetime(2026, 10, 13, 10, 0, tzinfo=TZ)

TOOL_APPID = 228980


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fail loudly if anything here reaches for the network (guarantee 5).

    Tests that want oracle answers pass a fake ``fetch`` (or patch
    ``oracle.http_fetch`` themselves, which overrides this guard for that test
    only, with a recorded fake).
    """
    attempts: list[str] = []

    def no_fetch(url: str, *args: Any, **kwargs: Any) -> bytes:
        attempts.append(url)
        raise AssertionError(f"network fetch attempted in a test: {url}")

    def no_connect(self: socket.socket, address: Any) -> None:
        attempts.append(str(address))
        raise AssertionError(f"socket connect attempted in a test: {address!r}")

    monkeypatch.setattr(oracle, "http_fetch", no_fetch)
    monkeypatch.setattr(socket.socket, "connect", no_connect)
    return attempts


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = dict(
        schedule_window=None,
        schedule_interval_minutes=180,
        steamprefill_cache_dir=str(tmp_path / "unused-steamprefill-cache"),
        manifest_archive_dir=str(tmp_path / "manifest-archive"),
    )
    values.update(overrides)
    return enabled_settings(tmp_path, **values)


@pytest.fixture
def conn(tmp_path: Path):
    path = str(tmp_path / "vault.db")
    init_db(path)
    connection = get_connection(path)
    try:
        yield connection
    finally:
        connection.close()


def seed_cached_app(
    conn: sqlite3.Connection,
    settings: Settings,
    appid: int,
    depotid: int,
    *,
    recorded_manifest: str | None = "100",
    on_disk: bool = True,
) -> None:
    """A mapped app, with bytes on disk and the manifest vault-api recorded."""
    upsert_mapping(conn, depotid, appid, f"Game {appid}")
    if on_disk:
        chunk = Path(settings.cache_root) / "depot" / str(depotid) / "chunk" / "a"
        chunk.parent.mkdir(parents=True, exist_ok=True)
        chunk.write_bytes(b"x" * 32)
    if recorded_manifest is not None:
        depot_manifests.upsert_depot_manifest(
            conn,
            appid=appid,
            containing_appid=None,
            depotid=depotid,
            manifestid=recorded_manifest,
            chunk_count=1,
            total_bytes=32,
            recorded_at="2026-10-01T00:00:00Z",
            source="test",
        )


class FakeOracle:
    """An injectable ``Fetcher``: one public manifest per appid, recorded."""

    def __init__(self, gids: dict[int, str], depot_of: dict[int, int]) -> None:
        self.gids = gids
        self.depot_of = depot_of
        self.urls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        appid = int(url.rstrip("/").rsplit("/", 1)[-1])
        if appid not in self.gids:
            raise oracle.OracleError("not found")
        return app_info(
            appid,
            depots=one_depot(
                {"public": {"gid": self.gids[appid]}},
                depotid=str(self.depot_of[appid]),
            ),
            branches={"public": {"buildid": "100"}},
        )

    @property
    def appids(self) -> list[int]:
        return sorted(int(u.rstrip("/").rsplit("/", 1)[-1]) for u in self.urls)


def job_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]


# --------------------------------------------------------------------------
# check_apps: verdicts, no download
# --------------------------------------------------------------------------


def test_check_apps_reports_stale_and_current_and_enqueues_nothing(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = make_settings(tmp_path)
    seed_cached_app(conn, settings, 440, 441, recorded_manifest="100")
    seed_cached_app(conn, settings, 730, 731, recorded_manifest="200")
    fake = FakeOracle({440: "900", 730: "200"}, {440: 441, 730: 731})

    result = update_check.check_apps(conn, settings, [440, 730], fetch=fake)

    verdicts = {app.appid: app.verdict for app in result.apps}
    assert verdicts == {440: "stale", 730: "current"}
    assert (result.ok_count, result.failed_count, result.stale_count) == (2, 0, 1)
    assert job_count(conn) == 0  # never a download


def test_check_apps_with_the_oracle_off_does_nothing(
    tmp_path: Path, conn: sqlite3.Connection, _no_network: list[str]
) -> None:
    settings = make_settings(tmp_path, manifest_oracle="")
    result = update_check.check_apps(conn, settings, [440])

    assert result.enabled is False and result.ran is False
    assert result.skipped_reason == "oracle-disabled"
    assert _no_network == []


def test_a_failed_refresh_keeps_the_previous_verdict(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = make_settings(tmp_path)
    seed_cached_app(conn, settings, 440, 441, recorded_manifest="100")
    update_check.check_apps(
        conn, settings, [440], fetch=FakeOracle({440: "900"}, {440: 441})
    )

    result = update_check.check_apps(conn, settings, [440], fetch=FakeOracle({}, {}))

    (app,) = result.apps
    assert app.ok is False and app.error
    assert app.verdict == "stale"  # the last validated snapshot still stands


def test_a_dead_oracle_stops_the_run_after_repeated_failures(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = make_settings(tmp_path)
    appids = list(range(1000, 1000 + update_check.CONSECUTIVE_FAILURE_LIMIT + 4))
    fake = FakeOracle({}, {})  # every request fails

    result = update_check.check_apps(conn, settings, appids, fetch=fake)

    assert result.bailed_out is True
    assert len(fake.urls) == update_check.CONSECUTIVE_FAILURE_LIMIT
    assert result.unchecked == 4


def test_one_success_resets_the_failure_streak(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = make_settings(tmp_path)
    limit = update_check.CONSECUTIVE_FAILURE_LIMIT
    failing = list(range(2000, 2000 + limit - 1))
    good = 2050
    seed_cached_app(conn, settings, good, 2051)
    fake = FakeOracle({good: "100"}, {good: 2051})
    # Sorted by appid: 4 failures, the success (2050), 4 failures.
    order = failing + [good] + list(range(2100, 2100 + limit - 1))

    result = update_check.check_apps(conn, settings, order, fetch=fake)

    assert result.bailed_out is False
    assert len(result.apps) == len(order)


def test_a_second_check_while_one_runs_is_refused(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = make_settings(tmp_path)
    assert update_check._RUN_LOCK.acquire(blocking=False)
    try:
        result = update_check.check_apps(conn, settings, [440], fetch=FakeOracle({}, {}))
    finally:
        update_check._RUN_LOCK.release()

    assert result.ran is False and result.skipped_reason == "already-running"


def test_shutdown_aborts_between_apps(tmp_path: Path, conn: sqlite3.Connection) -> None:
    settings = make_settings(tmp_path)
    fake = FakeOracle({}, {})
    stop = iter([False, True])

    result = update_check.check_apps(
        conn, settings, [1, 2, 3], fetch=fake, should_abort=lambda: next(stop)
    )

    assert result.aborted is True
    assert len(result.apps) == 1 and result.unchecked == 2


# --------------------------------------------------------------------------
# maybe_check_updates: independent of the window, claim-then-work
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "window", [None, "03:00-07:00", "09:00-17:00"], ids=["no-window", "closed", "open"]
)
def test_the_check_runs_whatever_the_window_says(
    tmp_path: Path, conn: sqlite3.Connection, window: str | None
) -> None:
    settings = make_settings(
        tmp_path, schedule_window=None if window is None else parse_window(window)
    )
    seed_cached_app(conn, settings, 440, 441)
    fake = FakeOracle({440: "900"}, {440: 441})

    result = update_check.maybe_check_updates(
        conn, settings, NOW, lambda: [440], fetch=fake
    )

    assert result.ran is True and result.stale_count == 1
    assert job_count(conn) == 0


def test_the_interval_gates_the_check_and_is_claimed_up_front(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = make_settings(tmp_path, schedule_interval_minutes=60)
    fake = FakeOracle({}, {})

    first = update_check.maybe_check_updates(conn, settings, NOW, lambda: [], fetch=fake)
    soon = update_check.maybe_check_updates(
        conn, settings, NOW + timedelta(minutes=59), lambda: [], fetch=fake
    )
    later = update_check.maybe_check_updates(
        conn, settings, NOW + timedelta(minutes=60), lambda: [], fetch=fake
    )

    assert first.ran is True
    assert soon.skipped_reason == "interval-not-elapsed"
    assert later.ran is True  # an empty run still consumed the interval


def test_a_crash_mid_check_does_not_recheck_on_restart(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = make_settings(tmp_path, schedule_interval_minutes=60)

    def boom() -> list[int]:
        raise RuntimeError("crash while selecting")

    with pytest.raises(RuntimeError):
        update_check.maybe_check_updates(conn, settings, NOW, boom)

    row = conn.execute("SELECT * FROM update_check_state WHERE id = 1").fetchone()
    assert row["last_check_at"] is not None
    assert row["last_check_apps"] is None  # "in flight or interrupted"
    again = update_check.maybe_check_updates(
        conn, settings, NOW + timedelta(minutes=5), lambda: []
    )
    assert again.skipped_reason == "interval-not-elapsed"


def test_the_oracle_off_neither_checks_nor_claims(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = make_settings(tmp_path, manifest_oracle="")

    result = update_check.maybe_check_updates(conn, settings, NOW, lambda: [440])

    assert result.skipped_reason == "oracle-disabled"
    assert update_check.read_last_check_at(conn) is None


def test_a_future_stamp_blocks_and_a_garbage_stamp_does_not(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = make_settings(tmp_path)
    conn.execute(
        "INSERT INTO update_check_state (id, last_check_at) VALUES (1, ?)",
        ("2030-01-01T00:00:00Z",),
    )
    conn.commit()
    blocked = update_check.maybe_check_updates(conn, settings, NOW, lambda: [])
    assert blocked.skipped_reason == "interval-not-elapsed"

    conn.execute("UPDATE update_check_state SET last_check_at = 'garbage'")
    conn.commit()
    assert update_check.maybe_check_updates(conn, settings, NOW, lambda: []).ran is True


# --------------------------------------------------------------------------
# The scheduler tick: cached games only, download gating unchanged
# --------------------------------------------------------------------------


def seed_library(conn: sqlite3.Connection, settings: Settings) -> FakeOracle:
    """440 cached+stale, 730 cached+current, 10 installed on a PC but NOT
    cached, 20 mapped with nothing on disk, plus the Steam tool package."""
    seed_cached_app(conn, settings, 440, 441, recorded_manifest="100")
    seed_cached_app(conn, settings, 730, 731, recorded_manifest="200")
    seed_cached_app(conn, settings, TOOL_APPID, 228984)
    seed_cached_app(conn, settings, 20, 21, on_disk=False)
    conn.execute(
        "INSERT INTO agent_reports (client_id, reported_at, appids) VALUES (?, ?, ?)",
        ("pc", "2026-10-13T07:00:00Z", "[10, 440]"),
    )
    conn.commit()
    return FakeOracle(
        {440: "900", 730: "200", 10: "1", 20: "1", TOOL_APPID: "1"},
        {440: 441, 730: 731, 10: 11, 20: 21, TOOL_APPID: 228984},
    )


def test_the_tick_checks_cached_games_only(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    path = settings.db_path
    init_db(path)
    conn = get_connection(path)
    try:
        fake = seed_library(conn, settings)
        sched = PrefillScheduler(
            settings, clock=lambda: NOW, oracle_fetch=fake, update_check_pause_seconds=0
        )
        sched._tick(conn)

        assert fake.appids == [440, 730]  # not 10 (installed only), 20, nor the tool app
        stored = oracle.verdicts_by_app(conn, settings=settings)
        assert {a: v for a, (v, _t) in stored.items()} == {440: "stale", 730: "current"}
        assert job_count(conn) == 0  # no window: nothing downloaded
    finally:
        conn.close()


def test_an_open_window_still_enqueues_exactly_as_before(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, schedule_window=parse_window("09:00-17:00"))
    path = settings.db_path
    init_db(path)
    conn = get_connection(path)
    try:
        fake = seed_library(conn, settings)
        before = maybe_sweep  # the sweep itself is untouched by this package
        sched = PrefillScheduler(
            settings, clock=lambda: NOW, oracle_fetch=fake, update_check_pause_seconds=0
        )
        sched._tick(conn)

        assert before is scheduler.maybe_sweep
        queued = {
            row["appid"] for row in conn.execute("SELECT appid FROM jobs").fetchall()
        }
        # The sweep's own target set (installed + cached), not filtered by
        # staleness: 10 and 440 installed, 440 and 730 cached.
        assert queued == {10, 440, 730}
        assert fake.appids == [440, 730]  # the check still looks at cached only
    finally:
        conn.close()


def test_the_tick_with_the_oracle_off_makes_no_request(
    tmp_path: Path, _no_network: list[str]
) -> None:
    settings = make_settings(tmp_path, manifest_oracle="")
    init_db(settings.db_path)
    conn = get_connection(settings.db_path)
    try:
        seed_library(conn, settings)
        PrefillScheduler(settings, clock=lambda: NOW)._tick(conn)
    finally:
        conn.close()

    assert _no_network == []


def test_a_failing_check_does_not_break_the_tick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings(tmp_path)
    init_db(settings.db_path)
    conn = get_connection(settings.db_path)

    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("check bug")

    monkeypatch.setattr(update_check, "maybe_check_updates", boom)
    try:
        PrefillScheduler(settings, clock=lambda: NOW)._tick(conn)  # must not raise
    finally:
        conn.close()


# --------------------------------------------------------------------------
# POST /v1/updates/check
# --------------------------------------------------------------------------


def client_with_library(tmp_path: Path, **overrides: Any) -> tuple[TestClient, FakeOracle]:
    settings = make_settings(tmp_path, **overrides)
    init_db(settings.db_path)
    conn = get_connection(settings.db_path)
    try:
        fake = seed_library(conn, settings)
    finally:
        conn.close()
    return TestClient(create_app(settings)), fake


def use_fake_oracle(monkeypatch: pytest.MonkeyPatch, fake: FakeOracle) -> None:
    """Route the REAL configured fetcher into the recorded fake."""
    monkeypatch.setattr(oracle, "http_fetch", lambda url, timeout=None: fake(url))
    monkeypatch.setattr(update_check, "INTER_REQUEST_PAUSE_SECONDS", 0.0)


def test_the_endpoint_requires_the_api_key(tmp_path: Path) -> None:
    client, _ = client_with_library(tmp_path)
    assert client.post("/v1/updates/check").status_code == 401


def test_the_endpoint_returns_verdicts_and_starts_no_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, fake = client_with_library(tmp_path)
    use_fake_oracle(monkeypatch, fake)

    response = client.post("/v1/updates/check", headers=AUTH)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["enabled"] is True
    assert (body["checked"], body["ok"], body["failed"], body["stale"]) == (2, 2, 0, 1)
    assert body["bailed_out"] is False and body["unchecked"] == 0
    by_app = {a["appid"]: a for a in body["apps"]}
    assert set(by_app) == {440, 730}
    assert by_app[440]["verdict"] == "stale" and by_app[440]["update_available"] is True
    assert by_app[440]["name"] == "Game 440"
    assert by_app[730]["verdict"] == "current" and by_app[730]["update_available"] is False
    assert fake.appids == [440, 730]
    jobs = client.get("/v1/jobs", headers=AUTH).json()
    assert jobs == []


def test_the_endpoint_answers_disabled_when_the_oracle_is_off(
    tmp_path: Path, _no_network: list[str]
) -> None:
    client, _ = client_with_library(tmp_path, manifest_oracle="")

    response = client.post("/v1/updates/check", headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False and body["checked"] == 0 and body["apps"] == []
    assert _no_network == []


def test_the_endpoint_reports_a_failed_app_without_a_5xx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, fake = client_with_library(tmp_path)
    fake.gids.pop(730)
    use_fake_oracle(monkeypatch, fake)

    body = client.post("/v1/updates/check", headers=AUTH).json()

    by_app = {a["appid"]: a for a in body["apps"]}
    assert by_app[730]["ok"] is False and by_app[730]["error"]
    assert by_app[730]["verdict"] == "unknown" and by_app[730]["checked_at"] is None
    assert body["failed"] == 1 and body["ok"] == 1


def test_the_endpoint_answers_409_while_a_check_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, fake = client_with_library(tmp_path)
    use_fake_oracle(monkeypatch, fake)
    assert update_check._RUN_LOCK.acquire(blocking=False)
    try:
        response = client.post("/v1/updates/check", headers=AUTH)
    finally:
        update_check._RUN_LOCK.release()

    assert response.status_code == 409
    assert fake.urls == []


# --------------------------------------------------------------------------
# The library listing exposes the flag
# --------------------------------------------------------------------------


def test_the_listing_flags_a_stale_cached_game(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _no_network: list[str]
) -> None:
    client, fake = client_with_library(tmp_path)
    use_fake_oracle(monkeypatch, fake)

    before = {g["appid"]: g for g in client.get("/v1/games", headers=AUTH).json()}
    assert before[440]["update_available"] is False  # never checked
    assert before[440]["update_checked_at"] is None

    client.post("/v1/updates/check", headers=AUTH)
    requests_after_check = len(fake.urls)

    listing = {g["appid"]: g for g in client.get("/v1/games", headers=AUTH).json()}
    assert listing[440]["update_available"] is True
    assert listing[440]["update_checked_at"] is not None
    assert listing[730]["update_available"] is False
    assert listing[730]["update_checked_at"] is not None
    assert listing[20]["update_available"] is False  # never asked
    assert len(fake.urls) == requests_after_check  # listing is read-only

    detail = client.get("/v1/games/440", headers=AUTH).json()
    assert detail["update_available"] is True and detail["update_checked_at"]
    assert client.get("/v1/games/730", headers=AUTH).json()["update_available"] is False


def test_the_flag_is_false_when_the_oracle_is_off(tmp_path: Path) -> None:
    on_settings = make_settings(tmp_path)
    init_db(on_settings.db_path)
    conn = get_connection(on_settings.db_path)
    try:
        fake = seed_library(conn, on_settings)
        update_check.check_apps(conn, on_settings, [440], fetch=fake)  # stored: stale
    finally:
        conn.close()

    client = TestClient(create_app(make_settings(tmp_path, manifest_oracle="")))
    game = next(
        g for g in client.get("/v1/games", headers=AUTH).json() if g["appid"] == 440
    )
    assert game["update_available"] is False and game["update_checked_at"] is None
    assert client.get("/v1/games/440", headers=AUTH).json()["update_available"] is False


def test_a_deleted_game_is_not_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, fake = client_with_library(tmp_path)
    use_fake_oracle(monkeypatch, fake)
    client.post("/v1/updates/check", headers=AUTH)
    assert client.delete("/v1/cache/440", headers=AUTH).status_code == 200

    game = next(
        g for g in client.get("/v1/games", headers=AUTH).json() if g["appid"] == 440
    )
    assert game["size_bytes"] in (None, 0)
    assert game["update_available"] is False


def test_the_bulk_verdicts_agree_with_the_per_app_view(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = make_settings(tmp_path)
    fake = seed_library(conn, settings)
    update_check.check_apps(conn, settings, [440, 730, 10, 20], fetch=fake)

    bulk = oracle.verdicts_by_app(conn, settings=settings)

    for appid in (440, 730, 10, 20):
        view = oracle.app_view(conn, appid, settings=settings)
        assert bulk[appid] == (view.verdict, view.checked_at)


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------


def test_schema_v19_adds_update_check_state(tmp_path: Path) -> None:
    assert SCHEMA_VERSION == 19
    path = str(tmp_path / "old.db")
    init_db(path)
    conn = get_connection(path)
    try:
        conn.execute("DROP TABLE update_check_state")
        conn.execute("UPDATE schema_version SET version = 18")
        conn.commit()
    finally:
        conn.close()

    init_db(path)
    init_db(path)
    conn = get_connection(path)
    try:
        (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
        columns = [r["name"] for r in conn.execute("PRAGMA table_info(update_check_state)")]
    finally:
        conn.close()
    assert version == 19
    assert columns == [
        "id", "last_check_at", "last_check_apps", "last_check_ok", "last_check_stale",
    ]
