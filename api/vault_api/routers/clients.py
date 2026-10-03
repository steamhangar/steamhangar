"""Client endpoint (plan §6): ``GET /v1/clients``.

Plan §6 describes this row as "per-client hit stats incl. bypass warnings".
WP 2.4 shipped the agent-report half of that object; **WP 3.11 (ADR-0008) adds
the promised cache-side fields** now that vault-api sweeps vault-core's
structured event log:

    {"client_id", "first_seen", "last_reported_at", "app_count",
     "source_addrs", "cache_hits", "cache_misses", "bytes_served",
     "last_seen_in_cache_log", "bypass_suspected"}

WP AGENT-FEAT-1 (schema v17) adds four more, again next to the old ones:

    {"agent_version", "report_interval_seconds", "presence", "offline_after"}

The WP 2.4 shape was chosen to be forward-compatible — a flat object per
client — and that held: every new field sits next to the old ones and nothing
was restructured.

Presence (WP AGENT-FEAT-1)
--------------------------
``presence`` is ``"online"`` or ``"offline"``, nothing else: a client is
offline once its last report is older than two report intervals plus five
minutes, using the interval its latest report stated, or 30 minutes when it
stated none (an agent from before AGENT-FEAT-1). The rule lives in
``agent_reports.presence``; this router only calls it, with one ``now`` for
the whole answer. ``offline_after`` says when the client turns offline if no
further report arrives, so a UI can show "last seen ..." and know when to
flip without re-implementing the rule.

How a client is correlated with cache traffic
---------------------------------------------
The event log knows **addresses**; agent reports know **client_ids**. Schema
v9's ``agent_reports.source_addr`` is the only bridge: each report records the
address it arrived from, and a client's statistics are the sum over every
address its retained reports came from (``agent_reports.source_addrs_for``).

Bypass detection, and why it fails toward NOT accusing
------------------------------------------------------
**The rule itself lives in ``event_sweep.bypass_suspected`` (moved there in
WP 3.13)**, shared with the cache-event sweep's own transition check
(``event_sweep.check_bypass_transitions``, the webhook feature's persist
step) — one definition read by both a live GET request and a background
sweep, so they can never disagree about who is flagged. This module still
computes the two feed-level inputs (``feed_can_accuse``, ``cutoff_iso``) and
calls it per client.

``bypass_suspected`` answers plan §5's actual pain point: a machine that
reports installed games but never appears at the cache is probably resolving
Steam's CDN around vault-dns (IPv6 leak, hardcoded DNS, a VPN). A false
positive here sends an operator hunting a network fault that does not exist,
so **every unknown reads as "not suspected"**. The rule is a chain of
disqualifications, and only a client that survives all of them is flagged:

1. the event feed is off (``VAULT_EVENT_LOG_PATH`` unset) — there is no cache
   log to be absent from;
2. no sweep has ever completed, or the feed is younger than
   ``VAULT_BYPASS_WINDOW_DAYS`` — "we have not been watching long enough" is
   not evidence about the client (``event_sweep.feed_is_young``);
3. the client's own report is older than the window — a machine that has been
   off cannot be bypassing anything, and would otherwise be accused forever;
4. the client reports **no** installed games (or its snapshot was unreadable) —
   nothing to download, so nothing to download around;
5. no retained report recorded a source address — including every report
   written before schema v9 — so the client cannot be correlated at all;
6. the client HAS appeared in the cache log within the window.

Only then: reporting, recently, with games, from a known address, and with
zero cache-log presence in the window ⇒ ``true``.

ADR-0001's production requirement 7 is why the default window is 3 days rather
than 1: Steam LAN peer-to-peer transfers can legitimately replace cache
traffic, so a single quiet day proves nothing.

Auth is attached at the router level (secure-by-default pattern, see
api/README.md "Auth").
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from vault_api import agent_reports, event_sweep
from vault_api.auth import require_api_key
from vault_api.config import Settings
from vault_api.deps import DbOpener, db_opener
from vault_api.jobs import to_utc_iso

router = APIRouter(dependencies=[Depends(require_api_key)], tags=["clients"])


class ClientOut(BaseModel):
    client_id: str
    #: Oldest RETAINED report. Retention (VAULT_AGENT_REPORT_KEEP) prunes older
    #: snapshots, so on a long-running client this moves forward over time — it
    #: is not a permanent "first contact" record. See api/README.md.
    first_seen: str
    last_reported_at: str
    #: Size of the client's latest snapshot. ``null`` only if that stored row's
    #: JSON was unreadable (corrupt/hand-edited database; logged at WARNING).
    app_count: int | None
    #: Addresses this client's retained reports arrived from (schema v9). The
    #: keys its cache statistics are summed over. Empty for reports stored
    #: before schema v9, which is why such a client is never bypass_suspected.
    source_addrs: list[str]
    #: Cache HITs served to this client, summed over the RETAINED statistics
    #: windows (VAULT_CLIENT_STATS_KEEP). Not a lifetime counter — old windows
    #: are pruned, so this number can go down. 0 when the event feed is off.
    cache_hits: int
    #: Cache MISSes, same retention caveat. hits/(hits+misses) is the hit rate.
    cache_misses: int
    #: Bytes vault-core actually delivered to this client (2xx responses only),
    #: same retention caveat.
    bytes_served: int
    #: Newest event-log timestamp for any of this client's addresses; ``null``
    #: when it has never appeared in the cache log (or the feed is off).
    last_seen_in_cache_log: str | None
    #: Reports installed games but has no cache-log presence within
    #: VAULT_BYPASS_WINDOW_DAYS. Fails toward ``false`` on every unknown — see
    #: the module docstring for the full disqualification chain.
    bypass_suspected: bool
    #: WP AGENT-FEAT-1: the agent build that sent the LATEST report
    #: (``vault-agent --version``). ``null`` = version unknown: an agent from
    #: before AGENT-FEAT-1, or a latest report that did not carry it.
    agent_version: str | None
    #: WP AGENT-FEAT-1: the report interval the latest report stated, in
    #: seconds. ``null`` = not stated; presence then assumes 1800 (30 min).
    report_interval_seconds: int | None
    #: WP AGENT-FEAT-1: ``"online"`` until ``offline_after``, ``"offline"``
    #: after it. Computed per request from ``last_reported_at``; see the
    #: module docstring.
    presence: Literal["online", "offline"]
    #: WP AGENT-FEAT-1: ``last_reported_at`` + 2 x interval + 5 minutes, the
    #: moment this client turns offline without another report. ``null`` only
    #: when ``last_reported_at`` is unreadable (then ``presence`` is offline).
    offline_after: str | None


@router.get("/v1/clients", response_model=list[ClientOut])
def list_clients(
    request: Request,
    open_db: DbOpener = Depends(db_opener),
) -> list[ClientOut]:
    """Every client that has reported installed apps, by ``client_id``."""
    settings: Settings = request.app.state.settings
    now = datetime.now(timezone.utc)
    cutoff_iso = to_utc_iso(now - timedelta(days=settings.bypass_window_days))

    with open_db() as conn:
        summaries = agent_reports.list_clients(conn)
        state = event_sweep.read_state(conn)
        totals = {
            summary.client_id: event_sweep.totals_for_addrs(conn, summary.source_addrs)
            for summary in summaries
        }

    # Computed once for every client rather than per row: whether the feed can
    # support an accusation at all is a property of the FEED, not of a client.
    feed_can_accuse = settings.event_sweep_enabled and not event_sweep.feed_is_young(
        state, settings, now
    )

    # One `now` for every row (and for the bypass cutoff above), so two
    # clients with the same last report can never disagree within one answer.
    presence = {
        summary.client_id: agent_reports.presence(
            summary.last_reported_at, summary.report_interval_seconds, now
        )
        for summary in summaries
    }

    return [
        ClientOut(
            client_id=summary.client_id,
            first_seen=summary.first_seen,
            last_reported_at=summary.last_reported_at,
            app_count=summary.app_count,
            source_addrs=summary.source_addrs,
            cache_hits=totals[summary.client_id].hits,
            cache_misses=totals[summary.client_id].misses,
            bytes_served=totals[summary.client_id].bytes_served,
            last_seen_in_cache_log=totals[summary.client_id].last_seen,
            bypass_suspected=event_sweep.bypass_suspected(
                summary,
                totals[summary.client_id],
                feed_can_accuse=feed_can_accuse,
                cutoff_iso=cutoff_iso,
            ),
            agent_version=summary.agent_version,
            report_interval_seconds=summary.report_interval_seconds,
            presence=presence[summary.client_id][0],
            offline_after=presence[summary.client_id][1],
        )
        for summary in summaries
    ]


@router.delete(
    "/v1/clients/{client_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
def delete_client(
    client_id: str,
    open_db: DbOpener = Depends(db_opener),
) -> None:
    """Remove one client's rows (WP AG-1 — see ``agent_reports.delete_client``
    for the full table-by-table accounting and race analysis).

    **Not a ban.** A client that reports again under this same ``client_id``
    simply reappears with a fresh diff chain, as documented in
    ``api/README.md``'s "Deleting a client" section — this exists for the
    rename-cleanup case AG-0 introduced (an operator renamed a machine's
    agent identity and the old name would otherwise sit in ``GET /v1/clients``
    forever), not to block a machine from ever reporting again.

    404 if the client_id has no rows at all — same "nothing to act on" shape
    as ``DELETE /v1/mapping/{depotid}/{appid}``.
    """
    with open_db() as conn:
        existed = agent_reports.delete_client(conn, client_id)
    if not existed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown client_id {client_id!r}",
        )
