"""WP TOOL-APPS-2b (ADR-0022 §4): grouped delete of a game with its packages.

``DELETE /v1/cache/{appid}?with_packages=true`` and its preview ``GET
/v1/cache/{appid}/delete-plan``. Real directories on disk, real database, no
network. Fixture shape follows the prod observation of 2026-10-13: the HD2
packages (2506210..) have their own mapped depots.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from vault_api import deletion, jobs
from vault_api.config import Settings
from vault_api.main import create_app

AUTH = {"X-Api-Key": TEST_API_KEY}

HD2 = 553850
PKG_A = 2506210  # dlc, own depot + a depot shared with HD2
PKG_B = 2506220  # dlc, its depot is shared with an outside cached game
OUTSIDE = 730  # an unrelated cached game
GAME_CHILD = 4000001  # grouped under HD2 but its own type is "game"
LINKED = 4000002  # only HD2's DLC list names it (inferred dlc)
TOOL = 228980  # fixed tool list

D_HD2_OWN = 553851
D_HD2_PKG_A = 553852
D_PKG_A_OWN = 2506211
D_PKG_B_SHARED = 2506221
D_HD2_GAME_CHILD = 553853
D_LINKED_OWN = 4000003
D_TOOL_OWN = 228982


def _make(tmp_path: Path, *, oracle: bool = False) -> tuple[TestClient, Path, Settings]:
    root = tmp_path / "cache"
    (root / "depot").mkdir(parents=True)
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(root),
        log_level="INFO",
        manifest_oracle="steamcmd_api" if oracle else "",
    )
    return TestClient(create_app(settings)), root, settings


def _depot(root: Path, depotid: int, size: int = 100) -> None:
    chunk = root / "depot" / str(depotid) / "chunk" / "aa"
    chunk.parent.mkdir(parents=True, exist_ok=True)
    chunk.write_bytes(b"1" * size)


def _map(client: TestClient, depotid: int, appid: int) -> None:
    response = client.put(f"/v1/mapping/{depotid}", json={"appid": appid, "app_name": None}, headers=AUTH)
    assert response.status_code == 200, response.text


def _sql(settings: Settings, statement: str, params: tuple = ()) -> None:
    conn = sqlite3.connect(settings.db_path)
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


def _classify(settings: Settings, appid: int, app_type: str | None, parent: int | None,
              source: str = "store", linked: int = 0) -> None:
    _sql(
        settings,
        "INSERT INTO app_classification (appid, source, app_type, parent_appid, linked, "
        "name, checked_at) VALUES (?, ?, ?, ?, ?, NULL, 'x')",
        (appid, source, app_type, parent, linked),
    )


def _app(settings: Settings, appid: int) -> sqlite3.Row:
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT status, last_prefill_at, needs_force FROM apps WHERE appid = ?", (appid,)
        ).fetchone()
    finally:
        conn.close()


@pytest.fixture
def hd2(tmp_path: Path):
    """HD2 with two packages, a game-typed child, a linked-only child, a tool
    app claiming HD2 as parent, and an outside cached game."""
    client, root, settings = _make(tmp_path)
    for depotid, owners in {
        D_HD2_OWN: [HD2],
        D_HD2_PKG_A: [HD2, PKG_A],
        D_PKG_A_OWN: [PKG_A],
        D_PKG_B_SHARED: [PKG_B, OUTSIDE],
        D_HD2_GAME_CHILD: [HD2, GAME_CHILD],
        D_LINKED_OWN: [LINKED],
        D_TOOL_OWN: [TOOL],
    }.items():
        _depot(root, depotid)
        for owner in owners:
            _map(client, depotid, owner)
    for appid in (HD2, PKG_A, PKG_B, OUTSIDE, GAME_CHILD, LINKED):
        _cached(settings, appid)
    _classify(settings, HD2, "game", None)
    _classify(settings, PKG_A, "dlc", HD2)
    _classify(settings, PKG_B, "dlc", HD2)
    _classify(settings, GAME_CHILD, "game", HD2)
    _classify(settings, LINKED, None, HD2, source="store")  # parent known, own type unknown
    _classify(settings, TOOL, "dlc", HD2)  # stored, but the fixed list wins
    return client, root, settings


def _on_disk(root: Path, depotid: int) -> bool:
    return (root / "depot" / str(depotid)).exists()


# ==========================================================================
# 1. Without the parameter nothing changes
# ==========================================================================


def test_plain_delete_is_unchanged_and_has_no_group_fields(hd2) -> None:
    client, root, settings = hd2
    body = client.delete(f"/v1/cache/{HD2}", headers=AUTH).json()
    assert set(body) == {"appid", "deleted_depots", "skipped_shared", "failed", "total_bytes_freed"}
    assert [d["depotid"] for d in body["deleted_depots"]] == [D_HD2_OWN]
    assert {s["depotid"]: s["shared_with"] for s in body["skipped_shared"]} == {
        D_HD2_PKG_A: [PKG_A],
        D_HD2_GAME_CHILD: [GAME_CHILD],
    }
    assert _on_disk(root, D_PKG_A_OWN) and _on_disk(root, D_HD2_PKG_A)
    assert _app(settings, PKG_A)["status"] == "done"


def test_with_packages_false_equals_plain(hd2) -> None:
    client, _root, _settings = hd2
    body = client.delete(f"/v1/cache/{HD2}?with_packages=false", headers=AUTH).json()
    assert set(body) == {"appid", "deleted_depots", "skipped_shared", "failed", "total_bytes_freed"}
    assert [d["depotid"] for d in body["deleted_depots"]] == [D_HD2_OWN]


@pytest.mark.parametrize("value", ["1", "yes", "True", "on", ""])
def test_with_packages_accepts_only_literal_true_or_false(hd2, value: str) -> None:
    client, root, _settings = hd2
    response = client.delete(f"/v1/cache/{HD2}?with_packages={value}", headers=AUTH)
    assert response.status_code == 422
    assert _on_disk(root, D_HD2_OWN)
    assert client.get(f"/v1/cache/{HD2}/delete-plan?with_packages={value}", headers=AUTH).status_code == 422


# ==========================================================================
# 2. The grouped delete
# ==========================================================================


def test_grouped_delete_removes_packages_and_keeps_protected_depots(hd2) -> None:
    client, root, settings = hd2
    response = client.delete(f"/v1/cache/{HD2}?with_packages=true", headers=AUTH)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["with_packages"] is True
    assert [(m["appid"], m["role"]) for m in body["members"]] == [
        (HD2, "parent"), (PKG_A, "package"), (PKG_B, "package"),
    ]
    deleted = {d["depotid"] for d in body["deleted_depots"]}
    # Members do not protect each other: the HD2/PKG_A shared depot goes.
    assert deleted == {D_HD2_OWN, D_HD2_PKG_A, D_PKG_A_OWN}
    assert {s["depotid"]: s["shared_with"] for s in body["skipped_shared"]} == {
        D_PKG_B_SHARED: [OUTSIDE],  # an outside cached game keeps its depot
        D_HD2_GAME_CHILD: [GAME_CHILD],  # a game-typed child is an outside owner
    }
    members = {m["appid"]: m for m in body["members"]}
    assert members[PKG_A]["deleted_depots"] == [D_HD2_PKG_A, D_PKG_A_OWN]
    assert members[PKG_B]["deleted_depots"] == []
    assert {(c["appid"], c["reason"]) for c in body["kept_children"]} == {
        (GAME_CHILD, "not_a_package_type"),
        (LINKED, "type_unknown"),
    }
    assert body["total_bytes_freed"] == 300

    for depotid in (D_PKG_B_SHARED, D_HD2_GAME_CHILD, D_LINKED_OWN, D_TOOL_OWN):
        assert _on_disk(root, depotid)
    # Every member that lost depots is reset like a single deletion.
    for appid in (HD2, PKG_A):
        row = _app(settings, appid)
        assert (row["status"], row["last_prefill_at"], row["needs_force"]) == ("idle", None, 1)
    # A package none of whose depots was touched is left alone; so are outsiders.
    for appid in (PKG_B, GAME_CHILD, OUTSIDE):
        assert _app(settings, appid)["status"] == "done"


def test_a_wrongly_classified_game_keeps_its_depots(tmp_path: Path) -> None:
    """ADR pin: a child whose own type is "game" is never deleted with the
    parent, and its exclusive depot stays."""
    client, root, settings = _make(tmp_path)
    for depotid, owner in ((1001, 1000), (2001, 2000)):
        _depot(root, depotid)
        _map(client, depotid, owner)
    _cached(settings, 2000)
    _classify(settings, 2000, "game", 1000)
    body = client.delete("/v1/cache/1000?with_packages=true", headers=AUTH).json()
    assert [m["appid"] for m in body["members"]] == [1000]
    assert [d["depotid"] for d in body["deleted_depots"]] == [1001]
    assert _on_disk(root, 2001)


def test_linked_only_dlc_is_not_deletable(tmp_path: Path) -> None:
    """2a review rule: a dlc inferred only from the parent's DLC list is not
    the app's own type."""
    client, root, settings = _make(tmp_path, oracle=True)
    for depotid, owner in ((1001, 1000), (2001, 2000)):
        _depot(root, depotid)
        _map(client, depotid, owner)
    _classify(settings, 2000, None, 1000, source="steamcmd_api", linked=1)
    plan = client.get("/v1/cache/1000/delete-plan?with_packages=true", headers=AUTH).json()
    assert [m["appid"] for m in plan["members"]] == [1000]
    assert plan["kept_children"] == [
        {"appid": 2000, "name": None, "app_type": "dlc", "reason": "type_only_inferred"}
    ]
    client.delete("/v1/cache/1000?with_packages=true", headers=AUTH)
    assert _on_disk(root, 2001)


