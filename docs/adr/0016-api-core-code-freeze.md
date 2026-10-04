# ADR-0016: api/ and core/ code freeze before the first release candidate

Date: 2026-10-01
Status: Accepted (user decision 2026-09-29)

## Context

The pre-release security review (WP 5.3 review half, `docs/PROJECT_PLAN.md`
§7 Phase 5 and §11 item 6) waits on an api/core code freeze, so the
reviewed code is the code that ships. On 2026-09-29 the user decided to
freeze after the pre-freeze project review and its fix packages (§11
item 12). Before the freeze the user pulled in two more items: download
throttling (WP TH-1a/TH-1b/TH-1-FIX, ADR-0015) and the web
connection-lost indicator (WP WEB-FIX-2). All of them have passed review.

## Decision

- **Frozen:** `api/`, `core/` and `dns/`. The user decision named api/ and
  core/; dns/ is the orchestrator's extension (it ships in the same image
  set and was reviewed together with core), not part of that decision.
- **Allowed in frozen code:** fixes from the WP 5.3 security review, fixes
  from the first real-world rollout, fixes for red CI, and docs.
- **Not frozen:** `web/`, `app/`, `agent/` and `deploy/`. They stay open
  for fixes; no new features before the release candidate without a user
  decision.
- **Freeze commit:** `92e95be` on branch `wp/pre-freeze-review`. It
  becomes `main` once the user merges the PR.
- **Exit:** the tag `v0.1.0-rc1`, pushed after the security review's fixes
  have landed.
- **Model rule:** the WP 5.3 second review pass uses the strongest
  available model (the "[Fable]" marker names a boundary, not a model,
  since WP R-0). On 2026-10-01 that is Opus 5.5, because Fable was
  unavailable and the user decided not to wait.

## Consequences

- The security review can start; it reviews `92e95be` (or `main` after the
  merge, if nothing in the frozen paths changed in between).
- Any change to a frozen path that is not one of the allowed classes needs
  a user decision and a note here.
- Features for web/app/agent/deploy wait until after `v0.1.0-rc1` unless
  the user decides otherwise.

## Addendum 2026-10-01 — freeze exception: `steam_library_steamid` (WP API-FEAT-1)

User decision, 2026-10-01: the api/ freeze opens for exactly one new
setting, `steam_library_steamid`, and nothing else. Why: there should be one
SteamID per vault, not one per device. The web library (WP WEB-FEAT-1) reads
it now, and the app reads it later, both from `GET /v1/settings`, instead of
each device storing its own.

Scope of the exception, all in WP API-FEAT-1:

- one `OVERRIDABLE_SPECS` entry (`api/vault_api/settings_store.py`) and one
  `Settings` field with its env source `VAULT_STEAM_LIBRARY_STEAMID`
  (`api/vault_api/config.py`);
- no new grammar: the value must pass `steam_relay.valid_steamid64`, the
  relay's existing check (17 ASCII digits, individual-account range);
- the forwarding line in `deploy/compose.yaml` and the stanza in
  `deploy/.env.example` (deploy/ is not frozen; listed for completeness).

`PATCH` accepts the value only as a JSON string: a JSON number gets `422`,
because a JavaScript sender has already rounded a 17-digit id to a
different account.

No route, schema or relay behaviour changed. Every other frozen-path change
still needs its own user decision and note here.

## Addendum 2026-10-02 — freeze exception: CGNAT port exhaustion during prefill (WP CORE-FIX-2, stage 1)

(Named CORE-FIX-2 because CORE-FIX-1 is the pre-freeze core + dns review
package, commit 30151f8.)

User decision, 2026-10-02: "RC 6 erst wenn der Bug hier mit dem Download
gefixt ist" (no rc6 until the download bug is fixed). The api/ and core/
freeze opens for this one bug fix.

