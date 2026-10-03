"""WP VER-2: ``GET /v1/about`` and the vault-runner presence record.

Guarantees, each pinned by name below:

G1  the route needs the API key; ``/v1/health`` stays a fixed, version-free
    body.
G2  the answer is six components in a fixed order, each with exactly the
    six documented fields and a status from the closed set.
G3  vault-api: the baked version/commit; ``invalid`` (not the fallback) for
    a bad baked value; the release line on a native run.
G4  vault-core: read from the hook's file on the shared volume; status is
    always ``unknown`` (no network path, ADR-0011); missing file -> "no
    version recorded yet"; a malformed or non-regular file -> ``invalid``;
    the hook's own ``invalid`` passes through.
G5  vault-runner / SteamPrefill: ``ok`` only while the freshest presence row
    is younger than 90 s, ``unreachable`` after; ``not_in_use`` in subprocess
    mode, where SteamPrefill comes from vault-api's own env; stored values
    are validated again on read.
G6  the runner writes its presence on start, then at most once per
    interval, from the idle loop AND from inside a running job; a database
    error never escapes; a day-old row is pruned.
G7  vault-proxy: reachability only. The probe asks for the literal
    ``steamhangar-about-probe.invalid`` and ``403`` is the only ``ok``.
G8  vault-dns: never probed, ``unknown``.
G9  60 s cache; parallel lookups with a deadline: a hung lookup degrades
    its own entry only and does not hold the route.
G10 nothing in the answer names a path, the proxy host, or a runner id.
G11 schema v16 migration adds ``runner_presence`` to an existing database.
G12 the api image bakes ``STEAMPREFILL_VERSION`` from the one global build
    arg; the core image ships the version hook and serves no version.
"""

from __future__ import annotations

import json
import os
import re
import socket
import sqlite3
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from vault_api import BASE_VERSION, about, runner_presence
from vault_api.about import AboutService
from vault_api.config import PREFILL_MODE_QUEUE, Settings
from vault_api.db import SCHEMA_VERSION, get_connection, init_db
from vault_api.jobs import to_utc_iso
from vault_api.main import create_app
from vault_api.prefill_runner import PrefillRunner

REPO = Path(__file__).resolve().parents[2]
SHA = "0123456789abcdef0123456789abcdef01234567"
NOW = datetime(2026, 10, 3, 12, 0, 0, tzinfo=timezone.utc)
HEADERS = {"X-Api-Key": TEST_API_KEY}
NAMES = ["vault-api", "vault-core", "vault-runner", "steamprefill", "vault-proxy", "vault-dns"]
STATUSES = {"ok", "unreachable", "not_in_use", "unknown"}


# --- helpers -------------------------------------------------------------------


def _queue(settings: Settings) -> Settings:
    return replace(settings, prefill_mode=PREFILL_MODE_QUEUE)


def _service(settings: Settings, environ=None, prober=None, **kw) -> AboutService:
    return AboutService(
        settings,
        environ={} if environ is None else environ,
        wallclock=lambda: NOW,
        proxy_prober=prober or (lambda addr, t: 403),
        **kw,
    )


def _by_name(components) -> dict:
    return {c.name: c for c in components}


def _core_file(settings: Settings) -> Path:
    path = Path(about.core_version_path(settings))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _write_core(settings: Settings, **override) -> Path:
    data = {
        "component": "vault-core",
        "version": "0.1.0-rc9",
        "commit": SHA,
        "recorded_at": "2026-10-03T08:00:00Z",
    }
    data.update(override)
    path = _core_file(settings)
    path.write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8")
    return path


def _presence(settings: Settings, *, runner_id="r1", age_s: float, version="0.1.0-rc9",
              commit=SHA, sp="3.7.1") -> None:
    init_db(settings.db_path)
    conn = get_connection(settings.db_path)
    try:
        runner_presence.record_presence(
            conn,
            runner_id=runner_id,
            started_at="2026-10-03T07:00:00Z",
            build_version=version,
            build_commit=commit,
            steamprefill_version=sp,
            now=NOW - timedelta(seconds=age_s),
        )
    finally:
        conn.close()


# --- G1: auth, /v1/health -----------------------------------------------------


def test_about_requires_the_api_key(client: TestClient) -> None:
    assert client.get("/v1/about").status_code == 401
    assert client.get("/v1/about", headers={"X-Api-Key": "wrong"}).status_code == 401
    assert client.get("/v1/about", headers=HEADERS).status_code == 200


