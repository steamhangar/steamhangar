"""WP TOOL-APPS-2a (ADR-0022 §1): content packages grouped under their game.

No network anywhere: the store lookup is driven with fake fetchers, the
oracle with stored or parsed synthetic answers. Fixtures are synthetic,
modeled on the P1 measurement of 2026-10-13 (HD2 553850 and its packages
2506210.., 2514120; tool package 228980): the store answers the game but not
the packages (success 15); the oracle names the packages (type DLC, parent
553850) and HD2's own answer lists them (extended.listofdlc, depot dlcappid).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from vault_api import app_classification, cover_art, oracle
from vault_api.config import Settings
from vault_api.db import SCHEMA_VERSION, get_connection, init_db
from vault_api.main import create_app

AUTH = {"X-Api-Key": TEST_API_KEY}
NOW = datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)

HD2 = 553850
PKG_A = 2506210
PKG_B = 2506220
PKG_OWN = 2514120
TOOL = 228980


# ==========================================================================
# 1. Vocabulary and provenance values (literal tables, never derived)
# ==========================================================================


def test_app_type_vocabulary_is_exactly_the_adr_set() -> None:
    assert app_classification.APP_TYPES == {
        "game", "dlc", "tool", "application", "config", "music", "video", "demo",
    }


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Game", "game"),
        ("game", "game"),
        ("GAME", "game"),
        ("DLC", "dlc"),
        ("Tool", "tool"),
        ("Application", "application"),
        ("Config", "config"),
        ("Music", "music"),
        ("Video", "video"),
        ("Demo", "demo"),
        ("Beta", None),
        ("Media", None),
        ("Guide", None),
        ("Series", None),
        ("", None),
        ("Gаme", None),  # Cyrillic a
        (1, None),
        (None, None),
        (True, None),
        (["Game"], None),
    ],
)
def test_pics_type_mapping_literal_table(raw: object, expected: str | None) -> None:
    assert app_classification.app_type_from_pics(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (0, "game"),
        (1, "demo"),
        (4, "dlc"),
        (6, "application"),
        (7, "video"),
        (11, "music"),
        (13, "tool"),
        (2, None),
        (3, None),
        (5, None),
        (8, None),
        (9, None),
        (10, None),
        (12, None),
        (14, None),
        (-1, None),
        (99, None),
        (True, None),
        (False, None),
        ("4", None),
        (4.0, None),
        (None, None),
    ],
)
def test_store_type_mapping_literal_table(raw: object, expected: str | None) -> None:
    assert app_classification.app_type_from_store(raw) == expected


def test_provenance_source_values_are_literal() -> None:
    assert app_classification.SOURCE_STORE == "store"
    assert app_classification.SOURCE_STEAMCMD_API == "steamcmd_api"
    assert oracle.SOURCE_STEAMCMD_API == "steamcmd_api"


# ==========================================================================
# 2. Oracle parsing: common.type / common.parent / listofdlc / dlcappid
# ==========================================================================


def _app_info(appid: int, app: dict) -> bytes:
    body = {"appid": str(appid), **app}
    return json.dumps({"status": "success", "data": {str(appid): body}}).encode()


HD2_ANSWER = _app_info(
    HD2,
    {
        "common": {"name": "HELLDIVERS 2", "type": "Game"},
        "extended": {"listofdlc": f"{PKG_A},{PKG_B}, {PKG_OWN}"},
        "depots": {
            "branches": {"public": {"buildid": "123"}},
            "553851": {"manifests": {"public": {"gid": "1111"}}},
            "553852": {"dlcappid": str(PKG_A), "manifests": {"public": {"gid": "2222"}}},
            # A DLC depot with no manifests of its own still names its DLC.
            "553853": {"dlcappid": str(PKG_B)},
        },
    },
)
PKG_OWN_ANSWER = _app_info(
    PKG_OWN, {"common": {"type": "DLC", "parent": str(HD2), "name": "Synthetic package"}}
)


def test_parse_reads_parent_side_links_and_keeps_existing_fields() -> None:
    info = oracle.parse_app_info(HD2, HD2_ANSWER)
    assert info.app_type == "game"
    assert info.parent_appid is None
    assert info.name == "HELLDIVERS 2"
    assert info.dlc_appids == (PKG_A, PKG_B, PKG_OWN)
    # Existing consumers' view is unchanged by the new fields.
    assert info.depotids == (553851, 553852)
    assert [(bm.depotid, bm.branch, bm.manifestid) for bm in info.branch_manifests] == [
        (553851, "public", "1111"),
        (553852, "public", "2222"),
    ]
    assert info.buildid == "123"


def test_parse_reads_child_type_and_parent_without_depots() -> None:
    info = oracle.parse_app_info(PKG_OWN, PKG_OWN_ANSWER)
    assert (info.app_type, info.parent_appid, info.dlc_appids) == ("dlc", HD2, ())
    assert info.name == "Synthetic package"
    assert info.branch_manifests == ()


def test_missing_fields_mean_unknown_never_an_error() -> None:
    info = oracle.parse_app_info(440, _app_info(440, {"depots": {}}))
    assert (info.app_type, info.parent_appid, info.dlc_appids) == (None, None, ())
    info = oracle.parse_app_info(440, _app_info(440, {"common": "x", "extended": 5}))
    assert (info.app_type, info.parent_appid, info.dlc_appids) == (None, None, ())


@pytest.mark.parametrize("parent", ["abc", " 1", "+1", "1_0", "0", "-5", True, 1.5, "440"])
def test_unusable_or_self_parent_is_no_parent(parent: object) -> None:
    info = oracle.parse_app_info(440, _app_info(440, {"common": {"type": "DLC", "parent": parent}}))
    assert info.parent_appid is None
    assert info.app_type == "dlc"


def test_parsed_oracle_name_is_sanitized_plain_text() -> None:
    raw = "  \u202eHD2\u200b - Pack\x00 " + "x" * 400
    info = oracle.parse_app_info(440, _app_info(440, {"common": {"name": raw}}))
    assert info.name == ("HD2 - Pack " + "x" * 400)[: app_classification.MAX_NAME_LEN]
    assert oracle.parse_app_info(440, _app_info(440, {"common": {"name": "\u200b "}})).name is None
    assert oracle.parse_app_info(440, _app_info(440, {"common": {"name": 5}})).name is None


def test_listofdlc_junk_entries_are_skipped_and_self_excluded() -> None:
    info = oracle.parse_app_info(
        440, _app_info(440, {"extended": {"listofdlc": "1, x,  ,0,3,440,3,٣"}})
    )
    assert info.dlc_appids == (1, 3)
    assert any("listofdlc" in w for w in info.warnings)


def test_listofdlc_of_the_wrong_type_is_ignored() -> None:
    info = oracle.parse_app_info(440, _app_info(440, {"extended": {"listofdlc": ["1"]}}))
    assert info.dlc_appids == ()
    info = oracle.parse_app_info(440, _app_info(440, {"extended": {"listofdlc": 7}}))
    assert info.dlc_appids == (7,)


def test_unusable_dlcappid_is_skipped_with_a_warning() -> None:
    info = oracle.parse_app_info(
        440, _app_info(440, {"depots": {"441": {"dlcappid": "x1"}, "442": {"dlcappid": "9"}}})
    )
    assert info.dlc_appids == (9,)
    assert "ignored unusable dlcappid of depot 441" in info.warnings


def test_dlc_links_are_bounded(monkeypatch) -> None:
    monkeypatch.setattr(oracle, "MAX_DLC_LINKS", 3)
    info = oracle.parse_app_info(440, _app_info(440, {"extended": {"listofdlc": "1,2,3,4,5"}}))
    assert info.dlc_appids == (1, 2, 3)


# ==========================================================================
# 3. Oracle storage: own columns, links with snapshot semantics
# ==========================================================================


@pytest.fixture
def db(tmp_path) -> str:
    path = str(tmp_path / "vault.db")
    init_db(path)
    return path


@pytest.fixture
def conn(db: str):
    connection = get_connection(db)
    try:
        yield connection
    finally:
        connection.close()


def _links(conn: sqlite3.Connection) -> set[tuple[int, int, str]]:
    return {
        (r["appid"], r["dlc_appid"], r["source"])
        for r in conn.execute("SELECT appid, dlc_appid, source FROM oracle_dlc_links")
    }


def test_store_writes_type_parent_and_links_snapshot(conn: sqlite3.Connection) -> None:
    oracle.store_app_info(conn, oracle.parse_app_info(HD2, HD2_ANSWER), checked_at="2026-10-13T00:00:00Z")
    oracle.store_app_info(
        conn, oracle.parse_app_info(PKG_OWN, PKG_OWN_ANSWER), checked_at="2026-10-13T00:00:00Z"
    )
    state = {r["appid"]: r for r in conn.execute("SELECT * FROM oracle_app_state")}
    assert (state[HD2]["app_type"], state[HD2]["parent_appid"]) == ("game", None)
    assert (state[PKG_OWN]["app_type"], state[PKG_OWN]["parent_appid"]) == ("dlc", HD2)
    assert (state[HD2]["name"], state[PKG_OWN]["name"]) == ("HELLDIVERS 2", "Synthetic package")
    assert _links(conn) == {
        (HD2, PKG_A, "steamcmd_api"),
        (HD2, PKG_B, "steamcmd_api"),
        (HD2, PKG_OWN, "steamcmd_api"),
    }

    smaller = _app_info(HD2, {"common": {"type": "Game"}, "extended": {"listofdlc": str(PKG_A)}})
    oracle.store_app_info(conn, oracle.parse_app_info(HD2, smaller), checked_at="2026-10-14T00:00:00Z")
    assert _links(conn) == {(HD2, PKG_A, "steamcmd_api")}

    oracle.clear_app(conn, HD2)
    assert _links(conn) == set()


# ==========================================================================
# 4. Store lookup (S1): parsing and recording
# ==========================================================================


def _store_item(appid: int, *, success: int = 1, **extra: object) -> dict:
    if success != 1:
        return {"item_type": 0, "id": appid, "success": success, "visible": False,
                "name": "", "store_url_path": "app/0/", "appid": 0}
    item = {"item_type": 0, "id": appid, "success": 1, "appid": appid,
            "name": f"Synthetic {appid}"}
    item.update(extra)
    return item


def _payload(*items: dict) -> bytes:
    return json.dumps({"response": {"store_items": list(items)}}).encode()


def test_parse_store_items_reads_type_parent_and_name() -> None:
    payload = _payload(
        _store_item(HD2, type=0, name="HELLDIVERS™ 2"),
        _store_item(4000001, type=4, related_items={"parent_appid": HD2}),
        _store_item(PKG_A, success=15),
        _store_item(4000002, name=""),  # answered, nothing usable
        _store_item(4000003, type=True, related_items={"parent_appid": 4000003}, name=7),
        _store_item(4000004, type=4, related_items="junk", name="A\x00B\u202e C "),
    )
    items = cover_art.parse_store_items(
        payload, [HD2, 4000001, PKG_A, 4000002, 4000003, 4000004]
    )
    facts = {appid: item.facts for appid, item in items.items()}
    Facts = app_classification.StoreFacts
    assert facts[HD2] == Facts(True, "game", None, "HELLDIVERS™ 2")
    assert facts[4000001] == Facts(True, "dlc", HD2, "Synthetic 4000001")
    assert facts[PKG_A] == Facts(False, None, None, None)
    assert facts[4000002] == Facts(True, None, None, None)
    assert facts[4000003] == Facts(True, None, None, None)
    assert facts[4000004] == Facts(True, "dlc", None, "AB C")
    assert not facts[4000002].usable and facts[4000004].usable
    # The old cover-only view is unchanged in shape.
    assert cover_art.parse_items(payload, [HD2, PKG_A]) == {HD2: None, PKG_A: None}


def _add_apps(db_path: str, *appids: int, name: str | None = None) -> None:
    connection = get_connection(db_path)
    try:
        for appid in appids:
            connection.execute(
                "INSERT INTO apps (appid, name, status) VALUES (?, ?, 'idle')", (appid, name)
            )
        connection.commit()
    finally:
        connection.close()


def _classes(db_path: str) -> dict[tuple[int, str], tuple[str | None, int | None, int, str | None]]:
    """``{(appid, source): (app_type, parent_appid, linked, name)}``."""
    connection = get_connection(db_path)
    try:
        return {
            (r["appid"], r["source"]): (r["app_type"], r["parent_appid"], r["linked"], r["name"])
            for r in connection.execute("SELECT * FROM app_classification")
        }
    finally:
        connection.close()


S, O = "store", "steamcmd_api"


def test_refresh_records_store_classification(db: str) -> None:
    _add_apps(db, HD2, 4000001, PKG_A)
    connection = get_connection(db)
    try:
        outcome = cover_art.refresh_once(
            connection,
            NOW,
            lambda ids: _payload(
                _store_item(HD2, type=0),
                _store_item(4000001, type=4, related_items={"parent_appid": HD2}),
                _store_item(PKG_A, success=15),
            ),
        )
    finally:
        connection.close()
    assert outcome.requested and outcome.error is None
    assert _classes(db) == {
        (HD2, S): ("game", None, 0, "Synthetic 553850"),
        (4000001, S): ("dlc", HD2, 0, "Synthetic 4000001"),
    }


def _insert(db_path: str, *rows: tuple) -> None:
    """Rows of (appid, source, app_type, parent_appid[, linked[, name]])."""
    connection = get_connection(db_path)
    try:
        for row in rows:
            appid, source, app_type, parent, *rest = row
            linked = rest[0] if rest else 0
            name = rest[1] if len(rest) > 1 else None
            connection.execute(
                "INSERT INTO app_classification (appid, source, app_type, parent_appid, "
                "linked, name, checked_at) VALUES (?, ?, ?, ?, ?, ?, 'x')",
                (appid, source, app_type, parent, linked, name),
            )
        connection.commit()
    finally:
        connection.close()


def test_store_non_answer_removes_only_the_store_row(db: str) -> None:
    _insert(db, (1, S, "dlc", 9), (1, O, "dlc", 9), (2, O, "dlc", 9))
    connection = get_connection(db)
    try:
        app_classification.record_store_results(
            connection,
            {1: app_classification.StoreFacts(False), 2: app_classification.StoreFacts(False)},
            NOW,
        )
    finally:
        connection.close()
    assert _classes(db) == {(1, O): ("dlc", 9, 0, None), (2, O): ("dlc", 9, 0, None)}


def test_store_answer_never_touches_the_oracle_row(db: str) -> None:
    _insert(db, (1, O, "dlc", 9))
    connection = get_connection(db)
    try:
        app_classification.record_store_results(
            connection, {1: app_classification.StoreFacts(True, "game", None, "One")}, NOW
        )
    finally:
        connection.close()
    assert _classes(db) == {(1, S): ("game", None, 0, "One"), (1, O): ("dlc", 9, 0, None)}


# ==========================================================================
# 5. Oracle rows (S2/S3, decision Q8 B: no request, cached answers only)
# ==========================================================================


def _store_oracle(db_path: str, appid: int, payload: bytes, checked_at: str = "2026-10-13T01:00:00Z") -> None:
    connection = get_connection(db_path)
    try:
        oracle.store_app_info(connection, oracle.parse_app_info(appid, payload), checked_at=checked_at)
    finally:
        connection.close()


def _fill(db_path: str, *, oracle_enabled: bool = True) -> app_classification.FillOutcome:
    connection = get_connection(db_path)
    try:
        return app_classification.fill_from_oracle(connection, oracle_enabled=oracle_enabled)
    finally:
        connection.close()


def test_fill_records_own_answers_and_parent_links(db: str) -> None:
    _add_apps(db, HD2, PKG_A, PKG_B, PKG_OWN, 4000009)
    _store_oracle(db, HD2, HD2_ANSWER)
    _store_oracle(db, PKG_OWN, PKG_OWN_ANSWER, checked_at="2026-10-13T02:00:00Z")

    outcome = _fill(db)

    assert _classes(db) == {
        (HD2, O): ("game", None, 0, "HELLDIVERS 2"),
        # Only linked: no own type, the single claimant is the parent.
        (PKG_A, O): (None, HD2, 1, None),
        (PKG_B, O): (None, HD2, 1, None),
        # Own answer plus linked.
        (PKG_OWN, O): ("dlc", HD2, 1, "Synthetic package"),
    }
    assert outcome.written == 4
    # Idempotent: nothing changes on the next tick.
    assert _fill(db) == app_classification.FillOutcome()


def test_fill_does_nothing_while_the_oracle_is_off(db: str) -> None:
    _add_apps(db, HD2, PKG_A)
    _store_oracle(db, HD2, HD2_ANSWER)
    assert _fill(db, oracle_enabled=False) == app_classification.FillOutcome()
    assert _classes(db) == {}


def test_fill_writes_its_own_row_beside_a_store_row(db: str) -> None:
    _add_apps(db, HD2, PKG_A)
    _store_oracle(db, HD2, HD2_ANSWER)
    _insert(db, (PKG_A, S, "dlc", None))
    _fill(db)
    assert _classes(db)[(PKG_A, S)] == ("dlc", None, 0, None)
    assert _classes(db)[(PKG_A, O)] == (None, HD2, 1, None)


def test_fill_own_parent_beats_the_single_linking_parent(db: str) -> None:
    _add_apps(db, 10, 20, 30)
    _store_oracle(db, 10, _app_info(10, {"extended": {"listofdlc": "30"}}))
    _store_oracle(db, 30, _app_info(30, {"common": {"type": "DLC", "parent": "20"}}))
    _fill(db)
    assert _classes(db)[(30, O)] == ("dlc", 20, 1, None)


def test_fill_two_claiming_parents_name_no_parent(db: str) -> None:
    _add_apps(db, 10, 20, 30)
    _store_oracle(db, 10, _app_info(10, {"extended": {"listofdlc": "30"}}))
    _store_oracle(db, 20, _app_info(20, {"extended": {"listofdlc": "30"}}))
    _fill(db)
    assert _classes(db)[(30, O)] == (None, None, 1, None)


def test_fill_only_covers_tracked_apps(db: str) -> None:
    _add_apps(db, HD2)
    _store_oracle(db, HD2, HD2_ANSWER)
    _fill(db)
    assert set(_classes(db)) == {(HD2, O)}


def test_fill_removes_its_row_when_the_oracle_no_longer_describes_the_app(db: str) -> None:
    _add_apps(db, HD2, PKG_A)
    _store_oracle(db, HD2, HD2_ANSWER)
    _fill(db)
    assert (PKG_A, O) in _classes(db)
    _store_oracle(db, HD2, _app_info(HD2, {"depots": {}}))
    outcome = _fill(db)
    assert _classes(db) == {}
    assert outcome.removed == 2  # PKG_A, and HD2 itself (no type or name any more)


def test_fill_makes_no_oracle_request(db: str, monkeypatch) -> None:
    def boom(*_a, **_k):
        raise AssertionError("the fill must not fetch anything")

    monkeypatch.setattr(oracle, "http_fetch", boom)
    monkeypatch.setattr(oracle, "refresh_app", boom)
    _add_apps(db, HD2, PKG_A)
    _store_oracle(db, HD2, HD2_ANSWER)
    _fill(db)
    assert _classes(db)[(PKG_A, O)] == (None, HD2, 1, None)


def test_tick_fills_even_while_the_store_lookup_is_paused(db: str) -> None:
    _add_apps(db, HD2, PKG_A)
    _store_oracle(db, HD2, HD2_ANSWER)

    def failing(_ids):
        raise cover_art.steam_relay.SteamRelayError("offline")

    clock = [NOW]
    refresher = cover_art.CoverArtRefresher(db, fetch=failing, now=lambda: clock[0], oracle_enabled=True)
    assert refresher.tick() is not None  # the failing lookup pauses the thread
    connection = get_connection(db)
    try:
        connection.execute("DELETE FROM app_classification")
        connection.commit()
    finally:
        connection.close()
    clock[0] = NOW + timedelta(seconds=1)
    assert refresher.tick() is None  # paused: no lookup ...
    assert _classes(db)[(PKG_A, O)] == (None, HD2, 1, None)  # ... but the fill ran


def test_tick_without_the_oracle_does_not_fill(db: str) -> None:
    _add_apps(db, HD2, PKG_A)
    _store_oracle(db, HD2, HD2_ANSWER)
    refresher = cover_art.CoverArtRefresher(
        db, fetch=lambda ids: _payload(), now=lambda: NOW
    )
    refresher.tick()
    assert _classes(db) == {}


def test_main_wires_the_oracle_switch_into_the_refresher(tmp_path) -> None:
    base = dict(vault_api_key=TEST_API_KEY, cache_root=str(tmp_path / "c"), log_level="INFO")
    on = create_app(Settings(db_path=str(tmp_path / "a.db"), manifest_oracle="steamcmd_api", **base))
    off = create_app(Settings(db_path=str(tmp_path / "b.db"), **base))
    assert on.state.cover_art_refresher._oracle_enabled is True
    assert off.state.cover_art_refresher._oracle_enabled is False


# ==========================================================================
# 6. Reading: per-field merge, nesting, cycles, tool apps, names
# ==========================================================================


def _raw(db_path: str, *rows: tuple[int, str | None, object, str]) -> None:
    """Rows of (appid, app_type, parent_appid, source), the 2a-round-1 shape."""
    _insert(db_path, *[(appid, source, app_type, parent) for appid, app_type, parent, source in rows])


def _resolve_full(db_path: str, *, oracle_enabled: bool = True) -> dict[int, app_classification.Classification]:
    connection = get_connection(db_path)
    try:
        return app_classification.resolve_classifications(connection, oracle_enabled=oracle_enabled)
    finally:
        connection.close()


def _resolve(db_path: str, *, oracle_enabled: bool = True) -> dict[int, tuple[str | None, int | None]]:
    return {
        appid: (c.app_type, c.parent_appid)
        for appid, c in _resolve_full(db_path, oracle_enabled=oracle_enabled).items()
    }


def test_store_type_without_parent_takes_the_oracle_parent(db: str) -> None:
    """Q1 A, "oracle for gaps", per field (2a review)."""
    _add_apps(db, HD2, PKG_A)
    _insert(db, (PKG_A, S, "dlc", None), (PKG_A, O, None, HD2, 1))
    assert _resolve(db) == {PKG_A: ("dlc", HD2)}
    # Without the oracle the gap stays open, the store's type stays.
    assert _resolve(db, oracle_enabled=False) == {PKG_A: ("dlc", None)}


def test_store_type_wins_over_the_oracle_type(db: str) -> None:
    _add_apps(db, HD2, PKG_A)
    _insert(db, (PKG_A, S, "music", None), (PKG_A, O, "dlc", HD2))
    assert _resolve(db) == {PKG_A: ("music", HD2)}


def test_store_parent_always_wins_over_the_oracle_parent(db: str) -> None:
    _add_apps(db, HD2, 730, PKG_A)
    _insert(db, (PKG_A, S, None, 730), (PKG_A, O, "dlc", HD2))
    assert _resolve(db)[PKG_A] == ("dlc", 730)


def test_own_type_is_kept_apart_from_a_linked_dlc(db: str) -> None:
    """For TOOL-APPS-2b: a dlc inferred only from a parent's list is not the
    app's own type."""
    _add_apps(db, HD2, PKG_A, PKG_OWN)
    _insert(db, (PKG_A, O, None, HD2, 1), (PKG_OWN, O, "dlc", HD2, 1))
    resolved = _resolve_full(db)
    assert (resolved[PKG_A].app_type, resolved[PKG_A].own_app_type) == ("dlc", None)
    assert (resolved[PKG_OWN].app_type, resolved[PKG_OWN].own_app_type) == ("dlc", "dlc")


