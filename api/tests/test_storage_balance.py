"""WP STORAGE-BALANCE-1: the storage balance in the cache summary and the
update check log.

Guarantees pinned here, by name:

1. **Shared depots count once** in planned/present (per depot and manifest).
2. **Every depot directory lands in one class**: measured (present + old
   builds), unmeasured mapped, or unmapped; ``unexplained`` is the rest and is
   0 when snapshot and rows agree, negative when present exceeds the disk.
3. **Warning threshold**: old builds + |unexplained| above 15 % or 50 GB.
4. **The API** reports it additively in ``GET /v1/cache/summary``.
5. **The update check** logs one INFO line per run and WARNING only when the
   flag first turns on; it never walks the disk itself.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_cache_complete_1a import (  # noqa: F401 - fixture
    AUTH,
    chunks,
    conn,
    record,
    settings_for,
)
from tests.test_gc import write_archive_bin, write_chunks
from vault_api import cache_completeness, sizes, storage_balance, update_check
from vault_api.cache_completeness import CompletenessIndex, DepotCompleteness
from vault_api.main import create_app
from vault_api.mapping import upsert_mapping
from vault_api.sizes import SizeCache

GB = storage_balance.GB


def ok_row(appid: int, depotid: int, gid: str, expected: int, present: int) -> DepotCompleteness:
    return DepotCompleteness(
        appid=appid,
        depotid=depotid,
        manifestid=gid,
        status=cache_completeness.STATUS_OK,
        checked_at="2026-11-01T00:00:00Z",
        expected_bytes=expected,
        present_bytes=present,
        missing_chunks=0,
    )


def index_of(
    rows: list[DepotCompleteness], targets: dict[tuple[int, int], str | None]
) -> CompletenessIndex:
    return CompletenessIndex(rows={(r.appid, r.depotid): r for r in rows}, targets=targets)


# --------------------------------------------------------------------------
# 1 + 2. computation
# --------------------------------------------------------------------------


def test_shared_depot_counts_once_and_every_class_is_split() -> None:
    # 100: shared by apps 1 and 2, same target, measured under both.
    # 200: app 1 only, measured, 30 bytes of an older build on disk.
    # 300: app 3, mapped but never measured (no row).
    # 400: app 4, row for an OLDER target -> not measured -> unmeasured.
    # 500: no mapping row at all.
    rows = [
        ok_row(1, 100, "11", expected=50, present=50),
        ok_row(2, 100, "11", expected=50, present=50),
        ok_row(1, 200, "21", expected=80, present=70),
        ok_row(4, 400, "40", expected=10, present=10),
    ]
    targets = {(1, 100): "11", (2, 100): "11", (1, 200): "21", (3, 300): "31", (4, 400): "41"}
    disk = {100: 50, 200: 100, 300: 7, 400: 9, 500: 13}
    balance = storage_balance.compute(disk, [100, 200, 300, 400], index_of(rows, targets))

    assert balance.on_disk_bytes == 179
    assert balance.planned_bytes == 130  # 50 once + 80
    assert balance.present_bytes == 120  # 50 once + 70
    assert balance.measured_depots == 2
    assert balance.old_builds_bytes == 30  # depot 200: 100 on disk - 70 present
    assert (balance.unmeasured_bytes, balance.unmeasured_depots) == (16, 2)
    assert (balance.unmapped_bytes, balance.unmapped_depots) == (13, 1)
    assert balance.unexplained_bytes == 0


def test_present_above_disk_shows_as_negative_unexplained() -> None:
    # The size snapshot is older than the measurement: the depot has 40 bytes
    # in the snapshot but 60 present in the row. Never floored away.
    rows = [ok_row(1, 100, "11", expected=60, present=60)]
    balance = storage_balance.compute({100: 40}, [100], index_of(rows, {(1, 100): "11"}))
    assert balance.old_builds_bytes == 0
    assert balance.unexplained_bytes == -20
    # A measured depot gone from disk entirely: its present bytes stay in
    # ``present`` and the residual says so.
    gone = storage_balance.compute({}, [100], index_of(rows, {(1, 100): "11"}))
    assert gone.unexplained_bytes == -60


def test_present_is_capped_at_expected_like_the_figure() -> None:
    rows = [ok_row(1, 100, "11", expected=50, present=80)]
    balance = storage_balance.compute({100: 50}, [100], index_of(rows, {(1, 100): "11"}))
    assert balance.present_bytes == 50
    assert balance.unexplained_bytes == 0


def test_empty_cache_is_all_zero_without_warning() -> None:
    balance = storage_balance.compute({}, [], index_of([], {}))
    assert balance.on_disk_bytes == 0
    assert balance.unexplained_bytes == 0
    assert balance.warning is False
    assert balance.warning_reason is None


# --------------------------------------------------------------------------
# 3. warning threshold
# --------------------------------------------------------------------------


def _with_old(disk_bytes: int, present: int) -> storage_balance.StorageBalance:
    rows = [ok_row(1, 100, "11", expected=present, present=present)]
    return storage_balance.compute({100: disk_bytes}, [100], index_of(rows, {(1, 100): "11"}))


def test_warning_above_the_fraction() -> None:
    # 16 % old builds of 100 GB: above 15 %, below 50 GB.
    balance = _with_old(100 * GB, 84 * GB)
    assert balance.warning is True
    assert balance.warning_reason is not None
    assert "16.0 GB" in balance.warning_reason
    assert "16 %" in balance.warning_reason


def test_no_warning_at_or_below_both_limits() -> None:
    # Exactly 15 % of 100 GB: not above.
    assert _with_old(100 * GB, 85 * GB).warning is False
    assert _with_old(100 * GB, 85 * GB).warning_reason is None


def test_warning_above_the_byte_limit_even_below_the_fraction() -> None:
    # 51 GB of 1000 GB = 5.1 %: below 15 %, above 50 GB.
    balance = _with_old(1000 * GB, 949 * GB)
    assert balance.warning is True
    assert "51.0 GB" in (balance.warning_reason or "")


def test_unexplained_counts_toward_the_warning_in_both_directions() -> None:
    rows = [ok_row(1, 100, "11", expected=60 * GB, present=60 * GB)]
    balance = storage_balance.compute({100: 0}, [100], index_of(rows, {(1, 100): "11"}))
    assert balance.unexplained_bytes == -60 * GB
    assert balance.warning is False  # on_disk 0: nothing to warn about
    balance = storage_balance.compute(
        {100: 1 * GB, 200: 100 * GB}, [100], index_of(rows, {(1, 100): "11"})
    )
    assert balance.unexplained_bytes == -59 * GB
    assert balance.warning is True
    assert "unexplained" in (balance.warning_reason or "")


def test_unmeasured_and_unmapped_alone_never_warn() -> None:
    balance = storage_balance.compute({300: 500 * GB, 500: 100 * GB}, [300], index_of([], {}))
    assert balance.unmeasured_bytes == 500 * GB
    assert balance.unmapped_bytes == 100 * GB
    assert balance.warning is False


# --------------------------------------------------------------------------
# 4. the API (real disk, real measurement)
# --------------------------------------------------------------------------


def seed_disk(conn: sqlite3.Connection, settings) -> None:
    """440 and 730 share depot 900 (both target "9"); 441 holds 3 old-build
    chunks next to its 2 current ones; 551 is mapped to 550 with no target;
    depot 777 has no mapping."""
    cache = Path(settings.cache_root)
    archive = Path(settings.manifest_archive_dir)
    record(conn, 440, 441, "4", "2026-10-01T00:00:00Z")
    write_archive_bin(archive, depotid=441, manifestid="4", chunks=chunks(0, 2))
    write_chunks(cache, 441, chunks(0, 5))  # 0-1 current, 2-4 an older build
    record(conn, 440, 900, "9", "2026-10-01T00:00:00Z")
    record(conn, 730, 900, "9", "2026-10-01T00:00:00Z")
    write_archive_bin(archive, depotid=900, manifestid="9", chunks=chunks(20, 3))
    write_chunks(cache, 900, chunks(20, 3))
    upsert_mapping(conn, 551, 550, "Tool")
    write_chunks(cache, 551, chunks(40, 1))
    write_chunks(cache, 777, chunks(50, 2))
    cache_completeness.evaluate_apps(conn, settings, [440, 730])


def test_summary_reports_the_balance(tmp_path: Path, conn: sqlite3.Connection) -> None:
    settings = settings_for(tmp_path)
    seed_disk(conn, settings)
    body = TestClient(create_app(settings)).get("/v1/cache/summary", headers=AUTH).json()

    # Existing fields untouched.
    assert set(body) >= {"total_bytes", "top_consumers", "unmapped_depots", "free_disk_bytes"}
    assert body["unmapped_depots"] == {"count": 1, "size_bytes": 200}
    balance = body["storage_balance"]
    assert balance == {
        "on_disk_bytes": body["total_bytes"],
        "planned_bytes": 500,  # 441: 200 + 900: 300, the shared depot once
        "present_bytes": 500,
        "measured_depots": 2,
        "old_builds_bytes": 300,  # 441's three older chunks
        "unmeasured_bytes": 100,
        "unmeasured_depots": 1,
        "unmapped_bytes": 200,
        "unmapped_depots": 1,
        "unexplained_bytes": 0,
        # 300 of 1100 bytes = 27 %
        "warning": True,
        "warning_reason": balance["warning_reason"],
    }
    assert body["total_bytes"] == 1100
    assert "27 %" in balance["warning_reason"]


def test_summary_balance_on_an_empty_cache(client: TestClient) -> None:
    body = client.get("/v1/cache/summary", headers=AUTH).json()
    assert body["storage_balance"]["on_disk_bytes"] == 0
    assert body["storage_balance"]["warning"] is False


# --------------------------------------------------------------------------
# 5. the update check log line
# --------------------------------------------------------------------------


def _run_check(runner: update_check.UpdateCheckRunner, conn, settings) -> None:
    started = runner.start(
        conn,
        settings,
        trigger=update_check.TRIGGER_MANUAL,
        select_appids=lambda _c: [440, 730],
        now=datetime.now(timezone.utc),
        require_interval=False,
    )
    assert started == update_check.STARTED
    assert runner.wait(10)


def test_update_check_logs_the_balance_and_warns_once(
    tmp_path: Path,
    conn: sqlite3.Connection,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = settings_for(tmp_path)
    seed_disk(conn, settings)
    walks: list[str] = []
    real_scan = sizes.scan_depot_signatures

    def counting_scan(root: str, *args: object, **kwargs: object):
        walks.append(root)
        return real_scan(root, *args, **kwargs)

    # Counts EVERY depot walk in the process, not only the cache's own.
    monkeypatch.setattr(sizes, "scan_depot_signatures", counting_scan)
    size_cache = SizeCache(ttl_seconds=3600)
    size_cache.get(settings.cache_root)  # the walk the API already made

    def fetch(url: str) -> bytes:
        raise update_check.oracle.OracleError("offline in this test")

    runner = update_check.UpdateCheckRunner(
        settings.db_path, fetch=fetch, pause_seconds=0, size_cache=size_cache
    )
    caplog.set_level(logging.INFO, logger="vault_api")
    _run_check(runner, conn, settings)
    _run_check(runner, conn, settings)

    lines = [r for r in caplog.records if r.getMessage().startswith("storage balance: on disk")]
    assert len(lines) == 2
    assert lines[0].levelno == logging.INFO
    assert lines[0].getMessage() == (
        "storage balance: on disk 0.0 GB, planned 0.0 GB, present 0.0 GB, "
        "old builds 0.0 GB, unmeasured 0.0 GB (1 depots), unmapped 0.0 GB "
        "(1 depots), unexplained 0.0 GB, sizes from a walk 0 min old"
    )
    warnings = [r for r in caplog.records if r.getMessage().startswith("storage balance warning")]
    assert len(warnings) == 1  # first crossing only
    assert warnings[0].levelno == logging.WARNING
    assert walks == [settings.cache_root]  # no walk of its own


@pytest.fixture
def walk_count(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every depot walk in the process (the size cache's included)."""
    walks: list[str] = []
    real_scan = sizes.scan_depot_signatures

    def counting_scan(root: str, *args: object, **kwargs: object):
        walks.append(root)
        return real_scan(root, *args, **kwargs)

    monkeypatch.setattr(sizes, "scan_depot_signatures", counting_scan)
    return walks


