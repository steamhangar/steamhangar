/**
 * Header/hero art for the game detail card (WP 4h.3, load-then-reveal
 * design superseded to this shape by WP 4h.5 — docs/WORKPACKAGES.md D-17).
 *
 * `components/header-art.js` is DOM-building, so — per the general posture
 * `sheet-dialog.js`'s header documents for this whole family of components —
 * it is not unit-tested for its general shape. It gets ONE named exception,
 * the same kind `dialog-wiring.test.js` already carves out of that general
 * rule for `sheet-dialog.js`'s own modal-stack wiring: the behaviors this
 * WP's brief (and WP 4h.3's before it) demand a mutation-tested pin for.
 *
 *   1. The `<img>` src is built through `lib/cover-art.js`'s shipped
 *      `headerArtUrl(appid)` mechanism, never a hardcoded string
 *      (`cover-art.test.js` already pins `headerArtUrl` itself; this file
 *      additionally proves `buildHeaderArt` actually calls it, varying with
 *      the appid it is given).
 *   2. Graceful absence: on an image load failure the ENTIRE wrapper is
 *      removed — not just the `<img>` — so no reserved band survives a 404.
 *      Contrast `game-card.js`'s `buildCover()`, where `img.remove()` alone
 *      is correct because a styled fallback tile stays underneath; this
 *      component has no such tile. Under WP 4h.5's load-then-reveal design
 *      this matters even more directly than it did under WP 4h.3: nothing
 *      was ever reserved before removal, so there is nothing left to prove
 *      about a "collapse" — there was never anything to collapse.
 *   3. New in WP 4h.5: the wrapper starts WITHOUT the `.loaded` class (zero
 *      reserved size, per the CSS section below) and gains it only once the
 *      image actually signals ready — proving the reveal never fires early.
 *
 * WP 4h.5 also changes the fake-DOM harness's relevance for the reveal path
 * itself: `buildHeaderArt` prefers `img.decode()` when available and falls
 * back to the `load` event otherwise. `fake-dom.js`'s `FakeElement` has no
 * `decode` method, so every test below exercises the fallback `load`-event
 * path — this is deliberate and is what a real browser lacking `decode()`
 * support would also do; the decode() branch itself is plain use of a
 * standard Promise-returning DOM API with no project-specific logic beyond
 * "call reveal on success, swallow the rejection" (the module's own header
 * explains why), so it is not separately re-implemented in this harness.
 *
 * Uses the shared `web/tests/fake-dom.js` harness (same one
 * `css-overlay-geometry.test.js`/`dialog-wiring.test.js`/`modal-stack.
 * test.js` use) — `header-art.js` only touches `document.createElement`
 * inside `buildHeaderArt()`, never at module load, so a fresh fake
 * `{document}` set as `globalThis.document` before importing is enough (same
 * reasoning `game-card.test.js`'s header gives for `game-card.js` itself).
 *
 * The CSS section below pins the load-then-reveal resolution this WP's
 * brief asks to be stated and pinned explicitly: `.header-art` reserves
 * NOTHING in its base state (no `aspect-ratio`/`height`/`min-height`, a
 * `grid-template-rows:0fr` track), `.header-art.loaded` reveals the true
 * 460/215 box via `grid-template-rows:1fr`, the `<img>` itself carries the
 * real `aspect-ratio:460/215`, and the reveal transition carries no
 * `!important` (so the standing whole-app `prefers-reduced-motion`
 * wildcard, pinned separately in `keyboard-pointer-model.test.js`, governs
 * it automatically).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { createFakeDom } from "./fake-dom.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const webDir = path.join(__dirname, "..");

// ---------------------------------------------------------------------
// 1. Behavioural pins (fake DOM) — buildHeaderArt itself.
// ---------------------------------------------------------------------

function findChild(el, tagName) {
  return el.children.find((c) => c.tagName === tagName) || null;
}

test("buildHeaderArt: the <img> src is built via the shipped headerArtUrl(appid) mechanism, and varies with the appid (mutation: hardcode a URL -> dies)", async () => {
  const dom = createFakeDom();
  globalThis.document = dom.document;
  globalThis.window = dom.window;
  const { buildHeaderArt } = await import("../js/components/header-art.js");
  const { headerArtUrl } = await import("../js/lib/cover-art.js");

  const wrapA = buildHeaderArt(440);
  const imgA = findChild(wrapA, "IMG");
  assert.ok(imgA, "buildHeaderArt(440) produced no <img>");
  assert.equal(imgA.src, headerArtUrl(440));

  const wrapB = buildHeaderArt(2010010);
  const imgB = findChild(wrapB, "IMG");
  assert.equal(imgB.src, headerArtUrl(2010010));

  // The load-bearing mutation-kill: a hardcoded (or appid-440-only) URL
  // would make imgB.src equal imgA.src instead of tracking its own appid.
  assert.notEqual(imgA.src, imgB.src, "the <img> src must be a function of the appid passed to buildHeaderArt, not a fixed/hardcoded URL");
});

test("buildHeaderArt: an <img> load failure removes the WHOLE wrapper, not just the <img> — no band ever appeared to collapse (mutation: drop the error handler -> dies)", async () => {
  const dom = createFakeDom();
  globalThis.document = dom.document;
  globalThis.window = dom.window;
  const { buildHeaderArt } = await import("../js/components/header-art.js");

  const container = dom.document.createElement("div");
  const wrap = buildHeaderArt(440);
  container.appendChild(wrap);
  assert.equal(container.children.length, 1, "sanity: the wrapper was appended");

  const img = findChild(wrap, "IMG");
  img.dispatchEvent({ type: "error" });

  assert.equal(
    container.children.length,
    0,
    "the wrapper must be gone from its parent entirely after an image load failure — a guard that only hides/removes the <img> itself would leave this at 1",
  );
});

test("buildHeaderArt: the wrapper starts WITHOUT the .loaded class — nothing is revealed before the image is known-good (mutation: add .loaded eagerly -> dies)", async () => {
  const dom = createFakeDom();
  globalThis.document = dom.document;
  globalThis.window = dom.window;
  const { buildHeaderArt } = await import("../js/components/header-art.js");

  const wrap = buildHeaderArt(440);
  assert.equal(wrap.classList.contains("loaded"), false, "buildHeaderArt must not add .loaded synchronously — reveal only happens once the image signals ready");
});

test("buildHeaderArt: a successful load reveals the wrapper by adding .loaded (mutation: drop the reveal listener -> dies)", async () => {
  const dom = createFakeDom();
  globalThis.document = dom.document;
  globalThis.window = dom.window;
  const { buildHeaderArt } = await import("../js/components/header-art.js");

  const wrap = buildHeaderArt(440);
  const img = findChild(wrap, "IMG");
  // fake-dom's FakeElement has no `decode()` method, so buildHeaderArt's
  // fallback path is exercised here: reveal is wired to the "load" event.
  assert.equal(typeof img.decode, "undefined", "sanity: this harness has no decode() — the load-event fallback is what's under test");

  img.dispatchEvent({ type: "load" });

  assert.equal(wrap.classList.contains("loaded"), true, "a successful load must add .loaded to the wrapper");
});

test("buildHeaderArt: the <img> is decorative (empty alt) — same discipline as the grid cover/mini-cover", async () => {
  const dom = createFakeDom();
  globalThis.document = dom.document;
  globalThis.window = dom.window;
  const { buildHeaderArt } = await import("../js/components/header-art.js");
  const img = findChild(buildHeaderArt(440), "IMG");
  assert.equal(img.alt, "");
});

// ---------------------------------------------------------------------
// 2. Production wiring — game-detail-sheet.js actually calls buildHeaderArt
// (the WP 4a.1/4a.3 failure class: a mechanism with zero real callers).
// ---------------------------------------------------------------------

test("game-detail-sheet.js imports header-art.js and calls buildHeaderArt(state.appid) inside render()", () => {
  const detailJs = readFileSync(path.join(webDir, "js", "components", "game-detail-sheet.js"), "utf8");
  assert.match(
    detailJs,
    /import\s*\{\s*buildHeaderArt\s*\}\s*from\s*["']\.\/header-art\.js["']/,
    "game-detail-sheet.js no longer imports buildHeaderArt from ./header-art.js",
  );
  assert.match(
    detailJs,
    /hero\.append\(\s*buildHeaderArt\(state\.appid\)\s*\);\s*contentEl\.append\(hero\)/,
    "game-detail-sheet.js no longer appends buildHeaderArt(state.appid) into the hero band in contentEl — the header art has no real caller",
  );
});

// ---------------------------------------------------------------------
// 3. CSS: the hero band and the art overlay (WP LIBRARY-2), pinned structurally.
// ---------------------------------------------------------------------

const kitCssPath = path.join(webDir, "css", "kit.css");
const kitCss = readFileSync(kitCssPath, "utf8");

function stripComments(css) {
  return css.replace(/\/\*[\s\S]*?\*\//g, "");
}

/** Same brace-balanced single-rule extractor css-overlay-geometry.test.js
 * uses — duplicated locally per this codebase's own convention. */
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

