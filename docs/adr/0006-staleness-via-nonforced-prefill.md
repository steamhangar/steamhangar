# ADR-0006: Staleness detection — non-forced prefill as the primary oracle

Date: 2026-08-06
Status: Accepted (orchestrator decision from evidence; see docs/research/phase3-manifests.md).
Decision 4's "opt-in, default off" superseded by the addendum 2026-10-13 below.

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
   control that only checks. From now on an update check that downloads
   nothing runs always: on vault-api's schedule tick independent of the
   schedule window, and on demand (`POST /v1/updates/check`, WP UPD-1;
   asynchronous: it answers `202` and progress is read from
   `GET /v1/updates/status`).
   Downloads still start only inside an open schedule window, or from the
   existing explicit "Check & update" control. The check result surfaces as
   `update_available` on the games endpoints and as an "Update available"
   badge, a "Check updates" button and an "Updates" filter chip in web
   (UPD-2) and Android (UPD-3). The pull-to-refresh rule of the same WP
   (a refresh gesture never contacts Steam) is unchanged.
2. **The manifest oracle (`VAULT_MANIFEST_ORACLE`) defaults to on in
   code** (user decision 2026-10-13, option A), superseding decision 4's
   "opt-in" and "default off": the oracle is now opt-out. The update
   check works only through the oracle, so with the old default it would
   do nothing in prod. Rationale: only prod has
   network access for this; the dev instance keeps its scheduler off, so it
   never checks on its own. Opt-out stays possible:
   `VAULT_MANIFEST_ORACLE=off` disables the oracle; unset or empty means
   the default, on (on = `steamcmd_api`) (WP UPD-1). No prod `.env`
   change is part of this decision. The rest of decision 4 holds: optional
   (can be switched off), fail-soft, unaffiliated with Valve, public branch
   only.
3. **Only prod checks, cached games only.** The check asks about games
   that currently have cache content (Steam tool packages excluded); it
   never widens the set to owned-but-uncached apps. Production is the only
   instance meant to check on its own. User decision 2026-10-13: a manual
   "Check updates" on the dev instance MAY query the oracle (app metadata
   only, no game data); the dev env stays unchanged.
4. **Behaviour change on upgrade.** Installs that already had the oracle
   on now also get background polling: every cached app id is sent to
   `api.steamcmd.net` once per `VAULT_SCHEDULE_INTERVAL_MINUTES`. Fresh
   installs, and upgrades that never set the variable, now query this
   third party by default. Opt-out for both: `VAULT_MANIFEST_ORACLE=off`.
