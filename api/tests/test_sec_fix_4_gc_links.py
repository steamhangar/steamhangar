"""WP SEC-FIX-4 S-2: GC never follows a symlinked directory on the shared volume.

Threat: code execution as uid 101 in vault-core plants a directory symlink on
/vault; vault-api (same uid, its own mount namespace) would resolve it and
reach /data. Each level the planner walks gets one test, plus the execute-time
realpath check for a plan made before the link was planted.
"""

from __future__ import annotations

import os
import types
from pathlib import Path

from vault_api import deletion, gc, gc_execute

CHUNK = "a" * 40


def _private(tmp_path: Path) -> Path:
    """Stands in for vault-api's /data: two identical files and a chunk-named one."""
    private = tmp_path / "private"
    private.mkdir()
    for name in ("backup-a.json", "backup-b.json"):
        (private / name).write_text("same\n")
    (private / CHUNK).write_bytes(b"x" * 10)
    return private


def _depot_root(tmp_path: Path) -> Path:
    root = tmp_path / "cache" / "depot"
    root.mkdir(parents=True)
    return root


def test_a_linked_depot_dir_is_skipped_by_the_planner(tmp_path: Path) -> None:
    private = _private(tmp_path)
    root = _depot_root(tmp_path)
    os.symlink(private, root / "441")

    plan = gc._plan_one_depot(
        441, [], depot_root=str(root), archive_dir=str(tmp_path / "a"), reader=None
    )

    assert plan.status == gc.STATUS_LINKED_DIR
    assert plan.orphan_chunks == {}
    assert not plan.dedupe


def test_a_linked_chunk_dir_is_not_scanned(tmp_path: Path) -> None:
    private = _private(tmp_path)
    depot = _depot_root(tmp_path) / "441"
    depot.mkdir()
    os.symlink(private, depot / gc.CHUNK_DIRNAME)

    scan = gc.scan_depot_chunks(str(depot))

    assert scan.chunks == {}
    assert scan.chunk_dir_exists is False


def test_a_linked_manifest_dir_is_not_scanned(tmp_path: Path) -> None:
    private = _private(tmp_path)
    (private / "123" / "5").mkdir(parents=True)
    for name in ("backup-a.json", "backup-b.json"):
        (private / "123" / "5" / name).write_text("same\n")
    depot = _depot_root(tmp_path) / "441"
    depot.mkdir()
    os.symlink(private, depot / gc.MANIFEST_DIRNAME)

    assert gc.scan_stored_manifests(str(depot)) == {}


def test_a_linked_request_dir_is_not_scanned_or_deduplicated(tmp_path: Path) -> None:
    """gcprobe.py's shape: manifest/<mid>/5 -> the private directory."""
    private = _private(tmp_path)
    root = _depot_root(tmp_path)
    mdir = root / "441" / gc.MANIFEST_DIRNAME / "123"
    mdir.mkdir(parents=True)
    os.symlink(private, mdir / gc.MANIFEST_REQUEST_DIR)
    depot_dir = deletion.depot_dir_path(str(root), 441)

    stored = gc.scan_stored_manifests(depot_dir)
    plan = types.SimpleNamespace(dedupe=gc.dedupe_candidates(stored))
    gc_execute.execute_dedupe(plan, depot_dir=depot_dir)

    assert stored == {}
    assert sorted(os.listdir(private)) == sorted(["backup-a.json", "backup-b.json", CHUNK])


def test_execute_refuses_a_chunk_dir_linked_after_planning(tmp_path: Path) -> None:
    """The plan was made against a real chunk/; the link appeared afterwards."""
    private = _private(tmp_path)
    root = _depot_root(tmp_path)
    (root / "441").mkdir()
    os.symlink(private, root / "441" / gc.CHUNK_DIRNAME)

    result = gc_execute.execute_depot(
        gc.DepotGcPlan(depotid=441, status=gc.STATUS_PLANNED, orphan_chunks={CHUNK: 10}),
        depot_root=str(root),
    )

    assert [p.outcome for p in result.problems] == [gc_execute.REFUSED_LINK]
    assert (private / CHUNK).exists()


def test_execute_refuses_a_request_dir_linked_after_planning(tmp_path: Path) -> None:
    private = _private(tmp_path)
    root = _depot_root(tmp_path)
    mdir = root / "441" / gc.MANIFEST_DIRNAME / "123"
    mdir.mkdir(parents=True)
    os.symlink(private, mdir / gc.MANIFEST_REQUEST_DIR)
    depot_dir = deletion.depot_dir_path(str(root), 441)
    request_dir = mdir / gc.MANIFEST_REQUEST_DIR

    def copy(name: str, mtime: int) -> gc.StoredManifestCopy:
        return gc.StoredManifestCopy(
            manifestid="123", path=str(request_dir / name), size_bytes=5, mtime_ns=mtime
        )

    plan = types.SimpleNamespace(
        dedupe=(
            gc.DedupeCandidate(
                manifestid="123",
                keep=copy("backup-b.json", 2),
                duplicates=(copy("backup-a.json", 1),),
            ),
        )
    )
    results = gc_execute.execute_dedupe(plan, depot_dir=depot_dir)

    assert [r.outcome for r in results] == [gc_execute.REFUSED_LINK]
    assert (private / "backup-a.json").exists()


def test_a_real_tree_still_collects(tmp_path: Path) -> None:
    """The realpath check must not refuse the ordinary, link-free case."""
    root = _depot_root(tmp_path)
    chunk_dir = root / "441" / gc.CHUNK_DIRNAME
    chunk_dir.mkdir(parents=True)
    (chunk_dir / CHUNK).write_bytes(b"x" * 10)

    result = gc_execute.execute_depot(
        gc.DepotGcPlan(depotid=441, status=gc.STATUS_PLANNED, orphan_chunks={CHUNK: 10}),
        depot_root=str(root),
    )

    assert result.removed_count == 1
    assert not (chunk_dir / CHUNK).exists()


def test_gc_still_collects_when_the_cache_base_itself_is_a_link(
    tmp_path: Path,
) -> None:
    """Review S3: a linked base (bind-mount style) resolves consistently."""
    real = tmp_path / "realcache"
    chunk_dir = real / "depot" / "441" / gc.CHUNK_DIRNAME
    chunk_dir.mkdir(parents=True)
    (chunk_dir / CHUNK).write_bytes(b"x" * 10)
    os.symlink(real, tmp_path / "linkcache")

    plan = gc.DepotGcPlan(
        depotid=441, status=gc.STATUS_PLANNED, orphan_chunks={CHUNK: 10}
    )
    result = gc_execute.execute_depot(
        plan, depot_root=str(tmp_path / "linkcache" / "depot")
    )

    assert result.removed_count == 1
    assert not result.problems
    assert not (chunk_dir / CHUNK).exists()
