"""Persisted settings (ADR-0009): ``GET``/``PATCH /v1/settings`` and the
``settings_store`` module underneath them.

Layout, mirroring ``docs/LEARNINGS.md``'s testing-discipline entries:

1. **Precedence** — db > env > default, with an explicit test per direction
   so flipping the priority order in ``settings_store._source_without_override``
   or ``effective_settings`` kills a NAMED test, not just "something in the
   suite".
2. **PATCH validation** — one bad-value case per grammar family (schedule
   window, strict int, auto-GC enum, webhook URL scheme), each asserted
   ``422`` AND not persisted (a second GET/`get_override` call proves it).
3. **Env-only / readonly / auth** — the operator hard-locks.
4. **Redaction** — a STRING-LITERAL pin (docs/LEARNINGS.md "Security-constant
   pins must assert STRING LITERALS"): the secret substring must be provably
   ABSENT from the raw response text, not merely "not equal to what
   ``redact_url`` would produce if called again" (that would only prove the
   test and the code agree with each other, not that the secret is gone).
5. **Wiring** — ``effective_settings`` fed straight to the scheduler's own
   ``maybe_sweep``/``interval_elapsed`` (next_sweep) and through the real
   worker thread for ``auto_gc`` (immediately), so "the override actually
   takes effect" is demonstrated, not just "the accessor returns the right
   Python value".
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests import stub_prefill
from tests.conftest import TEST_API_KEY
from vault_api import __version__ as VAULT_API_VERSION
from vault_api import config
from vault_api import scheduler as scheduler_module
from vault_api import settings_store
from vault_api.config import Settings
from vault_api.db import get_connection, init_db
from vault_api.main import create_app
from vault_api.schedule_window import parse_window

AUTH = {"X-Api-Key": TEST_API_KEY}


def find(body: dict, key: str) -> dict:
    for row in body["settings"]:
        if row["key"] == key:
            return row
    raise AssertionError(f"{key!r} missing from response: {body}")


def keys_of(body: dict) -> set[str]:
    return {row["key"] for row in body["settings"]}


# ==========================================================================
# Auth
# ==========================================================================


def test_get_settings_requires_api_key(client: TestClient) -> None:
    response = client.get("/v1/settings")
    assert response.status_code == 401


def test_patch_settings_requires_api_key(client: TestClient) -> None:
    response = client.patch("/v1/settings", json={"vault_name": "x"})
    assert response.status_code == 401


# ==========================================================================
# GET shape: defaults, env-only rows, the excluded secret
# ==========================================================================


def test_get_reports_defaults_on_a_fresh_install(client: TestClient) -> None:
    response = client.get("/v1/settings", headers=AUTH)
    assert response.status_code == 200
    body = response.json()

    assert body["readonly"] is False

    vault_name = find(body, "vault_name")
    assert vault_name == {
        "key": "vault_name",
        "effective": "",
        "source": "default",
        "fallback": "",
        "applies": "restart-required",
        "env_only": False,
    }

    # WP SWEEP-1 (ADR-0014): the `client` fixture's `Settings` sets neither
    # `auto_gc` nor `sweep_include_cached` explicitly, so this reports their
    # own dataclass defaults -- `execute` / `True` since the operator-decided
    # flip, paired together on purpose (see `docs/adr/0014-sweep-cached-and-
    # auto-gc-default-on.md`). Previously `off` -- flip either
    # `DEFAULT_AUTO_GC`/`DEFAULT_SWEEP_INCLUDE_CACHED` back and this test
    # dies.
    auto_gc = find(body, "auto_gc")
    assert auto_gc["effective"] == "execute"
    assert auto_gc["source"] == "default"
    assert auto_gc["applies"] == "immediately"

    window = find(body, "schedule_window")
    assert window["effective"] is None
    assert window["applies"] == "next_sweep"

    # WP 4d / WP SWEEP-1.
    sweep_include_cached = find(body, "sweep_include_cached")
    assert sweep_include_cached == {
        "key": "sweep_include_cached",
        "effective": True,
        "source": "default",
        "fallback": True,
        "applies": "next_sweep",
        "env_only": False,
    }


def test_get_includes_informational_env_only_rows(client: TestClient) -> None:
    body = client.get("/v1/settings", headers=AUTH).json()

    db_path_row = find(body, "db_path")
    assert db_path_row["env_only"] is True
    assert db_path_row["applies"] == "restart-required"

    settings_readonly_row = find(body, "settings_readonly")
    assert settings_readonly_row["env_only"] is True
    assert settings_readonly_row["effective"] is False


def test_vault_api_key_never_appears_in_the_response(client: TestClient) -> None:
    """Not even redacted — the actual auth secret, never listed at all."""
    response = client.get("/v1/settings", headers=AUTH)
    assert "vault_api_key" not in keys_of(response.json())
    assert TEST_API_KEY not in response.text


# ==========================================================================
# server_version (WP 4e.7) — its own top-level field, not a settings row
# ==========================================================================


def test_get_reports_server_version_as_a_top_level_field(client: TestClient) -> None:
    """`vault_api.__version__` -- not env-derived, not a `settings` row (see
    `routers/settings.py`'s module docstring for the shape decision)."""
    body = client.get("/v1/settings", headers=AUTH).json()
    assert body["server_version"] == VAULT_API_VERSION
    assert "server_version" not in keys_of(body)


def test_server_version_is_unaffected_by_a_db_override_of_an_unrelated_key(
    client: TestClient,
) -> None:
    """Not a setting: nothing a PATCH does to a real key can move it."""
    client.patch("/v1/settings", json={"vault_name": "homelab"}, headers=AUTH)
    body = client.get("/v1/settings", headers=AUTH).json()
    assert body["server_version"] == VAULT_API_VERSION


def test_patch_rejects_server_version_like_any_unknown_key(
    client: TestClient,
) -> None:
    """A version is not settable -- `PATCH` must answer exactly the generic
    "not a recognised setting" 422 (not the distinct env-only 422, and not a
    silent 200) precisely because `server_version` is in neither
    `OVERRIDABLE_SPECS` nor `ENV_ONLY_KEYS`."""
    response = client.patch(
        "/v1/settings", json={"server_version": "9.9.9"}, headers=AUTH
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "not a recognised setting" in detail
    assert "environment-only" not in detail

    # And the attempt persisted nothing.
    conn = get_connection(client.app.state.settings.db_path)
    try:
        assert settings_store.get_override(conn, "server_version") is None
    finally:
        conn.close()


# ==========================================================================
# Precedence (ADR-0009 decision 1) — db > env > default
# ==========================================================================


def test_precedence_default_when_nothing_is_set(client: TestClient) -> None:
    """WP SWEEP-1 (ADR-0014): the built-in default `auto_gc` resolves to
    (with no env override and no DB override) is `execute`, not `off`, since
    the operator-decided flip -- see
    `test_config.py::test_auto_gc_defaults_to_execute` for the constant-level
    pin this test's expectation follows."""
    body = client.get("/v1/settings", headers=AUTH).json()
    row = find(body, "auto_gc")
    assert row["source"] == "default"
    assert row["effective"] == "execute"
    assert row["fallback"] == "execute"


def test_precedence_env_wins_over_default(tmp_path: Path) -> None:
    """A base ``Settings`` whose ``auto_gc`` differs from the built-in
    default simulates "the operator set VAULT_AUTO_GC" — no DB override
    exists, so the env value must be reported, not the default.
    """
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
        auto_gc="dry-run",
    )
    with TestClient(create_app(settings)) as client:
        body = client.get("/v1/settings", headers=AUTH).json()

    row = find(body, "auto_gc")
    assert row["effective"] == "dry-run"
    assert row["source"] == "env"
    assert row["fallback"] == "dry-run"


def test_precedence_db_wins_over_env(tmp_path: Path) -> None:
    """The named mutation-pinning test (docs/LEARNINGS.md "Testing
    discipline"): base/env says ``dry-run``, a DB override says ``execute``.
    If ``effective_settings``' precedence were ever flipped (env checked
    AFTER db, or the override simply ignored), this assertion would report
    ``dry-run`` and fail — the whole point of pinning db > env here rather
    than only testing db > default or env > default individually.
    """
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
        auto_gc="dry-run",
    )
    with TestClient(create_app(settings)) as client:
        patch_response = client.patch(
            "/v1/settings", json={"auto_gc": "execute"}, headers=AUTH
        )
        assert patch_response.status_code == 200
        body = client.get("/v1/settings", headers=AUTH).json()

    row = find(body, "auto_gc")
    assert row["effective"] == "execute"
    assert row["source"] == "db"
    # The fallback is what CLEARING the override reverts to -- the env value
    # (dry-run), never the hardcoded default (off). Getting this backwards
    # would tell an operator the wrong thing about what "revert" does.
    assert row["fallback"] == "dry-run"


