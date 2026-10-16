"""Manifest ingestion: after a successful prefill job, learn what SteamPrefill
just wrote (WP 3.2).

This is the orchestration layer that ties the three pieces WP 3.1/3.2 built
together — none of which know about each other:

- ``vault_api.manifests`` parses a ``.bin`` file's bytes (pure, no I/O side
  effects beyond reading the one file).
- ``vault_api.depot_manifests`` records "what we currently believe this
  app/depot's manifest is" in SQLite (ADR-0006 decision 3).
- ``vault_api.manifest_archive`` copies the source file out durably, since
  SteamPrefill's own temp cache does not survive its ``clear-temp``.

``ingest_after_prefill`` is called by ``vault_api/worker.py`` right after a
prefill job succeeds. It is deliberately designed to **never fail the job**:
a parse failure is data (warn + skip that one file), and the worker wraps the
whole call in its own try/except as a second line of defense in case this
module has a bug — ingestion is a nice-to-have layered on top of a prefill
that has already succeeded, not a gate on it.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Iterable

from vault_api import depot_manifests, manifest_archive
from vault_api.config import Settings
from vault_api.jobs import immediate_transaction, parse_utc_iso, utcnow_iso
from vault_api.mapping import upsert_mapping
from vault_api.manifests import (
    ManifestParseError,
    SOURCE_STEAMPREFILL_BIN,
    parse_bin_filename,
    parse_steamprefill_bin,
)

logger = logging.getLogger(__name__)

#: How many parse-failure filenames a job's log excerpt shows before summarizing
#: the rest as a count. Same bound/rationale as manifests.py's zip-namelist
#: truncation (WP 3.1 review carry-over): a job that ingests from a cache
#: directory full of bad files must not turn into a multi-MB log excerpt.
MAX_LOGGED_NAMES = 10


@dataclass(frozen=True)
class IngestedManifest:
    """One ``.bin`` file this pass successfully ingested."""

    depotid: int
    manifestid: str
    containing_appid: int
    chunk_count: int
    total_bytes: int
    #: ``None`` if the DB row was recorded but the archive copy itself failed
    #: (see ``ingest_after_prefill``'s inner ``except OSError``) — an absent
    #: path is a real "no archive exists" state, distinct from an empty
    #: string, which would look like a (wrong) path relative to somewhere.
    archived_path: str | None


@dataclass(frozen=True)
class IngestResult:
    """What one ``ingest_after_prefill`` call did — folded into the job's log
    excerpt so an operator sees it without a separate query."""

    ingested: list[IngestedManifest] = field(default_factory=list)
    #: Filenames still present on disk that failed to parse for a DATA
    #: reason (corrupt bytes, a filename that doesn't match the contract, an
    #: id mismatch) — warn + skip, never fails the job.
    parse_failures: list[str] = field(default_factory=list)
    #: Filenames that vanished BETWEEN ``os.scandir()`` listing them and the
    #: parse attempt a few lines later — an I/O race (SteamPrefill's own
    #: ``clear-temp`` running concurrently, or an operator clearing the
    #: directory by hand), not a data-quality problem. Kept separate from
    #: ``parse_failures`` (WP 3.2 review nitpick): conflating "this file is
    #: bad" with "this file disappeared out from under us" would make a
    #: perfectly benign race read like a corruption finding in the job log.
    vanished_during_scan: list[str] = field(default_factory=list)
    #: Well-formed files this pass deliberately did NOT record because a
    #: newer file exists for the same depot (WP UPD-FIX-1,
    #: ``select_current_manifest``): manifests left over from earlier runs.
    superseded: list[str] = field(default_factory=list)
    #: True if the configured cache dir does not exist / isn't readable —
    #: the common, unremarkable case on a host where VAULT_STEAMPREFILL_PATH
    #: itself is unset or SteamPrefill has never run. Not an error.
    cache_dir_unavailable: bool = False
    #: Depots this pass newly mapped to the job's own app (WP
    #: MAP-FROM-MANIFEST-1): the manifest names the app as the original
    #: requester and the depot has bytes in the cache, but no
    #: ``depot_app_map`` row existed (an all-hit run observes nothing, so the
    #: before/after diff never maps it). Already-mapped depots are not listed.
    mapped_own: list[int] = field(default_factory=list)

    def summary(self) -> str:
        if self.cache_dir_unavailable:
            return (
                "[vault-api] Manifest ingestion: SteamPrefill temp-cache directory "
                "not found or unreadable; nothing ingested (VAULT_STEAMPREFILL_CACHE_DIR)."
            )
        if (
            not self.ingested
            and not self.parse_failures
            and not self.vanished_during_scan
            and not self.superseded
        ):
            return "[vault-api] Manifest ingestion: no matching .bin files found."

        parts = []
        if self.ingested:
            depots = sorted({entry.depotid for entry in self.ingested})
            parts.append(
                f"[vault-api] Manifest ingestion: recorded {len(self.ingested)} "
                f"manifest(s) for depot(s) {depots}."
            )
        if self.mapped_own:
            parts.append(
                "[vault-api] Manifest ingestion: mapped depot(s) "
                f"{sorted(self.mapped_own)} to this app (they are cached and "
                "named by this app's manifests, but were not mapped yet)."
            )
        if self.superseded:
            shown = self.superseded[:MAX_LOGGED_NAMES]
            omitted = len(self.superseded) - len(shown)
            suffix = "" if omitted <= 0 else f" (+{omitted} more)"
            parts.append(
                f"[vault-api] Manifest ingestion: ignored {len(self.superseded)} "
                f"older manifest file(s) left over from earlier runs (only the "
                f"newest file per depot is recorded): {shown}{suffix}."
            )
        if self.parse_failures:
            shown = self.parse_failures[:MAX_LOGGED_NAMES]
            omitted = len(self.parse_failures) - len(shown)
            suffix = "" if omitted <= 0 else f" (+{omitted} more)"
            parts.append(
                f"[vault-api] Manifest ingestion: {len(self.parse_failures)} file(s) "
                f"failed to parse and were skipped: {shown}{suffix}."
            )
        if self.vanished_during_scan:
            shown = self.vanished_during_scan[:MAX_LOGGED_NAMES]
            omitted = len(self.vanished_during_scan) - len(shown)
            suffix = "" if omitted <= 0 else f" (+{omitted} more)"
            parts.append(
                f"[vault-api] Manifest ingestion: {len(self.vanished_during_scan)} "
                f"file(s) vanished during the scan (I/O race, not a data problem, "
                f"skipped): {shown}{suffix}."
            )
        return "\n".join(parts)


def ingest_after_prefill(
    conn: sqlite3.Connection,
    *,
    appid: int,
    settings: Settings,
    skip_own_mapping: Iterable[int] = (),
) -> IngestResult:
    """Scan ``settings.steamprefill_cache_dir`` for ``{appid}_*.bin`` files
    belonging to this job's app, record ONE manifest per depot, and archive
    its source file.

    1. Parse every filename (``manifests.parse_bin_filename``) to recover
       ``containing_appid`` — the shared-depot attribution signal — and group
       the files per depot.
    2. **Per depot, pick the one file ``select_current_manifest`` names (WP
       UPD-FIX-1)**: the newest by file mtime. The temp cache keeps every
       manifest SteamPrefill ever downloaded; recording each of them in
       directory order let a leftover overwrite the current one. The others
       are listed as ``superseded`` in the job log and not touched.

    For the chosen file:

    3. Parse the payload (``manifests.parse_steamprefill_bin``), which
       independently re-validates the filename and cross-checks it against
       the payload's own depot/manifest ids (corruption signal).
    4. Upsert ``depot_manifests`` (latest-per-(appid, depotid), replacing any
       older row for this app+depot — ADR-0006 decision 3).
    5. **If ``containing_appid != appid``, additively map the depot to
       ``containing_appid`` too** (``mapping.upsert_mapping``) — this is
       purely additive (ADR-0003), a SEPARATE fact from the job's own
       replace-set mapping (``vault_api.prefill.apply_observed_mapping``,
       which is unaffected by this function and still comes from the
       before/after depot diff exactly as before WP 3.2).
    ``skip_own_mapping``: depots step 5b must not map in THIS call -- the
    worker passes the depots ``apply_observed_mapping`` just removed from this
    app in the same job (a depot Steam reassigned away), so ingestion does not
    undo that decision from a leftover ``.bin``. A depot someone deleted by
    hand is not in this set: with its ``.bin`` and bytes still present it
    reappears on the next run (documented behaviour).

    5b. **Also map the depot to the job's own app** (WP MAP-FROM-MANIFEST-1),
       additively (``INSERT OR IGNORE``, never a delete or reassignment): a
       manifest whose ORIGINAL app is this job's app says "this app's depot
       list contains depot D". Without it an all-hit run (verify/force over a
       fully cached game) observes no new bytes, ``apply_observed_mapping``
       returns early, and a depot cached by an earlier run stays unmapped for
       good (size, completeness and exclusive-depot counts were wrong). It
       runs after ``apply_observed_mapping`` in the worker, so the
       replace-within-app step cannot undo it in the same job. It is only
       done when the depot has bytes in the cache (a leftover ``.bin`` for a
       depot that is gone must not create a phantom row) and never when the
       cache cannot be judged. MAP-DRIFT-1's orphan cleanup only deletes
       ``depot_manifests`` rows for depots NO app maps, so a mapped depot is
       never an orphan; rows of other apps are never touched. Covers both
       ``containing == original`` (the depot is exclusive to this app unless
       another app also maps it) and ``containing != original`` (the
       containing app is mapped by step 5 and this app is mapped too, so the
       depot reads as shared by both).
    6. Archive the ``.bin`` file durably and prune old archives for that
       depot to ``settings.manifest_keep``.

    **A file that fails to parse is warned about and skipped — it never
    fails the job** (this work package's explicit scope). Only files whose
    name starts with ``"{appid}_"`` and ends with ``.bin`` are considered —
    an unrelated app's ``.bin`` files sitting in the same shared directory
    are simply not this call's business (a later job for that app ingests
    them, or they age out via SteamPrefill's own ``clear-temp``).

    Filename matching, precisely: the prefix check is ``f"{appid}_"``, which
    is unambiguous because the appid segment is immediately followed by an
    underscore in the real contract — appid ``44`` cannot match a file whose
    name begins ``441_`` (the character after ``"44"`` there is ``"1"``, not
    ``"_"``).

    **The REVERSE direction of ADR-0003, measured (WP 3.2 review):** the
    additive ``upsert_mapping(depotid, containing_appid)`` call in step 5
    above can later be undone by the CONTAINING app's own next prefill job.
    ``vault_api.prefill.apply_observed_mapping`` is "replace within the app":
    when ``containing_appid``'s own job runs, any depot currently mapped to
    it that is NOT in *that job's* observed set is deleted. If the shared
    depot this function just additively mapped happens to already be fully
    cached from ``containing_appid``'s point of view (so its own next prefill
    writes nothing new for it and therefore never "observes" it), that job's
    replace-within-app step removes the very row this function added.

    **This is not a regression, and it self-heals**: the next time the
    ORIGINAL app (the one actually pulling the shared depot, e.g. 107100 in
    the research doc's example) is prefilled and re-ingested, the additive
    mapping is written again. Nothing is lost permanently — the mapping can
    flicker, not disappear.

    **Which table is authoritative for the shared-depot signal depends on
    what's asking, stated explicitly because two different answers coexist
    on purpose:**

    - ``depot_app_map`` (``vault_api/mapping.py``) is what **today's**
      shared-depot deletion protection reads (``DELETE /v1/cache/{appid}``,
      WP 1.6) — and it is exactly the table subject to the flicker above.
    - ``depot_manifests.containing_appid`` (this work package) is the
      **durable** record of "which app this depot's manifest said it belongs
      to", written fresh on every ingest of the ORIGINAL app and never
      touched by any OTHER app's prefill job. It does NOT have the flicker --
      but as of WP API-FIX-2 nothing reads it for attribution: the shipped
      GC (``vault_api/gc.py::load_gc_inputs`` -> ``deletion.load_mapping_rows``
      and ``deletion.other_owner_ids``) derives co-owners from
      ``depot_app_map`` only, and ``gc.load_recorded_manifests`` reads
      ``depot_manifests`` for manifest IDs, never for ``containing_appid``.
      Consequence: while a co-owner's additive row is flickered out, GC for
      the other app cannot see that co-owner and may delete chunks the
      co-owner still needs. That is a re-download for the co-owner's next
      prefill, never data loss -- exactly the bound ADR-0007 sets for every
      GC mistake -- and the row comes back on the original app's next
      ingest. Making GC read ``containing_appid`` is the obvious follow-up;
      it is not what ships today, and this docstring says so rather than
      describing the intended design as the implemented one.
    """
    cache_dir = settings.steamprefill_cache_dir
    scan = _scan_bin_files(cache_dir, prefix=f"{appid}_")
    if scan is None:
        logger.info(
            "manifest-ingest appid=%s: cache dir %r not found or unreadable; "
            "nothing to ingest.",
            appid, cache_dir,
        )
        return IngestResult(cache_dir_unavailable=True)

    ingested: list[IngestedManifest] = []
    parse_failures: list[str] = list(scan.parse_failures)
    vanished_during_scan: list[str] = list(scan.vanished)
    superseded: list[str] = []
    mapped_own: list[int] = []
    skip_own = {int(d) for d in skip_own_mapping}
    depot_root: str | None = None
    depot_root_resolved = False

    # WP UPD-FIX-1: one manifest per depot, chosen by ``select_current_manifest``
    # -- never "every file, last one wins". See that function for the rule.
    for (original_appid, depotid), group in sorted(scan.groups.items()):
        chosen = select_current_manifest(group)
        if chosen is None:  # pragma: no cover - a group is never empty
            continue
        superseded.extend(
            sorted(candidate.name for candidate in group if candidate is not chosen)
        )
        try:
            manifest = parse_steamprefill_bin(chosen.path)
        except ManifestParseError as exc:
            # Distinguish an I/O race (WP 3.2 review nitpick) from a genuine
            # bad file: if the path is gone by now, it vanished BETWEEN the
            # os.scandir() listing and this parse attempt (SteamPrefill's own
            # clear-temp running concurrently, or an operator clearing the
            # directory by hand) -- not a data-quality problem, and logging it
            # as one would train an operator to distrust a real corruption
            # warning. `lexists`, not `exists`: a broken/dangling link (not
            # expected here, but consistent with deletion.py's own rule)
            # still counts as "something is there".
            #
            # No fallback to an older file of the same depot (WP UPD-FIX-1):
            # recording a superseded manifest because the current one is
            # unreadable is exactly the wrong-gid failure this rule exists to
            # prevent. The depot keeps its previous row.
            if not os.path.lexists(chosen.path):
                logger.info(
                    "manifest-ingest appid=%s file=%s: vanished during the scan "
                    "(I/O race, not a data problem), skipping: %s",
                    appid, chosen.name, exc,
                )
                vanished_during_scan.append(chosen.name)
            else:
                logger.warning(
                    "manifest-ingest appid=%s file=%s: parse failed, skipping: %s",
                    appid, chosen.name, exc,
                )
                parse_failures.append(chosen.name)
            continue

        recorded_at = utcnow_iso()
        manifestid = str(manifest.manifest_id)
        chunk_count = len(manifest.chunks)
        total_bytes = sum(manifest.chunks.values())
        containing_app_id = chosen.containing_appid

        depot_manifests.upsert_depot_manifest(
            conn,
            appid=appid,
            containing_appid=containing_app_id,
            depotid=manifest.depot_id,
            manifestid=manifestid,
            chunk_count=chunk_count,
            total_bytes=total_bytes,
            recorded_at=recorded_at,
            source=SOURCE_STEAMPREFILL_BIN,
        )

        # Additive shared-depot attribution (WP 3.2 item 4): the job's OWN
        # mapping (appid -> observed depots) is the before/after disk diff in
        # vault_api.prefill.apply_observed_mapping and is not touched here.
        # This is a SEPARATE, additive fact about a depot's "home" app.
        if containing_app_id != appid:
            upsert_mapping(conn, depotid=manifest.depot_id, appid=containing_app_id, name=None)

        # WP MAP-FROM-MANIFEST-1: the job's own app owns the depot too (see
        # the docstring, step 5b). Only when the manifest's original app IS
        # this job's app and the depot has bytes on disk.
        if original_appid == appid and manifest.depot_id not in skip_own:
            if not depot_root_resolved:
                depot_root_resolved = True
                depot_root = _resolve_depot_root_or_none(settings)
            if (
                depot_root is not None
                and _depot_has_bytes(depot_root, manifest.depot_id)
                and conn.execute(
                    "SELECT 1 FROM depot_app_map WHERE depotid = ? AND appid = ?",
                    (manifest.depot_id, appid),
                ).fetchone() is None
            ):
                upsert_mapping(conn, depotid=manifest.depot_id, appid=appid, name=None)
                mapped_own.append(manifest.depot_id)

        archived_path = _archive_quietly(
            settings, appid=appid, depotid=manifest.depot_id,
            manifestid=manifestid, src_path=chosen.path,
        )

        ingested.append(
            IngestedManifest(
                depotid=manifest.depot_id,
                manifestid=manifestid,
                containing_appid=containing_app_id,
                chunk_count=chunk_count,
                total_bytes=total_bytes,
                archived_path=archived_path,
            )
        )

    return IngestResult(
        ingested=ingested,
        parse_failures=parse_failures,
        vanished_during_scan=vanished_during_scan,
        superseded=superseded,
        mapped_own=mapped_own,
    )


def _resolve_depot_root_or_none(settings: Settings) -> str | None:
    """The cache's depot root, or ``None`` when it cannot be judged (then
    nothing is mapped from manifests: no evidence, no row)."""
    from vault_api import deletion  # local: deletion imports jobs, keep import order simple

    try:
        return deletion.resolve_depot_root(settings.cache_root)
    except Exception as exc:  # noqa: BLE001 - unjudgeable disk maps nothing
        logger.info("manifest-ingest: depot cache unavailable, own-app mapping skipped: %s", exc)
        return None


def _depot_has_bytes(depot_root: str, depotid: int) -> bool:
    from vault_api import deletion

    try:
        return _has_bytes_on_disk(deletion.depot_dir_path(depot_root, depotid))
    except deletion.DeletionGuardError:
        return False


def _archive_quietly(
    settings: Settings, *, appid: int, depotid: int, manifestid: str, src_path: str
) -> str | None:
    """Archive one ``.bin`` and prune the depot's archive. ``None`` on failure.

    The DB row is already recorded even if the archive copy fails -- "we know
    what the manifest was" and "we kept a durable copy of the bytes" are two
    different facts, and losing the second must not undo the first.
    """
    try:
        archived_path = manifest_archive.archive_manifest(
            settings.manifest_archive_dir,
            depotid=depotid,
            manifestid=manifestid,
            src_path=src_path,
        )
        manifest_archive.prune_archive(
            settings.manifest_archive_dir,
            depotid=depotid,
            keep=settings.manifest_keep,
            current=os.path.basename(archived_path),
        )
    except OSError as exc:
        logger.warning(
            "manifest-ingest appid=%s depot=%s manifest=%s: archiving failed "
            "(the depot_manifests row was still recorded): %s",
            appid, depotid, manifestid, exc,
        )
        return None
    return archived_path


# --------------------------------------------------------------------------
# Which .bin file is "the" manifest of a depot (WP UPD-FIX-1)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class BinCandidate:
    """One well-named ``.bin`` file in SteamPrefill's temp cache."""

    path: str
    name: str
    original_appid: int
    containing_appid: int
    depotid: int
    manifestid: int
    #: The file's own mtime. SteamPrefill writes a manifest file exactly once,
    #: when it first downloads that manifest, and only ever reads it after
    #: that (``ManifestHandler``: ``File.Exists`` -> ``LoadFromFile``, else
    #: download -> ``SaveToFile``; v3.7.2 source) -- so the mtime is the
    #: moment SteamPrefill first used this manifest.
    mtime_ns: int


