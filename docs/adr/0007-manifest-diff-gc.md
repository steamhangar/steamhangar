# ADR-0007: Garbage collection by manifest diff; time-based attribution rejected

Date: 2026-08-06
Status: Accepted (orchestrator decision from evidence; see docs/research/phase3-manifests.md)

## Decision

`POST /v1/cache/{appid}/gc` runs as a queued job (serialized with prefills
on the single worker), **dry-run by default**:

1. Chunk "keep" sets come from parsed manifests — SteamPrefill's `.bin`
   temp-cache files and/or cache-stored client manifests (ZIP → sectioned
   payload → protobuf chunk SHAs; ~60 lines of stdlib parsing, no
   dependencies). Both sources yield identical chunk sets (proven against
   ~12,000 on-disk files: zero orphans, byte-exact `cb_compressed` sizes).
2. Shared depots: keep = UNION of the current manifests of ALL apps mapped
   to the depot. Readiness gate: if any mapped app has no resolvable
   manifest, the depot is skipped and reported — never GC on partial
   knowledge.
3. Deletion goes through the WP 1.6 guard path (settle-and-recheck,
   link-safe removal). Duplicate stored manifests per (depot, manifest)
   are deduped to one copy.

## Rejected: time-based attribution ("delete chunks untouched during the job")

Two independent, measured reasons:
- nginx `proxy_store` stamps stored files with the upstream
  `Last-Modified` — on-disk mtimes are content publish times spread over
  months, not fetch times.
- A current, already-cached chunk is served as a HIT and never rewritten —
  a time-based GC would delete exactly the chunks it must keep. Recovering
  HITs would require access-log parsing, the LanCache anti-pattern this
  project exists to eliminate (plan §1).

## Addendum (2026-08-09, WP 3.7): uncached mapped apps in the keep set

Resolves the open note at the end of ADR-0003's addendum. In `plan_gc`:

- An app that is **verifiably idle, never prefilled, job-free AND has no
  `depot_manifests` row** is excluded from the union requirement — it
  neither pins chunks nor blocks the readiness gate. A blocking uncached
  co-owner would reproduce the WP 3.6 last-remnant leak (mapping rows
  survive deletion by design, so the block would be permanent).
- Fail-closed retained: a missing `apps` row, non-idle status, a set
  `last_prefill_at`, an active job, or an unreadable row all make the app
  COUNT. An uncached app WITH a recorded manifest row also counts — a
  claim vault-api wrote down but cannot resolve is exactly the partial
  knowledge the gate exists for.
- A depot where EVERY mapped app is excluded is `skipped_no_counting_apps`,
  never "everything is an orphan". That end state belongs to
  `DELETE /v1/cache/{appid}`'s last-remnant rule (execute-time recheck,
  `needs_force` bookkeeping); GC does not grow a second, weaker deletion
  path for the same state.

## Addendum (2026-08-09): beta-branch protection — user decision A+B

Opt-in Steam beta branches reach the cache only via store-on-miss (real
client downloads; SteamPrefill has no branch selection), and their chunks
are absent from every `public` manifest — plain manifest-diff GC would
collect them and force a re-download. User decision 2026-08-09: implement
both protections:

- **A — recently-stored grace window:** chunks stored within the last N
  days are never orphan candidates, implemented as a `ChunkExclusion`
  predicate on the existing hook in `gc_execute`. Recency MUST come from
  the inode change time (st_ctime, set by nginx's store-time rename), NOT
  mtime — proxy_store stamps mtime with the upstream `Last-Modified`
  (publish time, see Rejected section). Linux/container semantics;
  documented limitation on plain Windows dev runs.
- **B — oracle branch manifests (extends WP 3.9):** when the opt-in
  manifest oracle is enabled, manifest gids of open (non-passworded) beta
  branches join the depot's keep set. Passworded branches stay encrypted
  and uncoverable — the grace window remains their only protection.

Fully-EA titles need nothing: their EA build IS the `public` branch.

## Honest limits

GC can delete chunks a client pinned to an unrecorded/beta manifest still
wants (consequence: re-download, never corruption); unknown-manifest and
unmapped depots are skipped and reported; GC reclaims space but does not
certify completeness; no atomicity against concurrent client writes
(benign: re-fetch).

## Addendum 2026-10-13 — oracle data and the grouped delete (WP TOOL-APPS-2b, ADR-0022)

Decision B's oracle branch manifests still only ever ADD gids to a keep
set; GC is unchanged and never reads content-package classification. The
project-wide promise that oracle data can only add protection now has one
recorded exception outside GC: the user-confirmed grouped delete
(`DELETE /v1/cache/{appid}?with_packages=true&members=...`), where stored
store or oracle classification decides which apps are packages and so can
widen what is deleted. Its bounds are recorded in the ADR-0006 addendum of
the same date and in ADR-0022 §4.