def test_nested_packages_attach_to_the_top_level_parent(db: str) -> None:
    _add_apps(db, 1, 2, 3)
    _raw(db, (1, "dlc", 2, "store"), (2, "dlc", 3, "store"), (3, "game", None, "store"))
    assert _resolve(db) == {1: ("dlc", 3), 2: ("dlc", 3), 3: ("game", None)}


def test_nesting_follows_the_merged_parent(db: str) -> None:
    _add_apps(db, 1, 2, 3)
    _insert(db, (1, S, "dlc", 2), (2, S, "dlc", None), (2, O, None, 3))
    assert _resolve(db) == {1: ("dlc", 3), 2: ("dlc", 3)}


def test_chain_of_four_links_groups_five_does_not(db: str) -> None:
    _add_apps(db, 1, 2, 3, 4, 5, 6)
    _raw(db, *[(n, "dlc", n + 1, "store") for n in range(1, 6)])
    resolved = _resolve(db)
    assert resolved[2] == ("dlc", 6)  # 2->3->4->5->6: four links
    assert resolved[1] == ("dlc", None)  # five links: ungrouped


def test_cycle_and_self_reference_are_ungrouped(db: str) -> None:
    _add_apps(db, 1, 2, 7)
    _raw(db, (1, "dlc", 2, "store"), (2, "dlc", 1, "store"), (7, "dlc", 7, "store"))
    assert _resolve(db) == {1: ("dlc", None), 2: ("dlc", None), 7: ("dlc", None)}


