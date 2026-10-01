"""Static pin for the shared /vault mount, in both VAULT_CACHE_PATH modes.

Named-volume mode (VAULT_CACHE_PATH unset or blank): both the vault-core and
the vault-api image contain /vault/cache/depot and /vault/tmp. Docker copies
image content into an EMPTY named volume when a container is created with it,
and Compose creates both services concurrently, so on a fresh volume both
copied at once and one create failed with "mkdir .../_data/tmp: file exists"
(live `verify-stack.sh` run). The fix is `nocopy` on every consumer except
vault-core, the one designated seeder (WP TH-1-FIX).

Bind mode (VAULT_CACHE_PATH set to an absolute host path): there is no
copy-up for a bind, and the option must be ABSENT. Docker 28 / Compose 2.38
hand the short-syntax string to the daemon, which refuses
"/path:/vault:rw,nocopy" with 'invalid mount config for type "bind": field
VolumeOptions must not be specified' (WP DEPLOY-FIX-3, found by a real
rollout; verify-stack.sh only ever ran the named volume).

The mount lines are rendered here with a minimal re-implementation of the
Compose interpolation subset they use (`${VAR}`, `${VAR:-default}`,
`${VAR:+replacement}`, no nesting), then split as short syntax. The Docker-
rendered counterpart is verify-stack.sh step 3p, the live one section 9.

Parsed as text (no YAML dependency in the api venv): each service block is
cut at the next two-space-indented sibling key, and only real sequence items
under the service's own `volumes:` key count, never comments that mention
the mount in prose.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

COMPOSE_PATH = Path(__file__).resolve().parents[2] / "deploy" / "compose.yaml"

_ITEM_RE = re.compile(r"^      -[ \t]*(?P<item>\S+)[ \t]*$")
_VAR_RE = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?:(?P<op>:-|:\+)(?P<arg>[^${}]*))?\}")

BIND_PATH = "/srv/steamhangar-cache"
MODES = {
    "unset": {},
    "blank": {"VAULT_CACHE_PATH": ""},
    "bind": {"VAULT_CACHE_PATH": BIND_PATH},
}


def _interpolate(text: str, env: dict[str, str]) -> str:
    """Compose's `:-`/`:+` semantics: unset and empty are treated alike."""

    def sub(m: re.Match[str]) -> str:
        value = env.get(m.group("name"), "")
        op, arg = m.group("op"), m.group("arg") or ""
        if op == ":-":
            return value or arg
        if op == ":+":
            return arg if value else ""
        return value

    out = _VAR_RE.sub(sub, text)
    # Anything left is an expression outside the subset above (nesting, `?`,
    # bare `-`/`+`). Refuse rather than guess: the mount line must stay in the
    # subset every Compose version parses the same way.
    assert "${" not in out, f"unsupported interpolation in {text!r}"
    return out


def _service_block(text: str, service: str) -> str:
    match = re.search(rf"^  {re.escape(service)}:[ \t]*$", text, re.MULTILINE)
    assert match, f"service {service!r} not found in deploy/compose.yaml"
    rest = text[match.end():]
    end = re.search(r"^(?:  )?[^\s#]", rest, re.MULTILINE)
    return rest[: end.start()] if end else rest


def _volume_items(text: str, service: str) -> list[str]:
    items: list[str] = []
    in_volumes = False
    for line in _service_block(text, service).splitlines():
        if re.match(r"^    volumes:[ \t]*$", line):
            in_volumes = True
            continue
        if in_volumes and re.match(r"^ {0,4}[^\s#]", line):
            in_volumes = False
        if in_volumes:
            m = _ITEM_RE.match(line)
            if m:
                items.append(m.group("item"))
    return items


def _vault_mounts(
    text: str, service: str, env: dict[str, str]
) -> list[tuple[str, set[str]]]:
    """(source, options) of every short-syntax mount whose target is /vault."""
    mounts: list[tuple[str, set[str]]] = []
    for item in _volume_items(text, service):
        rendered = _interpolate(item, env)
        parts = rendered.split(":")
        if len(parts) >= 2 and parts[1] == "/vault":
            opts = set(filter(None, ",".join(parts[2:]).split(",")))
            mounts.append((parts[0], opts))
    return mounts


