"""``GET /v1/about`` (WP VER-2): version and status of every component.

Authenticated at the router level like every route except ``/v1/health``,
which stays a fixed ``{"status": "ok"}`` without any version (WP VER-1,
api/README.md "Auth"). The response model is strict and closed: the six
component names and four status words are literal sets, and unknown fields
cannot appear. ``vault_api/about.py`` has what each entry means and how the
lookups degrade.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from vault_api.about import AboutService
from vault_api.auth import require_api_key
from vault_api.client_versions import release_out

router = APIRouter(dependencies=[Depends(require_api_key)], tags=["about"])


class ComponentOut(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    #: Fixed order in the list: vault-api, vault-core, vault-runner,
    #: steamprefill, vault-proxy, vault-dns.
    name: Literal[
        "vault-api", "vault-core", "vault-runner", "steamprefill", "vault-proxy", "vault-dns"
    ]
    #: The component's version, ``"invalid"`` for a baked value outside the
    #: VER-1 grammar, ``null`` when it is not known.
    version: str | None
    #: The commit id, ``"invalid"``, or ``null`` when not known.
    commit: str | None
    status: Literal["ok", "unreachable", "not_in_use", "unknown"]
    #: When vault-api looked (UTC, ``YYYY-MM-DDTHH:MM:SSZ``). Up to 60 s old:
    #: the answer is cached.
    checked_at: str
    #: One or two plain sentences: where the facts come from and what the
    #: status rests on. Never a path, address or secret.
    detail: str | None = None


class ReleaseComponentOut(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    #: Fixed order: server, agent, app (ADR-0031 components).
    name: Literal["server", "agent", "app"]
    #: The release in which the component last changed, ``null`` when the
    #: image states none (local build).
    version: str | None


class ReleaseOut(BaseModel):
    """WP RELEASE-SPLIT-1c (ADR-0031, ABOUT-VERSION-1 Weg A): the product
    version and each component's version. A component older than the
    product version is "unchanged since" its version; the About pages
    compare. Not cached and not a probe: facts baked into the image."""

    model_config = ConfigDict(extra="forbid", strict=True)

    #: The product version (``VAULT_BUILD_VERSION``) when it is a release
    #: version, else ``null`` (``dev``, ``dev-<sha>``).
    version: str | None
    components: list[ReleaseComponentOut]


class AboutOut(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    components: list[ComponentOut]
    release: ReleaseOut


@router.get("/v1/about", response_model=AboutOut)
def get_about(request: Request) -> AboutOut:
    service: AboutService = request.app.state.about
    return AboutOut(
        components=[
            ComponentOut(
                name=c.name,
                version=c.version,
                commit=c.commit,
                status=c.status,
                checked_at=c.checked_at,
                detail=c.detail,
            )
            for c in service.components()
        ],
        release=ReleaseOut.model_validate(release_out(
            request.app.state.build_info.version,
            getattr(request.app.state, "server_component_version", None),
            getattr(request.app.state, "client_versions", None) or {},
        )),
    )
