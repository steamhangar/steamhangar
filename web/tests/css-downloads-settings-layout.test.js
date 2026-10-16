/**
 * Downloads/Settings desktop layout (WP 4e.5, D-15) — structural CSS pins.
 *
 * The problem, `docs/PROJECT_PLAN.md`'s Phase 4e section: Downloads and
 * Settings were the last two views that "just stretch" — they inherited
 * `.view-root`'s BP-L width (`--w-wall`, 960-2000px, the Library grid's own
 * cap) with no content-shape review of their own. Both are, top to bottom,
 * ROWS (job cards, queue rows, history rows) or a linear FORM (label/input/
 * caption fields) — never tiles that benefit from more columns — so the fix
 * is a capped, centred reading column at `--w-text` (unchanged, 760px from
 * BP-M up), not a new token and not a multi-column arrangement. Full
 * per-view inventory and the reasons a multi-column/hybrid layout was
 * considered and rejected for each view live in `docs/WORKPACKAGES.md`'s
 * D-15 entry and in `css/app.css`'s own comment on the rule this file pins.
 *
 * Same posture as `css-layout-foundation.test.js`/`css-overlay-geometry.
 * test.js`: no jsdom/browser in `web/tests/` (see this directory's README),
 * so this is a structural/value pin over the CSS source text, not a real
 * layout measurement — self-contained parsing utilities, not imported from
 * either sibling file, matching this suite's existing per-file convention.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const webDir = path.join(__dirname, "..");
const appCss = readFileSync(path.join(webDir, "css", "app.css"), "utf8");
const themeCss = readFileSync(path.join(webDir, "css", "theme.css"), "utf8");

function stripComments(css) {
  return css.replace(/\/\*[\s\S]*?\*\//g, "");
}

/** Same brace-balanced @media splitter css-layout-foundation.test.js/
 * css-overlay-geometry.test.js each carry their own copy of. */
function splitMedia(cssText) {
  const css = stripComments(cssText);
  let topLevel = "";
  const mediaBlocks = [];
  let i = 0;
  while (i < css.length) {
    const atIdx = css.indexOf("@media", i);
    if (atIdx === -1) {
      topLevel += css.slice(i);
      break;
    }
    topLevel += css.slice(i, atIdx);
    const openBrace = css.indexOf("{", atIdx);
    const header = css.slice(atIdx, openBrace + 1).trim();
    let depth = 1;
    let j = openBrace + 1;
    while (depth > 0 && j < css.length) {
      if (css[j] === "{") depth++;
      else if (css[j] === "}") depth--;
      j++;
    }
    mediaBlocks.push({ header, body: css.slice(openBrace + 1, j - 1) });
    i = j;
  }
  return { topLevel, mediaBlocks };
}

/** First top-level-selector rule body matching `selector` in `text`. */
function ruleBody(text, selector) {
  const idx = text.indexOf(selector + "{");
  if (idx === -1) return null;
  const openBrace = idx + selector.length;
  let depth = 1;
  let j = openBrace + 1;
  while (depth > 0 && j < text.length) {
    if (text[j] === "{") depth++;
    else if (text[j] === "}") depth--;
    j++;
  }
  return text.slice(openBrace + 1, j - 1);
}

const { topLevel: appTop, mediaBlocks: appMedia } = splitMedia(appCss);
const { topLevel: themeTop } = splitMedia(themeCss);
const codeOnlyAll = stripComments(themeCss) + "\n" + stripComments(appCss);

function findMediaBlock(minWidthPx) {
  return appMedia.find((b) => new RegExp(`min-width:\\s*${minWidthPx}px`).test(b.header) && !/and/.test(b.header));
}
const bpL = findMediaBlock(1024);
const bpXl = findMediaBlock(1800);

// ---------------------------------------------------------------------
// 1. The layout exists, lives in the BP-L block, and is derived from the
// live --w-text token (never a re-typed literal) — the brief's own pin:
// "each view's BP-L layout exists and is derived from the live tokens
// (mutation: revert the layout block -> dies by name)".
// ---------------------------------------------------------------------

test("BP-L (min-width:1024px) exists and gives .view-downloads/.view-settings their own capped, centred column", () => {
  assert.ok(bpL, "no bare min-width:1024px block found — did the breakpoint move or get renamed?");
  const body = ruleBody(bpL.body, ".view-downloads, .view-settings, .view-clients");
  assert.ok(
    body,
    "no '.view-downloads, .view-settings, .view-clients{...}' rule found in the BP-L block — this is the WP 4e.5 layout itself; reverting/deleting it must fail exactly this test",
  );
  assert.match(body, /max-width:\s*var\(--w-page\)/, "must reference the live --w-page token (LAYOUT-1), never a re-typed literal");
  assert.match(body, /margin:\s*0\s+auto\b/, "must self-centre (margin:0 auto) — without it the capped column would hug the left edge of .view-root's own, wider box instead of sitting centred inside it");
  assert.equal(/\b\d+px\b/.test(body), false, "the rule must contain no bare px literal at all — width comes ONLY from --w-page, not a hand-typed number that could drift from it");
});

