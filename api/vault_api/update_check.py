"""The always-on update check (WP UPD-1, user decision 2026-10-13).

Two callers, one routine:

* the scheduler thread (``maybe_check_updates``), once per
  ``VAULT_SCHEDULE_INTERVAL_MINUTES``, **independent of the schedule window** —
  a vault with no window, or one that is currently closed, still learns which
  cached games have an update;
* ``POST /v1/updates/check`` (``routers/updates.py``), on demand.

Both only **refresh the manifest oracle** (``oracle.refresh_app``) for games
that currently hold cache content and then read back the per-app verdict. Neither
starts a download: the download enqueue stays in ``scheduler.maybe_sweep``,
gated by the window exactly as before. "Update available" is the oracle's
``stale`` verdict — the oracle's current public manifest differs from the one
vault-api recorded for the cache (``oracle.app_view``).

**What leaves the LAN.** The check talks to the manifest oracle and nothing
else, so it is exactly as private as ``POST /v1/oracle/{appid}/refresh`` and is
on only when the operator enabled ``VAULT_MANIFEST_ORACLE`` (off by default,
ADR-0006 decision 4). With the oracle off the check does nothing and says so
(``enabled: false`` / ``skipped_reason: "oracle-disabled"``); it never falls
back to another source. Fetching is injectable (``fetch``) so no test, and no
dev instance with the oracle unset, ever touches the network.

**Fail-soft and polite.** ``refresh_app`` never raises; a failed app keeps its
previous snapshot. A dead oracle is not hammered once per cached game: after
``CONSECUTIVE_FAILURE_LIMIT`` failures in a row the run stops and reports
``bailed_out``. Requests are spaced by ``pause`` and only one check runs at a
time per process (``_RUN_LOCK``): a second one reports ``already-running``.

**Bookkeeping** is the single-row ``update_check_state`` table (schema v19),
claim-then-work like ``scheduler.claim_sweep``: the claim is stamped in a
``BEGIN IMMEDIATE`` transaction *before* any request, so a restart or crash
does not re-check until the interval has passed.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable

from vault_api import jobs, oracle
from vault_api.config import Settings

logger = logging.getLogger(__name__)

#: Stop a run after this many failed refreshes in a row (a dead or blocked
#: oracle). The apps not reached keep their previous snapshot and are retried
#: by the next run.
CONSECUTIVE_FAILURE_LIMIT = 5

#: Pause between two oracle requests in a scheduled run, so a few hundred
#: cached games are not a burst. Not applied to ``POST /v1/updates/check``
#: callers' own pacing; the endpoint uses the same default.
INTER_REQUEST_PAUSE_SECONDS = 0.25

#: One check at a time per process (scheduler thread vs. endpoint vs. a second
#: endpoint call). Never blocks: a busy lock is reported, not waited on.
_RUN_LOCK = threading.Lock()


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
    #: Why not, when ``ran`` is False: ``"oracle-disabled"``,
    #: ``"interval-not-elapsed"``, ``"already-running"``.
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

    Never starts a download, never raises for an oracle problem. Holds the
    process-wide run lock for its whole duration.
    """
    if not settings.manifest_oracle_enabled:
        return UpdateCheckResult(enabled=False, skipped_reason="oracle-disabled")

    selected = sorted(set(appids))
    if not _RUN_LOCK.acquire(blocking=False):
        return UpdateCheckResult(enabled=True, skipped_reason="already-running")
    try:
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
    finally:
        _RUN_LOCK.release()


# --------------------------------------------------------------------------
# Scheduled run: claim-then-work bookkeeping
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
    conn: sqlite3.Connection, now: datetime, interval_minutes: int
) -> bool:
    """Re-check the interval and stamp ``last_check_at`` atomically (one
    ``BEGIN IMMEDIATE``), before any request is made. Resets the counters to
    NULL ("in flight, or the process died in it")."""
    if not _interval_elapsed(read_last_check_at(conn), now, interval_minutes):
        return False
    with jobs.immediate_transaction(conn):
        if not _interval_elapsed(read_last_check_at(conn), now, interval_minutes):
            return False
        conn.execute(
            """
            INSERT INTO update_check_state
                (id, last_check_at, last_check_apps, last_check_ok, last_check_stale)
            VALUES (1, ?, NULL, NULL, NULL)
            ON CONFLICT(id) DO UPDATE SET
                last_check_at = excluded.last_check_at,
                last_check_apps = NULL,
                last_check_ok = NULL,
                last_check_stale = NULL
            """,
            (jobs.to_utc_iso(now),),
        )
    return True


def finish_update_check(
    conn: sqlite3.Connection, *, apps: int, ok: int, stale: int
) -> None:
    """Record what the claimed run found. Leaves ``last_check_at`` alone."""
    conn.execute(
        """
        UPDATE update_check_state
        SET last_check_apps = ?, last_check_ok = ?, last_check_stale = ?
        WHERE id = 1
        """,
        (apps, ok, stale),
    )
    conn.commit()


def maybe_check_updates(
    conn: sqlite3.Connection,
    settings: Settings,
    now: datetime,
    select_appids: Callable[[], Iterable[int]],
    *,
    fetch: oracle.Fetcher | None = None,
    should_abort: Callable[[], bool] = _never,
    pause: Callable[[], None] = _no_pause,
) -> UpdateCheckResult:
    """One scheduler tick of the update check.

    Deliberately does **not** look at ``settings.schedule_window``: that is the
    point of this feature. Only the oracle switch and the interval gate it.
    ``select_appids`` is called only after the claim, so the (disk-walking)
    cached-app selection is not paid on the ~99% of ticks that decide "not yet".
    """
    if not settings.manifest_oracle_enabled:
        return UpdateCheckResult(enabled=False, skipped_reason="oracle-disabled")
    if not claim_update_check(conn, now, settings.schedule_interval_minutes):
        return UpdateCheckResult(enabled=True, skipped_reason="interval-not-elapsed")

    result = check_apps(
        conn,
        settings,
        select_appids(),
        fetch=fetch,
        should_abort=should_abort,
        pause=pause,
    )
    finish_update_check(
        conn, apps=len(result.apps), ok=result.ok_count, stale=result.stale_count
    )
    logger.info(
        "update check: %d cached app(s) checked, %d ok, %d failed, %d with an "
        "update available%s%s",
        len(result.apps), result.ok_count, result.failed_count, result.stale_count,
        " [ABORTED by shutdown]" if result.aborted else "",
        " [STOPPED after repeated failures]" if result.bailed_out else "",
    )
    return result
