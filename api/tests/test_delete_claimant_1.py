"""WP DELETE-CLAIMANT-1: a recorded manifest makes its app a co-owner of the
depot for ``DELETE /v1/cache/{appid}``, mapped or not.

The prod shape (HELLDIVERS 2 until its mapping heals): a content package's
depot is mapped only to the package app, while the game's prefill recorded
its manifest (``depot_manifests``) under the game. Judged from
``depot_app_map`` alone, the depot looked exclusive to the package, and
deleting the package removed a directory the game still uses. Mirrors the GC
claimant rule of WP GROUP-VERIFIED-1. Real directories, real database, no
network; ids are synthetic.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from vault_api import deletion, depot_manifests, scheduler
from vault_api.config import Settings
from vault_api.main import create_app

AUTH = {"X-Api-Key": TEST_API_KEY}

GAME = 553850
PKG = 2506210
D_GAME = 553851  # mapped to the game
D_PKG = 2506211  # mapped ONLY to the package; the game recorded its manifest


def _make(tmp_path: Path) -> tuple[TestClient, Path, Settings]:
    root = tmp_path / "cache"
    (root / "depot").mkdir(parents=True)
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(root),
        log_level="INFO",
    )
    return TestClient(create_app(settings)), root, settings


def _depot(root: Path, depotid: int, size: int = 100) -> None:
    chunk = root / "depot" / str(depotid) / "chunk" / "aa"
    chunk.parent.mkdir(parents=True, exist_ok=True)
    chunk.write_bytes(b"1" * size)


def _map(client: TestClient, depotid: int, appid: int) -> None:
    response = client.put(
        f"/v1/mapping/{depotid}", json={"appid": appid, "app_name": None}, headers=AUTH
    )
    assert response.status_code == 200, response.text


def _db(settings: Settings) -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _sql(settings: Settings, statement: str, params: tuple = ()) -> None:
    conn = _db(settings)
    try:
        conn.execute(statement, params)
        conn.commit()
    finally:
        conn.close()


def _cached(settings: Settings, appid: int) -> None:
    _sql(
        settings,
        "UPDATE apps SET status = 'done', last_prefill_at = '2026-10-01T00:00:00Z', "
        "needs_force = 0 WHERE appid = ?",
        (appid,),
    )


def _record(settings: Settings, appid: object, depotid: int) -> None:
    conn = _db(settings)
    try:
        depot_manifests.upsert_depot_manifest(
            conn, appid=appid, containing_appid=PKG, depotid=depotid,  # type: ignore[arg-type]
            manifestid="2000", chunk_count=1, total_bytes=100,
            recorded_at="2026-10-18T14:30:00Z", source="test",
        )
        conn.commit()
    finally:
        conn.close()


def _needs_force(settings: Settings, appid: int) -> int:
    conn = _db(settings)
    try:
        return conn.execute(
            "SELECT needs_force FROM apps WHERE appid = ?", (appid,)
        ).fetchone()["needs_force"]
    finally:
        conn.close()


@pytest.fixture
def lost_mapping(tmp_path: Path):
    """The game (cached) maps its own depot; the package (cached) alone maps
    its depot; the game's record names the package depot."""
    client, root, settings = _make(tmp_path)
    _depot(root, D_GAME)
    _depot(root, D_PKG)
    _map(client, D_GAME, GAME)
    _map(client, D_PKG, PKG)
    _cached(settings, GAME)
    _cached(settings, PKG)
    _record(settings, GAME, D_PKG)
    return client, root, settings


# --------------------------------------------------------------------------
# The fix, end to end
# --------------------------------------------------------------------------


def test_delete_package_keeps_depot_the_game_recorded_but_lost_its_mapping(
    lost_mapping, caplog: pytest.LogCaptureFixture
) -> None:
    """MUTATION TARGET. Fails on main before the fix: the package depot was
    planned exclusive and its whole directory removed."""
    client, root, _settings = lost_mapping
    caplog.set_level(logging.INFO, logger="vault_api")
    response = client.delete(f"/v1/cache/{PKG}", headers=AUTH)
    assert response.status_code == 200, response.text
    body = response.json()

    assert (root / "depot" / str(D_PKG) / "chunk" / "aa").exists()
    assert body["deleted_depots"] == []
    assert body["skipped_shared"] == [
        {
            "depotid": D_PKG,
            "shared_with": [GAME],
            "reason": deletion.KEEP_REASON_RECORDED_MANIFEST,
            "recorded_only": [GAME],
        }
    ]
    assert body["total_bytes_freed"] == 0

    # Plan-stage wiring, independent of the execute-time recheck: the PLAN
    # already kept the depot (log_kept_depots), it was not only rescued late.
    # Mutant "claimants=[] in the route" fails here.
    kept = [r.getMessage() for r in caplog.records if "KEPT" in r.getMessage()]
    assert any(
        f"depot={D_PKG} KEPT ({deletion.KEEP_REASON_RECORDED_MANIFEST})" in m
        for m in kept
    ), kept
    assert not any("late recheck" in m for m in kept), kept


