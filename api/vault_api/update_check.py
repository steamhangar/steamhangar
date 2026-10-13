"""The always-on update check (WP UPD-1, user decision 2026-10-13).

Two triggers, one background runner (``UpdateCheckRunner``):

* the scheduler tick (``trigger="scheduled"``), once per
  ``VAULT_SCHEDULE_INTERVAL_MINUTES``, **while a schedule window is
  configured, open or closed** — a vault whose window is currently closed
  still learns which cached games have an update; a vault with no window at
  all (the dev instance) never checks on its own (review round 2, "only prod
  checks");
* ``POST /v1/updates/check`` (``trigger="manual"``, ``routers/updates.py``),
  on demand, not interval-limited.

Neither caller waits for the check: ``UpdateCheckRunner.start`` decides, stamps
the bookkeeping row and hands the work to a daemon thread, so the HTTP request
answers ``202`` at once and the scheduler tick is never stalled by oracle
round trips. ``GET /v1/updates/status`` reads what the last run found.

A run only **refreshes the manifest oracle** (``oracle.refresh_app``) for games
that currently hold cache content. It never starts a download: the download
enqueue stays in ``scheduler.maybe_sweep``, gated by the window exactly as
before. "Update available" is the oracle's ``stale`` verdict — the oracle's
current public manifest differs from the one vault-api recorded for the cache
(``oracle.app_view``).

**What leaves the LAN.** A run talks to the manifest oracle and nothing else,
one request per cached game (``POST /v1/oracle/{appid}/refresh`` is the
single-app equivalent). ``VAULT_MANIFEST_ORACLE`` gates both triggers: with
the oracle off, nothing runs, nothing is stamped, and the API says
``enabled: false``; there is no fallback source. The scheduled trigger
additionally needs a configured schedule window. Fetching is injectable
(``fetch``) so no test ever touches the network.

**Fail-soft and polite.** ``refresh_app`` never raises; a failed app keeps its
previous snapshot. A dead oracle is not hammered once per cached game: after
``CONSECUTIVE_FAILURE_LIMIT`` failures in a row the run stops (outcome
``bailed_out``). Requests are spaced by ``INTER_REQUEST_PAUSE_SECONDS`` and only
one run exists at a time per runner (the app shares one runner between the
scheduler and the endpoint): a second start reports ``already-running``.

**Bookkeeping** is the single-row ``update_check_state`` table (schema
v19/v20), claim-then-work like ``scheduler.claim_sweep``: ``last_check_at`` is
stamped in a ``BEGIN IMMEDIATE`` transaction *before* any request, by either
trigger, so a restart or crash does not re-check until the interval has passed,
and a manual run also pushes the next scheduled one an interval out (no double
traffic right after a click). The counters and ``last_check_finished_at`` are
NULL while a run is in flight, or if the process died in one.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable
from urllib.parse import urlsplit

from vault_api import jobs, oracle
from vault_api.config import Settings
from vault_api.db import get_connection

logger = logging.getLogger(__name__)

#: Stop a run after this many failed refreshes in a row (a dead or blocked
#: oracle). The apps not reached keep their previous snapshot and are retried
#: by the next run.
CONSECUTIVE_FAILURE_LIMIT = 5

#: Pause between two oracle requests in one run, so a few hundred cached games
#: are not a burst. Applies to both triggers.
INTER_REQUEST_PAUSE_SECONDS = 0.25

#: How long ``UpdateCheckRunner.stop`` waits for a running check to notice the
#: stop flag. One oracle request is bounded by its own timeout (10 s default).
STOP_JOIN_TIMEOUT_SECONDS = 15.0

TRIGGER_MANUAL = "manual"
TRIGGER_SCHEDULED = "scheduled"

#: ``UpdateCheckRunner.start`` results.
STARTED = "started"
ALREADY_RUNNING = "already-running"
DISABLED = "oracle-disabled"
INTERVAL_NOT_ELAPSED = "interval-not-elapsed"
#: Scheduled trigger only: no schedule window is configured (WP UPD-1 review
#: round 2, "only prod checks": a vault without a window, like the dev
#: instance, never checks on its own; the manual trigger still works).
NO_SCHEDULE_WINDOW = "no-schedule-window"
#: The runner was stopped (shutdown): nothing new starts.
STOPPING = "stopping"

#: ``update_check_state.last_check_outcome`` values (NULL while running).
OUTCOME_COMPLETED = "completed"
OUTCOME_BAILED_OUT = "bailed_out"
OUTCOME_ABORTED = "aborted"
OUTCOME_ERROR = "error"
#: Not stored: reported by ``read_status`` for a row with no finish stamp and
#: no run in flight (the process died during the run).
OUTCOME_INTERRUPTED = "interrupted"

#: Selects the appids to check, given the run thread's own connection.
AppSelector = Callable[[sqlite3.Connection], Iterable[int]]


def _never() -> bool:
    return False


def _no_pause() -> None:
    return None


@dataclass(frozen=True)
class AppCheck:
    """One app's outcome."""

    appid: int
    #: Did this refresh succeed? ``False`` keeps the previous snapshot.
    ok: bool
    error: str
    #: ``stale`` | ``current`` | ``unknown`` (``oracle.app_view``'s roll-up).
    verdict: str
    #: When the stored snapshot (this one if ``ok``) was taken; ``None`` if the
    #: oracle has never answered for this app.
    checked_at: str | None


