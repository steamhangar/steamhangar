"""WP API-FIX-5: real cover art via Steam's store item lookup.

The lookup is never made for real here (the test instance and CI unit tests
are offline by design): the outbound request is captured at the urllib
opener, and the refresher is driven with fake fetchers. Response fixtures are
synthetic, modeled on GetItems answers measured 2026-10-09 (apps 730,
3527290, 2807960, 228980).
"""

from __future__ import annotations

import io
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from vault_api import cover_art, steam_relay, webui
from vault_api.config import Settings
from vault_api.db import SCHEMA_VERSION, get_connection, init_db
from vault_api.main import create_app

AUTH = {"X-Api-Key": TEST_API_KEY}
NOW = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)

HASHED = (
    "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/3527290/"
    "480bd879ac737921bfa2529a6fea15961267ad21/library_600x900.jpg?t=1790591892"
)
PLAIN = (
    "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/730/"
    "library_600x900.jpg?t=1789251637"
)


def _item(appid: int, fmt: str | None, capsule: str | None, success: int = 1) -> dict:
    item: dict = {"item_type": 0, "id": appid, "success": success}
    if success != 1:
        # Real shape for an unknown/hidden app (measured 2026-10-10).
        item.update({"visible": False, "name": "", "store_url_path": "app/0/", "appid": 0})
    if success == 1:
        item["appid"] = appid
        item["name"] = f"Synthetic {appid}"
        assets: dict = {"header": "header.jpg"}
        if fmt is not None:
            assets["asset_url_format"] = fmt
        if capsule is not None:
            assets["library_capsule"] = capsule
        item["assets"] = assets
    return item


def _payload(*items: dict) -> bytes:
    return json.dumps({"response": {"store_items": list(items)}}).encode()


REAL_SHAPED = _payload(
    _item(730, "steam/apps/730/${FILENAME}?t=1789251637", "library_600x900.jpg"),
    _item(
        3527290,
        "steam/apps/3527290/${FILENAME}?t=1790591892",
        "480bd879ac737921bfa2529a6fea15961267ad21/library_600x900.jpg",
    ),
    _item(
        2807960,
        "steam/apps/2807960/${FILENAME}?t=1790375732",
        "289b1c193f9730a0d4ea4dbf912219e46cd1a8a3/library_capsule.jpg",
    ),
    _item(999999, None, None, success=15),
)


# --------------------------------------------------------------------------
# URL building and validation
# --------------------------------------------------------------------------


def test_build_cover_url_real_shapes() -> None:
    assert (
        cover_art.build_cover_url(
            3527290,
            "steam/apps/3527290/${FILENAME}?t=1790591892",
            "480bd879ac737921bfa2529a6fea15961267ad21/library_600x900.jpg",
        )
        == HASHED
    )
    assert (
        cover_art.build_cover_url(
            730, "steam/apps/730/${FILENAME}?t=1789251637", "library_600x900.jpg"
        )
        == PLAIN
    )
    # Without the cache buster too.
    assert cover_art.build_cover_url(730, "steam/apps/730/${FILENAME}", "x/library.jpg") is None
    assert cover_art.build_cover_url(730, "steam/apps/730/${FILENAME}", "library.jpg") == (
        "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/730/library.jpg"
    )


@pytest.mark.parametrize(
    ("fmt", "capsule"),
    [
        ("steam/apps/731/${FILENAME}?t=1", "library_600x900.jpg"),  # other appid
        ("steam/apps/730/${FILENAME}?t=1&x=2", "library_600x900.jpg"),
        ("//evil.example/${FILENAME}", "library_600x900.jpg"),
        ("steam/apps/730/library_600x900.jpg", "library_600x900.jpg"),  # no placeholder
        ("steam/apps/730/${FILENAME}", "../../x/library_600x900.jpg"),
        ("steam/apps/730/${FILENAME}", "abcdef12/../library_600x900.jpg"),
        ("steam/apps/730/${FILENAME}", "library_600x900.jpg?x=@evil.example"),
        ("steam/apps/730/${FILENAME}", "library_600x900.exe"),
        ("steam/apps/730/${FILENAME}", "/library_600x900.jpg"),
        ("steam/apps/730/${FILENAME}", "library_600x900.jpg#frag"),
        ("steam/apps/730/${FILENAME}", "líbrary.jpg"),
        (None, "library_600x900.jpg"),
        ("steam/apps/730/${FILENAME}", 5),
    ],
)
def test_build_cover_url_rejects_unknown_shapes(fmt, capsule) -> None:
    assert cover_art.build_cover_url(730, fmt, capsule) is None