def test_own_pics_type_from_the_oracle_makes_a_package(tmp_path: Path) -> None:
    client, root, settings = _make(tmp_path, oracle=True)
    for depotid, owner in ((1001, 1000), (2001, 2000)):
        _depot(root, depotid)
        _map(client, depotid, owner)
    _classify(settings, 2000, "dlc", 1000, source="steamcmd_api", linked=1)
    body = client.delete("/v1/cache/1000?with_packages=true", headers=AUTH).json()
    assert [m["appid"] for m in body["members"]] == [1000, 2000]
    assert not _on_disk(root, 2001)


def test_oracle_rows_do_not_count_while_the_oracle_is_off(tmp_path: Path) -> None:
    client, root, settings = _make(tmp_path, oracle=False)
    for depotid, owner in ((1001, 1000), (2001, 2000)):
        _depot(root, depotid)
        _map(client, depotid, owner)
    _classify(settings, 2000, "dlc", 1000, source="steamcmd_api")
    body = client.delete("/v1/cache/1000?with_packages=true", headers=AUTH).json()
    assert [m["appid"] for m in body["members"]] == [1000]
    assert _on_disk(root, 2001)


@pytest.mark.parametrize("package_type", ["dlc", "config", "music", "video", "demo"])
def test_every_package_type_is_deletable(tmp_path: Path, package_type: str) -> None:
    client, root, settings = _make(tmp_path)
    for depotid, owner in ((1001, 1000), (2001, 2000)):
        _depot(root, depotid)
        _map(client, depotid, owner)
    _classify(settings, 2000, package_type, 1000)
    client.delete("/v1/cache/1000?with_packages=true", headers=AUTH)
    assert not _on_disk(root, 2001)


