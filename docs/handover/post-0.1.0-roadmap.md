# Handover: post-v0.1.0 roadmap (items D1-D29)

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

## Release plan (user decisions 2026-10-16)

Supersedes "Suggested order" and the release cuts in the wave plan for
everything after rc19.3. Mirrored as version blocks in
`docs/PROJECT_PLAN.md` §11 item 13; when scope moves, update both in the
same commit (roadmap rule in `CLAUDE.md`). Where a version's packages are
not cut yet, they are cut in that version's planning session (each
package at most ~1-2 h, list shown to the user before delegating).

Default exit criterion for every rc below (called "release gate"): every
package reviewed PASS and merged, `dev.sh test-*` and `dev.sh verify`
green, main CI green (incl. Android), tag with the user's yes, deploy
Antrag filed and the rollout on the operator's stack checked.

### rc20: web redesign

- **Goal:** the web UI gets the approved rc20 layout (`docs/design/rc20/`)
  on a correct live data path.
- **Packages:** LAYOUT-1, SHEET-MOTION-1, CLIENTS-1, CLIENTS-2, CLIENTS-3,
  ADD-DEVICE-1, DOWNLOADS-1 (includes VERIFY-SPEED-TEXT-1 and
  QUEUE-KBD-KEEP), SETTINGS-1, LIBRARY-1, LIBRARY-DETAIL-1; plus
  LIVE-DELAY-1 (done 2026-10-16: Weg A, Downloads defaults to the live range; decided by Jan 2026-10-16), BOOT-RATE-1 (D24),
  LIB-META-WINDOW-1 (D23 note). Cut and ordered in
  `docs/handover/rc20-run-plan.md`.
- **Entry:** rc19.3 tagged (done).
- **Exit:** release gate, plus the operator's browser check of every page
  at 1400 / 1000 / 400 px on the dev instance.
- **rc20.x (optional patch):** new defaults for `VAULT_NGINX_AIO` /
  `VAULT_NGINX_WORKERS` only if the Sunday cold-game test (ADR-0027) says
  so; a default change needs a new ADR with the numbers. Otherwise this
  moves to rc21.

### rc21: Android parity and cache health

- **Goal:** the Android app matches the rc20 web redesign, and the cache
  state the UIs show is honest and repairable.
