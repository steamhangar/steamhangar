# Learnings: Web UI

Scope: web/ views, kit.css, fake-dom and node tests, demo data, goldens of the web, a11y. Part of the learnings index
[`docs/LEARNINGS.md`](../LEARNINGS.md); entries moved verbatim by
DOC-DIET-1 (rc22), original section headings kept. Append new findings
at the end, under a dated heading, one entry each with the WP that proved it.

## Web UI (Phase 4a)
- A Starlette `Mount("/")` catch-all pre-empts the router's partial-match
  bookkeeping app-wide: every trailing-slash 307 became a 404 and
  HEAD-on-real-route 405s became 404s. Serve a SPA with exact GET+HEAD
  routes plus narrow asset-subtree mounts (/css, /js) instead — then API
  routing is untouched by construction (WP 4a.1 blocker).
- Routing changes are verified by running the SAME request matrix against
  a pristine pre-change baseline (git archive HEAD), with the feature both
  enabled and disabled — README claims about "unchanged behavior" were
  measurably false until the rig existed (WP 4a.1).
- httpx/TestClient normalizes `../` client-side before sending: un-encoded
  traversal tests assert on the wrong path. Real traversal pins need
  %2e%2e-encoded forms or a raw ASGI scope with un-normalized raw_path
  (WP 4a.1 review).
- The frozen mockup is binding for UI surfaces: scaffolding must not add
  navigation items or views the mockup lacks — any divergence is a user
  decision, recorded like the paused-slot one (WP 4a.1 blocker). Example:
  Clients started as a sheet, not a nav entry; the user's 2026-10-13
  decision made it its own nav page (D15c).
- Security-constant pins must assert STRING LITERALS, not the module's own
  constants: a test comparing the built URL against STEAM_API_BASE stays
  green when STEAM_API_BASE itself is mutated to attacker.example or http.
  Capture the real outbound Request object and urlsplit it against literal
  scheme/host/path (WP 4a.6r blocker).
- An outbound-HTTP module's headline guarantee (host pin, HTTPS) needs its
  own named mutation kill — 83 passing tests proved everything except where
  the secret is actually sent (WP 4a.6r).
- An async poll tick without an in-flight guard forks the loop: while the
  fetch awaits, the stored timer id is already dead, so any nudge
  (visibilitychange, manual refresh) clears nothing and starts a second
  self-re-arming chain — compounding poll rate and duplicating every
  diffed notification. Coalesce nudges into a pendingRefresh flag plus a
  generation token; a ~40-line bare-Node harness (fake document object
  injected before import, gated fake fetcher) tests this glue headlessly —
  "timer glue can't be tested without a browser" was false (WP 4a.2
  blocker).
- Demo-mode fixtures are a shipped surface: they must demonstrate the
  product's invariants (ADR-0003 shared-depot protection), not violate
  them, and a "shapes match the real API 1:1" claim is verified endpoint
  by endpoint against the Pydantic models at git HEAD (WP 4a.2 blocker).
- Before exposing a DB column in the API, verify its actual WRITE matrix
  in code — the plan claimed last_manifest_check was "written on every
  run"; the shipped rule stamps it only on confirmed-current runs
  (done + Updated==0 + UpToDate>0), it survives cache deletion unlike
  last_prefill_at, and the UI wording must say "confirmed current at X",
  not "checked at X" (last_manifest_check mini-WP).
- A documented mechanism with zero callers is the WP 4a.1 failure class in
  module-header form: the card docstring described patch-in-place while
  every path full-rendered and cardStructuralKey had no caller. Grep for
  callers of any mechanism a header claims before believing it (WP 4a.3
  blocker).
- Proving "no animated node touched" is instrumentable headless: stub the
  card, record every querySelector/mutation, and wire createElementNS to
  THROW — a patch path that would rebuild an icon then fails loudly
  instead of passing on trust (WP 4a.3 review technique).
- A mounted()/isConnected gate on a freshly BUILT section no-ops the first
  paint: render() runs synchronously before app.js attaches the node, so
  isConnected is false at exactly that moment. Nulling sectionEl in the
  view-change listener is the complete staleness signal on its own — and
  with a per-card patch planner the bug stops self-healing (re-navigation
  leaves an empty grid until real data changes) (WP 4a.5; twin bug in
  library.js fixed separately).
