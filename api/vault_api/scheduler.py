"""The prefill scheduler (WP 3.5) — plan §7 Phase 3's "configurable cron window".

What it does, in one sentence: inside a configurable daytime window, every
``VAULT_SCHEDULE_INTERVAL_MINUTES``, take the union of the app ids every
gaming machine most recently reported as installed and enqueue a normal
prefill job for each one.

Plan A7 ("prefill updates automatically during the day") + A8 ("the criterion
is: games actually installed on the gaming machines"). The target set was the
agent reports and nothing else through Phase 3 — no popularity heuristic, no
size budget, no manual include/exclude list. Installed *is* the prefill set.

Phase 4d adds exactly one more source: every app with cache content on disk,
regardless of whether anything currently reports it installed
(``VAULT_SWEEP_INCLUDE_CACHED`` / ``Settings.sweep_include_cached``,
``compute_targets``'s ``include_cached`` parameter). Off by default through
WP 4d; **on by default since WP SWEEP-1 / ADR-0014 (2026-08-22, operator
decision)**, paired with auto-GC also defaulting to ``execute`` in the same
change — see ``docs/adr/0014-sweep-cached-and-auto-gc-default-on.md``.
``compute_targets``'s own ``include_cached`` keyword parameter still
defaults to ``False`` (unchanged by ADR-0014, see its docstring below) —
that is a separate, defensive default for direct/test callers that bypass
``Settings`` entirely, not the value the shipped scheduler actually runs
with. See that function's docstring and api/README.md's "Sweep target set"
section for the rationale, the cost model, and the auto-GC coupling this
couples with (``cached_sweep_gc_risk``).

Why enqueuing everything is already the rate limiting
-----------------------------------------------------
ADR-0006's honest-limits section: each per-app check costs a Steam login
(~3 s), so sweeps are "spaced across the cron window, not batched" — batching
several apps into one SteamPrefill invocation would destroy per-app
attribution (one exit code, one summary table, N apps).

The spacing mechanism is therefore deliberately *not* a rate limiter in this
module. There is exactly ONE worker thread and it runs exactly one job at a
time (plan §3, ``vault_api/worker.py``), so a sweep that enqueues 60 apps
produces 60 sequential runs — the queue drains at whatever pace SteamPrefill
manages, one login at a time, and the next sweep cannot even start until the
interval has elapsed. Adding a sleep between enqueues would only make the
*queueing* slow, not the work, while breaking the queue's own dedupe window
and delaying shutdown. Nothing to add.

Cost of a sweep, for the same reason: a non-forced run for an already-current
app is a ~3 s no-op (ADR-0006 decision 1 — SteamPrefill's own up-to-date
bookkeeping). A sweep over an unchanged library is minutes of idle checking,
not a re-download.

Why it needs no lock against ``DELETE /v1/cache/{appid}``
---------------------------------------------------------
Because the scheduler is *just another client of the existing enqueue path*.
It calls ``jobs.enqueue_prefill``, exactly like ``POST /v1/prefill`` does for
a button press in the app (and like the Phase-3 miss trigger will). It never
touches the filesystem, never claims a job and never runs SteamPrefill. So it
introduces no interaction that the reviewed WP 1.4/1.6/3.4 semantics do not
already cover:

* **Job already queued/running for that app** — ``enqueue_prefill`` dedupes
  inside ``BEGIN IMMEDIATE`` and hands back the existing job. The sweep counts
  it as "already active" and moves on.
* **A DELETE is in flight for that app** — ``DELETE`` refuses with 409 while a
  job is queued/running, and its own docstring already documents the
  check-then-act window in the other direction (a job enqueued microseconds
  after its guard). The consequence there is stated and benign: the job
  refills what was deleted. A sweep makes that window no more dangerous, only
  slightly more likely to be hit; the operator-visible effect is that a DELETE
  during the window may 409 and need a retry.
* **A DELETE lands while a swept job is running** — covered by WP 3.4's
  compare-and-swap in ``jobs.clear_needs_force_if_unchanged``: the deletion's
  ``needs_force = 1`` survives, so the next run for that app is forced. That
  is precisely the wedge that CAS was added to close, and it closes it for
  scheduler-enqueued jobs identically.

A lock would therefore protect nothing that is not already protected, while
adding a way for a stuck sweep to block deletions. Deliberately absent.

Threading and database access
-----------------------------
A second daemon thread next to the worker, started and stopped by the FastAPI
lifespan (``vault_api/main.py``), same shape as ``PrefillWorker``. It opens its
own SQLite connection **inside its own thread** and closes it there — the
one-thread-one-connection rule this project's LEARNINGS.md was written in
blood over (a connection touched from two threads segfaulted CPython, see
``vault_api/deps.py``). It shares nothing with the worker but the database
file, and WAL + ``busy_timeout`` (``db.get_connection``) is what makes that
safe.

Timezone
--------
The window is interpreted in **server-local time** — the machine's timezone
(the container's ``TZ``, if you set one). That is the useful reading for
"09:00-17:00 while I'm at work". Every *timestamp* the scheduler stores or
reports is UTC in the project's standard ``YYYY-MM-DDTHH:MM:SSZ`` form, same
as every other table.

DST: the tick loop re-reads the clock and re-evaluates ``contains`` every
minute, so a transition simply shifts when the window opens and closes in UTC
— nothing to correct. On the "spring forward" night an overnight window can
be one hour shorter (and one hour longer in autumn); the interval gate is
UTC-based and unaffected. The only value that can be an hour off across a
transition is the advisory ``next_eligible_at`` in ``GET /v1/schedule`` (see
``schedule_window.next_open``); no decision is made from it.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from vault_api import (
    agent_reports,
    deletion,
    event_sweep,
    jobs,
    settings_store,
    sizes,
    webhooks,
)
from vault_api.config import Settings
from vault_api.db import get_connection

# Re-exported under their historical names (WP 3.11 moved the bodies into
# ``jobs`` so ``event_sweep`` can share them without an import cycle — this
# module imports ``event_sweep``, so ``event_sweep`` must not import this one).
# ``scheduler.to_utc_iso`` / ``scheduler.parse_utc_iso`` keep working for every
# existing caller and test.
from vault_api.jobs import (  # noqa: F401  (re-export)
    TIMESTAMP_FORMAT as _TIMESTAMP_FORMAT,
    immediate_transaction,
    parse_utc_iso,
    to_utc_iso,
)
from vault_api.schedule_window import next_open

logger = logging.getLogger(__name__)

#: How often the thread wakes up to ask "is a sweep due?". Not the sweep
#: interval — that is ``VAULT_SCHEDULE_INTERVAL_MINUTES``, checked on each of
#: these ticks. One minute is the resolution the window itself has (whole
#: minutes), and a wakeup that reads one row and usually decides "no" is free.
#: Deliberately not an env var: it is an implementation detail with no
#: operator-visible effect, and the tests inject it directly.
DEFAULT_TICK_SECONDS = 60.0

#: How long ``stop()`` waits for the thread. A tick does a handful of small
#: queries, so this only has to cover an in-flight sweep's enqueue loop (which
#: also checks the stop flag between apps).
SHUTDOWN_JOIN_TIMEOUT_SECONDS = 30.0

#: Returns the current time as an AWARE datetime in the server's local zone.
Clock = Callable[[], datetime]


def local_now() -> datetime:
    """Current server-local time as an *aware* datetime (the default clock).

    Aware, not naive, on purpose: the window is evaluated on the local wall
    clock while every stored timestamp is UTC, and one aware value gives both
    without a second clock reading that could disagree with the first.
    ``datetime.now().astimezone()`` attaches the system's current UTC offset,
    so ``.hour``/``.minute`` are local and ``.astimezone(timezone.utc)`` is
    exact.

    Tests inject a fixed-offset clock instead, which is what makes every
    window decision reproducible on any machine's timezone.
    """
    return datetime.now().astimezone()


# --------------------------------------------------------------------------
# schedule_state (schema v6) — the single-row sweep bookkeeping
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ScheduleState:
    """The ``schedule_state`` row. All fields ``None`` before the first sweep."""

    last_sweep_at: str | None
    #: Size of the last sweep's target set. ``None`` while a sweep is running
    #: (or if the process died during one) — see the table's DDL comment.
    last_sweep_targets: int | None
    #: How many NEW jobs the last sweep created (dedupe hits are not counted).
    last_sweep_enqueued: int | None


#: What ``read_state`` returns for a database that has never been swept.
EMPTY_STATE = ScheduleState(
    last_sweep_at=None, last_sweep_targets=None, last_sweep_enqueued=None
)


def read_state(conn: sqlite3.Connection) -> ScheduleState:
    """Read the single ``schedule_state`` row (``EMPTY_STATE`` if absent)."""
    row = conn.execute(
        """
        SELECT last_sweep_at, last_sweep_targets, last_sweep_enqueued
        FROM schedule_state WHERE id = 1
        """
    ).fetchone()
    if row is None:
        return EMPTY_STATE
    return ScheduleState(
        last_sweep_at=row["last_sweep_at"],
        last_sweep_targets=row["last_sweep_targets"],
        last_sweep_enqueued=row["last_sweep_enqueued"],
    )


def interval_elapsed(
    state: ScheduleState, now: datetime, interval_minutes: int
) -> bool:
    """Has ``interval_minutes`` passed since the last sweep started?

    ``True`` when nothing has ever been swept, or when the stored timestamp is
    unreadable (logged — an unreadable value must not disable the scheduler
    permanently; the next sweep overwrites it with a good one).

    A stored timestamp in the *future* (the operator stepped the clock back,
    or the container's clock was wrong on a previous boot) makes this
    ``False`` until real time catches up. That is the deliberate direction to
    fail in: skipping sweeps is recoverable and quiet, whereas treating a
    future timestamp as "long ago" would sweep on every single tick — a Steam
    login storm — until somebody noticed.
    """
    if state.last_sweep_at is None:
        return True
    last = parse_utc_iso(state.last_sweep_at)
    if last is None:
        logger.warning(
            "scheduler: schedule_state.last_sweep_at is %r, which is not a "
            "'YYYY-MM-DDTHH:MM:SSZ' timestamp; treating it as 'never swept' "
            "(the next sweep replaces it)",
            state.last_sweep_at,
        )
        return True
    return now.astimezone(timezone.utc) >= last + timedelta(minutes=interval_minutes)


def claim_sweep(
    conn: sqlite3.Connection, now: datetime, interval_minutes: int
) -> bool:
    """Re-check the interval and stamp ``last_sweep_at`` atomically.

    Claim-then-work, the same shape as ``jobs.claim_next_job``: the interval
    check and the write that consumes it are one ``BEGIN IMMEDIATE``
    transaction, so the check-then-act cannot be interleaved (LEARNINGS.md:
    "every check-then-act needs BEGIN IMMEDIATE"). Only one scheduler thread
    exists today, so this is belt-and-braces — but it is also what makes the
    crash-recovery rule real: ``last_sweep_at`` is committed **before** any
    job is enqueued, so a process that dies mid-sweep does not re-sweep on
    restart. It waits out the interval like any other sweep, and the apps it
    did not reach are picked up by the next one (they are still installed —
    the target set is recomputed from scratch every time, not a work list).

    The two counters are reset to NULL by the same statement; ``finish_sweep``
    fills them in afterwards.
    """
    if not interval_elapsed(read_state(conn), now, interval_minutes):
        return False

    with immediate_transaction(conn):
        # Re-read inside the lock: the value may have changed between the
        # cheap pre-check above (which exists only to avoid taking the write
        # lock on the ~99% of ticks that decide "not yet") and here.
        if not interval_elapsed(read_state(conn), now, interval_minutes):
            return False
        conn.execute(
            """
            INSERT INTO schedule_state
                (id, last_sweep_at, last_sweep_targets, last_sweep_enqueued)
            VALUES (1, ?, NULL, NULL)
            ON CONFLICT(id) DO UPDATE SET
                last_sweep_at = excluded.last_sweep_at,
                last_sweep_targets = NULL,
                last_sweep_enqueued = NULL
            """,
            (to_utc_iso(now),),
        )
    return True


def finish_sweep(conn: sqlite3.Connection, targets: int, enqueued: int) -> None:
    """Record what the just-claimed sweep found and created.

    Deliberately does NOT touch ``last_sweep_at`` — that was stamped at claim
    time and the interval is measured from the sweep's *start*, so a long
    sweep cannot push the next one further out than the configured interval.
    """
    conn.execute(
        """
        UPDATE schedule_state
        SET last_sweep_targets = ?, last_sweep_enqueued = ?
        WHERE id = 1
        """,
        (targets, enqueued),
    )
    conn.commit()


# --------------------------------------------------------------------------
# Target set — plan A8: installed on the gaming machines IS the prefill set
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ExcludedClient:
    """A client whose latest snapshot did not contribute to the target set."""

    client_id: str
    #: One of ``"stale"``, ``"unreadable-snapshot"``, ``"unreadable-timestamp"``.
    reason: str
    #: Its newest report's timestamp, for the log line / the operator.
    last_reported_at: str | None


@dataclass(frozen=True)
class TargetSet:
    """The union of every fresh client's latest installed list, plus (WP 4d;
    on by default since WP SWEEP-1 / ADR-0014, 2026-08-22) every app with
    cache content on disk."""

    appids: list[int]
    included_clients: list[str]
    excluded_clients: list[ExcludedClient]
    #: WP 4d. Apps present ONLY because ``include_cached`` was set — i.e. NOT
    #: already reachable through the installed-clients union above. Always
    #: ``()`` when the mode is off (default-constructed, never touched), and
    #: also ``()`` when every cached app happens to already be installed
    #: somewhere. Exists for the sweep's log line and for tests to pin the
    #: union+dedupe behaviour directly — ``appids`` already contains every
    #: one of these, this field is purely "which ones, and why".
    cached_only_appids: tuple[int, ...] = ()


@dataclass(frozen=True)
class FreshClientSnapshot:
    """One client's latest agent-report snapshot, having ALREADY passed the
    same freshness gate ``compute_targets`` applies before trusting it for
    sweep targeting (readable snapshot, parseable timestamp, not older than
    ``stale_after_days``). See ``fresh_client_snapshots`` below."""

    client_id: str
    reported_at: str
    appids: list[int]


def fresh_client_snapshots(
    conn: sqlite3.Connection, now: datetime, stale_after_days: int
) -> tuple[list[FreshClientSnapshot], list[ExcludedClient]]:
    """Every client's latest snapshot that passes the sweep's own freshness
    gate, plus the ones that were excluded and why.

    Extracted out of ``compute_targets`` (WP AG-1) so a SECOND caller —
    ``routers/games.py``'s per-app ``installed_on`` field — asks the exact
    same "is this client's claim fresh enough to trust" question through the
    exact same code, not a hand-copied re-implementation of the three
    exclusion checks. ``docs/LEARNINGS.md``'s "two call sites computing the
    same predicate diverge" entry (WP 4f) is precisely the failure class this
    avoids: a UI badge built from an independently-written staleness check
    could disagree with the scheduler about which reports it trusts, and
    "installed on the operator's server" from a report the scheduler itself
    would refuse to act on is the same dishonesty in a different place.

    ``compute_targets`` is UNCHANGED in behaviour — it now builds its
    ``appids``/``included_clients`` from this function's result instead of
    running the loop itself, but the loop body is verbatim.
    """
    cutoff = now.astimezone(timezone.utc) - timedelta(days=stale_after_days)

    client_rows = conn.execute(
        "SELECT DISTINCT client_id FROM agent_reports ORDER BY client_id"
    ).fetchall()

    fresh: list[FreshClientSnapshot] = []
    excluded: list[ExcludedClient] = []

    for row in client_rows:
        client_id = str(row["client_id"])
        snapshot = agent_reports.latest_snapshot(conn, client_id)
        # WP AG-1: no longer provably unreachable. Before ``DELETE
        # /v1/clients/{client_id}`` existed, nothing could remove a client's
        # LAST row between this DISTINCT query and the lookup above, so this
        # was dead code. Now a DELETE committing in that exact window makes
        # ``latest_snapshot`` return None for a client_id this loop just
        # read — see ``agent_reports.delete_client``'s docstring and
        # ``test_a_client_deleted_between_the_distinct_query_and_the_lookup_
        # is_silently_skipped`` below, which exercises this line directly
        # rather than relying on a real race to hit it.
        if snapshot is None:
            continue

        if snapshot.appids is None:
            excluded.append(
                ExcludedClient(client_id, "unreadable-snapshot", snapshot.reported_at)
            )
            continue

        reported_at = parse_utc_iso(snapshot.reported_at)
        if reported_at is None:
            excluded.append(
                ExcludedClient(
                    client_id, "unreadable-timestamp", snapshot.reported_at
                )
            )
            continue

        if reported_at < cutoff:
            excluded.append(
                ExcludedClient(client_id, "stale", snapshot.reported_at)
            )
            continue

        fresh.append(
            FreshClientSnapshot(
                client_id=client_id,
                reported_at=snapshot.reported_at,
                appids=snapshot.appids,
            )
        )

    return fresh, excluded


def cached_appids(conn: sqlite3.Connection, cache_root: str) -> set[int]:
    """Every appid that currently holds cache content on disk — **exclusive
    OR last-cached-remnant** — right now.

    **WP 4f: this is now a thin wrapper around
    ``deletion.appids_with_cache_content``, the ONE shared definition of
    "which apps hold cache content" also used by ``routers/jobs.py``'s
    ``POST /v1/prefill/cached`` selection.** Before WP 4f this function
    computed its own, narrower answer (``exclusive`` only) independently of
    that route's own, more generous one — and the two disagreed on exactly
    the case that matters most: a game the operator just deleted whose only
    surviving content is a shared, still-protected depot. The sweep
    correctly excluded it; the button re-queued it — one press away from the
    exact re-download-after-delete behaviour Plan A exists to prevent
    (2026-08-18 user decision: "the user's decision applies to both paths").
    See ``deletion.appids_with_cache_content``'s docstring for the full "why
    exclusive + remnant, why one shared bulk read" write-up, and
    api/README.md's "Sweep target set" section for the cost-model numbers.

    **Deliberately NARROWER than ``sizes.app_size_bytes``'s "cached" — and
    that is still the point.** ``app_size_bytes`` (``GET /v1/games``'s
    per-app ``size_bytes``) answers "how much would deleting this app free",
    and correctly counts a shared depot's bytes into EVERY co-owner's total
    (plan §4) regardless of whether any of them still has other content.
    That double-counting is exactly right for a sizing question and exactly
    wrong for a sweep target-set question — the shared helper only counts a
    shared depot toward an app when no OTHER co-owner currently has content
    (ADR-0003's remnant rule), which is what stops a "shared and protected"
    depot from making a fully-deleted app look cached forever.
    ``sizes.app_size_bytes`` itself is UNCHANGED by this package.

    A raw, uncached filesystem walk (``sizes.scan_depot_dir_bytes``), not the process-wide
    ``SizeCache`` — WP 4d's sweep runs at most once per
    ``VAULT_SCHEDULE_INTERVAL_MINUTES`` (default 3 h), so the walk's own cost
    is negligible next to that cadence, and threading the request-scoped
    ``SizeCache`` into this background thread would add a second dependency
    (and a second TTL policy to reason about) for a call site that already
    only fires a few times a day.

    **Three things about that walk worth stating plainly (reviewer
    should-fix S5, 2026-08-18 review round) — see api/README.md "Sweep
    target set" for the full write-up:**

    1. **Order of magnitude.** A warm-cache directory walk runs at roughly
       microseconds per file (``sizes.walk_file_stats``'s own measured
       17x-over-``os.walk`` figure implies low-single-digit microseconds per
       entry on NVMe) — a few hundred thousand chunk files is sub-second.
       Cold-cache / spinning-disk is a different story: each depot directory
       is a separate seek-bound `readdir`, so tens of seconds to low minutes
       is plausible on a large HDD-backed library that has not been walked
       recently. Either way it is bounded and read-only, never a download.
    2. **No coalescing with concurrent request-path scans.** Deliberately
       NOT using ``SizeCache`` (see above) means this walk and an unrelated
       ``GET /v1/cache/summary`` cache miss can walk the SAME depot tree at
       the same time, doing the work twice. This is not a rare coincidence:
       ``worker.py`` calls ``SizeCache.invalidate()`` after every successful
       prefill, so a miss is common in exactly the window a sweep is also
       enqueueing work — the two are correlated, not independent. The same
       caveat now applies the other way too (WP 4f): ``POST
       /v1/prefill/cached`` DOES use the shared ``SizeCache``, so its own
       cold-cache walk and a concurrent sweep's raw walk can likewise
       duplicate the same directory tree — see api/README.md's "Check &
       update all cached games" section, which cross-references this
       paragraph instead of leaving the interaction sweep-only documented.
    3. **Not interruptible mid-walk.** ``PrefillScheduler``'s ``should_abort``
       is only checked inside the PER-APP enqueue loop in ``maybe_sweep``,
       never inside this walk. A ``stop()`` that lands while this function is
       mid-scan can make shutdown take longer than
       ``SHUTDOWN_JOIN_TIMEOUT_SECONDS`` (30 s) and log the "did not stop
       within 30s" warning during e.g. ``docker compose down`` — harmless
       (a read-only walk left to finish on its own, nothing corrupted, no
       lock held), but it will read as a fault in the log if this isn't
       said up front.

    Candidate app ids come from ``depot_app_map`` directly (inside the shared
    helper), the same source the pre-WP-4f version used — NOT from the
    ``apps`` table. A mapping row with no corresponding ``apps`` row is not
    rejected by any foreign-key constraint (verified), so this can enqueue an
    appid the library UI never shows a card for. Narrow, documented
    behaviour, not a new hole: the installed-based half of this same sweep
    has always been able to target an appid with no ``apps`` row either, via
    a bare agent report.
    """
    depot_bytes = sizes.scan_depot_dir_bytes(cache_root)
    if not depot_bytes:  # empty/missing cache — no error, just nothing found
        return set()
    return deletion.appids_with_cache_content(conn, depot_bytes)


def compute_targets(
    conn: sqlite3.Connection,
    now: datetime,
    stale_after_days: int,
    *,
    include_cached: bool = False,
    cache_root: str | None = None,
) -> TargetSet:
    """Union the app ids from every client's LATEST agent report, optionally
    widened with every app that has cache content on disk (WP 4d).

    "Latest" is ``agent_reports.latest_snapshot`` — newest by **rowid**, i.e.
    insertion order, not by ``reported_at`` (WP 2.4's rule: second-precision
    timestamps tie, and a clock stepped backwards would reorder the chain).
    Reusing that function rather than writing a second query keeps the rule in
    exactly one place.

    A client is EXCLUDED when:

    * its newest report is older than ``stale_after_days`` (see
      ``config.DEFAULT_SCHEDULE_CLIENT_STALE_DAYS`` for why a silent machine
      should stop driving downloads),
    * that report's ``appids`` JSON is unreadable (corrupt row — the same
      degradation ``POST /v1/agent/installed`` already applies), or
    * its ``reported_at`` is not a parseable timestamp, which makes the
      staleness question unanswerable. Excluded rather than assumed fresh:
      the fail-safe direction here is "prefill less", never "prefill on the
      strength of a value we could not read".

    Exclusions are returned (and logged by the caller), never silently
    dropped — "the scheduler stopped covering my Steam Deck" must be
    diagnosable from ``GET /v1/schedule`` plus the log, not from reading this
    source file.

    Through Phase 3, intersected with nothing else: plan A8 made the
    installed list the whole criterion. ``include_cached`` — this keyword
    parameter, which defaults to ``False`` here and is UNCHANGED by
    ADR-0014 (a deliberate, separate, defensive default for direct/test
    callers that bypass ``Settings`` entirely) — is the one exception, and
    it is deliberately ADDITIVE rather than a replacement. The production
    caller (``maybe_sweep`` below) always passes
    ``include_cached=settings.sweep_include_cached`` explicitly, and THAT
    field's own default is ``True`` since WP SWEEP-1 / ADR-0014
    (2026-08-22) — see ``config.DEFAULT_SWEEP_INCLUDE_CACHED``. Do not
    conflate the two: this keyword staying ``False`` is what keeps
    "everything above this line is unchanged when the mode is off" testable
    at all (see the BYTE-IDENTICAL note two bullets below), not a claim
    about what the shipped scheduler actually runs with.

    * it is computed **independently** of the client-freshness logic above —
      a cached app is swept even if the client that installed it is
      ``stale``-excluded, or if no client has ever reported it at all (an
      orphaned-by-agents-but-still-cached game). That is the exact case this
      mode exists for (plan §7 Phase 4d): "or whose PC has been quiet longer
      than VAULT_SCHEDULE_CLIENT_STALE_DAYS — is never refreshed and
      silently rots";
    * with ``include_cached=False`` (the default), this function's behaviour
      — including its return type and every existing caller's result — is
      BYTE-IDENTICAL to the pre-WP-4d implementation: the cached-apps branch
      below is skipped entirely, nothing about the client loop above changes.

    ``cache_root`` has NO usable default (reviewer should-fix S1, 2026-08-18
    review round): a blank/missing value used to mean "scanned an empty
    directory, found nothing" — indistinguishable from "the cache genuinely
    has nothing cached", which is exactly the shape that once masked one of
    this module's own mutation-kill tests during review. ``include_cached=True``
    with no real ``cache_root`` now raises ``ValueError`` immediately rather
    than silently returning an empty cached set — a caller (production or
    test) that forgot to pass it gets a loud failure, not a quiet no-op.
    """
    fresh, excluded = fresh_client_snapshots(conn, now, stale_after_days)

    appids: set[int] = set()
    included: list[str] = []
    for snapshot in fresh:
        included.append(snapshot.client_id)
        appids.update(snapshot.appids)

    cached_only: tuple[int, ...] = ()
    if include_cached:
        # N4 (reviewer nitpick, 2026-08-18 review round, WP 4f): `.strip()`
        # too, not just falsy -- `config.py`'s own boot-time guard (WP 4f)
        # rejects a present-but-BLANK VAULT_CACHE_ROOT the same way, and a
        # direct call here (bypassing that boot check, as tests do) with a
        # whitespace-only value must not silently pass this assertion and
        # then scan nothing, the exact "indistinguishable from a genuinely
        # empty cache" failure mode S1 already closed for the empty-string
        # case.
        if not cache_root or not cache_root.strip():
            # S1: a falsy cache_root used to mean "silently scan nothing" --
            # indistinguishable from a genuinely empty cache. Loud failure
            # instead (see this function's docstring).
            raise ValueError(
                "compute_targets(include_cached=True) requires a real "
                f"cache_root, got {cache_root!r}. Pass settings.cache_root "
                "explicitly -- a missing/blank value must not silently "
                "resolve to 'nothing is cached'."
            )
        cached = cached_appids(conn, cache_root)
        cached_only = tuple(sorted(cached - appids))
        appids.update(cached)

    return TargetSet(
        appids=sorted(appids),
        included_clients=included,
        excluded_clients=excluded,
        cached_only_appids=cached_only,
    )


# --------------------------------------------------------------------------
# The sweep
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SweepResult:
    """Outcome of one tick. ``swept`` is False when nothing was due."""

    swept: bool
    #: Why not, when ``swept`` is False: ``"disabled"``, ``"outside-window"``,
    #: ``"interval-not-elapsed"``. ``""`` when a sweep did run.
    skipped_reason: str = ""
    targets: tuple[int, ...] = ()
    #: Apps a NEW job was created for.
    enqueued: tuple[int, ...] = ()
    #: Apps that already had a queued/running job (the queue's own dedupe).
    already_active: tuple[int, ...] = ()
    excluded_clients: tuple[ExcludedClient, ...] = ()
    #: True when shutdown interrupted the enqueue loop.
    aborted: bool = False
    swept_at: str | None = None
    #: WP 4d. Mirrors ``TargetSet.cached_only_appids`` — apps in ``targets``
    #: ONLY because ``sweep_include_cached`` was on, i.e. not reachable
    #: through any client's installed list this sweep. ``()`` when the mode
    #: is off (default-constructed, untouched) or when it added nothing new.
    cached_only_appids: tuple[int, ...] = ()


def _never() -> bool:
    return False


def maybe_sweep(
    conn: sqlite3.Connection,
    settings: Settings,
    now: datetime,
    should_abort: Callable[[], bool] = _never,
) -> SweepResult:
    """One scheduler tick: decide, and sweep if due.

    Order of gates — cheapest and most decisive first:

    1. no window configured -> the scheduler is off (nothing here ever runs);
    2. ``now`` outside the window -> not our time of day;
    3. the interval has not elapsed since the last sweep STARTED -> too soon
       (this is also the crash-recovery gate, see ``claim_sweep``).

    Only then is the target set computed and the queue written to. Splitting
    this out of the thread makes every decision testable by passing a
    datetime, with no clock and no waiting.
    """
    window = settings.schedule_window
    if window is None:
        return SweepResult(swept=False, skipped_reason="disabled")
    if not window.contains(now):
        return SweepResult(swept=False, skipped_reason="outside-window")
    if not claim_sweep(conn, now, settings.schedule_interval_minutes):
        return SweepResult(swept=False, skipped_reason="interval-not-elapsed")

    swept_at = to_utc_iso(now)
    target_set = compute_targets(
        conn,
        now,
        settings.schedule_client_stale_days,
        include_cached=settings.sweep_include_cached,
        cache_root=settings.cache_root,
    )

    enqueued: list[int] = []
    already_active: list[int] = []
    aborted = False
    for appid in target_set.appids:
        if should_abort():
            # Shutdown mid-sweep. The remaining apps are simply not enqueued;
            # nothing is left half-done, because each enqueue is its own
            # committed transaction and the next sweep recomputes the whole
            # target set anyway.
            aborted = True
            break
        _job, created = jobs.enqueue_prefill(conn, appid)
        (enqueued if created else already_active).append(appid)

    finish_sweep(conn, targets=len(target_set.appids), enqueued=len(enqueued))

    logger.info(
        "scheduler: sweep at %s enqueued %d new job(s), %d app(s) already "
        "queued/running, from %d target app(s) across %d client(s)%s%s%s",
        swept_at, len(enqueued), len(already_active), len(target_set.appids),
        len(target_set.included_clients),
        (
            " (EXCLUDED clients: "
            + ", ".join(
                f"{c.client_id}={c.reason}" for c in target_set.excluded_clients
            )
            + ")"
            if target_set.excluded_clients
            else ""
        ),
        (
            # S1 (reviewer should-fix, 2026-08-18 review round): this fragment
            # must appear whenever the mode is ON, INCLUDING the zero case --
            # gating it on "cached_only_appids is non-empty" made a live-but-
            # inert mode (missing cache volume, empty depot_app_map, a typo'd
            # root that still resolves to a real-but-wrong directory) produce
            # a log line byte-identical to the mode being off, while
            # GET /v1/schedule kept reporting sweep_include_cached: true.
            # Naming the scanned root turns "why did nothing get added" into
            # a one-line diagnosis instead of a silent non-event.
            f" (cached-apps sweep mode ON, root={settings.cache_root!r}: "
            f"{len(target_set.cached_only_appids)} target(s) added that were "
            "not already installed)"
            if settings.sweep_include_cached
            else ""
        ),
        " [ABORTED by shutdown]" if aborted else "",
    )

    return SweepResult(
        swept=True,
        targets=tuple(target_set.appids),
        enqueued=tuple(enqueued),
        already_active=tuple(already_active),
        excluded_clients=tuple(target_set.excluded_clients),
        aborted=aborted,
        swept_at=swept_at,
        cached_only_appids=target_set.cached_only_appids,
    )


def next_eligible_at(
    settings: Settings, state: ScheduleState, now: datetime
) -> str | None:
    """Earliest UTC instant at which a sweep could run. ``None`` if disabled.

    Both gates, in order: the interval (from the last sweep's start), then the
    window. ``now`` itself when a sweep is due right now.

    An **estimate**, not a promise — it is computed from the current clock and
    can be an hour out across a DST transition (see
    ``schedule_window.next_open``). Nothing decides anything from it; the
    thread re-evaluates the real gates every minute.
    """
    window = settings.schedule_window
    if window is None:
        return None

    now_utc = now.astimezone(timezone.utc)
    earliest = now_utc
    if state.last_sweep_at is not None:
        last = parse_utc_iso(state.last_sweep_at)
        if last is not None:
            earliest = max(
                now_utc, last + timedelta(minutes=settings.schedule_interval_minutes)
            )

    # Back into the local frame `now` carries, because the window is a
    # local-wall-clock concept.
    candidate_local = earliest.astimezone(now.tzinfo)
    return to_utc_iso(next_open(window, candidate_local))


def format_utc_offset(moment: datetime) -> str:
    """``UTC+02:00`` for ``moment``'s own UTC offset.

    The one shared implementation — ``routers/schedule.py``'s
    ``GET /v1/schedule`` (``server_timezone``) and
    ``describe_resolved_schedule`` below (the startup log line, WP SWEEP-1
    follow-up) both call this rather than each formatting an offset their
    own way, which is exactly the "two call sites computing the same domain
    predicate WILL diverge" shape docs/LEARNINGS.md already names.
    """
    offset = moment.utcoffset()
    if offset is None:  # pragma: no cover - every caller here passes an aware moment
        return "UTC"
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    total_minutes = abs(total_minutes)
    return f"UTC{sign}{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def describe_resolved_schedule(settings: Settings, now: datetime) -> str:
    """One human-readable line naming the resolved window, timezone, and next
    opening — logged once at scheduler startup (WP SWEEP-1 follow-up,
    ADR-0014 §"Shipping an enabled nightly schedule").

    **Why this exists at all.** ``VAULT_SCHEDULE_WINDOW`` is interpreted on
    the container's own local wall clock, and a container's "local" time is
    exactly whatever ``TZ`` resolves to — ``UTC`` if unset, which this
    project ships as the honest, zone-agnostic default (deploy/compose.yaml's
    own comment on ``TZ`` has the full argument against guessing a populated
    zone). An operator who ships the default window without also setting
    ``TZ`` gets a window that opens at a UTC hour, not their own local hour,
    and nothing before this function made that fact visible anywhere except
    by reading source. This line is the fix: it names the resolved zone and
    offset, and the next opening in BOTH local and UTC time, so a mismatch is
    something an operator NOTICES in the log rather than a silent surprise at
    3 AM UTC.

    **The requested TZ is named alongside the resolved one (review round 2,
    blocker R2-B2a, measured in the real image).** A typo'd IANA name is the
    single most likely operator error with a brand-new variable, and glibc
    does NOT fail loudly on one: ``TZ=Europe/Berlinn`` (one extra letter)
    falls back to a POSIX-style parse that silently resolves to a
    plausible-LOOKING but wrong zone — measured directly, it resolves to a
    zone named ``Europe`` at ``UTC+00:00``, not an error, not "Berlinn", and
    not the intended Berlin offset either. Printing only the resolved side
    (as an earlier version of this function did) would have shown
    "resolved against Europe (UTC+00:00)" — plausible enough to not raise an
    eyebrow. Printing the REQUESTED value (``os.environ.get("TZ")``)
    alongside it is deliberately the fix that needs no timezone-database
    lookup of its own: the mismatch between what was typed and what it
    resolved to tells the truth on its own, and it is what makes this
    monkeypatch-testable (see ``test_scheduler.py``) without needing a
    container with a broken zone actually running.

    Pure (no I/O beyond the one ``os.environ.get`` read, no clock of its
    own, no thread) so it is unit-testable without starting the scheduler
    thread — ``_tick`` below is the only production caller (moved there
    from ``start()``, review round 2 blocker R2-B2b — see
    ``PrefillScheduler._logged_resolved_schedule``'s own comment for why).
    Deliberately uses ``schedule_window.next_open`` directly against ``now``
    rather than ``next_eligible_at`` above: this is a boot-time sanity
    display, not a promise, and does not need (or want) the
    interval-since-last-sweep gate ``next_eligible_at`` layers on top — at
    boot there usually is no meaningful "last sweep" yet, and this line's
    only job is "does today's window/timezone make sense", not "when
    exactly will the next sweep fire".
    """
    window = settings.schedule_window
    offset_str = format_utc_offset(now)
    zone_name = now.tzname() or "an unnamed zone"
    requested_tz = os.environ.get("TZ")
    tz_clause = (
        f"TZ={requested_tz!r} resolved to {zone_name} ({offset_str})"
        if requested_tz is not None
        else f"TZ unset, resolved to {zone_name} ({offset_str})"
    )
    if window is None:
        return (
            f"scheduler: no VAULT_SCHEDULE_WINDOW configured -- the "
            f"scheduler is DISABLED, no automatic sweep will run as of this "
            f"tick's resolved settings. {tz_clause}. Auto-GC still applies "
            f"immediately to manual and miss-triggered prefills regardless "
            f"of this setting -- see api/README.md 'Auto-GC'."
        )
    next_local = next_open(window, now)
    next_utc = next_local.astimezone(timezone.utc)
    # N2 (should-fix, review round 2): `next_open` returns `now` UNCHANGED
    # when the window is already open (its own docstring) -- worded plainly
    # as "next opening <now>" that would technically be correct (the window
    # IS open, right now counts as an opening) but reads oddly on a boot
    # that happens to land inside the window. Special-cased rather than left
    # to be misread as a bug in the log.
    opening_clause = (
        f"the window is OPEN right now ({next_local.isoformat()} local = "
        f"{next_utc.isoformat()} UTC)"
        if window.contains(now)
        else (
            f"next opening {next_local.isoformat()} local = "
            f"{next_utc.isoformat()} UTC"
        )
    )
    return (
        f"scheduler: window {window.normalized} in effect as of this "
        f"tick's resolved settings -- {tz_clause} -- {opening_clause}. If "
        f"the requested/resolved TZ above don't match what you expect, "
        f"check for a typo (see deploy/.env.example)."
    )


# --------------------------------------------------------------------------
# WP 4d — the auto-GC coupling, stated honestly
# --------------------------------------------------------------------------


def cached_sweep_gc_risk(settings: Settings) -> bool:
    """True iff the cached-apps sweep mode is on while auto-GC is NOT
    ``execute`` — i.e. ``dry-run`` counts as risky too (B2, user decision
    "nothing is being reclaimed", 2026-08-18 review round).

    Named and exported as its own function — rather than inlined at each of
    its two call sites — so ``PrefillScheduler``'s one-time log warning and
    ``GET /v1/schedule``'s ``sweep_cached_gc_risk`` field can never quietly
    disagree about what "at risk" means: one boolean expression, computed
    once, read twice.

    **Why this is a risk worth naming (plan §7 Phase 4d):** every kept-current
    game the cached-apps mode refreshes adds fresh chunks for the new manifest
    while the old manifest's chunks become orphans — that is what a game
    *update* is, on disk. A vault that keeps itself current without
    collecting garbage keeps itself current straight into a full disk.

    **Why ``dry-run`` does not clear the risk.** The risk this flag names is
    "orphans are accumulating, unreclaimed" — a fact about the DISK, not
    about whether anyone is watching. ``VAULT_AUTO_GC=dry-run`` queues a
    REPORTING-only GC job after every updating prefill: it tells the operator
    exactly what could be freed, and frees NOTHING (see ``worker.py``'s
    ``_maybe_queue_auto_gc`` / api/README.md "Auto-GC"). A vault swept in
    ``dry-run`` mode grows at IDENTICAL speed to one with auto-GC fully off —
    the only difference is a log line describing the growth on the way past.
    Reporting the condition as resolved once dry-run is on would be exactly
    the "doc sentence telling an operator X works when it does not" class
    docs/LEARNINGS.md warns about, just moved from a doc sentence into a
    boolean API field. Only ``execute`` (``settings.auto_gc_executes``)
    actually reclaims, so only ``execute`` clears this flag.

    This function does not act on the fact it names (WP 4d's brief is
    explicit: "do not silently enable auto-GC, and do not refuse to run — the
    operator decides"); it only names the condition so something else can
    warn.
    """
    return settings.sweep_include_cached and not settings.auto_gc_executes


def warn_once_if_cached_sweep_without_gc(settings: Settings, already_warned: bool) -> bool:
    """Log the auto-GC coupling warning on a transition INTO the risky state,
    and a matching all-clear ``INFO`` line on the transition back OUT of it.

    ``already_warned`` is the caller's own memory of the last decision (an
    instance attribute on ``PrefillScheduler`` in production); this function
    is pure so the transition logic is unit-testable without a thread or a
    clock. Returns the new value for the caller to store.

    Fires **once per transition**, not once per tick and not once per
    process — the same "state changes in both directions" shape WP 3.13's
    bypass-detection transitions use (docs/LEARNINGS.md "Testing discipline"):
    if the operator fixes it (sets ``VAULT_AUTO_GC=execute``, or turns the
    cached mode back off) and later re-creates the same risky combination,
    that is worth a fresh warning, not silence because a boolean was set once
    at boot. The all-clear is an ``INFO``, not a second ``WARNING`` — without
    it, "why did that warning stop appearing?" was unanswerable from the log
    alone (reviewer nitpick N1): the state IS persisted correctly in both
    directions, but silently is not the same as visibly.
    """
    risk = cached_sweep_gc_risk(settings)
    if risk and not already_warned:
        logger.warning(
            "scheduler: VAULT_SWEEP_INCLUDE_CACHED is on while VAULT_AUTO_GC "
            "is %r, not 'execute'. Every kept-current game this sweep mode "
            "refreshes adds fresh chunks for the new manifest while the "
            "superseded ones become orphans -- a vault that keeps itself "
            "current without ACTUALLY collecting garbage keeps itself "
            "current straight into a full disk. 'dry-run' only REPORTS what "
            "could be reclaimed, it reclaims nothing -- this warning stays "
            "active in dry-run mode for exactly that reason. This is not a "
            "failure and nothing is refused: set VAULT_AUTO_GC=execute to "
            "actually close the loop, or run POST /v1/cache/{appid}/gc "
            "yourself. GET /v1/schedule also reports this condition as "
            "'sweep_cached_gc_risk' for a UI to surface. This warning is "
            "logged once per time the condition becomes true, not on every "
            "sweep.",
            settings.auto_gc,
        )
        return True
    if not risk:
        if already_warned:
            logger.info(
                "scheduler: the VAULT_SWEEP_INCLUDE_CACHED / VAULT_AUTO_GC "
                "risk condition cleared (auto_gc=%r now actually reclaims, "
                "or the cached-apps sweep mode was turned back off) -- the "
                "previous WARNING no longer applies.",
                settings.auto_gc,
            )
        return False
    return already_warned


# --------------------------------------------------------------------------
# The thread
# --------------------------------------------------------------------------


class PrefillScheduler:
    """The second background thread: ticks, and sweeps when due.

    Constructed unconditionally by ``main.create_app`` (so
    ``GET /v1/schedule`` has something to ask for the clock and the config,
    enabled or not) but only ``start()``ed by the lifespan when a window is
    configured. Constructing it starts nothing.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        clock: Clock = local_now,
        tick_seconds: float = DEFAULT_TICK_SECONDS,
        webhook_notifier: "webhooks.WebhookNotifier | None" = None,
    ) -> None:
        self._settings = settings
        self._clock = clock
        self._tick_seconds = tick_seconds
        #: WP 3.13: threaded through to event_sweep.maybe_sweep's persist step
        #: (client.bypass_suspected transitions). None in every existing
        #: caller/test that predates the webhook feature.
        self._webhook_notifier = webhook_notifier
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        #: WP 4d. This thread's own memory for
        #: ``warn_once_if_cached_sweep_without_gc`` — mutated only from
        #: ``_tick``, i.e. only ever touched by this one thread, same as
        #: every other plain attribute on this class.
        self._warned_cached_sweep_gc_risk = False
        #: WP SWEEP-1 follow-up (review round 2, blocker R2-B2b, measured
        #: both directions): this used to be logged once, synchronously,
        #: inside ``start()`` — against ``self._settings``, the boot-time
        #: snapshot. That is wrong whenever a DB override (ADR-0009,
        #: ``PATCH /v1/settings``) changes ``schedule_window`` relative to
        #: what booted: measured with a DB-stored window and no env window,
        #: the old line said "the scheduler is DISABLED, no automatic sweep
        #: will run" while an unattended nightly sweep with executing GC was
        #: in fact about to run on the very next tick — precisely backwards
        #: for the one line this project offers as the reason unattended
        #: deletion is acceptable (ADR-0014). Logged from ``_tick`` instead
        #: (below), against ``effective`` (the SAME db>env>default
        #: resolution every sweep decision already uses), on the first tick
        #: only — mutated only from ``_tick``, same as
        #: ``_warned_cached_sweep_gc_risk`` above.
        self._logged_resolved_schedule = False

    # -- accessors ---------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """Is the PREFILL scheduler on (i.e. is a window configured)?

        Unchanged meaning since WP 3.5 — ``GET /v1/schedule`` reports it. Note
        that this is no longer the same question as "does the thread need to
        run": WP 3.11's event sweep rides the same tick and is enabled
        separately. See ``thread_needed``.
        """
        return self._settings.scheduler_enabled

    @property
    def thread_needed(self) -> bool:
        """True when EITHER background job wanted ticks AT BOOT: the prefill
        sweep (``VAULT_SCHEDULE_WINDOW``) or the cache-event sweep
        (``VAULT_EVENT_LOG_PATH``, WP 3.11).

        **No longer what decides whether the lifespan starts the thread**
        (settings-API work package, ADR-0009, reviewer blocker B1) — it once
        did, but that made the thread's existence depend on the boot
        snapshot of ``Settings``, so a stock deployment with neither
        configured at boot could never run a sweep a later
        ``PATCH /v1/settings`` enabled (``schedule_window`` is
        ``applies: "next_sweep"``, which is a promise this class can only
        keep if the thread already exists to tick on). ``main.py`` now calls
        ``start()`` unconditionally; this property survives purely as a
        boot-time diagnostic (used by ``vault_api/routers/schedule.py``-style
        logging and by ``tests/test_event_sweep.py``'s wiring test), not as a
        gate.
        """
        return self._settings.scheduler_enabled or self._settings.event_sweep_enabled

    @property
    def settings(self) -> Settings:
        return self._settings

    def now(self) -> datetime:
        """Current time per this scheduler's clock (aware, server-local).

        Exposed so ``GET /v1/schedule`` reports ``next_eligible_at`` on the
        same clock the sweeps use — including an injected one in tests.
        """
        return self._clock()

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:  # pragma: no cover - guarded by lifespan
            raise RuntimeError("scheduler already started")
        # WP SWEEP-1 follow-up (ADR-0014 §"Shipping an enabled nightly
        # schedule"): the resolved-schedule line used to be logged HERE,
        # synchronously, against ``self._settings`` (the boot snapshot).
        # Moved to ``_tick``'s first call instead (review round 2, blocker
        # R2-B2b) — see ``self._logged_resolved_schedule``'s own comment in
        # ``__init__`` for the measured failure mode that motivated the
        # move. The first tick fires immediately once the thread starts
        # (see the "tick immediately, then wait" comment in ``_run`` below),
        # so this is not a behaviour change in WHEN the line appears, only
        # in WHICH settings it is computed against.
        # daemon=True for the same reason as the worker: a hard shutdown must
        # never be able to wedge the interpreter.
        self._thread = threading.Thread(
            target=self._run, name="vault-prefill-scheduler", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = SHUTDOWN_JOIN_TIMEOUT_SECONDS) -> None:
        """Signal and join. A no-op when the scheduler was never started."""
        self._stop.set()
        thread = self._thread
        if thread is None:
            return
        thread.join(timeout=timeout)
        if thread.is_alive():  # pragma: no cover - a wedged DB write only
            logger.warning(
                "Scheduler thread did not stop within %.0fs; leaving it as a "
                "daemon thread.",
                timeout,
            )

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    # -- loop --------------------------------------------------------------

    def _run(self) -> None:
        conn = get_connection(self._settings.db_path)
        try:
            # Tick immediately, then wait: a restart inside the window with the
            # interval already elapsed should act now, not sit out a tick. The
            # interval gate in claim_sweep is what stops that from becoming a
            # restart-driven sweep storm.
            while True:
                self._tick(conn)
                if self._stop.wait(self._tick_seconds):
                    return
        finally:
            conn.close()

    def _tick(self, conn: sqlite3.Connection) -> None:
        """One decision. Never raises — a bug here must not kill the thread.

        Same last-resort net as the worker's job body: if this thread dies,
        nothing restarts it and the scheduler silently stops scheduling, which
        is far worse than a logged traceback and a retry in a minute.

        Two independent jobs ride this tick, each with its OWN ``try`` (WP
        3.11): a failing cache-event sweep must not stop prefill scheduling,
        and a failing prefill sweep must not stop the event sweep — which owns
        the event log's rotation, so silencing it would eventually fill a disk.
        The event sweep goes first because it is the cheap one and because a
        miss it discovers can enqueue work the prefill sweep would then see.
        """
        now = self._clock()

        # Settings-API work package (ADR-0009 decision 6): resolve DB
        # overrides > env > default ONCE per tick, using the connection this
        # tick already opened, and hand the result to both sweeps below
        # instead of self._settings directly. This is what makes a
        # PATCH /v1/settings change to the schedule window/interval/
        # staleness keys apply "next_sweep" (documented in
        # vault_api/settings_store.py) rather than requiring a restart —
        # every OTHER field on the returned Settings is identical to
        # self._settings (effective_settings only swaps in overridden
        # fields), so this changes nothing for event_sweep's own settings.
        # Wrapped in its own try (this method's whole contract is "never
        # raises" — a broken connection resolving overrides must not be a
        # new way to violate that): falls back to self._settings, i.e. no
        # override applied this tick, same as before this work package.
        try:
            effective = settings_store.effective_settings(conn, self._settings)
        except Exception:
            logger.exception(
                "Could not resolve settings overrides for this tick; "
                "proceeding with the env/default configuration (no override "
                "applied) for one cycle."
            )
            effective = self._settings

        # WP SWEEP-1 follow-up (review round 2, blocker R2-B2b): the
        # resolved-schedule line, logged against ``effective`` (DB-resolved,
        # not the boot snapshot) on the FIRST tick only — see
        # ``self._logged_resolved_schedule``'s own comment in ``__init__``
        # for why this moved here from ``start()``. Its own try, same
        # "never raises" contract as every other block in this method.
        if not self._logged_resolved_schedule:
            try:
                logger.info("%s", describe_resolved_schedule(effective, now))
            except Exception:
                logger.exception(
                    "Could not log the resolved-schedule line this tick; "
                    "not fatal, retried next tick."
                )
            else:
                self._logged_resolved_schedule = True

        # WP 4d: a config-risk warning, not a sweep — evaluated every tick
        # (cheap, no I/O) regardless of whether a sweep is actually due this
        # minute, so the operator hears about it as soon as the combination
        # exists rather than only once a sweep happens to run. Its own try:
        # this method's contract is "never raises", and a warning must not
        # become a new way to violate that.
        try:
            self._warned_cached_sweep_gc_risk = warn_once_if_cached_sweep_without_gc(
                effective, self._warned_cached_sweep_gc_risk
            )
        except Exception:
            logger.exception(
                "Could not evaluate the sweep/auto-GC risk warning this tick; "
                "not fatal, retried next tick."
            )

        try:
            event_sweep.maybe_sweep(conn, effective, now, self._webhook_notifier)
        except Exception:
            logger.exception(
                "Cache-event sweep failed (WP 3.11). The thread survives and "
                "retries in %.0fs. The event-log cursor only advances inside "
                "the transaction that commits a batch's effects, so nothing "
                "was half-applied and no line was consumed -- the next sweep "
                "re-reads from the last committed offset.",
                self._tick_seconds,
            )

        try:
            maybe_sweep(
                conn, effective, now, should_abort=self._stop.is_set
            )
        except Exception:
            logger.exception(
                "Scheduler tick failed. The thread survives and ticks again "
                "in %.0fs. If the failure happened AFTER the sweep was "
                "claimed, last_sweep_at is already stamped and its counters "
                "stay NULL, so the next sweep is one full interval away — "
                "apps it did not reach are picked up then (the target set is "
                "recomputed from scratch, never resumed).",
                self._tick_seconds,
            )
