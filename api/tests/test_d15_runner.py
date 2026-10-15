"""WP D15-RUNNER: the prefill runner flagged as a client.

Three layers:

* ``runner_identity``: tokens, marker validation (the anti-spoof promise),
  address periods and their day-aligned windows (how identity expires);
* ``runner_mark``: the runner finds vault-core like SteamPrefill and sends
  the marker (fake resolver/fetcher, plus one real HTTP round trip);
* the sweep and the endpoints: a marker line is not traffic, and the
  runner's rows leave every client figure while ``bytes_served`` keeps them
  -- with ``/v1/throughput``, the series and ``/v1/clients/overview``
  adding up to the same totals.

Synthetic addresses and ids only.
"""

from __future__ import annotations

import http.server
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from tests.test_event_sweep import event_line, make_settings, write_log
from vault_api import (
    event_sweep,
    jobs,
    mapping,
    prefill,
    runner_identity,
    runner_mark,
    traffic,
)
from vault_api.config import Settings
from vault_api.db import get_connection, init_db
from vault_api.main import create_app
from vault_api.prefill_runner import PrefillRunner
from vault_api.worker import PrefillWorker

AUTH = {"X-Api-Key": TEST_API_KEY}
UTC = timezone.utc

PC = "10.0.0.5"
FRAME = "10.0.0.9"
RUNNER = "172.30.0.1"


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def open_conn(settings: Settings) -> sqlite3.Connection:
    init_db(settings.db_path)
    return get_connection(settings.db_path)


def mark_line(token: str, *, moment: datetime, addr: str = RUNNER, status: str = "204") -> str:
    return event_line(
        time=iso(moment),
        addr=addr,
        cache_status="RUNNER",
        depot="-",
        uri=runner_identity.MARK_PATH_PREFIX + token,
        bytes_sent="150",
        http_status=status,
    )


# ---------------------------------------------------------------------------
# runner_identity: tokens and validation
# ---------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path: Path):
    settings = make_settings(tmp_path, cooldown=0)
    connection = open_conn(settings)
    try:
        yield connection
    finally:
        connection.close()


def test_schema_v27_has_the_runner_tables(conn: sqlite3.Connection) -> None:
    from vault_api.db import SCHEMA_VERSION

    assert SCHEMA_VERSION >= 27
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {"runner_marks", "runner_address_periods"} <= tables


def test_init_db_adds_the_tables_to_a_file_already_stamped_27(tmp_path: Path) -> None:
    """Don't trust the stamp: a parallel branch may stamp v27 first."""
    db_path = str(tmp_path / "v.db")
    init_db(db_path)
    conn = get_connection(db_path)
    conn.execute("DROP TABLE runner_marks")
    conn.execute("DROP TABLE runner_address_periods")
    conn.execute("UPDATE schema_version SET version = 27")
    conn.commit()
    conn.close()
    init_db(db_path)
    init_db(db_path)  # idempotent
    conn = get_connection(db_path)
    try:
        conn.execute("SELECT * FROM runner_marks").fetchall()
        conn.execute("SELECT * FROM runner_address_periods").fetchall()
    finally:
        conn.close()


def test_tokens_are_128_bit_hex_and_unique(conn: sqlite3.Connection) -> None:
    tokens = {runner_identity.issue_token(conn, "r") for _ in range(50)}
    assert len(tokens) == 50
    assert all(runner_identity.is_valid_token(t) for t in tokens)
    assert all(len(t) == 32 for t in tokens)


@pytest.mark.parametrize(
    "uri",
    [
        "/steamhangar/runner-mark/" + "A" * 32,  # upper case
        "/steamhangar/runner-mark/" + "a" * 31,
        "/steamhangar/runner-mark/" + "a" * 33,
        "/steamhangar/runner-mark/" + "a" * 31 + "g",
        "/steamhangar/runner-mark/" + "a" * 16 + "/" + "a" * 15,
        "/steamhangar/runner-mark/" + "١" * 32,  # Arabic-Indic digits
        "/depot/1/chunk/" + "a" * 32,
    ],
)
def test_token_from_uri_rejects_everything_but_32_lowercase_hex(uri: str) -> None:
    assert runner_identity.token_from_uri(uri) is None