The bug, from the production rollout on a DS-Lite line: vault-core logged
57845 upstream 502s (`connect() failed (113: Host is unreachable) while
connecting to upstream`, to Valve CDN addresses) against 4029 200s in about
two minutes. A single request always worked; from another container on the
same host 50 parallel new connections worked and 200 failed about half the
time; the router logged an "ICMP Flood" from the DS-Lite AFTR. The
carrier-grade NAT had run out of port mappings and answered new connections
with ICMP host-unreachable (RFC 6888 REQ-11). Every miss is a new upstream
connection (variable `proxy_pass`, no keepalive pool), SteamPrefill 3.7.1
keeps 30 requests in flight and re-requests failures at once, and
vault-core retried each connect error (`proxy_next_upstream_tries 3`: up to
three attempts, i.e. two retries).

Scope of the exception (stage 1), all in WP CORE-FIX-2:

- api/: SteamPrefill gets its hidden `--max-threads N` flag on every
  prefill, in subprocess and queue mode. N comes from the new env-only
  setting `VAULT_PREFILL_MAX_THREADS` (`api/vault_api/config.py`, default 8,
  strict whole number 1..64, invalid values refuse to boot). Not a
  `PATCH /v1/settings` key, so no route or schema changes. `api/Dockerfile`
  probes the pinned binary for the flag at build time.
- core/: `@miss` no longer retries on `error` and allows one retry
  (`proxy_next_upstream timeout http_502 http_503 http_504`,
  `proxy_next_upstream_tries 2`), pinned by
  `core/docker/check-config-drift.sh` step 2c.
- deploy/ (not frozen, listed for completeness): forwarding on vault-api and
  vault-runner, `.env.example`, README troubleshooting row, verify-stack
  checks.

Not in this exception: an upstream keepalive pool for vault-core (the root
cause, since it removes the new connection per chunk). That is a separate
decision for the user. Every other frozen-path change still needs its own
user decision and note here.

## Addendum 2026-10-02 — freeze exception: upstream keepalive pool (WP CORE-FEAT-1, CGNAT fix stage 2)

User decision, 2026-10-02: roadmap item D1 (`docs/PROJECT_PLAN.md` §11
item 13 D) is pulled ahead of `v0.1.0`. ADR-0017 is accepted with the
answers 1A 2B 3A 4B 5A 6A (`docs/adr/0017-upstream-keepalive-pool.md`
"Decisions (user, 2026-10-02)"): a static env list
`VAULT_UPSTREAM_POOL_HOSTS` rendered fail-closed at container start, a
shipped seed in `deploy/.env.example`, `keepalive 8` per edge with a
ceiling of 32 idle connections in total, list changes by container
recreate, the rate cap untouched plus a CI pin, and `error` restored in
`proxy_next_upstream`. Release order: `v0.1.0-rc7` carries both
CORE-FIX-3 (TLS SNI passthrough, ADR-0020, a parallel track on its own
branch) and D1 (user decision 2026-10-03, reversing the earlier Weg B,
which had D1 as `v0.1.0-rc8`), each switchable off independently
(`VAULT_TLS_PASSTHROUGH=0`, empty `VAULT_UPSTREAM_POOL_HOSTS`). The core/ freeze opens for this one feature: stage 2
of the CGNAT fix whose stage 1 is the addendum above.

Why now: stage 1 bounds the burst but keeps the one-TCP-connection-per-chunk
cost (ADR-0017 "Context"). The 2026-10-02 measurement (ADR-0017
"Measurement") showed the Valve edge reuses a connection across requests
and keeps an idle one for at least 60 s, so a pool removes the root cause
rather than throttling around it.

Scope of the exception, per package as ADR-0017 "Work package split"
defines them, refined here:

- **CORE-FEAT-1a** (docs, not frozen): this addendum, the status pointer
  in ADR-0017, and the operator measurement page `deploy/README.md`
  "Checking that a Steam edge keeps connections alive".
