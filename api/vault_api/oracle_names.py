"""Oracle names for tracked apps no source names (WP TOOL-NAMES-1, rc21.1;
ADR-0022 addendum 2026-11-01).

## The gap this closes

Steam Tool/Config apps without a store page (prod 2026-11-01: 3990420, a
Tool with no depots; 1716750, a Config app) showed "Unknown app": the store
lookup answers ``success != 1``, they are not in the owned list, and the
oracle's stored ``common.name`` exists only for apps the update check asks
about (cached and not ``not_owned``). ``app_classification.fill_from_oracle``
can only use what the oracle already stored.

## What this does

Once per cover-refresher tick (``cover_art.CoverArtRefresher.tick``), while
the oracle is on, it picks at most ``BATCH_SIZE`` tracked apps that have NO
name from any source and asks the oracle about each, through the very same
``oracle.refresh_app`` the update check uses (same client, same URL, same
validation, same "this request LEAVES the LAN" log line, same snapshot
storage). The answer is stored by ``oracle.store_app_info``, so the next
``fill_from_oracle`` (run in the same tick) supplies the name. Nothing else
about the update check changes: ``scheduler.update_check_appids`` is not
touched, and the update check still asks only about cached apps.

An app is asked only when ALL of these hold (``due_appids``):

* it has an ``apps`` row, its ``apps.name`` is blank, and neither a store
  row nor an oracle row in ``app_classification`` names it;
* it is not on the fixed tool list (``tool_apps``: those carry their own
  name);
* the store lookup has been tried for it at least once (an
  ``app_cover_art`` row exists), so the store gets the first word;
* the oracle has not answered for it within ``ANSWERED_RETRY`` (an
  ``oracle_app_state`` row with a recent ``checked_at``: an answer without
  ``common.name`` is not asked again for that long);
* it is not in this process's failure backoff (``FAIL_BACKOFF_BASE``
  doubling up to ``FAIL_BACKOFF_MAX`` per app).

## Request budget

At most ``BATCH_SIZE`` oracle requests per tick (``cover_art.TICK_SECONDS``,
60 s), and only while unnamed apps are due. A named app never triggers a
request. An app the oracle cannot answer waits ``FAIL_BACKOFF_BASE`` (1 day),
then 2, 4 ... up to ``FAIL_BACKOFF_MAX`` (30 days); one the oracle answered
without a name waits ``ANSWERED_RETRY`` (30 days, persisted through
``oracle_app_state.checked_at``). A failed request also ends the batch and
pauses ALL name lookups for ``GLOBAL_PAUSE`` (1 hour), so a dead oracle costs
at most one request per hour; with K unanswerable apps it therefore takes
about K hours until each was asked once. The failure backoff is in memory only (no
schema step): a restart may ask each still-unanswerable app once more.

## Oracle off

``refresh_app`` checks ``Settings.manifest_oracle_enabled`` itself, and this
module checks it first: with the oracle off no app is selected and no request
is made; the app stays "Unknown app" as before.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from vault_api import app_classification, deletion, oracle, tool_apps
from vault_api.config import Settings
from vault_api.jobs import TIMESTAMP_FORMAT

logger = logging.getLogger(__name__)

#: Oracle requests per refresher tick, at most.
BATCH_SIZE = 2
#: An app the oracle answered without a usable ``common.name`` is asked again
#: after this long (measured from ``oracle_app_state.checked_at``).
ANSWERED_RETRY = timedelta(days=30)
#: Per-app backoff after a failed request: base, doubling, capped.
FAIL_BACKOFF_BASE = timedelta(days=1)
FAIL_BACKOFF_MAX = timedelta(days=30)
#: After any failed request, no name lookup at all for this long.
GLOBAL_PAUSE = timedelta(hours=1)


def _ts(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(TIMESTAMP_FORMAT)


def failure_backoff(attempts: int) -> timedelta:
    """Per-app wait after ``attempts`` consecutive failures (``>= 1``)."""
    step = FAIL_BACKOFF_BASE * (2 ** max(0, min(attempts, 20) - 1))
    return min(step, FAIL_BACKOFF_MAX)


def unnamed_appids(conn: sqlite3.Connection, *, oracle_enabled: bool) -> list[int]:
    """Tracked apps that no source names (``apps.name`` blank, no stored
    store or oracle name), fixed-list tool apps excluded. Sorted. Read-only."""
    blank: list[int] = []
    for row in conn.execute("SELECT appid, name FROM apps"):
        appid = deletion.coerce_positive_id(row["appid"])
        if appid is None or tool_apps.is_tool_app(appid):
            continue
        own = row["name"]
        if isinstance(own, str) and own.strip():
            continue
        blank.append(appid)
    named = app_classification.fallback_names(conn, blank, oracle_enabled=oracle_enabled)
    return sorted(appid for appid in blank if appid not in named)


@dataclass
class NameLookupOutcome:
    requested: tuple[int, ...] = ()
    #: Requests that stored an answer (named or not).
    answered: int = 0
    failed: int = 0


@dataclass
class OracleNameLookup:
    """The per-process state: failure backoff per app and the global pause.
    Driven by the cover refresher's single thread, so no locking."""

    settings: Settings
    fetch: oracle.Fetcher | None = None
    _attempts: dict[int, int] = field(default_factory=dict)
    _next_try: dict[int, datetime] = field(default_factory=dict)
    _paused_until: datetime | None = None

    @property
    def enabled(self) -> bool:
        return self.settings.manifest_oracle_enabled

    def due_appids(self, conn: sqlite3.Connection, now: datetime) -> list[int]:
        """Up to ``BATCH_SIZE`` unnamed apps to ask now (module docstring)."""
        if not self.enabled:
            return []
        candidates = unnamed_appids(conn, oracle_enabled=True)
        if not candidates:
            return []
        store_tried = {
            int(row["appid"]) for row in conn.execute("SELECT appid FROM app_cover_art")
        }
        cutoff = _ts(now - ANSWERED_RETRY)
        answered_recently = {
            int(row["appid"])
            for row in conn.execute(
                "SELECT appid FROM oracle_app_state WHERE checked_at > ?", (cutoff,)
            )
        }
        due: list[int] = []
        for appid in candidates:
            if appid not in store_tried or appid in answered_recently:
                continue
            next_try = self._next_try.get(appid)
            if next_try is not None and now < next_try:
                continue
            due.append(appid)
            if len(due) >= BATCH_SIZE:
                break
        return due

    def run_once(self, conn: sqlite3.Connection, now: datetime) -> NameLookupOutcome:
        """Ask the oracle about one batch of due apps. Never raises for an
        oracle problem (``refresh_app`` never does)."""
        if not self.enabled:
            return NameLookupOutcome()
        if self._paused_until is not None and now < self._paused_until:
            return NameLookupOutcome()
        appids = self.due_appids(conn, now)
        if not appids:
            return NameLookupOutcome()
        asked: list[int] = []
        answered = failed = 0
        for appid in appids:
            logger.info(
                "oracle name lookup: app %s has no name from any source; asking "
                "the manifest oracle (metadata only)",
                appid,
            )
            asked.append(appid)
            result = oracle.refresh_app(conn, appid, settings=self.settings, fetch=self.fetch)
            if result.ok:
                answered += 1
                self._attempts.pop(appid, None)
                self._next_try.pop(appid, None)
                continue
            failed += 1
            attempts = self._attempts.get(appid, 0) + 1
            self._attempts[appid] = attempts
            self._next_try[appid] = now + failure_backoff(attempts)
            self._paused_until = now + GLOBAL_PAUSE
            logger.info(
                "oracle name lookup: app %s not answered (%s); next try for it "
                "after %s, all name lookups paused until %s",
                appid,
                result.error,
                _ts(self._next_try[appid]),
                _ts(self._paused_until),
            )
            break
        return NameLookupOutcome(requested=tuple(asked), answered=answered, failed=failed)
