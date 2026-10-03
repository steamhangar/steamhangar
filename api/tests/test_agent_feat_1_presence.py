"""WP AGENT-FEAT-1: agent version, report interval and online/offline.

Pinned here, one guarantee per group:

1. Schema v17 adds ``agent_reports.agent_version`` / ``report_interval_seconds``
   in place, NULL for existing rows, idempotently, with the same column types
   as a fresh database.
2. ``POST /v1/agent/installed`` accepts both fields as optional, validates the
   version with the VER-1 grammar and the interval as a strict int in
   60..86400, and an agent that sends neither works exactly as before.
3. ``agent_reports.presence``: offline once the last report is older than
   2 x interval + 5 min (online at exactly that age), 30 min assumed when the
   interval is unknown, offline when the timestamp is unreadable.
4. ``GET /v1/clients`` shows the fields from the LATEST report and computes
   presence through ``agent_reports.presence`` (structural pin), with one
   ``now`` for the whole answer.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from vault_api import agent_reports
from vault_api.db import SCHEMA_VERSION, get_connection, init_db
from vault_api.jobs import to_utc_iso

AUTH = {"X-Api-Key": TEST_API_KEY}
ENDPOINT = "/v1/agent/installed"


def post(client: TestClient, **body: object):
    payload = {"client_id": "pc", "appids": [440]}
    payload.update(body)
    return client.post(ENDPOINT, json=payload, headers=AUTH)


def clients(client: TestClient) -> dict[str, dict]:
    response = client.get("/v1/clients", headers=AUTH)
    assert response.status_code == 200, response.text
    return {row["client_id"]: row for row in response.json()}


def insert_report(
    db_path: str,
    client_id: str,
    reported_at: str,
    agent_version: object = None,
    interval: object = None,
) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO agent_reports (client_id, reported_at, appids, agent_version, "
            "report_interval_seconds) VALUES (?, ?, ?, ?, ?)",
            (client_id, reported_at, "[440]", agent_version, interval),
        )
        conn.commit()
    finally:
        conn.close()


# --- 1. schema v17 ----------------------------------------------------------------


def test_schema_version_is_17() -> None:
    assert SCHEMA_VERSION == 17


def _make_v16_agent_reports(db_path: str) -> None:
    """A database whose ``agent_reports`` has the exact pre-v17 shape (a
    hand-written table, same reason as test_db.py's ALTER tests: DROP COLUMN
    rewrites the stored CREATE TABLE text)."""
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        conn.execute("DROP TABLE agent_reports")
        conn.execute(
            """
            CREATE TABLE agent_reports (
                client_id   TEXT NOT NULL,
                reported_at TEXT NOT NULL,
                appids      TEXT NOT NULL,
                source_addr TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO agent_reports (client_id, reported_at, appids, source_addr) "
            "VALUES ('old-pc', '2026-10-01T10:00:00Z', '[440, 730]', '192.168.1.20')"
        )
        conn.execute("UPDATE schema_version SET version = 16")
        conn.commit()
    finally:
        conn.close()


