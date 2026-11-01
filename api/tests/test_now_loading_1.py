"""WP NOW-LOADING-1: what is loading now, one row per source.

``GET /v1/throughput`` (``now.loading_sources``) and ``GET
/v1/throughput/live`` (``loading_sources``) name the sources of the traffic
in the live "now" window: the prefill runner with its running job and phase,
each client as the Clients page names it with its games, and the bytes no
address explains. Both read the window once (``LiveTier.now_view``), so the
rows agree with the speed, the pill's runner figures and the device count.

Also GRAPH-RECENT-1 nit N2: the live endpoint's running 5 s point divides by
the seconds elapsed in it.

Synthetic event lines, an injected clock, the real sweep; no threads.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from tests.test_event_sweep import append_log, event_line, write_log
from tests.test_graph_recent_1 import (  # noqa: F401 - ``served`` is a fixture
    AUTH,
    NOW,
    PC,
    RUNNER,
    iso,
    served,
    sweep,
    throughput,
)
from vault_api import agent_reports, client_names, jobs, live_tier, mapping, runner_identity
from vault_api.live_tier import LiveTier

#: 12:00:07, so the window is 11:59:50 .. now: 15 s + 2 s of the running cell.
WINDOW = 17
OTHER = "10.0.0.6"
NEW = "10.0.0.7"
#: Every field of a source row; the web and (later) Android read these.
ROW_KEYS = {
    "kind",
    "key",
    "name",
    "addresses",
    "known",
    "bytes_per_second",
    "hit_bytes_per_second",
    "miss_bytes_per_second",
    "apps",
    "unattributed_bytes_per_second",
    "phase",
    "job",
}


def chunk(ago: float, size: int, *, addr: str, depot: str = "100", status: str = "HIT") -> str:
    return event_line(
        time=iso(NOW - timedelta(seconds=ago)),
        addr=addr,
        cache_status=status,
        depot=depot,
        bytes_sent=str(size),
    )


def live(client) -> dict:
    return client.get("/v1/throughput/live", headers=AUTH).json()


def sources(client, range_name: str = "1h") -> list[dict]:
    """The rows of ``/v1/throughput``, checked to equal the live endpoint's."""
    now = throughput(client, range_name)["now"]
    rows = now["loading_sources"]
    assert live(client)["loading_sources"] == rows
    return rows


@pytest.fixture
def setup(served, tmp_path: Path):
    """A fresh feed (one sweep 4 minutes ago), two games, a runner window."""
    settings, client, clock, conn = served
    mapping.upsert_mapping(conn, depotid=100, appid=440, name=None)
    mapping.upsert_mapping(conn, depotid=200, appid=570, name=None)
    # Depot 300 is shared by both games: never one game's.
    mapping.upsert_mapping(conn, depotid=300, appid=440, name=None)
    mapping.upsert_mapping(conn, depotid=300, appid=570, name=None)
    conn.execute("UPDATE apps SET name = 'Game Four Forty' WHERE appid = 440")
    conn.execute("UPDATE apps SET name = 'Game Five Seventy' WHERE appid = 570")
    conn.commit()
    log = tmp_path / "event.log"
    write_log(log)
    sweep(conn, settings, NOW - timedelta(minutes=4))
    runner_identity._extend_period(conn, RUNNER, NOW - timedelta(minutes=2))  # noqa: SLF001
    conn.commit()
    return settings, client, clock, conn, log


def running_job(conn, appid: int = 440) -> int:
    job, _created = jobs.enqueue_prefill(conn, appid)
    claimed = jobs.claim_next_job(conn)
    assert claimed is not None and claimed["id"] == job["id"]
    return int(job["id"])


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------


def test_a_downloading_runner_names_its_job_game_and_phase(setup) -> None:
    _settings, client, _clock, conn, log = setup
    job_id = running_job(conn, 440)
    append_log(
        log,
        chunk(3, 6000, addr=RUNNER, status="MISS"),
        chunk(2, 1000, addr=RUNNER, status="HIT"),
    )
    rows = sources(client)
    assert len(rows) == 1
    row = rows[0]
    assert set(row) == ROW_KEYS
    assert row["kind"] == "runner" and row["key"] == "runner"
    assert row["name"] == "Prefill runner" and row["addresses"] == [RUNNER]
    assert row["phase"] == "download"
    assert row["bytes_per_second"] == pytest.approx(7000 / WINDOW)
    assert row["hit_bytes_per_second"] == pytest.approx(1000 / WINDOW)
    assert row["miss_bytes_per_second"] == pytest.approx(6000 / WINDOW)
    assert row["apps"] == [
        {"appid": 440, "name": "Game Four Forty", "bytes_per_second": pytest.approx(7000 / WINDOW)}
    ]
    assert row["job"] == {
        "job_id": job_id,
        "appid": 440,
        "name": "Game Four Forty",
        "stop_request": None,
    }
    # The same read as the pill's figures and the counts.
    now = throughput(client, "1h")["now"]
    assert now["runner_active"] is True and now["active_clients"] == 0
    assert now["current"]["runner_bytes_per_second"] == pytest.approx(row["bytes_per_second"])


