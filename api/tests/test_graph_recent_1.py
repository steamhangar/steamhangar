"""WP GRAPH-RECENT-1 + LOADING-NOW-1: the persisted Downloads ranges read the
live tier for the minutes since the last event sweep, and "Loading now" uses
the "Current speed" window.

Synthetic event lines, an injected clock, the real sweep; no threads, no
network.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from tests.test_event_sweep import append_log, event_line, make_settings, write_log
from vault_api import event_sweep, live_tier, runner_identity
from vault_api.db import get_connection, init_db
from vault_api.live_tier import LiveTier, LiveTotals
from vault_api.main import create_app

UTC = timezone.utc
AUTH = {"X-Api-Key": TEST_API_KEY}
#: 12:00:07 -- 7 s into the running minute.
NOW = datetime(2026, 10, 18, 12, 0, 7, tzinfo=UTC)
RUNNER = "10.0.0.9"
PC = "10.0.0.5"


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def zulu(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def minute_of(moment: datetime) -> str:
    return zulu(moment.replace(second=0, microsecond=0))


def line(at: datetime, bytes_sent: int, *, addr: str = PC, status: str = "HIT") -> str:
    return event_line(time=iso(at), addr=addr, cache_status=status, bytes_sent=str(bytes_sent))


class Clock:
    def __init__(self, start: datetime = NOW) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


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
    """The real sweep, then the 1-minute tier dated far back so the 1 h / 6 h
    ranges read it (right after the v29 upgrade they fall back to 5 minutes)."""
    event_sweep.sweep_once(conn, settings, at)
    conn.execute("UPDATE event_sweep_state SET traffic_1m_since = ?", (zulu(at - timedelta(days=1)),))
    conn.commit()


def series(client: TestClient, range_name: str) -> dict:
    body = client.get("/v1/throughput/series", params={"range": range_name}, headers=AUTH).json()
    assert body["range"] == range_name
    return body


def throughput(client: TestClient, range_name: str) -> dict:
    return client.get("/v1/throughput", params={"range": range_name}, headers=AUTH).json()


def point_at(body: dict, t: str) -> dict:
    return next(p for p in body["points"] if p["t"] == t)


# ---------------------------------------------------------------------------
# First question of the package: does the next sweep fill the 1 h view?
# ---------------------------------------------------------------------------


def test_the_next_sweep_fills_the_1h_view_from_the_persisted_tier(served, tmp_path: Path) -> None:
    """Yes: lag, not a bug. Proven with the log rotated away after the sweep,
    so the ring cannot supply the minutes and only the swept tier answers."""
    settings, client, _clock, conn = served
    log = tmp_path / "event.log"
    write_log(log, line(NOW - timedelta(minutes=3), 4000), line(NOW - timedelta(seconds=70), 6000))
    sweep(conn, settings, NOW - timedelta(seconds=30))
    write_log(log)  # the file is gone (rotated); nothing for the ring to read

    body = series(client, "1h")
    assert body["tier"] == "1m" and len(body["points"]) == 60
    assert point_at(body, minute_of(NOW - timedelta(minutes=3)))["bytes_served"] == 4000
    assert point_at(body, minute_of(NOW - timedelta(seconds=70)))["bytes_served"] == 6000
    assert {p["source"] for p in body["points"]} == {"persisted"}
    assert sum(p["bytes_served"] for p in body["points"]) == 10000


# ---------------------------------------------------------------------------
# The graph: minutes since the last sweep
# ---------------------------------------------------------------------------


def test_the_1h_series_shows_the_minutes_since_the_last_sweep(served, tmp_path: Path) -> None:
    settings, client, _clock, conn = served
    log = tmp_path / "event.log"
    write_log(log, line(NOW - timedelta(minutes=6), 500))
    sweep(conn, settings, NOW - timedelta(minutes=4))
    # Not swept yet: 90 s ago and in the running minute.
    append_log(log, line(NOW - timedelta(seconds=90), 2000), line(NOW - timedelta(seconds=3), 1000))

    body = series(client, "1h")
    assert body["tier"] == "1m"
    recent = point_at(body, minute_of(NOW - timedelta(seconds=90)))
    assert recent["bytes_served"] == 2000 and recent["source"] == "live"
    assert recent["bytes_per_second"] == pytest.approx(2000 / 60)
    assert recent["peak_bytes_per_second"] == pytest.approx(2000 / 60)
    newest = body["points"][-1]
    assert newest["t"] == minute_of(NOW) and newest["bytes_served"] == 1000
    assert newest["source"] == "live"
    # The swept minute is there once: nothing counted twice.
    assert point_at(body, minute_of(NOW - timedelta(minutes=6)))["bytes_served"] == 500
    assert sum(p["bytes_served"] for p in body["points"]) == 3500


def test_the_6h_and_24h_series_stitch_too_but_7d_stays_swept(served, tmp_path: Path) -> None:
    settings, client, _clock, conn = served
    log = tmp_path / "event.log"
    write_log(log, line(NOW - timedelta(minutes=20), 700))
    sweep(conn, settings, NOW - timedelta(minutes=4))
    append_log(log, line(NOW - timedelta(seconds=30), 3000))

    six = series(client, "6h")
    assert six["tier"] == "1m" and six["points"][-1]["t"] == minute_of(NOW)
    assert point_at(six, minute_of(NOW - timedelta(seconds=30)))["bytes_served"] == 3000
    assert sum(p["bytes_served"] for p in six["points"]) == 3700

    day = series(client, "24h")
    assert day["tier"] == "5m"
    last = day["points"][-1]
    assert last["t"] == "2026-10-18T12:00:00Z"  # the running 5-minute bucket
    # 11:59:37 belongs to the 11:55 bucket, not the running one.
    bucket = point_at(day, "2026-10-18T11:55:00Z")
    assert bucket["bytes_served"] == 3000 and bucket["source"] == "live"
    assert bucket["bytes_per_second"] == pytest.approx(3000 / 300)
    assert sum(p["bytes_served"] for p in day["points"]) == 3700

    week = series(client, "7d")
    assert {p["source"] for p in week["points"]} == {"persisted"}
    assert sum(p["bytes_served"] for p in week["points"]) == 700


def test_runner_bytes_in_live_buckets_are_split_like_swept_ones(served, tmp_path: Path) -> None:
    settings, client, _clock, conn = served
    log = tmp_path / "event.log"
    write_log(log)
    sweep(conn, settings, NOW - timedelta(minutes=4))
    runner_identity._extend_period(conn, RUNNER, NOW - timedelta(minutes=2))
    conn.commit()
    append_log(
        log,
        line(NOW - timedelta(seconds=4), 5000, addr=RUNNER, status="MISS"),
        # The operator's verify case: the runner reads chunks the cache has.
        line(NOW - timedelta(seconds=3), 7000, addr=RUNNER, status="HIT"),
        line(NOW - timedelta(seconds=2), 800, addr=PC, status="HIT"),
    )
    newest = series(client, "1h")["points"][-1]
    assert newest["source"] == "live"
    assert newest["bytes_served"] == 12800 and newest["runner_bytes"] == 12000
    # Clients-only figures, as in the swept tier (module docstring): the
    # runner's HIT and MISS bytes and its requests are all left out.
    assert (newest["hit_bytes"], newest["miss_bytes"], newest["requests"]) == (800, 0, 1)


def test_a_bucket_the_ring_holds_only_partly_keeps_the_larger_swept_part(tmp_path: Path) -> None:
    """The ring started mid-file (backfill), so it holds only the tail of an
    older minute the sweep has whole: the swept row wins, the ring part is
    not added on top."""
    log = tmp_path / "event.log"
    old = NOW - timedelta(minutes=8)
    write_log(log, *[line(old.replace(second=s), 10) for s in range(0, 50)])
    tier = LiveTier(clock=lambda: NOW, max_backfill_bytes=1500, max_read_bytes=1500)
    tier.touch(NOW)
    folded = tier.refresh(str(log), NOW)
    assert 0 < folded < 50
    buckets = tier.buckets(NOW, 60, lambda _a, _i: False)
    bucket = buckets[int(old.replace(second=0).timestamp())]
    assert bucket.all.bytes_served == 10 * folded
    swept = LiveTotals(requests=50, bytes_served=500)
    assert live_tier.prefer_ring(bucket.all, swept) is False
    # With no swept row yet (the minute is after the last sweep) the ring's
    # part is the better answer.
    assert live_tier.prefer_ring(bucket.all, None) is True


def test_prefer_ring_rules() -> None:
    ring = LiveTotals(requests=2, bytes_served=100)
    more = LiveTotals(requests=3, bytes_served=150)
    assert live_tier.prefer_ring(ring, more) is False  # the sweep holds more
    assert live_tier.prefer_ring(more, ring) is True  # the ring holds more
    assert live_tier.prefer_ring(ring, None) is True  # nothing swept yet
    assert live_tier.prefer_ring(ring, ring) is True  # equal: the fresher source
    assert live_tier.prefer_ring(LiveTotals(), None) is False  # nothing anywhere: swept zero
    assert live_tier.prefer_ring(LiveTotals(), LiveTotals()) is False
    # Requests break a byte tie (errors only, no bytes served).
    assert live_tier.prefer_ring(LiveTotals(requests=1), LiveTotals()) is True
    assert live_tier.prefer_ring(LiveTotals(requests=1), LiveTotals(requests=2)) is False


def test_a_tailer_started_on_a_rotated_file_does_not_blank_the_rotated_minutes(tmp_path: Path) -> None:
    """Regression (found in this package): a fresh tailer reading a small,
    freshly rotated file from byte 0 used to claim the whole ring as covered,
    so the minutes before the rotation read zero although the sweep had them."""
    log = tmp_path / "event.log"
    write_log(log, line(NOW - timedelta(seconds=10), 30))  # the new file: one line
    tier = LiveTier(clock=lambda: NOW)
    tier.touch(NOW)
    tier.refresh(str(log), NOW)
    rotated = int((NOW - timedelta(minutes=6)).replace(second=0).timestamp())

    def persisted(start: int, end: int) -> dict[int, live_tier.PersistedMinute]:
        return {rotated: live_tier.PersistedMinute(LiveTotals(4, 4000, 4000, 0), LiveTotals(), 4000)}

    points = tier.stitch(NOW, persisted, lambda _a, _i: False)
    row = next(p for p in points if p.t == zulu(datetime.fromtimestamp(rotated, tz=UTC)))
    assert row.source == "persisted" and row.all_totals.bytes_served == 4000
    assert sum(p.all_totals.bytes_served for p in points) == 4030


# ---------------------------------------------------------------------------
# Header, pill and newest point agree
# ---------------------------------------------------------------------------


def test_header_pill_and_newest_point_agree_while_loading(served, tmp_path: Path) -> None:
    settings, client, _clock, conn = served
    log = tmp_path / "event.log"
    write_log(log)
    sweep(conn, settings, NOW - timedelta(minutes=4))
    append_log(log, line(NOW - timedelta(seconds=5), 3400), line(NOW - timedelta(seconds=1), 3400))

    head = throughput(client, "1h")
    now = head["now"]
    assert now["current"]["bytes_per_second"] == pytest.approx(6800 / 17)
    assert now["window_seconds"] == now["current"]["window_seconds"] == 17
    assert now["active_clients"] == 1 and now["runner_active"] is False
    # The swept figures still lag: the last sweep saw nothing.
    assert head["current"]["bytes_per_second"] == 0
    newest = series(client, "1h")["points"][-1]
    assert newest["t"] == minute_of(NOW) and newest["bytes_served"] == 6800


def test_an_old_peak_reads_idle_now_not_the_swept_window(served, tmp_path: Path) -> None:
    """Operator report 2026-10-18: the only peak was 9 min old, the graph was
    flat "now", yet the header said 10.3 MB/s and the pill "Downloading" --
    both came from the last swept 5-10 minute window."""
    settings, client, _clock, conn = served
    log = tmp_path / "event.log"
    peak_at = NOW - timedelta(minutes=9)
    write_log(log, *[line(peak_at + timedelta(seconds=s), 2_000_000, addr=RUNNER) for s in range(10)])
    runner_identity._extend_period(conn, RUNNER, peak_at - timedelta(seconds=30))
    conn.commit()
    sweep(conn, settings, NOW - timedelta(minutes=2))

    head = throughput(client, "1h")
    assert head["current"]["bytes_per_second"] > 0  # the lagging swept window
    assert head["current"]["runner_bytes_per_second"] > 0
    now = head["now"]
    assert now["current"]["bytes_per_second"] == 0
    assert now["current"]["runner_bytes_per_second"] == 0
    assert now["active_clients"] == 0 and now["runner_active"] is False
    body = series(client, "1h")
    assert body["points"][-1]["bytes_served"] == 0
    assert point_at(body, minute_of(peak_at))["bytes_served"] == 20_000_000


def test_the_runner_alone_sets_runner_active_but_no_client_is_loading(served, tmp_path: Path) -> None:
    settings, client, _clock, conn = served
    log = tmp_path / "event.log"
    write_log(log)
    sweep(conn, settings, NOW - timedelta(minutes=4))
    runner_identity._extend_period(conn, RUNNER, NOW - timedelta(minutes=1))
    conn.commit()
    append_log(log, line(NOW - timedelta(seconds=2), 9000, addr=RUNNER, status="MISS"))
    now = throughput(client, "24h")["now"]
    assert now["runner_active"] is True and now["active_clients"] == 0
    assert now["current"]["runner_bytes_per_second"] == pytest.approx(9000 / 17)


# ---------------------------------------------------------------------------
# LOADING-NOW-1: one window with the Clients page
# ---------------------------------------------------------------------------


def test_loading_now_uses_the_clients_page_window_on_every_endpoint(served, tmp_path: Path) -> None:
    settings, client, clock, conn = served
    log = tmp_path / "event.log"
    # Both machines were swept once, so the Clients page lists them.
    write_log(
        log,
        line(NOW - timedelta(minutes=6), 10, addr=PC),
        line(NOW - timedelta(minutes=6), 10, addr="10.0.0.6"),
    )
    sweep(conn, settings, NOW - timedelta(minutes=4))
    append_log(
        log,
        line(NOW - timedelta(seconds=3), 1000, addr=PC),
        line(NOW - timedelta(seconds=60), 1000, addr="10.0.0.6"),  # paused a minute ago
    )
    live = client.get("/v1/throughput/live", headers=AUTH).json()
    head = throughput(client, "1h")
    overview = client.get("/v1/clients/overview", headers=AUTH).json()
    loading = sorted(
        c["addresses"][0] for c in overview["clients"] if c["live_now"] and c["live_now"]["loading"]
    )
    assert loading == [PC]
    assert live["active_clients"] == head["now"]["active_clients"] == len(loading) == 1
    assert (
        live["active_window_seconds"]
        == head["now"]["window_seconds"]
        == overview["live"]["current_window_seconds"]
        == 17
    )
    # The swept count keeps its 15-minute window for older clients: both
    # machines, one of which has not loaded anything for a minute.
    assert head["active_clients_now"] == 2


def test_now_is_null_unless_the_feed_is_ok(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings.db_path)
    app = create_app(settings)
    app.state.live_tier = LiveTier(clock=lambda: NOW)
    client = TestClient(app)
    write_log(tmp_path / "event.log", line(NOW - timedelta(seconds=2), 1000))
    head = throughput(client, "1h")
    assert head["event_feed"]["state"] == "never-swept"
    assert head["now"] is None and head["current"] is None
    body = series(client, "1h")
    assert {p["source"] for p in body["points"]} <= {"persisted"}
    assert sum(p["bytes_served"] for p in body["points"]) == 0


def test_a_disabled_feed_has_no_now_and_never_opens_the_log(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path, log_path="")
    init_db(settings.db_path)
    client = TestClient(create_app(settings))

    def boom(*_a, **_k):
        raise AssertionError("a disabled feed must not open a log")

    monkeypatch.setattr(event_sweep, "open_event_log", boom)
    head = throughput(client, "6h")
    assert head["event_feed"]["state"] == "disabled" and head["now"] is None
    assert series(client, "6h")["points"] == []


# ---------------------------------------------------------------------------
# The persisted ranges' 30 s poll keeps the tailer reading incrementally
# ---------------------------------------------------------------------------


def test_a_30s_poll_keeps_the_watcher_armed(tmp_path: Path) -> None:
    log = tmp_path / "event.log"
    write_log(log, line(NOW - timedelta(seconds=2), 100))
    clock = Clock()
    tier = LiveTier(clock=clock)
    tier.touch(clock())
    assert tier.refresh(str(log), clock()) == 1
    since = tier.covered_from()
    for _ in range(4):
        clock.advance(31)  # the web's 30 s view poll, plus a slow answer
        append_log(log, line(clock() - timedelta(seconds=1), 100))
        tier.touch(clock())
        # Only the new line: an incremental read, not a restart.
        assert tier.refresh(str(log), clock()) == 1
    assert tier.covered_from() == since
    assert live_tier.WATCHER_WINDOW_SECONDS >= 2 * 30

