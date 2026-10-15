"""Event-log traffic statistics in three retention tiers (WP D15b, CLIENTS-1).

The cache-event sweep (``event_sweep.py``) already turns vault-core's event
log into per-address counters (``client_cache_stats``, about four hours of
sweep-time windows). This module keeps the longer history the Clients page
and the Downloads header need (roadmap D15, D13 SPEED-1 folded in):

========  ==================  =====================================
tier      bucket              kept
========  ==================  =====================================
``5m``    5 minutes           48 hours
``1h``    1 hour              30 days
``1d``    1 day (UTC)         forever
========  ==================  =====================================

(User decision 2026-10-13, late evening: "5-minute rows for 48 h, hourly rows
for 30 days, daily rows forever (the 'all' filter works)".)

How the tiers are filled -- "rollup at write time"
---------------------------------------------------
Every sweep batch is folded into ALL three tiers additively, inside the same
``BEGIN IMMEDIATE`` transaction that advances the event-sweep cursor
(``event_sweep.commit_batch``). There is no separate "read the 5-minute rows
and sum them into hours" job, on purpose:

* **Exactly-once comes for free.** The cursor transaction is already the
  idempotence mechanism for ``client_cache_stats``; a batch whose commit did
  not happen left nothing in any tier, and a re-read batch is counted once.
* **Late lines land in the right bucket of every tier.** Lines are bucketed by
  their EVENT time (field 2), not by the sweep time. A backlog read after
  vault-api was down for a day adds to yesterday's hour and day rows directly.
  A recompute-from-5-minute-rows rollup would instead overwrite an old hour
  with only the late lines once its 5-minute rows had been pruned.
* **Each tier is complete within its retention on its own**, so a reader picks
  ONE tier per range and never stitches tiers together (no double counting at
  a seam).

The per-sweep job that remains is retention: ``prune`` deletes 5-minute rows
older than 48 h and hourly rows older than 30 days. It is idempotent (a
``DELETE ... WHERE bucket_at < cutoff``) and runs in the same transaction.
It is skipped while the sweep is still working through a backlog (a full read
batch), so a backlog older than 48 h keeps its 5-minute rows until it is
consumed -- otherwise a later batch adding to the same old 5-minute bucket
would see it "empty" and over-count the active-bucket counter below.

Two values are NOT sums
-----------------------
``server_traffic`` carries ``peak_bucket_bytes`` (the largest server-wide
5-minute total inside a bucket -> peak speed) and ``active_buckets`` (how many
5-minute buckets inside it carried traffic -> average speed while loading).
Neither can be derived from an hourly or daily sum later, so both are
maintained at write time from the server's 5-minute row before and after the
batch. Peak is a MAX over a value that only grows, so a repeated write cannot
inflate it.

Resolution, stated honestly
---------------------------
The sweep runs every ``VAULT_EVENT_SWEEP_INTERVAL_MINUTES`` (5) and nginx
flushes the event log every 5 s, so "current" speed is the average over the
5 to 10 minutes before the LAST sweep, not a live gauge. ``current_window``
says exactly which window, and returns ``None`` once the last sweep is older
than three intervals (a stopped sweeper must not keep reporting the speed it
saw an hour ago).

Daily buckets are UTC days.

The prefill runner's share (WP D15-RUNNER)
------------------------------------------
Rows are stored per address exactly as before; which rows are the prefill
runner's is decided at READ time from ``runner_identity.windows`` (address
plus a UTC-day-aligned time window). Every client-level reader takes a
:class:`Scope`: ``all`` (every row), ``clients`` (rows outside every runner
window) or ``runner`` (rows inside one). The two non-``all`` sides partition
the rows, so ``clients + runner == all`` holds for every sum, in every tier
(day-aligned windows select the same rows in each tier). ``server_traffic``
has no address, so it stays the ALL figure; a client-only sum is
``server - runner`` (``routers/traffic.py``).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable

from vault_api import app_classification, tool_apps
from vault_api.jobs import parse_utc_iso, to_utc_iso
from vault_api.runner_identity import RunnerWindow

# --------------------------------------------------------------------------
# Tiers
# --------------------------------------------------------------------------

TIER_5M = "5m"
TIER_1H = "1h"
TIER_1D = "1d"
TIERS = (TIER_5M, TIER_1H, TIER_1D)

TIER_SECONDS = {TIER_5M: 300, TIER_1H: 3600, TIER_1D: 86400}

#: Retention per tier (user decision 2026-10-13). ``None`` = forever.
TIER_RETENTION: dict[str, timedelta | None] = {
    TIER_5M: timedelta(hours=48),
    TIER_1H: timedelta(days=30),
    TIER_1D: None,
}

#: The time filters the UI offers, and which tier answers each one.
RANGE_24H = "24h"
RANGE_7D = "7d"
RANGE_30D = "30d"
RANGE_ALL = "all"
RANGES = (RANGE_24H, RANGE_7D, RANGE_30D, RANGE_ALL)
_RANGE_TIER = {
    RANGE_24H: (TIER_5M, 288),  # 24 h of 5-minute buckets
    RANGE_7D: (TIER_1H, 168),
    RANGE_30D: (TIER_1H, 720),
    RANGE_ALL: (TIER_1D, None),
}

#: "Current" speed and "active now" stop being reported once the last sweep
#: is older than this many sweep intervals.
STALE_AFTER_INTERVALS = 3


def floor_bucket(moment: datetime, tier: str) -> datetime:
    """Start of ``moment``'s bucket in ``tier`` (UTC)."""
    moment = moment.astimezone(timezone.utc).replace(second=0, microsecond=0)
    if tier == TIER_5M:
        return moment.replace(minute=moment.minute - moment.minute % 5)
    if tier == TIER_1H:
        return moment.replace(minute=0)
    if tier == TIER_1D:
        return moment.replace(hour=0, minute=0)
    raise ValueError(f"unknown tier {tier!r}")