@pytest.fixture(scope="module")
def compose_text() -> str:
    return COMPOSE_PATH.read_text(encoding="utf-8")


def test_interpolate_matches_compose_semantics() -> None:
    assert _interpolate("${A:-d}", {}) == "d"
    assert _interpolate("${A:-d}", {"A": ""}) == "d"
    assert _interpolate("${A:-d}", {"A": "v"}) == "v"
    assert _interpolate("${A:+r}", {}) == ""
    assert _interpolate("${A:+r}", {"A": ""}) == ""
    assert _interpolate("${A:+r}", {"A": "v"}) == "r"
    with pytest.raises(AssertionError):
        _interpolate("${A:-${B}}", {})


@pytest.mark.parametrize("mode", ["unset", "blank"])
def test_vault_core_is_the_only_seeder(compose_text: str, mode: str) -> None:
    mounts = _vault_mounts(compose_text, "vault-core", MODES[mode])
    assert mounts == [("vault-cache", set())], (
        f"vault-core must mount the vault-cache volume at /vault exactly once "
        f"WITHOUT nocopy (it seeds the fresh volume with layout and 101:101 "
        f"ownership); got {mounts!r} with VAULT_CACHE_PATH {mode}"
    )


@pytest.mark.parametrize("mode", ["unset", "blank"])
def test_vault_api_named_volume_keeps_nocopy(compose_text: str, mode: str) -> None:
    mounts = _vault_mounts(compose_text, "vault-api", MODES[mode])
    assert mounts == [("vault-cache", {"nocopy"})], (
        f"vault-api must mount the same volume with nocopy, or the first-start "
        f"copy-up race with vault-core returns; got {mounts!r} with "
        f"VAULT_CACHE_PATH {mode}"
    )


def test_vault_api_named_volume_line_is_unchanged(compose_text: str) -> None:
    # The rendered string for every existing deployment is byte-identical to
    # the pre-DEPLOY-FIX-3 line `vault-cache:/vault:nocopy`.
    rendered = [
        _interpolate(item, {})
        for item in _volume_items(compose_text, "vault-api")
        if ":/vault" in _interpolate(item, {})
    ]
    assert rendered == ["vault-cache:/vault:nocopy"], rendered


@pytest.mark.parametrize("service", ["vault-core", "vault-api"])
def test_bind_mode_mount_carries_no_volume_options(compose_text: str, service: str) -> None:
    mounts = _vault_mounts(compose_text, service, MODES["bind"])
    assert mounts == [(BIND_PATH, set())], (
        f"{service} must bind VAULT_CACHE_PATH at /vault with NO options: the "
        f"Docker 28 daemon refuses nocopy (VolumeOptions) on a bind; got "
        f"{mounts!r}"
    )


@pytest.mark.parametrize("mode", list(MODES))
def test_both_services_resolve_the_same_source(compose_text: str, mode: str) -> None:
    core = [src for src, _ in _vault_mounts(compose_text, "vault-core", MODES[mode])]
    api = [src for src, _ in _vault_mounts(compose_text, "vault-api", MODES[mode])]
    assert core == api and len(core) == 1, (core, api)


@pytest.mark.parametrize("service", ["vault-runner", "vault-proxy", "vault-dns"])
def test_other_services_never_seed_or_break_the_cache(compose_text: str, service: str) -> None:
    for mode, env in MODES.items():
        for _, opts in _vault_mounts(compose_text, service, env):
            if mode == "bind":
                assert not opts, f"{service} carries {opts} on the /vault bind"
            else:
                assert "nocopy" in opts, (
                    f"{service} mounts /vault without nocopy -- any second "
                    f"seeder reopens the copy-up race"
                )


def test_block_cut_ignores_comment_mentions(compose_text: str) -> None:
    # vault-runner's volumes: comment names the /vault mount in prose; it
    # must not be read as a mount.
    for env in MODES.values():
        assert _vault_mounts(compose_text, "vault-runner", env) == []
    assert "/vault" in _service_block(compose_text, "vault-runner")
