# ADR-0004: Steam credentials never touch SteamVault code

Date: 2026-08-05
Status: Accepted (binds Phase 4 app design and all server-side tooling)

## Context

SteamVault has two Steam login touchpoints: the server-side prefill
(SteamPrefill needs a real Steam session with the user's licenses to
download depot content) and the Android app (library + covers via the
Steam Web API). Both are trust-critical for an open-source project that
asks homelab users to run it next to their Steam account.

## Decision

1. **Server side:** the Steam session belongs to SteamPrefill alone. Login
   happens once, interactively, in SteamPrefill's own prompt — QR login via
   the Steam Mobile App is the recommended and documented path (the
   password is never typed on the server at all). SteamPrefill persists
   only its refresh token in its own config directory (gitignored; a
   dedicated volume in the container setup). vault-api drives the already
   authenticated CLI with stdin closed and treats "not logged in" as a job
   error with instructions — there is deliberately NO code path that
   accepts, forwards, or stores Steam credentials.
2. **App side (Phase 4):** identity via "Sign in with Steam" (OpenID)
   against Valve's official login page — the app never sees credentials,
   only the resulting SteamID64. Library data is fetched directly from the
   Steam Web API (device-local user-owned API key, stored only on the
   phone), never proxied through vault-api. OpenID cannot replace the
   server-side session (it asserts identity, it cannot download content) —
   that asymmetry is why the two touchpoints differ.
3. **Verifiability:** the community release ships a SECURITY.md section
   ("Where your Steam credentials go: only to Valve") documenting both
   flows and the data paths; no telemetry.

## Addendum (2026-08-09): web UI library relay — user decision A+C

The Steam Web API sends no CORS headers, so the Phase-4a WEB UI (unlike
the native Android app) cannot call `GetOwnedGames` from the browser.
User decision: **option A with option C's input UX** — vault-api gains a
small opt-in relay, and the Web API key is entered by the user in the web
UI's settings, then stored server-side (one-time setup).

Boundaries that keep this inside the spirit of this ADR:

- The relay covers ONLY public-profile read endpoints needed for the
  library grid (`GetOwnedGames`, `GetPlayerSummaries`) — never anything
  that could act on the account.
- What is stored is a revocable, read-scoped **Web API key**, never the
  account password; login still happens on Valve's OpenID page. Decision 1
  (server-side prefill session) is untouched.
- The key is stored like the vault API key is handled elsewhere: never
  logged, never echoed back in full by any endpoint, redacted in job logs.
- The relay is off until a key is configured; the Android app keeps the
  original device-local path (decision 2 stands unmodified for native
  clients).
- SECURITY.md documents the added data path: with the relay configured,
  library queries originate from the SERVER (they leave the LAN toward
  Valve), not from the browser.

## Addendum 2 (2026-08-18): the Android app's library fetch also moves to
## the vault relay — decision 2 superseded for library data (WP 4h.4)

**What decision 2 said, verbatim in spirit:** "Library data is fetched
directly from the Steam Web API (device-local user-owned API key, stored
only on the phone), never proxied through vault-api" — an asymmetry with
the server-side prefill session (decision 1) justified at the time by
"OpenID cannot replace the server-side session... that asymmetry is why
the two touchpoints differ." The first addendum (2026-08-09, four days
later) then gave the WEB UI its own relay path for exactly the same
library data, for a reason specific to browsers (no CORS story for calling
Valve directly) — explicitly leaving native clients on the device-local
path: "the Android app keeps the original device-local path (decision 2
stands unmodified for native clients)."

**Why decision 2 is now superseded for library data, specifically.** Once
the relay existed for the web UI, keeping the app on a SEPARATE, parallel
path stopped being free:

- **The per-user key ask, in the operator's own words.** Every person who
  installs this app — a stranger to the project, in the community-release
  sense docs/PROJECT_PLAN.md's Phase 5 targets — would have had to create
  and paste their OWN Steam Web API key before seeing a library at all.
  That is a heavy trust ask for someone installing an unknown app, for a
  feature (their own game list) that a single operator-owned key already
  serves everyone else on the same vault through the relay.
