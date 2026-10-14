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

## Note 2026-10-14 — the scheduled check no longer needs a window (WP UPD-5)

User decision 2026-10-14 (morning, `docs/handover/post-0.1.0-roadmap.md`).
Supersedes the window gate in points 1 and 3 of the 2026-10-13 addendum
(the orchestrator's implementation of "only prod checks"):

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
