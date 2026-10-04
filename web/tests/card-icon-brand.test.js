/**
 * WP WEB-FIX-9: three items of user feedback on rc9 (2026-10-04).
 *
 * 1. Library card capsule pill. With no number to print (every kind but a
 *    cached game, game-status.js Divergence 2) the pill's dark ground was a
 *    small grey lozenge around the icon with an empty 8px right end, read
 *    as "a grey ring where the status word should be". Icon-only pills are
 *    now `.bare` (no ground, symmetric padding); the word stays in the meta
 *    row under the cover (round-6 rule: nothing said twice).
 * 2. Downloads job badge. Measured in headless Chromium: the glyph is
 *    centred in its disc and the disc on its word to 0.01px; the offset
 *    was the badge itself, pinned to the top of the two-line name block by
 *    `.jobtop{ align-items:flex-start }` (disc centre 8px vs block centre
 *    14.5px). `.jobtop > .badge{ align-self:center }` matches the History
 *    rows, which centre their icon on the same name + job line.
 * 3. Brand: "SteamHangar" everywhere, no uppercase transform on `.brand`.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { createFakeDom } from "./fake-dom.js";

const dom = createFakeDom();
globalThis.document = dom.document;
globalThis.window = dom.window;

const { buildCard, patchCardVolatile, pillClassName } = await import("../js/components/game-card.js");
const { createStatusIcon } = await import("../js/components/status-icon.js");

const webDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const appCss = readFileSync(path.join(webDir, "css", "app.css"), "utf8");
const indexHtml = readFileSync(path.join(webDir, "index.html"), "utf8");
const onboardingJs = readFileSync(path.join(webDir, "js", "onboarding.js"), "utf8");

// ---------------------------------------------------------------------
// CSS helpers (per-file copy, suite convention)
// ---------------------------------------------------------------------

function stripComments(css) {
  return css.replace(/\/\*[\s\S]*?\*\//g, "");
}

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
const bpL = app.mediaBlocks.find((b) => /min-width:\s*1024px/.test(b.header) && !/and/.test(b.header));

// ---------------------------------------------------------------------
// 1. Capsule pill: bare when there is no number
// ---------------------------------------------------------------------

function noop() {}
const ctx = (liveJob, over = {}) => ({
  liveJob,
  picked: false,
  selecting: false,
  onOpen: noop,
  onLongPress: noop,
  onToggle: noop,
  onAction: noop,
  ...over,
});
const game = (over = {}) => ({
  appid: 275850,
  name: "No Man's Sky",
  status: "none",
  size_bytes: null,
  needs_force: false,
  installed_on: [],
  ...over,
});

// Every pill shape the card can build: [label, game, liveJob, ctx overrides].
const ICON_ONLY = [
  ["paused job (resume button)", game({ status: "running" }), { status: "paused", appid: 275850 }, {}],
  ["running job (pause button)", game({ status: "running" }), { status: "running", appid: 275850 }, {}],
  ["not cached (download button)", game(), undefined, {}],
  ["update ready (update button)", game({ status: "done", size_bytes: 5e9, needs_force: false, stale: true }), undefined, {}],
  ["failed", game({ status: "error" }), undefined, {}],
  ["not cached while selecting (inert span)", game(), undefined, { selecting: true }],
];

test("MUTATION TARGET: an icon-only pill is .bare for every kind without a number (paused, running, none, error, inert)", () => {
  let checked = 0;
  for (const [label, g, job, over] of ICON_ONLY) {
    const card = buildCard(g, ctx(job, over));
    const pill = card.querySelector(".cappill");
    if (pill.querySelector(".pv")) continue; // a kind that does print a number is covered below
    checked++;
    assert.ok(pill.classList.contains("bare"), `${label}: icon-only pill must be .bare (no grey ground)`);
    assert.ok(pill.querySelector(".sic"), `${label}: the status icon is still there`);
  }
  assert.ok(checked >= 5, `the loop must not pass vacuously (checked ${checked})`);
});

test("the paused card from the report: pause glyph, .bare, a resume button, and the word 'Paused' in the meta row", () => {
  const card = buildCard(game({ status: "running" }), ctx({ status: "paused", appid: 275850 }));
  const pill = card.querySelector(".cappill");
  assert.equal(pill.tagName, "BUTTON");
  assert.ok(pill.classList.contains("bare"));
  assert.ok(pill.querySelector(".sic.k-paused"));
  assert.equal(pill.querySelector(".pv"), null, "no fabricated number");
  assert.equal(card.querySelector(".meta .state").textContent, "Paused", "the status word lives under the cover");
});

test("MUTATION TARGET: a cached pill with a size keeps its ground (not .bare)", () => {
  const card = buildCard(game({ status: "done", size_bytes: 3.9e9 }), ctx(undefined));
  const pill = card.querySelector(".cappill");
  assert.ok(pill.querySelector(".pv"), "cached pill prints the size");
  assert.equal(pill.classList.contains("bare"), false);
});

test("pillClassName: bare iff there is no number, independent of the button state", () => {
  assert.equal(pillClassName(true, null), "cappill act bare");
  assert.equal(pillClassName(false, null), "cappill bare");
  assert.equal(pillClassName(false, "3.9 GB"), "cappill");
  assert.equal(pillClassName(true, "3.9 GB"), "cappill act");
});

test("MUTATION TARGET: patchCardVolatile keeps .bare in step with the number", () => {
  const g = game({ status: "done", size_bytes: 3.9e9 });
  const card = buildCard(g, ctx(undefined));
  const pill = card.querySelector(".cappill");
  // Same structural kind, number gone (a patch-path tick the planner allows
  // only for an unchanged kind; forced here to prove the toggle both ways).
  patchCardVolatile(card, { ...g, size_bytes: null }, "none");
  assert.ok(pill.classList.contains("bare"), "number removed -> bare");
  patchCardVolatile(card, g, "cached");
  assert.equal(pill.classList.contains("bare"), false, "number back -> ground back");
});

test("MUTATION TARGET: CSS — a bare pill has no ground and symmetric padding, in every layout", () => {
  const bare = allRuleBodies(app.topLevel, ".cap .cappill.bare");
  assert.match(bare, /background:\s*transparent/);
  assert.match(bare, /box-shadow:\s*none/);
  assert.match(bare, /(^|;|\s)backdrop-filter:\s*none/);
  assert.match(bare, /padding:\s*3px\s*;/, "symmetric, same inset as the base pill's left edge");
  assert.match(bare, /filter:\s*drop-shadow\(/, "contrast for the bare disc on light art");
  assert.match(allRuleBodies(app.topLevel, ".grid.cols3 .cap .cappill.bare"), /padding:\s*2px\s*;/, "phone compact grid");
  assert.ok(bpL, "BP-L block not found");
  assert.match(allRuleBodies(bpL.body, ".grid.cols3 .cap .cappill.bare"), /padding:\s*3px\s*;/, "BP-L compact grid resets to base");
});

// ---------------------------------------------------------------------
// 2. Downloads badge centring + glyph geometry
// ---------------------------------------------------------------------

test("MUTATION TARGET: the Downloads job badge centres on the two-line name block", () => {
  assert.match(allRuleBodies(app.topLevel, ".jobtop > .badge"), /align-self:\s*center/);
  // The row itself keeps flex-start (the clients sheet's badge column relies on it).
  assert.match(allRuleBodies(app.topLevel, ".jobtop"), /align-items:\s*flex-start/);
});

function rectBox(r) {
  const x = Number(r.getAttribute("x"));
  const y = Number(r.getAttribute("y"));
  return { x0: x, y0: y, x1: x + Number(r.getAttribute("width")), y1: y + Number(r.getAttribute("height")) };
}

function filledGlyphBox(kind) {
  const svg = createStatusIcon(kind).querySelector("svg");
  assert.equal(svg.getAttribute("viewBox"), "0 0 24 24");
  const boxes = svg.querySelectorAll("rect").map(rectBox);
  assert.ok(boxes.length > 0, `${kind}: filled glyph built from rects`);
  return {
    boxes,
    x0: Math.min(...boxes.map((b) => b.x0)),
    y0: Math.min(...boxes.map((b) => b.y0)),
    x1: Math.max(...boxes.map((b) => b.x1)),
    y1: Math.max(...boxes.map((b) => b.y1)),
  };
}

test("MUTATION TARGET: the pause and stop glyphs are centred on the 24-unit viewBox (12,12)", () => {
  for (const kind of ["paused", "cancelled"]) {
    const b = filledGlyphBox(kind);
    assert.ok(Math.abs((b.y0 + b.y1) / 2 - 12) < 1e-9, `${kind}: vertical centre ${(b.y0 + b.y1) / 2}`);
    assert.ok(Math.abs((b.x0 + b.x1) / 2 - 12) < 1e-9, `${kind}: horizontal centre ${(b.x0 + b.x1) / 2}`);
  }
  const pause = filledGlyphBox("paused");
  assert.equal(pause.boxes.length, 2);
  assert.deepEqual(
    pause.boxes.map((b) => [b.y0, b.y1]),
    [[5.6, 18.4], [5.6, 18.4]],
    "both bars share one vertical extent",
  );
});

// ---------------------------------------------------------------------
// 3. Brand
// ---------------------------------------------------------------------

test("MUTATION TARGET: .brand has no uppercase transform (the header reads 'SteamHangar')", () => {
  const body = allRuleBodies(app.topLevel, ".brand");
  assert.ok(body, ".brand rule not found");
  assert.doesNotMatch(body, /text-transform/);
  // No other rule may re-add it on the brand either.
  assert.doesNotMatch(stripComments(appCss), /\.brand[^{}]*\{[^}]*text-transform:\s*uppercase/);
});

test("the product name is spelled 'SteamHangar' in the tab title, the header and the onboarding wordmark", () => {
  assert.match(indexHtml, /<title>SteamHangar<\/title>/);
  assert.match(indexHtml, /<span class="brand">SteamHangar<\/span>/);
  assert.match(onboardingJs, /el\("div", "wordmark", "SteamHangar"\)/);
});
