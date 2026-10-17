/**
 * components/sheet-dialog.js: drag the grab handle down to close.
 * fake-dom, no browser; pointer events are plain objects.
 */
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { createFakeDom } from "./fake-dom.js";
import { resetModalStack } from "../js/lib/modal-stack.js";

const dom = createFakeDom();
globalThis.document = dom.document;
globalThis.window = dom.window;
const { createSheetDialog } = await import("../js/components/sheet-dialog.js");

beforeEach(() => resetModalStack(dom.document));

function setup() {
  const dlg = createSheetDialog({ ariaLabel: "t" });
  dlg.open();
  const grab = dlg.sheet.children[0];
  const ev = (type, y, t) => grab.dispatchEvent({ type, pointerId: 1, clientY: y, timeStamp: t, button: 0 });
  return { dlg, grab, ev };
}

test("a slow drag past the threshold follows the finger, then closes", () => {
  const { dlg, ev } = setup();
  ev("pointerdown", 100, 0);
  ev("pointermove", 150, 500);
  assert.equal(dlg.sheet.style.transform, "translateY(50px)");
  assert.equal(dlg.sheet.style.transition, "none");
  ev("pointermove", 200, 1000);
  ev("pointerup", 200, 1000);
  assert.equal(dlg.isOpen(), false);
  assert.equal(dlg.sheet.style.transform, "");
  assert.equal(dlg.sheet.style.transition, "");
});

test("a short slow drag snaps back and stays open", () => {
  const { dlg, ev } = setup();
  ev("pointerdown", 100, 0);
  ev("pointermove", 140, 800);
  ev("pointerup", 140, 800);
  assert.equal(dlg.isOpen(), true);
  assert.equal(dlg.sheet.style.transform, "");
});

test("a fast flick below the distance threshold closes", () => {
  const { dlg, ev } = setup();
  ev("pointerdown", 100, 0);
  ev("pointermove", 140, 40);
  ev("pointerup", 140, 40);
  assert.equal(dlg.isOpen(), false);
});

test("dragging up never moves the sheet above its resting place", () => {
  const { dlg, ev } = setup();
  ev("pointerdown", 100, 0);
  ev("pointermove", 60, 100);
  assert.equal(dlg.sheet.style.transform, "translateY(0px)");
  ev("pointerup", 60, 100);
  assert.equal(dlg.isOpen(), true);
});

test("pointercancel snaps back even past the threshold", () => {
  const { dlg, ev } = setup();
  ev("pointerdown", 100, 0);
  ev("pointermove", 300, 2000);
  ev("pointercancel", 300, 2000);
  assert.equal(dlg.isOpen(), true);
  assert.equal(dlg.sheet.style.transform, "");
});

test("pointermove during a running close animation does not re-apply the drag transform", () => {
  const { dlg, ev } = setup();
  globalThis.getComputedStyle = () => ({ transitionDuration: "300ms", transitionDelay: "0s" });
  try {
    ev("pointerdown", 100, 0);
    ev("pointermove", 150, 500);
    dlg.close();
    assert.equal(dlg.sheet.style.transform, "");
    ev("pointermove", 200, 600);
    assert.equal(dlg.sheet.style.transform, "");
    assert.equal(dlg.sheet.style.transition, "");
  } finally {
    delete globalThis.getComputedStyle;
  }
});

test("the backdrop tap still closes", () => {
  const { dlg } = setup();
  dlg.backdrop.dispatchEvent({ type: "click", target: dlg.backdrop });
  assert.equal(dlg.isOpen(), false);
});

test("closing mid-drag leaves no stale drag or inline style; a new drag works after reopen", () => {
  const { dlg, ev } = setup();
  ev("pointerdown", 100, 0);
  ev("pointermove", 150, 500);
  dlg.close();
  dlg.open();
  assert.equal(dlg.sheet.style.transform, "");
  assert.equal(dlg.sheet.style.transition, "");
  ev("pointerdown", 100, 0);
  ev("pointermove", 120, 500);
  assert.equal(dlg.sheet.style.transform, "translateY(20px)");
  ev("pointermove", 200, 1000);
  ev("pointerup", 200, 1000);
  assert.equal(dlg.isOpen(), false);
});

test("lostpointercapture snaps back like pointercancel", () => {
  const { dlg, ev } = setup();
  ev("pointerdown", 100, 0);
  ev("pointermove", 300, 2000);
  ev("lostpointercapture", 300, 2000);
  assert.equal(dlg.isOpen(), true);
  assert.equal(dlg.sheet.style.transform, "");
});

test("a second pointer is ignored while a drag runs", () => {
  const { dlg, grab, ev } = setup();
  ev("pointerdown", 100, 0);
  grab.dispatchEvent({ type: "pointermove", pointerId: 2, clientY: 400, timeStamp: 10, button: 0 });
  assert.equal(dlg.sheet.style.transform, "");
  grab.dispatchEvent({ type: "pointerup", pointerId: 2, clientY: 400, timeStamp: 20, button: 0 });
  assert.equal(dlg.isOpen(), true);
  ev("pointermove", 120, 500);
  assert.equal(dlg.sheet.style.transform, "translateY(20px)");
});

test("pointerdown while closed or from a mouse starts nothing", () => {
  const { dlg, grab, ev } = setup();
  grab.dispatchEvent({ type: "pointerdown", pointerId: 1, pointerType: "mouse", clientY: 100, timeStamp: 0, button: 0 });
  ev("pointermove", 300, 10);
  assert.equal(dlg.sheet.style.transform, "");
  dlg.close();
  ev("pointerdown", 100, 0);
  ev("pointermove", 300, 10);
  assert.equal(dlg.sheet.style.transform, "");
});
