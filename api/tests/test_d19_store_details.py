"""WP D19-1c STORE-DETAILS: store details on the game card (schema v33).

No test touches the network: the store answers are synthetic fixtures injected
through the service's ``fetch`` parameter, and every fetch is counted.

Pinned by name:

1. **Off unless the own switch is on**, whatever the oracle switch says: no
   fetch on a default install, and a card open with the switch off never
   starts a lookup.
2. **Asked at most once per TTL and per app**: repeat, concurrent and
   post-failure opens ask nothing; a burst of apps is rate limited.
3. **Third-party text is data**: tags stripped, entities decoded, capped.
4. **Overall rating only**, and removed/age-gated apps show nothing.
5. **The migration is additive** and an older build refuses a v33 file.
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
from vault_api import config, oracle, store_details
from vault_api.db import SCHEMA_VERSION, get_connection, init_db
from vault_api.main import create_app
from vault_api.store_details import StoreDetailsService

AUTH = {"X-Api-Key": TEST_API_KEY}


def details_body(appid: int = 1366540, **data) -> bytes:
    payload = {
        "short_description": "A <b>fast</b> game &amp; more.<script>alert(1)</script>",
        "genres": [{"id": "1", "description": "Action"}, {"id": "2", "description": "Indie"}],
        "developers": ["Some Studio"],
        "release_date": {"coming_soon": False, "date": "3 Mar, 2023"},
        "categories": [
            {"id": 2, "description": "Single-player"},
            {"id": 9, "description": "Co-op"},
            {"id": 53, "description": "VR Supported"},
            {"id": 22, "description": "Steam Achievements"},
        ],
    }
    payload.update(data)
    return json.dumps({str(appid): {"success": True, "data": payload}}).encode()


def reviews_body(positive: int = 970, negative: int = 30) -> bytes:
    return json.dumps(
        {
            "success": 1,
            "query_summary": {
                "num_reviews": 0,
                "review_score_desc": "Overwhelmingly Positive",
                "total_positive": positive,
                "total_negative": negative,
            },
        }
    ).encode()


class Fetch:
    """Counts URLs; answers appdetails and appreviews separately."""

    def __init__(self, details=None, reviews=None) -> None:
        self.urls: list[str] = []
        self.details = details
        self.reviews = reviews if reviews is not None else reviews_body()

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        if "/appreviews/" in url:
            payload = self.reviews
        elif self.details is not None:
            payload = self.details
        else:  # an answer for whatever app id was asked
            payload = details_body(int(url.split("appids=")[1].split("&")[0]))
        if isinstance(payload, Exception):
            raise payload
        return payload


class Clock:
    def __init__(self) -> None:
        self.now = datetime.now(timezone.utc)

    def __call__(self) -> datetime:
        return self.now


def make_client(settings, fetch: Fetch, clock: Clock | None = None) -> TestClient:
    init_db(settings.db_path)
    app = create_app(settings)
    app.state.store_details = StoreDetailsService(
        run=lambda job: job(), fetch=fetch, clock=clock or Clock()
    )
    return TestClient(app)


def on(tmp_path: Path, **overrides):
    return enabled_settings(tmp_path, store_details_enabled=True, **overrides)


def details(client: TestClient, appid: int = 1366540) -> dict:
    response = client.get(f"/v1/store/{appid}/details", headers=AUTH)
    assert response.status_code == 200
    return response.json()


# --------------------------------------------------------------------------
# Gating: the privacy property
# --------------------------------------------------------------------------


def test_switch_defaults_off_everywhere() -> None:
    assert config.DEFAULT_STORE_DETAILS is False
    assert config.Settings.__dataclass_fields__["store_details_enabled"].default is False


def test_default_settings_ask_nothing_even_with_the_oracle_on(tmp_path: Path) -> None:
    fetch = Fetch()
    client = make_client(enabled_settings(tmp_path), fetch)  # oracle on, own switch off
    assert details(client)["state"] == "off"
    assert fetch.urls == []


def test_the_switch_alone_is_enough_and_the_oracle_is_not_needed(tmp_path: Path) -> None:
    settings = dataclasses.replace(off_settings(tmp_path), store_details_enabled=True)
    fetch = Fetch()
    assert details(make_client(settings, fetch))["state"] == "ready"
    assert len(fetch.urls) == 2


def test_an_unauthenticated_request_is_refused_and_asks_nothing(tmp_path: Path) -> None:
    fetch = Fetch()
    client = make_client(on(tmp_path), fetch)
    assert client.get("/v1/store/1/details").status_code in (401, 403)
    assert fetch.urls == []


def test_the_service_itself_refuses_when_off(tmp_path: Path) -> None:
    settings = off_settings(tmp_path)
    init_db(settings.db_path)
    fetch = Fetch()
    service = StoreDetailsService(run=lambda job: job(), fetch=fetch)
    conn = get_connection(settings.db_path)
    try:
        assert service.lookup(conn, 1366540, settings).state == store_details.STATE_OFF
    finally:
        conn.close()
    assert fetch.urls == []


def test_env_switch_is_read_and_listed_env_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from vault_api import settings_store

    assert any(s.env_var == "VAULT_STORE_DETAILS" for s in settings_store.ENV_ONLY_INFO_KEYS)
    assert "store_details_enabled" in settings_store.ENV_ONLY_KEYS


# --------------------------------------------------------------------------
# Content, sanitising, rating
# --------------------------------------------------------------------------


def test_ready_payload_has_plain_text_modes_and_overall_rating(tmp_path: Path) -> None:
    fetch = Fetch()
    body = details(make_client(on(tmp_path), fetch))
    assert body["state"] == "ready"
    assert "<" not in body["description"] and ">" not in body["description"]
    assert body["description"].startswith("A fast game & more.")
    assert body["genres"] == ["Action", "Indie"]
    assert body["developers"] == ["Some Studio"]
    assert body["release_date"] == "3 Mar, 2023"
    assert body["modes"] == ["single_player", "co_op", "vr"]
    assert body["review_summary"] == "Overwhelmingly Positive"
    assert (body["review_positive"], body["review_negative"]) == (970, 30)
    # Only the app id and the fixed language leave the LAN.
    assert fetch.urls == [
        "https://store.steampowered.com/api/appdetails?appids=1366540&l=english",
        "https://store.steampowered.com/appreviews/1366540"
        "?json=1&num_per_page=0&language=all&purchase_type=all",
    ]


def test_clean_text_strips_markup_decodes_entities_and_caps() -> None:
    assert store_details.clean_text("<p>a&nbsp;&lt;i&gt;b&lt;/i&gt;</p>", 50) == "a b"
    assert store_details.clean_text("x" * 1000, 20) == "x" * 19 + "…"
    assert store_details.clean_text("  \x00\x07  ", 20) is None
    assert store_details.clean_text(None, 20) is None
    assert store_details.clean_text(["<b>"], 20) is None


def test_hostile_text_is_stored_as_plain_text(tmp_path: Path) -> None:
    hostile = details_body(
        short_description='<img src=x onerror="alert(1)">&lt;script&gt;boom&lt;/script&gt;ok',
        genres=[{"id": 1, "description": "<i>Action</i>"}],
    )
    client = make_client(on(tmp_path), Fetch(details=hostile))
    body = details(client)
    assert body["description"] == "boom ok"
    assert body["genres"] == ["Action"]
    conn = get_connection(on(tmp_path).db_path)
    try:
        row = conn.execute("SELECT description FROM store_details").fetchone()
    finally:
        conn.close()
    assert "<" not in row["description"] and ">" not in row["description"]


def test_no_recent_review_figure_exists(tmp_path: Path) -> None:
    body = details(make_client(on(tmp_path), Fetch()))
    assert not [k for k in body if "recent" in k]


def test_no_reviews_means_no_rating_but_keeps_the_rest(tmp_path: Path) -> None:
    fetch = Fetch(reviews=reviews_body(0, 0))
    body = details(make_client(on(tmp_path), fetch))
    assert body["state"] == "ready"
    assert body["review_summary"] is None and body["review_positive"] is None


def test_a_failing_reviews_request_keeps_the_details(tmp_path: Path) -> None:
    fetch = Fetch(reviews=oracle.OracleError("boom"))
    body = details(make_client(on(tmp_path), fetch))
    assert body["state"] == "ready"
    assert body["review_summary"] is None
    assert body["genres"] == ["Action", "Indie"]


def test_success_false_shows_nothing_and_skips_reviews(tmp_path: Path) -> None:
    fetch = Fetch(details=json.dumps({"1366540": {"success": False}}).encode())
    client = make_client(on(tmp_path), fetch)
    assert details(client)["state"] == "unavailable"
    assert len(fetch.urls) == 1  # no appreviews for an app the store does not know
    details(client)
    assert len(fetch.urls) == 1  # and the "none" outcome is cached


def test_unavailable_payload_has_no_fields(tmp_path: Path) -> None:
    fetch = Fetch(details=json.dumps({"1366540": {"success": False}}).encode())
    body = details(make_client(on(tmp_path), fetch))
    assert body["description"] is None and body["genres"] == [] and body["modes"] == []


# --------------------------------------------------------------------------
# TTL, back-off, dedupe, rate limit
# --------------------------------------------------------------------------


def test_a_repeat_open_inside_the_ttl_asks_nothing(tmp_path: Path) -> None:
    clock = Clock()
    fetch = Fetch()
    client = make_client(on(tmp_path), fetch, clock)
    assert details(client)["state"] == "ready"
    clock.now += timedelta(hours=23)
    assert details(client)["state"] == "ready"
    assert len(fetch.urls) == 2
    clock.now += timedelta(hours=2)
    assert details(client)["state"] == "ready"
    assert len(fetch.urls) == 4


def test_a_failure_backs_off_for_thirty_minutes(tmp_path: Path) -> None:
    clock = Clock()
    fetch = Fetch(details=oracle.OracleError("down"))
    client = make_client(on(tmp_path), fetch, clock)
    assert details(client)["state"] == "unavailable"
    assert details(client)["state"] == "unavailable"
    assert len(fetch.urls) == 1
    clock.now += timedelta(minutes=31)
    details(client)
    assert len(fetch.urls) == 2


def test_garbage_is_a_stored_failure_not_an_error(tmp_path: Path) -> None:
    fetch = Fetch(details=b"<html>not json</html>")
    client = make_client(on(tmp_path), fetch)
    assert details(client)["state"] == "unavailable"
    details(client)
    assert len(fetch.urls) == 1


def test_a_burst_of_different_apps_is_rate_limited(tmp_path: Path) -> None:
    fetch = Fetch()
    settings = on(tmp_path)
    client = make_client(settings, fetch)
    client.app.state.store_details = StoreDetailsService(
        run=lambda job: job(), fetch=fetch, monotonic=lambda: 100.0
    )
    states = [
        client.get(f"/v1/store/{5000 + i}/details", headers=AUTH).json()["state"]
        for i in range(store_details.RATE_LIMIT + 3)
    ]
    assert states[: store_details.RATE_LIMIT] == ["ready"] * store_details.RATE_LIMIT
    assert states[store_details.RATE_LIMIT :] == ["unavailable"] * 3
    assert len(fetch.urls) == 2 * store_details.RATE_LIMIT


def test_concurrent_opens_share_one_in_flight_lookup(tmp_path: Path) -> None:
    settings = on(tmp_path)
    init_db(settings.db_path)
    fetch = Fetch()
    started: list = []
    service = StoreDetailsService(run=started.append, fetch=fetch)
    conn = get_connection(settings.db_path)
    try:
        first = service.lookup(conn, 1366540, settings)
        second = service.lookup(conn, 1366540, settings)
        assert first.state == second.state == store_details.STATE_PENDING
        assert len(started) == 1
        started[0]()
        assert service.lookup(conn, 1366540, settings).state == store_details.STATE_READY
    finally:
        conn.close()
    assert len(fetch.urls) == 2


def test_an_unreadable_table_is_unavailable_not_an_error(tmp_path: Path) -> None:
    settings = on(tmp_path)
    client = make_client(settings, Fetch())
    conn = sqlite3.connect(settings.db_path)
    conn.execute("DROP TABLE store_details")
    conn.commit()
    conn.close()
    assert details(client)["state"] == "unavailable"


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------


def test_schema_is_at_least_v33() -> None:
    assert SCHEMA_VERSION >= 33


def _make_v32_database(db_path: str) -> None:
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        conn.executescript("DROP TABLE store_details; UPDATE schema_version SET version = 32;")
        conn.execute("INSERT INTO apps (appid, name) VALUES (441, 'Team Fortress 2')")
        conn.commit()
    finally:
        conn.close()


def test_a_populated_v32_database_migrates_idempotently(tmp_path: Path) -> None:
    original = str(tmp_path / "vault.db")
    _make_v32_database(original)
    copy = str(tmp_path / "copy.db")
    shutil.copy(original, copy)

    init_db(copy)
    init_db(copy)  # idempotent

    conn = get_connection(copy)
    try:
        (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
        app = conn.execute("SELECT name FROM apps WHERE appid = 441").fetchone()
        assert conn.execute("SELECT COUNT(*) FROM store_details").fetchone()[0] == 0
        store_details.store_outcome(
            conn, 441, store_details.OUTCOME_OK, info=store_details.StoreInfo(description="x")
        )
        stored = store_details.load_details(conn, 441)
    finally:
        conn.close()
    assert version == SCHEMA_VERSION
    assert app["name"] == "Team Fortress 2"
    assert stored is not None and stored.info.description == "x"

    raw = sqlite3.connect(original)  # the untouched original is still v32
    try:
        assert raw.execute("SELECT version FROM schema_version").fetchone()[0] == 32
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
