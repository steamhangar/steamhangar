"""The queue-mode SteamPrefill runner (WP S-1, ADR-0012).

    python -m vault_api.prefill_runner

A slim, standalone process: it owns nothing but "poll the ``jobs`` table for
a handed-off prefill job, run SteamPrefill for it, report back" — the exact
piece of ``vault_api.worker.PrefillWorker`` that used to be a direct
``subprocess.Popen`` call inside vault-api itself. Job lifecycle (claiming
from 'queued', deciding ``--force``, applying the depot mapping, manifest
ingestion, webhooks, auto-GC) stays entirely in vault-api's worker — this
process never touches any of that, and imports nothing from ``worker.py``.

**Why this process needs broad network egress and vault-api should not
(the reason this split exists at all, EG-1's stop report — see
``docs/adr/0012-*.md``):** SteamPrefill talks to Steam's CM/CDN network
directly. Splitting it into its own process is what makes it possible for
EG-1 to lock vault-api's own container down to LAN-only egress without also
cutting off the one thing that legitimately needs the wider internet.

**The interactive login (ADR-0004 decision 1) now happens in THIS
container**, not vault-api's: SteamPrefill's ``Config/`` directory (the Steam
session) lives wherever this process's ``VAULT_STEAMPREFILL_PATH`` points,
so the one-time ``SteamPrefill select-apps`` login step is run via
``docker exec`` into the runner container once S-2 wires it up — see
api/README.md "Queue mode: the prefill_runner process" for the full
walkthrough. vault-api still never sees or stores Steam credentials; that
part of ADR-0004 is completely unaffected.

Started unconditionally by whatever launches it (a second command in
``compose.yaml``, S-2) — it is a genuine no-op, sleeping between polls, on an
install with nothing queued, and idles harmlessly if ``VAULT_PREFILL_MODE``
on the vault-api side is still ``subprocess`` (nothing ever gets handed off
for it to claim in that mode).
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import sqlite3
import threading
import time
import uuid

from vault_api import jobs, prefill, prefill_queue
from vault_api.config import Settings
from vault_api.db import get_connection

logger = logging.getLogger(__name__)

#: What an ``'aborted'`` result says in THIS process (WP API-FIX-1, P4):
#: ``run_prefill``'s ``should_abort`` here is the runner's own stop event
#: (SIGTERM from ``docker stop``/``compose up -d``), not vault-api's — the
#: default wording would send an operator to the wrong container's logs.
ABORT_REASON_RUNNER = "prefill_runner is shutting down"

#: How often ``_record_result`` tries to store a finished result before it
#: gives up (WP API-FIX-1, S2), and how long it sleeps between attempts.
#: Deliberately a plain ``time.sleep``, not ``self._stop.wait``: the most
#: common caller during shutdown is the SIGTERM ``aborted`` result, which
#: must still get its retries.
RESULT_WRITE_ATTEMPTS = 5
RESULT_WRITE_RETRY_SECONDS = 1.0


def make_runner_id() -> str:
    """A human-recognisable, effectively-unique id for this process instance.

    Purely for observability (``jobs.run_claimed_by``, logs) — nothing in the
    claim/heartbeat/result mechanics compares two runner ids against each
    other for correctness; ``jobs.claim_run``'s compare-and-swap is what
    actually guarantees exclusivity, this id just says who to blame in a log
    line. Hostname + pid identifies the container/process; the random suffix
    disambiguates two processes that crash-looped fast enough to reuse a pid.
    """
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


class PrefillRunner:
    """The claim -> execute -> report loop. See the module docstring."""

    def __init__(self, settings: Settings, runner_id: str | None = None) -> None:
        self._settings = settings
        self._runner_id = runner_id or make_runner_id()
        self._stop = threading.Event()

    @property
    def runner_id(self) -> str:
        return self._runner_id

    def stop(self) -> None:
        self._stop.set()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def run_forever(self) -> None:
        """The main loop. Blocks until :meth:`stop` is called (typically from
        a signal handler — see :func:`main`)."""
        conn = get_connection(self._settings.db_path)
        logger.info(
            "prefill_runner %s starting (poll every %.1fs, heartbeat every "
            "%.1fs, SteamPrefill path %r, --max-threads %d).",
            self._runner_id,
            self._settings.runner_poll_seconds,
            self._settings.runner_heartbeat_seconds,
            self._settings.steamprefill_path,
            self._settings.prefill_max_threads,
        )
        try:
            while not self._stop.is_set():
                try:
                    job = jobs.claim_run(conn, self._runner_id)
                except sqlite3.Error:
                    # Same net vault-api's worker has around claim_next_job
                    # (WP API-FIX-1, P2): a locked/failed claim must not
                    # kill the runner; the job stays handed-off and the next
                    # poll retries.
                    logger.exception("Failed to claim a run; retrying after a poll")
                    self._stop.wait(self._settings.runner_poll_seconds)
                    continue
                if job is None:
                    self._stop.wait(self._settings.runner_poll_seconds)
                    continue
                self._execute(conn, job)
        finally:
            conn.close()
            logger.info("prefill_runner %s stopped.", self._runner_id)

    def _execute(self, conn, job: dict[str, object]) -> None:
        job_id = int(job["id"])  # type: ignore[arg-type]
        appid = int(job["appid"])  # type: ignore[arg-type]
        use_force = bool(job["run_use_force"])
        logger.info(
            "prefill_runner %s claimed job %s (appid %s, use_force=%s).",
            self._runner_id, job_id, appid, use_force,
        )

        last_heartbeat = time.monotonic()

        def stop_request_with_heartbeat() -> str | None:
            """Piggyback the heartbeat on ``run_prefill``'s existing 0.2s
            subprocess poll tick (see ``prefill.py``'s ``_wait_for_process``)
            instead of adding a second polling loop. Throttled to
            ``runner_heartbeat_seconds`` — every 0.2s tick would otherwise
            write to the shared database several times a second for no
            benefit (``jobs.run_is_stale``'s margin is measured in whole
            heartbeat intervals, not sub-second ticks).

            **A database error here is logged and read as "no stop request"
            (WP API-FIX-1, P2).** This callback runs INSIDE
            ``prefill.run_prefill``'s wait loop, with SteamPrefill live
            underneath it; letting a ``sqlite3.Error`` (a busy-timeout
            overrun, a transient I/O error on the shared volume) escape
            would abandon a download over a bookkeeping hiccup. A missed
            heartbeat costs one interval of lease margin (the runner retries
            at the next one, not on the next 0.2s tick); a missed
            stop-request read is answered on the next tick. If the database
            stays broken past the lease, vault-api fails the job as
            ``runner_lost`` — the same outcome as a dead runner, which from
            vault-api's side is exactly what this looks like.
            """
            nonlocal last_heartbeat
            now = time.monotonic()
            if now - last_heartbeat >= self._settings.runner_heartbeat_seconds:
                last_heartbeat = now
                try:
                    jobs.record_run_heartbeat(conn, job_id, self._runner_id)
                except sqlite3.Error:
                    logger.exception(
                        "prefill_runner %s: heartbeat for job %s failed; "
                        "retrying at the next interval.",
                        self._runner_id, job_id,
                    )
            try:
                return jobs.read_stop_request(conn, job_id)
            except sqlite3.Error:
                logger.exception(
                    "prefill_runner %s: could not read job %s's stop request; "
                    "treating it as none until the next tick.",
                    self._runner_id, job_id,
                )
                return None

        result = prefill.run_prefill(
            appid=appid,
            steamprefill_path=self._settings.steamprefill_path,
            timeout_seconds=self._settings.prefill_timeout_seconds,
            should_abort=self._stop.is_set,
            use_force=use_force,
            stop_request=stop_request_with_heartbeat,
            abort_reason=ABORT_REASON_RUNNER,
            max_threads=self._settings.prefill_max_threads,
        )

        applied = self._record_result(conn, job_id, prefill_queue.encode_result(result))
        if applied is None:
            return
        if applied:
            # WP API-FIX-3: this is the PROCESS outcome, not the job's final
            # state. vault-api's worker decides that from the result and can
            # still end a clean exit as 'error' (SteamPrefill's summary
            # reports the app as failed or not considered). A bare
            # "success=True" here once sat next to a job the UI showed as
            # failed, so the line says what it is.
            logger.info(
                "prefill_runner %s: job %s (appid %s) SteamPrefill run "
                "finished, run_success=%s failure_reason=%r exit_code=%s; "
                "result handed to vault-api, which sets the job's final state "
                "from SteamPrefill's summary table.",
                self._runner_id, job_id, appid, result.success,
                result.failure_reason, result.exit_code,
            )
        else:
            # vault-api already declared this job's lease dead (staleness,
            # ADR-0012 §4) and failed it while we were still running
            # SteamPrefill — see jobs.record_run_result's docstring. The
            # bytes we wrote to the cache are still on disk; there is nothing
            # left for us to do with this outcome.
            logger.warning(
                "prefill_runner %s: job %s (appid %s) finished, but vault-api "
                "had already declared it dead (result discarded; the run's "
                "output is not lost, only this row's bookkeeping is).",
                self._runner_id, job_id, appid,
            )

    def _record_result(
        self, conn: sqlite3.Connection, job_id: int, result_json: str
    ) -> bool | None:
        """``jobs.record_run_result`` with a retry net (WP API-FIX-1, S2).

        Returns what ``record_run_result`` returned, or ``None`` if every
        attempt raised ``sqlite3.Error``. A transient lock or I/O error on
        the shared volume right after SteamPrefill exits must neither kill
        the runner loop nor silently drop a finished download's outcome, so
        it is retried a few times. If the database stays broken, the error is
        logged loudly and the runner moves on: the job's lease then expires
        (no more heartbeats) and vault-api fails it as ``runner_lost`` — the
        bytes SteamPrefill wrote stay in the cache either way.

        With a locked database every attempt can block for the full
        ``busy_timeout`` (5 s, ``db.get_connection``) before it raises, so the
        retries (up to ``RESULT_WRITE_ATTEMPTS`` x 5 s plus the sleeps
        between them) can outlast the container's 20 s ``stop_grace_period``
        during a shutdown. The resulting SIGKILL has the same outcome as
        giving up here: no result is stored, the lease expires and the job
        is failed as ``runner_lost``.
        """
        for attempt in range(1, RESULT_WRITE_ATTEMPTS + 1):
            try:
                return jobs.record_run_result(conn, job_id, self._runner_id, result_json)
            except sqlite3.Error:
                logger.exception(
                    "prefill_runner %s: storing job %s's result failed "
                    "(attempt %s/%s).",
                    self._runner_id, job_id, attempt, RESULT_WRITE_ATTEMPTS,
                )
                if attempt < RESULT_WRITE_ATTEMPTS:
                    time.sleep(RESULT_WRITE_RETRY_SECONDS)
        logger.error(
            "prefill_runner %s: GAVE UP storing job %s's result after %s "
            "attempts; the result is lost and vault-api will fail the job as "
            "runner_lost once its lease expires. The downloaded bytes stay "
            "in the cache.",
            self._runner_id, job_id, RESULT_WRITE_ATTEMPTS,
        )
        return None


def main() -> None:
    # require_api_key=False (WP S-1 round-2 review, S2; ADR-0012 §2/§5
    # addendum): this process never serves HTTP and never authenticates
    # anything, so it has no legitimate use for the LAN control-plane
    # secret vault-api itself requires -- see Settings.from_env's own
    # docstring for the full argument. VAULT_API_KEY does not need to be
    # injected into the one container this split exists to isolate.
    settings = Settings.from_env(require_api_key=False)
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))

    runner = PrefillRunner(settings)

    def _handle_signal(signum: int, _frame: object) -> None:
        logger.info("prefill_runner received signal %s; shutting down.", signum)
        runner.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    try:
        signal.signal(signal.SIGTERM, _handle_signal)
    except (AttributeError, ValueError):  # pragma: no cover - SIGTERM is POSIX-only
        pass

    runner.run_forever()


if __name__ == "__main__":
    main()
