"""WP CORE-FIX-3 (ADR-0020): vault-core's HTTPS passthrough, compose/env side.

Production evidence (2026-10-02): with the LAN DNS rewriting
``*.steamcontent.com`` to vault-core (the documented DNS mode), a prefill
failed with ``HttpRequestException ... while downloading manifests``:
SteamPrefill fetches depot manifests over HTTPS from the CDN host, which then
resolved to vault-core, which only listened on port 80. vault-core now runs
an nginx ``stream`` SNI passthrough on 443 (``core/``, pinned by
``core/docker/check-config-drift.sh`` and ``.github/scripts/verify-core-nginx.sh``).

This file pins the deployment half, one test (group) per guarantee:

G1  the switch ``VAULT_TLS_PASSTHROUGH`` defaults to ON (1) in all three
    places that state a default -- compose's forwarding line, the image ENV,
    the entrypoint hook -- and compose uses the ``:-`` form (blank = default
    on; "off" needs an explicit 0), with deploy/.env.example documenting the
    same default;
G2  port 443 is published by exactly one line, on ``VAULT_TLS_BIND`` only:
    unset/blank falls back to 127.0.0.1 (never 0.0.0.0, never
    ``VAULT_CORE_BIND``), and the host port is switched off by the SAME
    variable via ``${VAULT_TLS_BIND:+...}`` -- no LAN publication without an
    explicit bind address;
G3  port 80's publish line is unchanged by this package;
G4  no other service publishes 443;
G5  the image exposes 443 and still exposes 80;
G6  deploy/.env.example documents every new variable with an anchored
    assignment line (``test_p1_compose_env_defaults.py``'s convention).

What compose actually RESOLVES for each case (unset, blank, set, explicit
0.0.0.0, the port override) is measured by ``deploy/tests/verify-stack.sh``
step 3q against a real ``docker compose config``; this file has no Docker
dependency and pins the text that produces it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.test_p1_compose_env_defaults import _extract_service_environment_block

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "deploy" / "compose.yaml"
CORE_DOCKERFILE = REPO_ROOT / "core" / "Dockerfile"
TLS_HOOK = REPO_ROOT / "core" / "docker" / "26-vault-tls-passthrough.sh"
DEPLOY_ENV_EXAMPLE = REPO_ROOT / "deploy" / ".env.example"

#: The one line that publishes 443. String literal on purpose (LEARNINGS:
#: security pins assert literals, not a value derived from the file under test).
TLS_PORT_LINE = '- "${VAULT_TLS_BIND:-127.0.0.1}:${VAULT_TLS_BIND:+${VAULT_TLS_PORT:-443}}:443"'
HTTP_PORT_LINE = '- "${VAULT_CORE_BIND:-0.0.0.0}:${VAULT_CORE_PORT:-80}:80"'


@pytest.fixture(scope="module")
def compose_text() -> str:
    return COMPOSE.read_text(encoding="utf-8")


def _service_block(compose_text: str, service: str) -> list[str]:
    """Raw lines of one top-level service, up to the next sibling or top-level key."""
    out: list[str] = []
    inside = False
    for line in compose_text.splitlines():
        if re.match(rf"^  {re.escape(service)}:\s*$", line):
            inside = True
            continue
        if inside and (re.match(r"^  [A-Za-z0-9_-]+:\s*$", line) or re.match(r"^[A-Za-z]", line)):
            break
        if inside:
            out.append(line)
    assert out, f"could not isolate the {service}: block in {COMPOSE}"
    return out


def _ports_entries(compose_text: str, service: str) -> list[str]:
    """The non-comment entries of ``service``'s ``ports:`` list, stripped."""
    lines = _service_block(compose_text, service)
    entries: list[str] = []
    in_ports = False
    for line in lines:
        if re.match(r"^\s{4}ports:\s*$", line):
            in_ports = True
            continue
        if in_ports:
            if not re.match(r"^\s{6}", line):
                break
            stripped = line.strip()
            if stripped.startswith("- "):
                entries.append(stripped)
    return entries


# -- G1: the switch defaults to on, everywhere -----------------------------


def test_compose_forwards_the_switch_with_the_colon_form(compose_text: str) -> None:
    block = _extract_service_environment_block(compose_text, "vault-core")
    lines = [line.strip() for line in block.splitlines() if re.match(r"^\s{6}VAULT_TLS_PASSTHROUGH:", line)]
    assert lines == ["VAULT_TLS_PASSTHROUGH: ${VAULT_TLS_PASSTHROUGH:-1}"], (
        f"vault-core forwards VAULT_TLS_PASSTHROUGH as {lines!r}. Expected the `:-1` form: "
        "a blank .env line must mean the default (on), and the default must be on."
    )


