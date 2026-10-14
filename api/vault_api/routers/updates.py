"""Update check endpoints (WP UPD-1, user decision 2026-10-13).

``POST /v1/updates/check`` starts a check of every cached game **in the
background** and answers at once: ``202`` with ``status: "started"`` (or
``"already_running"`` when the scheduler's or an earlier manual check is still
going), ``200`` with ``status: "disabled"`` / ``enabled: false`` when the
manifest oracle is off (nothing runs, nothing leaves the LAN). It never starts a
download: that is ``POST /v1/prefill/cached`` ("Check & update") or the
scheduler's windowed sweep. A client polls ``GET /v1/updates/status`` until
``running`` is ``false``, then re-reads ``GET /v1/games`` for the
``update_available`` flags.

Why asynchronous: a run asks the oracle once per cached game, spaced by
``update_check.INTER_REQUEST_PAUSE_SECONDS`` and bounded by
``VAULT_MANIFEST_ORACLE_TIMEOUT`` per request, so with a few hundred games it
takes minutes, far past any client's HTTP timeout (the Android client gives up
after 45 s).

Auth is attached at the router level (secure-by-default pattern, see
api/README.md "Auth") — every route added here is authenticated automatically.

**A started check leaves the LAN** (one oracle request per cached game;
api/README.md "Manifest oracle -> Privacy").
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from vault_api import deletion, tool_apps, update_check
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


class UpdateCheckStartOut(BaseModel):
    #: ``started``: a check now runs in the background. ``already_running``:
    #: one was already going (scheduled or manual); nothing new was started.
    #: ``disabled``: the manifest oracle is off; nothing runs.
    status: Literal["started", "already_running", "disabled"]
    #: ``false`` iff ``VAULT_MANIFEST_ORACLE=off``.
    enabled: bool


class UpdateCheckStatusOut(BaseModel):
    #: ``false`` iff ``VAULT_MANIFEST_ORACLE=off``: no check runs, scheduled or
    #: manual, and no game is flagged.
    enabled: bool
    #: Scheduled (background) checks are on. Since WP UPD-5 (user decision
    #: 2026-10-14) this is the same as ``enabled``: the check runs every
    #: ``VAULT_SCHEDULE_INTERVAL_MINUTES`` whenever the oracle is on, with or
    #: without a schedule window (the window only bounds downloads). Kept as
    #: its own field so clients written against UPD-1 keep working.
    scheduled: bool
    #: A check is in flight right now.
    running: bool
    #: The last run's trigger: ``manual`` | ``scheduled``; ``null`` = never.
    last_trigger: Literal["manual", "scheduled"] | None
    #: UTC ``YYYY-MM-DDTHH:MM:SSZ``, stamped before the first request.
    last_started_at: str | None
    #: ``null`` while running, or if the process died during the run.
    last_finished_at: str | None
    #: ``completed`` | ``bailed_out`` (stopped after repeated oracle failures)
    #: | ``aborted`` (shutdown) | ``error`` (a bug, see the log) |
    #: ``interrupted`` (the process died during the run); ``null`` while
    #: running or never.
    last_outcome: (
        Literal["completed", "bailed_out", "aborted", "error", "interrupted"] | None
    )
    #: Counters of the last finished run (``null`` while running): cached
    #: games asked about, answered ok, failed, with an update available.
    last_apps: int | None
    last_ok: int | None
    last_failed: int | None
    last_stale: int | None


_START_STATUS = {
    update_check.STARTED: "started",
    update_check.ALREADY_RUNNING: "already_running",
    update_check.DISABLED: "disabled",
}


def _runner(request: Request) -> update_check.UpdateCheckRunner:
    return request.app.state.update_checker


@router.post(
    "/v1/updates/check",
    response_model=UpdateCheckStartOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def check_updates(
    response: Response,
    open_db: DbOpener = Depends(db_opener),
    settings: Settings = Depends(get_settings),
    size_cache: SizeCache = Depends(get_size_cache),
    cache_root: str = Depends(get_cache_root),
    runner: update_check.UpdateCheckRunner = Depends(_runner),
) -> UpdateCheckStartOut:
    """Start an update check of every cached game; answer immediately.

    Starts no download and enqueues no job. "Cached" is the one shared
    definition (``deletion.appids_with_cache_content``, the same one
    ``POST /v1/prefill/cached`` and the scheduler use), Steam tool packages
    left out; it is computed on the run's own thread (a cold SizeCache walks
    the depot tree). Not interval-limited (the operator asked), but it does
    stamp the interval, so the next scheduled check is one interval away.
    """

    def select(conn: sqlite3.Connection) -> list[int]:
        depot_bytes = size_cache.get(cache_root).depot_bytes
        ordinary, _tools = tool_apps.split_tool_apps(
            deletion.appids_with_cache_content(conn, depot_bytes)
        )
        return sorted(ordinary)

    with open_db() as conn:
        started = runner.start(
            conn,
            settings,
            trigger=update_check.TRIGGER_MANUAL,
            select_appids=select,
            now=datetime.now(timezone.utc),
            require_interval=False,
        )
    if started == update_check.STOPPING:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="vault-api is shutting down; no update check was started.",
        )
    if started == update_check.DISABLED:
        response.status_code = status.HTTP_200_OK
    return UpdateCheckStartOut(
        status=_START_STATUS[started],  # type: ignore[arg-type]
        enabled=settings.manifest_oracle_enabled,
    )


@router.get("/v1/updates/status", response_model=UpdateCheckStatusOut)
def update_status(
    open_db: DbOpener = Depends(db_opener),
    settings: Settings = Depends(get_settings),
    runner: update_check.UpdateCheckRunner = Depends(_runner),
) -> UpdateCheckStatusOut:
    """Is a check running, and what did the last one (either trigger) find?"""
    # Sampled before AND after the row read: a run that starts or finishes in
    # between must not make an in-flight row look "interrupted".
    running_before = runner.running
    with open_db() as conn:
        row_status = update_check.read_status(conn, settings, running=True)
    running = running_before or runner.running
    current = row_status if running else update_check.settle_status(row_status)
    return UpdateCheckStatusOut(
        enabled=current.enabled,
        scheduled=current.enabled,
        running=current.running,
        last_trigger=current.last_trigger,  # type: ignore[arg-type]
        last_started_at=current.last_started_at,
        last_finished_at=current.last_finished_at,
        last_outcome=current.last_outcome,  # type: ignore[arg-type]
        last_apps=current.last_apps,
        last_ok=current.last_ok,
        last_failed=current.last_failed,
        last_stale=current.last_stale,
    )
