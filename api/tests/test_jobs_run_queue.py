"""WP S-1 (ADR-0012): the queue-mode job hand-off primitives in
``vault_api.jobs`` — ``handoff_run``, ``claim_run``, ``record_run_heartbeat``,
``record_run_result``, ``find_active_run``, ``run_is_stale``, and the
``recover_stale_jobs(queue_mode=...)`` narrowing.

These are unit tests against the DB layer directly (no worker thread, no
runner process, no subprocess) — the full end-to-end flow through a real
``PrefillWorker`` + ``PrefillRunner`` pair lives in
``tests/test_prefill_runner_process.py``.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from vault_api import jobs, prefill_queue
from vault_api.db import get_connection, init_db


@pytest.fixture
def db_path(tmp_path) -> str:
    path = str(tmp_path / "vault.db")
    init_db(path)
    return path


@pytest.fixture
def conn(db_path):
    connection = get_connection(db_path)
    yield connection
    connection.close()


def _make_running_prefill_job(conn, appid: int = 440) -> int:
    """A job already claimed by ``claim_next_job`` (status='running',
    started_at set) — the state ``handoff_run``/``claim_run`` operate on."""
    job, _created = jobs.enqueue_prefill(conn, appid)
    claimed = jobs.claim_next_job(conn)
    assert claimed is not None
    return int(claimed["id"])


# -- handoff_run -------------------------------------------------------------


def test_handoff_run_sets_use_force_and_before(conn) -> None:
    job_id = _make_running_prefill_job(conn)

    jobs.handoff_run(conn, job_id, True, '{"441": [1, 2, 3]}')

    row = jobs.get_run_row(conn, job_id)
    assert row is not None
    assert row["run_use_force"] == 1
    assert row["run_before_json"] == '{"441": [1, 2, 3]}'


def test_handoff_run_overwrites_use_force_and_before_on_a_second_call(conn) -> None:
    """WP S-1 round-2 (blocker B1): handoff_run is an unconditional
    FRESH-ATTEMPT write, not write-once. The restart-reattach path never
    calls this function at all (it reads the row back instead — see
    ``PrefillWorker._resume_prefill``), so the only real caller is the
    fresh-claim path, invoked once per genuinely NEW run of a job row — the
    shape a resumed (paused -> queued -> claimed again) job produces. A
    second call must overwrite the first attempt's values, not preserve
    them."""
    job_id = _make_running_prefill_job(conn)

    jobs.handoff_run(conn, job_id, True, '{"441": [1, 2, 3]}')
    jobs.handoff_run(conn, job_id, False, '{"999": [9, 9, 9]}')

    row = jobs.get_run_row(conn, job_id)
    assert row is not None
    assert row["run_use_force"] == 0
    assert row["run_before_json"] == '{"999": [9, 9, 9]}'


def test_handoff_run_resets_every_runner_owned_column_on_a_second_call(conn) -> None:
    """The other half of the B1 fix: a second hand-off must clear whatever a
    PREVIOUS attempt's runner left behind (claim, heartbeat, and especially
    the completed result) — otherwise a resumed job's ``await_run_result``
    would immediately observe the OLD result and never let the job actually
    re-run (the exact bug the reviewer measured end-to-end: ``argv.json``
    never recreated, ``run_completed_at`` byte-identical after resume)."""
    job_id = _make_running_prefill_job(conn)
    jobs.handoff_run(conn, job_id, True, "{}")
    jobs.claim_run(conn, "runner-a")
    jobs.record_run_heartbeat(conn, job_id, "runner-a")
    jobs.record_run_result(
        conn, job_id, "runner-a",
        '{"success": false, "failure_reason": "paused", "exit_code": null, "output": "x"}',
    )
    before = jobs.get_run_row(conn, job_id)
    assert before is not None
    assert before["run_claimed_by"] == "runner-a"
    assert before["run_completed_at"] is not None

    jobs.handoff_run(conn, job_id, True, "{}")

    after = jobs.get_run_row(conn, job_id)
    assert after is not None
    assert after["run_claimed_by"] is None
    assert after["run_claimed_at"] is None
    # WP API-FIX-1 (B2): the heartbeat column is no longer cleared but
    # restamped -- it is the lease clock for the NEW attempt (see
    # test_handoff_run_stamps_the_lease_clock below), so "reset" here means
    # "not the previous runner's value", not NULL.
    assert after["run_heartbeat_at"] is not None
    assert after["run_completed_at"] is None
    assert after["run_result_json"] is None


# -- claim_run: the atomic claim --------------------------------------------


def test_claim_run_claims_a_handed_off_job(conn) -> None:
    job_id = _make_running_prefill_job(conn)
    jobs.handoff_run(conn, job_id, True, "{}")

    claimed = jobs.claim_run(conn, "runner-a")

    assert claimed is not None
    assert int(claimed["id"]) == job_id
    assert claimed["run_claimed_by"] == "runner-a"
    assert claimed["run_claimed_at"] is not None
    assert claimed["run_heartbeat_at"] == claimed["run_claimed_at"]


def test_claim_run_ignores_a_job_not_yet_handed_off(conn) -> None:
    _make_running_prefill_job(conn)  # run_use_force stays NULL

    assert jobs.claim_run(conn, "runner-a") is None


def test_claim_run_ignores_a_completed_job(conn) -> None:
    job_id = _make_running_prefill_job(conn)
    jobs.handoff_run(conn, job_id, True, "{}")
    jobs.claim_run(conn, "runner-a")
    jobs.record_run_result(conn, job_id, "runner-a", '{"success": true}')

    assert jobs.claim_run(conn, "runner-b") is None


def test_claim_run_ignores_a_gc_job(conn) -> None:
    # GC jobs never carry run_use_force (worker.py never hands them off), so
    # this is really the same "run_use_force IS NOT NULL" guard, but pinned
    # by name since GC sharing this table is load-bearing elsewhere (jobs.py
    # module docstring: "one worker means a GC job can never unlink chunks
    # out of a depot SteamPrefill is downloading into").
    job, _created = jobs.enqueue_gc(conn, 440, execute=False)
    jobs.claim_next_job(conn)

    assert jobs.claim_run(conn, "runner-a") is None


def test_claim_run_two_concurrent_claimers_exactly_one_wins(db_path) -> None:
    """The mutation bar's real threaded/multiprocess claim race.

    20 threads, EACH with its OWN sqlite3 connection (the project's own
    thread-confinement rule — a shared connection across threads is not a
    legitimate scenario to test), synchronized with a Barrier so every
    thread calls ``claim_run`` for the SAME job at, as close as the OS
    scheduler allows, the same instant. Exactly one call must return a job;
    every other call must return ``None``; the row afterwards must show
    ``run_claimed_by`` set to exactly the winner's id.
    """
    setup_conn = get_connection(db_path)
    try:
        job_id = _make_running_prefill_job(setup_conn, appid=440)
        jobs.handoff_run(setup_conn, job_id, True, "{}")
    finally:
        setup_conn.close()

    n_racers = 20
    barrier = threading.Barrier(n_racers)
    results: list[dict[str, object] | None] = [None] * n_racers
    errors: list[BaseException] = []

    def racer(index: int) -> None:
        conn = get_connection(db_path)
        try:
            barrier.wait(timeout=5)
            results[index] = jobs.claim_run(conn, f"runner-{index}")
        except BaseException as exc:  # pragma: no cover - failure path only
            errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=racer, args=(i,)) for i in range(n_racers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors, f"claim_run raised under contention: {errors}"

    winners = [r for r in results if r is not None]
    assert len(winners) == 1, (
        f"expected exactly one winner among {n_racers} concurrent claimers, "
        f"got {len(winners)}: {winners}"
    )

    verify_conn = get_connection(db_path)
    try:
        row = jobs.get_run_row(verify_conn, job_id)
    finally:
        verify_conn.close()
    assert row is not None
    assert row["run_claimed_by"] == winners[0]["run_claimed_by"]


# -- record_run_heartbeat / record_run_result: guarded writes ---------------


def test_record_run_heartbeat_updates_when_claim_and_status_match(conn) -> None:
    job_id = _make_running_prefill_job(conn)
    jobs.handoff_run(conn, job_id, True, "{}")
    jobs.claim_run(conn, "runner-a")

    applied = jobs.record_run_heartbeat(conn, job_id, "runner-a")

    assert applied is True
    row = jobs.get_run_row(conn, job_id)
    assert row is not None and row["run_heartbeat_at"] is not None


def test_record_run_heartbeat_is_a_noop_for_the_wrong_runner_id(conn) -> None:
    job_id = _make_running_prefill_job(conn)
    jobs.handoff_run(conn, job_id, True, "{}")
    jobs.claim_run(conn, "runner-a")

    applied = jobs.record_run_heartbeat(conn, job_id, "someone-else")

    assert applied is False


def test_record_run_result_is_a_noop_once_the_job_is_terminal(conn) -> None:
    """The safety property behind the "no lease-stealing reclaim" design:
    once vault-api finalizes a job (simulated here via jobs.finish_job), a
    late write from the runner that used to own it must not resurrect it."""
    job_id = _make_running_prefill_job(conn)
    jobs.handoff_run(conn, job_id, True, "{}")
    jobs.claim_run(conn, "runner-a")

    jobs.finish_job(conn, job_id, jobs.STATUS_ERROR, "declared dead")

    applied = jobs.record_run_result(conn, job_id, "runner-a", '{"success": true}')

    assert applied is False
    row = jobs.get_job(conn, job_id)
    assert row is not None and row["status"] == jobs.STATUS_ERROR


# -- find_active_run ---------------------------------------------------------


def test_find_active_run_finds_a_handed_off_incomplete_job(conn) -> None:
    job_id = _make_running_prefill_job(conn)
    jobs.handoff_run(conn, job_id, True, "{}")

    found = jobs.find_active_run(conn)

    assert found is not None
    assert int(found["id"]) == job_id


def test_find_active_run_ignores_a_job_never_handed_off(conn) -> None:
    _make_running_prefill_job(conn)

    assert jobs.find_active_run(conn) is None


def test_find_active_run_returns_a_completed_but_uncollected_job(conn) -> None:
    """WP API-FIX-1 (B1) -- the INVERSE of the pin that shipped with S-1.
    A runner that finished while vault-api was down leaves a row that is
    still 'running' with ``run_completed_at`` set; nothing but the reattach
    path can ever finalize it, so the reattach lookup must return it.
    The old ``run_completed_at IS NULL`` filter left such a job 'running'
    forever (dedupe onto a dead id, 409 on cache delete)."""
    job_id = _make_running_prefill_job(conn)
    jobs.handoff_run(conn, job_id, True, "{}")
    jobs.claim_run(conn, "runner-a")
    jobs.record_run_result(conn, job_id, "runner-a", '{"success": true}')

    found = jobs.find_active_run(conn)

    assert found is not None
    assert int(found["id"]) == job_id
    assert found["run_completed_at"] is not None


def test_find_active_run_ignores_a_job_vault_api_already_finalized(conn) -> None:
    """The boundary the B1 fix must not cross: once vault-api has moved the
    row out of 'running', it is not 'active' any more even though its
    run_* columns are fully populated."""
    job_id = _make_running_prefill_job(conn)
    jobs.handoff_run(conn, job_id, True, "{}")
    jobs.claim_run(conn, "runner-a")
    jobs.record_run_result(conn, job_id, "runner-a", '{"success": true}')
    jobs.finish_job(conn, job_id, jobs.STATUS_DONE, "collected")

    assert jobs.find_active_run(conn) is None


# -- run_is_stale -------------------------------------------------------------


def _iso(dt: datetime) -> str:
    return jobs.to_utc_iso(dt)


def test_run_is_stale_false_when_heartbeat_is_fresh() -> None:
    now = datetime.now(timezone.utc)
    row = {"run_heartbeat_at": _iso(now - timedelta(seconds=2))}

    assert jobs.run_is_stale(row, lease_timeout_seconds=30, now=now) is False


def test_run_is_stale_true_when_heartbeat_exceeds_the_lease(monkeypatch=None) -> None:
    now = datetime.now(timezone.utc)
    row = {"run_heartbeat_at": _iso(now - timedelta(seconds=31))}

    assert jobs.run_is_stale(row, lease_timeout_seconds=30, now=now) is True


def test_run_is_stale_falls_back_to_claimed_at_with_no_heartbeat() -> None:
    now = datetime.now(timezone.utc)
    row = {"run_heartbeat_at": None, "run_claimed_at": _iso(now - timedelta(seconds=31))}

    assert jobs.run_is_stale(row, lease_timeout_seconds=30, now=now) is True


def test_run_is_stale_falls_back_to_started_at_when_never_claimed() -> None:
    now = datetime.now(timezone.utc)
    row = {
        "run_heartbeat_at": None,
        "run_claimed_at": None,
        "started_at": _iso(now - timedelta(seconds=31)),
    }

    assert jobs.run_is_stale(row, lease_timeout_seconds=30, now=now) is True


def test_run_is_stale_false_once_the_run_has_completed() -> None:
    now = datetime.now(timezone.utc)
    row = {
        "run_completed_at": _iso(now),
        "run_heartbeat_at": _iso(now - timedelta(days=1)),
    }

    assert jobs.run_is_stale(row, lease_timeout_seconds=30, now=now) is False


def test_run_is_stale_false_when_there_is_no_timestamp_at_all() -> None:
    """Fail-toward-'don't declare a job dead on missing data' — same
    direction as routers/clients.py's bypass-detection default."""
    row = {"run_heartbeat_at": None, "run_claimed_at": None, "started_at": None}

    assert jobs.run_is_stale(row, lease_timeout_seconds=30) is False


