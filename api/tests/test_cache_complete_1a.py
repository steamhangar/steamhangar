"""WP CACHE-COMPLETE-1a: cache completeness per game (schema v25).

Guarantees pinned here, by name:

1. **Like with like.** Expected bytes are the target manifest's
   ``cb_compressed`` sum; present bytes count only chunk files of exactly that
   size. Leftover chunks of an older build count for nothing, so a depot whose
   directory is LARGER than the new build can still be incomplete (the
   Timberborn report).
2. **The target is the current build.** The oracle's public gid, unless
   vault-api recorded a manifest after the oracle's last answer, or the oracle
   is off / silent for the depot.
3. **Unknown is null, never a guess.** No target, an unreadable target
   manifest, a target that changed since the measurement, no bytes on disk:
   the API says null.
4. **Run by the update check, read by the API.** The check stores rows; the
   games list only reads them; ``?incomplete=true`` lists the games below 100.
5. **Groups (D9)** aggregate the members' depots once each.
"""

from __future__ import annotations

import dataclasses
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from tests.test_gc import _cid, write_archive_bin, write_cache_manifest, write_chunks
from tests.test_oracle import enabled_settings, off_settings
from vault_api import app_classification, cache_completeness, depot_manifests, update_check
from vault_api.config import Settings
from vault_api.db import SCHEMA_VERSION, get_connection, init_db
from vault_api.main import create_app
from vault_api.mapping import upsert_mapping

AUTH = {"X-Api-Key": TEST_API_KEY}

APPID = 440
DEPOT = 441
OLD_GID = "1000"
NEW_GID = "2000"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def settings_for(tmp_path: Path, *, oracle_on: bool = True) -> Settings:
    base = enabled_settings(tmp_path) if oracle_on else off_settings(tmp_path)
    archive = tmp_path / "manifests"
    archive.mkdir(exist_ok=True)
    (tmp_path / "cache" / "depot").mkdir(parents=True, exist_ok=True)
    return dataclasses.replace(base, manifest_archive_dir=str(archive))


@pytest.fixture
def conn(tmp_path: Path):
    path = str(tmp_path / "vault.db")
    init_db(path)
    connection = get_connection(path)
    try:
        yield connection
    finally:
        connection.close()


def record(conn: sqlite3.Connection, appid: int, depotid: int, gid: str, at: str) -> None:
    upsert_mapping(conn, depotid, appid, f"Game {appid}")
    depot_manifests.upsert_depot_manifest(
        conn,
        appid=appid,
        containing_appid=None,
        depotid=depotid,
        manifestid=gid,
        chunk_count=1,
        total_bytes=1,
        recorded_at=at,
        source="test",
    )