def test_valid_cover_url_allows_exactly_the_two_asset_hosts() -> None:
    # Literal hosts (LEARNINGS: pin security constants as literals).
    assert cover_art.valid_cover_url(HASHED) == HASHED
    legacy = "https://cdn.akamai.steamstatic.com/steam/apps/440/library_600x900.jpg"
    assert cover_art.valid_cover_url(legacy) == legacy
    assert cover_art.ALLOWED_ASSET_HOSTS == frozenset(
        {"shared.akamai.steamstatic.com", "cdn.akamai.steamstatic.com"}
    )


@pytest.mark.parametrize(
    "value",
    [
        "http://shared.akamai.steamstatic.com/store_item_assets/x.jpg",
        "https://evil.example/x.jpg",
        "https://shared.akamai.steamstatic.com.evil.example/x.jpg",
        "https://avatars.steamstatic.com/x.jpg",
        "https://SHARED.akamai.steamstatic.com/x.jpg",
        "https://u:p@shared.akamai.steamstatic.com/x.jpg",
        "https://shared.akamai.steamstatic.com:8443/x.jpg",
        "https://shared.akamai.steamstatic.com/x.jpg#f",
        "https://shared.akamai.steamstatic.com/x y.jpg",
        "https://shared.akamai.steamstatic.com\\@evil.example/x.jpg",
        "javascript:alert(1)",
        "data:image/png;base64,AAAA",
        "https://shared.akamai.steamstatic.com/" + "a" * 600,
        "",
        None,
        42,
    ],
)
def test_valid_cover_url_rejects(value) -> None:
    assert cover_art.valid_cover_url(value) is None


# --------------------------------------------------------------------------
# The request (captured at the opener -- LEARNINGS 4a.6r)
# --------------------------------------------------------------------------


class _FakeResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_fetch_items_sends_exactly_the_keyless_getitems_request(monkeypatch) -> None:
    captured = []

    def fake_open(request, timeout=None):
        captured.append(request)
        return _FakeResponse(REAL_SHAPED)

    monkeypatch.setattr(steam_relay._OPENER, "open", fake_open)
    body = cover_art.fetch_items([730, 3527290])

    assert body == REAL_SHAPED
    (request,) = captured
    parts = urlsplit(request.full_url)
    assert parts.scheme == "https"
    assert parts.netloc == "api.steampowered.com"
    assert parts.path == "/IStoreBrowseService/GetItems/v1/"
    assert request.get_method() == "GET"
    query = parse_qs(parts.query)
    assert set(query) == {"input_json"}  # no key, no steamid
    assert json.loads(query["input_json"][0]) == {
        "ids": [{"appid": 730}, {"appid": 3527290}],
        "context": {"language": "english", "country_code": "US"},
        "data_request": {"include_assets": True},
    }


# --------------------------------------------------------------------------
# Parsing (hostile input)
# --------------------------------------------------------------------------


def test_parse_items_real_shaped_answer() -> None:
    result = cover_art.parse_items(REAL_SHAPED, [730, 3527290, 2807960, 999999])
    assert result[730] == PLAIN
    assert result[3527290] == HASHED
    assert result[2807960].endswith(
        "/steam/apps/2807960/289b1c193f9730a0d4ea4dbf912219e46cd1a8a3/library_capsule.jpg?t=1790375732"
    )
    assert result[999999] is None  # success 15


