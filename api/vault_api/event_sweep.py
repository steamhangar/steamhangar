"""The cache-event sweep (WP 3.11, ADR-0008).

vault-core writes a second, purpose-built, machine-readable ``access_log``
(WP 3.10). This module is its only consumer: it reads new lines on the
scheduler's cadence, turns them into per-client statistics, and — the part
ADR-0001's hybrid miss handling was waiting for — enqueues a prefill when a
cache MISS proves a mapped app is not fully cached.

Nothing here is on vault-core's serving path. The event log is a file; if
vault-api is down, stopped, or has never been configured with a path, nginx
keeps writing and keeps serving, and this module simply reads more lines the
next time it runs (ADR-0008 option C, chosen precisely so the serving path
never depends on the API being alive).

The line format (vault-core, core/README.md "Cache-event log")
--------------------------------------------------------------
Tab-separated, ``escape=default``, exactly **9** fields, version-prefixed::

    v1  2026-08-09T14:03:11+02:00  192.168.1.42  MISS  70403 \
        /depot/70403/chunk/773d1005…  999232  lancache.steamcontent.com  200

1. ``v1`` — format version
2. ``$time_iso8601``
3. ``$remote_addr``
4. ``HIT`` / ``MISS`` / ``BYPASS``
5. depot id, or ``-``
6. ``$uri`` (decoded, query-free, bounded to 300 chars by nginx)
7. ``$bytes_sent``
8. ``$host``
9. ``$status``

``escape=default`` escapes every byte below 0x20, above 0x7E, plus ``"`` and
``\\``, as a printable ``\\xXX`` sequence. A hostile request path containing a
percent-encoded tab or newline therefore arrives here as the literal text
``\\x09``/``\\x0A``, never as a real tab or newline — so the only tab bytes on a
line are the separators nginx itself wrote, and a plain ``split("\\t")`` always
sees exactly 9 fields. That guarantee is pinned empirically on the producing
side (``core/tests/test-core.ps1``) and defended on this side anyway: a line
that does not split into 9 fields is counted and skipped, never guessed at.

**Unknown versions are skipped, never misparsed.** Field 1 must be exactly
``v1``. A future ``v2`` with a different column order would otherwise be read
as v1 and silently produce wrong depot ids and wrong byte counts — the failure
mode a version field exists to prevent. Such lines are counted and a warning
names the version once per sweep.

The cursor contract (ADR-0008)
------------------------------
``event_sweep_state.cursor_offset`` is a byte offset into the log. Three rules
make "each line is read once, and a sweep failure re-reads instead of losing
data" true rather than aspirational:

1. **A line is never consumed without its trailing newline.** nginx's writes
   are buffered (``buffer=64k flush=5s``), so a read at EOF routinely lands
   mid-line. ``read_batch`` advances the cursor only to just past the LAST
   ``\\n`` in what it read; a trailing partial line stays unconsumed and is
   re-read, whole, next time.

   **With one exception that has to be distinguished from it** (review finding
   S1): a *full* ``MAX_BATCH_BYTES`` read containing no newline at all is not a
   partial tail — no amount of waiting turns 4 MiB of newline-free bytes into a
   line this parser accepts, since ``MAX_LINE_LENGTH`` is 8 KiB. Treated as a
   tail it was a silent, permanent stall: every sweep re-read the same bytes,
   consumed nothing, statistics stopped, bypass detection went blind, rotation
   could never fire (the file was never fully swept) and the only signal was an
   INFO line that read like progress. ``_skip_oversized`` now steps over the
   region to the next newline, counts it in ``oversized_skips_total``
   (``GET /v1/stats``) and warns. The residual case — an oversized region
   running to EOF with no newline anywhere — cannot be skipped without
   consuming an unterminated line, so the cursor holds and the sweep says so
   at WARNING on every tick instead of pretending to progress.
2. **The cursor advances in the same transaction as the batch's effects.**
   Statistics rows and the new cursor are one ``BEGIN IMMEDIATE`` write. There
   is no state in which the effects are committed but the cursor is not, or the
   other way round — see "Idempotence" below.
3. **A file that SHRANK below the cursor is treated as rotated**, and the
   cursor resets to 0 rather than seeking past the new end. Otherwise a rotated
   or externally-truncated log would be silently skipped forever.

Rotation, and why truncate-in-place is safe
-------------------------------------------
ADR-0008 gives rotation to this module and to nothing else — no logrotate, no
rename, nothing in ``core/``. ``maybe_truncate`` truncates the file to zero
bytes and resets the cursor, and only when **all** of these hold:

* the sweep succeeded and its cursor was committed,
* the file is at least ``VAULT_EVENT_LOG_MAX_BYTES`` (``0`` = never truncate),
* the file's size *right now* still equals the committed cursor, i.e. every
  byte in it has been read.

nginx opens ``access_log`` files with ``O_APPEND``, under which every write
targets the file's current end-of-file as tracked by the kernel rather than an
offset the process cached. Truncating while nginx holds the file open therefore
moves that end-of-file too: the next line nginx writes lands at offset 0, with
no gap and no sparse hole, and no ``USR1`` reopen is needed. That property is
why ADR-0008 could choose "sweep, then truncate" over a rename dance.

**The one residual race, stated plainly.** Between the size check and the
``truncate`` syscall, nginx can flush a buffer. Those lines are destroyed. The
window is two adjacent syscalls with no I/O between them, nginx flushes at most
every 5 seconds or 64 KiB, and truncation only happens once the log has grown
past 64 MiB (hundreds of thousands of lines) — so this is rare, bounded to a
fraction of one flush, and costs statistics plus possibly one miss trigger that
the next miss re-raises anyway. It is not zero, and no portable
"truncate-if-size-is-still-N" primitive exists to make it zero. An operator who
will not accept it sets ``VAULT_EVENT_LOG_MAX_BYTES=0`` and rotates the file
externally; the shrink detection above then picks the new file up.

Idempotence — what a re-read batch does, honestly
-------------------------------------------------
A crash (or a failing statement) anywhere before the commit means the whole
batch is re-read next sweep. What that costs, per effect:

* **Statistics: exactly-once.** The aggregate and the cursor advance are the
  same transaction, so a batch whose commit did not happen left no counters
  behind either. Nothing is double-counted. (This is the reason the commit is
  one transaction rather than a row-by-row loop, and
  ``test_a_crash_before_the_commit_does_not_double_count_statistics`` is the
  pin.)
* **Miss triggers: at-most-twice, and harmless.** Enqueues happen BEFORE the
  cursor commit, because a job that exists is better than a job that was lost.
  A crash after an enqueue re-reads the same MISS line, and two independent
  guards absorb it: the per-app cooldown row is written in its own committed
  transaction immediately after the enqueue, and if even that did not commit,
  ``jobs.enqueue_prefill``'s per-app dedupe returns the still-queued job
  instead of stacking a second one. The one gap left is a crash where the
  enqueued job also *finished* before the next sweep — then the app is
  eligible again and gets a second (non-forced, therefore ~3 s if it really is
  current) prefill. That is the trade this ordering deliberately makes.

Scheduling
----------
Runs on WP 3.5's scheduler thread but on its **own interval** and
**unconditionally** — deliberately NOT gated on ``VAULT_SCHEDULE_WINDOW``. See
``config.DEFAULT_EVENT_SWEEP_INTERVAL_MINUTES`` for the argument; the short
version is that this module owns log rotation and bypass detection, and both
break if the log goes unread for the 16 hours a day the window is shut.
"""

from __future__ import annotations

import errno
import logging
import os
import sqlite3
import stat
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from vault_api import agent_reports, deletion, jobs, tool_apps, webhooks
from vault_api.config import (
    WEBHOOK_EVENT_BYPASS_RESOLVED,
    WEBHOOK_EVENT_BYPASS_SUSPECTED,
    Settings,
)
from vault_api.jobs import immediate_transaction, parse_utc_iso, to_utc_iso
from vault_api.webhooks import WebhookNotifier

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Format constants
# --------------------------------------------------------------------------

#: The only format version this code understands (core/README.md field 1).
EVENT_LOG_VERSION = "v1"

#: Exactly this many tab-separated fields, or the line is not a v1 event line.
EVENT_FIELD_COUNT = 9

#: Cache statuses vault-core emits (``$vault_event_status``).
STATUS_HIT = "HIT"
STATUS_MISS = "MISS"
STATUS_BYPASS = "BYPASS"
CACHE_STATUSES = (STATUS_HIT, STATUS_MISS, STATUS_BYPASS)

#: The "no depot id in this URI" placeholder in field 5.
DEPOT_PLACEHOLDER = "-"

#: Longest line accepted, in characters. nginx bounds the URI field to 300 and
#: every other field is short, so a real line is comfortably under 500; 8 KiB
#: leaves enormous headroom while still bounding what one malformed/hostile
#: line can cost. Longer lines are counted and skipped.
MAX_LINE_LENGTH = 8192

#: Longest accepted client address. An IPv6 address with a zone id fits in 45
#: characters; 64 covers nginx's ``unix:`` form too, with room to spare.
MAX_ADDR_LENGTH = 64

#: Depot ids are uint32 in Steam's protocol (10 decimal digits at most). A
#: longer digit run in the URI is not a depot id — it is somebody probing
#: ``/depot/99999999999999999999/…`` — and rejecting it here also keeps the
#: value trivially inside SQLite's signed-64-bit INTEGER.
MAX_DEPOT_ID_DIGITS = 10

#: Bound on ``$bytes_sent``. 15 digits is a petabyte; a single HTTP response
#: is not larger, and the bound keeps the value inside int64.
MAX_BYTES_SENT_DIGITS = 15

