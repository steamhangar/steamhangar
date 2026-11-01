"""WP TOOL-NAMES-1: oracle names for tracked apps no source names.

No network: the store lookup and the oracle are driven with fake fetchers.
Fixtures are synthetic, modeled on the prod finding of 2026-11-01 (a Tool app
with no depots and a Config app with one depot; the store answers both with
``success != 1``; neither is owned or cached).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import TEST_API_KEY
from vault_api import app_classification, cover_art, oracle, oracle_names
from vault_api.config import Settings
from vault_api.db import get_connection, init_db
from vault_api.main import create_app

NOW = datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc)

TOOL_NODEPOTS = 4100001  # like 3990420: Tool, no depots
CONFIG_APP = 4100002  # like 1716750: Config, one depot
NAMED = 4100003
FIXED_TOOL = 228980  # tool_apps list


def _settings(tmp_path, *, oracle_on: bool = True) -> Settings:
    extra = {"manifest_oracle": "steamcmd_api"} if oracle_on else {}
    return Settings(
        vault_api_key=TEST_API_KEY,
        cache_root=str(tmp_path / "cache"),
        db_path=str(tmp_path / "vault.db"),
        log_level="INFO",
        **extra,
    )


@pytest.fixture
def db(tmp_path) -> str:
    path = str(tmp_path / "vault.db")
    init_db(path)
    return path


def _add_apps(db_path: str, *appids: int, name: str | None = None) -> None:
    conn = get_connection(db_path)
    try:
        for appid in appids:
            conn.execute(
                "INSERT INTO apps (appid, name, status) VALUES (?, ?, 'idle')", (appid, name)
            )
        conn.commit()
    finally:
        conn.close()


def _answer(appid: int, app: dict) -> bytes:
    return json.dumps(
        {"status": "success", "data": {str(appid): {"appid": str(appid), **app}}}
    ).encode()


ANSWERS = {
    TOOL_NODEPOTS: _answer(
        TOOL_NODEPOTS, {"common": {"name": "Synthetic Wireless Adapter Driver", "type": "Tool"}}
    ),
    CONFIG_APP: _answer(
        CONFIG_APP,
        {
            "common": {"name": "Synthetic Launcher PC Client", "type": "Config"},
            "depots": {
                "branches": {"public": {"buildid": "77"}},
                str(CONFIG_APP + 1): {"manifests": {"public": {"gid": "5555"}}},
            },
        },
    ),
}


def _store_unanswered(ids) -> bytes:
    """The store answers every id with success 15 (no store page)."""
    return json.dumps(
        {
            "response": {
                "store_items": [
                    {"item_type": 0, "id": a, "success": 15, "visible": False, "appid": 0}
                    for a in ids
                ]
            }
        }
    ).encode()


class FakeOracle:
    def __init__(self, answers: dict[int, bytes] | None = None, fail: bool = False) -> None:
        self.answers = ANSWERS if answers is None else answers
        self.fail = fail
        self.calls: list[int] = []

    def __call__(self, url: str) -> bytes:
        appid = int(url.rsplit("/", 1)[1])
        self.calls.append(appid)
        if self.fail or appid not in self.answers:
            raise oracle.OracleError("synthetic: unreachable")
        return self.answers[appid]


def _refresher(
    db_path: str, tmp_path, fake: FakeOracle, clock: list[datetime], *,
    oracle_on: bool = True, store=_store_unanswered,
):
    settings = _settings(tmp_path, oracle_on=oracle_on)
    return cover_art.CoverArtRefresher(
        db_path,
        fetch=store,
        now=lambda: clock[0],
        oracle_enabled=settings.manifest_oracle_enabled,
        name_lookup=oracle_names.OracleNameLookup(settings, fetch=fake),
    )


def _names(db_path: str, *, oracle_enabled: bool = True) -> dict[int, str | None]:
    conn = get_connection(db_path)
    try:
        resolved = app_classification.resolve_classifications(conn, oracle_enabled=oracle_enabled)
        return {
            int(row["appid"]): app_classification.display_name(
                row["name"], resolved.get(int(row["appid"]))
            )
            for row in conn.execute("SELECT appid, name FROM apps")
        }
    finally:
        conn.close()


# --------------------------------------------------------------------------


def test_unnamed_tool_and_config_get_a_name_after_one_refresher_cycle(db, tmp_path) -> None:
    _add_apps(db, TOOL_NODEPOTS, CONFIG_APP)
    fake = FakeOracle()
    refresher = _refresher(db, tmp_path, fake, [NOW])
    assert _names(db) == {TOOL_NODEPOTS: None, CONFIG_APP: None}

    refresher.tick()

    assert sorted(fake.calls) == [TOOL_NODEPOTS, CONFIG_APP]
    assert _names(db) == {
        TOOL_NODEPOTS: "Synthetic Wireless Adapter Driver",
        CONFIG_APP: "Synthetic Launcher PC Client",
    }
    conn = get_connection(db)
    try:
        resolved = app_classification.resolve_classifications(conn, oracle_enabled=True)
    finally:
        conn.close()
    assert resolved[TOOL_NODEPOTS].app_type == "tool"
    assert resolved[CONFIG_APP].app_type == "config"


def test_games_endpoint_shows_the_oracle_name(db, tmp_path) -> None:
    from fastapi.testclient import TestClient

    _add_apps(db, TOOL_NODEPOTS)
    _refresher(db, tmp_path, FakeOracle(), [NOW]).tick()
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        response = client.get(f"/v1/games/{TOOL_NODEPOTS}", headers={"X-Api-Key": TEST_API_KEY})
    assert response.status_code == 200
    assert response.json()["name"] == "Synthetic Wireless Adapter Driver"


def test_no_depots_answer_still_stores_the_name(db) -> None:
    info = oracle.parse_app_info(TOOL_NODEPOTS, ANSWERS[TOOL_NODEPOTS])
    assert info.depotids == ()
    assert info.name == "Synthetic Wireless Adapter Driver"
    conn = get_connection(db)
    try:
        oracle.store_app_info(conn, info, checked_at="2026-11-01T12:00:00Z")
        row = conn.execute(
            "SELECT name, app_type, depot_count FROM oracle_app_state WHERE appid = ?",
            (TOOL_NODEPOTS,),
        ).fetchone()
    finally:
        conn.close()
    assert (row["name"], row["app_type"], row["depot_count"]) == (
        "Synthetic Wireless Adapter Driver", "tool", 0,
    )


def test_named_apps_never_trigger_a_request(db, tmp_path) -> None:
    _add_apps(db, NAMED, name="Own Name")  # apps.name
    _add_apps(db, FIXED_TOOL)  # fixed tool list carries its own name
    _add_apps(db, 4100010, 4100011)
    conn = get_connection(db)
    try:
        # 4100010: named by the store (below); 4100011: named by a stored
        # oracle answer whose checked_at is old (the recent-answer skip does
        # not apply, only the name does).
        oracle.store_app_info(
            conn,
            oracle.parse_app_info(4100011, _answer(4100011, {"common": {"name": "Oracle Name"}})),
            checked_at="2020-01-01T00:00:00Z",
        )
    finally:
        conn.close()
    def store(ids) -> bytes:
        items = [
            {"item_type": 0, "id": a, "success": 1, "appid": a, "name": "Store Name"}
            if a == 4100010
            else {"item_type": 0, "id": a, "success": 15, "appid": 0}
            for a in ids
        ]
        return json.dumps({"response": {"store_items": items}}).encode()

    fake = FakeOracle()
    clock = [NOW]
    refresher = _refresher(db, tmp_path, fake, clock, store=store)
    for minute in range(5):
        clock[0] = NOW + timedelta(minutes=minute)
        refresher.tick()
    assert fake.calls == []
    assert _names(db)[4100010] == "Store Name"
    assert _names(db)[4100011] == "Oracle Name"


def test_blank_whitespace_name_counts_as_unnamed(db, tmp_path) -> None:
    _add_apps(db, TOOL_NODEPOTS, name="   ")
    fake = FakeOracle()
    _refresher(db, tmp_path, fake, [NOW]).tick()
    assert fake.calls == [TOOL_NODEPOTS]


def test_store_gets_the_first_word(db, tmp_path) -> None:
    """An app the store has not been asked about yet is not sent to the oracle."""
    _add_apps(db, TOOL_NODEPOTS)
    settings = _settings(tmp_path)
    lookup = oracle_names.OracleNameLookup(settings, fetch=FakeOracle())
    conn = get_connection(db)
    try:
        assert lookup.due_appids(conn, NOW) == []
        conn.execute(
            "INSERT INTO app_cover_art (appid, cover_url, outcome, attempts, checked_at, "
            "next_check_at) VALUES (?, NULL, 'none', 0, 'x', 'x')",
            (TOOL_NODEPOTS,),
        )
        conn.commit()
        assert lookup.due_appids(conn, NOW) == [TOOL_NODEPOTS]
    finally:
        conn.close()


def test_oracle_off_makes_no_request_and_the_app_stays_unknown(db, tmp_path) -> None:
    _add_apps(db, TOOL_NODEPOTS, CONFIG_APP)
    fake = FakeOracle()
    clock = [NOW]
    refresher = _refresher(db, tmp_path, fake, clock, oracle_on=False)
    for minute in range(3):
        clock[0] = NOW + timedelta(minutes=minute)
        refresher.tick()
    assert fake.calls == []
    assert _names(db, oracle_enabled=False) == {TOOL_NODEPOTS: None, CONFIG_APP: None}


def test_oracle_off_refresh_app_is_never_reached(db, tmp_path, monkeypatch) -> None:
    def boom(*_a, **_k):
        raise AssertionError("no oracle request while the oracle is off")

    monkeypatch.setattr(oracle, "refresh_app", boom)
    monkeypatch.setattr(oracle, "http_fetch", boom)
    _add_apps(db, TOOL_NODEPOTS)
    lookup = oracle_names.OracleNameLookup(_settings(tmp_path, oracle_on=False))
    conn = get_connection(db)
    try:
        conn.execute(
            "INSERT INTO app_cover_art (appid, cover_url, outcome, attempts, checked_at, "
            "next_check_at) VALUES (?, NULL, 'none', 0, 'x', 'x')",
            (TOOL_NODEPOTS,),
        )
        conn.commit()
        assert lookup.run_once(conn, NOW).requested == ()
    finally:
        conn.close()


def test_failure_backs_off_per_app_and_pauses_the_lookup(db, tmp_path) -> None:
    _add_apps(db, TOOL_NODEPOTS, CONFIG_APP)
    fake = FakeOracle(answers={CONFIG_APP: ANSWERS[CONFIG_APP]})  # TOOL fails
    clock = [NOW]
    refresher = _refresher(db, tmp_path, fake, clock)

    refresher.tick()  # TOOL (lowest id) fails: batch ends, global pause
    assert fake.calls == [TOOL_NODEPOTS]

    clock[0] = NOW + timedelta(minutes=30)
    refresher.tick()  # still in the global pause
    assert fake.calls == [TOOL_NODEPOTS]

    clock[0] = NOW + timedelta(hours=1, minutes=1)
    refresher.tick()  # pause over; TOOL is backing off, CONFIG is asked
    assert fake.calls == [TOOL_NODEPOTS, CONFIG_APP]
    assert _names(db)[CONFIG_APP] == "Synthetic Launcher PC Client"

    for hours in (2, 6, 23):
        clock[0] = NOW + timedelta(hours=hours)
        refresher.tick()
    assert fake.calls == [TOOL_NODEPOTS, CONFIG_APP]  # TOOL waits a day

    clock[0] = NOW + timedelta(days=1, minutes=1)
    refresher.tick()  # second failure: two days now
    assert fake.calls == [TOOL_NODEPOTS, CONFIG_APP, TOOL_NODEPOTS]
    clock[0] = NOW + timedelta(days=2, hours=12)
    refresher.tick()
    assert fake.calls.count(TOOL_NODEPOTS) == 2
    clock[0] = NOW + timedelta(days=3, hours=2)
    refresher.tick()
    assert fake.calls.count(TOOL_NODEPOTS) == 3
    assert _names(db)[TOOL_NODEPOTS] is None


def test_failure_backoff_is_capped() -> None:
    assert oracle_names.failure_backoff(1) == timedelta(days=1)
    assert oracle_names.failure_backoff(2) == timedelta(days=2)
    assert oracle_names.failure_backoff(5) == timedelta(days=16)
    assert oracle_names.failure_backoff(6) == oracle_names.FAIL_BACKOFF_MAX
    assert oracle_names.failure_backoff(100) == oracle_names.FAIL_BACKOFF_MAX


def test_answer_without_a_name_is_not_asked_again_for_a_long_time(db, tmp_path) -> None:
    _add_apps(db, TOOL_NODEPOTS)
    fake = FakeOracle(answers={TOOL_NODEPOTS: _answer(TOOL_NODEPOTS, {"common": {"type": "Tool"}})})
    clock = [NOW]
    refresher = _refresher(db, tmp_path, fake, clock)
    refresher.tick()
    assert fake.calls == [TOOL_NODEPOTS]
    for days in (1, 10, 29):
        clock[0] = NOW + timedelta(days=days)
        refresher.tick()
    assert fake.calls == [TOOL_NODEPOTS]
    # The marker is the stored answer itself, so a restart (new lookup state)
    # does not ask again either.
    clock[0] = NOW + timedelta(days=5)
    _refresher(db, tmp_path, fake, clock).tick()
    assert fake.calls == [TOOL_NODEPOTS]


def test_batch_is_bounded_per_tick(db, tmp_path) -> None:
    many = list(range(4200001, 4200011))
    _add_apps(db, *many)
    fake = FakeOracle(answers={a: _answer(a, {"common": {"name": f"N{a}"}}) for a in many})
    clock = [NOW]
    refresher = _refresher(db, tmp_path, fake, clock)
    refresher.tick()
    assert len(fake.calls) == oracle_names.BATCH_SIZE
    for minute in range(1, 10):
        clock[0] = NOW + timedelta(minutes=minute)
        refresher.tick()
    assert sorted(fake.calls) == many  # every app asked exactly once
    assert all(_names(db)[a] == f"N{a}" for a in many)


def test_every_request_is_logged(db, tmp_path, caplog) -> None:
    _add_apps(db, TOOL_NODEPOTS)
    with caplog.at_level(logging.INFO, logger="vault_api"):
        _refresher(db, tmp_path, FakeOracle(), [NOW]).tick()
    messages = [r.getMessage() for r in caplog.records]
    assert any(f"oracle name lookup: app {TOOL_NODEPOTS}" in m for m in messages)
    assert any(f"manifest oracle: app {TOOL_NODEPOTS} refreshed" in m for m in messages)


def test_update_check_selection_is_unchanged(db, tmp_path) -> None:
    """A name lookup for an uncached app does not put it into the update check."""
    from vault_api import scheduler

    _add_apps(db, CONFIG_APP)
    _refresher(db, tmp_path, FakeOracle(), [NOW]).tick()
    conn = get_connection(db)
    try:
        assert scheduler.update_check_appids(conn, str(tmp_path / "cache")) == []
    finally:
        conn.close()


def test_main_wires_the_name_lookup(tmp_path) -> None:
    on = create_app(_settings(tmp_path))
    off_path = tmp_path / "off"
    off_path.mkdir()
    off = create_app(_settings(off_path, oracle_on=False))
    assert on.state.cover_art_refresher._name_lookup.enabled is True
    assert off.state.cover_art_refresher._name_lookup.enabled is False


# --------------------------------------------------------------------------
# Review S1: what the stored snapshot does beyond the name
# --------------------------------------------------------------------------

DEPOT = CONFIG_APP + 1
RECORDED_GID = "4444"
PUBLIC_GID = "5555"
BETA_GID = "6666"

ANSWER_WITH_BETA = _answer(
    CONFIG_APP,
    {
        "common": {"name": "Synthetic Launcher PC Client", "type": "Config"},
        "depots": {
            "branches": {"public": {"buildid": "77"}, "beta": {"buildid": "78"}},
            str(DEPOT): {"manifests": {"public": {"gid": PUBLIC_GID}, "beta": {"gid": BETA_GID}}},
        },
    },
)


def _cached_not_owned(db_path: str, cache_root) -> None:
    """CONFIG_APP holds cache bytes for its mapped depot, has a recorded
    manifest that differs from the oracle's public one, and its newest prefill
    ended not_owned (so the update check leaves it out)."""
    depot_dir = cache_root / "depot" / str(DEPOT)
    depot_dir.mkdir(parents=True)
    (depot_dir / "chunk").write_bytes(b"x" * 1024)
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO apps (appid, name, status, last_prefill_at) "
            "VALUES (?, NULL, 'done', '2026-10-31T12:00:00Z')",
            (CONFIG_APP,),
        )
        conn.execute("INSERT INTO depot_app_map (depotid, appid) VALUES (?, ?)", (DEPOT, CONFIG_APP))
        conn.execute(
            "INSERT INTO depot_manifests (appid, containing_appid, depotid, manifestid, "
            "chunk_count, total_bytes, recorded_at, source, first_seen_at, manifest_changed_at) "
            "VALUES (?, NULL, ?, ?, 1, 1024, '2026-10-31T12:00:00Z', 'prefill', "
            "'2026-10-31T12:00:00Z', '2026-10-31T12:00:00Z')",
            (CONFIG_APP, DEPOT, RECORDED_GID),
        )
        finished = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute(
            "INSERT INTO jobs (appid, type, status, created_at, finished_at) "
            "VALUES (?, 'prefill', 'not_owned', ?, ?)",
            (CONFIG_APP, finished, finished),
        )
        conn.commit()
    finally:
        conn.close()


def test_name_lookup_snapshot_only_adds_to_the_gc_keep_set(db, tmp_path) -> None:
    """Oracle data can only ADD protection: after the lookup the GC keep set
    gains the open beta gid, never the public one, and vault-api's own
    recorded manifests (the public keep set) are unchanged."""
    from vault_api import gc

    _cached_not_owned(db, tmp_path / "cache")
    settings = _settings(tmp_path)
    conn = get_connection(db)
    try:
        recorded_before = gc.load_recorded_manifests(conn, CONFIG_APP)
        extra_before = oracle.gc_keepset_gids(conn, CONFIG_APP, settings=settings)
    finally:
        conn.close()
    assert recorded_before == {(CONFIG_APP, DEPOT): RECORDED_GID}
    assert extra_before == {}

    fake = FakeOracle(answers={CONFIG_APP: ANSWER_WITH_BETA})
    _refresher(db, tmp_path, fake, [NOW]).tick()
    assert fake.calls == [CONFIG_APP]

    conn = get_connection(db)
    try:
        recorded_after = gc.load_recorded_manifests(conn, CONFIG_APP)
        extra_after = oracle.gc_keepset_gids(conn, CONFIG_APP, settings=settings)
        extra_off = oracle.gc_keepset_gids(
            conn, CONFIG_APP, settings=_settings(tmp_path, oracle_on=False)
        )
    finally:
        conn.close()
    assert recorded_after == recorded_before
    assert extra_after == {DEPOT: [BETA_GID]}
    assert PUBLIC_GID not in extra_after[DEPOT]
    for depot, gids in extra_before.items():
        assert set(gids) <= set(extra_after.get(depot, ()))
    assert extra_off == {}


def test_name_lookup_verdict_on_a_cached_not_owned_app_shows_no_update(db, tmp_path) -> None:
    """The stored answer is an ordinary oracle snapshot (ADR-0006 note
    2026-11-01), so a cached, not-owned app with a recorded manifest that
    differs from the oracle's public one gets a ``stale`` verdict from that one
    answer. NITS-API-2 (NOT-OWNED-UPDATE-1, Weg A): neither the list nor the
    detail offers it as ``update_available`` (nothing the operator can
    update); ``update_checked_at`` still names the answer. The update check
    leaves the app out (not_owned)."""
    from fastapi.testclient import TestClient

    from vault_api import jobs, scheduler

    cache_root = tmp_path / "cache"
    _cached_not_owned(db, cache_root)
    fake = FakeOracle(answers={CONFIG_APP: ANSWER_WITH_BETA})
    _refresher(db, tmp_path, fake, [NOW]).tick()

    settings = _settings(tmp_path)
    conn = get_connection(db)
    try:
        assert CONFIG_APP in jobs.not_owned_appids(conn)
        assert scheduler.update_check_appids(conn, str(cache_root)) == []
        view = oracle.app_view(conn, CONFIG_APP, settings=settings)
    finally:
        conn.close()
    assert view.verdict == oracle.VERDICT_STALE
    assert view.checked_at is not None

    with TestClient(create_app(settings)) as client:
        headers = {"X-Api-Key": TEST_API_KEY}
        body = client.get(f"/v1/games/{CONFIG_APP}", headers=headers).json()
        listed = [
            g for g in client.get("/v1/games", headers=headers).json()
            if g["appid"] == CONFIG_APP
        ]
    assert body["name"] == "Synthetic Launcher PC Client"
    assert body["not_owned"] is True
    assert body["size_bytes"]
    assert body["update_available"] is False
    assert body["update_checked_at"] == view.checked_at
    assert len(listed) == 1
    assert listed[0]["not_owned"] is True
    assert listed[0]["update_available"] is False
    assert listed[0]["update_checked_at"] == view.checked_at


def test_the_same_cached_app_once_owned_shows_its_update(db, tmp_path) -> None:
    """Owned apps are unchanged (NITS-API-2): the same fixture whose newest
    prefill job did not end not_owned reads update_available=true on the list
    and the detail. The control for the test above (the gate is ownership,
    not the fixture)."""
    from fastapi.testclient import TestClient

    from vault_api import jobs

    _cached_not_owned(db, tmp_path / "cache")
    conn = get_connection(db)
    try:
        conn.execute(
            "INSERT INTO jobs (appid, type, status, created_at, finished_at) "
            "VALUES (?, 'prefill', 'done', ?, ?)",
            (CONFIG_APP, "2026-11-01T11:00:00Z", "2026-11-01T11:05:00Z"),
        )
        conn.commit()
        assert CONFIG_APP not in jobs.not_owned_appids(conn)
    finally:
        conn.close()
    fake = FakeOracle(answers={CONFIG_APP: ANSWER_WITH_BETA})
    _refresher(db, tmp_path, fake, [NOW]).tick()

    with TestClient(create_app(_settings(tmp_path))) as client:
        headers = {"X-Api-Key": TEST_API_KEY}
        body = client.get(f"/v1/games/{CONFIG_APP}", headers=headers).json()
        listed = [
            g for g in client.get("/v1/games", headers=headers).json()
            if g["appid"] == CONFIG_APP
        ]
    assert body["not_owned"] is False
    assert body["update_available"] is True
    assert listed[0]["not_owned"] is False
    assert listed[0]["update_available"] is True
