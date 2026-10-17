"""WP UNMAPPED-FLAG-1: ``cache_unmapped_depots`` on the game list and detail.

A depot with a recorded manifest for the app, no ``depot_app_map`` row under
any app and bytes on disk is not in the completeness figure; the API counts
it so the UIs do not show a clean figure for a partly mapped game.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from tests.test_cache_complete_1a import chunks, record, seed_two_games, settings_for
from tests.test_gc import write_chunks
from vault_api import cache_completeness, depot_manifests, jobs
from vault_api.db import get_connection, init_db
from vault_api.main import create_app
from vault_api.prefill import upsert_mapping

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


def record_only(conn: sqlite3.Connection, appid: int, depotid: int) -> None:
    """A recorded manifest with NO mapping row."""
    depot_manifests.upsert_depot_manifest(
        conn,
        appid=appid,
        containing_appid=None,
        depotid=depotid,
        manifestid="2002",
        chunk_count=1,
        total_bytes=1,
        recorded_at="2026-10-01T00:00:00Z",
        source="test",
    )


def test_unmapped_depot_with_bytes_is_counted_in_list_and_detail(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = settings_for(tmp_path)
    seed_two_games(conn, settings)
    for appid in (440, 730):
        jobs.ensure_app_row(conn, appid)
    cache = Path(settings.cache_root)
    # 440: one unmapped depot with bytes (counted), one without bytes (not),
    # one mapped to another app (not unmapped).
    record_only(conn, 440, 442)
    write_chunks(cache, 442, chunks(0, 1))
    record_only(conn, 440, 443)
    record_only(conn, 440, 731)
    upsert_mapping(conn, 731, 730, "Game 730")
    conn.commit()
    cache_completeness.evaluate_apps(conn, settings, [440, 730])

    client = TestClient(create_app(settings))
    rows = {g["appid"]: g for g in client.get("/v1/games", headers=AUTH).json()}
    assert rows[440]["cache_unmapped_depots"] == 1
    assert rows[730]["cache_unmapped_depots"] == 0
    assert rows[440]["cache_complete_pct"] == 100  # the figure itself is unchanged

    detail = client.get("/v1/games/440", headers=AUTH).json()
    assert detail["cache_unmapped_depots"] == 1
    assert client.get("/v1/games/730", headers=AUTH).json()["cache_unmapped_depots"] == 0


def test_unmapped_counts_helper_ignores_mapped_and_empty_depots(
    conn: sqlite3.Connection,
) -> None:
    record(conn, 10, 11, "1", "2026-10-01T00:00:00Z")  # mapped
    record_only(conn, 10, 12)
    record_only(conn, 10, 13)
    assert cache_completeness.unmapped_depot_counts(conn, {11: 5, 12: 5, 13: 0}) == {10: 1}
    assert cache_completeness.unmapped_depot_counts(conn, {}) == {}
