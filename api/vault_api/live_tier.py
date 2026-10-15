"""The live traffic tier (WP GRAPH-LIVE): memory only, never persisted.

The persisted tiers (``traffic.py``) are filled by the event sweep, which
reads vault-core's event log on the scheduler cadence (5 minutes by default).
That is the real limit of how "live" the Downloads graph can be: nginx writes
a line when a request ENDS (chunks are small, so a 5 s bucket is accurate),
the sweep just reads it late. This module reads the same file a second time,
on its own cursor, for the graph only:

* **Ring buffer.** 5-second cells per address for the last ``RING_SECONDS``
  (15 minutes), pruned on every write. Bounded: at most ``RING_SECONDS / 5``
  cells x ``MAX_ADDRS_PER_CELL`` addresses (overflow folds into one anonymous
  entry), so a flood of distinct addresses cannot grow it. Lost on restart,
  never written to SQLite (no schema change, no extra writes).
* **Watcher.** The log is tailed only while somebody polls the live endpoint
  (``touch``): at most once per ``TAIL_INTERVAL_SECONDS`` (2 s) however many
  clients poll, and not at all when nobody polled for
  ``WATCHER_WINDOW_SECONDS`` (30 s). There is no background thread, so an
  idle server does exactly what it did before. After an idle stretch the next
  poll reads the backlog (bounded per call) and buckets it by EVENT time, so
  nothing the file still holds is lost to the graph.
* **Stitching.** ``stitch`` returns one series: 5 s points for the last
  minute, 15 s points up to 5 minutes back, then 1-minute points up to an
  hour back. A 1-minute point comes from the ring when the ring has covered
  that whole minute since the tailer started (the persisted row would lag the
  sweep by up to one interval), else from the persisted 1-minute tier. Each
  point has exactly one source: nothing is summed across sources, so nothing
  is counted twice.

Known limits (honest, not bugs): the sweep truncates the log once it has
caught up; lines that arrive between our last read and that truncation never
reach the ring (the persisted tiers have them). A shrunk file or a changed
inode restarts the tailer at offset 0. Runner attribution uses the persisted
runner windows, which a sweep extends: runner traffic before the first sweep
of a UTC day counts as client traffic here until that sweep happened.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from vault_api import event_sweep
from vault_api.jobs import parse_utc_iso, to_utc_iso

logger = logging.getLogger(__name__)

UTC = timezone.utc

CELL_SECONDS = 5
#: How much history the ring keeps.
RING_SECONDS = 15 * 60
#: Distinct addresses kept per cell; the rest folds into ``OVERFLOW_ADDR``.
MAX_ADDRS_PER_CELL = 64
OVERFLOW_ADDR = ""

TAIL_INTERVAL_SECONDS = 2.0
WATCHER_WINDOW_SECONDS = 30.0
#: One read call never takes more than this (the sweep's own batch size)...
MAX_READ_BYTES = event_sweep.MAX_BATCH_BYTES
#: ...and one refresh at most this many calls; a larger backlog is skipped to
#: its tail (the persisted tiers keep it).
MAX_READS_PER_REFRESH = 3
#: First read of a fresh tailer: at most this much of the file's end.
MAX_BACKFILL_BYTES = 4 * 1024 * 1024

#: Stitched series geometry (seconds back from now).
FINE_SECONDS = 60
MEDIUM_SECONDS = 5 * 60
TOTAL_SECONDS = 60 * 60
MEDIUM_BUCKET = 15
COARSE_BUCKET = 60
#: The "Current speed" window: this many seconds plus the running cell.
CURRENT_SECONDS = 15

#: Per-address counters, in this order.
_REQ, _BYTES, _HIT, _MISS = range(4)


@dataclass(frozen=True)
class LiveTotals:
    requests: int = 0
    bytes_served: int = 0
    hit_bytes: int = 0
    miss_bytes: int = 0


@dataclass(frozen=True)
class LivePoint:
    """One stitched point; ``bucket_seconds`` varies along the series."""

    t: str
    bucket_seconds: int
    source: str  # "live" (ring) | "persisted" (1-minute tier)
    all_totals: LiveTotals
    #: The prefill runner's part (bytes_served/hit/miss/requests).
    runner: LiveTotals
    #: Largest single cell inside the bucket / its length (a 5 s cell for ring
    #: points, the 1-minute row itself for persisted ones).
    peak_bytes_per_second: float


def _floor(epoch: float, step: int) -> int:
    return int(epoch // step) * step


def _epoch(moment: datetime) -> float:
    return moment.timestamp()


def _iso(epoch: int) -> str:
    return to_utc_iso(datetime.fromtimestamp(epoch, tz=UTC))


class LiveTier:
    """Ring buffer + tailer. One instance per app, shared across requests."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        tail_interval: float = TAIL_INTERVAL_SECONDS,
        watcher_window: float = WATCHER_WINDOW_SECONDS,
        ring_seconds: int = RING_SECONDS,
        max_read_bytes: int = MAX_READ_BYTES,
        max_backfill_bytes: int = MAX_BACKFILL_BYTES,
    ) -> None:
        self.clock: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))
        self._tail_interval = tail_interval
        self._watcher_window = watcher_window
        self._ring_seconds = ring_seconds
        self._max_read = max_read_bytes
        self._max_backfill = max_backfill_bytes
        self._lock = threading.Lock()
        #: cell start (epoch s) -> address -> [requests, bytes, hit, miss]
        self._cells: dict[int, dict[str, list[int]]] = {}
        self._path: str | None = None
        self._cursor: int | None = None
        self._ino: int | None = None
        self._last_poll: float | None = None
        self._last_tail: float | None = None
        #: Minutes starting at or after this epoch are fully in the ring.
        self._covered_from: float | None = None

    # -- watcher ----------------------------------------------------------

    def touch(self, now: datetime) -> None:
        """A client polled the live endpoint."""
        with self._lock:
            self._last_poll = _epoch(now)

    def watcher_active(self, now: datetime) -> bool:
        with self._lock:
            return self._watching(_epoch(now))

    def _watching(self, now: float) -> bool:
        return self._last_poll is not None and now - self._last_poll <= self._watcher_window

    # -- tailing ----------------------------------------------------------

    def refresh(self, path: str, now: datetime) -> int:
        """Tail the log if the watcher is active and a tail is due.

        Returns the number of lines folded into the ring (0 when skipped).
        Never raises: the live graph is best effort.
        """
        epoch = _epoch(now)
        with self._lock:
            if not path or not self._watching(epoch):
                return 0
            if self._last_tail is not None and 0 <= epoch - self._last_tail < self._tail_interval:
                return 0
            self._last_tail = epoch
            try:
                return self._tail(path, now, epoch)
            except Exception:  # noqa: BLE001 - best effort, never break a request
                logger.exception("live-tier: tailing %r failed; retried on the next poll.", path)
                return 0

    def _reset(self, path: str) -> None:
        self._path = path
        self._cursor = None
        self._ino = None
        self._covered_from = None

    def _tail(self, path: str, now: datetime, epoch: float) -> int:
        if path != self._path:
            self._cells.clear()
            self._reset(path)
        try:
            fd = event_sweep.open_event_log(path, os.O_RDONLY)
        except FileNotFoundError:
            return 0
        except OSError as exc:
            logger.warning("live-tier: cannot open %r (%s).", path, exc)
            return 0
        folded = 0
        try:
            info = os.fstat(fd)
            size = info.st_size
            fresh = self._cursor is None
            if self._ino is not None and info.st_ino != self._ino:
                # Another file at the path (rotation): start over on it.
                self._cursor = 0
                fresh = False
            elif self._cursor is not None and size < self._cursor:
                self._cursor = 0  # truncated by the sweep or rotated
                fresh = False
            self._ino = info.st_ino
            if self._cursor is None:
                self._cursor = max(0, size - self._max_backfill)
            elif size - self._cursor > self._max_read * MAX_READS_PER_REFRESH:
                # A backlog we will not chew through: keep its end only.
                self._cursor = max(0, size - self._max_backfill)
                fresh = True
            started_mid_file = self._cursor > 0 and (fresh or self._covered_from is None)
            first_line_epoch: float | None = None
            for _ in range(MAX_READS_PER_REFRESH):
                data = os.pread(fd, self._max_read, self._cursor)
                if not data:
                    break
                end = data.rfind(b"\n")
                if end < 0:
                    if len(data) >= self._max_read:
                        # One "line" longer than a whole read: skip it whole.
                        self._cursor += len(data)
                        continue
                    break  # partial line, wait for the rest
                text = data[: end + 1].decode("utf-8", errors="replace")
                self._cursor += end + 1
                for raw in text.split("\n"):
                    if not raw:
                        continue
                    seen = self._fold(raw, now, epoch)
                    if seen is not None:
                        folded += 1
                        if first_line_epoch is None:
                            first_line_epoch = seen
                if len(data) < self._max_read:
                    break
            if self._covered_from is None or fresh:
                if not started_mid_file:
                    # Read from the file's first byte: everything the file
                    # holds is in the ring (older lines are persisted).
                    self._covered_from = epoch - self._ring_seconds
                elif first_line_epoch is not None:
                    self._covered_from = float(_floor(first_line_epoch, 60) + 60)
                else:
                    self._covered_from = float(_floor(epoch, 60) + 60)
        finally:
            os.close(fd)
        self._prune(epoch)
        return folded

    def _fold(self, raw: str, now: datetime, epoch: float) -> float | None:
        line, _reason = event_sweep.parse_line(raw)
        if line is None or line.cache_status == event_sweep.STATUS_RUNNER_MARK:
            return None
        seen = parse_utc_iso(line.time_utc) if line.time_utc else None
        seen_epoch = min(_epoch(seen), epoch) if seen is not None else epoch
        if seen_epoch < epoch - self._ring_seconds:
            return None  # older than the ring: persisted already
        cell = self._cells.setdefault(_floor(seen_epoch, CELL_SECONDS), {})
        addr = line.addr
        counters = cell.get(addr)
        if counters is None:
            if len(cell) >= MAX_ADDRS_PER_CELL:
                addr = OVERFLOW_ADDR
                counters = cell.setdefault(addr, [0, 0, 0, 0])
            else:
                counters = cell[addr] = [0, 0, 0, 0]
        counters[_REQ] += 1
        if line.served:
            counters[_BYTES] += line.bytes_sent
            if line.cache_status == event_sweep.STATUS_HIT:
                counters[_HIT] += line.bytes_sent
            elif line.cache_status == event_sweep.STATUS_MISS:
                counters[_MISS] += line.bytes_sent
        return seen_epoch

    def _prune(self, epoch: float) -> None:
        horizon = _floor(epoch - self._ring_seconds, CELL_SECONDS)
        for key in [k for k in self._cells if k < horizon]:
            del self._cells[key]

    # -- reading ----------------------------------------------------------

    def _copy(self) -> tuple[dict[int, dict[str, tuple[int, ...]]], float | None]:
        with self._lock:
            cells = {
                key: {addr: tuple(c) for addr, c in by_addr.items()}
                for key, by_addr in self._cells.items()
            }
            return cells, self._covered_from

    def stitch(
        self,
        now: datetime,
        persisted: Callable[[int, int], dict[int, "PersistedMinute"]],
        is_runner: Callable[[str, str], bool],
    ) -> list[LivePoint]:
        """The stitched series, oldest first, ending with the running cell.

        ``persisted(start_epoch, end_epoch)`` returns the 1-minute tier's rows
        for the minutes in ``[start, end)`` keyed by minute epoch (missing
        minute = nothing recorded); ``is_runner(addr, bucket_iso)`` says
        whether an address's traffic in that bucket is the prefill runner's.
        """
        epoch = _epoch(now)
        cells, covered_from = self._copy()

        def totals(start: int, end: int) -> tuple[LiveTotals, LiveTotals, int]:
            """Ring totals over [start, end): all, runner, largest cell."""
            all_t = [0, 0, 0, 0]
            run_t = [0, 0, 0, 0]
            peak = 0
            for key in range(_floor(start, CELL_SECONDS), end, CELL_SECONDS):
                by_addr = cells.get(key)
                if not by_addr:
                    continue
                cell_bytes = 0
                iso = _iso(key)
                for addr, c in by_addr.items():
                    cell_bytes += c[_BYTES]
                    for i in range(4):
                        all_t[i] += c[i]
                    if addr != OVERFLOW_ADDR and is_runner(addr, iso):
                        for i in range(4):
                            run_t[i] += c[i]
                peak = max(peak, cell_bytes)
            return LiveTotals(*all_t), LiveTotals(*run_t), peak

        points: list[LivePoint] = []
        now_cell = _floor(epoch, CELL_SECONDS)
        fine_start = _floor(epoch, MEDIUM_BUCKET) - (FINE_SECONDS - MEDIUM_BUCKET)
        medium_start = _floor(fine_start, 60) - (MEDIUM_SECONDS - FINE_SECONDS - 60)
        coarse_start = medium_start - (TOTAL_SECONDS - MEDIUM_SECONDS) + 60

        # 1-minute region: ring where it covers the whole minute, else persisted.
        rows = persisted(coarse_start, medium_start)
        ring_floor = _floor(epoch - self._ring_seconds, 60) + 60
        for minute in range(coarse_start, medium_start, COARSE_BUCKET):
            if covered_from is not None and minute >= covered_from and minute >= ring_floor:
                all_t, run_t, peak = totals(minute, minute + COARSE_BUCKET)
                points.append(
                    LivePoint(_iso(minute), COARSE_BUCKET, "live", all_t, run_t, peak / CELL_SECONDS)
                )
                continue
            row = rows.get(minute)
            if row is None:
                points.append(LivePoint(_iso(minute), COARSE_BUCKET, "persisted", LiveTotals(), LiveTotals(), 0.0))
            else:
                points.append(
                    LivePoint(
                        _iso(minute), COARSE_BUCKET, "persisted", row.all, row.runner,
                        row.peak / COARSE_BUCKET,
                    )
                )

        for start in range(medium_start, fine_start, MEDIUM_BUCKET):
            all_t, run_t, peak = totals(start, start + MEDIUM_BUCKET)
            points.append(
                LivePoint(_iso(start), MEDIUM_BUCKET, "live", all_t, run_t, peak / CELL_SECONDS)
            )
        for start in range(fine_start, now_cell + CELL_SECONDS, CELL_SECONDS):
            all_t, run_t, peak = totals(start, start + CELL_SECONDS)
            points.append(
                LivePoint(_iso(start), CELL_SECONDS, "live", all_t, run_t, peak / CELL_SECONDS)
            )
        return points

    def current(self, now: datetime, is_runner: Callable[[str, str], bool]) -> "LiveCurrent":
        """Speed over the last ``CURRENT_SECONDS`` plus the running cell."""
        epoch = _epoch(now)
        cells, _covered = self._copy()
        now_cell = _floor(epoch, CELL_SECONDS)
        start = now_cell - CURRENT_SECONDS
        total = 0
        runner = 0
        for key in range(start, now_cell + CELL_SECONDS, CELL_SECONDS):
            by_addr = cells.get(key)
            if not by_addr:
                continue
            iso = _iso(key)
            for addr, c in by_addr.items():
                total += c[_BYTES]
                if addr != OVERFLOW_ADDR and is_runner(addr, iso):
                    runner += c[_BYTES]
        seconds = max(1.0, epoch - start)
        return LiveCurrent(
            bytes_per_second=total / seconds,
            runner_bytes_per_second=runner / seconds,
            window_start=_iso(start),
            window_end=to_utc_iso(now),
            window_seconds=int(round(seconds)),
        )

    def covered_from(self) -> str | None:
        with self._lock:
            return _iso(int(self._covered_from)) if self._covered_from is not None else None

    def cell_count(self) -> int:
        with self._lock:
            return len(self._cells)


@dataclass(frozen=True)
class PersistedMinute:
    all: LiveTotals
    runner: LiveTotals
    #: ``peak_bucket_bytes`` of the row (bytes in the minute).
    peak: int


@dataclass(frozen=True)
class LiveCurrent:
    bytes_per_second: float
    runner_bytes_per_second: float
    window_start: str
    window_end: str
    window_seconds: int


def minute_epoch(iso: str) -> int | None:
    moment = parse_utc_iso(iso)
    return int(moment.timestamp()) if moment is not None else None
