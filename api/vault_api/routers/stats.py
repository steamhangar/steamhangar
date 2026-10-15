"""Cache-event sweep status (WP 3.11, ADR-0008): ``GET /v1/stats``.

Read-only, and small on purpose. It exists because without it the entire
event-sweep feature is **invisible**: the sweeper is a background thread
reading a file nobody looks at, and the three questions an operator actually
has — "is it reading anything?", "is it throwing lines away?", "is it able to
rotate the log?" — would otherwise only be answerable by grepping container
logs or opening the SQLite file.

Two of those questions matter enough to be worth an endpoint on their own:

* ``lines_skipped_total`` climbing means vault-core is writing something this
  vault-api does not understand (a format change, or a truncated write). The
  sweep degrades quietly and correctly in that case — which is exactly why it
  needs a counter, or "quietly" wins.
* ``truncate_denied_count`` climbing means the sweeper is reading the event log
  but is not permitted to rotate it, so the file grows without bound. In the
  shipped containers that is the *default* situation (vault-api runs as uid
  101, the log belongs to vault-core's nginx user), so this is a number an
  operator will really need — see ``event_sweep.maybe_truncate``.

Auth is attached at the router level (secure-by-default pattern, see
api/README.md "Auth").
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from vault_api import event_sweep
from vault_api.auth import require_api_key
from vault_api.config import Settings
from vault_api import deps
from vault_api.deps import DbOpener, db_opener

router = APIRouter(dependencies=[Depends(require_api_key)], tags=["stats"])

#: How many depot rows the response lists. Enough to be useful in the app,
#: small enough that this stays a cheap polling endpoint.
DEPOT_MISS_LIMIT = 20


class DepotMissOut(BaseModel):
    depotid: int
    miss_count: int
    last_seen: str


class StatsOut(BaseModel):
    #: False unless VAULT_EVENT_LOG_PATH is set — the safe default. Every other
    #: field still reports its configured/stored value so an operator can see
    #: what *would* happen before switching it on.
    event_feed_enabled: bool
    sweep_interval_minutes: int
    #: True iff a sweep may enqueue miss-triggered prefills: the feed is on AND
    #: the cooldown is non-zero (a 0 cooldown is the trigger's off switch).
    miss_trigger_enabled: bool
    miss_trigger_cooldown_minutes: int
    miss_trigger_max_per_sweep: int
    bypass_window_days: int
    #: Byte offset into the event log up to which every line has been read and
    #: its effects committed.
    cursor_offset: int
    #: When the feed was first swept — bypass detection stays silent until this
    #: is at least bypass_window_days old.
    first_sweep_at: str | None
    last_sweep_at: str | None
    #: When this sweeper last truncated the log (its rotation contract).
    last_rotated_at: str | None
    lines_read_total: int
    #: Lines skipped as unparseable or of an unknown format version. A rising
    #: number here means vault-core and vault-api disagree about the format.
    lines_skipped_total: int
    last_lines: int | None
    last_skipped: int | None
    last_enqueued: int | None
    #: Miss candidates the per-sweep cap refused on the last sweep.
    last_dropped_by_cap: int | None
    #: How often rotation was refused by the filesystem. NON-ZERO IS AN ALERT:
    #: sweeping is fine, but the event log is growing without bound because
    #: vault-api may read it and not write it. See api/README.md.
    truncate_denied_count: int
    last_truncate_denied_at: str | None
    #: How often the sweeper stepped over a region whose "line" was longer than
    #: a whole 4 MiB read batch. NON-ZERO IS AN ALERT: no valid event line can
    #: be that long, so something other than vault-core is writing to the file
    #: (or it is corrupt), and those bytes were discarded, not retried.
    oversized_skips_total: int
    last_oversized_at: str | None
    #: WP D15b: how often the sweeper found the event log rotated by someone
    #: else -- smaller than the cursor, or replaced by another file (inode
    #: change). Each one reset the cursor to 0. Expected to climb once
    #: vault-core rotates the log itself (D15-ROT); unexpected before that.
    rotations_detected_total: int = 0
    last_rotation_detected_at: str | None = None
    #: WP D15b: when the traffic tiers behind /v1/throughput and
    #: /v1/clients/overview started filling (``null`` = not yet).
    traffic_since: str | None = None
    #: GRAPH-ZOOM (schema v29): when the 1-minute tier started filling.
    traffic_1m_since: str | None = None
    #: Most recently MISSed depots that map to no app — vault-api can neither
    #: attribute nor prefill these (plan §4's manual-mapping fallback case).
    top_unmapped_depots: list[DepotMissOut]


@router.get("/v1/stats", response_model=StatsOut)
def get_stats(
    request: Request,
    open_db: DbOpener = Depends(db_opener),
) -> StatsOut:
    """Cache-event sweep configuration plus its bookkeeping (WP 3.11)."""
    settings: Settings = deps.effective_settings(request)

    with open_db() as conn:
        state = event_sweep.read_state(conn)
        depots = event_sweep.top_depot_misses(conn, DEPOT_MISS_LIMIT)

    return StatsOut(
        event_feed_enabled=settings.event_sweep_enabled,
        sweep_interval_minutes=settings.event_sweep_interval_minutes,
        miss_trigger_enabled=settings.miss_trigger_enabled,
        miss_trigger_cooldown_minutes=settings.miss_trigger_cooldown_minutes,
        miss_trigger_max_per_sweep=settings.miss_trigger_max_per_sweep,
        bypass_window_days=settings.bypass_window_days,
        cursor_offset=state.cursor_offset,
        first_sweep_at=state.first_sweep_at,
        last_sweep_at=state.last_sweep_at,
        last_rotated_at=state.last_rotated_at,
        lines_read_total=state.lines_read_total,
        lines_skipped_total=state.lines_skipped_total,
        last_lines=state.last_lines,
        last_skipped=state.last_skipped,
        last_enqueued=state.last_enqueued,
        last_dropped_by_cap=state.last_dropped_by_cap,
        truncate_denied_count=state.truncate_denied_count,
        last_truncate_denied_at=state.last_truncate_denied_at,
        oversized_skips_total=state.oversized_skips_total,
        last_oversized_at=state.last_oversized_at,
        rotations_detected_total=state.rotations_detected_total,
        last_rotation_detected_at=state.last_rotation_detected_at,
        traffic_since=state.traffic_since,
        traffic_1m_since=state.traffic_1m_since,
        top_unmapped_depots=[
            DepotMissOut(
                depotid=row.depotid,
                miss_count=row.miss_count,
                last_seen=row.last_seen,
            )
            for row in depots
        ],
    )