# -- recover_stale_jobs: mode-aware narrowing --------------------------------


def test_recover_stale_jobs_subprocess_mode_is_unchanged(conn) -> None:
    """Regression pin: the default (``queue_mode=False``) call must still
    fail EVERY 'running' row, exactly as before WP S-1 — including one that
    happens to have ``run_use_force`` set (a job started in queue mode, then
    the operator switched VAULT_PREFILL_MODE back to subprocess without
    resolving it first; still an orphan by the one-process rule)."""
    job_id = _make_running_prefill_job(conn)
    jobs.handoff_run(conn, job_id, True, "{}")

    recovered = jobs.recover_stale_jobs(conn)

    assert recovered == 1
    row = jobs.get_job(conn, job_id)
    assert row is not None and row["status"] == jobs.STATUS_ERROR


def test_recover_stale_jobs_queue_mode_leaves_a_handed_off_job_running(conn) -> None:
    job_id = _make_running_prefill_job(conn)
    jobs.handoff_run(conn, job_id, True, "{}")

    recovered = jobs.recover_stale_jobs(conn, queue_mode=True)

    assert recovered == 0
    row = jobs.get_job(conn, job_id)
    assert row is not None and row["status"] == jobs.STATUS_RUNNING


def test_recover_stale_jobs_queue_mode_still_fails_a_job_never_handed_off(conn) -> None:
    """The narrow single-process-orphan window queue mode does NOT cover:
    vault-api died between claim_next_job and handoff_run, so no runner was
    ever told about this job — it is exactly as orphaned as before."""
    _make_running_prefill_job(conn)  # no handoff_run call

    recovered = jobs.recover_stale_jobs(conn, queue_mode=True)

    assert recovered == 1