def test_issue_token_prunes_tokens_older_than_a_week(conn: sqlite3.Connection) -> None:
    now = datetime.now(UTC)
    old = runner_identity.issue_token(conn, "r", now - timedelta(days=8))
    kept = runner_identity.issue_token(conn, "r", now - timedelta(days=6))
    runner_identity.issue_token(conn, "r", now)
    stored = {row["token"] for row in conn.execute("SELECT token FROM runner_marks")}
    assert old not in stored and kept in stored


def _apply(conn: sqlite3.Connection, *marks: runner_identity.Mark) -> runner_identity.MarkResult:
    with jobs.immediate_transaction(conn):
        return runner_identity.apply_marks(conn, list(marks))


def _periods(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    return [
        (row["addr"], row["first_marked_at"], row["last_marked_at"])
        for row in conn.execute(
            "SELECT addr, first_marked_at, last_marked_at FROM runner_address_periods ORDER BY id"
        )
    ]


def test_a_valid_marker_flags_its_address_and_consumes_the_token(
    conn: sqlite3.Connection,
) -> None:
    now = datetime(2026, 10, 14, 12, 0, 0, tzinfo=UTC)
    token = runner_identity.issue_token(conn, "r", now)
    result = _apply(conn, runner_identity.Mark(token, RUNNER, stamp(now + timedelta(seconds=2))))
    assert (result.accepted, result.rejected) == (1, 0)
    assert _periods(conn) == [(RUNNER, "2026-10-14T12:00:02Z", "2026-10-14T12:00:02Z")]
    row = conn.execute("SELECT consumed_at, consumed_addr FROM runner_marks").fetchone()
    assert row["consumed_addr"] == RUNNER and row["consumed_at"] is not None


def test_an_unknown_token_flags_nothing(conn: sqlite3.Connection) -> None:
    """The anti-spoof promise: a LAN host can send the path, but without a
    token the runner stored it marks nothing -- not even itself."""
    now = datetime.now(UTC)
    runner_identity.issue_token(conn, "r", now)
    result = _apply(conn, runner_identity.Mark("0" * 32, FRAME, stamp(now)))
    assert (result.accepted, result.rejected) == (0, 1)
    assert _periods(conn) == []


def test_a_replayed_token_flags_nothing_new(conn: sqlite3.Connection) -> None:
    now = datetime.now(UTC)
    token = runner_identity.issue_token(conn, "r", now)
    _apply(conn, runner_identity.Mark(token, RUNNER, stamp(now)))
    result = _apply(conn, runner_identity.Mark(token, FRAME, stamp(now + timedelta(seconds=5))))
    assert (result.accepted, result.rejected) == (0, 1)
    assert [p[0] for p in _periods(conn)] == [RUNNER]


@pytest.mark.parametrize(
    "offset",
    [timedelta(minutes=-6), timedelta(hours=1, seconds=1)],
    ids=["before-issue", "after-validity"],
)
def test_a_marker_outside_the_validity_window_is_rejected(
    conn: sqlite3.Connection, offset: timedelta
) -> None:
    now = datetime(2026, 10, 14, 12, 0, 0, tzinfo=UTC)
    token = runner_identity.issue_token(conn, "r", now)
    result = _apply(conn, runner_identity.Mark(token, RUNNER, stamp(now + offset)))
    assert result.rejected == 1 and _periods(conn) == []


@pytest.mark.parametrize(
    "offset", [timedelta(minutes=-5), timedelta(hours=1)], ids=["earliest", "latest"]
)
def test_the_validity_window_edges_are_inclusive(
    conn: sqlite3.Connection, offset: timedelta
) -> None:
    now = datetime(2026, 10, 14, 12, 0, 0, tzinfo=UTC)
    token = runner_identity.issue_token(conn, "r", now)
    assert _apply(conn, runner_identity.Mark(token, RUNNER, stamp(now + offset))).accepted == 1


def _mark_at(conn: sqlite3.Connection, moment: datetime, addr: str = RUNNER) -> None:
    token = runner_identity.issue_token(conn, "r", moment)
    assert _apply(conn, runner_identity.Mark(token, addr, stamp(moment))).accepted == 1


def test_markers_within_the_hold_extend_one_period_a_later_one_opens_another(
    conn: sqlite3.Connection,
) -> None:
    day = datetime(2026, 10, 1, 3, 0, 0, tzinfo=UTC)
    _mark_at(conn, day)
    _mark_at(conn, day + timedelta(days=7))  # exactly HOLD later: extends
    _mark_at(conn, day + timedelta(days=14, seconds=1))  # > HOLD after the last
    assert _periods(conn) == [
        (RUNNER, "2026-10-01T03:00:00Z", "2026-10-08T03:00:00Z"),
        (RUNNER, "2026-10-15T03:00:01Z", "2026-10-15T03:00:01Z"),
    ]


def test_windows_are_day_aligned_and_end_a_day_after_the_hold(
    conn: sqlite3.Connection,
) -> None:
    _mark_at(conn, datetime(2026, 10, 14, 21, 30, 0, tzinfo=UTC))
    (window,) = runner_identity.windows(conn)
    assert window.addr == RUNNER
    assert window.start == "2026-10-14T00:00:00Z"
    assert window.end == "2026-10-22T00:00:00Z"  # day(14th 21:30 + 7 d) + 1 d
    assert runner_identity.current_addrs((window,), datetime(2026, 10, 21, 23, 59, tzinfo=UTC)) == [RUNNER]
    assert runner_identity.current_addrs((window,), datetime(2026, 10, 22, 0, 0, tzinfo=UTC)) == []


def test_scope_sides_partition_the_rows(conn: sqlite3.Connection) -> None:
    window = runner_identity.RunnerWindow(
        addr=RUNNER, start="2026-10-14T00:00:00Z", end="2026-10-15T00:00:00Z",
        first_marked_at="2026-10-14T01:00:00Z", last_marked_at="2026-10-14T01:00:00Z",
    )
    for addr, bucket, size in (
        (RUNNER, "2026-10-13T23:55:00Z", 1),  # before the window
        (RUNNER, "2026-10-14T00:00:00Z", 10),  # first bucket inside
        (RUNNER, "2026-10-14T23:55:00Z", 100),  # last bucket inside
        (RUNNER, "2026-10-15T00:00:00Z", 1000),  # end is exclusive
        (PC, "2026-10-14T12:00:00Z", 10000),  # other address
    ):
        conn.execute(
            """INSERT INTO client_traffic (tier, bucket_at, client_addr, requests,
               bytes_served, last_seen) VALUES ('5m', ?, ?, 1, ?, ?)""",
            (bucket, addr, size, bucket),
        )
    conn.commit()
    start = "2026-10-01T00:00:00Z"
    runner = traffic.scoped_totals(conn, "5m", start, traffic.runner_scope((window,)))
    clients = traffic.scoped_totals(conn, "5m", start, traffic.clients_scope((window,)))
    everything = traffic.scoped_totals(conn, "5m", start, traffic.ALL)
    assert runner.bytes_served == 110
    assert clients.bytes_served == 11001
    assert runner.bytes_served + clients.bytes_served == everything.bytes_served
    # No windows: the runner side is empty, the clients side is everything.
    assert traffic.scoped_totals(conn, "5m", start, traffic.runner_scope(())).bytes_served == 0
    assert traffic.scoped_totals(conn, "5m", start, traffic.clients_scope(())).bytes_served == 11111


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------


def test_the_sweep_turns_a_marker_line_into_a_runner_period_and_no_traffic(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path, cooldown=0)
    now = datetime.now(UTC)
    conn = open_conn(settings)
    try:
        token = runner_identity.issue_token(conn, "r", now - timedelta(minutes=1))
        write_log(
            tmp_path / "event.log",
            mark_line(token, moment=now - timedelta(minutes=1)),
            mark_line("f" * 32, moment=now - timedelta(minutes=1), addr=FRAME),  # forged
            event_line(  # status RUNNER on a non-marker path
                time=iso(now), addr=FRAME, cache_status="RUNNER", depot="-",
                uri="/steamhangar/runner-mark/nope", http_status="204",
            ),
            mark_line(token, moment=now, status="404"),  # not served
        )
        outcome = event_sweep.sweep_once(conn, settings, now)
        assert outcome.runner_marks_accepted == 1
        assert outcome.runner_marks_rejected == 1
        assert outcome.skip_reasons == {"bad-runner-mark": 2}
        assert [p[0] for p in _periods(conn)] == [RUNNER]
        # A marker is not traffic: no tier row, no client_cache_stats row.
        assert conn.execute("SELECT COUNT(*) FROM client_traffic").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM client_cache_stats").fetchone()[0] == 0
    finally:
        conn.close()


def test_an_older_vault_api_parser_would_have_skipped_a_marker_line() -> None:
    """The marker status is new: before this package parse_line refused it
    (bad-cache-status). It is accepted now, and still only RUNNER."""
    line, reason = event_sweep.parse_line(mark_line("a" * 32, moment=datetime.now(UTC)))
    assert line is not None and reason == "" and line.cache_status == "RUNNER"
    line, reason = event_sweep.parse_line(
        event_line(cache_status="RUNNERX", depot="-", uri="/x")
    )
    assert line is None and reason == "bad-cache-status"


# ---------------------------------------------------------------------------
# The endpoints: figures and consistency
# ---------------------------------------------------------------------------


def _build_feed(tmp_path: Path, *, old_runner_traffic: bool = False):
    """PC (HIT 1000 on depot 100 / app 440), FRAME (MISS 600 on depot 200 /
    app 570) and the runner (verify HIT 3000 + fill MISS 2000 on depot 100),
    all within the last minutes. ``old_runner_traffic`` adds 7000 bytes from
    the runner's address 20 days ago, before its first marker."""
    settings = make_settings(tmp_path, cooldown=0)
    now = datetime.now(UTC)
    conn = open_conn(settings)
    mapping.upsert_mapping(conn, depotid=100, appid=440, name=None)
    mapping.upsert_mapping(conn, depotid=200, appid=570, name=None)
    token = runner_identity.issue_token(conn, "r", now - timedelta(minutes=3))
    lines = [
        mark_line(token, moment=now - timedelta(minutes=3)),
        event_line(time=iso(now - timedelta(minutes=2)), addr=RUNNER,
                   cache_status="HIT", depot="100", bytes_sent="3000"),
        event_line(time=iso(now - timedelta(minutes=2)), addr=RUNNER,
                   cache_status="MISS", depot="100", bytes_sent="2000"),
        event_line(time=iso(now - timedelta(minutes=2)), addr=PC,
                   cache_status="HIT", depot="100", bytes_sent="1000"),
        event_line(time=iso(now - timedelta(minutes=1)), addr=FRAME,
                   cache_status="MISS", depot="200", bytes_sent="600"),
    ]
    if old_runner_traffic:
        lines.insert(0, event_line(time=iso(now - timedelta(days=20)), addr=RUNNER,
                                   cache_status="HIT", depot="200", bytes_sent="7000"))
    write_log(tmp_path / "event.log", *lines)
    event_sweep.sweep_once(conn, settings, now)
    return settings, conn, now


@pytest.fixture
def feed(tmp_path: Path):
    settings, conn, now = _build_feed(tmp_path)
    try:
        yield settings, conn, now
    finally:
        conn.close()


@pytest.fixture
def old_feed(tmp_path: Path):
    settings, conn, now = _build_feed(tmp_path, old_runner_traffic=True)
    try:
        yield settings, conn, now
    finally:
        conn.close()


def _client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


def test_throughput_keeps_the_runner_out_of_every_client_figure(feed) -> None:
    settings, _conn, _now = feed
    body = _client(settings).get("/v1/throughput?range=24h", headers=AUTH).json()
    summary = body["summary"]
    # Everything served, runner included: the cache's real load.
    assert summary["bytes_served"] == 3000 + 2000 + 1000 + 600
    # Clients only.
    assert summary["client_bytes_served"] == 1600
    assert summary["hit_bytes"] == 1000  # "saved by the cache": no runner HITs
    assert summary["miss_bytes"] == 600
    assert (summary["hits"], summary["misses"], summary["requests"]) == (1, 1, 2)
    assert summary["byte_hit_rate"] == pytest.approx(1000 / 1600)
    assert summary["hit_rate"] == pytest.approx(0.5)
    assert summary["active_clients"] == 2
    # The runner's own share.
    assert summary["prefill_runner"] == {
        "bytes_served": 5000, "hit_bytes": 3000, "miss_bytes": 2000,
        "requests": 2, "hits": 1, "misses": 1, "errors": 0,
    }
    assert summary["bytes_served"] == (
        summary["client_bytes_served"] + summary["prefill_runner"]["bytes_served"]
    )
    # WP TILE-INTERNET: all-scope miss bytes, the runner's included.
    assert summary["internet_bytes"] == 600 + 2000
    assert summary["internet_bytes"] == (
        summary["miss_bytes"] + summary["prefill_runner"]["miss_bytes"]
    )
    assert body["active_clients_now"] == 2
    assert body["runner_active_now"] is True


@pytest.mark.parametrize("which", ["feed", "old_feed"])
def test_series_add_up_to_the_summary(request, which: str) -> None:
    settings, _conn, _now = request.getfixturevalue(which)
    client = _client(settings)
    for range_name in ("24h", "7d", "30d", "all"):
        summary = client.get(f"/v1/throughput?range={range_name}", headers=AUTH).json()["summary"]
        points = client.get(
            f"/v1/throughput/series?range={range_name}", headers=AUTH
        ).json()["points"]
        assert sum(p["bytes_served"] for p in points) == summary["bytes_served"], range_name
        assert sum(p["hit_bytes"] for p in points) == summary["hit_bytes"], range_name
        assert sum(p["miss_bytes"] for p in points) == summary["miss_bytes"], range_name
        assert sum(p["runner_bytes"] for p in points) == (
            summary["prefill_runner"]["bytes_served"]
        ), range_name


def test_overview_shows_one_prefill_runner_entry_and_no_unknown_address(feed) -> None:
    settings, _conn, _now = feed
    body = _client(settings).get("/v1/clients/overview?range=24h", headers=AUTH).json()
    by_key = {c["key"]: c for c in body["clients"]}
    assert set(by_key) == {f"addr:{PC}", f"addr:{FRAME}", "runner"}
    runner = by_key["runner"]
    assert runner["kind"] == "runner"
    assert runner["display_name"] == "Prefill runner"
    assert runner["name"] is None and runner["name_addr"] is None
    assert runner["addresses"] == [RUNNER]
    assert runner["connected"] is True and runner["cache_active"] is True
    assert runner["stats"]["bytes_served"] == 5000
    assert runner["stats"]["hit_bytes"] == 3000
    assert [a["appid"] for a in runner["recent_apps"]] == [440]
    assert runner["loading_now"]["loading"] is True


@pytest.mark.parametrize("range_name", ["24h", "7d", "30d", "all"])
@pytest.mark.parametrize("which", ["feed", "old_feed"])
def test_overview_entries_add_up_to_throughput(request, which: str, range_name: str) -> None:
    """Every traffic row is in exactly one entry: the entries' stats sum to
    /v1/throughput's totals, and the runner entry equals prefill_runner."""
    settings, _conn, _now = request.getfixturevalue(which)
    client = _client(settings)
    summary = client.get(f"/v1/throughput?range={range_name}", headers=AUTH).json()["summary"]
    clients = client.get(
        f"/v1/clients/overview?range={range_name}", headers=AUTH
    ).json()["clients"]
    stats = [c["stats"] for c in clients if c["stats"] is not None]
    assert sum(s["bytes_served"] for s in stats) == summary["bytes_served"]
    assert sum(s["hit_bytes"] for s in stats) == (
        summary["hit_bytes"] + summary["prefill_runner"]["hit_bytes"]
    )
    (runner,) = [c for c in clients if c["kind"] == "runner"]
    assert runner["stats"]["bytes_served"] == summary["prefill_runner"]["bytes_served"]
    non_runner = [c for c in clients if c["kind"] != "runner" and c["stats"]]
    assert sum(c["stats"]["bytes_served"] for c in non_runner) == summary["client_bytes_served"]


def test_traffic_before_the_first_marker_stays_with_the_address(old_feed) -> None:
    """20 days ago the runner's address was not known to be the runner: in
    the 30-day range that traffic is an ordinary address entry, next to the
    runner entry for today's traffic of the same address."""
    settings, _conn, _now = old_feed
    body = _client(settings).get("/v1/clients/overview?range=30d", headers=AUTH).json()
    by_key = {c["key"]: c for c in body["clients"]}
    assert by_key[f"addr:{RUNNER}"]["stats"]["bytes_served"] == 7000
    assert by_key[f"addr:{RUNNER}"]["cache_active"] is False
    assert by_key["runner"]["stats"]["bytes_served"] == 5000
    summary = _client(settings).get("/v1/throughput?range=30d", headers=AUTH).json()["summary"]
    assert summary["hit_bytes"] == 1000 + 7000
    assert summary["active_clients"] == 3  # PC, FRAME, the runner's old address


def test_an_expired_runner_address_counts_as_a_client_again(tmp_path: Path) -> None:
    """Identity expires: a marker 10 days ago does not flag today's traffic
    (window end = day(last marker + 7 d) + 1 d)."""
    settings = make_settings(tmp_path, cooldown=0)
    now = datetime.now(UTC)
    conn = open_conn(settings)
    try:
        _mark_at(conn, now - timedelta(days=10))
        write_log(
            tmp_path / "event.log",
            event_line(time=iso(now - timedelta(minutes=1)), addr=RUNNER,
                       cache_status="HIT", depot="100", bytes_sent="900"),
        )
        event_sweep.sweep_once(conn, settings, now)
    finally:
        conn.close()
    client = _client(settings)
    summary = client.get("/v1/throughput?range=24h", headers=AUTH).json()
    assert summary["summary"]["hit_bytes"] == 900
    assert summary["summary"]["prefill_runner"]["bytes_served"] == 0
    assert summary["active_clients_now"] == 1
    assert summary["runner_active_now"] is False
    by_key = {
        c["key"]: c
        for c in client.get("/v1/clients/overview?range=24h", headers=AUTH).json()["clients"]
    }
    assert by_key[f"addr:{RUNNER}"]["stats"]["bytes_served"] == 900
    # The old window still reaches into the 30-day listing period, so the
    # runner stays listed -- idle, with nothing in this range.
    assert by_key["runner"]["stats"]["bytes_served"] == 0
    assert by_key["runner"]["cache_active"] is False


def test_without_any_marker_nothing_changes(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, cooldown=0)
    now = datetime.now(UTC)
    conn = open_conn(settings)
    try:
        write_log(
            tmp_path / "event.log",
            event_line(time=iso(now - timedelta(minutes=1)), addr=RUNNER,
                       cache_status="HIT", depot="100", bytes_sent="900"),
        )
        event_sweep.sweep_once(conn, settings, now)
    finally:
        conn.close()
    client = _client(settings)
    summary = client.get("/v1/throughput?range=24h", headers=AUTH).json()["summary"]
    assert summary["hit_bytes"] == 900 and summary["active_clients"] == 1
    assert summary["prefill_runner"]["bytes_served"] == 0
    clients = client.get("/v1/clients/overview", headers=AUTH).json()["clients"]
    assert [c["kind"] for c in clients] == ["address"]


# ---------------------------------------------------------------------------
# runner_mark: finding vault-core, sending the marker
# ---------------------------------------------------------------------------


class FakeNet:
    """Resolver + fetcher over a dict; records every request."""

    def __init__(self, names: dict[str, str], answers: dict[tuple[str, str], tuple[int, dict]]):
        self.names = names
        self.answers = answers
        self.requests: list[tuple[str, int, str]] = []

    def resolve(self, host: str) -> str | None:
        return self.names.get(host)

    def fetch(self, ip: str, port: int, path: str, timeout: float):
        self.requests.append((ip, port, path))
        key = (ip, "mark" if path.startswith(runner_identity.MARK_PATH_PREFIX) else path)
        return self.answers.get(key)


HEARTBEAT_OK = (200, {"x-lancache-processed-by": "steamhangar"})
MARK_OK = (204, {"x-steamhangar-runner-mark": "logged"})


def test_announce_mirrors_steamprefills_candidate_order_and_rules(
    conn: sqlite3.Connection,
) -> None:
    """Public IPs are skipped, a private address without the heartbeat
    header is skipped, the first qualifying one gets the marker."""
    net = FakeNet(
        names={
            "lancache.steamcontent.com": "203.0.113.7",  # public: never contacted
            "localhost": "127.0.0.1",  # answers, but no heartbeat header
            "172.17.0.1": "172.17.0.1",
            "box": "172.30.0.9",
        },
        answers={
            ("127.0.0.1", "/lancache-heartbeat"): (200, {}),
            ("172.17.0.1", "/lancache-heartbeat"): HEARTBEAT_OK,
            ("172.17.0.1", "mark"): MARK_OK,
            ("172.30.0.9", "/lancache-heartbeat"): HEARTBEAT_OK,
        },
    )
    outcome = runner_mark.announce(
        conn, "r", resolve=net.resolve, fetch=net.fetch,
        names=["lancache.steamcontent.com", "localhost", "172.17.0.1", "box"],
    )
    assert outcome == runner_mark.AnnounceOutcome("sent", "172.17.0.1")
    contacted = [(ip, path) for ip, _port, path in net.requests]
    assert contacted[:2] == [
        ("127.0.0.1", "/lancache-heartbeat"),
        ("172.17.0.1", "/lancache-heartbeat"),
    ]
    (mark_ip, mark_path) = contacted[2]
    assert mark_ip == "172.17.0.1" and len(contacted) == 3
    token = runner_identity.token_from_uri(mark_path)
    stored = conn.execute("SELECT runner_id FROM runner_marks WHERE token = ?", (token,)).fetchone()
    assert stored is not None and stored["runner_id"] == "r"
    assert all(ip != "203.0.113.7" for ip, _p, _q in net.requests)


def test_default_candidates_are_steamprefills_list_then_the_hostname() -> None:
    names = runner_mark.candidates()
    assert names[:3] == ["lancache.steamcontent.com", "localhost", "172.17.0.1"]
    assert len(names) == 4


@pytest.mark.parametrize(
    "address,private",
    [
        ("10.1.2.3", True), ("172.16.0.1", True), ("172.31.255.255", True),
        ("192.168.10.15", True), ("127.0.0.1", True),
        ("172.32.0.1", False), ("100.64.0.1", False), ("169.254.1.1", False),
        ("8.8.8.8", False), ("::1", False), ("fd00::1", False), ("nonsense", False),
    ],
)
def test_only_rfc1918_and_loopback_ipv4_qualify(address: str, private: bool) -> None:
    assert runner_mark.is_private_v4(address) is private


def test_announce_without_a_cache_sends_nothing_and_stores_no_token(
    conn: sqlite3.Connection,
) -> None:
    net = FakeNet(names={"localhost": "127.0.0.1"}, answers={})
    outcome = runner_mark.announce(conn, "r", resolve=net.resolve, fetch=net.fetch, names=["localhost"])
    assert outcome.state == "no-cache"
    assert conn.execute("SELECT COUNT(*) FROM runner_marks").fetchone()[0] == 0


def test_a_failed_discovery_is_cached_for_five_minutes(conn: sqlite3.Connection) -> None:
    """A run of jobs without a reachable cache must not pay the full
    discovery (up to 4 x 3 s) per job: the negative result is reused for
    NEGATIVE_CACHE_SECONDS, then discovery runs again."""
    assert runner_mark.NEGATIVE_CACHE_SECONDS == 300.0
    clock = [1000.0]
    lookups: list[str] = []

    def resolve(host: str) -> str | None:
        lookups.append(host)
        return None

    def announce() -> str:
        return runner_mark.announce(
            conn, "r", resolve=resolve, names=["localhost"], clock=lambda: clock[0]
        ).state

    assert announce() == "no-cache" and lookups == ["localhost"]
    clock[0] += 299.0
    assert announce() == "no-cache-cached" and lookups == ["localhost"]
    clock[0] += 1.0
    assert announce() == "no-cache" and lookups == ["localhost", "localhost"]


def test_a_found_cache_is_not_cached(conn: sqlite3.Connection) -> None:
    net = FakeNet(
        names={"localhost": "127.0.0.1"},
        answers={
            ("127.0.0.1", "/lancache-heartbeat"): HEARTBEAT_OK,
            ("127.0.0.1", "mark"): MARK_OK,
        },
    )
    for _ in range(2):
        outcome = runner_mark.announce(conn, "r", resolve=net.resolve, fetch=net.fetch, names=["localhost"])
        assert outcome.state == "sent"
    assert [p for _i, _o, p in net.requests].count("/lancache-heartbeat") == 2


def test_announce_against_an_old_vault_core_reports_not_supported(
    conn: sqlite3.Connection,
) -> None:
    net = FakeNet(
        names={"localhost": "127.0.0.1"},
        answers={
            ("127.0.0.1", "/lancache-heartbeat"): HEARTBEAT_OK,
            ("127.0.0.1", "mark"): (404, {}),
        },
    )
    outcome = runner_mark.announce(conn, "r", resolve=net.resolve, fetch=net.fetch, names=["localhost"])
    assert outcome.state == "not-supported"


def test_announce_when_the_marker_request_fails(conn: sqlite3.Connection) -> None:
    net = FakeNet(
        names={"localhost": "127.0.0.1"},
        answers={("127.0.0.1", "/lancache-heartbeat"): HEARTBEAT_OK},
    )
    outcome = runner_mark.announce(conn, "r", resolve=net.resolve, fetch=net.fetch, names=["localhost"])
    assert outcome.state == "unreachable"


def test_announce_with_a_broken_database_does_not_raise(tmp_path: Path) -> None:
    bare = sqlite3.connect(str(tmp_path / "empty.db"))  # no runner_marks table
    net = FakeNet(
        names={"localhost": "127.0.0.1"},
        answers={("127.0.0.1", "/lancache-heartbeat"): HEARTBEAT_OK},
    )
    try:
        outcome = runner_mark.announce(bare, "r", resolve=net.resolve, fetch=net.fetch, names=["localhost"])
    finally:
        bare.close()
    assert outcome.state == "db-error"
    assert all(not p.startswith(runner_identity.MARK_PATH_PREFIX) for _i, _o, p in net.requests)


def test_announce_never_raises_when_discovery_blows_up(conn: sqlite3.Connection) -> None:
    def boom(host: str) -> str | None:
        raise RuntimeError("resolver exploded")

    assert runner_mark.announce(conn, "r", resolve=boom, names=["localhost"]).state == "no-cache"


class _CoreLike(http.server.BaseHTTPRequestHandler):
    """The two locations vault-core answers, as nginx.conf defines them."""

    seen: list[str] = []

    def do_GET(self) -> None:  # noqa: N802
        type(self).seen.append(self.path)
        if self.path == "/lancache-heartbeat":
            self.send_response(200)
            self.send_header("X-LanCache-Processed-By", "steamhangar")
        elif self.path.startswith(runner_identity.MARK_PATH_PREFIX):
            self.send_response(204)
            self.send_header("X-SteamHangar-Runner-Mark", "logged")
        else:
            self.send_response(404)
        self.end_headers()

    def log_message(self, *args: object) -> None:
        pass


def test_announce_over_real_http(conn: sqlite3.Connection) -> None:
    _CoreLike.seen = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _CoreLike)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        outcome = runner_mark.announce(
            conn, "r", resolve=lambda host: "127.0.0.1",
            port=server.server_address[1], names=["localhost"],
        )
    finally:
        server.shutdown()
        server.server_close()
    assert outcome.state == "sent"
    assert _CoreLike.seen[0] == "/lancache-heartbeat"
    assert runner_identity.token_from_uri(_CoreLike.seen[1]) is not None


