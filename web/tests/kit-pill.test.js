/**
 * `createPill` and the hidden /kit page (WP UI-KIT-1, ADR-0028), fake-dom.
 *
 * Asserts on TEXT and class strings, never on nodes (docs/LEARNINGS.md,
 * WP WEB-FIX-1: a failing node assertion dumps the fake-DOM graph and
 * OOM-kills the process).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createFakeDom } from "./fake-dom.js";

const dom = createFakeDom();
globalThis.document = dom.document;
globalThis.window = dom.window;

const { createPill, DEFAULT_PILL_TONE, PILL_TONES } = await import("../js/components/pill.js");
const { STATUS_LABEL } = await import("../js/components/status-icon.js");
const { renderKit } = await import("../js/views/kit.js");
const { VIEWS } = await import("../js/router.js");

test("createPill: <span class='pill TONE'> wrapping the status disc (.sic.k-KIND) and the visible word", () => {
  const pill = createPill({ kind: "running", text: "Downloading", tone: "run" });
  assert.equal(pill.tagName, "SPAN");
  assert.equal(pill.className, "pill run");
  const icon = pill.querySelector(".sic");
  assert.ok(icon, "no .sic disc inside the pill");
  assert.equal(icon.className, "sic k-running");
  // The disc is first, the word second; the icon's sr-only fallback is
  // dropped because the visible word already carries it.
  assert.equal(pill.textContent, "Downloading");
  assert.equal(pill.querySelector(".sr-only"), null, "duplicate screen-reader word inside a worded pill");
  assert.ok(pill.querySelector("svg"), "the disc lost its glyph");
});

test("createPill: an icon-only pill keeps the status icon's screen-reader word", () => {
  const pill = createPill({ kind: "error" });
  assert.equal(pill.className, "pill danger");
  assert.equal(pill.textContent, STATUS_LABEL.error);
});

test("createPill: tone defaults per kind, null means the neutral pill, unknown tones are dropped", () => {
  assert.equal(createPill({ kind: "cached", text: "Online" }).className, "pill ok");
  assert.equal(createPill({ kind: "stale", text: "Update ready" }).className, "pill acc");
  assert.equal(createPill({ kind: "paused", text: "Paused" }).className, "pill");
  assert.equal(createPill({ kind: "paused", text: "Paused", tone: null }).className, "pill");
  assert.equal(createPill({ kind: "cached", text: "x", tone: "purple" }).className, "pill");
  // Every status kind has a default tone entry, and every entry is a kit tone.
  assert.deepEqual(Object.keys(DEFAULT_PILL_TONE).sort(), Object.keys(STATUS_LABEL).sort());
  for (const tone of Object.values(DEFAULT_PILL_TONE)) {
    assert.ok(tone === null || PILL_TONES.includes(tone), `unknown default tone ${tone}`);
  }
});

test("renderKit: a .kit root with the demo strings, every primitive twice (full width + 400px frame)", () => {
  const root = renderKit();
  assert.equal(root.className, "view-kit kit kit-page");
  assert.equal(root.querySelector("h1").textContent, "UI kit");
  const text = root.textContent;
  for (const s of ["Page title", "Connected", "Hit rate", "Agent outdated", "Partially cached", "Changed here", "From env", "Takes effect within a minute.", "Drag the handle to change the order.", "Satisfactory", "+2 more"]) {
    assert.ok(text.includes(s), `demo string missing: ${s}`);
  }
  assert.equal(root.querySelectorAll(".kit-frame").length, 1);
  assert.equal(root.querySelectorAll(".tiles").length, 2, "tiles once at full width, once in the frame");
  assert.equal(root.querySelectorAll(".fcard").length, 2);
  assert.equal(root.querySelectorAll(".pill").length, 2 * 16, "15 vocabulary pills + the Agents tile pill, twice");
  assert.equal(root.querySelectorAll(".seg button[aria-pressed=\"true\"]").length, 2);
});

test("router: kit is a view (hidden route) and index.html has no nav button for it", () => {
  assert.ok(VIEWS.includes("kit"));
});
