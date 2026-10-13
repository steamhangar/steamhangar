"""Cover images stored by vault-api and served over the LAN (WP D10 COVER-CACHE-1).

## Why this exists

WP API-FIX-5 (``cover_art.py``) resolves each tracked app's real cover URL on
Steam's image CDN, and the clients loaded it from there. Measured on the
operator line 2026-10-13: the CDN answers in 0.2-0.8 s with a ten-year
``Cache-Control`` and an ETag, so a client's own HTTP cache already handles
repeat views. What this module adds is the first load at LAN speed and covers
that still show while the internet is down (user decision 2026-10-13, Weg A).

## What it does

``CoverFileFetcher`` is one daemon thread, started unconditionally by the
lifespan like the cover-art refresher. Every ``TICK_SECONDS`` it:

1. reconciles the directory with ``app_cover_file`` (schema v21): a row whose
   file is gone or has the wrong size is cleared (fetched again), a stored
   file without a row and a stale temp file are removed;
2. downloads up to ``DOWNLOADS_PER_TICK`` covers that are due: the app has a
   resolved ``cover_url`` (``cover_art.valid_cover_url``) and no stored file,
   or the stored file came from a different URL (Valve changed the art).

Never on a request path: ``GET /v1/covers/...`` only reads what is stored.
Fetching lazily on the first request would let any viewer of the library
page start hundreds of outbound downloads at once and make the image request
wait for the CDN, which is the latency this module exists to remove.

## Bounds

- Only apps in ``apps`` with a resolved cover URL ever get a file; the
  file name is ``<appid>.img`` built from a validated integer, never from
  anything the network sent.
- One file is at most ``MAX_FILE_BYTES``; the sum of stored files is at
  most ``MAX_TOTAL_BYTES``. A download that would pass the total cap is not
  stored (logged once per process at WARNING); that app keeps the CDN URL and
  is asked again after ``CAP_RETRY``. Measured covers are 100-400 KB, so the
  cap holds well over a thousand games.
- A body is stored only when its first bytes are a JPEG, PNG or WebP
  signature; the served ``Content-Type`` comes from that check, never from
  the upstream header.
- Files are written to a temp name in the same directory and renamed into
  place (atomic on one filesystem).

## Fetching

``fetch_image`` re-validates the URL with ``cover_art.valid_cover_url``
(https, exactly ``shared.akamai.steamstatic.com`` or
``cdn.akamai.steamstatic.com``), refuses redirects, bounds the body, and
honours ``HTTPS_PROXY`` (the egress lock, ADR-0011): vault-proxy bakes in
exactly these two hosts (user decision 2026-10-13, ADR-0011 addendum
2026-10-13 (D10)). If a download fails anyway (offline, proxy refusal), it
fails soft as described next and clients keep loading covers from the CDN.
A failure backs that app off
(``cover_art.failure_backoff``), ends the tick, and pauses the thread for the
same backoff, so an offline vault sends at most one request per step. The
first failure of a streak logs one WARNING, repeats DEBUG, recovery INFO.

## Serving and its authentication

``GET /v1/covers/{appid}/{signature}`` (``routers/covers.py``). An ``<img>``
element cannot send the ``X-Api-Key`` header, so the URL itself carries the
credential: ``signature`` is an HMAC-SHA256, keyed with ``VAULT_API_KEY``,
over the appid and the stored file's SHA-256 (``cover_signature``). Only the
authenticated ``GET /v1/games[/{appid}]`` hands these URLs out
(``cover_cached_url``), so without the key nobody can build one: there is no
way to enumerate which apps a vault tracks, and the route only ever serves a
file this module stored for a known app (never a proxy for arbitrary URLs).
A new API key or a new cover yields a new URL; the content behind one URL
never changes, hence ``immutable``.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import sqlite3
import stat
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from vault_api import cover_art
from vault_api.db import get_connection
from vault_api.jobs import TIMESTAMP_FORMAT

logger = logging.getLogger(__name__)

#: Directory name next to the database file (like ``manifests``).
CACHE_DIR_NAME = "covers"
FILE_SUFFIX = ".img"
TEMP_SUFFIX = ".tmp"

MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
DOWNLOADS_PER_TICK = 20
TICK_SECONDS = 60.0
#: Later than the cover-art lookup's first tick (30 s): that one resolves the
#: URLs this thread downloads.
INITIAL_DELAY_SECONDS = 45.0
CAP_RETRY = timedelta(days=1)
#: A temp file older than this is a leftover of a crash, never a write in
#: flight (a write takes well under a second).
STALE_TEMP_SECONDS = 3600.0
FETCH_TIMEOUT_SECONDS = 10.0

#: ``Cache-Control`` of a served cover. ``private``: the URL carries a
#: credential, shared caches have no business keeping it.
CACHE_CONTROL = "private, max-age=31536000, immutable"

USER_AGENT = "SteamHangar-vault-api/0.1 (+https://github.com/, cover cache)"

_SIGNATURE_LEN = 32
_SIGNATURE_CONTEXT = b"steamhangar-cover-v1"
_HEX = frozenset("0123456789abcdef")
_MAX_APPID = 2**32 - 1


class CoverFetchError(Exception):
    """A cover could not be downloaded or is not an image we store."""


# --------------------------------------------------------------------------
# Names and signatures
# --------------------------------------------------------------------------


def cache_dir_for(db_path: str) -> str:
    """``covers`` next to the database file (the persistent data volume)."""
    parent = os.path.dirname(os.path.abspath(db_path)) or "."
    return os.path.join(parent, CACHE_DIR_NAME)


def parse_appid(text: object) -> int | None:
    """A positive appid from a path segment, ASCII digits only, else None."""
    if not isinstance(text, str) or not (1 <= len(text) <= 10):
        return None
    if not (text.isascii() and text.isdigit()):
        return None
    value = int(text)
    if value < 1 or value > _MAX_APPID:
        return None
    return value


def file_path(cache_dir: str, appid: int) -> str:
    """The stored file of ``appid``. ``appid`` must be an ``int``: the name is
    built from its decimal form only."""
    if isinstance(appid, bool) or not isinstance(appid, int) or not (1 <= appid <= _MAX_APPID):
        raise ValueError(f"not an appid: {appid!r}")
    return os.path.join(cache_dir, f"{appid}{FILE_SUFFIX}")


def cover_signature(api_key: str, appid: int, sha256: str) -> str:
    """HMAC-SHA256(``api_key``, context | appid | sha256), first 32 hex chars."""
    key = api_key.encode("utf-8", "surrogateescape")
    message = b"\0".join((_SIGNATURE_CONTEXT, str(int(appid)).encode(), sha256.encode("ascii")))
    return hmac.new(key, message, hashlib.sha256).hexdigest()[:_SIGNATURE_LEN]


def valid_signature_text(text: object) -> bool:
    return (
        isinstance(text, str)
        and len(text) == _SIGNATURE_LEN
        and all(ch in _HEX for ch in text)
    )


def cover_path(appid: int, signature: str) -> str:
    """The relative URL clients load (``cover_cached_url``)."""
    return f"/v1/covers/{int(appid)}/{signature}"


def etag_for(sha256: str) -> str:
    return f'"{sha256}"'


def if_none_match_hits(header: str | None, sha256: str) -> bool:
    """RFC 9110 If-None-Match: weak comparison, ``*`` matches anything."""
    if not header:
        return False
    for part in header.split(","):
        token = part.strip()
        if token == "*":
            return True
        if token.startswith("W/"):
            token = token[2:]
        if token == etag_for(sha256):
            return True
    return False


# --------------------------------------------------------------------------
# Image check and download
# --------------------------------------------------------------------------


def sniff_content_type(data: bytes) -> str | None:
    """``image/jpeg``, ``image/png`` or ``image/webp`` from the magic bytes."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


