"""WP D19-1c STORE-DETAILS: short description, genres, release date, modes and
the overall review rating on the game card (schema v33).

Opening a card asks Valve's store host (``store.steampowered.com``) two
questions about ONE app id, in the background; the card fills in when the
answer is stored. Guarantees:

1. **Own switch, off by default.** Nothing leaves the LAN unless
   ``VAULT_STORE_DETAILS`` (env-only) is on. It does NOT ride on the oracle
   switch: a different host and party, and it shows what a household member
   browses (``docs/security/privacy-review.md`` O9 / F1). Every path here
   consults ``enabled`` itself; an off switch means "off", never a query.
2. **Nothing but the app id leaves.** Anonymous GETs: no key, no SteamID, no
   cookie, no client id. ``l=english`` is fixed (no user text goes out).
3. **Asked at most once per TTL and per app.** A stored answer (24 h) or
   stored failure (30 min back-off) is served without a query; concurrent
   opens share one in-flight query; a global rate limit bounds a burst.
4. **Third-party text is data, never markup.** Every string is HTML-stripped,
   unescaped, whitespace-collapsed and length-capped BEFORE it is stored; the
   web renders it with ``textContent``.
5. **Overall rating only** (option A, user decision 2026-10-15): Steam's
   summary exposes no "recent" figure, so none is shown or sampled.
6. **Nobody is recorded** and failures are fail-soft: they end as a stored
   ``failed`` outcome, never as an exception to the request.
"""

from __future__ import annotations

import html
import json
import logging
import re
import sqlite3
import threading
import time
from collections import deque
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from vault_api import jobs, oracle
from vault_api.config import Settings
from vault_api.db import get_connection

logger = logging.getLogger(__name__)

HOST = "store.steampowered.com"
DETAILS_URL = "https://" + HOST + "/api/appdetails?appids={appid}&l=english"
REVIEWS_URL = (
    "https://" + HOST + "/appreviews/{appid}"
    "?json=1&num_per_page=0&language=all&purchase_type=all"
)

#: A stored answer is served this long without asking again.
TTL = timedelta(hours=24)
#: A stored failure is served this long before the next attempt (back-off).
FAILURE_TTL = timedelta(minutes=30)
#: At most this many lookups START per ``RATE_WINDOW_SECONDS``, for all apps
#: together (one lookup = up to two requests). appdetails is limited to
#: roughly 200 requests per 5 minutes per IP; cards are opened at human pace.
RATE_LIMIT = 10
RATE_WINDOW_SECONDS = 60.0

#: Bounds on stored third-party text.
MAX_DESCRIPTION = 600
MAX_ITEM = 60
MAX_GENRES = 8
MAX_DEVELOPERS = 3
MAX_DATE = 40
MAX_COUNT = 2**40

#: ``state`` values of the endpoint.
STATE_OFF = "off"  # switch off: nothing was asked
STATE_PENDING = "pending"  # a query is running; ask again in a moment
STATE_READY = "ready"
STATE_UNAVAILABLE = "unavailable"  # failed, rate limited, or the store knows nothing

OUTCOME_OK = "ok"
OUTCOME_NONE = "none"  # success:false (removed, region-locked, age-gated)
OUTCOME_FAILED = "failed"

#: Store category ids -> the mode flags the card shows. Ids, never the
#: localized text.
_MODE_CATEGORIES: dict[str, frozenset[int]] = {
    "single_player": frozenset({2}),
    "multiplayer": frozenset({1, 20, 27, 36, 49}),
    "co_op": frozenset({9, 38, 39}),
    "vr": frozenset({31, 53, 54}),
}
_MODE_ORDER = ("single_player", "multiplayer", "co_op", "vr")


def enabled(settings: Settings) -> bool:
    """The own switch, nothing else. Every path here consults this itself."""
    return bool(settings.store_details_enabled)


class StoreError(Exception):
    """The store answer is unusable as a whole."""


# --------------------------------------------------------------------------
# Parsing and sanitising
# --------------------------------------------------------------------------

_TAG = re.compile(r"<[^>]*>")
_SPACE = re.compile(r"\s+")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def clean_text(value: object, limit: int) -> str | None:
    """Third-party text to plain text: tags removed, entities decoded, control
    characters and runs of whitespace collapsed, length capped. ``None`` when
    nothing is left. Decoding comes after stripping on purpose, and the result
    is never treated as markup (the web uses ``textContent``)."""
    if not isinstance(value, str):
        return None
    text = html.unescape(_TAG.sub(" ", value))
    # A second pass: "&lt;b&gt;" decodes to "<b>", which is still just text,
    # but dropping it keeps stored values free of anything tag-shaped.
    text = _TAG.sub(" ", text)
    text = _SPACE.sub(" ", _CONTROL.sub(" ", text)).strip()
    if not text:
        return None
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= MAX_COUNT else None