def test_precedence_env_wins_over_default_for_sweep_include_cached(
    tmp_path: Path,
) -> None:
    """WP 4d's own precedence pin, same shape as ``auto_gc``'s above --
    a boolean-typed override needs its own test since ``_source_without_override``
    compares TYPED values and ``True != False`` must resolve the same way
    ``"dry-run" != "off"`` already does.

    WP SWEEP-1 (ADR-0014) flipped ``DEFAULT_SWEEP_INCLUDE_CACHED`` to
    ``True``, which flips which explicit value actually differs from it: a
    base ``Settings`` built with ``sweep_include_cached=True`` (this test's
    value before ADR-0014) would now equal the default and be reported as
    ``"default"``, not ``"env"`` -- exactly the false-negative
    ``_source_without_override``'s own docstring warns "an operator who
    explicitly sets an env var to the SAME value the default already has"
    produces. Using ``False`` here is what actually exercises "env differs
    from the built-in default" now.
    """
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
        sweep_include_cached=False,
    )
    with TestClient(create_app(settings)) as client:
        body = client.get("/v1/settings", headers=AUTH).json()

    row = find(body, "sweep_include_cached")
    assert row["effective"] is False
    assert row["source"] == "env"
    assert row["fallback"] is False


def test_precedence_db_wins_over_env_for_sweep_include_cached(tmp_path: Path) -> None:
    """Not in the failing set after WP SWEEP-1 (ADR-0014 flipped
    ``DEFAULT_SWEEP_INCLUDE_CACHED`` to ``True``), but the base value this
    test used (``True``) now EQUALS the hardcoded default -- the exact
    numbers still passed, but the "reverts to the env value, not the
    hardcoded default" claim in the old comment stopped being demonstrated,
    since post-ADR-0014 the two values are identical and the assertion could
    no longer tell them apart. Flipped the base to ``False`` (which now
    genuinely differs from the default) so ``fallback`` again proves
    "the env value" as distinct from "the built-in default", same fix as
    ``test_precedence_env_wins_over_default_for_sweep_include_cached`` above.
    """
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
        sweep_include_cached=False,
    )
    with TestClient(create_app(settings)) as client:
        patch_response = client.patch(
            "/v1/settings", json={"sweep_include_cached": "true"}, headers=AUTH
        )
        assert patch_response.status_code == 200
        body = client.get("/v1/settings", headers=AUTH).json()

    row = find(body, "sweep_include_cached")
    assert row["effective"] is True
    assert row["source"] == "db"
    assert row["fallback"] is False  # clearing reverts to the env value, not True


