"""WP RUNNER-RETRY-1: retry a prefill when SteamPrefill reports "Unable to
detect Lancache server!" (its /lancache-heartbeat probe to the cache timed
out).

Pinned guarantees, each by name:

G1  Only that failure is retried: ``exit_code`` + the exact phrase. Other
    exit codes, not-logged-in, timeouts, the public-IP variant of the same
    exception and successful runs end after one attempt.
G2  Success on attempt 2 is a success; each retry is logged in the job log
    ("[vault-api] Lancache not detected, retry 2 of 3 in <n> s.").
G3  All attempts fail -> the existing failure: reason ``exit_code``, the
    worker's ``Prefill failed (reason=...)`` line stays LAST (the line the
    web reads), plus a "giving up" line.
G4  A cancel, pause or shutdown during the pause ends the job as
    cancelled/paused/aborted and starts no further attempt; the wait is cut
    short, not slept out.
G5  Side effects run once, from the final attempt only: depot mapping,
    manifest ingestion, summary parsing and auto-GC.
G6  Queue mode retries in the runner, and the runner's job heartbeat keeps
    the lease alive during the pause.

The failure is simulated with the stub SteamPrefill (``lancache_fail_runs``)
or a fake ``run_prefill``; no network, no real cache.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests import stub_prefill
from tests.conftest import TEST_API_KEY
from vault_api import jobs, prefill
from vault_api.config import Settings
from vault_api.db import get_connection, init_db
from vault_api.main import create_app
from vault_api.prefill_runner import PrefillRunner
from vault_api.worker import PrefillWorker

AUTH = {"X-Api-Key": TEST_API_KEY}

PHRASE = "Unable to detect Lancache server!"
HEARTBEAT_FAILURE_OUTPUT = (
    "[stub] Detecting Lancache server...\n"
    f"LancacheNotFoundException: {PHRASE}\n"
)

TABLE_UPDATED = (
    "  Prefilled 1 apps totaling 12 MiB in 05.0000 \n"
    "   Updated | Up To Date\n"
    "  ---------+------------\n"
    "      1    |     0\n"
)

#: The 0/0 "not considered" shape. Planted into a FAILED earlier attempt's
#: output to prove only the final attempt's table decides the outcome.
TABLE_NOT_CONSIDERED = (
    "  Prefilled 0 apps totaling 0 b in 03.2491\n"
    "   Updated | Up To Date\n"
    "  ---------+------------\n"
    "      0    |     0\n"
)

#: The web's reason regex (web/js/lib/job-failure.js REASON_LINE) as a literal.
WEB_REASON_LINE = re.compile(r"^\[vault-api\] Prefill failed \(reason=([a-z_]+)\)")

SHORT_PAUSE = 0.05


def _last_line(text: str) -> str:
    return [line for line in text.splitlines() if line.strip()][-1]


def _result(reason: str | None, output: str, *, success: bool = False) -> prefill.PrefillResult:
    exit_code = 0 if success else (1 if reason == "exit_code" else None)
    return prefill.PrefillResult(success, reason, exit_code, output)


@pytest.fixture
def short_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    """Production pause is 15 s; the worker/runner read it at call time."""
    monkeypatch.setattr(prefill, "LANCACHE_RETRY_PAUSE_SECONDS", SHORT_PAUSE)


def _make_settings(tmp_path: Path, executable: str, **overrides: object) -> Settings:
    base: dict[str, object] = dict(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
        steamprefill_path=executable,
        prefill_timeout_seconds=60,
        worker_poll_seconds=0.02,
        steamprefill_cache_dir=str(tmp_path / "unused-steamprefill-cache"),
        manifest_archive_dir=str(tmp_path / "manifest-archive"),
        auto_gc="off",
    )
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _enqueue(client: TestClient, appid: int = 440) -> int:
    response = client.post("/v1/prefill", json={"appids": [appid]}, headers=AUTH)
    assert response.status_code == 202, response.text
    return int(response.json()[0]["job_id"])


def _wait_status(client: TestClient, job_id: int, wanted: tuple[str, ...], timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    job: dict = {}
    while time.monotonic() < deadline:
        job = client.get(f"/v1/jobs/{job_id}", headers=AUTH).json()
        if job["status"] in wanted:
            return job
        time.sleep(0.05)
    pytest.fail(f"job {job_id} never reached {wanted}: {job}")


# -- G1: what is retried ---------------------------------------------------------


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (_result("exit_code", HEARTBEAT_FAILURE_OUTPUT), True),
        (_result("exit_code", "Depot 441 failed: disk full"), False),
        # Same exception type, a configuration error a retry cannot fix.
        (_result("exit_code", "LancacheNotFoundException: Lancache server is resolving to a public IP : 1.2.3.4"), False),
        # Narrow, case-sensitive phrase.
        (_result("exit_code", "unable to detect lancache server!"), False),
        (_result("not_logged_in", HEARTBEAT_FAILURE_OUTPUT), False),
        (_result("timeout", HEARTBEAT_FAILURE_OUTPUT), False),
        (_result("cancelled", HEARTBEAT_FAILURE_OUTPUT), False),
        (_result("aborted", HEARTBEAT_FAILURE_OUTPUT), False),
        (_result("setup", HEARTBEAT_FAILURE_OUTPUT), False),
        (_result(None, HEARTBEAT_FAILURE_OUTPUT, success=True), False),
    ],
)
def test_only_the_heartbeat_failure_is_retryable(result: prefill.PrefillResult, expected: bool) -> None:
    assert prefill.lancache_not_detected(result) is expected


def test_retry_constants_match_the_decision() -> None:
    """3 attempts in total, a pause inside the 10-30 s band (operator
    decision 2026-10-18)."""
    assert prefill.LANCACHE_RETRY_ATTEMPTS == 3
    assert 10 <= prefill.LANCACHE_RETRY_PAUSE_SECONDS <= 30


def _fake_runs(monkeypatch: pytest.MonkeyPatch, results: list[prefill.PrefillResult]) -> list[dict]:
    calls: list[dict] = []
    queue = list(results)

    def fake_run_prefill(**kwargs: object) -> prefill.PrefillResult:
        calls.append(kwargs)
        return queue.pop(0)

    monkeypatch.setattr(prefill, "run_prefill", fake_run_prefill)
    return calls


@pytest.mark.parametrize(
    "first",
    [
        _result("exit_code", "Depot 441 failed: disk full"),
        _result("not_logged_in", stub_prefill.NOT_LOGGED_IN_OUTPUT),
        _result("timeout", HEARTBEAT_FAILURE_OUTPUT),
        _result("exit_code", "LancacheNotFoundException: Lancache server is resolving to a public IP : 1.2.3.4"),
        _result(None, TABLE_UPDATED, success=True),
    ],
)
def test_other_outcomes_run_once_and_are_returned_unchanged(
    monkeypatch: pytest.MonkeyPatch, first: prefill.PrefillResult
) -> None:
    calls = _fake_runs(monkeypatch, [first, _result(None, TABLE_UPDATED, success=True)])
    result = prefill.run_prefill_with_retry(440, "unused", 30, pause_seconds=0)
    assert len(calls) == 1
    assert result == first


def test_other_exit_code_with_the_real_stub_is_not_retried(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    executable = stub_prefill.make_stub(bindir, mode="fail")
    result = prefill.run_prefill_with_retry(440, executable, 30, pause_seconds=SHORT_PAUSE)
    assert (result.success, result.failure_reason) == (False, "exit_code")
    assert len(stub_prefill.read_runs(bindir)) == 1
    assert "Lancache not detected" not in result.output


# -- G2/G3: retry and give up, through the real stub pipe --------------------------


def test_success_on_attempt_two_with_the_real_stub(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    executable = stub_prefill.make_stub(bindir, lancache_fail_runs=1)
    result = prefill.run_prefill_with_retry(440, executable, 30, pause_seconds=SHORT_PAUSE)

    assert (result.success, result.failure_reason, result.exit_code) == (True, None, 0)
    assert len(stub_prefill.read_runs(bindir)) == 2
    assert "[vault-api] Lancache not detected, retry 2 of 3 in 0.05 s." in result.output
    assert "retry 3 of 3" not in result.output
    # Both attempts stay visible, in order.
    assert result.output.index(PHRASE) < result.output.index("Prefill complete!")
    # The final attempt's part holds only that attempt.
    final = prefill.final_attempt_output(result.output)
    assert PHRASE not in final and "Prefill complete!" in final


def test_all_attempts_fail_with_the_real_stub(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    executable = stub_prefill.make_stub(bindir, lancache_fail_runs=5)
    result = prefill.run_prefill_with_retry(440, executable, 30, pause_seconds=SHORT_PAUSE)

    assert (result.success, result.failure_reason, result.exit_code) == (False, "exit_code", 1)
    assert len(stub_prefill.read_runs(bindir)) == 3
    assert result.output.count(PHRASE) == 3
    assert "retry 2 of 3 in 0.05 s." in result.output
    assert "retry 3 of 3 in 0.05 s." in result.output
    assert "retry 4 of" not in result.output
    assert _last_line(result.output).startswith(
        "[vault-api] Lancache not detected in any of 3 attempts; giving up."
    )


def test_single_attempt_setting_adds_no_retry_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_runs(monkeypatch, [_result("exit_code", HEARTBEAT_FAILURE_OUTPUT)])
    result = prefill.run_prefill_with_retry(440, "unused", 30, attempts=1, pause_seconds=0)
    assert len(calls) == 1
    assert result.output == HEARTBEAT_FAILURE_OUTPUT


def test_every_attempt_gets_the_same_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_runs(
        monkeypatch,
        [_result("exit_code", HEARTBEAT_FAILURE_OUTPUT), _result(None, TABLE_UPDATED, success=True)],
    )
    prefill.run_prefill_with_retry(
        440, "x", 77, use_force=False, max_threads=5, abort_reason="r", pause_seconds=0
    )
    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert calls[0]["timeout_seconds"] == 77 and calls[0]["use_force"] is False
    assert calls[0]["max_threads"] == 5 and calls[0]["abort_reason"] == "r"


def test_final_attempt_output_splits_on_the_last_retry_line() -> None:
    assert prefill.final_attempt_output("plain run\n") == "plain run\n"
    text = (
        "a1\n[vault-api] Lancache not detected, retry 2 of 3 in 15 s.\n"
        "a2\n[vault-api] Lancache not detected, retry 3 of 3 in 15 s.\nfinal\n"
    )
    assert prefill.final_attempt_output(text) == "final\n"
    # Not at a line start: not a retry line.
    assert prefill.final_attempt_output("x [vault-api] Lancache not detected, retry 2 of 3 in 1 s.\n") == (
        "x [vault-api] Lancache not detected, retry 2 of 3 in 1 s.\n"
    )


# -- G4: stop during the pause ---------------------------------------------------


@pytest.mark.parametrize(
    ("request_value", "abort", "expected", "note"),
    [
        ("cancel", False, "cancelled", "[vault-api] Cancelled on request before retry 2"),
        ("pause", False, "paused", "[vault-api] Paused on request before retry 2"),
        (None, True, "aborted", "[vault-api] Aborted: test shutdown."),
    ],
)
def test_a_stop_during_the_pause_starts_no_further_attempt(
    monkeypatch: pytest.MonkeyPatch,
    request_value: str | None,
    abort: bool,
    expected: str,
    note: str,
) -> None:
    calls = _fake_runs(
        monkeypatch,
        [_result("exit_code", HEARTBEAT_FAILURE_OUTPUT), _result(None, TABLE_UPDATED, success=True)],
    )
    polls = {"n": 0}

    def stop_request() -> str | None:
        # The request arrives a few ticks INTO the pause, not before it.
        polls["n"] += 1
        return request_value if polls["n"] >= 3 else None

    def should_abort() -> bool:
        return abort and polls["n"] >= 3

    started = time.monotonic()
    result = prefill.run_prefill_with_retry(
        440, "unused", 30,
        should_abort=should_abort, stop_request=stop_request,
        abort_reason="test shutdown", pause_seconds=60,
    )
    elapsed = time.monotonic() - started

    assert len(calls) == 1, "no attempt may start after a stop"
    assert (result.success, result.failure_reason, result.exit_code) == (False, expected, None)
    assert elapsed < 10, f"the 60 s pause was slept out ({elapsed:.1f}s)"
    assert "retry 2 of 3 in 60 s." in result.output
    assert _last_line(result.output).startswith(note)
    assert polls["n"] >= 3


def test_shutdown_wins_over_an_operator_request_in_the_same_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_runs(monkeypatch, [_result("exit_code", HEARTBEAT_FAILURE_OUTPUT)])
    result = prefill.run_prefill_with_retry(
        440, "unused", 30,
        should_abort=lambda: True, stop_request=lambda: "pause", pause_seconds=60,
    )
    assert result.failure_reason == "aborted"


# -- G4/G5 end to end, subprocess mode (real worker, real stub) --------------------


def _gc_jobs(db_path: str, appid: int = 440) -> list:
    conn = get_connection(db_path)
    try:
        return conn.execute(
            "SELECT id FROM jobs WHERE appid = ? AND type = ?", (appid, jobs.JOB_TYPE_GC)
        ).fetchall()
    finally:
        conn.close()


def _mapped(db_path: str, appid: int = 440) -> list[int]:
    conn = get_connection(db_path)
    try:
        return sorted(
            int(r[0]) for r in conn.execute(
                "SELECT depotid FROM depot_app_map WHERE appid = ?", (appid,)
            ).fetchall()
        )
    finally:
        conn.close()


def test_worker_success_on_attempt_two_applies_side_effects_once(
    tmp_path: Path, short_pause: None
) -> None:
    from tests.test_manifests import _bin_manifest_bytes, _chunk_id

    bindir = tmp_path / "bin"
    cache_root = tmp_path / "cache"
    temp_cache = tmp_path / "steamprefill-cache"
    executable = stub_prefill.make_stub(
        bindir,
        cache_root=str(cache_root),
        depots_by_app={440: [441]},
        summary_text=TABLE_UPDATED,
        lancache_fail_runs=1,
        manifest_bins=[{
            "dir": str(temp_cache),
            "filename": "440_440_441_555.bin",
            "data": _bin_manifest_bytes(depot_id=441, manifest_id=555, files=[[(_chunk_id(1), 1000)]]),
        }],
    )
    settings = _make_settings(
        tmp_path, executable, auto_gc="execute", steamprefill_cache_dir=str(temp_cache)
    )
    with TestClient(create_app(settings)) as client:
        job_id = _enqueue(client)
        job = _wait_status(client, job_id, ("done", "error", "cancelled", "not_owned"))

    excerpt = job["log_excerpt"]
    assert job["status"] == "done", excerpt
    assert len(stub_prefill.read_runs(bindir)) == 2
    assert "[vault-api] Lancache not detected, retry 2 of 3 in 0.05 s." in excerpt
    assert excerpt.count("[vault-api] Prefill summary:") == 1
    assert excerpt.count("[vault-api] Depot mapping updated") == 1
    assert excerpt.count("[vault-api] Manifest ingestion:") == 1
    assert excerpt.count("[vault-api] Auto-GC (VAULT_AUTO_GC=execute)") == 1
    assert (job["updated"], job["up_to_date"], job["summary_parse_ok"]) == (1, 0, True)
    assert _mapped(settings.db_path) == [441]
    assert len(_gc_jobs(settings.db_path)) == 1
    # One chunk file: only the successful attempt wrote to the cache.
    assert len(list((cache_root / "depot" / "441" / "chunk").iterdir())) == 1


def test_worker_reads_the_summary_from_the_final_attempt_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, short_pause: None
) -> None:
    """A failed earlier attempt whose output holds a 0/0 table must not make
    the job 'not_owned': parse_summary takes the FIRST table it finds."""
    first = _result("exit_code", TABLE_NOT_CONSIDERED + HEARTBEAT_FAILURE_OUTPUT)
    _fake_runs(monkeypatch, [first, _result(None, TABLE_UPDATED, success=True)])
    settings = _make_settings(tmp_path, "unused")
    with TestClient(create_app(settings)) as client:
        job_id = _enqueue(client)
        job = _wait_status(client, job_id, ("done", "error", "cancelled", "not_owned"))
    assert job["status"] == "done", job["log_excerpt"]
    assert (job["updated"], job["up_to_date"]) == (1, 0)


def test_worker_all_attempts_fail_with_the_existing_error(
    tmp_path: Path, short_pause: None
) -> None:
    bindir = tmp_path / "bin"
    cache_root = tmp_path / "cache"
    executable = stub_prefill.make_stub(
        bindir, cache_root=str(cache_root), depots_by_app={440: [441]},
        summary_text=TABLE_UPDATED, lancache_fail_runs=99,
    )
    settings = _make_settings(tmp_path, executable, auto_gc="execute")
    with TestClient(create_app(settings)) as client:
        job_id = _enqueue(client)
        job = _wait_status(client, job_id, ("done", "error", "cancelled", "not_owned"))
        game = client.get("/v1/games/440", headers=AUTH).json()

    excerpt = job["log_excerpt"]
    assert job["status"] == "error", excerpt
    assert len(stub_prefill.read_runs(bindir)) == 3
    last = _last_line(excerpt)
    match = WEB_REASON_LINE.match(last)
    assert match is not None and match.group(1) == "exit_code", last
    assert "[vault-api] Lancache not detected in any of 3 attempts; giving up." in excerpt
    assert "retry 3 of 3" in excerpt
    assert game["status"] == "error"
    assert _mapped(settings.db_path) == []
    assert _gc_jobs(settings.db_path) == []


@pytest.mark.parametrize(("action", "expected"), [("cancel", "cancelled"), ("pause", "paused")])
def test_worker_stop_during_the_pause_ends_the_job_without_another_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    action: str,
    expected: str,
) -> None:
    # A long pause: the test fails by timeout if the stop is not honoured.
    monkeypatch.setattr(prefill, "LANCACHE_RETRY_PAUSE_SECONDS", 60.0)
    caplog.set_level(logging.WARNING, logger="vault_api.prefill")
    bindir = tmp_path / "bin"
    executable = stub_prefill.make_stub(bindir, lancache_fail_runs=99)
    settings = _make_settings(tmp_path, executable)
    with TestClient(create_app(settings)) as client:
        job_id = _enqueue(client)
        # The warning is logged right before the pause starts, so the request
        # below lands inside the pause, not during attempt 1.
        deadline = time.monotonic() + 20
        while not any("retry 2 of 3" in r.getMessage() for r in caplog.records):
            assert time.monotonic() < deadline, "attempt 1 never reached the pause"
            time.sleep(0.02)
        if action == "cancel":
            response = client.delete(f"/v1/jobs/{job_id}", headers=AUTH)
        else:
            response = client.post(f"/v1/jobs/{job_id}/pause", headers=AUTH)
        assert response.status_code in (200, 202), response.text
        started = time.monotonic()
        job = _wait_status(client, job_id, (expected, "done", "error"), timeout=15)
        elapsed = time.monotonic() - started

    assert job["status"] == expected, job["log_excerpt"]
    assert elapsed < 10
    assert len(stub_prefill.read_runs(bindir)) == 1
    word = "Cancelled" if action == "cancel" else "Paused"
    assert f"[vault-api] {word} on request before retry 2" in job["log_excerpt"]


# -- G6: queue mode -----------------------------------------------------------------


def test_queue_mode_retries_in_the_runner_and_keeps_the_lease_alive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pause (3 s) is longer than the lease (2.5 s): without the runner's
    heartbeat during the pause, vault-api would fail the job as runner_lost."""
    from tests.test_prefill_runner_process import _queue_settings, _wait_for_job

    monkeypatch.setattr(prefill, "LANCACHE_RETRY_PAUSE_SECONDS", 3.0)
    bindir = tmp_path / "bin"
    cache_root = tmp_path / "cache"
    executable = stub_prefill.make_stub(
        bindir, cache_root=str(cache_root), depots_by_app={440: [441]},
        summary_text=TABLE_UPDATED, lancache_fail_runs=1,
    )
    settings = _queue_settings(
        tmp_path, steamprefill_path=executable, cache_root=str(cache_root),
        runner_lease_timeout_seconds=2.5,
    )
    init_db(settings.db_path)

    worker = PrefillWorker(settings)
    runner = PrefillRunner(settings, runner_id="test-runner-retry")
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
        finished = _wait_for_job(settings.db_path, job_id, timeout=20)
    finally:
        worker.stop(timeout=5)
        runner.stop()
        runner_thread.join(timeout=5)

    excerpt = str(finished["log_excerpt"])
    assert finished["status"] == jobs.STATUS_DONE, excerpt
    assert "[vault-api] Lancache not detected, retry 2 of 3 in 3 s." in excerpt
    assert excerpt.count("[vault-api] Depot mapping updated") == 1
    assert len(stub_prefill.read_runs(bindir)) == 2
    assert _mapped(settings.db_path) == [441]
