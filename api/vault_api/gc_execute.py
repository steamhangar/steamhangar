"""Garbage-collection EXECUTION: acting on a ``vault_api.gc`` plan (WP 3.8, ADR-0007).

``vault_api.gc`` decides **which chunks are not needed any more** and mutates
nothing. This module is the other half: it takes that decision and deletes.
Everything dangerous therefore lives here, and everything here is written so a
reviewer can answer three questions from the code alone — *what may be
deleted*, *what happens when the world changes underneath us*, and *what is
reported when only part of it worked*.

## The four rules, stated before anything else

1. **Dry run is the default and the default is enforced elsewhere.** This
   module deletes only when it is handed ``execute=True``. The endpoint
   (``routers/cache.py``) and the job row (``jobs.gc_execute``, schema v7)
   default to ``False``; ``jobs.job_deletes`` resolves a missing/NULL mode to
   "dry run", never to "delete".
2. **The plan is recomputed at execution start. Always.** ``run_gc`` takes an
   app id, not a plan — there is no parameter through which an enqueue-time
   plan could be passed in, so "we executed a stale plan" is not a bug this
   code can have. See "TOCTOU" below.
3. **Only a ``planned`` depot is touched — at all.** Not its chunks, not its
   redundant stored manifests, not anything else under it. Every skip status
   ``vault_api.gc`` can produce (unmapped, unreadable owner, no counting apps,
   no resolvable manifest, missing directory, poisoned depot id) means the
   depot loses nothing whatsoever.
4. **Only a chunk the plan named is deleted**, and only after its name has
   been re-validated as 40 lowercase hex characters and its path re-derived
   through the WP 1.6 guards. A plan that somehow carried ``"../../etc"`` gets
   a refusal, not a deletion.

## TOCTOU: why there is no enqueue-time plan

``POST /v1/cache/{appid}/gc`` returns 202 and stores *two* facts: the app id
and the mode. It stores no plan, no depot list and no chunk list. The worker
later calls ``run_gc``, which loads the database inputs and calls
``gc.plan_gc`` **then** — after the queue wait, which on a busy single worker
can be minutes behind a running prefill. A game that updated in that window is
therefore planned against its new manifest; a chunk that was an orphan at
enqueue time but belongs to the current manifest by execution time is kept, and
the reverse case is likewise decided by the fresh plan. That is not a
convention this module follows carefully — it is the only thing it *can* do,
because there is nowhere to put a stale plan.

The residual window is plan → unlink, inside one job. Two things keep it small
and its consequences benign:

- Prefills and GC share the single worker queue (``jobs.JOB_TYPE_GC``), so
  SteamPrefill can never be writing into a depot while this code unlinks from
  it. That is the hazard ``DELETE /v1/cache/{appid}`` has to refuse with a
  409; here it is structurally impossible.
- What *can* still land in the window is a ``PUT /v1/mapping/{depotid}`` (a
  new co-owner whose manifest might pin one of the planned chunks) or a client
  fetching through vault-core. ADR-0007's honest limit covers both: the
  consequence of deleting a chunk something still wants is a **re-download**,
  never corruption. Paying for a second recheck per chunk to shave that window
  would buy nothing that limit does not already accept.

## Racing removals (the WP 1.6 lessons, applied to files)

``DELETE /v1/cache/{appid}`` runs in a request thread, not on the worker, so
nothing *structural* keeps it out of this module's way: it can ``rmtree`` a
depot while this code unlinks chunks out of it. What narrows that hazard —
and the complete reason the GC endpoint needs no 409 guard of its own:

- **Same app: already refused.** ``jobs.active_job_for_app`` is
  **type-agnostic** — it returns the app's oldest ``queued``/``running`` job
  whatever its type. A GC job for app 440 therefore makes
  ``DELETE /v1/cache/440`` answer 409 through the *existing* WP 1.6 guard, with
  no GC-specific code, and the reverse pairing (delete first, GC second) is
  serialized by the same guard on the enqueue side.
- **A co-owner's DELETE: closed by the deletion rules, not by luck.** Nothing
  stops ``DELETE /v1/cache/730`` while GC runs for 440, and both may map depot
  D. ADR-0003's shared-depot protection keeps D unless *every* readable
  co-owner is verifiably uncached — and ``deletion._has_cache_content`` treats
  an app with an **active job** as having content (``load_co_owner_states``
  asks for exactly that, again type-agnostically). The GC job that is running
  right now is 440's active job, so 440 counts as content, so D is ``shared``
  and protected rather than a deletable last remnant.
- **What is left** is a delete of a depot GC is not touching, or the same depot
  through a path the two bullets above do not cover (a poisoned row, a mapping
  written mid-run). That residue is handled below rather than prevented, and it
  is why every removal here still goes through the settle-and-recheck path.

Every removal therefore goes through
``deletion.remove_file_settling``, which decides the outcome from the settled
filesystem state rather than from the exception: a racing delete surfaces as
``FileNotFoundError`` **or**, on Windows, as ``PermissionError [WinError 5]``
(a file deleted while a handle is open is "delete pending", and further access
reports access-denied, not not-found — docs/LEARNINGS.md, WP 1.6), and
``os.path.lexists`` has the last word in both cases. A file that ended up gone
is reported as ``already_gone`` with **0 bytes attributed to this run**, so two
racing actors never both claim the same reclaimed space.

## Honesty about partial work

A GC run that could not finish must not report as if it had. The rule is one
sentence: **an execute run is ``done`` only when every chunk it planned to
delete is now gone** (removed by this run, or found already gone). Anything
else — a failed unlink, a name that turned into a link, a plan that could not
be built at all — makes the job ``error``, and the log carries the exact
counts either way. There is no "mostly worked" status and no rounding: bytes
are summed from a fresh ``lstat`` taken immediately before each unlink, never
from the plan's numbers, so ``bytes_freed`` is what was actually freed.

(The rmtree trap that produced this rule does not apply literally here — a
single ``unlink`` either removed the file or did not, so "failed" really does
mean "still on disk" for one chunk. It applies to the *run*: a run that
removed 900 of 1000 chunks and then hit a locked one has changed the disk a
lot, and reporting that as a clean failure would be as wrong as reporting it
as a clean success.)

## State bookkeeping: what GC writes to the database, and why

**``apps.status`` and ``apps.last_prefill_at``: untouched.** They describe the
app's *prefill lifecycle* ("was it filled, when, did the last run fail"). GC
reclaims bytes that are, by construction, not part of any counting app's
current manifest, so a ``done`` app is still done and its
``last_prefill_at`` is still true. Flipping a green badge to ``error`` because
one chunk file was locked would report a prefill problem that does not exist.

**The size cache: invalidated** after an execute run that actually removed
something (``sizes.SizeCache.invalidate``, the hook WP 1.5 exports for exactly
this). Reclaimed bytes that ``GET /v1/games`` keeps hiding for up to
``VAULT_SIZE_CACHE_TTL`` seconds would make the one visible effect of GC look
like it did not happen. Not invalidated for a dry run — nothing changed.

**``apps.needs_force``: set to 1 for every app mapped to a depot this run
actually removed chunks from.** This is the one call that was genuinely open,
so the full argument, including the counter-argument:

*Why it looks unnecessary.* ``needs_force`` (ADR-0006 decision 2) exists
because SteamPrefill keeps its own ``successfullyDownloadedDepots.json``
bookkeeping, and a non-forced run trusts it. What it records is "depot D was
downloaded at manifest M". GC's keep set **is** the union of the current
manifests — and for an app vault-api prefilled itself, "current manifest" is
literally what SteamPrefill downloaded, because ``depot_manifests`` is
ingested from SteamPrefill's own ``.bin`` files (WP 3.2). Under that identity,
every chunk of M survives GC, SteamPrefill's claim stays true, and the next
non-forced run is honest without any flag being set.

*Why it is set anyway.* That identity is an assumption about two records
agreeing, and there are real, already-reachable states where they do not:

- ``manifest_ingest.ingest_after_prefill`` is wrapped in its own
  ``try``/``except`` in ``worker.py`` — a crash there is logged and the prefill
  still finishes ``done``. ``depot_manifests`` then still names the *previous*
  manifest while the disk holds the *new* chunks. GC would plan the new
  chunks as orphans (they are not in the recorded manifest) while
  SteamPrefill's bookkeeping says the depot is complete at the new manifest.
- An app with no ``depot_manifests`` row at all falls back to the newest
  *cache-stored* manifest — evidence written by a client, which can be older
  than what SteamPrefill fetched.

In both, a non-forced follow-up run would skip the depot and leave the cache
silently incomplete, with **no self-healing path** — the exact shape of wedge
the WP 3.4 review reproduced and rejected. The asymmetry decides it: being
wrong in the "set it" direction costs one redundant ``--force`` run (re-issued
requests served from cache at disk speed — the measurement ``--force`` always
relied on, ADR-0001); being wrong in the "leave it" direction costs a
permanently incomplete cache reported as complete.

*Kept minimal, and that matters as much as the decision.* The flag is set:

- only on an **execute** run (never a dry run — nothing changed);
- only for a depot where at least one planned chunk is **actually gone**
  afterwards (removed here, or found removed by a concurrent actor);
- **not** for a depot that only had redundant stored-manifest copies removed —
  a duplicate manifest copy is not chunk content and changes nothing about
  whether a depot is completely downloaded;
- **not** for a skipped depot, a zero-orphan depot, or a depot where every
  removal failed (nothing was reclaimed, so nothing became uncertain);
- for **every app mapped to that depot**, not just the requested one: a shared
  depot's bytes just changed under all of its co-owners, which is precisely
  the reasoning ADR-0003's addendum already applies to remnant deletions.

## The exclusion hook, and the grace window that now uses it

The orphan set is consumed through ``orphans_to_delete``, which takes a
sequence of ``ChunkExclusion`` predicates and reports what they held back.
Exclusions can only ever **shrink** the delete set — there is no hook that adds
to it — so the worst a broken predicate can do is leave bytes on disk.

``RecentlyStoredGrace`` (WP 3.8b, ADR-0007's beta-branch addendum, decision A)
is the first and, for now, only one: a chunk stored within the last
``VAULT_GC_GRACE_DAYS`` days is held back. It exists because opt-in beta
branches reach the cache only through store-on-miss and appear in no ``public``
manifest, so the planner correctly calls their chunks orphans and this layer
correctly declines to act on that.

**The separation is deliberate: the plan still names them; the execution holds
them back.** ``gc.plan_gc`` stays a pure statement about manifests ("these
chunks are in no counting app's current manifest"), which is what makes it
testable and what makes the dry run's orphan numbers mean one thing. Time is a
policy on top of that statement, not part of it.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import stat as stat_module
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping, Sequence

from vault_api import deletion, gc, jobs, oracle, webhooks
from vault_api.config import Settings
from vault_api.sizes import SizeCache
from vault_api.webhooks import WebhookNotifier

logger = logging.getLogger(__name__)

#: How many individual problem entries a job log lists before summarising the
#: rest as a count. Same bound and same reasoning as ``gc.MAX_REPORTED_NAMES``:
#: a depot full of locked files must not turn a 4 KiB log excerpt into pure
#: noise (``jobs.tail_excerpt`` would then throw away the totals, which are the
#: part an operator actually needs).
MAX_REPORTED_PROBLEMS = 10

#: Longest skip note reproduced verbatim in the job log. The notes
#: ``vault_api.gc`` writes are several sentences long by design (they are the
#: operator's instructions for un-skipping a depot); the full text stays in the
#: server log, the job log gets the beginning.
MAX_LOGGED_NOTE_CHARS = 180

#: How many depot ids one ``needs_force`` owner lookup binds at once. Same
#: reasoning and same headroom as ``gc.CONTENT_STATE_BATCH`` against SQLite's
#: default ``SQLITE_MAX_VARIABLE_NUMBER`` of 999.
OWNER_LOOKUP_BATCH = 500

#: Block size for the byte-identity check between two stored manifest copies.
COMPARE_BLOCK_BYTES = 256 * 1024


# --------------------------------------------------------------------------
# Outcomes of one file removal attempt
# --------------------------------------------------------------------------

#: This run unlinked the file. Its bytes count towards ``bytes_freed``.
REMOVED = "removed"

#: The file is gone but this run did not remove it — it was already absent, or
#: a concurrent actor removed it. **0 bytes attributed** (see the module
#: docstring: two racing actors must never both claim the same space).
ALREADY_GONE = "already_gone"

#: The name is (or became) a symlink/junction. Never unlinked: GC planned the
#: deletion of a *chunk file*, and a name that points somewhere else is not
#: that file. Reported, and it makes the job ``error`` — vault-core does not
#: create links in the cache, so this is an anomaly, not a race.
REFUSED_LINK = "refused_link"

#: The name exists but is not a regular file (a directory named like a chunk
#: id, a device node). Same treatment as ``REFUSED_LINK``.
REFUSED_NOT_A_FILE = "refused_not_a_file"

#: The name or path failed re-validation (not 40 lowercase hex, or it would not
#: land strictly inside the depot's ``chunk/`` directory). Nothing is touched.
#: Unreachable through ``gc.plan_gc``, whose orphan ids are 40-hex by
#: construction — this is the guard that makes that guarantee independent of
#: the planner rather than dependent on it.
REFUSED_UNSAFE_NAME = "refused_unsafe_name"

#: The copy that would have been KEPT is missing/unusable, so its duplicates
#: were left alone. Dedupe never removes the last copy of a manifest.
REFUSED_NO_KEEPER = "refused_no_keeper"

#: A duplicate stored manifest whose bytes differ from the kept copy (or could
#: not be compared). Kept — see ``execute_dedupe``.
KEPT_DIFFERING = "kept_differing"

#: The file is still on disk and could not be removed: a real failure.
FAILED = "failed"

#: Outcomes that mean "the planned file is gone now". For **chunks** this set
#: is the whole ``done``/``error`` decision: the job is ``done`` exactly when
#: every planned chunk removal landed in it.
GONE_OUTCOMES = frozenset({REMOVED, ALREADY_GONE})

#: Outcomes where GC **decided on purpose** not to reclaim something, having
#: found the world in a state its own safety rules cover. These are findings,
#: not failures, and they never make a job ``error``.
#:
#: The distinction, stated once: *a decision GC made on purpose is never an
#: error; a thing GC could not do is.* ``KEPT_DIFFERING`` is the dedupe
#: identity rule doing its job. ``REFUSED_NO_KEEPER`` is the never-remove-the-
#: last-copy rule doing its job — and its usual cause is a concurrent
#: ``DELETE /v1/cache/{appid}`` taking the depot away mid-run, which WP 1.6
#: already established must not drag an otherwise-clean run to ``error``
#: (a racing deleter is not a failure). Deleting nothing is always safe;
#: reporting a benign race as a failure is not honest either.
#:
#: Note this set applies to dedupe only. There is no equivalent for chunks:
#: every chunk outcome is either "gone" or something went wrong.
DECLINED_OUTCOMES = frozenset({KEPT_DIFFERING, REFUSED_NO_KEEPER})


@dataclass(frozen=True)
class FileRemoval:
    """What happened to ONE file this run tried to remove."""

    #: Chunk id, or the stored manifest copy's filename (its request code).
    name: str
    outcome: str
    #: Non-zero only for ``REMOVED`` — the size read by an ``lstat`` taken
    #: immediately before the unlink, i.e. what was really freed.
    bytes_freed: int = 0
    detail: str = ""

    @property
    def is_problem(self) -> bool:
        return self.outcome not in GONE_OUTCOMES

    def describe(self) -> str:
        return f"{self.name}: {self.outcome}" + (f" ({self.detail})" if self.detail else "")


# --------------------------------------------------------------------------
# The exclusion hook (see the module docstring)
# --------------------------------------------------------------------------

#: An extra "do not delete this orphan after all" rule. Given a chunk id and
#: the depot plan it came from, it returns a reason string to hold the chunk
#: back, or ``None`` to let the plan stand. Exclusions can only ever *shrink*
#: the set that gets deleted — there is no hook that can add to it.
ChunkExclusion = Callable[[str, gc.DepotGcPlan], "str | None"]

#: The default: no extra rules, the plan governs. Named rather than written as
#: a bare ``()`` at each call site, and what ``VAULT_GC_GRACE_DAYS=0`` resolves
#: to: with the window disabled the predicate is not constructed at all.
NO_EXCLUSIONS: tuple[ChunkExclusion, ...] = ()

#: Seconds per day, spelled once. The grace window is configured in days
#: because that is how an operator thinks about a beta test; every comparison
#: below happens in seconds.
SECONDS_PER_DAY = 86400.0


class RecentlyStoredGrace:
    """Hold back any orphan chunk **stored** within the last ``grace_days`` days.

    ADR-0007's beta-branch addendum, decision A. Opt-in Steam beta branches
    only ever reach the cache through store-on-miss (SteamPrefill has no branch
    selection) and their chunks are in no ``public`` manifest, so manifest-diff
    GC plans every one of them as an orphan. This predicate is what stops the
    tester who downloaded a beta build on Monday from re-downloading it after
    Tuesday night's collection. It is not a completeness guarantee: after the
    window passes, an unrecorded manifest's chunks are collectable again, and
    ADR-0007's honest limit (consequence = re-download, never corruption)
    stands unchanged.

    ## Recency comes from ``st_ctime``, and what that means on each platform

    **Not ``st_mtime``.** nginx ``proxy_store`` stamps a stored file's mtime
    with the *upstream* ``Last-Modified``, i.e. Steam's content publish time —
    months old for a freshly downloaded chunk of an old build. That is the
    measured reason ADR-0007 rejected time-based attribution outright, and it
    is why an mtime-based grace window would protect nothing.

    ``st_ctime`` instead, whose meaning honestly differs by platform:

    - **Linux / the container (the deployment target):** the inode *change*
      time. ``proxy_store`` writes to a temp file and ``rename()``s it into
      place; the rename sets ctime on the target inode, so ctime is the
      store time. The known caveat: ``chmod``/``chown``/a hardlink count
      change also bumps ctime. Nothing touches cache chunk files that way —
      vault-core writes them, vault-api deletes them, and neither changes
      permissions — so the caveat is real but not reachable here. If an
      operator does run a recursive ``chown`` over the cache, the effect is
      that everything looks freshly stored and GC frees less for one window:
      the safe direction.
    - **Windows (dev runs only):** the file *creation* time, which for a
      store-on-miss write is also the store time. One quirk worth naming:
      NTFS "file system tunneling" hands a recreated file its predecessor's
      creation time when the recreate follows the delete within ~15 s, so a
      re-stored chunk can look older than it is and lose protection early.
      Windows is not the deployment target, and a chunk deleted and re-fetched
      inside 15 s is not a case this cache produces.

    Cheap either way: one ``lstat`` per **orphan** chunk, not per cached
    chunk. Reading the times during ``gc.scan_depot_chunks`` would save that
    stat, and is deliberately not done — the planner stays a statement about
    manifests (module docstring).

    ## The two decisions worth naming

    **Boundary: ``age < window`` holds back, so exactly N days is deleted.**
    The window is the half-open interval "stored less than N days ago". A
    chunk whose age is exactly ``grace_days`` has served the full window and
    is released. The alternative (``<=``) would make the release time depend
    on stat resolution rather than on the number the operator configured.

    **Fail-closed on an unreadable age.** A chunk whose ``lstat`` fails is
    held back — an age we cannot read must protect, not expose. What that
    branch really does is permission/ACL refusals (``EACCES``) and transient
    I/O errors; a Windows delete-pending name is **not** one of its cases, see
    the next paragraph. Two failures are deliberately *not* routed through it:

    - ``FileNotFoundError`` — the file is unambiguously gone, so there is
      nothing to protect. The plan stands and ``remove_one_file`` reports it
      as ``ALREADY_GONE`` with 0 bytes, WP 1.6's accurate accounting for a
      chunk a concurrent ``DELETE`` removed.

      **A Windows delete-pending name lands here, and that is correct.** Worth
      stating because WP 1.6's ``PermissionError [WinError 5]`` finding invites
      the opposite guess: measured on Windows 11 (open a handle with
      ``FILE_SHARE_DELETE``, unlink the name while the handle lives, then stat
      it), ``os.lstat`` on a delete-pending name raises ``FileNotFoundError
      [WinError 2]``. WP 1.6's access-denied observation was about ``unlink``
      — a different syscall — and applies to the removal path, not to this
      one. The outcome is the right one anyway: a delete-pending name has
      already been successfully unlinked and nothing can bring it back, so
      "gone, 0 bytes" is the honest report and protecting it would be a
      fiction.

      Note this decision is made from the **error type**, not from an
      ``os.path.lexists`` recheck the way ``remove_one_file`` makes its
      equivalent call: ``lexists`` is itself ``lstat``-based and swallows every
      ``OSError`` into ``False``, so consulting it here would collapse "cannot
      read" into "gone" — turning the protective branch into a deletion.
      ``remove_one_file`` can afford that recheck because both of its answers
      keep the file; this one cannot.
    - the chunk id is **not a valid chunk filename**: that is an anomaly, not
      an age question, and holding it back would hide it. The plan stands and
      ``_remove_planned_chunk`` refuses it as ``REFUSED_UNSAFE_NAME`` — a
      reported problem that makes the job ``error``. Nothing is deleted on
      either route; the difference is only whether an operator sees it.

    A clock that runs backwards (a chunk whose ctime is in the future) yields
    a negative age, which is ``< window`` and therefore held back. Protective,
    and the reported age is clamped at 0 rather than printed as "-0.3 days".
    """

    def __init__(
        self,
        *,
        cache_root: str,
        grace_days: int,
        now: Callable[[], float] = time.time,
    ) -> None:
        """Never raises for a filesystem problem — see ``_depot_root_error``.

        ``now`` is injectable for the tests, which cannot manufacture an old
        ``ctime``: ``os.utime`` sets atime/mtime and explicitly NOT ctime, and
        the alternative (sleeping) would be a slow, flaky test. The tests
        therefore create real files and move the *clock*, which exercises the
        same comparison with the same real ``st_ctime`` on both platforms.
        """
        if grace_days <= 0:
            raise ValueError(
                "RecentlyStoredGrace needs grace_days > 0; a disabled window is "
                "expressed by not constructing the predicate at all "
                "(see grace_window_exclusions)."
            )
        self.grace_days = int(grace_days)
        self.grace_seconds = self.grace_days * SECONDS_PER_DAY
        self._now = now
        # Resolved once, here, rather than per chunk: it is a realpath + isdir
        # check. A failure is REMEMBERED, not raised — this object is built by
        # ``run_gc_job``, whose contract is that it never raises — and it makes
        # every chunk fail closed. In the wiring it is also unreachable: the
        # same guard runs at the top of ``run_gc`` against the same cache root
        # and aborts the whole run before any predicate is consulted.
        try:
            self._depot_root = deletion.resolve_depot_root(cache_root)
            self._depot_root_error = ""
        except deletion.DeletionGuardError as exc:
            self._depot_root = ""
            self._depot_root_error = str(exc)

    def __call__(self, chunk_id: str, depot_plan: gc.DepotGcPlan) -> str | None:
        """Reason to hold ``chunk_id`` back, or ``None`` to let the plan stand.

        Reasons about **only the chunk the plan named**: the path is rebuilt
        from the depot id in the plan through the very same guards
        ``execute_depot`` uses (``deletion.depot_dir_path`` →
        ``deletion.safe_child_path``), so there is no second path builder to
        keep in sync with the first.
        """
        if not gc.is_chunk_filename(chunk_id):
            return None  # an anomaly for the executor to refuse and report

        path = self._chunk_path(depot_plan.depotid, chunk_id)
        if path is None:
            return self._unreadable_age_reason(
                self._depot_root_error
                or f"its path under depot {depot_plan.depotid} could not be rebuilt"
            )

        try:
            stored_at = os.lstat(path).st_ctime
        except FileNotFoundError:
            # Unambiguously gone: nothing to protect. The plan stands and the
            # removal path reports ALREADY_GONE / 0 bytes, which is WP 1.6's
            # accurate accounting for a chunk a concurrent DELETE removed.
            # A Windows delete-pending name arrives HERE (measured: lstat
            # raises FileNotFoundError [WinError 2] for it; WP 1.6's
            # PermissionError was unlink's answer, not lstat's) — correctly,
            # since such a name is already unlinked for good.
            return None
        except OSError as exc:
            # A permission/ACL refusal or a transient I/O error: the file may
            # well be there and young, so it is protected rather than deleted.
            return self._unreadable_age_reason(f"{type(exc).__name__}: {exc}")

        age_seconds = self._now() - stored_at
        if age_seconds < self.grace_seconds:
            age_days = max(age_seconds, 0.0) / SECONDS_PER_DAY
            return (
                f"stored {age_days:.1f} days ago, grace window is "
                f"{self.grace_days} days"
            )
        return None

    def _chunk_path(self, depotid: int, chunk_id: str) -> str | None:
        """``depot/<id>/chunk/<chunk id>``, or ``None`` if a guard refused it."""
        try:
            depot_dir = deletion.depot_dir_path(self._depot_root, depotid)
            chunk_dir = deletion.safe_child_path(depot_dir, gc.CHUNK_DIRNAME)
            return deletion.safe_child_path(chunk_dir, chunk_id)
        except deletion.DeletionGuardError:
            return None

    def _unreadable_age_reason(self, detail: str) -> str:
        return (
            f"its store time could not be read ({detail}), and the "
            f"{self.grace_days}-day grace window holds back what it cannot age"
        )


def grace_window_exclusions(settings: Settings) -> tuple[ChunkExclusion, ...]:
    """The exclusions one GC run should apply, per configuration.

    ``VAULT_GC_GRACE_DAYS=0`` returns ``NO_EXCLUSIONS`` — the predicate is not
    constructed, nothing stats anything, and the executor behaves exactly as it
    did before WP 3.8b. That is the one switch; there is no second "enabled"
    flag to get out of sync with it.
    """
    if settings.gc_grace_days <= 0:
        return NO_EXCLUSIONS
    return (
        RecentlyStoredGrace(
            cache_root=settings.cache_root, grace_days=settings.gc_grace_days
        ),
    )


def orphans_to_delete(
    depot_plan: gc.DepotGcPlan,
    *,
    exclusions: Sequence[ChunkExclusion] = NO_EXCLUSIONS,
) -> tuple[dict[str, int], dict[str, str]]:
    """Split a depot plan's orphans into ``(to_delete, held_back)``.

    ``to_delete`` maps chunk id -> the plan's byte size; ``held_back`` maps
    chunk id -> why it was excluded.

    **The status gate is here, not only in the planner.** ``vault_api.gc``
    already guarantees an empty orphan set for every non-``planned`` status,
    but this function refuses a non-``planned`` plan anyway instead of relying
    on that guarantee holding forever in another module. It is the difference
    between "we delete what the plan says and the plan is careful" and "a
    depot that was not planned loses nothing, whatever else changes".
    """
    if depot_plan.status != gc.STATUS_PLANNED:
        return {}, {}

    to_delete: dict[str, int] = {}
    held_back: dict[str, str] = {}
    for chunk_id, size in depot_plan.orphan_chunks.items():
        reason = None
        for exclusion in exclusions:
            reason = exclusion(chunk_id, depot_plan)
            if reason:
                break
        if reason:
            held_back[chunk_id] = reason
        else:
            to_delete[chunk_id] = size
    return to_delete, held_back


# --------------------------------------------------------------------------
# Removing one file
# --------------------------------------------------------------------------


def expected_real_dir(depot_root: str, depotid: object, *parts: str) -> str:
    """``realpath(depot_root)/<depotid>/<parts...>`` — where a GC unlink may land.

    WP SEC-FIX-4 (S-2). Only the BASE is resolved: everything below it is
    joined lexically, so if any of ``<depotid>``, ``chunk``, ``manifest``,
    ``<mid>`` or ``5`` is a symlink planted on the shared volume, the
    ``realpath`` of the file's actual parent differs from this and the unlink
    is refused (``remove_one_file(expected_parent=...)``).
    """
    return os.path.join(os.path.realpath(depot_root), str(depotid), *parts)


def remove_one_file(
    path: str, *, name: str, expected_parent: str | None = None
) -> FileRemoval:
    """Remove one regular, non-link file. Never raises, never follows a link.

    Order of checks, each of which can only make the outcome *less*
    destructive:

    1. ``os.lstat`` — a missing file is ``ALREADY_GONE``, not an error, and a
       Windows delete-pending name is measurably one of those (``lstat`` raises
       ``FileNotFoundError [WinError 2]`` for it; the ``PermissionError
       [WinError 5]`` WP 1.6 recorded is what ``unlink`` answers, a different
       syscall — corrected in WP 3.8b, measured). An ``lstat`` that fails some
       other way is re-decided by ``os.path.lexists``, which is the same
       "settled state has the last word" rule the removal helper uses.
    2. link check — refused, never unlinked (see ``REFUSED_LINK``). Both the
       ``S_ISLNK`` bit and ``deletion.is_link_like`` are consulted: measured on
       Windows (WP 1.6, docs/LEARNINGS.md) a **junction** is not
       ``os.path.islink`` and does not necessarily carry the link bit either,
       which is why ``is_link_like``'s ``isjunction`` arm exists. The
       redundancy is stated rather than implied — a junction cannot point at a
       file, so it would also be caught by check 3, and one of the two checks
       alone would keep the tests green.
    3. regular-file check — a directory named like a chunk id is refused.
    4. size — read from the ``lstat`` above, *before* the unlink, so the bytes
       reported freed are the bytes that were there.
    5. ``deletion.remove_file_settling`` — the WP 1.6 settle-and-recheck path.

    ``expected_parent`` (WP SEC-FIX-4, S-2): when given, the ``realpath`` of
    the file's directory must equal it, checked FIRST. Steps 1-3 only look at
    the last component; this is what refuses a symlinked directory *above*
    the file (``chunk/`` -> /data), which ``lstat`` would happily traverse.

    Known residual TOCTOU window (docs/security/threat-model.md §9): between
    the ``realpath`` check and the unlink, ``chunk/`` or ``5/`` can be swapped
    for a link. It needs code execution in vault-core and the window is
    narrow. The proper fix (``O_DIRECTORY|O_NOFOLLOW`` per level plus
    ``unlink(dir_fd=)``) is post-release.
    """
    if expected_parent is not None and (
        os.path.realpath(os.path.dirname(path)) != expected_parent
    ):
        return FileRemoval(
            name=name,
            outcome=REFUSED_LINK,
            detail=(
                "a directory on the way to this file is a symlink or junction "
                f"(it does not resolve to {expected_parent})"
            ),
        )
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return FileRemoval(name=name, outcome=ALREADY_GONE)
    except OSError as exc:
        if not os.path.lexists(path):
            return FileRemoval(name=name, outcome=ALREADY_GONE)
        return FileRemoval(
            name=name, outcome=FAILED, detail=f"{type(exc).__name__}: {exc}"
        )

    if stat_module.S_ISLNK(st.st_mode) or deletion.is_link_like(path):
        return FileRemoval(
            name=name,
            outcome=REFUSED_LINK,
            detail="the name is a symlink or junction, not the planned file",
        )
    if not stat_module.S_ISREG(st.st_mode):
        return FileRemoval(
            name=name,
            outcome=REFUSED_NOT_A_FILE,
            detail="the name is not a regular file",
        )

    size = st.st_size
    removed, error = deletion.remove_file_settling(path)
    if error is not None:
        return FileRemoval(
            name=name, outcome=FAILED, detail=f"{type(error).__name__}: {error}"
        )
    if not removed:
        # Gone, but not by us — a concurrent DELETE/GC got there first. 0 bytes.
        return FileRemoval(name=name, outcome=ALREADY_GONE)
    return FileRemoval(name=name, outcome=REMOVED, bytes_freed=size)


def files_are_identical(left: str, right: str) -> bool | None:
    """Byte-compare two files. ``None`` when they could not be compared.

    Used before removing a redundant stored manifest copy — see
    ``execute_dedupe`` for why "same manifest id" is not accepted as proof of
    "same bytes". Streams in ``COMPARE_BLOCK_BYTES`` blocks: stored manifests
    run to a few MB and duplicates are rare, but reading two files fully into
    memory to compare them is not a habit this codebase needs.
    """
    try:
        if os.path.getsize(left) != os.path.getsize(right):
            return False
        with open(left, "rb") as left_handle, open(right, "rb") as right_handle:
            while True:
                left_block = left_handle.read(COMPARE_BLOCK_BYTES)
                right_block = right_handle.read(COMPARE_BLOCK_BYTES)
                if left_block != right_block:
                    return False
                if not left_block:
                    return True
    except OSError:
        return None


# --------------------------------------------------------------------------
# Executing one depot's plan
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DepotGcResult:
    """What actually happened to ONE depot. Mirrors ``gc.DepotGcPlan``'s shape
    so a dry run and an execute run report the same fields, with the executed
    counters simply staying at zero for a dry run."""

    depotid: int
    #: Copied verbatim from the plan — the reason a depot was skipped is part
    #: of the result, not something the reader has to correlate.
    status: str
    #: Were deletions attempted for this depot? False for every dry run and
    #: for every non-``planned`` status.
    executed: bool

    planned_orphan_count: int = 0
    planned_orphan_bytes: int = 0
    kept_count: int = 0
    kept_bytes: int = 0
    planned_dedupe_count: int = 0
    planned_dedupe_bytes: int = 0

    removed_count: int = 0
    removed_bytes: int = 0
    already_gone_count: int = 0
    #: chunk id -> reason, from the ``ChunkExclusion`` hook (WP 3.8b: the
    #: recently-stored grace window). Filled for a **dry run too** — an
    #: operator reading a plan needs to see why the bytes it counts will not
    #: all come back.
    held_back: dict[str, str] = field(default_factory=dict)
    #: The plan's byte sizes for exactly those chunks. Part of the same
    #: honesty: "orphans=900 bytes, held_back=700 bytes" is an explanation,
    #: "orphans=900 bytes" followed by 200 freed is a puzzle.
    held_back_bytes: int = 0
    problems: list[FileRemoval] = field(default_factory=list)

    #: WP 3.9 / ADR-0007 decision B: on-disk chunks that would have been
    #: orphans if the manifest oracle had not contributed an open beta
    #: branch's manifest to this depot's keep set. Copied from the plan, zero
    #: whenever the oracle is off — which is the default.
    oracle_protected_count: int = 0
    oracle_protected_bytes: int = 0

    dedupe_removed_count: int = 0
    dedupe_removed_bytes: int = 0
    #: Reclaims GC deliberately passed on (``DECLINED_OUTCOMES``): a duplicate
    #: whose bytes differ from the kept copy, or a group whose keeper vanished.
    #: Reported because "GC ran and freed less than the plan predicted" needs a
    #: visible reason — and never counted as a failure.
    dedupe_declined: list[FileRemoval] = field(default_factory=list)
    dedupe_problems: list[FileRemoval] = field(default_factory=list)

    note: str = ""

    @property
    def all_problems(self) -> list[FileRemoval]:
        """Things that went wrong. Deliberate declines are NOT in here — see
        ``DECLINED_OUTCOMES``."""
        return self.problems + self.dedupe_problems

    @property
    def ok(self) -> bool:
        """Was every file this depot's plan named dealt with?"""
        return not self.all_problems

    @property
    def chunks_gone(self) -> int:
        """Planned chunks that are gone now — removed here or by someone else.

        This, not ``removed_count``, is what ``needs_force`` keys on: the
        depot's content changed either way, and which process did it makes no
        difference to whether SteamPrefill's bookkeeping is now stale.
        """
        return self.removed_count + self.already_gone_count