# ==========================================================================
# null clears the override (ADR-0009 decision 2)
# ==========================================================================


def test_patch_null_clears_the_override(client: TestClient) -> None:
    set_response = client.patch(
        "/v1/settings", json={"vault_name": "homelab"}, headers=AUTH
    )
    assert set_response.status_code == 200
    assert find(set_response.json(), "vault_name")["effective"] == "homelab"

    clear_response = client.patch(
        "/v1/settings", json={"vault_name": None}, headers=AUTH
    )
    assert clear_response.status_code == 200
    row = find(clear_response.json(), "vault_name")
    assert row["effective"] == ""
    assert row["source"] == "default"

    # The row itself is gone, not merely blanked -- verified at the storage
    # layer directly, one level below the HTTP response.
    conn = get_connection(client.app.state.settings.db_path)
    try:
        assert settings_store.get_override(conn, "vault_name") is None
    finally:
        conn.close()


# ==========================================================================
# PATCH validation — one grammar family per key type
# ==========================================================================


@pytest.mark.parametrize(
    "bad",
    ["not-a-window", "25:00-26:00", "09:00-09:00", "24:00-06:00"],
)
def test_patch_bad_schedule_window_is_422_and_not_persisted(
    client: TestClient, bad: str
) -> None:
    response = client.patch(
        "/v1/settings", json={"schedule_window": bad}, headers=AUTH
    )
    assert response.status_code == 422

    conn = get_connection(client.app.state.settings.db_path)
    try:
        assert settings_store.get_override(conn, "schedule_window") is None
    finally:
        conn.close()


@pytest.mark.parametrize("bad", ["abc", "0", "-3", " 7 ", "1_0", "٧", ""])
def test_patch_bad_schedule_interval_is_422_and_not_persisted(
    client: TestClient, bad: str
) -> None:
    response = client.patch(
        "/v1/settings", json={"schedule_interval_minutes": bad}, headers=AUTH
    )
    assert response.status_code == 422

    conn = get_connection(client.app.state.settings.db_path)
    try:
        assert settings_store.get_override(conn, "schedule_interval_minutes") is None
    finally:
        conn.close()


@pytest.mark.parametrize("bad", ["exectue", "on", "true", "delete", ""])
def test_patch_bad_auto_gc_is_422_and_not_persisted(
    client: TestClient, bad: str
) -> None:
    response = client.patch("/v1/settings", json={"auto_gc": bad}, headers=AUTH)
    assert response.status_code == 422

    conn = get_connection(client.app.state.settings.db_path)
    try:
        assert settings_store.get_override(conn, "auto_gc") is None
    finally:
        conn.close()


@pytest.mark.parametrize("bad", ["yeah", "1.0", "enabled", "2", ""])
def test_patch_bad_sweep_include_cached_is_422_and_not_persisted(
    client: TestClient, bad: str
) -> None:
    """Same strictness ``config.py`` applies at startup (ADR-0009 decision 4):
    ``config.parse_strict_bool`` backs both ``VAULT_SWEEP_INCLUDE_CACHED`` at
    startup and this PATCH path -- one grammar, not two."""
    response = client.patch(
        "/v1/settings", json={"sweep_include_cached": bad}, headers=AUTH
    )
    assert response.status_code == 422

    conn = get_connection(client.app.state.settings.db_path)
    try:
        assert settings_store.get_override(conn, "sweep_include_cached") is None
    finally:
        conn.close()


def test_patch_rejects_a_json_boolean_for_sweep_include_cached(
    client: TestClient,
) -> None:
    """The exact trap this key exists to demonstrate: a UI toggle naively
    sending a JSON ``true`` (not the string ``"true"``) must be a clear 422,
    never silently stored as the Python-str "True" (docs/LEARNINGS.md
    "Parsers")."""
    response = client.patch(
        "/v1/settings", json={"sweep_include_cached": True}, headers=AUTH
    )
    assert response.status_code == 422

    conn = get_connection(client.app.state.settings.db_path)
    try:
        assert settings_store.get_override(conn, "sweep_include_cached") is None
    finally:
        conn.close()


@pytest.mark.parametrize(
    "raw, expected",
    [("true", True), ("YES", True), ("on", True), ("false", False), ("0", False)],
)
def test_patch_sweep_include_cached_accepts_every_bool_spelling(
    client: TestClient, raw: str, expected: bool
) -> None:
    response = client.patch(
        "/v1/settings", json={"sweep_include_cached": raw}, headers=AUTH
    )
    assert response.status_code == 200
    assert find(response.json(), "sweep_include_cached")["effective"] is expected


def test_patch_null_clears_the_sweep_include_cached_override(
    client: TestClient,
) -> None:
    """WP SWEEP-1 (ADR-0014) flipped the built-in default to ``True``. Using
    ``"false"`` for the override (not the pre-ADR-0014 ``"true"``) is
    deliberate, not arbitrary: if the override value equalled the new
    default, a PATCH that failed to actually clear anything (a no-op bug in
    the DELETE-the-row path) would still report the same ``effective``
    value before and after and this test would not notice. Setting an
    override that DIFFERS from the default, then asserting the transition
    back to the default on clear, is what makes "clearing did something"
    observable.
    """
    set_response = client.patch(
        "/v1/settings", json={"sweep_include_cached": "false"}, headers=AUTH
    )
    assert find(set_response.json(), "sweep_include_cached")["effective"] is False

    clear_response = client.patch(
        "/v1/settings", json={"sweep_include_cached": None}, headers=AUTH
    )
    row = find(clear_response.json(), "sweep_include_cached")
    assert row["effective"] is True
    assert row["source"] == "default"

    conn = get_connection(client.app.state.settings.db_path)
    try:
        assert settings_store.get_override(conn, "sweep_include_cached") is None
    finally:
        conn.close()


