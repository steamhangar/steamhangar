"""WP JOB-SIZE-1: expected size and live progress for a first download.

A running prefill of an app with no mapped depots used to report null speeds
("the server does not know this game's depots yet") until the run ended. With
the D19 depot preview on, ``GET /v1/throughput`` counts the per-depot bytes of
the preview's EXPECTED depots and carries the oracle's totals, marked
``expected``. No test touches the network: the oracle answer is injected
through the preview service's ``fetch``, and every fetch is counted.

Pinned by name:

1. Preview ready: speed, bytes so far and the expected sizes are reported,
   counting only the expected public-branch depots (no DLC depot, no depot
   without traffic in the preview, no foreign depot).
2. A depot some other app already maps is not counted.
3. Preview off (own switch or oracle) asks nothing and keeps the old nulls.
4. Failed or pending preview keeps the old nulls.
5. An app with mapped depots never consults the preview.
6. Nothing is written to ``depot_app_map``.
7. ETA inputs (scope addition 2026-11-01): ``miss_bytes_per_second`` is the
   MISS share only (a Verify / cache-only run reads 0), ``remaining_bytes``
   comes from the preview for a first download and from the stored
   completeness figure (minus MISS bytes since the check) for a mapped game.
"""

from __future__ import annotations

import dataclasses
import sqlite3
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from tests.test_d19_depot_preview import Counter, answer
from tests.test_event_sweep import event_line, make_settings, write_log
from vault_api import (
    cache_completeness,
    event_sweep,
    jobs as jobs_queue,
    mapping,
    oracle,
    runner_identity,
    traffic,
)
from vault_api.routers import traffic as traffic_router
from vault_api.config import MANIFEST_ORACLE_STEAMCMD_API, Settings
from vault_api.db import get_connection, init_db
from vault_api.depot_preview import DepotPreviewService
from vault_api.main import create_app

AUTH = {"X-Api-Key": TEST_API_KEY}
UTC = timezone.utc
APPID = 1366540  # preview: 1366541 (public), 1366542 (DLC), 1366544 (public)
OTHER_APP = 570
RUNNER = "10.0.0.7"  # the prefill runner's address (a marked period)
HOUSEHOLD_PC = "10.0.0.8"  # a client loading the same game at the same time


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def settings_for(tmp_path: Path, *, preview: bool = True, oracle_on: bool = True) -> Settings:
    base = make_settings(tmp_path, cooldown=0)
    values: dict[str, object] = {"depot_preview_enabled": preview}
    if oracle_on:
        values["manifest_oracle"] = MANIFEST_ORACLE_STEAMCMD_API
    return dataclasses.replace(base, **values)  # type: ignore[arg-type]


DEFAULT_LINES = (
    ("MISS", 1366541, 1000), ("MISS", 1366544, 500), ("MISS", 1366542, 300), ("MISS", 200, 7000)
)


def seed(
    tmp_path: Path,
    settings: Settings,
    now: datetime,
    lines_spec: tuple[tuple, ...] = DEFAULT_LINES,
) -> sqlite3.Connection:
    """A swept log with bytes on both expected depots, the DLC depot and a
    depot of another app, plus a running prefill of APPID (first download).
    Lines are ``(status, depot, bytes[, addr])``; the address defaults to the
    runner, whose marked period is stored."""
    init_db(settings.db_path)
    conn = get_connection(settings.db_path)
    mapping.upsert_mapping(conn, depotid=200, appid=OTHER_APP, name=None)
    conn.execute(
        "INSERT INTO runner_address_periods (addr, first_marked_at, last_marked_at) "
        "VALUES (?, ?, ?)",
        (RUNNER, stamp(now - timedelta(hours=1)), stamp(now)),
    )
    conn.commit()
    lines = [
        event_line(time=iso(now - timedelta(minutes=2)), addr=(rest[0] if rest else RUNNER),
                   cache_status=status, depot=str(depot), bytes_sent=str(size))
        for status, depot, size, *rest in lines_spec
    ]
    write_log(tmp_path / "event.log", *lines)
    event_sweep.sweep_once(conn, settings, now)
    jobs_queue.enqueue_prefill(conn, APPID)
    jobs_queue.claim_next_job(conn)
    conn.execute("UPDATE jobs SET started_at = ?", (stamp(now - timedelta(minutes=10)),))
    conn.commit()
    return conn


