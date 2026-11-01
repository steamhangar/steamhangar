# Plan archive: Phase 4 intro, 4a web UI, 4b Android app

Moved verbatim from `docs/PROJECT_PLAN.md` by DOC-DIET-1 (rc22, 2026-11-01).
Finished history; do not read whole, grep or read line ranges. Open
boxes of this file are copied to the core plan, section "12. Carried-over
open items", and are ticked THERE, not here. Index: [README.md](README.md).

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
web/tests/README.md (real screen reader, phone browser cover art, a
library at the operator's real scale) — the first real-world rollout
session covers it.

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