@pytest.mark.parametrize(
    "bad", ["not a url", "ftp://example.invalid/hook", "example.invalid/hook"]
)
def test_patch_bad_webhook_url_is_422_and_not_persisted(
    client: TestClient, bad: str
) -> None:
    response = client.patch("/v1/settings", json={"webhook_url": bad}, headers=AUTH)
    assert response.status_code == 422

    conn = get_connection(client.app.state.settings.db_path)
    try:
        assert settings_store.get_override(conn, "webhook_url") is None
    finally:
        conn.close()


def test_patch_webhook_url_blank_is_accepted_and_disables(client: TestClient) -> None:
    """Blank is the documented "off" spelling (mirrors ``VAULT_WEBHOOK_URL``
    at startup) — accepted, not a validation failure."""
    response = client.patch("/v1/settings", json={"webhook_url": ""}, headers=AUTH)
    assert response.status_code == 200
    row = find(response.json(), "webhook_url")
    assert row["effective"] == ""
    assert row["source"] == "db"


def test_patch_webhook_events_accepts_a_json_list(client: TestClient) -> None:
    response = client.patch(
        "/v1/settings",
        json={"webhook_events": ["job.done", "job.error"]},
        headers=AUTH,
    )
    assert response.status_code == 200
    row = find(response.json(), "webhook_events")
    assert row["effective"] == ["job.done", "job.error"]


def test_patch_webhook_events_rejects_an_unknown_name(client: TestClient) -> None:
    response = client.patch(
        "/v1/settings",
        json={"webhook_events": "job.done,job.finished"},
        headers=AUTH,
    )
    assert response.status_code == 422


def test_patch_rejects_a_json_boolean(client: TestClient) -> None:
    """docs/LEARNINGS.md "Parsers": a JSON bool must never be silently
    stringified into "True"/"False"."""
    response = client.patch("/v1/settings", json={"vault_name": True}, headers=AUTH)
    assert response.status_code == 422


def test_apply_updates_is_one_transaction_all_or_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reviewer should-fix S2: ``settings_store.apply_updates`` must persist a
    whole batch as ONE committed transaction, not one commit per key. Proven
    by forcing the SECOND of two writes to raise mid-batch and then re-opening
    a FRESH connection (not the one the transaction ran on) to confirm even
    the FIRST, individually-valid write was rolled back -- the exact failure
    the old per-key-commit loop in ``routers/settings.py`` could not prevent.
    """
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        original_write = settings_store._write_override
        calls = {"n": 0}

        def flaky_write(conn_: sqlite3.Connection, key: str, raw_value: str) -> None:
            calls["n"] += 1
            if calls["n"] == 2:
                raise sqlite3.OperationalError("simulated failure on the 2nd key")
            original_write(conn_, key, raw_value)

        monkeypatch.setattr(settings_store, "_write_override", flaky_write)

        with pytest.raises(sqlite3.OperationalError):
            settings_store.apply_updates(
                conn,
                to_set=[("vault_name", "homelab"), ("auto_gc", "execute")],
                to_clear=[],
            )
    finally:
        conn.close()

    fresh_conn = get_connection(db_path)
    try:
        assert settings_store.get_override(fresh_conn, "vault_name") is None
        assert settings_store.get_override(fresh_conn, "auto_gc") is None
    finally:
        fresh_conn.close()


def test_patch_500_on_a_mid_batch_db_error_persists_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same guarantee as the unit test above, exercised through the real
    ``PATCH /v1/settings`` HTTP path (the reviewer's pin explicitly named
    ``routers/settings.py``, not just ``settings_store.py``)."""
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
    )
    app = create_app(settings)
    test_client = TestClient(app, raise_server_exceptions=False)

    original_write = settings_store._write_override
    calls = {"n": 0}

    def flaky_write(conn_: sqlite3.Connection, key: str, raw_value: str) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise sqlite3.OperationalError("simulated failure on the 2nd key")
        original_write(conn_, key, raw_value)

    monkeypatch.setattr(settings_store, "_write_override", flaky_write)

    with test_client as client:
        response = client.patch(
            "/v1/settings",
            json={"vault_name": "homelab", "auto_gc": "execute"},
            headers=AUTH,
        )
        assert response.status_code == 500

        conn = get_connection(settings.db_path)
        try:
            assert settings_store.get_override(conn, "vault_name") is None
            assert settings_store.get_override(conn, "auto_gc") is None
        finally:
            conn.close()


def test_patch_multi_key_failure_persists_nothing(client: TestClient) -> None:
    """One bad value in a multi-key PATCH must fail the WHOLE request and
    persist NEITHER key -- not just skip the bad one."""
    response = client.patch(
        "/v1/settings",
        json={"vault_name": "homelab", "auto_gc": "bogus-mode"},
        headers=AUTH,
    )
    assert response.status_code == 422

    conn = get_connection(client.app.state.settings.db_path)
    try:
        assert settings_store.get_override(conn, "vault_name") is None
        assert settings_store.get_override(conn, "auto_gc") is None
    finally:
        conn.close()


def test_patch_unknown_key_is_422(client: TestClient) -> None:
    response = client.patch(
        "/v1/settings", json={"totally_bogus_key": "x"}, headers=AUTH
    )
    assert response.status_code == 422
    assert "not a recognised setting" in response.json()["detail"]


