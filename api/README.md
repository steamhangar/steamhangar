# vault-api

FastAPI + SQLite backend for SteamHangar. Plain `sqlite3` with small helper
functions — no ORM. WP 1.2 shipped the project skeleton (config, DB schema,
auth dependency, `GET /v1/health`); WP 1.3 added depot→app mapping storage and
the games endpoints; WP 1.4 added **prefill orchestration** — a job queue, the
SteamPrefill subprocess runner, and the prefill-driven mapping import; WP 1.5
added **per-game size calculation** (`vault_api/sizes.py`) — a cached "du over
depot folders" (`docs/PROJECT_PLAN.md` §3), wired into `GET /v1/games`/
`GET /v1/games/{appid}`, plus `GET /v1/cache/summary` (§6); WP 1.6 adds
**per-game deletion** (`vault_api/deletion.py`) — `DELETE /v1/cache/{appid}`
with shared-depot protection, path/link safety guards and an audit trail in the
log (§4); WP 2.4 adds the **agent report data path**
(`vault_api/agent_reports.py`) — `POST /v1/agent/installed` with ADR-0002
full-list snapshots, the server-side diff that surfaces removals, snapshot
retention, plus a minimal `GET /v1/clients`; WP 3.1 adds **manifest parsers**
(`vault_api/manifests.py`) — pure functions that turn the two on-disk Steam
manifest formats into one common shape, the foundation for staleness
detection (ADR-0006) and manifest-diff garbage collection (ADR-0007); WP 3.2
adds **schema v3, a durable manifest archive, and worker ingestion**
(`vault_api/depot_manifests.py`, `vault_api/manifest_archive.py`,
`vault_api/manifest_ingest.py`) — after a successful prefill job, the worker
scans SteamPrefill's own manifest temp-cache directory for this app's `.bin`
files, records the latest manifest state per `(appid, depotid)`, archives the
source file durably (SteamPrefill's temp cache does not survive its own
`clear-temp`), and additively maps a shared depot to its "containing" app.
WP 3.3 adds **summary parsing and job outcome honesty**
(`vault_api/prefill_summary.py`) — schema v4 (three new `jobs` columns), a
decode fix for SteamPrefill's OEM-codepage console output, and the
`Updated`/`Up To Date`-driven job-outcome rule (ADR-0006 decision 1) that
stops an unowned app's zero-work run from being reported as a successful
prefill (see "Job outcome honesty" below).
WP 3.9 adds the **opt-in manifest oracle** (`vault_api/oracle.py`,
`vault_api/routers/oracle.py`, schema v10) — ADR-0006 decision 4's Tier-2
staleness source and ADR-0007's beta-branch keep-set protection (decision B).
It is **off by default** and is the only component that talks to anything
outside the LAN; see "Manifest oracle" below, including the privacy note.
Bypass detection and the miss trigger remain scope for later work packages.
WP 4a.1 adds **static serving for the built-in web UI**
(`vault_api/webui.py`) — vault-api now mounts the no-build vanilla SPA in
`web/` (a new top-level sibling of `api/`) as static files, with a
narrowly-scoped SPA fallback for the app's own client-side routes, sane
security headers (CSP included) on every response, and a Cache-Control
split (`index.html` always revalidates; other assets get a short TTL).
See "Web UI static serving" below.

## Layout

```
api/
├── vault_api/
│   ├── main.py           # FastAPI app factory + lifespan (worker + scheduler)
│   ├── config.py         # Settings, read once from env vars
│   ├── db.py             # SQLite schema v10, idempotent init
│   ├── auth.py           # X-Api-Key dependency (constant-time compare)
│   ├── deps.py           # Shared FastAPI dependencies (db_opener)
│   ├── mapping.py        # upsert_mapping() — the depot->app write path
│   ├── jobs.py           # job queue + apps.status transitions
│   ├── prefill.py        # SteamPrefill runner + depot attribution
│   ├── worker.py         # the single background job worker thread
│   ├── sizes.py          # depot disk walk, TTL size cache, summary aggregation
│   ├── deletion.py       # per-game deletion: path/link guards, shared-depot plan
│   ├── agent_reports.py  # agent snapshots: store, ADR-0002 diff, retention
│   ├── manifests.py      # manifest parsers (.bin / cache-stored), pure functions
│   ├── depot_manifests.py # depot_manifests table writes/reads (WP 3.2)
│   ├── manifest_archive.py # durable .bin archive + retention (WP 3.2)
│   ├── manifest_ingest.py  # scan temp-cache -> parse -> store -> archive (WP 3.2)
│   ├── schedule_window.py # window parsing/containment, pure (WP 3.5)
│   ├── scheduler.py      # the second background thread: sweeps (WP 3.5)
│   ├── gc.py             # GC core: keep-set resolution + plan_gc, read-only (WP 3.7)
│   ├── gc_execute.py     # GC execution: deletes what gc.py planned (WP 3.8),
│   │                     #   minus what the grace window holds back (WP 3.8b)
│   ├── webhooks.py       # generic webhook notifications: bounded queue + one
│   │                     #   delivery thread, job/bypass event payloads (WP 3.13)
│   ├── oracle.py         # opt-in third-party manifest oracle (WP 3.9) —
│   │                     #   OFF by default; the one component that talks to
│   │                     #   anything outside the LAN
│   ├── validation.py     # shared request types (AppId) — one coercion rule
│   ├── webui.py          # static serving + SPA fallback for web/ (WP 4a.1)
│   └── routers/
│       ├── health.py     # GET /v1/health — the ONE public router
│       ├── games.py      # GET /v1/games, GET /v1/games/{appid}
│       ├── mapping.py    # PUT /v1/mapping/{depotid}, GET /v1/mapping
│       ├── jobs.py       # POST /v1/prefill, GET /v1/jobs[/{id}]
│       ├── cache.py      # GET /v1/cache/summary, DELETE /v1/cache/{appid},
│       │                 #   POST /v1/cache/{appid}/gc
│       ├── agent.py      # POST /v1/agent/installed
│       ├── clients.py    # GET /v1/clients (minimal v1, stats in Phase 3)
│       ├── schedule.py   # GET /v1/schedule (read-only, env-only config)
│       └── oracle.py     # GET/POST/DELETE /v1/oracle/{appid} (WP 3.9)
├── tests/                # pytest (incl. tests/stub_prefill.py — fake CLI)
├── requirements.txt      # pinned, runtime only, full transitive set
├── requirements-dev.txt  # pinned, adds test-only deps (pytest, httpx)
├── .env.example          # committed template — never commit a real .env
└── pytest.ini
```

## Dependencies

`requirements.txt` is the **complete** resolved runtime set: every
transitive dependency is pinned with `==` (WP SEC-FIX-3). Before that WP only
the four direct dependencies were pinned and everything below them floated,
so two image builds from the same commit could install different code. The
Dockerfile is unchanged; it still runs `pip install -r requirements.txt`,
which now has nothing left to choose. `requirements-dev.txt` adds pytest and
httpx on top of it. Their own transitive dependencies (iniconfig, pluggy,
packaging, httpcore, certifi) are still unpinned. They are test-only and
never reach the image.

Direct dependencies: `fastapi`, `starlette`, `uvicorn[standard]`,
`python-dotenv`. starlette is pinned directly, not only through fastapi,
because the security fixes live in starlette.

**Regenerating.** Edit the direct pins, then resolve universally so that the
native Windows path ("Running natively" below) keeps its `colorama` /
`uvloop` markers:

```
printf 'fastapi==X\nstarlette==Y\nuvicorn[standard]==Z\npython-dotenv==W\n' > top.in
uv pip compile --universal --python-version 3.13 --no-header --no-annotate top.in
```

Then paste the output under the file's header comment, keep `uvicorn[standard]`
as written, run the full suite in a fresh venv, and re-run `pip-audit -r
requirements.txt`.

### Security audit, 2026-10-01 (WP SEC-FIX-3)

Tool: `pip-audit 2.10.1` (PyPI/OSV advisory data), Python 3.13.15.

**Before** (`fastapi==0.115.6`, which caps `starlette<0.47`, so it resolved to
`starlette 0.41.3`; `python-dotenv==1.0.1`): 8 distinct advisories in 2
packages.

| Package | Advisory | Fixed in | Applies to vault-api? |
|---|---|---|---|
| starlette 0.41.3 | CVE-2025-62727 / GHSA-7f5h-v6xp-fcq8: quadratic-time `Range` header parsing in `FileResponse`, an unauthenticated CPU DoS | 0.49.1 | **Yes.** `webui.py` serves `/css` and `/js` through `StaticFiles` and `index.html` through `FileResponse`, all without authentication |
| starlette 0.41.3 | CVE-2025-54121 / GHSA-2c2j-9gv5-cj73: multipart spool rollover blocks the event loop | 0.47.2 | No. vault-api parses no multipart forms |
| starlette 0.41.3 | CVE-2026-48710 / GHSA-86qp-5c8j-p5mr: unvalidated `Host` header used to rebuild `request.url` | 1.0.1 | Indirectly (the framework builds `request.url` for every request) |
| starlette 0.41.3 | CVE-2026-54282 / GHSA-jp82-jpqv-5vv3: unvalidated path used to rebuild `request.url` | 1.3.0 | Indirectly (same as above) |
| starlette 0.41.3 | CVE-2026-54283 / GHSA-82w8-qh3p-5jfq: `max_fields`/`max_part_size` ignored for urlencoded forms | 1.3.1 | No. There is no `request.form()` |
| starlette 0.41.3 | CVE-2026-48818 / GHSA-wqp7-x3pw-xc5r: `StaticFiles` UNC-path SSRF on Windows | 1.1.0 | Native Windows only |
| starlette 0.41.3 | CVE-2026-48817 / GHSA-x746-7m8f-x49c: `HTTPEndpoint` method lookup via `getattr` | 1.1.0 | No. There is no `HTTPEndpoint` |
| python-dotenv 1.0.1 | CVE-2026-28684 / GHSA-mf9w-mj56-hr94: `set_key`/`unset_key` follow symlinks | 1.2.2 | No. `config.py` only calls `load_dotenv()`. Bumped anyway because the fix is a minor bump |

**Bumps.** Each package moved to the smallest version that fixes all of its
advisories:

- `starlette 0.41.3 -> 1.3.1`. 1.3.1 is the highest fix version across all
  seven starlette advisories.
- `fastapi 0.115.6 -> 0.133.0`. This is the first fastapi release that allows
  `starlette>=1.0` (0.130-0.132 cap it at `<1.0.0`, 0.115.x at `<0.47`).
  Measured against PyPI metadata.
- `python-dotenv 1.0.1 -> 1.2.2`.
- `uvicorn[standard]` stays at 0.32.1, which has no advisory.

The full suite passed unchanged in a fresh Python 3.13 venv built from these
pins: 1959 passed, 6 skipped, the same counts as before the bump. Starlette 1.x
emits `StarletteDeprecationWarning` for `HTTP_422_UNPROCESSABLE_ENTITY`
(renamed `HTTP_422_UNPROCESSABLE_CONTENT`) and for httpx in its TestClient.
These are warnings only. The rename is left for a later WP.

**After:** `pip-audit -r requirements.txt` reports "No known vulnerabilities
found". `pip-audit -r requirements-dev.txt` still reports
CVE-2025-71176 / GHSA-6w46-j5rx-g56g in **pytest 8.3.4** (predictable
`/tmp/pytest-of-<user>` directory, fixed in 9.0.3). pytest is test-only and
never installed in the image. The major bump is left open on purpose.

## Configuration

Read once at startup from environment variables (`vault_api/config.py`).
Copy `.env.example` to `.env` and adjust:

| Variable                        | Required | Default      | Purpose                                                            |
|---------------------------------|----------|--------------|--------------------------------------------------------------------|
| `VAULT_API_KEY`                 | yes      | *(none)*     | Shared secret for the `X-Api-Key` header                           |
| `VAULT_DB_PATH`                 | no       | `./vault.db` | SQLite database file                                               |
| `VAULT_CACHE_ROOT`              | no       | `./cache`    | Depot cache root — diffed before/after a prefill, and the **deletion base** (guarded, see below). **Exception to the blank-means-default rule below (WP 4f):** an ABSENT variable falls back to `./cache`, but a PRESENT-and-empty one refuses to boot — see "Path safety" |
| `VAULT_LOG_LEVEL`               | no       | `INFO`       | Log level                                                          |
| `VAULT_STEAMPREFILL_PATH`       | no*      | *(empty)*    | Path to the SteamPrefill executable; *required to run prefill jobs* |
| `VAULT_PREFILL_TIMEOUT_SECONDS` | no       | `14400`      | Hard time budget for one SteamPrefill run (hang backstop)           |
| `VAULT_PREFILL_MAX_THREADS`     | no       | `8`          | Whole number 1-64: concurrent chunk requests per SteamPrefill run, passed as its hidden `--max-threads N` flag on every prefill in both modes (SteamPrefill's own default is 30). Keeps a prefill from exhausting a carrier-grade NAT (DS-Lite). Env-only. See "SteamPrefill concurrency (`--max-threads`)" (WP CORE-FIX-2) |
| `VAULT_WORKER_POLL_SECONDS`     | no       | `1.0`        | Worker sleep between polls of an empty queue                        |
| `VAULT_PREFILL_MODE`            | no       | `subprocess` | `subprocess` (vault-api runs SteamPrefill itself, unchanged pre-WP-S-1 behaviour) \| `queue` (hand execution off to a separate `prefill_runner` process — see "Queue mode" below). Any other value is refused at startup |
| `VAULT_RUNNER_HEARTBEAT_SECONDS` | no      | `5.0`        | `queue` mode only: how often `prefill_runner` refreshes its lease while executing; **must be > 0** |
| `VAULT_RUNNER_LEASE_TIMEOUT_SECONDS` | no  | `30.0`       | `queue` mode only: how stale a runner's lease must be before it is presumed dead; **must be > 0** |
| `VAULT_RUNNER_POLL_SECONDS`     | no       | `1.0`        | `prefill_runner` only: how often it polls for a job to claim when idle. Set on the `prefill_runner` process/container's own environment, not vault-api's — vault-api's worker reuses `VAULT_WORKER_POLL_SECONDS` for its own wait-on-hand-off loop in `queue` mode; **must be > 0** |
| `VAULT_SIZE_CACHE_TTL`          | no       | `60`         | TTL (seconds) for the in-process per-game size cache; **must be > 0** (see below) |
| `VAULT_AGENT_REPORT_KEEP`       | no       | `20`         | Agent report snapshots kept per `client_id`; **must be >= 2** (see "Agent reports") |
| `VAULT_STEAMPREFILL_CACHE_DIR`  | no       | platform default (see below) | SteamPrefill's own manifest temp-cache directory, scanned after a successful prefill job (see "Manifest ingestion") |
| `VAULT_MANIFEST_ARCHIVE_DIR`    | no       | `<dir of VAULT_DB_PATH>/manifests` | Where archived manifest `.bin` files are copied durably |
| `VAULT_MANIFEST_KEEP`           | no       | `3`          | Archived manifests kept per depot (total, current included); **must be >= 1** |
| `VAULT_GC_GRACE_DAYS`           | no       | `14`         | Days a stored chunk is protected from GC, by store time (`ctime`); `0` disables the window; **must be >= 0**. See "Garbage collection → The recently-stored grace window" |
| `VAULT_SCHEDULE_WINDOW`         | no       | *(empty — scheduler OFF)* | Daytime window the scheduler sweeps in, `HH:MM-HH:MM` **server-local** time (e.g. `09:00-17:00`, `22:00-06:00`). Unset/blank = disabled. See "Scheduler" |
| `VAULT_SCHEDULE_INTERVAL_MINUTES` | no     | `180`        | Minimum spacing between two sweeps (plan §7's "every 3 h"); **must be > 0** |
| `VAULT_SCHEDULE_CLIENT_STALE_DAYS` | no    | `7`          | A client whose newest agent report is older than this drops out of the sweep's target set; **must be > 0** |
| `VAULT_AUTO_GC`                 | no       | `execute` (**was `off` through WP 3.12; flipped by WP SWEEP-1 / ADR-0014, 2026-08-22**) | `off` \| `dry-run` \| `execute` — queue a GC job after a prefill that actually **updated** something (WP 3.12). See "Job control → Auto-GC" |
| `VAULT_EVENT_LOG_PATH`          | no       | *(empty — sweep OFF)* | Path to vault-core's structured cache-event log (WP 3.10). Unset/blank = the whole cache-event feature is off. See "Cache-event sweep" |
| `VAULT_EVENT_SWEEP_INTERVAL_MINUTES` | no  | `5`          | Minutes between sweeps of that log; **must be > 0**. Deliberately independent of `VAULT_SCHEDULE_WINDOW` |
| `VAULT_MISS_TRIGGER_COOLDOWN_MINUTES` | no | `60`         | Per-app cooldown for miss-triggered prefill; **`0` turns the trigger OFF** (statistics keep running); **must be >= 0** |
| `VAULT_MISS_TRIGGER_MAX_PER_SWEEP` | no    | `5`          | Hard cap on miss-triggered enqueues per sweep (storm backstop); **must be > 0** |
| `VAULT_BYPASS_WINDOW_DAYS`      | no       | `3`          | Cache-log silence beyond this many days makes a still-reporting client `bypass_suspected`; **must be > 0** |
| `VAULT_CLIENT_STATS_KEEP`       | no       | `48`         | Per-sweep statistics rows kept per client address; **must be > 0** |
| `VAULT_EVENT_LOG_MAX_BYTES`     | no       | `67108864`   | Truncate the event log at/above this size once fully swept; `0` = never truncate; **must be >= 0** |
| `VAULT_WEBHOOK_URL`             | no       | *(empty — webhooks OFF)* | Generic JSON webhook target (WP 3.13). Unset/blank = the whole feature is off. See "Webhooks" |
| `VAULT_WEBHOOK_EVENTS`          | no       | *(all five)* | Comma list of events to send: `job.done`, `job.error`, `job.cancelled`, `client.bypass_suspected`, `client.bypass_resolved`. Unknown names or empty entries fail at startup |
| `VAULT_WEBHOOK_TIMEOUT_SECONDS` | no       | `5`          | Per-attempt HTTP timeout for one delivery try; **must be > 0** |
| `VAULT_NAME`                    | no       | *(empty)*    | Optional label carried as `"vault_name"` in every webhook payload — omitted entirely when unset. Purely cosmetic, for an operator running more than one SteamHangar instance. Validated: at most 64 characters after trimming surrounding whitespace, printable characters only (no tab, newline or other control character inside the name); anything else refuses startup with a clear `RuntimeError` naming `VAULT_NAME`, and is answered `422` on `PATCH /v1/settings`. **Upgrade caveat:** a deployment whose existing `VAULT_NAME` is longer than 64 characters or contains a non-printable character such as a tab stops booting until the value is shortened or cleaned |
| `VAULT_STEAM_LIBRARY_STEAMID`   | no       | *(empty — not set)* | WP API-FEAT-1. The one SteamID64 whose library the web UI and the app show, stored per vault instead of per device. Both frontends read it from `GET /v1/settings` (key `steam_library_steamid`). Public identifier, not a secret. Validated with the Steam relay's own grammar (`steam_relay.valid_steamid64`: exactly 17 ASCII digits in the individual-account range, surrounding whitespace trimmed); anything else refuses startup with a `RuntimeError` naming the variable, and is answered `422` on `PATCH /v1/settings` |
| `VAULT_MANIFEST_ORACLE`         | no       | *(empty — oracle OFF)* | Third-party manifest oracle. Only `steamcmd_api` is implemented. **Enabling it makes vault-api send app ids to a service outside your LAN** — see "Manifest oracle" below before setting it. Any other value is refused at startup |
| `VAULT_MANIFEST_ORACLE_URL`     | no       | `https://api.steamcmd.net/v1/info` | Base URL the oracle asks (`<base>/<appid>`). Point it at your own mirror to keep the queries on your network. Must be `http`/`https`; redirects away from it are never followed |
| `VAULT_MANIFEST_ORACLE_TIMEOUT` | no       | `10`         | Socket timeout (seconds) for one oracle request; **must be > 0**. A timeout is an ordinary "no data" outcome, never an error the API surfaces |
| `VAULT_EGRESS_ALLOW`            | no       | *(empty)*    | WP EG-1 (ADR-0011). Comma-separated bare hostnames the egress-lock proxy (`deploy/proxy`) may additionally reach on vault-api's behalf, beyond the one host baked into that proxy's image unconditionally (`api.steampowered.com`, the Steam Web API relay). **Enabling `VAULT_MANIFEST_ORACLE` with its host missing from this list refuses to boot** — see "Egress lock" below. Each entry must look like a plausible hostname (letters, digits, `.`, `-` only; no leading/trailing `.`/`-`) or vault-api refuses to boot |
| `VAULT_SWEEP_INCLUDE_CACHED`    | no       | `true` (**was `false` through WP 4d; flipped by WP SWEEP-1 / ADR-0014, 2026-08-22**) | Strict boolean (`true`/`1`/`yes`/`on` or `false`/`0`/`no`/`off`, case-insensitive). Widens the scheduler's target set from "installed on a fresh agent" to installed PLUS everything holding cache content. Runtime-editable via `PATCH /v1/settings`. See "Sweep target set — installed PLUS cached" |
| `VAULT_RELAY_EXPOSE_PLAYTIME`   | no       | `false`      | Strict boolean. Privacy gate for the Steam Web API relay: off = `GET /v1/steam/owned-games` omits `playtime_forever`. Env-only. See "The privacy gate" (WP 4h.0, ADR-0010) |
| `VAULT_RELAY_EXPOSE_LAST_PLAYED` | no      | `false`      | Strict boolean. Same gate for `rtime_last_played`, independent of the playtime switch. Env-only. See "The privacy gate" |
| `VAULT_SETTINGS_READONLY`       | no       | `false`      | Strict boolean. Operator hard-lock: on = `PATCH /v1/settings` answers `403` (`GET` still works; the `/v1/steam/key` routes are not covered). Env-only by construction. See "`VAULT_SETTINGS_READONLY` — the operator hard-lock" |
| `VAULT_WEB_DIR`                 | no       | the repo's `web/` directory (resolved relative to the package) outside the image; the image sets `/app/web` (api/Dockerfile) | Directory the web UI's static files are served from. Blank = the default. Env-only. See "Web UI static serving" |

**All twenty numeric settings are parsed strictly (WP 3.12).** Thirteen take a
whole number (`VAULT_PREFILL_TIMEOUT_SECONDS`, WP CORE-FIX-2's
`VAULT_PREFILL_MAX_THREADS` (also bounded to 1-64), `VAULT_AGENT_REPORT_KEEP`,
`VAULT_MANIFEST_KEEP`, `VAULT_GC_GRACE_DAYS`,
`VAULT_SCHEDULE_INTERVAL_MINUTES`, `VAULT_SCHEDULE_CLIENT_STALE_DAYS`,
and WP 3.11's `VAULT_EVENT_SWEEP_INTERVAL_MINUTES`,
`VAULT_MISS_TRIGGER_COOLDOWN_MINUTES`, `VAULT_MISS_TRIGGER_MAX_PER_SWEEP`,
`VAULT_BYPASS_WINDOW_DAYS`, `VAULT_CLIENT_STATS_KEEP`,
`VAULT_EVENT_LOG_MAX_BYTES`) and seven take a decimal
(`VAULT_WORKER_POLL_SECONDS`, `VAULT_SIZE_CACHE_TTL`, WP 3.13's
`VAULT_WEBHOOK_TIMEOUT_SECONDS`, WP 3.9's
`VAULT_MANIFEST_ORACLE_TIMEOUT`, and WP S-1's `VAULT_RUNNER_HEARTBEAT_SECONDS`/
`VAULT_RUNNER_LEASE_TIMEOUT_SECONDS`/`VAULT_RUNNER_POLL_SECONDS` — all through
the same `_env_float`). `VAULT_PREFILL_MODE` follows the separate strict-enum
rule `VAULT_AUTO_GC`/`VAULT_MANIFEST_ORACLE` already use (see "Queue mode"
below) rather than the numeric grammar.
Each family goes through exactly one validator, with the same house rule:

| | Accepted grammar | Examples that pass | Examples that are now refused |
|---|---|---|---|
| integers (`_env_int`) | ASCII digits only | `7`, `14400` | `" 7 "`, `"+7"`, `"-7"`, `"1_0"`, `"٧"`, `"0x7"`, `"1.5"` |
| decimals (`_env_float`) | ASCII digits, optionally **one** `.` with digits on **both** sides | `60`, `1.0`, `0.25`, `3.5` | `" 1.5 "`, `"+1.5"`, `"1_0"`, `"٧"`, `"1e3"`, `".5"`, `"5."`, `"1,5"`, **`"nan"`**, **`"inf"`** |

No signs, no whitespace, no underscores, no exponents, no thousands
separators, no non-ASCII digits. Write `0.5`, not `.5`.

**Why this is not pedantry.** Python's `int()`/`float()` are far more
permissive than anyone configuring a service expects — most dangerously
`"1_0"`, which both read as **ten**. `float()` additionally accepts `"nan"` and
`"inf"`, and `nan` is the one value that slips past a range check silently
(`nan <= 0` is `False`). Downstream that is not harmless: a `nan`
`VAULT_SIZE_CACHE_TTL` makes the size cache's `(now - computed_at) < ttl`
freshness test *always false*, so every request re-walks the whole `depot/`
tree — exactly the footgun the "`0` is rejected, there is no disable switch"
rule below exists to prevent, reintroduced through the back door; a `nan`
`VAULT_WORKER_POLL_SECONDS` is handed straight to `threading.Event.wait()`.
A grammatically valid but absurdly long digit string (400 digits) also
overflows to `inf` in `float()` and is refused explicitly.

**For these nineteen numeric settings** (and every other blank-means-off switch
in the table above — `VAULT_SCHEDULE_WINDOW`, `VAULT_EVENT_LOG_PATH`,
`VAULT_WEBHOOK_URL`, `VAULT_MANIFEST_ORACLE`, `VAULT_STEAMPREFILL_PATH`), a
**blank** value still means "not configured" and falls back to the default (a
stray space after `=` in a `.env` file must not stop the service). Consequence
worth knowing: a negative value is now reported as a *syntax* error rather than
a range error — still loud, still at startup, and the message names the
smallest accepted value. Range rules are unchanged and still apply after the
grammar check (`VAULT_SIZE_CACHE_TTL=0` is still refused).
`tests/test_config.py` parses the shipped `.env.example` and asserts every
documented value still passes.

**`VAULT_CACHE_ROOT` is the one NAMED EXCEPTION to "blank still means
default" (WP 4f).** Every setting above has a value that means "feature off"
or a value that means "unset, use the default" — blank is safe to treat as
either because there is always a safe *something* behind it. There is no safe
"cache root off": it is the deletion base, the size-scan root, and (with
`VAULT_SWEEP_INCLUDE_CACHED` on) the sweep's own scan target, so a present-but-
blank value is refused at startup rather than silently accepted as "use the
default" — see "Path safety" below for the mechanism (a present-but-blank
value bypasses `os.environ.get`'s own default, which only ever applies to an
ABSENT key) and for why an *unforwarded* compose key is not the case this
guards (it is simply absent, and the default applies fine).

`VAULT_API_KEY` has no default. Starting the app without it raises
`RuntimeError` immediately (`Settings.from_env`) — this is the "fail loudly"
behavior required by the work package, verified in `tests/test_config.py`.

`VAULT_STEAMPREFILL_PATH` is deliberately **not** validated at startup (the
`no*` above): vault-api must still serve `/v1/games`, `/v1/mapping` and
`/v1/health` on a host where SteamPrefill hasn't been set up yet. A missing or
wrong path fails the individual *job* with an actionable message
(`tests/test_worker.py::test_missing_steamprefill_path_fails_the_job_but_not_the_app`).
The numeric settings reject malformed and non-positive values loudly at startup
(see the grammar table above). In particular **`VAULT_SIZE_CACHE_TTL`
must be greater than 0**: `0` is rejected rather than accepted as "no caching",
because a zero TTL would mean a full `depot/` tree walk on *every* request —
a footgun on a large cache, not a feature. Set a small value (e.g. `1`) if you
want near-live numbers; note the cache is invalidated explicitly after a
successful prefill and after a deletion anyway, so a long TTL does not make
those two events look stale.

`VAULT_STEAMPREFILL_CACHE_DIR`'s platform default (WP 3.2,
`config._default_steamprefill_cache_dir`, `docs/research/phase3-manifests.md`
§1a): `%LOCALAPPDATA%\SteamPrefill\v1` on Windows (falling back to
`~\AppData\Local` if `LOCALAPPDATA` is unset), `$HOME/.cache/SteamPrefill/v1`
everywhere else — the path inside the container, which will need volume/env
wiring in `deploy/`'s Compose file (explicitly **not** this work package's
scope — a follow-up on top of WP 1.9). `VAULT_MANIFEST_ARCHIVE_DIR`'s default
is a `manifests` sibling of `VAULT_DB_PATH`'s directory (`config._default_manifest_archive_dir`)
— consistent with a single persistent volume holding both the database and
the archive, same caveat about `deploy/` wiring.

The three `VAULT_SCHEDULE_*` settings are validated at **startup**, window
included — a malformed window raises `RuntimeError` from `Settings.from_env`
rather than failing on the first tick hours later inside a background thread
where nobody is watching. The two numbers are validated even when no window is
set, so a typo surfaces the day it is made rather than the day the scheduler
is switched on.

The three `VAULT_MANIFEST_ORACLE*` settings follow the same rule (WP 3.9): the
URL and the timeout are validated at startup **even while the oracle is off**.
`VAULT_MANIFEST_ORACLE` itself is the one place where a wrong value is a hard
startup error rather than a soft "no data": leaving the variable unset is how
an operator asks for *off*, so a non-empty value is an explicit request for a
feature, and a typo in it must not quietly look like it worked. Once the
oracle is running, every failure it can have is soft — see below.

## Database schema (v15)

Created idempotently at startup by `vault_api/db.py::init_db` (safe to call
on every process start — uses `CREATE TABLE IF NOT EXISTS` and only seeds
`schema_version` once).

| Table            | Columns                                                                                   | Purpose |
|------------------|--------------------------------------------------------------------------------------------|---------|
| `schema_version` | `version`                                                                                   | Single-row marker for future migrations |
| `apps`           | `appid` (PK), `name`, `status`, `last_prefill_at`, `last_manifest_check`, `needs_force`     | One row per tracked Steam app. `needs_force` (**v5**, WP 3.4) is ADR-0006 decision 2's per-app flag — see "needs_force" below |
| `depot_app_map`  | `depotid`, `appid`, PK `(depotid, appid)`                                                   | Depot→app mapping; a depot can map to multiple apps (shared depots, plan §4) |
| `jobs`           | `id` (PK autoincrement), `appid`, `type`, `status`, `created_at`, `started_at`, `finished_at`, `log_excerpt`, `updated`, `up_to_date`, `summary_parse_ok`, `gc_execute`, `paused_at`, `stop_request`, `run_use_force`, `run_before_json`, `run_claimed_by`, `run_claimed_at`, `run_heartbeat_at`, `run_completed_at`, `run_result_json` | Prefill/GC job queue (plan §3, §6). `updated`/`up_to_date`/`summary_parse_ok` (**v4**, WP 3.3) are SteamPrefill's own summary-table counters — see "Job outcome honesty" above. `gc_execute` (**v7**, WP 3.8) is the GC dry-run/execute bit: `NULL` for every non-GC job, `0` = report only, `1` = delete — see "Garbage collection" below. `paused_at`/`stop_request` (**v8**, WP 3.12) are job control: when the job was last suspended, and the operator's pending `cancel`/`pause` request against a *running* job — see "Job control" below. The seven `run_*` columns (**v15**, WP S-1, ADR-0012) are queue-mode's job hand-off to a separate `prefill_runner` process — `NULL` for every job that never goes through queue mode (any GC job, any prefill job run in the default `subprocess` mode). See "Queue mode" below |
| `agent_reports`  | `client_id`, `reported_at`, `appids` (JSON array of ints), `source_addr`                     | One row per agent report — a full installed-app-ID snapshot at that timestamp (ADR-0002: the agent is stateless/dumb, always reports the complete list). vault-api derives additions/removals by diffing the two most recent rows per `client_id`. `source_addr` (**v9**, WP 3.11) is the address the report arrived FROM — the only key correlating a `client_id` with the event log's addresses; `NULL` for pre-v9 rows, which is why such a client is never `bypass_suspected` |
| `depot_manifests` | `appid`, `containing_appid`, `depotid`, `manifestid` (TEXT), `chunk_count`, `total_bytes`, `recorded_at`, `source`, `first_seen_at`, `manifest_changed_at`, `observation_count`, PK `(appid, depotid)` | **Latest**-known manifest state per (app, depot) — WP 3.2, ADR-0006 decision 3. `first_seen_at`/`manifest_changed_at`/`observation_count` (**v14**, WP 4h.1) are the change-frequency bookkeeping — see "Manifest ingestion" and "Change frequency" below |
| `oracle_app_state` | `appid` (PK), `buildid`, `checked_at`, `source`, `depot_count`, `branch_count` | **v10**, WP 3.9. One row per app the opt-in oracle has been asked about: when, by which oracle (`source` provenance), and what it said the public build id is. See "Manifest oracle" below |
| `oracle_branch_manifests` | `appid`, `depotid`, `branch`, `manifestid` (TEXT), `recorded_at`, `source`, PK `(appid, depotid, branch)` | **v10**, WP 3.9. One row per (app, depot, **open** branch) → manifest gid. Password-protected branches are never inserted at all. Written with snapshot semantics (a refresh replaces the app's rows in one transaction). **Never mixed into `depot_manifests`** — a third-party claim must stay distinguishable from a manifest vault-api parsed itself |
| `schedule_state` | `id` (PK, `CHECK (id = 1)`), `last_sweep_at`, `last_sweep_targets`, `last_sweep_enqueued` | **v6**, WP 3.5. Single-row scheduler bookkeeping: when the last sweep started (UTC) and what it did. Persisted rather than in-memory so a restart mid-window does not re-sweep — see "Scheduler" below. The two counters are `NULL` while a sweep is in flight (or if the process died during one) |
| `event_sweep_state` | `id` (PK, `CHECK (id = 1)`), `cursor_offset`, `first_sweep_at`, `last_sweep_at`, `last_rotated_at`, `lines_read_total`, `lines_skipped_total`, `last_lines`, `last_skipped`, `last_enqueued`, `last_dropped_by_cap`, `truncate_denied_count`, `last_truncate_denied_at`, `oversized_skips_total`, `last_oversized_at` | **v9**, WP 3.11. Single-row cache-event sweep bookkeeping. `cursor_offset` is the durable contract (each line read once); `first_sweep_at` is how bypass detection knows how long the feed has been watched; `truncate_denied_count` records that the sweeper may read the log but not rotate it; `oversized_skips_total` records a newline-free region longer than a read batch having to be discarded. See "Cache-event sweep" below |
| `client_cache_stats` | `client_addr`, `window_at`, `requests`, `hits`, `misses`, `bypasses`, `errors`, `bytes_served`, `last_seen`, PK `(client_addr, window_at)` | **v9**, WP 3.11. One row per client address per sweep window (plan §5/§6 "per-client hit stats"). `requests = hits + misses + bypasses + errors` by construction; the three cache counters and `bytes_served` include **2xx responses only** (event-log field 9). Retention: `VAULT_CLIENT_STATS_KEEP` windows per address |
| `depot_miss_stats` | `depotid` (PK), `miss_count`, `mapped`, `first_seen`, `last_seen`   | **v9**, WP 3.11. Which depots are being MISSed, and whether a mapping existed at the last sighting. ADR-0008's "misses on unmapped depots are counted but trigger nothing" is what this table counts. Bounded to `event_sweep.MAX_DEPOT_MISS_ROWS` (500) least-recently-seen-first |
| `miss_trigger_state` | `appid` (PK), `last_triggered_at`, `trigger_count`                | **v9**, WP 3.11. The miss trigger's per-app cooldown, persisted so a restart cannot become "re-trigger everything that misses next" |
| `client_bypass_state` | `client_id` (PK), `bypass_suspected`, `updated_at`               | **v10**, WP 3.13. One row per client holding the LAST computed `bypass_suspected` verdict, so the cache-event sweep can fire `client.bypass_suspected`/`client.bypass_resolved` only on the TRANSITION (either direction), never on the steady state. Only populated when `VAULT_WEBHOOK_URL` is set and at least one of the two events is enabled — see "Webhooks" below |
| `steam_relay_key` | `id` (PK, `CHECK (id = 1)`), `api_key`, `updated_at`               | **v12**, WP 4a.6r. Single-row: the opt-in Steam Web API relay's one revocable, read-scoped Web API key (ADR-0004 addendum), entered by the operator in the web UI and set via `PUT /v1/steam/key`. Never a password. See "Steam Web API relay" below |
| `settings` | `key` (PK), `value`, `updated_at` | **v13**, settings-API work package (ADR-0009). One row per OVERRIDDEN key only — a key with no row falls through to its env value or built-in default. `value` is TEXT in the same string grammar the corresponding `VAULT_*` env var uses. See "Persisted settings" below |

Indexes beyond the primary keys: `idx_depot_app_map_appid` on
`depot_app_map(appid)` (plan §4's main lookup direction is appid → depots,
e.g. for delete/size-by-game), `idx_agent_reports_client_time` on
`agent_reports(client_id, reported_at DESC)` (fetch the latest report(s) per
client for the diff), `idx_jobs_status_id` on `jobs(status, id)` plus
`idx_jobs_appid_status` on `jobs(appid, status)` (**v2**, WP 1.4 — the worker
asks "oldest queued job?" on every poll tick and `POST /v1/prefill` asks "does
this app already have a queued/running job?" on every request; without those
two indexes both scan the whole append-only `jobs` table), and
`idx_depot_manifests_depotid` on `depot_manifests(depotid)` (**v3**, WP 3.2 —
a future GC pass needs "every app's current manifest for this depot", which
the `(appid, depotid)` primary key alone doesn't serve efficiently), and
`idx_oracle_branch_manifests_depotid` on `oracle_branch_manifests(depotid)`
(**v10**, WP 3.9 — the GC keep-set query asks the same depot-first question of
the oracle table, for the same shared-depot reason).

**Migration story.** Every statement in `db.py`'s DDL is
`CREATE ... IF NOT EXISTS`, and every version bump so far has been purely
additive: v1 → v2 added two `jobs` indexes, v2 → v3 added `depot_manifests`
and its one index. `init_db` upgrades an existing older file by running the
current DDL and then recording the new `schema_version` — no per-version
data migration has been needed yet. A stored version *higher* than
`SCHEMA_VERSION` raises `RuntimeError` instead of silently operating on a
schema this code doesn't know (downgrade guard). The first non-additive change
will need a real per-version step list — that's called out in `init_db`'s
docstring. Both upgrade paths are covered in `tests/test_db.py`.

**v3 → v4 (WP 3.3) needed a real migration step, not just a version bump.**
Adding three nullable `jobs` columns is still additive data-wise, but
`CREATE TABLE IF NOT EXISTS jobs` — the mechanism every earlier bump relied
on — is a no-op against an *existing* `jobs` table from v1-v3, which lacks
the columns. `init_db` now runs an explicit
`db._add_missing_job_columns` step (`ALTER TABLE jobs ADD COLUMN ...`,
guarded per-column via `PRAGMA table_info` so calling it twice never raises
`duplicate column name`) for any database recorded below v4, before the rest
of `_DDL` runs; a brand-new database gets the columns directly from `_DDL`'s
`CREATE TABLE` and skips this step entirely. Covered by
`tests/test_db.py::test_init_db_upgrades_a_v3_database_to_v4_in_place` (a
pre-existing job row survives with the new columns `NULL`, never a guessed
value) and `..._idempotent_if_called_twice`.

**v4 → v5 (WP 3.4) is the same situation, one column.** `apps.needs_force`
(ADR-0006 decision 2) needs its own `db._add_missing_app_columns` step for
the same reason: `CREATE TABLE IF NOT EXISTS apps` is a no-op against an
existing pre-v5 `apps` table. Unlike the v4 columns, this one is
`NOT NULL DEFAULT 1` — SQLite applies a constant `ALTER TABLE ... ADD COLUMN
... DEFAULT` retroactively to existing rows, so an app that predates this
migration starts at `needs_force=1` (forced), which costs at most one
redundant `--force` run per app after the upgrade and is the safe side to
err on for an app this code has no force-history for. Covered by
`tests/test_db.py::test_init_db_upgrades_a_v4_database_to_v5_in_place` and
`..._idempotent_if_called_twice`.

**v7 → v8 (WP 3.12) reuses the same per-column step, with one fix.** The
`jobs` migration loop used to hardcode `ALTER TABLE jobs ADD COLUMN <name>
INTEGER`, which was fine while every added column was an integer. `paused_at`
and `stop_request` are `TEXT`, so the loop now carries a type per column
(`db._POST_V1_JOB_COLUMNS`) — otherwise an upgraded database and a freshly
created one would report the same `schema_version` with different column
types. Pinned by
`tests/test_db.py::test_init_db_upgrades_a_pre_v8_database_by_adding_the_job_control_columns`
and by `..._a_fresh_database_and_an_upgraded_one_agree_on_the_jobs_columns`,
which takes a v1 file all the way up and compares the full column list against
a fresh one.

**v8 → v9 (WP 3.11) is a mix of both mechanisms, which is worth naming.** The
four new tables (`event_sweep_state`, `client_cache_stats`, `depot_miss_stats`,
`miss_trigger_state`) are the *v6 situation* — plain `CREATE TABLE IF NOT
EXISTS`, so an older database simply gains four empty tables and needs no step.
`agent_reports.source_addr` is the *v4/v5/v8 situation* — an existing
`agent_reports` table lacks the column and `CREATE TABLE IF NOT EXISTS` is a
no-op against it — so it gets a third per-column `ALTER` step,
`db._add_missing_agent_report_columns`, carrying an explicit `TEXT` type for
the reason WP 3.12 discovered (a hardcoded type gives an upgraded database a
different column affinity from a fresh one at the same `schema_version`). The
column is nullable with **no default**: a report stored before v9 has no
recorded address and there is no honest value to invent, so `NULL` means
"unknown" and every consumer treats unknown as "cannot correlate, therefore
cannot accuse".

**v9 → v10 (WP 3.9) needs no migration step at all.** Both new tables are
brand-new `CREATE TABLE IF NOT EXISTS` statements (like v6's `schedule_state`
and like v9's four sweep tables, unlike v4/v5/v7/v8/v9's added columns), so an
older database simply gains two empty tables — which read as "the oracle has
never said anything", exactly the state a fresh install with the oracle off is
in. Covered by
`tests/test_db.py::test_init_db_upgrades_a_v9_database_to_v10_in_place` (both
tables and `idx_oracle_branch_manifests_depotid` reappear, they come out
empty, and a pre-existing `apps` row survives) and
`..._upgrade_to_v10_is_idempotent_if_called_twice`.

(This work package was developed against v8 and has been renumbered twice by
rebases — onto WP 3.12, which claimed v8, and onto WP 3.11, which claimed v9.
That is harmless precisely because it adds no column to anything those
versions touched: the three `ALTER` steps are each guarded per column via
`PRAGMA table_info`, so which file gets which column is decided by what the
file actually has, not by where this table bump landed in the sequence.)

**v11 → v12 (WP 4a.6r) needs no migration step at all.** `steam_relay_key` is
a brand-new `CREATE TABLE IF NOT EXISTS` (the v6/v9/v10 situation, not the
v4/v5/v8/v9 add-a-column one), so an older database simply gains one empty
table, which reads as "no relay key configured" — exactly the state a fresh
install is in before the operator ever opens the web UI's settings. Covered by
`tests/test_db.py::test_init_db_upgrades_a_v11_database_to_v12_in_place` and
`..._upgrade_to_v12_is_idempotent_if_called_twice`.

**v13 → v14 (WP 4h.1) is the v4/v5/v9 situation again, on `depot_manifests`
this time.** Three columns — `first_seen_at`, `manifest_changed_at`,
`observation_count` — needed for the "how often does this game change" panel
field, and `CREATE TABLE IF NOT EXISTS depot_manifests` is a no-op against an
existing v3-v13 table that lacks them. `db._add_missing_depot_manifest_columns`
adds them via `ALTER TABLE ... ADD COLUMN`, guarded per column exactly like
the earlier steps. Two details that make this one slightly different from its
predecessors:

- `first_seen_at`/`manifest_changed_at` are added **NULLable**, not
  `NOT NULL DEFAULT ...` like `apps.needs_force` was — SQLite's `ADD COLUMN`
  can only default to a *constant*, and the honest default for "when was this
  row first observed" is "whatever `recorded_at` already says", not a literal.
  A follow-up `UPDATE ... SET first_seen_at = recorded_at WHERE first_seen_at
  IS NULL` (and the same for `manifest_changed_at`) backfills them
  immediately afterwards, inside the same migration step. This is the
  *conservative* direction, not a guess dressed up as one: a pre-existing
  row's TRUE first-observed moment is unknown, and dating it to its LATEST
  known touch can only ever make the row look YOUNGER than it really is,
  never older — which is exactly the side the two honesty pins below want a
  wrong guess to fall on.
- `observation_count` DOES get a constant default (`DEFAULT 1`, the
  `apps.needs_force` shape) — 1 is genuinely correct for a migrated row: it
  resets every pre-existing depot back to "insufficient data" (pin 2's
  boundary) until it is genuinely re-observed at least once post-upgrade,
  rather than inventing an observation history this code never recorded.

Also guarded against `depot_manifests` not existing at all yet (a v1/v2
database, from before v3 introduced the table in the first place) —
`PRAGMA table_info` on a missing table returns zero rows rather than raising,
so the per-column loop is skipped entirely in that case and the unconditional
`CREATE TABLE IF NOT EXISTS` right after creates the table fresh, with every
v14 column already `NOT NULL` and nothing to backfill. Covered by
`tests/test_db.py`'s v13→v14 upgrade test plus its idempotent-if-called-twice
sibling, and by `tests/test_depot_manifests.py`'s upsert-semantics tests.

**v14 → v15 (WP S-1, ADR-0012) reuses the v4/v7/v8 per-column `jobs` step
again, for seven columns this time.** `run_use_force`, `run_before_json`,
`run_claimed_by`, `run_claimed_at`, `run_heartbeat_at`, `run_completed_at`,
`run_result_json` are all nullable with no default, so they join
`db._POST_V1_JOB_COLUMNS`/`db._add_missing_job_columns` rather than needing a
fourth mechanism — the same guarded loop that already knows how to bring a
`jobs` table at any version from v1 to v14 up to date now also knows about
these seven. Covered by
`tests/test_db.py::test_init_db_upgrades_a_v14_database_to_v15_in_place` and
`..._a_fresh_database_and_a_v14_upgrade_agree_on_the_jobs_columns`.

**Ordering fix (WP 1.5 carry-over from the WP 1.4 review):** `init_db` now
creates only the `schema_version` table, reads the stored version, and checks
the downgrade guard *before* running the rest of `_DDL` — previously the full
DDL ran unconditionally first and the guard was checked afterwards, so it read
as a gate without functioning as one (harmless so far since every change has
been additive, but a future non-additive statement would already have run
against a newer-than-understood schema by the time the check fired).

**Retention (implemented in WP 2.4):** `agent_reports` would otherwise grow one
row per client per report interval forever. `POST /v1/agent/installed` prunes
to the newest `VAULT_AGENT_REPORT_KEEP` snapshots per `client_id` **inside the
same transaction that inserts the new one** — see "Agent reports" below.

No foreign keys enforced between `depot_app_map`/`jobs`/`agent_reports`/
`depot_manifests` and `apps.appid` — depot mappings, agent reports and
manifest rows may arrive before an app row exists (e.g. first prefill of a
new title). Revisit if this becomes a data-integrity problem in practice.

**Connection pragmas** (`db.py::get_connection`): `journal_mode=WAL` and
`busy_timeout=5000` (ms) on every connection, so HTTP request handlers
reading the database don't immediately fail with "database is locked" while
the background job worker holds a write transaction.

### Connection handling — `deps.db_opener` (WP 1.4, replaces WP 1.3's `get_db`)

WP 1.3 injected a `sqlite3.Connection` via a sync **generator** dependency
(open → `yield` → close in a `finally`), with `check_same_thread=False` to
tolerate the connection crossing anyio worker threads. **That combination
segfaulted the interpreter under concurrent load** — reproduced repeatedly on
Windows/CPython 3.12 as `Windows fatal exception: access violation`, ~1 in 1–3
runs of the hammer test, once `POST /v1/prefill` (a *write* endpoint that takes
SQLite's write lock) joined the request mix.

Root cause, measured — not guessed. Wrapping `sqlite3.Connection` in a subclass
that took a per-connection lock around every `execute`/`commit`/`close` and
recorded contention produced this, five times per run:

```
close  owner (thread 11072, 'execute:BEGIN IMMEDIATE')  intruder thread 36880
  fastapi/routing.py:290  async with AsyncExitStack() as async_exit_stack:
  contextlib.py:737       cb_suppress = await cb(*exc_details)
  vault_api/deps.py:31    conn.close()
```

One thread is blocked *inside* `execute()` on the connection (waiting on the
write lock, GIL released) while FastAPI's `AsyncExitStack` unwinds the
dependency on another thread and calls `close()` on that same connection.
`sqlite3_close` concurrent with `sqlite3_step` is a use-after-free at the C
level: sqlite's serialized threading mode protects the *database*, not
CPython's per-connection objects. With the lock in place the crash disappeared
and only the (separate) mapping race surfaced — that is the confirmation.

The fix is structural rather than another flag: **a connection never leaves the
thread that created it and never outlives the endpoint body.**
`deps.db_opener` is a plain (non-generator) dependency that returns a
zero-argument *opener*; endpoints do

```python
def list_games(open_db: DbOpener = Depends(db_opener)) -> list[GameSummary]:
    with open_db() as conn:
        ...
```

so open, use and close all happen inside the single `run_in_threadpool` call
that runs the body. Nothing else holds the connection, so nothing else can
close it. `check_same_thread` is consequently back at its **safe default
(`True`)** — a future accidental hand-off is now a loud `ProgrammingError`
instead of a native crash, pinned by
`tests/test_db.py::test_connections_are_thread_confined_by_default`.

The job worker follows the same rule from the other side: it owns exactly one
connection, created inside its own thread, closed by that thread on exit.

Regression tests: `tests/test_concurrency.py` — the existing mixed-request
hammer (now 50 parallel requests incl. `POST /v1/prefill` and `GET /v1/jobs`)
plus `test_http_reads_survive_a_worker_writing_in_the_background`, which
hammers reads while the real worker thread writes jobs/apps/mapping rows.

**Also fixed (same class, found by the same hammer): `upsert_mapping`'s app-row
creation was `SELECT`-then-`INSERT`.** Two writers upserting the *same* new
appid both saw "no row" and both inserted; the loser got
`IntegrityError: UNIQUE constraint failed: apps.appid` and the request 500'd. It
is now `INSERT OR IGNORE` plus a conditional name `UPDATE`, pinned by
`tests/test_mapping.py::test_concurrent_upsert_of_the_same_new_appid_does_not_500`.

## Endpoints (WP 1.3 + 1.4 + 1.5 + 1.6 + 2.4 + 3.5 + 3.8 + 3.9 + 3.11 + 3.12 + 4a.6r + settings-API/ADR-0009 + 4h.1 + AG-1)

All routes below require `X-Api-Key` (see "Auth"). Full API table:
`docs/PROJECT_PLAN.md` §6; every row there is implemented, and every
authenticated one is listed below (`GET /v1/health` is the one public
route, see "Auth").

| Method | Endpoint                          | Purpose |
|--------|-------------------------------------|---------|
| GET    | `/v1/games`                        | All tracked apps: `appid`, `name`, `status`, `last_prefill_at`, `last_manifest_check` (schema v4, WP 3.3; surfaced here WP 4c — the last run that CONFIRMED this app current, `null` until that exact outcome happens; **much narrower than "last time a job ran"**, see "Job outcome honesty" below; **and unlike `last_prefill_at`, survives `DELETE /v1/cache/{appid}`** — deletion nulls `last_prefill_at` but deliberately leaves this field, so a game with zero cached bytes can still show a past confirmation timestamp, see "Per-game deletion" below), `depot_count`, `size_bytes` (sum of the app's mapped depots' bytes on disk; `null` if unmapped or not yet cached — see "Per-game size calculation" below), `needs_force` (schema v5, WP 3.4 — whether the NEXT prefill will run with `--force`, see "needs_force" below), `manifest_change_frequency` (schema v14, WP 4h.1 — `null`/`"insufficient_data"`/`"stable"`/`"changed"`; **NOT a rate**, see "Change frequency" below), `manifest_observation_days` (days since the youngest-observed depot's first observation; `null` only alongside a `null` category), `manifest_days_since_last_change` (days since the most recently observed change; populated ONLY when the category is `"changed"`), `installed_on` (WP AG-1 — list of `{client_id, reported_at}`, ALREADY filtered to fresh agent reports only; `[]` for an unmapped/never-installed app and for a vault with zero agents — see "Installed state per app" below) |
| GET    | `/v1/games/{appid}`                | Detail for one app: same fields (incl. `installed_on`) plus `depots` (list of `{depotid, shared, size_bytes}`); `404` for an unknown `appid` |
| PUT    | `/v1/mapping/{depotid}`            | Body `{"appid": int, "app_name": str \| null}` — **additively** upsert one depot→app mapping fact (manual fallback, see below); `422` for `depotid <= 0`, `appid <= 0`, or an unrecognized body field |
| GET    | `/v1/mapping`                      | Full depot→app mapping table: list of `{depotid, appid}` |
| DELETE | `/v1/mapping/{depotid}/{appid}`    | Remove one mapping pair (correction path for the additive `PUT`, see below); `204` on success, `404` if the pair doesn't exist, `422` for non-positive ids |
| POST   | `/v1/prefill`                      | Body `{"appids": [int, ...]}` — queue one prefill job per app id. `202` with a list of `{appid, job_id, status, deduplicated}`. `422` for an empty list, an appid `< 1`, a non-list, or an unrecognized body field |
| POST   | `/v1/prefill/cached`               | Phase 4c, WP 4c-api. **No request body.** Selects every app that currently has cache content and queues a prefill for each through the exact same path `POST /v1/prefill` uses (same dedupe, same response shape). `202` with a list of `{appid, job_id, status, deduplicated}`, one entry per selected app, `[]` if nothing is cached. See "Check & update all cached games" below |
| GET    | `/v1/jobs`                         | Recent jobs, newest first. `?limit=` 1–200, default 20 (`422` outside that range). Omits `log_excerpt` on purpose — this is the polling list. Includes `updated`, `up_to_date`, `summary_parse_ok` (schema v4, WP 3.3 — see "Job outcome honesty" below; `null` until the job finishes or if the summary couldn't be parsed), `gc_execute` (schema v7, WP 3.8 — `null` for a prefill job, `false`/`true` for a GC job's mode), plus `paused_at` and `stop_request` (schema v8, WP 3.12 — see "Job control" below) |
| GET    | `/v1/jobs/{id}`                    | One job incl. `log_excerpt` plus the same `updated`/`up_to_date`/`summary_parse_ok`/`gc_execute` fields; `404` for an unknown id |
| DELETE | `/v1/jobs/{id}`                    | **Cancel** a job (WP 3.12). `200` with `{job_id, status, outcome, detail}` — `outcome` is `"immediate"` (a queued/paused job, finalized here) or `"requested"` (a running job; the worker stops it, keep polling). `404` unknown id; `409` if the job already finished. See "Job control" below |
| POST   | `/v1/jobs/{id}/pause`              | **Pause** a running **prefill** job. Same response shape; `outcome` is always `"requested"`. `404` unknown id; `409` for a GC job or any job that is not `running` |
| POST   | `/v1/jobs/{id}/resume`             | **Resume** a paused job — back to `queued`, keeping its original job id, so it runs *before* anything enqueued while it was paused. `outcome: "resumed"`. `404` unknown id; `409` if the job is not `paused` |
| DELETE | `/v1/cache/{appid}`                | Delete this game's depot directories. `200` with `{appid, deleted_depots[], skipped_shared[], failed[], total_bytes_freed}`; `404` unknown appid or no mappings; `409` while a prefill **or GC** job for the app is queued/running/**paused** (WP 3.12); `422` for `appid < 1`; `500` if the cache-root guards refuse. See "Per-game deletion" below |
| POST   | `/v1/cache/{appid}/gc`             | Queue a garbage-collection job. Body optional; `{"execute": true}` (a literal JSON boolean) is the only way to delete — **dry run by default**. `202` with `{appid, job_id, status, type, mode, execute, deduplicated}`; `404` unknown appid or no mappings; `422` for `appid < 1`, a non-boolean `execute`, or an unrecognized body field. See "Garbage collection" below |
| GET    | `/v1/cache/summary`                | `total_bytes` (disk usage of `depot/`, each depot counted once), `top_consumers` (top 10 `{appid, name, size_bytes}`, largest first), `unmapped_depots` (`{count, size_bytes}` for depot dirs on disk with no mapping row for any app), `free_disk_bytes` (free space on the cache filesystem, `null` if undeterminable) |
| POST   | `/v1/agent/installed`              | Body `{"client_id": str, "appids": [int, ...]}` — store one **full-list** snapshot of a client's installed games. `200` with `{client_id, received, added, removed, first_report}`; `422` for a bad `client_id` (empty, > 64 chars, control characters, surrounding whitespace, `.`/`..`), an appid `< 1` or a boolean, a missing/non-list `appids`, more than 10 000 ids, or an unrecognized body field. See "Agent reports" below |
| GET    | `/v1/clients`                      | One row per reporting client, sorted by `client_id`: `{client_id, first_seen, last_reported_at, app_count}` (WP 2.4) plus `{source_addrs, cache_hits, cache_misses, bytes_served, last_seen_in_cache_log, bypass_suspected}` (WP 3.11, ADR-0008 — the hit statistics and bypass warnings plan §5/§6 promised). The cache fields are `0`/`null`/`false` when the event feed is off. See "Cache-event sweep → Bypass detection" |
| DELETE | `/v1/clients/{client_id}`          | Remove one client's rows (WP AG-1 — the rename-cleanup path AG-0 named). `204` on success; `404` if the `client_id` has no rows at all. **Not a ban** — a client that reports again under the same id simply reappears with a fresh diff chain. See "Deleting a client" below |
| GET    | `/v1/oracle/{appid}`               | Stored manifest-oracle view (WP 3.9): `{appid, enabled, checked_at, source, buildid, verdict, depots[]}`, each depot `{depotid, recorded_manifestid, oracle_manifestid, verdict, beta_branches[]}`. `verdict` is `current`/`stale`/`not_cached`/`unknown`. **No network, no `404`** — an app nobody asked about answers `checked_at: null`, and `enabled: false` when the oracle is off. `422` for `appid < 1` |
| POST   | `/v1/oracle/{appid}/refresh`       | Ask the oracle now (WP 3.9). **This request leaves the LAN** when the oracle is enabled. Always `200`: `{appid, enabled, ok, error, checked_at, depot_count, branch_manifest_count, open_branches[], skipped_password_branches, warnings[]}` — an unreachable or garbage answer is `ok: false` with a reason, never a 5xx. `422` for `appid < 1` |
| DELETE | `/v1/oracle/{appid}`               | Forget everything the oracle said about one app (WP 3.9). `204` whether or not anything was stored — idempotent, and the way to withdraw the extra GC keep-set protection oracle rows grant |
| GET    | `/v1/schedule`                     | Scheduler config + last-sweep bookkeeping: `{enabled, window, overnight, interval_minutes, client_stale_days, server_timezone, last_sweep_at, last_sweep_targets, last_sweep_enqueued, next_eligible_at}`. **Read-only itself** — the window/interval/staleness values are now writable through `PATCH /v1/settings` (settings-API work package, ADR-0009; see "Persisted settings" below), this endpoint just reports the currently EFFECTIVE configuration, override included |
| GET    | `/v1/stats`                        | Cache-event sweep config + bookkeeping (WP 3.11): `{event_feed_enabled, sweep_interval_minutes, miss_trigger_enabled, miss_trigger_cooldown_minutes, miss_trigger_max_per_sweep, bypass_window_days, cursor_offset, first_sweep_at, last_sweep_at, last_rotated_at, lines_read_total, lines_skipped_total, last_lines, last_skipped, last_enqueued, last_dropped_by_cap, truncate_denied_count, last_truncate_denied_at, oversized_skips_total, last_oversized_at, top_unmapped_depots[]}`. **Read-only.** Three counters are alerts: `lines_skipped_total` climbing means a format disagreement, `truncate_denied_count` climbing means the log is growing unbounded, and any non-zero `oversized_skips_total` means bytes were discarded because something wrote a newline-free region longer than a read batch. See "Cache-event sweep" below |
| GET    | `/v1/steam/key`                    | Opt-in Steam Web API relay (WP 4a.6r): key status only — `{configured, key_last4}`. `key_last4` is `null` when unconfigured; the full key is never returned |
| PUT    | `/v1/steam/key`                    | Body `{"key": str}` — set (or replace) the relay's Web API key. `200` with the same shape `GET` returns; `422` unless `key` is exactly 32 hexadecimal characters |
| DELETE | `/v1/steam/key`                    | Clear the configured key. `204` whether or not one was set |
| GET    | `/v1/steam/owned-games`            | `?steamid=<SteamID64>` — relay `GetOwnedGames`. `200` with `{configured: true, game_count, games: [{appid, name, playtime_forever, img_icon_url, rtime_last_played}]}` (`rtime_last_played` added WP 4h.1: Unix-epoch seconds or `null` — see "rtime_last_played — absence is not zero" below); `409` if no key is configured; `422` for an unusable `steamid`; `502` for any upstream failure. **`playtime_forever`/`rtime_last_played` are each independently OMITTED from the JSON body entirely** (not sent as `0`/`null`) unless `VAULT_RELAY_EXPOSE_PLAYTIME`/`VAULT_RELAY_EXPOSE_LAST_PLAYED` are set — WP 4h.0, ADR-0010, see "The privacy gate" below. See "Steam Web API relay" below |
| GET    | `/v1/steam/player-summaries`       | `?steamid=<SteamID64>` — relay `GetPlayerSummaries`. `200` with `{configured: true, players: [{steamid, personaname, avatar, avatarmedium, avatarfull, personastate}]}`; same `409`/`422`/`502` shape as `owned-games`. See "Steam Web API relay" below |
| GET    | `/v1/settings`                     | Settings-API work package (ADR-0009): `{readonly, settings: [{key, effective, source, fallback, applies, env_only}, ...]}` — every overridable key plus informational env-only ones. `source` is `db`/`env`/`default`; `applies` is `immediately`/`next_sweep`/`restart-required`. See "Persisted settings" below |
| PATCH  | `/v1/settings`                     | Partial update: `null` clears an override (revert to env/default), any other value sets it, validated with the SAME grammar `config.py` applies at startup. `200` with the same shape `GET` returns. `422` for an invalid value, an unknown key, or a recognised-but-environment-only key (distinct detail); `403` if `VAULT_SETTINGS_READONLY` is set. See "Persisted settings" below |

## Prefill orchestration (WP 1.4, job outcome honesty WP 3.3)

### Queue semantics

- `POST /v1/prefill` only *enqueues* and returns `202` immediately — the app
  polls `GET /v1/jobs/{id}` for the outcome.
- **Exactly one job runs at a time** (plan §3), FIFO by job id.
- **Dedupe:** if an app already has an in-flight job (`queued`, `running` or —
  since WP 3.12 — `paused`), that job is
  returned with `"deduplicated": true` instead of a second one being stacked.
  Duplicate app ids *within one body* fall out of the same rule — the response
  keeps one entry per requested id, in request order, so the repeat points at
  the same `job_id`. Rationale: the app fires this on a button press and Phase
  3's miss trigger (ADR-0001) will fire it on cache misses, so repeats are the
  normal case. A *finished* job never blocks a new one.
- Enqueueing an app that has never been seen creates its `apps` row (status
  `idle`) so `GET /v1/games/{appid}` answers 200 while the job is still queued.
- Statuses: jobs are `queued` → `running` → `done` | `error` | `cancelled`,
  with a non-terminal `paused` detour (WP 3.12 — see "Job control" below);
  `apps.status` follows `idle` → `running` → `done` | `error` (plan §3) and
  gains **no** new values. `last_prefill_at` is
  written **only** on success. **A successful (exit `0`) SteamPrefill run is
  not automatically `'done'`** — see "Job outcome honesty" below for the
  summary-table-driven rule (WP 3.3, ADR-0006 decision 1) that can still end
  such a run as `'error'`.
- `log_excerpt` is the ANSI-stripped **tail** of SteamPrefill's combined
  stdout/stderr, capped at 4 KiB and prefixed with `[...truncated...]` when it
  was cut, plus vault-api's own `[vault-api] …` diagnostic lines.

### Check & update all cached games (`POST /v1/prefill/cached`, Phase 4c, WP 4c-api)

`docs/PROJECT_PLAN.md` §7 Phase 4c asked a decision question: does a
convenience route that selects "every cached app" server-side earn its keep,
or does the frontend simply post the ids it already has? **Decision taken:
ship the server-side route.** Reasons accepted, costs accepted along with
them:

- **Robust for very large libraries.** The frontend would otherwise have to
  enumerate every cached app itself (paging `GET /v1/games`, filtering by
  `size_bytes is not null`) before doing exactly what this route does in one
  round trip — duplicated client-side logic in both frontends (WP 4a/4b) for
  no benefit.
- **Directly callable by external automation** (n8n, cron, a shell script
  over the tailnet) without needing to already know the app id list — "check
  my whole vault now" becomes one authenticated `curl` call.
- **The cost, stated up front:** this is new, write-capable API surface (a
  single call can trigger real Steam downloads for every cached app). Phase
  6's scoped API keys — not designed yet — must cover this route explicitly
  when that phase lands, the same way they will need to cover every other
  mutating route that exists by then. Recorded here so it is a known,
  pre-accepted obligation rather than a surprise at Phase 6 time.

**No new enqueue mechanism.** The route selects app ids and hands every one
of them to the exact same `jobs.enqueue_prefill` that `POST /v1/prefill`
calls — same dedupe against `queued`/`running`/`paused` jobs, same
`PrefillJobRef` response shape (`{appid, job_id, status, deduplicated}`).
There is deliberately no second queue-writing code path; this endpoint is a
*selection* convenience layered on top of the one that already existed.

**Selection: disk-and-mapping truth, one query, and (WP 4f) the SAME
definition the sweep uses.** "Currently has cache content" is decided by
`deletion.appids_with_cache_content` — the one shared predicate
`scheduler.cached_appids` (see "Sweep target set — installed PLUS cached"
below) also calls, so this route and the background sweep can no longer
answer "is this app cached" differently. It counts a depot toward an app
when the depot is either **exclusive** (mapped to no other tracked app) or a
**last cached remnant** (ADR-0003 addendum: every OTHER co-owner is
verifiably uncached right now) — NOT merely "mapped to any depot with
bytes", which was this route's rule before WP 4f and is what let it re-queue
a game the sweep correctly treated as deleted (see the cross-referenced
section for the measured before/after). Disk truth comes from the same
`SizeCache`-backed snapshot `GET /v1/cache/summary` and `GET /v1/games`
already share (so a request right after either of those costs nothing extra
within the TTL); mapping/sharing truth comes from a single bulk read of the
entire `depot_app_map` table, joined with every owner's lifecycle state in
one statement — never a per-app or per-depot query
(`vault_api/routers/jobs.py::_select_appids_with_cache_content` delegates to
`deletion.appids_with_cache_content`, statement-count-pinned at 500 apps in
`tests/test_prefill_cached.py`; see "Sweep target set" for the measured
statement-count and timing numbers at 300 apps, which apply identically
here since it is the same function). Consequences, each deliberate:

- A depot with bytes on disk but **no mapping row** (an unmapped depot, same
  concept `GET /v1/cache/summary`'s `unmapped_depots` reports) contributes to
  no app — there is no id to enqueue a job for.
- An app left at `status='error'` by a **partially-failed**
  `DELETE /v1/cache/{appid}` (WP 1.6: `shutil.rmtree` can leave a depot half
  removed) is still selected as long as any of its depots still have bytes on
  disk. This is the honest answer, not a gap: "check & update" is exactly the
  repair such an app needs, and its leftover `needs_force = 1` (set by the
  failed deletion) makes the resulting run forced automatically — see
  "needs_force" below.
- **No cached apps ⇒ `[]` with a normal `202`**, never an error.
- **No coalescing with a concurrent sweep's raw filesystem walk** — see
  "Sweep target set"'s cost-model note (point 2): this route's `SizeCache`
  read and the sweep's own uncached `sizes.scan_depot_dir_bytes` walk can
  duplicate the same `depot/` tree walk in the same window, most often right
  after a successful prefill invalidates `SizeCache` (`worker.py`).

**"Non-forced" describes what happens by construction, not a flag this route
sets.** `jobs.enqueue_prefill` has no per-job force parameter — whether a
queued job's run passes `--force` is decided entirely by the per-app
`apps.needs_force` flag at claim time (`worker.py`, see "needs_force"
below), exactly like every other prefill job in the system. That flag is set
back to `1` by **two routine (non-error) events**, not one: a deletion
touching a depot the app maps, **and an executing `POST
/v1/cache/{appid}/gc` run that actually reclaimed chunks from a depot the
app maps** (`gc_execute.flag_needs_force_for_depots`, ADR-0007 — see
"needs_force" and "Garbage collection" below). GC execute is routine
maintenance, not a failure path, so "the ordinary case is non-forced" is
only true for an app that has not recently been through either event — it
is false for the whole window right after a GC execute touches a depot it
shares. An app this route selects that carries `needs_force = 1` for either
reason still gets queued and correctly runs forced — the route defers to
the existing flag in every case rather than asserting a blanket "always
non-forced" guarantee it has no mechanism to enforce.

**What a forced run actually costs, quantified, since "forced" sounds like
"re-download everything":** per the module docstring in `vault_api/prefill.py`,
`--force` re-*requests* every chunk, but a chunk still present on disk is
served by vault-core as a local HIT — measured ~120x faster than a MISS
(ADR-0001). The real-world consequence of a forced run in this route's
selection is therefore **duration, not bandwidth**: minutes of disk-speed
re-touching instead of the ~3 s non-forced no-op, serialized behind whatever
else is ahead of it on the single worker — genuine internet traffic only for
chunks that are actually gone (the deletion or GC-reclaim case, where those
bytes are, correctly, no longer on disk to replay).

**Bypasses the WP 3.11 miss-trigger cooldown — structurally, on purpose.**
That cooldown (`event_sweep.in_cooldown`, backed by `miss_trigger_state`) is
consulted by exactly one caller, `event_sweep.run_miss_trigger`, and exists
to stop an unattended, flapping cache-MISS signal from re-triggering the same
app repeatedly. A human pressing "check & update now" is a deliberate one-off
ask, not a flapping signal, and must not be silently absorbed by another
feature's cooldown. This route never imports `event_sweep` and never touches
`miss_trigger_state`, so the bypass falls out of the code structure rather
than a conditional that could later be "fixed" into a silent no-op —
`tests/test_prefill_cached.py::test_cached_prefill_bypasses_the_miss_trigger_cooldown`
seeds an app still inside the cooldown window and asserts a fresh job is
queued anyway; adding a cooldown check to this route fails that test by name
(verified by temporarily adding one during review of this package). Still
bounded by the two guards that were never about the cooldown: **worker
slots** (exactly one worker, plan §3 — queuing 500 jobs here still runs them
strictly one at a time) and **job dedupe** (the same `enqueue_prefill` rule
every other prefill request follows).

**Immediate relative to the prefill work — not necessarily fast on the wall
clock, and that caveat is not new here.** The request never waits for a
single prefill job to run; it only enqueues and returns `202` with the job
ids, the same way `POST /v1/prefill` does, and a 50-game library (roughly
2.5 minutes of serial Steam logins on the single worker) shows its progress
in `GET /v1/jobs`, never behind a spinner on this endpoint. But the
*selection* step's `size_cache.get(cache_root)` (see above) can itself pay
for a full `depot/` walk while holding `SizeCache`'s lock — a pre-existing
cost this route shares with `GET /v1/cache/summary` and `GET /v1/games`, not
something new. `VAULT_SIZE_CACHE_TTL` defaults to 60 s, and `worker.py`
invalidates the cache after **every** successful prefill job — so a second
call to this route while the queue it just filled is still draining can pay
a fresh cold walk again. Warm, on SSD, this is sub-second even at hundreds
of thousands of files (measured 1.76 µs/file); cold and seek-bound on a
spinning-disk target it is plausibly tens of seconds. A client calling this
endpoint should use the same timeout it already uses for
`GET /v1/cache/summary`.

**On top of that filesystem walk, the classification step
(`deletion.appids_with_cache_content`, WP 4f) adds its own, separately
measured cost — and unlike the walk, it is genuinely quadratic in
owners-per-depot, not "negligible" (S4, reviewer correction, 2026-08-18
review round).** Still exactly ONE SQL statement at every size, but the
in-memory reconstruction measured **up to ~3.6 s** on a library with a large
shared depot (see "Sweep target set"'s cost-model table above for the full
numbers) — and this route's documented "`202` immediately" contract does NOT
cover this cost, because selection runs synchronously before that response is
sent. A big redistributable depot mapped to every tracked game is the
realistic shape that triggers this, not a contrived one. Not (yet) bounded
or made asynchronous; recorded here as a known cost.

**Any request body is silently accepted and ignored.** The route declares no
body parameter, and FastAPI does not reject an unexpected one by default —
an empty body, `{}`, `{"appids": [...]}`, even invalid JSON or a form
content-type all still reach the handler and still return `202`. This is
the opposite of `POST /v1/prefill`'s `extra="forbid"` `PrefillRequest`, which
`422`s a typo'd field. A frontend that posts `{"appids": [...]}` to this
route by mistake (expecting it to behave like `POST /v1/prefill`) gets a
cheerful `202` that queues **every cached app**, not the ids it sent — worth
getting right in client code, since the server gives no signal that the body
was pointless.

**A mid-loop `5xx` leaves a partial, unreported result — same as
`POST /v1/prefill`.** Both routes loop over app ids inside one open
connection and enqueue one at a time; if SQLite's `busy_timeout` is exceeded
partway through (contention with the worker's own writes) the request can
fail after some apps are already durably `queued` but before a response
body is ever sent. A `5xx` from either route means "unknown outcome for this
call" — the client's correct recovery is `GET /v1/jobs`, not a blind retry
that would re-enqueue everything (dedupe makes a retry safe, but reading the
actual state first is the honest first step).

### Worker lifecycle

One background thread, started by the FastAPI lifespan
(`vault_api/worker.py`) — "one job queue, not Celery".

1. **Startup, before the worker starts:** `jobs.recover_stale_jobs` fails every
   job still marked `running`. The rule: vault-api runs exactly one worker in
   exactly one process, and at startup that worker has claimed nothing, so a
   `running` row can only be an orphan from a process that died mid-job (crash,
   container kill, reboot). Leaving it would also wedge the dedupe rule —
   `POST /v1/prefill` would keep handing out a job id nothing is executing. The
   app's `status` is repaired the same way (otherwise a permanent yellow badge).
   **Honest caveat: do not point two vault-api processes at the same database.**
   The second one's startup would fail the first one's genuinely running job.
   Single-process operation is the documented deployment model (one container),
   not something the code enforces.
2. **Loop:** claim (atomic conditional `UPDATE` inside `BEGIN IMMEDIATE`) →
   run → record; sleep `VAULT_WORKER_POLL_SECONDS` when the queue is empty.
   A crash inside a job is caught, recorded on that job, and the thread lives on
   — a bug must not silently stop the queue draining.
3. **Shutdown:** the stop event makes the loop exit before claiming another job,
   *and* a prefill subprocess in flight is terminated (then killed after a
   grace period) — otherwise `docker stop` would block for as long as the
   download takes. The aborted job is recorded as `error` with a clear reason.
   A hard `SIGKILL` still leaves a `running` row, which step 1 cleans up.
4. **On success only:** the shared `SizeCache` (WP 1.5, see "Per-game size
   calculation" below) is invalidated so `GET /v1/games` reflects the new
   disk content immediately instead of waiting out `VAULT_SIZE_CACHE_TTL`.
5. **`VAULT_PREFILL_MODE=queue` only (WP S-1, ADR-0012):** step 2's "run" is a
   hand-off through the `jobs` table to a separate `prefill_runner` process
   instead of an in-process `prefill.run_prefill` call — see "Queue mode"
   below. Every other step above, including step 1's stale-job recovery
   (narrowed for a job already handed off — the runner is a SEPARATE
   process and a vault-api restart says nothing about whether it died too),
   is unchanged in shape.

`PrefillWorker` previously carried a `_finished` event that was set on exit
but never read anywhere (WP 1.4 review carry-over) — removed in WP 1.5 rather
than kept as unused state; `stop()`'s `thread.join()` already provides the
deterministic wait tests need.

### SteamPrefill invocation — verified, not assumed

Checked empirically against `poc/steamprefill/bin/SteamPrefill.exe` (v3.7.1)
while writing this package:

- `prefill --help` offers `--all`, `--recent`, `--recently-purchased`, `--top`,
  `-f|--force`, `--os`, `--verbose`, `--unit`, `--no-ansi`. **There is no
  `--app-ids`-style option and no positional app id** — `prefill zzz` is
  rejected with `Unexpected parameter(s): <zzz>`, and the binary contains no
  `app-ids` string at all.
- The selection `prefill` consumes is a **state file**:
  `<exe dir>/Config/selectedAppsToPrefill.json`, a plain JSON array of app ids.
  Observed content `[3419430]`; `select-apps status` read it back and listed
  exactly that one app, confirming the file is the selection store (the
  interactive `select-apps` TUI is just one writer of it).

So the invocation vault-api uses is:

```
write <exe dir>/Config/selectedAppsToPrefill.json  =  [<appid>]
run   <exe> prefill [--force] --no-ansi --max-threads <N>   (cwd = exe dir, stdin = DEVNULL)
```

- **`--force` is deliberate, but no longer unconditional (WP 3.4, ADR-0006
  decision 2 — see "needs_force" below).** Without it SteamPrefill skips apps
  its own `Config/successfullyDownloadedDepots.json` thinks are up to date —
  state that knows nothing about vault-api deleting an app from the cache
  (`DELETE /v1/cache/{appid}`, WP 1.6), so a non-forced run would silently
  refuse to refill a game we just deleted. Chunks still on disk are re-requested
  and served by vault-core as local HITs, so the cost is disk speed, not
  internet bandwidth (Phase 0: HIT ~120× faster than MISS, ADR-0001) — which is
  why it stays the right tool for first fills and post-deletion refills
  specifically, gated by the per-app `needs_force` flag, rather than something
  to run on every job regardless of whether it's needed.
- **`--no-ansi` is not sufficient** — Spectre.Console's exception renderer still
  emits SGR escapes (observed), so captured output is ANSI-stripped in code.
- **vault-api OWNS `Config/selectedAppsToPrefill.json`** and overwrites it on
  every job. A manual `select-apps` selection on the same SteamPrefill
  installation will be replaced. That is the trade-off of the only
  non-interactive selection mechanism v3.7.1 offers.
- **The write is atomic (WP 1.5 carry-over fix from the WP 1.4 review):**
  `write_selected_apps` writes to a tempfile in the same `Config/` directory,
  then `os.replace`s it over the real path — a same-directory tempfile keeps
  the replace a same-filesystem rename (atomic on both POSIX and Windows), so
  nothing that reads the file (SteamPrefill itself, mid-write) can ever
  observe a half-written selection. The previous `open(path, "w")` truncated
  the file in place first, with no such guarantee.
- OS selection is left at SteamPrefill's default (Windows). Prefilling Linux
  depots for Steam Deck clients (ADR-0002) would need `--os linux` and is not
  in this package's scope.

### SteamPrefill concurrency (`--max-threads`, WP CORE-FIX-2)

Every prefill, in subprocess mode (vault-api's worker) and in queue mode
(vault-runner), passes `--max-threads <N>` with N from
`VAULT_PREFILL_MAX_THREADS` (default **8**, whole number 1-64; anything else,
including `0`, `65`, `+8` or `8 `, refuses to boot either process; blank means
the default).

- **Why.** SteamPrefill 3.7.1 keeps up to 30 chunk requests in flight
  (`Models/DownloadArguments.cs`) and re-requests failed chunks at once.
  vault-core opens a new upstream TCP connection per miss, so on a line behind
  a carrier-grade NAT (DS-Lite and similar) a prefill can use up the NAT's port
  mappings. The NAT then answers new connections with ICMP host-unreachable,
  vault-core logs `connect() failed (113: Host is unreachable) while
  connecting to upstream`, and SteamPrefill's instant retries turn that into a
  storm (production, 2026-10-02: 57845 such 502s against 4029 200s in about
  two minutes). 8 is well below the 50 parallel connections still measured to
  work on that line, and cuts the concurrent upstream connections and the
  retry storm to about a quarter. That was not measured on the real line, and
  it is a mitigation, not the root fix: while a download is bandwidth-bound,
  the rate of NEW connections is roughly throughput divided by chunk size,
  whatever the thread count, and a CGN that keeps mappings after close
  (RFC 5382 REQ-5: at least 4 minutes) can still run out at 8 threads.
  `VAULT_UPSTREAM_RATE` (vault-core's bandwidth cap, ADR-0015) lowers that
  rate too. The root fix is an upstream keepalive pool in vault-core, planned
  after v0.1.0. Eight ~1 MB chunk streams still fill a typical home line; set
  30 to get SteamPrefill's own behaviour back on a line with its own public
  IPv4.
- **The flag is hidden.** It is not in `--help`: `Program.cs`
  (`ParseHiddenFlags`) strips it from the argv before the CLI parser runs and
  logs `Using --max-threads flag.  Will download using at most N threads`,
  which therefore shows up at the top of every job's output. If a future
  SteamPrefill dropped it, the CLI parser would reject it as an unknown option
  and every job would fail. `api/Dockerfile` runs
  `SteamPrefill prefill --max-threads 3 --help` at build time and fails the
  build unless that log line appears (exit codes cannot prove it: `--help`
  ignores unknown options and exits 0), and
  `tests/test_core_fix_2_max_threads.py` only accepts SteamPrefill versions
  listed as verified, so a version bump forces a re-check.
- **Env-only, not a `PATCH /v1/settings` key.** In the shipped queue mode the
  consumer is vault-runner, which reads its environment once at start and
  never the settings table. A database override would be read by vault-api
  but not by the process that runs SteamPrefill (the boot-snapshot versus
  effective-settings split `docs/LEARNINGS.md` warns about). The variable is
  forwarded to both services by `deploy/compose.yaml`; change it in `.env`
  and recreate the containers.
- It bounds concurrency, not bandwidth. The aggregate bandwidth cap is
  vault-core's `VAULT_UPSTREAM_RATE` (ADR-0015).

### Login prerequisite (and why a job can't hang on it)

SteamPrefill needs a Steam session and prompts interactively when it has none.
vault-api runs it with **stdin closed** (`DEVNULL`), which was verified against
a fresh copy of the real binary with an empty `Config/`: it does *not* hang — it
prints `A Steam account is required in order to prefill apps!` /
`Please enter your Steam account name :` and dies with
`InvalidOperationException: Failed to read input in non-interactive mode.`,
exit code 1, in about a second. Login happens *before* cache detection, so this
is the first thing that fails on an unconfigured install.

vault-api detects that output and fails the job with an actionable message
("run `SteamPrefill select-apps` in a terminal once, enter your
account/password/Steam Guard code, then retry"). **Log in once by hand on the
server before the first job.** `VAULT_PREFILL_TIMEOUT_SECONDS` is still enforced
as a backstop — the fast-fail above is proven, a wedged network connection or a
future Steam Guard prompt variant is not.

**vault-api never sees, stores, transmits or logs Steam credentials.** The
session lives in SteamPrefill's own `Config/` directory next to the executable.

### Queue mode: the `prefill_runner` process (WP S-1, ADR-0012)

Everything above this section describes `VAULT_PREFILL_MODE=subprocess` (the
default): vault-api's worker calls `prefill.run_prefill` directly, in its own
process — unchanged by this work package, byte for byte. Set
`VAULT_PREFILL_MODE=queue` to hand execution off to a separate process
instead:

```
python -m vault_api.prefill_runner
```

**Why this exists.** SteamPrefill needs broad Steam CM/CDN network access;
vault-api's control-plane role does not. As long as vault-api's own process
spawns the SteamPrefill subprocess directly, vault-api's *container* can
never be locked down to LAN-only egress without also breaking prefill. Queue
mode moves the one thing that needs the wider internet into its own process,
so a separate container running `prefill_runner` can be the only one with
that access — see `docs/adr/0012-*.md` for the full design and the two
rejected alternatives (an HTTP sidecar; a Docker-socket mount). **WP S-1
shipped only the api-side code; WP S-2 (landed) wires `prefill_runner` into
`deploy/compose.yaml` as its own `vault-runner` service** — see
`deploy/README.md`'s service table and "First run" section. The egress lock
itself is EG-1, still to come. `deploy/compose.yaml` now SHIPS `queue` mode
by default (`vault-runner`'s own `environment:` block, plus the matching
`Config/`/`vault-steamprefill-home` volumes it mounts); `queue` mode is also
still usable for native/manual testing outside Compose (run both processes
yourself, pointed at the same `VAULT_DB_PATH` and `VAULT_CACHE_ROOT`) for
anyone not using the shipped compose file at all.

**vault-api's worker keeps owning everything except the subprocess call.**
Claiming a job, deciding `--force`, applying the depot mapping, manifest
ingestion, webhooks, auto-GC and every status transition all still happen in
vault-api — `prefill_runner` imports nothing from `worker.py` and touches
none of that. It only: polls the `jobs` table for a job vault-api has handed
off, calls the exact same `prefill.run_prefill` function vault-api used to
call directly, and reports a result back. See `vault_api/prefill_queue.py`
(the encode/decode + wait-loop glue shared by both sides) and
`vault_api/prefill_runner.py` (the CLI entrypoint) for the mechanics, and
`docs/adr/0012-*.md` for the full crash-semantics reasoning (a dead runner's
job fails cleanly; there is no lease-stealing reclaim, so a job vault-api has
already declared dead can never be silently resurrected by a second runner
instance).

**The one-time interactive login moves containers.** ADR-0004 decision 1's
"log in once, interactively, in SteamPrefill's own prompt" still applies
unchanged — only WHERE you run it changes. In `subprocess` mode, that is
still `docker exec` (or a native shell) wherever vault-api runs. **In `queue`
mode, SteamPrefill's binary and its `Config/` (the Steam session) live in the
`prefill_runner` process/container instead, so the login step moves there
too:**

```
docker compose exec vault-runner /opt/steamprefill/SteamPrefill select-apps
```

(the full path is required — measured: `SteamPrefill` alone is not on
`$PATH` in this image, only `/opt/steamprefill/SteamPrefill` is executable
there; the bare-name form fails with "command not found", not a login
prompt.) `deploy/README.md`'s "First run" section has the full walkthrough,
including the stable `container_name` WP S-2 gives this service for a
plain `docker exec` outside Compose. vault-api itself still never sees or
stores Steam credentials either way — nothing about that guarantee changes.

**`prefill_runner` does NOT need `VAULT_API_KEY`.** It calls
`Settings.from_env(require_api_key=False)` — the one exception to the "no
default, fails loudly if absent" rule `Settings.from_env()` otherwise
enforces. This process never serves HTTP and never authenticates a request,
so it has no legitimate use for the LAN control-plane secret that gates every
other vault-api endpoint; requiring it there anyway would mean the one
broad-egress container this split exists to isolate also holds the one
secret that guards everything else, for no functional benefit. Every OTHER
setting `prefill_runner` reads (`VAULT_DB_PATH`, `VAULT_STEAMPREFILL_PATH`,
`VAULT_PREFILL_TIMEOUT_SECONDS`, the three `VAULT_RUNNER_*` tunables) still
goes through the same `Settings`/`.env` mechanism vault-api uses.

**Crash semantics, briefly (full reasoning in the ADR):**

- *Runner dies mid-prefill:* its lease (`jobs.run_heartbeat_at`) goes stale;
  vault-api's worker — which is the one actively waiting on the job — notices
  on its own poll tick and fails the job through the same branch a timeout or
  a non-zero exit code already uses. The depot mapping is left untouched, the
  same as every other prefill failure.
- *vault-api restarts while the runner keeps going:* the job is left exactly
  `running` (queue mode's `jobs.recover_stale_jobs` narrowing) and the new
  process's worker thread resumes waiting on it on its very first tick
  (`jobs.find_active_run`) — the runner finishes normally, the restart is
  invisible to the eventual outcome.
- *No runner is running at all* (queue mode enabled, but nothing has claimed
  the job): the SAME staleness check fires once `VAULT_RUNNER_LEASE_TIMEOUT_SECONDS`
  has elapsed since the job started, so this is a visible `error` — not a
  silent, permanent `running` badge — even with no runner process to blame it
  on.

### Egress lock (WP EG-1, ADR-0011)

The runner split above is what made this possible: with SteamPrefill's own
broad Steam CM/CDN traffic living entirely in `vault-runner`, vault-api's
own container has nothing left that needs the wider internet by default —
`deploy/compose.yaml` now gives it no default network route for an
**arbitrary** outbound connection. Reaching an arbitrary WAN or other-LAN
device now requires passing through a separate `vault-proxy` container
(`deploy/proxy/`), reached over `HTTP_PROXY`/`HTTPS_PROXY`, which refuses
any such destination not on an allowlist. **No code in this package
changed for this to work**: `steam_relay.py`, `oracle.py`, and
`webhooks.py` all build their requests through Python's standard
`urllib`, which already honours those two environment variables on its
own.

**Two channels are measured, working, and deliberately left open (round-2
review B1/B2; `docs/adr/0011-egress-lock.md` has the full argument, not
repeated here):** DNS resolution from inside vault-api still reaches the
real internet (Docker's embedded resolver answers from the HOST's own
network namespace, not the container's — this is a working, unfiltered,
one-label-at-a-time exfiltration path, not a theoretical one), and the
Docker host's own reachable addresses — including anything any container
has published on `0.0.0.0` (`vault-core:80` by default) — remain directly
reachable, because a reply from the host needs no masquerade. Neither is
"vault-api can reach an arbitrary destination," which the lock does make
false; both are narrower, real exceptions this document states rather than
hides.

`VAULT_EGRESS_ALLOW` (see the Configuration table above) is the
operator-facing half of the allowlist. `Settings.from_env` cross-checks it
against `VAULT_MANIFEST_ORACLE` at boot: turning the oracle on without
adding its host here is refused, loudly, naming the missing host — the
alternative (booting clean, then failing every oracle query forever with a
filtered-403 from the proxy container, with nothing pointing back at the
allowlist as the cause) is exactly the "advertised but unreachable" bug
class this project has hit before (`docs/LEARNINGS.md` "Containers"), and
this check exists so this feature does not become its next instance. This
check deliberately lives in `from_env`, not `Settings.__post_init__` — it
is a check on the environment, not an invariant of the type, and several
of this project's own tests construct `Settings` directly with the oracle
on and no allowlist at all, unrelated to this feature.

**This check is unconditional, on purpose, and fires even without a
proxy in the picture at all (round-2 review S3).** The bare-metal/native
dev setup (`VAULT_PREFILL_MODE=subprocess`, no `vault-proxy` container
anywhere) still refuses to boot if `VAULT_MANIFEST_ORACLE` is on and its
host is missing from `VAULT_EGRESS_ALLOW` — the check has no way to know
whether a network-level lock is actually enforcing anything in a given
deployment, and cheaply assuming "no Docker means no need to set this" is
exactly the kind of assumption that stops being true the day someone
copies a `.env` from a locked deployment into an unlocked one, or vice
versa. Set `VAULT_EGRESS_ALLOW` to the oracle's host in either setup.

`VAULT_WEBHOOK_URL` gets no equivalent startup check: it is DB-overridable
at runtime (`PATCH /v1/settings`, ADR-0009), so there is no fixed boot-time
value to check it against. If a configured webhook stops firing after
upgrading to this version, that is the lock working — add the receiver's
host to `VAULT_EGRESS_ALLOW`. This applies the same way whether the
receiver is on the LAN or the internet, PROVIDED it is a genuinely
separate device: `vault-api`'s own network has no working direct route to
an arbitrary device once this lock is in effect, so there is no "skip the
proxy for local traffic" case for that common shape. The one exception
(a receiver bound to the Docker host's own address rather than a separate
device) is named, not hidden, two paragraphs up.

Full mechanism, the rejected alternatives, and a five-minute verification
recipe: `docs/adr/0011-egress-lock.md` and `deploy/README.md`'s "Egress
lock" section.

### Job outcome honesty (WP 3.3, ADR-0006 decision 1)

**The problem this closes (WP 1.7 finding):** SteamPrefill exiting `0` does
not mean it did anything for the requested app. An app the logged-in account
does not own resolves zero depots, prints `Prefilled 0 apps totaling 0 b`,
and still exits `0` — before this package, that landed as a `'done'` job with
a green app badge for a game that was never cached (real evidence:
`core/tests/mvp/RESULTS-20260805-222046.md`, app 480/Spacewar).

**The fix:** after a successful (exit `0`) run, `vault_api/prefill_summary.py`
parses SteamPrefill's own end-of-run summary table —

```
Prefilled 1 apps totaling 75.97 MiB in 16.5553

 Updated | Up To Date
---------+------------
    1    |     0
```

— and the parsed `Updated`/`Up To Date` counters, not the exit code alone,
decide the job's outcome. `last_manifest_check`'s column here is what
`GET /v1/games`/`GET /v1/games/{appid}` expose verbatim (WP 4c) — this table
is the authoritative list of which outcomes set it:

| Exit code | Summary parse | Updated / Up To Date | Job outcome | `apps.status` | `last_prefill_at` | `last_manifest_check` |
|---|---|---|---|---|---|---|
| non-zero / timeout / aborted / not-logged-in | — | — | `error` (unchanged, WP 1.4) | `error` | untouched | untouched |
| `0` | ok or failed | Failed column `>0`, or a Failed column whose count cannot be read | **`error`**, reason `prefill_failed` (WP API-FIX-3): the last line names what failed and, for two exact SteamPrefill phrases, the likely cause | `error` | untouched | untouched |
| `0` | failed (`parse_ok=False`), no Failed column | — | `done` (exit-code rule, unchanged) | `done` | set | untouched |
| `0` | ok | `0` / `0`, no Failed column or Failed `0` | **`error`** — "SteamPrefill did not consider this app — is it owned by the logged-in account?" | `error` | untouched | untouched |
| `0` | ok | `0` / `>0` | `done` | `done` | set | **set** (ADR-0006 "current as of \<timestamp\>") |
| `0` | ok | `>0` / anything | `done` | `done` | set | untouched |

**The Failed column (WP API-FIX-3).** SteamPrefill also exits 0 when it
fails an app; the table then has a third column, `Updated | Up To Date |
Failed`, and a successful run has none. Seen in production on 2026-10-02
twice: once with the cache unreachable ("22213 requests failed
unexpectedly"), once with "Unable to download manifests!". Both tables read
`0 | 0 | 1` and used to land in the "did not consider this app" row. They
now end `error` with vault-api's last line

```
[vault-api] Prefill failed (reason=prefill_failed): SteamPrefill reported 1 app(s) as failed: <cause>. The depot mapping and manifest state for this app were left unchanged.
```

where `<cause>` is "N download requests failed unexpectedly, so the cache or
the upstream it fetches from was unreachable", or "it could not download the
depot manifests; a possible cause is HTTPS to *.steamcontent.com being
rewritten to a cache that does not pass port 443 through" (the
DNS-mode/port-443 case, see `core/README.md`), or a pointer to the output
above. The hints match those exact phrases only and change no outcome. The
line keeps the `Prefill failed (reason=...)` shape the web reads. Like every
failure, a `prefill_failed` run writes no depot mapping and no manifest
state, leaves `needs_force` unchanged and queues no auto-GC; the job row still
records the parsed `updated`/`up_to_date`.

In queue mode the runner logs `run_success=... failure_reason=...` for the
SteamPrefill process only; vault-api sets the job's final state afterwards,
so `run_success=True` next to an `error` job is expected for the
`prefill_failed` and "not considered" rows.

The middle three rows are the interesting ones: a parse failure deliberately
falls back to the pre-WP-3.3 exit-code rule rather than guessing — a summary
table this parser cannot recognize is not evidence of anything — while
`Updated==0 AND Up To Date==0` is treated as definitive (that is the one
shape SteamPrefill only ever produces for "nothing to evaluate"). `apps.status`
in the unowned-app row goes to `error`, not left at `running` or reset to
`idle`: a run did execute, it just accomplished nothing, and `'error'` is
already this codebase's status for exactly that shape of outcome elsewhere
in `worker.py`.

**Parsing has to be tolerant, not exact — evidenced, not assumed.** The real
captured table above is not what actually reaches the parser. This project's
own SteamPrefill console output was found to be written in the OS's OEM
codepage (verified: 850 on this dev machine's German Windows install — see
the "OEM codepage" note under "Agent reports" below and
`tests/conftest.py`'s `mklink` capture comment), not UTF-8; decoding those
box-drawing-glyph bytes as UTF-8 (the pre-WP-3.3 code) fails on nearly every
one of them, and `errors="replace"` then silently replaced each with U+FFFD.
Real capture, corrupted exactly this way:
`core/tests/mvp/RESULTS-20260805-222046.md` / `RESULTS-20260805-223328.md`.
Two independent fixes land in this package:

1. **The decode itself** (`vault_api/prefill.py::_read_text`): try strict
   UTF-8 first (correct as-is for ASCII-only lines, or a SteamPrefill run
   that genuinely does write UTF-8, e.g. inside a Linux container); only on
   a decode failure fall back to the OS's actual OEM codepage
   (`GetOEMCP()`, not a hardcoded constant — a different Windows locale has
   a different one, 437 on US Windows for example).
2. **The parser itself** (`prefill_summary.parse_summary`) stays defensive
   even after the decode fix, for a stored job row from before this package,
   a host where OEM-codepage detection fails, or an SGR/ANSI remnant
   `--no-ansi` doesn't fully strip: it locates the `Updated ... Up To Date`
   header line (plain ASCII, survives any corruption of the divider glyph
   between the words), then the first following line that contains an ASCII
   digit (skipping the border/separator row, which is glyphs only), and
   pulls the first two integers out of it in column order. Any layout it
   doesn't recognize this way returns `parse_ok=False` with every field
   `None` — **never a guessed zero**, since `Updated==0 AND Up To Date==0`
   has the specific "app not owned" meaning above and an unparseable table
   never earned the right to claim it.

`GET /v1/jobs/{id}` and `GET /v1/jobs` both expose the parsed result as three
additive fields: `updated`, `up_to_date` (`int | null`), `summary_parse_ok`
(`bool | null`) — `null` for a job that never reaches a parsed summary (a GC
job, later; a failed/timed-out/aborted prefill; a job still queued/running).
Schema v4 (see "Database schema" below) adds the three backing `jobs`
columns.

### needs_force — reserving `--force` for first fills and refills (WP 3.4, ADR-0006 decision 2)

**The problem this closes:** `--force` used to be unconditional (see
"SteamPrefill invocation" above) — every job re-touched every chunk on disk,
even a routine "is this app still current?" check, which is exactly the cheap
non-forced no-op ADR-0006 decision 1's staleness check is built around
(~3 s, zero bytes, for an up-to-date app — measured, `docs/research/phase3-manifests.md`
§1b). Passing `--force` unconditionally would make that check nowhere near as
cheap for an app whose chunks are already large.

**The fix:** a per-app `apps.needs_force` flag (schema v5) decides, at the
start of every job, whether `run_prefill` is called with `use_force=True`.
`jobs.get_app_needs_force` reads it; `jobs.clear_needs_force_if_unchanged`
(the worker's end-of-job clear, see "Concurrent DELETE" below),
`deletion.reset_app_after_deletion(..., set_needs_force=...)` (the deletion
path's set for the *requesting* app),
`deletion.set_needs_force_for_remnant_co_owners` (the deletion path's set for
a last-remnant depot's *co-owners* — ADR-0003 addendum, see "Per-game
deletion" below), **and `gc_execute.flag_needs_force_for_depots`** (an
*executing* GC run's set, for every app mapped to a depot it actually removed
chunks from — see "Garbage collection" below; corrected here in the WP
4c-api fix round, this table previously said "only three writers" and
omitted this one) — are the writers. `set_app_status` deliberately has
**no** `needs_force` parameter at all, so there is no unconditional-write
path left for a future call site to accidentally reintroduce the race
described next. The lifecycle:

| Event | `needs_force` after |
|---|---|
| Fresh app (schema default, never filled) | `1` |
| Successful job — `'done'` outcome (covers both the `Updated>0` row and the `Up To Date>0`/`Updated==0` confirmed-current row of the job-outcome table above, and a `parse_ok=False` exit-code-rule `'done'`) | `0` |
| Unowned outcome (`Updated==0 AND Up To Date==0`, ends `'error'`) | unchanged |
| Any failure (non-zero exit, timeout, aborted, not-logged-in, internal error) | unchanged |
| `DELETE /v1/cache/{appid}` removed ≥1 depot, or reported a depot ALREADY-ABSENT | `1` |
| `DELETE /v1/cache/{appid}` — any depot landed in `failed` (partial deletion; cache state now unknown) | `1` |
| `DELETE /v1/cache/{appid}` — nothing exclusive/remnant existed to delete (every mapped depot was shared and protected) | unchanged |
| `DELETE /v1/cache/{appid}` deleted (or attempted, or found already-absent) a last-remnant depot — every **other** app still mapping it (ADR-0003 addendum) | `1` |
| `POST /v1/cache/{appid}/gc` with `execute: true` actually removed chunks from a depot — every app mapped to that depot, whether or not it is the app the GC job was queued for (ADR-0007) | `1` |
| `POST /v1/cache/{appid}/gc` — dry run, or an executing run that removed nothing | unchanged |

**This is routine maintenance, not a failure path — say so wherever this
flag's "ordinary case" is described.** A GC execute run is expected to run
periodically (optionally automatically, see `VAULT_AUTO_GC`), so any app
sharing a depot GC just reclaimed orphans from can carry `needs_force = 1`
while its cache content is otherwise perfectly healthy. A blanket "the
ordinary case is `needs_force = 0`" is therefore false for the window after
a GC execute touches a shared depot — it is only true absent a recent
deletion *or* a recent executing GC run.

Why deletion sets it rather than clearing it (this is the WP 1.4 rationale
ADR-0006 decision 2 explicitly preserves): SteamPrefill's own
`Config/successfullyDownloadedDepots.json` has no idea vault-api just removed
an app's depot directories, so a non-forced run after a deletion would
silently refuse to re-fill a game that is now demonstrably not on disk. The
ALREADY-ABSENT case is included on purpose: a repeated/racing `DELETE`
finding "nothing there" is still new information about cache state (not "no
change happened"), so it earns the same `needs_force=1` a real removal would.

**Concurrent DELETE: the clear is a compare-and-swap, not an unconditional
write (review fix).** `DELETE /v1/cache/{appid}`'s active-job check only
looks at the instant it runs (documented check-then-act, see "Per-game
deletion" below) — a *different* job for the same app can still be enqueued
and claimed while a DELETE's filesystem work is in flight. An earlier version
of this package cleared `needs_force` at the end of a successful job with a
plain `UPDATE apps SET needs_force = 0 WHERE appid = ?`, which is a
last-writer-wins race: a job that read `needs_force=0` at claim time and
finishes *after* a concurrent DELETE has already set it back to `1` would
clobber that `1`, wedging the app at `'done'` over an **empty** cache with no
self-healing path (SteamPrefill's own bookkeeping never learns the depots
were deleted, so every future run stays non-forced forever). The fix:
`jobs.clear_needs_force_if_unchanged(conn, appid, expected_needs_force)` clears
via `UPDATE apps SET needs_force = 0 WHERE appid = ? AND needs_force = ?`, where
the second parameter is the exact value the job read at claim time — a single
atomic SQL statement, so a DELETE's `1` landing in between makes the clear a
no-op instead of overwriting it, and the app's *next* prefill is correctly
forced. Regression-tested end to end in
`tests/test_worker.py::test_a_deletion_racing_a_slow_job_does_not_get_its_needs_force_clobbered`
(deterministic: the racing job is injected inside a wrapped
`deletion.plan_deletion`, which runs after the DELETE's own active-job check
and before its filesystem work, with a stub sleep long enough that the job
can only finish after the — fast — DELETE has already returned).

**No separate "force" flag on `POST /v1/prefill`** — the flag is entirely
server-managed (plan §9 "keep the API surface small"). An operator who wants
to force a re-fill uses the documented path: `DELETE /v1/cache/{appid}` then
`POST /v1/prefill {"appids": [appid]}`, which sets `needs_force=1` as a
natural consequence of the deletion. A non-forced run still correctly
"fetches only the changed chunks" (plan §4): it is SteamPrefill's own
bookkeeping deciding there's nothing to fetch at all when nothing changed,
and its normal delta behavior when something did.

`GET /v1/games` and `GET /v1/games/{appid}` expose `needs_force` (operator
visibility only — see "Endpoints" above).

### Mapping import — replace-semantics per app (ADR-0003 decision 3)

Which depots belong to the prefilled app is determined by **diffing the cache**
around the run, not by parsing SteamPrefill's output:

1. Before the subprocess starts, snapshot every
   `<VAULT_CACHE_ROOT>/depot/<depotid>/` directory as
   `(file_count, total_bytes, newest_mtime_ns)`. An aggregate, not a file
   listing — a real cache holds hundreds of thousands of chunks and this runs
   twice per job. Directories with zero files are ignored (nothing was stored).
2. After a **successful** run, snapshot again. Depots that are new or whose
   signature changed were filled by this job — unambiguous because only one job
   runs at a time.
3. `prefill.apply_observed_mapping` then writes the mapping:
   - **Replace within the app:** rows mapped to this appid that were *not*
     observed are deleted, so a depot Steam reassigned away from the app stops
     being reported as its content (and stops blocking WP 1.6's deletion as a
     false "shared" depot).
   - **Additive across apps:** only rows for *this* appid are touched. An
     observed depot that another app also maps keeps that mapping — plan §4's
     shared-depot case (redistributables); both apps keep reporting it as
     `shared`.

Edge cases, stated honestly:

- **Nothing observed → nothing changed.** A fully-cached app re-prefilled with
  `--force` serves every chunk from disk, writes nothing, and therefore yields
  an empty observation. The existing mapping is then left *completely* alone —
  wiping it on the strength of no evidence would delete correct data. The cost:
  a stale depot row only disappears once a prefill actually writes something for
  that app again (a game update), or via
  `DELETE /v1/mapping/{depotid}/{appid}`. The job log says so explicitly.
- **A failed, timed-out or aborted run never touches the mapping.** A partial
  run is not evidence about which depots belong to the app.
- **Concurrent cache writes are misattributed.** One job at a time makes
  *prefill* attribution unambiguous, but vault-core's store-on-miss keeps
  writing while a job runs — a LAN client downloading a *different* game
  through the cache during the window would have its depots attributed to the
  prefilled app. Acceptable for now (prefill is the primary fill mechanism, and
  the result is an over-broad mapping, not a lost one); a genuine fix needs
  per-app depot lists from PICS, which is a later package's business.
- **`successfullyDownloadedDepots.json` is not usable as a mapping source.** It
  maps depot id → manifest id (observed:
  `{"242921":[...],"3419431":[...]}`) with no app attribution, so it cannot
  replace the diff.

### Depot→app mapping semantics (plan §4)

`vault_api/mapping.py::upsert_mapping(conn, depotid, appid, name)` is the
single write path for the mapping table:

- Creates the `apps` row if it doesn't exist yet (status `'idle'`).
- Idempotent: calling it again with the same `(depotid, appid)` pair is a
  no-op the second time (`INSERT OR IGNORE` into `depot_app_map`) — the
  same pair legitimately gets reported repeatedly (repeated prefill runs,
  repeated manual calls).
- A `None` name never overwrites an existing name (only fills it in if the
  app row didn't have one yet).
- **This is the hook WP 1.4's prefill flow calls directly** — SteamPrefill
  knows the depot→app mapping at download time (plan §4), so the prefill
  job writes mappings by calling `upsert_mapping` in-process, not via HTTP.
- `PUT /v1/mapping/{depotid}` / `GET /v1/mapping` are the **manual
  fallback** for edge cases plan §4 names explicitly (delisted games,
  depots SteamPrefill doesn't recognize) — deliberately minimal, not a
  general CRUD surface.

**Mapping is additive, not replace-on-conflict (WP 1.3 review, B2 — decided
by the orchestrator, plan §4-conformant).** `PUT /v1/mapping/{depotid}`
with a *different* `appid` than an existing mapping for that depotid does
**not** replace the old mapping — it **adds** a second `(depotid, appid)`
row. A depot legitimately belonging to two tracked apps is exactly plan
§4's shared-depot case (redistributables, shared content), not an error
condition; a "last write wins" upsert would silently break that. Concretely:
re-`PUT`-ing depotid 999 first under appid 440 then under appid 730 leaves
*both* mappings in place, and `GET /v1/games/440` and `GET /v1/games/730`
both report depot 999 as `"shared": true`.

**Corrections use `DELETE /v1/mapping/{depotid}/{appid}`**, not the `PUT`.
If a mapping was created with the wrong `appid` (fat-fingered manual entry),
delete the specific wrong pair and `PUT` the correct one — don't rely on
`PUT` to overwrite it, since it won't. `DELETE` removes exactly that one
pair from `depot_app_map` and **never touches the `apps` row** — it's a
mapping correction, not a "forget this game" operation (that's
`DELETE /v1/cache/{appid}`, later work package, plan §4/§6). `404` if the
pair doesn't exist.

**Shared-depot flagging** (`GET /v1/games/{appid}`): a depot is reported
`"shared": true` if `depot_app_map` has any other `appid` row for the same
`depotid` (a single correlated `EXISTS` subquery per the full depot list,
not a per-depot round-trip). Plan §4: shared depots (redistributables,
shared content) are protected from deletion and reported, not silently
removed, **as long as at least one of the other mapped apps currently has
cache content** — this endpoint is what surfaces that fact before a deletion
is even attempted. `"shared": true` is therefore no longer synonymous with
"never deleted": since the ADR-0003 addendum (WP 3.5), a shared depot whose
every *other* mapped app is currently uncached is deleted anyway as a "last
cached remnant" — see "Last cached remnants" below for the exact rule.

### Input validation on the manual fallback endpoints (WP 1.3 review, should-fix)

Steam depot/app ids are always positive, and this is a human-driven
fallback endpoint (typos are the expected failure mode, not malice):

- `depotid` (`PUT`/`DELETE` path parameter) and `appid` (`PUT` body field,
  `DELETE` path parameter) all require `>= 1`; `0` or negative values `422`
  instead of creating a permanent junk row that nothing can clean up yet
  (no delete-by-appid exists before this endpoint).
- `MappingUpsertRequest` uses `model_config = ConfigDict(extra="forbid")` —
  an unrecognized body field (e.g. a typo'd `appId`) `422`s instead of
  silently upserting with `app_name` defaulting to `None`.

## Job control — cancel / pause / resume (WP 3.12)

Three endpoints, one mechanism, and one decision that the UI has to know
about (see "The worker slot" below).

| Endpoint | On a `queued` job | On a `running` job | On a `paused` job | On a finished job |
|---|---|---|---|---|
| `DELETE /v1/jobs/{id}` | cancelled immediately, never runs | `stop_request='cancel'`; the worker stops it | cancelled immediately | `409` |
| `POST /v1/jobs/{id}/pause` | `409` | prefill: `stop_request='pause'`; GC: `409` | `409` (already paused) | `409` |
| `POST /v1/jobs/{id}/resume` | `409` | `409` | back to `queued`, same job id | `409` |

`404` for an unknown job id everywhere. All three answer `200` with
`{job_id, status, outcome, detail}`; a client that reads only `outcome` always
knows whether it still has to poll (`"requested"`) or not (`"immediate"` /
`"resumed"`).

### The status model, and the audit behind it

Two new job statuses. `apps.status` gains **none** — it keeps exactly the four
plan-§3 values.

| Status | Terminal? | In `ACTIVE_STATUSES`? | Means |
|---|---|---|---|
| `cancelled` | yes | no | An operator stopped this job. Deliberately **not** `error`: stopping something on purpose is not a failure, and `error` has to keep meaning "something went wrong" |
| `paused` | no | **yes** | The subprocess was terminated and the job is parked until `resume` |

The shape above is the result of auditing every existing consumer of
`jobs.status`, not of picking names. Each row is pinned by a named test in
`tests/test_job_control.py`:

1. **`ACTIVE_STATUSES`** — `paused` is in, `cancelled` is out. The decisive
   consumer is `deletion._has_cache_content` (ADR-0003 addendum): a paused
   prefill has, by construction, written chunks to disk, so its app **must**
   count as "has cache content" — otherwise a co-owner's deletion could take a
   last-remnant shared depot out from under the very download the pause exists
   to preserve. Fail-closed, so `paused` counts.
2. **Dedupe** (`enqueue_prefill`, `enqueue_gc`) follows from (1): a second
   `POST /v1/prefill` for an app with a paused job returns *that* job with
   `deduplicated: true` rather than stacking a rival that would re-download
   what the paused one is holding progress for. Honest consequence: a paused
   job an operator forgets about keeps deduplicating. The escape hatch is
   `DELETE /v1/jobs/{id}`, which cancels a paused job immediately.
3. **`DELETE /v1/cache/{appid}` → `409`** also follows, with its own reason
   in the message: there is no write in flight, but deleting the depots would
   throw away the partial download resume continues from. Cancel the job, then
   delete.
4. **`claim_next_job`** still claims `queued` only — a paused job re-enters the
   queue exclusively through `resume`.
5. **`recover_stale_jobs`** still recovers `running` only, and that is
   load-bearing: **a paused job must survive an API restart and stay
   resumable.** Widening this query would eat every paused job on every
   container restart (mutation-tested).
6. **`needs_force`** (ADR-0006 decision 2) is keyed on *outcomes*, not
   statuses: `clear_needs_force_if_unchanged` is reached only from the one
   successful branch in `worker.py`, so cancel and pause leave the flag exactly
   as the deletion path or the schema default set it. Correct rather than
   merely convenient — the run did not complete, so whatever made it forced
   still holds.

`apps.status` after a cancel or pause goes back to **`idle`** (conditionally,
only if it is currently `running`). Not `error`, because nothing failed and the
red badge must keep its meaning; not left at `running`, because nothing is
running and that is the permanent-stale-badge bug `recover_stale_jobs` exists
to prevent. `last_prefill_at` is untouched, so an app filled earlier still
reports when. A **queued** job that is cancelled never touches `apps.status` at
all — it never set it, so cancelling a queued re-check cannot grey out a filled
game.

### Pause = terminate. Resume = re-run. The cache is the progress store.

**There is no wire protocol to SteamPrefill, because SteamPrefill has no pause
signal.** Pausing terminates the subprocess; resuming runs it again from the
start. That sounds wasteful and is not, for one measured reason: every chunk
the first attempt already stored is served back by vault-core as a **local
HIT** (Phase 0 / ADR-0001: ~120× faster than a miss), so a re-run replays at
disk speed rather than re-downloading. **The cache itself is the progress
store** — vault-api stores no byte offsets, no resume tokens and no partial
state of its own, and therefore has none to get wrong.

SteamPrefill's own `Config/successfullyDownloadedDepots.json` additionally
still lists the depots the interrupted run *completed*, which is true — those
depots really are done. A **non-forced** resume therefore skips them outright,
which is exactly the behaviour wanted. A **forced** resume (the app had
`needs_force = 1`, e.g. a first fill or a post-deletion refill) re-requests
everything instead, and is still cheap for the same HIT reason.

`_stop_process` terminates **and waits** (then kills and waits again), so by
the time a job row says `paused` the child has been reaped: a paused job never
has an orphan SteamPrefill behind it. Measured directly in
`test_terminating_the_child_really_reaps_it` against a real long-lived child
(not the `.cmd` stub shim, whose known Windows artifact is that terminating it
leaves the Python grandchild alive).

### The worker slot — a paused job does NOT hold it

**Decision: pause RELEASES the single worker slot, and resume goes to the front
of the queue.** This diverges from the UI mockup, which describes a paused job
as holding the slot with the queue waiting behind it — see the work-package
report; the UI should follow this behaviour.

Reasoning:

- Pause already terminated the subprocess, so there is nothing left to hold.
  "Holding the slot" would mean parking the *worker thread* on a job that owns
  no process, no file handle and no lock.
- vault-api runs exactly one worker (plan §3). A paused 60 GB download that
  kept the slot would starve everything else indefinitely — every other
  prefill, and GC. That is a worse failure mode than anything it buys.
- It would also break shutdown: `worker.stop()` joins the thread, and a thread
  blocked waiting for a human to press resume does not come back.
- It cannot survive a restart anyway. The paused state has to live in the
  database to be resumable at all, and once it does, an in-memory "slot held"
  is state that exists only until the next `docker restart`.

Resume needs no priority column to be a priority: the queue is FIFO by
`jobs.id` and a resumed job keeps the id it was created with, which is by
construction older than everything enqueued while it was paused. Flipping the
status back to `queued` therefore puts it at the **front**. (Creating a new job
row on resume — the obvious alternative — would silently do the opposite; a
test mutation-pins it.)

### What a cancelled or paused prefill does NOT do

A run stopped part-way is not evidence, and every consumer of "evidence"
already lives inside the success branch of `worker.py`. This package pinned
that rather than changing it:

- **No summary parse.** `updated`, `up_to_date` and `summary_parse_ok` stay
  `NULL` ("not applicable"). SteamPrefill prints its counter table at the *end*
  of a run, so a terminated run has either no table or one describing an
  earlier state — and `0 / 0` in particular has the specific "app not owned"
  meaning (ADR-0006 decision 1) that a stopped run has not earned.
- **No depot mapping.** `apply_observed_mapping`'s replace-semantics
  (ADR-0003 decision 3) would delete good rows on the strength of a partial
  observation.
- **No manifest ingestion.** `ingest_after_prefill` is inside the success
  branch, so a run killed mid-depot can never ingest a half-written `.bin`.
  The test plants a *valid* `.bin` in the temp-cache directory before the run
  and asserts `depot_manifests` is still empty afterwards, so this is a
  statement about a file that really was there to ingest.
- **No `needs_force` clear** (item 6 above).

What *does* survive is the only thing that should: the bytes already written to
the cache. Nothing is deleted or rolled back. Honest limit, unchanged from
every other failed-run path: a **first-ever** prefill that is cancelled leaves
depot directories with no mapping rows, so those bytes show up under
`unmapped_depots` in `GET /v1/cache/summary` until a later successful run for
that app attributes them.

**A stop request that arrives too late loses.** `_wait_for_process` checks
`process.poll()` first on every tick, so a run that finished on its own keeps
its real outcome and the log notes that the request was not applied — rewriting
a completed download as `cancelled` would discard the mapping and manifest work
it earned. Cancelling an already-finished job is a `409` for the same reason.

**Shutdown beats a pending pause.** If the container is going down and a pause
is pending, the run ends `aborted` (job `error`, which `recover_stale_jobs`
handles) rather than being parked at `paused` with no process left to honour
the resume.

### Cancelling a GC job — cooperative, between depots

GC cancellation is checked **between depots**, including before the first one,
and in dry runs as well as execute runs. Consequences, stated rather than
hidden:

- The depot being processed when the cancel lands **is finished**. One depot is
  bounded work, and stopping half way through it would leave the least useful
  state of all: some of that depot's orphans gone, some not.
- Depots not yet started lose nothing at all, and the report names them
  (`skipped_depots`).
- What was already removed stays removed and is reported exactly, following the
  module's existing honesty rule for partial work.
- The job ends **`cancelled`**, not `done` (it did not finish what it planned)
  and not `error` (nothing went wrong). If the depots that *did* run also had
  problems, the log still lists every one of them.
- `needs_force` is still set for the apps mapped to depots the cancelled run
  actually took chunks from — those bytes really are gone, so their owners
  really do have stale SteamPrefill bookkeeping.

Pausing a GC job is a `409`: a GC run is short and rebuilds its plan from
scratch on every execution, so "pause" would mean "throw the plan away and
build a new one later", which is what cancel-and-requeue already says honestly.

### How the request reaches the worker

`jobs.stop_request` (schema v8) — a database column, not an in-process event.
The request arrives on an HTTP thread and has to reach the worker thread, which
is inside a `subprocess` poll loop. A column needs no wiring between the two
(the worker already holds a connection), is visible to an operator with
`sqlite3`, and cannot be lost by the two sides disagreeing about which job id
is current. The worker re-reads it on its existing 0.2 s subprocess poll tick
(one primary-key `SELECT`; WAL readers never block) and between GC depots.
Every transition to a terminal status clears it (`jobs.finish_job`), so a job
never sits at "cancelling…" forever.

Every state transition — cancel, pause, resume — is decided inside a single
`BEGIN IMMEDIATE` transaction, the same write lock `claim_next_job` takes. So
"cancelled a queued job the worker was claiming at that exact moment" resolves
one way or the other and never both: either the job is cancelled and never
claimed, or it is running and the cancel becomes a `stop_request` the worker
honours.

### Auto-GC (`VAULT_AUTO_GC`)

`off` | `dry-run` | `execute` (**default since WP SWEEP-1 / ADR-0014,
2026-08-22 — was `off` through WP 3.12**). After a **successful** prefill
whose parsed summary reports `updated > 0`, vault-api queues a GC job for
that app in the configured mode.

Three conditions, all required, each a decision:

1. The setting is not `off`. WP 3.12's own reasoning for shipping `off` — "a
   feature that can delete files does not switch itself on" — is still why
   `off`/`dry-run` exist as real, supported choices; it stopped being the
   *default* the moment this setting was paired with the cached-apps sweep
   mode defaulting on too (see "Sweep target set" below and
   `docs/adr/0014-sweep-cached-and-auto-gc-default-on.md`). The `dry-run`
   rung still exists so an operator who wants that can watch what automatic
   collection *would* reclaim before trusting it.
2. The prefill reached the **successful** branch. A failed, aborted, unowned,
   cancelled or paused run tells you nothing about what is now orphaned.
3. The summary **parsed** and `updated > 0`. Orphans are what a game *update*
   leaves behind. A run that only confirmed "up to date" changed nothing, and
   queueing a full depot scan after every routine staleness check would turn
   ADR-0006's ~3 s no-op into real work on every sweep tick. An unparseable
   table is not evidence of an update either.

No new mechanism: it calls the same `jobs.enqueue_gc` the endpoint does, so the
per-(app, mode) dedupe rule applies unchanged — an operator's pending GC job
for that app in the same mode absorbs the automatic one instead of stacking a
second scan. The call is wrapped in its own `try`, because a follow-up job that
cannot be queued must not flip a genuinely successful prefill to `error` (same
reasoning as the manifest-ingestion call next to it). What happened is written
into the prefill job's own `log_excerpt`.

**`execute` combines with the grace window.** `VAULT_GC_GRACE_DAYS` still
applies, so the chunks the prefill just stored are protected by their own store
time and are not collected out from under the run that fetched them. (They are
also in the current manifest, so GC would keep them anyway — the window is the
belt to that suspenders. It matters for the *other* content in those depots:
beta-branch and store-on-miss chunks keep their fortnight.)

**Coupling with the WP 4d cached-apps sweep mode.** `VAULT_SWEEP_INCLUDE_CACHED`
(see the scheduler section's "Sweep target set — installed PLUS cached")
widens the nightly sweep to every app with content of its own on disk, and
every one it refreshes can leave its superseded chunks as fresh orphans.
`VAULT_AUTO_GC=execute` is what actually collects those orphans — `dry-run`
only reports what could be reclaimed and reclaims nothing, so it does NOT
close this loop either (B2, user decision "nothing is being reclaimed") — the
two settings are designed to be turned on together, with `execute` being the
half that matters for this specific coupling. Since WP SWEEP-1 / ADR-0014
(2026-08-22) both actually ship on together by default, for exactly this
reason. Turning the sweep mode on
while `VAULT_AUTO_GC` is anything other than `execute` is not refused (the
operator may have a reason: manual `POST /v1/cache/{appid}/gc` runs, say),
but it is never silent: `scheduler.cached_sweep_gc_risk` names the condition,
the scheduler logs a one-time `WARNING` on the transition into it (and a
matching `INFO` all-clear on the transition back out), and
`GET /v1/schedule`'s `sweep_cached_gc_risk` field exposes the identical
condition to a UI too.

## Per-game size calculation and cache summary (WP 1.5)

`vault_api/sizes.py` implements plan §3's "per-game size calculation (du over
depot folders, cached)" and feeds `GET /v1/games`, `GET /v1/games/{appid}` and
the new `GET /v1/cache/summary` (plan §6).

### The disk walk — shared with the prefill attribution diff

`sizes.scan_depot_signatures(cache_root)` walks `<cache_root>/depot/<depotid>/`
and returns `(file_count, total_bytes, newest_mtime_ns)` per depot id. This is
the **same** function `vault_api.prefill.scan_depots` uses for its
before/after attribution diff (WP 1.4) — `prefill.scan_depots` is now a thin
wrapper around it (WP 1.5 carry-over fix from the WP 1.4 review: the walk used
to be duplicated, `os.walk` + a separate `os.stat()` per file in both places).
The walk itself (`sizes.walk_file_stats`) uses `os.scandir` +
`DirEntry.stat()` instead — measured 17x faster on a 21k-file depot tree
(0.922s → 0.055s) because `DirEntry.stat()` is answered from data the
directory listing already returned rather than a second filesystem round
trip per file. `sizes.scan_depot_dir_bytes` is just the byte totals out of
the same signatures.

**Link handling (WP 1.6 carry-over fix #1 from the WP 1.5 review).** Top-level
depot directories are detected **following** links (`entry.is_dir()`), because
placing one depot directory on another volume via a symlink/junction is a
legitimate homelab move. Previously they were detected with
`follow_symlinks=False`, which made a *symlinked* depot directory **invisible**
— and the damage went beyond an under-reported size: the depot never showed up
in a prefill's before/after snapshot either, so `prefill.apply_observed_mapping`
treated the app's correct mapping row as stale and **deleted** it on the next
prefill. Measured: `DirEntry.is_dir(follow_symlinks=False)` is `False` for a
directory symlink but `True` for a Windows junction, so only symlinks were
affected — and that same asymmetry is why the fix is pinned by a test using a
**real** directory symlink (`make_dir_link` in `tests/conftest.py`, which falls
back to `mklink /J` where Windows refuses symlink creation without Developer
Mode).

*Inside* the tree the walk still refuses to follow links, and now refuses
junctions too (`sizes.is_link_like`): `follow_symlinks=False` alone reports a
junction as an ordinary directory, so a junction pointing at one of its own
ancestors would have recursed forever, and a junction pointing outside the cache
would have counted foreign bytes as this depot's size. Skipping them also keeps
"reported size" equal to "bytes a deletion would actually free".

### The TTL cache

`sizes.SizeCache` is deliberately the simplest thing that could work (plan
§9): one `threading.Lock`, one cached `SizeSnapshot` (`{depot_bytes,
total_bytes, computed_at}`), no background thread, no second table. A cache
miss (TTL expired, or never computed) walks the whole `depot/` tree once
**while holding the lock**, so concurrent callers that arrive during that walk
wait for and reuse the one fresh result instead of each re-walking the tree —
the lock doubles as request coalescing. One `SizeCache` instance lives on
`app.state.size_cache` (created in `main.create_app`, alongside
`app.state.settings`) and is shared by every request via the
`deps.get_size_cache` dependency — the whole point of the cache is that
concurrent requests share one scan.

TTL default: 60s, tunable via `VAULT_SIZE_CACHE_TTL`. **Invalidation is
explicit, not polled** — plan §3 says "cached", not "polled every N seconds":
`vault_api/worker.py` calls `size_cache.invalidate()` right after a
**successful** prefill job (unconditionally — even a "nothing new observed"
`--force` run may have rewritten existing chunks), so a game's size is never
more than `VAULT_SIZE_CACHE_TTL` seconds stale for an otherwise-idle cache,
and is fresh immediately after a fill. `SizeCache.invalidate()` is exported
for exactly this pattern — WP 1.6's deletion endpoint will call it the same
way after removing an app's depot folders.

### Per-app sizes: shared depots counted into every app that maps them

`sizes.app_size_bytes(depotids, depot_bytes)` sums an app's mapped depots'
bytes. A depot shared with another tracked app (plan §4: redistributables)
counts its **full** size into **every** app that maps it — this answers "how
much would deleting just this game free up if its depots weren't shared",
which is what an operator deciding what to delete wants to know. The
consequence, stated plainly and pinned by `tests/test_sizes.py`: **per-app
sizes may sum to more than the cache's actual disk usage.**
`GET /v1/cache/summary`'s `total_bytes` is the other number — each depot
counted exactly once — precisely so both questions can be answered.

Returns `None` (not `0`) in two distinct cases the API must not confuse:
- **unmapped** — the app has no depot rows at all (`depot_count == 0`).
- **uncached** — depots are mapped, but none have ever been written to disk
  (mapped before the first successful prefill, or a `--force` re-run that
  wrote nothing new — see the prefill mapping semantics above).

A *partially*-cached app (some depots on disk, others not yet) returns the sum
of what IS on disk — a missing depot there contributes `0` correctly, it just
hasn't been filled yet.

### `GET /v1/cache/summary`

`sizes.build_cache_summary` assembles the whole response from one `SizeCache`
snapshot plus two small queries (`depot_app_map` for per-app depot membership +
`apps` for names) — a summary request inside the TTL window costs no filesystem
access at all.

**WP 1.6 carry-over fix #2 from the WP 1.5 review:** the function now takes a
ready `SizeSnapshot` instead of the `SizeCache`, and `routers/cache.py` fetches
that snapshot *before* opening its SQLite connection. Previously a cache miss
walked the whole `depot/` tree under `SizeCache`'s lock while an open database
connection sat idle in the caller. Same fix removed the dynamically built
`WHERE appid IN (?,?,?)` list (with its `WHERE 0` special case for the empty
set) in favour of `SELECT appid, name FROM apps` plus a dict lookup — one row
per tracked game is less data than the mapping query already reads.

- **`total_bytes`** — real disk usage of `depot/`, each depot id counted once
  (see above).
- **`top_consumers`** — top 10 apps by `size_bytes` (ties broken by `appid`
  for a deterministic order), using the same `app_size_bytes` per-app sums
  `GET /v1/games` reports.
- **`unmapped_depots`** — `{count, size_bytes}` for depot directories present
  on disk with **no** `depot_app_map` row for *any* app. This is real operator
  information (plan §6): either a mapping was deleted/never created, or
  vault-core's store-on-miss wrote a depot that hasn't been attributed to an
  app by a prefill run yet (see the "concurrent cache writes" caveat under
  Mapping import above).
- **`free_disk_bytes`** — `shutil.disk_usage()` on `VAULT_CACHE_ROOT`, walking
  up to the nearest existing ancestor directory first (a fresh install may not
  have created it yet); `null` if no ancestor exists at all (defensive, should
  not happen on a real filesystem).

## Per-game deletion (WP 1.6)

`DELETE /v1/cache/{appid}` implements plan §4's "Deletion" section:
`appid → [depotids] → remove those depot directories → reset status to idle`,
with **shared-depot protection** — extended by the ADR-0003 addendum
(2026-08-06, WP 3.5) so a shared depot no longer leaks forever once every
co-owner has been deleted (see "Last cached remnants" below). The mechanics
live in `vault_api/deletion.py`, the HTTP shape in `vault_api/routers/cache.py`.

### Semantics

| Situation | Result |
|---|---|
| `appid` has no `apps` row | `404` — nothing to delete |
| `appid` has no `depot_app_map` rows | `404` — nothing to delete |
| a prefill job for the app is `queued`/`running` | `409` — retry after the job |
| `appid < 1` | `422` |
| the `VAULT_CACHE_ROOT` guards refuse | `500`, nothing deleted (see "Path safety") |
| otherwise | `200` with the per-depot report |

```json
{
  "appid": 440,
  "deleted_depots":  [
    {"depotid": 441, "size_bytes_freed": 1000000, "shared_with_uncached": []},
    {"depotid": 900, "size_bytes_freed": 50000, "shared_with_uncached": [730]}
  ],
  "skipped_shared":  [],
  "failed":          [],
  "total_bytes_freed": 1050000
}
```

(Depot 900 above is a **last cached remnant**: app 730 still maps it, but 730
is idle/never-prefilled/job-free, so its bytes would otherwise be
unreclaimable forever — see "Last cached remnants" below. `shared_with_uncached`
is `[]` on depot 441, an ordinary exclusive deletion, and would be omitted
from `skipped_shared` entirely, not listed there, once it's gone — that field
now means "protected", see next bullet.)

- **A shared depot is protected only while it has cache content somewhere else
  (ADR-0003 addendum) — not simply "shared".** A depot that any *other* tracked
  app also maps is skipped and reported with the other app ids (plan §4: "2
  depots shared with game Y, not deleted") **as long as at least one of those
  other apps currently has cache content** (see "Last cached remnants" below
  for the exact rule and what happens when none of them do). Before the
  addendum this held unconditionally; now it holds only for a "live" share.
  This still holds in both directions for a live share — after deleting game A,
  deleting game B still finds a depot shared with a *third*, still-cached app C
  protected, because A's mapping rows are kept (see below). Exclusivity/remnant
  status is decided **twice**: once when the plan is built, and again
  immediately before each individual depot is removed (see "Concurrency"
  below) — a depot whose co-owner state changed in between (became shared, or
  a co-owner gained content or a job) is kept and reported in `skipped_shared`
  with its fresh owner list.
- **A depot that is already gone** counts as `deleted` with
  `size_bytes_freed: 0` rather than as an error: the endpoint is idempotent, so a
  repeated `DELETE` (or a racing second one) answers `200` with zeros instead of
  failing.
- **`total_bytes_freed` is a floor, never an overstatement.** Sizes are measured
  by walking each depot directory immediately before removing it. A depot whose
  removal fails part-way contributes `0`, and a symlinked/junctioned depot
  directory contributes `0` because only the link is removed (its target's bytes
  are not freed).
- **`failed` is a partial deletion, still reported as `200`** (not `207`): the
  body already says per depot what happened, and a multi-status code would force
  every client to special-case something it cannot act on differently. What
  matters is that a depot still on disk is *never* reported as deleted.
- **`last_prefill_at` is always cleared, and `apps.status` reflects the
  outcome**: `idle` after a clean deletion, **`error` if anything landed in
  `failed`**. The status rule is deliberate (WP 1.6 review): *"failed" does not
  mean "untouched"*. `shutil.rmtree` deletes files as it walks and only then
  raises — measured here, an open handle on the 11th of 20 chunk files left 10
  of them already deleted — so a failed depot is typically **half** deleted
  while reporting `0` bytes freed. Leaving such an app at `done` would put a
  green badge on a half-destroyed game, and Phase 3's staleness check compares
  manifest ids rather than file counts, so it would never re-fill it. `error` is
  the honest state: the app UI surfaces it, and a re-prefill (which runs with
  `--force`) repairs the cache. Clearing `last_prefill_at` is unconditional for
  the same reason — that timestamp is no longer true either way. (Honest edge
  case: an app whose depots are *all* shared **and protected** has nothing
  deleted and is still reset to `idle`, even though its content remains on
  disk inside the shared depots. `GET /v1/games` still reports its real
  `size_bytes`, so the operator sees the truth. An app whose only depot is a
  last cached remnant is the opposite edge case — everything mapped IS
  deleted, `needs_force` is set exactly as for any other deletion, see
  `test_delete_deletes_the_only_depot_when_it_is_an_all_shared_remnant` in
  `tests/test_cache_delete.py`.)
- **`last_manifest_check` (WP 4c) is deliberately NOT cleared here**
  (`deletion.reset_app_after_deletion`, schema v4) — the one place it diverges
  from `last_prefill_at`. It is not a false claim even after the depots are
  gone: the app genuinely WAS confirmed current as of that timestamp, and
  what actually gates whether the *cache* is trusted after a deletion is
  `needs_force`/`status`, both of which this same statement does update. The
  visible consequence: `GET /v1/games`/`GET /v1/games/{appid}` can report a
  non-`null` `last_manifest_check` for a game with `size_bytes: 0` right
  after `DELETE /v1/cache/{appid}` — that is correct, not stale data left
  behind by accident.
- **The `SizeCache` is invalidated** right after the deletion
  (`sizes.SizeCache.invalidate()`, the hook WP 1.5 exported for exactly this),
  so `GET /v1/games` and `GET /v1/cache/summary` never serve pre-deletion sizes
  for up to `VAULT_SIZE_CACHE_TTL` seconds.
- **`apps.needs_force` is set to `1`** whenever this request changed or left
  uncertain what is on disk for this app — anything in `deleted_depots`
  (including the ALREADY-ABSENT case) or `failed` — so the app's next prefill
  runs with `--force` instead of trusting SteamPrefill's own now-stale
  bookkeeping (schema v5, WP 3.4, ADR-0006 decision 2 — see "needs_force"
  above). Left untouched in the all-shared-and-protected edge case just above,
  since nothing on disk actually changed for this app. This is also the
  documented way to force a re-fill: `DELETE` then `POST /v1/prefill`, no
  separate flag on the prefill API. **Every co-owner app id in a
  `shared_with_uncached` list also gets `needs_force = 1`** (ADR-0003
  addendum) — their own `status`/`last_prefill_at` are left untouched, only
  the force flag, since a depot they still map just changed under them; see
  "Last cached remnants" below.

### Last cached remnants (ADR-0003 addendum, WP 3.5)

**The problem this closes.** The original plan §4 rule protected any depot
mapped to more than one tracked app, judged purely from `depot_app_map` rows —
which survive deletion by design (see "The mapping rows are KEPT" below). If
apps A and B share depot D and both are deleted, one after the other, that
rule kept D "shared with the other" **both** times: neither app ever reports
D cached again, `GET /v1/cache/summary` attributes D's size to apps that say
"not cached", and nothing ever reclaims the space. Found during the Phase-4
mockup review; full decision in `docs/adr/0003-additive-depot-mapping.md`'s
addendum.

**The rule.** A shared depot may be deleted when **no co-owning app currently
has cache content** — judged conservatively (unknown ⇒ protected), so the
depot stays protected (kept, reported in `skipped_shared`) if ANY co-owner:

- has `apps.status != 'idle'` (`done`/`stale`/`error`/`running` all protect —
  even `error`, since that state doesn't mean "no content", just "last run
  failed"), or
- has `last_prefill_at` set (non-`NULL`), or
- has a queued or running job (`jobs.ACTIVE_STATUSES`), or
- is unreadable (a poisoned mapping row — reported as owner `0`, same
  convention as the pre-addendum code), or
- has no `apps` row at all (the mapping table has no foreign key to `apps`,
  so this can genuinely happen).

Only when **every** co-owner is verifiably `status='idle'`,
`last_prefill_at IS NULL`, and job-free is the depot deleted as a "last cached
remnant". **Mapping rows are still kept** either way — that part of ADR-0003
is unchanged; only the *directory* is removed.

**Two-stage decision, same shape as the shared-depot TOCTOU recheck.** At plan
time (`deletion.plan_deletion`), every co-owner named in the app's mapping
rows has its `status`/`last_prefill_at`/active-job state read up front
(`deletion.load_co_owner_states`, one bulk query) and handed in as plain data
— `plan_deletion` itself stays a pure function with no database access, so
every classification branch is directly unit-testable with dicts and tuples.
Immediately before each candidate depot is actually removed, the **full**
rule is re-evaluated from fresh data (`deletion.load_co_owners`, one joined,
indexed query per depot — still a handful of index seeks, not a scan: served
by `depot_app_map`'s primary key, `apps`'s primary key, and
`idx_jobs_appid_status`). A co-owner that became non-idle, gained a
`last_prefill_at`, or had a job enqueued in the window between planning and
removal protects the depot at execute time exactly like the pre-addendum
TOCTOU recheck protected a depot that became newly *mapped* in that window —
the depot lands in `skipped_shared` (or `late_shared` internally) instead of
being deleted. A recheck failure (a database error) still means "not
deleted, reported failed": unknown ownership never resolves to delete, and
never resolves to "these are the co-owners to flag" either.

**Reporting.** A remnant deletion appears in `deleted_depots` exactly like an
ordinary exclusive deletion, plus a non-empty `shared_with_uncached` field
naming the co-owner app ids it was shared with (see the example response
above) — additive only, every existing field keeps its exact prior meaning.
`skipped_shared` keeps its pre-addendum meaning unchanged: "shared with at
least one content-having co-owner". The audit log distinguishes a remnant
deletion from an ordinary one (`DELETED (last remnant): shared with uncached
app(s) [...]`, see "Audit trail" below).

**`needs_force` on the co-owners.** Every co-owner appid a removed (or
removal-attempted, or found-already-absent) remnant depot was shared with
gets `apps.needs_force = 1` — see the needs_force bullet above and
`deletion.set_needs_force_for_remnant_co_owners`'s docstring for the race
analysis (a co-owner's job claimed between the recheck and this write is
handled correctly by the *existing* `jobs.clear_needs_force_if_unchanged`
compare-and-swap, unmodified by this addendum). Their `status` and
`last_prefill_at` are deliberately **not** touched — the precondition for
landing in this set is that they are already `'idle'`/`NULL`, and this write
is only about disk state, not app lifecycle. **This is log-only when the
remnant removal itself FAILS**: `failed[]` entries in the response never
carry `shared_with_uncached` (only `deleted_depots[]` does — additive-only
per the decision above), so an operator watching the API alone cannot see
*which* co-owners were flagged for a failed remnant depot, only that they
were (via their own `GET /v1/games/{appid}.needs_force`); the reason —
"which depot, shared with which app ids" — is in the `cache-delete` audit
log line for that failure, not in the HTTP response.

**Retroactive repair needs no new endpoint.** Because mapping rows survive
deletion, an already-orphaned remnant (created by the pre-fix code, or by a
deletion that happened before this addendum shipped) self-heals the next time
*any* co-owning app is deleted again: `DELETE /v1/cache/{appid}` re-reads the
current mapping and co-owner state every time, so it has no memory of "already
decided this was shared" to work around.

**Worst case of the conservative judgment being wrong** (store-on-miss
content a client is actively using gets deleted because vault-api's tracked
state hadn't caught up yet): a re-download, never corruption — the same
honest limit ADR-0007's garbage collection accepts.

### The mapping rows are KEPT (decision)

Deleting a game from the cache does **not** delete its `depot_app_map` rows. The
mapping is *knowledge* — "these depots are this game's content" — not cache
state. Keeping it means a later prefill reuses it, the shared-depot protection
keeps working for the other apps that share a depot, and no information gathered
from PICS/prefill is thrown away by a cleanup action.

Consequences, stated plainly:

- `GET /v1/games/{appid}` keeps listing the app's depots, each with
  `size_bytes: null`, and the app's own `size_bytes` becomes `null` **only if all
  of its depots are gone**. An app that kept a shared depot still reports that
  depot's bytes — which is correct: that content is still on disk.
- `depot_count` does not drop after a deletion.
- Removing a mapping stays a separate, explicit operation:
  `DELETE /v1/mapping/{depotid}/{appid}` (ADR-0003's repair path).

### Path safety

**Two layers, boot-time and request-time (WP 4f added the first).**
`config.py`'s `Settings.from_env()` now refuses to **boot** at all on a
present-but-blank `VAULT_CACHE_ROOT`: `os.environ.get("VAULT_CACHE_ROOT",
"./cache")` only supplies that default for an ABSENT key — an unforwarded
compose key is exactly that (absent), and the default applies fine. The
actual blank case is a key that IS present with an empty value: a compose
`environment:` entry that forwards it via `${VAULT_CACHE_ROOT}`
interpolation with nothing set in `.env` renders as `VAULT_CACHE_ROOT=` in
the container, and a bare `KEY:` (compose) or `ENV KEY=` (a derived
Dockerfile) does the same explicitly. Before this guard, that case sailed
through as `cache_root=""` and only failed later — either inside
`resolve_depot_root` below (a 500 on every delete) or, with
`VAULT_SWEEP_INCLUDE_CACHED` on, as a `ValueError` inside the background
sweep thread every tick, forever, silencing the installed-based half of the
sweep along with it. Failing at boot turns that `ValueError` back into what
it was meant to be: an internal-contract assertion that should only ever
fire on a programming mistake, not on operator misconfiguration reaching it
live. See "Configuration"'s `VAULT_CACHE_ROOT` row for the one-line summary.

The deletion target itself is built from the **integer** depot id only
(`int` → `str`), joined under the resolved cache root, and verified before
anything is removed. Both inputs come from outside the code, so both are
guarded — as small pure functions with direct unit tests
(`tests/test_cache_delete.py`):

- `deletion.resolve_depot_root(cache_root)` refuses, deleting nothing, when
  `VAULT_CACHE_ROOT` is **empty** (measured: `os.path.abspath("")` is the current
  working directory, so an unset value would aim a recursive delete at wherever
  vault-api happens to run), resolves to a **filesystem root** (`/`, `C:\`, a
  bare UNC share — note `os.path.realpath("/")` is `C:\` on Windows, so a
  Docker-style path pasted onto a Windows host lands here), or contains **no
  `depot/` directory**. That last one refuses a misconfigured root and a
  never-used cache alike: both mean "there is nothing here to delete from", and
  saying so loudly beats "successfully deleted nothing".
- `deletion.depot_dir_path(depot_root, depotid)` rejects anything that is not a
  positive integer (`0`, negatives, `None`, `True`, floats, `"../../etc"`,
  `"441/../.."`, `"441; rm -rf /"`, `"C:\Windows"`, …) and then verifies the
  result is a **direct child** of `depot_root` (`dirname(candidate) ==
  depot_root`, which a `startswith` prefix check would not give you — that would
  accept `…/depot-evil`). Non-integer depot ids are not merely theoretical:
  SQLite's INTEGER *affinity* does not enforce the column type, so a hand-edited
  or corrupted database really can hold a string there. Such a row is reported in
  `failed` (with `depotid: 0`, since it cannot be named) and nothing is touched
  for it.
- A mapping row whose *co-owner* app id is unreadable makes the depot count as
  **shared and protected** — something references it, so refusing to delete is
  the safe reading. Unconditional: such a depot is never eligible for the
  "last cached remnant" classification either, no matter what the *readable*
  co-owners' content state says (ADR-0003 addendum).
- String depot ids must be **exactly ASCII digits** (`isascii() and isdigit()`),
  not "whatever `int()` accepts" (WP 1.6 review). `int()` parses `" 441 "`,
  `"1_0"` and Arabic-Indic `"٤٤١"` perfectly happily; none of those is a depot id
  vault-api ever writes, and on the path that decides which directory gets
  destroyed a value that odd means the row is broken and the operator should be
  told rather than have it silently normalised.

### Link safety (symlinks and Windows junctions), measured

A depot directory that is a symlink or a junction must never be traversed —
recursively deleting through it would destroy data outside the cache. Everything
below was **measured** on Windows 11 / CPython 3.12.10 against a real
`mklink /J` junction pointing at a directory outside the cache root, and every
row is pinned by a test:

| Probe | Result |
|---|---|
| `os.path.islink(<junction>)` | **`False`** — an islink-only guard misses junctions |
| `os.path.isjunction(<junction>)` | `True` (CPython ≥ 3.12) |
| `DirEntry.is_dir(follow_symlinks=False)` on a junction | **`True`** — looks like a plain directory to a walk |
| `shutil.rmtree(<junction>)` | **raises** `OSError("Cannot call rmtree on a symbolic link")`; the target is untouched |
| `os.rmdir(<junction>)` | removes the junction only; target and its contents survive |
| `shutil.rmtree(<real dir containing a junction>)` | completes, removes the junction *as a link*, target survives |

So `shutil.rmtree` does **not** follow junctions — neither at the top level,
where it refuses outright, nor nested inside a tree, where it unlinks them. But
because it refuses outright, it also cannot delete a legitimately linked depot
directory. `deletion.remove_depot_dir` therefore handles the link case itself: if
the depot directory is link-like (`sizes.is_link_like`, which checks *both*
`islink` and `isjunction`), only the link is removed (`os.rmdir`, falling back to
`os.unlink` for POSIX symlinks, where `rmdir` fails with `ENOTDIR`); otherwise
`shutil.rmtree` runs. The nested-junction behavior is pinned by its own test so a
future CPython regression surfaces in this suite instead of in a user's data.

### Audit trail

Every decision goes to the standard `logging` module at INFO (failures at ERROR),
prefixed `cache-delete`, and records the **resolved absolute path** rather than
just the depot id — this is the audit trail for an operation that destroys user
data. Real output from the live verification below (app 440 maps an exclusive
depot 441/442 each, plus depot 900 shared with a still-cached app 730):

```
INFO vault_api.routers.cache: cache-delete appid=440 starting: depot_root=...\cache\depot exclusive=[441, 442] remnant=[] shared=[900] unusable_rows=0
INFO vault_api.deletion:      cache-delete appid=440 depot=900 KEPT: shared with app(s) [730]
INFO vault_api.deletion:      cache-delete appid=440 depot=441 DELETED path=...\cache\depot\441 bytes_freed=1000000 link=False
INFO vault_api.deletion:      cache-delete appid=440 depot=442 DELETED path=...\cache\depot\442 bytes_freed=2000000 link=False
INFO vault_api.routers.cache: cache-delete appid=440 finished: deleted=2 (of which 0 last-remnant) skipped_shared=1 (of which 0 late) failed=0 bytes_freed=3000000; status set to 'idle', last_prefill_at cleared, needs_force=1; remnant co-owners flagged=[]; mapping rows kept
```

A depot that was already gone logs `ALREADY-ABSENT`, a path-guard rejection logs
`REFUSED by the path guard`, a failed removal logs `FAILED` with the exception
type and message, a depot removed by a racing request logs `ALREADY-ABSENT`
with the reason, a depot that became shared between plan and removal logs
`KEPT (late recheck)`, and a refused cache root logs
`REFUSED (cache root guard)`.

**Last cached remnant (ADR-0003 addendum, WP 3.5)**, illustrating the same
depot 900 once app 730 has ALSO been deleted or was never prefilled, so 900
becomes the last cached remnant instead of a protected share (exact format
strings from `deletion.delete_app_depots`/`routers/cache.py`):

```
INFO vault_api.routers.cache: cache-delete appid=440 starting: depot_root=...\cache\depot exclusive=[441] remnant=[900] shared=[] unusable_rows=0
INFO vault_api.deletion:      cache-delete appid=440 depot=900 DELETED (last remnant): shared with uncached app(s) [730] path=...\cache\depot\900 bytes_freed=50000 link=False
INFO vault_api.deletion:      cache-delete appid=440 depot=441 DELETED path=...\cache\depot\441 bytes_freed=1000000 link=False
INFO vault_api.routers.cache: cache-delete appid=440 finished: deleted=2 (of which 1 last-remnant) skipped_shared=0 (of which 0 late) failed=0 bytes_freed=1050000; status set to 'idle', last_prefill_at cleared, needs_force=1; remnant co-owners flagged=[730]; mapping rows kept
```

A remnant depot whose removal *fails* still logs the remnant context in its
`FAILED`/`ALREADY-ABSENT` line (e.g. `FAILED path=... OSError: ... (last
remnant, shared with uncached app(s) [730])`), and app 730 still gets
`needs_force = 1` — see "Last cached remnants" above for why a failed or
already-absent remnant removal still counts.

### Concurrency, transactions and cancellation

- **No database transaction is held across the filesystem work.** The endpoint
  opens a connection for the read (app row, mapping rows, active-job check),
  closes it, deletes, then opens a connection again for the status reset. That
  keeps the `deps.db_opener` rule intact (a connection never leaves the thread
  that created it, see "Connection handling") and means a long `rmtree` cannot
  block writers.
- **The shared-depot decision is rechecked at execute time (TOCTOU), and since
  the ADR-0003 addendum that recheck covers the FULL remnant rule, not just
  "does anyone else map it".** The plan is built from one snapshot at the
  start of the request, and no lock is held across the filesystem work — so a
  `PUT /v1/mapping/{depotid}` landing in that window could make a depot shared
  *after* it was planned for deletion, or a co-owner's job/prefill/status
  could change in that window and turn a planned last-remnant deletion into
  content destruction for another game — either way breaking plan §4's (and
  its addendum's) guarantee. Immediately before removing **each** depot —
  whether it was planned as exclusive or as a remnant, both go through the
  identical check — its owners AND their current content state are re-read
  with one **joined** indexed lookup (`depot_app_map`'s primary key, `apps`'s
  primary key, `idx_jobs_appid_status`'s correlated `EXISTS` — three index
  seeks, not a scan, which is what keeps a per-depot recheck affordable). A
  depot with at least one currently-content-having owner is kept, logged as
  `KEPT (late recheck)` and reported in `skipped_shared`; a depot whose every
  owner is (still, or newly) uncached is deleted as a remnant. If the recheck
  itself fails, the depot is **not** deleted and is reported in `failed` —
  "unknown ownership" must never resolve to "delete it", and never resolves
  to "these are the co-owners to flag" either. This narrows the window to the
  microseconds between the recheck and `remove_depot_dir`; a mapping or a job
  written *during* the `rmtree` was always going to lose that race, and
  closing that last gap would need a lock held across filesystem work.
- **The 409 guard is check-then-act, and that is stated rather than hidden.** A
  prefill job enqueued in the microseconds after the check would still race the
  deletion. Single-worker operation keeps the window tiny and the outcome is
  benign (the job refills what was deleted), so this stays a guard rather than a
  lock that would have to be held across filesystem work.
- **Two concurrent `DELETE`s of the same app** both answer `200`: the loser
  reports zeros, so the freed bytes are counted exactly once. Getting that right
  needs more than a `try`/`except` — the loser's `rmtree` walks a tree the winner
  is deleting underneath it and raises part-way through, while the outer depot
  directory may still be there for another moment. `deletion.remove_depot_dir_settling`
  therefore decides the outcome from the **settled** filesystem state: a removal
  that raised an `OSError` is retried a few times (it is idempotent; worst case
  ~80 ms) and only a path that is *still present* at the end counts as a failure.
  Retrying every `OSError` rather than just `FileNotFoundError` is measured, not
  padding: on Windows a racing deletion surfaces as `PermissionError [WinError 5]`
  just as often, because a deleted-but-still-open file enters "delete pending" and
  reports access-denied instead of not-found. Without this, a perfectly clean
  concurrent deletion landed in `failed` and dragged the app to `status='error'`
  (measured: `test_two_racing_deletes_do_not_5xx_or_double_delete` failed in 2 of
  15 isolated module runs before the fix, 0 of 70 after). Also covered by the
  mixed hammer in `tests/test_concurrency.py`.
- **Deletion runs in the request thread** (a sync endpoint, so FastAPI's
  threadpool) — right for the size of a typical depot tree, and it is what lets
  the response report exactly what happened. A client that disconnects
  mid-deletion does **not** abort the work; only the report is lost.

## Agent reports (WP 2.4)

`POST /v1/agent/installed` is the vault-agent's only write path (plan §3, §6).
The agent — on a Windows gaming PC or a Linux/SteamOS device (ADR-0002) — posts
the **complete** list of installed Steam app ids, typically every 30 minutes.
The mechanics live in `vault_api/agent_reports.py`, the HTTP shape in
`vault_api/routers/agent.py`.

```
POST /v1/agent/installed
{"client_id": "gaming-pc", "appids": [440, 570, 1234]}

200
{"client_id": "gaming-pc", "received": 3,
 "added": [1234], "removed": [730], "first_report": false}
```

### Full-list snapshots and the server-side diff (ADR-0002)

The agent is **stateless and dumb by design**: it never computes a delta, it
just says what is installed *now*. vault-api stores that as one snapshot row
and derives `added`/`removed` by diffing against that client's previous
snapshot. Consequences worth stating:

- **A snapshot is a set.** Duplicate ids in one request collapse
  (`[440, 440, 730]` stores `[440, 730]`) and the stored order is sorted, so
  `received` is the number of **distinct** ids — it can be lower than the
  number of entries sent. The response's `added`/`removed` are sorted too.
- **`first_report: true`** means "there was no usable previous snapshot", and
  then `added` is the whole list and `removed` is empty. That covers the
  client's genuine first report *and* the rare case of a predecessor row whose
  JSON could not be decoded (corrupt/hand-edited database) — that row is
  logged at WARNING and the chain restarts at the next report instead of the
  endpoint 500ing forever.
- **An empty list is a legitimate report**, not an error: a machine with
  nothing installed reports `[]`, and the diff correctly says everything was
  removed. The empty snapshot is stored like any other, so a reinstall later
  shows up as an addition.
- **`appids` is required.** Omitting it is a `422`, deliberately not "an empty
  library" — a broken agent build that stops sending the field must not read
  as "every game was uninstalled". Same reason `extra="forbid"` is set: a
  typo'd `appIds` would otherwise leave `appids` missing.
- **Clients are fully isolated.** The diff only ever looks at rows with the
  same `client_id`; two machines with different libraries never interfere.
- `client_id` must be 1–64 characters, printable, with no control characters,
  no leading/trailing whitespace, and it may not be `.` or `..`. Nothing
  derives a filesystem path from it today; rejecting the two path segments is
  one line that forecloses a traversal if a future feature ever does (a
  per-client log or export directory is an obvious candidate), and neither is
  a machine label anyone meant to type. Control characters are rejected because
  the value is written into log lines (a newline would let a malformed agent
  forge audit lines); surrounding whitespace because it is an identity key and
  `"pc"` / `"pc "` would silently be two clients. Both are `422`, not silently
  normalised — it is a one-line setting in the agent's config.
- `appids` is capped at 10 000 entries (larger than any real Steam library),
  so a broken agent cannot push a multi-megabyte blob into SQLite every 30
  minutes.
- Numeric strings (`"440"`) are coerced to ints by Pydantic's default lax
  mode. Pinned by a test rather than "fixed": `POST /v1/prefill` behaves
  identically, and the `>= 1` constraint still applies afterwards (`"0"` is a
  `422`). A JSON agent cannot produce this anyway.
- **Booleans are rejected** (`422`) — see "One shared `AppId` type" below.

### One shared `AppId` type (WP 2.4 review)

`vault_api/validation.py` defines the single app-id request type used by
`POST /v1/agent/installed`, `POST /v1/prefill` **and**
`PUT /v1/mapping/{depotid}`:

```python
AppId = Annotated[int, BeforeValidator(reject_bool), Field(ge=1)]
```

Two things it fixes, both found by the WP 2.4 review:

- **`true` is no longer an app id.** `bool` is an `int` subclass in Python, so
  lax mode accepted a JSON `true` and stored **app id 1** — a real Steam app
  id, so nothing downstream would have looked wrong. It also made the *write*
  path more permissive than this project's own *read* path, which already
  drops booleans when decoding a stored snapshot
  (`agent_reports._decode_appids`). (`false` was already a `422`, but only by
  accident: it coerces to `0`, which fails `ge=1`.)
- **The three endpoints can no longer diverge.** They previously each declared
  their own `Annotated[int, Field(ge=1)]` — identical by coincidence, not by
  construction. Behavior for ordinary integers and for numeric strings is
  unchanged; the full existing suite (including the pinned `"440"` coercion
  test) stays green.

### Removals are SURFACED, never acted on (the boundary)

This is the deliberate limit of this work package, and it is what ADR-0002 and
plan A9 ask for. When a title disappears from a client's library, vault-api:

- **returns** it in `removed`, and
- **logs** it at INFO, audit-style:

```
INFO vault_api.agent_reports: agent-report client='gaming-pc' stored snapshot: reported_at=2026-08-05T20:44:34Z apps=3 first_report=False added=1 removed=1 pruned=0
INFO vault_api.agent_reports: agent-report client='gaming-pc' REMOVED 1 app(s) from the client library: [730] - cache content is NOT deleted and apps.status is NOT changed (ADR-0002: removals are surfaced; deletion stays a human/API decision, plan A9)
```

and it does **none** of the following:

- it does not delete cache content for the removed app (that is
  `DELETE /v1/cache/{appid}`, an explicit human/API decision — plan A9),
- it does not change `apps.status` or `last_prefill_at`,
- it does not create `apps` rows for reported app ids (a report is an
  observation about a *client machine*, not a statement about the server's
  cache; creating rows here would make every game installed anywhere show up
  as a tracked, uncached game),
- it queues nothing.

Phase 3's scheduler is the component that turns these snapshots into a prefill
set (plan §7 Phase 2's last bullet, executed in Phase 3). This package builds
the data path it will read; live-verified above and pinned by
`test_a_removal_is_logged_but_changes_no_app_state`, which seeds a tracked,
cached, `status='done'` app and asserts the status, the timestamp, the depot
mapping, the chunk file on disk and the job queue are all untouched by a
removal report.

The log line is ASCII on purpose (a Windows console at codepage 850 renders an
em dash as a replacement character — observed during verification), and long
removal lists are sampled (`[...50 ids] (+N more)`) so one report cannot emit a
10 000-element log line.

### Retention

`VAULT_AGENT_REPORT_KEEP` (default 20, **minimum 2**) snapshots are kept per
`client_id`. Pruning happens **inside the same transaction as the insert**, so
the table is bounded at all times rather than by a periodic cleanup job that
could fail to run. Only the *oldest* rows go.

The floor of 2 is enforced at startup (`Settings.from_env` raises) and clamped
again in `agent_reports.prune_reports`: with `keep=1` the prune would delete
the predecessor in the same transaction that writes the new snapshot, so every
report would come back as a `first_report` with no removals — a silently
broken diff, which is worse than refusing to boot.

Consequence to be aware of: **`first_seen` in `GET /v1/clients` is the oldest
*retained* report**, not a permanent first-contact record. It moves forward as
old snapshots are pruned. A durable "known clients" table is Phase 3's business
(it needs one anyway for hit statistics).

### Ordering: rowid, not the timestamp

The "previous" snapshot is the previous row by SQLite **`rowid`** (insertion
order), not by `reported_at`. `reported_at` is a second-precision server
timestamp, so two reports inside one second tie, and a clock that steps
backwards (NTP correction on a homelab box that just booted) would reorder the
chain outright. `rowid` can do neither, and pruning only ever removes the
oldest rows, so the maximum `rowid` stays monotonic and is never recycled.
`idx_agent_reports_client_time` still narrows the lookup to one client's rows;
retention keeps that at a handful, so the small sort by rowid on top is free.

### Concurrency: the diff chain must not fork

Reading the previous snapshot, inserting the new one and pruning all happen
inside **one `BEGIN IMMEDIATE` transaction** (`jobs.immediate_transaction`,
the same primitive the job queue uses). Without the write lock, two reports
from the same client arriving together both read the same predecessor: both
report the same additions, one snapshot gets diffed twice and another never at
all — a forked chain, and the ADR's removal detection silently loses events.

That is measured, not assumed.
`test_parallel_reports_from_one_client_form_one_unbroken_chain` fires 8
parallel reports at one `client_id`, each with a distinct single-app library so
every response identifies itself (`added`) and names its predecessor
(`removed`), and asserts a single unbroken chain: exactly one first report, no
predecessor claimed twice, the walk from the first report visiting all 8, and
all 8 snapshots on disk. Re-running it against a version with the transaction
removed failed 5 out of 5 times — a representative failure reported **4 of 8**
requests as `first_report: true`.

`GET /v1/clients` and `POST /v1/agent/installed` also joined the mixed hammer
in `tests/test_concurrency.py` (now 90 parallel requests), where agent writes
race prefill enqueues, cache deletes and the size-cache scan.

### `GET /v1/clients` — minimal v1, forward-compatible

```json
[{"client_id": "gaming-pc", "first_seen": "2026-08-05T20:44:34Z",
  "last_reported_at": "2026-08-05T20:44:36Z", "app_count": 0},
 {"client_id": "steam-deck", "first_seen": "2026-08-05T20:44:36Z",
  "last_reported_at": "2026-08-05T20:44:36Z", "app_count": 1}]
```

Plan §6 describes this endpoint as "per-client hit stats incl. bypass
warnings". **Hit statistics and bypass detection are Phase 3** — they need
vault-core's access log, which does not feed vault-api yet. What ships here is
the agent-report half of the same object, in a shape Phase 3 can extend rather
than replace: a flat object per client, so fields like `cache_hits`,
`last_seen_in_cache_log` or `bypass_suspected` are *added* next to these and a
client that reads only the fields it knows keeps working.

- `app_count` is the size of the client's **latest** snapshot (`0` for an empty
  library; `null` only if that stored row's JSON is unreadable — logged at
  WARNING).
- `last_reported_at` and `app_count` come from the *same* row, which is why
  the implementation does a `GROUP BY` for `first_seen` plus one
  `latest_snapshot` lookup per client rather than a single `MAX(reported_at)`
  aggregate. The follow-up query runs once per gaming machine — a homelab has
  a handful.

### Installed state per app (`installed_on`, WP AG-1)

`GET /v1/games` / `GET /v1/games/{appid}` gained an additive field: which
agents currently claim this app installed, and when.

```json
{"appid": 440, "name": "Team Fortress 2", "status": "idle",
 "installed_on": [{"client_id": "gaming-pc", "reported_at": "2026-08-22T09:15:03Z"}]}
```

**Freshness is enforced before an entry exists, not left to the frontend to
judge from a raw timestamp.** `installed_on` is built from
`scheduler.fresh_client_snapshots` — **the exact same function**
`compute_targets` calls before trusting a client's report for sweep
targeting: readable snapshot, parseable `reported_at`, and no older than
`schedule_client_stale_days` (default 7, `VAULT_SCHEDULE_CLIENT_STALE_DAYS`).
A client whose latest report fails any of those checks simply has no entry
here, in any game — the same way it would be excluded from a sweep. This was
a deliberate choice between the two options on the table: expose the raw
timestamp and let the frontend guess at staleness, or reuse the scheduler's
own trust boundary. The second was chosen because a UI badge reading
"installed on the operator's server" from a report the scheduler itself refuses to act on
would be the same dishonesty in a different place, and because
`docs/LEARNINGS.md`'s "two call sites computing the same predicate diverge"
entry (WP 4f) is exactly the failure mode a second, independently-written
staleness check invites. `reported_at` is still returned on every surviving
entry — it already passed the gate, but a frontend showing "as of 09:15" or
sorting by recency needs the real value, not just a boolean.

`compute_targets` itself is unchanged in behaviour: the loop that used to
live inline now lives in `fresh_client_snapshots`, called once from
`compute_targets` and once from `routers/games.py`'s
`_installed_on_by_appid`/`_installed_on_for_appid` (via the shared
`_fresh_snapshots_now` helper), so the two can never quietly disagree about
which reports are trustworthy again — pinned structurally in
`tests/test_scheduler.py` (a sentinel-returning fake swapped in for
`fresh_client_snapshots`, asserting `compute_targets` returns exactly its
result), not just behaviourally.

**The staleness BOUND itself must be resolved live, not read from the boot
snapshot (review round 1, blocker B1).** `schedule_client_stale_days` is
overridable at runtime (`PATCH /v1/settings`, `applies: "next_sweep"`), and
the scheduler's own tick already re-resolves it via
`settings_store.effective_settings` on every tick. Round 1 fed
`fresh_client_snapshots` from `app.state.settings` directly — the boot
snapshot, set once and never mutated — so a `PATCH` that tightened the
window took effect for the NEXT SWEEP but not for this endpoint: measured,
a client 2 days quiet kept showing as `installed_on` here after a PATCH to
`schedule_client_stale_days: 1` had already made the scheduler treat it as
stale. `routers/games.py`'s `_fresh_snapshots_now` now resolves through
`settings_store.effective_settings(conn, base_settings)` on every call —
the exact one-line fix `routers/schedule.py`'s `GET /v1/schedule` already
uses for the identical reason — and `base_settings`/`now` both come from
`request.app.state.scheduler` (`.settings`, `.now()`), the same object and
the same injectable clock the scheduler's tick itself uses, rather than a
second, independent `datetime.now()` call in this file.
`tests/test_games.py::test_installed_on_follows_a_runtime_override_of_the_stale_days_setting`
pins this: PATCH the key, assert `installed_on` follows on the same request
cycle; reverting the fix to read the boot snapshot makes that test fail.

**Cost:** `GET /v1/games`' `_installed_on_by_appid` and `GET /v1/games/{appid}`'s
`_installed_on_for_appid` both call the shared `_fresh_snapshots_now`, which
runs the distinct-client query plus one indexed `latest_snapshot` lookup per
client **once per request** (list endpoint) or once per detail request
(review round 1 nitpick: the single-app endpoint no longer builds the
whole-library dict just to read one key back out of it — same per-client
scan, lighter re-keying). Cost scales with the number of *gaming machines*
that have ever reported, not with the number of *apps* being listed; a
vault with zero agents never executes the per-client branch (the
distinct-client query returns no rows) and the field comes back `[]` for
every game, cheaply.

**Scope note:** an app only appears in `GET /v1/games` at all if it has an
`apps` row (created by a mapping upsert — see "Depot→app mapping semantics"
below). An agent reporting an appid nobody has ever mapped contributes
nothing to `installed_on` for that reason, same as `size_bytes` already
ignores an unmapped appid; nothing new here, just stated for completeness.

**Decided resolution (review round 1, S4): "never installed anywhere" and
"installed, but every reporting agent has gone quiet" are the SAME empty
list at the per-app level, and that is being kept, deliberately.**
`_fresh_snapshots_now` already computes the excluded clients (the
`_excluded` return value of `fresh_client_snapshots`) and the router
currently discards it — a game nobody has ever installed and a game
the operator's server reported two months ago (now past the stale window) both render
`installed_on: []`, with nothing in the response distinguishing "no signal"
from "stale signal". The field shape stays additive and app-scoped as
shipped; the fix is NOT a bigger `installed_on` payload. AG-2's badge
("installed but not cached") should build any global "an agent is present
but quiet" indicator from `GET /v1/clients`' existing `last_reported_at`
compared against the schedule's `client_stale_days`
(`GET /v1/schedule`) — both already shipped, both already vault-wide rather
than per-app, and exactly the information "is anything still reporting at
all" needs. If a frontend package later demonstrates an actual per-app need
to tell the two states apart (not merely that the distinction is
theoretically visible), `installed_on_stale: list[InstalledOn]` alongside
`installed_on` is an additive two-liner then — same shape, opposite filter,
no schema change to the existing field. Not built speculatively now.

### Deleting a client (`DELETE /v1/clients/{client_id}`, WP AG-1)

WP AG-0 made a client's reported identity (`client_id`) a visible, operator-
renameable choice — and a rename leaves the *old* name as a ghost row in
`GET /v1/clients` forever, since nothing previously removed a client's rows.
This endpoint is that cleanup path.

```
DELETE /v1/clients/gaming-pc
204 No Content
```

`404` if `client_id` has no rows at all — repeating the call is not
idempotent by design, same shape as `DELETE /v1/mapping/{depotid}/{appid}`.

**Tables touched — established by reading `db.py`'s DDL, not assumed:**

- `agent_reports` — every retained snapshot for this client. This is the
  table that makes a client "exist" at all (`list_clients` groups by it).
- `client_bypass_state` (WP 3.13) — the webhook feature's last-computed
  bypass verdict for this client. Cleared too: if the same `client_id`
  reports again, the transition detector should establish a fresh baseline
  rather than compare against a verdict computed for what is, semantically,
  a different machine now.

**Tables deliberately left alone, and why nothing is left dangling:**

- `client_cache_stats` is keyed on `client_addr` (a network address), not
  `client_id` — schema v9's `agent_reports.source_addr` is the only bridge
  between the two, and it lives on the table this endpoint DOES clear.
  Deleting a client's agent reports simply removes its contribution to
  `GET /v1/clients`' per-client totals on the next read; the address rows
  themselves are cache-traffic history and outlive the client exactly like
  they outlive an agent that just stops reporting on its own.
- `depot_miss_stats` / `miss_trigger_state` are keyed on `depotid`/`appid` —
  cache content, not client identity (plan §4: "cache content is keyed by
  app, not client"). No column in either table references a `client_id`.
- `jobs`, `depot_app_map`, `apps`, and every manifest/oracle table: none
  carry a `client_id` column, so there is nothing to cascade.

**Not a ban.** A client that reports again under the same `client_id` after
being deleted is treated as a genuine first report — a fresh diff chain,
`first_report: true`, nothing "removed" — exactly as if the machine had
never reported before. This is the intended behaviour for the rename
cleanup use case: delete the old name, let the agent's next scheduled report
(now sent under the new name, or the same name if nothing else changed)
start clean.

**Concurrency.** The existence check and both deletes run inside one
`BEGIN IMMEDIATE` transaction — this project's standing idiom for any
check-then-act write (`jobs.immediate_transaction`, the same primitive
`store_report`'s own read-previous/insert/prune sequence uses). A concurrent
`POST /v1/agent/installed` for the *same* `client_id` therefore serializes
against this DELETE rather than interleaving mid-write, and either order is
accepted:

1. **DELETE commits first.** The waiting report then runs against an empty
   history — `latest_snapshot` returns `None`, so it is a genuine first
   report. Exactly the reappearance behaviour above.
2. **The report commits first.** Its new snapshot lands, then DELETE removes
   *everything* for that `client_id`, including the report that just
   arrived. The operator's delete wins the race — a report landing
   microseconds before an explicit "remove this client" call is swept up in
   the same intent, the same class of accepted race `DELETE /v1/cache/{appid}`
   already documents against the scheduler (a job enqueued microseconds
   after a guard check "refills what was deleted" — here the agent can
   simply report again).

A read that is not inside either transaction (`GET /v1/clients`, a sweep
tick reading `scheduler.fresh_client_snapshots`) can observe the `client_id`
from its own `DISTINCT`/`GROUP BY` query and then find `latest_snapshot`
return `None` for it a moment later, if this DELETE's transaction commits in
between. Both call sites already handle that shape defensively (skip the
client for that one read) — before this endpoint existed the branch was
unreachable in practice (nothing else could remove a client's *last* row);
this package makes it reachable for the first time, and the existing
degrade-gracefully behaviour is exactly correct, not a new gap.

`tests/test_concurrency.py`'s mixed hammer (now 100 parallel requests) and a
dedicated 15-iteration race test in `tests/test_clients_api.py` both drive
this: the dedicated test interleaves a real `DELETE` and a real
`POST /v1/agent/installed` for the same `client_id` across genuine
`threading.Thread`s and asserts the on-disk result is always exactly one of
the two outcomes above, never a partial or duplicated state.

## Manifest parsers (WP 3.1)

`vault_api/manifests.py` implements the parsing half of ADR-0006 (staleness
detection) and ADR-0007 (manifest-diff garbage collection):
`docs/research/phase3-manifests.md` established that both Steam manifest
formats are parseable with ~60 lines of stdlib code, and this package turns
that research into pure functions plus a test suite. **This module itself
stays pure parsing** — no schema, no filesystem archive, no GC logic, no HTTP
endpoint. WP 3.2 (see "Manifest ingestion" below) is what stores and archives
what these functions produce; GC itself remains Phase 3.6.

### Two formats, one shared result shape

| | SteamPrefill `.bin` | Cache-stored manifest |
|---|---|---|
| Where | `$HOME/.cache/SteamPrefill/v1/{originalAppId}_{containingAppId}_{depotId}_{manifestId}.bin` (`%LOCALAPPDATA%\SteamPrefill\v1` on Windows) | `/cache/depot/<id>/manifest/<manifestid>/5/<requestcode>` |
| On-disk shape | Plain, uncompressed protobuf | ZIP, single deflate entry `z`, containing a sectioned stream (PAYLOAD/METADATA/SIGNATURE/END) |
| Parser | `parse_steamprefill_bin(path) -> PrefillManifest` | `parse_cache_manifest(path) -> CacheManifest` |
| Self-check | filename ids vs. payload ids must match | parsed chunk count vs. METADATA `unique_chunks` must match |

`PrefillManifest` and `CacheManifest` are **the same dataclass**
(`ParsedManifest`), not two structurally-similar types — that is the point of
"a common shape usable by GC" (the work package's own phrasing): calling code
never has to branch on which source a manifest came from.

```python
@dataclass(frozen=True)
class ParsedManifest:
    depot_id: int
    manifest_id: int
    chunks: dict[str, int]   # 40-hex chunk id -> compressed byte size
    source: str              # "steamprefill_bin" | "cache_manifest"
```

`chunks` values are `cb_compressed`/the `.bin` format's compressed length —
prior research established these are byte-exact against the real cached
chunk file's size, so this map alone is enough for GC's reclaim-size
reporting without a second filesystem walk.

### Wire format, and one detail the research doc didn't spell out

Field numbers (confirmed empirically against real files while building this
module — see "Empirical validation" below, not just taken on faith from the
research document):

- `.bin` payload: field 2 = manifest id, field 4 = depot id, field 1
  (repeated) = `FileData`, whose field 1 (repeated) = `ChunkData`
  (field 1 = chunk id as a 40-hex ASCII string, field 2 = compressed length).
- Cache manifest METADATA: field 1 = depot_id, field 2 = gid_manifest, field 7
  = unique_chunks.
- Cache manifest PAYLOAD (`ContentManifestPayload`): field 1 (repeated) =
  `FileMapping`, whose field 6 (repeated) = `ChunkData` (field 1 = sha, 20 raw
  bytes -> hex-encoded; field 5 = cb_compressed).

**New finding, not in the research document:** the sectioned stream's END
marker (`0x32C415AB`) is a **bare 4-byte magic with no length field** —
unlike PAYLOAD/METADATA/SIGNATURE, which are each `u32 magic + u32 length
(LE) + payload`. Discovered by probing the raw bytes of a real cache-stored
manifest (`poc/cache/depot/481/manifest/...`) while writing
`manifests._read_sections`; missing this would have made every real manifest
file look truncated.

**Filenames inside a cache-stored manifest's PAYLOAD are Valve-encrypted**
(need the depot decryption key, which vault-api never holds) — this module
never attempts to read them. Garbage collection (ADR-0007) only needs chunk
SHAs, which are **not** encrypted, so this is a non-limitation for this
project's purposes, stated once here rather than re-litigated later.

### Untrusted input, bounded by construction

Both formats are bytes that ultimately trace back to network content
SteamPrefill or a Steam client wrote, so every entry point treats them as
hostile:

- `MAX_MESSAGE_SIZE` (64 MiB) bounds every buffer this module parses as one
  unit — the whole `.bin` file, one section of the sectioned stream, the
  decompressed `z` entry, and every nested submessage.
- `MAX_CHUNK_COUNT` (2,000,000) bounds how many chunks a single manifest may
  contribute — headroom above the largest real manifest seen while
  researching this WP (SteamPrefill depot 990081: 72,283 chunks).
- The ZIP entry is read through a **bounded** `read(MAX_MESSAGE_SIZE + 1)` on
  an open stream, not `ZipFile.read(name)` — the ZIP format's declared
  `file_size`/`compress_size` describe what the writer *claims*, not a limit
  enforced during decompression, so reading unbounded would let a hostile
  entry inflate past any check this module could otherwise make.
- The field reader (`_read_fields`) is **iterative, not recursive**: a nested
  submessage is parsed by calling it again on a byte slice, not by the
  function calling itself on growing input. Unlike `agent/vault_agent/acf.py`'s
  genuine recursive-descent KeyValues parser (which needs, and has, an
  explicit depth cap after the WP 2.1 `RecursionError` lesson in
  `docs/LEARNINGS.md`), there is no call-stack depth here driven by
  attacker-controlled nesting to bound in the first place — nesting in both
  manifest formats is a small, fixed number of levels this module's own code
  walks explicitly.
- Every failure mode — a truncated varint, an oversized declared length, a
  non-zip file, a zip missing the `z` entry, a missing required field, a
  filename/payload id mismatch, a `unique_chunks` mismatch, a malformed chunk
  id/sha — raises this module's own `ManifestParseError`. No other exception
  type escapes a public function here.

### Empirical validation (read-only; no output committed)

Run manually against real files on this dev machine while building this
module (not part of the automated suite — these paths are machine-local):

- **SteamPrefill `.bin` manifests**, `%LOCALAPPDATA%\SteamPrefill\v1\*.bin`
  (8 files): all parsed without error; filename ids matched payload ids in
  every case, including the shared-depot example
  `107100_228980_229002_....bin` (app 107100 pulling a depot nominally
  belonging to app 228980). Chunk counts ranged from 1 (`107103`) to 70,643
  distinct chunks (`990081`, 72,283 raw `ChunkData` entries before dedup by
  id — the difference is repeated chunk ids across `FileData` entries, not a
  bug).
- **Cache-stored manifests**, `poc/cache/depot/*/manifest/*/5/*` (7 files
  across 5 depots, one depot with 3 differently-request-coded copies of the
  same manifest): all parsed without error, `unique_chunks` matched the
  parsed chunk count in every case. Reproduced
  `docs/research/phase3-manifests.md`'s numbers exactly: depot 1070561 → 3594
  chunks, 1391111 → 8174, 229006 → 84, 4594150 → 2, 481 → 8 (all three
  differently-request-coded copies of depot 481's manifest agree).
- **Cross-check against on-disk chunk files** (both sources): for every depot
  above, the parsed chunk id set was diffed against
  `poc/cache/depot/<id>/chunk/` and every parsed `cb_compressed`/compressed
  length was compared against the real on-disk file size —
  **zero orphans, zero size mismatches** in all 5 cache-stored-manifest
  depots and all 8 `.bin`-manifest depots (including the two partially-cached
  ones, `107101` and `990081`, where the `.bin` manifest legitimately lists
  more chunks than are on disk yet — a "missing" chunk, not an orphan).
- `core/cache/depot/` held no manifest files at this time (only chunk data
  for depot 70403) — nothing to validate there.

Two mutation tests were run manually (revert the check, confirm the
corresponding test fails, restore it) as an extra confidence check beyond the
committed suite: disabling the `.bin` filename/payload depot-id cross-check,
and disabling the cache manifest `unique_chunks` self-check, each made exactly
one test fail (`test_bin_depot_id_mismatch_between_filename_and_payload_is_rejected`,
`test_cache_manifest_unique_chunks_mismatch_is_rejected`) — confirming those
tests are genuine regression guards, not incidental passes.

## Manifest ingestion (WP 3.2)

`vault_api/manifest_ingest.py` wires up what WP 3.1's parsers produce: after
every **successful** prefill job, the worker (`vault_api/worker.py`) calls
`ingest_after_prefill(conn, appid=<job's appid>, settings=...)`, which:

1. Scans `VAULT_STEAMPREFILL_CACHE_DIR` for files named `{appid}_*.bin` —
   only this job's own app, never every file in the (shared) directory.
2. Parses each candidate with `manifests.parse_bin_filename` (recovers
   `containing_appid`) and `manifests.parse_steamprefill_bin` (the validated
   payload, which independently re-checks the filename against the payload).
3. Upserts a `depot_manifests` row (`vault_api/depot_manifests.py`) —
   **latest-per-`(appid, depotid)`, replacing any older row** (ADR-0006
   decision 3; this is not a manifest history table).
4. **Additive shared-depot mapping (WP 3.2 item 4):** if `containing_appid !=
   appid`, the depot is *also* mapped to `containing_appid`
   (`mapping.upsert_mapping`, additive per ADR-0003) — on top of, not instead
   of, the job's own replace-set mapping
   (`vault_api.prefill.apply_observed_mapping`, unchanged by this work
   package, still driven by the before/after depot-directory diff).
5. Archives the source `.bin` file durably (`vault_api/manifest_archive.py`)
   and prunes older archives for that depot down to `VAULT_MANIFEST_KEEP`.

**A file that fails to parse is warned about and skipped — it never fails the
job.** Two kinds of "skip" are tracked *separately*, not conflated
(`IngestResult.parse_failures` vs. `IngestResult.vanished_during_scan`,
review nitpick): a file still on disk that is genuinely corrupt/malformed is
a parse failure (WARNING); a file that disappeared *between* the directory
listing and the parse attempt — SteamPrefill's own `clear-temp` running
concurrently, or an operator clearing the directory by hand — is an I/O race
(INFO), not a data-quality problem, and reads that way in the job log.
Neither ever fails the job, and neither does a bug anywhere in this whole
ingestion step: `worker.py` wraps the call in its own `try`/`except`, *local*
to the success branch and separate from the job's outer exception handler,
specifically so a crash in ingestion can never flip an already-successful
prefill job to `'error'`
(`tests/test_worker.py::test_ingestion_failure_never_flips_a_successful_job_to_error`).
A missing/unreadable cache directory (the common case: SteamPrefill has never
run, or `VAULT_STEAMPREFILL_CACHE_DIR` isn't set up yet) is the same kind of
non-event, logged at INFO, not a warning.

### The reverse direction of ADR-0003 (measured) — which table is authoritative

Step 4's additive mapping can later be **undone** by the containing app's own
next prefill: `apply_observed_mapping` is "replace within the app" — any
depot mapped to `containing_appid` that is NOT in *that app's own* next job's
observed set gets deleted. If the shared depot is already fully cached from
`containing_appid`'s point of view, its own next prefill writes nothing new
for it, never "observes" it, and that job's replace step removes the very
row this function added. **This is not a regression and it self-heals**: the
next time the *original* app (the one actually pulling the shared depot) is
re-prefilled and re-ingested, the additive mapping is written again — the
mapping can flicker, it does not disappear for good.

Two tables can therefore legitimately disagree about a shared depot's owners
at a given moment, and that is by design, not a bug to reconcile:

- **`depot_app_map`** is what **today's** shared-depot deletion protection
  reads (`DELETE /v1/cache/{appid}`, WP 1.6) — and it is exactly the table
  subject to the flicker above.
- **`depot_manifests.containing_appid`** is the **durable** record of "which
  app this depot's manifest said it belongs to", rewritten fresh on every
  ingest of the *original* app and never touched by any *other* app's
  prefill job. This is what ADR-0007's future GC keep-set is expected to
  read for shared-depot attribution, precisely because it doesn't flicker.

### Why `.bin` files are archived at all

SteamPrefill's manifest temp cache does **not** survive its own `clear-temp`
command (research doc, Q1) — the only durable record of a manifest vault-api
ever saw is a copy it makes itself. `manifest_archive.archive_manifest` copies
(never moves — the temp cache isn't vault-api's to delete from) the source
file into `VAULT_MANIFEST_ARCHIVE_DIR` as `{depotid}_{manifestid}.bin`,
**atomically**: written to a same-directory tempfile, then `os.replace`d into
place (the same pattern as `vault_api.prefill.write_selected_apps`), so a
reader never observes a partially-written file and a re-archive of an
already-current manifest safely overwrites rather than corrupting it.

**Retention (`VAULT_MANIFEST_KEEP`, default `3`):** `prune_archive` keeps only
the newest `VAULT_MANIFEST_KEEP` archived files **per depot** — this is a
**total** count including the current manifest, the same "keep the last N"
semantics as `VAULT_AGENT_REPORT_KEEP`, not "N previous *in addition to* the
current one" (a plausible alternative reading of "current + N previous" —
stated explicitly here since the wording is genuinely ambiguous).
"Newest" is decided by the archived file's own mtime (set at the moment this
module wrote it — i.e. ingestion order), not by parsing `manifestid` as a
number: `manifestid` is stored as opaque TEXT (see below) precisely because it
is not guaranteed sortable across every source.

### Why `manifestid` is TEXT, not INTEGER

Steam manifest ids are unsigned 64-bit values; SQLite's `INTEGER` storage is
signed 64-bit. Every manifest id observed during this project's research
stayed under `2**63 - 1` (e.g. `3040704736299968944`, ≈3×10¹⁸, comfortably
under ≈9.2×10¹⁸) — but a u64 value **can** legitimately exceed that ceiling,
and this column is a durable, load-bearing record, not a scratch value. TEXT
never overflows and the column is never used for arithmetic, only
equality/lookup, so there is no cost to being safe here.
Pinned by `tests/test_depot_manifests.py::test_manifestid_stores_a_value_beyond_sqlite_int64_range_as_text`.

### Coupling canary (research doc risk 6)

Reading SteamPrefill's own temp-cache directory couples vault-api to its
internal layout — version-pinned at 3.7.1 (same pin as the rest of the
prefill orchestration, WP 1.4). `manifest_ingest.log_cache_dir_canary` runs
once at startup (`main.py`'s lifespan, right after stale-job recovery) and
logs a **WARNING, never a failure**, if `VAULT_STEAMPREFILL_CACHE_DIR` exists
and contains a **`.bin`** file that doesn't match the
`{originalAppId}_{containingAppId}_{depotId}_{manifestId}.bin` filename
pattern — bounded to the first 10 offending names plus a total count (the WP
3.1 review's namelist-truncation lesson, applied here proactively rather than
waited out for a second report). This is a coupling *canary*, not a
validation gate: it never stops vault-api from starting and never fails a
job — a per-file parse failure during ingestion is still just "warn and skip
that one file". The canary is the only signal an operator gets that a future
SteamPrefill version may have changed its cache layout underneath this code.

**Restricted to `.bin` files (WP 3.2 review fix).** SteamPrefill's real
temp-cache directory also holds non-manifest sidecar files — observed on a
live host: `cellId.txt` (its cached Steam cell/region id) and
`lastUpdateCheck.txt` (a timestamp). The first version of this canary flagged
both of those on **every single boot** of a real deployment, which trains an
operator to ignore the warning — the one failure mode a canary must not
have. Non-`.bin` files are now ignored entirely, whatever their name; only a
`.bin` file that fails the filename contract counts as a real mismatch.
Pinned by `tests/test_manifest_ingest.py::test_canary_ignores_known_non_bin_sidecar_files`
and `::test_log_cache_dir_canary_is_silent_for_known_sidecar_files`.

### Change frequency (WP 4h.1)

Phase 4h's decision-support panel wants to say "changes every few days" vs.
"unchanged for two years" per app — one of the two fields WP 4h.1 added
(alongside `rtime_last_played` in the Steam relay section above). This is the
first thing that reads `depot_manifests` outside the ingestion path itself
(see "What this work package deliberately did NOT do" below, which this
supersedes for that one bullet), and it had to reckon with a fact this table
was designed around: **it is "latest-per-(appid, depotid), not a history
table"** (see the top of this section) — every re-ingest REPLACES the row, so
there was never a record of individual change EVENTS, only the current
snapshot. Three columns (schema v14, "Database schema" above) make a bounded,
honest signal possible without turning this table into the history table its
own design deliberately avoids being:

- `first_seen_at` — written once, at INSERT, never touched by the upsert's
  `DO UPDATE` afterwards. The observation window's anchor.
- `manifest_changed_at` — starts equal to `first_seen_at`; only advances to
  the new `recorded_at` when a re-ingest's `manifestid` actually **differs**
  from what was stored. A confirmed-current, non-forced run (ADR-0006 tier 1)
  re-ingests the SAME manifest and must not look like a change just because
  vault-api looked again.
- `observation_count` — increments on every ingest, changed or not.

`vault_api.depot_manifests.change_frequency_for_app` (single app, used by
`GET /v1/games/{appid}`) and `.change_frequency_by_app` (one bulk query for
every app, used by `GET /v1/games` to avoid an N+1, pinned by
`test_change_frequency_by_app_is_one_statement_regardless_of_app_count` —
the same statement-count technique `test_prefill_cached.py` uses for the
analogous `POST /v1/prefill/cached` claim) turn these into
`GameSummary`/`GameDetail`'s three new fields:

| Field | Type | Meaning |
|---|---|---|
| `manifest_change_frequency` | `str \| null` | `null` = no `depot_manifests` row for any of this app's own-ingested depots at all (never prefilled since this feature existed, or mapped only via the manual fallback). `"insufficient_data"` = some data exists, but not enough to say anything (see the two pins below). `"stable"` = enough data, no observed manifest change, ever. `"changed"` = enough data, and at least one depot has been seen to change AT LEAST ONCE. **This field's own name is more confident than what it measures — see the correction right below the table.** |
| `manifest_observation_days` | `int \| null` | Days since the YOUNGEST-observed depot's `first_seen_at` — the conservative (shortest) reading across the app's own-ingested depots. `null` only alongside a `null` category. Populated even when the category is `"insufficient_data"`, so a frontend can render the actual number ("observed for 3 days") instead of a bare label with nothing behind it. |
| `manifest_days_since_last_change` | `int \| null` | Days since the MOST RECENTLY observed change across this app's depots (the latest, not the first, and not an average). Populated **only** when `manifest_change_frequency == "changed"` — `null` for `"stable"` (never observed one), `"insufficient_data"`, and `null` (never earned the right to say). |

**Post-review correction: `manifest_change_frequency` is not a rate, and the
category value is named to say so.** The category was originally called
`"changing"` — reviewed and rejected: it answers "has this app's manifest
been observed to change at least once since we started watching", full stop.
An app that changed once three years ago and one that changes every week are
BOTH `"changed"`, indistinguishable from the category alone, because nothing
in it decays and `depot_manifests` never stored an event log to compute a
cadence from in the first place (see "Latest-per-(appid, depotid)" at the top
of this section — there is no history to rate). The category is now named
`"changed"` (past tense, not `"changing"`) to stop implying an ongoing rate.
`manifest_days_since_last_change` is the one rate-ADJACENT fact this table
actually supports cheaply, and it is what gets a frontend closest to the
plan section's own example statements: "changes every three days" is out of
reach from this data; "last changed 3 days ago" is in reach, and the two are
not the same claim — which is why it is its own field instead of being
folded into the category.

**The two honesty pins this field exists to prove, both mutation-tested
(`tests/test_depot_manifests.py`):**

1. **`null` and `"insufficient_data"` are deliberately different answers.**
   "We have never looked" (no row at all) and "we looked, but not long or
   often enough to say" (some rows, thin data) must never collapse into the
   same "unknown" — a frontend that can only render one undifferentiated
   "?" cannot distinguish a never-tracked game from a freshly-tracked one,
   and Phase 4h's plan section explicitly wants that distinction visible.
   Pinned by `test_pin_no_manifest_history_at_all_is_none_not_insufficient_data`.
2. **A game with exactly one observation is the boundary where "frequency" is
   undefined**, however old that single observation is —
   `MIN_OBSERVATIONS_FOR_FREQUENCY = 2` gates on the WEAKEST-observed depot
   across the app (one freshly-mapped depot pulls the whole app back to
   `"insufficient_data"` even if every other depot has years of history).
   Pinned by `test_pin_exactly_one_observation_is_insufficient_data_however_old`
   and `test_one_freshly_added_depot_pulls_the_whole_app_back_to_insufficient_data`.
3. **A short observation window is not stability.** `depot_manifests` starts
   recording when *this vault* started watching, so on a vault running three
   days every game looks unchanged forever if that alone were trusted.
   `MIN_OBSERVATION_WINDOW_DAYS = 14` (same "give it two weeks" scale as the
   existing `VAULT_GC_GRACE_DAYS` default) must clear before `"stable"` or
   `"changed"` is reported at all — below it, `"insufficient_data"`, even
   with two-plus unchanged observations. Pinned by
   `test_pin_a_young_vault_reports_insufficient_data_not_stable` and the
   half-open boundary test right after it.

Both thresholds are hardcoded module constants in `depot_manifests.py`, not a
new `VAULT_*` setting — this work package adds no new environment variable,
matching `steam_relay.DEFAULT_CACHE_TTL_SECONDS`'s precedent for a fixed,
documented floor instead of an operator-facing knob.

**Scoping note:** both functions read only rows recorded under the target
app's OWN `appid` column — never a co-owning app's rows for a shared depot
(see "The reverse direction of ADR-0003" above). This keeps the feature from
having to solve the shared-depot merge problem ADR-0003/ADR-0007 already have
dedicated machinery for; a shared depot's change history is attributed to
whichever app's own prefill jobs actually ingested it.

**A poisoned `appid` is skipped, not raised.** `change_frequency_by_app`
groups rows by `appid` read straight out of the database — SQLite enforces
column *affinity*, not type, so a hand-edited/corrupted row can hold a
non-numeric string there, exactly the poison
`tests/test_gc.py::test_load_recorded_manifests_skips_poisoned_rows` already
seeds against this same table. The function uses the SAME
`deletion.coerce_positive_id` validator `gc.load_recorded_manifests` does for
this column, and drops an unusable row rather than crashing — a single
corrupted `depot_manifests` row must degrade the way it already did for GC
reporting, not take out the whole `GET /v1/games` listing for every app.
Pinned by `test_change_frequency_by_app_skips_a_poisoned_appid_row_instead_of_raising`
(unit) and `test_list_games_survives_a_poisoned_depot_manifests_appid_row`
(HTTP, `tests/test_games.py`).

**These fields outlive a cache deletion, unlike `size_bytes`.**
`DELETE /v1/cache/{appid}` never touches `depot_manifests` (see "Per-game
deletion" below) — the same precedent `last_manifest_check` already sets
(GameSummary, above): a `"stable"`/`"changed"` verdict describes the GAME's
upstream update history, not the current cache state, so it is not a false
claim for it to keep reporting `"stable"` (or a `manifest_days_since_last_
change` value) about an app with zero bytes on disk right now.

**Privacy note:** unlike `rtime_last_played`/`playtime_forever` above, this
field is NOT personal data — it describes the GAME's upstream update
history, not anything about an account or a person, so it carries none of the
WP 4h.1 relay addendum's privacy caveats.

### What this work package deliberately did NOT do

- No garbage collection (`3.6`/`3.7`) — this package only *records* manifest
  state; nothing deletes a chunk because of it.
- No `deploy/` changes — `VAULT_STEAMPREFILL_CACHE_DIR`'s container-side
  volume mount and `VAULT_MANIFEST_ARCHIVE_DIR`'s persistent-volume wiring are
  explicitly a follow-up on top of WP 1.9's Compose file, not this package's
  scope.
- ~~No HTTP endpoint reads `depot_manifests`~~ — true when this section was
  written (WP 3.2), **superseded by WP 4h.1** ("Change frequency" above):
  `GET /v1/games`/`GET /v1/games/{appid}` now read it, in aggregate, for the
  `manifest_change_frequency`/`manifest_observation_days` fields.

## Scheduler (WP 3.5)

Plan A7 ("prefill updates automatically during the day") and plan §7 Phase 3's
"configurable cron window (e.g. 09:00–17:00, every 3 h)". In one sentence:

> inside a configurable daytime window, every `VAULT_SCHEDULE_INTERVAL_MINUTES`,
> take the union of the app ids every gaming machine most recently reported as
> installed and enqueue a normal prefill job for each one.

**`config.py`'s own built-in default is disabled** — with `VAULT_SCHEDULE_WINDOW`
unset (the bare-metal/native dev path's own default, `Settings.from_env`),
no sweep is ever enqueued on vault-api's own initiative. That was the
*whole* story through WP SWEEP-1's own first draft: "a fresh install must
not start Steam logins and downloads because nobody has read the docs yet."

**The shipped Compose stack is no longer disabled by default.**
`deploy/compose.yaml` forwards `VAULT_SCHEDULE_WINDOW` with its own default
of `03:00-07:00` (WP SWEEP-1 follow-up, operator decision, ADR-0014
§"Shipping an enabled nightly schedule") — the argument above was correct
for a sweep mode that could only widen an installed-only sweep; it stopped
being the whole story once `sweep_include_cached`/`auto_gc` (below and
"Sweep target set") needed the scheduler actually running for their own
defaults to mean anything. See that ADR for the pairing argument, and
"Timezone" just below for what the window is actually measured against —
`03:00-07:00` opens at 3 AM **UTC** unless `TZ` is also set, which for most
operators is not 3 AM where they live. Opt out entirely with
`VAULT_SCHEDULE_WINDOW=` (blank, not unset — see the compose-substitution
note in `deploy/compose.yaml`'s own comment on this key for why blank and
unset are deliberately NOT the same thing for this one variable), or via
`PATCH /v1/settings`.

### Window semantics

`HH:MM-HH:MM`, zero-padded, 24-hour. Whitespace around the value and around
the `-` is tolerated; anything else is rejected at startup.

| Value | Meaning |
|---|---|
| `09:00-17:00` | 09:00:00 up to but **not including** 17:00:00, every day |
| `22:00-06:00` | **Overnight** — 22:00 through midnight *and* midnight through 06:00, i.e. one contiguous night, recurring |
| `18:00-00:00` | Evening until midnight (end earlier than start ⇒ read as overnight, the `[00:00, 00:00)` half being empty) |
| `00:00-24:00` | Always open. `24:00` is accepted **as the end value only**, meaning end-of-day |
| `09:00-09:00` | **Rejected** — ambiguous (zero minutes, or the whole day?). Use `00:00-24:00` |
| `9:00-17:00`, `0900-1700`, `09:60-…`, `25:00-…`, `24:00-…` as a *start* | **Rejected** |

Start inclusive, end exclusive; containment is decided on whole minutes, so
16:59:59 is inside `09:00-17:00`.

Overnight windows are supported deliberately rather than rejected: "prefill
while nobody is gaming" is the natural homelab window and it crosses midnight.
No calendar day is tracked — the window simply recurs.

### Timezone

The window is interpreted in **server-local time** — the machine's timezone,
or the container's `TZ` if you set one. That is the useful reading for
"09:00–17:00 while I'm at work". Every *timestamp* the scheduler stores or
reports (`last_sweep_at`, `next_eligible_at`) is UTC in the project's standard
`YYYY-MM-DDTHH:MM:SSZ` form, like every other timestamp in this database.
`GET /v1/schedule` reports the server's current offset as `server_timezone`
(e.g. `UTC+02:00`) so you can tell the two apart at a glance.

**`deploy/compose.yaml` forwards `TZ`, defaulting to `UTC` (WP SWEEP-1
follow-up).** Deliberately UTC and not a guessed populated zone — this
project ships to operators in every timezone, and a non-UTC guess would be
silently wrong for most of them; see that file's own comment on the `TZ`
key for the full argument. The real cost: the shipped `03:00-07:00` window
above does not land at 3 AM *local* for an operator who never sets this.

**A wrong or absent `TZ` is made visible, not silent.** The scheduler logs
one line on its first tick after every start naming the *requested* `TZ`
value (`os.environ.get("TZ")`) alongside the *resolved* zone abbreviation
and UTC offset, plus the next window opening in both local and UTC time —
`vault_api/scheduler.py`'s `describe_resolved_schedule`. This exists
specifically because a typo'd IANA zone name does not fail loudly: measured
in the shipped image, `TZ=Europe/Berlinn` (one extra letter) silently falls
back to a POSIX-style parse that resolves to a plausible-looking but wrong
zone (`Europe`, `UTC+00:00`) rather than raising an error — printing only
the resolved side would never have shown that anything was wrong. The line
is logged against `effective_settings` (the same `db > env > default`
resolution every sweep decision already uses, ADR-0009), not the boot
snapshot, specifically so a `PATCH /v1/settings`-stored window is reported
correctly instead of the line claiming the scheduler is disabled while an
unattended sweep is in fact about to run.

DST needs no handling: the tick loop re-reads the clock and re-evaluates the
window every minute, so a transition just shifts when the window opens and
closes in UTC. On the "spring forward" night an overnight window is one hour
shorter (one hour longer in autumn). The interval gate is UTC-based and
unaffected. The only value that can be an hour out across a transition is the
advisory `next_eligible_at`; nothing decides anything from it.

### The target set — installed *is* the prefill set

Plan A8. Every sweep recomputes the set from scratch:

1. for each `client_id` in `agent_reports`, take that client's **latest**
   snapshot (newest by `rowid`, i.e. insertion order — WP 2.4's rule, since
   second-precision timestamps tie);
2. drop clients that are **stale**, **unreadable**, or **undatable** (below);
3. union the remaining app id lists.

Intersected with nothing else in v1: no popularity heuristic, no size budget,
no include/exclude list. Because only the *latest* snapshot counts, a game
uninstalled on every machine simply stops being a target on the next sweep —
its cached content is untouched (ADR-0002: removals are surfaced, never acted
on; deleting stays a human/API decision).

**Staleness bound** (`VAULT_SCHEDULE_CLIENT_STALE_DAYS`, default 7 days). A
machine whose newest report is older than this is excluded. The target set is
meant to be "what is installed on the gaming machines *right now*"; a PC that
has been off for a week is not reporting anything right now, and continuing to
keep its last-known library current quietly burns bandwidth and disk on a
possibly decommissioned machine. Seven days is generous enough to survive a
holiday without the Steam Deck dropping out.

Two other exclusions, both fail-safe in the "prefill less" direction and both
reported in the log (with the client id and the reason):

- **unreadable snapshot** — that row's `appids` JSON is corrupt (the same
  degradation `POST /v1/agent/installed` already applies);
- **unreadable timestamp** — `reported_at` is not a parseable timestamp, which
  makes the staleness question unanswerable. Excluded rather than assumed
  fresh: never prefill on the strength of a value that could not be read.

### Sweep target set — installed PLUS cached (WP 4d; default-on since WP SWEEP-1)

Plan §7 Phase 4d. Through Phase 3, the section above was the *whole*
criterion: "intersected with nothing else". WP 4d adds exactly one more
source, and it is additive by construction — everything above this line is
completely unchanged when the setting is off, which is pinned by a test
asserting `compute_targets` returns a BYTE-IDENTICAL result with the mode
off, cache content or not. **This mode is ON by default since WP SWEEP-1
(ADR-0014, operator decision 2026-08-22) — it shipped OFF by default through
WP 4d**; see "Off by default through WP 4d — now on by default, paired with
auto-GC" below for the full argument for the flip.

**What it does.** `VAULT_SWEEP_INCLUDE_CACHED` / `sweep_include_cached`
(overridable via `PATCH /v1/settings`, ADR-0009) widens the target set to
every app that holds content of its OWN on disk right now (`scheduler.
cached_appids`), union `compute_targets`'s installed set. Dedupe is
automatic: both sets are plain `set[int]`s, so an app that is both installed
and cached appears exactly once, and the sweep enqueues it through the SAME
`jobs.enqueue_prefill` path as everything else — an app already
`queued`/`running` is absorbed by that function's own dedupe, never a second
mechanism.

**"Holds content of its own" is DELIBERATELY NARROWER than `GET /v1/games`'s
per-app `size_bytes` (B1, reviewer blocker, real-`DELETE`-rig measurement,
user decision "Plan A: narrow the definition").** `sizes.app_size_bytes`
correctly counts a SHARED depot's bytes into every co-owning app's total
(plan §4 — "how much would deleting this app free"), and mapping rows
deliberately survive a `DELETE` (ADR-0003). Combined, those two correct
facts produce a wrong answer for THIS question: an app whose only surviving
mapped depot is one it shares with another app that still has content
(ADR-0003's "shared and protected" outcome, never deleted) kept reporting as
"cached" forever under the generous definition — so a mode-ON sweep would
silently re-download a game the operator had just deleted. Measured against
the real endpoint:

```
DELETE /v1/cache/440 -> deleted depot 441, skipped_shared depot 300 (shared_with [730])
on disk after delete: [300]
generous definition:  cached_appids after delete -> {440, 730}   -- 440 WRONG
this mode's definition: cached_appids after delete -> {730}      -- correct
```

**WP 4f: `scheduler.cached_appids` and `POST /v1/prefill/cached`'s own
selection (see "Check & update all cached games" above) now share ONE
definition, `deletion.appids_with_cache_content`, instead of computing this
predicate twice.** Before WP 4f they disagreed — this section described an
`exclusive`-only rule while the route above used a separate, more generous
"any mapped depot with bytes" rule — and the disagreement was exactly the
gap named in the measurement above: a real, deployed pair of packages from
the same night answered "is 440 cached" differently depending on which
button was pressed. The shared function asks "does this app map at least one
depot that is either **exclusive** (no other tracked app maps it) or a
**last cached remnant** (ADR-0003 addendum: every OTHER co-owner is
verifiably uncached right now), and does that depot have bytes on disk" —
reusing `deletion.plan_deletion`, the exact pure function
`DELETE /v1/cache/{appid}` itself calls, rather than a second predicate that
could quietly drift from it. This is a **widening** from the WP 4d-only
`exclusive` rule: two apps that share ALL their depots with each other and
nothing else, with neither one otherwise recorded as having content, are
`remnant`-eligible for each other and are now correctly swept — under
`exclusive` alone they were invisible to this mode forever, since neither
ever becomes the sole owner of anything. `DeletionPlan.shared` (at least one
co-owner DOES currently have content, i.e. the B1 case above) is the one
outcome still excluded — the B1 measurement's outcome (`{730}`, not `{440,
730}`) is unchanged by this widening, and is pinned by name in
`tests/test_scheduler.py::test_cached_appids_excludes_an_app_whose_only_
surviving_content_is_shared`.

**The remaining conservative edge, stated on purpose:** an app whose cache
content lives ENTIRELY in depots shared with other tracked apps that DO
currently have content elsewhere is still never a target through this
predicate — `sizes.app_size_bytes` itself remains completely UNCHANGED by
any of this; its generous, double-counting-on-purpose definition remains
exactly correct for the sizing question it answers, and only the sweep's
(and the route's) shared notion of "holds cache content" is narrower than it.

**Why this is the exact fix for the problem it names (plan §7 Phase 4d):** a
game that sits in the cache but is currently installed nowhere — or whose
only client has gone quiet longer than `VAULT_SCHEDULE_CLIENT_STALE_DAYS` — is
invisible to the installed-only union above and therefore never refreshed.
The cached-apps source is computed **independently of client freshness
entirely**: it does not consult `agent_reports` at all, so a stale-excluded
client's cached games are swept anyway. `TargetSet.cached_only_appids` (and
the matching field on `SweepResult`) names exactly which apps were added
*only* because of this mode — i.e. not already reachable through any
included client — purely for the sweep's log line and for tests; the apps
themselves are already in `appids`, counted once.

**Cost model, stated plainly (why this mode is cheap enough to be the
default now, not just safe to turn on for an operator who opts in):** a
non-forced SteamPrefill run against an already-current app is
a ~3 s no-op that transfers zero bytes (ADR-0006 decision 1 — the same fact
Phase 4c's manual-check feature rests on). Real bandwidth is spent only on
apps that actually have an update. The filesystem side of the cost is one
extra `sizes.scan_depot_dir_bytes` walk of the whole `depot/` tree, PLUS
exactly **one** SQL statement regardless of library size
(`deletion.load_all_mapping_rows_with_owner_state`, WP 4f — see "Sweep target
set" cross-reference in "Check & update all cached games" above), per sweep —
not per tick — because it only runs inside an already-claimed sweep
(`VAULT_SCHEDULE_INTERVAL_MINUTES`, default 3 h).

**Query count, measured on a synthetic 300-app / 260-depot library (250
exclusively-owned apps + 10 clusters of 5 apps mutually sharing one depot
each — NOT the same fixture as WP 4d's original "301 statements / 143 ms"
number below, which used a different topology that was not preserved in the
repo; both are cited here for what each one actually shows, not as
before/after on identical inputs):**

| Shape | Statements | Wall time | Apps found |
|---|---|---|---|
| WP 4d (`exclusive`-only, one `load_mapping_rows` call per candidate app, PLUS the initial `SELECT DISTINCT appid` needed to build the candidate list) | 301 | ~6 ms | 250 (the 50 mutually-shared apps are invisible — the bug WP 4f fixes) |
| WP 4f (`appids_with_cache_content`, one bulk read) | **1** | ~2 ms | 300 (all 50 mutually-shared apps now correctly included as `remnant`) |

The query-count fix (301 → 1, matching WP 4d's own "301 statements" figure
exactly once the same initial candidate-list query is counted on both sides)
is real and library-size-independent. It is NOT the whole cost story, below.

**The reconstruction cost is genuinely quadratic in owners-per-depot, and
that number belongs in front of an operator, not buried as "negligible"
(S4, reviewer correction, 2026-08-18 review round — an earlier draft of this
section understated this).** Measured directly against
`deletion.appids_with_cache_content` (still exactly **one** SQL statement at
every size below — the cost is 100% in-memory Python, zero extra DB round
trips):

| Shape | Wall time |
|---|---|
| 300 apps, all sharing ONE depot | ~34 ms |
| 600 apps, all sharing ONE depot | ~145 ms |
| 1000 apps, all sharing ONE depot | ~363 ms |
| 2000 apps, all sharing ONE depot | ~1.4 s |
| 1000 apps, each mapping ALL of 10 independently-shared depots (1000 owners per depot) | **~3.6 s** |

A large, all-tracked-games redistributable depot (a shared runtime, a
language pack) is the realistic shape that produces owner counts in the
hundreds-to-low-thousands on a big library — not a contrived adversarial
input. **Where this actually lands matters more than the number itself:**
inside the background sweep (`scheduler.cached_appids`, this section) a few
seconds is genuinely negligible against the `VAULT_SCHEDULE_INTERVAL_MINUTES`
cadence (default 3 h). Inside **`POST /v1/prefill/cached`** (see "Check &
update all cached games" above) it is NOT free in the same way: that route's
documented contract is "returns `202` immediately, progress happens on the
worker" — but the *selection* step, including this reconstruction, runs
synchronously inside the request handler, before the `202` is sent. A
library with a large shared depot can therefore make that specific request
take seconds to respond, independent of and in addition to the filesystem
walk's own cost described below. Neither call site aborts or bounds this
work today; it is documented here as a known, measured cost, not (yet) a
mitigated one.

Three things about the filesystem-walk half of that cost worth stating
plainly rather than leaving to be discovered (reviewer should-fix S5, WP 4d):

1. **Order of magnitude.** Warm-cache (NVMe/SSD, OS page cache populated) is
   low-single-digit microseconds per file — a few hundred thousand chunk
   files walks in well under a second. Cold-cache on a spinning disk is a
   different story: every depot directory is a separate seek-bound
   `readdir`, so tens of seconds to low minutes is plausible on a large
   HDD-backed library that has not been walked recently. Either way it is
   bounded, read-only, and never triggers a download by itself.
2. **No coalescing with concurrent request-path scans.** This walk
   deliberately does NOT use the process-wide `SizeCache` (see "The TTL
   cache" above) — threading a request-scoped, TTL-bearing cache into a
   background thread that fires a few times a day was judged not worth the
   second dependency. Consequence: this walk and an unrelated
   `GET /v1/cache/summary` cache miss CAN walk the same `depot/` tree at the
   same time, doing the work twice. This is not a rare coincidence:
   `worker.py` calls `SizeCache.invalidate()` after every successful
   prefill, so a size-cache miss is common in exactly the window a sweep is
   also enqueueing work — the two are correlated, not independent events.
   **This interaction is not sweep-only (WP 4f):** `POST /v1/prefill/cached`
   above DOES read through the shared `SizeCache`, so the same correlated-miss
   window can make the ROUTE'S selection pay for a cold walk at the same
   moment a background sweep pays for its own uncached one — see "Check &
   update all cached games"'s own cost-model paragraph above, which
   cross-references this one instead of repeating it.
3. **Not interruptible mid-walk.** `PrefillScheduler.stop()`'s abort signal
   (`should_abort`) is only checked inside the PER-APP enqueue loop in
   `maybe_sweep`, never inside the depot walk itself. A shutdown that lands
   while this walk is in flight can make the scheduler thread take longer
   than `SHUTDOWN_JOIN_TIMEOUT_SECONDS` (30 s) to stop, logging the "did not
   stop within 30s, leaving it as a daemon thread" warning during e.g.
   `docker compose down`. Harmless (a read-only walk left to finish on its
   own; nothing is corrupted and no lock is held across the join), but it
   will read as a fault in the log if this isn't said up front.

**Off by default through WP 4d — now on by default, paired with auto-GC
(WP SWEEP-1, ADR-0014, operator decision 2026-08-22).** The cost that made
WP 4d ship this off is real and unchanged: the mode spends bandwidth and, on
real updates, disk on games nobody currently asked to have refreshed. That
cost was presented to the operator twice before this change, explicitly, and
the operator chose the new default anyway — see
`docs/adr/0014-sweep-cached-and-auto-gc-default-on.md` for the full argument,
including why pairing this with `VAULT_AUTO_GC` defaulting to `execute` in
the SAME change is what makes accepting the cost defensible rather than
reckless (shipping this on alone would be exactly the "keeps itself current
straight into a full disk" condition `cached_sweep_gc_risk` below exists to
warn about). An operator who wants the pre-2026-08-22 behavior back sets
`VAULT_SWEEP_INCLUDE_CACHED=false` (env or `PATCH /v1/settings`).

The ON/OFF split itself is unchanged and still pinned three ways: a test
that omits the `include_cached` keyword entirely and asserts nothing cached
leaks in (`compute_targets`'s own keyword default stays `False` on purpose,
for direct/test callers — see `scheduler.py`'s module docstring); a second
test that explicitly sets `sweep_include_cached=False` on `Settings` and
asserts the same through the full `maybe_sweep` call; a THIRD test that
relies on `Settings`' own dataclass default (now `True`) and asserts cache
content IS picked up; and `config.DEFAULT_SWEEP_INCLUDE_CACHED` itself,
mutation-killed by all three.

**The auto-GC coupling, stated honestly — this mode does not collect
garbage, it only refreshes it.** Every kept-current game this mode refreshes
adds fresh chunks for the new manifest while the *old* manifest's chunks
become orphans — that is what a game update is, on disk. A vault that keeps
itself current without collecting garbage keeps itself current straight into
a full disk. WP 4d does **not** silently enable auto-GC and does **not**
refuse to run when the coupling is unresolved — the operator decides (plan's
own words). Instead:

- `scheduler.cached_sweep_gc_risk(settings)` names the condition in exactly
  one place, so the warning below and the API field further down can never
  quietly disagree about what "at risk" means: `sweep_include_cached` on AND
  `VAULT_AUTO_GC` is **anything other than `execute`** (B2, user decision
  "nothing is being reclaimed", 2026-08-18 review round — see below for why
  `dry-run` counts).
- **`dry-run` does NOT clear the risk.** The condition this flag names is
  "orphans are accumulating, unreclaimed" — a fact about the disk, not about
  whether anyone is watching. `VAULT_AUTO_GC=dry-run` queues a REPORTING-only
  GC job that tells the operator exactly what could be freed and frees
  NOTHING (see "Auto-GC" above); a vault swept in `dry-run` mode grows at
  IDENTICAL speed to one with auto-GC fully off. Reporting the risk as
  cleared once `dry-run` is on would be a doc-and-API version of exactly the
  "a sentence telling an operator X works when it does not" class
  `docs/LEARNINGS.md` warns about. Only `execute` (`settings.
  auto_gc_executes`) actually reclaims, so only `execute` clears
  `sweep_cached_gc_risk` — **that field asserts "orphans created by refreshes
  are actually being reclaimed", not merely "someone configured GC".**
- `PrefillScheduler` logs a `WARNING` **once per transition into the risky
  state** — not once per tick, not once per process — and a matching `INFO`
  **once per transition back out of it** (reviewer nitpick N1: without the
  all-clear line, "why did that warning stop?" was unanswerable from the log
  alone, even though the underlying state was already tracked correctly in
  both directions). If the operator fixes it (sets `VAULT_AUTO_GC=execute`,
  or turns the cached mode back off) and the same risky combination
  reappears later via another `PATCH`, that is a fresh transition and gets a
  fresh warning. This is the same "state changes in both directions" shape
  WP 3.13's bypass-detection transitions already use.
- `GET /v1/schedule` exposes the SAME condition as `sweep_cached_gc_risk`
  (see below) so a UI can render a banner without re-deriving the interaction
  between two independent settings itself.
- Nothing is forced. `VAULT_AUTO_GC=execute` actually closes the loop; a
  `dry-run` step first to preview what would be reclaimed is recommended but
  does not, on its own, resolve this condition. `POST /v1/cache/{appid}/gc`
  by hand is the third option — see "Auto-GC" above.

### Spacing: why enqueue-everything *is* the rate limiting

ADR-0006's honest-limits section: each per-app check costs a Steam login
(~3 s), so sweeps are "spaced across the cron window, not batched" — batching
several apps into one SteamPrefill invocation would destroy per-app
attribution (one exit code, one summary table, N apps), so it is explicitly
not done.

The spacing mechanism is **the queue plus the single worker**, not a limiter
in the scheduler. There is exactly one worker thread running exactly one job
at a time (plan §3), so a sweep that enqueues 60 apps produces 60 *sequential*
runs, one Steam login at a time, and the next sweep cannot start until the
interval has elapsed. Adding a sleep between enqueues would only slow the
*queueing*, not the work, while breaking the queue's dedupe window and
delaying shutdown.

A sweep over an unchanged library is cheap for the same reason it is useful:
a non-forced run for an already-current app is a ~3 s no-op (ADR-0006 decision
1 — SteamPrefill's own up-to-date bookkeeping), and the non-forced run **is**
the staleness check. Apps that already have a `queued`/`running` job are
deduped by `jobs.enqueue_prefill` itself (WP 1.4) and reported separately from
the newly created ones.

`needs_force` is untouched by all of this: a scheduled run of an
already-filled app is non-forced, and a scheduled *first* fill of a
never-filled app still runs with `--force`, exactly as a button press would.
The flag belongs to the app, not to whoever enqueued the job (see
"needs_force lifecycle").

### Sweep bookkeeping and the crash-recovery rule

One row in `schedule_state` (schema v6), written **claim-then-work**:
`last_sweep_at` is stamped inside a `BEGIN IMMEDIATE` transaction *before* any
job is enqueued, and the two counters are filled in afterwards (they read
`NULL` in between, which is honest — "in flight or interrupted" — where a
stale count from the previous sweep would not be).

The rule that follows, stated plainly: **a restart does not re-sweep.**
`last_sweep_at` is in the database, not in memory, so a process that dies
mid-window — or an operator who edits `.env` and restarts three times — waits
out the remaining interval like any other sweep. Apps a crashed sweep never
reached are picked up by the next one; nothing is resumed, because the target
set is recomputed from scratch every time rather than being a work list.

Two related behaviours:

- **A sweep with zero targets still consumes the interval.** Otherwise an
  installation with no agents (or only stale ones) would sweep on every tick.
- **A `last_sweep_at` in the future** (the clock was stepped back, or a
  previous boot had a wrong clock) blocks sweeps until real time catches up.
  That is the deliberate direction to fail in: skipping is quiet and
  recoverable, whereas treating a future timestamp as "long ago" would sweep
  on every tick — a Steam login storm — until somebody noticed. An
  *unparseable* `last_sweep_at`, by contrast, is treated as "never swept" and
  logged: a corrupt value must not disable the scheduler permanently.

The thread ticks once a minute (an implementation constant, not a setting) and
sweeps only when the window and the interval both allow it. It ticks
immediately on start rather than waiting out the first tick, and a failing
tick is logged and retried — a bug there must never silently end scheduling.

### Why there is no lock against `DELETE /v1/cache/{appid}`

Because the scheduler is *just another client of the existing enqueue path*:
it calls `jobs.enqueue_prefill`, exactly like `POST /v1/prefill` does for a
button press (and like the Phase-3 miss trigger will). It never touches the
filesystem, never claims a job, never runs SteamPrefill. So it introduces no
interaction the already-reviewed WP 1.4/1.6/3.4 semantics do not cover:

| Situation | Already handled by |
|---|---|
| App already has a queued/running job | `enqueue_prefill`'s dedupe inside `BEGIN IMMEDIATE` — the existing job is returned and counted as "already active" |
| A `DELETE` is in flight for that app | `DELETE` refuses with `409` while a job is queued/running, and its own documented check-then-act window in the other direction has a stated, benign consequence (the job refills what was deleted) |
| A `DELETE` lands while a swept job is running | WP 3.4's compare-and-swap in `jobs.clear_needs_force_if_unchanged` — the deletion's `needs_force = 1` survives, so the next run is forced |

A lock would protect nothing that is not already protected, while adding a way
for a stuck sweep to block deletions. The one operator-visible consequence
worth knowing: during the window, a `DELETE` is more likely to hit the `409`
and need a retry once the job finishes.

### Threading and database access

A second daemon thread next to the job worker, started and stopped by the
FastAPI lifespan — started *after* the worker (a sweep is only useful if
something drains the queue) and stopped *before* it (stop producing before
stopping the consumer). It opens its own SQLite connection **inside its own
thread** and closes it there, per this project's one-thread-one-connection
rule (`vault_api/deps.py` documents the measured access violation that rule
exists for). It shares nothing with the worker but the database file; WAL plus
`busy_timeout` (`db.get_connection`) is what makes that safe.

### `GET /v1/schedule`

```jsonc
{
  "enabled": true,
  "window": "09:00-17:00",       // exactly as configured; null when disabled
  "overnight": false,
  "interval_minutes": 180,
  "client_stale_days": 7,
  "server_timezone": "UTC+02:00", // the window is LOCAL; timestamps are UTC
  "last_sweep_at": "2026-08-06T08:00:00Z",   // when it STARTED
  "last_sweep_targets": 12,       // null while a sweep is in flight
  "last_sweep_enqueued": 3,       // NEW jobs only (dedupe hits not counted)
  "next_eligible_at": "2026-08-06T11:00:00Z", // estimate; interval then window
  "sweep_include_cached": true,   // WP 4d — effective value, additive field. Default `true` since WP SWEEP-1 (ADR-0014)
  "sweep_cached_gc_risk": false   // WP 4d — cached mode on AND auto_gc != 'execute'. `false` here because auto_gc also defaults to 'execute' (paired, ADR-0014)
}
```

**This endpoint itself has no write verb — `PATCH /v1/settings` is the write
path.** That was true unconditionally through WP 3.5; since the settings-API
work package (ADR-0009) every field above except the four `last_sweep_*`/
`next_eligible_at` bookkeeping values is overridable at runtime via
`PATCH /v1/settings` (`window`/`interval_minutes`/`client_stale_days`/
`sweep_include_cached`, applying `next_sweep`) — see "Persisted settings"
below for the full precedence/validation story. `GET /v1/schedule` always
reports the EFFECTIVE, override-resolved configuration, never the raw env
snapshot.

When the scheduler is disabled the endpoint still reports the configured
interval and staleness bound, so an operator can see what enabling the window
*would* do.

### What this work package deliberately did NOT do

- No miss-triggered prefill completion (ADR-0001's hybrid half) — a separate
  work package.
- No garbage collection (`3.6`/`3.7`) and no third-party manifest oracle
  (`3.8`, ADR-0006 decision 4).
- No config-write API, no per-app schedule overrides, no "sweep now" endpoint.
- ~~No `deploy/` changes — the new `VAULT_SCHEDULE_*` variables are
  documented in `api/.env.example`; wiring them (and a `TZ`) into the
  Compose file is a follow-up on top of WP 1.9.~~ **Done.**
  `VAULT_SCHEDULE_WINDOW` (default `03:00-07:00`) and `TZ` (default `UTC`)
  are forwarded by `deploy/compose.yaml` and documented in
  `deploy/.env.example` as of WP SWEEP-1's follow-up round (ADR-0014
  §"Shipping an enabled nightly schedule", operator decision 2026-08-22).

## Cache-event sweep (WP 3.11, ADR-0008)

The consumer of vault-core's structured cache-event log (WP 3.10). It answers
the two questions the plan left hanging: **miss-triggered prefill completion**
(ADR-0001's hybrid decision, staged to "lands in Phase 3 together with the
scheduler/job infrastructure") and **per-client hit statistics + bypass
detection** (plan §5/§6). `vault_api/event_sweep.py`.

Off unless `VAULT_EVENT_LOG_PATH` is set. With no path there is no sweeping, no
table growth, no miss trigger, and `bypass_suspected` is `false` for everyone —
ADR-0008's "optional at runtime" boundary, matching the empty `VAULT_EVENT_LOG`
default vault-core itself ships.

### The line format, and what "strict" means here

Tab-separated, `escape=default`, exactly **9** fields, version-prefixed
(`core/README.md` "Cache-event log" is the normative definition):

| # | Field | Notes |
|---|-------|-------|
| 1 | `v1` | format version — must match **exactly** |
| 2 | `$time_iso8601` | normalized to the project's stored UTC format |
| 3 | `$remote_addr` | the correlation key |
| 4 | `HIT` / `MISS` / `BYPASS` | |
| 5 | depot id, or `-` | |
| 6 | `$uri` | decoded, query-free, bounded to 300 chars by nginx |
| 7 | `$bytes_sent` | |
| 8 | `$host` | validated, not stored |
| 9 | `$status` | **the served/not-served filter** |

Every field is validated before any of them is used, and a line that fails is
counted by category and skipped — never partially trusted, never crashed on:

- **not exactly 9 fields** → `field-count`. `escape=default` renders a
  percent-encoded tab or newline in a hostile request path as the literal text
  `\x09`/`\x0A`, so the only real tabs on a line are nginx's own separators —
  the guarantee is pinned empirically on the producing side
  (`core/tests/test-core.ps1`) and defended here anyway.
- **field 1 is not `v1`** → `unknown-version`, its own category on purpose. A
  future v2 with nine fields in a *different order* is exactly the line that
  must not be read as v1; it would otherwise produce wrong depot ids and wrong
  byte counts silently. Logged, counted in `lines_skipped_total`, never guessed.
- **depot id / bytes / status that are not plain ASCII digits** → refused.
  `docs/LEARNINGS.md`'s house rule: `int()` accepts `" 4 "`, `"+4"`, `"1_0"`
  (= **ten**) and non-ASCII digits, and these values feed SQL and an app-id
  lookup. `isascii() and isdigit()` closes all four. The depot id is
  additionally bounded to 10 digits — Steam depot ids are uint32, so a longer
  run is somebody probing `/depot/999…/`, and the bound also keeps the value
  inside SQLite's signed-64-bit INTEGER.
- **a malformed address, an unknown cache status, an over-long line** →
  refused. Lines are bounded at 8 KiB and one sweep reads at most 4 MiB, so a
  backlog is consumed across sweeps rather than loaded into memory at once.

An unparseable *timestamp* is the one field that does **not** discard the line:
it falls back to the sweep's own clock. Losing a client's whole request because
nginx's clock stamp was odd would be the worse trade.

**Field 9 is the reason hit statistics are honest.** `hits`/`misses`/`bypasses`
and `bytes_served` count **2xx responses only**. Without that filter a 403 from
the Host allowlist, a 404, or a 502 from a dead CDN edge would all count as
served traffic — and a MISS that 502'd would "prove" an app is not cached and
trigger a prefill when nothing was ever fetched. Non-2xx lines are counted in
their own `errors` column, so `requests = hits + misses + bypasses + errors`
still closes.

### The cursor contract

`event_sweep_state.cursor_offset` is a byte offset. Three rules make "each line
is read once, and a sweep failure re-reads instead of losing data" real:

1. **A line is never consumed without its trailing newline.** nginx buffers
   (`buffer=64k flush=5s`), so a read at EOF routinely lands mid-line. The
   cursor advances only to just past the last `\n` in what was read; the
   partial tail is re-read whole next time.

   **One case must be distinguished from that** (review finding S1): a *full*
   4 MiB read containing no newline at all is **not** a partial tail — no
   amount of waiting turns 4 MiB of newline-free bytes into a line this parser
   accepts, since `MAX_LINE_LENGTH` is 8 KiB. Treated as a tail it was a
   silent, **permanent** stall: every sweep re-read the same bytes, consumed
   nothing, statistics stopped, bypass detection went blind, rotation could
   never fire (the file was never fully swept), and the only signal was an
   INFO line that read like progress. The sweeper now steps over the region to
   the next newline, discards it, counts it in `oversized_skips_total`
   (`GET /v1/stats`) and logs a WARNING naming the offset. The residual case —
   an oversized region running to EOF with **no** newline anywhere — cannot be
   skipped without consuming an unterminated line, so the cursor holds and
   every tick logs a *stalled* WARNING instead of pretending to progress; it
   resolves by itself the moment a newline appears. Non-zero
   `oversized_skips_total` means something other than vault-core's event log is
   writing to that file, since nginx bounds the URI field to 300 characters.
2. **The cursor advances in the same transaction as the batch's effects** —
   one `BEGIN IMMEDIATE` covering the statistics upserts, the retention prunes
   and the new offset. There is no state where one is committed and the other
   is not.
3. **A file smaller than the cursor is treated as rotated** and the cursor
   resets to 0. This covers an operator, a `logrotate` with `copytruncate`, or
   a vault-core redeployed onto a fresh volume — without it, everything the new
   file accumulated below the old offset would be skipped forever.

### Idempotence, stated honestly

A crash anywhere before the commit means the whole batch is re-read. Per effect:

- **Statistics: exactly-once.** Rule 2 is the whole mechanism — a batch whose
  commit did not happen left no counters behind either. Pinned twice: once by
  killing the sweep *before* the commit, and once (the stronger pin) by failing
  *inside* the transaction after the counters are written, which a row-by-row
  commit design would fail.
- **Miss triggers: at-most-twice, and harmless.** Enqueues happen **before** the
  cursor commit, because a job that exists beats a job that was lost. A re-read
  is absorbed by two guards: the per-app cooldown row is committed immediately
  after the enqueue, and failing that, `jobs.enqueue_prefill`'s per-app dedupe
  returns the still-queued job. The one gap left: a crash where the enqueued job
  also *finished* before the next sweep makes the app eligible again and it gets
  a second non-forced prefill — ~3 s if it really is current (ADR-0006). That is
  the trade this ordering deliberately makes, not an oversight.

### Rotation — best-effort, and usually impossible in the shipped containers

ADR-0008 gives rotation to this sweeper and to nothing else. `maybe_truncate`
truncates to zero and resets the cursor only when the sweep succeeded, the file
is at least `VAULT_EVENT_LOG_MAX_BYTES`, **and** its size right now still equals
the committed cursor (i.e. every byte has been read).

Truncate-in-place is safe against nginx's own writer: nginx opens `access_log`
files with `O_APPEND`, so every write targets the file's *current* end-of-file
as tracked by the kernel. Truncating moves that end-of-file too, and the next
line lands at offset 0 — no gap, no sparse hole, no `USR1` reopen. That is why
ADR-0008 could choose "sweep, then truncate" over a rename dance.

**The one residual race, stated plainly.** Between the size check and the
`truncate` syscall nginx can flush a buffer, and those lines are destroyed. The
window is two adjacent syscalls, nginx flushes at most every 5 s or 64 KiB, and
truncation only happens past 64 MiB (hundreds of thousands of lines). It is
rare and bounded, it is not zero, and no portable "truncate-if-size-is-still-N"
primitive exists to make it zero. Set `VAULT_EVENT_LOG_MAX_BYTES=0` and rotate
externally if that is unacceptable — the shrink detection then picks the new
file up.

**In the shipped containers the sweeper cannot rotate at all, and that is
handled rather than assumed away.** vault-api runs as uid/gid `101:101`
(`api/Dockerfile`) while `/vault/logs` is vault-core's `nginx` user's `0755`
directory and the event log it creates there is `0644`. The sweeper can open it
for reading and **`os.truncate` raises `PermissionError`**. The design is
fail-soft:

- **Sweeping is unaffected.** Correctness is cursor-based and the cursor is
  already committed before truncation is attempted. Nothing is re-read, nothing
  is lost, statistics and the miss trigger keep working exactly as before.
- **The cost is unbounded file growth**, which must be *visible* rather than
  silently swallowed: every denial logs a WARNING naming the one-line fix and
  increments `event_sweep_state.truncate_denied_count`, which `GET /v1/stats`
  reports. An operator who never reads container logs still sees it climb.
- **The fix is a permission change on the vault-core side** — make
  `/vault/logs/event.log` writable by vault-api's uid (`chown 101:101`, or a
  shared group with `0664`). Wiring that into `deploy/` is a follow-up work
  package, not this one.

A native install (the WP 1.7 MVP setup, or a dev machine where both processes
run as the same user) hits none of this and truncates normally. Both directions
are pinned by tests: truncation happens when permitted, and `EPERM` keeps
sweeping correctly, records the denial and warns.

### Why the sweep ignores the schedule window (decision)

The sweep runs on WP 3.5's scheduler thread but on **its own interval**
(`VAULT_EVENT_SWEEP_INTERVAL_MINUTES`, default 5) and **unconditionally** — it
is not gated on `VAULT_SCHEDULE_WINDOW`. Three reasons, heaviest first:

1. **This sweeper owns the event log's rotation.** A window-gated sweeper would
   leave the file unread for the 16 hours a day the window is shut, so the
   feature would create the unbounded-growth problem it exists to own.
2. **Bypass detection must not have blind hours.** "This machine never appears
   in the cache log" is only trustworthy if the log is read around the clock;
   windowed, every evening gamer would look like a suspect at 22:00 and
   innocent again at 09:00.
3. **A sweep is cheap and bounded** — one bounded read plus a handful of small
   SQLite writes — unlike the prefill sweep the window exists for, which starts
   Steam logins and downloads.

Consequence worth naming: a miss-triggered prefill can therefore be enqueued
outside the window, and the worker will run it. That is consistent with
`POST /v1/prefill`, which has never been window-gated either — the window is
"when vault-api starts downloads *on its own initiative*", and a miss trigger is
a reaction to a human who is already downloading. The cap and cooldown, not the
window, are the storm control. The thread now starts when *either* feature is
configured (`PrefillScheduler.thread_needed`), and each rides the tick inside
its own `try`, so neither can silence the other.

### The miss trigger — four guards and one narrowing rule

A MISS line may enqueue a **non-forced** prefill (a miss says "something is not
on disk", which argues for filling, never for re-downloading what is). Guards,
cheapest and most decisive first:

1. **The per-sweep cap** (`VAULT_MISS_TRIGGER_MAX_PER_SWEEP`, default 5) — the
   storm backstop for many *different* apps at once (a LAN party where six
   machines each start a different game). Everything past it is reported by app
   id at WARNING; `docs/LEARNINGS.md` forbids silent caps. Dropped candidates
   are reconsidered on the next sweep — the cap delays, it does not discard.
2. **The queue's own per-app dedupe** (`jobs.active_job_for_app`,
   `ACTIVE_STATUSES` — which includes `paused` since WP 3.12, so a job an
   operator deliberately suspended is not quietly replaced). Its *unique*
   effect, beyond what `enqueue_prefill` already dedupes, is that such an app
   does not burn a scarce cap slot or start a cooldown.
3. **The per-app cooldown** (`VAULT_MISS_TRIGGER_COOLDOWN_MINUTES`, default 60).
4. **cached-and-current**: `apps.status = 'done'` **and** `needs_force = 0`
   **and** `last_manifest_check` no older than the cooldown window. The
   freshness bound is deliberately the cooldown rather than a fourth setting —
   "how recently must we have confirmed this app" and "how often may one app be
   re-triggered" are the same operator question asked twice. Anything unknown
   reads as **not** current: the cost of being wrong is one ~3 s non-forced run,
   while the opposite error leaves a half-cached game nobody completes.

Two narrowing rules on top, both counted-but-never-triggered:

- **Unmapped depots** — ADR-0008 verbatim: "no mapping = no honest target".
- **Depots mapped to more than one app** — the same rule from the other side. A
  shared depot (redistributables, plan §4) maps to every app that pulled it, so
  one chunk miss cannot say *which* game is downloading. Enqueueing for all of
  them would fan a single miss into N jobs; picking one would be a guess.
- **Manifest and patch misses** — ADR-0001 production finding 5: manifest URLs
  carry a per-request code, so they never URL-deduplicate and *every* manifest
  request is a structural MISS, forever, for every app however completely it is
  cached. Only `/depot/<id>/chunk/...` paths trigger; chunk URLs are content
  addressed, so a chunk miss really does mean "this byte range is not on disk".
  Manifest misses are still counted in the statistics and in `depot_miss_stats`.

**On by default when the sweep runs, and why.** The operator already made the
opt-in decision by pointing `VAULT_EVENT_LOG_PATH` at the log. Miss→prefill
completion is not a bonus bolted onto that — it is the *reason* ADR-0001 chose
hybrid miss handling. Behind a second switch defaulted to off, a decided
architecture would quietly never run anywhere. `VAULT_MISS_TRIGGER_COOLDOWN_MINUTES=0`
is the explicit off switch for operators who want statistics without enqueues;
`0` means **OFF**, not "no cooldown", because "no cooldown" is precisely the
storm shape the setting exists to prevent and is therefore not selectable.

### Bypass detection — fail toward NOT accusing

`GET /v1/clients` gains `cache_hits`, `cache_misses`, `bytes_served`,
`last_seen_in_cache_log`, `source_addrs` and `bypass_suspected`. The event log
knows **addresses**; agent reports know **client_ids**; schema v9's
`agent_reports.source_addr` is the only bridge, and a client's statistics are
summed over **every** address its retained reports arrived from (a laptop that
moved from wifi to cable still has traffic under the old address). The peer
address is taken from the TCP peer, **never** from `X-Forwarded-For` — trusting
a client-settable header for an identity key would let any agent claim another
machine's traffic, or disclaim its own to dodge detection.

A false positive sends an operator hunting a network fault that does not exist,
so every unknown reads as "not suspected". A client is flagged only after
surviving all six disqualifications:

1. the event feed is off — there is no cache log to be absent from;
2. no sweep has ever completed, or the feed is younger than
   `VAULT_BYPASS_WINDOW_DAYS` — "we have not been watching long enough" is not
   evidence about the client;
3. the client's own newest report is older than the window — a machine that has
   been off cannot be bypassing, and would otherwise be accused forever;
4. it reports no installed games, or its snapshot was unreadable;
5. no retained report recorded a source address — **including every pre-v9
   row**, so an upgraded installation does not light up with accusations
   against every machine at once;
6. it *has* appeared in the cache log within the window.

Each of the six is separately mutation-tested: flip it and a named test in
`tests/test_bypass_detection.py` dies. `docs/LEARNINGS.md` is explicit that
fail-safe defaults need tests pinning the DEFAULT direction, not just the happy
path.

The default window is 3 days rather than 1 because of ADR-0001's production
requirement 7: Steam LAN peer-to-peer transfers can legitimately replace cache
traffic, so one quiet day proves nothing.

**Retention caveat, stated in the response's own field docs:** `cache_hits` and
`bytes_served` are sums over the *retained* windows
(`VAULT_CLIENT_STATS_KEEP`), so they are not lifetime counters and can go down.

### What this work package deliberately did NOT do

- **No `deploy/` changes.** The new variables are documented in
  `api/.env.example`; mounting the event log into the vault-api container (and
  granting the write access rotation needs) is a parallel/follow-up package.
- **No `core/` changes.** The producing side shipped in WP 3.10 and its format
  is consumed exactly as documented.
- No third-party manifest oracle (WP 3.9) — webhooks (WP 3.13) shipped
  separately, see "Webhooks" below.
- No config-write API and no "sweep now" endpoint — same reasoning as
  `GET /v1/schedule`'s read-only stance.
- No content attribution from the event log. ADR-0008's boundary holds: the
  depot id is used to *look up* the mapping vault-api already owns, never to
  build it. Chunk→game attribution stays with manifests (ADR-0006/0007).

## Webhooks (WP 3.13)

`vault_api/webhooks.py` can POST a small, generic JSON notification to one
operator-supplied URL when a job concludes or a client newly looks like it is
bypassing the cache. Deliberately generic — there is no Discord/Slack/ntfy
embed builder here, one stable schema for every receiver, and a receiver that
wants a platform-specific look adapts on its own side (most chat webhook
endpoints, and every ntfy/Slack/Discord-compatible relay, already ingest
generic JSON).

**The one switch is `VAULT_WEBHOOK_URL`.** Blank/unset — the default — means
the whole feature is off: no background thread, no queue, no HTTP calls, and
none of the code paths below even build a payload.

### The envelope

Every event is the same shape; only `payload` varies:

```json
{
  "event": "job.done",
  "timestamp": "2026-08-09T14:03:11Z",
  "vault_name": "homelab",
  "payload": { "id": 42, "type": "prefill", "appid": 440, "status": "done" }
}
```

`timestamp` is when the notification was generated (not necessarily the
job's own `finished_at`, which is already in the row `GET /v1/jobs/{id}`
returns). `vault_name` is `VAULT_NAME` and is **omitted entirely** — not sent
as `""` — when that variable is unset; it exists purely so an operator
running more than one SteamHangar instance behind the same receiver can tell
notifications apart.

### The five events

| Event | Fires when | `payload` fields |
|---|---|---|
| `job.done` | A prefill or GC job finished successfully | `id`, `type`, `appid`, `status`, `mode` (GC only: `"execute"`/`"dry-run"`), `bytes` (GC only: `bytes_freed`, 0 for a dry run) |
| `job.error` | A prefill or GC job finished as `error` (incl. an unrecognised job type, and a crash inside the worker) | same shape as `job.done` |
| `job.cancelled` | An operator cancelled a job (`DELETE /v1/jobs/{id}`) — **not** sent for `paused`, which is not a conclusion | same shape as `job.done`, no `bytes` (a stopped run's byte count is not meaningful — see `worker._finish_stopped_prefill`) |
| `client.bypass_suspected` | A client **newly** flips to `bypass_suspected` in the sense `GET /v1/clients` already reports (see "Cache-event sweep" above) | `client_id`, `address` (every address the client's retained agent reports arrived from), `last_seen` (its most recent cache-log timestamp, or `null`) |
| `client.bypass_resolved` | The all-clear: a previously-`bypass_suspected` client's cache-log presence returned | same shape as `client.bypass_suspected` — `last_seen` is now a real timestamp (its return to the cache log is exactly what caused the flip back) |

`client.bypass_suspected` and `client.bypass_resolved` are the two directions
of the SAME verdict flip, never the steady state in either direction — see
"Bypass transitions, not the steady state" below. Together they close the
loop for an operator: get paged when a machine looks like it started
bypassing the cache, get paged again when it stopped.

Example `client.bypass_suspected` payload:

```json
{
  "event": "client.bypass_suspected",
  "timestamp": "2026-08-09T09:00:00Z",
  "payload": {
    "client_id": "steam-deck-01",
    "address": ["192.168.1.55"],
    "last_seen": null
  }
}
```

Example `client.bypass_resolved` payload (same shape, `last_seen` now populated):

```json
{
  "event": "client.bypass_resolved",
  "timestamp": "2026-08-09T15:00:00Z",
  "payload": {
    "client_id": "steam-deck-01",
    "address": ["192.168.1.55"],
    "last_seen": "2026-08-09T14:58:03Z"
  }
}
```

**`bytes` for a prefill job is never included.** SteamPrefill's own summary
table reports a formatted string ("12.3 GB"), not a raw byte count, and
parsing it back into an integer just for a webhook would not be the "cheaply
available" value the work package asked for — it is omitted rather than
guessed at.

### Configuration

`VAULT_WEBHOOK_EVENTS` is a comma list (default: all five); a name outside
the table above, or an empty entry (a stray comma), fails loudly at startup —
the same house rule every other list/enum setting in this project follows.
`VAULT_WEBHOOK_TIMEOUT_SECONDS` (default `5`, must be `> 0`) bounds one HTTP
attempt; see "Delivery semantics" for how attempts and timeouts interact.

### Hook points — exactly one call site per event class

**Job events** all go through `webhooks.finish_job_and_notify`, called
immediately after every place a job concludes: `worker.py`'s prefill
success/failure/unowned/exception/cancelled branches, its unknown-job-type
guard, and `gc_execute.run_gc_job`'s crash and normal-completion paths.
Deliberately **not** wired inside `jobs.finish_job` itself — that function is
called directly, with no `Settings`/notifier in scope, by most of
`tests/test_job_control.py` and friends, describing plenty of transitions
(crash-recovery at startup, a synthetic test state) that were never real
webhook-worthy events. Routing the hook through the callers, where a genuine
worker/GC run is actually in progress, keeps "a job really just concluded" as
the only thing that can fire it.

**Bypass events** (both directions) go through
`event_sweep.check_bypass_transitions`, called from `event_sweep.sweep_once`
strictly *after* `commit_batch`/`maybe_truncate`. Both hook points share the
same rule: a webhook fires only after the state it describes is already
committed, never a state that could still roll back.

### Bypass transitions, not the steady state

A client that has been `bypass_suspected` for a week must not re-fire the
webhook on every 5-minute sweep — and once it clears, it must not re-fire
`client.bypass_resolved` on every sweep after that either. Schema v10 adds
`client_bypass_state` — one row per `client_id` holding the LAST computed
verdict — so `check_bypass_transitions` can tell "just became suspected" and
"just cleared" apart from "no change" and only notify on the two flips, never
on either steady state. The verdict itself (`event_sweep.bypass_suspected`)
is the **same function** `GET /v1/clients` answers with (moved there from
`routers/clients.py` in this work package), so the sweep's opinion and the
API's opinion can never disagree.

Both directions are always computed and persisted together once the checker
runs at all — asking for only one of the two events in
`VAULT_WEBHOOK_EVENTS` filters which webhook actually gets sent (in
`WebhookNotifier.enqueue`), not which transition gets tracked, so enabling
the missing event later produces correct transitions from that point on
rather than a false first-sight event for every already-suspected client.
The table is only ever written when `VAULT_WEBHOOK_URL` is set and at least
one of `client.bypass_suspected`/`client.bypass_resolved` is a configured
event — an installation that never asked for either webhook never gets a row
in it.

A full cycle, id by id: a client goes quiet (feed old enough, client still
reporting, no cache-log presence) → `client.bypass_suspected` fires once →
the client stays quiet for days, no further webhook → traffic resumes →
`client.bypass_resolved` fires once → silence again until the next
`bypass_suspected`, if any.

### Delivery semantics: at-most-once, bounded retry, drops counted

Every hook point above calls `WebhookNotifier.enqueue`, which never blocks
and never raises: it JSON-encodes the payload and does a
`queue.Queue.put_nowait` onto a bounded queue (`MAX_QUEUE_SIZE = 100`). Actual
HTTP delivery happens on exactly one dedicated background thread, so a slow
or hanging receiver can only ever delay *itself* — never the worker
finishing a job or the scheduler sweeping the event log. That single-thread
design is also why delivery is **serialized**: two events are never in
flight at once, which is what keeps a receiver from being hit by concurrent
requests from one vault-api process.

- **At-most-once.** There is no persistent outbox; an event that was queued
  when the process died is lost, and a delivery that fails after every retry
  is simply dropped (logged, never re-queued days later). If you need
  guaranteed delivery, front the URL with a message queue or a webhook relay
  that persists on its own side.
- **Bounded retry.** Up to `DELIVERY_ATTEMPTS = 3` HTTP attempts per event
  with a short backoff between them (`RETRY_BACKOFF_SECONDS = (0.2, 0.5)`).
  Giving up after the last attempt is one WARNING naming the event and the
  reason — **never** a full traceback at ERROR for what is usually just "the
  receiver is down right now", an operational fact about the other end, not
  a bug in this code.
- **Redirects are never followed; a 3xx is a failed delivery.** The
  receiver must answer the configured URL directly with a 2xx. A `3xx`
  counts as a failed attempt (retried, then dropped with the usual WARNING)
  and its `Location` is never contacted, so a Basic-Auth header can never be
  handed to a different host. A receiver that redirects (e.g. `http://` ->
  `https://`, or a trailing-slash redirect) must be configured with its final
  URL.
- **Drops are counted, never silent.** A full queue means delivery is
  falling behind the rate events are produced; `enqueue` drops the OLDEST
  queued event (not the newest) to make room, logs it at WARNING, and counts
  it (`WebhookNotifier.dropped_count`) — the same "no silent caps" rule
  `docs/LEARNINGS.md` already applies to the miss trigger's per-sweep cap.
- **Basic Auth in the URL works, and is redacted from logs.** If
  `VAULT_WEBHOOK_URL` carries userinfo (`https://user:secret@host/path`), it
  is converted into a real `Authorization: Basic` header before delivery
  (`urllib.request` does not do this on its own — a userinfo-carrying URL
  handed to it directly fails to even resolve, measured), and every LOG line
  still renders the URL as `https://***@host/path` (`webhooks.redact_url`).
  The header conversion is about making the convenient syntax actually work;
  the redaction is about not leaking the credential into `docker logs` — see
  "SSRF / trust posture" below.

### SSRF / trust posture

`VAULT_WEBHOOK_URL` is operator-supplied configuration, exactly like
`VAULT_STEAMPREFILL_PATH` or `VAULT_CACHE_ROOT` — this project's threat model
(plan §9, "Auth" below) is a single homelab operator running their own
trusted stack, not a multi-tenant service accepting attacker-influenced
URLs. There is deliberately **no allowlist, no DNS-rebinding defense, no
blocking of loopback/RFC1918 targets**: the operator who sets this value is
the same person who can already edit `.env` or run arbitrary code in the
container, so treating their own configuration as untrusted input would be
security theatre rather than a real mitigation.

### What this work package deliberately did NOT do

- **No `deploy/` changes.** `VAULT_WEBHOOK_URL`/`VAULT_WEBHOOK_EVENTS`/
  `VAULT_WEBHOOK_TIMEOUT_SECONDS`/`VAULT_NAME` were documented only in
  `api/.env.example` by this package. Since then all four are wired through
  `deploy/compose.yaml`'s `vault-api` environment and documented in
  `deploy/.env.example` (pre-freeze review S3).
- **No persistent delivery queue / outbox.** At-most-once, in-process only —
  see "Delivery semantics" above.
- **No vendor-specific templates.** One schema; a receiver that wants a
  Discord/Slack/ntfy-native look adapts it on its own side.
- No UI for configuring or previewing webhooks (Phase 4 territory).

## Garbage-collection core (WP 3.7, ADR-0007)

Plan A13 ("reclaim space from outdated chunks per game") and §5's "stale
chunks waste space forever". `vault_api/gc.py` is the *deciding* half of that
feature; WP 3.8 is the *acting* half.

> For one depot, the chunks worth keeping are the UNION of the current
> manifests of every app that has a claim on that depot; everything else in
> that depot's `chunk/` directory is an orphan left behind by a game update.

**This module mutates nothing.** No file is written, renamed or removed, no
row is inserted or updated. `plan_gc` returns a `GcPlan` — a dry run that can
be inspected in a response, a test or a review before anything acts on it.
`tests/test_gc.py::test_plan_gc_is_read_only` snapshots every path and size
under the cache root and the archive, plans, and compares.

### Where a keep set comes from

Per (app, depot), first success wins:

| # | Source | What it gives |
|---|---|---|
| 1 | `depot_manifests` row (WP 3.2) | the manifest **id** vault-api believes is current — not a chunk set |
| 1a | archived `.bin` — `{archive_dir}/{depotid}_{manifestid}.bin` | that manifest's chunks (`manifests.parse_bin_payload`) |
| 1b | cache-stored copy — `depot/<id>/manifest/<manifestid>/5/<code>` | the same chunks from the other on-disk format (`parse_cache_manifest`) |
| 2 | no row at all → the depot's **newest** cache-stored manifest | chunks, from the only evidence available |

**GC never falls back from a newer manifest it cannot read to an older one it
can.** If a recorded manifest id is known but neither source yields its chunk
set, the app is unresolved and the depot is skipped; if the newest stored
manifest cannot be parsed, an older one is *not* substituted. Keeping an old
manifest's chunk set would plan the deletion of exactly the chunks the current
version needs — the one failure mode a garbage collector must not have.
Multiple stored copies *of the same manifest id* are tried in turn (a
truncated store-on-miss write is real), which is a different thing entirely.

"Newest" is decided by mtime. `proxy_store` stamps stored files with the
upstream `Last-Modified` (ADR-0007's measured reason for rejecting time-based
GC), so these are Steam's publish times — which for *ordering manifests of one
depot* is the right signal, not the trap it is for chunks. It is still only
used where vault-api has no recorded manifest id of its own.

**On top of that union, WP 3.9 can add `extra_manifest_ids`** (ADR-0007's beta
addendum, decision B): manifest ids that are no app's *current* manifest but
whose chunks must survive anyway — today only the opt-in oracle's open
beta-branch gids. They are unioned in **after** the readiness gate has already
passed, they never gate a depot themselves, and they go through the same
`valid_manifest_id` guard as everything else that becomes a path here. With
the oracle off (the default) the list is empty and this step does not exist.
See "Manifest oracle" below.

### The uncached-app decision (the one this package had to reconcile)

Two accepted documents pulled in opposite directions for one case: a depot
mapped to an app with **no cache content and no recorded manifest**.

- ADR-0007's readiness gate: "if any mapped app has no resolvable manifest,
  the depot is skipped — never GC on partial knowledge."
- ADR-0003's addendum (WP 3.6): a co-owner without cache content does **not**
  protect bytes, because mapping rows survive deletion and a rule keyed on
  them alone leaks a shared depot forever. Its closing line hands the question
  to this package: *"for consistency, an uncached mapped app should not pin
  chunks in a shared depot's keep set; decide when GC is built."*

**Decision:** an app that is verifiably idle, never prefilled, job-free **and**
has no `depot_manifests` row is *excluded from the union requirement* — it
neither contributes chunks nor blocks the depot.

1. The gate protects against **partial** knowledge — an app we know has bytes
   on disk but whose manifest we cannot read. An uncached app has, by the same
   conservative predicate WP 3.6 already uses, nothing on disk to protect:
   that is complete knowledge of an empty claim, not partial knowledge.
2. Treating it as a blocker would reproduce the WP 3.6 leak in a new place —
   one never-prefilled app mapped to a shared depot would freeze GC for that
   depot forever, with no operator action available to fix it (mapping rows
   survive by design).
3. The direction stays fail-closed: a missing `apps` row, a non-idle status,
   a set `last_prefill_at`, a queued/running job or an unreadable row all
   resolve to "has content" ⇒ the app counts ⇒ an unresolvable manifest skips
   the depot. Only the verifiably-empty case is excluded.
4. Symmetrically, an app that **does** have a `depot_manifests` row always
   counts even without cache content: a recorded manifest is a claim vault-api
   itself wrote down, and an unreadable claim is exactly what the gate is for.

**Sub-decision:** a depot where *every* mapped app is excluded is **skipped**
(`skipped_no_counting_apps`), not emptied. The exclusion rule exists so an
uncached co-owner cannot *block* a depot that has other resolvable claims; it
is not an authorisation to wipe a depot nobody has knowledge about. That case
already belongs to `DELETE /v1/cache/{appid}`'s last-remnant rule (ADR-0003
addendum), which rechecks co-owners at execute time and sets `needs_force` —
neither of which GC has.

### Depot statuses

| Status | Meaning |
|---|---|
| `planned` | keep set resolved, orphans computed — **the only status that may carry orphans** |
| `skipped_unusable_depotid` | the mapping row's depot id is not a positive integer (poisoned DB) |
| `skipped_missing_dir` | `depot/<id>/` does not exist — nothing cached, nothing to reclaim |
| `skipped_unmapped` | no usable `depot_app_map` row names this depot |
| `skipped_unreadable_owner` | a mapping row has an unreadable app id ⇒ the claim set is incomplete |
| `skipped_no_counting_apps` | every mapped app is verifiably uncached with no recorded manifest |
| `skipped_no_manifest` | ADR-0007's readiness gate: a counting app's manifest could not be resolved |
| `skipped_link_like_dir` | `depot/<id>/` is a symlink or junction — GC never descends through a link (threat model §9, S-2) |

### Guarantees (each pinned by a named test, each mutation-tested)

- `DepotGcPlan.orphan_chunks` is non-empty **only** under `planned`. WP 3.8
  deleting "whatever the plan says" therefore deletes nothing on a skip.
- Only files directly inside `depot/<id>/chunk/` are ever orphan candidates.
  The `manifest/` subtree is structurally out of reach — a GC that walked the
  whole depot directory would classify every stored manifest as an orphan,
  since their filenames are per-request codes, not chunk ids.
- An orphan candidate's filename is exactly 40 lowercase hex characters.
  Anything else in `chunk/` (a subdirectory, a link, an uppercase or truncated
  name, a temp file) is reported as *unrecognised* and never planned. This is
  also what makes every id in `orphan_chunks` safe to join onto a path: WP 3.8
  needs no further validation of it.
- Every id that becomes a path is validated first — depot ids through the
  reviewed WP 1.6 guards (`deletion.coerce_positive_id` / `depot_dir_path`,
  reused rather than re-implemented), manifest ids through
  `gc.valid_manifest_id` (ASCII decimals only, so a poisoned
  `depot_manifests.manifestid` such as `'../../etc'` never becomes a path
  component).
- `orphan_bytes` / `kept_bytes` are the **exact on-disk** sizes
  (`DirEntry.stat().st_size`), not the manifest's declared `cb_compressed`. A
  kept chunk whose two sizes disagree is counted in `size_mismatch_count` — a
  free corruption signal (research §2 proved them byte-exact against ~12,000
  real files) that deliberately does not change what is planned.
- Set-based throughout: one `scandir` per `chunk/` and per `manifest/` subtree,
  at most one parse per distinct manifest file per run (memoised), and a
  dict/set difference for the orphans — no quadratic join at the real sizes
  involved (72283 chunks in the largest manifest seen in Phase-3 research).

### Duplicate stored manifests

Manifest URLs carry a per-request code, so the same manifest legitimately
lands on disk several times (research §2 observed 3×). `dedupe_candidates`
reports, per `(depot, manifest id)`, the newest copy as `keep` and the rest as
`duplicates` with their sizes. These are reported for **every** depot,
including skipped ones and independently of the keep set: a second copy of one
manifest is redundant no matter which manifest is current. Byte-identity is
**not** verified here — the sizes are carried precisely so WP 3.8 can decide
how much it wants to check before removing anything.

### What `plan_gc` deliberately does NOT do

- **Delete anything.** No chunk, no manifest, no directory, no database row.
  WP 3.8 owns execution, through the WP 1.6 guard path.
- No HTTP endpoint, no job type, no worker wiring, no `POST /v1/cache/{appid}/gc`
  — all WP 3.8.
- No `needs_force` / status / size-cache bookkeeping. Reclaiming bytes changes
  what is on disk for an app, so that bookkeeping is real work — it belongs
  with the code that actually changes the disk.
- No deletion of *old* manifests, only of redundant copies of the same one.
  Nothing in ADR-0007 asks for superseded manifests to go, and they are the
  cheapest possible evidence of what a depot used to hold.
- No unmapped-depot GC: `plan_gc` is per app and only considers depots that
  app maps. Depots on disk with no mapping row at all are surfaced by
  `GET /v1/cache/summary` (WP 1.5) and are not this module's business.
- No third-party manifest oracle (ADR-0006 decision 4, gated) — the sources
  are vault-api's own record, its own archive, and the cache tree.

## Garbage collection — `POST /v1/cache/{appid}/gc` (WP 3.8, ADR-0007)

The acting half of the feature whose deciding half is documented above.
`vault_api/gc.py` produces the plan and mutates nothing; `vault_api/gc_execute.py`
carries it out; this endpoint only queues the job.

```
POST /v1/cache/440/gc                      -> dry run  (deletes NOTHING)
POST /v1/cache/440/gc  {"execute": false}  -> dry run
POST /v1/cache/440/gc  {"execute": true}   -> deletes
```

Response (`202`), which never leaves the mode in doubt:

```json
{"appid":440,"job_id":7,"status":"queued","type":"gc",
 "mode":"dry-run","execute":false,"deduplicated":false}
```

`404` for an unknown app or an app with no depot mappings (same reasoning and
wording as `DELETE /v1/cache/{appid}`); `422` for `appid < 1`, for an unknown
body field, and for anything but a literal JSON boolean in `execute`.

### Dry run is the default, in three independent places

1. **The request.** `execute` defaults to `false`, and there is no query-string
   or header alternative. It is a `StrictBool`, so `"true"`, `"yes"`, `"on"` and
   `1` are all `422` — pydantic's lax mode would have accepted every one of
   them (docs/LEARNINGS.md records the int-field version of that coercion), and
   the one flag that turns a report into a deletion is not a place for a
   generous parser. `extra="forbid"` makes `{"exceute": true}` a `422` rather
   than a silently ignored typo.
2. **The job row.** The mode is stored in `jobs.gc_execute` (**schema v7**), not
   held in the request thread — the worker may pick the job up minutes later, or
   after a restart. `NULL` (every prefill job, and any GC row written before
   v7) reads as *dry run*: `jobs.job_deletes` resolves a missing mode to the
   non-destructive one, never the other way round.
3. **The executor.** `gc_execute.run_gc` deletes only when handed
   `execute=True`, a required keyword argument with no default.

### What an executing run does — and what it never touches

| | |
|---|---|
| Deletes | orphaned chunk **files** in `depot/<id>/chunk/` of a `planned` depot |
| Deletes | redundant **copies** of a stored manifest (same `(depot, manifest id)`), keeping the newest — and only when the duplicate is byte-identical to it |
| Never touches | a depot whose plan status is not `planned` — not its chunks, not its duplicate manifests, nothing |
| Never touches | any chunk the plan did not name; any name that is not exactly 40 lowercase hex characters; anything outside `depot/<id>/chunk/` |
| Never touches | a symlink or a Windows junction — it is refused, never unlinked (the plan named a *file*, and a name that points elsewhere is not that file) |
| Never touches | the `manifest/` subtree beyond the duplicate copies above, the archive, other depots, or the depot directory itself |
| Never touches | a chunk stored within the last `VAULT_GC_GRACE_DAYS` days, or one whose store time cannot be read (WP 3.8b — see "The recently-stored grace window") |
| Never touches | `apps.status`, `apps.last_prefill_at`, mapping rows, `depot_manifests` |

Both the "only a `planned` depot" and the "only a 40-hex name the planner
produced" rules are enforced *again* in the executor rather than trusted from
the planner: they are the two guarantees that keep a future change to `gc.py`
from becoming a deletion bug, so they live with the code that deletes.

### The plan is built when the job runs

The endpoint stores an app id and a mode. **No plan is computed at enqueue
time, and `run_gc` has no parameter through which one could be passed in** — it
loads the database inputs and scans the cache tree itself, every run. A game
that updates while the job waits in the queue is therefore collected against
its *new* manifest, and a chunk that was an orphan when the request was made
but belongs to the current manifest by the time the job runs is kept.

The residual window is plan → unlink, inside one job:

- A prefill can never be in it: GC shares the single worker queue, so a
  download and a collection of the same depot cannot overlap. (That is the
  hazard `DELETE /v1/cache/{appid}` has to refuse with a `409`; here it is
  structural, which is why the GC endpoint has no such guard.)
- A `PUT /v1/mapping/{depotid}` or a client fetch can be. Consequence: a
  re-download, never corruption — ADR-0007's accepted limit.

`DELETE /v1/cache/{appid}` *does* run concurrently (it works in the request
thread), so every removal goes through the WP 1.6 settle-and-recheck path
(`deletion.remove_file_settling`): the outcome is decided by the settled
filesystem state, not by the exception, because a racing removal surfaces as
`FileNotFoundError` **or** as `PermissionError [WinError 5]` on Windows
("delete pending"), and `lexists` has the last word. A file that turned out
gone but was not removed by this run contributes **0 bytes**, so two racing
actors never both claim the same reclaimed space.

### Job outcome: exact, or `error`

> An executing GC job is `done` only when **every chunk it planned to delete is
> gone** — removed by this run, or found already removed.

Anything else is `error`, with the exact counts in the log either way. Bytes
are read by an `lstat` taken immediately before each unlink, never taken from
the plan, so `bytes_freed` is what was really freed. A dry run is `done`
whenever the plan could be built at all; a depot *skip* is a reported,
deliberate outcome (the readiness gate doing its job), not a failure. A run
that could not even build a plan (an unusable `VAULT_CACHE_ROOT`) is `error`
and says that nothing was planned, inspected or deleted.

The job log puts per-depot lines first and the totals **last**, because
`jobs.finish_job` keeps the last 4 KiB — a run over many depots must not have
its summary truncated away.

```
[vault-api] GC for app 440: EXECUTE.
  depot 441 planned: orphans=2 (700 bytes) -> removed=2 (700 bytes) already_gone=0 ...
  depot 900 skipped_no_manifest: ADR-0007 readiness gate: no current manifest ...
[vault-api] GC totals (EXECUTED): chunks_removed=2 bytes_freed=700 already_gone=0
  dedupe_removed=2 dedupe_bytes_freed=1234 total_bytes_freed=1934 problems=0
  declined=0 held_back=0 (0 bytes) depots_touched=[441]
  needs_force_set_for=[440, 730]
```

### The recently-stored grace window (`VAULT_GC_GRACE_DAYS`, WP 3.8b)

**What it protects.** GC decides what is dead by diffing the cache against the
*current manifests it knows about*. Content that reached the cache only because
a real client pulled it through vault-core is in none of them: an opt-in Steam
**beta branch** (SteamPrefill has no branch selection, so vault-api can never
prefill one), a **demo**, or any build downloaded against a manifest vault-api
never recorded. Manifest-diff GC is not wrong about those chunks — they really
are absent from every counting app's current manifest — it is simply too eager,
and the tester who downloaded a beta build on Monday would re-download it after
Tuesday's collection. The window holds back anything stored in the last N days.

**Where it lives, and why not in the planner.** It is a `ChunkExclusion`
predicate (`gc_execute.RecentlyStoredGrace`) on the hook WP 3.8 left open:
`gc.plan_gc` still names those chunks as orphans, and the *execution* holds them
back. That separation is deliberate — the planner stays a pure statement about
manifests, and time is a policy layered on top of it. Exclusions can only ever
**shrink** the delete set; there is no hook that adds to it.

**`ctime`, not `mtime`.** nginx `proxy_store` stamps a stored file's mtime with
the *upstream* `Last-Modified`, i.e. Steam's publish time — months old for a
freshly stored chunk of an old build. That measured fact is why ADR-0007
rejected time-based attribution in the first place, and an mtime-based window
would protect nothing. `st_ctime` means:

- **Linux / the container (the deployment target):** the inode *change* time.
  `proxy_store` renames its temp file into place and the rename sets ctime, so
  ctime is the store time. Known caveat: `chmod`/`chown` also bump ctime.
  Nothing touches cache chunk files that way — but if an operator does run a
  recursive `chown` over the cache, everything looks freshly stored and GC frees
  less for one window, which is the safe direction.
- **Windows (dev runs only):** the file creation time, which for a
  store-on-miss write is also the store time. NTFS "file system tunneling" can
  hand a recreated file its predecessor's creation time within ~15 s of a
  delete; not the deployment target, and not a case this cache produces.

**Semantics, pinned:** the window is half-open — `age < N days` is held back, so
a chunk exactly N days old is released. Age is read with one `os.lstat` per
*orphan* (never following links, on the path rebuilt through the same
`deletion.safe_child_path` guards the deletion loop uses). A chunk whose age
**cannot be read** — a permission/ACL refusal, a transient I/O error — is held
back: fail-closed, an unreadable age must protect, not expose. Two cases are
deliberately not that: a file that is unambiguously **gone**
(`FileNotFoundError`) is left to the normal `already_gone` / 0-bytes
accounting, and an **invalid chunk id** is left to the executor's loud
`refused_unsafe_name` refusal rather than being quietly "held back".

A Windows **delete-pending** name belongs to the first of those, which is worth
saying because WP 1.6's `PermissionError [WinError 5]` note invites the
opposite guess: measured on Windows 11 (a handle opened with
`FILE_SHARE_DELETE`, the name unlinked while the handle lives), `os.lstat`
raises `FileNotFoundError [WinError 2]` for such a name. WP 1.6's access-denied
result was `unlink`'s answer, a *different* syscall, and applies to the removal
path rather than to this one. Landing on the plan-stands side is right anyway:
the name has already been unlinked for good, so "gone, 0 bytes" is the accurate
report and protecting it would be a fiction.

**Tuning.** `VAULT_GC_GRACE_DAYS=0` disables the window entirely — the predicate
is not even constructed and GC behaves exactly as it did before WP 3.8b. Raise
it for a long beta cycle. Note the window is not a completeness guarantee: after
it passes, an unrecorded manifest's chunks are collectable again and ADR-0007's
honest limit (consequence = re-download, never corruption) stands. The second
half of that protection — beta manifests joining the keep set via the opt-in
oracle (ADR-0007 addendum, decision B) — is WP 3.9's scope, not this one's.

**What you see.** A **dry run applies the window too**, so a preview cannot
promise bytes an execute run would refuse to free:

```
[vault-api] GC for app 440: DRY RUN.
  depot 441 planned: orphans=2 (700 bytes) held_back=2 (700 bytes) kept=2 ...
    ~ 00..b1: stored 0.3 days ago, grace window is 14 days
[vault-api] GC totals (DRY RUN): orphans=2 (700 bytes) held_back=2 (700 bytes)
  would_delete=0 (0 bytes) reclaimable_dedupe_bytes=0 planned_depots=[441]. ...
```

Held-back chunks are listed with a `~` marker (bounded like the `!` and `-`
lists, so a beta branch holding thousands of chunks cannot push the totals out
of the 4 KiB log excerpt), and the totals of an execute run carry
`held_back=<n> (<bytes> bytes)`. Holding chunks back is never a failure: a run
that freed nothing because everything was young is `done`, and — since nothing
was reclaimed — it sets no `needs_force`.

### Duplicate stored manifests: identical only

`gc.dedupe_candidates` marks the newest copy `keep` and the rest `duplicates`,
and deliberately does not verify that they hold the same bytes — it carries
their sizes so this side can decide how much to check. It checks: a duplicate
is removed only if it is **byte-identical** to the kept copy. Same manifest id
is a strong reason to expect identical content, but a truncated store-on-miss
write is real, and if the *newest* copy happened to be the truncated one, blind
dedupe would delete the good older copy and leave the corrupt one as the
depot's only manifest evidence. A duplicate that differs (or cannot be read for
comparison) is kept and reported — a finding, not a failure, so it does not
make the job `error`. If the copy that would be *kept* is missing or unusable
at execute time, the whole group is left alone: dedupe never ends with zero
copies, and that too is a finding rather than a failure (its realistic cause is
a concurrent `DELETE` taking the depot away mid-run, and a racing deleter must
not drag an otherwise-clean run to `error` — the WP 1.6 rule). The rule in one
line: **a decision GC made on purpose is never an `error`; a thing GC could not
do is.** Declines are listed in the log with a `-` marker, problems with `!`.

### State bookkeeping (the decision this package had to make)

**`apps.status` / `apps.last_prefill_at`: untouched.** They describe the app's
*prefill lifecycle*. GC reclaims bytes that are by construction not part of any
counting app's current manifest, so a `done` app is still done. Turning a green
badge red because one chunk file was locked would report a prefill problem that
does not exist.

**Size cache: invalidated** after an execute run that actually freed something
(the `sizes.SizeCache.invalidate` hook, WP 1.5) — reclaimed space that
`GET /v1/games` keeps hiding for up to `VAULT_SIZE_CACHE_TTL` seconds would
make GC's one visible effect look like it did not happen. Not invalidated for a
dry run.

**`apps.needs_force`: set to 1 for every app mapped to a depot the run actually
removed chunks from.** The full argument, including why the opposite answer was
tempting:

- *Why it looks unnecessary.* `needs_force` (ADR-0006 decision 2) exists
  because a non-forced run trusts SteamPrefill's own
  `successfullyDownloadedDepots.json` ("depot D was downloaded at manifest M").
  GC's keep set **is** the union of the current manifests — and for an app
  vault-api prefilled itself, "current manifest" is literally what SteamPrefill
  downloaded, since `depot_manifests` is ingested from its own `.bin` files
  (WP 3.2). Under that identity every chunk of M survives GC, SteamPrefill's
  claim stays true, and the next non-forced run is honest with no flag at all.
- *Why it is set anyway.* That identity is an assumption about two records
  agreeing, and there are reachable states where they do not:
  `manifest_ingest.ingest_after_prefill` is wrapped in its own `try`/`except`
  in `worker.py`, so a crash there leaves the prefill `done` with
  `depot_manifests` still naming the *previous* manifest while the disk holds
  the *new* chunks; and an app with no `depot_manifests` row falls back to the
  newest *cache-stored* manifest, which a client may have written earlier than
  SteamPrefill's fetch. In both, a non-forced follow-up would skip the depot and
  leave the cache silently incomplete, with no self-healing path — the exact
  wedge shape the WP 3.4 review reproduced and rejected. Being wrong in the
  "set it" direction costs one redundant `--force` run (re-requests served from
  cache at disk speed); being wrong in the "leave it" direction costs a
  permanently incomplete cache reported as complete.
- *Kept minimal.* Never on a dry run. Never for a depot that lost no chunk
  (skipped, zero orphans, or every removal failed). Never for a **dedupe-only**
  removal — a redundant manifest copy is not chunk content and changes nothing
  about whether a depot is completely downloaded. And when it does fire, it
  fires for **every app mapped to that depot**, not just the requester: a shared
  depot's bytes just changed under all of its co-owners (ADR-0003 addendum's own
  reasoning).

### Honest limits (ADR-0007, echoed)

- GC can delete chunks a client pinned to an **unrecorded or beta-branch
  manifest** still wants **once they are older than `VAULT_GC_GRACE_DAYS`**.
  The consequence is a **re-download**, never corruption. The grace window
  (WP 3.8b, above) narrows the exposure to content older than the window; the
  keep-set half of the protection (open beta branches via the manifest oracle,
  ADR-0007 addendum decision B) is WP 3.9. Passworded branches stay encrypted
  and uncoverable — the window is their only protection, ever.
- Unknown-manifest, unmapped and unreadable-owner depots are **skipped and
  reported**, never collected on partial knowledge.
- GC reclaims space; it does not certify that a depot is complete or correct.
- No atomicity against concurrent client writes (benign: re-fetch).
- No unmapped-depot GC and no *old*-manifest deletion — see the WP 3.7 section's
  "deliberately does NOT do" list, which still holds.

### What this work package deliberately did NOT do

- No auto-GC after update prefills (the config wiring is a later package).
- No third-party manifest oracle (ADR-0006 decision 4, gated).
- No job pause/resume/cancel, and no way to stop a GC job once it is running
  beyond stopping the worker.
- No changes outside `api/`.

### What WP 3.8b (the grace window) deliberately did NOT do

- No change to `plan_gc`: the plan still names recently-stored chunks as
  orphans and the execution holds them back — see above for why that
  separation is the point rather than an oversight.
- No manifest oracle and no beta-branch keep sets (ADR-0007 addendum decision
  B = WP 3.9).
- No `VAULT_GC_GRACE_DAYS` wiring in `deploy/compose.yaml` / `deploy/.env.example`
  (outside `api/`; the setting has a working default, so a deployment that does
  not pass it is protected, not broken).
- No per-app or per-depot window, and no way to exempt one depot from it.

## Manifest oracle (WP 3.9, ADR-0006 decision 4 + ADR-0007 decision B)

**Off by default, and the only part of SteamHangar that talks to anything
outside your LAN.** Read the privacy note below before enabling it.

### What it is for

Staleness detection's Tier 1 (ADR-0006 decision 1) is a non-forced SteamPrefill
run: cheap (~3 s for an app that is current) but it only answers *while a job
runs*, and each answer costs a Steam login. Between cron ticks vault-api
therefore cannot tell you a game has an update waiting, and it knows nothing at
all about a game it has never cached.

With `VAULT_MANIFEST_ORACLE=steamcmd_api`, vault-api asks a third-party public
mirror of Steam's PICS app info (`api.steamcmd.net`) for one app's depot list,
its current **public** manifest gid per depot, and its branch list. That buys
three things:

1. a **pre-emptive stale badge** — compare the oracle's public gid against
   what `depot_manifests` says vault-api last parsed;
2. **depot information for a never-cached game** — depot ids and gids for a
   title nothing has prefilled yet;
3. **beta-branch protection for GC** (ADR-0007 addendum, decision B) — see
   below.

### Beta-branch protection (decision B)

Opt-in Steam beta branches reach the cache only through store-on-miss: a real
client downloads them through vault-core, because SteamPrefill has no branch
selection. Their chunks appear in no `public` manifest, so plain manifest-diff
GC classifies every one of them as an orphan. WP 3.8b's grace window
(`VAULT_GC_GRACE_DAYS`) buys them N days; decision B is the durable half.

When the oracle is enabled, the manifest gids of **open (non-passworded)**
branches join the depot's GC keep set. The chunk set itself still comes from a
manifest vault-api can read — the beta build's own manifest, which the client
that downloaded the build also stored in the cache (or an archived `.bin`);
the oracle only says *which* gid that is. Passworded branches stay encrypted
and uncoverable: the grace window remains their only protection, and their
gids are dropped at validation time rather than stored with a flag some future
query might forget to filter on.

`public` is stored (the stale badge needs it) but deliberately excluded from
the keep-set query: the current public manifest already reaches the keep set
through vault-api's own `depot_manifests` record.

An operator can see this working in the GC job log: a depot line gains
`oracle_protected=<n> (<bytes> bytes)` — and only gains it when the oracle
actually saved something, so its presence means something.

### The safety invariant: oracle data can only ADD protection

Its single effect on the deletion path is that extra manifest gids join a
keep set, and a keep set can only grow. Consequences, each pinned by a test:

- the planned orphan set with the oracle on is always a **subset** of the same
  cache's orphan set with it off
  (`tests/test_gc.py::test_the_oracle_can_only_shrink_the_orphan_set`);
- a garbage, poisoned, stale or missing oracle answer therefore cannot cause a
  deletion — the worst it can do is fail to *prevent* one, which is exactly
  the pre-WP-3.9 baseline;
- **the readiness gate is never fed by the oracle.** An open beta branch whose
  manifest vault-api cannot read does not block GC. Blocking would freeze
  every depot of every app that ever had a beta branch, permanently — the same
  leak ADR-0007's own addendum refused for uncached co-owners.

### Fail-soft everywhere

Unreachable, slow, redirected, HTML instead of JSON, JSON with the wrong
shape, an app the oracle has never heard of, deliberately hostile content: all
of them mean *no oracle data*, which means *behave as if the oracle were off*.
`POST /v1/oracle/{appid}/refresh` answers `200` with `ok: false` and a reason;
`vault_api.oracle.refresh_app` never raises. Nothing here can fail a job,
block GC, or take the API down.

**The document shape this parser expects was modeled on `api.steamcmd.net`'s
`/v1/info/{appid}` responses and has NOT been verified against the live
service by this work package** — every test fixture is synthetic. If the real
shape differs, or changes later, the mismatch degrades to "as if the oracle
were off": the answer yields no usable branch manifests, GC loses only the
*extra* protection, and nothing is deleted that would not have been deleted
before WP 3.9 existed. Confirming the shape against the real endpoint is a
deployment-time check, not a code change.

Two details worth being precise about, because they are the difference
between "stale data" and "no data":

- **A failed *fetch* preserves the previous snapshot** — a validated,
  timestamped snapshot can only add keep-set protection, and `checked_at`
  says how old it is. **A parseable-but-degraded answer *replaces* it**: if
  `data.<appid>` is present but `depots` (or `branches`) is missing or
  unusable, that is a successful refresh reporting "no open-branch manifests",
  and the app's rows are replaced with an empty snapshot. Snapshot semantics
  are what stop a branch that disappeared upstream from protecting chunks
  forever; the price is that a degraded-but-well-formed answer withdraws
  protection too. `warnings[]` in the refresh response names the reason.
- **`oracle_app_state.depot_count` counts depots that yielded at least one
  *open-branch* manifest**, not every depot the app has. An app whose branches
  are all password protected therefore records `depot_count: 0` even though
  the document listed depots — correctly, since nothing about those depots was
  stored.

### Everything returned is validated before it is stored

The response is attacker-shaped input by definition. `docs/LEARNINGS.md`'s
"Parsers" rules are binding because these ids feed SQL parameters and — via
the keep set — filesystem paths:

- app/depot ids go through `deletion.coerce_positive_id` (strict ASCII digits;
  `" 4 "`, `"+4"`, `"1_0"`, Arabic-Indic digits and `bool` all rejected);
- manifest gids go through `gc.valid_manifest_id` — the *same* validator GC
  applies to its own `depot_manifests` column, so the two cannot drift;
- branch names go through `oracle.valid_branch_name` (bounded ASCII, no path
  separators, no `.`/`..`);
- the body is size-bounded before it is decoded; `RecursionError` from deeply
  nested JSON is caught **by name** and converted to `OracleError` (WP 2.1's
  finding: an exception outside a parser's documented contract crashes the
  caller); depot/branch/row counts are capped, as is the number of extra
  manifests one depot may contribute (`gc.MAX_EXTRA_MANIFESTS`);
- values are re-validated on the way **out** of the database too: only
  validated data is ever written, but the database is a file an operator can
  edit, and a gid becomes a filename.

Both manifest spellings are accepted (`manifests.<branch>` as an object with a
`gid` field, and the older bare-gid string). A branch that the `branches`
object never declared is treated as password-state-unknown and skipped; if
`branches` is unreadable entirely, **no** branch is open and nothing is
recorded.

### Privacy — this is the note to read before enabling it

Every other component talks only to the LAN, to Steam's CDN through
vault-core, or to Valve through SteamPrefill. **With the oracle enabled,
vault-api makes outbound HTTPS requests to a third party** (by default
`api.steamcmd.net`, which is not affiliated with Valve and not run by this
project).

- **What leaves the network:** the Steam **app id** being asked about, in the
  URL path — i.e. which games this vault tracks, and roughly when — plus the
  usual things any HTTP client reveals (your public IP, a `User-Agent`
  identifying vault-api).
- **What never leaves it:** no API key, no `client_id`, no agent report, no
  user identity, no Steam credentials (ADR-0004: vault-api never has any), no
  cache contents, no LAN addresses.
- **Nothing is sent automatically.** Refresh is an explicit
  `POST /v1/oracle/{appid}/refresh`; the WP 3.5 scheduler and the job worker
  never call it. There is no background polling to disable.
- **Keeping it on your network:** point `VAULT_MANIFEST_ORACLE_URL` at your
  own mirror of the same API. Redirects are never followed, so the request
  cannot be handed to a host you did not configure.
- **Turning it off is immediate:** unset `VAULT_MANIFEST_ORACLE` and the stored
  rows stop influencing GC on the next run, without waiting for a refresh
  (`DELETE /v1/oracle/{appid}` removes them entirely).

### What this work package deliberately did NOT do

- **No automatic refresh.** Wiring the oracle into the scheduler or the worker
  would make an outbound third-party request a background, invisible event on
  a box whose operator may never have read this section. Scheduling it (with
  its own cadence setting and an explicit opt-in) is a follow-up.
- **No oracle fields on `GET /v1/games`.** The stale badge is served from
  `/v1/oracle/{appid}` so the games endpoints keep answering identically with
  the feature off; folding a `stale` flag into the library list is a Phase-4
  decision to make once the UI knows how it wants to render it.
- **No second oracle, and no fallback chain between oracles.**
- **No use of the oracle's `common`/`config` data** (names, cover art,
  install sizes) — only depots, branches and gids are read.
- **No `deploy/` wiring** (outside `api/`; the feature is off by default, so a
  deployment that does not pass the variables is in the intended state).

## Steam Web API relay (WP 4a.6r; ADR-0004 addendum, user decision A+C)

**Off until a key is configured, and one of the few things in SteamHangar that
talks to anything outside your LAN.** Read the privacy note below before
setting a key.

### What it is for

The Steam Web API sends no CORS headers, so the Phase-4a **web UI**, running
in a browser, cannot call `GetOwnedGames`/`GetPlayerSummaries` directly at
all. This relay is vault-api's narrow answer: it stores one revocable,
**read-scoped** Steam Web API key — entered once by the operator in the web
UI's settings — and relays exactly two calls on the caller's behalf:

**Superseded (WP 4h.4, `app/README.md` "Steam library via the vault relay"):
the native Android app uses this exact same relay now, not a device-local
key of its own.** ADR-0004 decision 2 originally gave the app an independent,
device-local path (its own Steam Web API key, entered on the phone, never
proxied); ADR-0004's second addendum retired that path — the per-user key
ask, a second egress point toward Valve, and a privacy gate (WP 4h.0) that
only covered this relay were all reasons to move the app onto the SAME two
endpoints below rather than keep two parallel designs. `GET /v1/steam/key`
et al. below remain the operator-facing key management surface; nothing
about THIS relay's shape changed for the app to start using it.

- `IPlayerService/GetOwnedGames/v1` — the library grid's game list;
- `ISteamUser/GetPlayerSummaries/v2` — persona name and avatar.

**Nothing else.** This is a hard boundary from the ADR-0004 addendum, not a
default that happens to be narrow today: no endpoint that could act on the
account (friends list writes, inventory trades, anything requiring a session
rather than a Web API key) is in scope, and none is planned.

### Setting it up

1. Generate a Web API key at <https://steamcommunity.com/dev/apikey> (any
   Steam account; a domain of `localhost` is accepted for personal use).
2. `PUT /v1/steam/key` with `{"key": "<32 hex characters>"}` — normally done
   from the web UI's settings screen, not by hand.
3. `GET /v1/steam/key` confirms `{"configured": true, "key_last4": "...."}`.
4. `DELETE /v1/steam/key` at any time to revoke it from vault-api's side
   (independent of revoking the key itself on Valve's site, which an operator
   should also do if the key was ever exposed).

**The key is never a password.** Login for the web UI's own identity still
happens on Valve's OpenID page (ADR-0004 decision 2's "Sign in with Steam"),
completely separate from this key. Worst case of this key leaking: someone
else can read this Steam account's *public* profile and game list — nothing
they could not already see by visiting the profile in a browser, assuming its
privacy settings are public. Worst case of the vault API key itself leaking
is a much larger blast radius (job control, cache deletion) and is covered by
the existing "Auth" section below; this key is deliberately a separate,
narrower secret from that one.

### The key is never echoed, logged, or leaked into an error

- `GET`/`PUT /v1/steam/key` return `configured` and `key_last4` (the last 4
  characters) **only** — never the value itself, in any response, ever.
- Every upstream HTTP failure (unreachable, timeout, non-200, garbage body)
  becomes an error message built **only from the endpoint path**
  (`https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/`, no query
  string) — the key and the `steamid` being looked up live exclusively in the
  query string, which no function in `vault_api/steam_relay.py` ever formats
  into a log line or an exception message. `tests/test_steam_relay.py` pins
  this with a grep-style check across the JSON response body and captured log
  output.
- The stored value is re-validated on the way **out** of the database (same
  reasoning as the manifest oracle's `gc_keepset_gids`): a hand-edited or
  corrupted row is treated as "not configured" rather than sent to Valve.

### `409` while unconfigured — a distinct, documented shape

`GET /v1/steam/owned-games` and `GET /v1/steam/player-summaries` answer
**`409 Conflict`** whenever no key is configured — never a `200` with an
empty or guessed result. This is deliberately a status code rather than a
`{"configured": false}` body shape used elsewhere in this API (the manifest
oracle answers `200`/`enabled: false` because "never asked about this app" is
a normal informational state): a relay call while unconfigured is a request
this server genuinely cannot service, which `409` communicates unambiguously
to a web UI checking `response.status`. `GET /v1/steam/key` is the one place
`configured: false` appears in a normal `200` body — answering exactly that
question is its whole job.

### `steamid` is hostile input

Every relay call takes `?steamid=<SteamID64>` as a **query string**, strictly
validated (`vault_api.steam_relay.valid_steamid64`) before it goes anywhere
near a URL: exactly 17 ASCII decimal digits, range-checked against the
individual-account SteamID64 space (universe 1, type 1, instance 1, account
number 0..2³²-1). Anything else — wrong length, non-ASCII look-alike digits,
a numerically valid but out-of-range value — is a clean `422`, matching
`docs/LEARNINGS.md`'s "Parsers" house rule that a value feeding a URL is never
trusted just because it looks numeric.

### Everything Valve returns is validated and whitelisted

The response is attacker-shaped input by definition (a network call to a host
this project does not run). `vault_api/steam_relay.py` applies the same
discipline as the manifest oracle (`vault_api/oracle.py`):

- the raw body is bounded (2 MiB) before it is decoded; `RecursionError` from
  deeply nested JSON is caught **by name** and converted to the module's own
  `SteamRelayError` (the same WP 2.1/3.9 finding: an exception outside a
  parser's documented contract crashes the caller);
- the number of games/players is capped (`MAX_GAMES`, `MAX_PLAYERS`), and a
  hostile document degrades to "the rest were ignored" rather than a memory
  blow-up or a crash;
- **only the whitelisted fields the library grid/decision-support panel need
  ever cross into the response** — `appid`, `name`, `playtime_forever`,
  `img_icon_url`, `rtime_last_played` (the last one added in WP 4h.1, see
  below) for games; `steamid`, `personaname`,
  `avatar`/`avatarmedium`/`avatarfull`, `personastate` for players. Every
  other field Valve's API returns (visit timestamps, community-stats flags,
  real names, location) is read by nothing here and never reaches a client;
- a returned `steamid` that does not match the one requested is dropped, not
  trusted (the same corruption cross-check the oracle applies to `appid`);
- an avatar URL that is not a bounded `http(s)://` string is dropped rather
  than handed to a client's `<img src>`.

A garbage or unreachable upstream answer is a clean `502` with a generic
detail message — never a crash, never a partially-validated value reaching a
client.

### `rtime_last_played` — absence is not zero (WP 4h.1)

Phase 4h's decision-support panel wants to say things like "43 GB cached, 0
minutes played" — which needs to know when an account last played a game, not
just how long. `GetOwnedGames` carries this as `rtime_last_played`
(Unix-epoch seconds); it was never asked for before this work package because
nothing consumed it. `playtime_forever` (already relayed since WP 4a.6r) was
the other half of that statement and needed no change.

**No additional request parameter was needed.** Verified against the
Steamworks Web API documentation for `IPlayerService/GetOwnedGames`:
`rtime_last_played` is not gated behind a distinct flag, only behind the SAME
privacy/ownership condition that already governs whether Steam returns
anything beyond `appid`/`playtime_forever` at all — your own key against your
own account, or a fully public profile. `include_appinfo=1` was already being
sent. **This was checked against Valve's public documentation, not against a
live account or key** — see "What this coder could not verify" below.

**The honesty rule (pin 1): an absent upstream field surfaces as `null`, NEVER
as `0`.** Steam omits `rtime_last_played` entirely for a private profile, an
account viewed without full permissions, an old library entry, or (as far as
this codebase could verify without a live key) simply a game never launched —
that is a genuine ABSENCE, and rendering it as `0` would display as "last
played 1970-01-01", a claim about the account this code did not actually
observe.

`vault_api.steam_relay._coerce_last_played` ALSO maps an upstream
**explicit** `0` to `null` — but for a different, more careful reason than
"it carries no information". **Steam's own convention is that an explicit
`0` here DOES mean "never played" — that is real information, not noise.**
It is discarded anyway, on purpose, because `playtime_forever` in the SAME
response already states that identical fact (`playtime_forever == 0`), and
rendering `rtime_last_played` as a literal 1970-01-01 date — decades before
Steam existed, from a field whose whole job is to be a human-readable "last
played" moment — is a strictly worse result than `null` for a fact the other
field already carries. This is a deliberate trade (keep the two fields
independent vs. never show an absurd date), not a claim that `0` is
meaningless. `OwnedGameOut.rtime_last_played: int | None` carries the `null`
end to end either way — never defaulted.

This is deliberately asymmetric with `playtime_forever`, which keeps its
existing `0`-on-malformed-input default (`int`, not nullable) — that field's
TYPE is an existing, additive-only contract this work package does not touch,
and in practice Steam's documented contract guarantees `appid`+
`playtime_forever` together whenever a game object appears at all, so that
default only ever fires on a genuinely hostile upstream body, never on an
ordinary "never played" game (which Steam represents with its OWN explicit
`0` — a real claim, unlike the absence case above).

### Rate-limit friendliness: a small in-memory cache

`vault_api.steam_relay.RelayCache` holds the last validated answer per
`(endpoint, steamid)` for 5 minutes (`DEFAULT_CACHE_TTL_SECONDS`) — enough to
absorb a web UI re-rendering the library grid a few times in a short window
without turning every render into a fresh Steam Web API call. Deliberately
**not** an env-configurable setting (this work package adds no new `VAULT_*`
variable) and **not** persisted — a restart empties it, which costs nothing
but one extra upstream call on the next request. It stores already-whitelisted
result objects, never raw upstream bytes, and the router checks "is a key
configured" **before** consulting the cache — so clearing the key immediately
reverts to `409` even if a cached answer from moments ago is still warm.

### Privacy — the note to read before setting a key

Every other component talks only to the LAN, to Steam's CDN through
vault-core, or to Valve through SteamPrefill's own session. **With a relay key
configured, library queries originate from the SERVER** — they leave the LAN
toward Valve — not from the browser, mirroring the ADR-0004 addendum's own
wording:

- **What leaves the network:** the configured Web API key, and the SteamID64
  being looked up, as query parameters over HTTPS to
  `api.steampowered.com` — pinned as the only host this module will ever
  contact (no operator-configurable URL, unlike the manifest oracle: there is
  no "point it at your own mirror" story for a Valve-authenticated call), with
  redirects refused so the request cannot be handed to a different host.
- **What never leaves it:** the vault API key, any agent report, any cache
  content, any LAN address, and — per ADR-0004 — Steam **credentials**; this
  key is a revocable, read-scoped Web API key, never a password.
- **Nothing is sent automatically.** A relay call happens only when the web
  UI (or any authenticated client) calls `GET /v1/steam/owned-games` or
  `GET /v1/steam/player-summaries`. There is no background polling, no
  scheduler wiring, and no job-worker involvement.
- **Turning it off is immediate:** `DELETE /v1/steam/key`, and the very next
  relay call answers `409` — no waiting for a cache entry to expire.
- A full accounting of every data path a self-hosted install exposes belongs
  in `SECURITY.md` (planned, WP 5.3 — not yet written); this section is the
  interim, authoritative source for this one feature.

**WP 4h.1 addendum — a sharper personal-data note.** `rtime_last_played` (and
`playtime_forever`, already relayed but unused by any frontend until this
work package) are personal data in a sharper sense than the persona
name/avatar this relay already carried: "when did this person last play this
game" and "how long have they played it" are exactly the kind of fact the
Phase 4h plan section calls out as judgemental, particularly in a
shared-living-room vault with more than one person using the same Steam
account or the same physical screen. **WP 5.3's threat model must cover this
as a new personal-data surface flowing through the API response** — this
paragraph is that inheritance point. This package only relays and validates
the two fields; the display-side privacy controls the Phase 4h plan section
demands (off by default or dismissible at any time, no nagging, never held up
to somebody else) are WP 4h.2's (the web panel's) responsibility, not this
one's. **No new endpoint aggregates these fields across clients or accounts**
— both fields stay scoped to the one `steamid` a caller explicitly requested,
exactly like every other field this relay already returns.

### The privacy gate: `VAULT_RELAY_EXPOSE_PLAYTIME` / `VAULT_RELAY_EXPOSE_LAST_PLAYED` (WP 4h.0, ADR-0010)

Two independent, **environment-only** settings decide whether
`playtime_forever` and `rtime_last_played` may ever appear in
`GET /v1/steam/owned-games`'s response at all — separately, because "when
did this person last play" (per the WP 4h.1 addendum above) is the sharper
of the two facts; an operator may want the decision-support panel's
aggregate hour count while still refusing to ever surface the date.

**Both default OFF.** Phase 4h's own privacy stance
(`docs/PROJECT_PLAN.md`, user decision 2026-08-18) already treats playtime
itself — not only `rtime_last_played` — as something a shared-household
vault must not surface without an explicit opt-in ("off by default or
dismissible at any time, no nagging, and no number that gets held up to
somebody else"). That is the same house style the WP 3.11 event sweep
already follows for every cost-sensitive switch with no OTHER person's data
at stake: ship off, let an operator who wants it read this section and turn
it on. **`VAULT_SWEEP_INCLUDE_CACHED` is no longer a same-direction example
of that house style (WP SWEEP-1, ADR-0014, 2026-08-22) — it now ships ON by
default,** which is not a contradiction: disk/bandwidth an OPERATOR spends
and can see is a different kind of cost than a DIFFERENT person's playtime
surfaced without their knowledge, and only the latter is what this privacy
stance is about. "The decision-support panel needs it" is explicitly not
treated as a sufficient reason to default either of these two playtime
switches on — the privacy stance above is.

```bash
VAULT_RELAY_EXPOSE_PLAYTIME=true       # off by default
VAULT_RELAY_EXPOSE_LAST_PLAYED=true    # off by default
```

**When a key is off, the field is genuinely ABSENT from the JSON body** —
not sent as `0` or `null`, which a client (or anyone with the browser's dev
tools open) could still read. The gate is applied at the outermost
conversion from the relay's internal, fully-populated record to the wire
response (`routers/steam.py`'s `get_owned_games`), never earlier: the
in-memory `RelayCache` still stores the complete, validated answer either
way, so flipping either variable and restarting takes effect on the very
next request without needing to wait out or clear the cache.

**Why environment-only, with no `PATCH /v1/settings` override at all
(ADR-0010) — this is a deliberate, narrow exception to ADR-0009's db >
env > default precedence, not an oversight.** ADR-0009's settings table is
stored in the `vault-db` Docker volume; `deploy/compose.yaml` (and
`deploy/.env`) are not — they live in the operator's own persistent config,
covered by whatever backs that up. For an ordinary tuning knob, losing an
override and reverting to a built-in default (a lost volume, a
`docker compose down -v`) is a harmless, noticed event. For a privacy
opt-out the direction of that fallback is the whole question: a database
override that turned exposure OFF would be erased by a lost volume just
like any other row, silently reverting to whatever the environment says —
and if the environment was left at its default (unset, meaning off but not
LOCKED off under a design that let the database win), that reversion could
mean collection quietly resumes with no notification to anyone. These two
keys have exactly one source of truth, kept in the one place that survives
that failure mode. `docs/adr/0010-relay-privacy-gate-env-only.md` has the
full argument, including the alternative design (an env "ceiling" over a
runtime toggle) this project considered and rejected, and the real cost of
this choice: **there is no runtime opt-out** — changing either value means
editing `deploy/compose.yaml` (or `.env`) and restarting the `vault-api`
container.

**`GET /v1/settings` still reports both**, as ordinary informational,
env-only rows (`settings_store.ENV_ONLY_INFO_KEYS`, ADR-0009 §5's
allowlist mechanism) — `{"key": "relay_expose_playtime", "effective":
false, "source": "default", "applies": "restart-required", "env_only":
true}` on a fresh install — so a settings UI can show an operator the
current state and explain why there is no switch, instead of either hiding
the fact entirely or offering a toggle that would `422`. `PATCH
/v1/settings` refuses either key by name with the same distinct
"environment-only" `422` detail every other row in that allowlist gets.

### What this work package deliberately did NOT do

- **No new `VAULT_*` environment variable.** The key is runtime-set through
  the endpoints above, per the ADR-0004 addendum's explicit design (entered
  in the web UI, stored server-side) — unlike every other opt-in feature in
  this file, there is nothing to add to `.env.example`.
- **No per-user keys.** One relay key per vault, matching `steam_relay_key`'s
  single-row schema — this mirrors `VAULT_API_KEY` itself, not a
  multi-tenant credential store.
- **No endpoints beyond the two named in the ADR addendum.** Friends lists,
  inventories, achievements, and anything else the Steam Web API exposes are
  out of scope; adding one is a new ADR decision, not an extension of this
  module.
- **No `web/` or `deploy/` wiring** — this work package is `api/`-only by
  design (WP 4a.6 consumes this relay from the web UI in its own,
  branch-parallel work package).

### What WP 4h.1 deliberately did NOT do

- **No display-side privacy control.** The Phase 4h plan section's "off by
  default or dismissible, no nagging" stance applies to the WEB PANEL
  (WP 4h.2), not to this relay — this package only makes the field available,
  honestly, to whichever authenticated caller asks for it.
- **No new outbound call.** `rtime_last_played` rides the SAME
  `GetOwnedGames` request `playtime_forever` already used — no new endpoint,
  no new parameter, no new host.
- **No aggregation endpoint.** See the privacy addendum above — both new
  fields stay scoped to one requested `steamid`.
- **`playtime_forever`'s type is untouched.** It keeps its existing
  `int`/0-default contract; only the brand-new `rtime_last_played` field gets
  the fully nullable treatment. See the dedicated section above for why.
- **Not verified against a live Steam account or key.** The "no extra
  parameter needed" claim and the exact conditions under which Valve omits
  `rtime_last_played` were checked against the Steamworks Web API's public
  documentation and community reports, not against a real `GetOwnedGames`
  response — no Steam Web API key was available in this coding session. This
  belongs on the real-world/device verification list alongside the other
  documented "checked against docs, not against a live account" gaps in this
  file (e.g. the manifest oracle's shape assumption, "Manifest oracle"
  section above).

## Auth

Every endpoint requires the header `X-Api-Key: <VAULT_API_KEY>`, checked
with a constant-time comparison (`hmac.compare_digest`) in the
`require_api_key` FastAPI dependency (`vault_api/auth.py`). Missing or
wrong key → `401`.

**Checked before routing (WP SEC-FIX-4, S-3):** FastAPI parses a JSON body
before a router dependency runs, so `vault_api/body_guard.py` (a pure-ASGI
middleware) checks the key first for every `/v1` path except exactly
`/v1/health`, with the same comparison (`auth.api_key_matches`). A keyless
request is answered `401` without a body byte being read — also for a path
that does not exist (`401`, not `404`) and for malformed JSON (`401`, not
`422`). Every request body is capped at 1 MiB (`body_guard.MAX_BODY_BYTES`):
a larger `Content-Length` is `413` up front, a chunked body is `413` once it
passes the cap. The largest legitimate body (a 10 000-appid agent report) is
~110 KB. The dependency stays on every router as defence in depth.
Because the public exception is exactly `/v1/health`, a keyless
`/v1/health/` (trailing slash) now gets `401` rather than Starlette's
`307` redirect.

**Non-ASCII keys (WP 1.3 fix):** `hmac.compare_digest` raises `TypeError`
for a non-ASCII `str` argument (confirmed empirically — a key containing
e.g. "café" raised `TypeError: comparing strings with non-ASCII characters
is not supported`), which FastAPI/Starlette previously surfaced as an
unhandled `500` instead of a `401`. `require_api_key` now encodes both the
provided and expected key to UTF-8 bytes (`surrogateescape` error handler,
which also absorbs the rarer case of a lone surrogate code point reaching
the header) and compares the bytes with `hmac.compare_digest` — bytes have
no charset restriction, so a non-ASCII key is now treated like any other
wrong key: `401`. Covered by
`tests/test_auth.py::test_non_ascii_key_is_rejected_with_401_not_500`.

**The single documented exception is `GET /v1/health`.** Plan §9 states
"API designed with no unauthenticated endpoints"; plan §6 and §10
simultaneously designate `/v1/health` as the liveness endpoint "polled by
any external monitoring system", which by definition cannot present an API
key. This work package resolves the conflict in `/v1/health`'s favor for
that one route because:

- it returns a fixed body (`{"status": "ok"}`) and nothing else — no app
  names, sizes, job state, or client identifiers ever appear in it;
- it exposes no data and offers no way to enumerate or mutate anything;
- external monitoring (uptime checks, container healthchecks) is a stated
  requirement (plan §10) that an API-key gate would defeat.

**No version string either (WP 4e.7).** `GET /v1/settings`' `server_version`
field (see "Persisted settings" below) is deliberately NOT mirrored onto
`/v1/health` — a version number is free fingerprinting for anything that can
reach the port. **Correction (review round 1, should-fix S2):** an earlier
draft of this note cited "the one route this deployment's own docs say must
never be exposed to the open internet", which misattributes §10's rule —
that section says never to expose **vault-core**/port 80, and explicitly
offers a public-domain remote-access profile that fronts **vault-api**
itself (TLS-terminated, behind a reverse proxy) with `/v1/health` still
meant to answer an external monitor either way. The real point is the
opposite framing: because `/v1/health` CAN legitimately be internet-
reachable in that shipped profile, keeping its body minimal matters MORE,
not less. `tests/test_health.py::test_health_body_is_byte_for_byte_unchanged_by_wp_4e7`
pins the exact response text so a future change is caught by an assertion,
not a review comment.

This is now factually the *only* unauthenticated surface: FastAPI's
auto-generated docs (`/docs`, `/redoc`) and schema (`/openapi.json`) are
disabled (`FastAPI(..., openapi_url=None)` in `main.py`) rather than left
open, since they would otherwise expose the full route/schema map without
a key — and Swagger UI's assets load from a CDN anyway, so they wouldn't
render on an offline homelab install regardless.

**Secure-by-default pattern.** Auth is attached at the `APIRouter` level,
not per-route: `routers/health.py` is the one router with no
`require_api_key` dependency; every other router (`routers/games.py`,
`routers/mapping.py`, `routers/jobs.py`, `routers/cache.py`,
`routers/agent.py`, `routers/clients.py`, and future ones) is constructed as
`APIRouter(dependencies=[Depends(require_api_key)])`, so a route added to it is
authenticated automatically and can't be forgotten.
`tests/test_security.py` enforces this by walking `app.routes` and
asserting the dependency is present everywhere except `/v1/health` — it picked
up WP 1.4's three new routes, WP 1.6's `DELETE /v1/cache/{appid}` and WP 2.4's
`POST /v1/agent/installed` + `GET /v1/clients` with no test change, verified by
rerunning it each time (a route that deletes user data, or one that accepts
writes from a machine on the tailnet, is exactly the one you do not want to add
unauthenticated by accident).

**Phase 6 obligation (recorded WP 4c-api; extended WP AG-1):** the API key
today is a single static all-or-nothing secret (plan §6: "Auth: static API
key in a header"). `POST /v1/prefill/cached` (see "Check & update all cached
games" above) is write-capable surface that can trigger real downloads for
every cached app in one call, and `DELETE /v1/clients/{client_id}` (see
"Deleting a client" above) is destructive write surface that permanently
removes a client's report history — Phase 6's planned scoped API keys must
cover both explicitly, not just the routes that existed when that phase was
designed.

**`GET /v1/ping` removed (WP 1.3).** It was WP 1.2's scaffold route,
existing solely so the test suite had an authenticated endpoint to exercise
`require_api_key` against before real routes existed. Now that
`/v1/games`, `/v1/games/{appid}`, `/v1/mapping/{depotid}` and `/v1/mapping`
exist, the scaffold is gone (`routers/internal.py` deleted) and the test
suite (`tests/test_auth.py`) exercises auth against `/v1/games` instead.

## Web UI static serving (WP 4a.1)

vault-api mounts a second thing besides the `/v1/*` API: the built-in web
UI, a no-build vanilla ES-module SPA that lives in `web/` — a new
top-level directory, sibling to `api/` (Phase 4a stack decision,
`docs/WORKPACKAGES.md`: no bundler, ever, so `docker compose up` stays the
whole deploy story and CI stays trivial). All of the logic lives in
`vault_api/webui.py`; `main.create_app` calls `mount_web_ui(app,
settings.web_dir)` as the very LAST step, after every `/v1/*` router is
registered.

### Where `web/` is found

`Settings.web_dir` defaults to a path computed relative to
`vault_api/config.py`'s own file location (`_default_web_dir`), which
resolves to the real `web/` directory in a native checkout regardless of
the process's current working directory. Override it with `VAULT_WEB_DIR`
if you ever need to point at a different build of the UI.

**Packaging gap CLOSED (packaging work package, `docs/PROJECT_PLAN.md` §7
Phase 5, 2026-08-17).** Earlier text on this page said the shipped Docker
image did not copy `web/` in at all — that was true through WP 4a.1–4a.8,
and is no longer true. `deploy/compose.yaml`'s vault-api service now builds
with `context: ..` / `dockerfile: api/Dockerfile` (repo root, not `api/`,
because `web/` is a sibling of `api/`, not a child of it); `api/Dockerfile`
COPYs `web/` in at `/app/web` and sets `ENV VAULT_WEB_DIR=/app/web`
explicitly, because `_default_web_dir()`'s relative computation is correct
for a native checkout but resolves to the wrong path (`/web`, not
`/app/web`) once `vault_api/` itself lives at `/app/vault_api` inside the
image — see the comment next to that `ENV` line in `api/Dockerfile` for the
exact dirname-climb reasoning. The container now serves the real UI, same
as native dev (`uvicorn vault_api.main:create_app --factory`, run from
anywhere in the repo), with zero configuration beyond `docker compose up`.

### Auth boundary

No `require_api_key` dependency is attached to any app-shell route or
asset mount — the WP 4a.1 brief is explicit about this: the app shell and
its assets need no key, since the SPA itself sends `X-Api-Key` on its own
`/v1/*` calls once the user types one into Settings. Every `/v1/*` route
keeps exactly the auth it already had; see "Auth" above and
`tests/test_webui.py::test_v1_auth_is_unchanged_with_web_ui_mounted`.

Because these are now real `APIRoute`s (not a `Mount`, which
`tests/test_security.py`'s route walk already skipped), they DO show up in
that walk and needed to be added to its `PUBLIC_PATHS` explicitly
(`_WEBUI_PUBLIC_PATHS`, generated from `webui._SPA_ROUTES` — see
`tests/test_security.py`) — otherwise that test would fail the moment this
work package's routes exist, which is exactly what happened once during
review and is now fixed by listing them as intentionally public rather
than by weakening the walk.

### Routing: exact routes for the app shell, real asset subtrees only — no catch-all

**Review history worth keeping.** The first version of this section (and
of `webui.py`) mounted the WHOLE `web_dir` at `"/"` with `StaticFiles` and
used a global `StarletteHTTPException` handler to fall back to
`index.html` for any unmatched `GET` 404. That is a catch-all: it
intercepts every path with no other matching route, and it measurably
changed `/v1/*` behaviour that has nothing to do with the web UI —
confirmed empirically by diffing against the app with the mount removed
entirely (`git stash` the whole work package, boot, `curl`, compare):

| request | pre-WP-4a.1 (measured) | catch-all Mount (rejected) | current (this section) |
|---|---|---|---|
| `GET /v1/games/` (trailing slash) | `307` (Starlette `redirect_slashes`) | `404` | `307` |
| `HEAD /v1/health` | `405` | `404` | `405` |
| `POST /v1/does-not-exist` | `404` | `405` | `404` |
| `GET /v1/does-not-exist` | `404` (JSON) | `404` (JSON) | `404` (JSON) |

The `HEAD /v1/health` row is itself worth a note: FastAPI's `APIRoute`
does **not** auto-add `HEAD` support to a `GET`-only route the way plain
Starlette's own `Route` does (confirmed by reading both classes'
`__init__` — Starlette's adds `"HEAD"` to `self.methods` whenever `"GET"`
is present, FastAPI's `APIRoute.__init__` does not) — so `405` for
`HEAD /v1/health` is not a bug to fix, it is the pre-existing, correct
baseline this work package must not disturb.

**The fix: stop being a catch-all.** `mount_web_ui` (`vault_api/webui.py`)
registers:

- Exact routes for `/` and `/index.html`, both handled by the same small
  function that returns `FileResponse(index_path)`.
- Exact routes for each of the three scaffolded views — `/library`,
  `/downloads`, `/settings` (`_SPA_ROUTES`) — plus a `/{view}/{rest:path}`
  route per view so a future nested path (e.g. a per-game deep link like
  `/library/440`) is already covered; the client-side router owns
  everything past the first segment.
- `StaticFiles` mounted **only** on the two real asset subtrees, `/css`
  and `/js` — never on `/web_dir`'s root, and never on `/`.

Every route above is registered with `methods=["GET", "HEAD"]` explicitly
(not relying on FastAPI's — absent — auto-HEAD behaviour).
`starlette.responses.FileResponse` already special-cases `HEAD` itself at
the ASGI layer (`send_header_only`): same status and headers as `GET`, no
body. `tests/test_webui.py::test_head_on_app_shell_routes_matches_get`
pins this for every app-shell route.

**Consequence: a path that matches none of the above never reaches any
code in this module at all.** Every `/v1/*` path (matched or not, right
method or not, trailing slash or not), `/docs`/`/redoc`/`/openapi.json`
(deliberately disabled, see "Auth" above), and any other unmapped path
falls straight through to Starlette's own default routing — byte-for-byte
what it does in a vault-api build with no web UI mounted. No custom
exception handler exists any more; there was nothing left for one to do.

**Why an allowlist of three literal view names, not "any non-`/v1/` GET
404" or a wildcard mount:** both broader designs were tried and rejected.
The wildcard `Mount("/")` produced the table above; a plain "any non-`/v1/`
GET 404 → serve `index.html`" exception handler (the second design tried,
before the routing rework) separately broke
`tests/test_security.py::test_docs_and_openapi_are_disabled` — `/docs`,
`/redoc` and `/openapi.json` are non-`/v1/` GET 404s too, and would have
been silently upgraded from "disabled" to "200 HTML". The allowlist is
exactly the three nav destinations the frozen round-7 mockup
(`docs/design/vault-app-mockup.html`) actually has — **not four**: an
earlier draft of this work package added a `clients` nav item/view, which
review correctly flagged, since the mockup reaches the client list from
the bypass banner as a sheet, not a nav destination (WP 4a.7's job). Two
tests guard the sync between the server and the client router:
`tests/test_webui.py::test_router_js_views_match_webui_spa_routes` parses
`VIEWS` out of `web/js/router.js` and compares it to `webui._SPA_ROUTES`,
and `tests/test_security.py`'s `_WEBUI_PUBLIC_PATHS` is generated from
`_SPA_ROUTES` too — so a later 4a.x package adding a fourth view needs
exactly one edit (`_SPA_ROUTES` in `webui.py`) plus the matching one in
`router.js`, and both test files will catch a one-sided change immediately.

**The `/v1/*` pin, stated exactly:** an unmapped `/v1/...` path returns
the identical plain JSON 404 it always did — `{"detail": "Not Found"}` —
never the HTML shell, and (see the table above) every other `/v1/*`
status code (307, 401, 404, 405) is completely undisturbed by the web UI
being mounted or not. `tests/test_webui.py` pins the whole table, both
against the real `web/` directory and against a synthetic one, and against
a build with `VAULT_WEB_DIR` pointed at a directory that does not exist.

**Path traversal.** No custom guard was needed: a path outside the known
exact routes and the `/css`/`/js` prefixes has no matching route at all,
so no filesystem lookup happens for it (`/../api/vault_api/config.py`,
`/.git/config`, ...). A traversal attempt that DOES fall under `/css` or
`/js` (`/css/../../vault_api/config.py`, its `%2e%2e`-encoded form, ...)
is caught by Starlette's own `StaticFiles.lookup_path`, which resolves the
joined path and refuses anything whose realpath escapes the mounted
directory (`os.path.commonpath` check) — battle-tested library code, nine
variants pinned in `tests/test_webui.py::test_path_traversal_attempts_return_404`.

### Cache-Control

- `index.html` — always `Cache-Control: no-cache` (forces revalidation on
  every load), set directly by the `_serve_index` handler and identical
  whether reached via `/`, `/index.html`, or one of the three view routes
  (`/library`, `/downloads`, `/settings`) and their nested-path variants.
  A stale cached app shell after a deploy is the one caching mistake worth
  actively avoiding.
- Every other static asset (served from the `/css` and `/js` mounts, via
  `_AssetStaticFiles.file_response`) — `Cache-Control: public, max-age=300,
  must-revalidate`. Short and revalidate-friendly rather than long: this
  is a no-build, no-bundler SPA (stack decision, see above), so a
  `.js`/`.css` file has no content-hashed filename to bust a long cache
  with after a deploy. A `.js`/`.css` file that legitimately hasn't
  changed still costs a client one conditional-GET round trip every 5
  minutes at most; one that DID change is never served stale for longer
  than that window.

### Security headers, including CSP

`install_security_headers` attaches an `@app.middleware("http")` that adds
the same headers to **every** response — API and UI alike, and
unconditionally (even when no `web/` directory exists to serve):
`Content-Security-Policy`, `X-Content-Type-Options: nosniff`,
`X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
`Permissions-Policy` (camera/microphone/geolocation/payment all denied).

**The CSP is self-only, with no `'unsafe-inline'` anywhere**
(`default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'
data:; font-src 'self'; connect-src 'self'; base-uri 'none'; form-action
'self'; frame-ancestors 'none'; object-src 'none'`). This was possible
without any carve-out because `web/`'s HTML, as shipped by this work
package, uses **zero** inline `<script>` tags, `<style>` tags, or inline
`style="..."` attributes — every visual rule lives in
`web/css/theme.css` / `web/css/app.css`, and every dynamic value the app's
own JS sets later (WP 4a.2+: progress bar widths, live percentages) will
go through the CSSOM (`element.style.prop = value`), which CSP's
`style-src` does **not** restrict — only literal inline markup is
covered. This is worth stating explicitly because the frozen design mockup
(`docs/design/vault-app-mockup.html`) DOES use inline `style="..."`
attributes throughout (it predates this CSP and was never meant to ship
as-is — see its own NOTES.md); `web/`'s HTML is a fresh implementation
that intentionally avoids the pattern so the strict CSP above didn't need
loosening. If a later 4a.x package finds it genuinely needs an inline
style or script, it must extend `_CSP` in `webui.py` explicitly and say
why in a comment — never add a blanket `'unsafe-inline'`.

**Deliberate extension (WP 4a.3): `img-src` also allows exactly one
external host.** The Library view fetches real Steam capsule artwork by
appid (`web/js/lib/cover-art.js`,
`https://cdn.akamai.steamstatic.com/steam/apps/{appid}/library_600x900.jpg`
— the same 2:3 portrait shape the frozen mockup's fake covers already
used), so `img-src` is now `'self' data:
https://cdn.akamai.steamstatic.com` — one named host, no wildcard, no
second CDN alias. A blocked or missing image (offline LAN, no route to the
host — a real homelab deployment) degrades to a styled fallback tile
client-side; the CSP entry only widens what the browser is ALLOWED to
load, it is never a hard dependency. Pinned by
`tests/test_webui.py::test_csp_img_src_allows_self_data_and_exactly_the_steam_cdn_host`
against the exact directive value, not a substring check.

### App shell contents (WP 4a.1 scope: scaffolding only)

`web/index.html` + `web/js/app.js` wire up: a bottom nav with the mockup's
three destinations — Library / Downloads / Settings, matching
`_SPA_ROUTES` above 1:1 (Clients is NOT a nav item; it is WP 4a.7's sheet,
reached from the bypass banner in the frozen mockup, not the bottom nav —
see the routing section above for the review round that corrected this), a
History-API router (`web/js/router.js`) that swaps the active view on nav
clicks and browser back/forward, three placeholder views
(`web/js/views/{library,downloads,settings}.js`, each just a heading and a
one-line "this ships in WP 4a.x" note), the status-icon component
(`web/js/components/status-icon.js` — glyph set, colours, sizes and motion
rules ported from `docs/design/vault-app-mockup.html` round 7, including
the `prefers-reduced-motion` kill switch in `web/css/theme.css`), and a
toast component (`web/js/components/toast.js`) with no callers yet. No API
calls, no data fetching, no polling — that is WP 4a.2's scope entirely.

## Running natively (Windows dev, no Docker until WP 1.9)

```powershell
cd api
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env   # then edit VAULT_API_KEY
.venv\Scripts\python -m uvicorn vault_api.main:create_app --factory --host 0.0.0.0 --port 8000
```

Verify:

```powershell
curl http://localhost:8000/v1/health
# {"status":"ok"}

curl http://localhost:8000/v1/games
# 401 - missing X-Api-Key

curl -H "X-Api-Key: <your key>" http://localhost:8000/v1/games
# []

curl -H "X-Api-Key: <your key>" -X PUT http://localhost:8000/v1/mapping/441 ^
     -H "Content-Type: application/json" -d "{\"appid\": 440, \"app_name\": \"Team Fortress 2\"}"
# {"depotid":441,"appid":440}

curl -H "X-Api-Key: <your key>" http://localhost:8000/v1/games
# [{"appid":440,"name":"Team Fortress 2","status":"idle","last_prefill_at":null,"depot_count":1,"size_bytes":null}]

curl -H "X-Api-Key: <your key>" http://localhost:8000/v1/games/440
# {"appid":440,"name":"Team Fortress 2","status":"idle","last_prefill_at":null,
#  "depots":[{"depotid":441,"shared":false,"size_bytes":null}],"size_bytes":null}
# (size_bytes fields added in WP 1.5; null here because nothing has been
# written to VAULT_CACHE_ROOT yet in this walkthrough. needs_force added in
# WP 3.4 -- omitted from this WP 1.3-era transcript; would read "true" for
# this never-filled app, see "needs_force" above)

curl -H "X-Api-Key: <your key>" http://localhost:8000/v1/games/999999
# 404

curl -H "X-Api-Key: <your key>" -X DELETE http://localhost:8000/v1/mapping/441/440
# 204 (mapping removed, apps row untouched)

curl -H "X-Api-Key: <your key>" -X DELETE http://localhost:8000/v1/mapping/441/440
# 404 (already gone - second delete of the same pair)

curl http://localhost:8000/v1/ping
# 404 - removed in WP 1.3, real routes now exist

curl http://localhost:8000/docs
curl http://localhost:8000/openapi.json
# both 404 - disabled
```

All of the above was run against a live `uvicorn` instance during WP 1.3
review, not assumed from library docs.

### WP 1.4: prefill via curl (live-verified)

Requires `VAULT_STEAMPREFILL_PATH` and a SteamPrefill that has been logged in
once interactively.

```powershell
curl -H "X-Api-Key: <your key>" -X POST http://localhost:8000/v1/prefill ^
     -H "Content-Type: application/json" -d "{\"appids\": [440, 730, 440]}"
# 202
# [{"appid":440,"job_id":1,"status":"queued","deduplicated":false},
#  {"appid":730,"job_id":2,"status":"queued","deduplicated":false},
#  {"appid":440,"job_id":1,"status":"queued","deduplicated":true}]

curl -H "X-Api-Key: <your key>" -X POST http://localhost:8000/v1/prefill ^
     -H "Content-Type: application/json" -d "{\"appids\": [440]}"
# 202 - [{"appid":440,"job_id":1,"status":"queued","deduplicated":true}]  (no new job)

curl -H "X-Api-Key: <your key>" -X POST http://localhost:8000/v1/prefill ^
     -H "Content-Type: application/json" -d "{\"appids\": []}"
# 422 (also for [0], a non-list, or an unknown body field)

curl -H "X-Api-Key: <your key>" "http://localhost:8000/v1/jobs?limit=5"
# [{"id":2,"appid":730,"type":"prefill","status":"done","created_at":"...","started_at":"...","finished_at":"..."},
#  {"id":1,"appid":440,"type":"prefill","status":"done", ...}]

curl -H "X-Api-Key: <your key>" http://localhost:8000/v1/jobs/1
# {"id":1,"appid":440,...,"status":"done","log_excerpt":"...\n[vault-api] Depot mapping
#  updated (replace-semantics for this app): added=[441, 442] removed=[] unchanged=[]"}

curl -H "X-Api-Key: <your key>" http://localhost:8000/v1/jobs/9999
# 404

curl -H "X-Api-Key: <your key>" http://localhost:8000/v1/games/440
# {"appid":440,...,"status":"done","last_prefill_at":"...",
#  "depots":[{"depotid":441,"shared":false},{"depotid":442,"shared":true}],"size_bytes":null}
```

Run against a live `uvicorn` instance with a fake SteamPrefill
(`tests/stub_prefill.py`) pointed at by `VAULT_STEAMPREFILL_PATH`; the depot
directories, the shared-depot flag for the depot both apps filled, and the
`Config/selectedAppsToPrefill.json` content were all checked on disk
afterwards. `401` without a key and `404` for `/docs` still hold.

### WP 1.5: sizes and cache summary via curl (live-verified)

Against a live `uvicorn` instance with `VAULT_CACHE_ROOT` pointed at a
directory seeded by hand with depot files (441: 1,000,000 bytes, mapped only
to appid 440; 900: 5,000,000 bytes, mapped to both 440 and 730 — the shared
case; 555: 250,000 bytes, deliberately left unmapped) and mappings created via
the existing `PUT /v1/mapping/{depotid}`:

```
curl -H "X-Api-Key: <your key>" http://localhost:8000/v1/games
# [{"appid":440,"name":"Team Fortress 2","status":"idle","last_prefill_at":null,
#   "depot_count":2,"size_bytes":6000000},
#  {"appid":730,"name":"Counter-Strike 2","status":"idle","last_prefill_at":null,
#   "depot_count":1,"size_bytes":5000000}]

curl -H "X-Api-Key: <your key>" http://localhost:8000/v1/games/440
# {"appid":440,"name":"Team Fortress 2","status":"idle","last_prefill_at":null,
#  "depots":[{"depotid":441,"shared":false,"size_bytes":1000000},
#            {"depotid":900,"shared":true,"size_bytes":5000000}],
#  "size_bytes":6000000}

curl -H "X-Api-Key: <your key>" http://localhost:8000/v1/games/730
# {"appid":730,...,"depots":[{"depotid":900,"shared":true,"size_bytes":5000000}],
#  "size_bytes":5000000}

curl -H "X-Api-Key: <your key>" http://localhost:8000/v1/cache/summary
# {"total_bytes":6250000,
#  "top_consumers":[{"appid":440,"name":"Team Fortress 2","size_bytes":6000000},
#                    {"appid":730,"name":"Counter-Strike 2","size_bytes":5000000}],
#  "unmapped_depots":{"count":1,"size_bytes":250000},
#  "free_disk_bytes":119640584192}

curl http://localhost:8000/v1/cache/summary
# 401 - missing X-Api-Key header (router-level auth, no route added here forgets it)
```

`total_bytes` (6,250,000 = 1,000,000 + 5,000,000 + 250,000) counts depot 900
exactly once; `size_bytes` on the two games (6,000,000 + 5,000,000 =
11,000,000) sums to MORE than that, because the shared depot is counted fully
into both — exactly the documented asymmetry (see "Per-game size calculation"
above). `unmapped_depots` correctly picked up depot 555, which has no mapping
row at all.

### WP 1.6: per-game deletion via curl (live-verified)

Against a live `uvicorn` instance with `VAULT_CACHE_ROOT` seeded by hand (441:
1,000,000 bytes and 442: 2,000,000 bytes mapped only to appid 440; 900:
5,000,000 bytes mapped to both 440 and 730 — the shared case; 555: 250,000 bytes
left unmapped) and `VAULT_WORKER_POLL_SECONDS=30` so a queued job stays queued
long enough to demonstrate the 409:

```
curl -H "X-Api-Key: <your key>" http://localhost:8000/v1/games
# [{"appid":440,...,"depot_count":3,"size_bytes":8000000},
#  {"appid":730,...,"depot_count":1,"size_bytes":5000000}]

curl -X DELETE http://localhost:8000/v1/cache/440
# 401 - missing X-Api-Key

curl -H "X-Api-Key: <your key>" -X DELETE http://localhost:8000/v1/cache/0
# 422 - appid must be >= 1

curl -H "X-Api-Key: <your key>" -X DELETE http://localhost:8000/v1/cache/999999
# 404 {"detail":"Unknown appid 999999 - vault-api tracks no such app, so there is nothing to delete."}

curl -H "X-Api-Key: <your key>" -X DELETE http://localhost:8000/v1/cache/440
# 200
# {"appid":440,
#  "deleted_depots":[{"depotid":441,"size_bytes_freed":1000000},
#                    {"depotid":442,"size_bytes_freed":2000000}],
#  "skipped_shared":[{"depotid":900,"shared_with":[730]}],
#  "failed":[],"total_bytes_freed":3000000}
# on disk afterwards: depot/ contains only 555 and 900 - the shared depot and the
# unrelated unmapped one both survived

curl -H "X-Api-Key: <your key>" http://localhost:8000/v1/games/440
# {"appid":440,"name":"Team Fortress 2","status":"idle","last_prefill_at":null,
#  "depots":[{"depotid":441,"shared":false,"size_bytes":null},
#            {"depotid":442,"shared":false,"size_bytes":null},
#            {"depotid":900,"shared":true,"size_bytes":5000000}],
#  "size_bytes":5000000}
# mapping rows kept (depot list intact), the deleted depots report null, and the
# app's total is now just the kept shared depot

curl -H "X-Api-Key: <your key>" http://localhost:8000/v1/cache/summary
# {"total_bytes":5250000,...}   (was 8250000 - immediately, without waiting out
#                                VAULT_SIZE_CACHE_TTL: the cache was invalidated)

curl -H "X-Api-Key: <your key>" http://localhost:8000/v1/mapping
# [{"depotid":441,"appid":440},{"depotid":442,"appid":440},
#  {"depotid":900,"appid":440},{"depotid":900,"appid":730}]

curl -H "X-Api-Key: <your key>" -X DELETE http://localhost:8000/v1/cache/440
# 200 - clean no-op: deleted_depots report size_bytes_freed 0, failed is empty
# {"appid":440,"deleted_depots":[{"depotid":441,"size_bytes_freed":0},
#                               {"depotid":442,"size_bytes_freed":0}],
#  "skipped_shared":[{"depotid":900,"shared_with":[730]}],
#  "failed":[],"total_bytes_freed":0}

curl -H "X-Api-Key: <your key>" -X DELETE http://localhost:8000/v1/cache/730
# 200 - the other direction: 900 is still shared with 440, so nothing is deleted
# {"appid":730,"deleted_depots":[],
#  "skipped_shared":[{"depotid":900,"shared_with":[440]}],
#  "failed":[],"total_bytes_freed":0}

curl -H "X-Api-Key: <your key>" -X POST http://localhost:8000/v1/prefill ^
     -H "Content-Type: application/json" -d "{\"appids\": [440]}"
curl -H "X-Api-Key: <your key>" -X DELETE http://localhost:8000/v1/cache/440
# 409 {"detail":"Prefill job 1 for app 440 is queued. Deleting depots while they
#      are being downloaded would delete under an active write - retry once that
#      job has finished (poll GET /v1/jobs/{id})."}
```

Depot 900's chunk file was checked on disk after all of the above: still present,
still 5,000,000 bytes. The `cache-delete` audit lines quoted under "Audit trail"
are from this same run.

### WP 2.4: agent reports via curl (live-verified)

Against a live `uvicorn` instance with `VAULT_AGENT_REPORT_KEEP=3` and a
tracked app 440 (depot 441, 1,000 bytes on disk) seeded via
`PUT /v1/mapping/441` first:

```
curl -X POST http://localhost:8000/v1/agent/installed \
     -H "Content-Type: application/json" -d '{"client_id":"gaming-pc","appids":[440]}'
# 401 - missing X-Api-Key

curl -H "X-Api-Key: <your key>" -X POST http://localhost:8000/v1/agent/installed \
     -H "Content-Type: application/json" -d '{"client_id":"gaming-pc","appids":[440,730,570]}'
# {"client_id":"gaming-pc","received":3,"added":[440,570,730],"removed":[],"first_report":true}

curl ... -d '{"client_id":"gaming-pc","appids":[440,570,1234]}'
# {"client_id":"gaming-pc","received":3,"added":[1234],"removed":[730],"first_report":false}

curl ... -d '{"client_id":"gaming-pc","appids":[440,440,570,1234,440]}'
# {"client_id":"gaming-pc","received":3,"added":[],"removed":[],"first_report":false}
#   duplicates deduped -> received 3, and no phantom change

curl ... -d '{"client_id":"gaming-pc","appids":[]}'
# {"client_id":"gaming-pc","received":0,"added":[],"removed":[440,570,1234],"first_report":false}
#   an empty library is a legitimate report: everything is reported as removed

curl ... -d '{"client_id":"steam-deck","appids":[440]}'
# {"client_id":"steam-deck","received":1,"added":[440],"removed":[],"first_report":true}
#   another client's first report is diffed against ITS OWN history, not gaming-pc's

curl ... -d '{"client_id":"pc\nX","appids":[440]}'      # 422 (control character)
curl ... -d '{"client_id":"pc"}'                        # 422 (appids is required)

curl -H "X-Api-Key: <your key>" http://localhost:8000/v1/clients
# [{"client_id":"gaming-pc","first_seen":"2026-08-05T20:44:34Z",
#   "last_reported_at":"2026-08-05T20:44:36Z","app_count":0},
#  {"client_id":"steam-deck","first_seen":"2026-08-05T20:44:36Z",
#   "last_reported_at":"2026-08-05T20:44:36Z","app_count":1}]

curl http://localhost:8000/v1/clients
# 401 - missing X-Api-Key
```

Checked in the database and on disk after that run:

- `agent_reports` holds **3** rows for `gaming-pc` (the 4 reports minus the
  pruned oldest, `pruned=1` in the log) and 1 for `steam-deck`;
- `apps` still holds exactly the one row seeded by hand
  (`440 | Team Fortress 2 | idle`) — the reports created nothing, even though
  app ids 570, 730 and 1234 were reported and then "removed";
- `jobs` is empty — nothing was queued;
- `depot/441/chunk/aa` is still there, still 1,000 bytes, and `GET /v1/games`
  reports the unchanged `status`/`size_bytes` after the removal reports.

The audit log lines quoted under "Removals are SURFACED, never acted on" are
from this same run.

## Tests

```powershell
cd api
.venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

CI-gated (the CI run shows the count); no network and no Steam login required.

Covers: health returns `ok` without a key and leaks nothing else; every
registered route requires `require_api_key` except `/v1/health` (route-walk
regression test, `test_security.py`); `/docs`, `/redoc`, `/openapi.json` all
return 404; schema creation is idempotent
(tables exist, `schema_version` has exactly one row after calling `init_db`
twice, expected indexes and the reshaped `agent_reports` columns are
present, WAL + busy_timeout pragmas are active); missing/blank
`VAULT_API_KEY` fails loudly via `Settings.from_env`.

WP 1.3 additions:

- `test_auth.py`: 401 without/with-wrong key and 200 with the correct key
  against `/v1/games` (the auth dependency's real target now that
  `/v1/ping` is gone); a **non-ASCII key returns 401, not 500**
  (`test_non_ascii_key_is_rejected_with_401_not_500` — the carry-over fix,
  reproduced by sending raw non-ASCII UTF-8 bytes as the header value via
  `httpx.Headers`, since `str` headers with non-ASCII characters can't even
  be sent by httpx/TestClient).
- `test_mapping.py`: `upsert_mapping` unit tests (app auto-creation, same
  pair upserted twice is idempotent, a `None` name doesn't clobber an
  existing name, the same depotid under two appids is allowed/shared) plus
  HTTP tests for `PUT`/`GET`/`DELETE /v1/mapping` (401 without a key, upsert
  then list round-trip, `app_name` is optional).
- `test_games.py`: `GET /v1/games` happy path (empty list, depot count,
  `size_bytes: null`) and `GET /v1/games/{appid}` happy path (depot list,
  `shared` flagging across two apps sharing one depot, `404` for an unknown
  appid), both with a 401-without-key check. Plus (WP 4c) `last_manifest_check`
  is `null` for a never-checked app in both shapes, and round-trips a value
  written straight to the DB column byte-for-byte (no timezone/format
  mangling); the write-path itself (which outcomes set it) is pinned in
  `test_worker.py`, not here.
- `test_concurrency.py` (B1 regression): ~30 concurrent mixed requests
  (games list/detail, mapping PUT) via `httpx.ASGITransport` +
  `asyncio.gather`, asserting every response is non-5xx — see the
  `check_same_thread=False` note above for what this pins.

WP 1.3-review additions (B2 + should-fixes), also in `test_mapping.py`:

- **Additive mapping is pinned**, not just implemented: two `PUT`s of the
  same depotid under different appids leave both mappings in place and
  both apps report `shared: true` for that depot
  (`test_put_same_depot_different_appids_adds_both_and_flags_shared`).
- `DELETE /v1/mapping/{depotid}/{appid}`: 401 without a key, removes
  exactly the targeted pair (sibling pair and the `apps` row survive),
  `404` for a pair that doesn't exist, plus a DB-level unit test for
  `mapping.delete_mapping`'s `True`/`False` return value.
- Validation: `depotid <= 0`, `appid <= 0` (on both `PUT` and `DELETE`),
  and an unrecognized `PUT` body field all `422`.
- `test_security.py`'s existing route-walk test covers the new `DELETE`
  route automatically — no test change needed there, verified by rerunning
  it after adding the route.

WP 1.4 additions:

- `tests/stub_prefill.py` — a **fake SteamPrefill executable** generated per
  test. It reads its selection from `Config/selectedAppsToPrefill.json` next to
  itself (so the tests prove the runner really uses the verified mechanism),
  writes fake depot chunk files into a temp cache root, records its argv and its
  start/finish timestamps, and can reproduce: success, an arbitrary non-zero
  exit, the **verbatim** not-logged-in output of the real binary, a hang, a
  chatty run (>16 KB output), and a no-op "already cached" run. Emitted as a
  `.cmd` shim on Windows and a `sh` shim elsewhere — both verified to pass argv
  through, hand the child an EOF stdin, and propagate the exit code.
- `test_jobs_api.py`: 401 on all three new routes; `202` + one queued job per
  appid; the `apps` row is created at enqueue (so `GET /v1/games/{appid}` is 200
  while queued); dedupe against a queued job, duplicate ids inside one body, and
  the fact that a *finished* job doesn't block a new one; eight distinct `422`
  bodies; `?limit` bounds; newest-first ordering; `log_excerpt` absent from the
  list and present in the detail; `404` for an unknown job id.
- `test_jobs_queue.py`: FIFO claiming, `started_at`/`finished_at` transitions,
  dedupe against a *running* job, tail-truncation of the log excerpt, the
  crash-recovery rule (running → error, queued untouched, `apps.status`
  repaired, and the recovered job no longer blocking a new enqueue), rollback +
  `isolation_level` restoration of `immediate_transaction`, and two
  **concurrency** tests: 6 threads racing to claim 40 jobs hand out each job
  exactly once, and 8 threads enqueueing the same appid create exactly one job.
- `test_prefill_runner.py`: missing/nonexistent `VAULT_STEAMPREFILL_PATH` is a
  per-job failure not an exception; **the verified argv
  `["prefill", "--force", "--no-ansi"]` and the selection file are asserted**;
  stdin is EOF in the child; the selection is rewritten per job; non-zero exit
  (with stderr merged in), not-logged-in detection with the actionable hint,
  timeout kill, abort-on-request, ANSI stripping, >16 KB output captured without
  a pipe deadlock; `scan_depots`/`diff_depots` (missing root, non-numeric and
  empty depot dirs ignored, new + changed detected, removed ignored); and
  `apply_observed_mapping`'s three rules (replace within the app, shared depots
  of other apps preserved, empty observation changes nothing).
- `test_worker.py`: full stack through HTTP with the stub — success updates job,
  `apps.status`, `last_prefill_at` and the mapping; the ADR-0003 replace +
  shared-depot case end to end; the "nothing observed" case; **strict
  one-at-a-time FIFO execution proven by non-overlapping subprocess timestamps**;
  failure exit code, not-logged-in, and timeout paths all leaving the mapping
  untouched; a hanging job not stalling the queue; a missing SteamPrefill path
  failing the job while `/v1/health` and `/v1/games` keep working; startup
  crash-recovery through the lifespan; shutdown aborting a running job instead
  of waiting out a 600 s timeout; and a guard that no worker runs when the
  lifespan doesn't (which is what keeps the other test modules deterministic).
- `test_concurrency.py`: the mixed hammer grew to 50 parallel requests including
  `POST /v1/prefill` and `GET /v1/jobs`, plus a new test that hammers reads
  while the real worker thread writes in the background. Both pin the
  access-violation fix described under "Connection handling".
- `test_db.py` / `test_config.py`: the two new `jobs` indexes, the v1 → v2
  in-place upgrade, the newer-database downgrade guard, thread-confinement of
  connections, and the three new settings incl. their loud validation.

WP 1.5 additions:

- `test_sizes.py`: the disk walk (`walk_file_stats` finds nested files, a
  missing path yields nothing) and `scan_depot_dir_bytes`/
  `scan_depot_signatures` (per-depot byte sums, non-numeric/empty dirs
  ignored, **pinned equal to `prefill.scan_depots`'s output** — the shared-walk
  carry-over fix, not just "both happen to work"); `app_size_bytes`'s four
  cases (unmapped → `None`, uncached → `None`, partially cached → sum of what
  exists, a shared depot counted fully into two different apps' sums);
  `SizeCache` TTL behavior with an injected clock (stale within the window,
  recomputes after expiry, `invalidate()` forces a recompute regardless of
  TTL) and that `total_bytes` counts a depot once even though two apps map it;
  `free_disk_bytes` on both an existing path and one that needs to walk up to
  an existing ancestor; `build_cache_summary`'s total/top-consumers/unmapped
  shape, the top-10 cap and sort order, and that it goes through the injected
  `SizeCache` (a second call inside the TTL sees stale data; after
  `invalidate()` it doesn't).
- `test_games.py`: three new tests with real files under a real
  `VAULT_CACHE_ROOT` (a fresh `TestClient`/`Settings` per test, not the shared
  `client` fixture, since the cache root path matters here) — `GET /v1/games`
  reports a real non-null size once depots are on disk; `GET /v1/games/{appid}`
  reports per-depot `size_bytes` (null for a depot that's mapped but not yet
  written) alongside the app total; a depot shared between two apps counts its
  full size into both.
- `test_cache_summary.py`: 401 without a key; an empty cache reports
  `total_bytes: 0`, `top_consumers: []`, `unmapped_depots: {0, 0}`, and a real
  positive `free_disk_bytes`; a seeded cache (two apps, one shared depot, one
  unmapped depot) reports the correct total, per-app top consumers, and the
  unmapped depot's count/bytes.
- `test_prefill_runner.py`: two new tests for `write_selected_apps`'s atomic
  write (carry-over fix) — exactly one file (never a stray `.tmp`) is left in
  `Config/` after a write, and after two successive writes.
- `test_worker.py`: a successful job invalidates the shared `SizeCache` — with
  the *default* 60s TTL (not shortened for the test), so this only passes if
  the worker actively calls `invalidate()`; a passive TTL expiry could never
  complete inside the test's runtime.
- `test_concurrency.py`: `GET /v1/cache/summary` (which takes `SizeCache`'s
  own lock and, on a miss, walks the depot tree) joined both existing hammers
  — the mixed-endpoint gather (60 requests, up from 50) and the
  worker-writing-into-the-same-cache-root-while-HTTP-reads test.
- `test_db.py` / `test_mapping.py` / `test_games.py`: updated, not added —
  `init_db`'s reordered downgrade-guard check (WP 1.5 carry-over fix) doesn't
  change any DB test's observable behavior, so the existing tests continue to
  pin it unchanged; the depot list now always carries `size_bytes` (`null` in
  these fixtures, since none of them write real cache files), so three
  existing assertions were updated to include it.

WP 1.6 additions:

- `test_cache_delete.py` (new, the bulk of this package) — three layers:
  - **Path guards, as pure functions:** empty / whitespace / `None` cache root,
    `"/"` and the platform root, a cache root without a `depot/` directory and
    one where `depot` is a *file*, a UNC-style path, a *relative* cache root
    (the default `./cache` is relative, so this is the normal case), a cache root
    reached through a directory link (the resolved path becomes the deletion
    base), plus 17 parametrized poisoned depot ids (`"../../etc"`, `"441/../.."`,
    `".."`, `"0x1c1"`, `"441; rm -rf /"`, `"C:\Windows"`, `0`, `-5`, `None`,
    `True`, `1.5`, …) each asserted to raise instead of yielding a path, and
    `coerce_positive_id`'s `bool` exclusion (`True == 1` would otherwise become
    "delete depot 1").
  - **Link mechanics against the real filesystem** (no mocks): `shutil.rmtree`
    refusing a linked directory while its target survives, `remove_depot_dir`
    unlinking a link-like depot directory and sparing the target, a link *nested*
    inside a depot tree not being followed, and `depot_dir_bytes` reporting `0`
    for a link (so freed bytes are never overstated). Each of these has a
    **junction-specific** twin (`make_junction`, Windows-only) because
    `make_dir_link` prefers a symlink where it can create one and the two behave
    differently exactly where it matters.
  - **The endpoint end to end:** 401 without a key, 422 for `appid < 1`, 404 for
    an unknown app and for an app with no mappings, 409 while a prefill job is
    queued (and *not* for a job belonging to a different app), the happy path
    (exclusive depots removed, an unrelated unmapped depot and the `depot/` root
    itself untouched, exact freed byte counts), shared-depot protection **in both
    directions**, `status` reset to `idle` with `last_prefill_at` cleared, mapping
    rows surviving, size-cache invalidation asserted with the **default 60 s TTL**
    (so only an explicit `invalidate()` can make it pass), a second `DELETE` as a
    clean no-op, four racing `DELETE`s via `httpx.ASGITransport` (all 200, bytes
    freed exactly once in total, no exception), **partial failure** with one depot
    made undeletable (an open file handle on Windows, a read-only directory mode
    on POSIX — skipped as root) asserting the other depot still deleted and the
    failure reported per depot, an end-to-end symlinked *and* junctioned depot
    directory pointing OUTSIDE the cache root (link gone, foreign data intact,
    `size_bytes_freed: 0`), and both cache-root guard refusals surfacing as `500`
    with nothing deleted. Plus a **real poisoned database row**: a non-numeric
    `depotid` inserted as TEXT (which SQLite's INTEGER affinity permits,
    asserted via `typeof()`) is reported in `failed` with `depotid: 0`, the
    healthy depot is still deleted, and a file that the poisoned value pointed
    at outside the cache root survives.
- Two tests added for the WP 1.6 review findings, both verified to fail against
  the pre-fix code:
  - `test_a_partial_failure_leaves_the_app_in_error_not_done` — an open handle
    on a file in the **middle** of the only mapped depot (so `rmtree` really
    does remove part of the tree before raising) must end at `status='error'`
    with `last_prefill_at` cleared. Against the old rule the app stayed `done`
    with its timestamp intact.
  - `test_a_depot_that_becomes_shared_between_plan_and_removal_survives` — the
    exact TOCTOU interleaving: `deletion.plan_deletion` is wrapped so a second
    app maps depot 900 *after* the (real) plan is computed and *before* any
    removal, with nothing inside the deletion loop patched. The depot must
    survive and appear in `skipped_shared` with the fresh owner list; without
    the recheck it was deleted (50 bytes of another game's content).
  Plus unit tests for the recheck seam itself: a depot kept when `co_owners`
  reports a new owner, a recheck that *raises* leaving the depot alone and
  reporting it in `failed`, `load_co_owners` returning only other apps, and the
  ASCII-digit exactness rule (`" 441 "`, `"1_0"`, `"٤٤١"`, `"²"` all rejected
  even though `int()` accepts the first three).
- `test_sizes.py`: the carry-over fixes are pinned, not just implemented — a
  **real directory symlink** as a depot directory is now visible to
  `scan_depot_signatures` (this test fails against the pre-fix code: the depot is
  absent from the result entirely), a link *inside* the tree is not followed, the
  junction-specific version of that (which is the one that fails without the
  explicit junction check, since `is_dir(follow_symlinks=False)` reports a
  junction as a directory), and `is_link_like` detecting both kinds. The
  `build_cache_summary` tests were updated for its new `SizeSnapshot` parameter.
- `test_concurrency.py`: the mixed hammer grew to 70 parallel requests with
  `DELETE /v1/cache/{appid}` in the mix against a pre-seeded depot tree — several
  of them target the same app ids the concurrent mapping `PUT`s are creating, so
  deletes race each other, race the size-cache scan, and race the mapping writer.
  Assertion unchanged: nothing may 5xx.

WP 2.4 additions:

- `test_agent_reports.py` (new, the bulk of this package):
  - **The diff:** first report (everything added, nothing removed), a
    consecutive report's additions *and* removals, an unchanged library
    reporting neither, an empty library removing everything (and the empty
    snapshot being stored, so a reinstall afterwards reads as an addition), an
    empty *first* report, duplicate ids deduped (asserted both in `received`
    and in the stored row, plus "the dedupe must not look like a change next
    time"), and per-client isolation in both directions.
  - **The ADR-0002 boundary:**
    `test_a_removal_is_logged_but_changes_no_app_state` seeds a tracked app
    with a mapping row, a real chunk file and `status='done'`, reports the app
    as removed, and asserts exactly one audit log line *and* that `status`,
    `last_prefill_at`, the depot list, the file on disk and the job queue are
    all unchanged. `test_a_report_does_not_create_app_rows` pins that reported
    app ids do not become tracked games.
  - **Validation:** 25 parametrized `422` bodies (missing/empty/too-long
    `client_id`, newline/tab/NUL, leading/trailing/only whitespace, `.`/`..`,
    a non-string, missing/null/non-list `appids`, appid `0`/negative/float/
    null/non-numeric string/`true`/`false`/`true` mixed into a valid list,
    > 10 000 ids, an extra field), plus
    `test_a_missing_appids_field_never_reads_as_an_empty_library` which
    asserts the stored snapshot is untouched by the rejected request, and a
    positive list of accepted ids (64 chars, spaces inside, non-ASCII).
  - **Retention:** `keep + 2` reports leave exactly the newest `keep`
    snapshots in insertion order while every intermediate diff stays correct
    across the pruning boundary; retention is per client; and
    `prune_reports` clamps a nonsense `keep=1` to 2 (otherwise every report
    would be a first report).
  - **Units:** `normalize_appids`; `latest_snapshot` picking the newest row by
    **rowid** against deliberately non-monotonic `reported_at` values (two
    identical timestamps plus one that goes backwards); an unparseable stored
    snapshot degrading to `first_report` with a WARNING instead of a permanent
    500, and the next report having a healthy predecessor again; and
    non-integer entries in a stored snapshot being dropped — including `true`,
    which must **not** become app id 1 (`bool` is an `int` subclass).
  - **Concurrency:**
    `test_parallel_reports_from_one_client_form_one_unbroken_chain` (described
    under "Agent reports → Concurrency"). Verified to fail 5/5 runs against a
    version with the `BEGIN IMMEDIATE` transaction removed — 4 of 8 racing
    requests then claimed to be the first report.
- `test_clients_api.py` (new): 401 without a key; an empty list before any
  report; one row per client with `app_count` following the **latest**
  snapshot and the exact field set (so a Phase-3 addition is a deliberate
  change); `app_count: 0` for an empty library; `first_seen` being the oldest
  *retained* report after pruning (the documented consequence, pinned); and
  `app_count: null` for an unreadable latest snapshot.
- `test_concurrency.py`: the mixed hammer grew to **90** parallel requests —
  `POST /v1/agent/installed` (two client ids across ten iterations, so several
  race the *same* client's diff transaction against the prefill enqueues, the
  deletes and the size-cache scan) and `GET /v1/clients` joined it, and
  `/v1/clients` also joined the worker-writing-in-the-background test.
- `test_config.py`: `VAULT_AGENT_REPORT_KEEP`'s default, an override, and its
  loud rejection of `1`, `0`, `-3` and a non-integer — with `2` accepted, so
  the boundary itself is pinned.

WP 2.4-review additions:

- `test_no_body_endpoint_accepts_a_boolean_as_an_app_id` asserts `422` for a
  boolean app id on **all three** endpoints that take one
  (`/v1/agent/installed`, `/v1/prefill`, `/v1/mapping/{depotid}`), which is
  what pins the shared `AppId` type rather than one endpoint's behavior; plus
  a unit test for `validation.reject_bool` and three more parametrized `422`
  bodies. Verified against the pre-fix annotation: the `[true]`, the
  `[440, true]` and the all-endpoints test all fail without
  `BeforeValidator(reject_bool)` (`[false]` passes either way — it coerces to
  `0` and trips `ge=1`).
- Two parametrized `422` bodies for `client_id` `"."` and `".."`, and `"..."`
  added to the accepted-ids list so the rule stays exactly two values wide.

Each of the three carry-over fixes and both link guards were verified by
temporarily reverting the fix and re-running the affected tests: the
symlinked-depot test, the junction-walk test and the three link-deletion tests
all fail against the pre-fix code, so they are genuine regression guards rather
than tests that happen to pass.

WP 3.8 additions (`tests/test_gc_execute.py`, 76 tests) — this is
deletion-class code, so the emphasis is on what must NOT happen:

- **Dry run really deletes nothing**: the whole cache tree *and* the manifest
  archive are snapshotted (path → size) before and after, and compared. The
  same snapshot technique proves "deleted exactly the planned set and nothing
  else" for an executing run — the set difference must be exactly the two
  planned orphan files, byte-for-byte.
- **Skipped depots lose no file of any kind**, chunks and redundant manifest
  copies alike; a poisoned co-owner row likewise leaves the depot untouched.
- **TOCTOU**: an end-to-end test queues an execute job, then changes the world
  (a new manifest is archived and recorded that needs a chunk which *was* an
  orphan, while a previously-kept chunk drops out), then runs the job — and
  asserts the *fresh* plan governed, i.e. the exact opposite of the
  enqueue-time picture for both files. A second, structural test asserts
  `run_gc` has no `plan` parameter at all.
- **Racing removals**: a concurrent depot-wide removal is released from a
  `threading.Barrier` against a live GC run, parametrized 10x and flake-hunted
  by running the module isolated **30x** (30/30 green — full-suite green means
  nothing for timing bugs, docs/LEARNINGS.md). The deterministic half asserts a
  chunk removed by somebody else frees **0** bytes for this run.
- **Partial-failure honesty**: one orphan is made undeletable, the other is
  removed; the run must report `error` with `chunks_removed=1
  bytes_freed=400` — the bytes it claims freed really were freed, and the file
  it reports as failed really is still on disk.
- **Fourteen mutations, each killed by a named test** (the list is in the work
  package report): the endpoint's dry-run default, both status gates, the
  chunk-id re-validation, the link refusal, the `ok` rule, `run_gc`'s execute
  branch, the `needs_force` scoping, dedupe's byte-identity check, dedupe's
  keeper verification, `job_deletes`' NULL handling, the already-gone
  accounting, `parse_bin_payload`'s now-required expected ids, and the worker's
  refusal to guess an unknown job type.

WP 3.8b additions (`tests/test_gc_execute.py` +14, `tests/test_config.py` +3):

- **How time is faked, and why that way.** `os.utime` sets atime/mtime and
  explicitly **not** ctime, so a test cannot age a file into the past. Sleeping
  would be slow, flaky and could only produce seconds of age. These tests
  therefore write real chunk files, read their **real** `st_ctime` back off the
  disk, and inject a fake `now` into the predicate: the comparison under test
  runs against a genuine platform ctime on both Windows and Linux, and only the
  reference point — the one value a deployment's clock supplies anyway — is
  synthetic. The boundary test anchors the clock on the *newest* of the two
  orphans, since files written microseconds apart do not share a ctime.
- **Young survives byte-for-byte**: whole-tree snapshot plus a content
  comparison of the protected chunks, with the reason string pinned verbatim
  (`stored 0.0 days ago, grace window is 14 days`). **Old is deleted.**
- **The boundary is half-open**: exactly 14 days old is collected, one second
  younger is held, and a *future* ctime (clock skew) is held with a clamped
  `0.0 days` reading rather than a negative one.
- **Fail-closed both ways**: an `lstat` that fails on a file that is there holds
  the chunk back; an unambiguously absent file (plan built first, chunk removed
  before execution) is still reported `already_gone` with 0 bytes rather than
  "protected"; an invalid chunk id is still the loud `refused_unsafe_name`
  problem that makes the job `error`, not a quiet hold-back.
- **Dry runs report it**: `held_back`/`would_delete` totals, per-chunk `~` lines
  with reasons, the bounded list (25 extra orphans → 10 lines plus an "and N
  more" tail with the totals still last), and nothing deleted.
- **The wiring**: `VAULT_GC_GRACE_DAYS=0` → `NO_EXCLUSIONS` and the old
  behaviour end-to-end through a queued job; `=14` → the job finishes `done`,
  frees nothing, sets no `needs_force`, and says why; and the **shipped
  default** (taken from `Settings`' own default, not from a value the test file
  chose) protects a freshly stored chunk.
- **Ten mutations, each killed by a named test**: the fail-closed stat branch
  (→ `return None`, and → re-adding an `os.path.lexists` recheck, which
  `lexists` would collapse into "gone"), the comparison direction
  (`<` → `>`), the boundary (`<` → `<=`), the `N=0` bypass, the invalid-name
  route, the dry-run exclusions, `run_gc_job`'s `exclusions=` argument, the
  broken-cache-root protection, and `DEFAULT_GC_GRACE_DAYS` itself.

WP 4a.6r additions (`tests/test_steam_relay.py`, plus two migration tests and
one `EXPECTED_TABLES` entry in `tests/test_db.py`):

- **The mutation pin the work package asked for by name**:
  `test_relay_is_off_by_default_returns_409_not_success` — default-enabling
  the relay (skipping or inverting the "is a key configured" check) makes
  this test fail, on both relay routes.
- **The key never appears in a response or a log line.** Grep-style checks:
  `VALID_KEY not in resp.text` on the set/status/error-path responses, and
  `VALID_KEY not in caplog.text` after a simulated upstream failure with a
  key configured (`test_upstream_failure_never_leaks_the_key_into_logs`).
  `test_http_fetch_error_messages_never_contain_the_key_or_query_string`
  pins the same property one layer down, against a mocked `URLError`.
- **Cache correctness under a key change**:
  `test_clearing_the_key_immediately_reverts_to_409_even_with_a_warm_cache`
  — a cached answer from before `DELETE /v1/steam/key` must never make an
  unconfigured relay look configured; the router checks the key BEFORE the
  cache. `test_repeated_requests_within_the_ttl_reuse_the_cache` and
  `..._cache_independently` (for the two endpoints) pin the opposite
  direction — the cache actually avoids a second upstream call.
- **Validator edge cases**: `TestValidSteamWebApiKey`/`TestValidSteamId64`
  parametrize the rejections `docs/LEARNINGS.md`'s "Parsers" section calls
  out by name — surrounding whitespace, non-ASCII (Arabic-Indic) digit
  look-alikes, `bool`, off-by-one length, one-below/one-above the SteamID64
  individual-account range.
- **Hostile-upstream parsing, mirroring `test_oracle.py`'s shape**: a
  private-profile answer with no `games` key is a normal empty result, not
  an error; a non-JSON body and 20 000-deep nested JSON are refused via
  `SteamRelayError`, never a stack overflow; a game/player entry missing its
  id, of the wrong type, or exceeding `MAX_GAMES`/`MAX_PLAYERS` degrades
  with a bounded warning instead of discarding the whole answer; a
  `steamid` in the response that does not match the one requested is
  dropped, never trusted (the same corruption cross-check
  `oracle._app_object` applies to `appid`); only the whitelisted fields
  (`test_only_whitelisted_fields_survive_owned_games`) ever reach the
  parsed result, however innocuous an extra field looks.
- **Storage re-validates what it reads back**:
  `test_a_hand_edited_invalid_key_is_treated_as_not_configured` — a
  corrupted `steam_relay_key.api_key` value (the database is a file an
  operator can edit) is treated as "not configured", never sent to Valve.
- **Schema migration**: `test_init_db_upgrades_a_v11_database_to_v12_in_place`
  and `..._upgrade_to_v12_is_idempotent_if_called_twice` mirror the v9→v10
  oracle migration tests — a v11 file gains one empty `steam_relay_key`
  table and existing rows in other tables survive untouched.

## Persisted settings (settings-API work package, ADR-0009)

`vault_api/settings_store.py` plus `GET`/`PATCH /v1/settings`
(`vault_api/routers/settings.py`) add a **write path** on top of a small,
named set of settings that used to be env-only. `GET /v1/schedule`,
`GET /v1/stats` and the webhook feature are all still read models over the
same `Settings` object they always were — this endpoint is what can now
change a subset of it at runtime.

### Precedence: DB override > env value > built-in default

`settings_store.effective_settings(conn, base)` is the ONE accessor that
resolves this, called at CALL TIME (never cached beyond one request/tick):

1. a row in the `settings` table (schema v13) for that key wins if present;
2. otherwise the env var `Settings.from_env()` read at startup (`base`);
3. otherwise the built-in default baked into `config.py`.

Forcing a value back to the env/default is explicit and first-class:
`PATCH` with `null` for a key **deletes** the override row outright — there
is no tombstone, no "overridden to blank" state — so "does an override
exist" is a plain row-presence check, not a `NULL` check.

### `server_version` — the running server's own version (WP 4e.7, WP VER-1)

`GET /v1/settings` reports one more field, `server_version` (a JSON string,
e.g. `"0.1.0-rc8"`), a sibling of `readonly` at the TOP LEVEL of the
response — not a row in the `settings` list. It is the same value `main.py`
passes to `FastAPI(version=...)`, resolved once per app by
`vault_api.build_info()` (see "Build version" below).

Why this shape, and why here:

- **Not a setting.** It cannot be changed through the API, has no
  db/env/default precedence and no `applies` timing, so it does not belong
  in `SettingInfoOut` — that would either fake those fields or special-case
  a row that behaves differently from every other one. `PATCH /v1/settings`
  rejects `server_version` exactly like any other name it does not
  recognise (`422`, "not a recognised setting") — it needs no entry in
  `OVERRIDABLE_SPECS` or `ENV_ONLY_KEYS` to be refused correctly; adding one
  would wrongly imply it is either overridable or an operator setting.
- **On `/v1/settings`, not a new route.** That endpoint is already
  authenticated and already polled by every frontend that has a settings
  screen, so this costs zero new routes and zero new requests.
- **Deliberately NOT on `GET /v1/health`.** See "Auth" above for the full
  reasoning (fingerprinting risk on the one unauthenticated route).

### Build version (WP VER-1)

Where the version comes from, in order:

| Running as | `VAULT_BUILD_VERSION` | `server_version` |
|---|---|---|
| a published image (`publish.yml`, tag `v0.1.0-rc8`) | `0.1.0-rc8` | `0.1.0-rc8` |
| a locally built image (`docker build`, `docker compose up --build`, `verify-stack.sh`) | `dev` (the Dockerfile default) | `dev` |
| natively from a checkout, or the test suite | absent | `0.1.0` (`vault_api.BASE_VERSION`) |
| any image with a blank or invalid value | e.g. `""`, `1.0 beta` | `0.1.0`, plus one startup WARNING |

Every Dockerfile takes the build args `VAULT_VERSION` (default `dev`) and
`VAULT_COMMIT` (default `unknown`) and bakes them as the runtime env
`VAULT_BUILD_VERSION` / `VAULT_BUILD_COMMIT` and as the OCI
`org.opencontainers.image.version` / `.revision` labels. `publish.yml`'s
`build-version` job passes the tag without its `v` (the same string the
image is tagged with; the publish job checks that equality) and the full
commit SHA; a branch rehearsal passes `dev-<short sha>`, ci.yml's
`image-build` passes `ci-<short sha>`. Nothing reads the labels at runtime.

`vault_api.build_info()` accepts a version of 1-64 characters from
`[0-9A-Za-z._+-]` starting with a letter or digit (the same grammar
`publish.yml` enforces) and a commit of 7-40 lowercase hex characters (or
the literal default `unknown`). Anything else, including a present-but-blank
value, falls back as in the last table row: the version is informational,
so a bad value does not stop vault-api from booting. The env can be
overridden at `docker run` time, which is why the check happens here and
not only in the Dockerfile. vault-api logs `vault-api version X (commit Y)`
once at startup. The commit is not served by any route yet; WP VER-2 adds
`GET /v1/about`.

`vault_api.BASE_VERSION` is the one remaining hand-maintained release
number in `api/`, the fallback above. Bump it together with every `image:`
line in `deploy/compose.yaml` (`${VAULT_IMAGE_TAG:-...}`),
`deploy/tests/verify-stack.sh`'s `TAG=${VAULT_IMAGE_TAG:-...}` and
`deploy/.env.example`'s `#VAULT_IMAGE_TAG=...` example;
`tests/test_version_pin.py` fails by name for each one that drifts. The
Dockerfile labels are no longer literals, so they are no longer on that
list; `tests/test_ver_1_build_version.py` pins their ARG/ENV/LABEL wiring
in all four Dockerfiles, `publish.yml`'s build args and version script, and
the fallback above.

### Which keys are overridable, and when a change takes effect

| Key | Env var | `applies` | Why |
|---|---|---|---|
| `vault_name` | `VAULT_NAME` | `restart-required` | Only read by `WebhookNotifier._build_body`, which holds a fixed `Settings` snapshot for the notifier's lifetime — see "The honest gap" below. Value rule (shared with the env var): at most 64 characters after trimming, printable only — anything else is `422` here and a startup refusal for `VAULT_NAME`. Upgrade caveat: an existing value longer than 64 characters or containing a non-printable character such as a tab stops vault-api booting until it is fixed |
| `schedule_window` | `VAULT_SCHEDULE_WINDOW` | `next_sweep` | `vault_api/scheduler.py`'s tick loop resolves `effective_settings` fresh every ~60s tick, using the connection the tick already opened |
| `schedule_interval_minutes` | `VAULT_SCHEDULE_INTERVAL_MINUTES` | `next_sweep` | Same tick-loop resolution as `schedule_window` |
| `schedule_client_stale_days` | `VAULT_SCHEDULE_CLIENT_STALE_DAYS` | `next_sweep` | Same tick-loop resolution |
| `auto_gc` | `VAULT_AUTO_GC` | `immediately` | `worker.py`'s `_maybe_queue_auto_gc` resolves `effective_settings` using the connection the just-finished job is already on, so the very next completed prefill sees a changed value |
| `sweep_include_cached` | `VAULT_SWEEP_INCLUDE_CACHED` | `next_sweep` | Same tick-loop resolution as the other `schedule_*` keys — see "Sweep target set — installed PLUS cached" above (WP 4d) |
| `webhook_url` | `VAULT_WEBHOOK_URL` | `restart-required` | See "The honest gap" below |
| `webhook_events` | `VAULT_WEBHOOK_EVENTS` | `restart-required` | Same as `webhook_url` |
| `steam_library_steamid` | `VAULT_STEAM_LIBRARY_STEAMID` | `immediately` | WP API-FEAT-1 (ADR-0016 freeze exception). Nothing in vault-api caches it: the web library (WP WEB-FEAT-1) and, later, the app read it per request from `GET /v1/settings`, so every device shows the same account. Returned as a JSON **string** (17 digits exceed JavaScript's safe-integer range); `PATCH` accepts it only as a JSON string too: a JSON number gets `422`, since a JavaScript sender may already have rounded it and the API cannot tell. Same grammar as the env var (`steam_relay.valid_steamid64`). Not secret, not redacted |

**Blank is a valid override value for `schedule_window`, `webhook_url` and `steam_library_steamid`**
(mirroring `Settings.from_env`'s own handling of all three): it means
"disabled" (or, for the SteamID, "not set"), which is what makes "force the
scheduler or the webhook off, or the SteamID empty, via the API even
though the env var still configures one" expressible at all, rather than a
gap where only `null` (revert to env) is available and env itself cannot be
overridden downward. Every other numeric/enum key requires an explicit,
non-blank value — clearing those is spelled `null`, not an empty string.

### The honest gap: `vault_name` / `webhook_url` / `webhook_events` are restart-required

`WebhookNotifier` (`vault_api/webhooks.py`) is constructed once per process
with a fixed `Settings` snapshot, and — more fundamentally —
`WebhookNotifier.start()` only spawns its delivery thread if
`settings.webhook_enabled` was true **at boot** (`vault_api/main.py`'s
lifespan). A `PATCH` that turns the feature on from off cannot start a
thread that already decided not to exist. Wiring live reload into that
thread (a second long-lived connection, its own refresh cadence, its own
tests) is real, scoped work, deliberately left as a follow-up rather than
claimed here. `PATCH` still validates and persists these three keys — the
override is real and `GET` reports it — but it takes a restart to actually
change what gets delivered. This is a deliberate, documented choice for this
work package, not an oversight: the alternative (silently claiming
`applies: "immediately"` while nothing actually changed until a restart)
was rejected as dishonest.

### Env-only keys (`PATCH` refuses these by name, `422`, distinct detail)

Bootstrap and security settings never become overridable (ADR-0009 decision
5): `vault_api_key`, `db_path`, `cache_root`, `steamprefill_path`,
`steamprefill_cache_dir`, `manifest_archive_dir`, `web_dir`,
`settings_readonly` itself. All but `vault_api_key` also appear in
`GET /v1/settings` as informational rows (`env_only: true`, `applies:
"restart-required"`) so a settings screen can show "this exists but only the
environment controls it" instead of silently omitting the key.
**`vault_api_key` never appears in any `GET /v1/settings` response at all —
not even redacted** — it is the authentication secret itself, a strictly
higher bar than the webhook URL's userinfo.

**`relay_expose_playtime` / `relay_expose_last_played` (WP 4h.0, ADR-0010)
share the same allowlist mechanism for a DIFFERENT reason.** They are not
bootstrap or security settings — see "The privacy gate" under "Steam Web
API relay" above and `docs/adr/0010-relay-privacy-gate-env-only.md` for the
actual rationale (a privacy opt-out must not be backed by a store, the
`vault-db` Docker volume, that can be lost independently of the environment
that is meant to govern it). They still behave exactly like every other row
in this section for the purposes of `GET`/`PATCH /v1/settings`: reported as
informational, env-only rows; `PATCH` refuses them with the same distinct
detail.

### Validation reuses the exact startup grammars — no duplicated parsing

`PATCH` validates each value with the SAME function `config.py` applies to
the corresponding env var at startup (ADR-0009 decision 4):
`schedule_window` uses `schedule_window.parse_window`; `schedule_interval_minutes`/
`schedule_client_stale_days` use `config.parse_strict_int` (ASCII-digits-only,
`nan`/signs/underscores rejected — the WP 3.12 grammar); `auto_gc` uses
`config.parse_auto_gc` (must be exactly `off`/`dry-run`/`execute`);
`sweep_include_cached` (WP 4d) uses `config.parse_strict_bool` (the same
`1`/`true`/`yes`/`on` / `0`/`false`/`no`/`off` word set `VAULT_SETTINGS_READONLY`
already validates against at startup). A JSON `true`/`false` literal in the
PATCH body never even reaches this grammar — `routers/settings.py` rejects a
JSON boolean outright (docs/LEARNINGS.md "Parsers": a bool must not be
silently stringified into `"True"`/`"False"`), so `sweep_include_cached` must
be sent as the STRING `"true"`/`"false"` like every other value here.
`config._env_int`/`_env_float`/`_env_auto_gc`/`_env_bool`/`_env_webhook_events`
were each split into a pure `parse_*` half (no env-var name, no `os.environ`
access) plus a thin env-reading wrapper, specifically so both the startup path
and this module call the identical function — a bad value cannot reach the
`settings` table with a grammar even slightly looser than the one the
scheduler/worker would apply hours later.

**One documented exception: `webhook_url`.** WP 3.13 deliberately never
validates `VAULT_WEBHOOK_URL` as a URL at startup — it is treated as trusted
operator configuration, and a malformed value simply fails per-delivery at
WARNING (see "Webhooks → SSRF / trust posture" above). No startup grammar
therefore exists for `PATCH` to reuse. `config.validate_webhook_url` is a
**new** function (scheme must be `http`/`https`, blank means disabled) that
exists ONLY for this endpoint — a human typing a value into a form right now
gets a `422` immediately for "that is not even a URL" rather than a silent
warning hours later. `Settings.from_env()` is intentionally left unchanged;
this is a deliberate, narrow addition, not a change to WP 3.13's shipped
startup behavior.

### `VAULT_SETTINGS_READONLY` — the operator hard-lock

`VAULT_SETTINGS_READONLY=1` (env-only, by definition — a flag that disables
the settings-write API cannot itself be re-enabled through that same API)
makes `PATCH /v1/settings` answer `403` unconditionally, checked BEFORE the
request body is even inspected — a locked-down/GitOps deployment gets the
same answer regardless of what was sent. `GET /v1/settings` still works
(`{"readonly": true, ...}`), and the documented escape hatch below still
works, since it never goes through the API at all. Accepted spellings:
`1`/`true`/`yes`/`on` (true), `0`/`false`/`no`/`off`/blank (false,
case-insensitive) — anything else is refused at startup like every other
enum-shaped setting in `config.py`.

**Not covered by the lock: `PUT`/`DELETE /v1/steam/key`.** The Steam Web
API relay key (see "Steam Web API relay") is stored through its own routes
in `routers/steam.py`, and those stay writable with
`VAULT_SETTINGS_READONLY=1`. The lock exists to "restore pure env
semantics", and the relay key has no env source to restore to (there is no
`VAULT_STEAM_RELAY_KEY`); gating the routes today would leave a readonly
deployment with no way to configure the relay at all. Gating them together
with an env source for the key is post-release work (ADR-0009 addendum
2026-09-30, `docs/security/threat-model.md` §7).

### Redaction

`webhook_url` is redacted in every `GET`/`PATCH` response exactly like the
WP 3.13 logging redaction (`webhooks.redact_url`): a userinfo-carrying URL
renders as `https://***@host/path`. This applies to **both** the
`effective` value AND the `fallback` value (what clearing the override
would revert to) — an env-configured webhook URL with embedded credentials
must not leak its secret through the fallback field just because no
override is currently active. `PATCH` still accepts the full, un-redacted
value in the request body (it has to, to store something useful); only
responses redact.

### The sqlite3 escape hatch

Per ADR-0009 decision 3: if the settings table itself ends up in a state an
operator wants to bypass the API entirely for (a stuck
`VAULT_SETTINGS_READONLY=1` deployment with no other admin path, or simply
cleaning up while debugging), stop vault-api and edit the database directly:

    sqlite3 vault.db "DELETE FROM settings WHERE key = 'auto_gc';"
    # or, to clear every override at once:
    sqlite3 vault.db "DELETE FROM settings;"

This is documented, not hidden: it is the same trust posture the rest of
this database already has (api/README.md "Auth" — a single-operator
homelab service where whoever can reach this file can already read
`VAULT_API_KEY`/`VAULT_WEBHOOK_URL` out of it in plaintext). A row written
this way is still re-validated on every read (`effective_settings`,
`describe_settings`): a value that no longer passes the current grammar is
logged and treated as absent rather than crashing a live request or the
scheduler tick.

### What this work package deliberately did NOT do

- ~~No `deploy/` changes.~~ **Done.** `VAULT_SETTINGS_READONLY` is forwarded
  in `deploy/compose.yaml`'s `vault-api` service (`${VAULT_SETTINGS_READONLY:-false}`,
  off/read-write by default) and documented in both `api/.env.example` and
  `deploy/.env.example` -- the follow-up this section originally deferred.
  Pinned by `api/tests/test_p1_compose_env_defaults.py`.
- **No live reload for `vault_name`/`webhook_url`/`webhook_events`** — see
  "The honest gap" above.
- **No UI.** Phase 4a's settings screen is expected to build on this
  endpoint instead of "set this env var" hints, but no web UI changes are
  part of this work package.
- **No sweep-mode flag yet, at the time this WP shipped.** ADR-0009's
  consequences section named the Phase 4d "keep the cache current" sweep-mode
  switch as "one more overridable key when it lands". **Landed since:**
  `sweep_include_cached` (`VAULT_SWEEP_INCLUDE_CACHED`) is now in
  `OVERRIDABLE_SPECS` — see "Sweep target set — installed PLUS cached" in the
  Scheduler section above and the table entry earlier in this section.
