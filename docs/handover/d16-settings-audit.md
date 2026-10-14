# D16a-1: Settings audit, env-variable classification, D16c sketch

Status: audit for user sign-off (rc18, 2026-10-14). Docs only, no code
changed. Input for D16a-2 (web sections/tooltips), D16b (expert-mode API)
and D16c (core settings path + the settings-volume Antrag).

Applies the classification principle "Tuning + Debug" decided by the user
on 2026-10-14 (`post-0.1.0-roadmap.md`, Wave plan, "rc18 cut"). Section 5
records the user's decisions on the cases the principle did not settle
(Q1-Q7, 2026-10-14). The user signs off the per-variable table in
section 3.

Revision 2: review round 1 findings (blank-vs-value per key, real
compose forwarding forms, staging and reload gate in D16c) and the user
decisions of 2026-10-14 worked in. Revision 3: Q7 decided (keyword
`off`, blank = not set for every editable key), with the grammar changes
and the upgrade mitigations that follow from it.

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
| Webhook › URL | "Restart-required: vault-api only starts the delivery thread at boot… (api/README.md “The honest gap”)" | ~150 | drop: the URL becomes masked text, env-only (Q2), so there is no input left to explain; the "set via env" note replaces the hint |
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
| B. Download window & updates | closed | Download window (Q3: the one window; inside = automatic downloads + full speed, outside = no automatic downloads + rate cap), sweep interval, client staleness, include cached, sweep status line + GC-risk warning |
| C. Disk & cleanup | closed | Auto-GC (moved from Schedule), GC grace days (new), keep counters (new, expert) |
| D. Notifications | closed | Webhook URL as masked text, env-only (Q2), events (editable) |
| E. Steam | closed | Steam identity key, Steam library SteamID64 |
| F. Expert | closed, plus a separate "Show expert settings" switch | Bandwidth & connections (upstream rate cap, upstream cap, TLS caps, prefill threads/timeout; no separate rate window, Q3); Cache-miss trigger & event sweep; Oracle & webhook timeouts; Debug (risky: edge, pool hosts, resolver, log level) |
| G. About | closed (opened by the rail's version button, as today) | unchanged |

Details for D16a-2/D16b:

- A section summary shows a one-line state when collapsed, e.g. "Window
  03:00-07:00 · every 180 min", "Auto-GC: Execute", "Upstream cap: off".
  The summary only reads fields of the same section (no derived claims,
  LEARNINGS 4d-web "one field over-claims").
- The save bar stays global (one PATCH for every dirty field across
  sections). A collapsed section with a dirty field shows a dot in its
  summary, so a draft is never hidden.
- Env-pinned values (user decisions 2026-10-13 and Q1 2026-10-14: env
  always wins) render as **plain text with a "set via env" note** — not
  as a disabled input box, no Reset. This applies to every key, the nine
  existing overrides included. (The `VAULT_SETTINGS_READONLY` mode keeps
  its disabled inputs; that is a different state.)
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

1. **Precedence flips (Q1, decided 2026-10-14).** Env always wins, for
   every key including the nine existing overrides. That reverses
   ADR-0009 decision 1; **the ADR-0009 addendum is a D16b deliverable**.
   An existing DB override that an env value now shadows must not vanish
   silently: vault-api logs one startup warning per shadowed key, and the
   UI shows the env value with "set via env".
2. **"Is the env set?" cannot be answered today for some keys.** Measured
   per key in section 3.5. Most core caps are already forwarded as
   `${VAR-}` (blank → hook default), so they are not pinned. Pinned with a
   compose default today, i.e. always "set": in vault-core
   `VAULT_RESOLVER`, `VAULT_PREFILL_MAX_THREADS`, `VAULT_UPSTREAM_EDGE`,
   `VAULT_UPSTREAM_RATE_WINDOW`; in vault-api/runner every `:-` key of
   section 3.1 (`VAULT_AUTO_GC`, `VAULT_SWEEP_INCLUDE_CACHED`,
   `VAULT_GC_GRACE_DAYS`, the keep counters, sweep and trigger knobs, both
   timeouts, `VAULT_LOG_LEVEL`) and `VAULT_SCHEDULE_WINDOW`. With env-wins
   these would be read-only forever, so their forwarding form must change.
   **The form is chosen per variable** (LEARNINGS SWEEP-1, 2026-08-22: blank
   may be a value; `:-` vs `-` is a per-key decision with a recorded
   reason). Q7 (decided): every editable key is forwarded as `${VAR-}`
   and blank means "not set"; where blank used to be a value (off /
   legacy), the grammar gains the keyword `off` (section 3.5, 3.6).
   vault-core's image `ENV` defaults (`VAULT_UPSTREAM_MAX_CONNS=16`,
   `VAULT_TLS_*`, `VAULT_RESOLVER=1.1.1.1`) are overridden by compose and
   only matter for a plain `docker run`; D16c blanks or drops them so the
   "set?" answer is the same with and without compose (image change, not
   part of the Antrag).

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
"Q n" refers to a decision in section 5.1 (Q1-Q7, 2026-10-14).
"(default)" marks a classification the user took over from the
recommendation; the user may still object at table sign-off.
How each key is forwarded and what blank means: section 3.5.

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
| `VAULT_UPSTREAM_RATE` | core | blank = no cap | aggregate byte rate of cache MISS reads from Steam, nginx size syntax (`800k`); applies outside the download window | E | core (27 include) | `27-vault-upstream-rate.sh` `parse_rate` (min 1024 B/s, ≤9 digits, k/m) |
| `VAULT_UPSTREAM_MAX_CONNS` | core | blank → hook default 16 (image ENV 16 only without compose) | global cap on concurrent upstream connections, 1..64, must be ≥ prefill threads | E | core (28 cap include) | `28-vault-upstream-pool.sh` (digits, 1..64, floor check) |
| `VAULT_TLS_CLIENT_MAX_CONNS` | core | blank → hook default 64 (image ENV 64 only without compose) | HTTPS passthrough sessions per client address, 1..256, ≤ total | E | core (nginx.conf re-render, stream block) | `26-vault-tls-passthrough.sh` `tls_cap` |
| `VAULT_TLS_MAX_CONNS` | core | blank → hook default 256 (image ENV 256 only without compose) | HTTPS passthrough sessions in total, 1..400 | E | core (as above) | `26` `tls_cap` |
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
| `VAULT_SCHEDULE_WINDOW` | api; core gets the effective value through the settings file (Q3) | `03:00-07:00` (compose) [code: unset = off] | **the one download window** (Q3): inside = automatic downloads + full speed, outside = no automatic downloads + rate cap; blank = scheduler off and cap around the clock (override exists) | E | next sweep (already) + core (27 include, via the file) | `schedule_window.parse_window`; `27` `parse_window` mirrors it |
| `VAULT_SCHEDULE_INTERVAL_MINUTES` | api | 180 | minutes between scheduled sweeps (override exists) | E | next sweep (already) | `config._env_int` |
| `VAULT_SCHEDULE_CLIENT_STALE_DAYS` | api | 7 | agent reports older than this are ignored by the sweep (override exists) | E | next sweep (already) | `config._env_int` |
| `VAULT_MANIFEST_ORACLE_TIMEOUT` | api | 10.0 | seconds per oracle request | E | api live | `config._env_float` |
| `VAULT_WEBHOOK_EVENTS` | api | all 5 events | which events are delivered (override exists) | E | restart today; live in D16b if cheap | `config.parse_webhook_events` |
| `VAULT_NAME` | api | empty | display name of the vault (override exists) | E | restart (as today) | `config.parse_vault_name` |
| `VAULT_STEAM_LIBRARY_STEAMID` | api | empty | SteamID64 whose library the Library view lists (override exists) | E | api live (already) | `config.parse_steam_library_steamid` |
| `VAULT_BYPASS_WINDOW_DAYS` | api | 3 | days of cache-log absence before a client is "bypass suspected" | E † | api live | `config._env_int` |
| `VAULT_WEBHOOK_TIMEOUT_SECONDS` | api | 5.0 | seconds per webhook delivery | E † | restart today (notifier snapshot) | `config._env_float` |
| `VAULT_EVENT_LOG_ROTATE_BYTES` | core | 64 MiB, not forwarded by compose | rotate the event log at this size; 0 = off | E (Q5, default) | core (the 45 loop must re-read it; today read at start only) | `45-vault-eventlog-rotate.sh` `digits` |

