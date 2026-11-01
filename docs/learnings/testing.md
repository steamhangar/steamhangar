# Learnings: Testing discipline

Scope: cross-cutting test technique: mutation checks, pins and their generations, flakes, fixtures, twin pins. Part of the learnings index
[`docs/LEARNINGS.md`](../LEARNINGS.md); entries moved verbatim by
DOC-DIET-1 (rc22), original section headings kept. Append new findings
at the end, under a dated heading, one entry each with the WP that proved it.

## Testing discipline
- Fail-closed defaults need tests that pin the DEFAULT direction: flip each
  "unknown ⇒ protected" branch and watch a test die — two such flips passed
  the entire 434-test suite unnoticed in review (WP 3.6 Opus).
- A protection rule keyed on data that deletion deliberately preserves
  (mapping rows, ADR-0003) leaks resources forever unless it has an explicit
  last-remnant escape — audit every "skip if referenced" guard for the
  all-referrers-gone end state (WP 3.6).
- Flake-hunt concurrency tests: run the module isolated in a 20-40x loop —
  full-suite green means nothing for timing bugs (WP 1.6 Fable).
- Enumerate the package's GUARANTEES and mutation-test each one by name —
  six killed mutations mean nothing if the seventh (the primary
  fail-closed promise) was never targeted (WP 2.3).
- Mutation-test regression tests (revert the fix, watch the test fail)
  before trusting them (multiple WPs).
- Fixtures: synthetic only, modeled on real structure — never personal data
  (WP 2.1).
- Verify empirically over believing docs or reviewers — several review
  claims were corrected by rigs (WP 1.1 301/302, WP 1.6 rmtree).
- nginx's event log is written with `buffer=64k flush=5s`, so a
  "request it, then grep the log" test is **never** raceless: measured
  0 lines at t≈0 s and t≈2 s, the correct 9-field line at t≈7 s. Any such
  check needs a bounded wait-for-line loop, not a fixed sleep — and the
  same 5 s applies to WP 3.11's sweeper, which therefore sees every line
  up to 5 s late (harmless against a 5-minute sweep interval, but it is
  the reason the naive test shape cannot be made reliable). Found as four
  failures in `verify-stack.sh` step 5i — reproducible, but **inherently
  timing-dependent, not deterministic**: the direction depends on how fast
  the grep follows the request, and on a slow or loaded host the
  `docker compose exec` alone can exceed 5 s and the step passes. Do not
  record it as deterministic, or a later green 5i reads as a regression.
  Pre-existing, and independently reproduced on the untouched baseline
  during the P1 review. **CLOSED in WP 4g** (2026-08-18): step 5i now polls
  for the line — bounded at 10 polls, each preceded by a
  `docker compose exec` round trip, so the effective window is 2-4x the
  flush and widens on exactly the slow hosts that need it. Two details worth
  copying into any future write-then-read check against this log: a flat
  sleep was rejected because it keeps the race with better odds while hiding
  the failure mode, and on timeout the step reports the log's line COUNT plus
  its tail instead of a diagnosis — the wait predicate greps the chunk id, so
  a format regression that dropped field 6 writes a line the grep cannot
  match and would otherwise be misreported as "nothing was written". Suite
  109/109, verified twice.

