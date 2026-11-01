"""Clients overview and server-side throughput (WP D15b, roadmap D15 + D13).

Three read-only endpoints over the traffic tiers ``vault_api/traffic.py``
fills from vault-core's event log:

* ``GET /v1/clients/overview`` -- every client the vault knows: agent clients
  (``agent_reports``) merged with every address that appears in the event
  log, including machines without an agent;
* ``GET /v1/throughput`` -- the Downloads header: current, average and peak
  speed, bytes loaded, bytes saved by the cache, hit rate, active clients,
  and a speed per running prefill job;
* ``GET /v1/throughput/series`` -- the same numbers per bucket for graphs;
* ``GET /v1/throughput/live`` -- WP GRAPH-LIVE: one stitched series (5 s
  points for the last minute, 15 s up to 5 minutes, then the persisted
  1-minute tier up to an hour back) plus a ``current`` speed, from the
  in-memory live tier (``vault_api/live_tier.py``). Polling it is what makes
  the server tail the event log every ~2 s (only while somebody polls).

WP GRAPH-RECENT-1 + LOADING-NOW-1: the persisted ranges poll the live tier
too. ``/v1/throughput`` carries a ``now`` block (current speed, "loading
now" and the runner flag, all over the one live "now" window the Clients
page's ``live_now`` uses) next to the swept ``current`` it keeps for older
clients, and ``/v1/throughput/series`` stitches the ring into its newest 1-
and 5-minute buckets (``1h``/``6h``/``24h``), so the graph shows the minutes
since the last sweep and agrees with the header. The range's summary tiles
(served, average, peak, hit rate) stay swept, as in the live view.

WP NOW-LOADING-1: ``now.loading_sources`` (``/v1/throughput``) and
``loading_sources`` (``/v1/throughput/live``, same rows) say WHAT loads in
that window: one row per source -- the prefill runner with its running job
(or none) and its phase (download / verify), each client as the Clients page
names it with the games it loads, and the bytes no address explains. Speed,
counts and rows come from one read of the ring (``LiveTier.now_view``).

WP CLIENTS-LIVE-1: the clients overview polls the live tier too. Each entry
carries ``live_now`` (the shape of ``loading_now``, read from the ring per
address instead of the swept window, over the current-speed window only) and
the answer a ``live`` block; both null when the feed is disabled.
``loading_now`` keeps its swept meaning for older clients.

All three take ``range`` = ``24h`` | ``7d`` | ``30d`` | ``all``; the two
throughput endpoints also take ``1h`` | ``6h`` (GRAPH-ZOOM). The range picks
the tier (``1h``/``6h`` -> 1-minute rows, ``24h`` -> 5-minute rows,
``7d``/``30d`` -> hourly rows, ``all`` -> daily rows), see
``traffic.range_spec``.

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

The prefill runner (WP D15-RUNNER)
----------------------------------
The runner marks its source address through vault-core before each prefill
(``runner_identity``); its rows are selected by ``runner_identity.windows``
and kept apart everywhere a figure describes CLIENTS:

* ``/v1/throughput``: speed figures (``current``, average, peak, the series'
  ``bytes_served``/``bytes_per_second``) and ``summary.bytes_served`` are
  everything the cache served, the runner included -- that is real load on
  the cache and the line. Every cache-effectiveness and client figure
  (``hit_bytes`` = "saved by the cache", ``miss_bytes``, the hit rates,
  request counts, ``client_bytes_served``, ``active_clients``,
  ``active_clients_now``, the series' ``hit_bytes``/``miss_bytes``/
  ``requests``) is clients only. A runner HIT saves nothing (a forced verify
  run reads every chunk it already has), and a runner MISS is the prefill
  itself, not a client missing the cache. The runner's own share is
  ``summary.prefill_runner`` and the series' ``runner_bytes``, so
  ``bytes_served == client_bytes_served + prefill_runner.bytes_served``.
  WP RUNNER-SPLIT-1: the series points (both tiers and the live ring) carry
  ``runner_hit_bytes``/``runner_miss_bytes`` and every ``current`` the
  ``runner_hit``/``runner_miss_bytes_per_second``, so the graph can count a
  verifying runner's HITs as "served from cache" and the pill can tell a
  runner that downloads from one that only verifies. Additive fields only.
* ``/v1/clients/overview``: one ``kind: "runner"`` entry ("Prefill runner")
  carries every runner row; agent and address entries carry only the rest.
  Each traffic row is in exactly one entry, so the entries' ``stats`` add up
  to the same totals ``/v1/throughput`` reports.

Auth is attached at the router level.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from vault_api import (
    agent_reports,
    cache_completeness,
    client_names,
    depot_preview,
    event_sweep,
    live_tier,
    peer_hops,
    runner_identity,
    traffic,
)
from vault_api.auth import require_api_key
from vault_api.config import Settings
from vault_api import deps
from vault_api.deps import DbOpener, db_opener
from vault_api.jobs import JOB_TYPE_PREFILL, STATUS_RUNNING, parse_utc_iso

router = APIRouter(dependencies=[Depends(require_api_key)], tags=["traffic"])

RangeName = Literal["24h", "7d", "30d", "all"]
#: GRAPH-ZOOM: the throughput endpoints additionally answer 1 h and 6 h from
#: the 1-minute tier. The clients overview keeps ``RangeName`` (no per-depot
#: rows in that tier).
ThroughputRangeName = Literal["1h", "6h", "24h", "7d", "30d", "all"]
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
    #: GRAPH-ZOOM: when the 1-minute tier (1 h / 6 h graph ranges) started
    #: filling; null until the first sweep after the v29 upgrade wrote rows.
    traffic_1m_since: str | None = None


class CurrentOut(BaseModel):
    #: Server-wide bytes per second over [window_start, window_end].
    bytes_per_second: float
    window_start: str
    #: The last sweep -- data is never newer than this.
    window_end: str
    window_seconds: int
    #: WP METRIC-AUDIT: the prefill runner's part of ``bytes_per_second``
    #: (the latter is ALL traffic, runner included).
    runner_bytes_per_second: float
    #: WP RUNNER-SPLIT-1: the runner's part split by cache status: HIT bytes
    #: (chunks the cache already had, e.g. a verify) and MISS bytes (fetched
    #: from upstream: the prefill downloads). BYPASS is in neither, so
    #: hit + miss <= ``runner_bytes_per_second``. Absent before rc21.
    runner_hit_bytes_per_second: float = 0.0
    runner_miss_bytes_per_second: float = 0.0


class RunnerTrafficOut(BaseModel):
    """The prefill runner's share of a range (WP D15-RUNNER)."""

    bytes_served: int
    #: Chunks the runner read that were already cached (verify reads).
    hit_bytes: int
    #: Chunks the runner fetched from upstream (the prefill itself).
    miss_bytes: int
    requests: int
    hits: int
    misses: int
    errors: int
    #: WP METRIC-AUDIT: hit_bytes / (hit_bytes + miss_bytes); null without either.
    byte_hit_rate: float | None = None