def select_current_manifest(
    candidates: Iterable[BinCandidate], *, not_after_ns: int | None = None
) -> BinCandidate | None:
    """The manifest SteamPrefill used last for one depot, or ``None``.

    **The rule (WP UPD-FIX-1):** among the depot's ``.bin`` files (optionally
    only those written at or before ``not_after_ns``), the one with the newest
    file mtime; an mtime tie (coarse filesystem ticks, ``docs/LEARNINGS.md``)
    goes to the larger manifest id, so the answer never depends on directory
    order.

    Why this and not something else:

    - SteamPrefill's temp cache keeps every manifest it ever downloaded
      (nothing prunes it), so a depot routinely has several files. Before this
      rule, ingestion upserted every one of them in unsorted ``os.scandir``
      order and the LAST file won -- on prod an old manifest overwrote the
      current one and "Update available" never cleared.
    - SteamPrefill's own record, ``Config/successfullyDownloadedDepots.json``,
      cannot answer the question: it is ``{depotid: [manifestid, ...]}``, a
      ``HashSet`` of EVERY manifest it ever finished (``DepotHandler.
      MarkDownloadAsSuccessful`` only ever adds; v3.7.2 source), with no
      timestamp, and it lives in the runner's ``Config/`` next to the Steam
      session, which vault-api deliberately does not mount (ADR-0012). So the
      file mtime is the record that is both reachable and ordered.
    - Each new manifest is written by the run that first downloads it, so the
      newest file is the build the latest successful run fetched. A run that
      found the app "Up To Date" writes nothing, and its current manifest is
      the newest one it already has.

    Known limit, stated rather than hidden: if Steam ever rolls a depot BACK
    to a manifest SteamPrefill downloaded before, no new file is written and
    the rule keeps the newer, no-longer-current one. The oracle comparison then
    flags the game and the post-run re-check says so in the job log
    (``worker``), instead of silently recording something else.
    """
    eligible = [
        candidate
        for candidate in candidates
        if not_after_ns is None or candidate.mtime_ns <= not_after_ns
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda candidate: (candidate.mtime_ns, candidate.manifestid))


