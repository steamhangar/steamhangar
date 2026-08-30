# SteamHangar — Project Plan

A Steam game cache with true per-game management — self-hosted, Docker-first.

Status: IMPLEMENTATION — Phases 0–3, 4a, and 4b (4b.1–4b.10) complete;
pre-release (see §11 Next Steps) · License: Apache-2.0

> SteamHangar is a community project and is not affiliated with Valve Corporation.
> "Steam" is a trademark of Valve Corporation.

---

## 1. Vision & Problem Statement

LanCache is the de-facto standard for LAN caching of game downloads, but it has
a well-known structural limitation: the cache is a generic nginx HTTP cache
(files stored under hashed cache keys), which makes **deleting individual games
from the cache impossible**. Community tools like lancache-manager reconstruct
the game-to-file mapping after the fact by parsing access logs — clever, but
error-prone and maintenance-heavy.

SteamHangar inverts the approach: the **depot ID is already part of the Steam
CDN URL** (`/depot/<depotid>/chunk/<hash>`). By storing the cache path-faithfully
(nginx `proxy_store` instead of `proxy_cache`), the game mapping becomes part of
the directory structure from the start. Deleting a game = deleting its depot
folders. No log parsing, no key reconstruction, no heuristics.

**Target audience:** Homelab operators and LAN party organizers who want to know
which game occupies how much space — and want to clean up selectively.

**Deliberate scope cut:** Steam only. No Epic/Battle.net/Riot multi-service
support like LanCache — this keeps the URL-schema problem manageable and the
project focused. (Extensibility via a plugin architecture is kept open, but not
for v1.)

---

## 2. Requirements

| # | Requirement | Component |
|---|---|---|
| A1 | Prefill Steam games onto the server remotely | Backend + Prefill |
| A2 | Serve downloads at LAN speed from the cache at home | Cache Core |
| A3 | Browse the Steam library visually in an Android app (covers, names) | Android App |
| A4 | Per-game cache status badge (cached / running / not cached) | Backend + App |
| A5 | Per-game download trigger from the app (start → done status) | Backend + App |
| A6 | App "homecall" works over Tailscale (embedded tsnet), Twingate, or a public domain — user-selectable connectivity profile | Android App |
| A7 | Prefill updates automatically during the day (cron) | Scheduler |
| A8 | Cron criterion: games actually installed on the gaming machines (Windows PC and SteamOS/Linux devices such as Steam Deck / Steam Machine); removals are detected and reflected | PC Agent |
| A9 | Delete individual games from the cache to free up space | Cache Core + Backend |
| A10 | Per-game size overview | Backend |
| A11 | Community-ready: Docker-first, documented, licensed, CI | Project Infra |
| A12 | Detect clients silently bypassing the cache (DoH/DoT, IPv6, Linux client quirks) and surface it | Backend |
| A13 | Reclaim space from outdated chunks per game (manifest-based garbage collection) | Backend |
| A14 | Works without a pre-existing local DNS server (bundled optional DNS container, or DNS-free hosts-file mode) | vault-dns / PC Agent |

---

## 3. Architecture

```
                    ┌─────────────────────────────────────────┐
                    │            Cache Server                 │
   Android App      │                                         │
  ┌────────────┐    │  ┌─────────────┐    ┌───────────────┐   │
  │ Compose UI │    │  │ vault-core   │    │ vault-api     │   │
  │ Steam Lib  │────┼─▶│ nginx        │    │ FastAPI       │◀──┼── PC Agent
  │ tsnet      │    │  │ proxy_store  │◀───│ SQLite        │   │   (gaming PC,
  └────────────┘    │  │ /cache/depot/│    │ prefill ctrl  │   │    reports
        │           │  └─────────────┘    └───────┬───────┘   │    installed
        │           │         ▲                   │           │    games)
        ▼           │         │           ┌───────▼───────┐   │
  Steam Web API     │   DNS rewrite       │ SteamPrefill  │   │
  (library+covers,  │   *.steamcontent    │ (subprocess/  │   │
   regular internet)│   .com → server     │  container)   │   │
                    └─────────────────────────────────────────┘
```

### Components

**vault-core** — the cache itself
- nginx container with `proxy_store`: Steam CDN responses are stored
  path-faithfully under `/cache/depot/<depotid>/...`