class SummaryOut(BaseModel):
    #: Everything the cache served in the range, the prefill runner included.
    bytes_served: int
    #: Served to CLIENTS from the cache's disk -- "saved" from the internet
    #: line. The runner's HITs are not savings and are excluded.
    hit_bytes: int
    #: Served to clients while fetching from upstream (MISS), runner excluded.
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
    #: Distinct addresses that loaded bytes in the range, the prefill
    #: runner's rows excluded (module docstring).
    active_clients: int
    #: WP D15-RUNNER: bytes served to clients, the runner excluded.
    #: ``bytes_served`` (everything) = this + ``prefill_runner.bytes_served``.
    client_bytes_served: int
    #: WP D15-RUNNER: the prefill runner's own traffic in the range.
    prefill_runner: RunnerTrafficOut
    #: WP TILE-INTERNET: bytes that missed the cache and came from the
    #: internet, the prefill runner INCLUDED (the all-scope twin of
    #: ``miss_bytes``): ``miss_bytes + prefill_runner.miss_bytes``.
    internet_bytes: int


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
    #: Depots mapped to this app at all, exclusive or shared (WP
    #: MAP-FROM-MANIFEST-1): lets a client tell "the server knows no depots
    #: of this game" (0) from "all of them are shared" (> 0 with
    #: ``exclusive_depots`` 0).
    mapped_depots: int = 0
    #: WP JOB-SIZE-1, additive. ``true`` when the server knows no depots of
    #: this game yet (``mapped_depots`` 0, a first download) and the D19 depot
    #: preview (oracle, both switches on) supplied the EXPECTED depots instead:
    #: ``bytes_per_second`` and ``bytes_since_start`` then count the PREFILL
    #: RUNNER's bytes on those expected depots (only the ones no app has
    #: mapped; a household PC loading the same game is not job progress), and
    #: the two ``expected_*`` sizes are the oracle's public-branch totals of
    #: exactly those depots. An estimate until the run's own mapping exists;
    #: nothing is written to ``depot_app_map``. ``false`` (and the rest
    #: 0/null) otherwise, also when the preview is off, failed or pending.
    expected: bool = False
    #: How many expected depots the figures count (0 unless ``expected``).
    expected_depots: int = 0
    #: Expected installed size (oracle ``size``), null when unknown.
    expected_size_bytes: int | None = None
    #: Expected download size (oracle ``download``), the figure comparable to
    #: ``bytes_since_start`` (the cache stores the downloaded chunks).
    expected_download_bytes: int | None = None
    #: WP JOB-SIZE-1 (ETA), additive. The prefill runner's MISS bytes/s on
    #: the counted depots over the swept window (what the cache fetched from
    #: the internet for this job). A Verify or cache-only run has 0 here.
    #: Null like ``bytes_per_second``.
    miss_bytes_per_second: float | None = None
    #: Estimated bytes this run still has to load (runner traffic only), or
    #: null when unknown. ``remaining_source`` ``preview``: a first download,
    #: ``expected_download_bytes`` minus the runner's bytes so far, HIT or
    #: MISS (every chunk is requested once, so this is chunks still to
    #: REQUEST). ``completeness``: a mapped game, the stored completeness
    #: figure of its exclusive depots (current target manifests: expected
    #: minus present at the check) minus the runner's MISS bytes since the
    #: later of the check and the job's start (bytes still to FETCH from the
    #: internet); unknown when any of those depots is unmeasured for its
    #: current target.
    remaining_bytes: int | None = None
    remaining_source: Literal["preview", "completeness"] | None = None
    #: The speed an ETA divides ``remaining_bytes`` by, matched to the
    #: source: ``preview`` -> the runner's total bytes/s, ``completeness`` ->
    #: the runner's MISS bytes/s. ``eta_speed_source`` ``live``: the live
    #: tier's runner speed (``now.current``, a ~15-20 s window; only the
    #: running prefill job, the runner loads one job at a time). ``window``:
    #: the live tier is not available, so the job's own swept figure, and
    #: only once that window starts at or after the job's start (an older
    #: window would dilute the speed with time before the job). Null with no
    #: remainder or no undiluted speed. ``ETA = remaining_bytes /
    #: eta_bytes_per_second``; clients smooth it over polls.
    eta_bytes_per_second: float | None = None
    eta_speed_source: Literal["live", "window"] | None = None


class LiveCurrentOut(BaseModel):
    bytes_per_second: float
    runner_bytes_per_second: float
    window_start: str
    window_end: str
    window_seconds: int
    #: WP RUNNER-SPLIT-1: as in ``CurrentOut``, from the ring.
    runner_hit_bytes_per_second: float = 0.0
    runner_miss_bytes_per_second: float = 0.0


class LoadingJobOut(BaseModel):
    """The prefill job the runner row belongs to (WP NOW-LOADING-1)."""

    job_id: int
    appid: int
    name: str | None
    #: ``cancel`` / ``pause`` while an operator's stop request waits for the
    #: runner to end SteamPrefill (the job is still ``running`` until then);
    #: null otherwise.
    stop_request: str | None = None


class LoadingSourceOut(BaseModel):
    """One source of the traffic in the live "now" window (WP NOW-LOADING-1):
    what is loading right now, and for whom.

    ``runner``: the server's prefill runner (at most one row, all its
    addresses together); ``job`` is the running prefill job, null when the
    runner has traffic in the window but no prefill job is running (e.g. the
    last lines of a job that just ended or was cancelled). ``client``: one
    row per client as the Clients page lists it (an agent with all its
    addresses, or one agentless address). ``unattributed``: served bytes in
    the window that no address explains (more distinct addresses in one
    5-second cell than the live tier keeps apart); no name, no games.
    """

    kind: Literal["runner", "client", "unattributed"]
    #: The Clients overview's key: ``runner``, ``agent:<client_id>``,
    #: ``addr:<address>``; ``unattributed`` for that row.
    key: str
    #: The Clients page's ``display_name`` ("Prefill runner" for the runner);
    #: null for the unattributed row.
    name: str | None
    addresses: list[str]
    #: False for an address the Clients page does not list yet (no agent, no
    #: name, no swept traffic in the listing period): a new device loading
    #: before its first sweep. True otherwise; an operator-named address is
    #: known even without any swept traffic. Known gap: runner traffic before
    #: the first sweep of a UTC day is outside every runner window yet and
    #: shows as such a row with the runner's address (api/README.md).
    known: bool = True
    #: Served bytes per second over the window, and their HIT (from the
    #: cache's disk) / MISS (from the internet) parts; BYPASS is in neither.
    bytes_per_second: float
    hit_bytes_per_second: float
    miss_bytes_per_second: float
    #: Games with bytes in the window (``depot_app_map``: exactly one mapped
    #: app per depot), each with its speed, fastest first.
    apps: list[LoadingAppOut]
    #: Bytes per second on depots linked to no single game, or without one.
    unattributed_bytes_per_second: float
    #: Runner only: ``verify`` when every runner byte in the window was a
    #: cache HIT (the pill's "Verifying" rule), else ``download``.
    phase: Literal["download", "verify"] | None = None
    #: Runner only: the running prefill job, or null (see above).
    job: LoadingJobOut | None = None


class ThroughputNowOut(BaseModel):
    """The page's "now" figures from the live tier (WP GRAPH-RECENT-1,
    LOADING-NOW-1): seconds old, not a sweep. One window for all of them,
    the live "Current speed" window (``window_seconds``), the same one the
    Clients page's ``live_now`` reads."""

    current: LiveCurrentOut
    #: Client addresses that were served bytes in the window, the prefill
    #: runner excluded.
    active_clients: int
    #: The prefill runner was served bytes in the window.
    runner_active: bool
    window_seconds: int
    #: Minutes from here on are fully in the ring (``/v1/throughput/live``).
    live_since: str | None
    #: WP NOW-LOADING-1: what is loading in the window, one row per source
    #: (runner first, then clients by speed, unattributed last); empty when
    #: nothing loads. Same shape as ``/v1/throughput/live``'s field of the
    #: same name. Absent before rc21.
    loading_sources: list[LoadingSourceOut] | None = None