def test_deleted_package_kept_for_the_recording_game_is_not_requeued(
    lost_mapping,
) -> None:
    """Review S1 (B1 class): after the package is deleted and its depot kept
    for the recording game, the package must not count as cached (sweep and
    POST /v1/prefill/cached) through that depot; the game still does."""
    client, root, settings = lost_mapping
    assert client.delete(f"/v1/cache/{PKG}", headers=AUTH).status_code == 200
    assert (root / "depot" / str(D_PKG)).is_dir()

    conn = _db(settings)
    try:
        assert scheduler.cached_appids(conn, str(root)) == {GAME}
    finally:
        conn.close()

    response = client.post("/v1/prefill/cached", headers=AUTH)
    assert response.status_code == 202, response.text
    assert [entry["appid"] for entry in response.json()] == [GAME]


def test_dry_run_shows_the_kept_depot_with_its_reason_and_matches_execute(
    lost_mapping,
) -> None:
    client, root, _settings = lost_mapping
    plan = client.get(f"/v1/cache/{PKG}/delete-plan", headers=AUTH)
    assert plan.status_code == 200, plan.text
    planned = plan.json()
    assert planned["delete_depots"] == []
    assert planned["total_bytes"] == 0
    assert planned["skipped_shared"] == [
        {
            "depotid": D_PKG,
            "shared_with": [GAME],
            "reason": deletion.KEEP_REASON_RECORDED_MANIFEST,
            "recorded_only": [GAME],
        }
    ]

    executed = client.delete(f"/v1/cache/{PKG}", headers=AUTH).json()
    assert executed["skipped_shared"] == planned["skipped_shared"]
    assert [d["depotid"] for d in executed["deleted_depots"]] == [
        d["depotid"] for d in planned["delete_depots"]
    ]
    assert (root / "depot" / str(D_PKG)).is_dir()


def test_unreadable_record_app_id_keeps_the_depot(tmp_path: Path) -> None:
    """A record whose app id cannot be read is a claim by an unknown app:
    the depot is shared (kept), in the dry run and in the deletion."""
    client, root, settings = _make(tmp_path)
    _depot(root, D_PKG)
    _map(client, D_PKG, PKG)
    _cached(settings, PKG)
    _record(settings, "not-an-id", D_PKG)

    planned = client.get(f"/v1/cache/{PKG}/delete-plan", headers=AUTH).json()
    expected = [
        {
            "depotid": D_PKG,
            "shared_with": [],
            "reason": deletion.KEEP_REASON_UNREADABLE_OWNER,
            "recorded_only": [],
        }
    ]
    assert planned["delete_depots"] == []
    assert planned["skipped_shared"] == expected

    executed = client.delete(f"/v1/cache/{PKG}", headers=AUTH).json()
    assert executed["deleted_depots"] == []
    assert executed["skipped_shared"] == expected
    assert (root / "depot" / str(D_PKG) / "chunk" / "aa").exists()


def test_uncached_recorder_still_lets_the_last_remnant_go(tmp_path: Path) -> None:
    """The all-referrers-gone escape (LEARNINGS, WP 3.6): a recorder that is
    verifiably uncached (idle, never prefilled, no job) does not protect the
    depot forever; it goes as a last remnant and the recorder is flagged."""
    client, root, settings = _make(tmp_path)
    _depot(root, D_GAME)
    _depot(root, D_PKG)
    _map(client, D_GAME, GAME)
    _map(client, D_PKG, PKG)
    _cached(settings, PKG)
    _sql(settings, "UPDATE apps SET needs_force = 0 WHERE appid = ?", (GAME,))
    _record(settings, GAME, D_PKG)

    planned = client.get(f"/v1/cache/{PKG}/delete-plan", headers=AUTH).json()
    assert planned["skipped_shared"] == []
    assert [(d["depotid"], d["shared_with_uncached"]) for d in planned["delete_depots"]] == [
        (D_PKG, [GAME])
    ]

    executed = client.delete(f"/v1/cache/{PKG}", headers=AUTH).json()
    assert executed["skipped_shared"] == []
    assert [(d["depotid"], d["shared_with_uncached"]) for d in executed["deleted_depots"]] == [
        (D_PKG, [GAME])
    ]
    assert not (root / "depot" / str(D_PKG)).exists()
    assert _needs_force(settings, GAME) == 1


def test_grouped_delete_members_do_not_protect_each_other_through_records(
    tmp_path: Path,
) -> None:
    """A delete set's own records never protect its own depots (ADR-0022 §4):
    the game's record of the package depot does not keep it when the game and
    the package are deleted together."""
    plan = deletion.plan_deletion(
        [(D_GAME, GAME), (D_PKG, PKG)],
        deletion.DeleteSet(GAME, frozenset({GAME, PKG})),
        {},
        claimants=[(D_PKG, GAME)],
    )
    assert plan.exclusive == [D_GAME, D_PKG]
    assert plan.shared == []


