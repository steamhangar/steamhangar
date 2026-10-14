"""WP D15-NAMES: client names (schema v26) and "Delete history".

Synthetic addresses (RFC 5737 / documentation ranges and 10/8) and ids only.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from tests.test_event_sweep import make_settings
from vault_api import agent_reports, client_names, history, jobs as jobs_queue, traffic
from vault_api.config import Settings
from vault_api.db import SCHEMA_VERSION, get_connection, init_db
from vault_api.main import create_app

AUTH = {"X-Api-Key": TEST_API_KEY}
UTC = timezone.utc


def stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    # Feed disabled: the overview still lists agent clients, and nothing here
    # depends on the event log.
    s = make_settings(tmp_path, log_path="")
    init_db(s.db_path)
    return s


def conn_for(settings: Settings) -> sqlite3.Connection:
    return get_connection(settings.db_path)


def client_for(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Living room PC", "Living room PC"),
        ("  frame \t", "frame"),
        ("x" * 40, "x" * 40),
        ("Jans Büro-PC", "Jans Büro-PC"),
        ("\U0001f3ae" * 40, "\U0001f3ae" * 40),  # 40 code points
        ("-", "-"),  # punctuation alone is visible
        ("e\u0301", "e\u0301"),  # a combining mark next to a letter
    ],
)
def test_names_are_trimmed_and_accepted_up_to_40_characters(raw: str, expected: str) -> None:
    assert client_names.normalize_name(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "x" * 41,
        "two\nlines",
        "tab\tinside",
        "nul\x00",
        "esc\x1b[31m",
        "zero\u200bwidth",  # Cf
        "bidi\u202eflip",  # Cf
        "line\u2028sep",  # Zl
        "private\ue000",  # Co
        "\u0301\u0301",  # combining marks only: nothing visible
        "\u3000",  # an ideographic space strips to nothing
        None,
        42,
        ["frame"],
    ],
)
def test_names_with_control_characters_bad_lengths_or_types_are_refused(raw: object) -> None:
    with pytest.raises(ValueError):
        client_names.normalize_name(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("192.0.2.7", "192.0.2.7"),
        ("2001:DB8:0:0:0:0:0:1", "2001:db8::1"),
        ("::ffff:192.0.2.7", "192.0.2.7"),
        ("127.0.0.1", "127.0.0.1"),
    ],
)
def test_addresses_are_stored_in_canonical_form(raw: str, expected: str) -> None:
    assert client_names.canonical_ip(raw) == expected


@pytest.mark.parametrize(
    "raw", ["", " 192.0.2.7", "192.0.2.7 ", "fe80::1%eth0", "frame", "192.0.2.300", "１９２.0.2.7"]
)
def test_bad_addresses_are_refused(raw: str) -> None:
    with pytest.raises(ValueError):
        client_names.canonical_ip(raw)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def test_name_endpoints_require_the_api_key(settings: Settings) -> None:
    client = client_for(settings)
    assert client.get("/v1/client-names").status_code == 401
    assert client.put("/v1/client-names/ip/192.0.2.7", json={"name": "x"}).status_code == 401
    assert client.delete("/v1/client-names/ip/192.0.2.7").status_code == 401
    assert client.delete("/v1/history", params={"scope": "all"}).status_code == 401


def test_set_rename_list_and_clear_a_name(settings: Settings) -> None:
    client = client_for(settings)
    put = client.put(
        "/v1/client-names/ip/::ffff:192.0.2.7", json={"name": "  frame  "}, headers=AUTH
    )
    assert put.status_code == 200
    body = put.json()
    assert (body["key_type"], body["key"], body["name"]) == ("ip", "192.0.2.7", "frame")

    renamed = client.put("/v1/client-names/ip/192.0.2.7", json={"name": "Hall TV"}, headers=AUTH)
    assert renamed.status_code == 200
    listed = client.get("/v1/client-names", headers=AUTH).json()
    assert [(n["key"], n["name"]) for n in listed] == [("192.0.2.7", "Hall TV")]

    assert client.delete("/v1/client-names/ip/192.0.2.7", headers=AUTH).status_code == 204
    gone = client.delete("/v1/client-names/ip/192.0.2.7", headers=AUTH)
    assert gone.status_code == 404
    assert gone.json()["detail"].startswith("No name stored")
    assert client.get("/v1/client-names", headers=AUTH).json() == []


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/v1/client-names/mac/00:11:22:33:44:55", {"name": "x"}),  # reserved
        ("/v1/client-names/host/frame", {"name": "x"}),  # unknown type
        ("/v1/client-names/ip/frame", {"name": "x"}),  # not an IP
        ("/v1/client-names/ip/192.0.2.7", {"name": ""}),  # empty: clear is DELETE
        ("/v1/client-names/ip/192.0.2.7", {"name": "a\nb"}),
        ("/v1/client-names/ip/192.0.2.7", {"name": "x" * 41}),
        ("/v1/client-names/ip/192.0.2.7", {"name": 7}),  # strict str
        ("/v1/client-names/ip/192.0.2.7", {"name": "x", "extra": 1}),
        ("/v1/client-names/ip/192.0.2.7", {}),
    ],
)
def test_put_refuses_bad_input_with_422_and_stores_nothing(
    settings: Settings, path: str, body: dict[str, object]
) -> None:
    client = client_for(settings)
    response = client.put(path, json=body, headers=AUTH)
    assert response.status_code == 422, response.text
    assert client.get("/v1/client-names", headers=AUTH).json() == []


def test_mac_keys_are_reserved_in_the_schema_but_other_key_types_are_not(
    settings: Settings,
) -> None:
    conn = conn_for(settings)
    try:
        conn.execute(
            "INSERT INTO client_names VALUES ('mac', '00:11:22:33:44:55', 'x', '2026-10-14T00:00:00Z')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO client_names VALUES ('host', 'frame', 'x', '2026-10-14T00:00:00Z')"
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Names in GET /v1/clients and GET /v1/clients/overview
# ---------------------------------------------------------------------------


def _by_client_id(rows: list[dict[str, object]]) -> dict[object, dict[str, object]]:
    return {row["client_id"]: row for row in rows}


def test_an_explicit_name_wins_over_the_agent_name_in_both_endpoints(settings: Settings) -> None:
    conn = conn_for(settings)
    try:
        agent_reports.store_report(
            conn, "DEMON", [440], 10, source_addr="172.16.13.1", local_addrs=["10.0.0.5"]
        )
        agent_reports.store_report(
            conn, "loft", [570], 10, source_addr="172.16.13.1", local_addrs=["10.0.0.6"]
        )
        client_names.set_name(conn, "ip", "10.0.0.5", "Living room PC")
    finally:
        conn.close()
    client = client_for(settings)

    flat = _by_client_id(client.get("/v1/clients", headers=AUTH).json())
    assert (flat["DEMON"]["name"], flat["DEMON"]["display_name"]) == ("Living room PC", "Living room PC")
    assert flat["DEMON"]["name_addr"] == "10.0.0.5"
    assert (flat["loft"]["name"], flat["loft"]["display_name"]) == (None, "loft")
    assert flat["loft"]["name_addr"] == "10.0.0.6"

    overview = client.get("/v1/clients/overview", headers=AUTH).json()["clients"]
    rows = _by_client_id(overview)
    for client_id in ("DEMON", "loft"):
        for field in ("name", "name_addr", "display_name"):
            assert rows[client_id][field] == flat[client_id][field], (client_id, field)


def test_a_name_follows_the_address_when_it_moves_to_another_pc(settings: Settings) -> None:
    conn = conn_for(settings)
    try:
        agent_reports.store_report(conn, "old-pc", [440], 10, local_addrs=["10.0.0.5"])
        conn.execute("UPDATE agent_reports SET reported_at = '2026-01-01T00:00:00Z'")
        conn.commit()
        agent_reports.store_report(conn, "new-pc", [570], 10, local_addrs=["10.0.0.5"])
        client_names.set_name(conn, "ip", "10.0.0.5", "Desk")
    finally:
        conn.close()
    rows = _by_client_id(client_for(settings).get("/v1/clients", headers=AUTH).json())
    assert rows["new-pc"]["name"] == "Desk"
    # The old PC still lists the address in source_addrs (retained report),
    # but no longer owns it, so it does not show the name.
    assert "10.0.0.5" in rows["old-pc"]["source_addrs"]
    assert (rows["old-pc"]["name"], rows["old-pc"]["display_name"]) == (None, "old-pc")
    assert rows["old-pc"]["name_addr"] is None


def test_an_agent_without_a_correlated_address_cannot_be_named(settings: Settings) -> None:
    conn = conn_for(settings)
    try:
        # An old agent: no local_addrs, and loopback is always a forwarding hop.
        agent_reports.store_report(conn, "old-agent", [440], 10, source_addr="127.0.0.1")
    finally:
        conn.close()
    rows = _by_client_id(client_for(settings).get("/v1/clients", headers=AUTH).json())
    assert rows["old-agent"]["name_addr"] is None
    assert rows["old-agent"]["display_name"] == "old-agent"


def test_resolve_address_names_an_agentless_client_and_matches_canonically() -> None:
    names = {"2001:db8::9": "frame"}
    resolved = client_names.resolve_address(names, "2001:DB8::9")
    assert (resolved.name, resolved.name_addr, resolved.display_name) == (
        "frame",
        "2001:db8::9",
        "frame",
    )
    plain = client_names.resolve_address(names, "10.0.0.9")
    assert (plain.name, plain.name_addr, plain.display_name) == (None, "10.0.0.9", "10.0.0.9")


def test_resolve_agent_takes_the_first_named_owned_address_in_sorted_order() -> None:
    owners = {"10.0.0.7": "pc", "10.0.0.3": "pc", "10.0.0.9": "other"}
    names = {"10.0.0.7": "Seven", "10.0.0.9": "Nine"}
    resolved = client_names.resolve_agent(names, owners, "pc")
    assert (resolved.name, resolved.name_addr) == ("Seven", "10.0.0.7")
    names["10.0.0.3"] = "Three"
    assert client_names.resolve_agent(names, owners, "pc").name == "Three"


def test_both_endpoints_take_the_name_from_the_one_shared_rule(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Structural pin (LEARNINGS, WP 4f): both routers must return EXACTLY
    what client_names.resolve_agent says, not a rule of their own."""
    conn = conn_for(settings)
    try:
        agent_reports.store_report(conn, "pc", [440], 10, local_addrs=["10.0.0.5"])
    finally:
        conn.close()
    sentinel = client_names.ResolvedName(
        name="SENTINEL", name_addr="198.51.100.1", display_name="SENTINEL-DISPLAY"
    )
    monkeypatch.setattr(client_names, "resolve_agent", lambda *_args: sentinel)
    client = client_for(settings)
    flat = client.get("/v1/clients", headers=AUTH).json()[0]
    over = client.get("/v1/clients/overview", headers=AUTH).json()["clients"][0]
    for row in (flat, over):
        assert (row["name"], row["name_addr"], row["display_name"]) == (
            "SENTINEL",
            "198.51.100.1",
            "SENTINEL-DISPLAY",
        )


