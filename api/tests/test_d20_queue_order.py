"""WP D20 QUEUE-ORDER-1a: queue order column, claim order, move endpoint."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from vault_api import jobs
from vault_api.config import Settings
from vault_api.db import SCHEMA_VERSION, get_connection, init_db
from vault_api.main import create_app

AUTH = {"X-Api-Key": TEST_API_KEY}


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    path = str(tmp_path / "vault.db")
    init_db(path)
    return path


@pytest.fixture
def conn(db_path: str):
    connection = get_connection(db_path)
    yield connection
    connection.close()


@pytest.fixture
def client(db_path: str, tmp_path: Path) -> TestClient:
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=db_path,
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
    )
    return TestClient(create_app(settings))


def queue(conn: sqlite3.Connection, *appids: int) -> list[int]:
    return [int(jobs.enqueue_prefill(conn, a)[0]["id"]) for a in appids]


def claim_order(conn: sqlite3.Connection) -> list[int]:
    """Drain the queue through the real claim and return the job ids."""
    out = []
    while (claimed := jobs.claim_next_job(conn)) is not None:
        out.append(int(claimed["id"]))
    return out


def move(client: TestClient, job_id: int, position: int):
    return client.post(
        f"/v1/jobs/{job_id}/move", json={"position": position}, headers=AUTH
    )


def keyed_rows(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE order_key IS NOT NULL"
    ).fetchone()[0]


# --- migration -------------------------------------------------------------


def _rebuild_jobs_without_order_key(c: sqlite3.Connection) -> None:
    """Make ``jobs`` look like a v30 table, without ``ALTER ... DROP COLUMN``.

    DROP COLUMN rewrites the stored CREATE TABLE text and older SQLite builds
    (about 3.45, as on CI) reject that for this table's commented DDL, so the
    old table is rebuilt by hand from ``PRAGMA table_info`` and the rows are
    copied across. Indexes go with the dropped table; ``init_db`` recreates
    them.
    """
    cols = [r for r in c.execute("PRAGMA table_info(jobs)") if r["name"] != "order_key"]
    defs = []
    for r in cols:
        text = f'{r["name"]} {r["type"]}'
        if r["pk"]:
            text += " PRIMARY KEY AUTOINCREMENT"
        if r["notnull"]:
            text += " NOT NULL"
        if r["dflt_value"] is not None:
            text += f' DEFAULT {r["dflt_value"]}'
        defs.append(text)
    names = ", ".join(r["name"] for r in cols)
    c.execute(f"CREATE TABLE jobs_v30 ({', '.join(defs)})")
    c.execute(f"INSERT INTO jobs_v30 ({names}) SELECT {names} FROM jobs")
    c.execute("DROP TABLE jobs")
    c.execute("ALTER TABLE jobs_v30 RENAME TO jobs")


def test_v30_database_with_jobs_gains_order_key_idempotently(tmp_path) -> None:
    path = str(tmp_path / "old.db")
    init_db(path)
    c = get_connection(path)
    try:
        queue(c, 10, 20, 30)
        _rebuild_jobs_without_order_key(c)
        c.execute("UPDATE schema_version SET version = 30")
        c.commit()
        assert "order_key" not in {
            r["name"] for r in c.execute("PRAGMA table_info(jobs)")
        }
    finally:
        c.close()

    init_db(path)
    init_db(path)  # per-column guard: no 'duplicate column name'

    c = get_connection(path)
    try:
        (version,) = c.execute("SELECT version FROM schema_version").fetchone()
        types = {r["name"]: r["type"] for r in c.execute("PRAGMA table_info(jobs)")}
        rows = c.execute("SELECT appid, order_key FROM jobs ORDER BY id").fetchall()
        assert version == SCHEMA_VERSION >= 31
        assert types["order_key"] == "INTEGER"
        assert [(r["appid"], r["order_key"]) for r in rows] == [
            (10, None),
            (20, None),
            (30, None),
        ]
        # Existing rows keep FIFO behaviour after the upgrade.
        assert [int(jobs.get_job(c, i)["appid"]) for i in claim_order(c)] == [
            10,
            20,
            30,
        ]
    finally:
        c.close()


# --- claim order -----------------------------------------------------------


def test_unmoved_queue_stays_fifo_and_writes_no_key(conn) -> None:
    ids = queue(conn, 1, 2, 3)
    assert jobs.queue_positions(conn) == {ids[0]: 0, ids[1]: 1, ids[2]: 2}
    assert keyed_rows(conn) == 0
    assert claim_order(conn) == ids


def test_move_to_front_middle_and_back_changes_claim_order(conn) -> None:
    a, b, c_, d = queue(conn, 1, 2, 3, 4)
    assert jobs.move_queued_job(conn, d, 0).outcome == jobs.CONTROL_IMMEDIATE
    assert list(jobs.queue_positions(conn)) == [d, a, b, c_]
    jobs.move_queued_job(conn, a, 2)
    assert list(jobs.queue_positions(conn)) == [d, b, a, c_]
    jobs.move_queued_job(conn, d, 99)  # past the end = last
    assert list(jobs.queue_positions(conn)) == [b, a, c_, d]
    assert claim_order(conn) == [b, a, c_, d]


def test_moving_to_the_current_place_writes_nothing(conn) -> None:
    a, b = queue(conn, 1, 2)
    jobs.move_queued_job(conn, b, 1)
    jobs.move_queued_job(conn, a, 0)
    assert keyed_rows(conn) == 0


def test_jobs_enqueued_after_a_move_queue_behind_everything(conn) -> None:
    a, b, c_ = queue(conn, 1, 2, 3)
    jobs.move_queued_job(conn, c_, 0)
    jobs.move_queued_job(conn, b, 0)
    (d,) = queue(conn, 4)
    assert claim_order(conn) == [b, c_, a, d]


def test_repeated_moves_keep_a_strict_total_order(conn) -> None:
    ids = queue(conn, 1, 2, 3, 4, 5)
    jobs.move_queued_job(conn, ids[4], 0)
    jobs.move_queued_job(conn, ids[0], 4)
    jobs.move_queued_job(conn, ids[2], 1)
    expected = list(jobs.queue_positions(conn))
    assert sorted(expected) == sorted(ids)
    assert expected == [ids[4], ids[2], ids[1], ids[3], ids[0]]
    assert claim_order(conn) == expected


def test_resumed_job_still_runs_before_jobs_enqueued_while_paused(conn) -> None:
    a, b = queue(conn, 1, 2)
    jobs.request_pause(conn, a)  # queued -> paused
    (c_,) = queue(conn, 3)
    jobs.resume_job(conn, a)
    assert claim_order(conn) == [a, b, c_]


# --- move rules ------------------------------------------------------------


def test_running_paused_finished_jobs_cannot_be_moved(conn) -> None:
    run, paused, other, done = queue(conn, 1, 2, 3, 4)
    jobs.request_pause(conn, paused)
    jobs.cancel_job(conn, done)
    claimed = jobs.claim_next_job(conn)  # `run` is the lowest queued id
    assert int(claimed["id"]) == run
    for job_id in (run, paused, done):
        result = jobs.move_queued_job(conn, job_id, 0)
        assert result.outcome == jobs.CONTROL_CONFLICT, job_id
    assert jobs.move_queued_job(conn, 9999, 0).outcome == jobs.CONTROL_UNKNOWN
    assert jobs.queue_positions(conn) == {other: 0}


# --- HTTP ------------------------------------------------------------------


def test_move_endpoint_reorders_and_reports_positions(client, db_path) -> None:
    c = get_connection(db_path)
    try:
        a, b, c_ = queue(c, 1, 2, 3)
    finally:
        c.close()

    reply = move(client, c_, 0)
    assert reply.status_code == 200
    body = reply.json()
    assert (
        body["job_id"],
        body["status"],
        body["outcome"],
        body["queue_position"],
    ) == (c_, "queued", "immediate", 0)

    listed = {
        j["id"]: j["queue_position"]
        for j in client.get("/v1/jobs", headers=AUTH).json()
    }
    assert listed == {c_: 0, a: 1, b: 2}
    assert client.get(f"/v1/jobs/{a}", headers=AUTH).json()["queue_position"] == 1


def test_move_endpoint_errors(client, db_path) -> None:
    c = get_connection(db_path)
    try:
        a, b = queue(c, 1, 2)
        jobs.claim_next_job(c)  # a is running
    finally:
        c.close()

    assert move(client, 9999, 0).status_code == 404
    assert move(client, a, 0).status_code == 409
    assert move(client, b, -1).status_code == 422
    assert client.post(f"/v1/jobs/{b}/move", json={}, headers=AUTH).status_code == 422
    assert client.post(f"/v1/jobs/{b}/move", json={"position": 0}).status_code in (
        401,
        403,
    )
    # A non-queued job has no queue position.
    assert client.get(f"/v1/jobs/{a}", headers=AUTH).json()["queue_position"] is None