- No LRU, no automatic eviction — cleanup is deliberately explicit
  (that's the feature, not the flaw)
- A lean, purpose-built nginx config set; NOT a LanCache fork — only the
  DNS-redirection principle is shared (rewrite `*.steamcontent.com` to the
  cache server via any local DNS: AdGuard Home, Pi-hole, dnsmasq, ...)

**vault-dns** — optional bundled DNS (for users without a local DNS server)
- dnsmasq container, enabled via a Compose profile (`--profile dns`)
- Answers `*.steamcontent.com` with the cache IP, forwards everything else
  to a configurable upstream. IMPORTANT (verified in Phase 0): modern
  dnsmasq (2.9x) FORWARDS non-matched record types upstream, so `address=`
  alone leaks AAAA answers and IPv6-capable clients silently bypass the
  cache — the zone must additionally be declared `local=/steamcontent.com/`
  so AAAA queries get a local NODATA answer. Required design element for
  vault-dns.
- Not needed if the user already runs AdGuard Home, Pi-hole, dnsmasq or
  Unbound — a rewrite there does the same job (recommended for homelabs)

**vault-api** — brain & API
- FastAPI + SQLite (single file, no DB container — deliberately simple
  for easy adoption)
- Responsible for:
  - Depot→app mapping (from SteamPrefill data / Steam PICS)
  - Prefill orchestration (SteamPrefill as subprocess/sidecar, job queue)
  - Per-app status tracking (idle / running / done / error / stale)
  - Per-game size calculation (du over depot folders, cached)
  - Per-game deletion (remove the app's depot folders)
  - Scheduler (configurable daytime window, runs over the installed list)
- REST API (see section 5)

**vault-agent** — PC listener
- Small static Go binary (ADR-0005: single-file distribution, trivial
  cross-compilation for windows/amd64, linux/amd64, linux/arm64) on the
  gaming machine — Windows PC first, plus a Linux/SteamOS variant
  (Steam Deck, Steam Machine) in Phase 2
- Reads `steamapps/appmanifest_*.acf` from all library folders
  (parses `libraryfolders.vdf` for multiple drives); the ACF/VDF format
  is identical on Linux/SteamOS, only library paths and packaging differ
  (XDG paths under `~/.local/share/Steam`, systemd user service instead
  of a scheduled task)
- Reports the FULL list of installed app IDs periodically (e.g. every
  30 min) via HTTP POST to vault-api — over Tailscale. Removed titles
  are derived server-side by diffing against the previous report — the
  agent stays stateless and dumb by design
- Runs as a scheduled task / optional tray icon; config: one URL + API key
- Deliberately dumb: read + report only, no control logic
- **Optional hosts-file mode (opt-in, requires admin rights):** writes a
  `lancache.steamcontent.com → cache IP` entry into the Windows hosts file.
  The Windows Steam client checks this hostname itself and uses it as a
  cache when it resolves — no DNS server needed at all. Windows-only
  (the Linux/Steam Deck client does not perform this lookup).
  *Note: this hostname is hardcoded by Valve in the Steam client and lives
  on Valve's own `steamcontent.com` domain — it is the client's built-in
  cache-discovery interface, not a LanCache-project dependency. It cannot
  be renamed.*

**vault-app** — Android app
- Kotlin + Jetpack Compose
- Steam identity via "Sign in with Steam" (OpenID against Valve's login
  page — the app never sees credentials, see ADR-0004); library + persona
  via vault-api's Steam relay since WP 4h.4 (ADR-0004 addendum 2: the
  device-local Steam Web API key is gone — one operator key, server-side,
  and WP 4h.0's privacy gate covers both frontends); covers still load
  from Steam's public CDN
- **Connectivity profiles** (user-selectable, abstracted behind one API-client
  interface — the server never knows or cares which one is used):
  - **Embedded Tailscale (tsnet):** Go Mobile `.aar` bridge, auth-key based.
    Zero-config for the user beyond pasting an auth key. Tailscale only.
  - **System VPN:** plain HTTPS to an internal hostname/IP; works with the
    Tailscale app, Twingate client, WireGuard, or any other VPN the OS
    provides. (Twingate has no embeddable SDK — this profile covers it.)
  - **Public domain:** plain HTTPS to a public URL fronted by the user's
    reverse proxy (Traefik, Caddy, Nginx Proxy Manager, Cloudflare Tunnel).
    Requires TLS; strongly recommends forward-auth/OIDC in front of the API
    in addition to the API key.
- Grid view with status badges, multi-select, trigger, polling until "done",
  delete function with size display ("Game X occupies 43 GB — delete?")

---

## 4. Cache Design (Core Innovation)

### Storage layout
```
/cache/
└── depot/
    ├── 441/                    ← depot ID (belongs to app 440, TF2)
    │   └── chunk/
    │       ├── <sha>...
    │       └── <sha>...
    ├── 442/
    └── manifest/               ← manifest responses stored separately
```

### Depot→app mapping
- A game consists of multiple depots (content, languages, DLC)
- Source: Steam PICS via SteamKit — SteamPrefill already uses this;
  vault-api keeps its own mapping table in SQLite and updates it during
  prefill (SteamPrefill knows the mapping at download time anyway)
- Fallback: manual mapping via the API (edge cases / delisted games)

### Deletion
```
DELETE /cache/{appid}
  → mapping: appid → [depotids]
  → rm -rf /cache/depot/<each depotid>
  → reset status to "idle"
```
Shared depots (redistributables, shared content): before deleting, check
whether a depot ID is mapped to multiple tracked apps → skip those and
report them in the result ("2 depots shared with game Y, not deleted").
Exception (ADR-0003 addendum): a shared depot whose co-owning apps ALL have
no cache content (idle, never prefilled, no active job) is the last cached
remnant — it IS deleted and reported distinctly, otherwise its bytes would
be unreclaimable forever once every co-owner has been deleted.

### Staleness / updates
- vault-api stores the manifest ID of the last prefill per app
- The scheduler periodically compares against the current manifest ID
  (Steam API) → if it differs: status "stale", an update prefill fetches
  only the changed chunks
- App badge logic: green=current, yellow=running, orange=stale, gray=not cached

---

## 5. Known LanCache Pain Points SteamHangar Addresses

Documented community issues (GitHub issues, Steam forums, LanCache docs) that a
Steam-only, prefill-first design can solve better:

| Pain point | How SteamHangar addresses it |
|---|---|
| **No per-game visibility or deletion** — cache is opaque hashed storage | Core design: path-faithful depot storage, per-game size, per-game delete |
| **Slow cache-miss downloads** — nginx slice mechanics + CDN back-off behave poorly with the Steam client; users resort to multi-IP workarounds | **Prefill-first philosophy, hybrid miss path (Phase 0 decision, ADR-0001):** misses are stored synchronously (`proxy_store`, no slice mechanics — measured overhead within noise) AND trigger an async prefill job that completes the affected app. Prefill remains the primary fill mechanism |
| **Clients silently bypassing the cache** — Linux/Steam Deck clients not honoring `lancache.steamcontent.com`, DoH/DoT ignoring local DNS, ISP DNS hijacking | **Bypass detection:** vault-api tracks per-client hit statistics; a client that reports installed games (agent) but never appears in cache logs triggers a visible warning in app/API. Setup docs cover the Linux-client and DoH caveats explicitly |
| **IPv6 undermines DNS redirection** — clients resolve AAAA records of the real CDN and bypass the cache | Documented stance + setup guidance (block/rewrite AAAA for `*.steamcontent.com` in the local DNS); bypass detection catches the failure case |
| **Stale chunks waste space forever** — a generic HTTP cache never learns that a game update obsoleted old chunks | **Manifest-based garbage collection:** per depot, diff cached chunks against the current manifest and delete orphans — only possible because storage is depot-structured |
| **No insight without extra tooling** (Grafana/ELK stacks or lancache-manager needed) | Stats are first-class API citizens: summary, per-game, per-client — the app is the dashboard |

---

## 6. API Design (vault-api)

| Method | Endpoint | Purpose |
|---|---|---|
| GET | /v1/games | All tracked games: status, size, last prefill |
| GET | /v1/games/{appid} | Detail incl. depot list |
| POST | /v1/prefill | Body: `{appids: [..]}` → create jobs (response marks deduplicated entries) |
| GET | /v1/jobs | Recent jobs, newest first (app polling UI) |
| GET | /v1/jobs/{id} | Job status (for app polling) |
| DELETE | /v1/jobs/{id} | Cancel a queued or running job (Phase 3, user decision 2026-08-06) |
| POST | /v1/jobs/{id}/pause | Pause a running download — terminates the subprocess; cached chunks make resume cheap |
| POST | /v1/jobs/{id}/resume | Resume: re-runs the prefill; already-cached chunks replay as disk-speed HITs |
| GET | /v1/schedule | Scheduler state: window, interval, last/next sweep |
| DELETE | /v1/cache/{appid} | Delete a game from the cache |
| POST | /v1/cache/{appid}/gc | Garbage-collect orphaned chunks (manifest diff) |
| GET | /v1/cache/summary | Total usage, top consumers, unmapped depots, free space |
| GET | /v1/oracle/{appid} | Oracle snapshot: depots, branch manifest gids, staleness hints (WP 3.9; 404-equivalent `enabled:false` when oracle off) |
| POST | /v1/oracle/{appid}/refresh | Explicitly re-query the manifest oracle for one app (never automatic; `ok:false` on oracle failure, never 5xx) |
| DELETE | /v1/oracle/{appid} | Drop stored oracle data for one app |
| GET | /v1/clients | Per-client hit stats incl. bypass warnings |
| DELETE | /v1/clients/{client_id} | Remove a ghost client's rows after a rename (WP AG-1; not a ban — a client that reports again reappears) |
| GET | /v1/stats | Cache-event sweep state: cursor, lines read/skipped, miss-trigger activity, unmapped depot misses (WP 3.11, ADR-0008) |
| POST | /v1/agent/installed | PC agent reports installed app IDs |
| PUT | /v1/mapping/{depotid} | Manual depot→app mapping fallback (additive, see ADR-0003) |
| GET | /v1/mapping | List all depot→app mappings |
| DELETE | /v1/mapping/{depotid}/{appid} | Remove one mapping pair (repair path) |
| GET | /v1/health | Liveness (for external monitoring) |

Auth: static API key in a header (v1). Since everything is only reachable
over the tailnet, this is sufficient; OIDC/forward-auth is a later option
for users who expose the API differently.

---

## 7. Phase Plan

### Phase 0 — Feasibility PoC (CRITICAL, before anything else)
**Goal: verify the central assumption before writing product code.**
- [x] Test setup: nginx with `proxy_store` + hosts-file redirect
      *(run natively on Windows instead of a container — deliberate decision:
      develop natively first, containerize at end of Phase 1; see `poc/`)*
- [x] Route a real Steam download through it and verify:
  - [x] Does Steam consistently use the `/depot/<id>/chunk/<hash>` scheme?
        *(2026-08-04: 187 real-client requests, 100% chunk/manifest
        conformance, zero non-conforming URIs — see
        `poc/steam-client-test/RESULTS-*.md`)*
  - [x] Do range requests work cleanly with `proxy_store`?
    (known risk: `proxy_store` only stores complete responses —
    may need the `slice` module, or chunks may be small enough)
    *(2026-08-04: the real Windows client sent ZERO Range headers
    (~1 MiB chunks, full-body GETs); the CDN edge tested ignores Range
    on miss and always returns full 200; warm-cache ranges served
    natively. Production follow-up: strip client Range upstream +
    store-only-200 guard — see `poc/RANGE-FINDINGS.md`)*
  - [x] Cache hit on second download? Speed LAN-limited?
        *(2026-08-04: 93/93 HIT after uninstall/reinstall, zero upstream
        contact, disk-limited — 81.8 MiB served in ~1 s)*
  - [x] **Miss-handling decision:** synchronous store vs. transparent
    passthrough + async prefill (see pain-points section) — measure both
    *(measured in `poc/MISS-HANDLING-FINDINGS.md`: perf difference within
    noise. 2026-08-05 gate decision: HYBRID — store-on-miss (Phase 1)
    plus miss-triggered prefill completion (Phase 3). See ADR-0001)*
- [x] Run SteamPrefill against the PoC cache — does it fill correctly?
      *(2026-08-04: SteamPrefill v3.7.1 auto-detected the cache via the
      lancache-heartbeat contract and prefilled path-faithfully — layout
      cross-check PASS, repeat requests served as HITs. Notable: 0 manifest
      requests through the cache, and `?nocache=1` speed probes that
      production vault-core should honor as a cache bypass — see
      `poc/steamprefill/RESULTS-STEAMPREFILL-*.md`)*
- [x] Verify behavior of the Linux/Steam Deck client (known upstream quirk:
      does not perform the `lancache.steamcontent.com` lookup like Windows)
      *(2026-08-05, Ubuntu 26.04/WSL2, current stable client: the quirk is
      OUTDATED — the Linux client DOES perform lancache discovery and sent
      3574 requests through the cache with real CDN Host headers, zero
      Range headers. Cross-client sharing confirmed: chunks cached by the
      Windows client served to the Linux client as HITs. A transient
      upstream-IP outage also produced 502/stall evidence motivating
      production upstream design (honor client Host header, short
      connect timeouts, retry) — see
      `poc/linux-client-test/RESULTS-20260805-083353.md`)*
- **Abort criterion:** If `proxy_store` fails on range requests with no clean
  workaround → fall back to Plan A (unmodified LanCache + a manager layer in
  the spirit of lancache-manager; the rest of the project stays usable as-is)

### Phase 1 — vault-core + vault-api (server MVP)
- [x] Production-ready nginx config (log rotation, healthcheck) —
      implements the Phase-0 requirements from ADR-0001: lancache
      heartbeat, Range-strip + 200-only store guards (incl. retried-200
      handling), nocache=1 bypass, client-Host upstream with short
      timeouts/retry, store-on-miss
      *(caveat: log rotation is documented, implemented with the
      container in WP 1.9; tmp/ and cache/ must share one volume)*
- [x] FastAPI skeleton, SQLite schema, depot mapping import
      *(WP 1.2/1.3: skeleton with secure-by-default auth, schema v1,
      manual mapping endpoints per ADR-0003; the prefill-driven mapping
      import lands with the orchestration in the next package)*
- [x] Prefill orchestration (SteamPrefill subprocess, job queue, one job at a time)
      *(WP 1.4: selection-file driven — SteamPrefill has no app-id CLI;
      hybrid miss-trigger itself remains Phase 3 per ADR-0001)*
- [x] Size calculation + deletion incl. shared-depot protection
      *(WP 1.5/1.6: TTL-cached sizes; deletion with path guards,
      link-safe removal, execute-time shared recheck closing the
      TOCTOU, settle-and-recheck against racing deletes; mapping rows
      survive deletion by design. Opus + Fable double review)*
- [x] Docker Compose (2 services + volume), .env convention, pinned image tags
      *(WP 1.9: tag+digest-pinned images, preflight guards, json-file log
      rotation, SteamPrefill v3.7.1 with TOFU checksum and a dedicated
      HOME volume, 62-check container verification incl. real CDN
      MISS/HIT through the Linux container. Opus + Fable double review)*
- [x] Optional vault-dns container behind a Compose profile (`--profile dns`)
      *(WP 1.9: envsubst entrypoint with IPv4 validation and address=/
      local= re-assertion, fail-closed loopback default binding)*
- [x] **MVP test: prefill a game via curl, query its size, delete it — no app**
      *(2026-08-05, WP 1.7: full HTTP-only cycle against the real Steam
      CDN through the resident vault-core — prefill 79.7 MiB via job
      queue, size and summary internally consistent to the byte,
      deletion freed exactly the app's size, disk verified. Evidence:
      `core/tests/mvp/RESULTS-*.md`. Note: unattended real-account
      prefills are blocked by the local permission classifier — the
      run is operator-executed by design)*

### Phase 2 — vault-agent (PC listener) — COMPLETE 2026-08-09
- [x] ACF/VDF parser (appmanifest + libraryfolders, multiple drives)
      *(WP 2.1 Python reference with spec-parity pins, ported to Go in
      WP 2.1b per ADR-0005; reference implementation removed at close-out
      (WP 2.6), embedded EAppState bit table preserved in go/acf/acf.go)*
- [x] HTTP reporter with retry (tolerate VPN/network outages)
      *(WP 2.2: 46/46 validation parity, ctx-cancellable backoff, 429
      retryable, no secret in flag defaults)*
- [x] Optional hosts-file mode (opt-in, admin rights, clean uninstall path)
      *(WP 2.3: marker-block management with byte-exact preservation,
      fail-closed backup, measured Windows ACL write strategy, UTF-16/
      symlink refusal, SIGKILL/ENOSPC fault-injection evidence. Note:
      Phase 0 proved CURRENT Linux clients also perform lancache
      discovery, so hosts mode works beyond Windows; Opus + Fable
      double review. Real-Windows verification done 2026-08-06: hosts
      status on the live machine correctly reported the user's manual
      Phase-0 entry as a conflict without touching anything)*
- [x] Document Windows scheduled-task setup, optional installer script
      *(WP 2.6: per-user task via install-task.ps1/uninstall-task.ps1,
      API key in an owner-only env file, never on the command line
      (verified with a canary key against schtasks + task XML),
      icacls-before-write ACL, idempotent re-install, 36-assertion
      real-machine harness; Python reference implementation removed per
      ADR-0005 addendum, fixtures kept, EAppState bit table preserved
      in go/acf/acf.go)*
- [x] Linux/SteamOS agent variant (Steam Deck / Steam Machine): library
      discovery under `~/.local/share/Steam`, systemd user service
      packaging, SteamOS read-only-rootfs-friendly install (home dir only)
      — see ADR-0002
      *(WP 2.5: OnCalendar=*:0/30 + Persistent=true (monotonic timers
      proved a silent no-op for catch-up), umask-077-first secret env
      files)*
- [x] vault-api: scheduler uses the installed list as the prefill set;
      server-side diff of consecutive agent reports surfaces removed
      titles (status update / optional cleanup hint, see ADR-0002)
      *(WP 2.4 report chains ordered by rowid + WP 3.5 window sweeps)*

### Phase 3 — Scheduler & Update Logic — COMPLETE 2026-08-09

All packages merged: 3.1–3.6 (manifests, ingestion, summary, needs_force,
scheduler, last-remnant), 3.7/3.8/3.8b (GC plan/execute/grace window),
3.9 (oracle, branch session), 3.10 (event log), 3.11 (event sweep),
3.12 (job control), 3.13 (webhooks). Suite at close: 1239 green.

Closing plan (2026-08-09, user decision "implement everything"): remaining
packages are 3.8b grace window (merged) · 3.9 oracle incl. open-beta
branch manifests (ADR-0007 addendum B) · 3.10 vault-core cache-event log
(ADR-0008) · 3.11 event sweep = miss-trigger + client hit stats/bypass ·
3.12 job control + optional auto-GC · 3.13 webhooks. api/-packages run
strictly serially; 3.10 (core/) runs in parallel.

Branch-dispatch structure for ALL remaining phases (package briefs,
parallel/serial decisions, merge discipline, open user decisions):
`docs/WORKPACKAGES.md` (2026-08-09).

- [x] Miss-triggered prefill completion: a cache miss on an unknown/partial
      app queues a prefill job for that app (hybrid decision, ADR-0001;
      feed design decided in ADR-0008 → WP 3.10 core log + WP 3.11 sweep)
      *(WP 3.11: non-forced enqueues under cap → active-job → cooldown →
      cached-and-current guards; unmapped/shared/manifest misses counted,
      never triggered)*
- [x] Job outcome honesty: a prefill run that observed zero depots and has
      zero cached bytes must not end 'done' (WP 1.7 finding: SteamPrefill
      exits 0 for unowned apps → green badge for a never-cached game)
      *(WP 3.3: summary parsing with digits-and-separators row detection,
      cp850-safe decode chain, 0/0 ⇒ error)*
- [x] Job control: pause/resume/cancel (`DELETE /v1/jobs/{id}`,
      pause = terminate the SteamPrefill subprocess, resume = re-run —
      already-cached chunks replay as disk-speed HITs, so the cache
      itself is the progress store; user feature decision 2026-08-06)
      *(WP 3.12: distinct 'cancelled' terminal + 'paused' in
      ACTIVE_STATUSES (a paused prefill keeps protecting its shared
      depots), stop_request as a DB column cleared at every
      terminal/parking transition, pause RELEASES the worker slot with
      resume priority via the original job id (documented mockup
      divergence — UI follows backend), cooperative GC cancellation,
      VAULT_AUTO_GC hook, strict numeric env grammar (6 int + 2 float
      settings, nan/inf rejected); schema v8; suite 895 green; Opus
      PASS + Fable PASS)*
- [x] Manifest comparison (stale detection)
      *(ADR-0006: staleness via non-forced prefill (Tier 1), depot_manifests
      latest-per-(app,depot) from WP 3.2 ingestion, needs_force lifecycle
      with CAS-protected clear from WP 3.4)*
- [x] Configurable cron window (e.g. 09:00–17:00, every 3 h)
      *(WP 3.5: schedule_window parsing incl. overnight windows + 24:00 end,
      OnCalendar-style sweeps from agent reports)*
- [x] Manifest-based garbage collection (`/v1/cache/{appid}/gc`, optional
      auto-GC after successful update prefill)
      *(auto-GC half CLOSED — the earlier "still open" note below is
      superseded: WP 3.12 shipped `VAULT_AUTO_GC=off|dry-run|execute`
      (`worker.py::_maybe_queue_auto_gc`, re-read per job so a
      `PATCH /v1/settings` applies without restart, key exposed by
      ADR-0009 and wired through `deploy/compose.yaml` in WP D1). This
      is NOT a Phase-4d blocker any more)*
      *(WP 3.7 done: read-only GC core — `plan_gc()` with exact on-disk
      orphan bytes, shared-depot UNION keep sets, ADR-0007 readiness gate,
      uncached-app exclusion per ADR-0007 addendum, stored-manifest dedupe
      candidates; 86 tests, mutation-tested fail-closed directions,
      verified against the real PoC cache (0 orphans, research-doc parity).
      Endpoint + execution = WP 3.8, Opus + Fable mandatory)*
      *(WP 3.8 done: POST /v1/cache/{appid}/gc as queued job, dry-run by
      default at every layer (StrictBool opt-in, NULL job rows read as
      dry-run), execute-time re-plan by construction (run_gc takes no plan),
      WP 1.6 guard path reused (safe_child_path, remove_file_settling),
      keep-newest dedupe with byte-identity check, needs_force for touched
      depots' owners, partial-failure honesty; schema v7; 687 tests green;
      Opus PASS + Fable PASS. Optional auto-GC after update prefills still
      open)*
- [x] Beta-branch protection for GC (user decision 2026-08-09, ADR-0007
      addendum): (A) recently-stored grace window via st_ctime as a
      ChunkExclusion predicate; (B) open-beta branch manifests join the
      keep set when the oracle (WP 3.9) is enabled
      *(A: WP 3.8b merged. B: WP 3.9 done — opt-in manifest oracle
      (`VAULT_MANIFEST_ORACLE=steamcmd_api`, default OFF, mutation-pinned),
      own tables `oracle_app_state`/`oracle_branch_manifests` (schema v10,
      `depot_manifests` never written — test-pinned), passworded/unknown
      branches never stored, `public` excluded from the keep set, additive
      post-gate union in `gc.py` via generic `extra_manifest_ids` (no oracle
      import; orphans(on) ⊆ orphans(off)), fail-soft everywhere incl. broad
      catch so an oracle error can never fail a GC job; stdlib urllib with
      redirects refused + bounded read; three authed `/v1/oracle/*` routes.
      Renumbered twice by rebases: WP 3.12 took v8, WP 3.11 took v9, so the
      oracle tables are v10 — harmless, they add no column to either. 1193
      tests green, 17 mutations killed by named tests, reviewer re-verified
      with its own mutation set; Opus PASS ×3. Note: response shape modeled on
      api.steamcmd.net, not yet verified against the live service — mismatch
      degrades to oracle-off, documented in api/README)*
      *(WP 4a.6 done 2026-08-11: web settings view over GET/PATCH
      /v1/settings — per-key source/applies captions, only-changed-keys
      PATCH bodies (mutation-pinned), per-field reset, readonly banner;
      Steam identity per A+C — key entry/removal with masked last4,
      typed key cleared from DOM on every path (pinned), SteamID64
      validation with literal boundary fixtures incl. the BigInt
      0x-hex kill, library preview with 409 setup guidance, ADR privacy
      note; 3-step onboarding overlay with dialog semantics (focus trap
      deferred to 4a.8, recorded); serverUrl setting removed (same-origin
      by design); demo parity programmatically diffed against live
      responses; 242 headless tests; Opus PASS + should-fix round)*
- [x] Per-client hit statistics + bypass detection (`/v1/clients`)
      *(WP 3.11, ADR-0008: event sweep with a persisted byte-offset cursor,
      strict 9-field v1 parsing, miss-triggered prefill (cooldown + per-sweep
      cap + unmapped/shared/manifest never trigger), per-address hit stats,
      bypass detection failing toward NOT accusing; schema v9; new
      `GET /v1/stats`. Rotation is best-effort — in the shipped containers
      vault-api may read the log but not truncate it, which is handled,
      counted and surfaced rather than fatal)*
- [x] Optional generic webhook notifications (built as a generic webhook
      feature, not vendor-specific. NOTE: the original wording claimed
      "Discord/Slack/ntfy-compatible" — that overstates what shipped.
      Generic JSON receivers and n8n consume the envelope directly;
      Discord requires `{"content": …}`/`embeds` and needs a relay until
      the Phase 6 format adapters land)
      *(WP 3.13: five events — job.done/error/cancelled +
      client.bypass_suspected/resolved (transition-only, state tracked in
      both directions regardless of the delivery filter) — one generic
      JSON schema, single delivery daemon thread with bounded drop-oldest
      queue, measured zero worker latency against hanging receivers,
      userinfo→Authorization-header conversion with redacted logging;
      schema v10; suite 1102 green; Opus PASS + fix round)*

### Phase 4 — Frontends (user decision 2026-08-06: web first, then app)

#### Phase 4a — Web UI (served by vault-api as static files) — COMPLETE 2026-08-11

All packages merged: 4a.1 (shell/static serving), 4a.2 (client/store),
4a.3 (library), 4a.4 (detail sheet + delete/GC), 4a.5 (downloads),
4a.6 (settings/onboarding/Steam identity) + 4a.6r (relay, api/),
4a.7 (notifications/bypass), 4a.8 (e2e + a11y). Suite at close: 379
headless web tests + 1461 api tests green. Every package: Sonnet coder
→ Opus review with independent mutation batteries → PASS. Recorded
mockup divergences live in WORKPACKAGES.md (user veto welcome).
Remaining for real-world validation: the honest still-open list in
web/tests/README.md (real screen reader, phone browser cover art,
Zeus-scale library) — the Zeus rollout session covers it.

- [x] SPA sharing the mockup's design language; zero extra deploy
      complexity (vault-api serves it; works over Tailscale/LAN and on
      phone browsers immediately)
      *(WP 4a.1 done 2026-08-10: vault-api serves `web/` via exact GET+HEAD
      routes + /css,/js StaticFiles mounts — /v1 routing parity pinned
      empirically against a pre-change baseline (trailing-slash 307s, 405s);
      strict CSP with zero inline script/style in web/; app shell with
      3-view nav per frozen mockup, status-icon component incl.
      reduced-motion, toasts; traversal pinned incl. %2e%2e forms; suite
      1281 green; Opus FAIL→fix→PASS. Docker packaging of web/ is a
      documented known gap until the packaging WP)*
- [x] Same API surface as the app: library grid with badges + search,
      download-to-cache/delete flows, jobs view, settings incl. vault name
      *(WP 4a.2 done 2026-08-10: fetch wrapper with X-Api-Key + six-kind
      error taxonomy + demo mode (fixtures 1:1 with the real Pydantic
      shapes incl. ADR-0003 shared-depot semantics in demo deletes);
      polling store with jobs-fast/games-clients-slow cadence, backoff
      with load-bearing jitter floor, hidden-tab full park, nudge
      coalescing + generation token (fork-free under nudge storms, pinned
      headless); pure notification differ per mockup NOTES (first poll
      silent, cancelled silent, update_ready gated on cached bytes,
      bypass both directions); 60 headless Node tests; Opus
      FAIL→fix→PASS with 10-mutation battery)*
      *(WP 4a.3 done 2026-08-10: library view — grid 2/3/list, ANDed
      search+chips (Failed replaces Update ready until the stale field
      exists — recorded divergence in WORKPACKAGES.md), capsule pills,
      multi-select with mockup-faithful bulk-download split and set-aware
      multiPlan bulk delete (fail-closed on unresolvable owners), real
      Steam cover art behind an exact-value CSP img-src pin with offline
      fallback tiles, and round-7 patch-in-place for games ticks
      (render-plan.js; node-identity verified live, icon subtree
      untouched by instrumented proof); 135 headless Node tests; Opus
      FAIL→fix→PASS, 7/7 render-plan + 16-mutation battery killed)*
      *(WP 4a.5 done 2026-08-10: downloads view — Active/Paused as
      independent sections per the recorded slot-release divergence
      (paused holds no slot, queue hint says so), FIFO queue with
      positions, history newest-first with lazy-fetched cached log
      excerpts, pause/resume/cancel non-optimistic with error-taxonomy
      toasts, nav queue-pip with aria-label, new neutral 'cancelled'
      icon kind (recorded divergence), jobs-tick patch-in-place
      (stop_request drift patches, status change rebuilds); 173 headless
      Node tests; Opus PASS, 12/12 mutation battery killed)*
      *(WP 4a.7 done 2026-08-11: bell + panel consuming the 4a.2 differ
      (no re-derivation — first-poll-silent inherited), unread badge
      clears on panel open per NOTES round 6, literal-pinned
      navigate-to-target via the router incl. downloads job highlight;
      app-shell bypass banner with WP 3.11's not-accusing wording;
      clients sheet on real /v1/clients with patch-in-place both
      directions pinned and the 4a.6 dialog semantics; bell-in-topbar +
      session-only log recorded as divergences; original coder hung in
      live verification and was replaced by a finisher who fixed two
      real gaps; 286 headless tests; Opus PASS, 13/13 mutations)*
      *(WP 4a.4 done 2026-08-11: game detail sheet on the 4a.7
      sheet-dialog — four-state depot sharing (adopts the recorded
      ORPHANED divergence), survives-deletion wording branch, job
      control incl. queued, single-game delete via multiplan with
      server-reported bytes, GC dry-run→plan→confirm→execute porting
      the reviewed Android reducer (execute only via plan+confirm,
      job-id-bound generation-guarded polling — fork-free), GC-totals
      scrape with load-bearing after-header scoping and \b insurance
      verified against gc_execute.py; update_ready notifications now
      target the detail sheet (4a.7 TODO closed); includes the tracked
      library.js mounted() fix (empty-grid-on-renavigation regression);
      demo games routes gained the missing last_manifest_check; 351
      headless tests; Opus PASS, 15/15 mutations)*
- [x] Steam identity via Sign in with Steam (ADR-0004); library fetch per
      the 2026-08-09 addendum (user decision A+C): opt-in server-side relay
      because the Steam Web API sends no CORS headers — Android keeps the
      device-local path
      *(WP 4a.6r done 2026-08-10: opt-in relay — GET/PUT/DELETE
      /v1/steam/key (32-hex validated, masked key_last4, never echoed) +
      /v1/steam/owned-games + /v1/steam/player-summaries, all authed;
      schema v12 single-row key table; oracle-style outbound HTTP with
      host/scheme/path pinned by string-literal tests against the captured
      Request; strict steamid64 + upstream-shape validation; 256-entry TTL
      cache keyed per (endpoint, steamid), cleared on key change; 92 relay
      tests, 13-mutation review set killed; suite 1375 green; Opus
      FAIL→fix→PASS. Web-UI consumption follows in WP 4a.6)*

#### Phase 4b — Android App — 4b.1–4b.10 MERGED 2026-08-17

Open: tsnet (post-v1 by decision) and the honest on-device list in
`app/README.md` — no emulator or phone was available in any implementation
session, including the WP 4b.9 signing work (proven with a throwaway test
keystore against Gradle/`apksigner` only, never against a real device
install). WP 4b.10 closed the clients/bypass detail surface (4b.8 had
routed bypass notifications to Settings for lack of one — see the PARTIAL
checkbox below); WP 4b.9 closed the release-signing/APK-docs item and the
`docs/WORKPACKAGES.md` review carry-over list (one item — the `security-
crypto` GA re-check — resolved as "report + recommend, do not bump", per
its own explicit instruction; the LogExcerpt truncation-marker carry-over
item turned out to already be closed since WP 4b.5, a stale list entry
rather than open work). Suite at close: 550 JVM tests, lint clean,
`assembleDebug` green, `assembleRelease` verified both to fail actionably
without a keystore and to produce a genuinely `apksigner`-verified APK with
one.

- [x] Kotlin/Compose project, Steam Web API integration (library + covers)
      *(WP 4b.1 done 2026-08-10: self-contained app/ Gradle project —
      pinned catalog (AGP 8.7.3, Kotlin 2.0.21, Compose BOM 2024.10.01,
      SDK 35/min 26), checksum-pinned wrapper; dark theme byte-for-byte
      from the mockup tokens; status-icon composable with all 10 kinds
      incl. cancelled, reduced-motion via areAnimatorsEnabled +
      ContentObserver; allowBackup=false + dataExtractionRules (future
      API-key storage); 30 JVM tests incl. literal cross-frontend
      wire-name contract; assembleDebug/test/lint green from cold build;
      Opus PASS + should-fix round)*
- [x] Connectivity-profile abstraction (one API-client interface, three
      implementations: tsnet / system VPN / public domain)
      *(two of three shipped — System-VPN and Public-domain; tsnet stays a
      documented seam, see the post-v1 item below)*
      *(WP 4b.2 done 2026-08-11: suspend OkHttp client for the full /v1
      surface the app needs (no /v1/steam/* — ADR-0004 device-local
      path), DTOs field-exact vs HEAD Pydantic incl. strict-Json fixture
      pass + verbatim api-test anchor; six-kind error taxonomy with
      literal cross-frontend pin; SystemVpn + PublicDomain profiles with
      defence-in-depth against redirect key leaks — followRedirects AND
      followSslRedirects false plus CleartextPolicyInterceptor at
      application AND network level, each layer standalone-pinned after
      the reviewer measured an https→http redirect forwarding X-Api-Key;
      EncryptedSharedPreferences key storage; polling/backoff pure
      functions in parity with the web store incl. the load-bearing
      jitter floor; 124 JVM tests; Opus FAIL→fix→PASS→should-fix round;
      tsnet stays a documented seam, post-v1)*
      *(WP 4b.3 done 2026-08-11: Steam OpenID login — checkid_setup via
      Custom Tab with steamvault:// return scheme, assertions
      re-verified via check_authentication against pinned
      steamcommunity.com with redirects refused and strict is_valid
      parsing, signed-fields gate before trust; SteamID64 validator with
      in-range Arabic-Indic mutation pin; on-device GetOwnedGames/
      GetPlayerSummaries with the user's own key (never sent to
      vault-api — allowlist-pinned), key-redacted error paths;
      documented replay residual (no state binding — candidate 4b.7/
      4b.9) and honest device-test list incl. 4b.7-blocked items; 219
      JVM tests; Opus PASS, 12/12 security mutations dead after fix
      round)*
- [ ] tsnet Go module + gomobile build (`.aar`), auth-key handling
      *(deliberately POST-V1: the profile abstraction from WP 4b.2 makes
      this additive, and the System-VPN profile covers Tailscale via the
      regular Tailscale app on day one)*
- [x] Grid + badges + multi-select + trigger + polling
      *(WP 4b.4 done 2026-08-11: library screen with grid 2/3/list +
      persisted layout, ANDed search+chips (recorded chip set),
      multi-select with bulk-download split and set-aware multiPlan bulk
      delete — all four web logic modules ported semantics-exact
      (reviewer: 12/12 mutations killed, zero cross-frontend drift);
      3-item bottom nav per frozen mockup; Coil covers on a separate
      OkHttp stack (API key cannot ride cover requests — by
      construction, dependency noted); vault ⊎ Steam-owned merge with
      honest synthesized rows (needs_force=false pinned); animation
      preservation via stable GameCardModel equality + lazy keys;
      "not connected" placeholder until 4b.7 lands the connection UI;
      314 JVM tests; Opus PASS + should-fix round (string-resource
      rule recorded in app/README))*
      *(WP 4b.5 done 2026-08-11: downloads screen — Active/Paused as
      independent sections with the web's verbatim slot-release wording,
      FIFO queue with positions, history newest-first with session-
      cached lazy log excerpts (truncation marker pinned to position 0),
      non-optimistic pause/resume/cancel with prefill-only pause gating,
      nav pip (queued|running|paused, foreground-only — recorded in the
      device-test list); unknown job statuses route to History instead
      of vanishing (recorded cross-frontend divergence, web backport in
      4a.8); JobCardModel stability with the strongest pin yet
      (stop_request drift changes ONLY the action field); 362 JVM
      tests; Opus PASS, 12/12 parity mutations killed)*
- [x] Delete flow with size display and confirmation; GC action per game
      *(WP 4b.6 done 2026-08-11: detail sheet with four-state depot
      sharing wording (ORPHANED added for the ADR-0003 last-remnant
      case — recorded divergence), honest last_manifest_check wording
      incl. the survives-deletion branch ("before the cache was
      cleared", pinned); delete confirm reuses buildMultiPlan verbatim
      (single-id ADR-0003 pins); GC flow as a pure reducer — dry-run →
      plan → confirm → execute, execute reachable ONLY via
      DryRunPlan→ConfirmExecute (parametrised no-op pins over all
      states), job-id-bound polling both branches, log-scrape verified
      against the real gc_execute.py emitter with after-header scoping;
      fix round also repaired a dead "Check again" button via a
      controller reset path; 407 JVM tests; Opus PASS + should-fix
      round)*
      *(WP 4b.7 done 2026-08-11: 3-step onboarding (profile choice,
      base-URL+key with a REAL two-step connection check — health for
      reachability, authed /v1/settings for key validity; optional
      Steam step closes the setWebApiKey UI gap); settings screen over
      GET/PATCH /v1/settings with the web's diff/presentation semantics
      (only-changed-keys pinned, honest applies wording, readonly
      banner with device-local Steam section correctly ungated);
      disconnect wipes store + reopens first-run with no stale polls;
      AND the recorded 4b.3 replay residual is CLOSED — per-login
      192-bit CSPRNG state in return_to, single-use consume before any
      network call, mutation-pinned in all directions; 492 JVM tests;
      Opus PASS 8/8 mutations + nit round)*
- [x] Bypass warnings surfaced in the UI
      *(WP 4b.10 done 2026-08-17: closes the routing gap the WP 4b.8 note
      below records ("recorded routing gap bypass→Settings until a clients
      surface exists") — a real
      `ui/clients/ClientsSheet.kt` backed by `GET /v1/clients`, Kotlin port
      of `web/js/components/clients-sheet.js` + `lib/clients-view.js`'s
      bypassing/healthy grouping and not-accusing wording, literal-pinned
      against the web source (`ClientsCrossFrontendContractTest`); the WP
      4b.8 bypass notification now opens this sheet directly
      (`NotificationRouting.opensClientsSheetFor`) instead of routing to
      Settings (`destinationFor` returns `null` for bypass events now, not
      `Destination.SETTINGS`); a "Clients" button in Settings is the
      second, non-notification entry point; `ClientRowModel`'s stats/
      section field split pins the WP 4b.5-style "a drift-only tick must
      not move the row" stability claim; 16 new/changed JVM tests (550
      total). Recorded divergence (`docs/WORKPACKAGES.md` Phase 4a
      cluster): unlike web's WP 4a.7 app-shell bypass banner, Android has
      no persistent third entry point — the WorkManager differ already
      covers the push half, and a banner is its own frozen-mockup-scope
      decision, not a side effect of closing this routing gap; Opus PASS
      (joint review with WP 4b.9, 4 should-fixes addressed))*
      *(WP 4b.8 done 2026-08-11: background notifications via
      WorkManager — 15-min constrained PeriodicWorkRequest with UPDATE
      policy, Doze respected by design (no exact alarms/foreground
      service); pure differ port of the web notifications semantics
      (first-poll silent, cancelled silent, update_ready gated on
      cached bytes, bypass both directions, the 4b.5 unknown-status
      improvement); notify-then-persist idempotency with stable
      per-event IDs + setOnlyAlertOnce; compact non-secret snapshot in
      plain prefs with shared decodeSnapshotOrNull fail-soft (pinned on
      the PRODUCTION path); foreground suppression gates posting only;
      catch-all worker with CancellationException rethrow; recorded
      routing gap bypass→Settings until a clients surface exists; 534
      JVM tests; Opus PASS 8/9 mutations + fix round)*
- [x] Document APK build (no Play Store requirement; F-Droid as a long-term goal)
      *(WP 4b.9 done 2026-08-17: release signing config reading
      storeFile/storePassword/keyAlias/keyPassword from a gitignored
      `app/keystore.properties` (template committed at
      `app/keystore.properties.example`) or four `VAULT_RELEASE_*` env
      vars, never generating a keystore itself; a missing/incomplete
      config fails `assembleRelease`/`bundleRelease`/`packageRelease`/
      `packageReleaseBundle` immediately via `gradle.taskGraph.whenReady`
      with an actionable Gradle error (review round 1 finding: the first
      version only guarded the two aggregate tasks, and `packageRelease`
      run directly bypassed it, producing a self-labelled
      `app-release-unsigned.apk` with no `META-INF` signature and no
      debug-key fallback — bounded severity, fixed by widening the
      guarded set; `installRelease` stays listed too as a forward-compat
      belt even though it is currently inert on its own); verified: fails
      in ~1s with no keystore, and a throwaway `keytool`-generated test
      keystore produced an APK independently confirmed signed via
      `apksigner verify --verbose` (`Number of signers: 1`, v2 scheme);
      `assembleDebug`/`test`/`lintDebug` proven unaffected either way
      (re-run green with no keystore.properties present after the
      signing-config change landed). `app/README.md`'s new "Release
      build, signing, and distribution" section documents the `keytool`
      command, the properties file, and the verification step; states
      plainly no Play Store listing exists and F-Droid stays a long-term
      goal, per this line item. Also closed the `docs/WORKPACKAGES.md`
      Phase 4b carry-over list in the same pass: moved `IdentityScreen`
      to `src/debug/` (GalleryScreen had already moved in WP 4b.4);
      re-checked `security-crypto` 1.1.0-alpha — a GA 1.1.0 now exists
      and is API-compatible (verified via the downloaded `.aar`'s
      classes), reported + recommended in `gradle/libs.versions.toml`'s
      comment, deliberately NOT bumped per the brief's own instruction;
      found the LogExcerpt truncation-marker item already closed since
      WP 4b.5 (stale carry-over entry, not open work); made the WP 4b.8
      notification-icon-reuse note a final v1 decision rather than a
      standing "revisit" pointer. 550 JVM tests (unchanged from WP
      4b.10 — no new test files, packaging/config/doc work only), lint
      clean, `assembleDebug` green; Opus PASS, no blockers (joint review
      with WP 4b.10) — 4 should-fixes addressed (S1 above; S2 the
      recorded-divergence note on the checkbox above; S3 `.gitignore`
      widened to `*.p12`/`*.pfx`/`*.bks` plus a root-level `keystore
      .properties` belt; S4 reworded a self-contradictory "no keystore,
      anywhere, ever" README line to distinguish the shipped build from
      the throwaway verification keystore), plus nit fixes (stale KDoc
      links, the AGP v2/v3-default overclaim, a stale WORKPACKAGES.md
      truncation-marker line))*

#### Phase 4c — Manual update check (both frontends, user decision 2026-08-10)

A user-triggered "check my cached games for updates now", so the vault can be
told from outside the LAN (from work, over Tailscale) to pull whatever is new
— the point is arriving home to a game that is already playable, not merely
knowing that an update exists.

**The check IS the fill.** SteamPrefill v3.7.1 has no `--dry-run` (verified
against `prefill --help`), and it does not need one: a non-forced run costs
~3 s and zero bytes for an up-to-date app, and downloads only the changed
chunks when stale (`docs/research/phase3-manifests.md`). So one action
answers the question and resolves it. Consequence for the UI: the affordance
must be worded honestly (`Check & update`, not `Check`) — pressing it can
consume real bandwidth. A check that only reports without filling is not
available at any reasonable cost, and would be the less useful half anyway.

- [x] Backend gap: `GET /v1/games` exposes `last_prefill_at` but NOT
      `apps.last_manifest_check`. Surface it in `GameSummary`/`GameDetail`
      — ADR-0006 tier 1 semantics are "current as of <timestamp>", which is
      only honest if that timestamp is visible. NOTE the shipped write rule
      (WP 3.3, verified in this WP): the column is stamped ONLY by a run
      that CONFIRMED the app already current (done + Updated==0 +
      UpToDate>0) — not on every run, not on done-with-updates. The UI must
      label it "confirmed current at X", never "checked at X", and the
      value survives a cache deletion (unlike `last_prefill_at`)
      *(mini-WP done 2026-08-10: field in both models, verbatim/null
      semantics with byte-for-byte round-trip pin, README honesty-table
      pointer; suite 1379 green; Opus PASS, 6/6 reviewer mutations killed)*
- [x] Enqueue endpoint question, decided (2026-08-17, WP 4c-api): `POST
      /v1/prefill` already takes a LIST of app ids and dedupes against
      `queued`/`running`/`paused` jobs, so an impatient double-tap converges
      on one job — but the open question was whether a server-side "select
      all cached apps" convenience route is worth it anyway. **Decision:
      ship it.** Robust for very large libraries (no frontend needs to
      enumerate `GET /v1/games` and filter by size first) and directly
      callable by external automation (n8n, a shell script over the
      tailnet) without already knowing the app id list. Cost accepted along
      with it: new write-capable API surface Phase 6's scoped keys must
      cover explicitly (recorded in `api/README.md`'s Auth section)
      *(WP 4c-api done: `POST /v1/prefill/cached`, no body — selects every
      app with cache content (disk-and-mapping truth, one bulk query, no
      per-app N+1) and enqueues through the SAME `jobs.enqueue_prefill`
      `POST /v1/prefill` uses, identical dedupe and response shape; "non-
      forced" is `apps.needs_force`'s existing per-app decision, not a new
      flag, and its lifecycle table in api/README.md was corrected in the
      fix round to also name GC execute (not only deletion) as a writer.
      Fix round (Opus FAIL→fix→PASS): added the primary-guarantee test the
      first pass shipped without — a multi-depot app plus an ADR-0003
      shared depot, asserting exactly one response entry per app — after
      the reviewer's mutation (per-depot instead of per-app selection) went
      undetected by the original 12 tests; that mutation is now killed by
      name (`test_multi_depot_apps_and_an_adr_0003_shared_depot_yield_
      exactly_one_entry_per_app`, verified by re-applying it and watching
      it fail, then reverting). Also added a route-level paused-dedupe
      test, fixed two dangling docstring test-file references, renamed a
      misleadingly-named test, and documented the cold-size-cache latency,
      the silently-ignored request body, and the mid-loop 5xx case. Suite
      1475 green (14 new tests). See api/README.md "Check & update all
      cached games" for the full, corrected contract. Frontend trigger UI
      and the pull-to-refresh divergence below are separate, unticked,
      packages that consume this route)*
- [x] Trigger in the web frontend (WP 4c-web, 2026-08-17/18): a
      library-header action over all cached games, plus the existing
      per-game and multi-select paths
      *(`web/js/views/library.js` gained a "Check & update all cached
      games" button in its own full-width header row, calling the new
      `api.prefillCached()` (`POST /v1/prefill/cached`, `web/js/api.js`).
      Mixed-outcome partition/wording, the forced-run heads-up, the
      mid-loop-5xx "re-read GET /v1/jobs" recovery signal, and the
      in-flight guard are pure, DOM-free functions in `web/js/lib/
      cached-prefill-outcome.js` (web/tests/cached-prefill-outcome.test.js):
      a paused dedupe is never worded as queued/started and an empty
      selection is never worded as a failure, both mutation-tested by name.
      Demo mode (`web/js/demo-data.js`) gained the same route, refactored to
      share the exact per-appid enqueue helper `POST /v1/prefill` already
      used — no second enqueue mechanism — with dedicated fixtures for the
      paused-dedupe and empty-selection cases
      (web/tests/demo-data-cached-prefill.test.js). Opus round 1: FAIL (one
      blocker, four should-fixes), all fixed and re-verified in round 2:
      (blocker) the forced-run note was computed by the view from its OWN
      `GET /v1/games` snapshot and appended unconditionally — live-
      reproduced as `"Nothing cached to check. (1 forced...)"` with zero
      jobs queued — composition moved into `cached-prefill-outcome.js`,
      gated on the response's own `queued` bucket being non-empty and
      scoped to only those appids, two named regression tests, both
      mutation-verified against the reviewer's exact reproduction string;
      (S1) a fourth bucket, `alreadyQueued` ("N already queued"), for a
      dedupe against a job the worker has not yet claimed — the common
      double-press case, previously mis-worded "already in progress" —
      with a real demo fixture (pause then resume) exercising it end to
      end; (S2) every string now says "check & update" in full ("queued for
      check & update", "Checking & updating…", incl. the busy label's
      accessible name); (S3) the divergence (new header row absent from
      the frozen mockup; the `doRefresh()` resolution) recorded in
      `docs/WORKPACKAGES.md`'s Phase 4a register alongside the project's
      other recorded UI divergences; (S4) the in-flight-guard mutation test
      now asserts synchronously so a broken guard fails in under 1ms
      instead of hanging the suite. Suite 414 green (35 new tests). The
      Android half of this trigger is a separate, still-open work
      package.)*
- [x] Trigger in the Android frontend (WP 4c-app, 2026-08-18): the same
      action, a Kotlin port of the web trigger's pure logic
      *(`ui/library/logic/CachedPrefillOutcome.kt` ports
      `web/js/lib/cached-prefill-outcome.js`'s five functions verbatim —
      partition into `queued`/`alreadyQueued`/`alreadyRunning`/
      `alreadyPaused` (with the unknown-status catch-all landing in
      `alreadyRunning`, never dropped), the wording/forced-note composer
      (`summarizeCachedPrefillOutcome`), the mid-loop-5xx "re-read jobs"
      signal (`describeCachedPrefillError`), and the in-flight guard
      (`CheckAndUpdateAction`). `LibraryController.checkAndUpdateCachedGames`
      wires it through `VaultApiClient.prefillCached()`
      (`POST /v1/prefill/cached`, no body ever — reusing the same
      `postEmpty` plumbing `pauseJob`/`resumeJob` already use) and a new,
      end-aligned button in its own `fillMaxWidth()` row directly below
      `LibraryScreen.kt`'s search/layout toolbar — the row's CONTAINER
      spans the width, the button itself does not (unlike web's own
      `width:100%` button), and the placement order also differs from web's
      (web: tools → check row → search; Android: search → layout/select →
      check row) — said plainly rather than reusing web's "full-width
      header row" description for a materially different layout. Both of
      `docs/WORKPACKAGES.md`'s recorded WP 4c-web decisions are adopted
      verbatim, per that entry's instruction: the button wording rule
      ("check & update", never bare "check") and pull-to-refresh staying
      passive-only (this screen has no pull-to-refresh gesture at all, so
      the rule is satisfied by omission). `warn` stays paused-only, pinned
      by an explicit
      alreadyQueued-must-not-warn assertion (the gap the web port shipped
      without). Suite 591 green (41 new tests: the four-bucket partition
      incl. the unknown-status catch-all, both ported BLOCKER REGRESSION
      cases, the in-flight guard's synchronous assertion, and a literal
      cross-frontend wording-contract test file, hand-transcribed against
      the web source, never derived from the Kotlin functions under test).
      No emulator/device in this environment — `lintDebug`/`assembleDebug`
      both green is the build evidence; see app/README.md's honest
      on-device list for what could not be verified here.)*
- [x] Mockup divergence to resolve in design: `doRefresh()`
      (`vault-app-mockup-NOTES.md`) only reloads what vault-api already
      knows. The update check asks Steam and must NOT be silently folded into
      pull-to-refresh — a gesture that can start downloads is a trap
      *(Resolved on the web frontend, WP 4c-web, 2026-08-17: kept strictly
      separate. `store.refreshNow()` (this app's `doRefresh()` equivalent,
      wired to pull-to-refresh's `visibilitychange` nudge in
      `web/js/store.js`) still only re-polls vault-api and never calls
      `POST /v1/prefill/cached` — the new "Check & update" button is the
      ONLY control that does. `refreshNow()` is still called AFTER a
      successful check, same as every other mutating action in
      `library.js`, purely to pull the resulting job rows onto screen
      sooner — never as a substitute for the Steam-contacting call itself.
      Decision recorded in `web/js/views/library.js`'s module header and
      (review round 1, S3) `docs/WORKPACKAGES.md`'s Phase 4a divergence
      register. Resolved on the Android frontend too, WP 4c-app,
      2026-08-18, by omission rather than by an explicit carve-out:
      `ui/library/` has no pull-to-refresh gesture at all
      (`LibraryController.pollGamesForever`/`pollJobsForever` are
      fixed-cadence foreground polls only, never triggered by a swipe), so
      there is no gesture for `POST /v1/prefill/cached` to be folded into
      in the first place — the same rule holds with nothing extra to
      write.)*
- [x] Guardrails (backend half, WP 4c-api): a user-initiated check
      deliberately bypasses the WP 3.11 miss-trigger cooldown (the user
      pressed the button) — structural, not conditional: `POST
      /v1/prefill/cached` never imports `event_sweep`/consults
      `miss_trigger_state` at all, mutation-tested (adding a cooldown check
      fails the pinning test by name) — but stays bounded by worker slots
      (one worker, plan §3) and job dedupe (the same rule every prefill
      request follows). A 50-game library is ~2.5 min of serial Steam
      logins, so progress belongs in the Jobs view, never behind a spinner
      — the endpoint returns `202` immediately by construction. The
      frontend half (routing an actual button press to this endpoint) is
      done in both UIs — the web and Android trigger items above
- Remote use ("check from work") needs no extra work: 4a is served over
  Tailscale/LAN by design, 4b has the connectivity-profile abstraction
- Complements Phase 6's `app.updated` webhook: that is the passive/push
  half (get pinged when the nightly sweep finds something), this is the
  active/pull half (ask right now)

#### Phase 4d — Persisted settings + "keep the cache current" sweep mode

Two coupled items (user decision 2026-08-10). The sweep mode is the feature;
persisted settings are what makes it a switch in the UI rather than a
Compose edit plus restart.

**Settings persistence — Plan B (chosen over env-only).** Today EVERY
setting is env-only (`VAULT_NAME`, `VAULT_SCHEDULE_*`, `VAULT_WEBHOOK_*`)
and `GET /v1/schedule` is read-only; there is no settings write endpoint at
all. A settings screen that can toggle anything therefore needs a new layer.

- [x] Settings table whose values override the env defaults, plus
      `GET`/`PATCH /v1/settings`. Needs an ADR: precedence rules (env vs DB
      — which wins, and how an operator forces a value back), validation
      reusing the same strict grammars `config.py` applies at startup
      (a bad value must fail at PATCH time, not hours later in the
      scheduler thread), and which settings stay deliberately env-only
      *(settings-WP done 2026-08-10, ADR-0009: schema v13 settings table;
      db>env>default via one accessor; PATCH all-or-nothing transaction,
      null clears, startup grammars reused (webhook-URL scheme check is
      the documented API-only exception), env-only allowlist,
      VAULT_SETTINGS_READONLY 403 lock, webhook-URL userinfo redacted;
      scheduler thread now starts unconditionally so schedule keys are
      genuinely next_sweep (B1 pinned end-to-end through the real
      lifespan); webhook keys honestly restart-required; suite 1461
      green; Opus FAIL→fix→PASS, 13-mutation battery + A/B thread-cost
      measurement)*
- [ ] Phase 4a's settings screen builds on this rather than displaying
      read-only values with "set this env var" hints

**Sweep target set — installed PLUS cached (default-on since WP SWEEP-1,
2026-08-22 — opt-in as originally shipped by WP 4d below).** Today the
nightly sweep targets the union of *installed* lists from fresh agent
reports (`scheduler.compute_targets`: "Intersected with nothing else", plan
A8) PLUS, by default, every app that already holds cache content on disk. A
game that sits in the cache but is currently installed nowhere — or whose
PC has been quiet longer than `VAULT_SCHEDULE_CLIENT_STALE_DAYS` — used to
never be refreshed and silently rot; it now is, out of the box.

**Update (WP SWEEP-1, operator decision 2026-08-22):** the checklist and
evidence log directly below are WP 4d's own historical record and are left
as they were written — they describe an opt-in, off-by-default feature,
which is what WP 4d actually shipped and what was true until this date. It
no longer describes the shipped default. The operator asked for "keep the
cache current" to be the out-of-the-box behavior rather than something an
installer has to discover, was shown the cost this section itself names
(bandwidth/disk on games nobody asked for, a real behaviour change on
upgrade) twice, and chose the new default anyway.
`DEFAULT_SWEEP_INCLUDE_CACHED` flipped to `True` PAIRED with
`DEFAULT_AUTO_GC` flipping to `execute` in the same change — shipping the
cached-apps sweep on without
also shipping real garbage collection on would be exactly the "keeps itself
current straight into a full disk" condition `scheduler.cached_sweep_gc_risk`
(named two bullets below) exists to warn about, not a smaller, safer step.
See `docs/adr/0014-sweep-cached-and-auto-gc-default-on.md` for the full
argument, the upgrade impact, and the two-line opt-out
(`VAULT_SWEEP_INCLUDE_CACHED=false` / `VAULT_AUTO_GC=off`, env or `PATCH
/v1/settings` — both keys remain fully overridable either way, ADR-0009).
Making that pairing actually run out of the box needed a scheduler window,
so the same package also ships `VAULT_SCHEDULE_WINDOW=03:00-07:00` /
`TZ=UTC` as the default Compose configuration (WP SWEEP-1 follow-up,
2026-08-22) — see that ADR's "Shipping an enabled nightly schedule" section
for the full argument.

- [x] New target-set mode adding every cached app to the sweep. Cheap by
      construction: ~3 s and zero bytes per app that is already current
      (see 4c), real traffic only for actual deltas
      *(WP 4d-backend done: `scheduler.compute_targets(..., include_cached=,
      cache_root=)` unions `scheduler.cached_appids` with the installed set,
      deduped by construction (`set[int]`); enqueued through the existing
      `jobs.enqueue_prefill` dedupe path, no second mechanism. Computed
      independently of client freshness, so a cached app whose only client is
      `VAULT_SCHEDULE_CLIENT_STALE_DAYS`-stale is still swept — tested
      explicitly. Review-round fix (blocker B1, real-`DELETE`-rig
      measurement, user decision "narrow the definition"): "cached" for THIS
      purpose is narrower than `GET /v1/games`'s `size_bytes` — an app counts
      only if it holds a depot NO OTHER tracked app also maps (reusing
      `deletion.plan_deletion`'s existing exclusive/shared split, not a
      second definition), because the generous definition kept an app
      "cached" via a shared/protected depot even after its own content was
      deleted, silently re-downloading it. `sizes.app_size_bytes` itself is
      unchanged)*
- [x] Opt-in, and off by default — it spends bandwidth on games nobody has
      asked for, which must be the operator's explicit choice
      *(`VAULT_SWEEP_INCLUDE_CACHED` / `Settings.sweep_include_cached`,
      default `False`; pinned by mutation at both the `compute_targets`
      parameter default and the `Settings` field default, each with its own
      named test)*
- [x] The backend half (an env var + the widened target set) can land
      independently of the settings layer; only the UI switch depends on it
      *(landed on top of ADR-0009: `sweep_include_cached` is in
      `settings_store.OVERRIDABLE_SPECS`, `applies: "next_sweep"`,
      PATCH-validated with `config.parse_strict_bool` — reused, not
      reinvented, and shared with `VAULT_SETTINGS_READONLY`'s startup
      grammar; `GET /v1/schedule` reports the effective value additively,
      end-to-end-pinned through a real bare-boot lifespan + PATCH + a real
      background sweep (review-round should-fix S2, same shape as ADR-0009's
      own B1 pin). The Phase 4a UI switch closed 2026-08-22 as WP 4d-web
      (commit c825625): toggle + GC-risk warning + three-way last-sweep
      status in the web Settings view, all read from served state, never
      recomputed; review FAILed round 1 on two one-field-over-claim
      blockers (a crashed sweep would have read "has not completed a run
      yet" forever; the warning asserted activity a configuration
      predicate cannot support) — both reworded to state-only copy. Also
      closed the demo-fixture drift class: a text-scrape guard pins
      web/js/demo-data.js's stated defaults to api/vault_api/config.py's
      real ones, labelling VALUE vs GRAMMAR drift. The Android half of
      the sweep surface is still open — see §11 item 4)*
- [x] **Pair with auto-GC** (still open in Phase 3): every kept-current game
      adds fresh chunks while the old manifest's chunks become orphans. A
      vault that keeps itself current without collecting garbage keeps
      itself current straight into a full disk. These two ship together or
      the growth must at least be surfaced
      *(surfaced, not auto-paired, per the backend brief: auto-GC is never
      silently enabled and the sweep is never refused when unresolved — the
      operator decides. `scheduler.cached_sweep_gc_risk` names the condition
      once; `PrefillScheduler` logs a one-time `WARNING` on the transition
      into it and a matching `INFO` all-clear on the way back out (not per
      tick, not per process — re-fires on a fresh transition, mutation-
      tested); `GET /v1/schedule`'s new `sweep_cached_gc_risk` field exposes
      the identical condition for a future UI banner. Review-round fix
      (blocker B2, user decision "nothing is being reclaimed"): `dry-run`
      counts as risky too — it reports what could be freed and frees
      nothing, so only `VAULT_AUTO_GC=execute` actually clears the flag.
      api/README.md's "Auto-GC" section documents the coupling)*

  Evidence (WP 4d-backend, 2026-08-17, fix round 2026-08-18):
  `api/vault_api/scheduler.py` (`cached_appids` now built on
  `deletion.plan_deletion`/`load_mapping_rows`, `compute_targets
  (include_cached=, cache_root=)` — `cache_root` now REQUIRED when the mode
  is on, `cached_sweep_gc_risk`, `warn_once_if_cached_sweep_without_gc` with
  its INFO all-clear), `api/vault_api/config.py` (`VAULT_SWEEP_INCLUDE_CACHED`,
  `config.parse_strict_bool`), `api/vault_api/settings_store.py`
  (`sweep_include_cached` in `OVERRIDABLE_SPECS`, now eight overridable
  keys), `api/vault_api/routers/schedule.py` (`sweep_include_cached`,
  `sweep_cached_gc_risk` on `GET /v1/schedule`, additive; module docstring
  corrected — should-fix S3). Suite 1554 green. Mutations run and killed by
  name across both rounds: default-off ×2, mode-off-byte-identical,
  auto-GC-risk logic (both the off/execute boundary and the dry-run-is-risky
  B2 fix), warn-once-not-every-tick, union-dedupe ×2,
  cached-app-survives-staleness, the B1 exclusive-vs-shared regression
  (reviewer's own real-`DELETE` fixture), the S1 always-log-when-on line, and
  the S1 required-`cache_root` guard. Not done: the Phase 4a settings-screen
  switch (UI, out of scope per the brief); forwarding
  `VAULT_SWEEP_INCLUDE_CACHED` through `deploy/compose.yaml` is now a
  deliberate non-goal, not a pending handoff — WP P1 (`da79aca`) established
  that DB-overridable settings (this one, `VAULT_SCHEDULE_*`,
  `VAULT_WEBHOOK_URL`/`EVENTS`) are NOT env-forwarded by design, `PATCH
  /v1/settings` being the one supported path; `deploy/.env.example` documents
  the key with that framing instead of a commented assignment line.

- [x] **WP 4f — one definition of "which apps hold cache content"** (gap
      measured by the WP 4d reviewer on one post-delete state, 2026-08-18: the
      sweep's `cached_appids` said `[730]`, the "Check & update all cached
      games" button's own selection said `[440, 730]` — a game the operator
      just deleted, one button press away from the re-download-after-delete
      behaviour Plan A exists to prevent. User decision, 2026-08-18: "the
      user's decision applies to both paths")
      *(WP 4f done, api/-side, two review rounds — Opus FAIL → fix → PASS:
      one shared bulk function, `deletion.appids_with_cache_content`, fed by
      a new single-statement join query
      (`deletion.load_all_mapping_rows_with_owner_state`);
      `scheduler.cached_appids` and `routers/jobs.py`'s `POST
      /v1/prefill/cached` selection are now both thin wrappers around it — no
      duplicated predicate, structurally pinned by
      `tests/test_wp4f_shared_cache_content_definition.py` (swap the shared
      function for a fake, both callers return exactly its result) and by
      mutation (breaking the shared function fails tests in BOTH
      `tests/test_scheduler.py` and `tests/test_prefill_cached.py`, verified
      by re-applying the mutation and watching it fail, then reverting).
      Adopted `exclusive + remnant` (ADR-0003 addendum, now with its own
      2026-08-18 note recording this reuse), widening WP 4d's
      `exclusive`-only rule: two apps sharing ALL their depots with each
      other and nothing else, both otherwise uncached, are now visible
      (`tests/test_cache_delete.py::test_appids_with_cache_content_mutual_
      sharing_pair_becomes_visible`) while the B1 fixture (a co-owner that
      DOES have content) still excludes the deleted app, pinned unchanged.
      Fix round closed two unpinned fail-closed arms (B1, docs/LEARNINGS.md's
      standing WP-3.6 rule): `LEFT JOIN apps` -> `JOIN apps` silently dropped
      a co-owner with no `apps` row entirely instead of protecting via it
      (`test_appids_with_cache_content_a_co_owner_with_no_apps_row_
      protects_the_depot`), and moving the `depot_groups` append below the
      owner-coercion guard dropped a poisoned co-owner row the same way
      (`test_appids_with_cache_content_a_poisoned_co_owner_appid_protects_
      the_depot`) — both mutations re-applied and confirmed to fail these
      tests by name, then reverted. Also added the HTTP-level regression
      test the divergence actually needs (S1): a real
      `DELETE /v1/cache/{appid}` with a cached co-owner, then
      `POST /v1/prefill/cached` must not reselect the deleted app
      (`test_a_deleted_app_whose_only_surviving_content_is_a_shared_
      protected_depot_is_not_reselected` — reverting the route to its
      pre-WP-4f generous rule left every OTHER test in that file green,
      confirmed by re-running against the reverted route). Cost: measured on
      a synthetic 300-app/260-depot mixed-sharing library, the old
      per-app-query shape (301 statements, including the initial candidate
      query — matching WP 4d's own historical figure once counted the same
      way) became ONE statement; separately measured, the in-memory
      reconstruction is genuinely quadratic in owners-per-depot, NOT
      negligible (fix round correction, S4) — up to ~3.6 s on a library with
      a large shared depot (1000 apps × 10 fully-shared depots), which
      matters most inside `POST /v1/prefill/cached`'s synchronous selection
      step (its own "202 immediately" contract does not cover this cost);
      inside the 3-hour-cadence background sweep the same cost is genuinely
      negligible. Both existing statement-count pins
      (`test_selecting_cached_apps_is_not_a_per_app_query` at 500 apps,
      route-level; still 500 exclusively-owned apps, no sharing) plus a new one
      directly on the shared function
      (`test_appids_with_cache_content_is_one_statement_regardless_of_app_
      count`, same N5 fix) stay green. `api/README.md`'s "Sweep target set"
      and "Check & update all cached games" sections cross-reference each
      other, state the narrowing edge once, and carry the corrected measured
      numbers (the WP 4d text's "one small indexed SQL query per candidate
      app" claim is corrected, and the S2/S3 fix round corrected the env
      table's blank-means-default rule to name `VAULT_CACHE_ROOT` as the
      exception plus the actual mechanism for a present-but-blank value: a
      FORWARDED compose key with empty `.env` interpolation, or a bare
      `KEY:`/`ENV KEY=` in a derived image — NOT an unforwarded key, which is
      simply absent and gets the default fine); the "no coalescing with
      request-path scans" caveat is documented in both directions, since the
      route also reads through `SizeCache`. `config.py`'s
      `Settings.from_env()` now refuses to boot on a present-but-blank
      `VAULT_CACHE_ROOT` (previously silent, and fatal to the sweep every
      tick with `VAULT_SWEEP_INCLUDE_CACHED` on) — mutation-verified, comment
      and test docstring corrected to the real mechanism in the fix round.
      Renamed
      `test_b2_bare_boot_patch_enables_cached_mode_and_a_real_sweep_
      enqueues_it` to `test_s2_...` (it was already documented as the S2 pin;
      the name just hadn't caught up). `docs/adr/0003-additive-depot-
      mapping.md` gained a short 2026-08-18 addendum recording this reuse
      (N3). Suite 1566 passed, 1 skipped (1554 pre-WP-4f baseline + 12 new
      tests across both review rounds). Footprint: `api/`, `docs/PROJECT_PLAN.md`, `docs/WORKPACKAGES.md` +
      `docs/adr/0003-additive-depot-mapping.md`, no commit/push per the
      work-package brief.
      **Explicit carry-over, NOT done here (B2, reviewer 2026-08-18): the web
      demo mode still implements the pre-WP-4f generous rule and its
      docstring states that rule as the current one.**
      `web/js/demo-data.js`'s `selectCachedAppids()` (lines ~701–717)
      selects every game whose `depots` array is non-empty; after a demo
      `DELETE` that correctly moves a shared, still-protected depot into
      `skipped_shared` (the handler at line ~997 keeps that depot in BOTH
      the deleted app's and its co-owner's `depots` arrays by design — only
      exclusively-deleted depot ids are filtered out, line 1078-1081), the
      just-deleted app's `depots` array is still non-empty, so
      `selectCachedAppids()` — and therefore the demo's
      `POST /v1/prefill/cached` — reselects it. The function's own docstring
      compounds this: it cites `api/README.md`'s "Selection: disk-and-mapping
      truth, one query" heading (renamed by this package to "...and (WP 4f)
      the SAME definition the sweep uses") as describing the CURRENT real
      rule, when the real rule is now `exclusive + remnant`, not "any mapped
      depot with bytes". `web/tests/README.md` (lines ~592–596,
      `demo-data-cached-prefill.test.js`'s coverage description) repeats the
      same stale framing verbatim ("selects every game whose `depots` array
      is non-empty ... this demo model's stand-in for 'has cache content on
      disk'"). Per docs/LEARNINGS.md ("Web UI" section, WP 4a.2 blocker):
      demo fixtures are a shipped surface and must demonstrate the product's
      invariants, not violate them — this is that same bug class. **Not
      fixed here**: `web/` is occupied by WP 4e.1, which is actively editing
      `demo-data.js`; the reviewer will close this once 4e.1 merges. The
      shape of the fix: hoist the DELETE handler's local `otherOwners`/
      `hasCacheContent` helpers (currently function-scoped inside the
      `DELETE` branch, lines ~1039–1051) to module scope so
      `selectCachedAppids()` can reuse them, then change its filter
      predicate from `g.depots.length > 0` to "has at least one depot that is
      exclusive to this game OR a last-cached-remnant (every other current
      owner uncached)" — the same `exclusive + remnant` predicate this
      package's real backend now applies, expressed against the demo's
      depots-array model instead of `deletion.plan_deletion`. Also recorded
      in `docs/WORKPACKAGES.md` (N6).)*
#### Phase 4e — Web UI desktop layout (track: web)

The web UI (Phase 4a) has no responsive layer at all: the frozen round-7
mockup is a single 390×844 phone frame ported into a `max-width:960px`
column, so a 173×260 cover tile renders at a fixed 458×687 from 1280 to
2560px, the bottom nav's three items stretch to 631–844px around a 21px
glyph, and roughly half of a 1920px viewport carries nothing. **User
decision (2026-08-18): approved the full desktop-layout proposal, all
divergences included** ("setze alles so um") — implemented as designed,
not narrowed. See `docs/WORKPACKAGES.md`'s divergence register (D-1, D-3,
D-4, D-11, D-12) for what each package diverges from the mockup and why.

- [x] Spatial tokens, breakpoints (BP-M ≥720px / BP-L ≥1024px / BP-XL
      ≥1800px, width-keyed; pointer affordances in a separate
      `(hover:hover) and (pointer:fine)` query), and the bottom-nav-to-
      left-rail shell conversion; the large-library demo fixture the rest
      of the phase measures against
      *(WP 4e.1 done 2026-08-18: `--gutter`/`--nav-h`/`--rail-w`/
      `--w-text`/`--w-wall`/`--tile-min`/`--w-sheet` tokens in theme.css
      (no colour/font/radius value touched — Android's VaultColors literal-
      pins stay valid); `.view-root`/`.onb`/`.banner-wrap`'s four
      hand-copied 960/928/78 numbers replaced with token references/`calc()`
      derivations (correction, Opus review nitpick N5: this is four numbers
      that were COPIES of each other, not every dimensional literal in
      app.css — `.view-root`'s own `20px 16px 32px` padding and `.bulk`'s
      `left/right:12px` insets are untouched, independent literals, not
      claimed otherwise); `.banner-wrap`'s shrink-to-fit bug fixed (`width:100%`,
      no `display` declaration added — would have been a fourth `[hidden]`-
      vs-author-`display` instance); `<nav>` moved before `<main>` in
      index.html (D-11) with a compensating `.nav{ order:1 }` so VISUAL
      order below BP-L is unchanged (`order` is paint-order only — see S2
      below for the READING/focus-order consequence this claim does not
      cover); the shell/rail/breakpoint machinery verified to live only
      inside `min-width` blocks via a structural headless pin, plus a live
      six-width measurement (390/768/1280/1440/1920/2560) — reported in the
      coder's response (superseded by the corrected, extended table below —
      see blocker B3).
      Two general-purpose CSS lints added per the brief's request: (1) every
      class with an author `display` rule that is ALSO hidden-toggled
      somewhere in `web/js/` must have a matching `[hidden]` guard rule —
      a real cross-reference derived from the CSS/JS source each run, not a
      hand-typed list of the three previously-found offenders (`.btn`,
      `h4.sec`, `.onbnav`), mutation-verified by removing a guard and
      watching the named test die; (2) no `!important` outside the
      `prefers-reduced-motion` block, mutation-verified by adding one to a
      new breakpoint. `web/js/demo-data.js` gained
      `generateSyntheticGames`/a `localStorage["steamvault.demoLibrarySize"]`
      toggle (400 selectable, default unchanged at 6) reachable from both a
      headless test and the running app; documented in its own header what
      it can measure (DOM/poll-diff cost at scale) and cannot (no real cover
      art, so it does not exercise the CSP image-allowance half of a real
      400-game library).

      **Sequencing fix (orchestrator review, same day):** a first version of
      this package widened `--w-wall` to 1440px at BP-L and 1720px at BP-XL,
      on the reasoning that the rail frees up horizontal room the library
      grid should get to use. Measuring it live instead of assuming it
      exposed the opposite: because the Library grid is STILL the mockup's
      fixed 2/3/list column switch, not an `auto-fill` density grid,
      widening `.view-root`'s cap only grew the ALREADY-oversized cover tile
      further — 458×687 (the pre-WP baseline, unchanged from 1280–2560px)
      became ~815×1222 at 1920px and ~838×1257 at 2560px. Shipping that
      would have made the phase's own motivating problem measurably worse
      the moment this "foundation" package landed on its own, which is not
      independently shippable. **Fixed by sequencing, not scope creep:**
      `--w-wall` stays equal to the pre-WP effective cap (960px, `theme.css`)
      in EVERY breakpoint block (`:root` base, BP-L, BP-XL all say 960px
      explicitly — restated rather than omitted, so a later reader sees a
      deliberate decision, not a forgotten override) until a later package's
      auto-fill grid can actually use the room; that package changes this
      ONE value. `--w-text` (Settings/Downloads/onboarding/banner — text-
      first views sharing the same `.view-root`) still narrows to 760px at
      BP-M as designed; only the library-grid-specific widening at BP-L/XL
      was deferred. Two headless pins added
      (`web/tests/css-layout-foundation.test.js`): every `--w-wall:`
      assignment anywhere in theme.css/app.css must equal `960px`, and
      `.view-root`'s BP-L override must resolve to the SAME 960px as its
      base/BP-M cap — both mutation-verified (temporarily restoring
      1440px/1720px kills each by name). Re-measured live after the fix (see
      the corrected six-width table below): the tile is now IDENTICAL to the
      pre-WP baseline at every width from 1280–2560px, not merely "no
      worse". **Honest status, not silently narrowed:** the tile-oversize
      problem itself remains genuinely unfixed — that is explicitly D-4's
      scope, landing with the auto-fill grid below — this package now
      simply does not regress it in the meantime.

      **Opus review round 1: FAIL — three blockers, five should-fixes, six
      nitpicks, all fixed same day.** The reviewer's framing: the shell
      change (bottom nav -> left rail, `#app` flex -> grid) re-parents every
      `position:fixed` surface, and the first live-verification pass only
      measured three selectors (the tile, `.view-root`, the nav) — it should
      have swept every fixed/overlay surface. Two real bugs existed that a
      3-selector check could not see:
      - **B1** — `--nav-h:0` (BP-L) is UNITLESS; `.bulk`'s
        `bottom:calc(var(--nav-h) + 14px)` mixes it with a `14px` length,
        which is a calc() type mismatch, invalid at computed-value time,
        silently falling back to `bottom:auto`. Measured: the bulk action
        bar's `bottom` computed to -143782px (off screen) at every width
        >=1024px — multi-select (enter select mode, pick games, Download or
        Delete) was **unusable on desktop**. Fix: `--nav-h:0px`, plus a pin
        requiring every `--nav-h:` assignment anywhere to match `/^\d+px$/`
        (the CLASS of bug, not just the instance) — mutation-verified.
      - **B2** — `index.html` puts `.nav-pip` FIRST inside the Downloads
        button (harmless while `position:absolute`); BP-L's
        `position:static; margin-left:auto` override then rendered it BEFORE
        the icon in plain DOM/flex order, pushing icon+label right instead —
        measured live with a job running: the whole Downloads row sat ~88px
        out of line with Library/Settings. Fix: `order:1` on the BP-L
        `.nav-pip` rule (no DOM move — `aria-current`/click/keyboard/the
        `.on` toggle are all untouched), pinned and mutation-verified;
        re-measured at 1024/1280/1920px with an active job — Downloads'
        icon (x=22) and label (x=55) now match Library/Settings exactly.
      - **B3** — the plan and the test-file header both claimed "base
        (<720px) is byte-identical", which is false from ~430px up:
        `.banner-wrap{width:100%}` (the shrink-to-fit fix) is a deliberate
        TOP-LEVEL rule, not gated by any media query, so it changes real
        rendering wherever the bug it fixes existed. Measured against a
        `git archive HEAD` baseline: 430px viewport, 398.3px-wide banner @
        x=8.3 -> 415px @ x=0; 719px viewport, 398.3px @ x=152.8 -> 704px @
        x=0. **Fixed the CLAIM, not the behaviour** (the behaviour is the
        correct fix and was already disclosed elsewhere): only the mockup's
        own 390px is genuinely byte-identical end to end; "base is
        untouched" now means specifically "the shell/rail/breakpoint
        machinery is gated correctly", stated exactly that way in both this
        section and the test file's header, with `.banner-wrap{width:100%}`
        pinned as the one rule that must never silently revert.

      Should-fixes, all closed: **S1** `generateSyntheticGames` now sets
      `needsForce: !cached` (an idle/never-filled synthetic row was
      `needs_force=false`, a shape the real API can never produce per
      api/README.md's lifecycle — a demo fixture is a shipped 1:1 surface).
      **S2** `docs/WORKPACKAGES.md`'s D-11 entry now records the phone-side
      consequence honestly: `order` is visual-only, so below BP-L a
      keyboard/screen-reader user now tabs the nav BEFORE the content on
      every view — a real change to the exact surface WP 4a.8's a11y pass
      signed off on, defensible but not previously stated. **S3**
      `web/tests/README.md` gained a WP 4e.1 section (three new test files)
      and an honest "what this pass did not catch on its own" note naming
      B1/B2's category (any shell-layout-changing package must re-check
      every fixed/overlay surface, not just the ones its diff touches — no
      headless equivalent exists for this, same posture as WP 4a.8's list).
      **S4** `.banner-wrap` now picks up `--w-wall` (not `--w-text`) at
      BP-L, matching `.view-root` exactly — they share one visual column and
      a mismatched cap was a measured 100px seam (left edges 688.5 vs
      588.5 at 1920px); tying both to one token also keeps a later
      `--w-wall` raise aligned for free. **S5 — the process fix.** Widened
      this package's own regression pass to the surfaces B1/B2 fell into:
      `.bulk`, `.sheet-backdrop` (notifications panel, verified directly),
      `.dialog-backdrop` (delete confirm, verified directly) and `#toast`
      (verified directly) all confirmed on screen at 1024/1280/1920px in
      multi-select with an active job. The 1023–1279px transition band
      (below) was added to the measurement table per this same finding — the
      rail visibly taking 232px out of the available column is the widest-
      changing band in the whole spec and the first draft skipped it.

      Nitpicks, all closed: **N1** `css-hygiene.test.js`'s display/hidden
      lint now also recognizes `setAttribute("hidden", ...)`/
      `removeAttribute("hidden")` as toggle sites (not just the `.hidden =`
      property form — the reviewer's own probe of this hole survived the
      first version and now dies by name), and its header states the
      multi-class/descendant-selector limitation explicitly. **N2**
      `sectionHeadingFactory`'s hardcoded `["sec"]` mapping is now asserted
      against the real `sectionHeading()` source, so drift fails loudly.
      **N3** the six-width table below now says which scrollbar mode
      produced which row: viewports <768px trigger this Browser pane's
      mobile/touch emulation (overlay scrollbar, 0px reserved — confirmed:
      `innerWidth === clientWidth` at 390px), 768px and up use a classic
      scrollbar (15px reserved whenever content actually overflows, which
      the 400-game fixture guarantees) — not an inconsistency, a real
      platform difference the numbers now name. **N4** a dedicated pin now
      checks the exact shape of `last_prefill_at` (real ISO string for
      "done", exactly `null` for "idle") separately from the determinism
      check that strips it. **N5** the "four hand-copied numbers" claim
      above is corrected to name what it does NOT cover (`.view-root`'s own
      `20px 16px 32px` padding, `.bulk`'s `left/right:12px`, both still
      independent literals). **N6** `--tile-min`'s existence is now asserted
      inside the SAME test as the `--w-wall` anti-widening pin, so an
      automated "remove this, nothing references it" sweep cannot silently
      delete a token a later WP needs.

      Suite 450 green (36 new relative to the pre-WP-4e.1 baseline of 414:
      30 from the first pass, plus 6 new named tests in this fix round — one
      each for the B1 `--nav-h` unit pin, the B2 `.nav-pip order:1` pin, the
      S4 `.banner-wrap`/`--w-wall` alignment pin, the S1 `needs_force`
      inverse-of-cached pin, the N2 `sectionHeadingFactory` mapping pin, and
      the N4 `last_prefill_at` shape pin. B3's fix and N5/N6 extended
      EXISTING tests/comments in place rather than adding new ones; N1's
      `setAttribute`/`removeAttribute` recognition extends the existing
      lint's own scan, covered by the existing display/hidden-guard test
      re-passing plus the mutation check below, not a new test file entry).

      **Six-width table, final (re-measured after every fix above, live
      against a running vault-api + the 400-game demo fixture; scrollbar
      mode noted per N3):**

      | width | cover tile | `.view-root` | nav | scrollbar |
      |---|---|---|---|---|
      | 390 | 173.0×259.5 (byte-identical to pre-WP) | — (base) | bottom, 3-col | overlay (mobile emulation) |
      | 768 | 354.5×531.8 | 760px cap (BP-M) | bottom, 3-col, chips wrap | classic, 15px |
      | 1023 | 358.0×537.0 | 760px cap (still BP-M) | bottom, 3-col | classic, 15px |
      | 1024 | 366.5×549.8 | 777px (rail takes 232px out of the column) | rail, 232px | classic, 15px |
      | 1200 | 454.5×681.8 | 953px (approaching the 960px cap) | rail | classic, 15px |
      | 1279 | **458.0×687.0** (cap reached) | 960px cap | rail | classic, 15px |
      | 1280 | **458.0×687.0** (pre-WP baseline, exact) | 960px cap | rail, 232px | classic, 15px |
      | 1440 | **458.0×687.0** | 960px cap | rail | classic, 15px |
      | 1920 | **458.0×687.0** | 960px cap | rail | classic, 15px |
      | 2560 | **458.0×687.0** | 960px cap | rail | classic, 15px |

      The 1023→1279 band (added per should-fix S5) is the widest-changing
      one in the whole table: crossing BP-L subtracts the rail's 232px from
      the available column before `.view-root` can reach its 960px cap, so
      the tile visibly SHRINKS relative to where it would have landed
      without a rail, then grows back to the steady 458×687 by ~1279px —
      correct, expected behaviour, not a bug, but worth seeing in the table
      rather than jumping straight from 1023 to 1280.

      Re-verified separately, live, at 1024/1280/1920px in multi-select
      with an active synthetic job (the exact conditions B1/B2 needed to
      surface): `.bulk` on screen at all three (`bottom` computed to the
      intended `14px`, `rect.bottom` inside the viewport with the .22s
      slide-up transition settled); the Downloads nav-pip `on`, showing
      "1", at the row's trailing edge with icon/label x matching Library/
      Settings exactly; the notifications sheet-backdrop, the bulk-delete
      `.dialog-backdrop`, and `#toast` all independently confirmed on
      screen at 1920px.

      **DOM node count vs. column count — a framing correction (orchestrator
      review), pinned in a code comment so it is not re-litigated in a later
      package.** `web/js/views/library.js`'s `renderGrid` builds one card
      node per VISIBLE game regardless of `state.layout` (2/3/list all run
      the identical loop; only `els.grid.className` — a CSS concern —
      changes). A 400-game library already builds 400 card nodes TODAY, in
      every layout, on every viewport width; there is no virtualization
      either before or after this package. The auto-fill grid Phase 4e
      eventually ships changes how those 400 nodes are ARRANGED (more
      columns at wider viewports), never how many exist. The measured
      ~29–33ms cost of a full 400-card rebuild (see WP 4e.1's coder report)
      is therefore the number that actually matters for scaling, not a
      column-count framing — the gate this fixture exists to clear is
      already cleared)*
- [x] Auto-fill library grid using `--tile-min` (`repeat(auto-fill,
      minmax(var(--tile-min),1fr))`), replacing the fixed 2/3/list switch at
      BP-L/BP-XL, and raising `--w-wall` past its currently-deferred 960px
      alongside it (in the SAME package — see the sequencing note above,
      widening one without the other is exactly the regression already
      caught and fixed once) — the D-4 fix
      *(WP 4e.2, 2026-08-18, Opus round 1: FAIL — one blocker, five
      should-fixes — fix round verified, re-verified live: `web/css/app.css`'s
      BP-L block wires `.grid`/`.grid.cols3` to `repeat(auto-fill,minmax(
      var(--tile-min),1fr))`, `--tile-min` split into a 176px "Comfortable"
      default and a 150px "Compact" override (`.grid.cols3{--tile-min:150px}`,
      operator-chosen values, see below) — the D-3 density relabeling in
      `web/js/views/library.js` (`LAYOUT_LABEL`: "Two columns"/"Three columns"
      -> "Comfortable"/"Compact", `docs/WORKPACKAGES.md`'s register, now in
      proper bullet form). "Fix the tile, multiply the columns": `.grid.
      cols3`'s mobile-only compact card typography (tuned for a ~113px
      3-per-row phone tile) is reset back to the base card's own values at
      BP-L, seven reset rules pinned by literal name (round 1's blocker-class
      finding, S3: deleting the base `.grid`/`.grid.cols3` rules OR all of
      the BP-L reset rules survived 456/456 — both now die by name, verified
      by re-applying each mutation and watching the named test fail);
      `.grid.list` needed no override at all (kept via CSS specificity,
      pinned structurally). `--w-wall` raised to 1600px at BP-L (round 1
      should-fix S2: this value is UNREACHABLE within BP-L's own range —
      BP-L's widest viewport, 1799px, leaves a 1567px main column, 33px short
      — so it functions purely as a guard against a future `--rail-w`/
      breakpoint change, not as an active cap; the original comment's "lands
      at ~186px @1920, capped" claim was wrong, since 1920px is BP-XL's range
      where a SEPARATE 2000px value applies, fixed in both the code comment
      and the test's assertion message) and 2000px at BP-XL (bounded, not
      fully uncapped — argued against the brief's "topbar/reading measures
      don't improve past ~1600" note: true of `--w-text` content, not of a
      tile WALL, which keeps getting more columns from more room). `.bulk`
      re-derived from `--w-wall` with `--rail-w + --gutter` / `--gutter`
      insets — proven algebraically exact in both the capped and uncapped
      regimes, confirmed live at ten widths total across both review rounds
      with Δleft=Δwidth=0 throughout, including inside BP-XL. Search+chips
      moved into a `.view-library` BP-L grid, search capped at the new
      `--search-w:420px` token — round 1 should-fix S4: the cap and the
      area-map row order were both asserted by NAME (declared) but not by
      VALUE, so removing the cap (`1fr 1fr`) and swapping "search chips" to
      "chips search" both survived; both mutations now die by name.
      **Blocker B1 (round 1): `.empty` (the "no results" fallback, appended
      as a direct child of `.grid`) had no `grid-column` rule, unlike
      `.noresult` — under auto-fill it collapsed into a single track (an
      8.5%-of-row-wide block hard left at 2560px), reachable one click away
      via any zero-count filter chip (`renderChips` renders zero-count chips
      too). Fixed with `grid-column:1/-1`, pinned, and confirmed safe for
      `downloads.js`'s two other `.empty` uses (plain block containers, the
      property is ignored there) — re-verified live via the exact
      reproduction (the real "Downloading 0" chip on the 400-game fixture):
      `.empty` now measures the full grid width, not a single track.**
      **Should-fix S1 (round 1): operator-chosen `--tile-min` values,
      176px Comfortable / 150px Compact**, replacing the coder's
      first-shipped 210px/168px — measured live, 210px produced
      225.6-246.0px tiles (1.30x-1.42x the 173px mockup tile), falsifying
      the "no card here ever sees a size it wasn't designed for" claim.
      150px restores Compact's original WP 4e.1 value.

      **B2 (round 2, blocker): round 1's OWN S1 fix was still wrong.** It
      asserted 176px keeps the range at "~176-205px (mockup ±18%)" — wrong
      on both counts (a 4px-resolution sweep over 396 widths, 1024-2600px
      plus 3440px, both densities, measured the real range: 177-222px,
      1.02x-1.29x; 220/173 is +27%, not ±18%), and self-contradicted by the
      SAME comment's own sawtooth example ten lines below (220.0px) sitting
      under the 205px ceiling it had just asserted. Corrected in `theme.css`
      (and in `docs/WORKPACKAGES.md`/`web/tests/README.md`, which repeated
      the same wrong figure), WITH the load-bearing clause a bare
      number-fix would have omitted: a <=205px ceiling at every width is
      unachievable with `minmax(F,1fr)` at all — it would need F<=158px,
      already below Compact's own 150px floor — so this was never a value
      the token failed to find. The overshoot mechanism itself
      (`max = F + (F+gap)/n`, worst at the fewest columns, tightening as
      columns are added) is documented in theme.css's `--tile-min` comment,
      along with the operator's sawtooth-not-smoothed decision (re-measured
      example: 220.0px at a 1195px viewport drops to 177.6px at 1215px, a
      ~19% shrink from a 20px WIDER window).

      **S6 (operator decision, round 2):** narrowing the two floors this
      close (176px/150px, a 1.17x ratio) to keep S1's corrected range
      honest made Comfortable and Compact render IDENTICALLY — same column
      count, same tile width within 0.2px — across several sub-1400px
      bands (1024-1076, 1208-1238, ~1396px up). Accepted and documented
      (`theme.css`, this entry) rather than narrowing Compact further: a
      ~135px floor to force visible separation would sit 22% below the
      mockup's design size, with no measurement behind it.

      **S7 (round 2):** the round-1 B1 pin (`.empty{grid-column:1/-1}`) was
      class-specific — generalised into a static-analysis test scanning
      `library.js` for every class appended as a direct child of `.grid`
      (excluding `buildCard`'s "card" output, the grid's actual content)
      and requiring `grid-column` on each; mutation-verified against a
      synthetic new `libnotice` class the same way the reviewer's own
      reproduction worked. Also (round 2): B1's fix is a CORRECTION, not a
      new divergence — the frozen mockup already applies
      `style="grid-column:1/-1"` inline to this exact element
      (`docs/design/vault-app-mockup.html:1826`); the WP 4a.3 port had
      dropped it. And the fix's own call-site inventory was corrected: ONE
      `emptyMessage()` caller in `downloads.js` (not two), plus
      `settings.js:553`'s loading state, previously omitted entirely.

      Nitpicks closed: the hidden-toggle lint's three attribute regexes
      gained the `i` flag (case-insensitive, round 1) AND now also
      recognise `el.hidden ||=`/`??=` and `el.toggleAttribute?.(`
      (optional chaining, round 2 — both one character from an idiom
      already covered, both probed and found surviving), with
      `Object.assign(el, {hidden:true})` documented as a genuine, unclosed
      gap rather than implied covered; `LAYOUT_LABEL[key]` is now the
      single source `segButton` reads its title/aria-label from; the
      redundant `.grid.cols3 .meta .size{font-size:11px}` reset line was
      removed; the BP-XL comment's "1656px is 1920's natural column width"
      corrected to name it as the CONTENT width (the column box itself is
      1688px). Suite 462 green (451 WP 4e.1 baseline + 5 round-1 first pass
      + 4 round-1 fix-round pins + 1 round-2 S7 test + 1 round-2
      compound-operator/optional-chaining test). Rebased cleanly onto
      `da7ceae` (WP 4h.1, api/-only) mid-review with no conflicts in `web/`.
      Re-verified live against the demo 400-game fixture with the 176/150
      values: six-width table (390/768/1280/1440/1920/2560) for both
      densities (e.g. 1920px: 8 cols/194.6px Comfortable, 10 cols/153.3px
      Compact; 2560px: 10 cols/186px Comfortable, 12 cols/153px Compact),
      `.bulk` exact-pixel alignment reconfirmed, BP-M (768px) and base
      (390px) still byte-identical to the pre-4e.2/pre-4e.1 baselines. 400-card
      full grid rebuild measured 21-27ms (WP 4e.1 baseline: ~29-33ms — no
      regression).)*
- [x] The rail narrower, and earning its width: `--rail-w` 232px -> 180px
      (operator verdict at ~2000px, live: "232px feels unnecessarily large
      for the three things in it"; asked whether to narrow or add content,
      the operator chose both), plus vault name (rail head) and a cache
      used/free summary (rail foot) so the rail carries more than the three
      nav items — inserted ahead of WP 4e.3/4e.4/4e.5 in the numbering, at
      the operator's request
      *(WP 4e.6 done 2026-08-18: `--rail-w` narrowed to a live-measured
      180px (theme.css) — a `.nav-btn`'s own content box is 135px
      (x=22-157; corrected from a first-pass 136px/169px that omitted
      `.nav`'s `border-right:1px`, Opus review nitpick), the longest label
      ("Downloads") spanning x=55-120.5 (65.5px); since `.nav-pip` always
      sits flush against the 157px edge, the figure that matters is the
      label-to-pip gap — 21.5px for a one-digit pip, 17.4px for the worst
      reachable case (a two-digit "20"; three digits is impossible,
      `jobs(limit=20)`) — no wrap, no overlap either way; the freed 52px
      flows into `--w-wall` through the EXISTING WP 4e.2 formulas with no
      change needed to them (`.bulk`'s
      Δleft/Δwidth measured exactly 0/0 at 1024/1920/2560px in multi-select,
      confirming they are genuinely parametric on `--rail-w`). Vault name
      (`#rail-vault-name`) via a one-time, gated `GET /v1/settings` fetch
      (`components/rail-panel.js`, mirrors `onboarding.js`'s reconnect-path
      gating); cache used/free (`#rail-cache`) via a FOURTH `store.js`
      resource loop added by this WP — the brief's own claim that
      `GET /v1/cache/summary` was "already polled" was false as of `git
      log` (zero call sites for `api.cacheSummary()` anywhere in `web/js/`
      before this WP), corrected in `store.js`'s header rather than left
      wrong; the new loop has no `keyFn`/diff (a single snapshot, not a
      list — no notification event either, since the Downloads nav pip
      already carries queue state). Both figures distinguish "unknown"
      from a GENUINE zero (`formatBytesGBOrZero`, a new sibling of the
      tile-badge's `formatBytesGB` with the OPPOSITE zero-handling rule —
      "0 B free" must render, not hide as "not known yet"), and a failed
      poll leaves the last successful render on screen rather than
      blanking it (same convention as `bypass-banner.js`'s "clients"
      subscription; live-verified end to end by monkey-patching
      `api.cacheSummary` to reject and confirming byte-identical text
      before/after). **Coordinator addition mid-WP:** a third rail-foot
      line reads `GET /v1/settings`'s confirmed top-level `server_version`
      string (a parallel `api/` package, WP 4e.7, landed the field while
      this WP was in flight) — absent/non-string/empty are all "render
      nothing", rendered as `v<value>` when present, clamped to 24 chars
      before the prefix; `demo-data.js`'s settings fixture gained the same
      field (`"0.1.0"`, matching `vault_api/__init__.py`'s real
      `__version__`) for 1:1 demo parity. Both new rail elements are
      `display:none` below BP-L unconditionally (load-bearing, not
      cosmetic — `.nav` is a 3-column CSS grid there, so two more in-flow
      children would auto-place into a second implicit row without the
      override); base (<720px) confirmed byte-unaffected at 375px/719px.
      All new content is plain, non-interactive text — no new focusable
      control, so "nothing reachable only by hover" holds trivially, and
      `aria-current`/keyboard reachability on the three nav buttons are
      untouched. First pass: suite 498 green (462 baseline + 36).

      **Opus review: PASS, no blockers — ten mutations died by name incl.
      three the reviewer added itself; four should-fixes, all addressed,
      suite 515 green.** **S1:** WP 4e.2's `--w-wall:1600px` comment claimed
      1600px was unreachable at BP-L — true at 232px, false at 180px (the
      cap now genuinely binds at BP-L's own top end, measured: `.view-root`
      1600px capped at 1799px vs. 1599px uncapped at 1794px, ~4px visual
      cost) — exactly the "future `--rail-w` change" scenario that comment's
      own guard warned about, which this WP triggered without anyone
      updating the sentence; fixed in the comment plus a new structural pin
      computing the same breakeven from the live tokens. **S2:** the cache
      loop's cadence (`intervals.gamesMs`) was completely unpinned —
      mutating it to `jobsFastMs` (7.5x more frequent against the one
      endpoint that can trigger a cold depot walk) survived all 498 prior
      tests; fixed with a live-timing pin (developing it surfaced a REAL
      hang — a failing assertion skipping `store.stop()` left a 50s timer
      alive past a 120s harness timeout — fixed with `try`/`finally`).
      **S3:** `rail-panel.js` had zero test coverage; deleting the
      poll-failure guard (blanks the rail instead of keeping the last real
      number) and the `freeText` guard (renders a literal `"Free null"`)
      both passed the full suite. Fixed by refactoring `rail-panel.js` into
      a dependency-injected `createRailPanel()` — the same `store.js`/
      `store-singleton.js` split, applied to a component for the first time
      (`app.js` now makes the real wiring call) — tested headlessly in new
      `rail-panel-wiring.test.js` (15 tests) via `fake-dom.js`. **S4:**
      "render nothing, never a placeholder" only covered the TEXT, not the
      `.rail-head`/`.rail-foot` containers — an empty box with a bare
      divider showed on a default install. Fixed: both ARE now
      `[hidden]`-toggled (guarded in app.css's BP-L block against the
      `display:block` override, same fix class as `.btn[hidden]`), with
      `.rail-foot` hidden only when BOTH the cache summary and the version
      line have nothing to show — pinned as its own AND-not-OR test, and
      live-verified in a real browser (`display:none`, zero height, when
      force-hidden). Nitpicks: the rail-geometry numbers above corrected
      (135px/157px/21.5px, not 136px/169px/12px); the `--tile-min` overshoot
      band's low end restated as exactly 176px (the token's own `minmax()`
      floor, not a third swept approximation); a stale "paints before
      subscribing" claim corrected to the actual order. Divergence recorded
      as D-12 (`docs/WORKPACKAGES.md`) — content beyond what D-1 already
      covers for the rail's mere existence, since the frozen mockup has no
      rail at all.)*
- [x] Overlay geometry at BP-L (detail sheet / notifications / clients sheet
      sizing and placement once the shell is no longer phone-width) — WP 4e.3,
      2026-08-18: centred card (680px via --w-sheet-l, operator-approved range
      680-760) + right drawer at BP-L, mobile byte-unchanged; Opus review
      round 1 FAIL (confirm dialogs -90px off-axis, found by headless-Chrome
      measurement), round 2 PASS (offset 0.0px at three widths); D-13 in
      docs/WORKPACKAGES.md
- [x] Downloads/Settings-specific desktop layout — WP 4e.5, 2026-08-19:
      both views become a capped, centred 760px reading column at BP-L
      (existing --w-text token, no new magic number); multi-column and a
      control/help hybrid considered and rejected with measured reasons
      (D-15 in docs/WORKPACKAGES.md — the 178-char holdnote used to render
      as ONE line across 1931px at 2560). Opus review PASS, centring
      measured 0.0px off the content axis at four widths. PHASE 4e
      COMPLETE with this package.
- [x] Pointer/keyboard interaction model pass (hover states, focus order)
      — WP 4e.4, 2026-08-19: 11 hover rules gated in place behind
      (hover:hover) and (pointer:fine), clipped focus rings fixed via
      outline-offset, desktop scrollbar affordances, bulk bar removed from
      the hidden tab order. Opus round 1 FAIL (relocating the hover rules
      inverted the cascade for five equal-specificity state rules, found by
      live measurement), round 2 PASS (all five re-measured as holding).
      Roving tabindex deferred with honest costs — D-14 in
      docs/WORKPACKAGES.md
      beyond the one rail hover affordance WP 4e.1 added
- [x] **4e.7 (api)** — the server's own version, reported once (`GET
      /v1/settings`) and pinned against drift, so the new left rail can show
      what is actually running rather than a frontend-side constant that
      would only state what the UI believes, not what is running
      (`VAULT_WEB_DIR` can point at a different `web/` than the image
      shipped, so the two really can diverge)
      *(WP 4e.7 done: `vault_api.__version__` (`api/vault_api/__init__.py`)
      is the one hand-maintained constant — no env var, no git describe (no
      `.git` in the image) — now also used for `FastAPI(version=...)` in
      `main.py` instead of a second hardcoded `"0.1.0"` literal;
      `GET /v1/settings` gained a top-level `server_version` field (string),
      a sibling of `readonly`, deliberately NOT a `settings` row (not
      settable, no source/applies semantics) — `PATCH` rejects it exactly
      like any unrecognised key (`422`, generic detail, not the env-only
      one) since it lives in neither `OVERRIDABLE_SPECS` nor
      `ENV_ONLY_KEYS`; deliberately NOT mirrored onto `GET /v1/health`
      (the one unauthenticated route, fixed body by design — a version
      there is free fingerprinting), pinned with a byte-for-byte response-
      text assertion so a future addition trips an assertion, not a review
      comment. Anti-drift pin (`api/tests/test_version_pin.py`) derives
      `vault_api.__version__` and `deploy/compose.yaml`'s three
      `${VAULT_IMAGE_TAG:-0.1.0}` image-tag defaults independently and
      compares them, no Docker/network required; mutation-verified in both
      directions plus a narrower internal-consistency pin catching a
      partial hand-edit across the three `image:` lines. `api/README.md`
      documents the field and states plainly that the number is
      hand-maintained and means "the code in this image", not a published
      release (no release tags exist yet; the publish PATH is now built —
      WP CI-3, 2026-08-22 — but has never executed, so "nothing published"
      remains true until the first tag).
      **Opus review round 1: FAIL — one blocker (B1), two should-fixes,
      three nitpicks, all fixed same day.** B1: the first version's pin
      covered only `deploy/compose.yaml`, while THREE more hand-maintained
      copies of the same number existed unpinned — `api/Dockerfile`'s
      `org.opencontainers.image.version` LABEL (a build-time literal that
      cannot read a Python module, so it can never be collapsed into
      `__version__` itself), `deploy/tests/verify-stack.sh`'s
      `TAG=${VAULT_IMAGE_TAG:-...}`, and `deploy/.env.example`'s example
      line — plus `api/Dockerfile`'s own free-text `docker build` comment,
      fixed by removing the literal instead of pinning a comment. All
      three real sites now have their own named, mutation-verified (both
      directions) pin; two more copies (`core/Dockerfile`,
      `dns/Dockerfile`) stay explicitly OUT of this pin's reach per this
      WP's `api/`+`deploy/`-only footprint and are named as a real,
      documented gap rather than omitted — see `test_version_pin.py`'s
      module docstring for the full table. **S1:** `main.py`'s
      `version=VAULT_API_VERSION` wiring had a comment-only guarantee —
      `version="9.9.9"` survived the full suite (bounded blast radius,
      confirmed empirically: `openapi_url=None` makes `GET /openapi.json`
      404, so the value is unobservable over HTTP either way) — closed
      with a direct `app.version == __version__` assertion through a real
      `create_app()` call, mutation-verified. **S2:** the `/v1/health`
      justification in both `routers/settings.py` and this README
      misattributed §10's "never expose vault-core/port 80" rule to
      vault-api itself; corrected to the real, STRONGER reason — §10's
      public-domain profile fronts vault-api (not vault-core) and can
      legitimately put `/v1/health` on the open internet, which is exactly
      why its body must stay minimal. Nitpicks: the compose precondition
      test now asserts the key SET directly instead of a self-referential
      dict comparison; every by-name lookup uses `.get(...)` so a mutated-
      away key reports a clear value mismatch instead of a bare
      `KeyError`; and this README no longer calls `VAULT_IMAGE_TAG` an
      "already-published" tag (nothing is published yet — it is a locally
      built image's tag). Suite 1617 passed, 1 skipped (baseline 1603/1)
      — 14 new tests.)*
#### Phase 4h — Decision support in the reclaimed desktop width (user decision 2026-08-18)

The desktop portering (Phase 4e) frees width that today carries nothing. The
question "what do we put there" was answered deliberately: **decision support
about the cache, not taste.** SteamHangar knows things Steam does not — what is
on the disk, how much of it sits in SHARED depots, when a game was last
confirmed current, what is installed on which machine, and from
`depot_manifests`, how often a game actually changes. Crossed with playtime
(already relayed and validated, see below) that yields statements nobody else
can make:

- "43 GB cached, 0 minutes played" — prefilled and never touched
- "Installed on your PC, not in the cache" — the real prefill list, not a guess
- "Not confirmed current for 40 days" (`last_manifest_check` already exists)
- "Deleting frees 12 of 43 GB; the other 31 GB sit in depots other games use"
  (pure ADR-0003 data)
- "Changes every three days" vs "unchanged for two years" — the actual input
  for the Phase 4d decision about which games the sweep should keep current

**Deliberately rejected: taste recommendations** ("what should I play next" in
the Steam-discovery sense). They need tags, similar titles and review scores
from the store API, they duplicate a Steam feature, and they turn a cache
manager into a storefront — against the scope discipline in §9. The honest
version of the same idea needs no foreign data and is uniquely ours: *"these
games are fully cached and you have never started them — playable now, no
wait."* The cache is the reason "playable now" is true.

**Privacy stance (user, 2026-08-18), binding on every item below:** playtime
makes the UI judgemental ("never played"), and a household vault has more than
one person in the living room. So: off by default or dismissible at any time,
no nagging, and no number that gets held up to somebody else. This is the same
posture §7 Phase 6 already takes toward client addresses in webhook payloads,
for the same reason. WP 5.3's threat model must cover the new personal-data
surface (playtime and last-played now flow through the API response).

- [x] **4h.0 (api)** — the API-level half of the privacy stance above:
      two independent, off-by-default settings
      (`VAULT_RELAY_EXPOSE_PLAYTIME`/`VAULT_RELAY_EXPOSE_LAST_PLAYED`) gate
      whether `playtime_forever`/`rtime_last_played` may leave the Steam
      relay's response AT ALL, enforced by omitting the JSON key entirely
      (not `0`/`null`) when off. Deliberately environment-only — no
      `PATCH /v1/settings` override — because the `settings` table lives in
      a Docker volume that can be lost independently of the environment,
      and a privacy opt-out must not have "silently starts collecting
      again" as its failure mode; see
      [ADR-0010](adr/0010-relay-privacy-gate-env-only.md) for the full
      argument, including the runtime-toggle design considered and
      rejected. This is the API-only half of the stance: the display-side
      "dismissible at any time, no nagging" half remains 4h.2/4h.3's to
      build. `docs/security/threat-model.md` §4 updated accordingly.
- [x] **4h.4 (app)** — the Android app fetches library + persona through
      vault-api's Steam relay; the device-local Steam Web API key is gone
      (entry UI, EncryptedSharedPreferences storage, direct-to-Valve client
      — deleted, with a construction-time + sign-out scrub for legacy
      installs), no fallback by design so the 4h.0 gate cannot be bypassed
      from a phone. Private-profile limit is a first-class UI state and an
      accepted, recorded cost (ADR-0004 addendum 2). Suite 592→578 both
      variants, ledger reconciled per file; Opus review rounds 1-3
      (FAIL/FAIL/PASS — threat-model truth, pinned-the-fake call-site gap,
      dual-source-set isolation blind spot all closed). 2026-08-19.
- [x] **4h.1 (api)** — two small additions that make the panel sharp:
      `rtime_last_played` carried through the Steam relay (Steam returns it,
      we simply never asked; `playtime_forever` is ALREADY relayed and
      validated — `steam_relay.py`, unused by any frontend today), and a
      per-app change frequency derived from `depot_manifests`. Two honesty
      pins are the point of this package, not the plumbing: **an absent
      upstream field must surface as UNKNOWN, never as "never played"** (0 is
      a claim, absence is not), and **a short observation window must surface
      as "not enough data", never as "changes rarely"** — the manifest history
      starts when WE started watching, so on a young vault every game looks
      stable.
      *(WP 4h.1 done, Opus FAIL→fix→re-verified: `rtime_last_played` added to
      the Steam relay's `OwnedGame`/`OwnedGameOut` — no extra request
      parameter needed (verified against the Steamworks Web API docs, not a
      live account); an absent OR an implausible-sentinel (`<=0`) upstream
      value both degrade to `null`, never a manufactured `0` (pin 1) —
      wording fixed in the fix round: Steam's own convention IS that `0`
      means never-played (real information), discarded here only because
      `playtime_forever` already carries that fact and a 1970-01-01 render
      would be worse; `playtime_forever`'s existing type is untouched
      (additive-only). Change frequency: schema v14 adds
      `depot_manifests.first_seen_at`/`manifest_changed_at`/
      `observation_count` (ALTER-migration with a conservative recorded_at
      backfill, guarded against the table not existing yet); `GameSummary`/
      `GameDetail` gain `manifest_change_frequency`
      (`null`/`"insufficient_data"`/`"stable"`/`"changed"`),
      `manifest_observation_days`, and — fix round, reviewer-authorised
      semantic correction — `manifest_days_since_last_change`: the category
      was originally `"changing"`, which the review correctly called not a
      rate (an app that changed once 3 years ago and one that changes weekly
      were indistinguishable); renamed to `"changed"` (has-changed-at-least-
      once, past tense) and the new field carries the one rate-adjacent fact
      the table can actually support (days since the MOST RECENT observed
      change). Computed by
      `depot_manifests.change_frequency_for_app`/`.change_frequency_by_app`
      (bulk, no N+1, statement-count pinned) with a weakest-observed-depot
      rule: `null` only when an app has NO manifest rows at all (never
      conflated with `"insufficient_data"`, pin 2); fewer than 2 observations
      on any one depot, or an observation window under 14 days (matching
      `VAULT_GC_GRACE_DAYS`'s established scale), both force
      `"insufficient_data"` rather than a confident claim. Fix round also
      closed two review blockers: B1 (the deliverable WP 4h.2 consumes had
      zero endpoint tests — added to `test_games.py`, all four states over
      real HTTP for both routes, plus the null-out mutation re-run and
      confirmed dead by name) and B2 (`change_frequency_by_app` 500'd `GET
      /v1/games` on a poisoned `depot_manifests.appid` row that `gc.py`
      already defends against for the same table — fixed with the same
      `deletion.coerce_positive_id` skip, regression-pinned unit + HTTP).
      Both original honesty-pin mutations re-verified dead by name after the
      fix round. 1603 tests green, 1 skipped, on a tree rebased onto WP 4f:
      1554 baseline + 12 (WP 4f) + 37 across this package's three rounds
      (26 initial, 10 for the untested endpoint surface, 1 real pin for the
      most-recent-change guarantee). An earlier draft of this note claimed
      1580 and "net count unchanged by the fix round" — both wrong, and
      caught by the reviewer in the same diff that shipped them, which is
      why the numbers here are the measured ones. api/README.md documents both fields' exact semantics,
      absence/unknown handling, the observation-window caveat, the NOT-a-rate
      correction, the poisoned-row degrade, and that these fields survive
      cache deletion (unlike `size_bytes`). Not verified against a live Steam
      account/key — recorded on the Zeus/device list, incl. the reviewer's
      own addition: every migrated row starts at one observation, so on Zeus
      the panel legitimately shows `insufficient_data` for every game until
      two post-upgrade observations plus a 14-day window exist.)*
- [x] **4h.2 (web)** — DONE 2026-08-19: both modes shipped (BP-XL right
      column + collapsible card below, one statement module), privacy stance
      structurally pinned (negative pin survived adversarial probing), demo
      fixtures corrected to the shipped default gate. Opus review, FOUR
      rounds: grid auto-placement blocker (browser-measured), a
      measurement dispute settled by full-chain measurement (both rigs
      right, different elements/page states — see LEARNINGS), the column
      released when there is nothing to say. 587→665 web tests. D-16.
      Original bullet: **4h.2 (web)** — the panel itself: a right-hand column at BP-XL
      (>=1800px), a collapsible card below that width. Serves the statements
      above. Depends on 4h.1's fields and on Phase 4e's breakpoints.
- [x] **4h.3 (web)** — DONE 2026-08-19: header art in the detail card
      (aspect-ratio as the only reservation, whole-wrapper collapse on 404
      — verified against the real CDN both ways), 665→676 web tests, Opus
      PASS round 1 incl. four reviewer-own mutations and a hostile-appid
      host-escape probe (impossible by construction). D-17. PHASE 4h
      COMPLETE. Original bullet: **4h.3 (web)** — header art in the detail drawer. Nearly free: a
      predictable `header.jpg` URL on the CDN host the CSP already allows for
      cover art, no relay endpoint, no new external call.
- Screenshots (the `appdetails` store API) are **not** in this phase:
  undocumented, rate-limited, no CORS, so they need their own relay endpoint
  and privacy note — and a screenshot does not help anyone manage a cache.
  If wanted at all, they belong to Phase 6 with the other external
  integrations.

### Phase 5 — Community Release
- [x] README with architecture diagram, quickstart (compose up in 5 minutes),
      and an explicit "works for guests" FAQ note: any Steam client behind
      the LAN DNS is served with zero setup — no account, agent, or app;
      store-on-miss means the first guest download fills the cache for
      everyone (ADR-0001)
      *(WP 5.2: root README with Mermaid diagram, quickstart verified
      command-by-command against deploy/, works-for-guests FAQ plus four
      more ADR-backed entries; feature status stated against shipped code,
      not ADR designs — license section says "planned Apache-2.0" until
      the LICENSE file lands with the release)*
- [x] License: Apache-2.0 (permissive for maximum adoption, includes patent
      grant; AGPL deliberately rejected as it deters contributors and
      companies in the early phase)
      *(WP 5.4: root `LICENSE`, canonical text, copyright line "Copyright
      2026 SteamHangar contributors")*
- [x] **Packaging: ship the web UI in the vault-api image + close the
      env-forwarding gaps** (gap recorded 2026-08-17, closed same day, WP
      P1, two Opus review rounds: FAIL → fix → FAIL → fix → re-verified).
      User decision: bake `web/` into the image (a mount-only fix cannot
      survive `docker pull` of a published image, WP 5.5) — vault-api's
      `build:` in `deploy/compose.yaml` moved to `context: .. / dockerfile:
      api/Dockerfile`, every `COPY` in `api/Dockerfile` became
      repo-root-relative, `COPY web /app/web` was added, and
      `ENV VAULT_WEB_DIR=/app/web` replaces the now-wrong relative default
      inside the image. New root `.dockerignore` keeps the wider context
      from also sending `app/` (Android), `poc/` (Phase-0 evidence) and
      api/'s own dev-only files (`README.md`, `pytest.ini`,
      `requirements-dev.txt` — round-2 review caught these missing from the
      first pass) to the daemon (measured 9.734 MB → 1.54 MB, 84% smaller);
      the old api/-rooted `api/.dockerignore` is deleted and its rules fully
      folded in (Docker only reads a `.dockerignore` at the context root,
      and an api/-rooted build no longer even works); `core/`/`dns/` keep
      their own build contexts, unaffected.
      **Env-forwarding: a complete audit, not a two-key patch** (the
      round-2 reviewer's B1: `.env.example` told operators to set
      `VAULT_MANIFEST_ORACLE_URL` for a privacy mitigation that could not
      work while unforwarded — the exact `docs/LEARNINGS.md` "Containers"
      bug class this package exists to close, re-introduced by an
      incomplete first pass). Every env var `config.py` reads was checked
      against `deploy/compose.yaml`: twelve were unforwarded with no
      good reason and now are —
      `VAULT_EVENT_LOG_PATH`/`VAULT_MANIFEST_ORACLE`/
      `VAULT_MANIFEST_ORACLE_URL`/`VAULT_MANIFEST_ORACLE_TIMEOUT`/
      `VAULT_MANIFEST_KEEP`/`VAULT_EVENT_SWEEP_INTERVAL_MINUTES`/
      `VAULT_MISS_TRIGGER_COOLDOWN_MINUTES`/`VAULT_MISS_TRIGGER_MAX_PER_SWEEP`/
      `VAULT_BYPASS_WINDOW_DAYS`/`VAULT_CLIENT_STATS_KEEP`/
      `VAULT_EVENT_LOG_MAX_BYTES`/`VAULT_WEBHOOK_TIMEOUT_SECONDS`
      (round-2 reviewer independently re-derived all 33 keys from
      `config.py` and reconciled this classification exactly, then proved
      every default byte-identical by diffing `Settings.from_env()` JSON
      dumped from a bare container against one fed exactly what `docker
      compose config` renders; this package additionally proved each key
      arrives with a non-default value too). New `api/tests/
      test_p1_compose_env_defaults.py` (round-2 should-fix S-D) keeps this
      from drifting again without Docker: it derives expected values from
      `config.py`'s own `DEFAULT_*` constants and diffs them against
      `compose.yaml`'s `${VAR:-default}` text on every `pytest` run,
      mutation-tested in both directions (a changed default, a deleted
      passthrough line) — and its first real run caught a genuine,
      harmless pre-existing drift of its own: `VAULT_SIZE_CACHE_TTL`'s
      compose default was the bare string `60` against `config.py`'s
      `60.0`; fixed to match (both parse to the same float, so no behavior
      changed). The rest stay unforwarded for one of three recorded
      reasons: baked into the image (`VAULT_DB_PATH`/`VAULT_CACHE_ROOT`/
      `VAULT_STEAMPREFILL_PATH`/`VAULT_WEB_DIR`); DB-overridable at runtime
      via `PATCH /v1/settings` instead (ADR-0009: `VAULT_NAME`,
      `VAULT_SCHEDULE_*`, `VAULT_WEBHOOK_URL`/`VAULT_WEBHOOK_EVENTS` —
      `deploy/examples/tuned-setup.md` §2 rewritten to point at this instead
      of its now-stale override-file workaround, and its `VAULT_GC_GRACE_DAYS`
      claim corrected, that one having already been forwarded before this
      package); or deliberately env-only, volume-topology-sensitive
      (`VAULT_MANIFEST_ARCHIVE_DIR`, `VAULT_STEAMPREFILL_CACHE_DIR` — new §3
      in `tuned-setup.md`); plus `VAULT_SETTINGS_READONLY`, env-only by
      definition — left unforwarded by P1, closed as a forwarding gap on
      2026-08-18 (Fable audit finding: the one env-only key with NO
      alternative path, so the shipped compose could not enable the
      documented hard-lock at all; now forwarded + pinned). `.env.example` makes the cache-event log pair default-ON
      (user decision, WP 3.11's consumer justifies it) with an honest
      read-not-truncate growth warning, a reachable
      `VAULT_EVENT_LOG_MAX_BYTES=0` escape hatch, an upgrade note that a
      dozen previously-silent-no-op keys now fail loudly at boot if
      malformed, and widens the `VAULT_RESOLVER` warning from
      vault-dns-only to any same-host rewriting resolver (AdGuard
      Home/Pi-hole).
      **The SteamPrefill container-detection trap — corrected twice.**
      Round 1 named only the DNS candidate and treated a DNS miss as
      universally fatal; round-2 review (B2) fixed that to a four-candidate
      model but used `curl`/`ip`, neither of which exists in the
      `python:3.13-slim`-based `vault-api` image (measured: `curl: not
      found`), and its worked-example transcript was mislabelled — the
      `200 OK` shown as `curl` output was actually produced by the
      `python3`/`urllib.request` command this package used throughout, not
      by running `curl` inside `vault-api`, which cannot happen. Round-2's
      OWN measurement (the container's dynamic default-route gateway) also
      turned out not to be the actual mechanism: round-3 review (S-A)
      settled, via a string scan of the shipped SteamPrefill binary, that
      candidate 3 is the FIXED LITERAL `172.17.0.1` (present as a managed
      UTF-16LE string; the only private IPv4 literal there matching the
      documented candidate list — `127.0.0.1` also appears, as candidate 2 —
      and no `host.docker.internal`-style name at all) — not something SteamPrefill
      dynamically detects from `vault-api`'s own routing table. Re-verified
      directly: a real Compose stack's `vault-api` container, whose OWN
      network gateway is `172.19.0.1` (a Compose-managed bridge, not the
      classic default bridge), still reaches the literal `172.17.0.1` and
      gets the heartbeat back when `VAULT_CORE_BIND=0.0.0.0` — ordinary
      host-level routing between the host's own `docker0` and Compose-bridge
      interfaces, not container-to-container traffic (which Docker's
      inter-network isolation WOULD block). The conclusion is unchanged
      (default layout: nothing to fix; dedicated `VAULT_CORE_BIND`: the
      trap is real), only the mechanism description and the diagnostic
      command are corrected. `deploy/README.md`, `deploy/examples/
      truenas-scale-dockge.md` §7.1 and its troubleshooting row now state
      the literal, use `docker compose exec vault-api python3 -c
      "import urllib.request as u; ..."` (verified present and working;
      `curl` stays available only in vault-core's own Alpine image, a
      different question), and every transcript shown was re-run with that
      exact command and pasted verbatim, including the real Python
      traceback tail for the refused-connection case. The `extra_hosts` fix
      recipe itself was correct in every round and stays unchanged —
      confirmed sufficient on its own (SteamPrefill uses the resolved IP
      for all subsequent depot traffic once detection succeeds, per WP
      0.4), with the added requirement that the value be a plain IPv4
      address, never a hostname.
      Verified against real Docker (WSL2, Engine 29.1.3/Compose 2.40.3),
      three review rounds: built the image from the repo-root context, ran
      it standalone with zero volumes and confirmed `GET /` serves the real
      app shell (`docker inspect` showed `Mounts: []`); proved all twelve
      newly-forwarded keys reach `Settings.from_env()` with non-default
      values in a running container; rendered `docker compose config`
      against both the shipped `.env.example` defaults and with every knob
      uncommented; re-ran the exact documented `python3`/`urllib.request`
      diagnostic (not a stand-in) in both `VAULT_CORE_BIND` layouts and
      pasted the real transcripts into the docs; and ran `deploy/tests/
      verify-stack.sh` to completion four times across three review rounds
      (109 checks, up from WP D1's 73): 105/109 passed on the final run,
      every time. The 4 failures are a pre-existing WP D1 bug in step 5i
      unrelated to this package (nginx's `flush=5s` event-log buffer vs. an
      immediate post-request grep with no wait; confirmed by an isolated
      repro) — reproducible on every run so far, but genuinely
      timing-dependent rather than strictly deterministic (a slower/faster
      host could tip the race either way), corrected wording per
      `docs/LEARNINGS.md`. Every check this package added, across all three
      rounds, passed on every run; api suite 1482 passed/1 skipped on the
      final run (1461 baseline + 21 new in `test_p1_compose_env_defaults.py`).
      **Fixed in WP 4g** (2026-08-18): step 5i's grep is now a bounded
      wait-for-line loop instead of an immediate read, so this 105/109
      result is historical — see `verify-stack.sh`'s step-5i comment and
      `deploy/README.md`.
- [x] **Product rename SteamVault → SteamHangar** (WP RN-1, 2026-08-19):
      the working title collided with piracy tools and an established
      achievement tracker. Three tiers — product word and image namespace
      renamed; Kotlin package `dev.steamvault.app`, the `steamvault://`
      scheme, internal `vault-*`/`VAULT_` names, and every dated historical
      record deliberately NOT renamed. Two persisted-state identifiers
      (Android prefs file, web localStorage keys) left on the old name so no
      existing install silently loses its saved connection — a migration is
      a separate package. See docs/adr/0013-product-rename-steamvault-to-
      steamhangar.md for the full change list, the wire-visible items, and
      the open follow-ups.
- [x] CI: GitHub Actions — lint, tests, multi-arch image build (amd64/arm64),
      publish to ghcr.io with pinned version tags
      *(BUILT, in four packages; the publish path itself has never
      executed — no tag exists — so every "nothing is published yet"
      statement elsewhere in this plan remains true until the first tag,
      which is the user's click. WP CI-1, 2026-08-21, commit 72a0aeb: the
      web suite (node --test) and the verify-stack integration suite join
      CI — verify-stack nightly/manual only, isolated from the PR gate.
      WP CI-2, 2026-08-21, commit e8a09ca: the Android Gradle suite joins
      the PR gate (assembleDebug + both unit-test variants + lintDebug),
      closing the last blind spot; known residual: a Gradle cache hit
      skips distributionSha256Sum re-verification, recorded in the threat
      model. WP CI-3, 2026-08-22, commit 3f5fd0e: publish.yml on v* tags
      + workflow_dispatch — four images to ghcr (vars.GHCR_NAMESPACE,
      flavor latest=false, unknown/unknown manifest guard, per-platform
      SteamPrefill smoke), plus android-release (secrets-gated signed
      APK, apksigner verify, if:always() keystore cleanup). WP AGENT-BIN,
      2026-08-22, commit 29d875c: vault-agent binaries for the three
      ADR-0005 targets + SHA256SUMS attached to the same release; static
      CGO_ENABLED=0, per-target file-type guard, VERSION sanitized for
      slashed refs (workflow_dispatch rehearsal), release notes generated
      by exactly one job per tag. Known, commented: re-pushing the SAME
      tag appends release notes a second time. First-tag advice recorded
      by review: a throwaway v0.0.1-rc1 pre-release to exercise the
      never-run path end to end.)*
      *(WP 5.1 done 2026-08-09 — the test/lint half: api pytest (Linux),
      agent go build/vet/test (Linux+Windows matrix), core `nginx -t`
      through the image's REAL entrypoint render path (pinned upstream
      image derived from core/Dockerfile) + config-drift check +
      shellcheck, PS 5.1 parser/pure-ASCII gate + PSScriptAnalyzer
      PSUseCompatibleSyntax(5.1); actions SHA-pinned, contents:read,
      NO publishing. Manual/network harnesses documented as CI-excluded.
      First gate exposed a real WP 3.10 bug — 25-vault-eventlog.sh's
      survivor check made vault-core unable to start in its default
      config; fixed in a separate fix(core) commit. Image build/publish
      remains WP 5.5, user-gated. Opus PASS + delta-confirmed. CI run #1
      on real runners: 4/5 jobs green incl. all three locally-unverifiable
      ones; the api/pytest Linux job found a second real bug — a
      Windows-only-measured path guard accepting `a\b`/`C:x` on POSIX —
      fixed as fix(api), run #2 green expected.)*
- [x] CONTRIBUTING.md, issue templates, example configs
      *(WP 5.4: root `CONTRIBUTING.md` with verified per-component test
      commands (api pytest: 704 passed/1 skipped; agent go build/vet/test:
      all packages green, incl. a documented CRLF/`gofmt -l` checkout
      caveat) and an honest local-only-vs-CI test matrix; GitHub issue-forms
      `.github/ISSUE_TEMPLATE/{bug_report,feature_request,config}.yml`
      (YAML-validated) + `.github/PULL_REQUEST_TEMPLATE.md`;
      `deploy/examples/{minimal-lan,tuned-setup}.md` plus an examples
      `README.md` index — `tuned-setup.md` documents a real gap (scheduler/
      GC env vars are read by `vault_api/config.py` but not yet forwarded
      by `deploy/compose.yaml`) with a working Compose-override recipe
      rather than a silently-broken `.env` example)*
- [x] **SECURITY.md + threat model** (WP 5.3 docs half, 2026-08-18; the
      pre-release security review below is a separate, still-open half of
      the same work package — see that item). Root
      [`SECURITY.md`](../SECURITY.md): supported-versions note (pre-release,
      no tags yet), reporting via GitHub's private vulnerability reporting
      for this repository (no email address anywhere, per the operator's
      constraint). The substantial part is
      [`docs/security/threat-model.md`](security/threat-model.md): every
      behavioural claim cited by file/line (or, for `docs/PROJECT_PLAN.md`
      itself, a section-plus-quote anchor rather than a line number, since
      this file grows under active editing) against the shipped code, not
      the ADRs' designs (same discipline WP 5.2 established for the README).
      Covers the LAN trust boundary and what an untrusted device on it can
      already do (`vault-core`'s `/depot/` path is unauthenticated by
      design, and its Host allowlist restricts *where* it can relay, not
      *who* may use it), the cache contents and why they are not a licence
      bypass, ADR-0004's credentials claim verified against the code (and
      exactly where a real Steam session lives at rest —
      `vault-steamprefill` volume), the WP 4h.1 personal-data surface
      (playtime/last-played: the API-level half of the operator's privacy
      stance has landed since — WP 4h.0's two env-only, off-by-default
      gates mean the API no longer answers unconditionally to anyone
      holding the one shared key. What remains open is the display-side
      half: no UI renders either field yet, so "off by default or
      dismissible" cannot be said fully met until WP 4h.2/4h.3 ship;
      flagged for whoever builds those to read the threat model's own
      framing first), outbound data flows (the opt-in Steam relay,
      the opt-in manifest oracle, and the CSP's one external cover-art
      host — what leaves the LAN and under what conditions), the ADR-0008
      event log, precisely what `VAULT_SETTINGS_READONLY` and the bypass
      banner do and do not mean, and supply-chain pinning (digest-pinned
      base images and SHA-pinned CI actions vs. this project's own
      mutable-tag image references, pending WP 5.5's first publish). Named
      plainly as out of scope rather than glossed over: physical host
      access, a compromised LAN, a malicious operator, and in-LAN DoS via
      the unauthenticated relay-and-store path. Reviewed by Opus (round 1:
      FAIL, four must-fixes — two broken-by-drift citations into this very
      file, an understated personal-data claim, a missing outbound-data-flows
      section, and the plan tick overreaching the delivered scope — all
      fixed; round 2: FAIL, two blockers plus should-fixes — §4 citations
      drifted again when WP 4h.0 rewrote that section in the same commit
      that added 109 lines to `routers/steam.py`, and §5's outbound-flows
      list falsely claimed exhaustiveness while omitting the Android app's
      own direct Steam Web API and OpenID calls — addressed in the WP
      5.3-fix follow-up commit; round 3: PASS, 2026-08-18). Several claims
      explicitly could not be substantiated from code alone and are listed
      as open gaps in the document's own closing section rather than
      asserted anyway.
- [ ] **Pre-release security review (WP 5.3 review half, Fable mandatory)**
      — after an api/core code freeze. Not started: `api`/`core` are not
      frozen (4h.2/4h.3 are open, and WP 4h.4 is in review),
      so this cannot start yet regardless of the docs half above being done.
      See §11 item 5.
- [ ] Announcement: r/selfhosted, r/homelab, LanCache Discord (stay fair:
      frame as a complement/alternative, not a "LanCache killer")

### Phase 6 — External Integrations (post-release, user decision 2026-08-10)

Deliberately NOT a release blocker: WP 3.13 already ships working generic
webhooks, and everything below is additive on top of them. Scheduled after
the community release so no integration work delays it.

Motivation: the WP 3.13 envelope is consumed happily by n8n and any generic
JSON receiver, but the Phase 3 checkbox claiming "Discord/Slack/ntfy-
compatible" overstates it — a Discord webhook accepts only `{"content": …}`
or `embeds` and rejects our envelope with 400. Today Discord needs a relay
in between. Phase 6 closes that gap and adds the events an automation
platform actually wants to react to.

- [ ] Multi-target delivery: `VAULT_WEBHOOK_TARGETS` — N receivers, each
      with its own URL, event filter, format and headers, so n8n and Discord
      can be fed at the same time (today: exactly one URL)
- [ ] Vendor format adapters `generic|discord|slack` (user decision
      2026-08-10: adapters in the backend, NOT a free-form body template —
      Discord must work without a relay; the cost is that we maintain two
      foreign formats). Includes honouring `Retry-After` on 429, which
      Discord does send and the current 3-attempt/0.2-0.5 s backoff ignores
- [ ] `app.updated` — the update notification. **No oracle dependency**
      (user decision 2026-08-10, superseding the earlier "either source"
      answer): the trigger is the existing non-forced scheduler sweep, which
      per `docs/research/phase3-manifests.md` costs ~3 s and zero bytes for
      an up-to-date app and is therefore already the manifest check. The
      changed manifest ids come from WP 3.2 ingestion, the byte/`Updated`
      counters from the WP 3.3 summary parser — so this is a notification
      hook on machinery that already runs, not new detection.
      The honest semantics are "the vault HAS this update" rather than
      "an update is available"; for a homelab that is the more actionable
      message. `VAULT_MANIFEST_ORACLE` (ADR-0006 tier 3) stays exactly what
      it is — an opt-in pre-emptive badge — and never becomes a
      precondition for notifications
- [ ] Outbound auth beyond Basic-in-URL: custom headers (n8n commonly wants
      a header token) + optional HMAC-SHA256 signature, plus a per-event
      `event_id` so a receiver can deduplicate across retries
- [ ] `POST /v1/webhooks/test` — fire a synthetic event at a target. Needed
      by any settings UI (Phase 4) and turns webhook setup from guesswork
      into one click
- [ ] Integration docs: n8n in BOTH directions (receiving events; calling
      `/v1/jobs` to trigger a prefill), Discord, ntfy
- [ ] Structured log export — opt-in `VAULT_EVENT_LOG_STDOUT` mirroring the
      cache-event TSV (WP 3.10) to vault-core's stdout, so Loki/Promtail/
      Vector-style collectors can ingest the machine-readable events without
      mounting the volume (user request 2026-08-17: keep it open for the
      community even though the requester uses Dozzle). Deliberately NOT a
      Dozzle gap: Dozzle reads container stdout, where vault-core's
      human-readable per-request log with HIT/MISS/BYPASS already is — a
      file-tailing log viewer is what the TSV would need, and no popular
      Docker log viewer does that. Cost side: doubles the line volume inside
      the json-file ring buffer, and the config-drift check's pinned
      `access_log` counts must be updated in the same change. Off by default
- [ ] Named, scoped API keys — the direct consequence of inviting external
      systems in (user question 2026-08-10). Today there is exactly ONE
      key: `auth.require_api_key` compares against `VAULT_API_KEY` for every
      router, so the key an n8n flow needs in order to enqueue a prefill is
      the same key that may `DELETE /v1/cache/{appid}`. Wanted: several
      named keys (web UI, agent, Android app, n8n) with a coarse scope —
      read / enqueue / destructive — each revocable on its own, so a leaked
      automation token does not mean rotating every agent in the house.
      Note this is about BLAST RADIUS, not about identity: it does not make
      the vault multi-user
- [ ] Payload scoping per target: a Discord channel is a room full of
      people, and `client.bypass_*` payloads carry `client_id` plus every
      IP address a device reported from, while job/update events reveal
      which games the household owns. A `full|minimal` payload mode per
      target (minimal drops device and address fields) is the cheap,
      correct fix — authentication cannot solve a receiver-side visibility
      problem
- Open, deliberately deferred: PER-USER webhooks ("tell ME when MY games
  update") require a real user identity, which only arrives with Phase 4a's
  Sign in with Steam (ADR-0004). Until then a vault has one operator's view
  and the webhook config belongs to that operator. Revisit after Phase 4a
- Explicitly rejected: persisting the delivery queue across restarts.
      At-most-once is the right hardness for homelab notifications
      (`webhooks.py` module docstring)

### Runner split & egress enforcement (S-track, decided 2026-08-19)

Operator decision after WP EG-1's first attempt STOPPED pre-implementation
(the coder proved the egress lock's promise unkeepable while SteamPrefill
runs as a subprocess of vault-api — network namespaces are per container;
ADR-0011 is reserved for the egress lock's ADR when EG-1 lands, which is
why 0012 precedes it in time): split first, then lock.

- [x] **S-1 (api)** — prefill runner split: VAULT_PREFILL_MODE
      subprocess|queue, schema v15 lease columns, atomic claim (two
      independent sufficient mechanisms, measured as 8 OS processes),
      no-lease-stealing crash semantics, bounded stale-lease tests after a
      mutation class was shown to wedge CI silently. ADR-0012. Opus review
      rounds 1-2 (FAIL: queue-mode pause→resume was an inescapable dead
      end, found by live probe; fixed + integration-pinned → PASS).
      1672→1729 api tests. 2026-08-19.
- [x] **S-2 (deploy)** — DONE 2026-08-19: vault-runner service (same
      image, queue mode on both sides, health-gated against a
      fresh-volume race found empirically), credential volume moved off
      vault-api, HOME volume shared (CI-pinned — the silent-forever
      ingestion case), unused cache mount dropped from the broad-egress
      container on review. verify-stack 112→149 checks, api tests
      1729→1747. Opus rounds 1-3 (version-pin guard silently disabled by
      the duplicate image line — re-keyed by service; a tuned-setup
      recipe asserting a mechanism the code lacks — rewritten to move the
      volume, not the variable).
- [x] **EG-1 (deploy, resumed)** — DONE 2026-08-19: the egress lock.
      vault-api on vault-lan (no-masquerade) + vault-egress (internal),
      allowlist tinyproxy as shipped default, VAULT_EGRESS_ALLOW, the
      five-minute tcpdump/router verify recipe, completeness pins. ADR-0011.
      Two channels the lock does NOT close are named as accepted gaps in the
      ADR and threat-model, both measured live: DNS resolution (resolver
      forwards from the host namespace) and the Docker host's own address
      (replies need no masquerade). verify-stack 149->185, api 1747->1809.
      Opus rounds 1-2 (FAIL: two security-doc absolutes overstated the lock,
      caught by the reviewer actually exfiltrating; a CRLF gitattributes gap
      that would crash-loop the proxy on a Windows checkout; the proxy image
      was never built by verify-stack -> all fixed). HARDENING CHAIN
      COMPLETE.

---

## 8. Repository Structure (Monorepo)

```
steamhangar/
├── core/            # nginx config, Dockerfile
├── dns/             # optional dnsmasq container (Compose profile)
├── api/             # FastAPI, SQLite schema, scheduler
├── agent/           # PC listener (Windows)
├── app/             # Android (Kotlin + Go tsnet module)
├── deploy/          # compose.yaml, example .env, DNS mode docs
├── docs/            # architecture, ADRs, setup guides
└── .github/         # CI, templates
```

---

## 9. Risks & Open Questions

| Risk | Severity | Mitigation |
|---|---|---|
| `proxy_store` incompatible with range requests | HIGH | Phase 0 resolves this; Plan A fallback defined |
| Steam changes its CDN URL scheme | MEDIUM | Log schema anomalies with alerts; abstract the mapping layer |
| Public-domain profile exposes the API to the internet | MEDIUM | TLS mandatory, strong bearer token, docs strongly recommend forward-auth/OIDC + rate limiting in the reverse proxy; API designed with no unauthenticated endpoints |
| Shared depots → incomplete deletion | LOW | Shared detection + transparent reporting |
| tsnet gomobile build complexity | MEDIUM | Profile abstraction means tsnet can ship later; system-VPN profile works day one |
| Single-maintainer risk | MEDIUM | Small scope (Steam only), good docs, permissive license lower the contribution barrier |
| SteamPrefill upstream dependency | LOW | Used only as a CLI subprocess, replaceable |

**Deliberately OUT of scope (v1):** multi-service (Epic etc.), multi-tenant
setups. *(The web UI moved INTO scope as Phase 4a by user decision on
2026-08-06 — it shares the app's design language and API surface and is
served by vault-api itself.)*

**Post-v1 roadmap:** an iOS app. The groundwork is deliberately kept
compatible: the app talks pure REST to vault-api, the connectivity-profile
abstraction is UI-framework-agnostic, and tsnet builds for iOS via the same
gomobile toolchain as the Android `.aar`.

---

## 10. Deployment Notes (generic)

- vault-core needs to answer on **port 80** (Steam CDN traffic is plain HTTP).
  If another service occupies port 80 on the host, use a dedicated IP
  (IP alias, macvlan, or a dedicated VLAN interface).
- **DNS redirection — three modes, pick one:**
  1. **Existing local DNS** (recommended for homelabs): rewrite
     `*.steamcontent.com` → cache server IP in AdGuard Home, Pi-hole,
     dnsmasq or Unbound. Block/rewrite AAAA records too — IPv6 fallback
     silently bypasses the cache.
  2. **Bundled vault-dns** (no local DNS server required): enable the
     optional dnsmasq container and point your router's DHCP DNS at it.
     AAAA handling is covered by vault-dns's config (`address=` +
     `local=` pairing — see the vault-dns component note in §3).
  3. **DNS-free hosts mode** (single Windows gaming PC, simplest setup):
     a `lancache.steamcontent.com` hosts entry — manually or automated by
     vault-agent (opt-in). Windows Steam client only.
- **Remote access (vault-api only — never expose vault-core/port 80):**
  - Tailscale: reusable auth key for the app's embedded tsnet node, or the
    regular Tailscale client app
  - Twingate: define vault-api as a Twingate resource; the app uses the
    system-VPN profile
  - Public domain: front vault-api with your reverse proxy (Traefik, Caddy,
    NPM, Cloudflare Tunnel); TLS required, forward-auth/OIDC strongly
    recommended on top of the API key
- `/v1/health` is designed to be polled by any external monitoring system.

---

## 11. Next Steps

The original three steps here (build the Phase-0 PoC, create the public
repository, then start Phase 1) are all DONE — Phase 0 answered the
`proxy_store` question in favour of Plan B (ADR-0001), the repo exists, and
Phases 1–3 plus 4a shipped. Rewritten 2026-08-17 to reflect the real state.

1. [x] **Packaging package** (§7 Phase 5, first bullet) — DONE, WP P1: `web/`
   baked into the vault-api image, the full env-forwarding gap audited and
   closed (not just `VAULT_EVENT_LOG_PATH` + `VAULT_MANIFEST_ORACLE` —
   twelve keys total, see §7 Phase 5's own bullet for the complete list),
   `deploy/tests/verify-stack.sh` extended and run for real. What follows is
   unchanged by it: next up is item 2 below.
2. [ ] **Zeus rollout** — joint interactive session (see the Deployment
   section of `docs/WORKPACKAGES.md`). Two user-side blockers first: the
   stale HADES Tailscale subnet route, and the Fritz!Box announcing itself
   as an IPv6 DNS server via RA (a live cache bypass). This session is also
   where the honest still-open lists in `web/tests/README.md` and
   `app/README.md` get verified — real screen reader, phone browser cover
   art, GC against real on-disk chunks, real multi-client bypass detection.
3. [x] **WP 4b.9** Android release build + signing docs + APK distribution
   (keystore creation is a user action), plus the review carry-over list in
   `docs/WORKPACKAGES.md` — done 2026-08-17, together with WP 4b.10 (the
   clients/bypass detail surface); see §7 Phase 4b's evidence notes.
4. [x] **Phase 4c / 4d** frontend halves — CLOSED 2026-08-22: the manual
   "check & update" trigger in both UIs (web WP 4c-web, Android WP 4c-app),
   and the now-default-on "keep the cache current" sweep mode (WP SWEEP-1
   flipped it — and its auto-GC pair — from opt-in to the shipped default,
   docs/adr/0014-sweep-cached-and-auto-gc-default-on.md). Its frontend
   surface shipped in both UIs the same day: web as WP 4d-web (commit
   c825625, see §7 Phase 4d's closing note), Android as part of WP AG-3
   (commit 0a3bfc5) — toggle, GC-risk warning and three-way last-sweep
   status, the Kotlin port pinned by string equality against the
   twice-reviewed web copy.
5. [x] **WP 5.3 docs half** — `SECURITY.md` + `docs/security/threat-model.md`,
   done 2026-08-18 (see §7 Phase 5). Round-2 review ran and FAILED (two
   blockers plus should-fixes, mostly citation drift from WP 4h.0 landing
   in the same commit range plus a false §5 exhaustiveness claim) —
   findings addressed in the WP 5.3-fix follow-up commit; round 3: PASS
   (2026-08-18).
6. [ ] **WP 5.3 review half, still open** — pre-release security review
   (Fable mandatory), after an api/core code freeze that has not happened
   yet.
7. [ ] **User-gated:** WP 5.5 — the machinery is no longer the gate. The
   GitHub org exists, the repo lives at `steamhangar/steamhangar`, and the
   full publish path is built and reviewed (WP CI-3 + WP AGENT-BIN, see §7
   Phase 5's CI bullet): images, signed APK, agent binaries, checksums.
   What remains is exactly one user action — pushing the first tag — and
   review's standing advice is a throwaway `v0.0.1-rc1` pre-release first,
   because the path has never executed and the release page is the only
   place its end-to-end result can be seen (re-pushing the SAME tag
   double-appends release notes; known, commented at the site). WP 5.6
   (announcement) stays gated on the user's own end-to-end test with the
   Android app. Phase 6 integrations are deliberately post-release.
8. [x] **The AG series — agents become first-class residents** — COMPLETE
   2026-08-22, four packages in one day (user's go for the full chain).
   AG-0 (commit 74e7727): client identity visible and attributed at
   startup (source + sanitize note), install-time preview reading the
   same hostname source as Go (the COMPUTERNAME case defect found in
   review would have minted ghost identities), rename semantics
   documented honestly. AG-1 (commit 54e7622): installed_on on the games
   endpoints with the scheduler's own freshness gate resolved against
   effective settings (round 1 caught the shared-function/unshared-bound
   divergence), plus DELETE /v1/clients/{client_id} for ghost rows.
   AG-2 (commit 0971a1d): the web badge and the payoff statement
   "installed but not cached", after a round-1 FAIL in which every DOM
   surface was deletable green — root cause a stub fake-dom whose no-ops
   silently swallowed the calls; the harness itself was extended and now
   throws on unsupported selectors. AG-3 (commit 0a3bfc5): Android
   parity for the badge plus the sweep surface, after the same
   wiring-unpinned FAIL on the same day, fixed with the codebase's
   strongest anchor generation; the demo's stale pre-ADR-0014 default
   (which made screenshots show a warning no real fresh install shows)
   corrected and pinned by the Android twin of the web's config drift
   guard. Both frontends' demo modes now exercise every badge state.
9. [x] **WP APP-DEMO** — Android demo mode, done 2026-08-22 (commit
   bdb2c19): screenshot-grade fixtures behind the onboarding skip link,
   no account, no vault, no network; six repository seams, compile-time
   fixture/model coupling, DEMO MODE banner unscrollable on every
   surface. Four review rounds; the durable outcome is recorded in
   docs/LEARNINGS.md (the guarantee-vs-mechanism ceiling of name-based
   isolation scans). Real-device residuals listed in app/README.md.
10. [ ] **Download throttling, possibly time-dependent** (operator request
    2026-08-30, roadmap entry — not yet a scoped work package). Cap the
    upstream (Steam → vault) bandwidth; LAN serving stays uncapped. The
    time-dependent shape ties into the shipped schedule window: full
    speed inside 03:00-07:00, capped outside it, so a daytime manual
    prefill ("I want this game tonight") does not saturate the WAN while
    people use it. Scoping questions to answer before briefing: does
    SteamPrefill expose a native concurrency/rate option (check upstream
    before building anything); if not, container-level shaping in
    compose vs. documenting router QoS as the supported answer; and how
    a cap interacts with the window scheduler (a capped sweep takes
    longer than its window — does it stop at the window edge or run
    over?). Interim answer that works today with zero code: per-device
    QoS on the operator's router (Omada), which throttles exactly the
    Steam-facing direction. README Roadmap carries the user-facing
    version of this entry.
11. [ ] **Macvlan deploy example — vault-core on its own LAN IP** (operator
    request 2026-08-30, roadmap entry — not yet a scoped work package).
    Steam clients require plain HTTP on port 80 (Steam's choice), so on
    a host whose port 80 is already owned by a reverse proxy (Traefik,
    NPM — the homelab default) vault-core needs a second address. Today
    that means a host-side IP alias created in the platform's own UI
    (TrueNAS middleware, so it survives reboots). The comfortable
    shipped answer: a documented compose override giving vault-core its
    own LAN IP via a macvlan network — Docker-native, no host network
    clicks, the established lancache pattern. Must ship as an EXAMPLE
    with its caveats stated, not as a silent default: the host cannot
    reach a macvlan container directly (affects the host-side health
    probes and any same-host DNS rewrite target), the parent interface
    and address range are operator-specific, and Dockge/TrueNAS
    interaction needs a real test on the deployment this was requested
    for. Belongs in deploy/examples/ beside minimal-lan and tuned-setup,
    with a verify recipe; footprint deploy/ + docs only.
