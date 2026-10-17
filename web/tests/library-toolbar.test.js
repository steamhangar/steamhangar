/**
 * WP LIBRARY-2: components/library-toolbar.js (the Library's page head,
 * toolbar row and status line on the UI kit, docs/design/rc20/library.html)
 * and the detail sheet's new anatomy (hero band, X close, header actions,
 * two-column Overview with a 2x2 facts grid).
 *
 * The toolbar is a DOM-only component, built here against the fake DOM; the
 * detail sheet is an import-time singleton, so its wiring is pinned with
 * function-bounded source scans (suite convention, see
 * suggestions-moved-wiring.test.js).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createFakeDom } from "./fake-dom.js";

const dom = createFakeDom();
globalThis.document = dom.document;
globalThis.window = dom.window;

const { buildLibraryToolbar, paintChips, paintFiltersButton } = await import("../js/components/library-toolbar.js");

const LABELS = { grid2: "Comfortable", grid3: "Compact", list: "List" };

function build(over = {}) {
  const settingsBtn = dom.document.createElement("button");
  settingsBtn.className = "linkbtn";
  const reloadBtn = dom.document.createElement("button");
  reloadBtn.className = "linkbtn";
  return buildLibraryToolbar({
    query: "",
    layout: "grid2",
    layoutLabels: LABELS,
    checkUpdatesLabel: "Check updates",
    checkAndUpdateLabel: "Check & update all cached games",
    settingsBtn,
    reloadBtn,
    ...over,
  });
}

test("head: a kit page head with the title and the split button; the primary half is always rendered", () => {
  const t = build();
  assert.equal(t.head.className, "page-head lib-head");
  assert.equal(t.head.querySelector("h1").textContent, "Library");
  assert.equal(t.split.children.length, 3);
  assert.equal(t.checkUpdatesBtn.hidden === true, false, "MUTATION PIN: never caret-only from the start");
  assert.equal(t.checkUpdatesBtn.className, "btn pri");
  assert.equal(t.checkAndUpdateBtn.getAttribute("role"), "menuitem");
  assert.equal(t.checkAndUpdateBtn.className, "menuitem", "the downloading entry is never a primary button");
});

test("toolbar: search + chips, then sort group, layout switch, Filters, Select — in that order", () => {
  const t = build();
  assert.deepEqual(t.bar.children.map((c) => c.className), ["lib-filters", "lib-tools"]);
  assert.deepEqual(
    t.bar.children[1].children.map((c) => c.className),
    ["sortg", "segi", "lib-filtersbtn", "btn lib-select"],
  );
});

test("MUTATION TARGET -- sort is ONE fused group: the select and an icon-only direction button", () => {
  const t = build();
  const g = t.bar.querySelector(".sortg");
  assert.equal(g.getAttribute("role"), "group");
  assert.equal(g.children.length, 2);
  assert.equal(g.children[0].tagName, "SELECT");
  assert.equal(g.children[1].className, "lib-sort-dir");
  assert.equal(g.children[1].textContent, "", "no text, the view paints an svg plus aria-label and title");
});

test("layout switch: three icon buttons with the density labels, the stored one pressed", () => {
  const t = build({ layout: "list" });
  const btns = t.layoutSegs.children;
  assert.deepEqual(btns.map((b) => b.dataset.layout), ["grid2", "grid3", "list"]);
  assert.deepEqual(btns.map((b) => b.getAttribute("aria-label")), ["Comfortable", "Compact", "List"]);
  assert.deepEqual(btns.map((b) => b.getAttribute("aria-pressed")), ["false", "false", "true"]);
});

test("MUTATION TARGET -- Filters is always visible and shows its count, 0 included", () => {
  const t = build();
  assert.equal(t.filtersWrap.hidden === true, false);
  assert.equal(t.filtersCount.textContent, "0");
  assert.equal(t.filtersCount.hidden === true, false);
  paintFiltersButton(t, 2, (n) => `Filters, ${n} active`);
  assert.equal(t.filtersCount.textContent, "2");
  assert.equal(t.filtersBtn.classList.contains("on"), true);
  assert.equal(t.filtersBtn.getAttribute("aria-label"), "Filters, 2 active");
  paintFiltersButton(t, 0, () => "Filters");
  assert.equal(t.filtersCount.textContent, "0", "back to 0, still shown");
  assert.equal(t.filtersBtn.classList.contains("on"), false);
});

test("MUTATION TARGET -- chips are kit .fchip in sentence case with a mono count, one pressed", () => {
  const t = build();
  paintChips(t.chips, [
    { key: "all", label: "All", count: 10 },
    { key: "cached", label: "Cached", count: 4 },
  ], "cached");
  const chips = t.chips.children;
  assert.deepEqual(chips.map((c) => c.className), ["fchip", "fchip"]);
  assert.deepEqual(chips.map((c) => c.textContent), ["All 10", "Cached 4"], "label, a space, the count");
  assert.equal(chips[1].querySelector("b.n").textContent, "4");
  assert.deepEqual(chips.map((c) => c.getAttribute("aria-pressed")), ["false", "true"]);
});

test("status line: ONE line holding the update text, the owned notice (the only live region) and its buttons", () => {
  const t = build();
  assert.equal(t.stat.className, "lib-stat");
  assert.deepEqual(t.stat.children.map((c) => c.className), ["lib-upd", "lib-owned-text", "linkbtn", "linkbtn"]);
  assert.equal(t.ownedText.getAttribute("role"), "status");
  assert.equal(t.ownedText.hidden === true, false, "the live region itself is never hidden");
});

test("MUTATION TARGET -- css: one toolbar row at full width (zero-basis filters), a stacked status line in a narrow container", () => {
  const kit = readFileSync(new URL("../css/kit.css", import.meta.url), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
  assert.match(kit, /\.kit \.lib-filters\{ flex:1 1 0; \}/, "the chips wrap inside the left part, the tools stay on row 1");
  const narrow = kit.slice(kit.indexOf("@container lib (max-width:560px)"));
  assert.match(narrow, /\.kit \.lib-filters\{ flex-basis:100%; \}/);
  assert.match(narrow, /\.kit \.lib-stat\{ flex-direction:column;/, "no wrapped line starts with a separator");
  assert.match(narrow, /\.kit \.lib-stat > \*::before,\s*\.kit \.lib-stat > \*:not\(\[hidden\]\):not\(:empty\) ~ \*:not\(\[hidden\]\):not\(:empty\)::before\{ display:none; \}/, "matches the separator rule's specificity");
});

// ---------------------------------------------------------------------
// Detail sheet anatomy (source pins)
// ---------------------------------------------------------------------

const strip = (src) => src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/[^\n]*/g, "$1");
const sheetJs = strip(readFileSync(new URL("../js/components/game-detail-sheet.js", import.meta.url), "utf8"));
function functionBody(src, signature) {
  const idx = src.indexOf(signature);
  if (idx === -1) return null;
  const start = src.indexOf("{", idx + signature.length - 1);
  let depth = 1;
  let j = start + 1;
  while (depth > 0 && j < src.length) {
    if (src[j] === "{") depth++;
    else if (src[j] === "}") depth--;
    j++;
  }
  return src.slice(start + 1, j - 1);
}

