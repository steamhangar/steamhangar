"""Clients overview and server-side throughput (WP D15b, roadmap D15 + D13).

Three read-only endpoints over the traffic tiers ``vault_api/traffic.py``
fills from vault-core's event log:

* ``GET /v1/clients/overview`` -- every client the vault knows: agent clients
  (``agent_reports``) merged with every address that appears in the event
  log, including machines without an agent;
* ``GET /v1/throughput`` -- the Downloads header: current, average and peak
  speed, bytes loaded, bytes saved by the cache, hit rate, active clients,
  and a speed per running prefill job;
* ``GET /v1/throughput/series`` -- the same numbers per bucket for graphs.

All three take ``range`` = ``24h`` | ``7d`` | ``30d`` | ``all``; the range
picks the tier (``24h`` -> 5-minute rows, ``7d``/``30d`` -> hourly rows,
``all`` -> daily rows), see ``traffic.range_spec``.

Fail-soft, and honest about it
------------------------------
Every response carries ``event_feed``. With ``VAULT_EVENT_LOG_PATH`` empty
(``state: "disabled"``) every traffic field is ``null`` -- not ``0``: zero
would claim "nothing was loaded", when in truth nothing was measured. Agent
clients are still listed (their presence and installed games do not depend
on the event log). ``"never-swept"`` and ``"stale"`` (last sweep older than
three intervals) keep the stored totals but report ``current`` speed,
``loading_now`` and ``active_clients_now`` as ``null``.

Address -> agent mapping
------------------------
The event log knows addresses only. Per agent report the addresses are
``agent_reports.effective_addrs`` (WP AGENT-ADDR-1): the LAN addresses the
agent stated (``local_addrs``, schema v24), else the TCP peer the report
arrived from (``source_addr``, schema v9) unless that peer is a forwarding
hop (``vault_api/peer_hops.py`` -- behind Docker's published port the
bridge gateway, shared by every PC). Each address is assigned to the ONE
agent client whose newest report correlates on it
(``agent_reports.address_owners``), so a DHCP address that moved from one PC
to another counts for the PC that has it now. Every other logged address is
an agentless client (``kind: "address"``). An agent from before
AGENT-ADDR-1 behind a reverse proxy or published port gets no address; the
PC then appears twice (agent entry without cache traffic, address entry
with it) until its agent is updated.

Names (WP D15-NAMES): every client carries ``name``, ``name_addr`` and
``display_name``, resolved by ``client_names.resolve_agent`` /
``resolve_address`` -- the same functions ``GET /v1/clients`` uses, on the
same ``address_owners`` map.

Known gap: the prefill runner's own downloads appear as an ordinary address
client and count as an active client. Its address as vault-core logs it
depends on the host's routing/NAT and cannot be derived deterministically
here (api/README.md, "Clients overview and throughput"); identifying it
needs a decision (operator-set address or a runner probe).

Auth is attached at the router level.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from vault_api import agent_reports, client_names, event_sweep, peer_hops, traffic
from vault_api.auth import require_api_key
from vault_api.config import Settings
from vault_api.deps import DbOpener, db_opener
from vault_api.jobs import JOB_TYPE_PREFILL, STATUS_RUNNING, parse_utc_iso, to_utc_iso

router = APIRouter(dependencies=[Depends(require_api_key)], tags=["traffic"])

RangeName = Literal["24h", "7d", "30d", "all"]
FeedState = Literal["disabled", "never-swept", "ok", "stale"]

#: How many recently loaded apps a client lists.
RECENT_APPS_LIMIT = 10

#: Agentless addresses are listed when seen within this period (the hourly
#: tier's retention), whatever the selected range.
ADDRESS_LISTING_DAYS = 30


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------


class EventFeedOut(BaseModel):
    #: False when VAULT_EVENT_LOG_PATH is empty: every traffic field is null.
    enabled: bool
    #: disabled | never-swept | ok | stale (last sweep older than 3 intervals).
    state: FeedState
    last_sweep_at: str | None
    sweep_interval_minutes: int
    #: When the traffic tiers started filling (schema v23 upgrade or first
    #: sweep); null while empty. Nothing before it was recorded.
    traffic_since: str | None


class CurrentOut(BaseModel):
    #: Server-wide bytes per second over [window_start, window_end].
    bytes_per_second: float
    window_start: str
    #: The last sweep -- data is never newer than this.
    window_end: str
    window_seconds: int


class SummaryOut(BaseModel):
    bytes_served: int
    #: Served from the cache's disk -- "saved" from the internet line.
    hit_bytes: int
    #: Served while fetching from upstream (MISS).
    miss_bytes: int
    requests: int
    hits: int
    misses: int
    errors: int
    #: hits / (hits + misses); null without either.
    hit_rate: float | None
    #: hit_bytes / (hit_bytes + miss_bytes); null without either.
    byte_hit_rate: float | None
    #: bytes_served / (active 5-minute buckets x 300 s): the average WHILE
    #: loading, null when nothing was loaded in the range.
    average_bytes_per_second: float | None
    #: Largest server-wide 5-minute total / 300 s.
    peak_bytes_per_second: float
    #: Distinct addresses that loaded bytes in the range. INCLUDES the
    #: prefill runner's address when it downloaded (see module docstring).
    active_clients: int


class JobThroughputOut(BaseModel):
    job_id: int
    appid: int
    name: str | None
    started_at: str | None
    #: Bytes/s on this app's EXCLUSIVE depots (mapped to no other app) over
    #: the current window, from every address. Null when the feed is not
    #: ``ok`` or the app has no exclusive depot mapping yet.
    bytes_per_second: float | None
    #: Bytes on those depots since the job's start, bucket-aligned (5-minute
    #: buckets within 48 h, hourly before that). Null like above.
    bytes_since_start: int | None
    exclusive_depots: int


class ThroughputOut(BaseModel):
    event_feed: EventFeedOut
    range: RangeName
    tier: str
    bucket_seconds: int
    range_start: str | None
    active_window_minutes: int
    current: CurrentOut | None
    #: Addresses with cache activity within active_window_minutes, the
    #: prefill runner's included while a job downloads (module docstring).
    active_clients_now: int | None
    summary: SummaryOut | None
    #: One entry per RUNNING prefill job (never null; values inside may be).
    jobs: list[JobThroughputOut]


class SeriesPointOut(BaseModel):
    #: Bucket start (UTC).
    t: str
    bytes_served: int
    hit_bytes: int
    miss_bytes: int
    requests: int
    #: bytes_served / bucket_seconds (the bucket's wall-clock average).
    bytes_per_second: float
    #: Largest 5-minute total inside the bucket / 300 s.
    peak_bytes_per_second: float


class SeriesOut(BaseModel):
    event_feed: EventFeedOut
    range: RangeName
    tier: str
    bucket_seconds: int
    range_start: str | None
    #: Oldest first, zero-filled up to the current bucket. Empty when the
    #: feed is disabled or (``all``) nothing has been recorded yet.
    points: list[SeriesPointOut]


class ClientStatsOut(BaseModel):
    bytes_served: int
    hit_bytes: int
    miss_bytes: int
    requests: int
    hits: int
    misses: int
    hit_rate: float | None


class LoadingAppOut(BaseModel):
    appid: int
    name: str | None
    bytes_per_second: float


class LoadingNowOut(BaseModel):
    #: bytes_per_second > 0.
    loading: bool
    bytes_per_second: float
    #: Apps attributed through depot_app_map (exactly one app per depot).
    apps: list[LoadingAppOut]
    #: Bytes/s on depots with no or several mapped apps, or no depot id.
    unattributed_bytes_per_second: float


class RecentAppOut(BaseModel):
    appid: int
    name: str | None
    bytes_served: int
    last_seen: str


class ClientActivityOut(BaseModel):
    #: Stable key: "agent:<client_id>" or "addr:<address>".
    key: str
    #: "address" = seen in the event log, no agent owns it. The prefill
    #: runner is one of these today (no reliable way to tell it apart yet).
    kind: Literal["agent", "address"]
    client_id: str | None
    #: Agent: the addresses assigned to it (see module docstring).
    #: Address: exactly that address.
    addresses: list[str]
    #: Agent online, or cache activity within active_window_minutes. Null
    #: only for an address client while the feed is not ``ok``.
    connected: bool | None
    agent_presence: Literal["online", "offline"] | None
    #: Cache activity within active_window_minutes; null when unknown.
    cache_active: bool | None
    #: Newest of last_seen_in_cache_log and last_reported_at.
    last_active: str | None
    last_seen_in_cache_log: str | None
    last_reported_at: str | None
    #: Agent clients only (null for address clients and unreadable snapshots).
    installed_appids: list[int] | None
    installed_app_count: int | None
    #: Over the selected range; null when the feed is disabled.
    stats: ClientStatsOut | None
    #: Null when the feed is not ``ok``.
    loading_now: LoadingNowOut | None
    #: Top apps by bytes over the selected range; null when disabled.
    recent_apps: list[RecentAppOut] | None
    recent_unattributed_bytes: int | None
    #: WP D15-NAMES: the operator-set name, null when none
    #: (``client_names.resolve_agent`` / ``resolve_address``).
    name: str | None
    #: WP D15-NAMES: the address a rename writes to; null = cannot be named
    #: (an agent with no assigned address).
    name_addr: str | None
    #: WP D15-NAMES: ``name`` when set (an explicit name wins), else the
    #: agent's client_id, else the address.
    display_name: str


class ClientsOverviewOut(BaseModel):
    event_feed: EventFeedOut
    range: RangeName
    tier: str
    range_start: str | None
    active_window_minutes: int
    clients: list[ClientActivityOut]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _feed(
    settings: Settings, state: event_sweep.SweepState, now: datetime
) -> tuple[EventFeedOut, traffic.CurrentWindow | None]:
    window = traffic.current_window(
        state.last_sweep_at, now, settings.event_sweep_interval_minutes
    )
    feed_state: FeedState
    if not settings.event_sweep_enabled:
        feed_state = "disabled"
        window = None
    elif state.last_sweep_at is None:
        feed_state = "never-swept"
    elif window is None:
        feed_state = "stale"
    else:
        feed_state = "ok"
    return (
        EventFeedOut(
            enabled=settings.event_sweep_enabled,
            state=feed_state,
            last_sweep_at=state.last_sweep_at,
            sweep_interval_minutes=settings.event_sweep_interval_minutes,
            traffic_since=state.traffic_since,
        ),
        window,
    )


def _ratio(part: int, other: int) -> float | None:
    total = part + other
    return part / total if total > 0 else None


def _summary(summary: traffic.ServerSummary) -> SummaryOut:
    totals = summary.totals
    seconds = summary.active_buckets * traffic.TIER_SECONDS[traffic.TIER_5M]
    return SummaryOut(
        bytes_served=totals.bytes_served,
        hit_bytes=totals.hit_bytes,
        miss_bytes=totals.miss_bytes,
        requests=totals.requests,
        hits=totals.hits,
        misses=totals.misses,
        errors=totals.errors,
        hit_rate=_ratio(totals.hits, totals.misses),
        byte_hit_rate=_ratio(totals.hit_bytes, totals.miss_bytes),
        average_bytes_per_second=(totals.bytes_served / seconds) if seconds else None,
        peak_bytes_per_second=summary.peak_bucket_bytes
        / traffic.TIER_SECONDS[traffic.TIER_5M],
        active_clients=summary.distinct_clients,
    )


def _active_addrs(
    conn: sqlite3.Connection, now: datetime, minutes: int
) -> dict[str, str]:
    """Addresses whose newest line is within ``minutes`` -> that time."""
    since = to_utc_iso(now - timedelta(minutes=minutes))
    rows = conn.execute(
        """
        SELECT client_addr, MAX(last_seen) AS seen FROM client_traffic
        WHERE tier = '5m' AND bucket_at >= ?
        GROUP BY client_addr HAVING MAX(last_seen) >= ?
        """,
        (traffic.bucket_iso(now - timedelta(minutes=minutes), traffic.TIER_5M), since),
    ).fetchall()
    return {str(row["client_addr"]): str(row["seen"]) for row in rows}


def _job_throughput(
    conn: sqlite3.Connection,
    window: traffic.CurrentWindow | None,
    now: datetime,
    feed_on: bool,
) -> list[JobThroughputOut]:
    rows = conn.execute(
        "SELECT id, appid, started_at FROM jobs WHERE status = ? AND type = ? ORDER BY id",
        (STATUS_RUNNING, JOB_TYPE_PREFILL),
    ).fetchall()
    names = traffic.app_names(conn, [int(row["appid"]) for row in rows])
    result: list[JobThroughputOut] = []
    for row in rows:
        appid = int(row["appid"])
        started_at = row["started_at"]
        depots = traffic.exclusive_depots_for_app(conn, appid)
        rate: float | None = None
        since_start: int | None = None
        if feed_on and depots:
            if window is not None:
                rate = (
                    traffic.depots_bytes_since(conn, depots, traffic.TIER_5M, window.start)
                    / window.seconds
                )
            started = parse_utc_iso(started_at) if isinstance(started_at, str) else None
            if started is not None:
                recent = now - started <= traffic.TIER_RETENTION[traffic.TIER_5M]  # type: ignore[operator]
                tier = traffic.TIER_5M if recent else traffic.TIER_1H
                since_start = traffic.depots_bytes_since(
                    conn, depots, tier, traffic.bucket_iso(started, tier)
                )
        result.append(
            JobThroughputOut(
                job_id=int(row["id"]),
                appid=appid,
                name=names.get(appid),
                started_at=started_at if isinstance(started_at, str) else None,
                bytes_per_second=rate,
                bytes_since_start=since_start,
                exclusive_depots=len(depots),
            )
        )
    return result


def _sum_totals(items: list[traffic.Totals]) -> traffic.Totals:
    seen = [item.last_seen for item in items if item.last_seen]
    return traffic.Totals(
        requests=sum(item.requests for item in items),
        hits=sum(item.hits for item in items),
        misses=sum(item.misses for item in items),
        bypasses=sum(item.bypasses for item in items),
        errors=sum(item.errors for item in items),
        bytes_served=sum(item.bytes_served for item in items),
        hit_bytes=sum(item.hit_bytes for item in items),
        miss_bytes=sum(item.miss_bytes for item in items),
        last_seen=max(seen) if seen else None,
    )


def _stats(totals: traffic.Totals) -> ClientStatsOut:
    return ClientStatsOut(
        bytes_served=totals.bytes_served,
        hit_bytes=totals.hit_bytes,
        miss_bytes=totals.miss_bytes,
        requests=totals.requests,
        hits=totals.hits,
        misses=totals.misses,
        hit_rate=_ratio(totals.hits, totals.misses),
    )


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------


@router.get("/v1/throughput", response_model=ThroughputOut)
def get_throughput(
    request: Request,
    range: RangeName = Query(default="24h"),  # noqa: A002 - the API's word
    open_db: DbOpener = Depends(db_opener),
) -> ThroughputOut:
    """Downloads header numbers (D13 SPEED-1 + D15)."""
    settings: Settings = request.app.state.settings
    now = datetime.now(timezone.utc)
    minutes = traffic.active_window_minutes(settings.event_sweep_interval_minutes)
    with open_db() as conn:
        state = event_sweep.read_state(conn)
        feed, window = _feed(settings, state, now)
        spec = traffic.range_spec(conn, range, now)
        feed_on = feed.enabled
        current: CurrentOut | None = None
        active_now: int | None = None
        summary: SummaryOut | None = None
        if feed_on:
            summary = _summary(traffic.server_summary(conn, spec))
            if window is not None:
                current = CurrentOut(
                    bytes_per_second=traffic.server_bytes_since(conn, window.start)
                    / window.seconds,
                    window_start=window.start,
                    window_end=window.end,
                    window_seconds=window.seconds,
                )
                active_now = len(_active_addrs(conn, now, minutes))
        jobs = _job_throughput(conn, window, now, feed_on)
    return ThroughputOut(
        event_feed=feed,
        range=range,
        tier=spec.tier,
        bucket_seconds=spec.bucket_seconds,
        range_start=spec.start,
        active_window_minutes=minutes,
        current=current,
        active_clients_now=active_now,
        summary=summary,
        jobs=jobs,
    )


@router.get("/v1/throughput/series", response_model=SeriesOut)
def get_throughput_series(
    request: Request,
    range: RangeName = Query(default="24h"),  # noqa: A002
    open_db: DbOpener = Depends(db_opener),
) -> SeriesOut:
    """Server-wide traffic per bucket for the Downloads graphs."""
    settings: Settings = request.app.state.settings
    now = datetime.now(timezone.utc)
    with open_db() as conn:
        state = event_sweep.read_state(conn)
        feed, _window = _feed(settings, state, now)
        spec = traffic.range_spec(conn, range, now)
        points = traffic.series(conn, spec) if feed.enabled else []
    five = traffic.TIER_SECONDS[traffic.TIER_5M]
    return SeriesOut(
        event_feed=feed,
        range=range,
        tier=spec.tier,
        bucket_seconds=spec.bucket_seconds,
        range_start=spec.start,
        points=[
            SeriesPointOut(
                t=point.bucket_at,
                bytes_served=point.totals.bytes_served,
                hit_bytes=point.totals.hit_bytes,
                miss_bytes=point.totals.miss_bytes,
                requests=point.totals.requests,
                bytes_per_second=point.totals.bytes_served / spec.bucket_seconds,
                peak_bytes_per_second=point.peak_bucket_bytes / five,
            )
            for point in points
        ],
    )


@router.get("/v1/clients/overview", response_model=ClientsOverviewOut)
def get_clients_overview(
    request: Request,
    range: RangeName = Query(default="7d"),  # noqa: A002
    open_db: DbOpener = Depends(db_opener),
) -> ClientsOverviewOut:
    """Agent clients merged with every event-log address (D15 Clients page)."""
    settings: Settings = request.app.state.settings
    now = datetime.now(timezone.utc)
    minutes = traffic.active_window_minutes(settings.event_sweep_interval_minutes)

    with open_db() as conn:
        state = event_sweep.read_state(conn)
        feed, window = _feed(settings, state, now)
        spec = traffic.range_spec(conn, range, now)
        feed_on = feed.enabled
        live = feed_on and window is not None

        # One routing-table snapshot for both lookups, so the agent list and
        # the address mapping can never judge the same peer differently.
        is_hop = peer_hops.forwarding_hop_filter()
        summaries = agent_reports.list_clients(conn, is_hop)
        snapshots = {
            summary.client_id: agent_reports.latest_snapshot(conn, summary.client_id)
            for summary in summaries
        }
        owners = agent_reports.address_owners(conn, is_hop)
        client_name_map = client_names.ip_names(conn)

        totals: dict[str, traffic.Totals] = {}
        last_seen: dict[str, str] = {}
        listed: set[str] = set()
        recent: dict[str, dict[int, tuple[int, str]]] = {}
        current: dict[str, dict[int, tuple[int, str]]] = {}
        current_bytes: dict[str, int] = {}
        active: dict[str, str] = {}
        if feed_on:
            totals = traffic.client_totals(conn, spec.tier, spec.start)
            last_seen = traffic.last_seen_by_addr(conn)
            listed = traffic.addrs_seen_since(
                conn,
                traffic.bucket_iso(now - timedelta(days=ADDRESS_LISTING_DAYS), traffic.TIER_1H),
            ) | set(totals)
            if spec.start is not None:
                recent = traffic.depot_bytes(conn, spec.tier, spec.start)
            if window is not None:
                current = traffic.depot_bytes(conn, traffic.TIER_5M, window.start)
                current_bytes = {
                    addr: value.bytes_served
                    for addr, value in traffic.client_totals(
                        conn, traffic.TIER_5M, window.start
                    ).items()
                }
                active = _active_addrs(conn, now, minutes)
        depots = {d for per in (*recent.values(), *current.values()) for d in per}
        depot_owners = traffic.depot_owners(conn, depots)
        app_ids = {a for apps in depot_owners.values() if len(apps) == 1 for a in apps}
        names = traffic.app_names(conn, app_ids)

    def loading(addrs: list[str]) -> LoadingNowOut | None:
        if not live or window is None:
            return None
        merged: dict[int, tuple[int, str]] = {}
        for addr in addrs:
            for depotid, (size, seen) in current.get(addr, {}).items():
                old = merged.get(depotid, (0, ""))
                merged[depotid] = (old[0] + size, max(old[1], seen))
        attribution = traffic.attribute(merged, depot_owners)
        total = sum(current_bytes.get(addr, 0) for addr in addrs)
        attributed = sum(app.bytes_served for app in attribution.apps)
        return LoadingNowOut(
            loading=total > 0,
            bytes_per_second=total / window.seconds,
            apps=[
                LoadingAppOut(
                    appid=app.appid,
                    name=names.get(app.appid),
                    bytes_per_second=app.bytes_served / window.seconds,
                )
                for app in attribution.apps
            ],
            unattributed_bytes_per_second=max(0, total - attributed) / window.seconds,
        )

    def recent_apps(addrs: list[str]) -> tuple[list[RecentAppOut] | None, int | None]:
        if not feed_on:
            return None, None
        merged: dict[int, tuple[int, str]] = {}
        for addr in addrs:
            for depotid, (size, seen) in recent.get(addr, {}).items():
                old = merged.get(depotid, (0, ""))
                merged[depotid] = (old[0] + size, max(old[1], seen))
        attribution = traffic.attribute(merged, depot_owners)
        return (
            [
                RecentAppOut(
                    appid=app.appid,
                    name=names.get(app.appid),
                    bytes_served=app.bytes_served,
                    last_seen=app.last_seen,
                )
                for app in attribution.apps[:RECENT_APPS_LIMIT]
            ],
            attribution.unattributed_bytes,
        )

    def cache_view(addrs: list[str]) -> tuple[bool | None, str | None, ClientStatsOut | None]:
        if not feed_on:
            return None, None, None
        seen = [last_seen[a] for a in addrs if a in last_seen]
        cache_active = any(a in active for a in addrs) if live else None
        return (
            cache_active,
            max(seen) if seen else None,
            _stats(_sum_totals([totals[a] for a in addrs if a in totals])),
        )

    clients: list[ClientActivityOut] = []
    for summary in summaries:
        addrs = sorted(a for a, owner in owners.items() if owner == summary.client_id)
        presence, _offline_after = agent_reports.presence(
            summary.last_reported_at, summary.report_interval_seconds, now
        )
        cache_active, seen_in_log, stats = cache_view(addrs)
        apps, unattributed = recent_apps(addrs)
        snapshot = snapshots.get(summary.client_id)
        installed = snapshot.appids if snapshot is not None else None
        named = client_names.resolve_agent(client_name_map, owners, summary.client_id)
        clients.append(
            ClientActivityOut(
                key=f"agent:{summary.client_id}",
                kind="agent",
                client_id=summary.client_id,
                addresses=addrs,
                connected=presence == "online" or bool(cache_active),
                agent_presence=presence,
                cache_active=cache_active,
                last_active=max(
                    (t for t in (seen_in_log, summary.last_reported_at) if t), default=None
                ),
                last_seen_in_cache_log=seen_in_log,
                last_reported_at=summary.last_reported_at,
                installed_appids=installed,
                installed_app_count=None if installed is None else len(installed),
                stats=stats,
                loading_now=loading(addrs),
                recent_apps=apps,
                recent_unattributed_bytes=unattributed,
                name=named.name,
                name_addr=named.name_addr,
                display_name=named.display_name,
            )
        )

    for addr in sorted(listed - set(owners)):
        cache_active, seen_in_log, stats = cache_view([addr])
        apps, unattributed = recent_apps([addr])
        named = client_names.resolve_address(client_name_map, addr)
        clients.append(
            ClientActivityOut(
                key=f"addr:{addr}",
                kind="address",
                client_id=None,
                addresses=[addr],
                connected=cache_active,
                agent_presence=None,
                cache_active=cache_active,
                last_active=seen_in_log,
                last_seen_in_cache_log=seen_in_log,
                last_reported_at=None,
                installed_appids=None,
                installed_app_count=None,
                stats=stats,
                loading_now=loading([addr]),
                recent_apps=apps,
                recent_unattributed_bytes=unattributed,
                name=named.name,
                name_addr=named.name_addr,
                display_name=named.display_name,
            )
        )

    clients.sort(key=lambda c: (not c.connected, _desc(c.last_active), c.key))
    return ClientsOverviewOut(
        event_feed=feed,
        range=range,
        tier=spec.tier,
        range_start=spec.start,
        active_window_minutes=minutes,
        clients=clients,
    )


def _desc(value: str | None) -> tuple[int, ...]:
    """Sort key: newest timestamp first, ``None`` last."""
    if value is None:
        return (1,)
    return (0, *(-ord(character) for character in value))