@dataclass(frozen=True)
class UpdateCheckResult:
    """Outcome of one check run. ``ran`` is False when nothing was done."""

    enabled: bool
    ran: bool = False
    #: Why not, when ``ran`` is False: ``"oracle-disabled"``.
    skipped_reason: str = ""
    apps: tuple[AppCheck, ...] = ()
    #: Shutdown interrupted the run.
    aborted: bool = False
    #: Too many consecutive failures: the run stopped early.
    bailed_out: bool = False
    #: Apps selected but not reached (abort or bail-out).
    unchecked: int = 0

    @property
    def ok_count(self) -> int:
        return sum(1 for app in self.apps if app.ok)

    @property
    def failed_count(self) -> int:
        return sum(1 for app in self.apps if not app.ok)

    @property
    def stale_count(self) -> int:
        return sum(1 for app in self.apps if app.verdict == oracle.VERDICT_STALE)

    @property
    def outcome(self) -> str:
        if self.aborted:
            return OUTCOME_ABORTED
        if self.bailed_out:
            return OUTCOME_BAILED_OUT
        return OUTCOME_COMPLETED


def check_apps(
    conn: sqlite3.Connection,
    settings: Settings,
    appids: Iterable[int],
    *,
    fetch: oracle.Fetcher | None = None,
    should_abort: Callable[[], bool] = _never,
    pause: Callable[[], None] = _no_pause,
) -> UpdateCheckResult:
    """Refresh the oracle for ``appids`` and return each app's verdict.

    The synchronous core of a run. Never starts a download, never raises for
    an oracle problem. Holds no lock: exclusivity is ``UpdateCheckRunner``'s
    job.
    """
    if not settings.manifest_oracle_enabled:
        return UpdateCheckResult(enabled=False, skipped_reason=DISABLED)

    selected = sorted(set(appids))
    checks: list[AppCheck] = []
    aborted = False
    bailed_out = False
    consecutive_failures = 0
    for index, appid in enumerate(selected):
        if should_abort():
            aborted = True
            break
        if index:
            pause()
            if should_abort():
                aborted = True
                break
        refreshed = oracle.refresh_app(conn, appid, settings=settings, fetch=fetch)
        view = oracle.app_view(conn, appid, settings=settings)
        checks.append(
            AppCheck(
                appid=appid,
                ok=refreshed.ok,
                error=refreshed.error,
                verdict=view.verdict,
                checked_at=view.checked_at,
            )
        )
        consecutive_failures = 0 if refreshed.ok else consecutive_failures + 1
        if consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT:
            bailed_out = True
            logger.warning(
                "update check: %d refreshes failed in a row (last: %s); "
                "stopping this run, the remaining apps are retried next time",
                consecutive_failures, refreshed.error,
            )
            break
    return UpdateCheckResult(
        enabled=True,
        ran=True,
        apps=tuple(checks),
        aborted=aborted,
        bailed_out=bailed_out,
        unchecked=len(selected) - len(checks),
    )


