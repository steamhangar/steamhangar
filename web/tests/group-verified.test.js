/**
 * WP GROUP-VERIFIED-1 — a D9 group whose game is 100 % and whose only
 * unmeasurable part is a content-package depot (an unowned package) reads
 * "Current", and the detail sheet's group line says what the figure does
 * not cover: `group_cache_unmeasured_depots` / `_bytes` from the API, never
 * counted as complete, bytes visible.
 *
 * Guarantees pinned by name (MUTATION TARGET):
 *  V1 the group line appends "not measured: N package depot(s) (X GB)";
 *  V2 nothing is appended for 0, absent or malformed counts;
 *  V3 the twin-pinned template prefix stays byte-identical (Android pin);
 *  V4 the sheet's group line renders the note (DOM, fake-dom);
 *  V5 the demo rows carry both new fields, and the server models declare
 *     them (twin pin against api/vault_api/routers/games.py).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { groupCompletenessLine, unmeasuredNote, cardDisplay } from "../js/lib/cache-completeness.js";
import { groupGames } from "../js/lib/content-groups.js";
import { STATUS_LABEL } from "../js/components/status-icon.js";
import { KIND } from "../js/lib/game-status.js";
import { createFakeDom } from "./fake-dom.js";

const GB = 1_073_741_824;
const row = (over = {}) => ({
  appid: 553850,
  name: "HELLDIVERS 2",
  status: "done",
  last_prefill_at: "2026-10-18T14:30:00Z",
  size_bytes: 21.3 * GB,
  needs_force: false,
  update_available: false,
  cache_complete_pct: 100,
  cache_expected_bytes: 21.3 * GB,
  cache_present_bytes: 21.3 * GB,
  cache_complete_checked_at: "2026-10-18T14:30:58Z",
  cache_missing_chunks: 0,
  group_cache_complete_pct: 100,
  group_cache_unmeasured_depots: 6,
  group_cache_unmeasured_bytes: 1.5 * GB,
  ...over,
});
const pkg = (appid) => ({
  ...row({ appid, name: `Package ${appid}`, parent_appid: 553850, size_bytes: 0.25 * GB }),
  cache_complete_pct: null,
  group_cache_complete_pct: null,
  group_cache_unmeasured_depots: 0,
  group_cache_unmeasured_bytes: 0,
});

test("MUTATION TARGET V1 -- the group line names the unmeasured package depots and their bytes", () => {
  const [group] = groupGames([row(), pkg(3506210)]).rows;
  assert.equal(groupCompletenessLine(group), "With content packages: 100% complete · not measured: 6 package depots (1.5 GB)");
  const [one] = groupGames([row({ group_cache_unmeasured_depots: 1, group_cache_unmeasured_bytes: 0.2 * GB }), pkg(3506210)]).rows;
  assert.equal(groupCompletenessLine(one), "With content packages: 100% complete · not measured: 1 package depot (0.2 GB)");
  const [noBytes] = groupGames([row({ group_cache_unmeasured_bytes: 0 }), pkg(3506210)]).rows;
  assert.equal(groupCompletenessLine(noBytes), "With content packages: 100% complete · not measured: 6 package depots");
});

test("MUTATION TARGET V2 -- no note for 0, absent or malformed counts", () => {
  for (const n of [0, undefined, null, -1, 1.5, "6"]) {
    assert.equal(unmeasuredNote(row({ group_cache_unmeasured_depots: n })), "", String(n));
  }
  const [group] = groupGames([row({ group_cache_unmeasured_depots: 0 }), pkg(3506210)]).rows;
  assert.equal(groupCompletenessLine(group), "With content packages: 100% complete");
});

test("V2b -- the group card reads Current at 100 (the unmeasured part is shown in the sheet, not hidden)", () => {
  const [group] = groupGames([row(), pkg(3506210)]).rows;
  const shown = cardDisplay(group, KIND.CACHED, STATUS_LABEL);
  assert.notEqual(shown.icon, "unverified");
  assert.notEqual(shown.icon, "partial");
});

test("MUTATION TARGET V3 -- the twin-pinned template literal is unchanged", () => {
  const src = readFileSync(new URL("../js/lib/cache-completeness.js", import.meta.url), "utf8");
  assert.ok(src.includes("`With content packages: ${pct}% complete`"), "Android CacheCompletenessWebTwinTest pins this fragment");
});

test("MUTATION TARGET V4 -- the sheet's group line renders the note", async () => {
  const dom = createFakeDom();
  dom.document.hidden = true;
  globalThis.document = dom.document;
  globalThis.window = dom.window;
  const { buildCompletenessSection } = await import("../js/components/game-detail-sheet.js");
  const parent = row();
  const [group] = groupGames([parent, pkg(3506210)]).rows;
  const el = buildCompletenessSection(parent, group, [553850], async () => {});
  assert.equal(
    el.querySelector('[data-role="complete-group"]').textContent,
    "With content packages: 100% complete · not measured: 6 package depots (1.5 GB)",
  );
});

test("MUTATION TARGET V5 -- server models and demo rows carry the two fields", async () => {
  const src = readFileSync(new URL("../../api/vault_api/routers/games.py", import.meta.url), "utf8");
  for (const model of ["GameSummary", "GameDetail"]) {
    const m = new RegExp(`class ${model}\\(BaseModel\\):([\\s\\S]*?)(?=\\n\\S)`).exec(src);
    assert.ok(m, `class ${model} not found`);
    for (const f of ["group_cache_unmeasured_depots", "group_cache_unmeasured_bytes"]) {
      assert.match(m[1], new RegExp(`^ {4}${f}: int = 0$`, "m"), `${model}.${f}`);
    }
  }
  const { demoRequest, resetDemoData } = await import("../js/demo-data.js");
  resetDemoData();
  for (const g of await demoRequest("GET", "/v1/games")) {
    assert.ok(Number.isInteger(g.group_cache_unmeasured_depots), `${g.appid}`);
    assert.ok(Number.isInteger(g.group_cache_unmeasured_bytes), `${g.appid}`);
  }
});
