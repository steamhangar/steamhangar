# D16a-1: Settings audit, env-variable classification, D16c sketch

Status: audit for user sign-off (rc18, 2026-10-14). Docs only, no code
changed. Input for D16a-2 (web sections/tooltips), D16b (expert-mode API)
and D16c (core settings path + the settings-volume Antrag).

Applies the classification principle "Tuning + Debug" decided by the user
on 2026-10-14 (`post-0.1.0-roadmap.md`, Wave plan, "rc18 cut"). Section 5
lists the cases where the principle does not decide on its own. The user
signs off the per-variable table in section 3.

Code state audited: `main` at 6502152.

---

## 1. Settings page today (`web/js/views/settings.js`, 1432 lines)

### 1.1 Section order and contents

`fullRender()` builds one flat column of `h4.sec` headings. Nothing can be
collapsed. In render order:

| # | Section (`h4`) | Builder | Controls | Backing |
|---|----------------|---------|----------|---------|
| 0 | (none) | `fullRender` | read-only hint when `VAULT_SETTINGS_READONLY` | `GET /v1/settings` `.readonly` |
| 1 | Vault | `buildVaultSection` | text: Vault name | `vault_name` |
| 2 | Schedule | `buildScheduleSection` | text: Sweep window, Sweep interval (minutes), Client staleness (days); segs: Include cached games (Off/On); segs: Auto-GC after a prefill (Off/Dry run/Execute); status line + GC-risk warning | `schedule_window`, `schedule_interval_minutes`, `schedule_client_stale_days`, `sweep_include_cached`, `auto_gc`; `GET /v1/schedule` |
| 3 | Webhook | `buildWebhookSection` | text: Webhook URL; 5 checkboxes: Events sent | `webhook_url`, `webhook_events` |
| - | save bar | `fullRender` | Discard / Save changes (fixed above the bottom nav, only while dirty) | `PATCH /v1/settings` via `lib/settings-diff.js` |
| 4 | Steam identity | `buildSteamSection` | password-style key input, Save key, Remove key, status line | `GET/PUT/DELETE /v1/steam/key` |
| 5 | Steam library | `buildSteamLibraryBlock` (called from 4) | text: SteamID64, Save SteamID64, Reset, Preview | `steam_library_steamid` (own PATCH), `/v1/steam/owned-games` |
| 6 | Connection | `buildConnectionSection` | row "Reconnect / switch account" + Start (Connect in demo) | onboarding overlay |
| 7 | PCs (agents) | `buildPcsSection` | row pointing to the Clients page + Open Clients | navigation only (D15c) |
| 8 | About | `buildAboutSection` | component table, Refresh, per-row (i) disclosure | `GET /v1/about` |

Every overridable field also carries a `foot-note` caption
"`<source>` · `<applies>`" (from `lib/settings-presentation.js`) and a
Reset button when `source == "db"`.

### 1.2 Text walls (candidates for tooltips)

Measured as the static strings rendered on a healthy, non-demo vault:

| Where | Text (start) | Length | Proposal |
|-------|--------------|--------|----------|
| Schedule › Sweep window | "Overnight windows are allowed (e.g. 22:00-06:00). Blank disables…" | ~90 chars | tooltip (i) |
| Schedule › Include cached | "When on, the next sweep also refreshes every game that already has content on disk…" | ~140 | tooltip (i) |
| Schedule › status | `sweepTargetsMessage` (up to ~230 chars for the "found no games" case) | variable | stays visible (state, not help), but one line; the explanation part moves into (i) |
| Schedule › GC risk | `cachedSweepGcRiskWarning` (~430 chars) | 430 | stays visible as a warning, shortened to one sentence ("Cached games are refreshed but GC is not set to Execute: disk usage can grow."); the rest into (i) |
| Webhook › URL | "Restart-required: vault-api only starts the delivery thread at boot… (api/README.md “The honest gap”)" | ~150 | drop: the caption already says "Takes effect after vault-api restarts"; the README pointer goes into (i) |
| Steam identity | "With a relay key configured, library queries leave your LAN toward Valve (ADR-0004 addendum)…" | ~170 | tooltip (i); the ADR reference is developer language and goes away |
| Steam library | "The Library lists every game this SteamID64 owns, on every device…" | ~150 | tooltip (i) |
| every field | caption "`<source>` · `<applies>`" | ~50 | stays, but shortened to chips: "Default" / "Env" / "Changed here" plus "live" / "next sweep" / "restart" |
| top | read-only hint naming `VAULT_SETTINGS_READONLY` | ~130 | stays (state) |

