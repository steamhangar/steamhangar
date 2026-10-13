"""WP UPD-1: the always-on update check.

Guarantees pinned here, by name:

1. **Window configured, open or closed.** The scheduled check runs with a
   closed window and with an open one, and NOT without a window (review round
   2, "only prod checks"); the manual check runs either way. Besides that,
   only the oracle switch and the interval gate it.
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
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from tests.test_oracle import app_info, enabled_settings, one_depot
from vault_api import depot_manifests, oracle, settings_store, update_check
from vault_api import jobs as jobs_queue
from vault_api.config import Settings
from vault_api.db import SCHEMA_VERSION, get_connection, init_db
from vault_api.main import create_app
from vault_api.mapping import upsert_mapping
from vault_api.schedule_window import parse_window
from vault_api.scheduler import PrefillScheduler

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


#: A configured window that is CLOSED at ``NOW`` (10:00): the scheduled check
#: needs a window to exist, not to be open.
CLOSED_WINDOW = "03:00-07:00"


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = dict(
        schedule_window=parse_window(CLOSED_WINDOW),
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


def test_shutdown_aborts_between_apps(tmp_path: Path, conn: sqlite3.Connection) -> None:
    settings = make_settings(tmp_path)
    fake = FakeOracle({}, {})
    stop = iter([False, False, True])

    result = update_check.check_apps(
        conn, settings, [1, 2, 3], fetch=fake, should_abort=lambda: next(stop)
    )

    assert result.aborted is True and result.outcome == "aborted"
    assert len(result.apps) == 1 and result.unchecked == 2


# --------------------------------------------------------------------------
# UpdateCheckRunner: background, one at a time, claim-then-work
# --------------------------------------------------------------------------


def start_and_wait(
    runner: update_check.UpdateCheckRunner,
    conn: sqlite3.Connection,
    settings: Settings,
    now: datetime,
    appids: list[int] | Any,
    *,
    trigger: str = update_check.TRIGGER_SCHEDULED,
    require_interval: bool = True,
) -> str:
    select = appids if callable(appids) else (lambda _conn: appids)
    started = runner.start(
        conn,
        settings,
        trigger=trigger,
        select_appids=select,
        now=now,
        require_interval=require_interval,
    )
    assert runner.wait(10)
    return started


def status_of(conn: sqlite3.Connection, settings: Settings) -> update_check.UpdateCheckStatus:
    return update_check.read_status(conn, settings, running=False)


@pytest.mark.parametrize(
    "window", ["03:00-07:00", "09:00-17:00"], ids=["closed", "open"]
)
def test_the_check_runs_whether_the_window_is_open_or_closed(
    tmp_path: Path, conn: sqlite3.Connection, window: str | None
) -> None:
    settings = make_settings(tmp_path, schedule_window=parse_window(window))
    seed_cached_app(conn, settings, 440, 441)
    runner = update_check.UpdateCheckRunner(
        settings.db_path, fetch=FakeOracle({440: "900"}, {440: 441}), pause_seconds=0
    )

    assert start_and_wait(runner, conn, settings, NOW, [440]) == update_check.STARTED

    current = status_of(conn, settings)
    assert (current.last_apps, current.last_ok, current.last_stale) == (1, 1, 1)
    assert current.last_trigger == "scheduled" and current.last_outcome == "completed"
    assert current.last_finished_at is not None
    assert job_count(conn) == 0


def test_the_interval_gates_the_check_and_is_claimed_up_front(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = make_settings(tmp_path, schedule_interval_minutes=60)
    runner = update_check.UpdateCheckRunner(
        settings.db_path, fetch=FakeOracle({}, {}), pause_seconds=0
    )

    first = start_and_wait(runner, conn, settings, NOW, [])
    soon = start_and_wait(runner, conn, settings, NOW + timedelta(minutes=59), [])
    later = start_and_wait(runner, conn, settings, NOW + timedelta(minutes=60), [])

    assert first == update_check.STARTED
    assert soon == update_check.INTERVAL_NOT_ELAPSED
    assert later == update_check.STARTED  # an empty run still consumed the interval


def test_a_manual_run_ignores_the_interval_but_stamps_it(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = make_settings(tmp_path, schedule_interval_minutes=60)
    runner = update_check.UpdateCheckRunner(
        settings.db_path, fetch=FakeOracle({}, {}), pause_seconds=0
    )
    start_and_wait(runner, conn, settings, NOW, [])

    manual = start_and_wait(
        runner, conn, settings, NOW + timedelta(minutes=1), [],
        trigger=update_check.TRIGGER_MANUAL, require_interval=False,
    )
    scheduled = start_and_wait(runner, conn, settings, NOW + timedelta(minutes=60), [])

    assert manual == update_check.STARTED
    assert status_of(conn, settings).last_trigger == "manual"
    # The manual run at +1 min pushed the next scheduled one to +61 min.
    assert scheduled == update_check.INTERVAL_NOT_ELAPSED


def test_a_crash_mid_check_does_not_recheck_on_restart(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = make_settings(tmp_path, schedule_interval_minutes=60)
    runner = update_check.UpdateCheckRunner(settings.db_path, pause_seconds=0)

    def boom(_conn: sqlite3.Connection) -> list[int]:
        raise RuntimeError("crash while selecting")

    assert start_and_wait(runner, conn, settings, NOW, boom) == update_check.STARTED

    row = conn.execute("SELECT * FROM update_check_state WHERE id = 1").fetchone()
    assert row["last_check_at"] is not None
    assert row["last_check_apps"] is None
    assert row["last_check_outcome"] == "error"
    assert runner.running is False  # the lock was released
    again = start_and_wait(runner, conn, settings, NOW + timedelta(minutes=5), [])
    assert again == update_check.INTERVAL_NOT_ELAPSED


def test_a_run_the_process_died_in_reads_as_interrupted(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = make_settings(tmp_path)
    update_check.claim_update_check(
        conn, NOW, trigger=update_check.TRIGGER_SCHEDULED, interval_minutes=None
    )

    assert status_of(conn, settings).last_outcome == "interrupted"
    in_flight = update_check.read_status(conn, settings, running=True)
    assert in_flight.running is True and in_flight.last_outcome is None


def test_the_oracle_off_neither_checks_nor_claims(
    tmp_path: Path, conn: sqlite3.Connection, _no_network: list[str]
) -> None:
    settings = make_settings(tmp_path, manifest_oracle="")
    runner = update_check.UpdateCheckRunner(settings.db_path, pause_seconds=0)

    for require_interval in (True, False):
        started = start_and_wait(
            runner, conn, settings, NOW, [440], require_interval=require_interval
        )
        assert started == update_check.DISABLED

    assert update_check.read_last_check_at(conn) is None
    assert runner._thread is None  # no thread was ever started
    assert _no_network == []


def test_no_window_means_no_scheduled_check_but_manual_works(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    """Review round 2 ("only prod checks"): a vault without a schedule window
    (the dev instance) never checks on its own."""
    settings = make_settings(tmp_path, schedule_window=None)
    seed_cached_app(conn, settings, 440, 441)
    fake = FakeOracle({440: "900"}, {440: 441})
    runner = update_check.UpdateCheckRunner(settings.db_path, fetch=fake, pause_seconds=0)

    scheduled = start_and_wait(runner, conn, settings, NOW, [440])
    assert scheduled == update_check.NO_SCHEDULE_WINDOW
    assert fake.urls == [] and update_check.read_last_check_at(conn) is None

    manual = start_and_wait(
        runner, conn, settings, NOW, [440],
        trigger=update_check.TRIGGER_MANUAL, require_interval=False,
    )
    assert manual == update_check.STARTED and fake.appids == [440]


def test_a_stopped_runner_starts_nothing_and_claims_nothing(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = make_settings(tmp_path)
    fake = FakeOracle({}, {})
    runner = update_check.UpdateCheckRunner(settings.db_path, fetch=fake, pause_seconds=0)
    runner.stop(1)

    for trigger, require_interval in (
        (update_check.TRIGGER_SCHEDULED, True), (update_check.TRIGGER_MANUAL, False),
    ):
        started = start_and_wait(
            runner, conn, settings, NOW, [440],
            trigger=trigger, require_interval=require_interval,
        )
        assert started == update_check.STOPPING
    assert update_check.read_last_check_at(conn) is None
    assert runner._thread is None and fake.urls == []


def test_a_future_stamp_blocks_and_a_garbage_stamp_does_not(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = make_settings(tmp_path)
    runner = update_check.UpdateCheckRunner(
        settings.db_path, fetch=FakeOracle({}, {}), pause_seconds=0
    )
    conn.execute(
        "INSERT INTO update_check_state (id, last_check_at) VALUES (1, ?)",
        ("2030-01-01T00:00:00Z",),
    )
    conn.commit()
    blocked = start_and_wait(runner, conn, settings, NOW, [])
    assert blocked == update_check.INTERVAL_NOT_ELAPSED

    conn.execute("UPDATE update_check_state SET last_check_at = 'garbage'")
    conn.commit()
    assert start_and_wait(runner, conn, settings, NOW, []) == update_check.STARTED


class BlockingOracle(FakeOracle):
    """A fake whose requests wait until ``release`` is set."""

    def __init__(self, gids: dict[int, str], depot_of: dict[int, int]) -> None:
        super().__init__(gids, depot_of)
        self.entered = threading.Event()
        self.release = threading.Event()

    def __call__(self, url: str) -> bytes:
        self.entered.set()
        assert self.release.wait(10), "test never released the oracle"
        return super().__call__(url)


def test_a_second_start_while_one_runs_is_refused_and_not_recorded(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = make_settings(tmp_path, schedule_interval_minutes=60)
    seed_cached_app(conn, settings, 440, 441)
    fake = BlockingOracle({440: "900"}, {440: 441})
    runner = update_check.UpdateCheckRunner(settings.db_path, fetch=fake, pause_seconds=0)
    try:
        first = runner.start(
            conn, settings, trigger=update_check.TRIGGER_MANUAL,
            select_appids=lambda _c: [440], now=NOW, require_interval=False,
        )
        assert first == update_check.STARTED
        assert fake.entered.wait(10)
        stamped = update_check.read_last_check_at(conn)

        # Interval elapsed, but the manual run is still going.
        second = runner.start(
            conn, settings, trigger=update_check.TRIGGER_SCHEDULED,
            select_appids=lambda _c: [440], now=NOW + timedelta(hours=2),
            require_interval=True,
        )
        assert second == update_check.ALREADY_RUNNING
        assert runner.running is True
        # Nothing recorded for the skipped start: the in-flight row stands.
        in_flight = update_check.read_status(conn, settings, running=True)
        assert in_flight.last_started_at == stamped
        assert in_flight.last_trigger == "manual" and in_flight.last_apps is None
    finally:
        fake.release.set()
        assert runner.wait(10)

    done = status_of(conn, settings)
    assert (done.last_trigger, done.last_apps, done.last_stale) == ("manual", 1, 1)


def test_stop_aborts_a_running_check(tmp_path: Path, conn: sqlite3.Connection) -> None:
    settings = make_settings(tmp_path)
    for appid in (440, 730):
        seed_cached_app(conn, settings, appid, appid + 1)
    fake = BlockingOracle({440: "1", 730: "1"}, {440: 441, 730: 731})
    runner = update_check.UpdateCheckRunner(settings.db_path, fetch=fake, pause_seconds=0)
    runner.start(
        conn, settings, trigger=update_check.TRIGGER_MANUAL,
        select_appids=lambda _c: [440, 730], now=NOW, require_interval=False,
    )
    assert fake.entered.wait(10)
    fake.release.set()
    runner.stop(10)

    done = status_of(conn, settings)
    assert done.last_outcome in ("aborted", "completed")
    assert runner.running is False


# --------------------------------------------------------------------------
# The scheduler tick: cached games only, never blocked, download gating unchanged
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


def tick_and_wait(sched: PrefillScheduler, conn: sqlite3.Connection) -> None:
    sched._tick(conn)
    assert sched._update_checker.wait(10)


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
        tick_and_wait(sched, conn)

        assert fake.appids == [440, 730]  # not 10 (installed only), 20, nor the tool app
        stored = oracle.verdicts_by_app(conn, settings=settings)
        assert {a: v for a, (v, _t) in stored.items()} == {440: "stale", 730: "current"}
        assert job_count(conn) == 0  # no window: nothing downloaded
        current = status_of(conn, settings)
        assert (current.last_trigger, current.last_apps, current.last_stale) == (
            "scheduled", 2, 1,
        )
    finally:
        conn.close()


def test_the_tick_without_a_window_makes_no_request(
    tmp_path: Path, _no_network: list[str]
) -> None:
    settings = make_settings(tmp_path, schedule_window=None)
    init_db(settings.db_path)
    conn = get_connection(settings.db_path)
    try:
        fake = seed_library(conn, settings)
        sched = PrefillScheduler(
            settings, clock=lambda: NOW, oracle_fetch=fake, update_check_pause_seconds=0
        )
        tick_and_wait(sched, conn)
        assert fake.urls == [] and update_check.read_last_check_at(conn) is None

        # A window set later through PATCH /v1/settings (a DB override) turns
        # the scheduled check on at the next tick, no restart.
        settings_store.set_override(conn, "schedule_window", CLOSED_WINDOW)
        tick_and_wait(sched, conn)
        assert fake.appids == [440, 730]
    finally:
        conn.close()
    assert _no_network == []


def test_the_tick_does_not_wait_for_the_oracle(tmp_path: Path) -> None:
    """Review finding 4: the check runs off the scheduler thread, so a slow
    oracle cannot stall the event and prefill sweeps of the next tick."""
    settings = make_settings(tmp_path)
    init_db(settings.db_path)
    conn = get_connection(settings.db_path)
    fake = BlockingOracle({440: "900", 730: "200"}, {440: 441, 730: 731})
    try:
        seed_library(conn, settings)
        sched = PrefillScheduler(
            settings, clock=lambda: NOW, oracle_fetch=fake, update_check_pause_seconds=0
        )
        sched._tick(conn)  # returns although the oracle has not answered
        assert fake.entered.wait(10)
        assert sched._update_checker.running is True

        sched._tick(conn)  # a second tick meanwhile: not blocked either
        assert status_of(conn, settings).last_apps is None  # still in flight
    finally:
        fake.release.set()
        assert sched._update_checker.wait(10)
        conn.close()


def test_a_busy_runner_is_logged_as_skipped_not_as_zero_checked(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = make_settings(tmp_path, schedule_interval_minutes=60)
    init_db(settings.db_path)
    conn = get_connection(settings.db_path)
    fake = BlockingOracle({440: "900"}, {440: 441})
    clock = [NOW]
    try:
        seed_library(conn, settings)
        sched = PrefillScheduler(
            settings, clock=lambda: clock[0], oracle_fetch=fake,
            update_check_pause_seconds=0,
        )
        sched._tick(conn)
        assert fake.entered.wait(10)
        clock[0] = NOW + timedelta(hours=2)  # due again, still running
        with caplog.at_level("INFO", logger="vault_api"):
            sched._tick(conn)
        messages = [r.getMessage() for r in caplog.records]
        assert any("skipped, a check is already running" in m for m in messages)
        assert not any("0 cached app(s) checked" in m for m in messages)
        assert update_check.read_last_check_at(conn) == "2026-10-13T08:00:00Z"
    finally:
        fake.release.set()
        assert sched._update_checker.wait(10)
        conn.close()


def test_the_tick_logs_whether_the_check_is_on_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    for oracle_value, window, expected in (
        ("steamcmd_api", parse_window(CLOSED_WINDOW), "Update check: ON"),
        ("steamcmd_api", None, "Update check: scheduled checks off, no schedule window; manual check available"),
        ("", None, "Update check: OFF"),
    ):
        settings = make_settings(tmp_path, manifest_oracle=oracle_value, schedule_window=window)
        init_db(settings.db_path)
        conn = get_connection(settings.db_path)
        caplog.clear()
        try:
            sched = PrefillScheduler(
                settings, clock=lambda: NOW, oracle_fetch=FakeOracle({}, {}),
                update_check_pause_seconds=0,
            )
            with caplog.at_level("INFO", logger="vault_api"):
                tick_and_wait(sched, conn)
                tick_and_wait(sched, conn)
        finally:
            conn.close()
        lines = [r.getMessage() for r in caplog.records if "Update check:" in r.getMessage()]
        assert len(lines) == 1 and lines[0].startswith(expected), lines


def test_an_open_window_still_enqueues_exactly_as_before(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, schedule_window=parse_window("09:00-17:00"))
    path = settings.db_path
    init_db(path)
    conn = get_connection(path)
    try:
        fake = seed_library(conn, settings)
        sched = PrefillScheduler(
            settings, clock=lambda: NOW, oracle_fetch=fake, update_check_pause_seconds=0
        )
        tick_and_wait(sched, conn)

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
        sched = PrefillScheduler(settings, clock=lambda: NOW)
        tick_and_wait(sched, conn)
        assert update_check.read_last_check_at(conn) is None
    finally:
        conn.close()

    assert _no_network == []


def test_a_failing_check_does_not_break_the_tick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings(tmp_path)
    init_db(settings.db_path)
    conn = get_connection(settings.db_path)

    def boom(*args: Any, **kwargs: Any) -> str:
        raise RuntimeError("check bug")

    sched = PrefillScheduler(settings, clock=lambda: NOW)
    monkeypatch.setattr(sched._update_checker, "start", boom)
    try:
        sched._tick(conn)  # must not raise
    finally:
        conn.close()


# --------------------------------------------------------------------------
# POST /v1/updates/check, GET /v1/updates/status
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


def wait_for_check(client: TestClient) -> None:
    assert client.app.state.update_checker.wait(10)  # type: ignore[attr-defined]


def test_the_endpoints_require_the_api_key(tmp_path: Path) -> None:
    client, _ = client_with_library(tmp_path)
    assert client.post("/v1/updates/check").status_code == 401
    assert client.get("/v1/updates/status").status_code == 401


def test_the_endpoint_starts_a_background_check_and_no_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, fake = client_with_library(tmp_path)
    use_fake_oracle(monkeypatch, fake)

    before = client.get("/v1/updates/status", headers=AUTH).json()
    assert before == {
        "enabled": True, "scheduled": True, "running": False, "last_trigger": None,
        "last_started_at": None, "last_finished_at": None, "last_outcome": None,
        "last_apps": None, "last_ok": None, "last_failed": None, "last_stale": None,
    }

    response = client.post("/v1/updates/check", headers=AUTH)
    assert response.status_code == 202, response.text
    assert response.json() == {"status": "started", "enabled": True}
    wait_for_check(client)

    after = client.get("/v1/updates/status", headers=AUTH).json()
    assert after["running"] is False and after["last_trigger"] == "manual"
    assert after["last_outcome"] == "completed"
    assert after["last_started_at"] and after["last_finished_at"]
    assert (after["last_apps"], after["last_ok"], after["last_failed"], after["last_stale"]) == (
        2, 2, 0, 1,
    )
    assert fake.appids == [440, 730]
    assert client.get("/v1/jobs", headers=AUTH).json() == []


def test_the_endpoint_works_without_a_window_and_reports_scheduled_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, fake = client_with_library(tmp_path, schedule_window=None)
    use_fake_oracle(monkeypatch, fake)

    assert client.get("/v1/updates/status", headers=AUTH).json()["scheduled"] is False
    assert client.post("/v1/updates/check", headers=AUTH).status_code == 202
    wait_for_check(client)
    assert fake.appids == [440, 730]


def test_the_endpoint_answers_at_once_while_the_oracle_is_slow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review finding 2: the request must not wait for the oracle; a second
    click while it runs is 202 already_running, never 409."""
    client, _ = client_with_library(tmp_path)
    slow = BlockingOracle({440: "900", 730: "200"}, {440: 441, 730: 731})
    use_fake_oracle(monkeypatch, slow)
    try:
        first = client.post("/v1/updates/check", headers=AUTH)
        assert first.status_code == 202 and first.json()["status"] == "started"
        assert slow.entered.wait(10)

        second = client.post("/v1/updates/check", headers=AUTH)
        assert second.status_code == 202
        assert second.json() == {"status": "already_running", "enabled": True}
        running = client.get("/v1/updates/status", headers=AUTH).json()
        assert running["running"] is True and running["last_outcome"] is None
    finally:
        slow.release.set()
        wait_for_check(client)