def test_parse_items_ignores_unrequested_and_mismatched_items() -> None:
    mismatched = _item(730, "steam/apps/730/${FILENAME}", "library.jpg")
    mismatched["appid"] = 731
    payload = _payload(
        _item(440, "steam/apps/440/${FILENAME}", "library.jpg"),  # not asked
        mismatched,
        "garbage",
        {"id": True, "success": 1},
    )
    assert cover_art.parse_items(payload, [730]) == {}


def test_parse_items_success_flag_must_be_int_one() -> None:
    item = _item(730, "steam/apps/730/${FILENAME}", "library.jpg")
    item["success"] = True
    assert cover_art.parse_items(_payload(item), [730]) == {730: None}


def test_parse_items_assets_without_capsule_is_no_cover() -> None:
    payload = _payload(_item(730, "steam/apps/730/${FILENAME}", None))
    assert cover_art.parse_items(payload, [730]) == {730: None}


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",
        b"\xff\xfe",
        b"[]",
        b'{"response": []}',
        b'{"response": {"store_items": {}}}',
        b"[" * 100_000 + b"]" * 100_000,
    ],
)
def test_parse_items_unusable_documents_raise_relay_error(payload) -> None:
    with pytest.raises(steam_relay.SteamRelayError):
        cover_art.parse_items(payload, [730])


def test_parse_items_empty_response_is_empty() -> None:
    assert cover_art.parse_items(b'{"response": {}}', [730]) == {}


# --------------------------------------------------------------------------
# Refresh: storage, backoff, quiet failures
# --------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path) -> str:
    path = str(tmp_path / "vault.db")
    init_db(path)
    return path


def _add_apps(db_path: str, *appids: int) -> None:
    conn = get_connection(db_path)
    try:
        for appid in appids:
            conn.execute("INSERT INTO apps (appid, status) VALUES (?, 'idle')", (appid,))
        conn.commit()
    finally:
        conn.close()


def _rows(db_path: str) -> dict[int, sqlite3.Row]:
    conn = get_connection(db_path)
    try:
        return {r["appid"]: r for r in conn.execute("SELECT * FROM app_cover_art")}
    finally:
        conn.close()


def _refresh(db_path: str, now: datetime, fetch) -> cover_art.RefreshOutcome:
    conn = get_connection(db_path)
    try:
        return cover_art.refresh_once(conn, now, fetch)
    finally:
        conn.close()


def test_refresh_stores_found_and_none_then_waits(db) -> None:
    _add_apps(db, 730, 3527290, 999999, 228980)
    asked: list[list[int]] = []

    def fetch(appids):
        asked.append(list(appids))
        return REAL_SHAPED

    outcome = _refresh(db, NOW, fetch)
    # The tool app 228980 is never asked about.
    assert asked == [[730, 999999, 3527290]]
    assert (outcome.found, outcome.none, outcome.failed) == (2, 1, 0)
    rows = _rows(db)
    assert rows[3527290]["cover_url"] == HASHED
    assert rows[3527290]["outcome"] == "found"
    assert rows[3527290]["next_check_at"] == "2026-11-09T12:00:00Z"  # +30 days
    assert rows[999999]["cover_url"] is None
    assert rows[999999]["outcome"] == "none"
    assert rows[999999]["next_check_at"] == "2026-10-17T12:00:00Z"  # +7 days
    assert 228980 not in rows

    # Nothing due right after: no second call.
    assert _refresh(db, NOW + timedelta(hours=1), fetch).requested == ()
    assert len(asked) == 1
    # The "none" app is due again after 7 days, the found ones are not.
    assert _refresh(db, NOW + timedelta(days=8), fetch).requested == (999999,)


