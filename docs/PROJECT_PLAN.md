# SteamHangar — Project Plan

A Steam game cache with true per-game management — self-hosted, Docker-first.

Status: IMPLEMENTATION — Phases 0–3 and 4 (4a–4e, 4h; the embedded tsnet
profile is deferred post-v1), the S-track (runner split + egress lock,
WP S-1/S-2/EG-1) and the CI/publish machinery (WP CI-1..3, AGENT-BIN)
complete; Phase 5 open only on the pre-release security review, the first
tag and the announcement (see §11 Next Steps) · License: Apache-2.0

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
                        ┌──────────────────────────────────────────────────┐
                        │                  Cache Server                    │
  Steam clients on the  │  ┌──────────────────┐     ┌────────────────────┐ │
  LAN (DNS rewrite      │  │ vault-core       │     │ vault-api          │◀┼── vault-agent
  *.steamcontent.com ───┼─▶│ nginx proxy_store│     │ FastAPI + SQLite   │ │   (gaming PC /
  → cache server, or    │  │ /vault/cache/    │◀────│ jobs, scheduler,   │ │    Steam Deck:
  hosts-file mode)      │  │   depot/<id>/... │     │ GC, settings, web  │ │    reports
                        │  └────────┬─────────┘     │ UI + Steam relay   │ │    installed
                        │           │ MISS          └───┬────────────┬───┘ │    games)
  Web UI (browser) ─────┼───────────┼─────────────── ▲  │ job queue  │      │
  Android app ──────────┼───────────┼─────────────── ┘  │ (SQLite)   │ egress
   (system VPN /        │           │              ┌─────▼──────┐  ┌──▼─────────┐
    public domain)      │           │              │vault-runner│  │vault-proxy │
                        │           │              │SteamPrefill│  │allow-list  │
                        └───────────┼──────────────┴─────┬──────┴──┴─────┬──────┘
                                    ▼                    ▼               ▼
                              Steam CDN            Steam CDN       api.steampowered.com
                            (store on miss)     (prefill, via      (+ optional oracle host)
                                                 vault-core)
