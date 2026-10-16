/**
 * SHEET-MOTION-1: close waits for the transition end (with a timeout
 * fallback), closes at once when no transition runs, keeps the modal-stack
 * bookkeeping synchronous; plus source pins for the CSS side. The animation
 * itself is not verified here (no browser on the devbox).
 *
 * Run: node --test "web/tests/*.test.js"
 */
import { test, beforeEach, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createFakeDom, fakeKeyEvent } from "./fake-dom.js";
import { resetModalStack, modalDepth } from "../js/lib/modal-stack.js";

const dom = createFakeDom();
globalThis.document = dom.document;
globalThis.window = dom.window;

let computed = { transitionDuration: "0.28s", transitionDelay: "0s" };
globalThis.getComputedStyle = () => computed;
after(() => { delete globalThis.getComputedStyle; });

const { createSheetDialog } = await import("../js/components/sheet-dialog.js");

beforeEach(() => {
  computed = { transitionDuration: "0.28s", transitionDelay: "0s" };
  resetModalStack(dom.document);
});

function endEvent(target) {
  return { type: "transitionend", target, propertyName: "opacity" };
}

test("open adds on and shown", () => {
  const d = createSheetDialog({ ariaLabel: "t" });
  d.open();
  assert.ok(d.backdrop.classList.contains("on"));
  assert.ok(d.backdrop.classList.contains("shown"));
  d.close();
  d.backdrop.dispatchEvent(endEvent(d.sheet));
});

test("close keeps the backdrop displayed until transitionend", () => {
  const d = createSheetDialog({ ariaLabel: "t" });
  d.open();
  d.close();
  assert.equal(d.backdrop.classList.contains("shown"), false);
  assert.equal(d.backdrop.classList.contains("on"), true, "still displayed while animating out");
  assert.equal(d.isOpen(), false);
  d.backdrop.dispatchEvent(endEvent(d.sheet));
  assert.equal(d.backdrop.classList.contains("on"), false);
});

test("transitionend from a descendant does not finish the close", async () => {
  const d = createSheetDialog({ ariaLabel: "t" });
  d.open();
  d.close();
  d.backdrop.dispatchEvent(endEvent(d.body));
  assert.equal(d.backdrop.classList.contains("on"), true);
  d.backdrop.dispatchEvent(endEvent(d.sheet));
  assert.equal(d.backdrop.classList.contains("on"), false);
});

test("backdrop transitionend arriving first (shorter fade) does not finish the close", () => {
  const d = createSheetDialog({ ariaLabel: "t" });
  d.open();
  d.close();
  d.backdrop.dispatchEvent(endEvent(d.backdrop));
  assert.equal(d.backdrop.classList.contains("on"), true, "sheet slide still running");
  d.backdrop.dispatchEvent(endEvent(d.sheet));
  assert.equal(d.backdrop.classList.contains("on"), false);
});

test("a closing scrim (not .shown) lets clicks through", () => {
  const rule = /\.sheet-backdrop:not\(\.shown\)\{[^}]*pointer-events:none/;
  assert.match(readFileSync(new URL("../css/app.css", import.meta.url), "utf8"), rule);
});

test("timeout fallback closes when transitionend never fires", async () => {
  computed = { transitionDuration: "10ms", transitionDelay: "0s" };
  const d = createSheetDialog({ ariaLabel: "t" });
  d.open();
  d.close();
  assert.equal(d.backdrop.classList.contains("on"), true);
  await new Promise((r) => setTimeout(r, 250));
  assert.equal(d.backdrop.classList.contains("on"), false);
});

test("closes immediately when no transition runs (reduced motion clamp)", () => {
  computed = { transitionDuration: "0.001ms", transitionDelay: "0s" };
  const d = createSheetDialog({ ariaLabel: "t" });
  d.open();
  d.close();
  assert.equal(d.backdrop.classList.contains("on"), false);
});