def describe_update_check(settings: Settings) -> str:
    """The line saying whether the check is on (like
    ``scheduler.describe_resolved_schedule``); the scheduler logs it on its
    first tick and again whenever the answer changes (a window set or cleared
    through ``PATCH /v1/settings``)."""
    if not settings.manifest_oracle_enabled:
        return (
            "Update check: OFF (VAULT_MANIFEST_ORACLE=off). No oracle request "
            "is made, no 'update available' flag is set."
        )
    host = urlsplit(settings.manifest_oracle_url).hostname or settings.manifest_oracle_url
    if settings.schedule_window is None:
        return (
            "Update check: scheduled checks off, no schedule window; manual "
            f"check available (POST /v1/updates/check asks the oracle at {host})."
        )
    return (
        f"Update check: ON (manifest oracle {settings.manifest_oracle!r} at "
        f"{host}). Every {settings.schedule_interval_minutes} minute(s) while "
        f"a schedule window is configured ({settings.schedule_window.raw}, "
        "open or closed), each cached game's app id is sent to the oracle; "
        "downloads still only start inside the window. Set "
        "VAULT_MANIFEST_ORACLE=off to stop all oracle requests."
    )


# --------------------------------------------------------------------------
# Bookkeeping: update_check_state (single row)
# --------------------------------------------------------------------------


def read_last_check_at(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT last_check_at FROM update_check_state WHERE id = 1"
    ).fetchone()
    return None if row is None else row["last_check_at"]


def _interval_elapsed(
    last_check_at: str | None, now: datetime, interval_minutes: int
) -> bool:
    """Same rules as ``scheduler.interval_elapsed``: never checked or an
    unreadable stamp means due; a stamp in the FUTURE means not due (skipping is
    quiet and recoverable, treating it as long ago would check on every tick)."""
    if last_check_at is None:
        return True
    last = jobs.parse_utc_iso(last_check_at)
    if last is None:
        logger.warning(
            "update check: update_check_state.last_check_at is %r, which is not "
            "a 'YYYY-MM-DDTHH:MM:SSZ' timestamp; treating it as 'never checked'",
            last_check_at,
        )
        return True
    return now.astimezone(timezone.utc) >= last + timedelta(minutes=interval_minutes)


def claim_update_check(
    conn: sqlite3.Connection,
    now: datetime,
    *,
    trigger: str,
    interval_minutes: int | None,
) -> bool:
    """Stamp the start of a run atomically (one ``BEGIN IMMEDIATE``), before
    any request is made.

    ``interval_minutes`` re-checks the interval inside the transaction
    (scheduled runs); ``None`` skips that gate (manual runs). Resets the
    counters, the finish stamp and the outcome to NULL ("in flight, or the
    process died in it").
    """
    with jobs.immediate_transaction(conn):
        if interval_minutes is not None and not _interval_elapsed(
            read_last_check_at(conn), now, interval_minutes
        ):
            return False
        conn.execute(
            """
            INSERT INTO update_check_state
                (id, last_check_at, last_check_trigger, last_check_finished_at,
                 last_check_outcome, last_check_apps, last_check_ok,
                 last_check_stale)
            VALUES (1, ?, ?, NULL, NULL, NULL, NULL, NULL)
            ON CONFLICT(id) DO UPDATE SET
                last_check_at = excluded.last_check_at,
                last_check_trigger = excluded.last_check_trigger,
                last_check_finished_at = NULL,
                last_check_outcome = NULL,
                last_check_apps = NULL,
                last_check_ok = NULL,
                last_check_stale = NULL
            """,
            (jobs.to_utc_iso(now), trigger),
        )
    return True


