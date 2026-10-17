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

const { buildCard, patchCardVolatile } = await import("../js/components/game-card.js");
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
// 1. Cover: size badge only (WP LIBRARY-2; WEB-FIX-9's bare pill is gone)
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

// WP LIBRARY-2 superseded the bare/ground pill: the cover carries the size
// badge only, the status (dot + word) and the quick action live under it.
test("MUTATION TARGET: no status icon on the cover for any kind; the size badge only for a game with a number", () => {
  let checked = 0;
  for (const [label, g, job, over] of ICON_ONLY) {
    const card = buildCard(g, ctx(job, over));
    const cap = card.querySelector(".cap");
    assert.ok(!cap.querySelector(".sic"), `${label}: no status icon on the cover`);
    assert.ok(!cap.querySelector(".cappill"), `${label}: no cover pill`);
    if (!g.size_bytes) assert.ok(!cap.querySelector(".sz"), `${label}: no number, no badge`);
    assert.ok(card.querySelector(".meta .stt .dot"), `${label}: the status line has its dot`);
    checked++;
  }
  assert.ok(checked >= 5, `the loop must not pass vacuously (checked ${checked})`);
});

test("the paused card from the report: a resume button with the pause glyph in the status line, the word 'Paused'", () => {
  const card = buildCard(game({ status: "running" }), ctx({ status: "paused", appid: 275850 }));
  const act = card.querySelector(".stt button.icnact");
  assert.ok(act, "the quick action stays on the card");
  assert.ok(act.querySelector(".sic.k-paused"));
  assert.equal(act.getAttribute("aria-label"), "Resume download — No Man's Sky");
  assert.equal(card.querySelector(".meta .state").textContent, "Paused", "the status word lives under the cover");
});

test("MUTATION TARGET: a cached game's cover carries its size badge", () => {
  const card = buildCard(game({ status: "done", size_bytes: 3.9 * 2 ** 30 }), ctx(undefined));
  assert.equal(card.querySelector(".cap .sz").textContent, "3.9 GB");
  assert.ok(!card.querySelector(".stt .icnact"), "cached: no quick action");
});

test("MUTATION TARGET: patchCardVolatile keeps the size badge in step with the number", () => {
  const g = game({ status: "done", size_bytes: 3.9e9 });
  const card = buildCard(g, ctx(undefined));
  patchCardVolatile(card, { ...g, size_bytes: null }, "none");
  assert.ok(!card.querySelector(".cap .sz"), "number removed -> badge gone");
  patchCardVolatile(card, { ...g, size_bytes: 4.2 * 2 ** 30 }, "cached");
  assert.equal(card.querySelector(".cap .sz").textContent, "4.2 GB", "number back -> badge back");
});

test("MUTATION TARGET: CSS — the size badge sits top-left on the cover in mono, from tokens", () => {
  const kit = stripComments(readFileSync(path.join(webDir, "css", "kit.css"), "utf8"));
  const body = allRuleBodies(kit, ".kit .cap .sz");
  assert.match(body, /position:absolute/);
  assert.match(body, /top:8px; left:8px/);
  assert.match(body, /var\(--mono\)/);
  assert.match(allRuleBodies(kit, ".kit .grid.list .card .cap .name, .kit .grid.list .card .cap .sz"), /display:none/);
});

test("MUTATION TARGET -- keyboard: Enter/Space on the status-line quick action runs the action and never opens the sheet (LIBRARY-2 review)", () => {
  for (const key of ["Enter", " "]) {
    const calls = [];
    const card = buildCard(game(), ctx(undefined, {
      onOpen: (id) => calls.push(["open", id]),
      onAction: (id, type) => calls.push(["action", id, type]),
    }));
    const btn = card.querySelector(".stt button.icnact");
    let prevented = false;
    // fake-dom does not bubble: deliver the keydown the card sees when it
    // bubbles up from the focused button.
    card.dispatchEvent({ type: "keydown", key, target: btn, preventDefault() { prevented = true; } });
    assert.equal(prevented, false, `${JSON.stringify(key)}: the button's own activation is not cancelled`);
    // The browser then activates the button (click), which runs the action only.
    btn.dispatchEvent({ type: "click", stopPropagation() {} });
    assert.deepEqual(calls, [["action", 275850, "download"]], JSON.stringify(key));
  }
  // Enter on the card itself still opens the sheet.
  const opened = [];
  const card = buildCard(game(), ctx(undefined, { onOpen: (id) => opened.push(id) }));
  card.dispatchEvent({ type: "keydown", key: "Enter", preventDefault() {} });
  assert.deepEqual(opened, [275850]);
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

// ---------------------------------------------------------------------
// 4. Android wiring pin
// ---------------------------------------------------------------------

test("Android CapsulePill actually calls capsulePillHasGround (comment-stripped scan)", () => {
  const src = readFileSync(
    path.join(webDir, "..", "app", "app", "src", "main", "java", "dev", "steamvault", "app", "ui", "library", "GameCard.kt"),
    "utf8",
  ).replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
  const fn = src.slice(src.indexOf("fun CapsulePill("));
  assert.ok(fn.length > 0, "CapsulePill not found");
  assert.match(fn, /capsulePillHasGround\(model\)/);
  assert.match(fn, /hasGround\s*&&\s*model\.sizeLabel/);
});