#: Bound on the ISO-8601 timestamp field. ``2026-08-09T14:03:11+02:00`` is 25.
MAX_TIME_LENGTH = 40

#: Bound on ``$host``. The field is validated but not stored (nothing needs it
#: yet); the bound exists so a huge Host header cannot make a line "valid".
MAX_HOST_LENGTH = 255

#: How many bytes one sweep reads at most. A backlog larger than this is
#: consumed over consecutive sweeps instead of being loaded into memory in one
#: go — the cursor makes that resumption free. 4 MiB is ~20 000 event lines.
MAX_BATCH_BYTES = 4 * 1024 * 1024

#: How many depots ``depot_miss_stats`` keeps. Not a setting: it is a
#: diagnostic table ("what is my LAN pulling that I have no mapping for"), and
#: 500 depots is far more than a homelab ever sees. Least-recently-seen rows
#: are dropped first.
MAX_DEPOT_MISS_ROWS = 500

#: Which URI shapes may fire the miss trigger. See ``_is_chunk_path``.
CHUNK_SEGMENT = "chunk"
DEPOT_SEGMENT = "depot"

#: How many app ids one log line prints before it summarizes.
_LOG_ID_SAMPLE = 20


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class EventLine:
    """One validated ``v1`` event-log line."""

    #: Field 2, normalized to the project's stored UTC format. ``None`` when
    #: nginx's timestamp could not be parsed (the caller substitutes the sweep
    #: time — an unreadable clock stamp must not discard a whole line).
    time_utc: str | None
    addr: str
    cache_status: str
    #: ``None`` for a URI with no depot id (field 5 was ``-``).
    depotid: int | None
    uri: str
    bytes_sent: int
    host: str
    http_status: int

    @property
    def served(self) -> bool:
        """Did this request actually deliver content (2xx, incl. 206)?

        Field 9 exists for exactly this question (core/README.md): field 4
        says HIT/MISS/BYPASS but nothing about whether the response succeeded,
        and field 7 counts an error body's bytes too. Without this filter a
        403 from the Host allowlist, a 404, or a 502 from a dead CDN edge
        would all be counted as served traffic — and a MISS that 502'd would
        "prove" an app is not cached and trigger a prefill, when in truth
        nothing was ever fetched.

        206 is inside 200-299 by construction; it is named in the docs because
        it is the range-request case a reader will look for.
        """
        return 200 <= self.http_status <= 299

    @property
    def is_chunk(self) -> bool:
        """Is this a depot CHUNK request (as opposed to a manifest/patch)?"""
        return _is_chunk_path(self.uri)


def _is_ascii_digits(text: str, max_digits: int) -> bool:
    """Strict numeric field check — ASCII digits only, 1..``max_digits`` of them.

    ``docs/LEARNINGS.md`` house rule: ``int()`` happily accepts ``" 4 "``,
    ``"+4"``, ``"1_0"`` (which is **ten**) and non-ASCII digits such as
    ``"٤"``. These values become SQL parameters, dictionary keys and app ids,
    so every one of those spellings is a line to reject, not to normalize.
    ``isascii()`` closes the Unicode-digit hole that ``re``'s ``\\d`` and
    ``str.isdigit()`` alone leave open; ``isdigit()`` on ASCII text is exactly
    ``[0-9]+``.
    """
    return bool(text) and len(text) <= max_digits and text.isascii() and text.isdigit()


def _is_clean_token(text: str, max_length: int) -> bool:
    """Bounded, printable, whitespace-free ASCII — for the address field.

    The address is a grouping key that ends up in log lines and in JSON
    responses. ``escape=default`` already guarantees the producer only emits
    printable ASCII, so anything else on this line means the file is not what
    it claims to be; rejecting is cheaper and safer than sanitizing.
    """
    if not text or len(text) > max_length:
        return False
    if not text.isascii() or not text.isprintable():
        return False
    return not any(character.isspace() for character in text)


def _is_chunk_path(uri: str) -> bool:
    """Is ``uri`` a ``/depot/<id>/chunk/...`` path?

    **Why the trigger is chunk-only (decision, WP 3.11).** ADR-0001's
    production finding 5 is that manifest URLs carry a per-request code, so
    identical manifests are never URL-deduplicated and *every* manifest request
    is a structural MISS — forever, for every app, no matter how completely it
    is cached. Letting those fire the trigger would mean a perfectly current
    library re-triggering on every launch, with the cooldown as the only thing
    between that and a permanent queue of no-op prefills.

    A chunk MISS carries the opposite information: chunk URLs are content
    addressed and stable, so a miss on one really does mean "this byte range of
    this depot is not on disk". That is the signal ADR-0001's hybrid decision
    is about.

    Manifest and patch misses are still counted in the statistics and in
    ``depot_miss_stats`` — they are dropped from *triggering*, not from
    *observation*.
    """
    parts = uri.split("/")
    # "/depot/70403/chunk/<hash>" -> ['', 'depot', '70403', 'chunk', '<hash>']
    return (
        len(parts) > 4
        and parts[0] == ""
        and parts[1] == DEPOT_SEGMENT
        and parts[3] == CHUNK_SEGMENT
    )


def parse_line(raw: str) -> tuple[EventLine | None, str]:
    """Validate one log line. Returns ``(line, "")`` or ``(None, reason)``.

    Never raises and never partially trusts a line: every field is checked
    before any of them is used. ``reason`` is a short stable token so the sweep
    can count skip causes by category (and so a test can assert on the cause
    rather than on "it was skipped").
    """
    if len(raw) > MAX_LINE_LENGTH:
        return None, "too-long"
    if not raw:
        return None, "empty"

    fields = raw.split("\t")
    if len(fields) != EVENT_FIELD_COUNT:
        return None, "field-count"

    version, time_raw, addr, cache_status, depot_raw, uri, bytes_raw, host, status_raw = (
        fields
    )

    if version != EVENT_LOG_VERSION:
        # Deliberately its own reason, separate from "field-count": a v2 line
        # with 9 fields in a different order is precisely the case that must
        # NOT be read as v1.
        return None, "unknown-version"
    if not _is_clean_token(addr, MAX_ADDR_LENGTH):
        return None, "bad-address"
    if cache_status not in CACHE_STATUSES:
        return None, "bad-cache-status"

    depotid: int | None = None
    if depot_raw != DEPOT_PLACEHOLDER:
        if not _is_ascii_digits(depot_raw, MAX_DEPOT_ID_DIGITS):
            return None, "bad-depot"
        depotid = int(depot_raw)

    if not _is_ascii_digits(bytes_raw, MAX_BYTES_SENT_DIGITS):
        return None, "bad-bytes"
    if not _is_ascii_digits(status_raw, 3) or len(status_raw) != 3:
        return None, "bad-http-status"
    if len(time_raw) > MAX_TIME_LENGTH or len(host) > MAX_HOST_LENGTH:
        return None, "field-too-long"

    return (
        EventLine(
            time_utc=_normalize_time(time_raw),
            addr=addr,
            cache_status=cache_status,
            depotid=depotid,
            uri=uri,
            bytes_sent=int(bytes_raw),
            host=host,
            http_status=int(status_raw),
        ),
        "",
    )


def _normalize_time(raw: str) -> str | None:
    """``$time_iso8601`` -> the project's stored UTC format; ``None`` if unusable.

    ``None`` rather than an exception, and rather than dropping the line: the
    timestamp only decides *when* we say a client was last seen, and the
    caller falls back to the sweep's own clock. Losing a client's whole request
    because nginx's clock stamp was odd would be a much worse trade.
    """
    try:
        moment = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        return None
    return to_utc_iso(moment)


# --------------------------------------------------------------------------
# Reading the file
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ReadBatch:
    """What one bounded read pulled out of the event log."""

    lines: tuple[str, ...] = ()
    #: Offset to commit once the batch's effects are written. Equals the start
    #: offset when nothing complete was read.
    new_cursor: int = 0
    #: The file was smaller than the cursor: rotated or externally truncated.
    rotated: bool = False
    #: The file does not exist (vault-core not up yet, or path misconfigured).
    missing: bool = False
    #: Read failed for another reason; the cursor must not move.
    error: str = ""
    #: Bytes left unconsumed at the end of the read because they were a partial
    #: line. Diagnostic only.
    partial_tail_bytes: int = 0
    #: The read hit MAX_BATCH_BYTES; more is waiting for the next sweep.
    truncated_batch: bool = False
    #: Bytes discarded because a single "line" exceeded a whole batch — see
    #: ``_skip_oversized``. 0 in every normal sweep.
    oversized_skipped_bytes: int = 0
    #: An oversized region runs to EOF with no newline anywhere, so it cannot
    #: be skipped yet without consuming an unterminated line. The cursor stays
    #: put and the sweep says so LOUDLY (the one case that can still stall).
    oversized_stalled: bool = False
    file_size: int = 0


class UnsafeEventLogError(OSError):
    """The event-log path is not a regular file on the log directory's volume.

    Threat model §9 (WP SEC-FIX-4, S-1): vault-core shares ``/vault`` with
    vault-api and runs as the same uid, so code execution in vault-core can
    replace ``event.log`` with a symlink or a FIFO. vault-api resolves
    paths in ITS OWN mount namespace, where ``/data`` and the runner home are
    reachable, so following such a link would read — and on rotation
    truncate — a file vault-core could never touch itself.
    """