Rule for D16a-2: **state stays visible, explanation goes behind (i)**. A
sentence that changes with the vault's state (sweep status, GC-risk
warning, read-only notice, env-pinned value) is not a tooltip.

Tooltip mechanism: reuse the About table's (i) disclosure
(`buildInfoButton`, `infoGlyph`, WP WEB-FIX-8): a real `<button
aria-expanded>` toggling a text row. It is keyboard- and touch-reachable,
which a `title=` hover tooltip is not (phones are a main client).

### 1.3 Proposed layout (web, rc18; Android sections in rc19)

Collapsible sections as native `<details>/<summary>` (no JS state, works
with keyboard and screen readers). Open state per section remembered in
`localStorage` via the guarded getter (LEARNINGS WEB-FIX-5). Default open:
section A only.

| Section | Default | Contents |
|---------|---------|----------|
| A. Vault & connection | open | Vault name; Connection row; PCs pointer row |
| B. Schedule & updates | closed | Sweep window, interval, client staleness, include cached, sweep status line + GC-risk warning |
| C. Disk & cleanup | closed | Auto-GC (moved from Schedule), GC grace days (new), keep counters (new, expert) |
| D. Notifications | closed | Webhook URL (read-only if Q2 = env-only), events |
| E. Steam | closed | Steam identity key, Steam library SteamID64 |
| F. Expert | closed, plus a separate "Show expert settings" switch | Bandwidth & connections (rate, rate window, upstream cap, TLS caps, prefill threads/timeout); Cache-miss trigger & event sweep; Oracle & webhook timeouts; Debug (risky: edge, pool hosts, resolver, log level) |
| G. About | closed (opened by the rail's version button, as today) | unchanged |

Details for D16a-2/D16b:

- A section summary shows a one-line state when collapsed, e.g. "Window
  03:00-07:00 · every 180 min", "Auto-GC: Execute", "Upstream cap: off".
  The summary only reads fields of the same section (no derived claims,
  LEARNINGS 4d-web "one field over-claims").
- The save bar stays global (one PATCH for every dirty field across
  sections). A collapsed section with a dirty field shows a dot in its
  summary, so a draft is never hidden.
- Env-pinned values (D16 user decision 2026-10-13: "env wins, no input
  field") render as text plus an "Env" chip, no input, no Reset.
- Risky fields: the Save of a batch that contains a risky key opens one
  confirm dialog listing those keys with old -> new values
  (`sheet-dialog.js`, inert-based trap, LEARNINGS 4a.8).
- Values applied by vault-core (section 3, apply path "core") show their
  apply state: "applied", "pending" or "rejected: <reason>" (from the
  core status file, section 4.4).

### 1.4 Override keys in the api settings store

Module: `api/vault_api/settings_store.py` (not moved). Precedence today
(ADR-0009 decision 1): **DB override > env > built-in default**, resolved
per call by `effective_settings()`.

`OVERRIDABLE_SPECS` (9 keys): `vault_name` (restart-required),
`schedule_window` (next_sweep), `schedule_interval_minutes` (next_sweep),
`schedule_client_stale_days` (next_sweep), `auto_gc` (immediately),
`sweep_include_cached` (next_sweep), `webhook_url` (restart-required,
secret/redacted), `webhook_events` (restart-required),
`steam_library_steamid` (immediately).

`ENV_ONLY_INFO_KEYS` (9 read-only rows in GET): `db_path`, `cache_root`,
`steamprefill_path`, `steamprefill_cache_dir`, `manifest_archive_dir`,
`web_dir`, `settings_readonly`, `relay_expose_playtime`,
`relay_expose_last_played`. Plus `vault_api_key`, refused by PATCH by name
and never echoed.

Two facts D16b must deal with:

1. **Precedence conflict.** The expert-mode decision says "env wins" for
   the new keys. The nine existing keys use "DB wins". See Q1.
2. **"Is the env set?" cannot be answered today for most keys.** Compose
   forwards most of them with `${VAR:-default}`, so the key is always
   present in the container with the default value. vault-core's image
   also sets `ENV` defaults (`VAULT_UPSTREAM_MAX_CONNS=16`,
   `VAULT_TLS_*`, `VAULT_RESOLVER=1.1.1.1`). With "env wins", every such
   key would be read-only forever. D16b/D16c therefore need the editable
   keys forwarded as `${VAR-}` (unset or blank both mean "not set", LEARNINGS
   SWEEP-1 on `:-` vs `-`), the defaults kept only in code and hooks, and
   the image `ENV` defaults blanked. That is a compose change and goes into
   the same Antrag as the volume (section 4.6).

---

## 2. Readers per container (summary)

| Container | Reads env in | Notes |
|-----------|--------------|-------|
| vault-core | `core/Dockerfile` ENV, `core/docker/2x-/40-/45-*.sh`, envsubst on `nginx.conf.template` | all read once at start |
| vault-api | `api/vault_api/config.py` `Settings.from_env`, `main.py` (log level), `__init__.py`/`about.py` (build identity) | boot snapshot; only the 9 ADR-0009 keys resolve per call |
| vault-runner | same `Settings.from_env(require_api_key=False)` in `prefill_runner.py` | boot snapshot, never reads DB overrides today |
| vault-proxy | `deploy/proxy/docker-entrypoint.sh` | egress allowlist, at start |
| vault-dns | `dns/docker-entrypoint.sh` | `CACHE_IP`, `UPSTREAM_DNS_1/2` (no `VAULT_*` setting) |
| compose only | `deploy/compose.yaml` interpolation | binds, ports, image tag, cache path, log driver, egress subnet |

vault-agent (`VAULT_AGENT_*`) runs on the PCs and is out of scope here.

---

## 3. Every `VAULT_*` variable

Columns: **Read by** = container(s) that read the value at runtime.
**Default** = shipped default (compose value; the code default in brackets
where it differs). **Class**: `E` editable, `R` editable-risky (confirm
dialog), `X` env-only. A dagger (†) marks a class that is proposed by
analogy because the principle does not name the variable (section 5.2).
"Q n" refers to an open question in section 5.1.

**Apply path**:
- `api live`: vault-api resolves the value at each use. Today only the 9
  ADR-0009 keys do that; every other `api live` key needs wiring in D16b
  (call site through `effective_settings`, LEARNINGS boot-snapshot class).
- `next sweep`: resolved by the scheduler/sweeper tick.
- `runner/job`: vault-runner must read the effective value from the DB per
  job (D16b change; today a boot snapshot).
- `core`: written to the settings file, vault-core re-renders and runs
  `nginx -s reload` (D16c, section 4).
- `restart`: needs a container restart. Stays that way for `X` keys.

**Grammar** = where the parser/validator lives today. D16b reuses it.

### 3.1 Editable (tuning)

| Variable | Read by | Default | Meaning | Class | Apply path | Grammar |
|----------|---------|---------|---------|-------|------------|---------|
| `VAULT_UPSTREAM_RATE` | core | empty = no cap | aggregate byte rate of cache MISS reads from Steam, nginx size syntax (`800k`) | E | core (27 include) | `27-vault-upstream-rate.sh` `parse_rate` (min 1024 B/s, ≤9 digits, k/m) |
| `VAULT_UPSTREAM_RATE_WINDOW` | core | `${VAULT_SCHEDULE_WINDOW-03:00-07:00}` [image: empty = around the clock] | full speed inside the window, capped outside | E (Q3) | core (27 include) | `27` `parse_window`, mirrors `schedule_window.parse_window` |
| `VAULT_UPSTREAM_MAX_CONNS` | core | 16 (image ENV) | global cap on concurrent upstream connections, 1..64, must be ≥ prefill threads | E | core (28 cap include) | `28-vault-upstream-pool.sh` (digits, 1..64, floor check) |
| `VAULT_TLS_CLIENT_MAX_CONNS` | core | 64 (image ENV) | HTTPS passthrough sessions per client address, 1..256, ≤ total | E | core (nginx.conf re-render, stream block) | `26-vault-tls-passthrough.sh` `tls_cap` |
| `VAULT_TLS_MAX_CONNS` | core | 256 (image ENV) | HTTPS passthrough sessions in total, 1..400 | E | core (as above) | `26` `tls_cap` |
| `VAULT_PREFILL_MAX_THREADS` | api, runner, core | 8 | SteamPrefill worker threads per job, 1..64; also the floor for `VAULT_UPSTREAM_MAX_CONNS` in core | E | runner/job + core (floor check) | `config._env_int` (1..64); `28` floor |
| `VAULT_PREFILL_TIMEOUT_SECONDS` | api, runner | 14400 | wall-clock limit per prefill job | E | runner/job (api worker too) | `config._env_int` |
| `VAULT_GC_GRACE_DAYS` | api | 14 | days unreferenced chunks are protected before GC deletes them | E | api live | `config._env_int` (0..3650) |
| `VAULT_AUTO_GC` | api | `execute` | GC after each prefill: off / dry-run / execute (override exists) | E | api live (already) | `config.parse_auto_gc` |
| `VAULT_MANIFEST_KEEP` | api | 3 | archived manifests kept per depot (≥1) | E | api live | `config._env_int` |
| `VAULT_AGENT_REPORT_KEEP` | api | 20 | installed-games snapshots kept per agent (≥2) | E | api live | `config._env_int` |
| `VAULT_CLIENT_STATS_KEEP` | api | 48 | per-client statistics rows kept (≥1) | E | api live | `config._env_int` |
| `VAULT_EVENT_SWEEP_INTERVAL_MINUTES` | api | 5 | cadence of the event-log sweeper | E | next sweep (thread cadence) | `config._env_int` |
| `VAULT_MISS_TRIGGER_COOLDOWN_MINUTES` | api | 60 | per-app cooldown of the cache-miss prefill trigger; 0 = trigger off | E | next sweep | `config._env_int` (0..) |
| `VAULT_MISS_TRIGGER_MAX_PER_SWEEP` | api | 5 | max prefill jobs one sweep may enqueue from misses | E | next sweep | `config._env_int` |
| `VAULT_SWEEP_INCLUDE_CACHED` | api | true | scheduled sweep also refreshes games already on disk (override exists) | E | next sweep (already) | `config.parse_strict_bool` |
| `VAULT_SCHEDULE_WINDOW` | api (and core via the compose fallback above) | `03:00-07:00` | window for scheduled sweeps and automatic downloads (override exists) | E | next sweep (already) | `schedule_window.parse_window` |
| `VAULT_SCHEDULE_INTERVAL_MINUTES` | api | 180 | minutes between scheduled sweeps (override exists) | E | next sweep (already) | `config._env_int` |
| `VAULT_SCHEDULE_CLIENT_STALE_DAYS` | api | 7 | agent reports older than this are ignored by the sweep (override exists) | E | next sweep (already) | `config._env_int` |
| `VAULT_MANIFEST_ORACLE_TIMEOUT` | api | 10.0 | seconds per oracle request | E | api live | `config._env_float` |
| `VAULT_WEBHOOK_EVENTS` | api | all 5 events | which events are delivered (override exists) | E | restart today; live in D16b if cheap | `config.parse_webhook_events` |
| `VAULT_NAME` | api | empty | display name of the vault (override exists) | E | restart (as today) | `config.parse_vault_name` |
| `VAULT_STEAM_LIBRARY_STEAMID` | api | empty | SteamID64 whose library the Library view lists (override exists) | E | api live (already) | `config.parse_steam_library_steamid` |
| `VAULT_BYPASS_WINDOW_DAYS` | api | 3 | days of cache-log absence before a client is "bypass suspected" | E † | api live | `config._env_int` |
| `VAULT_WEBHOOK_TIMEOUT_SECONDS` | api | 5.0 | seconds per webhook delivery | E † | restart today (notifier snapshot) | `config._env_float` |
| `VAULT_EVENT_LOG_ROTATE_BYTES` | core | 64 MiB, not forwarded by compose | rotate the event log at this size; 0 = off | Q5 | core (45 loop reads it at start only) | `45-vault-eventlog-rotate.sh` `digits` |
| `VAULT_EVENT_LOG_MAX_BYTES` | api | 64 MiB | vault-api's own truncation of the event log; mostly redundant since D15-ROT | Q5 | api live | `config._env_int` (0..) |

### 3.2 Editable-risky (debug knobs, confirm dialog)

| Variable | Read by | Default | Meaning | Class | Apply path | Grammar |
|----------|---------|---------|---------|-------|------------|---------|
| `VAULT_UPSTREAM_EDGE` | core | `dist-fra1.discovery.steamserver.net` (compose) [image: empty] | the one pooled Steam edge; empty = legacy per-name pool | R | core (28 pool include) | `28` `check_host` (one host, Steam families only) |
| `VAULT_UPSTREAM_POOL_HOSTS` | core | empty | legacy mode: up to 4 pooled edge names, used only while EDGE is empty | R | core (28) | `28` `check_host`, ceiling 4, no duplicates |
| `VAULT_RESOLVER` | core | `1.1.1.1` | DNS server nginx uses for upstream names (http and stream); must not rewrite Steam names to the LAN | R | core (envsubst re-render of nginx.conf + 40 resolver probe) | `40-vault-preflight.sh` §2 (IP character allowlist) + §2b (rewrite probe) |
| `VAULT_LOG_LEVEL` | api, runner | `INFO` | Python log level | R | api live (`setLevel`, D16b); runner per job or restart | `main.py` log-level check |

### 3.3 Env-only

| Variable | Read by | Default | Meaning | Reason |
|----------|---------|---------|---------|--------|
| `VAULT_API_KEY` | api | none (compose refuses to start) | API key for every non-health endpoint | secret |
| `VAULT_WEBHOOK_URL` | api | empty = off | webhook target; can carry credentials | webhook URL (principle); **override exists today**, Q2 |
| `VAULT_DB_PATH` | api, runner | `/data/vault.db` (image) [code `./vault.db`] | SQLite file | path |
| `VAULT_CACHE_ROOT` | api, runner | `/vault/cache` (image) [code `./cache`] | cache tree root as vault-api sees it | path |
| `VAULT_CACHE_PATH` | compose | named volume `vault-cache` | host path for `/vault` (bind mode) | path |
| `VAULT_STEAMPREFILL_PATH` | api, runner | `/opt/steamprefill/SteamPrefill` (image) | SteamPrefill binary | path |
| `VAULT_STEAMPREFILL_CACHE_DIR` | api, runner | computed under HOME | SteamPrefill manifest cache | path |
| `VAULT_MANIFEST_ARCHIVE_DIR` | api | next to the DB | manifest archive directory | path |
| `VAULT_WEB_DIR` | api | `/app/web` (image) | web UI files | path |
| `VAULT_EVENT_LOG` | core | empty = event log off | event-log path in core (also the on/off switch) | path |
| `VAULT_EVENT_LOG_PATH` | api | empty = sweep off | event-log path as vault-api sees it | path |
| `VAULT_CORE_BIND`, `VAULT_CORE_PORT` | compose | `0.0.0.0`, `80` | published HTTP cache address | bind/port |
| `VAULT_TLS_BIND`, `VAULT_TLS_PORT` | compose | `127.0.0.1`, `443` | published passthrough address | bind/port |
| `VAULT_API_BIND`, `VAULT_API_PORT` | compose | `0.0.0.0`, `8080` | published API address | bind/port |
| `VAULT_DNS_BIND`, `VAULT_DNS_PORT` | compose | `127.0.0.1`, `53` | published DNS address (profile `dns`) | bind/port, DNS |
| `VAULT_TLS_PASSTHROUGH` | core | `1` | HTTPS SNI passthrough on/off | TLS passthrough |
| `VAULT_EGRESS_ALLOW` | api, proxy | empty | extra hosts vault-api may reach through vault-proxy | egress allowlist |
| `VAULT_EGRESS_SUBNET` | proxy, compose (network) | `172.30.238.0/24` | subnet of the internal egress network | egress subnet |
| `VAULT_MANIFEST_ORACLE_URL` | api, proxy | `https://api.steamcmd.net/v1/info` | oracle endpoint; its host is auto-allowed only for the default | egress-coupled |
| `VAULT_MANIFEST_ORACLE` | api, proxy | empty = on (`steamcmd_api`) | oracle on/off | egress-coupled, Q4 |
| `VAULT_SETTINGS_READONLY` | api | false | locks every PATCH | security switch (ADR-0009) |
| `VAULT_RELAY_EXPOSE_PLAYTIME` | api | false | relay passes playtime to clients | privacy gate, env-only by ADR-0010 |
| `VAULT_RELAY_EXPOSE_LAST_PLAYED` | api | false | relay passes last-played time | privacy gate, ADR-0010 |
| `VAULT_PREFILL_MODE` | api, runner | `queue` [code `subprocess`] | prefill runs in vault-runner or as a subprocess | topology † |
| `VAULT_RUNNER_HEARTBEAT_SECONDS` | runner | 5.0 | runner heartbeat cadence | internal timing † |
| `VAULT_RUNNER_POLL_SECONDS` | runner | 1.0 | runner idle poll | internal timing † |
| `VAULT_RUNNER_LEASE_TIMEOUT_SECONDS` | api | 30.0 | runner considered lost after this | internal timing † |
| `VAULT_WORKER_POLL_SECONDS` | api | 1.0 | worker idle poll | internal timing † |
| `VAULT_SIZE_CACHE_TTL` | api | 60.0 | per-game size cache TTL | internal timing † |
| `VAULT_EVENT_LOG_ROTATE_INTERVAL` | core | 60, not forwarded | seconds between rotation size checks | internal timing † |
| `VAULT_LOG_MAX_SIZE`, `VAULT_LOG_MAX_FILE` | compose (json-file driver) | `10m`, `5` | docker log rotation | applies at container create only |
| `VAULT_IMAGE_TAG` | compose | `0.1.0` | image tag | build metadata |
| `VAULT_BUILD_VERSION`, `VAULT_BUILD_COMMIT` | core, api, runner, proxy, dns (image ENV) | from build args | build identity for `/v1/about` | build metadata |
| `VAULT_VERSION`, `VAULT_COMMIT` | Dockerfiles (build args) | `dev`, `unknown` | source of the two above | build metadata |
| `VAULT_PROXY_CONF_FILE_FOR_TESTS`, `VAULT_PROXY_CONF_TEMPLATE_FOR_TESTS`, `VAULT_PROXY_FILTER_FILE_FOR_TESTS` | proxy | unset | test hooks of the proxy entrypoint | not a setting |

Not `VAULT_*`, listed for completeness, all env-only: `TZ` (core, api,
runner: time base of both windows), `CACHE_IP`, `UPSTREAM_DNS_1`,
`UPSTREAM_DNS_2` (dns).

Grep hits that are not variables: `VAULT_EVENT_LOG_LINE`,
`VAULT_TLS_PASSTHROUGH_BEGIN/END` (template markers),
`VAULT_TLS_STREAM_EOF` (heredoc tag), `MAX_VAULT_NAME_LENGTH`,
`VAULT_RETURN_PATH` (Kotlin constant), `VAULT_API`/`VAULT_SCHEDULE_`
(prefixes in prose).

Totals: 31 editable or risky candidates (27 E incl. the 2 in Q5, 4 R),
of which 8 are already overridable (the ninth existing override, `webhook_url`, is Q2); the rest env-only or not settings.

### 3.4 Cross-variable rules D16b must validate at PATCH time

vault-api has to refuse what vault-core would refuse at render time, or
a Save turns into "rejected" in core later:

- `VAULT_UPSTREAM_MAX_CONNS ≥ VAULT_PREFILL_MAX_THREADS` (28).
- `VAULT_TLS_CLIENT_MAX_CONNS ≤ VAULT_TLS_MAX_CONNS` (26).
- `VAULT_UPSTREAM_EDGE` and `VAULT_UPSTREAM_POOL_HOSTS`: pool hosts are
  ignored while an edge is set (28 logs it); the UI should grey them out.
- vault-api does not see vault-core's env. For "env wins" on core keys,
  vault-core must report the effective value and its source per key in
  its status file (section 4.4). Otherwise the UI cannot tell an env pin
  from a default.

The shell grammars in 26/27/28/40 get a Python twin in vault-api. Twin
grammars drift. Pin them with one shared fixture table (valid and invalid
values) that both the pytest suite and the core shell tests run
(LEARNINGS "Twin config files need twin pins").

---

## 4. D16c sketch: core settings path

### 4.1 What vault-core would read from the settings file

| Hook | Keys | What changes on apply |
|------|------|-----------------------|
| `20-envsubst-on-templates.sh` (stock) | `VAULT_RESOLVER` | `nginx.conf` re-rendered from the template (both `resolver` lines) |
| `25-vault-eventlog.sh` | none (no editable key) | must re-run after a re-render, because it edits the rendered `nginx.conf` |
| `26-vault-tls-passthrough.sh` | `VAULT_TLS_CLIENT_MAX_CONNS`, `VAULT_TLS_MAX_CONNS` | rewrites the two `limit_conn` lines in the rendered `nginx.conf` (needs the fresh render, it dies on an already rewritten file) |
| `27-vault-upstream-rate.sh` | `VAULT_UPSTREAM_RATE`, `VAULT_UPSTREAM_RATE_WINDOW` | `vault-upstream-rate.conf` |
| `28-vault-upstream-pool.sh` | `VAULT_UPSTREAM_EDGE`, `VAULT_UPSTREAM_POOL_HOSTS`, `VAULT_UPSTREAM_MAX_CONNS`, `VAULT_PREFILL_MAX_THREADS` (floor) | `vault-upstream-pool.conf`, `vault-upstream-cap.conf` |
| `40-vault-preflight.sh` | `VAULT_RESOLVER` (§2, §2b), TLS caps, rate | the resolver checks must run again before a reload |
| `45-vault-eventlog-rotate.sh` | `VAULT_EVENT_LOG_ROTATE_BYTES` only if Q5 = editable | the loop would have to re-read it |
| `21-`, `29-` | none | boot only |

Since the order 20 → 25 → 26 → 27 → 28 → 40 is already the boot chain,
apply means re-running that same chain, not writing a second renderer.

### 4.2 File format

`core-settings.env`, written only by vault-api:

```
# generated by vault-api -- do not edit
GENERATION=42
VAULT_UPSTREAM_RATE=800k
VAULT_UPSTREAM_RATE_WINDOW=03:00-07:00
VAULT_UPSTREAM_MAX_CONNS=16
VAULT_PREFILL_MAX_THREADS=8
```

- One `KEY=VALUE` per line. No quotes, no expansion, no comments other than
  the header. Only keys whose value is overridden in the DB appear. A
  missing key means "env, else default".
- vault-core **never sources it** (`. file` would execute it). It reads it
  line by line, accepts only a fixed key allowlist, and runs every value
  through the hook's existing grammar. Unknown key or bad value: reject
  the whole file, keep the last good config.
- vault-api writes atomically (temp file in the same directory, then
  `rename`), and rewrites the file from the DB at every boot. That way a
  lost settings volume repairs itself.
- `GENERATION` increases with every write. vault-core reports the last
  generation it applied (4.4), and the UI shows "pending" until they
  match.

Precedence inside core: the env value wins over the file when the env
key is set and not blank (D16 decision "env wins"). This only works after
the `${VAR-}` forwarding change (1.4, point 2).

### 4.3 Apply mechanism in vault-core

- **Boot:** a new hook `15-vault-settings.envsh`. The stock nginx entrypoint
  sources executable `*.envsh` files (verify this on the pinned 1.29.8 image
  in D16c), so it can export the file's values before `20-envsubst` for
  every key the env leaves unset. The existing hooks then run unchanged.
- **Runtime:** a watcher loop started from a hook, the same pattern as
  `45-vault-eventlog-rotate.sh` (forked before the exec, re-parented to
  the nginx master, not started for `nginx -t`/`-T`). Every few seconds it
  checks `GENERATION`. When it changes:
  1. Parse and validate the file. On failure: status "rejected", nothing
     changes.
  2. Render 20 → 25 → 26 → 27 → 28 into a staging directory (26/27/28
     already take an output path; envsubst via `NGINX_ENVSUBST_OUTPUT_DIR`).
  3. Run the 40 checks that depend on values (resolver allowlist + probe,
     wiring asserts) and `nginx -t -c <staging>/nginx.conf`.
  4. Copy into `/etc/nginx` and run `nginx -s reload` (same master pid,
     45's rotation is unaffected). Keep the previous files. If the reload
     fails, restore them.
  5. Write the status (4.4).
- Read the settings file as the `nginx` user (busybox `su`, LEARNINGS
  VER-2/SEC-FIX-5). The volume is writable by uid 101, so a root read
  through a planted symlink would otherwise leak root-only files into
  the error messages.

### 4.4 Shared volume

Convention in this repo (`deploy/README.md` "Volumes and backup",
ADR-0013): named volumes `vault-<purpose>` declared under top-level
`volumes:` with no `name:` override, so Compose prefixes them with the
project name. Only one container seeds a fresh named volume; every other
consumer mounts it with `nocopy` (LEARNINGS TH-1-FIX copy-up race).

Proposal (Weg A, Q6):

| | |
|---|---|
| Volume | `vault-settings` (new) |
| vault-api | `/var/lib/vault-settings`, **rw**. The api image creates the directory owned by `101:101`, so the first mount seeds the volume with that owner. vault-api is the only writer. |
| vault-core | `/etc/vault-settings`, **ro**, `nocopy` |
| vault-runner, vault-proxy, vault-dns | not mounted. The runner gets its values from the DB, which it already mounts. |
| Bind mode | none, named volume only. The file is derived from the DB, so it needs no backup and no `VAULT_*_PATH` variable. |

Status back-channel without making the volume writable for core:
vault-core writes `settings-applied.json` (generation, ok/rejected,
reason, and effective value plus source for each core key) to
`/vault/logs/`. That directory is already root-owned in core and readable
by vault-api through the existing `/vault` mount (same path as the event
log), so no new mount is needed for it.

### 4.5 Compose change (`deploy/compose.yaml`)

```yaml
services:
  vault-core:
    volumes:
      - ${VAULT_CACHE_PATH:-vault-cache}:/vault
      - type: volume
        source: vault-settings
        target: /etc/vault-settings
        read_only: true
        volume:
          nocopy: true
  vault-api:
    volumes:
      # existing mounts unchanged
      - vault-settings:/var/lib/vault-settings

volumes:
  vault-cache:
  vault-db:
  vault-steamprefill:
  vault-steamprefill-home:
  vault-settings:
```

The long syntax is used for core because it states `read_only` and
`nocopy` without relying on comma-combined short-syntax modes.

### 4.6 What the Antrag covers (dev and prod overlay)

1. The new `vault-settings` volume and the two mounts above.
2. Forwarding switch for the editable keys: `${VAR:-default}` →
   `${VAR-}` in vault-core, vault-api and vault-runner, so "not set" can be
   seen (section 1.4). Any value the operator wants pinned stays in `.env`
   and then shows read-only.
3. The Antrag also checks that the existing overlays set none of the
   section 3.1/3.2 keys by accident, because a set key becomes a read-only
   pin in expert mode.

Failure modes, per LEARNINGS "Process" (state what happens when each
store is lost):

- Settings volume lost: vault-api rewrites the file from the DB at boot.
  Nothing is lost.
- DB lost: overrides are gone and env or defaults apply. For the upstream
  rate cap that means **cap off**, which on a CGNAT line is the
  connection-storm risk. Operators who must have a cap pin it in `.env`.
- Core rejects a file: the last good config stays live, and the UI shows
  "rejected: <reason>". It never fails open.

---

## 5. Open points

### 5.1 Open questions for the user

- **Q1 Precedence.** ADR-0009 says the DB override wins over env for the
  9 existing keys. The expert-mode decision says env wins.
  Weg A: env wins only for the new expert keys, and the 9 keep today's
  behaviour. Weg B: env wins everywhere, so an existing DB override is
  ignored once the env sets the same key (a behaviour change, ADR-0009
  addendum). Recommendation: **Weg A**.
- **Q2 Webhook URL.** The principle makes it env-only, but it is a DB
  override today (redacted in GET).
  Weg A: follow the principle. Remove the PATCH path, ignore an existing
  DB value and log one warning at startup. Weg B: keep it as it is.
  Recommendation: **Weg A**. Every webhook host needs a
  `VAULT_EGRESS_ALLOW` entry anyway, so it is already env-bound in
  practice.
- **Q3 Rate window when unset.** Today compose fills it from
  `VAULT_SCHEDULE_WINDOW`, and the core image default is "cap around the
  clock". A schedule window changed in Settings never reaches core.
  Weg A: an unset rate window follows the *effective* schedule window,
  and vault-api writes it into the core file. Weg B: an unset rate window
  means "around the clock". Recommendation: **Weg A**.
- **Q4 Oracle on/off (`VAULT_MANIFEST_ORACLE`).** It is not named by the
  principle. vault-proxy reads it at start to auto-allow
  api.steamcmd.net, so a Settings toggle would not reach the proxy
  without a restart. Recommendation: **env-only**.
- **Q5 Event-log size knobs.** `VAULT_EVENT_LOG_ROTATE_BYTES` (core,
  deliberately not forwarded since D15-ROT) and
  `VAULT_EVENT_LOG_MAX_BYTES` (vault-api truncation, redundant since
  D15-ROT). Retention would make them editable. Recommendation:
  `ROTATE_BYTES` editable on the core path, `MAX_BYTES` deprecated (left
  at the default, no UI).
- **Q6 Where the file lives.** Weg A: the new `vault-settings` volume,
  which needs the Antrag. Weg B: a 101-owned subdirectory of the existing
  `/vault` volume, which needs no compose change. Against Weg B: `/vault`
  is root-owned by design (SEC-FIX-5), it is a cache volume (wiped or
  bind-mounted on NAS), and a 101-owned directory there brings back the
  root-reads-in-a-101-directory class. Recommendation: **Weg A**.

### 5.2 Classified by analogy (no answer needed unless you disagree)

- Editable: `VAULT_BYPASS_WINDOW_DAYS` (sweep tuning),
  `VAULT_WEBHOOK_TIMEOUT_SECONDS` (like the oracle timeout).
- Env-only: `VAULT_PREFILL_MODE` (topology: needs the runner container),
  `VAULT_RUNNER_*`, `VAULT_WORKER_POLL_SECONDS`, `VAULT_SIZE_CACHE_TTL`,
  `VAULT_EVENT_LOG_ROTATE_INTERVAL` (internal timings, not operator
  tuning).

### 5.3 Notes for later packages (not questions)

- `VAULT_PREFILL_MODE` has a code default of `subprocess` and a compose
  default of `queue`. That is intended (ADR-0012), but the UI must show
  the effective value, not the code default.
- `VAULT_SCHEDULE_WINDOW` has a compose default of `03:00-07:00` while the
  code default is "off". D16b's "env set?" logic will report it as
  env-set until the forwarding change (4.6, point 2) lands.
