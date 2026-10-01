"""Job queue (plan §3: "prefill orchestration ... job queue", one job at a time).

Deliberately a table plus a handful of SQL statements — plan §9's simplicity
stance rules out Celery/RQ/APScheduler for a single-container homelab service
that runs at most one prefill at a time.

Also holds the ``apps.status`` transitions (idle -> running -> done/error),
because the job lifecycle is exactly what drives them (plan §3 "per-app status
tracking").

Concurrency model
-----------------
- N HTTP request threads may enqueue and read.
- Exactly ONE worker thread claims and executes.
- Every read-modify-write here (dedupe check + insert, claim, and every WP 3.12
  job-control transition) runs inside an ``BEGIN IMMEDIATE`` transaction, which
  takes SQLite's write lock up front, so two racing enqueues of the same appid
  cannot both insert, two claim attempts cannot both take the same job, and a
  cancel cannot slip past a claim. Readers are unaffected (WAL).

The job status model (WP 3.12) — and the audit that fixed its shape
-------------------------------------------------------------------
Before WP 3.12 a job was ``queued`` -> ``running`` -> ``done`` | ``error``.
Job control adds two states, and the shape below is the result of auditing
**every existing consumer of ``jobs.status``** rather than of picking names:

* ``cancelled`` — TERMINAL, deliberately distinct from ``error``. An operator
  stopping a job is not a failure, and squeezing it into ``error`` would make
  the one status that means "something went wrong" unreadable (the scheduler,
  the app's badges and any future alerting all key on it). It behaves exactly
  like ``done``/``error`` for every consumer: not claimable, not active, does
  not block dedupe or ``DELETE /v1/cache/{appid}``.
* ``paused`` — NON-terminal. The job has no subprocess (pause *terminates*
  SteamPrefill — see ``vault_api/worker.py``), holds no worker slot, and is
  waiting for ``POST /v1/jobs/{id}/resume``, which puts it back to ``queued``.

**The audit, consumer by consumer** (each line is pinned by a test in
``tests/test_job_control.py``):

1. ``ACTIVE_STATUSES`` — the shared "in flight" definition. ``paused`` is IN
   it; ``cancelled`` is NOT. The decisive consumer is ``deletion``'s
   ``_has_cache_content`` (ADR-0003 addendum): a paused prefill has, by
   construction, written chunks to disk (that is the entire premise of
   pause — the cache IS the progress store), so the app must count as
   "has content" or a co-owner's deletion could take a last-remnant shared
   depot out from under it. Fail-closed, so ``paused`` is active.
2. ``enqueue_prefill`` / ``enqueue_gc`` dedupe — follows (1). A second
   ``POST /v1/prefill`` for an app with a paused job returns THAT job with
   ``deduplicated: true`` rather than stacking a rival job that would download
   the same content the paused one is holding progress for. Honest consequence,
   documented in api/README.md: a paused job an operator forgets about keeps
   deduping. The escape hatch is ``DELETE /v1/jobs/{id}``, which cancels a
   paused job immediately.
3. ``active_job_for_app`` -> ``DELETE /v1/cache/{appid}`` 409 — follows (1)
   too, and the reason is stronger than "consistency": deleting the depots of a
   paused prefill destroys exactly the partially-downloaded content the pause
   exists to preserve. Cancel the job first, then delete.
4. ``claim_next_job`` — claims ``queued`` only, unchanged. ``paused`` is not
   claimable (that is what makes resume an explicit act) and ``cancelled`` is
   terminal.
5. ``recover_stale_jobs`` — recovers ``running`` only, unchanged, and that is
   load-bearing: a ``paused`` row MUST survive a restart and stay resumable.
   Widening it to include ``paused`` would silently eat every paused job on
   every container restart (mutation-tested).
6. ``clear_needs_force_if_unchanged`` (ADR-0006 decision 2) — keyed on
   OUTCOMES, not statuses: only ``worker.py``'s one successful branch calls it.
   Cancel and pause never reach it, so a cancelled/paused run leaves
   ``needs_force`` exactly as the deletion path (or the schema default) set it,
   which is correct — the run never completed, so the reason it was forced
   still holds.

``apps.status`` gains NO new values (it stays idle/running/done/error, plan
§3). Cancel and pause reset it from ``running`` to ``idle`` via a conditional
UPDATE, because nothing is running any more; the JOB row is the authority on
"there is unfinished work here". See ``reset_app_status_if_running``.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator, Mapping

#: The job type WP 1.4 creates. ``jobs.type`` exists because plan §6 also
#: lists a GC job (``POST /v1/cache/{appid}/gc``) sharing this queue.
JOB_TYPE_PREFILL = "prefill"

#: Garbage collection (WP 3.8, ADR-0007). Shares this queue *on purpose*: the
#: single worker then serializes GC against prefills, so chunks are never
#: unlinked while SteamPrefill is downloading into the same depot — the same
#: hazard ``DELETE /v1/cache/{appid}`` has to refuse with a 409, avoided here
#: structurally rather than by a check-then-act guard.
#:
#: The dry-run/execute distinction is NOT encoded in this string (no
#: ``"gc_execute"`` type): ``type`` names *what work* a job is, and one kind of
#: work with two modes is what ``jobs.gc_execute`` (schema v7) is for. Two
#: types would also have quietly changed what every existing ``type = ?``
#: query means.
JOB_TYPE_GC = "gc"

#: ``apps.status`` only (never a job status): the schema default, and what
#: deleting a game from the cache resets an app to (plan §4, WP 1.6).
STATUS_IDLE = "idle"

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_ERROR = "error"

#: WP 3.12. Non-terminal: the job's subprocess was terminated on operator
#: request and the job is parked until ``POST /v1/jobs/{id}/resume``. Job
#: statuses only — never an ``apps.status`` value.
STATUS_PAUSED = "paused"

#: WP 3.12. Terminal, and deliberately NOT ``error`` — see the module
#: docstring's status-model section.
STATUS_CANCELLED = "cancelled"

#: A job in one of these states is "in flight": for dedupe, for
#: ``DELETE /v1/cache/{appid}``'s 409 and for ADR-0003's has-content rule.
#: ``paused`` is included on purpose (module docstring, audit item 1).
ACTIVE_STATUSES = (STATUS_QUEUED, STATUS_RUNNING, STATUS_PAUSED)

#: A job in one of these states has an outcome and will never run again.
#: Cancelling one is a 409 — an outcome is history, not something to rewrite.
TERMINAL_STATUSES = (STATUS_DONE, STATUS_ERROR, STATUS_CANCELLED)

#: Placeholders for ``status IN (...)`` over ``ACTIVE_STATUSES`` — keeps the
#: count and the bound values trivially in sync (same device as
#: ``deletion._ACTIVE_JOB_PLACEHOLDERS``). Before WP 3.12 three queries in this
#: module spelled ``IN (?, ?)`` with two inlined constants; adding a third
#: active status is exactly the kind of change that silently misses one of
#: them, so there is now one definition.
_ACTIVE_PLACEHOLDERS = ",".join("?" for _ in ACTIVE_STATUSES)

#: WP 3.12. Values of ``jobs.stop_request`` (schema v8): what the operator
#: asked for while the job was RUNNING. ``NULL`` = nothing pending.
#:
#: This is a database column rather than an in-process event on purpose. The
#: request arrives on an HTTP thread and has to reach the worker thread, which
#: is inside ``subprocess`` polling; a DB flag needs no wiring between the two
#: (the worker already holds a connection), is visible to an operator with
#: ``sqlite3``, and — unlike an in-memory event — cannot be lost by the request
#: and worker disagreeing about which job id is current. The worker re-reads it
#: on its existing 0.2 s subprocess poll (one primary-key SELECT, no lock, WAL
#: readers never block) and GC re-reads it between depots.
STOP_REQUEST_CANCEL = "cancel"
STOP_REQUEST_PAUSE = "pause"

#: Columns returned by the job read helpers. ``log_excerpt`` is intentionally
#: excluded from the *list* query (see ``list_jobs``). ``updated``/
#: ``up_to_date``/``summary_parse_ok`` (schema v4, WP 3.3) are SteamPrefill's
#: own summary-table counters (ADR-0006 decision 1) — small scalars, so unlike
#: ``log_excerpt`` they ARE included in the list query too.
#: ``gc_execute`` (schema v7, WP 3.8) is included everywhere — list included —
#: for the same reason as those three: it is a single small scalar, and it is
#: the one field that says whether a GC job deletes or only reports.
#: ``paused_at``/``stop_request`` (schema v8, WP 3.12) are included everywhere
#: for the same reason as ``gc_execute``: two small scalars that answer
#: "since when has this been paused?" and "is a cancel already on its way?" —
#: the two questions a polling UI otherwise has to guess at.
_JOB_COLUMNS = (
    "id, appid, type, status, created_at, started_at, finished_at, log_excerpt, "
    "updated, up_to_date, summary_parse_ok, gc_execute, paused_at, stop_request"
)

#: Cap on stored log excerpts. 4 KiB is enough to show the tail of a
#: SteamPrefill run (the interesting part: the summary or the exception) while
#: keeping ``GET /v1/jobs/{id}`` responses small enough for the Android app.
LOG_EXCERPT_MAX_CHARS = 4096


#: The one timestamp format in this database. Lives here, next to
#: ``utcnow_iso``, because three modules now render and parse it (the job
#: queue, the prefill scheduler, and WP 3.11's event sweep) and a second copy
#: of the format string is a second place for it to drift.
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def utcnow_iso() -> str:
    """Current UTC time as a second-precision ISO-8601 string ('...Z').

    All timestamps in this database are stored in this one format so plain
    string comparison sorts chronologically.
    """
    return datetime.now(timezone.utc).strftime(TIMESTAMP_FORMAT)


def to_utc_iso(moment: datetime) -> str:
    """Render an aware datetime in the project's UTC timestamp format.

    Moved here from ``scheduler`` in WP 3.11 so ``event_sweep`` can use it
    without importing ``scheduler`` — which imports ``event_sweep`` to run the
    sweep on its tick, and would therefore be a cycle. ``scheduler`` re-exports
    both this and ``parse_utc_iso`` under their original names.
    """
    return moment.astimezone(timezone.utc).strftime(TIMESTAMP_FORMAT)


def parse_utc_iso(text: str) -> datetime | None:
    """Parse a stored ``...Z`` timestamp; ``None`` if it is not one.

    Returning ``None`` rather than raising keeps a corrupt/hand-edited row from
    wedging a background thread forever — see how the callers degrade (they
    treat it as "no usable value" and say so in the log), the same pattern
    ``agent_reports._decode_appids`` uses for an unreadable snapshot.
    """
    try:
        return datetime.strptime(text, TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def tail_excerpt(text: str, limit: int = LOG_EXCERPT_MAX_CHARS) -> str:
    """Keep the LAST ``limit`` characters — the tail is where the outcome is.

    Prefixes a marker when truncation happened so a reader isn't misled into
    thinking the run started mid-sentence.
    """
    if len(text) <= limit:
        return text
    return "[...truncated...]\n" + text[-limit:]


@contextmanager
def immediate_transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """Run a block inside ``BEGIN IMMEDIATE`` (write lock taken up front).

    sqlite3's default ``isolation_level=""`` opens a *deferred* transaction
    lazily on the first DML statement, which is not enough here: a deferred
    transaction that starts with a SELECT can have another writer commit
    underneath it between the SELECT and the UPDATE (the classic
    check-then-act race). ``BEGIN IMMEDIATE`` acquires the write lock before
    the SELECT, so competing writers serialize on it (and wait up to
    ``PRAGMA busy_timeout``, set to 5 s in ``db.get_connection``).

    Taking manual control requires ``isolation_level=None``; it is restored
    afterwards so the connection behaves normally for other callers (e.g.
    ``mapping.upsert_mapping``, which relies on ``conn.commit()``).
    """
    prior = conn.isolation_level
    # Assigning isolation_level commits an implicitly-open transaction; commit
    # first so that is explicit rather than a side effect.
    conn.commit()
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    finally:
        conn.isolation_level = prior


def _row_to_dict(row: sqlite3.Row) -> dict[str, object]:
    return {key: row[key] for key in row.keys()}


def ensure_app_row(conn: sqlite3.Connection, appid: int) -> None:
    """Make sure an ``apps`` row exists for ``appid`` (status 'idle').

    Called on enqueue so ``GET /v1/games/{appid}`` answers 200 the moment a
    prefill has been requested, even for an app that has never been mapped
    before (first prefill of a new title). Never touches an existing row.
    """
    conn.execute(
        "INSERT OR IGNORE INTO apps (appid, status) VALUES (?, 'idle')", (appid,)
    )


def set_app_status(
    conn: sqlite3.Connection,
    appid: int,
    status: str,
    last_prefill_at: str | None = None,
    last_manifest_check: str | None = None,
) -> None:
    """Set ``apps.status`` (plan §3: idle/running/done/error).

    ``last_prefill_at`` is only written when given — a failed run must not
    claim the app was prefilled. ``last_manifest_check`` (WP 3.3, ADR-0006
    "current as of <timestamp>" semantics) is likewise only written when
    given — the worker passes it exactly when SteamPrefill's summary reported
    ``Up To Date > 0 AND Updated == 0`` for this run, i.e. a non-forced-run
    style confirmation that the app is current as of now.

    Deliberately has **no** ``needs_force`` parameter (schema v5, WP 3.4,
    ADR-0006 decision 2) — see ``clear_needs_force_if_unchanged`` below for
    why an unconditional write here would be wrong, and its own docstring for
    the concurrent-deletion bug an earlier version of this function had.
    """
    ensure_app_row(conn, appid)
    fields = ["status = ?"]
    params: list[object] = [status]
    if last_prefill_at is not None:
        fields.append("last_prefill_at = ?")
        params.append(last_prefill_at)
    if last_manifest_check is not None:
        fields.append("last_manifest_check = ?")
        params.append(last_manifest_check)
    params.append(appid)
    conn.execute(f"UPDATE apps SET {', '.join(fields)} WHERE appid = ?", params)
    conn.commit()


def get_app_needs_force(conn: sqlite3.Connection, appid: int) -> bool:
    """Read ``apps.needs_force`` (schema v5, WP 3.4, ADR-0006 decision 2).

    Read by the worker at job start to decide whether this run must pass
    ``--force`` (see ``vault_api/prefill.py::run_prefill``'s ``use_force``
    parameter). Defaults to ``True`` when the app has no row at all — matching
    the column's own ``DEFAULT 1`` semantics ("never filled before, so force
    the first run") — even though in practice ``worker.py`` always calls this
    right after ``set_app_status`` has already ensured the row exists via
    ``ensure_app_row``; the default keeps this function safe for any other
    caller too.

    The value this returns is also what the worker must hand back to
    ``clear_needs_force_if_unchanged`` at the end of the job — see that
    function for why.
    """
    row = conn.execute(
        "SELECT needs_force FROM apps WHERE appid = ?", (appid,)
    ).fetchone()
    return True if row is None else bool(row["needs_force"])


def clear_needs_force_if_unchanged(
    conn: sqlite3.Connection, appid: int, expected_needs_force: bool
) -> bool:
    """Clear ``apps.needs_force`` to 0 — but ONLY as a compare-and-swap against
    the value read at job-claim time. Returns whether the clear applied.

    **The bug this closes (reviewer-reproduced end-to-end wedge, WP 3.4
    review).** The worker used to clear ``needs_force`` unconditionally on
    every successful job — an unconditional ``UPDATE apps SET needs_force =
    0 WHERE appid = ?``. That is a last-writer-wins race against
    ``DELETE /v1/cache/{appid}``, which is allowed to run concurrently with a
    *different* job than the one it 409-refused (the 409 guard only checks
    for an active job at the START of the DELETE request — plan §4's
    documented check-then-act window, see ``routers/cache.py``). The
    reproduced sequence:

    1. A job for this app is claimed; it reads ``needs_force`` (say ``0`` —
       not currently forced) and runs a non-forced check. SteamPrefill's own
       ``successfullyDownloadedDepots.json`` still says the depots are
       present, so it reports "up to date" — correctly, *for what was on
       disk when it started*.
    2. Concurrently, ``DELETE /v1/cache/{appid}`` removes the depot
       directories and, at the end, sets ``needs_force = 1`` (this request
       genuinely changed what is on disk — see
       ``deletion.reset_app_after_deletion``).
    3. The job finishes "successfully" (an up-to-date confirmation is a
       'done' outcome, ADR-0006 decision 1) and unconditionally clears
       ``needs_force`` back to ``0`` — clobbering step 2's ``1``.

    End state: ``apps.status = 'done'``, ``last_prefill_at`` set, the cache
    directory **empty**, and ``needs_force = 0``. There is no self-healing
    path from there: every future run goes non-forced, SteamPrefill's own
    bookkeeping keeps saying "up to date" forever (it was never told the
    depots were deleted), and the app is permanently wedged at a green badge
    over an empty cache.

    **The fix.** The worker remembers the ``needs_force`` value it read at
    claim time (``jobs.get_app_needs_force``, the same value it passed to
    ``prefill.run_prefill`` as ``use_force``) and hands it back here as
    ``expected_needs_force``. The clear is then a single atomic SQL
    statement — ``UPDATE ... WHERE appid = ? AND needs_force = ?`` — matched
    against the table's *current* value, not a value this function read
    itself: SQLite evaluates the ``WHERE`` clause and applies the write in
    one indivisible step under the write lock, so there is no separate
    read-then-write window for another connection to land in between. If
    step 2 above happened, the current value is ``1`` but
    ``expected_needs_force`` is ``0`` (what the job saw at claim time) — the
    ``WHERE`` clause matches zero rows, the clear is a no-op, and
    ``needs_force`` is correctly left at ``1``: the very next prefill for
    this app is forced, which is exactly the self-healing path that was
    missing. If nothing raced the job, the current value still equals
    ``expected_needs_force`` and the clear applies normally.

    Returns ``True`` when the clear applied (no concurrent write raced it),
    ``False`` when it was skipped because something changed the value in the
    meantime (the caller may want to log this — see ``worker.py``).
    """
    cursor = conn.execute(
        "UPDATE apps SET needs_force = 0 WHERE appid = ? AND needs_force = ?",
        (appid, int(expected_needs_force)),
    )
    conn.commit()
    return cursor.rowcount > 0


def enqueue_prefill(conn: sqlite3.Connection, appid: int) -> tuple[dict[str, object], bool]:
    """Queue a prefill job for ``appid``. Returns ``(job, created)``.

    Dedupe: if the app already has a ``queued`` or ``running`` job, that job is
    returned with ``created=False`` instead of stacking a second one. Rationale
    — the app's UI fires "prefill this game" on a button press and the Phase-3
    miss trigger (ADR-0001) will fire on cache misses, so duplicate requests
    for the same app are the normal case, not an error; running the same
    prefill twice back to back would only re-download nothing.
    """
    with immediate_transaction(conn):
        ensure_app_row(conn, appid)

        existing = conn.execute(
            f"""
            SELECT {_JOB_COLUMNS} FROM jobs
            WHERE appid = ? AND type = ? AND status IN ({_ACTIVE_PLACEHOLDERS})
            ORDER BY id
            LIMIT 1
            """,
            (appid, JOB_TYPE_PREFILL, *ACTIVE_STATUSES),
        ).fetchone()
        if existing is not None:
            return _row_to_dict(existing), False

        cursor = conn.execute(
            """
            INSERT INTO jobs (appid, type, status, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (appid, JOB_TYPE_PREFILL, STATUS_QUEUED, utcnow_iso()),
        )
        job_id = int(cursor.lastrowid)

        row = conn.execute(
            f"SELECT {_JOB_COLUMNS} FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return _row_to_dict(row), True


def enqueue_gc(
    conn: sqlite3.Connection, appid: int, *, execute: bool
) -> tuple[dict[str, object], bool]:
    """Queue a GC job for ``appid`` (WP 3.8, ADR-0007). Returns ``(job, created)``.

    ``execute`` is a **required keyword argument with no default** — the same
    device ``deletion.delete_app_depots``'s ``co_owners`` and
    ``deletion.reset_app_after_deletion``'s ``set_needs_force`` use. A default
    of ``False`` would be safe here, but it would also make "which mode is
    this job?" a question a call site does not have to answer, and this is the
    one flag that decides whether a job deletes files. It is spelled out at
    every call site, where a reviewer can see it.

    **Dedupe is per (app, mode), not per app.** ``enqueue_prefill`` folds a
    second request for the same app into the in-flight job because running the
    same prefill twice only re-downloads nothing. That reasoning does not carry
    over: a dry run and an execute run are different operations with different
    consequences, so folding one into the other would either silently downgrade
    an execute request to a report (dishonest) or — far worse — silently answer
    a dry-run request with a job that deletes files. So an in-flight job is
    reused only when its ``gc_execute`` value matches the request exactly;
    otherwise a second job is queued and the two run in turn on the single
    worker.

    ``ensure_app_row`` is deliberately NOT called: unlike ``POST /v1/prefill``
    (which may legitimately target an app vault-api has never seen), GC only
    makes sense for an app that already has cache state, and the endpoint
    404s on an unknown app before ever reaching this function. Queueing GC
    must not be a way to create app rows.
    """
    with immediate_transaction(conn):
        existing = conn.execute(
            f"""
            SELECT {_JOB_COLUMNS} FROM jobs
            WHERE appid = ? AND type = ? AND status IN ({_ACTIVE_PLACEHOLDERS})
              AND gc_execute = ?
            ORDER BY id
            LIMIT 1
            """,
            (appid, JOB_TYPE_GC, *ACTIVE_STATUSES, int(execute)),
        ).fetchone()
        if existing is not None:
            return _row_to_dict(existing), False

        cursor = conn.execute(
            """
            INSERT INTO jobs (appid, type, status, created_at, gc_execute)
            VALUES (?, ?, ?, ?, ?)
            """,
            (appid, JOB_TYPE_GC, STATUS_QUEUED, utcnow_iso(), int(execute)),
        )
        job_id = int(cursor.lastrowid)

        row = conn.execute(
            f"SELECT {_JOB_COLUMNS} FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return _row_to_dict(row), True


def job_deletes(job: Mapping[str, object]) -> bool:
    """Does this job row mean "delete", i.e. is it a GC job in execute mode?

    The one place the ``(type, gc_execute)`` pair is turned into a yes/no, so
    the fail-closed direction is written down exactly once: **anything that is
    not unambiguously a GC job with ``gc_execute`` truthy is a no.** A row
    whose ``gc_execute`` is ``NULL`` (every prefill job, and any GC row written
    by a version of this code that predates the column) reads as "dry run",
    never as "delete" — a missing mode must never resolve to the destructive
    one.
    """
    if str(job.get("type")) != JOB_TYPE_GC:
        return False
    return bool(job.get("gc_execute"))


def get_job(conn: sqlite3.Connection, job_id: int) -> dict[str, object] | None:
    """One job incl. its log excerpt, or None if the id is unknown."""
    row = conn.execute(
        f"SELECT {_JOB_COLUMNS} FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    return None if row is None else _row_to_dict(row)


def active_job_for_app(
    conn: sqlite3.Connection, appid: int
) -> dict[str, object] | None:
    """The app's oldest in-flight (``ACTIVE_STATUSES``) job, or None.

    Same "in flight" definition ``enqueue_prefill`` dedupes on, exposed as a
    read for ``DELETE /v1/cache/{appid}`` (WP 1.6): deleting depot directories
    while a prefill is writing into them would delete under an active download,
    so that endpoint refuses with 409. Served by ``idx_jobs_appid_status``.

    WP 3.12: a ``paused`` job counts here too. There is no subprocess writing
    at that moment, but the depots hold a partial download that resume is
    going to continue from (the cache is the progress store) — deleting them
    would throw exactly that away, so the 409 is the right answer and its
    message says so.
    """
    row = conn.execute(
        f"""
        SELECT {_JOB_COLUMNS} FROM jobs
        WHERE appid = ? AND status IN ({_ACTIVE_PLACEHOLDERS})
        ORDER BY id
        LIMIT 1
        """,
        (appid, *ACTIVE_STATUSES),
    ).fetchone()
    return None if row is None else _row_to_dict(row)


def list_jobs(conn: sqlite3.Connection, limit: int) -> list[dict[str, object]]:
    """Most recent jobs, newest first.

    Deliberately omits ``log_excerpt``: this endpoint is the app's polling
    surface, and 20 x 4 KiB of log text per poll is not what "small" means.
    ``GET /v1/jobs/{id}`` carries the excerpt. ``updated``/``up_to_date``/
    ``summary_parse_ok`` (schema v4, WP 3.3) ARE included here — they are
    small scalars, not multi-KB text, so the same size argument doesn't apply.
    """
    rows = conn.execute(
        """
        SELECT id, appid, type, status, created_at, started_at, finished_at,
               updated, up_to_date, summary_parse_ok, gc_execute,
               paused_at, stop_request
        FROM jobs
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def claim_next_job(conn: sqlite3.Connection) -> dict[str, object] | None:
    """Atomically claim the oldest queued job (FIFO by id). None if empty.

    The claim is a conditional UPDATE (``WHERE id = ? AND status = 'queued'``)
    inside an immediate transaction, so it is safe even if a second worker ever
    existed: the loser's UPDATE matches zero rows and it sees ``None``. The
    same lock is what makes ``cancel_job`` on a queued job safe: it either wins
    (the job is cancelled and never claimed) or loses (the job is running and
    the cancel becomes a ``stop_request`` the worker honors).

    ``paused`` rows are invisible here (WP 3.12) — a paused job re-enters the
    queue only through ``resume_job``, and because it keeps its original id it
    is then claimed before anything queued during the pause.
    """
    with immediate_transaction(conn):
        row = conn.execute(
            """
            SELECT id FROM jobs
            WHERE status = ?
            ORDER BY id
            LIMIT 1
            """,
            (STATUS_QUEUED,),
        ).fetchone()
        if row is None:
            return None

        job_id = int(row["id"])
        started_at = utcnow_iso()
        cursor = conn.execute(
            """
            UPDATE jobs SET status = ?, started_at = ?
            WHERE id = ? AND status = ?
            """,
            (STATUS_RUNNING, started_at, job_id, STATUS_QUEUED),
        )
        if cursor.rowcount == 0:  # pragma: no cover - only reachable with 2 workers
            return None

        claimed = conn.execute(
            f"SELECT {_JOB_COLUMNS} FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return _row_to_dict(claimed)


def finish_job(
    conn: sqlite3.Connection,
    job_id: int,
    status: str,
    log_excerpt: str,
    updated: int | None = None,
    up_to_date: int | None = None,
    summary_parse_ok: bool | None = None,
) -> None:
    """Mark a job terminal (``done``/``error``/``cancelled``) and store its
    (tail-truncated) log.

    **``stop_request`` is always cleared here (WP 3.12).** A terminal job has
    an outcome; a pending "please cancel this" against it is not just inert but
    actively misleading — a UI that shows a spinner for "cancelling…" would
    show it forever on a job that finished on its own microseconds before the
    request landed. Clearing it in the one function every terminal transition
    already goes through means no caller can forget.

    ``updated``/``up_to_date``/``summary_parse_ok`` (schema v4, WP 3.3) are
    SteamPrefill's own summary-table counters (``vault_api.prefill_summary``,
    ADR-0006 decision 1) — left at their SQL-``NULL`` default (all three
    parameters default to ``None``) for any job that never reaches a parsed
    summary (a GC job, a failed/timed-out/aborted prefill, the crash-recovery
    path in ``recover_stale_jobs``). ``summary_parse_ok`` is stored as 0/1,
    SQLite having no native boolean.
    """
    conn.execute(
        """
        UPDATE jobs
        SET status = ?, finished_at = ?, log_excerpt = ?,
            updated = ?, up_to_date = ?, summary_parse_ok = ?,
            stop_request = NULL
        WHERE id = ?
        """,
        (
            status,
            utcnow_iso(),
            tail_excerpt(log_excerpt),
            updated,
            up_to_date,
            None if summary_parse_ok is None else int(summary_parse_ok),
            job_id,
        ),
    )
    conn.commit()


# --------------------------------------------------------------------------
# Job control: cancel / pause / resume (WP 3.12)
# --------------------------------------------------------------------------

#: Outcomes of the three control transitions below. Deliberately data, not
#: exceptions: ``routers/jobs.py`` maps them to 200/404/409, and a future
#: caller that is not an HTTP endpoint gets to make its own choice.
CONTROL_UNKNOWN = "unknown"
#: The transition completed synchronously (a queued/paused job needs no worker).
CONTROL_IMMEDIATE = "immediate"
#: The job is running: the request was recorded and the worker will act on it.
CONTROL_REQUESTED = "requested"
#: A paused job was put back in the queue.
CONTROL_RESUMED = "resumed"
#: The job is in a state this transition does not apply to (-> 409).
CONTROL_CONFLICT = "conflict"

CANCELLED_QUEUED_MESSAGE = (
    "[vault-api] Cancelled while queued: this job was never started, so "
    "nothing ran and nothing on disk was touched."
)

CANCELLED_PAUSED_MESSAGE = (
    "[vault-api] Cancelled while paused. The SteamPrefill subprocess had "
    "already been terminated by the pause, so nothing was running at this "
    "point. Whatever the run had cached before it was paused stays on disk "
    "and is served as local HITs by the next prefill for this app."
)


@dataclass(frozen=True)
class ControlResult:
    """What one cancel/pause/resume attempt did."""

    outcome: str
    #: The job row AFTER the attempt — ``None`` only for ``CONTROL_UNKNOWN``.
    job: dict[str, object] | None = None
    #: Human-readable reason, filled for ``CONTROL_CONFLICT`` (becomes the 409
    #: detail) and for the informational outcomes.
    detail: str = ""


def read_stop_request(conn: sqlite3.Connection, job_id: int) -> str | None:
    """The pending stop request for a job (``'cancel'``/``'pause'``/``None``).

    Called from the WORKER thread on the worker's own connection, on the
    subprocess poll tick and between GC depots — a single primary-key SELECT.
    Returns ``None`` for an unknown id, which is the safe direction: a job row
    that vanished is not a reason to abort work that is already underway.
    """
    row = conn.execute(
        "SELECT stop_request FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    if row is None:
        return None
    value = row["stop_request"]
    return None if value is None else str(value)


def reset_app_status_if_running(conn: sqlite3.Connection, appid: int) -> None:
    """Put ``apps.status`` back to ``idle`` — but only if it is ``running``.

    Used by the cancel and pause paths. ``apps.status`` has exactly the four
    plan-§3 values and gains none from job control (module docstring), so the
    question is only which of them a stopped run leaves behind:

    - **not ``error``**: an operator pressing stop is not a failure, and the
      red badge is the one signal that has to keep meaning "something went
      wrong".
    - **not left at ``running``**: nothing is running, and a yellow badge with
      no worker behind it is the permanent-stale-badge bug ``recover_stale_jobs``
      exists to prevent.
    - **``idle``** says exactly what is true: no run is in flight and vault-api
      makes no claim about this app's fill state. ``last_prefill_at`` is left
      untouched, so an app that WAS filled earlier still reports when.

    The ``WHERE ... AND status = 'running'`` guard (same shape as
    ``recover_stale_jobs``') keeps this from clobbering a status a concurrent
    ``DELETE /v1/cache/{appid}`` just wrote.
    """
    conn.execute(
        "UPDATE apps SET status = ? WHERE appid = ? AND status = ?",
        (STATUS_IDLE, appid, STATUS_RUNNING),
    )
    conn.commit()


def park_paused(
    conn: sqlite3.Connection, job_id: int, log_excerpt: str
) -> dict[str, object] | None:
    """Park a running job at ``paused`` (worker-side half of pause).

    ``finished_at`` stays ``NULL`` — the job is not finished — while
    ``paused_at`` records when it was suspended, and ``stop_request`` is
    cleared because the request has now been honored. The summary counters are
    likewise left ``NULL``: a terminated run produced no trustworthy
    ``Updated``/``Up To Date`` numbers (see ``worker.py``).

    Conditional on ``status = 'running'`` so a job that finished on its own in
    the same instant is never dragged back out of a terminal state; returns the
    row afterwards (``None`` if the id is unknown).
    """
    with immediate_transaction(conn):
        conn.execute(
            """
            UPDATE jobs
            SET status = ?, paused_at = ?, stop_request = NULL, log_excerpt = ?
            WHERE id = ? AND status = ?
            """,
            (
                STATUS_PAUSED,
                utcnow_iso(),
                tail_excerpt(log_excerpt),
                job_id,
                STATUS_RUNNING,
            ),
        )
        row = conn.execute(
            f"SELECT {_JOB_COLUMNS} FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
    return None if row is None else _row_to_dict(row)


def cancel_job(conn: sqlite3.Connection, job_id: int) -> ControlResult:
    """``DELETE /v1/jobs/{id}``: cancel one job. Never raises for a bad state.

    Three shapes, decided inside ONE ``BEGIN IMMEDIATE`` transaction so the
    decision cannot be invalidated between the read and the write (the worker's
    claim takes the same write lock, so "cancel a queued job the worker is
    claiming right now" resolves one way or the other, never both):

    * **queued** — finalized here and now, and it never runs: the worker only
      ever claims ``queued`` rows, and this transaction moves it out of that
      state under the write lock.
    * **paused** — same, and equally immediate: pause already terminated the
      subprocess, so there is nothing left to stop.
    * **running** — ``stop_request`` is recorded and the WORKER finalizes the
      job (it owns the subprocess, and it is the only place that can honestly
      say what the run had achieved). Asynchronous by nature; the caller polls.

    A job that already has an outcome (``TERMINAL_STATUSES``) is a conflict,
    not a no-op: it already happened, and rewriting a ``done`` job to
    ``cancelled`` would falsify history.
    """
    with immediate_transaction(conn):
        row = conn.execute(
            f"SELECT {_JOB_COLUMNS} FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            return ControlResult(CONTROL_UNKNOWN)

        job_status = str(row["status"])
        appid = int(row["appid"])

        if job_status in TERMINAL_STATUSES:
            return ControlResult(
                CONTROL_CONFLICT,
                _row_to_dict(row),
                detail=(
                    f"Job {job_id} already finished with status "
                    f"'{job_status}'. A job that has an outcome cannot be "
                    "cancelled — that outcome is what actually happened."
                ),
            )

        if job_status in (STATUS_QUEUED, STATUS_PAUSED):
            message = (
                CANCELLED_QUEUED_MESSAGE
                if job_status == STATUS_QUEUED
                else CANCELLED_PAUSED_MESSAGE
            )
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, finished_at = ?, log_excerpt = ?,
                    stop_request = NULL
                WHERE id = ? AND status = ?
                """,
                (
                    STATUS_CANCELLED,
                    utcnow_iso(),
                    message,
                    job_id,
                    job_status,
                ),
            )
            # Only ever fires for a paused prefill whose apps row was somehow
            # left at 'running' (pause itself already resets it); a queued job
            # never set the status in the first place, and the guard makes this
            # a no-op there rather than a silent downgrade of a 'done' app.
            conn.execute(
                "UPDATE apps SET status = ? WHERE appid = ? AND status = ?",
                (STATUS_IDLE, appid, STATUS_RUNNING),
            )
            after = conn.execute(
                f"SELECT {_JOB_COLUMNS} FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            return ControlResult(
                CONTROL_IMMEDIATE, _row_to_dict(after), detail=message
            )

        # running
        conn.execute(
            "UPDATE jobs SET stop_request = ? WHERE id = ? AND status = ?",
            (STOP_REQUEST_CANCEL, job_id, STATUS_RUNNING),
        )
        after = conn.execute(
            f"SELECT {_JOB_COLUMNS} FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return ControlResult(
            CONTROL_REQUESTED,
            _row_to_dict(after),
            detail=(
                f"Job {job_id} is running; cancellation was requested. The "
                "worker stops it at its next check — a prefill by terminating "
                "SteamPrefill, a GC run after the depot it is currently "
                "working on. Poll GET /v1/jobs/{id} until the status is "
                "'cancelled'."
            ),
        )


def request_pause(conn: sqlite3.Connection, job_id: int) -> ControlResult:
    """``POST /v1/jobs/{id}/pause``: suspend a RUNNING PREFILL job.

    Restricted to running prefills, and both halves of that are deliberate:

    * **Prefill only.** A GC job is refused (409). GC runs are short — the plan
      is rebuilt from scratch on every run anyway, so "pause" would mean
      "throw away the current plan and rebuild it later", which is what
      cancelling and re-queueing already does, spelled honestly.
    * **Running only.** A queued job has nothing to suspend (cancel it, or let
      it run); a paused job is already paused; a finished one has an outcome.

    Like ``cancel_job``, the actual suspension is the worker's: it terminates
    SteamPrefill and calls ``park_paused``. **There is no wire protocol to
    SteamPrefill** — it has no pause signal, so pause IS terminate, and resume
    IS a fresh run. What makes that cheap rather than wasteful is that the
    chunks already on disk replay as local HITs (ADR-0001: ~120x faster than a
    miss), i.e. the cache itself is the progress store.
    """
    with immediate_transaction(conn):
        row = conn.execute(
            f"SELECT {_JOB_COLUMNS} FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            return ControlResult(CONTROL_UNKNOWN)

        job_status = str(row["status"])
        job_type = str(row["type"])

        if job_type != JOB_TYPE_PREFILL:
            return ControlResult(
                CONTROL_CONFLICT,
                _row_to_dict(row),
                detail=(
                    f"Job {job_id} is a '{job_type}' job and cannot be paused. "
                    "Only prefill jobs can — a GC run is short and rebuilds its "
                    "plan from scratch every time, so there is no partial "
                    "progress a pause could preserve. Cancel it with "
                    "DELETE /v1/jobs/{id} and queue a new one when you want it."
                ),
            )

        if job_status != STATUS_RUNNING:
            return ControlResult(
                CONTROL_CONFLICT,
                _row_to_dict(row),
                detail=(
                    f"Job {job_id} is '{job_status}', not 'running', so there "
                    "is nothing to pause. "
                    + (
                        "It is already paused — resume it with "
                        "POST /v1/jobs/{id}/resume."
                        if job_status == STATUS_PAUSED
                        else (
                            "A queued job has not started: cancel it with "
                            "DELETE /v1/jobs/{id}, or let it start and pause "
                            "it then."
                            if job_status == STATUS_QUEUED
                            else "It already finished."
                        )
                    )
                ),
            )

        conn.execute(
            "UPDATE jobs SET stop_request = ? WHERE id = ? AND status = ?",
            (STOP_REQUEST_PAUSE, job_id, STATUS_RUNNING),
        )
        after = conn.execute(
            f"SELECT {_JOB_COLUMNS} FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return ControlResult(
            CONTROL_REQUESTED,
            _row_to_dict(after),
            detail=(
                f"Pause requested for job {job_id}. The worker terminates "
                "SteamPrefill at its next check and parks the job at 'paused'; "
                "poll GET /v1/jobs/{id} until it gets there, then resume with "
                "POST /v1/jobs/{id}/resume."
            ),
        )


def resume_job(conn: sqlite3.Connection, job_id: int) -> ControlResult:
    """``POST /v1/jobs/{id}/resume``: put a paused job back in the queue.

    **Priority comes for free, and that is why there is no priority column.**
    The queue is FIFO by ``jobs.id`` (``claim_next_job``) and a resumed job
    keeps the id it was created with — which is, by construction, older than
    everything enqueued while it was paused. Flipping the status back to
    ``queued`` therefore puts it at the FRONT of the queue, which is what a
    resume should mean: the operator suspended this job to let something else
    through, not to send it to the back of the line. Creating a NEW job row
    instead (the obvious alternative) would silently do the opposite.

    ``stop_request`` is cleared so the stale pause request cannot make the
    resumed run stop again immediately, and ``paused_at`` is deliberately KEPT:
    it is the timestamp of the most recent pause, which stays true after a
    resume and gives a UI something honest to show ("resumed, was paused at
    …"). ``status`` is the authority on whether the job is paused right now.

    **The queue-mode hand-off columns are reset to NULL (WP API-FIX-1, S1).**
    A paused queue-mode job still carries the paused attempt's ``run_*``
    columns — ``run_use_force`` set, ``run_completed_at`` set,
    ``run_result_json`` = the ``paused`` result. Left in place, a vault-api
    death in the window between ``claim_next_job`` and ``handoff_run`` of the
    resumed attempt would make that row look like a handed-off,
    already-completed run: ``recover_stale_jobs`` would skip it (it skips
    ``run_use_force IS NOT NULL``) and ``find_active_run`` would hand the OLD
    ``paused`` result to ``await_run_result``, parking the job straight back
    at ``paused``. Clearing them here makes a resumed job indistinguishable
    from a never-handed-off one until its own ``handoff_run`` writes a fresh
    attempt. ``resume_job`` is the only transition back to ``queued`` (there
    is no retry path), so this is the one place that needs it.
    """
    with immediate_transaction(conn):
        row = conn.execute(
            f"SELECT {_JOB_COLUMNS} FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            return ControlResult(CONTROL_UNKNOWN)

        job_status = str(row["status"])
        if job_status != STATUS_PAUSED:
            return ControlResult(
                CONTROL_CONFLICT,
                _row_to_dict(row),
                detail=(
                    f"Job {job_id} is '{job_status}', not 'paused', so there is "
                    "nothing to resume."
                ),
            )

        conn.execute(
            """
            UPDATE jobs SET status = ?, stop_request = NULL,
                run_use_force = NULL, run_before_json = NULL,
                run_claimed_by = NULL, run_claimed_at = NULL,
                run_heartbeat_at = NULL, run_completed_at = NULL,
                run_result_json = NULL
            WHERE id = ? AND status = ?
            """,
            (STATUS_QUEUED, job_id, STATUS_PAUSED),
        )
        after = conn.execute(
            f"SELECT {_JOB_COLUMNS} FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return ControlResult(
            CONTROL_RESUMED,
            _row_to_dict(after),
            detail=(
                f"Job {job_id} is queued again and, keeping its original job "
                "id, runs before anything enqueued while it was paused. "
                "SteamPrefill re-runs from the start; every chunk the earlier "
                "attempt already cached is served from disk as a local HIT."
            ),
        )


#: Message stored on jobs recovered by ``recover_stale_jobs``.
STALE_JOB_MESSAGE = (
    "Job was still marked 'running' when vault-api started. The process that "
    "owned it died (crash, container kill, host reboot) — no worker is "
    "executing it any more, so it has been failed. Re-queue it with "
    "POST /v1/prefill if you still want that app prefilled."
)


def recover_stale_jobs(conn: sqlite3.Connection, queue_mode: bool = False) -> int:
    """Fail every orphaned job left in ``running`` at startup. Returns how many.

    **The rule, stated plainly (unchanged from before WP S-1):** vault-api
    runs exactly ONE job worker inside exactly ONE process (plan §3). A
    ``running`` row can therefore only be owned by the current process's
    worker — and at startup that worker has not claimed anything yet. So any
    ``running`` row is by definition an orphan from a previous process that
    died mid-job, and is failed here rather than being left to sit "running"
    forever (which would also block the app's dedupe: ``POST /v1/prefill``
    would keep handing out that dead job id).

    The consequence of that rule, honestly stated: **do not point two vault-api
    processes at the same database.** The second one's startup would fail the
    first one's genuinely-running job. Nothing detects that misconfiguration —
    single-process operation is the documented deployment model (one container,
    plan §3/§7), not something this code enforces. Also note a recovered job's
    SteamPrefill subprocess may briefly outlive the dead parent; it is
    harmless (it only writes into the cache) and exits on its own.

    ``apps.status`` is repaired for the same reason: an app left at 'running'
    would show a permanent yellow badge in the app.

    **``paused`` jobs are deliberately NOT touched (WP 3.12), and this is the
    single most important line of the function.** A paused job has no
    subprocess — pause terminates SteamPrefill and waits for the child before
    the row ever reaches ``paused`` — so it is not an orphan and there is
    nothing to recover. Surviving a restart is the whole point: an operator who
    pauses a 60 GB download on Friday must still be able to resume it after
    Monday's container restart. Widening this query to include ``paused``
    would eat every paused job on every restart; a test mutation-pins it.

    **``queue_mode`` (WP S-1, ADR-0012) narrows the rule's premise for
    prefill jobs specifically.** "Exactly ONE worker owns this job" stops
    being true the moment execution is handed off to a *separate*
    ``prefill_runner`` process — vault-api restarting says nothing about
    whether that process (a different container) is still alive. So in queue
    mode, a ``running`` prefill job that has already been handed off
    (``run_use_force IS NOT NULL`` — see ``handoff_run``) is left completely
    untouched here, no matter how it looks: ``PrefillWorker._run`` finds it
    again via ``find_active_run`` on its very first tick and resumes waiting
    on it exactly as if the process had never restarted, applying the same
    live heartbeat-staleness check (``run_is_stale``) it always would. That is
    the ONE reconciliation path this project has for "is the runner still
    there" — deliberately not duplicated here, so a stale-lease bug only has
    one place to hide instead of two disagreeing ones.
    A ``running`` prefill job that was never handed off (``run_use_force IS
    NULL`` — the narrow window between ``claim_next_job`` and ``handoff_run``;
    this holds for a resumed job too, because ``resume_job`` clears the
    paused attempt's ``run_*`` columns) is a genuine single-process orphan even in queue mode, exactly like every
    other job type, and is failed here as before. GC jobs never go through the
    runner split at all (worker.py still runs them in-process, ADR-0012 is
    prefill-only) and are always covered by the blanket rule regardless of
    ``queue_mode``.
    """
    with immediate_transaction(conn):
        rows = conn.execute(
            "SELECT id, appid, type, run_use_force FROM jobs WHERE status = ?",
            (STATUS_RUNNING,),
        ).fetchall()
        if not rows:
            return 0

        finished_at = utcnow_iso()
        recovered = 0
        for row in rows:
            if (
                queue_mode
                and str(row["type"]) == JOB_TYPE_PREFILL
                and row["run_use_force"] is not None
            ):
                # Handed off to a runner that may still be alive in a
                # separate process — not this function's call to make.
                continue
            # stop_request = NULL (WP API-FIX-1, P3): this is a terminal
            # transition like every other, and db.py's column comment
            # promises the one-shot flag is cleared at each of them. A
            # cancel/pause that raced the dead process would otherwise
            # survive on an 'error' row forever.
            conn.execute(
                """
                UPDATE jobs SET status = ?, finished_at = ?, log_excerpt = ?,
                    stop_request = NULL
                WHERE id = ?
                """,
                (STATUS_ERROR, finished_at, STALE_JOB_MESSAGE, int(row["id"])),
            )
            conn.execute(
                "UPDATE apps SET status = ? WHERE appid = ? AND status = ?",
                (STATUS_ERROR, int(row["appid"]), STATUS_RUNNING),
            )
            recovered += 1
        return recovered


# --------------------------------------------------------------------------
# Queue-mode job hand-off (WP S-1, ADR-0012): vault-api's worker keeps owning
# job lifecycle/state transitions; these functions are the ONLY thing that
# changes when ``VAULT_PREFILL_MODE=queue`` — execution moves to a separate
# ``prefill_runner`` process, coordinated through the seven ``run_*`` columns
# below (schema v15) instead of an in-process ``subprocess.Popen`` call. See
# ``vault_api/prefill_queue.py`` for the encode/decode helpers and the
# wait-loop that ties these together, and ``worker.py`` for the caller.
#
# There is deliberately NO lease-stealing reclaim here: once a runner's
# heartbeat goes stale (``run_is_stale``), the caller (worker.py) fails the
# JOB — it leaves 'running' entirely — rather than letting a second runner
# instance silently take over the same row. That is what makes
# ``claim_run``'s plain ``run_claimed_by IS NULL`` check sufficient for
# mutual exclusion on its own: a terminal job can never satisfy
# ``status = 'running'`` again, so nothing can claim it after the fact. See
# ADR-0012 §4 for the two-runners-racing scenario this rejects.
# --------------------------------------------------------------------------

#: Columns queue-mode helpers below read together. Deliberately its own
#: tuple, not folded into ``_JOB_COLUMNS`` above: the API-facing job dict
#: shape (``GET /v1/jobs`` etc.) is out of this work package's footprint, and
#: ``run_result_json``/``run_before_json`` can be several KB of raw
#: SteamPrefill output — exactly the "not in the polling surface" reasoning
#: ``list_jobs`` already applies to ``log_excerpt``.
_RUN_COLUMNS = (
    "id, appid, type, status, started_at, run_use_force, run_before_json, "
    "run_claimed_by, run_claimed_at, run_heartbeat_at, run_completed_at, "
    "run_result_json"
)


def get_run_row(conn: sqlite3.Connection, job_id: int) -> dict[str, object] | None:
    """One job's queue-mode hand-off columns (``_RUN_COLUMNS``), or ``None``."""
    row = conn.execute(
        f"SELECT {_RUN_COLUMNS} FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    return None if row is None else _row_to_dict(row)


def handoff_run(
    conn: sqlite3.Connection, job_id: int, use_force: bool, before_json: str
) -> None:
    """Record a FRESH execution request for this job. Unconditional — every
    call is a new attempt, not a one-time write.

    **Round-2 review fix (WP S-1 blocker B1).** An earlier version of this
    function only wrote on the FIRST call for a job id (a ``WHERE
    run_use_force IS NULL`` guard), reasoning about the wrong scenario: the
    restart-reattach case (``PrefillWorker._resume_prefill``) does NOT call
    this function AT ALL — by design, it reads the row's existing
    ``run_use_force``/``run_before_json`` back instead, exactly because that
    in-flight run must not be re-decided (see ``_resume_prefill``'s own
    docstring). This function's only real caller in the whole codebase is
    the fresh-claim path (``PrefillWorker._run_prefill_via_queue``), called
    exactly once per ``_execute_prefill`` invocation — so the write-once
    guard's only OBSERVABLE effect in production was blocking the SECOND,
    genuinely NEW run of the SAME job row that ``POST /v1/jobs/{id}/resume``
    produces: the second call silently no-opped, the STALE
    ``run_completed_at``/``run_result_json`` left over from the run BEFORE
    the pause were never cleared, and the resumed job's own
    ``await_run_result`` call immediately "completed" with that OLD
    (``paused``) result — a resumed queue-mode job never actually re-ran at
    all (measured end-to-end: ``argv.json`` was never recreated,
    ``run_completed_at`` was byte-identical before and after resume). A
    zero-legitimate-caller guard whose only real effect was a bug — see
    docs/LEARNINGS.md's "documented mechanism with zero callers" class.

    **The fix.** Every hand-off — first attempt or Nth, after a pause/resume
    or not — is a full fresh-attempt write: besides ``run_use_force``/
    ``run_before_json``, it explicitly resets the RUNNER-OWNED columns
    (``run_claimed_by``, ``run_claimed_at``, ``run_completed_at``,
    ``run_result_json``) to ``NULL`` in the SAME statement, so ``claim_run``
    sees a genuinely unclaimed job again and the ``await_run_result`` call
    that follows this hand-off cannot observe a stale result left over from
    a previous attempt at this job id. The window BEFORE this call (between
    ``claim_next_job`` and here) is covered by ``resume_job``, which clears
    the paused attempt's ``run_*`` columns when the job is re-queued — so a
    vault-api death in that window leaves a row with ``run_use_force IS
    NULL`` that ``recover_stale_jobs`` fails as an orphan, and
    ``find_active_run`` never sees it. This is safe for the restart-reattach
    case specifically because that case never calls this function.

    **``run_heartbeat_at`` is stamped with NOW, not NULL (WP API-FIX-1,
    B2): the hand-off is where the runner lease starts.** ``run_is_stale``
    consults ``run_heartbeat_at`` first and only falls back to
    ``started_at`` when it is NULL — but ``started_at`` is stamped by
    ``claim_next_job``, BEFORE vault-api's own pre-hand-off work
    (``prefill.scan_depots`` — a full cache walk that on a cold, large cache
    can take longer than the whole lease). Measured from ``started_at``, an
    unclaimed job's lease could therefore already be spent by the time this
    function ran, and ``await_run_result`` failed EVERY job as
    ``runner_lost`` before any runner had a chance to claim it. Writing the
    hand-off time here means "no runner claimed this within the lease" is
    measured from the moment a runner COULD have claimed it, which is the
    only reading of that timeout that makes sense. The runner overwrites
    the value with its own heartbeats once it owns the job (``claim_run``
    stamps it too), so nothing about the claimed-job semantics changes.
    """
    conn.execute(
        """
        UPDATE jobs
        SET run_use_force = ?, run_before_json = ?,
            run_claimed_by = NULL, run_claimed_at = NULL,
            run_heartbeat_at = ?, run_completed_at = NULL,
            run_result_json = NULL
        WHERE id = ?
        """,
        (int(use_force), before_json, utcnow_iso(), job_id),
    )
    conn.commit()


def claim_run(conn: sqlite3.Connection, runner_id: str) -> dict[str, object] | None:
    """Atomically claim the oldest handed-off, unclaimed prefill job.

    The compare-and-swap that makes "two concurrent claimers, one job ->
    exactly one wins" true: the ``UPDATE ... WHERE run_claimed_by IS NULL``
    runs inside ``BEGIN IMMEDIATE``, so competing callers (a second runner
    replica, or the same runner racing its own retry) serialize on SQLite's
    write lock and only the first one's ``UPDATE`` matches a row — every
    later one sees ``rowcount == 0`` and gets ``None`` back, exactly the same
    shape ``claim_next_job`` already uses for the analogous race between two
    hypothetical vault-api workers.

    Only considers jobs already handed off (``run_use_force IS NOT NULL`` —
    see ``handoff_run``) and not yet completed (``run_completed_at IS
    NULL``); ``status = 'running'`` excludes anything vault-api has since
    finalized (including a job this same function's caller declared dead via
    staleness — see the module-level note above for why that is what makes
    this safe without a lease-stealing reclaim).
    """
    with immediate_transaction(conn):
        row = conn.execute(
            f"""
            SELECT id FROM jobs
            WHERE status = ? AND type = ?
              AND run_use_force IS NOT NULL
              AND run_claimed_by IS NULL
              AND run_completed_at IS NULL
            ORDER BY id
            LIMIT 1
            """,
            (STATUS_RUNNING, JOB_TYPE_PREFILL),
        ).fetchone()
        if row is None:
            return None

        job_id = int(row["id"])
        now = utcnow_iso()
        cursor = conn.execute(
            """
            UPDATE jobs SET run_claimed_by = ?, run_claimed_at = ?, run_heartbeat_at = ?
            WHERE id = ? AND run_claimed_by IS NULL
            """,
            (runner_id, now, now, job_id),
        )
        if cursor.rowcount == 0:  # pragma: no cover - only with >=2 real racers
            return None

        claimed = conn.execute(
            f"SELECT {_RUN_COLUMNS} FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return None if claimed is None else _row_to_dict(claimed)


def record_run_heartbeat(conn: sqlite3.Connection, job_id: int, runner_id: str) -> bool:
    """Refresh ``run_heartbeat_at`` for the job THIS runner instance holds.

    Guarded by both ``run_claimed_by = ?`` and ``status = 'running'`` so a
    runner that vault-api has already declared dead (staleness detected,
    job failed and no longer 'running') cannot resurrect its own bookkeeping
    on a terminal row — the write is a silent no-op (``False``) rather than
    an error, matching ``record_run_result``'s same guard below and the
    project's existing "a too-late signal is logged, not fought over" house
    style (``worker.py``'s late-stop-request handling).
    """
    cursor = conn.execute(
        """
        UPDATE jobs SET run_heartbeat_at = ?
        WHERE id = ? AND run_claimed_by = ? AND status = ?
        """,
        (utcnow_iso(), job_id, runner_id, STATUS_RUNNING),
    )
    conn.commit()
    return cursor.rowcount > 0


def record_run_result(
    conn: sqlite3.Connection, job_id: int, runner_id: str, result_json: str
) -> bool:
    """Store the runner's finished result. Returns whether the write applied.

    Same ``run_claimed_by = ? AND status = 'running'`` guard as
    ``record_run_heartbeat`` — if vault-api already declared this job's
    runner dead and failed it (the job is no longer 'running'), this is a
    no-op: the bytes SteamPrefill already wrote stay on disk regardless (the
    same "the cache is the progress store" property job control's
    cancel/pause already relies on), but nothing here would resurrect a
    job whose outcome vault-api has already recorded.
    """
    cursor = conn.execute(
        """
        UPDATE jobs SET run_completed_at = ?, run_result_json = ?
        WHERE id = ? AND run_claimed_by = ? AND status = ?
        """,
        (utcnow_iso(), result_json, job_id, runner_id, STATUS_RUNNING),
    )
    conn.commit()
    return cursor.rowcount > 0


def find_active_run(conn: sqlite3.Connection) -> dict[str, object] | None:
    """The one queue-mode prefill job vault-api still has to finalize.

    Read-only reattachment lookup for ``PrefillWorker._run`` after a vault-api
    restart (ADR-0012's "worker dies while runner runs" crash-semantics case):
    a job that was handed off (``run_use_force IS NOT NULL``) and is still
    ``status = 'running'`` from before the restart — ``claim_next_job`` alone
    would never see it again (it only claims ``'queued'`` rows), so without
    this the worker would silently abandon it and go claim something else
    while the runner keeps working unheard. Staleness is judged by the CALLER
    (via ``run_is_stale``), not here — this function only answers "is there
    one", not "is it still alive", so there is exactly one place that decides
    that.

    **A row with ``run_completed_at`` already set IS returned (WP API-FIX-1,
    B1).** The runner finishing while vault-api was down is the ordinary
    shape of this crash case (``compose up -d`` recreates both containers;
    the runner's SIGTERM handler records an ``aborted`` result within
    seconds, while vault-api is still booting). Such a row is still
    ``running`` — only vault-api's ``_finalize_prefill_result`` can move it
    on — so it is exactly what the reattach path exists to collect;
    ``prefill_queue.await_run_result`` returns the recorded result on its
    first poll. An earlier version filtered ``run_completed_at IS NULL``
    here, which left that job ``running`` forever: ``recover_stale_jobs``
    skips handed-off rows by design, ``enqueue_prefill`` deduped every new
    request onto the dead id, and ``DELETE /v1/cache/{appid}`` answered 409
    until an operator edited the database by hand.

    At most one row can exist under the project's one-job-at-a-time invariant;
    ``LIMIT 1`` is defensive, not load-bearing.
    """
    row = conn.execute(
        f"""
        SELECT {_RUN_COLUMNS} FROM jobs
        WHERE status = ? AND type = ?
          AND run_use_force IS NOT NULL
        ORDER BY id
        LIMIT 1
        """,
        (STATUS_RUNNING, JOB_TYPE_PREFILL),
    ).fetchone()
    return None if row is None else _row_to_dict(row)


def run_is_stale(
    run_row: Mapping[str, object], lease_timeout_seconds: float, now: datetime | None = None
) -> bool:
    """Is this queue-mode job's runner presumed dead?

    The one signal used everywhere this question is asked (``worker.py``'s
    live wait loop, ``find_active_run``'s reattach path) — a single function
    so a lease-timeout bug has only one place to hide, per docs/LEARNINGS.md's
    "two call sites computing the same domain predicate WILL diverge" finding.

    Falls back through THREE timestamps, in order of trust:

    1. ``run_heartbeat_at`` — the runner is actively executing and refreshing
       it (the common case once claimed). Since WP API-FIX-1 (B2) it is
       also stamped by ``handoff_run`` itself, so for an UNCLAIMED job it
       is the hand-off time: "is a runner even running" is then measured
       from the moment a runner could first have claimed the job, not from
       ``started_at`` (which predates vault-api's own pre-hand-off cache
       walk and made every job on a cold cache fail as ``runner_lost``
       before a runner got to see it).
    2. ``run_claimed_at`` — the runner claimed the job but died before its
       first heartbeat tick (a crash in the gap between claim and the first
       poll of ``prefill.run_prefill``'s subprocess loop). Unreachable in
       practice since ``claim_run`` stamps both columns together; kept as
       a fallback for rows written by older code.
    3. ``started_at`` — nothing else is set. Only rows handed off by a
       vault-api older than WP API-FIX-1 can look like this; the reasoning
       is unchanged: a job with no runner talking to it should not wait
       forever any more than one with a dead one should.

    A completed run (``run_completed_at`` set) is never stale — it has a
    result waiting to be collected, not a dead runner. A row with none of the
    three timestamps (should not happen for anything that ever reached
    ``status = 'running'``) is treated as NOT stale — the fail-toward-"don't
    declare a job dead on missing data" direction, same house style as
    ``routers/clients.py``'s bypass-detection default.
    """
    if run_row.get("run_completed_at"):
        return False

    candidate = (
        run_row.get("run_heartbeat_at")
        or run_row.get("run_claimed_at")
        or run_row.get("started_at")
    )
    if not candidate:
        return False

    parsed = parse_utc_iso(str(candidate))
    if parsed is None:
        return False

    reference = now if now is not None else datetime.now(timezone.utc)
    return (reference - parsed).total_seconds() > lease_timeout_seconds