def _ids(raw: object) -> set[int]:
    found: set[int] = set()
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                number = item.get("id")
                if isinstance(number, str) and number.isdigit():
                    number = int(number)
                if isinstance(number, int) and not isinstance(number, bool):
                    found.add(number)
    return found


@dataclass(frozen=True)
class StoreInfo:
    description: str | None = None
    genres: tuple[str, ...] = ()
    developers: tuple[str, ...] = ()
    release_date: str | None = None
    modes: tuple[str, ...] = ()
    review_summary: str | None = None
    review_positive: int | None = None
    review_negative: int | None = None

    def has_content(self) -> bool:
        return bool(
            self.description
            or self.genres
            or self.developers
            or self.release_date
            or self.modes
            or self.review_summary
        )


def parse_details(appid: int, payload: bytes) -> StoreInfo | None:
    """One appdetails answer. ``None`` for ``success: false`` (the store knows
    nothing usable for this app); raises ``StoreError`` for an unusable body."""
    document = oracle._decode_json(payload)
    if not isinstance(document, dict):
        raise StoreError("appdetails answer is not an object")
    entry = document.get(str(appid))
    if not isinstance(entry, dict):
        raise StoreError("appdetails answer has no entry for the app")
    if entry.get("success") is not True:
        return None
    data = entry.get("data")
    if not isinstance(data, dict):
        return None

    genres: list[str] = []
    raw_genres = data.get("genres")
    if isinstance(raw_genres, list):
        for item in raw_genres:
            name = clean_text(item.get("description"), MAX_ITEM) if isinstance(item, dict) else None
            if name and name not in genres:
                genres.append(name)
            if len(genres) >= MAX_GENRES:
                break

    developers: list[str] = []
    raw_devs = data.get("developers")
    if isinstance(raw_devs, list):
        for item in raw_devs:
            name = clean_text(item, MAX_ITEM)
            if name and name not in developers:
                developers.append(name)
            if len(developers) >= MAX_DEVELOPERS:
                break

    release = data.get("release_date")
    release_date = clean_text(release.get("date"), MAX_DATE) if isinstance(release, dict) else None

    category_ids = _ids(data.get("categories"))
    modes = tuple(mode for mode in _MODE_ORDER if category_ids & _MODE_CATEGORIES[mode])

    return StoreInfo(
        description=clean_text(data.get("short_description"), MAX_DESCRIPTION),
        genres=tuple(genres),
        developers=tuple(developers),
        release_date=release_date,
        modes=modes,
    )


def parse_reviews(payload: bytes) -> tuple[str | None, int | None, int | None]:
    """``(summary, positive, negative)`` from an appreviews answer, the
    overall figures only. Anything off yields ``(None, None, None)``."""
    document = oracle._decode_json(payload)
    summary = document.get("query_summary") if isinstance(document, dict) else None
    if not isinstance(summary, dict):
        return None, None, None
    positive = _count(summary.get("total_positive"))
    negative = _count(summary.get("total_negative"))
    description = clean_text(summary.get("review_score_desc"), MAX_ITEM)
    if positive is None or negative is None or positive + negative == 0:
        # "No user reviews" has no rating worth a line on the card.
        return None, None, None
    return description, positive, negative


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class StoredDetails:
    appid: int
    outcome: str
    fetched_at: str
    info: StoreInfo


def store_outcome(
    conn: sqlite3.Connection,
    appid: int,
    outcome: str,
    *,
    info: StoreInfo | None = None,
    fetched_at: str | None = None,
) -> None:
    """Replace the app's row in one transaction. A failure keeps no fields."""
    stamp = fetched_at or jobs.utcnow_iso()
    data = info if (info is not None and outcome == OUTCOME_OK) else StoreInfo()
    with jobs.immediate_transaction(conn):
        conn.execute(
            """
            INSERT INTO store_details
                (appid, outcome, fetched_at, description, genres, developers,
                 release_date, modes, review_summary, review_positive, review_negative)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (appid) DO UPDATE SET
                outcome = excluded.outcome,
                fetched_at = excluded.fetched_at,
                description = excluded.description,
                genres = excluded.genres,
                developers = excluded.developers,
                release_date = excluded.release_date,
                modes = excluded.modes,
                review_summary = excluded.review_summary,
                review_positive = excluded.review_positive,
                review_negative = excluded.review_negative
            """,
            (
                appid,
                outcome,
                stamp,
                data.description,
                json.dumps(list(data.genres)),
                json.dumps(list(data.developers)),
                data.release_date,
                json.dumps(list(data.modes)),
                data.review_summary,
                data.review_positive,
                data.review_negative,
            ),
        )


def _json_list(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, str):
        return ()
    try:
        value = json.loads(raw)
    except ValueError:
        return ()
    return tuple(v for v in value if isinstance(v, str)) if isinstance(value, list) else ()


