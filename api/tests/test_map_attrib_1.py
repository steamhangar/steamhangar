"""WP MAP-ATTRIB-1 (rc21.1): a prefill mapped every depot whose directory
changed during its run to the running app, so depots another PC loaded at the
same time landed on the wrong game.

Fixtures are synthetic, shaped exactly like the prod rows measured on
2026-11-01 (docs/PROJECT_PLAN.md, rc21.1 "measured on prod"): Wanderburg
(3624140, own depot 3624141) also mapped 436781 (owner 436780), 553851 and
553854 (owner 553850) and 1716751 (owners 1716750 and 3751950); each true
owner is mapped and has a recorded manifest; the four foreign pairs have
``no_target`` completeness rows, 3624141 is ``ok``.

Guarantees pinned here, each by name:

- A1 an observed depot without ownership evidence is not mapped and is
  logged as observed but not attributed;
- A2 without any evidence, an observed depot another app maps never gets a
  second owner (an unmapped one still does: today's behaviour);
- A3 evidence still adds a real shared depot as a second owner;
- A4 removals never depend on the new filter;
- E1 evidence = own manifest file, recorded manifest (own or containing),
  oracle depot list (own + content packages, oracle on only);
- C1 the cleanup removes exactly the four foreign Wanderburg pairs and their
  completeness rows; 3624141 stays; 1716751 stays mapped to both owners;
- C2 the only mapping of a depot is never removed, nor a row whose
  co-owners have no evidence;
- C1 (full shape) exactly the reviewer's simulated prod effect: the four
  Wanderburg pairs and (1465471, 1203620) go, (1203621, 1203620) is
  restored; HD2 package rows, a shared redistributable and a store-parent
  package row stay;
- C3 no oracle data for the app (or oracle off) -> skipped, left for the
  operator, and the step is still stamped (never a standing startup rule);
- C4 unreadable temp cache or depot root -> nothing changed, not stamped;
- C5 a content package sharing its parent's depot is kept;
- C6 one-time and idempotent; runs from the startup lifespan;
- R1 a containing_appid alone never restores a mapping;
- R2 restore needs the app's own record, the oracle listing the depot
  (oracle on) and the depot directory in the cache;
- R3 restore needs the recorded manifest to be the oracle's public gid (own
  answer or a content package's); an older record is left for the operator;
- R4 a content-package depot recorded under the game with the package as
  containing app is never restored to the game (GROUP-VERIFIED-1);
- G1 GC keep sets of a multi-owner depot keep every owner's recorded
  manifest after the cleanup;
- G2 a depot is never planned as orphans because its mapping row was removed;
- G3 GC after a restore keeps the recorded build and removes only leftovers.
"""

from __future__ import annotations

import dataclasses
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests import stub_prefill
from tests.test_gc import _cid, write_archive_bin, write_chunks
from tests.test_manifests import _bin_manifest_bytes
from tests.test_oracle import enabled_settings
from tests.test_worker import enqueue, make_settings, wait_for_job
from vault_api import depot_manifests, gc_execute, map_attrib
from vault_api.config import MANIFEST_ORACLE_OFF, MANIFEST_ORACLE_STEAMCMD_API, Settings
from vault_api.db import get_connection, init_db
from vault_api.main import create_app
from vault_api.mapping import upsert_mapping
from vault_api.prefill import apply_observed_mapping

WANDERBURG = 3624140
OWN_DEPOT = 3624141
HD2 = 553850
HD2_DEPOTS = (553851, 553854)
OLD_APP = 436780
OLD_DEPOT = 436781
UBISOFT = 1716750
BLACK_FLAG = 3751950
UBI_DEPOT = 1716751
FOREIGN = {(OLD_DEPOT, WANDERBURG), (553851, WANDERBURG), (553854, WANDERBURG), (UBI_DEPOT, WANDERBURG)}
HD2_PACKAGES = (2506210, 2506220, 2506240, 2506250, 2506260, 2506270, 2506280, 4199890)
REDIST_APP, REDIST_DEPOT, REDIST_USERS = 228980, 229005, (780310, 990080)
FACTORIO, FACTORIO_PKG, FACTORIO_DEPOT = 427520, 645390, 645393
ENSHROUDED, ENSHROUDED_DEPOT, ENSHROUDED_GID = 1203620, 1203621, "4708593645837114731"
ENSH_OTHER_APP, ENSH_OTHER_DEPOT = 1465470, 1465471
WITCHER = 292030
WITCHER_LOST = (292033, 292036)
#: The Witcher 3 content-package depots recorded under the game with the
#: package as containing app, ``{depot: package app}`` (prod 2026-11-01).
WITCHER_PACKAGES = {
    292044: 378649, 310611: 378649, 310613: 378648, 310619: 378648,
    **{d: d for d in (370000, 370001, 370002, 370003, 373950, 373951, 376390, 376391)},
    **{d: d for d in range(378640, 378650)},
}
#: The full expected effect on the prod shape (reviewer's export simulation).
REMOVED = FOREIGN | {(ENSH_OTHER_DEPOT, ENSHROUDED)}
RESTORED = {(ENSHROUDED_DEPOT, ENSHROUDED), *((d, WITCHER) for d in WITCHER_LOST)}


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


def owners(conn: sqlite3.Connection, depotid: int) -> set[int]:
    return {
        int(r[0])
        for r in conn.execute("SELECT appid FROM depot_app_map WHERE depotid = ?", (depotid,))
    }