def test_v16_database_gains_both_columns_in_place(tmp_path) -> None:
    db = str(tmp_path / "vault.db")
    _make_v16_agent_reports(db)

    init_db(db)
    init_db(db)  # idempotent: no 'duplicate column name'

    conn = get_connection(db)
    try:
        (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
        types = {r["name"]: r["type"] for r in conn.execute("PRAGMA table_info(agent_reports)")}
        row = conn.execute("SELECT * FROM agent_reports WHERE client_id = 'old-pc'").fetchone()
    finally:
        conn.close()

    assert version == 17
    assert types["agent_version"] == "TEXT"
    assert types["report_interval_seconds"] == "INTEGER"
    # The existing row survives untouched, with "the agent did not say".
    assert row["appids"] == "[440, 730]"
    assert row["source_addr"] == "192.168.1.20"
    assert row["agent_version"] is None
    assert row["report_interval_seconds"] is None


def test_fresh_and_upgraded_databases_agree_on_agent_reports_columns(tmp_path) -> None:
    fresh = str(tmp_path / "fresh.db")
    upgraded = str(tmp_path / "upgraded.db")
    init_db(fresh)
    _make_v16_agent_reports(upgraded)
    init_db(upgraded)

    def columns(path: str) -> list[tuple[str, str]]:
        conn = get_connection(path)
        try:
            return [(r["name"], r["type"]) for r in conn.execute("PRAGMA table_info(agent_reports)")]
        finally:
            conn.close()

    assert columns(fresh) == columns(upgraded)


def test_old_snapshot_survives_the_upgrade_as_a_client_with_unknown_version(tmp_path) -> None:
    db = str(tmp_path / "vault.db")
    _make_v16_agent_reports(db)
    from tests.test_agent_reports import make_client

    client, _ = make_client(tmp_path)
    with client:
        row = clients(client)["old-pc"]
    assert row["agent_version"] is None
    assert row["report_interval_seconds"] is None
    assert row["app_count"] == 2
    # Reported days ago with the assumed 30-minute interval: offline.
    assert row["presence"] == "offline"
    assert row["offline_after"] == "2026-10-01T11:05:00Z"


# --- 2. POST /v1/agent/installed ---------------------------------------------------


def test_report_with_both_fields_is_stored_and_shown(client: TestClient) -> None:
    response = post(client, agent_version="0.1.0-rc9", report_interval_seconds=600)
    assert response.status_code == 200, response.text
    # The response to the agent is unchanged: the new fields are input only.
    assert set(response.json()) == {"client_id", "received", "added", "removed", "first_report"}

    row = clients(client)["pc"]
    assert row["agent_version"] == "0.1.0-rc9"
    assert row["report_interval_seconds"] == 600
    assert row["presence"] == "online"


def test_old_agent_without_the_fields_is_accepted_and_reads_as_unknown(client: TestClient) -> None:
    response = client.post(ENDPOINT, json={"client_id": "pc", "appids": [440]}, headers=AUTH)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "client_id": "pc",
        "received": 1,
        "added": [440],
        "removed": [],
        "first_report": True,
    }

    row = clients(client)["pc"]
    assert row["agent_version"] is None
    assert row["report_interval_seconds"] is None
    assert row["presence"] == "online"
    last = datetime.strptime(row["last_reported_at"], "%Y-%m-%dT%H:%M:%SZ")
    offline_after = datetime.strptime(row["offline_after"], "%Y-%m-%dT%H:%M:%SZ")
    # Unknown interval: 30 minutes assumed, so 2 x 30 + 5 = 65 minutes.
    assert offline_after - last == timedelta(minutes=65)


def test_explicit_nulls_are_the_same_as_absent(client: TestClient) -> None:
    assert post(client, agent_version=None, report_interval_seconds=None).status_code == 200
    row = clients(client)["pc"]
    assert row["agent_version"] is None and row["report_interval_seconds"] is None


@pytest.mark.parametrize(
    "version",
    ["0.1.0", "0.1.0-rc8", "dev", "dev-1a2b3c4", "ci-1a2b3c4", "1+build.5", "x" + "y" * 63],
)
def test_valid_versions_are_accepted(client: TestClient, version: str) -> None:
    assert post(client, agent_version=version).status_code == 200
    assert clients(client)["pc"]["agent_version"] == version


@pytest.mark.parametrize(
    "version",
    [
        "",
        " 0.1.0",
        "0.1.0 rc8",
        "0.1.0\n",
        "-dev",
        "a/b",
        'a"b',
        "x" + "y" * 64,  # 65 characters
        "v١",  # non-ASCII digit
        1,
        1.5,
        True,
        ["0.1.0"],
    ],
)
def test_invalid_versions_are_refused(client: TestClient, version: object) -> None:
    response = post(client, agent_version=version)
    assert response.status_code == 422, response.text
    assert any(err["loc"] == ["body", "agent_version"] for err in response.json()["detail"])
    assert clients(client) == {}, "a refused report must not be stored"


@pytest.mark.parametrize("interval", [60, 61, 600, 1800, 86400])
def test_intervals_in_range_are_accepted(client: TestClient, interval: int) -> None:
    assert post(client, report_interval_seconds=interval).status_code == 200
    assert clients(client)["pc"]["report_interval_seconds"] == interval


@pytest.mark.parametrize(
    "interval", [59, 0, -600, 86401, True, 600.0, 600.5, "600", {"seconds": 600}]
)
def test_intervals_out_of_range_or_not_strict_ints_are_refused(
    client: TestClient, interval: object
) -> None:
    response = post(client, report_interval_seconds=interval)
    assert response.status_code == 422, response.text
    assert any(
        err["loc"][:2] == ["body", "report_interval_seconds"] for err in response.json()["detail"]
    )
    assert clients(client) == {}