### 3.2 Editable-risky (debug knobs, confirm dialog)

| Variable | Read by | Default | Meaning | Class | Apply path | Grammar |
|----------|---------|---------|---------|-------|------------|---------|
| `VAULT_UPSTREAM_EDGE` | core | `dist-fra1.discovery.steamserver.net` (compose) [image: empty] | the one pooled Steam edge; `off` = legacy per-name pool (rollback switch; was blank before Q7) | R | core (28 pool include) | `28` `check_host` (one host, Steam families only) |
| `VAULT_UPSTREAM_POOL_HOSTS` | core | empty | legacy mode: up to 4 pooled edge names, used only while EDGE is empty | R | core (28) | `28` `check_host`, ceiling 4, no duplicates |
| `VAULT_RESOLVER` | core | `1.1.1.1` | DNS server nginx uses for upstream names (http and stream); must not rewrite Steam names to the LAN | R | core (envsubst re-render of nginx.conf + 40 resolver probe) | `40-vault-preflight.sh` §2 (IP character allowlist) + §2b (rewrite probe) |
| `VAULT_LOG_LEVEL` | api, runner | `INFO` | Python log level | R | api live (`setLevel`, D16b); runner per job or restart | `main.py` log-level check |

### 3.3 Env-only

| Variable | Read by | Default | Meaning | Reason |
|----------|---------|---------|---------|--------|
| `VAULT_API_KEY` | api | none (compose refuses to start) | API key for every non-health endpoint | secret |
| `VAULT_WEBHOOK_URL` | api | empty = off | webhook target; can carry credentials | webhook URL (Q2 decided): D16b removes the PATCH path, an existing DB value is ignored with one startup warning; Settings shows it masked as text |
| `VAULT_UPSTREAM_RATE_WINDOW` | core | `${VAULT_UPSTREAM_RATE_WINDOW-${VAULT_SCHEDULE_WINDOW-03:00-07:00}}` | separate full-speed window for the cap | **deprecated** (Q3): the download window replaces it. If set in env: startup warning, still honoured as an env pin of core's cap window. Not in Settings |
| `VAULT_EVENT_LOG_MAX_BYTES` | api | 64 MiB | vault-api's own truncation of the event log, redundant since D15-ROT | **deprecated** (Q5, default): left at its default, no UI |
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
| `VAULT_MANIFEST_ORACLE` | api, proxy | empty = on (`steamcmd_api`) | oracle on/off | egress-coupled (Q4, default) |
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
(prefixes in prose), `VAULT_API_VERSION` (api constant). Outside the
server containers: `VAULT_RELEASE_*` (CI publish workflow) and
`VAULT_AGENT_SERVER_URL` with the other `VAULT_AGENT_*` (vault-agent on
the PCs) — not server settings.

