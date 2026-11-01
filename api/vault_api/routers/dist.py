"""Release downloads from the vault (WP DIST-1a): the files and their index.

Two surfaces, two auth decisions:

- ``GET/HEAD /dist/<name>`` -- the files themselves. **Public, outside
  ``/v1``**, like the web UI's static files and the Steam app-return page:
  the device that downloads them is by definition not paired yet (a phone
  without the app has no key, the agent install snippet downloads before
  the key is entered). The files are the public release assets of this
  server's own version (identical bytes to the GitHub Release); serving them
  adds no secret to the LAN, only the version number in the file names.
  ``PreAuthBodyGuard`` keeps requiring the key on every ``/v1`` path; this
  route does not widen that carve-out. Only the exact names
  ``vault_api.dist.artifact_specs`` computes for the running version (plus
  the version-free APK alias) can be served; anything else is a plain 404
  with no listing.
- ``GET /v1/dist`` -- the index the web UI reads: which files are baked in
  (name, size, SHA-256), the server version, and for each file the GitHub
  Release URL of the same version as a fallback. Behind the API key like
  every ``/v1`` route.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from vault_api.auth import require_api_key
from vault_api.client_versions import client_versions_out
from vault_api.dist import (
    APK_ALIAS,
    DIST_URL_PREFIX,
    DistCatalog,
    is_release_version,
    release_base_url,
    release_url,
)

files_router = APIRouter(tags=["dist"])
index_router = APIRouter(dependencies=[Depends(require_api_key)], tags=["dist"])

#: Versioned names never change content; the others (scripts, SHA256SUMS,
#: the APK alias) change with every release under the same name.
_IMMUTABLE = "public, max-age=31536000, immutable"
_REVALIDATE = "no-cache"


def _catalog(request: Request) -> DistCatalog:
    return request.app.state.dist_catalog


@files_router.api_route("/dist/{name}", methods=["GET", "HEAD"], include_in_schema=False)
def get_dist_file(request: Request, name: str) -> FileResponse:
    catalog = _catalog(request)
    spec = catalog.spec_for_request(name)
    found = catalog.local_path(spec) if spec is not None else None
    if spec is None or found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    path, st = found
    cache = _IMMUTABLE if spec.versioned and name == spec.name else _REVALIDATE
    return FileResponse(
        path,
        stat_result=st,
        media_type=spec.media_type,
        # Always the real (versioned) name, also for the alias: a phone's
        # download list then says which version it holds.
        filename=spec.name,
        headers={"Cache-Control": cache},
    )


class ArtifactOut(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    #: Stable id: app-android, agent-windows-amd64, agent-linux-amd64,
    #: agent-linux-arm64, install-task, run-vault-agent, uninstall-task,
    #: systemd-service, systemd-timer, sha256sums.
    id: str
    #: The release asset name: the server's version, or since WP
    #: RELEASE-SPLIT-1b the bundled client's own version (``version``).
    name: str
    #: The release this file belongs to (RELEASE-SPLIT-1b).
    version: str
    #: True when the vault serves the file itself.
    local: bool
    #: Size in bytes and SHA-256 (lowercase hex) of the local copy; null
    #: when not local.
    size: int | None
    sha256: str | None
    #: ``/dist/<name>`` (path only, relative to the vault origin) when local.
    local_url: str | None
    #: The GitHub Release asset of the file's own version; null when the
    #: server version is not a published release.
    release_url: str | None
    #: Where to download from: ``local_url`` if local, else ``release_url``.
    url: str | None
    source: Literal["local", "release", "none"]


class ClientVersionOut(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: str
    build: str
    minimum: str | None
    same_build: list[str]


class ClientsOut(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    agent: ClientVersionOut | None
    app: ClientVersionOut | None


class DistOut(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    #: The vault-api build version (``GET /v1/about``'s vault-api version).
    version: str
    #: True when ``version`` is a published release (release-shaped version
    #: and a known commit), i.e. when ``release_url`` values exist.
    release: bool
    #: ``/dist/`` -- every local file is served under this prefix.
    local_base: str
    #: The GitHub Release download base of this version (trailing slash),
    #: or null.
    release_base: str | None
    #: ``/dist/steamhangar-app.apk`` while the APK is baked in, else null.
    apk_alias_url: str | None
    artifacts: list[ArtifactOut]
    #: WP RELEASE-SPLIT-1b: the bundled agent and app (component version,
    #: build version of the files, minimum, same_build), null each for a
    #: local build. Same object as ``GET /v1/settings``'s ``clients``.
    clients: ClientsOut



@index_router.get("/v1/dist", response_model=DistOut)
def get_dist_index(request: Request) -> DistOut:
    catalog = _catalog(request)
    build = catalog.build
    base = release_base_url(build)
    items: list[ArtifactOut] = []
    apk_local = False
    for art in catalog.artifacts():
        local = art.path is not None
        local_url = f"{DIST_URL_PREFIX}{art.spec.name}" if local else None
        rel_url = release_url(build, art.spec)
        if art.spec.id == "app-android" and local:
            apk_local = True
        items.append(
            ArtifactOut(
                id=art.spec.id,
                name=art.spec.name,
                version=art.spec.version,
                local=local,
                size=art.size,
                sha256=art.sha256,
                local_url=local_url,
                release_url=rel_url,
                url=local_url or rel_url,
                source="local" if local else ("release" if rel_url else "none"),
            )
        )
    return DistOut(
        version=build.version,
        release=is_release_version(build),
        local_base=DIST_URL_PREFIX,
        release_base=base,
        apk_alias_url=f"{DIST_URL_PREFIX}{APK_ALIAS}" if apk_local else None,
        artifacts=items,
        clients=ClientsOut.model_validate(client_versions_out(catalog.clients)),
    )
