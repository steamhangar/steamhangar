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
from dataclasses import replace
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


def client_paused_job(conn: sqlite3.Connection, settings: Settings, appid: int = 730) -> int:
    """A second job, parked by a controller of its own, so a release has a
    client-paused row to resume next to the manual one under test."""
    job_id = running_job(conn, appid)
    clock = Clock(NOW)
    controller, _tier = make_controller(settings, clock)
    miss(settings, clock, BIG)
    controller.tick(conn)
    worker_parks(conn, job_id)
    return job_id


def test_a_manual_pause_is_never_resumed_by_the_controller(settings, conn) -> None:
    known_runner(conn)
    job_id = running_job(conn)
    assert jobs.request_pause(conn, job_id).outcome == jobs.CONTROL_REQUESTED
    assert job(conn, job_id)["pause_reason"] == jobs.PAUSE_REASON_MANUAL
    worker_parks(conn, job_id)
    other = client_paused_job(conn, settings)
    clock = Clock(NOW + timedelta(seconds=1))
    controller, _tier = make_controller(settings, clock)
    for _ in range(3):
        clock.advance(GAP)
        controller.tick(conn)
    # The release path ran (the client-paused job is back in the queue)...
    assert job(conn, other)["status"] == jobs.STATUS_QUEUED
    # ...and left the manual pause alone.
    row = job(conn, job_id)
    assert row["status"] == jobs.STATUS_PAUSED
    assert row["pause_reason"] == jobs.PAUSE_REASON_MANUAL


def test_an_old_paused_row_without_a_tag_is_never_resumed(settings, conn) -> None:
    # A row paused before schema v34 (pause_reason NULL) next to a client one.
    known_runner(conn)
    old = running_job(conn)
    jobs.request_pause(conn, old)
    worker_parks(conn, old)
    conn.execute("UPDATE jobs SET pause_reason = NULL WHERE id = ?", (old,))
    conn.commit()
    other = client_paused_job(conn, settings)
    assert jobs.resume_client_paused(conn) == [other]
    assert job(conn, old)["status"] == jobs.STATUS_PAUSED


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
    other = client_paused_job(conn, settings)
    clock.advance(GAP)
    assert controller.tick(conn).holding is False
    assert job(conn, other)["status"] == jobs.STATUS_QUEUED
    assert job(conn, job_id)["status"] == jobs.STATUS_PAUSED


