/**
 * Pointer and keyboard interaction model (WP 4e.4).
 *
 * The WP 4e.1-4e.3 packages built the desktop SHELL (rail, wide grid,
 * centred/drawer overlays) but left it largely touch-first underneath: most
 * of this codebase's hover treatments were bare `:hover` rules with no
 * `(hover:hover) and (pointer:fine)` gate (the ONE exception, `.nav-btn:hover`
 * in `css/app.css`'s BP-L block, was this file's own precedent for the split
 * this WP generalizes), several interactive controls sit inside an
 * `overflow:hidden` ancestor that clips the global `:focus-visible` ring, and
 * nothing pinned the nested sheet+confirm keyboard flow end to end (only its
 * primitive, `lib/modal-stack.js`, had direct coverage — see
 * `modal-stack.test.js`).
 *
 * **Opus review round 1: FAIL.** A first draft of this file's CSS-side fix
 * relocated all 11 pre-existing hover rules into ONE trailing block instead
 * of gating each in place. The rule TEXT never changed, only its position —
 * but for five pairs, an equal-specificity "state" rule (`.iconbtn.on`,
 * `.segs button[aria-pressed]`, `.chip[aria-pressed]`, `.btn:disabled`,
 * `.notif.unread`) used to WIN on hover only because it was written AFTER
 * its element's `:hover` rule; moving every hover rule to the end of the
 * file made the hover rule "later" instead, flipping every one of those
 * ties (a disabled button visibly re-enabling under the cursor was the
 * sharpest example). Every hover rule in `css/app.css` is now gated IN
 * PLACE, at its original source position, specifically so this class of
 * regression cannot happen — see each rule's own comment for its
 * reasoning. Section 1 below is the cascade-OUTCOME pin the review asked
 * for (checking source ORDER for the five order-dependent pairs, not
 * merely "the rule text is somewhere behind a gate"), plus the sixth pair
 * this review found in the ORIGINAL `.nav-btn:hover` precedent itself
 * (`.nav-btn[aria-current="page"]`, fixed with a higher-specificity
 * override instead of reordering, since the BP-L breakpoint block cannot
 * structurally move before the base rule it would need to precede).
 *
 * Four kinds of test below, same split as `css-hygiene.test.js`/
 * `css-layout-foundation.test.js` (structural CSS static analysis) plus a new
 * fake-DOM behavioural pin (same harness as `dialog-wiring.test.js`/
 * `modal-stack.test.js`):
 *
 * 1. Every `:hover` rule in `css/app.css`/`css/theme.css` lives inside a
 *    media context that requires BOTH `(hover:hover)` and `(pointer:fine)`
 *    (touch devices, including the "sticky hover after tap" failure mode
 *    some mobile browsers exhibit, must never match any of them) — AND,
 *    for the six named pairs above, that the equal-specificity state rule
 *    still resolves to the value that must win when both apply at once
 *    (source order for five of them, a higher-specificity override for the
 *    sixth).
 * 2. The `:focus-visible` convention: the base rule (`theme.css`) is a bare,
 *    universal selector so every focusable element gets it for free, no real
 *    interactive class suppresses it outright (the ONE real exception —
 *    `.search input`/`.inp input`'s `outline:none` — is a deliberate,
 *    documented substitution via `:focus-within` on the wrapper, not a
 *    removed indicator, and is asserted as exactly that), and the three
 *    known overflow-clipped containers (`.segs button`, `.hrow > button`,
 *    `.depotwrap.sh .depot`) each carry their own `outline-offset:-2px`
 *    override so the ring is not invisible. Also pins B2 (Opus review
 *    must-fix): `.bulk`'s two action buttons must leave the tab order while
 *    the bar itself is invisible (`opacity:0`/`pointer-events:none` alone
 *    do NOT remove a `<button>` from Tab order — measured live: Tab from
 *    the last library card used to land on an invisible Cancel, then an
 *    invisible Download, then BODY).
 * 3. The reduced-motion override (`theme.css`) is the universal wildcard
 *    `*, *::before, *::after` — so every hover-triggered transition this WP
 *    rides on (e.g. `.cap`'s pre-existing `transition:box-shadow` now also
 *    triggered by the new `.card:hover .cap` rule) is covered by
 *    construction, with no per-rule enumeration to keep in sync.
 * 4. A fake-DOM behavioural pin (new) walking the sheet+nested-confirm
 *    keyboard flow — the exact shape every real delete-confirm dialog in
 *    this codebase uses (`views/library.js`'s bulk delete, `components/
 *    game-detail-sheet.js`'s delete/GC-execute confirms) — via `click`
 *    (native Enter/Space-on-a-button activation, which is a browser-level
 *    guarantee for a real `<button>`, not something this harness re-tests)
 *    and `keydown` `Escape`/`Tab` events only, asserting the confirm
 *    receives focus on open and Escape unwinds inner-first.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { createFakeDom, fakeKeyEvent, fakeClickEvent } from "./fake-dom.js";
import { pushModal, popModal, resetModalStack } from "../js/lib/modal-stack.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const webDir = path.join(__dirname, "..");
const appCssPath = path.join(webDir, "css", "app.css");
const themeCssPath = path.join(webDir, "css", "theme.css");

const appCss = readFileSync(appCssPath, "utf8");
const themeCss = readFileSync(themeCssPath, "utf8");

// ---------------------------------------------------------------------
// CSS parsing: rule -> its enclosing @media header CHAIN, plus its source
// character INDEX (needed for the cascade-outcome order checks below — an
// equal-specificity tie resolves by whichever rule is textually LATER).
// There is never more than one level of @media nesting in this codebase's
// stylesheets, but the chain is tracked generally rather than assuming that
// stays true. Self-contained rather than imported from css-hygiene.test.js/
// css-layout-foundation.test.js: neither exports its parsing helpers, and
// this codebase's own convention (established by those two files, which
// each carry their own copy) is a small enough parser per file rather than
// a shared module — see css-hygiene.test.js's header for why this codebase
// has not built a real CSS parser at all.
// ---------------------------------------------------------------------

function stripComments(css) {
  return css.replace(/\/\*[\s\S]*?\*\//g, "");
}

/** @returns {Array<{selector: string, body: string, mediaStack: string[], index: number}>} */
function extractRulesWithMediaContext(cssText) {
  const css = stripComments(cssText);
  const rules = [];

  function parseBlock(text, start, end, mediaStack) {
    let j = start;
    while (j < end) {
      while (j < end && /\s/.test(text[j])) j++;
      if (j >= end) break;
      const braceIdx = text.indexOf("{", j);
      if (braceIdx === -1 || braceIdx >= end) break;
      const header = text.slice(j, braceIdx).trim();
      const headerIndex = j;
      let depth = 1;
      let k = braceIdx + 1;
      while (depth > 0 && k < end) {
        if (text[k] === "{") depth++;
        else if (text[k] === "}") depth--;
        k++;
      }
      const body = text.slice(braceIdx + 1, k - 1);
      if (/^@media/.test(header)) {
        parseBlock(text, braceIdx + 1, k - 1, [...mediaStack, header]);
      } else if (/^@supports/.test(header)) {
        parseBlock(text, braceIdx + 1, k - 1, mediaStack); // not a media gate
      } else if (/^@keyframes/.test(header)) {
        // skip — percentage/from/to are not selectors
      } else if (header.startsWith("@")) {
        // unknown at-rule — skip defensively
      } else {
        for (const sel of header.split(",")) {
          const trimmed = sel.trim();
          if (trimmed) rules.push({ selector: trimmed, body, mediaStack, index: headerIndex });
        }
      }
      j = k;
    }
  }

  parseBlock(css, 0, css.length, []);
  return rules;
}