def test_a_typo_in_a_new_field_name_is_still_refused(client: TestClient) -> None:
    """extra="forbid" stays: ``agentVersion`` is not silently ignored."""
    response = post(client, agentVersion="0.1.0")
    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "extra_forbidden"


def test_store_report_refuses_values_the_api_would_not_show(tmp_path) -> None:
    db = str(tmp_path / "vault.db")
    init_db(db)
    conn = get_connection(db)
    try:
        with pytest.raises(ValueError):
            agent_reports.store_report(conn, "pc", [440], keep=5, agent_version="bad version")
        with pytest.raises(ValueError):
            agent_reports.store_report(conn, "pc", [440], keep=5, report_interval_seconds=59)
        with pytest.raises(ValueError):
            agent_reports.store_report(conn, "pc", [440], keep=5, report_interval_seconds=True)  # type: ignore[arg-type]
        assert conn.execute("SELECT COUNT(*) FROM agent_reports").fetchone()[0] == 0
    finally:
        conn.close()


# --- 3. the presence rule -----------------------------------------------------------

LAST = "2026-10-03T12:00:00Z"
LAST_DT = datetime(2026, 10, 3, 12, 0, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("interval", "limit_seconds"),
    [
        (600, 2 * 600 + 300),  # the new 10-minute agents: 25 minutes
        (60, 2 * 60 + 300),
        (86400, 2 * 86400 + 300),
        (None, 2 * 1800 + 300),  # unknown: 30 minutes assumed, 65 minutes
    ],
)
def test_presence_boundary_is_inclusive(interval: int | None, limit_seconds: int) -> None:
    deadline = LAST_DT + timedelta(seconds=limit_seconds)
    at = agent_reports.presence(LAST, interval, deadline)
    just_after = agent_reports.presence(LAST, interval, deadline + timedelta(microseconds=1))
    one_second_after = agent_reports.presence(LAST, interval, deadline + timedelta(seconds=1))
    just_before = agent_reports.presence(LAST, interval, deadline - timedelta(seconds=1))

    assert at == ("online", to_utc_iso(deadline)), "exactly 2 x interval + 5 min old is still online"
    assert just_before[0] == "online"
    assert just_after[0] == "offline"
    assert one_second_after == ("offline", to_utc_iso(deadline))


def test_presence_of_a_fresh_report_is_online() -> None:
    assert agent_reports.presence(LAST, 600, LAST_DT) == ("online", "2026-10-03T12:25:00Z")


def test_presence_of_a_report_from_the_future_is_online() -> None:
    """A server clock that stepped back must not show a reporting PC offline."""
    assert agent_reports.presence(LAST, 600, LAST_DT - timedelta(hours=1))[0] == "online"


@pytest.mark.parametrize("bad_interval", [0, 59, 86401, -1, True])
def test_presence_treats_an_impossible_interval_as_unknown(bad_interval: int) -> None:
    """Only reachable through a hand-edited row; it must not shorten or
    stretch the window, it falls back to the 30-minute assumption."""
    assert agent_reports.presence(LAST, bad_interval, LAST_DT)[1] == "2026-10-03T13:05:00Z"


@pytest.mark.parametrize("bad_timestamp", ["", "yesterday", "2026-10-03 12:00:00", "2026-13-01T00:00:00Z"])
def test_presence_of_an_unreadable_timestamp_is_offline(bad_timestamp: str) -> None:
    assert agent_reports.presence(bad_timestamp, 600, LAST_DT) == ("offline", None)


def test_presence_constants_match_the_user_decision() -> None:
    """Literal pins (not derived): 2026-10-03 "Weg B"."""
    assert agent_reports.ASSUMED_REPORT_INTERVAL_SECONDS == 1800
    assert agent_reports.PRESENCE_GRACE_SECONDS == 300
    assert agent_reports.MIN_REPORT_INTERVAL_SECONDS == 60
    assert agent_reports.MAX_REPORT_INTERVAL_SECONDS == 86400


# --- 4. GET /v1/clients --------------------------------------------------------------


