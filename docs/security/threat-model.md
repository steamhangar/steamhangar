# SteamHangar — Threat Model

**Verified against commit `234f16c`, 2026-08-18**, with §4 re-verified
against the code as of **WP 4h.0** (not yet its own commit at the time of
that update — see that section for file/line citations into the current
tree instead of a hash). **WP 5.3-fix (a follow-up commit, 2026-08-18)
re-verified every citation into `api/vault_api/routers/steam.py`,
`config.py` and `settings_store.py` against the current tree** — see §4's
own "Citation style note" for what changed there — **and corrected §5's
outbound-flows list**, which had omitted two Android outbound calls it
should have named from the start. **WP 4h.4 (this commit, 2026-08-18)
re-verified every §4/§5 citation into the Android app against the current
tree, following ADR-0004's second addendum**: the app's device-local Steam
Web API key and its direct-to-Valve calls are gone, closing the gap §4
previously flagged against the app and retiring §5's former item 4 to a
historical note (both sections below state the new facts plainly, not as
a diff against the old text) — `app/`'s Kotlin sources now count as
actively developed for this document's own citation-style purposes (see
§4's "Citation style note"), so citations into them added or touched by
this pass use `module::symbol` anchors with a short quote, the same
convention WP 5.3-fix established for the Python files. Every other
citation below was opened and read at `234f16c`. `docs/PROJECT_PLAN.md` in
particular grows under active editing — including this very package's own
tick — so citations into it are given as section-plus-quote anchors, never
line numbers, precisely because a line number into a file that grows is a
claim with a short shelf life. Citations into files that are not actively
growing (source code, other docs) are given as line numbers/ranges, each
verified at the stated commit; re-open them if reading this well after
that date.

This document describes the security posture of SteamHangar *as shipped*, not
as designed or aspired to. Every claim about behaviour below is followed by
the file and line (or section-plus-quote anchor) it was read from. Where a
protection does not exist, that is stated plainly rather than left implicit.
A separate, shorter [`SECURITY.md`](../../SECURITY.md) at the repository
root covers how to report a vulnerability.

This is a homelab project written by a single maintainer, reviewed by AI
review agents rather than a professional security audit. Treat this
document as an honest description of a hobby project's attack surface, not
as a certification.

**The privacy control §4 previously described as "in flight" has landed
(WP 4h.0, ADR-0010), and the gap this note used to flag against the
Android app is now closed too (WP 4h.4, this commit).** §4 below now
describes the shipped gate: two env-only, non-persistable, independent
opt-outs for the Steam relay's `playtime_forever`/`rtime_last_played`
fields, both off by default, each enforced at the relay boundary by
omitting the JSON key entirely (not sending `0`/`null`), with `PATCH
/v1/settings` rejecting attempts to set either. **What has changed since
the previous version of this note:** the gate now covers both frontends,
because the Android app no longer has an independent path to Valve for
library data at all — ADR-0004's second addendum removed the app's
device-local Steam Web API key entirely, so `GET /v1/steam/owned-games`
is the ONLY source of library data for either frontend, with no fallback.
See §4's own note below for the full detail, including what did NOT
change: the app's OpenID identity verification still talks to Valve
directly, but that flow carries no library data, only the identity
assertion.