/**
 * Whether `mediaStack` includes a media condition requiring BOTH
 * `(hover:hover)` and `(pointer:fine)` in the SAME and-chain.
 *
 * Two nitpicks fixed here (Opus review, WP 4e.4 fix round), both closed by
 * anchoring each feature check to its own parenthesis rather than a bare
 * substring test:
 * - `(any-hover: hover)` is a REAL, DIFFERENT media feature — the old bare
 *   `/hover\s*:\s*hover/` regex matched it anyway, because "any-hover: hover"
 *   contains the literal substring "hover: hover" starting right after the
 *   "any-" prefix. Requiring the feature name to start immediately after an
 *   opening paren (`\(\s*hover\s*:`) rejects it: "any-hover" never has
 *   nothing-but-whitespace between "(" and "hover".
 * - A COMMA in a media query is OR, not AND (`@media (hover:hover),
 *   (pointer:fine)` matches on a coarse-pointer touch device alone, via the
 *   second branch, defeating the whole gate) — the old check ran two
 *   independent `.test()` calls against the WHOLE header string, so it
 *   would have been satisfied by either substring appearing in EITHER
 *   branch. Splitting the header on top-level commas first, then requiring
 *   BOTH features inside the SAME branch, rejects that shape too.
 */
function isHoverPointerGated(mediaStack) {
  const HOVER_RE = /\(\s*hover\s*:\s*hover\s*\)/;
  const POINTER_RE = /\(\s*pointer\s*:\s*fine\s*\)/;
  return mediaStack.some((h) => {
    const body = h.replace(/^@media\s*/, "");
    // No media condition in this codebase's own CSS ever needs a literal
    // comma INSIDE a feature's parens (width/hover/pointer values are all
    // bare tokens) — a plain top-level split is exact for every real and
    // fixture case this file uses.
    return body.split(",").some((branch) => HOVER_RE.test(branch) && POINTER_RE.test(branch));
  });
}