- **CORE-FEAT-1b** (core/): the entrypoint hook
  `core/docker/28-vault-upstream-pool.sh`, rendering
  `vault-upstream-pool.conf` from `VAULT_UPSTREAM_POOL_HOSTS`: one
  `upstream` group per listed edge with a shared `zone`,
  `server <host> resolve max_fails=0`, `keepalive 8` and
  `keepalive_timeout 50s` (below the measured idle time of at least 60 s,
  so nginx closes first); validation against the two allowlist families,
  the ceiling, and the fail-closed self-check. With it: the include line
  in both `core/nginx/nginx.conf` and `core/docker/nginx.conf.template`,
  the empty native file `core/nginx/vault-upstream-pool.conf` (same
  contract as `vault-upstream-rate.conf`), the `core/Dockerfile` COPY and
  build-time self-check, the `core/docker/check-config-drift.sh` pins,
  and a docker-free hook test.
- **CORE-FEAT-1b2** (core/ and CI): the renders and refusals in
  `.github/scripts/verify-core-nginx.sh`; `error` restored in `@miss`'s
  `proxy_next_upstream` next to `timeout http_502 http_503 http_504`
  (decision 6A, `proxy_next_upstream_tries 2` kept, drift-guard step 2c
  updated); the two fields `upstream_addr="..."` and
  `upstream_connect_time=...` appended to `log_format vault`; the CI pin
  that `proxy_limit_rate $vault_upstream_rate;` stays in `@miss`
  (decision 5A).
- **CORE-FEAT-1c** (deploy/ tests, not frozen): the verify-stack fake edge
  with connection counting, test resolver, fallback, 508 and rate-cap
  checks in `deploy/tests/verify-stack.sh`.
- **CORE-FEAT-1d** (deploy/ and docs, not frozen): compose forwarding, the
  `.env.example` stanza with the seed and the edge-finding one-liner,
  `core/README.md` and `deploy/README.md` "Upstream keepalive pool",
  `docs/PROJECT_PLAN.md` §11 item 13 D1 ticked.

Not in this exception: port 443, any `stream` block, and TLS passthrough
(that is CORE-FIX-3, ADR-0020, its own track and its own note); anything
under api/, whose freeze stays as the stage-1 addendum left it. Every
other frozen-path change still needs its own user decision and note here.

## Addendum 2026-10-02 — freeze exception: HTTPS passthrough on port 443 (WP CORE-FIX-3)

User decision, 2026-10-02: "HTTPS-Durchreichung, Weg A (im Produkt, rc7)".
The HTTPS passthrough ships in the product, in rc7. The core/ freeze opens
for this one feature.

The trigger, from the production rollout: with the LAN DNS rewriting
`*.steamcontent.com` to vault-core (the documented DNS mode), a prefill
failed with `HttpRequestException ... while downloading manifests`.
SteamPrefill fetches depot manifests over HTTPS from the CDN host. That
host resolved to vault-core, which listened on port 80 only. The operator
worked around it by removing the runner's DNS override. The product must
still work when containers or clients use the rewriting resolver.

Scope of the exception, all in WP CORE-FIX-3 (design: ADR-0020):

- core/: a `stream {}` block in `core/nginx/nginx.conf` and
  `core/docker/nginx.conf.template` (SNI passthrough on 443 with
  `ssl_preread`, a `*.steamcontent.com` allowlist, no TLS termination);
  the new hook `core/docker/26-vault-tls-passthrough.sh` (the
  `VAULT_TLS_PASSTHROUGH` switch); a re-check of the rendered passthrough
  and an own-address answer check in `40-vault-preflight.sh`; drift pins
  in `check-config-drift.sh` (step 2e -- numbered 2d on its own branch,
  renumbered when merged after CORE-FEAT-1's step 2d --, a seventh delta
  kind, and the resolver delta now appearing twice); `core/Dockerfile` (hook, ENV
  default, `EXPOSE 443`, build-time module check).
- api/: tests only (`api/tests/test_core_fix_3_tls_passthrough.py`, one row
  in `test_p1_compose_env_defaults.py`). No application code changes.
