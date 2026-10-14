"""Cache completeness per game (WP CACHE-COMPLETE-1a, schema v25).

"Cached" used to mean only ``size_bytes > 0``: a game flipped to "Cached"
seconds after a download started (user report 2026-10-14, Timberborn). This
module answers the real question: **how much of the current build's chunk set
is on disk?**

## Formula (compares like with like)

For one (app, depot) pair:

1. **Target manifest** (``current_targets``): the depot's current ``public``
   manifest gid from the manifest oracle, unless vault-api's own record in
   ``depot_manifests`` is newer than the oracle's last answer for the app (a
   prefill ingested a manifest after the oracle was asked), or the oracle is
   off / has no row for the depot -- then the recorded manifest id.
2. **Its chunk set**: the manifest is read the way GC reads it
   (``gc.read_manifest_by_id``): the archived ``.bin`` first, else a
   cache-stored client copy under ``depot/<id>/manifest/<gid>/5/``. A Steam
   client that downloads an update through the cache stores the NEW manifest
   there, which is exactly what makes a half-finished client update
   measurable. Never a different (older or newer) manifest id.
3. ``expected_bytes`` = sum of the manifest's ``cb_compressed`` over its
   unique chunk ids. ``present_bytes`` = the same sum over the chunks whose
   file ``depot/<id>/chunk/<chunkid>`` exists with exactly that size. The cache
   stores compressed chunk bodies and ``cb_compressed`` is byte-exact against
   the file size (``manifests.ParsedManifest``, research §2), so both sides are
   compressed bytes. A chunk whose size differs counts as missing (damaged);
   chunks on disk that the target manifest does not list (an older build's
   leftovers) count for nothing -- which is why a plain "depot bytes vs
   manifest size" comparison would be unsound and is not used.

App level (``CompletenessIndex.figure``): the sum over the app's depots,
``pct = floor(100 * present / expected)`` -- 100 only when every byte is
there; ``expected == 0`` is 100.

## Which depots count

Exactly the depots ``depot_app_map`` maps to the app, which is also what
``size_bytes`` sums. That map is written from what SteamPrefill actually
filled (``prefill.apply_observed_mapping``), so it already reflects
SteamPrefill's own OS/language/DLC depot selection; the oracle's full depot
list (every language, every OS) is never used as the set. Known limit: a depot
SteamPrefill WOULD add now (a new DLC depot) but has never filled is not in
the map and is not counted until a prefill fills it.

## Edge cases -> ``null`` (UI falls back to the size-only behaviour)

- no depot mapped, or no bytes on disk (``size_bytes`` null/0);
- any counted depot without a usable stored row: never evaluated, the target
  is unknown (no record, no oracle row), the target manifest cannot be read
  (an update the oracle announced that no client or prefill has fetched yet),
  or the target changed since the row was written (the oracle answered again
  or a prefill ingested a new manifest);
- one unknown depot makes the whole app unknown -- a partial sum would claim
  a precision it does not have.

**UI contract (for 1b):** ``null`` while ``size_bytes > 0`` means "not
known", never "complete" -- it must not render as the green check. Only
``cache_complete_pct == 100`` earns it.

## DLC groups (D9)

``group_cache_complete_pct`` aggregates the (app, depot) pairs of the group
members that currently have bytes on disk (own ``size_bytes`` > 0) -- the
same members that contribute bytes to ``group_size_bytes`` -- each
(depot, manifest) counted once. A member with nothing on disk is skipped, so
a deleted DLC's last stored rows cannot keep a group at 100 %.

## When it runs, and how stale it can be

- Every update check (``update_check.UpdateCheckRunner._run``, UPD-5 cadence,
  both triggers) measures the cached games it selected, after the oracle
  refresh (``evaluate_selection``), then drops the rows of every app outside
  that selection. An aborted run (shutdown) stores no partial app and prunes
  nothing.
- The prefill worker re-measures an app right after a successful prefill job
  for it (``remeasure_app``; same oracle switch as the check).
- ``DELETE /v1/cache/{appid}`` drops the rows of the app and of every depot it
  removed (``forget``).

Anything else -- a Steam client finishing a download through the cache, GC --
shows only at the next check: **a figure is as old as
``cache_complete_checked_at``, up to one ``VAULT_SCHEDULE_INTERVAL_MINUTES``
(default 180 min).** The API only reads the table. Nothing here downloads,
deletes files or writes anywhere but that table.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping, Sequence

from vault_api import deletion, gc, jobs, oracle, sizes, tool_apps
from vault_api.config import Settings
from vault_api.manifest_archive import archive_filename

logger = logging.getLogger(__name__)

#: ``cache_completeness.status`` values.
STATUS_OK = "ok"
#: Neither a recorded manifest nor an oracle ``public`` gid for the depot.
STATUS_NO_TARGET = "no_target"
#: The target manifest id is known, but neither the archive nor a cache-stored
#: copy yields its chunk list -- or its chunk sizes sum past ``INT64_MAX``.
STATUS_MANIFEST_UNAVAILABLE = "manifest_unavailable"
#: The cache root or the depot directory cannot be used safely.
STATUS_UNREADABLE_CACHE = "unreadable_cache"

#: ``cache_completeness.source`` for an evaluated row (which file was read).
SOURCE_ARCHIVE = gc.SOURCE_ARCHIVE


def _never() -> bool:
    return False


@dataclass(frozen=True)
class DepotCompleteness:
    """One evaluated (app, depot) pair, as stored."""

    appid: int
    depotid: int
    manifestid: str | None
    status: str
    checked_at: str
    source: str | None = None
    expected_bytes: int | None = None
    present_bytes: int | None = None
    chunk_count: int | None = None
    missing_chunks: int | None = None
    mismatched_chunks: int | None = None


@dataclass(frozen=True)
class Completeness:
    """An aggregated figure. All ``None`` = unknown."""

    pct: int | None = None
    expected_bytes: int | None = None
    present_bytes: int | None = None
    checked_at: str | None = None
    #: Chunks of the target manifests with no file on disk (WP
    #: CACHE-COMPLETE-1b). Not in the API; the scheduler's automatic
    #: completion keys on it (a forced prefill fetches missing chunks, it does
    #: not replace a chunk file of the wrong size).
    missing_chunks: int | None = None


UNKNOWN = Completeness()


# --------------------------------------------------------------------------
# Target manifests (shared by evaluation and the read side)
# --------------------------------------------------------------------------


def _ids_clause(appids: Sequence[int] | None, column: str) -> tuple[str, list[int]]:
    if appids is None:
        return "", []
    return f" WHERE {column} IN ({','.join('?' for _ in appids)})", list(appids)


def current_targets(
    conn: sqlite3.Connection,
    settings: Settings,
    appids: Iterable[int] | None = None,
) -> dict[tuple[int, int], str | None]:
    """``{(appid, depotid): target manifest id or None}`` for every mapped pair.

    ``None`` = no usable target. Bulk: four statements regardless of the app
    count. Evaluation and the API both call this, so a stored row is only
    shown while its manifest id is still the target.
    """
    ids = None if appids is None else sorted(set(appids))
    if ids is not None and not ids:
        return {}

    where, params = _ids_clause(ids, "appid")
    pairs: list[tuple[int, int]] = []
    for row in conn.execute(f"SELECT appid, depotid FROM depot_app_map{where}", params):
        appid = deletion.coerce_positive_id(row["appid"])
        depotid = deletion.coerce_positive_id(row["depotid"])
        if appid is not None and depotid is not None:
            pairs.append((appid, depotid))

    recorded: dict[tuple[int, int], tuple[str | None, str]] = {}
    for row in conn.execute(
        f"SELECT appid, depotid, manifestid, recorded_at FROM depot_manifests{where}",
        params,
    ):
        appid = deletion.coerce_positive_id(row["appid"])
        depotid = deletion.coerce_positive_id(row["depotid"])
        if appid is None or depotid is None:
            continue
        recorded[(appid, depotid)] = (
            gc.valid_manifest_id(row["manifestid"]),
            str(row["recorded_at"] or ""),
        )

    public: dict[tuple[int, int], str] = {}
    oracle_checked: dict[int, str] = {}
    if settings.manifest_oracle_enabled:
        for row in conn.execute(f"SELECT appid, checked_at FROM oracle_app_state{where}", params):
            appid = deletion.coerce_positive_id(row["appid"])
            if appid is not None:
                oracle_checked[appid] = str(row["checked_at"] or "")
        branch_where = (
            f"{where} AND branch = ?" if where else " WHERE branch = ?"
        )
        for row in conn.execute(
            f"SELECT appid, depotid, manifestid FROM oracle_branch_manifests{branch_where}",
            [*params, oracle.BRANCH_PUBLIC],
        ):
            appid = deletion.coerce_positive_id(row["appid"])
            depotid = deletion.coerce_positive_id(row["depotid"])
            gid = gc.valid_manifest_id(row["manifestid"])
            if appid is not None and depotid is not None and gid is not None:
                public[(appid, depotid)] = gid

    targets: dict[tuple[int, int], str | None] = {}
    for pair in pairs:
        oracle_gid = public.get(pair)
        recorded_gid, recorded_at = recorded.get(pair, (None, ""))
        if oracle_gid is None:
            targets[pair] = recorded_gid
        elif recorded_gid is not None and recorded_at > oracle_checked.get(pair[0], ""):
            # vault-api parsed a manifest AFTER the oracle's last answer: its
            # own first-hand record is the newer knowledge (both timestamps are
            # 'YYYY-MM-DDTHH:MM:SSZ', so string order is time order).
            targets[pair] = recorded_gid
        else:
            targets[pair] = oracle_gid
    return targets


# --------------------------------------------------------------------------
# Evaluation (update check thread, prefill worker)
# --------------------------------------------------------------------------

#: SQLite INTEGER is signed 64-bit. A manifest whose chunk sizes sum past it is
#: corrupt or hostile (no real depot is 8 EiB); it lands on
#: ``STATUS_MANIFEST_UNAVAILABLE`` with a debug log line and its sizes are not
#: stored (the insert would raise ``OverflowError``).
INT64_MAX = 2**63 - 1


class EvaluationRun:
    """State shared by every app of ONE run.

    One ``gc.ManifestReader`` and one listing per depot (stored manifests +
    chunk scan). Only depots that more than one app of the run maps
    (``shared_depots``) are kept after use, so a shared depot is scanned and
    parsed once; every other depot's listing and parsed manifest is dropped
    as soon as it is measured, which bounds memory to roughly one large depot
    plus the shared ones (a 150k-chunk listing is tens of MB in Python).
    ``should_abort`` is checked between depots.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        shared_depots: Iterable[int] = (),
        should_abort: Callable[[], bool] = _never,
    ) -> None:
        self.settings = settings
        self.reader = gc.ManifestReader()
        self.shared_depots = frozenset(shared_depots)
        self.should_abort = should_abort
        try:
            self.depot_root: str | None = deletion.resolve_depot_root(settings.cache_root)
        except deletion.DeletionGuardError:
            self.depot_root = None
        self._stored: dict[int, Mapping[str, Sequence[gc.StoredManifestCopy]]] = {}
        self._chunks: dict[int, dict[str, int]] = {}
        #: How many chunk directories were scanned (tests pin "once per depot").
        self.chunk_scans = 0

    def stored_manifests(
        self, depotid: int, depot_dir: str
    ) -> Mapping[str, Sequence[gc.StoredManifestCopy]]:
        cached = self._stored.get(depotid)
        if cached is None:
            cached = gc.scan_stored_manifests(depot_dir)
            self._stored[depotid] = cached
        return cached

    def chunks_on_disk(self, depotid: int, depot_dir: str) -> dict[str, int]:
        cached = self._chunks.get(depotid)
        if cached is None:
            cached = gc.scan_depot_chunks(depot_dir).chunks
            self.chunk_scans += 1
            self._chunks[depotid] = cached
        return cached

    def depot_exists(self, depotid: int) -> bool:
        """Is the depot directory still there? (``store_app``'s race check.)"""
        if self.depot_root is None:
            return False
        try:
            return os.path.isdir(deletion.depot_dir_path(self.depot_root, depotid))
        except deletion.DeletionGuardError:
            return False

    def release(self, depotid: int, paths: Iterable[str | None]) -> None:
        """Forget a non-shared depot's listing and parses after measuring it."""
        if depotid in self.shared_depots:
            return
        self._stored.pop(depotid, None)
        self._chunks.pop(depotid, None)
        for path in paths:
            if path:
                self.reader.forget(path)


