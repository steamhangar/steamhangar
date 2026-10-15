"""WP D23 LIBRARY-META-1a/1b: durable app metadata, nightly oracle fill, and
the library filter/sort parameters of ``GET /v1/games`` (schema v30).

Guarantees pinned here, by name:

1. **Off by default, gated three ways.** Nothing is fetched unless the own
   switch (``VAULT_LIBRARY_META``), the oracle AND a schedule window all
   allow it, and only while the window is open.
2. **Unknown never hides an app silently.** A filter leaves unknown apps out
   by default, says how many in ``X-Meta-Unknown-Excluded``, and
   ``include_unknown=true`` keeps them.
3. **Resumable and polite.** Failures back off per app, keep old values, and a
   run stops after repeated failures; due apps are never-fetched first.
4. **The migration is additive.** A populated v29 file gains the tables and
   keeps its data; an older build refuses the v30 file.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from tests.test_oracle import enabled_settings, off_settings
from vault_api import library_meta, oracle
from vault_api.config import Settings
from vault_api.db import SCHEMA_VERSION, get_connection, init_db
from vault_api.main import create_app
from vault_api.mapping import upsert_mapping
from vault_api.schedule_window import parse_window
from vault_api.scheduler import PrefillScheduler

AUTH = {"X-Api-Key": TEST_API_KEY}
NOW = datetime(2026, 10, 15, 4, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def payload(appid: int, **common: object) -> bytes:
    document = {
        "status": "success",
        "data": {str(appid): {"appid": str(appid), "common": common}},
    }
    return json.dumps(document).encode()


FULL = dict(
    category_2="1",
    category_9="1",
    category_22="1",
    genres={"0": "1", "1": "23"},
    review_percentage="97",
    review_score="9",
    steam_release_date="1577836800",  # 2020-01-01 UTC
)


class FakeOracle:
    """Records every URL asked; answers from a dict, else fails."""

    def __init__(self, answers: dict[int, bytes] | None = None) -> None:
        self.answers = answers or {}
        self.asked: list[int] = []

    def __call__(self, url: str) -> bytes:
        appid = int(url.rstrip("/").rsplit("/", 1)[1])
        self.asked.append(appid)
        if appid not in self.answers:
            raise oracle.OracleError("HTTP 429")
        return self.answers[appid]


def meta_settings(tmp_path: Path, **overrides: object) -> Settings:
    return enabled_settings(
        tmp_path,
        library_meta_enabled=True,
        schedule_window=parse_window("03:00-07:00"),
        **overrides,
    )


@pytest.fixture
def conn(tmp_path: Path):
    path = str(tmp_path / "vault.db")
    init_db(path)
    connection = get_connection(path)
    try:
        yield connection
    finally:
        connection.close()


def add_app(conn: sqlite3.Connection, appid: int, name: str | None = None) -> None:
    upsert_mapping(conn, appid * 10, appid, name or f"Game {appid}")


# --------------------------------------------------------------------------
# Schema and migration
# --------------------------------------------------------------------------


def test_schema_is_at_least_v30() -> None:
    assert SCHEMA_VERSION >= 30


def _make_v29_database(db_path: str) -> None:
    """A populated v29 file: no metadata tables, stamped 29."""
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        conn.executescript(
            """
            DROP TABLE app_meta;
            DROP TABLE app_meta_genres;
            UPDATE schema_version SET version = 29;
            """
        )
        upsert_mapping(conn, 441, 440, "Team Fortress 2")
        conn.commit()
    finally:
        conn.close()


def test_a_copy_of_a_populated_v29_database_migrates_and_keeps_its_data(
    tmp_path: Path,
) -> None:
    original = str(tmp_path / "vault.db")
    _make_v29_database(original)
    copy = str(tmp_path / "copy.db")
    shutil.copy(original, copy)

    init_db(copy)
    init_db(copy)  # idempotent

    conn = get_connection(copy)
    try:
        (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        app = conn.execute("SELECT name FROM apps WHERE appid = 440").fetchone()
        meta_rows = conn.execute("SELECT COUNT(*) FROM app_meta").fetchone()[0]
        # The new tables are usable on the migrated copy.
        library_meta.store_meta(
            conn,
            library_meta.parse_app_meta(440, payload(440, **FULL)),
            fetched_at="2026-10-15T04:00:00Z",
        )
        stored = library_meta.meta_by_appid(conn)[440]
    finally:
        conn.close()
    assert version == SCHEMA_VERSION
    assert {"app_meta", "app_meta_genres"} <= tables
    assert app["name"] == "Team Fortress 2"
    assert meta_rows == 0  # nothing invented: every app is "unknown"
    assert stored["coop"] is True

    raw = sqlite3.connect(original)  # the untouched original is still v29
    try:
        assert raw.execute("SELECT version FROM schema_version").fetchone()[0] == 29
        assert raw.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'app_meta'"
        ).fetchone()[0] == 0
    finally:
        raw.close()


def test_an_older_build_refuses_a_newer_file(tmp_path: Path) -> None:
    path = str(tmp_path / "vault.db")
    init_db(path)
    conn = get_connection(path)
    conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION + 1,))
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="newer version"):
        init_db(path)


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def test_parse_reads_flags_genres_rating_and_year() -> None:
    meta = library_meta.parse_app_meta(440, payload(440, **FULL))
    assert (meta.flag_single, meta.flag_multi, meta.flag_coop, meta.flag_vr) == (
        True, False, True, False,
    )
    assert meta.genres == (1, 23)
    assert (meta.review_percent, meta.review_score, meta.release_year) == (97, 9, 2020)


def test_parse_maps_the_vr_and_multiplayer_category_families() -> None:
    meta = library_meta.parse_app_meta(
        5, payload(5, category_1="1", category_53="1", category_2="0")
    )
    assert meta.flag_multi is True and meta.flag_vr is True
    assert meta.flag_single is False  # "0" is not set


def test_parse_without_categories_leaves_flags_unknown_not_false() -> None:
    meta = library_meta.parse_app_meta(5, payload(5, review_percentage="80"))
    assert (meta.flag_single, meta.flag_multi, meta.flag_coop, meta.flag_vr) == (
        None, None, None, None,
    )
    assert meta.review_percent == 80


def test_parse_drops_malformed_values_and_survives_a_missing_common() -> None:
    meta = library_meta.parse_app_meta(
        5,
        payload(5, review_percentage="500", review_score="x", steam_release_date="-3",
                genres={"0": "abc", "1": "7", "2": "7"}),
    )
    assert (meta.review_percent, meta.review_score, meta.release_year) == (None, None, None)
    assert meta.genres == (7,)
    bare = json.dumps({"status": "success", "data": {"5": {"appid": "5"}}}).encode()
    assert library_meta.parse_app_meta(5, bare) == library_meta.AppMeta(appid=5)


def test_parse_refuses_an_answer_for_another_app() -> None:
    with pytest.raises(oracle.OracleError):
        library_meta.parse_app_meta(6, payload(5, **FULL))


# --------------------------------------------------------------------------
# Storage, selection and backoff
# --------------------------------------------------------------------------


def test_a_refetch_replaces_values_and_genres(conn: sqlite3.Connection) -> None:
    add_app(conn, 440)
    library_meta.store_meta(
        conn, library_meta.parse_app_meta(440, payload(440, **FULL)), fetched_at="2026-10-01T00:00:00Z"
    )
    library_meta.store_meta(
        conn,
        library_meta.parse_app_meta(440, payload(440, category_1="1", genres={"0": "2"})),
        fetched_at="2026-10-02T00:00:00Z",
    )
    stored = library_meta.meta_by_appid(conn)[440]
    assert stored["genres"] == [2]
    assert stored["multiplayer"] is True and stored["coop"] is False
    assert stored["review_percent"] is None  # the new answer did not carry it
    assert stored["fetched_at"] == "2026-10-02T00:00:00Z"


def test_known_apps_cover_every_source(conn: sqlite3.Connection) -> None:
    add_app(conn, 1)  # apps + depot_app_map
    conn.execute(
        "INSERT INTO jobs (appid, type, created_at) VALUES (2, 'prefill', '2026-10-01T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO oracle_app_state (appid, checked_at, source, depot_count, branch_count) "
        "VALUES (3, '2026-10-01T00:00:00Z', 'x', 0, 0)"
    )
    conn.commit()
    assert library_meta.known_appids(conn) == [1, 2, 3]


def test_due_apps_are_never_fetched_first_then_oldest_and_not_fresh(
    conn: sqlite3.Connection,
) -> None:
    for appid in (1, 2, 3, 4):
        add_app(conn, appid)
    fresh = (NOW - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    old = (NOW - timedelta(days=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
    older = (NOW - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for appid, stamp in ((1, fresh), (2, old), (3, older)):
        library_meta.store_meta(conn, library_meta.AppMeta(appid=appid), fetched_at=stamp)
    assert library_meta.due_appids(conn, now=NOW) == [4, 3, 2]


def test_a_failure_backs_off_keeps_old_values_and_doubles(conn: sqlite3.Connection) -> None:
    add_app(conn, 440)
    library_meta.store_meta(
        conn, library_meta.parse_app_meta(440, payload(440, **FULL)), fetched_at="2026-08-01T00:00:00Z"
    )
    library_meta.record_failure(conn, 440, now=NOW)
    first = conn.execute("SELECT fail_count, next_try_at FROM app_meta").fetchone()
    library_meta.record_failure(conn, 440, now=NOW)
    second = conn.execute("SELECT fail_count, next_try_at FROM app_meta").fetchone()
    assert (first["fail_count"], second["fail_count"]) == (1, 2)
    assert second["next_try_at"] > first["next_try_at"]
    assert library_meta.meta_by_appid(conn)[440]["review_percent"] == 97  # old data kept
    assert 440 not in library_meta.due_appids(conn, now=NOW)  # backing off
    later = NOW + timedelta(days=8)
    assert 440 in library_meta.due_appids(conn, now=later)


# --------------------------------------------------------------------------
# The fill: gates, order, bail-out
# --------------------------------------------------------------------------


def test_fill_is_a_no_op_unless_every_gate_is_on(tmp_path: Path, conn: sqlite3.Connection) -> None:
    add_app(conn, 440)
    fake = FakeOracle({440: payload(440, **FULL)})
    switch_off = enabled_settings(tmp_path)  # oracle on, own switch default off
    oracle_off = dataclasses.replace(off_settings(tmp_path), library_meta_enabled=True)
    for settings in (switch_off, oracle_off):
        result = library_meta.fill(conn, settings, now=lambda: NOW, fetch=fake)
        assert result == library_meta.FetchRun()
    assert fake.asked == []
    assert library_meta.meta_by_appid(conn) == {}


def test_fill_fetches_due_apps_in_order_and_stores_them(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    for appid in (30, 10, 20):
        add_app(conn, appid)
    fake = FakeOracle({a: payload(a, **FULL) for a in (10, 20, 30)})
    result = library_meta.fill(conn, meta_settings(tmp_path), now=lambda: NOW, fetch=fake)
    assert fake.asked == [10, 20, 30]
    assert (result.selected, result.ok, result.failed) == (3, 3, 0)
    assert set(library_meta.meta_by_appid(conn)) == {10, 20, 30}
    # A second pass has nothing due: resumable, no repeat requests.
    again = library_meta.fill(conn, meta_settings(tmp_path), now=lambda: NOW, fetch=fake)
    assert again.selected == 0 and fake.asked == [10, 20, 30]


def test_fill_stops_after_repeated_failures_and_resumes_after_backoff(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    for appid in range(1, 9):
        add_app(conn, appid)
    fake = FakeOracle({})  # every request fails like a 429
    result = library_meta.fill(conn, meta_settings(tmp_path), now=lambda: NOW, fetch=fake)
    assert result.bailed_out and result.failed == library_meta.CONSECUTIVE_FAILURE_LIMIT
    assert fake.asked == [1, 2, 3, 4, 5]
    # Next run: the five failed apps back off, the untouched ones come next.
    fake.asked.clear()
    library_meta.fill(conn, meta_settings(tmp_path), now=lambda: NOW, fetch=fake)
    assert fake.asked == [6, 7, 8]


def test_fill_stops_when_the_window_closes(tmp_path: Path, conn: sqlite3.Connection) -> None:
    for appid in (1, 2, 3):
        add_app(conn, appid)
    fake = FakeOracle({a: payload(a, **FULL) for a in (1, 2, 3)})
    result = library_meta.fill(
        conn,
        meta_settings(tmp_path),
        now=lambda: NOW,
        fetch=fake,
        should_continue=lambda: len(fake.asked) < 2,
    )
    assert fake.asked == [1, 2] and result.ok == 2


# --------------------------------------------------------------------------
# The runner and the scheduler tick
# --------------------------------------------------------------------------


def test_runner_gates_window_and_switches(tmp_path: Path) -> None:
    path = str(tmp_path / "vault.db")
    init_db(path)
    fake = FakeOracle()
    runner = library_meta.LibraryMetaRunner(path, fetch=fake, pause_seconds=0)
    window = parse_window("03:00-07:00")
    clock = lambda: NOW  # noqa: E731
    assert runner.maybe_start(enabled_settings(tmp_path), window, NOW, clock) == "disabled"
    on = meta_settings(tmp_path)
    assert runner.maybe_start(on, None, NOW, clock) == "no-window"
    noon = NOW.replace(hour=12)
    assert runner.maybe_start(on, window, noon, clock) == "outside-window"
    assert fake.asked == []


def test_after_a_bail_out_the_runner_does_not_restart_on_the_next_tick(
    tmp_path: Path,
) -> None:
    settings = meta_settings(tmp_path)
    init_db(settings.db_path)
    conn = get_connection(settings.db_path)
    for appid in range(1, 12):
        add_app(conn, appid)
    conn.close()
    fake = FakeOracle({})  # every request fails
    runner = library_meta.LibraryMetaRunner(
        settings.db_path, fetch=fake, pause_seconds=0
    )
    window = parse_window("03:00-07:00")
    clock = lambda: NOW  # noqa: E731
    assert runner.maybe_start(settings, window, NOW, clock) == "started"
    assert runner.wait(10)
    assert runner.last_run.bailed_out
    asked = list(fake.asked)
    assert runner.maybe_start(settings, window, NOW + timedelta(minutes=1), clock) == "idle"
    assert runner.maybe_start(settings, window, NOW + timedelta(minutes=2), clock) == "idle"
    assert fake.asked == asked  # no further requests to a failing oracle


def test_the_scheduler_tick_fills_inside_the_window_and_only_then(tmp_path: Path) -> None:
    settings = meta_settings(tmp_path)
    init_db(settings.db_path)
    conn = get_connection(settings.db_path)
    add_app(conn, 440)
    fake = FakeOracle({440: payload(440, **FULL)})

    def sched(clock_now: datetime) -> PrefillScheduler:
        return PrefillScheduler(
            settings,
            clock=lambda: clock_now,
            oracle_fetch=fake,
            update_check_pause_seconds=0,
        )

    try:
        closed = sched(datetime(2026, 10, 15, 12, 0).astimezone())
        closed._tick(conn)
        assert closed._library_meta.wait(10)
        assert library_meta.meta_by_appid(conn) == {}

        opened = sched(datetime(2026, 10, 15, 4, 0).astimezone())
        opened._tick(conn)
        assert opened._library_meta.wait(10)
        assert library_meta.meta_by_appid(conn)[440]["vr"] is False
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------


def test_the_switch_is_off_by_default_and_env_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "k")
    monkeypatch.delenv("VAULT_LIBRARY_META", raising=False)
    assert Settings.from_env().library_meta_enabled is False
    monkeypatch.setenv("VAULT_LIBRARY_META", "true")
    assert Settings.from_env().library_meta_enabled is True


def test_patching_the_switch_through_the_settings_api_is_refused(
    tmp_path: Path,
) -> None:
    settings = off_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.patch(
            "/v1/settings", json={"library_meta_enabled": True}, headers=AUTH
        )
    assert response.status_code == 422
    assert "environment-only" in response.json()["detail"]


# --------------------------------------------------------------------------
# GET /v1/games: filters and sort
# --------------------------------------------------------------------------


def seed_library(settings: Settings) -> None:
    """Five apps: 1 co-op RPG 2020 rated 95, 2 single-player RPG 2015 rated
    70, 3 VR action 2022 rated 85, 4 known but without a rating or year,
    5 never fetched (unknown)."""
    init_db(settings.db_path)
    conn = get_connection(settings.db_path)
    try:
        for appid, name in ((1, "Zeta"), (2, "alpha"), (3, "Mid"), (4, "Delta"), (5, "Echo")):
            add_app(conn, appid, name)
        answers = {
            1: dict(category_2="1", category_9="1", genres={"0": "3"},
                    review_percentage="95", steam_release_date="1577836800"),
            2: dict(category_2="1", genres={"0": "3"}, review_percentage="70",
                    steam_release_date="1420070400"),  # 2015-01-01
            3: dict(category_53="1", genres={"0": "1"}, review_percentage="85",
                    steam_release_date="1641081600"),  # 2022-01-02
            4: dict(category_2="1", genres={"0": "1"}),
        }
        for appid, common in answers.items():
            library_meta.store_meta(
                conn,
                library_meta.parse_app_meta(appid, payload(appid, **common)),
                fetched_at="2026-10-14T04:00:00Z",
            )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def games_client(tmp_path: Path):
    settings = off_settings(tmp_path)
    seed_library(settings)
    with TestClient(create_app(settings)) as client:
        yield client


def ids(response) -> list[int]:
    assert response.status_code == 200, response.text
    return [g["appid"] for g in response.json()]


def test_no_filter_is_unchanged_and_carries_meta_only_where_known(games_client) -> None:
    response = games_client.get("/v1/games", headers=AUTH)
    assert ids(response) == [1, 2, 3, 4, 5]
    assert "X-Meta-Unknown-Excluded" not in response.headers
    by_id = {g["appid"]: g for g in response.json()}
    assert by_id[1]["meta"]["coop"] is True and by_id[1]["meta"]["release_year"] == 2020
    assert by_id[1]["meta"]["genres"] == [3]
    assert by_id[5]["meta"] is None  # unknown, not an empty object


def test_category_filters_and_all_must_hold(games_client) -> None:
    assert ids(games_client.get("/v1/games?category=single", headers=AUTH)) == [1, 2, 4]
    assert ids(games_client.get("/v1/games?category=coop", headers=AUTH)) == [1]
    assert ids(games_client.get("/v1/games?category=vr", headers=AUTH)) == [3]
    both = games_client.get("/v1/games?category=single&category=coop", headers=AUTH)
    assert ids(both) == [1]


def test_genre_is_any_of(games_client) -> None:
    assert ids(games_client.get("/v1/games?genre=3", headers=AUTH)) == [1, 2]
    assert ids(games_client.get("/v1/games?genre=3&genre=1", headers=AUTH)) == [1, 2, 3, 4]


def test_rating_and_year_range(games_client) -> None:
    assert ids(games_client.get("/v1/games?min_rating=85", headers=AUTH)) == [1, 3]
    assert ids(games_client.get("/v1/games?year_from=2018&year_to=2021", headers=AUTH)) == [1]
    assert ids(games_client.get("/v1/games?year_to=2015", headers=AUTH)) == [2]
    combined = games_client.get("/v1/games?genre=3&min_rating=90", headers=AUTH)
    assert ids(combined) == [1]


def test_unknown_is_left_out_but_counted_and_included_on_request(games_client) -> None:
    response = games_client.get("/v1/games?min_rating=85", headers=AUTH)
    assert ids(response) == [1, 3]
    # App 4 (fetched, no rating) and app 5 (never fetched) are unknown for
    # this filter: left out, and said so.
    assert response.headers["X-Meta-Unknown-Excluded"] == "2"

    kept = games_client.get("/v1/games?min_rating=85&include_unknown=true", headers=AUTH)
    assert ids(kept) == [1, 3, 4, 5]  # 2 is KNOWN to be 70: still excluded
    assert kept.headers["X-Meta-Unknown-Excluded"] == "0"


def test_unknown_genre_bucket(games_client) -> None:
    kept = games_client.get("/v1/games?genre=3&include_unknown=true", headers=AUTH)
    assert ids(kept) == [1, 2, 5]  # 5 has no genre rows at all


def test_sort_by_rating_and_year_puts_unknown_last_in_both_directions(games_client) -> None:
    assert ids(games_client.get("/v1/games?sort=rating", headers=AUTH)) == [1, 3, 2, 4, 5]
    assert ids(games_client.get("/v1/games?sort=rating&order=asc", headers=AUTH)) == [2, 3, 1, 4, 5]
    assert ids(games_client.get("/v1/games?sort=year", headers=AUTH)) == [3, 1, 2, 4, 5]
    assert ids(games_client.get("/v1/games?sort=year&order=asc", headers=AUTH)) == [2, 1, 3, 4, 5]


def test_sort_by_name_is_case_insensitive_and_appid_can_descend(games_client) -> None:
    assert ids(games_client.get("/v1/games?sort=name", headers=AUTH)) == [2, 4, 5, 3, 1]
    assert ids(games_client.get("/v1/games?sort=appid&order=desc", headers=AUTH)) == [5, 4, 3, 2, 1]


def test_filter_and_sort_combine(games_client) -> None:
    response = games_client.get("/v1/games?category=single&sort=year", headers=AUTH)
    assert ids(response) == [1, 2, 4]


@pytest.mark.parametrize(
    "query",
    [
        "category=nope",
        "genre=abc",
        "min_rating=101",
        "year_from=2022&year_to=2015",
        "sort=size",
        "order=sideways",
    ],
)
def test_bad_parameters_are_422(games_client, query: str) -> None:
    assert games_client.get(f"/v1/games?{query}", headers=AUTH).status_code == 422
