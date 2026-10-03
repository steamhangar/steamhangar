/**
 * Bulk action bar vs. content in select mode (WP WEB-FIX-6) — structural
 * CSS pins plus the library.js wiring of the live height.
 *
 * Noted during WP WEB-FIX-3 (web/tests/README.md): with games selected, the
 * fixed `.bulk` bar covered the last row of cards, and on a phone with a
 * home indicator it also covered the bottom nav's top edge (`--nav-h` does
 * not include `env(safe-area-inset-bottom)`, `.nav` adds it to its own
 * padding). Fix:
 *   - `.bulk`'s `bottom` adds the bottom safe-area inset, so the bar sits
 *     `--bulk-gap` above the nav's REAL top edge (BP-L: above the inset);
 *   - select mode (`body.selecting`, library.js's own class) reserves
 *     `--bulk-room` = `--bulk-h` + 2 x `--bulk-gap` at the end of the flow
 *     (`.view-root`, or the Suggestions card when it is the last box);
 *   - `--bulk-h` is measured live (lib/bulk-room.js) because the bar's
 *     height varies; theme.css carries a fallback;
 *   - BP-XL with the Suggestions column: the bar's right inset clears the
 *     column and the room goes back to the view.
 * Same posture as the other css-*.test.js files: no browser here, so these
 * pin declarations in the right selector and the right (or no) media block.
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
const libraryJs = readFileSync(path.join(webDir, "js", "views", "library.js"), "utf8");

function stripComments(css) {
  return css.replace(/\/\*[\s\S]*?\*\//g, "");
}

/** Brace-balanced @media splitter (per-file copy, suite convention). */
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

/** Body of the rule whose selector list is EXACTLY `selector`. Null when absent. */
function ruleBody(text, selector) {
  const re = /(^|\})\s*([^{}]+)\{([^{}]*)\}/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    if (m[2].trim().replace(/\s+/g, " ") === selector) return m[3];
    re.lastIndex = m.index + m[0].length - 1;
  }
  return null;
}

/** Every selector (normalised) of every rule in `text`. */
function selectors(text) {
  const out = [];
  const re = /(^|\})\s*([^{}]+)\{([^{}]*)\}/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    out.push({ selector: m[2].trim().replace(/\s+/g, " "), body: m[3] });
    re.lastIndex = m.index + m[0].length - 1;
  }
  return out;
}

