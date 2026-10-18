"""Per-game cache size calculation, cached (plan §3: "du over depot folders,
cached"; plan §6: ``GET /v1/cache/summary``).

Three layers, each independently testable:

1. ``walk_file_stats`` / ``scan_depot_signatures`` — the actual filesystem
   walk. This is the SAME walk ``vault_api.prefill.scan_depots`` uses for its
   before/after attribution diff (WP 1.4 carry-over fix #1: a fast walk was
   built for that diff — 17x faster than ``os.walk`` + a separate ``os.stat``
   per file, measured 0.922s -> 0.055s on 21k files, because
   ``DirEntry.stat()`` is served from the directory-listing data the OS
   already returned instead of a second round trip) — sharing it here means
   the size calculation gets the same speedup for free, and there is exactly
   one place that knows how to walk the depot tree, not two.
2. ``SizeCache`` — the in-process cache plan §3 asks for ("du ... cached"):
   one cached snapshot, no extra table, served stale-while-revalidate with at
   most one background walk at a time (UI-COLD-CACHED-1; the original
   walk-under-the-lock blocked requests on a cold disk). Explicit invalidation
   hooks (not polling) are exported for callers that know disk content just
   changed: the prefill worker calls ``invalidate()`` after a successful job
   (``vault_api/worker.py``), and WP 1.6's deletion endpoint will do the same.
3. ``app_size_bytes`` / ``build_cache_summary`` — turn a byte-per-depot
   snapshot into what the API actually reports: per-app totals (shared depots
   counted into every app that maps them — see ``app_size_bytes``), the cache
   summary's top consumers, unmapped depots, and free disk space.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator

logger = logging.getLogger(__name__)

#: Default TTL for the in-process size cache (plan §3: "du over depot
#: folders, cached"). Env-tunable via VAULT_SIZE_CACHE_TTL (config.py).
DEFAULT_SIZE_CACHE_TTL_SECONDS = 60.0

#: (file_count, total_bytes, newest_mtime_ns) per depot id. Shared with
#: vault_api.prefill, which needs the full signature for its diff; this
#: module only ever looks at total_bytes (index 1).
DepotSignature = tuple[int, int, int]


# --------------------------------------------------------------------------
# The filesystem walk (shared with vault_api.prefill.scan_depots)
# --------------------------------------------------------------------------


def is_link_like(path: str) -> bool:
    """True if ``path`` is a symlink **or** a Windows junction.

    Both are "this name points somewhere else" and must never be traversed or
    recursively deleted — but they are *different* things to Python, which is
    exactly the trap this helper exists to close. Measured on Windows 11 /
    CPython 3.12.10 against a real junction created with ``mklink /J`` (WP 1.6):

    ==================================  ===============  ==============
    check                               dir symlink      junction
    ==================================  ===============  ==============
    ``os.path.islink()``                True             **False**
    ``os.path.isjunction()``            False            True
    ``DirEntry.is_dir(follow=False)``   False            **True**
    ==================================  ===============  ==============

    So ``os.path.islink`` alone silently misses junctions, and
    ``is_dir(follow_symlinks=False)`` reports a junction as an ordinary
    directory — a walk relying only on those descends straight through it.

    ``os.path.isjunction`` exists from CPython 3.12; the ``getattr`` keeps
    this function correct (just symlink-only) on an older interpreter instead
    of raising.
    """
    if os.path.islink(path):
        return True
    isjunction = getattr(os.path, "isjunction", None)
    return bool(isjunction is not None and isjunction(path))


def entry_is_link_like(entry: os.DirEntry) -> bool:
    """``is_link_like`` for an already-listed directory entry (no extra stat).

    ``DirEntry.is_junction()`` exists from CPython 3.12; ``getattr`` keeps
    older interpreters working (symlink detection only).

    **Public (WP 3.7):** ``vault_api.gc`` scans a depot's ``chunk/`` directory
    entry by entry and must refuse to treat a link as a deletable chunk. Doing
    that through the path-based ``is_link_like`` would cost one extra ``lstat``
    per file — measurable on the 70k-chunk depots this project really sees —
    so the entry-based check is shared rather than duplicated (it was
    ``_entry_is_link_like`` until GC needed it; renamed, not reimplemented).
    """
    if entry.is_symlink():
        return True
    is_junction = getattr(entry, "is_junction", None)
    return bool(is_junction is not None and is_junction())


@dataclass
class WalkCounts:
    """Mutable tally a caller may pass into the walk (UI-COLD-CACHED-1).

    ``dirs`` counts every directory the walk listed (the depot directories
    themselves included). It exists so ``SizeCache`` can log what a recompute
    cost: on a cold dentry cache the walk time scales with directory and
    file count, and the next cold test needs both numbers to read the log.
    """

    dirs: int = 0


def walk_file_stats(path: str, counts: WalkCounts | None = None) -> Iterator[os.stat_result]:
    """Recursively yield an ``os.stat_result`` for every regular file under ``path``.

    Uses ``os.scandir`` + ``DirEntry.stat()`` rather than ``os.walk`` +
    ``os.stat()`` per file — ``DirEntry.stat()`` is answered from data the
    directory listing already returned (cached by the OS on Windows, and
    avoids a second ``stat`` syscall on POSIX too), while ``os.walk`` +
    ``os.stat(path)`` forces one extra filesystem round trip per file.
    Measured 17x on a 21k-file depot tree (0.922s -> 0.055s) while writing
    ``vault_api.prefill.scan_depots`` (WP 1.4 review) — this is the same
    walk, reused here instead of duplicated (WP 1.5 carry-over fix #1).

    **Links are never traversed *inside* the tree** (cycle protection, and it
    keeps reported sizes equal to what a deletion would actually free — see
    ``vault_api.deletion``, which likewise refuses to follow a link out of the
    cache). ``follow_symlinks=False`` alone is not enough for that on Windows:
    a junction is reported as a plain directory by
    ``DirEntry.is_dir(follow_symlinks=False)`` (measured, see
    ``is_link_like``), so a junction pointing at one of its own ancestors would
    recurse forever. Junction and symlink entries are therefore skipped
    explicitly. The *top-level* depot directory is the deliberate exception —
    see ``scan_depot_signatures``.

    A file or directory that vanishes mid-walk (vault-core is writing into
    this tree concurrently) is skipped rather than failing the whole scan.

    ``counts``, when given, is incremented once per directory listed.
    """
    try:
        entries = list(os.scandir(path))
    except OSError:
        return
    if counts is not None:
        counts.dirs += 1
    for entry in entries:
        try:
            if entry_is_link_like(entry):
                continue
            if entry.is_dir(follow_symlinks=False):
                yield from walk_file_stats(entry.path, counts)
            elif entry.is_file(follow_symlinks=False):
                yield entry.stat()
        except OSError:
            continue


def scan_depot_signatures(
    cache_root: str, counts: WalkCounts | None = None
) -> dict[int, DepotSignature]:
    """Signature per depot directory under ``<cache_root>/depot/<depotid>/``.

    The signature is an aggregate, not a per-file listing: a real cache holds
    hundreds of thousands of chunk files and this runs on every prefill job
    (twice) and on every size-cache miss. ``(count, bytes, newest mtime)``
    changes whenever a chunk is added, replaced, or rewritten — everything a
    caller of this function needs.

    Depots with zero files are omitted: an empty directory means nothing was
    ever stored there, so it must not be attributed to an app or counted
    towards any size.

    This is the single implementation of "scan the depot tree" — both
    ``vault_api.prefill.scan_depots`` (before/after attribution diff) and
    this module's own ``scan_depot_dir_bytes`` (size calculation) call it
    rather than each walking the tree themselves.

    **Top-level depot directories are detected FOLLOWING links** (WP 1.6
    carry-over fix #1 from the WP 1.5 review): ``entry.is_dir()``, not
    ``entry.is_dir(follow_symlinks=False)``. Placing an individual depot
    directory as a symlink/junction onto another volume is a legitimate
    homelab move, and the old check made such a depot **invisible** —
    measured: ``DirEntry.is_dir(follow_symlinks=False)`` is ``False`` for a
    directory symlink (it *is* ``True`` for a Windows junction, so only
    symlinks were affected). Consequences of that invisibility were worse than
    an under-reported size: the depot never appeared in a prefill's
    before/after snapshot either, so ``prefill.apply_observed_mapping`` treated
    the app's correct mapping row as stale and **deleted** it on the next
    prefill.

    Inside the tree the walk still refuses to follow links
    (``walk_file_stats``) — the exception is exactly one level deep, where the
    directory name IS the depot id and there is no cycle risk from entering it
    once.
    """
    signatures: dict[int, DepotSignature] = {}
    depot_root = os.path.join(cache_root, "depot")
    try:
        entries = list(os.scandir(depot_root))
    except (FileNotFoundError, NotADirectoryError):
        return signatures
    except OSError as exc:  # pragma: no cover - defensive
        logger.warning("Could not scan %s: %s", depot_root, exc)
        return signatures

    for entry in entries:
        # isascii() AND isdigit() (docs/LEARNINGS.md "Parsers"): isdigit()
        # alone is True for "²" and Arabic-Indic digits, and int() on those
        # either raises (crashing the whole scan on one odd directory name)
        # or silently normalises. Neither is a depot id anything here wrote.
        if not (entry.name.isascii() and entry.name.isdigit()):
            continue
        try:
            # follow_symlinks defaults to True here ON PURPOSE — see the
            # docstring: a symlinked/junctioned depot directory must be seen.
            if not entry.is_dir():
                continue
        except OSError:  # pragma: no cover - defensive
            continue

        count = 0
        total_bytes = 0
        newest_mtime_ns = 0
        for stat in walk_file_stats(entry.path, counts):
            count += 1
            total_bytes += stat.st_size
            newest_mtime_ns = max(newest_mtime_ns, stat.st_mtime_ns)

        if count:
            signatures[int(entry.name)] = (count, total_bytes, newest_mtime_ns)

    return signatures


def scan_depot_dir_bytes(cache_root: str) -> dict[int, int]:
    """Total bytes per depot id currently on disk (the "du" plan §3 asks for).

    Just the byte totals out of ``scan_depot_signatures`` — a depot id absent
    from the returned dict has no bytes on disk yet (never prefilled, or
    genuinely empty), which callers must treat as "unknown", not "zero" (see
    ``app_size_bytes``).
    """
    return {depotid: signature[1] for depotid, signature in scan_depot_signatures(cache_root).items()}


# --------------------------------------------------------------------------
# The TTL cache (plan §3: "du over depot folders, cached")
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SizeSnapshot:
    """One cached disk scan. ``total_bytes`` is the real, counted-once disk
    usage of ``depot/`` — the sum of every depot's bytes, each depot counted
    exactly once regardless of how many apps map it (contrast with per-app
    totals, which double-count shared depots on purpose — see
    ``app_size_bytes``).

    ``computed_at`` is the monotonic time the walk STARTED, so a snapshot's
    age never understates how old its data can be."""

    computed_at: float
    depot_bytes: dict[int, int]
    total_bytes: int


@dataclass(frozen=True)
class DepotWalk:
    """What one full walk of ``depot/`` found: the byte totals ``SizeCache``
    serves, plus the counts it logs (UI-COLD-CACHED-1)."""

    depot_bytes: dict[int, int]
    file_count: int
    dir_count: int


def walk_depot_tree(cache_root: str) -> DepotWalk:
    """The default walk behind ``SizeCache``: ``scan_depot_signatures`` with
    the file and directory counts kept for the duration log."""
    counts = WalkCounts()
    signatures = scan_depot_signatures(cache_root, counts)
    return DepotWalk(
        depot_bytes={depotid: signature[1] for depotid, signature in signatures.items()},
        file_count=sum(signature[0] for signature in signatures.values()),
        dir_count=counts.dirs,
    )


class SizeWalkError(RuntimeError):
    """Raised to a caller that had to wait for a walk (no usable snapshot) when
    that walk failed. Callers that get a stale snapshot never see it."""


class SizeCache:
    """In-process cache over one ``cache_root``'s depot byte totals, served
    stale-while-revalidate (UI-COLD-CACHED-1).

    **Why not a plain TTL any more.** The first version walked the whole
    ``depot/`` tree inside ``get()`` while holding the lock whenever the TTL
    had expired. On a warm dentry cache that is sub-second; in the cold run of
    2026-10-18 (host page cache and ZFS ARC dropped, a client downloading) the
    walk competed with the download for the disks, every request needing sizes
    queued behind the lock once a minute, and the Library showed no game as
    cached while every request was a HIT. So now:

    - **Fresh snapshot** (younger than ``ttl_seconds``): returned as is.
    - **Expired snapshot**: returned immediately, and exactly one background
      recompute is started (single-flight: while a walk runs, further expired
      reads start nothing). The next read after the walk sees new numbers.
    - **No usable snapshot** (the very first call, a different ``cache_root``,
      or after ``invalidate()``): the caller blocks until a walk that started
      after that point has finished. Concurrent callers wait for the same
      walk; they never start a second one. Blocking here, rather than
      returning a "not ready" answer, keeps every API response unchanged: an
      empty answer would read as "nothing cached", exactly the wrong picture
      this package removes. The first call blocks once per process; the
      web client already shows a loading state for a slow first fetch
      (WEB-FIX-20.2).

    **``invalidate()`` blocks the next read, on purpose.** It is called right
    after the API itself changed the disk (a finished prefill job, a deletion,
    a GC run, a repair), and the callers' contract is that the next read shows
    the change: the deletion endpoint's response is followed by a Library
    refresh that must not show the deleted game's old size as if it were
    current, and a stale size right after "delete" looks like the deletion
    failed. Serving the pre-change snapshot and refreshing in the background
    would break that for one TTL window. ``invalidate()`` itself starts no
    walk: the worker invalidates after every successful job, no-op ones
    included, and an eager re-walk per job would walk the whole tree back to
    back during a scheduled run, the very disk contention this package
    removes. The walk starts when someone reads. A walk that was already
    running when ``invalidate()`` was called started before the change; its
    result is stored but never served, and the waiting reader starts a new
    walk once it ends.

    **Threading.** Every route calling ``get()`` is a sync ``def`` that FastAPI
    runs in its threadpool, so a blocking ``get()`` never blocks the event
    loop; only the cold first call and the read right after an invalidation
    block at all. The walk always runs on its own daemon thread, at most one
    at a time (bounded: no per-request threads). A cancelled request only
    stops waiting; the walk finishes and stores its result under the lock, so
    no state is ever half-updated. A walk that raises keeps the previous
    snapshot (still served, still expired, so the next read tries again) and
    logs the error; a caller blocked on that walk gets ``SizeWalkError``.

    Each finished walk logs one INFO line with its duration and the depot,
    directory and file counts, so the next cold test can measure it.

    ``walk`` is injectable for tests (a fake slow walk driven by events);
    production uses ``walk_depot_tree``.
    """

    def __init__(
        self,
        ttl_seconds: float = DEFAULT_SIZE_CACHE_TTL_SECONDS,
        *,
        walk: Callable[[str], DepotWalk] | None = None,
    ) -> None:
        self._ttl = ttl_seconds
        self._walk = walk if walk is not None else walk_depot_tree
        self._cond = threading.Condition()
        self._snapshot: SizeSnapshot | None = None
        self._snapshot_root: str | None = None
        self._snapshot_generation = -1
        # Bumped by invalidate(); a snapshot is only usable when it was
        # produced by a walk that started in the current generation.
        self._generation = 0
        self._walking = False
        self._walk_root: str | None = None
        self._walk_generation = -1
        self._walks_finished = 0
        # Outcome of the most recently finished walk: its error (None on
        # success) and which (root, generation) it was for.
        self._last_error: BaseException | None = None
        self._last_walk_key: tuple[str, int] | None = None

    def get(self, cache_root: str, *, now: float | None = None) -> SizeSnapshot:
        """Return the cached snapshot; see the class docstring for when it
        is stale and when this blocks.

        ``now`` is only ever overridden by tests (to exercise TTL expiry
        without a real sleep) — production callers always get the live clock.
        """
        current = time.monotonic() if now is None else now
        with self._cond:
            seen_finished = self._walks_finished
            while True:
                snapshot = self._snapshot
                if (
                    snapshot is not None
                    and self._snapshot_root == cache_root
                    and self._snapshot_generation == self._generation
                ):
                    # `not (age < ttl)` rather than `age >= ttl`: same
                    # freshness test as before, nan included.
                    if not (current - snapshot.computed_at) < self._ttl:
                        try:
                            self._start_walk_locked(cache_root, current, reason="expired")
                        except Exception:  # noqa: BLE001 - the stale answer is still good
                            logger.exception("Could not start a background depot size walk")
                    return snapshot

                if not self._walking:
                    if (
                        self._walks_finished > seen_finished
                        and self._last_error is not None
                        and self._last_walk_key == (cache_root, self._generation)
                    ):
                        raise SizeWalkError(
                            f"depot size walk of {cache_root} failed"
                        ) from self._last_error
                    if self._snapshot is None:
                        reason = "first"
                    elif self._snapshot_root != cache_root:
                        reason = "root-changed"
                    else:
                        reason = "invalidated"
                    # Raises (to this caller) if no thread can be started.
                    self._start_walk_locked(cache_root, current, reason=reason)
                # Either the walk we need is running, or another one (older
                # generation or another root) is; its completion notifies us
                # and the loop re-checks.
                self._cond.wait()

    def invalidate(self) -> None:
        """Mark the snapshot as pre-change: the next ``get()`` blocks until a
        walk that started after this call has finished. Starts no walk
        itself (see the class docstring)."""
        with self._cond:
            self._generation += 1

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Block until no walk is running; ``False`` on timeout. For tests,
        never on a request path."""
        with self._cond:
            return self._cond.wait_for(lambda: not self._walking, timeout)

    def _start_walk_locked(self, cache_root: str, started_at: float, *, reason: str) -> None:
        if self._walking:
            return  # single-flight
        self._walking = True
        self._walk_root = cache_root
        self._walk_generation = self._generation
        try:
            threading.Thread(
                target=self._run_walk,
                args=(cache_root, self._generation, started_at, reason),
                name="size-cache-walk",
                daemon=True,
            ).start()
        except BaseException:
            # No thread means no completion to clear the flag or wake anyone:
            # undo it and wake the other waiters (they re-check and try to
            # start a walk themselves), or every later reader would hang.
            self._walking = False
            self._cond.notify_all()
            raise

    def _run_walk(self, cache_root: str, generation: int, started_at: float, reason: str) -> None:
        clock_start = time.monotonic()
        result: DepotWalk | None = None
        error: BaseException | None = None
        try:
            result = self._walk(cache_root)
        except BaseException as exc:  # noqa: BLE001 - must always release waiters
            error = exc
        duration = time.monotonic() - clock_start

        with self._cond:
            self._walking = False
            self._walks_finished += 1
            self._last_walk_key = (cache_root, generation)
            if result is not None:
                self._last_error = None
                self._snapshot = SizeSnapshot(
                    computed_at=started_at,
                    depot_bytes=result.depot_bytes,
                    total_bytes=sum(result.depot_bytes.values()),
                )
                self._snapshot_root = cache_root
                self._snapshot_generation = generation
                logger.info(
                    "Depot size walk (%s) of %s took %.3f s: %d depots, %d dirs, %d files",
                    reason,
                    cache_root,
                    duration,
                    len(result.depot_bytes),
                    result.dir_count,
                    result.file_count,
                )
            else:
                self._last_error = error
                logger.error(
                    "Depot size walk (%s) of %s failed after %.3f s; keeping the previous snapshot",
                    reason,
                    cache_root,
                    duration,
                    exc_info=error,
                )
            # If invalidate() arrived while this walk ran, its result is stored
            # under the old generation and never served; a reader that needs
            # the current generation starts the next walk itself.
            self._cond.notify_all()


# --------------------------------------------------------------------------
# Per-app / summary aggregation
# --------------------------------------------------------------------------


def app_size_bytes(depotids: Iterable[int], depot_bytes: dict[int, int]) -> int | None:
    """Sum of the given depot ids' bytes, or ``None`` if unmapped/uncached.

    Plan §3/§6: an app's size is the sum of its mapped depots. A depot shared
    with another tracked app (redistributables, plan §4) still counts its
    full size into THIS app's total — per-app sizes may therefore sum to more
    than the actual disk usage reported by the cache summary's ``total_bytes``
    (which counts each depot exactly once). That is documented behaviour, not
    a bug: it answers "how much would deleting just this game free up if its
    depots weren't shared", which is what an operator deciding what to delete
    actually wants to know, while ``total_bytes`` answers "how much disk is
    actually used".

    Returns ``None`` (not ``0``) in two distinct cases the API must not
    confuse:
    - **unmapped**: no depot ids at all (``depot_count == 0``) — there is
      nothing to sum.
    - **uncached**: depots are mapped, but none of them have ever been
      written to disk (a fresh mapping before the first successful prefill)
      — the size is unknown, not zero.
    A partially-cached app (some depots on disk, others not yet) returns the
    sum of what IS on disk; treating a missing depot as 0 bytes there is
    correct (it hasn't contributed any bytes yet), not a data gap.
    """
    ids = list(depotids)
    if not ids:
        return None

    total = 0
    any_cached = False
    for depotid in ids:
        size = depot_bytes.get(depotid)
        if size is not None:
            any_cached = True
            total += size
    return total if any_cached else None


@dataclass(frozen=True)
class TopConsumer:
    appid: int
    name: str | None
    size_bytes: int


@dataclass(frozen=True)
class UnmappedDepots:
    """Depot directories on disk with no mapping row for any app — real
    operator information: either a mapping was deleted/never created, or a
    LAN client's store-on-miss wrote a depot SteamPrefill hasn't attributed
    yet (see ``vault_api/prefill.py``'s "concurrent cache writes" caveat)."""

    count: int
    size_bytes: int


@dataclass(frozen=True)
class CacheSummary:
    #: Actual disk usage of depot/, each depot counted exactly once.
    total_bytes: int
    top_consumers: list[TopConsumer] = field(default_factory=list)
    unmapped_depots: UnmappedDepots = field(default_factory=lambda: UnmappedDepots(0, 0))
    #: None if the cache filesystem's free space could not be determined
    #: (e.g. VAULT_CACHE_ROOT and every ancestor directory is missing).
    free_disk_bytes: int | None = None


def free_disk_bytes(cache_root: str) -> int | None:
    """Free space on the filesystem backing ``cache_root``.

    ``shutil.disk_usage`` requires an existing path; a fresh install may not
    have created ``VAULT_CACHE_ROOT`` yet (nothing prefilled so far), so this
    walks up to the nearest existing ancestor directory rather than raising.
    Returns ``None`` if no ancestor exists at all (defensive — should not
    happen on a real filesystem, every path has a root).
    """
    path = os.path.abspath(cache_root)
    while not os.path.exists(path):
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent
    try:
        return shutil.disk_usage(path).free
    except OSError:  # pragma: no cover - defensive
        logger.warning("Could not determine free disk space for %s", path)
        return None


def build_cache_summary(
    conn: sqlite3.Connection,
    cache_root: str,
    snapshot: SizeSnapshot,
    top_n: int = 10,
) -> CacheSummary:
    """Assemble ``GET /v1/cache/summary`` (plan §6): total usage, top
    consumers, unmapped depots, free disk space.

    All app/depot data comes from two small queries against the already-open
    connection.

    **Takes a ready ``SizeSnapshot``, not the ``SizeCache``** (WP 1.6
    carry-over fix #2 from the WP 1.5 review): a call with no usable
    snapshot waits for a whole ``depot/`` walk, and doing that from inside
    this function meant the caller's SQLite connection sat open for the
    duration. The caller now takes the snapshot *before* opening its
    connection (``routers/cache.py``), which also makes this function purely
    a function of its inputs: no clock, no filesystem walk, no cache state.
    """
    depot_bytes = snapshot.depot_bytes

    mapping_rows = conn.execute("SELECT appid, depotid FROM depot_app_map").fetchall()
    app_depotids: dict[int, list[int]] = {}
    mapped_depotids: set[int] = set()
    for row in mapping_rows:
        appid = int(row["appid"])
        depotid = int(row["depotid"])
        app_depotids.setdefault(appid, []).append(depotid)
        mapped_depotids.add(depotid)

    # Plain "give me all app names" + a dict lookup, rather than a dynamically
    # built IN (?,?,?) list with an empty-set special case (WP 1.6 carry-over
    # fix #2 from the WP 1.5 review — that ternary produced two different SQL
    # strings and needed a "WHERE 0" branch for the empty case). The apps table
    # has one row per tracked game: a homelab library is hundreds of rows, and
    # the mapping query above already reads more than this.
    names = {
        int(row["appid"]): row["name"]
        for row in conn.execute("SELECT appid, name FROM apps").fetchall()
    }

    consumers: list[TopConsumer] = []
    for appid, depotids in app_depotids.items():
        size = app_size_bytes(depotids, depot_bytes)
        if size is None:
            continue
        consumers.append(TopConsumer(appid=appid, name=names.get(appid), size_bytes=size))
    # Sort by size desc; break ties by appid for a deterministic order.
    consumers.sort(key=lambda c: (-c.size_bytes, c.appid))
    top_consumers = consumers[:top_n]

    unmapped_ids = set(depot_bytes) - mapped_depotids
    unmapped = UnmappedDepots(
        count=len(unmapped_ids),
        size_bytes=sum(depot_bytes[depotid] for depotid in unmapped_ids),
    )

    return CacheSummary(
        total_bytes=snapshot.total_bytes,
        top_consumers=top_consumers,
        unmapped_depots=unmapped,
        free_disk_bytes=free_disk_bytes(cache_root),
    )
