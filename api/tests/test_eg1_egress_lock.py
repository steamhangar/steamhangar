"""WP EG-1 (ADR-0011) — the egress lock's static, CI-runnable pins.

`deploy/compose.yaml`'s network topology is the mechanism that actually
enforces the lock (vault-api loses `default`, gains a no-masquerade
`vault-lan` plus an `internal: true` `vault-egress` it shares with exactly
one other container, `vault-proxy`) — see the banner comment above that
file's own top-level `networks:` block for the full argument. This module is
the same class of guard `test_p1_compose_env_defaults.py` and
`test_version_pin.py` already are for their own facts: parse the real files
directly (no Docker, no network, plain pytest, runs in CI on every commit),
never hand-copy a fact this repository already states once elsewhere, and
fail BY NAME in the specific direction that regressed.

Two files each state the `vault-egress` subnet independently
(`deploy/compose.yaml`'s `ipam.config` and `deploy/proxy/tinyproxy.conf`'s
`Allow` directive) with no YAML/config anchor tying them together — a hand-
edit to one that misses the other is a *silent* regression, not a loud one:
tinyproxy would still start, still render a filter file, and simply reject
every request from vault-api's real (now-unlisted) subnet with an `Allow`-
stage rejection that looks, from the outside, identical to a `Filter`-stage
403 — see `test_tinyproxy_conf_allow_subnet_matches_compose_vault_egress_subnet`
below, this module's own cross-file consistency pin for exactly that.

The empirical counterparts that need a real Docker host (a real container
reattached to a default-routed network dies by name; an un-allowlisted
host actually gets refused by a running proxy; the whole stack behaves
this way end to end; DNS resolution and the Docker host's own reachable
addresses stay open, exactly as documented) live in
`deploy/tests/verify-stack.sh`, which is not part of `ci.yml` for that
reason — same division of labour as every other Docker-dependent check
this project already has two layers for.

Round-2 review N4/S2 added a THIRD layer, still Docker-free: several tests
below invoke `deploy/proxy/validate-hostname.sh` and
`deploy/proxy/docker-entrypoint.sh` directly, as real `sh` subprocesses
(not inside any container, no Docker involved) — pinning the actual shell
logic vault-proxy ships, not a Python reimplementation of it, for the one
class of fact (character-validation agreement between this shell script
and `config.py`, and the `set -f` fix for a literal `*` entry) that a pure
text-parsing pin cannot see.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from vault_api.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = REPO_ROOT / "deploy" / "compose.yaml"
PROXY_DOCKERFILE_PATH = REPO_ROOT / "deploy" / "proxy" / "Dockerfile"
PROXY_TINYPROXY_CONF_PATH = REPO_ROOT / "deploy" / "proxy" / "tinyproxy.conf"
PROXY_ENTRYPOINT_PATH = REPO_ROOT / "deploy" / "proxy" / "docker-entrypoint.sh"
PROXY_VALIDATOR_PATH = REPO_ROOT / "deploy" / "proxy" / "validate-hostname.sh"

#: Round-2 review N4/S2: the api-tests CI job (`.github/workflows/ci.yml`,
#: `runs-on: ubuntu-latest`) always has a POSIX `sh` (dash) on PATH, and so
#: does a Windows development machine with Git for Windows installed (it
#: ships `sh.exe` under its `usr/bin` directory, confirmed during this WP)
#: -- but `shutil.which` is the honest, environment-agnostic way to state
#: that requirement rather than assume it, and skips cleanly with a named
#: reason on the one hypothetical host that lacks it.
_SH = shutil.which("sh")
requires_sh = pytest.mark.skipif(_SH is None, reason="no POSIX 'sh' on PATH")

#: Same subnet compose.yaml's `vault-egress` network pins (see this module's
#: own docstring for why the two are checked against EACH OTHER, not just
#: each against a hand-typed literal here — a literal here would only catch
#: one of the two ever drifting, not the pair drifting apart from each
#: other while each individually still "matches" this constant).
_EXPECTED_VAULT_EGRESS_SUBNET = "172.30.238.0/24"


@pytest.fixture(scope="module")
def compose_text() -> str:
    return COMPOSE_PATH.read_text(encoding="utf-8")


def _top_level_block(compose_text: str, key: str) -> str:
    """Return the raw text under a 0-indent top-level ``key:`` (e.g.
    ``networks``), from its own header line (exclusive) to the next 0-indent
    key (exclusive). Distinct from `_service_networks_block` below: this one
    is anchored to column 0, a SERVICE's `networks:` sub-key sits at column
    4 inside that service's own block — the same string ("networks:") means
    two different things depending on indentation, so the two extractors
    must not be conflated into one regex.
    """
    lines = compose_text.splitlines()
    header = re.compile(rf"^{re.escape(key)}:\s*$")
    block: list[str] = []
    in_block = False
    for line in lines:
        if header.match(line):
            in_block = True
            continue
        if in_block and re.match(r"^[A-Za-z]", line):
            break
        if in_block:
            block.append(line)
    return "\n".join(block)


def _service_block(compose_text: str, service_name: str) -> str:
    """Isolate one service's whole body (header exclusive, next sibling
    service or next top-level key exclusive) — the same extraction shape
    `test_version_pin.py`'s `_service_blocks` and
    `test_p1_compose_env_defaults.py`'s per-service helpers already use,
    reimplemented narrowly here (single service, not "every 2-space-indented
    key in the file") because this module only ever needs one at a time.
    """
    lines = compose_text.splitlines()
    header = re.compile(rf"^  {re.escape(service_name)}:\s*$")
    block: list[str] = []
    in_service = False
    for line in lines:
        if header.match(line):
            in_service = True
            continue
        if in_service and re.match(r"^  [A-Za-z0-9_-]+:\s*$", line):
            break
        if in_service and re.match(r"^[A-Za-z]", line):
            break
        if in_service:
            block.append(line)
    return "\n".join(block)


def _service_networks(compose_text: str, service_name: str) -> list[str]:
    """The list of network names under a SERVICE's own `networks:` sub-key
    (4-space indent, `- name` items at 6-space indent) — never the top-level
    `networks:` block (see `_top_level_block`'s docstring for why the two
    must stay separate extractors).
    """
    block = _service_block(compose_text, service_name)
    lines = block.splitlines()
    result: list[str] = []
    in_networks = False
    for line in lines:
        if re.match(r"^\s{4}networks:\s*$", line):
            in_networks = True
            continue
        if in_networks:
            item = re.match(r"^\s{6}-\s*([A-Za-z0-9_-]+)\s*$", line)
            if item:
                result.append(item.group(1))
                continue
            if re.match(r"^\s{0,4}\S", line):
                break
    return result


# ==========================================================================
# 1. The top-level `networks:` block itself
# ==========================================================================


def test_vault_lan_network_disables_masquerade(compose_text: str) -> None:
    """The one directive that makes `vault-lan` a "no default route out"
    network at all (deploy/compose.yaml's own banner comment; measured
    directly during this WP: a container on a network configured exactly
    this way times out reaching a public IP, but a published port on it
    stays reachable inbound).
    """
    networks_block = _top_level_block(compose_text, "networks")
    vault_lan_block = re.search(
        r"^  vault-lan:\s*\n((?:^(?:    |  \S).*\n?)*)",
        networks_block,
        re.MULTILINE,
    )
    assert vault_lan_block, "deploy/compose.yaml: top-level networks: block has no vault-lan: entry"
    assert (
        'com.docker.network.bridge.enable_ip_masquerade: "false"' in vault_lan_block.group(1)
    ), (
        "deploy/compose.yaml's vault-lan network no longer disables IP "
        "masquerade -- without this, vault-api's own container would have "
        "a normal, working default route out, and the egress lock would be "
        "cosmetic (the network would allow exactly what it claims to block)."
    )


def test_vault_egress_network_is_internal(compose_text: str) -> None:
    networks_block = _top_level_block(compose_text, "networks")
    vault_egress_block = re.search(
        r"^  vault-egress:\s*\n((?:^(?:    |  \S).*\n?)*)",
        networks_block,
        re.MULTILINE,
    )
    assert vault_egress_block, "deploy/compose.yaml: top-level networks: block has no vault-egress: entry"
    assert re.search(r"^\s+internal:\s*true\s*$", vault_egress_block.group(1), re.MULTILINE), (
        "deploy/compose.yaml's vault-egress network is missing `internal: "
        "true` -- without it, Docker gives this network a normal route out "
        "via masquerade, which is the ONE thing this network exists to "
        "never have (it is vault-api's sole path to vault-proxy, and must "
        "not become a second path to anywhere else)."
    )


def test_vault_egress_subnet_is_pinned(compose_text: str) -> None:
    networks_block = _top_level_block(compose_text, "networks")
    assert f"subnet: {_EXPECTED_VAULT_EGRESS_SUBNET}" in networks_block, (
        f"deploy/compose.yaml's vault-egress network no longer pins the "
        f"subnet {_EXPECTED_VAULT_EGRESS_SUBNET} -- "
        "deploy/proxy/tinyproxy.conf's own Allow directive names this exact "
        "CIDR; changing one without the other silently breaks the proxy's "
        "client-allowlist stage for real traffic (see this module's "
        "docstring, and test_tinyproxy_conf_allow_subnet_matches_compose_"
        "vault_egress_subnet below for the cross-file pin)."
    )


# ==========================================================================
# 2. vault-api: which networks it is (and is NOT) attached to
# ==========================================================================


def test_vault_api_is_attached_to_vault_lan_and_vault_egress_only(
    compose_text: str,
) -> None:
    """The mutation this package's own bar names explicitly: reattaching
    vault-api to a default-routed network must be caught, by name. `default`
    absent is as load-bearing as `vault-lan`/`vault-egress` present — a
    service with no `networks:` key at all falls back to Compose's own
    `default` network (see vault-core/vault-runner/vault-dns, deliberately
    unchanged by this package), so vault-api regaining that fallback (by
    losing this list, or by the list gaining `default`) is exactly the
    regression this test exists to catch.
    """
    nets = set(_service_networks(compose_text, "vault-api"))
    assert nets == {"vault-lan", "vault-egress"}, (
        f"deploy/compose.yaml's vault-api service is attached to {sorted(nets)}, "
        "expected exactly {'vault-lan', 'vault-egress'} -- vault-api must "
        "never be attached to 'default' (that IS the egress lock; a "
        "default-routed vault-api has a normal, working route to the "
        "internet, lock or no lock), and must keep BOTH of the other two "
        "(vault-lan for its published port, vault-egress for the proxy)."
    )


def test_vault_api_networks_line_exists_at_all(compose_text: str) -> None:
    """Precondition for the test above (LEARNINGS.md "Testing discipline"):
    if vault-api's `networks:` sub-key vanished entirely, `_service_networks`
    would return an empty list, which is a DIFFERENT failure from "the wrong
    set" and deserves its own name so a reader is not stuck comparing an
    empty set against the expected one and wondering why.
    """
    block = _service_block(compose_text, "vault-api")
    assert re.search(r"^\s{4}networks:\s*$", block, re.MULTILINE), (
        "deploy/compose.yaml's vault-api service has no networks: sub-key "
        "at all -- it would fall back to Compose's implicit 'default' "
        "network, which is the egress lock's exact failure mode."
    )


# ==========================================================================
# 3. vault-proxy: dual attachment, no published ports, least-privilege posture
# ==========================================================================


def test_vault_proxy_service_exists(compose_text: str) -> None:
    block = _service_block(compose_text, "vault-proxy")
    assert block.strip(), (
        "deploy/compose.yaml has no vault-proxy: service block -- this is "
        "the mutation bar's own named case: dropping the proxy service must "
        "fail here (and, empirically, in deploy/tests/verify-stack.sh)."
    )


def test_vault_proxy_is_attached_to_vault_egress_and_default(compose_text: str) -> None:
    nets = set(_service_networks(compose_text, "vault-proxy"))
    assert nets == {"vault-egress", "default"}, (
        f"deploy/compose.yaml's vault-proxy service is attached to "
        f"{sorted(nets)}, expected exactly {{'vault-egress', 'default'}} -- "
        "vault-egress to reach vault-api, default for its own real route "
        "out. Losing 'default' would strand it with no WAN/LAN reach at "
        "all; losing 'vault-egress' would strand vault-api with no proxy to "
        "talk to."
    )


def test_vault_proxy_publishes_no_ports(compose_text: str) -> None:
    """This process is reached only from vault-api, over vault-egress —
    never from the LAN or the host. A `ports:` entry here would be a new,
    unintended inbound surface with no legitimate consumer.
    """
    block = _service_block(compose_text, "vault-proxy")
    assert not re.search(r"^\s{4}ports:\s*$", block, re.MULTILINE), (
        "deploy/compose.yaml's vault-proxy service now has a ports: "
        "mapping -- this process should never be reachable from outside "
        "vault-egress; a published port is a new inbound surface this "
        "container was never meant to have."
    )


def test_vault_proxy_has_least_privilege_posture(compose_text: str) -> None:
    block = _service_block(compose_text, "vault-proxy")
    assert re.search(r"^\s{4}security_opt:\s*$", block, re.MULTILINE), (
        "deploy/compose.yaml's vault-proxy service is missing a "
        "security_opt: block."
    )
    assert re.search(r"^\s{6}-\s*no-new-privileges:true\s*$", block, re.MULTILINE), (
        "deploy/compose.yaml's vault-proxy service is missing "
        "no-new-privileges:true."
    )
    assert re.search(r"^\s{4}cap_drop:\s*$", block, re.MULTILINE), (
        "deploy/compose.yaml's vault-proxy service is missing a cap_drop: "
        "block."
    )
    assert re.search(r"^\s{6}-\s*ALL\s*$", block, re.MULTILINE), (
        "deploy/compose.yaml's vault-proxy service's cap_drop: block no "
        "longer drops ALL -- it never runs as root (Dockerfile's USER "
        "tinyproxy) and has no legitimate use for any Linux capability."
    )


# ==========================================================================
# 4. vault-core, vault-runner, vault-dns: deliberately UNCHANGED by this WP
# ==========================================================================


@pytest.mark.parametrize("service_name", ["vault-core", "vault-runner", "vault-dns"])
def test_unlocked_services_have_no_explicit_networks_override(
    compose_text: str, service_name: str
) -> None:
    """ADR-0011: vault-runner stays broad on purpose (its egress is Steam's
    CM/CDN fleet, not enumerable), and vault-core keeps its own, separate,
    already-narrower Host-allowlist mechanism (ADR-0001 req 4). Neither
    needed a single line changed for this package — the way that STAYS true
    is that neither gained a `networks:` sub-key at all, leaving both on
    Compose's ordinary, fully-routed implicit `default` network exactly as
    before. A `networks:` key appearing on any of these three is itself the
    finding (regardless of what it says), which is why this asserts absence
    rather than a specific value.
    """
    block = _service_block(compose_text, service_name)
    assert not re.search(r"^\s{4}networks:\s*$", block, re.MULTILINE), (
        f"deploy/compose.yaml's {service_name} service now has an explicit "
        "networks: override -- ADR-0011 states this service is deliberately "
        "UNCHANGED by the egress lock; if this is a real, intended change, "
        "update ADR-0011 and this test together, not just compose.yaml."
    )


# ==========================================================================
# 5. vault-api's HTTP_PROXY / HTTPS_PROXY / NO_PROXY — literal, not
#    operator-configurable (unlike VAULT_EGRESS_ALLOW, these are not
#    `${VAR:-default}` lines, so test_p1_compose_env_defaults.py's own
#    generic mechanism does not see them at all).
# ==========================================================================


def test_vault_api_has_the_proxy_env_lines(compose_text: str) -> None:
    block = _service_block(compose_text, "vault-api")
    assert re.search(r"^\s{6}HTTP_PROXY:\s*http://vault-proxy:8888\s*$", block, re.MULTILINE), (
        "deploy/compose.yaml's vault-api service is missing (or has the "
        "wrong value for) HTTP_PROXY -- this is vault-api's only route to "
        "anything beyond vault-lan/vault-egress; a missing or wrong value "
        "here means every outbound call (oracle, relay, webhooks) fails, "
        "not just the ones the allowlist would refuse anyway."
    )
    assert re.search(r"^\s{6}HTTPS_PROXY:\s*http://vault-proxy:8888\s*$", block, re.MULTILINE), (
        "deploy/compose.yaml's vault-api service is missing (or has the "
        "wrong value for) HTTPS_PROXY."
    )
    assert re.search(
        r'^\s{6}NO_PROXY:\s*"127\.0\.0\.1,localhost"\s*$', block, re.MULTILINE
    ), (
        "deploy/compose.yaml's vault-api service's NO_PROXY is not the "
        "literal \"127.0.0.1,localhost\" -- see the long comment on this "
        "key in compose.yaml for why it is exactly this and nothing wider: "
        "measured directly (WP EG-1), api/Dockerfile's own HEALTHCHECK "
        "calls urllib against 127.0.0.1:8080, and with an empty NO_PROXY "
        "that request was ALSO routed through HTTP_PROXY and filtered "
        "(403), making the container permanently unhealthy. A non-loopback "
        "value here would not create a LAN shortcut either, it would just "
        "break the excluded host entirely (vault-lan has no working direct "
        "route to anything ELSE, LAN or WAN)."
    )


# ==========================================================================
# 6. Cross-file consistency: the vault-egress subnet, and the base image pin
# ==========================================================================


def test_tinyproxy_conf_allow_subnet_matches_compose_vault_egress_subnet() -> None:
    """See this module's own docstring for why this is checked as a PAIR,
    not against a hand-typed literal in each file separately: a hand-edit
    that changes compose.yaml's subnet without also updating
    tinyproxy.conf's Allow line degrades silently (tinyproxy still starts,
    still renders a filter file, and refuses every real request from
    vault-api's now-unlisted address with an Allow-stage rejection that is
    easy to mistake for a Filter-stage 403 from the outside).
    """
    compose_text = COMPOSE_PATH.read_text(encoding="utf-8")
    conf_text = PROXY_TINYPROXY_CONF_PATH.read_text(encoding="utf-8")
    networks_block = _top_level_block(compose_text, "networks")
    compose_match = re.search(r"subnet:\s*(\S+)", networks_block)
    assert compose_match, "deploy/compose.yaml: could not find vault-egress's subnet: line at all"
    # Specifically the CIDR-shaped Allow line (contains "/") -- tinyproxy.conf
    # also has a plain `Allow 127.0.0.1` loopback line with no such shape,
    # which a bare `^Allow\s+(\S+)$` search would find FIRST and wrongly
    # compare against compose's subnet.
    conf_match = re.search(r"^Allow\s+(\d[\d.]*/\d+)\s*$", conf_text, re.MULTILINE)
    assert conf_match, "deploy/proxy/tinyproxy.conf: could not find an Allow <cidr>/<bits> line at all"
    assert compose_match.group(1) == conf_match.group(1), (
        f"deploy/compose.yaml's vault-egress subnet ({compose_match.group(1)}) "
        f"and deploy/proxy/tinyproxy.conf's Allow CIDR "
        f"({conf_match.group(1)}) have drifted apart -- update both "
        "together, or every real request from vault-api will be rejected "
        "at tinyproxy's client-allowlist stage, regardless of the "
        "destination-allowlist (Filter) contents."
    )


def test_proxy_dockerfile_reuses_the_dns_dockerfiles_pinned_alpine_digest() -> None:
    """ADR-0011's stated design (reusing an already-vetted, already-pinned
    base beats adding a second one to track) — pinned here as a real
    equality check, not a comment reading each other's mind: both files must
    name the exact SAME `FROM alpine:...@sha256:...` line.
    """
    dns_dockerfile = (REPO_ROOT / "dns" / "Dockerfile").read_text(encoding="utf-8")
    proxy_dockerfile = PROXY_DOCKERFILE_PATH.read_text(encoding="utf-8")
    pattern = re.compile(r"^FROM\s+(alpine:\S+)\s*$", re.MULTILINE)
    dns_match = pattern.search(dns_dockerfile)
    proxy_match = pattern.search(proxy_dockerfile)
    assert dns_match, "dns/Dockerfile: could not find its own FROM alpine:... line"
    assert proxy_match, "deploy/proxy/Dockerfile: could not find its own FROM alpine:... line"
    assert dns_match.group(1) == proxy_match.group(1), (
        f"dns/Dockerfile pins {dns_match.group(1)!r} but "
        f"deploy/proxy/Dockerfile pins {proxy_match.group(1)!r} -- ADR-0011 "
        "states these reuse the SAME already-vetted base digest; if a "
        "deliberate, independent bump is intended, update ADR-0011's "
        "wording along with whichever Dockerfile changed."
    )
    assert "@sha256:" in dns_match.group(1), (
        "dns/Dockerfile's own FROM line has no @sha256: digest pin -- "
        "precondition for the equality check above meaning anything at all."
    )


# ==========================================================================
# 7. The entrypoint's hostname validation ACTUALLY agrees with config.py's
#    (both parse the SAME operator-supplied VAULT_EGRESS_ALLOW value
#    independently) — round-2 review N4/S2. The pre-round-2 version of this
#    section only grepped for the literal substring "A-Za-z0-9.-" in the
#    shell script's text, which cannot see a BEHAVIOURAL disagreement (the
#    same character class, applied in a different order or with a different
#    shape rule, still "contains" that substring) — exactly how three real
#    disagreements shipped without this file catching any of them:
#    "a..b" (empty label), internal whitespace ("ap i.com"), and a literal
#    "*" (shell pathname expansion at the `for raw in $VAR` line itself,
#    before any validation ever ran). Every test below invokes the REAL
#    artifacts — `deploy/proxy/validate-hostname.sh` via a real `sh`
#    subprocess, `deploy/proxy/docker-entrypoint.sh` end to end via the
#    same — never a Python reimplementation of either
#    (docs/LEARNINGS.md's "pinned-the-fake" class, WP 4b.2/4b.3/4b.8: a test
#    fake that re-implements production logic proves nothing about the
#    shipped path).
# ==========================================================================


def _run_shell_validator(raw_entry: str) -> tuple[int, str]:
    """Source `validate-hostname.sh` and call `normalize_egress_hostname`
    with ``raw_entry``, passed through an ENVIRONMENT VARIABLE rather than
    an argv element.

    Deliberately not `subprocess.run([..., raw_entry])`: measured directly,
    on this project's own Windows dev box, Git for Windows' MSYS runtime
    auto-globs a bare `*` ARGV element against the current directory before
    `sh` itself ever sees it (a `sh -c 'echo "$1"' sh '*'` prints a real
    filename, e.g. "Dockerfile", not the literal `*`) — an environment
    variable is unaffected (confirmed the same way) because it never passes
    through argv-style command-line construction at all. This is a property
    of THIS TEST's own subprocess invocation on this one platform, not of
    the shipped script (verified separately, directly, under WSL2's dash —
    see docs/adr/0011-egress-lock.md and this module's own docstring for
    the `set -f` fix this exact "*" case is pinning); the env-var passing
    style here sidesteps the host quirk entirely, on every platform.

    Returns ``(exit_code, stdout_stripped)``: exit 0 with the normalized
    hostname on stdout, 1 (empty stdout) for an implausible hostname, 2
    (empty stdout) for a blank/whitespace-only entry.
    """
    script = f'. "{PROXY_VALIDATOR_PATH}"\nnormalize_egress_hostname "$RAW_ENTRY_UNDER_TEST"\n'
    proc = subprocess.run(
        [_SH, "-c", script],
        env={"RAW_ENTRY_UNDER_TEST": raw_entry, "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=10,
    )
    return proc.returncode, proc.stdout.strip()


def _run_entrypoint(
    egress_allow: str, tmp_path: Path
) -> tuple[int, str, Path]:
    """Run the REAL `docker-entrypoint.sh` end to end (rendering into a
    throwaway filter file, then a harmless final command instead of
    tinyproxy — `VAULT_PROXY_FILTER_FILE_FOR_TESTS`, added specifically for
    this test, see that script's own comment on the variable). No Docker,
    no root, no `/etc` involved. Returns
    ``(exit_code, combined_stdout_stderr, filter_file_path)``.
    """
    filter_file = tmp_path / "filter"
    proc = subprocess.run(
        [_SH, str(PROXY_ENTRYPOINT_PATH), "true"],
        env={
            "VAULT_EGRESS_ALLOW": egress_allow,
            "VAULT_PROXY_FILTER_FILE_FOR_TESTS": str(filter_file),
            "PATH": "/usr/bin:/bin",
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    return proc.returncode, proc.stdout + proc.stderr, filter_file


#: One (description, raw entry, expect-valid) triple per case round-2
#: review named explicitly, plus the pre-existing happy-path/shape cases —
#: run against BOTH sides below so a future divergence is caught on
#: whichever specific input triggers it, not just "some input, somewhere".
_AGREEMENT_CASES: list[tuple[str, str, bool]] = [
    ("valid simple", "api.steamcmd.net", True),
    ("valid uppercase normalizes", "API.STEAMCMD.NET", True),
    ("valid multi-label with hyphen", "my-webhook.example.co.uk", True),
    ("empty label", "a..b", False),
    ("internal whitespace", "ap i.com", False),
    ("leading whitespace only (trimmed)", "  api.steamcmd.net", True),
    ("trailing whitespace only (trimmed)", "api.steamcmd.net  ", True),
    ("literal star", "*", False),
    ("leading dot", ".steamcmd.net", False),
    ("trailing dot", "steamcmd.net.", False),
    ("leading hyphen", "-steamcmd.net", False),
    ("trailing hyphen", "steamcmd.net-", False),
    ("shell metacharacter injection attempt", "api.steamcmd.net;rm -rf /", False),
    ("non-ASCII", "évil.example.com", False),
]


@requires_sh
@pytest.mark.parametrize(
    "description,raw_entry,expect_valid", _AGREEMENT_CASES, ids=[c[0] for c in _AGREEMENT_CASES]
)
def test_shell_validator_matches_config_py_on_every_case(
    description: str,
    raw_entry: str,
    expect_valid: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The core agreement pin: for each input, `_env_egress_allow` (Python)
    and `normalize_egress_hostname` (the REAL shell script, subprocessed)
    must reach the SAME accept/reject verdict, and on acceptance, the SAME
    normalized value.
    """
    shell_rc, shell_out = _run_shell_validator(raw_entry)
    shell_accepted = shell_rc == 0

    monkeypatch.setenv("VAULT_API_KEY", "k")
    monkeypatch.setenv("VAULT_EGRESS_ALLOW", raw_entry)
    try:
        python_hosts = Settings.from_env().egress_allow
        python_accepted = True
    except RuntimeError:
        python_hosts = frozenset()
        python_accepted = False

    assert shell_accepted == expect_valid, (
        f"[{description}] shell validator {'accepted' if shell_accepted else 'rejected'} "
        f"{raw_entry!r}, expected {'accept' if expect_valid else 'reject'} "
        f"(exit={shell_rc}, stdout={shell_out!r})"
    )
    assert python_accepted == expect_valid, (
        f"[{description}] config.py {'accepted' if python_accepted else 'rejected'} "
        f"{raw_entry!r}, expected {'accept' if expect_valid else 'reject'}"
    )
    assert shell_accepted == python_accepted, (
        f"[{description}] DISAGREEMENT on {raw_entry!r}: shell "
        f"{'accepted' if shell_accepted else 'rejected'} it "
        f"(exit={shell_rc}, stdout={shell_out!r}), config.py "
        f"{'accepted' if python_accepted else 'rejected'} it -- the two "
        "independent renderers of this value no longer agree."
    )
    if expect_valid:
        assert shell_out in python_hosts, (
            f"[{description}] shell normalized {raw_entry!r} to {shell_out!r}, "
            f"but config.py's accepted set is {sorted(python_hosts)!r} -- "
            "the normalized VALUE (not just the accept/reject verdict) must "
            "agree too."
        )


@requires_sh
def test_entrypoint_end_to_end_rejects_a_literal_star_by_name_not_by_glob(
    tmp_path: Path,
) -> None:
    """Round-2 review's own named finding: `VAULT_EGRESS_ALLOW=*` used to
    undergo the SHELL's OWN pathname expansion at the `for raw in $VAR`
    line, before `normalize_egress_hostname` ever ran -- turning one
    malicious/typo'd entry into one iteration per filename in the
    container's working directory (measured: 17 patterns in that review
    round). This runs the REAL entrypoint end to end (not just the
    validator function in isolation, the way the parametrized test above
    does) so the fix -- `set -f` around that exact loop, in
    docker-entrypoint.sh -- is pinned as a whole, not just the function it
    calls.
    """
    rc, output, filter_file = _run_entrypoint("*", tmp_path)
    assert rc != 0, (
        f"entrypoint exited 0 with VAULT_EGRESS_ALLOW='*' -- it must refuse "
        f"to boot. Output:\n{output}"
    )
    assert "entry '*' is not a plausible hostname" in output, (
        f"entrypoint's failure message does not name the literal '*' entry "
        f"-- if pathname expansion regressed, this would instead name some "
        f"unrelated, glob-expanded filename. Output:\n{output}"
    )
    assert "filter file rendered:" not in output, (
        "entrypoint logged a successful render despite the invalid entry -- "
        "it must die before completing, not after."
    )


@requires_sh
def test_entrypoint_end_to_end_renders_the_expected_filter_file(
    tmp_path: Path,
) -> None:
    """The full, real rendering pipeline, exercised end to end: baked-in
    host present unconditionally, operator entries anchored and
    dot-escaped, mixed case normalized, and nothing from a rejected/blank
    entry leaking through.
    """
    rc, output, filter_file = _run_entrypoint(
        "API.STeamCMD.net, my-webhook.example.com,,", tmp_path
    )
    assert rc == 0, f"entrypoint failed on a valid allowlist. Output:\n{output}"
    rendered = filter_file.read_text(encoding="utf-8")
    assert r"^api\.steampowered\.com$" in rendered, "baked-in relay host missing from the rendered filter"
    assert r"^api\.steamcmd\.net$" in rendered, "operator host missing or not normalized/anchored/escaped correctly"
    assert r"^my-webhook\.example\.com$" in rendered, "second operator host missing or malformed"
    assert "filter file rendered: api.steampowered.com (baked-in) + 2 operator host(s)" in output, (
        f"entrypoint's own summary log line does not report exactly 2 "
        f"operator hosts (the two valid entries; the blank ones from the "
        f"trailing commas must be silently skipped, not counted). Output:\n{output}"
    )


# ==========================================================================
# 8. Round-2 review S1: the two directives that make "Filter" an ALLOWLIST
#    at all had no static pin -- deleting either one survived the full
#    suite (caught only by verify-stack.sh's live 6l, not in CI).
# ==========================================================================


def test_tinyproxy_conf_has_filter_default_deny_yes() -> None:
    conf_text = PROXY_TINYPROXY_CONF_PATH.read_text(encoding="utf-8")
    assert re.search(r"^FilterDefaultDeny\s+Yes\s*$", conf_text, re.MULTILINE), (
        "deploy/proxy/tinyproxy.conf is missing 'FilterDefaultDeny Yes' -- "
        "without it, an unlisted host is ALLOWED by default and the "
        "rendered filter file only removes hosts instead of restricting to "
        "them. This is the one directive that makes this an allowlist "
        "instead of a blocklist."
    )


def test_tinyproxy_conf_has_the_filter_directive() -> None:
    conf_text = PROXY_TINYPROXY_CONF_PATH.read_text(encoding="utf-8")
    assert re.search(r'^Filter\s+"/etc/tinyproxy/filter"\s*$', conf_text, re.MULTILINE), (
        "deploy/proxy/tinyproxy.conf is missing the 'Filter "
        '"/etc/tinyproxy/filter"\' directive -- without it, '
        "FilterDefaultDeny has nothing to consult and tinyproxy behaves as "
        "an unrestricted forward proxy."
    )