def open_event_log(path: str, flags: int) -> int:
    """Open the event log without following a planted link. Returns an fd.

    * ``O_NOFOLLOW`` refuses a symlink in the LAST component (ELOOP);
    * ``O_NONBLOCK`` keeps a planted FIFO from hanging the sweep in open();
    * the log's own directory (``logs/``) must not be a symlink
      (``lstat`` + ``S_ISLNK``). The st_dev check below cannot catch a link
      to a same-device target, and the default compose puts every named
      volume on one device. Only ``logs/`` itself is checked, not a full
      ``realpath`` comparison: ancestors above it may legitimately be links
      or bind mounts (a linked cache base);
    * the fd must be ``S_ISREG`` and live on the same device as the log
      directory (``lstat``, so a ``logs/`` mount pointing at another volume
      is refused too).

    Raises ``UnsafeEventLogError`` (an ``OSError``) on refusal, and lets the
    ordinary ``OSError`` family (missing file, EACCES) through unchanged.
    """
    try:
        fd = os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise UnsafeEventLogError(
                errno.ELOOP, "refusing a symlinked event log", path
            ) from exc
        raise
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise UnsafeEventLogError(
                errno.EINVAL, "refusing an event log that is not a regular file", path
            )
        parent = os.path.dirname(path) or "."
        parent_info = os.lstat(parent)
        if stat.S_ISLNK(parent_info.st_mode):
            raise UnsafeEventLogError(
                errno.ELOOP, "refusing an event log in a symlinked directory", path
            )
        if info.st_dev != parent_info.st_dev:
            raise UnsafeEventLogError(
                errno.EXDEV,
                "refusing an event log on another device than its directory",
                path,
            )
    except BaseException:
        os.close(fd)
        raise
    return fd


def read_batch(path: str, cursor: int) -> ReadBatch:
    """Read complete lines from ``cursor`` onward. Never raises.

    Binary mode on purpose. The cursor is a **byte** offset, and decoding
    before splitting would make "how many bytes did I consume" depend on the
    codec — an off-by-N cursor on any non-ASCII byte. So the split happens on
    bytes, the cursor is computed on bytes, and only the resulting complete
    lines are decoded (with ``errors="replace"``, because a decode failure must
    degrade one line's text, never abort a sweep).
    """
    try:
        fd = open_event_log(path, os.O_RDONLY)
    except FileNotFoundError:
        return ReadBatch(new_cursor=cursor, missing=True)
    except OSError as exc:
        return ReadBatch(new_cursor=cursor, error=str(exc))
    try:
        size = os.fstat(fd).st_size
    except OSError as exc:
        os.close(fd)
        return ReadBatch(new_cursor=cursor, error=str(exc))

    start = cursor
    rotated = False
    if size < cursor:
        # Rule 3: the file shrank. Either somebody rotated it out from under
        # us or vault-core was redeployed onto a fresh volume. Seeking to the
        # old offset would skip everything the new file has accumulated so far,
        # silently, so restart at the beginning.
        start = 0
        rotated = True

    try:
        # The fd is the one that passed open_event_log's checks; reading it
        # (never re-opening the path) is what keeps a symlink swapped in after
        # the check from being read through.
        with os.fdopen(fd, "rb") as handle:
            handle.seek(start)
            chunk = handle.read(MAX_BATCH_BYTES)
    except OSError as exc:
        return ReadBatch(new_cursor=cursor, rotated=rotated, error=str(exc))

    truncated_batch = len(chunk) == MAX_BATCH_BYTES
    end = chunk.rfind(b"\n")
    if end == -1:
        if not truncated_batch:
            # Rule 1: not one complete line, and the file ends here. Consume
            # nothing — not even if the partial text looks whole — and re-read
            # it next sweep once nginx has flushed its newline.
            return ReadBatch(
                new_cursor=start,
                rotated=rotated,
                partial_tail_bytes=len(chunk),
                truncated_batch=False,
                file_size=size,
            )
        # A FULL batch with no newline anywhere in it is NOT a partial tail:
        # there is no amount of waiting that turns 4 MiB of newline-free bytes
        # into a line this parser would accept (MAX_LINE_LENGTH is 8 KiB).
        # Treating it as a tail is what made this a silent, PERMANENT stall —
        # every sweep re-read the same bytes, consumed nothing, statistics
        # stopped, bypass detection went blind, and rotation could never fire
        # because the file was never fully swept.
        return _skip_oversized(path, start, len(chunk), rotated, size)

    complete = chunk[: end + 1]
    text = complete.decode("utf-8", errors="replace")
    lines = tuple(line for line in text.split("\n") if line != "")

    return ReadBatch(
        lines=lines,
        new_cursor=start + len(complete),
        rotated=rotated,
        partial_tail_bytes=len(chunk) - len(complete),
        truncated_batch=truncated_batch,
        file_size=size,
    )


def _skip_oversized(
    path: str, start: int, batch_len: int, rotated: bool, size: int
) -> ReadBatch:
    """Step over a region whose "line" is longer than a whole batch.

    Called only when a FULL ``MAX_BATCH_BYTES`` read contained no newline at
    all. Scans forward from the end of that batch for the next ``\\n`` and
    resumes just past it, discarding everything in between.

    **The cursor still only ever advances to just past a newline.** That
    invariant is what keeps the skip safe: we never consume an unterminated
    line, so a genuinely huge line that nginx is still writing is not cut in
    half and re-parsed as two bogus records. The consequence is the one case
    that can still stall — an oversized region running to EOF with no newline
    anywhere — and it is reported as ``oversized_stalled`` and warned about on
    every sweep rather than being mistaken for progress. It resolves by itself
    the moment a newline appears; if it never does, the file is not being
    written by nginx's event log at all, which is what the warning says.

    The scan is bounded by the file itself (chunked reads to EOF, worst case
    one pass) and this whole path is unreachable in normal operation: nginx
    bounds the URI field to 300 characters, so a real event line is well under
    500 bytes.
    """
    scan_from = start + batch_len
    try:
        with os.fdopen(open_event_log(path, os.O_RDONLY), "rb") as handle:
            handle.seek(scan_from)
            offset = scan_from
            while True:
                chunk = handle.read(MAX_BATCH_BYTES)
                if not chunk:
                    break
                index = chunk.find(b"\n")
                if index != -1:
                    new_cursor = offset + index + 1
                    return ReadBatch(
                        new_cursor=new_cursor,
                        rotated=rotated,
                        oversized_skipped_bytes=new_cursor - start,
                        truncated_batch=True,
                        file_size=size,
                    )
                offset += len(chunk)
    except OSError as exc:
        return ReadBatch(new_cursor=start, rotated=rotated, error=str(exc))

    return ReadBatch(
        new_cursor=start,
        rotated=rotated,
        oversized_stalled=True,
        partial_tail_bytes=batch_len,
        truncated_batch=True,
        file_size=size,
    )


# --------------------------------------------------------------------------
# event_sweep_state (schema v9)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SweepState:
    """The single ``event_sweep_state`` row."""

    cursor_offset: int = 0
    first_sweep_at: str | None = None
    last_sweep_at: str | None = None
    last_rotated_at: str | None = None
    lines_read_total: int = 0
    lines_skipped_total: int = 0
    last_lines: int | None = None
    last_skipped: int | None = None
    last_enqueued: int | None = None
    last_dropped_by_cap: int | None = None
    #: How often rotation was refused by the filesystem — see
    #: ``maybe_truncate``. Non-zero means the log is growing without bound.
    truncate_denied_count: int = 0
    last_truncate_denied_at: str | None = None
    #: How often an oversized (newline-free) region had to be stepped over.
    #: Non-zero means the event log contains something nginx did not write.
    oversized_skips_total: int = 0
    last_oversized_at: str | None = None


#: What ``read_state`` returns before the first sweep has ever claimed.
EMPTY_STATE = SweepState()


def read_state(conn: sqlite3.Connection) -> SweepState:
    """Read the single ``event_sweep_state`` row (``EMPTY_STATE`` if absent)."""
    row = conn.execute(
        """
        SELECT cursor_offset, first_sweep_at, last_sweep_at, last_rotated_at,
               lines_read_total, lines_skipped_total, last_lines, last_skipped,
               last_enqueued, last_dropped_by_cap, truncate_denied_count,
               last_truncate_denied_at, oversized_skips_total, last_oversized_at
        FROM event_sweep_state WHERE id = 1
        """
    ).fetchone()
    if row is None:
        return EMPTY_STATE
    return SweepState(
        cursor_offset=int(row["cursor_offset"]),
        first_sweep_at=row["first_sweep_at"],
        last_sweep_at=row["last_sweep_at"],
        last_rotated_at=row["last_rotated_at"],
        lines_read_total=int(row["lines_read_total"]),
        lines_skipped_total=int(row["lines_skipped_total"]),
        last_lines=row["last_lines"],
        last_skipped=row["last_skipped"],
        last_enqueued=row["last_enqueued"],
        last_dropped_by_cap=row["last_dropped_by_cap"],
        truncate_denied_count=int(row["truncate_denied_count"]),
        last_truncate_denied_at=row["last_truncate_denied_at"],
        oversized_skips_total=int(row["oversized_skips_total"]),
        last_oversized_at=row["last_oversized_at"],
    )


def interval_elapsed(state: SweepState, now: datetime, interval_minutes: int) -> bool:
    """Has ``interval_minutes`` passed since the last sweep started?

    Same shape and the same fail direction as ``scheduler.interval_elapsed``: a
    never-swept or unreadable timestamp means "yes, sweep" (an unreadable value
    must not disable the feature permanently), while a timestamp in the
    *future* means "no" until real time catches up — skipping sweeps is quiet
    and recoverable, whereas treating a future stamp as "long ago" would sweep
    on every tick.
    """
    if state.last_sweep_at is None:
        return True
    last = parse_utc_iso(state.last_sweep_at)
    if last is None:
        logger.warning(
            "event-sweep: event_sweep_state.last_sweep_at is %r, which is not a "
            "'YYYY-MM-DDTHH:MM:SSZ' timestamp; treating it as 'never swept' "
            "(the next sweep replaces it)",
            state.last_sweep_at,
        )
        return True
    return now.astimezone(timezone.utc) >= last + timedelta(minutes=interval_minutes)


