"""Bundled client versions and minimums (WP RELEASE-SPLIT-1b, ADR-0031).

Since ADR-0031 every component (server, agent, app) carries the product
version in which it last changed. The vault-api image bakes the agent files
and the APK of their recorded versions; publish.yml passes the facts about
them as build args, which ``api/Dockerfile`` turns into these env names:

=================================  ===========================================
``VAULT_CLIENT_<C>_VERSION``       the component version (the release in
                                   which the client last changed)
``VAULT_CLIENT_<C>_BUILD``         the version stamped on the baked files
                                   (the new release's version when the client
                                   was rebuilt, the recorded one when reused)
``VAULT_CLIENT_<C>_MINIMUM``       the oldest client this server supports
=================================  ===========================================

with ``<C>`` = ``AGENT`` or ``APP``. ``GET /v1/dist`` and ``GET /v1/settings``
state them as ``clients`` (one builder, :func:`client_versions_out`); the
Clients page compares an agent's reported version with ``same_build`` and
``minimum`` instead of the server version (web ``clients-page.js``, Android
``ClientCard.kt``).

``same_build`` lists every version string a client of exactly the bundled
build may report: the component version, the build stamp, and for the
agent the :data:`AGENT_BUILD_ALIASES` that name the component version. A
client outside it differs from the bundled build; below ``minimum`` it
needs an update.

A local build sets none of them (empty build args): every client is
``None`` and the frontends fall back to comparing with the server version.
A value outside the release grammar is treated as not set, never shown.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

#: Same grammar as ``vault_api.dist`` and web ``isReleaseVersion``.
_RELEASE_VERSION = re.compile(
    r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*)?", re.ASCII
)

CLIENTS = ("agent", "app")

#: Agent builds that are the 0.1.0-rc17 source with another version stamp
#: (ADR-0031 section 7, checked with ``component-versions.py history``:
#: the agent paths did not change from v0.1.0-rc17 to v0.1.0-rc21.1). Only
#: tags that exist; a version maps to the component version it is a build
#: of. Never extend this for a build whose source changed.
AGENT_BUILD_ALIASES: Mapping[str, str] = {
    "0.1.0-rc18": "0.1.0-rc17",
    "0.1.0-rc18.1": "0.1.0-rc17",
    "0.1.0-rc19": "0.1.0-rc17",
    "0.1.0-rc19.2": "0.1.0-rc17",
    "0.1.0-rc19.3": "0.1.0-rc17",
    "0.1.0-rc20": "0.1.0-rc17",
    "0.1.0-rc20.1": "0.1.0-rc17",
    "0.1.0-rc20.2": "0.1.0-rc17",
    "0.1.0-rc21": "0.1.0-rc17",
    "0.1.0-rc21.1": "0.1.0-rc17",
}


def env_name(client: str, field: str) -> str:
    return f"VAULT_CLIENT_{client.upper()}_{field.upper()}"


def is_release(value: str | None) -> bool:
    return isinstance(value, str) and _RELEASE_VERSION.fullmatch(value) is not None


@dataclass(frozen=True)
class ClientVersion:
    """One bundled client as the server states it."""

    #: Component version: the release in which the client last changed.
    version: str
    #: The version stamped on the bundled files (their names, ``--version``).
    build: str
    #: The oldest client version the server supports, or None.
    minimum: str | None
    #: Every version a client of exactly the bundled build may report.
    same_build: tuple[str, ...]


def _same_build(client: str, version: str, build: str) -> tuple[str, ...]:
    same = {version, build}
    if client == "agent":
        same.update(k for k, v in AGENT_BUILD_ALIASES.items() if v == version)
    return tuple(sorted(same))


def client_version(client: str, environ: Mapping[str, str] | None = None) -> ClientVersion | None:
    """The bundled ``client`` (``agent``/``app``), or None when the image
    states none (local build) or its component version is unusable."""
    env = os.environ if environ is None else environ
    version = env.get(env_name(client, "version"), "").strip()
    if not is_release(version):
        return None
    build = env.get(env_name(client, "build"), "").strip()
    if not is_release(build):
        build = version
    minimum: str | None = env.get(env_name(client, "minimum"), "").strip()
    if not is_release(minimum):
        minimum = None
    return ClientVersion(version, build, minimum, _same_build(client, version, build))


def client_versions(environ: Mapping[str, str] | None = None) -> dict[str, ClientVersion | None]:
    return {c: client_version(c, environ) for c in CLIENTS}


def client_versions_out(clients: Mapping[str, ClientVersion | None]) -> dict[str, dict | None]:
    """The JSON shape of ``clients`` in ``GET /v1/dist`` and
    ``GET /v1/settings``."""
    out: dict[str, dict | None] = {}
    for c in CLIENTS:
        cv = clients.get(c)
        out[c] = None if cv is None else {
            "version": cv.version,
            "build": cv.build,
            "minimum": cv.minimum,
            "same_build": list(cv.same_build),
        }
    return out
