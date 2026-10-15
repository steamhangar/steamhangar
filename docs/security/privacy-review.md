# SteamHangar - Privacy Review (D21, PRIVACY-REVIEW-1)

Status: review of the tree at rc18.1 plus main (2026-10-15). Read-only: no
production code, config or test was changed. Companion documents:
`docs/security/threat-model.md` (the long-form model; its sections 4 to 6 are
the earlier per-topic analysis this review consolidates) and the draft
`docs/adr/0023-privacy-mode.md` (D22).

Citations are `file:line` against this tree. Line numbers drift; the symbol
named next to each one is the stable anchor.

Rating scale: **low** (no personal data, or fully bounded and optional),
**medium** (personal or household-identifying data reaches a party outside the
person it describes, or a control is missing, but the exposure is bounded),
**high** (a household member's behaviour or identity leaves the LAN or reaches
other members without a working off switch, or a failure turns a protection
into collection).

## 1. Method and scope

Grepped `api/`, `core/`, `proxy/` (the tree has `deploy/proxy/`), `dns/`,
`deploy/`, `agent/`, `app/` and `web/` for hostnames, `urlopen`/proxy use,
log formats, retention constants and auth dependencies, then read the owning
module's docstring and the compose wiring. Each outbound row states the
control, the default, and what happens when its source is off, unreachable or
blocked (the per-store failure-mode analysis `docs/LEARNINGS.md:557-565`
requires before any switch protecting another person is designed).

Out of scope: Steam credentials inside `vault-runner` (SteamPrefill logs in to
Valve; product purpose, ADR-0004, not optional) and the cache MISS traffic
itself (threat-model section 5).

## 2. Outbound inventory

All `vault-api` traffic goes through `vault-proxy` (an allow list on hostnames,
`FilterDefaultDeny Yes`, `deploy/proxy/tinyproxy.conf`); `HTTP_PROXY`/
`HTTPS_PROXY` are set at `deploy/compose.yaml:700-701`, `NO_PROXY` is loopback
only (`:737`). The allow list is baked hosts
(`deploy/proxy/docker-entrypoint.sh:114`: `api.steampowered.com`,
`shared.akamai.steamstatic.com`, `cdn.akamai.steamstatic.com`), plus
`api.steamcmd.net` while the oracle is on and its URL is the default
(`docker-entrypoint.sh:168`), plus `VAULT_EGRESS_ALLOW` (`compose.yaml:747`,
`config.py:973`).