const strippedKitCss = stripComments(kitCss);

// WP LIBRARY-2: the hero band is permanent (`.gd-hero`, fixed height, the
// fallback-hue gradient), and the header art is an overlay inside it — the
// WP 4h.5 grow-in (0fr -> 1fr) is gone because nothing grows any more.

test(".gd-hero is a fixed-height band with a gradient of its own (the band never depends on the art)", () => {
  const body = ruleBody(strippedKitCss, ".kit .gd-hero");
  assert.ok(body, ".kit .gd-hero rule not found in kit.css");
  assert.match(body, /height:140px/);
  assert.match(body, /overflow:hidden/);
  assert.match(body, /linear-gradient\(160deg, hsl\(var\(--h1\)/);
});

test(".header-art is an absolutely positioned overlay that starts invisible (mutation: start at opacity 1 -> dies)", () => {
  const body = ruleBody(strippedKitCss, ".kit .header-art");
  assert.ok(body, ".kit .header-art rule not found in kit.css");
  assert.match(body, /position:absolute/);
  assert.match(body, /inset:0/);
  assert.match(body, /opacity:0/);
  assert.doesNotMatch(body, /aspect-ratio\s*:|(^|;|\s)height\s*:/, "the overlay reserves nothing of its own");
});

test(".header-art.loaded reveals it via opacity:1", () => {
  const body = ruleBody(strippedKitCss, ".kit .header-art.loaded");
  assert.ok(body, ".kit .header-art.loaded rule not found");
  assert.match(body, /opacity\s*:\s*1\b/);
});

test(".header-art declares its own fade with NO !important, so the reduced-motion wildcard governs it (mutation: add !important -> dies)", () => {
  const body = ruleBody(strippedKitCss, ".kit .header-art");
  assert.match(body, /transition\s*:/);
  assert.doesNotMatch(body, /!important/);
});

test(".header-art img fills the band (object-fit:cover), is a block element and can shrink (min-height:0)", () => {
  const body = ruleBody(strippedKitCss, ".kit .header-art img");
  assert.ok(body, ".kit .header-art img rule not found");
  assert.match(body, /display\s*:\s*block/);
  assert.match(body, /object-fit\s*:\s*cover/);
  assert.match(body, /min-height\s*:\s*0\b/);
  assert.match(body, /height:100%/);
});

test("app.css carries no header-art rules any more (one owner: kit.css)", () => {
  const appCss = stripComments(readFileSync(path.join(webDir, "css", "app.css"), "utf8"));
  assert.doesNotMatch(appCss, /\.header-art/);
});
