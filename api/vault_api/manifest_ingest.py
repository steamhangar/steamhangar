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

from vault_api import depot_manifests, manifest_archive
from vault_api.config import Settings
from vault_api.jobs import utcnow_iso
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
    #: True if the configured cache dir does not exist / isn't readable —
    #: the common, unremarkable case on a host where VAULT_STEAMPREFILL_PATH
    #: itself is unset or SteamPrefill has never run. Not an error.
    cache_dir_unavailable: bool = False

    def summary(self) -> str:
        if self.cache_dir_unavailable:
            return (
                "[vault-api] Manifest ingestion: SteamPrefill temp-cache directory "
                "not found or unreadable; nothing ingested (VAULT_STEAMPREFILL_CACHE_DIR)."
            )
        if not self.ingested and not self.parse_failures and not self.vanished_during_scan:
            return "[vault-api] Manifest ingestion: no matching .bin files found."

        parts = []
        if self.ingested:
            depots = sorted({entry.depotid for entry in self.ingested})
            parts.append(
                f"[vault-api] Manifest ingestion: recorded {len(self.ingested)} "
                f"manifest(s) for depot(s) {depots}."
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
    conn: sqlite3.Connection, *, appid: int, settings: Settings
) -> IngestResult:
    """Scan ``settings.steamprefill_cache_dir`` for ``{appid}_*.bin`` files
    belonging to this job's app, record each one's manifest state, and
    archive the source file.

    Per file:

    1. Parse the filename (``manifests.parse_bin_filename``) to recover
       ``containing_appid`` — the shared-depot attribution signal.
    2. Parse the payload (``manifests.parse_steamprefill_bin``), which
       independently re-validates the filename and cross-checks it against
       the payload's own depot/manifest ids (corruption signal).
    3. Upsert ``depot_manifests`` (latest-per-(appid, depotid), replacing any
       older row for this app+depot — ADR-0006 decision 3).
    4. **If ``containing_appid != appid``, additively map the depot to
       ``containing_appid`` too** (``mapping.upsert_mapping``) — this is
       purely additive (ADR-0003), a SEPARATE fact from the job's own
       replace-set mapping (``vault_api.prefill.apply_observed_mapping``,
       which is unaffected by this function and still comes from the
       before/after depot diff exactly as before WP 3.2).
    5. Archive the ``.bin`` file durably and prune old archives for that
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
    additive ``upsert_mapping(depotid, containing_appid)`` call in step 4
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
    try:
        entries = list(os.scandir(cache_dir))
    except OSError:
        logger.info(
            "manifest-ingest appid=%s: cache dir %r not found or unreadable; "
            "nothing to ingest.",
            appid, cache_dir,
        )
        return IngestResult(cache_dir_unavailable=True)

    prefix = f"{appid}_"
    candidates = [
        entry
        for entry in entries
        if entry.is_file() and entry.name.startswith(prefix) and entry.name.endswith(".bin")
    ]

    ingested: list[IngestedManifest] = []
    parse_failures: list[str] = []
    vanished_during_scan: list[str] = []

    for entry in candidates:
        try:
            _original_app_id, containing_app_id, _filename_depot_id, _filename_manifest_id = (
                parse_bin_filename(entry.path)
            )
            manifest = parse_steamprefill_bin(entry.path)
        except ManifestParseError as exc:
            # Distinguish an I/O race (WP 3.2 review nitpick) from a genuine
            # bad file: if the path is gone by now, it vanished BETWEEN the
            # os.scandir() listing above and this parse attempt (SteamPrefill's
            # own clear-temp running concurrently, or an operator clearing the
            # directory by hand) -- not a data-quality problem, and logging it
            # as one would train an operator to distrust a real corruption
            # warning. `lexists`, not `exists`: a broken/dangling link (not
            # expected here, but consistent with deletion.py's own rule)
            # still counts as "something is there".
            if not os.path.lexists(entry.path):
                logger.info(
                    "manifest-ingest appid=%s file=%s: vanished during the scan "
                    "(I/O race, not a data problem), skipping: %s",
                    appid, entry.name, exc,
                )
                vanished_during_scan.append(entry.name)
            else:
                logger.warning(
                    "manifest-ingest appid=%s file=%s: parse failed, skipping: %s",
                    appid, entry.name, exc,
                )
                parse_failures.append(entry.name)
            continue

        recorded_at = utcnow_iso()
        manifestid = str(manifest.manifest_id)
        chunk_count = len(manifest.chunks)
        total_bytes = sum(manifest.chunks.values())

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

        try:
            archived_path = manifest_archive.archive_manifest(
                settings.manifest_archive_dir,
                depotid=manifest.depot_id,
                manifestid=manifestid,
                src_path=entry.path,
            )
            manifest_archive.prune_archive(
                settings.manifest_archive_dir,
                depotid=manifest.depot_id,
                keep=settings.manifest_keep,
                current=os.path.basename(archived_path),
            )
        except OSError as exc:
            # The DB row is already recorded even if the archive copy fails —
            # "we know what the manifest was" and "we kept a durable copy of
            # the bytes" are two different facts, and losing the second must
            # not undo the first.
            logger.warning(
                "manifest-ingest appid=%s depot=%s manifest=%s: archiving failed "
                "(the depot_manifests row was still recorded): %s",
                appid, manifest.depot_id, manifestid, exc,
            )
            archived_path = None

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