def bucket_iso(moment: datetime, tier: str) -> str:
    return to_utc_iso(floor_bucket(moment, tier))


def active_window_minutes(sweep_interval_minutes: int) -> int:
    """How recent cache activity must be for a client to count as active.

    Two sweep intervals plus five minutes (15 at the default 5-minute
    interval): a client that loaded something just after one sweep shows up
    at the next one, up to one interval plus nginx's 5 s flush later, and a
    second interval keeps it from flickering between two sweeps.
    """
    return 2 * max(1, int(sweep_interval_minutes)) + 5


# --------------------------------------------------------------------------
# Runner scope (WP D15-RUNNER)
# --------------------------------------------------------------------------

SIDE_ALL = "all"
SIDE_CLIENTS = "clients"
SIDE_RUNNER = "runner"


@dataclass(frozen=True)
class Scope:
    """Which rows of a per-address table a read covers (module docstring)."""

    windows: tuple[RunnerWindow, ...] = ()
    side: str = SIDE_ALL

    def __post_init__(self) -> None:
        if self.side not in (SIDE_ALL, SIDE_CLIENTS, SIDE_RUNNER):
            raise ValueError(f"unknown scope side {self.side!r}")

    def sql(self) -> tuple[str, list[str]]:
        """``(" AND <condition>", params)`` for a query over ``client_addr``
        and ``bucket_at``; ``("", [])`` when nothing needs filtering."""
        if self.side == SIDE_ALL:
            return "", []
        if not self.windows:
            return ("", []) if self.side == SIDE_CLIENTS else (" AND 0", [])
        parts: list[str] = []
        params: list[str] = []
        for window in self.windows:
            parts.append("(client_addr = ? AND bucket_at >= ? AND bucket_at < ?)")
            params.extend((window.addr, window.start, window.end))
        inside = "(" + " OR ".join(parts) + ")"
        return (f" AND NOT {inside}" if self.side == SIDE_CLIENTS else f" AND {inside}"), params


#: Every row (the pre-D15-RUNNER behaviour).
ALL = Scope()


def clients_scope(windows: tuple[RunnerWindow, ...]) -> Scope:
    return Scope(windows=windows, side=SIDE_CLIENTS)


def runner_scope(windows: tuple[RunnerWindow, ...]) -> Scope:
    return Scope(windows=windows, side=SIDE_RUNNER)


# --------------------------------------------------------------------------
# Accumulating one batch
# --------------------------------------------------------------------------


@dataclass
class TrafficCell:
    """One (address, 5-minute bucket) accumulator for one batch."""

    requests: int = 0
    hits: int = 0
    misses: int = 0
    bypasses: int = 0
    errors: int = 0
    bytes_served: int = 0
    hit_bytes: int = 0
    miss_bytes: int = 0
    last_seen: str = ""


@dataclass
class DepotCell:
    """One (address, depot, 5-minute bucket) accumulator (served lines only)."""

    requests: int = 0
    bytes_served: int = 0
    hit_bytes: int = 0
    last_seen: str = ""