def pairs(conn: sqlite3.Connection) -> set[tuple[int, int]]:
    return {
        (int(r[0]), int(r[1]))
        for r in conn.execute("SELECT depotid, appid FROM depot_app_map")
    }


def recorded(
    conn: sqlite3.Connection, appid: int, depotid: int, gid: str, containing: int | None = None
) -> None:
    depot_manifests.upsert_depot_manifest(
        conn, appid=appid, containing_appid=containing, depotid=depotid, manifestid=gid,
        chunk_count=1, total_bytes=1, recorded_at="2026-10-31T12:42:00Z", source="test",
    )


def oracle_lists(
    conn: sqlite3.Connection, appid: int, depots: list[int], gids: dict[int, str] | None = None
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO oracle_app_state (appid, buildid, checked_at, source, "
        "depot_count, branch_count) VALUES (?, '1', '2026-11-01T06:00:00Z', 'test', ?, 1)",
        (appid, len(depots)),
    )
    for depotid in depots:
        conn.execute(
            "INSERT OR REPLACE INTO oracle_branch_manifests (appid, depotid, branch, "
            "manifestid, recorded_at, source) VALUES (?, ?, 'public', ?, "
            "'2026-11-01T06:00:00Z', 'test')",
            (appid, depotid, (gids or {}).get(depotid, f"{depotid}9")),
        )
    conn.commit()


def completeness(conn: sqlite3.Connection, appid: int, depotid: int, status: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO cache_completeness (appid, depotid, manifestid, status, "
        "checked_at) VALUES (?, ?, NULL, ?, '2026-11-01T06:00:00Z')",
        (appid, depotid, status),
    )
    conn.commit()


def cleanup_settings(
    tmp_path: Path, *, oracle_on: bool = True, temp_cache: bool = True, depot_root: bool = True
) -> Settings:
    cache_dir = tmp_path / "steamprefill-cache"
    if temp_cache:
        cache_dir.mkdir(exist_ok=True)
    if depot_root:
        (tmp_path / "cache" / "depot").mkdir(parents=True, exist_ok=True)
    base = enabled_settings(tmp_path, steamprefill_cache_dir=str(cache_dir))
    if not oracle_on:
        base = dataclasses.replace(base, manifest_oracle=MANIFEST_ORACLE_OFF)
    return base


def seed_prod_shape(conn: sqlite3.Connection, *, ubisoft_own_record: bool = True) -> None:
    """The prod rows of 2026-11-01 (module docstring)."""
    for depotid in (OWN_DEPOT, OLD_DEPOT, *HD2_DEPOTS, UBI_DEPOT):
        upsert_mapping(conn, depotid, WANDERBURG, "Wanderburg")
    upsert_mapping(conn, OLD_DEPOT, OLD_APP, None)
    for depotid in HD2_DEPOTS:
        upsert_mapping(conn, depotid, HD2, "HELLDIVERS 2")
    upsert_mapping(conn, UBI_DEPOT, UBISOFT, "Ubisoft Connect PC Client")
    upsert_mapping(conn, UBI_DEPOT, BLACK_FLAG, "AC Black Flag Resynced")

    recorded(conn, WANDERBURG, OWN_DEPOT, "1001")
    recorded(conn, OLD_APP, OLD_DEPOT, "2001")
    for depotid in HD2_DEPOTS:
        recorded(conn, HD2, depotid, f"{depotid}1")
    if ubisoft_own_record:
        recorded(conn, UBISOFT, UBI_DEPOT, "3001")
    recorded(conn, BLACK_FLAG, UBI_DEPOT, "3002", containing=UBISOFT)

    oracle_lists(conn, WANDERBURG, [OWN_DEPOT])
    completeness(conn, WANDERBURG, OWN_DEPOT, "ok")
    for depotid, _app in FOREIGN:
        completeness(conn, WANDERBURG, depotid, "no_target")


def seed_full_prod_shape(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """``seed_prod_shape`` plus the prod rows the guards must protect and the
    Enshrouded case (review of ce7c4e3, prod measurement 2026-11-01)."""
    seed_prod_shape(conn)
    oracle_lists(conn, HD2, list(HD2_DEPOTS))
    # HELLDIVERS 2 content packages: depot id = package app id, recorded under
    # the game with the package as containing app, linked by the game's answer.
    for package in HD2_PACKAGES:
        upsert_mapping(conn, package, HD2, None)
        upsert_mapping(conn, package, package, None)
        recorded(conn, HD2, package, f"{package}1", containing=package)
        conn.execute(
            "INSERT INTO oracle_dlc_links VALUES (?, ?, '2026-11-01T06:00:00Z', 'test')",
            (HD2, package),
        )
    # A redistributable shared by two games and its own tool app.
    upsert_mapping(conn, REDIST_DEPOT, REDIST_APP, None)
    for user in REDIST_USERS:
        upsert_mapping(conn, REDIST_DEPOT, user, None)
        recorded(conn, user, REDIST_DEPOT, f"{user}1", containing=REDIST_APP)
    # A package (store parent) sharing its game's depot; the package has an
    # oracle list that does not name the depot: only the relation keeps it.
    upsert_mapping(conn, FACTORIO_DEPOT, FACTORIO, None)
    upsert_mapping(conn, FACTORIO_DEPOT, FACTORIO_PKG, None)
    recorded(conn, FACTORIO, FACTORIO_DEPOT, "6453931", containing=FACTORIO)
    oracle_lists(conn, FACTORIO_PKG, [645391])
    conn.execute(
        "INSERT INTO app_classification (appid, source, app_type, parent_appid, linked, "
        "name, checked_at) VALUES (?, 'store', 'dlc', ?, 0, NULL, 'x')",
        (FACTORIO_PKG, FACTORIO),
    )
    # Enshrouded: its own depot lost its row, another game's depot was given
    # to it.
    upsert_mapping(conn, ENSH_OTHER_DEPOT, ENSHROUDED, "Enshrouded")
    upsert_mapping(conn, ENSH_OTHER_DEPOT, ENSH_OTHER_APP, None)
    recorded(conn, ENSHROUDED, ENSHROUDED_DEPOT, ENSHROUDED_GID, containing=ENSHROUDED)
    recorded(conn, ENSH_OTHER_APP, ENSH_OTHER_DEPOT, "14654711", containing=ENSH_OTHER_APP)
    oracle_lists(
        conn, ENSHROUDED, [ENSHROUDED_DEPOT, 4951850], gids={ENSHROUDED_DEPOT: ENSHROUDED_GID}
    )
    oracle_lists(conn, ENSH_OTHER_APP, [ENSH_OTHER_DEPOT])
    # The Witcher 3: two own depots lost their rows; its content-package
    # depots are recorded under the game with the package as containing app
    # and mapped only to the package (GROUP-VERIFIED-1). Every record is the
    # oracle's public build, so only the containing-app rule keeps the
    # package depots from gaining the game as a second owner.
    upsert_mapping(conn, 292031, WITCHER, "The Witcher 3")
    recorded(conn, WITCHER, 292031, "2920319", containing=WITCHER)
    for depotid in WITCHER_LOST:
        recorded(conn, WITCHER, depotid, f"{depotid}9", containing=WITCHER)
    for depotid, package in WITCHER_PACKAGES.items():
        upsert_mapping(conn, depotid, package, None)
        recorded(conn, WITCHER, depotid, f"{depotid}9", containing=package)
    for package in sorted(set(WITCHER_PACKAGES.values())):
        conn.execute(
            "INSERT OR IGNORE INTO oracle_dlc_links VALUES (?, ?, 'x', 'test')", (WITCHER, package)
        )
    oracle_lists(conn, WITCHER, [292031, *WITCHER_LOST, *WITCHER_PACKAGES])
    conn.commit()
    for depotid in (ENSHROUDED_DEPOT, ENSH_OTHER_DEPOT, *WITCHER_LOST, *WITCHER_PACKAGES):
        (tmp_path / "cache" / "depot" / str(depotid)).mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------
# A: attribution in apply_observed_mapping
# --------------------------------------------------------------------------


def test_A1_observed_depot_without_evidence_is_not_mapped(conn: sqlite3.Connection) -> None:
    """MUTATION TARGET (A1): before the fix 436781 was added to Wanderburg."""
    upsert_mapping(conn, OLD_DEPOT, OLD_APP, None)
    change = apply_observed_mapping(
        conn, WANDERBURG, {OWN_DEPOT, OLD_DEPOT}, manifest_named={OWN_DEPOT}, owned={OWN_DEPOT}
    )
    assert mapped(conn, WANDERBURG) == {OWN_DEPOT}
    assert owners(conn, OLD_DEPOT) == {OLD_APP}
    assert change.added == {OWN_DEPOT}
    assert change.not_attributed == {OLD_DEPOT}
    assert f"observed_not_attributed=1 [{OLD_DEPOT}]" in change.summary()


def test_A1_an_unmapped_foreign_depot_is_not_mapped_either(conn: sqlite3.Connection) -> None:
    change = apply_observed_mapping(
        conn, WANDERBURG, {OWN_DEPOT, 999}, manifest_named=set(), owned={OWN_DEPOT}
    )
    assert pairs(conn) == {(OWN_DEPOT, WANDERBURG)}
    assert change.not_attributed == {999}


def test_A2_no_evidence_never_adds_a_second_owner(conn: sqlite3.Connection) -> None:
    """MUTATION TARGET (A2): the fallback keeps today's behaviour only for
    depots no other app maps."""
    upsert_mapping(conn, OLD_DEPOT, OLD_APP, None)
    change = apply_observed_mapping(
        conn, WANDERBURG, {OWN_DEPOT, OLD_DEPOT}, manifest_named=None, owned=None
    )
    assert mapped(conn, WANDERBURG) == {OWN_DEPOT}
    assert owners(conn, OLD_DEPOT) == {OLD_APP}
    assert change.ownership_unknown is True
    assert change.not_attributed == {OLD_DEPOT}
    assert "no ownership evidence" in change.summary()


def test_A3_evidence_still_adds_a_shared_depot(conn: sqlite3.Connection) -> None:
    upsert_mapping(conn, 228989, 228980, "Redist")
    change = apply_observed_mapping(
        conn, 440, {441, 228989}, manifest_named={441, 228989}, owned={441, 228989}
    )
    assert owners(conn, 228989) == {228980, 440}
    assert change.not_attributed == set()


def test_A4_removal_does_not_depend_on_the_filter(conn: sqlite3.Connection) -> None:
    """An unevidenced depot already mapped to the app and observed again is
    not removed by the filter, and the replace step removes the same rows
    whatever ``owned`` says."""
    results = []
    for owned in ({1}, set(), None):
        conn.execute("DELETE FROM depot_app_map")
        conn.commit()
        for depotid in (1, 2, 3, 4):
            upsert_mapping(conn, depotid, 440, None)
        change = apply_observed_mapping(conn, 440, {1, 2}, manifest_named={3}, owned=owned)
        results.append((change.removed, mapped(conn, 440)))
    assert all(r == ({4}, {1, 2, 3}) for r in results), results


def test_A5_owned_is_required(conn: sqlite3.Connection) -> None:
    with pytest.raises(TypeError):
        apply_observed_mapping(conn, 440, {1}, manifest_named=set())  # type: ignore[call-arg]


# --------------------------------------------------------------------------
# E: what counts as evidence
# --------------------------------------------------------------------------


def test_E1_owned_depots_unions_every_source(conn: sqlite3.Connection) -> None:
    recorded(conn, 440, 442, "1")
    recorded(conn, 10, 443, "2", containing=440)  # names 440 as containing app
    oracle_lists(conn, 440, [444])
    oracle_lists(conn, 450, [445])  # a content package of 440
    conn.execute(
        "INSERT INTO oracle_dlc_links (appid, dlc_appid, recorded_at, source) "
        "VALUES (440, 450, 'x', 'test')"
    )
    conn.commit()
    assert map_attrib.owned_depots(
        conn, 440, manifest_named={441}, oracle_enabled=True
    ) == {441, 442, 443, 444, 445}
    # Oracle off: its rows are not evidence (every oracle read checks the flag).
    assert map_attrib.owned_depots(
        conn, 440, manifest_named={441}, oracle_enabled=False
    ) == {441, 442, 443}


def test_E1_no_evidence_at_all_is_none(conn: sqlite3.Connection) -> None:
    assert map_attrib.owned_depots(conn, 440, manifest_named=None, oracle_enabled=True) is None
    assert map_attrib.owned_depots(conn, 440, manifest_named=set(), oracle_enabled=True) is None
    oracle_lists(conn, 440, [])
    assert map_attrib.oracle_depots(conn, 440, oracle_enabled=True) == set()
    assert map_attrib.owned_depots(conn, 440, manifest_named=set(), oracle_enabled=True) is None


# --------------------------------------------------------------------------
# A, end to end through the real worker
# --------------------------------------------------------------------------


def _wanderburg_stub(tmp_path: Path, cache_root: Path) -> tuple[str, Path]:
    """Wanderburg's run writes its own depot; meanwhile another PC loads
    436781 into the same cache. SteamPrefill's temp cache has a manifest
    file for Wanderburg's own depot only."""
    temp_cache = tmp_path / "steamprefill-cache"
    bins = [
        {
            "dir": str(temp_cache),
            "filename": f"{WANDERBURG}_{WANDERBURG}_{OWN_DEPOT}_{OWN_DEPOT}7.bin",
            "data": _bin_manifest_bytes(
                depot_id=OWN_DEPOT,
                manifest_id=int(f"{OWN_DEPOT}7"),
                files=[[(_cid(1), 160)]],
            ),
        }
    ]
    executable = stub_prefill.make_stub(
        tmp_path / "bin",
        cache_root=str(cache_root),
        depots_by_app={WANDERBURG: [OWN_DEPOT, OLD_DEPOT]},
        manifest_bins=bins,
    )
    return executable, temp_cache


def test_A1_worker_maps_only_the_apps_own_depot(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    executable, temp_cache = _wanderburg_stub(tmp_path, cache_root)
    temp_cache.mkdir(exist_ok=True)
    settings = make_settings(
        tmp_path, cache_root, executable, steamprefill_cache_dir=str(temp_cache)
    )
    with TestClient(create_app(settings)) as client:
        (job_id,) = enqueue(client, WANDERBURG)
        job = wait_for_job(client, job_id)
        assert job["status"] == "done", job["log_excerpt"]
        assert f"observed_not_attributed=1 [{OLD_DEPOT}]" in job["log_excerpt"]
    conn = get_connection(settings.db_path)
    try:
        assert mapped(conn, WANDERBURG) == {OWN_DEPOT}
        assert owners(conn, OLD_DEPOT) == set()
    finally:
        conn.close()


def test_A2_worker_without_evidence_leaves_the_other_owner_alone(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    executable = stub_prefill.make_stub(
        tmp_path / "bin", cache_root=str(cache_root),
        depots_by_app={WANDERBURG: [OWN_DEPOT, OLD_DEPOT]},
    )
    # make_settings: temp cache that never exists, oracle off -> no evidence.
    settings = make_settings(tmp_path, cache_root, executable)
    with TestClient(create_app(settings)) as client:
        client.put(f"/v1/mapping/{OLD_DEPOT}", json={"appid": OLD_APP},
                   headers={"X-Api-Key": settings.vault_api_key})
        (job_id,) = enqueue(client, WANDERBURG)
        job = wait_for_job(client, job_id)
        assert job["status"] == "done", job["log_excerpt"]
    conn = get_connection(settings.db_path)
    try:
        assert mapped(conn, WANDERBURG) == {OWN_DEPOT}
        assert owners(conn, OLD_DEPOT) == {OLD_APP}
    finally:
        conn.close()


# --------------------------------------------------------------------------
# C: the one-time cleanup
# --------------------------------------------------------------------------


@pytest.mark.parametrize("ubisoft_own_record", [True, False])
def test_C1_prod_shape_removes_exactly_the_four_foreign_pairs(
    tmp_path: Path, conn: sqlite3.Connection, ubisoft_own_record: bool
) -> None:
    """MUTATION TARGET (C1). ``ubisoft_own_record=False``: 1716750 is
    evidenced only as the containing app of Black Flag's manifest."""
    seed_prod_shape(conn, ubisoft_own_record=ubisoft_own_record)
    before = pairs(conn)
    result = map_attrib.cleanup_foreign_mappings(conn, cleanup_settings(tmp_path))
    assert set(result.removed) == FOREIGN
    assert result.restored == ()
    assert pairs(conn) == before - FOREIGN
    assert mapped(conn, WANDERBURG) == {OWN_DEPOT}
    assert owners(conn, UBI_DEPOT) == {UBISOFT, BLACK_FLAG}
    assert owners(conn, OLD_DEPOT) == {OLD_APP}
    assert owners(conn, 553851) == owners(conn, 553854) == {HD2}
    rows = conn.execute(
        "SELECT depotid, status FROM cache_completeness WHERE appid = ?", (WANDERBURG,)
    ).fetchall()
    assert [(r[0], r[1]) for r in rows] == [(OWN_DEPOT, "ok")]
    assert result.completeness_removed == 4
    assert result.stamped


def test_C1_full_prod_shape_exact_effect(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """MUTATION TARGET (C1/R). Exactly the reviewer's simulated prod effect:
    the four Wanderburg pairs and (1465471, 1203620) go, Enshrouded's 1203621
    and The Witcher 3's 292033/292036 are restored, no Witcher 3 package depot
    gains the game, every guarded shared row stays."""
    settings = cleanup_settings(tmp_path)
    seed_full_prod_shape(conn, tmp_path)
    before = pairs(conn)
    result = map_attrib.cleanup_foreign_mappings(conn, settings)
    assert set(result.removed) == REMOVED
    assert set(result.restored) == RESTORED
    assert result.skipped_no_oracle == ()
    assert pairs(conn) == (before - REMOVED) | RESTORED
    assert mapped(conn, ENSHROUDED) == {ENSHROUDED_DEPOT}
    assert owners(conn, ENSH_OTHER_DEPOT) == {ENSH_OTHER_APP}
    for package in HD2_PACKAGES:
        assert owners(conn, package) == {HD2, package}
    assert mapped(conn, WITCHER) == {292031, *WITCHER_LOST}
    for depotid, package in WITCHER_PACKAGES.items():
        assert owners(conn, depotid) == {package}
    assert owners(conn, REDIST_DEPOT) == {REDIST_APP, *REDIST_USERS}
    assert owners(conn, FACTORIO_DEPOT) == {FACTORIO, FACTORIO_PKG}
    assert mapped(conn, HD2) == set(HD2_DEPOTS) | set(HD2_PACKAGES)


def test_R1_containing_app_alone_never_restores(tmp_path: Path, conn: sqlite3.Connection) -> None:
    settings = cleanup_settings(tmp_path)
    recorded(conn, 10, 501, "1", containing=500)
    oracle_lists(conn, 500, [501], gids={501: "1"})  # gid matches: only R1 blocks
    (tmp_path / "cache" / "depot" / "501").mkdir()
    result = map_attrib.cleanup_foreign_mappings(conn, settings)
    assert result.restored == ()
    assert pairs(conn) == set()


@pytest.mark.parametrize("missing", ["oracle_row", "oracle_off", "not_listed", "depot_dir"])
def test_R2_restore_needs_record_oracle_and_depot_dir(
    tmp_path: Path, conn: sqlite3.Connection, missing: str
) -> None:
    settings = cleanup_settings(tmp_path, oracle_on=missing != "oracle_off")
    recorded(conn, ENSHROUDED, ENSHROUDED_DEPOT, ENSHROUDED_GID, containing=ENSHROUDED)
    if missing != "oracle_row":
        oracle_lists(
            conn, ENSHROUDED,
            [4951850] if missing == "not_listed" else [ENSHROUDED_DEPOT],
            gids={ENSHROUDED_DEPOT: ENSHROUDED_GID},
        )
    if missing != "depot_dir":
        (tmp_path / "cache" / "depot" / str(ENSHROUDED_DEPOT)).mkdir()
    result = map_attrib.cleanup_foreign_mappings(conn, settings)
    assert result.restored == ()
    assert pairs(conn) == set()


@pytest.mark.parametrize("containing", [ENSHROUDED, None])
def test_R2_control_all_conditions_restore(
    tmp_path: Path, conn: sqlite3.Connection, containing: int | None
) -> None:
    settings = cleanup_settings(tmp_path)
    recorded(conn, ENSHROUDED, ENSHROUDED_DEPOT, ENSHROUDED_GID, containing=containing)
    oracle_lists(conn, ENSHROUDED, [ENSHROUDED_DEPOT], gids={ENSHROUDED_DEPOT: ENSHROUDED_GID})
    (tmp_path / "cache" / "depot" / str(ENSHROUDED_DEPOT)).mkdir()
    result = map_attrib.cleanup_foreign_mappings(conn, settings)
    assert result.restored == ((ENSHROUDED_DEPOT, ENSHROUDED),)
    assert pairs(conn) == {(ENSHROUDED_DEPOT, ENSHROUDED)}


def test_R3_recorded_gid_older_than_public_is_not_restored(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    """MUTATION TARGET (R3). The oracle keep set leaves ``public`` out, so a
    restored depot whose only record is an old build would let GC plan the
    current chunks as orphans: skipped and left for the operator."""
    settings = cleanup_settings(tmp_path)
    recorded(conn, ENSHROUDED, ENSHROUDED_DEPOT, "1000", containing=ENSHROUDED)
    oracle_lists(conn, ENSHROUDED, [ENSHROUDED_DEPOT], gids={ENSHROUDED_DEPOT: ENSHROUDED_GID})
    (tmp_path / "cache" / "depot" / str(ENSHROUDED_DEPOT)).mkdir()
    result = map_attrib.cleanup_foreign_mappings(conn, settings)
    assert result.restored == ()
    assert result.restore_skipped_gid == ((ENSHROUDED_DEPOT, ENSHROUDED),)
    assert pairs(conn) == set()


def test_R3_a_beta_gid_match_does_not_count(tmp_path: Path, conn: sqlite3.Connection) -> None:
    settings = cleanup_settings(tmp_path)
    recorded(conn, ENSHROUDED, ENSHROUDED_DEPOT, "1000", containing=ENSHROUDED)
    oracle_lists(conn, ENSHROUDED, [ENSHROUDED_DEPOT], gids={ENSHROUDED_DEPOT: ENSHROUDED_GID})
    conn.execute(
        "INSERT INTO oracle_branch_manifests VALUES (?, ?, 'beta', '1000', 'x', 'test')",
        (ENSHROUDED, ENSHROUDED_DEPOT),
    )
    conn.commit()
    (tmp_path / "cache" / "depot" / str(ENSHROUDED_DEPOT)).mkdir()
    result = map_attrib.cleanup_foreign_mappings(conn, settings)
    assert result.restored == ()
    assert result.restore_skipped_gid == ((ENSHROUDED_DEPOT, ENSHROUDED),)


def test_R3_package_public_gid_counts(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """The public gid may sit in one of the app's content packages' answers."""
    settings = cleanup_settings(tmp_path)
    recorded(conn, WITCHER, 292033, "77", containing=WITCHER)
    oracle_lists(conn, WITCHER, [292031])
    oracle_lists(conn, 378649, [292033], gids={292033: "77"})
    conn.execute("INSERT INTO oracle_dlc_links VALUES (?, ?, 'x', 'test')", (WITCHER, 378649))
    conn.commit()
    (tmp_path / "cache" / "depot" / "292033").mkdir()
    result = map_attrib.cleanup_foreign_mappings(conn, settings)
    assert result.restored == ((292033, WITCHER),)


def test_R4_package_depot_is_never_restored_to_the_game(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    """MUTATION TARGET (R4). A content-package depot recorded under the game
    with the package as containing app stays mapped to the package only."""
    settings = cleanup_settings(tmp_path)
    upsert_mapping(conn, 310611, 378649, None)
    recorded(conn, WITCHER, 310611, "3106119", containing=378649)
    oracle_lists(conn, WITCHER, [310611])
    (tmp_path / "cache" / "depot" / "310611").mkdir()
    result = map_attrib.cleanup_foreign_mappings(conn, settings)
    assert result.restored == ()
    assert pairs(conn) == {(310611, 378649)}


def test_C2_only_mapping_or_unevidenced_co_owners_are_never_removed(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    oracle_lists(conn, WANDERBURG, [OWN_DEPOT])
    upsert_mapping(conn, OWN_DEPOT, WANDERBURG, None)
    # The only mapping of a depot, no evidence anywhere.
    upsert_mapping(conn, 777, WANDERBURG, None)
    # A co-owner without evidence does not make the row wrong.
    upsert_mapping(conn, 778, WANDERBURG, None)
    upsert_mapping(conn, 778, 9000, None)
    before = pairs(conn)
    result = map_attrib.cleanup_foreign_mappings(conn, cleanup_settings(tmp_path))
    assert result.removed == ()
    assert pairs(conn) == before


@pytest.mark.parametrize("oracle_on", [True, False])
def test_C3_no_oracle_data_skips_and_stamps(
    tmp_path: Path, conn: sqlite3.Connection, oracle_on: bool
) -> None:
    seed_prod_shape(conn)
    if oracle_on:
        conn.execute("DELETE FROM oracle_app_state WHERE appid = ?", (WANDERBURG,))
        conn.execute("DELETE FROM oracle_branch_manifests WHERE appid = ?", (WANDERBURG,))
        conn.commit()
    before = pairs(conn)
    result = map_attrib.cleanup_foreign_mappings(
        conn, cleanup_settings(tmp_path, oracle_on=oracle_on)
    )
    assert result.removed == ()
    assert set(result.skipped_no_oracle) == FOREIGN
    # Left for the operator, but stamped: the step never becomes a standing
    # startup rule that could later remove a manual PUT /v1/mapping.
    assert result.stamped
    assert pairs(conn) == before
    assert map_attrib.cleanup_foreign_mappings(conn, cleanup_settings(tmp_path)).already_done


def test_C4_unreadable_temp_cache_removes_nothing(tmp_path: Path, conn: sqlite3.Connection) -> None:
    seed_prod_shape(conn)
    before = pairs(conn)
    result = map_attrib.cleanup_foreign_mappings(
        conn, cleanup_settings(tmp_path, temp_cache=False)
    )
    assert result.cache_dir_unavailable
    assert pairs(conn) == before
    assert conn.execute("SELECT COUNT(*) FROM maintenance_steps").fetchone()[0] == 0


def test_C4_unresolvable_depot_root_changes_nothing(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    seed_prod_shape(conn)
    before = pairs(conn)
    result = map_attrib.cleanup_foreign_mappings(
        conn, cleanup_settings(tmp_path, depot_root=False)
    )
    assert result.depot_root_unavailable
    assert pairs(conn) == before
    assert conn.execute("SELECT COUNT(*) FROM maintenance_steps").fetchone()[0] == 0


def test_C4_own_manifest_file_is_evidence(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """A Wanderburg manifest file naming 436781 keeps the pair."""
    seed_prod_shape(conn)
    settings = cleanup_settings(tmp_path)
    Path(settings.steamprefill_cache_dir, f"{WANDERBURG}_{OLD_APP}_{OLD_DEPOT}_55.bin").write_bytes(b"x")
    result = map_attrib.cleanup_foreign_mappings(conn, settings)
    assert set(result.removed) == FOREIGN - {(OLD_DEPOT, WANDERBURG)}
    assert owners(conn, OLD_DEPOT) == {OLD_APP, WANDERBURG}


@pytest.mark.parametrize("relation", ["dlc_link", "oracle_parent", "store_parent", "sibling"])
def test_C5_related_package_mapping_is_kept(
    tmp_path: Path, conn: sqlite3.Connection, relation: str
) -> None:
    parent, package = 553850, 2506210
    depot = 2506211
    upsert_mapping(conn, depot, parent, None)
    upsert_mapping(conn, depot, package, None)
    recorded(conn, parent, depot, "1")
    oracle_lists(conn, package, [9])  # the package has oracle data, not naming the depot
    if relation == "dlc_link":
        conn.execute("INSERT INTO oracle_dlc_links VALUES (?, ?, 'x', 'test')", (parent, package))
    elif relation == "oracle_parent":
        conn.execute("UPDATE oracle_app_state SET parent_appid = ? WHERE appid = ?", (parent, package))
    elif relation == "store_parent":
        conn.execute(
            "INSERT INTO app_classification (appid, source, app_type, parent_appid, linked, "
            "name, checked_at) VALUES (?, 'store', 'dlc', ?, 0, NULL, 'x')",
            (package, parent),
        )
    else:
        # Another package of the same parent holds the evidence.
        conn.execute("DELETE FROM depot_manifests")
        conn.execute("DELETE FROM depot_app_map WHERE appid = ?", (parent,))
        upsert_mapping(conn, depot, 2506220, None)
        recorded(conn, 2506220, depot, "1")
        conn.execute("INSERT INTO oracle_dlc_links VALUES (?, ?, 'x', 'test')", (parent, package))
        conn.execute("INSERT INTO oracle_dlc_links VALUES (?, ?, 'x', 'test')", (parent, 2506220))
    conn.commit()
    before = pairs(conn)
    result = map_attrib.cleanup_foreign_mappings(conn, cleanup_settings(tmp_path))
    assert result.removed == ()
    assert pairs(conn) == before


def test_C5_unrelated_control_is_removed(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """Control for C5: the same rows without a relation are removed."""
    upsert_mapping(conn, 2506211, 553850, None)
    upsert_mapping(conn, 2506211, 2506210, None)
    recorded(conn, 553850, 2506211, "1")
    oracle_lists(conn, 2506210, [9])
    result = map_attrib.cleanup_foreign_mappings(conn, cleanup_settings(tmp_path))
    assert result.removed == ((2506211, 2506210),)


def test_C6_runs_once_and_is_idempotent(tmp_path: Path, conn: sqlite3.Connection) -> None:
    seed_prod_shape(conn)
    settings = cleanup_settings(tmp_path)
    map_attrib.cleanup_foreign_mappings(conn, settings)
    after_first = pairs(conn)
    assert map_attrib.cleanup_foreign_mappings(conn, settings).already_done
    conn.execute("DELETE FROM maintenance_steps")
    conn.commit()
    again = map_attrib.cleanup_foreign_mappings(conn, settings)
    assert again.removed == ()
    assert pairs(conn) == after_first


def test_C6_startup_runs_the_cleanup(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    temp_cache = tmp_path / "steamprefill-cache"
    temp_cache.mkdir()
    (cache_root / "depot").mkdir(parents=True)
    settings = dataclasses.replace(
        make_settings(tmp_path, cache_root, "", steamprefill_cache_dir=str(temp_cache)),
        manifest_oracle=MANIFEST_ORACLE_STEAMCMD_API,
    )
    init_db(settings.db_path)
    conn = get_connection(settings.db_path)
    try:
        seed_prod_shape(conn)
    finally:
        conn.close()
    with TestClient(create_app(settings)):
        pass
    conn = get_connection(settings.db_path)
    try:
        assert mapped(conn, WANDERBURG) == {OWN_DEPOT}
        assert conn.execute(
            "SELECT 1 FROM maintenance_steps WHERE name = ?", (map_attrib.CLEANUP_STEP_NAME,)
        ).fetchone() is not None
    finally:
        conn.close()


# --------------------------------------------------------------------------
# G: GC safety after the cleanup
# --------------------------------------------------------------------------


def _gc_disk(tmp_path: Path) -> tuple[Path, Path, dict[str, int], dict[str, int], dict[str, int]]:
    """1716751 holds Ubisoft's build (chunks 0-2), Black Flag's build
    (chunks 2-4, a different manifest) and one old leftover (chunk 9).
    The other depots hold their owner's current build plus one leftover."""
    cache = tmp_path / "cache"
    (cache / "depot").mkdir(parents=True)
    archive = tmp_path / "manifests"
    ubi = {_cid(i): 160 for i in (0, 1, 2)}
    flag = {_cid(i): 160 for i in (2, 3, 4)}
    leftover = {_cid(9): 160}
    write_chunks(cache, UBI_DEPOT, {**ubi, **flag, **leftover})
    write_archive_bin(archive, depotid=UBI_DEPOT, manifestid="3001", chunks=ubi)
    write_archive_bin(archive, depotid=UBI_DEPOT, manifestid="3002", chunks=flag)
    for depotid, gid in ((OLD_DEPOT, "2001"), (553851, "5538511"), (553854, "5538541"), (OWN_DEPOT, "1001")):
        current = {_cid(depotid % 1000 + 20): 160}
        write_chunks(cache, depotid, {**current, _cid(depotid % 1000 + 40): 160})
        write_archive_bin(archive, depotid=depotid, manifestid=gid, chunks=current)
    return cache, archive, ubi, flag, leftover


def _chunks_on_disk(cache: Path, depotid: int) -> set[str]:
    return {p.name for p in (cache / "depot" / str(depotid) / "chunk").iterdir()}


@pytest.mark.parametrize("planner", [UBISOFT, BLACK_FLAG])
def test_G1_multi_owner_keep_set_keeps_every_owners_manifest(
    tmp_path: Path, conn: sqlite3.Connection, planner: int
) -> None:
    """MUTATION TARGET (G1), executed for real after the cleanup."""
    cache, archive, ubi, flag, leftover = _gc_disk(tmp_path)
    seed_prod_shape(conn)
    map_attrib.cleanup_foreign_mappings(conn, cleanup_settings(tmp_path))
    assert owners(conn, UBI_DEPOT) == {UBISOFT, BLACK_FLAG}

    report = gc_execute.run_gc(
        conn, planner, cache_root=str(cache), archive_dir=str(archive), execute=True
    )
    (result,) = [d for d in report.depots if d.depotid == UBI_DEPOT]
    assert result.status == "planned", report
    assert _chunks_on_disk(cache, UBI_DEPOT) == set(ubi) | set(flag)
    assert result.removed_count == len(leftover)


def test_G2_removed_row_never_turns_a_depot_into_orphans(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    """MUTATION TARGET (G2). After the cleanup GC for Wanderburg plans only
    its own depot and touches no foreign depot; GC of each true owner still
    plans its depot and keeps the current build."""
    cache, archive, *_ = _gc_disk(tmp_path)
    seed_prod_shape(conn)
    snapshot = {d: _chunks_on_disk(cache, d) for d in (OLD_DEPOT, *HD2_DEPOTS, UBI_DEPOT)}
    map_attrib.cleanup_foreign_mappings(conn, cleanup_settings(tmp_path))

    report = gc_execute.run_gc(
        conn, WANDERBURG, cache_root=str(cache), archive_dir=str(archive), execute=True
    )
    assert {d.depotid for d in report.depots} == {OWN_DEPOT}
    for depotid, chunk_names in snapshot.items():
        assert _chunks_on_disk(cache, depotid) == chunk_names

    for owner, depots in ((OLD_APP, (OLD_DEPOT,)), (HD2, HD2_DEPOTS)):
        report = gc_execute.run_gc(
            conn, owner, cache_root=str(cache), archive_dir=str(archive), execute=True
        )
        assert {d.depotid for d in report.depots} == set(depots)
        for result in report.depots:
            depotid = result.depotid
            assert result.status == "planned", report
            assert _chunks_on_disk(cache, depotid) == {_cid(depotid % 1000 + 20)}
            assert result.removed_count == 1  # only the leftover


def test_G3_gc_after_restore_keeps_the_recorded_build(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    """MUTATION TARGET (G3). Enshrouded after the restore: GC for the app
    plans its own depot, keeps the recorded manifest (= the oracle's public
    gid) and removes only the leftover chunk."""
    settings = cleanup_settings(tmp_path)
    seed_full_prod_shape(conn, tmp_path)
    cache, archive = tmp_path / "cache", tmp_path / "manifests"
    current = {_cid(i): 160 for i in (0, 1, 2)}
    write_chunks(cache, ENSHROUDED_DEPOT, {**current, _cid(9): 160})
    write_archive_bin(archive, depotid=ENSHROUDED_DEPOT, manifestid=ENSHROUDED_GID, chunks=current)
    other = {_cid(5): 160}
    write_chunks(cache, ENSH_OTHER_DEPOT, other)
    write_archive_bin(archive, depotid=ENSH_OTHER_DEPOT, manifestid="14654711", chunks=other)
    map_attrib.cleanup_foreign_mappings(conn, settings)

    report = gc_execute.run_gc(
        conn, ENSHROUDED, cache_root=str(cache), archive_dir=str(archive), execute=True,
        oracle_manifests={ENSHROUDED_DEPOT: [ENSHROUDED_GID]},
    )
    assert {d.depotid for d in report.depots} == {ENSHROUDED_DEPOT}
    (result,) = report.depots
    assert result.status == "planned", report
    assert result.removed_count == 1
    assert _chunks_on_disk(cache, ENSHROUDED_DEPOT) == set(current)
    assert _chunks_on_disk(cache, ENSH_OTHER_DEPOT) == set(other)
