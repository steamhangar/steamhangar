"""WP D15b2: GET /v1/clients/overview, /v1/throughput[/series] and the
GET /v1/jobs history filter.

The routers read the real clock, so the event lines are stamped relative to
``datetime.now`` and the sweep is driven with that same moment. Synthetic
addresses and ids only.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from tests.test_event_sweep import event_line, make_settings, write_log
from vault_api import agent_reports, event_sweep, jobs as jobs_queue, mapping
from vault_api.config import Settings
from vault_api.db import get_connection, init_db
from vault_api.main import create_app

AUTH = {"X-Api-Key": TEST_API_KEY}
UTC = timezone.utc


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def open_conn(settings: Settings) -> sqlite3.Connection:
    init_db(settings.db_path)
    return get_connection(settings.db_path)


def client_for(settings: Settings) -> TestClient:
    # No `with`: no lifespan, so no scheduler thread sweeping concurrently.
    return TestClient(create_app(settings))


@pytest.fixture
def feed(tmp_path: Path):
    """A swept event log: an agent PC (10.0.0.5) and an agentless one
    (10.0.0.9, the "frame" case), app 440 on its own depot 100, app 570 on
    depot 200, and depot 300 shared by both (unattributable). The miss
    trigger is off (cooldown 0) so the fixture queues no jobs of its own."""
    settings = make_settings(tmp_path, cooldown=0)
    now = datetime.now(UTC)
    conn = open_conn(settings)
    try:
        mapping.upsert_mapping(conn, depotid=100, appid=440, name=None)
        mapping.upsert_mapping(conn, depotid=200, appid=570, name=None)
        mapping.upsert_mapping(conn, depotid=300, appid=440, name=None)
        mapping.upsert_mapping(conn, depotid=300, appid=570, name=None)
        conn.execute("UPDATE apps SET name = 'Game Four Forty' WHERE appid = 440")
        conn.commit()
        agent_reports.store_report(conn, "pc-one", [440, 730], 10, source_addr="10.0.0.5")
        write_log(
            tmp_path / "event.log",
            event_line(
                time=iso(now - timedelta(minutes=2)),
                addr="10.0.0.5",
                cache_status="HIT",
                depot="100",
                bytes_sent="1000",
            ),
            event_line(
                time=iso(now - timedelta(minutes=1)),
                addr="10.0.0.9",
                cache_status="MISS",
                depot="200",
                bytes_sent="600",
            ),
            event_line(
                time=iso(now - timedelta(minutes=1)),
                addr="10.0.0.9",
                cache_status="MISS",
                depot="300",
                bytes_sent="400",
            ),
            # Three days ago: outside 24h, inside 7d.
            event_line(
                time=iso(now - timedelta(days=3)),
                addr="10.0.0.9",
                cache_status="HIT",
                depot="200",
                bytes_sent="5000",
            ),
        )
        event_sweep.sweep_once(conn, settings, now)
        yield settings, conn, now
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Fail-soft: event feed disabled
# ---------------------------------------------------------------------------


def test_with_the_feed_disabled_every_traffic_field_is_null_and_flagged(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path, log_path="")
    conn = open_conn(settings)
    try:
        agent_reports.store_report(conn, "pc-one", [440], 10, source_addr="10.0.0.5")
        jobs_queue.enqueue_prefill(conn, 440)
        jobs_queue.claim_next_job(conn)
    finally:
        conn.close()
    client = client_for(settings)

    body = client.get("/v1/throughput", headers=AUTH).json()
    assert body["event_feed"]["enabled"] is False
    assert body["event_feed"]["state"] == "disabled"
    assert body["summary"] is None
    assert body["current"] is None
    assert body["active_clients_now"] is None
    (job,) = body["jobs"]
    assert job["appid"] == 440
    assert job["bytes_per_second"] is None and job["bytes_since_start"] is None

    series = client.get("/v1/throughput/series?range=7d", headers=AUTH).json()
    assert series["event_feed"]["state"] == "disabled"
    assert series["points"] == []

    overview = client.get("/v1/clients/overview", headers=AUTH).json()
    (pc,) = overview["clients"]
    assert pc["kind"] == "agent"
    assert pc["connected"] is True  # from the agent's presence alone
    assert pc["agent_presence"] == "online"
    assert pc["cache_active"] is None
    assert pc["stats"] is None
    assert pc["loading_now"] is None
    assert pc["recent_apps"] is None
    assert pc["installed_appids"] == [440]


# ---------------------------------------------------------------------------
# GET /v1/throughput
# ---------------------------------------------------------------------------


def test_throughput_reports_current_average_peak_hit_rate_and_saved_bytes(feed) -> None:
    settings, _conn, _now = feed
    body = client_for(settings).get("/v1/throughput", headers=AUTH).json()

    assert body["event_feed"]["state"] == "ok"
    assert body["range"] == "24h" and body["tier"] == "5m"
    assert body["bucket_seconds"] == 300

    summary = body["summary"]
    assert summary["bytes_served"] == 2000  # the 3-day-old line is outside 24h
    assert summary["hit_bytes"] == 1000
    assert summary["miss_bytes"] == 1000
    assert summary["hits"] == 1 and summary["misses"] == 2
    assert summary["hit_rate"] == pytest.approx(1 / 3)
    assert summary["byte_hit_rate"] == pytest.approx(0.5)
    assert summary["active_clients"] == 2
    # The three recent lines fall into one or two 5-minute buckets.
    assert summary["peak_bytes_per_second"] in (1000 / 300, 2000 / 300)
    assert summary["average_bytes_per_second"] in (2000 / 300, 2000 / 600)

    current = body["current"]
    assert current["bytes_per_second"] == pytest.approx(2000 / current["window_seconds"])
    assert 300 <= current["window_seconds"] <= 600
    assert body["active_clients_now"] == 2
    assert body["active_window_minutes"] == 15


def test_the_7d_range_uses_hourly_rows_and_includes_older_traffic(feed) -> None:
    settings, _conn, _now = feed
    body = client_for(settings).get("/v1/throughput?range=7d", headers=AUTH).json()
    assert body["tier"] == "1h"
    assert body["summary"]["bytes_served"] == 7000
    assert body["summary"]["hit_bytes"] == 6000


def test_a_running_job_gets_the_speed_of_its_exclusive_depots(feed) -> None:
    settings, conn, now = feed
    jobs_queue.enqueue_prefill(conn, 440)
    jobs_queue.claim_next_job(conn)
    # Started before the fixture's lines, so the bucket-aligned
    # bytes_since_start cannot depend on where "now" falls in its bucket.
    conn.execute("UPDATE jobs SET started_at = ?", (stamp(now - timedelta(minutes=10)),))
    conn.commit()

    body = client_for(settings).get("/v1/throughput", headers=AUTH).json()
    (job,) = body["jobs"]
    window = body["current"]["window_seconds"]
    assert job["appid"] == 440
    assert job["name"] == "Game Four Forty"
    assert job["exclusive_depots"] == 1  # depot 300 is shared, not counted
    assert job["bytes_per_second"] == pytest.approx(1000 / window)
    assert job["bytes_since_start"] == 1000


def test_a_stale_feed_keeps_totals_but_reports_no_current_speed(feed) -> None:
    settings, conn, now = feed
    conn.execute(
        "UPDATE event_sweep_state SET last_sweep_at = ?",
        (stamp(now - timedelta(hours=1)),),
    )
    conn.commit()
    client = client_for(settings)
    body = client.get("/v1/throughput", headers=AUTH).json()
    assert body["event_feed"]["state"] == "stale"
    assert body["current"] is None
    assert body["active_clients_now"] is None
    assert body["summary"]["bytes_served"] == 2000

    overview = client.get("/v1/clients/overview", headers=AUTH).json()
    for entry in overview["clients"]:
        assert entry["loading_now"] is None
        assert entry["cache_active"] is None


def test_a_feed_that_never_swept_says_so(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    body = client_for(settings).get("/v1/throughput", headers=AUTH).json()
    assert body["event_feed"]["state"] == "never-swept"
    assert body["current"] is None
    assert body["summary"]["bytes_served"] == 0
    assert body["summary"]["average_bytes_per_second"] is None
    assert body["summary"]["hit_rate"] is None


def test_an_unknown_range_is_a_422(feed) -> None:
    settings, _conn, _now = feed
    client = client_for(settings)
    for path in ("/v1/throughput", "/v1/throughput/series", "/v1/clients/overview"):
        assert client.get(f"{path}?range=1y", headers=AUTH).status_code == 422


def test_the_new_routes_require_the_api_key(feed) -> None:
    settings, _conn, _now = feed
    client = client_for(settings)
    for path in ("/v1/throughput", "/v1/throughput/series", "/v1/clients/overview"):
        assert client.get(path).status_code == 401


# ---------------------------------------------------------------------------
# GET /v1/throughput/series
# ---------------------------------------------------------------------------


def test_the_24h_series_is_zero_filled_5_minute_buckets_ending_now(feed) -> None:
    settings, _conn, now = feed
    body = client_for(settings).get("/v1/throughput/series", headers=AUTH).json()
    points = body["points"]
    assert body["tier"] == "5m"
    assert len(points) == 288
    assert points[-1]["t"] <= stamp(now)
    assert sum(p["bytes_served"] for p in points) == 2000
    assert sum(1 for p in points if p["bytes_served"] == 0) >= 286
    busy = [p for p in points if p["bytes_served"]]
    for point in busy:
        assert point["bytes_per_second"] == pytest.approx(point["bytes_served"] / 300)
        assert point["peak_bytes_per_second"] == pytest.approx(point["bytes_served"] / 300)


@pytest.mark.parametrize(("name", "tier", "count"), [("7d", "1h", 168), ("30d", "1h", 720)])
def test_the_7d_and_30d_series_use_hourly_buckets(feed, name: str, tier: str, count: int) -> None:
    settings, _conn, _now = feed
    body = client_for(settings).get(f"/v1/throughput/series?range={name}", headers=AUTH).json()
    assert body["tier"] == tier
    assert body["bucket_seconds"] == 3600
    assert len(body["points"]) == count
    assert sum(p["bytes_served"] for p in body["points"]) == 7000


def test_the_all_series_starts_at_the_first_recorded_day(feed) -> None:
    settings, _conn, now = feed
    body = client_for(settings).get("/v1/throughput/series?range=all", headers=AUTH).json()
    assert body["tier"] == "1d"
    first_day = (now - timedelta(days=3)).strftime("%Y-%m-%dT00:00:00Z")
    assert body["range_start"] == first_day
    assert body["points"][0]["t"] == first_day
    assert len(body["points"]) == 4
    assert sum(p["bytes_served"] for p in body["points"]) == 7000


# ---------------------------------------------------------------------------
# GET /v1/clients/overview
# ---------------------------------------------------------------------------


def test_the_overview_merges_agent_pcs_with_agentless_addresses(feed) -> None:
    settings, _conn, _now = feed
    body = client_for(settings).get("/v1/clients/overview", headers=AUTH).json()
    assert body["range"] == "7d"
    by_key = {entry["key"]: entry for entry in body["clients"]}
    assert set(by_key) == {"agent:pc-one", "addr:10.0.0.9"}

    pc = by_key["agent:pc-one"]
    assert pc["kind"] == "agent"
    assert pc["addresses"] == ["10.0.0.5"]
    assert pc["connected"] is True and pc["cache_active"] is True
    assert pc["installed_appids"] == [440, 730]
    assert pc["installed_app_count"] == 2
    assert pc["stats"]["bytes_served"] == 1000
    assert pc["stats"]["hit_rate"] == 1.0
    assert [a["appid"] for a in pc["recent_apps"]] == [440]
    assert pc["recent_apps"][0]["name"] == "Game Four Forty"
    assert pc["loading_now"]["loading"] is True
    assert [a["appid"] for a in pc["loading_now"]["apps"]] == [440]

    frame = by_key["addr:10.0.0.9"]
    assert frame["kind"] == "address"
    assert frame["client_id"] is None
    assert frame["installed_appids"] is None
    assert frame["agent_presence"] is None
    assert frame["connected"] is True
    assert frame["stats"]["bytes_served"] == 6000  # 7d includes the old line
    assert [(a["appid"], a["bytes_served"]) for a in frame["recent_apps"]] == [(570, 5600)]
    assert frame["recent_unattributed_bytes"] == 400  # the shared depot
    loading = frame["loading_now"]
    window = loading["bytes_per_second"] and 1000 / loading["bytes_per_second"]
    assert [a["appid"] for a in loading["apps"]] == [570]
    assert loading["unattributed_bytes_per_second"] == pytest.approx(400 / window)


def test_the_24h_overview_counts_only_the_last_day(feed) -> None:
    settings, _conn, _now = feed
    body = client_for(settings).get("/v1/clients/overview?range=24h", headers=AUTH).json()
    frame = next(e for e in body["clients"] if e["key"] == "addr:10.0.0.9")
    assert frame["stats"]["bytes_served"] == 1000


def test_an_address_belongs_to_the_agent_that_reported_from_it_last(feed) -> None:
    settings, conn, _now = feed
    conn.execute(
        "INSERT INTO agent_reports (client_id, reported_at, appids, source_addr) "
        "VALUES ('pc-two', '2000-01-01T00:00:00Z', '[]', '10.0.0.9')"
    )
    conn.commit()
    body = client_for(settings).get("/v1/clients/overview", headers=AUTH).json()
    keys = {entry["key"]: entry for entry in body["clients"]}
    assert "addr:10.0.0.9" not in keys
    assert keys["agent:pc-two"]["addresses"] == ["10.0.0.9"]
    assert keys["agent:pc-two"]["agent_presence"] == "offline"
    assert keys["agent:pc-two"]["connected"] is True  # its address is active

    # A newer report by pc-one from the same address moves it over.
    agent_reports.store_report(conn, "pc-one", [440], 10, source_addr="10.0.0.9")
    body = client_for(settings).get("/v1/clients/overview", headers=AUTH).json()
    keys = {entry["key"]: entry for entry in body["clients"]}
    assert keys["agent:pc-one"]["addresses"] == ["10.0.0.5", "10.0.0.9"]
    assert keys["agent:pc-two"]["addresses"] == []


def test_connected_clients_come_first(feed) -> None:
    settings, conn, _now = feed
    conn.execute(
        "INSERT INTO agent_reports (client_id, reported_at, appids, source_addr) "
        "VALUES ('old-pc', '2000-01-01T00:00:00Z', '[]', NULL)"
    )
    conn.commit()
    body = client_for(settings).get("/v1/clients/overview", headers=AUTH).json()
    assert body["clients"][-1]["key"] == "agent:old-pc"
    assert body["clients"][-1]["connected"] is False


# ---------------------------------------------------------------------------
# GET /v1/jobs history filter
# ---------------------------------------------------------------------------


@pytest.fixture
def jobs_client(tmp_path: Path):
    settings = make_settings(tmp_path, log_path="")
    conn = open_conn(settings)
    now = datetime.now(UTC)
    rows = [
        (1, "done", stamp(now - timedelta(days=40)), stamp(now - timedelta(days=40))),
        (2, "error", stamp(now - timedelta(days=3)), stamp(now - timedelta(days=3))),
        (3, "done", stamp(now - timedelta(hours=2)), stamp(now - timedelta(hours=2))),
        (4, "queued", stamp(now - timedelta(days=60)), None),
    ]
    try:
        for job_id, status, created, finished in rows:
            conn.execute(
                "INSERT INTO jobs (id, appid, type, status, created_at, finished_at) "
                "VALUES (?, 440, 'prefill', ?, ?, ?)",
                (job_id, status, created, finished),
            )
        conn.commit()
    finally:
        conn.close()
    return client_for(settings), now


def ids(response) -> list[int]:
    assert response.status_code == 200, response.text
    return [job["id"] for job in response.json()]


def test_the_jobs_history_filter_keeps_recent_finished_and_all_active_jobs(jobs_client) -> None:
    client, now = jobs_client
    assert ids(client.get("/v1/jobs", headers=AUTH)) == [4, 3, 2, 1]
    assert ids(client.get("/v1/jobs?range=all", headers=AUTH)) == [4, 3, 2, 1]
    assert ids(client.get("/v1/jobs?range=24h", headers=AUTH)) == [4, 3]
    assert ids(client.get("/v1/jobs?range=7d", headers=AUTH)) == [4, 3, 2]
    assert ids(client.get("/v1/jobs?range=30d", headers=AUTH)) == [4, 3, 2]
    since = stamp(now - timedelta(days=50))
    assert ids(client.get(f"/v1/jobs?since={since}", headers=AUTH)) == [4, 3, 2, 1]
    assert ids(client.get("/v1/jobs?range=24h&limit=1", headers=AUTH)) == [4]


def test_the_jobs_history_filter_rejects_bad_input(jobs_client) -> None:
    client, _now = jobs_client
    assert client.get("/v1/jobs?range=1y", headers=AUTH).status_code == 422
    assert client.get("/v1/jobs?since=yesterday", headers=AUTH).status_code == 422
    assert (
        client.get("/v1/jobs?range=24h&since=2026-10-01T00:00:00Z", headers=AUTH).status_code
        == 422
    )