def execute_depot(
    depot_plan: gc.DepotGcPlan,
    *,
    depot_root: str,
    exclusions: Sequence[ChunkExclusion] = NO_EXCLUSIONS,
) -> DepotGcResult:
    """Delete one depot's planned orphans and redundant manifest copies.

    Never raises. Every failure is data in the returned result, because a
    single locked chunk file must not abandon the remaining depots (the
    per-depot failure isolation ``deletion.delete_app_depots`` applies at depot
    granularity, applied here at file granularity as well).

    A depot whose status is not ``gc.STATUS_PLANNED`` returns immediately with
    ``executed=False`` and every counter at zero — **no chunk, no manifest
    copy, nothing**. Dedupe is included in that refusal on purpose: the
    duplicate-manifest finding does not depend on the readiness gate (see
    ``gc.DedupeCandidate``), but "GC never touches a depot it refused to plan"
    is a rule worth having in one piece rather than with an exception attached.
    """
    common = dict(
        depotid=depot_plan.depotid,
        status=depot_plan.status,
        planned_orphan_count=depot_plan.orphan_count,
        planned_orphan_bytes=depot_plan.orphan_bytes,
        kept_count=depot_plan.kept_count,
        kept_bytes=depot_plan.kept_bytes,
        planned_dedupe_count=sum(len(c.duplicates) for c in depot_plan.dedupe),
        planned_dedupe_bytes=depot_plan.dedupe_bytes,
        oracle_protected_count=depot_plan.oracle_protected_count,
        oracle_protected_bytes=depot_plan.oracle_protected_bytes,
        note=depot_plan.note,
    )

    if depot_plan.status != gc.STATUS_PLANNED:
        return DepotGcResult(executed=False, **common)

    try:
        depot_dir = deletion.depot_dir_path(depot_root, depot_plan.depotid)
        chunk_dir = deletion.safe_child_path(depot_dir, gc.CHUNK_DIRNAME)
    except deletion.DeletionGuardError as exc:
        # Unreachable via plan_gc (which built its own paths through the same
        # guards), and kept anyway: this is the point where a plan from
        # anywhere else would be refused rather than trusted.
        logger.error(
            "cache-gc depot=%s REFUSED by the path guard at execute time: %s",
            depot_plan.depotid, exc,
        )
        return DepotGcResult(
            executed=False,
            problems=[
                FileRemoval(
                    name=str(depot_plan.depotid),
                    outcome=REFUSED_UNSAFE_NAME,
                    detail=str(exc),
                )
            ],
            **common,
        )

    to_delete, held_back = orphans_to_delete(depot_plan, exclusions=exclusions)

    real_chunk_dir = expected_real_dir(
        depot_root, depot_plan.depotid, gc.CHUNK_DIRNAME
    )
    removals: list[FileRemoval] = []
    for chunk_id in sorted(to_delete):
        removals.append(_remove_planned_chunk(chunk_dir, chunk_id, real_chunk_dir))

    dedupe_removals = execute_dedupe(depot_plan, depot_dir=depot_dir)

    removed = [r for r in removals if r.outcome == REMOVED]
    dedupe_removed = [r for r in dedupe_removals if r.outcome == REMOVED]

    result = DepotGcResult(
        executed=True,
        removed_count=len(removed),
        removed_bytes=sum(r.bytes_freed for r in removed),
        already_gone_count=sum(1 for r in removals if r.outcome == ALREADY_GONE),
        held_back=held_back,
        held_back_bytes=_held_back_bytes(depot_plan, held_back),
        problems=[r for r in removals if r.is_problem],
        dedupe_removed_count=len(dedupe_removed),
        dedupe_removed_bytes=sum(r.bytes_freed for r in dedupe_removed),
        dedupe_declined=[
            r for r in dedupe_removals if r.outcome in DECLINED_OUTCOMES
        ],
        dedupe_problems=[
            r
            for r in dedupe_removals
            if r.is_problem and r.outcome not in DECLINED_OUTCOMES
        ],
        **common,
    )

    logger.info(
        "cache-gc depot=%s EXECUTED: planned=%d removed=%d bytes_freed=%d "
        "already_gone=%d held_back=%d problems=%d dedupe_removed=%d/%d bytes",
        depot_plan.depotid, len(to_delete), result.removed_count,
        result.removed_bytes, result.already_gone_count, len(held_back),
        len(result.all_problems), result.dedupe_removed_count,
        result.dedupe_removed_bytes,
    )
    for problem in result.all_problems:
        logger.warning(
            "cache-gc depot=%s NOT REMOVED %s", depot_plan.depotid, problem.describe()
        )
    return result