@pytest.mark.parametrize("other_type", ["game", "application", "tool", None])
def test_no_other_type_is_deletable(tmp_path: Path, other_type: str | None) -> None:
    client, root, settings = _make(tmp_path)
    for depotid, owner in ((1001, 1000), (2001, 2000)):
        _depot(root, depotid)
        _map(client, depotid, owner)
    _classify(settings, 2000, other_type, 1000)
    client.delete("/v1/cache/1000?with_packages=true", headers=AUTH)
    assert _on_disk(root, 2001)


def test_a_fixed_list_tool_app_never_joins_the_delete(hd2) -> None:
    client, root, _settings = hd2
    body = client.delete(f"/v1/cache/{HD2}?with_packages=true", headers=AUTH).json()
    assert TOOL not in {m["appid"] for m in body["members"]}
    assert TOOL not in {c["appid"] for c in body["kept_children"]}
    assert _on_disk(root, D_TOOL_OWN)


def test_a_tool_app_as_the_parent_deletes_only_itself(tmp_path: Path) -> None:
    client, root, settings = _make(tmp_path)
    for depotid, owner in ((D_TOOL_OWN, TOOL), (2001, 2000)):
        _depot(root, depotid)
        _map(client, depotid, owner)
    _classify(settings, 2000, "dlc", TOOL)
    plan = client.get(f"/v1/cache/{TOOL}/delete-plan?with_packages=true", headers=AUTH).json()
    assert [m["appid"] for m in plan["members"]] == [TOOL]
    client.delete(f"/v1/cache/{TOOL}?with_packages=true", headers=AUTH)
    assert _on_disk(root, 2001)