# ---------------------------------------------------------------------------
# Schema v26
# ---------------------------------------------------------------------------


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }


@pytest.mark.parametrize("stamp_version", [24, 25, 26])
def test_the_names_table_is_created_whatever_version_the_file_is_stamped_with(
    tmp_path: Path, stamp_version: int
) -> None:
    """Does not trust the stamp: a file a parallel branch stamped v25 (or
    even v26) without the table still gets it; a second start is a no-op."""
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        conn.execute("DROP TABLE client_names")
        conn.execute("UPDATE schema_version SET version = ?", (stamp_version,))
        conn.commit()
    finally:
        conn.close()

    init_db(db_path)
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        assert "client_names" in _tables(conn)
        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    finally:
        conn.close()
    assert version == SCHEMA_VERSION == 26


# ---------------------------------------------------------------------------
# Delete history
# ---------------------------------------------------------------------------


def _job(conn: sqlite3.Connection, status: str, finished: datetime | None, created: datetime) -> int:
    cursor = conn.execute(
        "INSERT INTO jobs (appid, type, status, created_at, finished_at) VALUES (440, 'prefill', ?, ?, ?)",
        (status, stamp(created), stamp(finished) if finished else None),
    )
    return int(cursor.lastrowid)


def _traffic(conn: sqlite3.Connection, tier: str, bucket: datetime) -> None:
    at = traffic.bucket_iso(bucket, tier)
    conn.execute(
        "INSERT INTO client_traffic (tier, bucket_at, client_addr, bytes_served, last_seen) VALUES (?, ?, '10.0.0.5', 1, ?)",
        (tier, at, at),
    )
    conn.execute(
        "INSERT INTO client_depot_traffic (tier, bucket_at, client_addr, depotid, bytes_served, last_seen) VALUES (?, ?, '10.0.0.5', 100, 1, ?)",
        (tier, at, at),
    )
    conn.execute(
        "INSERT INTO server_traffic (tier, bucket_at, bytes_served) VALUES (?, ?, 1)",
        (tier, at),
    )


