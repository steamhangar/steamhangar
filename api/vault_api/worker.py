"""The single background job worker (plan §3: "one job at a time").

One ``threading.Thread`` started by the FastAPI lifespan, polling the ``jobs``
table. No Celery, no APScheduler, no second process — plan §9's simplicity
stance, and the queue only ever executes one job at a time anyway.

Two job types share that queue: ``prefill`` (WP 1.4, the body of this file) and
``gc`` (WP 3.8, executed by ``vault_api.gc_execute``). Sharing is the point —
one worker means a GC job can never unlink chunks out of a depot SteamPrefill
is downloading into.

Lifecycle
---------
startup  ->  ``jobs.recover_stale_jobs`` (fail orphaned 'running' rows from a
             process that died mid-job — see its docstring for the rule)
         ->  thread starts, loops: claim -> execute -> repeat; sleeps
             ``VAULT_WORKER_POLL_SECONDS`` when the queue is empty
shutdown ->  stop event set; the loop exits before claiming another job, and a
             prefill subprocess currently in flight is terminated (otherwise
             ``docker stop`` would block for as long as the download takes).
             The aborted job is recorded as 'error' with a clear reason.

Job control (WP 3.12)
---------------------
``DELETE /v1/jobs/{id}`` and ``POST /v1/jobs/{id}/pause`` do not talk to this
thread directly. They write ``jobs.stop_request`` (schema v8) and this worker
polls it — on the subprocess wait tick for a prefill, between depots for a GC
run. What each request turns into once honored:

- **cancel**, prefill: SteamPrefill is terminated, the job ends ``cancelled``
  (a real terminal status, not ``error``), ``apps.status`` goes back to
  ``idle``, and neither the depot mapping nor manifest ingestion is touched.
- **cancel**, GC: cooperative between depots; the depot currently being
  executed finishes (that is documented, not accidental — a mid-depot abort
  would be a *worse* state than a completed one, and one depot is bounded work).
- **pause**, prefill only: identical termination, but the job is parked at
  ``paused`` instead of finalized, and ``resume`` puts it back in the queue.
  There is no pause signal in SteamPrefill; pause IS terminate and resume IS
  re-run, which is affordable only because already-cached chunks replay as
  local HITs (ADR-0001) — the cache is the progress store.
- **either, too late**: a run that finished on its own before the request was
  noticed keeps its real outcome; the log says the request arrived too late.

Queue mode (WP S-1, ADR-0012)
------------------------------
This thread ALWAYS owns prefill job lifecycle (claim, ``--force`` decision,
mapping, manifest ingestion, webhooks, auto-GC). What changes with
``VAULT_PREFILL_MODE=queue`` is only how the ``PrefillResult`` for step 2 of
the lifecycle above is obtained: instead of calling ``prefill.run_prefill``
directly (the default ``subprocess`` mode, unchanged), it hands the job off
through the ``jobs`` table (``jobs.handoff_run``) to a separate
``prefill_runner`` process and waits (``prefill_queue.await_run_result``).
Everything downstream of getting a result — ``_finalize_prefill_result`` — is
identical either way. See ``docs/adr/0012-*.md`` for the crash semantics
(a dead runner fails the job cleanly; a vault-api restart resumes waiting via
``jobs.find_active_run`` rather than re-doing the blanket startup recovery
above, which would otherwise wrongly treat a still-running SEPARATE process
as an orphan) and ``api/README.md``'s "Queue mode" section for the operator
view.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import traceback

from vault_api import (
    gc_execute,
    jobs,
    manifest_ingest,
    prefill,
    prefill_queue,
    prefill_summary,
    settings_store,
    webhooks,
)
from vault_api.config import PREFILL_MODE_QUEUE, Settings
from vault_api.db import get_connection
from vault_api.sizes import DepotSignature, SizeCache
from vault_api.webhooks import WebhookNotifier

logger = logging.getLogger(__name__)

#: How long ``stop()`` waits for the thread to wind down. The abort path
#: terminates the subprocess, so this only needs to cover that plus the
#: terminate->kill grace period.
SHUTDOWN_JOIN_TIMEOUT_SECONDS = 30.0


class PrefillWorker:
    """Runs queued prefill jobs, strictly one at a time, in FIFO order."""

    def __init__(
        self,
        settings: Settings,
        size_cache: SizeCache | None = None,
        webhook_notifier: WebhookNotifier | None = None,
    ) -> None:
        self._settings = settings
        #: Invalidated after a successful prefill job (WP 1.5: plan §3's "du
        #: over depot folders, cached" needs an explicit invalidation hook,
        #: not polling). None in tests that don't care about size caching.
        self._size_cache = size_cache
        #: WP 3.13: fires job.done/job.error/job.cancelled webhooks. None in
        #: every existing test that does not care about webhooks — see
        #: webhooks.notify_job_event's own None-is-a-no-op guard.
        self._webhook_notifier = webhook_notifier
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:  # pragma: no cover - guarded by lifespan
            raise RuntimeError("worker already started")
        # daemon=True so a SIGKILLed/aborted shutdown can never wedge the
        # interpreter; stop() is still the orderly path.
        self._thread = threading.Thread(
            target=self._run, name="vault-prefill-worker", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = SHUTDOWN_JOIN_TIMEOUT_SECONDS) -> None:
        self._stop.set()
        thread = self._thread
        if thread is None:
            return
        thread.join(timeout=timeout)
        if thread.is_alive():  # pragma: no cover - only on an unkillable subprocess
            logger.warning(
                "Prefill worker did not stop within %.0fs; leaving it as a daemon "
                "thread. Any job still marked 'running' will be recovered to "
                "'error' on the next startup.",
                timeout,
            )

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    # -- loop --------------------------------------------------------------

    def _run(self) -> None:
        conn = get_connection(self._settings.db_path)
        try:
            while not self._stop.is_set():
                # WP S-1 (ADR-0012): in queue mode, a prefill job that was
                # already handed off to a runner BEFORE this process started
                # (i.e. a previous vault-api process died while the runner
                # kept going — the crash-semantics case ADR-0012 §4
                # covers) is still 'running' but invisible to
                # ``claim_next_job`` (it only claims 'queued' rows). Checked
                # first, every tick, ahead of claiming anything new — the
                # project's one-job-at-a-time invariant means there is at
                # most one such row, and it must be resumed before this
                # worker looks for other work.
                if self._settings.prefill_mode_queue:
                    try:
                        reattach = jobs.find_active_run(conn)
                    except sqlite3.Error:
                        # Same net as claim_next_job below (WP API-FIX-1,
                        # S2): a locked/failed lookup must not end the
                        # worker thread.
                        logger.exception(
                            "Failed to look up an active run; retrying after a poll"
                        )
                        self._stop.wait(self._settings.worker_poll_seconds)
                        continue
                    if reattach is not None:
                        self._resume_prefill(conn, reattach)
                        if not self._stop.is_set() and self._still_running(
                            conn, int(reattach["id"])  # type: ignore[arg-type]
                        ):
                            # N6: _resume_prefill could not move the row on
                            # (the DB is broken and even recording the
                            # failure failed). find_active_run would return
                            # it again at once; back off instead of spinning.
                            self._stop.wait(self._settings.worker_poll_seconds)
                        continue

                try:
                    job = jobs.claim_next_job(conn)
                except sqlite3.Error:
                    # A locked/failed claim must not kill the worker; the job
                    # stays 'queued' and the next tick retries.
                    logger.exception("Failed to claim a job; retrying after a poll")
                    self._stop.wait(self._settings.worker_poll_seconds)
                    continue

                if job is None:
                    self._stop.wait(self._settings.worker_poll_seconds)
                    continue

                self._execute(conn, job)
        finally:
            conn.close()

    def _still_running(self, conn: sqlite3.Connection, job_id: int) -> bool:
        """Whether ``job_id`` is still 'running'. A DB error counts as yes,
        so the caller backs off rather than spinning on a broken database."""
        try:
            row = jobs.get_run_row(conn, job_id)
        except sqlite3.Error:
            return True
        return row is not None and row.get("status") == jobs.STATUS_RUNNING

    def _execute(self, conn: sqlite3.Connection, job: dict[str, object]) -> None:
        """Dispatch one claimed job to the code that knows how to run it.

        Deliberately an exhaustive match rather than "prefill unless it says
        gc" (WP 3.8): an unrecognised ``type`` is failed with a clear message,
        never guessed at. Guessing would mean a typo'd or future job type
        silently running SteamPrefill against an app id, and the queue is
        shared with a job type that deletes files.
        """
        job_type = str(job["type"])
        if job_type == jobs.JOB_TYPE_PREFILL:
            self._execute_prefill(conn, job)
            return
        if job_type == jobs.JOB_TYPE_GC:
            # Owns its own error handling and never raises; it deliberately
            # does NOT touch apps.status (see gc_execute's module docstring).
            gc_execute.run_gc_job(
                conn,
                job,
                settings=self._settings,
                size_cache=self._size_cache,
                webhook_notifier=self._webhook_notifier,
            )
            return
        self._fail_unknown_job_type(conn, job, job_type)

    def _fail_unknown_job_type(
        self, conn: sqlite3.Connection, job: dict[str, object], job_type: str
    ) -> None:
        job_id = int(job["id"])  # type: ignore[arg-type]
        logger.error(
            "Job %s has unknown type %r; failing it rather than guessing what to run.",
            job_id, job_type,
        )
        try:
            webhooks.finish_job_and_notify(
                conn,
                self._webhook_notifier,
                job_id,
                jobs.STATUS_ERROR,
                f"[vault-api] Unknown job type {job_type!r}. This worker only runs "
                f"{jobs.JOB_TYPE_PREFILL!r} and {jobs.JOB_TYPE_GC!r} jobs, and does "
                "not guess: nothing was executed for this job.",
            )
        except Exception:  # pragma: no cover - DB itself is broken
            logger.exception("Could not even record the failure of job %s", job_id)

    def _execute_prefill(self, conn: sqlite3.Connection, job: dict[str, object]) -> None:
        job_id = int(job["id"])  # type: ignore[arg-type]
        appid = int(job["appid"])  # type: ignore[arg-type]
        logger.info("Starting prefill job %s for appid %s", job_id, appid)

        try:
            jobs.set_app_status(conn, appid, jobs.STATUS_RUNNING)

            # WP 3.4 / ADR-0006 decision 2: read BEFORE running, right after
            # set_app_status above has already ensured the apps row exists.
            # --force is reserved for first fills and post-deletion refills
            # (needs_force set by the deletion path, see deletion.py); every
            # other run is a genuinely cheap non-forced staleness check.
            use_force = jobs.get_app_needs_force(conn, appid)
            before = prefill.scan_depots(self._settings.cache_root)

            if self._settings.prefill_mode_queue:
                result_and_before = self._run_prefill_via_queue(
                    conn, job_id, appid, use_force, before
                )
                if result_and_before is None:
                    # WP S-1 / ADR-0012 §4: vault-api is shutting down
                    # while waiting on a SEPARATE runner process that has not
                    # been asked to stop. The job is left exactly as
                    # 'running' with its hand-off intact — _run's reattach
                    # path (jobs.find_active_run) resumes waiting on it, via
                    # this exact same call, the next time this worker starts.
                    logger.info(
                        "vault-api is shutting down while job %s (appid %s) "
                        "waits on its runner; leaving it 'running' so a "
                        "future startup's worker resumes waiting on it.",
                        job_id, appid,
                    )
                    return
                result, before = result_and_before
            else:
                result = prefill.run_prefill(
                    appid=appid,
                    steamprefill_path=self._settings.steamprefill_path,
                    timeout_seconds=self._settings.prefill_timeout_seconds,
                    should_abort=self._stop.is_set,
                    use_force=use_force,
                    # WP 3.12: read on the runner's own 0.2 s poll tick, from THIS
                    # thread's connection (the thread-confinement rule in
                    # deps.py/db.py holds — `conn` belongs to the worker thread and
                    # never leaves it).
                    stop_request=lambda: jobs.read_stop_request(conn, job_id),
                    # WP CORE-FIX-2: same bound as vault-runner's queue mode.
                    max_threads=self._settings.prefill_max_threads,
                )

            self._finalize_prefill_result(conn, job_id, appid, use_force, before, result)
        except Exception:
            # Last-resort net: the worker thread must survive any bug in the
            # job body, otherwise the queue silently stops draining.
            logger.exception("Prefill job %s crashed", job_id)
            message = (
                "[vault-api] Internal error while running this job:\n"
                + traceback.format_exc()
            )
            try:
                jobs.set_app_status(conn, appid, jobs.STATUS_ERROR)
                webhooks.finish_job_and_notify(
                    conn, self._webhook_notifier, job_id, jobs.STATUS_ERROR, message
                )
            except Exception:  # pragma: no cover - DB itself is broken
                logger.exception("Could not even record the failure of job %s", job_id)

    # -- WP S-1 (ADR-0012) --------------------------------------------------

    def _run_prefill_via_queue(
        self,
        conn: sqlite3.Connection,
        job_id: int,
        appid: int,
        use_force: bool,
        before: dict[int, DepotSignature],
    ) -> tuple[prefill.PrefillResult, dict[int, DepotSignature]] | None:
        """Hand a freshly claimed prefill job off to a runner and wait.

        Thin wrapper around ``jobs.handoff_run`` + ``prefill_queue.await_run_result``
        — see their docstrings for the mechanics. ``None`` propagates the
        "vault-api is shutting down mid-wait" signal to the caller unchanged.
        """
        jobs.handoff_run(conn, job_id, use_force, prefill_queue.encode_signatures(before))
        return prefill_queue.await_run_result(
            conn,
            job_id,
            lease_timeout_seconds=self._settings.runner_lease_timeout_seconds,
            poll_seconds=self._settings.worker_poll_seconds,
            should_abort=self._stop.is_set,
        )

    def _resume_prefill(self, conn: sqlite3.Connection, run_row: dict[str, object]) -> None:
        """Resume waiting on a queue-mode prefill job handed off before a
        vault-api restart (``PrefillWorker._run``'s reattach check,
        ``jobs.find_active_run``).

        Deliberately does NOT repeat any of ``_execute_prefill``'s claim-time
        setup: ``apps.status`` is already 'running' (set before the restart
        and never reset — nothing has touched it), and ``use_force``/
        ``before`` are read back from the row exactly as they were recorded
        at the ORIGINAL hand-off (``handoff_run``'s own docstring on why a
        restarted process must not re-derive them). This function only waits
        and finalizes — the same ``await_run_result`` call the fresh-claim
        path uses, just skipping the ``handoff_run`` write (already done).
        """
        job_id = int(run_row["id"])  # type: ignore[arg-type]
        appid = int(run_row["appid"])  # type: ignore[arg-type]
        use_force = bool(run_row["run_use_force"])
        before = prefill_queue.decode_signatures(
            run_row["run_before_json"] if isinstance(run_row.get("run_before_json"), str) else None
        )
        logger.info(
            "Resuming wait on job %s (appid %s): handed off to a runner "
            "before this vault-api process started.",
            job_id, appid,
        )
        try:
            outcome = prefill_queue.await_run_result(
                conn,
                job_id,
                lease_timeout_seconds=self._settings.runner_lease_timeout_seconds,
                poll_seconds=self._settings.worker_poll_seconds,
                should_abort=self._stop.is_set,
            )
            if outcome is None:
                logger.info(
                    "vault-api is shutting down again while job %s (appid %s) "
                    "still waits on its runner; leaving it 'running'.",
                    job_id, appid,
                )
                return
            result, before = outcome
            self._finalize_prefill_result(conn, job_id, appid, use_force, before, result)
        except Exception:
            logger.exception("Reattached prefill job %s crashed", job_id)
            message = (
                "[vault-api] Internal error while resuming this job after a "
                "vault-api restart:\n" + traceback.format_exc()
            )
            try:
                jobs.set_app_status(conn, appid, jobs.STATUS_ERROR)
                webhooks.finish_job_and_notify(
                    conn, self._webhook_notifier, job_id, jobs.STATUS_ERROR, message
                )
            except Exception:  # pragma: no cover - DB itself is broken
                logger.exception("Could not even record the failure of job %s", job_id)

    def _finalize_prefill_result(
        self,
        conn: sqlite3.Connection,
        job_id: int,
        appid: int,
        use_force: bool,
        before: dict[int, DepotSignature],
        result: "prefill.PrefillResult",
    ) -> None:
        """Everything that happens once a ``PrefillResult`` exists, regardless
        of whether THIS process ran SteamPrefill directly (``VAULT_PREFILL_MODE
        =subprocess``) or a separate runner did (``=queue``, WP S-1 /
        ADR-0012). Extracted verbatim from the pre-WP-S-1 body of
        ``_execute_prefill`` — see ``docs/adr/0012-*.md`` §6 for why
        byte-identical subprocess-mode behaviour depends on this function
        being reached the exact same way it always was in that mode.
        """
        try:
            log_parts = [result.output]

            # WP 3.12: an operator-requested stop that was actually honored.
            # Handled before anything else because it is neither a success nor
            # a failure, and both of the branches below would misreport it.
            if result.failure_reason in prefill.STOP_FAILURE_REASONS:
                self._finish_stopped_prefill(conn, job_id, appid, result, log_parts)
                return

            # A request that lost the race with the process finishing (see
            # prefill._wait_for_process): the run has a real outcome, so that
            # outcome stands and the log says why the request had no effect.
            # jobs.finish_job clears the pending request either way.
            late_stop = jobs.read_stop_request(conn, job_id)
            if late_stop is not None:
                log_parts.append(
                    f"[vault-api] A '{late_stop}' request for this job arrived "
                    "after SteamPrefill had already exited on its own, so it "
                    "was not applied — the outcome recorded below is what "
                    "really happened."
                )

            if result.success:
                # WP 3.3 / ADR-0006 decision 1, reordered ahead of the
                # mapping/manifest mutation below (review S2 fix): SteamPrefill
                # exiting 0 does NOT mean it did anything for this app -- parse
                # its own summary table and let THAT decide the job outcome
                # FIRST, before anything on disk gets attributed to it. Doing
                # this after apply_observed_mapping/ingest_after_prefill left
                # an error-outcome job's mapping/depot_manifests state mutated
                # from evidence that (by the unowned branch's own definition)
                # cannot belong to this app -- an app that was never
                # considered has no depots and no manifests to attribute,
                # planted-or-not. See prefill_summary.py's docstring for the
                # evidenced reason a table like this exists at all: an
                # unowned app exits 0 with a "Prefilled 0 apps" /
                # Updated=0, Up To Date=0 summary (the WP 1.7 job-outcome
                # trap this whole package closes).
                summary = prefill_summary.parse_summary(result.output)
                # WP API-FIX-3: SteamPrefill's own table says the app FAILED
                # (a Failed column with a count above zero, or one that
                # could not be read). Checked before the 0/0 "not
                # considered" rule below, which a failed run's table
                # (0 | 0 | 1) would otherwise satisfy.
                run_failed = prefill_summary.reports_failure(summary)

                # An unparseable table that still showed a Failed column is
                # NOT the exit-code-rule case: the prefill_failed line below
                # says what happened, so the fallback note is skipped.
                if not summary.parse_ok and not run_failed:
                    logger.warning(
                        "Could not parse SteamPrefill's summary table for "
                        "appid %s (job %s); job outcome falls back to the "
                        "exit-code rule (process exited 0 -> 'done').",
                        appid, job_id,
                    )
                    log_parts.append(
                        "[vault-api] Could not parse SteamPrefill's summary "
                        "table; job outcome follows the exit-code rule only "
                        "(see api/README.md's job-outcome table)."
                    )
                elif summary.parse_ok:
                    log_parts.append(
                        "[vault-api] Prefill summary: updated="
                        f"{summary.updated} up_to_date={summary.up_to_date}"
                        + (f" failed={summary.failed}" if summary.failed_column else "")
                        + (
                            f" (totaling {summary.total_bytes_text})"
                            if summary.total_bytes_text
                            else ""
                        )
                    )

                if run_failed:
                    self._finish_prefill_failed(conn, job_id, appid, result, summary, log_parts)
                    return

                unowned = (
                    summary.parse_ok
                    and summary.updated == 0
                    and summary.up_to_date == 0
                )

                if unowned:
                    # Updated==0 AND Up To Date==0 means SteamPrefill never
                    # actually considered this app — reporting that as a
                    # successful prefill is precisely the trap above. The job
                    # ends 'error'; apps.status follows it to 'error' too
                    # (deliberately NOT 'done' — this run accomplished
                    # nothing for the app; NOT left at 'running' or reset to
                    # 'idle' either, since a run did execute, it just found
                    # nothing to do, and 'error' is the status this file
                    # already uses elsewhere for "a run happened and produced
                    # no usable outcome"). Neither last_prefill_at nor
                    # last_manifest_check are touched — nothing was prefilled
                    # or confirmed current. The depot mapping AND manifest
                    # state (depot_manifests) are likewise deliberately left
                    # untouched — scan_depots/apply_observed_mapping/
                    # ingest_after_prefill below never run for this branch
                    # (review S2), even if a depot directory or a stray
                    # SteamPrefill .bin file happens to exist on disk: an app
                    # SteamPrefill never considered has nothing that can
                    # honestly be attributed to it.
                    log_parts.append(
                        "[vault-api] SteamPrefill did not consider this app "
                        "- is it owned by the logged-in account? Depot "
                        "mapping and manifest state were NOT touched."
                    )
                    jobs.set_app_status(conn, appid, jobs.STATUS_ERROR)
                    webhooks.finish_job_and_notify(
                        conn, self._webhook_notifier,
                        job_id, jobs.STATUS_ERROR, "\n".join(log_parts),
                        updated=summary.updated,
                        up_to_date=summary.up_to_date,
                        summary_parse_ok=summary.parse_ok,
                    )
                    logger.warning(
                        "Prefill job %s for appid %s ended 'error': "
                        "SteamPrefill reported Updated=0 and Up To Date=0 "
                        "(app not considered).",
                        job_id, appid,
                    )
                    return

                after = prefill.scan_depots(self._settings.cache_root)
                observed = prefill.diff_depots(before, after)
                change = prefill.apply_observed_mapping(conn, appid, observed)
                log_parts.append(change.summary())

                # WP 3.2: learn what SteamPrefill just wrote (manifest state
                # for staleness/GC, ADR-0006/0007) from its temp-cache .bin
                # files. Wrapped in its own try/except, deliberately LOCAL to
                # this block: the job has already succeeded at this point,
                # and a bug in ingestion must never flip a genuinely
                # successful prefill to 'error' (the outer except below would
                # do exactly that if this were allowed to propagate).
                try:
                    ingest_result = manifest_ingest.ingest_after_prefill(
                        conn, appid=appid, settings=self._settings
                    )
                    log_parts.append(ingest_result.summary())
                except Exception:
                    logger.exception(
                        "Manifest ingestion crashed for appid %s (job %s); the "
                        "prefill job itself still succeeded.",
                        appid, job_id,
                    )
                    log_parts.append(
                        "[vault-api] Manifest ingestion crashed; see server logs "
                        "(the prefill job itself still succeeded)."
                    )

                now = jobs.utcnow_iso()
                # ADR-0006 "current as of <timestamp>" semantics: only this
                # exact shape — nothing changed, but SteamPrefill DID confirm
                # at least one already-current depot for this app — earns
                # last_manifest_check. A parse failure or the updated>0 case
                # both leave it untouched (None -> set_app_status skips it).
                last_manifest_check = (
                    now
                    if summary.parse_ok
                    and summary.updated == 0
                    and summary.up_to_date is not None
                    and summary.up_to_date > 0
                    else None
                )

                jobs.set_app_status(
                    conn, appid, jobs.STATUS_DONE,
                    last_prefill_at=now,
                    last_manifest_check=last_manifest_check,
                )

                # WP 3.4 / ADR-0006 decision 2, hardened against a concurrent
                # DELETE (reviewer-reproduced wedge, see
                # jobs.clear_needs_force_if_unchanged's docstring for the
                # full sequence): this is the ONE branch that reaches a
                # genuinely successful outcome (covers both the updated>0 and
                # the up-to-date-confirmed rows of the table above) --
                # --force has done its job for this app (or was never
                # needed), so the next run may safely go non-forced. But the
                # clear is a compare-and-swap against `use_force` -- the
                # value read at job-claim time, BEFORE this run started --
                # not an unconditional write: if a DELETE landed on this app
                # while the job was running and set needs_force=1 (cache
                # state changed underneath this run), the swap's WHERE
                # clause no longer matches and the clear is correctly a
                # no-op, leaving the NEXT run forced instead of wedging the
                # app at 'done' over an empty cache forever. The unowned
                # branch above and every failure branch below never call
                # this at all, leaving the flag exactly as the deletion path
                # (or the schema default) last set it.
                if not jobs.clear_needs_force_if_unchanged(conn, appid, use_force):
                    logger.info(
                        "Prefill job %s for appid %s: needs_force was changed "
                        "concurrently (likely a DELETE) while this job ran; "
                        "left as-is so the next run for this app is forced.",
                        job_id, appid,
                    )

                # WP 3.12's auto-GC hook. Deliberately the LAST thing before
                # the job is finalized: it must see the run's real summary, it
                # must not run for any other branch, and its log line belongs
                # in this job's excerpt.
                self._maybe_queue_auto_gc(conn, job_id, appid, summary, log_parts)

                webhooks.finish_job_and_notify(
                    conn, self._webhook_notifier,
                    job_id, jobs.STATUS_DONE, "\n".join(log_parts),
                    updated=summary.updated,
                    up_to_date=summary.up_to_date,
                    summary_parse_ok=summary.parse_ok,
                )

                # Disk content may have changed (plan §3: size calculation is
                # "cached" — explicit invalidation, not polling). Whether this
                # particular run was forced or not, invalidating unconditionally
                # rather than only when `observed` is non-empty is still
                # correct and cheap: a no-op recompute costs one disk walk.
                if self._size_cache is not None:
                    self._size_cache.invalidate()

                logger.info(
                    "Prefill job %s for appid %s done (%d depots observed)",
                    job_id, appid, len(observed),
                )
                return

            # Failure: the mapping is deliberately NOT touched. A partial or
            # aborted run is no evidence about which depots belong to the app,
            # and replace-semantics on bad evidence would delete good rows.
            log_parts.append(
                "[vault-api] Prefill failed "
                f"(reason={result.failure_reason}); the depot mapping for this app "
                "was left unchanged."
            )
            jobs.set_app_status(conn, appid, jobs.STATUS_ERROR)
            webhooks.finish_job_and_notify(
                conn, self._webhook_notifier,
                job_id, jobs.STATUS_ERROR, "\n".join(log_parts),
            )
            logger.warning(
                "Prefill job %s for appid %s failed (%s)",
                job_id, appid, result.failure_reason,
            )
        except Exception:
            # Last-resort net: the worker thread must survive any bug in the
            # job body, otherwise the queue silently stops draining.
            logger.exception("Prefill job %s crashed", job_id)
            message = (
                "[vault-api] Internal error while running this job:\n"
                + traceback.format_exc()
            )
            try:
                jobs.set_app_status(conn, appid, jobs.STATUS_ERROR)
                webhooks.finish_job_and_notify(
                    conn, self._webhook_notifier, job_id, jobs.STATUS_ERROR, message
                )
            except Exception:  # pragma: no cover - DB itself is broken
                logger.exception("Could not even record the failure of job %s", job_id)

    def _finish_prefill_failed(
        self,
        conn: sqlite3.Connection,
        job_id: int,
        appid: int,
        result: prefill.PrefillResult,
        summary: "prefill_summary.PrefillSummary",
        log_parts: list[str],
    ) -> None:
        """SteamPrefill exited 0 but its summary reports the app as failed
        (WP API-FIX-3, reason ``prefill_failed``).

        Same outcome as every other failure: job and ``apps.status`` go to
        ``error``, and nothing on disk is attributed to the app. The depot
        mapping, the manifest state, ``needs_force``, ``last_prefill_at`` and
        ``last_manifest_check`` are left alone and no auto-GC job is queued,
        because none of that code is reached from here. A failed run is no
        evidence about which depots belong to the app (half a download may
        sit on disk), so replace-semantics on it would delete good rows.

        The summary counters are still recorded on the job row, as they were
        before this reason existed (the failed table used to take the 0/0
        branch). The LAST log line keeps the ``Prefill failed (reason=...)``
        shape the web reads (web/js/lib/job-failure.js) and adds what failed
        and the likely cause.
        """
        detail = prefill.prefill_failed_detail(summary.failed, result.output)
        log_parts.append(
            f"[vault-api] Prefill failed (reason={prefill.FAILURE_PREFILL_FAILED}): "
            f"{detail}. The depot mapping and manifest state for this app were "
            "left unchanged."
        )
        jobs.set_app_status(conn, appid, jobs.STATUS_ERROR)
        webhooks.finish_job_and_notify(
            conn, self._webhook_notifier,
            job_id, jobs.STATUS_ERROR, "\n".join(log_parts),
            updated=summary.updated,
            up_to_date=summary.up_to_date,
            summary_parse_ok=summary.parse_ok,
        )
        logger.warning(
            "Prefill job %s for appid %s failed (%s): SteamPrefill exited 0 "
            "but its summary reported Failed=%s.",
            job_id, appid, prefill.FAILURE_PREFILL_FAILED,
            summary.failed if summary.failed is not None else "unreadable",
        )

    # -- WP 3.12 -----------------------------------------------------------

    def _finish_stopped_prefill(
        self,
        conn: sqlite3.Connection,
        job_id: int,
        appid: int,
        result: prefill.PrefillResult,
        log_parts: list[str],
    ) -> None:
        """Record a prefill that an operator cancelled or paused.

        **What is deliberately NOT done here, and why (the needs_force /
        ingestion audit this package owed):**

        - **No summary parse.** ``prefill_summary.parse_summary`` is only ever
          called on the success branch above, and a terminated run must keep it
          that way: SteamPrefill prints its ``Updated``/``Up To Date`` table at
          the *end* of a run, so a run killed part-way either has no table or
          has one describing a different, earlier state. The job's ``updated``/
          ``up_to_date``/``summary_parse_ok`` columns therefore stay ``NULL``
          ("not applicable"), never a guessed zero — and ``0/0`` in particular
          has the specific "app not owned" meaning (ADR-0006 decision 1) that a
          stopped run has not earned.
        - **No depot mapping.** Same rule every other non-success branch
          follows: a partial run is no evidence about which depots belong to
          the app, and ``apply_observed_mapping``'s replace-semantics
          (ADR-0003 decision 3) would delete good rows on the strength of it.
        - **No manifest ingestion.** ``manifest_ingest.ingest_after_prefill``
          lives inside the success branch, so a run killed mid-depot can never
          ingest a half-written ``.bin`` file. This function existing does not
          change that; the test suite pins it.
        - **No ``needs_force`` clear.** ``clear_needs_force_if_unchanged`` is
          likewise reached only from the success branch, so the flag stays
          exactly as the deletion path (or the schema default) left it. That is
          the correct answer rather than merely a convenient one: the run did
          not complete, so whatever made it forced still holds, and a resumed
          forced run is not wasteful — ``--force`` makes SteamPrefill re-request
          the chunks, and the ones already on disk come back as local HITs.

        What DOES survive is the only thing that should: the bytes SteamPrefill
        already wrote into the cache. Nothing here deletes or rolls back
        anything, and the next run — resumed or not — replays them at disk
        speed. SteamPrefill's own ``successfullyDownloadedDepots.json`` keeps
        claiming the depots it actually finished, which is TRUE, so a
        non-forced resume skips exactly those: cache-as-progress-store, using
        the tool's own bookkeeping rather than fighting it.
        """
        stopped = result.failure_reason
        log_parts.append(
            "[vault-api] This run was stopped on request "
            f"({stopped}). The depot mapping, the manifest state and the "
            "needs_force flag for this app were all left untouched — a run "
            "that did not complete is not evidence about any of them. "
            "Everything already written to the cache stays on disk."
        )

        # apps.status must not stay 'running' (nothing is), and must not become
        # 'error' (nothing failed) — see jobs.reset_app_status_if_running.
        jobs.reset_app_status_if_running(conn, appid)

        if stopped == prefill.FAILURE_PAUSED:
            jobs.park_paused(conn, job_id, "\n".join(log_parts))
            logger.info(
                "Prefill job %s for appid %s paused on request; SteamPrefill "
                "was terminated (exit code %s) and the job is parked until "
                "POST /v1/jobs/%s/resume.",
                job_id, appid, result.exit_code, job_id,
            )
        else:
            webhooks.finish_job_and_notify(
                conn, self._webhook_notifier,
                job_id, jobs.STATUS_CANCELLED, "\n".join(log_parts),
            )
            logger.info(
                "Prefill job %s for appid %s cancelled on request; "
                "SteamPrefill was terminated (exit code %s).",
                job_id, appid, result.exit_code,
            )

        # A stopped run is the one non-success case that reliably DID change
        # disk content (it downloaded until the moment it was stopped), and an
        # operator who just pressed stop is looking at the UI right now — so
        # the size cache is invalidated here as well as on success, rather than
        # letting GET /v1/games show a pre-run number for up to
        # VAULT_SIZE_CACHE_TTL seconds.
        if self._size_cache is not None:
            self._size_cache.invalidate()

    def _maybe_queue_auto_gc(
        self,
        conn: sqlite3.Connection,
        job_id: int,
        appid: int,
        summary: "prefill_summary.PrefillSummary",
        log_parts: list[str],
    ) -> None:
        """``VAULT_AUTO_GC``: queue a GC job after a prefill that changed something.

        Three conditions, all required, and each one is a decision:

        1. ``VAULT_AUTO_GC`` is ``dry-run`` or ``execute`` (``off`` through
           WP 3.12 — "a feature that can delete files does not switch itself
           on" — but ``execute`` since WP SWEEP-1 / ADR-0014, 2026-08-22,
           paired with ``sweep_include_cached`` defaulting on in the same
           change; see ``docs/adr/0014-sweep-cached-and-auto-gc-default-on.md``).
           This applies regardless of whether the scheduler's own window is
           configured — the trigger here is any successful, updating prefill
           (manual, miss-triggered, or scheduled), not the sweep itself.
        2. This job reached the **successful** branch. A failed, aborted,
           unowned, cancelled or paused run tells us nothing about what is now
           orphaned, and collecting off the back of one would be acting on a
           cache state nobody vouched for.
        3. The summary parsed **and** ``updated > 0``. Orphans are produced by
           *game updates*: superseded chunks are exactly what an update leaves
           behind. A run that only confirmed "up to date" (``updated == 0``)
           changed nothing, so there is nothing new to collect, and queueing GC
           after every routine staleness check would turn ADR-0006's ~3 s no-op
           into a full depot scan on every sweep tick. A summary that could not
           be parsed is not evidence of an update either.

        No new mechanism: this calls the same ``jobs.enqueue_gc`` the endpoint
        does, so per-(app, mode) dedupe applies unchanged — an operator's
        pending GC job for this app in the same mode absorbs the automatic one
        instead of stacking a second scan.

        Wrapped in its own ``try``: a prefill that genuinely succeeded must not
        be flipped to ``error`` because a follow-up job could not be queued
        (the same reasoning the manifest-ingestion call above is wrapped for).

        Settings-API work package (ADR-0009 decision 6): resolves DB
        override > env > default through ``settings_store.effective_settings``
        using the connection this job is already running on, rather than
        reading ``self._settings.auto_gc`` directly — that is what makes a
        ``PATCH /v1/settings`` change to ``auto_gc`` apply "immediately"
        (the very next job this worker finishes), not only after a restart.

        The resolution itself is wrapped in its own ``try``, same reasoning
        as the ``enqueue_gc`` call below: this whole method's contract is
        "a follow-up job that cannot be queued is a log line, never a reason
        to flip an already-successful prefill to 'error'", and a broken
        connection must not violate that contract one statement earlier than
        the existing guard. Falls back to ``self._settings`` (the env/default
        snapshot with no override applied) on any failure.
        """
        try:
            settings = settings_store.effective_settings(conn, self._settings)
        except Exception:
            logger.exception(
                "Auto-GC: could not resolve settings overrides for job %s "
                "(appid %s); falling back to the env/default configuration "
                "for this decision.",
                job_id, appid,
            )
            settings = self._settings
        if not settings.auto_gc_enabled:
            return
        if not summary.parse_ok or summary.updated is None or summary.updated <= 0:
            return

        try:
            gc_job, created = jobs.enqueue_gc(
                conn, appid, execute=settings.auto_gc_executes
            )
        except Exception:
            logger.exception(
                "Auto-GC could not be queued for appid %s after job %s; the "
                "prefill job itself still succeeded.",
                appid, job_id,
            )
            log_parts.append(
                "[vault-api] Auto-GC (VAULT_AUTO_GC="
                f"{settings.auto_gc}) could not be queued; see the server log "
                "(the prefill job itself still succeeded)."
            )
            return

        log_parts.append(
            f"[vault-api] Auto-GC (VAULT_AUTO_GC={settings.auto_gc}): this run "
            f"updated {summary.updated} app(s), so GC job {gc_job['id']} was "
            + ("queued" if created else "already queued")
            + f" for app {appid} in {settings.auto_gc} mode."
        )
        logger.info(
            "Auto-GC queued GC job %s for appid %s in %s mode after prefill "
            "job %s (deduplicated=%s)",
            gc_job["id"], appid, settings.auto_gc, job_id, not created,
        )