def evaluate_depot(
    appid: int,
    depotid: int,
    target: str | None,
    *,
    run: EvaluationRun,
    checked_at: str,
) -> DepotCompleteness:
    """Measure one (app, depot) pair against its target manifest. Never raises
    for missing or bad data; reads only."""

    def row(status: str, **fields: object) -> DepotCompleteness:
        return DepotCompleteness(
            appid=appid,
            depotid=depotid,
            manifestid=target,
            status=status,
            checked_at=checked_at,
            **fields,  # type: ignore[arg-type]
        )

    if target is None:
        return row(STATUS_NO_TARGET)
    if run.depot_root is None:
        return row(STATUS_UNREADABLE_CACHE)
    try:
        depot_dir = deletion.depot_dir_path(run.depot_root, depotid)
    except deletion.DeletionGuardError:
        return row(STATUS_UNREADABLE_CACHE)
    if gc.is_link_like(depot_dir):
        return row(STATUS_UNREADABLE_CACHE)

    archive_dir = run.settings.manifest_archive_dir
    archive_path = os.path.join(
        archive_dir, archive_filename(depotid=depotid, manifestid=target)
    )
    parsed, source, path, error = gc.read_manifest_by_id(
        depotid,
        target,
        archive_dir=archive_dir,
        stored_manifests=run.stored_manifests(depotid, depot_dir),
        reader=run.reader,
    )
    try:
        if parsed is None:
            logger.debug(
                "cache completeness: app %d depot %d manifest %s unreadable: %s",
                appid, depotid, target, error,
            )
            return row(STATUS_MANIFEST_UNAVAILABLE)

        expected = sum(parsed.chunks.values())
        if expected > INT64_MAX:
            logger.debug(
                "cache completeness: app %d depot %d manifest %s declares %d "
                "bytes (past INT64); treated as unavailable",
                appid, depotid, target, expected,
            )
            return row(STATUS_MANIFEST_UNAVAILABLE)

        on_disk = run.chunks_on_disk(depotid, depot_dir)
        present = 0
        missing = 0
        mismatched = 0
        for chunk_id, size in parsed.chunks.items():
            disk_size = on_disk.get(chunk_id)
            if disk_size is None:
                missing += 1
            elif disk_size != size:
                mismatched += 1
            else:
                present += size
        return row(
            STATUS_OK,
            source=source,
            expected_bytes=expected,
            present_bytes=present,
            chunk_count=len(parsed.chunks),
            missing_chunks=missing,
            mismatched_chunks=mismatched,
        )
    finally:
        run.release(depotid, (archive_path, path))