@pytest.fixture
def seeded(settings: Settings):
    now = datetime.now(UTC)
    conn = conn_for(settings)
    ids: dict[str, int] = {}
    try:
        ids["done_40d"] = _job(conn, "done", now - timedelta(days=40), now - timedelta(days=41))
        ids["error_31d"] = _job(conn, "error", now - timedelta(days=31), now - timedelta(days=31))
        ids["cancelled_old_unfinished_stamp"] = _job(conn, "cancelled", None, now - timedelta(days=45))
        ids["done_29d"] = _job(conn, "done", now - timedelta(days=29), now - timedelta(days=60))
        ids["done_1h"] = _job(conn, "done", now - timedelta(hours=1), now - timedelta(hours=2))
        # Work, not history: never deleted, however old.
        ids["queued_old"] = _job(conn, "queued", None, now - timedelta(days=90))
        ids["running_old"] = _job(conn, "running", None, now - timedelta(days=90))
        ids["paused_old"] = _job(conn, "paused", None, now - timedelta(days=90))

        _traffic(conn, traffic.TIER_1D, now - timedelta(days=40))
        _traffic(conn, traffic.TIER_1D, now - timedelta(days=30))  # straddles the cutoff
        _traffic(conn, traffic.TIER_1D, now - timedelta(days=2))
        _traffic(conn, traffic.TIER_1H, now - timedelta(days=30, hours=2))
        _traffic(conn, traffic.TIER_1H, now - timedelta(days=29))
        _traffic(conn, traffic.TIER_5M, now - timedelta(minutes=10))

        conn.execute(
            """
            INSERT INTO event_sweep_state
                (id, traffic_since, cursor_offset, lines_read_total, last_sweep_at)
            VALUES (1, ?, 4096, 17, '2026-01-04T00:00:00Z')
            """,
            (stamp(now - timedelta(days=50)),),
        )
        conn.execute(
            """
            INSERT INTO apps (appid, name, status, last_prefill_at, last_manifest_check)
            VALUES (440, 'Kept Game', 'idle', '2026-01-02T00:00:00Z', '2026-01-03T00:00:00Z')
            """
        )
        conn.execute(
            "INSERT INTO depot_miss_stats (depotid, miss_count, first_seen, last_seen) VALUES (100, 2, ?, ?)",
            (stamp(now), stamp(now)),
        )
        conn.execute(
            "INSERT INTO client_bypass_state (client_id, bypass_suspected, updated_at) VALUES ('pc', 0, ?)",
            (stamp(now),),
        )
        conn.execute(
            "INSERT INTO client_cache_stats (client_addr, window_at, requests, last_seen) VALUES ('10.0.0.5', ?, 3, ?)",
            (stamp(now), stamp(now)),
        )
        agent_reports.store_report(conn, "pc", [440], 10, local_addrs=["10.0.0.5"])
        client_names.set_name(conn, "ip", "10.0.0.5", "Desk")
        conn.commit()
    finally:
        conn.close()
    return settings, now, ids


