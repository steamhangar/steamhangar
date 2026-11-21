"""CLOCK-SEAM-1 (audit 2026-11-20, follow-up of CI-CLOCK-1): one request clock.

``GET /v1/clients``, ``GET /v1/jobs?range=``, ``DELETE /v1/history`` and
``POST /v1/updates/check`` take their ``now`` from ``deps.request_now``, the
live tier's clock (UTC wall time in production). Each test injects
``LiveTier(clock=...)`` far from the real date (2001 and 2100), so a handler
that reads the wall clock again answers differently and fails here.
Synthetic ids only.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from tests.test_event_sweep import make_settings
from vault_api import agent_reports, update_check
from vault_api.config import Settings
from vault_api.db import get_connection, init_db
from vault_api.jobs import to_utc_iso
from vault_api.live_tier import LiveTier
from vault_api.main import create_app

AUTH = {"X-Api-Key": TEST_API_KEY}
UTC = timezone.utc
PAST = datetime(2001, 6, 15, 12, 0, tzinfo=UTC)
FUTURE = datetime(2100, 6, 15, 12, 0, tzinfo=UTC)


def client_at(settings: Settings, now: datetime) -> TestClient:
    # No `with`: no lifespan, so no scheduler thread runs concurrently.
    app = create_app(settings)
    app.state.live_tier = LiveTier(clock=lambda: now)
    return TestClient(app)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    settings = make_settings(tmp_path, log_path="")
    init_db(settings.db_path)
    return settings


@pytest.mark.parametrize("now", [PAST, FUTURE])
def test_the_jobs_history_range_reads_the_request_clock(settings: Settings, now: datetime) -> None:
    rows = [
        (1, "done", now - timedelta(days=40)),
        (2, "error", now - timedelta(days=3)),
        (3, "done", now - timedelta(hours=2)),
    ]
    conn = get_connection(settings.db_path)
    try:
        for job_id, status, finished in rows:
            conn.execute(
                "INSERT INTO jobs (id, appid, type, status, created_at, finished_at) "
                "VALUES (?, 440, 'prefill', ?, ?, ?)",
                (job_id, status, to_utc_iso(finished), to_utc_iso(finished)),
            )
        conn.commit()
    finally:
        conn.close()
    client = client_at(settings, now)

    def ids(query: str) -> list[int]:
        response = client.get(f"/v1/jobs?{query}", headers=AUTH)
        assert response.status_code == 200, response.text
        return [job["id"] for job in response.json()]

    assert ids("range=24h") == [3]
    assert ids("range=7d") == [3, 2]
    assert ids("range=30d") == [3, 2]
    assert ids("range=all") == [3, 2, 1]


@pytest.mark.parametrize("now", [PAST, FUTURE])
def test_the_history_cutoff_reads_the_request_clock(settings: Settings, now: datetime) -> None:
    response = client_at(settings, now).delete(
        "/v1/history", params={"scope": "older-than-30d"}, headers=AUTH
    )
    assert response.status_code == 200, response.text
    assert response.json()["cutoff"] == to_utc_iso(now - timedelta(days=30))


def test_client_presence_reads_the_request_clock(settings: Settings) -> None:
    """The report is stamped with the real clock: online seen from 2001,
    offline seen from 2100, whatever today's date is."""
    conn = get_connection(settings.db_path)
    try:
        agent_reports.store_report(conn, "pc-one", [440], 10, report_interval_seconds=600)
    finally:
        conn.close()
    for now, presence in ((PAST, "online"), (FUTURE, "offline")):
        response = client_at(settings, now).get("/v1/clients", headers=AUTH)
        assert response.status_code == 200, response.text
        [client] = response.json()
        assert client["presence"] == presence, now


class _RecordingRunner:
    """Stands in for the update-check runner and records the ``now`` it gets."""

    def __init__(self) -> None:
        self.nows: list[datetime] = []

    def start(self, conn, settings, *, trigger, select_appids, now, require_interval) -> str:
        self.nows.append(now)
        return update_check.STARTED


@pytest.mark.parametrize("now", [PAST, FUTURE])
def test_a_manual_update_check_starts_at_the_request_clock(settings: Settings, now: datetime) -> None:
    client = client_at(settings, now)
    runner = _RecordingRunner()
    client.app.state.update_checker = runner
    response = client.post("/v1/updates/check", headers=AUTH)
    assert response.status_code == 202, response.text
    assert runner.nows == [now]
