/**
 * Phone-width fit (WP WEB-FIX-3) — structural CSS pins.
 *
 * Hardening against horizontal overflow — NOT the cause of the 2026-10-01
 * Pixel zoom-out report (its Library was empty, Settings has no grid). That
 * report's root cause was Chrome's "Desktop site" mode (980px layout),
 * confirmed by the user on the Pixel 2026-10-02 and mitigated by the
 * WP WEB-FIX-5 hint banner (desktop-site-hint.test.js). The hardening: the
 * phone grid's `1fr` tracks (= `minmax(auto,1fr)`) could not shrink below a
 * card's nowrap min-content (`.instbadge`, list `.rowname`). Same posture as
 * the other css-*.test.js files: no browser here, so these pin the
 * declarations in the right selector and the right (or no) media block;
 * real-device verification happens on the Pixel after deploy (README).
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

/** Body of the rule whose selector list is EXACTLY `selector` (a rule
 * starts after `}` / start of text, so `.grid` never matches inside
 * `.grid.list` or a combined list). Null when absent. */
function ruleBody(text, selector) {
  const re = /(^|\})\s*([^{}]+)\{([^{}]*)\}/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    if (m[2].trim().replace(/\s+/g, " ") === selector) return m[3];
    re.lastIndex = m.index + m[0].length - 1; // re-use the closing brace
  }
  return null;
}

/** Every body of rules whose selector is exactly `selector`, joined. */
function allRuleBodies(text, selector) {
  const out = [];
  const re = /(^|\})\s*([^{}]+)\{([^{}]*)\}/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    if (m[2].trim().replace(/\s+/g, " ") === selector) out.push(m[3]);
    re.lastIndex = m.index + m[0].length - 1;
  }
  return out.join(";");
}

const app = splitMedia(appCss);
const theme = splitMedia(themeCss);
const bpM = app.mediaBlocks.find((b) => /min-width:\s*720px/.test(b.header) && !/and/.test(b.header));

test("safety net: html, body carry overflow-x:clip at the top level (never hidden, which would break the sticky nav)", () => {
  const body = ruleBody(theme.topLevel, "html, body");
  assert.ok(body, "theme.css top-level `html, body` rule not found");
  assert.match(body, /overflow-x:\s*clip\s*(;|$)/);
  assert.doesNotMatch(body, /overflow(-x)?:\s*hidden/);
});

test("root cause: the phone grid columns are minmax(0,1fr), so a nowrap card child cannot widen a track past the viewport", () => {
  assert.match(ruleBody(app.topLevel, ".grid") ?? "", /grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/);
  assert.match(ruleBody(app.topLevel, ".grid.cols3") ?? "", /grid-template-columns:\s*repeat\(3,\s*minmax\(0,\s*1fr\)\)/);
  assert.match(ruleBody(app.topLevel, ".grid.list") ?? "", /grid-template-columns:\s*minmax\(0,\s*1fr\)/);
  assert.match(ruleBody(app.topLevel, ".card") ?? "", /min-width:\s*0\s*(;|$)/);
});

test("list layout: .grid.list .meta may shrink (flex-shrink:1, min-width:0) instead of pushing the row wider", () => {
  const body = allRuleBodies(app.topLevel, ".grid.list .meta");
  assert.match(body, /flex-shrink:\s*1/);
  assert.match(body, /min-width:\s*0/);
});

test("flex text columns may shrink: .jobtop > div and .banner .body carry min-width:0", () => {
  assert.match(ruleBody(app.topLevel, ".jobtop > div") ?? "", /min-width:\s*0/);
  assert.match(ruleBody(app.topLevel, ".banner .body") ?? "", /min-width:\s*0/);
});

test("long unbreakable strings break inside the text blocks (overflow-wrap:anywhere, top level)", () => {
  const sel = ".hint, .foot-note, .errline, .settings-warn, .notif .nx, .dp-row, .banner .body, .jobtop .nm";
  assert.match(ruleBody(app.topLevel, sel) ?? "", /overflow-wrap:\s*anywhere/);
});

test("toast: max-width uses 100% (not 100vw) and its text column may shrink and wrap", () => {
  const body = ruleBody(theme.topLevel, ".toast") ?? "";
  assert.match(body, /max-width:\s*min\(480px,\s*calc\(100%\s*-\s*32px\)\)/);
  const text = ruleBody(theme.topLevel, ".toast-text") ?? "";
  assert.match(text, /min-width:\s*0/);
  assert.match(text, /overflow-wrap:\s*anywhere/);
  assert.equal(/100vw/.test(stripComments(appCss) + stripComments(themeCss)), false, "no 100vw anywhere (it includes the scrollbar)");
});

test("no top-level fixed width/min-width above 360px (a phone floor) in either stylesheet", () => {
  for (const top of [app.topLevel, theme.topLevel]) {
    for (const m of top.matchAll(/(?:^|[;{\s])(min-)?width:\s*(\d+(?:\.\d+)?)px/g)) {
      assert.ok(Number(m[2]) <= 360, `top-level ${m[1] ?? ""}width:${m[2]}px exceeds a 360px phone`);
    }
  }
});

test("segmented text controls in Settings never wrap a label and scroll instead of widening the page", () => {
  const segs = ruleBody(app.topLevel, ".field > .segs") ?? "";
  assert.match(segs, /max-width:\s*100%/);
  assert.match(segs, /overflow-x:\s*auto/);
  const btn = ruleBody(app.topLevel, ".field > .segs button") ?? "";
  assert.match(btn, /white-space:\s*nowrap/);
  assert.match(btn, /width:\s*auto/, "the 28px icon-segment width must not apply to text segments");
  // The icon segments in the Library toolbar keep their 28px squares.
  assert.match(ruleBody(app.topLevel, ".segs button") ?? "", /width:\s*28px/);
});

test("webhook event checkboxes: `.field label.srow` restores the inline .srow row over `.field label`'s block/uppercase", () => {
  const body = ruleBody(app.topLevel, ".field label.srow") ?? "";
  assert.match(body, /display:\s*flex/);
  assert.match(body, /text-transform:\s*none/);
  assert.match(body, /font-size:\s*inherit/);
  assert.match(ruleBody(app.topLevel, ".srow") ?? "", /align-items:\s*center/);
});

test("bottom nav stays sticky and in flow below BP-L (never fixed), so it covers no content and needs no view padding", () => {
  const body = ruleBody(app.topLevel, ".nav") ?? "";
  assert.match(body, /position:\s*sticky/);
  assert.doesNotMatch(body, /position:\s*fixed/);
  assert.doesNotMatch(ruleBody(app.topLevel, ".view-root") ?? "", /padding-bottom/);
});

test("inputs are 16px below BP-M (no focus zoom) and get their desktop sizes back at BP-M", () => {
  assert.match(ruleBody(app.topLevel, "input.inp, .inp input") ?? "", /font-size:\s*16px/);
  assert.match(ruleBody(app.topLevel, ".search input") ?? "", /font-size:\s*16px/);
  assert.match(ruleBody(bpM.body, "input.inp, .inp input") ?? "", /font-size:\s*12\.5px/);
  assert.match(ruleBody(bpM.body, ".search input") ?? "", /font-size:\s*13px/);
});