def _held_back_bytes(
    depot_plan: gc.DepotGcPlan, held_back: Mapping[str, str]
) -> int:
    """Plan bytes of the held-back chunks.

    From the **plan's** sizes, not from a fresh ``lstat``: nothing was deleted,
    so this is a statement about what stayed, not about what was freed — the
    opposite of ``bytes_freed``, which is measured immediately before each
    unlink precisely because it claims something happened.
    """
    return sum(depot_plan.orphan_chunks.get(chunk_id, 0) for chunk_id in held_back)


def _remove_planned_chunk(
    chunk_dir: str, chunk_id: str, real_chunk_dir: str
) -> FileRemoval:
    """Re-validate one planned chunk id and remove its file.

    The re-validation is not ceremony. ``gc.plan_gc`` guarantees every id in
    ``orphan_chunks`` is 40 lowercase hex characters and therefore safe to join
    onto a path — but that guarantee lives in another module, and this is the
    line of code that turns a string from a data structure into a filesystem
    path. Checking it here means the promise "GC cannot be talked into deleting
    something outside ``depot/<id>/chunk/``" is enforced by the code that does
    the deleting, so it survives any future change to the planner.
    """
    if not gc.is_chunk_filename(chunk_id):
        return FileRemoval(
            name=chunk_id,
            outcome=REFUSED_UNSAFE_NAME,
            detail="not a 40-character lowercase hex chunk id",
        )
    try:
        path = deletion.safe_child_path(chunk_dir, chunk_id)
    except deletion.DeletionGuardError as exc:  # pragma: no cover - see the guard
        return FileRemoval(
            name=chunk_id, outcome=REFUSED_UNSAFE_NAME, detail=str(exc)
        )
    return remove_one_file(path, name=chunk_id, expected_parent=real_chunk_dir)