@dataclass
class TrafficBatch:
    """Everything one sweep batch adds to the tiers."""

    cells: dict[tuple[str, str], TrafficCell] = field(default_factory=dict)
    depot_cells: dict[tuple[str, int, str], DepotCell] = field(default_factory=dict)

    def add(
        self,
        *,
        addr: str,
        seen_at: str,
        now_iso: str,
        served: bool,
        cache_status: str,
        bytes_sent: int,
        depotid: int | None,
    ) -> None:
        """Fold one parsed event line in.

        ``seen_at`` is the line's UTC event time (or the sweep time when
        nginx's stamp was unreadable). A time in the FUTURE of the sweep is
        clamped to the sweep time: a skewed clock must not create buckets that
        a range ending "now" never shows, nor peaks in tomorrow's row.
        """
        if seen_at > now_iso:
            seen_at = now_iso
        moment = parse_utc_iso(seen_at)
        if moment is None:  # pragma: no cover - both inputs are our own format
            moment = parse_utc_iso(now_iso)
            if moment is None:
                return
            seen_at = now_iso
        bucket = bucket_iso(moment, TIER_5M)

        cell = self.cells.get((addr, bucket))
        if cell is None:
            cell = self.cells[(addr, bucket)] = TrafficCell()
        cell.requests += 1
        if seen_at > cell.last_seen:
            cell.last_seen = seen_at
        if not served:
            cell.errors += 1
            return
        cell.bytes_served += bytes_sent
        if cache_status == "HIT":
            cell.hits += 1
            cell.hit_bytes += bytes_sent
        elif cache_status == "MISS":
            cell.misses += 1
            cell.miss_bytes += bytes_sent
        else:
            cell.bypasses += 1

        if depotid is None:
            return
        depot = self.depot_cells.get((addr, depotid, bucket))
        if depot is None:
            depot = self.depot_cells[(addr, depotid, bucket)] = DepotCell()
        depot.requests += 1
        depot.bytes_served += bytes_sent
        if cache_status == "HIT":
            depot.hit_bytes += bytes_sent
        if seen_at > depot.last_seen:
            depot.last_seen = seen_at


# --------------------------------------------------------------------------
# Writing (inside the sweep's cursor transaction)
# --------------------------------------------------------------------------

_CLIENT_UPSERT = """
INSERT INTO client_traffic (
    tier, bucket_at, client_addr, requests, hits, misses, bypasses, errors,
    bytes_served, hit_bytes, miss_bytes, last_seen
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(tier, client_addr, bucket_at) DO UPDATE SET
    requests     = client_traffic.requests + excluded.requests,
    hits         = client_traffic.hits + excluded.hits,
    misses       = client_traffic.misses + excluded.misses,
    bypasses     = client_traffic.bypasses + excluded.bypasses,
    errors       = client_traffic.errors + excluded.errors,
    bytes_served = client_traffic.bytes_served + excluded.bytes_served,
    hit_bytes    = client_traffic.hit_bytes + excluded.hit_bytes,
    miss_bytes   = client_traffic.miss_bytes + excluded.miss_bytes,
    last_seen    = MAX(client_traffic.last_seen, excluded.last_seen)
"""

_DEPOT_UPSERT = """
INSERT INTO client_depot_traffic (
    tier, bucket_at, client_addr, depotid, requests, bytes_served, hit_bytes,
    last_seen
) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(tier, client_addr, depotid, bucket_at) DO UPDATE SET
    requests     = client_depot_traffic.requests + excluded.requests,
    bytes_served = client_depot_traffic.bytes_served + excluded.bytes_served,
    hit_bytes    = client_depot_traffic.hit_bytes + excluded.hit_bytes,
    last_seen    = MAX(client_depot_traffic.last_seen, excluded.last_seen)
"""

_SERVER_UPSERT_5M = """
INSERT INTO server_traffic (
    tier, bucket_at, requests, hits, misses, bypasses, errors, bytes_served,
    hit_bytes, miss_bytes, peak_bucket_bytes, active_buckets
) VALUES ('5m', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(tier, bucket_at) DO UPDATE SET
    requests     = server_traffic.requests + excluded.requests,
    hits         = server_traffic.hits + excluded.hits,
    misses       = server_traffic.misses + excluded.misses,
    bypasses     = server_traffic.bypasses + excluded.bypasses,
    errors       = server_traffic.errors + excluded.errors,
    bytes_served = server_traffic.bytes_served + excluded.bytes_served,
    hit_bytes    = server_traffic.hit_bytes + excluded.hit_bytes,
    miss_bytes   = server_traffic.miss_bytes + excluded.miss_bytes,
    peak_bucket_bytes = server_traffic.bytes_served + excluded.bytes_served,
    active_buckets = CASE
        WHEN server_traffic.bytes_served + excluded.bytes_served > 0 THEN 1
        ELSE 0 END
"""