- Status-icon kinds follow the SHIPPED status set, not the mockup's: a
  terminal 'cancelled' needs its own neutral glyph and sr word — reusing
  the error glyph would misreport an operator action as a failure
  (WP 4a.5, same class as the Failed-chip divergence).

## 2026-08-22 — the defaults-flip wave (SWEEP-1, APP-DEMO, 4d-web, AG-0, CI-3/AGENT-BIN)
- A UI sentence built from ONE API field over-claims unless that field
  alone carries the meaning (WP 4d-web, both round-1 blockers).
  last_sweep_targets=null has three server-documented meanings (never
  ran / in flight / died mid-run — claim stamps the timestamp and NULLs
  counters in one statement), so rendering it as "has not completed a
  run yet" told a crashed vault's operator to debug the wrong thing
  forever. sweep_cached_gc_risk is a pure configuration predicate, so
  "cached games are being refreshed" asserted activity it cannot know.
  Convergent proof: SWEEP-1 independently added "not a promise a sweep
  is happening" to the same field's server docstring the same day.
  Write state-only copy; when a second field separates the cases (here
  last_sweep_at), branch on it.

## 2026-09-30/10-01 — pre-freeze review (the *-FIX-1/2 packages, TH-0b)
- In fake-dom tests, assert on TEXT, never on nodes: a failing node
  assertion makes `node:assert` dump the whole fake-DOM graph, and the
  test process was OOM-killed (SIGKILL) instead of reporting a failure
  (WP WEB-FIX-1).
- A status line that rebuilds its children on every state change drops
  keyboard focus from its own button and re-announces the whole line.
  Build the nodes once per mount, put `role=status` on a text-only span
  that is never `hidden`, and mark a busy button `aria-disabled` plus a
  click guard instead of `disabled` (WP WEB-FEAT-1).
- An empty relay result is not a count: a private Steam profile returns
  0 games, so "0 owned" is false. Fall back to the vault wording and
  explain in the notice (WP WEB-FEAT-1).
- A page reload in a finish handler aborts any save still in flight.
  Keep the in-flight promise (with a never-reject catch that logs), await
  it in finish behind a synchronous re-entry guard, and show the wait on
  the button (WP WEB-FEAT-2).
- Pin race guarantees with a gated fake fetch: hold the response, act
  (double click, edit the input, press finish), then release. Fixed
  sleeps cannot show the order (WP WEB-FEAT-2).
- A demo fixture that is nicer than the real API hides real bugs: demo
  enqueue copied the owned title onto the new vault row, while the real
  `POST /v1/prefill` inserts the app with name NULL, so "App N" only
  showed on the live server. Demo shortcuts must mirror the API's actual
  data shape (WP WEB-FIX-4).
- Reading `window.localStorage` itself throws (SecurityError) when site
  data is blocked, not only `getItem`. A bare read in an argument list at
  module top level blanks the whole app; read it through a guarded
  getter (WP WEB-FIX-5).
- Chrome's "Desktop site" mode on a phone ignores the viewport meta and
  lays out at ~980 CSS px; it looks like a broken mobile layout. Detect
  it as coarse pointer + short screen side < 600 + layout > 900 and
  >= 1.5x the screen width, and hint instead of auto-zooming (WP
  WEB-FIX-5).

## 2026-10-02/03 — upstream keepalive pool (ADR-0017, WP CORE-FEAT-1a..1d)
- A clipped looping animation is seamless only if the leaving element
  has no ink inside the clip at the end frame: the period must cover the
  visible travel (disc radius plus glyph extent), not just the glyph
  box. Test the end frame's visibility, not only the transform numbers
  (WP WEB-FIX-7).
- With a valid key an unknown /v1 route answers 404 (pre-auth guard), so
  only a 404 means "server too old"; a 401 is always a bad or rotated
  key. A UI that reads 401 as "update the server" sends the operator to
  the wrong fix (WP WEB-FEAT-3).

