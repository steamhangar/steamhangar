"""WP UPD-FIX-1: "Update now" must clear "Update available".

Prod root cause (2026-10-14, Tavern Keeper, app 436780 / depot 436781):
SteamPrefill's temp cache held four ``.bin`` files for the depot, and
ingestion upserted every one of them in unsorted ``os.scandir`` order -- the
last file won, so an old manifest (5788...) was recorded although the newest
file (3589..., the oracle's public gid) was the current build.

Guarantees pinned here, by name:

1. **One manifest per depot, the newest file** (``select_current_manifest``),
   whatever order the directory lists them in; an mtime tie is decided by
   manifest id, never by listing order.
2. **No fallback to a leftover**: an unreadable newest file records nothing.
3. **One-time startup repair** re-derives old rows with the same rule, only
   from files that existed when the row was recorded, once per database.
4. **A partial update never hides a later update** of an untouched depot
   (review B1: the stale comparison is NOT limited to ``depot_app_map``,
   because ``apply_observed_mapping`` drops untouched depots from the map).
5. **"Update now" clears the flag end to end**, re-asks the oracle when the
   app still reads stale, and writes an honest job note when Steam and the
   oracle disagree.
6. **Cache completeness targets the right build** after such a run.
"""

from __future__ import annotations

import dataclasses
import itertools
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests import stub_prefill
from tests.conftest import TEST_API_KEY
from tests.test_manifests import _bin_manifest_bytes, _chunk_id
from tests.test_oracle import app_info, enabled_settings, one_depot
from vault_api import cache_completeness, manifest_ingest, oracle, worker
from vault_api.config import MANIFEST_ORACLE_OFF
from vault_api.db import get_connection, init_db
from vault_api.depot_manifests import get_depot_manifest, upsert_depot_manifest
from vault_api.main import create_app
from vault_api.manifest_ingest import (
    REPAIR_STEP_NAME,
    BinCandidate,
    ingest_after_prefill,
    repair_recorded_manifests,
    select_current_manifest,
)
from vault_api.mapping import upsert_mapping

AUTH = {"X-Api-Key": TEST_API_KEY}

APPID = 436780
DEPOT = 436781
#: The four files measured on prod, oldest first (mtime order), with the
#: newest one being the oracle's public gid.
OLDEST = 8230470978449025486  # Oct 4
OLD = 5788091679023567485  # Oct 5 -- what the buggy ingestion recorded
NEWER = 8766284553724588703  # Oct 12
NEWEST = 3589942376827713992  # Oct 14, oracle public gid
def _epoch(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())


TAVERN_KEEPER = [
    (OLDEST, _epoch("2026-10-04T10:00:00Z")),
    (OLD, _epoch("2026-10-05T10:00:00Z")),
    (NEWER, _epoch("2026-10-12T10:00:00Z")),
    (NEWEST, _epoch("2026-10-14T07:36:00Z")),
]

UP_TO_DATE_TABLE = (
    "  Prefilled 1 apps totaling 0 b in 02.9012 \n"
    "   Updated | Up To Date\n"
    "  ---------+------------\n"
    "      0    |     1\n"
)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """The lifespan starts the always-on update check, which asks the oracle
    through ``oracle.http_fetch``. Refuse it, so these tests never leave the
    host and the only oracle answers are the ones a test injects."""
    import socket

    def no_fetch(url: str, *args: Any, **kwargs: Any) -> bytes:
        raise oracle.OracleError(f"network fetch attempted in a test: {url}")

    def no_connect(self: socket.socket, address: Any) -> None:
        raise AssertionError(f"socket connect attempted in a test: {address!r}")

    monkeypatch.setattr(oracle, "http_fetch", no_fetch)
    monkeypatch.setattr(socket.socket, "connect", no_connect)


def _name(manifestid: int, *, appid: int = APPID, depot: int = DEPOT) -> str:
    return f"{appid}_{appid}_{depot}_{manifestid}.bin"