| # | Flow | Host | What is sent | Identifies user / account / library? | Default | Control | Off / unreachable / blocked | Rating | Recommended default |
|---|------|------|--------------|--------------------------------------|---------|---------|------------------------------|--------|---------------------|
| O1 | Manifest oracle, update check, per cached game every `VAULT_SCHEDULE_INTERVAL_MINUTES` (180) | `api.steamcmd.net` (third party, not Valve) | one app id per request, from the household WAN IP (`oracle.py:128-139`, `update_check.py:1-40`) | Library: yes, the set of games in the cache and roughly when. Account: no (no key, no SteamID, no client id) | **On** (`config.py:155`, unset/blank means on; `compose.yaml:604`) | `VAULT_MANIFEST_ORACLE=off\|none`, `_URL` (own mirror), `_TIMEOUT`; env only, not in `OVERRIDABLE_SPECS` (`settings_store.py:305` only lists the timeout) | Off: no update check, API says `enabled: false`, proxy drops the host (`docker-entrypoint.sh:168-210`); no fallback source. Unreachable or blocked: `refresh_app` never raises, previous snapshot kept, run bails after 5 consecutive failures (`update_check.py:73`). Custom `_URL` host not in `VAULT_EGRESS_ALLOW`: boot refused, by design (`compose.yaml:738-746`) | medium | on for `balanced`, off for `strict` |
| O2 | Oracle on card open (D19-1a, planned, not shipped) | same | one app id per opened card of an uncached game | Browsing behaviour of whoever opens the card, by game, with timing | n/a (design says follows `VAULT_MANIFEST_ORACLE`) | none of its own yet | design: card stays "unknown" | **high if shipped as sketched** (see F1) | off in `strict`, TTL plus per-appid dedupe in `balanced`, never on a bare `VAULT_MANIFEST_ORACLE=on` |
| O3 | Cover lookup `IStoreBrowseService/GetItems`, background, batches of 50 every 60 s until all rows are done, re-asked every 30 days (found) or 7 days (none) | `api.steampowered.com` (Valve) | app ids as `input_json`, language `english`, country `US` (`cover_art.py:16-40,114-124`) | Library: yes, every app that has a job or mapping row (`jobs.py:290`, `mapping.py:61`, selected by `cover_art.py:323-340`). Account: no (`cover_art.py:42-48`) | **On, not switchable** (user decision 2026-10-10, `cover_art.py:42`) | none; the only way off is the egress lock | Unreachable/blocked: per-app backoff doubling, whole thread pauses, stored URL kept, one WARNING per streak (`cover_art.py` module docstring) | medium | on for `balanced`, off for `strict` |
| O4 | Cover images, server side (`CoverFileFetcher`, 20 per 60 s tick) | `shared.akamai.steamstatic.com`, `cdn.akamai.steamstatic.com` | GET of one image path per app | Library: yes (path contains the app id) | **On, not switchable** | none | Failure: backoff, app keeps CDN URL (`cover_cache.py` "Fetching") | medium | with O3 |
| O5 | Cover images, client side (browser and Android app) | same two Valve hosts | image GET from each viewing device's own IP (web `cover-art.js:98,166`; app `CoverArt.kt:28,31`; CSP `webui.py:103-114`) | Library and viewing device: yes, per viewer, direct to Valve, bypassing the egress lock | **On**; used when the cached copy is missing (`cover_cache.py` serves signed LAN copies first) | none | Blocked: styled fallback tile; this is the one path where a failure of the cached store moves collection to the device | medium | `strict` must also drop the legacy-URL fallback and tighten `img-src` |
| O6 | Steam Web API relay | `api.steampowered.com` | `GetOwnedGames` and `GetPlayerSummaries` with the stored Web API key and SteamID64 in the query string (`steam_relay.py:147,444-459`) | **Account: yes** (key plus SteamID64). Library: yes. Playtime/last played returned to callers if the gates are on | **Off** until a key row exists (`steam_relay.py:14-22`), then every relay call from any API-key holder | key via `PUT/DELETE /v1/steam/key`; `VAULT_STEAM_LIBRARY_STEAMID` (DB-overridable, `settings_store.py:322`) | No key: `409`. Blocked or unreachable: `SteamRelayError`, `502`, message built from the path only | medium | off; on only by explicit key entry in all modes except `strict` |
| O7 | Webhooks | one operator URL (`VAULT_WEBHOOK_URL`) | job and client events; `vault_name`; for `client.bypass_*` the client id and addresses (`webhooks.py:253-254,538-539`) | Device names and LAN addresses: yes | **Off** (blank) | `VAULT_WEBHOOK_URL` (env only, masked, `settings_store.py:449`), `_EVENTS`, `_TIMEOUT`, `VAULT_NAME` | URL not in `VAULT_EGRESS_ALLOW`: `403 Filtered`, bounded queue, oldest dropped and counted (`webhooks.py` delivery section). The LAN-receiver case needs the same entry (`compose.yaml:703-720`) | medium | off; `strict` ignores it; `balanced` omits client addresses and ids from payloads |
| O8 | Extra egress hosts | any named host | whatever vault-api sends there | n/a | empty | `VAULT_EGRESS_ALLOW` (env, forwarded to api and proxy) | n/a | low | honoured everywhere except `strict` |
| O9 | `store.steampowered.com` (D19-1c, planned) | `store.steampowered.com` (Valve) | `appdetails` and `appreviews` for one app id per opened card | Browsing behaviour per game, WAN IP | planned `VAULT_STORE_DETAILS`, default off | planned | design: card omits extras | **high if on by default** (F1) | off in `strict` and `balanced` until chosen; on in `open` |
| O10 | Updates and releases | `ghcr.io` (image pull), `github.com` (release assets, install snippets) | pull by the operator's Docker; the UI hands out `github.com/.../releases` links (`dist.py:56,123`, web `agent-install.js:91`) | the operator's pull; no call is made by vault-api itself | operator-driven | `VAULT_IMAGE_TAG`, compose | vault-api never contacts either host (not on the allow list); the `/dist` files are baked into the image (`routers/dist.py` docstring) | low | unchanged |
| O11 | Android app Steam login | `steamcommunity.com` from the phone | OpenID `check_authentication` (`SteamOpenIdLoginUrl.kt:43`) | Account: yes, by purpose | on when the user signs in | sign-in is user-initiated | vault-api never sees it (`routers/app_return.py:1-30`) | low | unchanged |
| O12 | vault-dns upstream | `UPSTREAM_DNS_1/2`, default `1.1.1.1` and `8.8.8.8` (`compose.yaml:1212-1213`, `dns/dnsmasq.conf.template:81-82`) | every non-Steam DNS name any LAN client resolves, when the household uses vault-dns as its resolver | Browsing metadata of every device | **Defaults to two public third-party resolvers**; query logging itself is off (`dnsmasq.conf.template:165`) | `UPSTREAM_DNS_1/2` | Unreachable: resolution fails for non-Steam names | medium | document the default; recommend the router as upstream |
| O13 | vault-core resolver and MISS traffic | `VAULT_RESOLVER` (required, no default, `40-vault-preflight.sh:77-81`) and Valve CDN over plain HTTP | depot URL, header `X-SteamHangar-Hop: 1` (`nginx.conf.template:703`); no client address is forwarded (no `X-Forwarded-For`, `nginx.conf.template:363,506`) | The cache as a product; not the client | on, product purpose | n/a | n/a | low | unchanged |