def test_a_pending_manual_pause_survives_a_holding_tick(settings, conn) -> None:
    known_runner(conn)
    job_id = running_job(conn)
    jobs.request_pause(conn, job_id)
    clock = Clock(NOW)
    controller, _tier = make_controller(settings, clock)
    miss(settings, clock, BIG)
    assert controller.tick(conn).holding is True
    row = job(conn, job_id)
    assert row["stop_request"] == jobs.STOP_REQUEST_PAUSE
    assert row["pause_reason"] == jobs.PAUSE_REASON_MANUAL
    raw = conn.execute("SELECT pause_reason FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert raw[0] == jobs.PAUSE_REASON_MANUAL


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

    other = client_paused_job(conn, settings)
    clock.advance(GAP)
    controller.tick(conn)
    assert job(conn, other)["status"] == jobs.STATUS_QUEUED
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


# ---------------------------------------------------------------------------
# Operator override (Weg B, 2026-11-21): a manual Resume during a hold
# ---------------------------------------------------------------------------


def held_and_parked(settings: Settings, conn: sqlite3.Connection) -> tuple[ClientPriority, Clock, int]:
    known_runner(conn)
    job_id = running_job(conn)
    clock = Clock(NOW)
    controller, _tier = make_controller(settings, clock)
    miss(settings, clock, BIG)
    controller.tick(conn)
    worker_parks(conn, job_id)
    return controller, clock, job_id


def raw_override(conn: sqlite3.Connection, job_id: int) -> object:
    return conn.execute("SELECT client_override FROM jobs WHERE id = ?", (job_id,)).fetchone()[0]


def test_resume_during_a_hold_overrides_it_and_is_never_client_paused(settings, conn) -> None:
    controller, clock, job_id = held_and_parked(settings, conn)
    result = jobs.resume_job(conn, job_id, client_override=True)
    assert result.outcome == jobs.CONTROL_RESUMED
    assert result.detail.endswith(jobs.CLIENT_OVERRIDE_TAIL)
    row = job(conn, job_id)
    assert row["status"] == jobs.STATUS_QUEUED and row["client_override"] is True
    assert row["pause_reason"] is None
    # The gate lets it through while holding; nothing else.
    jobs.enqueue_prefill(conn, 730)
    claimed = jobs.claim_next_job(conn, overridden_only=True)
    assert claimed is not None and claimed["id"] == job_id
    assert jobs.claim_next_job(conn, overridden_only=True) is None
    # Running alongside the device: holding ticks never pause it.
    for _ in range(3):
        clock.advance(5)
        miss(settings, clock, BIG)
        assert controller.tick(conn).holding is True
    row = job(conn, job_id)
    assert row["stop_request"] is None and row["client_override"] is True
    assert jobs.client_override_ids(conn) == [job_id]


def test_resume_outside_a_hold_sets_no_override(settings, conn) -> None:
    _controller, _clock, job_id = held_and_parked(settings, conn)
    jobs.resume_job(conn, job_id)
    assert job(conn, job_id)["client_override"] is False
    assert raw_override(conn, job_id) is None


def test_the_override_ends_with_a_manual_pause(settings, conn) -> None:
    controller, clock, job_id = held_and_parked(settings, conn)
    # queued + override, then a manual pause of the queued job
    jobs.resume_job(conn, job_id, client_override=True)
    jobs.request_pause(conn, job_id)
    assert raw_override(conn, job_id) is None
    # resumed again with override, claimed, then a manual pause while running
    jobs.resume_job(conn, job_id, client_override=True)
    jobs.claim_next_job(conn, overridden_only=True)
    jobs.request_pause(conn, job_id)
    assert raw_override(conn, job_id) is None
    worker_parks(conn, job_id)
    # a manual pause of a client-paused job also ends a stale override
    conn.execute(
        "UPDATE jobs SET pause_reason = 'client', client_override = 1 WHERE id = ?", (job_id,)
    )
    conn.commit()
    jobs.request_pause(conn, job_id)
    assert raw_override(conn, job_id) is None


@pytest.mark.parametrize("end", ["finish", "cancel_queued", "cancel_paused", "recover"])
def test_the_override_ends_with_the_job(settings, conn, end) -> None:
    _controller, _clock, job_id = held_and_parked(settings, conn)
    jobs.resume_job(conn, job_id, client_override=True)
    if end == "finish":
        jobs.claim_next_job(conn, overridden_only=True)
        jobs.finish_job(conn, job_id, jobs.STATUS_DONE, "ok")
    elif end == "cancel_queued":
        jobs.cancel_job(conn, job_id)
    elif end == "cancel_paused":
        conn.execute("UPDATE jobs SET status = 'paused' WHERE id = ?", (job_id,))
        conn.commit()
        jobs.cancel_job(conn, job_id)
    else:
        jobs.claim_next_job(conn, overridden_only=True)
        assert jobs.recover_stale_jobs(conn) == 1
        conn.commit()
    assert raw_override(conn, job_id) is None
    row = conn.execute("SELECT pause_reason FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row[0] is None


def test_recover_clears_a_pending_client_pause_tag(settings, conn) -> None:
    known_runner(conn)
    job_id = running_job(conn)
    clock = Clock(NOW)
    controller, _tier = make_controller(settings, clock)
    miss(settings, clock, BIG)
    controller.tick(conn)
    assert jobs.recover_stale_jobs(conn) == 1
    conn.commit()
    raw = conn.execute(
        "SELECT status, pause_reason, stop_request FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    assert tuple(raw) == (jobs.STATUS_ERROR, None, None)


def test_an_automatic_resume_sets_no_override(settings, conn) -> None:
    controller, clock, job_id = held_and_parked(settings, conn)
    conn.execute("UPDATE jobs SET client_override = 1 WHERE id = ?", (job_id,))
    conn.commit()
    clock.advance(GAP)
    controller.tick(conn)
    assert job(conn, job_id)["status"] == jobs.STATUS_QUEUED
    assert raw_override(conn, job_id) is None


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
    # The tag stays on the row (inert while nothing is pending).
    raw = conn.execute("SELECT pause_reason FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert raw[0] == jobs.PAUSE_REASON_CLIENT


def test_a_job_parked_right_after_the_withdraw_is_resumed_on_the_next_tick(settings, conn) -> None:
    known_runner(conn)
    job_id = running_job(conn)
    clock = Clock(NOW)
    controller, _tier = make_controller(settings, clock)
    miss(settings, clock, BIG)
    controller.tick(conn)
    clock.advance(GAP)
    controller.tick(conn)  # release: the pending pause is withdrawn
    # The worker had read the request already and parks the job now.
    jobs.park_paused(conn, job_id, "[stub] SteamPrefill terminated")
    row = job(conn, job_id)
    assert row["status"] == jobs.STATUS_PAUSED
    assert row["pause_reason"] == jobs.PAUSE_REASON_CLIENT
    clock.advance(5)
    controller.tick(conn)
    assert job(conn, job_id)["status"] == jobs.STATUS_QUEUED


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
    assert state.restored is True and state.clients == ()
    assert job(conn, job_id)["status"] == jobs.STATUS_PAUSED
    clock.advance(GAP)
    state = second.tick(conn)
    assert state.holding is False and state.restored is False
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


def marker(settings: Settings, clock: Clock, token: str, addr: str = RUNNER) -> None:
    append_log(
        Path(settings.event_log_path),
        event_line(
            time=iso(clock() - timedelta(seconds=10)),
            addr=addr,
            cache_status=runner_identity.MARK_CACHE_STATUS,
            depot="-",
            uri=runner_identity.mark_path(token),
            bytes_sent="0",
            http_status="204",
        ),
    )


def test_a_marker_in_the_ring_makes_a_new_runner_address_known(settings, conn) -> None:
    job_id = running_job(conn)
    clock = Clock(NOW)
    controller, tier = make_controller(settings, clock)
    token = runner_identity.issue_token(conn, "vault-runner", now=clock() - timedelta(seconds=12))
    marker(settings, clock, token)
    miss(settings, clock, BIG, addr=RUNNER)
    state = controller.tick(conn)
    assert [m[:2] for m in tier.runner_marks(clock())] == [(RUNNER, token)]
    assert state.runner_address_unknown is False and state.holding is False
    clock.advance(5)
    miss(settings, clock, BIG, addr=PC)
    assert controller.tick(conn).holding is True
    assert job(conn, job_id)["pause_reason"] == jobs.PAUSE_REASON_CLIENT


def test_a_fake_marker_from_a_lan_host_changes_nothing(settings, conn) -> None:
    # vault-core logs any request to the marker path; a token nobody issued
    # must neither make the runner "known" nor make the host a runner.
    job_id = running_job(conn)
    clock = Clock(NOW)
    controller, tier = make_controller(settings, clock)
    marker(settings, clock, "b" * 32, addr=PC)
    miss(settings, clock, BIG, addr=RUNNER)
    state = controller.tick(conn)
    assert [m[0] for m in tier.runner_marks(clock())] == [PC]
    assert state.runner_address_unknown is True and state.holding is False
    assert job(conn, job_id)["stop_request"] is None


def test_live_mark_rules_match_the_sweep(settings, conn) -> None:
    issued = NOW - timedelta(minutes=2)
    token = runner_identity.issue_token(conn, "vault-runner", now=issued)
    valid = runner_identity.live_mark_valid
    assert valid(conn, token, RUNNER, issued) is True
    assert valid(conn, "c" * 32, RUNNER, issued) is False  # never issued
    assert valid(conn, token, RUNNER, issued + runner_identity.MARK_VALID_AFTER + timedelta(seconds=1)) is False
    assert valid(conn, token, RUNNER, issued - runner_identity.MARK_VALID_BEFORE - timedelta(seconds=1)) is False
    conn.execute("UPDATE runner_marks SET consumed_addr = ? WHERE token = ?", (RUNNER, token))
    conn.commit()
    assert valid(conn, token, RUNNER, issued) is True  # consumed by this address
    assert valid(conn, token, PC, issued) is False  # replayed from another host


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


def run_worker_once(settings: Settings, answer, monkeypatch) -> list[int]:
    """One loop of the real worker with the real claim; the job body is
    replaced, so nothing runs. Returns the claimed job ids."""
    executed: list[int] = []
    worker: PrefillWorker

    def hold() -> bool:
        worker._stop.set()  # one loop iteration, then _run returns
        if isinstance(answer, Exception):
            raise answer
        return answer

    worker = _worker(settings, hold)
    monkeypatch.setattr(
        worker, "_execute", lambda _conn, claimed: executed.append(int(claimed["id"]))
    )
    worker._run()
    return executed


def test_the_worker_claims_nothing_while_holding(settings, conn, monkeypatch) -> None:
    queued, _ = jobs.enqueue_prefill(conn, 440)
    assert run_worker_once(settings, True, monkeypatch) == []
    assert job(conn, int(queued["id"]))["status"] == jobs.STATUS_QUEUED  # type: ignore[arg-type]


def test_the_worker_claims_only_an_overridden_job_while_holding(settings, conn, monkeypatch) -> None:
    first, _ = jobs.enqueue_prefill(conn, 440)
    second, _ = jobs.enqueue_prefill(conn, 730)
    conn.execute("UPDATE jobs SET client_override = 1 WHERE id = ?", (second["id"],))
    conn.commit()
    assert run_worker_once(settings, True, monkeypatch) == [second["id"]]
    assert job(conn, int(first["id"]))["status"] == jobs.STATUS_QUEUED  # type: ignore[arg-type]


@pytest.mark.parametrize("answer", [False, RuntimeError("gate broken")])
def test_the_worker_claims_in_order_when_not_holding_or_the_gate_fails(
    settings, conn, monkeypatch, answer
) -> None:
    first, _ = jobs.enqueue_prefill(conn, 440)
    second, _ = jobs.enqueue_prefill(conn, 730)
    conn.execute("UPDATE jobs SET client_override = 1 WHERE id = ?", (second["id"],))
    conn.commit()
    assert run_worker_once(settings, answer, monkeypatch) == [first["id"]]


def test_the_lifespan_starts_the_controller_and_wires_the_gate(settings) -> None:
    app = create_app(settings)
    with TestClient(app):
        controller: ClientPriority = app.state.client_priority
        worker: PrefillWorker = app.state.worker
        assert controller._thread is not None and controller._thread.is_alive()
        assert worker._held() is False
        with controller._lock:
            controller._state = replace(controller._state, holding=True)
        assert worker._held() is True
        with controller._lock:
            controller._state = replace(controller._state, holding=False)
        assert worker._held() is False
    assert not controller._thread.is_alive()


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


def test_the_resume_endpoint_overrides_only_while_holding(settings, conn) -> None:
    known_runner(conn)
    job_id = running_job(conn)
    clock = Clock(NOW)
    app = create_app(settings)  # no `with`: no lifespan thread
    app.state.live_tier = LiveTier(clock=clock)
    client = TestClient(app)
    miss(settings, clock, BIG)
    app.state.client_priority.tick(conn)
    worker_parks(conn, job_id)
    body = client.get("/v1/client-priority", headers=AUTH).json()
    assert body["override_job_ids"] == [] and body["restored"] is False

    reply = client.post(f"/v1/jobs/{job_id}/resume", headers=AUTH)
    assert reply.status_code == 200 and reply.json()["outcome"] == "resumed"
    assert jobs.CLIENT_OVERRIDE_TAIL in reply.json()["detail"]
    assert client.get(f"/v1/jobs/{job_id}", headers=AUTH).json()["client_override"] is True
    listed = {j["id"]: j for j in client.get("/v1/jobs", headers=AUTH).json()}
    assert listed[job_id]["client_override"] is True
    assert client.get("/v1/client-priority", headers=AUTH).json()["override_job_ids"] == [job_id]

    # Not holding: a resume is a plain resume.
    client.post(f"/v1/jobs/{job_id}/pause", headers=AUTH)
    clock.advance(GAP)
    assert app.state.client_priority.tick(conn).holding is False
    reply = client.post(f"/v1/jobs/{job_id}/resume", headers=AUTH)
    assert jobs.CLIENT_OVERRIDE_TAIL not in reply.json()["detail"]
    assert client.get(f"/v1/jobs/{job_id}", headers=AUTH).json()["client_override"] is False


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
