# ADR-0012: SteamPrefill execution moves to a separate runner process

Date: 2026-08-19
Status: Accepted (operator decision 2026-08-19, WP S-1 — the api-side half of
the runner split; S-2 wires the second container into `compose.yaml`; EG-1
locks vault-api's egress down afterwards)

## Context

EG-1 (the egress-lock work package) stopped honestly: SteamPrefill runs as a
`subprocess.Popen` CHILD of vault-api's own process
(`vault_api/worker.py::PrefillWorker._execute_prefill` →
`vault_api/prefill.py::run_prefill`). Docker network namespaces are
per-CONTAINER, not per-process, so as long as vault-api's own process is the
one spawning the child that needs broad Steam CM/CDN access, vault-api's
container can never be egress-locked — locking it down would also cut off
the one thing in the whole stack that legitimately needs the wider internet.

The operator's decision: **split first, then lock.** This ADR is the split's
api-side half (WP S-1): a new, slim `prefill_runner` process, running the
SAME codebase, executes SteamPrefill; vault-api's worker keeps owning
everything else. WP S-2 wires the second container into `compose.yaml`
(out of this package's footprint — `deploy/` is untouched here). EG-1 can
then lock vault-api's container to LAN-only egress without breaking prefill,
because vault-api no longer spawns the process that needs the wider internet
at all.

## Decision

### 1. What moves, what stays

vault-api's worker (`PrefillWorker`) keeps owning job lifecycle end to end:
claiming from `queued`, deciding `--force` from `apps.needs_force`, applying
the depot mapping (`apply_observed_mapping`), manifest ingestion, webhooks,
auto-GC, and every `apps.status`/`jobs.status` transition. The ONLY thing
that moves is the one call to `prefill.run_prefill` — replaced, in queue
mode, by a hand-off through the `jobs` table to a separate `prefill_runner`
process that calls the exact same (unmodified) `prefill.run_prefill`
function and reports a `PrefillResult` back the same way.

This keeps the single-worker/one-job-at-a-time invariant that already
serializes prefill against GC (`worker.py`'s module docstring: "one worker
means a GC job can never unlink chunks out of a depot SteamPrefill is
downloading into") — GC jobs never go through this split at all; they still
run in-process, in vault-api, unchanged.

### 2. Mechanism: the existing SQLite job queue, not a new one

Three options were considered:

- **The `jobs` table (chosen).** The jobs table and vault-api's own polling
  loop already exist; queue mode adds seven columns and no new moving parts.
  Both processes already need access to the same SQLite file (vault-api for
  job lifecycle, the runner for nothing else) and the same cache volume (the
  runner writes chunks into it) — nothing new to provision.
- **An HTTP sidecar** (vault-api calls the runner over HTTP). Rejected: it
  adds an authenticated network surface BETWEEN two containers that already
  share a disk — a second API key or mTLS story to design and operate, to
  replace a database write both sides can already make. It also does not
  remove any complexity the DB approach has (a lease/heartbeat design is
  still needed either way — HTTP does not make "is the other side still
  alive" free).
- **A Docker-socket mount** (vault-api tells the Docker daemon to run a
  one-shot runner container per job). Rejected outright: mounting the Docker
  socket into vault-api hands it root-equivalent control over the whole
  host — a bigger hole than the one EG-1 exists to close, defeating the
  entire point of the split.

### 3. Two writers, one file — WAL, verified

Both processes open the SAME sqlite file via the EXISTING
`vault_api.db.get_connection` — `PRAGMA journal_mode = WAL` +
`PRAGMA busy_timeout = 5000`, unchanged by this work package. Two Docker
containers on ONE host sharing a bind-mounted or named volume are
filesystem-equivalent to two OS processes sharing a file path (a named
volume IS a directory on the host filesystem; a bind mount is one by
definition) — so this is verified directly with two REAL, SEPARATE OS
processes (`multiprocessing`, `spawn` on Windows — genuinely separate
interpreters) racing 300 read-modify-write increments each against one file
(`api/tests/test_sqlite_wal_multiprocess.py`). Measured, not asserted: 600
increments landed correctly (no lost update), `PRAGMA integrity_check`
reported `ok`, and — because `busy_timeout` is expected to absorb ordinary
contention without ever raising `database is locked` — the test counts real
occurrences of that error rather than assuming there are none. Observed: 0,
in 0.97s wall time for the full 600-increment race. What this does NOT cover:
WAL over a network filesystem (NFS/SMB, no reliable shared-memory mmap) —
irrelevant to this stack, whose volumes are always host-local, and would be
its own decision if that ever changed.

The pre-existing per-check-then-act pattern
(`vault_api.jobs.immediate_transaction`, `BEGIN IMMEDIATE`) already gives
every read-modify-write in this codebase compare-and-swap semantics; queue
mode's new writes (`handoff_run`, `claim_run`, `record_run_heartbeat`,
`record_run_result`): `claim_run` reuses exactly this pattern; the other
three are single atomic statements (plain execute+commit) whose own
`WHERE run_claimed_by = ? AND status = ?` guards make a late write from a
superseded runner a no-op — rather than inventing a
second locking story.

**Atomic claim.** `jobs.claim_run` is a `BEGIN IMMEDIATE` compare-and-swap
(`UPDATE ... WHERE run_claimed_by IS NULL`): a restarted runner or a second
replica racing to claim the same job cannot both win — losers see
`rowcount == 0` and get `None`. Verified with 20 real threads, each on its
OWN sqlite3 connection (this project's thread-confinement rule forbids
sharing one connection across threads, so a thread-per-connection race is
the correct shape for this claim, not a weaker substitute for a
multi-process one — SQLite's locking is per-connection/per-file, not
per-thread, so the guarantee under test is identical either way), released
simultaneously via a `threading.Barrier`
(`test_jobs_run_queue.py::test_claim_run_two_concurrent_claimers_exactly_one_wins`):
exactly one winner, every time, across repeated runs.

**Correction from round-2 review, stated precisely because the first-round
claim here was overstated.** The coder's own single-variable mutation
(remove ONLY the `WHERE run_claimed_by IS NULL` guard, keep
`immediate_transaction`) still gave one winner, which is correct on its own
— but the coder then attributed exclusivity entirely to
`immediate_transaction`'s lock, based on a SECOND, compound mutation
(removing the lock AND the guard together, plus an artificial delay) that
does not isolate which mechanism is doing the work. The reviewer re-ran the
experiment across REAL, SEPARATE OS PROCESSES (8-way, `multiprocessing`
`Manager` + `Barrier`), one variable at a time: shipped code, one winner
(5/5 repeats); guard removed, lock kept, one winner; lock removed, guard
kept, **one winner** — the guard alone is sufficient; both removed together,
eight winners. **The honest conclusion: the WHERE-clause guard and
`immediate_transaction`'s locking are two INDEPENDENT, redundant mechanisms,
either one alone sufficient for this guarantee — genuine defence-in-depth,
not one load-bearing mechanism plus one decorative check.** That is also why
both stay in the shipped code rather than simplifying to one: the guard
survives a future refactor of `immediate_transaction` (or a mistaken call
site that forgets to wrap a write in it), and the lock survives a future
edit that loosens the guard's WHERE clause for an unrelated reason.

### 4. Crash semantics — no lease-stealing reclaim

Two failure directions, both must resolve to "the job does not stay
`running` forever, and nothing double-runs":

- **Runner dies mid-prefill.** Its heartbeat (`jobs.run_heartbeat_at`,
  refreshed by `prefill_runner` on `prefill.run_prefill`'s existing 0.2s
  subprocess poll tick, throttled to `VAULT_RUNNER_HEARTBEAT_SECONDS`, default
  5s) goes stale. `jobs.run_is_stale` — the ONE function that answers "is
  this runner presumed dead", used by both vault-api's live wait loop and
  its startup reattach path, so a lease-timeout bug has only one place to
  hide (the class of bug docs/LEARNINGS.md's "two call sites computing the
  same domain predicate WILL diverge" names) — fires once
  `VAULT_RUNNER_LEASE_TIMEOUT_SECONDS` (default 30s, 6x the heartbeat
  interval: a single missed heartbeat under a GC pause or a slow WAL fsync
  must not read as a dead process) has elapsed since the last heartbeat (or,
  if never claimed, since the claim, or since the job started running).
  **Second, independent margin need (round-2 review S6):** every stored
  timestamp in this database is second-precision
  (`jobs.TIMESTAMP_FORMAT`) — floored to the second it was written in, never
  rounded — so a LIVE staleness check against one can OVER-report elapsed
  time by almost a full second, regardless of the heartbeat-interval margin
  above. Measured: a lease set close to that 1-second floor produced 5/5
  false "presumed dead" verdicts against a genuinely alive runner. The
  shipped default (30s) and this package's own test settings (raised to an
  8s default, individual tests using 3s where a fast, genuinely-stale
  outcome is the point — see `tests/test_prefill_runner_process.py`'s
  `_queue_settings`) both keep well clear of this floor; an operator tuning
  `VAULT_RUNNER_LEASE_TIMEOUT_SECONDS` down should keep both margins in mind,
  not just the heartbeat one. The
  WORKER that is actively waiting on the job notices this live, on its own
  poll tick, and fails the job through the exact same branch a `timeout` or
  `exit_code` failure already uses — mapping untouched, `apps.status` ->
  `error`.
- **Worker dies while runner runs.** The runner is a SEPARATE process; it
  keeps executing and, when done, writes `run_completed_at`/
  `run_result_json` regardless of whether vault-api is up to see it. On
  restart, `jobs.recover_stale_jobs(queue_mode=True)` leaves a handed-off
  `running` prefill job (`run_use_force IS NOT NULL`) completely untouched
  — vault-api restarting says nothing about whether the SEPARATE runner
  process died too. `PrefillWorker._run`'s very first loop iteration checks
  `jobs.find_active_run` before claiming anything new, and resumes waiting
  on it through the SAME `prefill_queue.await_run_result` call the
  fresh-claim path uses — which is what then applies the SAME
  staleness check if the runner really did die too, live, on the new
  process's first tick, rather than needing a second, disagreeing
  reconciliation path.

**Deliberately no lease-stealing reclaim.** Once `run_is_stale` fires, the
JOB is failed — its `status` leaves `'running'` — rather than a second
runner instance silently taking over the same row. This is what makes
`claim_run`'s plain `run_claimed_by IS NULL` check sufficient for mutual
exclusion on its own: `claim_run` also requires `status = 'running'`, and a
terminal job can never satisfy that again, so nothing can claim a job
vault-api has already declared dead. The alternative (steal the lease,
let a new runner instance re-execute the SAME job id) was considered and
rejected: it would put two independent authorities — vault-api's
staleness check and a runner's reclaim-and-retry — in an unresolvable race
about who gets to decide a job's fate, whereas failing the job and letting
an operator (or the scheduler) re-queue a FRESH job under `POST /v1/prefill`
reuses a decision this project already made for the single-process case
(`jobs.recover_stale_jobs`'s existing message: "Re-queue it ... if you still
want that app prefilled") rather than inventing a new one. The accepted
residual cost: an orphaned SteamPrefill subprocess behind a dead runner may
briefly keep writing into the cache after its job has been failed — harmless
(the bytes are real, and the next run replays them as local HITs; the same
reasoning `recover_stale_jobs`'s pre-existing docstring already accepts for
the single-process case) and guarded against corrupting a terminal row by
`record_run_result`/`record_run_heartbeat`'s own `WHERE run_claimed_by = ?
AND status = 'running'` no-op guards.

**The depot-signature snapshot survives the restart too — corrected call
graph (round-2 review, blocker B1).** `apply_observed_mapping`'s before/after
diff needs the snapshot taken BEFORE the run started; a process that
restarted mid-wait cannot honestly recompute it (a live re-scan at reattach
time would already include whatever the runner wrote while vault-api was
down). `handoff_run` persists it (`jobs.run_before_json`, JSON-encoded) at
the hand-off. **The first-round version of this ADR said a `WHERE
run_use_force IS NULL` write-once guard on `handoff_run` was what made a
reattach's "second call" safe — that described a call that never happens.**
`PrefillWorker._resume_prefill` (the reattach path) does not call
`handoff_run` AT ALL; it reads the row's existing `run_use_force`/
`run_before_json` back directly. `handoff_run`'s only real caller in the
whole codebase is the fresh-claim path
(`PrefillWorker._run_prefill_via_queue`), invoked once per genuinely NEW run
of a job row. Reasoning about the write-once guard as protection for the
reattach case — a call site that does not exist — hid its actual, harmful
effect: it also silently blocked the SECOND real run a
`POST /v1/jobs/{id}/resume` produces, leaving a resumed job's stale
`run_completed_at`/`run_result_json` in place forever (measured end to end:
`argv.json` never recreated, the job re-parked at `paused` without ever
re-invoking SteamPrefill). **The fix:** `handoff_run` is unconditional — every
call is a fresh-attempt write that also resets every runner-owned column
(`run_claimed_by`, `run_claimed_at`, `run_heartbeat_at`, `run_completed_at`,
`run_result_json`) to `NULL`. This is safe for the restart-reattach case
specifically because that case never calls it; the original snapshot the
reattach path relies on is simply never touched by a second hand-off it
never receives.

**Addendum 2026-10-01 (WP API-FIX-1).** The text above is kept as decided;
three points in it are superseded, and one rule is added (the last
bullet), by the following:

- **The lease for a never-claimed run starts at the hand-off**, not at the
  claim or at `started_at` (the "if never claimed, since the claim, or since
  the job started running" clause above). `started_at` is stamped by
  `claim_next_job`, before vault-api's own pre-hand-off depot scan, which on
  a cold, large cache can outlast the whole lease; every job then failed as
  `runner_lost` before any runner could claim it.
- **`handoff_run` stamps `run_heartbeat_at` with the hand-off time instead
  of resetting it to `NULL`** (the reset list above). This is the mechanism
  for the previous point: `run_is_stale` reads `run_heartbeat_at` first, and
  the runner overwrites it with its own heartbeats once it claims the job.
- **`find_active_run` also returns completed-but-uncollected runs**
  (`run_completed_at` set, job still `running`). The runner finishing while
  vault-api is down (`compose up -d` recreating both containers) is the
  ordinary shape of the "worker dies while runner runs" case; filtering
  those rows out left the job `running` forever, since `recover_stale_jobs`
  skips handed-off rows by design.
- **`resume_job` resets every `run_*` column to `NULL`** when it re-queues
  a paused job. Because `find_active_run` now returns completed rows, the
  paused attempt's leftovers (`run_use_force` set, a `paused` result
  recorded) would otherwise turn a vault-api death between `claim_next_job`
  and `handoff_run` of the resumed attempt into a reattach that delivers the
  OLD `paused` result. With the reset, such a row has `run_use_force IS
  NULL` and `recover_stale_jobs` fails it as a single-process orphan.
  `resume_job` is the only transition back to `queued`.

### 5. The interactive login path (ADR-0004 decision 1) moves containers

ADR-0004 decision 1: "Login happens once, interactively, in SteamPrefill's
own prompt." Today that means running `SteamPrefill select-apps` via
`docker exec` into the vault-api container, because that is where
`VAULT_STEAMPREFILL_PATH` and SteamPrefill's `Config/` (the Steam session)
live. **After the split, in queue mode, that changes: SteamPrefill's binary
and its `Config/` volume live in the `prefill_runner` container, so the
one-time login step becomes `docker exec` into THAT container instead.**
This is a deliberate, accepted consequence of the split, not an oversight —
it is exactly the container the credential-bearing state (a logged-in Steam
session) now lives in, and it is the container S-2's compose wiring gives a
volume mount for `Config/`. vault-api itself still never sees or stores
Steam credentials — nothing about that half of ADR-0004 changes; only WHERE
the interactive step is run does. `api/README.md`'s "Queue mode" section
documents the exact command for S-2's operators.

### 6. Mode switch — `VAULT_PREFILL_MODE`, default `subprocess`

`subprocess` (default) is BYTE-IDENTICAL to every version of vault-api
before this work package: `prefill.run_prefill` is called directly, from
`PrefillWorker._execute_prefill`, exactly as it always was — this module is
untouched by WP S-1. This is the required default for the bare-metal/native
dev setup (`api/README.md`), where there is no second process to run a
runner in at all, and it is what makes the existing prefill/worker test
suites (`test_prefill_runner.py`, `test_worker.py`, `test_jobs_queue.py`) a
genuine regression net rather than a test this package had to rewrite: they
were not touched, and they still pass unmodified against a full
`SCHEMA_VERSION = 15` database.

`queue` hands execution off through the `jobs` table instead. Chosen at
vault-api startup from `VAULT_PREFILL_MODE`, validated with the same
strict-enum house rule `VAULT_AUTO_GC`/`VAULT_MANIFEST_ORACLE` already use —
a typo here is a security-relevant misunderstanding (an operator believing
EG-1's egress lock is in effect while vault-api is quietly still running
SteamPrefill itself), not a cosmetic one, so it fails loudly at boot rather
than silently falling back.

## Consequences

- Schema v15: seven new `jobs` columns (`run_use_force`, `run_before_json`,
  `run_claimed_by`, `run_claimed_at`, `run_heartbeat_at`, `run_completed_at`,
  `run_result_json`), all nullable, additive, NULL for every job that never
  goes through queue mode (every GC job, every prefill job run in the
  default `subprocess` mode, every job that predates this version).
- New module `vault_api/prefill_queue.py`: the encode/decode helpers and the
  wait loop shared by both sides of the split. New module
  `vault_api/prefill_runner.py`: the CLI entrypoint
  (`python -m vault_api.prefill_runner`) — S-2 gives it its own service in
  `compose.yaml`.
- `worker.py`'s `_execute_prefill` is refactored (not behaviourally changed
  in `subprocess` mode) to extract the post-run branch logic
  (`_finalize_prefill_result`) so both the fresh-claim path and the
  after-restart reattach path (`_resume_prefill`) reach it identically,
  regardless of which process actually ran SteamPrefill.
- This enables EG-1's honest lock: with SteamPrefill execution living
  entirely in a separate container, vault-api's own container can be
  restricted to LAN-only egress without breaking prefill — the finding
  EG-1's stop report identified as the blocker this ADR exists to remove.
- Not in this package's footprint (by design, S-2's job): `deploy/compose.yaml`
  wiring for the `prefill_runner` service, its `Config/`/cache volume mounts,
  and the `docs/security/` threat-model update the pair (S-1 + S-2) will need
  once landed.
- `prefill_runner` does not require `VAULT_API_KEY` (round-2 review S2):
  `Settings.from_env(require_api_key=False)` is the one exception to that
  field's normal "fails loudly if absent" rule, because this process never
  serves HTTP and never authenticates anything — see §2/§5 above. S-2's
  compose service for `prefill_runner` therefore does not need that secret
  forwarded into it.
- `handoff_run` is an unconditional fresh-attempt write, not write-once
  (round-2 review, blocker B1) — see §4's corrected reattach discussion.
  This is what makes `POST /v1/jobs/{id}/resume` actually re-run
  SteamPrefill in queue mode; the write-once version shipped in the first
  round silently broke it.