@dataclass(frozen=True)
class _BinScan:
    #: ``{(original_appid, depotid): [candidates]}``.
    groups: dict[tuple[int, int], list[BinCandidate]]
    parse_failures: list[str]
    vanished: list[str]


def _scan_bin_files(cache_dir: str, *, prefix: str = "") -> _BinScan | None:
    """List the well-named ``.bin`` files whose name starts with ``prefix``,
    grouped per ``(original_appid, depotid)``. ``None`` if the directory is
    missing or unreadable."""
    try:
        entries = list(os.scandir(cache_dir))
    except OSError:
        return None

    groups: dict[tuple[int, int], list[BinCandidate]] = {}
    parse_failures: list[str] = []
    vanished: list[str] = []
    for entry in entries:
        name = entry.name
        if not (name.endswith(".bin") and name.startswith(prefix)):
            continue
        try:
            if not entry.is_file():
                continue
            original_appid, containing_appid, depotid, manifestid = parse_bin_filename(
                entry.path
            )
            mtime_ns = os.stat(entry.path).st_mtime_ns
        except ManifestParseError:
            parse_failures.append(name)
            continue
        except FileNotFoundError:
            logger.info(
                "manifest-ingest file=%s: vanished during the scan (I/O race, "
                "not a data problem), skipping.",
                name,
            )
            vanished.append(name)
            continue
        except OSError as exc:
            logger.warning("manifest-ingest: cannot stat %s, skipping: %s", entry.path, exc)
            parse_failures.append(name)
            continue
        groups.setdefault((original_appid, depotid), []).append(
            BinCandidate(
                path=entry.path,
                name=name,
                original_appid=original_appid,
                containing_appid=containing_appid,
                depotid=depotid,
                manifestid=manifestid,
                mtime_ns=mtime_ns,
            )
        )
    return _BinScan(groups=groups, parse_failures=parse_failures, vanished=vanished)


