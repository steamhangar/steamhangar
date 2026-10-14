/**
 * WP CACHE-COMPLETE-1c — the detail sheet's completeness block and "Verify"
 * (`components/game-detail-sheet.js`). Same split as
 * game-detail-sheet-update.test.js: the sheet is a store-wired singleton, so
 * the exported builders (`buildCompletenessSection`, `buildDetailStatusRow`)
 * are driven against fake-dom.js, and the wiring (render branch, structural
 * key, patch path, the request) is pinned by bounded source-text checks.
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

const GB = 1_073_741_824;
const row = (over = {}) => ({
  appid: 2010010,
  name: "Aurora Cascade",
  status: "done",
  last_prefill_at: "2026-10-01T00:00:00Z",
  size_bytes: 4 * GB,
  needs_force: false,
  update_available: false,
  cache_complete_pct: 80,
  cache_expected_bytes: 4 * GB,
  cache_present_bytes: 3.2 * GB,
  cache_complete_checked_at: "2026-10-14T08:00:00Z",
  cache_missing_chunks: 12,
  group_cache_complete_pct: 80,
  ...over,
});
const role = (el, r) => el.querySelector(`[data-role="${r}"]`);

test("MUTATION TARGET -- block: the figure line, and Verify carrying its app ids", () =>
  withFakeDom(({ buildCompletenessSection }) => {
    const el = buildCompletenessSection(row(), null, [2010010], async () => {}, "Aurora Cascade");
    assert.equal(el.dataset.role, "completeness");
    assert.match(role(el, "complete-line").textContent, /^80% complete — 3\.2 GB of 4\.0 GB on disk · checked /);
    const btn = role(el, "verify");
    assert.equal(btn.textContent, "Verify");
    assert.equal(btn.getAttribute("aria-label"), "Verify — Aurora Cascade");
    assert.equal(btn.dataset.appids, "2010010");
    const hint = role(el, "verify-hint");
    assert.match(hint.textContent, /downloads what is missing/, "visible explanation");
    assert.equal(btn.getAttribute("aria-describedby"), hint.id, "and the button's description");
    assert.equal(role(el, "damaged-note"), null);
    assert.equal(role(el, "complete-group"), null, "no group line for a plain game");
  }));

test("block: unknown figure says so (never a percentage)", () =>
  withFakeDom(({ buildCompletenessSection }) => {
    const el = buildCompletenessSection(
      row({ cache_complete_pct: null, cache_expected_bytes: null, cache_present_bytes: null, cache_complete_checked_at: null, cache_missing_chunks: null }),
      null,
      [2010010],
      async () => {},
    );
    assert.equal(role(el, "complete-line").textContent, "Completeness unknown — not measured for the current build.");
    assert.ok(role(el, "verify"), "Verify is still offered");
  }));

test("MUTATION TARGET -- block: damaged-only shows the note and no Verify (empty ids)", () =>
  withFakeDom(({ buildCompletenessSection }) => {
    const damaged = row({ cache_complete_pct: 99, cache_missing_chunks: 0 });
    const el = buildCompletenessSection(damaged, null, [], async () => {});
    assert.equal(role(el, "damaged-note").textContent, "Damaged chunks — SteamHangar can't repair these yet.");
    assert.equal(role(el, "verify"), null);
  }));

test("block: a group shows the group line; none for a tool app or a game with nothing on disk", () =>
  withFakeDom(async ({ buildCompletenessSection }) => {
    const { groupGames } = await import("../js/lib/content-groups.js");
    const parent = row({ appid: 500, group_cache_complete_pct: 70 });
    const pkg = row({ appid: 501, parent_appid: 500, group_cache_complete_pct: null });
    const [group] = groupGames([parent, pkg]).rows;
    const el = buildCompletenessSection(parent, group, [500, 501], async () => {});
    assert.equal(role(el, "complete-group").textContent, "With content packages: 70% complete");
    assert.equal(role(el, "verify").dataset.appids, "500,501");
    assert.equal(buildCompletenessSection(row({ tool_app: true }), null, [], async () => {}), null);
    assert.equal(buildCompletenessSection(row({ size_bytes: null }), null, [], async () => {}), null);
  }));

test("MUTATION TARGET -- Verify runs onVerify once per click, busy while it runs", () =>
  withFakeDom(async ({ buildCompletenessSection }) => {
    let calls = 0;
    let release;
    const el = buildCompletenessSection(row(), null, [2010010], () => {
      calls += 1;
      return new Promise((r) => (release = r));
    });
    const btn = role(el, "verify");
    btn.dispatchEvent({ type: "click" });
    assert.equal(calls, 1);
    assert.equal(btn.disabled, true);
    release();
    await new Promise((r) => setTimeout(r, 0));
    assert.equal(btn.disabled, false);
  }));

test("MUTATION TARGET -- header: a partial game shows 'Partially cached N%' with the half-disc, unknown never the check", () =>
  withFakeDom(({ buildDetailStatusRow }) => {
    const p = buildDetailStatusRow(row(), "cached");
    assert.match(p.querySelector(".sic").className, /\bk-partial\b/);
    assert.equal(p.children[1].textContent, "Partially cached 80%");
    assert.equal(p.children[1].className, "tx-partial");
    const u = buildDetailStatusRow(row({ cache_complete_pct: null }), "cached");
    assert.match(u.querySelector(".sic").className, /\bk-unverified\b/);
    assert.equal(u.children[1].textContent, "Cached · not verified");
    const c = buildDetailStatusRow(row({ cache_complete_pct: 100 }), "cached");
    assert.equal(c.children[1].textContent, "Current");
  }));

test("MUTATION TARGET -- wiring: render() builds the block after the facts with the current Verify ids", () => {
  const r = body("function render() {");
  assert.match(
    r,
    /contentEl\.append\(buildFactLines\(gameLike\)\);\s*\/\/[^\n]*\n\s*const verifyIds = currentVerifyAppids\(\);\s*const completeness = buildCompletenessSection\(\s*gameLike,\s*currentGroup\(\),\s*verifyIds,\s*\(\) => startSheetVerify\(verifyIds\),\s*sheetTitle\(gameLike\),\s*\);\s*if \(completeness\) contentEl\.append\(completeness\);/,
  );
});

test("MUTATION TARGET -- wiring: Verify ids are verifyAppids over the game (and its packages)", () => {
  const v = body("function currentVerifyAppids() {");
  assert.match(v, /return verifyAppids\(group \? \[gameLike, \.\.\.group\.packages\] : \[gameLike\]\);/);
});

test("MUTATION TARGET -- wiring: the request is POST /v1/prefill/verify, worded by summarizeVerifyStart", () => {
  const s = body("async function startSheetVerify(appids) {");
  assert.match(s, /await api\.prefillVerify\(appids\)/);
  assert.match(s, /summarizeVerifyStart\(refs, "verify"\)/);
  assert.match(s, /store\.refreshNow\(\)/);
  const apiSrc = readFileSync(new URL("../js/api.js", import.meta.url), "utf8");
  assert.match(apiSrc, /prefillVerify: \(appids\) => request\("POST", "\/v1\/prefill\/verify", \{ body: \{ appids \} \}\)/);
  assert.doesNotMatch(apiSrc, /prefill\/incomplete/, "Complete all sends the listed ids, never the server's selection");
});

test("MUTATION TARGET -- wiring: the structural key carries the completeness part; the patch path refreshes the lines", () => {
  assert.match(body("function computeStructuralKey() {"), /completeness: completenessStructuralPart\(dispKind\(gameLike, liveJob\)\)/);
  const part = body("function completenessStructuralPart(kind) {");
  assert.match(part, /cardDisplay\(currentUpdateRow\(\) \|\| gameLike, kind, STATUS_LABEL\)\.key/);
  assert.match(part, /currentVerifyAppids\(\)\.join\(","\)/);
  assert.match(part, /isDamagedOnly\(gameLike\)/);
  const patch = body("function patchVolatile() {");
  assert.match(patch, /completeLineEl\.textContent = completenessLine\(gameLike\)/);
});

test("detail-render-plan: the completeness part changes the key only when present", async () => {
  const { buildDetailStructuralKey } = await import("../js/lib/detail-render-plan.js");
  const base = { dispKind: "cached", trackedJobStatus: null, depotTags: [], installedBadge: "none" };
  assert.equal(buildDetailStructuralKey({ ...base, completeness: "" }), buildDetailStructuralKey(base));
  assert.notEqual(buildDetailStructuralKey({ ...base, completeness: "partial:40|1||0|0" }), buildDetailStructuralKey(base));
  assert.notEqual(
    buildDetailStructuralKey({ ...base, completeness: "partial:40|1|0|0" }),
    buildDetailStructuralKey({ ...base, completeness: "partial:41|1|0|0" }),
  );
});