const combinedCss = `${themeCss}\n${appCss}`;
const allRules = extractRulesWithMediaContext(combinedCss);
const hoverRules = allRules.filter((r) => r.selector.includes(":hover"));

// ---------------------------------------------------------------------
// 1a. Hover rules are gated behind (hover:hover) and (pointer:fine).
// ---------------------------------------------------------------------

test("real tree: every :hover rule in app.css/theme.css is gated behind (hover:hover) and (pointer:fine)", () => {
  const ungated = hoverRules.filter((r) => !isHoverPointerGated(r.mediaStack));
  assert.deepEqual(
    ungated.map((r) => r.selector),
    [],
    `hover rule(s) not gated behind (hover:hover) and (pointer:fine) — touch devices could inherit sticky hover styling: ${JSON.stringify(ungated.map((r) => r.selector))}`,
  );
});

// Sanity: proves the scan actually found real work (not vacuously passing
// because the regex never matches anything) — the exact count is not pinned
// (that would just be re-typing the CSS), but a minimum floor is, so a
// future refactor that accidentally stops finding any hover rule at all
// (e.g. a typo'd selector split) is caught.
test("sanity: at least a dozen real :hover rules were found and checked (the scan is not vacuous)", () => {
  assert.ok(hoverRules.length >= 12, `expected >= 12 :hover rules, found ${hoverRules.length}`);
});

// MUTATION-PROOF (not the real tree — a synthetic fixture proving the
// checker above actually CAN fail): a properly AND-gated rule, a
// width-only-gated rule (the pre-WP-4e.4 shape every rule this WP touched
// used to have), a top-level rule with no @media at all, an `any-hover`
// false-positive probe, and a comma-OR probe — the two review nitpicks.
test("mutation-proof: the hover-gate checker rejects every ungated shape, including the two review nitpicks", () => {
  const fixture = `
    @media (hover:hover) and (pointer:fine){
      .probe-gated:hover{ color:red; }
    }
    @media (min-width:1024px){
      .probe-width-only:hover{ color:red; }
    }
    .probe-top-level:hover{ color:red; }
    @media (any-hover: hover) and (pointer:fine){
      .probe-any-hover:hover{ color:red; }
    }
    @media (hover:hover), (pointer:fine){
      .probe-comma-or:hover{ color:red; }
    }
  `;
  const rules = extractRulesWithMediaContext(fixture).filter((r) => r.selector.includes(":hover"));
  const bySelector = new Map(rules.map((r) => [r.selector, r]));
  assert.equal(isHoverPointerGated(bySelector.get(".probe-gated:hover").mediaStack), true);
  assert.equal(isHoverPointerGated(bySelector.get(".probe-width-only:hover").mediaStack), false);
  assert.equal(isHoverPointerGated(bySelector.get(".probe-top-level:hover").mediaStack), false);
  assert.equal(
    isHoverPointerGated(bySelector.get(".probe-any-hover:hover").mediaStack),
    false,
    "(any-hover: hover) is a DIFFERENT media feature from (hover:hover) and must not satisfy the gate",
  );
  assert.equal(
    isHoverPointerGated(bySelector.get(".probe-comma-or:hover").mediaStack),
    false,
    "a comma is OR, not AND — (hover:hover), (pointer:fine) matches on EITHER alone and must not satisfy the gate",
  );
});

// Named pin (brief requirement, same convention as css-hygiene.test.js's
// "named pin" tests): the ONE pre-existing exception this WP's own header
// cites as its precedent must still exist and still be gated — a
// regression here would mean the precedent itself broke, not just a new
// rule going ungated.
test("named pin: .nav-btn:hover (the pre-existing precedent this WP generalizes) is still hover/pointer-gated", () => {
  const navBtnHover = hoverRules.find((r) => r.selector === ".nav-btn:hover");
  assert.ok(navBtnHover, ".nav-btn:hover rule not found at all");
  assert.equal(isHoverPointerGated(navBtnHover.mediaStack), true);
});