def client(settings: Settings, fetch: Counter, *, inline: bool = True) -> TestClient:
    app = create_app(settings)
    app.state.depot_preview = DepotPreviewService(
        run=(lambda job: job()) if inline else (lambda job: None), fetch=fetch
    )
    return TestClient(app)  # no `with`: no lifespan, no scheduler thread


def job_entry(c: TestClient) -> tuple[dict, dict]:
    body = c.get("/v1/throughput", headers=AUTH).json()
    (job,) = body["jobs"]
    return body, job


def map_rows(conn: sqlite3.Connection) -> list[tuple[int, int]]:
    return [
        (int(r[0]), int(r[1]))
        for r in conn.execute("SELECT depotid, appid FROM depot_app_map ORDER BY depotid, appid")
    ]


@pytest.fixture
def now() -> Iterator[datetime]:
    yield datetime.now(UTC)


def assert_not_expected(job: dict) -> None:
    assert job["expected"] is False
    assert job["expected_depots"] == 0
    assert job["expected_size_bytes"] is None
    assert job["expected_download_bytes"] is None
    assert job["bytes_per_second"] is None
    assert job["bytes_since_start"] is None
    assert job["exclusive_depots"] == 0 and job["mapped_depots"] == 0
    assert job["miss_bytes_per_second"] is None
    assert job["remaining_bytes"] is None and job["remaining_source"] is None


def test_a_first_download_counts_the_expected_depots_and_reports_the_expected_size(
    tmp_path: Path, now: datetime
) -> None:
    settings = settings_for(tmp_path)
    conn = seed(tmp_path, settings, now)
    fetch = Counter()
    try:
        body, job = job_entry(client(settings, fetch))
        window = body["current"]["window_seconds"]
        assert job["appid"] == APPID
        assert job["expected"] is True
        assert job["expected_depots"] == 2  # 1366541 + 1366544; DLC 1366542 never
        # Only the expected public depots: not the DLC depot (300), not depot 200 (7000).
        assert job["bytes_since_start"] == 1500
        assert job["bytes_per_second"] == pytest.approx(1500 / window)
        assert job["expected_size_bytes"] == 4_680_000_000 + 1_000
        assert job["expected_download_bytes"] == 1_820_000_000 + 400
        # The observed fields keep their meaning: the server maps nothing yet.
        assert job["exclusive_depots"] == 0 and job["mapped_depots"] == 0
        assert len(fetch.urls) == 1 and fetch.urls[0].endswith(f"/{APPID}")
        # ETA inputs: everything was a MISS; remaining = download - loaded.
        assert job["miss_bytes_per_second"] == pytest.approx(1500 / window)
        assert job["remaining_bytes"] == 1_820_000_400 - 1500
        assert job["remaining_source"] == "preview"
    finally:
        conn.close()


def test_the_preview_never_writes_depot_app_map(tmp_path: Path, now: datetime) -> None:
    settings = settings_for(tmp_path)
    conn = seed(tmp_path, settings, now)
    try:
        before = map_rows(conn)
        c = client(settings, Counter())
        job_entry(c)
        job_entry(c)  # a repeat poll reads the stored answer
        assert map_rows(conn) == before == [(200, OTHER_APP)]
    finally:
        conn.close()


def test_an_expected_depot_another_app_maps_is_not_counted(tmp_path: Path, now: datetime) -> None:
    settings = settings_for(tmp_path)
    conn = seed(tmp_path, settings, now)
    mapping.upsert_mapping(conn, depotid=1366544, appid=OTHER_APP, name=None)
    conn.commit()
    try:
        _, job = job_entry(client(settings, Counter()))
        assert job["expected"] is True
        assert job["expected_depots"] == 1
        assert job["bytes_since_start"] == 1000  # 1366544's 500 belong to the other app too
        # MUST-FIX 1: the sizes cover the counted depot only, so the remainder
        # can reach 0 and "of ~X" is not inflated by the dropped depot.
        assert job["expected_size_bytes"] == 4_680_000_000
        assert job["expected_download_bytes"] == 1_820_000_000
        assert job["remaining_bytes"] == 1_820_000_000 - 1000
    finally:
        conn.close()


