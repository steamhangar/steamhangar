"""Garbage-collection CORE: chunk-set resolution and planning (WP 3.7, ADR-0007).

**Nothing in this module mutates anything.** No file is written, renamed or
removed; no database row is inserted or updated. Every function here reads
(the database via small loaders at the bottom, the cache tree and the manifest
archive via ``os.scandir``/the WP 3.1 parsers) and returns a *plan*. WP 3.8
executes that plan through the WP 1.6 guard path. That split is deliberate:
the dangerous half of GC is deciding **which chunks are not needed any more**,
and that decision has to be inspectable — as a dry run, in a test, in a review
— before anything is allowed to act on it.

## What GC does, in one sentence (ADR-0007)

> For one depot, the chunks worth keeping are the UNION of the current
> manifests of every app that has a claim on that depot; everything else in
> that depot's ``chunk/`` directory is an orphan left behind by a game update.

The three layers:

1. ``scan_depot_chunks`` / ``scan_stored_manifests`` — what is actually on
   disk for one depot: its chunk files (id -> byte size) and its cache-stored
   manifest copies. Pure filesystem reads, no interpretation.
2. ``resolve_depot_chunkset`` — the keep set for ONE depot: classify every
   mapped app, resolve each counting app's current manifest through the
   source chain below, union the chunk ids. **This is where the readiness
   gate lives.**
3. ``plan_gc`` — per depot of one app: keep set vs. on-disk, orphans, exact
   byte totals, duplicate-stored-manifest candidates, and a status per depot.

## The manifest source chain (item 1 of the work package)

For one (app, depot) pair, in order, first success wins:

1. ``depot_manifests`` (WP 3.2) — vault-api's own record of *which* manifest
   id it currently believes is this app's manifest for this depot. This is an
   ID, not a chunk set, so it only selects which manifest to read:
   a. the archived ``.bin`` copy (``vault_api.manifest_archive``,
      ``{depotid}_{manifestid}.bin``) — parsed with
      ``manifests.parse_bin_payload``;
   b. failing that, a cache-stored client manifest for that same manifest id
      (``depot/<id>/manifest/<manifestid>/5/<requestcode>``) — parsed with
      ``manifests.parse_cache_manifest``.
2. No ``depot_manifests`` row at all — the app has cache content but vault-api
   never ingested a manifest for it (a store-on-miss fill by a LAN client, a
   database restored without its archive): fall back to the **newest**
   cache-stored manifest of the depot.

**GC never falls back from a newer manifest it cannot read to an older one it
can.** If the recorded manifest id is known but neither source yields its
chunk set, the app is unresolved and the whole depot is skipped — reading an
older manifest instead would produce a keep set that is missing exactly the
chunks the current version needs, which is the one failure mode a garbage
collector must never have. The same rule applies inside the fallback: the
newest stored manifest id is either read or the app is unresolved; an older
one is never silently substituted.

## Extra keep-set manifests (WP 3.9, ADR-0007 beta addendum, decision B)

``resolve_depot_chunkset`` accepts ``extra_manifest_ids``: manifest ids that
are **not** any app's current manifest but whose chunks must survive anyway.
Today the only producer is the opt-in manifest oracle
(``vault_api.oracle.gc_keepset_gids``), supplying the gids of open
(non-passworded) beta branches — content that reached the cache through a real
client's store-on-miss download and appears in no ``public`` manifest.

Three properties make this safe to bolt onto a deletion path:

- **Additive only.** They are unioned into the keep set *after* the readiness
  gate has already passed. A keep set can only grow, so the orphan set with
  extras is always a subset of the orphan set without them — an oracle that
  is wrong, poisoned or absent can never cause a deletion, only fail to
  prevent one (which is the pre-WP-3.9 baseline).
- **They never gate.** An extra id that cannot be resolved is reported in
  ``ChunkSetResolution.extras`` and otherwise ignored. It does NOT skip the
  depot: a beta branch whose manifest was never cached is the *normal* case,
  and gating on it would freeze GC for every app that ever had a beta branch —
  the permanent-block leak this ADR's own addendum refused elsewhere.
- **Same validation as everything else here.** Each id goes through
  ``valid_manifest_id`` before it becomes a path component, and the count is
  bounded by ``MAX_EXTRA_MANIFESTS``.

## The uncached-app decision (item 1's explicit reconciliation)

Two accepted documents point in opposite directions for one specific case: a
depot mapped to an app that has **no cache content and no recorded manifest**.

- ADR-0007's readiness gate says "if any mapped app has no resolvable
  manifest, the depot is skipped — never GC on partial knowledge".
- ADR-0003's addendum (2026-08-06, implemented in WP 3.6) established the
  opposite direction for the same kind of app: a co-owner *without cache
  content does not protect bytes*, because mapping rows survive deletion and
  a rule keyed on them alone leaks a shared depot's bytes forever once every
  co-owner has been deleted. Its closing line hands the question here
  explicitly: "for consistency, an uncached mapped app should not pin chunks
  in a shared depot's keep set; decide when GC is built."

**Decision, and the reasoning chain behind it:**

An app that is verifiably idle, never prefilled, job-free AND has no
``depot_manifests`` row is **excluded from the union requirement** — it
neither contributes chunks to the keep set nor blocks the depot on the
readiness gate.

1. What the gate is *for*: ADR-0007's gate protects against acting on
   **partial** knowledge — an app we know has content on disk but whose
   manifest we cannot read. The danger it addresses is deleting chunks that
   something on disk currently needs.
2. An uncached app has, by the same conservative predicate WP 3.6 already
   uses (``deletion._has_cache_content``), *nothing on disk to protect*. It is
   not partial knowledge; it is complete knowledge of an empty claim.
3. Treating it as a blocker would reproduce exactly the WP 3.6 leak in a new
   place: one never-prefilled app mapped to a shared depot would freeze GC for
   that depot forever, and nothing would ever reclaim the orphans — with no
   operator action available to fix it, because mapping rows survive by
   design (ADR-0003 main decision).
4. The direction is fail-closed anyway, because the predicate is: unknown
   status, a missing ``apps`` row, an ``error`` state, a set
   ``last_prefill_at`` or a queued/running job all resolve to "has content" ⇒
   the app counts ⇒ if its manifest is unresolvable, the depot is skipped.
   **Only the verifiably-empty case is excluded.**
5. Symmetrically: an app that *does* have a ``depot_manifests`` row always
   counts, even if it has no cache content — a recorded manifest is a claim
   vault-api itself wrote down, and an unreadable claim is exactly the partial
   knowledge the gate exists for.

**Sub-decision, flagged separately: a depot with ZERO counting apps is
skipped, not emptied.** If every mapped app is excluded by the rule above,
the union is empty and a naive reading would make every byte in the depot an
orphan. This module refuses that (``skipped_no_counting_apps``). The
exclusion rule exists so an uncached co-owner cannot *block* a depot that has
other, resolvable claims; it is not an authorisation to wipe a depot nobody
has any knowledge about. That case is already owned by
``DELETE /v1/cache/{appid}``'s last-remnant rule (ADR-0003 addendum), which
deletes it with an execute-time recheck and ``needs_force`` bookkeeping GC
does not have. A second, weaker deletion path for the same state is not
something this module is going to grow.

## Fail-closed guarantees this module makes (each pinned by a named test)

- ``DepotGcPlan.orphan_chunks`` is non-empty **only** when
  ``status == STATUS_PLANNED``. Every skip reason yields an empty orphan set,
  so WP 3.8 deleting "whatever the plan says" deletes nothing on a skip.
- Only files directly inside ``depot/<id>/chunk/`` are ever orphan
  candidates. The depot's ``manifest/`` subtree — and anything else under the
  depot directory — is structurally out of reach of the orphan set. A GC that
  walked the whole depot directory would classify every stored manifest as an
  orphan (their filenames are request codes, not chunk ids).
- An orphan candidate's filename must be exactly 40 lowercase hex characters
  (``manifests.CHUNK_ID_HEX_LEN``). Anything else in ``chunk/`` — a
  subdirectory, a link, an uppercase or truncated name, a temp file — is
  reported as *unrecognised* and never planned for deletion. This also means
  every id in ``orphan_chunks`` is safe to join onto a directory path by
  construction: WP 3.8 needs no further validation of it.
- A link-like entry is never an orphan candidate (``sizes.entry_is_link_like``
  — a junction is not ``islink()``, WP 1.6).
- Every id that reaches a filesystem path here is validated first: depot ids
  through ``deletion.coerce_positive_id``/``deletion.depot_dir_path`` (the
  reviewed WP 1.6 guards, reused rather than re-implemented) and manifest ids
  through ``valid_manifest_id`` (ASCII digits only — a poisoned
  ``depot_manifests.manifestid`` such as ``'../../etc'`` never becomes a path
  component).

## Cost

Set-based throughout. One ``scandir`` per depot ``chunk/`` directory, one per
``manifest/`` subtree, and at most one parse per distinct manifest file
(memoised per plan run, so two apps sharing a depot's manifest parse it once).
The orphan computation is a dict/set difference — no per-chunk loop over the
keep set, which matters at the real sizes involved (72283 chunks in the
largest manifest observed while researching Phase 3).
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from vault_api import deletion
from vault_api.manifests import (
    CHUNK_ID_HEX_LEN,
    SOURCE_CACHE_MANIFEST,
    ManifestParseError,
    ParsedManifest,
    parse_bin_payload,
    parse_cache_manifest,
)
from vault_api.manifest_archive import archive_filename
from vault_api.sizes import entry_is_link_like, is_link_like

logger = logging.getLogger(__name__)

#: Subdirectories of ``depot/<depotid>/`` this module knows about (plan §4's
#: storage layout). ``CHUNK_DIRNAME`` is the ONLY directory orphan candidates
#: can come from.
CHUNK_DIRNAME = "chunk"
MANIFEST_DIRNAME = "manifest"

#: The one subdirectory level between a manifest id and its stored copies:
#: ``/depot/<id>/manifest/<manifestid>/5/<requestcode>`` (the literal ``5`` is
#: part of Steam's own CDN URL, observed on every stored manifest in the
#: Phase-0 PoC cache and in docs/research/phase3-manifests.md §2).
#:
#: If Steam ever changes it, stored manifests simply become invisible to this
#: module — which is fail-closed, not dangerous: manifest files are never
#: orphan candidates in the first place, so the only consequence is that a
#: fallback source disappears and affected depots report
#: ``skipped_no_manifest`` instead of being GC'd on a guess.
MANIFEST_REQUEST_DIR = "5"

#: Steam manifest ids are unsigned 64-bit decimals — 20 digits at most
#: (``2**64 - 1`` = 18446744073709551615). Used to bound
#: ``valid_manifest_id``: the value comes from a TEXT column and from
#: directory names on disk, i.e. from two places a corrupted or hostile
#: environment can write anything into.
MAX_MANIFEST_ID_DIGITS = 20

#: How many unrecognised ``chunk/`` entry names a depot report carries as
#: examples before summarising the rest as a count. Same bound and same
#: reasoning as ``manifests.py``'s zip-namelist truncation and
#: ``manifest_ingest.MAX_LOGGED_NAMES``: a depot full of junk must not turn a
#: job's log excerpt into megabytes.
MAX_REPORTED_NAMES = 10

#: How many ``extra_manifest_ids`` one depot may contribute to the keep set
#: (WP 3.9). Each one costs a parse of a multi-megabyte manifest, and the list
#: comes from a database table an operator can edit — a poisoned table must
#: turn into a bounded amount of work plus a report, not into a plan run that
#: never finishes. A real app has a handful of open branches.
MAX_EXTRA_MANIFESTS = 32

_HEX_LOWERCASE = frozenset("0123456789abcdef")


# --------------------------------------------------------------------------
# Statuses
# --------------------------------------------------------------------------

#: The keep set was resolved and orphans computed. **The only status under
#: which ``orphan_chunks`` may be non-empty.**
STATUS_PLANNED = "planned"

#: The mapping row's depot id is not a usable positive integer (poisoned
#: database — SQLite's INTEGER affinity does not enforce the type). Nothing is
#: touched and the raw value is reported so the row can be repaired.
STATUS_UNUSABLE_DEPOTID = "skipped_unusable_depotid"

#: ``depot/<depotid>/`` does not exist on disk. Nothing is cached for this
#: depot, so there is nothing to reclaim — reported rather than silently
#: dropped, because "the mapping says this depot exists and the disk
#: disagrees" is information an operator wants.
STATUS_MISSING_DIR = "skipped_missing_dir"

#: No usable ``depot_app_map`` row names this depot. Unknown ownership never
#: resolves to "free to delete" (the WP 1.6 rule, applied to GC).
STATUS_UNMAPPED = "skipped_unmapped"

#: At least one mapping row for this depot has an unreadable app id, so the
#: set of apps with a claim on it is unknown. Mirrors ``plan_deletion``'s
#: unconditional protection of a depot with an unreadable co-owner row.
STATUS_UNREADABLE_OWNER = "skipped_unreadable_owner"

#: Every mapped app is verifiably uncached with no recorded manifest, so the
#: union has no contributors at all. See the module docstring's sub-decision:
#: this is skipped, not treated as "everything is an orphan".
STATUS_NO_COUNTING_APPS = "skipped_no_counting_apps"

#: ADR-0007's readiness gate fired: at least one counting app's current
#: manifest could not be resolved from any source.
STATUS_NO_MANIFEST = "skipped_no_manifest"

#: ``depot/<depotid>/`` itself is a symlink or junction (WP SEC-FIX-4, S-2).
#: vault-core shares the volume and could have planted it to point GC at a
#: directory only vault-api can reach. GC never descends through it.
#: (``DELETE /v1/cache/{appid}`` keeps its own unlink-the-link behaviour.)
STATUS_LINKED_DIR = "skipped_link_like_dir"

#: Every status a depot report can carry. Anything that is not
#: ``STATUS_PLANNED`` guarantees an empty orphan set.
ALL_STATUSES = (
    STATUS_PLANNED,
    STATUS_UNUSABLE_DEPOTID,
    STATUS_MISSING_DIR,
    STATUS_UNMAPPED,
    STATUS_UNREADABLE_OWNER,
    STATUS_NO_COUNTING_APPS,
    STATUS_NO_MANIFEST,
    STATUS_LINKED_DIR,
)

#: ``ManifestResolution.source`` for a manifest read out of vault-api's own
#: archive (``vault_api.manifest_archive``). The cache-stored counterpart
#: reuses ``manifests.SOURCE_CACHE_MANIFEST`` so the two names an operator
#: sees are the same ones the parser module already defines.
SOURCE_ARCHIVE = "archive"


# --------------------------------------------------------------------------
# Small validators (every value that becomes a path goes through one)
# --------------------------------------------------------------------------


def is_chunk_filename(name: str) -> bool:
    """True for exactly a 40-character lowercase hex chunk id.

    On-disk chunk filenames ARE the manifest's chunk ids (proven against
    ~12,000 real files, docs/research/phase3-manifests.md §2), and both
    parsers normalise ids to lowercase (``manifests.ParsedManifest``), so a
    case-sensitive comparison is the correct one — but only if this side is
    equally strict. Anything else is reported as unrecognised and never
    deleted: an uppercase or unusual name may well be a real chunk on a
    case-insensitive filesystem, and leaking a few bytes is always the right
    trade against deleting a file whose meaning we could not establish.
    """
    return len(name) == CHUNK_ID_HEX_LEN and all(c in _HEX_LOWERCASE for c in name)


def valid_manifest_id(value: object) -> str | None:
    """A manifest id usable as a filename component, or ``None``.

    The value arrives from ``depot_manifests.manifestid`` (a TEXT column,
    which SQLite does not type-enforce) and from directory names under
    ``depot/<id>/manifest/`` — two places a corrupted database or a hostile
    write can put anything at all, including ``'../..'``. Since GC then builds
    ``{archive_dir}/{depotid}_{manifestid}.bin`` and
    ``depot/<id>/manifest/<manifestid>/5/`` out of it, this is a path guard,
    not a formatting preference.

    Accepted: a positive ``int`` (a poisoned row can hold one despite the
    column being TEXT), or a ``str`` of ASCII digits with no leading zero, at
    most ``MAX_MANIFEST_ID_DIGITS`` long. ``bool`` is rejected explicitly
    (``True == 1``), as is anything ``int()`` would liberally accept —
    ``" 123 "``, ``"+123"``, ``"1_0"``, non-ASCII digits — for the same reason
    ``deletion.coerce_positive_id`` rejects them (docs/LEARNINGS.md).
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value) if value > 0 else None
    if not isinstance(value, str):
        return None
    if not value or len(value) > MAX_MANIFEST_ID_DIGITS:
        return None
    if not (value.isascii() and value.isdigit()):
        return None
    if value[0] == "0":  # "0" itself is not a manifest id either
        return None
    return value


