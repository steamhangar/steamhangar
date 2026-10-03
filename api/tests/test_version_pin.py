"""WP 4e.7 anti-drift guard: ``vault_api.BASE_VERSION`` vs. every other
hand-maintained copy of the same release number this package's footprint
(``api/``, plus ``deploy/`` only where the pin requires it) can reach.

**WP VER-1 update.** The number the server REPORTS is no longer
hand-maintained: every image bakes ``VAULT_BUILD_VERSION`` from the
``VAULT_VERSION`` build arg (publish.yml passes the tag without its ``v``),
and ``vault_api.__version__`` resolves from it at runtime. What remains
hand-maintained is the BASE: ``vault_api.BASE_VERSION`` (the fallback for
a native run) and the compose/verify-stack/.env.example image-tag defaults,
which this module still pins against each other. The four Dockerfiles'
``org.opencontainers.image.version`` LABELs are now ``${VAULT_VERSION}``,
so they left this table; ``api/tests/test_ver_1_build_version.py`` pins
their ARG/ENV/LABEL wiring instead (all four, closing the old core/dns gap).

Same failure class docs/LEARNINGS.md already names for env-var defaults
(``test_p1_compose_env_defaults.py``): two hand-maintained copies of the same
fact, checked in two different files, will disagree eventually unless
something compares them on every commit. Here the "same fact" is one level
up from an env var — it is the released image tag / OCI version — but the
guard shape is identical: derive every side from its real source (never
hand-copy any of them into this file), parse the real files directly (no
Docker, no network, plain ``pytest``, runs in CI on every commit), and fail
BY NAME in either drift direction for each site.

**Every hand-maintained "0.1.0" this project has, and which ones this file
pins** (review round 1, blocker B1: an earlier version of this file covered
only ``deploy/compose.yaml`` and both this module's own docstring and
``api/README.md`` wrongly implied that was the complete set; table updated
by WP VER-1):

| Site | Pinned here? | Why |
|---|---|---|
| ``api/vault_api/__init__.py``'s ``BASE_VERSION`` | n/a (the source of truth) | Everything else is compared AGAINST this |
| ``deploy/compose.yaml`` (5x ``image:`` lines, one per service — WP S-2 added ``vault-runner``'s, WP EG-1 added ``vault-proxy``'s; all ``ghcr.io/steamhangar/...`` since the pre-freeze review's S1 fix) | YES | in this pin's footprint, checked at startup-config time |
| ``deploy/tests/verify-stack.sh``'s ``TAG=${VAULT_IMAGE_TAG:-...}`` | YES | in ``deploy/``, same conceptual value as compose's default |
| ``deploy/.env.example``'s ``#VAULT_IMAGE_TAG=...`` example line | YES | in ``deploy/``, same conceptual value as compose's default |
| the four Dockerfiles' ``org.opencontainers.image.version`` LABELs | no longer a literal (WP VER-1) | ``${VAULT_VERSION}``, pinned by ``test_ver_1_build_version.py`` |

Mutation-verified in both directions for every **YES** row above (bump
``BASE_VERSION`` alone; bump each site's own value alone) — each failure is
a DIFFERENT named test below, so a reader immediately knows which side of
which comparison drifted.

**Review round 2, WP S-2 blocker B1.** ``deploy/compose.yaml`` gained a
FOURTH ``image:`` line (``vault-runner``, ADR-0012's runner split) that
deliberately reuses the exact same image name as ``vault-api``
(``ghcr.io/steamhangar/vault-api`` — same codebase, same tag, different
``command:``, no separate build). The pre-fix version of this file's
extraction keyed its result dict by IMAGE NAME (the ``<name>`` segment of
the image reference), not
by which COMPOSE SERVICE the line lives under — so with two lines both
naming the image ``vault-api``, a plain ``{image_name: default}`` dict
comprehension let vault-runner's occurrence silently overwrite vault-api's
own, making vault-api's line invisible to every comparison below it.
Measured: mutating ONLY line 133 (vault-api's own image: default) to
``0.2.0`` left the full 1743-test suite green — the two tests that should
have caught it never saw that line at all. The fix keys extraction by
SERVICE NAME instead (``_compose_service_image_tags``), so vault-api's and
vault-runner's lines are two independent dict entries; each is now
mutation-verified alone (see ``test_vault_api_version_matches_compose_image_tag_default``
and ``test_vault_runner_image_tag_default_matches_vault_api_version``).
"""

