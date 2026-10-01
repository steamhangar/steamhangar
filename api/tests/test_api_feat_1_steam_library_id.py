"""WP API-FEAT-1: ``steam_library_steamid``, the vault's one SteamID64.

A DB-overridable setting (ADR-0009, freeze exception in ADR-0016's
addendum) with an env source, ``VAULT_STEAM_LIBRARY_STEAMID``, for
``VAULT_SETTINGS_READONLY`` deployments. Its grammar is
``steam_relay.valid_steamid64``, reused through
``config.parse_steam_library_steamid``; the two ``*_routes_through_*``
tests below pin that reuse structurally.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from vault_api import config, settings_store, steam_relay
from vault_api.config import Settings
from vault_api.db import get_connection
from vault_api.main import create_app

AUTH = {"X-Api-Key": TEST_API_KEY}
KEY = "steam_library_steamid"
ENV = "VAULT_STEAM_LIBRARY_STEAMID"

# Synthetic ids at the edges of the individual-account range (no real person).
VALID_LOW = str(steam_relay.STEAM_ID64_BASE + 1)
VALID_HIGH = str(steam_relay.STEAM_ID64_MAX)
BELOW_RANGE = str(steam_relay.STEAM_ID64_BASE - 1)
ABOVE_RANGE = str(steam_relay.STEAM_ID64_MAX + 1)


def row(body: dict) -> dict:
    for entry in body["settings"]:
        if entry["key"] == KEY:
            return entry
    raise AssertionError(f"{KEY!r} missing from response")


def stored(client: TestClient) -> str | None:
    conn = get_connection(client.app.state.settings.db_path)
    try:
        return settings_store.get_override(conn, KEY)
    finally:
        conn.close()


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    return Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
        **overrides,
    )


# --------------------------------------------------------------------------
# GET shape
# --------------------------------------------------------------------------


def test_get_reports_the_unset_default(client: TestClient) -> None:
    body = client.get("/v1/settings", headers=AUTH).json()
    assert row(body) == {
        "key": KEY,
        "effective": "",
        "source": "default",
        "fallback": "",
        "applies": "immediately",
        "env_only": False,
    }


# --------------------------------------------------------------------------
# PATCH accepts, clears
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", [VALID_LOW, VALID_HIGH, f"  {VALID_LOW}  "])
def test_patch_accepts_a_valid_id_and_get_reports_it(
    client: TestClient, value: str
) -> None:
    response = client.patch("/v1/settings", json={KEY: value}, headers=AUTH)
    assert response.status_code == 200
    body = client.get("/v1/settings", headers=AUTH).json()
    entry = row(body)
    assert entry["effective"] == value.strip()
    # A JSON string, never a number (17 digits exceed 2**53).
    assert isinstance(entry["effective"], str)
    assert entry["source"] == "db"
    assert entry["fallback"] == ""


@pytest.mark.parametrize("number", [int(VALID_LOW), float(VALID_LOW)])
def test_patch_rejects_a_json_number_and_asks_for_a_string(
    client: TestClient, number: object
) -> None:
    """S1: a JSON number is refused even when it is a valid id -- a JS
    sender would already have rounded it to a different account."""
    response = client.patch("/v1/settings", json={KEY: number}, headers=AUTH)
    assert response.status_code == 422
    assert "send the SteamID64 as a JSON string" in response.json()["detail"]
    assert stored(client) is None


def test_other_keys_still_accept_a_json_number(client: TestClient) -> None:
    response = client.patch(
        "/v1/settings", json={"schedule_interval_minutes": 60}, headers=AUTH
    )
    assert response.status_code == 200


def test_corrupt_stored_value_is_not_logged_verbatim(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """N3: a hand-edited row that no longer parses is logged as <invalid>."""
    settings = make_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        conn = get_connection(settings.db_path)
        try:
            settings_store.set_override(conn, KEY, "corrupt-xyz-123")
            with caplog.at_level("ERROR", logger="vault_api.settings_store"):
                effective = settings_store.effective_settings(conn, settings)
        finally:
            conn.close()
        assert client.get("/v1/settings", headers=AUTH).status_code == 200
    assert effective.steam_library_steamid == ""
    assert "<invalid>" in caplog.text
    assert "corrupt-xyz-123" not in caplog.text


def test_null_clears_the_override_back_to_env(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path, steam_library_steamid=VALID_HIGH))) as client:
        assert client.patch("/v1/settings", json={KEY: VALID_LOW}, headers=AUTH).status_code == 200
        response = client.patch("/v1/settings", json={KEY: None}, headers=AUTH)
        assert response.status_code == 200
        entry = row(response.json())
        assert stored(client) is None
    assert entry["effective"] == VALID_HIGH
    assert entry["source"] == "env"


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_clears_the_value_even_over_an_env_id(tmp_path: Path, blank: str) -> None:
    with TestClient(create_app(make_settings(tmp_path, steam_library_steamid=VALID_HIGH))) as client:
        response = client.patch("/v1/settings", json={KEY: blank}, headers=AUTH)
    assert response.status_code == 200
    entry = row(response.json())
    assert entry["effective"] == ""
    assert entry["source"] == "db"
    assert entry["fallback"] == VALID_HIGH


# --------------------------------------------------------------------------
# PATCH rejects
# --------------------------------------------------------------------------

MALFORMED = {
    "too_short": "1234567890123456",
    "too_long": VALID_LOW + "0",
    "non_digit": VALID_LOW[:-1] + "a",
    "plus_sign": "+" + VALID_LOW[1:],
    "underscore": VALID_LOW[:8] + "_" + VALID_LOW[9:],
    "inner_space": VALID_LOW[:8] + " " + VALID_LOW[9:],
    # Arabic-Indic digits: str.isdigit() and int() accept them.
    "unicode_digits": VALID_LOW.translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")),
    "fullwidth_digits": VALID_LOW.translate(str.maketrans("0123456789", "０１２３４５６７８９")),
    "below_range": BELOW_RANGE,
    "above_range": ABOVE_RANGE,
    "zero": "0" * 17,
}


@pytest.mark.parametrize("bad", MALFORMED.values(), ids=MALFORMED.keys())
def test_patch_rejects_malformed_ids_with_422(client: TestClient, bad: str) -> None:
    response = client.patch("/v1/settings", json={KEY: bad}, headers=AUTH)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail.startswith(f"{KEY!r}: must be a SteamID64")
    # The existing pattern names the key, never the raw value.
    assert bad not in detail
    assert stored(client) is None


@pytest.mark.parametrize("bad", [True, [VALID_LOW], {"id": VALID_LOW}])
def test_patch_rejects_non_string_shapes(client: TestClient, bad: object) -> None:
    response = client.patch("/v1/settings", json={KEY: bad}, headers=AUTH)
    assert response.status_code == 422
    assert stored(client) is None


# --------------------------------------------------------------------------
# Grammar reuse (structural): PATCH and startup both go through
# steam_relay.valid_steamid64 and nothing else.
# --------------------------------------------------------------------------


def test_patch_routes_through_valid_steamid64(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[object] = []

    def refuse(value: object) -> None:
        seen.append(value)
        return None

    monkeypatch.setattr(steam_relay, "valid_steamid64", refuse)
    response = client.patch("/v1/settings", json={KEY: VALID_LOW}, headers=AUTH)
    assert response.status_code == 422
    assert seen == [VALID_LOW]


def test_patch_has_no_second_grammar_in_front_of_valid_steamid64(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(steam_relay, "valid_steamid64", lambda value: 1)
    response = client.patch("/v1/settings", json={KEY: "not-an-id"}, headers=AUTH)
    assert response.status_code == 200


def test_startup_routes_through_valid_steamid64(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv(ENV, VALID_LOW)
    monkeypatch.setattr(steam_relay, "valid_steamid64", lambda value: None)
    with pytest.raises(RuntimeError, match=ENV):
        Settings.from_env()


# --------------------------------------------------------------------------
# Env source
# --------------------------------------------------------------------------


def test_env_unset_and_blank_mean_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.delenv(ENV, raising=False)
    assert Settings.from_env().steam_library_steamid == ""
    monkeypatch.setenv(ENV, "  ")
    assert Settings.from_env().steam_library_steamid == ""


def test_env_value_is_used_and_reported_as_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VAULT_API_KEY", TEST_API_KEY)
    monkeypatch.setenv("VAULT_DB_PATH", str(tmp_path / "vault.db"))
    monkeypatch.setenv("VAULT_CACHE_ROOT", str(tmp_path / "cache"))
    monkeypatch.setenv(ENV, f" {VALID_HIGH} ")
    settings = Settings.from_env()
    assert settings.steam_library_steamid == VALID_HIGH
    with TestClient(create_app(settings)) as client:
        entry = row(client.get("/v1/settings", headers=AUTH).json())
    assert entry["effective"] == VALID_HIGH
    assert entry["source"] == "env"
    assert entry["fallback"] == VALID_HIGH


@pytest.mark.parametrize("bad", MALFORMED.values(), ids=MALFORMED.keys())
def test_invalid_env_value_refuses_startup(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv(ENV, bad)
    with pytest.raises(RuntimeError, match=ENV) as excinfo:
        Settings.from_env()
    assert bad not in str(excinfo.value)


def test_config_and_patch_share_one_parser() -> None:
    assert config.parse_steam_library_steamid(VALID_LOW) == VALID_LOW
    with pytest.raises(ValueError):
        config.parse_steam_library_steamid(BELOW_RANGE)


# --------------------------------------------------------------------------
# Readonly lock (ADR-0009 decision 3)
# --------------------------------------------------------------------------


def test_readonly_refuses_patch_with_403_and_persists_nothing(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, settings_readonly=True, steam_library_steamid=VALID_HIGH)
    with TestClient(create_app(settings)) as client:
        response = client.patch("/v1/settings", json={KEY: VALID_LOW}, headers=AUTH)
        assert response.status_code == 403
        assert stored(client) is None
        entry = row(client.get("/v1/settings", headers=AUTH).json())
    # The env value stays the reachable path under the lock.
    assert entry["effective"] == VALID_HIGH
    assert entry["source"] == "env"