# --------------------------------------------------------------------------
# One-time repair of rows recorded before the rule (WP UPD-FIX-1, schema v28)
# --------------------------------------------------------------------------

#: ``maintenance_steps.name`` of the repair below.
REPAIR_STEP_NAME = "upd-fix-1-depot-manifest-repair"


@dataclass(frozen=True)
class RepairResult:
    #: The step had already run on this database; nothing was looked at.
    already_done: bool = False
    #: The temp-cache directory could not be listed; the step is NOT stamped
    #: and runs again on the next start.
    cache_dir_unavailable: bool = False
    #: ``(appid, depotid, old manifest id, new manifest id)`` per fixed row.
    repaired: tuple[tuple[int, int, str, str], ...] = ()


def repair_recorded_manifests(conn: sqlite3.Connection, settings: Settings) -> RepairResult:
    """Re-derive every ``steamprefill_bin`` row of ``depot_manifests`` once,
    with ``select_current_manifest``.

    Rows written before WP UPD-FIX-1 may hold a superseded manifest (the
    last-file-wins bug). For each such row this takes the depot's files for
    the row's app and picks with the same rule, restricted to files that
    already existed when the row was recorded (``not_after_ns`` = the row's
    ``recorded_at``, whose second precision is widened to the end of that
    second). That restriction is what makes the repair safe: a file written
    later -- by a run that failed, so its manifest was never confirmed -- is
    never promoted. A row is changed only when the chosen file differs from
    it and its payload parses; ``recorded_at`` and the change-frequency
    columns stay as they are (a correction, not a new observation).

    Runs once per database: stamped in ``maintenance_steps`` (schema v28) when
    the temp-cache directory could be listed, so a host whose directory is
    not mounted yet retries on the next start. Idempotent on its own as well:
    a second pass finds every row already equal to its choice. Never raises
    for missing or bad files; the caller still wraps it (startup must not
    fail over a repair).
    """
    if conn.execute(
        "SELECT 1 FROM maintenance_steps WHERE name = ?", (REPAIR_STEP_NAME,)
    ).fetchone() is not None:
        return RepairResult(already_done=True)

    scan = _scan_bin_files(settings.steamprefill_cache_dir)
    if scan is None:
        return RepairResult(cache_dir_unavailable=True)

    rows = conn.execute(
        "SELECT appid, depotid, manifestid, recorded_at FROM depot_manifests "
        "WHERE source = ? ORDER BY appid, depotid",
        (SOURCE_STEAMPREFILL_BIN,),
    ).fetchall()

    planned: list[tuple[int, int, str, BinCandidate, int, int]] = []
    for row in rows:
        appid = int(row["appid"])
        depotid = int(row["depotid"])
        old = str(row["manifestid"])
        group = scan.groups.get((appid, depotid))
        recorded = parse_utc_iso(str(row["recorded_at"] or ""))
        if not group or recorded is None:
            continue
        not_after_ns = (int(recorded.timestamp()) + 1) * 1_000_000_000 - 1
        chosen = select_current_manifest(group, not_after_ns=not_after_ns)
        if chosen is None or str(chosen.manifestid) == old:
            continue
        try:
            manifest = parse_steamprefill_bin(chosen.path)
        except ManifestParseError as exc:
            logger.warning(
                "manifest repair: app %s depot %s: %s does not parse, row left "
                "as-is: %s", appid, depotid, chosen.name, exc,
            )
            continue
        planned.append(
            (appid, depotid, old, chosen, len(manifest.chunks), sum(manifest.chunks.values()))
        )

    repaired: list[tuple[int, int, str, str]] = []
    with immediate_transaction(conn):
        for appid, depotid, old, chosen, chunk_count, total_bytes in planned:
            new = str(chosen.manifestid)
            cursor = conn.execute(
                """
                UPDATE depot_manifests
                SET manifestid = ?, containing_appid = ?, chunk_count = ?, total_bytes = ?
                WHERE appid = ? AND depotid = ? AND manifestid = ?
                """,
                (new, chosen.containing_appid, chunk_count, total_bytes, appid, depotid, old),
            )
            if cursor.rowcount:
                repaired.append((appid, depotid, old, new))
        conn.execute(
            "INSERT OR IGNORE INTO maintenance_steps (name, done_at) VALUES (?, ?)",
            (REPAIR_STEP_NAME, utcnow_iso()),
        )

    by_key = {(appid, depotid): chosen for appid, depotid, _old, chosen, _c, _t in planned}
    for appid, depotid, old, new in repaired:
        _archive_quietly(
            settings, appid=appid, depotid=depotid, manifestid=new,
            src_path=by_key[(appid, depotid)].path,
        )
        logger.warning(
            "manifest repair: app %s depot %s recorded manifest %s corrected to %s "
            "(the newest SteamPrefill manifest file at the time it was recorded; "
            "WP UPD-FIX-1)",
            appid, depotid, old, new,
        )
    return RepairResult(repaired=tuple(repaired))