def execute_dedupe(
    depot_plan: gc.DepotGcPlan, *, depot_dir: str
) -> list[FileRemoval]:
    """Remove redundant copies of a stored manifest, keeping the newest.

    ADR-0007 item 3. Manifest URLs carry a per-request code, so the same
    manifest legitimately lands on disk several times under different
    filenames; ``gc.dedupe_candidates`` marks the newest copy as ``keep`` and
    the rest as ``duplicates``.

    Two guards this adds on top of the plan, both fail-closed:

    - **The keeper is verified first.** If the copy that would be kept is
      missing, a link, or not a regular file, the whole candidate is left
      alone (``REFUSED_NO_KEEPER``). Dedupe must never end with zero copies of
      a manifest, and "delete the others, then discover the keeper was gone"
      is exactly how that would happen. Its usual cause — a concurrent
      ``DELETE /v1/cache/{appid}`` taking the depot away mid-run — is why this
      is a *decline*, not a failure (``DECLINED_OUTCOMES``).
    - **Only byte-identical duplicates are removed.** ``gc.DedupeCandidate``
      explicitly does not verify identity and carries the sizes so this
      decision could be made here; the decision is to verify, by comparing the
      bytes (``files_are_identical``). Same manifest id is a strong reason to
      *expect* identical content, but a truncated store-on-miss write is a real
      possibility this codebase already handles elsewhere — and if the newest
      copy happens to be the truncated one, blind dedupe would delete the good
      older copy and leave the corrupt one as the depot's only manifest
      evidence. A duplicate that differs (or cannot be read for comparison) is
      reported as ``KEPT_DIFFERING`` and survives. It is a *finding*, not a
      failure: nothing went wrong, GC simply declined to reclaim those bytes,
      so it does not make the job ``error``.

    Every path is rebuilt from validated components
    (``depot/<id>/manifest/<manifestid>/5/<name>``) and cross-checked against
    the path the scan recorded; a mismatch is refused. That cross-check is
    lexical, and a lexical path CAN escape the tree when a directory on it is
    a symlink planted on the shared volume — so, since WP SEC-FIX-4 (S-2), the
    request directory must also ``realpath`` to
    ``realpath(depot_root)/<id>/manifest/<mid>/5`` before anything in it is
    compared or unlinked, and every unlink re-checks its parent the same way.
    """
    results: list[FileRemoval] = []
    for candidate in depot_plan.dedupe:
        try:
            request_dir = deletion.safe_child_path(
                deletion.safe_child_path(
                    deletion.safe_child_path(depot_dir, gc.MANIFEST_DIRNAME),
                    candidate.manifestid,
                ),
                gc.MANIFEST_REQUEST_DIR,
            )
        except deletion.DeletionGuardError as exc:  # pragma: no cover - see the guard
            results.append(
                FileRemoval(
                    name=candidate.manifestid,
                    outcome=REFUSED_UNSAFE_NAME,
                    detail=str(exc),
                )
            )
            continue

        real_request_dir = expected_real_dir(
            os.path.dirname(depot_dir),
            os.path.basename(depot_dir),
            gc.MANIFEST_DIRNAME,
            candidate.manifestid,
            gc.MANIFEST_REQUEST_DIR,
        )
        if os.path.realpath(request_dir) != real_request_dir:
            # S-2: some directory on the way (depot/<id>, manifest/, <mid>/,
            # 5/) is a link. Nothing is compared, nothing is unlinked.
            results.append(
                FileRemoval(
                    name=candidate.manifestid,
                    outcome=REFUSED_LINK,
                    detail=(
                        f"{request_dir} does not resolve to {real_request_dir}: "
                        "a directory on the way is a symlink or junction"
                    ),
                )
            )
            continue

        keep_path = _verified_copy_path(request_dir, candidate.keep.path)
        if keep_path is None or not _is_usable_manifest_copy(keep_path):
            results.append(
                FileRemoval(
                    name=candidate.manifestid,
                    outcome=REFUSED_NO_KEEPER,
                    detail=(
                        f"the copy that would be kept ({candidate.keep.path}) is "
                        "missing or unusable, so its duplicates were left alone"
                    ),
                )
            )
            continue

        for duplicate in candidate.duplicates:
            name = os.path.basename(duplicate.path)
            dup_path = _verified_copy_path(request_dir, duplicate.path)
            if dup_path is None:
                results.append(
                    FileRemoval(
                        name=name,
                        outcome=REFUSED_UNSAFE_NAME,
                        detail=(
                            f"{duplicate.path} is not where this depot's manifest "
                            f"{candidate.manifestid} copies live"
                        ),
                    )
                )
                continue

            identical = files_are_identical(dup_path, keep_path)
            if identical is None:
                results.append(
                    FileRemoval(
                        name=name,
                        outcome=KEPT_DIFFERING,
                        detail="could not be compared against the kept copy",
                    )
                )
                continue
            if not identical:
                results.append(
                    FileRemoval(
                        name=name,
                        outcome=KEPT_DIFFERING,
                        detail=(
                            "differs from the kept copy of manifest "
                            f"{candidate.manifestid}; kept so the depot does not "
                            "lose its only good manifest evidence"
                        ),
                    )
                )
                continue

            results.append(
                remove_one_file(
                    dup_path, name=name, expected_parent=real_request_dir
                )
            )
    return results