from __future__ import annotations

import re
from pathlib import Path

from vault_api import BASE_VERSION as VAULT_API_VERSION
from vault_api import BUILD_VERSION_ENV, build_info
from vault_api.config import Settings
from vault_api.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = REPO_ROOT / "deploy" / "compose.yaml"
VERIFY_STACK_PATH = REPO_ROOT / "deploy" / "tests" / "verify-stack.sh"
ENV_EXAMPLE_PATH = REPO_ROOT / "deploy" / ".env.example"

#: Matches `image: ghcr.io/steamhangar/<name>:${VAULT_IMAGE_TAG:-<default>}`
#: lines exactly as they appear in deploy/compose.yaml today -- deliberately
#: narrow, like test_p1_compose_env_defaults.py's `_parsed_env_defaults`
#: regex, so a line that does not match this exact `${VAR:-default}` shape
#: is correctly absent rather than misparsed. Applied to ONE SERVICE's
#: block text at a time (see `_service_blocks`/`_compose_service_image_tags`
#: below) -- never to the whole file at once, which is the B1 fix: the
#: captured group here is the IMAGE NAME, which is NOT unique per service
#: since WP S-2 (vault-runner intentionally reuses `vault-api`'s image).
#:
#: The registry host is part of the pin (pre-freeze project review, finding
#: S1): compose used to name the bare Docker Hub namespace
#: `steamhangar/vault-*`, which nobody publishes to, while
#: .github/workflows/publish.yml pushes `ghcr.io/steamhangar/vault-*` -- so
#: `docker compose pull` could never have found the published images. The
#: literal `ghcr.io/steamhangar/` prefix here is what makes that parity a
#: tested fact rather than a comment in publish.yml.
_IMAGE_TAG_PATTERN = re.compile(
    r"^\s*image:\s*ghcr\.io/steamhangar/([a-z-]+):\$\{VAULT_IMAGE_TAG:-([^}]+)\}\s*$",
    re.MULTILINE,
)

#: A top-level compose SERVICE key -- exactly 2-space indented, e.g.
#: "  vault-api:". Same indentation contract
#: test_p1_compose_env_defaults.py's `_extract_service_environment_block`
#: already relies on for this same file.
_SERVICE_HEADER_PATTERN = re.compile(r"^  ([a-z][a-z0-9-]*):\s*$")


def _service_blocks(text: str) -> dict[str, str]:
    """Split the WHOLE compose.yaml file into per-2-space-indented-key
    blocks, keyed by that key's name, each block spanning from the key's own
    header line (exclusive) to the next such header or the next top-level
    (0-indent) key (exclusive).

    Round 3 docstring correction: this does NOT special-case the `services:`
    section boundary -- it walks the entire file by indentation alone, so
    the top-level `volumes:` block's own 2-space-indented children
    (`vault-cache:`, `vault-db:`, ...) get collected as entries too, exactly
    like `vault-api:`/`vault-runner:` do. Harmless for every caller in this
    file (those entries are always empty -- a bare `  vault-cache:` line has
    no body, so no `image:` line is ever found inside one), but "splits
    compose.yaml's `services:` section" (the pre-round-3 wording) overstated
    what this function actually knows: it has no concept of `services:` as
    a boundary, only of indentation.

    Generalized (review round 2, B1 fix) from
    test_p1_compose_env_defaults.py's `_extract_service_environment_block`,
    which isolates only a service's `environment:` sub-block; this isolates
    the WHOLE body under each collected key (`image:`, `environment:`,
    `volumes:`, ...) because the B1 fix needs the `image:` line specifically,
    which lives above `environment:`, not inside it.
    """
    blocks: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        header = _SERVICE_HEADER_PATTERN.match(line)
        if header:
            current = header.group(1)
            blocks[current] = []
            continue
        if re.match(r"^[A-Za-z]", line):
            # A top-level key (e.g. `volumes:` following the last service,
            # or `name:`/`x-vault-logging:` before `services:` even starts)
            # ends whatever service scope was open, if any.
            current = None
            continue
        if current is not None:
            blocks[current].append(line)
    return {name: "\n".join(body) for name, body in blocks.items()}