- **Two egress points instead of one.** With decision 2 unmodified, Steam
  library data would leave the LAN toward Valve from TWO independent
  places (the web relay's server-side call, and each phone's own
  device-local call) instead of one, each with its own attack surface and
  its own place for a privacy control to need enforcing.
- **The privacy gate covered only one of them.** WP 4h.0 (ADR-0010) built
  `VAULT_RELAY_EXPOSE_PLAYTIME`/`VAULT_RELAY_EXPOSE_LAST_PLAYED` as a
  SERVER-side gate on the relay's response — an operator who turns both
  off believes playtime and last-played data no longer leave their vault.
  That belief would have been FALSE for any phone still fetching directly
  from Valve with its own key: the gate would protect the web UI's users
  and quietly not protect the app's, with no way for the operator to tell
  from the outside. A privacy control that only closes one of two doors is
  not the control it claims to be.
- **The audit's private-profile trade-off, recorded as an accepted cost
  (see below) — not a reason to keep the old design, but the honest price
  of retiring it.**

**The decision.** The Android app's library/persona fetch
(`SteamLibraryFetcher`, `SteamIdentityRepository.ownedGames`/
`refreshPersonaName`) now goes exclusively through vault-api's relay
(`GET /v1/steam/owned-games`, `GET /v1/steam/player-summaries`),
authenticated like every other vault-api call the app makes
(`X-Api-Key`) — the SAME two endpoints the web UI already uses.
**Deliberately no fallback to a direct Valve call:** a fallback would make
WP 4h.0's privacy gate bypassable the moment a Steam Web API key sits on a
phone again, and would leave two codepaths (one per source of truth) to
maintain and reason about forever. The device-local key — its entry UI in
onboarding and Settings, its `EncryptedSharedPreferences` storage
(`CredentialStore.getSteamWebApiKey`/`setSteamWebApiKey`), and
`SteamWebApiClient`'s direct-to-`api.steampowered.com` calls — is deleted
outright, not hidden behind a flag; `net/steam/VaultRelayLibraryFetcher.kt`
is the one production implementation of `SteamLibraryFetcher` now.

**The accepted cost, stated plainly (audit requirement): a real regression
against the old design, not a hidden one.** Under decision 2's device-local
key, each user's OWN key saw their OWN library even behind a private Steam
profile — Valve applies its stricter permission checks against the calling
key's own account. The relay uses ONE operator-owned key for every
signed-in user on a given vault, and Valve's `GetOwnedGames` for a
DIFFERENT SteamID than the key's own account answers with nothing at all
(`configured: true, game_count: 0`) unless that profile's game details
happen to be public — the identical wire shape a genuinely empty library
produces, so the two causes cannot be told apart from the response alone.
The app surfaces this honestly (`ui/settings/logic/SteamLibraryStatus.kt`'s
`MaybePrivateOrEmpty` state: "0 games found — your Steam profile's game
details may be private") rather than silently, but it cannot recover the
old guarantee. This is accepted as the cost of the key never leaving the
server at all.

**What does NOT change:**

- **OpenID identity in the app.** "Sign in with Steam" still happens
  entirely in the app, against Valve's own login page
  (`net/steam/SteamOpenIdClient.kt`, `steamcommunity.com`) — the app still
  never sees a password, only the SteamID64 the OpenID assertion resolves
  to. Nothing about how identity is established changed; only the SEPARATE
  question of how library/persona DATA is subsequently fetched did.
- **Decision 1.** The server-side prefill session (SteamPrefill's own
  Steam login, driven by vault-api with stdin closed) is entirely
  untouched by this addendum — it was never part of decision 2's asymmetry
  to begin with.

## Addendum 3 (2026-09-30): where the promised SECURITY.md sections live

Decision 3 above, and the first addendum's last bullet, promise SECURITY.md
sections documenting both credential flows and the relay's added data
path. The community release (WP 5.3) put that material into the threat
model instead, where each claim is checked against the code:
`docs/security/threat-model.md` §3 ("Credentials — ADR-0004's claim,
checked against the code", including the web relay and the Android
identity flow) and §5 ("Outbound data flows — what leaves the LAN").
`SECURITY.md` carries a pointer paragraph to those two sections rather
than a copy. Read the two promises above as fulfilled there.