def test_the_endpoint_answers_disabled_when_the_oracle_is_off(
    tmp_path: Path, _no_network: list[str]
) -> None:
    client, _ = client_with_library(tmp_path, manifest_oracle="")

    response = client.post("/v1/updates/check", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {"status": "disabled", "enabled": False}
    assert client.app.state.update_checker._thread is None  # type: ignore[attr-defined]
    current = client.get("/v1/updates/status", headers=AUTH).json()
    assert current["enabled"] is False and current["last_started_at"] is None
    assert _no_network == []


def test_a_failed_app_is_counted_without_a_5xx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, fake = client_with_library(tmp_path)
    fake.gids.pop(730)
    use_fake_oracle(monkeypatch, fake)

    assert client.post("/v1/updates/check", headers=AUTH).status_code == 202
    wait_for_check(client)

    current = client.get("/v1/updates/status", headers=AUTH).json()
    assert (current["last_ok"], current["last_failed"]) == (1, 1)
    game = client.get("/v1/games/730", headers=AUTH).json()
    assert game["update_available"] is False and game["update_checked_at"] is None


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
    wait_for_check(client)
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
    wait_for_check(client)
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


def test_schema_v20_creates_the_full_table_on_a_v18_database(tmp_path: Path) -> None:
    assert SCHEMA_VERSION == 20
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
    assert_full_update_check_table(path)


def test_schema_v20_adds_the_run_columns_to_a_v19_table(tmp_path: Path) -> None:
    path = str(tmp_path / "v19.db")
    init_db(path)
    conn = get_connection(path)
    try:
        conn.execute("DROP TABLE update_check_state")
        conn.execute(
            """
            CREATE TABLE update_check_state (
                id INTEGER PRIMARY KEY CHECK (id = 1), last_check_at TEXT,
                last_check_apps INTEGER, last_check_ok INTEGER,
                last_check_stale INTEGER
            )
            """
        )
        conn.execute(
            "INSERT INTO update_check_state VALUES (1, '2026-10-13T08:00:00Z', 3, 3, 1)"
        )
        conn.execute("UPDATE schema_version SET version = 19")
        conn.commit()
    finally:
        conn.close()

    init_db(path)
    init_db(path)  # idempotent
    assert_full_update_check_table(path)
    conn = get_connection(path)
    try:
        row = conn.execute("SELECT * FROM update_check_state").fetchone()
    finally:
        conn.close()
    assert (row["last_check_at"], row["last_check_apps"]) == ("2026-10-13T08:00:00Z", 3)
    assert row["last_check_trigger"] is None


def assert_full_update_check_table(path: str) -> None:
    conn = get_connection(path)
    try:
        (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(update_check_state)")}
    finally:
        conn.close()
    assert version == 20
    assert columns == {
        "id", "last_check_at", "last_check_apps", "last_check_ok", "last_check_stale",
        "last_check_trigger", "last_check_finished_at", "last_check_outcome",
    }
