"""WP D15b1: traffic tiers (5m/1h/1d) and the rotation-robust sweeper.

Synthetic event lines only (LEARNINGS: fixtures synthetic, modelled on the
real nine-field format), every clock injected.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.test_event_sweep import append_log, event_line, make_settings, write_log
from vault_api import event_sweep, traffic
from vault_api.db import SCHEMA_VERSION, get_connection, init_db

UTC = timezone.utc


def at(day: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, second, tzinfo=UTC)


def iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S+00:00")


@pytest.fixture
def db(tmp_path: Path):
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        yield conn
    finally:
        conn.close()


def rows(conn: sqlite3.Connection, table: str, tier: str) -> list[dict[str, object]]:
    return [
        dict(row)
        for row in conn.execute(
            f"SELECT * FROM {table} WHERE tier = ? ORDER BY bucket_at", (tier,)
        )
    ]


# ---------------------------------------------------------------------------
# Schema v23
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("old_version", [21, 22])
def test_a_v21_or_v22_database_gains_the_v23_columns_exactly_once(
    tmp_path: Path, old_version: int
) -> None:
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        conn.execute("DROP TABLE event_sweep_state")
        conn.execute(
            """
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
                last_oversized_at TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO event_sweep_state (id, cursor_offset) VALUES (1, 1234)"
        )
        for table in ("client_traffic", "client_depot_traffic", "server_traffic"):
            conn.execute(f"DROP TABLE {table}")
        conn.execute("UPDATE schema_version SET version = ?", (old_version,))
        conn.commit()
    finally:
        conn.close()

    init_db(db_path)
    init_db(db_path)  # idempotent: no 'duplicate column name'

    conn = get_connection(db_path)
    try:
        (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
        columns = {
            row["name"]: row["type"]
            for row in conn.execute("PRAGMA table_info(event_sweep_state)")
        }
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        state = event_sweep.read_state(conn)
    finally:
        conn.close()

    assert version == SCHEMA_VERSION == 24
    assert columns["log_dev"] == "INTEGER"
    assert columns["log_ino"] == "INTEGER"
    assert columns["rotations_detected_total"] == "INTEGER"
    assert columns["last_rotation_detected_at"] == "TEXT"
    assert columns["traffic_since"] == "TEXT"
    assert {"client_traffic", "client_depot_traffic", "server_traffic"} <= tables
    assert state.cursor_offset == 1234  # the existing row survives
    assert state.log_identity is None
    assert state.rotations_detected_total == 0
    assert state.traffic_since is None


# ---------------------------------------------------------------------------
# Tiers: bucketing, rollup at write time, peak and active buckets
# ---------------------------------------------------------------------------


def test_lines_land_in_their_event_time_bucket_in_all_three_tiers(
    tmp_path: Path, db: sqlite3.Connection
) -> None:
    settings = make_settings(tmp_path)
    write_log(
        tmp_path / "event.log",
        event_line(time=iso(at(13, 10, 7, 30)), cache_status="HIT", bytes_sent="1000"),
        event_line(time=iso(at(13, 10, 9, 59)), cache_status="MISS", bytes_sent="300"),
        event_line(time=iso(at(13, 10, 12)), cache_status="MISS", bytes_sent="50"),
        # A 404 counts as a request and an error, never as bytes.
        event_line(time=iso(at(13, 10, 12)), bytes_sent="999", http_status="404"),
        # No depot id: counted, but not in the per-depot table.
        event_line(time=iso(at(13, 10, 12)), depot="-", cache_status="HIT", bytes_sent="7"),
    )
    event_sweep.sweep_once(db, settings, at(13, 10, 15))

    five = rows(db, "client_traffic", "5m")
    assert [(r["bucket_at"], r["bytes_served"]) for r in five] == [
        ("2026-10-13T10:05:00Z", 1300),
        ("2026-10-13T10:10:00Z", 57),
    ]
    assert five[0]["hit_bytes"] == 1000 and five[0]["miss_bytes"] == 300
    assert five[1]["errors"] == 1 and five[1]["requests"] == 3

    hour = rows(db, "client_traffic", "1h")
    day = rows(db, "client_traffic", "1d")
    for coarse, bucket in ((hour, "2026-10-13T10:00:00Z"), (day, "2026-10-13T00:00:00Z")):
        assert len(coarse) == 1
        assert coarse[0]["bucket_at"] == bucket
        assert coarse[0]["bytes_served"] == 1357
        assert coarse[0]["hit_bytes"] == 1007
        assert coarse[0]["miss_bytes"] == 350
        assert coarse[0]["requests"] == 5
        assert coarse[0]["errors"] == 1
        assert coarse[0]["last_seen"] == "2026-10-13T10:12:00Z"

    depot_hour = rows(db, "client_depot_traffic", "1h")
    assert [(r["depotid"], r["bytes_served"], r["hit_bytes"]) for r in depot_hour] == [
        (70403, 1350, 1000)
    ]
    assert event_sweep.read_state(db).traffic_since == "2026-10-13T10:15:00Z"


def test_peak_is_the_largest_server_wide_5_minute_total_and_active_counts_buckets(
    tmp_path: Path, db: sqlite3.Connection
) -> None:
    settings = make_settings(tmp_path)
    log = tmp_path / "event.log"
    write_log(
        log,
        event_line(time=iso(at(13, 10, 1)), addr="10.0.0.1", bytes_sent="100"),
        event_line(time=iso(at(13, 10, 2)), addr="10.0.0.2", bytes_sent="150"),
        event_line(time=iso(at(13, 10, 21)), addr="10.0.0.1", bytes_sent="200"),
    )
    event_sweep.sweep_once(db, settings, at(13, 10, 25))
    # A second sweep adds to the SAME 10:00 bucket: its total becomes 400,
    # which must raise the peak and must NOT count the bucket as active twice.
    append_log(log, event_line(time=iso(at(13, 10, 3)), addr="10.0.0.2", bytes_sent="150"))
    event_sweep.sweep_once(db, settings, at(13, 10, 30))

    server_five = {r["bucket_at"]: r for r in rows(db, "server_traffic", "5m")}
    assert server_five["2026-10-13T10:00:00Z"]["bytes_served"] == 400
    assert server_five["2026-10-13T10:00:00Z"]["peak_bucket_bytes"] == 400
    assert server_five["2026-10-13T10:20:00Z"]["active_buckets"] == 1

    for tier in ("1h", "1d"):
        (row,) = rows(db, "server_traffic", tier)
        assert row["bytes_served"] == 600
        assert row["peak_bucket_bytes"] == 400
        assert row["active_buckets"] == 2


def test_an_error_only_bucket_is_not_an_active_bucket(
    tmp_path: Path, db: sqlite3.Connection
) -> None:
    settings = make_settings(tmp_path)
    write_log(tmp_path / "event.log", event_line(time=iso(at(13, 10, 1)), http_status="502"))
    event_sweep.sweep_once(db, settings, at(13, 10, 5))
    (row,) = rows(db, "server_traffic", "1h")
    assert row["errors"] == 1
    assert row["active_buckets"] == 0
    assert row["peak_bucket_bytes"] == 0


def test_a_late_line_adds_to_its_old_bucket_in_every_tier(
    tmp_path: Path, db: sqlite3.Connection
) -> None:
    settings = make_settings(tmp_path)
    log = tmp_path / "event.log"
    write_log(log, event_line(time=iso(at(12, 8, 0)), bytes_sent="10"))
    event_sweep.sweep_once(db, settings, at(12, 8, 5))
    # Read a day later (vault-api was down): it still belongs to 12th 08:00.
    append_log(log, event_line(time=iso(at(12, 8, 3)), bytes_sent="5"))
    event_sweep.sweep_once(db, settings, at(13, 9, 0))

    assert [r["bytes_served"] for r in rows(db, "client_traffic", "5m")] == [15]
    assert [(r["bucket_at"], r["bytes_served"]) for r in rows(db, "client_traffic", "1h")] == [
        ("2026-10-12T08:00:00Z", 15)
    ]
    assert [r["bytes_served"] for r in rows(db, "server_traffic", "1d")] == [15]


def test_a_future_event_time_is_clamped_to_the_sweep_time(
    tmp_path: Path, db: sqlite3.Connection
) -> None:
    settings = make_settings(tmp_path)
    write_log(tmp_path / "event.log", event_line(time=iso(at(20, 0, 0)), bytes_sent="9"))
    event_sweep.sweep_once(db, settings, at(13, 10, 7))
    (row,) = rows(db, "client_traffic", "5m")
    assert row["bucket_at"] == "2026-10-13T10:05:00Z"
    assert row["last_seen"] == "2026-10-13T10:07:00Z"


def test_a_failure_inside_the_commit_leaves_no_tier_rows_and_the_retry_counts_once(
    tmp_path: Path, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings(tmp_path)
    write_log(tmp_path / "event.log", event_line(time=iso(at(13, 10, 1)), bytes_sent="100"))

    def boom(conn: sqlite3.Connection, now: datetime) -> int:
        raise RuntimeError("disk full")

    monkeypatch.setattr(traffic, "prune", boom)
    with pytest.raises(RuntimeError):
        event_sweep.sweep_once(db, settings, at(13, 10, 5))
    for table in ("client_traffic", "client_depot_traffic", "server_traffic"):
        assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert event_sweep.read_state(db).cursor_offset == 0

    monkeypatch.undo()
    event_sweep.sweep_once(db, settings, at(13, 10, 10))
    event_sweep.sweep_once(db, settings, at(13, 10, 15))  # nothing new
    assert [r["bytes_served"] for r in rows(db, "server_traffic", "1d")] == [100]


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


def test_retention_keeps_5m_for_48h_1h_for_30_days_and_1d_forever(
    tmp_path: Path, db: sqlite3.Connection
) -> None:
    settings = make_settings(tmp_path)
    now = at(13, 12, 0)
    old_5m = now - timedelta(hours=48, minutes=5)  # just outside 48 h
    kept_5m = now - timedelta(hours=47, minutes=55)
    old_1h = now - timedelta(days=30, hours=1)
    write_log(
        tmp_path / "event.log",
        event_line(time=iso(old_1h), bytes_sent="1"),
        event_line(time=iso(old_5m), bytes_sent="2"),
        event_line(time=iso(kept_5m), bytes_sent="4"),
    )
    event_sweep.sweep_once(db, settings, now)

    assert [r["bytes_served"] for r in rows(db, "server_traffic", "5m")] == [4]
    assert [r["bytes_served"] for r in rows(db, "server_traffic", "1h")] == [2, 4]
    assert sum(r["bytes_served"] for r in rows(db, "server_traffic", "1d")) == 7
    assert [r["bytes_served"] for r in rows(db, "client_depot_traffic", "5m")] == [4]
    # 1d keeps everything: the 30-day-old day plus the 11th (two lines).
    assert [r["bytes_served"] for r in rows(db, "client_depot_traffic", "1d")] == [1, 6]

    # Idempotent: a second sweep with nothing new removes nothing more.
    event_sweep.sweep_once(db, settings, now + timedelta(minutes=5))
    assert sum(r["bytes_served"] for r in rows(db, "server_traffic", "1d")) == 7


def test_retention_waits_while_a_backlog_is_still_being_read(
    tmp_path: Path, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings(tmp_path)
    now = at(13, 12, 0)
    old = now - timedelta(days=3)
    lines = [event_line(time=iso(old), bytes_sent=str(n + 1)) for n in range(6)]
    write_log(tmp_path / "event.log", *lines)
    # A batch limit of about two lines turns the file into a backlog.
    monkeypatch.setattr(event_sweep, "MAX_BATCH_BYTES", len(lines[0]) * 2 + 3)

    outcome = event_sweep.sweep_once(db, settings, now)
    assert outcome.lines > 0
    assert len(rows(db, "server_traffic", "5m")) == 1  # NOT pruned mid-backlog

    for minute in range(1, 10):
        event_sweep.sweep_once(db, settings, now + timedelta(minutes=minute))
    (hour,) = rows(db, "server_traffic", "1h")
    assert hour["bytes_served"] == sum(range(1, 7))
    assert hour["active_buckets"] == 1  # one 5-minute bucket, counted once
    assert rows(db, "server_traffic", "5m") == []  # caught up -> pruned


# ---------------------------------------------------------------------------
# Rotation robustness
# ---------------------------------------------------------------------------


def test_a_rename_rotated_log_larger_than_the_cursor_is_read_from_zero(
    tmp_path: Path, db: sqlite3.Connection
) -> None:
    settings = make_settings(tmp_path)
    log = tmp_path / "event.log"
    write_log(log, event_line(time=iso(at(13, 10, 0)), addr="10.0.0.1", bytes_sent="1"))
    event_sweep.sweep_once(db, settings, at(13, 10, 5))
    first = event_sweep.read_state(db)
    assert first.log_identity is not None
    assert first.cursor_offset > 0

    os.rename(log, tmp_path / "event.log.1")
    write_log(
        log,
        *[
            event_line(time=iso(at(13, 10, 6)), addr="10.0.0.2", bytes_sent="10")
            for _ in range(5)
        ],
    )
    assert log.stat().st_size > first.cursor_offset
    assert log.stat().st_ino != first.log_ino

    outcome = event_sweep.sweep_once(db, settings, at(13, 10, 10))
    state = event_sweep.read_state(db)

    assert outcome.rotated
    assert outcome.lines == 5  # all five, none skipped by the old offset
    assert state.rotations_detected_total == 1
    assert state.last_rotation_detected_at == "2026-10-13T10:10:00Z"
    assert state.log_ino == log.stat().st_ino
    assert state.cursor_offset == log.stat().st_size


def test_a_shrunken_log_is_counted_as_a_detected_rotation(
    tmp_path: Path, db: sqlite3.Connection
) -> None:
    settings = make_settings(tmp_path)
    log = tmp_path / "event.log"
    write_log(log, *[event_line(time=iso(at(13, 10, 0))) for _ in range(3)])
    event_sweep.sweep_once(db, settings, at(13, 10, 5))
    with open(log, "r+b") as handle:  # same inode, truncated in place
        handle.truncate(0)
    append_log(log, event_line(time=iso(at(13, 10, 6))))

    outcome = event_sweep.sweep_once(db, settings, at(13, 10, 10))
    assert outcome.rotated and outcome.lines == 1
    assert event_sweep.read_state(db).rotations_detected_total == 1


def test_the_sweepers_own_truncation_is_not_a_detected_rotation(
    tmp_path: Path, db: sqlite3.Connection
) -> None:
    settings = make_settings(tmp_path, max_bytes=1)
    log = tmp_path / "event.log"
    write_log(log, event_line(time=iso(at(13, 10, 0))))
    first = event_sweep.sweep_once(db, settings, at(13, 10, 5))
    assert first.truncated
    append_log(log, event_line(time=iso(at(13, 10, 6))))
    second = event_sweep.sweep_once(db, settings, at(13, 10, 10))

    state = event_sweep.read_state(db)
    assert not second.rotated
    assert second.lines == 1
    assert state.rotations_detected_total == 0
    assert state.last_rotated_at is not None


def test_stats_reports_detected_rotations_and_traffic_since(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from tests.conftest import TEST_API_KEY
    from vault_api.main import create_app

    settings = make_settings(tmp_path)
    init_db(settings.db_path)
    conn = get_connection(settings.db_path)
    try:
        log = tmp_path / "event.log"
        write_log(log, event_line(time=iso(at(13, 10, 0))))
        event_sweep.sweep_once(conn, settings, at(13, 10, 5))
        os.rename(log, tmp_path / "event.log.1")
        write_log(log, event_line(time=iso(at(13, 10, 6))))
        event_sweep.sweep_once(conn, settings, at(13, 10, 10))
    finally:
        conn.close()

    # No `with`: no lifespan, so no scheduler thread sweeping on the real clock.
    client = TestClient(create_app(settings))
    body = client.get("/v1/stats", headers={"X-Api-Key": TEST_API_KEY}).json()
    assert body["rotations_detected_total"] == 1
    assert body["last_rotation_detected_at"] == "2026-10-13T10:10:00Z"
    assert body["traffic_since"] == "2026-10-13T10:05:00Z"


# ---------------------------------------------------------------------------
# Review round: inode-only identity, draining <path>.1, active buckets of
# expired 5-minute buckets, traffic_since
# ---------------------------------------------------------------------------


def test_a_changed_st_dev_with_the_same_inode_is_not_a_rotation(
    tmp_path: Path, db: sqlite3.Connection
) -> None:
    """ZFS hands out anonymous device numbers that can change across a
    reboot; the same file must not be re-read from offset 0."""
    settings = make_settings(tmp_path)
    log = tmp_path / "event.log"
    write_log(log, event_line(time=iso(at(13, 10, 0)), bytes_sent="100"))
    event_sweep.sweep_once(db, settings, at(13, 10, 5))
    before = event_sweep.read_state(db)
    db.execute("UPDATE event_sweep_state SET log_dev = log_dev + 12345")
    db.commit()
    append_log(log, event_line(time=iso(at(13, 10, 6)), bytes_sent="1"))

    outcome = event_sweep.sweep_once(db, settings, at(13, 10, 10))
    state = event_sweep.read_state(db)
    assert not outcome.rotated
    assert outcome.lines == 1  # only the new line, nothing re-read
    assert state.rotations_detected_total == 0
    assert state.cursor_offset == log.stat().st_size > before.cursor_offset
    assert [r["bytes_served"] for r in rows(db, "server_traffic", "1d")] == [101]
    assert state.log_dev == log.stat().st_dev  # refreshed for diagnostics


def test_a_rename_rotation_first_drains_the_old_files_unread_tail(
    tmp_path: Path, db: sqlite3.Connection
) -> None:
    settings = make_settings(tmp_path)
    log = tmp_path / "event.log"
    old = tmp_path / "event.log.1"
    write_log(log, event_line(time=iso(at(13, 10, 0)), addr="10.0.0.1", bytes_sent="1"))
    event_sweep.sweep_once(db, settings, at(13, 10, 5))
    old_ino = log.stat().st_ino
    # Written after that sweep, then nginx's file is renamed away.
    append_log(
        log,
        event_line(time=iso(at(13, 10, 6)), addr="10.0.0.1", bytes_sent="10"),
        event_line(time=iso(at(13, 10, 7)), addr="10.0.0.1", bytes_sent="20"),
    )
    with open(log, "ab") as handle:
        handle.write(b"v1\tpartial line never finished")
    os.rename(log, old)
    write_log(log, event_line(time=iso(at(13, 10, 8)), addr="10.0.0.2", bytes_sent="300"))

    drained = event_sweep.sweep_once(db, settings, at(13, 10, 10))
    state = event_sweep.read_state(db)
    assert drained.lines == 2 and not drained.rotated
    assert state.log_ino == old_ino
    assert state.rotations_detected_total == 0

    switched = event_sweep.sweep_once(db, settings, at(13, 10, 15))
    state = event_sweep.read_state(db)
    assert switched.rotated and switched.lines == 1
    assert state.log_ino == log.stat().st_ino
    assert state.rotations_detected_total == 1
    assert [r["bytes_served"] for r in rows(db, "server_traffic", "1d")] == [331]


def test_an_old_file_with_another_inode_is_not_drained(
    tmp_path: Path, db: sqlite3.Connection
) -> None:
    settings = make_settings(tmp_path)
    log = tmp_path / "event.log"
    write_log(log, event_line(time=iso(at(13, 10, 0)), bytes_sent="1"))
    event_sweep.sweep_once(db, settings, at(13, 10, 5))
    os.rename(log, tmp_path / "gone.log")
    # Some unrelated file at <path>.1 with plenty of lines.
    write_log(tmp_path / "event.log.1", *[event_line(bytes_sent="999")] * 5)
    write_log(log, event_line(time=iso(at(13, 10, 6)), bytes_sent="2"))

    outcome = event_sweep.sweep_once(db, settings, at(13, 10, 10))
    assert outcome.rotated and outcome.lines == 1
    assert [r["bytes_served"] for r in rows(db, "server_traffic", "1d")] == [3]


def test_no_self_truncation_while_draining_the_old_file(
    tmp_path: Path, db: sqlite3.Connection
) -> None:
    """While the cursor refers to <path>.1 it says nothing about the live
    log; a live log that happens to be exactly cursor-sized must survive."""
    settings = make_settings(tmp_path, max_bytes=1)
    log = tmp_path / "event.log"
    line = event_line(time=iso(at(13, 10, 6)), bytes_sent="10")
    write_log(log, event_line(time=iso(at(13, 10, 0))))
    event_sweep.sweep_once(db, settings, at(13, 10, 5))  # truncates, cursor 0
    assert event_sweep.read_state(db).cursor_offset == 0
    append_log(log, line)
    os.rename(log, tmp_path / "event.log.1")
    write_log(log, line)  # same byte length as the drained tail

    outcome = event_sweep.sweep_once(db, settings, at(13, 10, 10))
    assert outcome.lines == 1 and not outcome.truncated
    assert log.stat().st_size == len(line) + 1


def test_a_late_line_for_an_expired_5_minute_bucket_does_not_count_it_active_twice(
    tmp_path: Path, db: sqlite3.Connection
) -> None:
    settings = make_settings(tmp_path)
    log = tmp_path / "event.log"
    now = at(13, 12, 0)
    old = now - timedelta(days=3)
    write_log(log, event_line(time=iso(old), bytes_sent="5"))
    event_sweep.sweep_once(db, settings, now)
    assert rows(db, "server_traffic", "5m") == []  # pruned, caught up
    append_log(log, event_line(time=iso(old + timedelta(seconds=30)), bytes_sent="7"))
    event_sweep.sweep_once(db, settings, now + timedelta(minutes=5))

    for tier in ("1h", "1d"):
        (row,) = rows(db, "server_traffic", tier)
        assert row["bytes_served"] == 12
        assert row["active_buckets"] == 1


def test_traffic_since_waits_for_the_first_written_rows(
    tmp_path: Path, db: sqlite3.Connection
) -> None:
    settings = make_settings(tmp_path)
    log = tmp_path / "event.log"
    write_log(log)
    event_sweep.sweep_once(db, settings, at(13, 10, 0))
    assert event_sweep.read_state(db).traffic_since is None
    append_log(log, event_line(time=iso(at(13, 10, 1))))
    event_sweep.sweep_once(db, settings, at(13, 10, 5))
    event_sweep.sweep_once(db, settings, at(13, 10, 10))
    assert event_sweep.read_state(db).traffic_since == "2026-10-13T10:05:00Z"