def test_untracked_top_level_parent_leaves_the_row_ungrouped(db: str) -> None:
    _add_apps(db, 1)
    _raw(db, (1, "dlc", 999, "store"))
    assert _resolve(db) == {1: ("dlc", None)}


def test_tracked_parent_without_a_row_is_a_valid_top_level(db: str) -> None:
    _add_apps(db, 1, HD2)
    _raw(db, (1, "dlc", HD2, "steamcmd_api"))
    assert _resolve(db) == {1: ("dlc", HD2)}


def test_fixed_list_tool_app_is_never_a_child_nor_a_parent(db: str) -> None:
    _add_apps(db, TOOL, HD2, 1)
    _raw(db, (TOOL, "tool", HD2, "store"), (1, "dlc", TOOL, "store"))
    assert _resolve(db) == {TOOL: ("tool", None), 1: ("dlc", None)}


def test_oracle_rows_are_ignored_while_the_oracle_is_off(db: str) -> None:
    _add_apps(db, HD2, PKG_A, 4000001)
    _raw(db, (PKG_A, "dlc", HD2, "steamcmd_api"), (4000001, "dlc", HD2, "store"))
    assert _resolve(db, oracle_enabled=False) == {4000001: ("dlc", HD2)}
    assert _resolve(db, oracle_enabled=True) == {4000001: ("dlc", HD2), PKG_A: ("dlc", HD2)}