def test_409_when_any_member_has_an_active_job(hd2) -> None:
    client, root, settings = hd2
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    try:
        _job, created = jobs.enqueue_prefill(conn, PKG_A)
        conn.commit()
    finally:
        conn.close()
    assert created
    response = client.delete(f"/v1/cache/{HD2}?with_packages=true", headers=AUTH)
    assert response.status_code == 409
    assert f"for app {PKG_A} is queued" in response.json()["detail"]
    assert _on_disk(root, D_HD2_OWN) and _on_disk(root, D_PKG_A_OWN)
    plan = client.get(f"/v1/cache/{HD2}/delete-plan?with_packages=true", headers=AUTH).json()
    assert [(b["appid"], b["status"], b["type"]) for b in plan["blocked_by"]] == [
        (PKG_A, "queued", "prefill")
    ]
    # The plain delete of the parent is not blocked by a package's job.
    assert client.delete(f"/v1/cache/{HD2}", headers=AUTH).status_code == 200


def test_404_when_no_member_maps_a_depot(tmp_path: Path) -> None:
    client, _root, settings = _make(tmp_path)
    _sql(settings, "INSERT INTO apps (appid, status) VALUES (1000, 'idle'), (2000, 'idle')")
    _classify(settings, 2000, "dlc", 1000)
    response = client.delete("/v1/cache/1000?with_packages=true", headers=AUTH)
    assert response.status_code == 404
    assert "and its content packages" in response.json()["detail"]
    assert client.get("/v1/cache/9999/delete-plan", headers=AUTH).status_code == 404


# ==========================================================================
# 3. Plan and execution agree
# ==========================================================================


def test_plan_and_execution_delete_the_same_depots(hd2) -> None:
    """ADR pin: the dialog's preview and the deletion use one definition."""
    client, _root, _settings = hd2
    plan = client.get(f"/v1/cache/{HD2}/delete-plan?with_packages=true", headers=AUTH).json()
    body = client.delete(f"/v1/cache/{HD2}?with_packages=true", headers=AUTH).json()

    assert {d["depotid"] for d in plan["delete_depots"]} == {d["depotid"] for d in body["deleted_depots"]}
    assert plan["skipped_shared"] == body["skipped_shared"]
    assert [m["appid"] for m in plan["members"]] == [m["appid"] for m in body["members"]]
    assert plan["kept_children"] == body["kept_children"]
    assert plan["total_bytes"] == body["total_bytes_freed"] == 300
    owners = {d["depotid"]: d["owners"] for d in plan["delete_depots"]}
    assert owners[D_HD2_PKG_A] == [HD2, PKG_A]
    assert plan["blocked_by"] == [] and plan["unusable"] == []


