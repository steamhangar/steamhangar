# ADR-0019: Several Steam accounts per household (MULTI-1, roadmap item D3)

Date: 2026-10-02
Status: Proposed (draft; depends on ADR-0018; awaits user decisions on the
open questions; code only after tag v0.1.0, ADR-0016)

## Context

**One session per SteamPrefill install.** SteamPrefill v3.7.1 resolves its
state directory from the binary's own location:
`ConfigDir = Path.Combine(AppContext.BaseDirectory, "Config")`, and under
it `account.config` (the session), `selectedAppsToPrefill.json` (the app
selection vault-api writes, `api/vault_api/prefill.py::write_selected_apps`)
and `successfullyDownloadedDepots.json` (its up-to-date bookkeeping, the
basis of ADR-0006's non-forced check). Source: `SteamPrefill/Settings/
AppConfig.cs` at tag v3.7.1, read this session; in-repo corroboration in
`poc/steamprefill/PROTOCOL.md` (`Config/` appears next to the binary on
first run, and that file cites `AppConfig.cs`). There is no `--user` or
config-path flag (`prefill.py` module docstring lists the verified `prefill`
options; none is a path). Upstream's FAQ says so and names the workaround:
"SteamPrefill doesn't directly support multiple accounts ... you should
create a separate instance of SteamPrefill for each account" (wiki FAQ,
read this session). Issue #113 ("Allow use of multiple accounts for
prefilling") was closed Won't Fix; the maintainer wrote on 2024-07-10 that
the workaround is "create one instance of SteamPrefill for each account"
(issue comments, read this session).

**One SteamID per vault today.** `steam_library_steamid`
(`api/vault_api/settings_store.py::OVERRIDABLE_SPECS`, ADR-0016 addendum,
ADR-0009 addendum) is a single SteamID64, validated by
`steam_relay.valid_steamid64`. The web library reads it per request
(`web/js/lib/owned-library.js`, `web/js/owned-singleton.js`) and merges the
one relay result into the grid (`mergeOwnedLibrary`). The Android app still
holds its own OpenID-derived SteamID (`app/.../repo/SteamIdentityRepository.kt`);
APP-FEAT-1 (plan §11 item 13 B) moves it to the stored setting.

**The cache is account-agnostic.** Chunks are stored and served by depot
path; ownership is checked by Steam when it hands out manifest request
codes, before any chunk is requested (`docs/handover/post-0.1.0-roadmap.md`
"Context", ADR-0001). A game one account prefilled is a HIT for every client
in the house. Nothing in `core/` changes for this ADR.

**Steam Families** sharing is deliberately not pursued (user decision
2026-10-02, plan §11 item 13 D).

**The household case.** Two or more people, each with their own Steam
account and library, one vault. Each wants their games kept current with
their own session, and the library view should show what the household
owns, not what one person owns.

## Design (proposed)

### 1. Per-account install layout

One SteamPrefill instance per account, inside the existing
`vault-steamprefill` volume (mounted at `/opt/steamprefill/Config` on
`vault-runner` only, `deploy/compose.yaml`; `deploy/tests/verify-stack.sh`
step 3h pins that exactly one service mounts it):

```
/opt/steamprefill/Config/accounts/<account_id>/SteamPrefill
/opt/steamprefill/Config/accounts/<account_id>/Config/account.config
/opt/steamprefill/Config/accounts/<account_id>/Config/selectedAppsToPrefill.json
/opt/steamprefill/Config/accounts/<account_id>/Config/successfullyDownloadedDepots.json
```

The nested `Config/accounts/<id>/Config` reads oddly but keeps the volume,
its mount, its backup advice ("treat the `vault-steamprefill` volume as
sensitive", `docs/security/threat-model.md` §3) and the verify-stack mount
assertion unchanged. `<account_id>` is the integer primary key of the
`steam_accounts` row (section 2), never a user-supplied label, so the path
needs no sanitising.

How the binary gets into each account directory:

- **Copy, made by the runner (decided).** `prefill_runner`
  compares the SHA-256 of the image's `/opt/steamprefill/SteamPrefill`
  (`api/Dockerfile`, hash-pinned release) against each
  `accounts/<id>/SteamPrefill` and copies when missing or different.
  Idempotent, survives an image bump (the next start refreshes every copy),
  and the running binary is a plain file on the volume, so nothing about
  `AppContext.BaseDirectory` is in question. Cost: one binary per account on
  the volume (the release zip is 12,095,541 bytes per `api/Dockerfile`; the
  unpacked self-contained binary is larger; measure in the WP).
- **Hardlink: not possible.** The image's binary sits in an overlay lower
  layer, the volume is a separate mount, and `link(2)` cannot cross mounts
  (`EXDEV`). General Linux fact, not re-measured.
- **Symlink: not chosen.** The handover says a symlinked binary "probably
  resolves to the real BaseDirectory" (inference, unverified); if so, every
  account would share the image's `Config/`. Even a favourable result would
  rest on a .NET runtime behaviour this project does not control and a
  version bump could change, so the copy is the decision here, not a user
  question. Optional verify-stack note, credential-free, if the fact is
  wanted on record: `accounts/A/SteamPrefill -> /opt/steamprefill/SteamPrefill`,
  `accounts/A/Config/selectedAppsToPrefill.json = [440]`,
  `/opt/steamprefill/Config/selectedAppsToPrefill.json = [730]`, then
  `accounts/A/SteamPrefill select-apps status < /dev/null`: `[440]` means
  the link is honoured, `[730]` means it is resolved.

**Shared HOME stays shared.** `vault-steamprefill-home` (mounted on
`vault-api` and `vault-runner`, `deploy/compose.yaml`) holds SteamPrefill's
regenerable manifest cache under `$HOME` (`api/Dockerfile` mount-point note),
which ADR-0006 decision 3 reads for `depot_manifests`. Manifests are keyed
by depot and manifest id, not by account (inference from the
`{appid}_{containingapp}_{depotid}_{manifestid}.bin` naming, ADR-0006), and
only one SteamPrefill process runs at a time (section 6), so accounts
cannot race on it.

**Backward compatibility.** The existing single `Config/` becomes account
1. On first start with the new layout, `prefill_runner` moves the three
root-level files into `accounts/1/Config/` if `accounts/` does not exist
and `account.config` does; vault-api's schema migration inserts row 1
("Account 1", `persona_name NULL`) unconditionally. A vault without a
legacy session then simply has an account 1 with no session, which the
Settings block shows as "missing". Whether this is automatic or the
operator re-logs in, and whether row 1 inherits `steam_library_steamid`,
is open question 5.

**Directory lifecycle across the mount boundary.** vault-api has no
`Config/` mount (`deploy/compose.yaml`, verify-stack step 3h), so it never
creates or deletes an account directory; it only writes `steam_accounts`
rows. The runner reconciles `accounts/<id>/` against that table: at start
for every row (hash-compared copy, refreshed after an image bump), and on
the first job it claims for an id (a prefill or an ADR-0018 `steam_login`
job) when the directory is still missing, so an account added at runtime
needs no restart. Removal travels the way ADR-0018 §2 moves a login: the
`DELETE` route only marks the row (`removed_at`) and enqueues a job of a
new type `steam_account_remove`; the runner deletes the directory, session
file included, and records the result; the worker's finalize step deletes
the row. The runner's database writes stay the `run_*` columns (ADR-0012
§1).

### 2. Data model

Schema v16, additive, SQLite-plain (`api/vault_api/db.py` house style: one
`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ... ADD COLUMN` guarded per
column as v4/v5/v15 do):

```sql
CREATE TABLE IF NOT EXISTS steam_accounts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    label         TEXT NOT NULL,            -- operator-chosen, shown in UI
    steamid64     TEXT,                     -- 17 digits or NULL (not known yet)
    persona_name  TEXT,                     -- public persona via fetch_player_summaries, NULL until known
    created_at    TEXT NOT NULL,
    last_login_at TEXT,                     -- stamped by a successful ADR-0018 login
    removed_at    TEXT                      -- set by DELETE; row deleted when the removal job ends
);
-- jobs: one nullable column
ALTER TABLE jobs ADD COLUMN account_id INTEGER;  -- NULL = legacy single account (migration)
```

`steamid64` passes `steam_relay.valid_steamid64` and is stored as TEXT like
the setting (a JSON number would already have rounded it, ADR-0016
addendum). `label` reuses the `vault_name` grammar (`config.parse_vault_name`).
The Steam login name is deliberately not stored: it is half of a credential
pair, and ADR-0018 open question 3 recommends against showing it; the
public persona from `steam_relay.fetch_player_summaries` labels the account
instead. The session file and its refresh token never enter the database
(ADR-0004; ADR-0018 "Why a challenge URL may sit in the database and the
token must not"). Presence and expiry do: ADR-0018 §4 proposes a single-row
`runner_state` with `session_present`, `session_exp`, `session_account`.
**Required amendment to ADR-0018:** that row needs an account dimension,
one status per `steam_accounts.id` (a `runner_account_state` table keyed by
it, or `runner_state` losing its `CHECK (id = 1)`), refreshed on the same
cadence from each account's directory. `jobs.account_id` rides the
queue-mode hand-off (`jobs.handoff_run` writes it, `jobs.claim_run` returns
it) so `prefill_runner` builds the executable path
`accounts/<account_id>/SteamPrefill` instead of the fixed
`Settings.steamprefill_path` (`api/vault_api/prefill_runner.py::PrefillRunner._execute`).
`prefill.run_prefill` already takes the executable path and derives
`Config/` from it (`prefill.py::write_selected_apps`); it is unchanged.

### 3. Account selection at run time

- **The job carries the account.** `POST /v1/prefill` gains an optional
  `account_id`. `jobs.enqueue_prefill`'s dedupe key stays `appid` (one
  active prefill job per app, `jobs.py::enqueue_prefill`); the account is a
  property of that one job.
- **Enqueue without an account.** vault-api resolves it: the per-account
  owned-games lists from the relay (`steam_relay.fetch_owned_games`, one
  call per account with a known `steamid64`, served from `RelayCache`)
  name the accounts that own the app. One owner: use it. Several owners:
  open question 3. None known: fallback below.
- **Fallback when ownership is unknown** (private profile, relay key not
  set, account without a `steamid64`): open question 4, because it is a
  user-visible cost decision. The signal both ways rely on exists: the
  worker ends a run in which SteamPrefill reported `Updated=0 AND Up To
  Date=0` as "did not consider this app"
  (`api/vault_api/worker.py::_finalize_prefill_result`, ADR-0006 decision 1),
  and API-FIX-3 (plan §11 item 13 B, before v0.1.0) separates that from a
  genuine `Failed` count. Weg A chains: on "not considered" with an
  auto-selected account and candidates left, the worker hands the same job
  off again with the next account in `id` order, each attempt one Steam
  login of about 3 s (ADR-0006 "Honest limits"), the log naming every
  account tried, `error` only after the last. Weg B does not chain: sticky
  owner, else lowest `id`, and "not considered" ends the job as today with
  a hint to set `account_id`. If Weg A is chosen, the re-hand-off must not
  use `handoff_run` as is: it is a fresh-attempt write (ADR-0012 §4) that
  overwrites `run_before_json`, and `apply_observed_mapping` needs the
  first attempt's depot snapshot, so the chain re-hands off with the
  snapshot carried over. A genuine failure on any account ends the job
  either way.
- **Stickiness, for ADR-0006's cheap check.** `successfullyDownloadedDepots.json`
  is per install, so a non-forced run under account B for an app account A
  filled is not a no-op: B's SteamPrefill re-requests everything, served as
  local HITs (disk speed, not WAN; `prefill.py` docstring on `--force`).
  The selection therefore prefers the account whose last job for this app
  ended `done` (from `jobs`), before owned-games ordering. Whether that
  stickiness is the rule is part of open question 3.
- **The sweep.** Clarification: today's sweep does not enumerate owned
  games. `scheduler.py::compute_targets` takes the union of every fresh
  client's installed list plus every app with cache content on disk
  (ADR-0014). That stays. Each swept app is enqueued without an account
  and attributed by the rule above, so a game only account B owns is swept
  under B. The owned-games union is a library concern (section 4), not a
  sweep input, unless question 6 says otherwise.

### 4. Library: the union of several SteamIDs

`web/js/lib/owned-library.js` merges one relay result into the vault rows.
With D3 the loader fetches one owned list per library SteamID and merges
the union (the synthesized "not cached" row carries
`owners: [account_id, ...]`); "N owned" becomes the size of the union.
Where the SteamID list comes from is open question 1. The owner hint per
card (and in the detail sheet and job title,
`web/js/views/downloads.js::nameFor`) is open question 2, bound by plan §7
Phase 4h: no number or label held up against a person in a shared living
room. Ownership is not playtime, but "whose game is this" is still a
per-person statement, so a hint is neutral or dismissible, never a
per-person count. Android parity follows APP-FEAT-1 (`LibraryMerge.kt` is
`mergeOwnedLibrary`'s sibling, per the web module's header).

### 5. Login per account

Login goes through ADR-0018 (QR login helper, draft). The helper runs in
`vault-runner` and writes `account.config`; for D3 it takes the account's
directory as its target (`accounts/<id>/Config/`). The terminal fallback
becomes `docker exec ... /opt/steamprefill/Config/accounts/<id>/SteamPrefill select-apps`
(today's command: `deploy/README.md` "First run", `api/README.md` "Queue
mode"). The Settings block "Steam library" (`web/js/views/settings.js::buildSteamLibraryBlock`)
becomes "Steam accounts": one row per account with label, SteamID64,
session status (present / missing / expired, from ADR-0018), "Log in",
"Remove"; plus "Add account". Removal, decided here from existing
patterns: `DELETE /v1/steam/accounts/{id}` answers `409` while a queued or
running job references the account (the `DELETE /v1/cache/{appid}` rule),
otherwise marks the row and enqueues the removal job (section 1); when it
ends, the worker deletes the row and sets `jobs.account_id` to NULL on the
account's history rows rather than deleting jobs. A marked row is excluded
from selection and from the library union.

### 6. What does NOT change

- The cache and `vault-core`: chunks keyed by depot path, no per-account
  state, no config change.
- The single-worker runner: one job at a time regardless of account
  (`worker.py` module docstring, ADR-0012 §1). Two accounts never download
  in parallel; D3 adds no concurrency.
- The API key model: one `VAULT_API_KEY` for every caller; named, scoped
  keys are D5 (plan §7 Phase 6). Anyone with the key can enqueue under any
  account, log in any account via ADR-0018, and see every account's library.
- The relay: still the two read-only endpoints, still one operator-owned
  key (ADR-0004 addendum 2), still the env-only privacy gate (ADR-0010).
- `steam_library_steamid` keeps working as the single-account form until
  the user decides question 1.

## Open questions for the user

1. **Where the library SteamID list comes from.**
   Weg A: the `steam_accounts` table only. A login yields the SteamID64:
   the stored refresh token is a JWT whose `sub` claim is the SteamID64
   (inference from the handover's "JWT `exp`" note and common Steam token
   shape; ADR-0018 must verify before relying on it). No second list.
   Weg B: an explicit setting `steam_library_steamids` (list), the
   generalisation of today's `steam_library_steamid`, independent of
   sessions.
   Weg C: both; accounts plus manual extra IDs for people without a
   session on the vault.
   Recommendation: Weg C, because a household member who never prefills
   (a child's account, a guest) still belongs in the library, and a vault
   without the relay key still needs the account list for prefill.

2. **Owner hint in the UI.**
   Weg A: the account label (or persona name) on the card.
   Weg B: a neutral "owned by 1 of N" marker plus a per-account filter
   chip the viewer picks for themselves.
   Weg C: no hint; the union only.
   Recommendation: Weg B. It answers "can I download this" without naming
   anyone on a shared screen, and the filter is opt-in per view, which is
   the Phase 4h posture (off by default or dismissible, nothing held up to
   somebody else).

3. **Selection rule when several accounts own the app.**
   Weg A: sticky owner (the account whose last job for the app ended
   `done`), then lowest `id`.
   Weg B: round robin across owners.
   Weg C: explicit choice in the enqueue dialog, default sticky owner.
   Recommendation: Weg A for the sweep and API default, Weg C's explicit
   field for `POST /v1/prefill` and the web dialog. Stickiness keeps
   ADR-0006's non-forced no-op cheap; round robin defeats it on every
   second run.

4. **Fallback when no account is known to own the app** (section 3).
   Weg A: the automatic chain, accounts in `id` order until one considers
   the app; up to N logins per app, on every sweep night, under the single
   worker, and the common case while profiles are private.
   Weg B: no chain; sticky owner, else lowest `id`; "not considered" ends
   the job as today with a hint to set `account_id`.
   Recommendation: Weg B. The chain is the most complex new mechanism in
   this ADR and its cost lands on the nightly sweep for every household
   with one private profile; Weg B keeps today's job semantics, and a
   wrong guess costs one explicit re-enqueue with the account chosen.

5. **Migration of the existing session.**
   Weg A: automatic, the runner moves the files into `accounts/1/`,
   vault-api labels it "Account 1" and copies `steam_library_steamid`
   (API-FEAT-1) into its `steamid64`, editable afterwards.
   Weg B: no move; the operator removes the old session and logs in again
   through ADR-0018 as a named account; `steamid64` stays NULL until typed
   or read by ADR-0018 (question 1 Weg A).
   Recommendation: Weg A. A session valid for about 200 days is worth
   keeping, the move is three files, and the label is editable afterwards.
   That `steam_library_steamid` belongs to the legacy session's account is
   an inference: nothing ties the setting to the session file, so the copy
   is a pre-filled guess the operator can correct.

6. **Should the sweep also enqueue owned-but-never-cached games** (the
   owned union as a third sweep source)? Weg A: no, installed and cached
   stay the criterion (ADR-0014). Weg B: yes, opt-in setting.
   Recommendation: Weg A; it is a separate decision with its own disk
   cost argument, not part of multi-account.

## Tests (planned)

- Settings and schema: `steam_accounts` created on a v15 database, row 1
  inserted once, `jobs.account_id` NULL on every existing row; `steamid64`
  rejected unless `valid_steamid64` passes; `label` grammar shared with
  `vault_name` (structural pin, `docs/LEARNINGS.md` "two call sites").
- Account selection, pure unit tests: one owner, several owners under the
  chosen rule, no owner under the question-4 rule, sticky owner from job
  history; with Weg A, a mutation check that removing the "not considered"
  signal ends the chain after one attempt, and that the second hand-off
  keeps the first attempt's `run_before_json`.
- Runner: `tests/stub_prefill.py` with one stub per account dir; the
  selection lands in that account's `Config/` and the other account's is
  untouched; the hash-compared copy is made, skipped when equal, refreshed
  when the image binary changes; the legacy move runs exactly once.
- Queue mode end to end (`test_prefill_runner_process.py` shape): a job
  under account 2 reaches the account-2 stub; an account added at runtime
  gets its directory on the first claimed job; a `steam_account_remove`
  job deletes the directory and the worker deletes the row; with
  question-4 Weg A, "not considered" under account 1 re-hands off to
  account 2 and ends `done`.
- `deploy/tests/verify-stack.sh`: two account directories with the real
  binary copied in, each reaching the credential-free "account is
  required" prompt from its own directory (step 2.smoke pattern); step 3h
  still sees exactly one `Config/` mount.
- Web (`node --test`): union of two owned lists, header count, owner marker
  and filter per the chosen option, single-list behaviour against an older
  vault-api without `steam_accounts`.
- Optional: the symlink note from section 1 as a verify-stack step, the
  result (`[440]` or `[730]`) recorded either way.

## Consequences and honest limits

- **The private-profile cost multiplies.** ADR-0004 addendum 2 accepted
  that the operator's relay key sees nothing for a private profile. With N
  accounts every private one is invisible to ownership-based selection,
  with question-4 Weg A the fallback costs up to N logins per app on every
  sweep night, and "0 games" still cannot be told from "private"
  (`docs/LEARNINGS.md` "An empty relay result is not a count").
- **One more full-account session file per person at rest**, all in one
  volume readable by the runner's uid (`docs/security/threat-model.md` §3
  "Where credentials do live"). Whoever reads the volume can act as every
  household member's session, not one. §3 needs a paragraph; ADR-0018's
  uid-isolation remark applies N times.
- **The operator-owned relay key sees everyone's library**; every
  account's queries leave the LAN under one key (threat model §5). The
  ADR-0010 gate applies to all of them equally.
- **One API key, no per-person boundary** until D5: any key holder can
  log in, remove, or prefill under any account.
- **Disk per binary copy** on the session volume; its backup grows by that.
- **Per-install bookkeeping** (`successfullyDownloadedDepots.json`): an app
  that changes owner costs one full local re-read; stickiness limits that.
- **Job dedupe is per app**, not per app and account: a second person
  pressing "download" on a game already queued gets the existing job.
- **Android parity later** (APP-FEAT-1, then a union-aware `LibraryMerge.kt`).
- **No Steam Families.** A game shared into a Family is not in the
  borrower's `GetOwnedGames`; the purchasing account must be logged in.
- **Steam's location check** (handover "Context") applies to each of the N
  logins.

## Work package split (all after tag v0.1.0 and after ADR-0018's packages)

1. **MULTI-1a, schema and accounts API** (api): `steam_accounts`,
   `jobs.account_id`, `GET/POST/PATCH/DELETE /v1/steam/accounts` (DELETE
   marks the row and enqueues `steam_account_remove`), `POST /v1/prefill`
   optional `account_id`, tests. About 1-2 h.
2. **MULTI-1b, runner per-account layout and migration** (api, deploy):
   reconcile at start and on first claim, hash-compared copy, path from
   `account_id`, the removal job, legacy move, verify-stack two-account
   step. About 1-2 h.
3. **MULTI-1c, selection rule** (api): owner resolution from the relay,
   the question-4 fallback, stickiness, sweep attribution, tests. About
   1-2 h. Needs API-FIX-3 merged. If question 4 is Weg A: the re-hand-off
   must carry the first attempt's `run_before_json` forward, since
   `handoff_run` resets it (ADR-0012 §4).
4. **MULTI-1d, library union and owner hint** (web): multi-list loader,
   union merge, the decided hint, tests. About 1-2 h.
5. **MULTI-1e, Settings accounts block** (web): account rows, session
   status from ADR-0018, add/remove, login entry point. About 1-2 h.
6. **MULTI-1f, docs**: `deploy/README.md` first run per account,
   `api/README.md` queue mode, threat model §3/§5, this ADR to Accepted
   with the decisions recorded. About 1 h.
7. **MULTI-1g, Android** (app): after APP-FEAT-1; union grid and hint.
   Separate package, not scheduled here.