def test_recover_stale_jobs_queue_mode_still_fails_a_stale_gc_job(conn) -> None:
    """GC never goes through the runner split; queue_mode must not change
    its recovery."""
    jobs.enqueue_gc(conn, 440, execute=False)
    jobs.claim_next_job(conn)

    recovered = jobs.recover_stale_jobs(conn, queue_mode=True)

    assert recovered == 1


def test_recover_stale_jobs_queue_mode_still_respects_the_paused_exemption(conn) -> None:
    """The pre-existing 'paused' exemption (WP 3.12) must survive unchanged
    alongside the new queue_mode narrowing."""
    job, _created = jobs.enqueue_prefill(conn, 440)
    claimed = jobs.claim_next_job(conn)
    job_id = int(claimed["id"])
    jobs.request_pause(conn, job_id)
    jobs.park_paused(conn, job_id, "paused for the test")

    recovered = jobs.recover_stale_jobs(conn, queue_mode=True)

    assert recovered == 0
    row = jobs.get_job(conn, job_id)
    assert row is not None and row["status"] == jobs.STATUS_PAUSED


# -- WP API-FIX-1 (B2): the lease starts at hand-off, not at claim_next_job --


def test_run_is_stale_measures_an_unclaimed_job_from_the_handoff_stamp() -> None:
    """The unit shape of B2: ``started_at`` is 60s old (vault-api spent that
    long in its pre-hand-off cache walk), the hand-off stamped
    ``run_heartbeat_at`` 1s ago, no runner has claimed yet -- NOT stale."""
    now = datetime.now(timezone.utc)
    row = {
        "run_heartbeat_at": _iso(now - timedelta(seconds=1)),
        "run_claimed_at": None,
        "run_claimed_by": None,
        "started_at": _iso(now - timedelta(seconds=60)),
    }

    assert jobs.run_is_stale(row, lease_timeout_seconds=30, now=now) is False


