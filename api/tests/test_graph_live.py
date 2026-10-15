"""WP GRAPH-LIVE: the in-memory live traffic tier and ``GET /v1/throughput/live``.

Synthetic event lines, an injected clock, no threads, no network.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from tests.test_event_sweep import append_log, event_line, make_settings, write_log
from vault_api import event_sweep, live_tier, runner_identity
from vault_api.db import get_connection, init_db
from vault_api.live_tier import LiveTier, PersistedMinute, LiveTotals
from vault_api.main import create_app

UTC = timezone.utc
AUTH = {"X-Api-Key": TEST_API_KEY}
#: 12:00:07 -- inside a 5 s cell, a 15 s cell and a minute.
NOW = datetime(2026, 10, 16, 12, 0, 7, tzinfo=UTC)


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def line(ago: float, bytes_sent: int, *, addr: str = "10.0.0.5", status: str = "HIT", now: datetime = NOW) -> str:
    return event_line(
        time=iso(now - timedelta(seconds=ago)),
        addr=addr,
        cache_status=status,
        bytes_sent=str(bytes_sent),
    )


class Clock:
    def __init__(self, start: datetime = NOW) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def no_rows(_start: int, _end: int) -> dict[int, PersistedMinute]:
    return {}


def never_runner(_addr: str, _iso: str) -> bool:
    return False


def tier_at(clock: Clock | None = None, **kwargs) -> tuple[LiveTier, Clock]:
    clock = clock or Clock()
    return LiveTier(clock=clock, **kwargs), clock


def total_bytes(points) -> int:
    return sum(p.all_totals.bytes_served for p in points)


# ---------------------------------------------------------------------------
# Watcher and cadence
# ---------------------------------------------------------------------------


def test_nobody_polling_means_the_log_is_never_opened(tmp_path: Path, monkeypatch) -> None:
    log = tmp_path / "event.log"
    write_log(log, line(1, 100))
    tier, clock = tier_at()

    def boom(*_a, **_k):
        raise AssertionError("the log must not be opened without a watcher")

    monkeypatch.setattr(event_sweep, "open_event_log", boom)
    assert tier.refresh(str(log), clock()) == 0
    assert tier.watcher_active(clock()) is False


def test_the_watcher_expires_after_the_window(tmp_path: Path) -> None:
    log = tmp_path / "event.log"
    write_log(log, line(1, 100))
    tier, clock = tier_at()
    tier.touch(clock())
    assert tier.watcher_active(clock()) is True
    clock.advance(live_tier.WATCHER_WINDOW_SECONDS + 1)
    assert tier.watcher_active(clock()) is False
    assert tier.refresh(str(log), clock()) == 0


def test_tails_are_throttled_to_one_per_interval_whoever_polls(tmp_path: Path) -> None:
    log = tmp_path / "event.log"
    write_log(log, line(1, 100))
    tier, clock = tier_at()
    tier.touch(clock())
    assert tier.refresh(str(log), clock()) == 1
    append_log(log, line(0.5, 50))
    clock.advance(1.0)
    tier.touch(clock())
    assert tier.refresh(str(log), clock()) == 0  # inside the 2 s interval
    clock.advance(1.5)
    tier.touch(clock())
    assert tier.refresh(str(log), clock()) == 1


def test_a_missing_log_is_fine(tmp_path: Path) -> None:
    tier, clock = tier_at()
    tier.touch(clock())
    assert tier.refresh(str(tmp_path / "nope.log"), clock()) == 0
    assert tier.stitch(clock(), no_rows, never_runner)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def test_lines_land_in_cells_by_event_time_and_counts_are_exact(tmp_path: Path) -> None:
    log = tmp_path / "event.log"
    write_log(
        log,
        line(1, 100),  # 12:00:06 -> cell :05
        line(3, 40, status="MISS"),  # 12:00:04 -> cell :00
        line(3, 60),  # same cell
        line(2, 999, status="HIT").replace("\t200", "\t404"),  # error: no bytes
    )
    tier, clock = tier_at()
    tier.touch(clock())
    assert tier.refresh(str(log), clock()) == 4
    points = tier.stitch(clock(), no_rows, never_runner)
    by_t = {p.t: p for p in points if p.bucket_seconds == 5}
    first = by_t["2026-10-16T12:00:00Z"].all_totals
    assert (first.bytes_served, first.hit_bytes, first.miss_bytes) == (100, 60, 40)
    second = by_t["2026-10-16T12:00:05Z"].all_totals
    assert (second.bytes_served, second.requests) == (100, 2)  # the 404 counts as a request
    assert total_bytes(points) == 200


def test_a_partial_last_line_waits_and_is_read_once_complete(tmp_path: Path) -> None:
    log = tmp_path / "event.log"
    write_log(log, line(1, 100), line(1, 7), terminated=False)
    tier, clock = tier_at()
    tier.touch(clock())
    assert tier.refresh(str(log), clock()) == 1
    clock.advance(3)
    tier.touch(clock())
    append_log(log, "")  # the newline arrives
    assert tier.refresh(str(log), clock()) == 1


def test_an_idle_stretch_is_caught_up_by_event_time(tmp_path: Path) -> None:
    log = tmp_path / "event.log"
    write_log(log, line(1, 100))
    tier, clock = tier_at()
    tier.touch(clock())
    tier.refresh(str(log), clock())
    clock.advance(200)  # nobody polls for 200 s
    append_log(log, line(150, 500, now=clock()), line(20, 300, now=clock()))
    tier.touch(clock())
    # After an idle gap the log is read afresh (the sweep may have rewritten it).
    assert tier.refresh(str(log), clock()) == 3
    points = tier.stitch(clock(), no_rows, never_runner)
    assert total_bytes(points) == 100 + 500 + 300


def test_old_future_garbage_and_runner_mark_lines(tmp_path: Path) -> None:
    log = tmp_path / "event.log"
    write_log(
        log,
        line(live_tier.RING_SECONDS + 600, 1000),  # older than the ring
        line(-3600, 70),  # an hour in the future: clamped to now
        "not a log line",
        event_line(
            time=iso(NOW - timedelta(seconds=1)),
            cache_status=runner_identity.MARK_CACHE_STATUS,
            uri="/x",
            bytes_sent="5",
        ),
    )
    tier, clock = tier_at()
    tier.touch(clock())
    assert tier.refresh(str(log), clock()) == 1
    points = tier.stitch(clock(), no_rows, never_runner)
    assert total_bytes(points) == 70
    assert points[-1].all_totals.bytes_served == 70  # the running cell


def test_truncation_and_replacement_restart_at_the_top(tmp_path: Path) -> None:
    log = tmp_path / "event.log"
    write_log(log, line(2, 100), line(2, 100))
    tier, clock = tier_at()
    tier.touch(clock())
    tier.refresh(str(log), clock())
    clock.advance(3)
    write_log(log, line(1, 5))  # shrank (the sweep truncated it)
    tier.touch(clock())
    assert tier.refresh(str(log), clock()) == 1
    clock.advance(3)
    log.rename(tmp_path / "event.log.1")  # keep the old inode alive so the new file cannot reuse its number
    write_log(log, line(1, 6), line(1, 7))  # new inode
    tier.touch(clock())
    assert tier.refresh(str(log), clock()) == 2


def test_a_sweep_truncation_during_an_idle_gap_hands_the_gap_to_the_persisted_tier(tmp_path: Path) -> None:
    log = tmp_path / "event.log"
    write_log(log, line(1, 100))
    tier, clock = tier_at()
    tier.touch(clock())
    tier.refresh(str(log), clock())
    first_cover = tier.covered_from()
    clock.advance(600)  # idle: the sweep consumes and truncates the log (same inode)
    with open(log, "r+b") as handle:
        handle.truncate(0)
    append_log(log, line(2, 40, now=clock()))
    tier.touch(clock())
    assert tier.refresh(str(log), clock()) == 1
    assert tier.covered_from() != first_cover
    gap_minute = int((NOW + timedelta(minutes=5)).replace(second=0).timestamp())

    def persisted(start: int, end: int) -> dict[int, PersistedMinute]:
        return {gap_minute: PersistedMinute(LiveTotals(1, 700, 700, 0), LiveTotals(0, 0, 0, 0), 20)}

    points = tier.stitch(clock(), persisted, never_runner)
    row = next(p for p in points if p.t.startswith(iso(datetime.fromtimestamp(gap_minute, tz=UTC))[:16]))
    assert row.source == "persisted"
    assert row.all_totals.bytes_served == 700


def test_a_log_that_regrew_past_the_old_cursor_is_not_resumed_mid_line(tmp_path: Path) -> None:
    log = tmp_path / "event.log"
    write_log(log, line(1, 100))
    tier, clock = tier_at()
    tier.touch(clock())
    tier.refresh(str(log), clock())
    clock.advance(120)
    with open(log, "r+b") as handle:
        handle.truncate(0)
    append_log(log, *[line(3, 11, now=clock()) for _ in range(8)])  # bigger than before
    tier.touch(clock())
    assert tier.refresh(str(log), clock()) == 8
    assert total_bytes(tier.stitch(clock(), no_rows, never_runner)) == 88


def test_a_read_starting_mid_file_aligns_to_the_next_line(tmp_path: Path) -> None:
    log = tmp_path / "event.log"
    write_log(log, *[line(5, 1) for _ in range(300)])
    tier, clock = tier_at(max_read_bytes=2048, max_backfill_bytes=1000)
    tier.touch(clock())
    folded = tier.refresh(str(log), clock())
    assert folded > 0
    # Every folded line is whole: bytes_sent=1 each, no torn fragment parsed.
    assert total_bytes(tier.stitch(clock(), no_rows, never_runner)) == folded


def test_a_huge_backlog_is_skipped_to_its_end(tmp_path: Path) -> None:
    log = tmp_path / "event.log"
    old = [line(5, 1) for _ in range(300)]
    write_log(log, *old)
    tier, clock = tier_at(max_read_bytes=2048, max_backfill_bytes=4096)
    tier.touch(clock())
    folded = tier.refresh(str(log), clock())
    # Only the end of the file is read: bounded work, not all 300 lines.
    assert 0 < folded < 300


# ---------------------------------------------------------------------------
# Bounded memory
# ---------------------------------------------------------------------------


def test_distinct_addresses_per_cell_are_capped(tmp_path: Path) -> None:
    log = tmp_path / "event.log"
    count = live_tier.MAX_ADDRS_PER_CELL + 40
    write_log(log, *[line(1, 10, addr=f"10.1.{i // 250}.{i % 250}") for i in range(count)])
    tier, clock = tier_at()
    tier.touch(clock())
    assert tier.refresh(str(log), clock()) == count
    points = tier.stitch(clock(), no_rows, never_runner)
    assert total_bytes(points) == 10 * count  # the overflow still counts
    assert tier._cells and max(len(c) for c in tier._cells.values()) <= live_tier.MAX_ADDRS_PER_CELL + 1


def test_the_ring_forgets_cells_older_than_its_horizon(tmp_path: Path) -> None:
    log = tmp_path / "event.log"
    write_log(log, line(1, 10))
    tier, clock = tier_at(ring_seconds=60)
    tier.touch(clock())
    tier.refresh(str(log), clock())
    assert tier.cell_count() == 1
    clock.advance(120)
    tier.touch(clock())
    append_log(log, line(1, 10, now=clock()))
    tier.refresh(str(log), clock())
    assert tier.cell_count() == 1  # the first cell was pruned


# ---------------------------------------------------------------------------
# Stitching
# ---------------------------------------------------------------------------


def test_the_stitched_series_gets_coarser_going_back_and_is_contiguous(tmp_path: Path) -> None:
    tier, clock = tier_at()
    points = tier.stitch(clock(), no_rows, never_runner)
    sizes = [p.bucket_seconds for p in points]
    # 60 s points first, then 15 s, then 5 s: monotonically finer.
    assert sizes == sorted(sizes, reverse=True)
    assert set(sizes) == {60, 15, 5}
    assert sizes.count(5) <= 12 and sizes.count(5) >= 9
    assert 50 <= sizes.count(60) <= 56
    # Contiguous: each point starts where the previous one ends.
    for prev, nxt in zip(points, points[1:]):
        a = datetime.strptime(prev.t, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        b = datetime.strptime(nxt.t, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        assert b - a == timedelta(seconds=prev.bucket_seconds)
    # The last point is the running 5 s cell; the series spans about an hour.
    last = datetime.strptime(points[-1].t, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    first = datetime.strptime(points[0].t, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    assert last <= clock() < last + timedelta(seconds=5)
    assert timedelta(minutes=55) <= clock() - first <= timedelta(minutes=61)


def test_every_point_has_one_source_so_nothing_is_counted_twice(tmp_path: Path) -> None:
    log = tmp_path / "event.log"
    # 6 minutes back: a minute the ring covers (the log was read from byte 0).
    write_log(log, line(6 * 60, 1000), line(30, 200))
    tier, clock = tier_at()
    tier.touch(clock())
    tier.refresh(str(log), clock())

    def persisted(start: int, end: int) -> dict[int, PersistedMinute]:
        # The persisted tier holds the same 1000 bytes, as a sweep would have.
        minute = int((NOW - timedelta(seconds=6 * 60)).replace(second=0).timestamp())
        return {minute: PersistedMinute(LiveTotals(1, 1000, 1000, 0), LiveTotals(), 1000)}

    points = tier.stitch(clock(), persisted, never_runner)
    assert total_bytes(points) == 1200  # 1000 once (ring), 200 once
    minute_point = next(p for p in points if p.t == "2026-10-16T11:54:00Z")
    assert minute_point.source == "live"


def test_a_minute_the_ring_never_covered_comes_from_the_persisted_tier(tmp_path: Path) -> None:
    log = tmp_path / "event.log"
    # Big file read mid-way: coverage starts at a later minute, earlier ones
    # must come from the persisted rows.
    write_log(log, *[line(20, 10) for _ in range(200)])
    tier, clock = tier_at(max_backfill_bytes=2048, max_read_bytes=2048)
    tier.touch(clock())
    tier.refresh(str(log), clock())
    old_minute = int((NOW - timedelta(minutes=30)).replace(second=0).timestamp())

    def persisted(start: int, end: int) -> dict[int, PersistedMinute]:
        return {old_minute: PersistedMinute(LiveTotals(3, 900, 600, 300), LiveTotals(1, 100, 100, 0), 500)}

    points = tier.stitch(clock(), persisted, never_runner)
    row = next(p for p in points if p.t == iso(datetime.fromtimestamp(old_minute, tz=UTC)).replace("+00:00", "Z"))
    assert row.source == "persisted"
    assert row.all_totals.bytes_served == 900
    assert row.runner.bytes_served == 100
    assert row.peak_bytes_per_second == pytest.approx(500 / 60)
    assert tier.covered_from() is not None


def test_current_speed_reads_the_last_seconds_and_splits_the_runner(tmp_path: Path) -> None:
    log = tmp_path / "event.log"
    write_log(
        log,
        line(2, 1000, addr="10.0.0.5"),
        line(4, 3000, addr="10.0.0.9"),  # the runner
        line(120, 99999, addr="10.0.0.5"),  # long ago: not in "current"
    )
    tier, clock = tier_at()
    tier.touch(clock())
    tier.refresh(str(log), clock())
    current = tier.current(clock(), lambda addr, _iso: addr == "10.0.0.9")
    # Window = 15 s + the 2 s already elapsed in the running 5 s cell.
    assert current.window_seconds == 17
    assert current.bytes_per_second == pytest.approx(4000 / 17)
    assert current.runner_bytes_per_second == pytest.approx(3000 / 17)


def test_concurrent_polls_and_reads_do_not_corrupt_the_ring(tmp_path: Path) -> None:
    log = tmp_path / "event.log"
    write_log(log, *[line(i % 50, 10) for i in range(500)])
    tier, clock = tier_at(tail_interval=0.0)
    errors: list[BaseException] = []

    def work() -> None:
        try:
            for _ in range(30):
                tier.touch(clock())
                tier.refresh(str(log), clock())
                tier.stitch(clock(), no_rows, never_runner)
                tier.current(clock(), never_runner)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert total_bytes(tier.stitch(clock(), no_rows, never_runner)) == 5000  # read exactly once


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@pytest.fixture
def served(tmp_path: Path):
    settings = make_settings(tmp_path, cooldown=0)
    init_db(settings.db_path)
    clock = Clock()
    app = create_app(settings)
    app.state.live_tier = LiveTier(clock=clock)
    client = TestClient(app)
    conn = get_connection(settings.db_path)
    try:
        yield settings, client, clock, conn
    finally:
        conn.close()


def test_the_route_requires_the_api_key(served) -> None:
    _settings, client, _clock, _conn = served
    assert client.get("/v1/throughput/live").status_code == 401


def test_a_disabled_feed_answers_null_and_empty(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, log_path="")
    init_db(settings.db_path)
    client = TestClient(create_app(settings))
    body = client.get("/v1/throughput/live", headers=AUTH).json()
    assert body["event_feed"]["state"] == "disabled"
    assert body["current"] is None and body["points"] == []


def test_polling_tails_the_log_and_returns_the_stitched_series(served, tmp_path: Path) -> None:
    settings, client, clock, conn = served
    write_log(
        tmp_path / "event.log",
        line(2, 1000, addr="10.0.0.5"),
        line(4, 3000, addr="10.0.0.9", status="MISS"),
    )
    # The runner's address is marked for today (as a sweep would have done).
    runner_identity._extend_period(conn, "10.0.0.9", NOW - timedelta(minutes=1))
    conn.commit()
    body = client.get("/v1/throughput/live", headers=AUTH).json()
    assert body["range"] == "live" and body["tier"] == "live"
    assert body["event_feed"]["state"] == "never-swept"
    assert body["watcher_window_seconds"] == 30
    sizes = [p["bucket_seconds"] for p in body["points"]]
    assert sizes == sorted(sizes, reverse=True) and set(sizes) == {60, 15, 5}
    assert sum(p["bytes_served"] for p in body["points"]) == 4000
    assert sum(p["runner_bytes"] for p in body["points"]) == 3000
    # Clients-only figures exclude the runner's miss bytes.
    assert sum(p["miss_bytes"] for p in body["points"]) == 0
    assert sum(p["hit_bytes"] for p in body["points"]) == 1000
    assert body["current"]["bytes_per_second"] == pytest.approx(4000 / 17)
    assert body["current"]["runner_bytes_per_second"] == pytest.approx(3000 / 17)
    assert body["live_since"] is not None
    assert body["range_start"] == body["points"][0]["t"]

    # A second poll within 2 s does not re-read; new lines show after 2 s.
    append_log(tmp_path / "event.log", line(0, 500, now=clock()))
    clock.advance(1)
    again = client.get("/v1/throughput/live", headers=AUTH).json()
    assert sum(p["bytes_served"] for p in again["points"]) == 4000
    clock.advance(2)
    later = client.get("/v1/throughput/live", headers=AUTH).json()
    assert sum(p["bytes_served"] for p in later["points"]) == 4500


def test_the_endpoint_stitches_in_persisted_minutes(served, tmp_path: Path) -> None:
    settings, client, clock, conn = served
    write_log(tmp_path / "event.log")
    # A sweep wrote an hour-old minute into the 1-minute tier.
    stamp = (NOW - timedelta(minutes=30)).replace(second=0)
    conn.execute(
        """
        INSERT INTO server_traffic (tier, bucket_at, requests, hits, misses, bypasses, errors,
                                    bytes_served, hit_bytes, miss_bytes, peak_bucket_bytes, active_buckets)
        VALUES ('1m', ?, 2, 2, 0, 0, 0, 800, 800, 0, 800, 1)
        """,
        (stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),),
    )
    conn.commit()
    body = client.get("/v1/throughput/live", headers=AUTH).json()
    row = next(p for p in body["points"] if p["t"] == stamp.strftime("%Y-%m-%dT%H:%M:%SZ"))
    assert row["source"] == "persisted" and row["bucket_seconds"] == 60
    assert row["bytes_served"] == 800
    assert row["peak_bytes_per_second"] == pytest.approx(800 / 60)
