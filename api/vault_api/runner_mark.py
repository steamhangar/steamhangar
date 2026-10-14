"""The prefill runner's marker request (WP D15-RUNNER, runner side).

Before each prefill the runner (``prefill_runner``, or vault-api's own worker
in subprocess mode) calls :func:`announce`:

1. find vault-core the way SteamPrefill will, a moment later: the same
   candidates in the same order (``lancache.steamcontent.com``,
   ``localhost``, the Docker gateway ``172.17.0.1``, the local hostname),
   each accepted only if it resolves to a private (RFC 1918) or loopback
   IPv4 address and answers ``GET /lancache-heartbeat`` with an
   ``X-LanCache-Processed-By`` header (deploy/README.md, "How SteamPrefill
   finds the cache"; docs/LEARNINGS.md, Steam ecosystem facts). Same
   destination, same route, so vault-core logs the marker under the same
   source address it then logs SteamPrefill's chunks under;
2. issue a token in the shared database (``runner_identity.issue_token``);
3. ``GET /steamhangar/runner-mark/<token>`` from that address. vault-core
   answers 204 locally and writes the line the sweep turns into the runner's
   identity (``runner_identity`` module docstring).

**Never raises, never blocks a download for long.** Every step is
fail-soft: no cache found, a database error, a refused connection or an old
vault-core without the marker location (it answers 404) are logged once and
the prefill runs unmarked -- a "no cache found" is remembered for
:data:`NEGATIVE_CACHE_SECONDS` so a queue of jobs does not repeat the
lookup each time -- the only loss is that its traffic counts as an
ordinary client, exactly as before this package. Each HTTP step has a
:data:`HTTP_TIMEOUT_SECONDS` timeout; name resolution uses the system
resolver (the same one SteamPrefill uses).

Only private/loopback IPv4 destinations are ever contacted, on port 80,
without following redirects (``http.client`` does not follow any), so this
adds no egress the runner did not already have.
"""

from __future__ import annotations

import http.client
import ipaddress
import logging
import socket
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Callable

from vault_api import runner_identity

logger = logging.getLogger(__name__)

#: SteamPrefill's cache-discovery candidates, in its order (deploy/README.md).
CANDIDATE_HOSTS = ("lancache.steamcontent.com", "localhost", "172.17.0.1")

#: The LanCache heartbeat contract vault-core implements.
HEARTBEAT_PATH = "/lancache-heartbeat"
HEARTBEAT_HEADER = "X-LanCache-Processed-By"

#: vault-core answers the marker with this header (core/nginx/nginx.conf), so
#: the runner can tell "logged" from an old core's 404.
MARK_HEADER = "X-SteamHangar-Runner-Mark"

HTTP_PORT = 80
HTTP_TIMEOUT_SECONDS = 3.0

#: After a discovery that found no cache, :func:`announce` skips discovery
#: for this long: a run of queued jobs on an install without a reachable
#: cache would otherwise pay up to four candidates x 3 s per job. Only the
#: negative result is cached; a found cache is looked up again every time
#: (it is cheap, and the route may change).
NEGATIVE_CACHE_SECONDS = 300.0

_negative_lock = threading.Lock()
#: ``time.monotonic()`` value until which discovery is skipped, or ``None``.
_negative_until: float | None = None


def reset_negative_cache() -> None:
    """Forget a cached "no cache found" (tests, and nothing else)."""
    global _negative_until
    with _negative_lock:
        _negative_until = None

_PRIVATE_V4 = tuple(
    ipaddress.IPv4Network(net)
    for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8")
)

#: (host) -> IPv4 literal or None.
Resolver = Callable[[str], "str | None"]
#: (ip, port, path, timeout) -> (status, headers lower-cased) or None on error.
Fetcher = Callable[[str, int, str, float], "tuple[int, dict[str, str]] | None"]


def is_private_v4(text: str) -> bool:
    """RFC 1918 or loopback IPv4 -- what SteamPrefill accepts."""
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return False
    return isinstance(address, ipaddress.IPv4Address) and any(
        address in net for net in _PRIVATE_V4
    )