def claim_sweep(conn: sqlite3.Connection, now: datetime, interval_minutes: int) -> bool:
    """Re-check the interval and stamp ``last_sweep_at`` atomically.

    Claim-then-work, the same ``BEGIN IMMEDIATE`` shape as
    ``scheduler.claim_sweep`` and for the same check-then-act reason
    (docs/LEARNINGS.md). Unlike the prefill scheduler, claiming before the work
    costs nothing here even if the sweep then fails: the *data* safety net is
    the cursor, not the claim, so a failed sweep re-reads its batch one
    interval later with nothing lost.

    ``first_sweep_at`` is set on the first claim only (``COALESCE``) — bypass
    detection needs to know how long the feed has been observed, and that
    question must not be re-answered by every subsequent sweep.
    """
    if not interval_elapsed(read_state(conn), now, interval_minutes):
        return False

    now_iso = to_utc_iso(now)
    with immediate_transaction(conn):
        # Re-read under the write lock: the cheap pre-check above exists only
        # to avoid taking that lock on the ticks that decide "not yet".
        if not interval_elapsed(read_state(conn), now, interval_minutes):
            return False
        conn.execute(
            """
            INSERT INTO event_sweep_state (id, first_sweep_at, last_sweep_at)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                last_sweep_at = excluded.last_sweep_at,
                first_sweep_at = COALESCE(
                    event_sweep_state.first_sweep_at, excluded.first_sweep_at
                )
            """,
            (now_iso, now_iso),
        )
    return True


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------


@dataclass
class AddrStats:
    """Mutable per-address accumulator for one sweep window."""

    requests: int = 0
    hits: int = 0
    misses: int = 0
    bypasses: int = 0
    errors: int = 0
    bytes_served: int = 0
    last_seen: str = ""


@dataclass
class DepotMiss:
    """Mutable per-depot miss accumulator for one sweep window."""

    miss_count: int = 0
    mapped: bool = False
    last_seen: str = ""


@dataclass
class Aggregate:
    """Everything one batch of lines adds up to."""

    addrs: dict[str, AddrStats] = field(default_factory=dict)
    depot_misses: dict[int, DepotMiss] = field(default_factory=dict)
    #: Candidate app ids for the miss trigger, in first-seen order.
    candidates: list[int] = field(default_factory=list)
    parsed: int = 0
    skipped: int = 0
    skip_reasons: dict[str, int] = field(default_factory=dict)
    #: Version strings seen in field 1 that were not ``v1``.
    unknown_versions: int = 0


def aggregate_lines(
    conn: sqlite3.Connection,
    lines: tuple[str, ...],
    fallback_time: str,
) -> Aggregate:
    """Parse and fold a batch of raw lines into one ``Aggregate``.

    Reads ``depot_app_map`` per distinct MISS depot (cached in a local dict, so
    a 20 000-line batch of one game's chunks costs one query, not 20 000).
    Writes nothing.
    """
    result = Aggregate()
    mapping_cache: dict[int, list[int]] = {}
    seen_candidates: set[int] = set()

    for raw in lines:
        line, reason = parse_line(raw)
        if line is None:
            result.skipped += 1
            result.skip_reasons[reason] = result.skip_reasons.get(reason, 0) + 1
            if reason == "unknown-version":
                result.unknown_versions += 1
            continue

        result.parsed += 1
        seen_at = line.time_utc or fallback_time

        stats = result.addrs.get(line.addr)
        if stats is None:
            stats = result.addrs[line.addr] = AddrStats()
        stats.requests += 1
        if seen_at > stats.last_seen:
            stats.last_seen = seen_at

        if not line.served:
            # A 403/404/502 is traffic, but it is not *served* traffic. It
            # counts as a request and as an error, and contributes nothing to
            # hit rate, byte totals, miss statistics or the trigger.
            stats.errors += 1
            continue

        stats.bytes_served += line.bytes_sent
        if line.cache_status == STATUS_HIT:
            stats.hits += 1
            continue
        if line.cache_status == STATUS_BYPASS:
            stats.bypasses += 1
            continue

        stats.misses += 1
        if line.depotid is None:
            continue

        appids = mapping_cache.get(line.depotid)
        if appids is None:
            appids = mapping_cache[line.depotid] = _appids_for_depot(conn, line.depotid)

        depot = result.depot_misses.get(line.depotid)
        if depot is None:
            depot = result.depot_misses[line.depotid] = DepotMiss()
        depot.miss_count += 1
        depot.mapped = bool(appids)
        if seen_at > depot.last_seen:
            depot.last_seen = seen_at

        if len(appids) != 1:
            # Zero mappings: ADR-0008's "no mapping = no honest target" — the
            # miss is counted above and triggers nothing.
            #
            # More than one: the same rule from the other side. A shared depot
            # (redistributables, plan §4) maps to every app that pulled it, so
            # one chunk miss cannot say WHICH game is being downloaded.
            # Enqueueing a prefill for all of them would turn a single miss on
            # a common depot into N jobs — the exact storm the ADR's dedupe and
            # cooldown language exists to prevent — and picking one arbitrarily
            # would be a guess. Counted, never triggered.
            continue
        if not line.is_chunk:
            # Manifest/patch misses are structural, not evidence — see
            # `_is_chunk_path`.
            continue

        appid = appids[0]
        if appid not in seen_candidates:
            seen_candidates.add(appid)
            result.candidates.append(appid)

    return result


def _appids_for_depot(conn: sqlite3.Connection, depotid: int) -> list[int]:
    """Every app this depot is mapped to (plan §4: a depot can map to many).

    Rows are coerced with ``deletion.coerce_positive_id`` and unusable ones
    are skipped, never raised on (WP API-FIX-2, data-path P1). SQLite's
    INTEGER affinity does not enforce the column type, and a hand-edited or
    corrupted ``depot_app_map`` row with a non-numeric ``appid`` used to
    raise ``ValueError`` out of ``aggregate_lines`` -- before ``commit_batch``
    ever ran, so the cursor never advanced and every later sweep re-read the
    same batch and failed the same way, forever. A broken mapping row is the
    same "no honest target" case as no row at all; the sweep logs it and
    carries on.
    """
    rows = conn.execute(
        "SELECT appid FROM depot_app_map WHERE depotid = ? ORDER BY appid",
        (depotid,),
    ).fetchall()
    appids: list[int] = []
    for row in rows:
        appid = deletion.coerce_positive_id(row["appid"])
        if appid is None:
            logger.warning(
                "event-sweep: depot_app_map row for depot %d has an unusable "
                "appid %r; ignoring that row (fix or delete it -- it can never "
                "be a prefill target).",
                depotid,
                row["appid"],
            )
            continue
        appids.append(appid)
    return appids


# --------------------------------------------------------------------------
# The miss trigger (ADR-0001's hybrid decision, ADR-0008's rules)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TriggerResult:
    """What the trigger did with one sweep's candidates."""

    enqueued: tuple[int, ...] = ()
    #: Candidates refused because the per-sweep cap was already reached.
    dropped_by_cap: tuple[int, ...] = ()
    skipped_cooldown: tuple[int, ...] = ()
    skipped_active: tuple[int, ...] = ()
    skipped_current: tuple[int, ...] = ()
    #: WP API-FIX-4: Steam tool apps (``tool_apps.TOOL_APPS``) among the
    #: candidates. Never enqueued; they do not count against the cap.
    skipped_tool: tuple[int, ...] = ()


def in_cooldown(conn: sqlite3.Connection, appid: int, cutoff_iso: str) -> bool:
    """Has this app been miss-triggered since ``cutoff_iso``?"""
    row = conn.execute(
        "SELECT last_triggered_at FROM miss_trigger_state WHERE appid = ?",
        (appid,),
    ).fetchone()
    if row is None:
        return False
    return str(row["last_triggered_at"]) >= cutoff_iso


def record_trigger(conn: sqlite3.Connection, appid: int, now_iso: str) -> None:
    """Start this app's cooldown. Own committed transaction, right after the
    enqueue it describes — see the ``miss_trigger_state`` DDL for why that
    ordering (and not the reverse) is the safe one.
    """
    with immediate_transaction(conn):
        conn.execute(
            """
            INSERT INTO miss_trigger_state (appid, last_triggered_at, trigger_count)
            VALUES (?, ?, 1)
            ON CONFLICT(appid) DO UPDATE SET
                last_triggered_at = excluded.last_triggered_at,
                trigger_count = miss_trigger_state.trigger_count + 1
            """,
            (appid, now_iso),
        )


def is_cached_and_current(
    conn: sqlite3.Connection, appid: int, cutoff_iso: str
) -> bool:
    """ADR-0008's "not currently cached-and-current" gate, spelled out.

    True only when all three of vault-api's own beliefs line up:

    * ``apps.status == 'done'`` — the last job for it succeeded;
    * ``apps.needs_force == 0`` — nothing has invalidated the cache since
      (a deletion sets it back to 1, ADR-0006 decision 2);
    * ``apps.last_manifest_check`` is not older than ``cutoff_iso`` — a run
      confirmed the app up to date recently.

    The freshness bound is deliberately **the cooldown window**, not a fourth
    setting: "how recently must we have confirmed this app to ignore a miss
    for it" and "how often may one app be re-triggered" are the same operator
    question asked twice, and a second knob would only create combinations
    where one silently overrides the other.

    Anything unknown reads as NOT current — an app with no row, a NULL
    ``last_manifest_check``, or an unparseable one. That direction is the safe
    one here: the cost of being wrong is one non-forced prefill, which
    ADR-0006 measures at ~3 s for an app that really is current, whereas the
    opposite error leaves a half-cached game nobody completes.
    """
    row = conn.execute(
        "SELECT status, needs_force, last_manifest_check FROM apps WHERE appid = ?",
        (appid,),
    ).fetchone()
    if row is None:
        return False
    if str(row["status"]) != "done" or bool(row["needs_force"]):
        return False
    checked = row["last_manifest_check"]
    if not isinstance(checked, str):
        return False
    return checked >= cutoff_iso