def finish_update_check(
    conn: sqlite3.Connection,
    *,
    finished_at: datetime,
    outcome: str,
    apps: int | None,
    ok: int | None,
    stale: int | None,
) -> None:
    """Record what the claimed run found. Leaves ``last_check_at`` alone."""
    conn.execute(
        """
        UPDATE update_check_state
        SET last_check_finished_at = ?, last_check_outcome = ?,
            last_check_apps = ?, last_check_ok = ?, last_check_stale = ?
        WHERE id = 1
        """,
        (jobs.to_utc_iso(finished_at), outcome, apps, ok, stale),
    )
    conn.commit()


@dataclass(frozen=True)
class UpdateCheckStatus:
    """``GET /v1/updates/status``'s data."""

    enabled: bool
    running: bool
    last_trigger: str | None
    last_started_at: str | None
    last_finished_at: str | None
    last_outcome: str | None
    last_apps: int | None
    last_ok: int | None
    last_stale: int | None

    @property
    def last_failed(self) -> int | None:
        if self.last_apps is None or self.last_ok is None:
            return None
        return self.last_apps - self.last_ok


def read_status(
    conn: sqlite3.Connection, settings: Settings, *, running: bool
) -> UpdateCheckStatus:
    row = conn.execute("SELECT * FROM update_check_state WHERE id = 1").fetchone()
    if row is None:
        return UpdateCheckStatus(
            enabled=settings.manifest_oracle_enabled,
            running=running,
            last_trigger=None,
            last_started_at=None,
            last_finished_at=None,
            last_outcome=None,
            last_apps=None,
            last_ok=None,
            last_stale=None,
        )
    current = UpdateCheckStatus(
        enabled=settings.manifest_oracle_enabled,
        running=running,
        last_trigger=row["last_check_trigger"],
        last_started_at=row["last_check_at"],
        last_finished_at=row["last_check_finished_at"],
        last_outcome=row["last_check_outcome"],
        last_apps=row["last_check_apps"],
        last_ok=row["last_check_ok"],
        last_stale=row["last_check_stale"],
    )
    return current if running else settle_status(current)


def settle_status(current: UpdateCheckStatus) -> UpdateCheckStatus:
    """The status with no run in flight: a started run without a finish
    stamp then means the process died during it (``interrupted``)."""
    outcome = current.last_outcome
    if (
        outcome is None
        and current.last_started_at is not None
        and current.last_finished_at is None
    ):
        outcome = OUTCOME_INTERRUPTED
    return replace(current, running=False, last_outcome=outcome)


