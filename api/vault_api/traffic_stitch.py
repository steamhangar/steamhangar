"""The unswept span in the Downloads summary and job figures (WP APP-DL-STATS-1b).

``GET /v1/throughput``'s summary tiles (served, from the internet, saved,
hit rate, average, peak, the runner's share) and its per-job figures used to
read only the tiers the event sweep fills, every 5 minutes. In the first
minutes of a prefill job they read 0 while the live graph, the big number
and "Loading now" (all from the ring, ``live_tier.py``) showed the download
(operator report 2026-11-01, decision Weg A 2026-11-21). These pure
functions put the ring's view of the minutes the sweep has not read into
those figures, by the rule ``/v1/throughput/series`` already uses for its
newest buckets (``live_tier.prefer_ring``, WP GRAPH-RECENT-1):

* **One source per bucket.** For each bucket the ring reaches, the swept row
  and the ring's totals are each a subset of the lines that fell into it;
  the larger one (by served bytes, then requests; an equal non-empty ring
  wins) stands for the bucket. A figure becomes ``swept total - swept bucket
  + ring bucket`` for the buckets the ring wins, so every line is counted
  once: by the sweep if the sweep has it and the ring has no more, else by
  the ring. Nothing is ever added on top of the swept bucket.
* **Same snapshot.** The swept total and the swept bucket rows subtracted
  from it come from one SQLite read transaction (``traffic.read_snapshot``),
  so a sweep committing in between cannot make the subtraction take away a
  line the total never had. The ring is read after the connection closed
  (tailing never holds SQLite open). A sweep that commits between the two
  reads can only make the swept side SMALLER than what the ring then holds
  (the ring wins that bucket) or the ring hold lines the snapshot lacks
  (counted once, by the ring); lines the sweep read and truncated before the
  tailer reached them are in neither for that one answer (the ring's known
  limit), so a figure can be short for a poll, never doubled.
* **Bytes only.** The ring counts requests and served / HIT / MISS bytes,
  not hits, misses, errors or bypasses: byte figures (and the active-bucket
  count and peak derived from them) are stitched, request counts stay swept.
* **Units.** The summary stitches at its peak unit (1-minute buckets for the
  1 h / 6 h ranges on the 1-minute tier, 5-minute buckets otherwise), so peak
  and "average while loading" keep their meaning. Job figures stitch at
  5-minute buckets, the tier of ``client_depot_traffic``; a bucket whose
  per-depot ring bytes are incomplete (``live_tier.MAX_DEPOT_BUCKET_ENTRIES``
  reached, or depot bytes in the overflow address) keeps its swept value.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone

from vault_api import live_tier, traffic
from vault_api.jobs import to_utc_iso


@dataclass(frozen=True)
class PersistedBucket:
    """One swept bucket of the summary's unit tier: all rows, and the
    prefill runner's part (``client_traffic`` in the runner scope)."""

    all: traffic.Totals
    runner: traffic.Totals


def _swept(totals: traffic.Totals) -> live_tier.LiveTotals:
    """A swept row in the shape ``live_tier.prefer_ring`` compares."""
    return live_tier.LiveTotals(requests=totals.requests, bytes_served=totals.bytes_served)


def ring_wins(ring: live_tier.RingBucket, swept: traffic.Totals | None) -> bool:
    """Whether the ring stands for this bucket (``live_tier.prefer_ring``)."""
    return live_tier.prefer_ring(ring.all, _swept(swept or traffic.Totals()))


def _replace_bytes(
    base: traffic.Totals, out: traffic.Totals, into: live_tier.LiveTotals
) -> traffic.Totals:
    """``base`` with one bucket's byte figures swapped from ``out`` (swept)
    to ``into`` (ring). Request counts stay ``base``'s (bytes only)."""
    return traffic.Totals(
        requests=base.requests,
        hits=base.hits,
        misses=base.misses,
        bypasses=base.bypasses,
        errors=base.errors,
        bytes_served=max(0, base.bytes_served - out.bytes_served + into.bytes_served),
        hit_bytes=max(0, base.hit_bytes - out.hit_bytes + into.hit_bytes),
        miss_bytes=max(0, base.miss_bytes - out.miss_bytes + into.miss_bytes),
        last_seen=base.last_seen,
    )