def _job_ids(conn: sqlite3.Connection) -> set[int]:
    return {int(row[0]) for row in conn.execute("SELECT id FROM jobs")}


def _buckets(conn: sqlite3.Connection, table: str) -> set[tuple[str, str]]:
    return {(str(r[0]), str(r[1])) for r in conn.execute(f"SELECT tier, bucket_at FROM {table}")}


def _untouched(conn: sqlite3.Connection) -> None:
    assert conn.execute("SELECT COUNT(*) FROM client_cache_stats").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM depot_miss_stats").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM client_bypass_state").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM agent_reports").fetchone()[0] == 1
    app = conn.execute(
        "SELECT name, last_prefill_at, last_manifest_check FROM apps WHERE appid = 440"
    ).fetchone()
    assert tuple(app) == ("Kept Game", "2026-01-02T00:00:00Z", "2026-01-03T00:00:00Z")
    state = conn.execute(
        "SELECT cursor_offset, lines_read_total, last_sweep_at FROM event_sweep_state"
    ).fetchone()
    assert tuple(state) == (4096, 17, "2026-01-04T00:00:00Z")
    assert client_names.ip_names(conn) == {"10.0.0.5": "Desk"}


def test_delete_all_removes_every_finished_job_and_every_traffic_row(seeded) -> None:
    settings, _now, ids = seeded
    response = client_for(settings).delete("/v1/history", params={"scope": "all"}, headers=AUTH)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scope"] == "all" and body["cutoff"] is None
    assert body["jobs_deleted"] == 5
    assert body["traffic_rows_deleted"] == {
        "client_traffic": 6,
        "client_depot_traffic": 6,
        "server_traffic": 6,
    }
    conn = conn_for(settings)
    try:
        assert _job_ids(conn) == {ids["queued_old"], ids["running_old"], ids["paused_old"]}
        for table in history.TRAFFIC_TABLES:
            assert _buckets(conn, table) == set()
        assert conn.execute("SELECT traffic_since FROM event_sweep_state").fetchone()[0] is None
        _untouched(conn)
    finally:
        conn.close()