def test_recheck_answering_no_cover_keeps_the_found_url(db) -> None:
    _add_apps(db, 3527290)
    _refresh(db, NOW, lambda appids: REAL_SHAPED)
    assert _rows(db)[3527290]["cover_url"] == HASHED

    delisted = _payload(_item(3527290, None, None, success=15))
    later = NOW + timedelta(days=31)
    outcome = _refresh(db, later, lambda appids: delisted)
    assert outcome.requested == (3527290,)
    row = _rows(db)[3527290]
    assert row["cover_url"] == HASHED  # kept, not overwritten with NULL
    assert row["outcome"] == "found"
    assert row["next_check_at"] == "2026-11-17T12:00:00Z"  # +7 days


def test_refresh_batches_at_most_batch_size(db) -> None:
    _add_apps(db, *range(1, cover_art.BATCH_SIZE + 6))
    asked: list[int] = []

    def fetch(appids):
        asked.append(len(appids))
        return _payload()

    _refresh(db, NOW, fetch)
    assert asked == [cover_art.BATCH_SIZE]


def test_refresh_failure_backs_off_and_keeps_a_found_url(db) -> None:
    _add_apps(db, 3527290)
    _refresh(db, NOW, lambda appids: REAL_SHAPED)

    def offline(appids):
        raise steam_relay.SteamRelayError("https://api.steampowered.com/... is unreachable")

    later = NOW + timedelta(days=31)
    outcome = _refresh(db, later, offline)
    assert outcome.failed == 1 and outcome.error
    row = _rows(db)[3527290]
    assert row["outcome"] == "failed"
    assert row["attempts"] == 1
    assert row["cover_url"] == HASHED  # kept
    assert row["next_check_at"] == "2026-11-10T12:15:00Z"  # +15 min

    _refresh(db, later + timedelta(minutes=15), offline)
    row = _rows(db)[3527290]
    assert row["attempts"] == 2
    assert row["next_check_at"] == "2026-11-10T12:45:00Z"  # 12:15 + 30 min

    # Success resets the counter.
    _refresh(db, later + timedelta(hours=2), lambda appids: REAL_SHAPED)
    row = _rows(db)[3527290]
    assert (row["outcome"], row["attempts"]) == ("found", 0)


def test_failure_backoff_doubles_and_caps() -> None:
    assert cover_art.failure_backoff(1) == timedelta(minutes=15)
    assert cover_art.failure_backoff(2) == timedelta(minutes=30)
    assert cover_art.failure_backoff(4) == timedelta(hours=2)
    assert cover_art.failure_backoff(99) == timedelta(hours=24)


def test_unanswered_apps_count_as_failed(db) -> None:
    _add_apps(db, 730, 440)
    outcome = _refresh(db, NOW, lambda appids: REAL_SHAPED)  # 440 not in it
    assert outcome.failed == 1
    rows = _rows(db)
    assert rows[440]["outcome"] == "failed"
    assert rows[730]["outcome"] == "found"


def test_refresher_logs_one_warning_per_failure_streak(db, caplog) -> None:
    _add_apps(db, 730)
    clock = [NOW]
    calls = []

    def offline(appids):
        calls.append(appids)
        raise steam_relay.SteamRelayError("https://api.steampowered.com/x is unreachable")

    refresher = cover_art.CoverArtRefresher(db, fetch=offline, now=lambda: clock[0])
    caplog.set_level(logging.DEBUG, logger="vault_api.cover_art")

    refresher.tick()
    # Paused: a tick inside the backoff sends nothing.
    clock[0] = NOW + timedelta(minutes=5)
    assert refresher.tick() is None
    assert len(calls) == 1
    clock[0] = NOW + timedelta(minutes=20)
    refresher.tick()
    assert len(calls) == 2

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1

    refresher._fetch = lambda appids: REAL_SHAPED
    clock[0] = NOW + timedelta(hours=2)
    refresher.tick()
    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert any("works again" in r.getMessage() for r in infos)
    assert _rows(db)[730]["cover_url"] == PLAIN


