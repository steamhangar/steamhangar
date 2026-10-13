"""WP UPD-1 (user decision 2026-10-13, Weg A as refined in the ADR-0011
review): the manifest oracle is ON by default, ``off``/``none`` opts out, and
while it is on vault-proxy allows the DEFAULT oracle host by itself — only
that host, and only when ``VAULT_MANIFEST_ORACLE_URL`` still points at it.

Two sides implement the rule: ``vault_api/config.py`` (selector grammar and the
refuse-to-boot check) and ``deploy/proxy/docker-entrypoint.sh`` (what lands in
the filter file). ``test_config_and_proxy_agree_on_every_case`` runs the REAL
entrypoint (the ``VAULT_PROXY_*_FOR_TESTS`` overrides, no Docker) next to
``Settings.from_env`` for every case, so the two cannot drift.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tests.test_eg1_egress_lock import (
    PROXY_ENTRYPOINT_PATH,
    PROXY_TINYPROXY_CONF_PATH,
    _EXPECTED_VAULT_EGRESS_SUBNET,
    _SH,
    requires_sh,
)
from vault_api import config
from vault_api.config import Settings

DEFAULT_HOST_PATTERN = r"^api\.steamcmd\.net$"


def from_env(
    monkeypatch: pytest.MonkeyPatch,
    oracle: str | None,
    url: str | None = None,
    egress_allow: str | None = None,
) -> Settings:
    monkeypatch.setenv("VAULT_API_KEY", "k")
    for name, value in (
        ("VAULT_MANIFEST_ORACLE", oracle),
        ("VAULT_MANIFEST_ORACLE_URL", url),
        ("VAULT_EGRESS_ALLOW", egress_allow),
    ):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    return Settings.from_env()


def run_proxy_entrypoint(
    tmp_path: Path, oracle: str | None, url: str | None, egress_allow: str = ""
) -> tuple[int, str, str]:
    """Run the real entrypoint; returns (exit code, output, filter file)."""
    filter_file = tmp_path / "filter"
    env = {
        "VAULT_EGRESS_ALLOW": egress_allow,
        "VAULT_EGRESS_SUBNET": _EXPECTED_VAULT_EGRESS_SUBNET,
        "VAULT_PROXY_FILTER_FILE_FOR_TESTS": str(filter_file),
        "VAULT_PROXY_CONF_TEMPLATE_FOR_TESTS": str(PROXY_TINYPROXY_CONF_PATH),
        "VAULT_PROXY_CONF_FILE_FOR_TESTS": str(tmp_path / "tinyproxy.conf"),
        "PATH": "/usr/bin:/bin",
    }
    if oracle is not None:
        env["VAULT_MANIFEST_ORACLE"] = oracle
    if url is not None:
        env["VAULT_MANIFEST_ORACLE_URL"] = url
    assert _SH is not None
    proc = subprocess.run(
        [_SH, str(PROXY_ENTRYPOINT_PATH), "true"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    rendered = filter_file.read_text(encoding="utf-8") if filter_file.exists() else ""
    return proc.returncode, proc.stdout + proc.stderr, rendered


# --------------------------------------------------------------------------
# config.py: default on, explicit opt-out, unknown refused
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "   ", "steamcmd_api", " STEAMCMD_API "])
def test_unset_empty_or_named_means_on(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    settings = from_env(monkeypatch, value)
    assert settings.manifest_oracle == config.MANIFEST_ORACLE_STEAMCMD_API
    assert settings.manifest_oracle_enabled is True


@pytest.mark.parametrize("value", ["off", "none", " OFF ", "None"])
def test_off_and_none_opt_out(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    settings = from_env(monkeypatch, value)
    assert settings.manifest_oracle == config.MANIFEST_ORACLE_OFF
    assert settings.manifest_oracle_enabled is False


@pytest.mark.parametrize("value", ["steamcmd-api", "false", "0", "disabled", "on"])
def test_an_unknown_value_is_refused_at_startup(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    with pytest.raises(RuntimeError, match="not a supported manifest oracle"):
        from_env(monkeypatch, value)


def test_an_empty_oracle_value_from_compose_means_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """deploy/compose.yaml forwards ``${VAULT_MANIFEST_ORACLE:-}``: an operator
    with no .env entry hands vault-api an EMPTY string, which must be on."""
    compose = (PROXY_ENTRYPOINT_PATH.parents[1] / "compose.yaml").read_text(encoding="utf-8")
    assert compose.count("VAULT_MANIFEST_ORACLE: ${VAULT_MANIFEST_ORACLE:-}") == 2
    assert from_env(monkeypatch, "").manifest_oracle_enabled is True


def test_a_directly_constructed_settings_stays_off() -> None:
    """Deliberate: only ``from_env`` (the operator path) defaults on; a
    ``Settings(...)`` built in code (tests, tools) never reaches the network."""
    settings = Settings(vault_api_key="k", db_path=":memory:", cache_root="c", log_level="INFO")
    assert settings.manifest_oracle_enabled is False


def test_the_default_host_needs_no_allowlist_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = from_env(monkeypatch, None, None, None)
    assert settings.manifest_oracle_enabled is True
    assert settings.egress_allow == frozenset()
    assert urlsplit(config.DEFAULT_MANIFEST_ORACLE_URL).hostname == config.ORACLE_AUTO_ALLOWED_HOST


def test_a_mirror_still_needs_an_allowlist_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    mirror = "https://oracle.lan.example:8443/v1/info"
    with pytest.raises(RuntimeError, match="VAULT_EGRESS_ALLOW"):
        from_env(monkeypatch, None, mirror, None)
    # Allowlisting the DEFAULT host does not satisfy it either.
    with pytest.raises(RuntimeError, match="oracle.lan.example"):
        from_env(monkeypatch, None, mirror, "api.steamcmd.net")
    assert from_env(monkeypatch, None, mirror, "oracle.lan.example").manifest_oracle_enabled


def test_off_never_mentions_the_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = from_env(monkeypatch, "off", "https://oracle.lan.example/v1/info", None)
    assert settings.manifest_oracle_enabled is False


# --------------------------------------------------------------------------
# The proxy entrypoint and config.py agree
# --------------------------------------------------------------------------


@requires_sh
def test_the_proxy_hardcodes_the_same_default_host() -> None:
    script = PROXY_ENTRYPOINT_PATH.read_text(encoding="utf-8")
    match = re.search(r'^ORACLE_AUTO_ALLOWED_HOST="([^"]+)"$', script, re.MULTILINE)
    assert match and match.group(1) == config.ORACLE_AUTO_ALLOWED_HOST


_AGREEMENT_CASES: list[tuple[str, str | None, str | None]] = [
    ("unset, default url", None, None),
    ("empty, default url", "", None),
    ("blank, empty url", "   ", ""),
    ("named, default url", "steamcmd_api", "https://api.steamcmd.net/v1/info"),
    ("named mixed case", " SteamCMD_API ", None),
    ("default host, uppercase, port, userinfo", None, "HTTPS://u@API.SteamCMD.net:443/v1/info?x=1"),
    ("default host over plain http", None, "http://api.steamcmd.net/v1/info"),
    ("off", "off", None),
    ("none", "NONE", None),
    ("off with a mirror", "off", "https://oracle.lan.example/v1/info"),
    ("off with plain http", "off", "http://api.steamcmd.net/v1/info"),
    ("mirror", None, "https://oracle.lan.example/v1/info"),
    ("lookalike host", None, "https://api.steamcmd.net.evil.example/v1/info"),
    ("lookalike prefix", None, "https://evilapi.steamcmd.net/v1/info"),
    ("default host only in the query", None, "https://evil.example/?u=https://api.steamcmd.net"),
    ("default host only in userinfo", None, "https://api.steamcmd.net@evil.example/v1/info"),
    ("ip literal", None, "http://10.0.0.5/v1/info"),
    ("ipv6 literal", None, "https://[::1]:8443/v1/info"),
    ("ipv6 literal, oracle off", "off", "https://[2001:db8::1]/v1/info"),
    ("unbalanced ipv6 bracket", None, "https://[::1/v1/info"),
    ("no scheme", None, "api.steamcmd.net/v1/info"),
    ("ftp scheme", None, "ftp://api.steamcmd.net/v1/info"),
    ("inner space", None, "https://api.steamcmd.net/v1/in fo"),
    ("inner tab", None, "https://api.steamcmd.net\t/v1/info"),
    ("control character", None, "https://api.steamcmd.net/v1/info\x01"),
    ("whitespace with the oracle off", "off", "https://oracle.lan.example/a b"),
    ("unknown selector", "steamcmd-api", None),
    ("unknown selector on", "on", None),
]


@requires_sh
@pytest.mark.parametrize(
    "description,oracle,url", _AGREEMENT_CASES, ids=[c[0] for c in _AGREEMENT_CASES]
)
def test_config_and_proxy_agree_on_every_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    description: str,
    oracle: str | None,
    url: str | None,
) -> None:
    """With an EMPTY allowlist: a value vault-api refuses (selector or URL)
    stops the proxy too; a URL vault-api accepts only with an allowlist entry
    leaves the proxy running with nothing extra allowed; otherwise the proxy
    allows the default host exactly when ``oracle_url_is_auto_allowed``."""
    rc, output, rendered = run_proxy_entrypoint(tmp_path, oracle, url)
    patterns = [line for line in rendered.splitlines() if line.startswith("^")]

    try:
        settings = from_env(monkeypatch, oracle, url, "")
    except RuntimeError as exc:
        if "VAULT_EGRESS_ALLOW" in str(exc):
            # Accepted by both parsers, but needs an allowlist entry.
            assert rc == 0, f"{description}: the proxy refused a valid value:\n{output}"
            assert DEFAULT_HOST_PATTERN not in rendered, description
            assert len(patterns) == 1, patterns  # the baked relay host only
        else:
            assert rc != 0, f"{description}: vault-api refuses ({exc}), the proxy started:\n{output}"
        return

    assert rc == 0, f"{description}: vault-api boots, the proxy refused:\n{output}"
    auto_allowed = settings.manifest_oracle_enabled and config.oracle_url_is_auto_allowed(
        settings.manifest_oracle_url
    )
    assert (DEFAULT_HOST_PATTERN in rendered) is auto_allowed, (description, rendered)
    assert len(patterns) == (2 if auto_allowed else 1), patterns


@requires_sh
def test_an_ipv6_oracle_host_is_logged_quoted_and_never_rendered(tmp_path: Path) -> None:
    rc, output, rendered = run_proxy_entrypoint(tmp_path, None, "https://[2001:DB8::1]:8443/v1/info")
    assert rc == 0, output
    assert "host '2001:db8::1'" in output
    assert "2001" not in rendered


def test_plain_http_to_the_default_host_needs_an_allowlist_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "http://api.steamcmd.net/v1/info"
    with pytest.raises(RuntimeError, match="VAULT_EGRESS_ALLOW"):
        from_env(monkeypatch, None, url, None)
    assert from_env(monkeypatch, None, url, "api.steamcmd.net").manifest_oracle_enabled


@pytest.mark.parametrize(
    "url", ["https://a b.example/", "https://x.example/\tpath", "https://x.example/\x7f"]
)
def test_whitespace_or_control_characters_in_the_url_refuse_to_boot(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    with pytest.raises(RuntimeError, match="whitespace or control"):
        from_env(monkeypatch, "off", url)


@requires_sh
def test_a_mirror_listed_in_the_allowlist_renders_once_and_the_default_host_not(
    tmp_path: Path,
) -> None:
    rc, output, rendered = run_proxy_entrypoint(
        tmp_path, None, "https://oracle.lan.example/v1/info", "oracle.lan.example"
    )
    assert rc == 0, output
    assert r"^oracle\.lan\.example$" in rendered
    assert DEFAULT_HOST_PATTERN not in rendered