def test_tampered_rows_are_revalidated_on_read(db: str) -> None:
    _add_apps(db, 1, 2, 3, 4, HD2)
    _raw(
        db,
        (1, "evil", HD2, "store"),
        (2, "dlc", "abc", "store"),  # stays TEXT under INTEGER affinity
        (3, "dlc", HD2, "somebody_else"),
    )
    _insert(db, (4, S, None, None, 0, "X" * 1000 + "\x07"))
    resolved = _resolve_full(db)
    assert (resolved[1].app_type, resolved[1].parent_appid) == (None, HD2)
    assert (resolved[2].app_type, resolved[2].parent_appid) == ("dlc", None)
    assert 3 not in resolved
    assert resolved[4].name == "X" * app_classification.MAX_NAME_LEN


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HELLDIVERS™ 2 - Português-Brasil", "HELLDIVERS™ 2 - Português-Brasil"),
        ("  padded  ", "padded"),
        ("tab\there\nnewline\x7f\x85", "tabherenewline"),
        ("\u202eevil\u2066", "evil"),
        ("a\u200eb\u200fc\u061cd", "abcd"),  # LRM, RLM, ALM
        ("z\u200bw\u200cj\u200dn\u2060b\ufeffo", "zwjnbo"),  # zero-width, WJ, BOM
        ("line\u2028para\u2029end", "lineparaend"),
        ("\u200b\ufeff\u200e", None),
        ("\ufeffBOM-led", "BOM-led"),
        ("<b>markup stays text</b>", "<b>markup stays text</b>"),
        ("", None),
        ("   ", None),
        ("\x00\x01", None),
        (5, None),
        (None, None),
        ("y" * 300, "y" * 256),
    ],
)
def test_valid_app_name(raw: object, expected: str | None) -> None:
    assert app_classification.valid_app_name(raw) == expected