def run_miss_trigger(
    conn: sqlite3.Connection,
    settings: Settings,
    candidates: list[int],
    now: datetime,
) -> TriggerResult:
    """Enqueue non-forced prefills for miss candidates, under four guards.

    Before the guards, a Steam tool app (``tool_apps.TOOL_APPS``, WP
    API-FIX-4) is skipped outright and reported in ``skipped_tool``: it is
    never prefilled, so it must not use up the per-sweep cap either. A miss
    on its depot is a miss on a depot that the games using it fill.

    In order, cheapest and most decisive first:

    1. **The per-sweep cap** (``VAULT_MISS_TRIGGER_MAX_PER_SWEEP``). Checked
       first so a pathological batch costs one comparison per candidate rather
       than three queries each. Everything past the cap is reported by app id —
       never silently dropped (docs/LEARNINGS.md).
    2. **The queue's own per-app dedupe**, via ``jobs.active_job_for_app`` and
       ``ACTIVE_STATUSES`` — which includes ``paused`` (WP 3.12), so a job an
       operator deliberately suspended is not quietly replaced by a new one.
    3. **The per-app cooldown** (``VAULT_MISS_TRIGGER_COOLDOWN_MINUTES``).
    4. **cached-and-current**, see ``is_cached_and_current``.

    Every enqueue is NON-FORCED (``jobs.enqueue_prefill`` never passes
    ``--force``; whether a given run forces is ``apps.needs_force``'s decision,
    ADR-0006 decision 2). A miss says "something is not on disk", which is an
    argument for filling, never for re-downloading what is.
    """
    if not settings.miss_trigger_enabled:
        return TriggerResult()

    now_iso = to_utc_iso(now)
    cutoff_iso = to_utc_iso(
        now - timedelta(minutes=settings.miss_trigger_cooldown_minutes)
    )

    enqueued: list[int] = []
    dropped: list[int] = []
    skipped_cooldown: list[int] = []
    skipped_active: list[int] = []
    skipped_current: list[int] = []
    skipped_tool: list[int] = []
    triggered = 0

    for appid in candidates:
        if tool_apps.is_tool_app(appid):
            skipped_tool.append(appid)
            continue
        if triggered >= settings.miss_trigger_max_per_sweep:
            dropped.append(appid)
            continue
        if jobs.active_job_for_app(conn, appid) is not None:
            skipped_active.append(appid)
            continue
        if in_cooldown(conn, appid, cutoff_iso):
            skipped_cooldown.append(appid)
            continue
        if is_cached_and_current(conn, appid, cutoff_iso):
            skipped_current.append(appid)
            continue

        triggered += 1
        _job, created = jobs.enqueue_prefill(conn, appid)
        # Cooldown first-class, committed immediately after the enqueue: see
        # the module docstring's idempotence section. Recorded even when the
        # enqueue folded into an existing job (a race with guard 2), because
        # the app demonstrably has a job either way.
        record_trigger(conn, appid, now_iso)
        if created:
            enqueued.append(appid)
        else:  # pragma: no cover - needs a job created between guard 2 and here
            skipped_active.append(appid)

    if skipped_tool:
        # WP API-FIX-4: say why a missed tool app got no job, so "why is
        # 228980 never prefilled" is answered by the log.
        logger.info(
            "event-sweep: miss trigger skipped %d Steam tool app(s), never "
            "prefilled, cached together with the games that use them: %s",
            len(skipped_tool),
            _sample(skipped_tool),
        )

    if dropped:
        logger.warning(
            "event-sweep: miss trigger hit its per-sweep cap of %d; %d further "
            "app(s) were NOT enqueued this sweep: %s. They are re-considered "
            "on the next sweep if they miss again. Raise "
            "VAULT_MISS_TRIGGER_MAX_PER_SWEEP if this is routine.",
            settings.miss_trigger_max_per_sweep,
            len(dropped),
            _sample(dropped),
        )

    return TriggerResult(
        enqueued=tuple(enqueued),
        dropped_by_cap=tuple(dropped),
        skipped_cooldown=tuple(skipped_cooldown),
        skipped_active=tuple(skipped_active),
        skipped_current=tuple(skipped_current),
        skipped_tool=tuple(skipped_tool),
    )


def _sample(ids: list[int]) -> str:
    """Render an id list for a log line without dumping hundreds of numbers."""
    if len(ids) <= _LOG_ID_SAMPLE:
        return str(ids)
    return f"{ids[:_LOG_ID_SAMPLE]} (+{len(ids) - _LOG_ID_SAMPLE} more)"


# --------------------------------------------------------------------------
# Committing a batch
# --------------------------------------------------------------------------


def commit_batch(
    conn: sqlite3.Connection,
    settings: Settings,
    aggregate: Aggregate,
    batch: ReadBatch,
    trigger: TriggerResult,
    now_iso: str,
) -> None:
    """Write the batch's statistics AND advance the cursor, atomically.

    One ``BEGIN IMMEDIATE`` transaction, and that is the whole idempotence
    story for statistics (module docstring): there is no window in which the
    counters are committed but the cursor is not, so a re-read batch can never
    be counted twice.
    """
    with immediate_transaction(conn):
        for addr, stats in aggregate.addrs.items():
            conn.execute(
                """
                INSERT INTO client_cache_stats (
                    client_addr, window_at, requests, hits, misses, bypasses,
                    errors, bytes_served, last_seen
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(client_addr, window_at) DO UPDATE SET
                    requests     = client_cache_stats.requests + excluded.requests,
                    hits         = client_cache_stats.hits + excluded.hits,
                    misses       = client_cache_stats.misses + excluded.misses,
                    bypasses     = client_cache_stats.bypasses + excluded.bypasses,
                    errors       = client_cache_stats.errors + excluded.errors,
                    bytes_served = client_cache_stats.bytes_served
                                   + excluded.bytes_served,
                    last_seen    = MAX(client_cache_stats.last_seen,
                                       excluded.last_seen)
                """,
                (
                    addr,
                    now_iso,
                    stats.requests,
                    stats.hits,
                    stats.misses,
                    stats.bypasses,
                    stats.errors,
                    stats.bytes_served,
                    stats.last_seen or now_iso,
                ),
            )
            _prune_client_stats(conn, addr, settings.client_stats_keep)

        for depotid, depot in aggregate.depot_misses.items():
            conn.execute(
                """
                INSERT INTO depot_miss_stats (
                    depotid, miss_count, mapped, first_seen, last_seen
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(depotid) DO UPDATE SET
                    miss_count = depot_miss_stats.miss_count + excluded.miss_count,
                    mapped     = excluded.mapped,
                    last_seen  = MAX(depot_miss_stats.last_seen, excluded.last_seen)
                """,
                (
                    depotid,
                    depot.miss_count,
                    int(depot.mapped),
                    depot.last_seen or now_iso,
                    depot.last_seen or now_iso,
                ),
            )
        _prune_depot_misses(conn)

        conn.execute(
            """
            INSERT INTO event_sweep_state (
                id, cursor_offset, first_sweep_at, last_sweep_at,
                lines_read_total, lines_skipped_total,
                last_lines, last_skipped, last_enqueued, last_dropped_by_cap,
                oversized_skips_total, last_oversized_at
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                cursor_offset       = excluded.cursor_offset,
                lines_read_total    = event_sweep_state.lines_read_total
                                      + excluded.lines_read_total,
                lines_skipped_total = event_sweep_state.lines_skipped_total
                                      + excluded.lines_skipped_total,
                last_lines          = excluded.last_lines,
                last_skipped        = excluded.last_skipped,
                last_enqueued       = excluded.last_enqueued,
                last_dropped_by_cap = excluded.last_dropped_by_cap,
                oversized_skips_total = event_sweep_state.oversized_skips_total
                                        + excluded.oversized_skips_total,
                last_oversized_at   = COALESCE(
                    excluded.last_oversized_at, event_sweep_state.last_oversized_at
                )
            """,
            (
                batch.new_cursor,
                now_iso,
                now_iso,
                aggregate.parsed,
                aggregate.skipped,
                aggregate.parsed,
                aggregate.skipped,
                len(trigger.enqueued),
                len(trigger.dropped_by_cap),
                # Counted in the SAME transaction as the cursor that skipped
                # past the region, so the counter and the offset can never
                # disagree about whether the skip happened.
                1 if batch.oversized_skipped_bytes else 0,
                now_iso if batch.oversized_skipped_bytes else None,
            ),
        )


def _prune_client_stats(conn: sqlite3.Connection, addr: str, keep: int) -> int:
    """Keep only this address's ``keep`` newest windows. Returns rows removed.

    Same retention shape as ``agent_reports.prune_reports`` (WP 2.4), run
    inside the writing transaction for the same reason: an address that is
    active all day would otherwise add a row every sweep, forever.
    """
    cursor = conn.execute(
        """
        DELETE FROM client_cache_stats
        WHERE client_addr = ?
          AND window_at NOT IN (
              SELECT window_at FROM client_cache_stats
              WHERE client_addr = ?
              ORDER BY window_at DESC
              LIMIT ?
          )
        """,
        (addr, addr, max(1, int(keep))),
    )
    return int(cursor.rowcount or 0)