# --------------------------------------------------------------------------
# The execute-time recheck reads records too
# --------------------------------------------------------------------------


def test_recheck_keeps_a_depot_recorded_after_the_plan(lost_mapping) -> None:
    """The plan said exclusive (no record yet); a record written before
    removal must still protect the depot (TOCTOU close, WP 1.6 rule)."""
    _client, root, settings = lost_mapping
    conn = _db(settings)
    try:
        conn.execute("DELETE FROM depot_manifests")
        conn.commit()
        plan = deletion.plan_deletion(
            deletion.load_mapping_rows(conn, PKG), PKG, {},
            claimants=deletion.load_manifest_claimants(conn, PKG),
        )
        assert plan.exclusive == [D_PKG]
    finally:
        conn.close()
    _record(settings, GAME, D_PKG)

    depot_root = deletion.resolve_depot_root(str(root))
    conn = _db(settings)
    try:
        assert deletion.load_co_owners(conn, D_PKG, PKG) == [
            deletion.CoOwner(appid=GAME, has_content=True, via_record=True)
        ]
        deleted, failed, late = deletion.delete_app_depots(
            depot_root, PKG, plan.exclusive,
            co_owners=lambda depotid: deletion.load_co_owners(conn, depotid, PKG),
        )
    finally:
        conn.close()
    assert deleted == [] and failed == []
    assert late == [
        deletion.SharedDepot(
            depotid=D_PKG, shared_with=[GAME],
            reason=deletion.KEEP_REASON_RECORDED_MANIFEST, recorded_only=[GAME],
        )
    ]
    assert (root / "depot" / str(D_PKG) / "chunk" / "aa").exists()


def test_recheck_unreadable_record_protects(lost_mapping) -> None:
    _client, root, settings = lost_mapping
    conn = _db(settings)
    try:
        conn.execute("DELETE FROM depot_manifests")
        conn.execute(
            "INSERT INTO depot_manifests (appid, containing_appid, depotid, manifestid, "
            "chunk_count, total_bytes, recorded_at, source, first_seen_at, "
            "manifest_changed_at) VALUES ('x', NULL, ?, '1', 1, 1, 't', 'test', 't', 't')",
            (D_PKG,),
        )
        conn.commit()
        assert deletion.load_co_owners(conn, D_PKG, PKG) == [
            deletion.CoOwner(appid=0, has_content=True, via_record=False)
        ]
        _deleted, _failed, late = deletion.delete_app_depots(
            deletion.resolve_depot_root(str(root)), PKG, [D_PKG],
            co_owners=lambda depotid: deletion.load_co_owners(conn, depotid, PKG),
        )
    finally:
        conn.close()
    assert [(s.depotid, s.reason) for s in late] == [
        (D_PKG, deletion.KEEP_REASON_UNREADABLE_OWNER)
    ]
    assert (root / "depot" / str(D_PKG)).is_dir()


# --------------------------------------------------------------------------
# plan_deletion, pure
# --------------------------------------------------------------------------


def test_plan_reason_prefers_a_mapped_co_owner_with_content() -> None:
    plan = deletion.plan_deletion(
        [(D_PKG, PKG), (D_PKG, 730)], PKG, {730: True, GAME: True},
        claimants=[(D_PKG, GAME)],
    )
    assert plan.shared == [
        deletion.SharedDepot(
            depotid=D_PKG, shared_with=[730, GAME],
            reason=deletion.KEEP_REASON_CO_OWNER_CONTENT, recorded_only=[GAME],
        )
    ]


def test_plan_recorder_without_apps_row_protects() -> None:
    """Missing from co_owner_states (no apps row) => has content, same
    default as a mapped co-owner."""
    plan = deletion.plan_deletion([(D_PKG, PKG)], PKG, {}, claimants=[(D_PKG, GAME)])
    assert plan.exclusive == []
    assert [(s.depotid, s.reason) for s in plan.shared] == [
        (D_PKG, deletion.KEEP_REASON_RECORDED_MANIFEST)
    ]


def test_plan_claimants_never_add_depots_and_bad_depot_ids_are_ignored() -> None:
    plan = deletion.plan_deletion(
        [(D_PKG, PKG)], PKG, {GAME: True},
        claimants=[(D_GAME, GAME), ("../..", GAME), (None, None)],
    )
    assert plan.exclusive == [D_PKG]
    assert plan.shared == [] and plan.remnant == [] and plan.unusable == []


def test_plan_without_claimants_is_unchanged() -> None:
    plan = deletion.plan_deletion([(D_PKG, PKG)], PKG, {GAME: True})
    assert plan.exclusive == [D_PKG]
