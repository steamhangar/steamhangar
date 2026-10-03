"""Agent endpoint (plan §6): ``POST /v1/agent/installed``.

The vault-agent on a gaming machine (Windows PC, Steam Deck / Steam Machine —
ADR-0002) posts the FULL list of installed Steam app ids here, at logon/boot
and every 10 minutes since WP AGENT-FEAT-1 (30 before). vault-api stores it as
a snapshot and answers with the diff against that client's previous snapshot.
Since schema v17 a report may also say which agent version sent it and how
often it reports (``agent_version``, ``report_interval_seconds``, both
optional); ``GET /v1/clients`` turns that into online/offline.

Auth is attached at the router level (secure-by-default pattern, see
api/README.md "Auth") — every route added here is authenticated automatically.

The semantics, the retention policy and the "removals are surfaced, never
acted on" boundary are documented in api/README.md ("Agent reports"); the
mechanics live in ``vault_api/agent_reports.py``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

from vault_api import agent_reports, is_valid_version
from vault_api.agent_reports import (
    MAX_APPIDS_PER_REPORT,
    MAX_CLIENT_ID_LENGTH,
    MAX_REPORT_INTERVAL_SECONDS,
    MIN_REPORT_INTERVAL_SECONDS,
)
from vault_api.auth import require_api_key
from vault_api.deps import DbOpener, db_opener, get_agent_report_keep
from vault_api.validation import AppId

router = APIRouter(dependencies=[Depends(require_api_key)], tags=["agent"])


class InstalledReportRequest(BaseModel):
    # extra="forbid" for the same reason as every other write endpoint here:
    # a typo'd field name (``appIds``) must 422 rather than be read as "the
    # library is empty", which would report every game as removed.
    model_config = ConfigDict(extra="forbid")

    client_id: str = Field(min_length=1, max_length=MAX_CLIENT_ID_LENGTH)

    #: The COMPLETE installed list (ADR-0002). May be empty — a machine with
    #: nothing installed is a legitimate report, and the diff then correctly
    #: says everything was removed. It is NOT optional, though: a missing
    #: field is a broken agent, and defaulting it to ``[]`` would silently
    #: report a full library as wiped.
    appids: list[AppId] = Field(max_length=MAX_APPIDS_PER_REPORT)

    #: WP AGENT-FEAT-1 (schema v17), optional: the agent's build version
    #: (``vault-agent --version``), in the VER-1 version grammar. Absent or
    #: ``null`` from agents that predate it -- ``GET /v1/clients`` then shows
    #: ``agent_version: null`` ("version unknown"). Strict: a JSON number is
    #: not a version.
    agent_version: StrictStr | None = None

    #: WP AGENT-FEAT-1 (schema v17), optional: how often this agent reports,
    #: in whole seconds, 60..86400. ``GET /v1/clients`` derives online/offline
    #: from it; absent means "assume 30 minutes". Strict: ``true``, ``600.0``
    #: and ``"600"`` are refused rather than coerced (docs/LEARNINGS.md,
    #: Pydantic lax mode turns ``true`` into 1).
    report_interval_seconds: Annotated[
        StrictInt,
        Field(ge=MIN_REPORT_INTERVAL_SECONDS, le=MAX_REPORT_INTERVAL_SECONDS),
    ] | None = None

    @field_validator("agent_version")
    @classmethod
    def _validate_agent_version(cls, value: str | None) -> str | None:
        """The VER-1 grammar (``vault_api.is_valid_version``): the value is
        shown in the web and app and written into log lines, so whitespace,
        quotes, slashes and control characters are refused, not stored."""
        if value is not None and not is_valid_version(value):
            raise ValueError(
                "agent_version must start with a letter or digit and contain "
                "only letters, digits and . _ + - (at most 64 characters)"
            )
        return value

    @field_validator("client_id")
    @classmethod
    def _validate_client_id(cls, value: str) -> str:
        """Printable, no control characters, no surrounding whitespace.

        This value is a grouping key *and* it is written into log lines. A
        control character would let a malformed (or hostile) agent forge log
        lines with an embedded newline; surrounding whitespace would make
        ``"pc"`` and ``"pc "`` two invisibly different clients. Both are
        rejected loudly instead of being silently normalised — the agent's
        config is a one-line setting the operator can fix.
        """
        if value != value.strip():
            raise ValueError(
                "client_id must not start or end with whitespace "
                "(it is an identity key: 'pc' and 'pc ' would be two clients)"
            )
        if not value.isprintable():
            raise ValueError(
                "client_id must not contain control characters "
                "(newlines, tabs, NUL); use a plain label such as a hostname"
            )
        if value in {".", ".."}:
            # Nothing derives a filesystem path from client_id today. One line
            # here forecloses that ever becoming a traversal (a per-client log
            # or export directory is an obvious future feature), and neither
            # value is a machine label anybody meant to type.
            raise ValueError(
                "client_id must not be '.' or '..'; use a plain label such as "
                "a hostname"
            )
        return value


class InstalledReportResponse(BaseModel):
    client_id: str
    #: Number of DISTINCT app ids stored — duplicates in the request collapse
    #: (a snapshot is a set), so this can be lower than ``len(appids)`` sent.
    received: int
    #: Installed now, not in the previous snapshot. Everything, on a first report.
    added: list[int]
    #: In the previous snapshot, gone now. ADR-0002: surfaced, never acted on —
    #: no cache content is deleted and no app status changes.
    removed: list[int]
    #: True when this client had no usable previous snapshot (first ever report,
    #: or its predecessor row was unreadable — see agent_reports._decode_appids).
    first_report: bool


@router.post("/v1/agent/installed", response_model=InstalledReportResponse)
def report_installed_apps(
    request: Request,
    body: InstalledReportRequest,
    open_db: DbOpener = Depends(db_opener),
    keep: int = Depends(get_agent_report_keep),
) -> InstalledReportResponse:
    """Store one full-list snapshot and return the diff (plan §6, ADR-0002).

    ``200``, not ``201``: the useful part of the answer is the diff, and the
    stored snapshot has no addressable URL of its own to point a ``Location``
    header at.

    What this endpoint does NOT do, deliberately (ADR-0002 + plan A9):

    * it never deletes cache content for a removed title,
    * it never changes ``apps.status`` and never creates an ``apps`` row,
    * it queues nothing.

    Removals are logged (INFO, audit-style) and returned. Acting on them stays
    a human/API decision (``DELETE /v1/cache/{appid}``); Phase 3's scheduler is
    what will consume these snapshots as the prefill set.

    **The peer address is recorded alongside the snapshot** (schema v9,
    WP 3.11, ADR-0008): "vault-agent reports already arrive FROM those
    addresses, so vault-api records the report source address and correlates".
    It is taken from the TCP peer (``request.client``) and **never** from
    ``X-Forwarded-For`` — vault-api is not behind a proxy in this design, and
    trusting a client-settable header for an identity key would let any agent
    claim any machine's cache traffic (or disclaim its own to dodge bypass
    detection). That "never" is only true because the shipped
    ``api/Dockerfile`` starts uvicorn with ``--no-proxy-headers``
    (WP API-FIX-2, N4): uvicorn's default is ``--proxy-headers`` ON, which
    rewrites ``request.client`` from ``X-Forwarded-For`` for connections
    from trusted hosts (``--forwarded-allow-ips``, default ``127.0.0.1``, so
    any same-host reverse proxy or container-network hop would have been
    enough). An operator who runs vault-api under their own uvicorn command
    without that flag re-opens this. ``request.client`` is ``None`` on some
    transports; the report is stored anyway, with a NULL address.
    """
    peer = request.client.host if request.client is not None else None
    with open_db() as conn:
        result = agent_reports.store_report(
            conn,
            client_id=body.client_id,
            appids=body.appids,
            keep=keep,
            source_addr=peer,
            agent_version=body.agent_version,
            report_interval_seconds=body.report_interval_seconds,
        )

    return InstalledReportResponse(
        client_id=result.client_id,
        received=result.received,
        added=result.added,
        removed=result.removed,
        first_report=result.first_report,
    )