class ThroughputOut(BaseModel):
    event_feed: EventFeedOut
    range: ThroughputRangeName
    tier: str
    bucket_seconds: int
    range_start: str | None
    active_window_minutes: int
    current: CurrentOut | None
    #: Addresses with cache activity within active_window_minutes, the
    #: prefill runner excluded (module docstring).
    active_clients_now: int | None
    #: WP D15-RUNNER: the runner loaded from the cache within
    #: active_window_minutes. Null when the feed is not ``ok``.
    runner_active_now: bool | None
    summary: SummaryOut | None
    #: One entry per RUNNING prefill job (never null; values inside may be).
    jobs: list[JobThroughputOut]
    #: WP GRAPH-RECENT-1 / LOADING-NOW-1: ``current``, ``active_clients_now``
    #: and ``runner_active_now`` read from the live tier instead of the last
    #: sweep. Null when the feed is not ``ok``; absent before rc21.
    now: ThroughputNowOut | None = None


class SeriesPointOut(BaseModel):
    #: Bucket start (UTC).
    t: str
    #: Everything served in the bucket, the prefill runner included.
    bytes_served: int
    #: Clients only (WP D15-RUNNER), as in ``SummaryOut``.
    hit_bytes: int
    miss_bytes: int
    requests: int
    #: WP D15-RUNNER: the prefill runner's bytes in the bucket.
    runner_bytes: int
    #: bytes_served / bucket_seconds (the bucket's wall-clock average). WP
    #: RUNNER-SPLIT-1 (GRAPH-RECENT-1 nit N1): the running bucket, when it
    #: comes from the ring, divides by the seconds elapsed in it instead, so
    #: early in a bucket the newest point is not far below the header speed.
    bytes_per_second: float
    #: Largest 5-minute total inside the bucket / 300 s (the 1-minute tier's
    #: rows are their own peak: bytes / 60 s).
    peak_bytes_per_second: float
    #: WP GRAPH-RECENT-1: ``persisted`` (the swept tier) or ``live`` (the
    #: in-memory ring, for a newest bucket the sweep has not fully read yet;
    #: such a bucket is its own peak).
    source: Literal["persisted", "live"] = "persisted"
    #: WP RUNNER-SPLIT-1: ``runner_bytes`` split by cache status (HIT = read
    #: from the cache, e.g. a verify; MISS = fetched from the internet). The
    #: graph's "served from cache" is ``hit_bytes + runner_hit_bytes``.
    #: BYPASS is in neither. Absent before rc21.
    runner_hit_bytes: int = 0
    runner_miss_bytes: int = 0


class SeriesOut(BaseModel):
    event_feed: EventFeedOut
    range: ThroughputRangeName
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
    #: Stable key: "agent:<client_id>", "addr:<address>" or "runner".
    key: str
    #: "address" = seen in the event log, no agent owns it. "runner" = the
    #: server's own prefill runner (WP D15-RUNNER), at most one entry.
    kind: Literal["agent", "address", "runner"]
    client_id: str | None
    #: Agent: the addresses assigned to it (see module docstring).
    #: Address: exactly that address. Runner: every address it marked whose
    #: window overlaps the listing period, newest window last.
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
    #: (an agent with no assigned address, and the prefill runner).
    name_addr: str | None
    #: WP D15-NAMES: ``name`` when set (an explicit name wins), else the
    #: agent's client_id, else the address.
    display_name: str
    #: WP CLIENTS-LIVE-1: ``loading_now`` from the live tier (seconds old,
    #: not a sweep). ONE window for all of it, the last
    #: ``live.current_window_seconds`` (the live "Current speed" window):
    #: ``loading`` = served bytes on the client's addresses in it,
    #: ``bytes_per_second`` over it, ``apps`` = the games with bytes in it,
    #: each with its speed. A client that paused longer than that window is
    #: not loading (its games stay in ``recent_apps``). Null when the feed
    #: is disabled; absent from servers before rc21.
    live_now: LoadingNowOut | None = None


class ClientsLiveOut(BaseModel):
    """How fresh the entries' ``live_now`` is (WP CLIENTS-LIVE-1)."""

    #: Minutes from here on are fully in the ring (``/v1/throughput/live``).
    live_since: str | None
    #: The window of ``live_now``, seconds (15 s plus the running 5 s cell).
    current_window_seconds: int


class ClientsOverviewOut(BaseModel):
    event_feed: EventFeedOut
    range: RangeName
    tier: str
    range_start: str | None
    active_window_minutes: int
    clients: list[ClientActivityOut]
    #: WP CLIENTS-LIVE-1: null when the feed is disabled.
    live: ClientsLiveOut | None = None


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
            traffic_1m_since=state.traffic_1m_since,
        ),
        window,
    )


def _ratio(part: int, other: int) -> float | None:
    total = part + other
    return part / total if total > 0 else None


def _runner_out(totals: traffic.Totals) -> RunnerTrafficOut:
    return RunnerTrafficOut(
        bytes_served=totals.bytes_served,
        hit_bytes=totals.hit_bytes,
        miss_bytes=totals.miss_bytes,
        requests=totals.requests,
        hits=totals.hits,
        misses=totals.misses,
        errors=totals.errors,
        byte_hit_rate=_ratio(totals.hit_bytes, totals.miss_bytes),
    )


def _summary(
    summary: traffic.ServerSummary, runner: traffic.Totals, tier: str = traffic.TIER_5M
) -> SummaryOut:
    """``summary`` is server-wide; ``runner`` the runner's rows of the same
    range. Client figures are the difference (module docstring)."""
    everything = summary.totals
    totals = traffic.subtract(everything, runner)
    unit = traffic.peak_unit_seconds(tier)
    seconds = summary.active_buckets * unit
    return SummaryOut(
        bytes_served=everything.bytes_served,
        hit_bytes=totals.hit_bytes,
        miss_bytes=totals.miss_bytes,
        requests=totals.requests,
        hits=totals.hits,
        misses=totals.misses,
        errors=totals.errors,
        hit_rate=_ratio(totals.hits, totals.misses),
        byte_hit_rate=_ratio(totals.hit_bytes, totals.miss_bytes),
        average_bytes_per_second=(everything.bytes_served / seconds) if seconds else None,
        peak_bytes_per_second=summary.peak_bucket_bytes / unit,
        active_clients=summary.distinct_clients,
        client_bytes_served=totals.bytes_served,
        prefill_runner=_runner_out(runner),
        internet_bytes=everything.miss_bytes,
    )


#: WP JOB-SIZE-1: where ``remaining_bytes`` comes from.
REMAINING_PREVIEW = "preview"
REMAINING_COMPLETENESS = "completeness"

ExpectFn = Callable[[sqlite3.Connection, int], "depot_preview.JobExpectation | None"]
#: (conn, appid, exclusive depots) -> the stored completeness figure.
FigureFn = Callable[[sqlite3.Connection, int, list[int]], cache_completeness.Completeness]


def _since_tier(now: datetime, moment: datetime) -> str:
    recent = now - moment <= traffic.TIER_RETENTION[traffic.TIER_5M]  # type: ignore[operator]
    return traffic.TIER_5M if recent else traffic.TIER_1H


def _mapped_remaining(
    conn: sqlite3.Connection,
    figure: cache_completeness.Completeness,
    depots: list[int],
    started: datetime,
    now: datetime,
    runner: traffic.Scope | None,
) -> int | None:
    """WP JOB-SIZE-1: internet bytes a mapped app still lacks.

    The stored completeness figure (current target manifests, chunk sizes)
    says what was missing when it was measured; every runner MISS on the
    app's exclusive depots since then (or since the job's start, the later
    of the two) filled part of it. Unknown (``None``) when the figure is.
    """
    if figure.expected_bytes is None or figure.present_bytes is None:
        return None
    missing = max(0, figure.expected_bytes - figure.present_bytes)
    checked = parse_utc_iso(figure.checked_at) if figure.checked_at else None
    since = max(started, checked) if checked is not None else started
    tier = _since_tier(now, since)
    # Rows are per bucket, so "since" rounds DOWN to its bucket start: up to
    # one bucket (5 min) of MISS bytes before it may count as filled. That
    # errs towards a smaller remainder, never towards a stuck one.
    filled = traffic.depots_miss_bytes_since(
        conn, depots, tier, traffic.bucket_iso(since, tier), runner
    )
    return max(0, missing - filled)