- **Packages (to be cut in the version's planning session):** Android
  follow-up of the rc20 redesign (Clients cards, Add a device tabs,
  Downloads, Settings section nav, Library toolbar and detail tabs, sheet
  motion where it fits Material); ANDROID-GAPS-1; APP-NIGHT-GOLDEN-1;
  CACHE-REPAIR-1; honesty flag for unmapped depots in the completeness
  display; package names for "Unknown app" entries; optional "Client
  downloading" row on Downloads (needs the user's yes); VERSION-HEALTH-1;
  tuning defaults from the Sunday test if not shipped in rc20.x; runner
  retry on "Unable to detect Lancache server!" only if the heartbeat log
  supports the late-heartbeat hypothesis (ADR-0027).
- **Entry:** rc20 tagged; Sunday test result known (for the tuning item).
- **Exit:** release gate; Android screenshot goldens refreshed, incl. a
  night golden that really renders dark.

### rc22: release hardening (no new features)

- **Goal:** close every measurement and open check that stands between the
  candidates and `v0.1.0` final.
- **Packages (to be cut in the version's planning session):** D7 `:443`
  measurement and the `VAULT_TLS_CLIENT_MAX_CONNS` default;
  CAP-503-LOOP-1; HIT-PATH-1; NITS-AUDIT-1 (except the GameHangar logo
  briefing, which goes to v0.2.0); remaining nits (MAP-DRIFT-1 fail-safe
  tests, claim-vs-move race test, aria-live on queue move, D19WiringTest
  pin, regrow-without-idle-gap test); the open boxes of
  `docs/PROJECT_PLAN.md` §11 item 13 B (signed APK, standard-user check,
  real-device checks, rollout gates (h) and (i), release notes) and the
  failed-jobs measurement (Subnautica, Timberborn, Hogwarts Legacy).
- **Entry:** rc21 tagged.
- **Exit:** release gate; every open item 13 B box ticked or explicitly
  moved by the user; measurements written up (inbox note or ADR addendum).
  Then tag **`v0.1.0` final**.

### v0.1.x: bug fixes only

No features, no schema bump unless a fix needs one. **The public
announcement (WP 5.6, r/selfhosted) is NOT made at 0.1.0**; it waits for
0.2.0, so no public user ever has to migrate through the rename.

### v0.2.0: GameHangar, privacy, schedules, agents

- **Goal:** the product goes public under its final name with a privacy
  mode, real schedules and a proper agent install.
- **Packages (to be cut in the version's planning session):** D18
  rename to GameHangar (user decision 2026-10-16: with 0.2.0; GitHub org
  choice A/B/C in D18 still open), incl. the GameHangar logo and header
  briefing; D22 privacy mode (ADR-0023 draft), then LIBRARY-META-1d store
  night fill on top of it; D8 schedules incl. rate per window; Weg B for
  agents (agent reports its OS, API serves a minimum agent version)
  together with the real installer (the installer part of D6); D28
  CLIENT-NAME-MAC-1; D29 PAIR-2; D5 named, scoped API keys (D29 builds on
  it).
- **Entry:** `v0.1.0` final tagged; D18 org decision taken.
- **Exit:** release gate; migration from 0.1.x (volumes, `applicationId`,
  image namespace) tested; then the public announcement (WP 5.6).

### v0.3.0: Steam accounts

- **Goal:** Steam login without a terminal, and several Steam accounts
  per household.
- **Packages:** D2 QR login, D3 several accounts. Not cut yet.
- **Entry:** the package cut is redone against the current schema and the
  11 open user questions (D2 1-5, D3 1-6) are settled; the ADR drafts
  0018/0019 live on unmerged branches (see the state note under D2).
- **Exit:** release gate, plus a real QR login and a prefill per account
  on the operator's stack.

### v0.4.0 and later: beyond Steam and integrations (order not set)

- **Goal:** more launchers and outside integrations, each when wanted.
- **Items:** D17 multi-launcher (needs D18; user: not before v0.2); D4
  IPv6 and DUAL-STACK-1 follow-ups; D25 TSNET-1; D26 NOTIFY-CURSOR-1; D27
  DEPOT-GRACE-1; Phase 6 external integrations; iOS app.
- **Entry / exit:** set when a version is planned.

### Operator items (independent of versions)

- DUAL-STACK-1 after the line change (AAAA check, then stepwise cap raise
  with measurement).
- Sunday cold-game test per ADR-0027 (feeds rc20.x / rc21).

## Open decisions (waiting for the user)

Kept current per the roadmap rule in `CLAUDE.md`: every Weg A / Weg B
question that is not answered in its session is listed here and removed
once decided (the decision then lives in the item it belongs to).

- **APP-NAMES-1 scope** (rc21, blocks the package): the entry names no name
  source, and the coder found none that is allowed. HD2-style content
  packages have no `apps.name`, the store answers without a name, and
  ADR-0022 Q8 B forbids extra oracle requests for packages. Weg A: label
  unnamed DLC packages "Content package" instead of "Unknown app" (web
  only, touches Android twin pins and the ID-AUDIT single-fallback
  decision). Weg B: harvest depot `name` of depots with a `dlcappid` from
  the cached parent PICS answer (schema bump, needs a P1-style
  measurement first, names may not equal package names). Weg C: send ACF
  names from the agent (agent + report format + Android, not rc21-sized).
  Recommendation: Weg A now, Weg B after a measurement.
- **D18 GitHub org** (v0.2.0): ask GitHub for the inactive `GameHangar`
  name, pick a free org, or keep `steamhangar` and rename product and repo
  only. See D18.
- **D2/D3 questions** (v0.3.0): D2 1-5 and D3 1-6 from the ADR drafts on
  `wp/docs-adr-post-0.1.0-d1-d3`; settle them in the D2/D3 planning session.
- **D6 desktop client**: service plus tray, or tray-only; and code signing
  as a cost decision (paid OV/EV certificate, Store/winget signing, or
  accept the SmartScreen warning). See D6.
- **D17 cache path** (v0.4.0+): Weg A extend vault-core, Weg B a lancache
  sidecar behind vault-core, or a mix; the 2026-10-15 assessment favours
  Weg B or the mix. See D17.
- **Tuning defaults** (rc20.x / rc21): whether `VAULT_NGINX_AIO` /
  `VAULT_NGINX_WORKERS` become defaults; decided by the Sunday test.
- **Runner retry** on "Unable to detect Lancache server!" (rc21): only if
  the heartbeat evidence supports it.

Decided 2026-10-16 (removed from the list above): VERIFY-SPEED-TEXT-1 reads
"Verifying files, no download speed" (DOWNLOADS-1); LIVE-DELAY-1 Weg A
(default range live, `active_clients` in `/v1/throughput/live`); the
"Client downloading" row is in rc21 (CLIENT-ROW-1, after DOWNLOADS-1).

Decided 2026-10-17: Verify, Verifying with animated circular arrows
(VERIFY-LABEL-1, rc21).

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

**State (audit 2026-10-16), applies to D2 and D3.** The ADR drafts 0018
(D2) and 0019 (D3) and a package cut exist only on the unmerged branches
`origin/wp/docs-adr-post-0.1.0-d1-d3` and
`origin/wp/docs-roadmap-d2-d3-packages`; do not merge them as they are. 11
user questions are still open there (D2 1-5, D3 1-6), and the cut assumes
schema v18/v19 while `main` is far beyond that. A D2/D3 session must redo
the cut against the current schema and settle the questions first.

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

**Depends on D2** for a usable login per account. ADR draft and package
cut: see the state note at the end of D2 (redo needed).

## D4: IPv6 egress (operator network, optional)

Steam CDN hosts publish AAAA records, and IPv6 bypasses the CGNAT
entirely. The operator's host runs without global IPv6 on purpose, so
this is a network decision for the operator's documentation and process,
not a code change. If it ever happens, vault-core would need
`resolver ... ipv6=on` and IPv6-capable Docker networks; check the
egress-lock networks, which set `enable_ipv6: false` on purpose.

**Operator follow-up DUAL-STACK-1 (audit 2026-10-16, no decision yet).**
The operator's line is expected to change from DS-Lite to dual stack.
After the switch: (1) verify that AAAA suppression for the Steam CDN names
still holds (clients must keep resolving to the cache); (2) then raise
prefill threads and the upstream caps (D7) step by step, with a
measurement before and after each step. The current caps were justified
by the CGNAT port quota, which a native IPv4 path no longer has.

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

**Measured 2026-10-10 (operator line, client on `:80`):** 0x `113`
errors, upstream connect time ~0 (reused connections), 16-23 upstream
sockets, no SYN floods at the gateway. Still open: a measurement on
`:443` (the passthrough caps) and the default of
`VAULT_TLS_CLIENT_MAX_CONNS`. After a line change see DUAL-STACK-1
under D4; a related open measurement is CAP-503-LOOP-1 (audit list at the
end of this file).

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

**Rate per window (user 2026-10-14 "nachts ungedrosselt tagsüber
gedrosselt", 2026-10-15 "off testen für das nachtfenster"; folded into D8
by the audit 2026-10-16).** D8 also owns the rate question: several
windows per day, each with its own "throttle" checkbox, and a window can
be unthrottled even when the scheduler is off (no automatic downloads;
the window then only lifts the rate cap for manual and client downloads).
Since rc18 there is ONE download window (D16a-1); the deprecated
`VAULT_UPSTREAM_RATE_WINDOW` stays an env pin until D8 replaces it.
vault-core's boot behaviour around the window is D24.

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
groups, and the detail sheet offers a retry per package. Prod library
screenshot (user, 2026-10-13, rc14): packages 2506210..2506280 and
4199890 show as cached (0.3 GB each, CURRENT, not failed), so they do
have depots mapped to their own appid — a grouped delete really frees
them. APP 1716750 (0.2 GB) is not an HD2 package. P1 step 3 measured
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

Implemented on `wp/cover-cache-1` (WP D10, not reviewed yet): vault-api
stores covers in `<data dir>/covers` (schema v21 `app_cover_file`, 512 MiB
cap, background thread) and serves `GET /v1/covers/{appid}/{signature}`
(HMAC of the API key in the path, since `<img>` cannot send the header;
strong ETag, `304`, `private, max-age=31536000, immutable`);
`cover_cached_url` on `/v1/games`; web and app try it first, then the CDN.
The app's `VaultApplication` is Coil's `ImageLoaderFactory` with a sized
disk cache. D10c: the user set vault-proxy's `BAKED_HOSTS` to include the
two steamstatic hosts; tests, verify-stack check and the ADR-0011 addendum
follow it.

## D11: APP-FIX-LOGIN, Steam login in the app fails with "Invalid return protocol"

Cause: `steamvault://auth/openid-return` is used as the return URL, and
Steam only accepts http(s) (`SteamOpenIdLoginUrl.kt:44-48`). Three
options are open: a detour through vault-api, typing the SteamID64 by
hand, or QR login (D2). Decided: the detour through vault-api (wave plan
item 10, shipped as D11).

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
is operator diagnostics only, not a D15b prerequisite. Measured on prod
2026-10-13 22:36 (user, docker exec): `VAULT_EVENT_LOG` and
`VAULT_EVENT_LOG_PATH` are both `/vault/logs/event.log`, so D15b needs no
Antrag. The file is 41 MB and grows unrotated (vault-api cannot truncate
it: nginx-owned 0644); at the default 64 MiB `VAULT_EVENT_LOG_MAX_BYTES`
every sweep will log a WARNING. D15 must add real rotation (vault-core
side) and longer client-stat retention (`VAULT_CLIENT_STATS_KEEP` default
48 = about 4 h) for the history/graphs. Includes
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

## D17: MULTI-LAUNCHER-1, cache more launchers than Steam ("GameHangar")

Roadmap only, **not before v0.2** (user decision 2026-10-15). No work
package exists yet. This section keeps the plan readable months later:
why it came up, what was measured, what the code does today, which
options exist and what should be prepared earlier so the later step stays
cheap.

### Origin and goal

The user's documentation session proposed it on 2026-10-15 after a
Ubisoft Connect download on the LAN ("it is slowly becoming the
GameHangar, not Steam"). Goal: the vault also caches downloads of other
launchers (first Ubisoft Connect, Battle.net, Epic; later possibly
Rockstar, Riot and others), so the second download of the same game on
the LAN is served from the vault, while every Steam function stays as it
is. Done means: a repeat download per launcher shows `cache=HIT` in the
vault-core log, and the Steam tests and `verify` stay green.

Not part of D17: a product rename (own decision, see "Prepare earlier"),
new hardware, library or update-check features for other launchers. The
first step is a transparent cache only; the web UI may show traffic per
launcher, nothing more.

### What was measured (user, 2026-10-15)

Ubisoft Connect, Anno 117 (4.69 GB) on a Windows client:

- The content download is plain **HTTP on port 80**: the `upc` process
  held 8 connections to one CDN address on :80 (PowerShell
  `Get-NetTCPConnection` during the download; the address looked like
  Akamai, unverified).
- The download host `uplaypc-s-ubisoft.cdn.ubi.com` (A record) showed up
  in the LAN resolver's query log, last at the moment the paused download
  resumed. A DNS rewrite to vault-core would therefore reach it.
- Control traffic runs on 443 to several `*.ubi.com` / `*.ubisoft.com`
  hosts. It is not cacheable and does not need to be.
- Throughput straight from the internet: about 14 MB/s.
- Open: for six hours before that, no Ubisoft name appeared in the
  resolver log although part of the download already ran. Cause unknown;
  Wi-Fi was ruled out. Short TTLs or a client-side cache are candidates.
- There is no prefill tool for Ubisoft (as far as known), so its cache
  only fills on the first real client download.

Battle.net and Epic: not measured. lancache's `cache-domains` list has
entries for both (`blizzard`, `epicgames`) and treats them as cacheable
over HTTP. The SteamPrefill author also publishes BattleNetPrefill and
EpicPrefill (versions, license and Epic login need checking). All of
this is from memory, not verified.

### What is Steam-specific today (state rc18.1)

- **vault-core storage model.** A `proxy_store` path mirror (ADR-0001):
  each file sits under its URL path (`/vault/cache/depot/<id>/chunk/...`),
  a hit is served by `try_files` straight from disk, a miss fetches the
  whole file and stores it. On a miss the client's `Range` header is
  stripped (`core/docker/nginx.conf.template`, location `@miss`), because
  Steam chunks are small whole files. There is no cache key, no `slice`,
  and no `max_size`/`inactive` bound; those exist only for nginx's other
  cache type, `proxy_cache`.
- **vault-core routing.** The port-80 server serves `/depot/` only; the
  upstream map and the port-443 SNI passthrough (ADR-0020) accept
  `*.steamcontent.com` only. The upstream pool, the connection cap and
  the rate cap (ADR-0015, ADR-0017, ADR-0021) are tuned for Steam edges.
- **DNS.** The LAN rewrite covers `*.steamcontent.com` only (`dns/`
  README; the operator's resolver config lives outside the repo).
- **vault-api.** Manifests, the update check, library, agent reports,
  miss trigger and GC are all keyed by Steam app and depot ids.
- **GC.** Only ever touches files named by exactly 40 hex characters
  directly in `depot/<id>/chunk/` (`api/vault_api/gc.py`). Files of other
  launchers outside `depot/` are safe from it, but nothing bounds or
  cleans them either.
- **Log and event feed.** The vault log line already carries `host=`, so
  traffic per launcher can be counted later. The event sweep only accepts
  `/depot/<id>/chunk/...` URIs (`api/vault_api/event_sweep.py`) and
  ignores everything else.
- **Prefill.** The runner's egress is a network-enforced allowlist
  (ADR-0011); Steam credentials never enter the vault (ADR-0004).

### Options

- **Weg A, extend vault-core.** One cache path per launcher in
  vault-core, DNS rewrites per launcher, the same rate and connection
  caps for all. Prefill for Battle.net and Epic in the runner later.
  Ubisoft on demand only.
- **Weg B, a lancache sidecar for the other launchers.** Faster to
  build; lancache already knows the domains and the `slice` setup. Cost:
  a second cache to maintain, its own size bound, no shared UI.
- **Mix.** Launchers whose files fit the path mirror (likely Epic) in
  vault-core, the rest through a lancache sidecar.

Assessment from the code check (2026-10-15): Weg A is bigger than it
looks. Battle.net reads byte ranges out of large archive files; lancache
needs `slice` for it. The path mirror cannot store partial responses, so
Weg A would add a second, `proxy_cache`-based cache path to core. That
weighs towards Weg B or the mix. Whichever way: keep vault-core as the
single front door (see below).

### Decisions (user, 2026-10-15)

- **Timing:** roadmap, not before v0.2.
- **One front door:** vault-core stays the only listener on the vault
  address for :80 and :443 and dispatches by Host/SNI; a later lancache
  sidecar sits behind it, never on its own address. The global connection
  and rate caps cover every launcher. Goes into the D17 ADR as a fixed
  premise.
- **Neutral cap names:** done in the rc19 run (D16 labels, see
  `rc19-run-plan.md`).
- **Name:** the product becomes **GameHangar**; the rename is item D18.

### Prepare earlier (cheap now, expensive later)

These do not need D17 to start; they keep the door open.

1. **One front door.** Decide (ADR) that vault-core stays the only
   listener on the vault address for :80 and :443 and dispatches by Host
   or SNI, also if a lancache sidecar comes later. A second cache with
   its own address would bypass the global connection and rate caps
   (ADR-0021), which exist because of the CGNAT line. If the user
   prefers a separate address instead, reserve it in the network plan
   early (request to the operator).
2. **Neutral names in D16.** The upstream rate and connection caps are
   moving into Settings right now (D16). Label them as the vault's WAN
   budget ("Upstream", not "Steam"), so later launchers share the same
   knobs without a rename. Steam-only things stay in the Steam section.
3. **Cache namespace rule.** Write down now: content of other launchers
   never goes under `depot/`; it gets sibling trees
   (`/vault/cache/<launcher>/...`). GC and the event sweep then need no
   change to stay safe. Size reporting must not assume `depot/` is the
   whole cache.
4. **Name before public release.** Decided: GameHangar (D18). It must
   land before the public announcement (v0.2.0, see Release plan), not
   after.
   Component names are already neutral (`vault-core`, `vault-api`, ...).
   What a rename touches: GitHub org and repo, `ghcr.io/steamhangar/*`,
   the Compose project name (it prefixes volume names; volumes cannot be
   renamed, see ADR-0013 for the copy procedure), the Android
   `applicationId` (still `dev.steamvault.app`; it cannot change once
   users install from a store), the Windows agent's service name and the
   docs. The public domain can stay.
5. **HTTPS-only launchers are out.** Caching works only for plain-HTTP
   content downloads. TLS interception is a permanent non-goal. Every
   new launcher gets a short measurement first (ports, DNS names, Range
   headers); EA app and GOG are expected to fail this test, Rockstar and
   Riot are listed in lancache's `cache-domains` (unverified).
6. **No provider column yet.** A `provider` dimension in the database is
   an additive migration (default `steam`) when it is needed. Do not
   build it ahead of time.

### Before the ADR (checklist)

- Measure per launcher during a real download: ports (80 vs. 443), DNS
  names from the resolver log, and whether the client sends `Range`
  (needs the HTTP headers, not just the connection list).
- Settle Weg A / Weg B / mix (user).
- Size bound for non-Steam content (dataset quota is shared with Steam).
- Runner egress hosts for prefill; for EpicPrefill the login question,
  decided like ADR-0004 decided it for Steam.
- New phase in `docs/PROJECT_PLAN.md` (multi-service is out of scope for
  v1), ADR, and requests to the operator for DNS rewrites and the SNI
  passthrough list (network changes are never self-service).
- Rename D18 done or scheduled (item 4 above).

## D18: RENAME-2, SteamHangar -> GameHangar

User decision 2026-10-15: the product is renamed to GameHangar, because
D17 makes it more than a Steam cache. Timing (user decision 2026-10-16):
with v0.2.0, before the public announcement (see "Release plan"); it
does not depend on D17 being built. Model: ADR-0013 (SteamVault ->
SteamHangar), which lists what a rename touches and how volumes are
carried over.

What it touches (inventory 2026-10-15, check again before starting):

- GitHub org and repo `steamhangar/steamhangar` (GitHub redirects old
  URLs, but update every link), the Go module path of `agent/`.
- Container images `ghcr.io/steamhangar/vault-*` (component names
  `vault-*` stay; only the namespace moves). Old tags stay pullable for
  existing deployments.
- Compose project name `steamhangar`: it prefixes named volumes. Volumes
  cannot be renamed (ADR-0013 copy procedure), so either keep `name:`
  overrides on the volumes or plan the copy. The operator's production
  stack, its volume labels and its cache dataset are the operator's
  business: request, never self-service.
- Android `applicationId` is still `dev.steamvault.app` (never changed in
  RN-1). Changing it means a new app for Android: users uninstall and
  reinstall. Do it in the same step, before any store publication.
- Windows agent: service name, install paths, winget/installer ids.
- Product strings in web UI, app, docs, README, `about` endpoint, the
  `/dist` download names, webhooks' user agent.
- The public domain can stay.

Name availability (checked 2026-10-15, re-check before the ADR):

- "GameVault" was considered and rejected: an established self-hosted,
  Docker-based game platform of that name exists (gamevau.lt), the same
  kind of collision that ended "SteamVault" (ADR-0013).
- "GameHangar": no self-hosted or cache project of that name found in a
  web search; only a small hobby repo (`Koubae/game-hangar`) and unrelated
  shops with similar names. Not a trademark search.
- GitHub org `GameHangar` is **taken** (empty organisation, created
  2025-06-04, no public repos). Free: `gamehangar-app`, `gamehangar-dev`,
  `gamehangar-io`, `gamehangarhq`. The ghcr namespace follows the org, so
  images would be `ghcr.io/<new-org>/vault-*`. Options for the ADR: ask
  GitHub for the inactive name, pick one of the free orgs, or keep the
  `steamhangar` org and rename only product and repo.
- Domains: `gamehangar.com` is **taken** (registered 2009, redirects to
  the games publisher Wired Productions; check for a trademark before
  going public). `.net`, `.org`, `.io`, `.app`, `.dev`, `.de` had no
  registration in RDAP. The public instance domain does not depend on it.
- Domain hack `gamehang.ar` (Argentina, in the style of `gamevau.lt`):
  not registered, nor `gamehangar.ar` / `gamehangar.com.ar` (NIC
  Argentina RDAP 404; cross-check with registered `.ar` names returned
  200). Caveat, unverified: NIC Argentina is understood to require an
  Argentine tax login, so a foreign owner registers through a registrar
  with a trustee/local-presence service (higher cost, dependence on the
  trustee). Confirm with a registrar before deciding.

Steps: inventory with grep, ADR with the open questions (volume strategy,
`applicationId`, old image tags, redirect period), user decision, then one
package per component. Not during the v0.1.0 release chain; planned with
v0.2.0 (Release plan).

## D19: DEPOT-PREVIEW-1, expected depots and size on a never-cached game

User request 2026-10-15 (Weg A chosen). Today the detail card of a game that
was never downloaded says "Size unknown" and "Depots unknown until the first
download": the worker learns depots only after a job, by diffing the cache
(`worker.py` `apply_observed_mapping`), and the oracle (`api.steamcmd.net`,
UPD-1) is polled every 180 min for cached games only. Measured 2026-10-15
from the diag container: one oracle query takes ~0.35 s (DNS 0.06, connect
0.15), 10 KB; the answer carries depot ids, per branch manifest gid, `size`
and `download` (appid 1366540: depot 1366541 public 4.68 GB, 1.82 GB
download; depot 1366542 is a DLC depot without manifests).

Design (proposed, to settle in the package brief):
- Opening the card of an uncached game triggers ONE oracle query in the
  background; the card opens at once and fills in when the answer arrives.
  Stored with a TTL (24 h) in the existing oracle storage; a repeat open
  inside the TTL asks nothing. Oracle off or timeout: card stays "unknown".
- The answer is an EXPECTATION. It never writes `depot_app_map` (delete and
  GC trust that table as observed). The card shows "expected" depots and size
  (public branch, DLC depots without manifest listed apart), and switches to
  the observed values after the first download.
- Privacy: each opened card sends one appid to a third party. Same data as
  the cached-game poll, but triggered by browsing. No separate switch of its
  own: it follows the privacy mode of D22 (until D22 lands it follows
  `VAULT_MANIFEST_ORACLE=off`), plus dedupe/rate limit per appid.
- Dev: anonymous metadata queries are allowed (user decision 2026-10-13);
  no downloads.

Packages: **DEPOT-PREVIEW-1a (api)** on-demand query, TTL storage, response
fields; **DEPOT-PREVIEW-1b (web)** "expected" display;
**DEPOT-PREVIEW-1c (api + web)** card enrichment, see below;
Android follows in the app track.

### D19-1c: card enrichment (description, modes, ratings)

User request 2026-10-15: make the card livelier with a short description,
single player / multiplayer / VR and ratings, also recent ones. Measured
2026-10-15 from the devbox, anonymous GETs without key or login, appids
292030, 250820, 1366540:

| Source | Delivers | Measured |
|---|---|---|
| Oracle `api.steamcmd.net/v1/info/<id>` (D19 queries it anyway) | `common`: type, `category_<id>` flags (2 single-player, 22 achievements, 62 family sharing, 31 VR support, 52 tracked controllers), genre ids, `review_percentage` (97 for 1366540), `review_score` (0-9), `steam_deck_compatibility`, `oslist`. No description text, no localized names (ids only: the web needs its own id to label map) | no extra request, no new host |
| `store.steampowered.com/api/appdetails?appids=<id>&l=<lang>` | `short_description` (156-284 chars), `categories` (id plus localized text), `genres`, `developers`, `release_date`, `metacritic`, `recommendations.total` | 200 in 0.31-0.39 s (one 4.4 s outlier on the first call), 11-54 KB; `german` and `english` both complete for these three, German texts differ in length |
| `store.steampowered.com/appreviews/<id>?json=1&num_per_page=0` | `query_summary`: `review_score_desc` ("Overwhelmingly Positive"), `total_positive`, `total_negative` | 200 in ~0.35 s, 0.2 KB |

Finding that changes the plan: **a "recent reviews" figure is NOT available
from the summary.** `day_range=30` and `filter=recent` leave `query_summary`
unchanged (all-time totals, tested with `num_per_page` 0, 1, 20). The store
page's "recent" rating is not exposed by this endpoint. Options: (A) show
only the overall rating (honest, recommended); (B) approximate recent
sentiment from a sample of `filter=recent` reviews (e.g. 100 per open, share
positive) and label it "sample of the last N reviews" - more traffic, more
code, and it is our own estimate, not Steam's number. Decision for Jan.

Design (proposed):
- New host `store.steampowered.com`: needs an egress-allow entry baked into
  the proxy like `api.steampowered.com` (ADR-0011) or a `VAULT_EGRESS_ALLOW`
  value; a deploy Antrag must list it. Opening a card sends one appid (two
  requests: appdetails, appreviews) to Valve from the household WAN IP;
  nothing else, no SteamID, no key. Never use the relay key for this.
- Same on-open, background, deduped, TTL (24 h) storage as 1a, in a
  separate table from `depot_app_map`; failures back off per appid; never
  more than the on-open pattern (appdetails is limited to roughly 200
  requests per 5 minutes per IP from memory, unverified: no bulk fills).
- Language: ask `l=<ui language>`, fall back to English when the field is
  empty. Texts are third-party: render as plain text (`textContent`), never
  HTML; cap the length.
- Card: description (3 lines, expandable), chips for single-player /
  multiplayer / co-op / VR, genres, developer and release date, overall
  rating with counts. Missing fields are omitted, never shown as
  placeholders. Removed, region-locked or age-gated apps (`success:false`)
  show nothing extra.
- Privacy: gated like 1a. Until D22 lands behind `VAULT_MANIFEST_ORACLE` is
  too weak (a different host and party): ship 1c behind its own env
  `VAULT_STORE_DETAILS` (default off, a failure of every store leaves it
  off), and let D22 fold it into the modes (light/secure on, strict off).
  The failure-mode analysis per store required by LEARNINGS comes before the
  coder brief. Entry in the D21 inventory table.

## D20: QUEUE-ORDER-1, make the Downloads queue sortable

User request 2026-10-15. The queue rows in Downloads show a drag grip
(`web/js/views/downloads.js` `buildQueueRow`, `GRIP_SVG`) but the queue is
FIFO and the footer says "Drag-to-reorder is not built yet". Rule from the
user: a control that looks like a feature has to be that feature.

Constraint found in code: the queue is FIFO by `jobs.id` (`claim_next_job`)
and there is deliberately no priority column; `resume_job` relies on a
resumed job keeping its old id to land at the front (`jobs.py`, router
`resume`).

Design (proposed):
- Schema: a nullable order key on `jobs` (migration), claim order
  `ORDER BY order_key, id`; NULL keeps today's FIFO, so resume semantics and
  existing rows stay unchanged.
- API: `POST /v1/jobs/{id}/move` (to a position, queued jobs only, `409`
  otherwise, `404` unknown); the running job cannot be moved.
- Web: drag and drop on the grip (pointer events, touch included), plus a
  keyboard alternative (grip focusable, move up/down) for a11y; optimistic
  reorder with rollback on error; polling must not fight an active drag.
- Interim until the package lands: nothing hidden, the footer text stays
  honest.

Packages: **QUEUE-ORDER-1a (api)** migration, claim order, move endpoint,
tests; **QUEUE-ORDER-1b (web)** drag and drop, keyboard path, tests; Android
follows in the app track.

## D21: PRIVACY-REVIEW-1, review of all privacy concerns (before D19 ships)

User request 2026-10-15. Read-only review package (reviewer, no production
code). Scope: every flow that leaves the LAN or shows personal data to
another person in the household, including the new D19 on-open oracle query.
Inventory as of rc18.1 (from `api/README.md`, `cover_art.py`, `steam_relay.py`,
ADR-0010, ADR-0011; to be re-verified in the review, not taken as final):

| Flow | Data | Recipient | Control today |
|------|------|-----------|---------------|
| Manifest oracle, cached games, every `VAULT_SCHEDULE_INTERVAL_MINUTES` | appids | api.steamcmd.net (third party) | `VAULT_MANIFEST_ORACLE`, `_URL`, `_TIMEOUT` |
| Oracle on card open (D19, new) | appids of browsed games | same | none yet |
| Cover lookup `IStoreBrowseService/GetItems` and cover images | appids | api.steampowered.com, steamstatic CDN (Valve) | egress lock only |
| Steam Web API relay (library preview) | SteamID64, optional web key | api.steampowered.com | `VAULT_STEAM_LIBRARY_STEAMID`, key routes |
| Playtime and last played in the relay response | household members' habits | people in the living room | `VAULT_RELAY_EXPOSE_PLAYTIME`, `_LAST_PLAYED` (env-only, ADR-0010) |
| Webhooks | job and client events, client addresses | operator-chosen URL | `VAULT_WEBHOOK_URL`, `_EVENTS`, `VAULT_NAME` |
| Extra egress hosts | any | any named host | `VAULT_EGRESS_ALLOW` |
| Downloads, Steam login | account session | Valve | not optional (product purpose) |

The review answers: which flows carry personal data, which are optional,
which are on by default, whether each control fails towards "more
collection" when its store is lost (the `docs/LEARNINGS.md` rule: a
failure-mode analysis per store, env, db volume, image default, BEFORE any
coder brief), and which controls overlap or contradict. Output:
`docs/security/privacy-review.md` plus findings for the D22 design.

## D22: PRIVACY-MODE-1, one privacy mode instead of many switches

User idea 2026-10-15: replace the many single privacy controls with a mode,
for example `off`, `light`, `secure`, `strict`, each a bundle. Proposed, not
decided; the ADR comes after D21. Starting sketch, to be corrected by the
review:

| Flow | off | light | secure | strict |
|------|-----|-------|--------|--------|
| Oracle poll, cached games | on | on | on | off |
| Oracle on card open (D19) | on | on | off | off |
| Covers (lookup and images) | on | on | on | placeholders |
| Library relay | on | on | on | off |
| Playtime, last played | allowed | hidden | hidden | hidden |
| Webhook payload with client addresses | included | omitted | omitted | webhooks off |
| `VAULT_EGRESS_ALLOW` extra hosts | honoured | honoured | honoured | ignored |

Design questions for the ADR:
- Where the mode lives. A mode stored only in the db volume reverts to its
  default when the volume is lost, so the default must be the safe direction,
  or the floor is an env value and the UI may only tighten it (ADR-0010 chose
  env-only for the playtime gate for this reason).
- Which existing env keys stay as expert overrides (`VAULT_MANIFEST_ORACLE`
  and so on) and which are removed; how the mode and an explicit override
  rank (an explicit env value wins, as in the rc18 settings rule) and how the
  Settings page shows "set via mode" versus "set via env".
- The default mode for a new install and the migration of the current
  rc18.1 production values (today's behaviour is closest to `light`).
- Whether `off` should exist at all, or "light" is the loosest level.

Packages: **PRIVACY-MODE-1a (docs)** ADR with the failure-mode analysis;
**1b (api)** mode resolver, mapping to the existing controls, tests per mode
and per store loss; **1c (web)** one Settings control and the "set via"
display; Android follows in the app track. Order: D21, then D22, then D19
ships on top of the mode.

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
Revised 2026-10-14 (user, multiple choice): rc17 = D15 (a-d, ROT,
RUNNER, client names, history delete) + AGENT-ADDR-1 (agent change
allowed; the Windows PC needs the new agent once) + CACHE-COMPLETE-1 +
LIB-LIST-1 + LIB-FILTER-INST-1 + UPD-6 Android; rc18 = D16 + APP-DIST-1.
rc18 cut (user, multiple choice, 2026-10-14): everything in rc18 —
UPD-FIX-1 first, D16a/b/c (incl. the core path; the tag waits for the
settings-volume Antrag), APP-DIST-1 + AGENT-DIST-1 (one ci+api package
DIST-1a, one web package DIST-1b). D16 UI ships for web in rc18; the app
gets sections and expert mode in rc19. D16a classification principle
"Tuning + Debug": editable = upstream rate and window, TLS and upstream
caps, prefill threads and timeout, GC and retention, miss trigger, sweep,
oracle timeout, webhook events, keep counters, plus the debug knobs
UPSTREAM_EDGE / UPSTREAM_POOL_HOSTS, RESOLVER and LOG_LEVEL (marked risky,
confirm dialog). Env-only = secrets, paths, binds and ports, build
metadata, egress allowlist and subnet, webhook URL, DNS, TLS passthrough.
The user signs off the full per-variable table from D16a-1.
D16a-1 questions (user, 2026-10-14): env ALWAYS wins, also for the 9
existing overrides (ADR-0009 addendum in D16b); an env-pinned value shows
as plain text with a "set via env" note, never as a disabled input.
`VAULT_WEBHOOK_URL` becomes env-only (shown masked; events stay
editable). ONE download window: inside it automatic downloads at full
speed, outside no automatic downloads and the rate cap applies;
`VAULT_UPSTREAM_RATE_WINDOW` is deprecated (still honored as an env pin,
startup warning). D8 later adds several windows per day with a per-window
throttle checkbox. The core settings file lives on a new volume.
Night run 2026-10-14 (user, multiple choice): tag v0.1.0-rc18 is allowed
when every package passed review and CI, tests and `dev.sh verify` are
green; then file the rc18 deploy Antrag (incl. the .env review). If D16c
fails a hard gate (envsh sourcing, volume seeding) without a clean
alternative, rc18 ships without D16c (core keys env-only, shown read-only)
and D16c moves to rc19.
D16a table sign-off (user, 2026-10-14, block by block): network and load
keys editable, but PREFILL_MAX_THREADS, UPSTREAM_MAX_CONNS and both TLS
caps are risky (confirm dialog; a too-high value can fill the CGNAT,
incident 2026-10-04); UPSTREAM_RATE and the download window stay plain
editable. Debug: UPSTREAM_EDGE, UPSTREAM_POOL_HOSTS, RESOLVER risky;
LOG_LEVEL editable without dialog (still in the expert area).
Housekeeping keys (GC grace, retention/keep counters, sweep and miss
trigger, client staleness, oracle and prefill timeouts) all live behind
the expert switch; the normal page shows the download window, auto-GC,
the vault name and the block-4 keys. Block 4 (borderline) as proposed in
docs/handover/d16-settings-audit.md section 3.

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

**WEB-FEAT-SUGGEST-2 (web, rc16; user decision 2026-10-13, replaces
SUGGEST-1 below):** the Phase 4h suggestions panel is removed. Its
statements move: "last changed N days ago" -> detail sheet ("Last
update"); "fully cached, never played" -> a second pill on the card,
right of the status pill; "unchanged for N days" -> dropped; "not
confirmed current for N days" -> a notification "Last update check was N
days ago", raised from 14 days on (was 30 in the panel).

~~WEB-FEAT-SUGGEST-1 (superseded the same evening):~~ once the
4h suggestions panel is dismissed, its suggestions appear in the
notifications (bell) instead, with a "Show suggestions panel again"
action there; dismissing shows a short notice that the suggestions now
live under notifications. Today a dismiss is permanent per browser
(`steamvault.decisionPanelDismissed`, no re-enable).

**User decisions 2026-10-13 (late evening):**
- "Never played" card pill also on Android (small follow-up, rc16).
- D15 statistics retention: 5-minute rows for 48 h, hourly rows for
  30 days, daily rows forever (the "all" filter works).
- D15 event-log rotation done by vault-core: rotate at 64 MiB, keep one
  old file (about 128 MiB max).
- D16 expert mode: as much as possible controllable from Settings. A
  value set in the env is shown read-only in expert settings (env wins,
  no input field); unset values are editable there. Rationale: easier
  debugging now, good values can be pinned in the env later. Secrets,
  network/egress, paths and subnets stay env-only by default; D16a lists
  every env value with a proposed editable/env-only class for the user's
  review.

**User decisions 2026-10-14 (morning):**
- UPD-5 (api, rc16): the scheduled update check is always on (oracle on),
  independent of the schedule window; automatic downloads stay bound to
  the window. Supersedes the window gate from the UPD-1 review (dev then
  checks in the background too: anonymous metadata, allowed).
- The web demo shows the "Never played" pill out of the box (demo seeds
  the playtime gate, key and SteamID; real installs keep the gate off).
- D15: clients can be given a name (by IP address; by MAC where the
  vault can learn it — agents can report it, the server itself only sees
  IPs).
- D15: "Delete history" in two variants: everything, and older than
  30 days.
- D15 runner (option B): before each prefill the runner sends a
  recognisable marker request through vault-core; vault-api records its
  source address and flags that client as the runner (excluded from
  "active clients"). Own small package D15-RUNNER (core/runner + api).
- rc16 prod Antrag must also switch the playtime relay on for the user's
  own install: `.env` `VAULT_RELAY_EXPOSE_PLAYTIME=true` (ADR-0010 gate,
  env-only today, forwarded by compose; default stays off for everyone
  else). The user wants to test the "Never played" pill with real data.

**UPD-6 (web + app, rc16; user feedback 2026-10-14 on prod rc15:
"Checked 14 games · 7 updates available", but the 7 were hard to find):**
the status pill on the cover shows an orange "Update" (instead of the
green check) and the status line reads "UPDATE AVAILABLE" instead of
"CURRENT" for games with update_available; Downloads gets a section
"Updates available (N)" listing them with "Update now" per game and
"Update all", plus a note when they download automatically ("in the
schedule window HH:MM-HH:MM" or "manual only" without a window). Starting
an update moves the game into the normal download queue. Prod has no
schedule window today (manual only); an automatic window needs its own
Antrag and also enables the nightly prefill.

**AGENT-ADDR-1 (agent + api, rc17 — moved from rc16 by the user 2026-10-14; bug found 2026-10-14):** prod
shows "Client Demon is bypassing the cache" permanently although Demon's
downloads demonstrably come from the cache (Graveyard Keeper 2, 94 % HIT,
2026-10-13). Cause, measured 2026-10-14 via `diag netns
steamhangar-vault-api-1 ss`: every connection to vault-api arrives from
172.16.13.1 (docker bridge gateway, published port through the userland
proxy), so `agent_reports.source_addr` is never Demon's LAN address and
the bypass rule (no cache-log presence for that address) always fires.
The same breaks the D15b agent<->address mapping. Fix (orchestrator):
the agent reports its own LAN address(es) used toward the vault in the
report payload; vault-api correlates on those (fallback source_addr);
the bypass rule refuses to accuse when the only known address is a
private bridge/gateway address. No ZEUS change.
Constraint (user, 2026-10-14): after v0.1.0 a real installer replaces
the PowerShell task scripts. The address detection lives in the agent
binary itself (no new script parameter, task argument or config entry),
and old servers / old agents keep working (optional field).

**LIB-FILTER-INST-1 (web + app, rc17; user request 2026-10-14):** a
library filter chip "Installed, not cached" — games an agent reports as
installed on a PC (installed_on non-empty) that have no bytes in the
cache. Uses the existing installed_on data; counts groups like the other
chips (D9); same chip on Android.

**LIB-LIST-1 (web + app, rc17; user feedback 2026-10-14 on prod rc15):**
the list layout's right side is restless: status word, size, "Update",
"not cached · Demon" and "Installed on" are right-aligned with varying
widths, so nothing lines up from row to row. Rework into fixed columns
(table-like grid): status (icon + word, fixed width), size
(right-aligned, tabular figures), update state, installed on (PC names,
truncated); one line per row where it fits, same columns in every row,
column headers optional. Check at 360/768/1440 px; same idea for the
Android list row.

**CACHE-COMPLETE-1 (api + web + app, rc17; user report 2026-10-14):**
Timberborn flipped from "Not cached" to "Cached" seconds after Demon
started its update, although 1.3 GB could not have arrived: "cached"
today only means size_bytes > 0 (hasVisibleCacheContent). User decision:
both stages. (1) Completeness: vault-api compares the bytes on disk with
the current build's manifest size per depot (manifest archive / oracle
data) and exposes a completeness figure; cards show "Partially cached
N %" and only 100 % gets the green check. (2) "Verify" action per game:
the runner re-validates the game (non-forced prefill / SteamPrefill
verify path — check what SteamPrefill offers) and fetches missing or
damaged chunks; progress in Downloads. Addition (user, same day): the
always-on update check (UPD-5 cadence) also runs the completeness check
automatically, so incomplete games are detected without a click. With
auto-download (a schedule window) they are completed inside the window;
without one they are listed in Downloads next to "Updates available"
(e.g. "Incomplete (N)" with "Complete now" / "Complete all") — the
completion itself is a download and never runs outside the window
unless started by hand.
UI rule (user, 2026-10-14): every new Downloads section ("Updates
available", "Incomplete", and later ones) is shown only when it has at
least one entry — no empty headings.

**CACHE-REPAIR-1 (api, after rc17; user decision 2026-10-14):** repair
games whose cache holds chunk files of the wrong size (CACHE-COMPLETE-1a
counts them as damaged; `cache_missing_chunks: 0` on an incomplete row). A
forced prefill cannot fix them: vault-core serves the existing file as a
HIT. vault-api deletes the mismatched chunk files of the app's target
manifests -- re-checking each file's size right before the delete, behind
the cache-root guards of `deletion.py` -- and then queues a forced run, which
misses on those chunks and stores fresh copies. Until then no action repairs
such a game; "Complete all" and the automatic completion skip it.

**APP-DIST-1 (ci + api + web, rc17; user decision 2026-10-14, option B):**
the release pipeline builds and signs the APK first and bakes it into the
vault-api image (about +9 MB on ~100 MB compressed); vault-api serves it
in the LAN at a fixed path. Clients page -> "Add device" -> Android
phone: "Download app" button plus a QR code to the vault's APK, then the
existing pairing QR. Dev builds (no APK in the image) fall back to the
GitHub release asset of the same version.

**UPD-FIX-1 (api, rc18 FIRST; bug reported by the user on prod rc17,
2026-10-14):** "Update now" does not clear "Update available". Prod showed
7 games under "Updates available" (Assassin's Creed Black Flag Resync,
Bongo Cat, Graveyard Keeper 2, No Man's Sky, Subnautica 2, Tavern Keeper,
The Crust). "Update now" on Tavern Keeper ran job #108 in 3 s: SteamPrefill
"Prefilled 1 apps totaling 0 b, Updated 0 | Up To Date 1"; vault-api
"Manifest ingestion: recorded 4 manifest(s) for depot(s) [436781]"; the game
stayed flagged.
Analysis (code, rc17; not yet confirmed with prod data):
- `update_available` = oracle public gid != `depot_manifests.manifestid`
  per depot (oracle.py `_compare_depots`, ~1065-1118; recomputed live on
  every GET /v1/games).
- Likely root cause: `manifest_ingest.py` (~216-273) ingests EVERY
  `{appid}_*.bin` in SteamPrefill's temp dir in unsorted `os.scandir` order
  and upserts each into the single `(appid, depotid)` row — last file wins.
  Old manifests from earlier runs accumulate there (nothing clears them),
  so the stored gid can be an old one, the flag never clears, and a real
  update run can overwrite the correct gid with a stale one. The only test
  of a replaced manifest deletes the old file first
  (tests/test_manifest_ingest.py ~186-189), so it hides this.
- Other candidates: oracle snapshot disagrees with Steam (would change
  after a manual update check); a `depot_manifests` row for a depot
  SteamPrefill no longer downloads (rows are never deleted; depot_app_map
  is not consulted).
Prod measurement (user, read-only, 2026-10-14 22:03): Tavern Keeper =
appid 436780, depot 436781. `depot_manifests` holds 5788091679023567485
(recorded 2026-10-14T19:39:10Z = the "Update now" job); oracle public =
3589942376827713992; depot_app_map maps 436781 -> 436780 (so not an orphan
row). Note: `apps.name` did not match 'Tavern Keeper%' (name column empty
or different) — names shown in the UI come from elsewhere. CONFIRMED cause 1 (user, 22:37): the runner's
SteamPrefill temp dir holds four .bin files for depot 436781 —
3589942376827713992 (Oct 14 07:36, newest = oracle's public gid),
8766284553724588703 (Oct 12), 5788091679023567485 (Oct 5),
8230470978449025486 (Oct 4). Ingestion stored 5788… (an old one) in the
"Update now" job: last-file-wins over unsorted scandir. The game is in fact
current; only the recorded gid is wrong. The runner container on prod is
named `steamhangar-vault-runner` (no -1); the temp dir is the default
$HOME/.cache/SteamPrefill/v1 (VAULT_STEAMPREFILL_CACHE_DIR unset).
Formerly open question, kept for context: which gid is Steam's current public one (the .bin list in SteamPrefill's
temp dir, or steamdb.info/depot/436781/manifests): if 3589… is among the
.bin files -> ingestion picked an old file (cause 1); if not -> the oracle
disagrees with Steam (cause 2: lagging/other branch at api.steamcmd.net).
Fix scope:
1. (done) prod data confirmed cause 1.
2. Ingestion records only the manifest SteamPrefill actually used in this
   run (newest by SteamPrefill's own record `successfullyDownloadedDepots.json`
   or by file mtime, documented), never an arbitrary leftover; one-time
   repair of existing rows at startup (re-derive from the same rule).
3. The oracle comparison only considers depots the vault prefills
   (depot_app_map) — a stale row for an unselected depot must not flag.
4. After a successful "Update now", re-evaluate the verdict immediately
   (no 3 h wait); if SteamPrefill says Up To Date but the oracle still says
   stale, surface it honestly (e.g. a job note "Steam reports this build as
   current — oracle and Steam disagree") instead of silently leaving the
   game flagged.
5. Tests: several .bin files for one depot in random order; end-to-end
   "Update now clears the flag"; stale row for an unmapped depot.
Scope change 2026-10-15 (review UPD-FIX-1): point 3 dropped. Filtering
the comparison by depot_app_map hides real updates, because
`apply_observed_mapping` replaces an app's mapping with only the depots
that got new bytes in a run, so an untouched depot drops out after a
partial update. Follow-up MAP-DRIFT-1 (after rc18): orphan
depot_manifests rows and mapping drift on partial updates; a non-lossy
filter (mapped OR bytes on disk) with a partial-update regression test.
Interplay: CACHE-COMPLETE-1a uses the same target selection (recorded vs
oracle gid) — check the completeness figure is not affected the same way.

**AGENT-DIST-1 (ci + api + web, rc18; user decision 2026-10-14):** like
APP-DIST-1 for the agent: the vault serves the vault-agent itself in the
LAN instead of the GitHub release. rc18: the release binaries (Windows exe,
Linux amd64/arm64) and the task scripts plus SHA256SUMS are baked into the
vault-api image and served at a fixed path; the "Add a device" install
snippet (web/js/lib/agent-install.js) downloads from the vault, with the
GitHub release asset of the same version as fallback for dev builds. Later
(after v0.1.0): a real installer replaces the snippet and the PowerShell
task scripts (see AGENT-ADDR-1 constraint) and is served the same way.

**WEB-FIX-UPD-VIS (web, rc16; user feedback 2026-10-13):** the library
check buttons do not read as buttons (full-width text bar). Make "Check
updates" a clearly visible primary button and give "Check & update all
cached games" a distinct style (it downloads); side by side, not a full
row. The web demo mode gets the update check, one flagged game and the
"Updates" chip (UPD-2 left demo out; the Android demo has it).

**rc17 follow-ups (review findings deferred 2026-10-14, after rc17):**
CACHE-REPAIR-1 (above); app list: fixture with installed_on + column
assertions, compact installed-on wording below 1024 dp; stronger
completeness twin pins; app parity gaps (client rename, Delete history,
Manage PCs add/remove, focus handling on Downloads rebuilds); clock
injection for the D15b/D15-NAMES endpoint tests (bucket-boundary edge
cases); a permanent conftest hook that emits failed test names as CI
annotations; per-app backoff for repeated forced completion runs.

**rc18 status (2026-10-15 night run):** UPD-FIX-1 (scope point 3 dropped,
see MAP-DRIFT-1), D16a-1 audit, D16b-1 settings API, D16c core settings
path, D16-WEB (sections, expert mode, env pins, vault-core apply line),
DIST-1a/1b (vault serves APK + agent) — all reviewed PASS and merged.
**rc18 follow-ups:** MAP-DRIFT-1 (above); D16 Android UI (rc19); D16c
nits: test for the watcher retry loop (transient failure retried on the
next tick), log a transient apply failure once per streak instead of
every poll, log a line when VS_WATCH_FUNCTIONS_ONLY is set; conftest-wide
refusal of oracle.http_fetch with an opt-out for the oracle unit tests;
webhook boot line witness test; demo core_apply generation bumps only
when core keys change.

**Before v0.1.0 (ongoing):** release notes, user device checks,
verify-stack 10 h flake, Android follow-ups (PC remove twin, onboarding
saves id; now ANDROID-GAPS-1 in the audit list), D7 measurement on `:443`
(TLS cap TODO in WORKPACKAGES), failed jobs Subnautica/Timberborn/Hogwarts
Legacy (measure live on the next run).

**rc19 Downloads block (Jan's yes, 2026-10-15):**
- METRIC-AUDIT: header "Current speed" (22.3 MB/s) vs the active job's own
  rate (4.0 MB/s) and "Loaded" growing faster than the job's bytes (+6.9 GB
  vs 1.8 GB); "Active clients 0" while Demon is "Connected, last active 2 min
  ago" (different windows); hit rate 0% in the header vs 94% for the runner
  on the Clients page (different scopes, not labelled). Measure against the
  event log on prod (`dev.sh diag`, read-only), then fix or label.
- TILE-INTERNET: eighth Downloads tile "Loaded from the internet" (bytes
  that missed the cache, runner included) so the grid is 2x4 and Loaded =
  internet + saved can be checked. Check first whether `server_summary`
  already exposes the all-scope miss bytes.
- GRAPH-ZOOM: 24 h graph is flat when all traffic fell into the last half
  hour (linear scale to the peak, 5-minute buckets). Add 1 h and 6 h ranges,
  trim the X axis to the span with data, fill under the line. No API change;
  a 1-minute bucket tier is a separate decision.
- GRAPH-LIVE (Jan, 2026-10-15): Steam-like live graph, 5 s points for the
  first minute then coarser; design in docs/handover/rc19-run-plan.md
  (in-memory live tier, faster log tail only while someone watches).
- DIAG-SESSION: why downloads are far below LAN speed. Separate the miss
  path (throttled by VAULT_UPSTREAM_RATE=8m, internet) from the hit path
  (cache disk on HDDs + LAN). LAN baseline with `diag net iperf3`, hit-path
  read of an already cached game, pool read throughput (fio/iostat may be
  missing in the diag container: Antrag), nginx connections and caps, miss
  counter-test only via `dev.sh prod rate` with Jan's yes per step. Result
  as an inbox note.

- ID-AUDIT (Jan, 2026-10-15): find out why Steam IDs still show where a
  game name belongs: Clients page "Loaded recently: App 1465470 / App
  3419430", Downloads job card "job #111 · appid 1962700". Reading the code:
  the UI falls back to `App <id>` whenever the `apps` table has no name for
  the id (api/vault_api/traffic.py `app_names`; web: clients-page.js:95,
  library.js:222/867/994, game-status.js:218, owned-library.js:165,
  depot-presentation.js:76). Hypotheses: apps seen only in cache traffic
  (not in the synced library, or tool/runtime apps like 3419430) never get a
  name; the name lookup (oracle/relay) is not run for traffic-only ids.
  Audit: list which ids on prod lack a name and why, then resolve names for
  traffic-seen ids (metadata-only lookup, same oracle rules) and decide
  whether the technical "appid" on job cards stays (it is a deliberate
  detail line).

- LIB-SORT (Jan, 2026-10-15; decided): the Library has filters but no sort
  control. Options: A-Z / Z-A, size, state (cached/installed), last cached
  or updated (job history), ascending/descending, and "last played" ONLY
  while the privacy setting `relay_expose_last_played` is on (off by
  default, shared-household decision in config.py). "Newly bought" is
  dropped (Steam has no purchase date). Persist the choice per browser like
  the view switch. Same screenshot: "APP 1716750" / "APP 3990420" cards
  without a name (ID-AUDIT).

- NOT-OWNED-1 (Jan, 2026-10-15, SteamVR 250820 "failed"): job log ends with
  "Prefilled 0 apps ... SteamPrefill did not consider this app - is it owned
  by the logged-in account? Depot mapping and manifest state were NOT
  touched." Same class as 228980 (API-FIX-4): the Demon agent reports it as
  installed, the scheduler or miss-trigger enqueues it, SteamPrefill cannot
  prefill an app the account does not own, the job ends `error`, the Library
  shows "Failed" + Retry. SteamVR is not in `tool_apps.py`. Weg A: add
  250820 to the fixed list (one entry, same review path). Weg B (better):
  make "SteamPrefill did not consider this app" a distinct outcome
  (`not_owned`), shown as "Not available to prefill (not owned)" instead of
  Failed, no Retry, and excluded from scheduler/miss-trigger/"update all"
  until the library sync says it is owned; keeps working for the next such
  app without a code change. Recommend B, and A as the seed. Verify first
  that 250820 is really absent from the owned list (Library header says
  437 owned vs 442 in All, so 5 cards are installed-but-not-owned). Open
  question from Jan: it was installed via Steam on Demon, so why "not
  owned"? Candidates: Demon's Steam account differs from the vault's Steam
  ID; SteamPrefill only considers app types it can prefill (tools such as
  SteamVR may not be listed in its owned set); no free license on the vault
  account. Jan confirmed 2026-10-15: Demon and the vault use the SAME
  Steam account, so the account candidate is out; most likely SteamPrefill
  skips tool-type apps (or there is no license entry). Decision: Weg B
  (distinct `not_owned` outcome) plus 250820 as the seed entry. Still
  verify the cause with one read of the SteamPrefill owned list / library
  sync entry for 250820 while implementing.

**UI polish (Jan, 2026-10-15; later, not scheduled yet):** (1) Clients page
and the "Add a device" sheet look busy: the same "Anyone who sees this can
control your hangar" warning repeats in four sections, many equally weighted
buttons, no clear hierarchy. Verify with static HTML mockups (2 variants,
real theme.css, demo data) BEFORE implementing. (2) After 0.1.0 closes: one
full UI pass over spacing and the features added since rc10, so they read as
one design.

**After v0.1.0:** D8 (incl. rate window in Settings, may fold into D16),
D1, D3 (incl. device/account note per game), D2, D4, D5, D6.

## Suggested order

**Superseded (2026-10-16)** by "Release plan (user decisions 2026-10-16)"
near the top of this file; kept for the record. Items 1, 3 and 5 are
done (DOCS-FIX-3, D1, D7 apart from the `:443` measurement); D2/D3 are
v0.3.0, D8 is v0.2.0.

1. DOCS-FIX-3 lands before `v0.1.0` (it is in item 13 B).
2. Write the ADR drafts for D1, D2 and D3, review them, and settle the
   open questions with the user.
3. D1 first: it is the widest benefit, and the production measurement is
   cheap.
4. D2, then D3 on top of it.
5. D7 BEFORE the `v0.1.0` tag (user decision 2026-10-04, superseding the
   earlier "after the tag"; ADR-0021, CORE-FIX-4a..4d); D8 after the tag.
   D7 is the only item with a measured harm on the production line.

## D23: LIBRARY-META-1, durable app metadata and library filters (after rc19)

User request 2026-10-15 (Weg A, scope decided): store the card data of D19-1c
long term, fill it overnight, and filter the library by category, genre and
rating. Not in rc19; builds on D19, D21 and D22.

Scope: **every app the vault knows**, not the Steam ownership list. Known =
cached games, apps in `depot_app_map`, apps with job history, apps whose card
was opened. No relay key and no ownership data are needed, so no SteamID goes
anywhere; the set of appids sent is the same kind the oracle poll sends today
plus the browsed ones.

Design (proposed):
- Durable table keyed by appid (columns, not a JSON blob, so filters can use
  indexes): category flags (single-player, multiplayer, co-op, VR), genre
  ids, review percentage and score, release year, plus text columns for
  description, developer and date; `fetched_at`, `source`, per-appid failure
  count and next-try time. Refresh every ~30 days or on demand. Kept apart
  from `depot_app_map` (expected/observed rule of D19 stays).
- Stage 1 (oracle only, no new host): nightly job in the schedule window
  fills flags, genres and rating for all known apps, ~1 app per 2 s,
  resumable, backoff on 429 or errors; this alone powers the filters.
- Stage 2 (store, `VAULT_STORE_DETAILS`): description, developer and date on
  card open as in D19-1c; a nightly store fill for all known apps is a
  separate explicit switch and follows the privacy mode of D22.
- API: filter and sort parameters on the library list (category, genre,
  minimum rating, release year range), indexes, migration; unknown metadata
  never excludes an app silently (an "unknown" bucket).
- Web: filter chips and a rating sort on the Library view; Android follows.
- Privacy: the nightly bulk sends all known appids at once to a third party,
  more than browsing does; the D21 inventory and the D22 mode table must list
  it. `strict` runs nothing; failure direction for every store comes before
  the coder brief (LEARNINGS).

Packages: **LIBRARY-META-1a (api)** table, migration, nightly oracle job,
tests; **LIBRARY-META-1b (api)** filter and sort parameters; **LIBRARY-META-1c
(web)** chips and sort; **LIBRARY-META-1d (api)** optional store night fill
after D22.

**Window decoupling, LIB-META-WINDOW-1 (api, rc20; user decision
2026-10-16, Weg B).** As built in rc19 the nightly `library_meta` job runs
only inside the download schedule window and otherwise skips with
`no-window`; with `VAULT_SCHEDULE_WINDOW=off` it never runs, so the library
filters stay empty. Decision: run it independent of the download window,
once per night, like the always-on update check (UPD-5). It still sends
metadata queries only (no downloads) and stays behind its own switch and
the privacy rules above. State: done in rc20 (no window check; at most one
finished pass per calendar day, own switch and oracle switch unchanged).

## D24: BOOT-RATE-1, rate window at vault-core boot (core, rc20)

Found 2026-10-15 in the A-20261015-01 check; scheduled by user decision
2026-10-16. At start vault-core renders its nginx config with the shipped
default rate window 03:00-07:00 and keeps it for a few seconds, until
settings generation 1 is applied. A restart between 03:00 and 07:00 so
runs unthrottled for those seconds, whatever the operator's settings say.
Fix: at boot read the core settings file if it is present and render from
it; if it is missing or unreadable, start on the throttled side until the
first generation is applied (fail towards the cap, never away from it).
Tests: boot render with and without the settings file, inside and outside
the default window. Depends on D16c (core settings file). State: done in
rc20 (generation 0 boots on window `off`, `vs_boot_throttle`).

## D25: TSNET-1, embedded tailscale (requirement A6)

From the old post-v1 backlog in `docs/WORKPACKAGES.md` (2026-08-09). Embed
tailscale (`tsnet`) so the app's "homecall" reaches the hangar from
outside the house without a separate VPN setup (requirement A6 also names
Twingate and a public domain as connectivity profiles). Needs an ADR (auth
key handling, which component joins the tailnet, interplay with the
egress lock). Dependencies: none scheduled. State: later, unscheduled.

## D26: NOTIFY-CURSOR-1, server-side notification cursor

From the old post-v1 backlog in `docs/WORKPACKAGES.md` (2026-08-09). The
read state of notifications lives per browser and per app today; a
server-side cursor (per device or per key, see D5 and D29) keeps it in
step across devices. Needs an API addition and a schema change.
Dependencies: D5 or D29 for a per-device identity. State: later,
unscheduled.

## D27: DEPOT-GRACE-1, per-depot grace windows

From the old post-v1 backlog in `docs/WORKPACKAGES.md` (2026-08-09).
Garbage collection uses one grace period for every depot; allow a grace
window per depot (or per app), so for example a large, rarely played game
is kept longer. Touches GC, the API and the Settings UI. Dependencies:
none. State: later, unscheduled.

## D28: CLIENT-NAME-MAC-1, client names that survive DHCP changes

User wish 2026-10-14 (D15 names: "by MAC where the vault can learn it").
The agent reports its MAC address(es) in the report payload (optional
field; old servers and old agents keep working, as in AGENT-ADDR-1).
vault-api accepts `key_type = "mac"`, which is reserved today and answered
with 422 (`api/vault_api/client_names.py`), and resolves a name by MAC
before IP, so a named PC keeps its name after a DHCP change. Agentless
clients stay IP-keyed. Pairs well with the installer and the v0.2.0 agent
work (agent reports its OS). Dependencies: an agent release. Schedule:
after rc20. State: open.

## D29: PAIR-2, proper device pairing

User decision 2026-10-04 (follows PAIR-1); the user agreed to build the
pairing ways one after the other ("Die wege nacheinander klingen gut",
2026-10-04). Until the audit of 2026-10-16 this item had no roadmap entry
and was labelled "D9 PAIR-2" in `docs/PROJECT_PLAN.md`, which clashed with
D9 TOOL-APPS-2 here. Scope: short-lived pairing codes (the web UI shows a
code or QR valid for minutes, the device trades it for its own key) and
one named, revocable key per device, so removing a phone or PC revokes
only its key; the `steamhangar://pair` URI gets a `v=2` with a code
instead of a key. Dependencies: D5 (named, scoped keys); D6's desktop
client builds on it. Needs an ADR. State: open, unscheduled.

## Audit 2026-10-16: open follow-ups

Found when the orchestrator went through all past sessions on 2026-10-16.
Each one's version slot is decided ("Release plan (user decisions
2026-10-16)" at the top of this file); its behaviour is not: each needs a
brief (and the user's go where it changes behaviour) before any code. In
`docs/PROJECT_PLAN.md` most appear as unchecked boxes in their version's
block; VERIFY-SPEED-TEXT-1 and QUEUE-KBD-KEEP are text inside DOWNLOADS-1,
and VERIFY-COLD-1 is prose in the Sunday-test box of the rc19.3 block.

- **DUAL-STACK-1 (operator):** AAAA check and stepwise cap raise after the
  line change, see D4.
- **ANDROID-GAPS-1 (app):** "Remove PC" is missing (`VaultApiClient.kt` has
  no `DELETE /v1/clients`), onboarding does not store the SteamID, client
  rename and Delete history are missing in the app. The rc17 follow-ups
  ("app parity gaps") and "Before v0.1.0" list parts of this; this entry
  is the single record.
- **APP-NIGHT-GOLDEN-1 (app, CI):** `library_411dp_font100_night.png` is
  byte-identical to the day golden, so the dark theme is probably not
  applied in the screenshot test. Make the night profile render dark, then
  refresh the golden.
- **CAP-503-LOOP-1 (measurement):** on 2026-10-10, with the 4m upstream
  rate and the global cap 16 on `:80` combined, vault-core rejected about
  14k-21k requests per minute with 503 and the clients retried at once;
  the rate was raised to 8m afterwards. Open: does the loop still occur at
  the current rate. See `docs/LEARNINGS.md` (production rollout) and
  ADR-0021 "Consequences and honest limits".
- **VERIFY-COLD-1 (operator):** a verify over many games on cold data runs
  against the single nginx worker. Keep the verify window off until the
  worker tuning (ADR-0027 Sunday test) decides.
- **HIT-PATH-1 (measurement):** the server-to-client HIT throughput (~91
  Mbit/s seen) has only an idle baseline. Missing: iperf3 LAN baseline,
  nginx counters during a hit, and the hit-path read of an already cached
  game (DIAG-SESSION in the rc19 block).
- **VERSION-HEALTH-1 (api):** `/v1/health` returns only a status and
  `/v1/about` needs the key, so rollout checks could not read version and
  schema. Candidate: a startup log line with version and schema, or the
  version in `/v1/health`.
- **VERIFY-SPEED-TEXT-1 (web, candidate for DOWNLOADS-1):** while
  Verifying, a job shows "Speed not measured yet". Undecided: more honest
  wording, or show the total speed.
- **QUEUE-KBD-KEEP (web, DOWNLOADS-1):** `web/js/lib/queue-reorder.js`
  already moves a focused row with ArrowUp/ArrowDown. DOWNLOADS-1 must keep
  that path when it drops the arrow buttons; it is not a new follow-up.
- **NITS-AUDIT-1 (small, rc22):**
  - dead code `packagesAriaText` (app `ContentGroups.kt`);
  - web test that "Update now" in the group sheet sends the package ids
    (`web/tests/game-detail-sheet-update.test.js`);
  - optional origin check in `isVaultReturnTo` (D11 review);
  - GameHangar logo and header briefing task for D18;
  - web `api.js` has no client-side request timeout;
  - Android tablet/foldable layout (WindowSizeClass) is unplanned;
  - TalkBack group semantics are unverified;
  - `DownloadsStatsLiveTest` has a trivial last assertion;
  - AGENT-FIX-2 follow-up: registry lookup in the Go agent itself;
  - CI-FIX-3 follow-up: pin the APK signing certificate fingerprint in CI;
  - `analyze.ps1` log format gap;
  - Hogwarts Legacy was missing next to Subnautica/Timberborn in the
    failed-jobs measurement note (added in "Before v0.1.0" above).