# --------------------------------------------------------------------------
# Layer 1: what is on disk for one depot
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DepotChunkScan:
    """The contents of one depot's ``chunk/`` directory."""

    #: chunk id -> on-disk byte size. Only 40-hex-lowercase filenames.
    chunks: dict[str, int]
    #: Entry names in ``chunk/`` that are NOT usable chunk files (a
    #: subdirectory, a link, an unexpected name). Never orphan candidates —
    #: reported so an operator can look, bounded by ``MAX_REPORTED_NAMES``
    #: when it reaches the plan.
    unrecognized: list[str]
    #: Entries that vanished between the directory listing and their ``stat``
    #: (vault-core writing into this tree concurrently). Not a data problem
    #: and explicitly not "unrecognised" — same distinction
    #: ``manifest_ingest`` draws between a bad file and an I/O race.
    vanished_count: int
    #: False when ``chunk/`` itself does not exist (a depot that has only ever
    #: had manifests stored, or nothing at all).
    chunk_dir_exists: bool


def scan_depot_chunks(depot_dir: str) -> DepotChunkScan:
    """List ``<depot_dir>/chunk/`` — the only directory GC may propose to
    delete from.

    Uses ``os.scandir`` + ``DirEntry.stat()`` (the WP 1.4 measurement: 17x
    faster than ``os.walk`` + a separate ``stat`` per file on a 21k-file tree)
    and does **not** recurse: real chunk files sit flat in ``chunk/``, and a
    subdirectory there is unexplained content, not a place to hunt for more
    deletion candidates.

    Never raises. A missing/unreadable ``chunk/`` is an empty scan.
    """
    chunk_dir = os.path.join(depot_dir, CHUNK_DIRNAME)
    chunks: dict[str, int] = {}
    unrecognized: list[str] = []
    vanished = 0

    if is_link_like(chunk_dir):
        # S-2: a planted ``chunk -> /data`` would make every file there look
        # like an unclaimed orphan. Refused as "no chunk dir", loudly.
        logger.warning(
            "cache-gc: REFUSING %s -- it is a symlink or junction, not a "
            "directory; nothing in it is scanned or deleted", chunk_dir,
        )
        return DepotChunkScan(
            chunks={}, unrecognized=[], vanished_count=0, chunk_dir_exists=False
        )

    try:
        entries = os.scandir(chunk_dir)
    except OSError:
        return DepotChunkScan(
            chunks={}, unrecognized=[], vanished_count=0, chunk_dir_exists=False
        )

    with entries:
        for entry in entries:
            name = entry.name
            try:
                # Measured, not assumed (WP 3.7 mutation run): this check and
                # the is_file(follow_symlinks=False) check below are BOTH
                # sufficient on their own for every link kind that can appear
                # here — a Windows junction, a directory symlink and a file
                # symlink are all "not a regular file" to
                # is_file(follow_symlinks=False). Removing either one alone
                # leaves the link test green; removing both makes a junction
                # named like a valid chunk id become an orphan candidate.
                # Kept as defense in depth and as explicit intent, with the
                # redundancy stated rather than implied.
                if entry_is_link_like(entry):
                    unrecognized.append(name)
                    continue
                if not is_chunk_filename(name):
                    unrecognized.append(name)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    unrecognized.append(name)
                    continue
                chunks[name] = entry.stat().st_size
            except OSError:
                # Gone between listing and stat, or unreadable. Either way it
                # is not something to plan a deletion for.
                vanished += 1

    return DepotChunkScan(
        chunks=chunks,
        unrecognized=unrecognized,
        vanished_count=vanished,
        chunk_dir_exists=True,
    )