def test_about_router_carries_the_auth_dependency() -> None:
    # Layered pin (docs/LEARNINGS.md "Redundant defence layers"): the ASGI
    # guard covers the route today; the router's own dependency must too.
    from vault_api.auth import require_api_key
    from vault_api.routers import about as about_router

    deps = [d.dependency for d in about_router.router.dependencies]
    assert require_api_key in deps


def test_health_stays_fixed_and_version_free(client: TestClient) -> None:
    response = client.get("/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# --- G2: shape -----------------------------------------------------------------


def test_about_lists_six_components_in_fixed_order_with_exact_fields(client: TestClient) -> None:
    body = client.get("/v1/about", headers=HEADERS).json()
    assert list(body) == ["components"]
    assert [c["name"] for c in body["components"]] == NAMES
    for c in body["components"]:
        assert set(c) == {"name", "version", "commit", "status", "checked_at", "detail"}
        assert c["status"] in STATUSES
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", c["checked_at"])


def test_response_model_refuses_an_unknown_status_or_name() -> None:
    from pydantic import ValidationError

    from vault_api.routers.about import ComponentOut

    base = dict(name="vault-api", version="1", commit=None, status="ok",
                checked_at="2026-10-03T12:00:00Z", detail=None)
    ComponentOut(**base)
    with pytest.raises(ValidationError):
        ComponentOut(**{**base, "status": "invalid"})
    with pytest.raises(ValidationError):
        ComponentOut(**{**base, "name": "vault-web"})
    with pytest.raises(ValidationError):
        ComponentOut(**{**base, "extra": 1})


# --- G3: vault-api -------------------------------------------------------------


def test_api_reports_the_baked_version_and_commit(settings: Settings) -> None:
    env = {"VAULT_BUILD_VERSION": "0.1.0-rc9", "VAULT_BUILD_COMMIT": SHA}
    api = _by_name(_service(settings, env).components())["vault-api"]
    assert (api.version, api.commit, api.status) == ("0.1.0-rc9", SHA, "ok")
    assert "web UI" in api.detail


def test_api_reports_invalid_not_the_fallback_for_a_bad_baked_value(settings: Settings) -> None:
    env = {"VAULT_BUILD_VERSION": "1.0 beta", "VAULT_BUILD_COMMIT": "XYZ"}
    api = _by_name(_service(settings, env).components())["vault-api"]
    assert (api.version, api.commit) == ("invalid", "invalid")
    assert BASE_VERSION in api.detail  # names what server_version shows instead


def test_api_on_a_native_run_reports_the_release_line_and_no_commit(settings: Settings) -> None:
    api = _by_name(_service(settings, {}).components())["vault-api"]
    assert (api.version, api.commit, api.status) == (BASE_VERSION, None, "ok")
    assert "native run" in api.detail


def test_dev_image_reports_dev_and_no_commit(settings: Settings) -> None:
    env = {"VAULT_BUILD_VERSION": "dev", "VAULT_BUILD_COMMIT": "unknown"}
    api = _by_name(_service(settings, env).components())["vault-api"]
    assert (api.version, api.commit) == ("dev", None)


# --- G4: vault-core ------------------------------------------------------------


def test_core_version_path_is_logs_beside_the_cache_root(settings: Settings) -> None:
    root = Path(settings.cache_root)
    assert Path(about.core_version_path(settings)) == root.parent / "logs" / "vault-core-version.json"
    image = replace(settings, cache_root="/vault/cache")
    assert about.core_version_path(image).replace("\\", "/") == "/vault/logs/vault-core-version.json"


def test_core_missing_file_is_unknown_with_no_version_recorded_yet(settings: Settings) -> None:
    core = _by_name(_service(settings).components())["vault-core"]
    assert (core.version, core.commit, core.status) == (None, None, "unknown")
    assert core.detail.startswith("No version recorded yet")
    assert "ADR-0011" in core.detail


def test_core_valid_file_reports_version_commit_and_recorded_at(settings: Settings) -> None:
    _write_core(settings)
    core = _by_name(_service(settings).components())["vault-core"]
    assert (core.version, core.commit) == ("0.1.0-rc9", SHA)
    # Fail-closed default direction (LEARNINGS, testing discipline): even a
    # perfect file never makes vault-core "ok" -- nothing proves it runs.
    assert core.status == "unknown"
    assert "2026-10-03T08:00:00Z" in core.detail
    assert "ADR-0011" in core.detail


def test_core_recorded_at_comes_from_the_file_not_its_mtime(settings: Settings) -> None:
    path = _write_core(settings, recorded_at="2026-01-02T03:04:05Z")
    os.utime(path, (1_900_000_000, 1_900_000_000))
    core = _by_name(_service(settings).components())["vault-core"]
    assert "2026-01-02T03:04:05Z" in core.detail


def test_core_hook_invalid_passes_through(settings: Settings) -> None:
    _write_core(settings, version="invalid", commit="invalid")
    core = _by_name(_service(settings).components())["vault-core"]
    assert (core.version, core.commit, core.status) == ("invalid", "invalid", "unknown")
    assert "not a valid version" in core.detail


def test_core_unknown_commit_is_null(settings: Settings) -> None:
    _write_core(settings, version="dev", commit="unknown")
    core = _by_name(_service(settings).components())["vault-core"]
    assert (core.version, core.commit) == ("dev", None)


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"not json",
        b"[]",
        b'{"component":"vault-core","version":"1.0","commit":"unknown"}',
        b'{"component":"vault-core","version":"1.0","commit":"unknown","recorded_at":"2026-10-03T08:00:00Z","x":1}',
        b'{"component":"vault-api","version":"1.0","commit":"unknown","recorded_at":"2026-10-03T08:00:00Z"}',
        b'{"component":"vault-core","version":"1.0 beta","commit":"unknown","recorded_at":"2026-10-03T08:00:00Z"}',
        b'{"component":"vault-core","version":1,"commit":"unknown","recorded_at":"2026-10-03T08:00:00Z"}',
        b'{"component":"vault-core","version":"1.0","commit":"ABCDEF1","recorded_at":"2026-10-03T08:00:00Z"}',
        b'{"component":"vault-core","version":"1.0","commit":"unknown","recorded_at":"2026-10-3T8:00:00Z"}',
        b'{"component":"vault-core","version":"1.0","commit":"unknown","recorded_at":"2026-13-03T08:00:00Z"}',
        b'{"component":"vault-core","version":"1.0","commit":"unknown","recorded_at":"2026-10-03 08:00:00"}',
        b"\xff\xfe",
        # Deep nesting under the 4 KiB cap. Measured on CPython 3.13: 4000
        # open brackets are a JSONDecodeError, RecursionError needs ~100000,
        # which the cap already refuses; the RecursionError catch in
        # parse_core_version_file is defence in depth (docs/LEARNINGS.md WP 3.9).
        b"[" * 4000,
        b'{"component":"vault-core","version":"1.0","commit":"unknown","recorded_at":"2026-10-03T08:00:00Z"}'
        + b" " * 5000,
    ],
    ids=lambda c: repr(c[:40]),
)
def test_core_malformed_file_is_invalid(settings: Settings, content: bytes) -> None:
    _core_file(settings).write_bytes(content)
    core = _by_name(_service(settings).components())["vault-core"]
    assert (core.version, core.commit, core.status) == ("invalid", None, "unknown")
    assert "malformed" in core.detail


