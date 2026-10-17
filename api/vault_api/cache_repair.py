"""Repair of wrong-size chunk files (WP CACHE-REPAIR-1).

CACHE-COMPLETE-1a counts a chunk file whose size differs from the target
manifest's ``cb_compressed`` as *damaged* (``mismatched_chunks``). A forced
prefill cannot fix it: vault-core serves the existing file as a HIT. The only
repair is to remove the damaged file so the next forced run misses on it and
stores a fresh copy.

This module has two halves, both without a network and without a database
write:

``plan_repair``
    Reads the app's target manifests (``cache_completeness.current_targets`` +
    ``gc.read_manifest_by_id``, i.e. exactly the figure the games list shows)
    and the depots' ``chunk/`` directories (``gc.scan_depot_chunks``, which
    already leaves links, directories and oddly named entries out) and lists
    the chunk files whose on-disk size differs from the manifest's.

``execute_repair``
    Removes the planned files, one at a time, behind the cache-root guards of
    ``deletion.py``. Per file, immediately before the unlink, it re-checks:

    - the depot directory and its ``chunk/`` directory are real directories
      (no symlink or junction) and still resolve inside the resolved
      ``depot/`` root;
    - the path is built by ``deletion.safe_child_path`` from a 40-hex chunk id
      (``gc.is_chunk_filename``), so nothing but a direct child of
      ``chunk/`` can be named;
    - the entry is a regular file (``lstat``, links are never followed);
    - its size is STILL the size the scan saw (a concurrent write -- vault-core
      storing the chunk, a client download -- changes it, and a file that is
      growing is not ours to delete) AND still differs from the manifest's
      size (if it now matches, it was repaired meanwhile: kept).

Anything that fails a re-check is skipped and counted, never deleted.

**Mass-deletion guard.** A plan that names more than ``MAX_REPAIR_FILES`` files
is refused as a whole (``exceeds_cap``): real damage is a handful of chunks, so
thousands of mismatches point at a wrong target manifest or a broken cache, and
a person should look first.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import stat
from dataclasses import dataclass, field
from typing import Callable, Iterable

from vault_api import cache_completeness, deletion, gc
from vault_api.config import Settings
from vault_api.manifest_archive import archive_filename
from vault_api.sizes import is_link_like

logger = logging.getLogger(__name__)

#: Most chunk files one repair request may delete (all depots of the app
#: together). The plan is refused above it; nothing is deleted.
MAX_REPAIR_FILES = 2000

#: Why a planned file was not deleted.
SKIP_NOT_REGULAR = "not_regular_file"
SKIP_GONE = "gone"
SKIP_SIZE_CHANGED = "size_changed"
SKIP_SIZE_NOW_MATCHES = "size_now_matches"
SKIP_UNSAFE_PATH = "unsafe_path"
SKIP_FAILED = "failed"


@dataclass(frozen=True)
class DamagedChunk:
    chunk_id: str
    #: Size the target manifest declares (``cb_compressed``).
    expected_size: int
    #: Size the scan found on disk.
    found_size: int


@dataclass
class DepotRepairPlan:
    depotid: int
    manifestid: str
    chunks: list[DamagedChunk] = field(default_factory=list)


@dataclass
class RepairPlan:
    appid: int
    depots: list[DepotRepairPlan] = field(default_factory=list)
    #: Depots whose target could not be read (no target, manifest unavailable,
    #: unusable depot directory). Reported, never guessed at.
    unreadable_depots: list[int] = field(default_factory=list)

    @property
    def damaged_count(self) -> int:
        return sum(len(d.chunks) for d in self.depots)

    @property
    def damaged_bytes(self) -> int:
        return sum(c.found_size for d in self.depots for c in d.chunks)

    @property
    def exceeds_cap(self) -> bool:
        return self.damaged_count > MAX_REPAIR_FILES


@dataclass
class RepairResult:
    removed: int = 0
    removed_bytes: int = 0
    #: ``reason -> count`` for every planned file that was not deleted.
    skipped: dict[str, int] = field(default_factory=dict)

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


def plan_repair(
    conn: sqlite3.Connection,
    settings: Settings,
    appid: int,
    *,
    depot_root: str | None = None,
) -> RepairPlan:
    """List the wrong-size chunk files of ``appid``'s target manifests. Reads only.

    ``depot_root`` defaults to the resolved cache root (a guard failure raises
    ``deletion.DeletionGuardError``, like every other deletion path).
    """
    root = depot_root or deletion.resolve_depot_root(settings.cache_root)
    plan = RepairPlan(appid=appid)
    reader = gc.ManifestReader()
    targets = cache_completeness.current_targets(conn, settings, [appid])
    for (owner, depotid), target in sorted(targets.items()):
        if owner != appid:
            continue
        if target is None:
            plan.unreadable_depots.append(depotid)
            continue
        try:
            depot_dir = deletion.depot_dir_path(root, depotid)
        except deletion.DeletionGuardError:
            plan.unreadable_depots.append(depotid)
            continue
        if is_link_like(depot_dir):
            plan.unreadable_depots.append(depotid)
            continue
        archive_path = os.path.join(
            settings.manifest_archive_dir,
            archive_filename(depotid=depotid, manifestid=target),
        )
        parsed, _source, path, _error = gc.read_manifest_by_id(
            depotid,
            target,
            archive_dir=settings.manifest_archive_dir,
            stored_manifests=gc.scan_stored_manifests(depot_dir),
            reader=reader,
        )
        try:
            if parsed is None:
                plan.unreadable_depots.append(depotid)
                continue
            on_disk = gc.scan_depot_chunks(depot_dir).chunks
            damaged = [
                DamagedChunk(chunk_id, size, on_disk[chunk_id])
                for chunk_id, size in sorted(parsed.chunks.items())
                if chunk_id in on_disk and on_disk[chunk_id] != size
            ]
        finally:
            for p in (archive_path, path):
                if p:
                    reader.forget(p)
        if damaged:
            plan.depots.append(DepotRepairPlan(depotid, target, damaged))
    return plan


def _within(root: str, path: str) -> bool:
    try:
        return os.path.commonpath([root, os.path.realpath(path)]) == root
    except ValueError:
        return False


def execute_repair(
    plan: RepairPlan,
    depot_root: str,
    *,
    unlink: Callable[[str], tuple[bool, Exception | None]] = deletion.remove_file_settling,
) -> RepairResult:
    """Delete the planned files after the per-file re-checks (module docstring).

    ``depot_root`` must come from ``deletion.resolve_depot_root``. Never raises
    for a single file; a file that fails any check is skipped and counted.
    """
    result = RepairResult()
    if plan.exceeds_cap:  # defence in depth: the caller refuses first
        for depot in plan.depots:
            for _ in depot.chunks:
                result.skip(SKIP_UNSAFE_PATH)
        return result
    for depot in plan.depots:
        try:
            depot_dir = deletion.depot_dir_path(depot_root, depot.depotid)
            chunk_dir = os.path.join(depot_dir, gc.CHUNK_DIRNAME)
        except deletion.DeletionGuardError:
            for _ in depot.chunks:
                result.skip(SKIP_UNSAFE_PATH)
            continue
        dirs_ok = (
            not is_link_like(depot_dir)
            and not is_link_like(chunk_dir)
            and _within(depot_root, chunk_dir)
        )
        for chunk in depot.chunks:
            if not dirs_ok or not gc.is_chunk_filename(chunk.chunk_id):
                result.skip(SKIP_UNSAFE_PATH)
                continue
            try:
                path = deletion.safe_child_path(chunk_dir, chunk.chunk_id)
            except deletion.DeletionGuardError:
                result.skip(SKIP_UNSAFE_PATH)
                continue
            try:
                st = os.lstat(path)
            except FileNotFoundError:
                result.skip(SKIP_GONE)
                continue
            except OSError:
                result.skip(SKIP_FAILED)
                continue
            if not stat.S_ISREG(st.st_mode):
                result.skip(SKIP_NOT_REGULAR)
                continue
            if st.st_size == chunk.expected_size:
                result.skip(SKIP_SIZE_NOW_MATCHES)
                continue
            if st.st_size != chunk.found_size:
                result.skip(SKIP_SIZE_CHANGED)
                continue
            removed, error = unlink(path)
            if removed:
                result.removed += 1
                result.removed_bytes += st.st_size
            elif error is not None:
                result.skip(SKIP_FAILED)
            else:
                result.skip(SKIP_GONE)
    logger.info(
        "cache-repair appid=%s removed=%d bytes=%d skipped=%s",
        plan.appid, result.removed, result.removed_bytes, result.skipped,
    )
    return result


def apps_for_depots(conn: sqlite3.Connection, depotids: Iterable[int]) -> set[int]:
    """Every app that maps one of ``depotids`` (a shared depot's co-owners)."""
    ids = sorted(set(depotids))
    if not ids:
        return set()
    marks = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT DISTINCT appid FROM depot_app_map WHERE depotid IN ({marks})", ids
    )
    return {int(r["appid"]) for r in rows}