@dataclass(frozen=True)
class StoredManifestCopy:
    """One cache-stored manifest file:
    ``depot/<id>/manifest/<manifestid>/5/<requestcode>``.

    Manifest URLs carry a per-request code (Phase 0 finding, plan §4), so the
    SAME manifest legitimately lands on disk several times under different
    filenames — the dedupe opportunity ADR-0007 names.
    """

    manifestid: str
    path: str
    size_bytes: int
    mtime_ns: int


def scan_stored_manifests(depot_dir: str) -> dict[str, list[StoredManifestCopy]]:
    """``{manifestid: [copies, newest first]}`` for one depot.

    Directory names that are not valid manifest ids are ignored entirely
    (``valid_manifest_id``); so are link-like entries at every level —
    ``manifest/`` itself, ``manifest/<mid>/``, ``manifest/<mid>/5/`` and the
    copies inside (WP SEC-FIX-4 added the first and third; before it, a
    planted ``5 -> /data`` made files there dedupe candidates). Copies
    are sorted by mtime descending, with the filename descending as a
    deterministic tie-break so two runs over the same tree never disagree
    about which copy is "newest".

    **On mtime.** ``proxy_store`` stamps stored files with the upstream
    ``Last-Modified`` (ADR-0007's measured reason for rejecting time-based
    GC), so these mtimes are Steam's own publish times rather than fetch
    times. For *ordering manifests of one depot* that is a feature, not the
    trap it is for chunks: a manifest published later IS the newer manifest.
    It is still a heuristic — used only where vault-api has no recorded
    manifest id of its own to go by (source 2 in the module docstring) — and
    never overrides source 1.

    Never raises: a missing ``manifest/`` directory is an empty result.
    """
    manifest_root = os.path.join(depot_dir, MANIFEST_DIRNAME)
    found: dict[str, list[StoredManifestCopy]] = {}

    if is_link_like(manifest_root):
        logger.warning(
            "cache-gc: REFUSING %s -- it is a symlink or junction; no stored "
            "manifest under it is scanned or deduplicated", manifest_root,
        )
        return found

    try:
        manifest_entries = list(os.scandir(manifest_root))
    except OSError:
        return found

    for manifest_entry in manifest_entries:
        try:
            if entry_is_link_like(manifest_entry):
                continue
            manifestid = valid_manifest_id(manifest_entry.name)
            if manifestid is None or not manifest_entry.is_dir():
                continue
            request_dir = os.path.join(manifest_entry.path, MANIFEST_REQUEST_DIR)
            if is_link_like(request_dir):
                logger.warning(
                    "cache-gc: REFUSING %s -- it is a symlink or junction",
                    request_dir,
                )
                continue
            copy_entries = list(os.scandir(request_dir))
        except OSError:
            continue

        copies: list[StoredManifestCopy] = []
        for copy_entry in copy_entries:
            try:
                if entry_is_link_like(copy_entry):
                    continue
                if not copy_entry.is_file(follow_symlinks=False):
                    continue
                stat = copy_entry.stat()
            except OSError:
                continue
            copies.append(
                StoredManifestCopy(
                    manifestid=manifestid,
                    path=copy_entry.path,
                    size_bytes=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                )
            )

        if copies:
            copies.sort(key=lambda c: (c.mtime_ns, os.path.basename(c.path)), reverse=True)
            found[manifestid] = copies

    return found