def test_a_verifying_runner_reads_verify_like_the_pill(setup) -> None:
    _settings, client, _clock, conn, log = setup
    running_job(conn, 440)
    append_log(log, chunk(3, 6000, addr=RUNNER, status="HIT"), chunk(1, 2000, addr=RUNNER, status="HIT"))
    (row,) = sources(client, "6h")
    assert row["phase"] == "verify"
    assert row["miss_bytes_per_second"] == 0
    cur = throughput(client, "6h")["now"]["current"]
    # The pill's rule (web heroStatus): runner HITs cover the runner's bytes.
    assert cur["runner_hit_bytes_per_second"] >= cur["runner_bytes_per_second"] > 0


def test_a_runner_with_one_miss_is_downloading(setup) -> None:
    """Mutation guard for the phase rule: a single MISS byte is a download."""
    _settings, client, _clock, conn, log = setup
    append_log(log, chunk(3, 6000, addr=RUNNER, status="HIT"), chunk(1, 1, addr=RUNNER, status="MISS"))
    (row,) = sources(client)
    assert row["phase"] == "download"


def test_runner_traffic_without_a_running_job_says_so(setup) -> None:
    """The screenshot case: the job was cancelled, its last lines are still in
    the window. The row stays (the pill says "Downloading"), without a job."""
    _settings, client, _clock, conn, log = setup
    job_id = running_job(conn, 440)
    assert jobs.cancel_job(conn, job_id).outcome == jobs.CONTROL_REQUESTED
    append_log(log, chunk(2, 5000, addr=RUNNER, status="MISS"))
    # Cancel requested, the runner has not stopped SteamPrefill yet.
    (row,) = sources(client)
    assert row["job"]["job_id"] == job_id and row["job"]["stop_request"] == "cancel"
    # The worker finalized the cancel: no running job, traffic still in the window.
    conn.execute("UPDATE jobs SET status = ?, stop_request = NULL WHERE id = ?", (jobs.STATUS_CANCELLED, job_id))
    conn.commit()
    (row,) = sources(client)
    assert row["kind"] == "runner" and row["job"] is None
    assert row["phase"] == "download"
    assert row["apps"][0]["appid"] == 440


def test_a_running_gc_job_is_not_the_runners_job(setup) -> None:
    _settings, client, _clock, conn, log = setup
    jobs.enqueue_gc(conn, 440, execute=False)
    assert jobs.claim_next_job(conn)["type"] == jobs.JOB_TYPE_GC
    append_log(log, chunk(2, 5000, addr=RUNNER, status="MISS"))
    (row,) = sources(client)
    assert row["job"] is None


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------


def test_two_clients_with_their_games_named_as_on_the_clients_page(setup) -> None:
    _settings, client, _clock, conn, log = setup
    agent_reports.store_report(conn, "pc-one", [440], 10, source_addr=PC)
    client_names.set_name(conn, client_names.KEY_TYPE_IP, OTHER, "Laptop")
    append_log(
        log,
        chunk(3, 30000, addr=PC, depot="100", status="HIT"),
        chunk(2, 1000, addr=PC, depot="300", status="MISS"),  # shared depot
        chunk(2, 9000, addr=OTHER, depot="200", status="MISS"),
        chunk(1, 500, addr=OTHER, depot="100", status="HIT"),
    )
    rows = sources(client)
    # Fastest first (not by key: "addr:" would sort before "agent:").
    assert [r["key"] for r in rows] == ["agent:pc-one", f"addr:{OTHER}"]
    pc, laptop = rows
    assert laptop["kind"] == "client" and laptop["name"] == "Laptop" and laptop["known"] is True
    assert laptop["phase"] is None and laptop["job"] is None
    assert [a["appid"] for a in laptop["apps"]] == [570, 440]
    assert laptop["apps"][0]["name"] == "Game Five Seventy"
    assert laptop["hit_bytes_per_second"] == pytest.approx(500 / WINDOW)
    assert laptop["miss_bytes_per_second"] == pytest.approx(9000 / WINDOW)
    assert pc["name"] == "pc-one" and pc["addresses"] == [PC] and pc["known"] is True
    assert pc["bytes_per_second"] == pytest.approx(31000 / WINDOW)
    assert [a["appid"] for a in pc["apps"]] == [440]
    assert pc["unattributed_bytes_per_second"] == pytest.approx(1000 / WINDOW)
    # Consistent with the device count.
    assert throughput(client, "1h")["now"]["active_clients"] == 2


