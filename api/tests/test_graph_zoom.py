"""WP GRAPH-ZOOM: the 1-minute traffic tier (schema v29) behind the 1 h and
6 h graph ranges.

Synthetic event lines only, clocks injected where the router allows it.
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from tests.test_event_sweep import append_log, event_line, make_settings, write_log
from vault_api import event_sweep, traffic
from vault_api.db import SCHEMA_VERSION, get_connection, init_db
from vault_api.main import create_app

UTC = timezone.utc
AUTH = {"X-Api-Key": TEST_API_KEY}


def at(day: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, second, tzinfo=UTC)


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def tier_rows(conn: sqlite3.Connection, table: str, tier: str) -> list[sqlite3.Row]:
    return conn.execute(
        f"SELECT * FROM {table} WHERE tier = ? ORDER BY bucket_at", (tier,)
    ).fetchall()


@pytest.fixture
def db(tmp_path: Path):
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        yield conn
    finally:
        conn.close()


def test_schema_is_v29() -> None:
    assert SCHEMA_VERSION == 29


# ---------------------------------------------------------------------------
# Write path and prune
# ---------------------------------------------------------------------------


def test_lines_land_in_one_minute_rows_next_to_the_other_tiers(
    tmp_path: Path, db: sqlite3.Connection
) -> None:
    settings = make_settings(tmp_path)
    write_log(
        tmp_path / "event.log",
        event_line(time=iso(at(13, 10, 7, 5)), cache_status="HIT", bytes_sent="1000"),
        event_line(time=iso(at(13, 10, 7, 59)), cache_status="MISS", bytes_sent="300"),
        event_line(time=iso(at(13, 10, 8, 1)), cache_status="MISS", bytes_sent="50"),
        event_line(time=iso(at(13, 10, 8, 2)), bytes_sent="999", http_status="404"),
    )
    event_sweep.sweep_once(db, settings, at(13, 10, 10))

    server = tier_rows(db, "server_traffic", "1m")
    assert [(r["bucket_at"], r["bytes_served"]) for r in server] == [
        ("2026-10-13T10:07:00Z", 1300),
        ("2026-10-13T10:08:00Z", 50),
    ]
    assert server[0]["hit_bytes"] == 1000 and server[0]["miss_bytes"] == 300
    # A 1-minute row is its own peak and its own activity.
    assert [(r["peak_bucket_bytes"], r["active_buckets"]) for r in server] == [
        (1300, 1),
        (50, 1),
    ]
    assert server[1]["errors"] == 1 and server[1]["requests"] == 2
    clients = tier_rows(db, "client_traffic", "1m")
    assert [r["bytes_served"] for r in clients] == [1300, 50]
    # The 5-minute tier is unchanged by the new one.
    five = tier_rows(db, "server_traffic", "5m")
    assert [(r["bucket_at"], r["bytes_served"]) for r in five] == [
        ("2026-10-13T10:05:00Z", 1350)
    ]
    assert event_sweep.read_state(db).traffic_1m_since == "2026-10-13T10:10:00Z"


def test_a_second_batch_adds_to_the_same_minute_exactly(
    tmp_path: Path, db: sqlite3.Connection
) -> None:
    settings = make_settings(tmp_path)
    log = tmp_path / "event.log"
    write_log(log, event_line(time=iso(at(13, 10, 7, 5)), bytes_sent="100"))
    event_sweep.sweep_once(db, settings, at(13, 10, 8))
    append_log(log, event_line(time=iso(at(13, 10, 7, 40)), bytes_sent="200"))
    event_sweep.sweep_once(db, settings, at(13, 10, 9))
    (row,) = tier_rows(db, "server_traffic", "1m")
    assert row["bytes_served"] == 300 and row["peak_bucket_bytes"] == 300
    assert row["active_buckets"] == 1


def test_prune_drops_one_minute_rows_after_24_hours_only(
    tmp_path: Path, db: sqlite3.Connection
) -> None:
    settings = make_settings(tmp_path)
    now = at(14, 12, 0, 30)
    write_log(
        tmp_path / "event.log",
        event_line(time=iso(now - timedelta(hours=25)), bytes_sent="1"),
        event_line(time=iso(now - timedelta(hours=23)), bytes_sent="2"),
        event_line(time=iso(now - timedelta(minutes=1)), bytes_sent="4"),
    )
    event_sweep.sweep_once(db, settings, now)
    # The 25 h old minute is not written at all; the 5-minute tier (48 h)
    # still holds it.
    assert [r["bytes_served"] for r in tier_rows(db, "server_traffic", "1m")] == [2, 4]
    assert [r["bytes_served"] for r in tier_rows(db, "server_traffic", "5m")] == [1, 2, 4]

    # Rows written earlier are pruned once they age out.
    removed = traffic.prune(db, now + timedelta(hours=2))
    db.commit()
    assert removed >= 1
    assert [r["bytes_served"] for r in tier_rows(db, "server_traffic", "1m")] == [4]
    assert traffic.RETENTION_1M == timedelta(hours=24)


def backdate_one_minute_tier(conn: sqlite3.Connection, since: str) -> None:
    """Pretend the 1-minute tier has been filling since ``since``."""
    conn.execute("UPDATE event_sweep_state SET traffic_1m_since = ? WHERE id = 1", (since,))
    conn.commit()


def seed_state_row(tmp_path: Path, conn: sqlite3.Connection, now: datetime) -> None:
    write_log(tmp_path / "event.log", event_line(time=iso(now), bytes_sent="1"))
    event_sweep.sweep_once(conn, make_settings(tmp_path), now)


def test_range_spec_maps_1h_and_6h_onto_the_one_minute_tier_once_it_covers_them(
    tmp_path: Path, db: sqlite3.Connection
) -> None:
    now = at(14, 12, 30, 20)
    seed_state_row(tmp_path, db, now)
    backdate_one_minute_tier(db, "2026-10-14T05:00:00Z")
    one = traffic.range_spec(db, "1h", now)
    six = traffic.range_spec(db, "6h", now)
    assert (one.tier, one.bucket_seconds, one.end) == ("1m", 60, "2026-10-14T12:30:00Z")
    assert one.start == "2026-10-14T11:31:00Z"
    assert (six.tier, six.start) == ("1m", "2026-10-14T06:31:00Z")
    assert traffic.peak_unit_seconds("1m") == 60
    assert traffic.peak_unit_seconds("5m") == 300


def test_range_spec_falls_back_to_the_five_minute_tier_until_the_one_minute_tier_covers_the_window(
    tmp_path: Path, db: sqlite3.Connection
) -> None:
    now = at(14, 12, 30, 20)
    # No sweep yet: no state row, the 1-minute tier is empty.
    for name in ("1h", "6h"):
        assert traffic.range_spec(db, name, now).tier == "5m"
    seed_state_row(tmp_path, db, now)
    # Filling since 2 h: the 1 h window is covered, the 6 h window is not.
    backdate_one_minute_tier(db, "2026-10-14T10:30:00Z")
    one = traffic.range_spec(db, "1h", now)
    six = traffic.range_spec(db, "6h", now)
    assert one.tier == "1m"
    assert (six.tier, six.bucket_seconds, six.end) == ("5m", 300, "2026-10-14T12:30:00Z")
    assert six.start == "2026-10-14T06:35:00Z"  # 72 five-minute buckets
    # Just filled this minute: both fall back.
    backdate_one_minute_tier(db, "2026-10-14T12:30:00Z")
    assert traffic.range_spec(db, "1h", now).tier == "5m"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def test_throughput_and_series_answer_1h_and_6h_from_the_one_minute_tier(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path, cooldown=0)
    init_db(settings.db_path)
    now = datetime.now(UTC)
    conn = get_connection(settings.db_path)
    try:
        write_log(
            tmp_path / "event.log",
            event_line(
                time=iso(now - timedelta(minutes=3)),
                addr="10.0.0.5",
                cache_status="HIT",
                bytes_sent="6000",
            ),
            event_line(
                time=iso(now - timedelta(minutes=2)),
                addr="10.0.0.9",
                cache_status="MISS",
                bytes_sent="1200",
            ),
            event_line(
                time=iso(now - timedelta(hours=4)),
                addr="10.0.0.9",
                cache_status="MISS",
                bytes_sent="30",
            ),
        )
        event_sweep.sweep_once(conn, settings, now)
        # The 1-minute tier has been filling for a day (see the fallback test
        # for the freshly upgraded case).
        backdate_one_minute_tier(conn, iso(now - timedelta(days=1)))
    finally:
        conn.close()
    client = TestClient(create_app(settings))

    body = client.get("/v1/throughput/series?range=1h", headers=AUTH).json()
    assert body["range"] == "1h" and body["tier"] == "1m"
    assert body["bucket_seconds"] == 60
    assert len(body["points"]) == 60
    assert sum(p["bytes_served"] for p in body["points"]) == 7200
    busiest = max(body["points"], key=lambda p: p["bytes_served"])
    assert busiest["bytes_served"] == 6000
    assert busiest["bytes_per_second"] == pytest.approx(100.0)
    assert busiest["peak_bytes_per_second"] == pytest.approx(100.0)

    six = client.get("/v1/throughput/series?range=6h", headers=AUTH).json()
    assert len(six["points"]) == 360
    assert sum(p["bytes_served"] for p in six["points"]) == 7230

    head = client.get("/v1/throughput?range=1h", headers=AUTH).json()
    assert head["tier"] == "1m"
    summary = head["summary"]
    assert summary["bytes_served"] == 7200
    assert summary["hit_bytes"] == 6000 and summary["miss_bytes"] == 1200
    assert summary["active_clients"] == 2
    assert summary["peak_bytes_per_second"] == pytest.approx(100.0)
    # Two active minutes -> 7200 B over 120 s.
    assert summary["average_bytes_per_second"] == pytest.approx(60.0)
    assert head["event_feed"]["traffic_1m_since"] is not None

    # A freshly upgraded server (tier started this minute) answers the same
    # ranges from real 5-minute data instead of an empty 1-minute tier.
    conn = get_connection(settings.db_path)
    try:
        backdate_one_minute_tier(conn, iso(now))
    finally:
        conn.close()
    fresh = client.get("/v1/throughput?range=6h", headers=AUTH).json()
    assert fresh["tier"] == "5m" and fresh["bucket_seconds"] == 300
    assert fresh["summary"]["bytes_served"] == 7230
    fresh_series = client.get("/v1/throughput/series?range=6h", headers=AUTH).json()
    assert len(fresh_series["points"]) == 72
    assert sum(p["bytes_served"] for p in fresh_series["points"]) == 7230

    # The 24 h range is unchanged (5-minute rows) and the overview keeps its
    # own range set.
    day = client.get("/v1/throughput?range=24h", headers=AUTH).json()
    assert day["tier"] == "5m"
    assert client.get("/v1/clients/overview?range=1h", headers=AUTH).status_code == 422
    assert client.get("/v1/throughput?range=2h", headers=AUTH).status_code == 422


# ---------------------------------------------------------------------------
# Migration on a COPY of a populated v28 database
# ---------------------------------------------------------------------------


def _make_v28_database(db_path: str) -> None:
    """A populated database in the v28 shape: ``event_sweep_state`` without
    ``traffic_1m_since`` (hand-written, never DROP COLUMN: CI links SQLite
    3.45), 5-minute rows present, stamped 28."""
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        conn.executescript(
            """
            DROP TABLE event_sweep_state;
            CREATE TABLE event_sweep_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                cursor_offset INTEGER NOT NULL DEFAULT 0,
                first_sweep_at TEXT, last_sweep_at TEXT, last_rotated_at TEXT,
                lines_read_total INTEGER NOT NULL DEFAULT 0,
                lines_skipped_total INTEGER NOT NULL DEFAULT 0,
                last_lines INTEGER, last_skipped INTEGER, last_enqueued INTEGER,
                last_dropped_by_cap INTEGER,
                truncate_denied_count INTEGER NOT NULL DEFAULT 0,
                last_truncate_denied_at TEXT,
                oversized_skips_total INTEGER NOT NULL DEFAULT 0,
                last_oversized_at TEXT,
                log_dev INTEGER, log_ino INTEGER,
                rotations_detected_total INTEGER NOT NULL DEFAULT 0,
                last_rotation_detected_at TEXT,
                traffic_since TEXT
            );
            INSERT INTO event_sweep_state (id, cursor_offset, traffic_since)
            VALUES (1, 4321, '2026-10-01T00:00:00Z');
            INSERT INTO server_traffic (tier, bucket_at, requests, bytes_served)
            VALUES ('5m', '2026-10-13T10:05:00Z', 3, 777);
            INSERT INTO client_traffic (tier, bucket_at, client_addr, requests,
                                        bytes_served, last_seen)
            VALUES ('5m', '2026-10-13T10:05:00Z', '10.0.0.5', 3, 777,
                    '2026-10-13T10:06:00Z');
            UPDATE schema_version SET version = 28;
            """
        )
        conn.commit()
    finally:
        conn.close()


def test_a_copy_of_a_populated_v28_database_migrates_and_keeps_its_data(
    tmp_path: Path,
) -> None:
    original = str(tmp_path / "vault.db")
    _make_v28_database(original)
    copy = str(tmp_path / "copy.db")
    shutil.copy(original, copy)

    init_db(copy)
    init_db(copy)  # idempotent: no 'duplicate column name'

    conn = get_connection(copy)
    try:
        (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
        columns = {
            row["name"]: row["type"]
            for row in conn.execute("PRAGMA table_info(event_sweep_state)")
        }
        state = event_sweep.read_state(conn)
        five = tier_rows(conn, "server_traffic", "5m")
        clients = tier_rows(conn, "client_traffic", "5m")
        one_minute = tier_rows(conn, "server_traffic", "1m")
    finally:
        conn.close()
    assert version == SCHEMA_VERSION == 29
    assert columns["traffic_1m_since"] == "TEXT"
    assert (state.cursor_offset, state.traffic_since) == (4321, "2026-10-01T00:00:00Z")
    assert state.traffic_1m_since is None
    assert [r["bytes_served"] for r in five] == [777]
    assert [r["bytes_served"] for r in clients] == [777]
    assert one_minute == []  # the tier starts empty, nothing is invented

    # The untouched original is still v28 (the migration ran on the copy).
    raw = sqlite3.connect(original)
    try:
        assert raw.execute("SELECT version FROM schema_version").fetchone()[0] == 28
        cols = {r[1] for r in raw.execute("PRAGMA table_info(event_sweep_state)")}
    finally:
        raw.close()
    assert "traffic_1m_since" not in cols


def test_the_migrated_copy_fills_the_new_tier_on_the_next_sweep(tmp_path: Path) -> None:
    original = str(tmp_path / "vault.db")
    _make_v28_database(original)
    copy = str(tmp_path / "copy.db")
    shutil.copy(original, copy)
    init_db(copy)

    settings = make_settings(tmp_path)
    conn = get_connection(copy)
    try:
        write_log(
            tmp_path / "event.log",
            event_line(time=iso(at(13, 10, 7, 5)), bytes_sent="500"),
        )
        event_sweep.sweep_once(conn, settings, at(13, 10, 10))
        (row,) = tier_rows(conn, "server_traffic", "1m")
        state = event_sweep.read_state(conn)
    finally:
        conn.close()
    assert row["bytes_served"] == 500
    assert state.traffic_1m_since == "2026-10-13T10:10:00Z"
    assert state.traffic_since == "2026-10-01T00:00:00Z"  # kept, not reset


def test_an_older_build_refuses_a_newer_file(tmp_path: Path) -> None:
    """The downgrade guard that makes the SQLite-dump rollback note necessary."""
    path = str(tmp_path / "v.db")
    init_db(path)
    conn = get_connection(path)
    try:
        conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION + 1,))
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(RuntimeError, match="newer version"):
        init_db(path)
