"""Depot attribution by evidence, and the one-time cleanup of wrong rows
(WP MAP-ATTRIB-1).

## The problem

A prefill maps the depots whose directories changed during its run to the
running app (``prefill.diff_depots`` / ``apply_observed_mapping``). The cache
is shared: a PC loading another game at the same time writes into other
depots, and before this package those depots were mapped to the running app
as well. Prod, 2026-11-01: Wanderburg (3624140) gained 436781 (owner 436780),
553851 and 553854 (owner 553850, HELLDIVERS 2) and 1716751 (owners 1716750
and 3751950). Those foreign pairs have no completeness target, so the game
read "Cached · not verified", and GC for 3624140 planned depots it does not
own (safe only because every true owner still had its own recorded manifest).

## Evidence that an app owns a depot

``owned_depots`` unions three sources; any one of them is enough:

1. **The app's own SteamPrefill manifest files** (``manifest_named``,
   ``manifest_ingest.own_manifest_depots``): a ``{appid}_*.bin`` file in the
   temp cache naming the depot. SteamPrefill writes one per depot it fetched
   for the app, so this covers every depot a run really downloaded for it.
2. **A recorded manifest** (``depot_manifests``): a row of the app for the
   depot, or a row whose ``containing_appid`` is the app (the manifest file
   said the depot belongs to it, ``manifest_ingest`` step 5).
3. **The oracle's depot list** (only while the oracle is on, the same rule as
   every oracle read path): depots with an open-branch manifest in the app's
   own answer or in the answer of one of its content packages
   (``oracle_dlc_links``), i.e. the depots ``oracle_branch_manifests`` holds
   for them.

Evidence can only widen what is mapped; it never removes anything. The
replace step of ``apply_observed_mapping`` decides removals from the
observed set and keeps an unobserved depot that ``manifest_named``, the
app's own recorded manifest (``own_recorded_depots``) or its oracle depot
list names (WP MAP-LOSS-1).

## The one-time cleanup (``cleanup_foreign_mappings``)

Removes a ``(depot, app)`` mapping row only when ALL of these hold:

- the depot has at least one other mapped owner, and one of them has evidence
  for the depot (so the row is never the depot's only mapping, and the depot
  stays mapped to a true owner whose GC plans it);
- the app itself has no evidence for the depot (none of the three sources);
- the app has oracle data with a non-empty depot list (the oracle is on and
  answered for it); without it the pair is logged as skipped and kept;
- the app is not related to an evidenced co-owner (parent, content package,
  or sibling package of the same parent, by ``oracle_dlc_links``,
  ``oracle_app_state.parent_appid`` or ``app_classification.parent_appid``):
  a package app mapped to its parent's depot is a legitimate shared row.

The matching ``cache_completeness`` row of a removed pair goes with it.

**Restore, same step and transaction.** An app's own depot that lost its
mapping row is mapped back to it when ALL of these hold:

- the app recorded its OWN manifest for the depot AND is its containing app
  (``depot_manifests.appid`` = the app and ``containing_appid`` = the app or
  NULL). A content-package depot recorded under the game with the package as
  containing app is NOT restored to the game: per GROUP-VERIFIED-1 only the
  package maps it, and GC already counts the game's record as a claimant
  (``gc.plan_gc``). A ``containing_appid`` alone never restores either;
- that recorded manifest id equals the oracle's ``public`` gid for the depot
  (in the app's own answer or one of its content packages'; oracle on). An
  older record is skipped and logged for the operator: the oracle keep set
  (``oracle.gc_keepset_gids``) leaves ``public`` out on purpose, so a
  restored depot whose only record is an old build would let GC plan the
  current build's chunks as orphans;
- the depot directory exists in the cache.

Prod 2026-11-01: Enshrouded (1203620) recorded 1203621 with the oracle's
public gid, but no row mapped it, and the removal of its one foreign row
(1465471) would have left the game with no mapping at all; The Witcher 3
(292030) likewise lost 292033 and 292036. GC then plans the depot with the
recorded manifest (= the current public build) as the app's claim.

Runs from the startup lifespan before the worker starts, **once per
database** (``maintenance_steps``): it is stamped after every pass in which
the temp cache could be listed and the depot root resolved. When either is
unavailable nothing is changed and the step runs again on the next start.
Pairs skipped for missing oracle data are logged once as left for the
operator (``DELETE /v1/mapping/{depotid}/{appid}``); the step is NOT re-run
for them, so it can never turn into a standing startup rule that would later
remove a manual ``PUT /v1/mapping``. A pass is idempotent: a second one finds
nothing to remove or restore.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass, field
from typing import AbstractSet, Iterable

from vault_api import deletion
from vault_api.config import Settings
from vault_api.jobs import immediate_transaction, utcnow_iso
from vault_api.oracle import BRANCH_PUBLIC

logger = logging.getLogger(__name__)

#: ``maintenance_steps.name`` of the cleanup below.
CLEANUP_STEP_NAME = "map-attrib-1-foreign-mapping-cleanup"


def _ids(rows: Iterable[sqlite3.Row], column: str) -> set[int]:
    found: set[int] = set()
    for row in rows:
        value = deletion.coerce_positive_id(row[column])
        if value is not None:
            found.add(value)
    return found


def oracle_depots(
    conn: sqlite3.Connection, appid: int, *, oracle_enabled: bool
) -> set[int] | None:
    """The oracle's depot list for ``appid`` (its own answer plus the answers
    of its content packages), or ``None`` when the oracle is off or never
    answered for the app. An answer that listed no depot yields an empty set.
    """
    if not oracle_enabled:
        return None
    if conn.execute(
        "SELECT 1 FROM oracle_app_state WHERE appid = ?", (appid,)
    ).fetchone() is None:
        return None
    return _ids(
        conn.execute(
            """
            SELECT DISTINCT depotid FROM oracle_branch_manifests
            WHERE appid = ?
               OR appid IN (SELECT dlc_appid FROM oracle_dlc_links WHERE appid = ?)
            """,
            (appid, appid),
        ).fetchall(),
        "depotid",
    )


def recorded_depots(conn: sqlite3.Connection, appid: int) -> set[int]:
    """Depots with a ``depot_manifests`` row of ``appid``, or whose recorded
    manifest names ``appid`` as the containing app."""
    return _ids(
        conn.execute(
            "SELECT DISTINCT depotid FROM depot_manifests "
            "WHERE appid = ? OR containing_appid = ?",
            (appid, appid),
        ).fetchall(),
        "depotid",
    )


def own_recorded_depots(conn: sqlite3.Connection, appid: int) -> set[int]:
    """Depots whose recorded manifest names ``appid`` as its containing app,
    or that ``appid`` recorded without naming a containing app (WP
    MAP-LOSS-1).

    Narrower than ``recorded_depots`` on purpose: a content-package depot the
    game recorded with the package as containing app belongs to the package
    (the ownership model of GROUP-VERIFIED-1 and of the restore below), not
    to the game. ``prefill.apply_observed_mapping`` never removes one of
    these depots from the app's mapping on a run that did not write into it.
    """
    return _ids(
        conn.execute(
            "SELECT DISTINCT depotid FROM depot_manifests "
            "WHERE containing_appid = ? OR (appid = ? AND containing_appid IS NULL)",
            (appid, appid),
        ).fetchall(),
        "depotid",
    )


def owned_depots(
    conn: sqlite3.Connection,
    appid: int,
    *,
    manifest_named: AbstractSet[int] | None,
    oracle_enabled: bool,
) -> set[int] | None:
    """Every depot there is positive evidence ``appid`` owns (module
    docstring), or ``None`` when there is no evidence at all: no own manifest
    file (unreadable or none), no recorded manifest, no oracle depot list.
    ``None`` is the fallback case of ``apply_observed_mapping``."""
    owned: set[int] = set(manifest_named or ())
    owned |= recorded_depots(conn, appid)
    owned |= oracle_depots(conn, appid, oracle_enabled=oracle_enabled) or set()
    return owned or None


# --------------------------------------------------------------------------
# One-time cleanup of rows written before the fix
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ForeignCleanupResult:
    """What one ``cleanup_foreign_mappings`` call did (logged by the caller)."""

    #: The step had already run on this database; nothing was looked at.
    already_done: bool = False
    #: The temp cache could not be listed: nothing changed, not stamped.
    cache_dir_unavailable: bool = False
    #: The depot root could not be resolved: nothing changed, not stamped.
    depot_root_unavailable: bool = False
    #: ``(depotid, appid)`` pairs whose mapping row was removed.
    removed: tuple[tuple[int, int], ...] = ()
    #: ``(depotid, appid)`` pairs mapped back to their own app.
    restored: tuple[tuple[int, int], ...] = ()
    #: Restore candidates whose recorded manifest is not the oracle's public
    #: gid; kept unmapped and left for the operator.
    restore_skipped_gid: tuple[tuple[int, int], ...] = ()
    #: Pairs that met every other removal condition but the app has no
    #: oracle data; kept and left for the operator.
    skipped_no_oracle: tuple[tuple[int, int], ...] = ()
    #: Whether the step was stamped as done.
    stamped: bool = False
    #: Completeness rows removed with the pairs.
    completeness_removed: int = 0
    #: Per removed pair, the co-owners with evidence (for the log).
    evidenced_owners: dict[tuple[int, int], tuple[int, ...]] = field(default_factory=dict)


def _relations(conn: sqlite3.Connection) -> dict[int, set[int]]:
    """``{app: {app, its parents, its packages}}`` from every stored source,
    regardless of the oracle flag: a relation only ever keeps a row."""
    links: dict[int, set[int]] = {}

    def link(a: object, b: object) -> None:
        x = deletion.coerce_positive_id(a)
        y = deletion.coerce_positive_id(b)
        if x is None or y is None or x == y:
            return
        links.setdefault(x, {x}).add(y)
        links.setdefault(y, {y}).add(x)

    for row in conn.execute("SELECT appid, dlc_appid FROM oracle_dlc_links"):
        link(row["appid"], row["dlc_appid"])
    for row in conn.execute(
        "SELECT appid, parent_appid FROM oracle_app_state WHERE parent_appid IS NOT NULL"
    ):
        link(row["appid"], row["parent_appid"])
    for row in conn.execute(
        "SELECT appid, parent_appid FROM app_classification WHERE parent_appid IS NOT NULL"
    ):
        link(row["appid"], row["parent_appid"])
    return links


def _related(links: dict[int, set[int]], a: int, b: int) -> bool:
    """Parent/package, or two packages of the same parent."""
    return bool(links.get(a, {a}) & links.get(b, {b}))


def cleanup_foreign_mappings(
    conn: sqlite3.Connection, settings: Settings
) -> ForeignCleanupResult:
    """Remove mapping rows a prefill attributed to the wrong app and map back
    an app's own recorded depots (module docstring, "The one-time cleanup").
    Never raises for missing files; the caller still wraps it (startup must
    not fail over a cleanup)."""
    from vault_api import manifest_ingest  # local: manifest_ingest imports mapping

    if conn.execute(
        "SELECT 1 FROM maintenance_steps WHERE name = ?", (CLEANUP_STEP_NAME,)
    ).fetchone() is not None:
        return ForeignCleanupResult(already_done=True)

    # Evidence 1: manifest files (filename only), original and containing app.
    named = manifest_ingest.manifest_files_by_app(settings.steamprefill_cache_dir)
    if named is None:
        logger.info(
            "mapping cleanup (MAP-ATTRIB-1): SteamPrefill temp cache %r not readable; "
            "nothing changed, retried on the next start.",
            settings.steamprefill_cache_dir,
        )
        return ForeignCleanupResult(cache_dir_unavailable=True)
    try:
        depot_root = deletion.resolve_depot_root(settings.cache_root)
    except Exception as exc:  # noqa: BLE001 - unjudgeable disk changes nothing
        logger.info(
            "mapping cleanup (MAP-ATTRIB-1): depot cache unavailable (%s); nothing "
            "changed, retried on the next start.",
            exc,
        )
        return ForeignCleanupResult(depot_root_unavailable=True)

    # Evidence 2: recorded manifests, under the recording and the containing
    # app. ``own_recorded`` (restore source): rows whose recording app is also
    # the containing app (or none is named), with the recorded manifest id.
    recorded: dict[int, set[int]] = {}
    own_recorded: dict[tuple[int, int], str] = {}
    for row in conn.execute(
        "SELECT appid, containing_appid, depotid, manifestid FROM depot_manifests"
    ):
        depotid = deletion.coerce_positive_id(row["depotid"])
        if depotid is None:
            continue
        for column in ("appid", "containing_appid"):
            owner = deletion.coerce_positive_id(row[column])
            if owner is not None:
                recorded.setdefault(owner, set()).add(depotid)
        app = deletion.coerce_positive_id(row["appid"])
        containing = row["containing_appid"]
        if app is not None and (
            containing is None or deletion.coerce_positive_id(containing) == app
        ):
            own_recorded[(depotid, app)] = str(row["manifestid"])

    # Evidence 3: the oracle's depot list, read lazily per app.
    oracle_cache: dict[int, set[int] | None] = {}

    def oracle_list(appid: int) -> set[int] | None:
        if appid not in oracle_cache:
            oracle_cache[appid] = oracle_depots(
                conn, appid, oracle_enabled=settings.manifest_oracle_enabled
            )
        return oracle_cache[appid]

    def has_evidence(appid: int, depotid: int) -> bool:
        return (
            depotid in named.get(appid, ())
            or depotid in recorded.get(appid, ())
            or depotid in (oracle_list(appid) or ())
        )

    owners: dict[int, set[int]] = {}
    for row in conn.execute("SELECT depotid, appid FROM depot_app_map"):
        depotid = deletion.coerce_positive_id(row["depotid"])
        appid = deletion.coerce_positive_id(row["appid"])
        if depotid is None or appid is None:
            continue  # unusable ids stay for an operator
        owners.setdefault(depotid, set()).add(appid)

    links = _relations(conn)
    doomed: list[tuple[int, int]] = []
    skipped: list[tuple[int, int]] = []
    evidenced_by: dict[tuple[int, int], tuple[int, ...]] = {}
    for depotid in sorted(owners):
        apps = owners[depotid]
        if len(apps) < 2:
            continue  # the only mapping of a depot is never removed
        evidenced = {a for a in apps if has_evidence(a, depotid)}
        for appid in sorted(apps - evidenced):
            others = evidenced - {appid}
            if not others:
                continue  # no co-owner with evidence: nothing says it is wrong
            if any(_related(links, appid, other) for other in others):
                continue  # package/parent sharing a depot is legitimate
            listed = oracle_list(appid)
            if not listed:
                skipped.append((depotid, appid))
                continue
            doomed.append((depotid, appid))
            evidenced_by[(depotid, appid)] = tuple(sorted(others))

    # Restore: an app's OWN recorded depot (it is the containing app) whose
    # record is the oracle's current public build and that exists in the
    # cache, but that the app does not map.
    restore: list[tuple[int, int]] = []
    restore_skipped_gid: list[tuple[int, int]] = []
    for (depotid, appid), manifestid in sorted(own_recorded.items()):
        if appid in owners.get(depotid, ()):
            continue
        if depotid not in (oracle_list(appid) or ()):
            continue
        public = {
            str(r["manifestid"])
            for r in conn.execute(
                """
                SELECT manifestid FROM oracle_branch_manifests
                WHERE depotid = ? AND branch = ?
                  AND (appid = ?
                       OR appid IN (SELECT dlc_appid FROM oracle_dlc_links WHERE appid = ?))
                """,
                (depotid, BRANCH_PUBLIC, appid, appid),
            )
        }
        if manifestid not in public:
            restore_skipped_gid.append((depotid, appid))
            continue
        try:
            exists = os.path.isdir(deletion.depot_dir_path(depot_root, depotid))
        except deletion.DeletionGuardError:
            exists = False
        if exists:
            restore.append((depotid, appid))

    removed: list[tuple[int, int]] = []
    restored: list[tuple[int, int]] = []
    completeness_removed = 0
    with immediate_transaction(conn):
        for depotid, appid in doomed:
            cursor = conn.execute(
                "DELETE FROM depot_app_map WHERE depotid = ? AND appid = ? "
                "AND (SELECT COUNT(*) FROM depot_app_map WHERE depotid = ?) > 1",
                (depotid, appid, depotid),
            )
            if cursor.rowcount:
                removed.append((depotid, appid))
                completeness_removed += conn.execute(
                    "DELETE FROM cache_completeness WHERE appid = ? AND depotid = ?",
                    (appid, depotid),
                ).rowcount
        for depotid, appid in restore:
            # Same two statements as mapping.upsert_mapping, without its commit:
            # upsert_mapping commits, which would end this transaction early and
            # split the removals from the restores.
            conn.execute(
                "INSERT OR IGNORE INTO apps (appid, name, status) VALUES (?, NULL, 'idle')",
                (appid,),
            )
            if conn.execute(
                "INSERT OR IGNORE INTO depot_app_map (depotid, appid) VALUES (?, ?)",
                (depotid, appid),
            ).rowcount:
                restored.append((depotid, appid))
        conn.execute(
            "INSERT OR IGNORE INTO maintenance_steps (name, done_at) VALUES (?, ?)",
            (CLEANUP_STEP_NAME, utcnow_iso()),
        )

    for depotid, appid in removed:
        logger.warning(
            "mapping cleanup (MAP-ATTRIB-1): removed depot %s from app %s (no manifest, "
            "recorded manifest or oracle depot list of the app names it; owners with "
            "evidence: %s)",
            depotid, appid, list(evidenced_by[(depotid, appid)]),
        )
    for depotid, appid in restored:
        logger.warning(
            "mapping cleanup (MAP-ATTRIB-1): restored depot %s to app %s (its own "
            "recorded manifest is the oracle's public build, depot in the cache)",
            depotid, appid,
        )
    if restore_skipped_gid:
        logger.warning(
            "mapping cleanup (MAP-ATTRIB-1): %d own depot(s) without a mapping row "
            "were NOT restored because the recorded manifest is not the oracle's "
            "current public build; left for the operator (a prefill of the app "
            "maps them again): %s",
            len(restore_skipped_gid), sorted(restore_skipped_gid),
        )
    if skipped:
        logger.warning(
            "mapping cleanup (MAP-ATTRIB-1): %d (depot, app) pair(s) look wrongly "
            "attributed but the app has no oracle depot list to check against; left "
            "for the operator (DELETE /v1/mapping/{depotid}/{appid}): %s",
            len(skipped), sorted(skipped),
        )
    return ForeignCleanupResult(
        removed=tuple(removed),
        restored=tuple(restored),
        restore_skipped_gid=tuple(restore_skipped_gid),
        skipped_no_oracle=tuple(skipped),
        stamped=True,
        completeness_removed=completeness_removed,
        evidenced_owners={k: evidenced_by[k] for k in removed},
    )