@dataclass(frozen=True)
class OrphanCleanupResult:
    """Counters of ``remove_orphan_depot_manifests`` (logged once per start)."""

    #: ``depot_manifests`` rows whose depot no app maps (candidates).
    found: int = 0
    #: Candidates deleted (no mapping AND no bytes on disk).
    removed: int = 0
    #: Candidates kept: bytes on disk, or the depot id was unusable.
    skipped: int = 0
    #: The depot cache root could not be resolved; nothing was touched.
    cache_unavailable: bool = False


def _has_bytes_on_disk(path: str) -> bool:
    """True when a regular file exists anywhere below ``path``.

    Stops at the first file, so a populated depot costs one ``scandir`` chain,
    not a walk. Links are never followed. Any ``OSError`` counts as "there may
    be bytes": an unreadable directory must never make a row deletable.
    """
    stack = [path]
    while stack:
        try:
            with os.scandir(stack.pop()) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                    else:
                        return True
        except FileNotFoundError:
            continue  # absent (or vanished mid-scan): no bytes there
        except OSError:
            return True
    return False


def remove_orphan_depot_manifests(
    conn: sqlite3.Connection, settings: Settings
) -> OrphanCleanupResult:
    """WP MAP-DRIFT-1: delete ``depot_manifests`` rows nothing needs any more.

    A row is an orphan only when BOTH hold: no ``depot_app_map`` row names its
    depot (for any app), and the depot has no bytes on disk. A partial update
    replaces an app's mapping with the depots that got new bytes
    (``apply_observed_mapping``), so an untouched depot drops out of the map
    while its cached bytes and its recorded manifest still matter -- the
    bytes-on-disk test keeps exactly those rows. Whenever the disk cannot be
    judged (cache root unresolved, directory unreadable) the row is kept.

    Runs on every start, no stamp: a second pass finds nothing. One SELECT for
    the candidates, one filesystem probe per distinct depot, then a single
    bounded ``BEGIN IMMEDIATE`` transaction whose DELETEs repeat the
    no-mapping test, so a mapping written in between wins.
    """
    from vault_api import deletion  # local: deletion imports jobs, keep import order simple

    candidates = [
        (int(r["appid"]), r["depotid"])
        for r in conn.execute(
            "SELECT dm.appid, dm.depotid FROM depot_manifests dm "
            "WHERE NOT EXISTS (SELECT 1 FROM depot_app_map m WHERE m.depotid = dm.depotid)"
        )
    ]
    if not candidates:
        return OrphanCleanupResult()
    try:
        depot_root = deletion.resolve_depot_root(settings.cache_root)
    except Exception as exc:  # noqa: BLE001 - unjudgeable disk keeps every row
        logger.info("orphan depot cleanup skipped, depot cache unavailable: %s", exc)
        return OrphanCleanupResult(found=len(candidates), skipped=len(candidates), cache_unavailable=True)

    verdict: dict[object, bool] = {}
    doomed: list[tuple[int, int]] = []
    for appid, raw_depot in candidates:
        if raw_depot not in verdict:
            try:
                keep = _has_bytes_on_disk(deletion.depot_dir_path(depot_root, raw_depot))
            except deletion.DeletionGuardError:
                keep = True  # unusable id: leave the row for an operator
            verdict[raw_depot] = keep
        if not verdict[raw_depot]:
            doomed.append((appid, int(raw_depot)))

    removed = 0
    with immediate_transaction(conn):
        for appid, depotid in doomed:
            removed += conn.execute(
                "DELETE FROM depot_manifests WHERE appid = ? AND depotid = ? "
                "AND NOT EXISTS (SELECT 1 FROM depot_app_map WHERE depotid = ?)",
                (appid, depotid, depotid),
            ).rowcount
    return OrphanCleanupResult(
        found=len(candidates), removed=removed, skipped=len(candidates) - removed
    )