def stitch_summary(
    summary: traffic.ServerSummary,
    runner: traffic.Totals,
    persisted: dict[int, PersistedBucket],
    ring: dict[int, live_tier.RingBucket],
    start_epoch: int | None,
) -> tuple[traffic.ServerSummary, traffic.Totals, int]:
    """The swept range summary (and the runner's share of it) with the
    unit buckets the ring wins swapped in. ``persisted`` and ``ring`` are
    keyed by bucket start (epoch s) at the same unit; buckets before
    ``start_epoch`` (the range start) are outside the range. Returns the
    stitched summary, the stitched runner share and how many buckets the
    ring stood for. ``distinct_clients`` stays swept (the ring's addresses
    are not part of this package)."""
    if start_epoch is None:
        return summary, runner, 0
    totals = summary.totals
    run = runner
    peak = summary.peak_bucket_bytes
    active = summary.active_buckets
    replaced = 0
    empty = PersistedBucket(traffic.Totals(), traffic.Totals())
    for start in sorted(ring):
        if start < start_epoch:
            continue
        bucket = ring[start]
        have = persisted.get(start, empty)
        if not ring_wins(bucket, have.all):
            continue
        totals = _replace_bytes(totals, have.all, bucket.all)
        run = _replace_bytes(run, have.runner, bucket.runner)
        active += int(bucket.all.bytes_served > 0) - int(have.all.bytes_served > 0)
        peak = max(peak, bucket.all.bytes_served)
        replaced += 1
    stitched = traffic.ServerSummary(
        totals=totals,
        peak_bucket_bytes=peak,
        active_buckets=max(0, active),
        distinct_clients=summary.distinct_clients,
    )
    return stitched, run, replaced


def _iso(epoch: int) -> str:
    return to_utc_iso(datetime.fromtimestamp(epoch, tz=timezone.utc))


def ring_depot_bytes(
    by_addr: dict[str, dict[int, int]],
    depots: Iterable[int],
    runner_only: bool,
    is_runner: Callable[[str, str], bool],
    bucket_iso: str,
) -> int:
    """Served bytes on ``depots`` in one ring bucket: every address, or only
    the prefill runner's (``runner_only``, a first download's expected
    depots, WP JOB-SIZE-1)."""
    wanted = set(depots)
    total = 0
    for addr, sizes in by_addr.items():
        if runner_only and not is_runner(addr, bucket_iso):
            continue
        total += sum(size for depot, size in sizes.items() if depot in wanted)
    return total


def stitch_job_since(
    swept_since: int,
    swept_by_bucket: dict[int, int],
    servers: dict[int, traffic.Totals],
    view: live_tier.StitchView,
    depots: Iterable[int],
    runner_only: bool,
    is_runner: Callable[[str, str], bool],
    start_epoch: int,
) -> int:
    """A job's "loaded so far": the swept bytes on its depots since its start
    bucket, with each 5-minute bucket the ring wins (decided server-wide,
    ``servers`` vs ``view.fives``, like the summary) swapped for the ring's
    bytes on those depots. A bucket with incomplete per-depot ring bytes keeps
    its swept value."""
    wanted = list(depots)
    total = swept_since
    for start in sorted(view.fives):
        if start < start_epoch or start in view.depots_incomplete:
            continue
        if not ring_wins(view.fives[start], servers.get(start)):
            continue
        ring_bytes = ring_depot_bytes(
            view.depots.get(start, {}), wanted, runner_only, is_runner, _iso(start)
        )
        total += ring_bytes - swept_by_bucket.get(start, 0)
    return max(0, total)


def job_ring_rate(view: live_tier.NowView, depots: Iterable[int], runner_only: bool) -> float:
    """A job's speed from the ring: served bytes on its depots in the
    "current speed" window (``now_view``, the same read as "Loading now"),
    divided by that window. Every address, or only the runner's."""
    wanted = set(depots)
    total = 0
    for (_addr, side), activity in view.activity.items():
        if runner_only and not side:
            continue
        total += sum(size for depot, size in activity.depots.items() if depot in wanted)
    return total / view.seconds