def test_image_env_default_is_on() -> None:
    text = CORE_DOCKERFILE.read_text(encoding="utf-8")
    assert re.findall(r"^\s*VAULT_TLS_PASSTHROUGH=(\S*)\s*$", text, re.MULTILINE) == ["1"]


def test_hook_default_is_on_for_unset_and_blank() -> None:
    text = TLS_HOOK.read_text(encoding="utf-8")
    assert re.findall(r'^VALUE="\$\{VAULT_TLS_PASSTHROUGH:-([^}]*)\}"$', text, re.MULTILINE) == ["1"], (
        "26-vault-tls-passthrough.sh must read the switch as ${VAULT_TLS_PASSTHROUGH:-1}, "
        "the same unset/blank = on meaning as compose's forwarding line."
    )


def test_env_example_documents_the_default() -> None:
    text = DEPLOY_ENV_EXAMPLE.read_text(encoding="utf-8")
    assert re.findall(r"^#?VAULT_TLS_PASSTHROUGH=(.*)$", text, re.MULTILINE) == ["1"]


# -- G2: 443 is published on VAULT_TLS_BIND only ---------------------------


def test_443_is_published_by_exactly_the_reviewed_line(compose_text: str) -> None:
    tls_entries = [e for e in _ports_entries(compose_text, "vault-core") if e.rstrip('"').endswith(":443")]
    assert tls_entries == [TLS_PORT_LINE], (
        f"vault-core's 443 publish lines are {tls_entries!r}, expected exactly [{TLS_PORT_LINE!r}]"
    )


def test_443_host_ip_never_defaults_to_all_interfaces_or_follows_core_bind() -> None:
    """The structure of TLS_PORT_LINE itself, stated as separate claims so a
    reviewer can see each one: host ip default 127.0.0.1, no VAULT_CORE_BIND,
    and the published port gated on the SAME variable as the host ip."""
    m = re.fullmatch(r'- "\$\{([A-Z_]+):-([^}]*)\}:\$\{([A-Z_]+):\+\$\{VAULT_TLS_PORT:-443\}\}:443"', TLS_PORT_LINE)
    assert m, TLS_PORT_LINE
    bind_var, default_ip, gate_var = m.groups()
    assert bind_var == "VAULT_TLS_BIND"
    assert default_ip == "127.0.0.1"
    assert gate_var == bind_var
    assert "VAULT_CORE_BIND" not in TLS_PORT_LINE


# -- G3 / G4: nothing else changed or publishes 443 ------------------------


def test_port_80_line_is_unchanged(compose_text: str) -> None:
    http_entries = [e for e in _ports_entries(compose_text, "vault-core") if e.rstrip('"').endswith(":80")]
    assert http_entries == [HTTP_PORT_LINE]


def test_vault_core_publishes_exactly_two_ports(compose_text: str) -> None:
    assert _ports_entries(compose_text, "vault-core") == [HTTP_PORT_LINE, TLS_PORT_LINE]


@pytest.mark.parametrize("service", ["vault-api", "vault-runner", "vault-proxy", "vault-dns"])
def test_no_other_service_publishes_443(service: str, compose_text: str) -> None:
    lines = "\n".join(_service_block(compose_text, service))
    assert not re.search(r":443[\"'\s]|:443$", lines, re.MULTILINE), f"{service} mentions a :443 mapping"


# -- G5: the image exposes both ports ----------------------------------------


def test_dockerfile_exposes_80_and_443() -> None:
    text = CORE_DOCKERFILE.read_text(encoding="utf-8")
    assert re.findall(r"^EXPOSE\s+(.+?)\s*$", text, re.MULTILINE) == ["80 443"]


# -- G6: every new variable is documented ----------------------------------


@pytest.mark.parametrize("env_var", ["VAULT_TLS_PASSTHROUGH", "VAULT_TLS_BIND", "VAULT_TLS_PORT"])
def test_new_vars_are_documented_in_env_example(env_var: str) -> None:
    text = DEPLOY_ENV_EXAMPLE.read_text(encoding="utf-8")
    assert re.search(rf"^#?{re.escape(env_var)}=", text, re.MULTILINE), (
        f"deploy/.env.example has no {env_var}= or #{env_var}= assignment line"
    )


def test_env_example_does_not_set_a_tls_bind_by_default() -> None:
    """Copying .env.example must not publish 443 to the LAN: the bind stays a
    commented-out, empty example."""
    text = DEPLOY_ENV_EXAMPLE.read_text(encoding="utf-8")
    assert re.findall(r"^VAULT_TLS_BIND=.*$", text, re.MULTILINE) == []
    assert re.findall(r"^#VAULT_TLS_BIND=(.*)$", text, re.MULTILINE) == [""]