def oracle_says(
    conn: sqlite3.Connection, appid: int, depotid: int, gid: str, checked_at: str
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO oracle_app_state (appid, buildid, checked_at, source, "
        "depot_count, branch_count) VALUES (?, '1', ?, 'test', 1, 1)",
        (appid, checked_at),
    )
    conn.execute(
        "INSERT OR REPLACE INTO oracle_branch_manifests (appid, depotid, branch, "
        "manifestid, recorded_at, source) VALUES (?, ?, 'public', ?, ?, 'test')",
        (appid, depotid, gid, checked_at),
    )
    conn.commit()


def chunks(start: int, count: int, size: int = 100) -> dict[str, int]:
    return {_cid(i): size for i in range(start, start + count)}


def measure(conn: sqlite3.Connection, settings: Settings, appid: int = APPID):
    cache_completeness.evaluate_apps(conn, settings, [appid])
    index = cache_completeness.load_index(conn, settings)
    depots = [d for (a, d) in index.targets if a == appid]
    return index.figure((appid, d) for d in depots)


# --------------------------------------------------------------------------
# 1. like with like
# --------------------------------------------------------------------------


def test_partial_depot_counts_only_exact_size_chunks_of_the_target(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = settings_for(tmp_path, oracle_on=False)
    cache = Path(settings.cache_root)
    manifest = chunks(0, 10)
    record(conn, APPID, DEPOT, OLD_GID, "2026-10-01T00:00:00Z")
    write_archive_bin(Path(settings.manifest_archive_dir), depotid=DEPOT, manifestid=OLD_GID,
                      chunks=manifest)
    present = dict(list(manifest.items())[:6])
    write_chunks(cache, DEPOT, present)
    # One listed chunk with the wrong size: damaged, counts as missing.
    write_chunks(cache, DEPOT, {_cid(6): 99})
    # A chunk the manifest does not list: never counted.
    write_chunks(cache, DEPOT, {_cid(500): 5000})

    figure = measure(conn, settings)

    assert figure.expected_bytes == 1000
    assert figure.present_bytes == 600
    assert figure.pct == 60
    row = conn.execute("SELECT * FROM cache_completeness").fetchone()
    assert (row["missing_chunks"], row["mismatched_chunks"], row["status"]) == (3, 1, "ok")


def test_pct_is_floored_so_only_a_full_cache_reads_100(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = settings_for(tmp_path, oracle_on=False)
    manifest = {**chunks(0, 1, size=999), **chunks(1, 1, size=1)}
    record(conn, APPID, DEPOT, OLD_GID, "2026-10-01T00:00:00Z")
    write_archive_bin(Path(settings.manifest_archive_dir), depotid=DEPOT, manifestid=OLD_GID,
                      chunks=manifest)
    write_chunks(Path(settings.cache_root), DEPOT, chunks(0, 1, size=999))

    assert measure(conn, settings).pct == 99

    write_chunks(Path(settings.cache_root), DEPOT, chunks(1, 1, size=1))
    assert measure(conn, settings).pct == 100


def test_client_update_in_progress_is_incomplete_although_the_depot_is_larger(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    """The Timberborn case: the old build is fully on disk, a Steam client
    started the update through the cache (its new manifest is stored under
    depot/<id>/manifest/), only part of the new chunks have arrived."""
    settings = settings_for(tmp_path)
    cache = Path(settings.cache_root)
    old = chunks(0, 10)
    new = {**chunks(0, 5), **chunks(100, 5)}  # half shared with the old build
    record(conn, APPID, DEPOT, OLD_GID, "2026-10-01T00:00:00Z")
    write_archive_bin(Path(settings.manifest_archive_dir), depotid=DEPOT, manifestid=OLD_GID,
                      chunks=old)
    write_chunks(cache, DEPOT, old)
    oracle_says(conn, APPID, DEPOT, NEW_GID, "2026-10-14T08:00:00Z")
    write_cache_manifest(cache, depotid=DEPOT, manifestid=NEW_GID, chunks=new)
    write_chunks(cache, DEPOT, chunks(100, 2))  # 2 of 5 new chunks so far

    figure = measure(conn, settings)

    assert figure.expected_bytes == 1000
    assert figure.present_bytes == 700
    assert figure.pct == 70
    row = conn.execute("SELECT manifestid, source FROM cache_completeness").fetchone()
    assert (row["manifestid"], row["source"]) == (NEW_GID, "cache_manifest")


# --------------------------------------------------------------------------
# 2. target selection / 3. unknown is null
# --------------------------------------------------------------------------


def test_announced_build_with_no_readable_manifest_is_unknown_not_complete(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = settings_for(tmp_path)
    old = chunks(0, 4)
    record(conn, APPID, DEPOT, OLD_GID, "2026-10-01T00:00:00Z")
    write_archive_bin(Path(settings.manifest_archive_dir), depotid=DEPOT, manifestid=OLD_GID,
                      chunks=old)
    write_chunks(Path(settings.cache_root), DEPOT, old)
    oracle_says(conn, APPID, DEPOT, NEW_GID, "2026-10-14T08:00:00Z")

    figure = measure(conn, settings)

    assert figure == cache_completeness.UNKNOWN
    status = conn.execute("SELECT status FROM cache_completeness").fetchone()["status"]
    assert status == cache_completeness.STATUS_MANIFEST_UNAVAILABLE


def test_a_record_newer_than_the_oracle_answer_wins(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = settings_for(tmp_path)
    oracle_says(conn, APPID, DEPOT, OLD_GID, "2026-10-01T00:00:00Z")
    record(conn, APPID, DEPOT, NEW_GID, "2026-10-14T09:00:00Z")

    targets = cache_completeness.current_targets(conn, settings)
    assert targets[(APPID, DEPOT)] == NEW_GID

    oracle_says(conn, APPID, DEPOT, "3000", "2026-10-14T10:00:00Z")
    assert cache_completeness.current_targets(conn, settings)[(APPID, DEPOT)] == "3000"


def test_oracle_off_measures_against_the_recorded_manifest(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = settings_for(tmp_path, oracle_on=False)
    record(conn, APPID, DEPOT, OLD_GID, "2026-10-01T00:00:00Z")
    oracle_says(conn, APPID, DEPOT, NEW_GID, "2026-10-14T08:00:00Z")

    assert cache_completeness.current_targets(conn, settings)[(APPID, DEPOT)] == OLD_GID


def test_no_record_and_no_oracle_row_is_no_target(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = settings_for(tmp_path)
    upsert_mapping(conn, DEPOT, APPID, "Game")
    write_chunks(Path(settings.cache_root), DEPOT, chunks(0, 2))

    assert measure(conn, settings) == cache_completeness.UNKNOWN
    status = conn.execute("SELECT status FROM cache_completeness").fetchone()["status"]
    assert status == cache_completeness.STATUS_NO_TARGET


def test_a_measurement_for_an_outdated_target_is_not_shown(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = settings_for(tmp_path)
    old = chunks(0, 4)
    record(conn, APPID, DEPOT, OLD_GID, "2026-10-01T00:00:00Z")
    write_archive_bin(Path(settings.manifest_archive_dir), depotid=DEPOT, manifestid=OLD_GID,
                      chunks=old)
    write_chunks(Path(settings.cache_root), DEPOT, old)
    assert measure(conn, settings).pct == 100

    # The oracle answers with a new build; nothing re-measured yet.
    oracle_says(conn, APPID, DEPOT, NEW_GID, "2026-10-14T08:00:00Z")
    index = cache_completeness.load_index(conn, settings)
    assert index.figure([(APPID, DEPOT)]) == cache_completeness.UNKNOWN


def test_one_unknown_depot_makes_the_app_unknown(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = settings_for(tmp_path, oracle_on=False)
    record(conn, APPID, DEPOT, OLD_GID, "2026-10-01T00:00:00Z")
    write_archive_bin(Path(settings.manifest_archive_dir), depotid=DEPOT, manifestid=OLD_GID,
                      chunks=chunks(0, 2))
    write_chunks(Path(settings.cache_root), DEPOT, chunks(0, 2))
    upsert_mapping(conn, 442, APPID, None)  # mapped, nothing recorded

    assert measure(conn, settings) == cache_completeness.UNKNOWN


def test_shared_depot_is_counted_once_in_a_group_figure() -> None:
    row = cache_completeness.DepotCompleteness(
        appid=1, depotid=9, manifestid="5", status="ok", checked_at="t",
        expected_bytes=100, present_bytes=50,
    )
    twin = cache_completeness.DepotCompleteness(
        appid=2, depotid=9, manifestid="5", status="ok", checked_at="t",
        expected_bytes=100, present_bytes=50,
    )
    other = cache_completeness.DepotCompleteness(
        appid=2, depotid=8, manifestid="6", status="ok", checked_at="s",
        expected_bytes=100, present_bytes=100,
    )
    index = cache_completeness.CompletenessIndex(
        rows={(1, 9): row, (2, 9): twin, (2, 8): other},
        targets={(1, 9): "5", (2, 9): "5", (2, 8): "6"},
    )
    figure = index.figure([(1, 9), (2, 9), (2, 8)])
    assert (figure.expected_bytes, figure.present_bytes, figure.pct) == (200, 150, 75)
    assert figure.checked_at == "s"
    assert index.figure([]) == cache_completeness.UNKNOWN


# --------------------------------------------------------------------------
# 4. update check stores, API reads
# --------------------------------------------------------------------------


def seed_two_games(conn: sqlite3.Connection, settings: Settings) -> None:
    """440: complete. 730: 1 of 4 chunks on disk."""
    cache = Path(settings.cache_root)
    archive = Path(settings.manifest_archive_dir)
    record(conn, 440, 441, OLD_GID, "2026-10-01T00:00:00Z")
    write_archive_bin(archive, depotid=441, manifestid=OLD_GID, chunks=chunks(0, 2))
    write_chunks(cache, 441, chunks(0, 2))
    record(conn, 730, 731, "1001", "2026-10-01T00:00:00Z")
    write_archive_bin(archive, depotid=731, manifestid="1001", chunks=chunks(10, 4))
    write_chunks(cache, 731, chunks(10, 1))


def test_the_update_check_run_stores_completeness(
    tmp_path: Path, conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = settings_for(tmp_path)
    seed_two_games(conn, settings)

    def fetch(url: str) -> bytes:
        raise update_check.oracle.OracleError("offline in this test")

    runner = update_check.UpdateCheckRunner(settings.db_path, fetch=fetch, pause_seconds=0)
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

    rows = {
        r["appid"]: (r["expected_bytes"], r["present_bytes"])
        for r in conn.execute("SELECT * FROM cache_completeness")
    }
    # The oracle failed for both, yet completeness still ran (local reads).
    assert rows == {440: (200, 200), 730: (400, 100)}


def test_a_failing_completeness_step_does_not_change_the_outcome(
    tmp_path: Path, conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = settings_for(tmp_path)
    seed_two_games(conn, settings)

    def boom(*args: Any, **kwargs: Any) -> int:
        raise RuntimeError("bug")

    monkeypatch.setattr(update_check.cache_completeness, "evaluate_selection", boom)

    def fetch(url: str) -> bytes:
        raise update_check.oracle.OracleError("offline")

    runner = update_check.UpdateCheckRunner(settings.db_path, fetch=fetch, pause_seconds=0)
    runner.start(
        conn, settings, trigger=update_check.TRIGGER_MANUAL,
        select_appids=lambda _c: [440], now=datetime.now(timezone.utc),
        require_interval=False,
    )
    assert runner.wait(10)
    status = update_check.read_status(conn, settings, running=False)
    assert status.last_outcome == update_check.OUTCOME_COMPLETED


def api_client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


def test_games_api_exposes_the_figures_and_the_incomplete_filter(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = settings_for(tmp_path)
    seed_two_games(conn, settings)
    upsert_mapping(conn, 999, 50, "Never measured")
    write_chunks(Path(settings.cache_root), 999, chunks(50, 1))
    cache_completeness.evaluate_apps(conn, settings, [440, 730])

    with api_client(settings) as client:
        games = {g["appid"]: g for g in client.get("/v1/games", headers=AUTH).json()}
        assert games[440]["cache_complete_pct"] == 100
        assert games[440]["cache_expected_bytes"] == 200
        assert games[440]["cache_present_bytes"] == 200
        assert games[440]["cache_complete_checked_at"]
        assert games[440]["group_cache_complete_pct"] == 100
        assert games[730]["cache_complete_pct"] == 25
        assert games[50]["cache_complete_pct"] is None
        assert games[50]["cache_expected_bytes"] is None

        incomplete = client.get("/v1/games?incomplete=true", headers=AUTH).json()
        assert [g["appid"] for g in incomplete] == [730]

        detail = client.get("/v1/games/730", headers=AUTH).json()
        assert detail["cache_complete_pct"] == 25
        assert detail["cache_expected_bytes"] == 400


def test_no_bytes_on_disk_hides_a_stored_figure(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = settings_for(tmp_path)
    seed_two_games(conn, settings)
    cache_completeness.evaluate_apps(conn, settings, [730])
    # The game was deleted from the cache; the stored row survives until the
    # next check.
    for path in (Path(settings.cache_root) / "depot" / "731" / "chunk").iterdir():
        path.unlink()

    with api_client(settings) as client:
        game = client.get("/v1/games/730", headers=AUTH).json()
    assert game["size_bytes"] is None
    assert game["cache_complete_pct"] is None
    assert game["group_cache_complete_pct"] is None


def test_group_figure_spans_the_members(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = settings_for(tmp_path)
    seed_two_games(conn, settings)
    # 730 is a DLC of 440 (store classification row).
    conn.execute(
        "INSERT INTO app_classification (appid, app_type, parent_appid, source, "
        "checked_at) VALUES (730, 'dlc', 440, ?, '2026-10-14T00:00:00Z')",
        (app_classification.SOURCE_STORE,),
    )
    conn.commit()
    cache_completeness.evaluate_apps(conn, settings, [440, 730])

    with api_client(settings) as client:
        games = {g["appid"]: g for g in client.get("/v1/games", headers=AUTH).json()}
    assert games[730]["parent_appid"] == 440
    assert games[440]["cache_complete_pct"] == 100
    # (200 + 100) / (200 + 400)
    assert games[440]["group_cache_complete_pct"] == 50
    assert games[730]["group_cache_complete_pct"] is None


# --------------------------------------------------------------------------
# Review round 1 (CACHE-COMPLETE-1a)
# --------------------------------------------------------------------------


def test_group_figure_skips_a_member_with_nothing_on_disk(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    """Parent + DLC both 100 %, the DLC's chunks are deleted, only the parent is
    re-checked: the DLC's stale 800/800 rows must not enter the group figure."""
    settings = settings_for(tmp_path)
    cache = Path(settings.cache_root)
    archive = Path(settings.manifest_archive_dir)
    record(conn, 440, 441, OLD_GID, "2026-10-01T00:00:00Z")
    write_archive_bin(archive, depotid=441, manifestid=OLD_GID, chunks=chunks(0, 2))
    write_chunks(cache, 441, chunks(0, 2))
    record(conn, 730, 731, "1001", "2026-10-01T00:00:00Z")
    write_archive_bin(archive, depotid=731, manifestid="1001", chunks=chunks(10, 8))
    write_chunks(cache, 731, chunks(10, 8))
    conn.execute(
        "INSERT INTO app_classification (appid, app_type, parent_appid, source, "
        "checked_at) VALUES (730, 'dlc', 440, ?, '2026-10-14T00:00:00Z')",
        (app_classification.SOURCE_STORE,),
    )
    conn.commit()
    cache_completeness.evaluate_apps(conn, settings, [440, 730])

    for path in (cache / "depot" / "731" / "chunk").iterdir():
        path.unlink()
    (cache / "depot" / "441" / "chunk" / _cid(1)).unlink()
    cache_completeness.evaluate_apps(conn, settings, [440])  # no prune here
    assert conn.execute(
        "SELECT present_bytes FROM cache_completeness WHERE appid = 730"
    ).fetchone()[0] == 800  # the stale row is still there

    with api_client(settings) as client:
        games = {g["appid"]: g for g in client.get("/v1/games", headers=AUTH).json()}
    assert games[440]["cache_complete_pct"] == 50
    # Only the parent: 100/200, not (100 + 800) / (200 + 800).
    assert games[440]["group_cache_complete_pct"] == 50


def test_a_completed_run_drops_rows_of_apps_outside_the_selection(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = settings_for(tmp_path)
    seed_two_games(conn, settings)
    cache_completeness.evaluate_selection(conn, settings, [440, 730])

    outcome = cache_completeness.evaluate_selection(conn, settings, [440])

    assert outcome == cache_completeness.RunOutcome(stored=1, aborted=False)
    apps = {r[0] for r in conn.execute("SELECT appid FROM cache_completeness")}
    assert apps == {440}


def test_an_aborted_run_stores_no_partial_app_and_prunes_nothing(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = settings_for(tmp_path)
    seed_two_games(conn, settings)
    upsert_mapping(conn, 732, 730, None)  # 730 now has two depots
    cache_completeness.evaluate_selection(conn, settings, [440, 730])
    before = conn.execute("SELECT * FROM cache_completeness ORDER BY appid, depotid").fetchall()

    calls = {"n": 0}

    def abort_after_first_depot() -> bool:
        calls["n"] += 1
        return calls["n"] > 2  # app check + first depot check pass, then stop

    outcome = cache_completeness.evaluate_selection(
        conn, settings, [730], should_abort=abort_after_first_depot
    )

    assert outcome.aborted
    after = conn.execute("SELECT * FROM cache_completeness ORDER BY appid, depotid").fetchall()
    assert [tuple(r) for r in after] == [tuple(r) for r in before]


def test_delete_cache_drops_the_rows_of_the_app_and_its_depots(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = settings_for(tmp_path)
    seed_two_games(conn, settings)
    cache_completeness.evaluate_selection(conn, settings, [440, 730])

    with api_client(settings) as client:
        response = client.delete("/v1/cache/730", headers=AUTH)
    assert response.status_code == 200, response.text

    apps = {r[0] for r in conn.execute("SELECT appid FROM cache_completeness")}
    assert apps == {440}


def _runner_result(**kwargs: Any) -> update_check.UpdateCheckResult:
    return update_check.UpdateCheckResult(enabled=True, ran=True, **kwargs)


def test_evaluate_completeness_is_skipped_on_abort_and_on_shutdown(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = settings_for(tmp_path)
    seed_two_games(conn, settings)
    runner = update_check.UpdateCheckRunner(settings.db_path, pause_seconds=0)

    assert runner._evaluate_completeness(
        conn, settings, [440, 730], _runner_result(aborted=True)
    ) == 0
    runner.stop()
    assert runner._evaluate_completeness(conn, settings, [440, 730], _runner_result()) == 0
    assert conn.execute("SELECT COUNT(*) FROM cache_completeness").fetchone()[0] == 0

    live = update_check.UpdateCheckRunner(settings.db_path, pause_seconds=0)
    assert live._evaluate_completeness(conn, settings, [440, 730], _runner_result()) == 2


def test_a_shared_depot_is_scanned_once_per_run_and_others_are_released(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = settings_for(tmp_path)
    seed_two_games(conn, settings)
    record(conn, 900, 441, OLD_GID, "2026-10-01T00:00:00Z")  # 441 shared by 440/900

    run = cache_completeness.EvaluationRun(settings, shared_depots={441})
    for appid in (440, 900, 730):
        rows = cache_completeness.evaluate_app(conn, settings, appid, run=run)
        assert rows and all(r.status == "ok" for r in rows)

    assert run.chunk_scans == 2  # 441 once, 731 once
    assert set(run._chunks) == {441}  # 731 was released after use


def test_a_manifest_past_int64_is_unavailable_not_a_crash(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = settings_for(tmp_path, oracle_on=False)
    record(conn, APPID, DEPOT, OLD_GID, "2026-10-01T00:00:00Z")
    write_archive_bin(Path(settings.manifest_archive_dir), depotid=DEPOT, manifestid=OLD_GID,
                      chunks={_cid(0): 2**62, _cid(1): 2**62})
    write_chunks(Path(settings.cache_root), DEPOT, chunks(5, 1))

    outcome = cache_completeness.evaluate_apps(conn, settings, [APPID])

    assert outcome.stored == 1
    status = conn.execute("SELECT status FROM cache_completeness").fetchone()[0]
    assert status == cache_completeness.STATUS_MANIFEST_UNAVAILABLE


def test_the_worker_remeasures_an_app_after_its_prefill(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    from vault_api.worker import PrefillWorker

    settings = settings_for(tmp_path)
    seed_two_games(conn, settings)
    PrefillWorker(settings)._remeasure_completeness(conn, 730)
    rows = conn.execute("SELECT appid, present_bytes FROM cache_completeness").fetchall()
    assert [tuple(r) for r in rows] == [(730, 100)]

    off = settings_for(tmp_path, oracle_on=False)
    PrefillWorker(off)._remeasure_completeness(conn, 440)
    assert conn.execute(
        "SELECT COUNT(*) FROM cache_completeness WHERE appid = 440"
    ).fetchone()[0] == 0


# --------------------------------------------------------------------------
# Review round 2: wiring
# --------------------------------------------------------------------------


def test_a_successful_prefill_job_through_the_worker_stores_a_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """worker.py's post-success hook, end to end: enqueue -> stub SteamPrefill
    writes one chunk and drops a manifest .bin -> ingestion records it -> the
    hook measures the app. The update check is switched off for this test, so
    the row can only come from the hook."""
    from tests import stub_prefill
    from tests.test_manifests import _bin_manifest_bytes
    from tests.test_worker import enqueue, make_settings, wait_for_job
    from vault_api import oracle
    from vault_api.config import MANIFEST_ORACLE_STEAMCMD_API

    monkeypatch.setattr(
        update_check.UpdateCheckRunner, "start",
        lambda self, *a, **k: update_check.DISABLED,
    )

    def no_fetch(url: str, *args: Any, **kwargs: Any) -> bytes:
        raise AssertionError(f"network fetch attempted: {url}")

    monkeypatch.setattr(oracle, "http_fetch", no_fetch)

    cache_root = tmp_path / "cache"
    temp_cache = tmp_path / "steamprefill-cache"
    stub_chunk = "%040x" % (441 * 1000)  # what the stub writes first, 160 bytes
    manifest = _bin_manifest_bytes(
        depot_id=441, manifest_id=555,
        files=[[(stub_chunk, 160), (_cid(7), 160)]],
    )
    executable = stub_prefill.make_stub(
        tmp_path / "bin",
        cache_root=str(cache_root),
        depots_by_app={440: [441]},
        manifest_bins=[{
            "dir": str(temp_cache), "filename": "440_440_441_555.bin", "data": manifest,
        }],
    )
    settings = dataclasses.replace(
        make_settings(
            tmp_path, cache_root, executable,
            steamprefill_cache_dir=str(temp_cache),
        ),
        manifest_oracle=MANIFEST_ORACLE_STEAMCMD_API,
    )

    with TestClient(create_app(settings)) as client:
        (job_id,) = enqueue(client, 440)
        assert wait_for_job(client, job_id)["status"] == "done"
        game = client.get("/v1/games/440", headers=AUTH).json()

    conn = get_connection(settings.db_path)
    try:
        rows = conn.execute(
            "SELECT appid, depotid, manifestid, expected_bytes, present_bytes "
            "FROM cache_completeness"
        ).fetchall()
    finally:
        conn.close()
    assert [tuple(r) for r in rows] == [(440, 441, "555", 320, 160)]
    assert game["cache_complete_pct"] == 50


def test_delete_drops_a_second_apps_row_for_the_removed_shared_depot(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    """The depot arm of ``forget``: app 900 maps 730's depot 731 but has no
    cache content of its own, so 731 goes as a last cached remnant -- and 900's
    (900, 731) row must go with it, although 900 itself was not deleted."""
    settings = settings_for(tmp_path)
    seed_two_games(conn, settings)
    record(conn, 900, 731, "1001", "2026-10-01T00:00:00Z")
    cache_completeness.evaluate_apps(conn, settings, [440, 730, 900])
    assert conn.execute(
        "SELECT COUNT(*) FROM cache_completeness WHERE appid = 900"
    ).fetchone()[0] == 1

    with api_client(settings) as client:
        response = client.delete("/v1/cache/730", headers=AUTH)
    assert response.status_code == 200, response.text
    assert [d["depotid"] for d in response.json()["deleted_depots"]] == [731]

    left = {tuple(r) for r in conn.execute("SELECT appid, depotid FROM cache_completeness")}
    assert left == {(440, 441)}


def test_a_measurement_of_a_depot_deleted_meanwhile_is_not_stored(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    import shutil

    settings = settings_for(tmp_path)
    seed_two_games(conn, settings)
    run = cache_completeness.EvaluationRun(settings)
    rows = cache_completeness.evaluate_app(conn, settings, 730, run=run)
    assert rows and rows[0].present_bytes == 100

    # DELETE /v1/cache/730 removes the directory between measure and store.
    shutil.rmtree(Path(settings.cache_root) / "depot" / "731")

    assert not cache_completeness.store_app(
        conn, 730, rows, depot_exists=run.depot_exists
    )
    assert conn.execute("SELECT COUNT(*) FROM cache_completeness").fetchone()[0] == 0
    # Without the check it would have been stored.
    assert cache_completeness.store_app(conn, 730, rows)


# --------------------------------------------------------------------------
# Schema v25
# --------------------------------------------------------------------------


def test_a_v23_database_gains_the_table(tmp_path: Path) -> None:
    path = str(tmp_path / "old.db")
    init_db(path)
    raw = get_connection(path)
    raw.execute("DROP TABLE cache_completeness")
    raw.execute("UPDATE schema_version SET version = 23")
    raw.commit()
    raw.close()

    init_db(path)

    conn = get_connection(path)
    try:
        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
        names = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()
    assert version == SCHEMA_VERSION >= 25
    assert "cache_completeness" in names


def test_a_v25_database_without_the_v24_column_still_gains_it(tmp_path: Path) -> None:
    """Merge order (AGENT-ADDR-1 v24 vs this v25): a file this branch stamped
    25 before v24 existed must still get ``agent_reports.local_addrs``.

    The pre-v24 table is hand-written (AGENT-ADDR-1's helper), not today's
    table with ``ALTER TABLE ... DROP COLUMN local_addrs``: DROP COLUMN
    rewrites the stored CREATE TABLE text, and older SQLite fails on the
    ``--`` comment block in front of that last column ("error in table
    agent_reports after drop column: incomplete input"). Measured
    (CI-FLAKE-1): the CI runner's system SQLite 3.45.1 and 3.40.1 fail, the
    3.53.1 bundled with uv's Python passes -- so it was green locally only.
    """
    from tests.test_agent_addr_1 import _stamp_without_local_addrs

    path = str(tmp_path / "v25-first.db")
    _stamp_without_local_addrs(path, 25)

    init_db(path)

    conn = get_connection(path)
    try:
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(agent_reports)")}
    finally:
        conn.close()
    assert "local_addrs" in columns
