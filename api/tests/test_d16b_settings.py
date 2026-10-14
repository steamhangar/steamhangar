"""D16b: settings API for the expert mode (ADR-0009 addendum 2026-10-14).

Guarantees, one named test (or group) each:

G1  env always wins (Q1): a pinned key ignores the stored value, GET says
    source "env", editable false, shadowed true; PATCH answers 409.
G2  the mutation pin for G1's direction: without the pin the stored value
    wins (so a flipped precedence kills a named test either way).
G3  ``Settings.from_env`` pins exactly the non-blank ``VAULT_*`` variables
    (blank = not set, Q7).
G4  one startup warning per shadowed key, one for a stored webhook URL (Q2),
    one for the deprecated ``VAULT_UPSTREAM_RATE_WINDOW`` (Q3).
G5  ``webhook_url`` is env-only and shown masked (Q2).
G6  risky keys need ``confirm_risky=true`` (428 otherwise), set and clear;
    the risky set and the expert flag follow the D16a table sign-off.
G7  cross-variable rules of audit 3.4 at PATCH time, only for touched keys.
G8  the ``off`` keyword for the window, blank PATCH = reset (Q7).
G9  GET carries the new per-key fields for the web package.
G10 the core settings file: off by default, written after a PATCH when a
    path is set, with the rate window derived from the download window (Q3).
G11 live wiring: log level immediately, prefill threads per runner job,
    request-time keys through ``deps.effective_settings``.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests import stub_prefill
from tests.conftest import TEST_API_KEY
from tests.test_prefill_runner_process import _queue_settings, _wait_for_job
from vault_api import config, deps, settings_store
from vault_api.config import Settings
from vault_api.db import get_connection, init_db
from vault_api.main import create_app
from vault_api.prefill_runner import PrefillRunner
from vault_api.worker import PrefillWorker

AUTH = {"X-Api-Key": TEST_API_KEY}


def _settings(tmp_path: Path, **kwargs) -> Settings:
    return Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
        **kwargs,
    )


def _store(settings: Settings, **values: str) -> None:
    init_db(settings.db_path)
    conn = get_connection(settings.db_path)
    try:
        for key, raw in values.items():
            settings_store.set_override(conn, key, raw)
    finally:
        conn.close()


def _row(body: dict, key: str) -> dict:
    for row in body["settings"]:
        if row["key"] == key:
            return row
    raise AssertionError(f"{key!r} missing: {body}")


# -- G1 / G2 ------------------------------------------------------------------


def test_env_pin_wins_over_a_stored_value(tmp_path: Path) -> None:
    settings = _settings(tmp_path, auto_gc="dry-run", env_pinned=frozenset({"VAULT_AUTO_GC"}))
    _store(settings, auto_gc="off")
    conn = get_connection(settings.db_path)
    try:
        assert settings_store.effective_settings(conn, settings).auto_gc == "dry-run"
    finally:
        conn.close()
    with TestClient(create_app(settings)) as client:
        row = _row(client.get("/v1/settings", headers=AUTH).json(), "auto_gc")
        response = client.patch("/v1/settings", json={"auto_gc": "execute"}, headers=AUTH)
        clear = client.patch("/v1/settings", json={"auto_gc": None}, headers=AUTH)
    assert row["source"] == "env"
    assert row["effective"] == "dry-run"
    assert row["editable"] is False
    assert row["shadowed"] is True
    assert row["value"] == "off"
    assert response.status_code == 409
    assert "VAULT_AUTO_GC" in response.json()["detail"]
    assert clear.status_code == 409


def test_without_a_pin_the_stored_value_wins(tmp_path: Path) -> None:
    settings = _settings(tmp_path, auto_gc="dry-run")
    _store(settings, auto_gc="off")
    conn = get_connection(settings.db_path)
    try:
        assert settings_store.effective_settings(conn, settings).auto_gc == "off"
    finally:
        conn.close()
    with TestClient(create_app(settings)) as client:
        row = _row(client.get("/v1/settings", headers=AUTH).json(), "auto_gc")
    assert (row["source"], row["effective"], row["editable"], row["shadowed"]) == (
        "db", "off", True, False,
    )
    assert row["fallback"] == "dry-run"


# -- G3 -----------------------------------------------------------------------


def test_from_env_pins_only_non_blank_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "k")
    monkeypatch.setenv("VAULT_AUTO_GC", "dry-run")
    monkeypatch.setenv("VAULT_GC_GRACE_DAYS", "")
    monkeypatch.setenv("VAULT_MANIFEST_KEEP", "   ")
    monkeypatch.delenv("VAULT_SCHEDULE_WINDOW", raising=False)
    settings = Settings.from_env()
    assert "VAULT_AUTO_GC" in settings.env_pinned
    assert "VAULT_GC_GRACE_DAYS" not in settings.env_pinned
    assert "VAULT_MANIFEST_KEEP" not in settings.env_pinned
    assert "VAULT_SCHEDULE_WINDOW" not in settings.env_pinned
    assert settings.gc_grace_days == config.DEFAULT_GC_GRACE_DAYS


# -- G4 -----------------------------------------------------------------------


def test_startup_warns_once_per_shadowed_key_and_for_deprecations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    settings = _settings(
        tmp_path, env_pinned=frozenset({"VAULT_AUTO_GC", "VAULT_MANIFEST_KEEP"})
    )
    _store(settings, auto_gc="off", gc_grace_days="3", webhook_url="https://u:p@hook.example/x")
    monkeypatch.setenv("VAULT_UPSTREAM_RATE_WINDOW", "03:00-07:00")
    caplog.set_level(logging.WARNING, logger="vault_api.settings_store")
    create_app(settings)
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    shadow = [m for m in warnings if "wins over the value stored" in m]
    assert len(shadow) == 1 and "'auto_gc'" in shadow[0]
    assert sum("webhook URL is stored" in m for m in warnings) == 1
    assert sum("VAULT_UPSTREAM_RATE_WINDOW is deprecated" in m for m in warnings) == 1
    assert not any("u:p" in m for m in warnings)


# -- G5 -----------------------------------------------------------------------


def test_webhook_url_is_env_only_and_masked(tmp_path: Path) -> None:
    settings = _settings(tmp_path, webhook_url="https://user:secret@hooks.example/api/token123?x=1")
    _store(settings, webhook_url="https://evil.example/")
    conn = get_connection(settings.db_path)
    try:
        effective = settings_store.effective_settings(conn, settings)
    finally:
        conn.close()
    assert effective.webhook_url == settings.webhook_url
    with TestClient(create_app(settings)) as client:
        response = client.get("/v1/settings", headers=AUTH)
        patch = client.patch("/v1/settings", json={"webhook_url": "https://x.example/"}, headers=AUTH)
    assert "secret" not in response.text and "token123" not in response.text
    row = _row(response.json(), "webhook_url")
    assert row["env_only"] is True and row["editable"] is False
    assert row["effective"] == "https://***@hooks.example/***"
    assert row["shadowed"] is True
    assert patch.status_code == 422
    assert "environment-only" in patch.json()["detail"]


@pytest.mark.parametrize(
    "url,shown",
    [
        ("", ""),
        ("https://hooks.example", "https://hooks.example"),
        ("https://hooks.example/", "https://hooks.example"),
        ("http://hooks.example:8080/a/b", "http://hooks.example:8080/***"),
        ("https://discord.com/api/webhooks/1/tok", "https://discord.com/***"),
        ("https://a:b@h.example/?k=v", "https://***@h.example/***"),
        ("not a url", "***"),
    ],
)
def test_mask_url(url: str, shown: str) -> None:
    assert settings_store.mask_url(url) == shown


# -- G6 -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "body", [{"resolver": "9.9.9.9"}, {"upstream_edge": "off"}, {"upstream_pool_hosts": "off"},
             {"resolver": None}, {"upstream_max_conns": "32"}, {"tls_max_conns": "300"},
             {"tls_client_max_conns": "32"}, {"prefill_max_threads": "4"}]
)
def test_risky_keys_need_the_confirm_flag(client: TestClient, body: dict) -> None:
    refused = client.patch("/v1/settings", json={"vault_name": "x", **body}, headers=AUTH)
    assert refused.status_code == 428
    assert refused.json()["detail"]["risky_keys"] == sorted(body)
    # Nothing persisted, not even the non-risky key of the batch.
    assert _row(client.get("/v1/settings", headers=AUTH).json(), "vault_name")["source"] == "default"
    accepted = client.patch(
        "/v1/settings", params={"confirm_risky": "true"}, json=body, headers=AUTH
    )
    assert accepted.status_code == 200, accepted.text


def test_non_risky_keys_need_no_confirm(client: TestClient) -> None:
    response = client.patch(
        "/v1/settings",
        json={"upstream_rate": "800k", "schedule_window": "22:00-06:00"},
        headers=AUTH,
    )
    assert response.status_code == 200
    row = _row(response.json(), "upstream_rate")
    assert (row["value"], row["effective"], row["source"]) == ("800k", "800k", "db")


def test_risky_flags_follow_the_d16a_sign_off() -> None:
    """User sign-off 2026-10-14: the network/load caps and the debug knobs,
    but not the log level, the rate or the window."""
    risky = {k for k, s in settings_store.OVERRIDABLE_SPECS.items() if s.risky}
    assert risky == {
        "upstream_edge", "upstream_pool_hosts", "resolver", "prefill_max_threads",
        "upstream_max_conns", "tls_client_max_conns", "tls_max_conns",
    }


def test_expert_flags_follow_the_d16a_sign_off() -> None:
    normal = {k for k, s in settings_store.OVERRIDABLE_SPECS.items() if not s.expert}
    assert normal == {
        "schedule_window", "auto_gc", "vault_name", "steam_library_steamid",
        "bypass_window_days", "webhook_events", "webhook_timeout_seconds",
        "event_log_rotate_bytes",
    }


# -- G7 -----------------------------------------------------------------------


CONFIRM = {"confirm_risky": "true"}


def test_upstream_cap_must_cover_the_prefill_threads(client: TestClient) -> None:
    low = client.patch(
        "/v1/settings", params=CONFIRM, json={"upstream_max_conns": "4"}, headers=AUTH
    )
    assert low.status_code == 422 and "prefill_max_threads" in low.json()["detail"]
    many = client.patch(
        "/v1/settings", params=CONFIRM, json={"prefill_max_threads": "32"}, headers=AUTH
    )
    assert many.status_code == 422
    both = client.patch(
        "/v1/settings", params=CONFIRM,
        json={"prefill_max_threads": "32", "upstream_max_conns": "32"}, headers=AUTH,
    )
    assert both.status_code == 200
    # An unrelated PATCH is not judged on these keys.
    assert client.patch("/v1/settings", json={"vault_name": "n"}, headers=AUTH).status_code == 200


def test_tls_client_cap_must_not_exceed_the_total(client: TestClient) -> None:
    response = client.patch(
        "/v1/settings", params=CONFIRM, json={"tls_max_conns": "32"}, headers=AUTH
    )
    assert response.status_code == 422 and "tls_client_max_conns" in response.json()["detail"]
    ok = client.patch(
        "/v1/settings", params=CONFIRM,
        json={"tls_max_conns": "32", "tls_client_max_conns": "16"}, headers=AUTH,
    )
    assert ok.status_code == 200


def test_cap_floor_uses_the_env_pinned_thread_count(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path, prefill_max_threads=20, env_pinned=frozenset({"VAULT_PREFILL_MAX_THREADS"})
    )
    with TestClient(create_app(settings)) as client:
        response = client.patch(
            "/v1/settings", params=CONFIRM, json={"upstream_max_conns": "16"}, headers=AUTH
        )
    assert response.status_code == 422


# -- G8 -----------------------------------------------------------------------


def test_window_off_and_blank_reset(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        off = client.patch("/v1/settings", json={"schedule_window": "off"}, headers=AUTH)
        assert _row(off.json(), "schedule_window")["effective"] == "off"
        conn = get_connection(settings.db_path)
        try:
            assert settings_store.get_override(conn, "schedule_window") == "off"
            assert settings_store.effective_settings(conn, settings).schedule_window is None
        finally:
            conn.close()
        reset = client.patch("/v1/settings", json={"schedule_window": "  "}, headers=AUTH)
        assert _row(reset.json(), "schedule_window")["source"] == "default"
        bad = client.patch("/v1/settings", json={"schedule_window": "OFF"}, headers=AUTH)
        assert bad.status_code == 422


def test_a_legacy_blank_stored_window_still_means_off(tmp_path: Path) -> None:
    settings = _settings(tmp_path, schedule_window=None)
    _store(settings, schedule_window="")
    conn = get_connection(settings.db_path)
    try:
        assert settings_store.effective_settings(conn, settings).schedule_window is None
    finally:
        conn.close()


# -- G9 -----------------------------------------------------------------------


def test_get_rows_carry_the_expert_fields(client: TestClient) -> None:
    body = client.get("/v1/settings", headers=AUTH).json()
    rate = _row(body, "upstream_rate")
    assert rate == {
        "key": "upstream_rate", "effective": None, "source": "default", "fallback": None,
        "applies": "core", "env_only": False, "env_var": "VAULT_UPSTREAM_RATE",
        "value": None, "editable": True, "risky": False, "expert": True,
        "grammar": rate["grammar"], "target": "core", "env_visible": False, "shadowed": False,
    }
    assert "off" in rate["grammar"]
    edge = _row(body, "upstream_edge")
    assert edge["effective"] == "dist-fra1.discovery.steamserver.net" and edge["risky"] is True
    assert _row(body, "schedule_window")["expert"] is False
    assert _row(body, "db_path")["expert"] is False
    threads = _row(body, "prefill_max_threads")
    assert threads["target"] == "api+core" and threads["env_visible"] is True
    assert threads["effective"] == 8
    keys = [row["key"] for row in body["settings"] if not row["env_only"]]
    assert keys == list(settings_store.OVERRIDABLE_SPECS)
    assert len(keys) == 29


# -- G10 ----------------------------------------------------------------------


def test_core_file_is_off_by_default(client: TestClient, tmp_path: Path) -> None:
    client.patch("/v1/settings", json={"upstream_rate": "800k"}, headers=AUTH)
    assert not list(tmp_path.glob("**/core-settings.env"))


def test_core_file_follows_a_patch(tmp_path: Path) -> None:
    path = tmp_path / "settings" / "core-settings.env"
    path.parent.mkdir()
    settings = _settings(tmp_path, core_settings_file=str(path))
    with TestClient(create_app(settings)) as client:
        boot = path.read_text(encoding="utf-8")
        client.patch(
            "/v1/settings",
            json={"upstream_rate": "800k", "schedule_window": "22:00-06:00"},
            headers=AUTH,
        )
        after = path.read_text(encoding="utf-8")
        client.patch("/v1/settings", json={"schedule_window": "off"}, headers=AUTH)
        off = path.read_text(encoding="utf-8")
    # Boot: the bare Settings has no window -> off.
    assert boot.splitlines()[1:] == ["GENERATION=1", "VAULT_UPSTREAM_RATE_WINDOW=off"]
    assert after.splitlines()[1:] == [
        "GENERATION=2",
        "VAULT_UPSTREAM_RATE=800k",
        "VAULT_UPSTREAM_RATE_WINDOW=22:00-06:00",
    ]
    assert "VAULT_UPSTREAM_RATE_WINDOW=off" in off and "GENERATION=3" in off


def test_core_values_skip_env_pinned_api_keys(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path, prefill_max_threads=12, env_pinned=frozenset({"VAULT_PREFILL_MAX_THREADS"})
    )
    _store(settings, prefill_max_threads="4")
    conn = get_connection(settings.db_path)
    try:
        values = settings_store.core_values(conn, settings)
    finally:
        conn.close()
    assert "VAULT_PREFILL_MAX_THREADS" not in values
    unpinned = dataclasses.replace(settings, env_pinned=frozenset())
    conn = get_connection(settings.db_path)
    try:
        assert settings_store.core_values(conn, unpinned)["VAULT_PREFILL_MAX_THREADS"] == "4"
    finally:
        conn.close()


# -- G11 ----------------------------------------------------------------------


def test_log_level_applies_immediately(client: TestClient) -> None:
    root = logging.getLogger()
    before = root.level
    try:
        # Not risky since the D16a sign-off: no confirm flag needed.
        response = client.patch("/v1/settings", json={"log_level": "error"}, headers=AUTH)
        assert response.status_code == 200
        assert root.level == logging.ERROR
    finally:
        root.setLevel(before)


def test_request_time_keys_resolve_through_effective_settings(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _store(settings, agent_report_keep="7", manifest_oracle_timeout="2.5")

    class _Req:
        class app:  # noqa: N801 - mimics request.app
            class state:  # noqa: N801
                pass

    _Req.app.state.settings = settings
    assert deps.get_agent_report_keep(_Req) == 7  # type: ignore[arg-type]
    assert deps.get_settings(_Req).manifest_oracle_timeout == 2.5  # type: ignore[arg-type]


def test_runner_reads_prefill_threads_per_job_from_settings(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    cache_root = tmp_path / "cache"
    executable = stub_prefill.make_stub(bindir, cache_root=str(cache_root), depots_by_app={440: [441]})
    settings = _queue_settings(
        tmp_path, steamprefill_path=executable, cache_root=str(cache_root), prefill_max_threads=5
    )
    _store(settings, prefill_max_threads="7")
    worker = PrefillWorker(settings)
    runner = PrefillRunner(settings, runner_id="d16b-runner")
    worker.start()
    runner_thread = threading.Thread(target=runner.run_forever, daemon=True)
    runner_thread.start()
    try:
        from vault_api import jobs

        conn = get_connection(settings.db_path)
        try:
            job, _created = jobs.enqueue_prefill(conn, 440)
        finally:
            conn.close()
        finished = _wait_for_job(settings.db_path, int(job["id"]))
    finally:
        worker.stop(timeout=5)
        runner.stop()
        runner_thread.join(timeout=5)
    assert finished["status"] == jobs.STATUS_DONE, finished
    assert stub_prefill.read_argv(bindir)[-2:] == ["--max-threads", "7"]


def test_subprocess_worker_reads_prefill_threads_per_job_from_settings(tmp_path: Path) -> None:
    from tests.test_worker import enqueue, make_settings, wait_for_job

    bindir = tmp_path / "bin"
    cache_root = tmp_path / "cache"
    executable = stub_prefill.make_stub(bindir, cache_root=str(cache_root), depots_by_app={440: [441]})
    settings = dataclasses.replace(
        make_settings(tmp_path, cache_root, executable), prefill_max_threads=5
    )
    assert settings.prefill_mode == config.PREFILL_MODE_SUBPROCESS
    with TestClient(create_app(settings)) as client:
        assert client.patch(
            "/v1/settings", params=CONFIRM, json={"prefill_max_threads": "6"}, headers=AUTH
        ).status_code == 200
        (job_id,) = enqueue(client, 440)
        assert wait_for_job(client, job_id)["status"] == "done"
    assert stub_prefill.read_argv(bindir)[-2:] == ["--max-threads", "6"]