- deploy/ and .github/ (not frozen, listed for completeness):
  `VAULT_TLS_PASSTHROUGH`, `VAULT_TLS_BIND` and `VAULT_TLS_PORT` in
  `deploy/compose.yaml` and `deploy/.env.example`, verify-stack steps 3q,
  5j and 7i, and the CI gate's new TLS checks
  (`.github/scripts/tls-sni-probe.sh`, `tls-preflight-tamper.cases`).

The HTTP cache path on port 80 is unchanged; the drift check pins it.
Every other frozen-path change still needs its own user decision and note
here.

## Addendum 2026-10-03 — freeze exception: failed prefill misread as "not considered" (WP API-FIX-3)

User decision: this bug fix goes in before `v0.1.0` (`docs/PROJECT_PLAN.md`
§11, list B, API-FIX-3). The api/ freeze opens for this one bug fix.

The bug, from production job outputs on 2026-10-02: SteamPrefill exited 0
and printed a summary table with a third column, `Updated | Up To Date |
Failed` = `0 | 0 | 1`, once with the cache unreachable ("22213 requests
failed unexpectedly") and once with "Unable to download manifests!".
vault-api read only the first two integers, took the 0/0 branch, and told
the operator the app was probably not owned. The job did end `error`, but
for the wrong stated reason. Separately, the runner logged
`success=True` for the same job, which it meant as the process outcome.

Scope of the exception, all in WP API-FIX-3:

- `api/vault_api/prefill_summary.py`: the parser reads the Failed column
  (`failed`, `failed_column`) and `reports_failure` decides. A Failed
  column whose count cannot be read counts as a failure.
- `api/vault_api/prefill.py`: the reason value `prefill_failed`
  (`FAILURE_PREFILL_FAILED`) and the text of its log line, with two
  exact-phrase cause hints.
- `api/vault_api/worker.py`: a failed summary ends the job `error` with that
  reason before the 0/0 rule runs; mapping, manifest state, `needs_force`
  and auto-GC are untouched, as for every failure.
- `api/vault_api/prefill_runner.py`: the runner's finish line names itself
  the process outcome (`run_success=`) and says vault-api sets the final
  state. Wording only.
- Tests and docs: `api/tests/test_api_fix_3_prefill_failed.py`,
  `api/README.md`'s job-outcome table; `web/tests/job-failure.test.js`
  (web/ is not frozen) pins that the web reads the new reason and shows no
  hint block for it.

No route, schema or job-status semantics changed: these runs ended `error`
before and still do; only the reason line and its wording are new. Every
other frozen-path change still needs its own user decision and note here.

## Addendum 2026-10-03 — freeze exception: build version in every artifact (WP VER-1, "Weg A1")

User decision, 2026-10-03 ("Weg A1"): `v0.1.0` shows the component versions.
The api/, core/ and dns/ freeze opens for the plumbing this needs, WP VER-1;
serving the version (WP VER-2, `GET /v1/about` and vault-core) and showing
it (web, app, agent report payload in AGENT-FEAT-1) are later packages,
each with its own scope.

The problem: `vault_api.__version__` was a hand-maintained `"0.1.0"`, so
`GET /v1/settings` reported `server_version: "0.1.0"` on `v0.1.0-rc8`. The
images carried the release version only as an OCI label set by
docker/metadata-action, which nothing reads at runtime, and the agent
binaries carried none.

Scope of the exception, all in WP VER-1:

- core/, dns/ (and api/, deploy/proxy/): each Dockerfile takes the build
  args `VAULT_VERSION` (default `dev`) and `VAULT_COMMIT` (default
  `unknown`) in its final stage, after the last `RUN`, and bakes them as
  ENV `VAULT_BUILD_VERSION` / `VAULT_BUILD_COMMIT` and the OCI
  `version` / `revision` labels. The hand-maintained `version="0.1.0"`
  labels are gone. No hook, config or self-check changed.
- api/: `vault_api.build_info()` resolves the version from
  `VAULT_BUILD_VERSION`, falling back to `vault_api.BASE_VERSION`
  (`"0.1.0"`, the release line, still pinned against compose's
  `VAULT_IMAGE_TAG` defaults) when the env is absent, blank or outside the
  grammar; `server_version` and `FastAPI(version=...)` use it; one startup
  log line names version and commit. No route, schema or setting changed.
- Not frozen, listed for completeness: `.github/workflows/publish.yml`
  (`build-version` job, build args, a metadata-action equality check on
  tags, agent `-ldflags -X`), `ci.yml`'s `image-build` (same build-arg
  keys, `ci-<sha>` values), `agent/` (`vault-agent --version`),
  `deploy/tests/verify-stack.sh` (steps 2.ver, 6v, 7h), tests and docs.