// Named pins for the new affordances this WP adds (brief item 2's
// "previously missing" half) — each must exist AND be gated, not merely
// exist unconditionally (which would silently re-introduce the sticky-hover
// risk for a brand new rule).
for (const selector of [
  ".hrow > button:hover",
  ".banner .acts button:hover",
  ".icnact:hover",
  ".card:hover .cap",
  ".grid.list .card:hover",
]) {
  test(`named pin: new hover affordance ${selector} exists and is hover/pointer-gated`, () => {
    const rule = hoverRules.find((r) => r.selector === selector);
    assert.ok(rule, `${selector} not found`);
    assert.equal(isHoverPointerGated(rule.mediaStack), true);
  });
}

// ---------------------------------------------------------------------
// 1b. Cascade-OUTCOME pins (Opus review B1 blocker fix). Existence-and-gated
// is not enough — for an equal-specificity state-vs-hover pair, the STATE
// rule must still WIN when both apply (hovering an already-"on"/"pressed"/
// "unread"/"disabled" element). Five pairs resolve by SOURCE ORDER (the
// state rule must appear strictly AFTER its hover rule); the sixth
// (`.nav-btn`) cannot use order at all (the BP-L breakpoint block the hover
// rule lives in structurally cannot move before the base rule it would
// need to precede) and is fixed with a higher-specificity override instead
// — checked by specificity math and the override's own existence, not by
// position.
// ---------------------------------------------------------------------

const ORDER_DEPENDENT_PAIRS = [
  { hover: ".iconbtn:hover", state: ".iconbtn.on" },
  { hover: ".segs button:hover", state: '.segs button[aria-pressed="true"]' },
  { hover: ".chip:hover", state: '.chip[aria-pressed="true"]' },
  { hover: ".btn:hover", state: ".btn:disabled" },
  { hover: ".btn:hover", state: '.btn[aria-disabled="true"]' },
  { hover: ".notif:hover", state: ".notif.unread" },
];

for (const { hover, state } of ORDER_DEPENDENT_PAIRS) {
  test(`cascade outcome: ${state} still wins over ${hover} on hover (source order preserved)`, () => {
    const hoverRule = allRules.find((r) => r.selector === hover);
    const stateRule = allRules.find((r) => r.selector === state);
    assert.ok(hoverRule, `${hover} rule not found`);
    assert.ok(stateRule, `${state} rule not found`);
    assert.ok(
      stateRule.index > hoverRule.index,
      `${state} (index ${stateRule.index}) must appear AFTER ${hover} (index ${hoverRule.index}) in source — ` +
        `equal specificity means source order decides the tie, and this pair must resolve in favour of the STATE, ` +
        `not the hover, when an element is both hovered and in that state`,
    );
  });
}

// MUTATION-PROOF: the order check above is not vacuously true — proven by
// swapping which rule comes first in a synthetic fixture.
test("mutation-proof: the cascade-order check correctly flags a state rule placed BEFORE its hover rule", () => {
  const wrongOrderFixture = `
    .probe.on{ color:blue; }
    .probe:hover{ color:red; }
  `;
  const rightOrderFixture = `
    .probe:hover{ color:red; }
    .probe.on{ color:blue; }
  `;
  const wrong = extractRulesWithMediaContext(wrongOrderFixture);
  const right = extractRulesWithMediaContext(rightOrderFixture);
  const wrongHover = wrong.find((r) => r.selector === ".probe:hover");
  const wrongState = wrong.find((r) => r.selector === ".probe.on");
  const rightHover = right.find((r) => r.selector === ".probe:hover");
  const rightState = right.find((r) => r.selector === ".probe.on");
  assert.equal(wrongState.index > wrongHover.index, false, "the wrong-order fixture must be detected as wrong");
  assert.equal(rightState.index > rightHover.index, true, "the right-order fixture must be detected as right");
});