def _prune_depot_misses(conn: sqlite3.Connection) -> int:
    """Bound ``depot_miss_stats`` to the ``MAX_DEPOT_MISS_ROWS`` newest depots."""
    row = conn.execute("SELECT COUNT(*) AS n FROM depot_miss_stats").fetchone()
    if row is None or int(row["n"]) <= MAX_DEPOT_MISS_ROWS:
        return 0
    cursor = conn.execute(
        """
        DELETE FROM depot_miss_stats
        WHERE depotid NOT IN (
            SELECT depotid FROM depot_miss_stats
            ORDER BY last_seen DESC, depotid DESC
            LIMIT ?
        )
        """,
        (MAX_DEPOT_MISS_ROWS,),
    )
    return int(cursor.rowcount or 0)


# --------------------------------------------------------------------------
# Rotation
# --------------------------------------------------------------------------


#: ``TruncateResult.reason`` values.
TRUNCATE_DONE = "truncated"
TRUNCATE_DISABLED = "disabled"
TRUNCATE_NOT_DUE = "not-due"
TRUNCATE_INCOMPLETE = "not-fully-swept"
TRUNCATE_DENIED = "permission-denied"
TRUNCATE_REFUSED = "unsafe-path-refused"
TRUNCATE_FAILED = "failed"


@dataclass(frozen=True)
class TruncateResult:
    """What ``maybe_truncate`` did, and why."""

    truncated: bool = False
    #: The sweeper may read the log but not write it — the container case.
    denied: bool = False
    reason: str = TRUNCATE_NOT_DUE


def maybe_truncate(
    conn: sqlite3.Connection, settings: Settings, cursor: int, now_iso: str
) -> TruncateResult:
    """Truncate the event log to zero and reset the cursor, if it is safe to.

    Every condition and the residual race are documented in the module
    docstring. **Never raises, and never blocks a sweep** — see below.

    Rotation is BEST-EFFORT, because a deployment can deny it
    -------------------------------------------------------------------------
    ADR-0008 assigns rotation to this sweeper. In the shipped stack it works:
    vault-api runs as uid/gid ``101:101`` (``api/Dockerfile``), and since WP
    SEC-FIX-5 vault-core keeps ``/vault/logs`` root-only but the event log
    itself ``101:101`` (core/README.md "Volume ownership"), so the write
    ``open`` below succeeds. Where it does not (a differently-owned
    vault-core, the event log ``0644`` and owned by someone else), the
    sweeper can open it for reading but not for writing — the write ``open``
    raises ``PermissionError`` (EPERM/EACCES).

    Since WP SEC-FIX-4 both opens go through ``open_event_log`` (no symlink,
    regular file, same device as the log directory) and rotation is an
    ``os.ftruncate`` on that verified fd, never a path-based
    ``os.truncate``. A refused path is reported like a denial — WARNING plus
    the persisted counter — and never breaks the sweep.

    That asymmetry is a *fail-soft* condition, deliberately, and it is worth
    being precise about why it is safe:

    * **Correctness does not depend on truncation.** The cursor is what makes
      each line be read exactly once, and it has already been committed by
      ``commit_batch`` before this function is reached. A denied truncation
      leaves the cursor exactly where it was, sweeping continues normally on
      the next tick, and no line is re-read or lost.
    * **What it does cost is unbounded file growth**, which is a real
      operational problem and therefore must be *visible* rather than silently
      swallowed: every denial logs a WARNING naming the one-line fix, and
      increments a persisted counter that ``GET /v1/stats`` reports. An
      operator who never reads the log still sees ``truncate_denied_count``
      climbing.

    The fix is a permission change on the vault-core side (make
    ``/vault/logs/event.log`` writable by vault-api's uid — chown, or a shared
    group with ``0664``); the shipped vault-core does exactly that since WP
    SEC-FIX-5. This module's job is to work correctly either way and to say
    clearly which way it is running.

    A native (non-container) install — the WP 1.7 MVP setup, or a dev machine
    where both processes run as the same user — hits neither of these problems
    and truncates normally.
    """
    limit = settings.event_log_max_bytes
    if limit <= 0:
        return TruncateResult(reason=TRUNCATE_DISABLED)
    if cursor <= 0:
        return TruncateResult(reason=TRUNCATE_NOT_DUE)

    path = settings.event_log_path
    try:
        read_fd = open_event_log(path, os.O_RDONLY)
    except UnsafeEventLogError as exc:
        return _refuse_truncate(conn, path, exc, now_iso)
    except OSError:
        return TruncateResult(reason=TRUNCATE_NOT_DUE)
    try:
        size = os.fstat(read_fd).st_size
    finally:
        os.close(read_fd)

    if size < limit:
        return TruncateResult(reason=TRUNCATE_NOT_DUE)
    if size != cursor:
        # nginx appended between the read and now, so part of the file has NOT
        # been swept. Truncating would destroy it. Wait for a sweep that ends
        # with the cursor at EOF.
        return TruncateResult(reason=TRUNCATE_INCOMPLETE)

    try:
        if not _ftruncate_if_unchanged(path, cursor):
            return TruncateResult(reason=TRUNCATE_INCOMPLETE)
    except UnsafeEventLogError as exc:
        return _refuse_truncate(conn, path, exc, now_iso)
    except PermissionError as exc:
        _record_truncate_denied(conn, now_iso)
        state = read_state(conn)
        logger.warning(
            "event-sweep: NOT PERMITTED to truncate %r (%s). The event log is "
            "%d bytes and fully swept, but vault-api may only READ it -- in the "
            "shipped containers vault-api runs as uid 101 while the log belongs "
            "to vault-core's nginx user. Sweeping is UNAFFECTED and nothing is "
            "lost (the cursor is committed independently); the file will keep "
            "growing until vault-api can write it. Fix it on the vault-core "
            "side, e.g. 'chown 101:101 %s' or make it group-writable (0664) "
            "with a shared group. Denied %d time(s) so far; set "
            "VAULT_EVENT_LOG_MAX_BYTES=0 to stop attempting rotation and "
            "rotate the file externally instead.",
            path,
            exc,
            size,
            path,
            state.truncate_denied_count,
        )
        return TruncateResult(denied=True, reason=TRUNCATE_DENIED)
    except OSError as exc:
        logger.warning(
            "event-sweep: could not truncate %r after a successful sweep (%s). "
            "The cursor is unchanged, so nothing is lost; the file keeps "
            "growing until this is fixed.",
            path,
            exc,
        )
        return TruncateResult(reason=TRUNCATE_FAILED)

    with immediate_transaction(conn):
        conn.execute(
            "UPDATE event_sweep_state SET cursor_offset = 0, last_rotated_at = ? "
            "WHERE id = 1",
            (now_iso,),
        )
    logger.info(
        "event-sweep: truncated %r at %d bytes (fully swept) and reset the "
        "cursor to 0.",
        path,
        size,
    )
    return TruncateResult(truncated=True, reason=TRUNCATE_DONE)


def _ftruncate_if_unchanged(path: str, cursor: int) -> bool:
    """Truncate via a verified write fd; False if the size moved meanwhile.

    The size is re-checked on the SAME fd that is truncated: nginx may have
    appended (or the file been swapped) since the read-side fstat.
    """
    fd = open_event_log(path, os.O_WRONLY)
    try:
        if os.fstat(fd).st_size != cursor:
            return False
        os.ftruncate(fd, 0)
        return True
    finally:
        os.close(fd)


def _refuse_truncate(
    conn: sqlite3.Connection, path: str, exc: OSError, now_iso: str
) -> TruncateResult:
    """An unsafe event-log path (S-1): loud, counted, never a crash."""
    _record_truncate_denied(conn, now_iso)
    logger.warning(
        "event-sweep: REFUSING to rotate %r (%s). The path is a symlink, not a "
        "regular file, or on another device than its directory -- something "
        "on the shared /vault volume replaced the event log. Nothing was read "
        "through it or truncated. Inspect /vault/logs on the vault-core side.",
        path,
        exc,
    )
    return TruncateResult(denied=True, reason=TRUNCATE_REFUSED)


def _record_truncate_denied(conn: sqlite3.Connection, now_iso: str) -> None:
    """Persist the denial so ``GET /v1/stats`` can show it (see above)."""
    with immediate_transaction(conn):
        conn.execute(
            """
            UPDATE event_sweep_state
            SET truncate_denied_count = truncate_denied_count + 1,
                last_truncate_denied_at = ?
            WHERE id = 1
            """,
            (now_iso,),
        )


# --------------------------------------------------------------------------
# The sweep
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SweepOutcome:
    """Outcome of one tick."""

    swept: bool
    #: Why not, when ``swept`` is False: ``"disabled"``,
    #: ``"interval-not-elapsed"``, ``"log-missing"``, ``"read-error"``.
    skipped_reason: str = ""
    lines: int = 0
    skipped_lines: int = 0
    skip_reasons: dict[str, int] = field(default_factory=dict)
    enqueued: tuple[int, ...] = ()
    dropped_by_cap: tuple[int, ...] = ()
    skipped_cooldown: tuple[int, ...] = ()
    skipped_active: tuple[int, ...] = ()
    skipped_current: tuple[int, ...] = ()
    #: WP API-FIX-4: see ``TriggerResult.skipped_tool``.
    skipped_tool: tuple[int, ...] = ()
    rotated: bool = False
    #: Bytes discarded because a single region exceeded a whole read batch.
    oversized_skipped_bytes: int = 0
    #: An oversized region runs to EOF with no newline — the sweep cannot make
    #: progress past it yet and says so loudly.
    oversized_stalled: bool = False
    truncated: bool = False
    #: The log was due for rotation but the filesystem refused (the container
    #: permission asymmetry — see ``maybe_truncate``). Sweeping was unaffected.
    truncate_denied: bool = False
    cursor: int = 0
    swept_at: str | None = None