_SERVER_UPSERT_COARSE = """
INSERT INTO server_traffic (
    tier, bucket_at, requests, hits, misses, bypasses, errors, bytes_served,
    hit_bytes, miss_bytes, peak_bucket_bytes, active_buckets
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(tier, bucket_at) DO UPDATE SET
    requests     = server_traffic.requests + excluded.requests,
    hits         = server_traffic.hits + excluded.hits,
    misses       = server_traffic.misses + excluded.misses,
    bypasses     = server_traffic.bypasses + excluded.bypasses,
    errors       = server_traffic.errors + excluded.errors,
    bytes_served = server_traffic.bytes_served + excluded.bytes_served,
    hit_bytes    = server_traffic.hit_bytes + excluded.hit_bytes,
    miss_bytes   = server_traffic.miss_bytes + excluded.miss_bytes,
    peak_bucket_bytes = MAX(server_traffic.peak_bucket_bytes,
                            excluded.peak_bucket_bytes),
    active_buckets = server_traffic.active_buckets + excluded.active_buckets
"""


def _coarse_buckets(bucket_5m: str) -> list[tuple[str, str]]:
    moment = parse_utc_iso(bucket_5m)
    assert moment is not None  # built by bucket_iso
    return [(TIER_1H, bucket_iso(moment, TIER_1H)), (TIER_1D, bucket_iso(moment, TIER_1D))]


def write_batch(
    conn: sqlite3.Connection, batch: TrafficBatch, now: datetime | None = None
) -> None:
    """Fold one batch into all three tiers. Caller holds the transaction.

    ``now`` (the sweep time) decides which 5-minute buckets are older than
    the 5m retention. For such a bucket a missing 5-minute row does not prove
    the bucket was never active -- its row may simply have been pruned -- so
    it counts as newly active in a coarse row only when that coarse row does
    not exist yet. That can under-count ``active_buckets`` for very late
    data (average speed slightly high), never over-count it.
    """
    server: dict[str, TrafficCell] = {}
    for (addr, bucket), cell in batch.cells.items():
        values = (
            cell.requests,
            cell.hits,
            cell.misses,
            cell.bypasses,
            cell.errors,
            cell.bytes_served,
            cell.hit_bytes,
            cell.miss_bytes,
        )
        conn.execute(_CLIENT_UPSERT, (TIER_5M, bucket, addr, *values, cell.last_seen))
        for tier, coarse in _coarse_buckets(bucket):
            conn.execute(_CLIENT_UPSERT, (tier, coarse, addr, *values, cell.last_seen))

        total = server.get(bucket)
        if total is None:
            total = server[bucket] = TrafficCell()
        total.requests += cell.requests
        total.hits += cell.hits
        total.misses += cell.misses
        total.bypasses += cell.bypasses
        total.errors += cell.errors
        total.bytes_served += cell.bytes_served
        total.hit_bytes += cell.hit_bytes
        total.miss_bytes += cell.miss_bytes

    for (addr, depotid, bucket), depot in batch.depot_cells.items():
        values = (depot.requests, depot.bytes_served, depot.hit_bytes, depot.last_seen)
        conn.execute(_DEPOT_UPSERT, (TIER_5M, bucket, addr, depotid, *values))
        for tier, coarse in _coarse_buckets(bucket):
            conn.execute(_DEPOT_UPSERT, (tier, coarse, addr, depotid, *values))

    cutoff_5m = (
        to_utc_iso(floor_bucket(now, TIER_5M) - TIER_RETENTION[TIER_5M])  # type: ignore[operator]
        if now is not None
        else None
    )
    for bucket, total in sorted(server.items()):
        row = conn.execute(
            "SELECT bytes_served FROM server_traffic WHERE tier = '5m' AND bucket_at = ?",
            (bucket,),
        ).fetchone()
        before = int(row["bytes_served"]) if row is not None else 0
        after = before + total.bytes_served
        sums = (
            total.requests,
            total.hits,
            total.misses,
            total.bypasses,
            total.errors,
            total.bytes_served,
            total.hit_bytes,
            total.miss_bytes,
        )
        conn.execute(
            _SERVER_UPSERT_5M, (bucket, *sums, after, 1 if after > 0 else 0)
        )
        newly_active = 1 if before == 0 and after > 0 else 0
        expired = cutoff_5m is not None and bucket < cutoff_5m
        for tier, coarse in _coarse_buckets(bucket):
            active = newly_active
            if active and expired:
                exists = conn.execute(
                    "SELECT 1 FROM server_traffic WHERE tier = ? AND bucket_at = ?",
                    (tier, coarse),
                ).fetchone()
                active = 0 if exists is not None else 1
            conn.execute(_SERVER_UPSERT_COARSE, (tier, coarse, *sums, after, active))


