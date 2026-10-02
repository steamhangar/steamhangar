# ADR-0018: Steam login by QR code through a helper in the runner container

Date: 2026-10-02
Status: Proposed (draft; amends ADR-0004 decision 1 once accepted; awaits
user decisions on the open questions; code only after tag `v0.1.0`,
ADR-0016)

Roadmap item D2 (AUTH-FEAT-1), `docs/handover/post-0.1.0-roadmap.md` and
`docs/PROJECT_PLAN.md` §11 item 13 D. Every claim below names its source;
claims taken from the handover without re-verification in this session say
so. SteamKit2 and SteamPrefill sources were read at the pinned tags
(SteamKit2 3.4.0, SteamPrefill v3.7.1) on GitHub; no binary was fetched and
Steam was not contacted.

## Context

**What the rollout taught.** SteamPrefill has no QR login. Verified in the
v3.7.1 source: `Handlers/Steam/Steam3Session.cs::GetAccessTokenAsync` calls
`BeginAuthSessionViaCredentialsAsync` with `Username`, `Password`,
`IsPersistentSession = true` and a console authenticator; nothing in
`Program.cs` or that file references the QR session type (v3.7.2: per
handover, not re-verified here). The refresh token is stored in
`Settings/UserAccountStore.cs` (`AccessToken = pollResponse.RefreshToken`),
a protobuf-net contract with field 3 `CurrentUsername`, field 4 `SessionId`
(a random `uint` in `0..16383`) and field 5 `AccessToken`, written to
`Config/account.config`, where `Config` is
`Path.Combine(AppContext.BaseDirectory, "Config")`
(`Settings/AppConfig.cs`). SteamPrefill reads the token's JWT `exp`
(`UserAccountStore.AccessTokenIsValid`: "Tokens seem to be valid for ~6
months") and prompts for a password when the token is missing or expired;
vault-api runs it with stdin closed, so that prompt becomes the
`not_logged_in` job failure (`api/vault_api/prefill.py::
NOT_LOGGED_IN_MARKERS` matches `UserAccountStore.PromptForUsernameAsync`'s
text).

The only login path today is account name plus password plus Steam Guard,
typed into a terminal attached to the `vault-runner` container
(`deploy/README.md` "First run: the one-time SteamPrefill login",
`api/README.md` "Login prerequisite"; the web UI's `not_logged_in` hint
shows the same command, `web/js/lib/job-failure.js::LOGIN_COMMAND`).
Steam's anti-phishing location check blocked an app approval during the
rollout when the phone was in another country than the server; typing the
5-character Steam Guard code is reported to bypass it (per handover,
community reports, no Valve documentation). Upstream is unlikely to take a
QR patch: the maintainer declined multi-account (#113), credential storage
(#303) and non-interactive select-apps (#427) (per handover, not
re-verified here).

**What ADR-0004 claims.** Decision 1 says "QR login via the Steam Mobile
App is the recommended and documented path (the password is never typed on
the server at all)". No SteamPrefill release does that; DOCS-FIX-3
(`docs/PROJECT_PLAN.md` §11 item 13 B) corrects the claim before `v0.1.0`
and points here. This ADR is the design that makes the original intent
true.

**Why QR.** SteamKit2 3.4.0 offers `BeginAuthSessionViaQRAsync`
(`SteamKit2/Steam/Authentication/SteamAuthentication.cs`): Steam issues a
challenge URL, the user scans it in the Steam app and approves there; no
password is typed anywhere, and the result is the same refresh token the
credentials flow yields (`Authentication/AuthPollResult.cs`: `AccountName`,
`RefreshToken`, `AccessToken`, `NewGuardData`; its doc comment says the
refresh token "can be provided to `SteamUser.LogOnDetails.AccessToken`",
which is how SteamPrefill logs on with the stored token,
`Steam3Session.cs::AttemptSteamLogin`).

## Design (proposed)

### 1. The helper

A small C# console program, `steam-login-helper` (name provisional), on
SteamKit2 pinned to the version SteamPrefill pins
(`SteamPrefill/SteamPrefill.csproj`: `SteamKit2 3.4.0`, `protobuf-net
3.2.56`, `net8.0`). It runs in the `vault-runner` container, the one with
`vault-steamprefill:/opt/steamprefill/Config` mounted
(`deploy/compose.yaml::vault-runner`), as uid 101 like SteamPrefill.

The api image ships no .NET runtime or SDK: `api/Dockerfile` installs only
`libicu76` on `python:3.13.14-slim-trixie` and copies the self-contained
SteamPrefill binary (upstream csproj: `PublishSingleFile`,
`PublishTrimmed`, `RuntimeIdentifiers linux-x64;linux-arm64;...`). The
helper must ship the same way: a self-contained, trimmed, single-file
build per architecture from a tiny project in the repo
(`tools/steam-login-helper/`, name provisional). Image cost: the
SteamPrefill zip is 12,095,541 bytes (`api/Dockerfile`'s checksum note);
the helper has fewer dependencies (no console framework), so the estimate
is 15-30 MB uncompressed per architecture (inference; the work package
measures it and records the number in `api/README.md`).

Contract of `steam-login-helper login`:

- Calls `BeginAuthSessionViaQRAsync(new AuthSessionDetails {
  IsPersistentSession = true, DeviceFriendlyName = "SteamHangar
  vault-runner (<hostname>)" })`, leaving `PlatformType` and `ClientOSType`
  at SteamKit2's defaults (`Authentication/AuthSessionDetails.cs`:
  `k_EAuthTokenPlatformType_SteamClient`, `Utils.GetOSType()`), the same
  defaults SteamPrefill's own login uses, so the token is issued for the
  platform SteamPrefill later logs on as (inference: tokens are
  platform-bound; the acceptance test checks it).
- stdout is JSON lines only: `{"event":"challenge","challenge_url":...}`
  at start and on every `QrAuthSession.ChallengeURLChanged`
  (`Authentication/QrAuthSession.cs`); `{"event":"status","state":
  "waiting_for_approval"}` heartbeats; `{"event":"ok","account_name":...}`
  on success; `{"event":"error","reason":"<EResult|timeout|cancelled>"}`
  on failure. The token never appears on stdout or stderr; the helper
  catches `AuthenticationException` and prints the `EResult` name only.
- Polls with `PollingWaitForResultAsync(cancellationToken)`
  (`Authentication/AuthSession.cs`; a QR session's confirmation type is
  `DeviceConfirmation`, a plain poll loop at the Steam-supplied
  `PollingInterval`). `--timeout` (default 5 min) and SIGTERM cancel it.
- On success it writes `Config/account.config` in the `UserAccountStore`
  wire layout: field 3 = `AccountName`, field 4 = a random `uint` in
  `0..16383` (SteamPrefill's range; it becomes `LogOnDetails.LoginID`,
  `Steam3Session.cs::ConfigureLoginDetailsAsync`), field 5 =
  `RefreshToken`. Temp file in `Config/`, mode 0600, renamed over the
  target. `NewGuardData` is dropped: the store has no field for it and
  the QR flow does not need it (`AuthPollResult.cs`: email Steam Guard).
- `steam-login-helper status` reads the file and prints
  `{"present":true,"account_name":...,"exp":<unix>}` or
  `{"present":false}`; `exp` is the JWT claim `AccessTokenIsValid` reads.
  Never the token.

### 2. Transport: a login job in the existing queue

In queue mode vault-api and the runner share only the SQLite database and
two volumes; the runner serves no HTTP and holds no API key (ADR-0012 §2,
`api/vault_api/prefill_runner.py::main`, `require_api_key=False`). The
login therefore travels like a prefill: a job of a new type `steam_login`
next to `jobs.py::JOB_TYPE_PREFILL` and `JOB_TYPE_GC`.

- `POST` (section 3) inserts the job `queued`. `PrefillWorker` claims it
  and hands it off with `jobs.handoff_run`; the runner loop
  (`prefill_runner.py::PrefillRunner.run_forever`) claims it with
  `jobs.claim_run` and branches on `type`: `prefill` runs SteamPrefill as
  today, `steam_login` spawns the helper, stdin closed, reading its stdout
  line by line.
- Progress goes into one new nullable column `jobs.run_progress_json`
  (`{"state":..., "challenge_url":...}`), rewritten on every helper line
  alongside the heartbeat (`jobs.record_run_heartbeat`). vault-api's
  `await_run_result` loop (`api/vault_api/prefill_queue.py`) and the lease
  rules apply unchanged: a dead runner fails the login as `runner_lost`.
- On finish the runner records the result via `jobs.record_run_result` and
  sets `run_progress_json` to `NULL` in the same statement, so no challenge
  URL outlives its login. The result carries `success`, `failure_reason`
  and at most the `account_name`; never raw helper output.
- Cancellation reuses `jobs.stop_request` (`jobs.read_stop_request`): the
  runner reads `cancel` on its tick and SIGTERMs the helper.
- Schema bump: one nullable column. `jobs.appid` is `NOT NULL`
  (`api/vault_api/db.py`) and every existing job type is per app
  (`jobs.py`'s GC insert passes an appid too); a login belongs to no app,
  so AUTH-3 either stores `0` as a sentinel or makes the column nullable.
  An implementation detail, not a user decision.

**Why a challenge URL may sit in the database and the token must not.** A
challenge URL identifies a pending login attempt. By itself it grants
nothing, it is useless once approved, rotated or timed out, and it has to
reach the browser anyway, so vault-api must carry it. The refresh token is
a long-lived bearer credential for the whole account. The database is
backed up (`deploy/README.md` volume table), mounted into the LAN-facing
control-plane container, and exposed through `GET /v1/jobs/{id}`'s log
excerpt; none of those may ever hold it.

**Mutual exclusion.** One worker and one runner, each one job at a time
(`worker.py` module docstring, ADR-0012 §1): while a login runs no prefill
runs, and a login queued behind a running prefill waits (open question 6).

### 3. vault-api

Two routes under `require_api_key` (`api/vault_api/auth.py`), in a router
module beside `routers/steam.py`:

- `POST /v1/steam/session/login` -> `202 {"login_id": <job id>}`. `409`
  if a login is `queued` or `running` (one at a time); `409` with a
  distinct detail in subprocess mode (section 6); `403` when
  `VAULT_SETTINGS_READONLY=1`, the guard `routers/settings.py::
  patch_settings` uses. A login writes a credential into the stack, and
  unlike the relay key (ADR-0009 addendum 2026-09-30) it has a non-API
  fallback, the terminal, so gating it is consistent;
  `docs/security/threat-model.md` §7 then lists this route under what
  read-only prevents. A failed or cancelled login starts a cooldown
  (default 30 s, env-tunable) before the next `POST`, bounding how often
  the stack opens auth sessions toward Steam.
- `GET /v1/steam/session/login/{id}` -> `{"state": queued |
  waiting_for_approval | succeeded | failed | cancelled | expired,
  "challenge_url": <string or null>, "error": <reason or null>}`, read
  from the job row. `DELETE` on the same path cancels via `stop_request`.
- `GET /v1/steam/session` -> `{"present", "expires_at", "checked_at",
  "runner_seen_at"}`, plus `"account_name"` only if open question 3 says
  so.

Timeout: the helper's `--timeout` plus the existing
`VAULT_RUNNER_LEASE_TIMEOUT_SECONDS` net. ADR-0016: the freeze holds until
the `v0.1.0` tag (`docs/PROJECT_PLAN.md` §11 item 13); if any freeze rule
is still in force when this lands, an addendum names the routes and the
schema bump as the exception.

### 4. Web UI

`web/js/views/settings.js::buildSteamSection` ("Steam identity") and
`buildSteamLibraryBlock` ("Steam library") set the pattern: a section
builder, a status line, buttons wired through `web/js/api.js`. A third
block, `buildSteamSessionBlock` ("Steam session"):

- Status line from `GET /v1/steam/session`: "present (valid until
  <date>)", "missing", "expired", or "unknown (runner not reporting)".
- Button "Log in with the Steam app": `POST`, then poll the `GET`; render
  the challenge URL as a QR code client-side with a small vendored encoder
  under `web/js/lib/` (no build step, no external service: the CSP is
  `script-src 'self'` and `connect-src 'self'`,
  `api/vault_api/webui.py::_CSP`). Re-render on every URL change; show
  the terminal state and a Retry.
- The copyable terminal command stays below it as the fallback (section
  5), reusing `job-failure.js::LOGIN_COMMAND`; the `not_logged_in` hint in
  Downloads (`job-failure.js::HINTS`) gains a line pointing to Settings.

**Session status comes from the runner.** vault-api has no `Config/` mount
in queue mode (`deploy/compose.yaml`, vault-api volumes comment:
"SteamPrefill's Config/ ... is NOT mounted here anymore"). ADR-0012's
heartbeat is per job (`jobs.run_heartbeat_at`); there is no standalone
runner status row today. Proposal: one single-row table `runner_state`
(the `CHECK (id = 1)` device `schedule_state` and `steam_relay_key` use in
`db.py`) that the runner refreshes every 60 s while idle and after every
job, with `runner_id`, `seen_at`, `session_present`, `session_exp`,
`session_account`, `checked_at`, obtained from `steam-login-helper status`.
One parser for the file, in the language that owns the format; a Python
wire-format decoder for three fields is the alternative if spawning the
helper per tick proves too costly.

### 5. Fallback: the terminal login stays

`SteamPrefill select-apps` through `docker compose exec` remains documented
and shown in the UI: the escape hatch for the location check (a Steam Guard
code typed in the terminal, per handover), for an account without the
Steam app at hand, and for `VAULT_PREFILL_MODE=subprocess`.

### 6. Subprocess mode: QR login is queue-mode only (decision)

In subprocess mode SteamPrefill and `Config/` live in vault-api's
container, whose egress is locked to allowlisted hosts via the proxy
(ADR-0011); the helper would have to run inside the control-plane container
and reach Steam's CM network from there, the opposite of what ADR-0012
split the runner out for. The shipped compose does not even mount `Config/`
into vault-api anymore. Decision: `POST` answers `409` with "QR login needs
VAULT_PREFILL_MODE=queue" and the UI shows only the terminal command.

## Security: what changes in ADR-0004 decision 1

Decision 1's "there is deliberately NO code path that accepts, forwards, or
stores Steam credentials" becomes: **no code path accepts or forwards a
Steam password; the only code touching the refresh token is the helper,
which writes SteamPrefill's own session file and never prints, logs or
transmits it.** Concretely:

- The token never crosses vault-api's HTTP layer, its logs or the
  database: the helper's stdout carries URLs and states, the runner stores
  parsed fields rather than raw output, so the job's log excerpt is
  structurally unable to contain it.
- At rest it sits where it sits today: `vault-steamprefill`, mounted only
  in `vault-runner`, readable by uid 101 there (the helper runs as that
  uid, like SteamPrefill). vault-api runs as the same uid but has no mount
  of that volume; reading it from elsewhere needs host access, already out
  of scope (`threat-model.md` §9, "Physical access to the host").
- A challenge URL is not a credential, but it carries authority: whoever
  approves it in the Steam app logs **their** account into this server.
  Hence API key only, one active login, a short helper timeout, the URL
  cleared on finish, and the cooldown.
- Token scope and lifetime are unchanged: a persistent refresh token with
  Steam-client platform type, valid about six months (`UserAccountStore.cs`
  comment; "about 200 days" per handover). The exposure equals today's.
- `DeviceFriendlyName` names the device so the operator can recognise and
  revoke it in Steam's authorised-devices list (Valve's UI, not verified
  here).
- `threat-model.md` §3 must then say: no password is typed into anything
  in this repository; the helper is the one code path that holds the token
  in memory and writes it; the challenge URL's authority and its controls.
  §7 lists the login route under the read-only lock. `SECURITY.md`'s
  pointer paragraph stays valid; README's "Is my Steam account safe?" is
  reworded.
- Who may trigger a login is, today, anyone with the one API key. D5
  (scoped keys, `docs/PROJECT_PLAN.md` §7 Phase 6) is the future answer:
  login start belongs in the destructive scope.

## Risks

- **A private serialization format.** `UserAccountStore` is SteamPrefill's
  internal class; field numbers can change in any release. Mitigation: pin
  SteamPrefill and SteamKit2 together, re-read `UserAccountStore.cs` and
  `Steam3Session.cs` on every bump (the 3.7.2 bump in §11 item 13 C is the
  first), and run the acceptance test below.
- **Maintainer stance.** Upstream will not carry this; the helper is ours
  to maintain for as long as SteamPrefill is the prefill engine.
- **Location check.** A QR approval from a phone far from the server can
  be blocked exactly like the app approval was; the terminal fallback is
  the answer, and the UI must say so.
- **Acceptance test, a hypothesis to verify.** In CI with egress blocked,
  write an `account.config` with the helper carrying a username and a
  syntactically valid JWT whose `exp` lies far in the future, then run the
  image's SteamPrefill with stdin closed. Expected: neither "A Steam
  account is required" nor "Please enter your Steam account name" appears
  (`AccessTokenIsValid` true, username present) and it fails on the CM
  connection instead. That proves the file parses and the three fields
  land where SteamPrefill reads them, without a real token. Not verified:
  whether SteamPrefill reaches the login step before something else fails
  offline, and whether Steam accepts a QR-issued token for a CM logon
  (SteamKit2's QR sample does so per handover).
- **Platform binding.** If a token issued with other `PlatformType` or
  `ClientOSType` values is refused at logon, the helper must mirror
  SteamPrefill's defaults exactly; the design does, the test confirms it.
- **Two writers of one file.** SteamPrefill rewrites `account.config` only
  after obtaining a new token (`GetAccessTokenAsync`) and clears it on
  `AccessDenied`; the helper writes by rename. Serialising login and
  prefill jobs (section 2) removes the overlap.

## Open questions for the user

1. **Helper packaging.** Weg A: a build stage in `api/Dockerfile` (pinned
   .NET SDK image, cross-compiled per RID, copied next to SteamPrefill;
   same image, same uid, zero compose change). Weg B: a separate tiny image
   and service sharing the `Config/` volume (own uid, but a third container
   the runner must reach, and the file's reader set does not shrink).
   Recommendation: A, because SteamPrefill reads the token file as uid 101
   anyway, so B buys no isolation and adds a transport.
2. **Transport.** Weg A: the job queue as in section 2. Weg B: a status
   file in the shared `vault-steamprefill-home` volume written by the
   runner and read by vault-api. Recommendation: A; B is a second channel
   with its own staleness story, and ADR-0012 §2 already rejected an HTTP
   sidecar.
3. **What Settings shows.** Weg A: present / missing / expired and the
   expiry date. Weg B: additionally the Steam account name. Recommendation:
   A. The login name is half of a credential pair, the Settings screen is
   shared in a household (`docs/PROJECT_PLAN.md` §7 Phase 4h privacy
   stance), and the "Steam identity" block already shows persona and
   SteamID64 as the public identity. D3 can label sessions by persona.
4. **QR only, or QR plus the terminal command in the UI.** Weg A: QR only.
   Weg B: both, the command as a copyable fallback. Recommendation: B; the
   location check makes the fallback necessary, and the handover assumes
   it.
5. **Where the helper is built.** Weg A: inside the image build (the
   Publish workflow already builds per platform,
   `.github/workflows/publish.yml`). Weg B: a CI job publishing per-RID
   artifacts the Dockerfile downloads with a pinned checksum, like
   SteamPrefill itself. Recommendation: A; B only if the SDK stage makes
   the build too slow.
6. **A login requested while a prefill runs.** Weg A: it waits behind the
   running job, the UI says so and offers Pause. Weg B: the runner runs the
   helper beside SteamPrefill in a second thread. Recommendation: A, to
   keep the one-job-at-a-time invariant every lease and crash rule in
   ADR-0012 rests on.

## Tests (planned)

- API: the routes with a fake runner that writes `run_progress_json` and
  results into the test database; `409` on a second login, `403` in
  read-only mode, `409` in subprocess mode, the cooldown, `NULL` URL after
  finish, no token-shaped string in any response or log line.
- Runner: the login branch against a fake helper (a script replaying a
  stdout JSON fixture with a URL rotation and a failure), heartbeat and
  `runner_lost`, cancellation via `stop_request`, the `runner_state` row.
- Helper: a round trip of the file format (write, then parse with
  protobuf-net into a copy of the `UserAccountStore` contract) and a
  "stdout never contains the token" assertion against a fake session.
- CI: the helper builds for both RIDs; the offline acceptance test above
  runs against the built image.
- Web: `node --test` for the Settings block's states (present, missing,
  expired, unknown, in progress, failed), the QR encoder against known
  vectors, and the existing drift test on `LOGIN_COMMAND`.

## Consequences and honest limits

- ADR-0004 decision 1 gets an addendum with the reworded claim; threat
  model §3 and §7, `SECURITY.md`, `README.md`, `deploy/README.md` and
  `api/README.md` change with it.
- The stack gains a .NET program of its own and a second pin to keep in
  step with SteamPrefill; every SteamPrefill bump includes a format
  re-check.
- The token's exposure does not change, and QR does not defeat the
  location check. What changes: no password is ever typed into the server,
  and no terminal is needed on the happy path.
- One Steam session per install remains (D3 builds on this).
- Android parity is a later package; the app keeps the terminal hint
  until then.

## Work package split (after tag `v0.1.0`, each at most 1-2 h)

- **AUTH-1** helper project, file-format round trip, stdout contract, the
  image build stage, the offline acceptance test.
- **AUTH-2** runner: `steam_login` branch, `run_progress_json`,
  `runner_state` row and status cadence, tests with the fake helper.
- **AUTH-3** vault-api: the routes, read-only and subprocess guards,
  cooldown, schema bump, tests with the fake runner; the ADR-0016 note if
  still needed.
- **AUTH-4** web: Settings "Steam session" block, vendored QR encoder,
  polling, the Downloads hint link, tests.
- **AUTH-5** docs: ADR-0004 addendum, threat model §3/§7, `SECURITY.md`,
  `README.md`, `deploy/README.md`, `api/README.md`, plan checkboxes.
- **AUTH-6** (later) Android parity.