# ---------------------------------------------------------------------------
# Wiring: the marker goes out before SteamPrefill, in both modes
# ---------------------------------------------------------------------------


def _settings(tmp_path: Path, mode: str) -> Settings:
    return Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
        prefill_mode=mode,
        runner_heartbeat_seconds=0.0,
        runner_lease_timeout_seconds=8.0,
        prefill_timeout_seconds=30,
    )


def _record_order(monkeypatch) -> list[str]:
    order: list[str] = []

    def fake_announce(conn, runner_id, **kwargs):
        order.append(f"announce:{runner_id}")
        return runner_mark.AnnounceOutcome("sent", "127.0.0.1")

    def fake_run_prefill(**kwargs):
        order.append("run_prefill")
        return prefill.PrefillResult(True, None, 0, "fake run")

    monkeypatch.setattr(runner_mark, "announce", fake_announce)
    monkeypatch.setattr(prefill, "run_prefill", fake_run_prefill)
    return order


def test_the_queue_runner_announces_before_running_steamprefill(
    tmp_path: Path, monkeypatch
) -> None:
    settings = _settings(tmp_path, "queue")
    init_db(settings.db_path)
    conn = get_connection(settings.db_path)
    try:
        jobs.enqueue_prefill(conn, 440)
        claimed = jobs.claim_next_job(conn)
        jobs.handoff_run(conn, int(claimed["id"]), False, "{}")
        job = jobs.claim_run(conn, "rid")
        order = _record_order(monkeypatch)
        PrefillRunner(settings, runner_id="rid")._execute(conn, job)
    finally:
        conn.close()
    assert order == ["announce:rid", "run_prefill"]


def test_the_subprocess_worker_announces_before_running_steamprefill(
    tmp_path: Path, monkeypatch
) -> None:
    settings = _settings(tmp_path, "subprocess")
    init_db(settings.db_path)
    conn = get_connection(settings.db_path)
    try:
        jobs.enqueue_prefill(conn, 440)
        job = jobs.claim_next_job(conn)
        order = _record_order(monkeypatch)
        PrefillWorker(settings)._execute_prefill(conn, job)
    finally:
        conn.close()
    assert order == ["announce:vault-api", "run_prefill"]