@dataclass(frozen=True)
class FetchedImage:
    data: bytes
    content_type: str


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect would hand the request to a host ``valid_cover_url`` never
    checked; turn it into an error instead."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


_OPENER = urllib.request.build_opener(_RefuseRedirects)


def _host_of(url: str) -> str:
    return url.split("/", 3)[2] if url.count("/") >= 3 else "?"


def fetch_image(url: str, *, timeout: float = FETCH_TIMEOUT_SECONDS) -> FetchedImage:
    """Download one cover. Raises ``CoverFetchError`` (and only that)."""
    checked = cover_art.valid_cover_url(url)
    if checked is None:
        raise CoverFetchError("not an allowed cover URL")
    host = _host_of(checked)
    request = urllib.request.Request(
        checked,
        method="GET",
        headers={"User-Agent": USER_AGENT, "Accept": "image/jpeg,image/png,image/webp"},
    )
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            status = getattr(response, "status", None)
            if status is not None and status != 200:
                raise CoverFetchError(f"{host} answered HTTP {status}")
            data = response.read(MAX_FILE_BYTES + 1)
    except CoverFetchError:
        raise
    except urllib.error.HTTPError as exc:
        raise CoverFetchError(f"{host} answered HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise CoverFetchError(f"{host} is unreachable: {exc.reason}") from exc
    except (OSError, ValueError) as exc:
        raise CoverFetchError(f"{host} could not be fetched: {exc}") from exc
    return check_image(data, host)


def check_image(data: bytes, source: str = "upstream") -> FetchedImage:
    if len(data) > MAX_FILE_BYTES:
        raise CoverFetchError(f"{source} sent more than {MAX_FILE_BYTES} bytes")
    content_type = sniff_content_type(data)
    if content_type is None:
        raise CoverFetchError(f"{source} sent something that is not a JPEG, PNG or WebP image")
    return FetchedImage(data=data, content_type=content_type)


# --------------------------------------------------------------------------
# Storage (table app_cover_file, schema v21)
# --------------------------------------------------------------------------


def _ts(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(TIMESTAMP_FORMAT)


@dataclass(frozen=True)
class StoredCover:
    appid: int
    sha256: str
    content_type: str
    size_bytes: int


def stored_cover(conn: sqlite3.Connection, appid: int) -> StoredCover | None:
    row = conn.execute(
        "SELECT appid, sha256, content_type, size_bytes FROM app_cover_file "
        "WHERE appid = ? AND sha256 IS NOT NULL",
        (appid,),
    ).fetchone()
    return _stored(row)


def _stored(row: sqlite3.Row | None) -> StoredCover | None:
    if row is None:
        return None
    sha = row["sha256"]
    ctype = row["content_type"]
    size = row["size_bytes"]
    if not (isinstance(sha, str) and len(sha) == 64 and all(ch in _HEX for ch in sha)):
        return None
    if ctype not in ("image/jpeg", "image/png", "image/webp"):
        return None
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        return None
    return StoredCover(int(row["appid"]), sha, ctype, size)


def cached_cover_paths(conn: sqlite3.Connection, api_key: str) -> dict[int, str]:
    """``{appid: cover_cached_url}`` for every stored cover."""
    out: dict[int, str] = {}
    for row in conn.execute(
        "SELECT appid, sha256, content_type, size_bytes FROM app_cover_file "
        "WHERE sha256 IS NOT NULL"
    ):
        stored = _stored(row)
        if stored is not None:
            out[stored.appid] = cover_path(
                stored.appid, cover_signature(api_key, stored.appid, stored.sha256)
            )
    return out


def cached_cover_path(conn: sqlite3.Connection, api_key: str, appid: int) -> str | None:
    stored = stored_cover(conn, appid)
    if stored is None:
        return None
    return cover_path(appid, cover_signature(api_key, appid, stored.sha256))


def due_downloads(
    conn: sqlite3.Connection, now: datetime, limit: int = DOWNLOADS_PER_TICK
) -> list[tuple[int, str]]:
    """Up to ``limit`` ``(appid, cover_url)`` pairs to download: tracked apps
    with a resolved URL and no stored file, or a file from another URL;
    apps inside a backoff are skipped."""
    rows = conn.execute(
        """
        SELECT c.appid, c.cover_url
        FROM app_cover_art c
        JOIN apps a ON a.appid = c.appid
        LEFT JOIN app_cover_file f ON f.appid = c.appid
        WHERE c.cover_url IS NOT NULL
          AND (f.appid IS NULL OR f.sha256 IS NULL OR f.source_url IS NOT c.cover_url)
          AND (f.next_try_at IS NULL OR f.next_try_at <= ?)
        ORDER BY (f.appid IS NOT NULL), c.appid
        LIMIT ?
        """,
        (_ts(now), limit),
    ).fetchall()
    out: list[tuple[int, str]] = []
    for row in rows:
        url = cover_art.valid_cover_url(row["cover_url"])
        if url is not None:
            out.append((int(row["appid"]), url))
    return out


def _stored_total(conn: sqlite3.Connection, except_appid: int) -> int:
    (total,) = conn.execute(
        "SELECT COALESCE(SUM(size_bytes), 0) FROM app_cover_file "
        "WHERE sha256 IS NOT NULL AND appid != ?",
        (except_appid,),
    ).fetchone()
    return int(total)


def _write_atomic(cache_dir: str, appid: int, data: bytes) -> None:
    os.makedirs(cache_dir, exist_ok=True)
    final = file_path(cache_dir, appid)
    temp = os.path.join(cache_dir, f".{appid}.{uuid.uuid4().hex}{TEMP_SUFFIX}")
    try:
        with open(temp, "xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, final)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def record_failure(conn: sqlite3.Connection, appid: int, now: datetime) -> int:
    """One more failed download for ``appid``; a stored file is kept. Commits.
    Returns the attempt count."""
    row = conn.execute(
        "SELECT attempts FROM app_cover_file WHERE appid = ?", (appid,)
    ).fetchone()
    attempts = (int(row["attempts"]) if row is not None else 0) + 1
    next_try = _ts(now + cover_art.failure_backoff(attempts))
    conn.execute(
        """
        INSERT INTO app_cover_file (appid, attempts, next_try_at) VALUES (?, ?, ?)
        ON CONFLICT (appid) DO UPDATE SET
            attempts = excluded.attempts, next_try_at = excluded.next_try_at
        """,
        (appid, attempts, next_try),
    )
    conn.commit()
    return attempts


def _record_deferred(conn: sqlite3.Connection, appid: int, until: datetime) -> None:
    conn.execute(
        """
        INSERT INTO app_cover_file (appid, attempts, next_try_at) VALUES (?, 0, ?)
        ON CONFLICT (appid) DO UPDATE SET next_try_at = excluded.next_try_at
        """,
        (appid, _ts(until)),
    )
    conn.commit()


def store(
    conn: sqlite3.Connection,
    cache_dir: str,
    appid: int,
    source_url: str,
    image: FetchedImage,
    now: datetime,
) -> str:
    """Write the file, then its row. Returns ``"stored"`` or ``"cap"``."""
    if _stored_total(conn, appid) + len(image.data) > MAX_TOTAL_BYTES:
        _record_deferred(conn, appid, now + CAP_RETRY)
        return "cap"
    sha = hashlib.sha256(image.data).hexdigest()
    _write_atomic(cache_dir, appid, image.data)
    conn.execute(
        """
        INSERT INTO app_cover_file
            (appid, source_url, sha256, content_type, size_bytes, stored_at, attempts, next_try_at)
        VALUES (?, ?, ?, ?, ?, ?, 0, NULL)
        ON CONFLICT (appid) DO UPDATE SET
            source_url = excluded.source_url, sha256 = excluded.sha256,
            content_type = excluded.content_type, size_bytes = excluded.size_bytes,
            stored_at = excluded.stored_at, attempts = 0, next_try_at = NULL
        """,
        (appid, source_url, sha, image.content_type, len(image.data), _ts(now)),
    )
    conn.commit()
    return "stored"


def forget(conn: sqlite3.Connection, appid: int, sha256: str | None = None) -> None:
    """Clear a stored file's row (it is fetched again). With ``sha256``, only
    if the row still names that file. Commits."""
    if sha256 is None:
        conn.execute(
            "UPDATE app_cover_file SET sha256 = NULL, content_type = NULL, size_bytes = NULL, "
            "source_url = NULL WHERE appid = ?",
            (appid,),
        )
    else:
        conn.execute(
            "UPDATE app_cover_file SET sha256 = NULL, content_type = NULL, size_bytes = NULL, "
            "source_url = NULL WHERE appid = ? AND sha256 = ?",
            (appid, sha256),
        )
    conn.commit()


def read_verified(cache_dir: str, stored: StoredCover) -> bytes | None:
    """The stored file's bytes if it is a regular file whose SHA-256 is the
    recorded one, else None."""
    path = file_path(cache_dir, stored.appid)
    try:
        info = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_size != stored.size_bytes:
        return None
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_FILE_BYTES + 1)
    except OSError:
        return None
    if hashlib.sha256(data).hexdigest() != stored.sha256:
        return None
    return data


def reconcile(conn: sqlite3.Connection, cache_dir: str, now_epoch: float | None = None) -> int:
    """Bring directory and table together (see the module docstring).
    Only names this module creates are ever removed. Returns the number of
    changes made."""
    changes = 0
    rows = {
        stored.appid: stored
        for stored in (
            _stored(r)
            for r in conn.execute(
                "SELECT appid, sha256, content_type, size_bytes FROM app_cover_file "
                "WHERE sha256 IS NOT NULL"
            )
        )
        if stored is not None
    }
    present: dict[int, int] = {}
    now_epoch = time.time() if now_epoch is None else now_epoch
    try:
        entries = list(os.scandir(cache_dir))
    except FileNotFoundError:
        entries = []
    for entry in entries:
        name = entry.name
        if name.startswith(".") and name.endswith(TEMP_SUFFIX):
            try:
                if now_epoch - entry.stat(follow_symlinks=False).st_mtime > STALE_TEMP_SECONDS:
                    os.unlink(entry.path)
                    changes += 1
            except OSError:
                pass
            continue
        if not name.endswith(FILE_SUFFIX):
            continue
        appid = parse_appid(name[: -len(FILE_SUFFIX)])
        if appid is None:
            continue
        try:
            regular = entry.is_file(follow_symlinks=False)
            size = entry.stat(follow_symlinks=False).st_size
        except OSError:
            continue
        if appid not in rows or not regular:
            try:
                os.unlink(entry.path)
                changes += 1
            except OSError:
                pass
            continue
        present[appid] = size
    for appid, stored in rows.items():
        if present.get(appid) != stored.size_bytes:
            forget(conn, appid)
            changes += 1
    return changes


# --------------------------------------------------------------------------
# One step and the thread around it
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DownloadOutcome:
    attempted: int = 0
    stored: int = 0
    capped: int = 0
    error: str | None = None


def download_once(
    conn: sqlite3.Connection,
    cache_dir: str,
    now: datetime,
    fetch: Callable[[str], FetchedImage] | None = None,
    limit: int = DOWNLOADS_PER_TICK,
) -> DownloadOutcome:
    """Download due covers until ``limit`` or the first failure. Never raises
    for an upstream problem."""
    fetcher = fetch if fetch is not None else fetch_image
    attempted = stored = capped = 0
    for appid, url in due_downloads(conn, now, limit):
        attempted += 1
        try:
            image = fetcher(url)
            if not isinstance(image, FetchedImage):
                raise CoverFetchError("fetcher returned no image")
            image = check_image(image.data)
        except CoverFetchError as exc:
            record_failure(conn, appid, now)
            return DownloadOutcome(attempted, stored, capped, f"app {appid}: {exc}")
        result = store(conn, cache_dir, appid, url, image, now)
        if result == "stored":
            stored += 1
        else:
            capped += 1
    return DownloadOutcome(attempted, stored, capped)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CoverFileFetcher:
    """The background thread (see the module docstring)."""

    def __init__(
        self,
        db_path: str,
        *,
        cache_dir: str | None = None,
        fetch: Callable[[str], FetchedImage] | None = None,
        now: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._db_path = db_path
        self.cache_dir = cache_dir if cache_dir is not None else cache_dir_for(db_path)
        self._fetch = fetch
        self._now = now
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._streak = 0
        self._paused_until: datetime | None = None
        self._cap_warned = False

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="cover-file-fetcher", daemon=True)
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
                logger.exception("Cover download tick failed unexpectedly.")
            if self._stop.wait(TICK_SECONDS):
                return

    def tick(self) -> DownloadOutcome | None:
        """Reconcile, then (unless paused after a failure) one batch."""
        now = self._now()
        conn = get_connection(self._db_path)
        try:
            reconcile(conn, self.cache_dir)
            if self._paused_until is not None and now < self._paused_until:
                return None
            outcome = download_once(conn, self.cache_dir, now, self._fetch)
        finally:
            conn.close()
        self._log(outcome, now)
        return outcome

    def _log(self, outcome: DownloadOutcome, now: datetime) -> None:
        if outcome.capped and not self._cap_warned:
            self._cap_warned = True
            logger.warning(
                "Cover cache is full (%d bytes); further covers load from Steam's CDN.",
                MAX_TOTAL_BYTES,
            )
        if outcome.error is not None:
            self._streak += 1
            self._paused_until = now + cover_art.failure_backoff(self._streak)
            log = logger.warning if self._streak == 1 else logger.debug
            log(
                "Cover download failed (%s); clients load that cover from Steam's CDN, "
                "next try after %s.",
                outcome.error,
                _ts(self._paused_until),
            )
            return
        if outcome.attempted and self._streak:
            logger.info("Cover downloads work again after %d failed try(s).", self._streak)
        if outcome.attempted:
            self._streak = 0
            self._paused_until = None
            logger.debug(
                "Cover downloads: %d attempted, %d stored, %d over the cap.",
                outcome.attempted,
                outcome.stored,
                outcome.capped,
            )