def _compose_service_image_tags() -> dict[str, tuple[str, str]]:
    """{service_name: (image_name, default_tag)} — one entry per COMPOSE
    SERVICE with an `image: steamhangar/<name>:${VAULT_IMAGE_TAG:-<default>}`
    line, keyed by the SERVICE the line lives under (never by the image name
    in its value — see the B1 fix note in this module's docstring for why
    that distinction is the whole point: vault-runner intentionally reuses
    vault-api's exact image name, so keying by image name collapses two
    independent hand-typed lines into one dict entry, silently hiding
    whichever one is not last).
    """
    text = COMPOSE_PATH.read_text(encoding="utf-8")
    result: dict[str, tuple[str, str]] = {}
    for service, body in _service_blocks(text).items():
        match = _IMAGE_TAG_PATTERN.search(body)
        if match:
            result[service] = (match.group(1), match.group(2))
    return result

#: verify-stack.sh's `TAG=` line is bash, not YAML, but the same
#: `${VAR:-default}` shape compose.yaml uses -- one pattern, two files.
_VERIFY_STACK_TAG_PATTERN = re.compile(
    r"^\s*TAG=\$\{VAULT_IMAGE_TAG:-([^}]+)\}\s*$", re.MULTILINE
)

#: The commented-out example line in .env.example, e.g. `#VAULT_IMAGE_TAG=0.1.0`.
_ENV_EXAMPLE_TAG_PATTERN = re.compile(r"^#VAULT_IMAGE_TAG=(.+)$", re.MULTILINE)


def _verify_stack_tag_default() -> str | None:
    text = VERIFY_STACK_PATH.read_text(encoding="utf-8")
    match = _VERIFY_STACK_TAG_PATTERN.search(text)
    return match.group(1) if match else None


def _env_example_tag_default() -> str | None:
    text = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
    match = _ENV_EXAMPLE_TAG_PATTERN.search(text)
    return match.group(1) if match else None


# ==========================================================================
# Preconditions -- same discipline as
# test_p1_compose_env_defaults.test_every_expected_key_is_present_in_compose:
# if an extraction regex and a file's actual layout have drifted apart (or a
# line lost its VAULT_IMAGE_TAG fallback / LABEL entirely), THESE fail, not a
# same-looking value mismatch below -- and not a silent None/empty-dict pass
# in the comparison tests that follow.
# ==========================================================================


def test_compose_has_the_expected_image_tag_lines() -> None:
    """WP S-2 (ADR-0012, review round 2 B1 fix): `vault-runner` joins the
    expected set. Its `image:` line is a real, independent hand-typed
    occurrence of the exact same string as vault-api's — reusing the same
    image name is WHY the B1 blocker existed (see this module's docstring).

    Also the registry-parity pin (finding S1, see `_IMAGE_TAG_PATTERN`): a
    line that drops or changes the `ghcr.io/steamhangar/` prefix simply
    stops matching, so its service goes missing from `found` and fails
    here BY NAME -- mutation-verified by reverting one line to the old bare
    `steamhangar/vault-core` shape.
    """
    found = _compose_service_image_tags()
    assert set(found) == {
        "vault-core",
        "vault-api",
        "vault-runner",
        "vault-proxy",
        "vault-dns",
    }, (
        f"deploy/compose.yaml: expected exactly the vault-core/vault-api/"
        f"vault-runner/vault-proxy/vault-dns image lines to carry a "
        f"ghcr.io/steamhangar/<image>:${{VAULT_IMAGE_TAG:-...}} reference, "
        f"found {sorted(found)} -- either the extraction regex in this test "
        "file and compose.yaml's actual layout have diverged, or an image: "
        "line no longer points at the ghcr.io/steamhangar namespace "
        "publish.yml pushes to (finding S1)."
    )