def test_handoff_run_stamps_the_lease_clock(conn) -> None:
    """DB-level pin of B2: a job whose ``started_at`` already predates the
    lease window is handed off, and the row as written is NOT stale --
    because ``handoff_run`` wrote ``run_heartbeat_at`` = now. Reverting the
    stamp to NULL makes ``run_is_stale`` fall back to ``started_at`` and
    this test fails."""
    job_id = _make_running_prefill_job(conn)
    long_ago = _iso(datetime.now(timezone.utc) - timedelta(seconds=60))
    conn.execute("UPDATE jobs SET started_at = ? WHERE id = ?", (long_ago, job_id))
    conn.commit()

    jobs.handoff_run(conn, job_id, True, "{}")

    row = jobs.get_run_row(conn, job_id)
    assert row is not None
    assert row["run_claimed_by"] is None
    assert row["run_heartbeat_at"] is not None
    assert row["run_heartbeat_at"] != long_ago
    assert jobs.run_is_stale(row, lease_timeout_seconds=30) is False


def test_handoff_stamp_still_goes_stale_when_no_runner_ever_claims(conn) -> None:
    """B2 must not turn the lease off: an unclaimed hand-off older than the
    lease is still presumed to have no runner behind it."""
    job_id = _make_running_prefill_job(conn)
    jobs.handoff_run(conn, job_id, True, "{}")
    row = jobs.get_run_row(conn, job_id)
    assert row is not None

    later = datetime.now(timezone.utc) + timedelta(seconds=31)

    assert jobs.run_is_stale(row, lease_timeout_seconds=30, now=later) is True