def resolve_v4(host: str) -> str | None:
    """First IPv4 address of ``host``, or ``None``."""
    try:
        infos = socket.getaddrinfo(host, HTTP_PORT, socket.AF_INET, socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return None
    for info in infos:
        address = info[4][0]
        if isinstance(address, str):
            return address
    return None


def http_get(
    ip: str, port: int, path: str, timeout: float
) -> tuple[int, dict[str, str]] | None:
    """One plain ``GET``; ``None`` on any connection or protocol error."""
    conn = http.client.HTTPConnection(ip, port, timeout=timeout)
    try:
        conn.request(
            "GET", path, headers={"Host": CANDIDATE_HOSTS[0], "User-Agent": "steamhangar-runner"}
        )
        response = conn.getresponse()
        response.read(4096)
        headers = {name.lower(): value for name, value in response.getheaders()}
        return response.status, headers
    except (OSError, http.client.HTTPException):
        return None
    finally:
        conn.close()


def candidates() -> list[str]:
    """The four candidate names, the local hostname last."""
    names = list(CANDIDATE_HOSTS)
    try:
        names.append(socket.gethostname())
    except OSError:  # pragma: no cover - gethostname does not fail on Linux
        pass
    return names


def find_cache(
    *,
    resolve: Resolver | None = None,
    fetch: Fetcher | None = None,
    port: int = HTTP_PORT,
    names: list[str] | None = None,
) -> str | None:
    """The address SteamPrefill will use, or ``None`` when none qualifies.

    ``resolve``/``fetch`` default to the module's :func:`resolve_v4` /
    :func:`http_get`, looked up at CALL time (tests replace them).
    """
    resolve = resolve if resolve is not None else resolve_v4
    fetch = fetch if fetch is not None else http_get
    for name in names if names is not None else candidates():
        ip = resolve(name)
        if ip is None or not is_private_v4(ip):
            continue
        answer = fetch(ip, port, HEARTBEAT_PATH, HTTP_TIMEOUT_SECONDS)
        if answer is not None and HEARTBEAT_HEADER.lower() in answer[1]:
            return ip
    return None


@dataclass(frozen=True)
class AnnounceOutcome:
    #: "sent" | "no-cache" | "no-cache-cached" (skipped, see
    #: NEGATIVE_CACHE_SECONDS) | "db-error" | "unreachable" | "not-supported"
    state: str
    cache_ip: str | None = None


def announce(
    conn: sqlite3.Connection,
    runner_id: str,
    *,
    resolve: Resolver | None = None,
    fetch: Fetcher | None = None,
    port: int = HTTP_PORT,
    names: list[str] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> AnnounceOutcome:
    """Send one marker through vault-core. Never raises (module docstring)."""
    global _negative_until
    fetch = fetch if fetch is not None else http_get
    now = clock()
    with _negative_lock:
        skip = _negative_until is not None and now < _negative_until
    if skip:
        logger.info(
            "runner mark: no cache was found less than %.0fs ago; prefilling "
            "unmarked without looking again.", NEGATIVE_CACHE_SECONDS,
        )
        return AnnounceOutcome("no-cache-cached")
    try:
        ip = find_cache(resolve=resolve, fetch=fetch, port=port, names=names)
    except Exception:  # noqa: BLE001 - a marker must never stop a prefill
        logger.exception("runner mark: cache discovery failed; prefilling unmarked")
        return AnnounceOutcome("no-cache")
    if ip is None:
        with _negative_lock:
            _negative_until = now + NEGATIVE_CACHE_SECONDS
        logger.warning(
            "runner mark: no candidate answered %s with %s (the same check "
            "SteamPrefill makes); prefill traffic will count as an ordinary "
            "client.",
            HEARTBEAT_PATH, HEARTBEAT_HEADER,
        )
        return AnnounceOutcome("no-cache")
    try:
        token = runner_identity.issue_token(conn, runner_id)
    except sqlite3.Error as exc:
        logger.warning(
            "runner mark: could not store a token (%s); prefilling unmarked.", exc
        )
        return AnnounceOutcome("db-error", ip)
    try:
        answer = fetch(ip, port, runner_identity.mark_path(token), HTTP_TIMEOUT_SECONDS)
    except Exception:  # noqa: BLE001
        answer = None
    if answer is None:
        logger.warning(
            "runner mark: vault-core at %s did not answer the marker request; "
            "prefilling unmarked.", ip,
        )
        return AnnounceOutcome("unreachable", ip)
    status, headers = answer
    if not (200 <= status <= 299) or MARK_HEADER.lower() not in headers:
        logger.warning(
            "runner mark: vault-core at %s answered %s without %s -- a "
            "vault-core older than D15-RUNNER; prefill traffic will count as "
            "an ordinary client until it is updated.",
            ip, status, MARK_HEADER,
        )
        return AnnounceOutcome("not-supported", ip)
    logger.info("runner mark: sent through vault-core at %s.", ip)
    return AnnounceOutcome("sent", ip)
