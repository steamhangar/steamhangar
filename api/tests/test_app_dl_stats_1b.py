"""WP APP-DL-STATS-1b: the Downloads summary tiles and the job figures include
the span the event sweep has not read yet (operator decision 2026-11-21, Weg
A; ``vault_api/traffic_stitch.py``).

The operator's report (2026-11-01): while the runner prefilled at ~13 MB/s,
the job card read "0 MB/s · 0 GB loaded so far" and Served / From internet /
Peak read 0, because they came from the swept tiers only. Synthetic event
lines, an injected clock, the real sweep; no threads, no network.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from tests.test_event_sweep import append_log, event_line, make_settings, write_log
from vault_api import event_sweep, jobs as jobs_queue, live_tier, mapping, runner_identity, traffic
from vault_api import traffic_stitch
from vault_api.db import get_connection, init_db
from vault_api.live_tier import LiveTier, LiveTotals, RingBucket
from vault_api.main import create_app

UTC = timezone.utc
AUTH = {"X-Api-Key": TEST_API_KEY}
#: 12:00:07 -- 7 s into the running minute (and 5-minute bucket).
NOW = datetime(2026, 10, 18, 12, 0, 7, tzinfo=UTC)
RUNNER = "10.0.0.9"
PC = "10.0.0.5"
APPID = 440
DEPOT = 441


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def zulu(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def line(
    at: datetime, size: int, *, addr: str = RUNNER, status: str = "MISS", depot: int = DEPOT
) -> str:
    return event_line(
        time=iso(at), addr=addr, cache_status=status, depot=str(depot), bytes_sent=str(size)
    )


class Clock:
    def __init__(self, start: datetime = NOW) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now


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


def sweep(conn: sqlite3.Connection, settings, at: datetime) -> None:
    """The real sweep, then the 1-minute tier dated far back so 1 h reads it."""
    event_sweep.sweep_once(conn, settings, at)
    conn.execute("UPDATE event_sweep_state SET traffic_1m_since = ?", (zulu(at - timedelta(days=1)),))
    conn.commit()


def throughput(client: TestClient, range_name: str = "1h") -> dict:
    body = client.get("/v1/throughput", params={"range": range_name}, headers=AUTH).json()
    assert body["event_feed"]["state"] == "ok"
    return body


def running_job(conn: sqlite3.Connection, started: datetime) -> None:
    mapping.upsert_mapping(conn, depotid=DEPOT, appid=APPID, name=None)
    conn.commit()
    jobs_queue.enqueue_prefill(conn, APPID)
    jobs_queue.claim_next_job(conn)
    conn.execute("UPDATE jobs SET started_at = ?", (zulu(started),))
    conn.commit()


# ---------------------------------------------------------------------------
# The operator's case
# ---------------------------------------------------------------------------


def test_the_operators_case_card_and_tiles_count_the_unswept_minutes(served, tmp_path: Path) -> None:
    """An idle hour, the last sweep 3 minutes ago, a job that started after
    it: before APP-DL-STATS-1b every figure read 0 (the swept tiers have
    nothing). Now the tiles and the card count what the ring has read."""
    settings, client, _clock, conn = served
    log = tmp_path / "event.log"
    write_log(log)
    sweep(conn, settings, NOW - timedelta(minutes=3))
    runner_identity._extend_period(conn, RUNNER, NOW - timedelta(minutes=3))
    conn.commit()
    running_job(conn, NOW - timedelta(minutes=2))
    append_log(
        log,
        line(NOW - timedelta(seconds=100), 3_000_000),  # 11:58:27, minute 11:58
        line(NOW - timedelta(seconds=50), 2_000_000),  # 11:59:17, minute 11:59
        line(NOW - timedelta(seconds=4), 600_000),  # 12:00:03, the running minute
        line(NOW - timedelta(seconds=3), 400_000),
    )

    body = throughput(client, "1h")
    summary = body["summary"]
    assert summary["bytes_served"] == 6_000_000
    assert summary["internet_bytes"] == 6_000_000
    assert summary["prefill_runner"]["bytes_served"] == 6_000_000
    assert summary["prefill_runner"]["miss_bytes"] == 6_000_000
    # Clients only: the runner's MISS is not a client missing the cache.
    assert summary["miss_bytes"] == 0 and summary["byte_hit_rate"] is None
    # 1 h is the 1-minute tier: peak = the busiest minute, average over the
    # three active minutes.
    assert body["tier"] == "1m"
    assert summary["peak_bytes_per_second"] == pytest.approx(3_000_000 / 60)
    assert summary["average_bytes_per_second"] == pytest.approx(6_000_000 / (3 * 60))
    # Request counts stay swept (the ring counts bytes only).
    assert summary["requests"] == 0

    (job,) = body["jobs"]
    # "So far": every byte on its depot since its start bucket (11:55).
    assert job["bytes_since_start"] == 6_000_000
    # Speed: the "current speed" window of the ring (12:00:07 - 11:59:50 = 17 s),
    # the same read as the runner's "Loading now" row.
    window = body["now"]["window_seconds"]
    assert window == 17
    assert job["bytes_per_second"] == pytest.approx(1_000_000 / 17)
    (runner_row,) = [s for s in body["now"]["loading_sources"] if s["kind"] == "runner"]
    assert runner_row["bytes_per_second"] == pytest.approx(job["bytes_per_second"])


def test_the_job_speed_is_not_diluted_by_minutes_before_the_job(served, tmp_path: Path) -> None:
    """The swept window (5-10 minutes) started before the job; the ring's
    window holds only the job's own seconds."""
    settings, client, _clock, conn = served
    log = tmp_path / "event.log"
    write_log(log, line(NOW - timedelta(minutes=4), 9_000_000))
    sweep(conn, settings, NOW - timedelta(seconds=30))
    runner_identity._extend_period(conn, RUNNER, NOW - timedelta(minutes=5))
    conn.commit()
    running_job(conn, NOW - timedelta(seconds=20))
    append_log(log, line(NOW - timedelta(seconds=2), 1_700_000))

    (job,) = throughput(client, "24h")["jobs"]
    assert job["bytes_per_second"] == pytest.approx(1_700_000 / 17)
    # The swept 9 MB fell into the start bucket (11:55), so they are in "so
    # far" by the bucket-aligned rule (as before), plus the unswept 1.7 MB.
    assert job["bytes_since_start"] == 10_700_000