def test_plan_without_packages_matches_the_plain_delete(hd2) -> None:
    client, _root, _settings = hd2
    plan = client.get(f"/v1/cache/{HD2}/delete-plan", headers=AUTH).json()
    body = client.delete(f"/v1/cache/{HD2}", headers=AUTH).json()
    assert plan["with_packages"] is False
    assert [m["appid"] for m in plan["members"]] == [HD2]
    assert plan["kept_children"] == []
    assert {d["depotid"] for d in plan["delete_depots"]} == {d["depotid"] for d in body["deleted_depots"]}
    assert plan["skipped_shared"] == body["skipped_shared"]


def test_plan_changes_nothing(hd2) -> None:
    client, root, settings = hd2
    client.get(f"/v1/cache/{HD2}/delete-plan?with_packages=true", headers=AUTH)
    assert _on_disk(root, D_HD2_OWN) and _on_disk(root, D_PKG_A_OWN)
    assert _app(settings, PKG_A)["status"] == "done"


def test_one_shared_membership_definition(monkeypatch) -> None:
    """Structural pin (LEARNINGS, WP 4f): every deletion primitive reads the
    delete set through ``deletion.as_delete_set``, so a different answer there
    changes the plan, the outside-owner list, the mapping read and the
    execute-time recheck alike."""
    real = deletion.as_delete_set
    sentinel = deletion.DeleteSet(1, frozenset({1, 2}))

    def fake(target):
        return sentinel if target == 1 else real(target)

    monkeypatch.setattr(deletion, "as_delete_set", fake)
    rows = [(10, 1), (10, 2), (20, 2), (30, 3), (30, 1)]
    plan = deletion.plan_deletion(rows, 1, {3: True})
    assert plan.exclusive == [10, 20]  # app 2 is a member through the sentinel
    assert [s.depotid for s in plan.shared] == [30]
    assert deletion.other_owner_ids(rows, 1) == {3}

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE depot_app_map (depotid INTEGER, appid INTEGER);
        CREATE TABLE apps (appid INTEGER PRIMARY KEY, status TEXT, last_prefill_at TEXT);
        CREATE TABLE jobs (id INTEGER, appid INTEGER, status TEXT);
        INSERT INTO depot_app_map VALUES (10, 1), (10, 2), (20, 2), (40, 5);
        INSERT INTO apps VALUES (1, 'done', 'x'), (2, 'done', 'x');
        """
    )
    assert deletion.load_co_owners(conn, 10, 1) == []
    assert sorted(deletion.load_mapping_rows(conn, 1)) == [(10, 1), (10, 2), (20, 2)]


def test_members_do_not_protect_each_other_in_the_plan() -> None:
    rows = [(10, 1), (10, 2), (20, 2), (30, 2), (30, 9)]
    plan = deletion.plan_deletion(rows, deletion.DeleteSet(1, frozenset({1, 2})), {9: True})
    assert plan.exclusive == [10, 20]
    assert [(s.depotid, s.shared_with) for s in plan.shared] == [(30, [9])]
    # The same rows for app 1 alone: app 2 protects (unknown state).
    single = deletion.plan_deletion(rows, 1, {})
    assert single.exclusive == [] and [s.depotid for s in single.shared] == [10]


def test_as_delete_set_always_contains_the_primary() -> None:
    assert deletion.as_delete_set(5) == deletion.DeleteSet(5, frozenset({5}))
    assert deletion.as_delete_set(deletion.DeleteSet(5, frozenset({6}))).members == {5, 6}