Every other frozen-path change still needs its own user decision and note
here.

## Addendum 2026-10-03 — VER-1 exception extended: serving the versions (WP VER-2)

The VER-1 addendum above opened api/ and core/ for the version plumbing and
named WP VER-2 as the package that serves it. User decisions: "Weg A1"
(2026-10-03, show component versions in `v0.1.0`) and "Weg A" (2026-10-03,
vault-core's version comes from a file on the cache volume, no network path
from vault-api, ADR-0011 unchanged). Scope, all in WP VER-2:

- api/: the authenticated route `GET /v1/about` (`vault_api/about.py`,
  `vault_api/routers/about.py`), schema v16 with the table
  `runner_presence` (`vault_api/db.py`, `vault_api/runner_presence.py`),
  the runner's presence writes (`vault_api/prefill_runner.py`), the helpers
  `reported_identity` / `steamprefill_version` / `is_valid_*` in
  `vault_api/__init__.py`, and `ENV STEAMPREFILL_VERSION` in `api/Dockerfile`
  (from the one global build arg). `/v1/health` and `/v1/settings` are
  unchanged.
- core/: the start hook `docker/29-vault-build-version.sh` (root only
  creates a missing `logs/`; every file operation runs as the nginx user,
  review M1), its COPY, chmod and build-time check in `core/Dockerfile`,
  `core/tests/test-build-version-hook.sh` and
  `core/tests/build-version-race-rig.sh`. No nginx config change: no
  `location = /vault-version`, so `check-config-drift.sh` and the
  preflight pins are untouched.
- Not frozen, listed for completeness: `.github/scripts/verify-core-nginx.sh`
  (hook test, hook order, collision refusal, 404 probes),
  `deploy/tests/verify-stack.sh` (steps 6w, 7h, 9d), the web rail footer
  (`web/js/lib/rail-content.js`: `dev` reads "dev build"), docs.

Every other frozen-path change still needs its own user decision and note
here.

## Addendum 2026-10-03 — freeze exception: volume ownership (WP SEC-FIX-5)

Found in the VER-2 review (2026-10-03), severity medium under a compromised
vault-api or nginx worker (both uid 101). User decision 2026-10-03: fix it
before `v0.1.0`.

The problem: `/vault`, `/vault/cache`, `/vault/tmp` and `/vault/logs`
belonged to uid 101, mode 0755, no sticky bit, while root acts on names in
them: `25-vault-eventlog.sh` checked and then created/truncated the event log
(`mkdir -p`, `[ -e ] || : >`), `40-vault-preflight.sh` ran `mkdir -p` +
`chown` on `cache/depot`, and the root nginx master opens the event log
`O_APPEND|O_CREAT` and creates, chowns and chmods its temp directories under
`tmp/` by name at every start, following symlinks. uid 101 could swap any of
those names for a symlink and make root create, truncate, append to, chown or
chmod any file in vault-core (the CVE-2016-1247 class). No re-check in a
hook can close the master's open.

Decision: fix it by ownership. Every directory whose entries root resolves is
root:root 0755; uid 101 owns only what it writes.