```

vault-api has no direct route to the internet: its only outbound path is
vault-proxy (WP EG-1, ADR-0011). Prefill execution lives in vault-runner
(WP S-1/S-2, ADR-0012), the one component with an ordinary internet route
— it downloads through vault-core like any Steam client, so every chunk
lands in the cache. Both frontends talk pure REST to vault-api; the
Android app reaches it over a system VPN or a public domain (the embedded
tsnet profile is post-v1), and since WP 4h.4 fetches library data through
vault-api's Steam relay rather than calling the Steam Web API itself
(ADR-0004 addendum 2). Cover art still loads from Steam's public CDN.

### Components

**vault-core** — the cache itself
- nginx container with `proxy_store`: Steam CDN responses are stored
  path-faithfully under `/vault/cache/depot/<depotid>/...` (nginx runs
  with `-p /vault`; the same tree is `VAULT_CACHE_ROOT` for vault-api)
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
  - Prefill orchestration: a job queue in SQLite; execution is handed to
    vault-runner in the shipped Compose stack (`VAULT_PREFILL_MODE=queue`,
    ADR-0012), with an in-process subprocess mode kept for single-process
    development runs
  - Per-app status tracking (idle / running / done / error / stale)
  - Per-game size calculation (du over depot folders, cached)
  - Per-game deletion (remove the app's depot folders)
  - Scheduler (configurable daytime window, runs over the installed list
    plus, by default, everything already cached — ADR-0014)
  - Persisted runtime settings (`GET/PATCH /v1/settings`, ADR-0009) and
    the opt-in Steam Web API relay both frontends use for library data
  - Serving the web UI as static files (Phase 4a)
- REST API (see section 6)
- No direct route to the internet: vault-api sits on a no-masquerade LAN
  network plus an internal egress network whose only exit is vault-proxy
  (WP EG-1, ADR-0011)

**vault-runner** — prefill execution (S-track, ADR-0012)
- Same image as vault-api, started as `prefill_runner`: claims queued
  prefill jobs under a heartbeat lease and runs SteamPrefill against the
  Steam CDN — through vault-core, so every downloaded chunk is stored
- The one component that needs an ordinary internet route; it holds the
  SteamPrefill credential volume, which is no longer mounted into vault-api
- A dead runner's lease expires and the job is re-queued, never stolen

**vault-proxy** — the egress lock (WP EG-1, ADR-0011)
- Allow-list HTTP proxy (tinyproxy) that every outbound call from
  vault-api's container must pass through; `api.steampowered.com` is baked
  in, `VAULT_EGRESS_ALLOW` adds hosts (the manifest oracle's, if enabled)
- Two channels it does not close are documented as accepted gaps in the
  ADR and the threat model: DNS resolution (the embedded resolver forwards
  from the host namespace) and the Docker host's own addresses

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
  30 min) via HTTP POST to vault-api — over the LAN or whatever VPN
  reaches it. Removed titles
  are derived server-side by diffing against the previous report — the
  agent stays stateless and dumb by design
- Runs as a scheduled task / optional tray icon; config: one URL + API key
- Deliberately dumb: read + report only, no control logic
- **Optional hosts-file mode (opt-in, requires admin rights):** writes a
  `lancache.steamcontent.com → cache IP` entry into the local hosts file.
  The Steam client checks this hostname itself and uses it as a cache when
  it resolves — no DNS server needed at all. For a single gaming PC,
  Windows or Linux/SteamOS: Phase 0 (WP 0.6) showed the current Linux
  client performs this lookup too, so the mode is platform-neutral
  (`agent/README.md`, "The Linux-client finding"); only the hosts-file
  path and the elevation wording differ.
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
  - **Embedded Tailscale (tsnet) — post-v1, not built:** Go Mobile `.aar`
    bridge, auth-key based. Zero-config for the user beyond pasting an
    auth key. Tailscale only. Designed as an additive profile; day one is
    covered by the System-VPN profile with the regular Tailscale app.
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
/vault/cache/                   ← container-real path (nginx -p /vault)
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
DELETE /v1/cache/{appid}
  → mapping: appid → [depotids]
  → rm -rf /vault/cache/depot/<each depotid>
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
| POST | /v1/prefill/cached | No body: queue a check-and-update job for every app that holds cache content — the manual "check & update" (WP 4c), same selection the sweep uses (WP 4f) |
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
| GET | /v1/steam/key | Steam Web API relay (WP 4a.6r, ADR-0004 addenda): whether a server-side key is configured — never the key itself |
| PUT | /v1/steam/key | Set or replace the server-side Web API key (one operator key for both frontends since WP 4h.4) |
| DELETE | /v1/steam/key | Clear the configured key |
| GET | /v1/steam/owned-games | Relay `GetOwnedGames` for a SteamID64; playtime/last-played fields are omitted unless the privacy gate opens them (WP 4h.0, ADR-0010) |
| GET | /v1/steam/player-summaries | Relay `GetPlayerSummaries` (persona name, avatar) |
| GET | /v1/settings | Effective runtime settings with the source of each value (settings API, ADR-0009) |
| PATCH | /v1/settings | Partial update of the runtime-editable keys, same grammar as startup; `403` under `VAULT_SETTINGS_READONLY` |
| GET | /v1/about | Version, commit and status of vault-api (web UI), vault-core, vault-runner, SteamPrefill, vault-proxy and vault-dns (WP VER-2; vault-core from a file on the cache volume, the proxy by reachability only, vault-dns never probed) |
| GET | /v1/covers/{appid}/{signature} | Cover image vault-api stored for a tracked app, ETag + long `Cache-Control` (WP D10 COVER-CACHE-1). No `X-Api-Key` header: the signature in the path is an HMAC of the API key, handed out only by the authenticated `GET /v1/games` (`cover_cached_url`) |
| GET | /v1/health | Liveness (for external monitoring) — the one unauthenticated route, besides the web UI's static files |

Auth: static API key in a header (v1) on every route except `/v1/health`
and the web UI's static files (threat model §1/§7). One route takes the key
in derived form instead of the header: `GET /v1/covers/{appid}/{signature}`,
whose path carries an HMAC of the key because an `<img>` cannot send a
header (WP D10, api/README.md "Auth"). Since everything is
only reachable over the LAN or a VPN, this is sufficient; OIDC/forward-auth
is a later option for users who expose the API differently.

---

## 7. Phase Plan

The full phase plan (every package, evidence note and decision) is archived
verbatim in [`docs/plan-archive/`](plan-archive/README.md). References like
"plan §7 Phase 3" in code and docs resolve through this table. Open boxes of
the archived phases are listed in section 12 and ticked there.

| Phase (heading as recorded) | Open boxes | Archive file |
| --- | --- | --- |
| Phase 0 — Feasibility PoC (CRITICAL, before anything else) | 0 | [phase-0-3.md](plan-archive/phase-0-3.md) |
| Phase 1 — vault-core + vault-api (server MVP) | 0 | [phase-0-3.md](plan-archive/phase-0-3.md) |
| Phase 2 — vault-agent (PC listener) — COMPLETE 2026-08-09 | 0 | [phase-0-3.md](plan-archive/phase-0-3.md) |
| Phase 3 — Scheduler & Update Logic — COMPLETE 2026-08-09 | 0 | [phase-0-3.md](plan-archive/phase-0-3.md) |
| Phase 4a — Web UI (served by vault-api as static files) — COMPLETE 2026-08-11 | 0 | [phase-4a-4b.md](plan-archive/phase-4a-4b.md) |
| Phase 4b — Android App — 4b.1–4b.10 MERGED 2026-08-17 | 1 | [phase-4a-4b.md](plan-archive/phase-4a-4b.md) |
| Phase 4c — Manual update check (both frontends, user decision 2026-08-10) | 0 | [phase-4c-4d.md](plan-archive/phase-4c-4d.md) |
| Phase 4d — Persisted settings + "keep the cache current" sweep mode | 0 | [phase-4c-4d.md](plan-archive/phase-4c-4d.md) |
| Phase 4e — Web UI desktop layout (track: web) | 0 | [phase-4e.md](plan-archive/phase-4e.md) |
| Phase 4h — Decision support in the reclaimed desktop width (user decision 2026-08-18) | 0 | [phase-4h.md](plan-archive/phase-4h.md) |
| Phase 5 — Community Release | 1 | [phase-5.md](plan-archive/phase-5.md) |
| Phase 6 — External Integrations (post-release, user decision 2026-08-10) | 9 | [phase-6-s-track.md](plan-archive/phase-6-s-track.md) |
| Runner split & egress enforcement (S-track, decided 2026-08-19) | 0 | [phase-6-s-track.md](plan-archive/phase-6-s-track.md) |

---

## 8. Repository Structure (Monorepo)

```
steamhangar/
├── core/            # nginx config, Dockerfile (vault-core)
├── dns/             # optional dnsmasq container (Compose profile, vault-dns)
├── api/             # FastAPI, SQLite schema, scheduler, runner entrypoint (vault-api / vault-runner)
├── agent/           # PC listener, Go (Windows, Linux, SteamOS) + packaging
├── web/             # browser UI, plain HTML/CSS/JS, served by vault-api (no build step)
├── app/             # Android app (Kotlin + Jetpack Compose; tsnet module is post-v1)
├── deploy/          # compose.yaml, example .env, proxy image (vault-proxy), examples/, verify-stack
├── docs/            # architecture, ADRs, security docs (threat model), setup guides
├── poc/             # Phase-0 evidence, frozen — plus later measurement spikes (poc/throttle/)
└── .github/         # CI + publish workflows, issue/PR templates
```

---

## 9. Risks & Open Questions

| Risk | Severity | Mitigation |
|---|---|---|
| `proxy_store` incompatible with range requests | HIGH | Phase 0 resolves this; Plan A fallback defined |
| Steam changes its CDN URL scheme | MEDIUM | Log schema anomalies with alerts; abstract the mapping layer |
| Public-domain profile exposes the API to the internet | MEDIUM | TLS mandatory, strong bearer token, docs strongly recommend forward-auth/OIDC + rate limiting in the reverse proxy; API has no unauthenticated endpoints except `/v1/health`, the web UI's static files and the static Steam sign-in hand-off page `/auth/steam/app-return` (ADR-0004 addendum 5) and the baked release downloads `GET`/`HEAD /dist/<name>` (WP DIST-1a; exposes the exact version, accepted trade-off against the §8 "no version on public health" decision, user decision APP-DIST-1/AGENT-DIST-1 2026-10-14, threat model §1); stored covers are fetched by a key-derived signed URL (WP D10) (threat model §1/§7) |
| Shared depots → incomplete deletion | LOW | Shared detection + transparent reporting |
| tsnet gomobile build complexity | MEDIUM | Profile abstraction means tsnet can ship later; system-VPN profile works day one |
| Single-maintainer risk | MEDIUM | Small scope (Steam only), good docs, permissive license lower the contribution barrier |
| SteamPrefill upstream dependency | LOW | Used only as a CLI subprocess, replaceable |

**Deliberately OUT of scope (v1):** multi-service (Epic etc.), multi-tenant
setups. Multi-launcher caching (Ubisoft, Battle.net, Epic) is a roadmap
item for v0.2 or later (`docs/handover/post-0.1.0-roadmap.md` D17,
user decision 2026-10-15). *(The web UI moved INTO scope as Phase 4a by user decision on
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
  3. **DNS-free hosts mode** (single gaming PC, Windows or Linux/SteamOS;
     simplest setup): a `lancache.steamcontent.com` hosts entry — manually
     or automated by vault-agent (opt-in). The current Steam client on both
     platforms performs the lookup (WP 0.6); multi-device LANs belong on
     mode 1 or 2.
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
Phases 1–3 plus 4a shipped (state as of the 2026-08-17 rewrite; the items
below carry their own later dates, item 12 is the current one).

Items 1-12 are archived verbatim in
[`plan-archive/next-steps-items-1-12.md`](plan-archive/next-steps-items-1-12.md);
their open boxes (items 2, 7 and 11) are in section 12. Item 13 stays here.

13. [ ] **Road to v0.1.0 final and the post-v0.1.0 roadmap (user
    decisions 2026-10-02).** The production rollout on the operator's
    host (item 2) is the release gate. What it found, what is still open
    before `v0.1.0`, and what was deliberately moved past it.

    Blocks A, B, B2 and C, block D (D1-D29), the rc15-rc18 record and the
    rc19 to rc21.1 blocks are archived verbatim in
    [`docs/plan-archive/`](plan-archive/README.md). Every box of them that is
    still open is in section 12 ("Carried-over open items"), grouped by
    origin; "the box above" or "B above" in the blocks below now means that
    copy in section 12, which is where it is ticked.

    **Release plan from rc20 on (user decisions 2026-10-16).** The blocks
    below are grouped by version; theme, goal, entry and exit criterion
    per version are in `docs/handover/post-0.1.0-roadmap.md`, section
    "Release plan (user decisions 2026-10-16)". Packages of rc21, rc22 and
    later minors are cut in each version's planning session. Version
    rules from 0.2.0 on (minor = themed set fixed upfront, rc = feature
    freeze, patch = fixes only): ADR-0029 (user decisions 2026-10-18).
    - rc20 web redesign: LAYOUT-1, SHEET-MOTION-1, CLIENTS-1..3,
      ADD-DEVICE-1, DOWNLOADS-1 (with VERIFY-SPEED-TEXT-1, QUEUE-KBD-KEEP),
      SETTINGS-1, LIBRARY-1, LIBRARY-DETAIL-1, LIVE-DELAY-1 (before
      DOWNLOADS-1), BOOT-RATE-1 (D24), LIB-META-WINDOW-1; optional rc20.x
      tuning defaults (ADR-0027)
    - rc21 Android parity and cache health: Android rc20 follow-up,
      ANDROID-GAPS-1, APP-NIGHT-GOLDEN-1, CACHE-REPAIR-1, unmapped-depot
      honesty flag, "Unknown app" package names, optional "Client
      downloading" row, VERSION-HEALTH-1, tuning defaults, runner retry
      (conditional)
    - rc22 release hardening: D7 `:443` + TLS default, CAP-503-LOOP-1,
      HIT-PATH-1, NITS-AUDIT-1, remaining nits, the open B boxes; then
      `v0.1.0` final; v0.1.x bug fixes only, no public announcement yet
    - v0.2.0 rename: D18 incl. logo, LAUNCHER-PREP-1
    - v0.3.0 privacy: D22 then LIBRARY-META-1d
    - v0.4.0 agents and devices: Weg B for agents with the installer (D6
      part), D28, D5, D29
    - v0.5.0 Steam accounts: D2, D3 (after a new package cut)
    - v0.6.0 schedules: D8; then the public announcement (WP 5.6)
    - v0.7.0+: D17, D4 / DUAL-STACK-1 follow-ups, D25, D26, D27, Phase 6,
      iOS app
    - operator items: DUAL-STACK-1, Sunday cold-game test

    The former "rc20 candidates" block (2026-10-16) is split into these
    version blocks; nothing was dropped. D22 moved to v0.2.0 (v0.3.0 since
    ADR-0029); the Clients /
    Add-a-device redesign is CLIENTS-1..3 and ADD-DEVICE-1 in rc20;
    VERIFY-SPEED-TEXT-1 is decided inside DOWNLOADS-1; D20 shipped in rc19
    (its nits are in rc22); DUAL-STACK-1 is an operator item.

    - [ ] **rc22 — release hardening, no new features** (packages cut
      2026-11-01 in the planning session, user yes the same day; each
      package runs through the Workflow tool, max 3 agents at a time).
      Order: wave 0 DOC-DIET-1 alone; wave 1 DEV-MODS-1a, DEV-MODS-1b,
      ANDROID-VERIFY-SPEED-1; wave 2 NITS-WEB-1, NITS-APP-1, NITS-API-1, ADD-DEVICE-QR-1;
      wave 3 SAMPLE-IP-1, CI-SIGN-PIN-1, VERIFY-FAST-1; wave 4
      RELEASE-SPLIT-1a/b/c ([Fable] second pass), RELEASE-NOTES-1.
      Measurements (orchestrator, read-only `dev.sh diag`, every rate step
      only after the operator's yes): D7 `:443`, CAP-503-LOOP-1,
      HIT-PATH-1, failed-jobs measurement. Operator: the B boxes below.
      - [ ] D7 `:443` measurement and the `VAULT_TLS_CLIENT_MAX_CONNS`
        default (closes the D7 box above)
      - [x] **ANDROID-VERIFY-SPEED-1** (android; found 2026-11-01) the web's
        decided Verifying speed line "Verifying files, no download speed"
        (VERIFY_SPEED_TEXT, DOWNLOADS-1, operator 2026-10-16) was never ported
        to Android; port it with a twin pin.
      - [ ] **NITS-WEB-1** (web) from NITS-AUDIT-1 and the remaining nits:
        client-side request timeout in `api.js`, group-sheet "Update now"
        test, origin check in `isVaultReturnTo`, aria-live on queue move,
        CARD-CORNERS-1 candidate fix. Done in NITS-WEB-1: request timeout
        (`web/js/api.js`, `web/tests/api-timeout.test.js`), group "Update now"
        (`web/tests/game-detail-sheet-update-group.test.js`), aria-live on
        queue move (`views/downloads.js`, `downloads-queue-reorder.test.js`),
        CARD-CORNERS-1 candidate CSS. Not web: `isVaultReturnTo` lives only in
        the Android app (`SteamOpenIdLoginUrl.kt`), moved to NITS-APP-1.
      - [ ] **NITS-APP-1** (android) dead `packagesAriaText`, trivial last
        assertion in `DownloadsStatsLiveTest`, D19WiringTest pin, open
        Android review nits of rc20-rc21.1.
      - [ ] **NITS-API-1** (api + agent) MAP-DRIFT-1 fail-safe tests,
        claim-vs-move race test, regrow-without-idle-gap test, open api
        review nits of rc20-rc21.1.
      - [x] **ADD-DEVICE-QR-1** (web; operator report 2026-11-01, wave 2)
        Add a device shows the app-download QR (step 1) and, once revealed,
        the pairing QR (step 2) close together, so the camera can catch the
        wrong one. Only one code is ever sharp: the download QR gets a small
        eye toggle that blurs it, and revealing the pairing QR blurs the
        download QR automatically (and the reverse). Applies to the wide and
        the narrow layout; `add-device-sheet.js`, `kit.css`, web tests and
        a `test-web-shots` golden. No Android twin (ANDROID-ADD-DEVICE-1).
      - [x] **NITS-API-2** (api + web/android twins; user decisions
        2026-11-01 on NITS-API-1 skips) NOT-OWNED-UPDATE-1 Weg A: no
        `update_available` for cached, not-owned apps; LOADING-COUNT-1 Weg A:
        the "now loading" device count counts agents like its rows;
        LIVE-DIVISOR-1 Weg A: the live rate of a partly covered bucket
        divides by the covered seconds. RETRY-REANNOUNCE-1 stays (Weg B).
      - [ ] **SAMPLE-IP-1** (tests/examples; user decision 2026-11-01,
        Weg A) sweep the generic `192.168.1.x` examples to `10.0.0.x`.
      - [x] **CI-SIGN-PIN-1** (ci; CI-FIX-3 follow-up) pin the APK signing
        certificate fingerprint in CI. `publish.yml`'s verify step compares
        the release APK's certificate SHA-256 with `app/signing-cert.sha256`
        (pinned to the rc10 value, still on rc21.1) via
        `apk-cert-digest.sh pin`; mismatch fails, unset pin fails tag runs
        and warns elsewhere. Tests `api/tests/test_ci_sign_pin_1.py`;
        operator steps in app/README.md "Pinned release certificate".
      - [ ] **RELEASE-NOTES-1** (docs) v0.1.0 release notes (B box).
      - [x] **DOC-DIET-1** (docs + agents, user decision 2026-11-01,
        Weg A; first rc22 package) every coder/reviewer start reads
        `PROJECT_PLAN.md` (292 KB) and `LEARNINGS.md` (87 KB), roughly
        100k tokens per agent. Split the plan into a core (sections 1-6,
        8-11, current version block) and an archive of finished phases;
        give `LEARNINGS.md` an index by area; `coder.md`/`reviewer.md`
        read the core plus the area section only. Docs-only, one short
        review.
      - [ ] **DEV-FLOW-1** (process, user decision 2026-11-01) every rc22
        package runs its coder -> reviewer -> fix loop through the Workflow
        tool (max 3 agents at a time); a private dashboard with a
        database (packages, CI, open decisions, Antraege, agents) replaces
        status messages. rc21.1 keeps the manual flow. Form (user decision
        2026-11-01, no artifact tool in the session): a local SQLite file
        on the dev host (outside the repo) rendered to one HTML page that
        is sent to the operator; agent numbers are synced from the token
        meter at each package end (Weg 1).
      - [ ] **DEV-MODS-1** (dev tooling, user decision 2026-11-01, Weg A)
        Claude Code mods under `tools/claude-mods/` (generic, no host
        paths; host paths come in as plugin options): token meter per
        request and agent (`turn.step`, writes a file), output trimming for
        long test/verify/CI output (`tool.call` after `next`, full output
        kept as a file), read brake for whole-file reads of
        `WORKPACKAGES.md`, `docs/plan-archive/*`, `PROJECT_PLAN.md` above
        100 KB and any file above 150 KB (Read and plain `cat`), and
        zero-token commands (`/ci`, `/antraege`, `/wp`). Each mod with
        `claude plugin test` tests and a clean `claude plugin validate`;
        no `$.process` call outside a fixed command list. The operator
        loads them as managed mods (`allowManagedModsOnly`). Entry:
        Claude Code >= 2.1.287 in the devbox (Antrag). First use: token
        meter numbers before and after DOC-DIET-1.
      - [ ] CAP-503-LOOP-1 measure whether the 503 retry loop still occurs
        at the current rate
      - [ ] HIT-PATH-1 measure server-to-client HIT throughput (iperf3,
        nginx counters, hit-path read)
      - [ ] NITS-AUDIT-1 small items (list in the roadmap; the GameHangar
        logo briefing goes to v0.2.0)
      - [ ] **RELEASE-SPLIT-1** (ci + api + web/android; user decision
        2026-10-18, revised the same day) one product version (ADR-0029);
        each component (server, agent, app) carries the product version in
        which it last changed and is only rebuilt when it changed; the
        vault-api image bakes the recorded agent/APK artifacts; the update
        hint compares against the bundled agent version (minimum version,
        alias list for the identical rc18-rc20.2 agent builds); About and
        release notes show each component's version. New ADR. Details in
        the roadmap.
      - [ ] **VERIFY-FAST-1** (deploy/tests + devbox, decided 2026-10-18,
        Weg A) `verify-stack.sh` gets section selection (`VERIFY_SECTIONS`);
        `dev.sh verify` defaults to a lean set (1 config drift, 3 compose
        rendering, 8 fail-fast guards, one stack start with 5a-5d and
        6a-6e, no CDN steps), `verify full` runs everything and stays the
        gate for release tags and the nightly CI. Per-section timestamps
        in the output; first measurement when the dev instance is free
        (`dev.sh verify` stops it). Note: `dev.sh` changes go via Antrag.
      - [ ] **CARD-CORNERS-1** (web, open, needs the operator's screenshot)
        Library game card reportedly lacks rounded top corners with real
        covers; demo render (`dev.sh test-web-shots`) is fine. Candidate
        fix: `border-radius: inherit` on `img.cover` plus `isolation:isolate`
        on `.cap` (`web/css/kit.css:817`). Candidate applied in NITS-WEB-1
        (`web/css/kit.css`, `.kit .card .cap` / `.kit .cap img.cover`); the
        box stays open until the operator's screenshot with real covers
        confirms it.
      - [ ] nit (DOC-DIET-1 review): the "Open boxes" column of the
        section 7 table is hard-coded and goes stale when section 12 items
        are ticked (drop it or derive it in the line-check test); label
        references "item 13 B/C/D1" in post-0.1.0-roadmap.md, ADR-0017 and
        web/tests/README.md could point to the archive files directly;
        tools/README.md should name where the line-check test lives;
        WORKPACKAGES.md:1120 cites an old plan line range. Core plan is
        85.8 KB, not 70: section 12 keeps every open item's full text and
        shrinks as rc22 ticks them (orchestrator choice, Weg B).
      - [x] nit (ANDROID-VERIFY-SPEED-1 review): `FeedState` import out of
        order in `DownloadsStats.kt`; `VerifySpeedTest.poll(null)` uses
        `throughput!!` and needs a comment that refreshOnce is never called.
        (fixed in NITS-APP-1: `DownloadsStats.kt` imports sorted, KDoc on
        `VerifySpeedTest.poll`)
      - [ ] DEV-MODS-1 live check once the operator loads the mods as
        managed mods (Antrag after 1a): core accepts output-trim's rewritten
        Bash result (`{ result, context }` and the `isError` branch); which
        directory `CLAUDE_PLUGIN_DATA` really resolves to.
        Also confirm the managed-mods install snippet in
        `tools/claude-mods/README.md` (extraKnownMarketplaces + enabledPlugins
        with allowManagedModsOnly) before it counts as documented.
      - [ ] nit (DEV-MODS-1a review): token-meter rewrites the whole part file
        per request (no `$.fs` append; consider partKb 1024); part size is
        counted in UTF-16 units, not bytes; with HOME and CLAUDE_CONFIG_DIR
        unset the log dir becomes a relative path (skip instead); the
        `$.clock.now()` calls around `next()` sit outside the try/catch;
        tools/README.md has no pointer to tools/claude-mods/ yet.
      - [ ] nit (DEV-MODS-1b review): output-trim names kept files with
        `Date.now()` (not `$.clock.now()`); read-back and SMALL_RANGE checks
        match loosely (fail open); kept files are never pruned (max-age or
        max-count prune later); no context assertion on the error branch.
      - [ ] nit (ADD-DEVICE-QR-1 review): no wide golden of the Add-a-device
        sheet (add e.g. clients-pair-1400 with the pairing QR shown, so the
        blurred download QR and its eye are pinned); the eye carries both
        aria-pressed and a flipping label (APG: keep one); comment that
        `appQr.eye` is moved between paints, not cloned. Chosen behaviour:
        hiding the pairing QR leaves the download QR blurred until the eye
        or a reopen. Local `test-web-shots` fails clients-pair-shown-400 on
        main too (1508 px, QR payload differs locally; see AUTO-VERIFY-1).
      - [ ] nit (NITS-APP-1 review): NightGoldenTwinTest lives in src/test but
        reads src/testDebug goldens (also runs in the release task; could
        move to testDebug); a third local copy of the assertCalledFrom
        helpers (shared test helper later); `tools/doc-lines-check.py`
        compares against the frozen pre-DIET ref, so every later tick shows
        as a "lost" line (it was a one-off proof; document or retire it).
      - [ ] nit (CI-SIGN-PIN-1 review): publish summary's else-branch treats
        any non-`match` state as "pin not set" (check `= unset` explicitly);
        the step's own error line duplicates the script's `::error::`;
        actionlint not run on the devbox, the first rc22 tag run is the
        real proof of the pin step.
      - [ ] nit (NITS-WEB-1 review): api.js `untimed` wrapper exists only to
        keep CacheRepairWebTwinTest's byte pin (pin route + NO_TIMEOUT intent
        in the twin test and drop the indirection); split LIBRARY-DETAIL-1
        and FIX-POP-HIDDEN-1 into one box per part when picked up. Checked:
        steam_relay/oracle server timeout 10 s < 20 s client deadline.
      - [ ] nit (NITS-API-2 follow-ups): the swept `active_clients_now` (15 min
        sweep) and the in-range `summary.active_clients` still count
        addresses; the header "Current speed" window (`LiveTier.now_view`)
        still divides by its full 15-20 s right after a mid-file tailer
        start; `LiveTier.active_clients()` stays address-based (tests only);
        a mid-file start without a complete line uses the read moment as
        `_data_from` (one bucket can read slightly high).
      - [ ] remaining nits: MAP-DRIFT-1 fail-safe tests, claim-vs-move race
        test, aria-live on queue move (fixed in NITS-WEB-1:
        `downloads-queue-reorder.test.js`), D19WiringTest pin,
        regrow-without-idle-gap test
        (D19WiringTest pin fixed in NITS-APP-1: the loaded-body pins use the enclosing-composable generation and a
        bounded `LoadedDetailBody` body instead of a contains-scan to the end
        of the file)
        (NITS-API-1 added the three api tests:
        `test_map_drift_1.py` unreadable-dir and unusable-id cases,
        `test_d20_queue_order.py` claim-vs-move race and loser path,
        `test_graph_live.py` regrow without an idle gap, which found and
        fixed a real resume-at-old-offset loss in `live_tier.py`; aria-live
        and D19WiringTest stay with NITS-WEB-1/NITS-APP-1)
      - the open boxes of **B** above (signed APK, standard-user check,
        real-device checks, rollout gates (h) and (i), release notes) and
        the failed-jobs measurement (Subnautica, Timberborn, Hogwarts
        Legacy; roadmap "Before v0.1.0") are part of rc22; their boxes stay
        in section 12 (origin item 13 B) and are ticked there, not repeated here
      - [ ] close: release gate, every open B box ticked or moved by the
        user, measurements written up; then tag **`v0.1.0` final**
    - **v0.1.x:** bug fixes only. The public announcement (WP 5.6,
      r/selfhosted) is NOT made at 0.1.0; it waits for 0.6.0 (ADR-0029).

    - [ ] **v0.2.0 — rename to GameHangar, launcher-neutral identity** (re-cut 2026-10-18,
      ADR-0029; packages cut in the version's planning session; entry:
      `v0.1.0` final and the D18 org decision)
      - [ ] **D18 RENAME-2** SteamHangar -> GameHangar (user decision
        2026-10-16: before the announcement; GitHub org choice A/B/C
        still open), incl. the GameHangar logo and header briefing
      - [ ] **LAUNCHER-PREP-1** (user decision 2026-10-18, Weg A):
        per-launcher measurements (D17 checklist), ADR for a
        launcher-neutral game identity (launcher plus id), cache
        namespace and single front door; then API, schema (`launcher`,
        default `steam`) and both apps on that identity, so D17 later
        ships as a 1.x minor without a contract break (ADR-0029)
      - [ ] close: release gate, migration from 0.1.x tested (volumes,
        `applicationId`, image namespace)

    - [ ] **v0.3.0 — privacy**
      - [ ] **D22** privacy mode (ADR-0023 draft), then
        **LIBRARY-META-1d** store night fill on top of it
      - [ ] close: release gate

    - [ ] **v0.4.0 — agents and devices** (entry: D6 questions decided)
      - [ ] **Weg B for agents** (decided 2026-10-16, with the
        installer): the agent reports its OS, the API stores it, and the
        Clients page opens the matching tab; the API serves a minimum agent
        version that replaces the Weg A "differs from server" check. Needs a
        schema bump and an agent release; already-running agents report no
        OS until updated. Ships with the real installer (the installer part
        of D6, box above).
      - [ ] **AGENT-PLATFORMS-1** (user wish 2026-10-18; roadmap D6)
        installer and background agent beyond Windows: Linux desktop
        (.deb/Flatpak/AUR, systemd user service), macOS (darwin builds,
        .pkg/Homebrew, launchd, notarization cost decision), SteamOS on
        Steam Deck (per-user install that survives OS updates, SD-card
        libraries) and Steam Frame (arm64, check user services first).
        Platform scope for 0.4.0 is an open decision at its cut.
      - [ ] **CLEANUP-SUGGEST-1** (user wish 2026-10-18; roadmap 0.4.0)
        suggest deleting cached games no reporting client has installed
        any more (grace period, offline clients don't count), with the
        space it frees; never automatic.
      - [ ] **CACHE-MAX-1** (user decision 2026-10-18, Weg B; roadmap)
        optional maximum cache size: above it no new games and no storing
        of misses for uncached depots (pass-through), updates of cached
        games keep running and storing, UI warning with cleanup
        suggestions, never deletes on its own.
      - [ ] **CLIENT-NAME-MAC-1** (roadmap D28, user wish 2026-10-14;
        pairs with the installer and the Weg B agent work): the agent
        reports its MAC, vault-api accepts `key_type = "mac"` (reserved
        today, answered with 422), so client names survive DHCP changes.
      - D5 named, scoped API keys and D29 PAIR-2 (boxes above; D29 builds
        on D5)
      - [ ] close: release gate, install and pairing on a real Windows PC
        with a standard user

    - [ ] **v0.5.0 — Steam accounts**: D2 QR login
      and D3 several accounts (boxes above). Entry: the package cut is
      redone against the current schema and the 11 open user questions
      (D2 1-5, D3 1-6) are settled; the ADR drafts 0018/0019 live on
      unmerged branches. Exit: release gate, plus a real QR login and a
      prefill per account on the operator's stack.

    - [ ] **v0.6.0 — schedules, then the announcement**: D8 schedules
      incl. rate per window (box D8 above). Exit: release gate; then the
      public announcement (WP 5.6).

    - [ ] **v0.7.0 and later — beyond Steam and integrations** (order not
      set): D17 multi-launcher (needs D18 and LAUNCHER-PREP-1; a 1.x
      minor after `1.0.0`, ADR-0029), D4 IPv6 and
      the DUAL-STACK-1 follow-ups, Phase 6 external integrations, the iOS
      app, and the old post-v1 backlog moved from `docs/WORKPACKAGES.md`
      on 2026-10-16: D25 TSNET-1 embedded tailscale (requirement A6), D26
      NOTIFY-CURSOR-1 server-side notification cursor, D27 DEPOT-GRACE-1
      per-depot grace windows.

    - [ ] **Operator items (independent of versions)**
      - [ ] DUAL-STACK-1 (after the line change) AAAA check, then stepwise
        cap raise with measurement
      - Sunday cold-game test per ADR-0027 (box in the rc19.3 block)

    **Parallel work on the post-v0.1.0 items:** design and ADR drafts can
    start on their own branches now. D1 was pulled ahead of `v0.1.0` by
    user decision on 2026-10-02 (ADR-0016 addendum, ships in
    `v0.1.0-rc7` with CORE-FIX-3 per the 2026-10-03 decision); code for D2 and D3 still waits for `v0.1.0` (the freeze
    holds for them until then, and every merge to `main` before the final
    tag would ride into the release).

---

## 12. Carried-over open items

Every box that was still open when its phase or version block moved to
[`docs/plan-archive/`](plan-archive/README.md) (DOC-DIET-1, rc22). The text is
unchanged apart from the origin in parentheses; ticked sub-items are left out
(they stay in the archive). The archive keeps the original line; when an item
is done, tick it HERE, not in the archive.

### From Phase 4b ([archive](plan-archive/phase-4a-4b.md))

- [ ] (origin: Phase 4b) tsnet Go module + gomobile build (`.aar`), auth-key handling
      *(deliberately POST-V1: the profile abstraction from WP 4b.2 makes
      this additive, and the System-VPN profile covers Tailscale via the
      regular Tailscale app on day one)*

### From Phase 5 ([archive](plan-archive/phase-5.md))

- [ ] (origin: Phase 5) Announcement: r/selfhosted, r/homelab, LanCache Discord (stay fair:
      frame as a complement/alternative, not a "LanCache killer").
      Waits for v0.6.0 (user decision 2026-10-16: after the D18 rename;
      2026-10-18, ADR-0029: once schedules are in).

### From Phase 6 ([archive](plan-archive/phase-6-s-track.md))

- [ ] (origin: Phase 6) Multi-target delivery: `VAULT_WEBHOOK_TARGETS` — N receivers, each
      with its own URL, event filter, format and headers, so n8n and Discord
      can be fed at the same time (today: exactly one URL)
- [ ] (origin: Phase 6) Vendor format adapters `generic|discord|slack` (user decision
      2026-08-10: adapters in the backend, NOT a free-form body template —
      Discord must work without a relay; the cost is that we maintain two
      foreign formats). Includes honouring `Retry-After` on 429, which
      Discord does send and the current 3-attempt/0.2-0.5 s backoff ignores
- [ ] (origin: Phase 6) `app.updated` — the update notification. **No oracle dependency**
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
- [ ] (origin: Phase 6) Outbound auth beyond Basic-in-URL: custom headers (n8n commonly wants
      a header token) + optional HMAC-SHA256 signature, plus a per-event
      `event_id` so a receiver can deduplicate across retries
- [ ] (origin: Phase 6) `POST /v1/webhooks/test` — fire a synthetic event at a target. Needed
      by any settings UI (Phase 4) and turns webhook setup from guesswork
      into one click
- [ ] (origin: Phase 6) Integration docs: n8n in BOTH directions (receiving events; calling
      `/v1/jobs` to trigger a prefill), Discord, ntfy
- [ ] (origin: Phase 6) Structured log export — opt-in `VAULT_EVENT_LOG_STDOUT` mirroring the
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
- [ ] (origin: Phase 6) Named, scoped API keys — the direct consequence of inviting external
      systems in (user question 2026-08-10). Today there is exactly ONE
      key: `auth.require_api_key` compares against `VAULT_API_KEY` for every
      router, so the key an n8n flow needs in order to enqueue a prefill is
      the same key that may `DELETE /v1/cache/{appid}`. Wanted: several
      named keys (web UI, agent, Android app, n8n) with a coarse scope —
      read / enqueue / destructive — each revocable on its own, so a leaked
      automation token does not mean rotating every agent in the house.
      Note this is about BLAST RADIUS, not about identity: it does not make
      the vault multi-user
- [ ] (origin: Phase 6) Payload scoping per target: a Discord channel is a room full of
      people, and `client.bypass_*` payloads carry `client_id` plus every
      IP address a device reported from, while job/update events reveal
      which games the household owns. A `full|minimal` payload mode per
      target (minimal drops device and address fields) is the cheap,
      correct fix — authentication cannot solve a receiver-side visibility
      problem

### From §11 items 1-12 ([archive](plan-archive/next-steps-items-1-12.md))

2. [ ] (origin: §11 items 1-12) **First real-world rollout** — joint interactive session with the
   operator (see the Deployment section of `docs/WORKPACKAGES.md`). The two
   operator-side network blockers are cleared (measured by the operator,
   2026-10-01): the VPN subnet router advertises only the current networks,
   and LAN clients get no IPv6 DNS server. One check stays for the rollout
   itself: with the DNS rewrite active, the AAAA answer for the cache name
   must not be a public address. Precondition decided 2026-10-01: the
   egress subnet becomes configurable (WP DEPLOY-FIX-2) so a production
   stack can run next to a test stack; the rollout started with tag
   v0.1.0-rc3. Gate (b) found that a bind-mounted cache
   (VAULT_CACHE_PATH) fails on Docker 28 because of vault-api's
   `:nocopy` (fixed in WP DEPLOY-FIX-3, verify now runs bind mode too);
   production moves to v0.1.0-rc5, which carries the fix. Findings from
   the operator's first phone test on rc5 (2026-10-02), fixed in WP
   WEB-FIX-3 (phone layout hardening), WEB-FIX-4 (job titles for
   owned-only games; plain-language hints plus Retry for a missing Steam
   login and for a cache name resolving to a public IP) and WEB-FIX-5
   (dismissible hint when the browser's "Desktop site" mode renders the
   UI at desktop width - the root cause of the zoomed-out phone view).
   Prefill on the production stack needs vault-core on its own address
   and `extra_hosts` for the cache name on vault-runner. This session is also
   where the honest still-open lists in `web/tests/README.md` and
   `app/README.md` get verified — real screen reader, phone browser cover
   art, GC against real on-disk chunks, real multi-client bypass detection.
7. [ ] (origin: §11 items 1-12) **User-gated:** WP 5.5 — the machinery is no longer the gate. The
   GitHub org exists, the repo lives at `steamhangar/steamhangar`, and the
   full publish path is built and reviewed (WP CI-3 + WP AGENT-BIN, see §7
   Phase 5's CI bullet): images, signed APK, agent binaries, checksums.
   What remains are user actions only (steps (a)-(d) at the end of this
   item), and review's standing advice is a throwaway pre-release first, named
   `v0.1.0-rc1`: three things in the tree bake `0.1.0` —
   `vault_api.__version__` (served as `server_version` by the
   authenticated `GET /v1/settings`; `/v1/health` returns only
   `{"status":"ok"}`), the compose `VAULT_IMAGE_TAG` default, and the
   `org.opencontainers.image.version` LABEL in the api, core, dns and
   deploy/proxy Dockerfiles. (Superseded 2026-10-03 by WP VER-1: the
   Dockerfile labels and `server_version` now come from the build args
   publish.yml derives from the tag, `dev` for local builds; only
   `vault_api.BASE_VERSION`, the fallback outside an image, and the
   compose `VAULT_IMAGE_TAG` default still say `0.1.0`.) What the tag sets: since DEPLOY-FIX-1/APP-FIX-1 the APK's
   versionName is the tag without its `v` (build.gradle.kts falls back to
   `0.1.0` for local builds only), and the published images are tagged
   (and labelled, via docker/metadata-action) with the same string. With
   `v0.1.0-rc1` the APK and images say `0.1.0-rc1`, a semver pre-release
   of the baked `0.1.0` base; a `v0.0.1-rc1` would ship images and an APK
   whose version is older than the `server_version` inside them. The path has never executed and the
   release page is the only place its end-to-end result can be seen
   (re-pushing the SAME tag double-appends release notes; known, commented
   at the site). WP 5.6
   (announcement) stays gated on the user's own end-to-end test with the
   Android app. Phase 6 integrations are deliberately post-release.
   **Remaining release steps (2026-10-01, all user actions):**
   (a) merge PR `wp/pre-freeze-review` once its CI is green — the first
   android-tests run there is the first compile of the APP changes;
   (b) one-time GitHub settings BEFORE setting the signing secrets: an
   Environment `release` with a `v*`-tag deployment rule, the four
   `ANDROID_*` secrets placed in it (not as repo secrets), and a `v*` tag
   ruleset (see app/README "CI signing secrets"); (c) push the tag
   `v0.1.0-rc1` from main; (d) check the release page's "Verify this
   release" block. After the first push, the GHCR packages must be made
   public.
11. [ ] (origin: §11 items 1-12) **Macvlan deploy example — vault-core on its own LAN IP** (operator
    request 2026-08-30, roadmap entry — not yet a scoped work package).
    Steam clients require plain HTTP on port 80 (Steam's choice), so on
    a host whose port 80 is already owned by a reverse proxy (Traefik,
    NPM — the homelab default) vault-core needs a second address. Today
    that means a host-side IP alias created in the host platform's own
    UI (so it survives reboots). The comfortable
    shipped answer: a documented compose override giving vault-core its
    own LAN IP via a macvlan network — Docker-native, no host network
    clicks, the established lancache pattern. Must ship as an EXAMPLE
    with its caveats stated, not as a silent default: the host cannot
    reach a macvlan container directly (affects the host-side health
    probes and any same-host DNS rewrite target), the parent interface
    and address range are operator-specific, and the interaction with
    the host platform's container manager needs a real test on the
    deployment this was requested for. Belongs in deploy/examples/ beside
    minimal-lan and tuned-setup, with a verify recipe; footprint deploy/ +
    docs only.

### From item 13 B ([archive](plan-archive/v0.1.0-road-a-c.md))

- [ ] (origin: item 13 B) Signed APK: operator creates and backs up the keystore, puts
  the four `ANDROID_*` secrets into the `release` Environment, then
  dispatches the publish workflow on the release tag (app/README).
- [ ] (origin: item 13 B) Standard-user check: `install-task.ps1` (AGENT-FEAT-1, with the
  AtLogOn trigger) registers the task on a non-admin Windows account
  (CI's harness runs as admin).
- [ ] (origin: item 13 B) Real-device checks from the honest open lists
  (`web/tests/README.md`, `app/README.md`): screen reader, phone
  cover art, GC against real chunks, multi-client bypass detection,
  bottom-nav behaviour with Desktop site off.
- [ ] (origin: item 13 B) Rollout gates (h) and (i) by the operator: re-measure with the
  DNS rewrite, scheduler on, stack in the operator's stack repo,
  SQLite dump in the backup job.
- [ ] (origin: item 13 B) Release notes for `v0.1.0`: the DS-Lite/CGNAT behaviour, the
  `extra_hosts` requirement with a dedicated `VAULT_CORE_BIND`, the
  bind-mount precondition (`cache/depot` + `tmp`; since SEC-FIX-5 only
  `cache/depot` is 101:101, the rest root:root, migrated at start; a
  rollback to an older image needs the old ownership back).
  Also, for the release candidate after rc12 (D7 CORE-FIX-4,
  ADR-0021; user decision 2026-10-04: fixed before `v0.1.0`): a Steam
  client downloading an app that is NOT in the cache used to open one
  upstream connection per chunk, so behind DS-Lite/CGNAT a MISS burst
  could exhaust the subscriber's port quota (first client test,
  2026-10-04: 1838x `113: Host is unreachable` across 14 edge hosts in
  ~16 s). Now: new env vars `VAULT_UPSTREAM_EDGE` (compose default
  `dist-fra1.discovery.steamserver.net`, on out of the box; empty =
  legacy per-name pool = rollback, which keeps the old limitation),
  `VAULT_UPSTREAM_MAX_CONNS` (default 16, 1..64, no off switch) and the
  forwarded `VAULT_PREFILL_MAX_THREADS` (floor for the cap). Behaviour:
  all MISSes go through ONE pooled edge (the client's Host only has to
  pass the allowlist), a global cap answers 503 above C concurrent
  upstream connections, three new log fields (`upstream_host`, `host`,
  `limit_conn`). Keep `VAULT_UPSTREAM_RATE=4m` with an empty
  `VAULT_UPSTREAM_RATE_WINDOW` (user decision 2026-10-04) until the
  post-rollout measurements are stable. Post-rollout check (ADR-0021
  "After rollout", deploy/README.md): `113` count 0, connect-time
  dominated by `0.000`, established :80 sockets <= 2C, and the
  Host-for-all-names inference confirmed (uncached app update without
  hash mismatch, no edge-mode-only 4xx/5xx). Scheduler mitigation
  stays: prefill every installed app of an active PC, window e.g.
  `06:00-24:00`. Windows agent installer notes from
  AGENT-FIX-2: a re-install keeps the key, client id and (when the
  registry has none) a working library root from `env.txt`; going back to the hostname-derived id
  needs uninstall + install; the registry SteamPath wins over an
  earlier explicit `-LibraryRoot`. AGENT-FIX-3 (release candidate
  after rc12): the Scheduled Task no longer flashes a console window
  on every run; it launches `powershell.exe` through
  `conhost.exe --headless` (direct `powershell.exe` action as before
  when `conhost.exe` is missing), logging unchanged. Existing installs
  keep the old action until `install-task.ps1` is re-run once.
  WEB-FIX-10: the web UI's Windows install command trims spaces
  around the pasted key (as `install-task.ps1` does), still starts the
  first report when the download folder cannot be removed (warning
  only), and its steps say to press Enter once more if nothing happens.
  DEPS-1 (release candidate after rc12): the vault-api image ships
  SteamPrefill 3.7.2 instead of 3.7.1 (upstream fix: a DLC depot that
  links to itself without a manifest no longer breaks a prefill);
  no setting, flag or file format changes.
  WEB-FEAT-6: web and app show a running prefill of an already
  completed game as "Updating" (turning arrows) and a forced re-run of
  one as "Verifying", instead of "Downloading".
  CORE-FIX-4d (release candidate after rc12): the HTTPS passthrough's
  connection caps are tunable via `VAULT_TLS_CLIENT_MAX_CONNS` (per
  client address, default 64, 1..256) and `VAULT_TLS_MAX_CONNS` (total,
  default 256, 1..400, per-client <= total); defaults unchanged, an
  invalid value stops vault-core's boot. A Steam client over the
  per-client cap retries refused connections at once, which a gateway
  may log as a SYN flood; count the `status=503` passthrough lines
  before raising it.
  API-FIX-5 (release candidate after rc13): newer games get their real
  cover art instead of the fallback tile. vault-api looks covers up in
  the background through Steam's store API on `api.steampowered.com`
  (no key; the list of tracked app ids and the server's address go to
  Valve, always on), stores them (schema v18, new table) and serves
  `cover_url`; offline vaults keep the old covers and log one warning
  per failure streak. A rollback to an older image refuses the v18
  database (as for every schema bump).

### From item 13 D ([archive](plan-archive/v0.1.0-road-d.md))

- [ ] (origin: item 13 D) **D1 CORE-FEAT-1 — upstream keepalive to the Steam CDN.**
  nginx pools connections only for named `upstream` groups; a
  variable `proxy_pass` first looks the name up among the defined
  groups, then falls back to the resolver. Plan: one `upstream`
  block per known edge host (`server <host> resolve; keepalive N;`
  with a shared zone), rendered at container start from a list;
  unknown hosts keep today's path, so the Host header, the
  path-faithful store and the loop guard stay unchanged. Before
  building: measure on production with a handful of requests whether
  Valve edges keep connections alive. Benefits Steam clients too,
  which also download through vault-core.
  **Pulled ahead of `v0.1.0` by user decision 2026-10-02.** ADR-0017
  accepted 2026-10-02 with decisions 1A 2B 3A 4B 5A 6A (static env
  list `VAULT_UPSTREAM_POOL_HOSTS`, shipped seed in `.env.example`,
  `keepalive 8` per edge with a ceiling of 32 idle = at most 4 edges,
  list changes by container recreate, rate cap untouched plus a CI
  pin, `error` restored in `proxy_next_upstream`). Measurement done
  on the operator's DS-Lite line (ADR-0017 "Measurement"): the edge
  keeps connections alive and holds an idle one for at least 60 s.
  Ships in `v0.1.0-rc7` together with CORE-FIX-3 (user decision
  2026-10-03, reversing the earlier Weg B), each switchable off
  independently; freeze exception in ADR-0016. Packages:
- [ ] (origin: item 13 D) **D2 AUTH-FEAT-1 — Steam login by QR code from the web UI.**
  A small own helper on SteamKit2 (`BeginAuthSessionViaQRAsync`)
  runs in the runner container, emits only the rotating challenge
  URL, polls, and writes SteamPrefill's `Config/account.config`
  (refresh token) directly; vault-api relays URL and status only, so
  no password and no token cross the API. Endpoints behind the API
  key, short-lived, rate-limited. The terminal login with a Steam
  Guard code stays as the fallback, because Steam's location check
  can block app/QR approval when the phone is far from the server.
  Includes the Settings "Steam session: present / missing" status.
  Amends ADR-0004 and needs the freeze opened for two endpoints.
- [ ] (origin: item 13 D) **D3 MULTI-1 — several Steam accounts per household.**
  SteamPrefill keeps one session per install (`Config/` next to the
  binary). Plan: one `Config/` per account, the job carries the
  account, the runner picks the account that owns the app; the
  library shows the union of several SteamIDs with an owner hint;
  QR login (D2) per account. The cache itself is already
  account-agnostic (chunks are keyed by depot path), so a game
  cached by one account is a HIT for every client in the house.
  Steam Families sharing is deliberately not pursued for now.
- [ ] (origin: item 13 D) **D4** IPv6 egress (operator network, optional): Steam CDN
  hosts publish AAAA records and IPv6 bypasses the CGNAT. The
  operator's host runs without global IPv6 on purpose; a network
  decision, not a code change.
- [ ] (origin: item 13 D) **D5** Named, scoped API keys and per-target payload scoping
  (Phase 6, already listed there).
- [ ] (origin: item 13 D) **D6 CLIENT-1 — a proper desktop client** (user request
  2026-10-03: "moderner und professioneller", no scheduled tasks).
  Replace the script + Scheduled Task / systemd timer packaging with:
  a Windows installer (MSI via WiX or Inno Setup, Apps & Features
  entry, clean uninstall, later winget); a background service that
  watches the Steam library folders and reports on change, plus a
  light heartbeat for presence; an optional tray app (status, last
  report, version, "open Hangar", settings and connection test); set-up
  by pairing from the web UI's "PCs" list (short code or QR) instead
  of typing the API key, ideally one revocable key per PC (D5);
  update offers driven by `GET /v1/about` (follow-up from WEB-FIX-8:
  a real "newer release available" check against GitHub releases
  belongs here too, so the About table can say "Update available"
  instead of only comparing vault-core with vault-api; it needs an
  egress decision, since vault-api reaches the internet only through
  the egress proxy's allowlist and GitHub is not on it); code signing
  (SmartScreen) as an explicit cost decision; Linux/SteamOS as a package with a
  systemd user service. Open decision: service + tray (reports
  without a logged-on user, sees all users' libraries, needs admin to
  install) versus tray-only (per user, no admin).
- [ ] (origin: item 13 D) **D7 CORE-FIX-4 — hard cap on concurrent upstream connections**
  (user decision 2026-10-04, after the first Steam-client MISS test
  behind CGNAT; see the release-notes item in B). The ADR-0017 pool
  caps idle connections per listed edge (8 per group, at most 4
  groups); it does not cap in-flight connections, and a Steam client's
  concurrency is not ours to cap. Research first what nginx OSS
  offers (`max_conns` without the Plus-only `queue`, `limit_conn` on
  the client side, a local forward proxy with a connection pool),
  and whether pooling by edge name fits when a client uses 14 edges.
  Needs an ADR-0017 addendum and a freeze note.
  **Timing (user, 2026-10-04): BEFORE `v0.1.0`**, not after the tag;
  designed in ADR-0021 (one pooled edge, global `limit_conn` cap),
  built as CORE-FIX-4a..4d. 4a (core: hook, configs, preflight, drift
  check, Dockerfile, hook test, ADR-0016 freeze note) is the first
  package; the box stays open until 4b-4d are done.
  **Status:** 4a (core), 4b (live verify checks) and 4c (compose
  forwarding with edge mode on by default, `.env.example`, deploy and
  core docs, release notes) are done; 4d is done in changed form
  (user decision 2026-10-09, "Weg A"): instead of lowering the 443
  passthrough caps to 32/16 they are operator-tunable
  (`VAULT_TLS_CLIENT_MAX_CONNS` default 64, `VAULT_TLS_MAX_CONNS`
  default 256, ADR-0021 addendum 2026-10-09). **Measured 2026-10-10**
  (operator line, client on `:80`): 0x `113` errors, upstream connect
  time ~0, 16-23 upstream sockets, no SYN floods. **Still open**, so
  the box stays unticked: the `:443` measurement and the default of
  `VAULT_TLS_CLIENT_MAX_CONNS` (WORKPACKAGES carry-over 2). Related
  open measurement: CAP-503-LOOP-1 (roadmap audit list).
- [ ] (origin: item 13 D) **D8 SCHED-FEAT-1 — weekday schedules, several windows per day** (user request
  2026-10-04: "the night check is no use if I come home at 17:00 and
  the update came during the day"). Today `schedule_window` is one
  `HH:MM-HH:MM` range (editable in Settings, applies at the next
  sweep); allow a list of ranges. Until then `06:00-24:00` covers it.
  Scope extended by the user on 2026-10-04: per-weekday schedules and
  several different windows per day (e.g. Mon-Fri 12:00-16:00 and
  22:00-06:00, weekend all day). The download-rate cap window
  (`VAULT_UPSTREAM_RATE_WINDOW`, env-only in vault-core) must follow
  the same schedule, or be replaced by it.
  **Rate per window** (user 2026-10-14 "nachts ungedrosselt tagsüber
  gedrosselt", 2026-10-15 "off testen für das nachtfenster"; folded in
  2026-10-16): several windows per day, a "throttle" checkbox per
  window, and an unthrottled window also works with the scheduler off
  (it then only lifts the rate cap). Since rc18 there is one download
  window (D16a-1); `VAULT_UPSTREAM_RATE_WINDOW` stays a deprecated env
  pin until D8 replaces it.
- [ ] (origin: item 13 D) **D29 PAIR-2 — proper device pairing** (labelled "D9 PAIR-2"
  here until 2026-10-16, which clashed with roadmap D9 TOOL-APPS-2;
  follows PAIR-1, user decision 2026-10-04). PAIR-1 hands every new
  device the one shared API key. Replace that with short-lived pairing
  codes (the web UI shows a code or QR valid for minutes, the device
  trades it for its own
  key) and one named, revocable key per device, so removing a phone or
  PC revokes only its key. Builds on D5 (named, scoped API keys) and is
  the pairing step D6's desktop client needs. Needs new endpoints, so
  an ADR and the freeze opened; the `steamhangar://pair` URI gets a
  `v=2` with a code instead of a key.

### From rc19 ([archive](plan-archive/rc19.md))

- [ ] (origin: rc19) **rc19 Downloads and card block** (run 2026-10-15/16; details in
  `docs/handover/rc19-run-plan.md` and
  `docs/handover/post-0.1.0-roadmap.md`; tag not yet cut).
- [ ] (origin: rc19) **rc19.2 follow-ups** (2026-10-16, patch release on top of rc19)
- [ ] (origin: rc19) **rc19.3** (2026-10-16, throughput diagnostics and tuning switches)
  - LIVE-DELAY-1 (client downloads show more than 1 min late on the
    Downloads page) moved to the rc20 block below (user decision
    2026-10-16): analysis and fix before DOWNLOADS-1.

### From rc20 ([archive](plan-archive/rc20.md))

- [ ] (origin: rc20) **rc20 web redesign** (decided 2026-10-16, mockup round with the
  operator; reference: `docs/design/rc20/`, run notes:
  `docs/handover/rc20-run-plan.md`). The redesign packages are web
  only, no schema change, no new env value. Order: LAYOUT-1 and
  SHEET-MOTION-1 first (shared base), then the page packages;
  LIVE-DELAY-1 lands before DOWNLOADS-1. The three non-web packages
  below (user decisions 2026-10-16) ride in the same rc.
  - [ ] nit (LIBRARY-DETAIL-1): tab keyboard focus is lost on a structural
    re-render of the detail sheet (fixed in NITS-WEB-1:
    `game-detail-sheet-update-group.test.js`); `.detail-box-danger` hard-codes an rgba
    (no `--danger-soft` token yet); optional `tabindex=0` on tabpanels;
    move `add-device-sheet.js` onto `components/tabs.js`
  - [ ] nit: Exit animation for the nine confirm-dialog call sites
    (.dialog-backdrop toggles .on directly; currently animates in only)
  - [ ] nit: Add-a-device tabs have no icons and the dialog has no
    numbered steps, unlike the mockup (`docs/design/rc20/`)
  - [ ] nit (DOWNLOADS-1): the generic failure text could add "If it fails
    again, check the log"
  - [x] nit (LIBRARY-1): hard-coded CSS fallbacks (`--tool-h` is not in
    theme.css) and a stale header comment in `library.js` (fixed in
    NITS-WEB-1: `web/js/views/library.js` header; the `--tool-h` fallbacks
    were already gone with LIBRARY-2)
  - [ ] **Rework after the operator's visual check (2026-10-17).** The
    packages above shipped the structure of the mockups (columns, tabs,
    nav, split button, drag queue) but not their anatomy: the page
    title, tiles, pills, section labels, segs and field cards were
    re-styled per page from the old primitives, so every page reads as
    "old look, new layout". Root causes and remedies are the UI kit
    (`docs/design/rc20/kit.html`, approved 2026-10-17) and web
    screenshot goldens. Decisions: Clients cards one per row in three
    zones; Downloads hero with the four totals beside the graph and
    three columns below; `--dim-2` raised to about #7A91A0 (5:1) and
    no text below 11px; pills wrap the existing `status-icon.js` disc
    (shape first, word second, colour third; glyph box 60% of the disc;
    motion means "right now" and stays as is).
    - [ ] nit (DOWNLOADS-2 review): the bypass banner (`.banner-wrap`,
      `--w-text`) is narrower than the Downloads page at BP-M.
    - [ ] nit (WEB-GOLDENS-1 review): goldens for one open sheet per page
      (library detail, add a device) deferred; `dev.sh test-web-shots`
      is operator infrastructure, requested as Antrag A-20261017-01.
- [ ] (origin: rc20) **rc20.x (optional patch)**: new defaults for `VAULT_NGINX_AIO` /
  `VAULT_NGINX_WORKERS` only if the Sunday cold-game test (ADR-0027,
  box in the rc19.3 block) says so, with a new ADR carrying the numbers;
  otherwise the item moves to rc21. Test 2026-10-18: AIO qualifies
  (about 5-7 times faster cold, link-limited), WORKERS=2 gets no default.
  rc20.2 is taken, so the slot is rc20.3 or rc21: open decision
  AIO-DEFAULT-1 in the roadmap (Weg A code default + ADR, B prod `.env`
  only, C both, D measure more).
  - [ ] VERIFY-COLD-1: verify window back on after AIO is live on prod
    (user decision 2026-10-18); devbox watches the first verify run.
    AIO is live on prod since 2026-10-18 12:40Z (A-20261018-02; idle
    heartbeat from outside 5x 200 in 0.8-0.9 ms). Prod has
    `VAULT_SCHEDULE_WINDOW=off` pinned in `.env`, so the window cannot
    be switched in Settings. Decided 2026-10-18: Weg A, a manual
    verify run (Complete all / Verify on several large games) started
    by the operator AFTER his vacation; the devbox probes the heartbeat
    every 5 s, iostat partition lines and the duration.
    Pre-measurement 2026-10-18 20:40-21:08 (prod rc20.2, AIO=threads,
    Auto-GC dry-run; manual Verify of Enshrouded, AC Black Flag
    Resynced, Gothic 1 Remake, Hogwarts Legacy done, No Man's Sky
    stopped; cache partly warm, no drop_caches): heartbeat from outside
    327 probes, all 200, p50 0.9 ms, p95 3.0 ms, max 9.1 ms (without AIO
    on 2026-10-18: up to 7.6 s); the four data partitions read about
    209 MB/s summed (p50, max 236 MB/s, incl. parity); about 238 GB in
    ~25 min (approximate, job times not taken exactly). Every job: all
    HIT, "0 depots observed", Auto-GC queued in dry-run mode (A-20261018-03
    check done). Open for the full run: cold data, exact job durations.
  - [ ] nit (AUTO-VERIFY-1): local `dev.sh test-web-shots` fails
    `clients-pair-shown-400` at 0.42 % inside the QR (the case encodes
    the serving URL/port); pin a fixed URL in the case. Android
    Downloads note does not read `auto_complete_cached` yet (says
    "complete automatically" when off).
    Also Android: the setting renders as a free-text field, not an
    Off/On toggle (`SettingsScreen.kt:316-341` special-cases only
    auto_gc, sweep_include_cached, webhook_events); Android demo
    (`DemoState.kt` ~920, `scheduleOut()`) lacks `auto_complete_cached`.
  - [ ] nit (MOBILE-1b review): real "Download app" button state is in
    no golden (pairing cases render the demo note); no real-device
    check of safe-area insets yet.
  - [ ] nit (GROUP-VERIFIED-1 review): ~~`routers/games.py:175-185`
    comment and api/README.md (~646, event-sweep section ~4470) still
    describe the old `last_manifest_check` rule~~ (fixed in NITS-API-1:
    `routers/games.py` GameSummary comment, api/README.md `/v1/games` row;
    the job-outcome table and the event-sweep section already matched);
    open (web, NITS-WEB-1): flaky
    `web/tests/clients-merged-card-wiring.test.js:103-108` (2 s wall
    clock under full-suite load: wait for the event or raise it).
  - [ ] nit (MOBILE-1b review): Library toolbar at 1000 px puts the
    search (180 px) and the chips in a narrow left column under the
    title (on main already, not changed by MOBILE-1b); check against
    the kit layout.
  - [ ] nit (UI-COLD-CACHED-1 review): no backoff when walks keep
    failing (one ERROR per expired read, bounded by request rate);
    theoretical waiter starvation after a failed walk; the Thread.start
    test patches `threading` process-wide
  - [x] nit (TP-REPORT-2 review): `parse_probe` accepts `nan`/`inf`/
    negative values (core/tools/throughput-report.py:327-342, 365);
    README probe example `date +%s.%N` needs a `%s` fallback.
    (fixed in NITS-API-1: `test_parse_probe_rejects_nan_inf_and_negative`
    in core/tests/test_throughput_report.py; core/README.md `%s` note)

### From rc21 ([archive](plan-archive/rc21.md))

- [ ] (origin: rc21) **rc21 — Android parity and cache health** (cut 2026-10-17; web
  and API packages first, Android packages and golden images only after
  the operator's rc20 visual check; no rc21 tag without the operator's yes;
  rc20 visual check PASSED, operator 2026-10-18: Android packages unblocked)
  - [ ] nit (CACHE-REPAIR-UI-1 review): the sheet's Repair buttons use
    `withButtonBusy` (`disabled`, no "Checking…") unlike the Downloads row
    (aria-disabled + "Checking…"); demo route has no `not_owned` 409
    branch; a flow closed during execute only re-reads the store, no toast
  - [ ] nit (TEST-HYGIENE-1 review): Lint 3 blind spots (guard
    ancestors must all appear in the rule's ancestors, so
    `.kit .grid.selecting .pick` vs `.kit .card .pick[hidden]` is not
    flagged; `.kit.<page>` treated as `.kit`) should be stated in the
    lint header; ten web test files still use `until(..., 2000)`
    (clients-live, clients-view-wiring, clients-nav-refresh,
    clients-rename-wiring, steam-library-setting, onboarding-steamid,
    downloads-names-loading, downloads-layout, downloads-updates-wiring,
    downloads-owned-preloaded).
  - [ ] nit (FIX-POP-HIDDEN-1): `.pop` has no box-shadow and
    `.pop.menu` min-width is 100% where the Library mockup shows 260px
    plus shadow; dead `.cl-list > p` rule (already gone, checked in
    NITS-WEB-1); the Clients empty state repeats
    the "Add a device" accessible name.
  - [ ] nit (UNMAPPED-FLAG-1, VERSION-HEALTH-1): ~~`get_game` scans all of
    `depot_manifests` for one appid (add `AND appid = ?`)~~ (fixed in
    NITS-API-1: `unmapped_depot_counts(..., appid=)`,
    `test_unmapped_counts_helper_narrows_to_one_app`); library card
    keeps the green check at 100% with unmapped depots and Android lacks
    the note (Android parity package); VERSION-HEALTH-1 chose the startup
    log line, `/v1/health` stays a fixed body (fingerprinting decision)
  - [ ] nit (rc20 reviews): CLIENTS-3 `tab:null` wiring check is a regex
    over source (use a spy) (fixed in NITS-WEB-1:
    `clients-view-wiring.test.js` clicks the button and reads the tabs);
    `getSettings` call on Clients has no guard against a detached view
    (checked in NITS-WEB-1: its callbacks only call `paint()`, which returns
    while `els` is null, so no change); tile wording "Version differs from the
    server" could read "differs from the server version"
  - [ ] nit (rc20 reviews): SETTINGS-1 nav label "Other settings" is
    vague; stale comment about the `api.schedule()` refetch after save
    in `settings.js` (check whether `state.schedule` is still needed)
  - [ ] nit (rc20 reviews): LIVE-DELAY-1 `active_clients` under-reports
    for up to 5 minutes after a vault-api restart (ring not filled yet);
    no test for a live answer with the feed ok but `active_clients`
    null or missing
  - [ ] nit (GRAPH-RECENT-1 review): the newest, still-open bucket is
    divided by the full bucket length, so early in a minute it draws far
    below the header speed (N1, handed to RUNNER-SPLIT-1); summary tiles
    (served, average, peak, hit rate) and job-card speeds stay swept, so
    the graph's newest point can briefly exceed "Peak"; Android demo
    `watcher_window_seconds = 30` vs web 60 (`DemoTraffic.kt:210`) and
    Android ignores the new `now` block (header and "Loading now" still
    lag there: Android Downloads follow-up); a brand-new PC loading
    before its first sweep counts in "Loading now" but has no Clients
    card yet; runner traffic counts as client traffic in the ring until
    a sweep extends the runner's address window on a new UTC day
    (relevant for NOW-LOADING-1).
  - [ ] nit (RUNNER-SPLIT-1 review): ~~redundant `when is not None` in
    `_series_point` (traffic.py ~748)~~ (fixed in NITS-API-1,
    `routers/traffic.py`); ~~`/v1/throughput/live` still divides
    its running 5/15/60 s point by the full bucket length (N1 fixed only
    for the series)~~ (fixed in NOW-LOADING-1, `_elapsed_seconds`); ~~a
    running ring bucket that only partly covers its bucket (api restarted
    mid-bucket) still reads too low~~ (fixed in NITS-API-2:
    `live_tier.covered_seconds`, LIVE-DIVISOR-1); demo summary
    says the runner was 60 % HIT while its points say 0 % (kept for
    golden stability). Android: read the runner split, add the
    "Verifying" pill and count runner HITs as cache in the graph.
  - Android package cut (orchestrator, 2026-10-31, operator's yes to run
    rc21 incl. Android): each package builds and tests only in GitHub CI
    (push `wp/*`, app goldens via a `wp/<name>-goldens` branch).
    - [ ] nit (ANDROID-DL-1 review): `ClientsScreenshotTest.kt:96`
      `heightDp = 640` cuts the 360dp/font130 Downloads golden through the
      axis captions and hides the legend (raise to ~760 with the next app
      goldens round, ANDROID-DL-2); Kotlin `splitGeometry` always draws
      the split (fields default to 0) where the web falls back to one
      line; TalkBack focus stays on "Show N more" (web moves it to the
      first new row); the two graph fills are hard to tell apart in the
      night golden (REDESIGN-1b); import order in `DownloadsStats.kt`.
      (partly fixed in NITS-APP-1: `DownloadsStats.kt` imports sorted; the
      Downloads shots are already 1000/1250 dp high, see REDESIGN-1c; still
      open: `splitGeometry` fallback, Show-more focus, night fills)
    - [ ] nit (ANDROID-GAPS-1 review): the Rename button leaves an empty
      band at the bottom of nameable Clients cards (`ClientsScreen.kt:273`,
      REDESIGN-1a); busy dialog buttons are not visually disabled (guards
      exist); `startLibraryLookup` guard could also check
      `lookupJob?.isActive`; leaving onboarding via Cancel/Demo or an
      activity recreate still cancels a running lookup; Look up uses the
      current step-1 fields, not the verified ones; no Remove on the
      Clients page cards (web has it, REDESIGN-1a); demo Delete history
      does not zero the synthetic traffic like the web demo's "since" cut.
      (partly fixed in NITS-APP-1: `startLibraryLookup` also checks
      `lookupJob?.isActive`, pinned by `OnboardingLibraryIdTest` "a double
      tap before the lookup runs"; the rest needs goldens or a behaviour
      decision)
    - [ ] nit (ANDROID-DL-2 review): focus after "Show more" goes to a
      wrapper Box (input focus; TalkBack accessibility focus may not
      follow, check on a device); "Show job" only scrolls, no focus;
      Repair only in the sheet's completeness block (web also in header
      and Maintenance tab, REDESIGN-1b); a flow reset during execute only
      re-reads (inherited from the web).
    - [ ] nit (ANDROID-REDESIGN-1a review): `SectionLabel` sets
      `contentDescription` on an uppercase Text; inside merged cards
      TalkBack may read "Connected, CONNECTED" (check on a device; else
      `clearAndSetSemantics { text = ... }`); 12dp gap above DEVICES
      before the first answer; no agent-only cards when the overview
      returns 404 (web shows them); footer buttons wrap below a long
      last-active line; no golden for the bypass row or the empty state;
      the PC list is fetched twice while the PCs sheet is open.
    - [ ] nit (ANDROID-REDESIGN-1b review): `DETAIL_TABS_TAG` unused
      (use it in the 1c sheet screenshot test or drop it); the Overview
      "Cache" fact lacks `groupCompletenessLine` (group figure only on
      Maintenance); the graph grew 72 -> 88dp; accepted deviations to note
      in rc20-visual-check.md so the device check does not flag them:
      Filters only with metadata, store details first on Overview
      (CARD-LAYOUT-1 pin); `strings.xml:210` hint `http://192.168.1.50:8080`
      belongs to SAMPLE-IP-1. (NITS-APP-1 check: `DETAIL_TABS_TAG` is used
      by `DetailSheetScreenshotTest`; the deviations are already listed in
      rc20-visual-check.md "Android rc21: accepted deviations"; still open:
      `groupCompletenessLine` on Overview, the hint via SAMPLE-IP-1)
    - [ ] nit (ANDROID-REDESIGN-1c review; KDoc and Overview ISO times fixed in POLISH-1): hero runner mini item and the
      0-devices caption descriptions differ from the web tile titles;
      downloads_stats shot heights 1000/1250 leave empty canvas;
      the "Surveyor Outfits" package row squeezes its name at 360dp/1.3.
    - [x] nit (ANDROID-POLISH-1 review): `assertOneControlRow` does not
      check the sort button (`LibraryScreenshotTest.kt:646-659`);
      `EXPERT_SECTION_ID` duplicates the id literal from
      `SettingsLayout.SECTIONS`; import order in `GameDetailSheet.kt:67`;
      at 360dp/1.3 with metadata Select still wraps alone (documented
      worst case). (fixed in NITS-APP-1: `assertOneControlRow` checks the
      sort button; `EXPERT_SECTION_ID` is the one Kotlin constant,
      `SettingsController.EXPERT_SECTION` derives from it and
      `SettingsDirtyDotsTest` pins it to a `SECTIONS` id; `GameDetailSheet.kt`
      imports sorted; the 360dp/1.3 wrap stays the documented worst case)
    - [x] nit (APP-NIGHT-GOLDEN-1 review): a blind golden refresh would
      silently accept a changed night shot; add a test asserting the
      night shot equals its notnight twin pixel for pixel. (fixed in
      NITS-APP-1: `NightGoldenTwinTest`)
  - Cancel question (NOW-LOADING-1, answered from code, reviewer-checked;
    no prod measurement): a cancelled job's SteamPrefill does not keep
    loading. `cancel_job` only sets `stop_request` (jobs.py ~894-990);
    the runner polls it every 0.2 s (prefill.py:122, prefill_runner.py
    ~215-251) and stops the child with SIGTERM, then SIGKILL after 5 s
    (prefill.py ~750-781); the job reads "cancelled" only after the
    process is reaped (prefill_runner.py ~281-298, worker.py ~376-383,
    ~918-921). The operator's screenshot (pill "Downloading", ACTIVE
    empty) was log flush (5 s, nginx.conf.template:671) plus window lag,
    on rc20.2 the swept 5-minute pill.
  - [ ] nit (NOW-LOADING-1 review): ~~`addrs_listed` ignores the
    clients/runner scope (an address listed only under the runner card
    counts as known on the client side)~~ (fixed in NITS-API-1:
    `test_an_address_listed_only_under_the_runner_is_not_a_known_client`);
    each loading poll reads all of
    `agent_reports` at the 3 s live rate; ~~device count counts addresses
    while rows group by agent ("2 devices loading", one row)~~ (fixed in
    NITS-API-2: the count is the client rows, LOADING-COUNT-1); demo job
    card shows 31.5 MB/s (swept window) next to a 6.4 MB/s runner row.
  - tuning defaults from the Sunday test, if not shipped in rc20.x
    (the rc20.x box moves here)
  - [ ] nit (RUNNER-RETRY-1 review): `runner_mark.announce` runs once
    before attempt 1, a successful retry may run unmarked
    (prefill.py:611, worker.py:326; NITS-API-1 left it: a mark that was
    sent covers the retries through its 7-day period, and the unmarked case
    is a first announce that found no cache, where a re-announce before the
    retry hits the 300 s negative cache, so it needs a decision on that
    cache); ~~runner_identity.py docstring
    "at most VAULT_PREFILL_TIMEOUT_SECONDS" is now up to 3x plus pauses;
    stub_prefill `lancache_failures.txt` counter never reset~~ (fixed in
    NITS-API-1: runner_identity.py docstring,
    `test_a_new_stub_or_failure_budget_resets_the_detection_counter`).

### From rc21.1 ([archive](plan-archive/rc21.1.md))

- [ ] (origin: rc21.1) **rc21.1 — mapping and name fixes from the prod rollout** (operator
  decision 2026-11-01: patch, not rc22, with every item below incl.
  PAUSE-ALL-GC-1, JOB-SIZE-1 and RUNNER-NAME-1; `dev.sh diag db` requested
  in Antrag A-20261101-02)
  - MAP-ATTRIB-1 review (2026-11-01, simulated on a read-only export of
    prod): the cleanup also removes (1465471, 1203620) — Enshrouded
    holding 1465470's depot — and Enshrouded's own depot 1203621 is not
    mapped on prod although its own recorded manifest (job 205,
    2026-10-31) and the oracle list name it (replace-step drift, MAP-DRIFT
    family). Added to MAP-ATTRIB-1: the one-time step also restores a
    missing own mapping when the app's own recorded manifest AND its
    oracle list both name the depot; the step stamps once the temp cache
    was readable (skips logged for the operator), so it never becomes a
    permanent startup rule. The rc21.1 rollout Antrag must list the
    expected removed and restored pairs.
    Final expected prod effect (reviewer simulation on a full read-only
    export, b97a62e): removed (436781, 3624140), (553851, 3624140),
    (553854, 3624140), (1716751, 3624140), (1465471, 1203620); restored
    (292033, 292030), (292036, 292030), (1203621, 1203620) — restores only
    from the app's own record (containing_appid A or NULL) whose gid equals
    the oracle public gid. Depot dirs measured on prod 2026-11-01 (operator,
    read-only): 292033 3.47 GiB, 292036 3.77 GiB, 1203621 42.97 GiB, all
    present; these are the "unmapped" depots of the storage balance.
  - [ ] nit (PAUSE-ALL-GC-1 review): a paused GC also blocks
    miss-triggered prefills for its app (documented); a paused execute GC
    resumed after switching VAULT_AUTO_GC still executes (documented,
    cancel instead); GcFlowTest "an unrecognized job status keeps
    polling" also asserts the known status "queued" (rename or use a second
    unknown status).
  - [x] nit (TOOL-NAMES-1 review): a cached, not-owned app (prod 1716750
    Ubisoft Connect) gets a one-off oracle verdict from the name lookup
    and shows "Update available" it cannot act on until the not_owned
    window expires; consider hiding update_available for not_owned apps.
    (fixed in NITS-API-2: no `update_available` while `not_owned`,
    NOT-OWNED-UPDATE-1)
  - [ ] nit (MAP-LOSS-1 review): a depot Steam moved away keeps its old
    row (keep-only, GC keeps more) and can leave an old completeness target
    ("Update available"/"not verified") until DELETE /v1/mapping; the
    oracle list also keeps an existing package-depot row on the game
    (docstring should say so) (docstring fixed in NITS-API-1:
    `apply_observed_mapping`, both points); the kept-reason line names all
    three sources at once.
  - [ ] nit (MAP-ATTRIB-1 review): one oracle_branch_manifests query per
    restore candidate; the inlined `apps` INSERT duplicates upsert_mapping
    on purpose (it commits inside BEGIN IMMEDIATE).
  - [ ] nit (STORAGE-BALANCE-1 review): the balance line is skipped
    when a job or GC invalidated the size snapshot and nothing read the
    sizes since; the expert card is in no golden (Expert panel hidden in
    every golden view).
  - [ ] nit (JOB-SIZE-1 review): the live runner speed counts all of
    the runner's depots, so a completeness ETA can run a little short;
    a not-yet-applied runner period leaves runner-scoped figures at 0
    for up to one sweep; `etaSmoother` keeps a rate across leaving and
    re-entering Downloads (clear it on mount); the preview total
    includes owned DLC and language depots (older D19 behaviour).
  - [ ] nit (RUNNER-NAME-1 review): mini line "SteamHangar 25.6 GB of
    it" could read "By SteamHangar: ..."; KDoc line widths in
    ClientsPage.kt / DownloadsHero.kt; web/tests/now-loading.test.js:47
    uses 192.168.1.50 (SAMPLE-IP-1).
  - [ ] nit (ANDROID-RC21-1-TWINS review): StorageBalanceCardTest uses
    contains-style pins (use assertCalledFrom like D15dWiringTest);
    `smoother.observe` runs as a composition side effect (idempotent); a
    malformed `storage_balance` fails the whole summary decode on Android.
    (partly fixed in NITS-APP-1: `StorageBalanceCardTest` source pins use
    `assertCalledFrom`; still open: the decode tolerance, a behaviour
    decision)
