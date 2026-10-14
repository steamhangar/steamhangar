# ADR-0008: Cache-event feed from vault-core via a dedicated structured log

Date: 2026-08-09
Status: Accepted (orchestrator decision under explicit user authorization to
decide the core→api feed question and document it; the user can overturn it —
this ADR names every option for that purpose)

## Context

Two remaining Phase-3 features need vault-api to know what requests actually
reached vault-core:

1. **Miss-triggered prefill completion** (hybrid decision, ADR-0001): a cache
   MISS on a depot of an unknown or partially-cached app should queue a
   prefill job so one player's download completes the cache for everyone.
2. **Per-client hit statistics + bypass detection** (plan §5/§6): which
   machines are being served, at what hit rate — and which machines report
   installed games via vault-agent but never appear at the cache at all
   (DNS bypass, the plan's own pain point).

Plan §1 positions this project against LanCache's "parse the access log to
attribute content" approach. That stance must be read precisely: what it
rejects is deriving *content ownership* (chunk→game attribution) from
URL-pattern parsing — fragile, and this project gets that from SteamPrefill
manifests instead (ADR-0006/0007). It does not forbid using nginx's own
logging for *coarse request facts* (who asked, was it a HIT, how many bytes),
which no other source can provide.

## Options considered

- **A — tail the standard access log.** Rejected: the combined-format log is
  built for humans, mixes non-cache traffic, needs brittle parsing, and
  rotating it fights every other consumer of that file.
- **B — per-request live feed (nginx `mirror`/`post_action` subrequest to a
  vault-api endpoint).** Rejected: a Steam download is tens of thousands of
  chunk requests; mirroring each one doubles request volume and turns the
  single-threaded API into a hot-path dependency of the serving path.
  vault-core must keep serving even when vault-api is down (deploy §
  independence), and a live feed breaks that.
- **C — dedicated structured event log + periodic sweep (CHOSEN).**
  vault-core writes a second, purpose-built `access_log` with a minimal,
  machine-readable line format (timestamp, client address, upstream cache
  status HIT/MISS/BYPASS, depot-bearing path prefix, bytes) — buffered, cheap,
  and entirely inside nginx. vault-api sweeps the file on the existing
  WP 3.5 scheduler cadence with a persisted byte-offset cursor, so each line
  is read once, a sweep failure re-reads instead of losing data, and rotation
  happens only after a successful sweep. The serving path never depends on
  the API being alive.

## Decision

Option C. Consequences and boundaries:

- The event log carries **request facts only**. Content attribution (which
  chunks belong to which app) stays with manifests (ADR-0006/0007) — the
  event log's depot id is used to *look up* the depot→app mapping vault-api
  already owns, never to build it.
- Miss-trigger rule: a MISS for a depot mapped to an app that is not
  currently cached-and-current MAY enqueue a non-forced prefill for that app,
  with strict dedupe (the existing per-app job dedupe) and a per-app cooldown
  so a busy download night cannot enqueue storms. Misses on unmapped depots
  are counted but trigger nothing (no mapping = no honest target; the
  readiness thinking of ADR-0007 applied to job creation).
- Client identity: event-log lines carry addresses; vault-agent reports
  already arrive FROM those addresses, so vault-api records the report source
  address and correlates. A client that reports installed games but has no
  cache-log presence within the report window is `bypass_suspected`.
- The feed is optional at runtime: vault-core without the extra log (or
  vault-api without read access to it) degrades to today's behavior —
  serving, prefill, GC all unaffected.

## Addendum 2026-10-13 (WP D15-ROT): vault-core rotates the log

User decision 2026-10-13 (`docs/handover/post-0.1.0-roadmap.md`, "User
decisions 2026-10-13 (late evening)"). The original decision gave rotation
to the sweeper alone ("sweep, then truncate"). That is best-effort: where
vault-api may not write the file, it never shrinks — measured on a
production instance at 41 MB and growing, every sweep past 64 MiB would
warn. Now:

- vault-core rotates the event log itself: at 64 MiB (`45-vault-eventlog-
  rotate.sh`, image-level `VAULT_EVENT_LOG_ROTATE_BYTES`, `0` = off) it
  renames `event.log` to `event.log.1`, creates a fresh 101:101 file and
  sends `USR1` to the nginx master. ONE old file is kept (about 128 MiB at
  most). Rename, never copytruncate: no line is lost, and the old file stays
  readable to the end.
- This supersedes "rotation happens only after a successful sweep" and
  "the sweeper owns rotation" in Option C above: vault-core is now the
  primary rotator, on its own schedule, independent of vault-api.
- The sweeper follows the rename (WP D15b): it stores the file's inode and
  treats a different inode at the path as a rotation (inode only, never
  `st_dev`, which ZFS does not keep stable across reboots). Before
  switching it reads the unread tail of `event.log.1` while that file still
  has the cursor's inode, then restarts at offset 0 of the new file. Lines
  are lost only if `event.log.1` is replaced again before the sweeper
  drains it (two rotations within one sweep interval).
- The sweeper's in-place truncation (`VAULT_EVENT_LOG_MAX_BYTES`, default
  64 MiB) stays as a fallback for deployments without the vault-core hook
  (`VAULT_EVENT_LOG_ROTATE_BYTES=0`, or an older core image). It never
  truncates while its cursor still refers to the renamed-away file. In the
  shipped containers the log belongs to uid 101 (vault-core's start hook),
  which vault-api runs as, so truncation is permitted and whichever of the
  two runs first wins; where the file is owned otherwise (as measured on
  production), a truncation attempt is refused and counted
  (`truncate_denied_count`) as before. With the core hook running the log
  rarely reaches the limit at sweep time, because the hook checks every
  60 s by default.
- "Optional at runtime" is unchanged: with `VAULT_EVENT_LOG` empty, the
  rotation hook does nothing.