def test_an_agent_with_two_addresses_is_one_row(setup) -> None:
    _settings, client, _clock, conn, log = setup
    agent_reports.store_report(conn, "pc-one", [440], 10, local_addrs=[PC, OTHER])
    append_log(log, chunk(3, 3000, addr=PC), chunk(2, 1000, addr=OTHER))
    (row,) = sources(client)
    assert row["key"] == "agent:pc-one" and row["addresses"] == [PC, OTHER]
    assert row["bytes_per_second"] == pytest.approx(4000 / WINDOW)


def test_an_unknown_address_is_a_new_device_until_it_was_swept(setup) -> None:
    settings, client, _clock, conn, log = setup
    append_log(log, chunk(2, 800, addr=NEW))
    (row,) = sources(client)
    assert row["key"] == f"addr:{NEW}" and row["name"] == NEW
    assert row["known"] is False
    # Once a sweep has recorded it, the Clients page lists it: known.
    sweep(conn, settings, NOW)
    (row,) = sources(client)
    assert row["known"] is True


def test_an_address_listed_only_under_the_runner_is_not_a_known_client(setup) -> None:
    """NITS-API-1 (NOW-LOADING-1 review nit): ``addrs_listed`` reads the client
    side only. NEW was the runner 10 days ago (its window ended 2 days ago);
    its only hourly row lies inside that window, so the Clients page does not
    list it and its client traffic now is a new device."""
    _settings, client, _clock, conn, log = setup
    marked = NOW - timedelta(days=10)
    runner_identity._extend_period(conn, NEW, marked)  # noqa: SLF001
    bucket = marked.replace(minute=0, second=0, microsecond=0)
    conn.execute(
        "INSERT INTO client_traffic (tier, bucket_at, client_addr, requests, bytes_served, last_seen) "
        "VALUES ('1h', ?, ?, 1, 500, ?)",
        (iso(bucket), NEW, iso(marked)),
    )
    conn.commit()
    append_log(log, chunk(2, 800, addr=NEW))
    (row,) = sources(client)
    assert row["kind"] == "client" and row["key"] == f"addr:{NEW}"
    assert row["known"] is False
    # The same row outside any runner window: the Clients page lists it.
    conn.execute("DELETE FROM runner_address_periods WHERE addr = ?", (NEW,))
    conn.commit()
    (row,) = sources(client)
    assert row["known"] is True


def test_a_named_address_is_known_before_its_first_sweep(setup) -> None:
    _settings, client, _clock, conn, log = setup
    client_names.set_name(conn, client_names.KEY_TYPE_IP, NEW, "Console")
    append_log(log, chunk(2, 800, addr=NEW))
    (row,) = sources(client)
    assert row["name"] == "Console" and row["known"] is True


# ---------------------------------------------------------------------------
# Unattributed, ordering, sums
# ---------------------------------------------------------------------------


def test_traffic_no_address_explains_is_an_unattributed_row_last(setup) -> None:
    _settings, client, _clock, conn, log = setup
    many = live_tier.MAX_ADDRS_PER_CELL + 3
    append_log(
        log,
        chunk(2, 4000, addr=RUNNER, status="MISS"),
        *[chunk(2, 10, addr=f"10.1.{i // 250}.{i % 250 + 1}", status="HIT") for i in range(many)],
    )
    rows = sources(client)
    assert rows[0]["kind"] == "runner"
    last = rows[-1]
    assert last["kind"] == "unattributed" and last["key"] == "unattributed"
    assert last["name"] is None and last["addresses"] == [] and last["apps"] == []
    # The runner took one of the 64 slots of that cell: 63 clients fit, the
    # rest (4 x 10 bytes) folded into the overflow entry.
    assert last["bytes_per_second"] == pytest.approx(40 / WINDOW)
    assert last["hit_bytes_per_second"] == pytest.approx(40 / WINDOW)
    assert sum(1 for r in rows if r["kind"] == "client") == live_tier.MAX_ADDRS_PER_CELL - 1
    # Every byte of the speed is in exactly one row.
    current = throughput(client, "1h")["now"]["current"]["bytes_per_second"]
    assert sum(r["bytes_per_second"] for r in rows) == pytest.approx(current)


def test_overflow_alone_is_still_a_row(setup) -> None:
    """The idle shortcut must not hide overflow bytes when no address has
    served bytes: the 64 slots of a cell taken by byte-less requests."""
    _settings, client, _clock, _conn, log = setup
    slots = live_tier.MAX_ADDRS_PER_CELL
    append_log(
        log,
        *[chunk(2, 0, addr=f"10.2.0.{i + 1}") for i in range(slots)],
        chunk(2, 700, addr="10.2.1.1", status="MISS"),
    )
    rows = sources(client)
    assert [r["kind"] for r in rows] == ["unattributed"]
    assert rows[0]["bytes_per_second"] == pytest.approx(700 / WINDOW)
    assert rows[0]["miss_bytes_per_second"] == pytest.approx(700 / WINDOW)


