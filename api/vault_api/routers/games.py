"""Games endpoints (plan §6): GET /v1/games, GET /v1/games/{appid}.

Auth is attached at the router level (secure-by-default pattern, see
api/README.md "Auth" section) — every route added here is authenticated
automatically.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from vault_api import cover_art, depot_manifests, scheduler, settings_store, tool_apps
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


class GameSummary(BaseModel):
    appid: int
    name: str | None
    status: str
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


def _tool_app_fields(appid: int) -> dict[str, object]:
    """``tool_app`` / ``tool_app_name`` for one app (WP API-FIX-4)."""
    tool = tool_apps.get_tool_app(appid)
    if tool is None:
        return {"tool_app": False, "tool_app_name": None}
    return {"tool_app": True, "tool_app_name": tool.name}


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


@router.get("/v1/games", response_model=list[GameSummary])
def list_games(
    request: Request,
    open_db: DbOpener = Depends(db_opener),
    size_cache: SizeCache = Depends(get_size_cache),
    cache_root: str = Depends(get_cache_root),
) -> list[GameSummary]:
    """All tracked apps with their depot count and size (plan §6)."""
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

    app_depotids: dict[int, list[int]] = {}
    for row in depot_rows:
        app_depotids.setdefault(int(row["appid"]), []).append(int(row["depotid"]))

    depot_bytes = size_cache.get(cache_root).depot_bytes
    no_manifest_data = depot_manifests.ChangeFrequency(None, None)

    games = []
    for row in rows:
        frequency = frequencies.get(row["appid"], no_manifest_data)
        games.append(
            GameSummary(
                appid=row["appid"],
                name=row["name"],
                status=row["status"],
                last_prefill_at=row["last_prefill_at"],
                last_manifest_check=row["last_manifest_check"],
                depot_count=row["depot_count"],
                size_bytes=app_size_bytes(app_depotids.get(row["appid"], []), depot_bytes),
                needs_force=bool(row["needs_force"]),
                manifest_change_frequency=frequency.category,
                manifest_observation_days=frequency.observation_days,
                manifest_days_since_last_change=frequency.days_since_last_change,
                installed_on=installed_on.get(row["appid"], []),
                **_tool_app_fields(row["appid"]),
                cover_url=covers.get(row["appid"]),
            )
        )
    return games


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

    depot_bytes = size_cache.get(cache_root).depot_bytes

    depots = [
        DepotEntry(
            depotid=row["depotid"],
            shared=bool(row["shared"]),
            size_bytes=depot_bytes.get(row["depotid"]),
        )
        for row in depot_rows
    ]

    return GameDetail(
        appid=app_row["appid"],
        name=app_row["name"],
        status=app_row["status"],
        last_prefill_at=app_row["last_prefill_at"],
        last_manifest_check=app_row["last_manifest_check"],
        depots=depots,
        size_bytes=app_size_bytes([row["depotid"] for row in depot_rows], depot_bytes),
        needs_force=bool(app_row["needs_force"]),
        manifest_change_frequency=frequency.category,
        manifest_observation_days=frequency.observation_days,
        manifest_days_since_last_change=frequency.days_since_last_change,
        installed_on=installed_on,
        **_tool_app_fields(app_row["appid"]),
        cover_url=cover_url,
    )