def _job_throughput(
    conn: sqlite3.Connection,
    window: traffic.CurrentWindow | None,
    now: datetime,
    feed_on: bool,
    oracle_enabled: bool = False,
    expect: ExpectFn | None = None,
    figure_of: FigureFn | None = None,
    runner: traffic.Scope | None = None,
) -> list[JobThroughputOut]:
    """``expect`` (WP JOB-SIZE-1) answers the expected depots of an app the
    vault has not mapped yet, ``figure_of`` the stored completeness figure of
    a mapped one (for ``remaining_bytes``); ``runner`` scopes the job's own
    progress to the prefill runner's rows (``None``: every address, tests of
    older callers). ``None`` callables leave their fields at the
    pre-JOB-SIZE-1 defaults. The ETA speed is filled in by ``_with_eta``."""
    rows = conn.execute(
        "SELECT id, appid, started_at FROM jobs WHERE status = ? AND type = ? ORDER BY id",
        (STATUS_RUNNING, JOB_TYPE_PREFILL),
    ).fetchall()
    names = traffic.app_names(
        conn, [int(row["appid"]) for row in rows], oracle_enabled=oracle_enabled
    )
    result: list[JobThroughputOut] = []
    for row in rows:
        appid = int(row["appid"])
        started_at = row["started_at"]
        depots = traffic.exclusive_depots_for_app(conn, appid)
        mapped = int(
            conn.execute(
                "SELECT COUNT(*) FROM depot_app_map WHERE appid = ?", (appid,)
            ).fetchone()[0]
        )
        expectation = expect(conn, appid) if expect is not None and mapped == 0 else None
        counted = list(expectation.depotids) if expectation is not None else depots
        # The card's speed and "so far": every address on the app's own
        # depots for a mapped game (as before JOB-SIZE-1); the runner only
        # for expected depots, which nothing has confirmed belong to this job.
        shown = runner if expectation is not None else None
        rate: float | None = None
        miss_rate: float | None = None
        since_start: int | None = None
        remaining: int | None = None
        remaining_source: str | None = None
        if feed_on and counted:
            if window is not None:
                rate = (
                    traffic.depots_bytes_since(
                        conn, counted, traffic.TIER_5M, window.start, shown
                    )
                    / window.seconds
                )
                miss_rate = (
                    traffic.depots_miss_bytes_since(
                        conn, counted, traffic.TIER_5M, window.start, runner
                    )
                    / window.seconds
                )
            started = parse_utc_iso(started_at) if isinstance(started_at, str) else None
            if started is not None:
                tier = _since_tier(now, started)
                start_bucket = traffic.bucket_iso(started, tier)
                since_start = traffic.depots_bytes_since(
                    conn, counted, tier, start_bucket, shown
                )
                if expectation is not None:
                    if expectation.download_bytes is not None:
                        # A first download requests every chunk once, HIT or
                        # MISS, so all the runner loaded so far is progress
                        # (``since_start`` is runner-scoped here).
                        remaining = max(0, expectation.download_bytes - since_start)
                        remaining_source = REMAINING_PREVIEW
                elif figure_of is not None:
                    remaining = _mapped_remaining(
                        conn, figure_of(conn, appid, depots), depots, started, now, runner
                    )
                    remaining_source = REMAINING_COMPLETENESS if remaining is not None else None
        result.append(
            JobThroughputOut(
                job_id=int(row["id"]),
                appid=appid,
                name=names.get(appid),
                started_at=started_at if isinstance(started_at, str) else None,
                bytes_per_second=rate,
                bytes_since_start=since_start,
                exclusive_depots=len(depots),
                mapped_depots=mapped,
                expected=expectation is not None,
                expected_depots=len(expectation.depotids) if expectation else 0,
                expected_size_bytes=expectation.size_bytes if expectation else None,
                expected_download_bytes=expectation.download_bytes if expectation else None,
                miss_bytes_per_second=miss_rate,
                remaining_bytes=remaining,
                remaining_source=remaining_source,
            )
        )
    return result


def _with_eta(
    jobs: list[JobThroughputOut],
    live: LiveCurrentOut | None,
    window: traffic.CurrentWindow | None,
) -> list[JobThroughputOut]:
    """WP JOB-SIZE-1: the ETA speed of each job with a remainder.

    ``live`` (the ring's runner speed) belongs to the running prefill job:
    the runner loads one job at a time, the oldest running one when a crash
    ever left two (``_running_prefill_job``). Without it, the job's swept
    figure is used only when its window started at or after the job did.
    """
    running_id = min((job.job_id for job in jobs), default=None)
    window_start = parse_utc_iso(window.start) if window is not None else None
    result: list[JobThroughputOut] = []
    for job in jobs:
        rate: float | None = None
        source: str | None = None
        preview = job.remaining_source == REMAINING_PREVIEW
        if job.remaining_source is None:
            pass
        elif live is not None and job.job_id == running_id:
            rate = live.runner_bytes_per_second if preview else live.runner_miss_bytes_per_second
            source = "live"
        else:
            started = parse_utc_iso(job.started_at) if job.started_at else None
            if window_start is not None and started is not None and window_start >= started:
                rate = job.bytes_per_second if preview else job.miss_bytes_per_second
                source = "window" if rate is not None else None
        result.append(
            job.model_copy(update={"eta_bytes_per_second": rate, "eta_speed_source": source})
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
    range: ThroughputRangeName = Query(default="24h"),  # noqa: A002 - the API's word
    open_db: DbOpener = Depends(db_opener),
) -> ThroughputOut:
    """Downloads header numbers (D13 SPEED-1 + D15)."""
    settings: Settings = deps.effective_settings(request)
    ring: live_tier.LiveTier = request.app.state.live_tier
    now = ring.clock()
    minutes = traffic.active_window_minutes(settings.event_sweep_interval_minutes)
    with open_db() as conn:
        state = event_sweep.read_state(conn)
        feed, window = _feed(settings, state, now)
        spec = traffic.range_spec(conn, range, now)
        feed_on = feed.enabled
        windows = runner_identity.windows(conn)
        clients = traffic.clients_scope(windows)
        runner = traffic.runner_scope(windows)
        current: CurrentOut | None = None
        active_now: int | None = None
        runner_now: bool | None = None
        summary: SummaryOut | None = None
        now_out: ThroughputNowOut | None = None
        if feed_on:
            summary = _summary(
                traffic.server_summary(conn, spec, clients),
                traffic.scoped_totals(conn, spec.tier, spec.start, runner),
                spec.tier,
            )
            if window is not None:
                runner_window = traffic.scoped_totals(
                    conn, traffic.TIER_5M, window.start, runner
                )
                current = CurrentOut(
                    bytes_per_second=traffic.server_bytes_since(conn, window.start)
                    / window.seconds,
                    window_start=window.start,
                    window_end=window.end,
                    window_seconds=window.seconds,
                    runner_bytes_per_second=runner_window.bytes_served / window.seconds,
                    runner_hit_bytes_per_second=runner_window.hit_bytes / window.seconds,
                    runner_miss_bytes_per_second=runner_window.miss_bytes / window.seconds,
                )
                active_now = len(traffic.active_addrs(conn, now, minutes, clients))
                runner_now = bool(traffic.active_addrs(conn, now, minutes, runner))
        jobs = _job_throughput(
            conn,
            window,
            now,
            feed_on,
            settings.manifest_oracle_enabled,
            _job_expectation(request, settings),
            _job_figure(settings),
            runner,
        )
    # The ring is read after the connection is closed: tailing the log never
    # holds SQLite open.
    if feed_on and window is not None:
        now_out = _ring_now(ring, settings, now, windows, open_db)
    return ThroughputOut(
        event_feed=feed,
        range=range,
        tier=spec.tier,
        bucket_seconds=spec.bucket_seconds,
        range_start=spec.start,
        active_window_minutes=minutes,
        current=current,
        active_clients_now=active_now,
        runner_active_now=runner_now,
        summary=summary,
        jobs=_with_eta(jobs, now_out.current if now_out is not None else None, window),
        now=now_out,
    )