def test_rows_add_up_to_the_current_speed(setup) -> None:
    _settings, client, _clock, conn, log = setup
    agent_reports.store_report(conn, "pc-one", [440], 10, source_addr=PC)
    append_log(
        log,
        chunk(3, 7000, addr=RUNNER, status="MISS"),
        chunk(3, 3000, addr=PC),
        chunk(1, 900, addr=NEW),
        chunk(60, 99999, addr=PC),  # before the window: in no row
    )
    rows = sources(client)
    assert [r["kind"] for r in rows] == ["runner", "client", "client"]
    current = throughput(client, "1h")["now"]["current"]["bytes_per_second"]
    assert sum(r["bytes_per_second"] for r in rows) == pytest.approx(current)
    assert current == pytest.approx(10900 / WINDOW)


# ---------------------------------------------------------------------------
# Honest states
# ---------------------------------------------------------------------------


def test_nothing_loading_is_an_empty_list(setup) -> None:
    _settings, client, _clock, _conn, log = setup
    append_log(log, chunk(120, 5000, addr=PC))  # two minutes ago: outside the window
    assert sources(client) == []


def test_no_rows_unless_the_feed_is_ok(served, tmp_path: Path) -> None:
    """Never swept (the dev setup with the scheduler off): no ``now`` block,
    and the live endpoint sends null rows, not an empty list."""
    _settings, client, _clock, _conn = served
    log = tmp_path / "event.log"
    write_log(log, chunk(2, 5000, addr=PC))
    body = throughput(client, "1h")
    assert body["event_feed"]["state"] == "never-swept"
    assert body["now"] is None
    answer = live(client)
    assert answer["current"]["bytes_per_second"] > 0  # the ring itself answers
    assert answer["loading_sources"] is None


def test_a_stale_feed_sends_no_rows(setup) -> None:
    settings, client, clock, _conn, log = setup
    append_log(log, chunk(2, 5000, addr=PC))
    clock.advance(3600)  # last sweep an hour old: stale
    assert throughput(client, "1h")["event_feed"]["state"] == "stale"
    assert throughput(client, "1h")["now"] is None
    assert live(client)["loading_sources"] is None


# ---------------------------------------------------------------------------
# The ring: one read
# ---------------------------------------------------------------------------


def test_now_view_is_current_and_addr_activity_in_one_read(tmp_path: Path) -> None:
    log = tmp_path / "event.log"
    write_log(
        log,
        chunk(2, 1000, addr=PC, status="HIT"),
        chunk(3, 500, addr=PC, status="MISS", depot="200"),
        chunk(1, 300, addr=RUNNER, status="MISS"),
        chunk(1, 200, addr=RUNNER, status="HIT"),
    )
    tier = LiveTier(clock=lambda: NOW)
    tier.touch(NOW)
    tier.refresh(str(log), NOW)
    is_runner = lambda addr, _iso: addr == RUNNER  # noqa: E731
    view = tier.now_view(NOW, is_runner)
    assert view.current == tier.current(NOW, is_runner)
    assert (view.activity, view.seconds) == tier.addr_activity(NOW, is_runner)
    pc = view.activity[(PC, False)]
    assert (pc.bytes_served, pc.hit_bytes, pc.miss_bytes) == (1500, 1000, 500)
    assert pc.depots == {100: 1000, 200: 500}
    run = view.activity[(RUNNER, True)]
    assert (run.bytes_served, run.hit_bytes, run.miss_bytes) == (500, 200, 300)
    assert view.overflow_bytes == 0
    assert view.current.runner_hit_bytes_per_second == pytest.approx(200 / WINDOW)


# ---------------------------------------------------------------------------
# GRAPH-RECENT-1 nit N2
# ---------------------------------------------------------------------------


def test_the_running_live_point_divides_by_its_elapsed_seconds(setup) -> None:
    _settings, client, _clock, _conn, log = setup
    # NOW is 12:00:07: the running cell started at 12:00:05, 2 s ago.
    append_log(log, chunk(1, 6000, addr=PC), chunk(7, 5000, addr=PC))
    points = live(client)["points"]
    newest = points[-1]
    assert newest["bucket_seconds"] == 5 and newest["bytes_served"] == 6000
    assert newest["bytes_per_second"] == pytest.approx(6000 / 2)
    # A finished cell keeps its full length.
    done = next(p for p in points if p["bytes_served"] == 5000)
    assert done["bytes_per_second"] == pytest.approx(5000 / 5)
