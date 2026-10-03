"""``GET /v1/about`` (WP VER-2): version and status of every component.

One list, fixed order, one entry per component:

=================  ===========================================================
``vault-api``      This process (it also serves the web UI, so the web shares
                   this version). ``vault_api.reported_identity``.
``vault-core``     The version file vault-core's start hook
                   (``core/docker/29-vault-build-version.sh``) writes into the
                   shared cache volume, ``<cache volume>/logs/
                   vault-core-version.json``. Status is always ``unknown``:
                   vault-api has no network path to vault-core (ADR-0011
                   egress lock, user decision "Weg A" 2026-10-03), so the file
                   says which version STARTED last, not whether it runs now.
``vault-runner``   The freshest ``runner_presence`` row (schema v16,
                   ``runner_presence.py``). ``not_in_use`` in
                   ``VAULT_PREFILL_MODE=subprocess``.
``steamprefill``   Queue mode: the version the runner reported. Subprocess
                   mode: this image's own ``STEAMPREFILL_VERSION``.
``vault-proxy``    Reachability only (see :func:`probe_proxy`), ``version``
                   always ``null``.
``vault-dns``      Never probed; ``unknown``.
=================  ===========================================================

**Never slow, never failing.** The three lookups that touch something outside
this process (the version file, the database, the proxy socket) run in
parallel on worker threads with :data:`PROBE_TIMEOUT_SECONDS` each and an
overall :data:`PROBE_DEADLINE_SECONDS`; a lookup that is late or raises
degrades its own entry only. The whole answer is cached for
:data:`CACHE_TTL_SECONDS`, so polling the route costs at most one round of
probes per minute.

**What it does not reveal.** No paths, host names, addresses, runner ids or
counters: only versions, commit ids, a status word, timestamps and a fixed
explanatory sentence. The route sits behind the API key like
``/v1/settings``; ``/v1/health`` stays version-free (WP VER-1).
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import sqlite3
import stat
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlsplit

from vault_api import (
    BASE_VERSION,
    BUILD_VERSION_ENV,
    INVALID_VALUE,
    is_valid_commit,
    is_valid_version,
    reported_identity,
    runner_presence,
    steamprefill_version,
)
from vault_api.config import Settings
from vault_api.db import get_connection
from vault_api.jobs import parse_utc_iso, to_utc_iso

logger = logging.getLogger(__name__)

Status = Literal["ok", "unreachable", "not_in_use", "unknown"]
Name = Literal[
    "vault-api", "vault-core", "vault-runner", "steamprefill", "vault-proxy", "vault-dns"
]

#: How long one ``GET /v1/about`` answer is reused.
CACHE_TTL_SECONDS = 60.0
#: Socket timeout for the proxy probe.
PROBE_TIMEOUT_SECONDS = 2.0
#: How long the route waits for all lookups together before it degrades the
#: late ones to ``unknown``/``unreachable``. A database read can wait up to the
#: 5 s ``busy_timeout``; the route does not.
PROBE_DEADLINE_SECONDS = 3.0

#: The file vault-core's start hook writes; relative to the cache volume root
#: (the parent of ``VAULT_CACHE_ROOT``, i.e. ``/vault`` in the images).
CORE_VERSION_FILE = os.path.join("logs", "vault-core-version.json")
#: Upper bound on what is read from that file. The hook writes ~150 bytes.
CORE_VERSION_FILE_MAX_BYTES = 4096
_CORE_FILE_KEYS = frozenset({"component", "version", "commit", "recorded_at"})
_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", re.ASCII)

#: The host name the proxy probe asks for. ``.invalid`` never resolves
#: (RFC 6761), it is on no allowlist, and tinyproxy refuses it on the name
#: alone, before any DNS lookup or connection (``process_request`` in
#: tinyproxy 1.11.3's ``reqs.c`` runs the filter on the host string). The
#: proxy logs one "Proxying refused on filtered domain" line per probe; the
#: distinctive name says where it came from.
PROXY_PROBE_HOST = "steamhangar-about-probe.invalid"

_CORE_NOT_PROBED = (
    "vault-api has no network path to vault-core (egress lock, ADR-0011), "
    "so whether it is running now is not checked."
)


@dataclass(frozen=True)
class Component:
    """One entry of the answer. ``routers/about.py`` turns it into JSON."""

    name: Name
    version: str | None
    commit: str | None
    status: Status
    checked_at: str
    detail: str | None = None


def _clean_version(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and is_valid_version(value):
        return value
    return INVALID_VALUE


def _clean_commit(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and (value == INVALID_VALUE or is_valid_commit(value)):
        return value
    return INVALID_VALUE


# --- vault-api -----------------------------------------------------------------


def api_component(environ: Mapping[str, str], checked_at: str) -> Component:
    version, commit = reported_identity(environ)
    detail = "Also serves the web UI, so the web UI has this version."
    if version == INVALID_VALUE:
        detail += (
            f" {BUILD_VERSION_ENV} is set but is not a valid version;"
            f" server_version in GET /v1/settings shows the fallback {BASE_VERSION}."
        )
    elif BUILD_VERSION_ENV not in environ:
        detail += " No build version is baked in (native run), so this is the source tree's release line."
    return Component("vault-api", version, commit, "ok", checked_at, detail)


# --- vault-core ----------------------------------------------------------------


def core_version_path(settings: Settings) -> str:
    """``<cache volume>/logs/vault-core-version.json``: the cache root is
    ``<volume>/cache`` in every image (``VAULT_CACHE_ROOT=/vault/cache``)."""
    volume = os.path.dirname(os.path.normpath(settings.cache_root))
    return os.path.join(volume, CORE_VERSION_FILE)


def _read_small_file(path: str) -> bytes:
    """Read at most ``CORE_VERSION_FILE_MAX_BYTES + 1`` bytes of a REGULAR
    file in a REAL directory. ``O_NOFOLLOW`` refuses a symlink in the last
    component only, so the parent is ``lstat``-ed first: a symlinked
    ``logs/`` raises ``ValueError`` (docs/LEARNINGS.md, WP SEC-FIX-4). A
    missing parent raises ``FileNotFoundError`` like a missing file.
    ``O_NONBLOCK`` keeps a planted FIFO from blocking the probe thread; a
    non-regular file raises ``ValueError``."""
    if not stat.S_ISDIR(os.lstat(os.path.dirname(path) or ".").st_mode):
        raise ValueError("parent directory is not a real directory")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("not a regular file")
        chunks: list[bytes] = []
        remaining = CORE_VERSION_FILE_MAX_BYTES + 1
        while remaining > 0:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def parse_core_version_file(raw: bytes) -> tuple[str, str | None, str] | None:
    """``(version, commit, recorded_at)`` from the hook's file, or ``None``
    when the content is not exactly what the hook writes.

    Strict on purpose: one JSON object with exactly the four keys,
    ``component == "vault-core"``, a version that passes the VER-1 grammar
    (the hook's ``invalid`` does), a commit id or ``unknown``/``invalid``, and
    a ``recorded_at`` in this project's UTC timestamp format.
    """
    if len(raw) > CORE_VERSION_FILE_MAX_BYTES:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    if not isinstance(data, dict) or set(data) != _CORE_FILE_KEYS:
        return None
    version, commit, recorded_at = data["version"], data["commit"], data["recorded_at"]
    if data["component"] != "vault-core":
        return None
    if not isinstance(version, str) or not is_valid_version(version):
        return None
    if not isinstance(commit, str):
        return None
    if commit not in ("unknown", INVALID_VALUE) and not is_valid_commit(commit):
        return None
    if not isinstance(recorded_at, str) or not _TIMESTAMP.fullmatch(recorded_at):
        return None
    if parse_utc_iso(recorded_at) is None:
        return None
    return version, (None if commit == "unknown" else commit), recorded_at


def core_component(path: str, checked_at: str) -> Component:
    try:
        raw = _read_small_file(path)
    except FileNotFoundError:
        return Component(
            "vault-core", None, None, "unknown", checked_at,
            "No version recorded yet: vault-core writes it into the shared "
            "cache volume when it starts. " + _CORE_NOT_PROBED,
        )
    except (OSError, ValueError) as exc:
        logger.warning("GET /v1/about: cannot read vault-core's version file: %s", exc)
        return Component(
            "vault-core", INVALID_VALUE, None, "unknown", checked_at,
            "vault-core's version file cannot be read as a regular file in "
            "a real directory. " + _CORE_NOT_PROBED,
        )
    parsed = parse_core_version_file(raw)
    if parsed is None:
        return Component(
            "vault-core", INVALID_VALUE, None, "unknown", checked_at,
            "vault-core's version file is malformed. " + _CORE_NOT_PROBED,
        )
    version, commit, recorded_at = parsed
    detail = f"Recorded at vault-core's last start, {recorded_at}. " + _CORE_NOT_PROBED
    if version == INVALID_VALUE:
        detail = f"vault-core's {BUILD_VERSION_ENV} is not a valid version. " + detail
    return Component("vault-core", version, commit, "unknown", checked_at, detail)


# --- vault-runner and SteamPrefill ---------------------------------------------


def _local_steamprefill(
    settings: Settings, environ: Mapping[str, str], checked_at: str
) -> Component:
    version = steamprefill_version(environ)
    path = settings.steamprefill_path
    if path and os.path.isfile(path) and os.access(path, os.X_OK):
        detail = "Runs inside vault-api (VAULT_PREFILL_MODE=subprocess)."
        if version is None:
            detail += " No SteamPrefill version is baked in (native run)."
        return Component("steamprefill", version, None, "ok", checked_at, detail)
    return Component(
        "steamprefill", version, None, "unknown", checked_at,
        "VAULT_PREFILL_MODE=subprocess, but no executable SteamPrefill is "
        "configured (VAULT_STEAMPREFILL_PATH).",
    )


def runner_components(
    settings: Settings,
    environ: Mapping[str, str],
    checked_at: str,
    now: datetime,
) -> tuple[Component, Component]:
    """``(vault-runner, steamprefill)``. Opens and closes its own database
    connection, in the calling thread (docs/LEARNINGS.md, SQLite)."""
    if not settings.prefill_mode_queue:
        return (
            Component(
                "vault-runner", None, None, "not_in_use", checked_at,
                "VAULT_PREFILL_MODE=subprocess: vault-api runs SteamPrefill "
                "itself, so a vault-runner gets no jobs.",
            ),
            _local_steamprefill(settings, environ, checked_at),
        )
    try:
        conn = get_connection(settings.db_path)
        try:
            latest, fresh, age = runner_presence.fresh_and_latest(conn, now)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning("GET /v1/about: cannot read runner presence: %s", exc)
        detail = "Could not read the runner presence table."
        return (
            Component("vault-runner", None, None, "unknown", checked_at, detail),
            Component("steamprefill", None, None, "unknown", checked_at,
                      "Runs in vault-runner. " + detail),
        )
    if latest is None or age is None:
        return (
            Component(
                "vault-runner", None, None, "unreachable", checked_at,
                "No vault-runner has reported yet. Queue mode needs the "
                "vault-runner container.",
            ),
            Component(
                "steamprefill", None, None, "unknown", checked_at,
                "Runs in vault-runner, which has not reported yet.",
            ),
        )
    version = _clean_version(latest.build_version)
    commit = _clean_commit(latest.build_commit)
    sp_version = _clean_version(latest.steamprefill_version)
    stale_limit = int(runner_presence.PRESENCE_STALE_SECONDS)
    if age < runner_presence.PRESENCE_STALE_SECONDS:
        detail = f"Last seen {int(age)} s ago."
        if fresh > 1:
            detail += f" More than one runner reported in the last {stale_limit} s."
        return (
            Component("vault-runner", version, commit, "ok", checked_at, detail),
            Component("steamprefill", sp_version, None, "ok", checked_at,
                      "Runs in vault-runner; version as the runner reported it."),
        )
    return (
        Component(
            "vault-runner", version, commit, "unreachable", checked_at,
            f"Last seen {latest.last_seen}, more than {stale_limit} s ago. "
            "Is the vault-runner container running?",
        ),
        Component(
            "steamprefill", sp_version, None, "unknown", checked_at,
            f"Runs in vault-runner; last reported {latest.last_seen}.",
        ),
    )


# --- vault-proxy ---------------------------------------------------------------


def proxy_address(environ: Mapping[str, str]) -> tuple[str, int] | None | Literal["bad"]:
    """vault-api's HTTP proxy from the environment, the way ``urllib`` finds
    it (``http_proxy`` wins over ``HTTP_PROXY``). ``None``: no proxy
    configured. ``"bad"``: set but not a usable ``http://host[:port]``."""
    raw = environ.get("http_proxy") or environ.get("HTTP_PROXY")
    if not raw:
        return None
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        return "bad"
    if parts.scheme != "http" or not parts.hostname:
        return "bad"
    return parts.hostname, (port if port is not None else 80)


def probe_proxy(address: tuple[str, int], timeout: float) -> int | None:
    """Ask the proxy for a host it must refuse; return the HTTP status code,
    or ``None`` when the answer is not an HTTP status line.

    **Why reachability only, and no version.** The proxy image carries its
    version as ``VAULT_BUILD_VERSION`` like every image, but nothing can
    read it over the network without weakening the egress lock: tinyproxy
    1.11.3 runs its destination filter BEFORE it checks ``StatHost`` (the
    only page it can serve itself), so a version page would need its host on
    the egress allowlist, and that filter matches case-insensitively while
    the ``StatHost`` comparison is a case-sensitive ``strcmp``, so an
    upper-case spelling of that host would pass the filter and be forwarded
    (WP VER-2; read in the 1.11.3 ``reqs.c`` source, ``process_request``).
    The probe instead proves the proxy answers and that its filter refuses
    an off-list host: ``403`` is the expected answer and no byte leaves the
    proxy for it. Raises ``OSError`` (incl. timeouts) when the proxy does not
    answer.
    """
    request = (
        f"GET http://{PROXY_PROBE_HOST}/ HTTP/1.0\r\n"
        f"Host: {PROXY_PROBE_HOST}\r\n"
        "User-Agent: vault-api-about\r\n\r\n"
    ).encode("ascii")
    with socket.create_connection(address, timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(request)
        data = b""
        while b"\r\n" not in data and len(data) < 256:
            chunk = sock.recv(256 - len(data))
            if not chunk:
                break
            data += chunk
    match = re.match(rb"HTTP/1\.[01] ([0-9]{3})[ \r]", data)
    return int(match.group(1)) if match else None


def proxy_component(
    environ: Mapping[str, str],
    checked_at: str,
    timeout: float,
    prober: Callable[[tuple[str, int], float], int | None] = probe_proxy,
) -> Component:
    address = proxy_address(environ)
    if address is None:
        return Component(
            "vault-proxy", None, None, "not_in_use", checked_at,
            "vault-api has no HTTP_PROXY, so this deployment does not use the "
            "egress-lock proxy.",
        )
    if address == "bad":
        return Component(
            "vault-proxy", None, None, "unknown", checked_at,
            "HTTP_PROXY is set but is not a usable http://host:port URL.",
        )
    try:
        code = prober(address, timeout)
    except OSError:
        return Component(
            "vault-proxy", None, None, "unreachable", checked_at,
            "The egress proxy did not answer.",
        )
    if code == 403:
        return Component(
            "vault-proxy", None, None, "ok", checked_at,
            "Answers, and refuses a host that is not on the egress allowlist. "
            "Its version is not shown: reading it would need a hole in the "
            "egress filter.",
        )
    if code is None:
        return Component(
            "vault-proxy", None, None, "unknown", checked_at,
            "Something answered on the proxy address, but not with HTTP.",
        )
    return Component(
        "vault-proxy", None, None, "unknown", checked_at,
        f"The proxy answered HTTP {code} for a host that is on no allowlist, "
        "instead of refusing it with 403. Check the egress filter.",
    )


# --- vault-dns -----------------------------------------------------------------


def dns_component(checked_at: str) -> Component:
    return Component(
        "vault-dns", None, None, "unknown", checked_at,
        "Optional (compose profile dns); most setups use their own DNS rewrite "
        "instead. Not checked: vault-api has no network path to vault-dns "
        "(egress lock, ADR-0011), and a test query would itself be DNS traffic.",
    )


# --- the service ---------------------------------------------------------------


class AboutService:
    """Builds and caches the component list. One per app (``app.state.about``)."""

    def __init__(
        self,
        settings: Settings,
        *,
        ttl_seconds: float = CACHE_TTL_SECONDS,
        probe_timeout_seconds: float = PROBE_TIMEOUT_SECONDS,
        deadline_seconds: float = PROBE_DEADLINE_SECONDS,
        environ: Mapping[str, str] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        wallclock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        proxy_prober: Callable[[tuple[str, int], float], int | None] = probe_proxy,
    ) -> None:
        self._settings = settings
        self._ttl = ttl_seconds
        self._timeout = probe_timeout_seconds
        self._deadline = deadline_seconds
        self._environ = environ
        self._monotonic = monotonic
        self._wallclock = wallclock
        self._proxy_prober = proxy_prober
        self._lock = threading.Lock()
        self._cached: tuple[float, list[Component]] | None = None

    def components(self) -> list[Component]:
        """The cached list, rebuilt when older than the TTL. Concurrent
        callers during a rebuild wait for it and share its result."""
        with self._lock:
            now = self._monotonic()
            if self._cached is not None and now - self._cached[0] < self._ttl:
                return list(self._cached[1])
            result = self._build()
            self._cached = (self._monotonic(), result)
            return list(result)

    def _build(self) -> list[Component]:
        environ = os.environ if self._environ is None else self._environ
        moment = self._wallclock()
        checked_at = to_utc_iso(moment)
        settings = self._settings

        def unknown(name: Name, why: str) -> Component:
            return Component(name, None, None, "unknown", checked_at, why)

        late = f"No answer within {self._deadline:g} s."
        pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="vault-about")
        try:
            core_f: Future[Component] = pool.submit(
                core_component, core_version_path(settings), checked_at
            )
            runner_f: Future[tuple[Component, Component]] = pool.submit(
                runner_components, settings, environ, checked_at, moment
            )
            proxy_f: Future[Component] = pool.submit(
                proxy_component, environ, checked_at, self._timeout, self._proxy_prober
            )
            wait([core_f, runner_f, proxy_f], timeout=self._deadline)
        finally:
            # Never wait for a late lookup: it finishes (bounded by its own
            # timeout) on its thread and its result is dropped.
            pool.shutdown(wait=False, cancel_futures=True)

        failed = "The lookup failed."

        def outcome(future: Future, fallback):  # type: ignore[no-untyped-def]
            if not future.done():
                return fallback(late)
            exc = future.exception()
            if exc is not None:
                logger.warning("GET /v1/about: a component lookup failed: %r", exc)
                return fallback(failed)
            return future.result()

        core = outcome(core_f, lambda why: unknown("vault-core", why))
        runner, prefill = outcome(
            runner_f,
            lambda why: (unknown("vault-runner", why), unknown("steamprefill", why)),
        )
        # A proxy that does not answer in time is unreachable; a probe that
        # crashed says nothing about the proxy, so that stays unknown.
        proxy = outcome(
            proxy_f,
            lambda why: replace(
                unknown("vault-proxy", why),
                status="unreachable" if why == late else "unknown",
            ),
        )
        return [
            api_component(environ, checked_at),
            core,
            runner,
            prefill,
            proxy,
            dns_component(checked_at),
        ]
