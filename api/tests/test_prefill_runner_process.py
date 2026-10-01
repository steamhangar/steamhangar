"""WP S-1 (ADR-0012): end-to-end queue mode — a real ``PrefillWorker``
(job-lifecycle side) and a real ``PrefillRunner`` (execution side)
cooperating through the shared ``jobs`` table, driving the same fake
SteamPrefill (``tests/stub_prefill``) every other worker/prefill test in this
suite already uses (``test_worker.py``, ``test_prefill_runner.py``).

Both run as real background THREADS against one on-disk sqlite file (this
project already runs vault-api's own worker as a thread; a second thread
standing in for the SEPARATE runner PROCESS is the same "two independent
writers, one file" situation the design doc calls for, minus the container
boundary — see this package's ADR/stop-report note on WAL-over-one-host being
process-count-agnostic).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests import stub_prefill
from vault_api import jobs
from vault_api.config import Settings
from vault_api.db import get_connection, init_db
from vault_api.main import create_app
from vault_api.prefill_runner import PrefillRunner
from vault_api.worker import PrefillWorker

TEST_API_KEY = "test-api-key-do-not-use-in-prod"


def _run_bounded(fn, *, timeout: float, label: str) -> None:
    """Run ``fn`` (a zero-arg callable) in a background thread with a hard
    wall-clock ceiling, so a broken fail-open direction in ``run_is_stale``
    (or anything else this test depends on eventually happening) FAILS this
    test loudly at ``timeout`` instead of hanging it — or, at full-suite
    scope, wedging the entire run with no summary line at all (WP S-1
    round-2 review S5, confirmed at both module and full-suite scope: a
    module-scoped run produced one unbounded hang; a full-suite run froze
    mid-progress-line with zero output until an external 300s timeout
    killed it). ``_wait_for_job``/``_wait_for_status`` are themselves
    already bounded polling loops that cannot hang on their own (they only
    ever read a DB row), but the background worker/runner THREADS they wait
    on are not otherwise capped from the test's own point of view — this
    wrapper is the outer, unconditional ceiling regardless of what the
    inner code does.

    Any exception ``fn`` raises (an ``AssertionError`` from a bounded helper
    timing out, for instance) is re-raised on the calling thread so pytest
    reports the real failure, not a generic "thread died" message.
    """
    error_box: list[BaseException] = []

    def wrapper() -> None:
        try:
            fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised below, not swallowed
            error_box.append(exc)

    thread = threading.Thread(target=wrapper, daemon=True)
    thread.start()
    thread.join(timeout=timeout)

    assert not thread.is_alive(), (
        f"{label} did not complete within {timeout}s -- this must fail "
        "loudly, not hang the test process (WP S-1 round-2 S5)."
    )
    if error_box:
        raise error_box[0]


def _queue_settings(tmp_path: Path, **overrides: object) -> Settings:
    base = dict(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
        prefill_mode="queue",
        worker_poll_seconds=0.05,
        runner_poll_seconds=0.05,
        runner_heartbeat_seconds=0.1,
        # WP S-1 round-2 (review S6): this project's stored timestamps are
        # second-precision (`jobs.TIMESTAMP_FORMAT`), so a live
        # `run_is_stale` check against one can OVER-report elapsed time by
        # almost a full second (the stored value is always FLOORED to the
        # second it was written in, never rounded). A lease this close to
        # that 1s floor produced 5/5 FALSE "presumed dead" verdicts against
        # a genuinely alive runner on this same host (reviewer measurement).
        # 8s against a 0.1s heartbeat is a ~80x margin, comfortably clear of
        # both the quantization noise and this host's observed antivirus/IO
        # slowness — see docs/adr/0012-*.md §4's "6x" note, corrected to
        # name this floor explicitly. Tests that specifically want a FAST,
        # genuinely-stale outcome override this with their own, still-safe
        # value (see test_queue_mode_no_runner_at_all_finalizes_as_a_stale_lease_error).
        runner_lease_timeout_seconds=8.0,
        prefill_timeout_seconds=30,
    )
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _wait_for_job(db_path: str, job_id: int, *, timeout: float = 10.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    conn = get_connection(db_path)
    try:
        while time.monotonic() < deadline:
            job = jobs.get_job(conn, job_id)
            assert job is not None
            if job["status"] in jobs.TERMINAL_STATUSES:
                return job
            time.sleep(0.02)
    finally:
        conn.close()
    raise AssertionError(f"job {job_id} did not reach a terminal status within {timeout}s")


def _wait_for_status(
    db_path: str, job_id: int, target_status: str, *, timeout: float = 10.0
) -> dict[str, object]:
    """Like ``_wait_for_job`` but for a specific (possibly non-terminal)
    status, e.g. ``'paused'``."""
    deadline = time.monotonic() + timeout
    conn = get_connection(db_path)
    try:
        while time.monotonic() < deadline:
            job = jobs.get_job(conn, job_id)
            assert job is not None
            if job["status"] == target_status:
                return job
            time.sleep(0.02)
    finally:
        conn.close()
    raise AssertionError(
        f"job {job_id} did not reach status {target_status!r} within {timeout}s"
    )


def test_queue_mode_end_to_end_success(tmp_path: Path) -> None:
    """The mutation bar's "queue mode: end-to-end fake-prefill flow through
    the queue" — a real worker hands a job off, a real runner claims,
    executes the stub, and reports back; the worker finalizes it exactly as
    it would in subprocess mode (depot mapping applied, apps.status done)."""
    bindir = tmp_path / "bin"
    cache_root = tmp_path / "cache"
    executable = stub_prefill.make_stub(
        bindir, cache_root=str(cache_root), depots_by_app={440: [441]}
    )
    settings = _queue_settings(tmp_path, steamprefill_path=executable, cache_root=str(cache_root))
    init_db(settings.db_path)

    worker = PrefillWorker(settings)
    runner = PrefillRunner(settings, runner_id="test-runner-1")
    worker.start()
    runner_thread = threading.Thread(target=runner.run_forever, daemon=True)
    runner_thread.start()

    try:
        conn = get_connection(settings.db_path)
        try:
            job, _created = jobs.enqueue_prefill(conn, 440)
            job_id = int(job["id"])
        finally:
            conn.close()

        finished = _wait_for_job(settings.db_path, job_id)
    finally:
        worker.stop(timeout=5)
        runner.stop()
        runner_thread.join(timeout=5)

    assert finished["status"] == jobs.STATUS_DONE, finished
    assert stub_prefill.read_selection(bindir) == [440]
    assert stub_prefill.read_argv(bindir) == ["prefill", "--force", "--no-ansi"]

    conn = get_connection(settings.db_path)
    try:
        app = conn.execute("SELECT status FROM apps WHERE appid = 440").fetchone()
        mapping = conn.execute(
            "SELECT depotid FROM depot_app_map WHERE appid = 440"
        ).fetchall()
        run_row = jobs.get_run_row(conn, job_id)
    finally:
        conn.close()

    assert app["status"] == jobs.STATUS_DONE
    assert {int(r["depotid"]) for r in mapping} == {441}
    assert run_row is not None
    assert run_row["run_claimed_by"] == "test-runner-1"
    assert run_row["run_completed_at"] is not None


def test_queue_mode_reattach_after_worker_restart(tmp_path: Path) -> None:
    """ADR-0012 §4's crash-semantics case: "worker dies while runner
    runs". A first PrefillWorker hands a job off; a real runner claims and
    starts a SLOW stub run. The first worker is stopped (simulating
    vault-api's own container restarting) WHILE the runner is still
    executing. A brand-new PrefillWorker (a fresh process's worker thread,
    for real — it has no in-memory knowledge of the first one) must find the
    job via ``find_active_run`` and resume waiting on it, finalizing it
    correctly once the still-running runner finishes."""
    bindir = tmp_path / "bin"
    cache_root = tmp_path / "cache"
    executable = stub_prefill.make_stub(
        bindir, cache_root=str(cache_root), depots_by_app={440: [441]}, sleep_seconds=1.0
    )
    settings = _queue_settings(tmp_path, steamprefill_path=executable, cache_root=str(cache_root))
    init_db(settings.db_path)

    worker_a = PrefillWorker(settings)
    runner = PrefillRunner(settings, runner_id="test-runner-reattach")
    worker_a.start()
    runner_thread = threading.Thread(target=runner.run_forever, daemon=True)
    runner_thread.start()

    conn = get_connection(settings.db_path)
    try:
        job, _created = jobs.enqueue_prefill(conn, 440)
        job_id = int(job["id"])
    finally:
        conn.close()

    # Give the runner time to claim it and start the (slow) stub, then kill
    # worker_a WHILE it is still genuinely mid-wait — the scenario this test
    # is named for, not a job that had already finished.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        check_conn = get_connection(settings.db_path)
        try:
            run_row = jobs.get_run_row(check_conn, job_id)
        finally:
            check_conn.close()
        if run_row is not None and run_row["run_claimed_by"] is not None:
            break
        time.sleep(0.02)
    else:
        raise AssertionError("runner never claimed the job")

    worker_a.stop(timeout=5)

    worker_b = PrefillWorker(settings)
    worker_b.start()
    try:
        finished = _wait_for_job(settings.db_path, job_id, timeout=10)
    finally:
        worker_b.stop(timeout=5)
        runner.stop()
        runner_thread.join(timeout=5)

    assert finished["status"] == jobs.STATUS_DONE, finished
    conn = get_connection(settings.db_path)
    try:
        mapping = conn.execute(
            "SELECT depotid FROM depot_app_map WHERE appid = 440"
        ).fetchall()
    finally:
        conn.close()
    assert {int(r["depotid"]) for r in mapping} == {441}


def test_queue_mode_pause_then_resume_genuinely_re_runs_the_stub(tmp_path: Path) -> None:
    """WP S-1 round-2, blocker B1: a resumed queue-mode job must actually
    re-invoke the runner, not silently replay a stale result left over from
    the run before the pause.

    Reproduces the reviewer's measured bug end to end and proves the fix:
    pause a genuinely running queue-mode job, delete the evidence of the
    FIRST invocation (``argv.json`` — written unconditionally at the top of
    the stub, before any mode branching, so its disappearance-then-return is
    unambiguous proof of a SECOND real invocation), resume, and require the
    file to come back — plus the job to actually reach 'done' this time
    (the bug parked it back at 'paused' forever, having never really run).
    """
    bindir = tmp_path / "bin"
    cache_root = tmp_path / "cache"
    # Long-hanging stub for the FIRST run so there is a comfortable window to
    # request the pause before it would ever finish on its own.
    executable = stub_prefill.make_stub(
        bindir, mode="hang", cache_root=str(cache_root),
        depots_by_app={440: [441]},
    )
    settings = _queue_settings(tmp_path, steamprefill_path=executable, cache_root=str(cache_root))
    init_db(settings.db_path)

    worker = PrefillWorker(settings)
    runner = PrefillRunner(settings, runner_id="test-runner-pause-resume")
    worker.start()
    runner_thread = threading.Thread(target=runner.run_forever, daemon=True)
    runner_thread.start()

    try:
        conn = get_connection(settings.db_path)
        try:
            job, _created = jobs.enqueue_prefill(conn, 440)
            job_id = int(job["id"])
        finally:
            conn.close()

        # Wait for the FIRST invocation to genuinely be under way -- claiming
        # the row alone only proves the runner picked it up, not that the
        # SteamPrefill stub subprocess has actually started (Popen + the
        # .cmd shim + interpreter startup all take real, if small, time on
        # Windows), so wait for argv.json itself, the concrete evidence this
        # test's later assertion depends on.
        argv_path = bindir / "argv.json"
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not argv_path.exists():
            time.sleep(0.02)
        assert argv_path.exists(), "the first invocation never wrote argv.json"

        pause_conn = get_connection(settings.db_path)
        try:
            result = jobs.request_pause(pause_conn, job_id)
            assert result.outcome == jobs.CONTROL_REQUESTED, result
        finally:
            pause_conn.close()

        _wait_for_status(settings.db_path, job_id, jobs.STATUS_PAUSED, timeout=10)

        # Proof of the FIRST invocation only, deleted so its reappearance
        # can only mean a genuinely NEW SteamPrefill invocation happened.
        argv_path.unlink()
        assert not argv_path.exists()

        run_row_after_pause = get_connection(settings.db_path)
        try:
            stale_run_row = jobs.get_run_row(run_row_after_pause, job_id)
        finally:
            run_row_after_pause.close()
        assert stale_run_row is not None
        stale_completed_at = stale_run_row["run_completed_at"]
        assert stale_completed_at is not None  # the runner did report 'paused'

        # Switch the stub to finish quickly on the resumed attempt instead
        # of hanging again for the rest of the test's patience.
        stub_prefill.set_mode(bindir, mode="success")

        resume_conn = get_connection(settings.db_path)
        try:
            resumed = jobs.resume_job(resume_conn, job_id)
            assert resumed.outcome == jobs.CONTROL_RESUMED, resumed
        finally:
            resume_conn.close()

        finished = _wait_for_job(settings.db_path, job_id, timeout=15)
    finally:
        worker.stop(timeout=5)
        runner.stop()
        runner_thread.join(timeout=5)

    assert finished["status"] == jobs.STATUS_DONE, finished
    assert argv_path.exists(), (
        "argv.json was never recreated -- the resumed job never actually "
        "re-invoked SteamPrefill (this is the exact shape of the B1 bug)"
    )
    assert stub_prefill.read_argv(bindir) == ["prefill", "--force", "--no-ansi"]

    conn = get_connection(settings.db_path)
    try:
        run_row = jobs.get_run_row(conn, job_id)
        mapping = conn.execute(
            "SELECT depotid FROM depot_app_map WHERE appid = 440"
        ).fetchall()
    finally:
        conn.close()
    assert run_row is not None
    assert run_row["run_completed_at"] != stale_completed_at or run_row["run_result_json"] != stale_run_row["run_result_json"], (
        "run_completed_at/run_result_json are byte-identical to the "
        "pre-resume (paused) attempt -- no new result was ever recorded"
    )
    assert {int(r["depotid"]) for r in mapping} == {441}


def test_queue_mode_no_runner_at_all_finalizes_as_a_stale_lease_error(tmp_path: Path) -> None:
    """The mutation bar's lease/heartbeat test at the worker-integration
    level: nothing ever claims the handed-off job (no ``prefill_runner``
    process is running), so ``run_is_stale``'s ``started_at`` fallback must
    fire once ``runner_lease_timeout_seconds`` elapses, and the WORKER (not a
    background reconciliation thread) must be the one to notice, live,
    while it is the one actively waiting.

    Wrapped in ``_run_bounded`` (WP S-1 round-2, S5): this test's whole
    point is "staleness eventually fires", so a broken (fail-open)
    ``run_is_stale`` must fail it loudly within a hard ceiling rather than
    hang — the exact class of mutation that, left unbounded, wedges the
    whole suite (see ``_run_bounded``'s own docstring).
    """
    box: dict[str, object] = {}

    def body() -> None:
        settings = _queue_settings(
            tmp_path,
            steamprefill_path=str(tmp_path / "bin" / "SteamPrefill.cmd"),  # never used
            # Round-2 (S6): well off the ~1s second-precision quantization
            # floor (see _queue_settings' own note) while still fast.
            runner_lease_timeout_seconds=3.0,
        )
        init_db(settings.db_path)

        worker = PrefillWorker(settings)
        worker.start()
        try:
            conn = get_connection(settings.db_path)
            try:
                job, _created = jobs.enqueue_prefill(conn, 440)
                job_id = int(job["id"])
            finally:
                conn.close()

            box["finished"] = _wait_for_job(settings.db_path, job_id, timeout=15)
            box["settings"] = settings
            box["job_id"] = job_id
        finally:
            worker.stop(timeout=5)

    _run_bounded(body, timeout=25, label="test_queue_mode_no_runner_at_all_finalizes_as_a_stale_lease_error")

    finished = box["finished"]
    settings = box["settings"]
    job_id = box["job_id"]
    assert finished["status"] == jobs.STATUS_ERROR, finished

    conn = get_connection(settings.db_path)
    try:
        full = jobs.get_job(conn, job_id)
        mapping = conn.execute(
            "SELECT depotid FROM depot_app_map WHERE appid = 440"
        ).fetchall()
        app = conn.execute("SELECT status FROM apps WHERE appid = 440").fetchone()
    finally:
        conn.close()

    assert full is not None
    assert "presumed dead" in full["log_excerpt"]
    assert mapping == []  # never any evidence for this app; mapping untouched
    assert app["status"] == jobs.STATUS_ERROR


def test_subprocess_mode_is_the_default_and_never_touches_the_run_columns(tmp_path: Path) -> None:
    """Byte-preservation sanity check at the integration level: a job run in
    the (default) subprocess mode must leave every queue-mode ``run_*``
    column NULL — proof that the queue-mode code path is not silently
    reached when ``VAULT_PREFILL_MODE`` is left unset."""
    bindir = tmp_path / "bin"
    cache_root = tmp_path / "cache"
    executable = stub_prefill.make_stub(
        bindir, cache_root=str(cache_root), depots_by_app={440: [441]}
    )
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(cache_root),
        log_level="INFO",
        steamprefill_path=executable,
        worker_poll_seconds=0.05,
    )
    assert settings.prefill_mode == "subprocess"
    init_db(settings.db_path)

    worker = PrefillWorker(settings)
    worker.start()
    try:
        conn = get_connection(settings.db_path)
        try:
            job, _created = jobs.enqueue_prefill(conn, 440)
            job_id = int(job["id"])
        finally:
            conn.close()

        finished = _wait_for_job(settings.db_path, job_id, timeout=10)
    finally:
        worker.stop(timeout=5)

    assert finished["status"] == jobs.STATUS_DONE, finished
    conn = get_connection(settings.db_path)
    try:
        run_row = jobs.get_run_row(conn, job_id)
    finally:
        conn.close()
    assert run_row is not None
    for column in (
        "run_use_force", "run_before_json", "run_claimed_by",
        "run_claimed_at", "run_heartbeat_at", "run_completed_at", "run_result_json",
    ):
        assert run_row[column] is None, column


def test_app_lifespan_wires_queue_mode_into_startup_recovery(tmp_path: Path) -> None:
    """Proves ``main.py``'s ``recover_stale_jobs(conn, queue_mode=...)`` call
    actually receives the setting, at the level a wiring bug would hide from
    every unit test above: a job left 'running' with a hand-off already
    recorded, before the app even starts.

    Distinguishing signal: the BLANKET pre-WP-S-1 recovery (what would still
    run if ``settings.prefill_mode_queue`` were not threaded through) stores
    ``jobs.STALE_JOB_MESSAGE`` ("was still marked 'running' when vault-api
    started") synchronously, during startup, before the worker thread has
    done anything. The correctly-wired queue-mode path leaves it alone at
    startup and instead lets ``PrefillWorker``'s live wait loop fail it
    later with ``prefill_queue.RUNNER_LOST_MESSAGE`` ("presumed dead") — a
    different message, arrived at a different way. Seeing the SECOND message
    is only possible if ``queue_mode=True`` actually reached
    ``recover_stale_jobs``.

    Wrapped in ``_run_bounded`` (WP S-1 round-2, S5) for the same reason as
    ``test_queue_mode_no_runner_at_all_finalizes_as_a_stale_lease_error``:
    this test's correctness depends on staleness eventually firing, so a
    broken ``run_is_stale`` must fail it within a hard ceiling, not hang the
    lifespan's shutdown (or the whole suite) indefinitely.
    """
    box: dict[str, object] = {}

    def body() -> None:
        settings = _queue_settings(
            tmp_path,
            steamprefill_path=str(tmp_path / "bin" / "SteamPrefill.cmd"),  # never used
            # Round-2 (S6): see _queue_settings' own note on the ~1s
            # second-precision quantization floor.
            runner_lease_timeout_seconds=3.0,
        )
        init_db(settings.db_path)

        # Simulate "a previous vault-api process claimed and handed off this
        # job, then the whole stack (including any runner) died" — written
        # directly, bypassing the API, so this is state the APP found at
        # startup rather than state it created itself.
        conn = get_connection(settings.db_path)
        try:
            job, _created = jobs.enqueue_prefill(conn, 440)
            job_id = int(job["id"])
            claimed = jobs.claim_next_job(conn)
            assert claimed is not None
            jobs.handoff_run(conn, job_id, True, "{}")
        finally:
            conn.close()

        app = create_app(settings)
        with TestClient(app):
            box["finished"] = _wait_for_job(settings.db_path, job_id, timeout=15)

    _run_bounded(body, timeout=25, label="test_app_lifespan_wires_queue_mode_into_startup_recovery")

    finished = box["finished"]
    assert finished["status"] == jobs.STATUS_ERROR, finished
    assert "presumed dead" in finished["log_excerpt"]
    assert jobs.STALE_JOB_MESSAGE not in finished["log_excerpt"]


def test_queue_mode_collects_a_run_that_finished_while_vault_api_was_down(tmp_path: Path) -> None:
    """WP API-FIX-1 (B1), end to end. The realistic restart shape:
    ``compose up -d`` recreates BOTH containers, the runner records its
    result (here the stub simply finishes; in production it is the SIGTERM
    handler's ``aborted``) while vault-api is not there to see it, and
    vault-api comes back to a row that is still 'running' but already
    complete. Before the fix ``find_active_run`` filtered that row out and
    the job stayed 'running' forever. Steps: worker A hands off; the runner
    claims; worker A is stopped mid-wait; the runner FINISHES with nobody
    listening; worker B starts and must finalize the job as 'done'."""
    bindir = tmp_path / "bin"
    cache_root = tmp_path / "cache"
    executable = stub_prefill.make_stub(
        bindir, cache_root=str(cache_root), depots_by_app={440: [441]}, sleep_seconds=2.0
    )
    settings = _queue_settings(tmp_path, steamprefill_path=executable, cache_root=str(cache_root))
    init_db(settings.db_path)

    worker_a = PrefillWorker(settings)
    runner = PrefillRunner(settings, runner_id="test-runner-b1")
    worker_a.start()
    runner_thread = threading.Thread(target=runner.run_forever, daemon=True)
    runner_thread.start()

    def _run_row() -> dict[str, object] | None:
        check_conn = get_connection(settings.db_path)
        try:
            return jobs.get_run_row(check_conn, job_id)
        finally:
            check_conn.close()

    try:
        conn = get_connection(settings.db_path)
        try:
            job, _created = jobs.enqueue_prefill(conn, 440)
            job_id = int(job["id"])
        finally:
            conn.close()

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            row = _run_row()
            if row is not None and row["run_claimed_by"] is not None:
                break
            time.sleep(0.02)
        else:
            raise AssertionError("runner never claimed the job")

        # vault-api goes away while the runner is mid-run ...
        worker_a.stop(timeout=5)

        # ... and the runner finishes with nobody listening.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            row = _run_row()
            if row is not None and row["run_completed_at"] is not None:
                break
            time.sleep(0.02)
        else:
            raise AssertionError("runner never recorded a result")

        # The defect's resting state, pinned: complete, yet still 'running',
        # and startup recovery correctly leaves it alone (the runner's row
        # is not its call). Only the reattach path can collect it.
        conn = get_connection(settings.db_path)
        try:
            parked = jobs.get_job(conn, job_id)
            assert parked is not None and parked["status"] == jobs.STATUS_RUNNING
            assert jobs.recover_stale_jobs(conn, queue_mode=True) == 0
            assert jobs.find_active_run(conn) is not None
        finally:
            conn.close()

        worker_b = PrefillWorker(settings)
        worker_b.start()
        try:
            finished = _wait_for_job(settings.db_path, job_id, timeout=10)
        finally:
            worker_b.stop(timeout=5)
    finally:
        runner.stop()
        runner_thread.join(timeout=5)

    assert finished["status"] == jobs.STATUS_DONE, finished
    conn = get_connection(settings.db_path)
    try:
        app = conn.execute("SELECT status FROM apps WHERE appid = 440").fetchone()
        mapping = conn.execute(
            "SELECT depotid FROM depot_app_map WHERE appid = 440"
        ).fetchall()
    finally:
        conn.close()
    assert app["status"] == jobs.STATUS_DONE
    assert {int(r["depotid"]) for r in mapping} == {441}
