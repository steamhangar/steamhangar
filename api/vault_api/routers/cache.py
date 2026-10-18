"""Cache endpoints (plan §6): ``GET /v1/cache/summary``, ``DELETE /v1/cache/{appid}``,
``POST /v1/cache/{appid}/gc``.

Auth is attached at the router level (secure-by-default pattern, see
api/README.md "Auth" section) — every route added here is authenticated
automatically.

The deletion endpoint's semantics, decisions and failure modes are documented
in api/README.md ("Per-game deletion"); the safety-critical mechanics live in
``vault_api/deletion.py``. Garbage collection is documented under "Garbage
collection" there, plans in ``vault_api/gc.py`` and executes in
``vault_api/gc_execute.py`` — this file only queues the job.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, StrictBool

from vault_api import cache_completeness, cache_repair, deletion, group_delete, jobs
from vault_api.auth import require_api_key
from vault_api.deps import DbOpener, db_opener, get_cache_root, get_size_cache
from vault_api.sizes import SizeCache, build_cache_summary

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_api_key)], tags=["cache"])

#: Plan §6: "per-app top consumers (top 10: appid, name, size_bytes)".
TOP_CONSUMERS_LIMIT = 10


class TopConsumerOut(BaseModel):
    appid: int
    name: str | None
    size_bytes: int


class UnmappedDepotsOut(BaseModel):
    count: int
    size_bytes: int


class CacheSummaryOut(BaseModel):
    #: Actual disk usage of depot/, each depot counted exactly once — may be
    #: LESS than the sum of every game's size_bytes on GET /v1/games, since
    #: shared depots count into every app that maps them there (documented
    #: in vault_api/sizes.py::app_size_bytes).
    total_bytes: int
    top_consumers: list[TopConsumerOut]
    #: Depot directories on disk with no mapping row for any app.
    unmapped_depots: UnmappedDepotsOut
    #: Free space on the filesystem backing VAULT_CACHE_ROOT; null if it
    #: could not be determined (see sizes.free_disk_bytes).
    free_disk_bytes: int | None


@router.get("/v1/cache/summary", response_model=CacheSummaryOut)
def get_cache_summary(
    open_db: DbOpener = Depends(db_opener),
    size_cache: SizeCache = Depends(get_size_cache),
    cache_root: str = Depends(get_cache_root),
) -> CacheSummaryOut:
    """Total usage, top consumers, unmapped depots, free space (plan §6)."""
    # The snapshot is taken BEFORE the connection is opened (WP 1.6 carry-over
    # fix #2 from the WP 1.5 review): a call with no usable snapshot (first
    # call, or right after an invalidation) waits for a whole depot/ walk, and
    # there is no reason to hold an open SQLite connection across that.
    snapshot = size_cache.get(cache_root)

    with open_db() as conn:
        summary = build_cache_summary(
            conn, cache_root, snapshot, top_n=TOP_CONSUMERS_LIMIT
        )

    return CacheSummaryOut(
        total_bytes=summary.total_bytes,
        top_consumers=[
            TopConsumerOut(appid=c.appid, name=c.name, size_bytes=c.size_bytes)
            for c in summary.top_consumers
        ],
        unmapped_depots=UnmappedDepotsOut(
            count=summary.unmapped_depots.count,
            size_bytes=summary.unmapped_depots.size_bytes,
        ),
        free_disk_bytes=summary.free_disk_bytes,
    )


# --------------------------------------------------------------------------
# DELETE /v1/cache/{appid} — plan §4 "Deletion"
# --------------------------------------------------------------------------


class DeletedDepotOut(BaseModel):
    depotid: int
    #: Bytes measured immediately before the directory was removed. 0 for a
    #: depot that was already absent, and 0 for a symlinked/junctioned depot
    #: directory (only the link is removed — its target's bytes are not freed).
    size_bytes_freed: int
    #: Non-empty ONLY for a "last cached remnant" deletion (ADR-0003 addendum):
    #: this depot was still mapped to these other, currently-uncached app ids
    #: when it was removed. Empty ``[]`` for an ordinary exclusive deletion
    #: (the ONLY case before this addendum, and still the common case). Every
    #: id listed here has had ``apps.needs_force`` set to 1 as a side effect of
    #: this request — see api/README.md "Per-game deletion".
    shared_with_uncached: list[int] = []


class SkippedSharedDepotOut(BaseModel):
    depotid: int
    #: The other tracked app ids that map this depot (plan §4: "2 depots shared
    #: with game Y, not deleted"). A depot that only *became* shared after the
    #: deletion was planned appears here too, with its fresh owner list — see
    #: the recheck in deletion.delete_app_depots. Meaning UNCHANGED by the
    #: ADR-0003 addendum: a depot only appears here when AT LEAST ONE of these
    #: owners currently has cache content. A shared depot whose every co-owner
    #: is uncached is no longer reported here — see ``deleted_depots``'s
    #: ``shared_with_uncached`` for that case instead.
    shared_with: list[int]


class FailedDepotOut(BaseModel):
    #: 0 when the mapping row's depot id itself was unusable and cannot be
    #: named (poisoned database); the error string carries the raw value.
    depotid: int
    error: str


class DeleteMemberOut(BaseModel):
    """One app a grouped delete removed together (WP TOOL-APPS-2b)."""

    appid: int
    #: ``"parent"`` (the requested app) or ``"package"``.
    role: str
    #: Display name (``apps.name``, else Steam's name), null if unknown.
    name: str | None
    #: The displayed app type (see ``GET /v1/games``), null if unknown.
    app_type: str | None
    #: This member's mapped depots that were deleted / that failed. A depot
    #: mapped by several members appears under each of them.
    deleted_depots: list[int] = []
    failed_depots: list[int] = []


class KeptChildOut(BaseModel):
    """An app grouped under the parent that is NOT deleted with it
    (WP TOOL-APPS-2b): its own type is not a package type, or only the
    parent's DLC list names it, or its type is unknown. It protects its
    depots like any other app."""

    appid: int
    name: str | None
    app_type: str | None
    #: ``"not_a_package_type"``, ``"type_only_inferred"`` or ``"type_unknown"``.
    reason: str


class CacheDeletionOut(BaseModel):
    appid: int
    deleted_depots: list[DeletedDepotOut]
    #: Never deleted, always reported (plan §4 shared-depot protection).
    skipped_shared: list[SkippedSharedDepotOut]
    #: Non-empty means a PARTIAL deletion: these depots are still on disk (or
    #: partly on disk). The response is still 200 — see api/README.md.
    failed: list[FailedDepotOut]
    #: Sum of deleted_depots[].size_bytes_freed. A floor, never an
    #: overstatement: a depot that failed mid-tree contributes 0.
    total_bytes_freed: int
    #: WP TOOL-APPS-2b: present (``true``) only on a grouped delete
    #: (``?with_packages=true``); a plain request's body is byte-identical to
    #: the pre-2b one (these three fields are absent).
    with_packages: bool = False
    #: Grouped delete only: the parent and every package removed with it.
    #: ``deleted_depots`` / ``skipped_shared`` / ``failed`` above cover the
    #: whole set, each depot once.
    members: list[DeleteMemberOut] = []
    #: Grouped delete only: grouped apps that were NOT deleted (see
    #: ``KeptChildOut``).
    kept_children: list[KeptChildOut] = []


#: Accepted spellings of ``with_packages`` -- literal ``true``/``false`` only
#: (same spirit as ``GcRequest.execute``'s ``StrictBool``: the flag that widens
#: a deletion is never half-understood from "1", "yes" or "on").
_WITH_PACKAGES_VALUES = {"true": True, "false": False}


def _parse_with_packages(raw: str | None) -> bool:
    if raw is None:
        return False
    try:
        return _WITH_PACKAGES_VALUES[raw]
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="with_packages must be exactly 'true' or 'false'.",
        ) from None


_WITH_PACKAGES_DOC = (
    "WP TOOL-APPS-2b: 'true' also deletes the app's content packages "
    "(grouped children whose own type is dlc, config, music, video or "
    "demo). Omitted or 'false': this app only, exactly as before."
)


#: Upper bound on ``members`` entries (a game with more packages than this is
#: not a real catalogue entry; the bound keeps a hostile query string cheap).
MAX_CONFIRMED_MEMBERS = 1024


def _parse_members(raw: str | None, *, with_packages: bool) -> frozenset[int] | None:
    """``members=<csv appids>``: exactly the apps the confirmation dialog
    listed (``delete-plan``'s ``members[].appid``, parent included). Required
    with ``with_packages=true`` (``422`` without it), refused without it.
    Strict grammar: comma-separated ASCII-digit ids, no spaces, no empties."""
    if not with_packages:
        if raw is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="members is only accepted together with with_packages=true.",
            )
        return None
    if raw is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "with_packages=true requires members=<comma-separated app ids>: "
                "exactly the apps the confirmation listed "
                "(GET /v1/cache/{appid}/delete-plan?with_packages=true, members[].appid)."
            ),
        )
    parts = raw.split(",")
    ids: set[int] = set()
    for part in parts[: MAX_CONFIRMED_MEMBERS + 1]:
        appid = deletion.coerce_positive_id(part)
        if appid is None or len(parts) > MAX_CONFIRMED_MEMBERS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "members must be comma-separated app ids (ASCII digits only, "
                    f"at most {MAX_CONFIRMED_MEMBERS})."
                ),
            )
        ids.add(appid)
    return frozenset(ids)


def _member_depots(
    rows: list[tuple[object, object]], members: frozenset[int]
) -> dict[int, set[int]]:
    """``{member: {depotid it maps}}`` from ``load_mapping_rows`` output."""
    out: dict[int, set[int]] = {member: set() for member in members}
    for raw_depotid, raw_owner in rows:
        depotid = deletion.coerce_positive_id(raw_depotid)
        owner = deletion.coerce_positive_id(raw_owner)
        if depotid is not None and owner in out:
            out[owner].add(depotid)
    return out


def _load_delete_request(
    open_db: DbOpener, appid: int, *, with_packages: bool, oracle_enabled: bool
) -> tuple[
    group_delete.GroupSelection,
    list[tuple[object, object]],
    dict[int, bool],
    list[tuple[int, dict[str, object]]],
]:
    """Everything a delete or its plan reads, in one connection: the delete
    set, the mapping rows of every member's depots, the outside co-owners'
    content state and every member's in-flight job. Raises the two ``404``s.
    Shared by ``DELETE /v1/cache/{appid}`` and its ``delete-plan`` preview,
    so the preview and the deletion read the same inputs."""
    with open_db() as conn:
        app_row = conn.execute(
            "SELECT appid FROM apps WHERE appid = ?", (appid,)
        ).fetchone()
        if app_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"Unknown appid {appid} — vault-api tracks no such app, so there "
                    "is nothing to delete."
                ),
            )

        selection = group_delete.select_group(
            conn, appid, with_packages=with_packages, oracle_enabled=oracle_enabled
        )
        delete_set = selection.delete_set

        rows = deletion.load_mapping_rows(conn, _target(delete_set))
        if not rows:
            what = (
                f"App {appid} and its content packages have"
                if len(delete_set.members) > 1
                else f"App {appid} has"
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"{what} no depot mappings, so there is nothing to "
                    "delete. Mappings are created by a successful prefill or via "
                    "PUT /v1/mapping/{depotid}."
                ),
            )

        # ADR-0003 addendum: read every co-owner's current content state
        # (status/last_prefill_at/active job) up front, in the same
        # connection as the mapping read, so plan_deletion below can stay a
        # pure function over plain data. Co-owners are apps OUTSIDE the
        # delete set (WP TOOL-APPS-2b: members never protect each other).
        co_owner_states = deletion.load_co_owner_states(
            conn, deletion.other_owner_ids(rows, _target(delete_set))
        )

        active_jobs: list[tuple[int, dict[str, object]]] = []
        for member in sorted(delete_set.members, key=lambda m: (m != appid, m)):
            job = jobs.active_job_for_app(conn, member)
            if job is not None:
                active_jobs.append((member, job))
    return selection, rows, co_owner_states, active_jobs


def _target(delete_set: deletion.DeleteSet) -> "int | deletion.DeleteSet":
    """What the deletion primitives are called with: the plain app id for a
    one-member set (the exact pre-2b call, which ``deletion.as_delete_set``
    reads as the same one-member set), the ``DeleteSet`` otherwise."""
    return delete_set if len(delete_set.members) > 1 else delete_set.primary


def _oracle_enabled(request: Request) -> bool:
    """Same settings object ``GET /v1/games`` resolves classification with
    (``manifest_oracle`` is not DB-overridable, so the boot value is the
    effective one)."""
    return bool(request.app.state.scheduler.settings.manifest_oracle_enabled)


@router.delete(
    "/v1/cache/{appid}",
    response_model=CacheDeletionOut,
    # WP TOOL-APPS-2b: the grouped-delete fields are only set for a grouped
    # request, so a plain response keeps exactly its pre-2b shape.
    response_model_exclude_unset=True,
)
def delete_cached_app(
    request: Request,
    appid: int = Path(ge=1, description="Steam app id whose depots to delete"),
    with_packages_raw: str | None = Query(
        None, alias="with_packages", description=_WITH_PACKAGES_DOC
    ),
    members_raw: str | None = Query(
        None,
        alias="members",
        description=(
            "Required with with_packages=true: the comma-separated app ids the "
            "confirmation listed (delete-plan members[].appid, parent included). "
            "409 when the server's delete set differs."
        ),
    ),
    open_db: DbOpener = Depends(db_opener),
    size_cache: SizeCache = Depends(get_size_cache),
    cache_root: str = Depends(get_cache_root),
) -> CacheDeletionOut:
    """Delete one game's depot directories from the cache (plan §4/§6).

    Order of decisions, all made before anything is removed:

    1. ``404`` if the app has no row in ``apps`` — nothing to delete.
    2. ``404`` if the app maps no depots — nothing to delete (checked before
       the job guard on purpose: "there is nothing here" is a more useful
       answer than "come back later").
    3. ``409`` if a prefill job for this app is ``queued`` or ``running``.
       Deleting the depots a download is writing into is a footgun: the job
       would keep refilling them and the reported freed bytes would be
       fiction. The client retries after the job finishes.
    4. ``500`` if the cache-root guards refuse (``deletion.resolve_depot_root``
       — empty / filesystem root / no ``depot/`` directory). Nothing was
       deleted.
    5. Otherwise ``200``: exclusive depots deleted, protected shared depots
       reported and kept, per-depot failures reported in ``failed`` — AND
       (ADR-0003 addendum) a shared depot deleted anyway when every co-owning
       app currently has no cache content (a "last cached remnant"), flagged
       distinctly in ``deleted_depots`` via ``shared_with_uncached`` rather
       than merged with an ordinary exclusive deletion.

    Deliberately **200, not 207**, when some depots failed: the response body
    already distinguishes deleted / skipped / failed per depot, and a
    multi-status code would force every client to special-case a status it
    cannot act on differently anyway. The app's own state does carry the
    outcome, though: ``apps.status`` becomes ``error`` if **anything** failed
    and ``idle`` only on a fully clean run, and ``last_prefill_at`` is cleared
    in both cases.

    The deletion runs in the request thread (FastAPI's threadpool, this being a
    sync endpoint), which is right for the typical size of a depot tree and is
    what lets the response report exactly what happened. A client that
    disconnects mid-deletion does not abort it — the work completes; only the
    report is lost. No database transaction is held across the filesystem work:
    the connection is opened for the read, closed, and re-opened afterwards for
    the status reset.

    **The depot→app mapping rows are KEPT.** The mapping is knowledge (which
    depots belong to this game), not cache state — see api/README.md for the
    decision and its consequences.

    **``apps.needs_force`` (schema v5, WP 3.4, ADR-0006 decision 2)** is set
    to 1 whenever this request changed or left uncertain what is on disk for
    this app — anything in ``deleted_depots`` (including the ALREADY-ABSENT
    case) or ``failed`` — so the next prefill for this app runs with
    ``--force`` rather than trusting SteamPrefill's own now-stale bookkeeping.
    Left untouched when nothing exclusive existed to delete (every mapped
    depot was shared and protected). This is also the documented way an
    operator forces a re-fill: ``DELETE`` then ``POST /v1/prefill`` — no
    separate "force" flag on the prefill API itself (see api/README.md's
    "needs_force lifecycle").

    **ADR-0003 addendum — last cached remnants.** A shared depot is not
    "never delete": it may be removed when NO co-owning app currently has
    cache content (conservatively judged — see ``deletion.plan_deletion`` and
    ``deletion.delete_app_depots``), because otherwise its bytes become
    unreclaimable the moment every co-owner has independently been deleted
    (mapping rows survive deletion by design, so the old "shared -> never
    delete" rule kept such a depot forever, attributed to no app). Every
    co-owner appid that was uncached at the moment ITS depot was removed
    (or removal was attempted and failed, or it was found already absent)
    gets ``apps.needs_force = 1`` — same honesty rule as the requesting app's
    own reset above, because a depot they still map just changed under them —
    but their ``status``/``last_prefill_at`` are left untouched (see
    ``deletion.set_needs_force_for_remnant_co_owners``).

    **``?with_packages=true`` (WP TOOL-APPS-2b, ADR-0022 §4).** Deletes the
    app together with its content packages: the server builds the delete set
    (``group_delete.select_group``: grouped children whose OWN type is dlc,
    config, music, video or demo; never a fixed-list tool app). Members do
    not protect each other's depots; every app outside the set protects
    under the rule above, through the same ``deletion.DeleteSet`` in the plan
    and in the execute-time recheck. ``409`` if any member has an in-flight
    job; each package that lost depots is reset like a single deletion. The
    response adds ``with_packages``, ``members`` and ``kept_children``; a
    plain request (no parameter, or ``false``) is unchanged, field for field.
    Preview: ``GET /v1/cache/{appid}/delete-plan``.
    """
    with_packages = _parse_with_packages(with_packages_raw)
    confirmed = _parse_members(members_raw, with_packages=with_packages)
    selection, rows, co_owner_states, active_jobs = _load_delete_request(
        open_db, appid, with_packages=with_packages,
        oracle_enabled=_oracle_enabled(request),
    )
    delete_set = selection.delete_set
    # WP TOOL-APPS-2b: the user confirmed a list; the server's own set must be
    # exactly that list, or nothing is deleted (a package classified between
    # the preview and the click must be shown before it is removed).
    if confirmed is not None and confirmed != delete_set.members:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": (
                    "The apps this delete would remove differ from the confirmed "
                    "list. Nothing was deleted; fetch the delete plan again and "
                    "confirm the current list."
                ),
                "server_members": sorted(delete_set.members),
                "confirmed_members": sorted(confirmed),
            },
        )
    # WP TOOL-APPS-2b: the oldest in-flight job of the parent first, then of
    # each package -- every member is guarded, with today's message.
    active_member, active_job = active_jobs[0] if active_jobs else (appid, None)

    if active_job is not None:
        # Check-then-act, stated honestly: a job enqueued in the microseconds
        # after this check would still race the deletion. Single-worker
        # operation keeps the window tiny and the consequence is benign (the
        # job refills what was deleted), so this stays a guard rather than a
        # lock that would have to be held across the filesystem work.
        # The label follows the job's own type (WP 3.8): GC jobs share this
        # queue, so "Prefill job N" would now be a lie for half the cases —
        # and an operator who reads "prefill" and goes looking for a download
        # that isn't running has been sent the wrong way.
        job_label = (
            "GC" if str(active_job["type"]) == jobs.JOB_TYPE_GC else "Prefill"
        )
        # WP 3.12: a PAUSED job is in ACTIVE_STATUSES too, and the reason it
        # blocks a deletion is a different one worth spelling out — there is no
        # write in flight, but the depots hold the partial download the pause
        # exists to preserve, and resume would have to fetch it all again.
        if str(active_job["status"]) == jobs.STATUS_PAUSED:
            why = (
                "A paused prefill has partially downloaded content in these "
                "depots and resuming it continues from what is on disk, so "
                "deleting them now would throw that progress away. Resume the "
                "job and let it finish, or cancel it with "
                f"DELETE /v1/jobs/{active_job['id']}, then delete."
            )
        else:
            why = (
                "Deleting depots while they are being downloaded (or "
                "garbage-collected) would delete under an active write — retry "
                "once that job has finished (poll GET /v1/jobs/{id})."
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{job_label} job {active_job['id']} for app {active_member} is "
                f"{active_job['status']}. {why}"
            ),
        )

    plan = deletion.plan_deletion(rows, _target(delete_set), co_owner_states)

    try:
        depot_root = deletion.resolve_depot_root(cache_root)
    except deletion.DeletionGuardError as exc:
        logger.error("cache-delete appid=%s REFUSED (cache root guard): %s", appid, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc

    if with_packages:
        logger.info(
            "cache-delete appid=%s grouped delete: members=%s kept_children=%s",
            appid, sorted(delete_set.members),
            [child.appid for child in selection.kept_children],
        )
    logger.info(
        "cache-delete appid=%s starting: depot_root=%s exclusive=%s remnant=%s "
        "shared=%s unusable_rows=%d",
        appid, depot_root, plan.exclusive,
        [depot.depotid for depot in plan.remnant],
        [depot.depotid for depot in plan.shared], len(plan.unusable),
    )
    deletion.log_kept_depots(appid, plan.shared)

    def current_co_owners(depotid: int) -> list[deletion.CoOwner]:
        """Re-read one depot's other owners immediately before it is removed.

        A fresh short-lived connection per depot, opened and closed inside this
        endpoint's own thread (the ``deps.db_opener`` rule) and holding no
        transaction — this is the execute-time half of the shared-depot
        protection, see ``deletion.delete_app_depots``.
        """
        with open_db() as conn:
            return deletion.load_co_owners(conn, depotid, _target(delete_set))

    # Both plan.exclusive AND plan.remnant go through the identical execute-
    # time recheck below — delete_app_depots does not trust either
    # classification, it re-derives the outcome from fresh co-owner data
    # (ADR-0003 addendum's TOCTOU close, see that function's docstring).
    # Sorted (WP 3.5 review nit): `deleted`/`failed` are built by
    # `delete_app_depots` in the order it iterates this list, and
    # `plan.exclusive + [...]` is not globally sorted by depotid on its own
    # (each half is, the concatenation isn't) — sorting here keeps
    # `deleted_depots` in the same depotid order `skipped_shared` already is.
    candidate_depotids = sorted(
        plan.exclusive + [depot.depotid for depot in plan.remnant]
    )
    deleted, failed, late_shared = deletion.delete_app_depots(
        depot_root, appid, candidate_depotids, co_owners=current_co_owners
    )
    failed = plan.unusable + failed
    skipped_shared = sorted(
        plan.shared + late_shared, key=lambda depot: depot.depotid
    )

    # ADR-0003 addendum consequence #2: every co-owner a remnant depot was
    # shared with — whether the removal succeeded, failed mid-tree, or found
    # the depot already absent — needs its own needs_force set, because a
    # depot IT still maps just changed (or its state became uncertain) out
    # from under it. Gathered from both deleted and failed so a partial
    # failure still flags the co-owners of the depots that WERE touched.
    remnant_co_owner_appids: set[int] = set()
    for depot in deleted:
        remnant_co_owner_appids.update(depot.shared_with_uncached)
    for depot in failed:
        remnant_co_owner_appids.update(depot.shared_with_uncached)

    # Disk content changed (or may have) — the size cache must not keep serving
    # pre-deletion sizes for up to VAULT_SIZE_CACHE_TTL seconds. This is the
    # invalidation hook sizes.SizeCache exports for exactly this case (WP 1.5).
    size_cache.invalidate()

    # Any failure at all -> 'error', never 'idle' (WP 1.6 review, blocker).
    # "Failed" does NOT mean "untouched": shutil.rmtree deletes files as it
    # walks and only then raises, so a failed depot is typically *half* deleted
    # (measured: 150 of 300 chunk files gone, 0 bytes reported freed). Leaving
    # such an app at 'done' with its last_prefill_at intact would show a green
    # badge on a half-destroyed game and — worse — Phase 3's staleness check
    # compares manifest ids, not file counts, so it would never re-fill it.
    # 'error' is the honest state: the operator sees it, and a re-prefill (which
    # runs with --force) repairs the cache. last_prefill_at is cleared either
    # way, because it is no longer true either way.
    member_depots = _member_depots(rows, delete_set.members)
    deleted_ids = {d.depotid for d in deleted}
    failed_ids = {f.depotid for f in failed if f.depotid}
    # WP TOOL-APPS-2b: the parent's outcome comes from its OWN depots, like
    # each package's below. A failure whose depot id is unusable (0) cannot be
    # attributed and counts for the parent. For a plain delete every
    # candidate is the parent's, so this equals the pre-2b "any failure".
    unattributed_failure = any(not f.depotid for f in failed)
    parent_failed = bool(member_depots[appid] & failed_ids) or unattributed_failure
    new_status = jobs.STATUS_ERROR if parent_failed else jobs.STATUS_IDLE
    # WP 3.4 / ADR-0006 decision 2: this request changed or left uncertain
    # what is on disk for this app the moment ANYTHING landed in `deleted`
    # (includes the ALREADY-ABSENT case — the mapping claimed this depot was
    # here and it wasn't) or in `failed` (cache state unknown after a partial
    # failure). The "everything mapped turned out to be shared, nothing
    # exclusive to touch" case leaves both empty, so needs_force is left
    # exactly as it was — nothing on disk changed for this app.
    set_needs_force = (
        bool(member_depots[appid] & (deleted_ids | failed_ids)) or unattributed_failure
    )
    package_ids = sorted(delete_set.members - {appid})
    with open_db() as conn:
        # Two separate statements, each committing on its own (both
        # `reset_app_after_deletion` and `set_needs_force_for_remnant_co_owners`
        # call `conn.commit()` internally) — a crash between them would leave
        # the requesting app's reset durable but the remnant co-owners'
        # needs_force not yet set. Accepted, not closed, for the same reason
        # WP 3.4 accepted the equivalent window around `reset_app_after_deletion`
        # itself: the filesystem work (which already happened, durably, before
        # either commit) is the side effect that actually matters, and the
        # worst case here is a co-owner's next prefill trusting SteamPrefill's
        # own bookkeeping one run longer than ideal, not data loss or a wrong
        # deletion — the depot in question is already gone either way.
        deletion.reset_app_after_deletion(
            conn, appid, new_status, set_needs_force=set_needs_force
        )
        # WP TOOL-APPS-2b: every package that lost (or may have lost) one of
        # its own depots is reset like a single deletion: status 'error' if
        # one of its depots failed, else 'idle'; last_prefill_at cleared;
        # needs_force set. A package none of whose depots was touched (all
        # kept as shared) is left alone.
        for member in package_ids:
            touched = member_depots[member] & (deleted_ids | failed_ids)
            if not touched:
                continue
            deletion.reset_app_after_deletion(
                conn,
                member,
                jobs.STATUS_ERROR if member_depots[member] & failed_ids else jobs.STATUS_IDLE,
                set_needs_force=True,
            )
        deletion.set_needs_force_for_remnant_co_owners(conn, remnant_co_owner_appids)
        # WP CACHE-COMPLETE-1a: the stored completeness of these apps and of
        # every depot touched is unknown now (until the next check).
        cache_completeness.forget(
            conn,
            appids=[appid, *package_ids],
            depotids=deleted_ids | failed_ids,
        )

    total_bytes_freed = sum(depot.size_bytes_freed for depot in deleted)
    remnant_deleted_count = sum(1 for d in deleted if d.shared_with_uncached)
    logger.info(
        "cache-delete appid=%s finished: deleted=%d (of which %d last-remnant) "
        "skipped_shared=%d (of which %d late) failed=%d bytes_freed=%d; status "
        "set to '%s', last_prefill_at cleared, needs_force=%s; remnant "
        "co-owners flagged=%s; mapping rows kept",
        appid, len(deleted), remnant_deleted_count, len(skipped_shared),
        len(late_shared), len(failed), total_bytes_freed, new_status,
        "1" if set_needs_force else "unchanged", sorted(remnant_co_owner_appids),
    )

    grouped: dict[str, object] = {}
    if with_packages:
        grouped = {
            "with_packages": True,
            "members": [
                DeleteMemberOut(
                    appid=member.appid,
                    role=member.role,
                    name=member.name,
                    app_type=member.app_type,
                    deleted_depots=sorted(member_depots[member.appid] & deleted_ids),
                    failed_depots=sorted(member_depots[member.appid] & failed_ids),
                )
                for member in selection.members
            ],
            "kept_children": _kept_children_out(selection),
        }
    return CacheDeletionOut(
        appid=appid,
        deleted_depots=[
            DeletedDepotOut(
                depotid=d.depotid,
                size_bytes_freed=d.size_bytes_freed,
                shared_with_uncached=d.shared_with_uncached,
            )
            for d in deleted
        ],
        skipped_shared=[
            SkippedSharedDepotOut(depotid=s.depotid, shared_with=s.shared_with)
            for s in skipped_shared
        ],
        failed=[FailedDepotOut(depotid=f.depotid, error=f.error) for f in failed],
        total_bytes_freed=total_bytes_freed,
        **grouped,
    )


def _kept_children_out(selection: group_delete.GroupSelection) -> list[KeptChildOut]:
    return [
        KeptChildOut(
            appid=child.appid, name=child.name, app_type=child.app_type, reason=child.reason
        )
        for child in selection.kept_children
    ]


# --------------------------------------------------------------------------
# GET /v1/cache/{appid}/delete-plan -- the confirmation dialog's preview
# --------------------------------------------------------------------------


class PlanMemberOut(BaseModel):
    appid: int
    role: str
    name: str | None
    app_type: str | None
    #: Every depot this member maps (deleted, kept or unusable alike).
    depot_ids: list[int]


class PlannedDepotOut(BaseModel):
    depotid: int
    #: Bytes on disk now (size cache), null when nothing is on disk.
    size_bytes: int | None
    #: Which members of the delete set map this depot.
    owners: list[int]
    #: Non-empty for a planned "last cached remnant" (ADR-0003 addendum).
    shared_with_uncached: list[int] = []


class BlockingJobOut(BaseModel):
    appid: int
    job_id: int
    type: str
    status: str


class DeletePlanOut(BaseModel):
    """What ``DELETE /v1/cache/{appid}`` with the same ``with_packages`` would
    do right now (WP TOOL-APPS-2b). Read-only: nothing is deleted, no state
    changes. The deletion itself re-checks every depot immediately before
    removing it, so a depot can still move to ``skipped_shared`` if another
    app gains content in between; it never deletes more than planned."""

    appid: int
    with_packages: bool
    members: list[PlanMemberOut]
    kept_children: list[KeptChildOut]
    delete_depots: list[PlannedDepotOut]
    skipped_shared: list[SkippedSharedDepotOut]
    #: Mapping rows with an unusable depot id (poisoned database): the
    #: deletion reports them in ``failed``.
    unusable: list[FailedDepotOut]
    #: Sum of ``delete_depots[].size_bytes`` (each depot once).
    total_bytes: int
    #: In-flight jobs of members: the deletion answers ``409`` while this is
    #: non-empty.
    blocked_by: list[BlockingJobOut]


@router.get("/v1/cache/{appid}/delete-plan", response_model=DeletePlanOut)
def plan_cached_app_deletion(
    request: Request,
    appid: int = Path(ge=1, description="Steam app id whose deletion to preview"),
    with_packages_raw: str | None = Query(
        None, alias="with_packages", description=_WITH_PACKAGES_DOC
    ),
    open_db: DbOpener = Depends(db_opener),
    size_cache: SizeCache = Depends(get_size_cache),
    cache_root: str = Depends(get_cache_root),
) -> DeletePlanOut:
    """Preview of ``DELETE /v1/cache/{appid}`` for the confirmation dialog.

    Same inputs as the deletion (``_load_delete_request``), same delete set
    and the same ``deletion.plan_deletion``: the dialog lists exactly the
    apps and depots the deletion plans. ``404`` cases are the deletion's own;
    an in-flight job is reported in ``blocked_by`` instead of a ``409``.
    """
    with_packages = _parse_with_packages(with_packages_raw)
    selection, rows, co_owner_states, active_jobs = _load_delete_request(
        open_db, appid, with_packages=with_packages,
        oracle_enabled=_oracle_enabled(request),
    )
    delete_set = selection.delete_set
    plan = deletion.plan_deletion(rows, _target(delete_set), co_owner_states)
    depot_bytes = size_cache.get(cache_root).depot_bytes
    member_depots = _member_depots(rows, delete_set.members)
    remnants = {depot.depotid: depot.shared_with_uncached for depot in plan.remnant}

    planned = [
        PlannedDepotOut(
            depotid=depotid,
            size_bytes=depot_bytes.get(depotid),
            owners=sorted(m for m, depots in member_depots.items() if depotid in depots),
            shared_with_uncached=remnants.get(depotid, []),
        )
        for depotid in sorted(set(plan.exclusive) | set(remnants))
    ]
    return DeletePlanOut(
        appid=appid,
        with_packages=with_packages,
        members=[
            PlanMemberOut(
                appid=member.appid,
                role=member.role,
                name=member.name,
                app_type=member.app_type,
                depot_ids=sorted(member_depots[member.appid]),
            )
            for member in selection.members
        ],
        kept_children=_kept_children_out(selection),
        delete_depots=planned,
        skipped_shared=[
            SkippedSharedDepotOut(depotid=s.depotid, shared_with=s.shared_with)
            for s in plan.shared
        ],
        unusable=[FailedDepotOut(depotid=f.depotid, error=f.error) for f in plan.unusable],
        total_bytes=sum(d.size_bytes or 0 for d in planned),
        blocked_by=[
            BlockingJobOut(
                appid=member, job_id=int(job["id"]), type=str(job["type"]),
                status=str(job["status"]),
            )
            for member, job in active_jobs
        ],
    )


# --------------------------------------------------------------------------
# POST /v1/cache/{appid}/gc — plan §6, ADR-0007
# --------------------------------------------------------------------------

#: The two words this API uses for the two modes, in the response and nowhere
#: else in logic — ``execute`` is the boolean that decides anything.
MODE_DRY_RUN = "dry-run"
MODE_EXECUTE = "execute"


class GcRequest(BaseModel):
    """Body of ``POST /v1/cache/{appid}/gc``. Optional — omitting it is a dry run.

    ``extra="forbid"`` for the same reason as ``PrefillRequest`` and the
    mapping endpoints, and with more at stake: a typo'd field name must 422
    rather than be silently dropped. (Dropping ``{"exceute": true}`` would land
    on the *safe* side — a dry run — but an operator who then believes the
    cache was cleaned is exactly as misinformed as one who did not want a
    deletion, and a 422 is free.)
    """

    model_config = ConfigDict(extra="forbid")

    #: ``StrictBool``, not ``bool``: pydantic's lax mode would accept
    #: ``"true"``, ``"yes"``, ``"on"`` and ``1`` here (docs/LEARNINGS.md
    #: records the int-field version of the same coercion). For the one flag
    #: that turns a report into a deletion, the only accepted spelling is a
    #: literal JSON ``true`` — a client that sends a string got its request
    #: rejected rather than half-understood.
    execute: StrictBool = False


class GcJobRef(BaseModel):
    """Which job was queued, and — unmistakably — in which mode."""

    appid: int
    job_id: int
    status: str
    #: Always ``"gc"``; present so a client that stores this response has the
    #: same ``type`` value ``GET /v1/jobs`` will report for the job.
    type: str
    #: ``"dry-run"`` or ``"execute"``. Redundant with ``execute`` below on
    #: purpose: the boolean is what the code acts on, the word is what a human
    #: reads in a terminal, and a UI that shows either one cannot get the mode
    #: wrong by accident.
    mode: str
    #: True only when this job will actually delete files.
    execute: bool
    #: True when an existing queued/running GC job **in the same mode** was
    #: returned instead of a second one being created (see
    #: ``jobs.enqueue_gc``: a dry run and an execute run never dedupe into
    #: each other).
    deduplicated: bool


@router.post(
    "/v1/cache/{appid}/gc",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=GcJobRef,
)
def create_gc_job(
    appid: int = Path(ge=1, description="Steam app id whose depots to collect"),
    body: GcRequest | None = None,
    open_db: DbOpener = Depends(db_opener),
) -> GcJobRef:
    """Queue a garbage-collection job for one game (plan §6, ADR-0007). 202.

    > For one depot, the chunks worth keeping are the UNION of the current
    > manifests of every app that has a claim on that depot; everything else in
    > that depot's ``chunk/`` directory is an orphan left behind by a game
    > update.

    **Dry run by default (ADR-0007).** With no body, or with
    ``{"execute": false}``, the job plans and reports and deletes **nothing**.
    Deletion requires the explicit opt-in ``{"execute": true}`` — a literal
    JSON boolean, see ``GcRequest.execute``. The mode is stored on the job row
    (``jobs.gc_execute``, schema v7), so it survives the queue wait and a
    restart, and an old job row still says which mode it ran in.

    Order of decisions:

    1. ``404`` if the app has no row in ``apps`` — vault-api tracks no such
       app, so there is nothing to collect.
    2. ``404`` if the app maps no depots — nothing to collect (same reasoning
       and same wording style as ``DELETE /v1/cache/{appid}``).
    3. ``202`` otherwise, with the job reference. The work happens on the
       single background worker.

    **No ``409`` for an active job, and that is not an oversight.** The
    equivalent guard on ``DELETE /v1/cache/{appid}`` exists because that
    endpoint deletes in the *request thread*, so it really can race a running
    download. GC runs *on the worker*, which executes one job at a time, so a
    GC job physically cannot overlap a prefill of the same app — it simply
    waits its turn. Serialization is the guard (ADR-0007: "runs as a queued
    job, serialized with prefills on the single worker").

    **The plan is built when the job runs, never here.** This endpoint stores
    an app id and a mode; the worker plans against a fresh database read and a
    fresh filesystem scan at execution time. A game that updates while the job
    sits in the queue is therefore collected against its new manifest — see
    ``vault_api/gc_execute.py``'s "TOCTOU" section.

    What a GC job does to app state: **nothing** to ``apps.status`` or
    ``last_prefill_at``; an *executing* run that actually reclaimed chunks
    invalidates the size cache and sets ``apps.needs_force = 1`` for every app
    mapped to a depot it took chunks from. The full argument (including why
    the "obvious" answer of not setting it was rejected) is in
    ``vault_api/gc_execute.py``'s module docstring and in api/README.md.
    """
    execute = bool(body.execute) if body is not None else False

    with open_db() as conn:
        app_row = conn.execute(
            "SELECT appid FROM apps WHERE appid = ?", (appid,)
        ).fetchone()
        if app_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"Unknown appid {appid} — vault-api tracks no such app, so there "
                    "is nothing to garbage-collect."
                ),
            )

        if not deletion.load_mapping_rows(conn, appid):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"App {appid} has no depot mappings, so there is nothing to "
                    "garbage-collect. Mappings are created by a successful prefill "
                    "or via PUT /v1/mapping/{depotid}."
                ),
            )

        job, created = jobs.enqueue_gc(conn, appid, execute=execute)

    logger.info(
        "cache-gc appid=%s queued job %s in %s mode (deduplicated=%s)",
        appid, job["id"], MODE_EXECUTE if execute else MODE_DRY_RUN, not created,
    )
    return GcJobRef(
        appid=appid,
        job_id=int(job["id"]),  # type: ignore[arg-type]
        status=str(job["status"]),
        type=str(job["type"]),
        mode=MODE_EXECUTE if execute else MODE_DRY_RUN,
        execute=execute,
        deduplicated=not created,
    )


# --------------------------------------------------------------------------
# Repair of wrong-size chunk files (WP CACHE-REPAIR-1)
# --------------------------------------------------------------------------


class RepairRequest(BaseModel):
    """Body of ``POST /v1/cache/{appid}/repair``. Optional: omitting it is a
    dry run, exactly like ``POST /v1/cache/{appid}/gc``."""

    model_config = ConfigDict(extra="forbid")

    #: A literal JSON ``true`` is the only way to delete (``StrictBool``).
    execute: StrictBool = False


class RepairDepotOut(BaseModel):
    depotid: int
    manifestid: str
    damaged_chunks: int
    damaged_bytes: int


class RepairOut(BaseModel):
    appid: int
    #: ``"dry-run"`` or ``"execute"``.
    mode: str
    execute: bool
    #: Wrong-size chunk files found in the app's target manifests' depots.
    damaged_chunks: int
    damaged_bytes: int
    depots: list[RepairDepotOut]
    #: Depots whose target manifest could not be read (never guessed at).
    unreadable_depots: list[int]
    #: The plan is larger than ``max_files``: refused, nothing deleted.
    exceeds_cap: bool
    max_files: int
    #: Execute only: files actually deleted, and why others were left alone.
    removed_chunks: int = 0
    removed_bytes: int = 0
    skipped: dict[str, int] = {}
    #: Execute only: the forced prefill queued after the deletion
    #: (``null`` when nothing was removed).
    job_id: int | None = None
    job_status: str | None = None
    job_deduplicated: bool | None = None


@router.post("/v1/cache/{appid}/repair", response_model=RepairOut)
def repair_cached_app(
    request: Request,
    response: Response,
    appid: int = Path(ge=1, description="Steam app id whose damaged chunks to repair"),
    body: RepairRequest | None = None,
    open_db: DbOpener = Depends(db_opener),
    size_cache: SizeCache = Depends(get_size_cache),
) -> RepairOut:
    """Repair a game whose cache holds chunk files of the wrong size (WP
    CACHE-REPAIR-1; they are what CACHE-COMPLETE-1a counts as damaged). A
    forced prefill cannot fix them -- vault-core serves the existing file as a
    HIT -- so the damaged files are deleted and a forced run is queued, which
    misses on them and stores fresh copies. User-triggered only.

    **Dry run by default.** No body or ``{"execute": false}``: ``200`` with
    what would be deleted (counts per depot), nothing touched. ``{"execute":
    true}`` (literal boolean): deletes, then queues the forced job, ``202``
    (``200`` when nothing was damaged or removed: no-op, no job).

    Order of decisions: ``404`` unknown app / no depot mappings; ``500`` the
    cache-root guards refuse; nothing damaged: ``200`` no-op; ``409`` a
    queued/running/paused job for the app, or for another app that maps one
    of the damaged depots (also for dry runs, so the preview never promises
    what execute would refuse); ``409`` the app's last prefill ended
    ``not_owned`` (a forced run could not store fresh copies); more than
    ``max_files`` damaged files: ``422`` on execute (``exceeds_cap: true`` in
    the dry-run report), nothing deleted.

    Only chunk files named in the app's current target manifests whose size
    differs from the manifest's are candidates; each is re-checked right before
    its unlink (``cache_repair.execute_repair``). Files behind a symlink, or
    whose size matches or changed meanwhile, are left alone and counted in
    ``skipped``.
    """
    execute = bool(body.execute) if body is not None else False
    settings = request.app.state.settings
    with open_db() as conn:
        if conn.execute("SELECT 1 FROM apps WHERE appid = ?", (appid,)).fetchone() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown appid {appid} -- vault-api tracks no such app.",
            )
        if not deletion.load_mapping_rows(conn, appid):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"App {appid} has no depot mappings, so there is nothing to repair.",
            )
        try:
            depot_root = deletion.resolve_depot_root(settings.cache_root)
        except deletion.DeletionGuardError as exc:
            logger.error("cache-repair appid=%s REFUSED (cache root guard): %s", appid, exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
            ) from exc
        plan = cache_repair.plan_repair(conn, settings, appid, depot_root=depot_root)

        out = RepairOut(
            appid=appid,
            mode="execute" if execute else "dry-run",
            execute=execute,
            damaged_chunks=plan.damaged_count,
            damaged_bytes=plan.damaged_bytes,
            depots=[
                RepairDepotOut(
                    depotid=d.depotid,
                    manifestid=d.manifestid,
                    damaged_chunks=len(d.chunks),
                    damaged_bytes=sum(c.found_size for c in d.chunks),
                )
                for d in plan.depots
            ],
            unreadable_depots=plan.unreadable_depots,
            exceeds_cap=plan.exceeds_cap,
            max_files=cache_repair.MAX_REPAIR_FILES,
        )
        if plan.damaged_count == 0:
            return out

        affected = cache_repair.apps_for_depots(conn, (d.depotid for d in plan.depots))
        for owner in sorted(affected | {appid}):
            active = jobs.active_job_for_app(conn, owner)
            if active is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"Job {active['id']} for app {owner} is {active['status']}; "
                        "repairing chunk files under an active job would race it. "
                        "Retry once it has finished (poll GET /v1/jobs/{id})."
                    ),
                )
        if appid in jobs.not_owned_appids(conn):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "The last prefill of this app ended not_owned, so a forced run "
                    "could not store fresh copies; nothing is deleted."
                ),
            )
        if not execute:
            return out
        if plan.exceeds_cap:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"{plan.damaged_count} damaged chunk files exceed the repair cap of "
                    f"{cache_repair.MAX_REPAIR_FILES}; nothing was deleted. That many "
                    "mismatches point at a wrong target manifest or a broken cache."
                ),
            )

        result = cache_repair.execute_repair(plan, depot_root)
        out.removed_chunks = result.removed
        out.removed_bytes = result.removed_bytes
        out.skipped = result.skipped
        if result.removed:
            size_cache.invalidate()
            try:
                cache_completeness.remeasure_app(conn, settings, appid)
            except Exception:  # noqa: BLE001 - the figure refreshes at the next check
                logger.exception("cache-repair appid=%s: remeasure failed", appid)
            deletion.set_needs_force(conn, [appid])
            job, created = jobs.enqueue_prefill(conn, appid)
            out.job_id = int(job["id"])  # type: ignore[arg-type]
            out.job_status = str(job["status"])
            out.job_deduplicated = not created
            response.status_code = status.HTTP_202_ACCEPTED
    return out
