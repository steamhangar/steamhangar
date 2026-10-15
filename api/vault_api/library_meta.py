"""Durable app metadata for the library filters (WP D23 LIBRARY-META-1a/1b).

What it stores, per app, from the manifest oracle's app-info answer (the SAME
document ``oracle.py`` already fetches, so no new host and no new kind of
request): category flags (single-player, multiplayer, co-op, VR), genre ids,
the overall review percentage and score, and the release year. The table is
display data only and is kept apart from ``depot_app_map`` and the oracle's
depot tables: nothing in GC, deletion or the update check reads it.

**What leaves the LAN.** The nightly job asks the oracle for every app the
vault knows (see ``known_appids``), one request per app, spaced
``PAUSE_SECONDS`` apart, only inside the schedule window. That is more appids
at once than browsing or the cached-game poll sends, so it has its OWN switch,
``VAULT_LIBRARY_META`` (env-only, **default off**), and it additionally needs
the oracle itself to be on (``oracle.manifest_oracle_enabled``) and a schedule
window to be configured. With any of the three missing, nothing runs and
nothing is written; the filters then simply see "unknown" for every app. No
SteamID, key or ownership data is involved.

**Unknown never hides an app silently.** An app without a row (or whose row
lacks the field a filter asks about) is "unknown". By default a filter leaves
unknown apps out, but ``include_unknown=true`` keeps them and the list
endpoint reports how many a filter left out (``X-Meta-Unknown-Excluded``).

**Resumable and polite.** There is no run-level state: each app carries its
own ``fetched_at`` (re-asked after ``REFRESH_DAYS``), ``fail_count`` and
``next_try_at`` (exponential backoff), so a run that the window end, a
shutdown or ``CONSECUTIVE_FAILURE_LIMIT`` failures in a row stops simply
continues from the same selection the next night.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable

from vault_api import jobs, oracle
from vault_api.config import Settings
from vault_api.db import get_connection
from vault_api.schedule_window import ScheduleWindow

logger = logging.getLogger(__name__)

#: Provenance tag stored with every row.
SOURCE_ORACLE = "oracle"

#: An app is asked again this long after its last successful fetch.
REFRESH_DAYS = 30
#: Spacing of the nightly requests (about one app per two seconds).
PAUSE_SECONDS = 2.0
#: The run stops after this many failures in a row (a dead oracle is not
#: hammered once per known app); the per-app backoff already moved on.
CONSECUTIVE_FAILURE_LIMIT = 5
#: Per-app backoff after a failure: base * 2 ** (fail_count - 1), capped.
BACKOFF_BASE_HOURS = 6
BACKOFF_CAP_DAYS = 7

# Steam category ids (``common.category_<id>``). Only these four filters exist.
# 31 is the older "VR Support" id, 53/54 are "VR Supported"/"VR Only".
SINGLE_CATEGORIES = frozenset({2})
MULTI_CATEGORIES = frozenset({1, 36, 37, 47, 49, 27})
COOP_CATEGORIES = frozenset({9, 38, 39, 48})
VR_CATEGORIES = frozenset({31, 53, 54})

#: The values ``category=`` accepts, each mapped to its ``app_meta`` column.
CATEGORY_COLUMNS = {
    "single": "flag_single",
    "multi": "flag_multi",
    "coop": "flag_coop",
    "vr": "flag_vr",
}

MAX_GENRES = 64
_MIN_YEAR = 1970
_MAX_YEAR = 2100


def _never() -> bool:
    return False


def _no_pause() -> None:
    return None


def enabled(settings: Settings) -> bool:
    """May the nightly job run at all? Own switch AND the oracle on."""
    return bool(settings.library_meta_enabled and settings.manifest_oracle_enabled)


# --------------------------------------------------------------------------
# Parsing (fail-soft: a missing or malformed field means "unknown")
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AppMeta:
    """One validated metadata answer. ``None`` = the answer did not say."""

    appid: int
    flag_single: bool | None = None
    flag_multi: bool | None = None
    flag_coop: bool | None = None
    flag_vr: bool | None = None
    genres: tuple[int, ...] = ()
    review_percent: int | None = None
    review_score: int | None = None
    release_year: int | None = None


def _int_in_range(value: object, low: int, high: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    try:
        number = int(str(value).strip())
    except ValueError:
        return None
    return number if low <= number <= high else None


def _year_from_timestamp(value: object) -> int | None:
    stamp = _int_in_range(value, 1, 4_102_444_800)
    if stamp is None:
        return None
    year = datetime.fromtimestamp(stamp, tz=timezone.utc).year
    return year if _MIN_YEAR <= year <= _MAX_YEAR else None


def _category_ids(common: dict) -> set[int] | None:
    """The ids of the ``category_<id>`` keys with a truthy value; ``None`` when
    the answer has no category keys at all (flags are then unknown, not 0)."""
    ids: set[int] = set()
    seen_key = False
    for key, value in common.items():
        if not isinstance(key, str) or not key.startswith("category_"):
            continue
        seen_key = True
        number = _int_in_range(key[len("category_"):], 1, 10_000)
        if number is not None and str(value).strip() not in ("", "0"):
            ids.add(number)
    return ids if seen_key else None


def parse_app_meta(appid: int, payload: bytes) -> AppMeta:
    """Validate one raw oracle response into an ``AppMeta``.

    Raises ``oracle.OracleError`` only when the document is unusable as a
    whole (same contract as ``oracle.parse_app_info``, whose envelope checks
    are reused). A missing ``common`` object yields an all-unknown ``AppMeta``.
    """
    app = oracle._app_object(appid, oracle._decode_json(payload))
    common = app.get("common")
    if not isinstance(common, dict):
        return AppMeta(appid=appid)

    categories = _category_ids(common)
    genres: list[int] = []
    raw_genres = common.get("genres")
    if isinstance(raw_genres, dict):
        for value in raw_genres.values():
            genre = _int_in_range(value, 1, 100_000)
            if genre is not None and genre not in genres:
                genres.append(genre)
            if len(genres) >= MAX_GENRES:
                break

    year = _year_from_timestamp(common.get("steam_release_date"))
    if year is None:
        year = _year_from_timestamp(common.get("original_release_date"))

    def flag(wanted: frozenset[int]) -> bool | None:
        return None if categories is None else bool(categories & wanted)

    return AppMeta(
        appid=appid,
        flag_single=flag(SINGLE_CATEGORIES),
        flag_multi=flag(MULTI_CATEGORIES),
        flag_coop=flag(COOP_CATEGORIES),
        flag_vr=flag(VR_CATEGORIES),
        genres=tuple(sorted(genres)),
        review_percent=_int_in_range(common.get("review_percentage"), 0, 100),
        review_score=_int_in_range(common.get("review_score"), 0, 9),
        release_year=year,
    )


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------


def _as_int(value: bool | None) -> int | None:
    return None if value is None else int(value)


def store_meta(conn: sqlite3.Connection, meta: AppMeta, *, fetched_at: str) -> None:
    """Replace the app's row and genre rows atomically; clears the failure
    bookkeeping. Columns the answer did not carry become NULL (unknown)."""
    with jobs.immediate_transaction(conn):
        conn.execute(
            """
            INSERT INTO app_meta
                (appid, flag_single, flag_multi, flag_coop, flag_vr,
                 review_percent, review_score, release_year,
                 fetched_at, source, fail_count, next_try_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL)
            ON CONFLICT (appid) DO UPDATE SET
                flag_single    = excluded.flag_single,
                flag_multi     = excluded.flag_multi,
                flag_coop      = excluded.flag_coop,
                flag_vr        = excluded.flag_vr,
                review_percent = excluded.review_percent,
                review_score   = excluded.review_score,
                release_year   = excluded.release_year,
                fetched_at     = excluded.fetched_at,
                source         = excluded.source,
                fail_count     = 0,
                next_try_at    = NULL
            """,
            (
                meta.appid,
                _as_int(meta.flag_single),
                _as_int(meta.flag_multi),
                _as_int(meta.flag_coop),
                _as_int(meta.flag_vr),
                meta.review_percent,
                meta.review_score,
                meta.release_year,
                fetched_at,
                SOURCE_ORACLE,
            ),
        )
        conn.execute("DELETE FROM app_meta_genres WHERE appid = ?", (meta.appid,))
        conn.executemany(
            "INSERT INTO app_meta_genres (appid, genre_id) VALUES (?, ?)",
            [(meta.appid, genre) for genre in meta.genres],
        )


def record_failure(conn: sqlite3.Connection, appid: int, *, now: datetime) -> None:
    """Count a failed fetch and push ``next_try_at`` out (exponential, capped).
    Previously stored values are kept: stale beats nothing."""
    with jobs.immediate_transaction(conn):
        row = conn.execute(
            "SELECT fail_count FROM app_meta WHERE appid = ?", (appid,)
        ).fetchone()
        fails = (int(row["fail_count"]) if row is not None else 0) + 1
        delay = min(
            timedelta(hours=BACKOFF_BASE_HOURS * 2 ** (fails - 1)),
            timedelta(days=BACKOFF_CAP_DAYS),
        )
        next_try = (now + delay).astimezone(timezone.utc).strftime(jobs.TIMESTAMP_FORMAT)
        conn.execute(
            """
            INSERT INTO app_meta (appid, fail_count, next_try_at)
            VALUES (?, ?, ?)
            ON CONFLICT (appid) DO UPDATE SET
                fail_count = excluded.fail_count,
                next_try_at = excluded.next_try_at
            """,
            (appid, fails, next_try),
        )


def known_appids(conn: sqlite3.Connection) -> list[int]:
    """Every app the vault knows: tracked apps, mapped apps, apps with job
    history and apps the oracle has answered for. No ownership data."""
    rows = conn.execute(
        """
        SELECT appid FROM apps
        UNION SELECT appid FROM depot_app_map
        UNION SELECT appid FROM jobs
        UNION SELECT appid FROM oracle_app_state
        """
    ).fetchall()
    return sorted(int(row["appid"]) for row in rows if int(row["appid"]) > 0)


def due_appids(conn: sqlite3.Connection, *, now: datetime) -> list[int]:
    """Known apps to fetch now: never fetched first (by id), then the oldest
    fetch older than ``REFRESH_DAYS``; apps still backing off are skipped."""
    stamp = now.astimezone(timezone.utc).strftime(jobs.TIMESTAMP_FORMAT)
    cutoff = (now - timedelta(days=REFRESH_DAYS)).astimezone(timezone.utc).strftime(
        jobs.TIMESTAMP_FORMAT
    )
    state = {
        int(row["appid"]): row
        for row in conn.execute(
            "SELECT appid, fetched_at, next_try_at FROM app_meta"
        )
    }
    never: list[int] = []
    old: list[tuple[str, int]] = []
    for appid in known_appids(conn):
        row = state.get(appid)
        if row is not None and row["next_try_at"] and row["next_try_at"] > stamp:
            continue
        if row is None or not row["fetched_at"]:
            never.append(appid)
        elif row["fetched_at"] < cutoff:
            old.append((row["fetched_at"], appid))
    return never + [appid for _, appid in sorted(old)]


# --------------------------------------------------------------------------
# Fetching (the oracle's own fetcher: same URL, same bounds, same lock-down)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FetchRun:
    """What one ``fill`` pass did."""

    selected: int = 0
    ok: int = 0
    failed: int = 0
    aborted: bool = False
    bailed_out: bool = False


def refresh_meta(
    conn: sqlite3.Connection,
    appid: int,
    *,
    settings: Settings,
    now: datetime,
    fetch: oracle.Fetcher | None = None,
) -> bool:
    """Fetch, validate and store one app. Never raises; ``False`` = failed
    (the failure is recorded for backoff)."""
    fetcher = fetch if fetch is not None else oracle._configured_fetcher(settings)
    try:
        payload = fetcher(oracle.app_info_url(settings.manifest_oracle_url, appid))
        meta = parse_app_meta(appid, payload)
        store_meta(conn, meta, fetched_at=jobs.utcnow_iso())
        return True
    except Exception as exc:  # OracleError, sqlite3.Error or a fetcher bug
        logger.warning("library meta: app %s failed: %s", appid, exc)
        try:
            record_failure(conn, appid, now=now)
        except sqlite3.Error:
            logger.exception("library meta: could not record the failure for %s", appid)
        return False


def fill(
    conn: sqlite3.Connection,
    settings: Settings,
    *,
    now: Callable[[], datetime],
    fetch: oracle.Fetcher | None = None,
    should_continue: Callable[[], bool] = lambda: True,
    should_abort: Callable[[], bool] = _never,
    pause: Callable[[], None] = _no_pause,
    appids: Iterable[int] | None = None,
) -> FetchRun:
    """Fetch every due app, in order, until done, out of window, aborted or
    ``CONSECUTIVE_FAILURE_LIMIT`` failures in a row. A no-op unless
    ``enabled(settings)``."""
    if not enabled(settings):
        return FetchRun()
    selected = list(appids) if appids is not None else due_appids(conn, now=now())
    ok = failed = streak = 0
    aborted = bailed = False
    for index, appid in enumerate(selected):
        if should_abort():
            aborted = True
            break
        if not should_continue():
            break
        if index:
            pause()
            if should_abort():
                aborted = True
                break
        if refresh_meta(conn, appid, settings=settings, now=now(), fetch=fetch):
            ok += 1
            streak = 0
        else:
            failed += 1
            streak += 1
            if streak >= CONSECUTIVE_FAILURE_LIMIT:
                bailed = True
                logger.warning(
                    "library meta: %d fetches failed in a row; stopping this "
                    "run, the rest is retried next time",
                    streak,
                )
                break
    return FetchRun(
        selected=len(selected), ok=ok, failed=failed, aborted=aborted, bailed_out=bailed
    )


class LibraryMetaRunner:
    """Runs the nightly fill on a daemon thread, one run at a time.

    The scheduler tick only DECIDES (``maybe_start``: switches, window, lock);
    the selection and every request run on the thread, which opens its own
    SQLite connection and stops at the window end or on shutdown.
    """

    #: A pass that found nothing due is not repeated within this many minutes
    #: (the tick runs every minute; the selection walks the known apps).
    IDLE_RECHECK_MINUTES = 60

    def __init__(
        self,
        db_path: str,
        *,
        fetch: oracle.Fetcher | None = None,
        pause_seconds: float | None = None,
    ) -> None:
        self._db_path = db_path
        self._fetch = fetch
        self._pause_seconds = pause_seconds
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._idle_until: datetime | None = None
        self.last_run: FetchRun | None = None

    @property
    def running(self) -> bool:
        return self._lock.locked()

    def maybe_start(
        self,
        settings: Settings,
        window: ScheduleWindow | None,
        now: datetime,
        clock: Callable[[], datetime],
    ) -> str:
        """``started``, or why not: ``disabled``, ``no-window``,
        ``outside-window``, ``idle``, ``stopping`` or ``already-running``."""
        if not enabled(settings):
            return "disabled"
        if window is None:
            return "no-window"
        if not window.contains(now):
            return "outside-window"
        if self._stop.is_set():
            return "stopping"
        if self._idle_until is not None and now < self._idle_until:
            return "idle"
        if not self._lock.acquire(blocking=False):
            return "already-running"
        try:
            thread = threading.Thread(
                target=self._run,
                args=(settings, window, clock),
                name="vault-library-meta",
                daemon=True,
            )
            thread.start()
            self._thread = thread
        except BaseException:
            self._lock.release()
            raise
        return "started"

    def _pause(self) -> None:
        seconds = PAUSE_SECONDS if self._pause_seconds is None else self._pause_seconds
        if seconds > 0:
            self._stop.wait(seconds)

    def _run(
        self, settings: Settings, window: ScheduleWindow, clock: Callable[[], datetime]
    ) -> None:
        try:
            with closing(get_connection(self._db_path)) as conn:
                result = fill(
                    conn,
                    settings,
                    now=clock,
                    fetch=self._fetch,
                    should_continue=lambda: window.contains(clock()),
                    should_abort=self._stop.is_set,
                    pause=self._pause,
                )
            self.last_run = result
            if result.selected == 0:
                self._idle_until = clock() + timedelta(minutes=self.IDLE_RECHECK_MINUTES)
            elif result.bailed_out:
                # A dead or throttling oracle: do not start a new run on the
                # next minute tick; wait out the backoff base before retrying.
                self._idle_until = clock() + timedelta(hours=BACKOFF_BASE_HOURS)
            logger.info(
                "library meta: %d app(s) due, %d ok, %d failed%s%s",
                result.selected, result.ok, result.failed,
                " [ABORTED by shutdown]" if result.aborted else "",
                " [STOPPED after repeated failures]" if result.bailed_out else "",
            )
        except Exception:
            logger.exception("library meta: run failed; retried next tick")
        finally:
            self._lock.release()

    def wait(self, timeout: float | None = None) -> bool:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
            return not thread.is_alive()
        return True

    def stop(self, timeout: float = 5.0) -> None:
        """Ask a running fill to stop between two apps and wait for it."""
        self._stop.set()
        self.wait(timeout)


# --------------------------------------------------------------------------
# Reads: the filter and sort parameters of GET /v1/games (1b)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class MetaFilter:
    categories: tuple[str, ...] = ()
    genres: tuple[int, ...] = ()
    min_rating: int | None = None
    year_from: int | None = None
    year_to: int | None = None
    include_unknown: bool = False

    @property
    def active(self) -> bool:
        return bool(
            self.categories
            or self.genres
            or self.min_rating is not None
            or self.year_from is not None
            or self.year_to is not None
        )


def matching_appids(conn: sqlite3.Connection, flt: MetaFilter) -> set[int]:
    """The tracked apps (``apps``) that satisfy every active clause.

    Clauses combine with AND; categories are AND-ed with each other, genres
    are any-of. With ``include_unknown`` a clause also accepts an app whose
    value for it is unknown (no row, or NULL / no genre rows); a value that is
    known and fails the clause always excludes.
    """
    clauses: list[str] = []
    params: list[object] = []

    def add(column: str, condition: str, *values: object) -> None:
        text = f"{column} {condition}"
        if flt.include_unknown:
            text = f"({text} OR {column} IS NULL)"
        clauses.append(text)
        params.extend(values)

    for category in flt.categories:
        add(f"m.{CATEGORY_COLUMNS[category]}", "= 1")
    if flt.min_rating is not None:
        add("m.review_percent", ">= ?", flt.min_rating)
    if flt.year_from is not None:
        add("m.release_year", ">= ?", flt.year_from)
    if flt.year_to is not None:
        add("m.release_year", "<= ?", flt.year_to)
    if flt.genres:
        marks = ",".join("?" for _ in flt.genres)
        has = (
            "EXISTS (SELECT 1 FROM app_meta_genres g WHERE g.appid = a.appid "
            f"AND g.genre_id IN ({marks}))"
        )
        if flt.include_unknown:
            has = (
                f"({has} OR NOT EXISTS (SELECT 1 FROM app_meta_genres g2 "
                "WHERE g2.appid = a.appid))"
            )
        clauses.append(has)
        params.extend(flt.genres)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        "SELECT a.appid FROM apps a LEFT JOIN app_meta m ON m.appid = a.appid" + where,
        params,
    ).fetchall()
    return {int(row["appid"]) for row in rows}


def meta_by_appid(conn: sqlite3.Connection) -> dict[int, dict]:
    """Stored metadata per app (only apps with a successful fetch), bulk."""
    rows = conn.execute(
        "SELECT appid, flag_single, flag_multi, flag_coop, flag_vr, review_percent, "
        "review_score, release_year, fetched_at FROM app_meta "
        "WHERE fetched_at IS NOT NULL"
    ).fetchall()
    genres: dict[int, list[int]] = {}
    for row in conn.execute(
        "SELECT appid, genre_id FROM app_meta_genres ORDER BY genre_id"
    ):
        genres.setdefault(int(row["appid"]), []).append(int(row["genre_id"]))
    result: dict[int, dict] = {}
    for row in rows:
        appid = int(row["appid"])
        result[appid] = {
            "single_player": None if row["flag_single"] is None else bool(row["flag_single"]),
            "multiplayer": None if row["flag_multi"] is None else bool(row["flag_multi"]),
            "coop": None if row["flag_coop"] is None else bool(row["flag_coop"]),
            "vr": None if row["flag_vr"] is None else bool(row["flag_vr"]),
            "genres": genres.get(appid, []),
            "review_percent": row["review_percent"],
            "review_score": row["review_score"],
            "release_year": row["release_year"],
            "fetched_at": row["fetched_at"],
        }
    return result
