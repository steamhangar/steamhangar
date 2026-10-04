/**
 * Layout pins for the Settings save bar (WP WEB-FIX-8). No browser here
 * (same posture as css-about-pcs.test.js): these pin the declarations in
 * the right selector and the right (or no) media block.
 *
 * Expected look, for the real-device check: as soon as a Settings field
 * differs from its saved value, a rounded bar with "Unsaved changes" above
 * "Discard changes | Save changes" floats 14px above the bottom nav on a
 * phone (14px above the viewport bottom from 1024px up, right of the rail),
 * as wide as the Settings column; scrolling to the very end leaves the
 * last line (the trademark note) above the bar.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const webDir = path.join(__dirname, "..");
const appCss = readFileSync(path.join(webDir, "css", "app.css"), "utf8");
const themeCss = readFileSync(path.join(webDir, "css", "theme.css"), "utf8");

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

/** Body of the rule whose selector list is EXACTLY `selector`; null when absent. */
function ruleBody(text, selector) {
  const re = /(^|\})\s*([^{}]+)\{([^{}]*)\}/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    if (m[2].trim().replace(/\s+/g, " ") === selector) return m[3];
    re.lastIndex = m.index + m[0].length - 1;
  }
  return null;
}

const app = splitMedia(appCss);
const theme = splitMedia(themeCss);
const bpL = app.mediaBlocks.find((b) => /min-width:\s*1024px/.test(b.header) && !/and/.test(b.header));
const norm = (s) => (s || "").replace(/\s+/g, "");
const decl = (body, prop) => {
  const m = new RegExp(`(?:^|;)\\s*${prop}\\s*:\\s*([^;]+)`).exec(body || "");
  return m ? m[1].trim() : null;
};

test("MUTATION TARGET: phone — the bar is fixed, never in the page flow, and sits above the bottom nav plus the safe-area inset", () => {
  const body = ruleBody(app.topLevel, ".savebar");
  assert.ok(body, ".savebar rule missing at top level");
  assert.equal(decl(body, "position"), "fixed", "fixed: visible without scrolling wherever the user is on the page");
  assert.equal(
    norm(decl(body, "bottom")),
    "calc(var(--nav-h)+var(--bulk-gap)+env(safe-area-inset-bottom,0px))",
    "clears the nav (--nav-h) and the home-indicator inset the nav keeps in its own padding",
  );
  assert.match(norm(decl(body, "left")), /safe-area-inset-left/);
  assert.match(norm(decl(body, "right")), /safe-area-inset-right/);
  assert.equal(norm(decl(body, "max-width")), "calc(var(--w-text)-(var(--gutter)*2))", "as wide as the Settings column");
});

test("the bar's bottom placement is the bulk bar's own formula (one rule for 'above the nav', two views that never coexist)", () => {
  assert.equal(norm(decl(ruleBody(app.topLevel, ".savebar"), "bottom")), norm(decl(ruleBody(app.topLevel, ".bulk"), "bottom")));
  const jsDir = path.join(webDir, "js");
  const files = [];
  const walk = (d) => {
    for (const e of readdirSync(d, { withFileTypes: true })) {
      const p = path.join(d, e.name);
      if (e.isDirectory()) walk(p);
      else if (e.name.endsWith(".js")) files.push(p);
    }
  };
  walk(jsDir);
  // Construction sites: `el("div", "savebar")` / `x.className = "bulk"`.
  const using = (re) => files.filter((f) => re.test(readFileSync(f, "utf8"))).map((f) => path.relative(jsDir, f));
  assert.deepEqual(using(/el\(\s*"div"\s*,\s*"savebar"\s*\)/), ["views/settings.js"], "the save bar is built only on Settings");
  assert.deepEqual(using(/className\s*=\s*"bulk"/), ["views/library.js"], "the bulk bar is built only on the Library");
});

test("MUTATION TARGET: hidden really hides it (author display:flex beats the UA [hidden] rule)", () => {
  assert.match(ruleBody(app.topLevel, ".savebar") ?? "", /display:\s*flex/);
  assert.match(ruleBody(app.topLevel, ".savebar[hidden]") ?? "", /display:\s*none/);
});

test("MUTATION TARGET: while the bar is up the page reserves room for it, so the last content is never under it", () => {
  assert.equal(norm(decl(ruleBody(app.topLevel, ".savebar-up"), "padding-bottom")), "calc(var(--savebar-h)+var(--bulk-gap)*2)");
  const token = /--savebar-h:\s*(\d+)px\s*;/.exec(stripComments(themeCss));
  assert.ok(token, "--savebar-h must be a px length (a unitless value breaks the calc)");
  assert.ok(Number(token[1]) >= 86, "room for one status line + one button row at 360px");
});

test("from BP-L up the bar clears the side rail", () => {
  assert.ok(bpL, "BP-L block not found");
  const body = ruleBody(bpL.body, ".savebar") ?? "";
  assert.equal(norm(decl(body, "left")), "calc(var(--rail-w)+var(--gutter))");
  assert.equal(norm(decl(body, "right")), "var(--gutter)");
});

test("theme tokens only (no hard-coded colours), the error line uses the danger token", () => {
  const body = ruleBody(app.topLevel, ".savebar") ?? "";
  assert.match(decl(body, "background") ?? "", /^var\(--/);
  assert.match(decl(body, "border") ?? "", /var\(--/);
  assert.match(ruleBody(app.topLevel, ".savebar-msg.is-error") ?? "", /color:\s*var\(--danger\)/);
  assert.ok(theme.topLevel.includes("--savebar-h"));
});

test("MUTATION TARGET: while the bar is up the root scroll padding keeps a focused control above it (WCAG 2.4.11)", () => {
  const body = ruleBody(app.topLevel, ":root:has(.savebar-up)");
  assert.ok(body, ":root:has(.savebar-up) rule missing at top level");
  assert.equal(
    norm(decl(body, "scroll-padding-bottom")),
    "calc(var(--savebar-h)+var(--bulk-gap)*2+var(--nav-h)+env(safe-area-inset-bottom,0px))",
    "bar height + its gaps + the nav + the bottom inset",
  );
});
