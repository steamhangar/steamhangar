"""Real cover art for every tracked app (WP API-FIX-5, ADR-0016 addendum).

## Why this exists

Both frontends used to build the portrait cover URL on their own as
``https://cdn.akamai.steamstatic.com/steam/apps/<appid>/library_600x900.jpg``.
Valve no longer serves that legacy path for newer games (measured
2026-10-09: app 3527290 answers 404 there); their assets live under a hashed
path on a second host, e.g.
``https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/3527290/
480bd879.../library_600x900.jpg?t=1790591892``. Only Steam's store item lookup
knows that path.

## The one request

``GET https://api.steampowered.com/IStoreBrowseService/GetItems/v1/`` with a
single query parameter ``input_json``::

    {"ids": [{"appid": N}, ...],
     "context": {"language": "english", "country_code": "US"},
     "data_request": {"include_assets": true}}

No Web API key, no SteamID, nothing else. Up to ``BATCH_SIZE`` apps per
call. Each answered item carries ``assets.asset_url_format`` (e.g.
``steam/apps/730/${FILENAME}?t=1789251637``) and ``assets.library_capsule``
(e.g. ``<hash>/library_600x900.jpg``; the filename itself varies, some games
use ``library_capsule.jpg``). The cover URL is ``ASSET_BASE`` + the format
with ``${FILENAME}`` replaced by the capsule name. An item with
``success != 1`` (an unknown app, a tool package such as 228980: 15) or
without assets has no cover; the frontends then keep the legacy URL.

It goes through ``steam_relay.http_fetch``: host pinned to
``api.steampowered.com`` (the one host vault-proxy always allows, ADR-0011
addendum), HTTPS only, no redirects, bounded body, ``HTTPS_PROXY`` honoured.

## Privacy (user decision 2026-10-10, "Weg B")

On by default and not switchable: this sends the list of app ids this vault
tracks, from the server's address, to Valve. No key, no SteamID, no client
id. Recorded in docs/security/threat-model.md (outbound flows) and the
ADR-0011/ADR-0016 addenda.

## Only two hosts are ever stored or served

``valid_cover_url`` accepts ``https`` URLs on exactly
``shared.akamai.steamstatic.com`` or ``cdn.akamai.steamstatic.com`` with no
userinfo, no port and no fragment. It runs before a URL is written AND again
when ``GET /v1/games`` reads it back (the database is a file an operator can
edit). Anything else becomes ``None``. The web CSP's ``img-src`` allows the
same two hosts.

## Refresh rules (never on the request path)

``CoverArtRefresher`` is one daemon thread, started unconditionally by the
lifespan (LEARNINGS: no boot-snapshot gating). Every ``TICK_SECONDS`` it
picks up to ``BATCH_SIZE`` apps that have no row in ``app_cover_art`` yet or
whose ``next_check_at`` has passed, and asks once:

- found: stored, checked again after ``REFRESH_FOUND_DAYS`` (Valve changes
  the hash when a game updates its art);
- no cover (``success != 1``, no assets, or an answer that fails
  validation): on a first lookup ``cover_url`` NULL (``outcome='none'``);
  on a re-check of an app that already has a URL, that URL is kept
  (``outcome`` stays ``'found'``; a dead URL is covered by the frontends'
  image-error fallback). Either way asked again after ``RETRY_NONE_DAYS``;
- the call failed (offline, filtered by the proxy, HTTP error, garbage):
  each app's ``attempts`` grows and its next try backs off from
  ``FAIL_BACKOFF_BASE`` doubling up to ``FAIL_BACKOFF_MAX``; a stored URL
  is kept. The whole thread also pauses for the same backoff, so an
  offline vault sends at most one request per backoff step. The first
  failure of a streak is logged once at WARNING, repeats at DEBUG, and the
  recovery once at INFO -- an offline test instance stays quiet.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Sequence
from urllib.parse import urlsplit

from vault_api import deletion, steam_relay, tool_apps
from vault_api.db import get_connection
from vault_api.jobs import TIMESTAMP_FORMAT

logger = logging.getLogger(__name__)

GET_ITEMS_PATH = "/IStoreBrowseService/GetItems/v1/"

#: Where ``asset_url_format`` is rooted (measured 2026-10-09).
ASSET_BASE = "https://shared.akamai.steamstatic.com/store_item_assets/"

#: The only hosts a stored or served cover URL may name. The legacy CDN host
#: stays allowed so a future answer pointing there is not thrown away.
ALLOWED_ASSET_HOSTS = frozenset(
    {"shared.akamai.steamstatic.com", "cdn.akamai.steamstatic.com"}
)

#: Store context of the lookup. A region-locked game may not be visible in
#: this region and then simply keeps the legacy URL.
LOOKUP_CONTEXT = {"language": "english", "country_code": "US"}

BATCH_SIZE = 50
MAX_COVER_URL_LEN = 512
MAX_STORE_ITEMS = 2 * BATCH_SIZE

TICK_SECONDS = 60.0
#: First tick after start: lets the stack (vault-proxy) come up first, and
#: keeps short-lived processes (tests) from ever making the call.
INITIAL_DELAY_SECONDS = 30.0

REFRESH_FOUND_DAYS = 30
RETRY_NONE_DAYS = 7
FAIL_BACKOFF_BASE = timedelta(minutes=15)
FAIL_BACKOFF_MAX = timedelta(hours=24)

OUTCOME_FOUND = "found"
OUTCOME_NONE = "none"
OUTCOME_FAILED = "failed"

# ``steam/apps/<appid>/${FILENAME}`` with an optional ``?t=<digits>`` cache
# buster -- the only shape Valve was seen to return for apps. The appid in it
# must be the one asked about (checked in build_cover_url).
_FORMAT_RE = re.compile(r"steam/apps/([0-9]{1,10})/\$\{FILENAME\}(\?t=[0-9]{1,12})?", re.ASCII)
# ``[<hex hash>/]<name>.<ext>`` -- no other separators, no ``..``.
_CAPSULE_RE = re.compile(
    r"(?:[0-9a-f]{8,64}/)?[A-Za-z0-9_-][A-Za-z0-9_.-]{0,95}\.(?:jpg|jpeg|png|webp)",
    re.ASCII,
)


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def valid_cover_url(value: object) -> str | None:
    """``value`` if it is an https URL on one of ``ALLOWED_ASSET_HOSTS``,
    else ``None``. No userinfo, no port, no fragment, no whitespace or
    backslash, bounded length; the netloc must equal the lowercase host
    exactly."""
    if not isinstance(value, str):
        return None
    if not value.isascii() or len(value) > MAX_COVER_URL_LEN:
        return None
    if any(ch.isspace() or ch == "\\" or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        return None
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme != "https":
        return None
    if parts.netloc not in ALLOWED_ASSET_HOSTS:
        return None
    if port is not None or parts.username is not None or parts.password is not None:
        return None
    if parts.fragment or "#" in value:
        return None
    if not parts.path.startswith("/"):
        return None
    return value


def build_cover_url(appid: int, asset_url_format: object, library_capsule: object) -> str | None:
    """The cover URL for ``appid`` from one GetItems ``assets`` object, or
    ``None`` when either part is missing or not of the known shape."""
    if not isinstance(asset_url_format, str) or not isinstance(library_capsule, str):
        return None
    match = _FORMAT_RE.fullmatch(asset_url_format)
    if match is None or match.group(1) != str(appid):
        return None
    if _CAPSULE_RE.fullmatch(library_capsule) is None or ".." in library_capsule:
        return None
    url = ASSET_BASE + asset_url_format.replace("${FILENAME}", library_capsule)
    return valid_cover_url(url)


# --------------------------------------------------------------------------
# The request and its answer
# --------------------------------------------------------------------------


def request_params(appids: Sequence[int]) -> dict[str, str]:
    """The one query parameter GetItems needs (see module docstring)."""
    payload = {
        "ids": [{"appid": int(appid)} for appid in appids],
        "context": LOOKUP_CONTEXT,
        "data_request": {"include_assets": True},
    }
    return {"input_json": json.dumps(payload, separators=(",", ":"))}


def fetch_items(appids: Sequence[int]) -> bytes:
    """``GET`` the store items for ``appids`` (raises ``SteamRelayError``)."""
    return steam_relay.http_fetch(GET_ITEMS_PATH, request_params(appids))


def parse_items(payload: bytes, requested: Iterable[int]) -> dict[int, str | None]:
    """``{appid: cover_url or None}`` for every REQUESTED app the answer
    talks about. An app missing from the answer is missing from the result
    (the caller treats it as a failed attempt). Raises ``SteamRelayError``
    when the document is unusable as a whole."""
    wanted = set(requested)
    document = steam_relay._decode_json(payload, GET_ITEMS_PATH)
    if not isinstance(document, dict) or not isinstance(document.get("response"), dict):
        raise steam_relay.SteamRelayError(
            f"{steam_relay._redacted_url(GET_ITEMS_PATH)} has no usable 'response' object"
        )
    items = document["response"].get("store_items")
    if items is None:
        return {}
    if not isinstance(items, list):
        raise steam_relay.SteamRelayError(
            f"{steam_relay._redacted_url(GET_ITEMS_PATH)} 'store_items' is not a list"
        )

    result: dict[int, str | None] = {}
    for item in items[:MAX_STORE_ITEMS]:
        if not isinstance(item, dict):
            continue
        appid = deletion.coerce_positive_id(item.get("id"))
        if appid is None or appid not in wanted:
            continue
        success = item.get("success")
        if isinstance(success, bool) or success != 1:
            # Unknown/hidden app: Steam answers e.g. {"id": 228980,
            # "success": 15, "appid": 0} (measured 2026-10-10), so the
            # appid cross-check below applies to answered items only.
            result[appid] = None
            continue
        other = item.get("appid")
        if other is not None and deletion.coerce_positive_id(other) != appid:
            continue
        assets = item.get("assets")
        if not isinstance(assets, dict):
            result[appid] = None
            continue
        result[appid] = build_cover_url(
            appid, assets.get("asset_url_format"), assets.get("library_capsule")
        )
    return result


# --------------------------------------------------------------------------
# Storage (table app_cover_art, schema v18)
# --------------------------------------------------------------------------


def _ts(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(TIMESTAMP_FORMAT)


def failure_backoff(attempts: int) -> timedelta:
    """Backoff after ``attempts`` consecutive failures (``attempts >= 1``)."""
    step = FAIL_BACKOFF_BASE * (2 ** max(0, min(attempts, 20) - 1))
    return min(step, FAIL_BACKOFF_MAX)


def due_appids(conn: sqlite3.Connection, now: datetime, limit: int = BATCH_SIZE) -> list[int]:
    """Up to ``limit`` tracked apps that need a lookup: never looked up
    first, then the ones whose ``next_check_at`` has passed. Tool apps
    (``tool_apps.py``) are skipped; Steam has no store item for them."""
    skip = sorted(tool_apps.TOOL_APPS)
    placeholders = ",".join("?" for _ in skip) or "NULL"
    rows = conn.execute(
        f"""
        SELECT a.appid
        FROM apps a
        LEFT JOIN app_cover_art c ON c.appid = a.appid
        WHERE (c.appid IS NULL OR c.next_check_at <= ?)
          AND a.appid NOT IN ({placeholders})
        ORDER BY (c.appid IS NOT NULL), c.next_check_at, a.appid
        LIMIT ?
        """,
        (_ts(now), *skip, limit),
    ).fetchall()
    return [int(row["appid"]) for row in rows]


def record_results(
    conn: sqlite3.Connection, results: dict[int, str | None], now: datetime
) -> None:
    """Store found/none outcomes. Commits.

    A "none" answer never overwrites a URL found earlier: a re-check that
    Steam answers with ``success != 1`` (or an unusable asset) keeps the old
    URL and ``outcome='found'``, and only moves ``next_check_at``.
    """
    checked = _ts(now)
    for appid, url in results.items():
        url = valid_cover_url(url)
        if url is not None:
            outcome, next_check = OUTCOME_FOUND, now + timedelta(days=REFRESH_FOUND_DAYS)
        else:
            outcome, next_check = OUTCOME_NONE, now + timedelta(days=RETRY_NONE_DAYS)
        conn.execute(
            """
            INSERT INTO app_cover_art (appid, cover_url, outcome, attempts, checked_at, next_check_at)
            VALUES (?, ?, ?, 0, ?, ?)
            ON CONFLICT (appid) DO UPDATE SET
                cover_url     = COALESCE(excluded.cover_url, app_cover_art.cover_url),
                outcome       = CASE
                    WHEN excluded.cover_url IS NULL AND app_cover_art.cover_url IS NOT NULL
                    THEN 'found' ELSE excluded.outcome END,
                attempts      = 0,
                checked_at    = excluded.checked_at,
                next_check_at = excluded.next_check_at
            """,
            (appid, url, outcome, checked, _ts(next_check)),
        )
    conn.commit()


def record_failures(conn: sqlite3.Connection, appids: Iterable[int], now: datetime) -> None:
    """One more failed attempt for each app; a stored URL is kept. Commits."""
    checked = _ts(now)
    for appid in appids:
        row = conn.execute(
            "SELECT attempts FROM app_cover_art WHERE appid = ?", (appid,)
        ).fetchone()
        attempts = (int(row["attempts"]) if row is not None else 0) + 1
        next_check = _ts(now + failure_backoff(attempts))
        conn.execute(
            """
            INSERT INTO app_cover_art (appid, cover_url, outcome, attempts, checked_at, next_check_at)
            VALUES (?, NULL, ?, ?, ?, ?)
            ON CONFLICT (appid) DO UPDATE SET
                outcome       = excluded.outcome,
                attempts      = excluded.attempts,
                checked_at    = excluded.checked_at,
                next_check_at = excluded.next_check_at
            """,
            (appid, OUTCOME_FAILED, attempts, checked, next_check),
        )
    conn.commit()


def cover_urls_by_appid(conn: sqlite3.Connection) -> dict[int, str]:
    """Every stored, still-valid cover URL (re-validated on read)."""
    out: dict[int, str] = {}
    for row in conn.execute(
        "SELECT appid, cover_url FROM app_cover_art WHERE cover_url IS NOT NULL"
    ):
        url = valid_cover_url(row["cover_url"])
        if url is not None:
            out[int(row["appid"])] = url
    return out


def cover_url_for_appid(conn: sqlite3.Connection, appid: int) -> str | None:
    row = conn.execute(
        "SELECT cover_url FROM app_cover_art WHERE appid = ?", (appid,)
    ).fetchone()
    return None if row is None else valid_cover_url(row["cover_url"])


# --------------------------------------------------------------------------
# One refresh step and the thread around it
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RefreshOutcome:
    requested: tuple[int, ...] = ()
    found: int = 0
    none: int = 0
    failed: int = 0
    error: str | None = None


def refresh_once(
    conn: sqlite3.Connection,
    now: datetime,
    fetch: Callable[[Sequence[int]], bytes] | None = None,
) -> RefreshOutcome:
    """Look up one batch of due apps and store the outcome. Never raises
    for an upstream problem (that becomes ``error`` plus failed attempts)."""
    appids = due_appids(conn, now)
    if not appids:
        return RefreshOutcome()
    fetcher = fetch if fetch is not None else fetch_items
    try:
        results = parse_items(fetcher(appids), appids)
    except steam_relay.SteamRelayError as exc:
        record_failures(conn, appids, now)
        return RefreshOutcome(requested=tuple(appids), failed=len(appids), error=str(exc))
    record_results(conn, results, now)
    missing = [appid for appid in appids if appid not in results]
    if missing:
        record_failures(conn, missing, now)
    found = sum(1 for url in results.values() if valid_cover_url(url) is not None)
    return RefreshOutcome(
        requested=tuple(appids),
        found=found,
        none=len(results) - found,
        failed=len(missing),
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CoverArtRefresher:
    """The background thread (see the module docstring's refresh rules)."""

    def __init__(
        self,
        db_path: str,
        *,
        fetch: Callable[[Sequence[int]], bytes] | None = None,
        now: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._db_path = db_path
        self._fetch = fetch
        self._now = now
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._streak = 0
        self._paused_until: datetime | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="cover-art-refresher", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def _run(self) -> None:
        if self._stop.wait(INITIAL_DELAY_SECONDS):
            return
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # pragma: no cover - defensive, keeps the thread alive
                logger.exception("Cover-art refresh tick failed unexpectedly.")
            if self._stop.wait(TICK_SECONDS):
                return

    def tick(self) -> RefreshOutcome | None:
        """One step: skipped while paused after a failure, else one batch."""
        now = self._now()
        if self._paused_until is not None and now < self._paused_until:
            return None
        conn = get_connection(self._db_path)
        try:
            outcome = refresh_once(conn, now, self._fetch)
        finally:
            conn.close()
        self._log(outcome, now)
        return outcome

    def _log(self, outcome: RefreshOutcome, now: datetime) -> None:
        if outcome.error is not None:
            self._streak += 1
            self._paused_until = now + failure_backoff(self._streak)
            log = logger.warning if self._streak == 1 else logger.debug
            log(
                "Cover-art lookup failed (%s); covers keep the legacy CDN path "
                "for now, next try after %s.",
                outcome.error,
                _ts(self._paused_until),
            )
            return
        if outcome.requested and self._streak:
            logger.info("Cover-art lookup works again after %d failed try(s).", self._streak)
        if outcome.requested:
            self._streak = 0
            self._paused_until = None
            logger.debug(
                "Cover-art lookup: %d app(s) asked, %d found, %d without cover, %d unanswered.",
                len(outcome.requested),
                outcome.found,
                outcome.none,
                outcome.failed,
            )