Not found anywhere in `api/`, `core/`, `dns/`, `deploy/`, `agent/`, `app/` or
`web/`: telemetry, crash reporting, analytics, web fonts or any CDN script.
The CSP is `default-src 'self'` plus the two image hosts (`webui.py:103-114`).
The agent talks only to the configured vault-api (`agent/go/report/report.go:9-13`).

## 3. Exposure of per-person data to household members

The auth boundary is one shared key. `require_api_key`
(`api/vault_api/auth.py`) guards every router except three public surfaces:
`GET /v1/health` (fixed body, `routers/health.py`), `GET /v1/covers/{appid}/{sig}`
(HMAC keyed with the API key; not enumerable, `routers/covers.py`) and the
non-`/v1` `/dist/*`, web static files and `/auth/steam/app-return`
(`routers/dist.py`, `routers/app_return.py`). The key is also the admin key
(delete cache, PATCH settings, set the relay key); there is no per-person scope
(D5 plans it). The web UI keeps the key in `localStorage`
(`web/js/api.js:37,62`), the Android app in an encrypted store.

| # | Datum | Where exposed | Who sees it | Control | Rating | Recommended default |
|---|-------|---------------|-------------|---------|--------|---------------------|
| E1 | Playtime and last played of the configured Steam identity | `GET /v1/steam/owned-games` (`routers/steam.py:291-292`) | any key holder | `VAULT_RELAY_EXPOSE_PLAYTIME`, `_LAST_PLAYED`, default false (`config.py:1148,1153`), env only so a lost db cannot re-enable (ADR-0010) | low | off in every mode except an explicit `open` |
| E2 | Persona name, SteamID64, avatar URLs | `GET /v1/steam/player-summaries`, Settings "Library preview" | any key holder | none; gate does not cover this endpoint (threat-model 4) | medium | hide behind the relay being configured (already) and the mode |
| E3 | Per-PC installed app ids and the PC's own name and LAN addresses | `POST /v1/agent/installed` stores, `GET /v1/clients` returns `client_id` (default: hostname), `source_addrs`, `peer_addrs`, `reported_addrs` (`routers/clients.py:115-140`, `agent/go/report/report.go:9-13`) | any key holder | none; retention `VAULT_AGENT_REPORT_KEEP` = 20 snapshots (`config.py:95`) | **medium** | keep; `strict` should let an operator turn the Clients page and agent reports off; no raw peer addresses in `balanced` |
| E4 | Per-address cache traffic (who loaded which depot when) | event log `$remote_addr` per request (`nginx.conf.template:488`), aggregated to `client_cache_stats`, shown by Clients | any key holder; the raw log to anyone with host log access | event log is opt-in (`VAULT_EVENT_LOG`, `compose.yaml:173`, default empty) but D15 needs it on; file cap `VAULT_EVENT_LOG_MAX_BYTES` 64 MiB (`config.py:396`); stats keep 48 windows (`config.py:386`) | medium | opt-in stays; `strict` off |
| E5 | Plain access log with `client=$remote_addr` and URI, plus TLS passthrough log with SNI | `docker logs` (`nginx.conf.template:522,953-957`) | host operators | no off switch; bounded by json-file rotation 10 MB x 5 per service (`compose.yaml:21-30`) | medium | document; consider a `strict` that logs without addresses |
| E6 | Job history (what was downloaded, when, outcome) | `GET /v1/history`, Downloads page | any key holder | delete scopes only; no automatic age limit found in `history.py` (its `KEEP_DAYS = 30` at `history.py:63` is a delete scope, not retention) | low | unchanged |
| E7 | Webhook payloads with client id and addresses | see O7 | the receiver | O7 | medium | omit addresses in `balanced` |
| E8 | Which apps the vault tracks | cached `/v1/covers` signatures are not enumerable; `GET /v1/games` is keyed | key holder | HMAC covers | low | unchanged |

Secrets handling. `VAULT_API_KEY` is mandatory in compose (`compose.yaml:418`)
and compared constant-time on bytes (`auth.py`). The Steam Web API relay key is
stored in clear in the SQLite file (`steam_relay.py:386,407`), returned only as
the last four characters, never logged (`steam_relay.py:24-40`); anyone with the
db volume or a backup reads it, and the key rides in the query string to Valve
(TLS). Webhook URL userinfo is converted to a header and redacted in logs
(`webhooks.py:102-107`). The oracle logs only app ids and parse warnings
(`oracle.py:915`).