def prune(conn: sqlite3.Connection, now: datetime) -> int:
    """Apply the tier retention. Idempotent; caller holds the transaction.

    Returns the number of rows removed (diagnostic).
    """
    removed = 0
    for tier in TIERS:
        keep = TIER_RETENTION[tier]
        if keep is None:
            continue
        cutoff = to_utc_iso(floor_bucket(now, tier) - keep)
        for table in ("client_traffic", "client_depot_traffic", "server_traffic"):
            cursor = conn.execute(
                f"DELETE FROM {table} WHERE tier = ? AND bucket_at < ?",
                (tier, cutoff),
            )
            removed += int(cursor.rowcount or 0)
    return removed


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RangeSpec:
    """Which tier answers a range, and where the range starts."""

    name: str
    tier: str
    bucket_seconds: int
    #: First bucket of the range (inclusive), or ``None`` for ``all`` on an
    #: empty table.
    start: str | None
    #: The bucket ``now`` falls into -- the last one a series shows.
    end: str


def range_spec(conn: sqlite3.Connection, name: str, now: datetime) -> RangeSpec:
    """Map ``24h``/``7d``/``30d``/``all`` onto a tier and a start bucket."""
    if name not in _RANGE_TIER:
        raise ValueError(f"unknown range {name!r}")
    tier, buckets = _RANGE_TIER[name]
    seconds = TIER_SECONDS[tier]
    end = floor_bucket(now, tier)
    if buckets is not None:
        start: str | None = to_utc_iso(end - timedelta(seconds=seconds * (buckets - 1)))
    else:
        row = conn.execute(
            "SELECT MIN(bucket_at) AS first FROM server_traffic WHERE tier = ?",
            (tier,),
        ).fetchone()
        start = row["first"] if row is not None else None
    return RangeSpec(
        name=name, tier=tier, bucket_seconds=seconds, start=start, end=to_utc_iso(end)
    )


@dataclass(frozen=True)
class CurrentWindow:
    """The window "current" speed is averaged over."""

    start: str
    end: str
    seconds: int


def current_window(
    last_sweep_at: str | None, now: datetime, sweep_interval_minutes: int
) -> CurrentWindow | None:
    """The previous 5-minute bucket plus the one the last sweep fell into.

    ``None`` when no sweep has run, its stamp is unreadable, or it is older
    than ``STALE_AFTER_INTERVALS`` intervals: a speed is only reported while
    the sweeper is demonstrably reading.
    """
    if last_sweep_at is None:
        return None
    last = parse_utc_iso(last_sweep_at)
    if last is None:
        return None
    if now - last > timedelta(minutes=STALE_AFTER_INTERVALS * max(1, sweep_interval_minutes)):
        return None
    start = floor_bucket(last, TIER_5M) - timedelta(minutes=5)
    seconds = max(1, int((last - start).total_seconds()))
    return CurrentWindow(start=to_utc_iso(start), end=last_sweep_at, seconds=seconds)


@dataclass(frozen=True)
class Totals:
    requests: int = 0
    hits: int = 0
    misses: int = 0
    bypasses: int = 0
    errors: int = 0
    bytes_served: int = 0
    hit_bytes: int = 0
    miss_bytes: int = 0
    last_seen: str | None = None


def _totals(row: sqlite3.Row | None) -> Totals:
    if row is None:
        return Totals()
    keys = row.keys()
    return Totals(
        requests=int(row["requests"] or 0),
        hits=int(row["hits"] or 0),
        misses=int(row["misses"] or 0),
        bypasses=int(row["bypasses"] or 0),
        errors=int(row["errors"] or 0),
        bytes_served=int(row["bytes_served"] or 0),
        hit_bytes=int(row["hit_bytes"] or 0),
        miss_bytes=int(row["miss_bytes"] or 0),
        last_seen=row["last_seen"] if "last_seen" in keys else None,
    )