def _offline(url: str) -> bytes:
    raise update_check.oracle.OracleError("offline in this test")


def test_an_expired_snapshot_is_used_with_its_age_and_starts_no_walk(
    tmp_path: Path,
    conn: sqlite3.Connection,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    walk_count: list[str],
) -> None:
    """Reviewer M1: ``get()`` on an expired snapshot starts a background
    walk; the balance line must not."""
    settings = settings_for(tmp_path)
    seed_disk(conn, settings)
    size_cache = SizeCache(ttl_seconds=3600)
    snapshot = size_cache.get(settings.cache_root)
    assert size_cache.wait_idle(5)
    assert len(walk_count) == 1
    size_cache._ttl = 0.0  # expired from now on: get() would start a walk
    monkeypatch.setattr(
        update_check.time, "monotonic", lambda: snapshot.computed_at + 10 * 60 + 5
    )
    runner = update_check.UpdateCheckRunner(
        settings.db_path, fetch=_offline, pause_seconds=0, size_cache=size_cache
    )
    caplog.set_level(logging.INFO, logger="vault_api")
    _run_check(runner, conn, settings)
    assert size_cache.wait_idle(5)
    assert len(walk_count) == 1, "no walk, not even the background refresh"
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("storage balance: on disk")]
    assert len(lines) == 1
    assert lines[0].endswith(", sizes from a walk 10 min old")