def _verified_copy_path(request_dir: str, recorded_path: str) -> str | None:
    """The scan's path for one stored manifest copy, rebuilt and cross-checked.

    Returns the rebuilt absolute path, or ``None`` when the recorded path is
    not the direct child of ``request_dir`` it claims to be.
    """
    try:
        rebuilt = deletion.safe_child_path(
            request_dir, os.path.basename(recorded_path)
        )
    except deletion.DeletionGuardError:  # pragma: no cover - see the guard
        return None
    if os.path.normpath(rebuilt) != os.path.normpath(recorded_path):
        return None  # pragma: no cover - scandir paths cannot disagree
    return rebuilt


def _is_usable_manifest_copy(path: str) -> bool:
    """True for an existing, non-link, regular file.

    Same doubled link check as ``remove_one_file``, and for the same measured
    reason: a Windows junction is not ``os.path.islink`` (WP 1.6), so
    ``is_link_like``'s ``isjunction`` arm is what catches it — and the
    regular-file test at the end would catch it too, since a junction cannot
    point at a file. Redundant, kept, and said out loud.
    """
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat_module.S_ISLNK(st.st_mode) or deletion.is_link_like(path):
        return False
    return stat_module.S_ISREG(st.st_mode)


# --------------------------------------------------------------------------
# The whole run
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class GcRunReport:
    """Everything one GC job did, and everything it deliberately did not do."""

    appid: int
    #: What the operator asked for.
    requested_execute: bool
    #: Whether deletions were actually attempted. False for a dry run, and
    #: false for an execute run whose plan could not be built at all.
    executed: bool
    #: False when the run never got as far as a plan (an unusable cache root).
    plan_ok: bool = True
    plan_error: str = ""
    depots: list[DepotGcResult] = field(default_factory=list)
    #: App ids whose ``needs_force`` this run set to 1 (see the module
    #: docstring). Always empty for a dry run.
    flagged_appids: list[int] = field(default_factory=list)
    #: WP 3.12: an operator cancelled this run part-way. The depots in
    #: ``depots`` were fully processed; ``skipped_depots`` were never started.
    cancelled: bool = False
    #: Depot ids the cancellation left untouched, in plan order.
    skipped_depots: list[int] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """``done`` vs ``error`` for the job row.

        A run is ``ok`` when the plan was built, every **chunk** it planned to
        remove is gone, and nothing else it attempted failed.

        Two things are deliberately NOT failures. Depot *skips*: a skip is a
        reported, deliberate outcome (ADR-0007's readiness gate doing its job),
        and a run that correctly refuses to touch anything is a successful run.
        And dedupe *declines* (``DECLINED_OUTCOMES``): GC looked, applied its
        own safety rule and reclaimed nothing, which is a finding — see that
        constant for the one-line rule.
        """
        return self.plan_ok and all(depot.ok for depot in self.depots)

    @property
    def planned_orphan_count(self) -> int:
        return sum(d.planned_orphan_count for d in self.depots)

    @property
    def planned_orphan_bytes(self) -> int:
        return sum(d.planned_orphan_bytes for d in self.depots)

    @property
    def planned_dedupe_bytes(self) -> int:
        return sum(d.planned_dedupe_bytes for d in self.depots)

    @property
    def removed_count(self) -> int:
        return sum(d.removed_count for d in self.depots)

    @property
    def removed_bytes(self) -> int:
        return sum(d.removed_bytes for d in self.depots)

    @property
    def already_gone_count(self) -> int:
        return sum(d.already_gone_count for d in self.depots)

    @property
    def held_back_count(self) -> int:
        return sum(len(d.held_back) for d in self.depots)

    @property
    def held_back_bytes(self) -> int:
        return sum(d.held_back_bytes for d in self.depots)

    @property
    def deletable_orphan_count(self) -> int:
        """Planned orphans MINUS the ones an exclusion holds back — i.e. what an
        execute run would actually attempt right now."""
        return self.planned_orphan_count - self.held_back_count

    @property
    def deletable_orphan_bytes(self) -> int:
        return self.planned_orphan_bytes - self.held_back_bytes

    @property
    def dedupe_removed_count(self) -> int:
        return sum(d.dedupe_removed_count for d in self.depots)

    @property
    def dedupe_removed_bytes(self) -> int:
        return sum(d.dedupe_removed_bytes for d in self.depots)

    @property
    def bytes_freed(self) -> int:
        """Chunk bytes plus manifest-copy bytes actually reclaimed by this run."""
        return self.removed_bytes + self.dedupe_removed_bytes

    @property
    def problems(self) -> list[FileRemoval]:
        return [p for depot in self.depots for p in depot.all_problems]

    @property
    def declined(self) -> list[FileRemoval]:
        """Reclaims GC deliberately passed on (``DECLINED_OUTCOMES``)."""
        return [d for depot in self.depots for d in depot.dedupe_declined]

    @property
    def planned_depots(self) -> list[int]:
        return [d.depotid for d in self.depots if d.status == gc.STATUS_PLANNED]

    @property
    def touched_depots(self) -> list[int]:
        """Depots whose chunk content this run actually changed."""
        return [d.depotid for d in self.depots if d.executed and d.chunks_gone]

    def log_text(self) -> str:
        """The job's ``log_excerpt``. **Totals last, on purpose.**

        ``jobs.finish_job`` keeps the LAST 4 KiB (``jobs.tail_excerpt``), so a
        run over a hundred depots must not put its summary at the top where
        truncation would eat it.
        """
        mode = "EXECUTE" if self.requested_execute else "DRY RUN"
        lines = [f"[vault-api] GC for app {self.appid}: {mode}."]

        if not self.plan_ok:
            lines.append(f"[vault-api] GC could not plan anything: {self.plan_error}")
            lines.append(
                "[vault-api] GC totals: NOTHING was planned, inspected or deleted."
            )
            return "\n".join(lines)

        for depot in self.depots:
            lines.append(_depot_line(depot))
            # '!' = something went wrong, '-' = GC declined on purpose,
            # '~' = an exclusion held a planned orphan back (WP 3.8b's grace
            # window). Three markers because they mean different things to an
            # operator, and a single "not reclaimed" list would hide that.
            for chunk_id in sorted(depot.held_back)[:MAX_REPORTED_PROBLEMS]:
                lines.append(f"    ~ {chunk_id}: {depot.held_back[chunk_id]}")
            extra = len(depot.held_back) - MAX_REPORTED_PROBLEMS
            if extra > 0:
                lines.append(f"    ~ ... and {extra} more held back")
            for problem in depot.all_problems[:MAX_REPORTED_PROBLEMS]:
                lines.append(f"    ! {problem.describe()}")
            extra = len(depot.all_problems) - MAX_REPORTED_PROBLEMS
            if extra > 0:
                lines.append(f"    ! ... and {extra} more")
            for declined in depot.dedupe_declined[:MAX_REPORTED_PROBLEMS]:
                lines.append(f"    - {declined.describe()}")
            extra = len(depot.dedupe_declined) - MAX_REPORTED_PROBLEMS
            if extra > 0:
                lines.append(f"    - ... and {extra} more")

        if self.cancelled:
            # Ahead of the totals on purpose, so the numbers are read with the
            # partial-pass caveat already in view rather than after it.
            lines.append(
                "[vault-api] GC was CANCELLED on request after "
                f"{len(self.depots)} depot(s). {len(self.skipped_depots)} "
                f"depot(s) were never started: {self.skipped_depots}. The "
                "totals below cover only the depots that did run — they are "
                "exact for those and say nothing about the rest."
            )

        if self.requested_execute:
            lines.append(
                f"[vault-api] GC totals (EXECUTED): chunks_removed={self.removed_count} "
                f"bytes_freed={self.removed_bytes} already_gone={self.already_gone_count} "
                f"dedupe_removed={self.dedupe_removed_count} "
                f"dedupe_bytes_freed={self.dedupe_removed_bytes} "
                f"total_bytes_freed={self.bytes_freed} "
                f"problems={len(self.problems)} declined={len(self.declined)} "
                f"held_back={self.held_back_count} ({self.held_back_bytes} bytes) "
                f"depots_touched={self.touched_depots} "
                f"needs_force_set_for={self.flagged_appids}"
            )
            if self.problems:
                lines.append(
                    "[vault-api] This run did NOT remove everything it planned "
                    "(see the '!' lines above); the job is reported as 'error' and "
                    "the counts above are exactly what did happen."
                )
        else:
            lines.append(
                f"[vault-api] GC totals (DRY RUN): orphans={self.planned_orphan_count} "
                f"({self.planned_orphan_bytes} bytes) "
                f"held_back={self.held_back_count} ({self.held_back_bytes} bytes) "
                f"would_delete={self.deletable_orphan_count} "
                f"({self.deletable_orphan_bytes} bytes) reclaimable_dedupe_bytes="
                f"{self.planned_dedupe_bytes} planned_depots={self.planned_depots}. "
                "NOTHING was deleted — re-run with {\"execute\": true} to reclaim it."
            )
        return "\n".join(lines)


