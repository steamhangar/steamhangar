/**
 * WP LIBRARY-DETAIL-1: the tab set (components/tabs.js) and its use in the
 * game detail sheet (Overview / Depots / Maintenance).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createFakeDom, fakeKeyEvent } from "./fake-dom.js";

const dom = createFakeDom();
globalThis.document = dom.document;
globalThis.window = dom.window;

const { createTabs } = await import("../js/components/tabs.js");

function make(selected, onSelect) {
  const panels = ["a", "b", "c"].map(() => dom.document.createElement("div"));
  const tabs = createTabs({
    idPrefix: "t",
    label: "Sections",
    tabs: [
      { id: "a", label: "Alpha", panel: panels[0] },
      { id: "b", label: "Beta", panel: panels[1] },
      { id: "c", label: "Gamma", panel: panels[2] },
    ],
    selected,
    onSelect,
  });
  return { tabs, panels, buttons: tabs.tablist.children };
}

const selectedIds = (buttons) => buttons.filter((b) => b.getAttribute("aria-selected") === "true").map((b) => b.dataset.role);

test("tablist semantics: roles, ids, aria-controls / aria-labelledby, one selected tab", () => {
  const { tabs, panels, buttons } = make("a");
  assert.equal(tabs.tablist.getAttribute("role"), "tablist");
  assert.equal(tabs.tablist.getAttribute("aria-label"), "Sections");
  assert.deepEqual(buttons.map((b) => b.getAttribute("role")), ["tab", "tab", "tab"]);
  assert.equal(buttons[1].getAttribute("aria-controls"), panels[1].id);
  assert.equal(panels[1].getAttribute("role"), "tabpanel");
  assert.equal(panels[1].getAttribute("aria-labelledby"), buttons[1].id);
  assert.deepEqual(selectedIds(buttons), ["tab-a"]);
  assert.deepEqual(panels.map((p) => p.hidden), [false, true, true]);
});

test("roving tabindex: only the selected tab is in the Tab order", () => {
  const { buttons } = make("b");
  assert.deepEqual(buttons.map((b) => b.tabIndex), [-1, 0, -1]);
});

test("an unknown selected id falls back to the first tab", () => {
  const { tabs } = make("nope");
  assert.equal(tabs.selectedId(), "a");
});

test("click selects, shows only that panel and reports it once", () => {
  const seen = [];
  const { buttons, panels } = make("a", (id) => seen.push(id));
  buttons[2].dispatchEvent({ type: "click" });
  assert.deepEqual(panels.map((p) => p.hidden), [true, true, false]);
  assert.deepEqual(buttons.map((b) => b.tabIndex), [-1, -1, 0]);
  buttons[2].dispatchEvent({ type: "click" });
  assert.deepEqual(seen, ["c"], "re-clicking the selected tab is not a change");
});

test("keyboard: Right/Left wrap, Home/End jump, focus follows, default prevented", () => {
  const { buttons, tabs } = make("a");
  const press = (i, key) => {
    const ev = fakeKeyEvent(key);
    buttons[i].dispatchEvent(ev);
    return ev;
  };
  assert.equal(press(0, "ArrowLeft").defaultPrevented, true);
  assert.equal(tabs.selectedId(), "c", "Left from the first wraps to the last");
  assert.equal(dom.document.activeElement, buttons[2]);
  press(2, "ArrowRight");
  assert.equal(tabs.selectedId(), "a", "Right from the last wraps to the first");
  press(0, "End");
  assert.equal(tabs.selectedId(), "c");
  press(2, "Home");
  assert.equal(tabs.selectedId(), "a");
  assert.equal(press(0, "x").defaultPrevented, false, "other keys are left alone");
  assert.equal(tabs.selectedId(), "a");
});

test("wiring: the detail sheet has the three tabs, keeps the selection across re-renders and resets it on open", () => {
  const src = readFileSync(new URL("../js/components/game-detail-sheet.js", import.meta.url), "utf8");
  assert.match(src, /\{ id: "overview", label: "Overview", panel: overview \}/);
  assert.match(src, /\{ id: "depots", label: "Depots", panel: depotsPanel \}/);
  assert.match(src, /\{ id: "maintenance", label: "Maintenance", panel: maintenance \}/);
  assert.match(src, /selected: activeTab,/);
  assert.match(src, /onSelect: \(id\) => \{\s*activeTab = id;/);
  const open = src.slice(src.indexOf("export function openDetail("), src.indexOf("async function loadPreview("));
  assert.match(open, /activeTab = "overview";/);
});

test("wiring: Overview shows the cache status as a fact line with its own data-role, patched in place; GC heading matches the other boxes", () => {
  const src = readFileSync(new URL("../js/components/game-detail-sheet.js", import.meta.url), "utf8");
  assert.match(src, /dataset\.role = "cache-status";\s*cacheLine\.textContent = completenessLine\(gameLike\);/);
  assert.match(src, /dataset\.role = "cache-damaged";/);
  assert.match(src, /querySelector\('\[data-role="cache-status"\]'\);\s*if \(cacheStatusEl\) cacheStatusEl\.textContent = completenessLine\(gameLike\);/);
  assert.match(src, /heading\.className = "detail-box-title";\s*heading\.textContent = "Garbage collection";/);
});

test("ADD-DEVICE-2: select(null) selects no tab and hides every panel; the first tab stays reachable", () => {
  const { tabs, panels, buttons } = make("b");
  tabs.select(null);
  assert.equal(tabs.selectedId(), null);
  assert.deepEqual(selectedIds(buttons), []);
  assert.deepEqual(panels.map((p) => p.hidden), [true, true, true]);
  assert.deepEqual(buttons.map((b) => b.tabIndex), [0, -1, -1]);
  tabs.select("c");
  assert.deepEqual(selectedIds(buttons), ["tab-c"]);
});

test("ADD-DEVICE-2: an optional icon goes in front of the label; the name stays the label", () => {
  const icon = dom.document.createElement("svg");
  icon.setAttribute("aria-hidden", "true");
  const panel = dom.document.createElement("div");
  const tabs = createTabs({ idPrefix: "i", label: "L", tabs: [{ id: "x", label: "Ex", panel, icon }], selected: "x" });
  const btn = tabs.tablist.children[0];
  assert.equal(btn.children[0] === icon, true, "icon first");
  assert.equal(btn.textContent, "Ex");
});

test("wiring: Maintenance holds Verify, the GC box and a red delete zone whose button opens the shared confirm", () => {
  const src = readFileSync(new URL("../js/components/game-detail-sheet.js", import.meta.url), "utf8");
  const m = src.slice(src.indexOf("function buildMaintenancePanel("), src.indexOf("/** Full rebuild of the sheet body"));
  assert.match(m, /buildMaintenanceBox\("Verify", \[completeness\]\)/);
  assert.match(m, /panel\.append\(gcSectionEl\)/);
  assert.match(m, /buildMaintenanceBox\("Delete from cache", \[text, buildDeleteButton\(\)\], \{ danger: true \}\)/);
  assert.match(src, /box\.className = danger \? "detail-box detail-box-danger" : "detail-box";/);
  assert.match(src, /function buildDeleteButton\(\) \{[\s\S]*?addEventListener\("click", \(\) => openDeleteConfirm\(\)\)/);
  const css = readFileSync(new URL("../css/app.css", import.meta.url), "utf8");
  assert.match(css, /\.detail-box-danger\{[^}]*rgba\(255,111,111/);
  assert.match(css, /\.detail-panel\[hidden\]\{ display:none; \}/);
});