@pytest.mark.parametrize(
    "key",
    [
        "vault_api_key",
        "db_path",
        "cache_root",
        "steamprefill_path",
        "steamprefill_cache_dir",
        "manifest_archive_dir",
        "web_dir",
        "settings_readonly",
        # WP 4h.0 (ADR-0010) -- see tests/test_relay_privacy.py for the full
        # pin (both defaults, both directions of independence); this just
        # keeps them in the one canonical list of every env-only key.
        "relay_expose_playtime",
        "relay_expose_last_played",
    ],
)
def test_patch_env_only_key_is_422_with_a_distinct_detail(
    client: TestClient, key: str
) -> None:
    response = client.patch("/v1/settings", json={key: "x"}, headers=AUTH)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "environment-only" in detail
    # The two 422 reasons must be tellable apart by a caller/UI -- an
    # env-only key must NEVER produce the "unknown setting" wording.
    assert "not a recognised setting" not in detail


# ==========================================================================
# Readonly hard-lock (ADR-0009 decision 3)
# ==========================================================================


def test_readonly_blocks_patch_but_not_get(tmp_path: Path) -> None:
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
        settings_readonly=True,
    )
    with TestClient(create_app(settings)) as client:
        get_response = client.get("/v1/settings", headers=AUTH)
        assert get_response.status_code == 200
        assert get_response.json()["readonly"] is True

        patch_response = client.patch(
            "/v1/settings", json={"vault_name": "x"}, headers=AUTH
        )
        assert patch_response.status_code == 403
        assert "read-only" in patch_response.json()["detail"]

        # Nothing was written despite the attempt.
        conn = get_connection(settings.db_path)
        try:
            assert settings_store.get_override(conn, "vault_name") is None
        finally:
            conn.close()


def test_readonly_checked_before_body_validation(tmp_path: Path) -> None:
    """403, not 422 -- the lock wins even over a body that would also have
    failed on its own merits."""
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
        settings_readonly=True,
    )
    with TestClient(create_app(settings)) as client:
        response = client.patch(
            "/v1/settings", json={"auto_gc": "not-a-real-mode"}, headers=AUTH
        )
    assert response.status_code == 403


# ==========================================================================
# Redaction (ADR-0009 decision 7) — string-literal pins
# ==========================================================================


def test_webhook_url_userinfo_is_redacted_in_the_patch_response(
    client: TestClient,
) -> None:
    secret_url = "https://opsbot:hunter2@example.invalid/hook"
    response = client.patch(
        "/v1/settings", json={"webhook_url": secret_url}, headers=AUTH
    )
    assert response.status_code == 200

    # STRING-LITERAL pin (docs/LEARNINGS.md): the secret must be provably
    # absent from the raw text, not merely "different from what redact_url
    # would produce if the test called it again".
    assert "hunter2" not in response.text
    assert "opsbot" not in response.text
    row = find(response.json(), "webhook_url")
    assert row["effective"] == "https://***@example.invalid/hook"


def test_webhook_url_userinfo_is_redacted_in_get_after_patch(
    client: TestClient,
) -> None:
    client.patch(
        "/v1/settings",
        json={"webhook_url": "https://opsbot:hunter2@example.invalid/hook"},
        headers=AUTH,
    )
    response = client.get("/v1/settings", headers=AUTH)
    assert "hunter2" not in response.text


def test_webhook_url_userinfo_is_redacted_in_the_fallback_field(
    tmp_path: Path,
) -> None:
    """The FALLBACK value (what clearing the override reverts to) must be
    redacted too, not only the effective one -- it carries the same
    credential when the env-configured URL itself has userinfo."""
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
        webhook_url="https://envuser:envsecret@example.invalid/hook",
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/v1/settings", headers=AUTH)

    assert "envsecret" not in response.text
    row = find(response.json(), "webhook_url")
    assert row["fallback"] == "https://***@example.invalid/hook"


def test_effective_settings_precedence_directly(tmp_path: Path) -> None:
    """Unit-level pin on ``settings_store.effective_settings`` itself — the
    function every LIVE consumer (scheduler tick, worker job-finish) actually
    calls — separate from the ``describe_settings``/GET-response precedence
    tests above, which exercise a DIFFERENT code path
    (``routers/settings.py`` -> ``describe_settings``) that happens to
    duplicate the same db/env/default decision for the read model. A
    precedence bug in ONE of the two would not necessarily show up in the
    other, so both get their own direct pin.
    """
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        base = Settings(
            vault_api_key=TEST_API_KEY,
            db_path=db_path,
            cache_root=str(tmp_path / "cache"),
            log_level="INFO",
            auto_gc="dry-run",  # simulates VAULT_AUTO_GC=dry-run
        )

        # No override yet: env value wins over the built-in default.
        assert settings_store.effective_settings(conn, base).auto_gc == "dry-run"

        # An override now must win over that env value.
        settings_store.set_override(conn, "auto_gc", "execute")
        assert settings_store.effective_settings(conn, base).auto_gc == "execute"

        # Clearing it must revert to the env value, not the default.
        settings_store.delete_override(conn, "auto_gc")
        assert settings_store.effective_settings(conn, base).auto_gc == "dry-run"
    finally:
        conn.close()