Totals: 29 editable or risky (25 E, 4 R), of which 8 are already
overridable (the ninth existing override, `webhook_url`, becomes env-only,
Q2); two deprecated (`VAULT_UPSTREAM_RATE_WINDOW`,
`VAULT_EVENT_LOG_MAX_BYTES`); the rest env-only or not settings.

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

### 3.5 Forwarding form and the meaning of blank, per editable key

Measured in `deploy/compose.yaml` at 6502152. Rule after Q7 (decided
2026-10-14, Weg A): **every editable key is forwarded as `${VAR-}`, and
blank or unset means "not set"** (Settings, else the shipped default,
decide). Where blank used to select a behaviour, that behaviour is now
spelled `off`. The column "Upgrade flip" names what an existing blank line
turns into; section 3.7 lists the mitigations.

| Key | Compose today | Blank today means | Blank after Q7 | `off` | Upgrade flip for a blank line |
|-----|---------------|-------------------|----------------|-------|-------------------------------|
| `VAULT_UPSTREAM_RATE` | `${VAR-}` | no cap | not set → Settings, else no cap | no cap | only if Settings sets a cap (then the cap applies) |
| `VAULT_UPSTREAM_POOL_HOSTS` | `${VAR-}` | no pool hosts | not set → Settings, else none | none | only if Settings sets hosts |
| `VAULT_UPSTREAM_EDGE` | `${VAR-dist-fra1.discovery.steamserver.net}` | **legacy mode** (rollback switch, ADR-0021) | not set → Settings, else edge `dist-fra1…` | legacy per-name mode | **yes: legacy → edge mode** |
| `VAULT_SCHEDULE_WINDOW` | `${VAR-03:00-07:00}` | **scheduler off** | not set → Settings, else 03:00-07:00 | scheduler off, cap around the clock | **yes: off → downloads 03:00-07:00** |
| `VAULT_UPSTREAM_RATE_WINDOW` (deprecated) | `${VAR-${VAULT_SCHEDULE_WINDOW-03:00-07:00}}` | **cap around the clock** | not set → follow the download window | cap around the clock | **yes: "cap always" → "follow the download window"** (an env pin of `off` keeps "cap always") |
| `VAULT_UPSTREAM_MAX_CONNS`, `VAULT_TLS_CLIENT_MAX_CONNS`, `VAULT_TLS_MAX_CONNS` | `${VAR-}` | hook default | not set | not accepted | none |
| `VAULT_NAME`, `VAULT_SCHEDULE_INTERVAL_MINUTES`, `VAULT_SCHEDULE_CLIENT_STALE_DAYS`, `VAULT_WEBHOOK_EVENTS`, `VAULT_STEAM_LIBRARY_STEAMID` | `${VAR-}` | code default (events: all five) | not set | not accepted | none |
| `VAULT_RESOLVER` | `${VAR:-1.1.1.1}` | 1.1.1.1 | not set → default 1.1.1.1 from the `15-` hook (4.3); blank never reaches envsubst | not accepted | none |
| `VAULT_PREFILL_MAX_THREADS` (core, api, runner) | `${VAR:-8}` | 8 | not set → 8 | not accepted | none |
| `VAULT_PREFILL_TIMEOUT_SECONDS`, `VAULT_GC_GRACE_DAYS`, `VAULT_AUTO_GC`, `VAULT_SWEEP_INCLUDE_CACHED`, `VAULT_MANIFEST_KEEP`, `VAULT_AGENT_REPORT_KEEP`, `VAULT_CLIENT_STATS_KEEP`, `VAULT_EVENT_SWEEP_INTERVAL_MINUTES`, `VAULT_MISS_TRIGGER_COOLDOWN_MINUTES`, `VAULT_MISS_TRIGGER_MAX_PER_SWEEP`, `VAULT_BYPASS_WINDOW_DAYS`, `VAULT_MANIFEST_ORACLE_TIMEOUT`, `VAULT_WEBHOOK_TIMEOUT_SECONDS`, `VAULT_LOG_LEVEL` | `${VAR:-<default>}` | the default (`:-` turns blank into it) | not set → same default from `config.py` | not accepted (their "off" is already a value, e.g. `auto_gc=off`, cooldown `0`) | none |
| `VAULT_EVENT_LOG_ROTATE_BYTES` | not forwarded | hook default 64 MiB | not set → 64 MiB | not accepted (`0` = off) | none |

