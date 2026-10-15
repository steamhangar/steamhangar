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


@pytest.fixture
def core_client(tmp_path: Path) -> TestClient:
    """A vault-api with the core settings file enabled (review B3: core keys
    are only editable then)."""
    settings = _settings(tmp_path, core_settings_file=str(tmp_path / "core-settings.env"))
    return TestClient(create_app(settings))


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


def test_startup_warns_once_per_shadowed_key_and_for_a_stored_webhook(
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
    # Review S4: the RATE_WINDOW deprecation warning is vault-core's (D16c);
    # compose does not forward the variable to vault-api.
    assert not any("VAULT_UPSTREAM_RATE_WINDOW" in m for m in warnings)
    assert not any("u:p" in m for m in warnings)


def test_startup_warns_when_the_window_is_the_code_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("VAULT_API_KEY", TEST_API_KEY)
    monkeypatch.setenv("VAULT_DB_PATH", str(tmp_path / "vault.db"))
    monkeypatch.setenv("VAULT_CACHE_ROOT", str(tmp_path / "cache"))
    monkeypatch.delenv("VAULT_SCHEDULE_WINDOW", raising=False)
    caplog.set_level(logging.WARNING, logger="vault_api.settings_store")
    create_app(Settings.from_env())
    assert sum("is the rc18 default" in r.getMessage() for r in caplog.records) == 1

    caplog.clear()
    monkeypatch.setenv("VAULT_SCHEDULE_WINDOW", "off")
    create_app(Settings.from_env())
    assert not any("rc18 default" in r.getMessage() for r in caplog.records)

    caplog.clear()
    monkeypatch.delenv("VAULT_SCHEDULE_WINDOW")
    _store(Settings.from_env(), schedule_window="22:00-06:00")
    create_app(Settings.from_env())
    assert not any("rc18 default" in r.getMessage() for r in caplog.records)


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
        # Review S3: an unencoded '/' or '#' in the password moves the
        # userinfo out of urlsplit's netloc; any '@' still masks it.
        ("https://s3cret/x@host.example/p", "https://***@host.example/***"),
        ("https://tok3n#x@host.example/p", "https://***@host.example/***"),
        ("https://u:p?q@host.example", "https://***@host.example"),
    ],
)
def test_mask_url(url: str, shown: str) -> None:
    masked = settings_store.mask_url(url)
    assert masked == shown
    for secret in ("s3cret", "tok3n", "u:p"):
        assert secret not in masked


# -- G6 -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "body", [{"resolver": "9.9.9.9"}, {"upstream_edge": "off"}, {"upstream_pool_hosts": "off"},
             {"resolver": None}, {"upstream_max_conns": "32"}, {"tls_max_conns": "300"},
             {"tls_client_max_conns": "32"}, {"prefill_max_threads": "4"}]
)
def test_risky_keys_need_the_confirm_flag(core_client: TestClient, body: dict) -> None:
    client = core_client
    refused = client.patch("/v1/settings", json={"vault_name": "x", **body}, headers=AUTH)
    assert refused.status_code == 428
    assert refused.json()["detail"]["risky_keys"] == sorted(body)
    # Nothing persisted, not even the non-risky key of the batch.
    assert _row(client.get("/v1/settings", headers=AUTH).json(), "vault_name")["source"] == "default"
    accepted = client.patch(
        "/v1/settings", params={"confirm_risky": "true"}, json=body, headers=AUTH
    )
    assert accepted.status_code == 200, accepted.text


def test_non_risky_keys_need_no_confirm(core_client: TestClient) -> None:
    client = core_client
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


def test_upstream_cap_must_cover_the_prefill_threads(core_client: TestClient) -> None:
    client = core_client
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


def test_tls_client_cap_must_not_exceed_the_total(core_client: TestClient) -> None:
    client = core_client
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
        tmp_path, prefill_max_threads=20, env_pinned=frozenset({"VAULT_PREFILL_MAX_THREADS"}),
        core_settings_file=str(tmp_path / "core-settings.env"),
    )
    with TestClient(create_app(settings)) as client:
        response = client.patch(
            "/v1/settings", params=CONFIRM, json={"upstream_max_conns": "16"}, headers=AUTH
        )
    assert response.status_code == 422


# -- B3: core keys while the core settings file is disabled ------------------