# ---------------------------------------------------------------------------
# Exactly once across the ring / sweep handover
# ---------------------------------------------------------------------------


def test_a_sweep_in_the_middle_of_the_span_never_counts_a_line_twice(served, tmp_path: Path) -> None:
    settings, client, clock, conn = served
    log = tmp_path / "event.log"
    write_log(log, line(NOW - timedelta(minutes=12), 500))
    sweep(conn, settings, NOW - timedelta(minutes=11))
    runner_identity._extend_period(conn, RUNNER, NOW - timedelta(minutes=12))
    conn.commit()
    running_job(conn, NOW - timedelta(minutes=10))
    append_log(log, line(NOW - timedelta(seconds=50), 1000), line(NOW - timedelta(seconds=40), 2000))

    def figures() -> tuple[int, int]:
        body = throughput(client, "24h")
        return body["summary"]["bytes_served"], body["jobs"][0]["bytes_since_start"]

    # Unswept: the ring supplies both lines (the job counts from its start
    # bucket 11:50, so the 500 of 11:48 is in the range but not the job).
    assert figures() == (3500, 3000)
    # The sweep reads the same two lines (the ring has them too): once.
    clock.now = NOW + timedelta(seconds=10)
    sweep(conn, settings, clock.now)
    assert figures() == (3500, 3000)
    # A line after that sweep, in the SAME 5-minute bucket (11:55) as the
    # swept ones: the ring holds the bucket whole (3 lines), the sweep two of
    # them; the ring stands for the bucket, never on top of it.
    append_log(log, line(NOW - timedelta(seconds=30), 400))
    clock.now = NOW + timedelta(seconds=13)  # past the 2 s tail interval
    assert figures() == (3900, 3400)
    # The next sweep catches up: still once.
    clock.now = NOW + timedelta(seconds=20)
    sweep(conn, settings, clock.now)
    assert figures() == (3900, 3400)