def test_no_attributable_expected_depot_keeps_the_old_answer(tmp_path: Path, now: datetime) -> None:
    settings = settings_for(tmp_path)
    conn = seed(tmp_path, settings, now)
    for depot in (1366541, 1366544):
        mapping.upsert_mapping(conn, depotid=depot, appid=OTHER_APP, name=None)
    conn.commit()
    try:
        _, job = job_entry(client(settings, Counter()))
        assert_not_expected(job)
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("preview", "oracle_on"),
    [(False, True), (True, False), (False, False)],
    ids=["own-switch-off", "oracle-off", "both-off"],
)
def test_preview_off_asks_nothing_and_keeps_the_honest_nulls(
    tmp_path: Path, now: datetime, preview: bool, oracle_on: bool
) -> None:
    settings = settings_for(tmp_path, preview=preview, oracle_on=oracle_on)
    conn = seed(tmp_path, settings, now)
    fetch = Counter()
    try:
        _, job = job_entry(client(settings, fetch))
        assert_not_expected(job)
        assert fetch.urls == []
    finally:
        conn.close()


def test_a_failed_preview_keeps_the_honest_nulls(tmp_path: Path, now: datetime) -> None:
    settings = settings_for(tmp_path)
    conn = seed(tmp_path, settings, now)
    fetch = Counter(oracle.OracleError("boom"))
    try:
        c = client(settings, fetch)
        _, job = job_entry(c)
        assert_not_expected(job)
        _, job = job_entry(c)  # inside the back-off: no second query
        assert_not_expected(job)
        assert len(fetch.urls) == 1
    finally:
        conn.close()


def test_a_pending_preview_keeps_the_honest_nulls(tmp_path: Path, now: datetime) -> None:
    settings = settings_for(tmp_path)
    conn = seed(tmp_path, settings, now)
    try:
        _, job = job_entry(client(settings, Counter(), inline=False))
        assert_not_expected(job)
    finally:
        conn.close()


def test_a_mapped_app_never_consults_the_preview(tmp_path: Path, now: datetime) -> None:
    settings = settings_for(tmp_path)
    conn = seed(tmp_path, settings, now)
    mapping.upsert_mapping(conn, depotid=1366541, appid=APPID, name=None)
    conn.commit()
    fetch = Counter()
    try:
        _, job = job_entry(client(settings, fetch))
        assert job["expected"] is False and job["expected_depots"] == 0
        assert job["exclusive_depots"] == 1 and job["mapped_depots"] == 1
        assert job["bytes_since_start"] == 1000  # the observed mapping, as before
        assert fetch.urls == []
    finally:
        conn.close()


def test_a_cache_only_run_has_no_miss_speed(tmp_path: Path, now: datetime) -> None:
    """The verify / cache-read case: every byte is a HIT, so the ETA basis
    (MISS speed) is 0 while the card's speed is the cache's read speed."""
    settings = settings_for(tmp_path)
    spec = (("HIT", 1366541, 1000), ("HIT", 1366544, 500))
    conn = seed(tmp_path, settings, now, spec)
    try:
        body, job = job_entry(client(settings, Counter()))
        assert job["bytes_per_second"] == pytest.approx(1500 / body["current"]["window_seconds"])
        assert job["miss_bytes_per_second"] == 0
    finally:
        conn.close()


def test_a_mixed_run_counts_only_the_miss_bytes(tmp_path: Path, now: datetime) -> None:
    settings = settings_for(tmp_path)
    spec = (("HIT", 1366541, 1000), ("MISS", 1366544, 500), ("MISS", 200, 7000))
    conn = seed(tmp_path, settings, now, spec)
    try:
        body, job = job_entry(client(settings, Counter()))
        window = body["current"]["window_seconds"]
        assert job["miss_bytes_per_second"] == pytest.approx(500 / window)
        # A first download requests each chunk once: HITs are progress too.
        assert job["remaining_bytes"] == 1_820_000_400 - 1500
    finally:
        conn.close()


def test_a_household_pc_loading_the_same_game_is_not_job_progress(
    tmp_path: Path, now: datetime
) -> None:
    """SHOULD-FIX 4: a first download's figures are the runner's only."""
    settings = settings_for(tmp_path)
    spec = DEFAULT_LINES + (("MISS", 1366541, 9000, HOUSEHOLD_PC), ("HIT", 1366544, 4000, HOUSEHOLD_PC))
    conn = seed(tmp_path, settings, now, spec)
    try:
        body, job = job_entry(client(settings, Counter()))
        window = body["current"]["window_seconds"]
        assert job["bytes_since_start"] == 1500
        assert job["bytes_per_second"] == pytest.approx(1500 / window)
        assert job["miss_bytes_per_second"] == pytest.approx(1500 / window)
        assert job["remaining_bytes"] == 1_820_000_400 - 1500
    finally:
        conn.close()


