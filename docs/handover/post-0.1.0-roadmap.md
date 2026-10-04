# Handover: post-v0.1.0 roadmap (items D1-D9)

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

**Decision (user, 2026-10-04, Weg A).** Ship `v0.1.0` with this as a known
limitation in the release notes; mitigate with the scheduler (installed
apps are prefilled, window covering the day). Fix after `v0.1.0`.

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

## D2 and D3: work packages (planned 2026-10-04)

State of the two tracks on 2026-10-04, the decisions still missing, and
the package cut. No code before the `v0.1.0` tag; the docs packages
(AUTH-0a..0c) may run now.

### Where D2 and D3 stand

- **ADR-0018** (QR login helper, D2): draft on branch
  `wp/docs-adr-post-0.1.0-d1-d3`, commit bcbc349, not merged. Review
  verdict PASS with eight should-fixes still to apply: the vault-api
  WORKER side of a login job (`worker.py::_execute` dispatches only
  prefill/gc; `claim_run`/`find_active_run`/`recover_stale_jobs` filter on
  the prefill type; the login result must not travel through
  `encode_result`/`PrefillResult.output`); `finish_job` must clear the
  challenge URL on every terminal transition (runner_lost, cancel of a
  queued row); drop the two questions that are not the user's (transport,
  terminal fallback); add the helper-language question; correct the
  subprocess-mode reasoning (SteamPrefill already reaches Steam CM from
  vault-api's container in that mode); decide how a `steam_login` row
  shows in `GET /v1/jobs` and the Downloads view (appid sentinel vs
  nullable); "only code in this repository that touches the refresh
  token"; stale temp file cleanup and `present` = token parseable. Plus
  the amendment ADR-0019 requires: the runner status row needs an account
  dimension from the start.
- **ADR-0019** (several accounts, D3): same branch and commit. Round-1
  findings applied (persona instead of login name, directory lifecycle
  across the mount boundary via a removal job, `steam_library_steamid`
  seed in the migration question, fallback chain posed as a question,
  binary copy and removal semantics decided). Round-2 review still open.
- Done since the drafts: DOCS-FIX-3 (ADR-0004 addendum 4, the QR claim
  corrected), API-FIX-3 (summary parsing, the "not considered" signal
  MULTI-1c relies on), APP-FEAT-1 (Android reads the stored SteamID),
  D1 shipped (rc7+). Schema on `main` is v17; ADR numbers 0020 (SNI
  passthrough) and 0021 (CORE-FIX-4 draft) are taken, 0018/0019 stay
  reserved for these two.

### Decisions still missing (user)

D2, ADR-0018 (after the review trimmed the list):

1. Helper packaging: Weg A1 .NET SDK build stage in `api/Dockerfile`,
   binary next to SteamPrefill / Weg A2 prebuilt per-RID artifact from a
   CI job, pinned checksum, downloaded like SteamPrefill / Weg B separate
   container sharing the session volume. Recommendation A1.
2. Helper language: Weg A C# on SteamKit2 3.4.0 (SteamPrefill's own
   token path, verified reference; a second language and SDK in the
   build) / Weg B Python inside the runner against Steam's
   IAuthenticationService web API, writing the three-field protobuf by
   hand (no new toolchain; token platform-equivalence unverified).
   Recommendation A.
3. Settings shows the Steam account name: Weg A no (present / missing /
   expired and the expiry only; the login name is half a credential pair,
   D3 labels by persona) / Weg B yes. Recommendation A.
4. Login requested while a prefill runs: Weg A waits behind the job, the
   UI says so and offers Pause / Weg B the runner runs the helper beside
   SteamPrefill. Recommendation A (one job at a time, ADR-0012).
5. Confirmations, one line each: `POST .../login` answers 403 under
   `VAULT_SETTINGS_READONLY=1`; 409 in subprocess mode (QR is queue-mode
   only); `steam_login` jobs are hidden from `GET /v1/jobs` and the
   Downloads view (they are not downloads). Recommendation: all three yes.

D3, ADR-0019:

