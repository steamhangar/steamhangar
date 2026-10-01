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
2. ``SizeCache`` — the in-process TTL cache plan §3 asks for ("du ... cached").
   Deliberately the simplest thing that works (plan §9): one lock, one cached
   snapshot, no background thread, no extra table. Explicit invalidation
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
from typing import Iterable, Iterator

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


def walk_file_stats(path: str) -> Iterator[os.stat_result]:
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
    """
    try:
        entries = list(os.scandir(path))
    except OSError:
        return
    for entry in entries:
        try:
            if entry_is_link_like(entry):
                continue
            if entry.is_dir(follow_symlinks=False):
                yield from walk_file_stats(entry.path)
            elif entry.is_file(follow_symlinks=False):
                yield entry.stat()
        except OSError:
            continue


def scan_depot_signatures(cache_root: str) -> dict[int, DepotSignature]:
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
        for stat in walk_file_stats(entry.path):
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
    ``app_size_bytes``)."""

    computed_at: float
    depot_bytes: dict[int, int]
    total_bytes: int


class SizeCache:
    """In-process TTL cache over one ``cache_root``'s depot byte totals.

    Deliberately the simplest thing that could work (plan §9): one lock, one
    cached snapshot, no background thread, no second table. A cache miss (TTL
    expired or never computed) walks the whole ``depot/`` tree once while
    holding the lock, so every concurrent caller that arrives during that walk
    waits for and then reuses the same fresh result instead of each re-walking
    the tree — the lock doubles as request coalescing, which is a feature
    here, not a downside (this is also what keeps it concurrency-safe under
    parallel/cancelled requests: a cancelled HTTP request just stops waiting
    on the lock, it can't corrupt or half-update the cached snapshot).

    Invalidation is explicit, not polled: call ``invalidate()`` right after an
    operation that changed what's on disk. ``vault_api/worker.py`` does this
    after a successful prefill job; WP 1.6's deletion endpoint will do the
    same — this method is exported for exactly that reuse.
    """

    def __init__(self, ttl_seconds: float = DEFAULT_SIZE_CACHE_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._snapshot: SizeSnapshot | None = None

    def get(self, cache_root: str, *, now: float | None = None) -> SizeSnapshot:
        """Return a snapshot no older than ``ttl_seconds``, recomputing if stale.

        ``now`` is only ever overridden by tests (to exercise TTL expiry
        without a real sleep) — production callers always get the live clock.
        """
        current = time.monotonic() if now is None else now
        with self._lock:
            if self._snapshot is not None and (current - self._snapshot.computed_at) < self._ttl:
                return self._snapshot

            depot_bytes = scan_depot_dir_bytes(cache_root)
            snapshot = SizeSnapshot(
                computed_at=current,
                depot_bytes=depot_bytes,
                total_bytes=sum(depot_bytes.values()),
            )
            self._snapshot = snapshot
            return snapshot

    def invalidate(self) -> None:
        """Drop the cached snapshot so the next ``get()`` recomputes from disk."""
        with self._lock:
            self._snapshot = None


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
    carry-over fix #2 from the WP 1.5 review): a cache miss walks the whole
    ``depot/`` tree while holding ``SizeCache``'s lock, and doing that from
    inside this function meant the caller's SQLite connection sat open — and
    every other request waiting on that same lock sat behind a disk walk —
    for the duration. The caller now takes the snapshot *before* opening its
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
