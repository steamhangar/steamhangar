# SteamHangar Android app (`app/`)

Kotlin/Compose Android client for SteamHangar (Phase 4b, `docs/PROJECT_PLAN.md`
/ `docs/WORKPACKAGES.md`). This directory is a fully self-contained Gradle
project — its own `settings.gradle.kts`, wrapper, and version catalog — and
does not depend on anything elsewhere in the monorepo at build time. It talks
to vault-api over HTTP only (no shared code with `web/`).

Design source of truth: `docs/design/vault-app-mockup.html` +
`vault-app-mockup-NOTES.md` (frozen, see `docs/WORKPACKAGES.md` Phase 4a
header). The Android app is required to stay visually/conceptually
consistent with the web frontend (`web/`) — same palette hex values, same
status-icon kind names.

## What exists after WP 4b.1

This work package ships the **project skeleton, dark theme, and status-icon
component only** — no networking, no navigation, no real screens. The single
screen in the app (`GalleryScreen`) is a debug artifact that renders every
status-icon kind so the theme and component can be visually verified; it is
not part of the shipped app. Real screens (library, downloads, settings,
navigation) arrive in later work packages (4b.4 onward per
`docs/WORKPACKAGES.md`).

```
app/
├── settings.gradle.kts        # root Gradle settings, includes :app
├── build.gradle.kts           # root build script (plugin declarations only)
├── gradle.properties
├── gradle/
│   ├── libs.versions.toml     # pinned version catalog (see below)
│   └── wrapper/               # committed wrapper (jar included, see .gitignore)
├── gradlew / gradlew.bat
├── local.properties           # NOT committed — see "Toolchain setup" below
└── app/                       # the :app module
    ├── build.gradle.kts
    ├── proguard-rules.pro
    └── src/
        ├── main/
        │   ├── AndroidManifest.xml
        │   ├── java/dev/steamvault/app/
        │   │   ├── MainActivity.kt            # single-activity shell
        │   │   └── ui/
        │   │       ├── theme/                 # Color.kt, Type.kt, Theme.kt
        │   │       ├── status/                # status-icon system (see below)
        │   │       └── gallery/               # debug gallery screen
        │   └── res/                           # strings (English), launcher icon, XML theme
        └── test/java/dev/steamvault/app/ui/status/
            └── StatusIconLogicTest.kt         # JVM unit tests, no device needed
```

## Toolchain setup

This project was built and verified with a pinned local toolchain (no
Android Studio, no emulator available in the dev environment):

- JDK 17 (Temurin 17.0.20)
- Android SDK: platform `android-35`, build-tools `35.0.0`, platform-tools
- Gradle 8.10.2 (the wrapper distribution — see
  `gradle/wrapper/gradle-wrapper.properties`), the `-bin` distribution (not
  `-all` — no bundled sources/docs needed for headless builds), with
  `distributionSha256Sum` pinned against the official checksum published at
  `https://services.gradle.org/distributions/gradle-8.10.2-bin.zip.sha256`
  so a compromised/mismatched mirror fails the download instead of silently
  running an unverified Gradle build

`local.properties` (containing `sdk.dir`) is machine-specific and is **never
committed** (`app/.gitignore`). To regenerate it, create the file at
`app/local.properties` with:

```properties
sdk.dir=/absolute/path/to/your/Android/sdk
```

(On Windows, use double backslashes or forward slashes, e.g.
`sdk.dir=C:\\Users\\you\\AppData\\Local\\Android\\sdk`.) Alternatively set the
`ANDROID_SDK_ROOT` / `ANDROID_HOME` environment variable — either satisfies
AGP's SDK discovery.

## Build & test commands

Run from `app/` (the wrapper, `gradlew`/`gradlew.bat`, lives here):

```bash
./gradlew.bat assembleDebug   # builds app-debug.apk
./gradlew.bat test            # JVM unit tests (debug + release variants)
./gradlew.bat lintDebug       # static analysis gate (see "Quality gate" below)
```

No emulator or physical device is available in this environment, so
verification here is build + JVM unit test + lint only — no instrumented
tests, no manual on-device check. `assembleDebug` produces an installable
APK (`app/build/outputs/apk/debug/app-debug.apk`) that has not been run on a
device.

## Quality gate: AGP lint, not ktlint

This WP picks the **AGP `lint` task** as the static-analysis gate (not
ktlint): it is already wired into every AGP module with no extra plugin,
covers both Kotlin style/correctness *and* Android-resource-level issues
(missing content descriptions, resource-qualifier mistakes, etc.), and needs
no additional toolchain download beyond what is already pinned. `lintDebug`
is configured `warningsAsErrors = true, abortOnError = true` (see
`app/app/build.gradle.kts`) so any new warning fails the build the same way
an error would — this is the intended CI gate shape for a later Phase-5 CI
package.

Two lint checks are deliberately disabled project-wide, not silenced by
accident: `AndroidGradlePluginVersion` and `GradleDependency`. Both just flag
"a newer release exists upstream" — which is true and irrelevant here, since
every dependency version in `gradle/libs.versions.toml` was chosen for
*compatibility with the pinned Gradle 8.10.2 bootstrap*, not for recency
(see the catalog file's header comment for the exact reasoning). Leaving
these checks enabled would fail the build every time upstream ships a
release regardless of whether upgrading here is safe — that is a human
upgrade decision for a future work package, not something lint can resolve.

## Conventions

### String resources

AGP lint's `HardcodedText` check only inspects XML layouts — a plain Kotlin
string literal passed to a Compose `Text(...)` call is invisible to it (see
`GalleryScreen.kt`'s `kind.wireName` for a pre-existing, deliberate example
that always passed `lintDebug`). So this is a HUMAN rule, not a lint-enforced
one — write it down here instead of re-deriving it per work package (WP 4b.4
review fix, after 27 literals had to be triaged one at a time).

**Default: static UI chrome belongs in `strings.xml`.** Screen titles,
button labels, placeholder/empty-state copy, toast text, error-fallback
text — anything a real user reads that isn't itself DATA (a game name, a
computed byte count, a server error detail) — is a string resource,
resolved via `stringResource`/`pluralStringResource` from a `@Composable`
context. This is what every screen before WP 4b.4 already did
(`IdentityScreen.kt`, `GalleryScreen.kt`) and what WP 4b.4's own toasts and
placeholder text were moved to match (`LibraryController`'s toasts were
originally inline Kotlin literals; see `ui/library/LibraryStrings.kt`).

**When the string is only known outside composition** (e.g. inside a
`scope.launch { }` block after a suspend network call returns — job counts,
freed bytes, failure counts), a plain Kotlin class/object CANNOT call
`stringResource` — it is `@Composable`-only. The fix is NOT to fall back to
a literal: define a small interface (`LibraryStrings` is the pattern) with
one method per message, implement it against `android.content.res.Resources`
(which has plain, non-Composable `getString`/`getQuantityString` methods),
and inject it into the plain-Kotlin class the same way `CredentialStore`/
`LibraryPreferences` are injected — so the class stays off-device-testable
against a fake implementation.

**Narrow exception: a verbatim, diffable port of a web module's own
literal.** `BulkPlan.kt`'s button labels/notes and `LibraryFilters.kt`'s
chip labels stay Kotlin string literals, not resources — they are
line-for-line ports of `web/js/lib/bulk-plan.js` / `library-filters.js`'s
own hardcoded strings, and the entire point of porting them verbatim is
that the correspondence can be read directly off two side-by-side literals;
resource indirection would hide that diff. This exception applies ONLY
when BOTH of the following hold, and each qualifying file's kdoc must say
so explicitly:
  1. the string's wording is "whatever the web source already decided", not
     an independent Android UI copy decision;
  2. a test pins the literal by STRING EQUALITY against a hand-transcribed
     expected value (never derived from the constant under test — same
     "literal-vs-literal" rule docs/LEARNINGS.md's Android section already
     requires for wire-format/status-word cross-frontend contracts).
A string that fails either test is static UI chrome and belongs in
`strings.xml`, full stop — "it happens to also appear in a `web/js/lib/`
file" is not by itself a reason to keep it out of resources.

## Versions pinned (`gradle/libs.versions.toml`)

Repo rule (CLAUDE.md / `docs/LEARNINGS.md`): pinned versions only, no
dynamic ranges anywhere.

| Component | Version | Why |
|---|---|---|
| Gradle (wrapper) | 8.10.2 | given bootstrap for this WP |
| Android Gradle Plugin | 8.7.3 | latest 8.7.x patch; AGP 8.7 requires Gradle 8.9+, comfortably under the 8.10.2 wrapper |
| Kotlin | 2.0.21 | latest 2.0.x patch; pairs with the Compose compiler Gradle plugin of the same version |
| Compose compiler plugin | 2.0.21 (= Kotlin version, required) | `org.jetbrains.kotlin.plugin.compose` must match the Kotlin version it compiles with |
| Compose BOM | 2024.10.01 | contemporaneous with Kotlin 2.0.21 / AGP 8.7.3 — a newer BOM risks needing AGP/compileSdk features this toolchain combination doesn't have |
| compileSdk / targetSdk | 35 | per WP brief |
| minSdk | 26 | per WP brief (also the exact API level `ValueAnimator.areAnimatorsEnabled()` — the reduced-motion signal — has been available since) |
| Java | 17 | matches the pinned JDK |

## Provisional decisions (not final)

- **Application id**: `dev.steamvault.app`. This is a placeholder —
  final naming (and therefore the id, since it cannot be changed later
  without a fresh Play/F-Droid listing) is a user/release decision per
  `docs/WORKPACKAGES.md` Phase 5, not an engineering one made here.
