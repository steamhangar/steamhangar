"""App factory for vault-api.

Run natively (Windows dev, no Docker until WP 1.9):

    uvicorn vault_api.main:create_app --factory --host 0.0.0.0 --port 8000

The factory pattern keeps env-var validation (Settings.from_env) and DB
initialization out of module-import time, which makes the app trivially
testable with an in-memory/temp-file Settings instance (see api/tests/).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from vault_api import __version__ as VAULT_API_VERSION
from vault_api.config import Settings
from vault_api.db import get_connection, init_db
from vault_api.jobs import recover_stale_jobs
from vault_api.manifest_ingest import log_cache_dir_canary
from vault_api.routers import (
    agent,
    cache,
    clients,
    games,
    health,
    jobs,
    mapping,
    oracle,
    schedule,
    settings as settings_router,
    stats,
    steam,
)
from vault_api.scheduler import PrefillScheduler
from vault_api.sizes import SizeCache
from vault_api.steam_relay import RelayCache
from vault_api.webhooks import WebhookNotifier, redact_url
from vault_api.webui import install_security_headers, mount_web_ui
from vault_api.worker import PrefillWorker

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start/stop the background threads: the prefill job worker (WP 1.4) and,
    when a window is configured, the scheduler (WP 3.5).

    Startup order matters: stale-claim recovery runs BEFORE the worker starts,
    so a job left 'running' by a process that died mid-job is failed while
    nothing can be concurrently claiming it (see ``jobs.recover_stale_jobs``
    for the rule and its single-process caveat).

    The worker is started even when ``VAULT_STEAMPREFILL_PATH`` is unset: the
    missing path is reported per job, not by refusing to boot, so the rest of
    the API stays available on a partially configured host.
    """
    settings: Settings = app.state.settings

    conn = get_connection(settings.db_path)
    try:
        # WP S-1 (ADR-0012): in queue mode, a 'running' prefill job already
        # handed off to a separate prefill_runner process is left untouched
        # here — this vault-api process restarting says nothing about
        # whether that OTHER process died too. PrefillWorker._run's own
        # reattach check (jobs.find_active_run) picks it back up on its
        # first tick. See recover_stale_jobs's docstring for the full rule.
        recovered = recover_stale_jobs(conn, queue_mode=settings.prefill_mode_queue)
    finally:
        conn.close()
    if recovered:
        logger.warning(
            "Recovered %d job(s) left in 'running' by a previous process; "
            "marked them as 'error'.",
            recovered,
        )

    # WP 3.2 coupling canary (research doc risk 6): warn once at startup if
    # SteamPrefill's temp-cache directory holds files that don't match the
    # filename contract manifest ingestion depends on. Never fails startup —
    # see manifest_ingest.log_cache_dir_canary's docstring.
    log_cache_dir_canary(settings.steamprefill_cache_dir)

    # WP 3.13: started before the worker so the worker's very first job
    # finalization already has somewhere to enqueue a webhook. A no-op thread
    # (start() itself checks settings.webhook_enabled) when VAULT_WEBHOOK_URL
    # is unset — the default.
    webhook_notifier: WebhookNotifier = app.state.webhook_notifier
    webhook_notifier.start()

    worker = PrefillWorker(
        settings, size_cache=app.state.size_cache, webhook_notifier=webhook_notifier
    )
    app.state.worker = worker
    worker.start()

    # WP 3.5: the second background thread. Started AFTER the worker (a sweep
    # is only useful if something is there to drain the queue) and stopped
    # BEFORE it (stop producing jobs before stopping the consumer).
    #
    # Settings-API work package (ADR-0009) fix, reviewer blocker B1: started
    # UNCONDITIONALLY now, never gated on `scheduler.thread_needed` (the BOOT
    # snapshot of "is a window or the event log configured"). Gating on that
    # snapshot was correct before PATCH /v1/settings existed, but is now a
    # bug: a stock deployment boots with no window and no event log, so the
    # thread would never be created, and a later PATCH enabling
    # schedule_window (`applies: "next_sweep"`, per api/README.md) would have
    # nothing to tick it — GET /v1/schedule would report a real
    # `next_eligible_at` that can never arrive. `_tick`'s own body already
    # no-ops cheaply when both sweeps are disabled (`maybe_sweep`/
    # `event_sweep.maybe_sweep` each gate internally on the settings they are
    # actually handed), so the accepted cost is one extra daemon thread
    # waking once a minute to do nothing — the same shape the job worker
    # thread already has on an idle queue.
    scheduler: PrefillScheduler = app.state.scheduler
    scheduler.start()
    if scheduler.enabled:
        window = settings.schedule_window
        assert window is not None  # implied by scheduler_enabled
        logger.info(
            "Scheduler enabled: window %s (server-local time%s), sweeping "
            "every %d minute(s); clients silent for more than %d day(s) are "
            "excluded from the target set.",
            window.raw,
            ", overnight" if window.overnight else "",
            settings.schedule_interval_minutes,
            settings.schedule_client_stale_days,
        )
    else:
        logger.info(
            "Scheduler disabled (VAULT_SCHEDULE_WINDOW is unset). Prefills "
            "only run when something asks for them (POST /v1/prefill). Set a "
            "window like '09:00-17:00' to have vault-api keep the gaming "
            "machines' installed apps current on its own."
        )

    # WP 3.11 (ADR-0008). Independent of the window above: the cache-event
    # sweep rides the same thread but on its own interval and with no window
    # gate at all (it owns the event log's rotation and feeds bypass
    # detection, both of which break if the log goes unread for hours).
    if settings.event_sweep_enabled:
        logger.info(
            "Cache-event sweep enabled: reading %s every %d minute(s). "
            "Miss-triggered prefill is %s (cooldown %d min, at most %d "
            "enqueue(s) per sweep). Bypass detection stays silent until the "
            "feed is %d day(s) old.",
            settings.event_log_path,
            settings.event_sweep_interval_minutes,
            "ON" if settings.miss_trigger_enabled else "OFF",
            settings.miss_trigger_cooldown_minutes,
            settings.miss_trigger_max_per_sweep,
            settings.bypass_window_days,
        )
    else:
        logger.info(
            "Cache-event sweep disabled (VAULT_EVENT_LOG_PATH is unset). No "
            "per-client hit statistics, no bypass detection and no "
            "miss-triggered prefill. Point it at vault-core's structured "
            "event log (VAULT_EVENT_LOG on that side) to switch it on."
        )

    # WP 3.13.
    if settings.webhook_enabled:
        logger.info(
            "Webhook notifications enabled: %s, events=%s, timeout=%.1fs.",
            redact_url(settings.webhook_url),
            sorted(settings.webhook_events),
            settings.webhook_timeout_seconds,
        )
    else:
        logger.info(
            "Webhook notifications disabled (VAULT_WEBHOOK_URL is unset)."
        )

    try:
        yield
    finally:
        scheduler.stop()
        worker.stop()
        # WP 3.13: stopped LAST — a job or sweep finalized microseconds before
        # shutdown may have just enqueued an event, and this gives the
        # delivery thread a last chance to send it rather than dropping it
        # unsent.
        webhook_notifier.stop()