def test_name_merge_store_first_then_oracle(db: str) -> None:
    _add_apps(db, 1, 2)
    _insert(db, (1, S, None, None, 0, "Store name"), (1, O, None, None, 0, "Oracle name"),
            (2, O, None, None, 0, "Oracle only"))
    resolved = _resolve_full(db)
    assert resolved[1].name == "Store name"
    assert resolved[2].name == "Oracle only"
    assert 2 not in _resolve_full(db, oracle_enabled=False)


# ==========================================================================
# 7. API: parent_appid / app_type / name on GET /v1/games and /v1/games/{appid}
# ==========================================================================


def _seed(client: TestClient, depotid: int, appid: int, name: str | None = None) -> None:
    response = client.put(f"/v1/mapping/{depotid}", json={"appid": appid, "app_name": name}, headers=AUTH)
    assert response.status_code == 200


def test_games_expose_parent_and_type(settings: Settings) -> None:
    client = TestClient(create_app(settings))
    _seed(client, HD2 + 1, HD2)
    _seed(client, PKG_A + 1, PKG_A)
    _seed(client, 731, 730)
    _raw(settings.db_path, (HD2, "game", None, "store"), (PKG_A, "dlc", HD2, "store"))

    games = {g["appid"]: g for g in client.get("/v1/games", headers=AUTH).json()}
    assert len(games) == 3  # flat: nothing nested, nothing dropped
    assert (games[PKG_A]["parent_appid"], games[PKG_A]["app_type"]) == (HD2, "dlc")
    assert (games[HD2]["parent_appid"], games[HD2]["app_type"]) == (None, "game")
    assert (games[730]["parent_appid"], games[730]["app_type"]) == (None, None)
    # The raw per-app truth is untouched.
    assert games[PKG_A]["tool_app"] is False and games[PKG_A]["depot_count"] == 1

    detail = client.get(f"/v1/games/{PKG_A}", headers=AUTH).json()
    assert (detail["parent_appid"], detail["app_type"]) == (HD2, "dlc")
    detail = client.get("/v1/games/730", headers=AUTH).json()
    assert (detail["parent_appid"], detail["app_type"]) == (None, None)