@dataclass(frozen=True)
class DedupeCandidate:
    """Redundant copies of ONE (depot, manifest id) pair (ADR-0007 item 3).

    ``keep`` is the newest copy; ``duplicates`` is everything else stored for
    the same manifest id under a different request code.

    **Byte-identity is not verified here.** These files are the same manifest
    fetched with different per-request codes, so their content is expected to
    be identical, but this module reads directory structure and file
    stat data only — it does not hash or compare the bytes. ``size_bytes`` is
    carried on every copy precisely so WP 3.8 can decide how much it wants to
    verify before removing anything.

    Reported for every depot, including skipped ones, and independently of
    the keep set: a second copy of a manifest is redundant no matter which
    manifest is current, so this finding does not depend on the readiness
    gate. It is still only a *candidate* — nothing here deletes it.
    """

    manifestid: str
    keep: StoredManifestCopy
    duplicates: list[StoredManifestCopy]

    @property
    def reclaimable_bytes(self) -> int:
        return sum(copy.size_bytes for copy in self.duplicates)


def dedupe_candidates(
    stored: Mapping[str, Sequence[StoredManifestCopy]]
) -> list[DedupeCandidate]:
    """Every manifest id stored more than once, newest copy marked as kept."""
    candidates: list[DedupeCandidate] = []
    for manifestid in sorted(stored):
        copies = list(stored[manifestid])
        if len(copies) > 1:
            candidates.append(
                DedupeCandidate(
                    manifestid=manifestid, keep=copies[0], duplicates=copies[1:]
                )
            )
    return candidates


# --------------------------------------------------------------------------
# Layer 2: the keep set for ONE depot
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class MappedApp:
    """One app that ``depot_app_map`` says has a claim on the depot.

    Built by ``plan_gc`` from raw database values so that
    ``resolve_depot_chunkset`` stays free of SQL and is directly unit-testable
    with plain dataclasses — the same split ``deletion.plan_deletion`` uses.

    ``appid == 0`` means the mapping row's app id was unreadable (poisoned
    database), the same convention ``deletion.CoOwner`` uses. Such a row
    protects the whole depot unconditionally.

    ``has_content`` is the ADR-0003-addendum predicate, computed by
    ``deletion.load_co_owner_states`` (``True`` whenever anything is unknown:
    no ``apps`` row, a non-idle status, a set ``last_prefill_at``, a
    queued/running job).

    ``recorded_manifestid`` is the RAW ``depot_manifests.manifestid`` value if
    a row exists for this (app, depot) pair, else ``None``. Raw on purpose:
    the *presence* of a row is what makes the app count, while its
    *usability* is decided by ``valid_manifest_id`` — a row holding garbage is
    a claim vault-api cannot read, which must fire the gate rather than
    quietly disappear.
    """

    appid: int
    has_content: bool
    recorded_manifestid: object | None = None


@dataclass(frozen=True)
class ManifestResolution:
    """What happened when GC tried to resolve one app's current manifest."""

    appid: int
    #: Does this app contribute to the union / can it block the gate?
    counting: bool
    #: Why it does not count (only set when ``counting`` is False).
    excluded_reason: str | None = None
    #: The manifest id whose chunks were used, once resolved.
    manifestid: str | None = None
    #: ``SOURCE_ARCHIVE`` or ``manifests.SOURCE_CACHE_MANIFEST``.
    source: str | None = None
    #: Absolute path of the file that was parsed.
    path: str | None = None
    chunk_count: int | None = None
    #: One line per source that was tried and failed, in order. Non-empty on
    #: a failure, and possibly non-empty on a success too (the archive was
    #: missing, the cache-stored copy worked).
    errors: list[str] = field(default_factory=list)

    @property
    def resolved(self) -> bool:
        return self.chunk_count is not None


@dataclass(frozen=True)
class ExtraManifestResolution:
    """What happened to one ``extra_manifest_ids`` entry (WP 3.9, decision B).

    Purely informational: whatever this says, the depot's status is unchanged
    (see the module docstring — extras never gate). ``error`` is set when the
    id was unusable or its manifest could not be read anywhere, in which case
    that manifest's chunks simply keep the protection they had before, i.e.
    none beyond WP 3.8b's grace window.
    """

    #: The raw value as it arrived, so a poisoned row is reportable verbatim.
    raw: object
    manifestid: str | None = None
    source: str | None = None
    path: str | None = None
    chunk_count: int | None = None
    #: Chunks this manifest contributed that NO app manifest already covered —
    #: the honest measure of what decision B actually protected here.
    added_count: int = 0
    error: str | None = None

    @property
    def resolved(self) -> bool:
        return self.chunk_count is not None


@dataclass(frozen=True)
class ChunkSetResolution:
    """The keep set for one depot, plus how each app got there."""

    depotid: int
    #: ``STATUS_PLANNED``-eligible when the gate passed, else the skip reason.
    #: ``plan_gc`` turns the passing case into ``STATUS_PLANNED``.
    status: str
    #: chunk id -> the manifest's declared ``cb_compressed``. Empty for every
    #: non-passing status. Includes the extra manifests' chunks (WP 3.9).
    keep: dict[str, int]
    apps: list[ManifestResolution]
    note: str = ""
    #: One entry per ``extra_manifest_ids`` value that was considered (WP 3.9).
    extras: list[ExtraManifestResolution] = field(default_factory=list)
    #: Keep-set ids contributed ONLY by an extra manifest — i.e. exactly the
    #: chunks that would be orphans without the oracle. Empty unless the gate
    #: passed, like ``keep`` itself.
    extra_only_chunks: frozenset[str] = frozenset()

    @property
    def gate_passed(self) -> bool:
        return self.status == STATUS_PLANNED


#: ``ManifestResolution.excluded_reason`` for the module docstring's decision.
EXCLUDED_UNCACHED = (
    "no cache content (idle, never prefilled, job-free) and no depot_manifests "
    "row: contributes no manifest requirement and does not block the depot "
    "(ADR-0003 addendum consistency — see vault_api/gc.py's module docstring)"
)


class _ManifestReader:
    """Parse-once memo for one plan run.

    Two apps sharing a depot resolve the same manifest file; a depot appearing
    under several apps does too. Keyed by absolute path, and it caches
    failures as well as successes so a corrupt file is not re-read once per
    app either.
    """

    def __init__(self) -> None:
        self._cache: dict[str, ParsedManifest | ManifestParseError] = {}

    def _read(self, path: str, load) -> ParsedManifest | ManifestParseError:
        cached = self._cache.get(path)
        if cached is None:
            try:
                cached = load(path)
            except ManifestParseError as exc:
                cached = exc
            self._cache[path] = cached
        return cached

    def archived_bin(
        self, path: str, *, depotid: int, manifestid: str
    ) -> ParsedManifest | ManifestParseError:
        """Parse an archived ``{depotid}_{manifestid}.bin``, cross-checking the
        payload's own ids against the pair we asked for (the corruption signal
        ``parse_steamprefill_bin`` keeps for the ingestion path).

        ``manifestid`` arrives as a ``str`` here and as an ``int`` there, so
        it is converted — see the call site's comment for why that conversion
        cannot raise.
        """
        return self._read(
            path,
            lambda p: parse_bin_payload(
                p,
                filename_depot_id=depotid,
                # int() is safe ONLY because valid_manifest_id ran first: every
                # path into this method (`_resolve_app_manifest`, both the
                # recorded-id branch and the newest-stored-manifest fallback)
                # has already passed `manifestid` through `valid_manifest_id`,
                # which admits nothing but a leading-zero-free string of at
                # most 20 ASCII digits. Without that guarantee this would be
                # exactly the liberal-int() trap docs/LEARNINGS.md records
                # (" 4 ", "+4", "1_0", non-ASCII digits) — and a raw ValueError
                # escaping here would crash the plan run rather than being
                # reported as an unresolved manifest.
                filename_manifest_id=int(manifestid),
            ),
        )

    def cache_manifest(
        self, path: str, *, depotid: int, manifestid: str
    ) -> ParsedManifest | ManifestParseError:
        """Parse a cache-stored manifest and cross-check its ids against the
        directory path it was found under. The path claims a (depot,
        manifest) pair; a payload that disagrees is corrupt, and using it
        would attribute one manifest's chunks to another depot."""
        parsed = self._read(path, parse_cache_manifest)
        if isinstance(parsed, ManifestParseError):
            return parsed
        if parsed.depot_id != depotid or str(parsed.manifest_id) != manifestid:
            return ManifestParseError(
                f"cache manifest {path!r}: payload claims depot {parsed.depot_id} / "
                f"manifest {parsed.manifest_id}, but it is stored under depot "
                f"{depotid} / manifest {manifestid} (corruption signal)"
            )
        return parsed