def test_clients_shows_online_and_offline_from_the_stored_timestamps(client: TestClient, settings) -> None:
    now = datetime.now(timezone.utc)
    # 10-minute agent: offline after 25 minutes. A minute of margin on each
    # side keeps this independent of how long the request takes.
    insert_report(settings.db_path, "recent", to_utc_iso(now - timedelta(minutes=24)), "0.1.0", 600)
    insert_report(settings.db_path, "stale", to_utc_iso(now - timedelta(minutes=26)), "0.1.0", 600)
    # Unknown interval: offline after 65 minutes.
    insert_report(settings.db_path, "old-agent-recent", to_utc_iso(now - timedelta(minutes=64)))
    insert_report(settings.db_path, "old-agent-stale", to_utc_iso(now - timedelta(minutes=66)))

    rows = clients(client)
    assert rows["recent"]["presence"] == "online"
    assert rows["stale"]["presence"] == "offline"
    assert rows["old-agent-recent"]["presence"] == "online"
    assert rows["old-agent-stale"]["presence"] == "offline"
    assert rows["old-agent-stale"]["agent_version"] is None


def test_clients_reads_the_latest_report_not_the_last_one_that_had_a_version(
    client: TestClient,
) -> None:
    assert post(client, agent_version="0.1.0", report_interval_seconds=600).status_code == 200
    # The same machine downgraded to an agent without the fields.
    assert post(client).status_code == 200
    row = clients(client)["pc"]
    assert row["agent_version"] is None
    assert row["report_interval_seconds"] is None


def test_hand_edited_values_read_back_as_unknown(client: TestClient, settings) -> None:
    now = to_utc_iso(datetime.now(timezone.utc))
    insert_report(settings.db_path, "edited", now, "not a version", 5)
    row = clients(client)["edited"]
    assert row["agent_version"] is None
    assert row["report_interval_seconds"] is None
    assert row["presence"] == "online"


def test_clients_computes_presence_through_the_shared_rule(
    client: TestClient, monkeypatch
) -> None:
    """Structural pin (docs/LEARNINGS.md, "two call sites WILL diverge"):
    the router must return exactly what ``agent_reports.presence`` returns,
    called with each client's stored values and one ``now`` for all rows."""
    assert post(client, agent_version="0.1.0", report_interval_seconds=600).status_code == 200
    assert client.post(ENDPOINT, json={"client_id": "other", "appids": []}, headers=AUTH).status_code == 200

    calls: list[tuple[str, int | None, datetime]] = []

    def fake_presence(last_reported_at: str, interval: int | None, now: datetime):
        calls.append((last_reported_at, interval, now))
        return "offline", f"SENTINEL-{interval}"

    monkeypatch.setattr(agent_reports, "presence", fake_presence)
    rows = clients(client)

    assert rows["pc"]["presence"] == "offline"
    assert rows["pc"]["offline_after"] == "SENTINEL-600"
    assert rows["other"]["offline_after"] == "SENTINEL-None"
    assert sorted(c[1] or 0 for c in calls) == [0, 600]
    assert {c[0] for c in calls} == {rows["pc"]["last_reported_at"], rows["other"]["last_reported_at"]}
    assert len({c[2] for c in calls}) == 1, "every row must be judged against the same now"


def test_existing_client_fields_are_unchanged_by_the_new_ones(client: TestClient) -> None:
    """The web and Android read the WP 2.4 / 3.11 fields; their values must
    not depend on whether the agent sent the presence fields."""
    post(client, client_id="with", agent_version="0.1.0", report_interval_seconds=600)
    post(client, client_id="without")
    rows = clients(client)
    old_keys = [
        "app_count", "source_addrs", "cache_hits", "cache_misses", "bytes_served",
        "last_seen_in_cache_log", "bypass_suspected",
    ]
    assert [rows["with"][k] for k in old_keys] == [rows["without"][k] for k in old_keys]


def test_delete_client_removes_the_presence_too(client: TestClient) -> None:
    post(client, agent_version="0.1.0", report_interval_seconds=600)
    assert client.delete("/v1/clients/pc", headers=AUTH).status_code == 204
    assert clients(client) == {}


def test_sqlite_column_types_survive_a_round_trip(tmp_path) -> None:
    """report_interval_seconds is read back as an int (INTEGER affinity),
    not a string the strict read-back check would discard."""
    db = str(tmp_path / "vault.db")
    init_db(db)
    conn = get_connection(db)
    try:
        agent_reports.store_report(
            conn, "pc", [440], keep=5, agent_version="0.1.0", report_interval_seconds=600
        )
        snapshot = agent_reports.latest_snapshot(conn, "pc")
    finally:
        conn.close()
    assert snapshot is not None
    assert snapshot.agent_version == "0.1.0"
    assert snapshot.report_interval_seconds == 600
    assert isinstance(snapshot.report_interval_seconds, int)

