"""WP NOT-OWNED-2: a content package that ends unowned must not disturb its
card or the game.

1. the not_owned fact is derived from the newest prefill job, so it survives
   an app keeping a cached-equivalent ``apps.status`` and keeps its 7 days;
2. the worker keeps the status of an app with cache bytes / last_prefill_at
   (the job still ends not_owned), and still sets not_owned for one without;
3. ``POST /v1/prefill/verify`` and ``/incomplete`` skip such apps and name
   them in the ``X-Skipped-Not-Owned`` response header;
4. the games list and detail carry an additive ``not_owned`` flag;
5. the scheduler and the other consumers drop a kept-status app too.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from tests import stub_prefill
from tests.test_api_fix_4_tool_apps import (  # noqa: F401  (fixtures)
    AUTH,
    _seed_cache_bytes,
    _seed_mapping,
    conn,
)
from tests.test_worker import (  # noqa: F401  (fixtures)
    _MOJIBAKE_TABLE_UNOWNED,
    bindir,
    cache_root,
    enqueue,
    make_settings,
    wait_for_job,
)
from vault_api import deletion, jobs
from vault_api.db import get_connection
from vault_api.main import create_app

HEADER = "X-Skipped-Not-Owned"


def _kept_not_owned(conn: sqlite3.Connection, appid: int, finished_at: str, status: str = "done") -> None:
    """An app that ended unowned but kept its cached status (job carries the fact)."""
    jobs.set_app_status(conn, appid, status)
    conn.execute(
        "INSERT INTO jobs (appid, type, status, created_at, finished_at) "
        "VALUES (?, 'prefill', ?, ?, ?)",
        (appid, jobs.STATUS_NOT_OWNED, finished_at, finished_at),
    )
    conn.commit()


def test_fact_survives_a_kept_status_and_expires_after_seven_days(
    conn: sqlite3.Connection,
) -> None:
    now = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
    fmt = jobs.TIMESTAMP_FORMAT
    _kept_not_owned(conn, 1, (now - timedelta(days=6, hours=23)).strftime(fmt))
    _kept_not_owned(conn, 2, (now - timedelta(days=7, seconds=1)).strftime(fmt))

    assert jobs.not_owned_appids(conn, now) == {1}


def test_a_newer_job_supersedes_the_fact(conn: sqlite3.Connection) -> None:
    _kept_not_owned(conn, 7, jobs.utcnow_iso())
    assert jobs.not_owned_appids(conn) == {7}

    jobs.enqueue_prefill(conn, 7)

    assert jobs.not_owned_appids(conn) == set()


def test_worker_keeps_status_for_an_app_with_cache_bytes(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    executable = stub_prefill.make_stub(
        bindir, cache_root=str(cache_root), summary_text=_MOJIBAKE_TABLE_UNOWNED
    )
    with TestClient(create_app(make_settings(tmp_path, cache_root, executable))) as client:
        _seed_mapping(client, 441, 440)
        _seed_cache_bytes(client, 441)

        (job_id,) = enqueue(client, 440)
        job = wait_for_job(client, job_id)

        assert job["status"] == "not_owned"
        game = client.get("/v1/games/440", headers=AUTH).json()
        assert game["status"] == "done"
        assert game["not_owned"] is True
        listed = {g["appid"]: g for g in client.get("/v1/games", headers=AUTH).json()}
        assert listed[440]["status"] == "done"
        assert listed[440]["not_owned"] is True
        db = get_connection(client.app.state.settings.db_path)
        try:
            assert 440 in jobs.not_owned_appids(db)
        finally:
            db.close()


def test_worker_keeps_done_for_a_previously_prefilled_app(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    executable = stub_prefill.make_stub(
        bindir, cache_root=str(cache_root), summary_text=_MOJIBAKE_TABLE_UNOWNED
    )
    with TestClient(create_app(make_settings(tmp_path, cache_root, executable))) as client:
        db = get_connection(client.app.state.settings.db_path)
        try:
            jobs.set_app_status(db, 440, "done", last_prefill_at="2026-10-01T00:00:00Z")
        finally:
            db.close()

        (job_id,) = enqueue(client, 440)
        assert wait_for_job(client, job_id)["status"] == "not_owned"

        game = client.get("/v1/games/440", headers=AUTH).json()
        assert game["status"] == "done"
        assert game["last_prefill_at"] == "2026-10-01T00:00:00Z"
        assert game["not_owned"] is True


def test_worker_still_flips_an_app_without_bytes(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    executable = stub_prefill.make_stub(
        bindir, cache_root=str(cache_root), summary_text=_MOJIBAKE_TABLE_UNOWNED
    )
    with TestClient(create_app(make_settings(tmp_path, cache_root, executable))) as client:
        (job_id,) = enqueue(client, 440)
        assert wait_for_job(client, job_id)["status"] == "not_owned"

        game = client.get("/v1/games/440", headers=AUTH).json()
        assert game["status"] == "not_owned"
        assert game["not_owned"] is True


def test_verify_skips_a_not_owned_app_and_names_it(client: TestClient) -> None:
    db = get_connection(client.app.state.settings.db_path)
    try:
        _kept_not_owned(db, 2506210, jobs.utcnow_iso())
    finally:
        db.close()

    response = client.post(
        "/v1/prefill/verify", json={"appids": [553850, 2506210]}, headers=AUTH
    )

    assert response.status_code == 202
    assert [r["appid"] for r in response.json()] == [553850]
    assert response.headers[HEADER] == "2506210"


def test_verify_without_skips_has_no_header_and_an_expired_fact_is_verified(
    client: TestClient,
) -> None:
    db = get_connection(client.app.state.settings.db_path)
    try:
        old = (datetime.now(timezone.utc) - timedelta(days=8)).strftime(jobs.TIMESTAMP_FORMAT)
        _kept_not_owned(db, 2506220, old)
    finally:
        db.close()

    response = client.post("/v1/prefill/verify", json={"appids": [2506220]}, headers=AUTH)

    assert response.status_code == 202
    assert [r["appid"] for r in response.json()] == [2506220]
    assert HEADER not in response.headers


def test_incomplete_skips_a_not_owned_app(client: TestClient, monkeypatch) -> None:
    from vault_api import cache_completeness

    db = get_connection(client.app.state.settings.db_path)
    try:
        _kept_not_owned(db, 11, jobs.utcnow_iso())
    finally:
        db.close()

    class _App:
        def __init__(self, appid: int) -> None:
            self.appid = appid
            self.figure = appid

    monkeypatch.setattr(
        cache_completeness, "incomplete_apps", lambda *_a, **_k: [_App(11), _App(12)]
    )
    monkeypatch.setattr(cache_completeness, "is_completable", lambda _f: True)

    response = client.post("/v1/prefill/incomplete", headers=AUTH)

    assert response.status_code == 202
    assert [r["appid"] for r in response.json()] == [12]
    assert response.headers[HEADER] == "11"


def test_scheduler_and_cached_prefill_drop_a_kept_status_app(client: TestClient) -> None:
    _seed_mapping(client, 301, 300)
    _seed_cache_bytes(client, 301)
    db = get_connection(client.app.state.settings.db_path)
    try:
        _kept_not_owned(db, 300, jobs.utcnow_iso())
    finally:
        db.close()

    response = client.post("/v1/prefill/cached", headers=AUTH)

    assert response.status_code == 202
    assert [r["appid"] for r in response.json()] == []


def test_kept_status_keeps_the_shared_depot_protection(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    """Bytes without last_prefill_at must end ``done``, not ``idle``: idle and
    never prefilled reads as "no content" and would unprotect a shared depot."""
    executable = stub_prefill.make_stub(
        bindir, cache_root=str(cache_root), summary_text=_MOJIBAKE_TABLE_UNOWNED
    )
    with TestClient(create_app(make_settings(tmp_path, cache_root, executable))) as client:
        _seed_mapping(client, 441, 440)
        _seed_cache_bytes(client, 441)
        (job_id,) = enqueue(client, 440)
        assert wait_for_job(client, job_id)["status"] == "not_owned"

        db = get_connection(client.app.state.settings.db_path)
        try:
            states = deletion.load_co_owner_states(db, [440])
            assert states == {440: True}
            plan = deletion.plan_deletion(
                [(900, 441), (900, 440)], appid=441, co_owner_states=states
            )
        finally:
            db.close()
        assert plan.remnant == []
        assert [(x.depotid, x.shared_with) for x in plan.shared] == [(900, [440])]


def test_cache_deletion_frees_a_kept_app_but_a_never_cached_one_stays(
    conn: sqlite3.Connection,
) -> None:
    now = jobs.utcnow_iso()
    _kept_not_owned(conn, 10, now, status="done")
    conn.execute("UPDATE apps SET last_prefill_at = ? WHERE appid = 10", (now,))
    conn.commit()
    jobs.set_app_status(conn, 11, jobs.STATUS_NOT_OWNED)
    conn.execute(
        "INSERT INTO jobs (appid, type, status, created_at, finished_at) "
        "VALUES (11, 'prefill', ?, ?, ?)",
        (jobs.STATUS_NOT_OWNED, now, now),
    )
    conn.commit()
    assert jobs.not_owned_appids(conn) == {10, 11}

    deletion.reset_app_after_deletion(conn, 10, jobs.STATUS_IDLE, set_needs_force=True)

    assert jobs.not_owned_appids(conn) == {11}


def test_flag_does_not_come_from_a_stale_not_owned_status(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    executable = stub_prefill.make_stub(bindir, cache_root=str(cache_root))
    with TestClient(create_app(make_settings(tmp_path, cache_root, executable))) as client:
        old = (datetime.now(timezone.utc) - timedelta(days=9)).strftime(jobs.TIMESTAMP_FORMAT)
        db = get_connection(client.app.state.settings.db_path)
        try:
            _kept_not_owned(db, 440, old, status=jobs.STATUS_NOT_OWNED)
        finally:
            db.close()

        game = client.get("/v1/games/440", headers=AUTH).json()
        assert game["status"] == "not_owned"
        assert game["not_owned"] is False
        listed = {g["appid"]: g for g in client.get("/v1/games", headers=AUTH).json()}
        assert listed[440]["not_owned"] is False