Shipped defaults do not change: every default compose supplies today
(edge on, window 03:00-07:00, resolver 1.1.1.1, threads 8, ...) moves into
the code or hook that reads the key, so an install without a `.env`
behaves as before. Only explicit blank lines flip (3.7).

### 3.6 Grammar changes for `off` (D16b/D16c)

`off` is exact, lowercase, after the same whitespace strip each parser
already does. It is accepted by these five keys only, everywhere a value
for them is parsed:

| Key | Shell parser (vault-core) | Python parser (vault-api) |
|-----|---------------------------|---------------------------|
| `VAULT_SCHEDULE_WINDOW` | reaches core only as the file key `VAULT_UPSTREAM_RATE_WINDOW` (below) | `schedule_window.parse_window` gains `off` → `None`; `config.Settings.from_env` (today: blank → `None`, must become blank → default 03:00-07:00, `off` → `None`); `settings_store._parse_schedule_window_override` (today: blank → `None`, becomes `off` → `None`; blank in a PATCH = reset) |
| `VAULT_UPSTREAM_RATE_WINDOW` (deprecated; also the file key for the download window) | `27-vault-upstream-rate.sh` `parse_window` (and the blank test before it): `off` → no window, cap around the clock; blank → not set | new twin in vault-api (D16b), shared with the env warning path; vault-api writes `VAULT_UPSTREAM_RATE_WINDOW=off` when the download window is off |
| `VAULT_UPSTREAM_RATE` | `27` `parse_rate`: `off` → `TOTAL=0` (no cap); blank → not set | new twin (D16b) |
| `VAULT_UPSTREAM_EDGE` | `28-vault-upstream-pool.sh` `EDGE_RAW` handling: `off` → legacy mode; blank → not set (default edge) | new twin (D16b) reusing a port of `check_host` |
| `VAULT_UPSTREAM_POOL_HOSTS` | `28` `HOSTS_RAW` handling: `off` → none; blank → not set | new twin (D16b) |