@pytest.mark.skipif(not hasattr(os, "symlink") or os.name == "nt", reason="POSIX symlinks")
def test_core_symlinked_file_is_not_followed(settings: Settings, tmp_path) -> None:
    target = tmp_path / "elsewhere.json"
    target.write_text(
        '{"component":"vault-core","version":"9.9.9","commit":"unknown","recorded_at":"2026-10-03T08:00:00Z"}'
    )
    os.symlink(target, _core_file(settings))
    core = _by_name(_service(settings).components())["vault-core"]
    assert core.version == "invalid"
    assert "9.9.9" not in json.dumps(core.__dict__)


@pytest.mark.skipif(not hasattr(os, "symlink") or os.name == "nt", reason="POSIX symlinks")
def test_core_symlinked_parent_directory_is_not_followed(settings: Settings, tmp_path) -> None:
    """S1 (review): O_NOFOLLOW guards the last component only; a logs/
    directory swapped for a symlink must not be read through."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "vault-core-version.json").write_text(
        '{"component":"vault-core","version":"9.9.9","commit":"unknown","recorded_at":"2026-10-03T08:00:00Z"}'
    )
    logs = Path(about.core_version_path(settings)).parent
    logs.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(elsewhere, logs)
    core = _by_name(_service(settings).components())["vault-core"]
    assert (core.version, core.status) == ("invalid", "unknown")
    assert "real directory" in core.detail
    assert "9.9.9" not in json.dumps(core.__dict__)


def test_core_directory_on_the_name_is_invalid(settings: Settings) -> None:
    _core_file(settings).mkdir()
    core = _by_name(_service(settings).components())["vault-core"]
    assert (core.version, core.status) == ("invalid", "unknown")


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFOs")
def test_core_fifo_on_the_name_does_not_block(settings: Settings) -> None:
    os.mkfifo(_core_file(settings))
    started = time.monotonic()
    core = _by_name(_service(settings).components())["vault-core"]
    assert time.monotonic() - started < 2.0
    assert core.version == "invalid"


# --- G5: vault-runner / SteamPrefill -----------------------------------------


def test_subprocess_mode_reports_runner_not_in_use_and_local_steamprefill(settings: Settings, tmp_path) -> None:
    binary = tmp_path / "SteamPrefill"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    s = replace(settings, steamprefill_path=str(binary))
    # A presence row from an idling runner container must not make it "ok".
    _presence(s, age_s=1)
    c = _by_name(_service(s, {"STEAMPREFILL_VERSION": "3.7.1"}).components())
    assert (c["vault-runner"].status, c["vault-runner"].version) == ("not_in_use", None)
    assert (c["steamprefill"].status, c["steamprefill"].version) == ("ok", "3.7.1")


def test_subprocess_mode_without_a_binary_is_unknown(settings: Settings) -> None:
    c = _by_name(_service(settings, {"STEAMPREFILL_VERSION": "3.7.1"}).components())
    assert (c["steamprefill"].status, c["steamprefill"].version) == ("unknown", "3.7.1")


def test_subprocess_mode_invalid_steamprefill_env_is_invalid(settings: Settings, tmp_path) -> None:
    binary = tmp_path / "SteamPrefill"
    binary.write_text("")
    binary.chmod(0o755)
    s = replace(settings, steamprefill_path=str(binary))
    c = _by_name(_service(s, {"STEAMPREFILL_VERSION": "3.7 .1"}).components())
    assert c["steamprefill"].version == "invalid"


def test_queue_mode_without_presence_is_unreachable(settings: Settings) -> None:
    s = _queue(settings)
    init_db(s.db_path)
    c = _by_name(_service(s).components())
    assert (c["vault-runner"].status, c["vault-runner"].version) == ("unreachable", None)
    assert "No vault-runner has reported yet" in c["vault-runner"].detail
    assert (c["steamprefill"].status, c["steamprefill"].version) == ("unknown", None)


@pytest.mark.parametrize("age_s, expected", [(0, "ok"), (89, "ok"), (90, "unreachable"), (3600, "unreachable")])
def test_queue_mode_runner_freshness_threshold(settings: Settings, age_s: float, expected: str) -> None:
    s = _queue(settings)
    _presence(s, age_s=age_s)
    c = _by_name(_service(s).components())
    assert c["vault-runner"].status == expected
    # The last known versions stay visible either way.
    assert (c["vault-runner"].version, c["vault-runner"].commit) == ("0.1.0-rc9", SHA)
    assert c["steamprefill"].version == "3.7.1"
    assert c["steamprefill"].status == ("ok" if expected == "ok" else "unknown")


def test_freshness_threshold_constants() -> None:
    assert runner_presence.PRESENCE_INTERVAL_SECONDS == 30.0
    assert runner_presence.PRESENCE_STALE_SECONDS == 90.0
    assert runner_presence.PRESENCE_STALE_SECONDS >= 3 * runner_presence.PRESENCE_INTERVAL_SECONDS


def test_queue_mode_reports_the_freshest_runner_and_counts_fresh_ones(settings: Settings) -> None:
    s = _queue(settings)
    _presence(s, runner_id="old", age_s=300, version="0.0.9")
    _presence(s, runner_id="a", age_s=20, version="0.1.0-rc8")
    _presence(s, runner_id="b", age_s=5, version="0.1.0-rc9")
    runner = _by_name(_service(s).components())["vault-runner"]
    assert runner.version == "0.1.0-rc9"
    assert "More than one runner reported" in runner.detail
    assert "2" not in runner.detail.replace("0.1.0-rc9", "")


@pytest.mark.parametrize(
    "stored, shown",
    [("bad value", "invalid"), ("invalid", "invalid"), (None, None), ("x" * 65, "invalid")],
)
def test_stored_presence_values_are_validated_on_read(settings: Settings, stored, shown) -> None:
    s = _queue(settings)
    _presence(s, age_s=1, version=stored, commit=stored, sp=stored)
    c = _by_name(_service(s).components())
    assert c["vault-runner"].version == shown
    assert c["vault-runner"].commit == shown
    assert c["steamprefill"].version == shown


def test_unparseable_last_seen_never_counts_as_fresh(settings: Settings) -> None:
    s = _queue(settings)
    init_db(s.db_path)
    conn = get_connection(s.db_path)
    conn.execute(
        "INSERT INTO runner_presence VALUES ('x', '1.0', NULL, NULL, 'whenever', ?)",
        ("2026-10-3T12:00:00Z",),
    )
    conn.commit()
    conn.close()
    assert _by_name(_service(s).components())["vault-runner"].status == "unreachable"


def test_presence_table_unreadable_is_unknown(settings: Settings, tmp_path) -> None:
    s = replace(_queue(settings), db_path=str(tmp_path))  # a directory, not a db
    c = _by_name(_service(s).components())
    assert c["vault-runner"].status == "unknown"
    assert c["steamprefill"].status == "unknown"


# --- G6: the runner writes its presence -----------------------------------------


def _rows(db_path: str) -> list[sqlite3.Row]:
    conn = get_connection(db_path)
    try:
        return conn.execute("SELECT * FROM runner_presence").fetchall()
    finally:
        conn.close()


def test_runner_records_its_identity_once_per_interval(settings: Settings, monkeypatch) -> None:
    init_db(settings.db_path)
    monkeypatch.setenv("VAULT_BUILD_VERSION", "0.1.0-rc9")
    monkeypatch.setenv("VAULT_BUILD_COMMIT", SHA)
    monkeypatch.setenv("STEAMPREFILL_VERSION", "3.7.1")
    clock = [1000.0]
    monkeypatch.setattr("vault_api.prefill_runner.time.monotonic", lambda: clock[0])
    runner = PrefillRunner(settings, runner_id="runner-a", presence_interval_seconds=30.0)
    conn = get_connection(settings.db_path)
    try:
        runner.maybe_record_presence(conn)
        rows = _rows(settings.db_path)
        assert [(r["runner_id"], r["build_version"], r["build_commit"], r["steamprefill_version"])
                for r in rows] == [("runner-a", "0.1.0-rc9", SHA, "3.7.1")]
        conn.execute("DELETE FROM runner_presence")
        conn.commit()
        clock[0] += 29.0
        runner.maybe_record_presence(conn)
        assert _rows(settings.db_path) == [], "wrote again inside the interval"
        clock[0] += 1.0
        runner.maybe_record_presence(conn)
        assert len(_rows(settings.db_path)) == 1, "did not write once the interval elapsed"
    finally:
        conn.close()


def test_runner_presence_survives_a_database_error(settings: Settings, caplog) -> None:
    init_db(settings.db_path)
    runner = PrefillRunner(settings, runner_id="runner-b", presence_interval_seconds=0.0)
    conn = get_connection(settings.db_path)
    try:
        conn.execute("DROP TABLE runner_presence")
        conn.commit()
        runner.maybe_record_presence(conn)  # must not raise
    finally:
        conn.close()
    assert "could not record its presence" in caplog.text


def test_runner_records_presence_from_its_idle_loop(settings: Settings) -> None:
    init_db(settings.db_path)
    s = replace(settings, runner_poll_seconds=0.05)
    runner = PrefillRunner(s, runner_id="runner-loop")
    thread = threading.Thread(target=runner.run_forever, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not _rows(s.db_path):
            time.sleep(0.05)
        assert [r["runner_id"] for r in _rows(s.db_path)] == ["runner-loop"]
    finally:
        runner.stop()
        thread.join(timeout=10)


def test_runner_records_presence_while_it_runs_a_job(settings: Settings, monkeypatch) -> None:
    """A four-hour prefill must not make the runner look gone: the heartbeat
    callback inside the job refreshes presence too."""
    init_db(settings.db_path)
    seen: list[int] = []

    def fake_run_prefill(**kwargs):
        conn = get_connection(settings.db_path)
        conn.execute("DELETE FROM runner_presence")
        conn.commit()
        conn.close()
        kwargs["stop_request"]()
        seen.append(len(_rows(settings.db_path)))
        raise RuntimeError("stop here")

    monkeypatch.setattr("vault_api.prefill_runner.prefill.run_prefill", fake_run_prefill)
    runner = PrefillRunner(settings, runner_id="runner-job", presence_interval_seconds=0.0)
    conn = get_connection(settings.db_path)
    try:
        with pytest.raises(RuntimeError):
            runner._execute(conn, {"id": 1, "appid": 440, "run_use_force": 0})
    finally:
        conn.close()
    assert seen == [1]


def test_record_presence_prunes_day_old_rows_but_keeps_its_own(settings: Settings) -> None:
    init_db(settings.db_path)
    conn = get_connection(settings.db_path)
    try:
        for rid, age in [("ancient", 25 * 3600), ("recent", 3600), ("me", 30 * 3600)]:
            runner_presence.record_presence(
                conn, runner_id=rid, started_at="2026-10-01T00:00:00Z",
                build_version="1", build_commit=None, steamprefill_version=None,
                now=NOW - timedelta(seconds=age),
            )
        runner_presence.record_presence(
            conn, runner_id="me", started_at="2026-10-01T00:00:00Z",
            build_version="2", build_commit=None, steamprefill_version=None, now=NOW,
        )
    finally:
        conn.close()
    rows = {r["runner_id"]: r for r in _rows(settings.db_path)}
    assert set(rows) == {"recent", "me"}
    assert rows["me"]["build_version"] == "2"
    assert rows["me"]["last_seen"] == to_utc_iso(NOW)
    assert rows["me"]["started_at"] == "2026-10-01T00:00:00Z"


# --- G7: vault-proxy -----------------------------------------------------------


def test_proxy_not_configured_is_not_in_use(settings: Settings) -> None:
    proxy = _by_name(_service(settings, {}).components())["vault-proxy"]
    assert (proxy.status, proxy.version) == ("not_in_use", None)


@pytest.mark.parametrize("value", ["socks5://vault-proxy:1080", "http://", "http://vault-proxy:notaport", "vault-proxy:8888"])
def test_proxy_unusable_url_is_unknown(settings: Settings, value: str) -> None:
    proxy = _by_name(_service(settings, {"HTTP_PROXY": value}).components())["vault-proxy"]
    assert proxy.status == "unknown"


@pytest.mark.parametrize(
    "answer, status, fragment",
    [
        (403, "ok", "refuses a host that is not on the egress allowlist"),
        (200, "unknown", "Check the egress filter"),
        (502, "unknown", "Check the egress filter"),
        (None, "unknown", "not with HTTP"),
    ],
)
def test_proxy_status_from_the_probe_answer(settings: Settings, answer, status, fragment) -> None:
    seen = []

    def prober(addr, timeout):
        seen.append(addr)
        return answer

    proxy = _by_name(
        _service(settings, {"HTTP_PROXY": "http://vault-proxy:8888"}, prober=prober).components()
    )["vault-proxy"]
    assert seen == [("vault-proxy", 8888)]
    assert (proxy.status, proxy.version, proxy.commit) == (status, None, None)
    assert fragment in proxy.detail


def test_proxy_lowercase_env_wins_like_urllib(settings: Settings) -> None:
    seen = []
    _service(
        settings,
        {"HTTP_PROXY": "http://upper:1", "http_proxy": "http://lower:2"},
        prober=lambda a, t: seen.append(a) or 403,
    ).components()
    assert seen == [("lower", 2)]


def test_proxy_connection_error_is_unreachable(settings: Settings) -> None:
    def prober(addr, timeout):
        raise ConnectionRefusedError()

    proxy = _by_name(_service(settings, {"HTTP_PROXY": "http://p:1"}, prober=prober).components())["vault-proxy"]
    assert proxy.status == "unreachable"


class _OneShotServer:
    """A loopback TCP server that records the request and answers ``reply``
    (or nothing, for the timeout case)."""

    def __init__(self, reply: bytes | None) -> None:
        self.reply = reply
        self.request = b""
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.release = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        conn, _ = self.sock.accept()
        with conn:
            conn.settimeout(5)
            try:
                while b"\r\n\r\n" not in self.request:
                    chunk = conn.recv(1024)
                    if not chunk:
                        break
                    self.request += chunk
            except OSError:
                pass
            if self.reply is not None:
                conn.sendall(self.reply)
            else:
                self.release.wait(5)

    def close(self) -> None:
        self.release.set()
        self.thread.join(timeout=5)
        self.sock.close()


def test_probe_asks_for_the_literal_invalid_host_and_reads_403() -> None:
    server = _OneShotServer(b"HTTP/1.0 403 Filtered\r\nContent-Type: text/html\r\n\r\nno")
    try:
        code = about.probe_proxy(("127.0.0.1", server.port), 2.0)
    finally:
        server.close()
    assert code == 403
    # Security-constant pin against a LITERAL (docs/LEARNINGS.md 4a.6r): the
    # probe host must stay an RFC 6761 .invalid name the proxy refuses on the
    # name alone, never a resolvable one.
    first_line = server.request.split(b"\r\n", 1)[0]
    assert first_line == b"GET http://steamhangar-about-probe.invalid/ HTTP/1.0"
    assert b"\r\nHost: steamhangar-about-probe.invalid\r\n" in server.request


def test_probe_reads_a_non_http_answer_as_none() -> None:
    server = _OneShotServer(b"SSH-2.0-OpenSSH\r\n")
    try:
        assert about.probe_proxy(("127.0.0.1", server.port), 2.0) is None
    finally:
        server.close()


def test_probe_times_out_on_a_silent_peer() -> None:
    server = _OneShotServer(None)
    try:
        started = time.monotonic()
        with pytest.raises(OSError):
            about.probe_proxy(("127.0.0.1", server.port), 0.3)
        assert time.monotonic() - started < 2.0
    finally:
        server.close()


def test_proxy_component_end_to_end_with_a_real_socket(settings: Settings) -> None:
    server = _OneShotServer(b"HTTP/1.0 403 Filtered\r\n\r\n")
    try:
        svc = AboutService(settings, environ={"HTTP_PROXY": f"http://127.0.0.1:{server.port}"})
        proxy = _by_name(svc.components())["vault-proxy"]
    finally:
        server.close()
    assert proxy.status == "ok"


# --- G8: vault-dns -------------------------------------------------------------


def test_dns_is_never_probed_and_unknown(settings: Settings) -> None:
    dns = _by_name(_service(settings).components())["vault-dns"]
    assert (dns.status, dns.version, dns.commit) == ("unknown", None, None)
    assert "ADR-0011" in dns.detail and "own DNS rewrite" in dns.detail


# --- G9: cache, parallelism, degradation ---------------------------------------


def test_answer_is_cached_for_the_ttl(settings: Settings) -> None:
    calls = []
    clock = [0.0]
    svc = _service(
        settings,
        {"HTTP_PROXY": "http://p:1"},
        prober=lambda a, t: calls.append(1) or 403,
        monotonic=lambda: clock[0],
    )
    first = svc.components()
    clock[0] = 59.9
    assert svc.components() == first
    assert len(calls) == 1
    clock[0] = 60.0
    svc.components()
    assert len(calls) == 2


def test_default_cache_ttl_is_sixty_seconds() -> None:
    assert about.CACHE_TTL_SECONDS == 60.0
    assert about.PROBE_TIMEOUT_SECONDS <= about.PROBE_DEADLINE_SECONDS <= 5.0


def test_a_hung_probe_degrades_only_its_own_entry_and_does_not_hold_the_route(settings: Settings) -> None:
    release = threading.Event()

    def hung(addr, timeout):
        release.wait(5)
        return 403

    _write_core(settings)
    svc = _service(settings, {"HTTP_PROXY": "http://p:1"}, prober=hung, deadline_seconds=0.3)
    started = time.monotonic()
    try:
        c = _by_name(svc.components())
    finally:
        release.set()
    assert time.monotonic() - started < 2.0
    assert c["vault-proxy"].status == "unreachable"
    assert "No answer within" in c["vault-proxy"].detail
    assert c["vault-core"].version == "0.1.0-rc9"
    assert c["vault-api"].status == "ok"


def test_a_crashing_probe_degrades_only_its_own_entry(settings: Settings) -> None:
    def boom(addr, timeout):
        raise RuntimeError("bug")

    c = _by_name(_service(settings, {"HTTP_PROXY": "http://p:1"}, prober=boom).components())
    assert (c["vault-proxy"].status, c["vault-proxy"].detail) == ("unknown", "The lookup failed.")
    assert [x.name for x in _service(settings).components()] == NAMES


def test_probes_run_in_parallel(settings: Settings, monkeypatch) -> None:
    gate = threading.Barrier(2, timeout=2)

    def core_waits(path, checked_at):
        gate.wait()
        return about.Component("vault-core", None, None, "unknown", checked_at, "x")

    def proxy_waits(addr, timeout):
        gate.wait()
        return 403

    monkeypatch.setattr(about, "core_component", core_waits)
    c = _by_name(_service(settings, {"HTTP_PROXY": "http://p:1"}, prober=proxy_waits).components())
    # With serial lookups the barrier would time out and both entries degrade.
    assert c["vault-proxy"].status == "ok"
    assert c["vault-core"].detail == "x"


# --- G10: no leaks ---------------------------------------------------------------


def test_answer_names_no_path_host_or_runner_id(settings: Settings, tmp_path) -> None:
    s = _queue(replace(settings, steamprefill_path=str(tmp_path / "nope")))
    _write_core(s)
    _presence(s, runner_id="secret-host:4242:abcd1234", age_s=1)
    app = create_app(s)
    app.state.about = AboutService(
        s, environ={"HTTP_PROXY": "http://vault-proxy-internal:8888"},
        proxy_prober=lambda a, t: 403,
    )
    body = TestClient(app).get("/v1/about", headers=HEADERS).text
    for needle in (str(tmp_path), s.db_path, s.cache_root, "vault-proxy-internal", "8888",
                   "secret-host", "4242", "abcd1234", TEST_API_KEY):
        assert needle not in body, needle


# --- G11: migration ------------------------------------------------------------


def test_schema_v16_migration_adds_runner_presence(tmp_path) -> None:
    db = str(tmp_path / "vault.db")
    init_db(db)
    conn = get_connection(db)
    conn.execute("DROP TABLE runner_presence")
    conn.execute("UPDATE schema_version SET version = 15")
    conn.execute("INSERT INTO apps (appid, name, status) VALUES (440, 'TF2', 'cached')")
    conn.commit()
    conn.close()

    init_db(db)

    conn = get_connection(db)
    try:
        # v16 introduced the table; later versions (v17, WP AGENT-FEAT-1)
        # must still carry a v15 database through it.
        assert SCHEMA_VERSION >= 16
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(runner_presence)")]
        assert cols == ["runner_id", "build_version", "build_commit",
                        "steamprefill_version", "started_at", "last_seen"]
        assert conn.execute("SELECT name FROM apps WHERE appid = 440").fetchone()[0] == "TF2"
    finally:
        conn.close()


# --- G12: images ---------------------------------------------------------------


def test_api_dockerfile_bakes_steamprefill_version_from_the_one_global_arg() -> None:
    text = (REPO / "api" / "Dockerfile").read_text(encoding="utf-8")
    lines = text.splitlines()
    froms = [i for i, line in enumerate(lines) if line.startswith("FROM ")]
    assert len(froms) == 2
    global_args = [line for line in lines[: froms[0]] if line.startswith("ARG ")]
    assert global_args == ["ARG STEAMPREFILL_VERSION=3.7.1"]
    stage1 = lines[froms[0]: froms[1]]
    assert "ARG STEAMPREFILL_VERSION" in stage1
    final = lines[froms[1]:]
    assert "ARG STEAMPREFILL_VERSION" in final
    assert 'ENV STEAMPREFILL_VERSION="${STEAMPREFILL_VERSION}"' in final
    assert not any(re.match(r"ARG STEAMPREFILL_VERSION=", line) for line in lines[froms[0]:]), (
        "a second default would let the baked version drift from the downloaded one"
    )


def test_core_image_ships_the_version_hook_and_serves_no_version() -> None:
    dockerfile = (REPO / "core" / "Dockerfile").read_text(encoding="utf-8")
    assert (
        "COPY docker/29-vault-build-version.sh /docker-entrypoint.d/29-vault-build-version.sh"
        in dockerfile
    )
    assert "/docker-entrypoint.d/29-vault-build-version.sh /docker-entrypoint.d/40-vault-preflight.sh" in dockerfile
    hook = (REPO / "core" / "docker" / "29-vault-build-version.sh").read_text(encoding="utf-8")
    assert 'OUT="${1:-/vault/logs/vault-core-version.json}"' in hook
    for conf in (REPO / "core" / "nginx" / "nginx.conf", REPO / "core" / "docker" / "nginx.conf.template"):
        text = conf.read_text(encoding="utf-8")
        assert "vault-version" not in text and "vault-core-version" not in text, conf
        assert re.search(r"^\s*root cache;", text, re.MULTILINE), conf


def test_hook_and_api_agree_on_the_file_location() -> None:
    hook = (REPO / "core" / "docker" / "29-vault-build-version.sh").read_text(encoding="utf-8")
    assert about.CORE_VERSION_FILE.replace("\\", "/") == "logs/vault-core-version.json"
    assert "/vault/" + about.CORE_VERSION_FILE.replace("\\", "/") in hook