def evaluate_app(
    conn: sqlite3.Connection,
    settings: Settings,
    appid: int,
    *,
    run: EvaluationRun | None = None,
    checked_at: str | None = None,
) -> list[DepotCompleteness] | None:
    """Evaluate every mapped depot of one app (no writes). ``None`` when the
    run was aborted between two depots: a partial app is never stored."""
    stamp = checked_at or jobs.utcnow_iso()
    run = run or EvaluationRun(settings)
    targets = current_targets(conn, settings, [appid])
    rows: list[DepotCompleteness] = []
    for (owner, depotid), target in sorted(targets.items()):
        if owner != appid:
            continue
        if run.should_abort():
            return None
        rows.append(
            evaluate_depot(appid, depotid, target, run=run, checked_at=stamp)
        )
    return rows


def store_app(
    conn: sqlite3.Connection,
    appid: int,
    rows: Sequence[DepotCompleteness],
    *,
    depot_exists: Callable[[int], bool] | None = None,
) -> bool:
    """Replace the app's stored rows with ``rows`` in one transaction (a depot
    no longer mapped stops being reported). Returns False when nothing new was
    stored.

    **Race with ``DELETE /v1/cache/{appid}`` (``depot_exists``).** A delete
    removes depot directories first and then drops their rows
    (``forget``). A measurement read before the removal must not be stored
    after the drop. So, inside this write transaction, every measured depot
    that had bytes present is checked to still exist; if one is gone the app's
    rows are dropped instead of stored. Holding ``BEGIN IMMEDIATE`` closes the
    window: a removal after the check makes the delete's ``forget`` wait for
    this commit and then drop these rows.
    """
    with jobs.immediate_transaction(conn):
        conn.execute("DELETE FROM cache_completeness WHERE appid = ?", (appid,))
        if depot_exists is not None:
            vanished = [
                r.depotid for r in rows
                if (r.present_bytes or 0) > 0 and not depot_exists(r.depotid)
            ]
            if vanished:
                logger.debug(
                    "cache completeness: app %d depot(s) %s vanished during the "
                    "measurement (cache delete); nothing stored",
                    appid, vanished,
                )
                return False
        conn.executemany(
            """
            INSERT INTO cache_completeness (
                appid, depotid, manifestid, status, source, expected_bytes,
                present_bytes, chunk_count, missing_chunks, mismatched_chunks,
                checked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    r.appid, r.depotid, r.manifestid, r.status, r.source,
                    r.expected_bytes, r.present_bytes, r.chunk_count,
                    r.missing_chunks, r.mismatched_chunks, r.checked_at,
                )
                for r in rows
            ],
        )
    return True


def _shared_depots(conn: sqlite3.Connection, appids: Sequence[int]) -> set[int]:
    """Depots more than one of ``appids`` maps."""
    if len(appids) < 2:
        return set()
    owners: dict[int, int] = {}
    selected = set(appids)
    for row in conn.execute("SELECT depotid, appid FROM depot_app_map"):
        if row["appid"] in selected:
            owners[row["depotid"]] = owners.get(row["depotid"], 0) + 1
    return {depotid for depotid, count in owners.items() if count > 1}


@dataclass(frozen=True)
class RunOutcome:
    stored: int
    aborted: bool


def evaluate_apps(
    conn: sqlite3.Connection,
    settings: Settings,
    appids: Iterable[int],
    *,
    should_abort: Callable[[], bool] = _never,
) -> RunOutcome:
    """Evaluate and store each app with one shared ``EvaluationRun``. One
    app's failure is logged and skipped, never fatal to the rest. No pruning
    (see ``evaluate_selection``)."""
    ids = sorted(set(appids))
    run = EvaluationRun(
        settings, shared_depots=_shared_depots(conn, ids), should_abort=should_abort
    )
    stored = 0
    for appid in ids:
        if should_abort():
            return RunOutcome(stored, aborted=True)
        try:
            rows = evaluate_app(conn, settings, appid, run=run)
            if rows is None:
                return RunOutcome(stored, aborted=True)
            if store_app(conn, appid, rows, depot_exists=run.depot_exists):
                stored += 1
        except Exception:  # noqa: BLE001 - one app must not stop the run
            logger.exception("cache completeness: evaluating app %d failed", appid)
    return RunOutcome(stored, aborted=False)


def evaluate_selection(
    conn: sqlite3.Connection,
    settings: Settings,
    appids: Iterable[int],
    *,
    should_abort: Callable[[], bool] = _never,
) -> RunOutcome:
    """The update check's step: evaluate the selected (cached) games, then --
    only if the run was not aborted -- drop the rows of every app outside the
    selection (no longer cached, or a tool package), so a stale figure cannot
    outlive the content it describes."""
    ids = sorted(set(appids))
    outcome = evaluate_apps(conn, settings, ids, should_abort=should_abort)
    if not outcome.aborted:
        prune_except(conn, ids)
    return outcome


def remeasure_app(conn: sqlite3.Connection, settings: Settings, appid: int) -> None:
    """Measure one app now and store it (the prefill worker, after a
    successful job for that app). Never prunes other apps."""
    run = EvaluationRun(settings)
    rows = evaluate_app(conn, settings, appid, run=run)
    if rows is not None:
        store_app(conn, appid, rows, depot_exists=run.depot_exists)


def prune_except(conn: sqlite3.Connection, appids: Iterable[int]) -> int:
    """Delete the rows of every app NOT in ``appids``; returns how many apps."""
    keep = set(appids)
    stale = [
        int(row["appid"])
        for row in conn.execute("SELECT DISTINCT appid FROM cache_completeness")
        if int(row["appid"]) not in keep
    ]
    if stale:
        with jobs.immediate_transaction(conn):
            conn.executemany(
                "DELETE FROM cache_completeness WHERE appid = ?", [(a,) for a in stale]
            )
    return len(stale)


def forget(
    conn: sqlite3.Connection,
    *,
    appids: Iterable[int] = (),
    depotids: Iterable[int] = (),
) -> None:
    """Delete the rows of these apps and of these depots (any app) -- used by
    ``DELETE /v1/cache/{appid}``: a removed depot's figure is unknown until the
    next check, for every app that maps it."""
    with jobs.immediate_transaction(conn):
        conn.executemany(
            "DELETE FROM cache_completeness WHERE appid = ?", [(a,) for a in set(appids)]
        )
        conn.executemany(
            "DELETE FROM cache_completeness WHERE depotid = ?",
            [(d,) for d in set(depotids)],
        )


# --------------------------------------------------------------------------
# Read side (API)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CompletenessIndex:
    """Stored rows plus the current targets, read once per request."""

    rows: Mapping[tuple[int, int], DepotCompleteness]
    targets: Mapping[tuple[int, int], str | None]

    def figure(self, pairs: Iterable[tuple[int, int]]) -> Completeness:
        """Aggregate (app, depot) pairs; unknown if any pair is unknown."""
        seen: set[tuple[int, str]] = set()
        expected = 0
        present = 0
        missing: int | None = 0
        oldest: str | None = None
        any_pair = False
        for pair in pairs:
            any_pair = True
            row = self.rows.get(pair)
            target = self.targets.get(pair)
            if (
                row is None
                or row.status != STATUS_OK
                or target is None
                or row.manifestid != target
                or row.expected_bytes is None
                or row.present_bytes is None
            ):
                return UNKNOWN
            if oldest is None or row.checked_at < oldest:
                oldest = row.checked_at
            key = (row.depotid, target)
            if key in seen:
                continue
            seen.add(key)
            expected += max(0, row.expected_bytes)
            present += max(0, min(row.present_bytes, row.expected_bytes))
            if missing is not None:
                missing = None if row.missing_chunks is None else missing + max(
                    0, row.missing_chunks
                )
        if not any_pair:
            return UNKNOWN
        pct = 100 if expected == 0 else (present * 100) // expected
        return Completeness(
            pct=pct,
            expected_bytes=expected,
            present_bytes=present,
            checked_at=oldest,
            missing_chunks=missing,
        )


def load_index(
    conn: sqlite3.Connection,
    settings: Settings,
    appids: Iterable[int] | None = None,
) -> CompletenessIndex:
    """Read the stored rows and current targets (all apps, or ``appids``)."""
    ids = None if appids is None else sorted(set(appids))
    rows: dict[tuple[int, int], DepotCompleteness] = {}
    if ids is None or ids:
        where, params = _ids_clause(ids, "appid")
        for row in conn.execute(f"SELECT * FROM cache_completeness{where}", params):
            rows[(int(row["appid"]), int(row["depotid"]))] = DepotCompleteness(
                appid=int(row["appid"]),
                depotid=int(row["depotid"]),
                manifestid=row["manifestid"],
                status=row["status"],
                checked_at=row["checked_at"],
                source=row["source"],
                expected_bytes=row["expected_bytes"],
                present_bytes=row["present_bytes"],
                chunk_count=row["chunk_count"],
                missing_chunks=row["missing_chunks"],
                mismatched_chunks=row["mismatched_chunks"],
            )
    return CompletenessIndex(rows=rows, targets=current_targets(conn, settings, ids))


# --------------------------------------------------------------------------
# Incomplete games (WP CACHE-COMPLETE-1b)
# --------------------------------------------------------------------------


def app_figure(
    index: CompletenessIndex,
    appid: int,
    depotids: Iterable[int],
    size_bytes: int | None,
) -> Completeness:
    """One app's figure as ``GET /v1/games`` reports it. A stored figure is
    never shown for a game with no bytes on disk (rows survive a delete until
    the next check)."""
    if not size_bytes:
        return UNKNOWN
    return index.figure((appid, d) for d in depotids)


def is_incomplete(figure: Completeness) -> bool:
    """THE predicate behind ``GET /v1/games?incomplete=true``, ``POST
    /v1/prefill/incomplete`` and the scheduler's automatic completion: a known
    figure below 100. Unknown is never incomplete (nothing to act on)."""
    return figure.pct is not None and figure.pct < 100


def is_completable(figure: Completeness) -> bool:
    """Incomplete AND short of at least one chunk file -- what "Complete all"
    and the scheduler's automatic completion act on. A game short only of
    chunk files with the wrong size is incomplete but not completable: a
    forced prefill serves those files from disk and cannot replace them
    (repair is the CACHE-REPAIR-1 follow-up)."""
    return is_incomplete(figure) and (figure.missing_chunks or 0) > 0


@dataclass(frozen=True)
class IncompleteApp:
    appid: int
    figure: Completeness


def incomplete_apps(
    conn: sqlite3.Connection,
    settings: Settings,
    depot_bytes: Mapping[int, int],
) -> list[IncompleteApp]:
    """Every tracked app (``apps`` row) that ``is_incomplete``, in appid
    order, computed exactly like the games list: ``depot_app_map`` depots,
    ``size_bytes`` from ``depot_bytes`` (``sizes.app_size_bytes``),
    ``app_figure``. Steam tool packages are left out (they are never
    prefilled, so there is nothing to enqueue). Reads only."""
    appids = [int(r["appid"]) for r in conn.execute("SELECT appid FROM apps ORDER BY appid")]
    depots: dict[int, list[int]] = {}
    for row in conn.execute("SELECT appid, depotid FROM depot_app_map"):
        depots.setdefault(int(row["appid"]), []).append(int(row["depotid"]))
    index = load_index(conn, settings)
    disk = dict(depot_bytes)
    found: list[IncompleteApp] = []
    for appid in appids:
        if tool_apps.is_tool_app(appid):
            continue
        app_depots = depots.get(appid, [])
        size = sizes.app_size_bytes(app_depots, disk)
        figure = app_figure(index, appid, app_depots, size)
        if is_incomplete(figure):
            found.append(IncompleteApp(appid=appid, figure=figure))
    return found


def any_stored_missing(conn: sqlite3.Connection) -> bool:
    """Cheap pre-check for the scheduler: is any stored depot row short of a
    chunk file? False means no app can be auto-completed, so the sweep skips
    the depot walk ``incomplete_apps`` needs."""
    row = conn.execute(
        "SELECT 1 FROM cache_completeness WHERE status = ? AND missing_chunks > 0 LIMIT 1",
        (STATUS_OK,),
    ).fetchone()
    return row is not None