def resolve_log_level(name: str) -> int:
    """``VAULT_LOG_LEVEL`` -> a numeric level, validated against the names
    ``logging`` itself knows (WP API-FIX-2, N3).

    The previous ``getattr(logging, name.upper(), logging.INFO)`` had two
    quiet failure modes: an unknown level (``VAULT_LOG_LEVEL=verbose``)
    silently became INFO with no hint, and a name that IS a ``logging``
    attribute but not a level (``disable``, ``config``, ``root``) reached
    ``basicConfig(level=<function>)`` and crashed startup with a
    ``TypeError`` from deep inside the logging module. Unknown names now log
    one WARNING (at INFO, so it is visible) and fall back to INFO.
    """
    known = logging.getLevelNamesMapping()
    level = known.get(name.strip().upper())
    if level is None:
        logging.getLogger(__name__).warning(
            "VAULT_LOG_LEVEL=%r is not a logging level (expected one of %s); "
            "using INFO.",
            name,
            ", ".join(sorted(known)),
        )
        return logging.INFO
    return level


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI app. Pass `settings` explicitly in tests; omit it to read from env."""
    settings = settings or Settings.from_env()

    logging.basicConfig(level=resolve_log_level(settings.log_level))

    init_db(settings.db_path)

    # openapi_url=None also disables the derived /docs and /redoc routes.
    # Plan §9 ("no unauthenticated endpoints") only carves out /v1/health;
    # Swagger/ReDoc would otherwise expose the full route/schema map without
    # a key, and Swagger UI loads its assets from a CDN anyway (won't work
    # offline on an isolated homelab install).
    # version=VAULT_API_VERSION (WP 4e.7): reconciled with the ONE constant in
    # vault_api/__init__.py rather than a second hardcoded "0.1.0" literal here
    # — this value used to be its own copy and could silently drift from the
    # one GET /v1/settings now also reports.
    app = FastAPI(
        title="vault-api",
        version=VAULT_API_VERSION,
        openapi_url=None,
        lifespan=_lifespan,
    )
    # WP 4a.1. Installed before any route exists: the middleware wraps
    # every response regardless of registration order, but doing it first
    # keeps this file's read order matching request-handling order.
    install_security_headers(app)
    app.state.settings = settings
    # Created here (not inside the lifespan) so it exists for a plain
    # TestClient() too, the same reasoning as app.state.settings above —
    # size-reporting endpoints don't need the worker running to be testable.
    app.state.size_cache = SizeCache(ttl_seconds=settings.size_cache_ttl_seconds)
    # WP 4a.6r: same reasoning as size_cache above -- constructed
    # unconditionally (cheap, no thread, no I/O) so it exists for a plain
    # TestClient() too, and populated lazily on the first relay call.
    app.state.steam_relay_cache = RelayCache()
    # WP 3.13: same reasoning as size_cache — constructed unconditionally so
    # it exists for a plain TestClient() too, but start() (called from the
    # lifespan below) is itself a no-op unless VAULT_WEBHOOK_URL is set.
    app.state.webhook_notifier = WebhookNotifier(settings)
    # WP 3.5: created here rather than in the lifespan (same reasoning as
    # size_cache above) for two reasons: GET /v1/schedule needs it to answer
    # even when the scheduler is disabled or the lifespan never ran (a plain
    # TestClient()), and a test that wants a deterministic clock can replace
    # app.state.scheduler before entering the lifespan, which then starts the
    # replacement. Constructing it starts no thread.
    app.state.scheduler = PrefillScheduler(
        settings, webhook_notifier=app.state.webhook_notifier
    )
    app.include_router(health.router)
    app.include_router(games.router)
    app.include_router(mapping.router)
    app.include_router(jobs.router)
    app.include_router(cache.router)
    app.include_router(agent.router)
    app.include_router(clients.router)
    app.include_router(schedule.router)
    # Settings-API work package (ADR-0009). Always mounted: GET/PATCH answer
    # against whatever the environment configured even on a fresh install
    # with no overrides yet, same reasoning as schedule.router above.
    app.include_router(settings_router.router)
    app.include_router(stats.router)
    # WP 3.9. Always mounted, even with VAULT_MANIFEST_ORACLE unset: the routes
    # answer "enabled: false" rather than 404 in that case, so a client can
    # tell "this vault-api is too old to have an oracle" from "this operator
    # chose not to enable it" — which are different things to show a user.
    app.include_router(oracle.router)
    # WP 4a.6r. Always mounted, even with no key configured: the relay routes
    # answer 409 in that state rather than 404, so a web UI can tell "this
    # vault-api is too old to have the relay" from "this operator hasn't set
    # a key yet" -- same reasoning as the oracle router above.
    app.include_router(steam.router)
    # WP 4a.1. Registered last, by convention (webui.mount_web_ui's own
    # docstring): every route it adds is an exact path or a narrow real
    # asset-subtree prefix (/, /index.html, the SPA view paths, /css, /js —
    # see the WP 4a.1 review fix in webui.py for why this is no longer a
    # catch-all Mount("/")), so it cannot shadow a /v1/* route regardless of
    # order; kept last anyway as a guard against a future collision, and
    # tests/test_webui.py pins the order explicitly. A missing web/
    # directory (the shipped Docker image today, see
    # config._default_web_dir) is not an error: the API works standalone.
    mount_web_ui(app, settings.web_dir)
    return app