# --------------------------------------------------------------------------
# Startup coupling canary (research doc risk 6)
# --------------------------------------------------------------------------

#: SteamPrefill's own filename contract
#: (``{originalAppId}_{containingAppId}_{depotId}_{manifestId}.bin``,
#: ``SteamPrefill/Models/DepotInfo.cs``) as a plain regex — used only for the
#: startup canary below, never for actual parsing (``manifests.parse_bin_filename``
#: is the strict, exception-raising parser used on the ingestion path).
_BIN_FILENAME_PATTERN = re.compile(r"^\d+_\d+_\d+_\d+\.bin$")


def find_canary_mismatches(cache_dir: str) -> list[str]:
    """Every ``.bin`` filename in ``cache_dir`` that does NOT match
    SteamPrefill's ``{originalAppId}_{containingAppId}_{depotId}_{manifestId}.bin``
    contract.

    **Restricted to ``.bin`` files (WP 3.2 review fix).** SteamPrefill's real
    temp-cache directory also holds plain sidecar files that are not
    manifests at all — observed on a live host: ``cellId.txt`` (its cached
    Steam cell/region id) and ``lastUpdateCheck.txt`` (a timestamp). Those are
    expected, unrelated content, not a canary signal, and the first version of
    this function flagged them on every single boot of a real deployment —
    exactly the "operator learns to ignore the warning" failure mode a canary
    must not have. Non-``.bin`` files are now ignored **entirely**, whatever
    their name; only a ``.bin`` file that fails the filename contract counts
    as a real mismatch.

    Research doc risk 6: reading SteamPrefill's own temp-cache directory
    couples vault-api to its internal layout, pinned to SteamPrefill 3.7.2
    (layout unchanged from 3.7.1, WP DEPS-1).
    A future SteamPrefill release renaming or restructuring its manifest
    files would not break anything loudly — ``ingest_after_prefill`` just
    wouldn't match any files, warn-and-skip is the failure mode for a bad
    *individual* file, not "the whole directory looks wrong". This function
    is the only signal an operator gets that the coupling itself may have
    broken.

    Returns an empty list if the directory doesn't exist, is empty, holds
    only non-``.bin`` files, or every ``.bin`` file matches — all of those are
    the unremarkable, expected case.
    """
    try:
        entries = list(os.scandir(cache_dir))
    except OSError:
        return []
    return [
        entry.name
        for entry in entries
        if entry.is_file()
        and entry.name.endswith(".bin")
        and not _BIN_FILENAME_PATTERN.match(entry.name)
    ]