1. Library SteamID list: Weg A accounts table only (SteamID from the
   token's `sub`, to verify) / Weg B explicit list setting / Weg C both.
   Recommendation C.
2. Owner hint: Weg A label or persona on the card / Weg B neutral "owned
   by 1 of N" plus an opt-in per-account filter / Weg C none.
   Recommendation B (Phase 4h posture).
3. Several owners: Weg A sticky owner, then lowest id / Weg B round robin /
   Weg C explicit choice in the enqueue dialog, default sticky.
   Recommendation A for sweep and API default plus C's explicit field.
4. No known owner: Weg A automatic chain through the accounts on "not
   considered" (up to N logins per app per sweep night) / Weg B no chain,
   the job ends with a hint to set the account. Recommendation B.
5. Existing session: Weg A moved to account 1 automatically,
   `steam_library_steamid` copied into it, editable / Weg B operator logs
   in anew. Recommendation A.
6. Owned-but-never-cached games as a third sweep source: Weg A no /
   Weg B opt-in setting. Recommendation A (separate decision).

### Review weights (user rule 2026-10-04)

- **full**: api, security-relevant runner/helper code, app, and (proposed)
  web. Coder -> reviewer -> fixes -> second pass before the commit.
- **short**: docs, CI, image build and deploy tests, when the branch CI
  (or `dev.sh verify` for verify-stack changes) is green. One review
  pass; nitpicks are not fixed in place but collected in the carry-over
  list below and taken into the next package of the same area.
- Status reports to the user only on decisions, errors and finished
  milestones (a package committed, a track done).
- About three agents at a time; packages are at most 1-2 h each.

### D2 packages (AUTH-*)

Docs, may run before the tag:

- **AUTH-0a** ADR-0018 final draft. Apply the eight review should-fixes
  and the per-account status amendment; the open-question list becomes
  the five items above. Files: `docs/adr/0018-steam-qr-login-helper.md`.
  Deps: none. Review: short.
- **AUTH-0b** ADR-0019 round-2 review and fixes. Files:
  `docs/adr/0019-multiple-steam-accounts.md`. Deps: none. Review: short
  (it is the second pass).
- **AUTH-0c** Both ADRs to Accepted with the user's decisions recorded,
  merged to `main` as docs. The ADR-0016 freeze ends with the `v0.1.0`
  tag; if any D2 code were pulled ahead of it, an ADR-0016 addendum names
  the exception (not planned). Files: the two ADRs, `docs/PROJECT_PLAN.md`
  §11 item 13 D. Deps: the decisions. Review: short.

Code, after the `v0.1.0` tag:

- **AUTH-1a** Helper project. `tools/steam-login-helper/` (csproj pinned
  to SteamKit2 3.4.0 and protobuf-net 3.2.56 like SteamPrefill, a copy of
  the `UserAccountStore` contract), commands `login` (QR session, JSON
  lines on stdout, `--timeout`, SIGTERM cancel, atomic write with temp
  cleanup, `DeviceFriendlyName` from `VAULT_NAME` or a fixed string) and
  `status` (present, exp; never the token). Unit tests: file-format round
  trip, "stdout never contains the token" against a fake session. CI job:
  `dotnet build` + tests. Files: `tools/steam-login-helper/*`,
  `.github/workflows/ci.yml`. Deps: AUTH-0c (decision 2 = C#).
  Review: full (security).
- **AUTH-1b** Image build. Per decision 1: SDK build stage in
  `api/Dockerfile` (or the artifact download), binary beside SteamPrefill,
  build-time smoke (`status` on an empty dir -> `present:false`), image
  size recorded in `api/README.md`; the offline acceptance test (a
  helper-written file with a bogus far-future JWT makes SteamPrefill skip
  the "account is required" prompt and fail on the CM connection) as a
  CI script. Files: `api/Dockerfile`, `.github/scripts/`, `api/README.md`.
  Deps: AUTH-1a. Review: short (CI) when green.
- **AUTH-2a** Schema and queue. Schema v18: `jobs.run_progress_json`,
  `jobs.appid` nullable (login belongs to no app), `JOB_TYPE_STEAM_LOGIN`,
  the type filters in `claim_run`/`find_active_run`/`recover_stale_jobs`
  widened, `finish_job` clears `run_progress_json` on every terminal
  transition, a `runner_account_state` table keyed by account id (account
  1 implicit until D3) with presence, exp, seen_at. Tests. Files:
  `api/vault_api/db.py`, `jobs.py`, `api/tests/`. Deps: AUTH-0c.
  Review: full (api).
- **AUTH-2b** Runner login branch. `prefill_runner.py`: `steam_login`
  spawns the helper (stdin closed), parses JSON lines into progress and
  heartbeat, records a result without raw output, cancels via
  `stop_request`, removes stale `account.config.*.tmp` on start, refreshes
  `runner_account_state` every 60 s idle and after every job via `helper
  status`. Fake helper stub replaying a stdout fixture (URL rotation,
  failure, timeout). Files: `prefill_runner.py`, `prefill_queue.py`,
  `api/tests/stub_login_helper.py`, tests. Deps: AUTH-1a (contract),
  AUTH-2a. Review: full (security).
- **AUTH-2c** Worker side. `worker.py`: `steam_login` branch (no app
  status, no depot scan, own hand-off and result decoding), the login
  result codec in `prefill_queue.py`, `runner_lost`/cancel paths tested.
  Files: `worker.py`, `prefill_queue.py`, tests. Deps: AUTH-2a.
  Review: full (api).
- **AUTH-3a** Routes. New router `routers/steam_session.py`: `POST
  /v1/steam/session/login` (202, 409 one-at-a-time, 409 subprocess mode,
  403 read-only, cooldown), `GET /v1/steam/session/login/{id}` (state,
  challenge URL or null on every terminal state), `DELETE` (cancel), `GET
  /v1/steam/session` (present, expires_at, checked_at). Env-only
  `VAULT_STEAM_LOGIN_COOLDOWN_SECONDS` forwarded in compose and
  `.env.example`. `GET /v1/jobs` hides `steam_login` rows (decision 5).
  Tests with a fake runner: every guard, no token-shaped string in any
  response or log line. Files: `routers/steam_session.py`, `config.py`,
  `deploy/compose.yaml`, `deploy/.env.example`, tests. Deps: AUTH-2a..2c.
  Review: full (api, security).
- **AUTH-4a** Web QR encoder. Vendored, build-free `web/js/lib/qr.js`
  (byte mode, error correction M, canvas or SVG output), tests against
  known vectors, CSP unchanged (`script-src 'self'`). Files:
  `web/js/lib/qr.js`, `web/tests/`. Deps: none. Review: full (web,
  proposed).
- **AUTH-4b** Web Settings "Steam session" block. `buildSteamSessionBlock`
  (status line, "Log in with the Steam app", polling, QR render, terminal
  command as fallback, Retry), `api.js` client, the Downloads
  `not_logged_in` hint links to Settings, demo-mode fixtures for every
  state, tests. Files: `web/js/views/settings.js`, `web/js/api.js`,
  `web/js/lib/job-failure.js`, web css, demo data, `web/tests/`. Deps:
  AUTH-3a, AUTH-4a. Review: full (web, proposed).
- **AUTH-5** Docs. ADR-0004 addendum 5 (the reworded decision 1), threat
  model §3 and §7, `SECURITY.md`, `README.md` "Is my Steam account
  safe?", `deploy/README.md` first run, `api/README.md` queue mode, plan
  checkboxes. Files: those. Deps: AUTH-3a, AUTH-4b. Review: short.
- **AUTH-6a** Android data layer. API client for the three routes, a
  repository and status model, tests. Files: `app/.../net/`, `repo/`,
  tests. Deps: AUTH-3a. Review: full (app).
- **AUTH-6b** Android Settings block. Steam session status, login flow
  with QR rendering (dependency decision inside the package: a pinned
  `zxing-core` vs a small Kotlin encoder, documented), terminal fallback,
  demo mode. Files: `app/.../ui/settings/`, tests. Deps: AUTH-6a,
  AUTH-4b (copy parity). Review: full (app).

### D3 packages (MULTI-*)

All after ADR-0019 is Accepted (AUTH-0c) and AUTH-2a/2b exist (login per
account rides the login job).

- **MULTI-1a** Schema and accounts API. Schema v19: `steam_accounts`
  (label, steamid64, persona_name, created_at, last_login_at, removed_at),
  `jobs.account_id`, row 1 inserted by the migration; `GET/POST/PATCH/
  DELETE /v1/steam/accounts` (DELETE marks and enqueues
  `steam_account_remove`, 409 while a job references the account), `POST
  /v1/prefill` optional `account_id`. Tests. Files: `db.py`, `jobs.py`,
  `routers/steam_accounts.py`, tests. Deps: AUTH-0c, AUTH-2a.
  Review: full (api).
- **MULTI-1b** Runner per-account layout and migration. Reconcile
  `accounts/<id>/` against the table at start and on the first claimed job
  for an id, hash-compared binary copy, executable path from `account_id`,
  the removal job (directory and session file deleted, worker deletes the
  row), the legacy move into `accounts/1/` exactly once. Stub binary per
  account dir, tests. Files: `prefill_runner.py`, `prefill.py`,
  `worker.py`, `api/tests/`. Deps: MULTI-1a. Review: full (security).
- **MULTI-1b2** Deploy tests. verify-stack: two account directories with
  the real binary, each reaching the credential-free "account is
  required" prompt from its own directory, the removal job end to end,
  step 3h still one `Config/` mount. Files: `deploy/tests/verify-stack.sh`.
  Deps: MULTI-1b. Review: short (CI) when `dev.sh verify` is green.
- **MULTI-1c** Selection rule. Owner resolution from the relay per
  account (`RelayCache`), sticky owner from job history, the decided
  fallback (question 4), sweep attribution in `scheduler.py`, the explicit
  `account_id` honoured. Tests; with question-4 Weg A a mutation check
  that the chain stops after one attempt without the "not considered"
  signal and keeps the first attempt's `run_before_json`. Files:
  `jobs.py`, `scheduler.py`, `steam_relay.py`, a new `account_select.py`,
  tests. Deps: MULTI-1a; API-FIX-3 (done). Review: full (api).
- **MULTI-1c2** Login per account. The `steam_login` job carries
  `account_id`, the helper targets `accounts/<id>/Config/`,
  `runner_account_state` refreshed per account directory, `GET
  /v1/steam/accounts` returns each account's session status,
  `last_login_at` stamped. Tests. Files: `prefill_runner.py`,
  `routers/steam_session.py`, `routers/steam_accounts.py`, tests. Deps:
  AUTH-2b, MULTI-1b. Review: full (security).
- **MULTI-1d** Web library union and owner hint. Loader fetches one owned
  list per library SteamID (decision 1), `mergeOwnedLibrary` merges the
  union with `owners`, the decided hint and filter (decision 2), header
  count, single-list behaviour against an older vault-api. Tests. Files:
  `web/js/lib/owned-library.js`, `web/js/owned-singleton.js`,
  `web/js/views/library.js`, tests. Deps: MULTI-1a. Review: full (web).
- **MULTI-1e** Web Settings "Steam accounts" block. Replaces "Steam
  library": one row per account (label, SteamID64, persona, session
  status, Log in via the AUTH-4b flow, Remove), "Add account", the
  enqueue dialog's account field (decision 3). Tests. Files:
  `web/js/views/settings.js`, `web/js/views/library.js` (dialog),
  `web/js/api.js`, tests. Deps: MULTI-1c2, MULTI-1d, AUTH-4b.
  Review: full (web).
- **MULTI-1f** Docs. `deploy/README.md` first run per account and the
  terminal fallback path, `api/README.md` queue mode, threat model §3
  (N session files) and §5 (one relay key, N libraries), ADR-0019 to
  Accepted with the decisions, plan. Deps: MULTI-1e. Review: short.
- **MULTI-1g1** Android data layer and union merge. Accounts API client
  and repository, `LibraryMerge.kt` becomes union-aware (web twin of
  MULTI-1d pinned by string equality where the web copy is the source).
  Tests. Deps: MULTI-1a, AUTH-6a. Review: full (app).
- **MULTI-1g2** Android Settings accounts block and hint. Deps:
  MULTI-1g1, MULTI-1e. Review: full (app).

### Order and parallelism

1. Now (docs): AUTH-0a and AUTH-0b in parallel; then the user's
   decisions; then AUTH-0c.
2. After the tag: AUTH-1a, AUTH-2a and AUTH-4a in parallel; then AUTH-1b,
   AUTH-2b and AUTH-2c; then AUTH-3a; then AUTH-4b and AUTH-6a; then
   AUTH-5 and AUTH-6b. D2 done = AUTH-5 merged.
3. D3: MULTI-1a; then MULTI-1b and MULTI-1c; then MULTI-1b2, MULTI-1c2 and
   MULTI-1d; then MULTI-1e; then MULTI-1f and MULTI-1g1; then MULTI-1g2.

### Review carry-over (nitpicks collected per area)

Filled by the orchestrator after each short review; the next package of
the same area takes the list.

- api: (none yet)
- web: (none yet)
- app: (none yet)
- docs / CI / deploy tests: ADR-0017 "Prove before building" intro and
  the README page already read "two event-log lines" (done); the two
  `$`-anchored PoC log analyzers (`poc/steam-client-test/analyze.ps1`,
  `poc/steamprefill/verify.ps1`) do not match the access-log format since
  CORE-FEAT-1b2 (documented gap, poc/ frozen; decide before any PoC rerun).

## Suggested order

1. DOCS-FIX-3 lands before `v0.1.0` (it is in item 13 B).
2. Write the ADR drafts for D1, D2 and D3, review them, and settle the
   open questions with the user.
3. D1 first: it is the widest benefit, and the production measurement is
   cheap.
4. D2, then D3 on top of it; the package cut is in "D2 and D3: work
   packages" above.
5. D7 and D8 after the `v0.1.0` tag (user decision 2026-10-04); D7 first,
   it is the only one with a measured harm on the production line.