def _write_bin(
    directory: Path, manifestid: int, mtime: int, *, appid: int = APPID, depot: int = DEPOT
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / _name(manifestid, appid=appid, depot=depot)
    path.write_bytes(
        _bin_manifest_bytes(
            depot_id=depot,
            manifest_id=manifestid,
            files=[[(_chunk_id(manifestid % 1000 + 1), 1000)]],
        )
    )
    os.utime(path, ns=(mtime * 1_000_000_000, mtime * 1_000_000_000))
    return path


def _tavern_keeper(directory: Path) -> None:
    for manifestid, mtime in TAVERN_KEEPER:
        _write_bin(directory, manifestid, mtime)


def _settings(tmp_path: Path, **overrides: Any):
    values: dict[str, Any] = dict(
        steamprefill_cache_dir=str(tmp_path / "steamprefill-cache"),
        manifest_archive_dir=str(tmp_path / "manifest-archive"),
        worker_poll_seconds=0.02,
    )
    values.update(overrides)
    return enabled_settings(tmp_path, **values)


def _conn(settings) -> sqlite3.Connection:
    init_db(settings.db_path)
    return get_connection(settings.db_path)


def _record(conn: sqlite3.Connection, manifestid: int, recorded_at: str, *, depot: int = DEPOT) -> None:
    upsert_depot_manifest(
        conn,
        appid=APPID,
        containing_appid=APPID,
        depotid=depot,
        manifestid=str(manifestid),
        chunk_count=1,
        total_bytes=1000,
        recorded_at=recorded_at,
        source="steamprefill_bin",
    )


def _store_oracle(conn: sqlite3.Connection, gid: int, checked_at: str) -> None:
    payload = app_info(
        APPID,
        depots=one_depot({"public": {"gid": str(gid)}}, depotid=str(DEPOT)),
        branches={"public": {"buildid": "100"}},
    )
    oracle.store_app_info(conn, oracle.parse_app_info(APPID, payload), checked_at=checked_at)


# --------------------------------------------------------------------------
# 1 + 2. Ingestion picks the newest file per depot
# --------------------------------------------------------------------------


@pytest.mark.parametrize("order", list(itertools.permutations(range(4))))
def test_tavern_keeper_shape_records_the_newest_file_in_every_listing_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, order: tuple[int, ...]
) -> None:
    settings = _settings(tmp_path)
    cache_dir = Path(settings.steamprefill_cache_dir)
    _tavern_keeper(cache_dir)

    real_scandir = os.scandir

    def scandir_in_order(path):
        if os.fspath(path) != str(cache_dir):
            return real_scandir(path)
        entries = sorted(real_scandir(path), key=lambda entry: entry.name)
        return iter([entries[index] for index in order])

    monkeypatch.setattr(manifest_ingest.os, "scandir", scandir_in_order)

    conn = _conn(settings)
    try:
        result = ingest_after_prefill(conn, appid=APPID, settings=settings)
        row = get_depot_manifest(conn, appid=APPID, depotid=DEPOT)
    finally:
        conn.close()

    assert row is not None and row["manifestid"] == str(NEWEST)
    assert [entry.manifestid for entry in result.ingested] == [str(NEWEST)]
    assert sorted(result.superseded) == sorted(_name(m) for m in (OLDEST, OLD, NEWER))
    assert "ignored 3 older manifest file(s)" in result.summary()
    # Only the chosen manifest is archived; leftovers never enter the archive.
    archive = sorted(p.name for p in Path(settings.manifest_archive_dir).iterdir())
    assert archive == [f"{DEPOT}_{NEWEST}.bin"]


def test_an_mtime_tie_goes_to_the_larger_manifest_id_in_both_orders() -> None:
    small = BinCandidate("a", "a", 1, 1, 2, 100, 5)
    large = BinCandidate("b", "b", 1, 1, 2, 200, 5)
    assert select_current_manifest([small, large]) is large
    assert select_current_manifest([large, small]) is large


def test_not_after_excludes_files_written_later() -> None:
    early = BinCandidate("a", "a", 1, 1, 2, 100, 10)
    late = BinCandidate("b", "b", 1, 1, 2, 200, 20)
    assert select_current_manifest([early, late], not_after_ns=15) is early
    assert select_current_manifest([late], not_after_ns=15) is None


