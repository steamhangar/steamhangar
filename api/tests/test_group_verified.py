"""WP GROUP-VERIFIED-1 (rc21): HELLDIVERS 2 stayed "Cached · not verified"
after a successful forced verify, and the verify's mapping replace dropped
the main depot and six content-package depots.

Fixtures are synthetic, shaped like the operator's report (2026-10-18): app
553850 with 11 depots, of which the forced verify wrote new bytes into only
four (553853, 553854, 2506270, 4199890); the content-package depots have
their own (unowned) package apps; every depot has a manifest ``.bin`` in
SteamPrefill's temp cache named for 553850.

Guarantees pinned here, each by name:

- G1 the mapping replace keeps an unobserved depot the app's own manifests
  name (bytes on disk) -- the HELLDIVERS 2 run keeps all 11 depots;
- G2 unreadable manifest evidence removes nothing;
- G3 a depot no manifest names is still pruned (decision 3 still works);
- G4 GC never plans the current build's chunks as orphans because the app
  whose manifest record describes them lost its mapping row;
- G5 a group reads 100 % when the game is 100 % and the only unmeasurable
  part is a package depot, which is reported separately with its bytes;
- G6 a package depot the game also maps is covered by the game's
  measurement (not reported as unmeasured);
- G7 the game's own pairs stay strict: game unknown -> group unknown.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests import stub_prefill
from tests.conftest import TEST_API_KEY
from tests.test_cache_complete_1a import chunks, record, settings_for
from tests.test_gc import (
    _cid,
    write_archive_bin,
    write_cache_manifest,
    write_chunks,
)
from tests.test_manifests import _bin_manifest_bytes
from tests.test_worker import enqueue, make_settings, wait_for_job
from vault_api import app_classification, cache_completeness, depot_manifests, gc_execute
from vault_api.db import get_connection, init_db
from vault_api.main import create_app
from vault_api.prefill import apply_observed_mapping, upsert_mapping

AUTH = {"X-Api-Key": TEST_API_KEY}

GAME = 553850
MAIN_DEPOTS = [553851, 553853, 553854]
PACKAGE_DEPOTS = [2506210, 2506220, 2506240, 2506250, 2506260, 2506270, 2506280, 4199890]
ALL_DEPOTS = MAIN_DEPOTS + PACKAGE_DEPOTS
WRITTEN_BY_THE_VERIFY = [553853, 553854, 2506270, 4199890]


def package_app(depotid: int) -> int:
    """The package app a content-package depot belongs to (synthetic ids)."""
    return depotid + 1_000_000


@pytest.fixture
def conn(tmp_path: Path):
    path = str(tmp_path / "vault.db")
    init_db(path)
    connection = get_connection(path)
    try:
        yield connection
    finally:
        connection.close()


def mapped(conn: sqlite3.Connection, appid: int) -> set[int]:
    return {
        int(r[0])
        for r in conn.execute("SELECT depotid FROM depot_app_map WHERE appid = ?", (appid,))
    }


# --------------------------------------------------------------------------
# B / fix 1: the mapping replace on partial evidence
# --------------------------------------------------------------------------


def test_G1_unit_unobserved_depot_named_by_a_manifest_is_kept(conn: sqlite3.Connection) -> None:
    for depotid in (1, 2, 3):
        upsert_mapping(conn, depotid, 440, None)
    change = apply_observed_mapping(conn, 440, {1}, manifest_named={1, 2})
    assert mapped(conn, 440) == {1, 2}
    assert change.removed == {3}
    assert change.protected == {2}
    assert "kept_unobserved=[2]" in change.summary()


def test_G2_unit_unreadable_evidence_removes_nothing(conn: sqlite3.Connection) -> None:
    for depotid in (1, 2, 3):
        upsert_mapping(conn, depotid, 440, None)
    change = apply_observed_mapping(conn, 440, {1}, manifest_named=None)
    assert mapped(conn, 440) == {1, 2, 3}
    assert change.removed == set()
    assert "could not be read" in change.summary()


def _helldivers_stub(tmp_path: Path, cache_root: Path) -> tuple[str, Path]:
    """A forced verify of the cached game: new bytes in four depots only,
    a manifest ``.bin`` for all eleven (package depots name their package
    app as the containing app, like SteamPrefill's filename contract)."""
    temp_cache = tmp_path / "steamprefill-cache"
    bins = []
    for depotid in ALL_DEPOTS:
        containing = package_app(depotid) if depotid in PACKAGE_DEPOTS else GAME
        bins.append(
            {
                "dir": str(temp_cache),
                "filename": f"{GAME}_{containing}_{depotid}_{depotid}7.bin",
                "data": _bin_manifest_bytes(
                    depot_id=depotid,
                    manifest_id=int(f"{depotid}7"),
                    files=[[(_cid(depotid), 160)]],
                ),
            }
        )
    executable = stub_prefill.make_stub(
        tmp_path / "bin",
        cache_root=str(cache_root),
        depots_by_app={GAME: WRITTEN_BY_THE_VERIFY},
        manifest_bins=bins,
    )
    return executable, temp_cache


def _seed_cached_game(client: TestClient, cache_root: Path) -> None:
    """The state before the verify: every depot has bytes on disk and is
    mapped to the game; each package depot is also mapped to its package."""
    for depotid in ALL_DEPOTS:
        chunk_dir = cache_root / "depot" / str(depotid) / "chunk"
        chunk_dir.mkdir(parents=True, exist_ok=True)
        (chunk_dir / _cid(depotid)).write_bytes(b"\xab" * 160)
        client.put(f"/v1/mapping/{depotid}", json={"appid": GAME}, headers=AUTH)
        if depotid in PACKAGE_DEPOTS:
            client.put(
                f"/v1/mapping/{depotid}", json={"appid": package_app(depotid)}, headers=AUTH
            )


def test_G1_helldivers_forced_verify_keeps_all_eleven_depots(tmp_path: Path) -> None:
    """MUTATION TARGET (G1): before the fix this run removed 553851 and six
    package depots, and 553851 ended unmapped ("1 unmapped depot")."""
    cache_root = tmp_path / "cache"
    executable, temp_cache = _helldivers_stub(tmp_path, cache_root)
    settings = make_settings(
        tmp_path, cache_root, executable, steamprefill_cache_dir=str(temp_cache)
    )
    with TestClient(create_app(settings)) as client:
        _seed_cached_game(client, cache_root)
        (job_id,) = enqueue(client, GAME)
        job = wait_for_job(client, job_id)
        assert job["status"] == "done", job["log_excerpt"]
        log = job["log_excerpt"]
        assert "removed=[]" in log, log
        kept = sorted(set(ALL_DEPOTS) - set(WRITTEN_BY_THE_VERIFY))
        assert f"kept_unobserved={kept}" in log, log
        detail = client.get(f"/v1/games/{GAME}", headers=AUTH).json()
        assert {d["depotid"] for d in detail["depots"]} == set(ALL_DEPOTS)
        assert detail["cache_unmapped_depots"] == 0

    conn = get_connection(settings.db_path)
    try:
        assert mapped(conn, GAME) == set(ALL_DEPOTS)
        for depotid in PACKAGE_DEPOTS:
            assert package_app(depotid) in {
                int(r[0])
                for r in conn.execute(
                    "SELECT appid FROM depot_app_map WHERE depotid = ?", (depotid,)
                )
            }
    finally:
        conn.close()


def test_G2_worker_without_a_readable_temp_cache_removes_nothing(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    executable = stub_prefill.make_stub(
        tmp_path / "bin", cache_root=str(cache_root), depots_by_app={440: [441]}
    )
    # make_settings points the temp cache at a directory that never exists.
    settings = make_settings(tmp_path, cache_root, executable)
    with TestClient(create_app(settings)) as client:
        client.put("/v1/mapping/999", json={"appid": 440}, headers=AUTH)
        (job_id,) = enqueue(client, 440)
        job = wait_for_job(client, job_id)
        assert job["status"] == "done"
        assert "kept_unobserved=[999] (the manifest evidence could not be read)" in job["log_excerpt"]
        pairs = {(e["depotid"], e["appid"]) for e in client.get("/v1/mapping", headers=AUTH).json()}
    assert pairs == {(441, 440), (999, 440)}


def test_G3_worker_still_prunes_a_depot_no_manifest_names(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    temp_cache = tmp_path / "steamprefill-cache"
    temp_cache.mkdir()
    executable = stub_prefill.make_stub(
        tmp_path / "bin", cache_root=str(cache_root), depots_by_app={440: [441]}
    )
    settings = make_settings(
        tmp_path, cache_root, executable, steamprefill_cache_dir=str(temp_cache)
    )
    with TestClient(create_app(settings)) as client:
        client.put("/v1/mapping/999", json={"appid": 440}, headers=AUTH)
        (job_id,) = enqueue(client, 440)
        job = wait_for_job(client, job_id)
        assert "removed=[999]" in job["log_excerpt"]
        pairs = {(e["depotid"], e["appid"]) for e in client.get("/v1/mapping", headers=AUTH).json()}
    assert pairs == {(441, 440)}


# --------------------------------------------------------------------------
# C: GC safety
# --------------------------------------------------------------------------


def test_G4_gc_keeps_current_chunks_whose_record_lost_its_mapping(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    """MUTATION TARGET (G4), executed for real. A package depot whose current
    manifest is recorded under the game (the run that downloaded it) is
    mapped only to the package app (the game's row was lost). The package
    app holds content (NOT-OWNED-2 kept it ``done``) and has no record of its
    own, and an OLDER client manifest sits in the cache. Before the fix GC for
    the package resolved the older stored manifest and deleted the current
    build's chunks."""
    cache = tmp_path / "cache"
    (cache / "depot").mkdir(parents=True)
    archive = tmp_path / "manifests"
    depot = 2506210
    pkg = package_app(depot)
    current = {**chunks(0, 3, 160)}
    old = {_cid(0): 160, _cid(10): 160, _cid(11): 160}
    write_chunks(cache, depot, {**current, **old})
    write_archive_bin(archive, depotid=depot, manifestid="2000", chunks=current)
    write_cache_manifest(cache, depotid=depot, manifestid="1000", chunks=old)

    upsert_mapping(conn, depot, pkg, "Package")
    conn.execute("UPDATE apps SET status = 'done' WHERE appid = ?", (pkg,))
    conn.execute("INSERT OR IGNORE INTO apps (appid, name, status) VALUES (?, 'Game', 'done')", (GAME,))
    depot_manifests.upsert_depot_manifest(
        conn, appid=GAME, containing_appid=pkg, depotid=depot, manifestid="2000",
        chunk_count=3, total_bytes=480, recorded_at="2026-10-18T14:30:00Z", source="test",
    )
    conn.commit()

    report = gc_execute.run_gc(
        conn, pkg, cache_root=str(cache), archive_dir=str(archive), execute=True
    )
    on_disk = {p.name for p in (cache / "depot" / str(depot) / "chunk").iterdir()}
    assert set(current) <= on_disk, (report, on_disk)
    (result,) = report.depots
    # The game's record counts: its archived manifest joins the keep set
    # (union with the package's older stored manifest), nothing is removed.
    assert result.status == "planned"
    assert result.removed_count == 0


def test_G4_gc_never_touches_a_depot_that_lost_every_mapping(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    """553851 after the bug: recorded, bytes on disk, mapped to no app. GC for
    the game plans only depots the game maps, so it is never a GC target."""
    cache = tmp_path / "cache"
    (cache / "depot").mkdir(parents=True)
    archive = tmp_path / "manifests"
    write_chunks(cache, 553851, chunks(0, 2, 160))
    write_chunks(cache, 553853, chunks(5, 1, 160))
    write_archive_bin(archive, depotid=553853, manifestid="3000", chunks=chunks(5, 1, 160))
    record(conn, GAME, 553853, "3000", "2026-10-18T14:30:00Z")
    depot_manifests.upsert_depot_manifest(
        conn, appid=GAME, containing_appid=GAME, depotid=553851, manifestid="4000",
        chunk_count=1, total_bytes=1, recorded_at="2026-10-18T14:30:00Z", source="test",
    )
    report = gc_execute.run_gc(
        conn, GAME, cache_root=str(cache), archive_dir=str(archive), execute=True
    )
    assert len(list((cache / "depot" / "553851" / "chunk").iterdir())) == 2
    assert report.plan_ok
    assert 553851 not in {d.depotid for d in report.depots}
    assert 553853 in {d.depotid for d in report.depots}


# --------------------------------------------------------------------------
# A / fix 2: the group figure
# --------------------------------------------------------------------------


def _group_setup(
    conn: sqlite3.Connection, tmp_path: Path, *, game_maps_package_depot: bool
):
    settings = settings_for(tmp_path)
    cache = Path(settings.cache_root)
    archive = Path(settings.manifest_archive_dir)
    # The game: one depot, fully cached, recorded and archived.
    record(conn, 440, 441, "1000", "2026-10-18T14:30:00Z")
    write_archive_bin(archive, depotid=441, manifestid="1000", chunks=chunks(0, 2))
    write_chunks(cache, 441, chunks(0, 2))
    # The unowned package 730: its depot's manifest is recorded under the GAME
    # (the game's prefill downloaded it); the package's own pair has no target.
    upsert_mapping(conn, 731, 730, "Package")
    depot_manifests.upsert_depot_manifest(
        conn, appid=440, containing_appid=730, depotid=731, manifestid="1001",
        chunk_count=3, total_bytes=300, recorded_at="2026-10-18T14:30:00Z", source="test",
    )
    if game_maps_package_depot:
        upsert_mapping(conn, 731, 440, None)
    write_archive_bin(archive, depotid=731, manifestid="1001", chunks=chunks(10, 3))
    write_chunks(cache, 731, chunks(10, 3))
    conn.execute(
        "INSERT INTO app_classification (appid, app_type, parent_appid, source, "
        "checked_at) VALUES (730, 'dlc', 440, ?, '2026-10-14T00:00:00Z')",
        (app_classification.SOURCE_STORE,),
    )
    conn.commit()
    cache_completeness.evaluate_apps(conn, settings, [440, 730])
    return settings


def _games(settings) -> dict[int, dict]:
    with TestClient(create_app(settings)) as client:
        rows = {g["appid"]: g for g in client.get("/v1/games", headers=AUTH).json()}
        rows["detail"] = client.get("/v1/games/440", headers=AUTH).json()
    return rows


def test_G5_unmeasurable_package_is_reported_not_blocking(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    """MUTATION TARGET (G5): before the fix the group figure was null (the
    card stayed "Cached · not verified" forever)."""
    settings = _group_setup(conn, tmp_path, game_maps_package_depot=False)
    status = conn.execute(
        "SELECT status FROM cache_completeness WHERE appid = 730"
    ).fetchone()[0]
    assert status == cache_completeness.STATUS_NO_TARGET
    games = _games(settings)
    for row in (games[440], games["detail"]):
        assert row["cache_complete_pct"] == 100
        assert row["group_cache_complete_pct"] == 100
        assert row["group_cache_unmeasured_depots"] == 1
        assert row["group_cache_unmeasured_bytes"] == 300
    assert games[730]["group_cache_complete_pct"] is None
    assert games[730]["group_cache_unmeasured_depots"] == 0


def test_G6_package_depot_the_game_maps_is_covered(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = _group_setup(conn, tmp_path, game_maps_package_depot=True)
    games = _games(settings)
    assert games[440]["group_cache_complete_pct"] == 100
    assert games[440]["group_cache_unmeasured_depots"] == 0
    assert games[440]["group_cache_unmeasured_bytes"] == 0


def test_G6_a_covered_package_depot_still_counts_its_missing_chunks(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = _group_setup(conn, tmp_path, game_maps_package_depot=True)
    (Path(settings.cache_root) / "depot" / "731" / "chunk" / _cid(10)).unlink()
    cache_completeness.evaluate_apps(conn, settings, [440, 730])
    games = _games(settings)
    # (200 + 200) / (200 + 300)
    assert games[440]["group_cache_complete_pct"] == 80


def test_G7_game_unknown_keeps_the_group_unknown(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    """MUTATION TARGET (G7): the packages alone never make a group current."""
    settings = _group_setup(conn, tmp_path, game_maps_package_depot=False)
    # A measured package of its own (record under the package app).
    depot_manifests.upsert_depot_manifest(
        conn, appid=730, containing_appid=730, depotid=731, manifestid="1001",
        chunk_count=3, total_bytes=300, recorded_at="2026-10-18T14:30:00Z", source="test",
    )
    cache_completeness.evaluate_apps(conn, settings, [730])
    conn.execute("DELETE FROM cache_completeness WHERE appid = 440")
    conn.commit()
    games = _games(settings)
    assert games[440]["cache_complete_pct"] is None
    assert games[440]["group_cache_complete_pct"] is None
    assert games[440]["group_cache_unmeasured_depots"] == 0


def test_group_figure_unit_rules() -> None:
    row = lambda a, d, gid, exp, pres: cache_completeness.DepotCompleteness(  # noqa: E731
        appid=a, depotid=d, manifestid=gid, status=cache_completeness.STATUS_OK,
        checked_at="2026-10-18T00:00:00Z", expected_bytes=exp, present_bytes=pres,
        missing_chunks=0,
    )
    index = cache_completeness.CompletenessIndex(
        rows={(1, 10): row(1, 10, "5", 100, 100), (2, 20): row(2, 20, "6", 50, 50)},
        targets={(1, 10): "5", (2, 20): "6", (3, 30): None, (2, 10): None},
    )
    group = cache_completeness.group_figure(
        index, 1, {1: [10], 2: [20, 10], 3: [30]}, {10: 100, 20: 50, 30: 7}
    )
    assert group.figure.pct == 100
    assert group.figure.expected_bytes == 150
    # (2, 10) is covered by (1, 10); only depot 30 is unmeasured.
    assert (group.unmeasured_depots, group.unmeasured_bytes) == (1, 7)
    assert cache_completeness.group_figure(index, 3, {3: [30], 1: [10]}, {}).figure.pct is None


# --------------------------------------------------------------------------
# Review round 1: B1 (only truly unmeasurable package pairs are set aside)
# --------------------------------------------------------------------------


def test_G8_stale_owned_package_keeps_group_unknown(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    """MUTATION TARGET (G8): an owned package measured 20 % on an OLD build,
    whose target has moved on since, must not hide behind "Current"."""
    settings = settings_for(tmp_path)
    cache = Path(settings.cache_root)
    archive = Path(settings.manifest_archive_dir)
    record(conn, 440, 441, "1000", "2026-10-18T14:30:00Z")
    write_archive_bin(archive, depotid=441, manifestid="1000", chunks=chunks(0, 2))
    write_chunks(cache, 441, chunks(0, 2))
    record(conn, 730, 731, "1001", "2026-10-18T14:30:00Z")
    write_archive_bin(archive, depotid=731, manifestid="1001", chunks=chunks(10, 5))
    write_chunks(cache, 731, chunks(10, 1))  # 20 %
    conn.execute(
        "INSERT INTO app_classification (appid, app_type, parent_appid, source, "
        "checked_at) VALUES (730, 'dlc', 440, ?, '2026-10-14T00:00:00Z')",
        (app_classification.SOURCE_STORE,),
    )
    conn.commit()
    cache_completeness.evaluate_apps(conn, settings, [440, 730])
    # A newer manifest is recorded for the package; its stored row is stale.
    record(conn, 730, 731, "1002", "2026-10-18T15:00:00Z")
    games = _games(settings)
    assert games[440]["cache_complete_pct"] == 100
    assert games[440]["group_cache_complete_pct"] is None
    assert games[440]["group_cache_unmeasured_depots"] == 0


def _unit_index(status: str, manifestid: str | None, target: str | None):
    ok = cache_completeness.DepotCompleteness(
        appid=1, depotid=10, manifestid="5", status=cache_completeness.STATUS_OK,
        checked_at="2026-10-18T00:00:00Z", expected_bytes=100, present_bytes=100,
        missing_chunks=0,
    )
    pkg = cache_completeness.DepotCompleteness(
        appid=2, depotid=20, manifestid=manifestid, status=status,
        checked_at="2026-10-18T00:00:00Z",
    )
    return cache_completeness.CompletenessIndex(
        rows={(1, 10): ok, (2, 20): pkg}, targets={(1, 10): "5", (2, 20): target}
    )


def test_G8_unreadable_cache_package_keeps_group_unknown() -> None:
    """MUTATION TARGET (G8): unreadable_cache is not 'unmeasurable'."""
    index = _unit_index(cache_completeness.STATUS_UNREADABLE_CACHE, "6", "6")
    group = cache_completeness.group_figure(index, 1, {1: [10], 2: [20]}, {20: 9})
    assert group.figure.pct is None


def test_G8_target_without_a_row_keeps_group_unknown() -> None:
    index = _unit_index(cache_completeness.STATUS_OK, "6", "6")
    index = cache_completeness.CompletenessIndex(
        rows={k: v for k, v in index.rows.items() if k != (2, 20)}, targets=index.targets
    )
    assert cache_completeness.group_figure(index, 1, {1: [10], 2: [20]}, {}).figure.pct is None


@pytest.mark.parametrize(
    "status, manifestid, target",
    [
        (cache_completeness.STATUS_NO_TARGET, None, None),
        (cache_completeness.STATUS_MANIFEST_UNAVAILABLE, "6", "6"),
    ],
)
def test_G8_truly_unmeasurable_package_is_set_aside(status, manifestid, target) -> None:
    index = _unit_index(status, manifestid, target)
    group = cache_completeness.group_figure(index, 1, {1: [10], 2: [20]}, {20: 9})
    assert group.figure.pct == 100
    assert (group.unmeasured_depots, group.unmeasured_bytes) == (1, 9)


def test_G8_manifest_unavailable_for_an_old_target_keeps_group_unknown() -> None:
    index = _unit_index(cache_completeness.STATUS_MANIFEST_UNAVAILABLE, "6", "7")
    assert cache_completeness.group_figure(index, 1, {1: [10], 2: [20]}, {}).figure.pct is None


# --------------------------------------------------------------------------
# Review round 1: S3, N2
# --------------------------------------------------------------------------


def test_S3_manifest_named_is_required(conn: sqlite3.Connection) -> None:
    with pytest.raises(TypeError):
        apply_observed_mapping(conn, 440, {1})  # type: ignore[call-arg]


def test_N2_record_with_unreadable_app_id_skips_the_depot(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    cache = tmp_path / "cache"
    (cache / "depot").mkdir(parents=True)
    archive = tmp_path / "manifests"
    write_chunks(cache, 441, chunks(0, 3, 160))
    write_archive_bin(archive, depotid=441, manifestid="1000", chunks=chunks(0, 1, 160))
    record(conn, 440, 441, "1000", "2026-10-18T14:30:00Z")
    depot_manifests.upsert_depot_manifest(
        conn, appid=999, containing_appid=None, depotid=441, manifestid="999",
        chunk_count=1, total_bytes=1, recorded_at="2026-10-18T14:30:00Z", source="test",
    )
    conn.execute("UPDATE depot_manifests SET appid = 'x' WHERE appid = 999")
    conn.commit()
    report = gc_execute.run_gc(
        conn, 440, cache_root=str(cache), archive_dir=str(archive), execute=True
    )
    (result,) = report.depots
    assert result.status == "skipped_unreadable_owner"
    assert len(list((cache / "depot" / "441" / "chunk").iterdir())) == 3


# --------------------------------------------------------------------------
# D: "Confirmed current" (operator decision 2026-10-18, Weg A)
# --------------------------------------------------------------------------

_UPDATED_TABLE = (
    "  Prefilled 1 apps totaling 12 MiB in 05.0000 \n"
    "   Updated | Up To Date\n"
    "  ---------+------------\n"
    "      1    |     0\n"
)


def _last_manifest_check(settings) -> str | None:
    conn = get_connection(settings.db_path)
    try:
        row = conn.execute("SELECT last_manifest_check FROM apps WHERE appid = 440").fetchone()
    finally:
        conn.close()
    return None if row is None else row[0]


def test_D_forced_verify_with_updated_1_is_confirmed_current(tmp_path: Path) -> None:
    """MUTATION TARGET (D): the HELLDIVERS 2 verify shape (forced, Updated=1)."""
    cache_root = tmp_path / "cache"
    executable = stub_prefill.make_stub(
        tmp_path / "bin", cache_root=str(cache_root), depots_by_app={440: [441]},
        summary_text=_UPDATED_TABLE,
    )
    settings = make_settings(tmp_path, cache_root, executable)
    with TestClient(create_app(settings)) as client:
        client.put("/v1/mapping/441", json={"appid": 440}, headers=AUTH)
        response = client.post("/v1/prefill/verify", json={"appids": [440]}, headers=AUTH)
        assert response.status_code == 202, response.text
        job = wait_for_job(client, response.json()[0]["job_id"])
        assert job["status"] == "done" and job["updated"] == 1
        detail = client.get("/v1/games/440", headers=AUTH).json()
    assert detail["last_manifest_check"] is not None
    assert detail["last_manifest_check"] == _last_manifest_check(settings)


@pytest.mark.parametrize("mode", ["fail", "unparseable"])
def test_D_failed_or_unreadable_run_leaves_it_untouched(tmp_path: Path, mode: str) -> None:
    cache_root = tmp_path / "cache"
    executable = stub_prefill.make_stub(
        tmp_path / "bin",
        mode="fail" if mode == "fail" else "success",
        cache_root=str(cache_root),
        depots_by_app={440: [441]},
    )
    settings = make_settings(tmp_path, cache_root, executable)
    with TestClient(create_app(settings)) as client:
        (job_id,) = enqueue(client, 440)
        job = wait_for_job(client, job_id)
        assert job["status"] == ("error" if mode == "fail" else "done")
        if mode == "unparseable":
            assert job["summary_parse_ok"] is False
    assert _last_manifest_check(settings) is None