test(".view-library is deliberately excluded from the capped-column rule — it still needs --w-wall for its own auto-fill tile grid", () => {
  const body = ruleBody(bpL.body, ".view-downloads, .view-settings, .view-clients");
  assert.ok(body);
  assert.equal(/view-library/.test(bpL.body.slice(bpL.body.indexOf(".view-downloads, .view-settings, .view-clients"), bpL.body.indexOf(".view-downloads, .view-settings, .view-clients") + 200)), false);
  // WP WEB-FIX-UPD-VIS removed .view-library's own BP-L grid (the toolbar
  // row is `.lib-bar`'s flexbox now, css-layout-foundation.test.js); the
  // library still must not appear in this capped-column list anywhere.
  assert.equal(/\.view-library\b/.test(body), false);
});

// ---------------------------------------------------------------------
// 2. --w-text stays FLAT past BP-M (the whole reason it, not --w-wall, was
// picked for this cap) — mutation-protects against a future BP-L/BP-XL
// redefinition silently widening Downloads/Settings again without anyone
// touching THIS rule at all.
// ---------------------------------------------------------------------

test("--w-text has exactly two assignments anywhere (theme.css :root base, app.css BP-M) — never redeclared at BP-L or BP-XL", () => {
  const assignments = [...codeOnlyAll.matchAll(/--w-text\s*:\s*([^;]+);/g)].map((m) => m[1].trim());
  assert.equal(
    assignments.length,
    2,
    `expected exactly 2 --w-text assignments (:root base, BP-M), found ${assignments.length}: ${JSON.stringify(assignments)} — a third assignment inside BP-L/BP-XL would silently change Downloads'/Settings' own cap without touching their rule at all`,
  );
  assert.equal(assignments[0], "960px");
  assert.equal(assignments[1], "760px");
});

test("theme.css's :root base --w-text is 960px, unaffected by this WP (sanity: the token itself was not touched, only its consumer)", () => {
  assert.match(themeTop, /--w-text:\s*960px/);
});

test("BP-XL (min-width:1800px) does not touch --w-text or .view-downloads/.view-settings — the cap must not widen past BP-L", () => {
  assert.ok(bpXl, "no bare min-width:1800px block found");
  assert.equal(/--w-text/.test(bpXl.body), false, "BP-XL must not redeclare --w-text");
  assert.equal(/view-downloads|view-settings/.test(bpXl.body), false, "BP-XL must not add its own override for either view");
});

// ---------------------------------------------------------------------
// 3. No cascade tie was created: nothing else in app.css targets
// `.view-downloads`/`.view-settings` at all, anywhere — the new rule is the
// ONLY declaration touching either class, so there is nothing for it to
// tie with. A future second rule touching either class should have to
// walk through (and update) this assertion deliberately, not land silently
// alongside an existing max-width/margin pair.
// ---------------------------------------------------------------------

test("no cascade tie: .view-downloads is referenced exactly once in app.css (no pre-existing rule touches it at all)", () => {
  const dlCount = (codeOnlyAll.match(/\.view-downloads\b/g) || []).length;
  assert.equal(dlCount, 1, `".view-downloads" appears ${dlCount} times in theme.css+app.css — expected exactly 1 (this WP's own selector); a second occurrence means some other rule now also targets it and needs a deliberate cascade review`);
});

test("no cascade tie: .view-settings' only OTHER reference is the pre-existing, unrelated h4.sec descendant rule — nothing else sets max-width/margin on the bare class", () => {
  const stCount = (codeOnlyAll.match(/\.view-settings\b/g) || []).length;
  assert.equal(
    stCount,
    2,
    `".view-settings" appears ${stCount} times in theme.css+app.css — expected exactly 2 (the pre-existing WP 4a.6 "h4.sec:first-of-type" descendant rule, plus this WP's own selector); a third occurrence means some other rule now ALSO targets it and needs a deliberate cascade review`,
  );
  // The one pre-existing reference is a DESCENDANT selector styling h4.sec
  // (margin-top only) — it does not set max-width/margin on .view-settings
  // itself, so it cannot tie with this WP's rule regardless of source order.
  assert.ok(
    appTop.includes(".view-settings h4.sec:first-of-type{ margin-top:2px; }"),
    "the known pre-existing .view-settings reference (a descendant rule styling h4.sec, not the section itself) changed or moved — re-check for a real tie before updating this pin",
  );
});

