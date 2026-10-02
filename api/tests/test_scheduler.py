"""The prefill scheduler (WP 3.5): decisions, target set, sweep, endpoint.

Every timing decision is driven by an INJECTED clock — a fixed-offset aware
datetime handed to ``maybe_sweep``/``PrefillScheduler`` — so nothing here
waits for real time to pass and nothing depends on the timezone of the
machine running the tests. The only real waiting is the end-to-end test that
lets the scheduler thread and the job worker drain a queue, and that polls the
API rather than sleeping a fixed amount.

Agent-report fixtures are written directly into ``agent_reports`` (synthetic,
modelled on the real row shape) because the staleness rules need control over
``reported_at``, which the HTTP endpoint takes from the server clock.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests import stub_prefill
from tests.conftest import TEST_API_KEY
from vault_api import agent_reports
from vault_api import jobs as jobs_queue
from vault_api import scheduler as scheduler_module
from vault_api import settings_store
from vault_api.config import Settings
from vault_api.db import get_connection, init_db
from vault_api.main import create_app
from vault_api.mapping import upsert_mapping
from vault_api.schedule_window import parse_window
from vault_api.scheduler import (
    EMPTY_STATE,
    PrefillScheduler,
    ScheduleState,
    cached_appids,
    cached_sweep_gc_risk,
    claim_sweep,
    compute_targets,
    describe_resolved_schedule,
    finish_sweep,
    format_utc_offset,
    interval_elapsed,
    maybe_sweep,
    next_eligible_at,
    read_state,
    warn_once_if_cached_sweep_without_gc,
)

AUTH = {"X-Api-Key": TEST_API_KEY}

#: Fixed +02:00, so a machine in UTC and a machine in Berlin agree.
TZ = timezone(timedelta(hours=2))


def local(hour: int, minute: int = 0, day: int = 6) -> datetime:
    """An aware server-local datetime on 2026-08-``day``."""
    return datetime(2026, 8, day, hour, minute, tzinfo=TZ)


def utc_iso_of(moment: datetime) -> str:
    """The stored-UTC rendering of any aware moment."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_iso(hour: int, minute: int = 0, day: int = 6) -> str:
    """The stored-UTC rendering of a local moment on 2026-08-``day``."""
    return utc_iso_of(local(hour, minute, day))


def make_settings(
    tmp_path: Path,
    *,
    window: str | None = "09:00-17:00",
    interval_minutes: int = 180,
    stale_days: int = 7,
    steamprefill_path: str = "",
    cache_root: Path | None = None,
) -> Settings:
    return Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(cache_root or (tmp_path / "cache")),
        log_level="INFO",
        steamprefill_path=steamprefill_path,
        prefill_timeout_seconds=60,
        worker_poll_seconds=0.02,
        steamprefill_cache_dir=str(tmp_path / "unused-steamprefill-cache"),
        manifest_archive_dir=str(tmp_path / "manifest-archive"),
        schedule_window=None if window is None else parse_window(window),
        schedule_interval_minutes=interval_minutes,
        schedule_client_stale_days=stale_days,
    )


@pytest.fixture
def conn(tmp_path: Path):
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    connection = get_connection(db_path)
    try:
        yield connection
    finally:
        connection.close()


def insert_report(
    conn: sqlite3.Connection,
    client_id: str,
    appids: list[int] | str,
    reported_at: str,
) -> None:
    """One synthetic ``agent_reports`` row with an explicit timestamp.

    ``appids`` may be a raw string instead of a list, to plant the corrupt
    row the unreadable-snapshot path degrades on.
    """
    payload = (
        appids
        if isinstance(appids, str)
        else json.dumps(appids, separators=(",", ":"))
    )
    conn.execute(
        "INSERT INTO agent_reports (client_id, reported_at, appids) VALUES (?, ?, ?)",
        (client_id, reported_at, payload),
    )
    conn.commit()


def write_cached_depot(cache_root: Path, depotid: int, content: bytes = b"x") -> None:
    """A minimal on-disk depot: one non-empty chunk file, which is all
    ``sizes.scan_depot_dir_bytes`` needs to consider a depot 'cached'
    (WP 4d's ``cached_appids``)."""
    path = cache_root / "depot" / str(depotid) / "chunk" / "a.bin"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


# ==========================================================================
# schedule_state bookkeeping (schema v6)
# ==========================================================================


def test_state_is_empty_before_the_first_sweep(conn: sqlite3.Connection) -> None:
    assert read_state(conn) == EMPTY_STATE


def test_claim_stamps_the_start_time_and_nulls_the_counters(
    conn: sqlite3.Connection,
) -> None:
    assert claim_sweep(conn, local(10), interval_minutes=180) is True

    state = read_state(conn)
    assert state.last_sweep_at == utc_iso(10)
    # NULL until finish_sweep — "in flight", not "zero apps".
    assert state.last_sweep_targets is None
    assert state.last_sweep_enqueued is None


def test_finish_records_counts_without_moving_the_start_time(
    conn: sqlite3.Connection,
) -> None:
    claim_sweep(conn, local(10), interval_minutes=180)
    finish_sweep(conn, targets=12, enqueued=3)

    state = read_state(conn)
    assert (state.last_sweep_targets, state.last_sweep_enqueued) == (12, 3)
    # The interval is measured from the sweep's START, so a slow sweep must
    # not push the next one out.
    assert state.last_sweep_at == utc_iso(10)


def test_schedule_state_holds_exactly_one_row(conn: sqlite3.Connection) -> None:
    """The CHECK (id = 1) is what makes 'the single row' a guarantee."""
    claim_sweep(conn, local(10), interval_minutes=180)
    claim_sweep(conn, local(14), interval_minutes=180)

    (count,) = conn.execute("SELECT COUNT(*) FROM schedule_state").fetchone()
    assert count == 1
    assert read_state(conn).last_sweep_at == utc_iso(14)

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO schedule_state (id, last_sweep_at) VALUES (2, 'x')")


# ==========================================================================
# Interval logic
# ==========================================================================


def test_interval_elapsed_when_nothing_was_ever_swept() -> None:
    assert interval_elapsed(EMPTY_STATE, local(10), 180) is True


def test_interval_not_elapsed_before_the_full_interval() -> None:
    state = ScheduleState(utc_iso(10), None, None)

    assert interval_elapsed(state, local(12, 59), 180) is False
    assert interval_elapsed(state, local(13, 0), 180) is True
    assert interval_elapsed(state, local(13, 1), 180) is True


def test_unreadable_last_sweep_at_is_treated_as_never_swept() -> None:
    """A hand-edited/corrupt value must not disable the scheduler forever."""
    for bad in ("", "not-a-time", "2026-08-06 10:00:00", "2026-08-06T10:00:00+02:00"):
        assert interval_elapsed(ScheduleState(bad, None, None), local(10), 180) is True


def test_a_future_last_sweep_at_blocks_rather_than_storms() -> None:
    """Clock stepped backwards: skip sweeps until real time catches up.

    The other direction — treating a future timestamp as 'long ago' — would
    sweep on every tick, i.e. a Steam login storm.
    """
    state = ScheduleState(utc_iso(10, day=7), None, None)

    assert interval_elapsed(state, local(11), 180) is False


def test_interval_survives_a_restart(tmp_path: Path) -> None:
    """The crash-recovery rule: a restart mid-window must not re-sweep.

    ``schedule_state`` lives in the database precisely so this holds; the two
    connections below stand in for two process lifetimes.
    """
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    first = get_connection(db_path)
    try:
        assert claim_sweep(first, local(10), interval_minutes=180) is True
    finally:
        first.close()

    # "Process restarts" — a brand-new connection, no in-memory state at all.
    second = get_connection(db_path)
    try:
        assert claim_sweep(second, local(10, 5), interval_minutes=180) is False
        assert claim_sweep(second, local(13, 0), interval_minutes=180) is True
    finally:
        second.close()


