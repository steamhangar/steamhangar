"""Client names (WP D15-NAMES): ``GET /v1/client-names``,
``PUT``/``DELETE /v1/client-names/{key_type}/{key}``.

A name is keyed by address (``key_type = "ip"``); ``"mac"`` is reserved and
answered with 422 until an agent reports MACs. Which client shows which
name, and why an explicit name beats an agent's own PC name, is
``vault_api/client_names.py``'s module docstring. Names appear as ``name``,
``name_addr`` and ``display_name`` in ``GET /v1/clients`` and
``GET /v1/clients/overview``.

Validation is strict: the address must be an IP literal (stored in its
canonical spelling, so ``::ffff:192.0.2.7`` and ``192.0.2.7`` are the same
key), the name 1 to 40 characters after trimming with no control or
invisible formatting characters. Clearing is ``DELETE``, never an empty
name. Auth is attached at the router level (the one API key).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, StrictStr

from vault_api import client_names
from vault_api.auth import require_api_key
from vault_api.deps import DbOpener, db_opener

router = APIRouter(dependencies=[Depends(require_api_key)], tags=["clients"])


class ClientNameIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: 1..40 characters after trimming; see ``client_names.normalize_name``.
    name: StrictStr


class ClientNameOut(BaseModel):
    #: "ip" today ("mac" reserved).
    key_type: str
    #: The canonical address the name is stored under.
    key: str
    name: str
    updated_at: str


def _key(key_type: str, key: str) -> str:
    """Validate the path key; 422 for an unsupported type or a bad address."""
    if key_type == client_names.KEY_TYPE_MAC:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Naming by MAC address is not supported yet: no source reports MACs.",
        )
    if key_type not in client_names.SUPPORTED_KEY_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown key type {key_type!r}; use 'ip'.",
        )
    try:
        return client_names.canonical_ip(key)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from None


def _out(entry: client_names.ClientName) -> ClientNameOut:
    return ClientNameOut(
        key_type=entry.key_type, key=entry.key, name=entry.name, updated_at=entry.updated_at
    )


@router.get("/v1/client-names", response_model=list[ClientNameOut])
def list_client_names(open_db: DbOpener = Depends(db_opener)) -> list[ClientNameOut]:
    """Every stored name."""
    with open_db() as conn:
        entries = client_names.list_names(conn)
    return [_out(entry) for entry in entries]


@router.put("/v1/client-names/{key_type}/{key}", response_model=ClientNameOut)
def put_client_name(
    key_type: str,
    key: str,
    body: ClientNameIn,
    open_db: DbOpener = Depends(db_opener),
) -> ClientNameOut:
    """Set or rename. The address need not be known yet: naming a machine
    before it first loads through the cache is allowed."""
    canonical = _key(key_type, key)
    try:
        name = client_names.normalize_name(body.name)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from None
    with open_db() as conn:
        entry = client_names.set_name(conn, key_type, canonical, name)
    return _out(entry)


@router.delete(
    "/v1/client-names/{key_type}/{key}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
def delete_client_name(
    key_type: str,
    key: str,
    open_db: DbOpener = Depends(db_opener),
) -> None:
    """Clear a name. 404 when that address has none (same "nothing to act
    on" shape as ``DELETE /v1/clients/{client_id}``)."""
    canonical = _key(key_type, key)
    with open_db() as conn:
        existed = client_names.clear_name(conn, key_type, canonical)
    if not existed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No name stored for {canonical!r}",
        )
