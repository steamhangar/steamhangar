# ADR-0006: Staleness detection — non-forced prefill as the primary oracle

Date: 2026-08-06
Status: Accepted (orchestrator decision from evidence; see docs/research/phase3-manifests.md).
Decision 4's "opt-in, default off" superseded by the addendum 2026-10-13 below;
that addendum's window gate superseded by the note 2026-10-14 (WP UPD-5).

## Decision

1. The primary staleness check is a **SteamPrefill run WITHOUT `--force`**:
   SteamPrefill's own up-to-date bookkeeping makes it a ~3 s, zero-download
   no-op for current apps (measured). The summary counters (`Updated`,
   `Up To Date`) become the job outcome; `Updated==0 AND UpToDate==0`
   means the app was never considered (unowned/filtered) and the job must
   NOT end `done` (job-outcome honesty, WP 1.7 finding).
2. `--force` is reserved for first fills and post-deletion refills via a
   per-app `needs_force` flag set by the deletion path (preserves the
   WP 1.4 rationale: SteamPrefill's state does not know about our deletes).
3. Manifest state is recorded per depot from SteamPrefill's temp-cache
   filenames (`{appid}_{containingapp}_{depotid}_{manifestid}.bin`) into a
   `depot_manifests` table; the `.bin` files are archived durably (they
   survive `clear-temp`).
4. An optional, opt-in, fail-soft third-party oracle
   (`VAULT_MANIFEST_ORACLE=steamcmd_api`) may set the pre-emptive orange
   "stale" badge between cron ticks. Default off; unaffiliated with Valve;
   public branch only.

## Rejected

- `ISteamApps/UpToDateCheck`: only answers for apps with dedicated-server
  versions; returns server version, not buildid (live-tested against five
  apps — four failed).
- SteamCMD binary / ValvePython PICS in-process: ~250 MB runtime or a
  heavy dependency stack inside a deliberately small service (plan §9).

## Honest limits

Tier 1 gives "current as of <timestamp>", not a pre-emptive stale badge;
each per-app check costs a Steam login (~3 s) — sweeps are spaced across
the cron window, not batched (batching would destroy per-app attribution).

## Addendum 2026-10-13 — always-on update check, oracle on by default (roadmap D14, UPD-1..4)

User decisions 2026-10-13 (roadmap D14 and its wave plan,
`docs/handover/post-0.1.0-roadmap.md`). The decisions above stay as
recorded; this addendum supersedes two points.

1. **The 2026-08-18 "check & update, never bare check" rule is
   superseded.** That rule (WP 4c-web divergence register in
   `docs/WORKPACKAGES.md`, adopted on Android in WP 4c-app) allowed no
   control that only checks. From now on there is an update check that
   downloads nothing. The scheduled update check runs every
   `VAULT_SCHEDULE_INTERVAL_MINUTES` while a schedule window is
   configured, open or closed; without a window there is no scheduled
   check, only the manual `POST /v1/updates/check` (WP UPD-1;
   asynchronous: it answers `202` and progress is read from
   `GET /v1/updates/status`). Only an open window starts a download,
   besides the existing explicit "Check & update" control. The check
   result surfaces as `update_available` on the games endpoints and as an
   "Update available" badge, a "Check updates" button and an "Updates"
   filter chip in web (UPD-2) and Android (UPD-3). The pull-to-refresh
   rule of the same WP (a refresh gesture never contacts Steam) is
   unchanged.
2. **The manifest oracle (`VAULT_MANIFEST_ORACLE`) defaults to on in
   code** (user decision 2026-10-13, option A), superseding decision 4's
   "opt-in" and "default off": the oracle is now opt-out. The manifest
   oracle is on by default: `VAULT_MANIFEST_ORACLE` unset or empty means
   `steamcmd_api`; `off` or `none` opts out; any other value refuses to
   start (WP UPD-1). The update check works only through the oracle, so
   with the old default it would do nothing in prod. Rationale: only prod
   has network access for this; the dev instance has no schedule window
   configured, so it never checks on its own. No prod `.env` change is
   part of this decision. Egress: while the oracle is on and
   `VAULT_MANIFEST_ORACLE_URL` is `https://` to the default host
   `api.steamcmd.net` (`config.ORACLE_AUTO_ALLOWED_HOST`), vault-proxy and
   vault-api's startup check allow that host without a
   `VAULT_EGRESS_ALLOW` entry; plain `http://` to the same host or any
   other oracle host still needs an explicit entry; `off` removes the
   exception (option A, narrowed by the orchestrator to the default host,
   confirmed by the user 2026-10-13; ADR-0011 addendum 2026-10-13). The
   rest of decision 4 holds: optional (can be switched off), fail-soft,
   unaffiliated with Valve, public branch only.
3. **Only prod checks, cached games only.** The check asks about games
   that currently have cache content (Steam tool packages excluded); it
   never widens the set to owned-but-uncached apps. Production is the only
   instance meant to check on its own. This is implemented by the window
   gate in point 1 (orchestrator choice, consistent with the user
   decision): an instance without a schedule window, like dev, has no
   scheduled check. User decision 2026-10-13: a manual "Check updates" on
   the dev instance MAY query the oracle (app metadata only, no game
   data); the dev env stays unchanged.
4. **Behaviour change on upgrade.** With the oracle on and a window
   configured, the app id of every cached game is sent to the oracle
   (`api.steamcmd.net` by default) in the background (metadata only),
   once per `VAULT_SCHEDULE_INTERVAL_MINUTES`. Installs that already had
   the oracle on get this background polling new; fresh installs, and
   upgrades that never set the variable, now query this third party by
   default. Opt-out for both: `VAULT_MANIFEST_ORACLE=off` (or `none`).

## Addendum 2026-10-13 — the grouped delete is the one place oracle data can widen a deletion (WP TOOL-APPS-2b, ADR-0022)

Decision 4 promised that oracle data can only add protection. That still
holds for GC, the scheduler and the plain `DELETE /v1/cache/{appid}`. It no
longer holds in full for one request: the user-confirmed grouped delete
(`DELETE /v1/cache/{appid}?with_packages=true&members=...`, ADR-0022 §4).
Which apps are content packages is decided from stored classification,
which comes from the store lookup or, while the oracle is on, from the
oracle's stored `common.type` / `common.parent` and a cached parent's
`listofdlc` / `dlcappid`. A wrong or poisoned answer can therefore put
another app into the delete set, and members of the set do not protect
each other's depots.

Bounds: opt-in per request; the request must name exactly the members the
confirmation dialog listed (from `GET /v1/cache/{appid}/delete-plan`;
`409` with both lists on any difference); only apps whose OWN type (store
or own PICS type, not one inferred from a parent's list) is `dlc`,
`config`, `music`, `video` or `demo` join; fixed-list tool apps never join;
any app outside the set still protects its depots under the ADR-0003
remnant rule. No new oracle request is made for this (ADR-0022 Q8 B: the
update check still asks only about cached apps). `api/vault_api/oracle.py`
states the exception in its safety-invariant docstring.

## Note 2026-10-14 — the scheduled check no longer needs a window (WP UPD-5)

User decision 2026-10-14 (morning, `docs/handover/post-0.1.0-roadmap.md`).
Supersedes the window gate in points 1 and 3 of the 2026-10-13 addendum
"always-on update check" (not the grouped-delete one)
(the orchestrator's implementation of "only prod checks"), and point 2's
rationale sentence "the dev instance has no schedule window configured,
so it never checks on its own":

- The scheduled update check runs every
  `VAULT_SCHEDULE_INTERVAL_MINUTES` **whenever the oracle is on**, with a
  schedule window open, closed or not configured at all. The oracle switch
  (`VAULT_MANIFEST_ORACLE=off`) is the only way to stop it.
- Automatic downloads and prefill sweeps stay strictly bound to the
  window, unchanged: without a window, no automatic download starts.
- The dev instance, which has no window, therefore also checks in the
  background now. That sends only anonymous metadata (the app ids of
  cached games) to the oracle; the user approved this.
- `GET /v1/updates/status` keeps its `scheduled` field; it now means
  "the oracle is on" and equals `enabled`.
- Point 4 applies without its window condition: with the oracle on, the
  background polling happens on every install.

## Addendum 2026-10-14 — cache completeness against the current build (WP CACHE-COMPLETE-1a)

"Cached" meant `size_bytes > 0`, so a game read as cached seconds after a
download started (user report, Timberborn). Decided (user: both stages;
orchestrator: the review-round rules below):

1. **Target manifest per (app, depot):** the oracle's current `public` gid;
   vault-api's own `depot_manifests` record instead when it is newer than the
   oracle's last answer for the app, or when the oracle is off or has no row
   for the depot. Never an older or different manifest id than the target.
2. **Chunk-level formula, compressed on both sides:** expected = sum of the
   target manifest's `cb_compressed` over its unique chunks; present = the
   same sum over chunks whose file `depot/<id>/chunk/<chunkid>` exists with
   exactly that size. Chunk list from the manifest archive, else a
   cache-stored client copy of the same id (GC's source chain). Rejected:
   depot directory bytes vs manifest size, because an older build's
   leftover chunks push it past 100 % during exactly the update it should
   reveal.
3. **Counted depots:** `depot_app_map` (what SteamPrefill filled, so its own
   OS/language/DLC selection), never the oracle's full depot list.
4. **Unknown is `null`**, and one unknown depot makes the app unknown; a
   `null` with bytes on disk is never shown as complete.
5. **Groups (D9):** only members with bytes on disk count, each
   depot/manifest once.
6. **Freshness:** measured by every update check (rows outside the
   selection are dropped), after each successful prefill of the app, and
   dropped by `DELETE /v1/cache/{appid}`; otherwise a figure is as old as
   `cache_complete_checked_at` (up to one check interval). Stored in
   `cache_completeness` (schema v25). Details: api/README.md "Cache
   completeness", `api/vault_api/cache_completeness.py`.

## Addendum 2026-10-14 — verify and complete (WP CACHE-COMPLETE-1b)

Decided: "Verify", "Complete now" and "Complete all" are a **forced
prefill** (`needs_force = 1`, then the normal enqueue). SteamPrefill has no
verify command and a non-forced run skips an app its own bookkeeping calls
current, i.e. exactly the incomplete ones. A forced run requests the current
build's full chunk list (as any run that downloads does); vault-core serves
present chunks from disk, so only missing chunks are fetched from Steam.
Rejected: `?nocache=1`/`--nocache` (full re-download, or manifests only).

1. Automatic completion runs only inside the schedule window; without a
   window it is manual only. In the window it covers every incomplete cached
   game with missing chunk files, sweep target or not, also with
   `VAULT_SWEEP_INCLUDE_CACHED` off (user decision 2026-10-14).
2. Chunk files of the wrong size are not replaced by a forced run (served as
   HITs). Such a game is skipped by "Complete all" and by the automatic
   completion and has no repair action yet; the fix (vault-api removes the
   mismatched files, then a forced run) is CACHE-REPAIR-1, after rc17 (user
   decision 2026-10-14).
3. No schema change. Details: api/README.md "Verify and complete".
