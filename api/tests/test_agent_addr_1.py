"""WP AGENT-ADDR-1: agents state their LAN addresses; forwarding hops never
count as a client's address.

The bug (measured 2026-10-14): behind Docker's published port every agent
report arrives from the bridge gateway, so ``agent_reports.source_addr`` was
never the PC's LAN address, the bypass rule accused every agent PC, and the
D15b overview handed the gateway to whichever agent reported last.

Synthetic addresses only (RFC 1918 / RFC 4193 / documentation ranges); the
"gateway" is whatever the fixture route file says it is.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from tests.test_bypass_detection import (
    add_cache_stats,
    add_report,
    conn_for,
    make_client,
    one_client,
    set_feed_age,
)
from tests.test_event_sweep import event_line, make_settings, write_log
from vault_api import agent_reports, event_sweep, peer_hops
from vault_api.db import SCHEMA_VERSION, get_connection, init_db
from vault_api.main import create_app

AUTH = {"X-Api-Key": TEST_API_KEY}
UTC = timezone.utc

#: The "Docker bridge gateway" of the fixture routing table.
GATEWAY = "10.88.0.1"
#: Its /proc/net/route spelling: 4 bytes, host (little-endian) order.
GATEWAY_HEX = "0100580A"
GATEWAY6 = "fd00:88::1"

IPV4_ROUTES = (
    "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
    f"eth0\t00000000\t{GATEWAY_HEX}\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
    "eth0\t0000580A\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0\n"
    # A gateway column without RTF_GATEWAY must not count (flags 0001).
    "eth1\t0000590A\t0100590A\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0\n"
)
IPV6_ROUTES = (
    "00000000000000000000000000000000 00 00000000000000000000000000000000 00 "
    "fd000088000000000000000000000001 00000400 00000001 00000000 00000003     eth0\n"
    "fd000088000000000000000000000000 40 00000000000000000000000000000000 00 "
    "00000000000000000000000000000000 00000100 00000001 00000000 00000001     eth0\n"
)


@pytest.fixture
def routes(tmp_path: Path, monkeypatch) -> None:
    """Give vault-api a routing table whose default gateway is GATEWAY."""
    v4 = tmp_path / "route"
    v6 = tmp_path / "ipv6_route"
    v4.write_text(IPV4_ROUTES, encoding="ascii")
    v6.write_text(IPV6_ROUTES, encoding="ascii")
    monkeypatch.setattr(peer_hops, "IPV4_ROUTE_FILE", str(v4))
    monkeypatch.setattr(peer_hops, "IPV6_ROUTE_FILE", str(v6))


# ===========================================================================
# The forwarding-hop rule (vault_api/peer_hops.py)
# ===========================================================================


def test_gateways_are_read_from_both_routing_tables(routes) -> None:
    assert peer_hops.gateway_addrs() == frozenset({GATEWAY, GATEWAY6})


def test_ipv4_gateways_follow_the_host_byte_order(tmp_path: Path, monkeypatch) -> None:
    """The column is the kernel's __be32 printed as a host integer: on a
    big-endian host the same address prints in network order."""
    v4 = tmp_path / "route"
    v4.write_text(IPV4_ROUTES.splitlines()[0] + "\neth0\t00000000\t0A580001\t0003\t0\t0\t0\t00000000\t0\t0\t0\n")
    monkeypatch.setattr(peer_hops.sys, "byteorder", "big")
    assert peer_hops.gateway_addrs(str(v4), str(tmp_path / "none")) == frozenset({GATEWAY})


def test_missing_route_files_mean_no_gateways(tmp_path: Path) -> None:
    assert (
        peer_hops.gateway_addrs(str(tmp_path / "nope"), str(tmp_path / "nope6"))
        == frozenset()
    )


@pytest.mark.parametrize(
    ("addr", "hop"),
    [
        (GATEWAY, True),
        (f"::ffff:{GATEWAY}", True),  # a dual-stack socket's spelling
        (GATEWAY6, True),
        ("fd00:0088:0:0::1", True),  # same address, other spelling
        ("127.0.0.1", True),
        ("127.3.4.5", True),
        ("::1", True),
        ("0.0.0.0", True),
        ("10.88.0.2", False),  # same subnet, not the gateway
        ("10.89.0.1", False),  # gateway column without RTF_GATEWAY
        ("192.168.1.42", False),
        ("testclient", False),  # not an IP literal: the rule does not speak
        ("", False),
    ],
)
def test_the_hop_rule(routes, addr: str, hop: bool) -> None:
    assert peer_hops.forwarding_hop_filter()(addr) is hop


# ===========================================================================
# The report field (schema v24)
# ===========================================================================


def test_stated_addresses_are_stored_canonical_sorted_and_deduplicated(
    tmp_path: Path,
) -> None:
    client, settings = make_client(tmp_path)
    response = client.post(
        "/v1/agent/installed",
        json={
            "client_id": "pc",
            "appids": [440],
            "local_addrs": [
                "fd00:10:0:0::42",
                "192.168.1.42",
                "::ffff:192.168.1.42",
                "2001:db8::42",
            ],
        },
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    conn = conn_for(settings)
    try:
        stored = conn.execute("SELECT local_addrs FROM agent_reports").fetchone()[0]
    finally:
        conn.close()
    assert json.loads(stored) == ["192.168.1.42", "2001:db8::42", "fd00:10::42"]


@pytest.mark.parametrize("value", [None, []])
def test_absent_or_empty_stated_addresses_store_null(tmp_path: Path, value) -> None:
    client, settings = make_client(tmp_path)
    body: dict = {"client_id": "pc", "appids": [440]}
    if value is not None:
        body["local_addrs"] = value
    assert client.post("/v1/agent/installed", json=body, headers=AUTH).status_code == 200
    conn = conn_for(settings)
    try:
        stored = conn.execute("SELECT local_addrs FROM agent_reports").fetchone()[0]
    finally:
        conn.close()
    assert stored is None


@pytest.mark.parametrize(
    "bad",
    [
        ["not-an-ip"],
        ["192.168.1.300"],
        [" 192.168.1.42"],
        ["192.168.1.42\n"],
        ["fe80::1%eth0"],
        ["127.0.0.1"],
        ["::1"],
        ["0.0.0.0"],
        ["169.254.10.10"],
        ["fe80::1"],
        ["224.0.0.1"],
        ["ff02::1"],
        [42],
        "192.168.1.42",
        [f"10.0.0.{n}" for n in range(1, 10)],  # nine: one over the cap
    ],
)
def test_bad_stated_addresses_are_a_422(tmp_path: Path, bad) -> None:
    client, settings = make_client(tmp_path)
    response = client.post(
        "/v1/agent/installed",
        json={"client_id": "pc", "appids": [440], "local_addrs": bad},
        headers=AUTH,
    )
    assert response.status_code == 422, response.text
    conn = conn_for(settings)
    try:
        assert conn.execute("SELECT COUNT(*) FROM agent_reports").fetchone()[0] == 0
    finally:
        conn.close()


def test_eight_stated_addresses_are_accepted(tmp_path: Path) -> None:
    client, _settings = make_client(tmp_path)
    response = client.post(
        "/v1/agent/installed",
        json={
            "client_id": "pc",
            "appids": [440],
            "local_addrs": [f"10.0.0.{n}" for n in range(1, 9)],
        },
        headers=AUTH,
    )
    assert response.status_code == 200, response.text


def _stamp_without_local_addrs(db_path: str, version: int) -> None:
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        conn.executescript(
            f"""
            DROP TABLE agent_reports;
            CREATE TABLE agent_reports (
                client_id TEXT NOT NULL,
                reported_at TEXT NOT NULL,
                appids TEXT NOT NULL,
                source_addr TEXT,
                agent_version TEXT,
                report_interval_seconds INTEGER
            );
            INSERT INTO agent_reports (client_id, reported_at, appids, source_addr)
            VALUES ('old-pc', '2026-10-01T00:00:00Z', '[440]', '192.168.1.7');
            UPDATE schema_version SET version = {version};
            """
        )
        conn.commit()
    finally:
        conn.close()


def test_the_column_step_does_not_trust_the_version_stamp(tmp_path: Path) -> None:
    """Parallel branches stamp schema versions in a different order (a
    sibling package takes the next number with a pure CREATE TABLE), so a
    file can carry the current stamp without this column. init_db adds it
    anyway, and a second run is a no-op."""
    db_path = str(tmp_path / "vault.db")
    _stamp_without_local_addrs(db_path, SCHEMA_VERSION)
    init_db(db_path)
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(agent_reports)")}
    finally:
        conn.close()
    assert "local_addrs" in columns


def test_a_v23_database_gains_the_column_and_keeps_its_rows(tmp_path: Path) -> None:
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        # Rebuild agent_reports in its v23 shape and mark the file v23.
        conn.executescript(
            """
            DROP TABLE agent_reports;
            CREATE TABLE agent_reports (
                client_id TEXT NOT NULL,
                reported_at TEXT NOT NULL,
                appids TEXT NOT NULL,
                source_addr TEXT,
                agent_version TEXT,
                report_interval_seconds INTEGER
            );
            INSERT INTO agent_reports (client_id, reported_at, appids, source_addr)
            VALUES ('old-pc', '2026-10-01T00:00:00Z', '[440]', '192.168.1.7');
            UPDATE schema_version SET version = 23;
            """
        )
        conn.commit()
    finally:
        conn.close()

    init_db(db_path)
    conn = get_connection(db_path)
    try:
        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
        columns = {
            row["name"]: row["type"]
            for row in conn.execute("PRAGMA table_info(agent_reports)")
        }
        row = conn.execute("SELECT source_addr, local_addrs FROM agent_reports").fetchone()
    finally:
        conn.close()
    assert version == SCHEMA_VERSION == 24
    assert columns["local_addrs"] == "TEXT"
    assert (row["source_addr"], row["local_addrs"]) == ("192.168.1.7", None)


# ===========================================================================
# Correlation: stated addresses first, the TCP peer only when it is no hop
# ===========================================================================


def test_effective_addresses_per_report(routes) -> None:
    hop = peer_hops.forwarding_hop_filter()
    stated = json.dumps(["192.168.1.42"])
    assert agent_reports.effective_addrs(GATEWAY, stated, hop) == ["192.168.1.42"]
    # Stated addresses win over a usable peer too: they are what the PC uses.
    assert agent_reports.effective_addrs("192.168.1.9", stated, hop) == ["192.168.1.42"]
    assert agent_reports.effective_addrs("192.168.1.9", None, hop) == ["192.168.1.9"]
    assert agent_reports.effective_addrs(GATEWAY, None, hop) == []
    assert agent_reports.effective_addrs("127.0.0.1", None, hop) == []
    assert agent_reports.effective_addrs(None, None, hop) == []
    # A hand-edited, invalid stored list reads as "stated nothing".
    assert agent_reports.effective_addrs(GATEWAY, '["127.0.0.1"]', hop) == []
    assert agent_reports.effective_addrs("192.168.1.9", "{bad", hop) == ["192.168.1.9"]


def test_clients_lists_stated_addresses_and_keeps_the_raw_peer_for_diagnosis(
    tmp_path: Path, routes
) -> None:
    _client, settings = make_client(tmp_path)
    conn = conn_for(settings)
    try:
        agent_reports.store_report(
            conn, "pc", [440], 10, source_addr=GATEWAY, local_addrs=["192.168.1.42"]
        )
    finally:
        conn.close()
    row = one_client(TestClient(create_app(settings)))
    assert row["source_addrs"] == ["192.168.1.42"]
    assert row["peer_addrs"] == [GATEWAY]
    assert row["reported_addrs"] == ["192.168.1.42"]


# ===========================================================================
# The bypass rule never accuses on a forwarding hop
# ===========================================================================


def _accusing_setup(settings) -> sqlite3.Connection:
    """A feed old enough to accuse, and cache traffic from the GATEWAY only
    -- the shape a Docker published port produces when vault-core logs the
    same hop (and the shape that would 'clear' any client mapped onto it)."""
    conn = conn_for(settings)
    set_feed_age(conn, days_old=10)
    return conn


def test_an_old_agent_whose_only_peer_is_the_gateway_is_never_accused(
    tmp_path: Path, routes
) -> None:
    client, settings = make_client(tmp_path)
    conn = _accusing_setup(settings)
    try:
        add_report(conn, "old-agent", source_addr=GATEWAY)
    finally:
        conn.close()
    row = one_client(client)
    assert row["source_addrs"] == []
    assert row["peer_addrs"] == [GATEWAY]
    assert row["bypass_suspected"] is False


def test_control_the_same_old_agent_from_a_real_lan_peer_is_accused(
    tmp_path: Path, routes
) -> None:
    """Pins that the test above is green for the hop reason, not because the
    setup could never accuse anybody."""
    client, settings = make_client(tmp_path)
    conn = _accusing_setup(settings)
    try:
        add_report(conn, "old-agent", source_addr="192.168.1.42")
    finally:
        conn.close()
    assert one_client(client)["bypass_suspected"] is True


def test_a_new_agent_behind_the_gateway_is_judged_on_its_stated_address(
    tmp_path: Path, routes
) -> None:
    client, settings = make_client(tmp_path)
    conn = _accusing_setup(settings)
    try:
        agent_reports.store_report(
            conn, "pc", [440], 10, source_addr=GATEWAY, local_addrs=["192.168.1.42"]
        )
        add_cache_stats(conn, "192.168.1.42", bytes_served=12345)
    finally:
        conn.close()
    row = one_client(client)
    assert row["bypass_suspected"] is False
    assert row["bytes_served"] == 12345
    assert row["last_seen_in_cache_log"] is not None


def test_a_new_agent_absent_from_the_cache_log_is_still_accused(
    tmp_path: Path, routes
) -> None:
    """The fix must not disable the rule: a PC that states its address and
    never shows up at the cache is exactly the case the rule exists for."""
    client, settings = make_client(tmp_path)
    conn = _accusing_setup(settings)
    try:
        agent_reports.store_report(
            conn, "pc", [440], 10, source_addr=GATEWAY, local_addrs=["192.168.1.42"]
        )
        add_cache_stats(conn, GATEWAY)  # gateway traffic is not the PC's
    finally:
        conn.close()
    row = one_client(client)
    assert row["bypass_suspected"] is True
    assert row["bytes_served"] == 0


def test_the_webhook_sweep_uses_the_same_addresses(tmp_path: Path, routes) -> None:
    """``check_bypass_transitions`` reads ``list_clients`` too; an old agent
    behind the gateway must not produce a 'suspected' transition."""
    _client, settings = make_client(tmp_path)
    conn = _accusing_setup(settings)
    try:
        add_report(conn, "old-agent", source_addr=GATEWAY)
        (summary,) = agent_reports.list_clients(conn)
        totals = event_sweep.totals_for_addrs(conn, summary.source_addrs)
        now = datetime.now(UTC)
        assert (
            event_sweep.bypass_suspected(
                summary,
                totals,
                feed_can_accuse=True,
                cutoff_iso=(now - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
            is False
        )
    finally:
        conn.close()


# ===========================================================================
# D15b clients overview
# ===========================================================================


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")


@pytest.fixture
def overview(tmp_path: Path, routes):
    """Two PCs behind the same gateway: one new agent stating its address,
    one old agent. vault-core logs both LAN addresses plus the gateway."""
    settings = make_settings(tmp_path, cooldown=0)
    now = datetime.now(UTC)
    init_db(settings.db_path)
    conn = get_connection(settings.db_path)
    try:
        agent_reports.store_report(
            conn, "new-pc", [440], 10, source_addr=GATEWAY, local_addrs=["192.168.1.42"]
        )
        agent_reports.store_report(conn, "old-pc", [570], 10, source_addr=GATEWAY)
        write_log(
            tmp_path / "event.log",
            event_line(time=iso(now - timedelta(minutes=2)), addr="192.168.1.42",
                       cache_status="HIT", depot="100", bytes_sent="1000"),
            event_line(time=iso(now - timedelta(minutes=2)), addr="192.168.1.77",
                       cache_status="HIT", depot="200", bytes_sent="700"),
            event_line(time=iso(now - timedelta(minutes=1)), addr=GATEWAY,
                       cache_status="MISS", depot="300", bytes_sent="300"),
        )
        event_sweep.sweep_once(conn, settings, now)
        yield settings
    finally:
        conn.close()


def test_the_overview_maps_agents_on_their_stated_addresses(overview) -> None:
    body = TestClient(create_app(overview)).get(
        "/v1/clients/overview", headers=AUTH
    ).json()
    by_key = {entry["key"]: entry for entry in body["clients"]}
    assert by_key["agent:new-pc"]["addresses"] == ["192.168.1.42"]
    assert by_key["agent:new-pc"]["stats"]["bytes_served"] == 1000
    # The old agent gets no address: the gateway is nobody's.
    assert by_key["agent:old-pc"]["addresses"] == []
    # So the gateway and the old PC's LAN address stay address clients.
    assert by_key[f"addr:{GATEWAY}"]["kind"] == "address"
    assert by_key["addr:192.168.1.77"]["kind"] == "address"
    assert "addr:192.168.1.42" not in by_key


def test_address_owners_never_assigns_a_hop(tmp_path: Path, routes) -> None:
    init_db(str(tmp_path / "v.db"))
    conn = get_connection(str(tmp_path / "v.db"))
    try:
        agent_reports.store_report(conn, "a", [1], 10, source_addr=GATEWAY)
        agent_reports.store_report(conn, "b", [1], 10, source_addr="127.0.0.1")
        agent_reports.store_report(
            conn, "c", [1], 10, source_addr=GATEWAY, local_addrs=["192.168.1.5"]
        )
        assert agent_reports.address_owners(conn) == {"192.168.1.5": "c"}
    finally:
        conn.close()
