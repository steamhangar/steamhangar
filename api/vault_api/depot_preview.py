"""WP D19-1a DEPOT-PREVIEW-1a: expected depots and size for a game that was
never downloaded (schema v32).

Opening the card of such a game asks the manifest oracle ONE question, in the
background, and the card fills in when the answer is stored. Guarantees:

1. **Gated twice, off by default.** Nothing leaves the LAN unless
   ``VAULT_DEPOT_PREVIEW`` (env-only, default off) AND the oracle are both on.
   The oracle alone is not enough: it is on by default, and a query per
   browsed card shows what a household member looks at
   (``docs/security/privacy-review.md`` O2 / F1). No new host: the same
   oracle URL the update check uses, through the same fetcher.
2. **An expectation, never an observation.** The answer lands in its own two
   tables. It never writes ``depot_app_map`` (delete and GC trust that table
   as observed) nor ``oracle_branch_manifests`` (the GC keep set).
3. **Asked at most once per TTL and per app.** A stored answer (24 h) or a
   stored failure (30 min) is served without a query; concurrent opens share
   one in-flight request; a global rate limit bounds a burst of different apps.
4. **Nobody is recorded.** One app id, the public branch, no client id and no
   address is stored or logged with it.
5. **Fail-soft.** Parse errors, timeouts and database errors end as a stored
   ``failed`` outcome (or nothing), never as an exception to the request.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections import deque
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable

from vault_api import deletion, jobs, oracle
from vault_api.config import Settings
from vault_api.db import get_connection

logger = logging.getLogger(__name__)

#: A stored answer is served this long without asking again.
TTL = timedelta(hours=24)
#: A stored failure is served this long before the next attempt (back-off).
FAILURE_TTL = timedelta(minutes=30)
#: At most this many oracle queries START per ``RATE_WINDOW_SECONDS``, for all
#: apps together. Opening cards is human-paced; this only bounds a bug or a
#: script walking the library.
RATE_LIMIT = 10
RATE_WINDOW_SECONDS = 60.0

#: Sizes are bounded to what a depot can plausibly be (1 PiB).
MAX_BYTES = 2**50
MAX_DEPOTS = oracle.MAX_DEPOTS

#: ``state`` values of the endpoint.
STATE_OFF = "off"  # switch or oracle off: nothing was asked
STATE_CACHED = "cached"  # the vault observed depots; the preview is not needed
STATE_PENDING = "pending"  # a query is running; ask again in a moment
STATE_READY = "ready"
STATE_UNAVAILABLE = "unavailable"  # failed, rate limited or no usable depots

OUTCOME_OK = "ok"
OUTCOME_FAILED = "failed"


def enabled(settings: Settings) -> bool:
    """Own switch AND the oracle. Every path here consults this itself."""
    return bool(settings.depot_preview_enabled and settings.manifest_oracle_enabled)


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PreviewDepot:
    depotid: int
    #: ``False`` for a depot without a public manifest (typically a DLC depot);
    #: its sizes are ``None``.
    manifest: bool
    size_bytes: int | None = None
    download_bytes: int | None = None
    dlcappid: int | None = None


@dataclass(frozen=True)
class PreviewInfo:
    appid: int
    depots: tuple[PreviewDepot, ...]


def _bytes(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    try:
        number = int(str(value).strip())
    except ValueError:
        return None
    return number if 0 <= number <= MAX_BYTES else None


def _other_os_only(raw_depot: dict[str, object]) -> bool:
    """A depot restricted to other operating systems than Windows.

    ``config.oslist`` is a comma list; an absent or empty list means every OS.
    Assumption (documented in the ADR note of this package): the prefill tool
    fetches the Windows depots, so macOS/Linux-only depots would inflate the
    expected size and are left out.
    """
    config = raw_depot.get("config")
    if not isinstance(config, dict):
        return False
    oslist = config.get("oslist")
    if not isinstance(oslist, str) or not oslist.strip():
        return False
    return "windows" not in {part.strip().lower() for part in oslist.split(",")}


def parse_preview(appid: int, payload: bytes) -> PreviewInfo:
    """One oracle answer to the expected depots of the public branch.

    Raises ``oracle.OracleError`` when the answer is unusable as a whole (same
    contract as ``oracle.parse_app_info``, whose envelope checks it reuses).
    A single odd depot is skipped, not fatal.
    """
    app = oracle._app_object(appid, oracle._decode_json(payload))
    raw_depots = app.get("depots")
    if not isinstance(raw_depots, dict):
        raise oracle.OracleError("no usable 'depots' object")

    found: list[PreviewDepot] = []
    for raw_id, raw_depot in raw_depots.items():
        if raw_id in oracle.KNOWN_NON_DEPOT_KEYS:
            continue
        depotid = deletion.coerce_positive_id(raw_id)
        if depotid is None or not isinstance(raw_depot, dict):
            continue
        if len(found) >= MAX_DEPOTS:
            break
        if _other_os_only(raw_depot):
            continue
        dlc = raw_depot.get("dlcappid")
        dlcappid = (
            deletion.coerce_positive_id(dlc) if isinstance(dlc, (str, int)) else None
        )
        manifests = raw_depot.get("manifests")
        public = manifests.get(oracle.BRANCH_PUBLIC) if isinstance(manifests, dict) else None
        if isinstance(public, (dict, str)) and oracle._extract_gid(public) is not None:
            sizes = public if isinstance(public, dict) else {}
            found.append(
                PreviewDepot(
                    depotid=depotid,
                    manifest=True,
                    size_bytes=_bytes(sizes.get("size")),
                    download_bytes=_bytes(sizes.get("download")),
                    dlcappid=dlcappid,
                )
            )
        else:
            found.append(PreviewDepot(depotid=depotid, manifest=False, dlcappid=dlcappid))
    found.sort(key=lambda d: d.depotid)
    return PreviewInfo(appid=appid, depots=tuple(found))


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class StoredPreview:
    appid: int
    outcome: str
    fetched_at: str
    depots: tuple[PreviewDepot, ...]

    @property
    def total_size_bytes(self) -> int | None:
        return _sum(d.size_bytes for d in self.depots if d.manifest)

    @property
    def total_download_bytes(self) -> int | None:
        return _sum(d.download_bytes for d in self.depots if d.manifest)


def _sum(values: Iterable[int | None]) -> int | None:
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def store_outcome(
    conn: sqlite3.Connection,
    appid: int,
    outcome: str,
    *,
    info: PreviewInfo | None = None,
    fetched_at: str | None = None,
) -> None:
    """Replace the app's preview in one transaction. A failure keeps no depots
    (an old answer is dropped rather than shown as current after a failed
    refresh: the preview only exists for never-downloaded games, so it is
    cheap to ask again)."""
    stamp = fetched_at or jobs.utcnow_iso()
    with jobs.immediate_transaction(conn):
        conn.execute("DELETE FROM depot_preview WHERE appid = ?", (appid,))
        if info is not None:
            conn.executemany(
                """
                INSERT INTO depot_preview
                    (appid, depotid, manifest, size_bytes, download_bytes, dlcappid)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        appid,
                        d.depotid,
                        1 if d.manifest else 0,
                        d.size_bytes,
                        d.download_bytes,
                        d.dlcappid,
                    )
                    for d in info.depots
                ],
            )
        conn.execute(
            """
            INSERT INTO depot_preview_state (appid, outcome, fetched_at, source)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (appid) DO UPDATE SET
                outcome = excluded.outcome,
                fetched_at = excluded.fetched_at,
                source = excluded.source
            """,
            (appid, outcome, stamp, oracle.SOURCE_STEAMCMD_API),
        )


