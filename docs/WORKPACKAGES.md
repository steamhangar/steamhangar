# Remaining work packages — branch-dispatch handbook

> **Historical handbook (note added 2026-09-30).** This file was the
> dispatch structure for the Phase-3 close-out and the Phase 4/5 packages
> as of 2026-08-09; most packages here have since shipped. It is kept as a
> record of how the work was cut and why, not as a live backlog. The
> current state, open items and next steps live in `docs/PROJECT_PLAN.md`
> (§7 checkboxes, §11 Next Steps).

Written 2026-08-09 at the Phase-3 close-out so remaining work can be
dispatched as parallel work packages on branches, each in its own session.
Canonical scope stays in `docs/PROJECT_PLAN.md`; this file adds the
*execution structure*: what a package contains, what it must read first,
what may run in parallel and what must stay serial — and why.

## How to dispatch a package

1. Branch off current `main`: `wp/<id>-<slug>` (e.g. `wp/3.9-oracle`).
2. Start a session whose brief is the package section below, verbatim, plus:
   read `docs/LEARNINGS.md` and the listed ADRs BEFORE writing code.
3. Pipeline inside the session is unchanged and mandatory:
   coder implements → reviewer reviews → FAIL loops back → PASS.
   Packages marked **[Fable]** additionally get a second review pass with
   the strongest available model at the irreversible boundary before merge
   — the marker names the boundary, not a model.
4. One package = one branch = one conventional commit (squash if needed).
   Update `docs/PROJECT_PLAN.md` checkboxes (with evidence notes) and
   append distilled findings to `docs/LEARNINGS.md` in the same commit.
5. Merge to `main` only after PASS. Rebase onto `main` first; the only
   expected cross-branch conflicts are `LEARNINGS.md` / `PROJECT_PLAN.md` /
   `README.md` — all append-biased: resolve by keeping BOTH sides.
6. After merge, re-read the "Parallelism map" below — merging a package can
   unblock others.

## The parallelism rule (proven in Phases 0–3)

Packages are parallel-safe when their **file-tree footprints are disjoint**.
The tracks:

| Track | Tree | Notes |
|---|---|---|
| api | `api/` | ONE package at a time unless marked branch-parallel |
| core | `core/` | independent of api until a package says otherwise |
| web | `web/` (new) | Phase 4a; talks to api only over HTTP |
| app | `app/` (new) | Phase 4b; talks to api only over HTTP |
| release | `.github/`, root `README.md`, `SECURITY.md`, `CONTRIBUTING.md` | Phase 5 |
| docs | `docs/` | orchestrator-owned; append-biased merges |

`deploy/` is shared: packages touching it say so explicitly and are serial
against each other.

---

## Phase 3 — remainder (backend)

Status at writing: 3.8b merged; **3.10 and 3.12 are in flight in the main
session** (do not dispatch); 3.9, 3.11, 3.13 are open.

### WP 3.9 — Manifest oracle, opt-in (api) — **branch-parallel** [Opus coder]
*May run in parallel with 3.11/3.13: it is a new module + one config row;
expected conflicts are only config.py/README appends.*
- ADRs: 0006 (Tier-2 oracle decision, fail-soft, default OFF), 0007 beta
  addendum **decision B**.
- Scope: `VAULT_MANIFEST_ORACLE=steamcmd_api` queries api.steamcmd.net for
  depot lists + current manifest gids; enables pre-emptive stale badges and
  depot info for never-cached games; **decision B**: open (non-passworded)
  beta-branch manifest gids join the GC keep set when the oracle is on.
  Fail-soft everywhere: oracle down/garbage ⇒ behave as if OFF, never block
  a job, never poison depot_manifests (oracle data lives in its own table or
  is clearly provenance-tagged). Strict validation of everything returned
  (LEARNINGS "Parsers" section is binding — ids feed SQL and paths).
- Tests: recorded fixtures (no live network in tests), poisoned/hostile
  responses, oracle-off default pinned by mutation, GC keep-set union with
  beta gids (mutation: dropping the union must kill a named test).
- DoD: suite green; README oracle section incl. privacy note (queries leave
  the LAN); PLAN checkbox.

### WP 3.11 — Event sweep: miss-trigger + client stats/bypass (api) — **serial after 3.12 merge** [Opus coder] **[Fable]**
*Serial because it wires into jobs/worker/scheduler that 3.12 reshapes, and
it auto-enqueues jobs (job-storm risk = the Fable boundary).*
- ADRs: 0008 (the feed contract — read the WP 3.10 format spec in
  `core/README` after 3.10 merges), 0001 (hybrid), 0003/0007 (mapping gate).
- Scope: cursor-based sweeper on the WP 3.5 scheduler cadence reading the
  vault-core event log (format-version checked, unknown version ⇒ skip +
  warn, never misparse). Persisted byte-offset cursor; rotation only after
  a successful sweep. Derives: (a) miss-triggered prefill — MISS on a depot
  mapped to a non-current app enqueues a non-forced prefill, strict dedupe
  + per-app cooldown (config), unmapped depots counted but NEVER trigger;
  (b) per-client hit/bytes stats; (c) bypass detection — agent-report source
  address never seen in the event log within the report window ⇒
  `bypass_suspected` on `GET /v1/clients`.
- Tests: cursor crash-recovery (sweep dies mid-file ⇒ re-read, no loss/dup
  beyond idempotent counters), enqueue-storm mutation (drop the cooldown ⇒
  named test dies), truncated/hostile log lines, log-absent = feature off.
- DoD: suite green; README; PLAN checkboxes (miss-trigger + client stats).

### WP 3.13 — Generic webhooks (api) — **serial after 3.12 merge** [Sonnet coder]
*Serial: consumes the 3.12 job-lifecycle events (done/error/cancelled).*
- Scope: optional outbound POST on job finished/failed and bypass-warning
  events; generic JSON body (Discord/Slack/ntfy-compatible via templates is
  a NON-goal — one generic schema, documented). URL from config, off by
  default; fire-and-forget with bounded retry, NEVER blocks the worker;
  secrets never logged; SSRF posture documented (operator-supplied URL is
  trusted by definition — say so).
- Tests: fake receiver, retry/backoff bounded, worker latency unaffected
  (webhook endpoint hanging ⇒ job completion not delayed — pin it).
- DoD: suite green; README; PLAN checkbox. Phase 3 is COMPLETE when 3.9,
  3.10, 3.11, 3.12, 3.13 are merged.

---

## Phase 4a — Web UI (track: web) — dispatching these packages IS the user's frontend go

Stack decision (made here so packages don't re-litigate it): **no-build
vanilla SPA** — ES modules, the mockup's design language, served by
vault-api as static files. No node toolchain: keeps `compose up` the whole
deploy story (plan §9) and CI trivial. Revisit only if a package hits a
hard wall and then via ADR.

The interactive mockup — **approved by the user at round 7 (2026-08-09)
and frozen** — is the design source of truth:
`docs/design/vault-app-mockup.html` + `vault-app-mockup-NOTES.md` (open the
HTML in any browser). It carries the status-icon system, capsule pills,
in-place tick patching (round 7), bulk semantics, stale-requires-cache
invariant and the notification model. One accepted divergence: the mockup
shows a paused job holding the worker slot; the WP 3.12 backend releases
it — UI packages follow the BACKEND semantics. Remaining polish is
expected to surface through real use, not further mockup rounds.

Recorded divergence (WP 4a.3, 2026-08-10, orchestrator decision — user
veto welcome): the library filter chips ship All / Cached / Not cached /
Downloading / **Failed**, where the mockup has **Update ready** in the
fifth slot. Rationale: `GET /v1/games` has no stale/update field yet
(Phase-4 decision pending), while a persistent per-app `error` status is
real shipped API surface the mockup never modeled — a failed prefill
needs a findable home. When the stale field lands, Update ready returns
and the chip set is re-decided against the mockup.

Recorded cross-frontend divergence (WP 4b.5, 2026-08-11,
reviewer-endorsed): the ANDROID job partition routes a job with an
unknown status into History (neutral glyph, raw status word) instead of
silently dropping it from every section as `web/js/lib/job-partition.js`
does; unknown statuses deliberately do NOT count toward the nav pip
(an unknown terminal status must not pin an uncloseable badge; an
unknown active status self-corrects and stays visible in History).
BACKPORT NOTE for the web: adopt the same routing in job-partition.js —
candidate for the WP 4a.8 polish pass. The log-excerpt truncation-marker
pin (startsWith, not contains) named here as a shared test gap is CLOSED
on both sides as of WP 4b.5 (Kotlin, `LogExcerptTest`'s named mutation
pin) and WP 4a.8 (web) — re-verified during WP 4b.9's carry-over pass;
this line stayed stale after 4a.8 landed and is corrected here rather than
left to mislead the next reader.

Recorded divergences (WP 4a.7, 2026-08-11, reviewer-endorsed): (1) the
notification bell lives in the app-shell topbar, not the mockup's
Library header — reachable from every view, and 4a.7 may not edit
library.js; functional superset. (2) The notification log is
session-only (in-memory) — the NOTES' poll+diff model has no
persistence mandate, and persisting derived events would need its own
dedupe design; revisit only if real use demands it. For the 4a.8 pass:
add the update_ready→detail-sheet target upgrade once 4a.4 lands, and
put the two DOM-wiring fixes (sheet closes on navigation, icon
aria-hidden) into the DOM-harness or live-check list.

Recorded divergence (WP 4b.6, 2026-08-11, reviewer-endorsed): the depot
sharing presentation has a FOURTH state beyond the mockup's three —
ORPHANED, for a shared depot whose co-owning apps all have no cache
content (ADR-0003 last-remnant case, the mockup's Meridian Rally
scenario). Collapsing it into "sole holder" would state a falsehood
(the viewed game holds nothing). Web 4a.4 adopts the same four-state
wording when it builds the detail sheet. User veto welcome.

Recorded divergence (WP 4a.5, 2026-08-10, reviewer-endorsed, same class):
a `cancelled` status-icon kind exists (distinct stop glyph, neutral
colour, own screen-reader word) because `cancelled` is a real terminal
job status (WP 3.12) the mockup never modeled; re-skinning it as Failed
would misreport an operator action. Also recorded for the 4a.8 polish
pass: job cards ship without the mockup's decorative mini cover
thumbnail (no information lost; mockup itself hides all content inside
`.thumb`).

Recorded divergence (WP 4b.10, 2026-08-17, reviewer-endorsed): the ANDROID
clients sheet has no persistent app-shell bypass banner, unlike web's
WP 4a.7 banner (`web/js/components/bypass-banner.js`, present on every
view as a third entry point alongside the notifications panel and the
Settings button this WP adds). Deliberately not ported: the WorkManager
background differ (WP 4b.8) already covers the PUSH half of bypass
awareness independently of any banner, and adding a new persistent,
always-visible surface is itself a frozen-mockup-scope decision (same
class as the WP 4a.1 "Clients is a sheet, not a nav item" ruling), not
something to slip in as a side effect of closing the notification-routing
gap. User veto welcome; revisit if real use shows the two notification-
only entry points (Settings button, bypass tap) are not discoverable
enough.

Recorded divergence (WP 4c-web, 2026-08-18, orchestrator decision — user
veto welcome): the Library view gains a full-width header row below
`.lib-head` for a "Check & update all cached games" button
(`web/js/views/library.js`) — an element the frozen round-7 mockup does
not have at all, because Phase 4c (`docs/PROJECT_PLAN.md` §7) is itself
post-mockup scope: the mockup's `doRefresh()` only ever re-polls vault-api,
it never contacts Steam or starts a download, so there was nothing in the
mockup for this control to extend. Resolved alongside it, same WP: the
`doRefresh()` divergence the plan asked this package to settle stays
resolved as "kept separate" — `store.refreshNow()` (this app's `doRefresh()`
equivalent, wired to pull-to-refresh's `visibilitychange` nudge in
`web/js/store.js`) remains a passive re-poll only; the new button is a
SEPARATE control calling `POST /v1/prefill/cached`, never folded into
pull-to-refresh (the plan calls a refresh gesture that can start downloads
a trap, and this WP agrees). The Android port (separate, still-open work
package) should adopt both decisions verbatim: its own equivalent header/
toolbar gains the same button, worded with the same "Check & update, never
Check" rule, and its own pull-to-refresh equivalent stays passive-only.

**Adopted verbatim on Android (WP 4c-app, 2026-08-18).** Both decisions
carried over exactly as recorded above, with the layout stated precisely
rather than reusing web's "full-width header row" description for a
materially different one (Opus review, this WP): `LibraryScreen.kt` gains
an end-aligned button inside its own `fillMaxWidth()` row directly below
`LibraryToolbar` (the Android equivalent of `.lib-head`) — the row's
container spans the width, the button does not (web's own button is
`width:100%`), and the placement order differs too (web: tools → check row
→ search; Android: search → layout/select → check row) — for the same
"Check & update all cached games" control, worded with the same "check &
update, never bare check" rule (`library_check_update_button`/`_busy` in
`strings.xml`); and `ui/library/` has no pull-to-refresh gesture at all
(`LibraryController.pollGamesForever`/`pollJobsForever` are fixed-cadence
foreground polls only), so the "stays passive-only" rule is satisfied by
the gesture's absence rather than by an explicit carve-out. The pure
wording/partition/error/in-flight logic is a line-for-line Kotlin port of
`web/js/lib/cached-prefill-outcome.js` in
`ui/library/logic/CachedPrefillOutcome.kt`, pinned by a literal
cross-frontend wording-contract test
(`CachedPrefillOutcomeWordingContractTest`) in addition to the ported
functional suite (`CachedPrefillOutcomeTest`, including both BLOCKER
REGRESSION cases by name).
Recorded divergences (WP 4e.1, 2026-08-18, orchestrator decision — **user
approved the full desktop-layout proposal, all divergences included, on
2026-08-18** — "setze alles so um"; implemented as designed, not narrowed):
the frozen round-7 mockup is a single 390×844 phone frame with no responsive
layer at all, and Phase 4e is the first package to deliberately diverge from
it above phone width. Three divergences land in this foundation package:

- **D-1 (bottom nav → left rail).** At BP-L (`min-width:1024px`) the
  mockup's 3-item bottom nav (`web/css/app.css`'s base `.nav`, unchanged
  below BP-L) becomes a sticky, full-height LEFT rail — `#app` turns into a
  `var(--rail-w) 1fr` grid (`grid-template-areas: "rail top" "rail banner"
  "rail main"`), and each `.nav-btn` switches from icon-above-label
  (mockup shape) to icon-beside-label rows. The mockup has no concept of a
  rail at all (it is phone-only chrome); `aria-current`, the queue pip and
  every click/keyboard handler carry over unchanged (`app.js` reads nav
  buttons by `data-view`, never by layout shape).
- **D-4 (derived column count — no auto-fill grid yet).** The brief's own
  measured motivation (a 173×260 cover tile rendering at a fixed 458×687
  from 1280 to 2560px, identically, because nothing before this WP capped
  `.view-root` at anything narrower than 960px) is **not resolved** by this
  package — deliberately out of scope, and this WP does not widen
  `.view-root` past that same 960px baseline either, on purpose. A first
  version of this package DID widen it (`--w-wall:1440px`/`1720px` at BP-L/
  BP-XL), and an orchestrator review measured live that this made the
  problem WORSE, not better, before the auto-fill grid exists to use the
  room (~815×1222 at 1920px, ~838×1257 at 2560px) — a foundation package
  regressing the exact problem its phase exists to fix, on its own, is not
  independently shippable. Fixed by sequencing: `--w-wall` now stays 960px
  in every breakpoint block (base/BP-L/BP-XL all say so explicitly), so the
  tile is IDENTICAL to the pre-WP baseline at 1280–2560px, not merely
  no-worse — re-measured live and pinned headlessly (mutation-verified:
  restoring 1440px/1720px kills two named tests in
  `web/tests/css-layout-foundation.test.js`). `--tile-min` is declared in
  `theme.css` as the future `minmax()` floor but wired to no
  `grid-template-columns` yet. The next package (auto-fill grid) is what
  actually derives the column count from viewport width AND raises
  `--w-wall` past 960px in the SAME change — this one only gives it the
  breakpoint/token plumbing to do so without re-litigating it, deliberately
  holding the width cap flat until then.
  **RESOLVED by WP 4e.2 (2026-08-18):** the auto-fill grid landed, `--w-wall`
  raised to 1600px (BP-L)/2000px (BP-XL), and the tile is no longer fixed at
  458×687 — see that package's own register entry below for the shipped
  numbers, the operator's `--tile-min` value decision (176px Comfortable/
  150px Compact, after a review round found the first-shipped 210px/168px
  produced tiles up to 1.42x the mockup's design size), and the sawtoothed-
  not-smoothed tile-width decision.