def _job_expectation(
    request: Request, settings: Settings
) -> Callable[[sqlite3.Connection, int], depot_preview.JobExpectation | None] | None:
    """WP JOB-SIZE-1: the preview service's expectation, or ``None`` when the
    preview is off (``VAULT_DEPOT_PREVIEW`` or the oracle) or not wired."""
    service = getattr(request.app.state, "depot_preview", None)
    if not isinstance(service, depot_preview.DepotPreviewService):
        return None
    if not depot_preview.enabled(settings):
        return None
    return lambda conn, appid: service.job_expectation(conn, appid, settings)


def _job_figure(settings: Settings) -> FigureFn:
    """WP JOB-SIZE-1: the stored completeness figure of one app's exclusive
    depots (read-only; no cache walk, no manifest read)."""

    def figure(
        conn: sqlite3.Connection, appid: int, depots: list[int]
    ) -> cache_completeness.Completeness:
        if not depots:
            return cache_completeness.UNKNOWN
        index = cache_completeness.load_index(conn, settings, [appid])
        return index.figure((appid, depot) for depot in depots)

    return figure


def _ring_now(
    ring: live_tier.LiveTier,
    settings: Settings,
    now: datetime,
    windows: tuple[runner_identity.RunnerWindow, ...],
    open_db: DbOpener,
) -> ThroughputNowOut:
    """Arm and tail the live tier, then read the "now" figures from it."""
    ring.touch(now)
    ring.refresh(settings.event_log_path, now)
    # One read of the window for every figure (WP NOW-LOADING-1): speed,
    # counts (the Clients page's rule) and the per-source rows agree.
    view = ring.now_view(now, _runner_test(windows))
    clients, runner = live_tier.loading_counts(view.activity)
    return ThroughputNowOut(
        current=_live_current_out(view.current),
        active_clients=clients,
        runner_active=runner,
        window_seconds=view.current.window_seconds,
        live_since=ring.covered_from(),
        loading_sources=_loading_sources(open_db, settings, view, now),
    )


