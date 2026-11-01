"""NITS-API-2: two operator decisions of 2026-11-01 on the live traffic tier.

LOADING-COUNT-1 (Weg A): the "N devices loading" count (``now.active_clients``
on ``GET /v1/throughput``, ``active_clients`` on ``GET /v1/throughput/live``)
counts clients like the ``loading_sources`` rows group them, so one PC loading
over two addresses counts once.

LIVE-DIVISOR-1 (Weg A): a ring bucket's rate divides by the seconds of it the
ring has read (``live_tier.covered_seconds``), so a bucket the tailer started
in (vault-api restarted mid-bucket, more log than it backfills) does not read
too low.

The third decision of the package (NOT-OWNED-UPDATE-1) is tested next to the
fixture it uses, in ``test_tool_names_1_oracle_names.py``.

Synthetic event lines (10.0.0.x), an injected clock, the real sweep; no
threads, no network.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_event_sweep import append_log, event_line, make_settings, write_log
from tests.test_graph_recent_1 import AUTH, NOW, PC, RUNNER, iso, series, throughput
from tests.test_now_loading_1 import (  # noqa: F401 - ``served``/``setup`` are fixtures
    chunk,
    live,
    served,
    setup,
)
from vault_api import agent_reports, client_names, live_tier
from vault_api.db import get_connection, init_db
from vault_api.live_tier import LiveTier
from vault_api.main import create_app

OTHER = "10.0.0.6"
THIRD = "10.0.0.7"


def epoch(moment: datetime) -> float:
    return moment.timestamp()


# ---------------------------------------------------------------------------
# LOADING-COUNT-1: the device count counts clients like the rows
# ---------------------------------------------------------------------------


def test_one_agent_loading_over_two_addresses_counts_once(setup) -> None:
    """The prod case: "2 devices loading" above one row. An agent with two
    addresses, an agentless address and the runner: two devices, two client
    rows, on both endpoints."""
    _settings, client, _clock, conn, log = setup
    agent_reports.store_report(conn, "pc-one", [440], 10, local_addrs=[PC, OTHER])
    client_names.set_name(conn, client_names.KEY_TYPE_IP, THIRD, "Laptop")
    append_log(
        log,
        chunk(3, 3000, addr=PC),
        chunk(2, 1000, addr=OTHER),
        chunk(2, 2000, addr=THIRD),
        chunk(1, 5000, addr=RUNNER, status="MISS"),
    )
    now = throughput(client, "1h")["now"]
    rows = now["loading_sources"]
    client_rows = [r for r in rows if r["kind"] == "client"]
    assert [r["key"] for r in client_rows] == ["agent:pc-one", f"addr:{THIRD}"]
    assert client_rows[0]["addresses"] == [PC, OTHER]
    # Three client addresses, two devices: the count equals the rows.
    assert now["active_clients"] == 2 == len(client_rows)
    assert now["runner_active"] is True
    body = live(client)
    assert body["active_clients"] == 2
    assert body["loading_sources"] == rows


def test_an_agent_alone_on_two_addresses_is_one_device(setup) -> None:
    _settings, client, _clock, conn, log = setup
    agent_reports.store_report(conn, "pc-one", [440], 10, local_addrs=[PC, OTHER])
    append_log(log, chunk(3, 3000, addr=PC), chunk(2, 1000, addr=OTHER))
    assert throughput(client, "1h")["now"]["active_clients"] == 1
    assert live(client)["active_clients"] == 1


def test_nothing_loading_counts_zero_devices(setup) -> None:
    _settings, client, _clock, _conn, log = setup
    append_log(log, chunk(1, 5000, addr=RUNNER, status="MISS"))
    now = throughput(client, "1h")["now"]
    assert now["active_clients"] == 0 and now["runner_active"] is True
    assert live(client)["active_clients"] == 0


def test_the_live_count_groups_by_agent_without_a_fresh_feed(tmp_path: Path) -> None:
    """``loading_sources`` is null unless the feed is ok, but the live count
    still counts devices, not addresses (it is read from the same rows)."""
    settings = make_settings(tmp_path)
    init_db(settings.db_path)
    app = create_app(settings)
    app.state.live_tier = LiveTier(clock=lambda: NOW)
    client = TestClient(app)
    conn = get_connection(settings.db_path)
    try:
        agent_reports.store_report(conn, "pc-one", [440], 10, local_addrs=[PC, OTHER])
    finally:
        conn.close()
    write_log(
        tmp_path / "event.log",
        chunk(3, 3000, addr=PC),
        chunk(2, 1000, addr=OTHER),
    )
    body = live(client)
    assert body["event_feed"]["state"] == "never-swept"
    assert body["loading_sources"] is None
    assert body["active_clients"] == 1


# ---------------------------------------------------------------------------
# LIVE-DIVISOR-1: a partly covered bucket divides by its covered seconds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "length", "now", "data_from", "expected"),
    [
        (0, 5, 10.0, None, 5.0),  # a finished bucket: its full length
        (0, 5, 2.0, None, 2.0),  # the running bucket: the seconds elapsed
        (0, 5, 0.0, None, 1.0),  # ... at least one second
        (0, 60, 50.0, 20.0, 30.0),  # running, the tailer started inside it
        (0, 15, 100.0, 12.0, 3.0),  # finished, the tailer started inside it
        (0, 15, 100.0, 15.0, 15.0),  # ended before the start: full length
        (0, 15, 100.0, 40.0, 15.0),  # long before the start: full length
        (30, 15, 100.0, 12.0, 15.0),  # started before the bucket: full length
        (0, 5, 4.0, 3.5, 1.0),  # just started: at least one second
        (10, 5, 8.0, None, 5.0),  # a future bucket keeps its length
    ],
)
def test_covered_seconds(start, length, now, data_from, expected) -> None:
    assert live_tier.covered_seconds(start, length, now, data_from) == pytest.approx(expected)


def _padding(at: datetime) -> str:
    """A line before the backfill window: the tailer starts inside it."""
    return event_line(time=iso(at), addr=PC, cache_status="HIT", bytes_sent="1")


def _start_mid_file(log: Path, clock, old: datetime, *lines: str) -> LiveTier:
    """A fresh tailer whose backfill starts inside the line before ``lines``
    (more log than it backfills, the restart case), armed and tailed once."""
    write_log(log, _padding(old), *lines)
    backfill = sum(len(text) + 1 for text in lines) + 10
    tier = LiveTier(clock=clock, max_backfill_bytes=backfill)
    tier.touch(clock())
    assert tier.refresh(str(log), clock()) == len(lines)
    return tier


def test_a_tailer_started_mid_file_starts_at_its_first_line(tmp_path: Path) -> None:
    first = NOW - timedelta(seconds=50)
    tier = _start_mid_file(
        tmp_path / "event.log",
        lambda: NOW,
        NOW - timedelta(seconds=300),
        event_line(time=iso(first), addr=PC, cache_status="HIT", bytes_sent="3000"),
    )
    assert tier.data_from() == epoch(first)


def test_a_tailer_that_read_the_whole_file_knows_no_start(tmp_path: Path) -> None:
    """From the file's first byte the ring holds every line not yet swept:
    no start inside the ring, every bucket keeps the old divisor."""
    log = tmp_path / "event.log"
    write_log(log, event_line(time=iso(NOW - timedelta(seconds=3)), addr=PC, bytes_sent="10"))
    tier = LiveTier(clock=lambda: NOW)
    tier.touch(NOW)
    assert tier.refresh(str(log), NOW) == 1
    assert tier.data_from() is None


def test_an_older_line_than_the_ring_still_marks_the_start(tmp_path: Path) -> None:
    """A line older than the ring is not folded but proves the tailer read
    from there: the ring is complete since then, so no bucket is shortened."""
    old = NOW - timedelta(seconds=live_tier.RING_SECONDS + 60)
    log = tmp_path / "event.log"
    lines = (
        event_line(time=iso(old), addr=PC, bytes_sent="10"),
        event_line(time=iso(NOW - timedelta(seconds=3)), addr=PC, bytes_sent="10"),
    )
    write_log(log, _padding(old - timedelta(seconds=5)), *lines)
    tier = LiveTier(clock=lambda: NOW, max_backfill_bytes=sum(len(t) + 1 for t in lines) + 10)
    tier.touch(NOW)
    assert tier.refresh(str(log), NOW) == 1  # the old line is not folded
    assert tier.data_from() == epoch(old)


def test_the_live_endpoint_divides_a_partly_covered_bucket_by_its_covered_seconds(
    setup,
) -> None:
    """vault-api restarted at 11:59:17 with more log than it backfills: the
    5 s cell 11:59:15 holds 3 s of lines, the running cell 12:00:05 2 s."""
    _settings, client, clock, _conn, log = setup
    first = NOW - timedelta(seconds=50)  # 11:59:17
    lines = (
        event_line(time=iso(first), addr=PC, cache_status="HIT", bytes_sent="3000"),
        event_line(time=iso(NOW - timedelta(seconds=1)), addr=PC, cache_status="HIT", bytes_sent="6000"),
    )
    client.app.state.live_tier = _start_mid_file(log, clock, NOW - timedelta(seconds=300), *lines)
    points = live(client)["points"]
    by_bytes = {p["bytes_served"]: p for p in points if p["bytes_served"]}
    assert set(by_bytes) == {3000, 6000}
    partly = by_bytes[3000]
    assert partly["bucket_seconds"] == 5
    # 11:59:15 .. 11:59:20, read from 11:59:17: 3 s, not 5.
    assert partly["bytes_per_second"] == pytest.approx(3000 / 3)
    # The running cell 12:00:05 .. now (12:00:07): 2 s, as before.
    assert by_bytes[6000]["bytes_per_second"] == pytest.approx(6000 / 2)


def test_a_bucket_before_the_start_keeps_its_full_length(setup) -> None:
    """Only the bucket the start falls in is shortened: a later finished
    bucket keeps its full length."""
    _settings, client, clock, _conn, log = setup
    lines = (
        event_line(time=iso(NOW - timedelta(seconds=50)), addr=PC, cache_status="HIT", bytes_sent="3000"),
        event_line(time=iso(NOW - timedelta(seconds=36)), addr=PC, cache_status="HIT", bytes_sent="5000"),
    )
    client.app.state.live_tier = _start_mid_file(log, clock, NOW - timedelta(seconds=300), *lines)
    points = live(client)["points"]
    later = next(p for p in points if p["bytes_served"] == 5000)
    assert later["bytes_per_second"] == pytest.approx(5000 / 5)


def test_the_series_divides_the_bucket_the_tailer_started_in_by_its_covered_seconds(
    setup,
) -> None:
    """The persisted ranges' running ring minute (12:00:00 .. 12:00:07) read
    from 12:00:03: 4 s, not the 7 s elapsed (RUNNER-SPLIT-1's divisor)."""
    _settings, client, clock, _conn, log = setup
    lines = (
        event_line(time=iso(NOW - timedelta(seconds=4)), addr=PC, cache_status="HIT", bytes_sent="3000"),
        event_line(time=iso(NOW - timedelta(seconds=2)), addr=PC, cache_status="HIT", bytes_sent="1000"),
    )
    client.app.state.live_tier = _start_mid_file(log, clock, NOW - timedelta(seconds=300), *lines)
    body = series(client, "1h")
    newest = body["points"][-1]
    assert newest["source"] == "live" and newest["bytes_served"] == 4000
    assert newest["bytes_per_second"] == pytest.approx(4000 / 4)
