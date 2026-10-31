"""WP RUNNER-SPLIT-1: the prefill runner's traffic split by cache status.

The series points (swept tiers and the live ring), the swept ``current``,
the live ``now.current`` and ``/v1/throughput/live`` carry the runner's HIT
and MISS parts, so the Downloads graph counts a verifying runner's reads as
"served from cache" and the pill can tell a verify from a download. All
fields are additive; the existing ones keep their meaning.

Also nit N1 of GRAPH-RECENT-1: the running bucket stitched from the ring
divides by the seconds elapsed in it, not by the bucket length.

Synthetic event lines, an injected clock, the real sweep; no threads.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from tests.test_event_sweep import append_log, write_log
from tests.test_graph_recent_1 import (  # noqa: F401 - ``served`` is a fixture
    AUTH,
    NOW,
    PC,
    RUNNER,
    line,
    minute_of,
    point_at,
    served,
    series,
    sweep,
    throughput,
)
from vault_api import runner_identity

#: Every field a series point had before rc21; Android and older web builds
#: read these, so they must stay with their old meaning.
OLD_POINT_KEYS = {
    "t",
    "bytes_served",
    "hit_bytes",
    "miss_bytes",
    "requests",
    "runner_bytes",
    "bytes_per_second",
    "peak_bytes_per_second",
}
OLD_CURRENT_KEYS = {
    "bytes_per_second",
    "runner_bytes_per_second",
    "window_start",
    "window_end",
    "window_seconds",
}
NEW_POINT_KEYS = {"runner_hit_bytes", "runner_miss_bytes"}
NEW_CURRENT_KEYS = {"runner_hit_bytes_per_second", "runner_miss_bytes_per_second"}


def _runner_from(conn, start) -> None:
    runner_identity._extend_period(conn, RUNNER, start)
    conn.commit()


def live(client) -> dict:
    return client.get("/v1/throughput/live", headers=AUTH).json()


# ---------------------------------------------------------------------------
# A runner that only verifies (all HIT)
# ---------------------------------------------------------------------------


def test_a_verify_only_runner_is_all_hit_on_every_endpoint(served, tmp_path: Path) -> None:
    """The operator's case: a forced verify reads chunks the cache has. Every
    "now" figure and the newest points say: runner traffic, none missed."""
    settings, client, _clock, conn = served
    log = tmp_path / "event.log"
    write_log(log)
    sweep(conn, settings, NOW - timedelta(minutes=4))
    _runner_from(conn, NOW - timedelta(minutes=2))
    append_log(
        log,
        line(NOW - timedelta(seconds=40), 4000, addr=RUNNER, status="HIT"),
        line(NOW - timedelta(seconds=3), 6000, addr=RUNNER, status="HIT"),
    )

    now = throughput(client, "1h")["now"]
    assert now["runner_active"] is True and now["active_clients"] == 0
    cur = now["current"]
    assert cur["runner_bytes_per_second"] == pytest.approx(6000 / 17)
    assert cur["runner_hit_bytes_per_second"] == pytest.approx(6000 / 17)
    assert cur["runner_miss_bytes_per_second"] == 0

    body = series(client, "1h")
    newest = body["points"][-1]
    assert newest["source"] == "live" and newest["runner_bytes"] == 6000
    assert (newest["runner_hit_bytes"], newest["runner_miss_bytes"]) == (6000, 0)
    # Clients' figures stay clients only.
    assert (newest["hit_bytes"], newest["miss_bytes"]) == (0, 0)
    before = point_at(body, minute_of(NOW - timedelta(seconds=40)))
    assert (before["runner_hit_bytes"], before["runner_miss_bytes"]) == (4000, 0)

    answer = live(client)
    assert answer["current"]["runner_hit_bytes_per_second"] == pytest.approx(6000 / 17)
    assert answer["current"]["runner_miss_bytes_per_second"] == 0
    assert sum(p["runner_hit_bytes"] for p in answer["points"]) == 10000
    assert sum(p["runner_miss_bytes"] for p in answer["points"]) == 0


# ---------------------------------------------------------------------------
# A runner that downloads and verifies, next to a client
# ---------------------------------------------------------------------------


def test_a_mixed_runner_is_split_and_clients_stay_apart(served, tmp_path: Path) -> None:
    settings, client, _clock, conn = served
    log = tmp_path / "event.log"
    write_log(log)
    sweep(conn, settings, NOW - timedelta(minutes=4))
    _runner_from(conn, NOW - timedelta(minutes=2))
    append_log(
        log,
        line(NOW - timedelta(seconds=4), 5000, addr=RUNNER, status="MISS"),
        line(NOW - timedelta(seconds=3), 7000, addr=RUNNER, status="HIT"),
        line(NOW - timedelta(seconds=2), 800, addr=PC, status="HIT"),
        line(NOW - timedelta(seconds=2), 300, addr=PC, status="MISS"),
    )
    cur = throughput(client, "6h")["now"]["current"]
    assert cur["runner_bytes_per_second"] == pytest.approx(12000 / 17)
    assert cur["runner_hit_bytes_per_second"] == pytest.approx(7000 / 17)
    assert cur["runner_miss_bytes_per_second"] == pytest.approx(5000 / 17)

    newest = series(client, "6h")["points"][-1]
    assert newest["bytes_served"] == 13100 and newest["runner_bytes"] == 12000
    assert (newest["runner_hit_bytes"], newest["runner_miss_bytes"]) == (7000, 5000)
    assert (newest["hit_bytes"], newest["miss_bytes"]) == (800, 300)
    # The graph's two series: served from cache = client HIT + runner HIT,
    # from the internet = the rest.
    cache = newest["hit_bytes"] + newest["runner_hit_bytes"]
    assert (cache, newest["bytes_served"] - cache) == (7800, 5300)

    day = series(client, "24h")["points"][-1]
    assert day["source"] == "live"
    assert (day["runner_hit_bytes"], day["runner_miss_bytes"]) == (7000, 5000)

    # The live view's 5 s cells: the runner's lines (12:00:03, 12:00:04) are
    # in the 12:00:00 cell, the client's (12:00:05) in the running one.
    answer = live(client)
    cell = point_at(answer, "2026-10-18T12:00:00Z")
    assert cell["bucket_seconds"] == 5
    assert (cell["runner_hit_bytes"], cell["runner_miss_bytes"]) == (7000, 5000)
    running = answer["points"][-1]
    assert (running["runner_hit_bytes"], running["runner_miss_bytes"]) == (0, 0)
    assert (running["hit_bytes"], running["miss_bytes"]) == (800, 300)


def test_the_swept_tier_and_current_carry_the_runner_split(served, tmp_path: Path) -> None:
    """Persisted points (no ring involved) and the swept ``current``; the
    points' sums equal the summary's ``prefill_runner``."""
    settings, client, _clock, conn = served
    log = tmp_path / "event.log"
    _runner_from(conn, NOW - timedelta(minutes=30))
    write_log(
        log,
        line(NOW - timedelta(minutes=3), 9000, addr=RUNNER, status="HIT"),
        line(NOW - timedelta(minutes=3), 1000, addr=RUNNER, status="MISS"),
        line(NOW - timedelta(minutes=3), 500, addr=PC, status="HIT"),
    )
    sweep(conn, settings, NOW - timedelta(seconds=30))
    write_log(log)  # rotated away: only the swept tier answers

    head = throughput(client, "1h")
    swept = head["current"]
    seconds = swept["window_seconds"]
    assert swept["runner_bytes_per_second"] == pytest.approx(10000 / seconds)
    assert swept["runner_hit_bytes_per_second"] == pytest.approx(9000 / seconds)
    assert swept["runner_miss_bytes_per_second"] == pytest.approx(1000 / seconds)

    body = series(client, "1h")
    point = point_at(body, minute_of(NOW - timedelta(minutes=3)))
    assert point["source"] == "persisted"
    assert (point["runner_bytes"], point["runner_hit_bytes"], point["runner_miss_bytes"]) == (10000, 9000, 1000)
    runner = head["summary"]["prefill_runner"]
    assert sum(p["runner_hit_bytes"] for p in body["points"]) == runner["hit_bytes"] == 9000
    assert sum(p["runner_miss_bytes"] for p in body["points"]) == runner["miss_bytes"] == 1000
    # 7 d reads the hourly tier: the split is there too.
    week = series(client, "7d")
    assert sum(p["runner_hit_bytes"] for p in week["points"]) == 9000


def test_old_fields_keep_their_meaning_and_new_ones_are_additive(served, tmp_path: Path) -> None:
    """Backward compatibility: every pre-rc21 key is still there with the
    same value; the split only adds keys (Android ignores unknown keys)."""
    settings, client, _clock, conn = served
    log = tmp_path / "event.log"
    write_log(log)
    sweep(conn, settings, NOW - timedelta(minutes=4))
    _runner_from(conn, NOW - timedelta(minutes=2))
    append_log(
        log,
        line(NOW - timedelta(seconds=3), 7000, addr=RUNNER, status="HIT"),
        line(NOW - timedelta(seconds=2), 800, addr=PC, status="HIT"),
    )
    head = throughput(client, "1h")
    for cur in (head["current"], head["now"]["current"]):
        assert OLD_CURRENT_KEYS | NEW_CURRENT_KEYS <= set(cur)
    # runner_bytes_per_second is still the whole runner part, not the MISS part.
    assert head["now"]["current"]["runner_bytes_per_second"] == pytest.approx(7000 / 17)
    for body in (series(client, "1h"), series(client, "7d")):
        for p in body["points"]:
            assert OLD_POINT_KEYS | NEW_POINT_KEYS <= set(p)
    newest = series(client, "1h")["points"][-1]
    assert (newest["runner_bytes"], newest["hit_bytes"], newest["bytes_served"]) == (7000, 800, 7800)
    answer = live(client)
    assert OLD_CURRENT_KEYS | NEW_CURRENT_KEYS <= set(answer["current"])
    for p in answer["points"]:
        assert OLD_POINT_KEYS | NEW_POINT_KEYS | {"bucket_seconds", "source"} <= set(p)


# ---------------------------------------------------------------------------
# GRAPH-RECENT-1 nit N1: the running bucket's rate
# ---------------------------------------------------------------------------


def test_the_running_ring_bucket_divides_by_its_elapsed_seconds(served, tmp_path: Path) -> None:
    """NOW is 7 s into the running minute (and the running 5-minute bucket):
    7000 bytes there are 1000 B/s, not 7000 / 60. Closed buckets keep the
    full length."""
    settings, client, _clock, conn = served
    log = tmp_path / "event.log"
    write_log(log)
    sweep(conn, settings, NOW - timedelta(minutes=4))
    append_log(
        log,
        line(NOW - timedelta(seconds=90), 6000),
        line(NOW - timedelta(seconds=3), 7000),
    )
    body = series(client, "1h")
    newest = body["points"][-1]
    assert newest["t"] == minute_of(NOW) and newest["source"] == "live"
    assert newest["bytes_per_second"] == pytest.approx(7000 / 7)
    assert newest["peak_bytes_per_second"] == pytest.approx(7000 / 7)
    closed = point_at(body, minute_of(NOW - timedelta(seconds=90)))
    assert closed["bytes_per_second"] == pytest.approx(6000 / 60)

    day = series(client, "24h")
    running = day["points"][-1]
    assert running["t"] == "2026-10-18T12:00:00Z"
    assert running["bytes_per_second"] == pytest.approx(7000 / 7)
    # 11:58:37 is in the closed 11:55 bucket: still / 300.
    assert point_at(day, "2026-10-18T11:55:00Z")["bytes_per_second"] == pytest.approx(6000 / 300)


def test_the_running_bucket_rate_never_divides_by_less_than_a_second(served, tmp_path: Path) -> None:
    settings, client, clock, conn = served
    log = tmp_path / "event.log"
    write_log(log)
    sweep(conn, settings, NOW - timedelta(minutes=4))
    clock.now = NOW.replace(second=0, microsecond=200_000)  # 0.2 s into the minute
    append_log(log, line(clock.now.replace(microsecond=0), 500))
    newest = series(client, "1h")["points"][-1]
    assert newest["bytes_served"] == 500
    assert newest["bytes_per_second"] == pytest.approx(500.0)
