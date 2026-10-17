/**
 * WP LIBRARY-DETAIL-1: the shared delete-confirm component
 * (components/delete-confirm.js) behind both the Library's bulk delete and
 * the game detail sheet's single delete. Behaviour tests on the fake DOM plus
 * wiring pins that both callers really use it (and nothing else builds a
 * confirm dialog of its own any more).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createFakeDom, fakeKeyEvent } from "./fake-dom.js";
import { resetModalStack, modalDepth } from "../js/lib/modal-stack.js";

const dom = createFakeDom();
globalThis.document = dom.document;
globalThis.window = dom.window;

const { createDeleteConfirm, buildPlanNodes, buildIntroNodes } = await import("../js/components/delete-confirm.js");

beforeEach(() => {
  resetModalStack(dom.document);
});

function make() {
  const calls = { confirm: 0, close: 0 };
  const dc = createDeleteConfirm({
    onConfirm: () => {
      calls.confirm++;
    },
    onClose: () => {
      calls.close++;
    },
  });
  const buttons = dc.backdrop.querySelectorAll("button");
  const keep = buttons.find((b) => b.textContent === "Keep");
  const del = buttons.find((b) => b.textContent === "Delete");
  return { dc, calls, keep, del };
}

const PLAN = {
  freedBytes: 5e9,
  occupiedBytes: 8e9,
  keptBytes: 3e9,
  sharedRows: [
    { depotid: 11, free: false, sizeBytes: 3e9, holderAppids: [2], others: [] },
    { depotid: 12, free: true, sizeBytes: 5e9, holderAppids: [], others: [3] },
  ],
};
const names = (ids) => ids.map((i) => `G${i}`).join(", ");

test("the dialog is an alertdialog on document.body, hidden until opened", () => {
  const { dc } = make();
  assert.equal(dc.backdrop.parentNode, dom.document.body);
  assert.equal(dc.backdrop.className, "dialog-backdrop");
  assert.equal(dc.isOpen(), false);
  const dialog = dc.backdrop.children[0];
  assert.equal(dialog.getAttribute("role"), "alertdialog");
  assert.equal(dialog.getAttribute("aria-modal"), "true");
});

test("open(): title, calculating text, Delete disabled, Keep focused, #app inert, invoker remembered", () => {
  const { dc, keep, del } = make();
  const invoker = dom.document.createElement("button");
  dom.document.activeElement = invoker;
  dc.open("Delete 3 games from cache?");
  assert.equal(dc.isOpen(), true);
  assert.equal(dc.backdrop.querySelector("h3").textContent, "Delete 3 games from cache?");
  assert.match(dc.backdrop.querySelector("p").textContent, /Calculating/);
  assert.equal(del.disabled, true);
  assert.equal(dom.document.activeElement, keep, "the non-destructive button takes the initial focus");
  assert.equal(dom.appRoot.getAttribute("inert"), "");
  dc.close();
  assert.equal(dom.document.activeElement, invoker, "focus returns to the invoker");
  assert.equal(dom.appRoot.hasAttribute("inert"), false);
});

test("re-opening while open (the 409 re-confirm) resets the content without a second modal push or a new invoker", () => {
  const { dc } = make();
  const invoker = dom.document.createElement("button");
  dom.document.activeElement = invoker;
  dc.open();
  dc.setNote([dom.document.createElement("div")]);
  dc.setConfirmEnabled(true);
  dc.open("Delete from cache?");
  assert.equal(modalDepth(), 1);
  assert.equal(dc.backdrop.querySelectorAll(".dialog div").length, 2, "only the note holder and the button row remain (note emptied)");
  dc.close();
  assert.equal(dom.document.activeElement, invoker);
});

test("Keep closes; Delete calls onConfirm; the two buttons follow setBusy", () => {
  const { dc, calls, keep, del } = make();
  dc.open();
  dc.setConfirmEnabled(true);
  del.dispatchEvent({ type: "click" });
  assert.equal(calls.confirm, 1);
  dc.setBusy(true);
  assert.equal(del.disabled, true);
  assert.equal(keep.disabled, true);
  dc.setBusy(false);
  assert.equal(del.disabled, false);
  assert.equal(keep.disabled, false);
  keep.dispatchEvent({ type: "click" });
  assert.equal(dc.isOpen(), false);
  assert.equal(calls.close, 1);
});

test("Escape goes through the modal stack and closes only this dialog, running onClose", () => {
  const { dc, calls } = make();
  dc.open();
  dom.document.dispatchEvent(fakeKeyEvent("Escape"));
  assert.equal(dc.isOpen(), false);
  assert.equal(calls.close, 1);
  assert.equal(modalDepth(), 0);
});

test("showError: the reason is shown and Delete stays disabled", () => {
  const { dc, del } = make();
  dc.open();
  dc.setConfirmEnabled(true);
  dc.showError("boom");
  assert.equal(dc.backdrop.querySelector("p").textContent, "Could not calculate the delete plan: boom");
  assert.equal(del.disabled, true);
});

test("buildPlanNodes: no shared depots gives the single 'full amount is freed' line", () => {
  const nodes = buildPlanNodes({ ...PLAN, sharedRows: [] }, new Map(), names);
  assert.equal(nodes.length, 1);
  assert.equal(nodes[0].className, "cfsolo");
  assert.equal(nodes[0].textContent, "No shared depots — the full 7.5 GB is freed.");
});

test("buildPlanNodes: label, one row per shared depot with freed/kept and the reason, then the totals", () => {
  const nodes = buildPlanNodes(PLAN, new Map(), names);
  assert.deepEqual(nodes.map((n) => n.className), ["cflabel", "cflist", "cftotal"]);
  assert.equal(nodes[0].textContent, "2 shared depots");
  const [keepRow, freeRow] = nodes[1].children;
  assert.equal(keepRow.className, "keep");
  assert.equal(keepRow.querySelector(".mk").textContent, "kept");
  assert.equal(keepRow.querySelector(".why").textContent, "G2 still cached");
  assert.equal(freeRow.className, "free");
  assert.equal(freeRow.querySelector(".mk").textContent, "freed");
  assert.equal(freeRow.querySelector(".why").textContent, "no cached co-owner — G3 is not cached");
  assert.equal(nodes[2].textContent, "4.7 GB freed · 2.8 GB stays on disk");
});

test("buildIntroNodes: one game reads 'it occupies', a selection 'they occupy'", () => {
  const text = (nodes) => nodes.map((n) => (typeof n === "string" ? n : n.textContent)).join("");
  assert.equal(text(buildIntroNodes("Foo", PLAN)), "Deleting Foo frees about 4.7 GB of the 7.5 GB it occupies.");
  assert.equal(text(buildIntroNodes("2 games", PLAN, { many: true })), "Deleting 2 games frees about 4.7 GB of the 7.5 GB they occupy.");
});

test("wiring: library.js and game-detail-sheet.js both use the shared component and build no confirm dialog of their own", () => {
  for (const file of ["views/library.js", "components/game-detail-sheet.js"]) {
    const src = readFileSync(new URL(`../js/${file}`, import.meta.url), "utf8");
    assert.match(src, /createDeleteConfirm\(\{ onConfirm: confirmDelete, onClose: onDeleteConfirmClosed \}\)/, file);
    assert.doesNotMatch(src, /"Confirm deletion"/, `${file} still builds its own delete dialog`);
    assert.doesNotMatch(src, /deleteConfirmInvokerEl|deleteInvokerEl/, file);
  }
  const lib = readFileSync(new URL("../js/views/library.js", import.meta.url), "utf8");
  assert.doesNotMatch(lib, /import[^;]*\b(pushModal|popModal)\b/, "library.js should reach the modal stack only through the shared component");
});
