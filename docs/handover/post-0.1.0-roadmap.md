# Handover: post-v0.1.0 roadmap (items D1-D16)

Written 2026-10-02 by the orchestrator session that ran the first
production rollout. For whoever picks up the work after `v0.1.0`. Read
`CLAUDE.md`, `docs/PROJECT_PLAN.md` (§11 item 13) and `docs/LEARNINGS.md`
first. The working agreement does not change: orchestrator plans, coder
implements, reviewer reviews every package, one commit per passed package.

## Ground rules for this track

- **Timing.** Code for D1-D3 merges to `main` only after the `v0.1.0`
  tag. The api/core freeze (ADR-0016) holds until then, and anything
  merged before the tag ships in the release. You can draft designs and
  ADRs on their own branches now.
- **Parallel work.** Use a separate git worktree, e.g. `git worktree add
  ../steamhangar-next <branch>`. The repo's dev tooling may assume the
  main checkout path. In that case run api tests (pytest in the uv venv)
  and web tests (`node --test "web/tests/*.test.js"`) directly in the
  worktree, and run stack verification only from the main checkout. Keep
  to about three agents at a time.
- **ADR first.** Each of D1-D3 changes architecture or a security
  decision. Write the ADR, have it reviewed, and get the user's decision
  on its open questions before any code.
- **Language.** Communicate with the user in German. The repo, code and
  commits are in English.

## Context: what the rollout taught us (2026-10-01/02)

- **Network.** The operator's internet line is DS-Lite: IPv4 runs through
  the provider's CGNAT (AFTR 192.0.0.1). When the per-subscriber port
  quota runs out, the CGN answers with ICMP host-unreachable (RFC 6888
  REQ-11). In nginx that shows up as `connect() failed (113: Host is
  unreachable)`. A prefill sent about 62k chunk requests in about 2
  minutes, and 94% failed. Single requests always work. From a container
  on the same host, 50 parallel new connections work, while 200 fail
  about half the time. Many residential lines in Germany are DS-Lite, so
  treat this as the normal case, not an edge case.
- **vault-core opens one upstream TCP connection per chunk.** The cause is
  `proxy_pass http://$vault_upstream_host$request_uri` with a resolver:
  an implicit upstream has no keepalive, even on nginx 1.29.7+ where
  explicit upstreams default to `keepalive 32 local`. lancache does the
  same (two nginx tiers, variable `proxy_pass`, no pool).
- **SteamPrefill 3.7.1** runs 30 concurrent requests by default
  (`Models/DownloadArguments.cs`). On failure it retries up to 2 more
  rounds right away, with `?nocache=1`. The hidden flag `--max-threads N`
  (`Program.cs`, since v2.8.1) caps the concurrency. WP CORE-FIX-2 uses
  it before `v0.1.0`; this is stage 1 of the fix, and D1 is stage 2.
- **SteamPrefill has no QR login.** This is verified in the v3.7.1 and
  v3.7.2 source. Login uses `BeginAuthSessionViaCredentialsAsync` (account
  name plus password typed on the console), followed by Steam Guard
  approval in the app or by code. The session lives in
  `Config/account.config` next to the binary: protobuf-net
  `UserAccountStore`, field 3 `CurrentUsername`, field 4 `SessionId`,
  field 5 `AccessToken`, which actually holds the refresh token. It is
  valid for about 200 days. ADR-0004 claimed otherwise; DOCS-FIX-3 (item
  13 B, 2026-10-03) corrected it in ADR-0004 addendum 4.