def test_games_fill_a_missing_name_but_never_replace_one(settings: Settings) -> None:
    client = TestClient(create_app(settings))
    _seed(client, HD2 + 1, HD2, name="HELLDIVERS 2 (owned)")
    _seed(client, PKG_A + 1, PKG_A)
    _seed(client, 731, 730)
    pkg_name = "HELLDIVERS™ 2 - Português-Brasil"
    _insert(
        settings.db_path,
        (HD2, S, "game", None, 0, "HELLDIVERS™ 2"),
        (PKG_A, S, None, None, 0, pkg_name),
    )
    games = {g["appid"]: g for g in client.get("/v1/games", headers=AUTH).json()}
    assert games[HD2]["name"] == "HELLDIVERS 2 (owned)"  # vault-api's own name wins
    assert games[PKG_A]["name"] == pkg_name  # filled, untrimmed
    assert games[730]["name"] is None
    assert client.get(f"/v1/games/{PKG_A}", headers=AUTH).json()["name"] == pkg_name
    assert client.get(f"/v1/games/{HD2}", headers=AUTH).json()["name"] == "HELLDIVERS 2 (owned)"
    # Never written into apps.
    connection = get_connection(settings.db_path)
    try:
        (stored,) = connection.execute("SELECT name FROM apps WHERE appid = ?", (PKG_A,)).fetchone()
    finally:
        connection.close()
    assert stored is None


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_games_blank_apps_name_falls_back(settings: Settings, blank: str) -> None:
    client = TestClient(create_app(settings))
    _seed(client, PKG_A + 1, PKG_A)
    connection = get_connection(settings.db_path)
    try:
        connection.execute("UPDATE apps SET name = ? WHERE appid = ?", (blank, PKG_A))
        connection.commit()
    finally:
        connection.close()
    _insert(settings.db_path, (PKG_A, S, None, None, 0, "Store name"))
    (game,) = client.get("/v1/games", headers=AUTH).json()
    assert game["name"] == "Store name"
    assert client.get(f"/v1/games/{PKG_A}", headers=AUTH).json()["name"] == "Store name"