# ==========================================================================
# Target set (plan A8: installed IS the prefill set)
# ==========================================================================


def test_targets_are_the_union_of_every_fresh_clients_latest_report(
    conn: sqlite3.Connection,
) -> None:
    insert_report(conn, "gaming-pc", [440, 730], utc_iso(9))
    insert_report(conn, "steam-deck", [730, 1091500], utc_iso(9, 30))

    result = compute_targets(conn, local(10), stale_after_days=7)

    assert result.appids == [440, 730, 1091500]  # union, sorted, deduped
    assert result.included_clients == ["gaming-pc", "steam-deck"]
    assert result.excluded_clients == []


def test_only_the_latest_snapshot_of_a_client_counts(
    conn: sqlite3.Connection,
) -> None:
    """An uninstalled game drops out of the target set (ADR-0002 removals)."""
    insert_report(conn, "gaming-pc", [440, 730], utc_iso(8))
    insert_report(conn, "gaming-pc", [440], utc_iso(9))

    assert compute_targets(conn, local(10), stale_after_days=7).appids == [440]


def test_a_client_silent_for_longer_than_the_bound_is_excluded(
    conn: sqlite3.Connection,
) -> None:
    insert_report(conn, "gaming-pc", [440], utc_iso(9))
    insert_report(
        conn, "retired-pc", [999], utc_iso_of(local(9) - timedelta(days=8))
    )

    result = compute_targets(conn, local(10), stale_after_days=7)

    assert result.appids == [440]
    assert result.included_clients == ["gaming-pc"]
    assert [(c.client_id, c.reason) for c in result.excluded_clients] == [
        ("retired-pc", "stale")
    ]


def test_the_staleness_bound_is_configurable(conn: sqlite3.Connection) -> None:
    insert_report(conn, "steam-deck", [730], utc_iso(9, day=4))  # 2 days old

    assert compute_targets(conn, local(10), stale_after_days=7).appids == [730]
    assert compute_targets(conn, local(10), stale_after_days=1).appids == []


def test_a_corrupt_snapshot_excludes_that_client_only(
    conn: sqlite3.Connection,
) -> None:
    insert_report(conn, "gaming-pc", [440], utc_iso(9))
    insert_report(conn, "broken", "{not json", utc_iso(9))

    result = compute_targets(conn, local(10), stale_after_days=7)

    assert result.appids == [440]
    assert [(c.client_id, c.reason) for c in result.excluded_clients] == [
        ("broken", "unreadable-snapshot")
    ]


def test_an_unreadable_timestamp_excludes_rather_than_assumes_fresh(
    conn: sqlite3.Connection,
) -> None:
    """Fail-safe direction: prefill less, never on an unreadable value."""
    insert_report(conn, "weird-clock", [12345], "yesterday-ish")

    result = compute_targets(conn, local(10), stale_after_days=7)

    assert result.appids == []
    assert [(c.client_id, c.reason) for c in result.excluded_clients] == [
        ("weird-clock", "unreadable-timestamp")
    ]