def test_core_keys_are_read_only_while_the_core_file_is_disabled(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _store(settings, upstream_rate="800k")
    with TestClient(create_app(settings)) as client:
        body = client.get("/v1/settings", headers=AUTH).json()
        refused = client.patch("/v1/settings", json={"upstream_rate": "2m"}, headers=AUTH)
        cleared = client.patch("/v1/settings", json={"upstream_rate": None}, headers=AUTH)
    rate = _row(body, "upstream_rate")
    assert (rate["editable"], rate["reason"], rate["source"], rate["effective"]) == (
        False, "core_file_disabled", "default", None,
    )
    assert rate["shadowed"] is True and rate["value"] == "800k"
    for key in ("upstream_max_conns", "tls_max_conns", "upstream_edge", "resolver",
                "event_log_rotate_bytes", "upstream_pool_hosts", "tls_client_max_conns"):
        assert _row(body, key)["reason"] == "core_file_disabled", key
    # api+core stays editable: vault-api and the runner apply it themselves.
    assert _row(body, "prefill_max_threads")["editable"] is True
    assert _row(body, "auto_gc")["reason"] is None
    assert _row(body, "db_path")["reason"] == "env_only"
    for response in (refused, cleared):
        assert response.status_code == 409
        assert "VAULT_CORE_SETTINGS_FILE" in response.json()["detail"]


def test_cross_rules_ignore_stored_core_values_while_the_file_is_disabled(tmp_path: Path) -> None:
    """A stored cap of 4 reaches nothing while the file is off, so it must
    not block raising the prefill threads; the hook default 16 counts."""
    settings = _settings(tmp_path)
    _store(settings, upstream_max_conns="4")
    with TestClient(create_app(settings)) as client:
        ok = client.patch(
            "/v1/settings", params=CONFIRM, json={"prefill_max_threads": "12"}, headers=AUTH
        )
        too_many = client.patch(
            "/v1/settings", params=CONFIRM, json={"prefill_max_threads": "17"}, headers=AUTH
        )
    assert ok.status_code == 200
    assert too_many.status_code == 422


# -- G8 -----------------------------------------------------------------------


def test_window_off_blank_means_off_and_null_resets(tmp_path: Path) -> None:
    """Review decision B2 (amends audit 3.6): blank in a PATCH means off (the
    rc17 frontends send "" to disable); null resets to the default."""
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
        client.patch("/v1/settings", json={"schedule_window": "22:00-06:00"}, headers=AUTH)
        blank = client.patch("/v1/settings", json={"schedule_window": ""}, headers=AUTH)
        assert blank.status_code == 200
        row = _row(blank.json(), "schedule_window")
        assert (row["source"], row["effective"], row["value"]) == ("db", "off", "off")
        conn = get_connection(settings.db_path)
        try:
            assert settings_store.get_override(conn, "schedule_window") == "off"
            assert settings_store.effective_settings(conn, settings).schedule_window is None
        finally:
            conn.close()
        spaces = client.patch("/v1/settings", json={"schedule_window": "  "}, headers=AUTH)
        assert _row(spaces.json(), "schedule_window")["effective"] == "off"
        reset = client.patch("/v1/settings", json={"schedule_window": None}, headers=AUTH)
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


def test_get_rows_carry_the_expert_fields(core_client: TestClient) -> None:
    body = core_client.get("/v1/settings", headers=AUTH).json()
    rate = _row(body, "upstream_rate")
    assert rate == {
        "key": "upstream_rate", "effective": None, "source": "default", "fallback": None,
        "applies": "core", "env_only": False, "env_var": "VAULT_UPSTREAM_RATE",
        "value": None, "editable": True, "risky": False, "expert": True,
        "grammar": rate["grammar"], "target": "core", "env_visible": False, "shadowed": False,
        "reason": None,
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
    client.patch("/v1/settings", json={"vault_name": "x"}, headers=AUTH)
    assert client.app.state.settings.core_settings_file == ""
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


# -- Review S1/S2: named witnesses for the per-job and boot wiring ------------


def _record_run_prefill(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    from vault_api import prefill

    calls: list[dict] = []
    original = prefill.run_prefill

    def recording(*args, **kwargs):
        calls.append(dict(kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(prefill, "run_prefill", recording)
    return calls


def test_worker_reads_timeout_and_manifest_keep_per_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_worker import enqueue, make_settings, wait_for_job
    from vault_api import manifest_ingest

    calls = _record_run_prefill(monkeypatch)
    keeps: list[int] = []
    original_ingest = manifest_ingest.ingest_after_prefill

    def recording_ingest(conn, *, appid, settings):
        keeps.append(settings.manifest_keep)
        return original_ingest(conn, appid=appid, settings=settings)

    monkeypatch.setattr(manifest_ingest, "ingest_after_prefill", recording_ingest)
    bindir = tmp_path / "bin"
    cache_root = tmp_path / "cache"
    executable = stub_prefill.make_stub(bindir, cache_root=str(cache_root), depots_by_app={440: [441]})
    settings = make_settings(tmp_path, cache_root, executable)
    with TestClient(create_app(settings)) as client:
        assert client.patch(
            "/v1/settings",
            json={"prefill_timeout_seconds": "321", "manifest_keep": "9"},
            headers=AUTH,
        ).status_code == 200
        (job_id,) = enqueue(client, 440)
        assert wait_for_job(client, job_id)["status"] == "done"
    assert calls and calls[-1]["timeout_seconds"] == 321
    assert keeps == [9]


def test_worker_reads_gc_grace_days_per_gc_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from vault_api import gc_execute

    settings = _settings(tmp_path)
    _store(settings, gc_grace_days="2")
    seen: list[int] = []
    monkeypatch.setattr(
        gc_execute, "run_gc_job",
        lambda conn, job, *, settings, size_cache, webhook_notifier: seen.append(settings.gc_grace_days),
    )
    worker = PrefillWorker(settings)
    conn = get_connection(settings.db_path)
    try:
        worker._execute(conn, {"id": 1, "type": "gc", "appid": None})
        settings_store.set_override(conn, "gc_grace_days", "0")
        worker._execute(conn, {"id": 2, "type": "gc", "appid": None})
    finally:
        conn.close()
    assert seen == [2, 0]


def test_runner_reads_timeout_and_log_level_per_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vault_api import jobs

    calls = _record_run_prefill(monkeypatch)
    levels: list[int] = []
    from vault_api import prefill_runner as runner_module

    original_apply = runner_module.apply_log_level

    def recording_apply(name: str) -> None:
        original_apply(name)
        levels.append(logging.getLogger().level)

    monkeypatch.setattr(runner_module, "apply_log_level", recording_apply)
    bindir = tmp_path / "bin"
    cache_root = tmp_path / "cache"
    executable = stub_prefill.make_stub(bindir, cache_root=str(cache_root), depots_by_app={440: [441]})
    settings = _queue_settings(tmp_path, steamprefill_path=executable, cache_root=str(cache_root))
    _store(settings, prefill_timeout_seconds="654", log_level="WARNING")
    root = logging.getLogger()
    before = root.level
    worker = PrefillWorker(settings)
    runner = PrefillRunner(settings, runner_id="d16b-runner-s1")
    worker.start()
    runner_thread = threading.Thread(target=runner.run_forever, daemon=True)
    runner_thread.start()
    try:
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
        root.setLevel(before)
    assert finished["status"] == jobs.STATUS_DONE, finished
    assert calls[-1]["timeout_seconds"] == 654
    assert levels == [logging.WARNING]


def test_boot_log_level_comes_from_settings(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _store(settings, log_level="ERROR")
    root = logging.getLogger()
    before = root.level
    try:
        root.setLevel(logging.INFO)
        app = create_app(settings)
        assert root.level == logging.ERROR
        assert app.state.boot_settings.log_level == "ERROR"
    finally:
        root.setLevel(before)


# -- N1: env pin wins for every api-side key -----------------------------------

_SAMPLES = {
    "vault_name": "x", "schedule_window": "22:00-06:00", "schedule_interval_minutes": "99",
    "schedule_client_stale_days": "9", "sweep_include_cached": "false", "auto_gc": "off",
    "gc_grace_days": "1", "manifest_keep": "7", "agent_report_keep": "9",
    "client_stats_keep": "9", "event_sweep_interval_minutes": "9",
    "miss_trigger_cooldown_minutes": "9", "miss_trigger_max_per_sweep": "9",
    "bypass_window_days": "9", "prefill_max_threads": "4", "prefill_timeout_seconds": "99",
    "manifest_oracle_timeout": "2.5", "webhook_events": "job.done",
    "webhook_timeout_seconds": "2.5", "steam_library_steamid": "76561197960287930",
    "log_level": "ERROR",
}
_API_KEYS = sorted(k for k, s in settings_store.OVERRIDABLE_SPECS.items() if s.target != "core")


def test_samples_cover_every_api_side_key() -> None:
    assert sorted(_SAMPLES) == _API_KEYS


@pytest.mark.parametrize("key", _API_KEYS)
def test_env_pin_wins_for_every_api_side_key(tmp_path: Path, key: str) -> None:
    spec = settings_store.OVERRIDABLE_SPECS[key]
    settings = _settings(tmp_path, env_pinned=frozenset({spec.env_var}))
    _store(settings, **{key: _SAMPLES[key]})
    conn = get_connection(settings.db_path)
    try:
        effective = settings_store.effective_settings(conn, settings)
        unpinned = settings_store.effective_settings(
            conn, dataclasses.replace(settings, env_pinned=frozenset())
        )
    finally:
        conn.close()
    assert getattr(effective, key) == getattr(settings, key)
    assert getattr(unpinned, key) != getattr(settings, key)
    with TestClient(create_app(settings)) as client:
        row = _row(client.get("/v1/settings", headers=AUTH).json(), key)
        response = client.patch(
            "/v1/settings", params=CONFIRM, json={key: _SAMPLES[key]}, headers=AUTH
        )
    logging.getLogger().setLevel(logging.WARNING)
    assert (row["source"], row["editable"], row["reason"], row["shadowed"]) == (
        "env", False, "env", True,
    )
    assert response.status_code == 409