def test_delete_older_than_30_days_keeps_newer_history_and_straddling_buckets(seeded) -> None:
    settings, now, ids = seeded
    response = client_for(settings).delete(
        "/v1/history", params={"scope": "older-than-30d"}, headers=AUTH
    )
    assert response.status_code == 200, response.text
    body = response.json()
    cutoff = now - timedelta(days=30)
    assert body["jobs_deleted"] == 3
    assert body["traffic_rows_deleted"]["server_traffic"] == 2
    assert jobs_queue.parse_utc_iso(body["cutoff"]) is not None
    conn = conn_for(settings)
    try:
        assert _job_ids(conn) == {
            ids["done_29d"],
            ids["done_1h"],
            ids["queued_old"],
            ids["running_old"],
            ids["paused_old"],
        }
        expected = {
            (traffic.TIER_1D, traffic.bucket_iso(now - timedelta(days=30), traffic.TIER_1D)),
            (traffic.TIER_1D, traffic.bucket_iso(now - timedelta(days=2), traffic.TIER_1D)),
            (traffic.TIER_1H, traffic.bucket_iso(now - timedelta(days=29), traffic.TIER_1H)),
            (traffic.TIER_5M, traffic.bucket_iso(now - timedelta(minutes=10), traffic.TIER_5M)),
        }
        for table in history.TRAFFIC_TABLES:
            assert _buckets(conn, table) == expected, table
        since = conn.execute("SELECT traffic_since FROM event_sweep_state").fetchone()[0]
        assert since == traffic.bucket_iso(cutoff, traffic.TIER_1D)
        _untouched(conn)
    finally:
        conn.close()


def test_delete_older_never_moves_traffic_since_backwards(settings: Settings) -> None:
    now = datetime.now(UTC)
    recent = stamp(now - timedelta(days=3))
    conn = conn_for(settings)
    try:
        conn.execute("INSERT INTO event_sweep_state (id, traffic_since) VALUES (1, ?)", (recent,))
        conn.commit()
        history.delete_history(conn, history.SCOPE_OLDER_THAN_30D, now)
        assert conn.execute("SELECT traffic_since FROM event_sweep_state").fetchone()[0] == recent
    finally:
        conn.close()


@pytest.mark.parametrize("params", [{}, {"scope": "everything"}, {"scope": "30d"}])
def test_delete_history_requires_a_known_scope(seeded, params: dict[str, str]) -> None:
    settings, _now, _ids = seeded
    response = client_for(settings).delete("/v1/history", params=params, headers=AUTH)
    assert response.status_code == 422
    conn = conn_for(settings)
    try:
        assert len(_job_ids(conn)) == 8
    finally:
        conn.close()


def test_the_history_filter_and_the_delete_share_one_time_rule(
    seeded, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Structural pin: GET /v1/jobs?range=30d and the 30-day delete read the
    same jobs.HISTORY_TIME_SQL. Switching it to created_at moves done_29d
    (created 60 days ago) out of both."""
    settings, now, ids = seeded
    monkeypatch.setattr(jobs_queue, "HISTORY_TIME_SQL", "created_at")
    conn = conn_for(settings)
    try:
        listed = {
            int(row["id"])
            for row in jobs_queue.list_jobs(conn, 200, since=stamp(now - timedelta(days=30)))
        }
        assert ids["done_29d"] not in listed
        history.delete_history(conn, history.SCOPE_OLDER_THAN_30D, now)
        assert ids["done_29d"] not in _job_ids(conn)
    finally:
        conn.close()