def _try_stored_copies(
    reader: _ManifestReader,
    copies: Sequence[StoredManifestCopy],
    *,
    depotid: int,
    manifestid: str,
    errors: list[str],
) -> tuple[ParsedManifest, StoredManifestCopy] | None:
    """First copy (newest first) that parses cleanly, or ``None``.

    Trying every copy of the SAME manifest id is deliberate — a truncated
    store-on-miss write is a real possibility and another request code's copy
    of the same manifest is an equally valid source. Note what this does NOT
    do: it never moves on to a *different* manifest id.
    """
    for copy in copies:
        parsed = reader.cache_manifest(copy.path, depotid=depotid, manifestid=manifestid)
        if isinstance(parsed, ManifestParseError):
            errors.append(f"cache-stored {copy.path}: {parsed}")
            continue
        return parsed, copy
    return None


def resolve_depot_chunkset(
    depotid: int,
    mapped_apps: Sequence[MappedApp],
    *,
    archive_dir: str,
    stored_manifests: Mapping[str, Sequence[StoredManifestCopy]],
    reader: "_ManifestReader | None" = None,
    extra_manifest_ids: Sequence[object] = (),
) -> ChunkSetResolution:
    """The keep set for ONE depot: the UNION of every counting app's current
    manifest (ADR-0007 item 2), or a skip reason.

    ``stored_manifests`` is ``scan_stored_manifests``'s output for this depot
    and is a **required argument**, not something this function goes and reads
    itself: the caller has already walked that tree (``plan_gc``), and passing
    it in keeps the gate logic testable with a plain dict and no filesystem at
    all for the cases that never reach a parse.

    Order of decisions, each fail-closed:

    1. no ``mapped_apps`` at all -> ``STATUS_UNMAPPED``;
    2. any ``appid == 0`` (unreadable mapping row) -> ``STATUS_UNREADABLE_OWNER``;
    3. classify each app as counting or excluded (module docstring's
       decision);
    4. no counting apps -> ``STATUS_NO_COUNTING_APPS`` (the sub-decision:
       skipped, never "everything is an orphan");
    5. resolve every counting app's manifest; any failure ->
       ``STATUS_NO_MANIFEST``, with the per-source errors kept in that app's
       ``ManifestResolution.errors``;
    6. otherwise ``STATUS_PLANNED`` with the union — plus, last,
       ``extra_manifest_ids``'s chunks (WP 3.9 / ADR-0007 decision B). Step 7
       runs only on this path and can only *add* to ``keep``; it never changes
       the status and never turns a passing depot into a skipped one.

    ``keep`` is empty for every outcome except 6 — a caller that ignores the
    status and just uses ``keep`` therefore still deletes nothing, which is
    the point.
    """
    reader = reader if reader is not None else _ManifestReader()

    if not mapped_apps:
        return ChunkSetResolution(
            depotid=depotid,
            status=STATUS_UNMAPPED,
            keep={},
            apps=[],
            note=(
                "no usable depot_app_map row names this depot, so which apps have a "
                "claim on it is unknown; nothing was planned for it."
            ),
        )

    unreadable = [app for app in mapped_apps if app.appid <= 0]
    if unreadable:
        return ChunkSetResolution(
            depotid=depotid,
            status=STATUS_UNREADABLE_OWNER,
            keep={},
            apps=[
                ManifestResolution(appid=app.appid, counting=True)
                for app in sorted(mapped_apps, key=lambda a: a.appid)
            ],
            note=(
                f"{len(unreadable)} depot_app_map row(s) for this depot have an "
                "unreadable app id, so the set of apps with a claim on it is "
                "incomplete; nothing was planned for it. Repair the row with "
                "DELETE /v1/mapping/{depotid}/{appid} or directly in the database."
            ),
        )

    resolutions: list[ManifestResolution] = []
    counting: list[MappedApp] = []
    for app in sorted(mapped_apps, key=lambda a: a.appid):
        has_row = app.recorded_manifestid is not None
        if not has_row and not app.has_content:
            resolutions.append(
                ManifestResolution(
                    appid=app.appid, counting=False, excluded_reason=EXCLUDED_UNCACHED
                )
            )
            continue
        counting.append(app)

    if not counting:
        return ChunkSetResolution(
            depotid=depotid,
            status=STATUS_NO_COUNTING_APPS,
            keep={},
            apps=resolutions,
            note=(
                "every app mapped to this depot is verifiably uncached with no "
                "recorded manifest, so the keep set has no contributors. GC does "
                "NOT treat that as 'everything is an orphan' — reclaiming a "
                "last-remnant depot is DELETE /v1/cache/{appid}'s job (ADR-0003 "
                "addendum), which rechecks co-owners at execute time and sets "
                "needs_force; GC has neither."
            ),
        )

    keep: dict[str, int] = {}
    unresolved: list[int] = []
    for app in counting:
        resolution, parsed = _resolve_app_manifest(
            depotid,
            app,
            archive_dir=archive_dir,
            stored_manifests=stored_manifests,
            reader=reader,
        )
        resolutions.append(resolution)
        if parsed is None:
            unresolved.append(app.appid)
        else:
            keep.update(parsed.chunks)

    resolutions.sort(key=lambda r: r.appid)

    if unresolved:
        return ChunkSetResolution(
            depotid=depotid,
            status=STATUS_NO_MANIFEST,
            keep={},
            apps=resolutions,
            note=(
                "ADR-0007 readiness gate: no current manifest could be resolved for "
                f"app(s) {sorted(unresolved)}, which have a claim on this depot. "
                "GC never runs on partial knowledge — nothing was planned for it."
            ),
        )

    # Step 7 (WP 3.9): the extras. Deliberately AFTER the gate, so nothing
    # here can influence whether the depot is planned at all — only what is
    # kept once it is.
    extras, extra_only = _union_extra_manifests(
        depotid,
        extra_manifest_ids,
        keep=keep,
        archive_dir=archive_dir,
        stored_manifests=stored_manifests,
        reader=reader,
    )

    return ChunkSetResolution(
        depotid=depotid,
        status=STATUS_PLANNED,
        keep=keep,
        apps=resolutions,
        extras=extras,
        extra_only_chunks=extra_only,
    )


