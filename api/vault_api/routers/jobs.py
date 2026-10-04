"""Prefill + job endpoints (plan §6): POST /v1/prefill, GET /v1/jobs[/{id}],
and job control (WP 3.12): DELETE /v1/jobs/{id}, POST /v1/jobs/{id}/pause,
POST /v1/jobs/{id}/resume. Also POST /v1/prefill/cached (Phase 4c, WP
4c-api): the server-side "check & update every cached game" convenience
route — see that endpoint's own docstring for the full contract.

Auth is attached at the router level (secure-by-default pattern, see
api/README.md "Auth" section) — every route added here is authenticated
automatically.

The endpoints only touch the queue table; execution is the background worker's
job (``vault_api/worker.py``), so ``POST /v1/prefill`` answers immediately with
202 and the app polls ``GET /v1/jobs/{id}``. The three control endpoints follow
the same shape: they record an intent and answer immediately. For a queued or
paused job the intent IS the whole transition and it completes inside the
request; for a running job the worker owns the subprocess and therefore owns
the transition, so the response says "requested" and the client polls. Both
cases return ``200`` and describe which happened in the body rather than
splitting the outcome across status codes a client would have to branch on
twice (once on the code, once on the state it then reads anyway).
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from vault_api import deletion, tool_apps
from vault_api import jobs as jobs_queue
from vault_api.auth import require_api_key
from vault_api.deps import DbOpener, db_opener, get_cache_root, get_size_cache
from vault_api.sizes import SizeCache
from vault_api.validation import AppId

router = APIRouter(dependencies=[Depends(require_api_key)], tags=["jobs"])


class PrefillRequest(BaseModel):
    # extra="forbid" for the same reason as the mapping endpoints: a typo'd
    # field name must 422 rather than silently queue nothing.
    model_config = ConfigDict(extra="forbid")

    #: At least one app id; each must be >= 1.
    appids: list[AppId] = Field(min_length=1)


class PrefillJobRef(BaseModel):
    appid: int
    job_id: int
    status: str
    #: True when an existing queued/running job for this app was returned
    #: instead of a new one being created (see the docstring below).
    deduplicated: bool


class JobSummary(BaseModel):
    """A job without its log excerpt — the list view stays small."""

    id: int
    appid: int
    type: str
    status: str
    created_at: str
    started_at: str | None
    finished_at: str | None
    #: SteamPrefill's own summary-table counters (schema v4, WP 3.3,
    #: ADR-0006 decision 1). All three ``None`` until the job finishes, and
    #: ``updated``/``up_to_date`` stay ``None`` even after finishing if
    #: ``summary_parse_ok`` is ``False`` (the table could not be parsed — see
    #: ``vault_api/prefill_summary.py``) or the job never got as far as a
    #: parsed summary (a GC job, a failed/timed-out/aborted prefill).
    updated: int | None = None
    up_to_date: int | None = None
    summary_parse_ok: bool | None = None
    #: GC jobs only (schema v7, WP 3.8, ADR-0007's "dry-run by default"):
    #: ``false`` = this job planned and reported but deleted nothing,
    #: ``true`` = it was allowed to delete. ``null`` for every ``prefill``
    #: job — the field is not applicable, which is a different thing from
    #: "dry run" and is reported as such.
    gc_execute: bool | None = None
    #: When this job was most recently suspended (schema v8, WP 3.12), or
    #: ``null`` if it never was. NOT cleared on resume — it stays the true
    #: answer to "when was this last paused"; ``status`` says whether it is
    #: paused *now*.
    paused_at: str | None = None
    #: A pending operator request against a **running** job: ``"cancel"``,
    #: ``"pause"`` or ``null``. Lets a polling UI show "cancelling…" instead of
    #: a job that still says ``running`` for no visible reason. Always ``null``
    #: once the job reaches a terminal status or is parked.
    stop_request: str | None = None


class JobDetail(JobSummary):
    #: Tail of SteamPrefill's combined stdout/stderr (ANSI-stripped, capped at
    #: 4 KiB) plus vault-api's own diagnostic lines. Null until the job finishes.
    log_excerpt: str | None


@router.post(
    "/v1/prefill",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=list[PrefillJobRef],
)
def create_prefill_jobs(
    body: PrefillRequest,
    open_db: DbOpener = Depends(db_opener),
) -> list[PrefillJobRef]:
    """Queue one prefill job per app id (plan §6). 202 — execution is async.

    **Dedupe:** if an app already has a ``queued`` or ``running`` job, that
    job's id is returned with ``deduplicated: true`` instead of a second job
    being stacked on top of it. Requesting the same app twice (impatient user,
    or Phase 3's miss trigger firing on several misses of one game) therefore
    converges on one job rather than a pile of redundant re-downloads.

    Duplicate app ids *within one request body* fall out of the same rule: the
    response keeps one entry per requested id, in request order, so the second
    occurrence points at the same ``job_id`` with ``deduplicated: true``.

    **Steam tool apps are refused with ``422`` (WP API-FIX-4).** A body that
    names an app in ``tool_apps.TOOL_APPS`` (228980, "Steamworks Common
    Redistributables") is rejected as a whole before anything is queued,
    with a string ``detail`` naming the first such app, the same shape as
    ``PATCH /v1/settings``'s refusals. SteamPrefill cannot prefill a tool
    app, and its depots are cached together with the games that use it, so
    a job for it could only fail. The UIs never offer the action; an older
    client that sends one in a bulk request gets the 422 and queues nothing.
    """
    for appid in body.appids:
        if tool_apps.is_tool_app(appid):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=tool_apps.reject_detail(appid),
            )
    refs: list[PrefillJobRef] = []
    with open_db() as conn:
        for appid in body.appids:
            job, created = jobs_queue.enqueue_prefill(conn, appid)
            refs.append(
                PrefillJobRef(
                    appid=appid,
                    job_id=int(job["id"]),  # type: ignore[arg-type]
                    status=str(job["status"]),
                    deduplicated=not created,
                )
            )
    return refs


def _select_appids_with_cache_content(
    conn: sqlite3.Connection, depot_bytes: dict[int, int]
) -> list[int]:
    """Every app id that currently holds cache content (Phase 4c, WP 4c-api;
    redefined by WP 4f). Sorted, for a deterministic response order.

    **WP 4f: a thin wrapper around `deletion.appids_with_cache_content` —
    the ONE shared definition of "which apps hold cache content" this route
    and `scheduler.cached_appids` (the WP 4d keep-current sweep) both call.**
    Before WP 4f this function had its OWN, more generous rule ("mapped to
    ANY depot with bytes on disk, exclusive or shared") that disagreed with
    the sweep's narrower one on exactly the case that matters: a game the
    operator just deleted whose only surviving content is a shared, still-
    protected depot. This route used to re-queue it; the sweep correctly
    skipped it. See `deletion.appids_with_cache_content`'s docstring for the
    full "why exclusive + remnant, why one shared bulk read" write-up, and
    api/README.md's "Check & update all cached games" / "Sweep target set"
    sections (each cross-references the other) for the measured cost numbers.

    **One SQL statement, regardless of how many apps or depots exist** — the
    whole point of the shared helper existing separately from a per-app loop.
    ``depot_bytes`` (the disk truth, see the caller) is already in memory;
    the shared helper reads the *entire* ``depot_app_map`` table (joined with
    every owner's lifecycle state) in a single ``SELECT`` and classifies it
    in Python, rather than running one query per app or one query per depot.
    A homelab library is hundreds of app rows and thousands of mapping rows —
    both comfortably small to read in full — and reading them all once is
    the only way to avoid an N+1 explosion when N is "every tracked app"
    (`tests/test_prefill_cached.py`'s
    ``test_selecting_cached_apps_is_not_a_per_app_query`` pins the exact
    statement count with a large synthetic library).

    **Selection is disk-and-mapping truth, not bare `apps.status`.** A depot
    with zero files was never omitted from `depot_bytes` in the first place
    (see `sizes.scan_depot_signatures`), so a fresh/never-prefilled app that
    maps only empty directories is correctly never selected. An app stuck at
    `status='error'` after a partially-failed `DELETE /v1/cache/{appid}` (WP
    1.6: `shutil.rmtree` can leave a depot half-deleted) is STILL selected here
    if any of its depots still have bytes on disk — which is the honest and
    useful answer: "check & update" is exactly the repair action such an app
    needs (the leftover `needs_force=1` from the failed deletion makes that
    run forced automatically, see `jobs.get_app_needs_force`). Note that
    `status`/`last_prefill_at` DO feed into the shared/remnant classification
    of a depot this app merely *shares* with another app (ADR-0003's
    addendum) — but never into whether this app's own exclusive/remnant
    depots count, which stays purely disk-and-mapping truth as before.

    **Cache content with no mapping contributes to no app.** A depot directory
    on disk that no `depot_app_map` row claims (see `GET /v1/cache/summary`'s
    `unmapped_depots`) has bytes but no appid to enqueue a prefill for — it is
    silently excluded here, same as it is excluded from every app's
    `size_bytes` in `sizes.build_cache_summary`. There is nothing dishonest
    about this: an unmapped depot is not "this game's cache content", it is
    orphaned or not-yet-attributed content, and `POST /v1/prefill/cached`
    only ever acts on apps it can name.

    **Steam tool apps are left out (WP API-FIX-4).** An app in
    `tool_apps.TOOL_APPS` is never selected, even with cache content of its
    own: its depots are cached together with the games that use them, and
    SteamPrefill cannot prefill it. Same rule as the scheduler's sweep.
    """
    selected, _tools = tool_apps.split_tool_apps(
        deletion.appids_with_cache_content(conn, depot_bytes)
    )
    return sorted(selected)


@router.post(
    "/v1/prefill/cached",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=list[PrefillJobRef],
)
def create_cached_prefill_jobs(
    open_db: DbOpener = Depends(db_opener),
    size_cache: SizeCache = Depends(get_size_cache),
    cache_root: str = Depends(get_cache_root),
) -> list[PrefillJobRef]:
    """"Check & update" every game that currently has cache content (Phase 4c,
    `docs/PROJECT_PLAN.md` §7). No request body — the server, not the caller,
    decides which apps qualify (`_select_appids_with_cache_content` above).

    **This is a fill, not a read-only check — say so wherever this is surfaced.**
    ``docs/PROJECT_PLAN.md``'s "The check IS the fill": SteamPrefill has no
    `--dry-run`, so a non-forced run costs ~3 s and zero bytes for an app that
    is already current and downloads only the changed chunks when it is not —
    one action answers "is there an update?" AND resolves it. Every job this
    route queues goes through the exact same `jobs.enqueue_prefill` `POST
    /v1/prefill` uses: same dedupe against `queued`/`running`/`paused` jobs
    (an impatient double-tap converges on one job per app, not a pile of
    them), same response shape (`PrefillJobRef`, `deduplicated` marks a
    request that was folded into an already in-flight job rather than
    stacking a new one). There is no second enqueue mechanism here — this
    endpoint only decides WHICH app ids to hand to the one that already
    exists.

    **"Non-forced" is what happens by construction, not a flag this route
    sets.** Whether a queued job's run actually passes `--force` is decided
    entirely by the per-app `apps.needs_force` flag at claim time
    (`worker.py`, `jobs.get_app_needs_force`) — `enqueue_prefill` has no force
    parameter to override, and this route does not invent one (see the module
    docstring's "reuse, do not reinvent"). That flag is set back to 1 by TWO
    routine (non-error) events, not one: a deletion touching a depot this app
    maps, AND an *executing* `POST /v1/cache/{appid}/gc` run that actually
    reclaimed chunks from a depot this app maps
    (`gc_execute.flag_needs_force_for_depots`, ADR-0007 — see "needs_force"
    and "Garbage collection" in api/README.md). GC execute is routine
    maintenance, so "the ordinary case is non-forced" only holds for an app
    that has been through neither event recently — it is false for the whole
    window right after a GC execute touches a depot it shares. An app
    selected here that carries `needs_force=1` for either reason still gets
    queued and still runs — forced, correctly, because the flag says the
    on-disk state is uncertain. This route defers to that existing per-app
    decision in every case rather than asserting a blanket "always
    non-forced" it cannot actually guarantee. What a forced run actually
    costs: per `prefill.py`, `--force` only re-*requests* chunks — one still
    on disk replays as a local HIT (~120x faster than a MISS, ADR-0001), so
    the real cost is DURATION (minutes of disk-speed re-touching, serialized
    on the single worker), not bandwidth; genuine downloads happen only for
    chunks that are actually gone.

    **Bypasses the WP 3.11 miss-trigger cooldown, structurally, on purpose.**
    `event_sweep.run_miss_trigger` is the ONLY caller that ever consults
    `miss_trigger_state` (via `event_sweep.in_cooldown`) before enqueuing —
    that cooldown exists to stop a flapping cache MISS from re-triggering the
    same app over and over unattended. A human pressing a button in the app or
    web UI is not a flapping signal; it is a deliberate one-off ask, and
    making it wait out another feature's cooldown would turn "check my
    library now" into "check my library, unless something else touched it
    recently, in which case do nothing and don't say so" — the exact silent
    no-op this docstring exists to head off. This route calls
    `jobs_queue.enqueue_prefill` directly and never imports `event_sweep`, so
    the bypass falls out of the code structure rather than a conditional that
    could be toggled — `tests/test_prefill_cached.py`'s
    ``test_cached_prefill_bypasses_the_miss_trigger_cooldown`` seeds an app
    still inside the cooldown window and asserts a fresh job is queued anyway;
    adding a cooldown check to this route would fail that test by name.
    Still bounded by the two guards that were never about the cooldown:
    **worker slots** (exactly one worker, plan §3 — this route can queue 500
    jobs and they still run strictly one at a time) and **job dedupe** (the
    same `enqueue_prefill` rule described above).

    **Immediate relative to the prefill work — not necessarily fast on the
    wall clock, and that caveat is not new here.** This request never waits
    for a single prefill job to run; it only enqueues and returns `202` with
    the job ids, then lets `GET /v1/jobs` carry progress (a 50-game library
    is roughly 2.5 minutes of serial Steam logins on the single worker —
    that belongs in the Jobs view, never behind a spinner on this endpoint).
    But the *selection* step's `size_cache.get(cache_root)` above can itself
    pay for a full `depot/` walk while holding `SizeCache`'s lock — a
    pre-existing cost shared with `GET /v1/cache/summary` and `GET
    /v1/games`, not something new here. `VAULT_SIZE_CACHE_TTL` defaults to
    60 s, and `worker.py` invalidates the cache after every successful
    prefill job — so a second call to this route while the queue it just
    filled is still draining can pay a fresh cold walk again. Warm, on SSD,
    this is sub-second even at hundreds of thousands of files (measured
    1.76 µs/file); cold and seek-bound on a spinning-disk target it is
    plausibly tens of seconds. Clients should use the same timeout they
    already use for `GET /v1/cache/summary`.

    **Any request body is silently accepted and ignored.** Unlike `POST
    /v1/prefill`'s `extra="forbid"` `PrefillRequest`, this route declares no
    body parameter and does not reject one — an empty body, `{}`, or even
    `{"appids": [...]}` sent by mistake all still return `202` and still
    queue every cached app, not the ids the caller sent. Document this in
    client code; the server gives no signal that a posted body was ignored.

    **A mid-loop `5xx` leaves a partial, unreported result, same as `POST
    /v1/prefill`.** If SQLite's `busy_timeout` is exceeded partway through
    this loop, some apps can already be durably `queued` before the request
    fails with no response body. A `5xx` here means "unknown outcome for
    this call" — the correct recovery is `GET /v1/jobs`, not a blind retry
    (dedupe would make a retry safe regardless, but reading real state first
    is the honest first step).

    **Empty and degenerate cases.** No app currently has cache content ⇒ an
    empty `[]` and a normal `202` — never an error; there is simply nothing to
    check. A cached depot with no mapping row is excluded (see the selection
    helper) — there is no appid to name a job for. An app mid-deletion or left
    at `status='error'` by a partially-failed one is still selected while any
    of its depots still have bytes on disk, per the selection helper's
    docstring — deferring to disk truth there is the same choice `sizes.py`
    already makes for `GET /v1/cache/summary`, not a new policy invented here.

    Phase 6 obligation, recorded so it is not a surprise later: this is new,
    authenticated write-capable API surface (it can trigger real network
    downloads for every cached app in one call), so Phase 6's scoped API keys
    must cover it explicitly rather than only the routes that existed when
    that phase was scoped.
    """
    # The filesystem walk happens through the SAME shared SizeCache instance
    # GET /v1/cache/summary and GET /v1/games use (deps.get_size_cache) — one
    # walk serves every concurrent caller within the TTL, this route included.
    # Taken before opening the connection (same reasoning as routers/cache.py):
    # a cache miss here walks the whole depot/ tree while holding SizeCache's
    # lock, and there is no reason to hold an open SQLite connection across it.
    depot_bytes = size_cache.get(cache_root).depot_bytes

    with open_db() as conn:
        appids = _select_appids_with_cache_content(conn, depot_bytes)
        refs: list[PrefillJobRef] = []
        for appid in appids:
            job, created = jobs_queue.enqueue_prefill(conn, appid)
            refs.append(
                PrefillJobRef(
                    appid=appid,
                    job_id=int(job["id"]),  # type: ignore[arg-type]
                    status=str(job["status"]),
                    deduplicated=not created,
                )
            )
    return refs


@router.get("/v1/jobs", response_model=list[JobSummary])
def list_recent_jobs(
    limit: int = Query(default=20, ge=1, le=200),
    open_db: DbOpener = Depends(db_opener),
) -> list[JobSummary]:
    """Most recent jobs, newest first — the app's polling list."""
    with open_db() as conn:
        rows = jobs_queue.list_jobs(conn, limit)
    return [JobSummary(**row) for row in rows]  # type: ignore[arg-type]


@router.get("/v1/jobs/{job_id}", response_model=JobDetail)
def get_job_status(
    job_id: int,
    open_db: DbOpener = Depends(db_opener),
) -> JobDetail:
    """Status of one job incl. its log excerpt (plan §6: "for app polling")."""
    with open_db() as conn:
        job = jobs_queue.get_job(conn, job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown job id {job_id}"
        )
    return JobDetail(**job)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Job control (WP 3.12)
# --------------------------------------------------------------------------


class JobControlOut(BaseModel):
    """What a cancel/pause/resume request did to the job."""

    job_id: int
    #: The job's status **after** the request. ``"cancelled"``/``"queued"`` when
    #: the transition completed inside this request; still ``"running"`` when
    #: only the request was recorded.
    status: str
    #: ``"immediate"`` — the transition is done; ``"requested"`` — the worker
    #: will carry it out (poll ``GET /v1/jobs/{id}``); ``"resumed"`` — the job
    #: is queued again. A client that only reads this field always knows
    #: whether it has to keep polling.
    outcome: str
    #: Plain-language explanation of exactly what happened and what to do next.
    detail: str


def _control_response(
    job_id: int, result: jobs_queue.ControlResult
) -> JobControlOut:
    """Turn a ``jobs.ControlResult`` into a response or the right HTTP error.

    One helper for all three endpoints so ``unknown`` -> 404 and ``conflict``
    -> 409 cannot drift apart between them (and so the 409 detail is always
    the domain layer's own explanation, never a second one written here).
    """
    if result.outcome == jobs_queue.CONTROL_UNKNOWN:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown job id {job_id}"
        )
    if result.outcome == jobs_queue.CONTROL_CONFLICT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=result.detail
        )
    assert result.job is not None  # every other outcome carries the row
    return JobControlOut(
        job_id=job_id,
        status=str(result.job["status"]),
        outcome=result.outcome,
        detail=result.detail,
    )


@router.delete("/v1/jobs/{job_id}", response_model=JobControlOut)
def cancel_job(
    job_id: int,
    open_db: DbOpener = Depends(db_opener),
) -> JobControlOut:
    """Cancel a job (WP 3.12). ``404`` unknown id, ``409`` already finished.

    - **queued** — cancelled on the spot and never runs. The decision is taken
      under the same write lock the worker claims jobs with, so "cancelled but
      it ran anyway" is not a race this can lose.
    - **paused** — cancelled on the spot too; pause already terminated
      SteamPrefill, so there is nothing left to stop.
    - **running, prefill** — SteamPrefill is terminated by the worker and the
      job ends ``cancelled``. Whatever was downloaded stays in the cache (it is
      served as local HITs later); the depot mapping, the manifest state and
      ``needs_force`` are all left untouched, because a run that did not finish
      is not evidence about any of them.
    - **running, GC** — cooperative: the depot currently being processed is
      finished, then the run stops. Documented rather than hidden — one depot
      is bounded work and stopping half way through one would leave a state
      nobody can reason about.
    - **done / error / cancelled** — ``409``. The job already has an outcome,
      and that outcome is what actually happened; it is not rewritten.

    Returns ``200`` in both working cases; ``outcome`` says whether the job is
    already cancelled (``"immediate"``) or whether the worker still has to act
    (``"requested"``). There is deliberately no ``204``: a cancellation that is
    still in flight has something to say.
    """
    with open_db() as conn:
        result = jobs_queue.cancel_job(conn, job_id)
    return _control_response(job_id, result)


@router.post("/v1/jobs/{job_id}/pause", response_model=JobControlOut)
def pause_job(
    job_id: int,
    open_db: DbOpener = Depends(db_opener),
) -> JobControlOut:
    """Pause a running **prefill** job (WP 3.12). ``404`` / ``409`` otherwise.

    **Pause terminates SteamPrefill — there is no wire protocol.** SteamPrefill
    offers no suspend signal, so pausing means killing the subprocess and
    resuming means running it again from the start. That is affordable, not
    wasteful: every chunk the first attempt already stored is served from disk
    by vault-core as a local HIT (ADR-0001 measured ~120x faster than a miss),
    so **the cache itself is the progress store**. SteamPrefill's own
    ``successfullyDownloadedDepots.json`` additionally keeps the depots it
    fully finished, so a non-forced resume skips those outright.

    ``409`` for a GC job (GC runs are short and rebuild their plan every time —
    cancel it instead) and for any job that is not currently ``running``.

    Answers ``200`` immediately with ``outcome: "requested"``: the worker owns
    the subprocess, so it performs the actual suspension. Poll
    ``GET /v1/jobs/{id}`` until ``status`` is ``"paused"``.
    """
    with open_db() as conn:
        result = jobs_queue.request_pause(conn, job_id)
    return _control_response(job_id, result)


@router.post("/v1/jobs/{job_id}/resume", response_model=JobControlOut)
def resume_job(
    job_id: int,
    open_db: DbOpener = Depends(db_opener),
) -> JobControlOut:
    """Resume a paused job (WP 3.12). ``404`` unknown, ``409`` if not paused.

    The job goes back to ``queued`` **keeping its original job id**, and since
    the queue is FIFO by id it therefore runs before anything enqueued while it
    was paused — resuming means "carry on with this", not "get in line again".
    That is also why no priority column exists.

    Completes inside the request (``outcome: "resumed"``): nothing has to be
    stopped, the worker simply finds the job at the head of the queue on its
    next poll.
    """
    with open_db() as conn:
        result = jobs_queue.resume_job(conn, job_id)
    return _control_response(job_id, result)
