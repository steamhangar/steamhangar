"""WP 3.11 (ADR-0008): GET /v1/clients cache fields + bypass detection.

Bypass detection is a fail-safe DEFAULT, and docs/LEARNINGS.md is explicit
that those need tests pinning the DEFAULT direction, not just the happy path:
"flip each 'unknown => protected' branch and watch a test die". So each of the
six disqualifications in ``routers/clients.py::_bypass_suspected`` gets its own
named test, and the mutation evidence for them is recorded in the WP report.

The direction that must never break: **no false accusations**. A machine
wrongly flagged as bypassing sends an operator hunting a network fault that
does not exist.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from vault_api.config import Settings
from vault_api.db import get_connection, init_db
from vault_api.jobs import to_utc_iso
from vault_api.main import create_app

AUTH = {"X-Api-Key": TEST_API_KEY}

#: The address every fixture client reports from and appears in the log as.
ADDR = "10.0.0.42"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ago(**kwargs) -> str:
    return to_utc_iso(utc_now() - timedelta(**kwargs))


def make_client(
    tmp_path: Path, *, feed_on: bool = True, bypass_days: int = 3
) -> tuple[TestClient, Settings]:
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
        event_log_path=str(tmp_path / "event.log") if feed_on else "",
        bypass_window_days=bypass_days,
        event_log_max_bytes=0,
    )
    return TestClient(create_app(settings)), settings


def conn_for(settings: Settings) -> sqlite3.Connection:
    init_db(settings.db_path)
    return get_connection(settings.db_path)


def add_report(
    conn: sqlite3.Connection,
    client_id: str,
    appids: str = "[440, 730]",
    *,
    reported_at: str | None = None,
    source_addr: str | None = ADDR,
) -> None:
    conn.execute(
        "INSERT INTO agent_reports (client_id, reported_at, appids, source_addr) "
        "VALUES (?, ?, ?, ?)",
        (client_id, reported_at or to_utc_iso(utc_now()), appids, source_addr),
    )
    conn.commit()


def set_feed_age(conn: sqlite3.Connection, *, days_old: float) -> None:
    """Pretend the sweeper has been running for ``days_old`` days."""
    conn.execute(
        """
        INSERT INTO event_sweep_state (id, first_sweep_at, last_sweep_at)
        VALUES (1, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            first_sweep_at = excluded.first_sweep_at,
            last_sweep_at = excluded.last_sweep_at
        """,
        (ago(days=days_old), to_utc_iso(utc_now())),
    )
    conn.commit()


def add_cache_stats(
    conn: sqlite3.Connection,
    addr: str = ADDR,
    *,
    hits: int = 10,
    misses: int = 2,
    bytes_served: int = 4096,
    last_seen: str | None = None,
    window_at: str | None = None,
) -> None:
    stamp = last_seen or to_utc_iso(utc_now())
    conn.execute(
        """
        INSERT INTO client_cache_stats (
            client_addr, window_at, requests, hits, misses, bypasses, errors,
            bytes_served, last_seen
        ) VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?)
        """,
        (addr, window_at or stamp, hits + misses, hits, misses, bytes_served, stamp),
    )
    conn.commit()


def one_client(client: TestClient) -> dict:
    body = client.get("/v1/clients", headers=AUTH).json()
    assert len(body) == 1
    return body[0]


# ===========================================================================
# The source address is recorded (schema v9)
# ===========================================================================


def test_the_report_source_address_is_recorded_and_exposed(tmp_path: Path) -> None:
    client, settings = make_client(tmp_path)

    client.post(
        "/v1/agent/installed",
        json={"client_id": "gaming-pc", "appids": [440]},
        headers=AUTH,
    )

    conn = conn_for(settings)
    try:
        stored = conn.execute(
            "SELECT source_addr FROM agent_reports WHERE client_id = 'gaming-pc'"
        ).fetchone()["source_addr"]
    finally:
        conn.close()

    # TestClient reports "testclient" as the peer; the point is that SOMETHING
    # was captured from the transport rather than from the request body.
    assert stored == "testclient"
    assert one_client(client)["source_addrs"] == ["testclient"]


def test_every_address_a_client_reported_from_is_kept(tmp_path: Path) -> None:
    """A laptop moving wifi -> cable keeps both addresses within retention."""
    client, settings = make_client(tmp_path)
    conn = conn_for(settings)
    try:
        add_report(conn, "laptop", source_addr="10.0.0.50")
        add_report(conn, "laptop", source_addr="10.0.0.51")
    finally:
        conn.close()

    assert one_client(client)["source_addrs"] == ["10.0.0.50", "10.0.0.51"]


def test_a_report_with_no_usable_peer_address_is_still_stored(
    tmp_path: Path,
) -> None:
    """Correlation is a nice-to-have; accepting the report is not negotiable."""
    from vault_api import agent_reports

    _client, settings = make_client(tmp_path)
    conn = conn_for(settings)
    try:
        result = agent_reports.store_report(
            conn, client_id="pc", appids=[440], keep=20, source_addr="  bad addr  "
        )
        stored = conn.execute(
            "SELECT source_addr FROM agent_reports WHERE client_id = 'pc'"
        ).fetchone()["source_addr"]
    finally:
        conn.close()

    assert result.received == 1
    assert stored is None


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("10.0.0.42", "10.0.0.42"),
        ("  10.0.0.42  ", "10.0.0.42"),
        ("fe80::1%eth0", "fe80::1%eth0"),
        (None, None),
        ("", None),
        ("a b", None),
        ("a" * 65, None),
        ("addr\x07", None),
        ("ünicode", None),
    ],
)
def test_source_address_normalisation(raw, expected) -> None:
    from vault_api import agent_reports

    assert agent_reports.normalize_source_addr(raw) == expected


# ===========================================================================
# The cache-side statistics fields
# ===========================================================================


def test_cache_statistics_are_summed_across_every_address_of_a_client(
    tmp_path: Path,
) -> None:
    client, settings = make_client(tmp_path)
    conn = conn_for(settings)
    try:
        add_report(conn, "laptop", source_addr="10.0.0.50")
        add_report(conn, "laptop", source_addr="10.0.0.51")
        add_cache_stats(conn, "10.0.0.50", hits=5, misses=1, bytes_served=1000)
        add_cache_stats(conn, "10.0.0.51", hits=7, misses=3, bytes_served=2000)
        # Another machine's traffic must not leak into this client's totals.
        add_cache_stats(conn, "10.0.0.9", hits=99, misses=99, bytes_served=99)
        set_feed_age(conn, days_old=10)
    finally:
        conn.close()

    row = one_client(client)
    assert row["cache_hits"] == 12
    assert row["cache_misses"] == 4
    assert row["bytes_served"] == 3000
    assert row["last_seen_in_cache_log"] is not None


def test_a_client_never_seen_in_the_cache_log_reports_zeroes(
    tmp_path: Path,
) -> None:
    client, settings = make_client(tmp_path)
    conn = conn_for(settings)
    try:
        add_report(conn, "gaming-pc")
    finally:
        conn.close()

    row = one_client(client)
    assert row["cache_hits"] == 0
    assert row["cache_misses"] == 0
    assert row["bytes_served"] == 0
    assert row["last_seen_in_cache_log"] is None


# ===========================================================================
# bypass_suspected — the accusation, and every reason NOT to make it
# ===========================================================================


def test_a_reporting_client_absent_from_the_cache_log_is_suspected(
    tmp_path: Path,
) -> None:
    """The one direction that says TRUE — everything else must disqualify."""
    client, settings = make_client(tmp_path)
    conn = conn_for(settings)
    try:
        add_report(conn, "ghost-pc")  # reporting now, with games installed
        set_feed_age(conn, days_old=10)  # feed older than the 3-day window
        # …and no client_cache_stats row for its address at all.
    finally:
        conn.close()

    row = one_client(client)
    assert row["bypass_suspected"] is True
    assert row["last_seen_in_cache_log"] is None


def test_a_client_seen_in_the_cache_log_within_the_window_is_not_suspected(
    tmp_path: Path,
) -> None:
    client, settings = make_client(tmp_path)
    conn = conn_for(settings)
    try:
        add_report(conn, "gaming-pc")
        set_feed_age(conn, days_old=10)
        add_cache_stats(conn, last_seen=ago(hours=2))
    finally:
        conn.close()

    assert one_client(client)["bypass_suspected"] is False


def test_a_client_whose_only_cache_traffic_predates_the_window_is_suspected(
    tmp_path: Path,
) -> None:
    """Presence must be RECENT; an old row is not an alibi."""
    client, settings = make_client(tmp_path)
    conn = conn_for(settings)
    try:
        add_report(conn, "gaming-pc")
        set_feed_age(conn, days_old=30)
        add_cache_stats(conn, last_seen=ago(days=20))
    finally:
        conn.close()

    assert one_client(client)["bypass_suspected"] is True


def test_bypass_is_never_suspected_when_the_event_feed_is_off(
    tmp_path: Path,
) -> None:
    """DISQUALIFIER 1. No cache log exists to be absent from."""
    client, settings = make_client(tmp_path, feed_on=False)
    conn = conn_for(settings)
    try:
        add_report(conn, "ghost-pc")
        # Even with an old-looking feed state left in the database.
        set_feed_age(conn, days_old=30)
    finally:
        conn.close()

    assert one_client(client)["bypass_suspected"] is False


def test_bypass_is_never_suspected_before_any_sweep_has_run(
    tmp_path: Path,
) -> None:
    """DISQUALIFIER 2a. No first_sweep_at = we have not been watching at all."""
    client, settings = make_client(tmp_path)
    conn = conn_for(settings)
    try:
        add_report(conn, "ghost-pc")
    finally:
        conn.close()

    assert one_client(client)["bypass_suspected"] is False


def test_bypass_is_never_suspected_while_the_feed_is_younger_than_the_window(
    tmp_path: Path,
) -> None:
    """DISQUALIFIER 2b. "No presence in 3 days" needs 3 days of watching."""
    client, settings = make_client(tmp_path, bypass_days=3)
    conn = conn_for(settings)
    try:
        add_report(conn, "ghost-pc")
        set_feed_age(conn, days_old=1)  # the feed is one day old
    finally:
        conn.close()

    assert one_client(client)["bypass_suspected"] is False


def test_a_client_that_has_been_silent_longer_than_the_window_is_not_suspected(
    tmp_path: Path,
) -> None:
    """DISQUALIFIER 3. A machine that has been off cannot be bypassing."""
    client, settings = make_client(tmp_path)
    conn = conn_for(settings)
    try:
        add_report(conn, "decommissioned-pc", reported_at=ago(days=30))
        set_feed_age(conn, days_old=60)
    finally:
        conn.close()

    assert one_client(client)["bypass_suspected"] is False


def test_a_client_with_nothing_installed_is_not_suspected(tmp_path: Path) -> None:
    """DISQUALIFIER 4. Nothing to download means nothing to download around."""
    client, settings = make_client(tmp_path)
    conn = conn_for(settings)
    try:
        add_report(conn, "fresh-deck", appids="[]")
        set_feed_age(conn, days_old=10)
    finally:
        conn.close()

    assert one_client(client)["bypass_suspected"] is False


def test_a_client_with_an_unreadable_snapshot_is_not_suspected(
    tmp_path: Path,
) -> None:
    """DISQUALIFIER 4, other half: app_count is None, not 0."""
    client, settings = make_client(tmp_path)
    conn = conn_for(settings)
    try:
        add_report(conn, "corrupt-pc", appids="not json{")
        set_feed_age(conn, days_old=10)
    finally:
        conn.close()

    row = one_client(client)
    assert row["app_count"] is None
    assert row["bypass_suspected"] is False


def test_a_client_with_no_recorded_address_is_never_suspected(
    tmp_path: Path,
) -> None:
    """DISQUALIFIER 5. Every pre-v9 report has source_addr NULL.

    An upgraded installation must not light up with accusations against every
    machine simply because their older reports predate the correlation column.
    """
    client, settings = make_client(tmp_path)
    conn = conn_for(settings)
    try:
        add_report(conn, "legacy-pc", source_addr=None)
        set_feed_age(conn, days_old=10)
    finally:
        conn.close()

    row = one_client(client)
    assert row["source_addrs"] == []
    assert row["bypass_suspected"] is False


def test_only_the_bypassing_client_is_flagged_among_several(
    tmp_path: Path,
) -> None:
    client, settings = make_client(tmp_path)
    conn = conn_for(settings)
    try:
        add_report(conn, "good-pc", source_addr="10.0.0.10")
        add_report(conn, "ghost-pc", source_addr="10.0.0.11")
        set_feed_age(conn, days_old=10)
        add_cache_stats(conn, "10.0.0.10", last_seen=ago(hours=1))
    finally:
        conn.close()

    body = client.get("/v1/clients", headers=AUTH).json()
    flagged = {row["client_id"]: row["bypass_suspected"] for row in body}
    assert flagged == {"good-pc": False, "ghost-pc": True}
