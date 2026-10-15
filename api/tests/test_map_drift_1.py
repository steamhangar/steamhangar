"""WP MAP-DRIFT-1: startup cleanup of orphan ``depot_manifests`` rows.

Guarantees pinned here, by name:

1. A row whose depot no app maps AND that has no bytes on disk is removed.
2. A partial update keeps its rows: the untouched depot dropped out of
   ``depot_app_map`` (``apply_observed_mapping``) but its bytes are still on
   disk, so its recorded manifest must survive.
3. A mapped depot is never touched, bytes or not.
4. Idempotent: a second pass removes nothing.
5. One summary line with found/removed/skipped counters at startup.
6. An unjudgeable disk (no depot cache) keeps every row.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_oracle import enabled_settings
from vault_api.db import get_connection, init_db
from vault_api.depot_manifests import get_depot_manifest, upsert_depot_manifest
from vault_api.main import create_app
from vault_api.manifest_ingest import OrphanCleanupResult, remove_orphan_depot_manifests
from vault_api.mapping import upsert_mapping

APPID = 500
MAPPED, UNTOUCHED, ORPHAN, EMPTY_DIRS = 5001, 5002, 5003, 5004


def _settings(tmp_path: Path):
    (tmp_path / "cache" / "depot").mkdir(parents=True)
    return enabled_settings(tmp_path)


def _conn(settings) -> sqlite3.Connection:
    init_db(settings.db_path)
    return get_connection(settings.db_path)


def _row(conn: sqlite3.Connection, depot: int, *, appid: int = APPID) -> None:
    upsert_depot_manifest(
        conn, appid=appid, containing_appid=appid, depotid=depot, manifestid="111",
        chunk_count=1, total_bytes=10, recorded_at="2026-10-14T10:00:00Z",
        source="steamprefill_bin",
    )


def _bytes(settings, depot: int) -> None:
    chunk = Path(settings.cache_root) / "depot" / str(depot) / "chunk" / "abc"
    chunk.parent.mkdir(parents=True)
    chunk.write_bytes(b"x")


def _present(conn: sqlite3.Connection, depot: int) -> bool:
    return get_depot_manifest(conn, appid=APPID, depotid=depot) is not None


def _seed(conn, settings) -> None:
    for depot in (MAPPED, UNTOUCHED, ORPHAN, EMPTY_DIRS):
        _row(conn, depot)
    upsert_mapping(conn, MAPPED, APPID, "App")  # the partial update kept only this depot
    _bytes(settings, UNTOUCHED)  # dropped from the map, bytes still cached
    (Path(settings.cache_root) / "depot" / str(EMPTY_DIRS) / "chunk").mkdir(parents=True)


def test_orphans_are_removed_and_the_partial_update_keeps_its_rows(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    conn = _conn(settings)
    try:
        _seed(conn, settings)
        result = remove_orphan_depot_manifests(conn, settings)

        assert result == OrphanCleanupResult(found=3, removed=2, skipped=1)
        assert _present(conn, MAPPED)
        assert _present(conn, UNTOUCHED), "partial-update row with bytes on disk must stay"
        assert not _present(conn, ORPHAN)
        assert not _present(conn, EMPTY_DIRS), "empty directories are not bytes"
    finally:
        conn.close()


def test_a_second_pass_removes_nothing(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    conn = _conn(settings)
    try:
        _seed(conn, settings)
        remove_orphan_depot_manifests(conn, settings)
        again = remove_orphan_depot_manifests(conn, settings)
        assert again == OrphanCleanupResult(found=1, removed=0, skipped=1)
    finally:
        conn.close()


def test_a_mapped_depot_without_bytes_is_kept(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    conn = _conn(settings)
    try:
        _row(conn, MAPPED)
        upsert_mapping(conn, MAPPED, APPID, "App")
        assert remove_orphan_depot_manifests(conn, settings) == OrphanCleanupResult()
        assert _present(conn, MAPPED)
    finally:
        conn.close()


def test_an_unavailable_depot_cache_keeps_every_row(tmp_path: Path) -> None:
    settings = enabled_settings(tmp_path)  # no cache/depot directory created
    conn = _conn(settings)
    try:
        _row(conn, ORPHAN)
        result = remove_orphan_depot_manifests(conn, settings)
        assert result.cache_unavailable is True and result.removed == 0
        assert _present(conn, ORPHAN)
    finally:
        conn.close()


def test_startup_cleans_and_logs_counters_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = _settings(tmp_path)
    conn = _conn(settings)
    try:
        _seed(conn, settings)
    finally:
        conn.close()

    with caplog.at_level(logging.INFO, logger="vault_api.main"):
        with TestClient(create_app(settings)):
            pass

    lines = [r.getMessage() for r in caplog.records if "Orphan depot cleanup" in r.getMessage()]
    assert lines == ["Orphan depot cleanup: found=3 removed=2 skipped=1 (WP MAP-DRIFT-1)."]
    conn = get_connection(settings.db_path)
    try:
        assert _present(conn, UNTOUCHED) and _present(conn, MAPPED)
        assert not _present(conn, ORPHAN)
    finally:
        conn.close()