def test_b1_scheduler_thread_exists_on_a_bare_boot_and_a_patched_window_sweeps(
    tmp_path: Path,
) -> None:
    """Reviewer blocker B1, required pin: boot with NO ``VAULT_SCHEDULE_WINDOW``
    and NO event log (the stock/default deployment), then ``PATCH`` a window
    in and prove a REAL sweep runs — end to end through the real FastAPI
    lifespan, not by calling ``scheduler_module.maybe_sweep`` directly.

    Before the fix, ``main.py`` only called ``scheduler.start()`` when
    ``scheduler.thread_needed`` was true at BOOT. On this exact boot
    configuration that property is ``False`` (no window, no event log), so
    the thread never existed and no ``PATCH`` could ever make a sweep run —
    ``GET /v1/schedule`` would report a ``next_eligible_at`` that could never
    arrive. This test fails against that old gate and passes now that
    ``main.py`` starts the thread unconditionally.
    """
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
        # Deliberately bare: this is the exact boot shape the blocker named.
    )
    app = create_app(settings)
    # Fast tick so the test does not have to wait a real minute for the first
    # one; everything else (the real lifespan, the real thread, the real
    # scheduler/settings-store code) is exercised unmodified.
    app.state.scheduler = scheduler_module.PrefillScheduler(settings, tick_seconds=0.05)

    with TestClient(app) as client:
        thread = client.app.state.scheduler._thread
        assert thread is not None and thread.is_alive(), (
            "the scheduler thread must exist even though nothing was "
            "configured at boot -- PATCH /v1/settings needs it to tick"
        )

        patch_response = client.patch(
            "/v1/settings",
            json={"schedule_window": "00:00-24:00"},
            headers=AUTH,
        )
        assert patch_response.status_code == 200
        assert find(patch_response.json(), "schedule_window")["effective"] == (
            "00:00-24:00"
        )

        deadline = time.monotonic() + 10.0
        last_sweep_at = None
        schedule_body: dict = {}
        while time.monotonic() < deadline:
            schedule_body = client.get("/v1/schedule", headers=AUTH).json()
            if schedule_body.get("enabled") and schedule_body.get("last_sweep_at"):
                last_sweep_at = schedule_body["last_sweep_at"]
                break
            time.sleep(0.05)

        assert last_sweep_at is not None, (
            f"a sweep never ran after PATCHing schedule_window: {schedule_body}"
        )
        assert schedule_body["enabled"] is True


# ==========================================================================
# Wiring: the override actually takes effect
# ==========================================================================


def test_effective_settings_ignores_a_corrupt_stored_override(tmp_path: Path) -> None:
    """A row written outside the validated PATCH path (the documented sqlite3
    escape hatch, or simply an older/incompatible value) must not crash a
    live request or the scheduler tick -- it is logged and treated as if the
    key had no override at all.
    """
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
            ("auto_gc", "not-a-real-mode", "2026-08-10T00:00:00Z"),
        )
        conn.commit()

        base = Settings(
            vault_api_key=TEST_API_KEY,
            db_path=db_path,
            cache_root=str(tmp_path / "cache"),
            log_level="INFO",
        )
        effective = settings_store.effective_settings(conn, base)
    finally:
        conn.close()

    # WP SWEEP-1 (ADR-0014): the built-in default `auto_gc` falls back to
    # is `execute`, not `off`, since the operator-decided flip.
    assert effective.auto_gc == "execute"  # fell back to the built-in default


def test_scheduler_tick_picks_up_an_interval_override_next_sweep(
    tmp_path: Path,
) -> None:
    """Unit-level proof of the "next_sweep" apply semantics documented in
    ``vault_api/settings_store.py``: a sweep claimed under the ORIGINAL
    180-minute interval is not due again a minute later, but IS due once a
    DB override shortens the interval -- exactly what
    ``vault_api/scheduler.py``'s tick loop now resolves every tick via
    ``effective_settings``.
    """
    from datetime import timedelta, timezone

    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        base = Settings(
            vault_api_key=TEST_API_KEY,
            db_path=db_path,
            cache_root=str(tmp_path / "cache"),
            log_level="INFO",
            schedule_window=parse_window("00:00-24:00"),
            schedule_interval_minutes=180,
        )
        now = scheduler_module.local_now().astimezone(timezone.utc)

        first = scheduler_module.maybe_sweep(conn, base, now)
        assert first.swept is True

        one_minute_later = now + timedelta(minutes=1)
        still_base = scheduler_module.maybe_sweep(conn, base, one_minute_later)
        assert still_base.skipped_reason == "interval-not-elapsed"

        settings_store.set_override(conn, "schedule_interval_minutes", "1")
        effective = settings_store.effective_settings(conn, base)
        assert effective.schedule_interval_minutes == 1

        two_minutes_later = now + timedelta(minutes=2)
        overridden = scheduler_module.maybe_sweep(conn, effective, two_minutes_later)
        assert overridden.swept is True
    finally:
        conn.close()


def test_scheduler_tick_picks_up_a_sweep_include_cached_override_next_sweep(
    tmp_path: Path,
) -> None:
    """WP 4d's own version of the test above: a cached-but-uninstalled app is
    invisible to the first sweep and picked up by the very next sweep once a
    ``PATCH`` turns the mode on -- ``next_sweep``, not ``restart-required``.

    WP SWEEP-1 (ADR-0014) flipped the mode's own default to ``True``, so
    ``base`` here explicitly overrides it to ``False`` (mirroring
    ``test_precedence_env_wins_over_default_for_sweep_include_cached``'s own
    fix for the same reason) -- this test's point is the ``next_sweep``
    transition an override produces, not which value happens to be the
    built-in default, and starting from ``base.sweep_include_cached is True``
    (the new default) would have made the "first sweep, mode off" half of
    the transition impossible to set up at all.
    """
    from datetime import timedelta, timezone

    from vault_api.mapping import upsert_mapping

    db_path = str(tmp_path / "vault.db")
    cache_root = tmp_path / "cache"
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        depot_dir = cache_root / "depot" / "441" / "chunk"
        depot_dir.mkdir(parents=True)
        (depot_dir / "a.bin").write_bytes(b"x")
        upsert_mapping(conn, depotid=441, appid=440, name="TF2")

        base = Settings(
            vault_api_key=TEST_API_KEY,
            db_path=db_path,
            cache_root=str(cache_root),
            log_level="INFO",
            schedule_window=parse_window("00:00-24:00"),
            schedule_interval_minutes=1,
            sweep_include_cached=False,
        )
        now = scheduler_module.local_now().astimezone(timezone.utc)

        first = scheduler_module.maybe_sweep(conn, base, now)
        assert first.swept is True
        assert first.targets == ()  # mode explicitly off: cache content ignored

        settings_store.set_override(conn, "sweep_include_cached", "true")
        effective = settings_store.effective_settings(conn, base)
        assert effective.sweep_include_cached is True

        one_minute_later = now + timedelta(minutes=1)
        overridden = scheduler_module.maybe_sweep(conn, effective, one_minute_later)
        assert overridden.swept is True
        assert overridden.targets == (440,)
        assert overridden.cached_only_appids == (440,)
    finally:
        conn.close()