test("MUTATION TARGET -- sheet: kit-scoped, an X close button on the sheet, no bottom Close button", () => {
  assert.match(sheetJs, /dialog\.sheet\.classList\.add\("kit", "game-detail"\);/);
  assert.match(sheetJs, /closeBtn\.className = "gd-x";\s*closeBtn\.setAttribute\("aria-label", "Close"\);/);
  assert.match(sheetJs, /closeBtn\.addEventListener\("click", \(\) => closeDetail\(\)\);/);
  assert.match(sheetJs, /dialog\.sheet\.appendChild\(closeBtn\);/);
  assert.match(sheetJs, /dialog\.body\.append\(contentEl\);/, "the body holds the content only");
  assert.doesNotMatch(sheetJs, /btn wide ghost/);
});

test("MUTATION TARGET -- sheet: the hero band is permanent (every state), the art goes inside it once loaded", () => {
  const r = functionBody(sheetJs, "function render() {");
  assert.equal((r.match(/contentEl\.append\(buildHero\(\), bodyWrap\(/g) || []).length, 3, "not tracked, error and loading all show the band");
  assert.match(r, /const hero = buildHero\(\);\s*hero\.append\(buildHeaderArt\(state\.appid\)\);\s*contentEl\.append\(hero\);/);
});

test("MUTATION TARGET -- sheet: the header row carries Verify and the primary action", () => {
  const r = functionBody(sheetJs, "function render() {");
  assert.match(r, /const headVerify = buildHeadVerifyButton\(gameLike\);\s*if \(headVerify\) acts\.append\(headVerify\);/);
  const v = functionBody(sheetJs, "function buildHeadVerifyButton(gameLike) {");
  assert.match(v, /const ids = currentVerifyAppids\(\);\s*if \(!ids\.length \|\| !showsCompletenessBlock\(gameLike, currentGroup\(\)\)\) return null;/, "offered exactly when the Maintenance Verify is");
  assert.match(v, /withButtonBusy\(btn, \(\) => startSheetVerify\(ids\)\)/, "the same POST as the Maintenance Verify");
});

test("MUTATION TARGET -- Overview: two columns (facts, installed on, About | content packages), facts as a 2x2 grid", () => {
  const r = functionBody(sheetJs, "function render() {");
  assert.match(r, /left\.append\(buildFactLines\(gameLike\)\);/);
  assert.match(r, /if \(storeBlock\) left\.append\(buildAboutSection\(storeBlock\)\);/);
  assert.match(r, /right\.append\(\s*buildPackagesSection\(/);
  assert.match(r, /if \(right\.childNodes\.length\) overview\.append\(right\);\s*else overview\.classList\.add\("one"\);/);
  const f = functionBody(sheetJs, "function buildFactLines(gameLike) {");
  for (const label of ["Size", "Last downloaded", "Confirmed current", "Cache"]) {
    assert.ok(f.includes(`"${label}"`), `fact ${label}`);
  }
  const kit = readFileSync(new URL("../css/kit.css", import.meta.url), "utf8");
  assert.match(kit, /\.kit \.gd-facts\{ display:grid; grid-template-columns:repeat\(2, minmax\(0, 1fr\)\);/);
  assert.match(kit, /@container gd \(min-width:720px\)\{\s*\.kit \.gd-ov:not\(\.one\)\{ grid-template-columns:minmax\(0, 1\.1fr\) minmax\(0, 1fr\);/);
  assert.match(kit, /\.kit\.game-detail\{ position:relative; max-width:var\(--w-sheet-l\); \}/);
  assert.match(kit, /\.kit \.gd-head\{[^}]*margin:-52px 0 0;/, "the mini cover overlaps the hero by 52px");
});