// The sixth pair — `.nav-btn`, fixed by specificity, not order.
test("cascade outcome: .nav-btn[aria-current=\"page\"] keeps its accent colour under hover via a higher-specificity override", () => {
  const override = allRules.find((r) => r.selector === '.nav-btn[aria-current="page"]:hover');
  assert.ok(
    override,
    '.nav-btn[aria-current="page"]:hover override not found — the current-page rail button would lose its accent ' +
      "colour the instant a mouse hovers it",
  );
  assert.match(override.body, /color\s*:\s*var\(--accent\)/);
  assert.equal(isHoverPointerGated(override.mediaStack), true, "the override itself must stay behind the same gate");
  // Specificity check, not a hand-wave: three simple/compound selector
  // components (.nav-btn, [aria-current="page"], :hover) each count once
  // toward the (0,3,0) class/attribute/pseudo-class tier — strictly more
  // than the plain `.nav-btn:hover` rule's (0,2,0), so this wins regardless
  // of source order, which is the whole point (the BP-L block cannot be
  // reordered before the base `.nav-btn[aria-current="page"]` rule).
  function classTierCount(selector) {
    const matches = selector.match(/(\.[\w-]+|\[[^\]]+\]|:[\w-]+(?:\([^)]*\))?)/g) || [];
    return matches.length;
  }
  const plainHover = allRules.find((r) => r.selector === ".nav-btn:hover");
  assert.ok(plainHover, ".nav-btn:hover rule not found");
  assert.ok(
    classTierCount(override.selector) > classTierCount(plainHover.selector),
    `expected ${override.selector} (${classTierCount(override.selector)} components) to outrank ` +
      `${plainHover.selector} (${classTierCount(plainHover.selector)})`,
  );
});

// ---------------------------------------------------------------------
// 2. Focus-visible convention.
// ---------------------------------------------------------------------

test("the base :focus-visible rule is a bare, universal selector (theme.css) using the accent token", () => {
  const rule = allRules.find((r) => r.selector === ":focus-visible");
  assert.ok(rule, "bare :focus-visible rule not found in theme.css");
  assert.match(rule.body, /outline\s*:\s*2px\s+solid\s+var\(--accent\)/);
});

// "the real cross-section of selectors, not one hand-picked example" (brief,
// Pins section) — a representative set of interactive classes spanning
// EVERY view this WP's inventory covered (library card/segmented control/
// filter chip, downloads history row, settings input wrapper, notifications
// row, detail-sheet depot row, bulk bar button, rail nav button). None of
// them may carry a rule that sets `outline` to `none`/`0` — that would
// silently defeat the global ring for exactly that class regardless of
// whether the base rule itself stays intact.
const FOCUS_CROSS_SECTION = [
  ".btn",
  ".chip",
  ".nav-btn",
  ".card",
  ".notif",
  ".segs button",
  ".depotwrap.sh .depot",
  ".hrow > button",
  ".iconbtn",
  ".qx",
];
test("real cross-section: no rule for a representative set of interactive classes across every view sets outline:none/0", () => {
  const offenders = [];
  for (const cls of FOCUS_CROSS_SECTION) {
    for (const rule of allRules) {
      if (rule.selector !== cls) continue;
      if (/outline\s*:\s*(none|0)\b/.test(rule.body)) offenders.push({ selector: rule.selector, body: rule.body });
    }
  }
  assert.deepEqual(offenders, [], `interactive class(es) suppressing outline entirely: ${JSON.stringify(offenders)}`);
});

// The one real, deliberate exception — documented so it cannot be mistaken
// for an oversight the cross-section pin above should have caught (it is
// intentionally NOT in FOCUS_CROSS_SECTION: these two are text inputs whose
// wrapper substitutes a `:focus-within` border-color change for the ring,
// same visible "something changed" contract, just styled differently — see
// css/app.css's ".search"/".inp" sections, WP 4a.3/4a.6, both pre-existing).
test("documented exception: .search input / .inp input suppress outline but their wrapper shows :focus-within instead", () => {
  // Both selectors are comma-list members of a combined rule in the real
  // CSS (`.search input{...}` standalone, `input.inp, .inp input{...}`
  // together) — extractRulesWithMediaContext splits comma lists into one
  // entry per compound selector, so each is looked up on its own.
  for (const selector of ["input.inp", ".inp input", ".search input"]) {
    const rule = allRules.find((r) => r.selector === selector);
    assert.ok(rule, `${selector} rule not found — has this selector's text changed?`);
    assert.match(rule.body, /outline\s*:\s*none/);
  }
  const searchWrapFocus = allRules.find((r) => r.selector === ".search:focus-within");
  const inpWrapFocus = allRules.find((r) => r.selector === ".inp:focus-within");
  assert.ok(searchWrapFocus && /border-color/.test(searchWrapFocus.body), ".search:focus-within must still change border-color");
  assert.ok(inpWrapFocus && /border-color/.test(inpWrapFocus.body), ".inp:focus-within must still change border-color");
});