// ---------------------------------------------------------------------
// 4. Mobile/base rules for the actual Downloads/Settings content — a
// representative cross-section of the inventory named in app.css's own
// comment — keep every pre-WP property value (same naming convention as
// css-overlay-geometry.test.js's "keeps every pre-WP property value" pins:
// this proves the SHAPE this WP reasoned about is still exactly what
// shipped before it, not that the files are byte-identical end to end).
// ---------------------------------------------------------------------

test("base .dl-head/.dl-sub keep every pre-WP property value", () => {
  const headBody = ruleBody(appTop, ".dl-head");
  assert.ok(headBody, ".dl-head rule not found at top level");
  assert.match(headBody, /margin-bottom:\s*2px/);
  const subBody = ruleBody(appTop, ".dl-sub");
  assert.ok(subBody, ".dl-sub rule not found at top level");
  assert.match(subBody, /font-size:\s*11px/);
});

test("base .jobcard/.jobtop keep every pre-WP property value (the Active/Paused card shape)", () => {
  const cardBody = ruleBody(appTop, ".jobcard");
  assert.ok(cardBody, ".jobcard rule not found at top level");
  assert.match(cardBody, /border-radius:\s*var\(--r-m\)/);
  assert.match(cardBody, /padding:\s*12px/);
  const topBody = ruleBody(appTop, ".jobtop");
  assert.ok(topBody, ".jobtop rule not found at top level");
  assert.match(topBody, /display:\s*flex/);
  assert.match(topBody, /justify-content:\s*space-between/, "the header row must still spread name/badge apart — this WP caps the COLUMN, not this row's own internal layout");
});

test("base .qrow keeps every pre-WP property value (the Queue row shape)", () => {
  const body = ruleBody(appTop, ".qrow");
  assert.ok(body, ".qrow rule not found at top level");
  assert.match(body, /display:\s*flex/);
  assert.match(body, /align-items:\s*center/);
  assert.match(body, /border-radius:\s*var\(--r-m\)/);
});

test("base .hrow / .hrow > button keep every pre-WP property value (the History row shape)", () => {
  const rowBody = ruleBody(appTop, ".hrow");
  assert.ok(rowBody, ".hrow rule not found at top level");
  assert.match(rowBody, /overflow:\s*hidden/);
  const btnBody = ruleBody(appTop, ".hrow > button");
  assert.ok(btnBody, ".hrow > button rule not found at top level");
  assert.match(btnBody, /width:\s*100%/, "the toggle button must still fill its row's own width (100% of whatever container it sits in — unaffected by the OUTER column narrowing this WP adds)");
});

test("base .field/.srow keep every pre-WP property value (the Settings form shape)", () => {
  const fieldBody = ruleBody(appTop, ".field");
  assert.ok(fieldBody, ".field rule not found at top level");
  assert.match(fieldBody, /margin-bottom:\s*11px/);
  const srowBody = ruleBody(appTop, ".srow");
  assert.ok(srowBody, ".srow rule not found at top level");
  assert.match(srowBody, /display:\s*flex/);
  assert.match(srowBody, /border-radius:\s*var\(--r-m\)/);
});

test("the capped-column rule exists ONLY inside the BP-L block, never at the top level", () => {
  // NB: `.view-settings h4.sec:first-of-type` (a pre-existing, WP 4a.6 rule,
  // unrelated to this WP) IS a legitimate top-level ".view-settings"
  // occurrence — asserting no mention of either class name at all outside
  // @media would false-positive on it. This checks specifically for the
  // WP 4e.5 selector pair, not any reference to either class.
  assert.equal(appTop.includes(".view-downloads, .view-settings, .view-clients{"), false, "the capped-column selector must not exist outside @media — the base/mobile column width for both views is unaffected by this WP, exactly as before");
});

test("--w-page (LAYOUT-1) is declared once, as 1400px, in theme.css and never redeclared in any breakpoint", () => {
  const assignments = [...codeOnlyAll.matchAll(/--w-page\s*:\s*([^;]+);/g)].map((m) => m[1].trim());
  assert.deepEqual(assignments, ["1400px"]);
  assert.match(themeTop, /--w-page:\s*1400px/);
  assert.equal(/--w-page/.test(bpXl.body), false, "BP-XL must not redeclare --w-page");
  const wall = ruleBody(bpL.body, ".view-root");
  assert.ok(wall && /max-width:\s*var\(--w-wall\)/.test(wall), "Library/.view-root keeps --w-wall");
});
