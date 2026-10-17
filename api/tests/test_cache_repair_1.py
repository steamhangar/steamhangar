"""WP CACHE-REPAIR-1: repair wrong-size chunk files.

Guarantees pinned here, by name:

1. A wrong-size chunk file of the target manifest is deleted, a right-size one
   is kept, and a forced run is queued (``needs_force = 1``, one job).
2. Dry run (the default) deletes and queues nothing.
3. Safety: a symlinked chunk is never followed or deleted, a depot reached
   through a symlink is refused, nothing outside the cache root is touched, a
   size change between scan and delete (or a file repaired meanwhile) keeps
   the file, the mass-deletion cap refuses the whole plan.
4. An active job (own or a co-owner's) is a 409; nothing damaged is a no-op.

All trees are tmp dirs; nothing is downloaded.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from tests.test_cache_complete_1a import APPID, DEPOT, OLD_GID, chunks, record, settings_for
from tests.test_gc import _cid, write_archive_bin, write_chunks
from vault_api import cache_completeness, cache_repair, deletion, jobs
from vault_api.config import Settings
from vault_api.db import get_connection, init_db
from vault_api.main import create_app

AUTH = {"X-Api-Key": TEST_API_KEY}


@pytest.fixture
def conn(tmp_path: Path):
    path = str(tmp_path / "vault.db")
    init_db(path)
    connection = get_connection(path)
    try:
        yield connection
    finally:
        connection.close()


def chunk_path(settings: Settings, index: int, depot: int = DEPOT) -> Path:
    return Path(settings.cache_root) / "depot" / str(depot) / "chunk" / _cid(index)


def seed(conn: sqlite3.Connection, settings: Settings, *, wrong: tuple[int, ...] = (1,)) -> None:
    """Manifest of 4 chunks (100 bytes each); the chunks in ``wrong`` are on disk
    with 99 bytes, the rest with the right size."""
    cache = Path(settings.cache_root)
    record(conn, APPID, DEPOT, OLD_GID, "2026-10-01T00:00:00Z")
    jobs.ensure_app_row(conn, APPID)
    conn.execute("UPDATE apps SET needs_force = 0")
    conn.commit()
    write_archive_bin(
        Path(settings.manifest_archive_dir), depotid=DEPOT, manifestid=OLD_GID, chunks=chunks(0, 4)
    )
    for i in range(4):
        write_chunks(cache, DEPOT, {_cid(i): 99 if i in wrong else 100})
    cache_completeness.evaluate_apps(conn, settings, [APPID])


def client_for(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))  # no lifespan: no worker claims jobs


def repair(client: TestClient, execute: bool | None = None, appid: int = APPID):
    body = None if execute is None else {"execute": execute}
    return client.post(f"/v1/cache/{appid}/repair", json=body, headers=AUTH)


def job_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])


def needs_force(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT needs_force FROM apps WHERE appid = ?", (APPID,)).fetchone()[0])


# --------------------------------------------------------------------------
# 1. execute
# --------------------------------------------------------------------------


def test_wrong_size_chunk_is_deleted_right_size_kept_and_forced_run_queued(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = settings_for(tmp_path, oracle_on=False)
    seed(conn, settings, wrong=(1,))
    assert cache_completeness.load_index(conn, settings).figure([(APPID, DEPOT)]).missing_chunks == 0

    response = repair(client_for(settings), execute=True)

    assert response.status_code == 202, response.text
    body = response.json()
    assert (body["damaged_chunks"], body["removed_chunks"], body["removed_bytes"]) == (1, 1, 99)
    assert body["job_status"] == "queued" and body["job_deduplicated"] is False
    assert not chunk_path(settings, 1).exists()
    for i in (0, 2, 3):
        assert chunk_path(settings, i).stat().st_size == 100
    assert needs_force(conn) == 1
    assert job_count(conn) == 1
    # Re-measured: the deleted chunk now counts as missing, so "Complete all"
    # and automatic completion see it as completable.
    figure = cache_completeness.load_index(conn, settings).figure([(APPID, DEPOT)])
    assert figure.missing_chunks == 1


def test_dry_run_is_the_default_and_changes_nothing(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = settings_for(tmp_path, oracle_on=False)
    seed(conn, settings, wrong=(1, 2))
    client = client_for(settings)

    for response in (repair(client), repair(client, execute=False)):
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["mode"] == "dry-run" and body["execute"] is False
        assert body["damaged_chunks"] == 2 and body["damaged_bytes"] == 198
        assert body["depots"] == [
            {"depotid": DEPOT, "manifestid": OLD_GID, "damaged_chunks": 2, "damaged_bytes": 198}
        ]
        assert body["job_id"] is None
    assert chunk_path(settings, 1).stat().st_size == 99
    assert job_count(conn) == 0 and needs_force(conn) == 0


def test_nothing_damaged_is_a_no_op(tmp_path: Path, conn: sqlite3.Connection) -> None:
    settings = settings_for(tmp_path, oracle_on=False)
    seed(conn, settings, wrong=())

    response = repair(client_for(settings), execute=True)

    assert response.status_code == 200
    body = response.json()
    assert body["damaged_chunks"] == 0 and body["removed_chunks"] == 0 and body["job_id"] is None
    assert job_count(conn) == 0 and needs_force(conn) == 0


def test_unknown_app_unmapped_app_and_auth(tmp_path: Path, conn: sqlite3.Connection) -> None:
    settings = settings_for(tmp_path, oracle_on=False)
    client = client_for(settings)
    assert repair(client, appid=999).status_code == 404
    jobs.ensure_app_row(conn, 998)
    assert repair(client, appid=998).status_code == 404
    assert client.post(f"/v1/cache/{APPID}/repair").status_code == 401
    bad = client.post(f"/v1/cache/{APPID}/repair", json={"execute": "yes"}, headers=AUTH)
    assert bad.status_code == 422


# --------------------------------------------------------------------------
# 2. conflicts
# --------------------------------------------------------------------------


def test_active_job_for_the_app_is_a_409_and_nothing_is_deleted(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = settings_for(tmp_path, oracle_on=False)
    seed(conn, settings)
    jobs.enqueue_prefill(conn, APPID)

    response = repair(client_for(settings), execute=True)

    assert response.status_code == 409, response.text
    assert chunk_path(settings, 1).stat().st_size == 99
    assert job_count(conn) == 1


def test_active_job_of_a_co_owner_of_the_depot_is_a_409(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = settings_for(tmp_path, oracle_on=False)
    seed(conn, settings)
    record(conn, 730, DEPOT, OLD_GID, "2026-10-01T00:00:00Z")
    jobs.ensure_app_row(conn, 730)
    jobs.enqueue_prefill(conn, 730)

    response = repair(client_for(settings), execute=True)

    assert response.status_code == 409, response.text
    assert chunk_path(settings, 1).stat().st_size == 99


# --------------------------------------------------------------------------
# 3. safety
# --------------------------------------------------------------------------


def test_symlinked_chunk_is_not_followed_or_deleted(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = settings_for(tmp_path, oracle_on=False)
    seed(conn, settings, wrong=())
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"x" * 7)
    link = chunk_path(settings, 2)
    link.unlink()
    os.symlink(outside, link)

    response = repair(client_for(settings), execute=True)

    assert response.status_code == 200, response.text
    assert response.json()["damaged_chunks"] == 0  # a link is never a candidate
    assert link.is_symlink() and outside.read_bytes() == b"x" * 7


def test_symlink_swapped_in_between_scan_and_delete_is_refused(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = settings_for(tmp_path, oracle_on=False)
    seed(conn, settings, wrong=(1,))
    root = deletion.resolve_depot_root(settings.cache_root)
    plan = cache_repair.plan_repair(conn, settings, APPID, depot_root=root)
    assert plan.damaged_count == 1
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"x" * 99)  # same size as the planned file
    target = chunk_path(settings, 1)
    target.unlink()
    os.symlink(outside, target)

    result = cache_repair.execute_repair(plan, root)

    assert result.removed == 0 and result.skipped == {cache_repair.SKIP_NOT_REGULAR: 1}
    assert outside.exists() and target.is_symlink()


def test_chunk_directory_symlinked_out_of_the_cache_is_refused(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = settings_for(tmp_path, oracle_on=False)
    seed(conn, settings, wrong=(1,))
    root = deletion.resolve_depot_root(settings.cache_root)
    plan = cache_repair.plan_repair(conn, settings, APPID, depot_root=root)
    # An outside directory holding a same-named, same-size file; the real
    # chunk/ directory is replaced by a link to it after the scan.
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / _cid(1)).write_bytes(b"x" * 99)
    chunk_dir = chunk_path(settings, 1).parent
    for f in chunk_dir.iterdir():
        f.unlink()
    chunk_dir.rmdir()
    os.symlink(outside, chunk_dir)

    result = cache_repair.execute_repair(plan, root)

    assert result.removed == 0 and result.skipped == {cache_repair.SKIP_UNSAFE_PATH: 1}
    assert (outside / _cid(1)).exists()


def test_planned_path_outside_the_root_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "cache" / "depot"
    root.mkdir(parents=True)
    victim = tmp_path / "victim"
    victim.write_bytes(b"x" * 5)
    plan = cache_repair.RepairPlan(
        appid=1,
        depots=[
            cache_repair.DepotRepairPlan(
                depotid=0,  # not a usable depot id
                manifestid="1",
                chunks=[cache_repair.DamagedChunk("../../victim", 9, 5)],
            ),
            cache_repair.DepotRepairPlan(
                depotid=5,
                manifestid="1",
                chunks=[cache_repair.DamagedChunk("../../../victim", 9, 5)],  # not 40 hex
            ),
        ],
    )

    result = cache_repair.execute_repair(plan, str(root))

    assert result.removed == 0 and result.skipped == {cache_repair.SKIP_UNSAFE_PATH: 2}
    assert victim.exists()


def test_size_change_between_scan_and_delete_keeps_the_file(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = settings_for(tmp_path, oracle_on=False)
    seed(conn, settings, wrong=(1, 2, 3))
    root = deletion.resolve_depot_root(settings.cache_root)
    plan = cache_repair.plan_repair(conn, settings, APPID, depot_root=root)
    assert plan.damaged_count == 3
    chunk_path(settings, 1).write_bytes(b"\xab" * 100)  # repaired meanwhile
    chunk_path(settings, 2).write_bytes(b"\xab" * 60)  # still being written
    # chunk 3 is untouched

    result = cache_repair.execute_repair(plan, root)

    assert result.removed == 1 and result.removed_bytes == 99
    assert result.skipped == {
        cache_repair.SKIP_SIZE_NOW_MATCHES: 1,
        cache_repair.SKIP_SIZE_CHANGED: 1,
    }
    assert chunk_path(settings, 1).stat().st_size == 100
    assert chunk_path(settings, 2).stat().st_size == 60
    assert not chunk_path(settings, 3).exists()


def test_vanished_file_is_skipped_not_an_error(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = settings_for(tmp_path, oracle_on=False)
    seed(conn, settings, wrong=(1,))
    root = deletion.resolve_depot_root(settings.cache_root)
    plan = cache_repair.plan_repair(conn, settings, APPID, depot_root=root)
    chunk_path(settings, 1).unlink()

    result = cache_repair.execute_repair(plan, root)

    assert result.removed == 0 and result.skipped == {cache_repair.SKIP_GONE: 1}


def test_only_the_target_manifests_chunks_are_candidates(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = settings_for(tmp_path, oracle_on=False)
    seed(conn, settings, wrong=())
    # Not listed in the manifest (an older build's leftover): never touched,
    # whatever its size.
    write_chunks(Path(settings.cache_root), DEPOT, {_cid(500): 5000})

    response = repair(client_for(settings), execute=True)

    assert response.json()["damaged_chunks"] == 0
    assert chunk_path(settings, 500).stat().st_size == 5000


def test_mass_deletion_cap_refuses_the_whole_plan(
    tmp_path: Path, conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = settings_for(tmp_path, oracle_on=False)
    seed(conn, settings, wrong=(0, 1, 2))
    monkeypatch.setattr(cache_repair, "MAX_REPAIR_FILES", 2)
    client = client_for(settings)

    preview = repair(client)
    assert preview.status_code == 200
    assert preview.json()["exceeds_cap"] is True and preview.json()["max_files"] == 2

    refused = repair(client, execute=True)
    assert refused.status_code == 422, refused.text
    assert all(chunk_path(settings, i).stat().st_size == 99 for i in (0, 1, 2))
    assert job_count(conn) == 0

    # Defence in depth: the executor refuses an over-cap plan on its own too.
    root = deletion.resolve_depot_root(settings.cache_root)
    plan = cache_repair.plan_repair(conn, settings, APPID, depot_root=root)
    assert cache_repair.execute_repair(plan, root).removed == 0


def test_not_owned_app_is_a_409_and_nothing_is_deleted(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = settings_for(tmp_path, oracle_on=False)
    seed(conn, settings)
    now = jobs.utcnow_iso()
    jobs.set_app_status(conn, APPID, "done")  # kept cached status, as in test_not_owned_2
    conn.execute(
        "INSERT INTO jobs (appid, type, status, created_at, finished_at) "
        "VALUES (?, 'prefill', ?, ?, ?)",
        (APPID, jobs.STATUS_NOT_OWNED, now, now),
    )
    conn.commit()
    assert APPID in jobs.not_owned_appids(conn)
    before = job_count(conn)

    response = repair(client_for(settings), execute=True)

    assert response.status_code == 409, response.text
    assert "not_owned" in response.json()["detail"]
    assert chunk_path(settings, 1).stat().st_size == 99
    assert job_count(conn) == before
    assert needs_force(conn) == 0


def test_unknown_body_field_is_a_422(tmp_path: Path, conn: sqlite3.Connection) -> None:
    settings = settings_for(tmp_path, oracle_on=False)
    seed(conn, settings)
    response = client_for(settings).post(
        f"/v1/cache/{APPID}/repair", json={"execute": True, "force": True}, headers=AUTH
    )
    assert response.status_code == 422
    assert chunk_path(settings, 1).stat().st_size == 99