def test_a_mapped_remainder_counts_only_runner_misses(tmp_path: Path, now: datetime) -> None:
    spec = (("MISS", 1366541, 400), ("MISS", 1366541, 2000, HOUSEHOLD_PC))
    settings, conn = _mapped_job(tmp_path, now, spec)
    runner = traffic.runner_scope(runner_identity.windows(conn))

    def figure_of(c, appid, depots):
        return cache_completeness.Completeness(
            pct=40, expected_bytes=10_000, present_bytes=4_000,
            checked_at=stamp(now - timedelta(hours=2)),
        )

    try:
        (job,) = traffic_router._job_throughput(
            conn, None, now, True, figure_of=figure_of, runner=runner
        )
        assert job.remaining_bytes == 6_000 - 400
        # The mapped card's own speed and "so far" keep every address (as before).
        assert job.bytes_since_start == 2_400
    finally:
        conn.close()


def test_the_miss_helper_subtracts_hits(tmp_path: Path, now: datetime) -> None:
    settings = settings_for(tmp_path)
    spec = (("HIT", 1366541, 1000), ("MISS", 1366541, 400), ("MISS", 200, 7000))
    conn = seed(tmp_path, settings, now, spec)
    try:
        start = traffic.bucket_iso(now - timedelta(hours=1), traffic.TIER_5M)
        assert traffic.depots_miss_bytes_since(conn, [1366541], traffic.TIER_5M, start) == 400
        assert traffic.depots_bytes_since(conn, [1366541], traffic.TIER_5M, start) == 1400
        assert traffic.depots_miss_bytes_since(conn, [], traffic.TIER_5M, start) == 0
    finally:
        conn.close()


def _mapped_job(tmp_path: Path, now: datetime, spec) -> tuple[Settings, sqlite3.Connection]:
    settings = settings_for(tmp_path)
    conn = seed(tmp_path, settings, now, spec)
    mapping.upsert_mapping(conn, depotid=1366541, appid=APPID, name=None)
    conn.commit()
    return settings, conn


def test_a_mapped_game_takes_remaining_from_the_completeness_figure(
    tmp_path: Path, now: datetime
) -> None:
    """Missing at the check (10 000 - 4 000) minus the MISS bytes since the
    later of the check and the job's start (400; the HIT 1000 was present)."""
    spec = (("HIT", 1366541, 1000), ("MISS", 1366541, 400))
    settings, conn = _mapped_job(tmp_path, now, spec)
    calls: list[tuple[int, list[int]]] = []

    def figure_of(c, appid, depots):
        calls.append((appid, depots))
        return cache_completeness.Completeness(
            pct=40, expected_bytes=10_000, present_bytes=4_000,
            checked_at=stamp(now - timedelta(hours=2)),
        )

    try:
        window = traffic.CurrentWindow(
            start=traffic.bucket_iso(now - timedelta(minutes=10), traffic.TIER_5M),
            end=stamp(now), seconds=600,
        )
        (job,) = traffic_router._job_throughput(conn, window, now, True, figure_of=figure_of)
        assert calls == [(APPID, [1366541])]
        assert job.expected is False
        assert job.remaining_bytes == 6_000 - 400
        assert job.remaining_source == "completeness"
        assert job.miss_bytes_per_second == pytest.approx(400 / 600)
    finally:
        conn.close()


def test_a_check_after_the_start_counts_misses_from_the_check_on(
    tmp_path: Path, now: datetime
) -> None:
    spec = (("MISS", 1366541, 400),)  # two minutes ago, before the check below
    settings, conn = _mapped_job(tmp_path, now, spec)
    later = now + timedelta(minutes=10)  # the check's 5-minute bucket starts after the line

    def figure_of(c, appid, depots):
        return cache_completeness.Completeness(
            pct=40, expected_bytes=10_000, present_bytes=4_000, checked_at=stamp(later)
        )

    try:
        (job,) = traffic_router._job_throughput(conn, None, now, True, figure_of=figure_of)
        assert job.remaining_bytes == 6_000
    finally:
        conn.close()


def test_an_unknown_figure_leaves_remaining_null(tmp_path: Path, now: datetime) -> None:
    settings, conn = _mapped_job(tmp_path, now, (("MISS", 1366541, 400),))
    try:
        (job,) = traffic_router._job_throughput(
            conn, None, now, True, figure_of=lambda c, a, d: cache_completeness.UNKNOWN
        )
        assert job.remaining_bytes is None and job.remaining_source is None
        # The endpoint's own figure: no stored completeness row -> unknown.
        _, entry = job_entry(client(settings, Counter()))
        assert entry["remaining_bytes"] is None and entry["remaining_source"] is None
    finally:
        conn.close()