def test_a_bucket_the_ring_holds_only_partly_keeps_the_larger_swept_part() -> None:
    """The tailer started mid-bucket: the swept row is the larger part and
    stands; the ring's part is not added on top (and a job keeps its swept
    bytes for that bucket)."""
    start = 1_760_788_800  # a 5-minute boundary
    swept = traffic.ServerSummary(
        totals=traffic.Totals(requests=3, bytes_served=9000, miss_bytes=9000),
        peak_bucket_bytes=9000,
        active_buckets=1,
        distinct_clients=0,
    )
    runner = traffic.Totals(bytes_served=9000, miss_bytes=9000)
    persisted = {
        start: traffic_stitch.PersistedBucket(
            all=traffic.Totals(requests=3, bytes_served=9000, miss_bytes=9000), runner=runner
        )
    }
    ring = {start: RingBucket(all=LiveTotals(1, 2000, 0, 2000), runner=LiveTotals(1, 2000, 0, 2000), seconds=300.0)}
    out, run, replaced = traffic_stitch.stitch_summary(swept, runner, persisted, ring, start - 3600)
    assert replaced == 0 and out == swept and run == runner
    # The ring holding MORE of it wins and replaces (never adds).
    ring[start] = RingBucket(all=LiveTotals(4, 9500, 0, 9500), runner=LiveTotals(4, 9500, 0, 9500), seconds=300.0)
    out, run, replaced = traffic_stitch.stitch_summary(swept, runner, persisted, ring, start - 3600)
    assert replaced == 1
    assert out.totals.bytes_served == 9500 and out.totals.miss_bytes == 9500
    assert out.totals.requests == 3  # request counts stay swept
    assert run.bytes_served == 9500
    assert out.peak_bucket_bytes == 9500 and out.active_buckets == 1
    # A bucket before the range start is outside the range.
    out, _run, replaced = traffic_stitch.stitch_summary(swept, runner, persisted, ring, start + 300)
    assert replaced == 0 and out == swept


def test_the_swept_total_and_the_bucket_rows_come_from_one_snapshot(tmp_path: Path) -> None:
    """A sweep committing between the two reads must not be seen half."""
    settings = make_settings(tmp_path, cooldown=0)
    init_db(settings.db_path)
    reader = get_connection(settings.db_path)
    writer = get_connection(settings.db_path)
    try:
        row = (
            "INSERT INTO server_traffic (tier, bucket_at, requests, hits, misses, bypasses, errors,"
            " bytes_served, hit_bytes, miss_bytes, peak_bucket_bytes, active_buckets)"
            " VALUES ('5m', ?, 1, 0, 1, 0, 0, ?, 0, ?, ?, 1)"
        )
        writer.execute(row, ("2026-10-18T11:55:00Z", 100, 100, 100))
        writer.commit()
        with traffic.read_snapshot(reader):
            before = traffic.server_bucket_rows(reader, "5m", "2026-10-18T11:00:00Z")
            writer.execute(row, ("2026-10-18T12:00:00Z", 700, 700, 700))
            writer.commit()
            during = traffic.server_bucket_rows(reader, "5m", "2026-10-18T11:00:00Z")
        after = traffic.server_bucket_rows(reader, "5m", "2026-10-18T11:00:00Z")
        assert during == before and set(before) == {"2026-10-18T11:55:00Z"}
        assert set(after) == {"2026-10-18T11:55:00Z", "2026-10-18T12:00:00Z"}
        assert not reader.in_transaction
    finally:
        reader.close()
        writer.close()


# ---------------------------------------------------------------------------
# Fallbacks: no ring, a feed that is not ok
# ---------------------------------------------------------------------------


def test_without_a_readable_log_every_figure_stays_swept(served, tmp_path: Path) -> None:
    """The ring never read the log (rotated away): the swept figures stand
    alone, the job keeps its swept window speed."""
    settings, client, _clock, conn = served
    log = tmp_path / "event.log"
    write_log(log, line(NOW - timedelta(minutes=3), 6000))
    sweep(conn, settings, NOW - timedelta(seconds=30))
    runner_identity._extend_period(conn, RUNNER, NOW - timedelta(minutes=4))
    conn.commit()
    running_job(conn, NOW - timedelta(minutes=10))
    log.unlink()

    body = throughput(client, "24h")
    assert body["summary"]["bytes_served"] == 6000
    (job,) = body["jobs"]
    window = body["current"]["window_seconds"]
    assert job["bytes_per_second"] == pytest.approx(6000 / window)
    assert job["bytes_since_start"] == 6000