## 2026-10-09/10 — rc13 night run (CORE-FIX-4d, WEB-FEAT-6, DEPS-1, HYG-1, WEB-FIX-10, AGENT-FIX-3)
- Without an API phase field, "Updating" and "Verifying" derive from
  `last_prefill_at` and `needs_force` of a running prefill's app; queued
  jobs must stay out of any header that shows a live kind (WP WEB-FEAT-6).

## 2026-10-14/15 — rc18 night run (UPD-FIX-1, D16a-c, DIST-1a/b)
- See also [android.md](android.md), this section: "The Android twin and pin tests (source scans of the web copy and ..."

## 2026-10-16 - rc19.3 throughput tuning (TUNE-1a/1b)
- **Nesting a view's blocks into wrapper columns breaks tests that read
  `section.children` (DOWNLOADS-1, rc20).** Order pins must read the
  wrapper's children (`.dl-col-main`), not the section's. A busy or
  disable loop over "every button in a box" must also skip toggle buttons
  added to that box (no `data-role` of the row actions), or the fold
  toggle goes aria-disabled while a request is out. When the full run
  reports a bare SIGKILL for one file, run it alone with
  `node --test web/tests/<file>`: the per-test output names the hanging test.
- See also [android.md](android.md), this section: "**Android twin pins break on template edits, not only on renames.** ..."

## 2026-10-17 — rc20 rework (UI-KIT-1, WEB-GOLDENS-1, TOKENS-SYNC-1, CLIENTS-4, ADD-DEVICE-2, DOWNLOADS-2, SETTINGS-2, LIBRARY-2)
- **Structure is not anatomy.** The first rc20 pass shipped every mockup's
  columns, tabs, nav and split buttons and still looked "old" on every
  page, because titles, tiles, pills, labels and segs were re-styled per
  page from the old primitives. A visual package is only done when its
  rendered screenshot matches the reference; DOM tests prove wiring, never
  looks. Pages now compose from `web/css/kit.css` (ADR-0028), and the
  kit's own reference (`docs/design/rc20/kit.html`) uses the same values
  as the real CSS: change one, change both.
- **Render before review, on this box.** Headless Chromium is in
  `~/.cache/ms-playwright/chromium-*`; `tools/web-shot.mjs` drives it over
  CDP in demo mode (`steamvault.demoMode=1`). The devbox fontconfig maps
  `sans-serif` to Liberation Mono, so always set
  `FONTCONFIG_FILE=tools/web-shot-fonts.conf`, or every page renders in a
  monospace font and looks wrong. Coders attach 1400/1000/400 shots,
  reviewers look at them. Kill stray `chrome-linux64` processes after an
  aborted run; dozens piled up and blocked later screenshots.
- **Moving or rewording a web string can turn main red through Android.**
  Kotlin `*WebTwinTest` files read web files by path and pin literals.
  DOWNLOADS-2 moved the statistics literals from `views/downloads.js` into
  `components/downloads-hero.js` and changed "highest" to "peak"; the web
  suite and the web review stayed green, the Android job failed on main.
  Every web package that moves code between files or touches wording greps
  `app/app/src/test` for its files and checks each pinned literal before
  merge. Mockup copy is never a reason to change shipped wording.
- **Kit rules are opt-in by scope.** Every kit.css selector starts with
  `.kit`, so a page migrates by adding `kit` to its root and legacy pages
  are untouched; the cost is that an old app.css rule with the same class
  name (`.chip`, `.btn:hover`) can still leak in by load order. Remove the
  old rule when the last legacy user migrates instead of adding overrides.
  `css-hygiene.test.js` reads kit.css too, so moved rules keep their
  `[hidden]` guards.
- **Appending blocks to one shared CSS file conflicts on every parallel
  merge.** Four packages appended to the end of kit.css; each merge after
  the first conflicted at the same spot and needed "keep both, mine last".
  Cheap to resolve, but plan it: merge in a fixed order and rebase the
  next branch right after each merge.

## 2026-10-31 — rc21 (GRAPH-RECENT-1, LOADING-NOW-1)
- **Optional split fields need a "field missing" fallback and a test that
  strips them** and expects the old output (older server, Android twin).
