# Plan archive: Phase 4c manual update check, 4d persisted settings and sweep mode

Moved verbatim from `docs/PROJECT_PLAN.md` by DOC-DIET-1 (rc22, 2026-11-01).
Finished history; do not read whole, grep or read line ranges. Open
boxes of this file are copied to the core plan, section "12. Carried-over
open items", and are ticked THERE, not here. Index: [README.md](README.md).

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
      ("check & update", never bare "check"; superseded, see ADR-0006
      addendum 2026-10-13) and pull-to-refresh staying
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
- [x] Phase 4a's settings screen builds on this rather than displaying
      read-only values with "set this env var" hints
      *(the web settings view shipped in WP 4a.6, commit ff9798a; WP 4d-web,
      commit c825625, drives it through `GET/PATCH /v1/settings` —
      `web/js/api.js` `getSettings`/`patchSettings`)*

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
The same window, by default, also lifts the optional upstream rate cap
(WP TH-1a/TH-1b, ADR-0015, §11 item 10): `VAULT_UPSTREAM_RATE_WINDOW`
falls back to `VAULT_SCHEDULE_WINDOW` in `deploy/compose.yaml`, and `TZ`
is forwarded to vault-core so both are read on the same clock.

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
      the sweep surface shipped the same day as part of WP AG-3, commit
      0a3bfc5 — see §11 item 4)*
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
      fixed here**: `web/` was occupied by WP 4e.1 at review time. WP 4e.1
      merged the same day (commit 07865a3) without picking this up, and no
      later web package did either — **open since 2026-08-18, closed by WP
      WEB-FIX-1 (2026-10-01)**, one of the fix packages from the pre-freeze
      project review (§11 item 12). The shape of the fix: hoist the DELETE handler's local `otherOwners`/
      `hasCacheContent` helpers (currently function-scoped inside the
      `DELETE` branch, lines ~1039–1051) to module scope so
      `selectCachedAppids()` can reuse them, then change its filter
      predicate from `g.depots.length > 0` to "has at least one depot that is
      exclusive to this game OR a last-cached-remnant (every other current
      owner uncached)" — the same `exclusive + remnant` predicate this
      package's real backend now applies, expressed against the demo's
      depots-array model instead of `deletion.plan_deletion`. Also recorded
      in `docs/WORKPACKAGES.md` (N6).)*