## 2026-08-22 — the defaults-flip wave (SWEEP-1, APP-DEMO, 4d-web, AG-0, CI-3/AGENT-BIN)
- Text-scrape drift guards (asserting another language's constants) work
  iff every failure message names which of two edits applies: VALUE
  drift -> fix the fixture, GRAMMAR drift -> widen this regex. A guard
  whose header forbids touching the regex while its most likely misfire
  REQUIRES touching the regex gets deleted in irritation. After ANY
  extractor widening, re-perturb for false passes (4d-web's survived 13
  shapes; every silent path exact-match only). The 4d-web guard also
  proved the design end-to-end: deliberately red in its own worktree
  naming the stale sibling config, green on the post-merge run, both
  for the stated reason.
- Twin config files need twin pins: api/.env.example drifted into
  actively setting AND arguing for pre-flip defaults precisely because
  only deploy/.env.example had a config.DEFAULT_*-derived test. Fixing
  the instance without the asymmetry invites the next drift.
- Between review rounds, a DROPPING test count is where lost coverage
  hides: APP-DEMO's 612->611 merge of two pins silently stopped
  requiring the banner call to be GUARDED — a bare DemoModeBanner()
  (permanent banner for real users) built green. Diff the guarantees,
  not the counts.

## 2026-08-22, second wave — the AG series (AG-0..AG-3)
- Pinning the pure/model layer proves NOTHING about the pixels: both
  frontend packages of this series FAILed review the same day with the
  same shape — web: all six DOM wirings deletable at 735/735 green;
  Android: ten of eleven UI/controller wirings deletable at 643/643.
  The one thing that fired on Android was an UnusedResources lint, i.e.
  luck (and deleting a LARGER set kept even that green, because dead
  private composables keep their resource references alive). Every
  wiring from model to pixel needs a named witness. The two idioms that
  work here: fake-dom tests for DOM-building web components, and the
  comment-stripped source-scan pin for Compose call sites.
- Root-cause the missing witnesses before writing them: the web DOM
  layer was unpinned because the SHARED TEST HARNESS (fake-dom.js) was
  a stub whose querySelector/appendChild were silent no-ops — the
  coder had written tests against it and they passed vacuously. After
  extending a shared harness, prove the OLD suites still bite (mutate
  their subjects once each), and make the harness THROW on unsupported
  input instead of returning empty — a harness that silently no-ops is
  the silent-pass class one layer down.
- Source-scan wiring pins have generations, and the weaker one has two
  measured failure modes: a dead never-called helper satisfies a
  contains/window scan (false negative — the feature silently gone),
  and a legitimately-moved-and-called helper fails with "deleted" (false
  positive with a wrong diagnosis — how guards get deleted in
  irritation). The strong generation requires the call's
  nearest-enclosing function to BE the intended top-level composable,
  and its message distinguishes deleted from moved. Use it from the
  start; both frontend packages had to be upgraded to it in review.
- Demo fixtures drift back the moment a twin lacks the twin pin,
  proven within 48 hours of writing the rule: Android's demo carried
  the pre-ADR-0014 auto_gc "off" and would have shipped screenshots
  showing a disk-growth warning no real fresh install shows — under a
  kdoc asserting the shipped defaults produce it. Every platform that
  restates a server default needs its own config-drift guard the day
  the fixture is born, not after the first drift.

## 2026-09-30/10-01 — pre-freeze review (the *-FIX-1/2 packages, TH-0b)
- Polling/loop tests must stop their loops in `finally`. A broken gate
  then fails the test instead of HANGING the whole suite — measured when
  a mutation forced the N4 store gate open (WP WEB-FIX-1).
- A live redirect test whose recording server only implements POST cannot
  see a POST→GET redirect being followed — it passed against the
  vulnerable code. Mutation-check every security test against the OLD
  code before trusting it (WP API-FIX-2).
- Linux file mtimes are tick-coarse (kernel 6.12): two files written in
  quick succession can share an mtime, so never rank files by mtime alone;
  in tests force the times with `os.utime`. A pre-existing retention bug
  hid behind this and failed 5 of 6 runs at HEAD (WP API-FIX-2).
- A self-test that copies a subset of files for a mutation check
  silently starts failing for the wrong reason once the checked script
  gains a dependency outside the subset. Assert the unmutated copy passes
  first, and assert that the failure names the injected difference
  (WP TH-1-FIX).
- A presence check on an env var the image already sets via `ENV KEY=`
  pins nothing: the key is present whether or not compose forwards it.
  Check values (WP TH-1b).
- A concurrent-delete test asserting an exact byte sum failed about 1% of
  runs: whoever wins the final `rmdir` gets the credit. Assert the
  documented contract (a floor), and measure flake rates in-process at
  1000 runs rather than calling a test flaky (WP SEC-FIX-4 follow-up).

## 2026-10-02/03 — upstream keepalive pool (ADR-0017, WP CORE-FEAT-1a..1d)
- Docker-free tests do not stand in for the image build and the live
  stack suite. CORE-FEAT-1's hook test, drift check and pytest were green
  while the core image could not build (`COPY nginx/...` of a path the
  build context's `.dockerignore` excludes; CI never builds that
  Dockerfile, only the publish job does) and the stack suite's fixed test
  subnet overlapped a real stack's network. Run both before a package
  counts as done (WP RC7-INT).
- A probabilistic security race rig with a "the race must have
  happened" check flakes in CI; without that check it passes vacuously.
  Make the attacker win by construction (PATH wrappers around the tool
  that opens the window) and count the attacks (WP VER-2).

## 2026-10-14/15 — rc18 night run (UPD-FIX-1, D16a-c, DIST-1a/b)
- Reviewer mutation scripts must `set -e` and assert the scratch path
  before mutating; a failed `cd` once applied mutations to a real
  worktree (DIST-1a review).

## 2026-10-31 — rc21 (GRAPH-RECENT-1, LOADING-NOW-1)
- See also [process.md](process.md), this section: "**Sample addresses in tests: 10.0.0.x.** The operator's server VLAN ..."

### 2026-11-01 SAMPLE-IP-1

- Shortening example literals breaks column alignment: gofmt realigns Go
  map literals (`gofmt -l`), and hand-aligned trailing comments in README
  or compose code blocks need re-padding.
- Sweeping into a subnet that tests already use creates prefix pairs
  (10.0.0.5 vs 10.0.0.50); check every fixture that pairs an unrelated
  address with the cache IP (safe here: exact string or whole-line checks).
- Before touching a UI string, check whether a golden renders it.