- **Launcher icon**: a plain generic vault-shield vector glyph (accent
  colour, no Steam trade dress — matches the mockup's "no Valve/Steam
  logos... the app mark is a generic vault shield" rule). Not a final app
  icon; exists only so the app installs and launches with *something*.

## Security note: `android:allowBackup="false"`

Set from this first work package, even though nothing sensitive is stored
yet. A later WP (4b.2, the API client) will store a vault-api key in this
app's private storage; Android's default Auto Backup
(`android:allowBackup="true"`) would copy that key into the user's cloud
backup and restore it onto any device that later signs into the same
account — an off-device leak of a credential the user typed in specifically
to talk to their own homelab server. Setting it correctly now avoids a
silent regression the day the key is added.

## Theme (`ui/theme/`)

`Color.kt` ports every hex value from `web/css/theme.css`'s `:root` palette
byte-for-byte (see the file's own comments for the `--custom-property`
mapping). The app is committed to a single dark theme on purpose — the same
design decision as the web frontend (mockup-notes.md "Committed to dark, on
purpose") — so `Theme.kt` does **not** read `isSystemInDarkTheme()`; the
scheme is always the same regardless of the device's system theme setting.

`Type.kt` covers typography basics only. The mockup's type stack (Roboto /
Roboto Condensed / Roboto Mono, no bundled webfonts) is approximated with
`FontFamily.Default` (renders as system Roboto on every targeted device) and
`FontFamily.Monospace` for the numeric/id role; a real Roboto
Condensed/Mono asset pair is deferred to whichever later WP first needs
capsule-art typography (4b.4, the library grid) — not needed by this WP's
one debug screen.

## Status-icon system (`ui/status/`)

Ports `web/js/components/status-icon.js` + the `.sic` rules in
`web/css/theme.css`:

- **`StatusKind.kt`** — the eleven kinds (`cached`, `running`, `updating`,
  `stale`, `none`, `paused`, `verify`, `error`, `warn`, `cancelled`, and
  `notinuse`, ported in WP API-FIX-4 for the Steam tool-app card: dash
  glyph, muted disc), 1:1 with web's `STATUS_LABEL` keys (`wireName`;
  web's `unknown` is not ported yet), each with an English string
  resource for the label. `fromWireName()` falls back to `NONE` for an
  unrecognized kind, mirroring the web component's own fallback.
- **`StatusIconLogic.kt`** — pure, Android-framework-free functions:
  `glyphFor(kind)` (kind → glyph shape), `backgroundFor(kind)` /
  `inkFor(kind)` (colours), and the two functions the WP brief specifically
  asks for as testable pure logic:
  - **`shouldAnimate(kind, animatorsEnabled)`** — the animate-or-not
    decision. Only `running`/`updating`/`verify` ever animate, and only
    when `animatorsEnabled` is true.
  - ~~`downloadDriftFraction` / `downloadOpacityFraction`~~ — removed in
    WP APP-FIX-3: the running download glyph now "falls through" (two
    arrows one `DOWNLOAD_FALL_PERIOD` apart, clipped to the disc, no fade);
    the geometry is `downloadFallOffset`/`downloadArrowOffsets` plus the
    measuring helpers, see "Android parity (WP APP-FEAT-2)" below.
- **`AnimatorsEnabled.kt`** — documents and implements *how Compose picks
  up the system reduced-motion setting* (see below).
- **`StatusIcon.kt`** — the Compose composable, drawing each glyph on a
  `Canvas` from the same 24×24-unit coordinate data as the SVG paths in
  status-icon.js. CHECK/DOWNLOAD/BANG/PAUSE/STOP are a direct
  coordinate-for-coordinate port. **REFRESH is a documented geometric
  approximation** (two circular arcs + chevrons rather than porting SVG
  elliptical-arc flag math into Compose's `Path.addArc`) — see the
  composable's kdoc. It preserves the design intent ("two opposing curved
  arrows forming one circle") without being pixel-identical to the SVG.

### How reduced motion is honoured

Compose's own animation APIs (`InfiniteTransition`, `animate*AsState`, …) do
**not** automatically respect the platform's "Remove animations"
accessibility toggle or Settings → Developer options → Animator duration
scale — those only gate the legacy `android.animation` framework
automatically. The documented way for a Compose app to see the same signal
is `ValueAnimator.areAnimatorsEnabled()` (public since API 26 — this app's
exact minSdk), which reads `Settings.Global.ANIMATOR_DURATION_SCALE` and
returns `false` when the scale is 0 (the value both the toggle and the
developer option write).

`AnimatorsEnabled.kt`'s `rememberAnimatorsEnabled()` wraps that check in a
live Compose `State`, updated via a `ContentObserver` on the backing
`Settings.Global` URI, so toggling the setting while a screen is open
updates the icons immediately. The actual go/no-go decision is the pure
`shouldAnimate()` function — the composable is just where the live boolean
comes from.

The disable path is proven in `StatusIconLogicTest.kt` without a device: the
tests pin that `running`/`updating`/`verify` all return `false` from
`shouldAnimate` when `animatorsEnabled = false`, alongside pinning that they
return `true` when it's `true`, and that every other kind never animates
either way — the fail-closed-direction discipline from
`docs/LEARNINGS.md` ("Testing discipline": pin the default direction, not
just the happy path).

## Tests (WP 4b.1: status-icon system)

`app/app/src/test/java/dev/steamvault/app/ui/status/StatusIconLogicTest.kt`
— 25 JVM unit tests, no Robolectric/emulator dependency, covering:

- icon-kind → glyph mapping (every `StatusKind`, pinned by name)
- wire-name round trip + unknown-kind fallback to `none`
- the animate-or-not decision in both directions, including the
  reduced-motion disable path, for every kind
- the download glyph's drift/opacity keyframe math at its boundary values,
  including the "must never reach fully transparent" invariant (mockup
  round 7: "a status icon must never be blank" — it doubles as a tap
  target in later WPs)

Verified command + output tail (debug + release variants both run under
`test`):

```
$ ./gradlew.bat test
...
BUILD SUCCESSFUL in 7s
45 actionable tasks: 20 executed, 25 up-to-date
```

`app/build/test-results/testDebugUnitTest/TEST-dev.steamvault.app.ui.status.StatusIconLogicTest.xml`
and the `testReleaseUnitTest` counterpart both report
`tests="25" skipped="0" failures="0" errors="0"`.

```
$ ./gradlew.bat assembleDebug
...
BUILD SUCCESSFUL in 5s
35 actionable tasks: 7 executed, 28 up-to-date
```

```
$ ./gradlew.bat lintDebug
...
BUILD SUCCESSFUL in 32s
26 actionable tasks: 26 executed
```
(`app/app/build/reports/lint-results-debug.txt`: "No issues found.")

## What WP 4b.1 deliberately did NOT do

- ~~No networking, no vault-api client~~ — done in WP 4b.2, below.
- No real navigation / bottom nav / multiple destinations (later 4b.x WPs).
- No library/downloads/settings screens — only the debug gallery.
- No instrumented (on-device) tests — no emulator/device is available in
  this environment; verification is build + JVM unit test + lint only.
- No release signing config (WP 4b.9).
- REFRESH glyph is a geometric approximation of the SVG source, not an
  exact port (documented above and in `StatusIcon.kt`'s kdoc) — worth a
  visual check once a device/emulator is available.

## API client, connectivity profiles, credential storage (WP 4b.2)

Serial after 4b.1 per `docs/WORKPACKAGES.md` Phase 4b. Adds the app's
entire vault-api HTTP surface, still with no UI consuming it yet (that
starts at 4b.3/4b.4/4b.5) — everything below is a library the next work
packages build screens on top of.

```
app/app/src/main/java/dev/steamvault/app/
├── net/
│   ├── VaultJson.kt                # the one kotlinx.serialization Json instance
│   ├── VaultApiClient.kt           # the OkHttp client, one suspend fun per endpoint
│   ├── model/                      # DTOs mirroring vault_api's Pydantic models verbatim
│   │   ├── Health.kt Games.kt Jobs.kt Cache.kt Clients.kt Settings.kt
│   ├── error/
│   │   └── VaultApiError.kt        # the six-kind error taxonomy (sealed class)
│   └── profile/
│       ├── ConnectivityProfile.kt          # the interface + SystemVpnProfile + PublicDomainProfile
│       └── CleartextPolicyInterceptor.kt   # second, OkHttp-level cleartext gate
├── storage/
│   ├── CredentialStore.kt          # the interface (+ ProfileKind constants)
│   └── EncryptedCredentialStore.kt # the real, EncryptedSharedPreferences-backed impl
├── repo/
│   ├── GamesRepository.kt JobsRepository.kt ClientsRepository.kt
└── polling/
    ├── PollingIntervals.kt         # pure "how often should the app poll" decisions
    └── Backoff.kt                  # pure exponential-backoff-with-jitter math
```

### API client (`net/`)

`VaultApiClient` wraps the `/v1` surface the app's later work packages need:
games incl. detail, jobs + control (prefill/cancel/pause/resume), cache
summary/delete, gc, clients, settings GET/PATCH, and health. Every method
is a one-line `suspend fun` — see the class kdoc in
`VaultApiClient.kt` for the full list and for what is DELIBERATELY not
wrapped:

- **`/v1/mapping`** — no current caller (same "add it with the WP that
  needs it" rule `web/js/api.js` documents for the web client).
- **`/v1/steam/*`** (the Steam Web API relay) is now WRAPPED
  (`steamOwnedGames`/`steamPlayerSummaries`), as of WP 4h.4 — **superseding
  this bullet's own WP 4b.2-era claim that it was excluded on purpose.**
  ADR-0004's second addendum removed the Android app's device-local Steam
  Web API key entirely: the relay these two methods call is the ONLY path
  this app has to library/persona data now, the same two endpoints the web
  UI already used. See "Steam library via the vault relay (WP 4h.4)" below
  for the full story.

`X-Api-Key` is attached to every request, including `/v1/health` — the
same choice `web/js/api.js`'s `request()` makes, rather than special-casing
the one route `api/README.md` documents as unauthenticated.

### DTOs (`net/model/`)

One `@Serializable` data class per Pydantic response/request model in
`vault_api/routers/*.py` (read at git HEAD for this WP), with field names
kept **verbatim** — snake_case, matching the wire JSON exactly, no
camelCase renaming layer — so a payload can be compared against
`api/README.md`'s "Endpoints" table or the router source without a mental
mapping step (the same decision `web/js/api.js` documents for the web
client). `VaultJson` sets `ignoreUnknownKeys = true` **deliberately**: the
apps/jobs schema has grown fields release over release (v4 through v13 in
`api/README.md`'s own history), and this client must not hard-fail the
day a future `api/` work package adds one more — every field this client
doesn't itself need has a Kotlin default so an older or newer server both
decode fine (pinned by `SerializationRoundTripTest`'s explicit "unknown
future field is ignored" case). `encodeDefaults = true` is the matching
decision on the way OUT — see `VaultJson.kt`'s kdoc for why (a
`GcRequest(execute = false)` would otherwise encode as `{}`, relying on
vault-api's own default happening to agree).

`GET`/`PATCH /v1/settings` (ADR-0009) is the one genuinely heterogeneous
shape: `effective`/`fallback` are a string for most keys, an int for the
two schedule-numeric keys, a JSON array of strings for `webhook_events`,
or `null`. `SettingInfoOut` models those two fields as
`kotlinx.serialization.json.JsonElement` rather than picking one scalar
type — `Settings.kt`'s `settingAsStringOrNull`/`settingAsIntOrNull`/
`settingAsBooleanOrNull`/`settingAsStringListOrNull` give typed access
without every call site re-deriving the same `when`.

### The six-kind error taxonomy (`net/error/VaultApiError.kt`)

A `sealed class` with one subclass per kind — `Network`, `Auth`,
`NotFound`, `Validation`, `Server`, `Unknown` — carrying the SAME kind
names `web/js/errors.js`'s `ERROR_KINDS` uses (`network`, `auth`,
`not_found`, `validation`, `server`, `unknown`), including the same
`classifyHttpStatus` boundary choices (401 → auth, 404 → not_found, ≥500 →
server, ≥400 → validation — so 409/422 both fold into `validation`, same
as the web client — else → unknown). `VaultApiErrorTaxonomyContractTest`
pins the kind names against hand-transcribed literals, never derived from
the enum itself (`docs/LEARNINGS.md` "Android (Phase 4b)": "a derived
round-trip is circular and cannot detect drift from the other frontend" —
same technique `StatusIconCrossFrontendContractTest` uses for
`StatusKind`).

### Connectivity profiles and the cleartext policy (`net/profile/`)

One interface, `ConnectivityProfile` (a base URL plus whether cleartext
HTTP is allowed), with two implementations now:

- **`SystemVpnProfile`** — the OS routes this directly (LAN or a
  Tailscale/VPN interface); plain HTTP to whatever IP/hostname the user
  entered is accepted, since there is no public CA-signed certificate
  story for a private address. First profile per
  `docs/WORKPACKAGES.md` Phase 4b ("System-VPN profile first").
- **`PublicDomainProfile`** — HTTPS only. Constructing one with an
  `http://` base URL throws `CleartextNotAllowedException` **at
  construction**, before any `Request` object exists.
  **The certificate must be publicly trusted** (Let's Encrypt, `tailscale
  cert`, any CA in Android's system store): the app's
  `res/xml/network_security_config.xml` does not add user-installed or
  private CAs to the trust anchors, so a vault behind a self-signed or
  private-CA certificate fails the TLS handshake — and that failure
  currently surfaces as the generic "Could not reach the server" message
  (an `SSLException` is an `IOException` and lands in
  `VaultApiError.Network`, WP APP-FIX-1 S6, wording not yet split). If
  the vault is on a private CA, use the System-VPN profile over the VPN
  instead of the Public-domain profile.

`tsnet` (an embedded userspace Tailscale client) is explicitly **post-v1**
(`docs/WORKPACKAGES.md` Phase 4b) — no dependency, no stub class, just the
interface seam a future `TsnetProfile` would implement.

**The cleartext tradeoff, stated plainly:** Android's Network Security
Config can only scope a cleartext exception by exact domain/wildcard
(`<domain-config>`), never by IP range — so it cannot express "cleartext
only for whatever LAN address the user typed in". `res/xml/
network_security_config.xml` therefore ships a blanket
`cleartextTrafficPermitted="true"` `<base-config>` (with a scoped
`tools:ignore="InsecureBaseConfiguration"`, not a project-wide lint
disable), and the real "only `SystemVpnProfile` may actually use it"
restriction is enforced ONE LAYER UP, in application code.

**BLOCKER fix (Opus review round 1).** The first version of this WP got
this wrong: it registered `CleartextPolicyInterceptor` ONLY as an OkHttp
application interceptor and claimed — falsely — that this alone would
catch a redirect to an `http://` `Location`. Empirically demonstrated
against the pinned OkHttp 4.12.0: an application interceptor wraps an
entire call and runs exactly once, seeing only the ORIGINAL request —
OkHttp's own `RetryAndFollowUpInterceptor` follows redirects internally,
beneath that layer, so it never saw a redirect's target at all. Combined
with `X-Api-Key` not being an `Authorization`-class header (so OkHttp does
NOT strip it on a host/scheme change) and `followSslRedirects` defaulting
to `true`, a `PublicDomainProfile` client would have silently followed an
`https://` response's `Location: http://attacker` redirect and sent the
API key in cleartext. Fixed with three independent, stacked layers, all
applied unconditionally on `VaultApiClient`'s own wrapping `OkHttpClient`
(so an injected `OkHttpClient` — tests, or a future caller — cannot lose
any of them by construction):

1. **`followSslRedirects(false)`** — refuse to auto-follow an https<->http
   redirect at all; OkHttp then never builds the second request in the
   first place. Primary fix for the https-to-http case specifically.
2. **`PublicDomainProfile`'s constructor guard** (above) — the pre-socket
   gate for the ORIGINAL request, before any HTTP machinery exists.
3. **`CleartextPolicyInterceptor` registered TWICE** — once as an
   application interceptor (same pre-socket coverage as (2), for the
   original request) AND once as a NETWORK interceptor
   (`addNetworkInterceptor`), which runs once per actual request OkHttp
   puts on the wire, INCLUDING every redirect hop and any other
   OkHttp-internal follow-up (e.g. an auth-challenge retry) that likewise
   skips application interceptors. This is the layer that does not depend
   on (1) staying correctly configured forever.

`ConnectivityProfileTest` pins both `PublicDomainProfile`'s guard and the
interceptor (exercised directly against a fake `Interceptor.Chain`,
proving `chain.proceed()` — the call that would open a socket — is never
reached for a blocked request), including the brief's named case verbatim:
"`PublicDomainProfile` + `http://` URL must throw before any socket I/O".
`VaultApiClientTest`'s `https to http redirect never reaches hop 2 for
PublicDomainProfile` is the end-to-end pin the review asked for: two real
`MockWebServer`s (hop 1 HTTPS via `okhttp-tls`'s `HeldCertificate`/
`HandshakeCertificates`, hop 2 plain HTTP), hop 1 answers with a `302` to
hop 2, and the test asserts hop 2's request count stays exactly `0` (with
a bounded `takeRequest` as a second check) — the canary API key
(`apiKeyProvider`) therefore never has anything hop 2 recorded to appear
in. One environment-specific snag worth recording: `MockWebServer.url()`
derives its host from a reverse DNS lookup of the loopback address, which
on this project's dev machine resolves to `lancache.steamcontent.com` (the
lancache DNS override `core/vault-core`'s own PoC relies on) rather than
`localhost` — the test builds both hop URLs explicitly against
`localhost:<port>` instead of trusting `.url()`'s host.

**Delta-review fixes (Opus review round 2 — S1/S2/S3).** The round-1 fix
above was still incomplete, and its own test coverage was weaker than it
looked:

- **S2 (security).** `followSslRedirects(false)` only refuses a SCHEME
  change (https<->http) — an https-to-https redirect to a DIFFERENT HOST
  is a same-scheme redirect that flag does not touch, and `X-Api-Key`
  would still be forwarded to it (still not an `Authorization`-class
  header OkHttp strips on a host change). Fixed with `.followRedirects(false)`
  alongside `.followSslRedirects(false)`, in both `defaultOkHttpClient()`
  and `VaultApiClient`'s own re-applying wrapper — no redirect is ever a
  legitimate outcome for this client's fixed `/v1/...` paths, same-scheme
  or not. Pinned by `VaultApiClientTest`'s `S2 -- https to https CROSS-HOST
  redirect` test: two HTTPS `MockWebServer`s on different ports (same test
  certificate, since TLS SANs are hostname-only), hop 1 redirects to hop 2,
  hop 2's request count stays `0`.
- **S1 (test rigor).** The round-1 end-to-end test could not actually tell
  the flag layer and the interceptor layer apart: against that ONE
  scenario, either layer alone is independently sufficient, so removing
  either one (leaving the other) still passes it — a claim that both
  layers are individually pinned would have been wrong. Fixed with two
  standalone tests: `S1a` builds a client carrying ONLY
  `CleartextPolicyInterceptor` (as a network interceptor) with BOTH
  redirect flags explicitly left at OkHttp's insecure default (`true`),
  proving the interceptor alone blocks the downgrade (hop 2 never records
  a request, even though a raw TCP connection may open — network
  interceptors run after connection setup but before any HTTP bytes are
  written, see the test's own comment for exactly what that does and does
  not prove); `S1b` inspects `VaultApiClient.debugHttpClientForTesting`
  (an `internal` test-only accessor) and asserts `followRedirects`/
  `followSslRedirects` are actually `false` on the built client — a pure
  configuration check, no network involved, that also finally exercises
  `defaultOkHttpClient()` directly rather than always overriding it.
- **S3 (test hygiene).** All four redirect tests now wrap their
  `MockWebServer` pairs in `try`/`finally` — a failing assertion no longer
  leaks two listening servers for the rest of the test JVM fork's
  lifetime. A shared private `TlsFixture` helper (one `HeldCertificate` +
  server/client `HandshakeCertificates`) also replaced three copies of the
  same certificate-setup boilerplate.

### Credential storage (`storage/`)

`CredentialStore` is the interface (API key, base URL, connectivity-profile
kind); `EncryptedCredentialStore` is the real implementation, backed by
`androidx.security-crypto`'s `EncryptedSharedPreferences` +
`MasterKey.Builder` — the vault-api key never lands in a plain,
unencrypted `SharedPreferences` file (the WP 4b.1 backup-posture note:
`allowBackup="false"` is already in place for exactly this class of
secret).

The interface is extracted specifically so tests run on the JVM: an
`InMemoryCredentialStore` fake (test sources only, a plain `Map`) is what
everything depending on `CredentialStore` is tested against.
`EncryptedCredentialStore` itself needs a real Android Keystore, which
this environment does not have (no emulator/device — unchanged from
WP 4b.1). Its one guarantee is pinned STRUCTURALLY instead:
`EncryptedCredentialStoreSourceTest` reads `EncryptedCredentialStore.kt`'s
own source text (the same lightweight technique
`StatusIconCrossFrontendContractTest` uses for `strings.xml`/`colors.xml`,
applied to a `.kt` file) and asserts no bare, plain preferences lookup
appears anywhere in it — a regression that "fixed" an
`EncryptedSharedPreferences.create` failure by silently falling back to
plaintext (a real, documented historical footgun with this API) fails
this test immediately, without needing a device. This is an honest,
narrower guarantee than a runtime test would give, stated as such rather
than hidden.

### Polling primitives (`polling/`) — decisions only, no scheduler yet

`PollingIntervals` and `Backoff`/`BackoffState` are direct, pure ports of
`web/js/store.js`'s `hasActiveJob`/`nextJobsIntervalMs`/
`DEFAULT_INTERVALS` and `web/js/backoff.js`'s `computeBackoffDelay`/
`createBackoffState` — same numbers (2 s/15 s/15 s/20 s cadence, 1 s
base / 30 s cap / 20% jitter backoff), so the Android app polls on the
same cadence the web UI does. **WorkManager wiring — the thing that
actually calls these on a schedule and respects Doze — is WP 4b.8, not
this WP.** `GamesRepository`/`JobsRepository`/`ClientsRepository` are the
thin suspend-based seams that future wiring calls through; they exist now
so 4b.4/4b.5/4b.3 have something to build view-models against without
waiting on 4b.8.

### Versions pinned for this WP

Added to the existing `gradle/libs.versions.toml` table:

| Component | Version | Why |
|---|---|---|
| kotlinx-serialization-json | 1.7.3 | latest patch contemporaneous with Kotlin 2.0.21 (K2 plugin model) |
| kotlinx-coroutines-core | 1.9.0 | same reasoning, contemporaneous with Kotlin 2.0.21 |
| OkHttp | 4.12.0 | current stable 4.x line (5.x still pre-release at the time of this WP) |
| OkHttp MockWebServer | 4.12.0 | test-only, pinned to the same version as OkHttp itself |
| OkHttp TLS (`okhttp-tls`) | 4.12.0 | test-only, same version again — added in the Opus review round for the redirect-leak pin's real HTTPS `MockWebServer` (`HeldCertificate`/`HandshakeCertificates`) |
| androidx.security:security-crypto | 1.1.0-alpha06 | the `MasterKey` builder API (1.0.0's `MasterKeys` helper is deprecated); no stable 1.1.0 GA exists yet — a documented, narrow exception to "stable only" |

### Tests (WP 4b.2)

94 new JVM unit tests (124 total with WP 4b.1's 30 —
`StatusIconLogicTest`'s 25 plus `StatusIconCrossFrontendContractTest`'s 5),
no
Robolectric/emulator dependency:

- `net/error/VaultApiErrorTaxonomyContractTest` — the six kind names
  pinned literally, `classifyHttpStatus` boundaries in both directions
  (401/404/409/422/5xx/sub-400).
- `net/SerializationRoundTripTest` — one round trip per DTO, modeled on
  `api/README.md`'s documented shapes (synthetic data — see the file's own
  header for why no single README curl transcript covers every current
  field after the v4→v13 schema history), plus the "unknown field is
  ignored" case. Each fixture is ALSO decoded through a test-only strict
  `Json { ignoreUnknownKeys = false }` alongside production `VaultJson`
  (Opus review should-fix: `ignoreUnknownKeys = true` in production would
  otherwise let a typo'd fixture key silently vanish instead of failing
  the anti-drift check it's supposed to be) — see the file's class kdoc
  for exactly what that strict pass does and does NOT catch (a fixture
  that OMITS a field with a Kotlin default is absorbed by design either
  way). `JobControlOut`'s anchor test lifts its fixture verbatim from
  `api/tests/test_job_control.py`'s own asserted response body.
- `net/model/SettingValueTest` — the `JsonElement` typed-access helpers,
  including the "numeric content is not a string" trap the first version
  of `settingAsStringListOrNull` got wrong (fixed before this report; see
  the function's inline comment).
- `net/VaultApiClientTest` — MockWebServer-backed: headers, method/path,
  request-body encoding (incl. the dry-run-by-default GC body and a
  mixed set+clear settings PATCH), error mapping for
  401/404/409/422/500 plus a genuine connection failure for `network`,
  and four redirect-leak pins (BLOCKER B1 + delta S1/S2, see "Connectivity
  profiles" above): the https-to-http end-to-end pin, the https-to-https
  cross-host pin (S2), the interceptor-alone pin (S1a), and the
  redirect-flags configuration assertion (S1b).
- `net/profile/ConnectivityProfileTest` — both cleartext-policy layers,
  including the brief's named "throws before any socket I/O" case.
- `polling/BackoffTest` / `polling/PollingIntervalsTest` — growth/cap/
  jitter math both directions, and the fast/slow cadence decision. The
  jitter-floor test now uses `jitterRatio=1.5` (mirroring
  `web/tests/backoff.test.js`'s own load-bearing case exactly), not the
  original `jitterRatio=1.0` version, which the Opus review found was
  VACUOUS — it lands on exactly `0` whether or not the `max(0.0, ...)`
  floor in `Backoff.kt` runs at all, so deleting that floor still passed
  every test in the file.
- `storage/InMemoryCredentialStoreTest` — the fake's own contract.
- `storage/EncryptedCredentialStoreSourceTest` — the structural pin
  described above.

Verified command + output tail (from-scratch `clean test lintDebug`):

```
$ ./gradlew.bat clean test lintDebug
...
BUILD SUCCESSFUL in 1m
56 actionable tasks: 56 executed
```

`124 tests completed, 0 failed` across all 11 test classes (verified via
the `testDebugUnitTest` XML reports' `tests=`/`failures=`/`errors=`
attributes, summed: 124/0/0); `app/app/build/reports/lint-results-debug.txt`:
"No issues found."

### What WP 4b.2 deliberately did NOT do

- **No WorkManager / background polling scheduler.** `polling/` is pure
  decision functions only — WP 4b.8.
- **No UI.** Nothing in `app/app/src/main/java/.../ui/` consumes any of
  this yet — the debug gallery screen is unchanged.
- **No `/v1/mapping` or `/v1/steam/*` client methods** — true as of WP
  4b.2; see the "API client" section above for why both were deliberate
  exclusions at the time, and its own updated note for why `/v1/steam/*`
  stopped being one in WP 4h.4 (`/v1/mapping` remains unwrapped — no
  caller needed it yet).
- **No `tsnet` profile, no dependency, no stub class** — post-v1 per
  `docs/WORKPACKAGES.md`; only the `ConnectivityProfile` seam exists.
- **No instrumented test of `EncryptedCredentialStore`** — no
  emulator/device available; its one guarantee is pinned structurally
  instead (see "Credential storage" above), an explicitly narrower bar
  than a runtime test would clear.
- **No settings/profile picker UI** to choose between `SystemVpnProfile`
  and `PublicDomainProfile` or to write into `CredentialStore` — later
  onboarding/settings work (4b.7).

## Steam identity — OpenID + GetOwnedGames on-device (WP 4b.3)

**Superseded in part by WP 4h.4 ("Steam library via the vault relay",
near the end of this file) — read that section for what is ACTUALLY
shipped today.** Everything below this point describes WP 4b.3 as it
shipped at the time: OpenID sign-in still works exactly as documented
here, but the on-device `GetOwnedGames`/`GetPlayerSummaries` call this
section describes — and the device-local Steam Web API key that drove it
— was deleted outright in WP 4h.4 (ADR-0004's second addendum). This
section is kept for its still-accurate OpenID content and as the
historical record of the design WP 4h.4 replaced, not as a description of
what ships now.

Branch-parallel after 4b.2 per `docs/WORKPACKAGES.md`. Implements
ADR-0004 decision 2 end to end on the Android side: "Sign in with Steam"
resolves to a SteamID64 without this app ever seeing a password, and the
user's own Steam Web API key (entered manually, never obtained via OpenID)
drives an on-device `GetOwnedGames` call that never touches vault-api.
**(WP 4h.4: the second half of that sentence — the device-local key and
the on-device call — no longer describes shipped behavior; see below.)**

```
app/app/src/main/java/dev/steamvault/app/
├── net/
│   ├── steam/
│   │   ├── SteamId64.kt                # SteamID64 validator (mirrors web/api)
│   │   ├── SteamOpenIdLoginUrl.kt      # SteamOpenIdConfig + checkid_setup URL builder
│   │   ├── SteamOpenIdCallback.kt      # callback parsing + signed-fields check (pure)
│   │   ├── SteamOpenIdClient.kt        # check_authentication network call
│   │   └── SteamWebApiClient.kt        # GetOwnedGames / GetPlayerSummaries + SteamWebApiError
│   └── model/
│       └── SteamWebApi.kt              # OwnedGame/SteamPersona + hostile-fixture parsers
├── repo/
│   └── SteamIdentityRepository.kt      # login state, sign-in/out, library count preview
└── ui/identity/
    └── IdentityScreen.kt               # sign-in / signed-in + sign-out + count preview
```

(This tree reflects the layout AS OF WP 4b.3. `IdentityScreen.kt` was later
moved to `app/app/src/debug/java/dev/steamvault/app/ui/identity/
IdentityScreen.kt` in WP 4b.9, once WP 4b.7 had made it unreachable from
the UI — see this file's own "Carry-over cleanup (WP 4b.9)" section below
for why and its current, correct path.)

`storage/CredentialStore.kt` gained three fields (`steamId64`,
`steamPersonaName`, `steamWebApiKey`) plus `clearSteamIdentity()` — a
narrower sign-out than `clear()`, which stays reserved for "forget this
vault entirely" (untouched by signing out of Steam).

### OpenID login flow

1. `MainActivity` builds the `checkid_setup` URL
   (`SteamIdentityRepository.buildLoginUrl()` → `SteamOpenIdLoginUrl.build()`)
   and opens it in an `androidx.browser` Custom Tab.
2. Valve redirects the Custom Tab back to this app's own custom scheme
   (`return_to`/`intent-filter` design below); `MainActivity.onNewIntent`
   receives it and hands the raw callback URL to
   `SteamIdentityRepository.completeLogin`.
3. `SteamOpenIdCallback.parse` extracts the full `openid.*` parameter map
   (shape checks only — this proves nothing about authenticity, since any
   app can send SteamHangar an `Intent` naming this scheme).
4. `SteamOpenIdCallback.signedCoversClaimedId` checks that
   `openid.signed` actually lists `claimed_id` — the OpenID 2.0
   requirement that is easy to skip if a caller stops at a bare
   `is_valid:true`.
5. `SteamOpenIdClient.checkAuthentication` POSTs every extracted param
   back to `https://steamcommunity.com/openid/login` with
   `openid.mode=check_authentication` and requires a **strict, exact**
   `is_valid:true` line in the response — this is the step that actually
   proves the callback was not forged.
6. `SteamOpenIdCallback.steamId64From` extracts and validates the
   SteamID64 out of `openid.claimed_id` (`SteamId64.validate` — 17 ASCII
   decimal digits, individual-account range). Only on success is anything
   persisted (`CredentialStore.setSteamId64`).

### `return_to`/intent-filter design (WP brief: "pick a scheme, document it")

**Chose a custom `steamvault://` scheme, not an `https://` return_to.**
SteamHangar has no hosted web presence — `vault-api` lives on the user's
LAN/VPN, never a public domain by default — so there is no HTTPS endpoint
this app could register as a redirect target. A custom-scheme deep link is
the standard native-app OpenID/OAuth pattern for exactly this situation.

- `RETURN_TO = REALM = "steamvault://auth/openid-return"` (`realm` equals
  `return_to` exactly — an explicitly permitted degenerate case of OpenID
  2.0's realm-matching rules, since this app has no wildcard-subdomain
  need).
- `AndroidManifest.xml`'s `MainActivity` carries a second intent-filter:
  `action=VIEW`, `category=DEFAULT,BROWSABLE`,
  `data android:scheme="steamvault" android:host="auth" android:path="/openid-return"`,
  plus `android:launchMode="singleTask"` so the redirect lands in
  `onNewIntent` on the app's existing instance instead of spawning a
  second one.
- **Honest caveat, stated in `SteamOpenIdConfig`'s kdoc too:** this
  environment cannot confirm empirically that Valve's login page accepts
  and correctly redirects to a `steamvault://` URL rather than rejecting
  or mangling it — that is squarely the "on-device only" item in the
  verification list below. If it turns out Valve rejects the scheme, the
  fix is narrow (swap the constants) and touches nothing downstream of
  `SteamOpenIdCallback.parse`.

### OpenID verification hardening

Same OkHttp security posture WP 4b.2's `VaultApiClient` established
(`docs/LEARNINGS.md` "Android (Phase 4b)"): `followSslRedirects(false)` +
`followRedirects(false)` (no redirect is ever legitimate for a fixed,
literal endpoint), HTTPS only, a bounded response read
(`BufferedSource.request(n)`, not a single `read()` call — a single
`read()` can return fewer bytes than available even when more remain, so
only `request` actually bounds the FULL body), and a strict, exact
`is_valid:true` line match (`isValidTrueStrict` — never a substring
check, so a garbage document that happens to contain that text elsewhere
is not accepted).

**Signed-fields check, scope stated honestly (WP brief; widened in WP
APP-FIX-1 S4a).** `signedCoversClaimedId` checks that `claimed_id` is a
member of `openid.signed` (the sole source of the persisted SteamID64),
and `signedCoversReturnTo` checks the same for `return_to` — since WP
4b.7 that field carries the per-login `state` the replay defence trusts,
so it is the second field this app branches on for a security decision
and an unsigned copy of it proves nothing (the earlier wording here,
"this app never branches on `return_to`'s value", was true before 4b.7
and false after it; a callback whose signed list omits `return_to` is now
rejected before `check_authentication`, pinned in
`SteamOpenIdCallbackTest` and `SteamIdentityRepositoryTest`). It still
deliberately does NOT require `response_nonce`/`op_endpoint`/`identity`
to be signed: this app never reads those fields' values for anything
security-relevant, unlike a fully general OpenID relying party.

### Residual narrowed by WP 4b.7: request↔callback binding (replay) — one class remains

**Original finding (WP 4b.3 review), kept for the record.**
`buildLoginUrl()`/`SteamOpenIdConfig.RETURN_TO` carried no per-login `state`
parameter, and `completeLogin` never checked the callback's
`openid.return_to` against the specific URL THIS login attempt built.
Concretely: a malicious app already installed on the same device could
capture a **genuine**, Valve-signed OpenID assertion for the ATTACKER's own
Steam account (e.g. by triggering its own sign-in against this app's exact
`return_to` scheme+host+path, since nothing was per-attempt-unique) and
replay it into SteamHangar's intent-filter — `check_authentication` would
legitimately return `is_valid:true` (it IS a genuine, unmodified Valve
assertion, just not the one THIS app's button press initiated), and the app
would flip its displayed identity to the attacker's account.

**Blast radius, stated precisely (unchanged by the fix, for context):** this
could only ever *replace which Steam account is shown as signed in* on the
victim's device — it could not forge a claim for an account the attacker
does not themselves control, could not touch vault-api (ADR-0004 decision
2's isolation holds regardless), and leaked no secret (there is no secret in
this flow to leak). Calling `signOut()` fully recovered.

**The fix (WP 4b.7).** `net/steam/SteamLoginState.kt` adds a CSPRNG-backed,
per-login random `state` token (`SteamLoginState.generate`, 192 bits,
URL-safe base64) and a single-use holder (`PendingLoginState`).
`SteamIdentityRepositoryImpl.buildLoginUrl()` generates one fresh `state`
per call, records it as "pending" BEFORE building the URL, and embeds it in
`openid.return_to` (`"$RETURN_TO?state=$state"`). `completeLogin` extracts
the `state` back out of the callback's echoed `openid.return_to`
(`SteamOpenIdCallback.stateFromReturnTo`) and calls
`PendingLoginState.consume(callbackState)` — which ALWAYS clears the
pending value, matched or not — **before** `signedCoversClaimedId` and
before the `check_authentication` network call, so a missing/wrong/replayed
state is rejected without ever reaching Valve. `consume()`'s single-use
semantics is what actually closes the residual: even a byte-for-byte replay
of the exact same, previously-successful callback URL now fails, because
the state it carries was already consumed by the first `completeLogin`
call. Pinned by `SteamLoginStateTest` (CSPRNG output shape, single-use in
both the matched and mismatched case), `SteamOpenIdCallbackTest`
(`stateFromReturnTo` parsing), and `SteamIdentityRepositoryTest`'s "a
consumed state cannot be replayed" mutation pin.

**A deliberate trade, worth stating plainly: single-use also means a junk
callback burns the pending attempt.** Because `consume()` clears the
pending state on EVERY call regardless of match (that is what makes replay
impossible), any deep link hitting `steamvault://auth/openid-return` while a
login is pending — a malicious probe, a stray duplicate Intent, a genuinely
malformed redirect — consumes the one legitimate attempt along with it; the
user simply has to tap "Sign in with Steam" again. This is known, accepted
behaviour (fail closed, cheap to retry, no security cost), not a bug —
`MainActivity.handleIntent` deliberately routes even an unroutable callback
through `identityRepository.completeLogin` for exactly this reason (review
fix N2), rather than leaving a stale pending value that a LATER attacker
window could still target.

**What the `state` binding does NOT close (WP APP-FIX-1 S4b — the earlier
heading said "FIXED"; "narrowed" is what is true).** Custom-scheme intent
filters are unverifiable on every Android version (`docs/LEARNINGS.md`,
"Android (Phase 4b)"): a second app that registers the same
`steamvault://auth/openid-return` filter is offered by Android's chooser
when Valve's page redirects, and if the user picks it, that app receives
the GENUINE callback — including this attempt's `state`. The state binding
cannot tell that apart from SteamHangar receiving it, because the value is
correct; it was simply delivered to the wrong recipient, and that app can
forward it to SteamHangar or keep it. Blast radius, unchanged from the
original finding: which Steam account is displayed as signed in and
therefore which PUBLIC Steam library the vault relay is asked for — no
secret exists in this flow to leak (the OpenID assertion carries no
credential), and the vault-api key is never involved. Closing this class
would need a verified `https://` App Link `return_to` (a domain the
operator controls) instead of a custom scheme, which is a deployment
decision, not an app-side fix; recorded here rather than claimed away.
`signOut()` recovers fully.

### Steam Web API on-device (`SteamWebApiClient`) — REMOVED in WP 4h.4

**Everything in this subsection describes a class and a data flow that no
longer exist.** Kept as the historical record of what WP 4b.3 shipped and
WP 4h.4 deleted outright (ADR-0004's second addendum) — not a description
of shipped behavior. See "Steam library via the vault relay (WP 4h.4)"
near the end of this file for what replaced it.

`GetOwnedGames` (`IPlayerService/GetOwnedGames/v1`, `include_appinfo=1`)
and `GetPlayerSummaries` (`ISteamUser/GetPlayerSummaries/v2`, for the
optional persona name), using the device-local key from
`CredentialStore.getSteamWebApiKey()` — **never** vault-api's own key,
and never sent to vault-api at all (ADR-0004 decision 2).
`SteamKeyIsolationTest` was the grep-provable pin: it read
`VaultApiClient.kt`'s source text and asserted it never referenced
`getSteamWebApiKey`, `SteamWebApiClient`, `SteamOpenIdClient`, or
`SteamIdentityRepository`. **(WP 4h.4: that test file still exists, but
every invariant above it is gone — `getSteamWebApiKey`/
`setSteamWebApiKey` no longer exist ANYWHERE, and `VaultApiClient.kt` now
correctly DOES reference the Steam relay routes; see its updated kdoc for
the current, narrower invariant it actually pins.)**

Same security posture as above (host pinned to Valve's Web API domain,
HTTPS only, no redirects, bounded 2 MiB read). **Key redaction**
(mirroring `api/vault_api/steam_relay.py`'s `_redacted_url` discipline,
read at HEAD as the reference): every error path built its
`SteamWebApiError` message from a fixed literal plus, at most, an HTTP
status code or an exception CLASS NAME — `e.message` from a caught
`IOException` was never interpolated (some `IOException` subtypes can
embed connection details), so the key — which lived only in the request
query string — could never reach a log line or an exception message.
`SteamWebApiClientTest`'s three `MUTATION PIN` tests (also deleted in WP
4h.4, along with the class they tested) planted a canary key and asserted
it was absent from the network-failure, non-2xx, and oversized-body
exception messages.

`net/model/SteamWebApi.kt`'s `parseOwnedGames`/`parsePlayerSummary`
(deleted in WP 4h.4; superseded by `net/model/SteamRelay.kt`'s plain
`@Serializable` DTOs, decoded through `VaultJson` like everything else in
that package) mirrored `api/vault_api/steam_relay.py::parse_owned_games`'s
tolerant shape: a malformed individual entry (wrong type, boolean
masquerading as an int/appid, oversized string) was skipped, not fatal; a
document with no usable `response` object raised `SteamWebApiError`; a
`SerializationException` or `StackOverflowError` from a hostile/deeply-
nested body was caught and converted rather than escaping as a raw
exception type.

### Data layer (`SteamIdentityRepository`)

**WP 4h.4 note:** `hasWebApiKey` below no longer exists on
`SteamIdentityState` — see "Steam library via the vault relay (WP 4h.4)"
for the current shape.

`SteamIdentityState(steamId64, personaName, hasWebApiKey)` is read fresh
from `CredentialStore` on every call. `completeLogin` never throws — every
failure (malformed callback, unsigned `claimed_id`, a rejected assertion,
an invalid SteamID64) becomes `SteamLoginResult.Failure` with a fixed,
secret-free reason string. `ownedGamesCountPreview()` returns a
`Result<Int>` (game COUNT only — the brief's explicit boundary: "library
fetch happens in 4b.4, expose the repository, render a count preview
only"); `refreshPersonaName()` is best-effort and requires both a
signed-in state and a configured key. `signOut()` calls
`CredentialStore.clearSteamIdentity()` — pinned to clear exactly the three
Steam fields and leave the vault connection (`apiKey`/`baseUrl`/`profileKind`)
untouched.

`SteamOpenIdVerifier`/`SteamLibraryFetcher` are the two seams
`SteamIdentityRepositoryImpl` depends on (implemented by `SteamOpenIdClient`/
`SteamWebApiClient` in production) — extracted purely so
`SteamIdentityRepositoryTest` can fake the network entirely and exercise
every branch (malformed callback / unsigned claimed_id / rejected
assertion / invalid SteamID64 / missing key / fetcher failure) on the JVM.

### UI (`ui/identity/IdentityScreen.kt`)

Minimal, per the brief: a sign-in button when signed out; steamid +
persona (or "not loaded yet") + a "check library size" button + sign-out
when signed in. The library size is a COUNT only (a `pluralStringResource`
sentence), never a rendered grid — the real library grid is WP 4b.4's job.
`MainActivity` now shows this screen in place of WP 4b.1's debug gallery
(`GalleryScreen.kt` still compiles and is still covered by its own tests,
just no longer wired into `MainActivity`) — flagged as an expected
reconciliation point for whichever later WP (4b.4/4b.5/4b.7, also
branch-parallel and also wanting to wire a screen into this
single-activity shell) introduces real navigation.

### Versions pinned for this WP

Added to `gradle/libs.versions.toml`:

| Component | Version | Why |
|---|---|---|
| androidx.browser | 1.8.0 | current stable release; used only for `CustomTabsIntent` to launch Valve's login page. No other new runtime dependency — verification and the Steam Web API calls reuse the already-pinned OkHttp 4.12.0. |

### Tests (WP 4b.3)

94 new JVM unit tests (218 total with WP 4b.1/4b.2's 124), no
Robolectric/emulator dependency:

- `net/steam/SteamId64Test` (12) — literal boundary fixtures shared with
  `web/tests/steamid.test.js`/`api/tests/test_steam_relay.py` (base/max/
  real-shaped/length/sign-character/whitespace/non-ASCII-digit/zeros).
- `net/steam/SteamOpenIdLoginUrlTest` (3) — the literal expected
  `checkid_setup` URL (measured empirically: OkHttp's
  `addQueryParameter` percent-encodes `:`/`/` inside a query value, which
  this file's kdoc records as a "verify, don't assume" case per
  `docs/LEARNINGS.md`), the mode-literal mutation pin, and a custom
  return_to/realm case.
- `net/steam/SteamOpenIdCallbackTest` (17) — parse (well-formed, stray
  param ignored, no query string, `cancel` mode rejected, each required
  field individually missing, base64 `=` padding preserved, malformed
  percent-encoding, duplicate key), `signedCoversClaimedId` (present/
  absent/empty/substring-trap), `steamId64From` (valid, wrong host, extra
  path segment, invalid tail, empty tail).
- `net/steam/SteamOpenIdClientTest` (12) — MockWebServer: `is_valid`
  true/false/garbage/empty/non-2xx, an oversized body cut off before the
  `is_valid:true` line, network failure, redirect refusal (reusing the
  WP 4b.2 `TlsFixture` pattern), the mode-override-to-check_authentication
  pin, the redirect-flags configuration pin (S1b), the host-pin literal
  test, and `isValidTrueStrict`'s own mutation-pinned exact-match cases.
- `net/steam/SteamWebApiClientTest` (9) — the host/path literal pin,
  successful `GetOwnedGames`/`GetPlayerSummaries` round trips, an empty
  library, and the three explicit key-redaction `MUTATION PIN` tests
  (network failure / non-2xx / oversized body).
- `net/model/SteamWebApiParsingTest` (22) — hostile fixtures: missing/
  zero/negative/boolean/string appid, boolean/negative playtime, non-object
  entries, `games` not a list, name/icon truncation, the `MAX_GAMES` bound,
  no-usable-`response` / non-object / non-JSON documents, and
  `parsePlayerSummary`'s steamid cross-check + persona truncation.
- `repo/SteamIdentityRepositoryTest` (16) — the full `completeLogin`
  branch set against fakes (success, malformed callback, unsigned
  claimed_id, rejected assertion, invalid SteamID64), `ownedGamesCountPreview`/
  `refreshPersonaName`'s missing-state paths, and `signOut`'s
  scoped-clear pin.
- `net/SteamKeyIsolationTest` (2) — the grep-provable structural pin.
- `storage/InMemoryCredentialStoreTest` gained 1 more test
  (`clearSteamIdentity` scoped-clear) on top of the existing four, updated
  to also cover the three new fields.

Mutation-verify targets named in the brief, each with an explicit test:
**host pin** (`hostPin` tests in both new client test files, literal
strings, never derived from the class's own constants), **is_valid strict
parse** (`isValidTrueStrict`'s own test plus the MockWebServer `is_valid`
variants), **steamid range** (`SteamId64Test`'s base/max/length mutation
pins), **key-redaction** (`SteamWebApiClientTest`'s three canary-key
tests).

Verified command + output tail:

```
$ ./gradlew.bat test lintDebug assembleDebug
...
BUILD SUCCESSFUL in 12s
74 actionable tasks: 33 executed, 41 up-to-date
```

218/0/0 across both `testDebugUnitTest` and `testReleaseUnitTest` (summed
from the XML reports' `tests=`/`failures=`/`errors=` attributes);
`app/app/build/reports/lint-results-debug.txt`: "No issues found."

### What is verifiable only on-device (honest list for the user's device test)

This environment has no emulator/device and cannot open a real browser —
everything below is exercised via MockWebServer/fakes up to the network
boundary, but the following need a real phone + real Steam account before
they can be called confirmed:

1. **Whether Valve's OpenID login page actually accepts and redirects to
   the `steamvault://auth/openid-return` custom scheme at all** (the
   "Honest caveat" above) — if Valve rejects or mangles it, sign-in will
   visibly fail to return to the app.
2. **Whether the Custom Tab correctly hands the redirect to
   `MainActivity.onNewIntent`** (manifest intent-filter matching,
   `launchMode="singleTask"` behaviour) — this is Android OS/Custom Tab
   plumbing this environment cannot instantiate.
3. **The real `check_authentication` round trip against the genuine
   `steamcommunity.com`** — the MockWebServer tests prove the CLIENT's
   logic against every shape of response, but never actually call Valve.
4. **REMOVED in WP 4h.4 — do not test this.** This item used to ask for
   the on-device `GetOwnedGames`/`GetPlayerSummaries` round trip against
   `api.steampowered.com` with a user-entered Steam Web API key via
   `setWebApiKey()`/`SteamWebApiKeyInput.kt`. None of that exists any
   more: the app never talks to Valve's Web API and has no key field
   (`SteamKeyIsolationTest` pins both absences). The replacement device
   items are 4a–4d below (WP APP-FIX-1 S3); the design is in "Steam
   library via the vault relay, superseding on-device GetOwnedGames
   (WP 4h.4)".
   - **4a. Relay happy path.** Signed in with Steam, vault-api has
     `STEAM_WEB_API_KEY` set, the Steam profile's "Game details" are
     public: Settings → Steam identity → "Check library" must show the
     `settings_steam_library_count` plural ("%1$d games found", e.g.
     "12 games found"), and the Library
     tab must merge owned-but-not-cached games into the grid.
   - **4b. `409` — relay key not configured on vault-api.** Same, but the
     operator has NOT set a key: the status line must read the
     `settings_steam_library_not_configured` text ("The vault owner hasn't
     set up a Steam Web API key yet …"), never a generic error, and the
     Library tab must still show the vault-only view without complaint.
   - **4c. `422` — rejected SteamID64.** Only reachable by corrupting the
     stored steamid (not from the UI); if it ever appears, the line must
     read `settings_steam_library_invalid_steamid` ("… try signing in
     again"). Low priority, listed for completeness.
   - **4d. Zero games (private profile OR genuinely empty).** Set "Game
     details" to Private on the test account and check again: the status
     must read `settings_steam_library_maybe_private` ("0 games found. Your
     Steam profile's game details may be private …") — the app cannot
     tell the two causes apart and must not pretend to (see the
     private-profile trade-off in the WP 4h.4 section).
5. **REMOVED in WP 4h.4.** `IdentityScreen` was deleted (it was already
   unreachable since WP 4b.7). The identity UI to look at on a real
   screen is onboarding step 2 and Settings' Steam-identity section.
5b. **Rotate the phone after a completed sign-in (WP APP-FIX-1 S2/P1).**
   Start "Sign in with Steam", rotate while Valve's page is in front,
   complete the sign-in, then rotate again once back in the app: the
   identity must stay signed in and no second "This sign-in link has
   expired or was already used" message may appear. Before this WP the
   first rotation emptied the pending `state` (per-Activity holder) and
   the second re-delivered the consumed callback from the Activity's
   Intent; both are fixed in code but only a device shows the OS actually
   recreates the Activity in those two windows.
6. **Watch for a malformed/rejected sign-in caused by a literal `+` in
   `openid.sig`.** `SteamOpenIdCallback.parse` decodes every query value
   with `java.net.URLDecoder.decode(_, "UTF-8")`, which follows
   `application/x-www-form-urlencoded` semantics: an UNENCODED `+`
   character decodes to a space. Base64 (the alphabet `openid.sig` is
   drawn from) legitimately contains `+`. A spec-compliant redirect from
   Valve percent-encodes it as `%2B` in the query string, which decodes
   back to a literal `+` correctly — this is expected to be a non-issue in
   practice — but if a real device test ever sees a sign-in fail for no
   apparent reason, check whether the callback URL's `openid.sig` carried
   a raw `+`: this fails CLOSED (a corrupted signature value simply makes
   `check_authentication` return `is_valid:false`, never a security hole),
   but it would look like an unexplained rejection rather than the
   `+`-decoding cause. Not fixed here (no evidence Valve's actual redirect
   needs it) — recorded as a debugging note for whoever sees the failure
   first.

### What WP 4b.3 deliberately did NOT do

- **No library grid, no game list UI** — `ownedGamesCountPreview()`
  exposes a COUNT only; the full grid is WP 4b.4.
- **No settings UI for entering the Steam Web API key** —
  `setWebApiKey()` exists on the repository; a real input screen is WP
  4b.7 (onboarding/settings, serial after 4b.3 per
  `docs/WORKPACKAGES.md`).
- **No real navigation** — `IdentityScreen` replaces the WP 4b.1 debug
  gallery as `MainActivity`'s one screen; multiple destinations arrive
  with 4b.4/4b.5/4b.7's navigation work.
- **No app-wide error-display convention** — review round S3 added ONE
  line of state (`MainActivity.identityState.loginError` →
  `IdentityScreen`'s inline `Text` in the error colour) so
  `SteamLoginResult.Failure.reason` is at least visible when sign-in
  fails, since that branch is exactly where the device-only verification
  items above (1-3) would land if any of them go wrong on a real device.
  A real Toast/Snackbar/inline-message CONVENTION for the whole app is
  still left to whichever later WP establishes one, rather than guessed
  at here.
- **No WorkManager-driven persona/library refresh** — `refreshPersonaName`/
  `ownedGamesCountPreview` are both manual, button-triggered calls; any
  background refresh is WP 4b.8's polling work.

## Downloads + job control (WP 4b.5)

Branch-parallel after 4b.2 per `docs/WORKPACKAGES.md` Phase 4b. Adds the
Downloads screen (`ui/downloads/`): an Active section and an INDEPENDENT
Paused section (the slot-release divergence — api/README.md "The worker
slot — a paused job does NOT hold it" — ported from `web/js/lib/
job-partition.js` onto the real WP 3.12 status set), a FIFO queue with
positions, and history newest-first with lazily-fetched log excerpts (one
`GET /v1/jobs/{id}` per job on first expand, cached for the session).
Job control (pause/resume/cancel) is non-optimistic — a click only calls
`VaultApiClient` and nudges an immediate re-poll, same "server confirms"
pattern `web/js/views/downloads.js` documents.

`ui/downloads/logic/JobPartition.kt` records one deliberate IMPROVEMENT
over the web port: an unrecognized job status is routed into the History
section with a neutral presentation instead of silently vanishing from
every bucket (the web module's own review nit) — see that file's kdoc.

### What is verifiable only on-device (WP 4b.5)

Everything in `ui/downloads/logic/` is proven pure/JVM-side
(`JobPartitionTest`, `JobCardModelTest`, `LogExcerptTest`,
`FormatTest`), and `DownloadsController`'s network calls go through the
same `VaultApiClient`/`VaultApiError` seams WP 4b.2 already device-verified
for other endpoints. What is NOT exercised by any of that, and needs a real
phone against a real vault-api before it can be called confirmed:

1. **Pause/resume/cancel against a REAL running job.** The `stop_request`
   round trip (`POST /v1/jobs/{id}/pause`/`resume`, `DELETE /v1/jobs/{id}`)
   is proven client-side against `VaultApiClient`'s request/response
   shapes only — that the WORKER actually terminates the SteamPrefill
   subprocess, that `stop_request` clears once it does, and that the
   "Pausing…"/"Cancelling…" note on the job card disappears at the right
   poll tick, is only observable end-to-end with a genuine vault-api
   worker doing real work.
2. **Active-vs-Paused slot-release presentation with a genuinely paused
   job.** `JobPartitionTest` proves the pure partitioning logic (running
   and paused as independent buckets); it does not prove that a real
   pause against a real download leaves a DIFFERENT queued job claimed and
   running while the paused one sits in its own section on screen — that
   needs two real jobs and a real worker.
3. **The lazy log-excerpt fetch on first expand.** `ExcerptCache`'s
   fetch-once/cache-for-the-session/retry-after-failure state machine is
   proven against a canned fetcher (`LogExcerptTest`); the real `GET
   /v1/jobs/{id}` call — its latency, a genuine truncated SteamPrefill
   log, and the Compose recomposition `DownloadsController.excerptVersion`
   drives on a real device — has not been exercised outside a JVM test.
4. **The nav pip's foreground-only staleness** (see `MainActivity.kt`'s
   `pendingJobsSnapshot` kdoc for the mechanism). The pip is only ever
   updated while Library or Downloads — whichever screen currently owns
   the jobs poll — is on screen; it goes stale (does not update) while
   Settings is visible, and only catches up once the user switches back.
   This is a real, user-visible behaviour, not just an implementation
   detail: a device test should confirm it reads as "a little behind",
   not as broken, and that a screen reader announces the overridden
   `contentDescription` (`"Downloads — N pending"`) correctly once a job
   is actually pending.

### What WP 4b.5 deliberately did NOT do

- **No WorkManager / background jobs poll** — foreground-only via
  `repeatOnLifecycle`, same constraint every screen in this app has before
  WP 4b.8.
- **No queue reordering / drag-to-reorder** — post-v1 backlog item per
  `docs/WORKPACKAGES.md`; the queue is presentation-only FIFO.
- **No detail-sheet integration** — WP 4b.6.
- **No update-check affordance anywhere on this screen** — Phase 4c guard
  (binding): a refresh only ever re-polls `GET /v1/jobs`/`GET /v1/games`,
  never triggers or checks for a download on its own initiative.

## Game detail sheet (WP 4b.6)

Serial after 4b.4 per `docs/WORKPACKAGES.md` Phase 4b. Adds the sheet opened
from a Library card (`ui/detail/`): cover/name/status, sizes, the honest
last-download/confirmed-current wording, per-depot sharing (computed live
from `buildMultiPlan`/`buildDepotPresentation`, never stored — mockup round
3), download/pause/resume/cancel for the app's own tracked job, delete with
a per-depot freed/kept preview (literally `buildMultiPlan(listOf(appid),
...)`, so it cannot drift from the Library's bulk-delete arithmetic), and a
dry-run → confirm → execute GC flow (`ui/detail/logic/GcFlow.kt`'s state
machine) that can never reach `execute=true` without an explicit second
confirm after a completed dry run.

**Recorded divergence — a fourth depot-sharing state, `ORPHANED`, beyond the
mockup's three (same class of documented deviation as the WP 4b.5
slot-release divergence above, and the WP 4a.5/4b.5 `cancelled` status-icon
divergences in `docs/WORKPACKAGES.md`'s Phase 4a header — docs/LEARNINGS.md
requires deviations from the frozen mockup to be recorded, not just
kdoc'd).** The mockup only ever distinguishes `shared` (kept) from `shared ·
sole holder` (the viewed game is the last cached holder, deleting frees it —
round 5). It never modeled a THIRD case the real API's ADR-0003 last-remnant
rule makes reachable: a game that has ALREADY been deleted keeps its mapping
rows by design (`DELETE /v1/cache/{appid}` "mapping rows survive deletion"),
so opening its detail sheet again can show a shared depot where NEITHER the
viewed game NOR any of its co-owners currently has cache content — the exact
"previously deleted game, mapping intact, nothing on disk" shape
`vault-app-mockup-NOTES.md`'s own sample-data note seeds for Meridian Rally,
just reached from the real deletion flow instead of authored fixture data.
Tagging that case `SOLE_HOLDER` would be dishonest (the viewed game holds
nothing to protect by deleting further); the sheet reports it as `ORPHANED`
("Shared · no cached owner") instead — see
`ui/detail/logic/DepotPresentation.kt`'s kdoc and `DepotPresentationTest`'s
"shared, no other holder, THIS app also does not hold it" case for the exact
condition (`row.free && !thisAppIsHolder`).

## Onboarding + settings (WP 4b.7)

**Partially superseded by WP 4h.4 ("Steam library via the vault relay",
near the end of this file).** The connection-flow half of this section
(Step 1 Connect, `net/connection/ConnectionCheck.kt`, the Connection
section in Settings) is unchanged and still accurate. Everything this
section says about a Steam Web API key entry field/UI —
`net/steam/SteamWebApiKeyInput.kt`, `submitWebApiKey`/`removeWebApiKey`,
the "closes the `setWebApiKey()` UI gap" framing below — describes a UI
path WP 4h.4 deleted outright, not what ships today.

Branch-parallel after 4b.3 per `docs/WORKPACKAGES.md` Phase 4b — dispatched
after every other 4b.x package, so it is the first WP with everything else
(4b.1-4b.6) already in place to write against. Closes the two gaps every
earlier WP's README section flagged as "not this WP": nothing wrote a
vault-api connection into `CredentialStore` (`net/profile/
ConnectivityProfileFactory.kt`'s "Gap this documents" note), and
`setWebApiKey()` had no UI path at all (WP 4b.3's own bullet list). **(WP
4h.4: `setWebApiKey()` itself no longer exists — see above.)**

```
app/app/src/main/java/dev/steamvault/app/
├── net/
│   ├── connection/
│   │   └── ConnectionCheck.kt          # two-step "test connection" state machine
│   └── steam/
│       ├── SteamLoginState.kt          # per-login random state (replay-residual fix)
│       └── SteamWebApiKeyInput.kt      # 32-hex validator + the field-clearing pin
├── ui/
│   ├── onboarding/
│   │   ├── logic/OnboardingSteps.kt    # pure 3-step gating machine
│   │   ├── OnboardingController.kt
│   │   ├── OnboardingStrings.kt
│   │   └── OnboardingScreen.kt
│   └── settings/
│       ├── logic/SettingsDiff.kt        # PATCH-body-from-drafts port
│       ├── logic/SettingsPresentation.kt
│       ├── SettingsController.kt
│       ├── SettingsStrings.kt
│       └── SettingsScreen.kt
└── MainActivity.kt                      # onboarding gate + reconnect/disconnect wiring
```

### Onboarding (`ui/onboarding/`)

Three steps, same shape the frozen mockup and the web port (`web/js/
onboarding.js`, WP 4a.6) both use, but step 1's FIELDS differ on purpose —
`ui/onboarding/logic/OnboardingSteps.kt`'s kdoc explains why the web port's
"no server-URL field, the page is already served by vault-api" shortcut
does not apply to a native app that has never talked to any server yet:

1. **Connect** — connectivity profile choice (`SystemVpnProfile` /
   `PublicDomainProfile`, WP 4b.2), base URL, vault API key, and a REAL
   connection check before `Continue` unlocks
   (`OnboardingSteps.canAdvanceOnboardingStep`: step 1 requires
   `tested == true`, exactly like the web port's step 1 gate).
2. **Steam identity (optional)** — the existing WP 4b.3 OpenID sign-in flow,
   plus the Steam Web API key entry this WP adds a UI for. Skippable:
   `canAdvanceOnboardingStep` never blocks on this step, matching the
   mockup's "Continue without one — you can sign in later under Settings".
3. **Done** — a summary (connection verified / Steam identity linked or
   not), then `finish()` persists the connection and the caller
   (`MainActivity`) rebuilds its `VaultApiClient`.

**The connection check (`net/connection/ConnectionCheck.kt`) mirrors
`web/js/api.js::checkVaultApiKey`'s two-step reasoning verbatim: `GET
/v1/health` never validates a key** (api/README.md "Auth" — this app
attaches `X-Api-Key` to every request including health, same choice
`web/js/api.js`'s client makes, but the SERVER never checks it there), so a
successful health call only proves reachability. Only the second,
authenticated call (`GET /v1/settings`, chosen for the same reason the web
port picks it — onboarding needs the response anyway) actually proves the
key. `checkVaultConnection` takes two suspend LAMBDAS rather than a
concrete `VaultApiClient` specifically so `ConnectionCheckTest` can pin the
step ORDERING (the settings lambda must never run if health already failed)
and the failure classification (`classifyConnectionFailure`) without any
MockWebServer/network stack at all — a `401` is only ever labelled
`KeyRejected` on the SETTINGS step, never on HEALTH (which has no auth
check to fail against a real server).

**Full-screen swap, not a modal overlay.** `web/js/onboarding.js` renders
onboarding as a `role="dialog"` overlay above the app shell; this app swaps
it in as `MainActivity`'s entire `setContent` body instead — the same plain
state-based screen-switching choice `ui/nav/Destination.kt`'s kdoc already
committed to for the three main destinations, extended to one more
top-level state rather than introducing a second, heavier overlay mechanism
for one screen. `OnboardingMode.RECONNECT` (Settings' "Reconnect / switch
vault") gets a "Cancel" action that bails out with the existing connection
untouched — `OnboardingController.canCancelWithoutFinishing`, the same
"nothing to fall back to on first run" distinction `web/js/onboarding.js`'s
`mode` makes with its Escape-only-in-reconnect handling.

### Settings (`ui/settings/`)

Replaces the bare `ui.identity.IdentityScreen` that used to be the entire
`Destination.SETTINGS` content (`IdentityScreen.kt` itself is untouched and
still compiles — same "kept but unreachable from the UI" treatment WP
4b.3/4b.4 gave `ui.gallery.GalleryScreen`). Three independent surfaces, same
split `web/js/views/settings.js` (WP 4a.6) documents:

- **Vault / Schedule / Webhook** — one form over `GET`/`PATCH /v1/settings`
  (ADR-0009), ported field-for-field from the web view. `ui/settings/logic/
  SettingsDiff.kt::buildSettingsPatchDraft` is the `settings-diff.js` port:
  a `drafts` map populated ONLY by fields the user actually touches (never
  pre-seeded from the loaded snapshot) is diffed against the last `GET`
  response, so a no-op edit (types the same value back) is dropped and the
  PATCH body contains ONLY genuinely changed keys — pinned by
  `SettingsDiffTest`'s named mutation case, same "removing the
  `valueChanged` guard must kill a named test" discipline the web port's
  own test file documents. `ui/settings/logic/SettingsPresentation.kt`
  ports `settings-presentation.js`'s `source`/`applies` captions as pure
  enums (`SettingsSource`/`SettingsApplies`) resolved to `strings.xml` text
  at the `SettingsScreen.kt` call site, honouring app/README.md's "String
  resources" convention rather than hardcoding the wording in Kotlin.
  `VAULT_SETTINGS_READONLY` disables the whole form (readonly banner,
  every field `enabled = false`, no Save/Discard bar) but the Steam
  identity section below stays fully usable — same reasoning
  `web/js/views/settings.js` documents (Steam identity is a separate
  endpoint, unaffected by the settings-API lock). A `422` from `PATCH`
  (api/README.md: "one bad value... fails the request... with a DISTINCT
  detail") is surfaced verbatim via `saveError` — vault_api's detail string
  already names the offending key, so no client-side field-mapping is
  needed to make the error legible.
- **Steam identity** — the existing WP 4b.3 sign-in/out state plus Web API
  key management this WP adds: a MASKED status line (`settings_steam_key_
  masked`/`_not_set` — WP brief: "only whether set, never the value") and
  a Remove action (`removeWebApiKey`, clears the key without ever reading
  it back).
- **Connection** — `SettingsController.connectionSummary()` reads
  `CredentialStore` directly and shows the base URL + profile kind, NEVER
  the API key (WP brief boundary, mirrored in the `ConnectionSummary` data
  class itself — it has no key field to leak by accident). "Reconnect /
  switch vault" opens onboarding in `RECONNECT` mode; "Disconnect" (behind
  an `AlertDialog` confirm) calls `CredentialStore.clear()` — the WHOLE
  store, deliberately broader than `clearSteamIdentity()`, matching that
  interface's own documented "forget this vault entirely" contract for
  exactly this action.

### `setWebApiKey` UI gap, closed (`net/steam/SteamWebApiKeyInput.kt`) — REMOVED in WP 4h.4

*Historical record only (WP APP-FIX-1 S3): `SteamWebApiKeyInput.kt`,
`submitWebApiKeyEntry()` on both controllers, and `SteamWebApiKeyInputTest`
were deleted in WP 4h.4 together with the device-local key itself — see
"Steam library via the vault relay" below.*

`submitWebApiKey` is a direct, Compose-free port of `web/js/lib/
steam-key-form.js::submitSteamKey`'s two guarantees: `validSteamWebApiKey`
mirrors the exact "32 hexadecimal characters" grammar
(`vault_api.steam_relay.valid_steam_web_api_key`), and the returned
`WebApiKeySubmitResult.nextFieldValue` is **`""` on every path** — a
rejected format, a `persist` that throws, or success alike — the same
unconditional-clear guarantee the web port's `field.value = ""` `finally`
block gives, pulled into a pure function specifically so it is mechanically
provable rather than "the code looks right"
(docs/LEARNINGS.md "Testing discipline"). `SteamWebApiKeyInputTest` pins
all three paths by name (`MUTATION PIN` cases). Both `OnboardingController
.submitWebApiKeyEntry()` and `SettingsController.submitWebApiKeyEntry()`
call this same function against their own `mutableStateOf` field —
`OnboardingControllerTest`'s own `MUTATION PIN` cases additionally exercise
the real Compose-state field end to end (not just the pure function), per
the WP brief's explicit ask to pin the clearing "in a controller test".

### Replay-residual fix (`net/steam/SteamLoginState.kt`)

See the "Residual narrowed by WP 4b.7" section above (in the WP 4b.3
write-up) for the full before/after and the one class that remains — summary: a CSPRNG per-login `state` token is
embedded in `openid.return_to`, checked via a single-use `PendingLoginState
.consume()` BEFORE `signedCoversClaimedId`/`check_authentication` run, so a
missing, wrong, OR previously-used state is rejected without any network
call. `SteamIdentityRepositoryTest`'s "a consumed state cannot be replayed"
case is the mutation pin the WP brief asked for.

### Tests (WP 4b.7)

85 new JVM unit tests (492 total with the prior WPs' 407), no
Robolectric/emulator dependency:

- `ui/onboarding/logic/OnboardingStepsTest` — progress percent, the step-1
  gating direction (both ways), Back/Continue boundary no-ops,
  `shouldShowOnboarding`'s mutation pin.
- `net/connection/ConnectionCheckTest` — `classifyConnectionFailure`'s
  three outcomes incl. the HEALTH-step-401-is-never-KeyRejected mutation
  pin, and `checkVaultConnection`'s step ordering/short-circuit (settings
  never called after a health failure).
- `ui/settings/logic/SettingsDiffTest` / `SettingsPresentationTest` — the
  no-op-edit-is-dropped mutation pin, the env_only/non-db-reset guards,
  the `webhook_events` list-vs-comma-text equivalence (no dedup, matching
  the web port exactly), and the applies/source/effective-text pure
  mappings.
- `net/steam/SteamLoginStateTest` — CSPRNG output shape (length, alphabet,
  no collisions in two calls), and `PendingLoginState.consume`'s single-use
  semantics in every direction (match, mismatch, nothing pending, null
  actual, consumed-then-replayed).
- `net/steam/SteamOpenIdCallbackTest` (extended) — `stateFromReturnTo`
  parsing (present, absent, alongside other params, percent-encoded,
  trailing bare `?`).
- `repo/SteamIdentityRepositoryTest` (extended) — every existing
  `completeLogin` test now seeds a pending state via `buildLoginUrl()`
  first (the state check is now the first gate); new cases cover no
  pending state, no state in the callback, a mismatched state, and the
  named "a consumed state cannot be replayed" mutation pin.
- `net/steam/SteamWebApiKeyInputTest` — **REMOVED in WP 4h.4** together
  with the code under test (the 32-hex validator and `submitWebApiKey`'s
  clearing guarantee); see the relay section for what replaced it.
- `ui/onboarding/OnboardingControllerTest` — step navigation delegation,
  `start()`'s field-seeding from `CredentialStore`, Steam login delegation,
  the web-API-key clearing pin exercised through the real Compose state
  field, and `finish()`'s exact persisted-fields pin (connection only,
  nothing Steam-related).

Verified command + output tail:

```
$ ./gradlew.bat test lintDebug assembleDebug
...
BUILD SUCCESSFUL in 1m 2s
74 actionable tasks: 22 executed, 52 up-to-date
```

492/0/0 across both `testDebugUnitTest` and `testReleaseUnitTest` (summed
from the XML reports' `tests=`/`failures=`/`errors=` attributes);
`app/app/build/reports/lint-results-debug.txt`: "No issues found."
`app/app/build/outputs/apk/debug/app-debug.apk` produced.

### What WP 4b.7 deliberately did NOT do

- **No instrumented test of the onboarding/settings screens** — no
  emulator/device available, unchanged constraint from every earlier 4b.x
  WP; `OnboardingController`/`SettingsController` are Compose-free-enough
  to unit test their orchestration logic (see above), but the actual
  Compose rendering, focus order, and field behaviour have not been seen
  on a real screen.
- **No real "About" section, no app version display** — not asked for by
  the WP brief; `web/js/views/settings.js`'s About block (Valve trademark
  notice) has no Android counterpart yet.
- **`tsnet` connectivity profile picker** — still post-v1
  (`docs/WORKPACKAGES.md`), unaffected by this WP; the onboarding profile
  choice only ever offers `SystemVpnProfile`/`PublicDomainProfile`.
- **No live re-validation of an already-configured connection** —
  Settings' Connection section trusts whatever is in `CredentialStore`
  without re-testing it; a connection that has gone stale (revoked key,
  changed IP) surfaces as a `VaultApiError` the next time Library/Downloads
  actually poll, not proactively on the Settings screen.
- **No WorkManager interaction** — onboarding/settings are foreground-only
  screens; WP 4b.8's background polling is unaffected by (and does not
  affect) anything in this WP.

## Notifications via WorkManager (WP 4b.8)

Serial after 4b.5 per `docs/WORKPACKAGES.md` Phase 4b. Closes the gap every
earlier WP's README section flagged ("WorkManager wiring... is WP 4b.8, not
this WP" — WP 4b.2's "Polling primitives" section, restated in 4b.5/4b.7):
a background poll that derives the same notification events
`docs/design/vault-app-mockup-NOTES.md`'s bell panel documents
(finished/failed downloads, update-ready games, cache-bypass warnings),
independent of whether the app is open.

```
app/app/src/main/java/dev/steamvault/app/
├── VaultApplication.kt                  # process-wide onCreate: channels + scheduling
├── MainActivity.kt                      # notification-tap routing (extended)
└── notifications/
    ├── NotificationEvent.kt             # the 5-event taxonomy (sealed class)
    ├── DiffByKey.kt                     # generic keyed-list differ (web diff-utils.js port)
    ├── NotificationSnapshot.kt          # the persisted compact snapshot shape + mappers
    ├── NotificationDiffer.kt            # the pure differ (web notifications.js port)
    ├── NotificationSnapshotStore.kt     # SharedPreferences persistence (interface + impl)
    ├── NotificationPollLogic.kt         # pure evaluate() — the worker's testable core
    ├── NotificationRouting.kt           # event -> channel/destination/id mapping
    ├── NotificationChannels.kt          # NotificationChannel creation
    ├── NotificationStrings.kt           # title/body text (Resources-backed interface)
    ├── NotificationPoster.kt            # posts the Android notification (foreground gate, permission check)
    ├── NotificationPollWorker.kt        # the CoroutineWorker (thin glue)
    └── NotificationScheduler.kt         # PeriodicWorkRequest enqueue
```

### The differ port (`NotificationDiffer.kt`, `DiffByKey.kt`)

Line-for-line port of `web/js/notifications.js` + `web/js/diff-utils.js`
onto a Kotlin `sealed class NotificationEvent` (`JobFinished`/`JobFailed`/
`UpdateReady`/`BypassSuspected`/`BypassResolved` — `BypassResolved` kept
per `docs/LEARNINGS.md`'s transition-detector rule, same reasoning the web
module's own kdoc gives). `DiffByKey.kt` mirrors `diffByKey` exactly except
it compares with Kotlin data-class `equals()` instead of the web module's
`JSON.stringify` stand-in — same added/updated/removed/unchanged/`isFirst`
buckets either way. Both invariants the DoD calls out by name are pinned:
the first poll (`isFirst`) never fires, and a no-op poll produces zero
events.

**One deliberate improvement over the web port**, per this WP's brief
("the Android improvement from 4b.5 (unknown statuses) applies where
relevant"): a job whose PREVIOUS status is neither a known active status
nor a known terminal one (a value this client has never seen) is now
treated as "was active" rather than "was not active" when deciding whether
a transition to `done`/`error` is real news — the web differ's
`JOB_ACTIVE_STATUSES.has(prev.status)` check would silently swallow that
transition instead. Same fail-toward-reporting-real-information posture
`ui/downloads/logic/JobPartition.kt`'s WP 4b.5 divergence already
established for the Downloads screen; see `NotificationDiffer.kt`'s kdoc
for the full reasoning and why `diffGames`/`diffClients` needed no
analogous change (both already compare against a single known value by
equality, which is fail-safe by construction).

### Compact snapshot, not the raw API response (`NotificationSnapshot.kt`)

The differ only ever reads four job fields, four game fields and two
client fields — the persisted `NotificationSnapshot` carries exactly
those, not the full `net/model/` response shapes (byte counters, source
addresses, timestamps the differ never looks at). Smaller on-disk payload
for a multi-hundred-game library, and it makes "did the differ see a real
change" and "did the persisted snapshot change" the same question by
construction. Plain (non-encrypted) `SharedPreferences`
(`SharedPreferencesNotificationSnapshotStore`) — nothing here is a secret,
same reasoning `storage/LibraryPreferences.kt` documents for the layout
preference; it deliberately does NOT go through `CredentialStore`/
`EncryptedCredentialStore`, whose one narrow guarantee is scoped to the
vault-api key alone. A corrupted/incompatible-schema stored value decodes
to `null` (treated as "never saved") rather than crashing the worker.

### Idempotency: notify, THEN persist (`NotificationPollLogic.kt`, `NotificationPollWorker.kt`)

`NotificationPollWorker.doWork()` fetches once, calls the pure
`NotificationPollLogic.evaluate(prevSnapshot, jobs, games, clients)`, posts
the resulting events, and ONLY THEN persists the new snapshot — in that
order, never reversed. A crash between fetch and persist re-derives the
identical event set on the next run (a possible harmless re-post of the
same notification — `NotificationRouting.notificationId` is a stable hash
of the event's own key, so a repost UPDATES the existing system
notification rather than stacking a duplicate); a crash strictly after
persist produces zero events on the next run (nothing looks new anymore).
The rejected alternative (persist-before-notify) would let a crash between
persist and notify silently DROP the event forever — a strictly worse
failure mode. `NotificationPollLogicTest` pins both crash windows against
an `InMemoryNotificationSnapshotStore` fake, without touching Android or
WorkManager at all.

Fail-soft rules mirror the WP brief exactly: no vault-api connection
configured, or no API key stored → `Result.success()` silently, nothing to
poll. The fetch call throwing `VaultApiError` (network down, auth failure,
server error, unparsable body) → `Result.success()` WITHOUT persisting, so
the next successful run still diffs against a valid baseline instead of a
partial one.

### Foreground suppression (`NotificationPoster.kt`, `NotificationPollWorker.kt`)

Simplest honest rule per the brief ("a process-lifecycle check is
enough"): `ProcessLifecycleOwner.get().lifecycle.currentState.isAtLeast
(Lifecycle.State.STARTED)` gates whether `AndroidNotificationPoster.post`
actually calls `NotificationManagerCompat.notify` — while foreground, the
call is a no-op. The worker still runs its full fetch + diff + PERSIST
cycle regardless of foreground state, so the snapshot always advances;
without that, returning to the background would replay every event that
happened while the user was already looking at the live Library/Downloads
polling loops (`polling/PollingIntervals.kt`).

### WorkManager scheduling and Doze (`NotificationScheduler.kt`, `VaultApplication.kt`)

A single `PeriodicWorkRequest` at `PeriodicWorkRequest.MIN_PERIODIC_
INTERVAL_MILLIS` (15 minutes — WorkManager's documented floor), constrained
to `NetworkType.CONNECTED`, enqueued with `ExistingPeriodicWorkPolicy.KEEP`
from the new `VaultApplication.onCreate` (this app had no custom
`Application` class before this WP) so re-launching the app never resets
WorkManager's internal next-run clock. **Deliberately no exact alarms, no
foreground service, no battery-optimization-exemption prompt** — all three
would fight Android's Doze/App Standby power model instead of living
inside it (the last one in particular would ask the user for a permission
most apps should not need). This means the REAL interval while the phone
sits idle is "15 minutes, or considerably longer under deep Doze, with
runs batched into maintenance windows" — accepted for v1 per
`docs/design/vault-app-mockup-NOTES.md` ("Notifications are a poll, not a
push") and the WP brief's own wording ("polling via WorkManager, respecting
Doze"); a user who wants faster feedback still has the foreground screens'
own 2–20 s polling.

### Notification channels, permission, routing (`NotificationChannels.kt`, `NotificationPoster.kt`, `NotificationRouting.kt`, `SettingsScreen.kt`)

Three channels (`downloads`/`updates`/`bypass`, `bypass` at
`IMPORTANCE_HIGH`) so the user can silence any one class independently.
`POST_NOTIFICATIONS` (API 33+, declared in `AndroidManifest.xml`) is
requested from the new Settings → Notifications section
(`SettingsScreen.kt`'s `NotificationsSection`, wired through
`MainActivity.requestNotificationPermission` and a class-level
`registerForActivityResult` launcher) — denying it leaves the worker
running exactly as before, just without a visible notification
(`AndroidNotificationPoster` re-checks the live grant on every post, no
separate reactive state needed). Tapping a notification opens
`MainActivity` with `NotificationRouting.EXTRA_DESTINATION` set to a
`Destination.name` string; `MainActivity.destination` was hoisted out of a
`remember { }` local into a class-level `mutableStateOf` specifically so
`handleNotificationTap` (called from both `onCreate`'s and
`onNewIntent`'s `handleIntent`) can change it from outside composition.
Routing: job events → `Destination.DOWNLOADS`, bypass events →
`Destination.SETTINGS` (this app has no dedicated clients sheet yet,
unlike `web/js/views/clients.js` — an honest simplification, not a
missing feature this WP was asked to add), `update_ready` →
`Destination.LIBRARY` (the brief names no destination for it explicitly;
a per-game deep link like the mockup's bell panel offers does not exist as
an addressable destination in this app yet). All three mappings are
literal-pinned in `NotificationRoutingTest`, never derived from the enums
under test.

**Recorded routing gap (review round, N3) — `bypass_suspected`/
`bypass_resolved` land on `Destination.SETTINGS`, not a clients surface
(same "recorded, not silently kept" treatment the depot-sharing `ORPHANED`
divergence above and `ui/downloads/logic/JobPartition.kt`'s WP 4b.5
divergence get).** Settings does not actually show per-client bypass
detail today — tapping the notification lands the user one screen away
from the client list `web/js/views/clients.js` gives the web frontend, not
on it. This is accepted as this WP's honest ceiling, not fixed here: **a
clients surface is the real fix**, and belongs to whichever future WP
gives this app a `GET /v1/clients`-backed screen (Settings is the nearest
existing destination only because no such screen exists yet).

### Versions pinned for this WP

Added to the existing `gradle/libs.versions.toml` table:

| Component | Version | Why |
|---|---|---|
| androidx.work:work-runtime-ktx | 2.9.1 | latest stable 2.9.x release; 2.10.x's floor needs a newer AGP than this project's pinned 8.7.3 |
| androidx.lifecycle:lifecycle-process | 2.8.7 | same version already pinned for lifecycle-runtime-ktx/-compose; provides `ProcessLifecycleOwner` |

No `androidx.work:work-testing` dependency: it needs Robolectric or an
instrumented device to drive `TestListenableWorkerBuilder`, neither of
which exists in this environment — see "What WP 4b.8 deliberately did NOT
do" below.

### Tests (WP 4b.8)

42 new JVM unit tests (534 total with the prior WPs' 492), no
Robolectric/emulator dependency:

- `notifications/DiffByKeyTest` — the generic differ's bucket semantics
  (`isFirst` on `null` vs. empty prev, unchanged vs. updated, removed).
- `notifications/NotificationDifferTest` — the full web-test-file port:
  first-poll silence (all three domains + the combined helper), no-change
  silence, `job_finished`/`job_failed` (incl. the added-vs-updated and
  aged-out-of-the-window cases), cancelled-is-silent, `update_ready`'s
  zero-bytes/null-bytes/already-stale/non-stale-transition gates,
  `bypass_suspected`/`bypass_resolved` both directions plus the
  steady-state no-op, the combined-helper ordering, and the two Android-
  improvement cases (unrecognized previous status → `done`/`error` still
  fires).
- `notifications/NotificationSnapshotSerializationTest` — the
  serialize/deserialize round trip (populated, all-empty, and
  null-vs-empty-string field cases), plus the corrupted-JSON-is-treated-
  as-never-saved case via `InMemoryNotificationSnapshotStore`.
- `notifications/NotificationPollLogicTest` — the idempotency decision pin
  (crash-before-persist re-derives the same event; crash-strictly-after-
  persist derives zero) against the in-memory fake store, end to end
  through `NotificationPollLogic.evaluate`.
- `notifications/NotificationRoutingTest` — every event type's
  channel/destination mapping, `EXTRA_DESTINATION`'s literal string, and
  `notificationId`'s stability/distinctness.

Verified command + output tail:

```
$ ./gradlew.bat test lintDebug assembleDebug
...
BUILD SUCCESSFUL in 30s
74 actionable tasks: 26 executed, 48 up-to-date
```

534/0/0 in both `testDebugUnitTest` and `testReleaseUnitTest` (verified via
the XML reports' `tests=`/`failures=`/`errors=` attributes, each variant
summed independently — both run the identical suite);
`app/app/build/reports/lint-results-debug.txt`: one pre-existing-pattern
`PluralsCandidate` false positive on `notif_job_failed_body` (`%1$d` is an
app id, not a count — same class of justified suppression as
`settings_schedule_window_placeholder`'s `TypographyDashes` ignore),
suppressed with a documented `tools:ignore`; otherwise clean.
`app/app/build/outputs/apk/debug/app-debug.apk` produced.

### What WP 4b.8 deliberately did NOT do — the honest device-test list

No emulator/device is available in this environment (unchanged constraint
from every earlier 4b.x WP). Everything above the Android-framework
boundary is unit-tested; everything below it is real, uncovered
device-territory that a future on-device pass must verify:

- **Does WorkManager actually invoke `NotificationPollWorker` on the
  declared ~15-minute cadence**, and does it visibly batch/defer under a
  real Doze session the way this WP's README section above claims it will.
- **Does `EncryptedCredentialStore`/Android Keystore actually work from a
  background process** (no Activity, no foreground UI thread) the same way
  it works from `MainActivity` — `NotificationPollWorker` is the first
  caller of `EncryptedCredentialStore` that isn't Activity-driven.
- **Does `NotificationManagerCompat.notify` actually show a heads-up/tray
  notification** with the right channel, title, body, and does tapping it
  actually launch `MainActivity` and land on the right `Destination` via
  `onNewIntent`.
- **Does the `POST_NOTIFICATIONS` permission prompt actually appear on a
  real API 33+ device** when Settings → "Enable notifications" is tapped,
  and does denying it leave the worker's fetch/diff/persist cycle running
  with only the visible notification suppressed.
- **Does the foreground-suppression rule actually feel right in practice**
  — `ProcessLifecycleOwner`'s STARTED threshold is a coarse process-wide
  signal (any part of the app in the foreground suppresses ALL
  notification classes), not a per-screen one; the brief calls this
  sufficient, but it has not been felt on a real device.
- **No `androidx.work:work-testing` / Robolectric** — `TestListenableWorkerBuilder`
  needs one of the two, neither available here; `NotificationPollLogic`
  (the pure decision core) carries the equivalent test weight on the plain
  JVM instead, per this WP's brief ("extract decisions so the untestable
  shell is thin").
- **No live re-check of the notification permission's grant state on the
  Settings screen** — `NotificationsSection` always shows the same
  "Enable notifications" button regardless of current grant, rather than a
  reactive granted/denied indicator (would need a lifecycle-resume
  observer to refresh after the user returns from the system dialog;
  out of scope for this WP's "keep it simple" instruction).
- **No per-game deep link for `update_ready`** — routes to the Library
  destination generally, not to the specific game's detail sheet the way
  the mockup's bell panel does; no "focus this appid" extra exists on the
  Library screen yet.
- **Reused the WP 4b.1 launcher's monochrome vector
  (`ic_launcher_monochrome.xml`) as the notification small icon** rather
  than commissioning a dedicated glyph — it is already a pure white-on-
  transparent silhouette, exactly the shape a status-bar icon needs;
  revisit in the WP 4b.9 release-art pass if a dedicated asset is wanted.

## Clients sheet (WP 4b.10)

Serial after 4b.8 per `docs/WORKPACKAGES.md` Phase 4b. Closes the recorded
routing gap that WP 4b.8's own README section above documents ("Recorded
routing gap... bypass_suspected/bypass_resolved land on Destination
.SETTINGS, not a clients surface") and the matching PARTIAL item in
`docs/PROJECT_PLAN.md` §7: a real `GET /v1/clients` screen, Kotlin port of
`web/js/components/clients-sheet.js` + `web/js/lib/clients-view.js`.

```
app/app/src/main/java/dev/steamvault/app/
├── ui/clients/
│   ├── logic/ClientsView.kt      # partitionClients, hitRatePercent, ClientRowModel
│   ├── ClientsController.kt      # state + the foreground-only poll loop
│   ├── ClientsStrings.kt         # the one Resources-backed fallback string
│   └── ClientsSheet.kt           # the ModalBottomSheet composable
├── notifications/NotificationRouting.kt  # destinationFor now nullable + opensClientsSheetFor
├── notifications/NotificationPoster.kt   # sets EXTRA_OPEN_CLIENTS_SHEET
├── ui/settings/SettingsScreen.kt         # new "Clients" section/button
└── MainActivity.kt                       # hoists ClientsController, wires both entry points
```

**Clients stays a sheet, not a nav item — `ui/nav/Destination.kt`'s kdoc and
the WP 4a.1 decision it restates are binding here too.** `ClientsController`
is therefore hoisted at `MainActivity` level next to `settingsControllerState`
(same reasoning that class's own kdoc gives), not owned by the Library/
Downloads/Settings screen it happens to render over — the mockup rule
"navigation dismisses transient surfaces" is honoured at the bottom nav's
`onSelect` call site (`clientsControllerState?.close()`), same place the
game detail sheet's dismissal precedent lives conceptually, generalized to
a sheet that is not scoped to any one destination's own `remember` block.

**Two entry points, matching `clients-sheet.js`'s own kdoc exactly:**
Settings' new "Clients" button (`SettingsScreen.kt`'s `ClientsSection`, for
a user who never got a notification), and a `bypass_suspected`/
`bypass_resolved` notification tap. The web twin also has a persistent
app-shell bypass banner as a third entry point — **deliberately not ported
here** (out of this WP's scope; the WorkManager background poll already
covers the notification half independently, and adding a persistent banner
is a real UI-surface decision matching the frozen-mockup discipline this
project holds Phase-4 UI to, not something to slip in as a side effect of
closing the routing gap).

### The routing gap, actually closed (`notifications/NotificationRouting.kt`)

`destinationFor(event: NotificationEvent): Destination?` is now nullable,
returning `null` for both bypass events (previously `Destination.SETTINGS`
— the recorded gap). A new `opensClientsSheetFor(event): Boolean` is `true`
for exactly those two events. `AndroidNotificationPoster` sets a new intent
extra, `EXTRA_OPEN_CLIENTS_SHEET`, only when `opensClientsSheetFor` says so,
and omits `EXTRA_DESTINATION` entirely when `destinationFor` is `null` — no
more forcing a bypass tap onto Settings at all. `MainActivity
.handleNotificationTap` checks the two extras independently (neither
early-returns on the other's absence), so a bypass tap now opens the
clients sheet directly, on top of whatever screen the user is currently on,
mirroring `web/js/components/notifications.js`'s own per-event
`target.kind` dispatch (a `"clients"` target calls `openClientsSheet()`
directly, never switching view first) rather than re-deriving a
destination-based approximation of it.
`NotificationRoutingTest`'s `bypass_suspected`/`bypass_resolved` cases now
assert BOTH `destinationFor(event) == null` (mutation target: distinct from
asserting `!= Destination.SETTINGS`, which alone would not catch a
regression to some OTHER wrong destination) and
`opensClientsSheetFor(event) == true` — either assertion alone would miss
half of a regression back toward the old behaviour.

### Model stability (`ui/clients/logic/ClientsView.kt`)

The WP brief's explicit ask ("follow the pattern the 4b.5 reviewer
endorsed — a tick that changes only a drift field must not rebuild the
row") is ported as a genuine, if differently-shaped, claim on this
platform — see that file's kdoc for the full reasoning: Compose's
`LazyColumn`/keyed recomposition has no imperative "rebuild the DOM" step
to avoid the way `web/js/lib/clients-render-plan.js`'s `full`/`patch`/
`rebuild` verdict exists for, so this WP does not port a redundant
render-plan diff object. What *is* still a real, testable claim is
`ClientRowModel`'s field split: [`ClientRowStats`] is the ONLY field a poll
tick may change while `clientId`/`bypassSuspected`/`addresses` stay put —
i.e. a stats-only tick can never also silently move a client to the other
section, and a section flip can never hide inside what looks like a stats
update. `ClientsViewTest` pins both directions with the same
`a.copy(volatileField = b.volatileField) == b` technique
`JobCardModelTest` uses for the `stop_request`-only diff.

### Wording contract (`ClientsCrossFrontendContractTest`)

Per the WP brief ("cross-frontend drift here is a defect — hold that
standard", referencing the 12/12 parity-mutation bar 4b.4/4b.5/4b.6 already
cleared), every section heading, fallback phrase, and the not-accusing
`BYPASS_EXPLANATION` sentence is literal-pinned against
`web/js/lib/clients-view.js`'s own hand-transcribed text by reading
`strings.xml` structurally (the same `readResFile`/`extractStringResource`
technique `StatusIconCrossFrontendContractTest` already established for the
status-word table) — never derived from the resource under test itself.

**A real finding from actually reading the web source, not trusting its own
comment (`docs/LEARNINGS.md` "verify empirically over believing docs"):**
`clients-view.js`'s header comment claims `BYPASS_EXPLANATION` is "shown
once per section rather than repeated per row", but `clients-sheet.js
::buildRow` actually appends a fresh hint paragraph inside its `if (bypass)`
branch for EVERY card it builds — once per bypassing client, not once per
section. `ClientsSheet.kt`'s `ClientRow` ports the code (an explanation
paragraph under every bypass-suspected row), not the stale comment; both
`ClientsView.kt`'s and `strings.xml`'s comments record the correction so a
future reader does not re-trust the same stale line.

### Tests (WP 4b.10)

16 new/changed JVM unit tests (550 total with WP 4b.8's 534 baseline: +10
`ClientsViewTest`, +5 `ClientsCrossFrontendContractTest`, net +1 in
`NotificationRoutingTest` — 2 old bypass-routes-to-Settings cases replaced
by 2 new bypass-opens-sheet cases plus 1 new `EXTRA_OPEN_CLIENTS_SHEET`
literal pin), no Robolectric/emulator dependency. `ClientsController`
itself is intentionally NOT given its own test file — same "thin glue,
tested only through its `logic/` pure functions" treatment
`DownloadsController`/`DetailController`/`SettingsController` already get
(none of the four has a dedicated `*ControllerTest.kt`); its `refreshOnce`/
`open`/`pollForever` are direct, un-branchy wiring around
`ClientsRepository`/`partitionClients`, with no idempotency or ordering
claim sharp enough to warrant the exception `OnboardingControllerTest`
earns for the field-clearing pin its own WP brief explicitly asked for.

Verified command + output tail:

```
$ ./gradlew.bat test lintDebug assembleDebug
...
BUILD SUCCESSFUL in 53s
74 actionable tasks: 37 executed, 37 up-to-date
```

550/0/0 (summed from the `testDebugUnitTest`/`testReleaseUnitTest` XML
reports' `tests=`/`failures=`/`errors=` attributes);
`app/app/build/reports/lint-results-debug.txt`: "No issues found."

### What WP 4b.10 deliberately did NOT do — the honest device-test list adds

Everything above the Android-framework boundary is unit-tested exactly like
every earlier UI WP; what remains real, uncovered device territory:

1. **`ModalBottomSheet` rendering, scroll, and dismiss gestures for the
   clients sheet specifically** — same class of item every earlier sheet/
   screen WP has recorded (`IdentityScreen`, `GameDetailSheet`), never
   exercised outside a JVM test.
2. **A real `bypass_suspected` transition observed live**, end to end from
   a genuine `vault-agent` report through `GET /v1/clients` into the sheet
   and the notification tap — `NotificationDifferTest`/`ClientsViewTest`
   prove the two halves independently, but not the seam between a live
   device notification tap and the sheet actually opening on top of
   whatever screen was showing.
3. **No persistent bypass banner** (unlike `web`'s app-shell banner) — see
   this section's own "Two entry points" note above for why that is a
   scope decision, not a gap this WP failed to close.
4. **`clearAndSetSemantics` on the badge icon has not been heard by a real
   screen reader** — proven only by inspection that the equivalent web
   `aria-hidden` treatment exists for the same reason, same honest caveat
   every other icon-decorative-vs-accessible-text call in this app carries
   until a device pass.

## Release build, signing, distribution, and carry-over cleanup (WP 4b.9)

Last per `docs/WORKPACKAGES.md` Phase 4b ("4b.9 — last"). Two independent
halves: a real release-signing story (never a secret in the repo), and the
carry-over list `docs/WORKPACKAGES.md`'s Phase 4b header names by name.

**Opus review verdict: PASS, no blockers (joint review with WP 4b.10).**
The key-material audit (extension sweep, ignored files,
`app/app/build/`, `app/.gradle/`, every git-object-store ref including
unreachable blobs, stash, staged, `~/.android/`, the toolchain dir) found
no keystore, signed artefact, password, alias, or store path anywhere in
the tree or its history. Four should-fixes landed, called out inline at
each fix site below (S1: `packageRelease`/`packageReleaseBundle` bypassed
the signing guard; S2: the missing persistent bypass banner needed its
own recorded-divergence entry, see the WP 4b.10 checkbox note and
`docs/WORKPACKAGES.md`'s Phase 4a divergence cluster; S3: `.gitignore`
widened for `*.p12`/`*.pfx`/`*.bks` plus a root-level belt; S4: the
self-contradictory "no keystore, anywhere, ever" line below), plus a
handful of nits (stale KDoc cross-references, an AGP v2/v3-default
overclaim, a stale WORKPACKAGES.md truncation-marker line, `IdentityScreen
.kt`'s WP 4b.3 directory tree left un-annotated after its WP 4b.9 move).

### Release signing (`app/app/build.gradle.kts`, `app/keystore.properties.example`)

**Never a secret in the repo, by construction, not by discipline.** Values
come from a gitignored `app/keystore.properties` file (the pattern was
already reserved in `app/.gitignore` since WP 4b.1 — `*.jks`/`*.keystore`/
`keystore.properties` — this WP is the first to actually wire it up) or
from environment variables (`VAULT_RELEASE_STORE_FILE`/
`VAULT_RELEASE_STORE_PASSWORD`/`VAULT_RELEASE_KEY_ALIAS`/
`VAULT_RELEASE_KEY_PASSWORD`) as a CI-style fallback — the properties file
wins when both are present (`releaseSigningValue`'s Elvis chain). This
app **never generates a keystore itself** — see the walkthrough below for
the `keytool` command the user runs. No keystore, password, or alias
appears anywhere in this tree, in a test fixture, or in git history from
this WP.

**Release version comes from the environment (WP APP-FIX-1 S5/S7).**
`versionName`/`versionCode` were literals (`0.1.0` / `1`), so every
published APK carried the same version regardless of tag. `build.gradle.kts`
now reads `VAULT_RELEASE_VERSION_NAME` and `VAULT_RELEASE_VERSION_CODE`
(`System.getenv`), falling back to exactly those literals when unset or
blank, so a local `assembleDebug` is unchanged. A code that is not a
positive integer fails Gradle configuration with the offending value in
the message — never a silent `1`. `.github/workflows/publish.yml` sets
both in the `env:` of its `gradlew assembleRelease (signed)` step: the name
from the release tag (computed by the `Compute release version (sanitized)`
step, which only computes it), the code from `github.run_number`. Caveat: `github.run_number` restarts if
the workflow is renamed or recreated, and Android rejects the lower
`versionCode` as a downgrade — bump past the old value before that happens.

**A missing/incomplete config is an immediate, actionable build failure —
never a crash, never a silently unsigned APK.** `assembleDebug` needs
none of this and is completely unaffected (debug builds keep using AGP's
own auto-generated debug keystore, untouched); `gradle.taskGraph.whenReady`
checks specifically for `assembleRelease`/`bundleRelease`/`installRelease`/
`packageRelease`/`packageReleaseBundle` in the resolved task graph (by
NAME, not by any looser "contains Release" match —
`test`/`testReleaseUnitTest`/`lintDebug`/`compileReleaseKotlin` all need no
signing config at all and must keep working with zero keystore setup) and
throws a `GradleException` with the exact fix (create the properties file
from the committed template, or set the four env vars) before any
compilation or packaging work starts.

**Review round 1 finding (Opus), fixed here.** The first version of this
guard only listed `assembleRelease`/`bundleRelease`/`installRelease` —
`assembleRelease`/`bundleRelease` are aggregate lifecycle tasks that
DEPEND ON `packageRelease`/`packageReleaseBundle`, but running
`./gradlew packageRelease` (or `packageReleaseBundle`) DIRECTLY bypassed
the guard entirely: measured, `BUILD SUCCESSFUL`, producing
`app/build/outputs/apk/release/app-release-unsigned.apk`. Severity was
bounded even before the fix — the artefact is self-labelled `-unsigned`,
`apksigner` refuses it, and there is no silent fallback to the debug key —
but the absolute claim above was falsifiable in one command, exactly the
`docs/LEARNINGS.md` pattern this project keeps re-learning ("Entry-point
docs describe SHIPPED behavior... grep the code... before claiming it
works"). Both task names are now in the guarded set; `installRelease`
stays listed too even though it is currently inert on its own (AGP will
not even create an install task for an unsignable variant, and once
signing IS configured this guard can never fire for it) — kept as a
forward-compat belt, not a live guarded path today. Re-verified after the
fix, directly against the exact task that bypassed the guard before:

```
$ ./gradlew.bat packageRelease            # no keystore.properties, no env vars
...
FAILURE: Build failed with an exception.
* What went wrong:
Release signing is not configured -- refusing to build an unsigned release artefact.
...
BUILD FAILED in 15s
$ ./gradlew.bat packageReleaseBundle      # no keystore.properties, no env vars
...
FAILURE: Build failed with an exception.
* What went wrong:
Release signing is not configured -- refusing to build an unsigned release artefact.
...
BUILD FAILED in 1s
```

Verified empirically, not just by reading the script (`docs/LEARNINGS.md`
"verify empirically"):

```
$ ./gradlew.bat assembleRelease          # no keystore.properties, no env vars
...
FAILURE: Build failed with an exception.
* What went wrong:
Release signing is not configured -- refusing to build an unsigned release artefact.
...
BUILD FAILED in 1s
```

— fails in ~1 second, before `compileReleaseKotlin` even runs. With a
throwaway test keystore (`keytool`-generated, used ONLY for this
verification and deleted immediately afterward, never committed):

```
$ ./gradlew.bat assembleRelease          # app/keystore.properties present
...
BUILD SUCCESSFUL in 20s
48 actionable tasks: 33 executed, 15 up-to-date
$ apksigner verify --verbose app/app/build/outputs/apk/release/app-release.apk
Verifies
Verified using v2 scheme (APK Signature Scheme v2): true
Number of signers: 1
```

— a genuinely signed APK, confirmed with `apksigner` (the AGP-bundled
`build-tools` verifier), not just "the task didn't error".
`./gradlew.bat assembleDebug test lintDebug` was re-run with NO keystore
configured immediately after, green (74 actionable tasks, 37 executed / 37
up-to-date), proving the debug/test/lint paths are genuinely unaffected by
any of this rather than accidentally passing because the test keystore was
still in place.

**Creating the keystore (the user runs this, once, and keeps the file and
its passwords somewhere durable and backed up — losing it means every
future release build can never again be recognized as an update to the
same app by any device that installed an earlier signed build):**

```bash
keytool -genkeypair -v \
  -keystore /absolute/path/to/steamhangar-release.jks \
  -keyalg RSA -keysize 2048 -validity 10000 \
  -alias steamhangar
```

`keytool` (bundled with any JDK — this project's own pinned JDK 17 works)
prompts for a keystore password, a key password, and the certificate's
distinguished-name fields (name/org/city/state/country) — none of it needs
to be a real identity; it is a self-signed certificate purely to prove
"every release build claiming to be this app came from the same key",
which is all Android's package-signature check actually verifies. `-validity
10000` (~27 years) avoids the certificate itself expiring mid-project;
Android does not currently reject an expired signing certificate for
already-installed apps, but starting fresh with a long validity avoids the
question entirely.

**Building and verifying a release APK:**

1. Copy `app/keystore.properties.example` to `app/keystore.properties`,
   fill in `storeFile`/`storePassword`/`keyAlias`/`keyPassword` from what
   `keytool` just created (or export the four `VAULT_RELEASE_*` env vars
   instead — see the properties file's own comment for the exact names).
2. `./gradlew.bat assembleRelease` from `app/` (the same wrapper every
   other command in this README uses). The signed APK lands at
   `app/app/build/outputs/apk/release/app-release.apk`.
3. Verify the signature independently of Gradle's own claim:
   `<sdk>/build-tools/<version>/apksigner verify --verbose app-release.apk`
   should print `Verifies` and `Verified using v2 scheme (APK Signature
   Scheme v2): true` — measured against this project's own pinned AGP
   8.7.3/compileSdk 35 defaults (v3/v3.1/v4 all report `false` on the
   verification transcript below; do not expect them without explicitly
   opting in via `signingConfigs`). A bare `jarsigner -verify` also works
   but only checks the older v1/JAR scheme, which this build does not
   produce at all (`Verified using v1 scheme (JAR signing): false`) —
   `apksigner` is the correct tool, not `jarsigner`, for exactly that
   reason.

**No Play Store requirement; F-Droid is a long-term goal, per
`docs/WORKPACKAGES.md` Phase 4b's own line item ("APK build docs; no Play
Store requirement; F-Droid long-term").** This WP ships a signed,
side-loadable APK and the documentation to build one — it does NOT set up
a Play Console listing (Google account, store presence, review process —
an account-level, with-the-user decision this project's own working
agreement reserves for the user, same class of decision as WP 5.5's GitHub
org move) or an F-Droid metadata/build-recipe submission (F-Droid's own
reproducible-build requirements are a real, separate piece of work — a
future WP once the app is otherwise stable, not a WP 4b.9 packaging
after-thought).

**Distribution (WP CI-3, `.github/workflows/publish.yml`): download the
signed APK from the GitHub Release for the version tag, not a manual
transfer.** The steps above (`assembleRelease` + `apksigner verify`) are
what CI itself runs, driven by four secrets an operator sets once in the
`release` Environment (see "CI signing secrets" below):
`ANDROID_KEYSTORE_B64` (base64 of the release `.jks` file's raw bytes),
`ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, and
`ANDROID_KEY_PASSWORD` (the CI-side names for the same four values
`storeFile`/`storePassword`/`keyAlias`/`keyPassword` above — see that
workflow file's own comments for the exact env-var wiring). Pushing a git
tag matching `v*` builds, signs, `apksigner`-verifies, and attaches
`steamhangar-app-<tag>.apk` (e.g. `steamhangar-app-v1.2.3.apk`) to that
tag's GitHub Release — a plain, no-account-needed download from the
repository's Releases page. A manual `workflow_dispatch` run never
attaches anything (the release-attach step guards on
`github.event_name == 'push'`), and from a branch it cannot sign either:
the job only requests the `release` Environment on a `v*` tag ref, so a
branch run sees no keystore and takes the green skip. If
`ANDROID_KEYSTORE_B64` is not available to the run, the job skips the
build entirely (a documented, green skip, not a failure) rather than ever
publishing an unsigned artefact; if it IS set but one of the other three
secrets is missing or wrong, this project's own signing guard
(`gradle.taskGraph.whenReady` above) turns that into a loud CI failure
instead — the same `GradleException` a local build with an incomplete
`keystore.properties` would hit, not a silent partial success. Side-loading
(ADB, a file share) remains available for a
locally-built APK — nothing above requires CI — but it is no longer the
documented distribution path for a tagged release; "install unknown apps"
still needs to be allowed for whichever source (a browser download, ADB)
delivers the APK to the device, the standard side-load flow for any
Android app outside a store.

#### CI signing secrets (one-time setup in GitHub settings)

The keystore is the one secret whose loss or leak cannot be repaired for
existing installs, so it must be reachable only by a release tag, not by
any branch that can start the workflow. The workflow side is done
(`environment:` on the `android-release` job); the repository side is a
maintainer task in **Settings**:

1. **Environments → New environment `release`.** Under *Deployment
   branches and tags*, choose *Selected branches and tags* and add one
   **tag** rule with the pattern `v*`, no branch rules. A job on any
   other ref is then refused entry to the Environment and never receives
   its secrets.
2. **Put the four `ANDROID_*` secrets in that Environment** (*Environment
   secrets*), and **delete them from Secrets and variables → Actions →
   Repository secrets** if they were set there earlier — a repository
   secret is visible to every workflow run on every branch, which is
   exactly what the Environment rule exists to prevent.
3. **Rules → Rulesets → New tag ruleset**, target `v*`, restrict
   *creations* (and updates and deletions) and add only the maintainers
   to the bypass list. Without it, anyone with write access could push a
   `v*` tag on an arbitrary commit and the Environment rule would admit it.

**Verifying a downloaded APK.** Each signed release lists the signing
certificate's SHA-256 in the release body ("Verify this release") and in
the workflow run summary. Compare it with
`apksigner verify --print-certs steamhangar-app-<tag>.apk`, line
`certificate SHA-256 digest`. Its label depends on the build-tools
version: current ones print `V2 Signer: certificate SHA-256 digest: …`,
older ones `Signer #1 certificate SHA-256 digest: …` (the SHA-1 and MD5
lines next to it are not the fingerprint). The release signing
certificate, measured on the first signed release build (tag
`v0.1.0-rc10`), has the SHA-256

```
f095d5abda5acb23f3f07c9fa68a5526a514f1f4e600fca17162aa8d17c66172
```

Every later release must show the same value — a different one means a
different key, and Android will refuse it as an update.

### Carry-over cleanup (`docs/WORKPACKAGES.md` Phase 4b header)

Each item below was individually re-verified against the actual code
before doing anything — `docs/LEARNINGS.md`'s standing rule ("Entry-point
docs describe SHIPPED behavior... grep the code... before claiming it
works") applies just as much to a carry-over TODO list as to a README.

1. **Move the unreferenced `GalleryScreen` (4b.1) and `IdentityScreen`
   (4b.3, superseded by `SettingsScreen` in 4b.7) to `src/debug/`, keeping
   their tests working.** `GalleryScreen` was **already moved** to
   `app/app/src/debug/java/dev/steamvault/app/ui/gallery/GalleryScreen.kt`
   during WP 4b.4 itself (that WP's own review nit — see the file's kdoc,
   unchanged by this WP; `git log` confirms the move landed in the WP 4b.4
   commit, not here) — the carry-over list was simply not updated to drop
   it once done. **`IdentityScreen` is moved by this WP**, to
   `app/app/src/debug/java/dev/steamvault/app/ui/identity/IdentityScreen.kt`
   — `src/debug/` is AGP's standard "compiled for debug builds only"
   source set (already proven correct by `GalleryScreen`'s own move; no
   `sourceSets {}` block is needed in `build.gradle.kts`, AGP wires
   `src/debug/java` to the debug variant by convention), so this
   now-unreferenced screen is excluded from the signed `release` variant
   by construction rather than shipping dead UI code into it. **No test
   referenced `IdentityScreen` directly, before or after the move** — it
   was never unit-tested on its own (only `dev.steamvault.app.repo
   .SteamIdentityState`/`SteamIdentityRepository`, which live under
   `repo/`, untouched by this move, and are covered by
   `SteamIdentityRepositoryTest`) — so "keeping tests working" was
   satisfied by construction; `./gradlew.bat test` (550/0/0, see below)
   confirms nothing broke.
2. **Re-check the `security-crypto` 1.1.0-alpha pin for a GA release —
   report, recommend, do not silently bump.** A GA release now exists:
   `androidx.security:security-crypto:1.1.0` (verified against Google's
   Maven metadata AND by downloading the `.aar` and listing its
   `classes.jar` — `MasterKey`/`MasterKey.Builder`/
   `EncryptedSharedPreferences` are all present, the same classes
   `EncryptedCredentialStore.kt` already depends on). **Not bumped here**
   — see `gradle/libs.versions.toml`'s expanded comment for the full
   finding and the explicit recommendation (bump in its own reviewed
   change once a device is available to re-verify
   `EncryptedCredentialStore` against a real Keystore on the new artifact
   — this environment has never been able to run that check at all, for
   any version, and a credential-storage dependency bump is exactly the
   wrong place to skip it).
3. **Pin the Kotlin `LogExcerpt` truncation marker to position 0
   (`startsWith`, not `contains`) — the web twin was pinned in 4a.8, this
   is the open half.** Verified against the actual code: this was
   **already correct and already pinned** —
   `ui/downloads/logic/LogExcerpt.kt`'s `selectExcerptDisplay` has used
   `text.startsWith(TRUNCATION_MARKER)` since WP 4b.5 (never `contains`),
   and `LogExcerptTest`'s `` `a truncation marker appearing mid-stream, not
   at position 0, is not treated as truncation` `` test already exercises
   the exact `startsWith`-vs-`contains` mutation this carry-over item
   describes, with the MUTATION TARGET comment spelling out why. This
   matches `docs/PROJECT_PLAN.md` §7's own WP 4b.5 entry, which already
   says "truncation marker pinned to position 0" in its evidence note —
   the carry-over line in `docs/WORKPACKAGES.md` was stale from before
   that fix landed and was never removed once it did. **No code change
   needed; the carry-over list entry is removed below as closed.**
4. **Notification icon art: 4b.8 reuses the launcher monochrome — either
   provide something better, or record explicitly why reuse is acceptable
   for v1.** No image-generation/design tooling exists in this
   environment to "provide something better" honestly, so this WP makes
   the earlier WP 4b.8 note a FINAL decision rather than leaving it as a
   deferred "revisit" pointing at this exact WP: **kept for v1.**
   Rationale, restated as a decision and not just an observation: the
   launcher's monochrome layer (`ic_launcher_monochrome.xml`) is already a
   pure white-on-transparent silhouette at exactly the visual weight a
   status-bar/notification small icon needs — Android's own guidance for
   notification icons IS a flat white silhouette, which is precisely what
   this asset already is, not merely a convenient reuse of unrelated art.
   A bespoke glyph remains a legitimate future polish item (tracked as a
   general "art pass" item, not a blocking WP 4b.9 carry-over) but is not
   needed for a correct, guideline-compliant v1 notification.

### Updated honest on-device list

WP 4b.10 adds (see that section above for the full list): `ModalBottomSheet`
rendering/dismiss for the clients sheet specifically, a real
`bypass_suspected` transition observed end to end from a live device
notification tap into the sheet, and `clearAndSetSemantics`'s screen-reader
behaviour on the badge icon.

WP 4b.9 adds exactly one item, and it is unavoidable in this environment:
**the entire release-signing walkthrough above (`keytool` keystore
creation, `assembleRelease`, `apksigner verify`) has been run and proven
end-to-end in THIS environment with a throwaway test keystore — but never
with a real device installing the resulting APK.** Everything Gradle/
`apksigner` can attest to (the artefact is genuinely signed, with the
right scheme, and the failure path is a clean Gradle error rather than a
crash or a silent unsigned build) is proven above; whether the resulting
APK actually installs and launches on a real device (`adb install`, "install
unknown apps" permission flow, first-launch behaviour) is not, for the same
"no emulator/device available" reason every earlier 4b.x WP records.

### Tests (WP 4b.9)

No new test files — this WP is packaging/build-config/doc/reorg work, not
a new logic module. `LogExcerptTest` and `SteamIdentityRepositoryTest`
(both pre-existing) continue to cover the two carry-over items that turned
out to already be closed; the `IdentityScreen` move touches no test file
at all (see carry-over item 1 above). Full suite, run from a clean state
after the `IdentityScreen` move and the signing-config change, with no
`keystore.properties` present:

```
$ ./gradlew.bat test lintDebug assembleDebug
...
BUILD SUCCESSFUL in 24s
74 actionable tasks: 21 executed, 53 up-to-date
```

550/0/0 (same count as WP 4b.10's — this WP adds no new JVM tests, and the
`IdentityScreen` move/signing-config work changes zero existing test
outcomes); `app/app/build/reports/lint-results-debug.txt`: "No issues
found." `assembleRelease` behaviour (fail-without-keystore, succeed-and-
verify-with-one) is proven separately above rather than folded into this
count, since it is a build-configuration property, not a JVM test.

### What WP 4b.9 deliberately did NOT do

- **No keystore is generated by the build, ever** — the whole point of the
  design; the `keytool` command above is written for the USER to run,
  with their own passwords, on their own machine. Review fix (S4): the one
  throwaway keystore behind the verification transcript above was created
  OUTSIDE the repo, used only to prove the signing path works, and deleted
  in-session — reworded here because the original "anywhere, ever"
  phrasing was a literally false statement about the one section a reader
  consults for exactly this question, even though the intent (the SHIPPED
  build/config never generates one) was always correct.
- **No Play Store listing, no F-Droid submission** — see the "No Play
  Store requirement" note above for why both are separate, later,
  with-the-user (or metadata-heavy) efforts, not this WP's job.
- **No bespoke notification icon art** — decided, not merely deferred
  again (see carry-over item 4); revisit only if a future design pass
  wants one, not because v1 is missing something guideline-required.
- **No `security-crypto` version bump** — reported and recommended, per
  the brief's explicit instruction not to silently bump a security
  dependency; see carry-over item 2 and `gradle/libs.versions.toml`'s
  comment for the full finding.
- **No on-device install/launch verification of the signed release APK**
  — see "Updated honest on-device list" above; unreachable in this
  environment for the same reason every earlier 4b.x WP records.

## "Check & update all cached games" — the Android trigger (Phase 4c, WP 4c-app)

Android twin of the web frontend's WP 4c-web trigger (`docs/WORKPACKAGES.md`'s
recorded divergence entry, both of whose decisions this WP adopts verbatim —
see that entry's own "Adopted verbatim on Android" addendum). Library gains
an end-aligned "Check & update all cached games" button inside its own
`fillMaxWidth()` row (`ui/library/LibraryScreen.kt`'s `CheckAndUpdateRow`),
placed directly below the search/layout toolbar. Stated precisely rather
than as "a full-width header row" (Opus review, this WP): the row's
CONTAINER spans the width, the button itself does not — unlike web's own
`width:100%` button — and the placement order differs from web's too (web:
tools → check row → search; Android: search → layout/select → check row).
It calls the new `POST /v1/prefill/cached`
(`VaultApiClient.prefillCached()` — `postEmpty`, the SAME no-body plumbing
`pauseJob`/`resumeJob` already use, since api/README.md documents that any
body this route receives is silently accepted and ignored). The existing
per-game and multi-select download/delete paths are unchanged.

```
app/app/src/main/java/dev/steamvault/app/
├── net/VaultApiClient.kt          # + prefillCached()
├── repo/JobsRepository.kt         # + prefillCached()
└── ui/library/
    ├── logic/CachedPrefillOutcome.kt   # the ported pure module (below)
    ├── LibraryController.kt            # + checkAndUpdateCachedGames(), LibraryToast
    └── LibraryScreen.kt                # + CheckAndUpdateRow, warn-aware Snackbar
```

### `ui/library/logic/CachedPrefillOutcome.kt` — verbatim port

A line-for-line Kotlin port of `web/js/lib/cached-prefill-outcome.js`'s
five functions, ported rather than re-derived (the module kdoc quotes the
web source's own header near-verbatim): `partitionCachedPrefillOutcome`
(four buckets — `queued`/`alreadyQueued`/`alreadyRunning`/`alreadyPaused`,
with a `deduplicated: true` entry whose status is neither `paused` nor
`queued` landing in `alreadyRunning` as a catch-all, never dropped, the
WP 4b.5 lesson pinned on both frontends), `countForcedCachedGames` (scoped
to the `queued` bucket only — never the whole `GET /v1/games` snapshot,
the web port's own review round 1 blocker), `summarizeCachedPrefillOutcome`
(the wording composer, `warn` true if-and-only-if a paused dedupe is
present), `describeCachedPrefillError` (the mid-loop-5xx "re-read
`GET /v1/jobs`" signal), and `CheckAndUpdateAction` (the run-at-most-once
in-flight guard).

**String-resource exception invoked, same as `BulkPlan.kt`/`LibraryFilters.kt`
(see "String resources" above).** Every message literal
`summarizeCachedPrefillOutcome`/`describeCachedPrefillError` builds stays a
Kotlin string in this file, not a `strings.xml` resource — the wording is
"whatever `web/js/lib/cached-prefill-outcome.js` already decided", and
`CachedPrefillOutcomeWordingContractTest` pins every one of them by
hand-transcribed literal string equality, the condition the exception
requires. The button label that triggers the action
(`library_check_update_button`/`_busy`) is ordinary static UI chrome and
stays in `strings.xml` — it is an independent Android wording decision
(worded to match the project-wide honesty rule, not lifted from one web
literal), not the narrow exception.

`LibraryController.checkAndUpdateCachedGames` is the stateful glue:
`checkAndUpdateBusy` (a Compose-observable flag `LibraryScreen.kt` uses to
disable the button and swap its label) plus a private `CheckAndUpdateAction`
instance. Unlike web's module-level singleton guard, this instance lives on
the `LibraryController` itself, which does not survive leaving the Library
tab (this class's own kdoc: not a ViewModel, `remember`-scoped) — an
honest, documented narrowing from web's cross-remount guarantee, not a
silent gap: server-side dedupe (`enqueue_prefill`) still makes a second
concurrent call harmless, it would just not paint as `busy` across a
tab-leave-and-return. `LibraryToast` (message + `warn` + `durationMs`)
replaced the previous bare-`String?` toast state so this action's paused
outcome can use the longer 6 s duration web's `CHECK_UPDATE_WARN_TOAST_MS`
uses, while every pre-existing toast (queued/paused/resumed/freed/action
failed) keeps its prior wording and the prior 2500 ms cadence unchanged.

### Tests (WP 4c-app)

42 new/changed JVM unit tests (592 total with the prior WPs' 550 baseline),
no Robolectric/emulator dependency:

- `ui/library/logic/CachedPrefillOutcomeTest` (28) — the four-bucket
  partition including the unknown-status catch-all and the null-input
  defensive case; both ported BLOCKER REGRESSION cases by name (an empty
  response plus a stale `needs_force` game still reads EXACTLY "Nothing
  cached to check.", and an all-deduplicated response with a forced app
  among them credits no forced note); a THIRD forced-note MUTATION PIN
  (S1, Opus review round on this WP) for the scoping half specifically —
  one fresh, non-forced app queued alongside an unrelated `needs_force`
  app elsewhere in the snapshot must NOT produce a forced note, closing
  the gap where replacing `countForcedCachedGames(p.queued, games)` with a
  whole-snapshot count passed every other test in the file because the
  `p.queued.isNotEmpty()` gate masked it; the explicit MUTATION PIN that an
  `alreadyQueued`-only outcome does not warn (the gap the web port shipped
  without — widening `warn`'s expression to OR in `alreadyQueued` would
  pass every other test in this suite too, so this is its own standalone
  assertion); the paused-never-worded-as-queued/started pin; and
  `CheckAndUpdateAction`'s in-flight guard with the call count asserted
  SYNCHRONOUSLY via `testScheduler.runCurrent()` before the gating
  `CompletableDeferred` resolves, so a removed guard fails the assertion
  immediately instead of the suite hanging.
- `ui/library/logic/CachedPrefillOutcomeWordingContractTest` (12) — one
  literal, hand-transcribed string-equality test per wording shape
  (empty/single-new/multiple-new/already-queued/already-running/
  single-paused/plural-paused/mixed-bucket-join-order/forced-note-suffix/
  forced-note-pluralization/SERVER-error/generic-fallback), never derived
  from `CachedPrefillOutcome.kt`'s own templates — the same technique
  `StatusIconCrossFrontendContractTest` applies to `StatusKind`.
- `net/VaultApiClientTest` (+2, one widened) — `prefillCached()` sends a
  genuinely empty body (`recorded.bodySize == 0L`) to `/v1/prefill/cached`,
  never an `{"appids": [...]}` shape, and decodes both a populated and an
  empty (`[]`) `202` response. Widened per N4 (Opus review round on this
  WP): also asserts `X-Api-Key` is present, `Content-Length: 0`, NO
  `Content-Type` header at all (distinct from a blank one —
  `ByteArray(0).toRequestBody(null)` means OkHttp never adds the header,
  since there is no `MediaType` to derive it from), and that
  `followRedirects`/`followSslRedirects` are still `false` on the built
  client (`debugHttpClientForTesting`, the same WP 4b.2 S1b technique) —
  pinned directly rather than left to a review transcript that no longer
  exists once this lands. The redirect posture matters specifically
  because this route has no trailing slash (api/README.md: the slash form
  is a `307`), and this client would refuse to follow it either way.

**M5 mutation, verified by name (Opus review round on this WP):**
temporarily replacing `countForcedCachedGames(p.queued, games)` in
`CachedPrefillOutcome.kt` with `(games ?: emptyList()).count { it.needs_force }`
(a whole-snapshot count, no scoping) and re-running
`CachedPrefillOutcomeTest` in isolation fails exactly the new
`MUTATION PIN -- the forced note is scoped to the queued bucket, not the
whole games snapshot` test (1 of 28 in the file) — every other test in the
class stays green under the mutant, confirming the gate alone was doing
all the previous work. Reverted before this report; not left in the tree.

Verified command + output tail (this environment's JDK 17/Android SDK were
provisioned fresh for this WP — see the toolchain note below):

```
$ ./gradlew.bat --stop && ./gradlew.bat test lintDebug assembleDebug
...
BUILD SUCCESSFUL in 1m 29s
74 actionable tasks: 74 executed
```

(`--stop` first, then every task actually executed rather than
up-to-date-skipped, per the same "verify empirically" discipline — a stale
Gradle daemon had one file lock issue against a bare `clean` in this
environment, unrelated to the source changes, worked around by stopping
the daemon rather than trusted away.)

`592 tests completed, 0 failed` (both `testDebugUnitTest` and
`testReleaseUnitTest` XML reports: `tests="592" skipped="0" failures="0"
errors="0"`, summed across all test classes);
`app/app/build/reports/lint-results-debug.txt`: "No issues found";
`assembleDebug` produced `app/app/build/outputs/apk/debug/app-debug.apk`.

**Toolchain note for this WP's own verification, not a project decision:**
this particular environment had neither a JDK nor an Android SDK installed
(unlike the "pinned local toolchain" the "Toolchain setup" section above
otherwise assumes exists already) — a Temurin 17.0.20 JDK (matching the
version already pinned there) and `platform-tools`/`platforms;android-35`/
`build-tools;35.0.0` were downloaded into scratch locations outside the
repo for this one verification run, matching every version this file
already pins. No repo file changed as a result beyond the machine-specific,
gitignored `app/local.properties` `sdk.dir` entry every contributor
generates locally per the "Toolchain setup" instructions above.

### Two findings recorded, not fixed, here (WP 4c-app brief)

- **No `BackHandler` anywhere in `app/`, and its actual effect is stronger
  than "does not leave multi-select" (corrected by Opus review, this WP —
  verified against `MainActivity.kt` at HEAD, not assumed).**
  `MainActivity.destination` (`ui/nav/Destination.kt`) is a plain
  `mutableStateOf` switch with no `NavHost` and no back stack entries at
  all — the twin of a bug the WP 4a.8 web review found and fixed there
  (native `inert`/`Escape` handling for modals), but never ported to this
  app's navigation model. `ModalBottomSheet` (`GameDetailSheet.kt`,
  `ClientsSheet.kt`) DOES consume the back gesture while a sheet is open —
  that composable registers its own back handling internally — but with no
  sheet open and no `BackHandler` registered anywhere else, the system back
  gesture/button falls all the way through to the platform default for a
  single Activity with an empty back stack: **it finishes the Activity**,
  i.e. exits the app. It neither leaves multi-select
  (`LibraryController.selecting`/`exitSelect()`) nor returns to the
  previously active [Destination] tab — there is no stack entry for either
  to pop to. A future WP should wire a
  `BackHandler(enabled = controller.selecting) { controller.exitSelect() }`
  in `LibraryScreen.kt` for the multi-select case, and separately decide
  whether tab switches should push a back-stack entry at all (a plain state
  switch was a deliberate WP 4a.1-era choice for this app, per
  `ui/nav/Destination.kt`'s own kdoc — reversing it is a bigger call than
  this one-line fix and is not assumed here).
- **The Android queue row (`ui/downloads/DownloadsScreen.kt`'s `QueueRow`)
  has no grip glyph at all, decorative or otherwise** — checked against the
  WP 4c-app brief's claim that it "mirrors a web grip with no drag
  handler": `web/js/views/downloads.js`'s queue row DOES render one
  (`GRIP_SVG`, purely decorative — no reorder functionality on either
  frontend today), but the current Android `QueueRow` is just
  `"#position  name"` plus a remove button, with no grip element to mirror
  in the first place (verified by reading both files at HEAD; not
  guessed). Recorded as the accurate version of that finding for whichever
  later package next touches `QueueRow` — either add the equivalent
  decorative glyph for visual parity with web, or note explicitly that
  Android intentionally omits it.

## Steam library via the vault relay, superseding on-device GetOwnedGames (WP 4h.4)

**The decision, in one sentence:** the Android app's Steam library and
persona data now come exclusively from vault-api's own relay
(`GET /v1/steam/owned-games`, `GET /v1/steam/player-summaries` — the SAME
two endpoints the web UI has used since the WP 4a.6r/ADR-0004 first
addendum), never directly from Valve, and there is no fallback. See
`docs/adr/0004-steam-credentials-never-touch-steamvault.md`'s second
addendum for the full "why now, why no fallback" reasoning; this section
is the "what actually shipped" companion.

**What this deleted outright (not hidden behind a flag):**

```
app/app/src/main/java/dev/steamvault/app/
├── net/steam/SteamWebApiClient.kt        # REMOVED — direct-to-Valve GetOwnedGames/GetPlayerSummaries
├── net/steam/SteamWebApiKeyInput.kt      # REMOVED — 32-hex key-entry validation + field-clearing
├── net/model/SteamWebApi.kt              # REMOVED — hand-parsed OwnedGame/SteamPersona DTOs
└── ui/identity/IdentityScreen.kt         # REMOVED (was src/debug/, already unreachable since WP 4b.7)
```

Plus every accessor/field/UI control that only existed to serve the
device-local key: `CredentialStore.getSteamWebApiKey`/`setSteamWebApiKey`
(and `EncryptedCredentialStore`'s backing pref entry),
`SteamIdentityState.hasWebApiKey`, `SteamIdentityRepository.setWebApiKey`,
`OnboardingController`/`SettingsController`'s `webApiKeyInput`/
`webApiKeyError`/`submitWebApiKeyEntry`/`removeWebApiKey`, and the
matching `OutlinedTextField`/button/masked-display Compose blocks in
`OnboardingScreen.kt`'s Step 2 and `SettingsScreen.kt`'s Steam-identity
section.

**Migration note, corrected (review catch — the first draft of this note
was wrong).** An install that already had a value under the now-removed
`steam_web_api_key` `EncryptedSharedPreferences` entry does NOT keep it
sitting unread forever — that was this WP's own first-draft argument, and
it does not survive contact with ADR-0010's logic applied to a credential
instead of a privacy flag: a credential nobody is ever prompted to revoke
is a real ongoing risk, not a harmless orphan. `CredentialStore.kt`'s
`legacyPrefKeysToScrub` runs once at construction in every implementation
— an upgrading install has the key actively removed the next time its
`CredentialStore` is constructed (in practice: the next app launch), and
`EncryptedCredentialStore.clearSteamIdentity` (Settings' sign-out) removes
it too, belt-and-suspenders.

**Precisely what is pinned where, and how (review round 2 correction — an
earlier version of this paragraph overstated the production half as
"actual behavioral pin," which was never true).** The JVM-testable
`InMemoryCredentialStore` fixture's copy of this migration IS behaviourally
pinned: `InMemoryCredentialStoreTest`'s `MUTATION PIN -- construction
scrubs an existing install's legacy Steam Web API key` constructs a real
store instance over seeded data and observes the key gone afterward. The
PRODUCTION copy — `EncryptedCredentialStore`'s real `init` block and its
`clearSteamIdentity`'s restored removal line — cannot be exercised the
same way: this class needs a real Android Keystore, unavailable off-device
(the same JVM constraint every other guarantee in this class carries), so
sharing `legacyPrefKeysToScrub` between the two closed only the
LOGIC-drift half of the "don't pin the fake" rule — the CALL-SITE-EXISTENCE
half was still open, and a diff that deleted BOTH the `init` block and the
restored `clearSteamIdentity` line passed the entire suite (577/0, both
variants — measured, not assumed). `EncryptedCredentialStoreSourceTest.kt`
closes that gap the same way its other three guarantees are already
pinned — STRUCTURALLY, by reading the class's own source text —
with a fourth assertion, `calls the shared legacy-key scrub at
construction and on sign-out`, checking both `legacyPrefKeysToScrub(`
and `editor.remove(LEGACY_STEAM_WEB_API_KEY_PREF_NAME)` are present.
Mutation-verified independently: deleting the `init` block alone fails
that test; separately reverting and deleting only `clearSteamIdentity`'s
removal line also fails it; both reverted before this report.

**Upgraders who want the STRONGER guarantee of revoking the key on
Valve's side, not just deleting it from this app, should do so directly
at <https://steamcommunity.com/dev/apikey>** — this app deleting its own
copy does not by itself invalidate the key at Valve.

**What replaced it:**

```
app/app/src/main/java/dev/steamvault/app/
├── net/
│   ├── VaultApiClient.kt                       # + steamOwnedGames()/steamPlayerSummaries()
│   ├── model/SteamRelay.kt                     # OwnedGame/OwnedGamesRelayOut/PlayerSummaryEntry/
│   │                                           #   PlayerSummariesRelayOut/SteamPersona — plain
│   │                                           #   @Serializable DTOs, decoded through VaultJson
│   └── steam/VaultRelayLibraryFetcher.kt       # SteamLibraryFetcher's ONE production impl now
├── repo/SteamIdentityRepository.kt             # ownedGames()/refreshPersonaName(): no more
│                                                #   "no Web API key configured" precondition
└── ui/settings/
    ├── logic/SteamLibraryStatus.kt             # the first-class UI states (below)
    ├── SettingsController.kt                   # + checkSteamLibrary(), libraryStatus/libraryChecking
    └── SettingsScreen.kt                       # + the "Check library" button/status text
```

### `net/model/SteamRelay.kt` — DTOs, not a hand-parser

Unlike the deleted `SteamWebApi.kt` (which hand-walked
`kotlinx.serialization.json.JsonElement` field by field, because it had to
tolerate a genuinely hostile, unvalidated Valve response), the relay's
response is vault-api's OWN already-validated, already-whitelisted output
(`vault_api/steam_relay.py`/`routers/steam.py` do that validation
server-side) — so these are plain `@Serializable` data classes, decoded
through the same `VaultJson` instance (`ignoreUnknownKeys = true`) every
other `net/model` DTO uses, field names kept verbatim snake_case per this
package's own no-renaming-layer convention.

**One real behavior change worth naming plainly, not just a plumbing
swap.** The deleted `parseOwnedGames` skipped an individual malformed game
ENTRY (wrong type, boolean masquerading as an int) and kept the rest of
the list — a private-input-tolerant design appropriate for a genuinely
hostile, unvalidated Valve response. `OwnedGamesRelayOut`'s plain
`@Serializable` decode has no such per-entry fallback: one malformed entry
anywhere in `games` fails the WHOLE decode (a `SerializationException`
surfaces as `VaultApiError.Unknown`), same as every other list-shaped
`net/model` DTO in this client (`GameSummary`, `JobSummary`, etc., none of
which skip individual malformed rows either). This is deliberate,
fail-loud, and consistent with treating vault-api as a validated,
already-whitelisted source rather than a hostile one — but it is a real
trade against the old design's per-entry resilience, made because the
threat model changed (this app now trusts vault-api's own validation
instead of re-validating Valve's raw output itself), not an oversight.

**The one property that is NOT defensive boilerplate: `OwnedGame.playtime_forever`/
`rtime_last_played` both default to `null` (audit requirement, not a
nicety).** WP 4h.0's privacy gate (`ADR-0010`,
`vault_api/routers/steam.py`, `response_model_exclude_unset=True`) OMITS
either JSON key from the response ENTIRELY when its
`VAULT_RELAY_EXPOSE_*` switch is off — and both default OFF server-side,
so the shape with BOTH keys textually absent is what a default-configured
vault actually sends, not an edge case a defensive default merely
tolerates. `SteamRelayParsingTest`'s
`MUTATION PIN -- both playtime_forever and rtime_last_played ABSENT...`
fixture is built to match that real shape exactly (both keys absent, not
present-as-`null`) and dies by name if either field's default is removed
(`MissingFieldException`).

### `net/steam/VaultRelayLibraryFetcher.kt` — the new `SteamLibraryFetcher`

A thin adapter over `VaultApiClient.steamOwnedGames`/`steamPlayerSummaries`
— no retry logic, no caching (vault-api's own `RelayCache` already covers
that), no error translation: whatever `VaultApiClient` throws (a
[`VaultApiError`](#the-six-kind-error-taxonomy-neterrorvaultapierrorkt)
with `.status` set to `409`/`422`/etc.) propagates unwrapped, per the WP
brief's "whatever the app already does for other vault calls." The one
piece of actual logic is `getPlayerSummary`'s steamid cross-check
(mirroring `vault_api/steam_relay.py::parse_player_summaries`'s own rule
server-side — kept here too, `docs/LEARNINGS.md`'s "everything returned is
hostile input" applied one layer further out) and the `vaultApiClientProvider`
indirection: `SteamIdentityRepositoryImpl` (and the `SteamLibraryFetcher`
it defaults to) is constructed once, in `MainActivity`'s `by lazy`
wiring, potentially before any vault-api connection exists (Steam OpenID
sign-in is reachable during onboarding, unlike library fetching) — so the
CURRENT `VaultApiClient` is read fresh on every call via
`{ vaultApiClientState }`, the same "read fresh" pattern
`VaultApiClient`'s own `apiKeyProvider` already established.

### The private-profile trade-off — a real regression, named honestly

Under the OLD device-local design, each user's OWN Steam Web API key saw
their OWN library even behind a private Steam profile (Valve's stricter
checks apply against the CALLING key's own account). The relay uses ONE
operator-owned key for every signed-in user on a vault; Valve's
`GetOwnedGames` for a DIFFERENT SteamID answers with nothing at all
(`configured: true, game_count: 0`) unless that profile's game details
happen to be public — the identical wire shape a genuinely empty library
produces. `ui/settings/logic/SteamLibraryStatus.kt`'s
`MaybePrivateOrEmpty` state is the honest answer: it cannot and does not
try to tell the two causes apart, and names both in the rendered message
(`settings_steam_library_maybe_private`) rather than presenting an empty
shelf that reads as a bug. See the ADR-0004 addendum for why this is an
ACCEPTED cost, not an oversight.

### First-class UI states (`ui/settings/logic/SteamLibraryStatus.kt`)

Settings' Steam-identity section gained a "Check library" button (visible
only once signed in — there is nothing to check otherwise) plus a status
line, backed by `SettingsController.checkSteamLibrary()` →
`SteamIdentityRepository.ownedGames()` → `steamLibraryStatusFor(result)`.
Six states, each with its OWN string resource — never a generic
"something went wrong":

| State | Cause | String resource |
|---|---|---|
| `Unknown` | Not checked yet this session | `settings_steam_library_unknown` |
| `Ready(count)` | Ordinary success | `settings_steam_library_count` (plural) |
| `MaybePrivateOrEmpty` | `configured:true, game_count:0` | `settings_steam_library_maybe_private` |
| `RelayNotConfigured` | `409` — no key on vault-api | `settings_steam_library_not_configured` |
| `InvalidSteamId` | `422` — rejected steamid | `settings_steam_library_invalid_steamid` |
| `Failed(message)` | Anything else (network, `5xx`, no vault-api connection) | `settings_steam_library_error` |

`409`/`422` are both read off `VaultApiError.status` — the shared
`web/js/errors.js`-mirroring taxonomy folds both into the `validation`
kind, so `.status` (not the sealed subclass) is what actually
distinguishes them, exactly as `VaultApiClientTest`'s existing 409/422
tests already establish for every other endpoint.

### What did NOT change

- **Steam OpenID sign-in.** `net/steam/SteamOpenIdClient.kt`,
  `SteamOpenIdLoginUrl.kt`, `SteamOpenIdCallback.kt`, the per-login replay
  fix (`SteamLoginState.kt`/`PendingLoginState`) — all untouched. The app
  still never sees a Steam password; only the flow that fetches library
  DATA after signing in changed.
- **`SteamKeyIsolationTest`'s existence and purpose**, even though its
  invariants were rewritten — see its own kdoc for the three specific,
  narrower claims it pins now (no `getSteamWebApiKey`/`setSteamWebApiKey`
  anywhere, no direct-to-Valve Web API host reference anywhere,
  `VaultApiClient.kt` still ignorant of the OpenID identity classes even
  though it now correctly knows about the relay routes).

### Tests (WP 4h.4)

**578 tests (both `testDebugUnitTest`/`testReleaseUnitTest`, identical
counts — the deleted `IdentityScreen.kt` was `src/debug`-only and already
unreferenced), down from the WP 4h.1 baseline of 592 — net −14, reconciled
file by file below (review round 2 correction: an earlier version of this
paragraph printed 572/−20/39-deleted, an arithmetic error caught in
review — `SteamWebApiClientTest` genuinely has 9 tests, not 8 — and two
review-fix rounds below added 6 more tests total across
`InMemoryCredentialStoreTest`, `VaultApiClientTest`, and
`EncryptedCredentialStoreSourceTest` after the original count was
measured; every number here is checked against the actual counted `@Test`
methods, not re-guessed).**

Deleted outright, with their production classes (40 tests):

| File | Tests |
|---|---|
| `SteamWebApiClientTest` | 9 |
| `SteamWebApiParsingTest` | 22 |
| `SteamWebApiKeyInputTest` | 9 |
| **Total deleted** | **40** |

New or changed (26 tests, net):

| File | Before → After | Net |
|---|---|---|
| `SteamRelayParsingTest` (new) | 0 → 7 | +7 |
| `VaultRelayLibraryFetcherTest` (new) | 0 → 6 | +6 |
| `SteamLibraryStatusTest` (new) | 0 → 8 | +8 |
| `VaultApiClientTest` (+4 relay tests, +1 review-fix canary-redaction test) | 21 → 26 | +5 |
| `SteamKeyIsolationTest` (rewritten invariant) | 2 → 3 | +1 |
| `SteamIdentityRepositoryTest` (rewritten in place — the "no Web API key configured" precondition tests are gone, nothing replaces them 1:1 since there is no longer a precondition to test) | 26 → 23 | −3 |
| `OnboardingControllerTest` (the `submitWebApiKeyEntry` mutation pins removed) | 10 → 7 | −3 |
| `InMemoryCredentialStoreTest` (review-fix round: the construction-time migration pin, its unrelated-values-untouched companion, a sanity check, and the pure-function sanity check) | 5 → 9 | +4 |
| `EncryptedCredentialStoreSourceTest` (review round 2: the structural pin closing the production-scrub call-site gap — see the corrected paragraph above) | 3 → 4 | +1 |
| **Total net** | | **+26** |

`592 − 40 + 26 = 578`, matching the measured count exactly. A smaller
passing suite here is the correct, honest shape for a work package that
deletes far more test surface (a whole device-local key/parse/input
stack, 40 tests) than it net-adds (21 new-file tests + 5 net across
changed files, 26 total) — not a coverage regression to explain away; the
review's own guarantee-by-guarantee walk of the three deleted files
concluded the suite is not weakened (the empty-library guarantee came out
upgraded, per `SteamLibraryStatusTest`'s dedicated `MaybePrivateOrEmpty`
state versus the old parser's silent empty-list fallthrough).

Mutation-verified by name (applied, run, observed the failing test(s),
reverted — none left in the tree). **Round 2 correction (Opus review):**
the first version of this section described mutation 1 and the
`api.steampowered.com` redirection wrongly — both are rewritten below to
what was actually observed on a second, careful run, not what seemed like
the obviously-correct story.

- **`OwnedGame.playtime_forever`/`rtime_last_played`'s absent-field
  tolerance.** The naive mutation — deleting the `Int? = null` defaults
  outright — does **not** isolate cleanly: it breaks *compilation*, not
  just decode, because `LibraryMergeTest`/`SteamIdentityRepositoryTest`/
  `SteamLibraryStatusTest` construct `OwnedGame` without supplying every
  field (relying on the very defaults being removed). The correct,
  compiling form of this mutation is
  `@kotlinx.serialization.Required` on both properties — it keeps the
  Kotlin-level default (every existing constructor call site still
  compiles) while forcing kotlinx's decoder to require the JSON key. Run
  this way, the mutation kills **five** tests by name, not one:
  `SteamRelayParsingTest`'s `MUTATION PIN -- both playtime_forever and
  rtime_last_played ABSENT...` (the intended target) plus
  `SteamRelayParsingTest`'s `only playtime_forever exposed...` and
  `a future field this client has never heard of is ignored...` (both
  fixtures also omit one or both fields), plus `VaultApiClientTest`'s
  `steamOwnedGames GETs v1 steam owned-games...` and
  `VaultRelayLibraryFetcherTest`'s `getOwnedGames delegates to
  VaultApiClient steamOwnedGames...` (both decode a fixture missing
  `playtime_forever` too). The correct claim is therefore "this mutation
  is guarded from five independent directions," not "the other cases stay
  green" — the LEARNINGS rule this corrects: pin the mechanism that
  *actually* kills the mutation, not the one that seems like it should.
- **Pointing `VaultApiClient.steamOwnedGames` at a literal
  `https://api.steampowered.com` URL.** This sandbox has real network
  egress — the request does NOT time out or go unreachable; it reaches
  Valve's actual API, which answers (predictably, given no valid key/
  steamid pairing) with a body that does not decode as
  `OwnedGamesRelayOut`. That is what actually kills the three tests
  exercising this method — `VaultApiClientTest`'s
  `steamOwnedGames GETs v1 steam owned-games...`,
  `steamOwnedGames 409 maps to Validation...`, and
  `steamOwnedGames 422 maps to Validation...` (all three route through the
  same mutated method) — each via a `kotlinx.serialization.json.internal.JsonDecodingException`
  wrapped in `VaultApiError.Unknown`, not a network timeout. The unmutated
  `steamPlayerSummaries` test stays green, confirming the mutation's
  effect is scoped to the one method changed.
- Folding `steamLibraryStatusFor`'s `409`/`422`/empty-list branches into a
  single generic `Failed(...)` result kills
  `SteamLibraryStatusTest`'s three dedicated `MUTATION PIN` tests
  (`RelayNotConfigured`, `InvalidSteamId`, `MaybePrivateOrEmpty`) by name,
  each independently, while every other test in that file (and the
  non-mutated cases within those tests' neighbors) stays green.
- Dropping `EncryptedCredentialStore`/`InMemoryCredentialStore`'s
  construction-time migration scrub (WP 4h.4 review fix — see the
  "Migration note, corrected" paragraph above) kills
  `InMemoryCredentialStoreTest`'s
  `MUTATION PIN -- construction scrubs an existing install's legacy Steam
  Web API key` AND `a seeded legacy key does not disturb unrelated seeded
  values` by name, both independently reverted before this report.
- `SteamKeyIsolationTest`'s reintroduced-violation guard was verified the
  other direction, **in both shipped source sets it now walks (review
  catch — the first version only ever walked `src/main`, exactly the blind
  spot the deleted, `src/debug`-only `IdentityScreen.kt` would have hidden
  behind)**: temporarily adding a literal `"api.steampowered.com"` string
  and a `getSteamWebApiKey()`-named call to one scratch file under
  `src/main/java/dev/steamvault/app/net/steam/` AND a second scratch file
  under `src/debug/java/dev/steamvault/app/net/` simultaneously made both
  of that test's assertions fail, and the reported `Hits:` list named BOTH
  scratch files in each failure message, confirming neither source set is
  a blind spot anymore (reverted before this report; not left in the tree).

Verified command + output tail (JDK 17.0.12 Temurin + the `platforms;
android-35`/`build-tools;35.0.0`/`platform-tools` SDK components this
project already pins, both provisioned fresh into scratch locations
outside the repo for this environment — same "Toolchain note" shape WP
4c-app's own verification recorded, no repo file changed as a result
beyond the machine-specific, gitignored `app/local.properties`; this is
the review round 3 re-run, after the production-scrub structural pin
below, forced with `--rerun-tasks` and a prior `--stop` since this
class's own tests read source files directly and can otherwise report a
stale `UP-TO-DATE` result — measured directly during this WP's own
mutation runs below, not a theoretical concern):

```
$ ./gradlew.bat --stop && ./gradlew.bat testDebugUnitTest testReleaseUnitTest lintDebug --console=plain --rerun-tasks
...
BUILD SUCCESSFUL in 1m 20s
55 actionable tasks: 55 executed
```

`578 tests completed, 0 failed` on both variants (summed from the
`testDebugUnitTest`/`testReleaseUnitTest` XML reports'
`tests=`/`failures=`/`errors=` attributes). `app/app/build/reports/lint-results-debug.txt`
reads exactly `"No issues found."` — the new plurals resource
(`settings_steam_library_count`) and every removed/renamed string were
checked this way, not assumed clean. `assembleDebug` was not run for this
WP (no APK-level claim made here beyond what the unit-test compile step
already proves — `compileDebugKotlin`/`compileReleaseKotlin` both
succeeded as part of the command above).

**Review round 3: the production-side legacy-key scrub was unpinned —
fixed.** Sharing `legacyPrefKeysToScrub` between `EncryptedCredentialStore`
and `InMemoryCredentialStore` closed the LOGIC-drift half of "don't pin
the fake," but left the CALL-SITE-EXISTENCE half open: nothing in the JVM
suite ever constructs a real `EncryptedCredentialStore` or calls its
`clearSteamIdentity()`, so a diff deleting BOTH the `init` block's scrub
loop and `clearSteamIdentity`'s restored `editor.remove(...)` line still
passed 577/0 on both variants (reviewer-verified). Fixed with a fourth
`EncryptedCredentialStoreSourceTest.kt` assertion (the same structural
technique its other three guarantees already use, since this class
cannot run on the JVM at all):

```kotlin
@Test
fun `calls the shared legacy-key scrub at construction and on sign-out`() {
    assertTrue(source.contains("legacyPrefKeysToScrub("))
    assertTrue(source.contains("editor.remove(LEGACY_STEAM_WEB_API_KEY_PREF_NAME)"))
}
```

Mutation-verified, both production lines independently (reverted before
this report):

- Deleting the entire `init` block: `EncryptedCredentialStoreSourceTest >
  calls the shared legacy-key scrub at construction and on sign-out
  FAILED` (`AssertionError` on the first assertion).
- Reverting that, then deleting only `clearSteamIdentity`'s restored
  `editor.remove(LEGACY_STEAM_WEB_API_KEY_PREF_NAME)` line: the SAME test
  failed again (`AssertionError` on the second assertion). A stale
  `UP-TO-DATE` pass was observed once during this probe and initially
  blamed on the `File(...)` path not being a declared Gradle input — the
  review measured that mechanism and refuted it: the subject file is
  production Kotlin, so any statement-level edit changes compiled output
  and re-runs the test task transitively; four review probes (two plain,
  two forced) all failed correctly. The one stale pass had some other
  cause (likely a report read from the previous run). `--rerun-tasks`
  remains sound hygiene for source-text pins whose subject is only
  incidentally a compile input — e.g. a token planted in a comment
  changes the text but not the compiled output.

## Demo mode (WP APP-DEMO)

Closes the gap `ui/onboarding/logic/OnboardingSteps.kt` used to state
outright: before this WP the app had no way to be looked at, screenshotted,
or reviewed (Play Store or otherwise) without a running vault-api
deployment — every screen before a connection exists showed the connection
dialog. The web frontend has had a demo mode since WP 4a.2; this WP is that
same idea, native.

### The seam (`demo/`, `ui/demo/`)

```
app/app/src/main/java/dev/steamvault/app/
├── demo/
│   ├── DemoState.kt          # the in-memory fixture store (games, jobs, clients, settings)
│   ├── DemoModels.kt         # DemoGame/DemoJob/DemoDepot -- mutable, package-internal
│   ├── DemoFixtures.kt       # the seed data (six fictional games, three jobs, two clients)
│   └── DemoRepositories.kt   # Demo{Games,Jobs,Cache,Clients,Mapping,Settings}Repository
├── repo/
│   └── SettingsRepository.kt # NEW interface, extracted from SettingsController's former
│                              # direct VaultApiClient dependency (VaultSettingsRepository
│                              # is its real implementation) -- the same seam every other
│                              # repository already had
└── ui/demo/
    └── DemoModeBanner.kt      # the persistent "DEMO MODE" indicator composable
```

**Every existing screen controller already took its data source as an
injected repository interface** (`GamesRepository`/`JobsRepository`/
`CacheRepository`/`ClientsRepository`/`MappingRepository`) — this WP's only
structural change to the production code was adding the sixth
(`SettingsRepository`, since `SettingsController` used to call
`VaultApiClient` directly for `GET`/`PATCH /v1/settings`) and then writing
one `Demo*Repository` per interface. No screen (`LibraryScreen.kt`,
`DownloadsScreen.kt`, `SettingsScreen.kt`, `GameDetailSheet.kt`) branches on
demo mode for DATA at all — `MainActivity.kt` decides which repository
implementation to hand out, and the screen never knows the difference. The
one thing every data-bearing screen (plus `ClientsSheet.kt`) DOES branch on
is a `demoMode: Boolean` parameter used for exactly one thing: whether to
render `DemoModeBanner()` (WP brief constraint 1).

**Shapes match the real API 1:1 by construction, not by convention.** Every
`Demo*Repository` method returns the SAME `@Serializable` data classes
`net/model/*.kt` defines for the real endpoints — there is no separate
"demo DTO" to drift from the real one; adding a field to (say)
`GameSummary` fails to compile `DemoState.kt` until its constructor call
sites are updated too. This is a stronger guarantee than the web fixture's
own "matches the docs" claim, which needs a dedicated cross-check test
(`web/tests/demo-data.test.js`) precisely because JS has no such compiler
enforcement.

### What demo mode does NOT touch (WP brief constraint 5)

No Steam-identity/owned-games fixture exists at all. `SteamIdentityRepository`
(OpenID sign-in, persona lookup) is handed to the demo screens completely
unmodified — Settings' Steam identity section and Library's owned-games
merge work exactly as they already did on a genuinely fresh install with no
vault-api connection: `VaultRelayLibraryFetcher.requireClient()` throws
`IllegalStateException` the instant anything calls it, because demo mode
never constructs a `VaultApiClient` (`MainActivity.vaultApiClientState`
stays `null` throughout a demo session — reachable only from
`OnboardingMode.FIRST_RUN`, which by definition has no connection yet). This
is reused fail-closed behaviour, not new code, and it is why this WP has no
`OwnedGame`/`SteamPersona` fixture to keep in sync with ADR-0010's
playtime/last-played default-off rule — there is no such fixture in this
WP's scope, full stop, rather than one that happens to comply.

### Entering and leaving demo mode

`OnboardingScreen.kt`'s "Skip for now — browse in demo mode" (same wording
as `web/js/onboarding.js`'s own link) is shown on Connect/Steam-identity
steps, first-run only (`OnboardingController.canSkipToDemo`) — mirroring
the web port's own "Reconnect's Skip just cancels, no demo" distinction.
Tapping it calls `MainActivity.enterDemoMode()`, which builds a brand-new
`DemoState.fresh()` and swaps `settingsControllerState`/
`clientsControllerState` to demo-backed instances; nothing is written to
`CredentialStore`.

`MainActivity.refreshVaultApiClient()` — the ONE function that ever builds
a real `VaultApiClient` from `CredentialStore` — unconditionally clears
`demoState` as its first statement. Finishing onboarding with a real
connection, or Settings' Disconnect, both call this function, so "a real
connection now exists" and "demo mode is over" are the same event by
construction; there is no second, independently-maintained "exit demo mode"
code path that could drift from it. Re-entering demo mode always calls
`DemoState.fresh()` again, so a previous session's deletions/settings
overrides never leak into the next one — demo state is a plain in-memory
field (the same category `destination`/`showOnboarding` already document:
gone the moment the `Activity` instance is, by design, not persisted).

### Fixtures

Six fictional games (`DemoFixtures.kt`'s own kdoc has the full reasoning
per game): a plain `done` game; an `idle`+`needs_force` game whose
`last_manifest_check` survives a simulated cache deletion (the
`CONFIRMED_BEFORE_CACHE_CLEARED` wording branch); a `running` game backed
by a seed job that is already mid-flight; two `done` games sharing one
depot (the `MultiPlan.kt` sharing arithmetic, deletable in either order);
and an `error`+`needs_force` game with a non-zero GC dry-run result. Three
seed jobs (one finished successfully, one finished with an error, one
running) plus two synthetic clients (one flagged `bypass_suspected`).

Jobs and GC runs advance by CALL COUNT (`DemoState.tick()`), never a timer
— the same reasoning `web/js/demo-data.js`'s header gives ("demo mode needs
no timers of its own"): every `GET /v1/jobs`-equivalent poll or
`GET /v1/jobs/{id}`-equivalent GC poll ticks the clock forward by one, so a
seed job or a freshly enqueued prefill/GC run reaches `done` after a small,
deterministic number of polls with no `Thread.sleep`/coroutine delay
involved on the fixture side.

**Simplification, stated plainly:** cache deletion clears a game's OWN
depot list unconditionally (mirroring api/README.md's "deletion clears
cache content, not mapping rows"); a shared depot with another currently-
cached owner is reported under `skipped_shared` (informational — bytes are
not double-freed) rather than kept on the deleting game's own list. This
reproduces the web fixture's documented "deleting either side of a shared
pair skips it once, the sole-holder case fires on the second delete" shape,
but does not attempt full ADR-0003 fidelity beyond that.

### Settings fixture (ADR-0009 / ADR-0010)

`DemoState`'s settings model mirrors `web/js/demo-data.js`'s own
db-override-over-env-over-default precedence for the seven overridable
keys (`vault_name`, `schedule_window`, `schedule_interval_minutes`,
`schedule_client_stale_days`, `auto_gc`, `webhook_url`, `webhook_events`)
plus every `settings_store.ENV_ONLY_INFO_KEYS` row the real `GET
/v1/settings` carries, including ADR-0010's two relay-privacy keys
(`relay_expose_playtime`/`relay_expose_last_played`) — both env-only,
both defaulting `false`, pinned by a named mutation test. `PATCH` on an
env-only key is rejected with the real `422` taxonomy
(`VaultApiError.Validation`), same as the shipped server.

### Structural pins

Same source-text-scan technique `SteamKeyIsolationTest` already
established, applied to three different guarantees this WP's brief asks
for by name (`DemoModeNetworkIsolationTest.kt`, `DemoModeUiWiringTest.kt`):

1. No file under `demo/` mentions a network-capable type
   (`VaultApiClient`, `okhttp3`, `OkHttpClient`, `java.net.`,
   `HttpURLConnection`, `URLConnection`, `Retrofit`) OUTSIDE a comment —
   comments are stripped before the scan specifically so this class's own
   KDoc can still document `VaultApiClient`'s deliberate absence by name.
2. Every data-bearing screen (Library, Downloads, Settings, game detail,
   the clients sheet) still contains the literal
   `if (demoMode) DemoModeBanner()`.
3. `MainActivity.refreshVaultApiClient()` still contains `demoState = null`.
4. `MainActivity.enterDemoMode()` still calls `DemoState.fresh()`.

### Tests

28 new JVM unit tests (606 total with the prior WPs' 578), no
Robolectric/emulator dependency — `DemoStateTest.kt` (21: fixture shape,
job ticking, dedupe, job control, shared-depot deletion in both orders, GC
dry-run/execute parsed by the REAL `parseGcLogSummary`, settings
precedence/rejection, and the fresh()-independence pin), the two structural
files above (5), and two new `OnboardingStepsTest.kt` cases for
`shouldShowOnboarding`'s new `demoMode` parameter.

Verified command + output tail:

```
$ ./gradlew.bat assembleDebug testDebugUnitTest testReleaseUnitTest lintDebug --console=plain
...
BUILD SUCCESSFUL in 19s
74 actionable tasks: 15 executed, 59 up-to-date
```

`606/0/0` across both `testDebugUnitTest` and `testReleaseUnitTest` (summed
from the XML reports' `tests=`/`failures=`/`errors=` attributes);
`app/app/build/reports/lint-results-debug.txt`: "No issues found."

Four mutations, each reverted immediately after observing the failure:

- Leaked a `VaultApiClient` reference into `DemoRepositories.kt`:
  `DemoModeNetworkIsolationTest > no CODE under demo ... mentions a
  network-capable type FAILED` — `Hits: [DemoRepositories.kt:
  VaultApiClient] expected:<[]> but was:<[DemoRepositories.kt:
  VaultApiClient]>`.
- Flipped `relay_expose_playtime`'s demo default to `true`: both
  `DemoStateTest > MUTATION PIN -- the two ADR-0010 privacy keys are
  env-only and default off` and the env-only-rejection test FAILED with
  `expected:<false> but was:<true>`.
- Removed `LibraryScreen.kt`'s `if (demoMode) DemoModeBanner()` line:
  `DemoModeUiWiringTest > MUTATION PIN -- every data-bearing screen still
  renders the demo mode banner ... FAILED` — `Missing: [Library
  (src/main/java/dev/steamvault/app/ui/library/LibraryScreen.kt)]`.
- Deleted `demoState = null` from `refreshVaultApiClient()`:
  `DemoModeUiWiringTest > MUTATION PIN -- refreshVaultApiClient
  unconditionally clears demoState FAILED` — the assertion message quotes
  the mutated function body verbatim.

### What this WP deliberately did NOT do

- **No Steam-identity/owned-games demo fixture** — see "What demo mode
  does NOT touch" above; this is a scope decision, not an oversight.
- **No instrumented/visual verification** — no emulator or device is
  available in this environment (unchanged constraint from every earlier
  4b.x WP); the banner's placement, colour contrast, and the onboarding
  skip-link's layout have not been seen on a real screen.
- **No `compose-ui-test`/Robolectric coverage of the new Composables** —
  `DemoModeBanner.kt`, the new banner call sites, and the demo-skip button
  are covered only by the structural source-scan tests above, same
  standing limitation the rest of this codebase's UI layer has.
- **No atomic all-or-nothing validation for demo `PATCH /v1/settings`** —
  the real server validates every key in a multi-key patch before applying
  any of them; `DemoState.patchSettings` applies each key as it validates
  it, so a patch with a valid key followed by an invalid one leaves the
  valid one applied. Not exercised by any test, and not what the real
  server does — noted here rather than silently.
- **Clients sheet demo coverage is best-effort** — the WP brief names
  Library/detail/Downloads/Settings explicitly; the clients sheet got the
  same treatment (a `DemoClientsRepository`, a banner) for consistency and
  to avoid a dead "Open Clients" button in demo mode, but it was not asked
  for by name and has correspondingly less scrutiny than the four named
  screens.

### Review round 2 (Opus, FAIL — two blockers, both fixed; three should-fix items addressed)

**B1 — the network-isolation pin was a denylist ("no file mentions these
seven spellings"), not isolation.** The reviewer patched
`DemoClientsRepository.list()` to call `SteamOpenIdClient().checkAuthentication(...)`
(a real OkHttp POST to `steamcommunity.com`, fired every time the demo
clients sheet opens) and the whole 606-test suite, plus the old
`DemoModeNetworkIsolationTest`, stayed green — `net.steam.*` was never on
the seven-item list. **Fixed by inverting the check to an allowlist**
(`DemoModeImportAllowlistTest.kt`, replacing the old file outright): every
qualified name `demo/` and `ui/demo/` reference at all — import line or
inline fully-qualified reference — must fall inside a fixed
`ALLOWED_PREFIXES` set (model/error DTOs, the repository interfaces,
Compose/theme/resources, `kotlinx.serialization`, `java.time.Instant`);
anything else fails closed by construction. Comments are stripped first
(package declarations too, after an initial false-positive from the file's
own `package dev.steamvault.app.demo` line) so KDoc can still name a
forbidden class while explaining its absence.

Re-verified by reproducing the reviewer's exact mutation (compiled and run,
not just quoted): adding
`import dev.steamvault.app.net.steam.SteamOpenIdClient` plus
`runBlocking { SteamOpenIdClient().checkAuthentication(emptyMap()) }` inside
`DemoClientsRepository.list()` —

```
DemoModeImportAllowlistTest > MUTATION PIN -- every qualified name demo code
references, import or inline, falls inside the fixed allowlist FAILED
```

with the assertion message quoting
`Hits: [DemoRepositories.kt: dev.steamvault.app.net.steam.SteamOpenIdClient]`
— reverted immediately after.

**B2 — the banner scrolled off screen on three of five surfaces.** On
`SettingsScreen.kt`, `GameDetailSheet.kt`, and `ClientsSheet.kt`,
`DemoModeBanner()` was the first child of the `Column` carrying
`.verticalScroll(...)` in its own modifier chain, so it translated with the
scroll offset — a screenshot taken anywhere but the very top of any of
those three screens carried no indicator at all, the exact thing brief
constraint 1 declares impossible. **Fixed** by hoisting the banner into a
non-scrolling outer `Column` that wraps the scrolling one as a sibling, on
all three files (`LibraryScreen.kt`/`DownloadsScreen.kt` were already
correct — their content is `LazyColumn`/`LazyVerticalGrid`, never a
scrolling `Column`). Pinned by a new
`DemoModeUiWiringTest` case that takes the span from each file's nearest
`@Composable` to its `DemoModeBanner()` call (comments stripped) and
asserts it never contains `verticalScroll` — reproducing the ORIGINAL bug
shape on `SettingsScreen.kt` (moving the banner back inside the scrolling
`Column`) kills it:

```
DemoModeUiWiringTest > MUTATION PIN -- the demo mode banner is never inside
a verticalScroll subtree FAILED
```

with `Violating files: [.../SettingsScreen.kt]` — reverted after.

**S1 — a real Steam identity could render on, or be silently wiped by, a
demo-mode Settings screenshot.** Investigated per the coordinator's ask:
`enterDemoMode()` hands the demo `SettingsController` the REAL
`CredentialStore`/`SteamIdentityRepository` (unmodified, brief constraint
5) — and onboarding Step 2 persists `steamId64`/persona to that store
IMMEDIATELY on a successful sign-in, not deferred to `finish()`. Since
"Skip for now" is offered on Step 2 too, the reachable sequence is: sign in
with a REAL Steam account during onboarding → tap Skip → land in demo mode
with a real identity sitting in `CredentialStore` → open Settings. Before
this fix, `SteamIdentitySection` would have read and rendered that real
`steamId64`/persona name on screen. **What only a device could confirm has
not changed: nobody has watched this render on a physical screen** — but
the source-level answer to "can a demo screenshot display a real identity"
was YES before this fix, traced through the exact code path above, not
inferred.

**Fixed**, without touching `CredentialStore` or the OpenID flow itself
(brief constraint 5 stays intact — nothing about HOW sign-in works
changed, only WHEN this screen is allowed to READ the result):
- `SteamIdentitySection` now gates on `demoMode` and returns a static,
  identity-free message BEFORE reading `controller.identityState` at all.
- `ConnectionSection`'s Disconnect action (`CredentialStore.clear()` — the
  WHOLE store) is hidden while `demoMode` is true, so a demo-mode tap
  cannot wipe a real identity left over from before the session started.
  Reconnect stays: it never touches `CredentialStore` until a connection
  is tested and finished, and remains the documented way to leave demo
  mode.
- `MainActivity.handleIntent`'s Steam-OpenID-callback routing no longer
  sends a completion into a demo-backed `SettingsController` (which cannot
  show or act on it anyway); it falls through to the pre-existing
  "unroutable callback" handling instead.
- **Residual, recorded rather than silently left:** that fallback path
  still calls `SteamIdentityRepository.completeLogin`, which persists a
  VALID completion to `CredentialStore` regardless of caller — pre-existing
  behaviour this WP does not touch. The narrow race this can combine with
  (sign-in started during onboarding, completing only after the user
  already skipped to demo mid-flow) is not closed. Judged out of scope
  under brief constraint 5 rather than fixed by touching identity code.
  **The write happens — but no demo surface can render the result.**
  Traced end to end, because a residual is only worth recording if the
  reader can tell whether it matters: there are exactly two places in this
  app that ever render a Steam identity. `SteamIdentitySection` is one —
  it now `return`s before its first read of `controller.identityState`
  whenever `demoMode` is true (the gate this WP's fix adds), so the
  freshly-written identity is never reached from there. The onboarding
  Steam step is the other, and it is reachable, post-race, only through
  Settings' Reconnect action — which opens `OnboardingScreen` as
  `MainActivity`'s entire `setContent` body (`onboarding/OnboardingScreen.kt`'s
  own "full-screen swap, not a modal overlay" kdoc), replacing the demo UI
  outright rather than appending to it; nothing from the demo session is
  still on screen once that happens. The Library screen's owned-games
  merge cannot reach it either, for an unrelated, already-existing reason:
  demo mode never holds a `VaultApiClient` (`vaultApiClientState` stays
  `null` throughout a demo session), so
  `VaultRelayLibraryFetcher.requireClient()` always throws
  `IllegalStateException` first, `SteamIdentityRepositoryImpl.ownedGames()`
  catches it and returns `Result.failure`, and `LibraryController
  .refreshOwnedGamesOnce()` stores `null` — `mergeLibrary` then renders
  fixtures only, the same "vault-only view stays fully functional" path a
  real, never-connected install already takes. A stray write reaching
  `CredentialStore` is real and unclosed; a demo screenshot displaying it
  is not possible through any surface this app has today.

Two new `DemoModeUiWiringTest` cases pin the UI-layer half; both reproduced
and killed the corresponding mutation (gate removed; Disconnect gate
removed) before being reverted, same as B1/B2 above.

**S2 — a screen rotation ejected the user from demo mode into onboarding.**
No `android:configChanges`, so rotation re-runs `onCreate`, which used to
call `refreshVaultApiClient()` (clearing the plain in-memory `demoState`
field) unconditionally and fall through to onboarding. **Fixed**: `onCreate`
now saves/restores one boolean (`KEY_WAS_IN_DEMO_MODE`, via
`onSaveInstanceState`) and re-enters demo mode with a FRESH `DemoState`
(never a restored one — matching this WP's existing "no stale state
carried over" rule) if that flag is set AND `refreshVaultApiClient()` did
NOT itself produce a real connection. The ordering is load-bearing
(`refreshVaultApiClient()` must run first, so a real connection always
wins over a stale saved flag) and is pinned by a new
`DemoModeUiWiringTest` case; reproduced by swapping the two calls'
order —

```
DemoModeUiWiringTest > MUTATION PIN -- onCreate restores demo mode across
rotation, but only AFTER refreshVaultApiClient runs FAILED
```

— and reverted. **What only a device could confirm: this has not been
seen through an actual rotation on a real screen or emulator** — the fix
and its pin are both source-level; no visual/behavioural confirmation
exists for this WP as a whole (no emulator in this environment).

**S3 — `demoMode: Boolean = false` defaulted the safety flag to the unsafe
direction.** **Fixed**: the default is removed on the four named
data-bearing screens (`LibraryScreen`, `DownloadsScreen`, `SettingsScreen`,
`GameDetailSheet`, plus their two private Settings sub-composables) so the
compiler enforces every call site choosing explicitly; every real call
site in `MainActivity.kt` was updated accordingly (the two "real
connection" branches for Library/Downloads previously relied on the
default and now pass `demoMode = false` explicitly). `ClientsSheet`'s
default was left as-is — it is the fifth, best-effort screen, not one of
the four the brief names, and its one call site already passes the value
explicitly.

**Nitpicks addressed:**
- `DemoState.controlJob` now refuses pause/resume/cancel against a job not
  in the matching prior state (a `409`, matching the real server's job-
  control refusal) — pinned by two new `DemoStateTest` cases.
- The "raw internal string on a screenshot surface" nitpick
  (`VaultRelayLibraryFetcher`'s `"no vault-api connection configured"`
  reaching `settings_steam_library_error`) is moot after the S1 fix above:
  the whole Steam identity section — including the library-check button
  and its status text — no longer renders at all while `demoMode` is true.
- Not addressed (unchanged from round 1, still noted rather than silently
  skipped): demo mode's "Check & update" never reaches the
  `Updated==0 && UpToDate>0` "confirmed current" outcome (realism-only;
  errs conservative), and `DemoState.patchSettings` is not atomic
  all-or-nothing across a multi-key patch.

Why demo code ships in the RELEASE variant, not debug-only like the WP
4b.1 gallery screen `app/README.md`'s "What WP 4b.1 deliberately did NOT
do" section already documents: the gallery screen is a development
artifact with no user-facing purpose ever, gated to `src/debug/` for
exactly that reason. Demo mode is the opposite — its whole point is to be
reachable by a real Play Store reviewer and to produce real store
screenshots, both of which require it to exist in the shipped release
build. The two are different in kind, not an inconsistency.

Re-verified after all of the above: `assembleDebug`, `testDebugUnitTest`,
`testReleaseUnitTest`, `lintDebug` —

```
$ ./gradlew.bat assembleDebug testDebugUnitTest testReleaseUnitTest lintDebug --console=plain
...
BUILD SUCCESSFUL in 27s
74 actionable tasks: 20 executed, 54 up-to-date
```

`612/0/0` across both `testDebugUnitTest` and `testReleaseUnitTest` (606
from round 1 plus 6 new: the B1 allowlist file replaces a 2-test file with
another 2-test file net-even, `DemoModeUiWiringTest` gained 4, `DemoStateTest`
gained 2); `app/app/build/reports/lint-results-debug.txt`: "No issues
found."

### Review round 3 (Opus, PASS — four should-fixes addressed, one nit recorded)

**F1 — the allowlist itself was defeatable, one level up from B1.**
`WATCHED_PREFIXES` omitted `javax.`/`android.`/`kotlin.`. The reviewer
measured it: `javax.net.SocketFactory.getDefault().createSocket(...)` plus
a real socket write, added to `DemoClientsRepository.list()`, built and ran
clean at 612/612. **Fixed** by adding `"javax."` and `"android."` to
`WATCHED_PREFIXES`, reproduced and killed with the reviewer's exact
snippet —

```
DemoModeImportAllowlistTest > MUTATION PIN -- every qualified name demo
code references, import or inline, falls inside the fixed allowlist FAILED
```

`Hits: [DemoRepositories.kt: javax.net.SocketFactory.getDefault]` —
reverted after. **The class KDoc's overstated claim is also fixed**, not
just the code: it no longer says the check "fails closed BY CONSTRUCTION...
including a class this file's author has never heard of." It now states
plainly what the test is — a fixed, enumerated set of watched prefix
families crossed with an allowlist within each, a strong practical barrier
against every network/platform-resource class this codebase and its
dependencies expose TODAY, not a proof against an unlisted family. Second
occurrence of the exact defect class in one package (round 2's B1 first);
recorded as such rather than only fixed.

**F2 — the banner pin caught the shape fixed, not the shape a future
coder would write.** The reviewer restored the original bug twice, green
both times against round 2's version: (1) extracting a
`DemoBannerRow(demoMode: Boolean) { if (demoMode) DemoModeBanner() }`
helper and calling it from inside the scrolling body — round 2's scan
anchored on the nearest `@Composable`, which was the helper's own, a
3-line window with no `verticalScroll` in it; (2) a
`LazyColumn { item { ... } }` shape, where the window contains
`LazyColumn`, not `verticalScroll`. **Fixed two ways.** First,
`GameDetailSheet.kt`/`ClientsSheet.kt` were refactored so the banner is
called directly in the screen's own top-level public composable (inside
`ModalBottomSheet`'s `content` lambda, which M3 types as
`ColumnScope.() -> Unit` — already a non-scrolling Column, so no wrapper
`Column` of this file's own is needed any more, and no `*Body` helper
sits between the top-level function and the banner call at all).
Second, the pin itself (`DemoModeUiWiringTest`, one merged test replacing
the two round-2 tests) now checks three things per screen: exactly one
`DemoModeBanner()` call exists; its nearest enclosing `@Composable fun` is
literally the screen's own top-level name (`LibraryScreen`,
`DownloadsScreen`, `SettingsScreen`, `GameDetailSheet`, `ClientsSheet` —
never a helper); and the span from that function's own `@Composable` to
the banner call contains none of `verticalScroll`/`LazyColumn`/
`LazyVerticalGrid`/`scrollable`. Reproduced and killed both counter-examples:

```
DemoModeUiWiringTest > MUTATION PIN -- the demo mode banner appears exactly
once per screen, directly in the screen's own top-level composable, outside
any scrolling container FAILED
```

- Extracted-helper mutation (on `SettingsScreen.kt`): `Violations:
  [.../SettingsScreen.kt: banner's enclosing composable is 'DemoBannerRow',
  expected the screen's own top-level 'SettingsScreen' -- a helper (e.g. an
  extracted DemoBannerRow-style wrapper) is not an acceptable substitute]`
- `LazyColumn { item { ... } }` mutation (on `LibraryScreen.kt`):
  `Violations: [.../LibraryScreen.kt: banner's own top-level composable
  contains [LazyColumn]...]`

Both reverted after.

**F3 — the S1 residual bullet stopped one sentence short of the answer
that actually mattered.** The residual write (a completed OpenID callback
during the narrow onboarding-then-skip race still reaches
`CredentialStore` via the pre-existing "unroutable callback" fallback) was
already recorded honestly, but the bullet did not say whether that write
could ever be SEEN. **Fixed** by tracing and stating the conclusion: there
are exactly two render paths for a Steam identity in this app, and neither
can show it while browsing demo data — `SteamIdentitySection` returns
before its first `identityState` read whenever `demoMode` is true (this
WP's own gate), and the onboarding Steam step is reachable, post-race,
only through Reconnect, which replaces the demo UI outright rather than
appending to it. The Library merge cannot surface it either, for the
existing structural reason this WP relies on throughout: no `VaultApiClient`
in demo mode means `ownedGames()` always fails closed. The residual bullet
in the S1 section above now ends with this traced conclusion instead of
stopping at "the write happens."

**F4 — cosmetic, fixed as a side effect of F2.** `ClientsSheetBody`'s
stray-indented closing brace (an artifact of the now-removed extra wrapper
`Column`) no longer exists — F2's refactor deleted that wrapper entirely
rather than reindenting around it.

**Nit recorded, not fixed (per the coordinator's own framing):**
`DemoState.controlJob`'s `PAUSE` guard checks `job.status`, not
`job.type` — it does not refuse `PAUSE` against a currently-running `gc`
job, which the real server does (only `prefill` jobs are pausable/
resumable). Unreachable through this app's own UI (job-control pause
buttons are wired to prefill jobs only, `ui/detail/DetailController.kt`),
so it is a fidelity gap in the demo model's direct-repository surface, not
a UI-reachable bug — recorded beside the other documented gaps (the
non-atomic settings patch, the "Check & update" outcome coverage) rather
than silently.

Re-verified after all of the above (`--rerun-tasks`, so every task
actually re-executed rather than reporting a stale `UP-TO-DATE`):

```
$ ./gradlew.bat assembleDebug testDebugUnitTest testReleaseUnitTest lintDebug --console=plain --rerun-tasks
...
BUILD SUCCESSFUL in 2m 42s
74 actionable tasks: 74 executed
```

`611/0/0` across both `testDebugUnitTest` and `testReleaseUnitTest` (612
from round 2, minus 1: the two round-2 banner tests in `DemoModeUiWiringTest`
were replaced by one stronger merged test); `assembleDebug` succeeded;
`app/app/build/reports/lint-results-debug.txt`: "No issues found."

**What only a device can settle, unchanged from every earlier round:** no
emulator exists in this environment. Nothing in this WP — the banner's
actual on-screen placement/contrast, the rotation fix's behaviour on a
real configuration change, the onboarding skip-link's layout — has been
seen rendered. Every claim in this file is a source-level, build-level, or
test-level claim, stated as such.

### Review round 4 (Opus, PASS — two fixes addressed, one limitation recorded)

The reviewer disassembled the pinned `material3-android:1.3.1` AAR to check
round 3's `ModalBottomSheet` refactor rather than take it on trust, and
confirmed the mechanism directly: `ModalBottomSheet`'s content lambda runs
inside Material3's own plain (non-scrolling) Column, with no
`verticalScroll`/`scrollable`/`ScrollKt.*` anywhere in the whole
`ModalBottomSheet*` class family, so the banner and each `*SheetBody()`
call are genuine siblings in a non-scrolling parent. It also worked
through nested scroll, drag-to-expand, and an oversized body case neither
of us had named, and concluded round 3's refactor is not just passing its
own pin but is a better design than round 2's (the framework's own
non-scrolling scope, in the public composable, removing the helper hiding
place — a case of the test shaping the code toward the property rather
than merely checking for it after the fact).

**Fix 1 — the merged banner pin had a real, if benign-direction, coverage
gap.** It counted bare `DemoModeBanner()` occurrences; round 2's version
had required the literal guarded form. Measured: replacing the guarded
call in `DownloadsScreen.kt` with an unguarded `DemoModeBanner()` still
built green, 611/611 — which would ship a permanent banner to a REAL
connected user. **Fixed**: the pin now separately counts the GUARDED
literal `if (demoMode) DemoModeBanner()` (must be exactly 1) alongside the
raw count (also must be exactly 1, so the two must be the SAME call).
Reproduced and killed the reviewer's exact mutation, then reverted:

```
DemoModeUiWiringTest > MUTATION PIN -- the demo mode banner appears exactly
once, GUARDED, per screen, directly in the screen's own top-level
composable, outside any scrolling container FAILED
```

`Violations: [.../DownloadsScreen.kt: expected the ONE DemoModeBanner()
call to be guarded as 'if (demoMode) DemoModeBanner()', found 0 such
guarded occurrence(s) against 1 total call(s) -- an unguarded
DemoModeBanner() would render on a REAL connected user's screen]`.

**Fix 2 — the allowlist's own KDoc attributed its residual to only ONE
mechanism (unlisted prefix families), and the reviewer found a second,
structurally different one.** `ProcessBuilder("/system/bin/ping", ...).start()`
and `Runtime.getRuntime().exec(...)`, added to `DemoClientsRepository.list()`,
both build and run clean — 611 tests, zero failures. Kotlin's implicit
imports (`java.lang.*`, `kotlin.*`) make these reachable with NO dotted
name at all, so `QUALIFIED_NAME`'s two-segment requirement never sees
either: `ProcessBuilder(` is a bare constructor call, and `Runtime.getRuntime`
is rooted at `Runtime`, not `java.`. This is the technique's structural
ceiling, not a missing list entry — no amount of adding prefixes closes
it, and an identifier denylist (`"ProcessBuilder"`, `"Runtime"`, ...) would
only be a third instance of the exact defect class this file's own history
already shows twice (round 2's seven-spelling denylist, round 3's missing
`javax.`/`android.`). **Documented, not coded around**, per the
coordinator's explicit instruction: the class KDoc now names implicit
imports as a second, unfixable mechanism, with `ProcessBuilder`/
`Runtime.getRuntime` as the concrete instances. Independently reproduced
(not just quoted from the review) before writing the KDoc: added both
lines to `DemoClientsRepository.list()`, ran the full debug suite —

```
$ ./gradlew.bat testDebugUnitTest
...
BUILD SUCCESSFUL
```

611 tests, 0 failures, 0 errors (summed from the XML reports) — matching
the reviewer's own measurement exactly — then reverted.

**Recorded, not fixed:** the reviewer's whole `ModalBottomSheet`
non-scrolling-Column argument (F2's fix, round 3) is specific to the
pinned `material3-android:1.3.1`. A future Compose Material3 version bump
that made sheet content scrollable internally would silently reintroduce
the exact defect B2 fixed, with no pin in this WP able to see it — the
structural pin checks the SOURCE TEXT of this app's own files, not the
library's behaviour, so a library-side regression is invisible to it by
construction. Added beside the device-only items in the "what only a
device can settle" note above: this is a version-coupled guarantee,
undocumented until now, which is exactly how this class of defect returns
in a year.

Re-verified after both fixes (`--rerun-tasks`, all 74 tasks actually
executed):

```
$ ./gradlew.bat assembleDebug testDebugUnitTest testReleaseUnitTest lintDebug --console=plain --rerun-tasks
...
BUILD SUCCESSFUL in 1m 35s
74 actionable tasks: 74 executed
```

`611/0/0` across both `testDebugUnitTest` and `testReleaseUnitTest`
(unchanged count from round 3 — Fix 1 strengthened an existing test in
place rather than adding one; Fix 2 is KDoc-only); `assembleDebug`
succeeded; `app/app/build/reports/lint-results-debug.txt`: "No issues
found."

**What only a device can settle, unchanged and now also covering one more
claim:** no emulator exists in this environment, so the banner's actual
on-screen placement — full-bleed across the sheet's width rather than
inset, per the reviewer's own framing — the rotation fix's real-device
behaviour, and every other visual claim in this WP remain unconfirmed by
sight. Every claim in this file is a source-level, build-level, or
test-level claim, stated as such, now including the Material3-version
coupling immediately above.

## Installed-state badge + sweep settings surface (WP AG-3)

Android parity for two pieces WP AG-1 (api/) and WP 4d-web (web/) shipped
first: the `installed_on` badge (`GET /v1/games`/`GET /v1/games/{appid}`,
WP AG-1) and the `sweep_include_cached` settings toggle plus the
`GET /v1/schedule` status/GC-risk block (WP 4d-web's Android twin).

### The seam

```
app/app/src/main/java/dev/steamvault/app/
├── net/model/
│   ├── Games.kt                     # + InstalledOnEntry, installed_on on GameSummary/GameDetail
│   └── Schedule.kt                  # NEW: ScheduleOut (GET /v1/schedule)
├── repo/ScheduleRepository.kt       # NEW: the seventh repository seam
├── ui/library/
│   ├── logic/InstalledState.kt      # NEW: InstalledBadge sealed class + installedBadgeFor(game)
│   ├── InstalledBadgeText.kt        # NEW: shared badge text, grid/list/detail
│   ├── GameCard.kt                  # + installedBadgeText(model.installedBadge)
│   └── GameListRow.kt               # + installedBadgeText(model.installedBadge)
├── ui/detail/GameDetailSheet.kt     # gameSummaryFrom copies installed_on; the protection-gap warning
├── ui/settings/
│   ├── logic/SchedulePresentation.kt  # NEW: Kotlin port of web/js/lib/schedule-presentation.js
│   ├── SettingsController.kt         # + scheduleRepository, `schedule` state (load()/save())
│   └── SettingsScreen.kt             # + SweepIncludeCachedField, SweepStatusBlock
└── demo/
    ├── DemoModels.kt / DemoFixtures.kt   # + installedOn on DemoGame, three-state fixture set
    ├── DemoState.kt                      # + scheduleOut(), CONFIG_DEFAULT_AUTO_GC/_SWEEP_INCLUDE_CACHED
    └── DemoRepositories.kt               # + DemoScheduleRepository
```

### Round 1 → round 2, what the review caught

Round 1 shipped the model-layer decisions correctly (`InstalledBadge`'s
three states, `sweepTargetsMessage`/`cachedSweepGcRiskWarning`'s ported
branches) but left four real gaps, all closed in round 2:

- **No wiring pins.** Every screen/controller call site (`GameCard.kt`/
  `GameListRow.kt`'s badge line, `GameDetailSheet.kt`'s installed section,
  `SettingsScreen.kt`'s toggle and status block, `load()`/`save()`'s
  schedule fetch, `gameSummaryFrom`'s `installed_on` copy) could be deleted
  outright and the full suite — build, both unit-test variants, lint —
  stayed green, because only the pure logic underneath was pinned, never
  the fact that a screen actually calls it. Fixed by `Ag3WiringTest.kt`,
  the same source-text-scan technique `DemoModeUiWiringTest`/
  `DemoModeImportAllowlistTest` already use for exactly this class of gap
  (no Compose test rule/emulator in this environment to catch it
  behaviourally).
- **The copy rule ("`[]` means no fresh signal, never 'not installed
  anywhere'") was prose-only.** `InstalledBadgeCopyTest.kt` now pins the
  `NoSignal -> null` mapping structurally and scans every badge-path source
  file and `strings.xml` entry for the forbidden phrase.
- **The restated `sweep_cached_gc_risk` formula in `DemoState.scheduleOut()`
  had zero tests.** `DemoScheduleContractTest.kt` mirrors
  `web/tests/demo-data-schedule.test.js`'s full six-combination truth table
  plus its named dry-run mutation pin (a formula that checks
  `auto_gc == "off"` instead of `auto_gc != "execute"` treats dry-run as
  safe, which is wrong — dry-run reports without reclaiming).
- **The demo's `auto_gc`/`sweep_include_cached` defaults were stale
  (pre-ADR-0014 `"off"`), and the kdoc asserted the resulting risk warning
  as the shipped default's own behaviour** — false: `api/vault_api/
  config.py`'s real `DEFAULT_AUTO_GC`/`DEFAULT_SWEEP_INCLUDE_CACHED` are
  `"execute"`/`true` (ADR-0014), so a fresh REAL vault shows no warning.
  Fixed by mirroring those real defaults in `DemoState.kt`'s
  `CONFIG_DEFAULT_AUTO_GC`/`CONFIG_DEFAULT_SWEEP_INCLUDE_CACHED` constants
  (the risk warning is still reachable in a demo session — flip "Include
  cached games" on and Auto-GC to Off/Dry run in Settings), plus
  `DemoConfigDefaultsDriftTest.kt`, the Kotlin twin of `web/tests/
  demo-data-config-defaults.test.js`, reading `config.py`'s real text and
  failing loudly — with a VALUE-drift vs. GRAMMAR-drift labelled message —
  the next time the two fixtures disagree.

### What only a device can settle

No emulator exists in this environment (this file's standing constraint).
Unconfirmed by sight, all real for this WP specifically:

- Whether the installed badge actually fits legibly inside a 2/3-column
  grid tile without truncation or overlapping the size/status line above it.
- Badge text contrast against real Steam cover art (the badge has no
  background chip of its own, unlike the capsule pill) — a bright cover
  could wash out the `onSurfaceVariant`/`error` text color choice.
- TalkBack behavior on the `sweep_include_cached` `SingleChoiceSegmentedButtonRow`
  — it currently carries no explicit `Modifier.semantics { }` group
  wiring (no `role="group"`/`aria-labelledby` equivalent the way the web
  port's `<div role="group" aria-labelledby=...>` does), so TalkBack's
  actual announcement for this control is unverified.
- Whether the detail sheet's job-control/delete/GC action row still lands
  above the fold once the new installed-state lines push it down on a
  typical phone screen height.

## Pre-freeze review fixes (WP APP-FIX-1)

Nine findings from the pre-freeze project review, all `app/`-only; no
build environment exists here (Gradle/JVM tests run in GitHub CI only), so
every Kotlin change is minimal and mirrors an existing pattern in this
tree. **Test count:** the JVM suite is CI-gated — `main` is green by
construction and the `android-tests` job's run shows the current count.
The numbers quoted in the sections above (the last one, 611, at the end of
WP APP-DEMO round 4) are historical measurements of their own package, not
the current total; this package added tests and recorded no new count by
hand. Per finding:

- **S1 — API key masked.** `ui/onboarding/OnboardingScreen.kt`'s API-key
  field now uses `PasswordVisualTransformation()` by default with a
  Show/Hide `TextButton` in the trailing slot (`rememberSaveable` keeps the
  toggle across rotation; no eye glyph exists in material-icons-core, and
  the extended pack is deliberately not a dependency — `NavIcons.kt`) and
  `KeyboardOptions(keyboardType = KeyboardType.Password,
  autoCorrectEnabled = false)`. Strings `onboarding_api_key_show`/`_hide`.
  Pinned by `ui/onboarding/OnboardingApiKeyMaskingSourceTest` (source-text
  scan of the one `OutlinedTextField` bound to `controller.apiKeyText`,
  same technique as `SteamKeyIsolationTest`).
- **S2 — consumed callback stripped from the Intent.** `MainActivity
  .handleIntent` sets `intent.data = null` once an OpenID return has
  been read, and `handleNotificationTap` removes both extras it consumed,
  so a configuration-change recreation cannot replay either (kdoc on both
  explains why). Device item 5b above. Pinned, together with the P1
  wiring, by `MainActivityIntentWiringTest` (comment-stripped source scan
  with enclosing-block anchors).
- **P1 — pending `state` outlives Activity recreation.** The
  `PendingLoginState` holder is now `MainActivity.PROCESS_PENDING_LOGIN_STATE`
  (companion `val`, process scope), passed into
  `SteamIdentityRepositoryImpl`; the repository kdoc that claimed
  "repository lifetime == process lifetime" now states the real contract
  (lifetime is the caller's choice; production passes the process-scoped
  holder; process death still fails closed, nothing persisted).
- **S4a — `return_to` must be signed.** `SteamOpenIdCallback
  .signedCoversReturnTo` + a second gate in `completeLogin`, kdoc and the
  "Signed-fields check" paragraph corrected; tests in
  `SteamOpenIdCallbackTest` (accept/reject/substring) and
  `SteamIdentityRepositoryTest` (rejected before the verifier is called).
- **S4b — "Residual FIXED" reworded** to "narrowed", with the remaining
  class named: a second app registering the same custom-scheme filter can
  receive the genuine callback (state included) via Android's chooser;
  blast radius stated.
- **S3 — device checklist** items 4/5 and the WP 4b.7 `setWebApiKey`
  section/test entry annotated REMOVED in WP 4h.4, replaced by relay-path
  items 4a–4d (`409` / `422` / zero-games as `SteamLibraryStatus` renders
  them).
- **S5/S7 — release version from the environment** (`build.gradle.kts`,
  release section above). `publish.yml` sets both variables in its
  `Compute release version` step; `versionCode = github.run_number`
  restarts on a workflow rename/recreate (Android rejects it as a
  downgrade).
- **S6 — private CA on the Public-domain profile** documented in the
  connectivity-profile section. No code change: an `SSLException`-specific
  `ConnectionFailureReason` is not a two-line addition (it would need a new
  `VaultApiError` kind or a new reason plus `classifyConnectionFailure`,
  `OnboardingStrings`'s exhaustive `when`, and a string), so the error still
  reads "Could not reach the server" — recorded, not hidden.
- **N1–N3** — `proguard-rules.pro` (shrinking is off; the "once 4b.9 turns
  it on" wording was stale), `AndroidManifest.xml` (the key is stored NOW,
  not "in a later WP"), `MainActivity.handleNotificationTap`'s unfinished
  kdoc sentence.

Left alone, as recorded by the review for post-release: P2
(`NotificationPollWorker` catch-all) and P3 (`security-crypto` GA bump,
scheduled for the device session).

**What needs the CI build to confirm (no Gradle here):** the two new
Compose imports resolve against BOM 2024.10.01 (`rememberSaveable` from
`runtime-saveable`, an `api` dependency of `compose-ui`;
`KeyboardOptions(autoCorrectEnabled = …)` is the non-deprecated 1.7.x
parameter name), the Kotlin smart cast that lets `intent.data = null`
follow `intent?.dataString ?: return`, and that AGP lint's
`UnusedResources` sees both new strings as used (they are referenced from
`OnboardingScreen.kt`).

## Android parity (WP APP-FEAT-2: APP-FIX-2, APP-FEAT-1, APP-FIX-3, B2)

Brings the app to the web's state after rc5. The web code is the reference;
where the web has literal pins, the Kotlin port keeps the same strings and
pins them twice: by string equality (this README's verbatim-port exception)
and against the web source itself (`*WebTwinTest`, read at test time, the
`DemoConfigDefaultsDriftTest` technique).

- **APP-FIX-2 (WEB-FIX-4).** `ui/downloads/logic/JobFailure.kt` ports
  `job-failure.js`: the reason comes from vault-api's LAST log line
  (`[vault-api] Prefill failed (reason=...)`), hints for `not_logged_in`
  and for `exit_code` + the narrow phrase "is resolving to a public IP"
  (never the exception name). `prefill_failed` (API-FIX-3) is recognised
  but gets NO hint block, exactly like the web (its cause is the output's
  last line, which a hint would collapse away). With a hint the raw output
  is collapsed behind "Show the full SteamPrefill output"; Retry only on
  the newest prefill job for the app (`isNewestPrefillJobForApp`).
  Titles: `appTitle` (vault name, owned name, "App N") via
  `nameFor(..., ownedNames)`; Downloads starts the one owned-list load
  itself only when a job has no vault name and nothing was loaded yet.
  Demo enqueue for an unknown app inserts an UNNAMED vault row
  (`needs_force = true`), like the real `POST /v1/prefill`; the seed
  failed job carries the real `not_logged_in` reason line.
- **APP-FEAT-1 (API-FEAT-1, WEB-FEAT-1/2).** The Library's owned games come
  from the vault's stored `steam_library_steamid` (a JSON string; anything
  else is "not set") plus the relay for that id, never from this device's
  OpenID sign-in. `ui/library/logic/OwnedLibrary.kt` (pure + the loader
  state machine with a generation token) and `OwnedLibraryController`
  (held by `MainActivity`, shared with Downloads). The Library shows the
  web's notice line (unset / 409 / stored id rejected / failed / private
  profile) with Settings and Reload actions. Settings gains the "Steam
  library" block: field pre-filled from the setting, Save (own PATCH,
  string body via `buildSettingsPatchDraft`), Reset (null = delete the
  override), Preview of the typed id (count, first 8 names, persona), plus
  "Use my signed-in SteamID64" (hidden in demo mode, because it reads the
  real identity, and wherever Save cannot store the value: read-only,
  env-only, or a server without the setting; its hint says the change is
  vault-wide). Coordinator decision "Weg A": the server setting is the ONLY
  source of the library account. A server older than API-FEAT-1 (no such
  setting) gets its own ABSENT state and a "server too old" notice, never
  "set it in Settings". A load cancelled because the user left the Library
  resets `loading` and, if nothing had completed yet, lets Downloads load
  again. The OpenID sign-in and "Check library" are unchanged.
- **APP-FIX-3 (WEB-FIX-7).** `StatusIcon.kt` draws the running arrow plus
  one trailing arrow 32 units above it, clipped to the badge disc
  (`clipPath`; a Compose Canvas does not clip by itself), 1.6 s linear,
  no opacity animation; reduced motion = progress 0 = one arrow. The
  arrow segments the composable draws are the same list the geometry tests
  measure (at rest the trailing arrow is outside the disc; at the end frame
  the leading one is 3.4 units outside; worst phase 70 % of one arrow
  visible).
- **B2 (VER-2, AGENT-FEAT-1).** Settings → About from `GET /v1/about`
  (status passed through; `404` = "server too old" note; versions shown
  verbatim, like web WEB-FEAT-3's `about-view.js` -- the "dev build"/"v"
  rule is the web rail's only). The app showed no `server_version`
  anywhere, so there was nothing else to fix. The clients sheet rows
  (reachable from Settings → Clients) show the server's `presence`
  verbatim ("Presence unknown" on an older server), "last seen 4 min ago"
  (a port of web `formatAgo`), and "agent 0.1.0" / "version unknown",
  next to the existing games count. Follow-up: twin pins against web
  `about-view.js`/`clients-view.js`/`format.js` once WEB-FEAT-3 is on this
  branch. `ClientOut` decodes the four new fields with defaults; the demo
  fixtures carry them, `/v1/about` and a relay fixture.

**Only CI can confirm** (no Gradle here): compile, lint (`UnusedResources`,
new strings), `clipPath`/`Path.addOval`/`SelectionContainer`/
`KeyboardType.Number` against the pinned Compose BOM, and every new test.
On a device: the clipped arrow's rim anti-aliasing, the hint block's
layout on a phone, and the Library notice under a slow relay.

## Pairing from the web UI (APP-PAIR-1)

User request 2026-10-04: no more typing the hangar URL and API key on every
device. The web UI (package PAIR-1, built in parallel) shows a QR code; the
phone's normal camera app scans it and opens this app through a deep link.
No in-app scanner, no camera permission, no API change.

**Contract** (fixed, shared with PAIR-1, pinned literally in
`PairingLinkContractTest`):

```
steamhangar://pair?v=1&url=<percent-encoded base URL>&key=<percent-encoded API key>
```

- **Intent filter.** `MainActivity` carries a third filter: `VIEW`,
  `DEFAULT` + `BROWSABLE`, scheme `steamhangar`, host `pair`. The Steam
  OpenID callback uses a different scheme (`steamvault://auth/openid-return`),
  so the two can never overlap; `net/pairing/PairingLink.kt::routeIncomingLink`
  routes an incoming `dataString` to one of them (the OpenID branch is the
  same `RETURN_TO` prefix test as before).
- **Parser** (`net/pairing/PairingLink.kt`, no Android types). Requires
  `v=1` and checks it first (another version: "Update the app"); `url`
  http/https only, `//` authority, no backslash/whitespace/control
  character, no userinfo, no fragment, no path or query (the API client
  replaces the path, so a path would be dropped silently); `key` present,
  trimmed, non-empty, printable ASCII (it travels as the `X-Api-Key`
  header). `v`/`url`/`key` twice is refused, unknown parameters are
  ignored. Each refusal has its own message (`pairing_error_*`).
  Decoding is form decoding: strict `%XX` with ASCII hex digits only,
  strict UTF-8, and `+` as a space, so links from both `encodeURIComponent`
  and `URLSearchParams` decode the same; a literal `+` in a key must be
  sent as `%2B` (both encoders do that). The URL is normalized to
  `scheme://host[:port]` (default port dropped, IDN in punycode), and that
  normalized form is what the dialog shows and what gets stored.
  `PairingRequest.toString()` redacts the key; nothing in the pairing code
  logs.
- **Dialog** (`ui/pairing/PairingDialog.kt`, composed by `MainActivity` above
  onboarding and the main shell). "Pair with <host>?", the address, a note
  that the key is stored but not shown, a cleartext warning for `http://`,
  and, if a working connection is stored, "currently connected to <host>,
  pairing replaces that connection" (or, for the same server, "replaces its
  stored API key"). Pair / Cancel; while checking, both are disabled and a
  spinner shows; a failure is shown in place in the dialog with the
  onboarding wording.
- **One check, one write path.** Pair runs
  `OnboardingController.verifyConnection` (extracted from `testConnection`,
  which now calls it): health, then the authenticated `GET /v1/settings`.
  Nothing is written before the check passes, so a failed pairing never
  replaces a working connection. Then (`pairingContinuation`):
  onboarding on screen → the connection is loaded into step 1
  (`applyVerifiedConnection`) and onboarding moves to the Steam identity
  step; its Done step persists it through `finish()` as always. Demo mode
  (no real connection) → onboarding opens first, then the same. Onboarding
  already finished → `applyVerifiedConnection` + `finish()` +
  `refreshVaultApiClient()` and home (Library). Pairing adds no second
  storage path. Profile: `https://` gets `PUBLIC_DOMAIN` (TLS-only),
  `http://` gets `SYSTEM_VPN`.
- **Consumed once.** The link is stripped from the Intent
  (`intent.data = null`) before it is handled, like the OpenID callback, so
  rotation does not re-open the dialog. The original launch Intent that
  Android re-delivers after process death (`savedInstanceState != null`) or
  from Recents (`FLAG_ACTIVITY_LAUNCHED_FROM_HISTORY`) is not offered again
  (`shouldOfferPairing`). The dialog state lives in a process-scoped
  `PairingController` (MainActivity's companion, like
  `PROCESS_PENDING_LOGIN_STATE`), so an open dialog survives rotation
  without the key ever entering a Bundle; process death drops it (scan
  again). A check cancelled with its Activity leaves the dialog usable
  once the in-flight request ends (the coroutine's `finally` runs only
  then). Works on cold start (`onCreate`) and while running
  (`onNewIntent`, `singleTask`).
- **Arm delay.** Pair is disabled for 700 ms after the dialog appears
  (`PAIR_ARM_DELAY_MILLIS`), against tap-jacking by a link fired at a
  moment of the sender's choosing. `PairingController.confirm` enforces
  it on a monotonic clock; the dialog only mirrors it. A failed check
  stays armed; a new link re-arms.
- **Notice wording.** When only the scheme differs (`http://hangar.lan`
  stored, `https://hangar.lan` paired) the replace notice names the stored
  vault with its scheme. For the same server the notice says the key is
  replaced and connection settings (the connection type) may be updated.
- **Lost before Done.** A pairing applied into an unfinished onboarding is
  held in onboarding's step-1 fields only, exactly like a manually tested
  connection: if the Activity is recreated (rotation) or the process dies
  before onboarding's Done step, it is gone and the user scans again.

**Threat model and residuals.**
- *Anyone can fire the link.* Any web page (after a user gesture) or any
  installed app can open `steamhangar://pair?...` with a URL and key of its
  choosing, e.g. to point the app at a server it controls. Mitigations:
  nothing is applied automatically; the user must press Pair in a dialog
  whose title names the host and whose body shows the exact address that
  will be stored; the replace notice says when a working connection would
  be replaced; the arm delay above; and nothing is written before Pair and
  a successful connection check against that server.
- *Custom schemes are unverifiable.* Another installed app can declare
  the same `steamhangar://pair` filter. If the user picks it in the
  chooser (or it is the default handler), that app receives the link, key
  included. Verified Android App Links would prevent that, but they are
  http/https only and need a public domain serving `assetlinks.json`,
  which a LAN vault does not have; not an option here.
- *The link is a credential.* The QR content may stay in the camera or
  Lens scan history (possibly synced to a cloud account) and in the task's
  base Intent shown by Recents. Stripping `intent.data` covers only the
  current Intent; the re-delivery guard only stops the app from offering
  the link again, it does not erase it. A user who suspects a leaked link
  should rotate the vault API key.

**Tests (JVM).** `PairingLinkTest` (accept/reject matrix: special
characters in key and URL, missing `v`, `v=2`, `javascript:`/`file:`/`data:`
URLs, OkHttp-lenient shapes, userinfo, fragment, path, empty key, duplicate
and extra parameters, Unicode-digit escapes, routing pair vs OpenID),
`PairingLinkContractTest` (literal link, wire names, manifest filter),
`PairingDecisionsTest` (replace notice, profile choice, continuation,
re-delivery guard), `PairingControllerTest` (dialog states, busy guard,
cancellation, key never in `toString`), `OnboardingControllerTest` (the
shared check against MockWebServer, apply + `finish()`), and source pins in
`MainActivityIntentWiringTest` / `PairingWiringTest`.

**Not verified** (no Gradle, no device here; CI compiles, lints and runs the
JVM tests): that a real camera app (Google Camera, Google Lens, Samsung
Camera) offers to open a `steamhangar://` QR code at all; some only show the
text for custom schemes, and then the user has to copy it into a browser or
use manual onboarding. The dialog's look on a phone, the cold-start path,
`onNewIntent` while the app is in the background, rotation with the dialog
open, the Recents re-delivery guard, and the web twin (PAIR-1 is not on
main yet, so there is no cross-pin to its link builder).
