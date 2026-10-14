"""Delete history (WP D15-NAMES): ``DELETE /v1/history?scope=...``.

``scope`` is required -- ``all`` or ``older-than-30d`` -- so a bare
``DELETE /v1/history`` deletes nothing (422). What counts as history (finished
jobs and the D15b traffic statistics) and what is never touched (running
work, cache content, agent reports, names, the short-lived state the bypass
rule decides on) is ``vault_api/history.py``'s module docstring.

The web UI asks for confirmation before it calls this; the API itself is
one admin call behind the API key like every other ``/v1`` route.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from vault_api import history
from vault_api.auth import require_api_key
from vault_api.deps import DbOpener, db_opener

router = APIRouter(dependencies=[Depends(require_api_key)], tags=["history"])


class TrafficRowsDeletedOut(BaseModel):
    client_traffic: int
    client_depot_traffic: int
    server_traffic: int


class HistoryDeleteOut(BaseModel):
    scope: Literal["all", "older-than-30d"]
    #: Finished jobs / traffic buckets before this UTC time were deleted;
    #: null for ``all``.
    cutoff: str | None
    #: Finished (done, error, cancelled) jobs deleted.
    jobs_deleted: int
    traffic_rows_deleted: TrafficRowsDeletedOut


@router.delete("/v1/history", response_model=HistoryDeleteOut)
def delete_history(
    scope: Literal["all", "older-than-30d"] = Query(...),
    open_db: DbOpener = Depends(db_opener),
) -> HistoryDeleteOut:
    """Delete finished jobs and traffic statistics, all or older than 30 days."""
    with open_db() as conn:
        result = history.delete_history(conn, scope, datetime.now(timezone.utc))
    return HistoryDeleteOut(
        scope=scope,
        cutoff=result.cutoff,
        jobs_deleted=result.jobs_deleted,
        traffic_rows_deleted=TrafficRowsDeletedOut(**result.traffic_rows_deleted),
    )