_SUM_COLUMNS = """
    COALESCE(SUM(requests), 0) AS requests,
    COALESCE(SUM(hits), 0) AS hits,
    COALESCE(SUM(misses), 0) AS misses,
    COALESCE(SUM(bypasses), 0) AS bypasses,
    COALESCE(SUM(errors), 0) AS errors,
    COALESCE(SUM(bytes_served), 0) AS bytes_served,
    COALESCE(SUM(hit_bytes), 0) AS hit_bytes,
    COALESCE(SUM(miss_bytes), 0) AS miss_bytes
"""


@dataclass(frozen=True)
class ServerSummary:
    totals: Totals
    peak_bucket_bytes: int
    active_buckets: int
    distinct_clients: int


def server_summary(
    conn: sqlite3.Connection, spec: RangeSpec, scope: Scope = ALL
) -> ServerSummary:
    """Sums, peak and active-bucket count over one range.

    Sums, peak and active buckets are server-wide (``server_traffic`` has no
    address); ``scope`` applies to ``distinct_clients`` only.
    """
    if spec.start is None:
        return ServerSummary(Totals(), 0, 0, 0)
    row = conn.execute(
        f"""
        SELECT {_SUM_COLUMNS},
               COALESCE(MAX(peak_bucket_bytes), 0) AS peak,
               COALESCE(SUM(active_buckets), 0) AS active
        FROM server_traffic WHERE tier = ? AND bucket_at >= ?
        """,
        (spec.tier, spec.start),
    ).fetchone()
    where, params = scope.sql()
    clients = conn.execute(
        f"""
        SELECT COUNT(DISTINCT client_addr) AS n FROM client_traffic
        WHERE tier = ? AND bucket_at >= ? AND bytes_served > 0{where}
        """,
        (spec.tier, spec.start, *params),
    ).fetchone()
    return ServerSummary(
        totals=_totals(row),
        peak_bucket_bytes=int(row["peak"]) if row is not None else 0,
        active_buckets=int(row["active"]) if row is not None else 0,
        distinct_clients=int(clients["n"]) if clients is not None else 0,
    )


@dataclass(frozen=True)
class SeriesPoint:
    bucket_at: str
    totals: Totals
    peak_bucket_bytes: int


def series(conn: sqlite3.Connection, spec: RangeSpec) -> list[SeriesPoint]:
    """Server-wide rows of one range, ZERO-FILLED from start to the current
    bucket, oldest first (a graph needs no gap logic)."""
    if spec.start is None:
        return []
    rows = conn.execute(
        """
        SELECT bucket_at, requests, hits, misses, bypasses, errors, bytes_served,
               hit_bytes, miss_bytes, peak_bucket_bytes
        FROM server_traffic WHERE tier = ? AND bucket_at >= ? AND bucket_at <= ?
        """,
        (spec.tier, spec.start, spec.end),
    ).fetchall()
    by_bucket = {str(row["bucket_at"]): row for row in rows}
    start = parse_utc_iso(spec.start)
    end = parse_utc_iso(spec.end)
    if start is None or end is None:  # pragma: no cover - our own format
        return []
    points: list[SeriesPoint] = []
    step = timedelta(seconds=spec.bucket_seconds)
    moment = start
    while moment <= end:
        key = to_utc_iso(moment)
        row = by_bucket.get(key)
        if row is None:
            points.append(SeriesPoint(bucket_at=key, totals=Totals(), peak_bucket_bytes=0))
        else:
            points.append(
                SeriesPoint(
                    bucket_at=key,
                    totals=_totals(row),
                    peak_bucket_bytes=int(row["peak_bucket_bytes"]),
                )
            )
        moment += step
    return points


def server_bytes_since(conn: sqlite3.Connection, start: str) -> int:
    row = conn.execute(
        """
        SELECT COALESCE(SUM(bytes_served), 0) AS b FROM server_traffic
        WHERE tier = '5m' AND bucket_at >= ?
        """,
        (start,),
    ).fetchone()
    return int(row["b"]) if row is not None else 0


def client_totals(
    conn: sqlite3.Connection, tier: str, start: str | None, scope: Scope = ALL
) -> dict[str, Totals]:
    """Per-address sums over one tier from ``start`` (``None`` = nothing)."""
    if start is None:
        return {}
    where, params = scope.sql()
    rows = conn.execute(
        f"""
        SELECT client_addr, {_SUM_COLUMNS}, MAX(last_seen) AS last_seen
        FROM client_traffic WHERE tier = ? AND bucket_at >= ?{where}
        GROUP BY client_addr
        """,
        (tier, start, *params),
    ).fetchall()
    return {str(row["client_addr"]): _totals(row) for row in rows}