const app = splitMedia(appCss);
const theme = splitMedia(themeCss);
const bpL = app.mediaBlocks.find((b) => /^@media \(min-width:1024px\)\s*\{$/.test(b.header));
const bpXL = app.mediaBlocks.find((b) => /^@media \(min-width:1800px\)\s*\{$/.test(b.header));
const ENV_BOTTOM = String.raw`env\(safe-area-inset-bottom,\s*0px\)`;

test("blocks exist (guard against a silent no-op suite)", () => {
  assert.ok(bpL, "BP-L block not found");
  assert.ok(bpXL, "BP-XL block not found");
  assert.ok(ruleBody(app.topLevel, ".bulk"), ".bulk not found at top level");
});

// ---------------------------------------------------------------------
// 1. Tokens
// ---------------------------------------------------------------------

test("theme.css: --bulk-gap is 14px (the pre-fix literal, so --nav-h + gap still = 78px) and --bulk-h has a px fallback", () => {
  const root = ruleBody(theme.topLevel, ":root") ?? "";
  assert.match(root, /--bulk-gap:\s*14px\s*;/);
  const h = /--bulk-h:\s*(\d+)px\s*;/.exec(root);
  assert.ok(h, "--bulk-h fallback missing or unitless (a bare number breaks every calc() it meets)");
  // Fallback must at least cover a one-line bar: 2x10px padding + 2px border
  // + 30px head + 8px gap + 16px note + 8px gap + 30px buttons = 114px.
  assert.ok(Number(h[1]) >= 114, `--bulk-h fallback ${h[1]}px is smaller than the smallest real bar`);
  const nav = /--nav-h:\s*(\d+)px/.exec(root);
  assert.equal(Number(nav[1]) + 14, 78);
});

// ---------------------------------------------------------------------
// 2. The bar sits above the bottom nav, safe area included
// ---------------------------------------------------------------------

test(".bulk sits --bulk-gap above the nav's real top: --nav-h + --bulk-gap + the bottom safe-area inset", () => {
  const body = ruleBody(app.topLevel, ".bulk");
  assert.match(
    body,
    new RegExp(String.raw`bottom:\s*calc\(var\(--nav-h\)\s*\+\s*var\(--bulk-gap\)\s*\+\s*${ENV_BOTTOM}\)`),
  );
  // Why the inset belongs in the bar's calc: .nav adds it to its OWN
  // padding, on top of --nav-h. If that ever moves into --nav-h, this pin
  // must move with it (else the inset is counted twice).
  assert.match(ruleBody(app.topLevel, ".nav") ?? "", /padding:[^;]*env\(safe-area-inset-bottom\)/);
  assert.doesNotMatch(ruleBody(theme.topLevel, ":root") ?? "", /--nav-h:[^;]*env\(/);
});

test("no breakpoint re-declares .bulk's bottom (it tracks --nav-h, which BP-L zeroes to 0px)", () => {
  for (const block of app.mediaBlocks) {
    for (const { selector, body } of selectors(block.body)) {
      if (/\.bulk\b/.test(selector) && !/\.bulk\s+\./.test(selector)) {
        assert.doesNotMatch(body, /(^|;|\s)bottom\s*:/, `${block.header} ${selector} overrides bottom`);
      }
    }
  }
});

test(".bulk respects the horizontal safe-area insets below BP-L (landscape notch)", () => {
  const body = ruleBody(app.topLevel, ".bulk");
  assert.match(body, /left:\s*calc\(12px\s*\+\s*env\(safe-area-inset-left,\s*0px\)\)/);
  assert.match(body, /right:\s*calc\(12px\s*\+\s*env\(safe-area-inset-right,\s*0px\)\)/);
});

// ---------------------------------------------------------------------
// 3. Scroll room while the bar is up
// ---------------------------------------------------------------------

test("select mode: --bulk-room = --bulk-h + 2 x --bulk-gap (gap below the bar to the nav, gap above it)", () => {
  assert.match(
    ruleBody(app.topLevel, "body.selecting") ?? "",
    /--bulk-room:\s*calc\(var\(--bulk-h\)\s*\+\s*var\(--bulk-gap\)\s*\*\s*2\)\s*;?\s*$/,
  );
});

test("BP-L: the room adds the bottom safe-area inset (no bottom nav carries it there)", () => {
  assert.match(
    ruleBody(bpL.body, "body.selecting") ?? "",
    new RegExp(String.raw`--bulk-room:\s*calc\(var\(--bulk-h\)\s*\+\s*var\(--bulk-gap\)\s*\*\s*2\s*\+\s*${ENV_BOTTOM}\)`),
  );
});

test("select mode: .view-root's bottom padding becomes the room; the plain .view-root rule stays without one (WEB-FIX-3)", () => {
  assert.match(ruleBody(app.topLevel, "body.selecting .view-root") ?? "", /padding-bottom:\s*var\(--bulk-room\)/);
  assert.doesNotMatch(ruleBody(app.topLevel, ".view-root") ?? "", /padding-bottom/);
  // Only select mode may add bottom padding to the view; nothing unscoped.
  for (const { selector, body } of selectors(app.topLevel)) {
    if (/\.view-root\b/.test(selector) && /padding-bottom/.test(body)) {
      assert.match(selector, /^body\.selecting /, `${selector} adds view padding outside select mode`);
    }
  }
});

test("select mode: a visible Suggestions card ends the flow, takes the room, and the view gets its 32px back", () => {
  assert.match(
    ruleBody(app.topLevel, "body.selecting .app > .decision-panel:not([hidden])") ?? "",
    /margin-bottom:\s*var\(--bulk-room\)/,
  );
  assert.match(
    ruleBody(app.topLevel, "body.selecting .app:has(> :where(.decision-panel:not([hidden]))) > .view-root") ?? "",
    /padding-bottom:\s*32px/,
  );
  // .view-root's own base bottom padding is the 32px restored above.
  assert.match(ruleBody(app.topLevel, ".view-root") ?? "", /padding:\s*20px 16px 32px/);
});

// ---------------------------------------------------------------------
// 4. BP-XL with the Suggestions column
// ---------------------------------------------------------------------

test("BP-XL: beside the Suggestions column the bar's right inset clears the column (--panel-w + --gutter)", () => {
  assert.match(
    ruleBody(bpXL.body, ".app.has-decision-panel .bulk") ?? "",
    /right:\s*calc\(var\(--panel-w\)\s*\+\s*var\(--gutter\)\)/,
  );
});

test("BP-XL: with the column the room goes back to the view and the column keeps its 32px", () => {
  assert.match(
    ruleBody(bpXL.body, "body.selecting .app.has-decision-panel > .view-root") ?? "",
    /padding-bottom:\s*var\(--bulk-room\)/,
  );
  assert.match(
    ruleBody(bpXL.body, "body.selecting .app.has-decision-panel > .decision-panel") ?? "",
    /margin-bottom:\s*32px/,
  );
});

// ---------------------------------------------------------------------
// 5. WEB-FIX-3 rules unaffected
// ---------------------------------------------------------------------

test("WEB-FIX-3: no viewport-unit width on .bulk, the nav stays sticky, select mode adds no fixed width", () => {
  const body = ruleBody(app.topLevel, ".bulk");
  assert.doesNotMatch(body, /\d+vw/);
  assert.doesNotMatch(body, /(^|;|\s)(min-)?width:/);
  assert.match(ruleBody(app.topLevel, ".nav") ?? "", /position:\s*sticky/);
  for (const { selector, body: b } of selectors(app.topLevel + app.mediaBlocks.map((m) => m.body).join("\n"))) {
    if (/^body\.selecting\b/.test(selector)) {
      assert.doesNotMatch(b, /(^|;|\s)(min-)?width:/, `${selector} sets a width`);
    }
  }
});

// ---------------------------------------------------------------------
// 6. library.js measures the bar
// ---------------------------------------------------------------------

test("library.js watches each mount's bulk bar with the shared height watcher, fed by the real ResizeObserver and <html>", () => {
  assert.match(libraryJs, /import \{ createBulkHeightWatcher \} from "\.\.\/lib\/bulk-room\.js";/);
  assert.match(libraryJs, /rootStyle:\s*document\.documentElement\?\.style/);
  assert.match(libraryJs, /ResizeObserverImpl:\s*globalThis\.ResizeObserver/);
  assert.match(libraryJs, /bulkHeight\.watch\(bulkBar\);/);
  // Leaving the Library releases the detached bar (next to `sectionEl = null`).
  assert.match(libraryJs, /sectionEl = null;[\s\S]{0,200}bulkHeight\.unwatch\(\);\s*\}\);/);
  // The body class the CSS keys on is library.js's own select-mode class.
  assert.match(libraryJs, /document\.body\.classList\.add\("selecting"\)/);
  assert.match(libraryJs, /document\.body\.classList\.remove\("selecting"\)/);
});