Telemetry retention: SteamHangar emits no telemetry of its own. Everything it
keeps is local: container logs (rotation above), the event log (size cap), the
db (agent reports 20 per client, client stats 48 windows, job rows until
deleted).

Open egress channels that the allow list does not close, both documented and
accepted in ADR-0011: DNS resolution through Docker's embedded resolver, and
the Docker host's own addresses (`compose.yaml:703-720`). A compromised
`vault-api` could use them to move a stored key or app list out.

## 4. Failure-direction analysis

Rule (`docs/LEARNINGS.md:557-565`): for each store a setting can live in, what
happens when it is lost, and which way does that point.

| Control | Lives in | Store lost (db volume wiped) | Env lost (compose file edited back to defaults) | Direction |
|---------|----------|------------------------------|--------------------------------------------------|-----------|
| `VAULT_MANIFEST_ORACLE` | env only | no effect | falls to code default **on** | toward collection, but the operator edited the env |
| Relay key, `VAULT_STEAM_LIBRARY_STEAMID` | db | relay returns `409`, no outbound call | n/a | toward protection |
| `VAULT_RELAY_EXPOSE_*` | env only (ADR-0010) | no effect | default false | toward protection |
| `VAULT_WEBHOOK_URL`, `VAULT_EGRESS_ALLOW` | env only | no effect | default off / empty | toward protection |
| Cover lookup, cover images | nothing to switch | n/a | n/a | always on |
| Cover fallback to the viewer's own request | code | cached file missing (db/dir lost) makes clients ask Valve directly | n/a | **toward collection**, silently (O5) |
| Any future mode stored only in the db | db | reverts to the code default | n/a | direction = whatever the default is |

Finding for D22: the only place a lost store silently increases collection is
O5. Every other loss points to protection or to a change the operator made
himself in the env.

## 5. Findings

- **F1 (high, planned work).** D19-1a and D19-1c add browse-triggered lookups to
  a third party (oracle) and to Valve's store host. O1 today polls cached games
  on a timer; O2/O9 would reveal which uncached games a household member looks
  at. D19-1c's own text says a bare `VAULT_MANIFEST_ORACLE` is too weak a gate
  for a different host and party. Gate both behind the D22 mode; ship 1c with
  its own `VAULT_STORE_DETAILS` default off until D22 lands; dedupe and rate
  limit per app id; do not store who opened a card.
- **F2 (medium).** O3/O4 are on and not switchable: the full list of app ids the
  household ever queued goes to Valve in batches of 50, from the WAN IP, and no
  setting stops it except the egress lock. Needs a switch before any `strict`
  claim is true.
- **F3 (medium).** O5: when the cached copy is missing, every viewing device
  contacts Valve's CDN directly, outside the lock. `strict` needs placeholders
  and a tighter CSP.
- **F4 (medium).** One shared key is both the read key for per-PC data (E3, E4)
  and the admin key. Any paired phone can read every other member's PC names,
  addresses and installed lists. D5 (scoped keys) is the structural fix.
- **F5 (medium).** O12: `vault-dns` forwards all household names to Cloudflare
  and Google by default. Not a SteamHangar flow when the router stays the
  resolver, but the shipped default is a third party.
- **F6 (medium).** E5: `client=` in the access log has no off switch.
- **F7 (low).** Relay key stored in clear in the db file.
- **F8 (low, process).** Controls are scattered: 3 env keys for the oracle, 2 for
  the playtime gate, a db key for the library id, an env-only webhook URL, an
  allow list, and two non-switchable flows. A user cannot answer "what leaves
  my house" from one screen. This is what D22 addresses.

Contradictions or overlaps found: `VAULT_MANIFEST_ORACLE=off` removes the
`api.steamcmd.net` allow entry but nothing removes the Valve hosts (baked,
`docker-entrypoint.sh:114`), so "oracle off" does not mean "no app id leaves";
`VAULT_WEBHOOK_URL` is env only, but `webhook_events` and `webhook_timeout_seconds`
are DB-overridable (`settings_store.py:311,317`), and a stored webhook URL is
ignored with a startup warning (`settings_store.py:718`).

## 6. Recommended defaults, summary

1. Keep every flow that carries a SteamID64 or key off until the operator enters
   it (O6, O7).
2. Keep playtime and last played off and env-floored (E1).
3. Make O3/O4 switchable, default on in `balanced`, off in `strict` (F2).
4. Put O2 and O9 behind the mode, never behind `VAULT_MANIFEST_ORACLE` alone (F1).
5. Floor of the mode in env, UI may only move toward protection (ADR-0010).
