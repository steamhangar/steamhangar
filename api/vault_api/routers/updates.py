"""Bulk update check (WP UPD-1, user decision 2026-10-13).

``POST /v1/updates/check`` — refresh the manifest oracle for every cached game
**now** and answer with each game's verdict. It never starts a download: that
is ``POST /v1/prefill/cached`` ("Check & update") or the scheduler's windowed
sweep. The verdict feeds the "update available" badge
(``GET /v1/games[/{appid}]`` -> ``update_available``).

Auth is attached at the router level (secure-by-default pattern, see
api/README.md "Auth") — every route added here is authenticated automatically.

Same fail-soft rule as ``routers/oracle.py``: an oracle that is off, unreachable
or answers garbage is not a client error. ``200`` with ``enabled: false`` when
``VAULT_MANIFEST_ORACLE`` is unset, ``200`` with per-app ``ok: false`` when a
refresh failed. The one non-200 is ``409`` when another check is already
running (the scheduler's or another call), so overlapping calls cannot double
the outbound traffic.

**This request leaves the LAN** when the oracle is enabled (one oracle request
per cached game; api/README.md "Manifest oracle -> Privacy"). It is
synchronous: the answer takes roughly (cached games x oracle latency), and it
runs in FastAPI's threadpool like every other sync route here.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from vault_api import deletion, oracle, tool_apps, update_check
from vault_api.auth import require_api_key
from vault_api.config import Settings
from vault_api.deps import (
    DbOpener,
    db_opener,
    get_cache_root,
    get_settings,
    get_size_cache,
)
from vault_api.sizes import SizeCache

router = APIRouter(dependencies=[Depends(require_api_key)], tags=["updates"])


class AppUpdateOut(BaseModel):
    appid: int
    name: str | None
    #: Did this app's oracle refresh succeed? ``false`` means ``verdict`` comes
    #: from the previous snapshot (or ``unknown`` if there never was one).
    ok: bool
    error: str
    #: ``stale`` | ``current`` | ``unknown``.
    verdict: str
    #: ``verdict == "stale"`` — the field the badge reads.
    update_available: bool
    checked_at: str | None


class UpdateCheckOut(BaseModel):
    #: ``false`` when ``VAULT_MANIFEST_ORACLE`` is unset: nothing was checked.
    enabled: bool
    #: Cached games the oracle was asked about.
    checked: int
    ok: int
    failed: int
    #: How many of them have an update available.
    stale: int
    #: Cached games not reached because the run stopped early.
    unchecked: int
    #: Stopped after repeated oracle failures (it is probably unreachable).
    bailed_out: bool
    apps: list[AppUpdateOut]


@router.post("/v1/updates/check", response_model=UpdateCheckOut)
def check_updates(
    open_db: DbOpener = Depends(db_opener),
    settings: Settings = Depends(get_settings),
    size_cache: SizeCache = Depends(get_size_cache),
    cache_root: str = Depends(get_cache_root),
) -> UpdateCheckOut:
    """Refresh the oracle for every cached game and return the verdicts.

    Starts no download and enqueues no job. "Cached" is the one shared
    definition (``deletion.appids_with_cache_content``, the same one
    ``POST /v1/prefill/cached`` and the scheduler use), Steam tool packages
    left out. Not interval-limited: the operator asked.
    """
    # Disk truth first, before the connection is opened (same reasoning as
    # POST /v1/prefill/cached): a cold SizeCache walks the depot tree.
    depot_bytes = size_cache.get(cache_root).depot_bytes

    with open_db() as conn:
        ordinary, _tools = tool_apps.split_tool_apps(
            deletion.appids_with_cache_content(conn, depot_bytes)
        )
        result = update_check.check_apps(
            conn,
            settings,
            ordinary,
            pause=lambda: time.sleep(update_check.INTER_REQUEST_PAUSE_SECONDS),
        )
        names = {
            int(row["appid"]): row["name"]
            for row in conn.execute("SELECT appid, name FROM apps")
        }

    if result.skipped_reason == "already-running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An update check is already running; try again when it has finished.",
        )

    return UpdateCheckOut(
        enabled=result.enabled,
        checked=len(result.apps),
        ok=result.ok_count,
        failed=result.failed_count,
        stale=result.stale_count,
        unchecked=result.unchecked,
        bailed_out=result.bailed_out,
        apps=[
            AppUpdateOut(
                appid=app.appid,
                name=names.get(app.appid),
                ok=app.ok,
                error=app.error,
                verdict=app.verdict,
                update_available=app.verdict == oracle.VERDICT_STALE,
                checked_at=app.checked_at,
            )
            for app in result.apps
        ],
    )