def _depot_line(depot: DepotGcResult) -> str:
    if depot.status != gc.STATUS_PLANNED:
        note = depot.note[:MAX_LOGGED_NOTE_CHARS]
        suffix = "..." if len(depot.note) > MAX_LOGGED_NOTE_CHARS else ""
        return f"  depot {depot.depotid} {depot.status}: {note}{suffix}"
    # WP 3.9: appended only when the oracle actually protected something, so a
    # default install's log lines are byte-identical to their pre-WP-3.9 form
    # and an operator who sees this suffix knows it means something.
    oracle_suffix = (
        ""
        if not depot.oracle_protected_count
        else (
            f" oracle_protected={depot.oracle_protected_count} "
            f"({depot.oracle_protected_bytes} bytes)"
        )
    )
    if not depot.executed:
        return (
            f"  depot {depot.depotid} planned: orphans={depot.planned_orphan_count} "
            f"({depot.planned_orphan_bytes} bytes) held_back={len(depot.held_back)} "
            f"({depot.held_back_bytes} bytes) kept={depot.kept_count} "
            f"({depot.kept_bytes} bytes) dedupe_candidates={depot.planned_dedupe_count} "
            f"({depot.planned_dedupe_bytes} bytes)" + oracle_suffix
        )
    return (
        f"  depot {depot.depotid} planned: orphans={depot.planned_orphan_count} "
        f"({depot.planned_orphan_bytes} bytes) -> removed={depot.removed_count} "
        f"({depot.removed_bytes} bytes) already_gone={depot.already_gone_count} "
        f"held_back={len(depot.held_back)} ({depot.held_back_bytes} bytes) "
        f"problems={len(depot.all_problems)} "
        f"kept={depot.kept_count} ({depot.kept_bytes} bytes) "
        f"dedupe_removed={depot.dedupe_removed_count} "
        f"({depot.dedupe_removed_bytes} bytes) "
        f"dedupe_declined={len(depot.dedupe_declined)}" + oracle_suffix
    )


