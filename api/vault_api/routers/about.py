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


class AboutOut(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    components: list[ComponentOut]


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
        ]
    )