def test_an_invalidated_snapshot_skips_the_line_without_walk_or_wait(
    tmp_path: Path,
    conn: sqlite3.Connection,
    caplog: pytest.LogCaptureFixture,
    walk_count: list[str],
) -> None:
    """Reviewer M1: ``get()`` after ``invalidate()`` (every job, delete, GC)
    blocks for a full walk while the run holds its lock."""
    settings = settings_for(tmp_path)
    seed_disk(conn, settings)
    size_cache = SizeCache(ttl_seconds=3600)
    size_cache.get(settings.cache_root)
    size_cache.invalidate()
    runner = update_check.UpdateCheckRunner(
        settings.db_path, fetch=_offline, pause_seconds=0, size_cache=size_cache
    )
    caplog.set_level(logging.INFO, logger="vault_api")
    _run_check(runner, conn, settings)
    assert size_cache.wait_idle(5)
    assert len(walk_count) == 1, "no walk after the invalidation"
    messages = [r.getMessage() for r in caplog.records if "storage balance" in r.getMessage()]
    assert len(messages) == 1
    assert messages[0].startswith("storage balance: skipped, no current size snapshot")


def test_peek_never_walks_and_never_waits(tmp_path: Path, walk_count: list[str]) -> None:
    root = str(tmp_path / "cache")
    cache = SizeCache(ttl_seconds=3600)
    assert cache.peek(root) is None  # nothing yet: no walk started
    assert walk_count == []
    snapshot = cache.get(root)
    assert cache.wait_idle(5)
    cache._ttl = 0.0  # expired from now on
    walks = len(walk_count)
    assert cache.peek(root) is snapshot  # expired, still served as is
    assert cache.peek(str(tmp_path / "other")) is None
    cache.invalidate()
    assert cache.peek(root) is None
    assert cache.wait_idle(5)
    assert len(walk_count) == walks