def run_gc(
    conn: sqlite3.Connection,
    appid: int,
    *,
    cache_root: str,
    archive_dir: str,
    execute: bool,
    exclusions: Sequence[ChunkExclusion] = NO_EXCLUSIONS,
    should_cancel: Callable[[], bool] | None = None,
    oracle_manifests: Mapping[int, Sequence[str]] | None = None,
) -> GcRunReport:
    """Plan garbage collection for one app **now**, and — only if ``execute``
    — carry it out. Never raises for an expected failure.

    ``execute`` is a required keyword argument with no default: see
    ``jobs.enqueue_gc`` for why the one flag that decides whether files are
    deleted is never allowed to be implicit.

    ``oracle_manifests`` (WP 3.9, ADR-0007 decision B) can only *grow* the
    keep set — the exact mirror image of ``exclusions``, and the same net
    direction: both can only shrink what is deleted. ``None`` means "no
    oracle", which is what ``run_gc_job`` passes whenever the feature is off
    and therefore the behaviour every pre-WP-3.9 caller keeps.

    ``exclusions`` can only shrink what gets deleted (see ``ChunkExclusion``).
    They are applied to a **dry run as well**, where they delete nothing and
    only populate ``held_back`` — a preview that ignored them would predict
    bytes the execute run then refuses to free. ``run_gc_job`` supplies the
    configured grace window via ``grace_window_exclusions``.

    There is deliberately **no ``plan`` parameter**. The plan is built here,
    from a fresh database read and a fresh filesystem scan, every single time —
    that is what makes "GC never executes an enqueue-time plan" a property of
    the interface rather than a habit of its callers (module docstring,
    "TOCTOU").

    ``should_cancel`` (WP 3.12) is polled **between depots** and stops the run
    where it stands. Cooperative on purpose, at exactly that granularity:

    - *Not mid-depot*, because one depot is bounded work and stopping half way
      through it would leave the least useful state of all — some of that
      depot's orphans gone, some not, with the operator unable to tell which
      without reading the log line by line. A depot either was processed
      completely or was never started, and the report says which.
    - *Not "abandon everything"*, because what was already removed IS removed;
      a cancelled GC run reports exactly what it did, the same honesty rule the
      module docstring sets out for partial work.
    - Cancellation is checked before the FIRST depot too, so a cancel that
      lands while the job is still starting up can stop it before it touches
      anything.

    It is polled in the dry-run branch as well. A dry run reads the filesystem
    for every depot, which on a large cache is not instant, and "cancel" must
    not mean "cancel, unless you happened to ask for a preview".
    """
    try:
        depot_root = deletion.resolve_depot_root(cache_root)
    except deletion.DeletionGuardError as exc:
        logger.error("cache-gc appid=%s REFUSED (cache root guard): %s", appid, exc)
        return GcRunReport(
            appid=appid,
            requested_execute=execute,
            executed=False,
            plan_ok=False,
            plan_error=str(exc),
        )

    inputs = gc.load_gc_inputs(conn, appid, oracle_manifests=oracle_manifests)
    plan = gc.plan_gc(appid, inputs, depot_root=depot_root, archive_dir=archive_dir)

    if not execute:
        # The exclusions run here too (WP 3.8b). A dry run is the operator's
        # preview of an execute run, and one that reported 700 orphan bytes
        # which an execute run then refused to free would be a preview of
        # something else. Predicates only read; running them changes nothing.
        dry: list[DepotGcResult] = []
        for index, depot in enumerate(plan.depots):
            if should_cancel is not None and should_cancel():
                skipped = [d.depotid for d in plan.depots[index:]]
                logger.info(
                    "cache-gc appid=%s dry run CANCELLED after %d depot(s); "
                    "%d depot(s) not inspected: %s",
                    appid, index, len(skipped), skipped,
                )
                return GcRunReport(
                    appid=appid,
                    requested_execute=False,
                    executed=False,
                    depots=dry,
                    cancelled=True,
                    skipped_depots=skipped,
                )
            _, held_back = orphans_to_delete(depot, exclusions=exclusions)
            dry.append(
                DepotGcResult(
                    depotid=depot.depotid,
                    status=depot.status,
                    executed=False,
                    planned_orphan_count=depot.orphan_count,
                    planned_orphan_bytes=depot.orphan_bytes,
                    kept_count=depot.kept_count,
                    kept_bytes=depot.kept_bytes,
                    planned_dedupe_count=sum(len(c.duplicates) for c in depot.dedupe),
                    planned_dedupe_bytes=depot.dedupe_bytes,
                    oracle_protected_count=depot.oracle_protected_count,
                    oracle_protected_bytes=depot.oracle_protected_bytes,
                    held_back=held_back,
                    held_back_bytes=_held_back_bytes(depot, held_back),
                    note=depot.note,
                )
            )
        return GcRunReport(
            appid=appid,
            requested_execute=False,
            executed=False,
            depots=dry,
        )

    # A plain loop rather than the comprehension this used to be: the
    # cancellation check has to happen BETWEEN depots, which a comprehension
    # cannot express (WP 3.12).
    results: list[DepotGcResult] = []
    cancelled = False
    skipped: list[int] = []
    for index, depot in enumerate(plan.depots):
        if should_cancel is not None and should_cancel():
            cancelled = True
            skipped = [d.depotid for d in plan.depots[index:]]
            logger.info(
                "cache-gc appid=%s CANCELLED after %d depot(s); %d depot(s) "
                "not started: %s",
                appid, index, len(skipped), skipped,
            )
            break
        results.append(
            execute_depot(depot, depot_root=depot_root, exclusions=exclusions)
        )

    # Runs for the depots that DID execute even on a cancelled run: their
    # chunks are really gone, so every app mapped to them really does have
    # stale SteamPrefill bookkeeping. Skipping this because the run was
    # cancelled would leave the exact wedge ADR-0003's addendum closes.
    touched = [d.depotid for d in results if d.executed and d.chunks_gone]
    flagged = flag_needs_force_for_depots(conn, touched)

    report = GcRunReport(
        appid=appid,
        requested_execute=True,
        executed=True,
        depots=results,
        flagged_appids=flagged,
        cancelled=cancelled,
        skipped_depots=skipped,
    )
    logger.info(
        "cache-gc appid=%s finished: removed=%d chunk(s) / %d byte(s), dedupe "
        "removed=%d / %d byte(s), already_gone=%d, held_back=%d / %d byte(s), "
        "problems=%d, depots_touched=%s, needs_force set for %s",
        appid, report.removed_count, report.removed_bytes,
        report.dedupe_removed_count, report.dedupe_removed_bytes,
        report.already_gone_count, report.held_back_count, report.held_back_bytes,
        len(report.problems), touched, flagged,
    )
    return report