def test_refresher_thread_starts_and_stops_without_calling_out(db) -> None:
    calls = []
    refresher = cover_art.CoverArtRefresher(db, fetch=lambda a: calls.append(a) or b"")
    refresher.start()
    refresher.stop()
    assert calls == []  # conftest pushes the first tick an hour out


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------


def test_schema_v18_adds_app_cover_art(tmp_path) -> None:
    assert SCHEMA_VERSION == 18
    path = str(tmp_path / "old.db")
    init_db(path)
    conn = get_connection(path)
    try:
        conn.execute("DROP TABLE app_cover_art")
        conn.execute("UPDATE schema_version SET version = 17")
        conn.execute("INSERT INTO apps (appid, name, status) VALUES (440, 'Keep', 'done')")
        conn.commit()
    finally:
        conn.close()

    init_db(path)
    init_db(path)
    conn = get_connection(path)
    try:
        (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
        columns = {r["name"]: r["type"] for r in conn.execute("PRAGMA table_info(app_cover_art)")}
        app = conn.execute("SELECT name FROM apps WHERE appid = 440").fetchone()
    finally:
        conn.close()
    assert version == 18
    assert columns == {
        "appid": "INTEGER",
        "cover_url": "TEXT",
        "outcome": "TEXT",
        "attempts": "INTEGER",
        "checked_at": "TEXT",
        "next_check_at": "TEXT",
    }
    assert app["name"] == "Keep"


# --------------------------------------------------------------------------
# API: cover_url in GET /v1/games and /v1/games/{appid}
# --------------------------------------------------------------------------


def _store(settings: Settings, appid: int, url: str | None) -> None:
    conn = get_connection(settings.db_path)
    try:
        conn.execute(
            "INSERT INTO app_cover_art (appid, cover_url, outcome, attempts, checked_at, "
            "next_check_at) VALUES (?, ?, 'found', 0, '2026-10-10T00:00:00Z', "
            "'2026-11-10T00:00:00Z')",
            (appid, url),
        )
        conn.commit()
    finally:
        conn.close()


def _seed(client: TestClient, depotid: int, appid: int) -> None:
    response = client.put(
        f"/v1/mapping/{depotid}", json={"appid": appid, "app_name": None}, headers=AUTH
    )
    assert response.status_code == 200


def test_games_expose_cover_url_and_null_when_unknown(settings: Settings) -> None:
    client = TestClient(create_app(settings))
    _seed(client, 3527291, 3527290)
    _seed(client, 731, 730)
    _store(settings, 3527290, HASHED)

    games = {g["appid"]: g for g in client.get("/v1/games", headers=AUTH).json()}
    assert games[3527290]["cover_url"] == HASHED
    assert games[730]["cover_url"] is None

    assert client.get("/v1/games/3527290", headers=AUTH).json()["cover_url"] == HASHED
    assert client.get("/v1/games/730", headers=AUTH).json()["cover_url"] is None


def test_games_revalidate_a_tampered_stored_url(settings: Settings) -> None:
    client = TestClient(create_app(settings))
    _seed(client, 441, 440)
    _store(settings, 440, "https://evil.example/library_600x900.jpg")

    (game,) = client.get("/v1/games", headers=AUTH).json()
    assert game["cover_url"] is None
    assert client.get("/v1/games/440", headers=AUTH).json()["cover_url"] is None


def test_lifespan_starts_and_stops_the_refresher(settings: Settings) -> None:
    app = create_app(settings)
    refresher = app.state.cover_art_refresher
    with TestClient(app):
        assert refresher._thread is not None and refresher._thread.is_alive()
    assert refresher._thread is None


def test_csp_img_src_allows_both_asset_hosts_and_nothing_else() -> None:
    directives = dict(
        part.strip().split(" ", 1) for part in webui._CSP.split(";") if part.strip()
    )
    allowed = set(directives["img-src"].split()) - {"'self'", "data:"}
    assert allowed == {
        "https://cdn.akamai.steamstatic.com",
        "https://shared.akamai.steamstatic.com",
    }
