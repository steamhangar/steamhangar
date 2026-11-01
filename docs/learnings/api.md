# Learnings: vault-api (Python)

Scope: FastAPI, SQLite and schema steps, parsers and input handling, subprocess output, scheduler, jobs, oracle, mapping. Part of the learnings index
[`docs/LEARNINGS.md`](../LEARNINGS.md); entries moved verbatim by
DOC-DIET-1 (rc22), original section headings kept. Append new findings
at the end, under a dated heading, one entry each with the WP that proved it.

## SQLite / FastAPI / vault-api
- Connections must be created, used, and closed in ONE thread. A generator
  dependency holding a connection segfaults (native access violation) when
  request cancellation unwinds AsyncExitStack on another thread —
  `check_same_thread=False` converts the loud error into a use-after-free
  (WP 1.4 fixing WP 1.3's review advice).
- Every check-then-act needs `BEGIN IMMEDIATE` (job claim/dedupe, report
  chains) (WP 1.4/2.4).
- Order report/diff chains by rowid, not second-precision timestamps
  (WP 2.4). WAL + busy_timeout on every connection (WP 1.2).
- FastAPI ships /docs, /redoc, /openapi.json world-readable — disable via
  `openapi_url=None`; auth belongs on the APIRouter, not per route; compare
  API keys with `hmac.compare_digest` on BYTES (non-ASCII str raises)
  (WP 1.2/1.3).

## Parsers / input handling
- Recursive-descent parsers need an explicit depth limit raising the
  module's own error type — RecursionError escapes the documented catch
  contract and crashes the caller (WP 2.1 blocker).
- Python `int()` accepts " 4 ", "+4", "1_0" (=10) and non-ASCII digits —
  any value that later feeds Go, SQL, or a filesystem path needs
  strip + isascii + isdigit validation (WP 1.6/2.1).
- Pydantic lax mode coerces `true`→1 on int fields — reject bools
  explicitly on any id field (WP 2.4).
- `re`'s `\d` matches Unicode digits unless `re.ASCII` is passed — strict
  numeric env/config grammars are safer as `isascii()+isdigit()` on the
  partitioned string; and a long digit string is a VALID decimal literal
  that `float()` rounds to `inf`, so check `math.isfinite` after
  conversion (WP 3.12).
- One-shot request flags (stop/cancel columns) must be cleared at EVERY
  terminal or parking transition (finish, park, resume) — a stale flag
  re-fires on the next run of the same row (WP 3.12).
- Cursor-based tailers: "no newline in the batch" conflates a partial
  tail (wait) with an oversized line (skip past the next newline, loudly)
  — a full-sized batch can never become a valid line, and treating it as
  a tail stalls the sweep silently forever. Never advance a cursor past
  unterminated bytes (half-written lines would parse as fresh records),
  and never let a progress log line fire when nothing was consumed
  (WP 3.11).
- `urllib.request` does not handle userinfo in URLs (`https://u:p@host/`
  fails getaddrinfo) — strip it and send a real Basic Authorization
  header; redact userinfo in every log line, and test the redaction
  against `@` inside passwords and IPv6 hosts (WP 3.13).
- Transition detectors: persist state changes in BOTH directions
  regardless of which notifications are enabled — filtering belongs at
  delivery, or enabling an event later fires falsely on first sight
  (WP 3.13).
- envsubst on config templates: a colliding env var REPLACES runtime
  variables with its value (not blanks) and `nginx -t` still passes —
  always restrict with an allowlist filter (WP 1.9).
- A consumer that renames a producer's files breaks filename-contract
  parsers: manifest_archive stores `{depotid}_{manifestid}.bin` while the
  parser demanded SteamPrefill's 4-field name — keep payload parsing and
  filename-contract validation separable, with the caller supplying the
  expected ids so the corruption cross-check survives (WP 3.7).
- `ntpath.join` has two surprising drive cases: `join(parent, "C:x")`
  silently drops the drive (yields `parent\x`) and `join(parent, "a:b")`
  discards the parent entirely — strict-child path guards must compare
  `dirname`/`basename` equality on the joined result, never prefixes
  (WP 3.8, measured).
- CPython's `json` scanner recurses per nesting level — a bounded response
  body is NOT a bounded parse; catch `RecursionError` by name around
  `json.loads` on untrusted input and convert it to the module's own error
  type (WP 3.9, same failure class as the WP 2.1 blocker).
- `urllib` follows redirects by default — an operator-configured outbound
  URL needs an explicit no-redirect opener, or the host actually contacted
  is not the one configured (WP 3.9).

## Subprocess output handling
- SteamPrefill writes its summary table in the OS OEM codepage (cp850
  here), not UTF-8 — decode strict-UTF-8 first, OS-queried OEM second,
  lossy LAST (a decode path that can raise turns a successful download
  into a crashed job: terminate() leaves truncated multibyte tails as
  the NORMAL case) (WP 3.3 blocker).
- "Contains a digit" is a terrible row-detector: SGR remnants
  (\x1b[38;5;226m) and timestamps are digits too — require
  digits-and-separators-only (WP 3.3).

## 2026-08-22 — the defaults-flip wave (SWEEP-1, APP-DEMO, 4d-web, AG-0, CI-3/AGENT-BIN)
- A startup line that exists to make unattended behaviour visible must
  (a) print the REQUESTED value next to the RESOLVED one — glibc
  answers a typo'd IANA zone with a plausible POSIX fallback
  ("Europe/Berlinn" -> "Europe (UTC+00:00)") and no error — and
  (b) read the same effective-settings resolution the behaviour itself
  uses, not the boot snapshot: with a db-stored window the boot-config
  line announced "scheduler DISABLED" while unattended deletion was
  scheduled (WP SWEEP-1 R2-B2, both directions measured in the image).

## 2026-08-22, second wave — the AG series (AG-0..AG-3)
- The shared-function/unshared-BOUND divergence: AG-1 unified the
  freshness predicate into one function and still diverged, because one
  caller fed it the boot settings snapshot while the other resolved
  effective settings (db>env>default) per tick — one PATCH made the API
  assert exactly the report the scheduler had just refused. Sharing the
  predicate is half the job; both callers must share its INPUTS
  (settings resolution and clock). Third instance of the
  boot-snapshot-vs-effective class in one week (SWEEP-1 startup line,
  AG-1, and the games clock seam).

## 2026-09-30/10-01 — pre-freeze review (the *-FIX-1/2 packages, TH-0b)
- A lease timeout must be measured from the moment the counterparty could
  first act (the hand-off), not from an earlier lifecycle stamp:
  `started_at` predates vault-api's own pre-hand-off cache walk, so every
  job on a cold cache failed as `runner_lost` before a runner saw it. Same
  package: a reattach lookup filtered on "not yet complete" excludes
  exactly the row a restart most commonly leaves behind (WP API-FIX-1).
- FastAPI parses the request body before router-level dependencies run,
  so an auth check that must precede body handling belongs in an ASGI
  layer, not a `Depends` (WP SEC-FIX-4).
- `O_NOFOLLOW` guards only the last path component. A symlinked parent
  directory on the same device passes an `st_dev` check, so `lstat` the
  parent too (WP SEC-FIX-4).
- Parsing a third-party tool's output for a hint: match the narrowest
  phrase that names the cause, not the exception type. SteamPrefill's
  `LancacheNotFoundException` covers both "resolving to a public IP" and
  "Unable to detect Lancache server!", and only the first one justifies
  a DNS hint (WP WEB-FIX-4).

## 2026-10-02/03 — upstream keepalive pool (ADR-0017, WP CORE-FEAT-1a..1d)
- See also [steam.md](steam.md), this section: "SteamPrefill exits 0 when an app fails; its summary table only grows ..."

## 2026-10-13/14 — rc15/rc16 (D9, D10, UPD-5, UPD-6, D15a-c)
- On ZFS `st_dev` is not stable across reboots; identify cached files by
  inode (plus path) only, never by `(st_dev, st_ino)`.

## 2026-10-14 afternoon — rc17 integration (D15c/d, NAMES, RUNNER, AGENT-ADDR-1, CACHE-COMPLETE-1a-c)
- Local uv Python bundles a newer SQLite (3.53) than CI's setup-python,
  which links the runner's system SQLite (3.45). `ALTER TABLE ... DROP
  COLUMN` on a table whose DDL has a `--` comment before the dropped
  column fails on the older one ("incomplete input"). Never DROP COLUMN in
  test fixtures; hand-write the old table instead.
- Parallel packages that add schema steps reserve version numbers up
  front and make every step idempotent without trusting the stamp
  (CREATE TABLE IF NOT EXISTS, per-column guards); version pins in tests
  use `>=`.

## 2026-10-14/15 — rc18 night run (UPD-FIX-1, D16a-c, DIST-1a/b)
- Changing what a blank value means also changes it for shipped clients
  that send blank on purpose (rc17 app, older web). Keep the old wire
  meaning in the API (blank PATCH = `off`, null = reset) even when env
  semantics change (D16b-1 B2).
- A setting the API accepts but nothing applies is worse than a read-only
  one: phantom values also feed cross-rules (threads vs connection cap)
  and break real jobs. Lock keys whose apply path is disabled (D16b-1 B3).
- Values from a server index that land in generated shell or PowerShell
  commands need the same grammar check the server uses, even in comment
  lines: a newline ends the comment (DIST-1b B1).
- A filter added "to be safe" can hide real signals when its input is
  lossy: depot_app_map is rewritten after partial updates, so filtering
  the stale verdict by it hid real updates (UPD-FIX-1 B1, MAP-DRIFT-1).
- Publishing a thread before `start()` lets a concurrent `join()` raise
  "cannot join thread before it is started"; start first, publish under a
  lock (UPD-FIX-1 S1, the long-standing test_upd flake).
- CI runs SQLite ~3.45, local runs 3.53. Tests and migrations must not use
  `ALTER TABLE ... DROP COLUMN` (or other newer syntax) even if the local
  run is green; build old-schema fixtures with CREATE TABLE instead (D20
  fixture, CI-only failure).
- Tailing a log for a live view (GRAPH-LIVE): an idle gap leaves a hole in
  the ring, so restart from the file's end window, drop stale cells and move
  the "covered from" mark to the next minute so the persisted tier fills the
  gap; a shrunk file or a changed inode restarts at offset 0; a read that
  starts mid-file must align to the next newline or it folds a torn line into
  the ring; cap bytes per read and reads per refresh and skip an oversized
  backlog to its tail.

## 2026-10-16 - rc19.3 throughput tuning (TUNE-1a/1b)
- LIVE-DELAY-1 (root cause): "client downloads show late" on Downloads is
  the event sweep cadence, not a bug. Every non-live range (default 6h)
  reads tiers that the sweep fills (5 min interval, 60 s scheduler tick,
  nginx flush 5 s, web poll 30 s, `traffic.py` module doc, `current_window`),
  so figures and `active_clients_now` lag up to ~6 min. Only the `live`
  range (`live_tier.py`, 3 s poll, 2 s tail) is ~10 s, and even there
  `active_clients_now` still comes from the persisted summary. Check which
  tier feeds a figure before hunting for a poll or cache bug: no
  Cache-Control or proxy buffering is involved on the JSON endpoints.
  Fix (Weg A, Jan 2026-10-16): Downloads defaults to `live`;
  `GET /v1/throughput/live` carries `active_clients` (distinct non-runner
  addresses in the ring over 5 min) and `mergeLive` uses it. A test that
  mounts the Downloads view must set `globalThis.localStorage` itself:
  `readStoredStatsRange` reads that, not `window.localStorage`, so a fake
  window alone silently gets the default range.

## 2026-10-31 — rc21 (GRAPH-RECENT-1, LOADING-NOW-1)
- **Never trust a "coverage" claim when merging two tiers of the same
  counter.** A log tailer that started on a just-rotated file assumed it
  held the whole 15-minute ring and showed already-swept minutes as 0.
  Per bucket, take the larger of the two sources (each is a subset of the
  truth) and never add them (`live_tier.prefer_ring`).
- **A watcher/keep-alive window must be clearly longer than every
  poller's interval.** 30 s against the web's 30 s poll restarted the
  tailer on every poll; 60 s keeps it incremental.
- **Test trap for 1h/6h ranges:** they fall back to the 5-minute tier
  until `traffic_1m_since` is at or before the range start; tests with a
  single fresh sweep must backdate it.
- **Runner tests need a HIT line.** A runner split tested with MISS only
  never exercises the hit-side subtraction; the operator's verify case is
  all HIT.
- **A rate test on the real clock must treat the running bucket
  separately** once its divisor depends on "now" (RUNNER-SPLIT-1, N1):
  otherwise the test passes or fails by time of day.
- **Read every "now" figure of one answer from a single ring snapshot**
  (`LiveTier.now_view`), so a tail between two reads cannot make rows,
  speed and device count disagree.

## 2026-11-01 — rc21.1 (prod findings after the rc21 rollout)
- **A before/after disk diff on a shared cache is not attribution**: any
  mapping from "what changed during my run" needs positive per-app evidence
  (own manifest, recorded manifest, oracle depot list), or it picks up other
  clients' traffic (MAP-ATTRIB-1, Wanderburg owning HD2 depots).
- **A seeded store row in a refresher test is deleted on the first tick if
  the fake store answers success != 1**; the fake store must answer it.
- **Stamps written by `oracle.refresh_app` use the real clock**, not an
  injected `now`; keep fake-clock windows on the safe side of the real date.
- **Making a refused job type pausable touches every text that assumed
  the old type** (resume, cancel-while-paused, DELETE 409), not only the
  guard.
- **Simulate a data-repair step on a full read-only prod export before
  release** (export via `dev.sh diag db`, page past its 200-row cap, run
  the real function on a scratch SQLite). Unit fixtures missed both the
  fifth removal (Enshrouded) and 22 wrong restores (Witcher 3 packages).
- **A cleanup that removes wrong rows can leave an app with no mapping**:
  it needs a restore counterpart, and the restore must respect the
  ownership model other packages built on purpose (package depots are
  recorded under the game but mapped only to the package) and only use a
  record equal to the oracle public gid (`gc_keepset_gids` leaves public
  out on purpose).
- **`upsert_mapping` commits internally**: never call it inside a
  `BEGIN IMMEDIATE` that must stay atomic.
- **Never call `SizeCache.get()` from a background check**: it starts a
  walk on an expired snapshot and blocks on an invalidated one. Use the
  non-blocking `SizeCache.peek()` and log the snapshot age.
- **A "the rest" bucket is 0 by construction when every item is in exactly
  one class**: it can only go negative; don't promise a positive residual.
- **Temp-cache `.bin` files are short-lived evidence** (clear-temp): any
  keep decision must also read the durable record (`depot_manifests` with
  containing app = A) and the oracle list. A replace-step removal plus
  `skip_own_mapping` cannot be undone in the same job, so the protection
  sits in the replace step itself (MAP-LOSS-1).

### 2026-11-01 NITS-API-1

- A tail that resumes at a saved byte offset must also notice an in-place
  truncate plus regrow past the cursor between two reads, not only a
  shrink: `live_tier` now keeps up to 512 bytes before the cursor
  (`MARK_BYTES`) and restarts at offset 0 with a hole when they changed.
  Found by the regrow-without-idle-gap test (6 of 8 lines folded before).
