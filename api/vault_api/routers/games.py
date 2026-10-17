"""Games endpoints (plan §6): GET /v1/games, GET /v1/games/{appid}.

Auth is attached at the router level (secure-by-default pattern, see
api/README.md "Auth" section) — every route added here is authenticated
automatically.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel

from vault_api import (
    app_classification,
    cache_completeness,
    cover_art,
    cover_cache,
    depot_manifests,
    jobs,
    library_meta,
    oracle,
    scheduler,
    settings_store,
    tool_apps,
)
from vault_api.auth import require_api_key
from vault_api.config import Settings
from vault_api.deps import DbOpener, db_opener, get_cache_root, get_size_cache
from vault_api.sizes import SizeCache, app_size_bytes

router = APIRouter(dependencies=[Depends(require_api_key)], tags=["games"])


class InstalledOn(BaseModel):
    """One client currently reporting this app installed (WP AG-1).

    **Freshness is not implied, it is enforced before this entry ever
    exists.** This list is built from ``scheduler.fresh_client_snapshots`` —
    the SAME freshness gate ``compute_targets`` applies before trusting a
    client's report for sweep targeting (readable snapshot, parseable
    timestamp, newer than ``VAULT_SCHEDULE_CLIENT_STALE_DAYS``). A client
    whose latest report is stale, corrupt, or unparseable simply does not
    appear here — the same way it does not appear in a sweep's target set.
    So "installed on Zeus" in this list is exactly as trustworthy as "the
    scheduler would currently prefill this for Zeus", never a stale claim
    presented as current (see the module the function lives in for the full
    reasoning, and ``docs/LEARNINGS.md``'s "two call sites, same predicate"
    entry for why this reuses that function instead of its own check).

    ``reported_at`` is included even though every entry already passed the
    freshness gate: a frontend that wants to say "as of 14:32" or sort by
    recency needs the real timestamp, not just a yes/no.
    """

    client_id: str
    reported_at: str


def _fresh_snapshots_now(
    conn: sqlite3.Connection, base_settings: Settings, now: datetime
) -> list[scheduler.FreshClientSnapshot]:
    """The freshness-gated snapshots ``installed_on`` is built from, resolved
    against the SAME db>env>default settings the scheduler's own tick uses
    (WP AG-1 review round 1, B1 blocker).

    ``base_settings`` must be the boot snapshot (``request.app.state.
    scheduler.settings``), NOT an already-resolved object -- resolution
    happens HERE, on every call, through ``settings_store.
    effective_settings(conn, base_settings)``. Reading a boot-time settings
    object directly (what round 1 shipped) freezes
    ``schedule_client_stale_days`` at whatever it was when the process
    started: that key is overridable at runtime (``PATCH /v1/settings``,
    ``applies: "next_sweep"``) and the scheduler tick already re-resolves it
    on every tick (``vault_api/scheduler.py``'s own tick-time resolution) --
    so a boot snapshot here would let this endpoint claim "installed on
    zeus" for a report the scheduler, one PATCH later, has already started
    refusing to trust. Same fix shape as ``routers/schedule.py``'s
    ``GET /v1/schedule``, which resolves through this exact function for the
    identical reason.

    ``now`` is likewise the scheduler's OWN clock (``PrefillScheduler.now()``,
    injectable in tests) rather than a fresh ``datetime.now()`` call here --
    one clock, so a frozen-clock test can control both the sweep's staleness
    decision and this endpoint's, instead of two clocks that could disagree
    by however long the request took to reach this line.
    """
    settings = settings_store.effective_settings(conn, base_settings)
    fresh, _excluded = scheduler.fresh_client_snapshots(
        conn, now, settings.schedule_client_stale_days
    )
    return fresh


def _installed_on_by_appid(
    conn: sqlite3.Connection, base_settings: Settings, now: datetime
) -> dict[int, list[InstalledOn]]:
    """Every fresh client's latest snapshot, re-keyed by appid (WP AG-1).

    One query for the distinct client list plus one indexed lookup per
    client (``scheduler.fresh_client_snapshots`` -> ``agent_reports.
    latest_snapshot``) -- cost scales with the number of GAMING MACHINES that
    have ever reported, not with the number of tracked apps, so this stays
    cheap next to a library of any size and adds no per-app query (the N+1
    shape the work package flagged to avoid). A vault with zero agents never
    executes the per-client branch at all: the distinct-client query returns
    no rows and this function returns ``{}`` immediately.
    """
    fresh = _fresh_snapshots_now(conn, base_settings, now)
    by_appid: dict[int, list[InstalledOn]] = {}
    for snapshot in fresh:
        entry = InstalledOn(client_id=snapshot.client_id, reported_at=snapshot.reported_at)
        for appid in snapshot.appids:
            by_appid.setdefault(appid, []).append(entry)
    return by_appid


def _installed_on_for_appid(
    conn: sqlite3.Connection, base_settings: Settings, now: datetime, appid: int
) -> list[InstalledOn]:
    """Same freshness-gated source as ``_installed_on_by_appid``, filtered to
    ONE app (review round 1 nitpick): ``GET /v1/games/{appid}`` has no reason
    to build the whole-library dict just to read one key back out of it --
    the per-client scan this still runs is the same cost either way, only
    the re-keying step changes shape.
    """
    fresh = _fresh_snapshots_now(conn, base_settings, now)
    return [
        InstalledOn(client_id=snapshot.client_id, reported_at=snapshot.reported_at)
        for snapshot in fresh
        if appid in snapshot.appids
    ]


class GameMeta(BaseModel):
    """Stored library metadata of one app (WP D23 LIBRARY-META-1b).

    Present on a game only once a fetch succeeded (``GameSummary.meta`` is
    null otherwise: "unknown"). A null field inside means that answer did not
    carry it. ``genres`` are Steam's numeric genre ids (labels are the
    client's job). ``review_percent`` is the overall positive share (0-100),
    ``review_score`` Steam's 0-9 bucket, ``fetched_at`` when it was stored.
    """

    single_player: bool | None = None
    multiplayer: bool | None = None
    coop: bool | None = None
    vr: bool | None = None
    genres: list[int] = []
    review_percent: int | None = None
    review_score: int | None = None
    release_year: int | None = None
    fetched_at: str | None = None


class GameSummary(BaseModel):
    appid: int
    # The name vault-api recorded (owned library, agent, mapping); when it has
    # none, Steam's name for the app from the store lookup or the oracle's
    # stored answer (WP TOOL-APPS-2a, plain text, at most 256 characters, as
    # Steam spells it). Null when no source knows it.
    name: str | None
    status: str
    # WP NOT-OWNED-2: the app's last prefill ended not_owned (fresh, see
    # jobs.not_owned_appids). Independent of `status`: an app that holds cache
    # bytes keeps its cached status and only carries this flag.
    not_owned: bool = False
    last_prefill_at: str | None
    # Timestamp of the last run that CONFIRMED this app current (schema v4,
    # WP 3.3; surfaced here WP 4c). ADR-0006's tier-1 semantics are "current
    # as of <timestamp>", which is only honest once the timestamp is
    # visible. Same null-when-never / verbatim-string style as
    # last_prefill_at (see ``jobs.TIMESTAMP_FORMAT``) -- but written on a
    # MUCH narrower set of outcomes: only a run whose parsed summary was
    # ``Updated == 0 AND Up To Date > 0`` sets it (api/README.md's "Job
    # outcome honesty" table has the full outcome matrix). In particular an
    # ordinary run that actually changed depots (``Updated > 0``), an
    # unparseable summary, an unowned-app run, a failed/cancelled/paused
    # run, and vault-api's own crash-recovery path (``recover_stale_jobs``)
    # all leave it untouched -- so a game can be ``status: "done"`` with
    # this field still null.
    #
    # One place the two fields DIVERGE: ``DELETE /v1/cache/{appid}``
    # (``deletion.reset_app_after_deletion``) unconditionally nulls
    # ``last_prefill_at`` on delete, but deliberately leaves
    # ``last_manifest_check`` alone -- it is not a false claim (the app WAS
    # confirmed current at that timestamp), and ``needs_force``/``status``
    # are what actually gate whether the cache is trusted, not this field.
    # So a game with zero cached bytes can still show a non-null
    # ``last_manifest_check`` from before the deletion.
    last_manifest_check: str | None
    depot_count: int
    # Per-app size (WP 1.5): sum of this app's mapped depots' bytes on disk,
    # from the cached scan (vault_api/sizes.py). Null if unmapped
    # (depot_count == 0) or uncached (mapped but never written to disk yet)
    # — see app_size_bytes for why those two cases are both "unknown", not 0.
    size_bytes: int | None = None
    # Whether the NEXT prefill for this app will run with --force (schema v5,
    # WP 3.4, ADR-0006 decision 2): True for a never-filled app (schema
    # default) or after a deletion that changed/left uncertain what's on
    # disk; False once a successful run has confirmed/refreshed it. Operator
    # visibility only — the API surface for triggering a forced run stays
    # DELETE then POST /v1/prefill, see api/README.md.
    needs_force: bool
    # Phase 4h / WP 4h.1: whether this game's manifest has EVER been observed
    # to change since this vault started watching it, derived from
    # depot_manifests (vault_api/depot_manifests.py's "Change frequency"
    # section has the full definition and reasoning). FOUR possible states:
    #   null                -> no manifest data recorded for this app at all
    #                          (never prefilled since this feature existed,
    #                          or mapped only via the manual fallback).
    #   "insufficient_data"  -> SOME data exists, but either fewer than 2
    #                          observations were recorded for at least one of
    #                          this app's depots, or the app's youngest-
    #                          observed depot has been watched for less than
    #                          14 days. Never "stable" -- a short window or a
    #                          single observation is not evidence of either
    #                          stability or change.
    #   "stable"             -> enough data, and no observed manifest change.
    #   "changed"            -> enough data, and at least one depot's
    #                          manifest has been seen to change AT LEAST ONCE.
    # `null` and `"insufficient_data"` are DELIBERATELY distinct states
    # (pin 2): "we have never looked" and "we looked, but not long/often
    # enough to say" are different honest answers, not the same "unknown".
    #
    # THIS IS NOT A FREQUENCY, despite the field's name (kept for continuity
    # with the plan section's own wording) -- "changed" only means "at least
    # once, ever", nothing decays, so a game that changed three years ago and
    # one that changes weekly are indistinguishable from this field alone.
    # See manifest_days_since_last_change below for the one rate-adjacent
    # fact depot_manifests can actually support.
    manifest_change_frequency: str | None = None
    # Days since the YOUNGEST-observed depot's first recorded observation
    # (the conservative bound across this app's depots -- see
    # depot_manifests.py). Null only alongside manifest_change_frequency
    # being null (no data at all); populated even when the category is
    # "insufficient_data" so a frontend can render the actual number ("only
    # observed for 3 days") instead of a bare label with nothing behind it.
    manifest_observation_days: int | None = None
    # Days since the MOST RECENTLY observed manifest change across this
    # app's own depots. Populated ONLY when manifest_change_frequency ==
    # "changed" -- null for "stable" (never observed one), "insufficient_data"
    # and null (never earned the right to say). This is the field that comes
    # closest to the plan section's "changes every three days" style
    # statement -- "last changed N days ago" is a supportable claim; a true
    # cadence is not (depot_manifests only ever stores the LATEST manifest
    # per depot, never a change history).
    manifest_days_since_last_change: int | None = None
    # Which fresh agent reports currently claim this app installed (WP AG-1).
    # See InstalledOn's docstring for the freshness rule. Empty (never null)
    # when nothing claims it -- including every vault with zero agents.
    installed_on: list[InstalledOn] = []
    # WP API-FIX-4: true for a Steam tool package (vault_api/tool_apps.py,
    # e.g. 228980 "Steamworks Common Redistributables") that vault-api never
    # prefills -- its depots are cached together with the games that use
    # them. UIs show it as a tool package instead of a game: no download or
    # retry action, and an old failed job (``status: "error"``) is not a
    # failure to show. ``status`` and every other field stay the raw truth.
    tool_app: bool = False
    # The tool package's display name from vault_api/tool_apps.py, null for
    # every ordinary app. ``name`` is untouched (it stays the apps-table
    # value, usually null for a tool app); UIs fall back to this one.
    tool_app_name: str | None = None
    # WP API-FIX-5: the real portrait cover (library capsule) from Steam's
    # store item lookup (vault_api/cover_art.py), or null while unknown --
    # not looked up yet, Steam has no store item for it, or the lookup
    # failed. Only https URLs on shared.akamai.steamstatic.com or
    # cdn.akamai.steamstatic.com ever appear here (validated on write AND
    # on this read). UIs fall back to the legacy CDN path when it is null.
    cover_url: str | None = None
    # WP D10 COVER-CACHE-1: the same cover, stored by vault-api and served
    # over the LAN: a path relative to this API's base URL
    # (``/v1/covers/{appid}/{signature}``, loadable without the X-Api-Key
    # header because the signature is the credential; routers/covers.py).
    # Null until vault-api has stored the image. UIs try it first, then
    # ``cover_url``, then the legacy CDN path. ``cover_url`` keeps its
    # meaning, so older clients are unaffected.
    cover_cached_url: str | None = None
    # WP UPD-1: an update is available -- the manifest oracle's current public
    # manifest differs from the one vault-api recorded for this app's cache
    # (``oracle.app_view``'s ``stale`` verdict). ``false`` is "no known update",
    # NOT "up to date": it is also ``false`` when the oracle is off
    # (``VAULT_MANIFEST_ORACLE=off``) or has never been asked about this app --
    # ``update_checked_at`` (null in both cases) tells the two apart. Read from
    # stored rows only; the list never makes a network request. The always-on
    # check and ``POST /v1/updates/check`` keep the rows fresh.
    update_available: bool = False
    # When the oracle last answered for this app (UTC), null if never.
    update_checked_at: str | None = None
    # WP TOOL-APPS-2a (ADR-0022 §1): the TOP-LEVEL game this app is a content
    # package of, or null when it is not grouped -- no source named a parent,
    # the chain is cyclic or longer than 4 links, the top-level parent is not
    # a tracked app, or the app (or a parent on its chain) is on the fixed
    # tool list (``tool_app``). The list stays flat: the server never nests or
    # drops rows, the UIs group. Display data only; no download, GC or delete
    # path reads it.
    parent_appid: int | None = None
    # One of "game", "dlc", "tool", "application", "config", "music",
    # "video", "demo", or null (unknown). Per field, Steam's store item lookup
    # first, else (only while the manifest oracle is on) the oracle's stored
    # answers for cached apps; "dlc" also when only a cached parent's DLC list
    # names the app. Display only: ``tool_app`` stays the fixed list (an
    # ``app_type`` of "tool" never stops a prefill).
    app_type: str | None = None
    # WP TOOL-APPS-2 (ADR-0022 §2, "decided in the API package"): the size of
    # the whole group this row heads -- the union of the depots mapped to this
    # app and to every app whose ``parent_appid`` is this app, each depot
    # counted ONCE (never the sum of the members' ``size_bytes``, which would
    # double-count a depot shared inside the group). Same null rules as
    # ``size_bytes``: null when no depot is mapped or none is on disk yet.
    # For an ungrouped top-level row it equals ``size_bytes``; for a row that
    # is itself grouped under a parent (``parent_appid`` set) it is null --
    # its bytes are part of its parent's ``group_size_bytes``.
    group_size_bytes: int | None = None
    # WP CACHE-COMPLETE-1a: how much of the CURRENT build is on disk, 0-100
    # (floor; 100 only when every byte is there). Measured by the update check
    # per mapped depot against the current manifest's chunk list (compressed
    # chunk bytes on both sides, exact-size chunk files only;
    # vault_api/cache_completeness.py). Null = unknown: nothing on disk
    # (``size_bytes`` null/0), not measured yet, the oracle announced a build
    # whose manifest nobody has fetched yet, or the build changed since the
    # last measurement. UIs then fall back to the ``size_bytes`` rule.
    cache_complete_pct: int | None = None
    # The current build's chunk bytes over the app's mapped depots, and how
    # many of them are on disk. Null exactly when ``cache_complete_pct`` is.
    cache_expected_bytes: int | None = None
    cache_present_bytes: int | None = None
    # When the oldest depot figure was measured (UTC), null when unknown.
    cache_complete_checked_at: str | None = None
    # WP CACHE-COMPLETE-1b: chunk files of the current build with no file on
    # disk; null exactly when ``cache_complete_pct`` is. An incomplete game
    # with 0 here is short only of wrong-size chunk files, which no current
    # action repairs (UIs do not offer "Complete now" for it).
    cache_missing_chunks: int | None = None
    # WP UNMAPPED-FLAG-1: depots the game demonstrably uses (a recorded
    # manifest) that have no mapping and so are NOT in the figure above, with
    # bytes on disk. 0 = none (or nothing on disk). A UI says the figure does
    # not cover the whole game when this is > 0.
    cache_unmapped_depots: int = 0
    # Same figure over the whole group this row heads (D9): the depots of the
    # members that currently have bytes on disk (own size_bytes > 0, the same
    # members that add bytes to ``group_size_bytes``), each depot/manifest
    # counted once; a member with nothing on disk is skipped. Null for a row
    # grouped under a parent, like ``group_size_bytes``.
    group_cache_complete_pct: int | None = None
    # Stored library metadata (WP D23), null = unknown.
    meta: GameMeta | None = None


def _completeness_fields(figure: cache_completeness.Completeness) -> dict[str, object]:
    """``cache_complete_*`` for one app (WP CACHE-COMPLETE-1a) from its
    ``cache_completeness.app_figure`` -- the same figure ``POST
    /v1/prefill/incomplete`` and the scheduler act on (WP CACHE-COMPLETE-1b).
    """
    return {
        "cache_complete_pct": figure.pct,
        "cache_expected_bytes": figure.expected_bytes,
        "cache_present_bytes": figure.present_bytes,
        "cache_complete_checked_at": figure.checked_at,
        "cache_missing_chunks": figure.missing_chunks if figure.pct is not None else None,
    }


def _group_completeness_pct(
    appid: int,
    classifications: dict[int, app_classification.Classification],
    children_by_parent: dict[int, list[int]],
    app_depotids: dict[int, list[int]],
    index: cache_completeness.CompletenessIndex,
    group_size: int | None,
    depot_bytes: dict[int, int],
) -> int | None:
    """``group_cache_complete_pct`` for one row (same grouping as
    ``_group_size_bytes``). Only members whose own ``size_bytes`` is > 0
    count (CACHE-COMPLETE-1a review): a member with nothing on disk must not
    contribute its last stored rows."""
    own = classifications.get(appid)
    if (own is not None and own.parent_appid is not None) or not group_size:
        return None
    members = [
        m
        for m in (appid, *children_by_parent.get(appid, []))
        if app_size_bytes(app_depotids.get(m, []), depot_bytes)
    ]
    pairs = [(m, d) for m in members for d in app_depotids.get(m, [])]
    return index.figure(pairs).pct


def _children_by_parent(
    classifications: dict[int, app_classification.Classification],
) -> dict[int, list[int]]:
    """``{parent appid: [grouped child appids]}`` -- built once per request,
    so the per-row group size is O(members), not O(classified apps)."""
    out: dict[int, list[int]] = {}
    for child, cls in classifications.items():
        if cls.parent_appid is not None:
            out.setdefault(cls.parent_appid, []).append(child)
    return out


def _group_size_bytes(
    appid: int,
    classifications: dict[int, app_classification.Classification],
    children_by_parent: dict[int, list[int]],
    app_depotids: dict[int, list[int]],
    depot_bytes: dict[int, int],
) -> int | None:
    """``group_size_bytes`` for one row (see ``GameSummary``). Grouping is the
    same ``parent_appid`` resolution the list returns."""
    own = classifications.get(appid)
    if own is not None and own.parent_appid is not None:
        return None
    members = [appid, *children_by_parent.get(appid, [])]
    depots = {depotid for member in members for depotid in app_depotids.get(member, [])}
    return app_size_bytes(sorted(depots), depot_bytes)


def _classification_fields(
    classification: app_classification.Classification | None,
) -> dict[str, object]:
    """``parent_appid`` / ``app_type`` for one app (WP TOOL-APPS-2a)."""
    if classification is None:
        classification = app_classification.UNCLASSIFIED
    return {
        "parent_appid": classification.parent_appid,
        "app_type": classification.app_type,
    }


def _tool_app_fields(appid: int) -> dict[str, object]:
    """``tool_app`` / ``tool_app_name`` for one app (WP API-FIX-4)."""
    tool = tool_apps.get_tool_app(appid)
    if tool is None:
        return {"tool_app": False, "tool_app_name": None}
    return {"tool_app": True, "tool_app_name": tool.name}


def _update_fields(
    verdict: tuple[str, str | None] | None, size_bytes: int | None
) -> dict[str, object]:
    """``update_available`` / ``update_checked_at`` from a stored oracle verdict
    (``None`` = the oracle never answered for this app).

    An update is only offered for a game that currently has bytes in the cache:
    ``depot_manifests`` rows survive ``DELETE /v1/cache/{appid}``, so a deleted
    game would otherwise keep a "stale" verdict for a cache that is gone.
    """
    if verdict is None:
        return {"update_available": False, "update_checked_at": None}
    return {
        "update_available": verdict[0] == oracle.VERDICT_STALE
        and bool(size_bytes),
        "update_checked_at": verdict[1],
    }


class DepotEntry(BaseModel):
    depotid: int
    # True if this depot is also mapped to at least one other app (plan §4
    # shared-depot semantics). NOT the same as "never deleted" any more
    # (ADR-0003 addendum, WP 3.5): a shared=true depot whose every OTHER
    # mapped app currently has no cache content is deleted anyway as a "last
    # cached remnant" — see api/README.md "Last cached remnants".
    shared: bool
    # Bytes on disk for this one depot (WP 1.5), null if never written yet.
    size_bytes: int | None = None


class GameDetail(BaseModel):
    appid: int
    name: str | None
    status: str
    # See GameSummary.not_owned.
    not_owned: bool = False
    last_prefill_at: str | None
    # See GameSummary.last_manifest_check.
    last_manifest_check: str | None
    depots: list[DepotEntry]
    # See GameSummary.size_bytes.
    size_bytes: int | None = None
    # See GameSummary.needs_force.
    needs_force: bool
    # See GameSummary.manifest_change_frequency / .manifest_observation_days /
    # .manifest_days_since_last_change.
    manifest_change_frequency: str | None = None
    manifest_observation_days: int | None = None
    manifest_days_since_last_change: int | None = None
    # See GameSummary.installed_on.
    installed_on: list[InstalledOn] = []
    # See GameSummary.tool_app / .tool_app_name (WP API-FIX-4).
    tool_app: bool = False
    tool_app_name: str | None = None
    # See GameSummary.cover_url (WP API-FIX-5).
    cover_url: str | None = None
    # See GameSummary.cover_cached_url (WP D10).
    cover_cached_url: str | None = None
    # See GameSummary.update_available / .update_checked_at (WP UPD-1).
    update_available: bool = False
    update_checked_at: str | None = None
    # See GameSummary.parent_appid / .app_type (WP TOOL-APPS-2a).
    parent_appid: int | None = None
    app_type: str | None = None
    # See GameSummary.group_size_bytes (WP TOOL-APPS-2).
    group_size_bytes: int | None = None
    # See GameSummary.cache_complete_* (WP CACHE-COMPLETE-1a).
    cache_complete_pct: int | None = None
    cache_expected_bytes: int | None = None
    cache_present_bytes: int | None = None
    cache_complete_checked_at: str | None = None
    cache_missing_chunks: int | None = None
    cache_unmapped_depots: int = 0
    group_cache_complete_pct: int | None = None


@router.get("/v1/games", response_model=list[GameSummary])
def list_games(
    request: Request,
    response: Response,
    open_db: DbOpener = Depends(db_opener),
    size_cache: SizeCache = Depends(get_size_cache),
    cache_root: str = Depends(get_cache_root),
    incomplete: bool = Query(
        False,
        description=(
            "Only games whose measured cache_complete_pct is below 100 "
            "(unknown figures are left out). For a Downloads 'Incomplete' list."
        ),
    ),
    category: list[Literal["single", "multi", "coop", "vr"]] = Query(
        [],
        description=(
            "Library filter (WP D23): keep games with ALL of these categories "
            "(single-player, multiplayer, co-op, VR). Repeat the parameter."
        ),
    ),
    genre: list[int] = Query(
        [],
        max_length=library_meta.MAX_GENRES,
        description="Library filter: keep games with ANY of these Steam genre ids.",
    ),
    min_rating: int | None = Query(
        None, ge=0, le=100, description="Library filter: overall review percentage at least this."
    ),
    year_from: int | None = Query(None, ge=1970, le=2100, description="Release year, inclusive."),
    year_to: int | None = Query(None, ge=1970, le=2100, description="Release year, inclusive."),
    include_unknown: bool = Query(
        False,
        description=(
            "With a library filter, also keep games whose value for it is "
            "unknown (no metadata stored yet). Default: leave them out; the "
            "count of games left out for that reason is in the "
            "X-Meta-Unknown-Excluded response header."
        ),
    ),
    sort: Literal["appid", "name", "rating", "year"] = Query(
        "appid",
        description=(
            "Sort key. Games without a value for rating/year always come last."
        ),
    ),
    order: Literal["asc", "desc"] | None = Query(
        None, description="Default: asc, except rating and year which default to desc."
    ),
) -> list[GameSummary]:
    """All tracked apps with their depot count and size (plan §6).

    ``?incomplete=true`` (WP CACHE-COMPLETE-1a) keeps only rows whose own
    ``cache_complete_pct`` is known and below 100; grouping stays the
    client's job, as for the rest of the list.

    Library filters and sort (WP D23 LIBRARY-META-1b), all optional and
    combined with AND: ``category`` (repeatable, ALL must hold), ``genre``
    (repeatable, ANY), ``min_rating``, ``year_from``/``year_to``. A game whose
    metadata is unknown (never fetched; the nightly fill is off by default) is
    left out of a filtered list unless ``include_unknown=true``, and the
    header ``X-Meta-Unknown-Excluded`` says how many such games a filter left
    out, so unknown never hides a game silently. ``sort`` is ``appid``
    (default), ``name``, ``rating`` or ``year``; games without a value sort
    last in either direction.
    """
    meta_filter = library_meta.MetaFilter(
        categories=tuple(dict.fromkeys(category)),
        genres=tuple(dict.fromkeys(genre)),
        min_rating=min_rating,
        year_from=year_from,
        year_to=year_to,
        include_unknown=include_unknown,
    )
    if year_from is not None and year_to is not None and year_from > year_to:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="year_from must not be greater than year_to",
        )
    # WP AG-1 review round 1, B1: the scheduler object, not app.state.settings
    # directly -- .settings is the boot snapshot _fresh_snapshots_now resolves
    # against the live DB override every call, and .now() is the SAME
    # injectable clock the scheduler's own tick uses (see
    # routers/schedule.py's identical pattern).
    scheduler_obj = request.app.state.scheduler
    base_settings = scheduler_obj.settings
    now = scheduler_obj.now()
    with open_db() as conn:
        rows = conn.execute(
            """
            SELECT a.appid, a.name, a.status, a.last_prefill_at,
                   a.last_manifest_check, a.needs_force,
                   COUNT(d.depotid) AS depot_count
            FROM apps a
            LEFT JOIN depot_app_map d ON d.appid = a.appid
            GROUP BY a.appid
            ORDER BY a.appid
            """
        ).fetchall()
        depot_rows = conn.execute(
            "SELECT appid, depotid FROM depot_app_map"
        ).fetchall()
        # One query for every app's change-frequency rows, grouped in Python
        # (vault_api.depot_manifests.change_frequency_by_app) -- same
        # avoid-the-N+1 shape as app_depotids/depot_bytes below, not a
        # per-app query in this loop.
        frequencies = depot_manifests.change_frequency_by_app(conn)
        # Same avoid-the-N+1 shape again: one pass over the fresh clients
        # (bounded by client count, not app count), re-keyed by appid, not a
        # per-app query in the loop below.
        installed_on = _installed_on_by_appid(conn, base_settings, now)
        # WP API-FIX-5: one query for every stored cover, re-validated.
        covers = cover_art.cover_urls_by_appid(conn)
        # WP D10: one query for every stored cover file.
        cached_covers = cover_cache.cached_cover_paths(
            conn, request.app.state.settings.vault_api_key
        )
        # WP UPD-1: stored oracle verdicts, three statements for the whole list.
        update_verdicts = oracle.verdicts_by_app(conn, settings=base_settings)
        # WP TOOL-APPS-2a: stored classification, nesting resolved, two
        # statements for the whole list.
        classifications = app_classification.resolve_classifications(
            conn, oracle_enabled=base_settings.manifest_oracle_enabled
        )
        children_by_parent = _children_by_parent(classifications)
        # WP CACHE-COMPLETE-1a: stored rows + current targets, bulk.
        completeness = cache_completeness.load_index(conn, base_settings)
        unmapped = cache_completeness.unmapped_depot_counts(
            conn, size_cache.get(cache_root).depot_bytes
        )
        not_owned_set = jobs.not_owned_appids(conn)
        # WP D23: stored metadata and the filter's match set (indexed SQL).
        app_meta = library_meta.meta_by_appid(conn)
        matching: set[int] | None = None
        if meta_filter.active:
            matching = library_meta.matching_appids(conn, meta_filter)
            if not include_unknown:
                with_unknown = library_meta.matching_appids(
                    conn, replace(meta_filter, include_unknown=True)
                )
                response.headers["X-Meta-Unknown-Excluded"] = str(
                    len(with_unknown - matching)
                )
            else:
                response.headers["X-Meta-Unknown-Excluded"] = "0"

    app_depotids: dict[int, list[int]] = {}
    for row in depot_rows:
        app_depotids.setdefault(int(row["appid"]), []).append(int(row["depotid"]))

    depot_bytes = size_cache.get(cache_root).depot_bytes
    no_manifest_data = depot_manifests.ChangeFrequency(None, None)

    games = []
    for row in rows:
        frequency = frequencies.get(row["appid"], no_manifest_data)
        size_bytes = app_size_bytes(app_depotids.get(row["appid"], []), depot_bytes)
        # WP CACHE-COMPLETE-1b: the shared figure and predicate (also behind
        # POST /v1/prefill/incomplete and the scheduler's completion).
        figure = cache_completeness.app_figure(
            completeness, row["appid"], app_depotids.get(row["appid"], []), size_bytes
        )
        completeness_fields = _completeness_fields(figure)
        completeness_fields["cache_unmapped_depots"] = (
            unmapped.get(row["appid"], 0) if size_bytes else 0
        )
        if incomplete and not cache_completeness.is_incomplete(figure):
            continue
        if matching is not None and row["appid"] not in matching:
            continue
        group_size = _group_size_bytes(
            row["appid"], classifications, children_by_parent, app_depotids, depot_bytes
        )
        stored_meta = app_meta.get(row["appid"])
        games.append(
            GameSummary(
                appid=row["appid"],
                name=app_classification.display_name(
                    row["name"], classifications.get(row["appid"])
                ),
                status=row["status"],
                not_owned=row["appid"] in not_owned_set,
                last_prefill_at=row["last_prefill_at"],
                last_manifest_check=row["last_manifest_check"],
                depot_count=row["depot_count"],
                size_bytes=size_bytes,
                needs_force=bool(row["needs_force"]),
                manifest_change_frequency=frequency.category,
                manifest_observation_days=frequency.observation_days,
                manifest_days_since_last_change=frequency.days_since_last_change,
                installed_on=installed_on.get(row["appid"], []),
                **_tool_app_fields(row["appid"]),
                cover_url=covers.get(row["appid"]),
                cover_cached_url=cached_covers.get(row["appid"]),
                **_update_fields(update_verdicts.get(row["appid"]), size_bytes),
                **_classification_fields(classifications.get(row["appid"])),
                group_size_bytes=group_size,
                **completeness_fields,
                group_cache_complete_pct=_group_completeness_pct(
                    row["appid"],
                    classifications,
                    children_by_parent,
                    app_depotids,
                    completeness,
                    group_size,
                    depot_bytes,
                ),
                meta=GameMeta(**stored_meta) if stored_meta is not None else None,
            )
        )
    return _sorted_games(games, sort, order)


def _sorted_games(
    games: list[GameSummary], sort: str, order: str | None
) -> list[GameSummary]:
    """Apply ``sort``/``order``; games without a sort value always come last."""
    if sort == "appid":
        return sorted(games, key=lambda g: g.appid, reverse=order == "desc")
    descending = (order or ("desc" if sort in ("rating", "year") else "asc")) == "desc"

    def value(game: GameSummary) -> object:
        if sort == "name":
            return (game.name or "").casefold() or None
        stored = game.meta
        if stored is None:
            return None
        return stored.review_percent if sort == "rating" else stored.release_year

    known = [g for g in games if value(g) is not None]
    unknown = [g for g in games if value(g) is None]
    known.sort(key=lambda g: (value(g), g.appid), reverse=descending)
    return known + unknown


@router.get("/v1/games/{appid}", response_model=GameDetail)
def get_game(
    request: Request,
    appid: int,
    open_db: DbOpener = Depends(db_opener),
    size_cache: SizeCache = Depends(get_size_cache),
    cache_root: str = Depends(get_cache_root),
) -> GameDetail:
    """Detail for one app, incl. its depot list with shared-depot flags and sizes.

    404 if the appid has no row in ``apps`` (plan §4: apps are created by
    a mapping upsert, either from a prefill run or the manual fallback —
    an appid with no mapping activity yet simply doesn't exist here).
    """
    # See list_games' identical comment (WP AG-1 review round 1, B1).
    scheduler_obj = request.app.state.scheduler
    base_settings = scheduler_obj.settings
    now = scheduler_obj.now()
    with open_db() as conn:
        app_row = conn.execute(
            "SELECT appid, name, status, last_prefill_at, last_manifest_check, "
            "needs_force FROM apps WHERE appid = ?",
            (appid,),
        ).fetchone()
        if app_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown appid {appid}"
            )

        # Single query for the whole depot list, each row carrying its own
        # "does at least one other app also map this depotid" flag via a
        # correlated EXISTS subquery — avoids one extra round-trip per depot
        # (WP 1.3 review nitpick; the previous version ran a COUNT(*) per row).
        depot_rows = conn.execute(
            """
            SELECT d.depotid,
                   EXISTS (
                       SELECT 1 FROM depot_app_map d2
                       WHERE d2.depotid = d.depotid AND d2.appid != d.appid
                   ) AS shared
            FROM depot_app_map d
            WHERE d.appid = ?
            ORDER BY d.depotid
            """,
            (appid,),
        ).fetchall()
        frequency = depot_manifests.change_frequency_for_app(conn, appid)
        installed_on = _installed_on_for_appid(conn, base_settings, now, appid)
        cover_url = cover_art.cover_url_for_appid(conn, appid)
        cover_cached_url = cover_cache.cached_cover_path(
            conn, request.app.state.settings.vault_api_key, appid
        )
        update_view = oracle.app_view(conn, appid, settings=base_settings)
        # Same resolution as the list (the chain may pass through other apps).
        classifications = app_classification.resolve_classifications(
            conn, oracle_enabled=base_settings.manifest_oracle_enabled
        )
        children_by_parent = _children_by_parent(classifications)
        classification = classifications.get(appid)
        # The depots of every app this one heads (group_size_bytes).
        group_members = sorted({appid, *children_by_parent.get(appid, [])})
        group_depot_rows = conn.execute(
            "SELECT appid, depotid FROM depot_app_map WHERE appid IN ({})".format(
                ",".join("?" for _ in group_members)
            ),
            group_members,
        ).fetchall()
        completeness = cache_completeness.load_index(conn, base_settings, group_members)
        detail_not_owned = appid in jobs.not_owned_appids(conn)

    depot_bytes = size_cache.get(cache_root).depot_bytes
    with open_db() as conn:
        detail_unmapped = cache_completeness.unmapped_depot_counts(conn, depot_bytes).get(appid, 0)

    depots = [
        DepotEntry(
            depotid=row["depotid"],
            shared=bool(row["shared"]),
            size_bytes=depot_bytes.get(row["depotid"]),
        )
        for row in depot_rows
    ]

    detail_size = app_size_bytes([row["depotid"] for row in depot_rows], depot_bytes)
    group_depotids: dict[int, list[int]] = {}
    for row in group_depot_rows:
        group_depotids.setdefault(int(row["appid"]), []).append(int(row["depotid"]))
    group_size = _group_size_bytes(
        appid, classifications, children_by_parent, group_depotids, depot_bytes
    )
    return GameDetail(
        appid=app_row["appid"],
        name=app_classification.display_name(app_row["name"], classification),
        status=app_row["status"],
        not_owned=detail_not_owned,
        last_prefill_at=app_row["last_prefill_at"],
        last_manifest_check=app_row["last_manifest_check"],
        depots=depots,
        size_bytes=detail_size,
        needs_force=bool(app_row["needs_force"]),
        manifest_change_frequency=frequency.category,
        manifest_observation_days=frequency.observation_days,
        manifest_days_since_last_change=frequency.days_since_last_change,
        installed_on=installed_on,
        **_tool_app_fields(app_row["appid"]),
        cover_url=cover_url,
        cover_cached_url=cover_cached_url,
        **_update_fields(
            (update_view.verdict, update_view.checked_at) if update_view.known else None,
            detail_size,
        ),
        **_classification_fields(classification),
        group_size_bytes=group_size,
        **_completeness_fields(
            cache_completeness.app_figure(
                completeness, appid, group_depotids.get(appid, []), detail_size
            )
        ),
        cache_unmapped_depots=detail_unmapped if detail_size else 0,
        group_cache_complete_pct=_group_completeness_pct(
            appid,
            classifications,
            children_by_parent,
            group_depotids,
            completeness,
            group_size,
            depot_bytes,
        ),
    )
