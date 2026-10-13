"""Manifest-oracle endpoints (WP 3.9, ADR-0006 decision 4).

``GET /v1/oracle/{appid}``     — the stored view: pre-emptive stale badge,
                                 depot list (including for a game that has
                                 never been cached), open beta branches.
``POST /v1/oracle/{appid}/refresh`` — ask the oracle now.
``DELETE /v1/oracle/{appid}``  — forget what it said.

Auth is attached at the router level (secure-by-default pattern, see
api/README.md "Auth") — every route added here is authenticated automatically.

**Two things this router deliberately does NOT do.**

This router itself does not run on a schedule; refresh here is an explicit
call. Since WP UPD-1 the scheduler separately refreshes cached games through
``update_check.maybe_check_updates``, but only while the operator has enabled
the oracle (see api/README.md "Always-on update check").

It does not fail. ``POST .../refresh`` answers ``200`` with ``ok: false`` and
a reason when the oracle is unreachable or answered garbage, and ``200`` with
``enabled: false`` when the feature is off. Neither is a client error — the
client did nothing wrong — and turning a flaky third party into 5xx responses
would be exactly the fragility ADR-0006's fail-soft rule exists to avoid. The
only error code here is ``422`` for an ``appid < 1``.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Path, status
from pydantic import BaseModel

from vault_api import oracle
from vault_api.auth import require_api_key
from vault_api.config import Settings
from vault_api.deps import DbOpener, db_opener, get_settings

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_api_key)], tags=["oracle"])

#: Path constraint shared by every route here — the same ``ge=1`` rule the
#: cache routes apply, so an appid can never reach a URL builder as ``0`` or a
#: negative number.
AppIdPath = Path(ge=1, description="Steam app id")


class DepotStalenessOut(BaseModel):
    depotid: int
    #: What vault-api itself last parsed for this depot (``depot_manifests``).
    #: ``null`` for a depot that has never been cached.
    recorded_manifestid: str | None
    #: What the oracle says the current ``public`` manifest is.
    oracle_manifestid: str | None
    #: ``current`` | ``stale`` | ``not_cached`` | ``unknown``.
    verdict: str
    #: Open (non-passworded) branches other than ``public`` that the oracle
    #: knows for this depot — the ones whose manifests join the GC keep set.
    beta_branches: list[str]


class OracleViewOut(BaseModel):
    appid: int
    #: ``false`` when ``VAULT_MANIFEST_ORACLE=off`` — render "not
    #: configured", never "nothing is stale".
    enabled: bool
    #: ``null`` when nothing has been fetched for this app yet.
    checked_at: str | None
    #: Which oracle produced the stored rows (provenance).
    source: str | None
    buildid: str | None
    #: App-level badge, rolled up from the depots — see
    #: ``oracle.AppOracleView``.
    verdict: str
    depots: list[DepotStalenessOut]


class OracleRefreshOut(BaseModel):
    appid: int
    enabled: bool
    ok: bool
    #: Empty on success and when the oracle is disabled (that is not an error).
    error: str
    checked_at: str | None
    depot_count: int
    branch_manifest_count: int
    open_branches: list[str]
    #: Branches dropped because they are (or might be) password protected —
    #: their manifests are encrypted and are never stored. See
    #: `vault_api/oracle.py`.
    skipped_password_branches: int
    warnings: list[str]


@router.get("/v1/oracle/{appid}", response_model=OracleViewOut)
def get_oracle_view(
    appid: int = AppIdPath,
    open_db: DbOpener = Depends(db_opener),
    settings: Settings = Depends(get_settings),
) -> OracleViewOut:
    """What the oracle currently says about one app. Read-only, no network.

    Deliberately **not** ``404`` for an app with no stored snapshot: "the
    oracle has never been asked about this game" is a normal state with a
    normal answer (``checked_at: null``, ``verdict: "unknown"``), not a
    missing resource. Nor does it require an ``apps`` row — showing depot
    information for a game that has never been cached is one of the two things
    this feature exists for.
    """
    with open_db() as conn:
        view = oracle.app_view(conn, appid, settings=settings)

    return OracleViewOut(
        appid=view.appid,
        enabled=view.enabled,
        checked_at=view.checked_at,
        source=view.source,
        buildid=view.buildid,
        verdict=view.verdict,
        depots=[
            DepotStalenessOut(
                depotid=depot.depotid,
                recorded_manifestid=depot.recorded_manifestid,
                oracle_manifestid=depot.oracle_manifestid,
                verdict=depot.verdict,
                beta_branches=list(depot.beta_branches),
            )
            for depot in view.depots
        ],
    )


@router.post("/v1/oracle/{appid}/refresh", response_model=OracleRefreshOut)
def refresh_oracle(
    appid: int = AppIdPath,
    open_db: DbOpener = Depends(db_opener),
    settings: Settings = Depends(get_settings),
) -> OracleRefreshOut:
    """Query the oracle for one app **now** and store the answer.

    **This request leaves the LAN** when the oracle is enabled (see
    api/README.md "Manifest oracle → Privacy"). It is a synchronous endpoint
    on purpose — the operator asked, and the answer is bounded by
    ``VAULT_MANIFEST_ORACLE_TIMEOUT`` — and it runs in FastAPI's threadpool
    like every other sync route here, so a slow oracle occupies one worker
    thread and nothing else. It is never enqueued as a job: it neither
    downloads nor deletes anything, and putting it behind the single prefill
    worker would make it wait behind multi-hour downloads.
    """
    with open_db() as conn:
        result = oracle.refresh_app(conn, appid, settings=settings)

    return OracleRefreshOut(
        appid=result.appid,
        enabled=result.enabled,
        ok=result.ok,
        error=result.error,
        checked_at=result.checked_at,
        depot_count=result.depot_count,
        branch_manifest_count=result.branch_manifest_count,
        open_branches=list(result.open_branches),
        skipped_password_branches=result.skipped_password_branches,
        warnings=list(result.warnings),
    )


@router.delete(
    "/v1/oracle/{appid}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
def forget_oracle(
    appid: int = AppIdPath,
    open_db: DbOpener = Depends(db_opener),
) -> None:
    """Drop everything the oracle said about one app.

    ``204`` whether or not anything was stored — this is an idempotent
    "make sure it is gone", not a lookup. It exists because oracle rows can
    only *add* GC keep-set protection: an operator who stops trusting the
    oracle needs a way to withdraw its claims without waiting for a refresh
    that may never succeed.
    """
    with open_db() as conn:
        oracle.clear_app(conn, appid)