def load_preview(conn: sqlite3.Connection, appid: int) -> StoredPreview | None:
    row = conn.execute(
        "SELECT outcome, fetched_at FROM depot_preview_state WHERE appid = ?", (appid,)
    ).fetchone()
    if row is None:
        return None
    depots = tuple(
        PreviewDepot(
            depotid=r["depotid"],
            manifest=bool(r["manifest"]),
            size_bytes=r["size_bytes"],
            download_bytes=r["download_bytes"],
            dlcappid=r["dlcappid"],
        )
        for r in conn.execute(
            "SELECT depotid, manifest, size_bytes, download_bytes, dlcappid "
            "FROM depot_preview WHERE appid = ? ORDER BY depotid",
            (appid,),
        )
    )
    return StoredPreview(appid, row["outcome"], row["fetched_at"], depots)


def clear_preview(conn: sqlite3.Connection, appid: int) -> None:
    with jobs.immediate_transaction(conn):
        conn.execute("DELETE FROM depot_preview WHERE appid = ?", (appid,))
        conn.execute("DELETE FROM depot_preview_state WHERE appid = ?", (appid,))


def _is_fresh(stored: StoredPreview, now: datetime) -> bool:
    try:
        fetched = datetime.strptime(stored.fetched_at, jobs.TIMESTAMP_FORMAT).replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return False
    ttl = TTL if stored.outcome == OUTCOME_OK else FAILURE_TTL
    return now - fetched < ttl


