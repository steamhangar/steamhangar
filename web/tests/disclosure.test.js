/**
 * lib/disclosure.js (WP LIBRARY-1): the shared menu / popover behaviour of
 * the Library's split button and Filters popover. fake-dom, no browser.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createFakeDom, fakeKeyEvent } from "./fake-dom.js";
import { createDisclosure } from "../js/lib/disclosure.js";

function setup({ menu = false } = {}) {
  const dom = createFakeDom();
  const doc = dom.document;
  const wrap = doc.createElement("div");
  const trigger = doc.createElement("button");
  const panel = doc.createElement("div");
  const items = [];
  if (menu) {
    for (let i = 0; i < 3; i++) {
      const it = doc.createElement("button");
      it.setAttribute("role", "menuitem");
      items.push(it);
      panel.appendChild(it);
    }
  }
  wrap.append(trigger, panel);
  dom.appRoot.appendChild(wrap);
  const d = createDisclosure({ trigger, panel, menu, doc });
  const key = (k) => doc.dispatchEvent(fakeKeyEvent(k));
  return { doc, trigger, panel, items, d, key, outside: doc.createElement("div") };
}

test("menu opens when querySelectorAll returns a NodeList without .filter (real browsers)", () => {
  const { trigger, panel, d } = setup({ menu: true });
  const real = panel.querySelectorAll.bind(panel);
  panel.querySelectorAll = (sel) => {
    const list = real(sel);
    return { length: list.length, item: (i) => list[i], [Symbol.iterator]: () => list[Symbol.iterator]() };
  };
  trigger.dispatchEvent({ type: "click", target: trigger });
  assert.equal(d.isOpen(), true);
  assert.equal(panel.hidden, false);
  assert.equal(trigger.getAttribute("aria-expanded"), "true");
});

test("starts closed: aria-expanded=false, panel hidden", () => {
  const { trigger, panel } = setup();
  assert.equal(trigger.getAttribute("aria-expanded"), "false");
  assert.equal(panel.hidden, true);
});

test("trigger click toggles open and closed, mirrored in aria-expanded", () => {
  const { trigger, panel, d } = setup();
  trigger.dispatchEvent({ type: "click", target: trigger });
  assert.equal(panel.hidden, false);
  assert.equal(trigger.getAttribute("aria-expanded"), "true");
  assert.equal(d.isOpen(), true);
  trigger.dispatchEvent({ type: "click", target: trigger });
  assert.equal(panel.hidden, true);
  assert.equal(trigger.getAttribute("aria-expanded"), "false");
});

test("Escape closes and returns focus to the trigger", () => {
  const { doc, trigger, panel, d, key } = setup();
  d.open();
  key("Escape");
  assert.equal(panel.hidden, true);
  assert.equal(doc.activeElement, trigger);
});

test("a click outside closes without stealing focus; a click inside stays open", () => {
  const { doc, panel, d, outside } = setup();
  d.open();
  const inner = doc.createElement("span");
  panel.appendChild(inner);
  doc.dispatchEvent({ type: "click", target: inner });
  assert.equal(d.isOpen(), true, "inside click keeps a popover open");
  doc.dispatchEvent({ type: "click", target: outside });
  assert.equal(d.isOpen(), false);
});

test("Escape while closed is a no-op; dispose() closes and unbinds the trigger", () => {
  const { trigger, panel, d, key } = setup();
  key("Escape");
  assert.equal(panel.hidden, true);
  d.open();
  d.dispose();
  assert.equal(d.isOpen(), false);
  assert.equal(panel.hidden, true);
  trigger.dispatchEvent({ type: "click", target: trigger });
  assert.equal(d.isOpen(), false, "the trigger listener is gone after dispose");
});

test("menu: ArrowDown on the trigger opens and focuses the first item, ArrowUp the last", () => {
  const { doc, trigger, items, d } = setup({ menu: true });
  const down = fakeKeyEvent("ArrowDown");
  trigger.dispatchEvent(down);
  assert.equal(d.isOpen(), true);
  assert.equal(down.defaultPrevented, true);
  assert.equal(doc.activeElement, items[0]);
  d.close();
  trigger.dispatchEvent(fakeKeyEvent("ArrowUp"));
  assert.equal(doc.activeElement, items[2]);
});

test("menu: arrows wrap, Home/End jump, Tab closes", () => {
  const { doc, panel, items, d } = setup({ menu: true });
  d.open({ focus: "first" });
  panel.dispatchEvent(fakeKeyEvent("ArrowDown"));
  assert.equal(doc.activeElement, items[1]);
  panel.dispatchEvent(fakeKeyEvent("End"));
  assert.equal(doc.activeElement, items[2]);
  panel.dispatchEvent(fakeKeyEvent("ArrowDown"));
  assert.equal(doc.activeElement, items[0], "wraps");
  panel.dispatchEvent(fakeKeyEvent("ArrowUp"));
  assert.equal(doc.activeElement, items[2], "wraps backwards");
  panel.dispatchEvent(fakeKeyEvent("Home"));
  assert.equal(doc.activeElement, items[0]);
  panel.dispatchEvent(fakeKeyEvent("Tab"));
  assert.equal(d.isOpen(), false);
});

test("menu: activating an item closes the menu and refocuses the trigger", () => {
  const { doc, trigger, panel, items, d } = setup({ menu: true });
  d.open({ focus: "first" });
  panel.dispatchEvent({ type: "click", target: items[1] });
  assert.equal(d.isOpen(), false);
  assert.equal(doc.activeElement, trigger);
});

test("menu: aria-disabled items are skipped by arrow navigation", () => {
  const { doc, items, panel, d } = setup({ menu: true });
  items[1].setAttribute("aria-disabled", "true");
  d.open({ focus: "first" });
  panel.dispatchEvent(fakeKeyEvent("ArrowDown"));
  assert.equal(doc.activeElement, items[2]);
});
