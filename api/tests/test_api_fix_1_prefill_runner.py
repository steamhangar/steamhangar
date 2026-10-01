"""WP API-FIX-1: ``prefill_runner`` resilience (P2) and abort wording (P4).

The runner-side half of the pre-freeze review's queue-mode findings. The
``prefill.py`` half (the wait loop reaping the child on ANY exception) is
pinned in ``test_prefill_runner.py``; this module pins what the RUNNER does
around it: a ``sqlite3.Error`` from the shared database must never kill the
poll loop (claim) nor abandon a live SteamPrefill (heartbeat / stop-request
read), and a runner SIGTERM must be recorded as the runner's shutdown, not
vault-api's.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pytest

from tests import stub_prefill
from vault_api import jobs, prefill, prefill_queue
from vault_api.config import Settings
from vault_api.db import get_connection, init_db
from vault_api import prefill_runner as prefill_runner_module
from vault_api.prefill_runner import ABORT_REASON_RUNNER, PrefillRunner
from vault_api.worker import PrefillWorker

TEST_API_KEY = "test-api-key-do-not-use-in-prod"


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    base = dict(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
        prefill_mode="queue",
        worker_poll_seconds=0.05,
        runner_poll_seconds=0.05,
        runner_heartbeat_seconds=0.0,  # heartbeat on EVERY tick, see below
        runner_lease_timeout_seconds=8.0,
        prefill_timeout_seconds=30,
    )
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _handed_off_job(db_path: str, appid: int = 440) -> int:
    """A job in exactly the state a runner polls for: claimed by vault-api's
    worker (status 'running') and handed off, nobody has claimed the run."""
    conn = get_connection(db_path)
    try:
        jobs.enqueue_prefill(conn, appid)
        claimed = jobs.claim_next_job(conn)
        assert claimed is not None
        job_id = int(claimed["id"])
        jobs.handoff_run(conn, job_id, True, "{}")
        return job_id
    finally:
        conn.close()


def _wait_until(predicate, *, timeout: float, label: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError(f"{label} did not happen within {timeout}s")


# -- P2: claim_run ---------------------------------------------------------------


def test_a_failing_claim_does_not_kill_the_poll_loop(tmp_path: Path, monkeypatch) -> None:
    """Same net vault-api's worker has around ``claim_next_job``: a
    ``sqlite3.Error`` out of ``claim_run`` is logged, the loop sleeps one
    poll and tries again. Before the fix it unwound ``run_forever`` and the
    runner process exited."""
    settings = _settings(tmp_path)
    init_db(settings.db_path)
    calls: list[int] = []
    real_claim = jobs.claim_run

    def flaky_claim(conn, runner_id):
        calls.append(1)
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked (simulated)")
        return real_claim(conn, runner_id)

    monkeypatch.setattr(jobs, "claim_run", flaky_claim)

    runner = PrefillRunner(settings, runner_id="test-runner-p2-claim")
    thread = threading.Thread(target=runner.run_forever, daemon=True)
    thread.start()
    try:
        _wait_until(lambda: len(calls) >= 3, timeout=5, label="polling after the error")
        assert thread.is_alive(), "the runner loop died on a claim error"
    finally:
        runner.stop()
        thread.join(timeout=5)
    assert not thread.is_alive()


# -- P2: heartbeat / stop-request inside the wait loop ------------------------


def _drive_callback_through_fake_run_prefill(
    monkeypatch, settings: Settings, job_id: int, ticks: int = 3
) -> list[str | None]:
    """Replace ``prefill.run_prefill`` with a fake that calls the runner's
    ``stop_request`` callback ``ticks`` times -- exactly what the real wait
    loop does on its 0.2s poll -- and records what each call returned (or
    lets an exception escape, which is the pre-fix failure)."""
    answers: list[str | None] = []

    def fake_run_prefill(**kwargs):
        stop_request = kwargs["stop_request"]
        for _ in range(ticks):
            answers.append(stop_request())
        return prefill.PrefillResult(True, None, 0, "fake run")

    monkeypatch.setattr(prefill, "run_prefill", fake_run_prefill)

    conn = get_connection(settings.db_path)
    try:
        job = jobs.claim_run(conn, "test-runner-p2-cb")
        assert job is not None and int(job["id"]) == job_id
        PrefillRunner(settings, runner_id="test-runner-p2-cb")._execute(conn, job)
    finally:
        conn.close()
    return answers


def test_a_failing_heartbeat_is_logged_and_read_as_no_stop_request(
    tmp_path: Path, monkeypatch
) -> None:
    """``record_run_heartbeat`` raising used to propagate out of the callback
    and through ``run_prefill``'s wait loop. Now: logged, callback answers
    the stop-request question normally."""
    settings = _settings(tmp_path)
    init_db(settings.db_path)
    job_id = _handed_off_job(settings.db_path)
    heartbeats: list[int] = []

    def broken_heartbeat(conn, job_id, runner_id):
        heartbeats.append(1)
        raise sqlite3.OperationalError("disk I/O error (simulated)")

    monkeypatch.setattr(jobs, "record_run_heartbeat", broken_heartbeat)

    answers = _drive_callback_through_fake_run_prefill(monkeypatch, settings, job_id)

    assert answers == [None, None, None]
    # runner_heartbeat_seconds=0.0 -> every tick retries the heartbeat; a
    # failed write must not stop later attempts either.
    assert len(heartbeats) == 3


def test_a_failing_stop_request_read_is_treated_as_none(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    init_db(settings.db_path)
    job_id = _handed_off_job(settings.db_path)

    def broken_read(conn, job_id):
        raise sqlite3.OperationalError("database is locked (simulated)")

    monkeypatch.setattr(jobs, "read_stop_request", broken_read)

    answers = _drive_callback_through_fake_run_prefill(monkeypatch, settings, job_id)

    assert answers == [None, None, None]


def test_a_real_stop_request_still_gets_through(tmp_path: Path, monkeypatch) -> None:
    """The resilience wrapper must not swallow the answer it exists to
    deliver: with a healthy database a pending 'cancel' is returned."""
    settings = _settings(tmp_path)
    init_db(settings.db_path)
    job_id = _handed_off_job(settings.db_path)
    conn = get_connection(settings.db_path)
    try:
        jobs.cancel_job(conn, job_id)
        assert jobs.read_stop_request(conn, job_id) == "cancel"
    finally:
        conn.close()

    answers = _drive_callback_through_fake_run_prefill(monkeypatch, settings, job_id, ticks=1)

    assert answers == ["cancel"]


# -- P4: a runner SIGTERM names the runner --------------------------------------


def test_runner_shutdown_is_recorded_as_the_runners_abort(tmp_path: Path) -> None:
    """End to end through the real runner and the real (hanging) stub: the
    runner's own stop event fires mid-run, the recorded result is
    ``aborted`` and its text blames THIS process, not vault-api."""
    bindir = tmp_path / "bin"
    executable = stub_prefill.make_stub(bindir, mode="hang")
    settings = _settings(tmp_path, steamprefill_path=executable)
    init_db(settings.db_path)
    job_id = _handed_off_job(settings.db_path)

    runner = PrefillRunner(settings, runner_id="test-runner-p4")
    thread = threading.Thread(target=runner.run_forever, daemon=True)
    thread.start()

    def _claimed() -> bool:
        conn = get_connection(settings.db_path)
        try:
            row = jobs.get_run_row(conn, job_id)
            return row is not None and row["run_claimed_by"] == "test-runner-p4"
        finally:
            conn.close()

    try:
        _wait_until(_claimed, timeout=5, label="runner claiming the job")
        time.sleep(0.3)  # let the stub actually start hanging
    finally:
        runner.stop()
        thread.join(timeout=15)
    assert not thread.is_alive(), "runner did not stop within the grace period"

    conn = get_connection(settings.db_path)
    try:
        row = jobs.get_run_row(conn, job_id)
    finally:
        conn.close()
    assert row is not None and row["run_completed_at"] is not None
    result = prefill_queue.decode_result(str(row["run_result_json"]))
    assert result.failure_reason == "aborted"
    assert f"Aborted: {ABORT_REASON_RUNNER}." in result.output
    assert "vault-api is shutting down" not in result.output


def test_runner_abort_reason_literal() -> None:
    """Pinned as a LITERAL (LEARNINGS: constants-vs-literals) -- the log
    line is what an operator greps for."""
    assert ABORT_REASON_RUNNER == "prefill_runner is shutting down"
    assert prefill.ABORT_REASON_VAULT_API == "vault-api is shutting down"


# -- S2: storing the result -----------------------------------------------------


def _run_once_with_result_writer(tmp_path: Path, monkeypatch, writer) -> tuple[Settings, int]:
    """Claim the handed-off job and run ``_execute`` with a fake SteamPrefill
    and the given ``record_run_result`` replacement; no real sleeps."""
    settings = _settings(tmp_path)
    init_db(settings.db_path)
    job_id = _handed_off_job(settings.db_path)
    monkeypatch.setattr(
        prefill, "run_prefill", lambda **kwargs: prefill.PrefillResult(True, None, 0, "fake run")
    )
    monkeypatch.setattr(jobs, "record_run_result", writer)
    monkeypatch.setattr(prefill_runner_module, "RESULT_WRITE_RETRY_SECONDS", 0.0)
    conn = get_connection(settings.db_path)
    try:
        job = jobs.claim_run(conn, "test-runner-s2")
        assert job is not None
        PrefillRunner(settings, runner_id="test-runner-s2")._execute(conn, job)
    finally:
        conn.close()
    return settings, job_id


def test_a_transient_result_write_error_is_retried(tmp_path: Path, monkeypatch) -> None:
    real_record = jobs.record_run_result
    calls: list[int] = []

    def flaky_record(conn, job_id, runner_id, result_json):
        calls.append(1)
        if len(calls) <= 2:
            raise sqlite3.OperationalError("database is locked (simulated)")
        return real_record(conn, job_id, runner_id, result_json)

    settings, job_id = _run_once_with_result_writer(tmp_path, monkeypatch, flaky_record)

    assert len(calls) == 3
    conn = get_connection(settings.db_path)
    try:
        row = jobs.get_run_row(conn, job_id)
    finally:
        conn.close()
    assert row is not None and row["run_completed_at"] is not None
    assert prefill_queue.decode_result(row["run_result_json"]).success


def test_a_persistent_result_write_error_gives_up_without_raising(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    calls: list[int] = []

    def broken_record(conn, job_id, runner_id, result_json):
        calls.append(1)
        raise sqlite3.OperationalError("disk I/O error (simulated)")

    with caplog.at_level("ERROR"):
        settings, job_id = _run_once_with_result_writer(tmp_path, monkeypatch, broken_record)

    assert len(calls) == prefill_runner_module.RESULT_WRITE_ATTEMPTS
    assert "GAVE UP" in caplog.text
    conn = get_connection(settings.db_path)
    try:
        row = jobs.get_run_row(conn, job_id)
    finally:
        conn.close()
    # Nothing recorded; vault-api's lease check is what fails this job.
    assert row is not None and row["run_completed_at"] is None


# -- S2 / N6: vault-api worker loop around find_active_run ---------------------


def test_a_failing_find_active_run_does_not_kill_the_worker(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    init_db(settings.db_path)
    calls: list[int] = []
    real_find = jobs.find_active_run

    def flaky_find(conn):
        calls.append(1)
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked (simulated)")
        return real_find(conn)

    monkeypatch.setattr(jobs, "find_active_run", flaky_find)
    worker = PrefillWorker(settings)
    worker.start()
    try:
        _wait_until(lambda: len(calls) >= 3, timeout=5, label="polling after the error")
        assert worker._thread is not None and worker._thread.is_alive()
    finally:
        worker.stop(timeout=5)


def test_an_unfinalizable_reattach_backs_off_instead_of_spinning(
    tmp_path: Path, monkeypatch
) -> None:
    """N6: if _resume_prefill cannot move the row on (broken DB), the row
    stays 'running' and find_active_run returns it again at once. The loop
    must wait a poll between attempts, not spin hot."""
    settings = _settings(tmp_path, worker_poll_seconds=0.2)
    init_db(settings.db_path)
    _handed_off_job(settings.db_path)
    attempts: list[int] = []
    monkeypatch.setattr(
        PrefillWorker, "_resume_prefill", lambda self, conn, row: attempts.append(1)
    )
    worker = PrefillWorker(settings)
    worker.start()
    try:
        time.sleep(0.7)
    finally:
        worker.stop(timeout=5)
    # ~0.7s / 0.2s poll -> about 4; a hot spin would be thousands.
    assert 1 <= len(attempts) <= 8, len(attempts)