// Named pins: the three overflow-clipped containers this WP found (their
// interactive child fills the row/track edge to edge inside an
// `overflow:hidden` ancestor, clipping the default OUTSIDE ring) each carry
// their own inset override. Each asserted separately so deleting any ONE
// fails by name, not merely a combined count.
for (const selector of [".segs button:focus-visible", ".hrow > button:focus-visible", ".depotwrap.sh .depot:focus-visible"]) {
  test(`named pin: ${selector}{ outline-offset:-2px } exists (overflow-clipped ancestor fix)`, () => {
    const rule = allRules.find((r) => r.selector === selector);
    assert.ok(rule, `${selector} rule not found`);
    assert.match(rule.body, /outline-offset\s*:\s*-2px/);
  });
}

// MUTATION-PROOF: the checker used by the tests above genuinely tells apart
// "has the override" from "does not" — proven on synthetic text, not by
// re-reading the real file a second time.
test("mutation-proof: the outline-offset check distinguishes a present override from a missing one", () => {
  const fixture = `
    .probe-fixed:focus-visible{ outline-offset:-2px; }
    .probe-unfixed{ color:red; }
  `;
  const rules = extractRulesWithMediaContext(fixture);
  const fixed = rules.find((r) => r.selector === ".probe-fixed:focus-visible");
  const unfixed = rules.find((r) => r.selector === ".probe-unfixed");
  assert.match(fixed.body, /outline-offset\s*:\s*-2px/);
  assert.ok(!unfixed || !/outline-offset\s*:\s*-2px/.test(unfixed.body));
});

// ---------------------------------------------------------------------
// 2b. B2 (Opus review must-fix): `.bulk`'s two action buttons must leave the
// tab order while the bar is invisible — `opacity:0`/`pointer-events:none`
// alone do not do this; `visibility` does, per spec, for both the tab order
// and the accessibility tree.
// ---------------------------------------------------------------------

test("B2: .bulk is visibility:hidden while closed and .bulk.up restores visibility:visible", () => {
  const closed = allRules.find((r) => r.selector === ".bulk");
  const open = allRules.find((r) => r.selector === ".bulk.up");
  assert.ok(closed, ".bulk rule not found");
  assert.ok(open, ".bulk.up rule not found");
  assert.match(
    closed.body,
    /visibility\s*:\s*hidden/,
    ".bulk must be visibility:hidden while closed, or its Cancel/Download buttons stay reachable by Tab while invisible " +
      "(measured live: Tab from the last library card landed on an invisible Cancel, then Download, then BODY)",
  );
  assert.match(open.body, /visibility\s*:\s*visible/, ".bulk.up must restore visibility:visible while the bar is shown");
});

// MUTATION-PROOF: proves the check above actually distinguishes "has the
// visibility toggle" from "relies on opacity/pointer-events alone".
test("mutation-proof: the B2 check fails on the pre-fix shape (opacity/pointer-events only, no visibility)", () => {
  const fixture = `
    .probe-bar{ opacity:0; pointer-events:none; }
    .probe-bar.up{ opacity:1; pointer-events:auto; }
  `;
  const rules = extractRulesWithMediaContext(fixture);
  const closed = rules.find((r) => r.selector === ".probe-bar");
  const open = rules.find((r) => r.selector === ".probe-bar.up");
  assert.equal(/visibility\s*:\s*hidden/.test(closed.body), false);
  assert.equal(/visibility\s*:\s*visible/.test(open.body), false);
});

// ---------------------------------------------------------------------
// 3. Reduced-motion coverage is a wildcard, so it covers every hover-
//    triggered transition automatically — including transitions this WP
//    never touched directly (`.cap`'s pre-existing `transition:box-shadow`,
//    now also triggered by the new `.card:hover .cap` rule, and `.bulk`'s
//    new `transition:...,visibility .18s`).
// ---------------------------------------------------------------------

