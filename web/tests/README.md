# web/tests

Headless tests for the `web/` no-build vanilla SPA. No dependencies, no
bundler, no browser — plain ES modules run directly by Node's built-in test
runner (`node:test`, confirmed available: `node --version` reported
v24.12.0 on this machine when WP 4a.2 was implemented).

## Run

```
node --test "web/tests/*.test.js"
```

**Note (measured on this machine, Node v24.12.0, Windows):** `node --test
web/tests` (a bare directory, no glob) fails hard with a CJS
`MODULE_NOT_FOUND` for `web\tests` instead of discovering the files inside
it — Node's own default-pattern directory walk did not trigger for an
explicit directory argument here. The explicit glob above is the form that
actually works; use it, not the bare-directory form some `node --test` docs
show.

Run a single file directly the same way:

```
node --test web/tests/backoff.test.js
```

**Run from a full repo checkout, not a copy of `web/` alone.** Several
tests pin the web UI against its twin in another component and read that
file from the repo by a `../../` path, so a partial copy (only `web/`, or
`web/` without `api/`) fails them with `ENOENT` instead of testing
anything (found in WP HYG-1's catch-up, plan §11 item 13 C):

- `demo-data-config-defaults.test.js` — `api/vault_api/config.py` (the
  `DEFAULT_AUTO_GC` / `DEFAULT_SWEEP_INCLUDE_CACHED` defaults the demo
  data must mirror)
- `demo-data-installed-on.test.js` — `api/vault_api/routers/games.py`
  (the `installed_on` shape)
- `about-view.test.js`, `demo-data-shape-guard.test.js`,
  `clients-remove.test.js` — files under `api/vault_api/`
- `about-android-twin.test.js`, `settings-save-bar.test.js` —
  `app/app/src/main/res/values/strings.xml`
- `agent-install.test.js` — `.github/workflows/publish.yml`,
  `.github/scripts/verify-ps-parse.ps1`,
  `agent/packaging/windows/install-task.ps1`
- `job-failure.test.js` — `deploy/README.md`

This is deliberate: each of these is a drift guard against that twin.

## Scope

- `backoff.test.js` — exponential backoff growth, cap, jitter bounds
  (including that the zero-floor is load-bearing, not just coincidentally
  satisfied), reset (`web/js/backoff.js`).
- `errors.test.js` — `classifyHttpStatus` across all six `ERROR_KINDS`
  (`web/js/errors.js`).
- `diff-utils.test.js` — the generic `diffByKey` list differ used by both
  the notification differ and the polling store
  (`web/js/diff-utils.js`).
- `notifications.test.js` — the client-side notification differ: every
  event type (`job_finished`, `job_failed`, `update_ready`,
  `bypass_suspected`, `bypass_resolved`), the no-change case, the
  first-poll case (must never fire a notification storm), the "stale
  requires cache content" invariant, and that an item aging out of a
  bounded list (e.g. `GET /v1/jobs?limit=20`) fires no event
  (`web/js/notifications.js`).
- `store.test.js` — the pure scheduling-decision helpers exported from
  `web/js/store.js` (`hasActiveJob`, `nextJobsIntervalMs`).
- `store-poll-loop.test.js` — the timer/in-flight orchestration in
  `web/js/store.js` (`ResourceLoop`), using a fake `document` object (only
  `hidden`/`addEventListener`/`removeEventListener` — `store.js` never
  touches more than that) and a manually-gated fetcher. No jsdom, no real
  browser: `store.js` only reads `document` lazily inside functions, never
  at module load, so a plain object stand-in is enough. Regression
  coverage for review blocker B1 (a nudge racing an in-flight poll used to
  fork a second, permanently duplicating timer chain and double-fire every
  notification). Extended (WP 4e.6) with the fourth, `keyFn`-less "cache"
  resource: its tick payload shape (`{item}`, never a `{diff}` key),
  `store.snapshot("cache")`'s undefined-before-first-poll/preserved-on-
  failure behaviour, and its own B1-style in-flight/nudge race replay — all
  fake `apiClient` objects in this file gained a `cacheSummary` stub so the
  new loop does not error on every `store.start()` call this file already
  made before this WP existed.
- `demo-data.test.js` — `web/js/demo-data.js`'s request routing: the exact
  `CacheDeletionOut` response shape, ADR-0003 shared-depot protection
  (`DELETE /v1/cache/{appid}` skips a depot still cached by another game,
  frees it once every co-owner is uncached), 404/409 cases, all-or-nothing
  `POST /v1/prefill` body validation, that a cancelled job settles to
  `'cancelled'` and stays there rather than continuing to tick toward
  `'done'` (WP 4a.2), and (WP 4a.3) the `GET /v1/mapping` route added for
  the Library view's bulk-delete confirm dialog. This module imports only
  `errors.js` — no `window`, `document` or `fetch` — so it runs in bare
  Node with no fake environment at all.

`web/js/api.js` is the one module NOT covered here: exercising its real
`request()` path meaningfully needs a `fetch`/`localStorage`-capable
environment, and WP 4a.2's DoD is scoped to the differ and the backoff
math. Its one pure export, `classifyHttpStatus`, is re-exported from
`errors.js` and IS covered, by `errors.test.js`.

### WP 4a.3 — Library view

The view itself (`web/js/views/library.js`) and its DOM-building card
component (`web/js/components/game-card.js`) are NOT unit-tested directly
(same posture as `status-icon.js`'s DOM builder, WP 4a.1) — instead every
piece of DECISION logic they lean on is pulled into a pure `web/js/lib/`
module and tested headlessly here:

- `game-status.test.js` — `web/js/lib/game-status.js`: `dispKind` (a live
  job overrides cache state; the "cached requires visible bytes" invariant
  for the `status: "done"`-with-`size_bytes: null` "last cached remnant"
  case; `status: "error"` always shows as Failed), `statusAction` (which
  states are actionable and what they offer, including the deliberate
  "error is retryable" extension over the mockup), `findLiveJob`/
  `indexLiveJobsByAppid` (GC jobs and queued jobs never drive a card),
  `hasProtectedCacheContent` vs `hasVisibleCacheContent` (the two
  DIFFERENT "has cache content" predicates — see that module's header —
  and the remnant case where they disagree), and `isJobStateTransition`
  (the round-7 "a growing `log_excerpt` on an otherwise-unchanged job must
  never look like a transition" guard).
- `library-filters.test.js` — `web/js/lib/library-filters.js`: search is a
  case-insensitive substring match, search AND chips (a live job moves a
  game out of "Not cached" and into "Downloading" at the same time),
  `chipCounts` recomputing against the current query, and that there is no
  "stale"/"Update ready" chip (no oracle data on `GET /v1/games` yet).
- `bulk-plan.test.js` — `web/js/lib/bulk-plan.js`: busy/needsDownload/
  current classification (a GC job never counts as "busy" for a download
  decision; `error` joins `none` in "needs a download"), all three
  `buildBulkDownloadPlan` outcomes (something to download and the skip
  count; everything already cached with an explicit re-download secondary;
  everything already busy), and (WP 4a.3 review fix, should-fix 1)
  `classifyBulkDeleteEligibility`: has-cache-content, not "status is not
  none" — an `error` game with ZERO visible bytes is excluded (it has no
  depot mappings left; `DELETE /v1/cache/{appid}` would 404), an `error`
  game WITH bytes (a half-deleted/partial run) is included, and a busy
  game is excluded even if it has bytes.
- `multiplan.test.js` — `web/js/lib/multiplan.js`: the round-6 mockup
  scenario ported almost verbatim (deleting two of three co-owners of a
  shared depot keeps it; adding the third to the SAME batch frees it — this
  is the real **mutation target** for "dropping the set-dedupe": removing
  the `others.filter(appid => !idSet.has(appid))` exclusion makes this
  test fail), the last-cached-remnant rule (an idle, never-prefilled,
  job-free co-owner does NOT protect a depot; an active job on an
  otherwise-idle co-owner DOES), fail-closed on an unresolvable owner
  appid, and a regression pin that a depot two selected games both list is
  counted once in `occupiedBytes` (NOT a mutation-kill for the `if
  (!depotsSeen.has(...))` line itself — see the comment on that line in
  multiplan.js and on the test: the Map's own keying already prevents
  double-counting regardless of that guard).
- `cover-art.test.js` — `web/js/lib/cover-art.js`: the exact CDN host +
  asset path (matches the CSP entry in `api/vault_api/webui.py` 1:1), and
  that the fallback hue/pattern hash is deterministic (same appid -> same
  look, every time — no flaky randomness) and stays in range.
- `format.test.js` — `web/js/lib/format.js`: `formatBytesGB`'s
  under/over-100-GB rounding and that it never fabricates a number for
  null/zero/negative/non-finite input. Gained `formatBytesGBOrZero` coverage
  (WP 4e.6, rail foot): the deliberately OPPOSITE zero rule from
  `formatBytesGB` — a genuine zero renders (`"0.0 GB"`), only
  null/undefined/negative/non-finite input stays `null`.
- `render-plan.test.js` — `web/js/lib/render-plan.js` (WP 4a.3 review fix,
  blocker B1): the pure games-tick patch-vs-rebuild decision. First poll,
  and added/removed rows, always mean a full render (grid membership can
  change). An updated row not currently on screen is skipped entirely. Two
  named **mutation targets**, one each direction: a game whose structural
  key CHANGED must land in `rebuild` (flip that branch and a card would
  silently keep showing the wrong icon shape forever); a game whose
  structural key is UNCHANGED (e.g. only `size_bytes` drifted while a
  download runs) must land in `patch`, NOT `rebuild` — flip THAT branch
  (treat any update as structural) and every games-poll tick would
  recreate, and thereby restart the animation of, a running download's
  status-icon node: the exact round-7 mockup bug, now on the games poll
  instead of the jobs one. `views/library.js` is the DOM-side executor of
  this plan (`applyGamesTick`) and is not unit-tested the same way — see
  the "WP 4a.3 — Library view" section above.

### WP 4a.5 — Downloads view

Same posture as WP 4a.3: `web/js/views/downloads.js` (the DOM-building
view) is not unit-tested directly — every piece of decision logic it leans
on is pulled into a pure `web/js/lib/` module and tested here.

- `job-partition.test.js` — `web/js/lib/job-partition.js`: `partitionJobs`
  buckets a `GET /v1/jobs` snapshot by status regardless of input order,
  treats a missing/non-array snapshot as empty, sorts `queued` FIFO by job
  id (NOT the snapshot's own newest-first order) while `history` keeps
  that newest-first order as-is. The load-bearing case is **the slot-release
  divergence** (api/README.md "The worker slot — a paused job does NOT
  hold it", recorded in docs/WORKPACKAGES.md's Phase 4a header): a paused
  job for one app and a running job for a DIFFERENT app coexist in two
  independent buckets, proving `running`/`paused` are not a single
  mutually-exclusive "active slot" like the mockup's — this is the exact
  presentation data the Downloads view's separate "Active"/"Paused"
  sections are built on. Also covers `countPending` (the nav-pip count:
  queued+running+paused, never done/error/cancelled), `queuePosition`,
  `jobIconKind` (every real status, including the real `cancelled` the
  mockup never modeled), and `jobStatusWord` (cancelled worded distinctly
  from failed — job outcome honesty; GC jobs get GC-specific wording, never
  the download vocabulary). WP 4a.8 backport: an unrecognized status routes
  into `history` with a neutral presentation (`jobIconKind` -> `"none"`,
  `jobStatusWord` -> the raw string) instead of matching no bucket at all
  and silently disappearing — ported from the Android sibling's
  `JobPartition.kt`, which had already made this improvement over the web
  port; does NOT count toward `countPending`.
- `downloads-render-plan.test.js` — `web/js/lib/downloads-render-plan.js`:
  the pure jobs-tick patch-vs-rebuild decision for the Downloads view. First
  poll and added/removed rows always mean a full render. Two named
  **mutation targets**, one each direction: ANY `status` change anywhere in
  the batch must force `full: true` (flip that branch and a job that just
  transitioned section — e.g. running -> done — could sit in the wrong
  section with stale action buttons indefinitely); a `stop_request`-only
  change with the SAME `status` (the operator's pause/cancel request being
  acknowledged, or cleared once the worker actually stops the job) must
  land in `patchStopRequest`, NOT force `full` — flip THAT branch (treat
  any update as structural) and every pause/cancel click would recreate the
  running card's animated status-icon node the instant the server
  acknowledged it: the round-7 mockup bug, reintroduced on the one live
  field the real `JobSummary` actually has (no byte-level progress field
  exists — see that module's header for why this narrows the round-7
  concern versus the games poll). Also covers a mixed batch (one genuine
  patch + one no-op update) and a batch where a status change on one job
  and a stop_request-only change on another must still resolve to `full`
  (the stricter branch wins).
- `log-excerpt.test.js` — `web/js/lib/log-excerpt.js`: the lazy
  `GET /v1/jobs/{id}` history-row excerpt display selection. Collapsed
  always wins even over a completed fetch or an in-flight load (a
  fast re-collapse must not leak stale content); loading; error (and error
  taking priority over a STALE excerpt from a previous successful fetch);
  empty for `null`/`undefined`/whitespace-only excerpt (the `undefined`
  case is "never fetched", distinct from a job that genuinely produced no
  output, but both display the same way); ready with normal multi-line and
  single-line text; and the truncation-marker handling from api/README.md's
  documented `log_excerpt` shape — detected and STRIPPED from the displayed
  body (never leaks into the first line), only recognised as a literal
  prefix (a log line merely containing the word "truncated" mid-file must
  not false-positive), and blank lines immediately after a stripped marker
  are not shown as spurious empty lines.
- `format.test.js` gained `formatTimestamp` coverage (WP 4a.5): null/
  undefined/unparseable input never fabricates a time (returns "—", same
  posture as `formatBytesGB`); a valid ISO timestamp renders through
  (asserted loosely — containing the year — since the exact locale-formatted
  string is runtime-locale/timezone-dependent).

### WP 4a.6 — Settings + onboarding + Steam identity

Same posture as WP 4a.3/4a.5: `web/js/views/settings.js` and
`web/js/onboarding.js` (the DOM-building view/overlay) are not unit-tested
directly — every piece of decision logic they lean on is pulled into a pure
`web/js/lib/` module and tested here. Both modules also import `window`
transitively (via `router.js`), same as every other view, so they cannot be
`import()`-ed under bare Node either — this WP's live verification was done
against a real `uvicorn` instance instead (see the coder's report).

- `steamid.test.js` — `web/js/lib/steamid.js`'s `validSteamId64`: the exact
  17-ASCII-digit, range-checked grammar mirrored from
  `vault_api.steam_relay.valid_steamid64`, using `BigInt` because the
  individual-account SteamID64 base (76561197960265728) already exceeds
  `Number.MAX_SAFE_INTEGER` — a plain `Number()` range check would silently
  round distinct 17-digit inputs onto the same handful of representable
  doubles. Covers the base/max boundary (mutation target: off-by-one either
  direction), wrong length, non-digit characters, and non-ASCII look-alike
  digits (the same Python `str.isdigit()` trap `docs/LEARNINGS.md`'s
  "Parsers" section already documents for other modules).
- `steam-key-form.test.js` — `web/js/lib/steam-key-form.js`: `validSteamWebApiKey`
  (exactly 32 hex characters, either case) and `submitSteamKey`'s orchestration
  — the **load-bearing pin**: the typed key is cleared from the field
  unconditionally in every outcome (validation failure, a rejected `PUT`, a
  network error, success), proven with a plain `{value}`-shaped stand-in
  object rather than a real `<input>` (ADR-0004 addendum: the key must never
  be retained after a submit attempt), and that a thrown error's message
  never contains the raw key.
- `settings-diff.test.js` — `web/js/lib/settings-diff.js`'s `buildSettingsPatch`:
  the **mutation-worthy pin** LEARNINGS asks for — a touched field whose
  draft value equals the current effective value must be DROPPED, not sent
  (removing that equality check makes every touched key appear regardless of
  whether anything changed). Also covers: an untouched field never appears at
  all; `reset` only sends `null` when a `db` override actually exists (a
  reset against an env/default-sourced key is a no-op); blank is a REAL
  override value for `schedule_window`/`webhook_url` (ADR-0009), never
  silently coerced into a reset; `webhook_events` list/comma-string
  equivalence (order- and whitespace-independent); env-only/unrecognised
  keys dropped defensively.
- `settings-presentation.test.js` — `web/js/lib/settings-presentation.js`:
  `appliesText`/`sourceLabel` cover all three real values distinctly (and
  fall back honestly, never silently, for an unrecognised one), `canReset`
  (only a `db`-sourced, non-`env_only` entry offers a reset), and
  `effectiveAsInputValue`'s three special cases — `null` becomes blank (the
  `schedule_window`/`webhook_url` "disabled" state, never the string
  `"null"`), a list (`webhook_events`) becomes a comma-joined string, and an
  empty list becomes `""` rather than `"[]"` or a stray leading comma.
- `onboarding-steps.test.js` — `web/js/lib/onboarding-steps.js`: the
  **mutation-worthy pin** that step 1 cannot be left until the vault API key
  has actually been verified (`canAdvance`/`nextStep` gated on `tested`,
  unlike the mockup's mere form-completeness check — this fork's step 1
  result is used for real, so advancing on an unverified guess would ship a
  broken key into `localStorage`); step 2 (Steam identity) is unconditionally
  optional; `prevStep`/`clampStep` bounds; `progressPercent` monotonicity;
  and the other mutation pin, `shouldShowOnboarding` (only true with no
  stored key AND no demo mode — either alone must suppress it).
- `demo-data-settings.test.js` extends `demo-data.js`'s coverage (WP 4a.2's
  fixture module) with the WP 4a.6 routes it gained: `GET`/`PATCH
  /v1/settings` (db/env/default precedence, all-or-nothing validation, an
  env-only key rejected by name distinct from "unknown key", the Pydantic
  lax-mode boolean trap, `webhook_events` accepting a JSON array) and
  `/v1/steam/*` (unconfigured -> `409`, a malformed key -> `422`, configured
  -> `200` with the fixture library, turning the relay off is immediate,
  `resetDemoData()` clears both). Reuses `lib/steamid.js`/
  `lib/steam-key-form.js`'s validators rather than duplicating the grammar a
  third time.

### WP 4a.4 — Detail sheet + delete flows

Same posture as every prior view: `web/js/components/game-detail-sheet.js`
(the DOM-building sheet) is not unit-tested directly (see
`components/sheet-dialog.js`'s header for the general reasoning) — every
piece of decision logic it leans on is pulled into a pure `web/js/lib/`
module, ported from the reviewed Android sibling's `ui/detail/logic/`
package (WP 4b.6) onto plain tagged objects instead of Kotlin sealed
classes, and tested here.

- `depot-presentation.test.js` — `web/js/lib/depot-presentation.js`: the
  four-state sharing tag (EXCLUSIVE/PROTECTED/SOLE_HOLDER/**ORPHANED** — the
  recorded WP 4b.6 divergence this WP adopts, docs/WORKPACKAGES.md's Phase
  4a header), each state's independence from `thisAppIsHolder` where
  irrelevant, and co-owner name resolution (`gamesByAppid` lookup, "App
  {appid}" fallback, `cached` mirroring `holderAppids` membership).
- `detail-job.test.js` — `web/js/lib/detail-job.js`: `findTrackedJob` is
  deliberately BROADER than `lib/game-status.js`'s `findLiveJob` (includes
  `queued`, excludes GC jobs — pause/resume/download are prefill-only
  concepts) and `detailJobActions`'s exact queued/running/paused ->
  action-set table from api/README.md's "Job control".
- `detail-wording.test.js` — `web/js/lib/detail-wording.js`:
  `confirmedCurrentWording`'s three cases, incl. the post-deletion shape
  (`last_manifest_check` survives, `last_prefill_at` does not) rendering as
  `CONFIRMED_BEFORE_CACHE_CLEARED` rather than a bare, contradiction-reading
  timestamp.
- `gc-log-summary.test.js` — `web/js/lib/gc-log-summary.js`: fixtures are
  the EXACT log text api/README.md quotes for a dry run and an executed run
  (same fixtures the Android sibling's `GcLogSummaryTest.kt` pins) —
  null/blank/no-totals-line input, dry-run `would_delete`/`held_back`
  scoped to the TOTALS line (not an earlier per-depot `held_back` with the
  same key name), the `\b`-guarded `bytes_freed` vs. the `dedupe_`/`total_`
  prefixed lookalikes regardless of key order.
- `gc-flow.test.js` — `web/js/lib/gc-flow.js`: the state machine's one
  guarantee (GC EXECUTE is never sent without an explicit confirm after a
  dry run) as a full parametrised pin — `confirm_execute`/`request_execute`
  rejected from every state except their one accepting predecessor — plus
  the full dry-run-then-execute path, cancellation mid-poll (Cancelled, not
  Error), a stale poll result for a different job id being ignored in BOTH
  polling states, and `start_dry_run`'s Idle/ExecuteDone/Error/Cancelled ->
  accepted, everything mid-flight -> rejected table.
- `detail-render-plan.test.js` — `web/js/lib/detail-render-plan.js`: the
  round-7 patch-vs-rebuild structural key for the sheet (WP brief: "must
  not rebuild animated nodes on poll ticks"). Two named mutation targets —
  a `dispKind` change and a depot's sharing TAG changing (a co-owner's cache
  state moved) must each change the key — plus a `trackedJobStatus`
  change, and the documented fact that a size-only tick is simply not part
  of the key's inputs at all (the caller never feeds bytes into it).
- `demo-data-gc.test.js` extends `demo-data.js`'s coverage with this WP's
  additions: `last_manifest_check` now appears on `GET /v1/games` AND
  `GET /v1/games/{appid}` (it was missing entirely before this WP, a real
  gap since the field has existed on the real API since the WP 4c mini-WP);
  `POST /v1/cache/{appid}/gc` — dry run default, `{execute:true}`, 404s
  (unknown app / no depot mappings), 422s (unrecognised field / non-boolean
  `execute` — no lax-mode coercion), the documented absence of a `409` for
  an active prefill job (GC serializes on the worker in the real API, so
  there is nothing to guard against), dry-run/execute mode-scoped dedupe,
  and a completed job's `log_excerpt` being REAL `GC totals (...)` text
  `lib/gc-log-summary.js` parses correctly end to end (not a fixture string
  tailored to the parser). WP 4a.8 extends the fixtures again: Glass
  Meridian's dry-run/execute log lines now carry the FULL real key set from
  `api/vault_api/gc_execute.py`'s `GcRunReport.log_text` (`orphans`,
  `already_gone`, `dedupe_removed`, `dedupe_bytes_freed`, `problems`,
  `declined`, `depots_touched`, `needs_force_set_for`, ...), including a
  non-zero `held_back` in both modes — before this WP every demo GC scenario
  hardcoded `held_back=0 (0 bytes)`, so that branch of
  `lib/gc-log-summary.js` (and the detail sheet's "N chunks held back" note)
  was never exercised by demo mode at all. Verified the held-back bytes
  survive an execute run (a time-window rule, not something an execute run
  clears).

### WP 4a.8 — End-to-end + a11y pass

Three new files, none of them DOM-building views/components (those stay
verified live, per every WP above's posture) — these are the pure
STACK ARITHMETIC and DOM-WIRING primitives introduced or hardened by this
WP's keyboard-nav/focus-trap work, which genuinely are testable headlessly:

- `fake-dom.js` — not a test file itself, a shared minimal in-memory DOM
  shim (`createElement`/`createElementNS`/`classList`/`dataset`/
  `addEventListener`/`dispatchEvent`/`focus`/`activeElement`) used by the
  two files below. Same spirit as `store-poll-loop.test.js`'s bare-object
  fake `document`, extended just far enough to run `router.js` (touches
  `window.addEventListener` at module load) and `sheet-dialog.js`/
  `status-icon.js` (build real element/SVG subtrees) — NOT a jsdom
  replacement, see its header. Extended (WP 4e.6, Opus review should-fix
  S3) with `FakeElement.replaceChildren` for `rail-panel-wiring.test.js`.
- `modal-stack.test.js` — `web/js/lib/modal-stack.js`, the new shared
  "which overlay is topmost" stack behind the focus trap both
  `onboarding.js` and `sheet-dialog.js` had deferred to this WP: pushing one
  overlay marks `#app` `inert`+`aria-hidden`; NESTING a second overlay (the
  detail sheet's own delete/GC-execute confirm dialogs open ON TOP of the
  already-open sheet) makes the FIRST one inert too, leaving only the
  topmost reachable — the mutation target named in the test is exactly "a
  plain counter instead of a stack cannot express this". Also covers
  out-of-LIFO-order popping, idempotent push, and — the load-bearing half
  found LIVE during this WP's e2e pass, not designed up front — the
  centralized Escape dispatcher: pressing Escape calls ONLY the topmost
  overlay's `onEscape`, never a lower one's. Before this dispatcher existed,
  `sheet-dialog.js` and the confirm dialogs each bound their OWN independent
  `document` keydown listener, and one Escape press fired BOTH — closing a
  GC-execute confirm AND the whole detail sheet behind it in one keystroke
  (reproduced live in the Browser pane, then pinned here as a mutation
  target: "if this dispatcher called every stacked `onEscape` instead of
  only the topmost's...").
- `dialog-wiring.test.js` — DOM-harness regression pins for the two WP 4a.7
  wiring fixes recorded in docs/WORKPACKAGES.md's Phase 4a header as due
  this WP: (1) a sheet opened via `createSheetDialog` closes when the view
  changes (`onViewChange(() => dialog.close())`, the exact one-liner every
  real sheet component uses) and returns focus to its invoker; (2) a status
  icon whose word is already shown visibly elsewhere is marked
  `aria-hidden` WITHOUT deleting its underlying `sr-only` label node (the
  "avoid double announcement" pattern `components/notifications.js`,
  `components/clients-sheet.js`, `views/downloads.js` and
  `components/game-card.js` all apply at their own call sites) — pins the
  shared primitive/contract every one of those call sites relies on, not
  each call site's own module (those stay DOM-building/verified-live per
  their existing posture). Also covers Escape-closes-the-sheet now that the
  WP 4a.8 trap is in place.

#### Real-server e2e checklist (repeatable, scripted — not a from-scratch investigation)

Per the WP 4a.7 "hang lesson" (docs/LEARNINGS.md-adjacent, recorded in this
WP's brief): keep browser sessions SHORT and SCRIPTED, prefer curl/`fetch`/
`dispatchEvent` assertions over waiting on animations or racing timers.
Two independent passes, both re-run for this WP and safe to re-run for any
future one:

**A. Live vault-api (uvicorn, temp DB + cache dir, no Docker needed):**

```
cd api
VAULT_API_KEY=<any string> \
VAULT_DB_PATH=<scratch>/vault.db \
VAULT_CACHE_ROOT=<scratch>/cache \
VAULT_WEB_DIR=<repo>/web \
python -m uvicorn vault_api.main:create_app --factory --host 127.0.0.1 --port 8123
```

Then, with `curl -H "X-Api-Key: <key>"`:

1. `GET /` and `GET /library` both 200 (SPA fallback serves `index.html`);
   `GET /css/theme.css` 200 (static asset mount); `GET /v1/games` with no
   key 401.
2. `GET /v1/settings`, `PATCH /v1/settings` (e.g. `vault_name`), re-`GET`
   confirms the `db`-sourced override round-trips.
3. `GET /v1/steam/owned-games?steamid=...` with no relay key configured ->
   409 (the relay's honest "not configured" branch); `PUT /v1/steam/key`
   with a malformed value -> 422.
4. `POST /v1/prefill {"appids":[...]}` with no `VAULT_STEAMPREFILL_PATH`
   configured -> the job is accepted (202-shaped body), the worker picks it
   up, and `GET /v1/jobs/{id}` settles to `status: "error"` with a clear
   `log_excerpt` diagnostic — **this error path itself is the valid e2e
   assertion**: an honest, actionable message, no crash, `GET /v1/health`
   still 200 afterwards.
5. Then drive the SAME server from the Browser pane: onboarding step 1
   "Test connection" against the real key, keyboard-only card open
   (`dispatchEvent(new KeyboardEvent("keydown", {key:"Enter"}))` — more
   reliable in this harness than an OS-level key press racing element
   focus, see the coder's report), confirm `#app[inert]`/`aria-hidden`
   while the sheet is open and Escape restores both.

**B. Demo mode (`localStorage.setItem("steamvault.demoMode","1")`, reload)
— no server needed, exercises the richer multi-game/GC/bypass fixtures:**

1. Onboarding overlay's `#app` inert/focus-trap (first-run).
2. Library: keyboard-open a card, bulk multi-select (`contextmenu` event ->
   select mode -> click a second card -> bulk bar), bulk delete confirm
   (nested-overlay-free — this one IS the only modal), Keep/Delete.
3. Detail sheet: GC dry run -> plan shown -> Execute -> the SECOND,
   NESTED confirm dialog opens ON TOP of the sheet — verify `#app` inert,
   the SHEET (now second-from-top) also inert, only the confirm reachable,
   Escape closes only the confirm (not the sheet), a second Escape then
   closes the sheet.
4. Delete-from-cache confirm: same nested-overlay checks.
5. Bypass banner -> Details -> clients sheet -> Escape -> banner still
   there (Dismiss is a separate, explicit action).
6. Notification bell: badge clears on open, tapping a `job_finished` row
   navigates to Downloads with that job's history row pre-expanded.

Both passes were run for this WP; see the coder's report for what they
found (a null-`name` fallback bug in `game-card.js` only reachable against
a real, un-Steam-linked server; the `[hidden]`-vs-author-`display` CSS
cascade bug on `.btn`/`.onbnav`; the library.js confirm-dialog-nested-
inside-`#app` bug; the Escape-closes-both-overlays bug — none of which a
demo-mode-only or unit-test-only pass would have surfaced).

#### Honest list of what this pass could NOT verify

Everything below needs a real device/OS or a real Steam-facing network path
— the Browser pane and `node --test` cannot exercise them, and no amount of
additional scripting in this harness closes the gap. Left for the Zeus/
Android session:

- **A real OS-level `prefers-reduced-motion` toggle.** This WP verified
  reduced motion by reading the CSS cascade (theme.css's `!important`
  block wins over every `animation`/`transition` declaration in the
  stylesheet, confirmed by grep — no competing `!important` exists) and,
  after the review fix, by reading `web/js/views/downloads.js`'s
  `prefersReducedMotion()` source — never by actually flipping the setting
  in a real OS and watching the status-icon animations/scroll actually stop.
  The Browser pane exposes no `prefers-reduced-motion` emulation.
- **Real Steam CDN cover art, and its offline/blocked-host fallback, from
  an actual phone.** `lib/cover-art.js`'s CDN URL and the procedural
  fallback tile (`img.addEventListener("error", () => img.remove())` in
  `game-card.js`) were verified against the live vault-api on a desktop
  Browser-pane session with working internet — not on a phone on a LAN
  where the CDN might be genuinely unreachable (the real "offline" case
  this fallback exists for).
- **Demo mode as an actual FIRST-RUN experience** — walked through in this
  WP via `localStorage.setItem("steamvault.demoMode","1")` set directly,
  never by tapping "Skip for now — browse in demo mode" from a truly fresh
  onboarding overlay with no prior app state at all (browser profile,
  service workers, etc.) the way a real first-time visitor would arrive at
  it.
- Real screen reader (NVDA/JAWS/VoiceOver) AUDIO verification — this WP
  confirmed the DOM semantics (`aria-hidden`, `aria-label`, `aria-pressed`,
  `role`, focus targets, and `inert`'s spec-mandated AT-hiding behaviour)
  are correct, never that a real screen reader actually announces them the
  way intended.
- Real touch/long-press timing (the 420 ms press-and-hold that enters
  multi-select) on an actual touchscreen — simulated here via a synthetic
  `contextmenu` event, never a real touch sequence.
- `inert` attribute support outside Chromium (the Browser pane is
  Chromium-based; `inert` is Baseline-widely-available but was not
  cross-checked on Firefox/Safari).
- GC execute against a REAL depot cache with real chunk files on disk —
  this WP's live-server pass only reached the `POST /v1/prefill` honest-
  error path (no `VAULT_STEAMPREFILL_PATH` configured, so no real
  download/cache content ever existed to run GC against); the GC flow
  itself was only exercised through demo mode's simulated model.
- Real multi-client bypass detection (demo fixtures only — no second real
  vault-agent on the LAN during this pass).
- Performance/rendering with a real, large (400+ game) library — the live
  server had exactly one game; demo mode has six.

### WP 4c-web — "Check & update all cached games" (Phase 4c)

Same posture as every prior WP: the DOM-building trigger itself
(`views/library.js`'s new header button) is not unit-tested directly — the
mixed-outcome wording, the forced-run heads-up composition, the mid-loop-5xx
recovery signal, and the in-flight button lock are pulled into a DOM-free
`web/js/lib/cached-prefill-outcome.js` and tested here, plus a demo-mode
extension for the new route. **Round 1 review (Opus): FAIL — one blocker,
four should-fixes, all fixed in this round** — see the inline notes below
for what changed and how each fix was mutation-verified.

- `cached-prefill-outcome.test.js` — `web/js/lib/cached-prefill-outcome.js`:
  - `partitionCachedPrefillOutcome`: sorts a `POST /v1/prefill/cached`
    response into `queued`/`alreadyQueued`/`alreadyRunning`/`alreadyPaused`
    regardless of input order; empty/non-array input treated as empty; a
    deduplicated entry with an unexpected non-`queued`/non-`paused`
    in-flight status still lands in `alreadyRunning` rather than vanishing
    (same "unknown routes somewhere honest, never oblivion" posture as
    `job-partition.js`'s status handling). **S1 (round 1 should-fix):**
    `alreadyQueued` is its OWN bucket, not folded into `alreadyRunning` —
    `enqueue_prefill` returns an existing job with ITS OWN status, and a
    double-press before the single worker claims anything is the COMMON
    case, not an edge case; mutation-verified by folding the `queued`
    branch back into `alreadyRunning` and watching 4 tests fail by name.
  - `summarizeCachedPrefillOutcome` — the mutation-worthy pins the WP brief
    named, each verified by reverting the fix and watching the named test
    fail, then restoring it: (1) *"a paused dedupe is NEVER worded as
    queued/started"* — wording the paused count with "queued for
    check & update" instead of "paused — resume or cancel..." fails this
    test (and others) by name; (2) *"empty selection reads as a normal
    outcome, not a failure"* — wording the empty case as a failure fails
    this test by name. **S1:** a `queued`-status dedupe is worded
    "N already queued", distinct from `alreadyRunning`'s "N already in
    progress" — a job still waiting in the FIFO queue is not "in progress".
    **S2 (round 1 should-fix):** every string carries the full "check &
    update" wording ("N queued for check & update", not "N queued for
    checking") — the plan's honesty rule applies to the whole action's
    language, not just the button label. **Blocker (round 1, live-
    reproduced in headless Chrome): the forced-run note used to be composed
    by the CALLER from its own `GET /v1/games` snapshot, unconditionally —
    "Nothing cached to check. (1 forced...)" claimed work that provably did
    not start.** Composition now lives entirely in this function, gated on
    `partition.queued.length > 0` and scoped to ONLY those appids
    (`countForcedCachedGames`'s new `queuedRefs` parameter, never the whole
    snapshot) — two named regression tests (`BLOCKER REGRESSION: empty
    response + a stale needs_force game...`, `BLOCKER REGRESSION:
    all-deduplicated response + a forced game...`) pin both shapes,
    mutation-verified by reverting the gate/scoping to the exact pre-fix
    behaviour and watching both reproduce the reviewer's literal string
    (`'Nothing cached to check. (1 forced — ...)'`) before being restored.
  - `describeCachedPrefillError` — the mid-loop-5xx honesty rule
    (api/README.md: each app is enqueued in its own committed transaction,
    so a `5xx` partway through can leave earlier apps durably `queued`):
    only `ERROR_KINDS.SERVER` sets `refresh: true` (the signal `library.js`
    maps to `store.refreshNow()`); every other kind (401, 422/409-folded
    `validation`, network, `not_found`, `unknown`) does not force a
    refresh, since those genuinely mean nothing was queued by that call.
    Prefers the server's `detail` text when present, never throws on a
    non-`ApiError` input.
  - `countForcedCachedGames(queuedRefs, games)` — **re-scoped in round 1**
    from a whole-`games`-snapshot count to only the appids present in
    `queuedRefs` (the `queued` bucket) that also carry `needs_force: true`
    in `games`; an appid in `queuedRefs` with no matching `games` entry is
    not counted (fails safe, never guesses).
  - `createCheckAndUpdateAction` — the in-flight guard, DOM-free by
    construction: a `run()` call while a manually-gated fetcher's promise
    is still pending returns `{skipped: true}` WITHOUT invoking the fetcher
    a second time; a `run()` after the previous one settles (success OR
    rejection) calls the fetcher again; a rejected fetch still clears the
    in-flight flag so the guard is usable immediately afterward. **S4
    (round 1 should-fix): the no-op assertion is now SYNCHRONOUS** —
    asserted on the fetcher's call count immediately after issuing both
    `run()` calls, before either promise is awaited or the deferred fetch
    is resolved. The original version awaited the second `run()` first,
    so a broken guard (removed `if (inFlight)` check) made that `await`
    hang on the SAME never-yet-resolved deferred instead of failing —
    mutation-verified: removing the guard now fails this test in under 1ms
    (`calls === 1` assertion, `2 !== 1`) with no `--test-timeout` needed.
- `demo-data-cached-prefill.test.js` extends `demo-data.js`'s coverage with
  the new `POST /v1/prefill/cached` route: selects every game that maps at
  least one EXCLUSIVE or LAST-CACHED-REMNANT depot (the real
  `deletion.appids_with_cache_content` rule since WP 4f, applied to the
  demo by WP WEB-FIX-1 S1 — see that section below), sorted ascending; response shape matches `PrefillJobRef`
  exactly; a brand-new job dedupes `false`; the seed data's already-`running`
  job dedupes onto itself with no second job created; pausing that job first
  and re-calling the route dedupes onto it with `status: "paused"` — the job
  itself stays paused afterward, proving this route never resumes it.
  **S1 fixture (round 1):** pausing then RESUMING that job (a real,
  reachable sequence — `POST /v1/jobs/{id}/resume` genuinely returns
  `status: "queued"`, api/README.md "Job control") then re-calling the
  route dedupes onto it with `status: "queued"`, exercising the
  `alreadyQueued` bucket end to end through the actual demo route rather
  than only via hand-built fixtures. Also: any request body (including a
  bogus `{"appids": [...]}`) is silently ignored, never read as an explicit
  id list; the empty-selection case after clearing every game's cache
  (reusing the shared-depot two-call dance `demo-data.test.js` already
  exercises) returns `[]`; and that this route shares the exact SAME
  per-appid enqueue helper `POST /v1/prefill` uses (`enqueuePrefillForAppid`,
  extracted from that route in this WP) rather than a second,
  potentially-drifting enqueue mechanism. **N1/N2 (round 1 nitpicks,
  documented in the test file's header, not fixed — both pre-existing,
  neither a two-line change):** a brand-new demo job flips straight to
  `"running"` on creation (the real contract allows `deduplicated: false`
  to arrive as `"queued"`, which this demo model never produces at that
  moment). (The second nitpick, demo selection keying on
  `depots.length > 0`, was closed by WP WEB-FIX-1 S1.)

### WP 4e.1 — Desktop layout foundation (Phase 4e)

Different posture from every WP above: this package's product is mostly
CSS/breakpoint plumbing, not JS decision logic, so its tests are not
"pull the logic into `lib/`" ports — they are (a) structural static analysis
of the CSS/JS source text itself, and (b) fixture-generation logic that
genuinely is pure and DOM-free.

- `css-hygiene.test.js` — two general-purpose lints, real static analysis
  (re-derived from source on every run, not a hand-typed list):
  1. every CSS class carrying an author `display` rule that is ALSO
     hidden-toggled somewhere in `web/js/` must have a matching
     `SELECTOR[hidden]{display:none}` guard — closes the `.btn`/`h4.sec`/
     `.onbnav` bug class (docs/LEARNINGS.md, "Web UI") permanently, catching
     a NEW instance built the same way, not just re-verifying the three
     known ones. Recognizes `.hidden = ...`, `setAttribute("hidden", ...)`
     and `removeAttribute("hidden")` as toggle sites (the first version of
     this lint only recognized the property form — an Opus review nitpick,
     N1, found and closed in the fix round: a `setAttribute`-based toggle on
     an unguarded class survived undetected until then). Documented
     limitations (same review, same header): a multi-class compound selector
     (`.jobcard.active`) or a descendant selector (`.grid.list .cap`) is
     invisible to the display-rule side even if JS hidden-toggles a matching
     element; the JS-side scan is per-FILE and flat, not block/function
     scoped, so two same-named identifiers in different functions of one
     file share one tracked class set. Neither is exploited by real code as
     of this WP.
  2. no `!important` anywhere except theme.css's `prefers-reduced-motion`
     block — in particular, none inside a NEW `@media (min-width...)`
     breakpoint block, which is exactly the kind of specificity workaround
     that makes later overrides unpredictable.
  Both mutation-verified in the fix round: removing a guard, adding an
  `!important` to a breakpoint block, and (new in the fix round) a
  `setAttribute`-based toggle with no guard at all — each kills its named
  test; reverted afterward.
- `css-layout-foundation.test.js` — structural pins for the spatial tokens,
  breakpoints (BP-M/BP-L/BP-XL) and the nav-to-rail conversion. Two
  anti-regression pins added in the Opus review fix round, both mutation-
  verified: `--w-wall` must equal `960px` in EVERY breakpoint block (a first
  version of this package widened it, which measurably made the mockup's
  already-oversized cover tile BIGGER — see docs/PROJECT_PLAN.md's Phase 4e
  section for the full story); every `--nav-h` assignment must carry an
  explicit `px` unit (blocker B1: a bare `--nav-h:0` makes `.bulk`'s
  `bottom:calc(var(--nav-h) + 14px)` a calc() type mismatch, invalid at
  computed-value time, silently falling back to `bottom:auto` — the bulk
  action bar landed off screen at every width >=1024px until this was
  caught). Also pins the BP-L `.nav-pip` `order:1` fix (blocker B2: DOM-first
  no longer means visually-first once the pip becomes `position:static` in
  a row-direction flex container) and `.banner-wrap` picking up `--w-wall`
  (not `--w-text`) at BP-L so it aligns with `.view-root` in the same visual
  column (should-fix S4). **Correction (Opus review blocker B3):** this
  file's own header used to claim "physically impossible for this WP to
  change anything below BP-M" — false: `.banner-wrap{width:100%}` (the
  shrink-to-fit bug fix) is a deliberate TOP-LEVEL rule change that alters
  real rendering from ~430px up to 719px (measured against a pre-WP
  baseline — see the header for the numbers). Only the mockup's own 390px is
  genuinely byte-identical end to end; "base is untouched" in this file's
  test names means the SHELL/RAIL/BREAKPOINT machinery is correctly gated,
  not that literally nothing renders differently below 720px.
- `demo-data-large-library.test.js` — `web/js/demo-data.js`'s
  `generateSyntheticGames`/`resetDemoData({librarySize})`: deterministic
  (same count -> same shape, modulo the one wall-clock-derived field,
  `last_prefill_at`, whose exact ISO string a millisecond apart is expected
  to differ and is stripped before the determinism comparison — its SHAPE
  is pinned separately: a real ISO string for "done" games, exactly `null`
  for "idle" ones, per N4), appid ranges clear of every other fixture,
  mixed cached/idle shape, and (Opus review should-fix S1) `needs_force`
  is the exact inverse of "cached" — an idle/never-filled row is
  `needs_force=true` and a done row is `false`, matching api/README.md's
  real lifecycle exactly (the pre-fix default produced `idle` +
  `needs_force=false`, a shape the real API can never emit).
- `rail-content.test.js` (WP 4e.6) — `web/js/lib/rail-content.js`'s three
  pure presentation functions, no DOM: `vaultNameFromSettings` (a real
  effective value trims and returns; a malformed response/missing entry/
  empty-after-trim value are all `null`); `cacheFootFromSummary` — the
  headline **unknown-vs-zero** guarantee: no summary yet or a malformed one
  is `null`, but a GENUINE zero (`total_bytes:0`, an empty cache;
  `free_disk_bytes:0`, the disk is full) renders as a real `"0.0 GB"`, never
  collapsed into "unknown" the way the tile-badge helper `formatBytesGB`
  deliberately does for the same input shape (two named mutation targets:
  swapping the module's `formatBytesGBOrZero` import back to `formatBytesGB`
  kills BOTH the total_bytes and free_disk_bytes zero pins); and
  `versionFromSettings` (coordinator addition mid-WP, confirmed shape from
  the parallel WP 4e.7 `api/` package: a top-level `server_version` string,
  sibling of `readonly`) — absent/non-string/empty are all `null`, a real
  value is trimmed and `v`-prefixed (not double-prefixed if already
  `v`/`V`-prefixed), and a pathologically long value is clamped with a
  trailing ellipsis (mutation target: returning `""` instead of `null` for
  the absent case kills the exact-null pin by name).
- `demo-data-settings.test.js` gained a pin (WP 4e.6/4e.7) for
  `GET /v1/settings`'s new `server_version` field: a top-level string
  sibling of `readonly`, never a row inside `settings` (matching the real
  endpoint's shape — the field has no source precedence and `PATCH` rejects
  it as an unrecognised key).
- `rail-panel-wiring.test.js` (WP 4e.6, Opus review should-fix S3, new) —
  `web/js/components/rail-panel.js`'s DOM-wiring, refactored into a
  dependency-injected `createRailPanel()` factory specifically so this file
  could exist (the same `store.js`/`store-singleton.js` split, applied to a
  component for the first time; `app.js` now performs the one real,
  side-effecting call this file used to make on import). Drives it with
  `fake-dom.js` `FakeElement`s and trivial fake store/api objects — no real
  `document`, `store-singleton.js`, or network. Covers: the two mutations
  that used to pass the full suite silently (a fabricated `"Free null"` row
  when `free_disk_bytes` is `null`; a poll failure blanking the rail
  instead of leaving the last real number on screen); the initial-snapshot
  paint (mirrors `bypass-banner.js`'s own pattern); `.rail-head`/
  `.rail-foot`/`#rail-version` all hidden when they have nothing to show
  (should-fix S4), including the AND-not-OR regression test for
  `.rail-foot`'s combined visibility rule; and the settings-fetch gating
  (skipped with no key and demo mode off, runs otherwise, never throws on
  rejection).

#### Honest list of what WP 4e.1's pass did NOT catch on its own — and what closed the gap

**This is the load-bearing lesson of this package's review round, not a
footnote.** The first live-verification pass measured exactly three
selectors — the cover tile, `.view-root`, and the nav rail's own box — and
concluded the shell conversion was safe. It was not: `#app` becoming a CSS
grid changes nothing about `position:fixed` containing blocks by itself
(that theory, floated during review, turned out not to be the actual
mechanism), but two REAL bugs existed anyway and were invisible to that
narrow a check:

- **B1** — the bulk action bar (`.bulk`, `position:fixed`) computed to
  `bottom:-143782px` (off screen) at every width >=1024px, because
  `--nav-h:0` (unitless) made `.bulk`'s `calc(var(--nav-h) + 14px)` invalid
  at computed-value time. Caught only by explicitly measuring `.bulk` itself,
  in multi-select mode, at desktop widths — not implied by checking the tile
  or the nav.
- **B2** — the Downloads nav button's queue-count pip visually preceded its
  own icon at BP-L (DOM order, not visual order, once the pip became
  `position:static`), throwing the whole row ~88px out of line with
  Library/Settings. Caught only by measuring icon/label x-position with an
  ACTIVE JOB running — a library at rest never shows the pip at all.

**The fix, going forward:** any package that changes the app shell's
layout mode (flex<->grid, adding/removing a rail/sidebar, repositioning the
nav) must explicitly re-check every OTHER `position:fixed`/overlay surface
for "does it still land on screen" — `.bulk`, `.sheet-backdrop` (the
notifications panel, the clients sheet, the game detail sheet all share
this), `.dialog-backdrop` (delete/GC-execute confirms), and `#toast` — not
just the surfaces the package's own diff obviously touches. Not their
GEOMETRY/sizing at the new breakpoints (that is later, narrower Phase 4e
work) — just "is it reachable and visible at all". WP 4e.1's fix round did
this for all four listed surfaces at 1024/1280/1920px in multi-select with
an active job (the exact conditions that exposed B1/B2) and found no
further issues; see the coder's report for the live measurements. This
check has no headless equivalent in this suite (it is fundamentally a
real-rendering/layout-engine question, same posture as every other item in
WP 4a.8's "Honest list" above) — it must be repeated by hand for every
future shell-layout-changing package, not assumed safe from CSS pins alone.

### WP 4e.2 — Auto-fill library grid + toolbar band (Phase 4e, D-3/D-4)

Same posture as WP 4e.1: CSS/breakpoint plumbing, not JS decision logic, so
the new pins are structural static analysis of the source text, plus one
small JS relabeling change with no headless test of its own (nothing in
this suite exercises `library.js`'s DOM against jsdom — see the top of this
file; the label strings are verified live, in the running app, alongside
the geometry claims below).

- `css-layout-foundation.test.js` — the two WP 4e.1 anti-regression pins
  that asserted `--w-wall` stays flat at 960px everywhere are UPDATED (not
  deleted, per the brief's own instruction) to the new, intentional
  three-value progression this package ships (960px base -> 1600px BP-L ->
  2000px BP-XL), pinned BY POSITION so a future reordering or drop still
  fails loudly. First-pass new tests: the auto-fill wiring itself (`.grid`/
  `.grid.cols3` both `repeat(auto-fill,minmax(var(--tile-min),1fr))` with
  DIFFERENT `--tile-min` values, `.grid.list` deliberately left with no
  BP-L override at all — relying on CSS specificity over the plain `.grid`
  rule, asserted structurally rather than assumed); `.bulk`'s BP-L override
  re-derived from `--w-wall`/`--rail-w`/`--gutter` instead of `--w-text`;
  `.view-library`'s BP-L named-area grid assigning all six in-flow children
  BY CLASS (`.view-library > .search` etc. — never `nth-child`); `--search-w`
  declared in theme.css. One trap found and worked around while writing the
  `.grid.cols3` pin: this file's own `ruleBody()` helper does a plain
  `indexOf(selector + "{")`, and the combined selector `.grid, .grid.cols3{
  ...}` rule (needed so both share one `grid-template-columns` formula)
  already CONTAINS the substring `.grid.cols3{` right after the comma —
  `ruleBody(block.body, ".grid.cols3")` would silently return THAT rule's
  body instead of the intended standalone `--tile-min` override a few lines
  below it. Worked around with a literal substring check on the exact
  standalone rule text rather than a "preceded by a non-comma boundary"
  regex (tried first — whitespace precedes both the comma-list's embedded
  occurrence AND the standalone rule, so a boundary class that includes
  plain whitespace cannot tell them apart either).

  **Opus review round 1: FAIL — one blocker, five should-fixes, all fixed
  and re-verified.** The first pass's structural pins asserted mechanisms
  were DECLARED without asserting they were WIRED to the claimed VALUE —
  four real mutations survived 456/456 as a result, all now killed by name:
  (1) switching the BASE (phone) `.grid`/`.grid.cols3` rules to `auto-fill`
  — the mockup-frozen surface the file's own header claims is untouched;
  (2) deleting the BP-L `.grid.cols3` reset entirely — the "fix the tile
  guarantee made concrete, not merely asserted" comment right above it in
  app.css; (3) changing `.view-library`'s `grid-template-columns` from
  `var(--search-w) 1fr` to `1fr 1fr` (the cap silently vanishes); (4)
  swapping the area map's `"search chips"` row to `"chips search"`. Two new
  tests close (1)/(2): a `.grid`/`.grid.cols3` base-rule pin (extending the
  house pattern this file already used for `.nav`/`.chips`/`.app`) and a
  literal restatement check of the seven BP-L reset rules (the file
  originally shipped eight — a review nitpick found the eighth,
  `.grid.cols3 .meta .size`, was restating a value already inherited from
  the `.meta` reset one line above it, so it was removed rather than
  pinned; the test asserts its absence explicitly so a "ninth rule quietly
  missing" reading is impossible). (3)/(4) are closed by extending the
  existing `.view-library` test with a `grid-template-columns` value
  assertion and five literal area-row checks (head/check/search+chips/
  cards/hint, in that exact order).

  The two `--w-wall`-progression pins also carried a **wrong rationale**
  (should-fix S2): the fix-round comment claimed BP-L's 1600px "lands at
  ~186px @1920, capped" — false, since 1920px falls in BP-XL's own range,
  where nothing is capped by the BP-L value at all (BP-L's widest possible
  viewport is 1799px, whose main column, 1567px, never reaches 1600px in
  the first place). Both the CSS comment and this test's assertion message
  are corrected to state the real story: BP-L's cap is a deliberate guard
  against a future `--rail-w`/breakpoint change, not an active constraint
  today. `--tile-min`'s pinned value also changed, 210px -> 176px
  (Comfortable) and 168px -> 150px (Compact) — the operator's decision
  after a live measurement showed 210px producing tiles up to 1.42x the
  mockup's own tile size (should-fix S1; see theme.css's `--tile-min`
  comment for the `minmax(F,1fr)` overshoot mechanism this number has to
  respect, and the sawtooth-not-smoothed decision that comes with it).

  **Blocker B1**, pinned here too even though the fix lives in app.css: a
  dedicated test asserts `.empty{grid-column:1/-1}` — see the app.css
  section below for the bug itself.
- `css-hygiene.test.js` — the display/hidden cross-reference lint gains a
  fourth toggle idiom, `el.toggleAttribute("hidden", cond)` (and the bare
  `el.toggleAttribute("hidden")` form) — the brief that shipped the
  `setAttribute`/`removeAttribute` pair in WP 4e.1 explicitly named this as
  the next gap. No file in `web/js/` uses it yet, so — unlike the sanity
  test that exercises the other three forms against the real `.btn`/
  `h4.sec`/`.onbnav` offenders — this is pinned against a throwaway fixture
  file written to and removed from `web/tests/` inside the single test that
  needs it (never `web/js/`, so it can never leak into the real scan),
  proving the new regex recognizes both the two-argument and bare forms.
  **Opus review nitpick, fix round:** all three attribute-based regexes
  (`setAttribute`/`removeAttribute`/`toggleAttribute`) were case-SENSITIVE —
  `toggleAttribute("HIDDEN", true)`/`setAttribute("Hidden", "")` both
  genuinely set the `hidden` attribute in a real DOM (HTML attribute names
  are case-insensitive by spec) and both survived the lint silently. Fixed
  with an `i` flag on all three, pinned by a second fixture test exercising
  the reviewer's own mixed-case spellings.

  **Opus review round 2: FAIL — one blocker (B2), plus S6/S7 and further
  nitpicks, all fixed and re-verified.**

  **B2** was round 1's OWN should-fix S1 sentence, still wrong after being
  "fixed" once: it claimed 176px keeps the range at "~176-205px (mockup
  ±18%)", which is wrong on both the range and the percentage (a
  4px-resolution sweep over 396 widths — 1024-2600px plus 3440px, both
  densities — measured the real range: 177-222px, 1.02x-1.29x the mockup
  tile; 220/173 is +27%, not ±18%) and directly self-contradicted by this
  same comment's OWN sawtooth example ten lines below (220.0px, sitting
  under the 205px ceiling the sentence right above it had just asserted).
  Corrected in `theme.css` with the clause a bare number-fix would have
  omitted: a <=205px ceiling at every width is unachievable with
  `minmax(F,1fr)` at all — it would need F<=158px, already below Compact's
  own 150px floor — so this was never a value the token failed to find.

  **S6 (operator decision):** narrowing the two floors to 176px/150px (to
  keep the corrected S1 range honest) made Comfortable and Compact render
  IDENTICALLY — same column count, same tile width within 0.2px — across
  several sub-1400px bands (1024-1076, 1208-1238, ~1396px up). Accepted and
  documented in `theme.css` and the plan's D-3 entry rather than narrowing
  Compact further (a ~135px floor to force visible separation would sit 22%
  below the mockup's design size, with no measurement behind it).

  **S7:** the round-1 B1 pin (`.empty{grid-column:1/-1}`, below) is
  class-specific — a hypothetical future class appended into `.grid` with
  no `grid-column` rule would reproduce the identical bug silently.
  Generalised into a new test scanning `renderGrid()` for
  `els.grid.appendChild(<call>)` sites, resolving each callee (excluding
  `buildCard`, the grid's actual content) to its function definition in the
  same file, and collecting every literal `className = "..."` it assigns.
  Two classes exist today (`.noresult`, `.empty`) and both are already
  correct — this closes the CLASS of bug, not just the one instance.
  Mutation-verified against a synthetic new class (`libnotice`) inserted
  into a copy of `library.js` and confirmed to fail the test by name, then
  reverted.

  Further nitpicks: the hidden-toggle lint's `.hidden = ` and
  `toggleAttribute(` regexes were still one character away from two more
  real idioms the reviewer's own probe found surviving — `el.hidden ||=
  true` / `el.hidden ??= true` (the literal `\s*=` required the `=`
  immediately, but `||=`/`??=` put extra characters in that gap) and
  `el.toggleAttribute?.("hidden", true)` (optional chaining put `?.`
  between the identifier and the call parenthesis). Both closed with a
  small character class each, pinned against a new fixture test exercising
  exactly those three forms, mutation-verified by reverting each regex in
  isolation and watching the test fail by name. `Object.assign(el,
  {hidden:true})` remains genuinely out of reach for a forward regex scan
  (the identifier and the property name never sit next to each other in
  the source) — documented as an acknowledged gap in the file's header
  rather than silently implied covered by the four idioms actually scanned
  for.

#### app.css (B1, blocker — Opus review round 1; corrected round 2)

`.empty` (the "no results" fallback `<p>`, appended as a direct child of
`.grid` by `library.js`'s `renderEmptyState`) had no `grid-column` rule,
unlike `.noresult` right above it in the same file. Under `.grid`'s BP-L
`auto-fill` rule, `.empty` collapsed into a single auto-fill track instead
of spanning the row — measured live: an 8.5%-of-row-wide block hard against
the left edge at 2560px. Reachable one click away in practice, not a
corner case: `renderChips` renders every filter chip including zero-count
ones, so a library with zero failed downloads still shows a clickable,
functioning "Failed 0" chip. Fixed with `grid-column:1/-1`, and re-verified
live via the exact reproduction: the real "Downloading 0" chip on the
400-game demo fixture now shows `.empty` measuring the grid's own full
width, not a narrow left-aligned sliver.

**This is a correction, not a new divergence (round 2):** the frozen
mockup already applies `style="grid-column:1/-1"` inline to this exact
element (`docs/design/vault-app-mockup.html:1826`), while its other two
`.empty` uses (the Downloads "no download running" line, and the
notifications-panel empty state) carry no such style — the WP 4a.3 port
dropped the inline style when translating the mockup's markup into this
stylesheet, so this restores mockup-faithful behaviour rather than
inventing new behaviour.

The fix's own "safe for every other `.empty` use" claim was also corrected
(round 2): the real inventory is ONE `emptyMessage()` caller in
`downloads.js` (its Active-section "no download running" fallback — an
earlier version of this comment said two) plus `settings.js:553`'s
`el("p", "empty", "Loading settings…")` loading state, omitted from the
earlier count entirely. Both live inside plain block containers (never
`display:grid`), where `grid-column` is simply ignored.

#### Live verification (no jsdom/browser here — done against a running vault-api + the 400-game demo fixture, per the brief)

Six-width table (390/768/1280/1440/1920/2560), both densities, plus `.bulk`
alignment in multi-select with an active+paused job, plus the fixed/overlay
surface re-check at 1024/1280/1920/2560 — see the coder's report (delivered
alongside this package) for the full numbers. Headline results, RE-measured
after the fix round with the operator's 176px/150px `--tile-min` values
(the numbers below supersede the first pass's 210px/168px-based table): the
cover tile is genuinely byte-identical to the WP 4e.1/pre-4e.1 baseline
below BP-L (173.0×259.5 at 390px, 354.5×531.8 at 768px — this package
changes NOTHING there); from BP-L up, both densities produce real,
width-derived column counts (e.g. 8×194.6px "Comfortable" / 10×153.3px
"Compact" at 1920px, 10×186px / 12×153px at the 2560px BP-XL cap); `.bulk`'s
left edge matched `.view-root`'s own content-area left edge to the pixel at
every one of 1024/1280/1440/1920/2560px, in both the `--w-wall`-capped and
uncapped regimes, reconfirmed after the tile-min change (which does not
affect `.bulk` at all — verified, not merely assumed); the toolbar band
(search capped at 420px, chips alongside) only applies from BP-L up — at
768px search and chips are still stacked, full width, byte-unaffected by
this package. A full 400-card grid rebuild (toggling density) measured
21-27ms, matching WP 4e.1's own ~29-33ms baseline within noise — column
count changing arrangement, not node count, confirmed live rather than
merely reasoned about.

Also re-measured for the S2 fix: BP-L's 1600px `--w-wall` genuinely never
binds within BP-L's own range — `cap binding?` false at 1024/1400/1700/
1799px viewports, no exception — confirming the corrected comment/test
message rather than the fix round's own first wrong claim. And for the S1
sawtooth documentation: 220.0px at a 1195px viewport (4 columns) drops to
177.6px at 1215px (5 columns), a ~19% shrink from a 20px WIDER window —
replacing the first pass's stale 210px-based example numbers, which no
longer applied once the token's value changed.

One methodology note for whoever verifies this live next: a raw
`document.dispatchEvent(new KeyboardEvent("keydown", {key:"Escape", ...}))`
used to close the notifications sheet during this verification left `#app`
stuck `inert` (the sheet's own close path evidently expects a real,
trusted-adjacent event sequence this synthetic dispatch didn't fully
replicate) — this blocked the NEXT click in the same session (the bulk
delete button) with no console error, only "nothing happened" as the
symptom. Recovered by clearing `#app`'s `inert` property directly and
re-verified the underlying delete flow works correctly once unstuck; closing
overlays via their actual UI controls (a real click on the panel's own close
affordance, or a trusted keypress) avoids the issue entirely. Not a product
bug — recorded here because it cost real time to diagnose and the next
person driving this suite by hand should not have to rediscover it.

### WP 4e.6 — The rail narrower, and earning its width (Phase 4e)

`--rail-w` 232px -> 180px (operator verdict: "232px feels unnecessarily
large for the three things in it"), plus two new rail-content pieces (vault
name, cache used/free) and a third added mid-WP by the coordinator (server
version) — see `docs/PROJECT_PLAN.md`'s Phase 4e section and
`docs/WORKPACKAGES.md`'s D-12 for the full narrative. Test additions:
`format.test.js` (+5, `formatBytesGBOrZero`), `rail-content.test.js` (new,
+21, all three pure content functions), `store-poll-loop.test.js` (+4, the
new "cache" resource loop), `css-layout-foundation.test.js` (+3, `--rail-w`/
display-toggle/`margin-top:auto`), `demo-data-settings.test.js` (+1,
`server_version`'s shape) — 462 baseline + 34 first pass, +2 more after the
coordinator's `server_version` shape correction (an "already starts with
v" pin and a real-world-shaped value pin) = 498 green, first-round PASS.

**Opus review: PASS, no blockers — four should-fixes, all addressed, suite
now 515 green.** S1 (`css-layout-foundation.test.js` +1): the WP 4e.2
`--w-wall:1600px` comment's own "unreachable at BP-L" claim went stale the
moment THIS package narrowed `--rail-w` — at 180px the cap genuinely binds
at BP-L's own top end (measured: `.view-root` 1600px, capped, at 1799px).
Comment corrected; a new structural pin computes the same breakeven
arithmetic from the live `--rail-w`/`--w-wall` tokens so the next such
change fails a named test instead of leaving a comment stale again. S2
(`store-poll-loop.test.js` +1): the cache loop's cadence
(`intervals.gamesMs`) was completely unpinned — mutating it to
`jobsFastMs` (2s in production, 7.5x more often) survived all 498 prior
tests, since none of them gave the cache loop a cadence distinct from
every other interval. A live-timing pin (short `gamesMs`, huge everything
else) closes it — wrapped in `try`/`finally` around `store.stop()` after a
REAL hang was measured while developing it (a failing assertion skipped
cleanup, leaving a 50s timer alive and hanging the test file's exit past a
120s harness timeout with zero output, until the retry with cleanup-on-
failure came back in under 100ms). S3 (`rail-panel-wiring.test.js`, new,
+15): `rail-panel.js` had NO test coverage at all — deleting
`if (payload.error) return;` (blanks the rail on a transient poll failure)
and deleting `if (foot.freeText !== null)` (renders a literal `"Free
null"`) both passed the full suite. Fixed by refactoring `rail-panel.js`
into a dependency-injected `createRailPanel()` factory — the SAME
`store.js`/`store-singleton.js` split applied to a component for the first
time, with the real wiring call moved into `app.js` — so it can be driven
headlessly with `fake-dom.js` elements (extended with `replaceChildren`)
and trivial fake store/api stand-ins; both mutations now die by name. S4
(`rail-panel-wiring.test.js`, folded into the same new file): "render
nothing, never a placeholder" had only been applied to the TEXT inside the
rail's elements, not their wrapping containers — a default install showed
an empty `.rail-head` with a bare divider line above the nav, and
`.rail-foot` carried dead space from `#rail-version`'s own margin even with
real cache data. `headEl.hidden`/`footEl.hidden`/`versionEl.hidden` are now
toggled alongside the text (via the plain `hidden` attribute, guarded in
app.css's BP-L block against the `display:block` override each would
otherwise lose to, same cascade fix as `.btn[hidden]`/`h4.sec[hidden]`);
`.rail-foot` hides only when BOTH the cache summary AND the version line
have nothing to show, pinned as its own AND-not-OR regression test.

Nitpicks also closed: `theme.css`'s rail geometry comment corrected its
136px/169px/12px figures to the actual measured 135px/157px/21.5px (the
`.nav` `border-right:1px` was missing from the original arithmetic; the
"12px slack" figure implicitly assumed a 3-digit pip, which `api.js`'s
`jobs(limit=20)` makes unreachable); the `--tile-min` overshoot band's low
end is now stated as exactly 176px (the token's own floor, by definition of
`minmax()`, not a swept approximation that can drift again) rather than a
third slightly-wrong sampled figure; this file's own WP 4e.6 write-up
corrected "paints... before subscribing" to the actual order (subscribe
first, then paint from whatever snapshot already exists).

**A correction to this WP's OWN brief, found before any code shipped, not
after (unlike WP 4e.1/4e.2's review-round corrections).** The brief that
opened this package asserted `GET /v1/cache/summary` was "already polled by
the store's slow loop". `git log -- web/js/store.js` showed exactly one
commit (WP 4a.2) since that loop's creation, and `api.cacheSummary()` —
defined in `api.js` since the same WP — had zero call sites anywhere in
`web/js/`. Rather than either (a) silently building on a false premise or
(b) invoking the brief's own fallback ("if a piece of data is not already
in the store, say so and leave it out") and shipping a rail with only ONE
content piece, the fourth `ResourceLoop` was added to `store.js` itself —
a real endpoint, the EXISTING `ResourceLoop` class (no new race-handling
code), the EXISTING slow cadence value (`intervals.gamesMs`, not a new
number) — on the reasoning that the brief's INTENT (the operator explicitly
rejected a rail with only one content piece: "A narrower rail with nothing
else in it would answer half of what they said") outweighed a literal
reading of a fallback clause written for the case where the underlying data
genuinely has no source at all, which is not what this is.

**Unknown-vs-zero, the headline guarantee, verified in BOTH directions —
this phase has burned a review round before on testing only one direction
of a fail-closed rule (LEARNINGS "Testing discipline").** `formatBytesGB`
(WP 4a.3, the library tile badge) deliberately treats 0 the SAME as
null/negative/non-finite ("nothing to print" — a never-downloaded game
shows the icon alone). Reusing it here would have been the natural,
WRONG choice: "0 bytes free" (disk full) and "0 bytes cached" (empty vault)
are both real, DIFFERENT-from-unknown facts the rail must show, not hide
behind the same "nothing to print" the tile badge uses for a genuinely
different situation. `formatBytesGBOrZero` exists specifically to invert
that one rule while keeping every other input (null/undefined/negative/
non-finite) mapped the same way — pinned with a `formatBytesGBOrZero(0) !==
formatBytesGB(0)` assertion in `format.test.js` so the two helpers cannot
silently converge again, plus the two dedicated "GENUINE zero" tests in
`rail-content.test.js` whose mutation target (aliasing the import back to
`formatBytesGB`) is recorded in this file's own header and re-verified live
by the coder (temporarily applied, watched both tests fail by name, then
reverted).

**A poll failure is NOT treated as "unknown" — a decision the pure-function
layer alone cannot prove, because it lives in the wiring, not the data.**
`cacheFootFromSummary(null)` is unknown (correct — no summary object at
all). But `rail-panel.js`'s subscription to the store's "cache" resource
deliberately does NOT call that function with the payload's `item` when the
payload is `{error}` — it leaves the LAST successful render on screen,
matching the exact convention `bypass-banner.js`'s own "clients"
subscription already uses (`if (!Array.isArray(items)) return;`). This is
DOM-wiring logic, outside what a pure-function test can see, so it was
verified the only way available: live, in the running browser, by
importing the already-loaded `api.js`/`store-singleton.js` modules from the
console, monkey-patching `api.cacheSummary` to reject, calling
`store.refreshNow()`, and confirming `#rail-cache`'s `textContent` was
byte-identical before and after the failure, then recovered on the next
successful poll after restoring the original function. Not encoded as a
headless test (there is no DOM/component-level test file for `rail-
panel.js`, same posture as `bypass-banner.js`/`notifications.js` — see this
file's own "Scope" section for why DOM-wiring components are deliberately
left to live verification, not unit-tested directly); recorded here as the
live-verification evidence a reviewer would otherwise have to re-derive
from source reading alone.

#### Live verification (no jsdom/browser here — done against a running vault-api + the 400-game demo fixture, per the brief)

Rail geometry (Chromium, the `--ui` font stack, live measurement, not
assumed — **corrected once already, Opus review round 1 nitpick: the
FIRST pass's 136px/169px/12px figures omitted `.nav`'s own
`border-right:1px`, restated below with the real measured edges**): at
180px, `.nav`'s content box is 159px (180 - 10px padding/side - the 1px
border-right), and a `.nav-btn`'s OWN content box (inside its further 12px
padding/side) is 135px, spanning x=22 to x=157; "Downloads" (the longest
label) spans x=55-120.5 (65.5px) at the rail's 12.5px `.nav-lb` size. Since
`.nav-pip`'s `margin-left:auto` always pushes it flush against the 157px
edge regardless of digit count, the number that matters is the label-to-pip
GAP, not a "slack past the button edge" a flush-right element can never
have: 21.5px for a one-digit pip, 17.4px for the worst REACHABLE case (a
two-digit "20" — `api.js`'s `jobs(limit=20)` makes a three-digit pip
impossible, not merely unlikely). No wrap, no overlap at any of
1024/1920/2560px.

Column/tile numbers, re-measured after the narrowing (compare against WP
4e.2's own table at 232px):

| width | Comfortable (was @232px) | Compact (was @232px) |
|---|---|---|
| 1024 | 4 cols / 190.3px (n/a — untested at 232px) | — |
| 1920 | 9 cols / 177.4px (was 8 / 194.6px) | 10 cols / 158.5px (was 10 / 153.3px) |
| 2560 | 10 cols / 186px (unchanged — BP-XL's 2000px `--w-wall` cap already bound before AND after) | 12 cols / 153px (unchanged, same reason) |

The freed 52px buys a genuinely extra column at 1920px (both densities
shift), but changes NOTHING at 2560px — the cap was already binding there
at 232px, and 52 more px of available column has nowhere left to go. Not a
bug: the exact "cap binding? false/true" mechanism WP 4e.2's own report
already established, re-confirmed rather than re-derived.

`.bulk`'s Δleft/Δwidth against `.view-root`'s own content-area edges
(`left+16`/`right-16` on `.view-root`'s border-box rect): measured exactly
`0`/`0` at 1024px, 1920px, and 2560px, in multi-select, with the bulk bar's
`.22s` slide-up transition settled — confirming WP 4e.2's
`left:calc(var(--rail-w) + var(--gutter))`/`right:var(--gutter)` formula is
genuinely parametric on `--rail-w` (no formula change was needed for this
WP to ship correctly), not merely re-verified by coincidence at one width.

Base (<720px) re-confirmed byte-unaffected at 375px (mobile-emulation
preset) and 719px: `.rail-head`/`.rail-foot` both computed `display:none`,
the bottom nav's `grid-template-columns` still exactly `121px 121px 121px`
at 375px, nav height unchanged.

Rail content, all three degrade states, live: `#rail-vault-name` /
`#rail-cache` / `#rail-version` all render correctly in demo mode
(`"steamhangar-demo"`, `"Used 6054 GB" "Free 466 GB"`, `"v0.1.0"` — the last
one only after the demo fixture was extended with `server_version` for 1:1
parity with the confirmed real shape); the cache-failure case is described
above (byte-identical before/after a monkey-patched rejection). The
"before the first poll" state could not be caught mid-flight in the BROWSER
(demo mode's fixture resolves too fast, sub-frame, to reliably observe the
gap from outside) — covered instead by the headless
`store.snapshot("cache")`-is-`undefined`-before-the-first-tick pin in
`store-poll-loop.test.js` plus `rail-panel.js`'s own order (subscribe FIRST,
then unconditionally paint from whatever snapshot already exists — which is
`undefined` at that point), which a source read, and now
`rail-panel-wiring.test.js`'s own headless pin, both confirm renders nothing
rather than a placeholder.

### WP 4e.3 — Overlay geometry at BP-L (Phase 4e, D-13)

The frozen mockup's overlays (`.sheet-backdrop`/`.sheet`,
`.dialog-backdrop`/`.dialog`) are a single 390px-phone-frame shape with no
responsive layer — on a >=1024px shell they rendered as a ~480px card glued
to the bottom edge, far from whatever control opened it (the operator's own
words: "strangely pressed against the bottom edge"). Operator decision (see
`docs/PROJECT_PLAN.md`'s Phase 4e section and `docs/WORKPACKAGES.md`'s D-13):
the game detail sheet becomes a CENTRED CARD at eye level; notifications and
the clients sheet become a right-edge DRAWER; mobile keeps its bottom
sheets, unchanged. `sheet-dialog.js`'s new `variant` option ("center" |
"drawer") appends a static modifier class at construction — presentation
only, `lib/modal-stack.js`'s push/pop/Escape stack is completely unaware of
it. New test file: `css-overlay-geometry.test.js` (structural CSS pins in
the same style `css-layout-foundation.test.js` established, plus one
fake-DOM behavioural pin for the nesting/Escape-ordering claim, using the
shared `fake-dom.js` harness). 524 baseline -> 539 green, first-round PASS.

**Opus review: FAIL, one blocker — found by the reviewer's own headless-
Chrome pass against a copy of the tree (screenshots at
1024/1184/1424/2544px), not by any structural pin in this file.** Every
structural claim held (13 mutations re-run and dying by name, the
`padding-left` centring measured exact — card centre == content-axis centre
to 0.0px at all four widths — presentation-only confirmed by grepping every
added line for listener/inert/focus vocabulary and finding none). **B1:**
the bulk-delete/GC-execute confirm dialogs (`.dialog-backdrop`, reused
verbatim by three call sites) still centred on the FULL viewport while the
sheet's own new centring axis moved to the content area — a live-measured
constant −90px (`--rail-w`/2) mismatch between a confirm and the card it
covers, the exact failure mode this package's own design comment had
already named for the sheet and then shipped anyway for the dialog. Fixed
with `padding-left:calc(var(--rail-w) + 22px)` on `.dialog-backdrop` at
BP-L (the `+ 22px` preserves the base rule's own uniform 22px inset, which
a bare `var(--rail-w)` would have replaced, landing 11px off); a new pin
computes both insets from the live `--rail-w` token and asserts they
resolve to the same axis rather than re-typing it as two literals that
could drift apart again — mutation-verified twice (reverting to the bare
`var(--rail-w)`, and deleting the override entirely both die by the same
named test). Suite: 539 -> 540 (one pin added).

Should-fixes, all addressed: **S1/S2** — this WP's divergence (D-13,
`docs/WORKPACKAGES.md`) and this README entry, previously missing (every
prior 4e web package shipped its divergence entry in the same commit).
**S3** — two first-pass test names claimed "byte-identical to the pre-WP
rule"; true of the shipped DIFF (purely additive, `git diff --stat` shows 0
deletions) but not of what those specific pins check — the reviewer added
an unrelated property to the base `.sheet-backdrop` rule and the suite
stayed green, since a regex-based pin asserts specific property values are
present, not that the rule contains nothing else. Renamed to "keeps every
pre-WP property value" (picked over switching to a literal rule-body
comparison, to stay consistent with `css-layout-foundation.test.js`'s
existing value-pin convention rather than adding a second, byte-snapshot
style only this file would use). **S4** — the mutation report had
mis-attributed a kill: reverting `variant:"center"` in
`game-detail-sheet.js`'s own `createSheetDialog(...)` call kills ONLY the
source-grep wiring test (section 5, "the three real overlay components
request the operator-decided variant"), never the fake-DOM nesting pin
(section 6) — that test builds its OWN `createSheetDialog({variant:
"center"})` instance directly and cannot see a regression in a different
file's call site at all. Corrected in both the coder's report and a new
comment on the nesting test stating this scope explicitly. **S6** — "slides
in from the right edge" appeared in `app.css`'s comments and two of the
three JS components; no transition/animation exists anywhere in this
codebase's overlays (verified: none of `.sheet`/`.sheet-backdrop`/
`.dialog`/`.dialog-backdrop` carry a `transition`/`animation` property, in
any state). Reworded to "appears at the right edge" — no motion added; this
package is geometry (position/shape), and a reduced-motion-guarded slide-in
transition would be a new, separately-scoped feature with its own testing
surface, not a fix to a wording bug. **N1/N2** (cheap, folded into the
existing drawer test rather than new ones): the drawer's now-full-height
top/bottom edges are flush against the viewport exactly like the (already
borderless) right edge — `border-top` dropped to match, `border-bottom`
left as the base rule's existing `none` rather than the fix round's own
first draft, which had mistakenly ADDED one; and with `.grab` hidden, the
drawer's `h2` sat 10px under the screen edge against the topbar's 14px —
matched via `.sheet--drawer .body{padding-top:14px}`.

**S5 — the operator's decision arrived in a second fix round: the detail
card widens to 680px at BP-L.** A DEDICATED token, `--w-sheet-l`
(`theme.css`), not a redefinition of the shared `--w-sheet` (which the
drawer, and every other sheet, still need at 480px) and not a literal
inside `.sheet--center` — `app.css`'s BP-L block needs both widths to
coexist at the same breakpoint, and WP 4h.3's header art is expected to
build against this same token. 680px, not the operator's own suggested
720px: chosen from the type measure of the card's own longest running
prose (the status-icon legend/depot-unknown captions, ~11.5px), which
yields ~102 characters/line at 680px against the classic 45-75-character
ideal — worse at 720/760px — so the LOW end of the operator's approved
680-760px range was the least-bad choice, not an arbitrary pick within it
(full character-per-line arithmetic in `theme.css`'s own token comment).
The plan item's "sizing and placement" is now genuinely delivered, not
merely placement; the confirm dialogs stay at their existing, deliberately
narrow `.dialog` width (420px) — "that is their job" (operator) — and the
drawer stays at `--w-sheet` (480px) for the same reason.

Three new pins for this decision (`css-overlay-geometry.test.js`):
`--w-sheet-l`'s existence and 680-760px range, `.sheet--center` sourcing
its `max-width` from `--w-sheet-l` rather than `--w-sheet`, and
`.sheet--drawer` gaining no such override at all. Mutation-tested in both
directions: deleting the token, and pointing `.sheet--center` back at
`--w-sheet`, each died by name.

Suite 543 green after both fix rounds (539 first pass + 1 B1's centring-
axis pin + 3 the width-token pair — N1/N2/S3/S4/S6 all extended or renamed
EXISTING assertions/comments rather than adding new test cases, matching
how WP 4e.2's own N5/N6 nitpicks were closed in place).

### WP 4e.4 — Pointer and keyboard interaction model (Phase 4e, D-14)

An inventory pass (every shipped view — library grid, game detail card,
notifications/clients drawers, downloads, settings, bulk bar, rail) found
the desktop shell (WP 4e.1-4e.3) still touch-first underneath: almost every
`:hover` rule was ungated (live at every width, every pointer type,
including touch — the sticky-hover risk `(hover:hover)`/`(pointer:fine)`
exists to prevent), three interactive controls sat flush against an
`overflow:hidden` ancestor that clipped the global `:focus-visible` ring,
the bulk bar kept its two buttons in the tab order while invisible, and
three surfaces hid their scrollbar with no other affordance for a mouse
user. Keyboard operability of the primary flows needed no new mechanism at
all — the grid card, the depot co-owner toggle and every delete/GC-execute
confirm were already fully wired (WP 4a.3/4a.4/4a.8) — but the composed,
NESTED sheet+confirm flow had no test of its own walking it end to end.
Full inventory and the roving-tabindex judgment call (explicitly NOT built,
and why, including its full measured cost) are recorded as D-14,
`docs/WORKPACKAGES.md`.

**Opus review round 1: FAIL — one blocker (B1), one must-fix (B2), plus a
D-14 sharpening (S1) and two cheap fixes (S2/S3), all fixed in round 2.**
B1: relocating all 11 pre-existing hover rules into one trailing block
(instead of gating each in place) inverted the cascade for five
equal-specificity state-vs-hover pairs, measured live in real Chrome —
`.iconbtn.on`, `.segs button[aria-pressed]`, `.chip[aria-pressed]`,
`.btn:disabled` (a disabled button visibly lit up under the cursor), and
`.notif.unread` each used to WIN on hover only by being written AFTER the
hover rule; moving every hover rule to the file's end flipped all five
ties. The round-1 test file's own header claimed a "moving a hover rule out
changes nothing else" pin existed — it did not; every hover assertion was
positional, never a cascade-OUTCOME check, so 22 passing tests coexisted
with all five regressions. Fixed by gating every hover rule in place, at
its exact original source position, plus six new named cascade-outcome
pins (source order for the five pairs, a higher-specificity override for
the `.nav-btn`/S2 sixth pair the review found in the ORIGINAL WP 4e.1
precedent). B2: `.bulk`'s opacity/pointer-events-only hide left its two
buttons reachable by Tab while invisible (measured: Tab from the last
library card landed on an invisible Cancel, then Download, then BODY) —
fixed with `visibility:hidden`/`.up{visibility:visible}`, composed with the
existing fade transition. S1: D-14 now states the Delete-selected button's
~590-Tab-press cost on the 394-game fixture, corrects the false "a skip
link exists" implication (`.skiplink` is onboarding's demo-mode button,
not a skip-to-content link, and appears nowhere on the library view), and
corrects stops-per-card to `>1` (measured 9 focusables for 6 demo-mode
games — action buttons are separate stops). S3: the scrollbar block's "no
layout shift" framing is corrected to scope that claim to the hover
tweaks only — an 8px scrollbar track is a real, if small, layout cost,
stated rather than hidden.

- `keyboard-pointer-model.test.js` — four structural CSS checks (same
  static-analysis posture as `css-hygiene.test.js`/`css-layout-
  foundation.test.js`, self-contained parsing rather than importing either
  file's helpers, matching this suite's existing per-file convention) plus
  a fake-DOM behavioural pin:
  1. **Hover gate.** Every `:hover` rule in `css/app.css`/`css/theme.css`
     must sit inside a media context requiring BOTH `(hover:hover)` and
     `(pointer:fine)` IN THE SAME and-chain — checked against the real tree
     (a `mediaStack` array per parsed rule, tracking enclosing `@media`
     headers) plus a synthetic mutation-proof fixture covering five shapes:
     a properly gated rule, a width-only-gated rule, a top-level rule, an
     `(any-hover: hover)` probe (a DIFFERENT, real media feature the round-1
     regex matched by bare substring — "any-**hover: hover**" — fixed by
     anchoring each feature check to its own opening parenthesis), and a
     comma-OR probe (`(hover:hover), (pointer:fine)` is EITHER-alone, not
     AND — the round-1 check ran two independent `.test()` calls against
     the whole header and would have been satisfied by either branch
     containing either substring; fixed by splitting the header on
     top-level commas first and requiring both features inside the SAME
     branch). Named pins for the pre-existing `.nav-btn:hover` precedent
     and for every new hover affordance this WP adds (`.hrow > button`,
     `.banner .acts button`, `.icnact`, `.card:hover .cap`, `.grid.list
     .card:hover`).
  2. **Cascade-outcome pins (the B1 fix, round 2's core addition).** For
     each of the five order-dependent pairs above, the STATE rule's source
     INDEX must be strictly greater than its hover rule's (the parser now
     records each rule's character offset for exactly this) — proven
     non-vacuous with a fixture asserting the check flags a
     deliberately-wrong-order pair and clears a correctly-ordered one. The
     sixth pair (`.nav-btn[aria-current="page"]` vs `.nav-btn:hover`)
     cannot use order at all — the BP-L breakpoint block the hover rule
     lives in structurally cannot move before the base rule — so it is
     checked by specificity arithmetic instead (a small selector-component
     counter) plus the override rule's own existence and `color` value.
  3. **Focus convention.** The base `:focus-visible` rule (theme.css) is a
     bare, universal selector using `var(--accent)`. A "real cross-section"
     pin (brief requirement, explicitly NOT one hand-picked example) checks
     ten interactive classes spanning every inventoried view
     (`.btn`/`.chip`/`.nav-btn`/`.card`/`.notif`/`.segs button`/
     `.depotwrap.sh .depot`/`.hrow > button`/`.iconbtn`/`.qx`) for any rule
     that suppresses `outline` entirely — none do. The one real,
     pre-existing exception (`.search input`/`.inp input`'s `outline:none`,
     substituted by a `:focus-within` border-color change on the wrapper,
     WP 4a.3/4a.6) is asserted as exactly that, by name, so it cannot be
     mistaken for something the cross-section pin should have caught.
     Named pins for the three new `outline-offset:-2px` overflow-clip
     fixes (`.segs button`, `.hrow > button`, `.depotwrap.sh .depot`), plus
     the B2 pin: `.bulk` is `visibility:hidden` while closed, `.bulk.up`
     restores `visibility:visible` — with its own mutation-proof fixture
     showing the pre-fix (opacity/pointer-events-only) shape does not
     satisfy the check.
  4. **Reduced motion.** The override is the universal wildcard `*,
     *::before, *::after` (not an enumerated list), so every hover-
     triggered transition this WP rides on (e.g. `.cap`'s pre-existing
     `transition:box-shadow`, now also triggered by the new `.card:hover
     .cap` rule, and `.bulk`'s new `visibility` transition) is covered by
     construction — pinned by asserting the wildcard itself, plus a
     synthetic fixture proving a NARROWED selector provably would not cover
     an arbitrary class the way the wildcard does.
  5. **Keyboard flow (fake-DOM).** Four tests walking the sheet +
     nested-confirm flow via `click` (standing in for native Enter/Space
     activation of a real `<button>` — a browser guarantee this harness
     does not re-test) and `keydown` `Escape`/`Tab` events only, using the
     shared `fake-dom.js` harness (`createFakeDom`/`fakeKeyEvent`/
     `fakeClickEvent`) already established by `dialog-wiring.test.js`/
     `modal-stack.test.js`. A confirm dialog is hand-built to the EXACT
     shape every real one in this codebase uses (`document.body`-level
     sibling, `pushModal`/`popModal`, safe-default focus on open, invoker
     focus restored on close) rather than importing `game-detail-sheet.js`
     directly — that module is a singleton DOM-building component this
     codebase deliberately does not unit-test (pulls in `store-
     singleton.js`/`api.js`, which need a real fetch-capable environment).
     Covers: the confirm receiving focus on its safe default (Keep) when
     opened on top of an already-open sheet; Escape unwinding INNER-first
     (first Escape closes only the confirm, restoring focus to the sheet's
     own Delete button; second Escape then closes the sheet, restoring
     focus to the original invoker); a Tab keydown never reaching the
     centralized Escape dispatcher at ANY stack depth (extends `modal-
     stack.test.js`'s single-overlay "Enter never triggers onEscape" check
     to the nested case, with the specific key — Tab — native focus order
     depends on staying uncaptured); and that closing the confirm via a
     real click on its own button restores focus identically to Escape.
     (Round 1's file also imported `router.js`'s `onViewChange` unused —
     dropped in round 2, a reviewer nitpick.)

  543 baseline → 565 green round 1 (undetected B1) → **574 green round 2**.
  Mutations applied to the REAL files and reverted, each dying by the exact
  name below:
  - Moving `.chip:hover` out of its in-place gate back to a top-level rule
    → **"real tree: every :hover rule in app.css/theme.css is gated behind
    (hover:hover) and (pointer:fine)"**.
  - Deleting `.hrow > button:focus-visible{ outline-offset:-2px }` →
    **"named pin: .hrow > button:focus-visible{ outline-offset:-2px }
    exists (overflow-clipped ancestor fix)"**.
  - Narrowing `theme.css`'s reduced-motion selector from `*, *::before,
    *::after` to `.btn, .chip` → **"the reduced-motion override is the
    universal wildcard *, *::before, *::after with !important on
    transition/animation duration"**.
  - Reverting `lib/modal-stack.js`'s `onEscapeKeydown` to call every
    stacked overlay's `onEscape` instead of only the topmost's (the exact
    historical WP 4a.8 bug) → **both** this WP's **"keyboard flow: Escape
    unwinds inner-first — the confirm closes before the sheet, restoring
    focus to the confirm's own invoker"** AND the pre-existing
    `modal-stack.test.js` pin **"Escape calls the topmost overlay's
    onEscape and nothing else"** — confirming the new, composed-level test
    and the old, primitive-level test agree on exactly what breaks.
  - **Round 2's B1 re-verification (the reviewer's explicit ask):**
    reconstructing the ORIGINAL bug shape — reversing source order for all
    five order-dependent pairs at once (`.iconbtn.on`/`.iconbtn:hover`,
    `.segs button[aria-pressed]`/`.segs button:hover`, `.chip[aria-pressed]`/
    `.chip:hover`, `.btn:disabled`/`.btn:hover`, `.notif.unread`/
    `.notif:hover`) → all five named cascade-outcome tests died
    SIMULTANEOUSLY, by name: **"cascade outcome: .iconbtn.on still wins
    over .iconbtn:hover on hover..."**, **"...segs button[aria-pressed]..."**,
    **"...chip[aria-pressed]..."**, **"...btn:disabled..."**,
    **"...notif.unread..."**.
  - **Round 2's B2 re-verification:** reverting `.bulk`/`.bulk.up` to the
    pre-fix opacity/pointer-events-only shape (no `visibility`) →
    **"B2: .bulk is visibility:hidden while closed and .bulk.up restores
    visibility:visible"**.
  - Every mutation above was reverted immediately after observing the named
    failure; suite re-confirmed at 574 green after each individual revert.

  Live-verified in the Browser pane against a static-served copy of `web/`,
  since neither a screenshot nor the polling store's first tick reliably
  completes in this pane's non-displayed/headless posture (`document.hidden`
  reads `true` there, parking every `store.js` loop by design — the same
  park-while-hidden behavior `store.js`'s own module header documents, not
  a bug). Round 1: `matchMedia('(hover:hover)')`/`(pointer:fine)` are `true`
  at a 1280×900 desktop viewport and `false`/`(pointer:coarse)` `true` at an
  emulated 375×812 touch viewport; a genuine CDP-level mouse hover (not a
  dispatched event) over the "Comfortable" segmented button painted
  `color:var(--text)` on its icon; a genuine CDP-level keyboard Tab (a
  JS-only `.focus()` call was confirmed NOT to trigger `:focus-visible` in
  this same browser, so the check specifically avoided that) onto the same
  button showed `outlineOffset: "-2px"` in its computed style. Round 2 (the
  reviewer's explicit re-measurement ask): six synthetic probe elements
  (carrying the exact class/attribute combination of each restored pair —
  `.iconbtn.on`, `.segs button[aria-pressed="true"]`,
  `.chip[aria-pressed="true"]`, a disabled `.btn`, `.notif.unread`, a
  `.nav-btn[aria-current="page"]`) were injected into the live page and
  hovered with genuine CDP mouse events; every one resolved to its STATE
  value under hover, not the plain hover value — `color:rgb(46,217,206)`
  (`--accent`) for the first three and the sixth, `filter:"none"` for the
  disabled button, and the accent gradient background for the unread
  notification. `.bulk`'s B2 fix was checked the same way: closed, its
  computed `visibility` is `"hidden"` and calling `.focus()` directly on its
  Cancel button does NOT move `document.activeElement`, confirming it is
  genuinely unreachable, not merely unpainted.

  No production JS changed — the inventory's keyboard-operability findings
  were all "already correct" (game-card.js, game-detail-sheet.js's depot
  toggle, lib/modal-stack.js's existing inert+Escape stack), so this
  package's only footprint is `css/app.css` (in-place hover gating, six
  cascade fixes, three focus-ring fixes, the B2 visibility fix, new hover/
  scrollbar affordances) and the one test file.

### WP 4e.5 — Downloads/Settings desktop layout (Phase 4e, D-15, last of the phase)

Both views "just stretched": a single, phone-width column of job cards/
queue rows/history rows (Downloads) or form fields (Settings), pulled
across whatever the shared `.view-root` BP-L cap happened to be —
`--w-wall` (960-2000px, the Library grid's own ceiling), never reviewed for
either view's own content shape. Full per-view inventory and the
multi-column/hybrid alternatives considered and rejected for each are
recorded as D-15, `docs/WORKPACKAGES.md`. Fix: one additive rule,
`.view-downloads, .view-settings{ max-width:var(--w-text); margin:0 auto;
}`, in `css/app.css`'s existing BP-L block, right after the pre-existing
`.view-root{ max-width:var(--w-wall); }` it composes with rather than
fights (two different elements — the child `<section>` and its parent
`<main>` — so no cascade tie exists to resolve). `--w-wall`, not `--w-text`,
stays on `.view-library`, unchanged, for its own auto-fill tile grid. No
new token: `--w-text` already existed (960px base, 760px from BP-M up) and
stays exactly that; `theme.css`'s own comment on it gained a short
addendum documenting the new BP-L consumer, and the pre-existing BP-XL
comment's "Settings/Downloads are --w-text-based content" claim — true in
prose since WP 4e.2, false in the actual shipped rule until this package —
was corrected in place rather than left stale.

- `css-downloads-settings-layout.test.js` (new, +13) — same structural,
  no-jsdom posture as `css-layout-foundation.test.js`/`css-overlay-
  geometry.test.js`, self-contained parsing utilities per this suite's
  per-file convention:
  1. **The layout itself.** The BP-L block contains the
     `.view-downloads, .view-settings` rule, referencing `var(--w-text)`
     (never a bare px literal) plus `margin:0 auto`; `.view-library` is
     confirmed excluded from the same selector and confirmed to keep its
     own, independent BP-L rule untouched.
  2. **Flatness.** `--w-text` has exactly two assignments anywhere in
     `theme.css`+`app.css` (the `:root` base, 960px, and BP-M, 760px) — a
     mutation-protection against a future BP-L/BP-XL redefinition silently
     widening Downloads'/Settings' own cap without anyone touching this
     WP's rule at all. BP-XL is asserted to touch neither `--w-text` nor
     either view's selector.
  3. **No cascade tie.** `.view-downloads` is referenced exactly once in
     `app.css` (nothing else touches it, so nothing can tie with this
     rule); `.view-settings` is referenced exactly twice — this WP's own
     rule plus the one pre-existing, unrelated WP 4a.6
     `h4.sec:first-of-type` descendant rule, which is checked by literal
     text to confirm it styles `h4.sec`'s own margin, never `max-width`/
     `margin` on the bare `.view-settings` class itself.
  4. **Mobile/base untouched.** Six "keeps every pre-WP property value"
     pins (same naming convention `css-overlay-geometry.test.js`
     established) across the actual inventory: `.dl-head`/`.dl-sub`,
     `.jobcard`/`.jobtop`, `.qrow`, `.hrow`/`.hrow > button`, `.field`/
     `.srow`. One final pin confirms the capped-column selector text
     itself appears nowhere outside `@media` (the pre-existing, unrelated
     top-level `.view-settings h4.sec:first-of-type` rule is the reason
     this check is scoped to the exact selector string, not "no mention of
     either class name at all", which would have false-positived on it).

  574 baseline → 587 green, first pass. Every pin's mutation target was
  applied to the real files and confirmed dying by name, then reverted
  (suite reconfirmed 587 green after each individual revert):
  - Deleting the `.view-downloads, .view-settings{...}` block entirely →
    **4 tests died simultaneously**: "BP-L (min-width:1024px) exists and
    gives .view-downloads/.view-settings their own capped, centred
    column", "`.view-library` is deliberately excluded from the
    capped-column rule...", and both no-cascade-tie tests (with the rule
    gone, `.view-downloads`/`.view-settings` drop to 0/1 references
    respectively, not the expected 1/2).
  - Changing `max-width:var(--w-text)` to a hardcoded `max-width:760px` →
    **"BP-L (min-width:1024px) exists and gives .view-downloads/
    .view-settings their own capped, centred column"** (the `/max-width:\s*
    var\(--w-text\)/` assertion, specifically — the rule otherwise still
    "exists").
  - Adding `--w-text:900px;` inside the BP-L block's own `:root{...}` (the
    exact drift the flatness pin exists to catch) →
    **"--w-text has exactly two assignments anywhere (theme.css :root
    base, BP-M) — never redeclared at BP-L or BP-XL"** (`3 !== 2` in the
    actual failure output).
  - Changing `.qrow`'s pre-existing `border-radius:var(--r-m)` to
    `var(--r-s)` → **"base .qrow keeps every pre-WP property value (the
    Queue row shape)"** — confirming this pin is a real value check, not a
    vacuous "rule exists" one.

  No production JS changed: a plain container-width cap needed no DOM
  restructuring in either view, so `web/js/views/downloads.js`/
  `web/js/views/settings.js` are untouched, and neither view gained a test
  file of its own beyond the shared CSS pins above (same "DOM-building
  views are not unit-tested directly" posture every prior Phase-4e/WP-4a
  package in this file already documents).

### WP 4h.2 — Suggestions panel (Phase 4h)

Plan-vs-brief conflict resolved before any code was written (recorded as
D-16, `docs/WORKPACKAGES.md`): `docs/PROJECT_PLAN.md`:1867-1869 requires
BOTH a right-hand column at BP-XL (>=1800px) AND a collapsible card below
that width, ONE component with two CSS presentations — the brief had
narrowed this to "BP-XL only" from memory, with a mutation pin that would
have killed the plan's own second presentation. Same "pull the decision
logic into `lib/`, keep the DOM-building file untested directly" posture as
every prior WP, extended (like WP 4e.6's `rail-panel.js`) with a
dependency-injected wiring component specifically so its localStorage/
visibility glue is provable headlessly too.

- `decision-support.test.js` (new, 25) — `web/js/lib/decision-support.js`'s
  pure statement-selection logic, no DOM: the four accepted statement
  families' exact wording and gating (`PLAYABLE_NOW` requires a REAL,
  explicit zero playtime — never a fabricated one for absent/negative/non-
  finite input — AND real cached bytes; `STALE_CONFIRMATION`'s 30-day
  threshold boundary, including a clock-skewed future timestamp never
  producing a negative-day claim; `CHANGED_RECENTLY`/`STABLE` reading WP
  4h.1's `manifest_change_frequency` correctly, with `"insufficient_data"`
  and `null` BOTH producing no statement, never conflated with `"stable"`);
  one game qualifying for two families returns only the highest-priority
  one; `buildSuggestions`' ranking (PLAYABLE_NOW > STALE_CONFIRMATION >
  CHANGED_RECENTLY > STABLE, ties broken by ascending appid), `limit`, and
  the three-way tier ladder (`"full"` whenever a PLAYABLE_NOW item is
  present, `"frequency"` when items exist without one, `"insufficient_data"`
  — the honest empty-panel state — when nothing qualifies at all, including
  for a non-array/undefined snapshot). **The negative privacy pin** (the
  plan's binding "no number held up to someone else in the living room"
  stance, structurally enforced): a broad sweep of non-zero playtime values
  never triggers `PLAYABLE_NOW` and never lets the raw number leak into any
  statement text; `rtime_last_played` is never read into a statement however
  it is spelled on the input object; every statement family, across a wide
  input sweep, is checked against a judgemental-pattern regex list
  (`haven't played`, `never played`, a played-time NUMBER, `last played`) and
  none ever matches.
- `decision-panel-wiring.test.js` (new, 17) —
  `web/js/components/decision-panel.js`'s DOM wiring, same `fake-dom.js` +
  dependency-injection posture as `rail-panel-wiring.test.js` (a
  `createDecisionPanel({elements, store, onViewChange, getCurrentView,
  storage})` factory takes every real dependency, including a fake
  `storage` object standing in for `window.localStorage`). Also pins that
  the collapse button's `aria-label` flips between "Expand suggestions"/
  "Collapse suggestions" with state, not just `aria-expanded` — a
  screen-reader user needs the ACTION the button performs, not only whether
  it is currently expanded. Covers:
  Library-view-only visibility (hidden on every other view, and hidden with
  zero games at all — "nothing to react to yet"); the `insufficient_data`
  empty state rendering an honest message rather than hiding the panel or
  showing an empty box, once at least one game exists; dismiss (persists
  `steamvault.decisionPanelDismissed=1`, hides both presentations, and — the
  precedent pin, `bypass-banner.test.js`'s "dismissed stays dismissed across
  an unrelated poll tick", adapted from that in-memory banner's auto-clear
  model to this module's read-once-into-state localStorage model) survives
  an unrelated `"games"` poll tick without being resurrected; collapse
  (defaults to collapsed on a first visit, toggles its OWN
  `steamvault.decisionPanelCollapsed` key, independently of dismiss — a
  named pin constructs the `collapsed:false, dismissed:true` combination and
  confirms dismiss still wins for visibility without the two keys aliasing);
  `#app`'s `has-decision-panel` class tracks visibility exactly (required so
  `css/app.css`'s BP-XL grid never reserves a permanent empty third column
  for a dismissed/off-Library panel); a failed `{error}` games tick leaves
  the last real render untouched; a throwing `storage.setItem` never blocks
  the in-memory dismiss action (same posture as `api.js`'s localStorage
  helpers).
- `decision-panel-layout.test.js` (new, 11) — same structural, no-jsdom
  posture as `css-layout-foundation.test.js`, pinning the plan's two-
  presentation requirement itself: exactly one `<aside id="decision-panel">`
  in `index.html`, `[hidden]` by default, positioned AFTER `<main
  id="view-root">` (the D-14 constraint this WP inherits — sitting after the
  Library view's entire flow trivially satisfies "no new focusable content
  between the filter row and the first card", since this is later than all
  of it). Two named **mutation targets**, one per presentation: `.decision-
  panel{ grid-area:panel }` and the `.app.has-decision-panel` 3-column
  template both found ONLY inside the bare `min-width:1800px` block (never
  at top level, never in any other breakpoint block — the "render the
  column below BP-XL" mutation); `.dp-collapse` (the card-only collapse
  toggle) is `display:none` ONLY inside that same block, proving it — and
  therefore the card itself — is a real, working, non-hidden presentation
  below BP-XL (the "render the card at BP-XL"/"remove the card entirely"
  mutations). A sanity pin confirms `.decision-panel` carries no author
  `display:` rule anywhere at all (top level or any media block) — by
  `css-hygiene.test.js`'s own documented rule (the `.rail-version` sanity
  pin), this means no `.decision-panel[hidden]` guard is required, since the
  UA's built-in `[hidden]{display:none}` never fights an author rule here
  (verified: `css-hygiene.test.js`'s own suite, unedited, still passes with
  zero new guard demands). A final pin confirms the panel's BP-XL rules live
  in the SAME bare block `css-layout-foundation.test.js`'s `--w-wall` pin
  already anchors to, not a second, independently-added block — the
  structural precondition for "the panel takes width from the wall" being
  something a reader can actually verify by inspection.
- `demo-data-relay-privacy.test.js` (new, 10) and one **named baseline-test
  edit** to `demo-data-settings.test.js` — two of the three carried-over
  defects from the WP 4h.0 review (both prior WPs landed `api/`-only,
  leaving `web/js/demo-data.js` diverged — see that module's own comments):
  (1) `DEMO_OWNED_GAMES`'s DEFAULT shape now omits `playtime_forever`/
  `rtime_last_played` entirely (both ADR-0010 keys ship off by default) —
  the previous fixture carried `playtime_forever` on every entry
  unconditionally, the shape of a NON-default gate state masquerading as the
  baseline. The enabled-gate shape is a separate, explicit fixture
  (`DEMO_OWNED_GAMES_PLAYTIME`) reached only via `resetDemoData({
  relayExposePlaytime, relayExposeLastPlayed })`, demo mode's analogue of
  "set the env var and restart" (there is no PATCH path, in demo mode or the
  real one) — including a deliberate case (appid 3300100) where playtime is
  exposed but no last-played value exists for it at all, proving the gate
  never fabricates one just because the SETTING is on. `demo-data-settings
  .test.js`'s pre-existing "GET /v1/steam/owned-games answers 200..." test
  is the one allowed edit: it used to assert `"playtime_forever" in g` for
  every game (the wrong, non-default shape baked into a baseline test); it
  now asserts the DEFAULT shape (both keys absent) instead, named and
  justified in its own comment per docs/LEARNINGS.md's "demo fixtures are a
  shipped surface, shapes 1:1 with the real API" rule. (2) `ENV_ONLY_DEMO`
  gains `relay_expose_playtime`/`relay_expose_last_played` as two more
  informational rows (read via a `get value()` getter off the same mutable
  state `resetDemoData()` sets, since — unlike every other env-only row here
  — these two have a demo-reachable "restart" analogue), and `PATCH` on
  either now answers the byte-identical "environment-only" 422 detail
  string `api/vault_api/routers/settings.py`'s `_ENV_ONLY_DETAIL_TEMPLATE`
  uses (cross-checked against that file, not guessed) instead of falling
  through to "unrecognised setting key". Fixing the ONE template (it was
  wrong for all nine env-only keys, not just these two) is pinned to also
  correct the pre-existing seven's message, verified by name for `db_path`.
- `demo-data-change-frequency.test.js` (new, 7) — a fourth fixture
  correction, the coder's own addition (not one of the brief's three named
  defects, same drift class): `GET /v1/games`/`GET /v1/games/{appid}` never
  projected WP 4h.1's `manifest_change_frequency`/`manifest_observation_
  days`/`manifest_days_since_last_change` fields at all. Now projected from
  the seed object on both routes, with three curated seed games (Aurora
  Cascade: `"stable"`; Driftwood Signal: `"changed"`; Frostline Convoy:
  `"insufficient_data"`, deliberately distinct from `null` per WP 4h.1's own
  pin 2) demonstrating all three real states plus the `null` default the
  rest keep — an end-to-end sanity test confirms `buildSuggestions()` finds
  a real, non-`"insufficient_data"`-tier suggestion from this fixture, not
  just that the fields are present.

**Not wired in this package, stated rather than silently implied
(`decision-panel.js`'s own module header carries the same note):** the
panel's "full" (playtime-inclusive) tier is real, tested code in
`lib/decision-support.js`, but nothing in `web/js/` currently supplies a
`playtimeByAppid` map to it in production — there is no persisted Steam
identity anywhere in this codebase (`onboarding.js`/`views/settings.js`'s
"Library preview" lookup is a deliberate one-off, never stored) to poll
`GET /v1/steam/owned-games` against on a recurring basis. The panel
therefore always operates in the `"frequency"`/`"insufficient_data"` tier in
the shipped product today; wiring a persisted identity + its own poll
loop/cadence decision is real, separate, unscoped work for a future package,
not silently promised here.

587 baseline (after WP 4e.5's fast-forward onto `origin/main`) → 657 green,
25+17+11+10+7 = 70 new tests, first pass, one existing-test edit (named
above), no test file other than that one edit touched.

**Opus review round 1: FAIL — one blocker (B1), five should-fixes (S1-S5),
all fixed and re-verified in this round. 665 green (78 new: 28+19+14+10+7).**

- **B1 (blocker, measured live in headless Chrome at 1280px, both bypass-
  banner states).** `.decision-panel` is a fifth in-flow child of `#app`
  with no explicit `grid-area` before this fix — CSS grid auto-placement
  dropped it into whichever cell it found first vacant once `#app` becomes
  a grid at BP-L: the empty `"banner"` cell (banner hidden), rendering it
  ABOVE the library grid while still DOM-last — the exact visual-vs-
  reading-order divergence D-14 exists to prevent; or an implicit row
  inside the RAIL's own 148px column (banner visible). Both measured live
  and reproduced BEFORE the fix, confirmed gone AFTER it, and reproduced
  AGAIN under the revert-as-mutation the review asked for (both banner
  states, each time) — see `decision-panel-layout.test.js`'s two new "B1"
  mutation pins for the structural half (a fourth `"rail panel"` row in
  BP-L's `.app` template; an explicit, unconditional
  `.decision-panel{ grid-area:panel }` there) and the coder's report for
  the live bounding-rect numbers (before/after/mutated, both states) this
  file cannot itself check (no jsdom in this suite). Fixing this also
  surfaced a SECOND, independent bug live: `margin:20px auto 32px` alone
  does not centre a grid item that also needs to STRETCH-then-cap — per
  the CSS box-alignment spec, an `auto` margin on a grid item overrides its
  default `justify-self:stretch`, so the box shrank to its own content
  width (188px) instead of filling toward `max-width:760px` before centring
  it. Fixed with an explicit `width:100%` (the same property
  `.view-root`/`.banner-wrap` already carry for the identical reason,
  discovered only by re-measuring live, not by reasoning about the
  declaration).
- **S1** — `theme.css`'s `--panel-w` comment claimed no visible tile-column
  shrink at 1920-2560px; measured live, with vs. without the panel present
  at the same viewport, on a SCROLLING page (`.grid`'s own resolved
  `grid-template-columns` track count — this panel only ever renders next
  to a library with enough games to scroll, `css-layout-foundation
  .test.js`'s own `SCROLLBAR_PX` convention): 1800px 8→6 (Δ2), 1920px 9→7
  (Δ2), 2200px **10→8 (Δ2)**, 2300px 10→9 (Δ1), 2395px 10→10 (Δ0, the real
  crossover — 2394px is still 9), 2560px 10→10 (Δ0). **Round 3 settled a
  genuine round-2 disagreement — both rigs had measured correctly, just
  two different quantities.** The decisive number at 2200px is **1673px**:
  `.grid`'s CONTENT-box width on the shipped, scrolling-page condition,
  below even the 9-track floor (1680px) — not the 1720px the coder
  reported in round 2, which was real but answered a different question
  (`.view-root`'s BORDER-box width on a page with NO scrollbar). Chain:
  `2200 - 180(--rail-w) - 300(--panel-w) = 1720px` (`.view-root`'s outer
  box) `- 15px` (the classic scrollbar, present once the page actually
  scrolls) `- 32px` (`.view-root`'s own left+right padding) `= 1673px`.
  Floors (`--tile-min:176px`, 12px gap): 8 tracks need >=1492px, 9 need
  >=1680px, 10 need >=1868px. The two-tile-column cost at 1800-2200px (not
  just 1800-1920px) is accepted and stated, not engineered away.
- **S6** — the B1/S2 fix round's `width:100%` on the TOP-LEVEL
  `.decision-panel` rule (needed there because an `auto` margin on a grid
  item overrides `justify-self:stretch`, forcing content-sized shrink
  instead — see S2 above) made the BP-XL-scoped rule's OWN
  `margin-right:16px` inert once it won the cascade: `width:100%` fills
  the entire track regardless of margin, so the card sat flush against the
  track's right edge at every BP-XL width — measured, `panel.right ===
  clientWidth` at 1800/1920/2200/2560px. Fixed with `width:auto` inside
  `.app.has-decision-panel .decision-panel` specifically — that rule's own
  margin has no `auto` component, so it needs the OTHER width algorithm
  (stretch-minus-margin), not the "fill regardless of margin" one the
  auto-margin rule needed. Re-verified live at 1900px AND 2200px after the
  fix: a real 16px gutter (`panel.right = clientWidth - 16`), the card's
  own BORDER box **284px** wide (`--panel-w:300px` is the TRACK width; 284
  + 16px margin = the full 300px track, not the card itself). This one,
  too, was invisible on the first live check after the S2 fix because of a
  stale, un-busted stylesheet `<link>` in the measurement rig — a
  forced-fresh reload was what actually exposed it. (The pin for this fix
  lives inside the test named `"MUTATION PIN (S3): the BP-XL column-only
  rules are scoped under '.app.has-decision-panel'..."` in
  `decision-panel-layout.test.js` — the assertion is correct, the test's
  own name predates S6 and does not mention it.)
- **S2** — `.decision-panel`'s margin used a fixed `var(--gutter)` left
  inset instead of centring; measured live, a 108px stair-step against
  `.view-root`'s own centred edge at 1023px. Fixed (`margin:20px auto
  32px`, plus the `width:100%` B1's fix round also needed — see above);
  `decision-panel-layout.test.js` gained a named pin.
- **S3** — `buildSuggestions()`'s `tier` had zero production callers, and
  the BP-XL column reserved `--panel-w` even for an `"insufficient_data"`
  result (a fresh vault showing one static sentence in a 300px sidebar for
  ~14 days). `decision-panel.js` now keys `#app`'s `has-decision-panel`
  class on `tier !== "insufficient_data"` (wiring the dead caller and
  fixing the cost in the same change) — the BP-XL block's three
  column-only rules are now scoped under `.app.has-decision-panel` rather
  than bare selectors, so an empty result falls through to the BP-L row
  placement (with a working collapse toggle) instead of keeping the
  column's "no collapse, force-expanded" treatment with no column behind
  it. New pins in both `decision-panel-layout.test.js` (the scoping) and
  `decision-panel-wiring.test.js` (the JS predicate, including a tick that
  flips a result from empty to real without re-navigating).
- **S4** — `manifest_days_since_last_change`/`manifest_observation_days`
  had no numeric sanitation beyond `typeof === "number"` — true for `NaN`/
  negatives/fractions/`Infinity` too; probed and confirmed rendering
  (`"Last changed NaN days ago."`, `-5`, `3.7`, `Infinity`) before this fix.
  `lib/decision-support.js` gained `realNonNegativeIntOrNull` (mirrors
  `realPlaytimeForeverOrNull`'s contract; `Number.isSafeInteger`, not
  merely `Number.isInteger`, additionally rejects a pathologically large
  integral value like `1e+21`, also probed) — two new mutation pins in
  `decision-support.test.js` sweep both fields against the full bad-value
  set, plus a pin confirming a real `0` still renders (never falsy-
  rejected).
- **S5** — `docs/WORKPACKAGES.md`'s D-16 extended: the discoverability-vs-
  keyboard-cost trade-off (the panel's two buttons are the last two tab
  stops in the whole app, after D-14's ~590) named explicitly, and the
  rejected alternative (DOM-early/visually-last via `order`) named as
  rejected specifically because it IS the B1 divergence, produced on
  purpose instead of by accident.

Three shipped comments claiming "a normal-flow card, last child of #app,
after `<main>`" (app.css, index.html, and this file's own header comment)
were corrected in the same round — DOM order and visual order are related
but not identical claims, and the fix round is what actually makes the
weaker, correct claim ("DOM order; CSS decides visual order") true.

### WP 4h.3 — Header art in the detail card (Phase 4h, last of the phase)

"Nearly free" per the plan: a wide hero image at the top of the game detail
card, built from `lib/cover-art.js`'s existing CDN-host discipline (a new
export, `headerArtUrl(appid)`, pointing at Steam's `header.jpg` instead of
the grid's `library_600x900.jpg` — same host, no CSP change). New DOM-
building module `web/js/components/header-art.js`; `game-detail-sheet.js`
calls its one export, `buildHeaderArt(appid)`, once per full `render()`,
appended first (above `.dhead`).

Unlike every prior DOM-building component in this app (`sheet-dialog.js`,
`clients-sheet.js`, `notifications.js`, `game-detail-sheet.js` itself —
all "not unit-tested directly"), `header-art.js` gets ONE named exception,
the same shape `dialog-wiring.test.js` already carves out of that general
rule for `sheet-dialog.js`'s modal-stack wiring: the brief demanded a
mutation-tested pin for graceful absence, so this module is small and
side-effect-free enough (no module-level DOM/store/api touches, only lazy
`document.createElement` calls inside `buildHeaderArt()`) to import
directly under the existing `fake-dom.js` harness.

**Absence/loading resolution, pinned exactly as designed:** `.header-art`
(`css/app.css`) reserves its box via `aspect-ratio:460/215` (Steam's own
header.jpg ratio) ONLY — no `height`/`min-height` anywhere on it. That box
exists from the instant the wrapper is inserted through a successful load
(the image paints INTO an already-sized box, so a slow load never shifts
anything below it), and disappears completely, in one step, the moment
`buildHeaderArt`'s `error` handler fires — critically, `wrap.remove()`,
removing the WHOLE wrapper, not `img.remove()` alone (contrast
`game-card.js`'s `buildCover()`, where removing only the `<img>` is correct
because a procedural fallback tile stays underneath; this hero has no such
tile, so removing only the image would leave the aspect-ratio band as a
bare, styled-nothing rectangle — exactly the "reserved empty band" the
brief forbids). Net effect stated plainly: a 404 title shows no header art
at all, with the rest of the card's content simply one sibling higher, and
the one visible move is whenever that error arrives (typically fast); a
slow-loading title never moves anything, ever.

**Superseded by WP 4h.5, below** — the operator watched this exact
reserve-then-shrink shape collapse visibly on a phone and asked for
"default off, and load it in if there is one" instead. This paragraph is
left as the historical record of what WP 4h.3 shipped; see the WP 4h.5
section for the design that replaced it.

- `header-art.test.js` (new, 8) — three behavioural pins against
  `components/header-art.js` (via `fake-dom.js`): the `<img>` src is
  `headerArtUrl(appid)`, proven to vary with the appid given (a hardcoded
  or appid-440-only URL fails the cross-appid `notEqual`); an `error` event
  on the `<img>` removes the entire wrapper from its parent, not just the
  `<img>` (a same-shaped mutation that only removes the `<img>` — the
  `game-card.js` pattern applied here by mistake — is caught by name); the
  `<img>` carries an empty `alt` (decorative, same as the grid's cover/mini-
  cover). One source-grep pin proves `game-detail-sheet.js` actually
  imports and calls `buildHeaderArt(state.appid)` inside `contentEl.append(
  ...)` (the WP 4a.1/4a.3 "documented mechanism, zero real callers" failure
  class). Four structural CSS pins: `.header-art`'s `aspect-ratio:460/215`
  exists; it carries neither `height` nor `min-height` (the mutation that
  would silently reintroduce a permanent band); neither `.header-art` nor
  `.header-art img` declares any `transition`/`animation` (standing "no
  motion on overlays" rule, D-13 — an image that finishes loading just
  appears); `.header-art img` is `display:block` with `object-fit:cover`.
  All four DOM-behaviour mutations above (hardcoded URL, `img.remove()`
  instead of `wrap.remove()`, dropped `aspect-ratio`, dropped call site) and
  the `min-height` reintroduction were applied by hand, run, confirmed to
  kill exactly the named test with the exact quoted assertion failure, and
  reverted (coder's report has the transcripts).
- `cover-art.test.js` gains three tests for the new `headerArtUrl` export:
  the exact CSP host + `header.jpg` path (mirrors the existing
  `coverArtUrl` pin 1:1); that it varies with the appid (same cross-appid
  `notEqual` shape as the behavioural pin above, at the pure-function
  level); and that `headerArtUrl`/`coverArtUrl` share the CDN host but
  never the asset path, for the same appid.
- `fake-dom.js`'s `FakeElement` gains one new method, `remove()` (real
  DOM's `Element.remove()`, detaching from `parentNode.children`) — the
  shim's own stated policy ("grows just far enough for each new consumer's
  actual DOM-API surface", WP 4e.6) — needed because `header-art.js` is the
  first module this harness drives whose error path removes an element via
  `.remove()` rather than a `classList`/`hidden` toggle.

**Not covered by any of the above, by design (matches every prior sibling
component's posture):** the DOM shape `buildHeaderArt` builds around the
`<img>` (the wrapper's class, the fact that it is a `<div>`) is exercised
only indirectly, through the behavioural pins' own traversal of
`wrap.children`; `game-detail-sheet.js`'s own render-scheduling decision
(header art rebuilt on every full `render()`, never patched by
`patchVolatile()`) is documented in that module's header comment but has
no dedicated test of its own, the same "not unit-tested directly" posture
every other DOM-building piece of that file already carries — a poll-tick
re-fetch of the SAME URL is a browser-cache concern, not a correctness one,
and is called out as such in the coder's report rather than pinned here.
Verified live in a running instance (see the coder's report) rather than
here: the actual visual result (a real Steam title's banner rendering
inside the reserved box at BP-L's 680px card width, and a fabricated/
unknown appid collapsing cleanly with no broken-image icon).

### WP 4h.5 — header art: load-then-reveal, no reserved band (polish of WP 4h.3)

Revises the "Absence/loading resolution" design WP 4h.3 shipped (above):
reserve-then-shrink (aspect-ratio held open from insertion, wrapper removed
outright on a 404) is replaced with load-then-reveal — nothing about
`.header-art` occupies layout until its `<img>` has actually loaded, so a
404/delisted title now reserves and reveals NOTHING (no band ever appears,
so there is nothing to collapse), and a real image grows into its true
460:215 box once it is known-good.

**CSS mechanism (`css/app.css`):** `.header-art` is a one-row/one-column
grid starting at `grid-template-rows:0fr` with `opacity:0` and
`margin-bottom:0` — genuinely zero height, not a 1px seam, because
`overflow:hidden` on the wrapper plus `min-height:0` on the `<img>`
together let the track actually collapse instead of being floored by the
image's own aspect-ratio-derived minimum. `components/header-art.js` adding
the `.loaded` class flips the row to `1fr`, opacity to `1`, and
margin-bottom to `12px`, animating open over `.2s` — the modern
`grid-template-rows: 0fr -> 1fr` accordion technique, chosen because
`height:auto` is not animatable and this keeps the expanded size exactly
the `<img>`'s own true `aspect-ratio:460/215` box (now declared on the
`<img>` itself, not the wrapper) rather than a second, independently-tuned
number. The transition carries no `!important`, so the standing whole-app
`prefers-reduced-motion` wildcard (`theme.css`, pinned in
`keyboard-pointer-model.test.js`) already forces it instant for users who
ask for less motion — no separate reduced-motion rule needed here.

**JS mechanism (`components/header-art.js`):** `img.decode()` is preferred
over the bare `load` event as the reveal trigger where available — it
settles only once the image is actually ready to paint (fully loaded AND
decoded), avoiding a reveal on a half-decoded first frame for a slow/large
image; its rejection (which also fires on a genuine load failure) is
swallowed on purpose, since the `error` listener already owns that path.
Engines without `img.decode` — and this codebase's `fake-dom.js` harness,
whose `FakeElement` has no `decode` method — fall back to the `load` event,
which still reveals correctly, just without the paint-readiness guarantee.
`src` is assigned before `decode()` is called (decode() operates on the
element's "current request," which only exists once a src is set — calling
it first would reject immediately with nothing to decode). The one visible
movement in the whole flow — the grow-in — is only ever seen when the image
genuinely arrives after the wrapper has already been painted at zero
height (a cold cache or a slow CDN round trip); when decode/load resolves
before the browser's next paint (the common case, e.g. a warm cache), the
zero-height frame is never painted at all, so the header simply appears
already-expanded with no visible motion.

- `header-art.test.js` (12, +4 from WP 4h.3's 8) — the three original
  behavioural pins (URL via `headerArtUrl`, varying with appid; an `error`
  event removes the whole wrapper; decorative empty `alt`) plus the
  source-grep call-site pin, all unchanged and still green (the call-site
  invariant `game-detail-sheet.js`'s header names — `contentEl.
  replaceChildren()` first on every full render, `patchVolatile()` never
  touching `.header-art` — was preserved by construction: the wrapper is
  still appended synchronously in the same place, only its CSS starting
  state changed). Two new behavioural pins: the wrapper carries no
  `.loaded` class synchronously right after `buildHeaderArt` returns
  (nothing is revealed before the image is known-good); a `load` event on
  the `<img>` (the fallback path this harness exercises, since
  `fake-dom.js` has no `decode()`) adds `.loaded` to the wrapper. Six CSS
  pins, replacing WP 4h.3's four: `.header-art`'s base rule has no
  `aspect-ratio`/`height`/`min-height` at all (the mutation that would
  silently reintroduce WP 4h.3's premature reservation); its row starts at
  `grid-template-rows:0fr`; `.header-art.loaded` reveals via
  `grid-template-rows:1fr` and `opacity:1`; `.header-art img` carries the
  real `aspect-ratio:460/215` (moved from the wrapper); `.header-art`
  declares a `transition` with no `!important` (so the reduced-motion
  wildcard governs it unconditionally); `.header-art img` is `display:block`
  with `object-fit:cover` and `min-height:0`. Every mutation this WP's
  pins target (reserve `aspect-ratio` on the base rule, wrong ratio on the
  `<img>`, `!important` on the transition, dropped error handler, dropped
  reveal listener, hardcoded URL) was applied by hand, run, confirmed to
  kill exactly the named test with the exact quoted assertion failure shown
  in the coder's report, and reverted.
- `docs/WORKPACKAGES.md` D-17 is amended in place (not a new entry) to
  record the supersession and drop WP 4h.3's phone-width band concern,
  which no longer applies once nothing is reserved.

**Not covered, by design:** the actual browser-rendered timing of "does the
grow-in visibly animate or not" (a cold-cache vs. warm-cache race,
inherently environment-dependent, same class of claim
`docs/LEARNINGS.md`'s nginx event-log timing entry already names as
real-but-non-deterministic) is not asserted here — this file pins the CSS
mechanism and the JS reveal trigger structurally, not a live paint timing.

### WP 4d-web — Sweep visibility and control (Phase 4d)

Closes the "Phase 4a UI switch remains open" gap `docs/PROJECT_PLAN.md`'s
Phase 4d entry names: `sweep_include_cached` becomes a real Settings toggle,
and `GET /v1/schedule`'s `last_sweep_targets`/`sweep_cached_gc_risk` fields
get their first UI consumer. Same posture as every prior Settings-adjacent
WP: `web/js/views/settings.js` (the DOM-building view) is not unit-tested
directly — the decision logic it leans on lives in a pure `web/js/lib/`
module and is tested here.

- `schedule-presentation.test.js` — `web/js/lib/schedule-presentation.js`:
  `sweepTargetsMessage`'s THREE distinct states — review round 1 (Opus)
  FAIL, blocker B1: the first version had only two, collapsing "never run"
  (`last_sweep_at` also null) together with "a sweep started but has not
  recorded a result" (`last_sweep_at` stamped, both counters still null —
  `api/vault_api/scheduler.py::claim_sweep` stamps the timestamp and NULLs
  both counters in ONE statement; `finish_sweep` fills them in only once
  the sweep actually completes, so a crash mid-sweep leaves this state
  PERMANENTLY). The fix branches on `last_sweep_at` and states both
  remaining possibilities (still running / the process stopped before
  finishing) rather than picking one; `0` targets ("ran, found nothing,"
  offering possibilities to check rather than naming a cause — a sibling
  package is about to make any single hardcoded cause wrong) and `>0`
  targets (count + enqueued, singular/plural correct) are the other two.
  Named mutation targets, each reverted-and-reconfirmed: a falsy check
  (`!targets`) in place of the explicit `null`/`undefined` check collapses
  `0` back into "never run" (kills 3 tests, including the one literally
  named `MUTATION PIN`); `cachedSweepGcRiskWarning`'s strict
  `sweep_cached_gc_risk !== true` loosened to a truthy check lets a stray
  `1`/`"true"` show the warning (kills its own named `MUTATION PIN`).
  Review round 1 blocker B2: `cachedSweepGcRiskWarning`'s first version
  asserted present-tense ACTIVITY ("cached games ARE BEING refreshed...")
  from a field that is a pure CONFIGURATION predicate
  (`sweep_include_cached and not auto_gc_executes`, unconditional on
  whether the scheduler is even enabled) — reworded to "is set to"/"would"
  throughout, matching how `scheduler.py`'s own log line for the identical
  condition is worded, never restated as present-tense fact.
- `demo-data-schedule.test.js` extends `demo-data.js`'s coverage with the
  new `GET /v1/schedule` route: the full `ScheduleOut` field set, that
  `sweep_include_cached` mirrors the LIVE settings override (not a frozen
  snapshot) via `describeDemoSettings()`, and — the load-bearing part —
  `sweep_cached_gc_risk` follows the EXACT SAME formula as
  `vault_api/scheduler.py::cached_sweep_gc_risk`
  (`sweep_include_cached and auto_gc != "execute"`) across all six
  on/off × off/dry-run/execute combinations, with a named mutation pin for
  the case most likely to regress silently: dry-run counts as risky too,
  not only `off` (review round 1 nitpick: the first version of this pin's
  own description named the WRONG mutation — `auto_gc !== "off"` leaves it
  green too, since dry-run is `!== "off"` regardless; the mutation that
  actually kills it, reverted-and-reconfirmed, is checking EQUALITY to
  `"off"` in place of INEQUALITY to `"execute"`, which silently lets
  dry-run — a mode that reports without reclaiming — through clean).
- `demo-data-config-defaults.test.js` — a cross-language drift guard, not a
  behavioural test: `web/js/demo-data.js` cannot import
  `api/vault_api/config.py`'s Python constants, but it can read that
  module's source as plain text (the same "structural static analysis of
  source text" `css-hygiene.test.js` already does for CSS/JS) and
  regex-extract `DEFAULT_AUTO_GC`/`DEFAULT_SWEEP_INCLUDE_CACHED`, comparing
  them against `demo-data.js`'s own exported
  `CONFIG_DEFAULT_AUTO_GC`/`CONFIG_DEFAULT_SWEEP_INCLUDE_CACHED` — the
  values `SETTINGS_BASE.auto_gc`/`.sweep_include_cached` are built from.
  Exists because those two fixture rows shipped with the PRE-ADR-0014
  defaults (`"off"`/`false`) for months without anyone noticing the drift
  by hand. Distinguishes two failure modes explicitly, in both the
  assertion messages and this file's own header (review round 1 S3: the
  first version's blanket "never edit this file's regexes" advice was
  itself wrong for a reformatting-shaped failure) — VALUE drift (the regex
  still matches, but resolves to a different value: fix `demo-data.js`)
  from GRAMMAR drift (the regex no longer matches `config.py` at all: fix
  THIS file's regex, `demo-data.js` may be innocent). Green since the
  sibling package that flipped `api/vault_api/config.py` to the ADR-0014
  defaults merged (the "2 intentional failures" this paragraph used to
  record are gone; WP WEB-FIX-1, N2).

**Not covered, by design:** everything below needs a real rendered page,
which neither `node --test` nor a pure `lib/` module can exercise — checked
instead in a live Browser-pane pass against a real `uvicorn` instance (see
the coder's report for the exact steps and observed output): the toggle's
pixel layout/label fit inside `.segs` at narrow widths; how visually
prominent `.settings-warn` reads next to the rest of the Schedule section
(a subjective design question, not a pass/fail one); a real screen reader's
announcement of the `role="group"`/`aria-pressed` segmented-button pair
(the DOM semantics were confirmed correct by inspection, same posture as WP
4a.8's honest list, never that a real AT actually speaks them as intended);
and the 422-rejection toast as a PAINTED thing (its wiring was confirmed
structurally live — a simulated 422 left the save bar open and the draft
un-cleared — but not its visual appearance/timing on screen).

**Known residual, recorded rather than fixed (review round 2): a HANGING
`/v1/schedule` still blocks first paint.** Moving the `schedule` fetch into
`loadSettings()`'s `Promise.all` (S1, round 1) fixed the *rejecting* case —
a `.catch(() => null)` on that one promise means a `404`/`5xx`/network error
no longer turns into "Could not load settings" for the whole screen — but
`Promise.all` still waits for every promise to SETTLE, so a request that
never resolves at all (a stalled connection, a server that accepts the TCP
connection and never answers) leaves the screen on the loading skeleton
indefinitely, with no error shown. This is not a regression this package
introduced: `api.getSettings()`/`api.getSteamKey()` were already awaited
untimed in the same `Promise.all` before this WP touched the file, so a
hanging `/v1/steam/key` had the identical effect beforehand, and `api.js`'s
own module header already states there is no `AbortController`-based
timeout anywhere in it, for any request. The same class applies to
`saveDrafts()`'s post-save schedule refetch, for the same reason. A client
timeout is a real, app-wide mechanism (every call site in `api.js` would
need it, not just this screen's three) and is correctly out of scope for
this package — noted here so it is found the next time a client timeout
actually lands, rather than rediscovered from scratch.

### WP WEB-FIX-1 — review findings B1/B2/S1/S2/S3/P2/N2-N5

Suite at the end of this package: **820 tests, 820 pass, 0 fail**
(`node --test "web/tests/*.test.js"`; 772 before it, 812 after review
round 1 — round 2 added `fake-dom.test.js` and four onboarding tests).

New files:
- `onboarding-wiring.test.js` — fake-dom drive of `onboarding.js`: B1
  (a verified key test writes `steamvault.demoMode = "0"`; finish never
  reloads into demo), N3 (OK line carries no `health.version`), N5
  ("Test connection" sends no PATCH; `finish()` sends the changed
  `vault_name`, skips it when unchanged or read-only, shows a failed save
  on step 3 without reloading and retries on the next press), and the B2
  surface (`openOnboarding({notice})`, `isOnboardingOpen()`). Error lines
  are looked up per step section (`section.ostep[data-step]`), never by
  document position — step 2 has its own `p.errline`.
- `settings-view-wiring.test.js` — `views/settings.js`: B2 (a 401 on
  `GET /v1/settings` still renders the Connection section and its button
  opens the reconnect overlay), B1 (demo notice + "Connect to a vault"
  row, through the real `api.js` → `demo-data.js` path, zero fetches),
  P2 (a response missing one of the eight keys lands on the error line
  naming it). Compares TEXT, never nodes: a failed node assertion makes
  `node:assert` dump the whole fake-DOM graph and the process was
  OOM-killed (SIGKILL) during development.
- `auth-recovery.test.js` — `components/auth-recovery.js` (B2): the first
  AUTH-kind store error opens reconnect once; NETWORK/SERVER never;
  no stored key never; an already-open overlay neither re-opens nor
  consumes the one-shot; plus a source pin that `app.js` wires it.
- `fake-dom.test.js` (round 2, N1/N2) — self-tests for the shared shim:
  the `innerHTML` setter accepts exactly ONE top-level element and throws
  otherwise, reading `innerHTML` throws "unsupported", and text nodes live
  in `childNodes` but never in `children`.
- `store-singleton-gate.test.js` — N4: no key and no demo starts no loop
  (zero requests); a key or demo mode starts them.
- `view-announcer.test.js` — S2: literal `VIEW_TITLES` pin + router twin
  pin, `<main id="view-root">` has no `aria-live` (HTML comments stripped
  before the scan), static `#view-announcer` (`.sr-only`, `role="status"`),
  and the announcer write lives inside `renderView`.

Extended: `settings-diff.test.js` (S3 trimmed string body, arrays
verbatim), `settings-presentation.test.js` (P2 `missingSettingKeys`),
`demo-data-cached-prefill.test.js` (S1: shared-with-cached-co-owner not
selected; the delete-then-check-all carry-over scenario; remnant after
both co-owners deleted), `fake-dom.js` (DocumentFragment move semantics,
text nodes; round 2: `childNodes` is now the backing list of elements AND
text nodes with `children` as the element-only view, and `innerHTML` is
strict — the setter takes ONE top-level element and throws on anything
else, the getter throws instead of returning a guess).

Mutation evidence (each applied alone, full suite run, then restored):
Connection section moved back below the load-error return → 2 fail;
`setDemoMode(false)` removed from the key test → 2 fail; `aria-live`
restored on `<main>` → 1 fail; announcer write removed → 1 fail; S3 trim
reverted → 2 fail; P2 guard disabled → 1 fail; auth-recovery once-guard
removed → 1 fail; `createAuthRecovery` call removed from `app.js` → 1
fail; N4 gate forced to `return true` → 1 fail (it HUNG the suite until the
first test moved `store.stop()` into `finally`); S1 filter reverted to
`depots.length > 0` → 3 fail; demo notice disabled → 1 fail; N3
`health.version` suffix restored → 5 fail.

Not covered: a real screen reader actually speaking the announcer, and the
demo notice's painted look (it reuses the existing `.hint` class only).

### WP DOCS-FIX-2 — fake-dom `textContent`

Closes the harness gap recorded after WEB-FIX-1 round 2: `textContent` was
a plain property, so an element built from child nodes read back as
`undefined` and setting it left the old children in place. It is now a
real-DOM getter/setter pair — the getter joins the children's text when
`childNodes` is non-empty (own text otherwise, `""` by default); only a
`#text` node stores its own text. On an element the setter detaches the old
children and, for a non-empty string, leaves exactly one new `#text` child
(an empty string leaves none), so `p.textContent = "x"; p.append(b)` reads
back `"xB"`. Four cases in `fake-dom.test.js` pin this (getter join; setter
→ one `#text` child; `""` → no children; set-then-append → `"xB"`); each
half was mutation-checked (getter reverted to own-text-only → 1 fail; setter
no longer clearing → 1 fail; setter not creating the `#text` node → 3 fail).
Suite: **824 tests, 824 pass, 0 fail**.


### WP WEB-FIX-2 — connection-lost indicator

The review's P1: every store subscriber drops `{error}` payloads, so a
vault-api restart or a dropped network left a frozen snapshot with
live-looking Downloads controls and no word about it. Now one app-level
banner, in the bypass banner's shell slot and styles, says "Lost connection
to the vault — showing the last data received (last update HH:MM).
Retrying…" (the suffix is omitted when no poll has succeeded yet in this
page load).

Threshold (`lib/connection-watch.js`, `LOST_AFTER_MS`): only NETWORK and
SERVER errors count. The banner shows on a counting failure that arrives at
least **20 s** after the first failure of the current streak, with no
successful poll of any resource in between. That means at least two
failures spanning 20 s. A bare count of two would not work, because
backoff.js retries after about 1 s, so two failures can be a single blip.
A real outage shows within about 20-37 s of the first failed poll (one
16 s backoff step past the threshold, +/-20 % jitter; review simulation
of backoff.js: 20.0-35.5 s). Known limits: an endpoint that fails while
the others succeed never shows the banner (by design: the vault answers),
and a silently dropped connection hangs fetch until the browser gives up,
because api.js sets no fetch timeout.
Any successful poll clears it. AUTH (owned by `auth-recovery.js`) and the
other kinds are ignored, so they neither start nor end a streak. Demo mode
subscribes to nothing. Each transition announces once through
`#view-announcer` (shown: the banner text; cleared: "Connection to the vault
restored."). While the banner is shown, Downloads disables every job-control
button (Pause/Resume/Cancel/Remove) and gives each the title "Not available
while the connection to the vault is lost."

Markup: the two banners now share ONE `.banner-wrap` (`#banner-wrap`),
because at BP-L that element owns the single "banner" grid area. Each
banner has its own `[hidden]`-toggled `.banner-slot`, and
`lib/banner-wrap.js` shows the wrap while any slot is shown. The only new
CSS is a margin between two slots that are both shown (no colour, no
`display`).

- `connection-banner.test.js`: subscribes to all four resources; one failure
  stays silent; a blip under 20 s stays silent; it shows past 20 s with the
  exact text and "last update 14:05"; there is no suffix before the first
  success; any success clears it; AUTH never shows it and does not break a
  NETWORK streak; demo mode makes no subscription; it announces once per
  transition; the shared wrap stays visible while the bypass slot is shown.
  Wiring pins: app.js calls the factory at top level with the three shell
  ids, the `#view-announcer` write and `setConnectionLost`; index.html has
  one wrap containing the connection slot and then the bypass slot.
- `connection-downloads-wiring.test.js`: the real `views/downloads.js`
  against the real store and a fake fetch. All five controls are live, then
  disabled with the title on loss (both repainted and freshly built), then
  live again on restore. The real `bypass-banner.js` still un-hides the
  shared wrap.
- `fake-dom.js`: `append()` now accepts strings as text nodes, as the real
  DOM does (Downloads' `queueHeading.append("Queue ")`). This is pinned in
  `fake-dom.test.js`.

Mutation evidence (each applied alone, full suite run, then restored):
20 s threshold removed → 1 fail; first failure shows → 2; success does not
clear → 3; AUTH counted → 1; last-update suffix dropped → 1; demo gate
removed → 1; once-per-transition guard removed → 6; announce call removed →
1; `setConnectionLost` publish removed → 3; factory wrap sync removed → 3;
app.js call removed → 1; app.js announce turned into a no-op → 1; Downloads
gate call removed → 1; Downloads transition repaint removed → 1; Pause's own
`disabled` overriding the gate → 1; same for Cancel → 1; bypass-banner wrap
sync removed → 1; gate title dropped → 1; fake-dom `append(string)` reverted
→ 2.

Not covered: the banner's painted look at each breakpoint (no browser
here; it reuses `.banner`), a real screen reader speaking the announcement,
and the clicked-while-dropping case (`withButtonBusy` re-enables a button
after a failed click until the next repaint).
Suite: **840 tests, 840 pass, 0 fail**.

### WP WEB-FIX-3 — the UI fits a phone screen

User report, 2026-10-01: Pixel, Chrome, portrait (~412 CSS px). The whole
page was zoomed out, the Settings segments wrapped "Dry run", the webhook
event checkboxes sat above their labels, and the last Settings section
stayed under the bottom nav. Analysed statically; no browser runs in this
devbox.

- **The zoom-out is NOT explained by this WP.** What follows is hardening
  against horizontal overflow, not the cause of the report: the user's
  Library was empty and Settings has no grid. **Resolved 2026-10-02:** the
  root cause was Chrome's "Desktop site" mode (a ~980px layout that
  ignores the viewport meta), confirmed by the user on the Pixel: turning
  it off fixed the layout. Mitigated by the WP WEB-FIX-5 hint banner
  below, not by forcing a zoom (user decision). The sign was as predicted:
  a full-width header and nav around a narrow, centred 760px column
  (BP-M on, BP-L off).
- Hardening: the phone library grid used `1fr` tracks (`repeat(2,1fr)`,
  `repeat(3,1fr)`, list `1fr`). `1fr` is `minmax(auto,1fr)`, so a track
  never shrinks below its card's min-content width, and the card holds
  `white-space:nowrap` text (`.instbadge`; `.rowname` in the list layout).
  A long line could widen the grid past the viewport. Now `minmax(0,1fr)`
  plus `.card{min-width:0}`, with `min-width:0` flex text columns,
  `overflow-wrap:anywhere` text blocks and the toast on `100%`, not
  `100vw`.
- Real bugs fixed: the Settings text segments reused the 28px icon-segment
  width (`.field > .segs`, now `nowrap` and scrolling instead of wrapping);
  the webhook rows lost to `.field label`'s block/uppercase rule
  (`.field label.srow` restores the inline row); inputs are 16px below
  BP-M.
- Bottom nav: `.nav` is `position:sticky` and in flow, so it covers no
  content at scale 1 and no view padding was added. The pin asserts it
  stays sticky, never fixed, and that `.view-root` carries no
  `padding-bottom`. The reported overlap is not explained by static
  analysis; it was reported while "Desktop site" mode was on (see above).
  Whether it remains with that mode off was not reported separately.
- `html, body{overflow-x:clip}` (theme.css) is defence in depth only. It
  is NOT the fix: it hides a future overflow, it does not stop one being
  introduced. It uses `clip`, never `hidden`, because `hidden` on body
  creates a scroll container and breaks the sticky topbar and bottom nav.
- `css-mobile-overflow.test.js` pins each of these, top-level or in the
  BP-M block, plus a scan that finds no top-level fixed width above 360px.
  `css-layout-foundation.test.js`: the base grid pin now expects
  `minmax(0,1fr)`.

Mutation evidence (each applied alone, the CSS pin files run, then
restored), all killed: safety net to `hidden` or removed; each grid back to
`1fr`; `.card` `min-width` removed; list `.meta` shrink removed; `.jobtop >
div` and `.banner .body` `min-width` removed; `overflow-wrap` reverted;
toast back to `100vw`; toast text `min-width` removed; a 400px `min-width`;
segment `nowrap`, `width:auto` or `max-width` removed; `.srow`
`display:flex` or uppercase reset removed; `.nav` switched to `fixed`; a
`.view-root` `padding-bottom` added; the 16px inputs or their BP-M restore
reverted.

Real-device verification happens after deploy: the user checks on the
Pixel, first whether Chrome's "Desktop site" is on, then Library, Settings
to the About section, and Downloads. Not fixed here: when selecting games,
the fixed bulk bar covers the last row of cards (its height varies, so it
needs a live measurement).
Suite: **851 tests, 851 pass, 0 fail**.

### WP WEB-FEAT-1 — owned Steam games in the library

The library used to list only `GET /v1/games` (games the vault knows) and
called that count "owned". It now shows the union of the vault's games and
the owned list of the vault's stored library SteamID64
(`steam_library_steamid`, WP API-FEAT-1).

- `owned-library.test.js` — the pure module `web/js/lib/owned-library.js`
  plus the two decisions it bends. G1 merge (union by appid, owned
  duplicates collapse, vault rows win for cache state, a missing vault name
  is filled from Steam, owned-only rows flagged `owned_only` and read as
  "Not cached"); G2 header ("N owned · M on the cache" only once the owned
  list loaded, otherwise "V games on the vault · M on the cache"); G3 error
  text (409 / 422 reuse the Settings strings, 0 games = private-profile
  hint, a failure keeps the vault list); G4 detail-only download (no card
  quick action in `statusAction`, `classifyBulkSelection`'s `notOnVault`
  bucket never becomes a bulk target, notes never call such a game "already
  cached"); G5 the setting is read only as a JSON string; G6 the loader has
  no timer and drops superseded results.
- `library-owned-wiring.test.js` — `views/library.js` through fake-dom, the
  real `api.js` and the real store singleton against a counting fetch fake:
  header text in both modes, owned-only cards without action buttons, no
  relay call on poll ticks (only view open and the Reload button), 409 and
  0-games notices with the vault cards still on screen, the detail sheet's
  "Download to cache" queuing exactly that appid, the notice inside
  `.lib-checkrow` (the BP-L area grid keeps six in-flow children), the
  live-region notice not rebuilt by a search keystroke, and a CSS pin for
  `.lib-owned` (wraps anywhere, no width).
- `steam-library-setting.test.js` — the Settings "Steam library" block:
  pre-fill from the setting, Save sends the RAW body
  `{"steam_library_steamid":"<17 digits>"}` (a string, never a number),
  trims, refuses invalid ids inline without a PATCH, shows a server 422
  inline, sends nothing for an unchanged value and `""` for a clear,
  read-only and older-server fallbacks, Preview's 409 / 0-games wording.
- `demo-data-owned-library.test.js` — the demo setting mirrors the real one
  (blank default, applies immediately, number -> 422, invalid -> 422, blank
  is an override, null resets), the demo owned list overlaps the demo vault
  (2 deduped, 3 owned-only), and queuing an owned-only demo game keeps its
  title.
- `fake-dom.js` gained the bare `[attr]` presence selector
  (`.card[data-appid]`, used by `views/library.js` on every games tick);
  other attribute operators still throw. `fake-dom.test.js` pins both.

Mutation evidence (each applied alone, full suite run, then restored), all
killed: the old `${games.length} owned` header; the `owned_only` guard in
`statusAction` removed; the `notOnVault` bucket removed; `load()` called
from the games subscription (relay polling); the SteamID sent as
`Number(...)`; the 409 mapping removed; owned rows overriding vault rows;
a numeric setting accepted; an empty owned list emptying the library; the
notice render call removed; dedupe removed; `load()` removed from view
open; `load()` only on the first open; the Reload button unwired; the
live-region rebuild guard removed.

Review fix round (PASS with should-fixes):
- The notice's nodes are built once per mount and only updated: the live
  region is the text span alone (`role="status"`), its text is written only
  when it changes, and the Reload button is the SAME node through its own
  reload cycle, busy via `aria-disabled` (never `disabled`, which drops
  focus), with a click guard. Pinned in `library-owned-wiring.test.js`
  (same node, focus kept, `aria-disabled` true while loading, no second
  request, live region = span only).
- A READY list with 0 games (private profile) uses the vault header wording,
  never "0 owned"; the notice keeps the private hint.
- Settings: the SteamID input is never disabled (Preview must work on a
  read-only vault and an older vault-api); only Save/Reset are gated. New
  Reset (PATCH `null`, shown only for a `db` override); blank + Save stays
  the `""` override. Preview errors: 409 shared no-key text, 422 the
  typed-id validation text, others the plain server error. The button row
  is a `.btnrow` class (`flex-wrap:wrap`). The test uses a polling
  `until()` instead of fixed sleeps (one negative "no PATCH" check keeps a
  short wait).
- `owned-library.test.js`: an older load's settings read landing after a
  newer load finished does not overwrite READY.

Mutation evidence for the round, all killed: Reload button rebuilt per
render; `disabled` set while loading; `role=status` on the whole line;
click guard removed; "0 owned" header back; input disabled when read-only;
Reset sending `""`; Reset always hidden; Preview 422 saying "stored";
generation check after the settings read removed; live-region text always
rewritten. Re-checked after the refactor: notice render removed, Reload
unwired, relay polled on games ticks — all still killed.

Suite: **908 tests, 908 pass, 0 fail**.

### WP WEB-FEAT-2 — onboarding step 2 saves the SteamID64

- `onboarding-steamid.test.js` — `onboarding.js` step 2 through fake-dom
  and the real `api.js` against a recording fetch fake: a lookup the relay
  answered PATCHes the RAW body `{"steam_library_steamid":"<17 digits>"}`
  and confirms it in a `role="status"` span; the same id again sends no
  second PATCH; relay 409 / 422 / 504 send no PATCH and show the shared
  strings; an invalid typed id reaches neither relay nor settings; 0 games
  still saves and shows the private hint; read-only settings and an older
  vault-api without the setting send no PATCH and say "Not saved"; the
  input is pre-filled from the stored value, a typed id is not overwritten,
  and a fresh open clears both input and saved line.
- The save goes through `lib/owned-library.js`'s `saveLibrarySteamId`,
  which Settings' "Save SteamID64" now uses too, so
  `steam-library-setting.test.js` pins the shared helper from that side.

Mutation evidence (each applied alone, then restored), all killed: the
save call after a lookup removed; a save added to the failure path; the
private hint removed; the read-only guard removed; the absent-setting guard
removed; the prefill removed; the id sent as `Number(...)`; the settings
snapshot not replaced by the PATCH answer; the input not cleared on open;
Settings not taking the PATCH answer from the shared helper.

Review fix round (FAIL on test gaps; production code judged correct):
- `onboarding-steamid.test.js` gained: failed PATCH 500 ("Not saved:
  <detail>.", snapshot kept so the next Look up PATCHes again) and 422
  (invalid-id text plus detail, one period); env-only setting (no PATCH,
  "set by the server environment"); double click while the relay call is
  gated (one relay call, one PATCH); input edited mid-flight (the looked-up
  id is saved); "Go to library" during a running save (waits, PATCH before
  the one reload, a second press does not reload twice); a later failed
  lookup clears "Saved"; the status `<p>` is never hidden; the input is
  `aria-describedby` its hint; the Saved line names the id.
- `owned-library.test.js` gained direct `saveLibrarySteamId` cases (SAVED,
  INVALID, UNCHANGED, READONLY, ABSENT, ENV_ONLY, blank override, ERROR
  500/422, the "Request failed." fallback) and `describeLookupError`.
- `keyboard-pointer-model.test.js`: `.btn[aria-disabled="true"]` joins the
  order-dependent pairs (must follow `.btn:hover`).

Mutation evidence for the round (each applied alone in the real tree, full
suite run, then restored), all killed: ERROR reported as Saved; the
in-flight guard removed; the in-flight promise never stored; the live input
value saved instead of the looked-up id; `finish()` not awaiting the
lookup; the `finish()` re-entry guard removed; the clear of the save line
removed; the busy CSS rule removed; an optimistic snapshot update on a
failed save; the period rule accepting `)`; the env-only branch removed;
the fallback without a period; `aria-describedby` removed; the id dropped
from the Saved line; the status `<p>` hidden when empty; the helper's
validation removed.

Final round: "Go to library" shows "Saving…" (aria-disabled, never `disabled`) while a save runs and restores label and aria state when the overlay stays open; an unexpected Look up error is logged with `console.error` (never rejects). Mutations killed: label, aria-disabled, `disabled` instead, aria restore, label restore, the log.

Suite: **934 tests, 934 pass, 0 fail**.

### WP WEB-FIX-4 — job titles from the owned list, failure hints

- `job-failure.test.js` — `lib/job-failure.js`: the reason comes only from
  vault-api's `[vault-api] Prefill failed (reason=...)` line, and only when
  it is the LAST non-empty line (trailing blanks and CRLF tolerated), only
  for a failed prefill job; SteamPrefill's login prompt text alone is no
  reason. The one text exception, the public-IP hint: detected on
  `reason=exit_code` plus the exact phrase "is resolving to a public IP"
  only, never for other exit_code output, other reasons, near-miss wording
  or SteamPrefill's other `LancacheNotFoundException` ("Unable to detect
  Lancache server!", a heartbeat failure with any cause). Hint texts; `isNewestJobForApp` (prefill
  jobs only). Drift guards against deploy/README.md: `LOGIN_COMMAND`
  (backslash continuations joined) and the "Fix it (only needed with a
  dedicated VAULT_CORE_BIND)" heading. Plus `lib/owned-library.js`'s
  `appTitle` order, `fillMissingNames` and the refactored merge.
- `downloads-owned-names.test.js` — `views/downloads.js` through fake-dom
  and the real store, tests in order on a page-global owned list: no
  relay call while every job has a vault name; the title order; a gated
  relay proves the owned name (row title and Retry `aria-label`) is
  painted by the owned-list subscription with no further `/v1/games`
  call; exactly one owned-list load for
  `/downloads` opened directly; the not_logged_in block (first, command,
  folder wording, closed `<details>`, Retry with `aria-label="Retry
  <title>"` POSTs `/v1/prefill`); the `<details>` open state survives a
  full rebuild caused by another job (`fireToggle` THROWS when nothing
  listens, so a missing handler cannot pass silently); Retry offline-gated
  (WEB-FIX-2 style); no Retry on an older row when a newer prefill exists;
  a plain exit_code failure unchanged; the public-IP block.
- `downloads-owned-preloaded.test.js` — an already-loaded owned list names
  the job and Downloads adds no relay call (separate file: page-global).
- `owned-names-wiring.test.js` — source pins: notification panel and
  detail sheet use the owned fallback; the Library uses the shared loader;
  app.js hands it to the decision panel.
- `decision-panel-wiring.test.js` gained the owned-name fallback case;
  `css-mobile-overflow.test.js` the command-box wrap pin;
  `demo-data-owned-library.test.js` pins that demo enqueue leaves the new
  vault row unnamed, like the real API.

Mutation evidence (each applied alone, the affected files run, then
restored), all killed: owned-list subscription removed; toggle listener
removed; rawOpen not applied on rebuild; Retry built without the offline
gate; reason taken from any line; Retry on every row; GC jobs counted as
newer; aria-label removed; old folder wording; public-IP detection off,
without exit_code, marker broadened; README heading drift; login command
drift; a block for every failure; round 3: the old regex matching the
bare exception name; the Retry label not patched; the old newer-job line;
the subprocess clause and the `up -d` step removed; no owned fallback; loading on every
render; never loading; the games-known gate removed; `<details>` open by
default; the fill adding rows; the merge mutating its input.

Suite: **963 tests, 963 pass, 0 fail** (round 3 added assertions, no new tests).

### WP WEB-FIX-5 — "Desktop site" hint banner

Root cause of the WEB-FIX-3 zoom-out, confirmed by the user on a Pixel in
Chrome (2026-10-02): "Desktop site" mode. Chrome then ignores the viewport
meta, lays the page out at ~980 CSS px and scales it down. The user chose
a dismissible hint over auto-zoom. A third `.banner-slot` (`#desktop-hint`)
in the shared `#banner-wrap` (no new `#app` grid child, so the BP-L area
map is unchanged), driven by `components/desktop-site-hint.js`; the rule
and the dismissal store are pure in `lib/desktop-site-hint.js`.

- **Rule** (all four): `(pointer: coarse)`; screen short side < 600 CSS px
  (phone, not tablet: Chrome's tablet desktop mode uses the real window
  width, so the page is not scaled there); layout width
  (`documentElement.clientWidth`, immune to pinch zoom) > 900; and layout
  width >= 1.5x the screen width in the current orientation. The ratio
  rule exists because a Pixel 7 in LANDSCAPE, normal mode, has a real
  915px layout, which `innerWidth > 900` alone would flag.
- **Assumption, not device-verified:** Chrome keeps `screen.width/height`
  at the device size (412x915 on a Pixel 7) in desktop mode. If a device
  reports 980 there, the hint never shows (fails toward no hint). Known
  gaps: desktop mode in phone landscape (980 vs 915, ratio 1.07) is not
  detected; a page zoom below 100% can trigger the hint.
- Re-evaluated on `resize`/`orientationchange`, debounced 200 ms; `hidden`
  is written only when the verdict changes (no flicker).
- ✕ dismisses for good: `localStorage` key
  `steamvault.desktopSiteHintDismissed`; every access (including reading
  `window.localStorage` itself) is in try/catch, falling back to an
  in-memory flag for the session. Listeners are removed after dismissal.
- a11y: the slot is `role="region"` with `aria-label="Display hint"`; the
  close button is a plain `<button type="button">`, 32px square. Its
  `aria-label` ("Dismiss desktop view hint") and the hint text have one
  source, `lib/desktop-site-hint.js`; index.html carries neither. On
  dismiss, focus moves to `#view-root` (now `tabindex="-1"`, no focus
  ring) instead of falling to `<body>`.
- Review fix: app.js passed `storage: window.localStorage` to the
  decision panel. With site data blocked that getter throws and the page
  stays blank. It is now a try/catch IIFE returning `null`, which
  decision-panel's readFlag/writeFlag already catch.
  `app-storage-guard.test.js` fails on any bare `window.localStorage`
  in an argument position in app.js.
- No store, no API: identical in demo mode.

`desktop-site-hint.test.js` (31 tests): the detection matrix (phone +
desktop mode show; phone normal, phone landscape, real desktop, narrow
desktop window, fine-pointer narrow screen, 600px tablet, iPad, Pixel
Tablet hide), the dismissal store with throwing `getItem`/`setItem` and a
throwing `getStorage`, resize/orientationchange re-evaluation, the
debounce/no-flicker write counter, listener removal, focus on dismiss,
the index.html a11y markup and the app.js wiring. Timers run on a manual
queue injected through `setTimer`/`clearTimer`, so the negative
assertions are deterministic, with no sleeps. `app-storage-guard.test.js`
(3) pins the guard; `decision-panel-wiring.test.js` gains a `storage:
null` case.

Mutation evidence (each applied alone in a scratch copy, the test file run,
then discarded), all killed: pointer guard removed (rule-1 fixture); short
side guard removed (600px tablet fixture); 900 floor removed (800px layout
fixture); ratio rule forced true (landscape normal fixture); in-memory flag
set only after a successful write; read not in try/catch (`SecurityError`
escapes); `setItem` removed; the no-change guard removed (write counter
2 !== 1); the debounce removed (3 !== 1); the resize listener dropped;
`dispose` not called on close (2 !== 0 listeners); `syncBannerWrap`
removed; close `aria-label` not set; boot-time dismissal ignored; the
app.js `getStorage` getter removed; `role="region"` removed; `tabindex="-1"`
on the close button; hint text changed. Review round: the app.js guard
reverted to `storage: window.localStorage` (3 fail); decision-panel
readFlag's try removed (null storage throws); the focus call removed
(0 !== 1); focus call not wrapped ("focus failed" escapes); the app.js
focus wiring removed; `#view-root` tabindex removed; the aria-label
duplicated back into index.html; debounce removed (3 fail); debounce not
clearing the previous timer (5 !== 1 pending); dispose keeping a pending
timer (1 !== 0); the no-change guard removed (2 !== 1).

Not covered: the painted look and a real device (no browser here); a
screen reader. Real-device check after deploy: on the Pixel with "Desktop
site" on, the hint shows and the ✕ hides it across reloads; with it off,
no hint.
Suite: **969 tests, 969 pass, 0 fail**.

### WP WEB-FIX-7 — the download arrow falls through the badge

- `status-icon-download.test.js` — the running download glyph (user
  decision Weg A, "arrow falls through"), fake-dom plus theme.css/app.css
  static analysis: the `vault-dlfall` keyframes animate `transform` only
  (no opacity; the old `vault-dlslide` is gone); the running `.dla` group
  holds the unchanged arrow plus ONE identical trailing arrow
  (`g.dlnext`, `translate(0 -32)`), and the keyframes move the group by
  exactly `DOWNLOAD_FALL_PERIOD` (32) from 0, in two steps, `linear
  infinite`, ~1.6s (about 20 units/s). The clip is
  `.sic.k-running{ clip-path:circle(50%) }` on a square `border-radius:50%`
  badge, no `.sic` size rule may make it non-square, `.sic svg` keeps
  `overflow:visible`, and building every kind three times yields no
  duplicate id. Geometry from the real paths and the `.sic svg` 64% box
  (disc radius 12/0.64 = 18.75 units around (12,12)):
  - at rest the parked trailing arrow (stroke included) is fully outside
    the disc and the leading one fully inside;
  - exit constraint: at the end frame (dy = period) the leading arrow's
    nearest ink is at least 1 unit outside the disc (measured 3.4), and
    the trailing arrow sits exactly at the rest position. Both ends of
    the cycle therefore show the same thing inside the disc: one
    rest-position arrow and no other ink. The snap back to 0 is visually
    seamless; that is the only sense in which "end frame equals start
    frame" holds (the transforms differ, the visible pixels do not). At
    the first draft's period 24 this failed: the leading shaft top was
    still 4.6 units inside the disc, so about 2px of shaft vanished in a
    single frame every cycle (review FAIL);
  - at every sampled phase the more-visible arrow has at least 65% of its
    stroke length inside the disc, ink included. Measured worst case:
    70.1%. A whole arrow is NOT always inside: with a period wide enough
    for a clean exit there is a moment where one arrow is leaving and the
    next is entering.
  Reduced motion: the wildcard override is there, and the running
  animation has no fill-mode, no delay and no `!important`. Only
  `.sic.k-running .dla` animates the glyph, nothing moves `.sic` itself
  or a paused glyph, only `running` builds a trailing arrow, `none` keeps
  arrow plus baseline, and the glyph stays `aria-hidden` with the word
  "Downloading".

Mutation evidence (each applied alone, full suite run, then restored),
all killed. First round: opacity in the keyframes; trailing offset 22;
keyframe travel 22px; ease-in-out; an intermediate keyframe step; clip
removed; clip `circle(40%)`; clip only inside a media query; a fixed
`id` on the trailing group; period 12; `forwards`; `!important` on the
animation; a delay; the reduced-motion iteration-count line removed;
`k-none` or `k-paused` animating `.dla`; the badge itself animating; a
trailing arrow for every kind; the running baseline shown again; a
different trailing shape; the svg box at 45%; the trailing arrow below
instead of above; a non-square `.sic-sm`; `aria-hidden` dropped. After
the review fix (period 32, 1.6s): period 24 with 24px/1.2s applied
consistently in JS and CSS (killed by the end-frame pin and the literal
pin; this is the review's bug); period 28 in JS only; `.sic svg`
`overflow:hidden`; opacity; travel 30px; ease-in-out; 3s duration;
clip removed; `forwards`; trailing arrow below; svg box 45%; `k-none`
animating.

Not covered: the painted motion itself (no browser here). Device check
item: `clip-path` on the badge anti-aliases the disc rim a second time on
top of the `border-radius` background edge, which may look a little
softer or show a faint fringe on some screens; look at a running badge
on the Pixel at 15px (cols3) and 19px (detail sheet).

Suite: **1010 tests, 1010 pass, 0 fail**.

### WP WEB-FEAT-3 — Settings: About and PCs (agents)

Settings gains two sections over WP VER-2's `GET /v1/about` and WP
AGENT-FEAT-1's presence fields on `GET /v1/clients`:

- **PCs (agents)**: the "Agents: N online, M offline" line and a "Show
  PCs" button that opens the existing clients sheet (decision: reuse the
  sheet, one row rendering and one store subscription instead of a second
  list; the sheet stays a sheet, not a nav item). The sheet was reachable
  only from the bypass banner and the notifications before. Each row now
  has a presence chip (the server's `presence`, never recomputed), "last
  seen … ago" and the agent version ("version unknown" for `null`); chip
  and time are repainted on every clients tick while the sheet is open
  (text only). Sheet title and dialog label are now "PCs (agents)".
- **About**: a table, one row per component (name, version, 7-character
  commit with the full id as title, status word + icon), then a plain note
  per component (vault-core "recorded at last start, not a live check",
  vault-dns "unknown, not probed", …) and the server's `detail` as text.
  "Checked by the server N min ago" from the oldest `checked_at`, with the
  60 s server cache stated. Loaded when Settings opens and on Refresh,
  never polled; Refresh is `aria-disabled` while in flight and announces
  its outcome in the section's only `role=status` span. Only a 404 shows
  the "server too old" note (with a valid key an unknown route is 404); a
  401 means the key was refused and is an error line (review fix). Two new neutral status-icon kinds, `unknown`
  ("?") and `notinuse` (dash), so "not checked" never reads as a fault.
- **Rail**: the version line ("dev build") is a `<button>` named
  "About: <version>" that opens Settings and focuses the About heading.
- **Demo**: `/v1/clients` rows carry the four AGENT-FEAT-1 fields (a
  10-minute agent online, a legacy row with nulls offline; presence and
  `offline_after` computed per request by the server's rule); a new demo
  `/v1/about`. The demo-data.js header no longer claims every route
  matches the server.

Tests: `about-view.test.js` (13: every status word/icon, version/commit
cells, notes, oldest-`checked_at` relative time, too-old classification,
status set/names/cache pinned against `routers/about.py`/`about.py`),
`clients-presence.test.js` (8: presence from the server field with
fixtures that contradict the timestamps, summary counts, "version
unknown", `formatAgo` boundaries), `demo-data-shape-guard.test.js` (9:
demo `/v1/clients` and `/v1/about` keys EQUAL `ClientOut`/`AboutOut`/
`ComponentOut` read from the Python source, names/order/statuses, presence
constants and `offline_after` per row), `settings-about-pcs-wiring.test.js`
(13: fake-dom + real store-singleton against a routing fetch fake: the
About table for every status, 404/401/500, no polling + Refresh, the
in-flight guard, the PCs sheet opened from Settings with no bypass, server
presence in the chip and both summary lines, "version unknown", a presence
flip on an open sheet, the rail button, About focus), `css-about-pcs.test.js`
(6: phone stacked rows on `minmax(0,1fr)`, wrapping, the BP-M table, chip
column, rail button without author `display`). `css-overlay-geometry`'s
dialog-label pin follows the rename. The wiring file ran 20x in a loop: 0
failures.

Mutation evidence (each alone, in a scratch copy, the five new files run):
all killed. presenceOf recomputing from `offline_after` (5 fail); the
summary counting by timestamp (3); `agent_version` null printed as "agent
null" (3); 401 as an error (2); settings ignoring the too-old verdict (2);
the Show PCs click unwired (4); About refetched on each clients tick (1);
demo `presence` dropped (3); demo `offline_after` with 1x interval (2);
commit not shortened (3); `unknown` with the warn glyph (3); rail click
unwired (1); `requestAboutFocus` ignored (1); the sheet not repainting
presence on patch ticks (1); newest instead of oldest `checked_at` (1); an
extra demo `/v1/about` key (1); `formatAgo` "just now" up to 2 min (1);
phone rows on `1fr` tracks (1); the server `detail` dropped (1). Server
side of the twin pins: a new `ClientOut` field, `CACHE_TTL_SECONDS` 30,
`PRESENCE_GRACE_SECONDS` 10 min and a fifth status word each fail one
named drift guard.

Not covered: the painted result and a screen reader (no browser here).
Expected look: on a phone the About table is one block per component
(name, then VERSION and COMMIT side by side, STATUS below, each with a
small uppercase label, then the note and the server detail); from 720px
up a four-column table with a header row. In the PCs sheet each row shows
the presence chip (filled dot "Online", hollow ring "Offline") above the
Healthy/Bypassing badge. Device check: a long agent version wraps inside
the row on the Pixel; Refresh announces once with TalkBack.

Review round 1 (FAIL, two deletable wirings) added `rail-about-wiring.test.js`
(2 source-scan pins: `onVersionActivate` inside the `createRailPanel({...})`
argument with both calls in order, failure text "DELETED" / "MOVED" /
"CHANGED"; `#rail-version` is `<button type="button">`), the 401-is-an-error
flip, a drift guard for the vault-runner note's "90 s" against
`runner_presence.PRESENCE_STALE_SECONDS`, shape-guard messages that print the
field list the regex read, a ComponentOut/AboutOut reader sanity test, the
sheet-heading pin, and the focus-request reset on leaving Settings; the
duplicate agents line under About is gone. Mutations, all killed: handler
deleted (DELETED), moved out of the call (MOVED), `requestAboutFocus()`
dropped (CHANGED); `#rail-version` back to `<p>`; 401 back to too-old (2);
the note at 60 s, the server constant at 120; focus flag not reset on leave;
sheet heading "Clients".

Suite: **1069 tests, 1069 pass, 0 fail**.

### WP WEB-FIX-6 — the bulk bar no longer covers content

Noted during WEB-FIX-3: in select mode the fixed `.bulk` bar covered the
last row of cards. Static analysis found a second cause on phones with a
home indicator: `--nav-h` (64px) does not include
`env(safe-area-inset-bottom)` (`.nav` adds it to its own padding), so the
bar's `bottom:calc(var(--nav-h) + 14px)` sat `inset - 14px` px inside the
nav.

- `.bulk`: `bottom:calc(var(--nav-h) + var(--bulk-gap) +
  env(safe-area-inset-bottom, 0px))`, plus left/right safe-area insets
  below BP-L. New tokens in theme.css: `--bulk-gap:14px` (the old literal)
  and `--bulk-h` (fallback 168px).
- Select mode (`body.selecting`, library.js's existing class) defines
  `--bulk-room = --bulk-h + 2 x --bulk-gap` (BP-L: plus the bottom inset,
  since no bottom nav carries it there) and puts it on the box that ends
  the flow: `.view-root`'s bottom padding, or a visible Suggestions card's
  bottom margin (the view then gets its 32px back through a
  `:has(> :where(...))` rule; without `:has()` both carry the room, a
  wider gap, nothing hidden). The plain `.view-root` rule still has no
  padding-bottom (WEB-FIX-3 pin unchanged).
- BP-XL with the Suggestions column: the bar's right inset becomes
  `--panel-w + --gutter` (it used to span the column), the room goes back
  to the view, the column keeps its 32px.
- `--bulk-h` is measured live: `lib/bulk-room.js` (one ResizeObserver for
  the module's lifetime, re-pointed per mount, released by `unwatch()`
  in library.js's view-change listener, zero heights ignored) writes the
  bar's `getBoundingClientRect().height`, rounded up, onto `<html>`. No-op
  without ResizeObserver; the CSS fallback applies.

Tests: `css-bulk-bar-room.test.js` (13: tokens, bar above the nav with
the inset, no breakpoint overrides `bottom`, horizontal insets, room
formula at base and BP-L, view/panel room and the `:has` restore, BP-XL
inset and room, WEB-FIX-3 rules, library.js wiring) and
`bulk-room.test.js` (5).

Mutation evidence (each applied alone, the CSS pin files plus
`bulk-room.test.js` run, then restored), all 22 killed: bar without the
bottom inset; bar back to `+ 14px`; room without the second gap; view
room removed; BP-L room without the inset; panel room removed; the `:has`
restore removed; BP-XL bar right back to `--gutter`; BP-XL view room
removed; BP-XL column margin restore removed; left / right insets
removed; an unscoped `.view-root` padding-bottom; a BP-L `.bulk` bottom
override; `--bulk-h` unitless; `--bulk-h` 60px; `--bulk-gap` 8px; the
`watch()` call removed; no `disconnect()`; zero heights accepted; no
rounding up; `--nav-h` with the inset folded in (double count).

**Not measured in a browser.** No browser runs in this devbox: every
geometry statement in this section (the bar's position, the scroll room,
the safe-area handling, BP-XL's right inset) is derived from the CSS, not
measured, and needs a check on the Pixel (and a desktop browser at BP-L
and BP-XL) after deploy. Expected result: Pixel portrait, select two games,
scroll to the end: the last row ends about 14px above the bar, the bar
ends 14px above the nav's top edge, the nav is fully visible. With the
Suggestions card shown, the card scrolls fully above the bar and there is
no large gap between the grid and the card. At 1280px the bar sits 14px
above the window bottom, aligned with the grid's edges; at 1920px with
the Suggestions column, the bar stops at the column's left edge. Leaving
select mode returns the normal 32px end padding.

Suite: **1032 tests, 1032 pass, 0 fail** on the WEB-FIX-6 branch; **1087 tests, 1087 pass, 0 fail** after merging with WEB-FEAT-3.

### WP WEB-FEAT-4 — remove a PC from the PCs list

Each row of the PCs (agents) sheet gets a "Remove" button (accessible
name "Remove <pc>"). It opens an alertdialog on top of the sheet (the
detail sheet's `.dialog` markup, stacked through `lib/modal-stack.js`,
focus on "Keep") that names the PC, says what `DELETE
/v1/clients/{client_id}` (WP AG-1) deletes (the agent's reports and the
bypass status; cached games, downloads and cache statistics stay) and that
a still-running agent's next report lists the PC again (true: the report
route stores for any valid id, "not a ban"). Confirm sends one request
(`api.deleteClient`, the id percent-encoded as one segment); on 204 or
404 ("already gone") the row is dropped at once, the next clients tick is
forced to re-render the whole list, and the store is nudged; any other
failure keeps the row and shows the error inline on it (`role=alert`).
Buttons are `aria-disabled` with a click guard while the request runs.
An id containing `/` gets a note instead of the button: the frozen route
cannot address it (Starlette decodes `%2F` before routing; measured with
TestClient: 404 while the PC stays listed, every other printable character
tried round-trips). Demo mode removes the row in memory (204, then 404),
mirroring the server; it never comes back because no agent runs there.

Tests: `clients-remove.test.js` (11: URL encoding of space ? # % + &
non-ASCII as one segment with no query/fragment leak, 204 → null, 404/500
kinds, demo makes no request; `isRemovableClientId`; the route grammar
pinned against `routers/clients.py`; twin pins: the confirm wording against
the `DELETE FROM` tables of `agent_reports.delete_client` and the "not a
ban" premise against the report route; phone-width CSS; demo DELETE and
its decoding), `clients-remove-wiring.test.js` (10: fake-dom + real
store-singleton against a routing fetch fake: button and name, the "/"
note, dialog text/role/focus/inert, Keep and Escape send nothing and
restore focus, confirm → one DELETE → row gone → store refreshed, row gone
before the refresh answers, 404 as success, 500 inline, the in-flight
guard, a re-reported PC listed again on the next tick). The wiring file
ran 20x in a loop: 0 failures.

Mutation evidence (each alone, in a scratch copy, both files run): all 19
killed. No `encodeURIComponent`; the "/" rule dropped; 404 as an error; no
store refresh; no forced full render after a remove; no local row drop;
the error not painted inline; busy guard removed; Keep deleting; focus on
Remove instead of Keep; button unwired; no accessible name; no focus
restore on cancel; demo DELETE a no-op; demo without decoding; the server
deleting a third table (twin pin); the wording without "bypass status";
the error line without `role=alert`; the dialog-title wrap rule removed.
Review note: the re-report test passed vacuously at first (it saw the row
before the local drop); it now holds the refresh poll with a gate.

Android follow-up (next app package, not done here): the same action in
the app's clients sheet, with twin pins on the wording and the "/" rule.

Not covered: the painted result and a screen reader (no browser here).
Expected look: under each PC's stats a small "Remove" button at the right;
the dialog is the narrow centred confirm card with "Keep" and a red-outlined
"Remove"; a long PC name wraps in the title on a phone.

Suite: **1108 tests, 1108 pass, 0 fail**.

Review round 1 (PASS with fixes): only the handler's own 404 (detail
starting "Unknown client_id", `isClientAlreadyGone`, prefix twin-pinned
against `routers/clients.py`) counts as already gone; any other 404 (old
server, proxy) is an inline error. Keep/Escape return focus to the row's
LIVE Remove button when a poll rebuilt the list meanwhile. Escape is
ignored while the request runs. The alertdialog is `aria-describedby` its
two consequence paragraphs. The encoding note now says it was measured
against TestClient, not through a reverse proxy. Tests +7 (clients-remove
14, wiring 14). Mutations, all 8 killed: any 404 as gone; the sheet's 404
as error; focus to the captured node only; Escape unguarded; no
describedby; a paragraph id missing; server detail reworded (twin pin);
demo detail reworded. Wiring file 20x in a loop: 0 failures.

Suite: **1115 tests, 1115 pass, 0 fail**.

### WP WEB-FIX-8 — About without "unknown", and a Settings save bar

User feedback on the rc9 About table: "Unknown" reads as if the user did
something wrong. No API change (ADR-0016 freeze); `lib/about-view.js`
now decides the words from what the server already sends:

- A version or commit the component does not report is an em dash (with
  an sr-only "Not reported"); the server's generic `unknown` reads "Not
  checked"; the status-icon word for that kind follows.
- vault-core: "OK" when its recorded version AND commit equal vault-api's
  in the same answer (same release); a neutral "Check" when they differ or
  cannot be compared (a missing or `invalid` value on either side, so two
  "dev" builds never read OK on the version alone); "Not reported" when
  core never recorded one. A status other than `unknown` is never
  overridden. vault-dns `unknown` reads "N/A"; vault-proxy keeps OK with
  dashes.
- Every explanation (the note, a "dash means not reported" line where the
  note does not already say it, the server's detail) sits behind a per-row
  (i) button: a native `<button>` after the name with `aria-expanded`,
  `aria-controls` on the details row and an accessible name, collapsed by
  default; the open state survives a Refresh. CSS: a `[hidden]` guard on
  the details row (it is display-styled at every width), a 28px target,
  hover only for fine pointers.

Second user request in the same package: Save visible as soon as
something changed. The Save/Discard bar is now `position:fixed` above the
bottom nav with the bulk bar's own bottom formula (the two never share a
view), cleared of the rail from BP-L, as wide as the Settings column; it
stays in the DOM right after the form (Tab order unchanged). It shows while
the PATCH body would be non-empty (a value typed back hides it), the page
gets `.savebar-up` scroll room while it is up, Save is `aria-disabled` with
a click guard while in flight, a failure keeps the bar and puts "Could not
save: ..." in its `role=status` line, and after a save or discard that
removed a focused bar, focus lands on the page heading. Read-only settings
build no bar. Android mirrors both (twin pins below).

Tests: `about-view.test.js` (23: every display word/icon/tone, the core
comparison in all four outcomes, dns N/A, proxy and steamprefill dashes,
no visible "unknown" for any row state), `about-android-twin.test.js` (4:
strings.xml words and notes equal about-view.js, no app About string says
"unknown"), `settings-about-pcs-wiring.test.js` (19: rows per state, core
Check/Not reported in the DOM, default-collapsed render with no visible
"unknown", the disclosure a11y and toggle, the dash label),
`css-about-pcs.test.js` (+2: the `[hidden]` guard out-ranks both display
rules, the button target), `settings-save-bar.test.js` (9: dirty/revert,
region and order, save, failure, in-flight, discard, read-only, demo, the
Android words), `css-settings-save-bar.test.js` (6: fixed above the nav and
the inset, same bottom as `.bulk` and only one bar per view, `[hidden]`
guard, scroll room token, BP-L rail, theme tokens). `css-hygiene`'s sanity
pin now names `.savebar` (the hidden toggle moved off `.onbnav`).

Mutation evidence (each alone, the seven affected files run): all 24
killed — core OK on version only; null commits comparable; dns rule
dropped; "Not checked" back to "Unknown"; Check in the error tone; a null
version as "unknown"; the proxy dash note not suppressed; Not reported as
Check; details open by default; `aria-controls` dropped; the toggle not
flipping `hidden`; no sr label on a dash; the details `[hidden]` guard
dropped; dirty as "touched"; the bar sticky instead of fixed; the bar
ignoring the nav; the `.savebar[hidden]` guard dropped; no scroll room;
a failure hiding the bar; no in-flight guard; no focus restore; the bar
appended after About; an Android note and an Android status word drifting.
The two wiring files ran 15x in a loop: 0 failures.

Not covered: the painted result, a screen reader and real phones (no
browser here); `--savebar-h` (104px) is an estimate like `--bulk-h`.
Expected look: About rows show the name with a small outlined (i) after
it, dashes in empty cells, and the details paragraph under the row once
opened; the save bar floats 14px above the bottom nav (14px above the
viewport bottom right of the rail on desktop) with "Unsaved changes" over
"Discard changes | Save changes".

Suite: **1149 tests, 1149 pass, 0 fail**.

Review round 1 (FAIL, one blocker): a server `unknown` on vault-proxy,
vault-runner or steamprefill (or a probe past its deadline) means vault-api
DID look and got a bad or unclear answer (`api/vault_api/about.py`: e.g. the
proxy forwarded a host it must refuse). "Not checked" said the opposite and
hid the fault. It now reads a neutral "Check" (the "Not checked" state is
gone; an unrecognised word is Check too), and a Check row's (i) details
start OPEN, so the server's reason is on screen; OK/N/A/Not reported/Not in
use rows start collapsed, and a user's choice still wins across Refresh.
The status-icon word for the "?" kind is "Check", and STATUS_LABEL is now
pinned literally. Also taken: the row header is `aria-labelledby` the name
span (the (i) label never joins it); a root `scroll-padding-bottom` (bar +
gaps + nav + inset) while the bar is up, so a focused field is not hidden
behind it (WCAG 2.4.11); an edit typed while a save is in flight survives
it (drafts that are no longer the sent objects are kept, the form is not
rebuilt, the bar stays up) and the line keeps "Saving…" while in flight;
the PCs sheet says "Not reported" / "last seen: not reported" / "version
not reported" / "game count not reported" instead of "unknown", twin-pinned
against strings.xml. Android mirrors all of it (CHECK with details open,
"Saving…", in-flight edits kept, save error cleared on edit and Discard,
with a controller test on the demo repositories).

Tests +9: about-view (+2: Check rows open with the server detail, the
STATUS_LABEL pin), wiring (+2: the proxy fault visible without a click,
aria-labelledby; the default-render test now expects exactly the Check
rows open), save bar (+3: focus to the heading after Discard, an edit
after a failure resets the line, an in-flight edit survives), CSS (+1:
scroll padding), twin (+1: PCs words). Mutations, all 14 killed: `unknown`
mapped elsewhere; Check rows collapsed; every row open; the DOM ignoring
the default; the icon word back to "Unknown"; no aria-labelledby; in-flight
edits dropped; "Saving…" overwritten by typing; no focus after Discard; the
error line sticking after an edit; the scroll padding dropped; the presence
word back to "Presence unknown"; Android last-seen and "Saving…" drifting.
The two wiring files ran 10x in a loop: 0 failures.

Expected look, added: on an Android phone in Chrome the soft keyboard
resizes only the visual viewport (no `interactive-widget` in the viewport
meta), so while a field is being typed in, the fixed save bar sits behind
the keyboard; it shows again as soon as the keyboard closes. The Android
app's bar is a Scaffold bottom bar and its keyboard behaviour depends on
the activity's soft-input mode; both are unverified on a device.

Suite: **1158 tests, 1158 pass, 0 fail**.
### WP PAIR-1 — "Add a device" (QR for the app, browser link, agent command)

User decision 2026-10-04, "Weg A": a new device is set up from this
already-connected web UI with the ONE shared vault API key, no API change
(ADR-0016 freeze). Per-device keys and short-lived codes are D9 PAIR-2.

Settings → PCs (agents) → "Add a device" opens a drawer sheet
(`components/add-device-sheet.js`) with three options. Each shows its
content only after its own "Show" press, under the warning "Anyone who
sees this can control your hangar. Only show it on your own screen.";
"Hide" and every close path (Close, Escape, backdrop, navigation — via the
new optional `onClose` of `sheet-dialog.js`) remove the content from the
DOM. Demo mode or no stored key: a note, no option.

- **Phone (Android app)**: QR code of
  `steamhangar://pair?v=1&url=<origin>&key=<key>` (`lib/pair-link.js`,
  the contract with the Android package APP-PAIR-1: both values through
  `encodeURIComponent`, so `+` is `%2B`; `url` reduced to
  `scheme://host[:port]`, no trailing slash; the base is the page origin,
  as `api.js` uses it). Inline SVG, black modules on a white plate with a
  4-module quiet zone in every theme. The same URI as an "Open on this
  phone" link and as copyable text (some camera apps only show the text of
  a custom-scheme QR).
- **Another browser**: `<origin>/#pair=<key>`. The receiving page
  (`lib/pair-intake.js`, wired at the top of `app.js`) strips the fragment
  with `history.replaceState` before the first render, then: same key →
  toast; a different stored key → alertdialog "Replace this browser's API
  key?" (`components/pair-confirm.js`, focus on "Keep current key");
  otherwise (or after "Replace") `checkVaultApiKey` (the onboarding step-1
  check) and only a passing key is stored, the onboarding way
  (`setStoredApiKey` + `setDemoMode(false)`), followed by a reload; the
  "Paired." toast crosses the reload as a sessionStorage flag ("1", never
  the key). A rejected or unchecked key is never stored. While a link is
  handled, the first-run overlay waits and auth-recovery does not fire.
- **Windows PC (vault-agent)**: a PowerShell 5.1 command
  (`lib/agent-install.js`) for the vault-api version from `GET /v1/about`
  (release asset names as publish.yml writes them, tag = "v" + version). A
  build without a release (dev-<sha>, a native run without a commit,
  "invalid") gets a note instead. The command (since round 1, "Weg A",
  it contains NO key): ask for the key with `Read-Host -AsSecureString`,
  download exe + three scripts + SHA256SUMS into
  `%LOCALAPPDATA%\VaultAgent\release-v<version>`, `Get-FileHash` check of
  all four (stop before installing on a mismatch), `Unblock-File`, exe to
  the versioned `%LOCALAPPDATA%\VaultAgent\vault-agent-v<version>.exe`
  (review round 1), key to a temp file locked
  with icacls before it is written, `install-task.ps1 -AgentPath -ServerUrl
  -ApiKeyFile` in a child `powershell.exe -ExecutionPolicy Bypass`, temp
  file deleted in `finally`, `Start-ScheduledTask VaultAgentReport`. The key
  reaches the PC only through a separate "Copy key" button. **Agent server address**: an editable
  field, prefilled with the page origin, validated (http/https + host,
  nothing else), remembered in localStorage (`steamvault.agentServerUrl`,
  not a secret). The note under it: the agent must reach vault-api's direct
  LAN address, not a reverse proxy — vault-api records the TCP peer of
  each report (uvicorn `--no-proxy-headers`) and matches it with cache
  traffic; through a proxy every PC gets the proxy's address (found on the
  real install, 2026-10-04). A Linux/SteamOS line points to agent/README.md.

Copy buttons use the async clipboard where it exists (secure contexts
only) and fall back to selecting the text field plus `execCommand("copy")`
on a plain-http LAN page; a failure says "copy it by hand".

**QR encoder: written for this project, not vendored**
(`lib/qr-encode.js`, byte mode, levels L/M/Q/H, versions 1-40). The web
UI has no build step and its CSP is `script-src 'self'`; the well-known
single-file JS libraries are UMD/CommonJS, and only one byte segment is
needed. Verified during the WP against segno 1.6.6 (BSD-3, Python, run once
on the developer machine, not a dependency): all 40 versions x 4 levels x
2 lengths with forced masks (320 cases) give identical matrices, after
patching one segno quirk (it appends a whole zero byte when the terminator
already ends on a byte boundary; ISO/IEC 18004 7.4.10 does not). Automatic
mask choice differs from segno in 7 of 16 cases (the two implement the N3
penalty differently; any mask decodes). The generated symbols were decoded
back with jsQR 1.4.0: 334 of 336 (the two misses were version 23 at level
L, where jsQR also fails on segno's output). `qr-reference-fixtures.js`
keeps five segno matrices (v1-M, v1-L, v5-H, v6-M exactly full, v8-M with
version bits).

Tests: `qr-encode.test.js` (15: five reference matrices, Annex C/D/E and
Table 7 spot values, structure, mask determinism, UTF-8, overflow throws,
SVG geometry), `pair-link.test.js` (10: the URI contract as literals, the
encoding, origin-only url, fragment round trip and refusals, the intake
decision), `pair-intake.test.js` (13: strip, every intake path with
recording fakes, the notice flag, two app.js source pins),
`agent-install.test.js` (10: release guard, asset names against
publish.yml, hash check before install, install-task.ps1 parameters
against the real script, key once and quoted, PS 5.1 operators, URL
validation, the note), `add-device-wiring.test.js` (11: fake-dom + real
settings view and store: the Add button, nothing secret in the DOM before
Show / after Hide / after each close path, QR/link/text, Windows command,
dev-build note, /v1/about error, the address field, demo and no-key notes,
the confirm dialog). `auth-recovery.test.js`'s app.js pin now expects the
`|| pairIntakeBusy` gate. The wiring file ran 20x in a loop: 0 failures.

Mutation evidence (each alone, in a scratch copy): all 30 killed — key
not encoded; url not reduced to the origin; a different key replaced
without asking; the browser link in the query; no `replaceState`; the
confirm ignored; key stored before the check; first-run overlay not gated;
fragment not read at top level; release guard without the commit; quotes
not doubled; no hash check; temp key not deleted; history not switched
off; a URL path accepted; an ECC table entry; the format XOR mask; the pad
byte; overflow not thrown; Hide keeping the DOM; no `onClose`; `onClose`
not called by sheet-dialog; agent address not saved; demo gate removed;
prefill not the origin; a dev build getting a command; the Settings row
removed; Escape answering "Replace"; focus on "Replace"; auth-recovery not
gated.

Not covered here: the painted result, a real scan with a phone, a real
paste into Windows PowerShell 5.1 (no PowerShell available to this WP; the
command is checked by the structural tests above, not by a parser or a
run), and a screen reader.

Suite: **1174 tests, 1174 pass, 0 fail**.

Review round 1 (FAIL on one blocker, the history file; its fix waits for
a user decision, the key source is isolated in `keySourceLines` so either
variant is a one-place change). Fixed in this round:
- Late `/v1/about` answers: both guards in `add-device-sheet.js` pinned
  with a gated fetch (held requests answered by the test): an answer after
  Hide paints nothing (guard A, `windowsOption.shown`); an answer for an
  earlier Show is dropped while the current one is pending (guard B,
  `windows.gen`); an answer after the sheet closed leaves no secret.
- Wording: `replaceState` cleans the address bar and this tab's history
  entry only, NOT the browser's persistent history or address-bar
  suggestions (which may be synced). The browser option now tells the user
  to delete the link there and wherever it was sent.
- The exe goes to `vault-agent-v<version>.exe`: a re-install of another
  version never overwrites an exe the task may be running (install-task.ps1
  re-points the task through `-AgentPath`); a same-version re-install skips
  the copy when the file is byte-identical. Older versioned exes are left
  in `%LOCALAPPDATA%\VaultAgent`; delete them by hand if wanted.
- After a successful install `uninstall-task.ps1` is copied next to the
  exe and the download folder is removed; on a failure it stays for
  inspection.
- Logging residuals, documented in `lib/agent-install.js` (superseded by
  finding 1 below: the command no longer carries the key, so history,
  transcription and event 4104 hold no key).
- The warning adds: copied text can also end up in clipboard history or
  cloud clipboard sync.
- Phone: the Android app's key rule is mirrored (`isAppPairableKey`:
  printable ASCII, no space at either end); another key gets a note
  instead of a QR code.
- `psQuote` pins all four quote characters (U+2018..U+201B).
- The generated command with a dummy key is committed as
  `fixtures/windows-install-command.ps1` (pure ASCII). `agent-install.test.js`
  regenerates it and fails on drift (`UPDATE_PS_FIXTURE=1` rewrites it), and
  CI's powershell-syntax job parses it with Windows PowerShell 5.1's parser
  (added to the parse-only list of `.github/scripts/verify-ps-parse.ps1`,
  never executed).
- Three node-vs-null assertions in the wiring file now compare booleans
  (a failing one dumped the fake-DOM graph and crashed the file instead of
  failing a test, measured by a mutation).

Mutations, each alone in a scratch copy, all 12 killed: guard A removed;
guard B removed; phone key rule gone; app key rule allowing edge spaces;
fixed exe path; always copying the exe; download folder kept; uninstaller
not kept; `psQuote` missing U+201A/B; fixture drift; CI not parsing the
fixture; warning without the clipboard sentence.

Suite: **1182 tests, 1182 pass, 0 fail**.

Finding 1 (blocker, user decision 2026-10-04: **Weg A**). PSReadLine 2.0
takes a pasted block as ONE history item and writes it to
ConsoleHost_history.txt before any line in it runs, so the earlier
"history off" first line could not protect a key inside the block. Now the
command contains no key at all:
- It asks `Read-Host 'Hangar API key (paste it, then press Enter)'
  -AsSecureString` (asterisks only), converts with `SecureStringToBSTR` +
  `PtrToStringBSTR`, `ZeroFreeBSTR` in a `finally`, disposes the
  SecureString, and refuses an empty or non-printable-ASCII answer before
  any download. The rest is unchanged: icacls-locked temp file (ACL before
  content), `-ApiKeyFile`, temp file deleted and `$apiKey` cleared in the
  `finally`. The history-off line is gone (nothing secret to keep out).
- The sheet's Windows option, behind the same Show gate and warning, has a
  "Copy key" button next to "Copy command". It reads the stored key at
  click time and never puts it in the DOM; the plain-http fallback uses a
  temporary off-screen textarea removed in a `finally`. A stored key the
  command would refuse (not printable ASCII, `isInstallableKey`) gets a
  note instead of the command.
- What is guaranteed: the key is never part of the pasted text, so it is
  not in the PSReadLine history, a transcript of the command or a 4104
  script-block record of it. Residuals, stated on screen and in the module
  header: the clipboard copy (and clipboard history or cloud clipboard
  sync where on) until something else is copied; the plain string in the
  PowerShell process while it runs; the owner-only temp file for the
  seconds install-task.ps1 needs, then install-task.ps1's own owner-only
  env.txt.
- The fixture now holds no key; a test pins that (no `$apiKey = '`
  literal, the Read-Host line present) and CI still parses it with 5.1.

Tests: `agent-install.test.js` 15 (was 13; no key in any form even when one is
passed; SecureString prompt, BSTR freed in a finally, check before any
download, `$apiKey` cleared; `isInstallableKey` and the command's regex are
one rule), `add-device-wiring.test.js` 17 (no key in the Windows option;
Copy key only after Show, copies exactly the stored key via the async
clipboard and via the fallback, the temporary textarea gone afterwards,
gone with Hide; an uninstallable key gets the note).

Mutations, each alone in a scratch copy, all killed: key literal back in
the command; no `-AsSecureString`; `ZeroFreeBSTR` outside a finally; the
key check after the downloads; `$apiKey` not cleared; Copy key copying
something else; the temporary textarea not removed; Copy key outside the
Show gate (built with the sheet); the uninstallable-key note removed.

Suite: **1186 tests, 1186 pass, 0 fail**.

Paste behaviour (coordinator must-fix): a console that types a multi-line
paste line by line (Windows Terminal, conhost right-click) runs every
complete top-level statement as it arrives, so a top-level `Read-Host`
would have taken the next pasted line as the key. The whole command is now
ONE statement, `& { ... }` from its first to its last non-empty line (the
comment lines moved inside); PowerShell reads continuation lines up to the
closing brace before running anything, so the prompt appears only after
the whole paste is in. Nothing relies on top-level scope. The on-screen
steps say: paste the whole command, then, at the prompt, press Copy key
and paste the key. `agent-install.test.js` checks it at parse level (first
and last non-empty lines, brace depth never back to 0 in between, string
literals and comments ignored); the wiring test pins the step text; the
CI-parsed fixture is regenerated. Mutations, both killed: a comment line
back above `& {`; the prompt moved in front of the block.

Suite: **1187 tests, 1187 pass, 0 fail**.

### WEB-FIX-10 — PAIR-1 review nits (Windows install command)

- The kit-folder cleanup is `try { Remove-Item ... -ErrorAction Stop }
  catch { Write-Warning ... }` (the block runs with
  `$ErrorActionPreference = 'Stop'`), so a locked or vanished folder only
  warns and `Start-ScheduledTask` still runs; it follows directly.
- Key edge whitespace matches install-task.ps1 (`.Trim()` of the
  -ApiKeyFile contents, inner spaces kept): the command trims the pasted
  key before its check, `installKey` trims for the sheet, "Copy key"
  copies the trimmed key, and `isInstallableKey` checks the trimmed key
  (a whitespace-only key is now refused). A test pins install-task.ps1's
  trim line.
- Step 2 adds "(press Enter once more if nothing happens)".
- agent/README.md "Windows Scheduled Task" points to the in-app flow; the
  sheet already points to agent/README.md.

Tests: `agent-install.test.js` +2 (guarded cleanup right before the task
start; trim rule and install-task.ps1's trim line), the key-rule cases
extended; `add-device-wiring.test.js` +1 (Copy key trims) and the step
text. Fixture regenerated (still ASCII). Mutations, each alone, all 5
killed: cleanup without `-ErrorAction Stop`; no trim in the command; Copy
key untrimmed; step sentence removed; `isInstallableKey` without trim.

Suite: **1293 tests, 1293 pass, 0 fail**.

### WP WEB-FEAT-6 — the Updating and Verifying states

User decision 2026-10-09: show them. The API has no `verify` job status and
no live phase field, so `lib/game-status.js`'s `liveRunKind` derives the
kind of a RUNNING prefill job from the app's games row: `last_prefill_at`
null -> `running` ("Downloading"); set with `needs_force` false ->
`updating`; set with `needs_force` true -> `verify` ("Verifying"). Never
from `size_bytes` (it grows during a first fill). `dispKind` uses it, so the
library card and the detail-sheet header follow; `lib/job-partition.js`'s
`activeJobKind`/`activeJobWord` give the Downloads Active card the same
kind and word, and the Downloads games subscription rebuilds a job card
whose kind changed (first games answer after the first jobs paint, or a
`needs_force` flip) — an unchanged games poll stays a name-only patch.

Tests: `update-verify.test.js` 20 (mapping, bytes ignored, paused/GC/queued
boundaries, pause action kept, filters, the Downloads helpers and their
words against STATUS_LABEL, the library card DOM — kind, word, colour
class, both icons, the turning-arrows group — CSS colours, and the
detail-sheet wiring by source scan), `downloads-update-verify.test.js` 2
(gated games endpoint: Downloading -> Updating -> Verifying on the real
store poll, marker attribute survives an unchanged poll; paused stays
Paused). `game-card-installed.test.js`: the "running download, not cached"
fixture now has `last_prefill_at: null` (it is a first fill; with a
completed copy the same job now reads "Updating").

Mutations, each alone, all killed: the games subscription never rebuilds
(times out at "Downloading"); it always rebuilds (marker lost); the
Downloads word back to `jobStatusWord`; `dispKind` back to plain
`running` (4 fail); `liveRunKind` ignoring `needs_force` (6 fail).

Suite: **1312 tests, 1312 pass, 0 fail**.

#### Honest list

- No browser run: the turning icon on a real Updating card was not looked
  at, only its DOM and CSS.
- The detail-sheet header is pinned by source scan, not by rendering the
  sheet (its builder reads module state).
- Known drift, by design of the API: a shared-depot or remnant delete of
  ANOTHER app can set this app's `needs_force` mid-run, so a run that
  started non-forced flips from "Updating" to "Verifying".
- A resumed run shows its normal kind; the mockup's short "verifying
  cached chunks" phase after a resume has no API signal.