def test_vault_runner_uses_the_vault_api_image_name() -> None:
    """WP S-2 (ADR-0012 §S2): vault-runner deliberately reuses vault-api's
    exact image (same codebase, `command:` override only, no separate
    `build:` block) rather than building its own. Pinned by name so a future
    edit that accidentally gives it its own image (defeating the "one image,
    two entrypoints" design) is caught here, not silently.
    """
    tags = _compose_service_image_tags()
    image_name, _default = tags.get("vault-runner", (None, None))
    assert image_name == "vault-api", (
        f"deploy/compose.yaml's vault-runner service has image name "
        f"{image_name!r}, expected 'vault-api' -- WP S-2 (ADR-0012) "
        "deliberately reuses vault-api's image rather than a separate build."
    )


def test_all_compose_image_tag_default_occurrences_agree() -> None:
    """vault-core, vault-api, vault-runner and vault-dns share ONE conceptual
    release number -- a hand-edit that bumped only one of the four `image:`
    lines (typo, partial find-and-replace) must fail here, distinctly from
    the `BASE_VERSION`-vs-compose comparisons below, so a reader immediately
    knows which of the two possible drifts happened.
    """
    tags = _compose_service_image_tags()
    distinct = {default for _image, default in tags.values()}
    assert len(distinct) == 1, (
        f"deploy/compose.yaml's four image: lines disagree on the "
        f"VAULT_IMAGE_TAG default: {tags} -- they must all carry the "
        "same release number."
    )


def test_verify_stack_has_a_vault_image_tag_default() -> None:
    assert _verify_stack_tag_default() is not None, (
        "deploy/tests/verify-stack.sh: no TAG=${VAULT_IMAGE_TAG:-...} line "
        "found -- the extraction regex in this test file and the script's "
        "actual layout have diverged, or the line was removed/reshaped."
    )


def test_env_example_has_a_vault_image_tag_default() -> None:
    assert _env_example_tag_default() is not None, (
        "deploy/.env.example: no #VAULT_IMAGE_TAG=... example line found -- "
        "the extraction regex in this test file and the file's actual "
        "layout have diverged, or the line was removed/reshaped."
    )


# ==========================================================================
# The pins: vault_api.BASE_VERSION vs. each site, one named test per site so
# a drift direction is identifiable from the test name alone.
# ==========================================================================


def test_vault_api_version_matches_compose_image_tag_default() -> None:
    """Mutation-verified in both directions (see this file's module
    docstring) -- bumping either side alone, without the other, fails this
    exact assertion. Reads ONLY vault-api's own service block (B1 fix,
    review round 2) -- before the fix, this looked up the value in a dict
    keyed by IMAGE NAME, and vault-runner's line (added by WP S-2, same
    image name, parsed LATER in file order) silently overwrote vault-api's
    own entry there, making a solo mutation of vault-api's line invisible
    to this test. Measured: reverting to that shape and mutating ONLY this
    service's default left the full suite green.
    """
    compose_default = _compose_service_image_tags().get("vault-api", (None, None))[1]
    assert compose_default == VAULT_API_VERSION, (
        f"deploy/compose.yaml's vault-api service's VAULT_IMAGE_TAG default "
        f"is {compose_default!r}, but vault_api.BASE_VERSION is "
        f"{VAULT_API_VERSION!r} -- one of the two changed without the "
        "other. Bump both together (api/vault_api/__init__.py's BASE_VERSION AND every "
        "image: line in deploy/compose.yaml)."
    )


def test_vault_runner_image_tag_default_matches_vault_api_version() -> None:
    """WP S-2 (ADR-0012), review round 2 B1 fix. vault-runner's `image:`
    line is a SEPARATE hand-typed occurrence of the exact same string as
    vault-api's — there is no shared YAML anchor tying the two together —
    so it must be checked independently, exactly like
    `test_vault_api_version_matches_compose_image_tag_default` above.
    Before the fix this service's default was not checked against
    `vault_api.__version__` at all (the old dict, keyed by image name,
    could only ever expose ONE of the two "vault-api"-named entries).
    """
    runner_default = _compose_service_image_tags().get("vault-runner", (None, None))[1]
    assert runner_default == VAULT_API_VERSION, (
        f"deploy/compose.yaml's vault-runner service's VAULT_IMAGE_TAG "
        f"default is {runner_default!r}, but vault_api.BASE_VERSION is "
        f"{VAULT_API_VERSION!r} -- one of the two changed without the "
        "other. Bump both together (api/vault_api/__init__.py's BASE_VERSION AND every "
        "image: line in deploy/compose.yaml, including vault-runner's)."
    )


