# Learnings: Android app

Scope: app/ Compose UI, Robolectric/Roborazzi screenshots, twin pins, lint, MockWebServer. Part of the learnings index
[`docs/LEARNINGS.md`](../LEARNINGS.md); entries moved verbatim by
DOC-DIET-1 (rc22), original section headings kept. Append new findings
at the end, under a dated heading, one entry each with the WP that proved it.

## Android (Phase 4b)
- allowBackup="false" alone is insufficient on API 31+: AGP lint flags it,
  and cloud backup / device-to-device transfer need explicit
  dataExtractionRules + fullBackupContent excluding the app-data domain —
  mandatory posture for an app that will store a vault-api key
  (WP 4b.1).
- Cross-frontend contracts (status-kind wire names, label words, theme
  hexes) are pinned with LITERAL expected sets in tests, never derived
  from the enum under test — a derived round-trip is circular and cannot
  detect drift from the other frontend (WP 4b.1; same class as the
  4a.6r constants-vs-literals rule).
- Gradle wrapper distributions get distributionSha256Sum — the same
  integrity bar the repo already applies to Docker images and CI actions
  (WP 4b.1).
- A background thread whose EXISTENCE is decided from the boot config
  snapshot makes every runtime-settings claim for its keys false ("next
  sweep" that never comes, /v1/schedule publishing a next_eligible_at
  that cannot arrive). Start such threads unconditionally with a cheap
  no-op tick — same shape as the worker thread; measured cost: noise
  (settings-WP blocker).
- The poll-based wait_for_job flake class has now hit TWO different
  tests under full-suite load (test_cache_delete WP 3.6, test_worker
  needs_force) — both pass isolated. Follow-up: scale wait deadlines
  under load instead of re-diagnosing each flake (settings-WP review).
- OkHttp application interceptors run ONCE PER CALL, never per redirect
  hop — a cleartext gate registered only via addInterceptor cannot see an
  https→http downgrade, OkHttp forwards custom auth headers (X-Api-Key)
  across host changes (it strips only Authorization-class headers), and
  followSslRedirects defaults to TRUE. TLS-mandatory profiles need
  followSslRedirects(false) PLUS a network-interceptor gate, pinned by a
  two-server redirect test asserting the canary key never reaches hop 2
  (WP 4b.2 blocker, measured on pinned OkHttp 4.12.0).
- Redundant defence layers cannot be pinned by one end-to-end test: each
  layer alone survives mutation because the other covers it. Pin each
  layer standalone (isolated-component test + configuration assertion)
  in addition to the conjunction (WP 4b.2 review).
- followSslRedirects(false) only blocks SCHEME-CHANGING redirects; a
  same-scheme cross-host 302 still forwards custom auth headers. A
  client with no legitimate redirects sets followRedirects(false) too
  (WP 4b.2 review).
- BigInt("0x...") accepts hex-prefixed strings — a 17-char "0x1100001
  00000000" equals STEAM_ID64_BASE, so an ASCII-digit guard in front of
  BigInt is load-bearing against HEX, not whitespace (whitespace always
  sacrifices a digit position and can never reach the 17-digit base —
  verified empirically). Verify a reviewer's assumed failure mechanism
  before writing the pin for it; pin the mechanism that actually kills
  the mutation (WP 4a.6 fix round).
- Kotlin toLongOrNull honours Unicode Nd digits (Character.digit), so an
  ASCII-digit walk in front of it is load-bearing against IN-RANGE
  Arabic-Indic spellings — a below-range non-ASCII fixture tests the
  range check, not the digit walk. Pin with the in-range non-ASCII
  spelling (WP 4b.3 review; JS BigInt had the analogous 0x-hex case).
- Custom-scheme OAuth/OpenID callbacks are unverifiable on EVERY Android
  version (verified App Links are http/https-only). Injection is fully
  mitigated by re-verifying assertions with the provider over a pinned
  host; the residual is REPLAY of a genuine assertion (attacker's own
  account) absent request<->callback binding — record it and plan a
  per-login random state (WP 4b.3 review).
- Third instance of the pinned-the-fake pattern (4b.2, 4b.3, 4b.8): a
  test fake that RE-IMPLEMENTS production logic (its own JSON decode +
  catch) proves nothing about the shipped path. Extract the logic into
  one shared function called by both production and fake — then the
  existing test pins the real code (WP 4b.8 review).
- WorkManager ExistingPeriodicWorkPolicy.KEEP freezes the schedule spec
  at whatever the FIRST app version enqueued, for the life of the app
  data — interval/constraint changes never reach existing installs.
  UPDATE (2.8+) applies changed specs without resetting the period; the
  common KEEP-vs-REPLACE dichotomy is false on modern WorkManager
  (WP 4b.8 review).
- Native inert (+ aria-hidden) beats hand-rolled Tab interception for
  modal traps: one attribute removes the subtree from tab order,
  hit-testing and the a11y tree by spec, with no focusable-enumeration
  drift; centralize Escape in one stack dispatcher — two independent
  document listeners closed two stacked overlays with one keypress
  (WP 4a.8, live-reproduced).
- An explicit scrollIntoView({behavior:"smooth"}) argument beats the
  CSS scroll-behavior:auto !important reduced-motion override per
  CSSOM-View — JS smooth-scroll call sites need their own matchMedia
  guard (WP 4a.8 review).
