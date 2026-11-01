"""WP MAP-LOSS-1 (rc21.1): an app's OWN depot lost its ``depot_app_map`` row
although its own recorded manifest and the oracle name it.

Prod evidence (read-only, ``dev.sh diag db``, 2026-11-01), both runs on
rc20.2 (the rc21 rollout was 2026-11-01):

- Enshrouded (1203620), prefill job 205, 2026-10-31 11:46-11:49Z, "Updated
  1", 28.84 GiB at 1.65 Gbit/s (all HITs, nothing written into 1203621):
  ``Depot mapping updated (replace-semantics for this app): added=[1465471]
  removed=[1203621] unchanged=[]`` followed by ``Manifest ingestion:
  recorded 1 manifest(s) for depot(s) [1203621]``. The replace removed the
  own depot, and ingestion could not map it back because the worker passes
  the removed depots as ``skip_own_mapping``.
- The Witcher 3 (292030), prefill job 207, 2026-10-31 11:58-12:07Z:
  ``removed=[292033, 292036, 310611, 310619]``, while the same job recorded
  own manifests (containing 292030) for 292033 and 292036.

rc21's GROUP-VERIFIED-1 keeps an unobserved depot named by the app's own
temp-cache ``.bin`` files, which covers the exact prod sequence (L1). The
remaining gap: when those files are gone (SteamPrefill ``clear-temp``, an
operator emptying the temp cache) the evidence is "readable, names nothing"
and the replace removed the depot although ``depot_manifests`` and the
oracle still name it as the app's own (L2, L3).

Guarantees pinned here, each by name:

- L1 the prod sequence (run writes only another app's depot, the own depot's
  ``.bin`` is in the temp cache) keeps the own depot and adds no foreign one;
- L2 with an empty temp cache, the app's own recorded manifest keeps the
  depot (MUTATION TARGET);
- L3 the oracle's depot list keeps the depot (unit and worker wiring);
- L4 a record alone keeps only the containing app's depot: a package depot
  the game recorded with the package as containing app is still removable
  from the game; a foreign row without any evidence is still removed;
- L5 GC after a kept row plans the depot and keeps the recorded build.
"""

from __future__ import annotations

import dataclasses
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests import stub_prefill
from tests.test_gc import _cid, write_archive_bin, write_chunks
from tests.test_manifests import _bin_manifest_bytes
from tests.test_worker import enqueue, make_settings, wait_for_job
from vault_api import depot_manifests, gc_execute
from vault_api.config import MANIFEST_ORACLE_STEAMCMD_API
from vault_api.db import get_connection, init_db
from vault_api.main import create_app
from vault_api.mapping import upsert_mapping
from vault_api.prefill import apply_observed_mapping

ENSHROUDED, OWN_DEPOT, OWN_GID = 1203620, 1203621, "4708593645837114731"
OTHER_APP, OTHER_DEPOT = 1465470, 1465471
WITCHER, WITCHER_PACKAGE, PACKAGE_DEPOT = 292030, 378649, 310611


@pytest.fixture
def conn(tmp_path: Path):
    path = str(tmp_path / "vault.db")
    init_db(path)
    connection = get_connection(path)
    try:
        yield connection
    finally:
        connection.close()


def mapped(conn: sqlite3.Connection, appid: int) -> set[int]:
    return {
        int(r[0])
        for r in conn.execute("SELECT depotid FROM depot_app_map WHERE appid = ?", (appid,))
    }


def record(
    conn: sqlite3.Connection, appid: int, depotid: int, gid: str, *, containing: int | None
) -> None:
    depot_manifests.upsert_depot_manifest(
        conn,
        appid=appid,
        containing_appid=containing,
        depotid=depotid,
        manifestid=gid,
        chunk_count=1,
        total_bytes=160,
        recorded_at="2026-10-31T11:49:36Z",
        source="test",
    )