def sweep_once(
    conn: sqlite3.Connection,
    settings: Settings,
    now: datetime,
    notifier: "WebhookNotifier | None" = None,
) -> SweepOutcome:
    """Read, trigger, commit, maybe truncate, check bypass transitions — the
    sweep proper, no gates.

    Split out from ``maybe_sweep`` so every test can drive a sweep with an
    injected clock and without waiting for an interval, exactly as WP 3.5 split
    its own decision layer from its work.

    The ORDER is the contract (module docstring, "Idempotence"): read, then
    enqueue, then commit statistics+cursor together, then truncate, THEN (WP
    3.13) check for a bypass_suspected/bypass_resolved transition (either
    direction) and fire its webhook. Enqueue before commit because a lost job
    is worse than a repeated one, and
    the repeat is absorbed by the cooldown and the queue's dedupe. The bypass
    check runs last and reads only what is already committed, so it never
    announces a state that could still roll back — see
    ``check_bypass_transitions``.

    ``notifier`` defaults to ``None`` (every existing caller/test that does
    not pass one gets the pre-WP-3.13 behavior unchanged: no bypass webhook
    lookup, no ``client_bypass_state`` writes).
    """
    now_iso = to_utc_iso(now)
    state = read_state(conn)
    batch = read_batch(settings.event_log_path, state.cursor_offset)

    if batch.missing:
        logger.info(
            "event-sweep: %r does not exist yet. vault-core creates it on its "
            "first logged request once VAULT_EVENT_LOG is set on that side; "
            "until then there is nothing to sweep.",
            settings.event_log_path,
        )
        return SweepOutcome(
            swept=False,
            skipped_reason="log-missing",
            cursor=state.cursor_offset,
            swept_at=now_iso,
        )
    if batch.error:
        logger.warning(
            "event-sweep: cannot read %r (%s). The cursor stays at %d, so "
            "nothing is lost -- the next sweep re-reads from there.",
            settings.event_log_path,
            batch.error,
            state.cursor_offset,
        )
        return SweepOutcome(
            swept=False,
            skipped_reason="read-error",
            cursor=state.cursor_offset,
            swept_at=now_iso,
        )

    if batch.rotated:
        logger.warning(
            "event-sweep: %r is %d bytes but the cursor was at %d -- the file "
            "shrank, so it was rotated or truncated by something other than "
            "this sweeper. Restarting from offset 0; lines written before the "
            "rotation that this sweeper had not read are gone.",
            settings.event_log_path,
            batch.file_size,
            state.cursor_offset,
        )

    aggregate = aggregate_lines(conn, batch.lines, fallback_time=now_iso)
    trigger = run_miss_trigger(conn, settings, aggregate.candidates, now)
    commit_batch(conn, settings, aggregate, batch, trigger, now_iso)
    # Strictly AFTER the cursor is committed, and strictly best-effort: a
    # refused rotation must not affect anything above it.
    rotation = maybe_truncate(conn, settings, batch.new_cursor, now_iso)
    truncated = rotation.truncated

    # WP 3.13's persist step: strictly AFTER the batch's own commit/truncate
    # above, and a no-op unless a webhook is actually listening for it (see
    # check_bypass_transitions).
    check_bypass_transitions(conn, settings, notifier, now)

    if aggregate.unknown_versions:
        logger.warning(
            "event-sweep: skipped %d line(s) whose format version is not %r. "
            "This vault-api understands v1 only; a newer vault-core writing a "
            "newer format needs a newer vault-api -- the lines are SKIPPED, "
            "never guessed at.",
            aggregate.unknown_versions,
            EVENT_LOG_VERSION,
        )
    if aggregate.skipped:
        logger.warning(
            "event-sweep: skipped %d of %d line(s) as unparseable: %s",
            aggregate.skipped,
            aggregate.skipped + aggregate.parsed,
            aggregate.skip_reasons,
        )
    if batch.oversized_skipped_bytes:
        logger.warning(
            "event-sweep: SKIPPED %d byte(s) of %r starting at offset %d -- a "
            "single 'line' there was longer than the %d-byte read batch, which "
            "no valid event line can be (nginx bounds the URI field to 300 "
            "characters, and this parser refuses anything over %d bytes). "
            "Something other than vault-core's event log is writing to this "
            "file, or it is corrupt. The skipped bytes are GONE, not retried; "
            "sweeping continues from the next newline.",
            batch.oversized_skipped_bytes,
            settings.event_log_path,
            state.cursor_offset,
            MAX_BATCH_BYTES,
            MAX_LINE_LENGTH,
        )
    if batch.oversized_stalled:
        logger.warning(
            "event-sweep: STALLED at offset %d of %r -- there is no newline "
            "anywhere between there and the end of the file, across at least "
            "%d bytes. The cursor deliberately does NOT advance past an "
            "unterminated line, so no progress is possible until a newline "
            "appears; statistics and bypass detection are frozen and the file "
            "cannot be rotated meanwhile. This resolves by itself if a writer "
            "finishes the line. If it persists, the file is not vault-core's "
            "event log.",
            state.cursor_offset,
            settings.event_log_path,
            batch.file_size - state.cursor_offset,
        )
    elif batch.truncated_batch and batch.lines:
        # Only a batch that actually YIELDED lines is making progress. Saying
        # "a backlog remains and is consumed by the following sweeps" about a
        # batch that consumed nothing is what hid the stall above.
        logger.info(
            "event-sweep: read the %d-byte batch limit; a backlog remains and "
            "is consumed by the following sweeps.",
            MAX_BATCH_BYTES,
        )

    logger.info(
        "event-sweep: %d line(s) from %d client address(es); %d new prefill "
        "job(s) from misses%s; cursor %d -> %d%s",
        aggregate.parsed,
        len(aggregate.addrs),
        len(trigger.enqueued),
        f" (enqueued: {_sample(list(trigger.enqueued))})" if trigger.enqueued else "",
        state.cursor_offset,
        0 if truncated else batch.new_cursor,
        " [log truncated]" if truncated else "",
    )

    return SweepOutcome(
        swept=True,
        lines=aggregate.parsed,
        skipped_lines=aggregate.skipped,
        skip_reasons=dict(aggregate.skip_reasons),
        enqueued=trigger.enqueued,
        dropped_by_cap=trigger.dropped_by_cap,
        skipped_cooldown=trigger.skipped_cooldown,
        skipped_active=trigger.skipped_active,
        skipped_current=trigger.skipped_current,
        skipped_tool=trigger.skipped_tool,
        rotated=batch.rotated,
        oversized_skipped_bytes=batch.oversized_skipped_bytes,
        oversized_stalled=batch.oversized_stalled,
        truncated=truncated,
        truncate_denied=rotation.denied,
        cursor=0 if truncated else batch.new_cursor,
        swept_at=now_iso,
    )


def maybe_sweep(
    conn: sqlite3.Connection,
    settings: Settings,
    now: datetime,
    notifier: "WebhookNotifier | None" = None,
) -> SweepOutcome:
    """One tick: decide, and sweep if due.

    Two gates only — no window gate, on purpose (see the module docstring and
    ``config.DEFAULT_EVENT_SWEEP_INTERVAL_MINUTES``). ``notifier`` (WP 3.13)
    is threaded straight through to ``sweep_once``.
    """
    if not settings.event_sweep_enabled:
        return SweepOutcome(swept=False, skipped_reason="disabled")
    if not claim_sweep(conn, now, settings.event_sweep_interval_minutes):
        return SweepOutcome(swept=False, skipped_reason="interval-not-elapsed")
    return sweep_once(conn, settings, now, notifier)


# --------------------------------------------------------------------------
# Read models for the API
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AddrTotals:
    """A client's cache-log presence, summed over its RETAINED windows."""

    requests: int = 0
    hits: int = 0
    misses: int = 0
    bypasses: int = 0
    bytes_served: int = 0
    last_seen: str | None = None


def totals_for_addrs(
    conn: sqlite3.Connection, addrs: list[str]
) -> AddrTotals:
    """Sum ``client_cache_stats`` across every address a client reported from.

    A machine legitimately changes address (DHCP lease, wifi vs. cable), so the
    correlation is over the SET of addresses its retained agent reports arrived
    from, not just the newest one.

    The totals are over retained windows only (``VAULT_CLIENT_STATS_KEEP``);
    that is stated in the API response's own field documentation rather than
    hidden here, because "why is my hit count going down" has to be answerable
    from the docs.
    """
    if not addrs:
        return AddrTotals()
    placeholders = ",".join("?" for _ in addrs)
    row = conn.execute(
        f"""
        SELECT COALESCE(SUM(requests), 0)     AS requests,
               COALESCE(SUM(hits), 0)         AS hits,
               COALESCE(SUM(misses), 0)       AS misses,
               COALESCE(SUM(bypasses), 0)     AS bypasses,
               COALESCE(SUM(bytes_served), 0) AS bytes_served,
               MAX(last_seen)                 AS last_seen
        FROM client_cache_stats
        WHERE client_addr IN ({placeholders})
        """,
        tuple(addrs),
    ).fetchone()
    if row is None:  # pragma: no cover - an aggregate always returns one row
        return AddrTotals()
    return AddrTotals(
        requests=int(row["requests"]),
        hits=int(row["hits"]),
        misses=int(row["misses"]),
        bypasses=int(row["bypasses"]),
        bytes_served=int(row["bytes_served"]),
        last_seen=row["last_seen"],
    )


