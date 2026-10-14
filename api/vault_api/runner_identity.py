"""Which cache-log address is the prefill runner (WP D15-RUNNER, schema v27).

The problem
-----------
SteamPrefill reads games through vault-core exactly like a PC does, so its
traffic lands in vault-core's event log under whatever source address
vault-core sees for the runner -- an address vault-api cannot derive: it
depends on how the runner reaches vault-core (the DNS rewrite, the Docker
gateway candidate, a dedicated bind address) and on the host's routing and
NAT (api/README.md, "The prefill runner in the client figures"). Before this
package that address was an ordinary agentless client: it counted as an
"active client", and every byte a forced verify run (CACHE-COMPLETE-1b) read
from disk was credited to "saved by the cache".

The mechanism (user decision 2026-10-14, option B)
---------------------------------------------------
Before each prefill the runner sends ONE marker request through vault-core,
over the same route SteamPrefill is about to use (``runner_mark.announce``):

    GET /steamhangar/runner-mark/<token>

vault-core answers it locally (204, never upstream) and writes it to the
event log with ``$remote_addr`` and the cache status ``RUNNER``. The sweep
(``event_sweep.aggregate_lines``) collects such lines and, in the same
transaction that advances its cursor, hands them to :func:`apply_marks`.

**Why a token, and why it cannot be spoofed into anything.** Any LAN host
can send the same path. What it cannot do is name a valid token: the runner
draws 128 random bits (:func:`issue_token`) and stores them in the shared
database BEFORE sending, and :func:`apply_marks` accepts a line only when its
token is an issued, not yet consumed one and the line's event time lies
within :data:`MARK_VALID_BEFORE` / :data:`MARK_VALID_AFTER` of the issue
time. That is the runner's existing credential, reused: the runner never
held an API key (``prefill_runner.main``, ``require_api_key=False``); what
it has that a LAN host lacks is write access to the shared SQLite file. A
token is consumed by its first valid line, so replaying a token read from a
log later does nothing. A guessed or stale token is counted and ignored. The
worst case of a forged marker would be a LAN host flagging ITSELF as the
runner (hiding its own traffic from the client figures); with the token that
is not reachable either.

How long an address stays the runner
------------------------------------
Each accepted marker extends a **period** for its address
(``runner_address_periods``): a marker within :data:`HOLD` of the address's
newest period's last marker extends that period, a later one opens a new
period. Traffic of the address counts as the runner's from the start of the
UTC day of the period's first marker up to the end of the UTC day
:data:`HOLD` after its last marker (:func:`windows`).

* **Day-aligned on purpose.** A UTC day boundary is also an hour and a
  5-minute boundary, so the same window selects the same rows in every
  traffic tier: 24 h, 7 d, 30 d and "all" can never disagree about whether a
  row was the runner's. The price: on the first day of a period, traffic the
  same address carried before the first marker counts as the runner's too.
* **Why 7 days.** A nightly prefill re-marks every day; a job runs for at
  most ``VAULT_PREFILL_TIMEOUT_SECONDS`` (default 4 h) after its marker.
  Seven days keeps the address flagged across a week without prefills, and
  stops flagging it a week after the runner moved to another address (a
  container re-created on a new bridge subnet, a changed bind address).
* **Periods are kept forever** (one tiny row per runner address and period):
  the daily traffic tier is kept forever too, and deleting a period would
  move that history back into the client figures.

What this does NOT do
---------------------
* Traffic from before the first accepted marker (an install upgraded from an
  older build) stays with the address it came from.
* If the runner's address is also a real client's address (both reach
  vault-core through the same NAT, e.g. the host itself), that client's
  traffic inside the window counts as the runner's. vault-core sees one
  address; nothing can split it.
* No marker reaches vault-api while the event log is off
  (``VAULT_EVENT_LOG`` unset) -- like every D15 figure.
"""

from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from vault_api.jobs import parse_utc_iso, to_utc_iso

#: The path prefix vault-core logs as a runner marker (core/nginx/nginx.conf,
#: ``location ^~ /steamhangar/runner-mark/``). The token follows it.
MARK_PATH_PREFIX = "/steamhangar/runner-mark/"

#: The cache status vault-core writes for a marker line (event-log field 4).
MARK_CACHE_STATUS = "RUNNER"

#: 128 random bits, lowercase hex.
TOKEN_HEX_LENGTH = 32

#: A marker line's event time may lie this far BEFORE the token's issue time
#: (the runner stamps the token, then sends; nginx and the runner read the
#: same host clock, the margin covers second flooring and a small step).
MARK_VALID_BEFORE = timedelta(minutes=5)
#: ... and this far AFTER it. The runner sends right after issuing; an hour
#: is generous and still keeps an unconsumed token from being usable later.
MARK_VALID_AFTER = timedelta(hours=1)

#: Unconsumed and consumed tokens older than this are deleted when the next
#: token is issued. Longer than any sweep backlog worth honouring.
TOKEN_KEEP = timedelta(days=7)

#: How long an address stays the runner after its last marker. See the
#: module docstring ("Why 7 days").
HOLD = timedelta(days=7)

#: What the Clients page calls it.
DISPLAY_NAME = "Prefill runner"


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(timezone.utc)


def is_valid_token(token: str) -> bool:
    """Exactly :data:`TOKEN_HEX_LENGTH` lowercase ASCII hex digits."""
    return (
        len(token) == TOKEN_HEX_LENGTH
        and token.isascii()
        and all(character in "0123456789abcdef" for character in token)
    )


