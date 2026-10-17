/**
 * The detail sheet's "Update available" state and "Update now" action (WP
 * UPD-6 review fix, `components/game-detail-sheet.js`). Same split as
 * game-detail-sheet-installed.test.js: the sheet is a store-wired
 * singleton, so the exported builders (`buildDetailStatusRow`,
 * `buildUpdateNowButton`) are driven against fake-dom.js, and the wiring
 * (header, render branch, structural key, the request itself) is pinned by
 * bounded source-text checks. The key's `updates` part is pinned in
 * detail-render-plan.test.js, `sheetUpdateAppids` in
 * updates-available.test.js.
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
  dom.document.hidden = true; // parks the store's poll loops (see the installed test)
  globalThis.document = dom.document;
  globalThis.window = dom.window;
  const mod = await import("../js/components/game-detail-sheet.js");
  return fn(mod, dom);
}

const row = (over = {}) => ({
  appid: 2010040,
  name: "Emberreach",
  status: "done",
  last_prefill_at: "2026-10-01T00:00:00Z",
  size_bytes: 1e9,
  needs_force: false,
  update_available: false,
  cache_complete_pct: 100, // WP CACHE-COMPLETE-1c: measured complete -> "Current"
  ...over,
});

test("MUTATION TARGET -- header: a flagged cached game reads 'Update available' with the orange refresh glyph", () =>
  withFakeDom(({ buildDetailStatusRow }) => {
    const r = buildDetailStatusRow(row({ update_available: true }), "cached");
    assert.match(r.querySelector(".sic").className, /\bk-stale\b/);
    const word = r.children[1];
    assert.equal(word.textContent, "Update available");
    assert.equal(word.className, "tx-stale");
  }));

test("header: no flag (or not cached) keeps the plain status", () =>
  withFakeDom(({ buildDetailStatusRow }) => {
    const plain = buildDetailStatusRow(row(), "cached");
    assert.equal(plain.children[1].textContent, "Current");
    assert.match(plain.querySelector(".sic").className, /\bk-cached\b/);
    const failed = buildDetailStatusRow(row({ update_available: true, status: "error" }), "error");
    assert.equal(failed.children[1].textContent, "Failed");
  }));

test("header: a tool app keeps the dash glyph and the tool word (even if flagged)", () =>
  withFakeDom(({ buildDetailStatusRow }) => {
    const r = buildDetailStatusRow(row({ appid: 228980, tool_app: true, update_available: true }), "tool");
    assert.match(r.querySelector(".sic").className, /\bk-notinuse\b/);
    assert.equal(r.children[1].textContent, "Steam tool package");
  }));

test("header: a group row (flag on a package only) shows the update state, like the Library card", () =>
  withFakeDom(async ({ buildDetailStatusRow }) => {
    const { groupGames } = await import("../js/lib/content-groups.js");
    const parent = row({ appid: 500, name: "Parent" });
    const pkg = row({ appid: 501, name: "Parent - Pack", parent_appid: 500, app_type: "dlc", update_available: true });
    const [group] = groupGames([parent, pkg]).rows;
    assert.equal(buildDetailStatusRow(group, "cached").children[1].textContent, "Update available");
  }));

test("MUTATION TARGET -- 'Update now' button: label, primary, carries its app ids, runs onStart once per click", () =>
  withFakeDom(async ({ buildUpdateNowButton }) => {
    let calls = 0;
    let release;
    const btn = buildUpdateNowButton([500, 501], () => {
      calls += 1;
      return new Promise((r) => (release = r));
    });
    assert.equal(btn.textContent, "Update now");
    assert.match(btn.className, /\bprimary\b/);
    assert.equal(btn.dataset.appids, "500,501");
    btn.dispatchEvent({ type: "click" });
    assert.equal(calls, 1);
    assert.equal(btn.disabled, true, "busy while the request is out");
    release();
    await new Promise((r) => setTimeout(r, 0));
    assert.equal(btn.disabled, false);
  }));

test("MUTATION TARGET -- wiring: the header uses buildDetailStatusRow with the group-aware row", () => {
  assert.match(body("function buildHeader(gameLike, liveJob) {"), /buildDetailStatusRow\(currentUpdateRow\(\) \|\| gameLike, dispKind\(gameLike, liveJob\)\)/);
});

test("MUTATION TARGET -- wiring: render() offers 'Update now' when there is no job and no download action", () => {
  // LIBRARY-DETAIL-1: the primary action row sits above the tabs and is
  // built by buildPrimaryAction (job controls, download, then update).
  assert.match(body("function render() {"), /const actions = buildPrimaryAction\(gameLike, trackedJob\);\s*if \(actions\) contentEl\.append\(actions\);/);
  const r = body("function buildPrimaryAction(gameLike, trackedJob) {");
  assert.match(
    r,
    /if \(action\) return buildDownloadButton\(action\);[\s\S]*?const updateAppids = currentUpdateAppids\(\);\s*if \(updateAppids\.length\) return buildUpdateNowButton\(updateAppids, \(\) => startSheetUpdate\(updateAppids\)\);/,
  );
});

test("MUTATION TARGET -- wiring: the request is POST /v1/prefill with exactly those ids (never /cached, no force)", () => {
  const b = body("async function startSheetUpdate(appids) {");
  assert.match(b, /await api\.prefill\(appids\)/);
  assert.doesNotMatch(b, /prefillCached|force/);
});

test("MUTATION TARGET -- wiring: the structural key carries the update part, so a flag flip re-renders an open sheet", () => {
  assert.match(body("function computeStructuralKey() {"), /updates: updatesStructuralPart\(dispKind\(gameLike, liveJob\)\)/);
  const part = body("function updatesStructuralPart(kind) {");
  assert.match(part, /showsUpdateState\(currentUpdateRow\(\), kind\)/);
  assert.match(part, /currentUpdateAppids\(\)/);
});