- **Steam's anti-phishing location check** blocked an app approval when
  the approving phone was in another country than the server ("Steam has blocked
  this sign in"). Entering the 5-character Steam Guard code is reported
  to bypass the check (community threads, no Valve documentation).
  Approving after routing the phone through a VPN exit at home
  worked. Any QR flow inherits this limit.
- **The cache does not depend on the account.** Chunks are stored and
  served by depot path (`cache/depot/<id>/chunk/<hash>`, `try_files
  $uri`). Ownership is checked earlier, when Steam hands out manifest
  request codes. Once a chunk is cached, every client in the house gets a
  HIT.

Sources and full research notes are in the session's research reports,
summarised above: the SteamPrefill source at tags v3.7.1/v3.7.2,
SteamKit2 3.4.0 `Authentication/*` and sample 001, lancachenet/monolithic
HEAD b9213a9, the nginx 1.29.8 source (`ngx_http_upstream.c` ~738-749,
the keepalive module) and RFC 6333/6888/5382. Re-verify anything you
build on; the pins may have moved.

## D1: CORE-FEAT-1, upstream keepalive to the Steam CDN

**Goal.** Reuse upstream connections, so that a prefill or a client
download opens about as many TCP connections as there are workers,
instead of one per chunk. This removes the CGNAT ceiling for good. It
also helps Steam clients, whose concurrency we do not control.

**Mechanism (verified in the nginx source).** With a variable
`proxy_pass`, nginx first looks the evaluated host name up among the
defined `upstream` groups (host and port match; no port in the URL means
port 0). It falls back to the resolver only if no group matches. Since
1.27.3, OSS nginx supports `server <name> resolve;` inside an `upstream`
that has a `zone`. Together with `keepalive`, this gives a pooled,
re-resolving group per edge host:

```nginx
upstream cache9-ams1.steamcontent.com {
    zone steam_edges 256k;
    server cache9-ams1.steamcontent.com resolve;
    keepalive 16;
}
```

The existing `proxy_pass` line, the Host header, the path-faithful store
path and the 508 loop guard stay unchanged. Unknown hosts take today's
resolver path, so a missing entry costs performance but never breaks
anything. Keepalive also needs `proxy_http_version 1.1` (already set) and
an empty `Connection` header toward the upstream; check that the loop-guard
hop header still goes out.

**Open design questions, for the ADR and the user:**
1. **Where the edge list comes from.**
   - Static seed list rendered by the entrypoint (env or file).
   - Learned from the access log's `$host` histogram, plus a reload.
   - A union of both.

   Steam's CDN host set varies by region and over time (`cacheN-<pop>`,
   plus ISP-hosted "CDN" servers that may return 403 for some depots).
2. **Keepalive size and timeouts per edge.** Mind the CGNAT quota: total
   pooled connections must stay well below it.
3. **Reload strategy** when the list changes (`nginx -s reload` from
   inside the container, or restart).
4. **Interaction with `VAULT_UPSTREAM_RATE`** (ADR-0015): `limit_rate`
   per connection versus pooled connections.

**Prove before building.** On production, with a handful of sequential
requests and no burst, check whether Valve edges keep a connection alive
across requests (count the TCP handshakes, or check
`$upstream_connect_time` going to 0 on reused connections). The project's
test instance has no internet access by design.

**Tests.** `nginx -t` on the rendered config; verify-stack: a fake edge
upstream that counts accepted connections, N chunk requests must cost
fewer than N connections; the unknown-host fallback still works; the loop
guard still fires.

## D2: AUTH-FEAT-1, Steam login by QR code from the web UI

**Goal.** No terminal, no password: the web UI shows a QR code, the user
approves it in the Steam app, and the session lands where SteamPrefill
reads it.

**Design sketch (inference, to be confirmed in the ADR):**
1. **Helper.** A small helper (C#, about 100 lines, SteamKit2 pinned to the
   version SteamPrefill uses) runs in the container that holds
   SteamPrefill's `Config/` (vault-runner in queue mode). It calls
   `BeginAuthSessionViaQRAsync(new AuthSessionDetails { IsPersistentSession
   = true, DeviceFriendlyName = ... })`. On stdout it prints JSON lines with
   the rotating `ChallengeURL` (via `ChallengeURLChanged`) and a status,
   and it polls `PollingWaitForResultAsync()`.
2. **Session file.** On success the helper writes `Config/account.config`
   in SteamPrefill's protobuf format itself: account name, a random
   session id, and the refresh token in field 5. It prints only "ok" (and
   maybe the account name), never the token.
