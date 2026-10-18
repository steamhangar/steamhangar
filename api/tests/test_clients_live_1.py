"""WP CLIENTS-LIVE-1: the Clients page's "loading now" from the live tier.

``GET /v1/clients/overview`` polls the in-memory live tier like
``GET /v1/throughput/live`` and carries ``live_now`` per entry plus a ``live``
block. Synthetic event lines, an injected clock for the ring, no threads.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from tests.test_event_sweep import event_line, make_settings, write_log
from tests.test_graph_live import NOW, Clock, iso, never_runner, tier_at
from vault_api import agent_reports, event_sweep, live_tier, mapping, runner_identity
from vault_api.db import get_connection, init_db
from vault_api.live_tier import LiveTier
from vault_api.main import create_app

UTC = timezone.utc
AUTH = {"X-Api-Key": TEST_API_KEY}


def chunk(ago: float, size: int, *, addr: str, depot: str = "100", status: str = "HIT", now: datetime = NOW) -> str:
    return event_line(
        time=iso(now - timedelta(seconds=ago)),
        addr=addr,
        cache_status=status,
        depot=depot,
        bytes_sent=str(size),
    )


# ---------------------------------------------------------------------------
# The ring, per address
# ---------------------------------------------------------------------------


def test_addr_activity_is_the_current_speed_window_per_address(tmp_path: Path) -> None:
    log = tmp_path / "event.log"
    write_log(
        log,
        chunk(2, 1000, addr="10.0.0.5", depot="100"),
        chunk(3, 500, addr="10.0.0.5", depot="200"),
        chunk(14, 200, addr="10.0.0.5", depot="100"),  # still in the 15 s + running cell
        chunk(200, 7000, addr="10.0.0.5", depot="100"),  # before the window
        chunk(250, 900, addr="10.0.0.6", depot="300"),  # paused: not listed
        chunk(1, 300, addr="10.0.0.9", depot="100"),  # the runner
        event_line(time=iso(NOW - timedelta(seconds=1)), addr="10.0.0.7", depot="100", bytes_sent="0"),
    )
    tier, clock = tier_at()
    tier.touch(clock())
    tier.refresh(str(log), clock())
    activity, seconds = tier.addr_activity(clock(), lambda addr, _iso: addr == "10.0.0.9")
    # 15 s + the 2 s already elapsed in the running cell, like `current`.
    assert seconds == pytest.approx(17)
    pc = activity[("10.0.0.5", False)]
    assert pc.bytes_served == 1700
    assert pc.depots == {100: 1200, 200: 500}
    assert ("10.0.0.6", False) not in activity
    assert ("10.0.0.7", False) not in activity  # a request without served bytes
    assert ("10.0.0.9", False) not in activity
    assert activity[("10.0.0.9", True)].bytes_served == 300


def test_depots_per_address_and_cell_are_capped(tmp_path: Path) -> None:
    log = tmp_path / "event.log"
    cap = live_tier.MAX_DEPOTS_PER_ADDR_CELL
    write_log(log, *[chunk(1, 10, addr="10.0.0.5", depot=str(1000 + i)) for i in range(cap + 5)])
    tier, clock = tier_at()
    tier.touch(clock())
    tier.refresh(str(log), clock())
    activity, _seconds = tier.addr_activity(clock(), never_runner)
    entry = activity[("10.0.0.5", False)]
    assert len(entry.depots) == cap
    # Bytes on the dropped depots still count; they are just not linked.
    assert entry.bytes_served == 10 * (cap + 5)


def test_the_overflow_entry_keeps_no_depots(tmp_path: Path) -> None:
    log = tmp_path / "event.log"
    many = live_tier.MAX_ADDRS_PER_CELL + 3
    write_log(log, *[chunk(1, 10, addr=f"10.0.{i // 250}.{i % 250 + 1}", depot="100") for i in range(many)])
    tier, clock = tier_at()
    tier.touch(clock())
    tier.refresh(str(log), clock())
    stored = {addr for by_addr in tier._depots.values() for addr in by_addr}  # noqa: SLF001
    assert live_tier.OVERFLOW_ADDR not in stored
    assert len(stored) == live_tier.MAX_ADDRS_PER_CELL


def test_depots_are_pruned_after_the_current_window(tmp_path: Path) -> None:
    log = tmp_path / "event.log"
    write_log(log, chunk(2, 1000, addr="10.0.0.5"))
    tier, clock = tier_at(tail_interval=0.0)
    tier.touch(clock())
    tier.refresh(str(log), clock())
    assert tier._depots != {}  # noqa: SLF001
    clock.advance(live_tier.CURRENT_SECONDS + 2 * live_tier.CELL_SECONDS)
    tier.touch(clock())
    tier.refresh(str(log), clock())
    # The bound is the point: depots live ~20 s, the counters 15 min.
    assert tier._depots == {}  # noqa: SLF001
    assert tier.cell_count() > 0
    activity, _seconds = tier.addr_activity(clock(), never_runner)
    assert activity == {}


# ---------------------------------------------------------------------------
# GET /v1/clients/overview
# ---------------------------------------------------------------------------


@pytest.fixture
def served(tmp_path: Path):
    """No sweep ever ran (the dev setup: scheduler off): ``loading_now`` is
    null, ``live_now`` still answers from the ring."""
    settings = make_settings(tmp_path, cooldown=0)
    init_db(settings.db_path)
    conn = get_connection(settings.db_path)
    mapping.upsert_mapping(conn, depotid=100, appid=440, name=None)
    mapping.upsert_mapping(conn, depotid=200, appid=570, name=None)
    mapping.upsert_mapping(conn, depotid=300, appid=440, name=None)
    mapping.upsert_mapping(conn, depotid=300, appid=570, name=None)
    conn.execute("UPDATE apps SET name = 'Game Four Forty' WHERE appid = 440")
    agent_reports.store_report(conn, "pc-one", [440], 10, source_addr="10.0.0.5")
    agent_reports.store_report(conn, "pc-two", [570], 10, source_addr="10.0.0.6")
    conn.commit()
    clock = Clock()
    app = create_app(settings)
    app.state.live_tier = LiveTier(clock=clock)
    try:
        yield settings, TestClient(app), clock, conn
    finally:
        conn.close()


def by_key(body: dict) -> dict[str, dict]:
    return {c["key"]: c for c in body["clients"]}


def test_live_now_comes_from_the_ring_per_client_without_a_sweep(served, tmp_path: Path) -> None:
    _settings, client, _clock, conn = served
    write_log(
        tmp_path / "event.log",
        chunk(2, 3000, addr="10.0.0.5", depot="100"),
        chunk(3, 400, addr="10.0.0.5", depot="300"),  # shared depot: not linked
        chunk(200, 900, addr="10.0.0.6", depot="200"),  # 200 s ago: loading, no speed now
        chunk(1, 1700, addr="10.0.0.9", depot="200", status="MISS"),  # the runner
    )
    runner_identity._extend_period(conn, "10.0.0.9", NOW - timedelta(minutes=1))  # noqa: SLF001
    conn.commit()
    body = client.get("/v1/clients/overview", headers=AUTH).json()
    assert body["event_feed"]["state"] == "never-swept"
    live = body["live"]
    assert live == {"live_since": live["live_since"], "current_window_seconds": 17}
    assert live["live_since"] is not None
    clients = by_key(body)

    one = clients["agent:pc-one"]
    assert one["loading_now"] is None  # the swept window has nothing yet
    now = one["live_now"]
    assert now["loading"] is True
    assert now["bytes_per_second"] == pytest.approx(3400 / 17)
    assert now["apps"] == [
        {"appid": 440, "name": "Game Four Forty", "bytes_per_second": pytest.approx(3000 / 17)}
    ]
    assert now["unattributed_bytes_per_second"] == pytest.approx(400 / 17)

    # pc-two's last chunk is 200 s old: one window, so not loading, no speed,
    # no game (it stays in recent_apps once swept).
    two = clients["agent:pc-two"]["live_now"]
    assert two == {"loading": False, "bytes_per_second": 0.0, "apps": [], "unattributed_bytes_per_second": 0.0}

    runner = clients["runner"]["live_now"]
    assert runner["loading"] is True
    assert runner["bytes_per_second"] == pytest.approx(1700 / 17)
    assert [a["appid"] for a in runner["apps"]] == [570]


def test_an_idle_client_and_the_overview_arms_the_watcher(served, tmp_path: Path) -> None:
    _settings, client, clock, _conn = served
    write_log(tmp_path / "event.log", chunk(2, 3000, addr="10.0.0.5"))
    tier: LiveTier = client.app.state.live_tier
    assert tier.watcher_active(clock()) is False
    body = client.get("/v1/clients/overview", headers=AUTH).json()
    assert tier.watcher_active(clock()) is True
    idle = by_key(body)["agent:pc-two"]["live_now"]
    assert idle == {"loading": False, "bytes_per_second": 0.0, "apps": [], "unattributed_bytes_per_second": 0.0}
    # Five minutes later (still polled), pc-one has stopped loading too.
    for _ in range(16):
        clock.advance(20)
        body = client.get("/v1/clients/overview", headers=AUTH).json()
    assert by_key(body)["agent:pc-one"]["live_now"]["loading"] is False


def test_a_pause_is_not_loading_loading_speed_and_apps_share_one_window(served, tmp_path: Path) -> None:
    """Review blocker: bytes 2 min ago, none in the last 20 s -> not loading,
    speed 0, no game (never "<Game> · Loading into the cache · 0 MB/s")."""
    _settings, client, clock, _conn = served
    write_log(tmp_path / "event.log", chunk(120, 50_000, addr="10.0.0.5", depot="100"))
    body = client.get("/v1/clients/overview", headers=AUTH).json()
    now = by_key(body)["agent:pc-one"]["live_now"]
    assert now == {"loading": False, "bytes_per_second": 0.0, "apps": [], "unattributed_bytes_per_second": 0.0}
    # A new chunk inside the window (appended) is loading, with speed and game.
    write_log(
        tmp_path / "event.log",
        chunk(120, 50_000, addr="10.0.0.5", depot="100"),
        chunk(4, 50_000, addr="10.0.0.5", depot="100"),
    )
    clock.advance(live_tier.TAIL_INTERVAL_SECONDS + 1)
    body = client.get("/v1/clients/overview", headers=AUTH).json()
    now = by_key(body)["agent:pc-one"]["live_now"]
    assert now["loading"] is True and now["bytes_per_second"] > 0
    assert [a["appid"] for a in now["apps"]] == [440]


def test_a_disabled_feed_has_no_live_block(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, log_path="")
    init_db(settings.db_path)
    conn = get_connection(settings.db_path)
    try:
        agent_reports.store_report(conn, "pc-one", [440], 10, source_addr="10.0.0.5")
    finally:
        conn.close()
    app = create_app(settings)
    body = TestClient(app).get("/v1/clients/overview", headers=AUTH).json()
    assert body["live"] is None
    (pc,) = body["clients"]
    assert pc["live_now"] is None and pc["loading_now"] is None
    assert app.state.live_tier.watcher_active(datetime.now(UTC)) is False


# ---------------------------------------------------------------------------
# A ring that just started (vault-api restart, idle gap)
# ---------------------------------------------------------------------------


def test_a_fresh_ring_does_not_carry_a_stale_swept_loading_over(tmp_path: Path) -> None:
    """A sweep saw pc-one load a minute ago, then truncated the log: the
    fresh ring has nothing in the speed window, so ``live_now`` is idle while
    the swept ``loading_now`` still says loading (no swept fallback: it
    would bring back "loading at 0 MB/s")."""
    settings = make_settings(tmp_path, cooldown=0)
    now = datetime.now(UTC)
    init_db(settings.db_path)
    conn = get_connection(settings.db_path)
    try:
        mapping.upsert_mapping(conn, depotid=100, appid=440, name=None)
        agent_reports.store_report(conn, "pc-one", [440], 10, source_addr="10.0.0.5")
        write_log(tmp_path / "event.log", chunk(60, 6000, addr="10.0.0.5", now=now))
        event_sweep.sweep_once(conn, settings, now)
    finally:
        conn.close()
    write_log(tmp_path / "event.log")
    app = create_app(settings)
    app.state.live_tier = LiveTier(clock=lambda: now)
    body = TestClient(app).get("/v1/clients/overview", headers=AUTH).json()
    assert body["event_feed"]["state"] == "ok"
    pc = by_key(body)["agent:pc-one"]
    assert pc["loading_now"]["loading"] is True
    assert pc["live_now"]["loading"] is False
    assert pc["live_now"]["apps"] == []


def test_a_fresh_ring_that_sees_the_client_answers_from_the_ring(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, cooldown=0)
    now = datetime.now(UTC)
    init_db(settings.db_path)
    conn = get_connection(settings.db_path)
    try:
        mapping.upsert_mapping(conn, depotid=100, appid=440, name=None)
        agent_reports.store_report(conn, "pc-one", [440], 10, source_addr="10.0.0.5")
        write_log(tmp_path / "event.log", chunk(60, 6000, addr="10.0.0.5", now=now))
        event_sweep.sweep_once(conn, settings, now)
    finally:
        conn.close()
    write_log(tmp_path / "event.log", chunk(2, 1700, addr="10.0.0.5", now=now))
    app = create_app(settings)
    app.state.live_tier = LiveTier(clock=lambda: now)
    body = TestClient(app).get("/v1/clients/overview", headers=AUTH).json()
    pc = by_key(body)["agent:pc-one"]
    seconds = body["live"]["current_window_seconds"]
    assert pc["live_now"]["loading"] is True
    assert pc["live_now"]["bytes_per_second"] == pytest.approx(1700 / seconds, rel=0.1)
    assert [a["appid"] for a in pc["live_now"]["apps"]] == [440]
    assert pc["live_now"] != pc["loading_now"]