def test_a_stale_feed_stitches_nothing(served, tmp_path: Path) -> None:
    settings, client, _clock, conn = served
    log = tmp_path / "event.log"
    write_log(log, line(NOW - timedelta(minutes=50), 6000))
    sweep(conn, settings, NOW - timedelta(minutes=45))
    append_log(log, line(NOW - timedelta(seconds=5), 9000))
    body = client.get("/v1/throughput", params={"range": "1h"}, headers=AUTH).json()
    assert body["event_feed"]["state"] == "stale"
    assert body["summary"]["bytes_served"] == 6000
    assert body["now"] is None


# ---------------------------------------------------------------------------
# The bounded per-depot store
# ---------------------------------------------------------------------------


def _fold(tier: LiveTier, at: datetime, depot: int, size: int = 100, addr: str = RUNNER) -> None:
    tier._fold(line(at, size, addr=addr, depot=depot), at, at.timestamp())


def test_the_per_depot_store_is_bounded_and_drops_the_oldest_bucket(monkeypatch) -> None:
    monkeypatch.setattr(live_tier, "MAX_DEPOT_BUCKET_ENTRIES", 3)
    tier = LiveTier(clock=lambda: NOW)
    old_bucket = NOW - timedelta(minutes=6)  # the 11:50 bucket
    _fold(tier, old_bucket, 1)
    _fold(tier, old_bucket, 2)
    _fold(tier, NOW, 3)
    assert tier.depot_bucket_entries() == 3
    # A known entry grows without a new one.
    _fold(tier, NOW, 3, size=50)
    assert tier.depot_bucket_entries() == 3
    # A fourth entry drops the oldest bucket whole and marks it incomplete.
    _fold(tier, NOW, 4)
    assert tier.depot_bucket_entries() == 2
    view = tier.stitch_view(NOW, 300, lambda _a, _t: True)
    old_epoch = int(datetime(2026, 10, 18, 11, 50, tzinfo=UTC).timestamp())
    now_epoch = int(datetime(2026, 10, 18, 12, 0, tzinfo=UTC).timestamp())
    assert old_epoch in view.depots_incomplete and old_epoch not in view.depots
    assert view.depots[now_epoch] == {RUNNER: {3: 150, 4: 100}}
    # The running bucket full on its own: it is dropped and marked too.
    for depot in (5, 6):
        _fold(tier, NOW, depot)
    assert tier.depot_bucket_entries() == 0
    view = tier.stitch_view(NOW, 300, lambda _a, _t: True)
    assert now_epoch in view.depots_incomplete and view.depots == {}


BUCKET = 1_760_788_800  # a 5-minute boundary

def _view(depots: dict[int, dict[str, dict[int, int]]], incomplete: frozenset[int] = frozenset()):
    return live_tier.StitchView(read=True, units={}, depots=depots, depots_incomplete=incomplete)


def _since(view, swept_since: int, swept_by_bucket: dict[int, int], start_epoch: int = BUCKET) -> int:
    return traffic_stitch.stitch_job_since(
        swept_since, swept_by_bucket, view, [DEPOT], False, lambda _a, _t: True, start_epoch
    )


def test_an_incomplete_bucket_keeps_the_jobs_swept_bytes() -> None:
    """What a dropped bucket shows: its swept value (late, never twice)."""
    view = _view({BUCKET: {RUNNER: {DEPOT: 5000}}})
    assert _since(view, 1000, {BUCKET: 1000}) == 5000
    dropped = _view({}, frozenset({BUCKET}))
    assert _since(dropped, 1000, {BUCKET: 1000}) == 1000
    # Marked incomplete but with leftover bytes: still the swept value.
    assert _since(_view({BUCKET: {RUNNER: {DEPOT: 5000}}}, frozenset({BUCKET})), 1000, {BUCKET: 1000}) == 1000