def test_games_hide_oracle_rows_when_the_oracle_is_off(settings: Settings, tmp_path) -> None:
    client = TestClient(create_app(settings))  # conftest settings: oracle off
    _seed(client, HD2 + 1, HD2)
    _seed(client, PKG_A + 1, PKG_A)
    _insert(settings.db_path, (PKG_A, O, "dlc", HD2, 0, "Oracle name"))
    games = {g["appid"]: g for g in client.get("/v1/games", headers=AUTH).json()}
    assert (games[PKG_A]["parent_appid"], games[PKG_A]["app_type"], games[PKG_A]["name"]) == (None, None, None)

    on = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=settings.db_path,
        cache_root=settings.cache_root,
        log_level="INFO",
        manifest_oracle="steamcmd_api",
    )
    games = {g["appid"]: g for g in TestClient(create_app(on)).get("/v1/games", headers=AUTH).json()}
    assert (games[PKG_A]["parent_appid"], games[PKG_A]["app_type"], games[PKG_A]["name"]) == (
        HD2, "dlc", "Oracle name",
    )


def test_tool_app_keeps_its_fixed_list_fields_and_is_not_grouped(settings: Settings) -> None:
    client = TestClient(create_app(settings))
    _seed(client, HD2 + 1, HD2)
    _seed(client, TOOL + 1, TOOL)
    _raw(settings.db_path, (TOOL, "dlc", HD2, "store"), (HD2, "tool", None, "store"))
    games = {g["appid"]: g for g in client.get("/v1/games", headers=AUTH).json()}
    assert games[TOOL]["tool_app"] is True
    assert games[TOOL]["parent_appid"] is None
    # A dynamic "tool" type is display data only: tool_app stays the fixed list.
    assert games[HD2]["app_type"] == "tool" and games[HD2]["tool_app"] is False


