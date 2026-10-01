"""Static pin for the first-start copy-up race on the shared /vault volume.

Both the vault-core and the vault-api image contain /vault/cache/depot and
/vault/tmp. Docker copies image content into an EMPTY named volume when a
container is created with it, and Compose creates both services
concurrently, so on a fresh volume both copied at once and one create failed
with "mkdir .../_data/tmp: file exists" (live `verify-stack.sh` run). The fix
(deploy/compose.yaml, vault-api's /vault mount) is `:nocopy` on every
consumer except vault-core, the one designated seeder.

Parsed as text (no YAML dependency in the api venv): each service block is
cut at the next two-space-indented sibling key, and only real sequence items
count, never comments that mention the mount in prose.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

COMPOSE_PATH = Path(__file__).resolve().parents[2] / "deploy" / "compose.yaml"

_MOUNT_RE = re.compile(r"^[ \t]*-[ \t]*(?P<source>\S+):/vault(?::(?P<opts>\S+))?[ \t]*$")


def _service_block(text: str, service: str) -> str:
    match = re.search(rf"^  {re.escape(service)}:[ \t]*$", text, re.MULTILINE)
    assert match, f"service {service!r} not found in deploy/compose.yaml"
    rest = text[match.end():]
    end = re.search(r"^(?:  )?[^\s#]", rest, re.MULTILINE)
    return rest[: end.start()] if end else rest


def _vault_mounts(text: str, service: str) -> list[tuple[str, set[str]]]:
    mounts: list[tuple[str, set[str]]] = []
    for line in _service_block(text, service).splitlines():
        m = _MOUNT_RE.match(line)
        if m:
            opts = set(filter(None, (m.group("opts") or "").split(",")))
            mounts.append((m.group("source"), opts))
    return mounts


@pytest.fixture(scope="module")
def compose_text() -> str:
    return COMPOSE_PATH.read_text(encoding="utf-8")


def test_vault_core_is_the_only_seeder(compose_text: str) -> None:
    mounts = _vault_mounts(compose_text, "vault-core")
    assert mounts == [("${VAULT_CACHE_PATH:-vault-cache}", set())], (
        f"vault-core must mount /vault exactly once WITHOUT nocopy (it seeds "
        f"the fresh volume with layout and 101:101 ownership); got {mounts!r}"
    )


def test_vault_api_mount_carries_nocopy(compose_text: str) -> None:
    mounts = _vault_mounts(compose_text, "vault-api")
    assert mounts == [("${VAULT_CACHE_PATH:-vault-cache}", {"nocopy"})], (
        f"vault-api must mount the same /vault source with :nocopy, or the "
        f"first-start copy-up race with vault-core returns; got {mounts!r}"
    )


@pytest.mark.parametrize("service", ["vault-runner", "vault-proxy", "vault-dns"])
def test_other_services_never_seed_the_cache(compose_text: str, service: str) -> None:
    mounts = _vault_mounts(compose_text, service)
    assert all("nocopy" in opts for _, opts in mounts), (
        f"{service} mounts /vault without :nocopy -- any second seeder "
        f"reopens the copy-up race; got {mounts!r}"
    )


def test_block_cut_ignores_comment_mentions(compose_text: str) -> None:
    # vault-runner's volumes: comment names the /vault mount in prose; it
    # must not be read as a mount.
    assert _vault_mounts(compose_text, "vault-runner") == []