def scoped_totals(
    conn: sqlite3.Connection, tier: str, start: str | None, scope: Scope
) -> Totals:
    """All addresses' sums in ``scope`` over one tier from ``start``."""
    if start is None:
        return Totals()
    where, params = scope.sql()
    row = conn.execute(
        f"""
        SELECT {_SUM_COLUMNS}, MAX(last_seen) AS last_seen
        FROM client_traffic WHERE tier = ? AND bucket_at >= ?{where}
        """,
        (tier, start, *params),
    ).fetchone()
    return _totals(row)


def scoped_bucket_totals(
    conn: sqlite3.Connection, spec: RangeSpec, scope: Scope
) -> dict[str, Totals]:
    """``{bucket_at: sums}`` of the rows in ``scope`` over one range."""
    if spec.start is None:
        return {}
    where, params = scope.sql()
    rows = conn.execute(
        f"""
        SELECT bucket_at, {_SUM_COLUMNS}
        FROM client_traffic WHERE tier = ? AND bucket_at >= ? AND bucket_at <= ?{where}
        GROUP BY bucket_at
        """,
        (spec.tier, spec.start, spec.end, *params),
    ).fetchall()
    return {str(row["bucket_at"]): _totals(row) for row in rows}


def active_addrs(
    conn: sqlite3.Connection, now: datetime, minutes: int, scope: Scope = ALL
) -> dict[str, str]:
    """Addresses whose newest line in ``scope`` is within ``minutes`` -> that
    time (5-minute tier)."""
    since = to_utc_iso(now - timedelta(minutes=minutes))
    where, params = scope.sql()
    rows = conn.execute(
        f"""
        SELECT client_addr, MAX(last_seen) AS seen FROM client_traffic
        WHERE tier = '5m' AND bucket_at >= ?{where}
        GROUP BY client_addr HAVING MAX(last_seen) >= ?
        """,
        (bucket_iso(now - timedelta(minutes=minutes), TIER_5M), *params, since),
    ).fetchall()
    return {str(row["client_addr"]): str(row["seen"]) for row in rows}


def subtract(whole: Totals, part: Totals) -> Totals:
    """``whole - part`` per counter, floored at 0 (``last_seen`` dropped)."""
    return Totals(
        requests=max(0, whole.requests - part.requests),
        hits=max(0, whole.hits - part.hits),
        misses=max(0, whole.misses - part.misses),
        bypasses=max(0, whole.bypasses - part.bypasses),
        errors=max(0, whole.errors - part.errors),
        bytes_served=max(0, whole.bytes_served - part.bytes_served),
        hit_bytes=max(0, whole.hit_bytes - part.hit_bytes),
        miss_bytes=max(0, whole.miss_bytes - part.miss_bytes),
    )


def last_seen_by_addr(conn: sqlite3.Connection, scope: Scope = ALL) -> dict[str, str]:
    """Newest event time per address over everything kept (the daily tier)."""
    where, params = scope.sql()
    rows = conn.execute(
        f"""
        SELECT client_addr, MAX(last_seen) AS last_seen FROM client_traffic
        WHERE tier = '1d'{where} GROUP BY client_addr
        """,
        params,
    ).fetchall()
    return {str(row["client_addr"]): str(row["last_seen"]) for row in rows}


def addrs_seen_since(
    conn: sqlite3.Connection, since: str, scope: Scope = ALL
) -> set[str]:
    """Addresses with any line in the hourly tier from ``since`` on."""
    where, params = scope.sql()
    rows = conn.execute(
        f"""
        SELECT DISTINCT client_addr FROM client_traffic
        WHERE tier = '1h' AND bucket_at >= ?{where}
        """,
        (since, *params),
    ).fetchall()
    return {str(row["client_addr"]) for row in rows}


def depot_bytes(
    conn: sqlite3.Connection, tier: str, start: str, scope: Scope = ALL
) -> dict[str, dict[int, tuple[int, str]]]:
    """``{addr: {depotid: (bytes_served, last_seen)}}`` over one tier/range."""
    where, params = scope.sql()
    rows = conn.execute(
        f"""
        SELECT client_addr, depotid, SUM(bytes_served) AS b, MAX(last_seen) AS seen
        FROM client_depot_traffic WHERE tier = ? AND bucket_at >= ?{where}
        GROUP BY client_addr, depotid
        """,
        (tier, start, *params),
    ).fetchall()
    result: dict[str, dict[int, tuple[int, str]]] = {}
    for row in rows:
        result.setdefault(str(row["client_addr"]), {})[int(row["depotid"])] = (
            int(row["b"] or 0),
            str(row["seen"]),
        )
    return result


