"""vault-api — the FastAPI backend for SteamHangar (depot mapping, prefill orchestration, cache control)."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

#: The release line this source tree belongs to (WP 4e.7). Hand-maintained,
#: and since WP VER-1 only the FALLBACK for what the server reports: the
#: published images carry the real release version as the build-time env
#: ``VAULT_BUILD_VERSION`` (set from the ``VAULT_VERSION`` build arg by
#: ``.github/workflows/publish.yml``; locally built images carry ``dev``).
#: This value is used only where no such env exists, i.e. a native run from
#: a checkout and the test suite. It stays pinned against
#: ``deploy/compose.yaml``'s ``${VAULT_IMAGE_TAG:-0.1.0}`` defaults by
#: ``api/tests/test_version_pin.py``, which lists every remaining copy.
BASE_VERSION = "0.1.0"

#: Runtime env names baked by every SteamHangar Dockerfile (WP VER-1). The
#: build args are ``VAULT_VERSION``/``VAULT_COMMIT``; the ENV gets a
#: ``BUILD`` infix so it cannot be mistaken for a setting an operator tunes
#: (it is a fact about the image) or for compose's ``VAULT_IMAGE_TAG`` (which
#: image to pull, not what is inside it).
BUILD_VERSION_ENV = "VAULT_BUILD_VERSION"
BUILD_COMMIT_ENV = "VAULT_BUILD_COMMIT"

#: The Dockerfiles' default for ``VAULT_COMMIT``, and this module's fallback.
UNKNOWN_COMMIT = "unknown"

#: Same grammar as publish.yml's and ci.yml's version step: starts with a
#: letter or digit, then letters, digits and ``. _ + -``, at most 64
#: characters. Covers ``0.1.0``, ``0.1.0-rc8``, ``dev``, ``dev-1a2b3c4`` and
#: ``ci-1a2b3c4``; refuses whitespace, quotes, slashes and anything else that
#: would need escaping wherever the string is shown. ``fullmatch`` with
#: ``re.ASCII`` so ``\n`` at the end and non-ASCII digits are refused too.
_VERSION_GRAMMAR = re.compile(r"[0-9A-Za-z][0-9A-Za-z._+-]{0,63}", re.ASCII)
#: A git commit id: 7 to 40 lowercase hex characters (short or full SHA-1).
_COMMIT_GRAMMAR = re.compile(r"[0-9a-f]{7,40}", re.ASCII)


@dataclass(frozen=True)
class BuildInfo:
    """What this process reports about the code it runs.

    ``version``: ``VAULT_BUILD_VERSION`` when it is set and valid, else
    :data:`BASE_VERSION`. ``commit``: ``VAULT_BUILD_COMMIT`` when it is a
    commit id, else ``"unknown"``. ``rejected``: env names that were present
    but unusable (blank or outside the grammar), for the startup warning.
    """

    version: str
    commit: str
    rejected: tuple[str, ...] = ()


def build_info(environ: Mapping[str, str] | None = None) -> BuildInfo:
    """Resolve the build identity from ``environ`` (default ``os.environ``).

    Never raises: the version is informational, so an unusable value falls
    back (and is named in ``rejected``) instead of refusing to boot. An
    absent key is simply not baked (native run) and is not "rejected"; a
    present-but-blank key is (docs/LEARNINGS.md: ``.get(key, default)``
    does not cover blank). The literal Dockerfile default ``unknown`` for the
    commit is accepted as "no commit known", not rejected.
    """
    env = os.environ if environ is None else environ
    rejected: list[str] = []

    version = BASE_VERSION
    raw_version = env.get(BUILD_VERSION_ENV)
    if raw_version is not None:
        if _VERSION_GRAMMAR.fullmatch(raw_version):
            version = raw_version
        else:
            rejected.append(BUILD_VERSION_ENV)

    commit = UNKNOWN_COMMIT
    raw_commit = env.get(BUILD_COMMIT_ENV)
    if raw_commit is not None and raw_commit != UNKNOWN_COMMIT:
        if _COMMIT_GRAMMAR.fullmatch(raw_commit):
            commit = raw_commit
        else:
            rejected.append(BUILD_COMMIT_ENV)

    return BuildInfo(version=version, commit=commit, rejected=tuple(rejected))


_IMPORT_BUILD = build_info()

#: What this process reports as its version (``GET /v1/settings``'s
#: ``server_version``, ``FastAPI(version=...)``), resolved once at import.
#: ``create_app`` resolves :func:`build_info` again so a test can set the env
#: before building an app; in a container the env never changes, so both
#: resolve the same value.
__version__ = _IMPORT_BUILD.version
#: The commit this image was built from, or ``"unknown"``. Not served by any
#: route yet (WP VER-2 adds ``GET /v1/about``); logged once at startup.
__commit__ = _IMPORT_BUILD.commit