def test_vault_proxy_image_tag_default_matches_vault_api_version() -> None:
    """WP EG-1 (ADR-0011). `vault-proxy` gets its OWN image (a real `build:`
    block, `deploy/proxy/Dockerfile` — unlike `vault-runner`, it does not
    reuse `vault-api`'s image), but it shares the exact same
    `${VAULT_IMAGE_TAG:-...}` variable and default — this project's stack
    ships one conceptual release number across every image it builds, not
    one per component. Same independent-occurrence reasoning as
    `test_vault_runner_image_tag_default_matches_vault_api_version` above:
    there is no YAML anchor tying this line to any other, so a bump that
    misses it must be caught here specifically.
    """
    proxy_default = _compose_service_image_tags().get("vault-proxy", (None, None))[1]
    assert proxy_default == VAULT_API_VERSION, (
        f"deploy/compose.yaml's vault-proxy service's VAULT_IMAGE_TAG "
        f"default is {proxy_default!r}, but vault_api.BASE_VERSION is "
        f"{VAULT_API_VERSION!r} -- one of the two changed without the "
        "other. Bump both together (api/vault_api/__init__.py's BASE_VERSION AND every "
        "image: line in deploy/compose.yaml, including vault-proxy's)."
    )


def test_vault_api_version_matches_verify_stack_tag_default() -> None:
    tag_default = _verify_stack_tag_default()
    assert tag_default == VAULT_API_VERSION, (
        f"deploy/tests/verify-stack.sh's VAULT_IMAGE_TAG default is "
        f"{tag_default!r}, but vault_api.BASE_VERSION is "
        f"{VAULT_API_VERSION!r} -- one of the two changed without the "
        "other. Bump both together."
    )


def test_vault_api_version_matches_env_example_tag_default() -> None:
    tag_default = _env_example_tag_default()
    assert tag_default == VAULT_API_VERSION, (
        f"deploy/.env.example's VAULT_IMAGE_TAG example default is "
        f"{tag_default!r}, but vault_api.BASE_VERSION is "
        f"{VAULT_API_VERSION!r} -- one of the two changed without the "
        "other. Bump both together."
    )


# ==========================================================================
# Wiring: main.py's `FastAPI(version=...)` uses the resolved build version,
# not a copy of it (review round 1, should-fix S1; WP VER-1).
# ==========================================================================


def test_create_app_uses_vault_api_version_as_its_fastapi_version(
    tmp_path: Path, monkeypatch
) -> None:
    """Before this test existed, `version=VAULT_API_VERSION` in `main.py`
    was enforced by a comment only -- hand-changing it to a literal
    `version="9.9.9"` survived the entire 1610-test suite. The blast radius
    of that particular drift is bounded (``openapi_url=None`` disables the
    only route that would ever echo ``app.version`` over HTTP -- confirmed
    empirically: ``GET /openapi.json`` answers ``404``, not the schema), but
    "bounded impact" is not the same as "tested", so this pins the object
    directly rather than relying on an HTTP round trip that cannot see it.
    """
    settings = Settings(
        vault_api_key="test-key-for-version-pin",
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
    )
    # WP VER-1: without the baked env the app reports BASE_VERSION; with it,
    # the env value -- in both cases the same object GET /v1/settings uses.
    monkeypatch.delenv(BUILD_VERSION_ENV, raising=False)
    app = create_app(settings)
    assert app.version == VAULT_API_VERSION
    assert app.state.build_info == build_info({})

    monkeypatch.setenv(BUILD_VERSION_ENV, "0.1.0-rc99")
    app = create_app(settings)
    assert app.version == "0.1.0-rc99"
    assert app.state.build_info.version == "0.1.0-rc99"
