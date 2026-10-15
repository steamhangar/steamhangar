"""WP D19-1c: store details for the game card.

``GET /v1/store/{appid}/details`` -- short description, genres, developers,
release date, mode flags and the overall review rating of one app.

**May start a background lookup that leaves the LAN** (two anonymous GETs to
``store.steampowered.com`` with the app id), but only when
``VAULT_STORE_DETAILS`` is on and no answer or failure younger than the TTL is
stored. With the switch off it answers ``state: "off"`` and asks nothing.
Never ``404`` and never an error for a flaky store: the card just shows
nothing extra. Texts are stored sanitised plain text and must be rendered with
``textContent``. Auth is attached at the router level like every router.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel

from vault_api import store_details
from vault_api.auth import require_api_key
from vault_api.config import Settings
from vault_api.deps import DbOpener, db_opener, get_settings

router = APIRouter(dependencies=[Depends(require_api_key)], tags=["store"])

AppIdPath = Path(ge=1, description="Steam app id")


class StoreDetailsOut(BaseModel):
    appid: int
    #: ``off`` (switch off, nothing asked) | ``pending`` (ask again in a few
    #: seconds) | ``ready`` | ``unavailable``.
    state: str
    checked_at: str | None = None
    #: Plain text, at most 600 characters.
    description: str | None = None
    genres: list[str] = []
    developers: list[str] = []
    release_date: str | None = None
    #: Subset of ``single_player``, ``multiplayer``, ``co_op``, ``vr``.
    modes: list[str] = []
    #: Overall rating only (Steam exposes no "recent" figure).
    review_summary: str | None = None
    review_positive: int | None = None
    review_negative: int | None = None


@router.get("/v1/store/{appid}/details", response_model=StoreDetailsOut)
def get_store_details(
    request: Request,
    appid: int = AppIdPath,
    open_db: DbOpener = Depends(db_opener),
    settings: Settings = Depends(get_settings),
) -> StoreDetailsOut:
    service: store_details.StoreDetailsService = request.app.state.store_details
    with open_db() as conn:
        result = service.lookup(conn, appid, settings)
    stored = result.stored if result.state == store_details.STATE_READY else None
    if stored is None:
        return StoreDetailsOut(appid=appid, state=result.state)
    info = stored.info
    return StoreDetailsOut(
        appid=appid,
        state=result.state,
        checked_at=stored.fetched_at,
        description=info.description,
        genres=list(info.genres),
        developers=list(info.developers),
        release_date=info.release_date,
        modes=list(info.modes),
        review_summary=info.review_summary,
        review_positive=info.review_positive,
        review_negative=info.review_negative,
    )