test("the reduced-motion override is the universal wildcard *, *::before, *::after with !important on transition/animation duration", () => {
  const inReducedMotion = (r) => r.mediaStack.some((h) => /prefers-reduced-motion/.test(h));
  // Scoped to the reduced-motion media context specifically — theme.css also
  // has an UNRELATED top-level `*, *::before, *::after{ box-sizing:border-box; }`
  // rule (no media query at all) that a bare `selector === "*"` lookup would
  // find FIRST (it appears earlier in the file), silently checking the wrong
  // rule's body entirely.
  const rule = allRules.find((r) => r.selector === "*" && inReducedMotion(r));
  assert.ok(rule, "no wildcard '*' rule found inside the reduced-motion block at all");
  // All three selectors must resolve to the SAME rule body (one comma-
  // separated rule, not three independent ones that could drift apart).
  const wildcardSelectors = new Set(
    allRules.filter((r) => r.body === rule.body && inReducedMotion(r)).map((r) => r.selector),
  );
  for (const sel of ["*", "*::before", "*::after"]) {
    assert.ok(wildcardSelectors.has(sel), `reduced-motion wildcard is missing the ${sel} branch`);
  }
  assert.match(rule.body, /transition-duration\s*:\s*\.001ms\s*!important/);
  assert.match(rule.body, /animation-duration\s*:\s*\.001ms\s*!important/);
});

// MUTATION-PROOF: if the override ever stopped being a wildcard (e.g.
// narrowed to a hand-picked class list), this fixture-based check proves
// the ABOVE test would actually notice — a new hover rule's transition
// (e.g. a hypothetical `.probe-hover-target`) would silently escape a
// narrowed selector, which the wildcard, by definition, cannot happen to.
test("mutation-proof: a narrowed (non-wildcard) reduced-motion selector would NOT cover an arbitrary new class", () => {
  const wildcardFixture = "*, *::before, *::after{ transition-duration:.001ms !important; }";
  const narrowedFixture = ".btn, .chip{ transition-duration:.001ms !important; }";
  const coversArbitraryClass = (css) => {
    const rules = extractRulesWithMediaContext(css);
    return rules.some((r) => r.selector.split(",").map((s) => s.trim()).includes("*"));
  };
  assert.equal(coversArbitraryClass(wildcardFixture), true);
  assert.equal(coversArbitraryClass(narrowedFixture), false);
});

// ---------------------------------------------------------------------
// 4. Fake-DOM behavioural pin: the sheet + nested-confirm keyboard flow.
//
// Mirrors the EXACT shape every real delete-confirm in this codebase uses
// (views/library.js's bulk delete, components/game-detail-sheet.js's delete
// AND GC-execute confirms — see those modules' own comments): a
// createSheetDialog() instance, and a bespoke `.dialog-backdrop`/`.dialog`
// pushed onto the SAME lib/modal-stack.js stack, with its own safe-default
// button focused on open and the invoker's focus restored on close. Not a
// new mechanism — a regression pin for the composed behaviour, built from
// the same primitives `modal-stack.test.js` already unit-tests in isolation
// and `dialog-wiring.test.js` already unit-tests for a single (non-nested)
// sheet. What is new here: NESTING a real confirm on top of a real sheet and
// walking the whole open -> Escape -> Escape flow by simulated input only.
// ---------------------------------------------------------------------

const dom = createFakeDom();
globalThis.document = dom.document;
globalThis.window = dom.window;

const { createSheetDialog } = await import("../js/components/sheet-dialog.js");

beforeEach(() => {
  resetModalStack(dom.document);
});

/** Builds a confirm dialog wired EXACTLY like game-detail-sheet.js's
 * delete-confirm (module-level `document.body` child, `pushModal`/
 * `popModal`, safe-default focus on open, invoker focus restored on close) —
 * reproduced here rather than imported, since game-detail-sheet.js itself is
 * a singleton DOM-building component this codebase deliberately does not
 * unit-test directly (see sheet-dialog.js's header) and pulls in
 * store-singleton.js/api.js, which need a real fetch-capable environment
 * this bare-Node harness does not provide. */
function buildConfirmDialog() {
  const backdrop = dom.document.createElement("div");
  backdrop.className = "dialog-backdrop";
  const keepBtn = dom.document.createElement("button");
  const deleteBtn = dom.document.createElement("button");
  backdrop.append(keepBtn, deleteBtn);
  dom.document.body.appendChild(backdrop);

  let invokerEl = null;
  function open() {
    invokerEl = dom.document.activeElement;
    backdrop.classList.add("on");
    pushModal(backdrop, close, dom.document);
    keepBtn.focus(); // "Keep" — the non-destructive default, same as the real dialogs
  }
  function close() {
    backdrop.classList.remove("on");
    popModal(backdrop, dom.document);
    if (invokerEl && typeof invokerEl.focus === "function") invokerEl.focus();
    invokerEl = null;
  }
  function isOpen() {
    return backdrop.classList.contains("on");
  }
  return { backdrop, keepBtn, deleteBtn, open, close, isOpen };
}