def _union_extra_manifests(
    depotid: int,
    extra_manifest_ids: Sequence[object],
    *,
    keep: dict[str, int],
    archive_dir: str,
    stored_manifests: Mapping[str, Sequence[StoredManifestCopy]],
    reader: _ManifestReader,
) -> tuple[list[ExtraManifestResolution], frozenset[str]]:
    """Add each extra manifest's chunks to ``keep`` (mutated in place).

    Returns the per-id report and the set of ids that were contributed
    **only** by an extra manifest. Never raises, never removes anything from
    ``keep``, never reports a status — see the module docstring's three
    properties.

    Duplicates are collapsed and the order is deterministic (sorted by the
    validated id) so two runs over the same data produce identical reports.
    """
    if not extra_manifest_ids:
        return [], frozenset()

    app_chunks = frozenset(keep)
    extras: list[ExtraManifestResolution] = []
    extra_only: set[str] = set()

    seen: set[str] = set()
    ordered: list[tuple[str | None, object]] = []
    for raw in extra_manifest_ids:
        validated = valid_manifest_id(raw)
        if validated is not None:
            if validated in seen:
                continue
            seen.add(validated)
        ordered.append((validated, raw))
    ordered.sort(key=lambda item: (item[0] is None, item[0] or ""))

    for validated, raw in ordered[:MAX_EXTRA_MANIFESTS]:
        if validated is None:
            extras.append(
                ExtraManifestResolution(
                    raw=raw,
                    error=(
                        f"unusable manifest id {raw!r} (expected a decimal Steam "
                        "manifest id); it protects nothing"
                    ),
                )
            )
            continue

        parsed, source, path, error = _read_manifest_by_id(
            depotid,
            validated,
            archive_dir=archive_dir,
            stored_manifests=stored_manifests,
            reader=reader,
        )
        if parsed is None:
            extras.append(
                ExtraManifestResolution(raw=raw, manifestid=validated, error=error)
            )
            continue

        added = 0
        for chunk_id, size in parsed.chunks.items():
            if chunk_id not in app_chunks:
                if chunk_id not in extra_only:
                    added += 1
                extra_only.add(chunk_id)
            keep.setdefault(chunk_id, size)
        extras.append(
            ExtraManifestResolution(
                raw=raw,
                manifestid=validated,
                source=source,
                path=path,
                chunk_count=len(parsed.chunks),
                added_count=added,
            )
        )

    dropped = len(ordered) - MAX_EXTRA_MANIFESTS
    if dropped > 0:
        extras.append(
            ExtraManifestResolution(
                raw=None,
                error=(
                    f"{dropped} further extra manifest id(s) ignored: more than "
                    f"{MAX_EXTRA_MANIFESTS} were supplied for this depot"
                ),
            )
        )

    return extras, frozenset(extra_only)


def _read_manifest_by_id(
    depotid: int,
    manifestid: str,
    *,
    archive_dir: str,
    stored_manifests: Mapping[str, Sequence[StoredManifestCopy]],
    reader: _ManifestReader,
) -> tuple[ParsedManifest | None, str | None, str | None, str | None]:
    """Read ONE known manifest id from the archive, else from the cache.

    Same source order and the same cross-checks as ``_resolve_app_manifest``'s
    recorded-id branch, minus the per-app bookkeeping — and, as there, never a
    fallback to a *different* manifest id. Returns
    ``(parsed, source, path, error)``.
    """
    errors: list[str] = []
    archive_path = os.path.join(
        archive_dir, archive_filename(depotid=depotid, manifestid=manifestid)
    )
    parsed = reader.archived_bin(archive_path, depotid=depotid, manifestid=manifestid)
    if not isinstance(parsed, ManifestParseError):
        return parsed, SOURCE_ARCHIVE, archive_path, None
    errors.append(f"archive {archive_path}: {parsed}")

    copies = stored_manifests.get(manifestid, ())
    if not copies:
        errors.append(
            f"cache-stored: no copy of manifest {manifestid} under "
            f"{MANIFEST_DIRNAME}/{manifestid}/{MANIFEST_REQUEST_DIR}/"
        )
        return None, None, None, "; ".join(errors)

    hit = _try_stored_copies(
        reader, copies, depotid=depotid, manifestid=manifestid, errors=errors
    )
    if hit is None:
        return None, None, None, "; ".join(errors)
    parsed, copy = hit
    return parsed, SOURCE_CACHE_MANIFEST, copy.path, None


def _resolve_app_manifest(
    depotid: int,
    app: MappedApp,
    *,
    archive_dir: str,
    stored_manifests: Mapping[str, Sequence[StoredManifestCopy]],
    reader: _ManifestReader,
) -> tuple[ManifestResolution, ParsedManifest | None]:
    """The source chain of the module docstring, for one counting app."""
    errors: list[str] = []
    raw = app.recorded_manifestid

    if raw is not None:
        recorded = valid_manifest_id(raw)
        if recorded is None:
            # A row exists but is unreadable. Deliberately NOT falling through
            # to the newest-stored-manifest guess: vault-api recorded a
            # manifest for this app and we cannot tell which, so any other
            # manifest's chunk set is a guess about the wrong thing.
            errors.append(
                f"depot_manifests row holds an unusable manifest id {raw!r} "
                "(expected a decimal Steam manifest id); repair the row"
            )
            return (
                ManifestResolution(
                    appid=app.appid, counting=True, errors=errors
                ),
                None,
            )

        archive_path = os.path.join(
            archive_dir, archive_filename(depotid=depotid, manifestid=recorded)
        )
        parsed = reader.archived_bin(archive_path, depotid=depotid, manifestid=recorded)
        if isinstance(parsed, ManifestParseError):
            errors.append(f"archive {archive_path}: {parsed}")
        else:
            return (
                ManifestResolution(
                    appid=app.appid,
                    counting=True,
                    manifestid=recorded,
                    source=SOURCE_ARCHIVE,
                    path=archive_path,
                    chunk_count=len(parsed.chunks),
                    errors=errors,
                ),
                parsed,
            )

        copies = stored_manifests.get(recorded, ())
        if not copies:
            errors.append(
                f"cache-stored: no copy of manifest {recorded} under "
                f"{MANIFEST_DIRNAME}/{recorded}/{MANIFEST_REQUEST_DIR}/"
            )
        else:
            hit = _try_stored_copies(
                reader, copies, depotid=depotid, manifestid=recorded, errors=errors
            )
            if hit is not None:
                parsed, copy = hit
                return (
                    ManifestResolution(
                        appid=app.appid,
                        counting=True,
                        manifestid=recorded,
                        source=SOURCE_CACHE_MANIFEST,
                        path=copy.path,
                        chunk_count=len(parsed.chunks),
                        errors=errors,
                    ),
                    parsed,
                )

        return ManifestResolution(appid=app.appid, counting=True, errors=errors), None

    # No recorded manifest id: this app counts because it has cache content.
    # Fall back to the depot's NEWEST cache-stored manifest — and only that
    # one (see the module docstring: never an older manifest instead).
    newest = _newest_stored_manifest_id(stored_manifests)
    if newest is None:
        errors.append(
            "no depot_manifests row for this app/depot and no cache-stored manifest "
            f"under {MANIFEST_DIRNAME}/*/{MANIFEST_REQUEST_DIR}/ to fall back to"
        )
        return ManifestResolution(appid=app.appid, counting=True, errors=errors), None

    copies = stored_manifests[newest]
    hit = _try_stored_copies(
        reader, copies, depotid=depotid, manifestid=newest, errors=errors
    )
    if hit is None:
        errors.append(
            f"newest cache-stored manifest {newest} could not be parsed from any of "
            f"its {len(copies)} stored cop(y/ies); GC does not fall back to an OLDER "
            "manifest, which would keep the wrong chunk set"
        )
        return ManifestResolution(appid=app.appid, counting=True, errors=errors), None

    parsed, copy = hit
    return (
        ManifestResolution(
            appid=app.appid,
            counting=True,
            manifestid=newest,
            source=SOURCE_CACHE_MANIFEST,
            path=copy.path,
            chunk_count=len(parsed.chunks),
            errors=errors,
        ),
        parsed,
    )


def _newest_stored_manifest_id(
    stored: Mapping[str, Sequence[StoredManifestCopy]]
) -> str | None:
    """The manifest id whose newest stored copy has the newest mtime.

    Tie-broken by manifest id (descending) so the choice is deterministic
    across runs and across filesystems with coarse timestamps.
    """
    best: tuple[int, str] | None = None
    for manifestid, copies in stored.items():
        if not copies:
            continue
        candidate = (copies[0].mtime_ns, manifestid)
        if best is None or candidate > best:
            best = candidate
    return None if best is None else best[1]