def token_from_uri(uri: str) -> str | None:
    """The token of a marker path, or ``None`` when ``uri`` is not one."""
    if not uri.startswith(MARK_PATH_PREFIX):
        return None
    token = uri[len(MARK_PATH_PREFIX):]
    return token if is_valid_token(token) else None


def mark_path(token: str) -> str:
    if not is_valid_token(token):
        raise ValueError("not a runner-mark token")
    return MARK_PATH_PREFIX + token


# --------------------------------------------------------------------------
# Runner side: issuing
# --------------------------------------------------------------------------


def issue_token(
    conn: sqlite3.Connection, runner_id: str, now: datetime | None = None
) -> str:
    """Store a fresh token and return it. Committed here.

    Also deletes tokens older than :data:`TOKEN_KEEP`. Raises
    ``sqlite3.Error`` (including "no such table" against a database vault-api
    has not migrated yet); the caller logs it and prefills unmarked.
    """
    moment = _now(now)
    token = secrets.token_hex(TOKEN_HEX_LENGTH // 2)
    try:
        conn.execute(
            "INSERT INTO runner_marks (token, issued_at, runner_id) VALUES (?, ?, ?)",
            (token, to_utc_iso(moment), runner_id),
        )
        conn.execute(
            "DELETE FROM runner_marks WHERE issued_at < ?",
            (to_utc_iso(moment - TOKEN_KEEP),),
        )
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise
    return token


# --------------------------------------------------------------------------
# Sweep side: accepting
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Mark:
    """One marker line as the sweep parsed it (not yet validated)."""

    token: str
    addr: str
    #: The line's UTC event time (sweep time when nginx's stamp was unreadable).
    seen_at: str


@dataclass
class MarkResult:
    accepted: int = 0
    #: Unknown, already consumed, or outside the validity window.
    rejected: int = 0


def apply_marks(conn: sqlite3.Connection, marks: list[Mark]) -> MarkResult:
    """Validate ``marks`` and extend the address periods.

    The CALLER holds the transaction (``event_sweep.commit_batch``), so a
    batch's markers are applied exactly once, together with the cursor.
    """
    result = MarkResult()
    for mark in marks:
        seen = parse_utc_iso(mark.seen_at)
        row = conn.execute(
            "SELECT issued_at, consumed_at FROM runner_marks WHERE token = ?",
            (mark.token,),
        ).fetchone()
        issued = parse_utc_iso(row["issued_at"]) if row is not None else None
        if (
            seen is None
            or row is None
            or row["consumed_at"] is not None
            or issued is None
            or not (issued - MARK_VALID_BEFORE <= seen <= issued + MARK_VALID_AFTER)
        ):
            result.rejected += 1
            continue
        seen_iso = to_utc_iso(seen)
        conn.execute(
            "UPDATE runner_marks SET consumed_at = ?, consumed_addr = ? WHERE token = ?",
            (seen_iso, mark.addr, mark.token),
        )
        _extend_period(conn, mark.addr, seen)
        result.accepted += 1
    return result


def _extend_period(conn: sqlite3.Connection, addr: str, seen: datetime) -> None:
    seen_iso = to_utc_iso(seen)
    newest = conn.execute(
        """
        SELECT id, first_marked_at, last_marked_at FROM runner_address_periods
        WHERE addr = ? ORDER BY last_marked_at DESC, id DESC LIMIT 1
        """,
        (addr,),
    ).fetchone()
    last = parse_utc_iso(newest["last_marked_at"]) if newest is not None else None
    if newest is not None and last is not None and seen <= last + HOLD:
        conn.execute(
            """
            UPDATE runner_address_periods SET
                first_marked_at = MIN(first_marked_at, ?),
                last_marked_at  = MAX(last_marked_at, ?)
            WHERE id = ?
            """,
            (seen_iso, seen_iso, newest["id"]),
        )
        return
    conn.execute(
        """
        INSERT INTO runner_address_periods (addr, first_marked_at, last_marked_at)
        VALUES (?, ?, ?)
        """,
        (addr, seen_iso, seen_iso),
    )


# --------------------------------------------------------------------------
# Readers
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RunnerWindow:
    """Rows of ``addr`` with ``start <= bucket_at < end`` are the runner's."""

    addr: str
    #: Inclusive; a UTC midnight.
    start: str
    #: Exclusive; a UTC midnight.
    end: str
    first_marked_at: str
    last_marked_at: str


def _day(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def windows(conn: sqlite3.Connection) -> tuple[RunnerWindow, ...]:
    """Every runner period as a day-aligned window (module docstring).

    A period whose stored stamps do not parse is skipped (fail-soft: that
    traffic stays with its address rather than wedging the endpoint).
    """
    rows = conn.execute(
        """
        SELECT addr, first_marked_at, last_marked_at FROM runner_address_periods
        ORDER BY first_marked_at, addr
        """
    ).fetchall()
    result: list[RunnerWindow] = []
    for row in rows:
        first = parse_utc_iso(row["first_marked_at"])
        last = parse_utc_iso(row["last_marked_at"])
        if first is None or last is None:
            continue
        result.append(
            RunnerWindow(
                addr=str(row["addr"]),
                start=to_utc_iso(_day(first)),
                end=to_utc_iso(_day(last + HOLD) + timedelta(days=1)),
                first_marked_at=to_utc_iso(first),
                last_marked_at=to_utc_iso(last),
            )
        )
    return tuple(result)


def current_addrs(
    found: tuple[RunnerWindow, ...], now: datetime | None = None
) -> list[str]:
    """Addresses whose window contains ``now``, sorted."""
    moment = to_utc_iso(_now(now))
    return sorted({w.addr for w in found if w.start <= moment < w.end})