def depot_owners(conn: sqlite3.Connection, depotids: Iterable[int]) -> dict[int, list[int]]:
    """``depot_app_map`` for the given depots: ``{depotid: [appid, ...]}``."""
    ids = sorted(set(depotids))
    owners: dict[int, list[int]] = {depot: [] for depot in ids}
    for offset in range(0, len(ids), 500):
        chunk = ids[offset : offset + 500]
        placeholders = ",".join("?" for _ in chunk)
        for row in conn.execute(
            f"SELECT depotid, appid FROM depot_app_map WHERE depotid IN ({placeholders})",
            tuple(chunk),
        ):
            owners[int(row["depotid"])].append(int(row["appid"]))
    return owners


def app_names(
    conn: sqlite3.Connection, appids: Iterable[int], *, oracle_enabled: bool = False
) -> dict[int, str | None]:
    """Display name per app id, ``None`` when nothing knows it.

    Same order as ``GET /v1/games`` (WP ID-AUDIT): ``apps.name``, then the
    name already stored from Steam's lookups (``app_classification``), then
    the fixed tool-app list. Reads stored rows only; no lookup is made here.
    """
    ids = sorted(set(appids))
    names: dict[int, str | None] = {appid: None for appid in ids}
    for offset in range(0, len(ids), 500):
        chunk = ids[offset : offset + 500]
        placeholders = ",".join("?" for _ in chunk)
        for row in conn.execute(
            f"SELECT appid, name FROM apps WHERE appid IN ({placeholders})",
            tuple(chunk),
        ):
            name = row["name"]
            names[int(row["appid"])] = (
                str(name) if isinstance(name, str) and name.strip() else None
            )
    missing = [appid for appid, name in names.items() if name is None]
    if missing:
        stored = app_classification.fallback_names(
            conn, missing, oracle_enabled=oracle_enabled
        )
        for appid in missing:
            tool = tool_apps.get_tool_app(appid)
            names[appid] = stored.get(appid) or (tool.name if tool is not None else None)
    return names


@dataclass(frozen=True)
class AppBytes:
    appid: int
    bytes_served: int
    last_seen: str


@dataclass(frozen=True)
class Attribution:
    """Depot bytes folded onto apps."""

    apps: list[AppBytes]
    #: Bytes on depots that map to no app, or to more than one (a shared
    #: redistributable depot cannot say which game pulled it).
    unattributed_bytes: int


def attribute(
    per_depot: dict[int, tuple[int, str]], owners: dict[int, list[int]]
) -> Attribution:
    """Fold ``{depotid: (bytes, last_seen)}`` onto apps; the miss trigger's
    rule: exactly one mapped app, or the bytes stay unattributed."""
    apps: dict[int, list[object]] = {}
    unattributed = 0
    for depotid, (size, seen) in per_depot.items():
        mapped = owners.get(depotid, [])
        if len(mapped) != 1:
            unattributed += size
            continue
        entry = apps.setdefault(mapped[0], [0, ""])
        entry[0] = int(entry[0]) + size  # type: ignore[call-overload]
        if seen > str(entry[1]):
            entry[1] = seen
    ordered = sorted(
        (AppBytes(appid, int(v[0]), str(v[1])) for appid, v in apps.items()),  # type: ignore[call-overload]
        key=lambda item: (-item.bytes_served, item.appid),
    )
    return Attribution(apps=ordered, unattributed_bytes=unattributed)


def exclusive_depots_for_app(conn: sqlite3.Connection, appid: int) -> list[int]:
    """Depots mapped to ``appid`` and to no other app (per-job attribution)."""
    rows = conn.execute(
        """
        SELECT depotid FROM depot_app_map
        WHERE appid = ?
          AND depotid NOT IN (
              SELECT depotid FROM depot_app_map WHERE appid != ?
          )
        ORDER BY depotid
        """,
        (appid, appid),
    ).fetchall()
    return [int(row["depotid"]) for row in rows]


def depots_bytes_since(
    conn: sqlite3.Connection, depotids: list[int], tier: str, start: str
) -> int:
    """All addresses' bytes on ``depotids`` from ``start`` on, in ``tier``."""
    if not depotids:
        return 0
    placeholders = ",".join("?" for _ in depotids)
    row = conn.execute(
        f"""
        SELECT COALESCE(SUM(bytes_served), 0) AS b FROM client_depot_traffic
        WHERE tier = ? AND bucket_at >= ? AND depotid IN ({placeholders})
        """,
        (tier, start, *depotids),
    ).fetchone()
    return int(row["b"]) if row is not None else 0