def test_the_jobs_so_far_never_goes_below_its_swept_value() -> None:
    """Review should-fix 1: the ring restarted after an idle gap may win a
    bucket SERVER-WIDE (more bytes of other traffic) yet hold fewer of this
    job's bytes; the job decides on its own bytes, so the swept value stands
    (the ring-loses branch), and an equal ring changes nothing."""
    fewer = _view({BUCKET: {RUNNER: {DEPOT: 300}, PC: {9999: 50_000}}})
    assert _since(fewer, 4000, {BUCKET: 1000}) == 4000
    equal = _view({BUCKET: {RUNNER: {DEPOT: 1000}}})
    assert _since(equal, 4000, {BUCKET: 1000}) == 4000
    more = _view({BUCKET: {RUNNER: {DEPOT: 1500}}})
    assert _since(more, 4000, {BUCKET: 1000}) == 4500


def test_a_bucket_before_the_jobs_start_bucket_is_not_the_jobs() -> None:
    """The start guard: unswept ring bytes on the job's depot in the bucket
    BEFORE its start bucket (an earlier run of the same game) are not counted."""
    view = _view({BUCKET - 300: {RUNNER: {DEPOT: 7000}}, BUCKET: {RUNNER: {DEPOT: 2000}}})
    assert _since(view, 0, {}) == 2000
    assert _since(view, 0, {}, start_epoch=BUCKET - 300) == 9000


def _now_view(activity: dict, seconds: float = 17.0) -> live_tier.NowView:
    current = live_tier.LiveCurrent(
        bytes_per_second=0.0, runner_bytes_per_second=0.0, window_start="x", window_end="y",
        window_seconds=int(seconds),
    )
    return live_tier.NowView(
        current=current, activity=activity, overflow_bytes=0, overflow_hit_bytes=0,
        overflow_miss_bytes=0, seconds=seconds,
    )


def test_a_first_downloads_preview_remainder_follows_the_stitched_so_far() -> None:
    from vault_api.routers import traffic as traffic_router

    job = traffic_router.JobThroughputOut(
        job_id=7, appid=APPID, name=None, started_at="2026-10-18T11:58:00Z",
        bytes_per_second=0.0, bytes_since_start=1000, exclusive_depots=0, mapped_depots=0,
        expected=True, expected_depots=1, expected_download_bytes=10_000,
        remaining_bytes=9_000, remaining_source="preview",
    )
    count = traffic_router._JobCount(
        job_id=7, depots=(DEPOT,), runner_only=True, tier=traffic.TIER_5M,
        start_bucket=zulu(datetime.fromtimestamp(BUCKET, tz=UTC)),
    )
    rows = traffic_router._StitchRows(units={}, job_since={7: 1000}, job_buckets={7: {BUCKET: 1000}})
    view = _view({BUCKET: {RUNNER: {DEPOT: 3400}, PC: {DEPOT: 9000}}})
    activity = {(RUNNER, True): live_tier.AddrActivity(bytes_served=1700, depots={DEPOT: 1700})}
    (out,) = traffic_router._jobs_with_ring(
        [job], [count], rows, view, _now_view(activity), lambda addr, _t: addr == RUNNER
    )
    # The runner's bytes only (expected depots): 1000 swept + (3400 - 1000).
    assert out.bytes_since_start == 3400
    assert out.remaining_bytes == 10_000 - 3400
    assert out.bytes_per_second == pytest.approx(1700 / 17)


def test_a_non_running_jobs_preview_eta_uses_its_ring_speed() -> None:
    """Nit 3: the ring speed is undiluted, so the window check no longer
    applies to it; without a ring speed the old window rule stands."""
    from vault_api.routers import traffic as traffic_router

    def job(job_id: int) -> traffic_router.JobThroughputOut:
        return traffic_router.JobThroughputOut(
            job_id=job_id, appid=APPID, name=None, started_at="2026-10-18T11:58:00Z",
            bytes_per_second=500.0, bytes_since_start=0, exclusive_depots=1,
            remaining_bytes=9_000, remaining_source="preview",
        )

    live = traffic_router.LiveCurrentOut(
        bytes_per_second=900.0, runner_bytes_per_second=800.0, window_start="x",
        window_end="y", window_seconds=17,
    )
    first, second = traffic_router._with_eta([job(1), job(2)], live, None, frozenset({1, 2}))
    assert (first.eta_bytes_per_second, first.eta_speed_source) == (800.0, "live")
    assert (second.eta_bytes_per_second, second.eta_speed_source) == (500.0, "live")
    _first, second = traffic_router._with_eta([job(1), job(2)], live, None)
    assert second.eta_bytes_per_second is None and second.eta_speed_source is None