test("closes immediately when styles cannot be measured (no getComputedStyle)", () => {
  const saved = globalThis.getComputedStyle;
  delete globalThis.getComputedStyle;
  try {
    const d = createSheetDialog({ ariaLabel: "t" });
    d.open();
    d.close();
    assert.equal(d.backdrop.classList.contains("on"), false);
  } finally {
    globalThis.getComputedStyle = saved;
  }
});

test("modal stack, onClose and focus return stay synchronous on close", () => {
  let closed = 0;
  const d = createSheetDialog({ ariaLabel: "t", onClose: () => { closed += 1; } });
  const invoker = dom.document.createElement("button");
  dom.document.activeElement = invoker;
  d.open();
  assert.equal(modalDepth(), 1);
  d.close();
  assert.equal(modalDepth(), 0, "popped before the transition ends");
  assert.equal(closed, 1);
  assert.equal(dom.document.activeElement, invoker);
  d.close(); // second close while animating is a no-op
  assert.equal(closed, 1);
  d.backdrop.dispatchEvent(endEvent(d.sheet));
});

test("Escape closes only the topmost sheet; order preserved with animated close", () => {
  const a = createSheetDialog({ ariaLabel: "a" });
  const b = createSheetDialog({ ariaLabel: "b" });
  a.open();
  b.open();
  dom.document.dispatchEvent(fakeKeyEvent("Escape"));
  assert.equal(b.isOpen(), false);
  assert.equal(a.isOpen(), true);
  assert.equal(a.backdrop.getAttribute("inert"), null, "a is topmost again at once");
  dom.document.dispatchEvent(fakeKeyEvent("Escape"));
  assert.equal(a.isOpen(), false);
  assert.equal(modalDepth(), 0);
  a.backdrop.dispatchEvent(endEvent(a.sheet));
  b.backdrop.dispatchEvent(endEvent(b.sheet));
});

test("reopening during the close animation cancels the pending hide", () => {
  const d = createSheetDialog({ ariaLabel: "t" });
  d.open();
  d.close();
  d.open();
  d.backdrop.dispatchEvent(endEvent(d.sheet));
  assert.equal(d.isOpen(), true);
  assert.ok(d.backdrop.classList.contains("on"));
  d.close();
  d.backdrop.dispatchEvent(endEvent(d.sheet));
});

const css = readFileSync(new URL("../css/app.css", import.meta.url), "utf8");
const theme = readFileSync(new URL("../css/theme.css", import.meta.url), "utf8");

test("motion durations and easing come from theme.css tokens", () => {
  for (const t of ["--dur-sheet", "--dur-fade", "--ease-sheet"]) {
    assert.match(theme, new RegExp(`${t}\\s*:`), `${t} defined`);
  }
  assert.match(css, /\.sheet\{[^}]*transition:transform var\(--dur-sheet\) var\(--ease-sheet\)/);
  assert.match(css, /\.sheet-backdrop\{[^}]*transition:opacity var\(--dur-fade\) var\(--ease-sheet\)/);
  assert.match(css, /\.sheet--drawer\{\s*transform:translateX\(100%\)/);
  assert.match(css, /\.sheet--center\{[^}]*opacity:0/);
  assert.match(css, /\.sheet-backdrop\.shown \.sheet\{ transform:none; \}/);
});

test("confirm dialogs animate in via the shared tokens", () => {
  assert.match(css, /\.dialog-backdrop\.on\{[^}]*animation:dialog-fade var\(--dur-fade\) var\(--ease-sheet\)/);
  assert.match(css, /\.dialog\{[^}]*animation:dialog-card var\(--dur-sheet\) var\(--ease-sheet\)/);
});

test("reduced motion clamps transition and animation durations globally", () => {
  assert.match(theme, /prefers-reduced-motion: reduce\)\{[^}]*transition-duration:\.001ms !important/s);
  assert.match(theme, /prefers-reduced-motion: reduce\)\{[^}]*animation-duration:\.001ms !important/s);
});