# ==========================================================================
# 8. Migration to v22 (incl. the one-time cover next_check_at reset)
# ==========================================================================


def _make_pre_v22(path: str, version: int) -> None:
    init_db(path)
    connection = get_connection(path)
    try:
        connection.execute("DROP TABLE app_classification")
        connection.execute("DROP TABLE oracle_dlc_links")
        if version < 21:
            # A true v20 database has no D10 table either.
            connection.execute("DROP TABLE app_cover_file")
        connection.execute("DROP TABLE oracle_branch_manifests")
        connection.execute("DROP TABLE oracle_app_state")
        connection.execute(
            """
            CREATE TABLE oracle_app_state (
                appid INTEGER PRIMARY KEY, buildid TEXT, checked_at TEXT NOT NULL,
                source TEXT NOT NULL, depot_count INTEGER NOT NULL,
                branch_count INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO oracle_app_state VALUES (440, '1', '2026-10-01T00:00:00Z', 'steamcmd_api', 1, 1)"
        )
        connection.executemany(
            "INSERT INTO app_cover_art (appid, cover_url, outcome, attempts, checked_at, next_check_at) "
            "VALUES (?, ?, ?, ?, '2026-10-01T00:00:00Z', ?)",
            [
                (10, "https://shared.akamai.steamstatic.com/x.jpg", "found", 0, "2099-01-01T00:00:00Z"),
                (20, None, "none", 0, "2099-01-01T00:00:00Z"),
                (30, None, "failed", 3, "2000-01-01T00:00:00Z"),
            ],
        )
        connection.execute("UPDATE schema_version SET version = ?", (version,))
        connection.commit()
    finally:
        connection.close()


@pytest.mark.parametrize("old_version", [20, 21])
def test_upgrade_to_v22_adds_schema_and_resets_cover_checks_once(tmp_path, old_version: int) -> None:
    # v22 is the step under test; later versions (v23 WP D15b) also apply.
    assert SCHEMA_VERSION >= 22
    path = str(tmp_path / "old.db")
    _make_pre_v22(path, old_version)
    before = datetime.now(timezone.utc).replace(microsecond=0)

    init_db(path)

    connection = get_connection(path)
    try:
        (version,) = connection.execute("SELECT version FROM schema_version").fetchone()
        state_cols = {r["name"]: r["type"] for r in connection.execute("PRAGMA table_info(oracle_app_state)")}
        class_cols = {r["name"]: r["type"] for r in connection.execute("PRAGMA table_info(app_classification)")}
        link_cols = {r["name"]: r["type"] for r in connection.execute("PRAGMA table_info(oracle_dlc_links)")}
        old_state = connection.execute("SELECT * FROM oracle_app_state WHERE appid = 440").fetchone()
        covers = {r["appid"]: r for r in connection.execute("SELECT * FROM app_cover_art")}
    finally:
        connection.close()

    assert version == SCHEMA_VERSION
    assert state_cols["app_type"] == "TEXT" and state_cols["parent_appid"] == "INTEGER"
    assert state_cols["name"] == "TEXT"
    assert class_cols == {
        "appid": "INTEGER", "source": "TEXT", "app_type": "TEXT", "parent_appid": "INTEGER",
        "linked": "INTEGER", "name": "TEXT", "checked_at": "TEXT",
    }
    assert link_cols == {"appid": "INTEGER", "dlc_appid": "INTEGER", "recorded_at": "TEXT", "source": "TEXT"}
    assert (old_state["buildid"], old_state["app_type"], old_state["parent_appid"], old_state["name"]) == (
        "1", None, None, None,
    )

    after = datetime.now(timezone.utc) + timedelta(seconds=1)
    for appid in (10, 20):
        moved = datetime.strptime(covers[appid]["next_check_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        assert before <= moved <= after
    assert covers[30]["next_check_at"] == "2000-01-01T00:00:00Z"  # already due: untouched
    assert covers[30]["attempts"] == 3
    assert covers[10]["cover_url"] == "https://shared.akamai.steamstatic.com/x.jpg"

    # Once only: a later init_db does not reset again.
    connection = get_connection(path)
    try:
        connection.execute("UPDATE app_cover_art SET next_check_at = '2099-02-02T00:00:00Z' WHERE appid = 10")
        connection.commit()
    finally:
        connection.close()
    init_db(path)
    connection = get_connection(path)
    try:
        (stamp,) = connection.execute("SELECT next_check_at FROM app_cover_art WHERE appid = 10").fetchone()
    finally:
        connection.close()
    assert stamp == "2099-02-02T00:00:00Z"


def test_reset_makes_existing_apps_due_for_the_store_lookup(tmp_path) -> None:
    path = str(tmp_path / "old.db")
    _make_pre_v22(path, 20)
    _add_apps(path, 10, 20)
    init_db(path)
    connection = get_connection(path)
    try:
        due = cover_art.due_appids(connection, datetime.now(timezone.utc) + timedelta(seconds=2))
    finally:
        connection.close()
    assert set(due) >= {10, 20}
