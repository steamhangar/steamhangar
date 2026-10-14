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
   (``gc._read_manifest_by_id``): the archived ``.bin`` first, else a
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

## DLC groups (D9)

``group_cache_complete_pct`` aggregates the union of the group members'
(app, depot) pairs, each (depot, manifest) counted once, like
``group_size_bytes``.

## When it runs

As part of every update check (``update_check.UpdateCheckRunner._run``, UPD-5
cadence, both triggers), for the cached games the check selected, after the
oracle refresh. Results are stored per (app, depot) in ``cache_completeness``
and the API only reads them. Between two checks the stored figure can lag
(a prefill or client download that finished since); the next check corrects
it. Nothing here downloads, deletes or writes anywhere but that table.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping, Sequence

from vault_api import deletion, gc, jobs, oracle
from vault_api.config import Settings

logger = logging.getLogger(__name__)

#: ``cache_completeness.status`` values.
STATUS_OK = "ok"
#: Neither a recorded manifest nor an oracle ``public`` gid for the depot.
STATUS_NO_TARGET = "no_target"
#: The target manifest id is known, but neither the archive nor a cache-stored
#: copy yields its chunk list.
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
# Evaluation (update check thread)
# --------------------------------------------------------------------------


def evaluate_depot(
    appid: int,
    depotid: int,
    target: str | None,
    *,
    depot_root: str | None,
    archive_dir: str,
    reader: "gc._ManifestReader",
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
    if depot_root is None:
        return row(STATUS_UNREADABLE_CACHE)
    try:
        depot_dir = deletion.depot_dir_path(depot_root, depotid)
    except deletion.DeletionGuardError:
        return row(STATUS_UNREADABLE_CACHE)
    if gc.is_link_like(depot_dir):
        return row(STATUS_UNREADABLE_CACHE)

    stored = gc.scan_stored_manifests(depot_dir)
    parsed, source, _path, error = gc._read_manifest_by_id(
        depotid,
        target,
        archive_dir=archive_dir,
        stored_manifests=stored,
        reader=reader,
    )
    if parsed is None:
        logger.debug(
            "cache completeness: app %d depot %d manifest %s unreadable: %s",
            appid, depotid, target, error,
        )
        return row(STATUS_MANIFEST_UNAVAILABLE)

    on_disk = gc.scan_depot_chunks(depot_dir).chunks
    expected = 0
    present = 0
    missing = 0
    mismatched = 0
    for chunk_id, size in parsed.chunks.items():
        expected += size
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


def evaluate_app(
    conn: sqlite3.Connection,
    settings: Settings,
    appid: int,
    *,
    checked_at: str | None = None,
) -> list[DepotCompleteness]:
    """Evaluate every mapped depot of one app (no writes)."""
    stamp = checked_at or jobs.utcnow_iso()
    targets = current_targets(conn, settings, [appid])
    try:
        depot_root: str | None = deletion.resolve_depot_root(settings.cache_root)
    except deletion.DeletionGuardError:
        depot_root = None
    reader = gc._ManifestReader()
    return [
        evaluate_depot(
            appid,
            depotid,
            target,
            depot_root=depot_root,
            archive_dir=settings.manifest_archive_dir,
            reader=reader,
            checked_at=stamp,
        )
        for (owner, depotid), target in sorted(targets.items())
        if owner == appid
    ]


def store_app(
    conn: sqlite3.Connection, appid: int, rows: Sequence[DepotCompleteness]
) -> None:
    """Replace the app's stored rows with ``rows`` in one transaction (a depot
    no longer mapped stops being reported)."""
    with jobs.immediate_transaction(conn):
        conn.execute("DELETE FROM cache_completeness WHERE appid = ?", (appid,))
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


def evaluate_apps(
    conn: sqlite3.Connection,
    settings: Settings,
    appids: Iterable[int],
    *,
    should_abort: Callable[[], bool] = _never,
) -> int:
    """Evaluate and store each app; returns how many were stored. One app's
    failure is logged and skipped, never fatal to the rest."""
    stored = 0
    for appid in sorted(set(appids)):
        if should_abort():
            break
        try:
            store_app(conn, appid, evaluate_app(conn, settings, appid))
            stored += 1
        except Exception:  # noqa: BLE001 - one app must not stop the run
            logger.exception("cache completeness: evaluating app %d failed", appid)
    return stored


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
        if not any_pair:
            return UNKNOWN
        pct = 100 if expected == 0 else (present * 100) // expected
        return Completeness(
            pct=pct, expected_bytes=expected, present_bytes=present, checked_at=oldest
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