@dataclass(frozen=True)
class DepotMissRow:
    """One row of ``GET /v1/stats``'s depot-miss list."""

    depotid: int
    miss_count: int
    mapped: bool
    last_seen: str


def top_depot_misses(
    conn: sqlite3.Connection, limit: int, *, unmapped_only: bool = True
) -> list[DepotMissRow]:
    """Most recently seen depot misses, newest first.

    Defaults to the unmapped ones because those are the actionable list: a
    depot being downloaded that vault-api has no mapping for is a game it can
    neither attribute nor prefill (plan §4's manual-fallback case).
    """
    where = "WHERE mapped = 0" if unmapped_only else ""
    rows = conn.execute(
        f"""
        SELECT depotid, miss_count, mapped, last_seen
        FROM depot_miss_stats
        {where}
        ORDER BY last_seen DESC, depotid DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    return [
        DepotMissRow(
            depotid=int(row["depotid"]),
            miss_count=int(row["miss_count"]),
            mapped=bool(row["mapped"]),
            last_seen=str(row["last_seen"]),
        )
        for row in rows
    ]


def feed_is_young(state: SweepState, settings: Settings, now: datetime) -> bool:
    """Has the feed been observed for less than the bypass window?

    The load-bearing half of "fail toward NOT suspecting": before the feed has
    run for a full ``VAULT_BYPASS_WINDOW_DAYS``, "no cache-log presence in the
    last N days" is a statement about how long we have been watching, not about
    the client. Also true when no sweep has ever completed.
    """
    if state.first_sweep_at is None:
        return True
    first = parse_utc_iso(state.first_sweep_at)
    if first is None:
        return True
    return now - timedelta(days=settings.bypass_window_days) < first


def bypass_suspected(
    summary: "agent_reports.ClientSummary",
    totals: AddrTotals,
    *,
    feed_can_accuse: bool,
    cutoff_iso: str,
) -> bool:
    """The disqualification chain (plan §5's DNS/IPv6-bypass pain point).

    Moved here from ``routers/clients.py`` in WP 3.13 so both call sites —
    ``GET /v1/clients`` (recomputed fresh on every request, read-only) and
    ``check_bypass_transitions`` below (recomputed once per sweep, to detect
    a NEW verdict worth a webhook) — share exactly one definition. Two copies
    of a fail-toward-not-accusing rule are two places for them to quietly
    drift apart, and a webhook that disagreed with what ``GET /v1/clients``
    reports at the same moment would be a worse bug than either call site
    alone.

    Written as early returns rather than one boolean expression on purpose:
    each ``return False`` is a distinct reason a client is NOT accused, and
    each one is separately mutation-tested (flip it and a named test dies,
    per docs/LEARNINGS.md's rule about pinning fail-safe DEFAULTS, not just
    the happy path).
    """
    # 1 + 2: the feed is off, has never swept, or is younger than the window
    # (both folded into feed_can_accuse by the caller).
    if not feed_can_accuse:
        return False
    # 3: the client itself has been silent longer than the window.
    if summary.last_reported_at < cutoff_iso:
        return False
    # 4: nothing installed (or an unreadable snapshot) means nothing to bypass.
    if not summary.app_count:
        return False
    # 5: no address on any retained report — correlation is impossible.
    if not summary.source_addrs:
        return False
    # 6: it HAS been seen at the cache within the window.
    if totals.last_seen is not None and totals.last_seen >= cutoff_iso:
        return False
    return True


# --------------------------------------------------------------------------
# Bypass TRANSITION detection (WP 3.13) — the webhook's persist step
# --------------------------------------------------------------------------


def read_bypass_states(conn: sqlite3.Connection) -> dict[str, bool]:
    """The LAST computed ``bypass_suspected`` verdict per client (schema v10).

    Empty for a client never checked before (which reads as "was not
    suspected", the correct starting assumption for the transition check
    below — a client's first-ever observation can never itself be a NEW
    transition).
    """
    rows = conn.execute(
        "SELECT client_id, bypass_suspected FROM client_bypass_state"
    ).fetchall()
    return {str(row["client_id"]): bool(row["bypass_suspected"]) for row in rows}


def _write_bypass_state(
    conn: sqlite3.Connection, client_id: str, suspected: bool, now_iso: str
) -> None:
    conn.execute(
        """
        INSERT INTO client_bypass_state (client_id, bypass_suspected, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(client_id) DO UPDATE SET
            bypass_suspected = excluded.bypass_suspected,
            updated_at       = excluded.updated_at
        """,
        (client_id, int(suspected), now_iso),
    )


@dataclass(frozen=True)
class BypassTransition:
    """One client's bypass verdict flip this tick — which way, and who."""

    event: str
    client_id: str


def check_bypass_transitions(
    conn: sqlite3.Connection,
    settings: Settings,
    notifier: "WebhookNotifier | None",
    now: datetime,
) -> tuple[BypassTransition, ...]:
    """Fire a bypass webhook for every client that JUST flipped, either way.

    Two transitions, symmetric in every way that matters:

    * ``client.bypass_suspected`` — a client NEWLY flags (was not suspected,
      now is).
    * ``client.bypass_resolved`` — the all-clear: a previously-suspected
      client's cache-log presence returned (was suspected, now is not). This
      closes the loop the ``suspected`` event opened — an operator who got
      paged for a possible DNS/IPv6 bypass wants to know when it stopped
      being true, not just when it started.

    Called from ``sweep_once`` strictly AFTER ``commit_batch``/
    ``maybe_truncate`` — this is the sweep's "persist step" the work package
    means: the verdict computed below reads THIS sweep's already-committed
    ``event_sweep_state``/``client_cache_stats``, so a webhook it fires
    describes a state that has already landed and cannot roll back.

    Runs once per SWEEP TICK, not once per event line: a client's own agent
    reports going stale, or the bypass window simply aging past
    ``last_seen_in_cache_log``, can flip the verdict (in either direction)
    even when this particular batch had zero new lines in it. Skipping ticks
    with no new lines would miss exactly the "quiet client sitting past the
    window" (and its later "came back") cases the feature exists for.

    Guarded so an installation that never asked for either event pays
    nothing for it: no notifier, the webhook feature off, BOTH bypass events
    excluded from ``VAULT_WEBHOOK_EVENTS``, or the cache-event sweep itself
    disabled all skip touching ``agent_reports``/``client_bypass_state``
    entirely. Once inside, both directions are always computed and
    persisted together — ``client_bypass_state`` is the one durable verdict
    per client, and asking for only one of the two events must not corrupt
    it for the other (an operator who later adds the missing event to
    ``VAULT_WEBHOOK_EVENTS`` gets correct transitions from that point on,
    not a false first-sight event for every already-suspected client).
    Per-event filtering happens where it always has, inside
    ``WebhookNotifier.enqueue``.

    Returns every transition this tick, in the order clients were visited
    (diagnostic / test-observable; the caller does not currently use the
    return value for anything but logging).
    """
    if notifier is None or not settings.webhook_enabled:
        return ()
    if not (
        WEBHOOK_EVENT_BYPASS_SUSPECTED in settings.webhook_events
        or WEBHOOK_EVENT_BYPASS_RESOLVED in settings.webhook_events
    ):
        return ()
    # Same first gate routers/clients.py's feed_can_accuse applies: with the
    # sweep itself off, event_sweep_state may be stale or entirely absent, and
    # "no cache-log presence" would mean nothing.
    if not settings.event_sweep_enabled:
        return ()

    state = read_state(conn)
    feed_can_accuse = not feed_is_young(state, settings, now)
    if not feed_can_accuse:
        return ()

    cutoff_iso = to_utc_iso(now - timedelta(days=settings.bypass_window_days))
    now_iso = to_utc_iso(now)
    previous_states = read_bypass_states(conn)

    # Collected during the transaction, but the webhook itself is only fired
    # AFTER it commits below (module docstring: never announce a state that
    # could still roll back) — enqueue() only touches an in-process queue, so
    # nothing is lost by deferring it the width of one Python block.
    new_transitions: list[tuple[str, str, list[str], str | None]] = []
    with immediate_transaction(conn):
        for summary in agent_reports.list_clients(conn):
            totals = totals_for_addrs(conn, summary.source_addrs)
            suspected = bypass_suspected(
                summary, totals, feed_can_accuse=True, cutoff_iso=cutoff_iso
            )
            was_suspected = previous_states.get(summary.client_id, False)

            if suspected != was_suspected:
                _write_bypass_state(conn, summary.client_id, suspected, now_iso)

            if suspected and not was_suspected:
                new_transitions.append(
                    (
                        WEBHOOK_EVENT_BYPASS_SUSPECTED,
                        summary.client_id,
                        summary.source_addrs,
                        totals.last_seen,
                    )
                )
            elif was_suspected and not suspected:
                new_transitions.append(
                    (
                        WEBHOOK_EVENT_BYPASS_RESOLVED,
                        summary.client_id,
                        summary.source_addrs,
                        totals.last_seen,
                    )
                )

    transitioned = tuple(
        BypassTransition(event=event, client_id=client_id)
        for event, client_id, _addrs, _seen in new_transitions
    )
    for event, client_id, addresses, last_seen in new_transitions:
        webhooks.notify_bypass_event(
            notifier,
            event=event,
            client_id=client_id,
            addresses=addresses,
            last_seen=last_seen,
        )

    if transitioned:
        logger.warning(
            "event-sweep: %d client bypass transition(s) this sweep: %s",
            len(transitioned),
            [(t.event, t.client_id) for t in transitioned],
        )

    return transitioned