Tests and fixtures that change:

- New shared fixture table `core/tests/fixtures/settings-grammar.tsv`
  (key, raw value, expected verdict and normalised value), covering
  `off`, blank, `OFF`/` off `/`off,x` (refused), and today's valid and
  invalid spellings. Run by both suites below; a twin without the table
  is the drift class LEARNINGS warns about.
- `core/tests/test-upstream-pool-hook.sh`: `off` / blank cases for EDGE and
  POOL_HOSTS, driven by the table.
- New `core/tests/test-upstream-rate-hook.sh` (27 has no hook test today;
  only `core/Dockerfile` build checks and `deploy/tests/verify-stack.sh`
  cover it): rate and window cases from the table.
- `core/Dockerfile` build checks: the current calls with
  `VAULT_UPSTREAM_EDGE= …` (blank = legacy) and `VAULT_UPSTREAM_RATE=
  VAULT_UPSTREAM_RATE_WINDOW=` must be re-pointed to `off` where they
  mean off, plus one `off` case per key.
- `api/tests/test_schedule_window.py`, `api/tests/test_settings_api.py`
  (window `off` vs blank/reset), plus a new pytest for the core-key twins
  that reads the same `.tsv`.
- Web: the window field's placeholder "blank to disable" becomes "off to
  disable"; `lib/settings-presentation.js` `effectiveAsInputValue` renders
  a `null` window as `off`.

### 3.7 Upgrade consequence of Q7 and the required mitigations

The known cost of Weg A: a **blank line that means "off" today becomes
"not set" after rc18**, so the shipped default applies: the scheduler
runs 03:00-07:00 instead of being off, the edge mode replaces the legacy
pool, and a blank rate window follows the download window instead of
capping around the clock. Compose cannot tell blank from unset, so nothing
can warn at runtime. Required mitigations:

1. **rc18 release note** (GitHub release text and `deploy/README.md`
   "Upgrading"): "Blank no longer means off. Write `off` instead of a
   blank value for `VAULT_SCHEDULE_WINDOW`, `VAULT_UPSTREAM_EDGE`,
   `VAULT_UPSTREAM_RATE`, `VAULT_UPSTREAM_POOL_HOSTS`,
   `VAULT_UPSTREAM_RATE_WINDOW`; a blank or missing value now means 'use
   Settings, else the default'." The ADR-0021 rollback recipe changes from
   `VAULT_UPSTREAM_EDGE=` to `VAULT_UPSTREAM_EDGE=off`.