3. **vault-api.** Two endpoints behind the API key: start (returns a
   short-lived login id) and status/URL (polled by the web UI, which
   renders the QR client-side). Add a rate limit, a timeout, cancellation,
   and allow only one login at a time.
4. **Fallback.** The terminal login with a Steam Guard code stays
   documented. It is the escape hatch for the location check.
5. **Session status in Settings.** Show "present / missing / expired"
   (the JWT `exp` of the stored token, read by the runner and never
   returned), plus the copyable terminal command.

**Security notes for the ADR (amends ADR-0004):**
- No code path accepts, forwards or stores a Steam password.
- The refresh token never crosses vault-api's HTTP layer, logs or DB.
- It does still sit on a filesystem the vault-api uid can read. That is
  true today as well; real isolation would need a separate uid or
  container.
- A challenge URL is not a credential, but it does carry authority:
  whoever approves it logs their account into the server. That is why it
  must sit behind the API key and be short-lived.
- The token has full Steam-client scope for about 200 days. This is the
  same exposure as today; say so in the threat model.

**Risks.** We depend on a private serialization format and on a SteamKit2
version. Pin both, and add a CI test that a SteamPrefill binary accepts a
helper-written file (offline parse, or a dry run against a mock if
possible). Upstream is unlikely to take a QR patch: the maintainer
declined multi-account (#113), credential storage (#303) and
non-interactive select-apps (#427).

## D3: MULTI-1, several Steam accounts per household

**Goal.** Each household member's games can be prefilled with their own
session, and the library shows everyone's games.

**Also (user, 2026-10-13):** in multi-client and multi-account setups every
game needs a visible note on which device or account it belongs to.

**Facts.**
- SteamPrefill supports exactly one session per install: `Config/` is
  hard-wired to `AppContext.BaseDirectory/Config`, and there is no
  `--user` or config-path flag. The FAQ and maintainer say to run one
  install per account.
- A symlinked binary probably resolves to the real BaseDirectory; test
  this.
- The library setting is one SteamID today (`steam_library_steamid`,
  API-FEAT-1).

**Design sketch.**
- One directory per account (a copy of the binary dir, or a per-account
  bind mount over `Config/`).
- A job carries the account; the runner picks the session that owns the
  app (from each account's owned-games list via the relay).
- The library setting becomes a list of SteamIDs. The web/Android library
  shows the union, with an owner hint per game.
- QR login (D2) per account.
- The privacy stance (PROJECT_PLAN §7 Phase 4h: no judgemental per-person
  numbers in a shared living room) applies to owner hints.
- Steam Families sharing is deliberately not pursued (user decision
  2026-10-02).

**Depends on D2** for a usable login per account.

## D4: IPv6 egress (operator network, optional)

Steam CDN hosts publish AAAA records, and IPv6 bypasses the CGNAT
entirely. The operator's host runs without global IPv6 on purpose, so
this is a network decision for the operator's documentation and process,
not a code change. If it ever happens, vault-core would need
`resolver ... ipv6=on` and IPv6-capable Docker networks; check the
egress-lock networks, which set `enable_ipv6: false` on purpose.

## D5: named, scoped API keys and per-target payload scoping

Already specified in PROJECT_PLAN §7 Phase 6. No new findings.

## D6: CLIENT-1, a proper desktop client

**Goal (user, 2026-10-03).** A client that installs like normal software
and runs without hand-made scheduled tasks or timers.

**Today.** The Go agent is a one-shot CLI. On Windows a PowerShell
installer registers a Scheduled Task (Interactive logon type, repetition
plus a logon trigger since AGENT-FEAT-1). On Linux it is a systemd user
timer. Configuration lives in environment variables and flags, and the
API key is typed in by hand.

**Sketch:**
- **Installer.** An MSI (WiX) or Inno Setup package with an Apps & Features
  entry and a clean uninstall; later a winget manifest.
- **Background service.** It watches each Steam library's `steamapps/`
  for appmanifest changes (ReadDirectoryChangesW on Windows, inotify on
  Linux) and reports on change, debounced. A small heartbeat carries
  presence, so presence no longer depends on the full report interval.
- **Tray app (optional).** Shows the connection status, the last report
  and the version, with "Open Hangar", settings and a connection test.
- **Pairing.** The web UI's "PCs" list offers "Add PC" with a short code
  or QR. The client exchanges it for its own revocable key (D5), so no
  shared API key gets copied around.
- **Updates.** The client compares its version with `GET /v1/about` and
  offers the matching release.
- **Code signing.** Without a certificate, SmartScreen warns. Options are
  a paid OV/EV certificate, signing via the Microsoft Store or winget, or
  accepting the warning. This is a cost decision for the user.
- **Linux/SteamOS.** Flatpak, .deb or AUR, with a systemd user service
  instead of a timer.

**Open decision.** Service plus tray, or tray-only:
- A service runs without a logged-on user, can read every user's Steam
  libraries, and needs admin to install.
- A tray-only client runs per user and needs no admin, but reports only
  while someone is logged on.

**Depends on.** D5 (per-PC keys) for pairing. The presence fields from
AGENT-FEAT-1 (`agent_version`, `report_interval_seconds`) stay the wire
contract.

## D7: CORE-FIX-4, cap concurrent upstream connections

**Why (measured 2026-10-04, first Steam-client test on rc9).** Steam on a
gaming PC updated two apps that had never been prefilled. In about 16
seconds vault-core logged 1838 `connect() failed (113: Host is
unreachable)` across 14 edge hosts; 898 of them went to
`cache9-ams1`, which IS in the keepalive pool. The pool caps idle
connections (8 per group, 4 groups), not in-flight ones, and the Steam
client's concurrency is not ours to cap, so every request above the idle
count opens and closes a fresh connection and a CGNAT port mapping. Steam
retried and finished, but the whole household briefly loses new IPv4
connections. Prefill is unaffected (`--max-threads 8`).

**Decision (user, 2026-10-04, Weg A, superseded the same day).** First
decided: ship `v0.1.0` with this as a known limitation and fix it after
the tag. **Superseding decision (user, 2026-10-04): fix it BEFORE
`v0.1.0`**, as ADR-0021 (one pooled upstream plus a global connection cap),
in the work packages CORE-FIX-4a..4d. The scheduler mitigation (installed
apps prefilled, window covering the day) stays in the release notes until
the fix is in a tagged build.

**Open.** What nginx OSS can do without the Plus-only `queue`
(`max_conns` alone should fail fast with 502 per the nginx docs, to verify; client-side `limit_conn`; a
separate pooled forward proxy); whether a per-edge-name pool is the right
model when a client spreads over 14 edges; how to measure safely on the
production line.

## D8: SCHED-FEAT-1, weekday schedules and several windows per day

User request 2026-10-04: a nightly-only check misses updates published
during the day. `schedule_window` is a single `HH:MM-HH:MM` range today;
allow a list. Workaround: one wide window such as `06:00-24:00` with a
60-minute interval.

**Scope extended (user, 2026-10-04).** Weekdays and several different
windows per day, e.g. Mon-Fri 12:00-16:00 plus 22:00-06:00 and the weekend
all day. Open design points: the storage format of the schedule (a list
of `{days, from, to}` entries, validated like today's window), how the
Settings UI edits it on web and Android, and keeping vault-core's
download-rate cap window (`VAULT_UPSTREAM_RATE_WINDOW`, env-only today) in
step with it.

## D9: TOOL-APPS-2, bare "APP 25062xx" cards and dynamic tool-package grading

Observed on the rc14 production library: many bare "APP 25062xx" cards,
mostly Helldivers 2 DLC/content packages. Proposed, not yet reviewed and
not decided: a filter for them, grouping them under the parent game, and
classifying tool packages dynamically instead of the fixed list in
`tool_apps.py`. Needs an ADR and the user's go before any code. Decision 2026-10-13:
group under the parent game (not just filter). Draft: ADR-0022 on
`wp/d9-adr-tool-apps`. User decisions 2026-10-13: parent source = Steam
store lookup first, oracle only for gaps while it is on (Q1 A); display =
"+N content packages" on the card, list in the detail sheet (Q4 A);
delete of a grouped game also deletes its packages' own depots when they
would be orphaned, shared depots still kept (Q6, user variant); prefill
unchanged until measured on prod (Q5 A). Flat list with `parent_appid`
from the server, UIs group (Q2 A, orchestrator). Open: Q3 failed package
on the card — decided 2026-10-13 (user, Q3 B variant): a failed package
shows a small warning on the group card, the "Failed" chip counts such
groups, and the detail sheet offers a retry per package. P1 step 3 measured
2026-10-13 (diag): the store GetItems answers 553850 but not the
packages (success 15) nor 228980; the oracle names them (2506210 /
2514120: type DLC, parent 553850, no own depots; 228980: type Tool), and
HD2's own PICS lists all 15 packages (`extended.listofdlc`, depot
`dlcappid`). User decisions 2026-10-13: Q8 B (no extra oracle requests,
packages come from the cached parent's answer); Q7 A (dynamic tool
classification for display only, the fixed list keeps stopping
prefills).

## D10: COVER-CACHE-1, slow cover loading

Covers load slowly. Proposed, not decided: vault-api stores the images
and serves them with an ETag and a long `Cache-Control`; the Android app
gets its own Coil disk cache (decision 2026-10-13: both).

Step 1 (2026-10-13): vault-api only resolves `cover_url`; web and app
load the image straight from `shared.akamai.steamstatic.com` /
`cdn.akamai.steamstatic.com`, which the egress lock does not allow for
vault-api. Coil 2.7 already has a default disk cache. User decision
2026-10-13: option A (allow the two steamstatic hosts, vault-api stores
and serves covers), but measure first what is actually slow.
Measured 2026-10-13 (diag, ZEUS host): one cover takes 0.2-0.8 s from
the CDN (50-175 KB), `Cache-Control: max-age` about 10 years plus an
ETag, vault-dns resolves the hosts normally. The CDN is not slow; the
gain is LAN speed on first loads and offline covers. User: build
option A anyway. User decisions 2026-10-13 (after the build): yes to
the egress change (vault-proxy bakes in exactly shared.akamai.steamstatic.com
and cdn.akamai.steamstatic.com); signed cover URLs
`/v1/covers/{appid}/{signature}` are accepted as a documented exception
to plan section 9 (`<img>` cannot send the API key).

## D11: APP-FIX-LOGIN, Steam login in the app fails with "Invalid return protocol"

Cause: `steamvault://auth/openid-return` is used as the return URL, and
Steam only accepts http(s) (`SteamOpenIdLoginUrl.kt:44-48`). Three
options are open: a detour through vault-api, typing the SteamID64 by
hand, or QR login (D2). The user decides.

## D12: APP-FIX-LAYOUT, Android library layout

`FilterChipsRow` (`LibraryScreen.kt:406`) does not scroll: "Downloading"
wraps into a column of letters and "Failed" falls off. The view toggle
(`LibraryScreen.kt:387`) wraps. The cause of the empty area and the tall
header is unknown and needs investigation first.

## D13: SPEED-1, download speed display

User request 2026-10-13. No package exists today (checked PROJECT_PLAN,
WORKPACKAGES, this roadmap). Proposed, not decided: show the current
throughput of a running job in Downloads (web and Android), derived from
vault-core's event log or the job's byte counter; open point is whether it
is per job or per client, and how it relates to the upstream rate cap.

## D14: UPD-1..4, always-on update check (user decision 2026-10-13)

Check cached games for updates independent of the schedule; only an
open schedule window also starts the download. Cached games only; only
production checks (the dev instance stays offline). Badge "Update
available" plus a "Check updates" button and filter chip on web and
Android. Supersedes the "Check & update, never bare Check" rule of
2026-08-18 (ADR addendum in UPD-4).

## D15: CLIENTS-1, Clients area plus server-side speed and history age filter

User request 2026-10-13 (answers the same day). A "Clients" nav entry
(own page, not a sheet; diverges from the WP 4a.1 "sheet" ruling, user
veto welcome) under Library. Per client: connected or not, what is
installed, what is loading now, what was loaded recently. Clients
without an agent (e.g. the WLAN client "frame") appear from the cache
traffic alone: connected, bytes loaded, last active; "installed" only
for agent clients, an agent can be added later (Windows and Linux/SteamOS
agents exist). Data source (found 2026-10-13 in D15a): the opt-in
event log (`VAULT_EVENT_LOG`, `$remote_addr` since WP 3.10), which
`event_sweep.py` already aggregates per address into
`client_cache_stats`; D15b builds on that and must check or document
that `VAULT_EVENT_LOG` is set. D15a (`client=` on the stdout access log)
is operator diagnostics only, not a D15b prerequisite. Includes
D13 SPEED-1: throughput measured once at the server (vault-core), shown
per running job. Downloads history gets a time filter: 24 h, 7 days,
30 days, all. Downloads header (user, 2026-10-13): current and average
speed, GB loaded, graphs, hit rate, GB saved from the cache, peak speed,
active clients. PC add/manage moves from Settings into the Clients page.
Order: after UPD-1..4.

## D16: SETTINGS-1, fewer env values, an expert mode, a calmer Settings page

User request and decisions 2026-10-13. Keep as few values as possible
fixed in the env; move them into Settings behind a collapsible expert
mode. Candidates (all four chosen): upstream rate cap and its window,
TLS and upstream caps, prefill threads, GC and retention timings. Risky
values get a confirmation dialog and the same validation grammar as the
env today. Core path (chosen): vault-api writes the values to a file on
a shared volume, vault-core renders its nginx includes from it
(`26-`, `27-`, `28-vault-*.sh` today read env at boot) and reloads with
`nginx -s reload`, no restart. This needs a compose volume change, which
is an Antrag to the user, never a self-service edit. Also audit the
Settings page structure (web/js/views/settings.js, 1466 lines; 9
override keys in settings_store.py vs 47 env vars): collapsible sections,
tooltips instead of text walls. Order: Wave 2, before D15.

## Wave plan (user-approved 2026-10-13)

Supersedes the order below for D9-D16. Each package: coder -> reviewer ->
one commit; review weight by package type (`docs/LEARNINGS.md`).

**Wave 1 (rc15)**
1. **UPD-1 (api)** always-on update check. Implemented on
   `wp/upd-1-update-check` (3619a71), WIP, NOT reviewed. Next: reviewer,
   then fix, commit, CI, merge. Open user decision: the check only
   refreshes the manifest oracle, which is off by default
   (`VAULT_MANIFEST_ORACLE`, ADR-0006 decision 4), so in prod it does
   nothing until the oracle is on. Decided 2026-10-13 (user, option A):
   the oracle defaults to on in code; the ADR-0006 addendum lands in
   UPD-4, no prod .env change. A manual "Check updates" on the dev
   instance may query the oracle (metadata only, user decision
   2026-10-13); dev.env stays unchanged. Egress (ADR-0011): when the
   oracle is on, its URL host is allowed automatically by vault-proxy and
   the vault-api startup check, without a `VAULT_EGRESS_ALLOW` entry
   (user decision 2026-10-13, option A; addendum to ADR-0011 in UPD-4).
   The automatic allowance covers only the default host api.steamcmd.net;
   a custom oracle URL host still needs a `VAULT_EGRESS_ALLOW` entry
   (orchestrator narrowing, confirmed by the user 2026-10-13).
2. **UPD-2 (web)** "Check updates" button (calls `POST
   /v1/updates/check`, no download), "Update available" badge from
   `update_available`, an "Updates" filter chip. The existing "Check &
   update all cached games" button stays.
3. **UPD-3 (app)** the same on Android.
4. **UPD-4 (docs)** ADR addendum: superseding the 2026-08-18 "never bare
   Check" rule, the oracle default, "only prod checks, cached games only".
5. **APP-TEST-1 (app, CI)** Roborazzi + Robolectric screenshot tests for
   the library screen; profiles 411 dp (Pixel reference, API 35) and
   360 dp, font scale 1.0 and 1.3, dark mode. Runs only in GitHub CI
   (`android-tests` job); golden images recorded once in CI, then checked
   in. No emulator in the devbox (no /dev/kvm). Golden refresh is
   automated (user decision 2026-10-13, option B): pushing a branch
   `wp/*-goldens` makes CI record the screenshots and commit them back
   to that branch with its own GITHUB_TOKEN; no token in the devbox,
   main is never written by CI.
6. **D12 APP-FIX-LAYOUT (app)** red screenshot test first, then fix:
   FilterChipsRow scrolls, the layout toggle does not wrap, find the cause
   of the empty area and the tall header.
7. **D10 COVER-CACHE-1 (api + app)** vault-api stores cover images and
   serves them with ETag + long Cache-Control; Coil disk cache on Android.

**Release cut (user decision 2026-10-13):** rc15 = wave 1; rc16 = D11 +
D9; rc17 = D15 + D16 (both rework Settings and both wait on Anträge:
`VAULT_EVENT_LOG` on prod, settings volume).

**Wave 2 (rc16/rc17)** — runs in parallel where it does not touch wave 1
(user decision 2026-10-13): D9 ADR, D15a, the D16c Antrag once its
design exists. Wave-2 code stays on branches until rc15 is tagged.
8. **D16 SETTINGS-1** split into: (a) settings audit, collapsible
   sections, tooltips instead of text walls (web, then app); (b) expert
   mode API: rate cap + window, TLS/upstream caps, prefill threads,
   GC/retention, confirm dialog for risky values, env grammar reused;
   (c) core path: settings file on a shared volume, core renders its
   includes and runs `nginx -s reload` (needs an Antrag for the compose
   volume, dev and prod).
9. **D15 CLIENTS-1** split into: (a) client address in the vault-core
   log format; (b) API: clients incl. agentless ones (e.g. "frame"),
   server-side throughput, history; (c) web Clients nav page (connected,
   installed for agent clients, loading now, loaded recently; PC
   add/manage moves here from Settings) and Downloads header (current,
   average and peak speed, GB loaded, graphs, hit rate, GB saved, active
   clients) plus history filter 24 h / 7 d / 30 d / all; (d) the same on
   Android.
10. **D11 APP-FIX-LOGIN** OpenID return via an https page on vault-api
    that deep-links into the app (decided; does not conflict with D2).
11. **D9 TOOL-APPS-2** ADR first, then group DLC/content under the
    parent game (decided).

**Before v0.1.0 (ongoing):** release notes, user device checks,
verify-stack 10 h flake, Android follow-ups (PC remove twin, onboarding
saves id), D7 measurement on `:443` (TLS cap TODO in WORKPACKAGES),
failed jobs Subnautica/Timberborn (measure live on the next run).

**After v0.1.0:** D8 (incl. rate window in Settings, may fold into D16),
D1, D3 (incl. device/account note per game), D2, D4, D5, D6.

## Suggested order

1. DOCS-FIX-3 lands before `v0.1.0` (it is in item 13 B).
2. Write the ADR drafts for D1, D2 and D3, review them, and settle the
   open questions with the user.
3. D1 first: it is the widest benefit, and the production measurement is
   cheap.
4. D2, then D3 on top of it.
5. D7 BEFORE the `v0.1.0` tag (user decision 2026-10-04, superseding the
   earlier "after the tag"; ADR-0021, CORE-FIX-4a..4d); D8 after the tag.
   D7 is the only item with a measured harm on the production line.