def test_a_client_deleted_between_the_distinct_query_and_the_lookup_is_silently_skipped(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WP AG-1 review round 1, S2: ``DELETE /v1/clients/{client_id}`` made
    the ``snapshot is None`` guard in ``fresh_client_snapshots`` reachable
    for the first time (a DELETE committing between this function's
    DISTINCT query and its per-client ``latest_snapshot`` lookup). The old
    ``# pragma: no cover`` comment claimed the branch could never fire;
    exercise it directly instead of hoping a real race lands in the window.
    """
    insert_report(conn, "gaming-pc", [440], utc_iso(9))
    insert_report(conn, "steam-deck", [730], utc_iso(9))

    real_latest_snapshot = agent_reports.latest_snapshot

    def vanishing_for_gaming_pc(passed_conn, client_id):
        if client_id == "gaming-pc":
            return None  # simulates DELETE committing right here
        return real_latest_snapshot(passed_conn, client_id)

    monkeypatch.setattr(agent_reports, "latest_snapshot", vanishing_for_gaming_pc)

    fresh, excluded = scheduler_module.fresh_client_snapshots(
        conn, local(10), stale_after_days=7
    )

    # gaming-pc is silently skipped -- neither trusted nor reported as an
    # exclusion reason (it simply no longer exists by the time this asks).
    assert [snapshot.client_id for snapshot in fresh] == ["steam-deck"]
    assert excluded == []


def test_no_clients_means_no_targets(conn: sqlite3.Connection) -> None:
    result = compute_targets(conn, local(10), stale_after_days=7)

    assert (result.appids, result.included_clients, result.excluded_clients) == (
        [],
        [],
        [],
    )


# ==========================================================================
# WP AG-1 review round 1, S1: ``compute_targets`` must be a THIN wrapper
# around ``fresh_client_snapshots`` — a re-inlined verbatim copy of the loop
# (the exact regression this extraction removed) would stay green on every
# BEHAVIOURAL test above, because a hand-copied duplicate can agree with the
# original on every fixture those tests happen to construct. The structural
# pin: replace ``fresh_client_snapshots`` with a fake returning a
# distinguishable sentinel, and assert ``compute_targets`` hands back EXACTLY
# that result — not a superset, not a re-derived agreement (same technique
# ``tests/test_wp4f_shared_cache_content_definition.py`` uses for the
# analogous "which apps are cached" predicate).
# ==========================================================================


def test_compute_targets_returns_exactly_fresh_client_snapshots_result(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A sentinel appid/client_id pair that appears in NO real fixture in this
    # file — if compute_targets derived its own answer instead of returning
    # this fake's result verbatim, these values would never appear.
    sentinel_snapshot = scheduler_module.FreshClientSnapshot(
        client_id="sentinel-client",
        reported_at="2026-01-01T00:00:00Z",
        appids=[999_001, 999_002],
    )
    calls: list[tuple] = []

    def fake_fresh_client_snapshots(passed_conn, now, stale_after_days):
        assert passed_conn is conn
        calls.append((now, stale_after_days))
        return [sentinel_snapshot], []

    monkeypatch.setattr(
        scheduler_module, "fresh_client_snapshots", fake_fresh_client_snapshots
    )

    now = local(10)
    result = compute_targets(conn, now, stale_after_days=7)

    assert result.appids == [999_001, 999_002]
    assert result.included_clients == ["sentinel-client"]
    assert result.excluded_clients == []
    # compute_targets called the shared function exactly once, with exactly
    # the arguments it was given -- not a second, independent staleness pass.
    assert calls == [(now, 7)]


def test_compute_targets_reflects_an_exclusion_from_fresh_client_snapshots(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation-kill counterpart: if the shared function reports NOTHING
    fresh, ``compute_targets`` must report nothing too -- proving there is no
    second, independently-derived path it could fall back on."""
    sentinel_excluded = scheduler_module.ExcludedClient(
        "sentinel-client", "stale", "2020-01-01T00:00:00Z"
    )

    monkeypatch.setattr(
        scheduler_module,
        "fresh_client_snapshots",
        lambda passed_conn, now, stale_after_days: ([], [sentinel_excluded]),
    )

    result = compute_targets(conn, local(10), stale_after_days=7)

    assert result.appids == []
    assert result.included_clients == []
    assert result.excluded_clients == [sentinel_excluded]


# ==========================================================================
# WP 4d — sweep target-set mode: installed PLUS cached (off by default
# through WP 4d; on by default since WP SWEEP-1 / ADR-0014, 2026-08-22)
# ==========================================================================


def test_cached_appids_finds_apps_with_disk_content(tmp_path: Path, conn: sqlite3.Connection) -> None:
    cache_root = tmp_path / "cache"
    write_cached_depot(cache_root, 441)
    upsert_mapping(conn, depotid=441, appid=440, name="TF2")
    # Mapped but never written to disk -- must NOT count as cached.
    upsert_mapping(conn, depotid=999, appid=730, name="CS")

    assert cached_appids(conn, str(cache_root)) == {440}


def test_cached_appids_on_an_empty_or_missing_cache_root_is_empty_not_an_error(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    upsert_mapping(conn, depotid=441, appid=440, name="TF2")

    assert cached_appids(conn, str(tmp_path / "does-not-exist")) == set()


def test_cached_appids_excludes_an_app_whose_only_surviving_content_is_shared(
    tmp_path: Path,
) -> None:
    """B1 (reviewer blocker, real-rig measurement, user decision "Plan A:
    narrow the definition"): after a REAL ``DELETE /v1/cache/{appid}`` that
    frees an app's own depot but keeps a shared depot protected (ADR-0003 —
    the other co-owner still has content), the deleted app must NOT count as
    'cached' just because its surviving mapping row points at a depot that
    happens to have bytes on disk.

    Fixture is the reviewer's own: app 440 has exclusive depot 441 plus depot
    300 shared with app 730; app 730 also has its own exclusive depot 731 and
    is marked prefilled so depot 300 stays SHARED/protected (not a deletable
    remnant) throughout. After deleting 440:

        DELETE /v1/cache/440 -> deleted depot 441, skipped_shared depot 300
        on disk after delete: depot 300 (shared), depot 731 (730's own)
        cached_appids after delete: {730}      -- NOT 440

    This is a real end-to-end HTTP delete against a real filesystem, exactly
    like the reviewer's own rig, not a synthetic ``cached_appids`` call
    against hand-planted disk state — the bug is specifically about what
    survives a REAL deletion.
    """
    cache_root = tmp_path / "cache"
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(cache_root),
        log_level="INFO",
    )
    client = TestClient(create_app(settings))

    def seed_mapping(depotid: int, appid: int, name: str | None = None) -> None:
        response = client.put(
            f"/v1/mapping/{depotid}",
            json={"appid": appid, "app_name": name},
            headers=AUTH,
        )
        assert response.status_code == 200, response.text

    seed_mapping(441, 440, "TF2 (exclusive)")
    seed_mapping(300, 440)
    seed_mapping(300, 730, "CS2 (shares depot 300)")
    seed_mapping(731, 730)
    write_cached_depot(cache_root, 441)
    write_cached_depot(cache_root, 300)
    write_cached_depot(cache_root, 731)

    conn = get_connection(settings.db_path)
    try:
        conn.execute(
            "UPDATE apps SET status = 'done', last_prefill_at = ? WHERE appid = 730",
            ("2026-08-05T10:00:00Z",),
        )
        conn.commit()
    finally:
        conn.close()

    delete_response = client.delete("/v1/cache/440", headers=AUTH).json()
    assert delete_response["deleted_depots"] == [
        {"depotid": 441, "size_bytes_freed": 1, "shared_with_uncached": []}
    ]
    assert delete_response["skipped_shared"] == [
        {"depotid": 300, "shared_with": [730]}
    ]
    assert (cache_root / "depot" / "441").exists() is False
    assert (cache_root / "depot" / "300").exists()  # shared, protected, survives
    assert (cache_root / "depot" / "731").exists()

    conn = get_connection(settings.db_path)
    try:
        result = cached_appids(conn, str(cache_root))
    finally:
        conn.close()

    assert result == {730}
    assert 440 not in result


def test_include_cached_defaults_to_off(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    """Mutation pin: flip ``compute_targets``'s ``include_cached`` default to
    ``True`` (or ``cached_only_appids``'s dataclass default away from ``()``)
    and this test dies -- no agent report exists at all, so a cached-but-
    uninstalled app must never appear unless the caller explicitly opts in.

    ``cache_root`` IS passed (pointing at real cache content) so the pin
    actually exercises the ``include_cached`` default rather than being
    masked by ``cache_root``'s own default of ``""`` (which would make ANY
    value of ``include_cached`` a no-op, since there would be nothing to
    scan) -- caught in review by running this exact mutation.
    """
    cache_root = tmp_path / "cache"
    write_cached_depot(cache_root, 441)
    upsert_mapping(conn, depotid=441, appid=440, name="TF2")

    result = compute_targets(  # no include_cached kwarg -- must default to off
        conn, local(10), stale_after_days=7, cache_root=str(cache_root)
    )

    assert result.appids == []
    assert result.cached_only_appids == ()


def test_include_cached_mode_off_is_byte_identical_to_pre_wp4d_behaviour(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    """Same fixture as the union test below, but with the mode explicitly
    OFF: the presence of cache content and a depot mapping must not change
    a single field of the result relative to what compute_targets returned
    before WP 4d existed (agent-report union only)."""
    cache_root = tmp_path / "cache"
    write_cached_depot(cache_root, 441)  # app 440, cached
    write_cached_depot(cache_root, 731)  # app 730, cached
    upsert_mapping(conn, depotid=441, appid=440, name="TF2")
    upsert_mapping(conn, depotid=731, appid=730, name="CS")
    insert_report(conn, "gaming-pc", [440], utc_iso(9))  # 440 also installed

    result = compute_targets(
        conn, local(10), stale_after_days=7,
        include_cached=False, cache_root=str(cache_root),
    )

    assert result.appids == [440]  # exactly the pre-WP-4d union, no 730
    assert result.cached_only_appids == ()
    assert result.included_clients == ["gaming-pc"]
    assert result.excluded_clients == []


def test_cached_apps_join_the_target_set_when_enabled(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    cache_root = tmp_path / "cache"
    write_cached_depot(cache_root, 441)
    upsert_mapping(conn, depotid=441, appid=440, name="TF2")

    result = compute_targets(
        conn, local(10), stale_after_days=7,
        include_cached=True, cache_root=str(cache_root),
    )

    assert result.appids == [440]
    assert result.cached_only_appids == (440,)


def test_an_app_both_installed_and_cached_appears_exactly_once(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    """Dedupe: overlap between the installed union and the cached set must
    not duplicate the appid, and it must not be reported as 'cached-only'
    (it was already reachable through the installed union)."""
    cache_root = tmp_path / "cache"
    write_cached_depot(cache_root, 441)  # 440: installed AND cached
    write_cached_depot(cache_root, 731)  # 730: cached only
    upsert_mapping(conn, depotid=441, appid=440, name="TF2")
    upsert_mapping(conn, depotid=731, appid=730, name="CS")
    insert_report(conn, "gaming-pc", [440], utc_iso(9))

    result = compute_targets(
        conn, local(10), stale_after_days=7,
        include_cached=True, cache_root=str(cache_root),
    )

    assert result.appids == [440, 730]  # each exactly once
    assert result.cached_only_appids == (730,)  # NOT 440 -- already installed


def test_an_empty_cache_with_the_mode_enabled_adds_nothing_and_errors_never(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    insert_report(conn, "gaming-pc", [440], utc_iso(9))

    result = compute_targets(
        conn, local(10), stale_after_days=7,
        include_cached=True, cache_root=str(tmp_path / "cache"),  # never created
    )

    assert result.appids == [440]
    assert result.cached_only_appids == ()


def test_a_cached_app_survives_its_only_clients_staleness(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    """The case WP 4d exists for (plan §7 Phase 4d): a PC that has gone quiet
    beyond VAULT_SCHEDULE_CLIENT_STALE_DAYS must not stop its cached games
    from being kept current, even though its INSTALLED contribution is
    excluded exactly as before."""
    cache_root = tmp_path / "cache"
    write_cached_depot(cache_root, 441)
    upsert_mapping(conn, depotid=441, appid=440, name="TF2")
    insert_report(
        conn, "retired-pc", [440], utc_iso_of(local(9) - timedelta(days=30))
    )

    off = compute_targets(
        conn, local(10), stale_after_days=7,
        include_cached=False, cache_root=str(cache_root),
    )
    assert off.appids == []  # unchanged: the only client is stale-excluded
    assert [c.reason for c in off.excluded_clients] == ["stale"]

    on = compute_targets(
        conn, local(10), stale_after_days=7,
        include_cached=True, cache_root=str(cache_root),
    )
    assert on.appids == [440]  # cached mode reaches it anyway
    assert on.cached_only_appids == (440,)
    assert [c.reason for c in on.excluded_clients] == ["stale"]  # unrelated, unchanged


def test_maybe_sweep_enqueues_cached_apps_when_the_setting_is_on(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    cache_root = tmp_path / "cache"
    write_cached_depot(cache_root, 441)
    upsert_mapping(conn, depotid=441, appid=440, name="TF2")
    settings = make_settings(tmp_path, cache_root=cache_root)
    settings = replace(settings, sweep_include_cached=True)

    result = maybe_sweep(conn, settings, local(10))

    assert result.swept is True
    assert result.targets == (440,)
    assert result.cached_only_appids == (440,)
    assert [job["appid"] for job in jobs_queue.list_jobs(conn, 10)] == [440]


def test_maybe_sweep_ignores_cache_content_when_the_setting_is_off(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """Explicit opt-out still behaves correctly (WP SWEEP-1, ADR-0014
    changed which value is the DEFAULT, not what either value DOES): with
    ``sweep_include_cached`` explicitly forced to ``False``, cache content
    must still be invisible to the sweep. This used to rely on
    ``make_settings``' ``Settings`` never setting the field at all (i.e. on
    ``Settings``' own dataclass default happening to be ``False``) -- that
    wiring-level mutation pin now lives on the ON side instead, see
    ``test_maybe_sweep_includes_cache_content_by_default`` below, since the
    dataclass default flipped to ``True``.
    """
    cache_root = tmp_path / "cache"
    write_cached_depot(cache_root, 441)
    upsert_mapping(conn, depotid=441, appid=440, name="TF2")
    settings = replace(make_settings(tmp_path, cache_root=cache_root), sweep_include_cached=False)

    result = maybe_sweep(conn, settings, local(10))

    assert result.swept is True
    assert result.targets == ()
    assert jobs_queue.list_jobs(conn, 10) == []


def test_maybe_sweep_includes_cache_content_by_default(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """Mutation pin on the wiring (WP SWEEP-1, ADR-0014; not just
    ``compute_targets``' own keyword default, which stays ``False`` on
    purpose for direct/test callers per ``scheduler.py``'s own docstring):
    ``make_settings``' ``Settings`` never sets ``sweep_include_cached``, so
    this relies on ``Settings``' OWN dataclass default -- flip
    ``DEFAULT_SWEEP_INCLUDE_CACHED`` back to ``False`` and this test dies.
    This is the mirror image of
    ``test_maybe_sweep_ignores_cache_content_when_the_setting_is_off``
    above, which used to be the wiring pin before the default flipped.
    """
    cache_root = tmp_path / "cache"
    write_cached_depot(cache_root, 441)
    upsert_mapping(conn, depotid=441, appid=440, name="TF2")
    settings = make_settings(tmp_path, cache_root=cache_root)

    result = maybe_sweep(conn, settings, local(10))

    assert result.swept is True
    assert result.targets == (440,)
    assert result.cached_only_appids == (440,)


def test_maybe_sweep_logs_the_cached_mode_even_when_it_adds_nothing(
    conn: sqlite3.Connection, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """S1 (reviewer should-fix, 2026-08-18 review round): a mode that is ON
    but happens to add zero targets this sweep (nothing cached yet, an empty
    ``depot_app_map``, ...) must still be VISIBLE in the log, naming the root
    that was scanned -- otherwise this case is byte-identical to the mode
    being off, and "why isn't this doing anything" is undiagnosable from the
    log alone. Deliberately no cache content seeded at all: this is exactly
    the zero-target case the fix targets.
    """
    cache_root = tmp_path / "cache"
    settings = make_settings(tmp_path, cache_root=cache_root)
    settings = replace(settings, sweep_include_cached=True)

    with caplog.at_level("INFO", logger=scheduler_module.__name__):
        result = maybe_sweep(conn, settings, local(10))

    assert result.swept is True
    assert result.targets == ()
    assert result.cached_only_appids == ()

    sweep_lines = [r.getMessage() for r in caplog.records if "sweep at" in r.getMessage()]
    assert len(sweep_lines) == 1
    assert "cached-apps sweep mode ON" in sweep_lines[0]
    assert repr(str(cache_root)) in sweep_lines[0]  # settings.cache_root is %r-logged
    assert "0 target(s) added" in sweep_lines[0]


def test_compute_targets_requires_a_real_cache_root_when_the_mode_is_on(
    conn: sqlite3.Connection,
) -> None:
    """S1: the old ``cache_root: str = ""`` default let a caller (production
    code or a test) forget to pass the root and get a silent, indistinguishable
    'nothing cached' result -- this is exactly the shape that once masked one
    of this module's own mutation-kill tests. Now it is a loud, immediate
    ``ValueError`` for every spelling of 'nothing was given', including
    whitespace-only (N4, reviewer nitpick, 2026-08-18 review round, WP 4f:
    consistency with ``config.py``'s own boot-time ``.strip()`` guard for the
    same value)."""
    for missing in (None, "", "   "):
        with pytest.raises(ValueError, match="cache_root"):
            compute_targets(
                conn, local(10), stale_after_days=7,
                include_cached=True, cache_root=missing,
            )

    # The default (no cache_root kwarg at all) is exactly the same failure --
    # there is no usable default left to fall back on.
    with pytest.raises(ValueError, match="cache_root"):
        compute_targets(conn, local(10), stale_after_days=7, include_cached=True)


# ==========================================================================
# WP SWEEP-1 follow-up (ADR-0014 §"Shipping an enabled nightly schedule",
# S3 review finding): the resolved-timezone startup log line.
# ==========================================================================


def test_format_utc_offset_positive_and_negative():
    assert format_utc_offset(local(10)) == "UTC+02:00"  # local()'s fixed TZ
    assert format_utc_offset(datetime(2026, 8, 6, 10, tzinfo=timezone.utc)) == "UTC+00:00"
    west = timezone(timedelta(hours=-5, minutes=-30))
    assert format_utc_offset(datetime(2026, 8, 6, 10, tzinfo=west)) == "UTC-05:30"


def test_format_utc_offset_is_the_one_shared_implementation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Structural pin (docs/LEARNINGS.md "Testing discipline": byte-identity
    today does not protect tomorrow) for the de-duplication claim in this
    function's own docstring: monkeypatch it to a sentinel and assert BOTH
    known callers -- `describe_resolved_schedule` below and
    `routers.schedule._format_offset` -- return exactly that sentinel,
    never a superset/subset or their own independently-computed answer. A
    future edit that reintroduces a second, separately-maintained offset
    formatter in either caller would pass every other test in this file
    (both would still produce a CORRECT-looking string) and only this test
    would notice the divergence.
    """
    import vault_api.routers.schedule as schedule_router

    sentinel = "UTC+99:99-SENTINEL"
    monkeypatch.setattr(scheduler_module, "format_utc_offset", lambda moment: sentinel)
    monkeypatch.delenv("TZ", raising=False)

    settings = Settings(
        vault_api_key=TEST_API_KEY, db_path=":memory:", cache_root="./cache", log_level="INFO"
    )
    assert sentinel in describe_resolved_schedule(settings, local(10))
    assert schedule_router._format_offset(local(10)) == sentinel


def test_describe_resolved_schedule_reports_disabled_when_no_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TZ", raising=False)
    settings = Settings(
        vault_api_key=TEST_API_KEY, db_path=":memory:", cache_root="./cache", log_level="INFO"
    )
    line = describe_resolved_schedule(settings, local(10))
    assert "DISABLED" in line
    assert "no automatic sweep" in line
    assert "UTC+02:00" in line
    assert "Auto-GC still applies" in line
    assert "TZ unset, resolved to" in line


def test_describe_resolved_schedule_names_window_offset_and_both_next_openings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact mechanism ADR-0014's schedule-window section promises: an
    operator who set nothing sees the window expressed against their
    resolved offset, plus the next opening in BOTH local and UTC time, so a
    TZ mismatch (this fixture uses a deliberately non-UTC +02:00 clock,
    mirroring `local()`'s own fixed offset used throughout this file) is
    visible in one log line rather than requiring the reader to do the math.
    """
    monkeypatch.setenv("TZ", "Europe/Berlin")
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=":memory:",
        cache_root="./cache",
        log_level="INFO",
        schedule_window=parse_window("03:00-07:00"),
    )
    now = local(1)  # 01:00 local, before the window -> opens later TODAY
    line = describe_resolved_schedule(settings, now)
    assert "03:00-07:00" in line
    assert "UTC+02:00" in line
    assert "2026-08-06T03:00:00+02:00" in line  # next opening, LOCAL
    assert "2026-08-06T01:00:00+00:00" in line  # the SAME instant, UTC
    assert "TZ='Europe/Berlin' resolved to" in line
    assert "check for a typo" in line


def test_describe_resolved_schedule_names_a_typod_tz_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review round 2, blocker R2-B2a, measured in the real built image:
    `TZ=Europe/Berlinn` (one extra letter) does NOT fail loudly -- glibc
    falls back to a POSIX-style parse and silently resolves to a zone named
    `Europe` at `UTC+00:00`, a plausible-looking wrong answer, not an error.
    Printing only the resolved side would show "resolved against Europe
    (UTC+00:00)" and nothing would look wrong. This test fixes the clock's
    OWN tzinfo to the measured wrong answer (UTC+00:00, tzname "Europe") so
    the assertion does not depend on this dev machine's own zoneinfo
    database recognising the typo the same way glibc does -- what matters
    is that the REQUESTED value ('Europe/Berlinn') and the RESOLVED one
    ('Europe') both appear side by side, so the mismatch is visible without
    needing a timezone-database lookup of its own.
    """
    monkeypatch.setenv("TZ", "Europe/Berlinn")
    typo_zone = timezone(timedelta(0), name="Europe")  # the measured glibc fallback
    settings = Settings(
        vault_api_key=TEST_API_KEY, db_path=":memory:", cache_root="./cache", log_level="INFO"
    )
    now = datetime(2026, 8, 6, 10, tzinfo=typo_zone)
    line = describe_resolved_schedule(settings, now)
    assert "TZ='Europe/Berlinn' resolved to Europe (UTC+00:00)" in line


def test_describe_resolved_schedule_says_open_right_now_inside_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N2 (should-fix, review round 2): `next_open` returns `now` UNCHANGED
    when already inside the window, which read oddly worded as "next
    opening <now>" (technically true, but not what "next" suggests). A boot
    landing inside the window must say the window is open, not something
    that reads like a stuck clock.
    """
    monkeypatch.delenv("TZ", raising=False)
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=":memory:",
        cache_root="./cache",
        log_level="INFO",
        schedule_window=parse_window("03:00-07:00"),
    )
    now = local(5)  # 05:00 local -- INSIDE 03:00-07:00
    line = describe_resolved_schedule(settings, now)
    assert "OPEN right now" in line
    assert "next opening" not in line


def test_prefill_scheduler_start_logs_the_resolved_schedule_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Integration-level pin for the wiring, not just the pure function
    above: a real `PrefillScheduler` must emit the line, even with no window
    configured (S3: "the scheduler is disabled" must be a visible fact too,
    not silence indistinguishable from "still booting").

    Review round 2, blocker R2-B2b: the line moved from `start()` (logged
    synchronously against the boot snapshot) to the first `_tick()` call
    (logged against `effective_settings`, asynchronously, on the scheduler's
    own thread) -- so this test now calls `stop()` (which joins the thread)
    BEFORE inspecting `caplog`, which is what guarantees the first tick has
    actually run by the time the assertion checks it, rather than racing a
    background thread.
    """
    settings = make_settings(tmp_path, window=None)
    init_db(settings.db_path)
    scheduler = PrefillScheduler(settings, clock=lambda: local(10))
    with caplog.at_level("INFO", logger=scheduler_module.__name__):
        scheduler.start()
        scheduler.stop()
        matches = [r for r in caplog.records if "no VAULT_SCHEDULE_WINDOW" in r.getMessage()]
        assert len(matches) == 1


def test_prefill_scheduler_logs_a_db_override_window_not_disabled(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Review round 2, blocker R2-B2b, the SERIOUS direction measured: with
    no env window but a DB-stored override setting one (ADR-0009, exactly
    what `PATCH /v1/settings` produces), the OLD (`start()`-based) line said
    "the scheduler is DISABLED, no automatic sweep will run" while an
    unattended nightly sweep with executing GC was in fact about to run on
    the very next tick -- backwards, for the one line ADR-0014 offers as
    the reason unattended deletion is acceptable. Logging against
    `effective_settings` (DB-resolved) on the first tick, as fixed, must
    name the DB-stored window, not report DISABLED.
    """
    settings = make_settings(tmp_path, window=None)
    init_db(settings.db_path)
    conn = get_connection(settings.db_path)
    try:
        settings_store.set_override(conn, "schedule_window", "03:00-07:00")
    finally:
        conn.close()

    scheduler = PrefillScheduler(settings, clock=lambda: local(1))
    with caplog.at_level("INFO", logger=scheduler_module.__name__):
        scheduler.start()
        scheduler.stop()
        messages = [r.getMessage() for r in caplog.records if "scheduler:" in r.getMessage()]

    assert any("03:00-07:00" in m for m in messages), messages
    assert not any("DISABLED" in m for m in messages), messages


# ==========================================================================
# WP 4d — the auto-GC coupling
# ==========================================================================


@pytest.mark.parametrize(
    "sweep_include_cached, auto_gc, expected_risk",
    [
        (False, "off", False),
        (True, "off", True),
        # B2 (user decision "nothing is being reclaimed"): dry-run REPORTS
        # what could be freed but frees nothing, so it stays risky -- only
        # 'execute' actually reclaims and clears the flag.
        (True, "dry-run", True),
        (True, "execute", False),
        (False, "execute", False),
        (False, "dry-run", False),  # mode off: no orphans are being CREATED
    ],
)
def test_cached_sweep_gc_risk_table(
    tmp_path: Path, sweep_include_cached: bool, auto_gc: str, expected_risk: bool
) -> None:
    base = make_settings(tmp_path)
    settings = replace(
        base, sweep_include_cached=sweep_include_cached, auto_gc=auto_gc
    )

    assert cached_sweep_gc_risk(settings) is expected_risk


def test_warn_once_fires_only_on_the_off_to_on_transition(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """B2: 'safe' here means ``auto_gc='execute'`` — the only mode that
    actually reclaims — not ``dry-run``, which stays risky (see the table
    test above)."""
    base = make_settings(tmp_path)
    risky = replace(base, sweep_include_cached=True, auto_gc="off")
    still_risky_dry_run = replace(base, sweep_include_cached=True, auto_gc="dry-run")
    safe = replace(base, sweep_include_cached=True, auto_gc="execute")

    with caplog.at_level("INFO", logger=scheduler_module.__name__):
        warned = False
        # Risky, three ticks in a row: exactly one warning, not three.
        for _ in range(3):
            warned = warn_once_if_cached_sweep_without_gc(risky, warned)
        assert warned is True
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1

        # dry-run: STILL risky (B2) -- no new warning (already warned), and
        # emphatically no all-clear either, since nothing was actually fixed.
        warned = warn_once_if_cached_sweep_without_gc(still_risky_dry_run, warned)
        assert warned is True
        assert len([r for r in caplog.records if r.levelname == "WARNING"]) == 1
        assert len([r for r in caplog.records if r.levelname == "INFO"]) == 0

        # The operator ACTUALLY fixes it (execute): the flag resets, and the
        # all-clear is INFO-logged exactly once (reviewer nitpick N1) -- not
        # a second WARNING.
        warned = warn_once_if_cached_sweep_without_gc(safe, warned)
        assert warned is False
        assert len([r for r in caplog.records if r.levelname == "WARNING"]) == 1
        all_clears = [r for r in caplog.records if r.levelname == "INFO"]
        assert len(all_clears) == 1

        # Already safe: calling again must not repeat the all-clear.
        warned = warn_once_if_cached_sweep_without_gc(safe, warned)
        assert warned is False
        assert len([r for r in caplog.records if r.levelname == "INFO"]) == 1

        # Risky again: a FRESH warning, because it is a new transition.
        warned = warn_once_if_cached_sweep_without_gc(risky, warned)
        assert warned is True
        assert len([r for r in caplog.records if r.levelname == "WARNING"]) == 2


def test_the_warning_is_not_a_hard_failure_across_real_ticks(tmp_path: Path) -> None:
    """Integration-level pin: a real PrefillScheduler thread, ticking for
    real, with the risky combination configured from boot, never raises and
    never stops sweeping -- 'not a hard failure' proven by the thread staying
    alive and continuing to do its job, not merely by the pure function
    above never raising."""
    cache_root = tmp_path / "cache"
    write_cached_depot(cache_root, 441)
    settings = make_settings(tmp_path, cache_root=cache_root)
    settings = replace(settings, sweep_include_cached=True, auto_gc="off")

    scheduler = PrefillScheduler(settings, clock=lambda: local(10), tick_seconds=0.01)
    init_db(settings.db_path)
    conn = get_connection(settings.db_path)
    try:
        upsert_mapping(conn, depotid=441, appid=440, name="TF2")
    finally:
        conn.close()

    scheduler.start()
    try:
        wait_for(lambda: scheduler._warned_cached_sweep_gc_risk is True)
        time.sleep(0.2)  # several more ticks
        assert scheduler._thread is not None and scheduler._thread.is_alive()
    finally:
        scheduler.stop()


# ==========================================================================
# maybe_sweep: the gates
# ==========================================================================


def test_no_window_configured_means_no_sweep_ever(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    settings = make_settings(tmp_path, window=None)
    insert_report(conn, "gaming-pc", [440], utc_iso(9))

    result = maybe_sweep(conn, settings, local(10))

    assert (result.swept, result.skipped_reason) == (False, "disabled")
    assert read_state(conn) == EMPTY_STATE
    assert jobs_queue.list_jobs(conn, 10) == []


@pytest.mark.parametrize("hour", [8, 17, 23, 3])
def test_outside_the_window_nothing_happens(
    conn: sqlite3.Connection, tmp_path: Path, hour: int
) -> None:
    settings = make_settings(tmp_path, window="09:00-17:00")
    insert_report(conn, "gaming-pc", [440], utc_iso(9))

    result = maybe_sweep(conn, settings, local(hour))

    assert (result.swept, result.skipped_reason) == (False, "outside-window")
    # Not even the timestamp is stamped — an out-of-window tick is a no-op.
    assert read_state(conn).last_sweep_at is None


def test_an_overnight_window_sweeps_after_midnight(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    settings = make_settings(tmp_path, window="22:00-06:00")
    insert_report(conn, "gaming-pc", [440], utc_iso(21))

    assert maybe_sweep(conn, settings, local(12)).skipped_reason == "outside-window"
    assert maybe_sweep(conn, settings, local(2)).swept is True


def test_inside_the_window_a_sweep_enqueues_every_target(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    settings = make_settings(tmp_path)
    insert_report(conn, "gaming-pc", [440, 730], utc_iso(9))

    result = maybe_sweep(conn, settings, local(10))

    assert result.swept is True
    assert result.targets == (440, 730)
    assert result.enqueued == (440, 730)
    assert result.already_active == ()

    queued = [(job["appid"], job["type"], job["status"]) for job in jobs_queue.list_jobs(conn, 10)]
    assert sorted(queued) == [(440, "prefill", "queued"), (730, "prefill", "queued")]

    state = read_state(conn)
    assert (state.last_sweep_at, state.last_sweep_targets, state.last_sweep_enqueued) == (
        utc_iso(10),
        2,
        2,
    )


def test_a_second_sweep_inside_the_interval_is_refused(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    settings = make_settings(tmp_path, interval_minutes=180)
    insert_report(conn, "gaming-pc", [440], utc_iso(9))

    assert maybe_sweep(conn, settings, local(9, 30)).swept is True
    assert maybe_sweep(conn, settings, local(12, 29)).skipped_reason == (
        "interval-not-elapsed"
    )
    assert maybe_sweep(conn, settings, local(12, 30)).swept is True


def test_apps_already_queued_are_not_enqueued_twice(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """The dedupe is the QUEUE's (WP 1.4), not a pre-filter — same result."""
    settings = make_settings(tmp_path)
    insert_report(conn, "gaming-pc", [440, 730], utc_iso(9))
    jobs_queue.enqueue_prefill(conn, 440)  # e.g. a button press in the app

    result = maybe_sweep(conn, settings, local(10))

    assert result.targets == (440, 730)
    assert result.enqueued == (730,)
    assert result.already_active == (440,)
    assert [job["appid"] for job in jobs_queue.list_jobs(conn, 10)] == [730, 440]
    # last_sweep_enqueued counts NEW jobs only.
    assert read_state(conn).last_sweep_enqueued == 1


def test_a_running_job_also_counts_as_already_active(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    settings = make_settings(tmp_path)
    insert_report(conn, "gaming-pc", [440], utc_iso(9))
    jobs_queue.enqueue_prefill(conn, 440)
    jobs_queue.claim_next_job(conn)  # -> 'running'

    result = maybe_sweep(conn, settings, local(10))

    assert result.already_active == (440,)
    assert len(jobs_queue.list_jobs(conn, 10)) == 1


def test_a_sweep_with_no_targets_still_consumes_the_interval(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """Otherwise an installation with no agents would sweep on every tick."""
    settings = make_settings(tmp_path)

    result = maybe_sweep(conn, settings, local(10))

    assert (result.swept, result.targets) == (True, ())
    assert read_state(conn).last_sweep_at == utc_iso(10)
    assert maybe_sweep(conn, settings, local(10, 1)).skipped_reason == (
        "interval-not-elapsed"
    )


def test_shutdown_mid_sweep_stops_enqueuing_and_is_recorded(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    settings = make_settings(tmp_path)
    insert_report(conn, "gaming-pc", [440, 730, 1091500], utc_iso(9))

    calls = {"n": 0}

    def should_abort() -> bool:
        calls["n"] += 1
        return calls["n"] > 1  # let the first app through, then "stop"

    result = maybe_sweep(conn, settings, local(10), should_abort=should_abort)

    assert result.aborted is True
    assert result.enqueued == (440,)
    assert [job["appid"] for job in jobs_queue.list_jobs(conn, 10)] == [440]
    # The claim stands: the next sweep is one interval away and recomputes the
    # whole target set, so nothing is "resumed" or lost.
    assert read_state(conn).last_sweep_enqueued == 1


def test_a_sweep_that_crashes_after_claiming_does_not_retry_immediately(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings(tmp_path)
    insert_report(conn, "gaming-pc", [440], utc_iso(9))

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated crash mid-sweep")

    monkeypatch.setattr(scheduler_module, "compute_targets", boom)
    with pytest.raises(RuntimeError):
        maybe_sweep(conn, settings, local(10))
    monkeypatch.undo()

    # Claim-then-work: the timestamp is committed before any work, so the
    # failed sweep consumed its interval instead of hammering on every tick.
    state = read_state(conn)
    assert state.last_sweep_at == utc_iso(10)
    assert state.last_sweep_targets is None  # honest: that sweep never finished
    assert maybe_sweep(conn, settings, local(10, 1)).skipped_reason == (
        "interval-not-elapsed"
    )


# ==========================================================================
# next_eligible_at
# ==========================================================================


def test_next_eligible_is_now_when_a_sweep_is_due(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)

    assert next_eligible_at(settings, EMPTY_STATE, local(10)) == utc_iso(10)


def test_next_eligible_adds_the_interval_inside_the_window(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, interval_minutes=180)
    state = ScheduleState(utc_iso(10), 5, 2)

    assert next_eligible_at(settings, state, local(10, 5)) == utc_iso(13)


def test_next_eligible_skips_to_the_next_window_opening(tmp_path: Path) -> None:
    """Interval lands after the window closes -> tomorrow's opening."""
    settings = make_settings(tmp_path, window="09:00-17:00", interval_minutes=180)
    state = ScheduleState(utc_iso(16), 5, 2)

    assert next_eligible_at(settings, state, local(16, 5)) == utc_iso(9, day=7)


def test_next_eligible_is_null_when_disabled(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, window=None)

    assert next_eligible_at(settings, EMPTY_STATE, local(10)) is None


# ==========================================================================
# GET /v1/schedule
# ==========================================================================


def test_schedule_endpoint_requires_the_api_key(tmp_path: Path) -> None:
    client = TestClient(create_app(make_settings(tmp_path)))

    assert client.get("/v1/schedule").status_code == 401


def test_schedule_endpoint_reports_config_and_last_sweep(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, window="09:00-17:00", interval_minutes=180)
    app = create_app(settings)
    app.state.scheduler = PrefillScheduler(settings, clock=lambda: local(10, 5))

    conn = get_connection(settings.db_path)
    try:
        insert_report(conn, "gaming-pc", [440, 730], utc_iso(9))
        maybe_sweep(conn, settings, local(10))
    finally:
        conn.close()

    body = TestClient(app).get("/v1/schedule", headers=AUTH).json()

    assert body == {
        "enabled": True,
        "window": "09:00-17:00",
        "overnight": False,
        "interval_minutes": 180,
        "client_stale_days": 7,
        "server_timezone": "UTC+02:00",
        "last_sweep_at": utc_iso(10),
        "last_sweep_targets": 2,
        "last_sweep_enqueued": 2,
        "next_eligible_at": utc_iso(13),
        # WP SWEEP-1 (ADR-0014): `make_settings` does not set
        # `sweep_include_cached`/`auto_gc` explicitly, so this reports
        # `Settings`' own dataclass defaults -- `True` /
        # `auto_gc_executes` respectively since the flip, hence
        # `sweep_cached_gc_risk` (on AND NOT executing) stays `False`.
        "sweep_include_cached": True,
        "sweep_cached_gc_risk": False,
    }


def test_schedule_endpoint_reports_a_disabled_scheduler(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, window=None)
    app = create_app(settings)
    app.state.scheduler = PrefillScheduler(settings, clock=lambda: local(10))

    body = TestClient(app).get("/v1/schedule", headers=AUTH).json()

    assert body["enabled"] is False
    assert body["window"] is None
    assert body["next_eligible_at"] is None
    # The defaults are still reported, so an operator can see what enabling
    # the window would do.
    assert (body["interval_minutes"], body["client_stale_days"]) == (180, 7)


def test_schedule_endpoint_marks_an_overnight_window(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, window="22:00-06:00")
    app = create_app(settings)
    app.state.scheduler = PrefillScheduler(settings, clock=lambda: local(23))

    body = TestClient(app).get("/v1/schedule", headers=AUTH).json()

    assert (body["window"], body["overnight"]) == ("22:00-06:00", True)


def test_schedule_endpoint_reports_the_cached_sweep_mode_and_its_gc_risk(
    tmp_path: Path,
) -> None:
    """WP 4d: the mode itself, plus the auto-GC risk flag it is coupled
    with, additively -- every other field stays exactly as documented above."""
    settings = replace(
        make_settings(tmp_path), sweep_include_cached=True, auto_gc="off"
    )
    app = create_app(settings)
    app.state.scheduler = PrefillScheduler(settings, clock=lambda: local(10))

    body = TestClient(app).get("/v1/schedule", headers=AUTH).json()

    assert body["sweep_include_cached"] is True
    assert body["sweep_cached_gc_risk"] is True


def test_schedule_endpoint_still_reports_gc_risk_under_dry_run(
    tmp_path: Path,
) -> None:
    """B2: dry-run REPORTS what could be reclaimed but reclaims nothing, so
    the field asserted here means "orphans created by refreshes are actually
    being reclaimed" -- dry-run does not make that true."""
    settings = replace(
        make_settings(tmp_path), sweep_include_cached=True, auto_gc="dry-run"
    )
    app = create_app(settings)
    app.state.scheduler = PrefillScheduler(settings, clock=lambda: local(10))

    body = TestClient(app).get("/v1/schedule", headers=AUTH).json()

    assert body["sweep_include_cached"] is True
    assert body["sweep_cached_gc_risk"] is True


def test_schedule_endpoint_reports_no_gc_risk_once_auto_gc_executes(
    tmp_path: Path,
) -> None:
    settings = replace(
        make_settings(tmp_path), sweep_include_cached=True, auto_gc="execute"
    )
    app = create_app(settings)
    app.state.scheduler = PrefillScheduler(settings, clock=lambda: local(10))

    body = TestClient(app).get("/v1/schedule", headers=AUTH).json()

    assert body["sweep_include_cached"] is True
    assert body["sweep_cached_gc_risk"] is False


# ==========================================================================
# The thread, end to end (scheduler -> queue -> worker -> SteamPrefill stub)
# ==========================================================================

#: A real-shaped SteamPrefill summary for a non-forced, already-current run
#: (ADR-0006 decision 1: Updated=0 AND UpToDate>0 is the 'confirmed current'
#: outcome). Without it the worker's job-outcome rule would read 0/0 as
#: "app never considered" and fail the job — the WP 1.7 trap.
UP_TO_DATE_SUMMARY = (
    "Prefilled 1 apps totaling 0 b in 03.2491\n"
    "\n"
    " Updated | Up To Date\n"
    "---------+------------\n"
    "    0    |      1\n"
)


def wait_for(predicate, timeout: float = 30.0, what: str = "condition") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    pytest.fail(f"{what} did not happen within {timeout}s")


def test_scheduler_thread_sweeps_and_the_worker_drains_the_queue(
    tmp_path: Path,
) -> None:
    """The whole WP 3.5 path with a real second thread, no real waiting.

    The clock is FROZEN inside the window, which makes the run deterministic
    in both directions: the first tick sweeps (nothing has ever been swept),
    and no later tick can, because the frozen clock never advances past the
    interval.

    App 440 is pre-seeded as already filled (``needs_force = 0``), so this
    also proves the sweep produces a **non-forced** run — ADR-0006's ~3 s
    no-op staleness check, which is the entire reason a sweep over an
    unchanged library is cheap. A scheduler that forced every run would
    re-touch every chunk of every game every three hours.
    """
    bindir = tmp_path / "bin"
    cache_root = tmp_path / "cache"
    executable = stub_prefill.make_stub(
        bindir,
        cache_root=str(cache_root),
        depots_by_app={440: [441]},
        summary_text=UP_TO_DATE_SUMMARY,
    )
    settings = make_settings(
        tmp_path, steamprefill_path=executable, cache_root=cache_root
    )

    app = create_app(settings)
    app.state.scheduler = PrefillScheduler(
        settings, clock=lambda: local(10), tick_seconds=0.02
    )

    conn = get_connection(settings.db_path)
    try:
        insert_report(conn, "gaming-pc", [440], utc_iso(9, 30))
        conn.execute(
            "INSERT INTO apps (appid, status, needs_force) VALUES (440, 'done', 0)"
        )
        conn.commit()
    finally:
        conn.close()

    with TestClient(app) as client:
        wait_for(
            lambda: any(
                job["status"] == "done"
                for job in client.get("/v1/jobs", headers=AUTH).json()
            ),
            what="a scheduled prefill job finishing",
        )

        finished = client.get("/v1/jobs", headers=AUTH).json()
        assert len(finished) == 1, finished
        assert (finished[0]["appid"], finished[0]["type"]) == (440, "prefill")
        assert finished[0]["updated"] == 0 and finished[0]["up_to_date"] == 1

        schedule = client.get("/v1/schedule", headers=AUTH).json()
        assert schedule["enabled"] is True
        assert schedule["last_sweep_at"] == utc_iso(10)
        assert (schedule["last_sweep_targets"], schedule["last_sweep_enqueued"]) == (1, 1)

    # ADR-0006 decision 2: a scheduled run of an already-filled app is NOT
    # forced. (--no-ansi is always passed; see prefill.py.)
    assert stub_prefill.read_argv(bindir) == ["prefill", "--no-ansi", "--max-threads", "8"]
    assert stub_prefill.read_selection(bindir) == [440]
    # The frozen clock guarantees the interval never elapses again.
    assert len(stub_prefill.read_runs(bindir)) == 1


def test_a_scheduled_first_fill_still_runs_forced(tmp_path: Path) -> None:
    """The scheduler goes through the SAME needs_force path as everything else.

    A never-filled app (``apps.needs_force`` defaults to 1) must still get
    ``--force`` when the sweep is what enqueued it — the flag belongs to the
    app, not to whoever pressed the button.
    """
    bindir = tmp_path / "bin"
    cache_root = tmp_path / "cache"
    executable = stub_prefill.make_stub(
        bindir,
        cache_root=str(cache_root),
        depots_by_app={730: [731]},
        summary_text="Prefilled 1 apps totaling 75.97 MiB in 16.55\n\n"
        " Updated | Up To Date\n"
        "---------+------------\n"
        "    1    |      0\n",
    )
    settings = make_settings(
        tmp_path, steamprefill_path=executable, cache_root=cache_root
    )

    app = create_app(settings)
    app.state.scheduler = PrefillScheduler(
        settings, clock=lambda: local(10), tick_seconds=0.02
    )

    conn = get_connection(settings.db_path)
    try:
        insert_report(conn, "steam-deck", [730], utc_iso(9, 30))
    finally:
        conn.close()

    with TestClient(app) as client:
        wait_for(
            lambda: any(
                job["status"] == "done"
                for job in client.get("/v1/jobs", headers=AUTH).json()
            ),
            what="the scheduled first fill finishing",
        )

    assert stub_prefill.read_argv(bindir) == ["prefill", "--force", "--no-ansi", "--max-threads", "8"]


def test_the_scheduler_thread_starts_even_with_no_window_but_sweeps_nothing(
    tmp_path: Path,
) -> None:
    """Disabled is the DEFAULT for SWEEPING, but the THREAD itself now always
    exists (settings-API work package, ADR-0009, reviewer blocker B1).

    Before that fix, ``main.py`` only started this thread when
    ``scheduler.thread_needed`` was true at BOOT, so a stock deployment with
    no window and no event log configured got no thread at all — and no
    later ``PATCH /v1/settings`` enabling ``schedule_window`` (``applies:
    "next_sweep"``) could ever have anything to tick it. ``main.py`` now
    calls ``scheduler.start()`` unconditionally; this test pins the other
    half of that fix, that an always-running thread over a disabled
    scheduler still does the same amount of real work as before: none. See
    ``tests/test_settings_api.py``'s
    ``test_b1_scheduler_thread_exists_on_a_bare_boot_and_a_patched_window_sweeps``
    for the end-to-end "and a PATCH afterwards actually sweeps" half.
    """
    settings = make_settings(tmp_path, window=None)
    app = create_app(settings)
    app.state.scheduler = PrefillScheduler(
        settings, clock=lambda: local(10), tick_seconds=0.01
    )

    conn = get_connection(settings.db_path)
    try:
        insert_report(conn, "gaming-pc", [440], utc_iso(9, 30))
    finally:
        conn.close()

    with TestClient(app) as client:
        assert app.state.scheduler.enabled is False
        wait_for(
            lambda: any(
                thread.name == "vault-prefill-scheduler" and thread.is_alive()
                for thread in threading.enumerate()
            )
        )
        # Plenty of tick intervals' worth of wall clock — nothing is queued,
        # even though the thread is very much alive and ticking.
        time.sleep(0.3)
        assert client.get("/v1/jobs", headers=AUTH).json() == []
        assert client.get("/v1/schedule", headers=AUTH).json()["last_sweep_at"] is None


def test_the_scheduler_thread_is_stopped_with_the_app(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    app.state.scheduler = PrefillScheduler(
        settings, clock=lambda: local(10), tick_seconds=0.02
    )

    with TestClient(app):
        wait_for(
            lambda: any(
                thread.name == "vault-prefill-scheduler" and thread.is_alive()
                for thread in threading.enumerate()
            ),
            what="the scheduler thread starting",
        )

    assert app.state.scheduler.stopping is True
    assert not any(
        thread.name == "vault-prefill-scheduler" and thread.is_alive()
        for thread in threading.enumerate()
    )


def test_a_failing_tick_does_not_kill_the_thread(tmp_path: Path) -> None:
    """The last-resort net: a bug in a sweep must not silently end scheduling."""
    settings = make_settings(tmp_path)
    scheduler = PrefillScheduler(settings, clock=lambda: local(10), tick_seconds=0.01)
    init_db(settings.db_path)

    ticks = {"n": 0}
    original = scheduler_module.maybe_sweep

    def flaky(*args: object, **kwargs: object):
        ticks["n"] += 1
        if ticks["n"] <= 3:
            raise RuntimeError("simulated tick failure")
        return original(*args, **kwargs)  # type: ignore[arg-type]

    scheduler_module.maybe_sweep = flaky  # type: ignore[assignment]
    try:
        scheduler.start()
        wait_for(lambda: ticks["n"] > 5, what="the thread surviving failed ticks")
    finally:
        scheduler.stop()
        scheduler_module.maybe_sweep = original  # type: ignore[assignment]

    conn = get_connection(settings.db_path)
    try:
        assert read_state(conn).last_sweep_at == utc_iso(10)  # it recovered
    finally:
        conn.close()