def test_s2_bare_boot_patch_enables_cached_mode_and_a_real_sweep_enqueues_it(
    tmp_path: Path,
) -> None:
    """S2 (reviewer should-fix, 2026-08-18 review round): the unit-level test
    above proves ``effective_settings`` resolves the override; this proves
    the actual PROMISE behind ``applies: "next_sweep"`` (as opposed to
    ``"restart-required"``) end to end -- bare boot (no window, cached mode
    off, the stock/default shape ADR-0009's B1 finding is about), a single
    ``PATCH`` turns the window AND the cached-apps mode on together, and a
    REAL background sweep thread enqueues the cached-only app with no
    restart in between.
    """
    from vault_api.mapping import upsert_mapping

    cache_root = tmp_path / "cache"
    depot_dir = cache_root / "depot" / "441" / "chunk"
    depot_dir.mkdir(parents=True)
    (depot_dir / "a.bin").write_bytes(b"x")

    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(cache_root),
        log_level="INFO",
        # Deliberately bare: no window, cached mode off -- the exact boot
        # shape a stock install has.
    )
    app = create_app(settings)
    app.state.scheduler = scheduler_module.PrefillScheduler(settings, tick_seconds=0.05)

    conn = get_connection(settings.db_path)
    try:
        upsert_mapping(conn, depotid=441, appid=440, name="TF2")
    finally:
        conn.close()

    with TestClient(app) as client:
        patch_response = client.patch(
            "/v1/settings",
            json={
                "schedule_window": "00:00-24:00",
                "sweep_include_cached": "true",
            },
            headers=AUTH,
        )
        assert patch_response.status_code == 200
        body = patch_response.json()
        assert find(body, "schedule_window")["effective"] == "00:00-24:00"
        assert find(body, "sweep_include_cached")["effective"] is True

        deadline = time.monotonic() + 10.0
        jobs_seen: list = []
        while time.monotonic() < deadline:
            jobs_seen = client.get("/v1/jobs", headers=AUTH).json()
            if any(job["appid"] == 440 for job in jobs_seen):
                break
            time.sleep(0.05)

        assert any(job["appid"] == 440 for job in jobs_seen), (
            f"the cached-only app was never enqueued by a real sweep: {jobs_seen}"
        )


def test_worker_auto_gc_override_applies_to_the_next_completed_job(
    tmp_path: Path,
) -> None:
    """End-to-end through the REAL worker thread (not a unit call): base
    ``Settings`` says ``VAULT_AUTO_GC=off``, a ``PATCH`` overrides it to
    ``execute`` BEFORE the job is enqueued, and the prefill the worker
    actually runs must queue an executing GC job -- proving the
    ``settings_store.effective_settings`` call inside
    ``worker._maybe_queue_auto_gc`` is real, not just unit-testable.
    """
    bindir = tmp_path / "bin"
    cache_root = tmp_path / "cache"
    executable = stub_prefill.make_stub(
        bindir,
        mode="success",
        cache_root=str(cache_root),
        depots_by_app={440: [441]},
        summary_text=(
            "  Prefilled 1 apps totaling 12 MiB in 05.0000 \n"
            "   Updated | Up To Date\n"
            "  ---------+------------\n"
            "      1    |     0\n"
        ),
    )
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(cache_root),
        log_level="INFO",
        steamprefill_path=executable,
        prefill_timeout_seconds=60,
        worker_poll_seconds=0.02,
        steamprefill_cache_dir=str(tmp_path / "unused-steamprefill-cache"),
        manifest_archive_dir=str(tmp_path / "manifest-archive"),
        auto_gc="off",
    )
    with TestClient(create_app(settings)) as client:
        patch_response = client.patch(
            "/v1/settings", json={"auto_gc": "execute"}, headers=AUTH
        )
        assert patch_response.status_code == 200

        enqueue_response = client.post(
            "/v1/prefill", json={"appids": [440]}, headers=AUTH
        )
        assert enqueue_response.status_code == 202
        job_id = int(enqueue_response.json()[0]["job_id"])

        deadline = time.monotonic() + 30.0
        job: dict = {}
        while time.monotonic() < deadline:
            job = client.get(f"/v1/jobs/{job_id}", headers=AUTH).json()
            if job["status"] in ("done", "error", "cancelled"):
                break
            time.sleep(0.05)
        assert job.get("status") == "done", job

        conn = get_connection(settings.db_path)
        try:
            gc_rows = conn.execute(
                "SELECT gc_execute FROM jobs WHERE appid = 440 AND type = 'gc'"
            ).fetchall()
        finally:
            conn.close()

    assert len(gc_rows) == 1
    assert gc_rows[0]["gc_execute"] == 1