def load_details(conn: sqlite3.Connection, appid: int) -> StoredDetails | None:
    row = conn.execute("SELECT * FROM store_details WHERE appid = ?", (appid,)).fetchone()
    if row is None:
        return None
    info = StoreInfo(
        description=row["description"],
        genres=_json_list(row["genres"]),
        developers=_json_list(row["developers"]),
        release_date=row["release_date"],
        modes=_json_list(row["modes"]),
        review_summary=row["review_summary"],
        review_positive=row["review_positive"],
        review_negative=row["review_negative"],
    )
    return StoredDetails(appid, row["outcome"], row["fetched_at"], info)


def _is_fresh(stored: StoredDetails, now: datetime) -> bool:
    try:
        fetched = datetime.strptime(stored.fetched_at, jobs.TIMESTAMP_FORMAT).replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return False
    ttl = FAILURE_TTL if stored.outcome == OUTCOME_FAILED else TTL
    return now - fetched < ttl


# --------------------------------------------------------------------------
# The on-open service
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DetailsResult:
    state: str
    stored: StoredDetails | None = None


def _run_in_thread(job: Callable[[], None]) -> None:
    threading.Thread(target=job, name="vault-store-details", daemon=True).start()


class StoreDetailsService:
    """Decides whether to ask the store, and does so off the request thread.

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

    def lookup(self, conn: sqlite3.Connection, appid: int, settings: Settings) -> DetailsResult:
        """Answer one card open. The only place a query can start."""
        if not enabled(settings):
            return DetailsResult(STATE_OFF)
        try:
            stored = load_details(conn, appid)
        except sqlite3.Error as exc:
            logger.warning("store details: could not read app %s: %s", appid, exc)
            return DetailsResult(STATE_UNAVAILABLE)
        if stored is not None and _is_fresh(stored, self._clock()):
            return DetailsResult(self._state_of(stored), stored)

        with self._lock:
            if appid in self._in_flight:
                return DetailsResult(STATE_PENDING)
            now = self._monotonic()
            while self._starts and now - self._starts[0] >= RATE_WINDOW_SECONDS:
                self._starts.popleft()
            if len(self._starts) >= RATE_LIMIT:
                return DetailsResult(STATE_UNAVAILABLE)
            self._starts.append(now)
            self._in_flight.add(appid)

        db_path = settings.db_path
        try:
            self._run(lambda: self._fetch_and_store(db_path, appid, settings))
        except Exception:  # starting the thread failed: release the slot
            logger.exception("store details: could not start the background query")
            with self._lock:
                self._in_flight.discard(appid)
            return DetailsResult(STATE_UNAVAILABLE)
        with self._lock:
            still_running = appid in self._in_flight
        if still_running:
            return DetailsResult(STATE_PENDING)
        try:
            fresh = load_details(conn, appid)
        except sqlite3.Error as exc:
            logger.warning("store details: could not re-read app %s: %s", appid, exc)
            return DetailsResult(STATE_UNAVAILABLE)
        return DetailsResult(self._state_of(fresh) if fresh else STATE_UNAVAILABLE, fresh)

    @staticmethod
    def _state_of(stored: StoredDetails) -> str:
        if stored.outcome == OUTCOME_OK and stored.info.has_content():
            return STATE_READY
        return STATE_UNAVAILABLE

    def _default_fetch(self, settings: Settings) -> oracle.Fetcher:
        timeout = settings.manifest_oracle_timeout

        def fetch(url: str) -> bytes:
            logger.info(
                "store details: querying %s (this request LEAVES the LAN)", HOST
            )
            return oracle.http_fetch(url, timeout=timeout)

        return fetch

    def _fetch_and_store(self, db_path: str, appid: int, settings: Settings) -> None:
        try:
            outcome, info = OUTCOME_FAILED, None
            try:
                fetch = self._fetch or self._default_fetch(settings)
                details = parse_details(appid, fetch(DETAILS_URL.format(appid=appid)))
                if details is None:
                    outcome = OUTCOME_NONE
                else:
                    info = details
                    outcome = OUTCOME_OK
                    try:  # the rating is optional: its failure keeps the rest
                        summary, positive, negative = parse_reviews(
                            fetch(REVIEWS_URL.format(appid=appid))
                        )
                        info = StoreInfo(
                            **{
                                **details.__dict__,
                                "review_summary": summary,
                                "review_positive": positive,
                                "review_negative": negative,
                            }
                        )
                    except (oracle.OracleError, StoreError) as exc:
                        logger.warning("store details: reviews for app %s failed: %s", appid, exc)
            except (oracle.OracleError, StoreError) as exc:
                logger.warning("store details: app %s failed: %s", appid, exc)
            except Exception:
                logger.exception("store details: app %s crashed", appid)
            try:
                with closing(get_connection(db_path)) as conn:
                    store_outcome(conn, appid, outcome, info=info)
            except sqlite3.Error as exc:
                logger.warning("store details: could not store app %s: %s", appid, exc)
        finally:
            with self._lock:
                self._in_flight.discard(appid)
