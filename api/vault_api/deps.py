"""Shared FastAPI dependencies (request-scoped resources).

Kept separate from db.py so db.py stays framework-agnostic (plain sqlite3
helpers, importable without FastAPI installed) while routers get a normal
FastAPI ``Depends`` dependency.

Why routers get an *opener* instead of a connection (WP 1.4 fix)
---------------------------------------------------------------
WP 1.3's version was a sync generator dependency: open a connection, yield it
to the endpoint, close it in a ``finally``. That is the shape every FastAPI
tutorial shows, and under concurrency it **segfaulted the interpreter** —
reproduced repeatedly on Windows/CPython 3.12 as "Windows fatal exception:
access violation" while hammering the API with parallel requests.

Measured mechanism (not a guess — captured by wrapping ``sqlite3.Connection``
in a subclass that recorded every overlapping use of one connection):

    thread A (anyio worker): conn.execute("BEGIN IMMEDIATE")  <- blocked on the
                                                                 write lock,
                                                                 GIL released
    thread B (event loop):   FastAPI's AsyncExitStack unwinds the dependency
                             -> the old get_db's finally -> conn.close()

Closing a connection while another thread is inside ``sqlite3_step`` on it is a
use-after-free at the C level; sqlite's serialized threading mode protects the
*database*, not CPython's per-connection objects. The window opens whenever the
exit stack unwinds while the body is still running, and write-lock contention
(``PRAGMA busy_timeout``) makes the body block long enough for it to matter.

The fix is structural: **the connection never leaves the thread that created it
and never outlives the endpoint body.** The dependency hands the endpoint a
zero-argument opener; the endpoint does ``with open_db() as conn:``, so open,
use and close all happen inside the single ``run_in_threadpool`` call that runs
the body. Nothing else can close it — and ``check_same_thread`` is back at its
safe default (see ``db.get_connection``), so a future regression is a loud
``ProgrammingError`` instead of a crash.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from typing import Callable, ContextManager

from fastapi import Request

from vault_api import settings_store
from vault_api.config import Settings
from vault_api.db import get_connection
from vault_api.sizes import SizeCache
from vault_api.steam_relay import RelayCache

logger = logging.getLogger(__name__)

#: What routers receive: call it to get a context-managed connection.
DbOpener = Callable[[], ContextManager[sqlite3.Connection]]


def get_cache_root(request: Request) -> str:
    """FastAPI dependency returning the configured ``VAULT_CACHE_ROOT`` (WP 1.5).

    Endpoints that need to read the size cache (``games.py``, ``cache.py``)
    pass this straight through to ``SizeCache.get()`` — mirrors ``db_opener``
    pulling the db path off the same ``app.state.settings``.
    """
    return request.app.state.settings.cache_root


def get_size_cache(request: Request) -> SizeCache:
    """FastAPI dependency returning the app-wide ``SizeCache`` (WP 1.5).

    One instance per app, created in ``main.create_app`` (not inside the
    lifespan) and stored on ``app.state`` — mirrors ``db_opener`` pulling the
    configured db path off ``app.state.settings``. A single shared instance
    is the point: the whole reason for the cache is that concurrent requests
    reuse one disk scan instead of each doing their own.
    """
    return request.app.state.size_cache


def get_steam_relay_cache(request: Request) -> RelayCache:
    """FastAPI dependency returning the app-wide Steam relay ``RelayCache``
    (WP 4a.6r). One instance per app, created in ``main.create_app`` and
    stored on ``app.state`` — same pattern as ``get_size_cache`` above: a
    single shared instance is the point, so repeated web-UI renders reuse one
    Steam Web API call instead of each making its own.
    """
    return request.app.state.steam_relay_cache


def get_settings(request: Request) -> Settings:
    """FastAPI dependency returning the whole ``Settings`` snapshot (WP 3.9).

    The existing dependencies here hand a router exactly the one value it
    needs (``get_cache_root``, ``get_agent_report_keep``) — the right shape
    when a router uses one setting. The oracle router passes ``settings``
    straight through to ``vault_api.oracle``, whose functions each consult the
    enable flag themselves (that is how "off" cannot be bypassed by a
    forgotten check), so handing it the object is the honest signature rather
    than three separate scalars re-assembled on the other side.

    D16b: the EFFECTIVE settings (env > Settings > default), resolved per
    request, so a Settings change to a key read here (oracle timeout, the
    keep counters, the bypass window, the event-sweep interval) applies to
    the next request.
    """
    return effective_settings(request)


def effective_settings(request: Request) -> Settings:
    """Resolve the stored Settings values on top of ``app.state.settings``
    with a connection opened, used and closed in this one call (the
    thread-confinement rule of this module). Fail-soft: on a DB error the
    env/default snapshot is returned."""
    base: Settings = request.app.state.settings
    try:
        with closing(get_connection(base.db_path)) as conn:
            return settings_store.effective_settings(conn, base)
    except sqlite3.Error:
        logger.exception("Could not resolve stored settings for this request.")
        return base


def get_agent_report_keep(request: Request) -> int:
    """FastAPI dependency returning ``VAULT_AGENT_REPORT_KEEP`` (WP 2.4).

    Same pattern as ``get_cache_root``: the retention policy is a setting, and
    ``POST /v1/agent/installed`` prunes inside its own insert transaction, so
    the endpoint needs the number rather than the whole ``Settings`` object.
    """
    return effective_settings(request).agent_report_keep


def db_opener(request: Request) -> DbOpener:
    """FastAPI dependency returning a per-call SQLite connection opener.

    Deliberately NOT a generator dependency (see the module docstring). This
    function touches no sqlite object at all — it only captures the configured
    database path — so FastAPI may run it in whatever thread it likes.
    """
    db_path = request.app.state.settings.db_path

    def open_db() -> ContextManager[sqlite3.Connection]:
        # closing(), not `with conn:` — the latter is sqlite3's *transaction*
        # context manager and never closes the connection.
        return closing(get_connection(db_path))

    return open_db