def test_the_answer_for_the_default_payload_has_the_preview_depots() -> None:
    # Guard against a fixture drift in test_d19_depot_preview.answer(): the
    # numbers above rely on its two public depots.
    assert b'"1366541"' in answer() and b'"1366544"' in answer()


# --------------------------------------------------------------------------
# MUST-FIX 2: the ETA speed (``eta_bytes_per_second``)
# --------------------------------------------------------------------------

LIVE = traffic_router.LiveCurrentOut(
    bytes_per_second=50.0,
    runner_bytes_per_second=40.0,
    window_start="2026-11-01T11:59:45Z",
    window_end="2026-11-01T12:00:00Z",
    window_seconds=15,
    runner_hit_bytes_per_second=10.0,
    runner_miss_bytes_per_second=30.0,
)


def _entry(job_id: int = 1, source: str | None = "preview", **extra) -> traffic_router.JobThroughputOut:
    values = dict(
        job_id=job_id, appid=APPID, name=None, started_at="2026-11-01T11:58:00Z",
        bytes_per_second=8.0, bytes_since_start=100, exclusive_depots=0,
        miss_bytes_per_second=6.0, remaining_bytes=None if source is None else 1000,
        remaining_source=source,
    )
    values.update(extra)
    return traffic_router.JobThroughputOut(**values)


def _window(start: str) -> traffic.CurrentWindow:
    return traffic.CurrentWindow(start=start, end="2026-11-01T12:00:00Z", seconds=600)


def test_eta_speed_preview_divides_by_the_live_runner_total() -> None:
    (job,) = traffic_router._with_eta([_entry(source="preview")], LIVE, _window("2026-11-01T11:50:00Z"))
    assert job.eta_bytes_per_second == 40.0 and job.eta_speed_source == "live"


def test_eta_speed_completeness_divides_by_the_live_runner_miss() -> None:
    (job,) = traffic_router._with_eta([_entry(source="completeness")], LIVE, None)
    assert job.eta_bytes_per_second == 30.0 and job.eta_speed_source == "live"


def test_eta_speed_live_belongs_to_the_running_job_only() -> None:
    """Two running prefills (crash leftovers): the runner row is the oldest."""
    first, second = traffic_router._with_eta(
        [_entry(job_id=7), _entry(job_id=9)], LIVE, _window("2026-11-01T11:50:00Z")
    )
    assert first.eta_speed_source == "live"
    # The other one has no live speed and a window older than its start.
    assert second.eta_bytes_per_second is None and second.eta_speed_source is None


def test_eta_speed_without_live_is_withheld_while_the_window_predates_the_job() -> None:
    """The dilution case: a swept 5-10 min window that started before the job
    averages in time the job did not exist, so no ETA from it."""
    (job,) = traffic_router._with_eta([_entry()], None, _window("2026-11-01T11:50:00Z"))
    assert job.eta_bytes_per_second is None and job.eta_speed_source is None


def test_eta_speed_without_live_uses_an_undiluted_window_per_source() -> None:
    later = _window("2026-11-01T11:58:00Z")  # starts with the job
    (preview,) = traffic_router._with_eta([_entry(source="preview")], None, later)
    assert preview.eta_bytes_per_second == 8.0 and preview.eta_speed_source == "window"
    (mapped,) = traffic_router._with_eta([_entry(source="completeness")], None, later)
    assert mapped.eta_bytes_per_second == 6.0 and mapped.eta_speed_source == "window"


def test_eta_speed_is_null_without_a_remainder() -> None:
    (job,) = traffic_router._with_eta([_entry(source=None)], LIVE, None)
    assert job.eta_bytes_per_second is None and job.eta_speed_source is None


def test_the_endpoint_fills_the_eta_speed_from_the_live_tier(tmp_path: Path, now: datetime) -> None:
    settings = settings_for(tmp_path)
    conn = seed(tmp_path, settings, now)
    try:
        body, job = job_entry(client(settings, Counter()))
        assert body["now"] is not None
        assert job["eta_speed_source"] == "live"
        assert job["eta_bytes_per_second"] == body["now"]["current"]["runner_bytes_per_second"]
    finally:
        conn.close()