- **Async UI flows need a per-flow token.** A dialog flow guarded only by
  its state kind ("CHECKING") accepts a late answer of an earlier, closed
  flow for another game; tie every answer to the flow that sent it
  (CACHE-REPAIR-UI-1 review).
- **A local aa-tolerant golden pass does not prove goldens unchanged.**
  To prove 0 px, `git archive` the base into the scratchpad, serve both
  with `tools/web-serve.mjs` on two ports, shoot both and compare with
  `png-diff --mode strict`.
- **Tests importing `store-singleton.js` with a stored key need
  `after(() => store.stop())`**, else the node test process never exits.
- **A hidden block inside a hero grid must span an existing grid cell**
  (`grid-column:1/-1` inside the cell); a new grid row adds a gap even when
  hidden and changes goldens.
- **A new visible block needs demo data that shows it**, or no golden ever
  renders it; the demo must show the product's invariants (pill
  "Downloading" implies a source row).
- **"timed out waiting for X" followed by a null in the next test is
  usually state left behind, not the clock.** Look for module state that
  only clears on an event arriving while the view is mounted (Clients
  `removedIds`); end the test on that event instead of raising timeouts.
- **A demo item exactly on a range boundary makes a second `Date.now()`
  a race**: freeze only Date (`mock.timers.enable({apis:["Date"]})`) and
  reseed under the frozen clock.
- **A `[hidden]` guard must also win**: a state class (`.selecting`,
  `.compact`) or a page-scoped kit rule that sets `display`, also inside
  @media, needs its own `<sel>[hidden]` guard (Lint 3 checks this).

## 2026-11-01 — rc21.1 (prod findings after the rc21 rollout)
- **When a web pin literal's meaning changes, add new literals** and keep
  the old one with a "kept for twin pin" comment and a web test, so the
  Android twin stays green until its follow-up.
- **An ETA's remainder and rate must be the same byte kind**: chunks still
  to request / total runner speed for a first download, internet bytes
  still to fetch / runner MISS speed for an update. A swept 5-10 min
  window that starts before the job dilutes the rate; use the live tier's
  runner speed for the running job.
- **Every additive per-job API field needs a demo value in the same
  package** (`demo-data-traffic.test.js` key parity); null/false keeps
  goldens unchanged.
- **Key an EMA "new sample" on the identity of the data list, not on the
  merged answer object**: the live range re-merges the same cached jobs
  every 3 s, so keying on the answer stepped the smoother ~10x per real
  sample (WEB-ETA-EMA-1; Android had it right from the start).

### 2026-11-01 ADD-DEVICE-QR-1

- `css-mobile-1b.test.js` reads only the FIRST `@media (pointer:coarse)`
  block of kit.css and finds the narrow-touch `@container addev` block by
  position: new touch rules go inside the existing coarse block; a second
  coarse block breaks two existing tests.
- `fake-dom.js` has no `prepend`, and a button's `type` is a plain
  property: build children in order with append, assert `el.type`.
- Never overlay a control on a QR code: the corners hold the finder
  patterns and the quiet zone is only ~16-20 px at 164 px. Put the control
  beside the code.

### 2026-11-01 NITS-WEB-1

- Android twin pins can freeze a whole web call line (CacheRepairWebTwinTest
  pins the repair `request("POST", ...)` text in api.js). To add an option
  without breaking it, route the call through a wrapper whose method is
  also named `request` (`untimed.request(...)`).
- A test wait must check for this test's own answer (text or count only
  that answer produces), not a state an earlier test may have left; one
  extra microtask in api.js made two such tests fail every time.
- `mock.timers.enable({apis:['setTimeout']})` plus a fetch fake that rejects
  on `signal` abort tests 20 s / 5 min client deadlines in milliseconds; a
  fake that ignores the signal shows a call has no timeout.
- Grep the whole repo for a symbol before assigning an audit item to a
  platform: `isVaultReturnTo` exists only in Android.
- `node --test web/tests` (bare directory) fails on Node 22; use
  `node --test "web/tests/*.test.js"`.