# --------------------------------------------------------------------------
# Layer 3: the plan
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DepotGcPlan:
    """What GC would do to ONE depot. Nothing here has happened yet."""

    depotid: int
    status: str
    #: chunk id -> its EXACT on-disk byte size (``DirEntry.stat().st_size``,
    #: not the manifest's declared ``cb_compressed``): this is what deleting
    #: it would really free. Guaranteed empty unless ``status`` is
    #: ``STATUS_PLANNED``.
    orphan_chunks: dict[str, int] = field(default_factory=dict)
    #: On-disk chunks that ARE in the keep set (count and their on-disk bytes).
    kept_count: int = 0
    kept_bytes: int = 0
    #: Size of the resolved keep set itself. Larger than ``kept_count`` when
    #: the depot is only partially cached — normal, not a finding.
    keep_set_count: int = 0
    #: Kept chunks whose on-disk size differs from the manifest's declared
    #: ``cb_compressed``. Expected to be 0 (proven byte-exact against ~12,000
    #: real files, research §2) — a non-zero count is a free corruption
    #: signal, and deliberately does NOT change what is planned.
    size_mismatch_count: int = 0
    #: ``chunk/`` entries that are not usable chunk files. Never deletable.
    unrecognized_count: int = 0
    unrecognized_examples: list[str] = field(default_factory=list)
    #: Entries that vanished mid-scan (concurrent vault-core writes).
    vanished_count: int = 0
    depot_dir_exists: bool = False
    chunk_dir_exists: bool = False
    apps: list[ManifestResolution] = field(default_factory=list)
    dedupe: list[DedupeCandidate] = field(default_factory=list)
    #: Human-readable reason, for the API response and the job log.
    note: str = ""
    #: WP 3.9: one entry per extra (oracle) manifest id considered here.
    extras: list[ExtraManifestResolution] = field(default_factory=list)
    #: On-disk chunks that would have been orphans WITHOUT the extra
    #: manifests — i.e. exactly what ADR-0007 decision B saved on this run.
    #: Reported (and logged) so an operator can see the oracle earning its
    #: keep instead of having to infer it from a smaller number.
    oracle_protected_count: int = 0
    oracle_protected_bytes: int = 0

    @property
    def orphan_count(self) -> int:
        return len(self.orphan_chunks)

    @property
    def orphan_bytes(self) -> int:
        return sum(self.orphan_chunks.values())

    @property
    def dedupe_bytes(self) -> int:
        return sum(candidate.reclaimable_bytes for candidate in self.dedupe)

    @property
    def extra_problems(self) -> list[ExtraManifestResolution]:
        """Extra manifest ids that could not be used. Never a failure — see
        ``ExtraManifestResolution`` — but worth surfacing: an oracle whose
        gids never resolve is protecting nothing."""
        return [extra for extra in self.extras if extra.error]


@dataclass(frozen=True)
class GcPlan:
    """The full dry run for ``POST /v1/cache/{appid}/gc`` (WP 3.8 executes it)."""

    appid: int
    depots: list[DepotGcPlan] = field(default_factory=list)

    @property
    def orphan_count(self) -> int:
        return sum(depot.orphan_count for depot in self.depots)

    @property
    def orphan_bytes(self) -> int:
        return sum(depot.orphan_bytes for depot in self.depots)

    @property
    def kept_count(self) -> int:
        return sum(depot.kept_count for depot in self.depots)

    @property
    def kept_bytes(self) -> int:
        return sum(depot.kept_bytes for depot in self.depots)

    @property
    def dedupe_bytes(self) -> int:
        return sum(depot.dedupe_bytes for depot in self.depots)

    @property
    def planned_depots(self) -> list[int]:
        return [d.depotid for d in self.depots if d.status == STATUS_PLANNED]

    @property
    def skipped_depots(self) -> list[int]:
        return [d.depotid for d in self.depots if d.status != STATUS_PLANNED]

    def status_counts(self) -> dict[str, int]:
        counts = {status: 0 for status in ALL_STATUSES}
        for depot in self.depots:
            counts[depot.status] = counts.get(depot.status, 0) + 1
        return counts


@dataclass(frozen=True)
class GcInputs:
    """Everything ``plan_gc`` needs out of the database, read by
    ``load_gc_inputs`` so the planner itself never touches SQL.

    Same shape of split as WP 1.6's ``plan_deletion`` + ``load_mapping_rows``
    + ``load_co_owner_states``: every branch of the planner is reachable from
    a test with plain dicts and tuples.
    """

    #: RAW ``(depotid, appid)`` pairs covering every app that maps any depot of
    #: the target app — ``deletion.load_mapping_rows``'s output, uncoerced so a
    #: poisoned row is data for the planner rather than an exception here.
    mapping_rows: list[tuple[object, object]]
    #: ``{appid: has_content}`` (ADR-0003 addendum predicate). A missing key
    #: means "no apps row", which the planner reads as ``True`` — protected.
    content_states: dict[int, bool]
    #: ``{(appid, depotid): raw manifestid}`` from ``depot_manifests``.
    recorded_manifests: dict[tuple[int, int], object]
    #: WP 3.9: ``{depotid: [extra manifest ids]}``. Empty (the default) means
    #: "no extra keep-set protection", which is precisely the behaviour before
    #: WP 3.9 — the oracle being off must be indistinguishable from it never
    #: having been built. Filled by ``vault_api.oracle.gc_keepset_gids``; this
    #: module deliberately does not know where the ids came from.
    oracle_manifests: dict[int, list[str]] = field(default_factory=dict)


def plan_gc(
    appid: int,
    inputs: GcInputs,
    *,
    depot_root: str,
    archive_dir: str,
) -> GcPlan:
    """Plan garbage collection for one app. **Reads only** — see the module
    docstring.

    ``depot_root`` is an already-resolved ``<cache_root>/depot`` (get it from
    ``deletion.resolve_depot_root``, which refuses an empty/rooted
    ``VAULT_CACHE_ROOT``); every per-depot path is then built through
    ``deletion.depot_dir_path``, so the WP 1.6 strict-child guard covers GC
    too instead of GC growing a second, less-reviewed path builder.

    Per depot, in order: validate the depot id, check the directory exists,
    scan ``chunk/`` and ``manifest/``, resolve the keep set
    (``resolve_depot_chunkset``), and subtract. Duplicate stored manifests are
    reported for every depot regardless of the gate (see ``DedupeCandidate``).
    """
    depots_owners: dict[int, list[MappedApp]] = {}
    own_depots: set[int] = set()
    unusable_reports: list[DepotGcPlan] = []
    seen_unusable: set[str] = set()

    for raw_depotid, raw_owner in inputs.mapping_rows:
        depotid = deletion.coerce_positive_id(raw_depotid)
        owner = deletion.coerce_positive_id(raw_owner)

        if depotid is None:
            # Only reportable as "this app's problem" when the row's own app id
            # is readable AND is this app: otherwise there is nothing tying the
            # broken row to the request at hand.
            if owner == appid:
                message = deletion.unusable_depotid_message(raw_depotid)
                if message not in seen_unusable:
                    seen_unusable.add(message)
                    unusable_reports.append(
                        DepotGcPlan(
                            depotid=0, status=STATUS_UNUSABLE_DEPOTID, note=message
                        )
                    )
            continue

        if owner == appid:
            own_depots.add(depotid)

        depots_owners.setdefault(depotid, []).append(
            MappedApp(
                appid=owner if owner is not None else 0,
                has_content=(
                    True
                    if owner is None
                    else inputs.content_states.get(owner, True)
                ),
                recorded_manifestid=(
                    None
                    if owner is None
                    else inputs.recorded_manifests.get((owner, depotid))
                ),
            )
        )

    reader = _ManifestReader()
    reports: list[DepotGcPlan] = list(unusable_reports)

    for depotid in sorted(own_depots):
        reports.append(
            _plan_one_depot(
                depotid,
                depots_owners.get(depotid, []),
                depot_root=depot_root,
                archive_dir=archive_dir,
                reader=reader,
                extra_manifest_ids=inputs.oracle_manifests.get(depotid, ()),
            )
        )

    plan = GcPlan(appid=appid, depots=reports)
    logger.info(
        "cache-gc appid=%s planned: %d depot(s), %d orphan chunk(s) / %d byte(s), "
        "statuses=%s",
        appid, len(reports), plan.orphan_count, plan.orphan_bytes,
        {k: v for k, v in plan.status_counts().items() if v},
    )
    return plan