def log_cache_dir_canary(cache_dir: str) -> None:
    """Log a WARNING (never raise) if ``cache_dir`` holds unrecognized ``.bin``
    files.

    Called once at startup (``vault_api/main.py``'s lifespan) — this is a
    coupling canary, not a validation gate (research doc risk 6): it never
    stops vault-api from starting and never fails a job. Bounded to the first
    ``MAX_LOGGED_NAMES`` filenames plus a count (WP 3.2 review carry-over
    pattern, same as ``manifests.py``'s zip-namelist truncation).

    Only ``.bin`` files are considered (see ``find_canary_mismatches``) — a
    real host's cache dir also holds non-manifest sidecars (``cellId.txt``,
    ``lastUpdateCheck.txt``) that must never trigger this warning; the first
    version of this function warned on every boot of a real deployment
    because of exactly those two files, which trains an operator to ignore
    the warning — the one outcome a canary must not have.
    """
    mismatches = find_canary_mismatches(cache_dir)
    if not mismatches:
        return

    shown = mismatches[:MAX_LOGGED_NAMES]
    omitted = len(mismatches) - len(shown)
    suffix = "" if omitted <= 0 else f" (+{omitted} more)"
    logger.warning(
        "manifest-ingest: %s contains %d .bin file(s) that do not match "
        "SteamPrefill's '{originalAppId}_{containingAppId}_{depotId}_"
        "{manifestId}.bin' filename contract: %s%s. This is a coupling canary "
        "(docs/research/phase3-manifests.md risk 6), not a failure — "
        "vault-api's manifest ingestion is version-pinned to SteamPrefill "
        "3.7.2's cache layout, and this may mean a different version changed it.",
        cache_dir, len(mismatches), shown, suffix,
    )
