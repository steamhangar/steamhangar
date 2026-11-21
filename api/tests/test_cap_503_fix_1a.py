"""CAP-503-FIX-1a: client priority over the prefill runner (operator Weg A).

The controller (``vault_api/client_priority.py``) pauses the running prefill
job while a client loads MISS traffic through the cache and resumes it after
``IDLE_GAP_SECONDS`` without client traffic. Every test drives it through
``ClientPriority.tick`` with an injected live-tier clock (CLOCK-SEAM-1):
no sleeps, no thread. The worker's half of a pause (terminate SteamPrefill,
park the job) is the unchanged WP 3.12 path, called here as
``jobs.park_paused`` exactly as the worker calls it.

Synthetic addresses only (10.0.0.x): 10.0.0.5 / 10.0.0.6 are PCs, 10.0.0.9
is the prefill runner.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from tests.test_event_sweep import append_log, event_line, make_settings, write_log
from tests.test_graph_live import Clock, iso, line
from vault_api import client_priority, jobs, runner_identity
from vault_api.client_priority import ClientPriority
from vault_api.config import Settings
from vault_api.db import SCHEMA_VERSION, get_connection, init_db
from vault_api.live_tier import LiveTier
from vault_api.main import create_app
from vault_api.worker import PrefillWorker

AUTH = {"X-Api-Key": TEST_API_KEY}
UTC = timezone.utc
NOW = datetime(2026, 10, 16, 12, 0, 7, tzinfo=UTC)
PC = "10.0.0.5"
PC2 = "10.0.0.6"
RUNNER = "10.0.0.9"
GAP = client_priority.IDLE_GAP_SECONDS
#: Well above the 512 KiB/s floor over the 17 s window at NOW (~600 KiB/s).
BIG = 10 * 1024 * 1024
#: Below it (~60 KiB/s).
SMALL = 1024 * 1024


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    settings = make_settings(tmp_path)
    init_db(settings.db_path)
    write_log(Path(settings.event_log_path))
    return settings


@pytest.fixture
def conn(settings: Settings):
    connection = get_connection(settings.db_path)
    try:
        yield connection
    finally:
        connection.close()


def make_controller(settings: Settings, clock: Clock) -> tuple[ClientPriority, LiveTier]:
    tier = LiveTier(clock=clock)
    return ClientPriority(settings, lambda: tier), tier


def known_runner(conn: sqlite3.Connection, addr: str = RUNNER) -> None:
    """A runner period as the sweep records it (WP D15-RUNNER)."""
    stamp = jobs.to_utc_iso(NOW - timedelta(hours=1))
    conn.execute(
        "INSERT INTO runner_address_periods (addr, first_marked_at, last_marked_at) "
        "VALUES (?, ?, ?)",
        (addr, stamp, stamp),
    )
    conn.commit()


def running_job(conn: sqlite3.Connection, appid: int = 440) -> int:
    jobs.enqueue_prefill(conn, appid)
    claimed = jobs.claim_next_job(conn)
    assert claimed is not None and claimed["status"] == jobs.STATUS_RUNNING
    return int(claimed["id"])  # type: ignore[arg-type]


def miss(settings: Settings, clock: Clock, size: int, addr: str = PC, ago: float = 2) -> None:
    append_log(
        Path(settings.event_log_path), line(ago, size, addr=addr, status="MISS", now=clock())
    )


def job(conn: sqlite3.Connection, job_id: int) -> dict[str, object]:
    row = jobs.get_job(conn, job_id)
    assert row is not None
    return row


def worker_parks(conn: sqlite3.Connection, job_id: int) -> None:
    """The worker honouring a pending pause (worker._finish_stopped_prefill)."""
    assert jobs.read_stop_request(conn, job_id) == jobs.STOP_REQUEST_PAUSE
    jobs.reset_app_status_if_running(conn, int(job(conn, job_id)["appid"]))  # type: ignore[arg-type]
    jobs.park_paused(conn, job_id, "[stub] SteamPrefill terminated")


# ---------------------------------------------------------------------------
# Pause while a client loads, resume after the idle gap
# ---------------------------------------------------------------------------


def test_client_miss_pauses_the_running_prefill_and_tags_it_client(settings, conn) -> None:
    known_runner(conn)
    job_id = running_job(conn)
    clock = Clock(NOW)
    controller, _tier = make_controller(settings, clock)
    miss(settings, clock, BIG, addr=RUNNER)  # the runner's own MISS: no reason to hold
    assert controller.tick(conn).holding is False
    assert job(conn, job_id)["stop_request"] is None

    clock.advance(5)
    miss(settings, clock, BIG)
    state = controller.tick(conn)

    assert state.holding is True
    assert controller.holding() is True  # the worker's gate
    assert [c.addr for c in state.clients] == [PC]
    assert state.since == clock() and state.last_client_at == clock()
    assert state.resume_at == clock() + timedelta(seconds=GAP)
    row = job(conn, job_id)
    assert row["status"] == jobs.STATUS_RUNNING
    assert row["stop_request"] == jobs.STOP_REQUEST_PAUSE
    assert row["pause_reason"] == jobs.PAUSE_REASON_CLIENT

    worker_parks(conn, job_id)
    row = job(conn, job_id)
    assert row["status"] == jobs.STATUS_PAUSED
    assert row["pause_reason"] == jobs.PAUSE_REASON_CLIENT


def test_resume_only_after_the_idle_gap_and_the_job_keeps_its_place(settings, conn, tmp_path) -> None:
    known_runner(conn)
    # The cache is the progress store: a chunk the first attempt wrote.
    chunk = Path(settings.cache_root) / "depot" / "441" / "chunk"
    chunk.parent.mkdir(parents=True)
    chunk.write_bytes(b"x" * 64)
    job_id = running_job(conn)
    created_at = job(conn, job_id)["created_at"]
    clock = Clock(NOW)
    controller, _tier = make_controller(settings, clock)
    miss(settings, clock, BIG)
    controller.tick(conn)
    worker_parks(conn, job_id)
    paused_at = job(conn, job_id)["paused_at"]
    # Queued while paused: must not overtake the paused job.
    later, _ = jobs.enqueue_prefill(conn, 570)

    # The client keeps loading for a while: the hold follows its last MISS.
    for _ in range(6):
        clock.advance(10)
        miss(settings, clock, BIG)
        assert controller.tick(conn).holding is True
    last_client = clock()

    clock.advance(GAP - 5)  # idle, but not long enough
    state = controller.tick(conn)
    assert state.holding is True
    assert job(conn, job_id)["status"] == jobs.STATUS_PAUSED

    clock.advance(5)  # exactly the gap since the last client MISS
    state = controller.tick(conn)
    assert clock() - last_client == timedelta(seconds=GAP)
    assert state.holding is False and controller.holding() is False
    assert state.clients == ()
    row = job(conn, job_id)
    assert row["status"] == jobs.STATUS_QUEUED
    assert row["pause_reason"] is None and row["stop_request"] is None
    # Progress kept: same row, same place, nothing deleted, nothing failed.
    assert row["id"] == job_id and row["created_at"] == created_at
    assert row["paused_at"] == paused_at
    assert jobs.queue_positions(conn) == {job_id: 0, int(later["id"]): 1}  # type: ignore[arg-type]
    assert chunk.read_bytes() == b"x" * 64
    assert conn.execute("SELECT status FROM apps WHERE appid = 440").fetchone()[0] == "idle"
    claimed = jobs.claim_next_job(conn)
    assert claimed is not None and claimed["id"] == job_id


def test_a_small_client_miss_does_not_stop_the_runner(settings, conn) -> None:
    known_runner(conn)
    job_id = running_job(conn)
    clock = Clock(NOW)
    controller, _tier = make_controller(settings, clock)
    miss(settings, clock, SMALL)
    assert controller.tick(conn).holding is False
    assert job(conn, job_id)["stop_request"] is None


def test_two_clients_add_up_and_are_listed_highest_rate_first(settings, conn) -> None:
    known_runner(conn)
    running_job(conn)
    clock = Clock(NOW)
    controller, _tier = make_controller(settings, clock)
    # Each alone is below the floor (~300 KiB/s), together above it.
    miss(settings, clock, 5 * 1024 * 1024, addr=PC)
    miss(settings, clock, 6 * 1024 * 1024, addr=PC2)
    state = controller.tick(conn)
    assert state.holding is True
    assert [c.addr for c in state.clients] == [PC2, PC]


# ---------------------------------------------------------------------------
# Manual pauses are never resumed automatically
# ---------------------------------------------------------------------------


def test_a_manual_pause_is_never_resumed_by_the_controller(settings, conn) -> None:
    known_runner(conn)
    job_id = running_job(conn)
    assert jobs.request_pause(conn, job_id).outcome == jobs.CONTROL_REQUESTED
    assert job(conn, job_id)["pause_reason"] == jobs.PAUSE_REASON_MANUAL
    worker_parks(conn, job_id)
    clock = Clock(NOW)
    controller, _tier = make_controller(settings, clock)
    for _ in range(3):
        controller.tick(conn)
        clock.advance(GAP)
    row = job(conn, job_id)
    assert row["status"] == jobs.STATUS_PAUSED
    assert row["pause_reason"] == jobs.PAUSE_REASON_MANUAL


def test_a_manual_pause_during_a_hold_survives_the_release(settings, conn) -> None:
    known_runner(conn)
    job_id = running_job(conn)
    clock = Clock(NOW)
    controller, _tier = make_controller(settings, clock)
    miss(settings, clock, BIG)
    controller.tick(conn)
    # The operator presses Pause before the worker honoured the client pause.
    jobs.request_pause(conn, job_id)
    worker_parks(conn, job_id)
    assert job(conn, job_id)["pause_reason"] == jobs.PAUSE_REASON_MANUAL
    clock.advance(GAP)
    assert controller.tick(conn).holding is False
    assert job(conn, job_id)["status"] == jobs.STATUS_PAUSED


def test_pause_on_a_client_paused_job_makes_it_a_manual_pause(settings, conn) -> None:
    known_runner(conn)
    job_id = running_job(conn)
    clock = Clock(NOW)
    controller, _tier = make_controller(settings, clock)
    miss(settings, clock, BIG)
    controller.tick(conn)
    worker_parks(conn, job_id)

    result = jobs.request_pause(conn, job_id)
    assert result.outcome == jobs.CONTROL_IMMEDIATE
    assert result.detail == jobs.PAUSED_CLIENT_TO_MANUAL_MESSAGE
    assert result.job is not None and result.job["pause_reason"] == jobs.PAUSE_REASON_MANUAL
    # A second press is the old 409: it is already (manually) paused.
    assert jobs.request_pause(conn, job_id).outcome == jobs.CONTROL_CONFLICT

    clock.advance(GAP)
    controller.tick(conn)
    assert job(conn, job_id)["status"] == jobs.STATUS_PAUSED


def test_the_controller_never_overwrites_an_operator_request(settings, conn) -> None:
    known_runner(conn)
    job_id = running_job(conn)
    jobs.cancel_job(conn, job_id)  # cancel pending on the running job
    clock = Clock(NOW)
    controller, _tier = make_controller(settings, clock)
    miss(settings, clock, BIG)
    assert controller.tick(conn).holding is True
    row = job(conn, job_id)
    assert row["stop_request"] == jobs.STOP_REQUEST_CANCEL
    assert row["pause_reason"] is None


def test_manual_resume_of_a_client_paused_job_clears_the_tag(settings, conn) -> None:
    known_runner(conn)
    job_id = running_job(conn)
    clock = Clock(NOW)
    controller, _tier = make_controller(settings, clock)
    miss(settings, clock, BIG)
    controller.tick(conn)
    worker_parks(conn, job_id)
    assert jobs.resume_job(conn, job_id).outcome == jobs.CONTROL_RESUMED
    raw = conn.execute("SELECT pause_reason FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert raw[0] is None
    # Still holding: the worker's gate keeps the resumed job queued.
    clock.advance(5)
    miss(settings, clock, BIG)
    assert controller.tick(conn).holding is True
    assert job(conn, job_id)["status"] == jobs.STATUS_QUEUED


# ---------------------------------------------------------------------------
# Withdraw, restart, fail-safe directions
# ---------------------------------------------------------------------------


def test_a_client_pause_not_yet_honoured_is_withdrawn_at_release(settings, conn) -> None:
    known_runner(conn)
    job_id = running_job(conn)
    clock = Clock(NOW)
    controller, _tier = make_controller(settings, clock)
    miss(settings, clock, BIG)
    controller.tick(conn)
    assert job(conn, job_id)["stop_request"] == jobs.STOP_REQUEST_PAUSE
    clock.advance(GAP)
    controller.tick(conn)
    row = job(conn, job_id)
    assert row["status"] == jobs.STATUS_RUNNING
    assert row["stop_request"] is None and row["pause_reason"] is None


def test_a_restart_adopts_client_paused_jobs_and_resumes_them_after_the_gap(settings, conn) -> None:
    known_runner(conn)
    job_id = running_job(conn)
    clock = Clock(NOW)
    first, _tier = make_controller(settings, clock)
    miss(settings, clock, BIG)
    first.tick(conn)
    worker_parks(conn, job_id)

    clock.advance(600)  # vault-api restarts: a fresh controller, no memory
    second, _tier2 = make_controller(settings, clock)
    state = second.tick(conn)
    assert state.holding is True and state.since == clock()
    assert job(conn, job_id)["status"] == jobs.STATUS_PAUSED
    clock.advance(GAP)
    assert second.tick(conn).holding is False
    assert job(conn, job_id)["status"] == jobs.STATUS_QUEUED


def test_event_log_off_never_holds_and_resumes_leftover_client_pauses(tmp_path, conn, settings) -> None:
    known_runner(conn)
    job_id = running_job(conn)
    clock = Clock(NOW)
    controller, _tier = make_controller(settings, clock)
    miss(settings, clock, BIG)
    controller.tick(conn)
    worker_parks(conn, job_id)

    off = make_settings(tmp_path, log_path="")
    blind, _tier2 = make_controller(off, clock)
    state = blind.tick(conn)
    assert state.enabled is False and state.holding is False
    assert job(conn, job_id)["status"] == jobs.STATUS_QUEUED


def test_an_unknown_runner_address_never_holds(settings, conn) -> None:
    # No runner period and no marker: the runner's own MISS would look like a
    # client's and pause it in a loop.
    job_id = running_job(conn)
    clock = Clock(NOW)
    controller, _tier = make_controller(settings, clock)
    miss(settings, clock, BIG, addr=RUNNER)
    state = controller.tick(conn)
    assert state.holding is False and state.runner_address_unknown is True
    assert job(conn, job_id)["stop_request"] is None


def test_a_marker_in_the_ring_makes_a_new_runner_address_known(settings, conn) -> None:
    job_id = running_job(conn)
    clock = Clock(NOW)
    controller, tier = make_controller(settings, clock)
    append_log(
        Path(settings.event_log_path),
        event_line(
            time=iso(clock() - timedelta(seconds=10)),
            addr=RUNNER,
            cache_status=runner_identity.MARK_CACHE_STATUS,
            depot="-",
            uri="/steamhangar/runner-mark/" + "a" * 32,
            bytes_sent="0",
            http_status="204",
        ),
    )
    miss(settings, clock, BIG, addr=RUNNER)
    state = controller.tick(conn)
    assert tier.runner_mark_addrs(clock()) == frozenset({RUNNER})
    assert state.runner_address_unknown is False and state.holding is False
    clock.advance(5)
    miss(settings, clock, BIG, addr=PC)
    assert controller.tick(conn).holding is True
    assert job(conn, job_id)["pause_reason"] == jobs.PAUSE_REASON_CLIENT


def test_no_work_means_the_event_log_is_never_read(settings, conn, monkeypatch) -> None:
    clock = Clock(NOW)
    controller, tier = make_controller(settings, clock)
    miss(settings, clock, BIG)

    def boom(*_args, **_kwargs):
        raise AssertionError("the log was read with an empty queue")

    monkeypatch.setattr(tier, "refresh", boom)
    assert controller.tick(conn).holding is False
    assert tier.watcher_active(clock()) is False


def test_a_queued_job_is_held_without_touching_its_row(settings, conn) -> None:
    known_runner(conn)
    queued, _ = jobs.enqueue_prefill(conn, 440)
    clock = Clock(NOW)
    controller, _tier = make_controller(settings, clock)
    miss(settings, clock, BIG)
    assert controller.tick(conn).holding is True
    row = job(conn, int(queued["id"]))  # type: ignore[arg-type]
    assert row["status"] == jobs.STATUS_QUEUED and row["pause_reason"] is None


# ---------------------------------------------------------------------------
# The worker's gate
# ---------------------------------------------------------------------------


def _worker(settings: Settings, hold) -> PrefillWorker:
    return PrefillWorker(settings, hold=hold)


def test_the_worker_claims_nothing_while_holding(settings, conn, monkeypatch) -> None:
    queued, _ = jobs.enqueue_prefill(conn, 440)
    calls: list[str] = []
    worker: PrefillWorker

    def hold() -> bool:
        calls.append("hold")
        worker._stop.set()  # one loop iteration, then _run returns
        return True

    def claim(_conn):
        raise AssertionError("claimed while holding")

    worker = _worker(settings, hold)
    monkeypatch.setattr(jobs, "claim_next_job", claim)
    worker._run()
    assert calls == ["hold"]
    assert job(conn, int(queued["id"]))["status"] == jobs.STATUS_QUEUED  # type: ignore[arg-type]


def test_the_worker_claims_when_not_holding_and_when_the_gate_fails(settings, monkeypatch) -> None:
    for hold_answer in (False, RuntimeError("gate broken")):
        claimed: list[int] = []
        worker: PrefillWorker

        def hold(answer=hold_answer) -> bool:
            if isinstance(answer, Exception):
                raise answer
            return answer

        def claim(_conn, box=claimed):
            box.append(1)
            worker._stop.set()
            return None

        worker = _worker(settings, hold)
        monkeypatch.setattr(jobs, "claim_next_job", claim)
        worker._run()
        assert claimed == [1]


# ---------------------------------------------------------------------------
# API: pause_reason on the jobs, GET /v1/client-priority
# ---------------------------------------------------------------------------


def test_the_api_reports_the_reason_and_the_clients(settings, conn) -> None:
    known_runner(conn)
    job_id = running_job(conn)
    conn.execute(
        "INSERT INTO client_names (key_type, key, name, updated_at) VALUES ('ip', ?, ?, ?)",
        (PC, "Living room PC", jobs.to_utc_iso(NOW)),
    )
    conn.commit()
    clock = Clock(NOW)
    app = create_app(settings)  # no `with`: no lifespan thread
    tier = LiveTier(clock=clock)
    app.state.live_tier = tier
    client = TestClient(app)

    body = client.get("/v1/client-priority", headers=AUTH).json()
    assert body["enabled"] is True and body["holding"] is False
    assert body["idle_gap_seconds"] == GAP and body["clients"] == []

    miss(settings, clock, BIG)
    app.state.client_priority.tick(conn)
    listed = {j["id"]: j for j in client.get("/v1/jobs", headers=AUTH).json()}
    assert listed[job_id]["pause_reason"] == "client"
    worker_parks(conn, job_id)
    detail = client.get(f"/v1/jobs/{job_id}", headers=AUTH).json()
    assert detail["status"] == "paused" and detail["pause_reason"] == "client"

    body = client.get("/v1/client-priority", headers=AUTH).json()
    assert body["holding"] is True
    assert body["since"] == jobs.to_utc_iso(clock())
    assert body["resume_at"] == jobs.to_utc_iso(clock() + timedelta(seconds=GAP))
    assert body["paused_job_ids"] == [job_id]
    assert [(c["addr"], c["name"]) for c in body["clients"]] == [(PC, "Living room PC")]
    assert body["clients"][0]["miss_bytes_per_second"] == BIG / 17
    assert body["runner_address_unknown"] is False

    # Pause through the endpoint: the job becomes a manual pause.
    reply = client.post(f"/v1/jobs/{job_id}/pause", headers=AUTH)
    assert reply.status_code == 200 and reply.json()["outcome"] == "immediate"
    assert client.get(f"/v1/jobs/{job_id}", headers=AUTH).json()["pause_reason"] == "manual"
    assert client.get("/v1/client-priority", headers=AUTH).json()["paused_job_ids"] == []


def test_pause_reason_is_null_unless_paused_or_pausing(settings, conn) -> None:
    job_id = running_job(conn)
    assert job(conn, job_id)["pause_reason"] is None
    # A terminal row keeps no reason, even if a stale tag was left behind.
    conn.execute("UPDATE jobs SET pause_reason = 'client' WHERE id = ?", (job_id,))
    conn.commit()
    jobs.finish_job(conn, job_id, jobs.STATUS_DONE, "ok")
    row = conn.execute("SELECT pause_reason FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row[0] is None and job(conn, job_id)["pause_reason"] is None


def test_the_auth_guard_covers_the_new_endpoint(settings) -> None:
    client = TestClient(create_app(settings))
    assert client.get("/v1/client-priority").status_code == 401


# ---------------------------------------------------------------------------
# Schema v34
# ---------------------------------------------------------------------------


def test_v33_database_gains_pause_reason_and_old_pauses_read_manual(tmp_path) -> None:
    db_path = str(tmp_path / "old.db")
    init_db(db_path)
    c = get_connection(db_path)
    try:
        cols = [r for r in c.execute("PRAGMA table_info(jobs)") if r["name"] != "pause_reason"]
        c.execute("ALTER TABLE jobs RENAME TO jobs_new")
        c.execute(
            "CREATE TABLE jobs ("
            + ", ".join(f"{r['name']} {r['type']}" for r in cols)
            + ")"
        )
        c.execute(
            "INSERT INTO jobs (id, appid, type, status, created_at, paused_at) "
            "VALUES (7, 440, 'prefill', 'paused', '2026-10-01T00:00:00Z', '2026-10-01T00:01:00Z')"
        )
        c.execute("DROP TABLE jobs_new")
        c.execute("UPDATE schema_version SET version = 33")
        c.commit()
    finally:
        c.close()

    init_db(db_path)
    init_db(db_path)  # idempotent
    c = get_connection(db_path)
    try:
        types = {r["name"]: r["type"] for r in c.execute("PRAGMA table_info(jobs)")}
        version = c.execute("SELECT version FROM schema_version").fetchone()[0]
        assert types["pause_reason"] == "TEXT"
        assert version == SCHEMA_VERSION >= 34
        assert jobs.get_job(c, 7)["pause_reason"] == jobs.PAUSE_REASON_MANUAL  # type: ignore[index]
        assert jobs.resume_client_paused(c) == []
    finally:
        c.close()