# ==========================================================================
# WP API-FIX-2
# ==========================================================================


@pytest.mark.parametrize(
    ("key", "cap"),
    [
        ("schedule_interval_minutes", config.MAX_INTERVAL_MINUTES),
        ("schedule_client_stale_days", config.MAX_DAYS),
    ],
)
def test_patch_over_the_timedelta_cap_is_422_and_not_persisted(
    client: TestClient, key: str, cap: int
) -> None:
    """S2: before the cap, a huge digit string was stored and every consumer
    that fed it into ``timedelta`` (GET /v1/games, GET /v1/schedule, the
    scheduler tick) raised ``OverflowError``."""
    response = client.patch("/v1/settings", json={key: str(cap + 1)}, headers=AUTH)
    assert response.status_code == 422
    assert f"must be <= {cap}" in response.json()["detail"]

    conn = get_connection(client.app.state.settings.db_path)
    try:
        assert settings_store.get_override(conn, key) is None
    finally:
        conn.close()


def test_patch_at_the_cap_keeps_games_and_schedule_answering(client: TestClient) -> None:
    response = client.patch(
        "/v1/settings",
        json={
            "schedule_interval_minutes": str(config.MAX_INTERVAL_MINUTES),
            "schedule_client_stale_days": str(config.MAX_DAYS),
        },
        headers=AUTH,
    )
    assert response.status_code == 200
    assert find(response.json(), "schedule_interval_minutes")["effective"] == (
        config.MAX_INTERVAL_MINUTES
    )

    assert client.get("/v1/games", headers=AUTH).status_code == 200
    assert client.get("/v1/schedule", headers=AUTH).status_code == 200


def test_a_stored_overflowing_interval_no_longer_breaks_reads(client: TestClient) -> None:
    """The escape-hatch path: a value written straight into the settings
    table (or stored before the cap existed) is re-validated on every read
    and treated as absent -- so the routes above answer 200, not 500."""
    conn = get_connection(client.app.state.settings.db_path)
    try:
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
            ("schedule_interval_minutes", "9" * 30, "2026-08-09T12:00:00Z"),
        )
        conn.commit()
    finally:
        conn.close()

    assert client.get("/v1/games", headers=AUTH).status_code == 200
    assert client.get("/v1/schedule", headers=AUTH).status_code == 200
    body = client.get("/v1/settings", headers=AUTH).json()
    assert find(body, "schedule_interval_minutes")["effective"] == (
        config.DEFAULT_SCHEDULE_INTERVAL_MINUTES
    )


@pytest.mark.parametrize(
    "bad",
    [
        "x" * (settings_store.MAX_VAULT_NAME_LENGTH + 1),
        "home\nlab",
        "home\tlab",
        "home\x00lab",
    ],
)
def test_patch_vault_name_is_bounded_and_printable(client: TestClient, bad: str) -> None:
    """P3: vault_name lands in every webhook envelope and in log lines; it
    gets client_id's rule (64 chars, printable)."""
    response = client.patch("/v1/settings", json={"vault_name": bad}, headers=AUTH)
    assert response.status_code == 422

    conn = get_connection(client.app.state.settings.db_path)
    try:
        assert settings_store.get_override(conn, "vault_name") is None
    finally:
        conn.close()


def test_patch_vault_name_accepts_the_maximum_length(client: TestClient) -> None:
    name = "h" * settings_store.MAX_VAULT_NAME_LENGTH
    response = client.patch("/v1/settings", json={"vault_name": f"  {name}  "}, headers=AUTH)
    assert response.status_code == 200
    assert find(response.json(), "vault_name")["effective"] == name


def test_patch_bad_webhook_url_detail_never_echoes_userinfo(client: TestClient) -> None:
    """P2, response side: the 422 detail used to end in ``Got '<raw>'``."""
    response = client.patch(
        "/v1/settings", json={"webhook_url": "ftp://user:s3cr3t@host/path"}, headers=AUTH
    )
    assert response.status_code == 422
    assert "s3cr3t" not in response.text


def test_effective_settings_redacts_a_corrupt_secret_override_in_the_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """P2, log side: a stored webhook_url that no longer validates was logged
    RAW, userinfo included -- into docker logs."""
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    base = Settings(
        vault_api_key=TEST_API_KEY, db_path=db_path, cache_root=str(tmp_path), log_level="INFO"
    )
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
            ("webhook_url", "ftp://user:s3cr3t@hooks.example/x", "2026-08-09T12:00:00Z"),
        )
        conn.commit()
        with caplog.at_level("ERROR", logger="vault_api.settings_store"):
            effective = settings_store.effective_settings(conn, base)
    finally:
        conn.close()

    assert effective.webhook_url == ""
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "webhook_url" in text
    assert "s3cr3t" not in text
    assert "<redacted>" in text


def test_effective_settings_redacts_a_corrupt_scheme_less_secret_override(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """S1: redact_url leaves scheme-less ``admin:s3cr3t@host/x`` unchanged,
    so a secret key's corrupt value is logged as a fixed placeholder."""
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    base = Settings(
        vault_api_key=TEST_API_KEY, db_path=db_path, cache_root=str(tmp_path), log_level="INFO"
    )
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
            ("webhook_url", "admin:s3cr3t@hooks.example/x", "2026-08-09T12:00:00Z"),
        )
        conn.commit()
        with caplog.at_level("ERROR", logger="vault_api.settings_store"):
            effective = settings_store.effective_settings(conn, base)
    finally:
        conn.close()

    assert effective.webhook_url == ""
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "webhook_url" in text
    assert "s3cr3t" not in text
    assert "admin" not in text
    assert "<redacted>" in text