# --------------------------------------------------------------------------
# The on-open service
# --------------------------------------------------------------------------


def _observed_depots(conn: sqlite3.Connection, appid: int) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM depot_app_map WHERE appid = ? LIMIT 1", (appid,)
        ).fetchone()
        is not None
    )


@dataclass(frozen=True)
class PreviewResult:
    state: str
    stored: StoredPreview | None = None


def _run_in_thread(job: Callable[[], None]) -> None:
    threading.Thread(target=job, name="vault-depot-preview", daemon=True).start()


class DepotPreviewService:
    """Decides whether to ask the oracle, and does so off the request thread.

    ``run`` starts the background job (tests pass ``lambda job: job()``);
    ``fetch`` replaces the HTTP fetcher (tests only); ``clock`` and
    ``monotonic`` make the TTL and the rate limit testable.
    """

    def __init__(
        self,
        *,
        run: Callable[[Callable[[], None]], None] = _run_in_thread,
        fetch: oracle.Fetcher | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._run = run
        self._fetch = fetch
        self._clock = clock
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._in_flight: set[int] = set()
        self._starts: deque[float] = deque()

    def lookup(self, conn: sqlite3.Connection, appid: int, settings: Settings) -> PreviewResult:
        """Answer one card open. The only place a query can start."""
        if not enabled(settings):
            return PreviewResult(STATE_OFF)
        if _observed_depots(conn, appid):
            return PreviewResult(STATE_CACHED)

        stored = load_preview(conn, appid)
        if stored is not None and _is_fresh(stored, self._clock()):
            return PreviewResult(self._state_of(stored), stored)

        with self._lock:
            if appid in self._in_flight:
                return PreviewResult(STATE_PENDING)
            now = self._monotonic()
            while self._starts and now - self._starts[0] >= RATE_WINDOW_SECONDS:
                self._starts.popleft()
            if len(self._starts) >= RATE_LIMIT:
                # Nothing is stored: a later open asks again once the window
                # has passed.
                return PreviewResult(STATE_UNAVAILABLE, stored)
            self._starts.append(now)
            self._in_flight.add(appid)

        db_path = settings.db_path
        try:
            self._run(lambda: self._fetch_and_store(db_path, appid, settings))
        except Exception:  # starting the thread failed: release the slot
            logger.exception("depot preview: could not start the background query")
            with self._lock:
                self._in_flight.discard(appid)
            return PreviewResult(STATE_UNAVAILABLE, stored)
        # With an inline runner the answer is already stored.
        with self._lock:
            still_running = appid in self._in_flight
        if still_running:
            return PreviewResult(STATE_PENDING)
        fresh = load_preview(conn, appid)
        return PreviewResult(self._state_of(fresh) if fresh else STATE_UNAVAILABLE, fresh)

    @staticmethod
    def _state_of(stored: StoredPreview) -> str:
        if stored.outcome == OUTCOME_OK and stored.depots:
            return STATE_READY
        return STATE_UNAVAILABLE

    def _fetch_and_store(self, db_path: str, appid: int, settings: Settings) -> None:
        try:
            outcome, info = OUTCOME_FAILED, None
            try:
                fetcher = self._fetch or oracle._configured_fetcher(settings)
                url = oracle.app_info_url(settings.manifest_oracle_url, appid)
                info = parse_preview(appid, fetcher(url))
                outcome = OUTCOME_OK
            except oracle.OracleError as exc:
                logger.warning("depot preview: app %s failed: %s", appid, exc)
            except Exception:
                logger.exception("depot preview: app %s crashed", appid)
            try:
                with closing(get_connection(db_path)) as conn:
                    store_outcome(conn, appid, outcome, info=info)
            except sqlite3.Error as exc:
                logger.warning("depot preview: could not store app %s: %s", appid, exc)
        finally:
            with self._lock:
                self._in_flight.discard(appid)