| Path | Before | After |
|---|---|---|
| `/vault` | 101:101 0755 | root:root 0755 |
| `/vault/cache` | 101:101 0755 | root:root 0755 |
| `/vault/cache/depot` (+ tree) | 101:101 | 101:101 (unchanged) |
| `/vault/tmp` | 101:101 0755 | root:root 0755 |
| `/vault/tmp/{proxy,client_body,fastcgi,uwsgi,scgi}` | 101:101 0700 (nginx) | 101:101 0700 (created by 21- when missing) |
| `/vault/logs` | 101:101 0755 | root:root 0755 |
| `/vault/logs/event.log` | 101:101 | 101:101 (vault-api truncates it in place) |
| `/vault/logs/vault-core-version.json` | 101:101 0644 (`su nginx` writer) | root:root 0644 (written by root) |

Who writes what, checked in the code: the nginx workers write the depot tree
(`proxy_store`) and their temp files; the master (root) opens the event log;
vault-api (uid 101) deletes under `cache/depot` (GC, `DELETE /v1/cache`),
reads and `ftruncate`s the event log through a verified fd, and reads the
version file; vault-runner does not mount `/vault`. Nothing in vault-api
creates a name directly in `/vault`, `cache/`, `tmp/` or `logs/`, so no api
code changes.

Upgrade -- **user decision 2026-10-03 ("Ist okay"): refuse to start when the
migration is impossible.** The new start hook
`21-vault-volume-ownership.sh` migrates every start (`chown -h 0:0`, then
`chmod 0755`, `/vault` first, then `cache/`, `tmp/`, `logs/`; each level
probed as uid 101 before the next is touched). It needs CAP_CHOWN, which
`deploy/compose.yaml` already grants vault-core. When it cannot make the
layout true (chown not permitted: no CAP_CHOWN, a root-squashing NFS export,
files owned by a uid outside a user-namespaced daemon's mapping; a mode that
does not stick; an ACL that still lets uid 101 create names; an inconclusive
probe; a symlink on one of these names), **vault-core refuses to start** and
prints the two host-side commands, with the caveat that under userns-remap
the owners are the remapped root and the remapped 101, and that on a
root-squashing export they run on the NFS server. Staying up on a volume uid
101 can rewire was the alternative and was rejected: a stopped cache is the
safer failure, and the fix is two commands. A planted symlink is never removed automatically; nothing in
SteamHangar creates one.

Scope of the exception:

- core/: new hook `docker/21-vault-volume-ownership.sh`; `25-vault-eventlog.sh`
  (every directory to the log root-only, missing ones created root-owned,
  symlink / non-regular / hard-linked log refused); `29-vault-build-version.sh`
  (root writes only into a root-only directory chain; the VER-2 `su nginx`
  writer is gone); `40-vault-preflight.sh` (no root `mkdir -p`/`chown` of
  `cache/depot`; the write probe targets `cache/depot` and `tmp/proxy`);
  `Dockerfile` (the root-owned layout, its build-time assertion, the build
  check of 29- moved off `/tmp`); `tests/test-root-only-dir.sh`,
  `tests/volume-ownership-race-rig.sh`, the reworked
  `tests/build-version-race-rig.sh` and `tests/test-build-version-hook.sh`.
  No nginx config change.
- api/: none. `api/Dockerfile` still creates its own `/vault` 101-owned; its
  mount is `nocopy` in named-volume mode, so that layout never seeds a volume.
- deploy/: `compose.yaml` comments only (the capability rationale for
  CHOWN, SETUID/SETGID, DAC_OVERRIDE, FOWNER and the `nocopy` note now
  describe the SEC-FIX-5 layout); no service definition changed.
- Not frozen, listed for completeness: `.github/scripts/verify-core-nginx.sh`,
  `deploy/tests/verify-stack.sh` (steps 4d, 5i, 9a, 9d2, 9e, section 11),
  docs (deploy/README "Using a dedicated cache mount" and "Upgrading",
  core/README, api/README, threat model §6/§9, `.env.example`, examples).