def test_a_reset_clears_the_per_depot_store(tmp_path: Path) -> None:
    """``_clear_cells`` on a path change and on an idle-gap restart drops the
    depot store and the incomplete marks with the cells."""
    tier = LiveTier(clock=lambda: NOW)
    tier._path = str(tmp_path / "old.log")
    tier._cursor = 0
    _fold(tier, NOW, 1)
    tier._bucket_depots_incomplete.add(BUCKET)
    tier._tail(str(tmp_path / "new.log"), NOW, NOW.timestamp())  # another path (missing file)
    assert tier.depot_bucket_entries() == 0 and not tier._bucket_depots_incomplete

    log = tmp_path / "event.log"
    write_log(log)
    tier = LiveTier(clock=lambda: NOW)
    tier.touch(NOW)
    tier.refresh(str(log), NOW)  # the first tail
    later = NOW + timedelta(seconds=120)  # past the 60 s watcher window: a gap
    _fold(tier, later, 2)
    tier._bucket_depots_incomplete.add(int(later.timestamp()) // 300 * 300)
    assert tier.depot_bucket_entries() == 1
    tier.touch(later)
    tier.refresh(str(log), later)
    assert tier.depot_bucket_entries() == 0 and not tier._bucket_depots_incomplete


def test_an_all_range_with_no_swept_row_yet_still_stitches(served, tmp_path: Path) -> None:
    """Nit 4: ``range=all`` before any traffic row (``range_start`` null)
    sets no lower bound for the stitch."""
    settings, client, _clock, conn = served
    log = tmp_path / "event.log"
    write_log(log)
    sweep(conn, settings, NOW - timedelta(minutes=2))
    append_log(log, line(NOW - timedelta(seconds=40), 2500, addr=PC, status="HIT"))
    body = throughput(client, "all")
    assert body["range_start"] is None
    assert body["summary"]["bytes_served"] == 2500
    assert body["summary"]["hit_bytes"] == 2500


def test_depot_bytes_of_the_overflow_address_mark_the_bucket_incomplete() -> None:
    tier = LiveTier(clock=lambda: NOW)
    for n in range(live_tier.MAX_ADDRS_PER_CELL):
        _fold(tier, NOW, 7, addr=f"10.0.1.{n}")
    assert tier.depot_bucket_entries() == live_tier.MAX_ADDRS_PER_CELL
    _fold(tier, NOW, 7, addr="10.0.2.1")  # the 65th address: overflow
    view = tier.stitch_view(NOW, 300, lambda _a, _t: False)
    assert int(datetime(2026, 10, 18, 12, 0, tzinfo=UTC).timestamp()) in view.depots_incomplete
    assert tier.depot_bucket_entries() == 0


def test_the_per_depot_store_is_pruned_with_the_ring() -> None:
    tier = LiveTier(clock=lambda: NOW)
    _fold(tier, NOW - timedelta(minutes=14), 1)
    assert tier.depot_bucket_entries() == 1
    tier._prune((NOW + timedelta(minutes=5)).timestamp())
    assert tier.depot_bucket_entries() == 0


# ---------------------------------------------------------------------------
# Expected depots (a first download): the runner only
# ---------------------------------------------------------------------------


def test_expected_depot_bytes_count_only_the_runner() -> None:
    by_addr = {RUNNER: {DEPOT: 700, 9: 50}, PC: {DEPOT: 300}}
    is_runner = lambda addr, _t: addr == RUNNER  # noqa: E731
    assert traffic_stitch.ring_depot_bytes(by_addr, [DEPOT], True, is_runner, "x") == 700
    assert traffic_stitch.ring_depot_bytes(by_addr, [DEPOT], False, is_runner, "x") == 1000