# -- WP API-FIX-1 (P3): recover_stale_jobs is a terminal transition too -----


def test_recover_stale_jobs_clears_a_pending_stop_request(conn) -> None:
    """db.py promises ``stop_request`` is cleared at EVERY terminal
    transition; the crash-recovery path is one and used to skip it, leaving
    a 'pause'/'cancel' flag on an 'error' row forever."""
    job_id = _make_running_prefill_job(conn)
    jobs.request_pause(conn, job_id)
    before = jobs.get_job(conn, job_id)
    assert before is not None and before["stop_request"] == "pause"

    assert jobs.recover_stale_jobs(conn) == 1

    after = jobs.get_job(conn, job_id)
    assert after is not None
    assert after["status"] == jobs.STATUS_ERROR
    assert after["stop_request"] is None


# -- resume_job resets the hand-off columns (WP API-FIX-1, S1) ---------------


def _paused_queue_job(conn) -> int:
    """A queue-mode job whose runner recorded a 'paused' result and which
    vault-api then parked at 'paused' — every run_* column is populated."""
    job_id = _make_running_prefill_job(conn)
    jobs.handoff_run(conn, job_id, True, '{"441": [1, 2, 3]}')
    assert jobs.claim_run(conn, "runner-x") is not None
    assert jobs.record_run_result(conn, job_id, "runner-x", '{"paused": true}')
    assert jobs.park_paused(conn, job_id, "paused")["status"] == jobs.STATUS_PAUSED
    return job_id


def test_resume_job_clears_every_run_column(conn) -> None:
    job_id = _paused_queue_job(conn)

    assert jobs.resume_job(conn, job_id).outcome == jobs.CONTROL_RESUMED

    row = jobs.get_run_row(conn, job_id)
    assert row is not None and row["status"] == jobs.STATUS_QUEUED
    for column in (
        "run_use_force", "run_before_json", "run_claimed_by", "run_claimed_at",
        "run_heartbeat_at", "run_completed_at", "run_result_json",
    ):
        assert row[column] is None, column


def test_restart_between_resume_claim_and_handoff_fails_the_orphan(conn) -> None:
    """The S1 window: resume -> claim_next_job -> vault-api dies before
    handoff_run. The row must look never-handed-off: recovery fails it, and
    find_active_run must not deliver the paused attempt's stale result."""
    job_id = _paused_queue_job(conn)
    jobs.resume_job(conn, job_id)
    claimed = jobs.claim_next_job(conn)
    assert claimed is not None and int(claimed["id"]) == job_id

    # Simulated restart: startup recovery runs before the worker's first tick.
    assert jobs.recover_stale_jobs(conn, queue_mode=True) == 1
    assert jobs.find_active_run(conn) is None
    after = jobs.get_job(conn, job_id)
    assert after is not None and after["status"] == jobs.STATUS_ERROR
    assert after["log_excerpt"] == jobs.STALE_JOB_MESSAGE