Every other frozen-path change still needs its own user decision and note
here.

## Addendum 2026-10-03 — freeze exception: agent presence (WP AGENT-FEAT-1, "Weg B")

User decision, 2026-10-03 ("Weg B"), for `v0.1.0`: the agent reports at
logon (Windows) / user-manager start (Linux) and every 10 minutes (was 30),
each report carries the agent version and its report interval, and the server
shows online/offline plus "last seen", with no third state. Offline when the
last report is older than `2 × interval + 5 minutes`; an unstated interval
(an old agent) counts as 30 minutes; old agents keep working and show
"version unknown". The api/ freeze opens for this one feature.

Scope of the exception, all in WP AGENT-FEAT-1:

- api/: schema v17 (`agent_reports.agent_version`,
  `agent_reports.report_interval_seconds`, both nullable, added by the
  existing per-column step `_add_missing_agent_report_columns`); two optional
  fields on `POST /v1/agent/installed` (`extra="forbid"` kept; version in the
  VER-1 grammar, interval a strict int 60..86400; response unchanged);
  `agent_reports.presence`, the one place the rule lives; four fields appended
  to `GET /v1/clients` (`agent_version`, `report_interval_seconds`,
  `presence`, `offline_after`), no existing field changed. `GET /v1/about` is
  unchanged: the web counts `/v1/clients` rows (WEB-FEAT-3) instead of a
  second presence computation.
- Not frozen, listed for completeness: `agent/` (payload fields, default
  interval 10m, one-shot states an interval only when told, the one-time
  resend without the fields to a server that answers `422 extra_forbidden`
  for exactly them, i.e. every vault-api up to `v0.1.0-rc8`), the Windows
  task (logon trigger, 10 minutes, `VAULT_AGENT_REPORT_INTERVAL` in the env
  file), the systemd units (`OnCalendar=*:0/10`, `OnStartupSec=30s`,
  `--interval 10m`), `deploy/tests/verify-stack.sh` step 6x, tests and docs.

Operator note: existing Windows tasks and systemd units keep their 30-minute
schedule until reinstalled (`install-task.ps1` again; copy the two unit files
and `daemon-reload`). They keep working meanwhile and show correct presence,
because their reports state no interval and 30 minutes is assumed.

Every other frozen-path change still needs its own user decision and note
here.

## Addendum 2026-10-04 — freeze exception: Steam tool apps never prefilled (WP API-FIX-4)

User decision 2026-10-04 ("Weg A"): a bug fix from the production rollout,
inside the freeze. The api/ freeze opens for this one fix.

The bug: every Windows agent reports app 228980, "Steamworks Common
Redistributables", as installed (Steam installs it next to games). The
scheduler enqueued a prefill for it on every sweep, SteamPrefill cannot
prefill it (not an owned app), every job ended `error`, and the library
showed "App 228980 / Failed / Installed but not cached / Retry download".
Its depots are shared depots, cached together with the games that use them.

Scope of the exception, all in WP API-FIX-4:

- api/: the new module `vault_api/tool_apps.py` (the one fixed list, today
  only 228980, with name and reason); `scheduler.compute_targets` drops
  tool apps from the installed and the cached source (`TargetSet` /
  `SweepResult.skipped_tool_appids`, one log fragment);
  `event_sweep.run_miss_trigger` skips them before the cap
  (`TriggerResult` / `SweepOutcome.skipped_tool`); `POST /v1/prefill/cached`
  never selects them; `POST /v1/prefill` answers `422` with a string
  `detail` for a body that names one, before queueing anything;
  `jobs.enqueue_prefill` raises `ToolAppNotPrefillable` as the last line;
  `GET /v1/games` and `GET /v1/games/{appid}` gain two additive fields,
  `tool_app` and `tool_app_name`. No schema change, no setting, no change
  to agent reports, `installed_on`, mapping or job-status semantics.