def flag_needs_force_for_depots(
    conn: sqlite3.Connection, depotids: Iterable[int]
) -> list[int]:
    """Set ``needs_force = 1`` for every app mapped to any of these depots.

    See the module docstring for the decision and the counter-argument. The
    input is the list of depots whose chunk content this run actually changed —
    never the planned list, never the full depot list — so a run that removed
    nothing flags nobody.

    Every app mapped to the depot is flagged, not just the requesting one: a
    shared depot's bytes just changed under all of its co-owners, and the one
    that did not ask for the GC has exactly the same stale-bookkeeping problem
    as the one that did (the reasoning ADR-0003's addendum already applies to
    remnant deletions). Returns the sorted app ids that were flagged, for the
    job log.
    """
    ids = sorted({int(d) for d in depotids if isinstance(d, int) and d > 0})
    if not ids:
        return []

    appids: set[int] = set()
    for start in range(0, len(ids), OWNER_LOOKUP_BATCH):
        batch = ids[start : start + OWNER_LOOKUP_BATCH]
        placeholders = ",".join("?" for _ in batch)
        rows = conn.execute(
            f"SELECT DISTINCT appid FROM depot_app_map WHERE depotid IN ({placeholders})",
            batch,
        ).fetchall()
        for row in rows:
            owner = deletion.coerce_positive_id(row["appid"])
            if owner is not None:
                appids.add(owner)

    deletion.set_needs_force(conn, appids)
    return sorted(appids)


# --------------------------------------------------------------------------
# The worker's entry point
# --------------------------------------------------------------------------


def run_gc_job(
    conn: sqlite3.Connection,
    job: Mapping[str, object],
    *,
    settings: Settings,
    size_cache: SizeCache | None = None,
    webhook_notifier: WebhookNotifier | None = None,
) -> None:
    """Run one claimed ``gc`` job to completion and record its outcome.

    Called by ``worker.PrefillWorker._execute``; kept here (and public) rather
    than written as a worker method so the whole GC job path is testable
    without a thread, and so the worker stays the thin dispatcher it is.

    **Never raises** — the worker thread must survive any bug in here, exactly
    as it does for a prefill job.

    ``apps.status`` is deliberately not touched in any branch (module
    docstring): GC does not change whether a game is filled, only how much
    dead weight its depots carry — and that stays true for a cancelled run.

    **Cancellation (WP 3.12) beats both other outcomes when it happened.** A
    run stopped between depots is reported ``cancelled``, not ``done`` (it did
    not finish what it planned) and not ``error`` (nothing went wrong — an
    operator asked it to stop). If the depots it DID process also produced
    problems, the log still lists every one of them; the status just names the
    reason the run ended, and "an operator stopped it" is the more informative
    of the two.
    """
    job_id = int(job["id"])  # type: ignore[arg-type]
    appid = int(job["appid"])  # type: ignore[arg-type]
    execute = jobs.job_deletes(job)

    logger.info(
        "Starting GC job %s for appid %s (%s)",
        job_id, appid, "EXECUTE" if execute else "dry run",
    )

    try:
        report = run_gc(
            conn,
            appid,
            cache_root=settings.cache_root,
            archive_dir=settings.manifest_archive_dir,
            execute=execute,
            # WP 3.8b: the configured grace window, applied to dry runs and
            # execute runs alike so the preview matches what would happen.
            exclusions=grace_window_exclusions(settings),
            # WP 3.12: cooperative cancellation. Read from the job row on the
            # worker's own connection (thread-confined, same as every other
            # use of `conn` in here) between depots. Only 'cancel' counts —
            # a GC job can never carry a 'pause' request (the endpoint refuses
            # to pause GC), and an unrecognized value must not stop a run.
            should_cancel=lambda: (
                jobs.read_stop_request(conn, job_id) == jobs.STOP_REQUEST_CANCEL
            ),
            # WP 3.9 / ADR-0007 decision B: open beta branches' manifests join
            # the keep set when the oracle is enabled. `gc_keepset_gids`
            # returns {} when it is not (and when anything at all goes wrong
            # reading it), so this line is a no-op on a default install.
            #
            # This is the SINGLE place the oracle enters the deletion path, and
            # it sits below every entry point: the manual
            # `POST /v1/cache/{appid}/gc` and WP 3.12's auto-GC hook both queue
            # an ordinary `gc` job that the worker runs through `run_gc_job`,
            # so an auto-queued collection inherits the beta-branch keep set
            # without auto-GC needing to know the oracle exists.
            oracle_manifests=oracle.gc_keepset_gids(conn, appid, settings=settings),
        )
    except Exception:
        # Last-resort net, same shape as the prefill path's. A crash here has
        # unknown on-disk consequences, so the job is 'error' and says so.
        logger.exception("GC job %s crashed", job_id)
        webhooks.finish_job_and_notify(
            conn,
            webhook_notifier,
            job_id,
            jobs.STATUS_ERROR,
            "[vault-api] Internal error while running this GC job. Some chunks may "
            "already have been deleted before the error — see the server log for "
            "what was removed.\n" + traceback.format_exc(),
        )
        return

    # Before finishing the job, so a client that polls the job to 'done' and
    # then reads GET /v1/games immediately sees the reclaimed space rather than
    # a pre-GC size for up to VAULT_SIZE_CACHE_TTL seconds.
    if size_cache is not None and report.bytes_freed:
        size_cache.invalidate()

    if report.cancelled:
        status = jobs.STATUS_CANCELLED
    else:
        status = jobs.STATUS_DONE if report.ok else jobs.STATUS_ERROR
    webhooks.finish_job_and_notify(
        conn, webhook_notifier, job_id, status, report.log_text(),
        bytes_freed=report.bytes_freed,
    )
    logger.info(
        "GC job %s for appid %s finished '%s' (%s; %d byte(s) freed)",
        job_id, appid, status, "execute" if execute else "dry run",
        report.bytes_freed,
    )
