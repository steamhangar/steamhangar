"""D16c: vault-core's apply status file, read by vault-api.

vault-core's ``47-vault-settings-watch.sh`` writes
``<cache volume>/logs/settings-applied.json`` (``core/docker/
vault-settings-lib.sh`` ``vs_write_status``); GET /v1/settings reads it.

Guarantees, one named test (or group) each:

S1  the shell writer's output parses with the Python reader (cross-language
    pin: the real library runs under ``sh``), IPv6 brackets kept, a hostile
    reason cannot break the JSON.
S2  the parser is strict: any shape vault-core does not write is ignored as a
    whole (``None``), never half trusted.
S3  the reader refuses a symlink, a FIFO (without blocking), a symlinked
    ``logs/`` and an oversized file; a missing file is ``None``.
S4  GET: a core key vault-core's env pins reads ``source: "env"`` with the
    pinned value, ``editable: false`` and ``shadowed`` when Settings also
    holds a value; every core key is ``env_visible`` once the file exists.
S5  GET without the file: unchanged from D16b (``env_visible: false``,
    ``core_apply: null``).
S6  ``core_apply.pending`` is true exactly while vault-api's own settings file
    has a newer generation than vault-core last looked at.
S7  the shell defaults (``vs_default``) equal vault-api's constants.
S9  PATCH refuses (409) a core key vault-core's env pins, like GET shows it;
    the cross rules use core's pinned values; B3 still locks core rows while
    the file is disabled.
S8  compose: the vault-settings volume, rw for vault-api, ro + nocopy for
    vault-core, nobody else; VAULT_CORE_SETTINGS_FILE set; the api image owns
    the mount point 101:101.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from vault_api import core_settings, settings_store
from vault_api.config import Settings
from vault_api.db import get_connection, init_db
from vault_api.main import create_app

AUTH = {"X-Api-Key": TEST_API_KEY}
REPO_ROOT = Path(__file__).resolve().parents[2]
LIB = REPO_ROOT / "core" / "docker" / "vault-settings-lib.sh"


def _status(**overrides) -> dict:
    data = {
        "component": "vault-core",
        "state": "applied",
        "generation": 4,
        "applied_generation": 4,
        "reason": "",
        "updated_at": "2026-10-15T08:00:00Z",
        "keys": {
            "VAULT_UPSTREAM_RATE": {"value": "4m", "source": "env"},
            "VAULT_UPSTREAM_EDGE": {"value": "off", "source": "file"},
            "VAULT_RESOLVER": {"value": "1.1.1.1", "source": "default"},
        },
    }
    data.update(overrides)
    return data


def _write_status(cache_root: Path, data: dict | bytes) -> Path:
    logs = cache_root.parent / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / "settings-applied.json"
    path.write_bytes(data if isinstance(data, bytes) else json.dumps(data).encode())
    return path


# -- S1 -----------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX sh")
def test_shell_writer_output_parses(tmp_path: Path) -> None:
    effective = tmp_path / "effective"
    effective.write_text(
        "VAULT_UPSTREAM_RATE=800k\tfile\n"
        "VAULT_RESOLVER=[2606:4700::1111]:53\tenv\n"
        "VAULT_UPSTREAM_EDGE=off\tdefault\n",
        encoding="ascii",
    )
    status_file = tmp_path / "settings-applied.json"
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "VS_STATUS_FILE": str(status_file)}
    subprocess.run(
        ["sh", "-c", '. "$1"; vs_write_status rejected 9 8 "$2" "$3"', "sh", str(LIB),
         'bad "quote" \\ and\nnewline', str(effective)],
        check=True, env=env, timeout=30,
    )
    status = core_settings.read_core_status(str(status_file))
    assert status is not None
    assert (status.state, status.generation, status.applied_generation) == ("rejected", 9, 8)
    assert status.keys["VAULT_RESOLVER"] == ("[2606:4700::1111]:53", "env")
    assert status.keys["VAULT_UPSTREAM_RATE"] == ("800k", "file")
    assert status.env_pinned("VAULT_RESOLVER") == "[2606:4700::1111]:53"
    assert status.env_pinned("VAULT_UPSTREAM_RATE") is None
    assert '"' not in status.reason and "\n" not in status.reason


# -- S2 -----------------------------------------------------------------------


def test_parser_accepts_the_written_shape() -> None:
    status = core_settings.parse_core_status(json.dumps(_status()).encode())
    assert status is not None and status.env_pinned("VAULT_UPSTREAM_RATE") == "4m"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(component="vault-api"),
        lambda d: d.update(state="ok"),
        lambda d: d.update(generation=True),
        lambda d: d.update(generation=-1),
        lambda d: d.update(applied_generation="4"),
        lambda d: d.update(reason=None),
        lambda d: d.update(extra=1),
        lambda d: d.pop("updated_at"),
        lambda d: d["keys"].update(VAULT_API_KEY={"value": "x", "source": "env"}),
        lambda d: d["keys"].update(VAULT_UPSTREAM_RATE={"value": "4m", "source": "db"}),
        lambda d: d["keys"].update(VAULT_UPSTREAM_RATE={"value": 4, "source": "env"}),
        lambda d: d["keys"].update(VAULT_UPSTREAM_RATE={"value": "4m"}),
        lambda d: d.update(keys=[]),
    ],
    ids=[
        "component", "state", "bool-generation", "negative-generation", "string-generation",
        "reason-null", "extra-key", "missing-key", "unknown-setting", "bad-source",
        "non-string-value", "missing-source", "keys-list",
    ],
)
def test_parser_refuses_other_shapes(mutate) -> None:
    data = _status()
    mutate(data)
    assert core_settings.parse_core_status(json.dumps(data).encode()) is None


def test_parser_refuses_garbage_and_oversize() -> None:
    assert core_settings.parse_core_status(b"not json") is None
    assert core_settings.parse_core_status(b"\xff\xfe") is None
    assert core_settings.parse_core_status(b"[" * 100000) is None
    big = _status(reason="x" * (core_settings.STATUS_MAX_BYTES + 1))
    assert core_settings.parse_core_status(json.dumps(big).encode()) is None


# -- S3 -----------------------------------------------------------------------


def test_reader_missing_symlink_fifo(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    target = tmp_path / "real.json"
    target.write_text(json.dumps(_status()))
    path = logs / "settings-applied.json"
    assert core_settings.read_core_status(str(path)) is None
    path.symlink_to(target)
    assert core_settings.read_core_status(str(path)) is None
    path.unlink()
    os.mkfifo(path)
    assert core_settings.read_core_status(str(path)) is None
    path.unlink()
    path.write_text(json.dumps(_status()))
    assert core_settings.read_core_status(str(path)) is not None


def test_reader_refuses_a_symlinked_logs_dir(tmp_path: Path) -> None:
    real = tmp_path / "elsewhere"
    real.mkdir()
    (real / "settings-applied.json").write_text(json.dumps(_status()))
    (tmp_path / "logs").symlink_to(real)
    assert core_settings.read_core_status(str(tmp_path / "logs" / "settings-applied.json")) is None


def test_status_path_is_next_to_the_cache() -> None:
    assert core_settings.core_status_path("/vault/cache") == "/vault/logs/settings-applied.json"


# -- S4 / S5 / S6 -------------------------------------------------------------


def _settings(tmp_path: Path, **kwargs) -> Settings:
    return Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
        **kwargs,
    )


def _row(body: dict, key: str) -> dict:
    for row in body["settings"]:
        if row["key"] == key:
            return row
    raise AssertionError(f"{key!r} missing")


def _get(settings: Settings) -> dict:
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get("/v1/settings", headers=AUTH)
    assert response.status_code == 200, response.text
    return response.json()


def _file_settings(tmp_path: Path) -> Settings:
    """The core settings file enabled, as compose ships it (B3 lock lifted)."""
    (tmp_path / "settings").mkdir(exist_ok=True)
    return _settings(tmp_path, core_settings_file=str(tmp_path / "settings" / "core-settings.env"))


def test_core_env_pin_is_reported(tmp_path: Path) -> None:
    settings = _file_settings(tmp_path)
    init_db(settings.db_path)
    conn = get_connection(settings.db_path)
    try:
        settings_store.set_override(conn, "upstream_rate", "800k")
    finally:
        conn.close()
    _write_status(tmp_path / "cache", _status())
    body = _get(settings)
    rate = _row(body, "upstream_rate")
    assert rate["source"] == "env" and rate["effective"] == "4m"
    assert rate["editable"] is False and rate["shadowed"] is True and rate["env_visible"] is True
    assert rate["reason"] == "env"
    edge = _row(body, "upstream_edge")
    assert edge["source"] == "default" and edge["editable"] is True and edge["env_visible"] is True
    assert edge["reason"] is None
    assert body["core_apply"]["state"] == "applied"
    assert body["core_apply"]["applied_generation"] == 4


def test_without_status_file_nothing_changes(tmp_path: Path) -> None:
    body = _get(_file_settings(tmp_path))
    rate = _row(body, "upstream_rate")
    assert rate["env_visible"] is False and rate["source"] == "default" and rate["editable"] is True
    assert rate["reason"] is None
    assert body["core_apply"] is None


def test_b3_lock_still_applies_without_the_file(tmp_path: Path) -> None:
    """The status file does not lift B3: with VAULT_CORE_SETTINGS_FILE blank
    a core row stays core_file_disabled (unless core's env pins it)."""
    _write_status(tmp_path / "cache", _status())
    body = _get(_settings(tmp_path))
    assert _row(body, "upstream_edge")["reason"] == "core_file_disabled"
    assert _row(body, "upstream_rate")["reason"] == "env"


# -- S9: PATCH agrees with GET on vault-core's env pins ------------------------


def _patch(settings: Settings, body: dict, confirm: bool = False):
    app = create_app(settings)
    with TestClient(app) as client:
        return client.patch(
            "/v1/settings" + ("?confirm_risky=true" if confirm else ""), headers=AUTH, json=body
        )


def test_patch_refuses_a_key_core_env_pins(tmp_path: Path) -> None:
    settings = _file_settings(tmp_path)
    _write_status(tmp_path / "cache", _status())
    response = _patch(settings, {"upstream_rate": "800k"})
    assert response.status_code == 409, response.text
    assert "VAULT_UPSTREAM_RATE" in response.json()["detail"]
    assert "vault-core" in response.json()["detail"]
    # Clearing it is refused too (same as an api-side env pin).
    assert _patch(settings, {"upstream_rate": None}).status_code == 409
    # A key core does not pin stays editable.
    ok = _patch(settings, {"upstream_edge": "off"}, confirm=True)
    assert ok.status_code == 200, ok.text
    assert _row(ok.json(), "upstream_edge")["source"] == "db"


def test_patch_without_status_file_is_not_guarded(tmp_path: Path) -> None:
    settings = _file_settings(tmp_path)
    assert _patch(settings, {"upstream_rate": "800k"}).status_code == 200


def test_cross_rules_use_core_pinned_values(tmp_path: Path) -> None:
    settings = _file_settings(tmp_path)
    keys = dict(_status()["keys"])
    keys["VAULT_UPSTREAM_MAX_CONNS"] = {"value": "8", "source": "env"}
    keys["VAULT_TLS_MAX_CONNS"] = {"value": "100", "source": "env"}
    _write_status(tmp_path / "cache", _status(keys=keys))
    # Core's cap 8 (env) stays below 12 threads, whatever Settings could say.
    response = _patch(settings, {"prefill_max_threads": "12"}, confirm=True)
    assert response.status_code == 422, response.text
    assert "upstream_max_conns (8)" in response.json()["detail"]
    # Core's total 100 (env) is below a per-client cap of 128.
    response = _patch(settings, {"tls_client_max_conns": "128"}, confirm=True)
    assert response.status_code == 422, response.text
    assert "tls_max_conns (100)" in response.json()["detail"]
    assert _patch(settings, {"tls_client_max_conns": "64"}, confirm=True).status_code == 200


def test_pending_follows_the_written_generation(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings" / "core-settings.env"
    settings_file.parent.mkdir()
    settings = _settings(tmp_path, core_settings_file=str(settings_file))
    core_settings.write_core_settings_file(str(settings_file), {"VAULT_UPSTREAM_RATE": "1m"})
    core_settings.write_core_settings_file(str(settings_file), {"VAULT_UPSTREAM_RATE": "2m"})
    _write_status(tmp_path / "cache", _status(generation=1, applied_generation=1))
    body = _get(settings)
    assert body["core_apply"]["written_generation"] >= 2
    assert body["core_apply"]["pending"] is True
    written = body["core_apply"]["written_generation"]
    _write_status(tmp_path / "cache", _status(state="rejected", generation=written, applied_generation=1))
    body = _get(settings)
    assert body["core_apply"]["pending"] is False and body["core_apply"]["state"] == "rejected"


# -- S7: the shell defaults equal vault-api's (twin pin) -----------------------


def _lib_defaults() -> dict[str, str]:
    text = LIB.read_text(encoding="utf-8")
    body = text[text.index("vs_default() {"):]
    body = body[: body.index("\n}\n")]
    return dict(re.findall(r"^\s*(VAULT_[A-Z_]+)\) echo (\S+) ;;$", body, re.MULTILINE))


def test_shell_defaults_equal_the_api_defaults() -> None:
    from vault_api import config

    assert _lib_defaults() == {
        "VAULT_UPSTREAM_RATE": core_settings.OFF,
        "VAULT_UPSTREAM_RATE_WINDOW": config.DEFAULT_SCHEDULE_WINDOW,
        "VAULT_UPSTREAM_EDGE": core_settings.UPSTREAM_EDGE_DEFAULT,
        "VAULT_UPSTREAM_POOL_HOSTS": core_settings.OFF,
        "VAULT_UPSTREAM_MAX_CONNS": str(core_settings.UPSTREAM_CAP_DEFAULT),
        "VAULT_PREFILL_MAX_THREADS": str(config.DEFAULT_PREFILL_MAX_THREADS),
        "VAULT_TLS_CLIENT_MAX_CONNS": str(core_settings.TLS_CLIENT_DEFAULT),
        "VAULT_TLS_MAX_CONNS": str(core_settings.TLS_TOTAL_DEFAULT),
        "VAULT_RESOLVER": core_settings.RESOLVER_DEFAULT,
        "VAULT_EVENT_LOG_ROTATE_BYTES": str(core_settings.ROTATE_DEFAULT_BYTES),
    }
    # String literals too (LEARNINGS: pins assert literals, not only constants).
    assert _lib_defaults()["VAULT_UPSTREAM_EDGE"] == "dist-fra1.discovery.steamserver.net"
    assert _lib_defaults()["VAULT_RESOLVER"] == "1.1.1.1"
    assert set(_lib_defaults()) == set(core_settings.FILE_KEYS)


# -- S8: the shared volume in compose (Q6, audit 4.4/4.5) ------------------------


def _compose() -> dict:
    return yaml.safe_load((REPO_ROOT / "deploy" / "compose.yaml").read_text(encoding="utf-8"))


def test_compose_mounts_the_settings_volume() -> None:
    compose = _compose()
    assert "vault-settings" in compose["volumes"]
    core_mounts = [m for m in compose["services"]["vault-core"]["volumes"] if isinstance(m, dict)]
    assert core_mounts == [{
        "type": "volume", "source": "vault-settings", "target": "/etc/vault-settings",
        "read_only": True, "volume": {"nocopy": True},
    }]
    api = compose["services"]["vault-api"]
    assert "vault-settings:/var/lib/vault-settings" in api["volumes"]
    assert api["environment"]["VAULT_CORE_SETTINGS_FILE"] == "/var/lib/vault-settings/core-settings.env"
    for name in ("vault-runner", "vault-proxy", "vault-dns"):
        service = compose["services"].get(name, {})
        mounts = " ".join(str(m) for m in service.get("volumes", []))
        assert "vault-settings" not in mounts, f"{name} must not mount vault-settings (audit 4.4)"


def test_api_image_owns_the_mount_point() -> None:
    text = (REPO_ROOT / "api" / "Dockerfile").read_text(encoding="utf-8")
    assert '"$(stat -c \'%u:%g %a\' /var/lib/vault-settings)" = "101:101 755"' in text


# -- S10: review witnesses (D16c review S3/S4) ---------------------------------


def test_pending_is_not_equal_after_a_lost_volume(tmp_path: Path) -> None:
    """vault-api restarts at GENERATION 1 when the settings volume is lost
    while vault-core last saw 5: still pending (not "greater than")."""
    settings = _file_settings(tmp_path)
    core_settings.write_core_settings_file(settings.core_settings_file, {"VAULT_UPSTREAM_RATE": "1m"})
    assert core_settings.written_generation(settings.core_settings_file) == 1
    _write_status(tmp_path / "cache", _status(generation=5, applied_generation=5))
    apply = _get(settings)["core_apply"]
    # vault-api's boot rewrites the file once more (generation 2): still a
    # LOWER generation than the 5 vault-core last saw.
    assert apply["written_generation"] < 5 and apply["generation"] == 5
    assert apply["pending"] is True


def test_core_pinned_prefill_threads_feed_the_cap_floor(tmp_path: Path) -> None:
    """vault-core's floor check uses ITS prefill thread count: pinned to 20
    in core's env, a Settings cap of 16 is refused although vault-api's own
    thread count (8) would allow it."""
    settings = _file_settings(tmp_path)
    keys = dict(_status()["keys"])
    keys["VAULT_PREFILL_MAX_THREADS"] = {"value": "20", "source": "env"}
    _write_status(tmp_path / "cache", _status(keys=keys))
    response = _patch(settings, {"upstream_max_conns": "16"}, confirm=True)
    assert response.status_code == 422, response.text
    assert "prefill_max_threads (20)" in response.json()["detail"]
    assert _patch(settings, {"upstream_max_conns": "24"}, confirm=True).status_code == 200


@pytest.mark.parametrize(
    "key", ("VAULT_RESOLVER", "VAULT_UPSTREAM_MAX_CONNS", "VAULT_TLS_CLIENT_MAX_CONNS", "VAULT_TLS_MAX_CONNS")
)
def test_core_image_env_does_not_pin_editable_keys(key: str) -> None:
    """Review S4: the core image states these EMPTY (= not set); a non-blank
    image ENV would show as an env pin in every plain image run."""
    text = (REPO_ROOT / "core" / "Dockerfile").read_text(encoding="utf-8")
    assert re.findall(rf"^\s*{key}=(\S*?)\s*\\?\s*$", text, re.MULTILINE) == [""]