test("keyboard flow: opening the confirm on top of the sheet focuses its safe default (Keep), not the sheet or nothing", () => {
  const sheet = createSheetDialog({ ariaLabel: "Test detail sheet" });
  const deleteTrigger = dom.document.createElement("button");
  dom.document.activeElement = deleteTrigger; // "Tab"ed to and activated
  sheet.open();
  assert.equal(dom.document.activeElement, sheet.sheet, "opening the sheet must focus the sheet itself (sheet-dialog.js's own tabIndex=-1 landing spot)");

  const confirm = buildConfirmDialog();
  dom.document.activeElement = deleteTrigger; // the sheet's own "Delete" button, focused before opening the confirm
  confirm.open(); // stands in for a click — native Enter/Space activation of a real <button>, not re-tested here
  assert.equal(dom.document.activeElement, confirm.keepBtn, "the confirm's safe default (Keep) must receive focus on open");
  assert.equal(sheet.isOpen(), true, "the sheet stays open (behind the confirm), never closed by opening a confirm on top of it");
});

test("keyboard flow: Escape unwinds inner-first — the confirm closes before the sheet, restoring focus to the confirm's own invoker", () => {
  const sheet = createSheetDialog({ ariaLabel: "Test detail sheet 2" });
  const deleteTrigger = dom.document.createElement("button");
  dom.document.activeElement = deleteTrigger;
  sheet.open();

  const confirm = buildConfirmDialog();
  dom.document.activeElement = deleteTrigger;
  confirm.open();

  // First Escape: MUTATION TARGET (the exact live-found WP 4a.8 bug this
  // codebase's own modal-stack.js header documents) — if Escape closed
  // every stacked overlay instead of only the topmost, `sheet.isOpen()`
  // would already be false here.
  dom.document.dispatchEvent(fakeKeyEvent("Escape"));
  assert.equal(confirm.isOpen(), false, "first Escape must close the confirm");
  assert.equal(sheet.isOpen(), true, "first Escape must NOT also close the sheet behind it");
  assert.equal(
    dom.document.activeElement,
    deleteTrigger,
    "closing the confirm must restore focus to what invoked IT (the sheet's Delete button), not the sheet or the original trigger",
  );

  // Second Escape: now the confirm is gone, the sheet is topmost again, so
  // Escape reaches ITS onEscape (dialog.close()).
  dom.document.dispatchEvent(fakeKeyEvent("Escape"));
  assert.equal(sheet.isOpen(), false, "second Escape must close the sheet, now that nothing sits above it");
  assert.equal(
    dom.document.activeElement,
    deleteTrigger,
    "closing the sheet restores focus to whatever originally opened it — here, coincidentally, the same element that later opened the confirm",
  );
});

test("keyboard flow: a Tab keydown is never intercepted by the centralized Escape dispatcher, even with a confirm nested on top", () => {
  // Complements modal-stack.test.js's identical check with a single overlay
  // ("a non-Escape key never calls onEscape", using "Enter") — this is the
  // NESTED case, and specifically the key this WP's brief names: native Tab
  // order inside the reachable (non-inert) subtree is exactly what this
  // codebase relies on instead of a hand-rolled focus trap (lib/modal-
  // stack.js's own header, "native inert... beats hand-rolled Tab
  // interception"), so the centralized dispatcher must be a complete no-op
  // for it, at any stack depth.
  const sheet = createSheetDialog({ ariaLabel: "Test detail sheet 3" });
  sheet.open();
  const confirm = buildConfirmDialog();
  confirm.open();

  dom.document.dispatchEvent(fakeKeyEvent("Tab"));
  assert.equal(confirm.isOpen(), true, "Tab must never close the confirm");
  assert.equal(sheet.isOpen(), true, "Tab must never close the sheet either");
});

test("keyboard flow: closing the confirm via its own Keep button (a click, standing in for Enter/Space) also restores the invoker, same as Escape", () => {
  const sheet = createSheetDialog({ ariaLabel: "Test detail sheet 4" });
  const deleteTrigger = dom.document.createElement("button");
  dom.document.activeElement = deleteTrigger;
  sheet.open();

  const confirm = buildConfirmDialog();
  dom.document.activeElement = deleteTrigger;
  confirm.open();
  confirm.keepBtn.addEventListener("click", () => confirm.close());
  confirm.keepBtn.dispatchEvent(fakeClickEvent(confirm.keepBtn));

  assert.equal(confirm.isOpen(), false);
  assert.equal(sheet.isOpen(), true);
  assert.equal(dom.document.activeElement, deleteTrigger);
});