- Not frozen, listed for completeness: web (card, detail sheet, bulk bar,
  demo shapes), Android (models, card, list row, detail sheet, bulk plan,
  the ported neutral `notinuse` status kind with its dash glyph), tests and
  docs (api/README.md "Steam tool apps").

Every other frozen-path change still needs its own user decision and note
here.

## Addendum 2026-10-04 — freeze exception: one pooled upstream and a global connection cap (WP CORE-FIX-4a, roadmap D7)

User decision 2026-10-04: fix before `v0.1.0`. ADR-0021 is accepted with the
answers 1 B1 + global cap, 2 edge on out of the box
(`dist-fra1.discovery.steamserver.net`, empty = rollback), 3 C default 16 in
1..64 with no off switch and boot refused below `VAULT_PREFILL_MAX_THREADS`
(empty counts as 8), 4 status 503, 5 passthrough lowered to 32/16 as
CORE-FIX-4d, 6 proof run by the operator (P1-P3 green).

Scope of the exception, core/: `28-vault-upstream-pool.sh`
(`VAULT_UPSTREAM_EDGE`, `VAULT_UPSTREAM_MAX_CONNS`, the cap include), both
nginx configs (the `vault_upstream_total` zone, the cap include inside
`@miss`, `limit_conn_status 503;`, `$vault_upstream_target` in the Host
header and `proxy_pass`, three new log fields), the native static includes
(`vault-upstream-pool.conf`, the new `vault-upstream-cap.conf`),
`40-vault-preflight.sh`, `check-config-drift.sh`, `core/Dockerfile`, the
hook test, and `verify-core-nginx.sh`. Not frozen: compose forwarding,
`.env.example`, verify-stack section 10 and the READMEs (CORE-FIX-4b/4c).
Not in this exception: api/ (no change; the event sweep already counts only
2xx as success, `api/vault_api/event_sweep.py`), and the stream block,
except CORE-FIX-4d on its own line (`vault_tls_total` 256 -> 32,
`vault_tls_client` 64 -> 16, droppable).

## Addendum 2026-10-04 — freeze exception: pause a queued prefill job (WP WEB-FEAT-5 / API)

User request, 2026-10-04: one Pause all / Resume all button on the
Downloads page instead of pausing 16 downloads one by one. User decision
the same day (Weg A): the api/ freeze opens for this one behaviour change.

Why the API had to change: pause was defined for a **running** prefill only
(`409` on a queued job), only one job runs at a time, and pause releases the
worker slot, so the worker claims the next queued job straight away. A
frontend loop over the existing endpoints could pause one download per
round and watch the next one start; every queued job would have started
SteamPrefill just to be stopped again. That traffic is the burst pattern the
CGNAT fixes above exist to avoid.

Scope of the exception, all in WP WEB-FEAT-5:

- api/: `jobs.request_pause` parks a **queued prefill** job at `paused`
  inside the request (`outcome: "immediate"`, new constant
  `PAUSED_QUEUED_MESSAGE` as `detail`), under the same `BEGIN IMMEDIATE` lock
  `claim_next_job` takes, so a pause racing a claim resolves one way or the
  other. Running prefill: unchanged (`stop_request`, `"requested"`).
  Paused/finished: unchanged `409` (detail now says "not 'queued' or
  'running'"). GC jobs: unchanged `409`, queued or running. No new route, no
  response-model change, no schema change. `CANCELLED_PAUSED_MESSAGE` is
  reworded to stay true for a job paused before it started.
- Unchanged and re-verified by tests: `claim_next_job` claims `queued` only,
  `resume_job` puts the job back with its original id (FIFO front),
  `cancel_job` cancels a paused job immediately, and `paused` stays in
  `ACTIVE_STATUSES`, so dedupe and the scheduler's sweep do not stack a
  second job for that app.
- Not frozen, listed for completeness: web and Android Pause all / Resume
  all, tests, api/README.md "Job control".

Every other frozen-path change still needs its own user decision and note
here.
