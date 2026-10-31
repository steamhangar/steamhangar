/**
 * WP CACHE-REPAIR-UI-1 — "Repair" in the game detail sheet
 * (`components/game-detail-sheet.js`). Same split as
 * game-detail-sheet-completeness.test.js: the exported builder
 * (`buildCompletenessSection`) is driven against fake-dom.js; the wiring
 * (who gets the button, the header twin, the structural key, "Show job"
 * going to Downloads, closing the dialog with the sheet) is pinned by
 * bounded source-text checks.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createFakeDom } from "./fake-dom.js";

const src = readFileSync(new URL("../js/components/game-detail-sheet.js", import.meta.url), "utf8");

function body(signature) {
  const idx = src.indexOf(signature);
  assert.notEqual(idx, -1, `${signature} not found — moved or renamed?`);
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

async function withFakeDom(fn) {
  const dom = createFakeDom();
  dom.document.hidden = true; // parks the store's poll loops
  globalThis.document = dom.document;
  globalThis.window = dom.window;
  const mod = await import("../js/components/game-detail-sheet.js");
  return fn(mod, dom);
}

const GB = 1_073_741_824;
const damaged = (over = {}) => ({
  appid: 2010080,
  name: "Starfall Legion",
  status: "done",
  last_prefill_at: "2026-10-01T00:00:00Z",
  size_bytes: 6 * GB,
  needs_force: false,
  update_available: false,
  cache_complete_pct: 99,
  cache_expected_bytes: 6 * GB,
  cache_present_bytes: 5.9 * GB,
  cache_complete_checked_at: "2026-10-30T08:00:00Z",
  cache_missing_chunks: 0,
  ...over,
});
const role = (el, r) => el.querySelector(`[data-role="${r}"]`);

test("MUTATION TARGET -- block: with a repair handler, 'Repair' + its hint, wired to the handler", () =>
  withFakeDom(async ({ buildCompletenessSection }) => {
    let calls = 0;
    const el = buildCompletenessSection(damaged(), null, [], async () => {}, "Starfall Legion", async () => {
      calls += 1;
    });
    const btn = role(el, "repair");
    assert.ok(btn, "Repair button");
    assert.equal(btn.textContent, "Repair");
    assert.equal(btn.getAttribute("aria-label"), "Repair — Starfall Legion");
    assert.equal(btn.getAttribute("aria-describedby"), "detail-repair-hint");
    assert.equal(role(el, "repair-hint").id, "detail-repair-hint");
    assert.equal(
      role(el, "repair-hint").textContent,
      "Deletes the chunk files that have the wrong size, then downloads fresh copies. You confirm before anything is deleted.",
    );
    assert.equal(role(el, "damaged-note").textContent, "Damaged chunks — Verify can't fix these, Repair can.");
    assert.equal(role(el, "verify"), null, "no Verify for a damaged-only game");
    btn.dispatchEvent({ type: "click" });
    await new Promise((r) => setTimeout(r, 0));
    assert.equal(calls, 1);
  }));

test("block: no handler -> no Repair; a not-owned game says why", () =>
  withFakeDom(({ buildCompletenessSection }) => {
    const plain = buildCompletenessSection(damaged(), null, [], async () => {}, "Starfall Legion");
    assert.equal(role(plain, "repair"), null);
    const notOwned = buildCompletenessSection(damaged({ status: "not_owned" }), null, [], async () => {}, "Starfall Legion", null);
    assert.equal(role(notOwned, "repair"), null);
    assert.equal(
      role(notOwned, "damaged-note").textContent,
      "Damaged chunks — can't be repaired: this Steam account doesn't own the game.",
    );
  }));

test("MUTATION TARGET -- wiring: the sheet offers Repair exactly where showsRepair holds, in the box and the header", () => {
  const show = body("function currentShowsRepair(gameLike) {");
  assert.match(show, /showsRepair\(gameLike, state\.jobs\)/, "the shared visibility rule, with the jobs");
  assert.match(show, /showsCompletenessBlock\(gameLike, currentGroup\(\)\)/);
  assert.match(body("function buildMaintenancePanel(gameLike, group) {"), /currentShowsRepair\(gameLike\) \? \(\) => startSheetRepair\(gameLike\.appid, title\) : null/);
  const head = body("function buildHeadRepairButton(gameLike) {");
  assert.match(head, /if \(!currentShowsRepair\(gameLike\)\) return null;/);
  assert.match(head, /startSheetRepair\(gameLike\.appid, title\)/);
  assert.match(head, /btn\.setAttribute\("aria-label", repairAriaLabel\(title\)\)/);
  assert.match(body("function render() {"), /const headRepair = buildHeadRepairButton\(gameLike\);\s*if \(headRepair\) acts\.append\(headRepair\);/);
});

test("MUTATION TARGET -- wiring: 'Show job' goes to Downloads and highlights the job; closing the sheet closes the dialog", () => {
  const start = body("function startSheetRepair(appid, title) {");
  assert.match(start, /startRepair\(\{/);
  assert.match(start, /navigateTo\("downloads"\);\s*import\("\.\.\/views\/downloads\.js"\)\.then\(\(m\) => m\.highlightJob\(jobId\)\)/);
  assert.match(body("function closeDetail() {"), /closeRepair\(\);/);
  assert.match(body("function completenessStructuralPart(kind) {"), /showsRepair\(gameLike, state\.jobs\) \? 1 : 0\}:\$\{repairNoteFor\(gameLike\)\}/);
});

test("MUTATION TARGET -- the Overview's damaged note follows repairNoteFor", () => {
  assert.match(body("function buildFactLines(gameLike) {"), /const damagedText = repairNoteFor\(gameLike\);/);
  assert.doesNotMatch(src, /DAMAGED_NOTE/, "the old 'can't repair these yet' note is gone from the web sheet");
});