def test_each_depot_gets_its_own_newest_file(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    cache_dir = Path(settings.steamprefill_cache_dir)
    _write_bin(cache_dir, 11, 100, depot=1001)
    _write_bin(cache_dir, 12, 300, depot=1001)
    _write_bin(cache_dir, 22, 100, depot=1002)
    _write_bin(cache_dir, 21, 300, depot=1002)

    conn = _conn(settings)
    try:
        ingest_after_prefill(conn, appid=APPID, settings=settings)
        first = get_depot_manifest(conn, appid=APPID, depotid=1001)
        second = get_depot_manifest(conn, appid=APPID, depotid=1002)
    finally:
        conn.close()
    assert first is not None and first["manifestid"] == "12"
    assert second is not None and second["manifestid"] == "21"


def test_a_corrupt_newest_file_records_nothing_instead_of_a_leftover(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    cache_dir = Path(settings.steamprefill_cache_dir)
    _write_bin(cache_dir, OLD, 100)
    newest = _write_bin(cache_dir, NEWEST, 200)
    newest.write_bytes(b"not a manifest")
    os.utime(newest, ns=(200 * 10**9, 200 * 10**9))

    conn = _conn(settings)
    try:
        result = ingest_after_prefill(conn, appid=APPID, settings=settings)
        row = get_depot_manifest(conn, appid=APPID, depotid=DEPOT)
    finally:
        conn.close()
    assert row is None
    assert result.parse_failures == [_name(NEWEST)]
    assert result.ingested == []


# --------------------------------------------------------------------------
# 3. One-time startup repair
# --------------------------------------------------------------------------


def test_repair_corrects_the_prod_row_and_runs_once(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    cache_dir = Path(settings.steamprefill_cache_dir)
    _tavern_keeper(cache_dir)
    conn = _conn(settings)
    try:
        _record(conn, OLD, "2026-10-14T19:39:10Z")
        result = repair_recorded_manifests(conn, settings)
        row = get_depot_manifest(conn, appid=APPID, depotid=DEPOT)

        assert result.repaired == ((APPID, DEPOT, str(OLD), str(NEWEST)),)
        assert row is not None
        assert row["manifestid"] == str(NEWEST)
        # A correction, not an observation.
        assert row["recorded_at"] == "2026-10-14T19:39:10Z"
        assert row["observation_count"] == 1
        assert (Path(settings.manifest_archive_dir) / f"{DEPOT}_{NEWEST}.bin").exists()

        # Stamped: a second start does nothing, even if the row is wrong again.
        _record(conn, OLD, "2026-10-14T19:39:10Z")
        again = repair_recorded_manifests(conn, settings)
        assert again.already_done is True
        assert get_depot_manifest(conn, appid=APPID, depotid=DEPOT)["manifestid"] == str(OLD)
    finally:
        conn.close()


def test_repair_never_promotes_a_file_written_after_the_row(tmp_path: Path) -> None:
    """A file newer than the recorded row came from a run that never reached
    ingestion (it failed), so its manifest was never confirmed."""
    settings = _settings(tmp_path)
    cache_dir = Path(settings.steamprefill_cache_dir)
    _write_bin(cache_dir, OLD, _epoch("2026-10-09T00:00:00Z"))
    _write_bin(cache_dir, NEWEST, _epoch("2026-10-10T00:00:01Z"))  # one second after the row
    conn = _conn(settings)
    try:
        _record(conn, OLD, "2026-10-10T00:00:00Z")
        result = repair_recorded_manifests(conn, settings)
        row = get_depot_manifest(conn, appid=APPID, depotid=DEPOT)
    finally:
        conn.close()
    assert result.repaired == ()
    assert row is not None and row["manifestid"] == str(OLD)


def test_repair_retries_when_the_cache_dir_is_missing(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    conn = _conn(settings)
    try:
        _record(conn, OLD, "2026-10-14T19:39:10Z")
        result = repair_recorded_manifests(conn, settings)
        stamped = conn.execute(
            "SELECT 1 FROM maintenance_steps WHERE name = ?", (REPAIR_STEP_NAME,)
        ).fetchone()
    finally:
        conn.close()
    assert result.cache_dir_unavailable is True
    assert stamped is None


def test_startup_runs_the_repair(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _tavern_keeper(Path(settings.steamprefill_cache_dir))
    conn = _conn(settings)
    try:
        _record(conn, OLD, "2026-10-14T19:39:10Z")
    finally:
        conn.close()

    with TestClient(create_app(settings)):
        pass

    conn = get_connection(settings.db_path)
    try:
        row = get_depot_manifest(conn, appid=APPID, depotid=DEPOT)
    finally:
        conn.close()
    assert row is not None and row["manifestid"] == str(NEWEST)


# --------------------------------------------------------------------------
# 4. A partial update never hides a later update (review B1)
# --------------------------------------------------------------------------


def test_a_later_update_of_a_depot_the_last_run_did_not_touch_still_flags(
    tmp_path: Path,
) -> None:
    """Reviewer probe: two mapped depots; a run writes only depot A, so
    ``apply_observed_mapping`` drops B from the map. When Steam later updates
    B, the game must still read stale."""
    from vault_api.prefill import apply_observed_mapping

    settings = _settings(tmp_path)
    depot_a, depot_b = DEPOT, DEPOT + 1
    conn = _conn(settings)
    try:
        upsert_mapping(conn, depot_a, APPID, None)
        upsert_mapping(conn, depot_b, APPID, None)
        _record(conn, NEWEST, "2026-10-14T19:39:10Z", depot=depot_a)
        _record(conn, OLD, "2026-10-14T19:39:10Z", depot=depot_b)
        apply_observed_mapping(conn, APPID, {depot_a})
        assert [
            row["depotid"]
            for row in conn.execute("SELECT depotid FROM depot_app_map WHERE appid = ?", (APPID,))
        ] == [depot_a]

        depots = {
            str(depot_a): {"name": "a", "manifests": {"public": {"gid": str(NEWEST)}}},
            str(depot_b): {"name": "b", "manifests": {"public": {"gid": str(NEWER)}}},
        }
        payload = app_info(APPID, depots=depots, branches={"public": {"buildid": "100"}})
        oracle.store_app_info(
            conn, oracle.parse_app_info(APPID, payload), checked_at="2026-10-15T08:00:00Z"
        )

        assert oracle.app_view(conn, APPID, settings=settings).verdict == oracle.VERDICT_STALE
        assert oracle.verdicts_by_app(conn, settings=settings)[APPID][0] == oracle.VERDICT_STALE
    finally:
        conn.close()


# --------------------------------------------------------------------------
# 5. "Update now" end to end
# --------------------------------------------------------------------------


class RecordingFetch:
    def __init__(self, gid: int | None) -> None:
        self.gid = gid
        self.urls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        if self.gid is None:
            raise oracle.OracleError("oracle unreachable (test)")
        return app_info(
            APPID,
            depots=one_depot({"public": {"gid": str(self.gid)}}, depotid=str(DEPOT)),
            branches={"public": {"buildid": "101"}},
        )


def _seed_flagged_game(settings, *, oracle_gid: int) -> None:
    """Prod state before "Update now": mapped depot with bytes on disk, the
    wrong (old) gid recorded, an oracle snapshot naming ``oracle_gid``."""
    chunk = Path(settings.cache_root) / "depot" / str(DEPOT) / "chunk" / ("a" * 40)
    chunk.parent.mkdir(parents=True, exist_ok=True)
    chunk.write_bytes(b"x" * 64)
    conn = _conn(settings)
    try:
        upsert_mapping(conn, DEPOT, APPID, "Tavern Keeper")
        _record(conn, OLD, "2026-10-14T19:39:10Z")
        _store_oracle(conn, oracle_gid, "2026-10-14T19:00:00Z")
        # Keep the startup repair out of these tests: they exercise the job.
        conn.execute(
            "INSERT INTO maintenance_steps (name, done_at) VALUES (?, ?)",
            (REPAIR_STEP_NAME, "2026-10-14T00:00:00Z"),
        )
        conn.commit()
    finally:
        conn.close()


def _update_available(client: TestClient) -> bool:
    games = client.get("/v1/games", headers=AUTH).json()
    return next(game for game in games if game["appid"] == APPID)["update_available"]


def _run_update_now(client: TestClient) -> dict:
    response = client.post("/v1/prefill", json={"appids": [APPID]}, headers=AUTH)
    assert response.status_code == 202, response.text
    job_id = response.json()[0]["job_id"]
    from tests.test_worker import wait_for_job

    return wait_for_job(client, job_id)


def _flagged_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, oracle_gid: int,
                 fetch: RecordingFetch):
    settings = _settings(tmp_path)
    _tavern_keeper(Path(settings.steamprefill_cache_dir))
    _seed_flagged_game(settings, oracle_gid=oracle_gid)
    executable = stub_prefill.make_stub(
        tmp_path / "bin", cache_root=settings.cache_root, summary_text=UP_TO_DATE_TABLE
    )
    settings = _settings(tmp_path, steamprefill_path=executable)
    monkeypatch.setattr(worker, "_default_oracle_fetch", lambda _settings: fetch)
    return settings


def test_update_now_clears_the_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The prod case: SteamPrefill says Up To Date, the newest .bin IS the
    oracle's gid. The flag clears from the run alone -- no oracle request."""
    fetch = RecordingFetch(NEWEST)
    settings = _flagged_app(tmp_path, monkeypatch, oracle_gid=NEWEST, fetch=fetch)

    with TestClient(create_app(settings)) as client:
        assert _update_available(client) is True
        job = _run_update_now(client)
        assert job["status"] == "done"
        assert _update_available(client) is False

    assert fetch.urls == []
    assert "oracle and Steam disagree" not in job["log_excerpt"]


def test_update_now_re_asks_the_oracle_when_its_snapshot_lags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stored snapshot predates the newest build; one re-ask settles it
    right away instead of at the next scheduled check."""
    fetch = RecordingFetch(NEWEST)
    settings = _flagged_app(tmp_path, monkeypatch, oracle_gid=NEWER, fetch=fetch)

    with TestClient(create_app(settings)) as client:
        assert _update_available(client) is True
        job = _run_update_now(client)
        assert _update_available(client) is False

    assert len(fetch.urls) == 1 and str(APPID) in fetch.urls[0]
    assert "the recorded build is current now" in job["log_excerpt"]


@pytest.mark.parametrize("fetch_gid", [NEWER, None], ids=["oracle-disagrees", "oracle-down"])
def test_steam_up_to_date_but_oracle_stale_is_said_in_the_job_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fetch_gid: int | None
) -> None:
    fetch = RecordingFetch(fetch_gid)
    settings = _flagged_app(tmp_path, monkeypatch, oracle_gid=NEWER, fetch=fetch)

    with TestClient(create_app(settings)) as client:
        job = _run_update_now(client)
        assert job["status"] == "done"
        assert _update_available(client) is True

    log = job["log_excerpt"]
    assert "Steam reports this build as current - oracle and Steam disagree" in log
    assert f"depot {DEPOT}: recorded {NEWEST}, oracle {NEWER}" in log
    if fetch_gid is None:
        assert "re-check failed" in log


def test_no_oracle_request_when_the_oracle_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetch = RecordingFetch(NEWEST)
    settings = _flagged_app(tmp_path, monkeypatch, oracle_gid=NEWER, fetch=fetch)
    off = dataclasses.replace(settings, manifest_oracle=MANIFEST_ORACLE_OFF)

    with TestClient(create_app(off)) as client:
        job = _run_update_now(client)
    assert job["status"] == "done"
    assert fetch.urls == []
    assert "Update check:" not in job["log_excerpt"]


# --------------------------------------------------------------------------
# 6. Cache completeness targets the right build (CACHE-COMPLETE-1a interplay)
# --------------------------------------------------------------------------


def test_completeness_target_is_the_newest_manifest_after_ingestion(tmp_path: Path) -> None:
    """``current_targets`` prefers the recorded gid when it is newer than the
    oracle snapshot -- so the recorded gid must be the right one. With the old
    last-file-wins ingestion it could be a leftover; now it is the newest."""
    settings = _settings(tmp_path)
    _tavern_keeper(Path(settings.steamprefill_cache_dir))
    conn = _conn(settings)
    try:
        upsert_mapping(conn, DEPOT, APPID, None)
        _store_oracle(conn, NEWEST, "2000-01-01T00:00:00Z")  # older than any ingest
        ingest_after_prefill(conn, appid=APPID, settings=settings)
        targets = cache_completeness.current_targets(conn, settings, [APPID])
    finally:
        conn.close()
    assert targets == {(APPID, DEPOT): str(NEWEST)}


# --------------------------------------------------------------------------
# 7. Review round 1: boundaries and the never-fail promises
# --------------------------------------------------------------------------


def test_repair_counts_a_file_written_in_the_same_second_as_the_row(tmp_path: Path) -> None:
    """``recorded_at`` has second precision; a file written later within that
    second existed when the row was recorded (N2: the not_after widening)."""
    settings = _settings(tmp_path)
    cache_dir = Path(settings.steamprefill_cache_dir)
    _write_bin(cache_dir, OLD, _epoch("2026-10-09T00:00:00Z"))
    same_second = _write_bin(cache_dir, NEWEST, _epoch("2026-10-10T00:00:00Z"))
    late_ns = _epoch("2026-10-10T00:00:00Z") * 1_000_000_000 + 999_000_000
    os.utime(same_second, ns=(late_ns, late_ns))
    conn = _conn(settings)
    try:
        _record(conn, OLD, "2026-10-10T00:00:00Z")
        result = repair_recorded_manifests(conn, settings)
    finally:
        conn.close()
    assert result.repaired == ((APPID, DEPOT, str(OLD), str(NEWEST)),)


def test_a_crashing_repair_never_stops_the_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vault_api import main

    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("repair bug (test)")

    monkeypatch.setattr(main, "repair_recorded_manifests", boom)
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        assert client.get("/v1/games", headers=AUTH).status_code == 200


def test_a_crashing_recheck_never_fails_the_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    fetch = RecordingFetch(NEWEST)
    settings = _flagged_app(tmp_path, monkeypatch, oracle_gid=NEWER, fetch=fetch)
    calls: list[int] = []

    def boom(_conn: Any, appid: int, **_kwargs: Any) -> None:
        calls.append(appid)
        raise RuntimeError("app_view bug (test)")

    monkeypatch.setattr(worker.oracle, "app_view", boom)
    with TestClient(create_app(settings)) as client:
        job = _run_update_now(client)
    assert APPID in calls
    assert any(
        record.name == "vault_api.worker" and "re-evaluating app" in record.getMessage()
        for record in caplog.records
    )
    assert job["status"] == "done"
    assert fetch.urls == []


def test_update_check_wait_never_sees_an_unstarted_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review S1: ``UpdateCheckRunner.start`` published its thread before
    starting it, so a shutdown ``wait`` in between raised "cannot join thread
    before it is started". Hold ``Thread.start`` open and call ``wait`` then."""
    import threading

    from vault_api import update_check

    settings = _settings(tmp_path)
    conn = _conn(settings)
    starting = threading.Event()
    release = threading.Event()
    real_thread = threading.Thread

    class SlowStartThread(real_thread):
        def start(self) -> None:
            starting.set()
            release.wait(5)
            super().start()

    monkeypatch.setattr(update_check.threading, "Thread", SlowStartThread)
    runner = update_check.UpdateCheckRunner(
        settings.db_path, fetch=RecordingFetch(NEWEST), pause_seconds=0
    )
    errors: list[BaseException] = []

    def start_run() -> None:
        own = get_connection(settings.db_path)
        try:
            runner.start(
                own, settings, trigger=update_check.TRIGGER_MANUAL,
                select_appids=lambda _c: [], now=datetime.now(timezone.utc),
                require_interval=False,
            )
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)
        finally:
            own.close()

    def wait_run() -> None:
        try:
            runner.wait(5)
        except BaseException as exc:
            errors.append(exc)

    starter = real_thread(target=start_run)
    waiter = real_thread(target=wait_run)
    try:
        starter.start()
        assert starting.wait(5)
        waiter.start()
        waiter.join(0.2)
    finally:
        release.set()
        starter.join(5)
        waiter.join(5)
        runner.stop(5)
        conn.close()
    assert errors == []