# --------------------------------------------------------------------------
# The background runner
# --------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class UpdateCheckRunner:
    """Runs one update check at a time on a daemon thread.

    ``main.create_app`` builds one and hands the SAME instance to the
    scheduler and to ``POST /v1/updates/check``, so the two triggers can never
    overlap (a second start answers ``already-running``). ``start`` itself
    never makes a network request: it checks the switch, takes the run lock
    without blocking, stamps the claim and starts the thread. The thread opens
    its own SQLite connection (connections never cross threads), selects the
    apps (the disk walk happens there too), refreshes, records the result and
    releases the lock.
    """

    def __init__(
        self,
        db_path: str,
        *,
        fetch: oracle.Fetcher | None = None,
        pause_seconds: float | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._db_path = db_path
        self._fetch = fetch
        #: ``None`` = read ``INTER_REQUEST_PAUSE_SECONDS`` at run time.
        self._pause_seconds = pause_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._lock.locked()

    def start(
        self,
        conn: sqlite3.Connection,
        settings: Settings,
        *,
        trigger: str,
        select_appids: AppSelector,
        now: datetime,
        require_interval: bool,
    ) -> str:
        """Start a run in the background; returns ``STARTED``,
        ``ALREADY_RUNNING``, ``DISABLED``, ``STOPPING``,
        ``NO_SCHEDULE_WINDOW`` (scheduled trigger only) or
        ``INTERVAL_NOT_ELAPSED``.

        The scheduled trigger runs only while a schedule window is configured
        (``settings.schedule_window``, the tick's effective settings, so a
        window set later through ``PATCH /v1/settings`` enables it without a
        restart); whether that window is open right now does not matter.

        ``conn`` is the CALLER's connection and is only used for the claim.
        With ``require_interval`` (the scheduler) the cheap interval read comes
        first, so the ~99% of ticks that decide "not yet" take no lock and log
        nothing; a busy runner is then reported, never recorded (the running
        run's row stays as it is).
        """
        if not settings.manifest_oracle_enabled:
            return DISABLED
        if self._stop.is_set():
            return STOPPING
        if trigger == TRIGGER_SCHEDULED and settings.schedule_window is None:
            return NO_SCHEDULE_WINDOW
        interval = settings.schedule_interval_minutes if require_interval else None
        if interval is not None and not _interval_elapsed(
            read_last_check_at(conn), now, interval
        ):
            return INTERVAL_NOT_ELAPSED
        if not self._lock.acquire(blocking=False):
            return ALREADY_RUNNING
        try:
            if not claim_update_check(conn, now, trigger=trigger, interval_minutes=interval):
                self._lock.release()
                return INTERVAL_NOT_ELAPSED
            thread = threading.Thread(
                target=self._run,
                args=(settings, trigger, select_appids),
                name="vault-update-check",
                daemon=True,
            )
            self._thread = thread
            thread.start()
        except BaseException:
            self._lock.release()
            raise
        return STARTED

    def _pause(self) -> None:
        seconds = (
            INTER_REQUEST_PAUSE_SECONDS
            if self._pause_seconds is None
            else self._pause_seconds
        )
        if seconds > 0:
            self._stop.wait(seconds)

    def _run(self, settings: Settings, trigger: str, select_appids: AppSelector) -> None:
        try:
            with closing(get_connection(self._db_path)) as conn:
                try:
                    result = check_apps(
                        conn,
                        settings,
                        select_appids(conn),
                        fetch=self._fetch,
                        should_abort=self._stop.is_set,
                        pause=self._pause,
                    )
                except Exception:
                    logger.exception(
                        "update check (%s) failed; the claim is already "
                        "stamped, so the next scheduled check is one interval "
                        "away.",
                        trigger,
                    )
                    finish_update_check(
                        conn,
                        finished_at=self._clock(),
                        outcome=OUTCOME_ERROR,
                        apps=None,
                        ok=None,
                        stale=None,
                    )
                    return
                finish_update_check(
                    conn,
                    finished_at=self._clock(),
                    outcome=result.outcome,
                    apps=len(result.apps),
                    ok=result.ok_count,
                    stale=result.stale_count,
                )
                logger.info(
                    "update check (%s): %d cached app(s) checked, %d ok, %d "
                    "failed, %d with an update available%s%s",
                    trigger,
                    len(result.apps), result.ok_count, result.failed_count,
                    result.stale_count,
                    " [ABORTED by shutdown]" if result.aborted else "",
                    " [STOPPED after repeated failures]" if result.bailed_out else "",
                )
        except Exception:
            logger.exception("update check (%s): could not record the result", trigger)
        finally:
            self._lock.release()

    def wait(self, timeout: float | None = None) -> bool:
        """Join the current run, if any. True when no run is left."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
            return not thread.is_alive()
        return True

    def stop(self, timeout: float = STOP_JOIN_TIMEOUT_SECONDS) -> None:
        """Ask a running check to stop between two apps and wait for it.
        Permanent: a stopped runner's later runs abort at once."""
        self._stop.set()
        if not self.wait(timeout):  # pragma: no cover - a hung oracle socket only
            logger.warning(
                "Update check did not stop within %.0fs; leaving it as a "
                "daemon thread.",
                timeout,
            )