def _plan_one_depot(
    depotid: int,
    mapped_apps: Sequence[MappedApp],
    *,
    depot_root: str,
    archive_dir: str,
    reader: _ManifestReader,
    extra_manifest_ids: Sequence[object] = (),
) -> DepotGcPlan:
    try:
        depot_dir = deletion.depot_dir_path(depot_root, depotid)
    except deletion.UnsafeDepotTargetError as exc:  # pragma: no cover - see guard
        logger.warning("cache-gc depot=%s REFUSED by the path guard: %s", depotid, exc)
        return DepotGcPlan(depotid=depotid, status=STATUS_UNUSABLE_DEPOTID, note=str(exc))

    if is_link_like(depot_dir):
        logger.warning(
            "cache-gc depot=%s REFUSED: %s is a symlink or junction", depotid, depot_dir
        )
        return DepotGcPlan(
            depotid=depotid,
            status=STATUS_LINKED_DIR,
            note=(
                f"{depot_dir} is a symlink or junction, not a directory; GC "
                "never descends through a link on the shared cache volume."
            ),
        )

    if not os.path.isdir(depot_dir):
        return DepotGcPlan(
            depotid=depotid,
            status=STATUS_MISSING_DIR,
            note=(
                f"{depot_dir} does not exist, so nothing is cached for this depot "
                "and there is nothing to reclaim."
            ),
        )

    scan = scan_depot_chunks(depot_dir)
    stored = scan_stored_manifests(depot_dir)
    duplicates = dedupe_candidates(stored)

    resolution = resolve_depot_chunkset(
        depotid,
        mapped_apps,
        archive_dir=archive_dir,
        stored_manifests=stored,
        reader=reader,
        extra_manifest_ids=extra_manifest_ids,
    )

    shown = scan.unrecognized[:MAX_REPORTED_NAMES]
    common = dict(
        depotid=depotid,
        unrecognized_count=len(scan.unrecognized),
        unrecognized_examples=shown,
        vanished_count=scan.vanished_count,
        depot_dir_exists=True,
        chunk_dir_exists=scan.chunk_dir_exists,
        apps=resolution.apps,
        dedupe=duplicates,
        extras=resolution.extras,
    )

    if not resolution.gate_passed:
        logger.info(
            "cache-gc depot=%s %s: %s", depotid, resolution.status, resolution.note
        )
        return DepotGcPlan(status=resolution.status, note=resolution.note, **common)

    keep = resolution.keep
    on_disk = scan.chunks
    orphan_chunks = {
        chunk_id: size for chunk_id, size in on_disk.items() if chunk_id not in keep
    }
    kept_count = 0
    kept_bytes = 0
    size_mismatches = 0
    for chunk_id, size in on_disk.items():
        declared = keep.get(chunk_id)
        if declared is None:
            continue
        kept_count += 1
        kept_bytes += size
        if declared != size:
            size_mismatches += 1

    # WP 3.9: what the extra (oracle) manifests actually saved here — on-disk
    # chunks no app manifest covers. Computed from the plan's own two sets, so
    # it cannot drift from what was really kept.
    protected = {
        chunk_id: size
        for chunk_id, size in on_disk.items()
        if chunk_id in resolution.extra_only_chunks
    }

    logger.info(
        "cache-gc depot=%s planned: keep_set=%d on_disk=%d orphans=%d bytes=%d "
        "oracle_protected=%d (%d bytes)",
        depotid, len(keep), len(on_disk), len(orphan_chunks),
        sum(orphan_chunks.values()), len(protected), sum(protected.values()),
    )
    for extra in resolution.extras:
        if extra.error:
            logger.info(
                "cache-gc depot=%s extra manifest %s unusable: %s",
                depotid, extra.manifestid, extra.error,
            )

    return DepotGcPlan(
        status=STATUS_PLANNED,
        orphan_chunks=orphan_chunks,
        kept_count=kept_count,
        kept_bytes=kept_bytes,
        keep_set_count=len(keep),
        size_mismatch_count=size_mismatches,
        oracle_protected_count=len(protected),
        oracle_protected_bytes=sum(protected.values()),
        note="",
        **common,
    )


# --------------------------------------------------------------------------
# The database statements GC planning needs
# --------------------------------------------------------------------------

#: How many app ids one ``load_content_states`` query binds at once. SQLite's
#: default ``SQLITE_MAX_VARIABLE_NUMBER`` is 999 and
#: ``deletion.load_co_owner_states`` also binds one parameter per active job
#: status, so the batch is kept well clear of the limit. A depot mapped to
#: thousands of apps is absurd, but it is exactly the kind of poisoned input
#: that must degrade into more queries rather than into an exception.
CONTENT_STATE_BATCH = 500


def load_content_states(
    conn: sqlite3.Connection, appids: Iterable[int]
) -> dict[int, bool]:
    """``{appid: has_content}`` for arbitrarily many apps, batched.

    Thin wrapper over ``deletion.load_co_owner_states`` so GC uses **exactly**
    the ADR-0003-addendum predicate deletion uses (``deletion._has_cache_content``),
    not a second copy of it that could drift. The only thing added here is the
    batching.
    """
    ids = sorted({a for a in appids if isinstance(a, int) and not isinstance(a, bool) and a > 0})
    states: dict[int, bool] = {}
    for start in range(0, len(ids), CONTENT_STATE_BATCH):
        states.update(
            deletion.load_co_owner_states(conn, ids[start : start + CONTENT_STATE_BATCH])
        )
    return states


def load_recorded_manifests(
    conn: sqlite3.Connection, appid: int
) -> dict[tuple[int, int], object]:
    """``{(appid, depotid): raw manifestid}`` for every depot of ``appid``.

    One query, no dynamically built ``IN (?,?,?)`` list — the depot set is
    expressed as the same subquery ``deletion.load_mapping_rows`` uses, so the
    two loaders cannot disagree about which depots are in scope.

    Values come back **raw**: ``manifestid`` is TEXT and SQLite does not
    enforce that, and the appid/depotid columns can hold poisoned values too,
    so validation belongs in the planner (``valid_manifest_id``,
    ``deletion.coerce_positive_id``) where a bad row becomes a reported status
    instead of an exception.
    """
    recorded: dict[tuple[int, int], object] = {}
    for row in conn.execute(
        """
        SELECT appid, depotid, manifestid FROM depot_manifests
        WHERE depotid IN (SELECT depotid FROM depot_app_map WHERE appid = ?)
        """,
        (appid,),
    ).fetchall():
        owner = deletion.coerce_positive_id(row["appid"])
        depotid = deletion.coerce_positive_id(row["depotid"])
        if owner is None or depotid is None:
            continue
        recorded[(owner, depotid)] = row["manifestid"]
    return recorded


def load_gc_inputs(
    conn: sqlite3.Connection,
    appid: int,
    *,
    oracle_manifests: Mapping[int, Sequence[str]] | None = None,
) -> GcInputs:
    """Read everything ``plan_gc`` needs for one app in three queries.

    ``oracle_manifests`` is **passed in, not read here** (WP 3.9). This module
    knows nothing about the manifest oracle — its caller
    (``gc_execute.run_gc``) asks ``vault_api.oracle.gc_keepset_gids``, which
    is the one place that consults the enable flag. Keeping the direction that
    way round means ``vault_api/gc.py`` has no import of, and no opinion
    about, a network-backed feature; ``None`` (the default) is exactly the
    pre-WP-3.9 behaviour.
    """
    mapping_rows = deletion.load_mapping_rows(conn, appid)
    owner_ids = {appid}
    owner_ids.update(deletion.other_owner_ids(mapping_rows, appid))
    return GcInputs(
        mapping_rows=mapping_rows,
        content_states=load_content_states(conn, owner_ids),
        recorded_manifests=load_recorded_manifests(conn, appid),
        oracle_manifests=(
            {}
            if oracle_manifests is None
            else {
                depotid: list(gids) for depotid, gids in oracle_manifests.items()
            }
        ),
    )
