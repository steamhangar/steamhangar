"""Scheduler endpoint (WP 3.5): ``GET /v1/schedule``.

**This module itself has no write verb — that claim used to be stronger and
is now corrected (reviewer should-fix S3, 2026-08-18 review round).** Through
WP 3.5 there really was no way to change the window, interval, staleness
bound or (later, WP 4d) the cached-apps sweep mode at runtime short of
editing the environment and restarting. Since the settings-API work package
(ADR-0009), ``PATCH /v1/settings`` IS that write path for every field this
endpoint reports except the bookkeeping ones (``last_sweep_at``,
``last_sweep_targets``, ``last_sweep_enqueued``, ``next_eligible_at``) — see
``vault_api/settings_store.py`` and api/README.md "Persisted settings". This
router stays read-only, but "vault-api has no config-write API" is no longer
true of the system as a whole, and this docstring must not be the one place
that still says otherwise.

What ``GET /v1/schedule`` answers is "what WILL the scheduler do, and what
did it last do", which is the question you actually have when a game did not
get updated overnight — always the EFFECTIVE, override-resolved
configuration (``settings_store.effective_settings``), never the raw
env/default snapshot, so a recent ``PATCH`` is reflected here immediately.

Auth is attached at the router level (secure-by-default pattern, see
api/README.md "Auth").
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from vault_api import scheduler as scheduler_module
from vault_api import settings_store
from vault_api.auth import require_api_key
from vault_api.deps import DbOpener, db_opener

router = APIRouter(dependencies=[Depends(require_api_key)], tags=["schedule"])


def _format_offset(moment: datetime) -> str:
    """``UTC+02:00`` for the server's current local offset.

    WP SWEEP-1 follow-up: delegates to ``scheduler.format_utc_offset`` now
    that a second caller (the scheduler's startup log line,
    ``describe_resolved_schedule``) needs the identical computation — one
    implementation, not two that could quietly diverge (docs/LEARNINGS.md
    "two call sites computing the same domain predicate WILL diverge").
    """
    return scheduler_module.format_utc_offset(moment)


class ScheduleOut(BaseModel):
    #: False unless VAULT_SCHEDULE_WINDOW is set — the safe default. Every
    #: field below still reports the configured values so an operator can see
    #: what *would* happen before switching it on.
    enabled: bool
    #: The window exactly as configured, or null when unset.
    window: str | None
    #: True for a window that wraps past midnight (e.g. "22:00-06:00").
    overnight: bool
    interval_minutes: int
    #: Clients whose newest agent report is older than this are excluded from
    #: the target set.
    client_stale_days: int
    #: The window is interpreted in SERVER-LOCAL time; this is that zone's
    #: current UTC offset. Every timestamp in this response is UTC.
    server_timezone: str
    #: When the last sweep STARTED (UTC). Null if none has ever run.
    last_sweep_at: str | None
    #: How many apps the last sweep enqueued for (its target set plus the
    #: incomplete games it completed that were no target, WP
    #: CACHE-COMPLETE-1b), and how many NEW jobs it created (apps that
    #: already had a queued/running job are not counted — the queue dedupes).
    #: Both null while a sweep is in flight, or if the process died during
    #: one.
    last_sweep_targets: int | None
    last_sweep_enqueued: int | None
    #: Earliest UTC instant a sweep could next run (interval gate, then window
    #: gate). An estimate computed from the current clock, not a promise — it
    #: can be an hour off across a DST transition, and it is null when the
    #: scheduler is disabled. Nothing decides anything from this value.
    next_eligible_at: str | None
    #: WP 4d. Effective value of ``sweep_include_cached``/
    #: ``VAULT_SWEEP_INCLUDE_CACHED`` — true iff the NEXT sweep will also
    #: target every app that holds content of its own on disk, not only the
    #: installed union. On by default since WP SWEEP-1 / ADR-0014
    #: (2026-08-22) — was off through WP 4d; see api/README.md "Sweep target
    #: set". Meaningless unless a sweep actually runs at all, i.e. unless
    #: ``enabled`` above is true (``VAULT_SCHEDULE_WINDOW`` configured) — this
    #: field reports the value the flag WOULD apply with, not a promise a
    #: sweep is happening.
    sweep_include_cached: bool
    #: WP 4d. True iff ``sweep_include_cached`` is on while ``VAULT_AUTO_GC``
    #: is anything OTHER than ``execute`` — the exact condition
    #: ``scheduler.cached_sweep_gc_risk`` names and the scheduler's own
    #: one-time log warning fires on. ``dry-run`` counts as risky too (B2,
    #: 2026-08-18 review round): it reports what could be reclaimed and
    #: reclaims nothing, so it does not change the growth this flag is about.
    #: ``False`` asserts "orphans created by refreshes are actually being
    #: reclaimed", not merely "someone configured GC". A UI can render this
    #: as a banner without re-deriving the two settings' interaction itself.
    sweep_cached_gc_risk: bool


@router.get("/v1/schedule", response_model=ScheduleOut)
def get_schedule(
    request: Request,
    open_db: DbOpener = Depends(db_opener),
) -> ScheduleOut:
    """Scheduler configuration plus last-sweep bookkeeping (WP 3.5).

    Reads the schedule window/interval/staleness bound through
    ``settings_store.effective_settings`` (settings-API work package,
    ADR-0009) rather than ``scheduler.settings`` directly, so a
    ``PATCH /v1/settings`` override shows up here immediately — this
    endpoint answers "what will the scheduler do", and after an override it
    would otherwise describe a configuration the next tick has already
    stopped using (see ``vault_api/scheduler.py``'s own tick-time resolution
    of the identical override).
    """
    scheduler = request.app.state.scheduler
    now = scheduler.now()

    with open_db() as conn:
        state = scheduler_module.read_state(conn)
        settings = settings_store.effective_settings(conn, scheduler.settings)
    window = settings.schedule_window

    return ScheduleOut(
        enabled=settings.scheduler_enabled,
        window=None if window is None else window.raw,
        overnight=bool(window is not None and window.overnight),
        interval_minutes=settings.schedule_interval_minutes,
        client_stale_days=settings.schedule_client_stale_days,
        server_timezone=_format_offset(now),
        last_sweep_at=state.last_sweep_at,
        last_sweep_targets=state.last_sweep_targets,
        last_sweep_enqueued=state.last_sweep_enqueued,
        next_eligible_at=scheduler_module.next_eligible_at(settings, state, now),
        sweep_include_cached=settings.sweep_include_cached,
        sweep_cached_gc_risk=scheduler_module.cached_sweep_gc_risk(settings),
    )
