"""WP D10 COVER-CACHE-1: covers stored by vault-api and served over the LAN.

No network: downloads go through fake fetchers, and the one test of the real
``fetch_image`` captures the request at the urllib opener. Image bodies are
synthetic (a magic number plus filler), never real artwork.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import logging
import os
import sqlite3
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from vault_api import body_guard, cover_cache
from vault_api.config import Settings
from vault_api.db import SCHEMA_VERSION, get_connection, init_db
from vault_api.main import create_app

AUTH = {"X-Api-Key": TEST_API_KEY}
NOW = datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)
URL_730 = (
    "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/730/"
    "library_600x900.jpg?t=1789251637"
)
URL_440 = "https://cdn.akamai.steamstatic.com/steam/apps/440/library_600x900.jpg"
JPEG = b"\xff\xd8\xff\xe0" + b"synthetic-jpeg" * 50
PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic-png" * 20
WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"x" * 40


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path) -> str:
    path = str(tmp_path / "vault.db")
    init_db(path)
    return path


def _conn(db_path: str) -> sqlite3.Connection:
    return get_connection(db_path)


def _track(db_path: str, appid: int, cover_url: str | None) -> None:
    conn = _conn(db_path)
    try:
        conn.execute("INSERT OR IGNORE INTO apps (appid, status) VALUES (?, 'idle')", (appid,))
        conn.execute(
            "INSERT OR REPLACE INTO app_cover_art (appid, cover_url, outcome, attempts, "
            "checked_at, next_check_at) VALUES (?, ?, 'found', 0, '2026-10-10T00:00:00Z', "
            "'2026-11-10T00:00:00Z')",
            (appid, cover_url),
        )
        conn.commit()
    finally:
        conn.close()


def _image(data: bytes) -> cover_cache.FetchedImage:
    return cover_cache.check_image(data)


def _download(db_path: str, cache_dir: str, fetch, now: datetime = NOW, limit: int = 20):
    conn = _conn(db_path)
    try:
        return cover_cache.download_once(conn, cache_dir, now, fetch, limit)
    finally:
        conn.close()


def _file_rows(db_path: str) -> dict[int, sqlite3.Row]:
    conn = _conn(db_path)
    try:
        return {r["appid"]: r for r in conn.execute("SELECT * FROM app_cover_file")}
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Names, signatures, ETags
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["", "0", "00", "0730", "01", "-1", "+1", " 1", "1_0", "١", "12345678901", "4294967296", "1.0", "a"],
)
def test_parse_appid_rejects(text) -> None:
    assert cover_cache.parse_appid(text) is None


def test_parse_appid_accepts_plain_digits() -> None:
    assert cover_cache.parse_appid("730") == 730
    assert cover_cache.parse_appid("4294967295") == 4294967295


@pytest.mark.parametrize("bad", ["730", 0, -1, True, 2**32, 1.5])
def test_file_path_only_takes_an_int_appid(tmp_path, bad) -> None:
    with pytest.raises(ValueError):
        cover_cache.file_path(str(tmp_path), bad)


def test_file_path_is_a_direct_child_named_by_appid(tmp_path) -> None:
    path = cover_cache.file_path(str(tmp_path), 730)
    assert os.path.dirname(path) == str(tmp_path)
    assert os.path.basename(path) == "730.img"


def test_cache_dir_sits_next_to_the_database(tmp_path) -> None:
    db_path = str(tmp_path / "data" / "vault.db")
    assert cover_cache.cache_dir_for(db_path) == str(tmp_path / "data" / "covers")


def test_signature_depends_on_key_appid_and_content() -> None:
    sha = "a" * 64
    base = cover_cache.cover_signature("k1", 730, sha)
    assert cover_cache.valid_signature_text(base)
    assert cover_cache.cover_signature("k2", 730, sha) != base
    assert cover_cache.cover_signature("k1", 731, sha) != base
    assert cover_cache.cover_signature("k1", 730, "b" * 64) != base
    assert cover_cache.cover_signature("k1", 730, sha) == base


@pytest.mark.parametrize(
    "header,hit",
    [
        (None, False),
        ("", False),
        ('"abc"', True),
        ('W/"abc"', True),
        ('"x", "abc"', True),
        ("*", True),
        ('"abcd"', False),
        ("abc", False),
    ],
)
def test_if_none_match(header, hit) -> None:
    assert cover_cache.if_none_match_hits(header, "abc") is hit


@pytest.mark.parametrize(
    "data,ctype",
    [(JPEG, "image/jpeg"), (PNG, "image/png"), (WEBP, "image/webp")],
)
def test_check_image_sniffs_the_type(data, ctype) -> None:
    assert cover_cache.check_image(data).content_type == ctype


@pytest.mark.parametrize("data", [b"", b"<html>nope</html>", b"GIF89a....", b"RIFF\0\0\0\0AVI "])
def test_check_image_rejects_non_images(data) -> None:
    with pytest.raises(cover_cache.CoverFetchError):
        cover_cache.check_image(data)


def test_check_image_rejects_oversized(monkeypatch) -> None:
    monkeypatch.setattr(cover_cache, "MAX_FILE_BYTES", 100)
    with pytest.raises(cover_cache.CoverFetchError):
        cover_cache.check_image(b"\xff\xd8\xff" + b"x" * 100)


# --------------------------------------------------------------------------
# fetch_image: host pinned, no redirects, proxy honoured
# --------------------------------------------------------------------------


class _FakeResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_fetch_image_sends_one_get_to_the_validated_url(monkeypatch) -> None:
    seen = []

    def fake_open(request, timeout):
        seen.append((request.full_url, request.get_method(), timeout))
        return _FakeResponse(JPEG)

    monkeypatch.setattr(cover_cache._OPENER, "open", fake_open)
    image = cover_cache.fetch_image(URL_730)
    assert image.data == JPEG and image.content_type == "image/jpeg"
    assert seen == [(URL_730, "GET", cover_cache.FETCH_TIMEOUT_SECONDS)]


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/library_600x900.jpg",
        "http://cdn.akamai.steamstatic.com/steam/apps/1/x.jpg",
        "https://cdn.akamai.steamstatic.com:444/steam/apps/1/x.jpg",
        "https://u@cdn.akamai.steamstatic.com/steam/apps/1/x.jpg",
    ],
)
def test_fetch_image_refuses_urls_outside_the_two_hosts(monkeypatch, url) -> None:
    monkeypatch.setattr(
        cover_cache._OPENER, "open", lambda *a, **k: pytest.fail("must not open")
    )
    with pytest.raises(cover_cache.CoverFetchError):
        cover_cache.fetch_image(url)


def test_fetch_image_turns_http_errors_into_cover_fetch_error(monkeypatch) -> None:
    def fake_open(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 403, "Filtered", {}, None)

    monkeypatch.setattr(cover_cache._OPENER, "open", fake_open)
    with pytest.raises(cover_cache.CoverFetchError, match="HTTP 403"):
        cover_cache.fetch_image(URL_730)


def test_opener_refuses_redirects_and_keeps_the_proxy_handler() -> None:
    import urllib.request

    handlers = {type(h) for h in cover_cache._OPENER.handlers}
    assert cover_cache._RefuseRedirects in handlers
    assert urllib.request.HTTPRedirectHandler not in handlers
    # ProxyHandler is not asserted: build_opener always constructs one, and
    # it reads the proxy env vars right then (when this module is imported),
    # but OpenerDirector.add_handler only keeps a handler that has a
    # <scheme>_open method, which ProxyHandler gets only for a configured
    # proxy. The test environment sets none, so it is absent here; in the
    # container HTTPS_PROXY=http://vault-proxy:8888 is set before vault-api
    # starts, so it is present there (same mechanism as steam_relay._OPENER).
    assert cover_cache._RefuseRedirects().redirect_request(None, None, 302, "", {}, "x") is None


# --------------------------------------------------------------------------
# Download, store, bounds
# --------------------------------------------------------------------------


def test_download_stores_due_covers_atomically(db, tmp_path) -> None:
    cache_dir = str(tmp_path / "covers")
    _track(db, 730, URL_730)
    _track(db, 440, URL_440)
    _track(db, 570, None)  # no resolved cover: never downloaded
    fetched = []

    def fetch(url):
        fetched.append(url)
        return _image(JPEG if url == URL_730 else PNG)

    outcome = _download(db, cache_dir, fetch)
    assert outcome == cover_cache.DownloadOutcome(attempted=2, stored=2)
    assert sorted(fetched) == sorted([URL_730, URL_440])
    assert sorted(os.listdir(cache_dir)) == ["440.img", "730.img"]
    rows = _file_rows(db)
    assert rows[730]["sha256"] == hashlib.sha256(JPEG).hexdigest()
    assert rows[730]["content_type"] == "image/jpeg"
    assert rows[730]["size_bytes"] == len(JPEG)
    assert rows[730]["source_url"] == URL_730
    assert rows[440]["content_type"] == "image/png"

    # Nothing due any more.
    assert _download(db, cache_dir, lambda u: pytest.fail("not due")).attempted == 0


def test_a_changed_cover_url_is_downloaded_again(db, tmp_path) -> None:
    cache_dir = str(tmp_path / "covers")
    _track(db, 730, URL_730)
    _download(db, cache_dir, lambda u: _image(JPEG))
    new_url = URL_730.replace("t=1789251637", "t=1799999999")
    _track(db, 730, new_url)
    _download(db, cache_dir, lambda u: _image(PNG))
    row = _file_rows(db)[730]
    assert row["source_url"] == new_url
    assert row["sha256"] == hashlib.sha256(PNG).hexdigest()


def test_apps_without_an_apps_row_are_not_downloaded(db, tmp_path) -> None:
    conn = _conn(db)
    try:
        conn.execute(
            "INSERT INTO app_cover_art (appid, cover_url, outcome, attempts, checked_at, "
            "next_check_at) VALUES (99, ?, 'found', 0, 'x', 'y')",
            (URL_440,),
        )
        conn.commit()
    finally:
        conn.close()
    assert _download(db, str(tmp_path / "c"), lambda u: pytest.fail("untracked")).attempted == 0


def test_a_tampered_stored_cover_url_is_not_downloaded(db, tmp_path) -> None:
    _track(db, 730, "https://evil.example/x.jpg")
    assert _download(db, str(tmp_path / "c"), lambda u: pytest.fail("bad url")).attempted == 0


def test_failure_backs_off_ends_the_tick_and_keeps_a_stored_file(db, tmp_path) -> None:
    cache_dir = str(tmp_path / "covers")
    _track(db, 730, URL_730)
    _download(db, cache_dir, lambda u: _image(JPEG))
    _track(db, 730, URL_730 + "0")  # new art, but the download will fail
    _track(db, 440, URL_440)
    calls = []

    def offline(url):
        calls.append(url)
        raise cover_cache.CoverFetchError("unreachable")

    outcome = _download(db, cache_dir, offline)
    assert outcome.error is not None and len(calls) == 1  # stops at the first failure
    rows = _file_rows(db)
    failed = [appid for appid, row in rows.items() if row["attempts"] == 1]
    assert len(failed) == 1
    assert rows[730]["sha256"] == hashlib.sha256(JPEG).hexdigest()  # old file kept
    # Inside the backoff the failed app is skipped; the other one is tried.
    urls = {730: URL_730 + "0", 440: URL_440}
    later_calls = []
    later = _download(
        db, cache_dir, lambda u: (later_calls.append(u), _image(PNG))[1], NOW + timedelta(minutes=5)
    )
    assert later.error is None
    assert later_calls == [urls[a] for a in (730, 440) if a != failed[0]]
    # After the backoff it is tried again.
    again = []
    _download(db, cache_dir, lambda u: (again.append(u), _image(PNG))[1], NOW + timedelta(minutes=16))
    assert again == [urls[failed[0]]]


def test_not_an_image_counts_as_a_failure(db, tmp_path) -> None:
    _track(db, 730, URL_730)
    outcome = _download(
        db, str(tmp_path / "c"), lambda u: cover_cache.FetchedImage(b"<html>", "image/jpeg")
    )
    assert outcome.error is not None
    assert _file_rows(db)[730]["sha256"] is None
    assert not os.path.exists(tmp_path / "c" / "730.img")


def test_total_cap_defers_instead_of_storing(db, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cover_cache, "MAX_TOTAL_BYTES", len(JPEG) + 10)
    cache_dir = str(tmp_path / "covers")
    _track(db, 440, URL_440)
    _track(db, 730, URL_730)
    outcome = _download(db, cache_dir, lambda u: _image(JPEG))
    assert outcome.stored == 1 and outcome.capped == 1
    rows = _file_rows(db)
    stored = [a for a, r in rows.items() if r["sha256"] is not None]
    deferred = [a for a, r in rows.items() if r["sha256"] is None]
    assert len(stored) == 1 and len(deferred) == 1
    assert rows[deferred[0]]["next_try_at"] == "2026-10-14T12:00:00Z"
    assert os.listdir(cache_dir) == [f"{stored[0]}.img"]
    # Replacing the one stored file does not count itself against the cap.
    _track(db, stored[0], (URL_440 if stored[0] == 440 else URL_730) + "1")
    assert _download(db, cache_dir, lambda u: _image(JPEG)).stored == 1


def test_downloads_per_tick_are_bounded(db, tmp_path) -> None:
    for appid in range(1, 8):
        _track(db, appid, f"https://cdn.akamai.steamstatic.com/steam/apps/{appid}/x.jpg")
    assert _download(db, str(tmp_path / "c"), lambda u: _image(JPEG), limit=3).attempted == 3


# --------------------------------------------------------------------------
# Reconcile
# --------------------------------------------------------------------------


def test_reconcile_clears_missing_files_and_removes_strays(db, tmp_path) -> None:
    cache_dir = tmp_path / "covers"
    _track(db, 730, URL_730)
    _track(db, 440, URL_440)
    _download(db, str(cache_dir), lambda u: _image(JPEG))
    os.unlink(cache_dir / "440.img")
    (cache_dir / "999.img").write_bytes(JPEG)  # no row: removed
    dead = f".730.{'c' * 32}.tmp"
    (cache_dir / dead).write_bytes(b"x")  # stale temp of our shape: removed
    old = 1_000_000.0
    os.utime(cache_dir / dead, (old, old))
    fresh = f".731.{'a' * 32}.tmp"
    (cache_dir / fresh).write_bytes(b"x")  # fresh temp: kept
    foreign = [".notes.tmp", ".730.ABC.tmp", ".0730." + "b" * 32 + ".tmp", ".x.tmp"]
    for name in foreign:  # old, but not our exact temp shape: kept
        (cache_dir / name).write_bytes(b"x")
        os.utime(cache_dir / name, (old, old))
    (cache_dir / "0730.img").write_bytes(JPEG)  # leading zero: not our name, kept
    (cache_dir / "README").write_text("not ours")  # not our name: kept

    conn = _conn(db)
    try:
        cover_cache.reconcile(conn, str(cache_dir))
    finally:
        conn.close()
    assert sorted(os.listdir(cache_dir)) == sorted([fresh, "730.img", "README", "0730.img", *foreign])
    rows = _file_rows(db)
    assert rows[440]["sha256"] is None  # due again
    assert rows[730]["sha256"] is not None


def test_write_atomic_temp_names_match_the_shape_reconcile_removes(tmp_path, monkeypatch) -> None:
    seen = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append(os.path.basename(src))
        return real_replace(src, dst)

    monkeypatch.setattr(cover_cache.os, "replace", spy)
    cover_cache._write_atomic(str(tmp_path), 730, JPEG)
    assert len(seen) == 1 and cover_cache._TEMP_NAME.fullmatch(seen[0]), seen


def test_reconcile_clears_a_row_whose_file_has_the_wrong_size(db, tmp_path) -> None:
    cache_dir = tmp_path / "covers"
    _track(db, 730, URL_730)
    _download(db, str(cache_dir), lambda u: _image(JPEG))
    (cache_dir / "730.img").write_bytes(JPEG + b"x")
    conn = _conn(db)
    try:
        cover_cache.reconcile(conn, str(cache_dir))
    finally:
        conn.close()
    assert _file_rows(db)[730]["sha256"] is None


def test_reconcile_without_a_directory_is_a_no_op(db, tmp_path) -> None:
    conn = _conn(db)
    try:
        assert cover_cache.reconcile(conn, str(tmp_path / "missing")) == 0
    finally:
        conn.close()


# --------------------------------------------------------------------------
# The thread
# --------------------------------------------------------------------------


def test_fetcher_logs_one_warning_per_failure_streak_and_pauses(db, tmp_path, caplog) -> None:
    _track(db, 730, URL_730)
    _track(db, 440, URL_440)
    clock = [NOW]
    calls = []

    def offline(url):
        calls.append(url)
        raise cover_cache.CoverFetchError("unreachable")

    fetcher = cover_cache.CoverFileFetcher(
        db, cache_dir=str(tmp_path / "c"), fetch=offline, now=lambda: clock[0]
    )
    caplog.set_level(logging.DEBUG, logger="vault_api.cover_cache")
    fetcher.tick()
    clock[0] = NOW + timedelta(minutes=5)
    assert fetcher.tick() is None  # paused
    assert len(calls) == 1
    clock[0] = NOW + timedelta(minutes=20)
    fetcher.tick()
    assert len(calls) == 2
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1

    fetcher._fetch = lambda url: _image(JPEG)
    clock[0] = NOW + timedelta(hours=3)
    fetcher.tick()
    assert any("work again" in r.getMessage() for r in caplog.records if r.levelno == logging.INFO)


def test_fetcher_thread_starts_and_stops_without_calling_out(db) -> None:
    calls = []
    fetcher = cover_cache.CoverFileFetcher(db, fetch=lambda u: calls.append(u))
    fetcher.start()
    fetcher.stop()
    assert calls == []  # conftest pushes the first tick an hour out


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------


def test_schema_v21_adds_app_cover_file(tmp_path) -> None:
    assert SCHEMA_VERSION >= 21
    path = str(tmp_path / "old.db")
    init_db(path)
    conn = get_connection(path)
    try:
        conn.execute("DROP TABLE app_cover_file")
        conn.execute("UPDATE schema_version SET version = 20")
        conn.commit()
    finally:
        conn.close()
    init_db(path)
    init_db(path)
    conn = get_connection(path)
    try:
        (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
        columns = {r["name"]: r["type"] for r in conn.execute("PRAGMA table_info(app_cover_file)")}
    finally:
        conn.close()
    assert version == SCHEMA_VERSION
    assert columns == {
        "appid": "INTEGER",
        "source_url": "TEXT",
        "sha256": "TEXT",
        "content_type": "TEXT",
        "size_bytes": "INTEGER",
        "stored_at": "TEXT",
        "attempts": "INTEGER",
        "next_try_at": "TEXT",
    }


# --------------------------------------------------------------------------
# API: cover_cached_url and GET /v1/covers/{appid}/{signature}
# --------------------------------------------------------------------------


def _seed(client: TestClient, depotid: int, appid: int) -> None:
    response = client.put(
        f"/v1/mapping/{depotid}", json={"appid": appid, "app_name": None}, headers=AUTH
    )
    assert response.status_code == 200


def _stored_app(settings: Settings, appid: int = 730, data: bytes = JPEG) -> TestClient:
    client = TestClient(create_app(settings))
    _seed(client, appid + 1, appid)
    _track(settings.db_path, appid, URL_730)
    cache_dir = client.app.state.cover_file_fetcher.cache_dir
    assert cache_dir == cover_cache.cache_dir_for(settings.db_path)
    _download(settings.db_path, cache_dir, lambda u: _image(data))
    return client


def _cached_url(client: TestClient, appid: int = 730) -> str:
    games = {g["appid"]: g for g in client.get("/v1/games", headers=AUTH).json()}
    url = games[appid]["cover_cached_url"]
    assert url == client.get(f"/v1/games/{appid}", headers=AUTH).json()["cover_cached_url"]
    return url


def test_games_expose_cover_cached_url_and_keep_cover_url(settings: Settings) -> None:
    client = _stored_app(settings)
    _seed(client, 441, 440)
    games = {g["appid"]: g for g in client.get("/v1/games", headers=AUTH).json()}
    sha = hashlib.sha256(JPEG).hexdigest()
    assert games[730]["cover_cached_url"] == (
        f"/v1/covers/730/{cover_cache.cover_signature(TEST_API_KEY, 730, sha)}"
    )
    assert games[730]["cover_url"] == URL_730  # unchanged for older clients
    assert games[440]["cover_cached_url"] is None
    assert client.get("/v1/games/440", headers=AUTH).json()["cover_cached_url"] is None


def test_cover_is_served_without_the_api_key_with_etag_and_long_cache(settings: Settings) -> None:
    client = _stored_app(settings)
    url = _cached_url(client)
    response = client.get(url)  # no X-Api-Key: the signature is the credential
    assert response.status_code == 200
    assert response.content == JPEG
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["etag"] == f'"{hashlib.sha256(JPEG).hexdigest()}"'
    assert response.headers["cache-control"] == "private, max-age=31536000, immutable"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_if_none_match_answers_304_without_a_body(settings: Settings) -> None:
    client = _stored_app(settings)
    url = _cached_url(client)
    etag = client.get(url).headers["etag"]
    response = client.get(url, headers={"If-None-Match": etag})
    assert response.status_code == 304
    assert response.content == b""
    assert response.headers["etag"] == etag
    assert response.headers["cache-control"] == cover_cache.CACHE_CONTROL
    assert client.get(url, headers={"If-None-Match": '"other"'}).status_code == 200


def test_every_refusal_is_the_same_404(settings: Settings) -> None:
    client = _stored_app(settings)
    url = _cached_url(client)
    good_sig = url.rsplit("/", 1)[1]
    other_key_sig = cover_cache.cover_signature("not-the-key", 730, hashlib.sha256(JPEG).hexdigest())
    _seed(client, 441, 440)  # tracked, nothing stored
    for path in (
        f"/v1/covers/730/{other_key_sig}",  # signed with another key
        f"/v1/covers/730/{'0' * 32}",
        f"/v1/covers/440/{good_sig}",  # another app's signature
        f"/v1/covers/12345/{good_sig}",  # unknown app
    ):
        response = client.get(path)
        assert response.status_code == 404, path
        assert response.json() == {"detail": "Not Found"}, path


@pytest.mark.parametrize(
    "path",
    [
        "/v1/covers/730",
        "/v1/covers/730/ABCDEF0123456789ABCDEF0123456789",
        "/v1/covers/730/abc",
        "/v1/covers/0730x/0123456789abcdef0123456789abcdef",
        "/v1/covers/0730/0123456789abcdef0123456789abcdef",  # leading zero
        "/v1/covers/730/0123456789abcdef0123456789abcdef/x",
    ],
)
def test_other_cover_shaped_paths_still_need_the_key(settings: Settings, path) -> None:
    client = TestClient(create_app(settings))
    assert client.get(path).status_code == 401


def test_signed_path_shape_is_exact() -> None:
    assert body_guard.is_signed_cover_path("/v1/covers/730/0123456789abcdef0123456789abcdef")
    for path in (
        "/v1/covers/730/0123456789abcdef0123456789abcdef/",
        "/v1/covers/730/0123456789abcdef0123456789abcdeff",
        "/v1/covers/12345678901/0123456789abcdef0123456789abcdef",
        "/v1/covers//0123456789abcdef0123456789abcdef",
        "/v1/covers/0730/0123456789abcdef0123456789abcdef",
        "/v1/games/0123456789abcdef0123456789abcdef",
        "/v1/covers/730/0123456789abcdef0123456789abcdef\n",
    ):
        assert not body_guard.is_signed_cover_path(path), path
    assert body_guard.needs_api_key("/v1/games")
    assert not body_guard.needs_api_key("/v1/covers/730/0123456789abcdef0123456789abcdef")


def test_a_new_api_key_invalidates_old_cover_urls(settings: Settings) -> None:
    client = _stored_app(settings)
    url = _cached_url(client)
    settings2 = dataclasses.replace(settings, vault_api_key="a-different-key")
    client2 = TestClient(create_app(settings2))
    assert client2.get(url).status_code == 404
    games = client2.get("/v1/games", headers={"X-Api-Key": "a-different-key"}).json()
    new_url = games[0]["cover_cached_url"]
    assert new_url != url and client2.get(new_url).status_code == 200


def test_an_altered_file_is_404_and_queued_again(settings: Settings) -> None:
    client = _stored_app(settings)
    url = _cached_url(client)
    path = cover_cache.file_path(client.app.state.cover_file_fetcher.cache_dir, 730)
    with open(path, "r+b") as handle:  # same size, other bytes
        handle.seek(10)
        handle.write(b"X")
    assert client.get(url).status_code == 404
    assert _file_rows(settings.db_path)[730]["sha256"] is None
    games = {g["appid"]: g for g in client.get("/v1/games", headers=AUTH).json()}
    assert games[730]["cover_cached_url"] is None  # clients use the CDN URL meanwhile


def test_a_missing_file_is_404(settings: Settings) -> None:
    client = _stored_app(settings)
    url = _cached_url(client)
    os.unlink(cover_cache.file_path(client.app.state.cover_file_fetcher.cache_dir, 730))
    assert client.get(url).status_code == 404


def test_the_route_only_reads_and_never_fetches(settings: Settings, monkeypatch) -> None:
    client = _stored_app(settings)
    monkeypatch.setattr(cover_cache, "fetch_image", lambda *a, **k: pytest.fail("fetched"))
    monkeypatch.setattr(
        cover_cache._OPENER, "open", lambda *a, **k: pytest.fail("network on request path")
    )
    assert client.get(_cached_url(client)).status_code == 200
    _seed(client, 441, 440)
    _track(settings.db_path, 440, URL_440)
    assert client.get(f"/v1/covers/440/{'1' * 32}").status_code == 404


def test_lifespan_starts_and_stops_the_fetcher(settings: Settings) -> None:
    app = create_app(settings)
    fetcher = app.state.cover_file_fetcher
    with TestClient(app):
        assert fetcher._thread is not None and fetcher._thread.is_alive()
    assert fetcher._thread is None


# --------------------------------------------------------------------------
# Egress parity: vault-proxy's baked hosts cover every host the cover code
# may contact (user decision 2026-10-13, ADR-0011 addendum D10)
# --------------------------------------------------------------------------


def _baked_hosts() -> set[str]:
    import re

    from tests.test_eg1_egress_lock import PROXY_ENTRYPOINT_PATH

    text = PROXY_ENTRYPOINT_PATH.read_text(encoding="utf-8")
    (value,) = re.findall(r'^BAKED_HOSTS="([^"]*)"$', text, re.MULTILINE)
    return set(value.split())


def test_proxy_bakes_in_exactly_the_relay_host_and_the_cover_hosts() -> None:
    from vault_api import cover_art, steam_relay

    expected = {steam_relay.STEAM_API_HOST} | set(cover_art.ALLOWED_ASSET_HOSTS)
    assert _baked_hosts() == expected


@pytest.mark.parametrize("url", [URL_730, URL_440])
def test_every_valid_cover_url_names_a_baked_host(url) -> None:
    from urllib.parse import urlsplit

    assert urlsplit(url).hostname in _baked_hosts()