def test_log_line_with_real_values_and_a_negative_residual() -> None:
    """Reviewer N2: sign and rounding of ``format_gb``."""
    balance = storage_balance.StorageBalance(
        on_disk_bytes=812 * GB + GB // 2,
        planned_bytes=int(401.26 * GB),
        present_bytes=int(390.24 * GB),
        measured_depots=42,
        old_builds_bytes=int(120.96 * GB),
        unmeasured_bytes=int(13.75 * GB),
        unmeasured_depots=24,
        unmapped_bytes=int(38.7 * GB),
        unmapped_depots=3,
        unexplained_bytes=-int(2.36 * GB),
        warning=True,
        warning_reason="x",
    )
    assert storage_balance.describe(balance, 125.0) == (
        "storage balance: on disk 812.5 GB, planned 401.3 GB, present 390.2 GB, "
        "old builds 121.0 GB, unmeasured 13.8 GB (24 depots), unmapped 38.7 GB "
        "(3 depots), unexplained -2.4 GB, sizes from a walk 2 min old"
    )
    assert storage_balance.format_gb(-int(0.04 * GB)) == "0.0 GB", "never -0.0"
    assert storage_balance.format_gb(-int(0.06 * GB)) == "-0.1 GB"
    assert storage_balance.describe(balance).endswith("unexplained -2.4 GB"), "no age, no suffix"