- **D-11 (nav DOM order).** `web/index.html`'s `<nav>` now precedes
  `<main id="view-root">` — reading/focus order matches the rail being
  visually first once BP-L applies, even though the mockup (phone-only,
  bottom nav) never had this ordering question. Visual order below BP-L is
  unchanged: `css/app.css`'s `.nav{ order:1 }` keeps it pinned at the
  bottom exactly as the frozen mockup shows, byte-for-byte in the rendered
  output (see `web/tests/css-layout-foundation.test.js`'s "base is
  untouched" pins). `app.js` finds nav buttons via `data-view`, not DOM
  position, so no script changed.

  **Phone-side consequence, recorded honestly (Opus review should-fix S2,
  WP 4e.1 fix round) — this register's original wording only claimed
  VISUAL order is unchanged below BP-L, which is true but incomplete.**
  `order` is a purely VISUAL/paint-order property; it does not affect tab
  order or DOM/accessibility-tree reading order, both of which follow
  source order regardless of `order`. Below BP-L (phone/tablet, the same
  surface WP 4a.8's a11y pass signed off on), a keyboard/screen-reader user
  now reaches the bottom nav's three buttons BEFORE the page's actual
  content on every view — the same phone experience 4a.8 verified now tabs
  "nav, then content" instead of "content, then nav". This is a defensible,
  conventional trade-off (most responsive sites with a DOM-first nav/rail
  make the same call, and a skip-link-style pattern is the usual mitigation
  if it proves to matter) — but it is a real, user-facing change to a
  surface a PRIOR work package already signed off on, not a no-op. Recorded
  here for the phone-side a11y record; revisit if real use surfaces it as a
  problem (a "skip to content" link is the natural fix if so). User veto
  welcome, same as every other entry in this register.

This entry covers only what WP 4e.1 itself shipped; the remaining Phase 4e
divergences it deliberately left unrecorded — overlay geometry at BP-L,
Downloads/Settings-specific layout, the pointer/keyboard model — are still
open, still un-shipped, and still not recorded here for that reason. D-4's
own auto-fill-grid divergence and D-3 below are recorded in the *next*
package's own entry (WP 4e.2, immediately following) rather than here,
since WP 4e.1 itself never shipped either.

Recorded divergence (WP 4e.2, 2026-08-18, orchestrator decision — user
approved the full desktop-layout proposal on 2026-08-18, "setze alles so
um" — user veto welcome, same as every other entry in this register): one
new divergence lands in this package.

- **D-3 (segmented layout control relabeled from a column count to a
  density control).** WP 4a.3 shipped the library's 2/3/list segmented
  control worded "Two columns"/"Three columns"/"List" (title attribute +
  `aria-label`, `web/js/views/library.js`'s `LAYOUT_LABEL`), true for as
  long as the grid itself was a fixed 2-or-3-column switch. WP 4e.2 makes
  the desktop column count derive from viewport width via `repeat(
  auto-fill, minmax(var(--tile-min), 1fr))` (the D-4 fix, closed by this
  same package — see D-4's entry above for the resolution note) — "two
  columns" stops being a true description of the button's effect the
  moment a 1920px screen renders seven or more of them. A false
  `aria-label` is worse than one that reads differently from Android's own
  wording (which nothing pins as cross-frontend parity — unlike the
  status-kind wire names/theme hexes docs/LEARNINGS.md's "Android" section
  calls out, this control's label was never literal-pinned on either side),
  so the control is relabeled to what it actually is, in BOTH densities, at
  every width (not conditionally by breakpoint — one JS-driven string, used
  everywhere, and now the SAME string `segButton` reads for its title/
  aria-label — Opus review nitpick, fix round: it used to be spelled out a
  second time at each of the three call sites with nothing pinning the two
  copies equal): `grid2` -> "Comfortable" (fewer, larger tiles), `grid3` ->
  "Compact" (more, smaller tiles), `list` unchanged ("List" never claimed a
  count). The segmented control's own icons (two/three rectangles) are left
  as-is — they still read as a fewer-bigger vs. more-smaller metaphor under
  the new wording.

  **S6 addendum (operator decision, round 2 review — recorded here because
  this entry's whole justification is that the label must be true of what
  the control DOES):** across several sub-1400px viewport bands
  (1024-1076, 1208-1238, ~1396px up), Comfortable and Compact render
  IDENTICALLY — same column count, same tile width within 0.2px — a direct
  consequence of the two `--tile-min` floors (176px/150px) needed to keep
  the "fix the tile" tile-size guarantee honest (see theme.css's
  `--tile-min` comment). Pressing "Compact" in those bands moves
  `aria-pressed` and fires the "Compact layout" toast but changes nothing
  on screen — the wording is still accurate (it describes the control's
  DENSITY setting, which genuinely did change), but a user acting purely on
  visual feedback at those specific widths would see no effect. Accepted:
  the affected widths are all below 1400px, and the alternative (narrowing
  Compact's floor further to force visible separation everywhere) would
  push the card 22% below the mockup's design size with no measurement
  behind it — a worse trade than an occasional no-visible-effect press.

WP 4e.2 evidence (2026-08-18, Opus round 1: FAIL — one blocker, five
should-fixes; fix round verified, re-verified live against a running
vault-api + the 400-game demo fixture): `web/css/app.css`'s BP-L block wires
`.grid`/`.grid.cols3` to `repeat(auto-fill,minmax(var(--tile-min),1fr))`;
`--tile-min` split into 176px "Comfortable" / 150px "Compact" — the
OPERATOR's decision (round 1 should-fix S1), replacing the coder's
first-shipped 210px/168px after live measurement showed 210px producing
225.6-246.0px tiles (1.30x-1.42x the 173px mockup tile), falsifying the
"fix the tile" claim as shipped. `theme.css`'s `--tile-min` comment now
documents the mechanism this number has to account for (`minmax(F,1fr)`
overshoots to just under `max = F + (F+gap)/n` before adding a column,
worst at the fewest columns) and the operator's second decision: tile width
is sawtoothed across breakpoint thresholds (re-measured: 220.0px at 1195px
viewport -> 177.6px at 1215px, a ~19% shrink from a WIDER window),
documented rather than smoothed away. **Round 2 correction (Opus review B2,
2026-08-18): round 1's own range claim for 176px — "~176-205px (mockup
±18%)" — was itself wrong** (a 4px-resolution sweep over 396 widths
measured 177-222px, 1.02x-1.29x the mockup tile; 220/173 is +27%, not
±18%), and self-contradicted by this same comment's own sawtooth example
(220.0px, sitting under the 205px ceiling just asserted ten lines above
it). Corrected in `theme.css` with the clause a bare number-fix would have
omitted: a <=205px ceiling at every width is unachievable with
`minmax(F,1fr)` at all — it would need F<=158px, already below Compact's
own 150px floor, so this was never a tuning miss to begin with. **S6
(operator decision, round 2): Comfortable and Compact render IDENTICALLY**
— same column count, same tile width within 0.2px — across several
sub-1400px bands (1024-1076, 1208-1238, ~1396px up), an unavoidable
consequence of picking two floors this close (176/150) to keep the "fix the
tile" guarantee honest; accepted and documented (theme.css, this entry)
rather than narrowing Compact further into territory with no measurement
behind it (a ~135px floor would sit 22% below the mockup's design size).
`--w-wall` raised to 1600px at BP-L (round 1 should-fix S2: this value is
UNREACHABLE within BP-L's own range — its widest viewport, 1799px, leaves a
1567px main column, 33px short of 1600 — so it is a guard against a future
`--rail-w`/breakpoint change, not an active cap there; the code comment and
the test's failure message both used to claim otherwise, fixed) and 2000px
at BP-XL (a genuinely separate, bounded decision, argued against the
brief's "topbar/reading measures don't improve past ~1600" note — true of
`--w-text` content, not of a tile wall). `.bulk` re-derived from `--w-wall`
with `--rail-w + --gutter` / `--gutter` insets, proven algebraically exact
in both the capped and uncapped regimes and confirmed live at ten widths
across both review rounds (Δleft = Δwidth = 0 throughout, including inside
BP-XL). Search+chips moved into a `.view-library` BP-L named-area grid, all
six in-flow children assigned by class; search capped at `--search-w:
420px` (round 1 should-fix S4: the cap's VALUE and the area map's row order
were asserted by name/declaration only, not by value — removing the cap
and swapping "search chips"->"chips search" both survived; both now die by
name).

**Blocker B1 (round 1):** `.empty` (the "no results" fallback, a direct
child of `.grid`, appended by `library.js`) had no `grid-column` rule,
unlike `.noresult` right above it — under auto-fill it collapsed into a
single track (measured: an 8.5%-of-row-wide block hard against the left
edge at 2560px), reachable one click away via any zero-count filter chip
(`renderChips` renders those too — a library with zero failed downloads
still shows a clickable "Failed 0" chip). Fixed with `grid-column:1/-1`,
re-verified live via the exact reproduction (the real "Downloading 0" chip
on the 400-game fixture): `.empty` now measures the grid's own full width.
**Correction, not a new divergence (round 2):** the frozen mockup already
applies `style="grid-column:1/-1"` inline to this exact element
(`docs/design/vault-app-mockup.html:1826`) — the WP 4a.3 port dropped the
inline style when translating it into this stylesheet, so this restores
mockup-faithful behaviour. **S7 (round 2):** the fix's own pin was
class-specific (`.empty` only) — generalised into a real static-analysis
test scanning `library.js` for every class appended as a direct child of
`.grid` (excluding `buildCard`'s "card" output, the grid's actual content)
and requiring `grid-column` on each; mutation-verified against a synthetic
new class the same way the reviewer's own probe worked. The app.css
call-site inventory this fix originally cited was also corrected (round
2): ONE `emptyMessage()` caller in `downloads.js`, not two, plus
`settings.js:553`'s loading-state use, previously omitted entirely.

Also closed in round 1's fix round: the base `.grid`/`.grid.cols3` rules and
the BP-L `.grid.cols3` reset (the "fix the tile guarantee made concrete, not
merely asserted" comment) were both completely unpinned — deleting either
survived 456/456 — now mutation-pinned by name in `css-layout-foundation.
test.js`, re-verified by re-applying each mutation and watching the named
test fail, then reverting; round 2 added three sharper variants (deleting a
single reset rule, changing the base `cols3` column count, changing the
base phone card typography), all killed. The hidden-toggle lint's three
attribute regexes gained the `i` flag in round 1 (HTML attribute names are
case-insensitive — `toggleAttribute("HIDDEN", ...)`/`setAttribute("Hidden",
...)` both genuinely set the attribute in a real DOM and both survived
silently before that fix) and, in round 2, `el.hidden ||=`/`??=` and
`el.toggleAttribute?.(` (optional chaining) — both one character from an
idiom already covered, both probed and found surviving; `Object.assign(el,
{hidden:true})` is documented as a genuine, unclosed gap in the lint's
header rather than implied covered. The BP-XL comment's "1656px is 1920's
natural column width" was corrected (round 2) to name it as the CONTENT
width — the column box itself is 1688px.

Suite 462 green (451 WP 4e.1 baseline + 5 round-1 first pass + 4 round-1
fix-round pins + 2 round-2 pins: the generalised S7 test, the compound-
operator/optional-chaining lint probe). Rebased cleanly onto `da7ceae`
(WP 4h.1, `api/`-only) mid-review with no conflicts in `web/`; docs
conflicts in this file and `docs/PROJECT_PLAN.md` auto-merged clean.
Re-verified live with the 176/150 values: six-width table re-measured at
every width (e.g. 1920px: 8 cols/194.6px Comfortable, 10 cols/153.3px
Compact; 2560px: 10 cols/186px Comfortable, 12 cols/153px Compact), `.bulk`
alignment reconfirmed exact, BP-M/base unaffected. 400-card full rebuild:
21-27ms (WP 4e.1 baseline ~29-33ms, no regression).

Recorded divergence (WP 4e.6, 2026-08-18, operator decision — inserted
ahead of WP 4e.3/4e.4/4e.5 in the numbering, at the operator's request, on
the shipped 232px rail: **"232px feels unnecessarily large for the three
things in it"**; asked whether to narrow the rail or give it content, the
operator chose **both** — user veto welcome, same as every entry in this
register): one new divergence lands in this package.

- **D-12 (rail content: vault name + cache summary, no mockup analogue).**
  D-1 already covers the rail's own EXISTENCE (the frozen mockup has no
  concept of a rail at all — phone-only bottom-nav chrome); this entry
  covers the CONTENT WP 4e.6 adds inside it, which is new relative to D-1
  too, not merely a restyling. `--rail-w` narrows 232px -> 180px
  (`theme.css`) — measured live (Chromium, the `--ui` font stack) rather
  than guessed: `.nav`'s content box is 159px (180 - 10px padding/side -
  the 1px `border-right`), and a `.nav-btn`'s OWN content box (inside its
  further 12px padding/side) is 135px, spanning x=22-157. The longest label
  ("Downloads" at the rail's 12.5px `.nav-lb` size) spans x=55-120.5
  (65.5px); `.nav-pip`'s `margin-left:auto` always sits flush against the
  157px edge regardless of digit count, so the number that matters is the
  label-to-pip GAP: 21.5px for a one-digit pip, 17.4px for the worst
  REACHABLE case (a two-digit "20" — `api.js`'s `jobs(limit=20)` makes a
  three-digit pip impossible, not merely unlikely), no wrap, no overlap
  either way. *(Correction, Opus review round 1 nitpick: the first pass's
  136px/169px/12px figures omitted the `border-right:1px` above — restated
  here with the actual measured edges, not the arithmetic-only ones.)* The
  freed 52px goes to the cover wall (`--w-wall`, WP 4e.2) via the existing
  `--rail-w`-derived formulas — nothing about those formulas needed to
  change for the width to move, which is exactly what "parametric, not
  hand-derived" (WP 4e.2's own framing for `.bulk`) is supposed to buy.
  Two content pieces fill the freed rail chrome, per the operator's own
  "not too wide for three items, but not nothing either" framing:
  - **Vault name** (rail head, `#rail-vault-name`) — the existing
    `vault_name` setting (`GET`/`PATCH /v1/settings`, ADR-0009), already
    rendered by `views/settings.js`, now ALSO shown here via an independent,
    ONE-TIME fetch (`components/rail-panel.js`), gated exactly like
    `onboarding.js`'s reconnect-path `getSteamKey()` call (skipped with no
    stored key and demo mode off, so a first run does not fire a call that
    would just 401). **Known, accepted gap:** a vault rename from Settings
    does not update the rail head until the next reload (Settings' own save
    path does not reload the page; onboarding's "Ready" step does) —
    teaching Settings/onboarding to publish into a shared cache was judged
    bigger than this package's brief.
  - **Cache used/free space** (rail foot, `#rail-cache`) — `GET
    /v1/cache/summary`. The brief that opened this package claimed this
    endpoint was "already polled by the store's slow loop"; it was not (`git
    log -- web/js/store.js` showed one commit, WP 4a.2, and
    `api.cacheSummary()` had zero call sites anywhere in `web/js/` before
    this WP) — corrected in `store.js`'s own module header rather than
    silently left wrong. This WP adds a FOURTH `ResourceLoop` to
    `createPollingStore` (a single-snapshot resource: no `keyFn`, no
    `diffByKey`, no notification event — the Downloads nav pip already
    carries queued/running/paused, so a queue summary here would be two
    truths about one thing, which is why cache/summary was picked over it),
    reusing `intervals.gamesMs` — not a new cadence number. Both figures
    degrade honestly: `lib/rail-content.js`'s `cacheFootFromSummary` returns
    `null` (render nothing) before the first poll or on a malformed body,
    but renders a GENUINE zero (`total_bytes:0` — an empty cache;
    `free_disk_bytes:0` — the disk is full) rather than collapsing it into
    "unknown" the way the tile-badge helper `formatBytesGB` deliberately
    does for the same input shape (a new sibling function,
    `formatBytesGBOrZero`, exists specifically because the two helpers need
    OPPOSITE zero-handling). A poll that fails outright is treated
    differently again: `rail-panel.js` leaves the last successful render on
    screen rather than blanking it (the same convention `bypass-banner.js`'s
    own "clients" subscription already uses) — live-verified end to end by
    monkey-patching `api.cacheSummary` to reject, nudging the store, and
    confirming the rail foot text is byte-identical before and after the
    failure, then recovers on the next successful poll.
  - **Server version (coordinator addition, mid-WP, landed via a parallel
    `api/` package — WP 4e.7):** a third line, `#rail-version`, reading
    `GET /v1/settings`'s confirmed top-level `server_version` string
    (a sibling of `readonly`, never a `settings` row — it has no source
    precedence and `PATCH` rejects it as an unrecognised key). Absent,
    non-string, or empty-after-trim are all the SAME "render nothing" case
    (`versionFromSettings`, `lib/rail-content.js`) — deliberately not
    distinguished, since the field is a hand-maintained constant server-side
    (no release-tagging process yet, WP 5.5) and a malformed value is a real
    possibility. Rendered as `v<value>` (never double-prefixed if the server
    value already starts with `v`/`V`), clamped to 24 chars before the
    prefix so a pathological value cannot widen the fixed rail even before
    CSS's own `overflow:hidden` backs it up. This is the SERVER's reported
    version, on purpose, never a frontend constant: `VAULT_WEB_DIR` can
    point this `web/` at a different image than the one actually running,
    so the two genuinely can diverge, which is exactly what an operator
    needs surfaced, not hidden behind a UI-side guess. `demo-data.js`'s
    `GET /v1/settings` fixture gained the same top-level field
    (`"0.1.0"`, matching `vault_api/__init__.py`'s real `__version__`) for
    1:1 demo-mode parity (LEARNINGS "Web UI": demo fixtures are a shipped
    surface, not exempt from matching the real shape).

  Both new elements are invisible below BP-L by construction
  (`.rail-head{ display:none }`/`.rail-foot{ display:none }`, unconditional
  top-level rules — below BP-L, `.nav` is still a 3-column CSS grid with
  exactly `repeat(3,1fr)` explicit tracks, so two extra in-flow children
  with no override would auto-place into a second implicit row instead of
  merely "not showing", which is why this is load-bearing, not cosmetic);
  BP-L's own block switches both back to `display:block`, with
  `.rail-foot{ margin-top:auto }` pinning it to the bottom of `.nav`'s BP-L
  flex column. **Both ARE now `[hidden]`-toggled from JS (should-fix S4,
  review round below) — an earlier draft of this entry said neither ever
  was, which stopped being true the moment S4 landed.** All three lines are
  plain, non-interactive text (`<p>`s): no
  new focusable control exists, so "nothing reachable only by hover" holds
  trivially, and `aria-current`/keyboard reachability on the three nav
  buttons are untouched (verified: no `tabindex` added anywhere, button DOM
  order/attributes unchanged).

  Live-verified (Chromium, running vault-api + the 400-game demo fixture,
  demo mode): rail width 180px at every viewport (fixed, not
  viewport-dependent); at 1024px the auto-fill grid now gives 4 cols/190.3px
  (Comfortable); at 1920px 9 cols/177.4px Comfortable (was 8 cols/194.6px at
  232px — the freed 52px buys a whole extra column) and 10 cols/158.5px
  Compact (was 10 cols/153.3px); at 2560px both densities are IDENTICAL to
  the pre-narrowing WP 4e.2 numbers (10 cols/186px Comfortable, 12
  cols/153px Compact) because BP-XL's 2000px `--w-wall` cap already bound
  before AND after the rail narrowed — the extra 52px simply has nowhere
  left to go at that width, correctly. `.bulk`'s Δleft/Δwidth against
  `.view-root`'s own content-area edges measured exactly 0/0 in multi-select
  at 1024/1920/2560px, confirming the WP 4e.2 formula
  (`left:calc(var(--rail-w) + var(--gutter))`) is genuinely parametric on
  `--rail-w` rather than re-verified by coincidence. Base (<720px, at 375px
  and 719px) confirmed byte-unaffected: `.rail-head`/`.rail-foot` both
  `display:none`, the bottom nav's own `grid-template-columns` still
  `121px 121px 121px` at 375px, unchanged.

  Suite 498 green at first pass (462 WP 4e.2 baseline + 36: 5
  `formatBytesGBOrZero` pins, 21 `rail-content.test.js` pins across all
  three content pieces incl. the unknown-vs-zero mutation targets, 4 new
  `store-poll-loop.test.js` "cache" loop pins, 3 `css-layout-foundation.
  test.js` pins for `--rail-w`/display toggling/`margin-top:auto`, 1
  `demo-data-settings.test.js` pin for `server_version`'s shape — plus 2
  more added after the coordinator's `server_version` shape correction).

  **Opus review: PASS, no blockers — "the first package in this phase
  where every pin the coder claimed could actually observe its own
  violation." Ten mutations died by name, including three the reviewer
  added itself. Four should-fixes, all addressed; suite now 515 green.**

  - **S1** — the WP 4e.2 `--w-wall:1600px` comment's own "1600px is
    unreachable at BP-L" claim went stale the INSTANT this package
    narrowed `--rail-w` — exactly the "future change to `--rail-w`"
    scenario that comment's guard was written to warn about. At 180px, BP-L's
    own widest viewport (1799.98px) gives a main column of
    `1799.98 - 180 - 15(scrollbar) = 1604.98px`, over 1600px — the cap now
    genuinely binds (measured: `.view-root` computes to exactly 1600px,
    capped, at 1799px; 8 columns, 185.5px tiles; uncapped at 1794px,
    1599px). Visual cost: ~4px, invisible in practice — the guard did its
    job at exactly the margin it was sized for, only the comment describing
    it was stale, and no test had caught the arithmetic drifting. Fixed:
    the comment corrected in place (the ORIGINAL WP 4e.2 reasoning for
    keeping 1600px as a guard rather than `none` is left intact, since that
    decision is still correct), plus a new structural pin in
    `css-layout-foundation.test.js` computing the same breakeven from the
    live `--rail-w`/`--w-wall` tokens, so the next such change fails a
    named test instead of leaving a comment stale again.
  - **S2** — the cache loop's cadence (`intervals.gamesMs`) was completely
    unpinned: mutating it to `intervals.jobsFastMs` (2s in production, 7.5x
    more frequent — the one loop whose endpoint can trigger a cold,
    seek-bound depot walk on `SizeCache` TTL expiry) survived all 498 prior
    tests, since none of them gave the cache loop a cadence distinct from
    every other interval. Fixed with a live-timing pin in
    `store-poll-loop.test.js` (a short `gamesMs`, huge everything else) —
    wrapped in `try`/`finally` around `store.stop()` after a REAL hang was
    measured while developing it: a failing assertion skipped cleanup,
    leaving a 50s reschedule timer alive and hanging the test file's exit
    past a 120s harness timeout with zero output; the fix (clean up
    regardless of outcome) brought it back to under 100ms.
  - **S3** — `rail-panel.js` had ZERO test coverage (the "components/*.js
    are DOM glue, not unit-tested" default, treated as a hard rule rather
    than the default it actually is). Two real bugs hid behind that gap:
    deleting `if (payload.error) return;` (a failed poll blanks the rail
    back to "unknown" instead of leaving the last real number visible) and
    deleting `if (foot.freeText !== null)` (renders a literal `"Free
    null"` — a fabricated value, the precise failure this package exists
    to prevent) both passed the full suite. Fixed by refactoring
    `rail-panel.js` into a dependency-injected `createRailPanel()` factory
    — the SAME `store.js`/`store-singleton.js` split, applied to a
    component for the first time (`app.js` now makes the one real,
    side-effecting call this file used to make on import) — so
    `rail-panel-wiring.test.js` (new, 15 tests) can drive it with
    `fake-dom.js` `FakeElement`s (extended with `replaceChildren`) and
    trivial fake store/api stand-ins; both mutations now die by name.
  - **S4** — "render nothing, never a placeholder" had only been applied to
    the TEXT inside `#rail-vault-name`/`#rail-cache`/`#rail-version`, not to
    the `.rail-head`/`.rail-foot` boxes drawn around them: a default install
    with no `vault_name` set showed an empty box with a bare divider line
    above the nav, and `.rail-foot` carried dead space from
    `#rail-version`'s own margin even with real cache data (every build
    before WP 4e.7 merged `server_version`). Fixed:
    `headEl.hidden`/`footEl.hidden`/`versionEl.hidden` now toggle alongside
    the text, guarded in app.css's BP-L block against the `display:block`
    override each would otherwise lose the cascade to (same
    `[hidden]`-vs-author-`display` fix as `.btn[hidden]`/`h4.sec[hidden]`/
    `.onbnav[hidden]` elsewhere in this file); `.rail-foot` hides ONLY when
    BOTH the cache summary and the version line have nothing to show
    (either alone keeps it visible), pinned as its own AND-not-OR
    regression test in `rail-panel-wiring.test.js` — live-verified in a
    real browser too (`.rail-head`/`.rail-foot` both computed
    `display:none`, zero height, when force-hidden).

  Nitpicks also closed: `theme.css`'s rail geometry comment's
  136px/169px/12px figures (the `.nav` `border-right:1px` was missing from
  the arithmetic) corrected to the real measured 135px/157px/21.5px-17.4px
  above; the `--tile-min` overshoot band's low end restated as exactly
  176px (the token's own `minmax()` floor, by definition, not a third
  swept approximation that could drift again — "this exact sentence has
  now been corrected twice... make it the last time"); `web/tests/
  README.md`'s "paints... before subscribing" corrected to the actual
  order (subscribe first, then paint from the existing snapshot).

  Suite 515 green after the fix round (498 + 17: the S1 structural pin, the
  S2 cadence pin, and `rail-panel-wiring.test.js`'s 15 tests). Re-verified
  live: the S1 binding at 1794/1799px, the S4 hidden-container behaviour in
  a real browser (not just the headless factory tests), and the demo-mode
  render (vault name, cache summary, version line) all still correct after
  the refactor.

Recorded divergence (WP 4e.3, 2026-08-18, operator decision — "detail +
confirmations become a centred card at eye level; notifications and clients
become a drawer from the right — ambient side panels, not focused tasks;
mobile keeps its bottom sheets, unchanged" — user veto welcome, same as every
entry in this register): one new divergence lands in this package.

- **D-13 (overlay geometry at BP-L: two new shapes with no mockup
  analogue).** The frozen mockup is phone-only — every overlay it defines
  (`.sheet-backdrop`/`.sheet`, `.dialog-backdrop`/`.dialog`) is a single
  390×844-frame shape with no wide-viewport variant to diverge FROM; this
  entry records the shapes WP 4e.3 invents for BP-L (`min-width:1024px`,
  the SAME breakpoint the D-1 rail conversion uses, not an independently
  chosen one), not a change to an existing mockup shape the way D-1/D-3/
  D-4/D-11/D-12 each were.
  - **Centred card** — the game detail sheet (`components/game-detail-sheet.
    js`, `sheet-dialog.js`'s new `variant:"center"` option) centres at eye
    level instead of sliding up from the bottom. **Card WIDTH (operator
    decision, 2026-08-18, fix round 2): the card widens to 680px at BP-L**
    (`--w-sheet-l`, a NEW, dedicated token in `theme.css` — not a
    redefinition of the shared `--w-sheet`, which the drawer and every
    other sheet still need at 480px, and not a literal inside
    `.sheet--center`, since `app.css`'s BP-L block needs both widths to
    coexist at the same breakpoint; WP 4h.3's header art is expected to
    build against this same token). This is the plan item's "sizing" half,
    genuinely delivered, not merely "placement" — 680px, the low end of the operator-approved
    680-760px range (the selected option was labelled ~720px), not 720px or the range's 760px ceiling, chosen from the type
    measure of the card's own longest running prose (the status-icon legend
    /depot-unknown captions, ~11.5px, ~0.55em/character): even 680px yields
    ~102 characters/line for that text against the classic 45-75-character
    ideal, worse at 720/760px, so the LOW end of the approved range was the
    least-bad choice, not an arbitrary pick within it — full arithmetic in
    `theme.css`'s own token comment. The confirm dialogs stay at their
    existing, deliberately narrow `.dialog` width (420px, untouched) —
    "that is their job" (operator) — only the detail sheet itself widens;
    the drawer stays at `--w-sheet` (480px) for the same reason (an ambient
    side panel, not the primary reading surface).
  - **Right-edge drawer** — the notifications panel and the clients sheet
    (`variant:"drawer"`) appear at the right edge, full viewport height,
    instead of the bottom. No motion either way for either new shape (nor
    for the bottom sheet they replace) — every overlay in this app,
    including these two, is a plain `display` toggle with no transition
    anywhere in the codebase; an early draft of this entry and of the
    shipped code comments said "slides in", which overclaimed an animation
    that does not exist (Opus review, fix round — corrected in both
    places).
  - The bulk-delete/GC-execute confirm dialogs (`.dialog-backdrop`/
    `.dialog`, reused verbatim by `game-detail-sheet.js`'s two confirms and
    `views/library.js`'s bulk delete) stay a centred card at every width, as
    they always were — but needed a BP-L re-centring fix of their own
    (**Opus review blocker B1**): they used to centre on the FULL viewport,
    while the sheet's own new centring axis moved to the content area
    (excluding the rail) — a live-measured constant −90px (`--rail-w`/2)
    mismatch between a confirm and the card it covers, the exact failure
    mode this package's own centred-card design note already named for the
    sheet, shipped anyway for the dialog. Fixed with
    `padding-left:calc(var(--rail-w) + 22px)` on `.dialog-backdrop` (the
    `+ 22px` preserves the base rule's own uniform 22px inset, which a bare
    `var(--rail-w)` would have replaced, landing 11px off) — z-index
    stacking (45 above the sheet's 40) needed no change, and still holds at
    this breakpoint by construction.
  - Both variants reuse the SAME `.sheet-backdrop`/`.sheet` DOM/JS scaffold
    (`sheet-dialog.js`), gated purely by a static modifier class
    (`sheet-backdrop--<variant>`/`sheet--<variant>`) — no second focus trap,
    no second Escape listener, no new state model; `lib/modal-stack.js`'s
    existing push/pop/Escape stack is unaware the classes exist. Mobile
    (below BP-L) is untouched: every rule this package adds lives inside
    the existing BP-L `@media` block (`git diff --stat` on `app.css` is
    purely additive).

  Verification: `web/tests/css-overlay-geometry.test.js` (new, structural
  CSS pins plus one fake-DOM behavioural pin for the nesting/Escape-ordering
  claim) — 524 baseline → 539 green on the first pass, 540 after the B1 fix
  round (one pin added: the confirm-dialog/sheet centring-axis match,
  computed from the live `--rail-w` token rather than two repeated
  literals), 543 after the operator's card-width decision (three more:
  `--w-sheet-l`'s existence/range, `.sheet--center` sourcing its max-width
  from it rather than `--w-sheet`, and `.sheet--drawer` gaining no such
  override — mutation-tested in both directions, token deleted and
  `.sheet--center` pointed back at `--w-sheet`, both dying by name). Every
  mutation this file's pins claim to catch was applied and reverted, dying
  by name each time, across all three rounds — see the coder's reports for
  the full list (breakpoint-anchoring, mobile-unchanged,
  z-index-above-rail/topbar, z-index-above-sheet, B1's centring-axis match,
  N1's drawer border cleanup, N2's drawer body top-padding match, the
  JS-side variant-class wiring, and the width-token pair). Two of the
  first-pass test names
  overclaimed "byte-identical to the pre-WP rule" (Opus review S3) — the
  underlying claim about the shipped diff is true (purely additive, `git
  diff --stat` shows 0 deletions), but the regex-based pins themselves only
  check specific property values, not full rule-body literal-identity (the
  reviewer proved this by adding an unrelated property to the base rule and
  watching the suite stay green); renamed to "keeps every pre-WP property
  value" rather than switched to a literal-text comparison, matching this
  codebase's existing value-pin convention (`css-layout-foundation.test.js`)
  rather than adding a new byte-snapshot style this file would be the only
  one to use.

- **D-14 (pointer/keyboard interaction model: hover, focus, scroll — no
  mockup analogue).** The frozen mockup is phone-only and touch-first; it
  has no concept of `:hover`, no opinion on what a keyboard focus ring
  should look like beyond the browser default, and no mouse-drag scrollbar
  — every decision below is WP 4e.4 inventing desktop-pointer/keyboard
  behavior from nothing, the same class of divergence D-1/D-12/D-13 already
  are for the rail/overlay geometry, not a change to an existing mockup
  shape.
  - **Hover gating.** Every `:hover` rule that existed before this WP
    (`.iconbtn`/`.segs button`/`.qx`/`.chip`/`button.cappill`/`.btn`/`.inp
    button`/`.skiplink`/`.notif`/`.depotwrap.sh .depot`) was a bare rule,
    live at every width for every pointer type, including touch — only
    `.nav-btn:hover` (WP 4e.1) was already gated behind
    `(hover:hover) and (pointer:fine)`. All 11, plus five new affordances
    for previously-silent controls (`.hrow > button`, `.banner .acts
    button`, `.icnact`, and a pointer-only echo of the existing `:active`
    press feedback on `.card`/`.grid.list .card`), now live inside that
    same gate.
    **Opus review round 1: FAIL — B1 (blocker), measured live in real
    Chrome at 1280px.** The first pass relocated all 11 rules into ONE
    trailing block at the end of `css/app.css`. The rule TEXT never
    changed, only its SOURCE POSITION — but five of them were an
    equal-specificity `(0,2,0)` sibling of a "state" rule
    (`.iconbtn.on`/`.segs button[aria-pressed]`/`.chip[aria-pressed]`/
    `.btn:disabled`/`.notif.unread`) that used to WIN on hover only because
    it was written AFTER the element's `:hover` rule; moving every hover
    rule to the file's end made it "later" instead, flipping all five ties
    (measured: a disabled button visibly re-enabled under the cursor, an
    active multi-select icon read as switched off, an unread notification's
    gradient vanished on hover, and so on for the other two). The reviewer
    also caught a comment claiming a "moving a hover rule out changes
    nothing else" pin existed when it did not — every hover assertion in
    the first draft was positional (rule text lives inside SOME gate),
    never a cascade-OUTCOME check, so all 22 first-pass tests passed with
    all five regressions present. **Fixed:** every hover rule now gets its
    OWN small `@media (hover:hover) and (pointer:fine){...}` wrapper, in
    place, at its exact original source position — preserving the
    pre-existing win order by construction, the reviewer's own
    reviewer-endorsed minimal fix. `web/tests/keyboard-pointer-model.test.js`
    gained a cascade-OUTCOME pin (source-index comparison) for each of the
    five pairs, re-verified by reconstructing the exact original bug
    (temporarily reversing all five pairs on the real file) and confirming
    all five named tests die, then reverting — see the coder's report.
    **S2 (cheap fix, same review):** `.nav-btn:hover` — the WP 4e.1
    precedent this package generalized — carried the identical defect on
    its OWN, independent of this WP's relocation: `.nav-btn[aria-current=
    "page"]` (the current-page accent, base "bottom nav" section) is an
    equal-`(0,2,0)`-specificity sibling that structurally CANNOT be
    reordered before `.nav-btn:hover` (the hover rule lives inside a
    `min-width:1024px` block, which by this file's own top-to-bottom
    convention can only appear after the base rules it overrides). Fixed
    instead with a higher-`(0,3,0)`-specificity override
    (`.nav-btn[aria-current="page"]:hover{ color:var(--accent); }`), which
    wins regardless of source order. Both the five order-dependent fixes
    and this specificity fix were re-measured live (Browser pane, real CDP
    mouse hover over synthetic probe elements carrying the exact class/
    attribute combinations): `.iconbtn.on`/`.segs button[aria-pressed]`/
    `.chip[aria-pressed]` all kept `color:rgb(46,217,206)` (`--accent`)
    under hover; `.btn:disabled` kept `filter:"none"`; `.notif.unread` kept
    its accent gradient background; `.nav-btn[aria-current="page"]` kept
    `color:rgb(46,217,206)` — none fell back to the plain hover treatment.
  - **Focus-ring visibility.** The existing global `:focus-visible` rule
    (`theme.css`, unchanged) already covers every focusable element by
    construction. Three controls were found to sit flush against an
    `overflow:hidden` ancestor with no inset — `.segs button` (inside
    `.segs`), `.hrow > button` (inside `.hrow`), `.depotwrap.sh .depot`
    (inside `.depots`) — so the ring's default 2px-OUTSIDE offset was
    invisible, clipped by the very container each control lives in. Fixed
    with a per-selector `outline-offset:-2px` (pulls the ring inside the
    box instead of loosening the container's own clip, which is
    load-bearing for each one's rounded-corner seam). Verified live: a real
    keyboard Tab (CDP Input, not a dispatched `keydown`) onto the
    "Comfortable" segmented button showed `outlineOffset: "-2px"` in the
    computed style, confirming the fix actually takes effect against a real
    focus-visible match (a JS-only `.focus()` call was measured NOT to
    trigger `:focus-visible` in this same browser, so the check specifically
    used a genuine input-level Tab press instead) — this pin was the one
    the reviewer confirmed as measured and load-bearing without a fix
    round: baseline ring clipped on three sides, the shipped fix fully
    inside the clip box.
  - **B2 (must-fix, same review) — invisible tab stops in the bulk bar.**
    `.bulk{opacity:0; pointer-events:none; transform:...}` kept its two
    action buttons (Cancel/Download) IN the tab order while invisible —
    measured live: Tab from the last library card landed on an invisible
    Cancel, then an invisible Download, then BODY, two dead stops
    immediately after the grid, squarely inside this package's own
    focus-order scope. Pre-existing (every WP back through 4a.3), but this
    package's full focus inventory makes it this WP's to fix. **Fixed:**
    `.bulk{visibility:hidden}` / `.bulk.up{visibility:visible}`, composed
    with the existing `opacity`/`transform` fade (per the CSS Transitions
    spec, animating TO `hidden` keeps the interpolated value `visible`
    until 100% of the transition, so the bar stays reachable through its
    own fade-out rather than vanishing from the tab order a frame early;
    animating FROM `hidden` flips to `visible` at 0%, immediately). Verified
    live: with the bar closed, `getComputedStyle(.bulk).visibility` is
    `"hidden"` and calling `.focus()` directly on its Cancel button (whose
    `tabIndex` attribute is still the native `0`) does NOT move
    `document.activeElement` — confirming `visibility:hidden` genuinely
    removes it from the reachable set, not merely from paint. Round 2
    verified the opposite direction too: with `.up` set, all three bulk
    buttons are reachable and operable in DOM order — the half a
    `visibility` regression would hide.
  - **Scroll affordances.** `.sheet .body` (notifications/clients/detail
    drawer or centred card), `.cflist` (delete-confirm shared-depot list)
    and `.onb .rest` (onboarding wizard) all hide their scrollbar
    unconditionally — a deliberate touch-surface choice, kept as the
    default. Decision (per the brief's own framing, "styled scrollbar OR
    another affordance"): a real, thin, themed scrollbar for pointer users,
    gated the same way as the hover rules (`(hover:hover) and
    (pointer:fine)`, not tied to BP-L) rather than a fade hint, which would
    need either JS scroll-position tracking or a static overlay that lies
    on non-overflowing content — a native scrollbar is honest by
    construction. `.chips`' own hidden scrollbar needed no decision: it
    already stops scrolling entirely from BP-M (720px) up (`flex-wrap:wrap`,
    WP 4e.1), so there is no scrollbar to reveal at any desktop width.
    **S3 (cheap fix, same review):** an earlier draft's "no layout shift"
    framing for this WP overreached — it is true of the hover box-shadow/
    background/filter tweaks above, but a scrollbar occupies a track by
    definition: an 8px-wide track narrows each of these three surfaces'
    content column by up to 8px the instant a fine pointer is detected.
    Stated plainly in `css/app.css`'s own comment rather than left
    overclaimed; accepted (not engineered around) because all three
    surfaces already carry generous padding relative to their own running
    text.
  - **Keyboard operability of the primary flows** needed no NEW mechanism —
    the inventory found the grid card (`role="button"`, `tabIndex=0`,
    Enter/Space wired, `components/game-card.js`), the depot co-owner toggle
    (`tabIndex=0` when expandable, Enter/Space wired,
    `components/game-detail-sheet.js`), and every delete/GC-execute confirm
    (focus-on-open to the safe default, `lib/modal-stack.js` inert +
    centralized Escape) were already fully wired by WP 4a.3/4a.4/4a.8 — this
    WP added a regression pin (`web/tests/keyboard-pointer-model.test.js`)
    for the composed, NESTED sheet+confirm flow (open → confirm opens on top
    → Escape closes the confirm only → second Escape closes the sheet,
    focus restored correctly at each step) that did not exist as its own
    test before, built from the same primitives `modal-stack.test.js`/
    `dialog-wiring.test.js` already unit-test individually.
  - **Roving tabindex for the library grid — explicitly NOT built —
    corrected and sharpened (Opus review S1).** The deferral's underlying
    reasoning held up under measurement (nav/search/chips genuinely precede
    the grid in DOM/tab order, and "filter first" is a real, reachable
    mitigation), but the first draft understated the cost and overstated
    one fact:
    (1) **Omitted cost, now stated:** with multi-select ON, the "Delete
    selected" bulk-bar button sits AFTER the entire grid in DOM order — on
    the `demo-data-large-library.test.js` 394-game fixture, that is
    measured at **~590 Tab presses** to reach the primary DESTRUCTIVE action
    of the bulk-delete flow from the top of the grid, not merely an
    inconvenience reaching an arbitrary card.
    (2) **False claim, corrected:** the inventory's mention of `.skiplink`
    implied a skip-to-content link exists on the library view. It does not
    — `.skiplink` is `onboarding.js:559`'s demo-mode "browse in demo mode"
    button, part of the first-run overlay, and appears nowhere on the
    library view itself. **No skip link exists anywhere in this app.**
    (3) **Undercounted stops-per-card, corrected:** the first draft implied
    roughly one Tab stop per card. Measured live against 6 demo-mode games:
    **9 focusable elements**, not 6 — a card with an available action
    (download/pause/resume) adds its own pill/meta-icon button as a
    SEPARATE tab stop alongside the card itself, so stops-per-card is
    `>1`, not `1`, and the ~590-press estimate above is if anything a floor.
    Decided AGAINST building roving tabindex in this package regardless: a
    real implementation needs to (a) survive `library.js`'s existing
    patch-vs-rebuild round-7 card lifecycle (a card can be replaced by
    `rebuildCard()` mid-session — the roving index would need to follow the
    NEW node, not silently point at a detached one), and (b) resolve
    2-D (arrow-key) movement against a column count that is not fixed
    (`auto-fill`, WP 4e.2) — both genuinely separate, non-trivial features
    with their own failure modes and test surface, not a same-package
    add-on, and the brief's own text says not to build a speculative ARIA
    grid pattern. Recorded here as a real, deliberately deferred gap with
    its full, now-corrected cost — not silently dropped, and not
    undersold — a follow-up package (its own effort estimate) is the
    honest way to build it.

  Verification: `web/tests/keyboard-pointer-model.test.js` (new) — structural
  CSS pins (hover-gate coverage including the two Opus-review regex
  nitpicks — `any-hover` false-positive rejection and comma-OR-disjunction
  rejection —, six named cascade-OUTCOME pins for the five order-dependent
  pairs plus the `.nav-btn` specificity pair, the focus-ring cross-section
  and the three named overflow-clip fixes, the B2 `.bulk` visibility pin,
  the reduced-motion wildcard) plus four fake-DOM behavioural pins for the
  nested sheet+confirm keyboard flow, using the same `fake-dom.js` harness
  `dialog-wiring.test.js`/`modal-stack.test.js` already share. 543 baseline
  → 574 green (round 2, after the fix round; round 1 shipped at 565 with
  the B1 defect undetected). Mutations applied to the REAL files and
  reverted, each dying by the exact name reported in the coder's report:
  moving `.chip:hover` out of the gate; deleting `.hrow >
  button:focus-visible`; narrowing the reduced-motion wildcard to `.btn,
  .chip`; reverting `lib/modal-stack.js`'s centralized Escape dispatcher to
  fire every stacked overlay's `onEscape` instead of only the topmost's
  (also killed the PRE-EXISTING `modal-stack.test.js` pin for the same
  historical bug); reconstructing the exact original B1 bug (reversing all
  five order-dependent pairs) killed all five named cascade-outcome tests
  by name simultaneously; reverting `.bulk`/`.bulk.up` to the pre-B2-fix
  opacity/pointer-events-only shape killed the B2 pin by name. All
  mutations reverted afterward; suite confirmed back to 574 green after
  each.

Recorded divergence (WP 4e.5, 2026-08-19, orchestrator decision — user
veto welcome, same as every entry in this register): one new divergence
lands in this package, the last of Phase 4e.

- **D-15 (Downloads/Settings own BP-L reading column — no mockup
  analogue).** The frozen mockup is phone-only; it has no concept of either
  view existing at a width where "how wide should this column be" is even a
  question. Both views inherited `.view-root`'s BP-L width (`--w-wall`,
  960-2000px — the Library grid's own cap, WP 4e.2/4e.6) with no content-shape
  review of their own, which is the literal gap `docs/PROJECT_PLAN.md`'s
  Phase 4e section named: "both currently just inherit .view-root's width...
  unreviewed for their own content shape at desktop widths".

  **Inventory, per view.** Downloads is Active/Paused job cards (`.jobcard`:
  a header row, a right-aligned action-button row, prose `.holdnote`/
  `.stopnote` notes), the Queue (`.qrow`: grip+name+position+remove, one line
  each) and History (`.hrow`: icon+name+time+chevron, expanding into pre-wrap
  `.log` text) — every one of those is a ROW or a short paragraph, never a
  tile that benefits from more columns. Settings is a stack of `.field`s
  (label above input above caption) plus a handful of `.srow`/hint
  paragraphs — a form that scans top-to-bottom by design. Stretched across
  `--w-wall`, rows just gain a wide, purposeless gap between their left and
  right content, and the prose (`.holdnote`/`.stopnote`/`.hint`/
  `.foot-note`) would run past a comfortable reading measure. **Citation
  correction (Opus review, WP 4e.5): this is NOT what `--w-sheet-l`'s own
  comment (`theme.css:222-230`, WP 4e.3) establishes** — an earlier draft
  claimed that comment treats "45-90 characters" as this codebase's
  ceiling; it actually records the codebase KNOWINGLY EXCEEDING the classic
  45-75-character ideal (and even its ~90-character outer bound) at 680px,
  calling the resulting ~102 chars/line "a real but tolerable readability
  tax" — the opposite of a ceiling anything else must respect. The real,
  measured numbers (Opus review): the 178-character `.holdnote` (the
  longest single line of prose either view carries) rendered as ONE line,
  1655px wide at a 1920px viewport / 1931px wide at 2560px, uncapped; with
  this WP's `--w-text` cap, it wraps to 2 lines at ~89 characters each —
  just inside, not past, that same ~90-character outer bound.

  **Decision: both get the SAME capped, centred column, `--w-text`** (no new
  token — still 760px from BP-M up, unchanged) — a "capped readable
  measure" (one of the three options the brief itself named as legitimate),
  not a multi-column arrangement, for reasons specific to each. Downloads'
  four sections vary in item count independently of each other in every real
  state (one active job and an empty queue; an idle vault with forty history
  rows and nothing running) — a side-by-side split would routinely leave one
  column much taller than the other with nothing to fill the gap, unlike the
  Library grid, where every cell is the same size by construction. Settings'
  fields are a linear form; a second column would be decoration, not
  function, on a screen this short, and building one would contradict the
  brief's own caution ("three columns of form fields is not automatically
  better"). A hybrid (help text beside each field instead of below it) was
  considered and rejected for THIS package specifically: it would need
  `buildTextField`'s DOM restructured into a control/help split — a real JS
  change for a cosmetic gain over the caption already shown today —
  recorded here as a legitimate follow-up, not silently dropped.

  Mechanically: `css/app.css`'s BP-L block gains one additive rule,
  `.view-downloads, .view-settings{ max-width:var(--w-text); margin:0 auto;
  }`, placed directly after the pre-existing `.view-root{ max-width:var(
  --w-wall); }` it composes with (not fights — the two rules target two
  different elements, the child `<section>` and its parent `<main>`, so
  there is no cascade tie to resolve). `.view-library` is deliberately
  excluded — it keeps inheriting `.view-root`'s wide cap unchanged, exactly
  as before, for its own auto-fill tile grid. No existing rule was touched
  to make this true; `theme.css`'s `--w-text` comment gained a short
  addendum documenting the new BP-L consumer (still no new token, no value
  change — pinned at exactly two assignments system-wide,
  `css-downloads-settings-layout.test.js`), and the pre-existing BP-XL
  comment's own "Settings/Downloads are --w-text-based content" claim —
  true in prose since WP 4e.2, false in the actual shipped rule until this
  package — is corrected in place to say so honestly. **That correction was
  itself only 2/3 right (Opus review, WP 4e.5): the same sentence also
  names the banner as `--w-text`-based, which it is not** — `.banner-wrap`
  was deliberately moved onto `--w-wall` at BP-L in WP 4e.1's own should-fix
  S4 (so it stays aligned with `.view-root`'s column, eight lines above
  this WP's new rule in `app.css`), and stays there; the correction now
  names all three cases individually rather than repeating the original
  sentence's own three-way list as if all three were the same.

  New test file `css-downloads-settings-layout.test.js` (13 tests): the
  layout rule's existence/derivation-from-the-live-token (mutation target:
  reverting the whole block, or hardcoding a `760px` literal in place of
  `var(--w-text)`, each dies by name), `.view-library`'s exclusion, `--w-text`
  pinned to exactly two assignments anywhere so a future BP-L/BP-XL
  redefinition cannot silently widen the cap without touching this rule at
  all (mutation-verified: sneaking `--w-text:900px` into the BP-L `:root`
  block kills this pin by name), two no-cascade-tie pins (`.view-downloads`
  referenced exactly once in `app.css`; `.view-settings` referenced exactly
  twice — this WP's own rule plus the one pre-existing, unrelated
  `h4.sec:first-of-type` descendant rule, verified NOT to touch `max-width`/
  `margin` on the bare class), and six "keeps every pre-WP property value"
  pins across the actual inventory (`.dl-head`/`.dl-sub`, `.jobcard`/
  `.jobtop`, `.qrow`, `.hrow`/`.hrow > button`, `.field`/`.srow`) — mutating
  `.qrow`'s `border-radius` from `var(--r-m)` to `var(--r-s)` dies by name,
  confirming these are real value pins, not vacuous existence checks. All
  mutations applied to the real files, confirmed dying by name, then
  reverted; suite reconfirmed green after each. 574 baseline → 587 green,
  first pass.

  **Opus review: PASS, no blockers.** The one failure mode a source-text
  pin cannot see is the actual centring AXIS — precisely the class of thing
  that shipped wrong in WP 4e.3's round-1 blocker (a confirm dialog centred
  on the full viewport instead of the content area, a live −90px/
  `--rail-w`/2 mismatch) — so the "a live check is unnecessary here"
  framing an earlier draft of this entry carried was luck-adjacent even
  though the outcome happened to be right. Measured live instead (reviewer):

  | viewport | section width | offset from the content axis |
  |---|---|---|
  | 1024px | 760.0px | 0.0px |
  | 1264px | 760.0px | 0.0px |
  | 1904px | 760.0px | 0.0px |
  | 2544px | 760.0px | 0.0px |

  — both `.view-downloads` and `.view-settings`, all four widths, exactly
  760.0px wide and exactly 0.0px off the content axis (the main column
  inside the rail, not the full viewport) every time; the apparent +90px
  offset from the raw VIEWPORT centre at each width is exactly
  `--rail-w`/2 (180px/2), the intended content-vs-viewport asymmetry this
  package's own design relies on, not a recurrence of the 4e.3 defect. The
  reviewer additionally checked the no-tie claim against all 46 selectors
  in the BP-L block (not just the ones this entry names) and ran three
  mutations beyond the coder's own four, all dying on the pins above:
  `margin:0 auto` → `margin:0` (off-centre, hugs the left edge of
  `.view-root`'s own wider box), dropping the `margin` declaration
  entirely (same failure), and swapping `var(--w-text)` for `var(--w-wall)`
  (the exact regression this whole package exists to fix). No survivor
  anywhere; Phase 4e closes on this package.

  Mobile (below BP-L) is untouched: every rule this package adds lives
  inside the existing BP-L `@media` block (`git diff --stat` on `app.css`
  shows only additions inside that block plus two comment-only edits
  elsewhere — no base/BP-M rule's declarations changed). No JS changed:
  neither view's DOM needed restructuring for a plain container-width cap,
  so `web/js/views/downloads.js`/`web/js/views/settings.js` are untouched.

- **D-16 (WP 4h.2 brief drift, caught and corrected — process note, per the
  coordinator's own request to record it here in one sentence).** The WP
  4h.2 brief narrowed `docs/PROJECT_PLAN.md`:1867-1869's requirement (a
  right-hand column at BP-XL AND a collapsible card below that width) down
  to "BP-XL only, absent below it" from memory, and specified a mutation pin
  that would have killed the plan's own second presentation; the coder
  stopped and reported the conflict instead of guessing, and the plan won —
  both presentations were built from one component, per the coordinator's
  follow-up guidance (see the coder's report for the full build). Recorded
  here as the process working as designed, the same posture
  `docs/LEARNINGS.md`'s "Process" section already takes toward earlier
  pipeline incidents.

  **Should-fix S5 (Opus review, fix round): the trade-off this two-
  presentation decision actually costs, named out loud rather than left
  implicit.** Making the panel a normal, DOM-last `<aside>` (not a modal,
  not a live-region-only announcement) means its two buttons — collapse and
  dismiss — are the LAST two tab stops in the entire application, after
  D-14's own ~590-stop inventory of everything else in the shell. That is a
  real discoverability cost for a keyboard-only user: reaching either
  control means tabbing through the whole Library grid first, every time,
  on every page load the panel is visible. Accepted anyway, for two
  reasons: (1) the alternative most naturally suggesting itself —
  rendering the panel EARLY in DOM order (near the top of the Library view,
  so it is a near-immediate tab stop) while keeping it VISUALLY last (via
  CSS `order`/grid placement, the same decoupling `.nav`'s own `order:1`
  already uses elsewhere in this file) — is REJECTED specifically because
  that is the exact divergence Opus review blocker B1 produced BY ACCIDENT
  in this same package: DOM-first-but-visually-elsewhere is precisely what
  auto-placement did to this element before the B1 fix, and D-14's whole
  point is that visual order and reading/focus order should agree, not
  diverge on purpose in the other direction. (2) a supplementary jump
  affordance (a skip-link, or surfacing the panel's dismiss control earlier
  via `aria-owns`/a duplicated reachable control) would need its own
  interaction design and testing this package's scope did not include —
  named here as a real, legitimate follow-up rather than silently absorbed
  into "done".

### WP 4a.1 — Static serving + app shell (api/ + web/) — **first, serial**
- vault-api mounts `web/` (StaticFiles, SPA fallback, sane CSP/security
  headers, no-cache for index). Router auth must NOT cover static assets;
  the app itself authenticates API calls with the key the user enters.
- App shell: nav, views scaffold, dark theme tokens from the mockup, the
  status-icon component (icons + motion incl. reduced-motion), toasts.
- DoD: served page loads against a running vault-api; suite green.

### WP 4a.2 — API client + polling store (web/) — **serial after 4a.1**
- Fetch wrapper (X-Api-Key, error taxonomy, demo mode), polling loops with
  backoff (jobs fast-poll while active; games/clients slow-poll), the
  client-side notification differ (mockup NOTES: poll + diff, no push v1).
- DoD: headless tests (node:test or plain harness) for differ + backoff.

### WP 4a.3 — Library view (web/) — **branch-parallel after 4a.2**
- Grid 2/3/list, search+chips (ANDed), capsule pill (icon+GB, actionable),
  multi-select incl. bulk download split semantics AND bulk delete with
  multiPlan arithmetic (port from the mockup, it is already set-aware),
  in-place progress patching (round-7 rule: never rebuild animated nodes on
  a tick).
### WP 4a.4 — Detail sheet + delete flows (web/) — **serial after 4a.3**
- Depots with computed sharing (holders/sole-holder/orphan wording), delete
  confirm with per-depot freed/kept list, pause/resume/cancel actions,
  GC action per game (dry-run first, show plan, then execute — the API is
  already shaped for this).
### WP 4a.5 — Downloads view (web/) — **branch-parallel after 4a.2**
- Active job card (pause/resume/cancel per 3.12 semantics — note the slot
  decision in api/README, the mockup may diverge), FIFO queue, history with
  log excerpts.
### WP 4a.6 — Settings + onboarding + Steam identity (web/) — **branch-parallel after 4a.2**
- Ports the 3-step onboarding, connection profiles, vault name.
- **DECIDED (user, 2026-08-09): option A with C's input UX** — vault-api
  gains an opt-in relay for `GetOwnedGames`/`GetPlayerSummaries`; the Web
  API key is entered in the web UI settings and stored server-side.
  Boundaries in the ADR-0004 addendum (public-profile reads only, key
  never logged/echoed, relay off until configured, Android kept the
  device-local path at the time — superseded by WP 4h.4 (2026-08-19),
  which moved the app onto this same relay; see ADR-0004 addendum 2). NOTE: the relay endpoint itself is an api/-track
  mini-package (serial against other api/ work); 4a.6 consumes it.
### WP 4a.7 — Notifications + bypass surfaces (web/) — **serial after 4a.3/4a.5 merge**
- Bell + panel with navigate-to-target (round-6 semantics), bypass banner
  + clients sheet backed by the real `/v1/clients` (bypass_suspected from
  WP 3.11).
### WP 4a.8 — End-to-end + a11y pass (web/) — **last, serial**
- Real-server e2e (demo dataset + live vault-api), keyboard navigation,
  screen-reader words for every icon (mockup rule), reduced-motion audit.
- DoD: Phase 4a complete; PLAN checkboxes.

---

## Phase 4b — Android app (track: app)

minSdk 26, Kotlin/Compose, English UI first, System-VPN profile first
(tsnet post-v1). Same design source of truth as 4a. Steam login via
OpenID in a Custom Tab; the PHONE fetches the library from Valve
(no CORS problem natively — ADR-0004 holds unmodified here).

- **WP 4b.1** project skeleton + theming + status-icon component — first.
- **WP 4b.2** API client + connectivity profile abstraction (one interface,
  System-VPN impl; public-domain impl behind it) — serial after 4b.1.
- **WP 4b.3** Steam identity (OpenID + GetOwnedGames on-device) —
  branch-parallel after 4b.2.
- **WP 4b.4** Library (grid/layouts/search/pills/multi-select incl. bulk
  delete) — branch-parallel after 4b.2.
- **WP 4b.5** Downloads + job control — branch-parallel after 4b.2.
- **WP 4b.6** Detail + delete/GC flows — serial after 4b.4.
- **WP 4b.7** Onboarding + settings — branch-parallel after 4b.3.
- **WP 4b.8** Notifications (polling via WorkManager, respecting Doze) —
  serial after 4b.5.
- **WP 4b.10** Clients/bypass detail surface (`GET /v1/clients` sheet,
  closing the WP 4b.8 recorded routing gap: bypass notifications land on
  the sheet directly instead of Settings) — serial after 4b.8. **DONE
  2026-08-17** — see `docs/PROJECT_PLAN.md` §7 Phase 4b evidence notes and
  `app/README.md`'s "Clients sheet (WP 4b.10)" section.
- **WP 4b.9** Release build + signing docs, APK distribution (no Play
  Store requirement; F-Droid long-term) — last. **DONE 2026-08-17** — see
  `docs/PROJECT_PLAN.md` §7 Phase 4b evidence notes and `app/README.md`'s
  "Release build, signing, distribution, and carry-over cleanup (WP 4b.9)"
  section. Carry-over items from reviews, each individually re-verified
  against the code before acting (not assumed from this list's own
  wording):
  - move the unreferenced GalleryScreen (4b.1) and IdentityScreen (4b.3,
    superseded by SettingsScreen in 4b.7) to src/debug/ keeping their
    tests — **done**; GalleryScreen had already moved during WP 4b.4
    itself (this list was never updated to drop it), IdentityScreen moved
    in WP 4b.9;
  - re-check the security-crypto 1.1.0-alpha pin for a GA release — **done
    (reported, not bumped)**: a GA 1.1.0 now exists and is API-compatible
    (verified via the downloaded .aar's class list); the WP 4b.9 brief
    explicitly required reporting + recommending rather than silently
    bumping a security dependency — see `gradle/libs.versions.toml`'s
    comment for the finding and the recommendation to bump in its own
    reviewed change once a device is available to re-verify
    EncryptedCredentialStore against the new artifact;
  - the OpenID state parameter landed in 4b.7 (residual closed) — already
    marked closed before this WP, unchanged;
  - pin the Kotlin LogExcerpt truncation marker to position 0 (web side
    pinned in 4a.8; the 4b.5 twin is still open) — **turned out to already
    be closed since WP 4b.5** (`startsWith`, not `contains`, with its own
    named mutation-pin test in `LogExcerptTest`; `docs/PROJECT_PLAN.md`
    §7's own WP 4b.5 evidence note already said so) — this list entry was
    stale, not open work;
  - release notification icon art (4b.8 reuses the launcher monochrome) —
    **decided for v1**: kept, as a deliberate final decision rather than a
    standing "revisit" pointer — the launcher's monochrome layer is
    already the flat white-on-transparent silhouette Android's own
    notification-icon guidance asks for, not merely convenient reuse; a
    bespoke glyph is a general future art-pass item, not a WP 4b.9
    blocker.

---

## Phase 5 — Community release (track: release)

- **WP 5.1 — CI** (`.github/`) — **DONE 2026-08-09** (was: dispatchable NOW, parallel to everything):
  GitHub Actions running api pytest (Linux), agent `go build/vet/test`
  (Linux + Windows), `nginx -t` on rendered core templates, PS 5.1 syntax
  checks for packaging scripts. NO image publishing here. Container-network
  dependent tests (real CDN) stay local-only — mark them skipped-in-CI
  honestly.
- **WP 5.2 — README + architecture diagram + 5-minute quickstart** — parallel;
  MUST include the works-for-guests FAQ (ADR-0001 store-on-miss) already
  outlined in PLAN Phase 5.
- **WP 5.3 — SECURITY.md + threat model + pre-release security review** —
  after code freeze of api/core; **[Fable] mandatory** (standing policy).
  Freeze declared 2026-10-01 (ADR-0016, commit 92e95be); the second pass
  runs on the strongest available model, Opus 5.5 on that date (Fable
  unavailable, user decision not to wait).
- **WP 5.4 — CONTRIBUTING.md, issue templates, example configs** — parallel.
- **WP 5.5 — Multi-arch images to ghcr.io** — **DECIDED (user,
  2026-08-09): move to a GitHub organization.** Target naming:
  `ghcr.io/steamvault/{core,api,dns}` (public) — as shipped after the
  rename (ADR-0013) and the org move: `ghcr.io/steamhangar/vault-{core,
  api,dns,proxy}` (see `deploy/compose.yaml`). Prerequisite the USER
  performs (account-level, never autonomous): create the org — name
  availability decides the final slug — and move the repo into it
  (GitHub auto-redirects the old URL). After the move: update the git
  remote, README badges/links, compose image references; publishing
  itself stays a with-the-user step.
- **WP 5.6 — Announcement** (r/selfhosted, r/homelab, LanCache Discord;
  complement-not-killer framing) — **with the user only. DECIDED gate
  (user, 2026-08-09): no announcement until the user has personally
  tested the stack end-to-end WITH the Android app** — i.e. after the
  first real-world rollout AND Phase 4b are usable; the post carries the user's own
  real-world numbers.

---

## Deployment (not a branch package)

The first real-world rollout is a joint interactive session with the
operator (SSH, the host's container-manager stack, an IP alias on
`<lan-interface>`, a DNS rewrite in the LAN resolver, a dedicated dataset
such as `zfs create -o recordsize=1M -o atime=off -o compression=off
<pool>/<cache-dataset>`, `VAULT_CACHE_PATH`). Two operator-side network
blockers noted earlier: a VPN subnet router still advertises a stale
subnet route, and the router announces itself as IPv6 DNS via RA (live
IPv6 bypass) — fix both before measuring hit rates.

## Parallelism map (what can be in flight at once)

```
NOW (main session):        3.10 (core)   3.12 (api)   mockup R7 (scratchpad)
dispatchable in branches:  3.9 (api, new-module isolation)   5.1 (.github)   5.2/5.4 (root docs)
after 3.12 merges:         3.11 (api)  → then 3.13 (api)
after 3.10+3.11+3.12+3.13: Phase 3 COMPLETE
4a: 4a.1 → 4a.2 → {4a.3, 4a.5, 4a.6(decision pending)} → 4a.4, 4a.7 → 4a.8
4b: 4b.1 → 4b.2 → {4b.3, 4b.4, 4b.5} → 4b.6, 4b.7, 4b.8 → 4b.10 → 4b.9
5.3 after api/core freeze · 5.5/5.6 gated on user
4a and 4b tracks are independent of each other and of Phase-3 remainder
(they consume the HTTP API only) — but 4a.5/4b.5 want 3.12's semantics
merged first, and 4a.7 wants 3.11.
```

## User decisions — all resolved 2026-08-09

1. **4a.6** Steam library in the browser: **A with C's input UX** (opt-in
   vault-api relay, key entered in the web UI, stored server-side —
   ADR-0004 addendum).
2. **5.5** ghcr/repo home: **move to a GitHub organization**
   (decided as `ghcr.io/steamvault/*`, shipped as `ghcr.io/steamhangar/*`
   after the rename; org creation + repo move are user-performed
   account actions).
3. **5.6** announcement: **only after the user has personally tested
   end-to-end with the Android app** (post-rollout, post-4b).

Post-v1 backlog (documented, undispatched): embedded tailscale (tsnet),
iOS app, queue reordering, drag-to-reorder, server-side notification
cursor, per-depot grace windows.

## Carry-overs (post-2026-08-09)

This file predates Phase 4c/4d/4e/4f dispatch (all landed after this file's
last edit) and has no register for them elsewhere — this section is the
first entry, added on request (N6, WP 4f review round) rather than backfilling
every later phase's history.

1. **WP 4f (api/, done) — web-side carry-over (B2, reviewer 2026-08-18),
   blocked on WP 4e.1 (`web/`, in flight).** `web/js/demo-data.js`'s
   `selectCachedAppids()` still implements the pre-WP-4f generous "any
   mapped depot with bytes" rule, and its docstring cites it as the CURRENT
   real rule via a README heading WP 4f renamed. `web/tests/README.md`
   repeats the stale framing. Fix shape (docs/PROJECT_PLAN.md's WP 4f entry
   has the full detail): hoist the DELETE handler's local
   `otherOwners`/`hasCacheContent` helpers to module scope and change
   `selectCachedAppids()`'s filter to "exclusive or last-cached-remnant",
   matching `deletion.appids_with_cache_content`'s real predicate. Not
   fixed by WP 4f itself — `web/` was occupied by WP 4e.1 at review time.
   WP 4e.1 merged (commit 07865a3) without picking it up. **Closed
   2026-10-01 by WP WEB-FIX-1 (commit 9572e6f)**, a fix package from the
   pre-freeze project review (`docs/PROJECT_PLAN.md` §11 item 12) — open
   since 2026-08-18.

### D-17 — header art in the detail card (WP 4h.3, 2026-08-19; superseded by WP 4h.5, 2026-08-19)

The frozen mockup's detail sheet body starts with `.dhead`
(`docs/design/vault-app-mockup.html:1993-1994`) — no hero image at any
width, and the new rule is not breakpoint-scoped, so this changes the
phone shape the mockup does define. Plan-authorized (the 4h.3 item calls
for header art explicitly); recorded per the D-12/D-15 precedent that
plan-driven-but-mockup-absent visuals get a register entry.

**Original WP 4h.3 design (superseded, kept for the record):**
aspect-ratio 460/215 was the only space reservation, held open from the
instant the wrapper was inserted (before the image had even started
loading); on error the whole wrapper was removed, collapsing that
reservation in one step. Known, accepted at the time: at the mockup's
390px frame the banner reserved ~165px above `.dhead` before a 404's
collapse fired — operator glance at phone width recommended before
release (review S4).

**Superseded by WP 4h.5: the operator took that glance and rejected the
result.** The reserve-then-shrink shape was seen collapsing visibly on a
real phone — a delisted title showed the ~165px band above, then it
snapped shut once the 404 arrived. The operator's own framing: "default
off, and load it in if there is one." WP 4h.5 replaces the design
outright: `.header-art` now reserves NOTHING before the image is known
good (a `grid-template-rows:0fr` box, no `aspect-ratio`/`height` on the
wrapper itself), and grows open to the `<img>`'s own true 460/215 box only
once `img.decode()` (or, as a fallback, the `load` event) confirms the
image is ready to paint. A 404/delisted title now reserves and reveals
nothing at all — there is no band to collapse, because none ever
appeared. Consequently the phone-width concern recorded above no longer
applies and is not carried forward: under load-then-reveal, a delisted
title at any width shows no banner and no reserved space, ever; a real
image grows in, once, only if it arrives after the sheet is already
visible. See `web/tests/README.md`'s WP 4h.5 section for the CSS/JS
mechanism and its test pins.