2. **`.env.example` edits** so the template pins nothing:
   `deploy/.env.example` `VAULT_UPSTREAM_RATE=` (line 526) becomes a
   commented `#VAULT_UPSTREAM_RATE=` documented under the new rule (blank
   = default, no pin; `off` = pin no cap); the uncommented seed line
   `VAULT_UPSTREAM_POOL_HOSTS=dist-fra1… cache6-ams1…` (line 583) becomes
   a commented example; every other editable key stays commented.
   `api/.env.example` `VAULT_SCHEDULE_WINDOW=` (line 210) becomes
   `VAULT_SCHEDULE_WINDOW=off` if the dev intent is "scheduler off", see
   5.3. The `.env.example` drift tests (LEARNINGS "Twin config files need
   twin pins") must assert "no editable key set" for both files.
3. **Antrag checklist item:** before the rc18 rollout the operator checks
   the dev and prod `.env` files and overlays for every editable key of
   section 3.1/3.2 that is present, blank or set: an intended "off"
   becomes `off`, an accidental pin is removed (it would show as "set via
   env" and block Settings).

---

## 4. D16c sketch: core settings path

### 4.1 What vault-core would read from the settings file

| Hook | Keys | What changes on apply |
|------|------|-----------------------|
| `20-envsubst-on-templates.sh` (stock) | `VAULT_RESOLVER` | `nginx.conf` re-rendered from the template (both `resolver` lines) |
| `25-vault-eventlog.sh` | none (no editable key) | must re-run after a re-render, because it edits the rendered `nginx.conf` |
| `26-vault-tls-passthrough.sh` | `VAULT_TLS_CLIENT_MAX_CONNS`, `VAULT_TLS_MAX_CONNS` | rewrites the two `limit_conn` lines in the rendered `nginx.conf` (needs the fresh render, it dies on an already rewritten file) |
| `27-vault-upstream-rate.sh` | `VAULT_UPSTREAM_RATE`, the window (file key `VAULT_UPSTREAM_RATE_WINDOW` = the effective download window, Q3) | `vault-upstream-rate.conf` |
| `28-vault-upstream-pool.sh` | `VAULT_UPSTREAM_EDGE`, `VAULT_UPSTREAM_POOL_HOSTS`, `VAULT_UPSTREAM_MAX_CONNS`, `VAULT_PREFILL_MAX_THREADS` (floor) | `vault-upstream-pool.conf`, `vault-upstream-cap.conf` |
| `40-vault-preflight.sh` | `VAULT_RESOLVER` (§2, §2b), TLS caps, rate | the resolver checks must run again before a reload |
| `45-vault-eventlog-rotate.sh` | `VAULT_EVENT_LOG_ROTATE_BYTES` (Q5) | the loop has to re-read it on a generation change |
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
- Exception, Q3: vault-api always writes `VAULT_UPSTREAM_RATE_WINDOW`
  with the **effective download window** (env or Settings or default
  `VAULT_SCHEDULE_WINDOW`; `off` when the scheduler is off, which 27
  renders as "cap around the clock"). The key name stays so 27 needs no
  new input. A core env pin of the deprecated `VAULT_UPSTREAM_RATE_WINDOW`
  still wins over the file, with a startup warning.
- D8 (later) adds several windows per day, each with a "throttle"
  checkbox. Do not bake "exactly one window" into anything but the
  grammar: the file value and the API field should be able to grow into a
  list (for example `03:00-07:00,22:00-23:00!nothrottle`), and 27's window
  map already renders arbitrary minute sets, so a list is a renderer
  extension, not a redesign.
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
key is set, i.e. non-blank (Q1, Q7); an env `off` is a pin like any other
value. This only works after the forwarding change (4.6, point 2).

### 4.3 Apply mechanism in vault-core

- **Boot:** a new hook `15-vault-settings.envsh`. The plan relies on the
  stock nginx entrypoint sourcing executable `*.envsh` files, so the hook
  can export the file's values before `20-envsubst` for every key the env
  leaves unset. **Hard D16c gate:** prove this on the pinned
  `nginx:1.29.8-alpine3.23` image (a build-time check like the existing
  ones in `core/Dockerfile`) before anything else is built on it; without
  it the design needs a different boot path. The same hook supplies the
  defaults compose stops supplying, above all `VAULT_RESOLVER=1.1.1.1`
  when unset or blank: a blank value would render `resolver  ipv6=off`
  (40 §2 dies on it, but the default must come from the hook, not from
  compose).
- **Prerequisite refactor:** `25-vault-eventlog.sh` hard-codes
  `CONF=/etc/nginx/nginx.conf` (line 40), and `40-vault-preflight.sh`
  hard-codes `/etc/nginx/nginx.conf` (line 43),
  `/etc/nginx/vault-upstream-rate.conf` (line 89) and the pool/cap paths
  (lines 114-115). Both get a config-directory parameter, and 40 is split
  into value checks (resolver §2/§2b, wiring asserts; re-run on apply) and
  boot-only checks (volume layout, ownership, writability; boot only).
- **Runtime:** a watcher loop started from a hook, the same pattern as
  `45-vault-eventlog-rotate.sh` (forked before the exec, re-parented to
  the nginx master, not started for `nginx -t`/`-T`). Every few seconds it
  checks `GENERATION`. When it changes:
  1. Parse and validate the file. On failure: status "rejected", nothing
     changes.
  2. Render 20 → 25 → 26 → 27 → 28 into a staging directory (26/27/28
     already take an output path, 25 after the refactor above; envsubst
     via `NGINX_ENVSUBST_OUTPUT_DIR`).
  3. Run 40's value checks against the staging directory.
  4. **Gate: `nginx -t -p /vault -c <staging>/nginx.conf`** (the same
     `-p /vault` as the image `CMD`; without it the root master resolves
     relative temp/log paths outside `/vault` and creates directories
     there, the SEC-FIX-5 class). `nginx -s reload`
     returns 0 even when the new config fails to load (the master only logs
     the error and keeps the old workers), so the reload's exit code is
     never the gate. Only a green `-t` on the staged files lets them be
     copied into `/etc/nginx` (previous files kept as backup).
  5. `nginx -s reload` (same master pid, 45's rotation unaffected), then a
     **post-reload check**: the rendered config carries the generation
     (e.g. `add_header X-Vault-Settings-Generation <n>` on `/health`), and
     the watcher polls `http://127.0.0.1/health` until the header shows
     `<n>`, with a timeout. Timeout: restore the backup, reload again,
     status "rejected: reload failed" with the tail of the error log.
  6. Only then status "applied" with `<n>` (4.4).
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

Decided (Q6, Weg A, 2026-10-14):

| | |
|---|---|
| Volume | `vault-settings` (new) |
| vault-api | `/var/lib/vault-settings`, **rw**. The api image creates the directory owned by `101:101`, so the first mount seeds the volume with that owner. vault-api is the only writer. |
| vault-core | `/etc/vault-settings`, **ro**, `nocopy` |
| vault-runner, vault-proxy, vault-dns | not mounted. The runner gets its values from the DB, which it already mounts. |
| Bind mode | none, named volume only. The file is derived from the DB, so it needs no backup and no `VAULT_*_PATH` variable. |

Seeding order: vault-core has no depends_on on vault-api, so either may
start first. Core mounts with `nocopy` and its image has nothing at the
mount point, so a core-first start leaves the fresh volume empty; the
api mount afterwards still finds it empty and should seed it (content and
the `101:101` owner of the image directory). That is expected Docker
behaviour, not yet measured here: **D16c must prove it live on a fresh
volume in both start orders** (LEARNINGS TH-1-FIX/DEPLOY-FIX-3: every
promised mode needs its own live run). If api-second does not seed, the
api entrypoint must create the file itself, never a root hook in core.

Status back-channel without making the volume writable for core:
vault-core writes `settings-applied.json` (generation, ok/rejected,
reason, and effective value plus source for each core key) to
`/vault/logs/`. Precedent: `29-vault-build-version.sh` already writes
`/vault/logs/vault-core-version.json` (root:root 0644) and vault-api reads
it for `/v1/about` (`about.py` `CORE_VERSION_FILE`). The directory is
root-owned in core and readable by vault-api through the existing
`/vault` mount, so no new mount is needed for it.

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
2. Forwarding change (section 3.5): every key pinned by a compose
   default today goes to `${VAR-}` — `VAULT_RESOLVER`,
   `VAULT_PREFILL_MAX_THREADS`, `VAULT_UPSTREAM_EDGE`,
   `VAULT_SCHEDULE_WINDOW`, `VAULT_UPSTREAM_RATE_WINDOW` and the api/runner
   `:-` keys. Keys already forwarded as `${VAR-}` need no change.
   `VAULT_EVENT_LOG_ROTATE_BYTES` gets forwarded. The shipped defaults
   (edge on, window 03:00-07:00, ...) stay, because they move into code
   and hooks in the same release. Any value the operator wants pinned
   stays in `.env` and then shows as "set via env".
3. **Checklist before the rollout (3.7, point 3):** check the dev and prod
   `.env` files and overlays for every editable key of section 3.1/3.2
   that is present, blank or set. Convert an intended "off" (blank today)
   to `off`; remove accidental pins, because a set key becomes a
   read-only "set via env" value.

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

## 5. Decisions and open points

### 5.1 Decided by the user (2026-10-14)

- **Q1 Precedence: env always wins**, for every key including the nine
  existing ADR-0009 overrides. An env-pinned value is shown as plain text
  with a "set via env" note, not as a disabled input. Reverses ADR-0009
  decision 1: **ADR-0009 addendum is a D16b deliverable**, together with
  one startup warning per DB override that an env value now shadows.
- **Q2 Webhook URL: env-only** (Weg A). D16b removes the PATCH path; an
  existing DB value is ignored with one startup warning; Settings shows
  the URL masked, as text. Webhook events stay editable.
- **Q3 One download window.** Inside: automatic downloads and full speed.
  Outside: no automatic downloads and the rate cap applies.
  `VAULT_SCHEDULE_WINDOW` is that window (env or Settings); its effective
  value reaches core through the settings file (4.2).
  `VAULT_UPSTREAM_RATE_WINDOW` is deprecated: if set in env, startup
  warning, still honoured as an env pin. Later, D8 adds several windows
  per day with a per-window "throttle" checkbox; D16 keeps the grammar and
  file value extensible to a list (4.2) so it does not block that.
- **Q4 Oracle on/off: env-only** (recommendation taken; default, the user
  may object at table sign-off).
- **Q5 `VAULT_EVENT_LOG_ROTATE_BYTES` editable on the core path,
  `VAULT_EVENT_LOG_MAX_BYTES` deprecated** (recommendation taken; default,
  the user may object at table sign-off).
- **Q6 Settings file on a new `vault-settings` volume** (Weg A), section 4.4.
- **Q7 Keyword `off`** (Weg A). Compose forwards `${VAR-}` for every
  editable key; blank or unset = "not set" (Settings, else the shipped
  default). `off` is accepted by `VAULT_SCHEDULE_WINDOW`,
  `VAULT_UPSTREAM_EDGE` (legacy per-name mode), `VAULT_UPSTREAM_RATE` (no
  cap), `VAULT_UPSTREAM_POOL_HOSTS` (none) and the deprecated
  `VAULT_UPSTREAM_RATE_WINDOW`. Parsers and fixtures: 3.6. The accepted
  cost is that existing blank lines flip to the default on upgrade;
  mitigations (release note, `.env.example` edits, Antrag checklist):
  3.7.

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
- `VAULT_SCHEDULE_WINDOW` (D16b): moving the default 03:00-07:00 from
  compose into `config.py` changes every non-compose run: a bare
  `python -m vault_api` and the api test suite get a scheduler window
  where they had none. `api/.env.example` line 210 ships
  `VAULT_SCHEDULE_WINDOW=` (blank = off today) and must become `off` if
  "off" is the intent; api tests that rely on "no window" must set `off`
  explicitly or construct `Settings` with `schedule_window=None`. Mutation
  check the default direction (LEARNINGS "Fail-closed defaults").
- UPD-5 stays as decided: the update *check* runs regardless of the
  window; only automatic downloads are bound to it.