def test_warning_reason_points_at_garbage_collection() -> None:
    reason = _with_old(100 * GB, 50 * GB).warning_reason or ""
    assert storage_balance.GC_HINT in reason
    assert storage_balance.UNEXPLAINED_HINT not in reason
    rows = [ok_row(1, 100, "11", expected=60 * GB, present=60 * GB)]
    both = storage_balance.compute(
        {100: 1 * GB, 200: 100 * GB}, [100], index_of(rows, {(1, 100): "11"})
    )
    assert storage_balance.UNEXPLAINED_HINT in (both.warning_reason or "")


def test_update_check_without_size_cache_logs_no_balance(
    tmp_path: Path, conn: sqlite3.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    settings = settings_for(tmp_path)
    seed_disk(conn, settings)

    def fetch(url: str) -> bytes:
        raise update_check.oracle.OracleError("offline")

    runner = update_check.UpdateCheckRunner(settings.db_path, fetch=fetch, pause_seconds=0)
    caplog.set_level(logging.INFO, logger="vault_api")
    _run_check(runner, conn, settings)
    assert not [r for r in caplog.records if "storage balance" in r.getMessage()]


def test_a_failing_balance_does_not_change_the_outcome(
    tmp_path: Path, conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = settings_for(tmp_path)
    seed_disk(conn, settings)

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("bug")

    monkeypatch.setattr(update_check.storage_balance, "compute_from_db", boom)

    def fetch(url: str) -> bytes:
        raise update_check.oracle.OracleError("offline")

    size_cache = SizeCache()
    size_cache.get(settings.cache_root)  # a usable snapshot, so compute runs
    runner = update_check.UpdateCheckRunner(
        settings.db_path, fetch=fetch, pause_seconds=0, size_cache=size_cache
    )
    _run_check(runner, conn, settings)
    status = update_check.read_status(conn, settings, running=False)
    assert status.last_outcome == update_check.OUTCOME_COMPLETED


def test_balance_logger_warns_on_first_crossing_and_notes_the_clear(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="vault_api.storage_balance")
    log = storage_balance.BalanceLogger()
    high = _with_old(100 * GB, 50 * GB)
    low = _with_old(100 * GB, 100 * GB)
    for balance in (high, high, low, low, high):
        log.log(balance)
    levels = [
        (r.levelno, r.getMessage().split(":")[0])
        for r in caplog.records
        if not r.getMessage().startswith("storage balance: on disk")
    ]
    assert levels == [
        (logging.WARNING, "storage balance warning"),
        (logging.INFO, "storage balance"),  # back below
        (logging.WARNING, "storage balance warning"),
    ]
