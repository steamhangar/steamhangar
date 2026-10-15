"""WP NOT-OWNED-1: ``not_owned`` is its own job outcome, and apps in that
state are left out of every automatic prefill source.

1. the worker ends a 0/0 summary ``not_owned`` (job and app), not ``error``;
2. the scheduler drops such an app from both target sources and from the
   completing set;
3. the miss trigger skips it without using the per-sweep cap;
4. ``POST /v1/prefill/cached`` leaves it out;
5. an app that ended ``done`` / ``error`` is untouched by all of the above;
6. 250820 (SteamVR) is on the fixed tool list.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from tests.test_api_fix_4_tool_apps import (  # noqa: F401  (fixtures)
    AUTH,
    _job_appids,
    _seed_cache_bytes,
    _seed_mapping,
    conn,
)
from tests.test_event_sweep import event_line, write_log
from tests.test_event_sweep import make_settings as make_sweep_settings
from tests.test_event_sweep import moment
from tests.test_scheduler import insert_report, local, utc_iso
from vault_api import event_sweep, jobs, tool_apps
from vault_api.mapping import upsert_mapping
from vault_api.scheduler import compute_targets


def test_status_is_a_terminal_free_text_value() -> None:
    assert jobs.STATUS_NOT_OWNED == "not_owned"
    assert jobs.STATUS_NOT_OWNED in jobs.TERMINAL_STATUSES
    assert jobs.STATUS_NOT_OWNED not in jobs.ACTIVE_STATUSES


def test_steamvr_is_a_seeded_tool_app() -> None:
    assert tool_apps.is_tool_app(250820)
    assert "250820" in tool_apps.reject_detail(250820)
    assert "SteamVR" in tool_apps.reject_detail(250820)


def test_not_owned_appids_reads_only_that_status(conn: sqlite3.Connection) -> None:
    _finish_not_owned_job(conn, 10, jobs.utcnow_iso())
    jobs.set_app_status(conn, 20, jobs.STATUS_ERROR)
    jobs.set_app_status(conn, 30, jobs.STATUS_DONE)
    assert jobs.not_owned_appids(conn) == {10}


def test_the_scheduler_drops_a_not_owned_app_and_keeps_the_rest(
    conn: sqlite3.Connection,
) -> None:
    """MUTATION TARGET: drop the not-owned filter in compute_targets."""
    insert_report(conn, "pc", [440, 730, 999], utc_iso(9))
    _finish_not_owned_job(conn, 999, jobs.utcnow_iso())
    jobs.set_app_status(conn, 730, jobs.STATUS_ERROR)  # a real failure stays a target

    result = compute_targets(conn, local(10), stale_after_days=7)

    assert result.appids == [440, 730]
    assert result.skipped_not_owned_appids == (999,)


def test_the_miss_trigger_skips_a_not_owned_app_without_using_the_cap(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    settings = make_sweep_settings(tmp_path, cap=1)
    upsert_mapping(conn, depotid=1001, appid=999, name=None)
    upsert_mapping(conn, depotid=70403, appid=440, name=None)
    _finish_not_owned_job(conn, 999, jobs.utcnow_iso())
    write_log(
        Path(settings.event_log_path),
        event_line(depot="1001"),
        event_line(depot="70403"),
    )

    outcome = event_sweep.sweep_once(conn, settings, moment())

    assert outcome.enqueued == (440,)
    assert outcome.dropped_by_cap == ()
    assert [a for a in _job_appids(conn) if a != 999] == [440]
    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE appid = 999 AND status = 'queued'").fetchone()[0] == 0


def test_cached_prefill_leaves_a_not_owned_app_out(client) -> None:
    _seed_mapping(client, 1001, 999)
    _seed_cache_bytes(client, 1001)
    _seed_mapping(client, 441, 440)
    _seed_cache_bytes(client, 441)
    from vault_api.db import get_connection

    db = get_connection(client.app.state.settings.db_path)
    try:
        _finish_not_owned_job(db, 999, jobs.utcnow_iso())
    finally:
        db.close()

    response = client.post("/v1/prefill/cached", headers=AUTH)

    assert response.status_code == 202
    assert [entry["appid"] for entry in response.json()] == [440]


def _finish_not_owned_job(conn: sqlite3.Connection, appid: int, finished_at: str) -> None:
    jobs.set_app_status(conn, appid, jobs.STATUS_NOT_OWNED)
    conn.execute(
        "INSERT INTO jobs (appid, type, status, created_at, finished_at) "
        "VALUES (?, 'prefill', ?, ?, ?)",
        (appid, jobs.STATUS_NOT_OWNED, finished_at, finished_at),
    )
    conn.commit()


def test_not_owned_is_retried_automatically_after_seven_days(
    conn: sqlite3.Connection,
) -> None:
    from datetime import datetime, timedelta, timezone

    from vault_api import config

    assert config.NOT_OWNED_RECHECK_DAYS == 7
    now = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
    fmt = jobs.TIMESTAMP_FORMAT
    _finish_not_owned_job(conn, 1, (now - timedelta(days=6, hours=23)).strftime(fmt))
    _finish_not_owned_job(conn, 2, (now - timedelta(days=7, seconds=1)).strftime(fmt))

    assert jobs.not_owned_appids(conn, now) == {1}


def test_explicit_prefill_clears_not_owned_and_requeues(client) -> None:
    from vault_api.db import get_connection

    path = client.app.state.settings.db_path
    db = get_connection(path)
    try:
        _finish_not_owned_job(db, 777, jobs.utcnow_iso())
        assert 777 in jobs.not_owned_appids(db)
    finally:
        db.close()

    response = client.post("/v1/prefill", json={"appids": [777]}, headers=AUTH)

    assert response.status_code == 202
    db = get_connection(path)
    try:
        assert 777 not in jobs.not_owned_appids(db)
        assert db.execute("SELECT status FROM apps WHERE appid = 777").fetchone()[0] == "idle"
        assert db.execute(
            "SELECT status FROM jobs WHERE appid = 777 AND status = 'queued'"
        ).fetchone() is not None
    finally:
        db.close()


def test_not_owned_without_a_job_row_counts_as_expired(conn: sqlite3.Connection) -> None:
    """History pruned (DELETE /v1/history): no timestamp left, so recheck."""
    jobs.set_app_status(conn, 5, jobs.STATUS_NOT_OWNED)

    assert jobs.not_owned_appids(conn) == set()