- Author display rules (.btn{display:inline-flex}) silently defeat the
  UA's [hidden]{display:none} — every class that is both display-styled
  and hidden-toggled needs a .class[hidden]{display:none} guard; sweep
  by cross-referencing display rules against .hidden= call sites
  (WP 4a.8; third instance of the class after h4.sec and .onbnav).

## 2026-10-14/15 — rc18 night run (UPD-FIX-1, D16a-c, DIST-1a/b)
- The Android twin and pin tests (source scans of the web copy and of
  Kotlin files) break on almost every UI or label change and only run in
  CI. Before editing UI text, a row or a screen, grep the pins
  (`*WebTwinTest`, `*PinTest`, wiring pins) for the touched string and
  update them in the same package.

## 2026-10-16 - rc19.3 throughput tuning (TUNE-1a/1b)
- **Android twin pins break on template edits, not only on renames.** Kotlin
  `*WebTwinTest` files pin quoted template fragments of web JS byte for byte
  (e.g. the `completenessLine` return template). Extending a pinned template
  literal fails the Android CI job even when the output is a superset. Keep the
  pinned literal untouched and append the new part outside it
  (`` `...` + note ``); a source-grep review cannot catch this, only the
  Android job can, so grep `app/app/src/test` for the exact literal before
  editing a template.

## 2026-10-17 — rc20 rework (UI-KIT-1, WEB-GOLDENS-1, TOKENS-SYNC-1, CLIENTS-4, ADD-DEVICE-2, DOWNLOADS-2, SETTINGS-2, LIBRARY-2)
- See also [web.md](web.md), this section: "**Moving or rewording a web string can turn main red through ..."

## 2026-10-31 — rc21 (GRAPH-RECENT-1, LOADING-NOW-1)
- **Check a "golden X equals golden Y" finding against the test KDoc and
  README first**: the identical night golden was the intended guard of a
  single-dark-theme app, not a bug (APP-NIGHT-GOLDEN-1).
- **A Compose dialog that closes itself on success cancels its own
  `rememberCoroutineScope`**: run follow-up work (re-fetch) on the screen's
  scope. Navigation after async work must come after a `join()` of that
  work in the same coroutine (ANDROID-GAPS-1 Finish).
- **No `runTest(UnconfinedTestDispatcher())` with MockWebServer calls**:
  after `withContext(IO)` the body can continue on the IO thread and state
  checks race (seen only in testReleaseUnitTest); use the standard
  scheduler plus `runCurrent()`.
- **`VaultApiClient.execute` treats an empty body as an error**; 204
  routes need the text helper (`sendForText`).
- **A source-scan pin counting a named argument (`execute = true`) also
  hits helpers with the same argument name**: anchor pins on the receiver
  call (`repository.repair(...)`).
- **Cross-flow late-answer tests need a gate per call** (a fake repository
  that holds each request until released): start A, reset, start B,
  release A in each of B's states.
- **`LibraryScreenshotTest` requires cards to start at most 32dp below the
  chips**: the chips stay the last toolbar row; anything new goes above.
- **AGP lint `PluralsCandidate` fires on "%1$d <word>"**, even for an
  adjective; use `<plurals>` for any count string.
- **Robolectric root capture does not include a `ModalBottomSheet` dialog
  window**: screenshot the sheet content composable directly.
- **One goldens branch per attempt** (`-r2-goldens`, ...) avoids force
  pushes when the record run fails before the bot commits.
- **Pin tests use `indexOf("private fun X(")` as range end markers**:
  changing a function's visibility breaks them; grep before changing it.
- **Keep the old TalkBack figure descriptions on a reshaped UI**
  (`tileDescription`), so content-description asserts survive a redesign.
- **Screenshot tests on demo data must pin every timestamp field**
  (`Instant.now()` in fixtures: last_prefill_at, last_manifest_check,
  update_checked_at, cache_complete_checked_at, installed_on.reported_at).
- **The first goldens run of a package that adds shots is red on
  `test_every_shot_has_a_checked_in_golden`** until the bot commit lands;
  judge that round by the app record job.
- **Labels in 2x2 tiles at 360dp / font 1.3 must wrap**, not `maxLines=1`
  ("SAVED BY CACHE" clipped silently; only the golden showed it).
- **Assert lower LazyColumn items only in a tall shot**: items below the
  viewport are never composed and `assertExists` fails.
- **Material3 Button's 58dp minimum width applies only when the incoming
  minWidth is 0**: `Modifier.widthIn(min = X)` gets below it; the 48dp
  touch target stays via `minimumInteractiveComponentSize`.
- **Pin up to the comma, not the closing paren** (`ExpertSwitchRow(x,`),
  so adding a parameter does not break the pin.
- **Roborazzi checks after `capture()` do not run on a golden mismatch**: a
  new layout assert is only proven in the verifying run after the refresh.
- **A LazyColumn item inserted above an already visible keyed item opens
  the list scrolled past it**: keep top items present from the start
  (empty content until data arrives).
- **"Is on screen" checks need `assertIsDisplayed`, not `assertExists`**:
  a composed item outside the viewport passes `assertExists`.

## 2026-11-01 — rc21.1 (prod findings after the rc21 rollout)
- **A Kotlin state that gains a defaulted field keeps old equality asserts
  compiling while they silently assert the default**; once a status
  becomes known, tests that used it as the "unknown" example must change.