def _running_prefill_job(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """The running prefill job (one runner, so at most one in practice; the
    oldest if a crash ever left two)."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT id, appid, stop_request FROM jobs WHERE status = ? AND type = ? "
        "ORDER BY id LIMIT 1",
        (STATUS_RUNNING, JOB_TYPE_PREFILL),
    ).fetchone()
    return row


def _loading_sources(
    open_db: DbOpener,
    settings: Settings,
    view: live_tier.NowView,
    now: datetime,
) -> list[LoadingSourceOut]:
    """WP NOW-LOADING-1: ``view`` as one row per source (``LoadingSourceOut``).

    Names come from the same lookups the Clients overview uses
    (``address_owners``, ``client_names``, ``depot_owners`` + ``attribute``,
    ``app_names``). Nothing loads -> ``[]`` without touching SQLite, so an
    idle poll costs what it did before.
    """
    seconds = view.seconds
    # Overflow bytes can come without any listed address: a cell whose 64
    # address slots went to requests without served bytes (heartbeats) and
    # whose served bytes then fell into the overflow entry.
    if not view.activity and view.overflow_bytes <= 0:
        return []
    runner_parts = [(a, e) for (a, r), e in view.activity.items() if r]
    client_parts = {a: e for (a, r), e in view.activity.items() if not r}
    listing_since = traffic.bucket_iso(
        now - timedelta(days=ADDRESS_LISTING_DAYS), traffic.TIER_1H
    )
    with open_db() as conn:
        depot_map = traffic.depot_owners(
            conn, {d for entry in view.activity.values() for d in entry.depots}
        )
        job = _running_prefill_job(conn) if runner_parts else None
        owners: dict[str, str] = {}
        name_map: dict[str, str] = {}
        listed: set[str] = set()
        if client_parts:
            owners = agent_reports.address_owners(conn, peer_hops.forwarding_hop_filter())
            name_map = client_names.ip_names(conn)
            # Client side only, as the Clients page lists it (NITS-API-1).
            listed = traffic.addrs_listed(
                conn,
                [a for a in client_parts if a not in owners],
                listing_since,
                traffic.clients_scope(runner_identity.windows(conn)),
            )
        app_ids = {apps[0] for apps in depot_map.values() if len(apps) == 1}
        if job is not None:
            app_ids.add(int(job["appid"]))
        names = traffic.app_names(
            conn, app_ids, oracle_enabled=settings.manifest_oracle_enabled
        )

    def row(
        kind: Literal["runner", "client"],
        key: str,
        name: str,
        parts: list[tuple[str, live_tier.AddrActivity]],
        known: bool = True,
    ) -> LoadingSourceOut:
        total = sum(e.bytes_served for _a, e in parts)
        hit = sum(e.hit_bytes for _a, e in parts)
        merged: dict[int, tuple[int, str]] = {}
        for _addr, entry in parts:
            for depotid, size in entry.depots.items():
                merged[depotid] = (merged.get(depotid, (0, ""))[0] + size, "")
        attribution = traffic.attribute(merged, depot_map)
        attributed = sum(app.bytes_served for app in attribution.apps)
        return LoadingSourceOut(
            kind=kind,
            key=key,
            name=name,
            addresses=sorted(a for a, _e in parts),
            known=known,
            bytes_per_second=total / seconds,
            hit_bytes_per_second=hit / seconds,
            miss_bytes_per_second=sum(e.miss_bytes for _a, e in parts) / seconds,
            apps=[
                LoadingAppOut(
                    appid=app.appid,
                    name=names.get(app.appid),
                    bytes_per_second=app.bytes_served / seconds,
                )
                for app in attribution.apps
            ],
            unattributed_bytes_per_second=max(0, total - attributed) / seconds,
            # The pill's rule (``heroStatus``): all runner bytes were HITs.
            phase=("verify" if 0 < total <= hit else "download") if kind == "runner" else None,
        )

    out: list[LoadingSourceOut] = []
    if runner_parts:
        runner_row = row("runner", "runner", runner_identity.DISPLAY_NAME, runner_parts)
        if job is not None:
            appid = int(job["appid"])
            stop = job["stop_request"]
            runner_row.job = LoadingJobOut(
                job_id=int(job["id"]),
                appid=appid,
                name=names.get(appid),
                stop_request=str(stop) if stop is not None else None,
            )
        out.append(runner_row)

    groups: dict[str, list[tuple[str, live_tier.AddrActivity]]] = {}
    for addr, entry in client_parts.items():
        owner = owners.get(addr)
        groups.setdefault(f"agent:{owner}" if owner else f"addr:{addr}", []).append((addr, entry))
    clients: list[LoadingSourceOut] = []
    for key, parts in groups.items():
        if key.startswith("agent:"):
            client_id = key[len("agent:"):]
            named = client_names.resolve_agent(name_map, owners, client_id)
            clients.append(row("client", key, named.display_name, parts))
        else:
            addr = parts[0][0]
            named = client_names.resolve_address(name_map, addr)
            clients.append(
                row(
                    "client", key, named.display_name, parts,
                    known=named.name is not None or addr in listed,
                )
            )
    clients.sort(key=lambda item: (-item.bytes_per_second, item.key))
    out.extend(clients)

    if view.overflow_bytes > 0:
        out.append(
            LoadingSourceOut(
                kind="unattributed",
                key="unattributed",
                name=None,
                addresses=[],
                bytes_per_second=view.overflow_bytes / seconds,
                hit_bytes_per_second=view.overflow_hit_bytes / seconds,
                miss_bytes_per_second=view.overflow_miss_bytes / seconds,
                apps=[],
                unattributed_bytes_per_second=view.overflow_bytes / seconds,
            )
        )
    return out


#: WP GRAPH-RECENT-1: the tiers whose newest buckets the ring can hold (a
#: bucket of at most 5 minutes; the ring keeps 15).
_STITCHED_TIERS = (traffic.TIER_1M, traffic.TIER_5M)


def _ring_buckets(
    ring: live_tier.LiveTier,
    settings: Settings,
    now: datetime,
    bucket_seconds: int,
    windows: tuple[runner_identity.RunnerWindow, ...],
) -> dict[int, live_tier.RingBucket]:
    """Arm and tail the live tier, then read its totals per series bucket."""
    ring.touch(now)
    ring.refresh(settings.event_log_path, now)
    return ring.buckets(now, bucket_seconds, _runner_test(windows))


def _series_point(
    point: traffic.SeriesPoint,
    runner_part: traffic.Totals,
    spec: traffic.RangeSpec,
    ring: dict[int, live_tier.RingBucket],
    now_epoch: float | None = None,
) -> SeriesPointOut:
    """One graph point: the swept bucket, or the ring's where the ring holds
    more of it (``live_tier.prefer_ring``, WP GRAPH-RECENT-1).

    One source per point, never a sum, so nothing is counted twice. A ring
    bucket is its own peak (bytes / bucket length, as a 1-minute row is).
    The running ring bucket (``now_epoch`` inside it) holds only the seconds
    elapsed so far, so its rate divides by those (WP RUNNER-SPLIT-1, nit N1
    of GRAPH-RECENT-1), at least one second, as ``LiveTier.current`` does.
    WP RUNNER-SPLIT-1: both sources carry the runner's HIT / MISS bytes.
    """
    when = live_tier.minute_epoch(point.bucket_at) if ring else None
    bucket = ring.get(when) if when is not None else None
    swept = live_tier.LiveTotals(
        requests=point.totals.requests, bytes_served=point.totals.bytes_served
    )
    if bucket is not None and live_tier.prefer_ring(bucket.all, swept):  # bucket implies when
        whole, run = bucket.all, bucket.runner
        seconds: float = spec.bucket_seconds
        if now_epoch is not None and when <= now_epoch < when + spec.bucket_seconds:
            seconds = max(1.0, now_epoch - when)
        rate = whole.bytes_served / seconds
        return SeriesPointOut(
            t=point.bucket_at,
            bytes_served=whole.bytes_served,
            hit_bytes=whole.hit_bytes - run.hit_bytes,
            miss_bytes=whole.miss_bytes - run.miss_bytes,
            requests=whole.requests - run.requests,
            runner_bytes=run.bytes_served,
            runner_hit_bytes=run.hit_bytes,
            runner_miss_bytes=run.miss_bytes,
            bytes_per_second=rate,
            peak_bytes_per_second=rate,
            source="live",
        )
    clients = traffic.subtract(point.totals, runner_part)
    return SeriesPointOut(
        t=point.bucket_at,
        bytes_served=point.totals.bytes_served,
        hit_bytes=clients.hit_bytes,
        miss_bytes=clients.miss_bytes,
        requests=clients.requests,
        runner_bytes=runner_part.bytes_served,
        runner_hit_bytes=runner_part.hit_bytes,
        runner_miss_bytes=runner_part.miss_bytes,
        bytes_per_second=point.totals.bytes_served / spec.bucket_seconds,
        peak_bytes_per_second=point.peak_bucket_bytes / traffic.peak_unit_seconds(spec.tier),
    )


@router.get("/v1/throughput/series", response_model=SeriesOut)
def get_throughput_series(
    request: Request,
    range: ThroughputRangeName = Query(default="24h"),  # noqa: A002
    open_db: DbOpener = Depends(db_opener),
) -> SeriesOut:
    """Server-wide traffic per bucket for the Downloads graphs."""
    settings: Settings = deps.effective_settings(request)
    ring: live_tier.LiveTier = request.app.state.live_tier
    now = ring.clock()
    with open_db() as conn:
        state = event_sweep.read_state(conn)
        feed, window = _feed(settings, state, now)
        spec = traffic.range_spec(conn, range, now)
        points = traffic.series(conn, spec) if feed.enabled else []
        windows = runner_identity.windows(conn) if feed.enabled else ()
        runner_rows = (
            traffic.scoped_bucket_totals(conn, spec, traffic.runner_scope(windows))
            if feed.enabled
            else {}
        )
    # WP GRAPH-RECENT-1: the minutes since the last sweep come from the ring --
    # only with a fresh feed, like the header's "now" figures they must agree with.
    ring_rows = (
        _ring_buckets(ring, settings, now, spec.bucket_seconds, windows)
        if window is not None and spec.tier in _STITCHED_TIERS
        else {}
    )
    empty = traffic.Totals()
    now_epoch = now.timestamp()
    return SeriesOut(
        event_feed=feed,
        range=range,
        tier=spec.tier,
        bucket_seconds=spec.bucket_seconds,
        range_start=spec.start,
        points=[
            _series_point(
                point, runner_rows.get(point.bucket_at, empty), spec, ring_rows, now_epoch
            )
            for point in points
        ],
    )


class LivePointOut(BaseModel):
    #: Bucket start (UTC).
    t: str
    #: 5, 15 or 60: the point's own bucket length.
    bucket_seconds: int
    #: ``live`` (in-memory ring) or ``persisted`` (1-minute tier).
    source: Literal["live", "persisted"]
    bytes_served: int
    hit_bytes: int
    miss_bytes: int
    requests: int
    runner_bytes: int
    bytes_per_second: float
    peak_bytes_per_second: float
    #: WP RUNNER-SPLIT-1: as in ``SeriesPointOut``.
    runner_hit_bytes: int = 0
    runner_miss_bytes: int = 0


class LiveOut(BaseModel):
    event_feed: EventFeedOut
    #: Always ``live``; ``tier`` is ``live`` too (the stitched series).
    range: Literal["live"] = "live"
    tier: Literal["live"] = "live"
    #: Seconds since the last poll after which the server stops tailing.
    watcher_window_seconds: int
    #: Minutes from here on come from the ring; earlier ones from the
    #: persisted tier (null until the tailer has read the log once).
    live_since: str | None
    range_start: str | None
    #: Null when the feed is disabled.
    current: LiveCurrentOut | None
    #: Client addresses loading now, counted from the ring (the persisted
    #: figure lags a sweep): served bytes in the ``current`` window (WP
    #: LOADING-NOW-1, the Clients page's ``live_now`` rule; was 5 minutes of
    #: any traffic). The prefill runner is not counted. Null when the feed is
    #: disabled.
    active_clients: int | None = None
    #: WP LOADING-NOW-1: the window of ``active_clients`` in seconds (=
    #: ``current.window_seconds``). Null when the feed is disabled.
    active_window_seconds: int | None = None
    #: Deprecated since rc21: the window rounded up to whole minutes (1);
    #: read ``active_window_seconds``.
    active_window_minutes: int = 1
    #: WP NOW-LOADING-1: as ``/v1/throughput``'s ``now.loading_sources``
    #: (the same window, the same rows). Null unless the feed is ``ok``.
    loading_sources: list[LoadingSourceOut] | None = None
    #: Oldest first, zero-filled, ending with the running 5 s cell. Empty
    #: when the feed is disabled.
    points: list[LivePointOut]


def _runner_test(
    windows: tuple[runner_identity.RunnerWindow, ...],
) -> Callable[[str, str], bool]:
    """``is_runner(addr, bucket_iso)`` for the live tier's reads."""

    def is_runner(addr: str, bucket_iso: str) -> bool:
        return any(w.addr == addr and w.start <= bucket_iso < w.end for w in windows)

    return is_runner


def _live_points(
    conn: sqlite3.Connection,
    tier: live_tier.LiveTier,
    now: datetime,
    windows: tuple[runner_identity.RunnerWindow, ...],
) -> tuple[list[live_tier.LivePoint], live_tier.NowView]:
    is_runner = _runner_test(windows)

    def persisted(start: int, end: int) -> dict[int, live_tier.PersistedMinute]:
        first = traffic.to_utc_iso(datetime.fromtimestamp(start, tz=timezone.utc))
        last = traffic.to_utc_iso(datetime.fromtimestamp(end - 60, tz=timezone.utc))
        spec = traffic.RangeSpec(
            name="live", tier=traffic.TIER_1M, bucket_seconds=60, start=first, end=last
        )
        runner_rows = traffic.scoped_bucket_totals(conn, spec, traffic.runner_scope(windows))
        out: dict[int, live_tier.PersistedMinute] = {}
        for point in traffic.series(conn, spec):
            when = live_tier.minute_epoch(point.bucket_at)
            if when is None:  # pragma: no cover - our own format
                continue
            run = runner_rows.get(point.bucket_at, traffic.Totals())
            out[when] = live_tier.PersistedMinute(
                all=_live_totals(point.totals),
                runner=_live_totals(run),
                peak=point.peak_bucket_bytes,
            )
        return out

    # WP NOW-LOADING-1: speed, "loading now" and the per-source rows from
    # one read of the window (``LiveTier.now_view``).
    return tier.stitch(now, persisted, is_runner), tier.now_view(now, is_runner)


def _live_current_out(current: live_tier.LiveCurrent) -> LiveCurrentOut:
    return LiveCurrentOut(
        bytes_per_second=current.bytes_per_second,
        runner_bytes_per_second=current.runner_bytes_per_second,
        window_start=current.window_start,
        window_end=current.window_end,
        window_seconds=current.window_seconds,
        runner_hit_bytes_per_second=current.runner_hit_bytes_per_second,
        runner_miss_bytes_per_second=current.runner_miss_bytes_per_second,
    )


def _live_totals(totals: traffic.Totals) -> live_tier.LiveTotals:
    return live_tier.LiveTotals(
        requests=totals.requests,
        bytes_served=totals.bytes_served,
        hit_bytes=totals.hit_bytes,
        miss_bytes=totals.miss_bytes,
    )


@router.get("/v1/throughput/live", response_model=LiveOut)
def get_throughput_live(
    request: Request,
    open_db: DbOpener = Depends(db_opener),
) -> LiveOut:
    """The stitched live series (WP GRAPH-LIVE). Polling arms the watcher."""
    settings: Settings = deps.effective_settings(request)
    tier: live_tier.LiveTier = request.app.state.live_tier
    now = tier.clock()
    with open_db() as conn:
        state = event_sweep.read_state(conn)
        feed, _window = _feed(settings, state, now)
        if not feed.enabled:
            return LiveOut(
                event_feed=feed,
                watcher_window_seconds=int(live_tier.WATCHER_WINDOW_SECONDS),
                live_since=None,
                range_start=None,
                current=None,
                points=[],
            )
        tier.touch(now)
        tier.refresh(settings.event_log_path, now)
        windows = runner_identity.windows(conn)
        points, view = _live_points(conn, tier, now, windows)
    current = view.current
    # The per-source rows read SQLite again (names), after the connection
    # above is closed; only with a fresh feed, like ``/v1/throughput``'s ``now``.
    sources = (
        _loading_sources(open_db, settings, view, now) if feed.state == "ok" else None
    )
    now_epoch = now.timestamp()
    return LiveOut(
        event_feed=feed,
        watcher_window_seconds=int(live_tier.WATCHER_WINDOW_SECONDS),
        live_since=tier.covered_from(),
        range_start=points[0].t if points else None,
        active_clients=live_tier.loading_counts(view.activity)[0],
        active_window_seconds=current.window_seconds,
        current=_live_current_out(current),
        loading_sources=sources,
        points=[
            LivePointOut(
                t=p.t,
                bucket_seconds=p.bucket_seconds,
                source=p.source,  # type: ignore[arg-type]
                bytes_served=p.all_totals.bytes_served,
                hit_bytes=p.all_totals.hit_bytes - p.runner.hit_bytes,
                miss_bytes=p.all_totals.miss_bytes - p.runner.miss_bytes,
                requests=p.all_totals.requests - p.runner.requests,
                runner_bytes=p.runner.bytes_served,
                runner_hit_bytes=p.runner.hit_bytes,
                runner_miss_bytes=p.runner.miss_bytes,
                bytes_per_second=p.all_totals.bytes_served / _elapsed_seconds(p, now_epoch),
                peak_bytes_per_second=p.peak_bytes_per_second,
            )
            for p in points
        ],
    )


def _elapsed_seconds(point: live_tier.LivePoint, now_epoch: float) -> float:
    """A live point's divisor: its bucket length, or for the running bucket
    (``now_epoch`` inside it) the seconds elapsed in it, at least one, as
    ``_series_point`` and ``LiveTier.current`` do (GRAPH-RECENT-1 nit N2)."""
    # ``minute_epoch`` parses any of our UTC ISO stamps to epoch seconds
    # (here a 5, 15 or 60 s bucket start), not only whole minutes.
    start = live_tier.minute_epoch(point.t)
    if start is not None and start <= now_epoch < start + point.bucket_seconds:
        return max(1.0, now_epoch - start)
    return float(point.bucket_seconds)


@router.get("/v1/clients/overview", response_model=ClientsOverviewOut)
def get_clients_overview(
    request: Request,
    range: RangeName = Query(default="7d"),  # noqa: A002
    open_db: DbOpener = Depends(db_opener),
) -> ClientsOverviewOut:
    """Agent clients merged with every event-log address (D15 Clients page)."""
    settings: Settings = deps.effective_settings(request)
    now = datetime.now(timezone.utc)
    minutes = traffic.active_window_minutes(settings.event_sweep_interval_minutes)
    listing_since = traffic.bucket_iso(
        now - timedelta(days=ADDRESS_LISTING_DAYS), traffic.TIER_1H
    )

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

        # WP D15-RUNNER: every traffic row goes to exactly one side.
        windows = runner_identity.windows(conn)
        side_clients = _read_side(
            conn, traffic.clients_scope(windows), spec, window, now, minutes,
            listing_since, feed_on,
        )
        side_runner = _read_side(
            conn, traffic.runner_scope(windows), spec, window, now, minutes,
            listing_since, feed_on,
        )
        # WP CLIENTS-LIVE-1: the live tier, polled like /v1/throughput/live.
        ring = _read_ring(request, settings, windows) if feed_on else None
        depots = {
            d
            for side in (side_clients, side_runner)
            for per in (*side.recent.values(), *side.current.values())
            for d in per
        }
        if ring is not None:
            depots |= {
                d
                for entry in ring.activity.values()
                for d in entry.depots
            }
        depot_owners = traffic.depot_owners(conn, depots)
        app_ids = {a for apps in depot_owners.values() if len(apps) == 1 for a in apps}
        names = traffic.app_names(
            conn, app_ids, oracle_enabled=settings.manifest_oracle_enabled
        )

    def loading(side: _Side, addrs: list[str]) -> LoadingNowOut | None:
        if not live or window is None:
            return None
        merged: dict[int, tuple[int, str]] = {}
        for addr in addrs:
            for depotid, (size, seen) in side.current.get(addr, {}).items():
                old = merged.get(depotid, (0, ""))
                merged[depotid] = (old[0] + size, max(old[1], seen))
        attribution = traffic.attribute(merged, depot_owners)
        total = sum(side.current_bytes.get(addr, 0) for addr in addrs)
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

    def live_loading(runner: bool, addrs: list[str]) -> LoadingNowOut | None:
        """``live_now`` (``ClientActivityOut``) of the entry with ``addrs``."""
        if ring is None:
            return None
        parts = [ring.activity[(a, runner)] for a in addrs if (a, runner) in ring.activity]
        seconds = ring.current_seconds
        total = sum(p.bytes_served for p in parts)
        merged: dict[int, tuple[int, str]] = {}
        for part in parts:
            for depotid, size in part.depots.items():
                merged[depotid] = (merged.get(depotid, (0, ""))[0] + size, "")
        attribution = traffic.attribute(merged, depot_owners)
        attributed = sum(app.bytes_served for app in attribution.apps)
        return LoadingNowOut(
            loading=total > 0,
            bytes_per_second=total / seconds,
            apps=[
                LoadingAppOut(
                    appid=app.appid,
                    name=names.get(app.appid),
                    bytes_per_second=app.bytes_served / seconds,
                )
                for app in attribution.apps
            ],
            unattributed_bytes_per_second=max(0, total - attributed) / seconds,
        )

    def recent_apps(
        side: _Side, addrs: list[str]
    ) -> tuple[list[RecentAppOut] | None, int | None]:
        if not feed_on:
            return None, None
        merged: dict[int, tuple[int, str]] = {}
        for addr in addrs:
            for depotid, (size, seen) in side.recent.get(addr, {}).items():
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

    def cache_view(
        side: _Side, addrs: list[str]
    ) -> tuple[bool | None, str | None, ClientStatsOut | None]:
        if not feed_on:
            return None, None, None
        seen = [side.last_seen[a] for a in addrs if a in side.last_seen]
        cache_active = any(a in side.active for a in addrs) if live else None
        return (
            cache_active,
            max(seen) if seen else None,
            _stats(_sum_totals([side.totals[a] for a in addrs if a in side.totals])),
        )

    clients: list[ClientActivityOut] = []
    for summary in summaries:
        addrs = sorted(a for a, owner in owners.items() if owner == summary.client_id)
        presence, _offline_after = agent_reports.presence(
            summary.last_reported_at, summary.report_interval_seconds, now
        )
        cache_active, seen_in_log, stats = cache_view(side_clients, addrs)
        apps, unattributed = recent_apps(side_clients, addrs)
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
                loading_now=loading(side_clients, addrs),
                recent_apps=apps,
                recent_unattributed_bytes=unattributed,
                name=named.name,
                name_addr=named.name_addr,
                display_name=named.display_name,
                live_now=live_loading(False, addrs),
            )
        )

    for addr in sorted(side_clients.listed - set(owners)):
        cache_active, seen_in_log, stats = cache_view(side_clients, [addr])
        apps, unattributed = recent_apps(side_clients, [addr])
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
                loading_now=loading(side_clients, [addr]),
                recent_apps=apps,
                recent_unattributed_bytes=unattributed,
                name=named.name,
                name_addr=named.name_addr,
                display_name=named.display_name,
                live_now=live_loading(False, [addr]),
            )
        )

    # WP D15-RUNNER: one entry for the prefill runner, listed while it has
    # traffic in the listing period or a window that reaches into it.
    recent_windows = [w for w in windows if w.end > listing_since]
    runner_addrs: list[str] = []
    for addr in [w.addr for w in recent_windows] + sorted(side_runner.listed):
        if addr not in runner_addrs:
            runner_addrs.append(addr)
    if runner_addrs:
        cache_active, seen_in_log, stats = cache_view(side_runner, runner_addrs)
        apps, unattributed = recent_apps(side_runner, runner_addrs)
        clients.append(
            ClientActivityOut(
                key="runner",
                kind="runner",
                client_id=None,
                addresses=runner_addrs,
                connected=cache_active,
                agent_presence=None,
                cache_active=cache_active,
                last_active=seen_in_log,
                last_seen_in_cache_log=seen_in_log,
                last_reported_at=None,
                installed_appids=None,
                installed_app_count=None,
                stats=stats,
                loading_now=loading(side_runner, runner_addrs),
                recent_apps=apps,
                recent_unattributed_bytes=unattributed,
                name=None,
                name_addr=None,
                display_name=runner_identity.DISPLAY_NAME,
                live_now=live_loading(True, runner_addrs),
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
        live=(
            ClientsLiveOut(
                live_since=ring.live_since,
                current_window_seconds=int(round(ring.current_seconds)),
            )
            if ring is not None
            else None
        ),
    )


@dataclass
class _Ring:
    """The live tier as one clients-overview request reads it."""

    activity: dict[tuple[str, bool], live_tier.AddrActivity]
    current_seconds: float
    live_since: str | None


def _read_ring(
    request: Request,
    settings: Settings,
    windows: tuple[runner_identity.RunnerWindow, ...],
) -> _Ring:
    """Arm and tail the live tier, then read it per address (CLIENTS-LIVE-1)."""
    tier: live_tier.LiveTier = request.app.state.live_tier
    now = tier.clock()
    tier.touch(now)
    tier.refresh(settings.event_log_path, now)
    activity, seconds = tier.addr_activity(now, _runner_test(windows))
    return _Ring(activity=activity, current_seconds=seconds, live_since=tier.covered_from())


@dataclass
class _Side:
    """One side of the runner split, read once per request."""

    totals: dict[str, traffic.Totals] = field(default_factory=dict)
    last_seen: dict[str, str] = field(default_factory=dict)
    listed: set[str] = field(default_factory=set)
    recent: dict[str, dict[int, tuple[int, str]]] = field(default_factory=dict)
    current: dict[str, dict[int, tuple[int, str]]] = field(default_factory=dict)
    current_bytes: dict[str, int] = field(default_factory=dict)
    active: dict[str, str] = field(default_factory=dict)


def _read_side(
    conn: sqlite3.Connection,
    scope: traffic.Scope,
    spec: traffic.RangeSpec,
    window: traffic.CurrentWindow | None,
    now: datetime,
    minutes: int,
    listing_since: str,
    feed_on: bool,
) -> _Side:
    side = _Side()
    if not feed_on:
        return side
    side.totals = traffic.client_totals(conn, spec.tier, spec.start, scope)
    side.last_seen = traffic.last_seen_by_addr(conn, scope)
    side.listed = traffic.addrs_seen_since(conn, listing_since, scope) | set(side.totals)
    if spec.start is not None:
        side.recent = traffic.depot_bytes(conn, spec.tier, spec.start, scope)
    if window is not None:
        side.current = traffic.depot_bytes(conn, traffic.TIER_5M, window.start, scope)
        side.current_bytes = {
            addr: value.bytes_served
            for addr, value in traffic.client_totals(
                conn, traffic.TIER_5M, window.start, scope
            ).items()
        }
        side.active = traffic.active_addrs(conn, now, minutes, scope)
    return side


def _desc(value: str | None) -> tuple[int, ...]:
    """Sort key: newest timestamp first, ``None`` last."""
    if value is None:
        return (1,)
    return (0, *(-ord(character) for character in value))