def oracle_lists(conn: sqlite3.Connection, appid: int, depotid: int, gid: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO oracle_app_state (appid, buildid, checked_at, source, "
        "depot_count, branch_count) VALUES (?, '1', '2026-10-31T11:00:00Z', 'test', 1, 1)",
        (appid,),
    )
    conn.execute(
        "INSERT OR REPLACE INTO oracle_branch_manifests (appid, depotid, branch, "
        "manifestid, recorded_at, source) VALUES (?, ?, 'public', ?, "
        "'2026-10-31T11:00:00Z', 'test')",
        (appid, depotid, gid),
    )
    conn.commit()


# --------------------------------------------------------------------------
# Unit: the replace step
# --------------------------------------------------------------------------


def test_L2_unit_own_recorded_manifest_keeps_the_depot(conn: sqlite3.Connection) -> None:
    upsert_mapping(conn, OWN_DEPOT, ENSHROUDED, None)
    record(conn, ENSHROUDED, OWN_DEPOT, OWN_GID, containing=ENSHROUDED)
    change = apply_observed_mapping(
        conn, ENSHROUDED, {OTHER_DEPOT},
        manifest_named=set(), owned={OWN_DEPOT}, oracle_listed=None,
    )
    assert mapped(conn, ENSHROUDED) == {OWN_DEPOT}
    assert change.removed == set()
    assert change.protected == {OWN_DEPOT}
    assert change.not_attributed == {OTHER_DEPOT}
    assert f"kept_unobserved=[{OWN_DEPOT}]" in change.summary()


def test_L2_unit_record_without_a_containing_app_counts(conn: sqlite3.Connection) -> None:
    upsert_mapping(conn, OWN_DEPOT, ENSHROUDED, None)
    record(conn, ENSHROUDED, OWN_DEPOT, OWN_GID, containing=None)
    change = apply_observed_mapping(
        conn, ENSHROUDED, {OTHER_DEPOT}, manifest_named=set(), owned=None, oracle_listed=None
    )
    assert change.removed == set()
    assert OWN_DEPOT in mapped(conn, ENSHROUDED)


def test_L3_unit_oracle_list_keeps_the_depot(conn: sqlite3.Connection) -> None:
    upsert_mapping(conn, OWN_DEPOT, ENSHROUDED, None)
    change = apply_observed_mapping(
        conn, ENSHROUDED, {OTHER_DEPOT},
        manifest_named=set(), owned={OWN_DEPOT}, oracle_listed={OWN_DEPOT},
    )
    assert change.removed == set()
    assert mapped(conn, ENSHROUDED) == {OWN_DEPOT}


def test_L3_unit_control_no_evidence_removes(conn: sqlite3.Connection) -> None:
    """Control for L2/L3: with every source silent the row still goes
    (ADR-0003 decision 3 keeps working)."""
    upsert_mapping(conn, OWN_DEPOT, ENSHROUDED, None)
    change = apply_observed_mapping(
        conn, ENSHROUDED, {OTHER_DEPOT}, manifest_named=set(), owned=set(), oracle_listed=set()
    )
    assert change.removed == {OWN_DEPOT}
    assert mapped(conn, ENSHROUDED) == set()


def test_L4_package_depot_recorded_under_the_game_is_not_kept_by_the_record(
    conn: sqlite3.Connection,
) -> None:
    """The game recorded 310611 with the package as containing app (prod
    Witcher 3 shape): that record is the package's evidence, not the game's.
    The package's own row is kept by the same record."""
    upsert_mapping(conn, PACKAGE_DEPOT, WITCHER, None)
    upsert_mapping(conn, PACKAGE_DEPOT, WITCHER_PACKAGE, None)
    record(conn, WITCHER, PACKAGE_DEPOT, "9087403118674428852", containing=WITCHER_PACKAGE)
    change = apply_observed_mapping(
        conn, WITCHER, {292031}, manifest_named=set(), owned={292031}, oracle_listed=None
    )
    assert change.removed == {PACKAGE_DEPOT}
    change = apply_observed_mapping(
        conn, WITCHER_PACKAGE, {378649}, manifest_named=set(), owned={378649},
        oracle_listed=None,
    )
    assert change.removed == set()
    assert PACKAGE_DEPOT in mapped(conn, WITCHER_PACKAGE)


def test_L4_foreign_row_without_evidence_is_still_removed(conn: sqlite3.Connection) -> None:
    """MAP-ATTRIB-1 shape: Enshrouded holding 1465471 with no record and no
    oracle listing; the owner's record (containing 1465470) is not
    Enshrouded's evidence. Nothing foreign is added either."""
    upsert_mapping(conn, OTHER_DEPOT, ENSHROUDED, None)
    upsert_mapping(conn, OWN_DEPOT, ENSHROUDED, None)
    record(conn, OTHER_APP, OTHER_DEPOT, "14654711", containing=OTHER_APP)
    record(conn, ENSHROUDED, OWN_DEPOT, OWN_GID, containing=ENSHROUDED)
    change = apply_observed_mapping(
        conn, ENSHROUDED, {OWN_DEPOT, 999},
        manifest_named=set(), owned={OWN_DEPOT}, oracle_listed={OWN_DEPOT},
    )
    assert change.removed == {OTHER_DEPOT}
    assert mapped(conn, ENSHROUDED) == {OWN_DEPOT}
    assert change.not_attributed == {999}


# --------------------------------------------------------------------------
# Worker: the prod sequence
# --------------------------------------------------------------------------


def _enshrouded_run(tmp_path: Path, *, own_bin: bool) -> tuple[str, Path, Path]:
    """Enshrouded's update run: every chunk of its own depot is a HIT, so the
    run writes no bytes into 1203621; another PC loads 1465471 at the same
    time. ``own_bin``: SteamPrefill writes the new manifest file (prod job
    205); without it the temp cache holds no Enshrouded file (cleared)."""
    cache_root = tmp_path / "cache"
    temp_cache = tmp_path / "steamprefill-cache"
    temp_cache.mkdir()
    bins = []
    if own_bin:
        bins.append(
            {
                "dir": str(temp_cache),
                "filename": f"{ENSHROUDED}_{ENSHROUDED}_{OWN_DEPOT}_{OWN_GID}.bin",
                "data": _bin_manifest_bytes(
                    depot_id=OWN_DEPOT, manifest_id=int(OWN_GID), files=[[(_cid(1), 160)]]
                ),
            }
        )
    executable = stub_prefill.make_stub(
        tmp_path / "bin",
        cache_root=str(cache_root),
        depots_by_app={ENSHROUDED: [OTHER_DEPOT]},
        manifest_bins=bins,
    )
    write_chunks(cache_root, OWN_DEPOT, {_cid(1): 160})
    return executable, cache_root, temp_cache


def _seed_before_the_run(db_path: str) -> None:
    """State after Enshrouded's previous run: own depot mapped, own manifest
    recorded."""
    conn = get_connection(db_path)
    try:
        upsert_mapping(conn, OWN_DEPOT, ENSHROUDED, "Enshrouded")
        record(conn, ENSHROUDED, OWN_DEPOT, OWN_GID, containing=ENSHROUDED)
    finally:
        conn.close()


def _run(settings, *, oracle: bool = False) -> str:
    with TestClient(create_app(settings)) as client:
        _seed_before_the_run(settings.db_path)
        if oracle:
            conn = get_connection(settings.db_path)
            try:
                conn.execute("DELETE FROM depot_manifests")
                conn.commit()
                oracle_lists(conn, ENSHROUDED, OWN_DEPOT, OWN_GID)
            finally:
                conn.close()
        (job_id,) = enqueue(client, ENSHROUDED)
        job = wait_for_job(client, job_id)
        assert job["status"] == "done", job["log_excerpt"]
        return job["log_excerpt"]


def _pairs(db_path: str) -> set[tuple[int, int]]:
    conn = get_connection(db_path)
    try:
        return {
            (int(r[0]), int(r[1]))
            for r in conn.execute("SELECT depotid, appid FROM depot_app_map")
        }
    finally:
        conn.close()


def test_L1_prod_sequence_job_205_keeps_the_own_depot(tmp_path: Path) -> None:
    executable, cache_root, temp_cache = _enshrouded_run(tmp_path, own_bin=True)
    settings = make_settings(
        tmp_path, cache_root, executable, steamprefill_cache_dir=str(temp_cache)
    )
    log = _run(settings)
    assert "removed=[]" in log, log
    assert f"kept_unobserved=[{OWN_DEPOT}]" in log, log
    assert f"observed_not_attributed=1 [{OTHER_DEPOT}]" in log, log
    assert _pairs(settings.db_path) == {(OWN_DEPOT, ENSHROUDED)}


def test_L2_worker_empty_temp_cache_keeps_the_recorded_own_depot(tmp_path: Path) -> None:
    """MUTATION TARGET (L2): without the record guard this run logs
    ``removed=[1203621]`` and Enshrouded ends with no mapping at all."""
    executable, cache_root, temp_cache = _enshrouded_run(tmp_path, own_bin=False)
    settings = make_settings(
        tmp_path, cache_root, executable, steamprefill_cache_dir=str(temp_cache)
    )
    log = _run(settings)
    assert "removed=[]" in log, log
    assert f"kept_unobserved=[{OWN_DEPOT}]" in log, log
    assert _pairs(settings.db_path) == {(OWN_DEPOT, ENSHROUDED)}


def test_L3_worker_oracle_list_keeps_the_own_depot(tmp_path: Path) -> None:
    """No temp-cache file, no record; only the oracle's depot list names the
    depot (oracle on): the worker passes it to the replace step."""
    executable, cache_root, temp_cache = _enshrouded_run(tmp_path, own_bin=False)
    settings = dataclasses.replace(
        make_settings(tmp_path, cache_root, executable, steamprefill_cache_dir=str(temp_cache)),
        manifest_oracle=MANIFEST_ORACLE_STEAMCMD_API,
    )
    log = _run(settings, oracle=True)
    assert "removed=[]" in log, log
    assert (OWN_DEPOT, ENSHROUDED) in _pairs(settings.db_path)


# --------------------------------------------------------------------------
# GC safety
# --------------------------------------------------------------------------


def test_L5_gc_after_a_kept_row_keeps_the_recorded_build(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    cache, archive = tmp_path / "cache", tmp_path / "manifests"
    current = {_cid(i): 160 for i in (0, 1, 2)}
    write_chunks(cache, OWN_DEPOT, {**current, _cid(9): 160})
    write_archive_bin(archive, depotid=OWN_DEPOT, manifestid=OWN_GID, chunks=current)
    upsert_mapping(conn, OWN_DEPOT, ENSHROUDED, None)
    record(conn, ENSHROUDED, OWN_DEPOT, OWN_GID, containing=ENSHROUDED)
    apply_observed_mapping(
        conn, ENSHROUDED, {OTHER_DEPOT}, manifest_named=set(), owned={OWN_DEPOT},
        oracle_listed=None,
    )
    report = gc_execute.run_gc(
        conn, ENSHROUDED, cache_root=str(cache), archive_dir=str(archive), execute=True,
        oracle_manifests={OWN_DEPOT: [OWN_GID]},
    )
    assert {d.depotid for d in report.depots} == {OWN_DEPOT}
    (result,) = report.depots
    assert result.status == "planned", report
    assert result.removed_count == 1
    chunk_dir = cache / "depot" / str(OWN_DEPOT) / "chunk"
    assert {p.name for p in chunk_dir.iterdir()} == set(current)


def test_L6_oracle_listed_is_required(conn: sqlite3.Connection) -> None:
    """No caller gets the weaker keep set by omission (same rule as
    ``manifest_named`` and ``owned``)."""
    with pytest.raises(TypeError):
        apply_observed_mapping(  # type: ignore[call-arg]
            conn, ENSHROUDED, {OTHER_DEPOT}, manifest_named=set(), owned=None
        )