**§5 gains a network-enforcement column, and §8 a new pinned image, as of
WP EG-1 (this commit, 2026-08-19; `docs/adr/0011-egress-lock.md`).** Every
outbound flow §5 lists was previously a *code-level* fact only ("this
module makes an HTTPS request to this host") — as of this commit, the two
genuinely third-party flows (the manifest oracle, items the operator opts
into) run inside `vault-api`'s own container, which now has **no default
network route for an arbitrary TCP/UDP connection to a WAN or LAN
destination**
(`deploy/compose.yaml`'s `vault-lan`/`vault-egress` networks); reaching
such a destination requires passing through a new, separate `vault-proxy`
container that refuses any destination not in its allowlist. §5's per-item
notes below now state, for each flow, whether it is proxy-gated (and how),
baked into the proxy's own default allowlist unconditionally, or
unaffected because it never left `vault-api`'s process boundary in the
first place. This is a *narrowing* of blast radius, not a new guarantee
about vault-api's own code, and it is deliberately NOT stated as "vault-api
cannot reach the internet" — two channels are measured, working, and
explicitly left open (round-2 review B1/B2, ADR-0011 §1/§4): DNS
*resolution* still succeeds from inside vault-api (Docker's embedded
resolver answers from the host's own namespace, not the container's — a
compromised process can still exfiltrate data one DNS label at a time),
and the Docker host's own reachable addresses — including anything any
container has published on `0.0.0.0` (`vault-core:80` in the shipped
default) — remain directly reachable, because a reply from the host to a
container needs no masquerade. See ADR-0011's own "What this ADR does NOT
claim to defend against" section for both, stated as plainly as this
document's own house style requires, alongside the other named
out-of-scope items (a compromised `vault-proxy` image, an operator who
edits the lock away, `vault-runner`'s deliberately broad egress).

---

## 1. The trust boundary: SteamHangar assumes a trusted LAN

This is the single load-bearing assumption of the whole design, stated
directly in the deployment docs: **`vault-core` (the cache) has no
authentication and cannot have any** — "the Steam client can't present a
credential" (`deploy/README.md:595-601`). The same section states the
consequence in imperative language: "Never port-forward it, never put it
behind a public reverse proxy" (`deploy/README.md:600-601`, echoed in
`docs/PROJECT_PLAN.md` §10's "Remote access" bullet, which states plainly:
"never expose vault-core/port 80").

### What an untrusted device on the same LAN can do today

Reading the actual nginx config (`core/docker/nginx.conf.template`), not the
ADR that describes it:

- **Reach the cache with zero authentication.** `location /depot/` has no
  auth directive of any kind (`core/docker/nginx.conf.template:428-476`).
  Any device that can route a TCP connection to port 80 can `GET` a depot
  chunk it already knows the path for, and get it served — HIT from disk,
  or MISS via a live fetch from Steam's real CDN followed by
  `proxy_store`-ing the result (`core/docker/nginx.conf.template:478-571`).
  There is no `limit_req`/rate limiting anywhere in this config — its
  absence was verified by reading the entire file, not assumed.
- **Use vault-core as a scoped, unauthenticated HTTP relay to Steam's CDN.**
  The one guard on the miss path is the Host-header allowlist
  (`core/docker/nginx.conf.template:145-164`, enforced at
  `core/docker/nginx.conf.template:511-513`): only `*.steamcontent.com` and
  `*.steamserver.net` are ever proxied to. This is explicitly an anti-open-proxy
  guard, not an access-control guard — the config comment names the threat
  it defends against precisely: "what stops this server being usable as a
  generic open HTTP proxy via a forged Host header"
  (`core/docker/nginx.conf.template:146-149`). It does **not** restrict *who*
  may use the cache as a relay to those two host families — it restricts
  *where* the relay can point. Concretely: any LAN device, trusted or not,
  can drive real Steam CDN egress traffic through your server and fill your
  disk with real (large) game content it requests, with no login and no
  rate limit — and the requester picks both ends of that transaction: which
  upstream edge gets contacted (`$host` becomes `$vault_upstream_host`
  verbatim for anything outside the one hardcoded hosts-file fallback,
  `core/docker/nginx.conf.template:177-180`, actually dialed at `:564`) and
  where the response lands on disk (`$vault_store_path` is built from the
  request's own `$uri`, `core/docker/nginx.conf.template:199-200`) — subject
  only to the Host-family allowlist above, nothing scopes *which* depot/path
  under that family gets fetched and stored. This is a real, if not
  especially severe, in-LAN DoS/cost vector against your own disk and
  upstream bandwidth, and nothing in the code defends against it.
- **Reach vault-api if it can guess or capture the API key** — but not
  without one. Every router except health is constructed as
  `APIRouter(dependencies=[Depends(require_api_key)])`
  (`api/vault_api/routers/games.py:18`, `cache.py:29`, `jobs.py:37`,
  `mapping.py:30`, `agent.py:27`, `clients.py:77`, `schedule.py:38`,
  `settings.py:92`, `stats.py:36`, `oracle.py:41`, `steam.py:45` — all
  verified individually), and `main.py` registers every one of those
  routers (`api/vault_api/main.py:236-257`). See §7 for exactly what that
  key does and does not protect.
- **Reach the web UI's static files without a key** (`index.html`,
  `.js`, `.css` — `api/vault_api/webui.py`), because the UI shell itself
  isn't API data; but every API call the UI's JavaScript then makes still
  needs the key, which the UI stores in the browser's own `localStorage`
  (see §7).

### What happens if someone exposes this to the internet — and what stops it

Nothing in the code stops it. `vault-core`'s default bind is `0.0.0.0`
(`deploy/compose.yaml:81`, comment at `deploy/compose.yaml:76-80`: "Default
0.0.0.0 is deliberate and correct HERE and only here… It has no
authentication, by design"). Port-forwarding that address from a home
router to the internet is a configuration action entirely outside
SteamHangar's control — there is no code-level guard (no IP allowlist, no
"refuse non-private source addresses") that would detect or block it. The
only thing standing between "LAN-only by design" and "an open,
unauthenticated Steam-CDN-scoped HTTP relay on the internet" is the
operator's own router/firewall configuration and reading the docs. The same
is true of `vault-api`'s port 8080 (`deploy/compose.yaml:260-266`) — its
authentication is real (§7), but exposing it directly still exposes a
single shared bearer credential with no rate limiting and no brute-force
lockout (see §7) to the entire internet instead of to a LAN.

`vault-dns` (the optional bundled DNS container) is the sharpest version of
this risk, and the compose file itself is unusually blunt about it: the
comment above its port mapping is titled "OPEN-RESOLVER WARNING — THE MOST
DANGEROUS TWO LINES IN THIS FILE" and explains that a wrong bind turns it
into "a DNS amplification/reflection weapon aimed at third parties, using
your bandwidth and your IP's reputation" (`deploy/compose.yaml:336-355`).
The default bind (`127.0.0.1`) fails closed if the operator forgets to set
`VAULT_DNS_BIND` (`deploy/compose.yaml:353-355`), which is a real,
code-level mitigation — but it only protects the *default*; nothing stops
an operator from setting `VAULT_DNS_BIND=0.0.0.0` themselves.

**Public-domain profile is the one documented exception, and only for
`vault-api`.** §10 of the plan (the "Remote access" bullet's "Public domain"
line — "front vault-api with your reverse proxy … TLS required,
forward-auth/OIDC strongly recommended on top of the API key") and the
Android connectivity-profile design allow fronting `vault-api` (never
`vault-core`) with the operator's own TLS reverse proxy. This is advice in
documentation, not a code-enforced requirement — vault-api itself serves
plain HTTP and will not refuse to run without TLS in front of it.

---

## 2. The cache contents

`vault-core` stores Steam depot content path-faithfully under
`cache/depot/<depotid>/...` (`core/docker/nginx.conf.template:63` — `root
cache` under the `-p /vault` prefix, i.e. `/vault/cache/depot/...`). There is
no `autoindex` directive anywhere in the config (verified by reading the
whole file) — a device cannot browse a depot's contents; it can only
request a chunk it already knows the hash/path of, learned in the ordinary
course of that device's own Steam client asking for it. `location ^~ /tmp/ {
return 404; }` (`core/docker/nginx.conf.template:579-581`) additionally
denies the in-flight temp-file path defensively.

### Who can read it

Anyone who can reach the cache (see §1 — on a correctly deployed LAN, that
is every device on the LAN, by design; ADR-0001's whole "works for guests"
value proposition depends on this). Nothing about depot content is
per-user or per-account-gated in `vault-core` itself; it is a shared,
path-keyed store any Steam client on the network benefits from.

### What it reveals about who owns which games

`vault-api`'s depot→app mapping table (`depot_app_map`,
`api/vault_api/db.py:182-190`) plus the on-disk depot tree is exactly the
information "which games are cached" — reachable via `GET /v1/games` and
`GET /v1/cache/summary`, both API-key gated (§7). Raw filesystem/nginx
access without the API key does **not** let a device enumerate "which
games are here" (no directory listing, as above) — but the response for a
HIT and a MISS is served by structurally different code paths (a HIT
resolves inside `location /depot/` via `try_files`; a MISS falls through to
`location @miss`, which round-trips to the real Steam CDN before answering
— `core/docker/nginx.conf.template:428-433, 478-571`), and there is no
cache-status response header that would make the difference explicit: the
only `add_header` directive anywhere in this config is
`X-LanCache-Processed-By` on the heartbeat endpoint
(`core/docker/nginx.conf.template:593`), not on `/depot/`. So a device that
already knows a valid depot/chunk path can plausibly *infer* whether it is
cached by observing response latency — a HIT skipping the upstream
round-trip should be measurably faster than a MISS. This is stated as a
structural near-certainty, not a measured one (see the closing gap list):
this document did not run a timing measurement against it. Severity is low
regardless of which way that measurement would land — it requires the
observer to already know a valid depot/chunk path (which itself requires
either owning the game or having captured that path from someone who
does), and even confirmed it reveals only "is this already cached," not who
cached it or when.

### What the cache is *not*: not a licence bypass

SteamHangar stores exactly the bytes Steam's own CDN serves to a licensed,
authenticated Steam client for the same request — the cache does not
decrypt, re-encrypt, strip protections from, or otherwise transform the
content (there is no code path in this repository that does any such
thing — the manifest-parsing module goes out of its way to note that it
*cannot*: "Filenames inside a cache-stored manifest's PAYLOAD are
Valve-encrypted (need the depot decryption key, which vault-api never
holds)" — `api/README.md:2165-2166`). A device that pulls cached bytes
still needs a genuine, licensed Steam client to make sense of them — the
same as it would need to make sense of a genuine Steam CDN response
outside a cache entirely. Whether that content is independently usable
without Steam's own login/licence checks depends on Steam's own DRM/content
architecture, which is outside this repository and not something this
threat model verifies one way or the other — it is asserted here only that
SteamHangar adds no bypass mechanism of its own.

---

## 3. Credentials — ADR-0004's claim, checked against the code

ADR-0004 (`docs/adr/0004-steam-credentials-never-touch-steamvault.md`)
states that Steam credentials never touch SteamHangar code. This was checked
directly, not taken on faith:

- **`vault_api/auth.py` and every router module** were read in full; none
  of them contains a Steam login/password code path. The only credential
  `vault-api` code handles is the operator's own `VAULT_API_KEY`
  (`api/vault_api/auth.py:16-53`) and, if configured, the opt-in Steam Web
  API relay key (below) — neither is a Steam account password.
- **The one-time interactive login is real, and happens outside vault-api's
  own code.** `deploy/README.md:110-114` documents the actual command an
  operator runs: `docker compose exec -it vault-runner
  /opt/steamprefill/SteamPrefill select-apps` (since WP S-2 the session
  volume and the login live in the dedicated `vault-runner` service, not
  vault-api — `deploy/README.md`'s login section) — this hands the terminal
  directly to SteamPrefill's own login prompt; nothing in `vault_api/*.py`
  is on that call path. `deploy/README.md:105-108` states the resulting
  claim in the docs: "vault-api never sees, stores, transmits or logs Steam
  credentials… and no login ever happens during an image build."

### Where credentials *do* live in a working setup

The password and Steam Guard code are typed once, interactively, into
SteamPrefill's own prompt. The resulting **session** (not the password
itself — this project's own Phase-0 research supports the checkable claim
that what gets persisted afterward is "not raw credentials," `poc/
steamprefill/PROTOCOL.md:176`; see the closing gap list for the stronger
claim this document does *not* make) is written by SteamPrefill into
`/opt/steamprefill/Config`, which `deploy/compose.yaml::vault-runner` (the `vault-steamprefill:/opt/steamprefill/Config` mount — moved off vault-api by WP S-2) mounts from the
named Docker volume `vault-steamprefill`. `deploy/README.md:119-120,
126-127` names this directly: "The session lands in the
`vault-steamprefill` volume at `/opt/steamprefill/Config`… **Treat the
`vault-steamprefill` volume as sensitive. It holds a logged-in Steam
session.**" Concretely: whoever has read access to that Docker volume's
files on the host (root on the Docker host, or anyone who can `docker
cp`/mount it) can act as that logged-in Steam session. This is the single
point where a real, **full-account** Steam credential-equivalent lives at
rest in a SteamHangar deployment — not inside the SQLite database. That
qualifier matters: §3's own next subsection describes a second, narrower
secret (the Steam Web API relay key) that *is* stored in the SQLite
database, in plain text — a real credential, but a revocable, read-scoped
one, not a full account session. The two are not the same category of risk,
and this document should not be read as contradicting itself between them.

### The Android app's identity flow

"Sign in with Steam" is OpenID against Valve's own login page
(ADR-0004 decision 2) — the app never sees a password, only the resulting
SteamID64. This is a separate, weaker-guarantee flow than the server-side
session above: OpenID asserts *identity*, it cannot download content, which
is exactly why the two touchpoints differ (ADR-0004's own framing). This
threat model did not re-verify the Android OpenID implementation itself
(the on-device replay-residual finding at WP 4b.3/4b.7 already covers that
surface in `app/README.md`) — it is out of scope for this package's
`api/`/`core/`/docs footprint.

### The web UI's Steam Web API relay — a second, narrower credential

ADR-0004's addendum (opt-in web relay, `docs/adr/0004-...md:36-59`) adds a
**Steam Web API key** — not a password, a revocable, read-scoped key the
operator generates on Valve's site — stored server-side once the operator
enters it in Settings. Verified in the schema: `steam_relay_key.api_key` is
a plain `TEXT` column with no encryption-at-rest of its own
(`api/vault_api/db.py:523-527`) inside the `vault-db` SQLite file
(`deploy/compose.yaml`'s `vault-db:/data` mount on vault-api). It never appears in a `GET` response body in
full — `GET /v1/steam/key` returns only whether one is configured plus the
last four characters (`api/vault_api/steam_relay.py:35-36`, docstring
verified against the router's actual response shape) — and is cleared from
the in-memory relay cache on every key change
(`api/vault_api/steam_relay.py:85-90`). Its confidentiality at rest
therefore rests entirely on who can read the `vault-db` volume's file on
the host, the same trust boundary as the Steam session above, not on any
in-database encryption.

---

## 4. Personal data — playtime and last-played (new in WP 4h.1)

This is the surface most likely to be missed, and the brief that produced
this document is right to call it out by name.

**Citation style note.** `api/vault_api/routers/steam.py`, `config.py` and
`settings_store.py` are under active development in this same phase — WP
4h.0 alone drifted four of this section's citations by adding 109 lines to
`routers/steam.py` in the same commit that rewrote this prose, on top of a
`settings_readonly` drift the same review round caught in §7. Every
citation into these three files **within this section (§4)** is therefore
a `module::symbol` anchor (function/class/constant name, optionally with a
short verbatim quote) rather than a line number, so it stays greppable
across the next insertion instead of drifting again. Citations into these
same three files elsewhere in this document (§5, §7) were re-verified
against the current tree for this follow-up but are left as line numbers
where they already were — converting every citation in the whole document
is out of this follow-up's footprint; a plain line number is kept there
only because it was re-checked as still accurate, not because the file has
stopped moving (see the top-of-document stamp for the files this document
actually judges to not be actively growing).

### What is stored, and where

Two distinct additions:

1. **Per-depot manifest-change history** (`depot_manifests.first_seen_at`,
   `.manifest_changed_at`, `.observation_count` — schema v14) drives
   `GameSummary`/`GameDetail`'s `manifest_change_frequency`,
   `manifest_observation_days`, and `manifest_days_since_last_change`
   fields, always present in `GET /v1/games`/`GET /v1/games/{appid}`
   (`api/vault_api/routers/games.py:87-105, 132-136`). **This is not
   personal data about a person** — it describes how often *Valve* updates
   a game's depots, derived from vault-api's own observation history, not
   from any household member's behaviour. It is included here only to be
   explicit that it was checked and is a different category from the next
   item.
2. **`playtime_forever` and `rtime_last_played` (Steam's own "last played"
   timestamp), relayed per WP 4h.1.** `playtime_forever` was already
   relayed and validated before WP 4h.1 (`api/vault_api/steam_relay.py:52`,
   `api/vault_api/routers/steam.py::OwnedGameOut.playtime_forever` — the
   field declared `playtime_forever: int = 0`); WP 4h.1 added
   `rtime_last_played` (`api/vault_api/routers/steam.py::
   OwnedGameOut.rtime_last_played` — declared `rtime_last_played: int |
   None = None`, `steam_relay.py:536`). Both are returned by `GET
   /v1/steam/owned-games` (`api/vault_api/routers/steam.py::get_owned_games`,
   the route decorated with path `"/v1/steam/owned-games"`) — **this is
   genuine behaviour data about a specific named Steam identity**, exactly
   the "who played what, and when" fact a household vault should be careful
   with.

### Who can read it via the API

Anyone holding the single `VAULT_API_KEY` (§7 — there is exactly one key,
with no per-endpoint scoping) can call `GET /v1/steam/owned-games` and, IF
both fields are turned on (see below — they are OFF by default), receive
playtime and last-played for whichever single Steam identity the operator
configured the relay against (§3).

**Landed (WP 4h.0, ADR-0010): two independent, env-only, off-by-default
gates.** `VAULT_RELAY_EXPOSE_PLAYTIME`/`VAULT_RELAY_EXPOSE_LAST_PLAYED`
(`api/vault_api/config.py::DEFAULT_RELAY_EXPOSE_PLAYTIME`/
`DEFAULT_RELAY_EXPOSE_LAST_PLAYED` — both `False`) each gate one field,
independently — an operator can allow the aggregate hour count while still
refusing to ever surface the timestamp, or vice versa.
`Settings.relay_expose_playtime`/`relay_expose_last_played`
(`api/vault_api/config.py::Settings`, the two fields of that name) are read
in `api/vault_api/routers/steam.py::get_owned_games` (the line
`expose_playtime=settings.relay_expose_playtime,`) and passed into
`OwnedGameOut` construction ONLY when their setting is on
(`api/vault_api/routers/steam.py::_build_owned_game_out`); that same route's
`response_model_exclude_unset=True` decorator argument
(`api/vault_api/routers/steam.py::get_owned_games`) then omits the JSON key
**entirely** when the corresponding constructor argument was never
passed — not `0`/`null`, which a client could still read as a claim about
the account. Verified at the wire level, not just by reading the code:
`api/tests/test_relay_privacy.py::test_both_fields_are_absent_by_default`
asserts `"playtime_forever" not in game` and `"rtime_last_played" not in
game` against a real HTTP response body, and
`test_response_key_sets_match_the_models_exactly` asserts the full response
key set against the Pydantic models' own field set in all four gate-state
combinations.

**Deliberately NOT persistable, and this is itself a documented trade-off
(ADR-0010), not an oversight.** Both keys join the `PATCH /v1/settings`
env-only allowlist (`api/vault_api/settings_store.py::ENV_ONLY_INFO_KEYS`,
the `relay_expose_playtime`/`relay_expose_last_played` entries) rather than
becoming DB-overridable like `sweep_include_cached`/`auto_gc` — the
`settings` table lives in the `vault-db` Docker volume, which can be lost
independently of the environment (`docker compose down -v`, a rebuild), and
a privacy opt-out whose failure mode is "silently resumes collecting
personal data with the loss of a volume, no notification to anyone" was
judged worse than the real cost this choice accepts: **there is no runtime
opt-out.** Changing either value means editing `deploy/compose.yaml`/`.env`
and restarting the `vault-api` container. `GET /v1/settings` still reports
both as informational, env-only rows so an operator (or a future settings
UI) can see the current state without a switch that would `422`.

**Updated (WP 4h.4, this commit): the gap this note used to describe is
closed — the gate now covers both frontends.** ADR-0004's second addendum
(`docs/adr/0004-steam-credentials-never-touch-steamvault.md`, "Addendum 2")
removed the Android app's device-local Steam Web API key entirely.
`net/steam/VaultRelayLibraryFetcher.kt::VaultRelayLibraryFetcher` — the
app's one production `SteamLibraryFetcher` — calls
`net/VaultApiClient.kt::steamOwnedGames`/`steamPlayerSummaries`, which hit
the SAME `GET /v1/steam/owned-games`/`GET /v1/steam/player-summaries`
routes the web UI already used ("`suspend fun steamOwnedGames(steamId64:
String): OwnedGamesRelayOut = get("/v1/steam/owned-games", …)`" —
`net/VaultApiClient.kt::steamOwnedGames`). Because both frontends now go
through the identical route, `VAULT_RELAY_EXPOSE_PLAYTIME`/`_LAST_PLAYED`
(`api/vault_api/routers/steam.py::_build_owned_game_out`) gate what the
app receives exactly as they already gated the web UI — there is no
longer a second, ungated path for these two fields to reach a device
through.

**There is no fallback to a direct Valve call from the app, and this is
structurally pinned, not merely true today.**
`app/app/src/test/java/dev/steamvault/app/net/SteamKeyIsolationTest.kt`
walks every shipped Kotlin source set (`src/main` AND `src/debug` — the
second scan closed a review-round blind spot) and asserts, by full-text
search, that neither the literal host `api.steampowered.com` nor a
`getSteamWebApiKey`/`setSteamWebApiKey` accessor pair appears anywhere:
"`api.steampowered.com must not appear ANYWHERE in src/main or
src/debug`" (`SteamKeyIsolationTest.kt`, the test's own failure-message
literal). The device-local key is gone from storage, not merely unused:
`storage/CredentialStore.kt::legacyPrefKeysToScrub` runs once at
construction in every `CredentialStore` implementation and removes an
existing install's already-abandoned key. **Precisely what is pinned
where (review round 2 correction — an earlier version of this sentence
overstated this): the JVM-testable fake's copy of this migration is
BEHAVIOURALLY tested** — `InMemoryCredentialStoreTest.kt`'s `MUTATION
PIN -- construction scrubs an existing install's legacy Steam Web API
key` actually constructs a store over seeded data and observes the key
gone — **but `storage/EncryptedCredentialStore.kt`'s real `init` block
and its `clearSteamIdentity`'s restored removal line cannot run on the
JVM at all (no Android Keystore off-device, same constraint this
document's §7 "Storage on each client" already states for this class),
so both are pinned STRUCTURALLY instead**, the same technique this class's
other three guarantees already use:
`EncryptedCredentialStoreSourceTest.kt`'s `calls the shared legacy-key
scrub at construction and on sign-out` reads the class's own source text
and asserts both call sites are present by name — proven to actually
catch a regression: deleting either the `init` block or
`clearSteamIdentity`'s removal line (independently, reviewer-verified)
made that structural test fail, while the full behavioural suite around
it stayed green (577/0 on both variants) because nothing else in the JVM
suite ever constructs a real `EncryptedCredentialStore` or calls its
`clearSteamIdentity()`. And `storage/EncryptedCredentialStore.kt::clearSteamIdentity`
removes the legacy key again on Steam sign-out, belt-and-suspenders. A
credential nobody is ever prompted to revoke is exactly the failure mode
ADR-0010 already names for a different control (§4 above); this WP
applies the same reasoning to a credential instead of a privacy flag.

**What did NOT change.** The app's OpenID identity verification
(`net/steam/SteamOpenIdClient.kt::SteamOpenIdClient.checkAuthentication`)
still POSTs directly to Valve's own login endpoint (`steamcommunity.com`,
§5 item 5 below) — that flow establishes WHO is signing in and carries no
library data; it is unaffected by this change, and §5 states the
distinction explicitly. Also unaffected: the relay's own confidentiality
and storage properties described earlier in §3/§4 — moving the app onto
the relay does not change what the relay key is, where it lives, or who
can call `GET /v1/steam/owned-games` (still: anyone holding the single
`VAULT_API_KEY`, §7) — it only removes the app's SEPARATE, previously
ungated path to the same data.

### For how long

Not stored server-side by vault-api at all for the playtime/last-played
pair — the relay is a live pass-through with a short in-memory TTL cache
(`RelayCache`, a few minutes, `api/vault_api/steam_relay.py:71-83`), never
persisted to the SQLite database and cleared on every key change. Each
`GET` re-fetches (or serves the brief in-memory cache) from Steam directly.
The manifest-change-frequency fields (item 1 above, not personal data) *are*
persisted, in `depot_manifests` (schema v14).

### The operator's stated requirement, checked against shipped code

The operator's privacy stance for this feature (`docs/PROJECT_PLAN.md` §7
Phase 4h, "Privacy stance") is explicit — a household vault "has more than
one person in the living room," so the rule that follows one sentence
later is: "off by default or dismissible at any time, no nagging, and no
number that gets held up to somebody else." Checking this against what
actually shipped:

- **The playtime/last-played consuming UI does not exist yet — but a
  different personal-data field from the same relay already renders.**
  WP 4h.2 (the decision-support panel) and WP 4h.3 (header art) have both
  landed (2026-08-19) — and the panel's statement module structurally
  cannot read `rtime_last_played` and never surfaces a playtime number
  (negative privacy pin, `web/js/lib/decision-support.js`); its
  playtime-derived tier is unreachable in production until a web Steam
  identity exists. The
  only UI surface that touches this relay today is the Settings "Library
  preview" lookup (`web/js/views/settings.js:462-508`), and its render
  function (`renderLookupResult`, `web/js/views/settings.js:368-391`) does
  **not** render `playtime_forever` or `rtime_last_played` anywhere — that
  much is correctly absent. **It does, however, already render a persona
  name and a full SteamID64:** `` `Signed in as ${state.lookup.persona.
  personaname} · SteamID64 ${state.lookup.persona.steamid}` ``
  (`web/js/views/settings.js:376-384`), sourced from a second relay call the
  lookup makes alongside `owned-games` —
  `api.steamPlayerSummaries(steamid)` (`web/js/views/settings.js:489`) hits
  `GET /v1/steam/player-summaries`
  (`api/vault_api/routers/steam.py::get_player_summaries`, the route
  decorated with path `"/v1/steam/player-summaries"`), which returns
  `personaname` plus three avatar image URLs
  (`api/vault_api/routers/steam.py::PlayerSummaryOut`, the
  `personaname`/`avatar`/`avatarmedium`/`avatarfull` fields) for the
  configured identity.
  A persona name and a SteamID64 are themselves personal data — a real
  name in many Steam accounts, and a stable identifier that resolves back
  to a public Steam profile — so "no UI renders personal data from this
  relay yet" is false; the correct, narrower statement is "no UI renders
  the *playtime/last-played* fields yet." The Android app's model
  (`net/model/SteamRelay.kt::OwnedGame`) still carries a `playtime_forever`
  field, gated by the identical server-side switches described above
  (both frontends decode the same `GET /v1/steam/owned-games` response) —
  no UI code under `app/app/src/main/java/dev/steamvault/app/ui/` renders
  it (verified by search — zero matches, unchanged from the previous
  version of this note). **Updated (WP 4h.4, this commit): this exposure
  is no longer web-only.** Android now also calls `GET
  /v1/steam/player-summaries`
  (`net/steam/VaultRelayLibraryFetcher.kt::VaultRelayLibraryFetcher.getPlayerSummary`)
  through the SAME server-side relay the web UI's lookup uses, for the
  same persona-name/SteamID64 purpose (Settings' Steam-identity section,
  `app/README.md` "Steam library via the vault relay") — so both
  frontends now read this exact field through the identical endpoint,
  gated by the identical single `VAULT_API_KEY` (§7), with no separate
  device-local path left on the Android side to be a second, independent
  exposure surface. This does not make the exposure itself any narrower
  (the fact "still renders a persona name and SteamID64, still has no
  suppression control" from the paragraph above remains true for both
  frontends) — it removes a duplicate, ungated copy of it, not the
  exposure.
- **Updated (WP 4h.0): the API-level half of "off by default" has now
  landed, ahead of the display-side half.** The "API answers with the data
  unconditionally to anyone with the key" gap this section used to flag
  (as of `234f16c`) is closed for the two fields the operator's privacy
  stance names: `playtime_forever`/`rtime_last_played` are both off by
  default and independently gated (§4's "Who can read it via the API"
  above). This is a genuinely different, and stronger, guarantee than "no
  display exists yet" — it is enforced at the API itself, so it holds even
  against a direct API consumer (a curl script, a future integration, a
  household member who has the key and knows the endpoint exists), not
  only against a UI that happens not to render the field. What is still
  true, and still worth stating plainly: there is currently no display to
  turn off or dismiss either (WP 4h.2/4h.3 remain unticked), so the
  "dismissible at any time, no nagging" half of the stance has no UI to
  apply to yet — that half of the requirement is `docs/PROJECT_PLAN.md`'s
  own "in flight" item, not this document's to close. **The persona
  name/SteamID64 exposure described in the paragraph above this one is
  UNCHANGED by WP 4h.0** — that data flows through a different endpoint
  (`GET /v1/steam/player-summaries`) which WP 4h.0's gate does not touch,
  and still has no suppression control of any kind. Whoever implements WP 4h.2
  should treat this document's framing as the requirement to design
  against: the "dismissible/no nagging" property belongs to a *display*
  concern, but "who can retrieve it at all" is an
  *API* concern that WP 4h.2 alone cannot fix from the frontend.

### Client identity and network addresses — a second, related personal-data surface

`GET /v1/clients` (`api/vault_api/routers/clients.py:80-155`) returns, per
client, `client_id`, `source_addrs` (a list of IP addresses), and hit/miss
statistics. `client_id` defaults to the reporting machine's own hostname if
the operator does not override it — `agent/README.md:368-372`: "Default
`--client-id`: the local hostname (`os.Hostname()`)…". Home networks
routinely name machines after their owner or its role — the project's own
documented example is `steam-deck-01` (`agent/README.md:945`), and a
personal machine name is at least as plausible a default — so this row is a
hostname-plus-IP-address record of which machine is or is not using the
cache, readable by anyone with the API key. This is the same category of
concern the project's own Phase 6 plan already names for webhook payloads
(`docs/PROJECT_PLAN.md` §7 Phase 6, "Payload scoping per target") — it
applies equally here, today, to `GET /v1/clients`, and has no
payload-scoping mitigation yet (that mitigation is explicitly planned only
for Phase 6 webhooks, not for this existing
endpoint).

---

## 5. Outbound data flows — what leaves the LAN

"What leaves my network" is the first question anyone self-hosting a cache
asks, and it deserves its own section rather than being scattered across
§2-§4. Two flows are the project's own core function and are already
covered in full elsewhere, named here only so this section is not read as
an exhaustive substitute for them: `vault-core` proxying an inbound cache
MISS to Steam's real CDN (§1/§2 — the point of the project, and a HIT never
leaves the LAN at all), and SteamPrefill's own login/prefill traffic to
Valve's servers from inside the `vault-runner` container since WP S-2 — the split that makes vault-api egress-lockable at all (§3 — the whole
reason the project exists, and the one flow that legitimately carries a
real Steam session). **Enforcement (WP EG-1): neither of these two core
flows is proxy-gated, and neither needed to be** — `vault-core` has its own,
separate, already-narrower Host-allowlist mechanism (ADR-0001 req 4), and
`vault-runner` is deliberately excluded from the egress lock's network
topology entirely (ADR-0011's "What this ADR does NOT claim to defend
against" — its egress is Steam's CM/CDN fleet, not an enumerable set a
proxy allowlist could name). Beyond those two, `api/vault_api/oracle.py`'s own
privacy section stakes out a claim worth checking precisely because it is
stated as exhaustive: "Every other component talks only to the LAN, to
Steam's CDN through vault-core, or to Valve through SteamPrefill" — i.e.
nothing else should leave. **As of this commit (WP 4h.4), that claim has
exactly four LIVE exceptions, plus one now-CLOSED historical exception
(item 4 below) kept in this list for the record rather than deleted** —
this section previously listed three exceptions, then five (the two
Android items had been named individually in §3/§4 but left out of this
inventory, corrected by WP 5.3-fix), and now nets to four live ones once
WP 4h.4 closed item 4 — some opt-in, some structural design decisions,
none of them Steam credentials (§3):

1. **The Steam Web API relay (§3, §4).** ADR-0004's addendum states the
   obligation directly: "SECURITY.md documents the added data path: with
   the relay configured, library queries originate from the SERVER (they
   leave the LAN toward Valve), not from the browser"
   (`docs/adr/0004-steam-credentials-never-touch-steamvault.md:57-59`).
   `steam_relay.py`'s own module docstring, "## Privacy" section, says the
   same thing in the same words and names this exact document by name as
   the place that should say it: "this is one of the few things in
   SteamHangar that leaves the LAN — and here it leaves it carrying the
   operator's own Steam Web API key and the SteamID64 being looked up…
   see api/README.md's 'Steam Web API relay' section for the full note
   WP 5.3's threat model is expected to read"
   (`api/vault_api/steam_relay.py:94-100, 109-111` — the quoted sentence's
   own tail sits on :111, not :110). Off by default (no row in
   `steam_relay_key` until the operator enters one, §3); once configured,
   every `GET /v1/steam/owned-games` or `GET /v1/steam/player-summaries`
   call sends the relay key and a SteamID64 to `api.steampowered.com` over
   HTTPS (`api/vault_api/steam_relay.py::STEAM_API_HOST`/`STEAM_API_BASE`
   — pinned as a literal host, not derived from any setting). **As of WP
   4h.4 (this commit), this is also the ONLY such call the Android app
   makes** — see item 4's historical note below and §4's own updated note
   for the full story; the app calls the exact same two routes through
   `net/VaultApiClient.kt::steamOwnedGames`/`steamPlayerSummaries`, not a
   second, device-local path to Valve. **Enforcement (WP EG-1):**
   `api.steampowered.com` is baked into `vault-proxy`'s own image
   unconditionally (`deploy/proxy/docker-entrypoint.sh`) — NOT gated behind
   `VAULT_EGRESS_ALLOW` the way item 2 below is, because the relay key is a
   runtime, DB-stored setting (ADR-0009) that can be turned on with no
   vault-api restart, so there is no boot-time moment at which a
   `VAULT_EGRESS_ALLOW`-based check could reliably know whether this flow
   is "in use" (ADR-0011 §3 has the full argument). The call still leaves
   `vault-api`'s own container through `vault-proxy`, over `HTTP_PROXY`/
   `HTTPS_PROXY` — it is proxy-gated, just permanently allowed rather than
   conditionally.
2. **The optional manifest oracle.** `VAULT_MANIFEST_ORACLE` is off by
   default (`api/vault_api/config.py:92-93`: "the default"); when an
   operator turns it on, `vault-api` sends the Steam **app ids** it tracks
   to a third party over HTTPS — default `api.steamcmd.net`
   (`api/vault_api/oracle.py:98-107`: "this is the one thing in SteamHangar
   that leaves the LAN… carrying the Steam app id it is asking about — i.e.
   which games this vault tracks, and roughly when. No API key, no client
   id, no user identity and no Steam credentials are ever sent"). Worth
   citing precisely because this exact feature is the subject of a
   standing `docs/LEARNINGS.md` finding about this class of bug: an earlier
   packaging pass shipped a `.env.example` line telling operators to "point
   `VAULT_MANIFEST_ORACLE_URL` at your own mirror instead" for privacy,
   while `deploy/compose.yaml` did not yet forward that variable — so an
   operator following the project's *own* privacy advice still shipped
   their cached app ids to the public default with no error
   (`docs/LEARNINGS.md:251-255`). That specific gap is recorded there as
   fixed (the WP that found it also closed it, same commit range), and
   `deploy/compose.yaml:197` now forwards `VAULT_MANIFEST_ORACLE_URL` — cited
   here as a worked example of why this category of claim ("set this env
   var for privacy") must be re-verified against `compose.yaml` on every
   read, not trusted from a comment. **Enforcement (WP EG-1):** proxy-gated
   via `VAULT_EGRESS_ALLOW`, and — unlike item 1 above — genuinely
   conditional: `vault_api/config.py`'s own startup check refuses to boot
   if this oracle is turned on with its configured host (the default
   `api.steamcmd.net`, or an operator's own private mirror) missing from
   that allowlist, rather than booting clean and failing every query
   forever with a filtered-403 and no obvious cause (ADR-0011 §3).
3. **Cover art.** The web UI's Content-Security-Policy allows exactly one
   external image host: `img-src 'self' data: https://cdn.akamai.
   steamstatic.com` (`api/vault_api/webui.py:94`). A browser fetches real Steam art directly from that CDN, by
   appid, from three surfaces: the library grid's capsule art, the detail
   card's mini-cover (WP 4a.4), and the detail card's header art (WP 4h.3
   — one additional request per opened detail, same host, same data
   class), with no vault-api relay in between — the browser's own request,
   not a server-side one, and carrying no vault-api secret (the CSP's
   `connect-src 'self'` — same file, line 96 — is what the API calls
   themselves are bound by; `img-src` is a separate, wider allowance
   specifically for this one host). This leaks "which app ids this browser
   is currently viewing" to that CDN the same way any hotlinked image would
   to any host, at ordinary web scale — named here for completeness, not
   because it is a sharp risk. **Enforcement (WP EG-1): unaffected, by
   construction.** This is the browser's own request, made from the LAN
   client's machine, never from inside `vault-api`'s container — the egress
   lock restricts exactly one container's own outbound traffic, and this
   flow never was that container's traffic to begin with.
4. **CLOSED (WP 4h.4, this commit) — historical: the Android app's own
   direct Steam Web API calls, the same two endpoints as item 1 but from
   the DEVICE, never through vault-api.** Recorded here for the audit
   trail, not as a live flow. Until this commit, `SteamIdentityRepositoryImpl`
   wired a `SteamWebApiClient` as its `libraryFetcher` by default, which
   called `IPlayerService/GetOwnedGames/v1` and
   `ISteamUser/GetPlayerSummaries/v2` against `api.steampowered.com`
   directly, using the device-local, user-owned key ADR-0004 decision 2
   originally specified — never the relay's key, and never proxied
   through vault-api. That class (`net/steam/SteamWebApiClient.kt`) and
   its device-local key (`storage/CredentialStore.kt`'s
   `getSteamWebApiKey`/`setSteamWebApiKey` accessor pair) are DELETED, not
   merely unused, as of this commit (ADR-0004's second addendum) — item 1
   above is now the only route for these two calls from either frontend,
   and `SteamKeyIsolationTest` (cited in §4's updated note) structurally
   pins that neither the old host literal nor the old accessor names can
   silently reappear anywhere in the app's shipped source. An existing
   install's already-abandoned key is actively scrubbed, not left
   orphaned — `storage/CredentialStore.kt::legacyPrefKeysToScrub`, cited
   in §4.
5. **The Android app's OpenID identity verification, to Valve (§3) —
   unaffected by WP 4h.4, and confirmed still live as of this commit.**
   Completing "Sign in with Steam" POSTs the callback's `openid.*`
   parameters back to Valve's login endpoint with
   `openid.mode=check_authentication`
   (`net/steam/SteamOpenIdClient.kt::SteamOpenIdClient.checkAuthentication`,
   whose own kdoc describes exactly this: "POSTs every `openid.*` param
   [...] extracted back to Valve with `openid.mode` overridden to
   check_authentication, per OpenID 2.0") — the step
   that actually proves the deep-link callback was not forged, per OpenID
   2.0. This call carries no Steam Web API key and no vault-api secret,
   only the OpenID assertion Valve itself issued, and it establishes
   *identity* (a SteamID64), never library data — a materially different
   flow from the now-closed item 4, which is why removing item 4 did not
   remove this one: the app still has to establish who is signing in
   against Valve regardless of how (or whether) it fetches library data
   afterward. **Enforcement (WP EG-1): unaffected, by construction** — same
   reasoning as item 3's: this call originates from the Android device
   itself, never from inside `vault-api`'s container, so a lock scoped to
   that one container's own network egress has nothing to do with it.

Beyond the two core flows named above and the four live exceptions just
listed (plus the one now-closed historical exception, item 4), nothing
else in this repository makes an outbound network call as shipped:
agent reports and Android/web-to-API traffic stay LAN-internal by design
(§1), and webhooks (`docs/PROJECT_PLAN.md` §7 Phase 3/Phase 6, out of this
package's own footprint) are opt-in and point at a URL the operator
supplies, not a name this document can pin. **Enforcement (WP EG-1):**
proxy-gated the same way the manifest oracle is, but with no matching
startup check — `VAULT_WEBHOOK_URL` is DB-overridable at runtime (ADR-0009,
`PATCH /v1/settings`), so there is no boot-time value to check it against,
and this project's own "cheapest honest version" standard for a startup
check stops at the one case that is actually checkable (ADR-0011 §3). A
webhook receiver's host — LAN or WAN, no distinction — must be added to
`VAULT_EGRESS_ALLOW` or delivery fails, silently retried and eventually
dropped, exactly the way a receiver that is merely offline already behaves
(`webhooks.py`'s own at-most-once, bounded-retry semantics, unchanged by
this package). Documented plainly, in `deploy/README.md`'s own words: "if
your webhook stops firing after this update, that is the lock working."

---

## 6. The event log (ADR-0008)

`vault-core` can optionally write a second, structured, tab-separated
access log purpose-built for `vault-api` to consume (ADR-0008,
`docs/adr/0008-cache-event-feed.md`). Reading the actual format
(`core/docker/nginx.conf.template:232-388`), each line records: a version
tag, an ISO-8601 timestamp, `$remote_addr` (the direct TCP peer address —
explicitly *not* trusting any `X-Forwarded-For`,
`core/docker/nginx.conf.template:261-263`), HIT/MISS/BYPASS, the depot id
parsed from the URI, the URI path (bounded to 300 characters), bytes sent,
the `Host` header, and the HTTP status code.

**Where it lands:** under `/vault/logs/` inside the same shared Docker
volume both `vault-core` and `vault-api` mount
(`deploy/compose.yaml:277-287`), only when the operator sets
`VAULT_EVENT_LOG`/`VAULT_EVENT_LOG_PATH` (both empty/off by default,
`deploy/compose.yaml:62, 164`). It is off by default in `core/Dockerfile`
per that same comment, though `.env.example` ships it uncommented (i.e.
turned on) as of the packaging work package
(`deploy/compose.yaml:56-58`) — so a fresh deployment following the shipped
`.env.example` has this on, not off.

**Who reads it:** only `vault-api`'s own background sweep
(`api/vault_api/event_sweep.py`), which reads it, never truncates it in the
shipped container layout (a documented, accepted limitation —
`deploy/compose.yaml:175-183`), and turns it into the derived, API-key-gated
summaries at `GET /v1/clients` and `GET /v1/stats`. Nothing serves the raw
log file itself over HTTP. At the filesystem level, whoever can read the
shared Docker volume on the host can read the raw file directly — the same
host-level trust boundary as §3's Steam session and §3's relay key; this
document does not repeat that caveat as a new finding each time it
recurs, because it is one property (host access = full access) applying to
several files, not several independent risks.

**What it is used for, per §1/§4 above:** miss-triggered prefill completion
and the bypass-suspicion determination in §7 — coarse request *facts*
(who asked, HIT or MISS, how many bytes), never used to *derive* the
depot→app content mapping (that comes from Steam manifests per
ADR-0006/0007) — exactly the boundary ADR-0008 draws for itself
(`docs/adr/0008-cache-event-feed.md:54-57`).

---

## 7. The API key, and the read-only / bypass modes

### How authentication actually works

One shared secret, `VAULT_API_KEY`, compared with `hmac.compare_digest`
against the caller's `X-Api-Key` header (`api/vault_api/auth.py:16-53`).
Constant-time comparison defends against a timing side-channel on the
comparison itself; it does **not** provide any rate limiting or
brute-force lockout on repeated wrong guesses — this repository's `auth.py`
and `main.py` were read in full and contain no such mechanism. Nor is any
minimum length or complexity enforced on the key itself: `config.py`
requires only that `VAULT_API_KEY` be non-empty after stripping whitespace
(`api/vault_api/config.py::Settings.from_env`, the check that raises
"VAULT_API_KEY is required and must not be empty.") — an operator who sets
it to a short or
guessable string is not stopped by any code path, only by the `.env.example`
comment recommending `python -c "import secrets;
print(secrets.token_urlsafe(36))"` (`deploy/.env.example:33-36`). Every
router except `health` requires it (§1, verified router-by-router); `GET
/v1/health` is the sole, deliberate exception, returning a fixed
`{"status": "ok"}` body with no data (`api/vault_api/routers/health.py`).
There is no CORS middleware configured anywhere in `vault_api`
(verified by search across the package) — a browser will not send the
custom `X-Api-Key` header to `vault-api` cross-origin without a CORS
preflight response the server never provides, which is an incidental,
browser-enforced mitigation against a malicious third-party web page
silently calling the authenticated API on a LAN user's behalf; this is
standard browser same-origin behaviour, not a SteamHangar-specific control,
and is not a substitute for anything above.

**The key travels in cleartext by default.** `vault-api` serves plain HTTP
(`deploy/compose.yaml:260-266`); nothing enforces TLS unless the operator
puts a reverse proxy in front of it (§1, public-domain profile). On the
default LAN deployment, `X-Api-Key` goes out unencrypted on every request,
same as the rest of the traffic — consistent with, and no worse than, the
LAN-trust assumption of §1, but worth stating rather than leaving implicit
given that this is the one credential that gates the whole control plane.

**Storage on each client:**

- The **web UI** stores the key in the browser's `localStorage`, in plain
  text, keyed `"steamvault.apiKey"` (`web/js/api.js:37, 43, 54, 62, 66`),
  sent back as the `X-Api-Key` header on every request
  (`web/js/api.js:106`). Anyone with script execution in that browser
  origin, or file-level access to that browser profile, can read it. The
  strict Content-Security-Policy the UI ships
  (`api/vault_api/webui.py:90-101, 104-122` — `script-src 'self'`, no
  inline scripts anywhere in `web/`, no third-party JavaScript) is the
  actual mitigation against the most likely way a key like this leaks
  (injected/XSS script reading `localStorage`), not encryption of the
  stored value itself, which does not exist.
- The **Android app** stores it in `EncryptedSharedPreferences`
  (`androidx.security-crypto`), a materially stronger at-rest guarantee
  than the web UI's plain `localStorage` — verified against
  `app/README.md:358, 552, 569` (`EncryptedCredentialStore.kt`, the
  `androidx.security-crypto`-backed implementation). This asymmetry
  between the two frontends is real and is stated here as a fact, not a
  recommendation to fix it (out of scope for this package).

### What "read-only" genuinely prevents

There is exactly one read-only mechanism in the codebase:
`VAULT_SETTINGS_READONLY` (env-only, `api/vault_api/config.py:923, 1186`),
forwarded in the shipped `deploy/compose.yaml` (`vault-api` service,
`deploy/compose.yaml:239`) -- an operator using the shipped stack can
actually set it, not just the underlying env var in isolation.
Reading `routers/settings.py` directly (`api/vault_api/routers/settings.py:95,
116, 189` and the surrounding handler): when set, it makes `PATCH
/v1/settings` answer `403` with a distinct detail message. **It does
nothing else.** It does not disable job creation, prefills, deletion, GC
execution, or any other mutating endpoint — those are unrelated routers with
their own, unrelated dependency chains (§1). A reader who assumes
`VAULT_SETTINGS_READONLY=1` makes a deployment "safe to expose more
broadly" would be wrong: it locks exactly one settings-write endpoint and
nothing about the trust model in §1 changes because of it.

**One more write route that looks like a setting and is NOT under the
lock:** `PUT /v1/steam/key` and `DELETE /v1/steam/key`
(both routes in `api/vault_api/routers/steam.py`) store and clear the Steam Web
API relay key in the database and stay writable with
`VAULT_SETTINGS_READONLY=1` (verified by reading the router: it has no
dependency on `settings_readonly`). This is deliberate for now, not an
oversight (pre-freeze review, WP API-FIX-2): there is no environment
variable for the relay key, so the key has no env source the lock could
"restore pure env semantics" to -- gating those routes would make the relay
unconfigurable in every readonly deployment. Gating them together with a
`VAULT_STEAM_RELAY_KEY` env source is post-release work (ADR-0009
addendum 2026-09-30). Until then, "read-only" means: `PATCH /v1/settings`
is locked; the relay key is not.

### What the bypass banner means

This is unrelated to authentication or "bypass" in the security sense —
worth stating precisely because the word invites the wrong reading.
`client.bypass_suspected` (surfaced in the web UI as a persistent banner,
`web/js/lib/bypass-banner.js:45-47`, and in `GET /v1/clients`,
`api/vault_api/routers/clients.py:106-109`) means: *this client reports
installed Steam games via the agent, but has not been observed at the cache
at all within the configured window* — evidence of a DNS/IPv6/hosts-file
misconfiguration causing that device's Steam traffic to route around the
cache, not evidence of an intruder or an authentication failure. The rule
is deliberately biased toward silence: the module docstring names six
numbered disqualifications (`api/vault_api/routers/clients.py:40-51`), but
`bypass_suspected` itself implements only **five** early-return branches
(`api/vault_api/event_sweep.py:1683-1699`) — its own comment explains why:
"1 + 2: the feed is off, has never swept, or is younger than the window
(both folded into `feed_can_accuse` by the caller)"
(`api/vault_api/event_sweep.py:1683-1684`), so the first two numbered items
collapse into one caller-supplied boolean before this function ever runs.
Six disqualifications, five branches — each remaining branch still a
distinct `return False`, and the result is `False` — never suspected —
whenever the event log is off, the observation window is
young, the client itself has been offline longer than the window, it
reports no installed games, no source address could be correlated, or it
*has* appeared in the log within the window. A reader who sees the banner
should read it as "check your DNS/hosts-file setup for this machine," not
as a security alert about an intruder.

---

## 8. Supply chain

Where this project actually looks disciplined, stated plainly rather than
only listing gaps elsewhere: every image this project builds FROM is
digest-pinned, not just tag-pinned. `core/Dockerfile:20`:
`FROM nginx:1.29.8-alpine3.23@sha256:5616878291a2eed…`; `api/Dockerfile:33`
and `:75` (build stage and runtime stage): the identical
`FROM python:3.13.14-slim-trixie@sha256:bf503bb2243c5aad…` pin, so the
runtime image cannot silently drift from the stage that fetched
SteamPrefill. CI's own third-party actions are SHA-pinned with the version
kept only as a trailing comment (`.github/workflows/*.yml`, e.g.
`uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1`).
The Android Gradle wrapper carries `distributionSha256Sum`
(`app/gradle/wrapper/gradle-wrapper.properties:4`) — the same integrity bar
applied consistently across four different ecosystems (Docker base images,
GitHub Actions, Gradle) rather than in just one. **One honest gap in that
bar, found in WP CI-2's review:** the Android continuous-integration job
restores a Gradle download cache, and on a cache hit the restored wrapper
distribution carries its own `.ok` marker, so `distributionSha256Sum` is
not re-verified; restored dependency artifacts are not re-verified either
(there is no `gradle/verification-metadata.xml`). Both are bounded — writing
that cache requires push access to a branch of this repository, which a fork
pull request does not have — but the checksum is cited above as a live
integrity bar, and continuous integration now has a path that skips it. **WP EG-1 (this commit)
adds a fifth image, `deploy/proxy/Dockerfile`, to this same disciplined
set**: `FROM alpine:3.23.5@sha256:fd791d74b68913cbb…` — deliberately the
identical digest `dns/Dockerfile` already pins (ADR-0011: reusing an
already-vetted base beats adding a second one to independently track), with
its own exact-version package pin (`tinyproxy=1.11.3-r0`) matching the
project's existing convention of pinning package versions alongside the
base image digest that resolved them.

**The one place this project's own pins are loose is its own output.**
`deploy/compose.yaml`'s `vault-core`, `vault-api`, `vault-runner`,
`vault-proxy`, and `vault-dns` service definitions all resolve
`${VAULT_IMAGE_TAG:-0.1.0}`, a mutable tag, not a digest, for the images
this project ships of *itself*.
This is a materially different risk than the base-image pins above (those
guard against a third party's registry serving different bytes under an old
tag; this one is about `steamhangar/vault-api:0.1.0` — or whatever tag a
future release publishes — potentially resolving to different bytes over
time on whatever registry ends up hosting it, once WP 5.5 publishes one).
No registry is published yet (`SECURITY.md` "Supported versions"), so this
is a gap to close before or shortly after that happens, not a live one
today.

---

## 9. What SteamHangar deliberately does not defend against

Named plainly, as out of scope, rather than implied to be covered:

- **Physical access to the host.** Anyone with filesystem access to the
  Docker host (or its volumes) can read the Steam session (§3), the
  relay key (§3), the SQLite database, and the raw cache/event log — all
  of which are unencrypted at rest, by design of this project's chosen
  simplicity (SQLite, no secrets manager, `docs/PROJECT_PLAN.md` §3's
  "single file, no DB container — deliberately simple for easy adoption").
- **A compromised LAN router or a malicious device already on the LAN.**
  §1 is explicit that the entire security model assumes the LAN itself is
  trusted. A device that can perform ARP spoofing, rogue DHCP/DNS, or
  simply sits on the same broadcast domain has the same access any
  legitimate device has (§1) — this is a property of the design, not a
  gap being tracked for a fix.
- **A malicious operator.** Whoever controls the `VAULT_API_KEY`,
  the Docker host, or the `.env` file has full control by construction;
  there is no separation of duties, no audit log resistant to the operator
  themselves, and no multi-user permission model (the project is explicit
  about this even in its Phase 6 roadmap for scoped keys
  (`docs/PROJECT_PLAN.md` §7 Phase 6, "Named, scoped API keys"): "this is
  about BLAST RADIUS, not about identity: it does not make the vault
  multi-user").
- **Denial of service from inside the LAN.** Covered concretely in §1 (the
  unauthenticated relay-and-store path) and §7 (no rate limiting on the API
  key check) — both are real, unmitigated vectors available to any LAN
  device, named here rather than left to be discovered.
- **Vulnerabilities in Valve's Steam infrastructure, or in SteamPrefill**,
  the third-party tool this project subprocess-drives. Both are outside
  this repository's code and this document's scope (see `SECURITY.md`
  "Scope").
- **DNS-based exfiltration from `vault-api`, and direct reach to the
  Docker host's own published ports, both survive the egress lock (WP
  EG-1)** — measured, not assumed (round-2 review B1/B2; the full argument
  is `docs/adr/0011-egress-lock.md`'s "What this ADR does NOT claim to
  defend against"). Neither is a code-level gap in vault-api: DNS
  resolution succeeds because Docker's embedded resolver answers from the
  HOST's own network namespace, not the container's, and a reply from the
  host to a container needs no masquerade either way — both are
  structural properties of the network topology existing at all, not
  omissions this package chose to leave unfixed in application code.

---

## Claims this document could not fully substantiate from code alone

In the interest of the review discipline this package was asked to follow:

- **"Depot content is cryptographically inert without a valid Steam
  license"** is *not* verified against this codebase — this repository
  has no code that decrypts depot content (§2), which is the only claim
  actually checked. Whether Steam's own CDN content is itself
  ciphertext requiring a licensed client's depot key, or merely
  access-controlled at the URL/session level, is a fact about Valve's
  infrastructure, not about SteamHangar, and was deliberately *not*
  asserted as a verified fact in §2 for that reason.
- **The Android OpenID login flow's current security properties** (state
  binding, replay residuals) were not re-verified for this document —
  `app/README.md`'s own WP 4b.3/4b.7 notes already cover that surface in
  detail, and re-deriving it was outside this package's `api/`/`core/`
  footprint.
- **The HIT/MISS timing side channel described in §2** is asserted there as
  a structural near-certainty (different code paths, no cache-status
  response header that would make the difference explicit — the only
  `add_header` directive in the whole config is `X-LanCache-Processed-By`
  on the heartbeat endpoint, `core/docker/nginx.conf.template:593`, not on
  `/depot/`), not as something this document measured. No timing
  measurement was run against a real deployment; §2's claim could be wrong
  if, for example, connection setup or TLS-adjacent overhead outweighs the
  upstream round-trip in practice.
- **The stronger version of §3's credentials claim** — that Steam's login
  handshake itself never hands the raw password back to the calling
  process — is not verified against Valve's actual protocol from this
  repository. What is verified and cited in §3 is the weaker, checkable
  claim this project's own Phase-0 research supports: that what
  SteamPrefill *persists* afterward is "not raw credentials"
  (`poc/steamprefill/PROTOCOL.md:176`). Whether the live handshake ever
  exposes the password in-process before that point is a fact about
  Valve's protocol, not about SteamHangar, and this document does not claim
  to have checked it.
