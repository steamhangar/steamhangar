/**
 * Tests for the grouped delete (WP TOOL-APPS-2c; api/README.md "Grouped
 * delete"): lib/group-delete.js wording and decisions, the plan block of
 * components/group-delete-plan.js (fake DOM), the api.js call shapes, and
 * the demo routes that mirror the server contract.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createFakeDom } from "./fake-dom.js";

const dom = createFakeDom();
globalThis.document = dom.document;
globalThis.window = {
  ...dom.window,
  localStorage: { getItem: () => null, setItem() {} },
  location: { origin: "http://vault.test" },
};

const {
  planMemberIds,
  isMembersMismatch,
  isPlanBlocked,
  describeGroupPlan,
  groupSubject,
  keptChildReason,
  blockingJobText,
} = await import("../js/lib/group-delete.js");
const { buildGroupDeletePlan, groupDeleteIntro } = await import("../js/components/group-delete-plan.js");
const { demoRequest, resetDemoData } = await import("../js/demo-data.js");

const GIB = 1_073_741_824;
const PLAN = {
  appid: 553850,
  with_packages: true,
  members: [
    { appid: 553850, role: "parent", name: "HELLDIVERS 2", app_type: "game", depot_ids: [1, 2] },
    { appid: 2506210, role: "package", name: "HELLDIVERS 2 - Voice", app_type: "dlc", depot_ids: [3] },
    { appid: 2506220, role: "package", name: null, app_type: "dlc", depot_ids: [] },
  ],
  kept_children: [{ appid: 4000001, name: "Side Game", app_type: "game", reason: "not_a_package_type" }],
  delete_depots: [{ depotid: 1, size_bytes: 2 * GIB, owners: [553850], shared_with_uncached: [] }],
  skipped_shared: [{ depotid: 2, shared_with: [4000001] }],
  unusable: [],
  total_bytes: 2 * GIB,
  blocked_by: [],
};
const nameFor = (id, serverName) => serverName || `App ${id}`;

test("planMemberIds: exactly the plan's members, parent included", () => {
  assert.deepEqual(planMemberIds(PLAN), [553850, 2506210, 2506220]);
  assert.deepEqual(planMemberIds(null), []);
});

test("isMembersMismatch: only the 409 carrying server_members", () => {
  assert.equal(isMembersMismatch({ status: 409, detail: { server_members: [1], confirmed_members: [2] } }), true);
  assert.equal(isMembersMismatch({ status: 409, detail: "Prefill job 3 for app 1 is running." }), false);
  assert.equal(isMembersMismatch({ status: 404, detail: { server_members: [] } }), false);
  assert.equal(isMembersMismatch(null), false);
});

test("describeGroupPlan and its wording", () => {
  const d = describeGroupPlan(PLAN, nameFor);
  assert.equal(d.subject, "HELLDIVERS 2 and its 2 content packages");
  assert.deepEqual(d.packages.map((p) => p.name), ["HELLDIVERS 2 - Voice", "App 2506220"]);
  assert.deepEqual(d.keptDepots, [{ depotid: 2, usedBy: ["App 4000001"] }]);
  assert.equal(d.keptChildren[0].reason, "not a content package");
  assert.equal(groupSubject("X", 1), "X and its 1 content package");
  assert.equal(groupSubject("X", 0), "X");
  assert.equal(keptChildReason("type_unknown"), "type unknown");
  assert.equal(keptChildReason("type_only_inferred"), "type not confirmed by Steam");
  assert.equal(blockingJobText({ type: "prefill", status: "running" }), "Download running");
  assert.equal(blockingJobText({ type: "gc", status: "queued" }), "Cleanup queued");
  assert.equal(isPlanBlocked(PLAN), false);
});

test("MUTATION TARGET -- the plan block lists the game and EVERY package, the bytes freed and the kept depot", () => {
  const block = buildGroupDeletePlan(PLAN, nameFor);
  const members = block.node.querySelector('[data-role="members"]').querySelectorAll("li");
  assert.deepEqual(members.map((li) => li.dataset.appid), ["553850", "2506210", "2506220"]);
  assert.deepEqual(members.map((li) => li.querySelector(".mk").textContent), ["game", "package", "package"]);
  assert.equal(members[1].querySelector(".did").textContent, "HELLDIVERS 2 - Voice");
  const kept = block.node.querySelector('[data-role="kept-depots"]').querySelectorAll("li");
  assert.equal(kept.length, 1);
  assert.equal(kept[0].querySelector(".why").textContent, "App 4000001 still uses it");
  assert.equal(block.node.querySelector('[data-role="kept-children"]').textContent, "Not deleted: Side Game (not a content package).");
  assert.equal(block.node.querySelector(".cftotal").textContent, "2.0 GB freed");
  assert.equal(block.blocked, false);
  assert.equal(block.node.querySelector('[data-role="blocked"]'), null);
  assert.equal(groupDeleteIntro(block.subject, block.freedBytes), "Deleting HELLDIVERS 2 and its 2 content packages frees about 2.0 GB.");
});

test("MUTATION TARGET -- a member's job in flight blocks the delete and says why", () => {
  const block = buildGroupDeletePlan(
    { ...PLAN, blocked_by: [{ appid: 2506210, job_id: 7, type: "prefill", status: "running" }] },
    nameFor,
  );
  assert.equal(block.blocked, true);
  assert.equal(
    block.node.querySelector('[data-role="blocked"]').textContent,
    "Not possible right now: App 2506210 — Download running. Wait for it to finish or cancel it in Downloads.",
  );
});

test("api.js: the plan and the grouped delete send exactly the documented query", async () => {
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    calls.push([init.method || "GET", String(url)]);
    return { ok: true, status: 200, text: async () => "{}" };
  };
  const { api } = await import("../js/api.js");
  await api.deletePlan(553850);
  await api.deleteCacheWithPackages(553850, [553850, 2506210]);
  assert.deepEqual(calls, [
    ["GET", "http://vault.test/v1/cache/553850/delete-plan?with_packages=true"],
    ["DELETE", "http://vault.test/v1/cache/553850?with_packages=true&members=553850%2C2506210"],
  ]);
});

test("demo: Starfall Legion's plan lists its three packages; a stale member list is a 409 with server_members; the confirmed list deletes", async () => {
  resetDemoData();
  try {
    const plan = await demoRequest("GET", "/v1/cache/2010080/delete-plan", { params: { with_packages: "true" } });
    assert.deepEqual(planMemberIds(plan), [2010080, 2010082, 2010084, 2010086]);
    assert.deepEqual(plan.delete_depots.map((d) => d.depotid), [2010081, 2010083, 2010085, 2010089]);
    assert.equal(plan.total_bytes, 6_100_000_000 + 300_000_000 + 450_000_000 + 150_000_000);
    assert.deepEqual(plan.blocked_by, []);

    await assert.rejects(
      demoRequest("DELETE", "/v1/cache/2010080", { params: { with_packages: "true", members: "2010080,2010082" } }),
      (err) => isMembersMismatch(err) && err.detail.server_members.length === 4,
    );
    const still = await demoRequest("GET", "/v1/games/2010082");
    assert.equal(still.depots.length, 2, "nothing deleted on the 409");

    const out = await demoRequest("DELETE", "/v1/cache/2010080", {
      params: { with_packages: "true", members: planMemberIds(plan).join(",") },
    });
    assert.equal(out.total_bytes_freed, plan.total_bytes);
    assert.equal(out.with_packages, true);
    const games = await demoRequest("GET", "/v1/games");
    for (const id of [2010080, 2010082, 2010084]) {
      assert.equal(games.find((g) => g.appid === id).size_bytes, null, `${id} has nothing left`);
    }
  } finally {
    resetDemoData();
  }
});

test("demo: the plain delete without the parameter is unchanged (skips the depot a cached package still uses)", async () => {
  resetDemoData();
  try {
    const plan = await demoRequest("GET", "/v1/cache/2010080/delete-plan");
    assert.deepEqual(plan.skipped_shared, [
      { depotid: 2010089, shared_with: [2010082], reason: "co_owner_has_content", recorded_only: [] },
    ]);
    const out = await demoRequest("DELETE", "/v1/cache/2010080");
    assert.deepEqual(out.skipped_shared, [
      { depotid: 2010089, shared_with: [2010082], reason: "co_owner_has_content", recorded_only: [] },
    ]);
    assert.equal("with_packages" in out, false);
  } finally {
    resetDemoData();
  }
});

test("wiring: the detail sheet deletes a game with packages through the plan, confirms its members and re-asks on a 409", async () => {
  const { readFileSync } = await import("node:fs");
  const src = readFileSync(new URL("../js/components/game-detail-sheet.js", import.meta.url), "utf8");
  const fn = (head) => {
    const i = src.indexOf(head);
    assert.ok(i >= 0, `${head} not found`);
    return src.slice(i, src.indexOf("\n}\n", i));
  };
  assert.match(fn("function openDeleteConfirm() {"), /if \(currentGroup\(\)\) \{\s*openGroupDeleteConfirm\(\);\s*return;/);
  const open = fn("async function openGroupDeleteConfirm(notice) {");
  assert.match(open, /await api\.deletePlan\(appid\)/);
  assert.match(open, /deleteConfirm\.setConfirmEnabled\(!isPlanBlocked\(plan\)\);/);
  const confirm = fn("async function confirmGroupDelete(appid, plan) {");
  assert.match(confirm, /api\.deleteCacheWithPackages\(appid, planMemberIds\(plan\)\)/);
  assert.match(confirm, /if \(isMembersMismatch\(err\)\) \{[\s\S]*openGroupDeleteConfirm\(MEMBERS_CHANGED_NOTICE\)/);
});

test("review fixes: 'Will be deleted (N)' heading, unusable entries shown, the notice is a focusable status", async () => {
  const { buildNotice } = await import("../js/components/group-delete-plan.js");
  const block = buildGroupDeletePlan({ ...PLAN, unusable: [{ depotid: 0, error: "bad id 'x'" }] }, nameFor);
  assert.equal(block.node.querySelector(".cflabel").textContent, "Will be deleted (3)");
  assert.equal(
    block.node.querySelector('[data-role="unusable"]').textContent,
    "1 unreadable depot entry — the delete will report it as failed.",
  );
  assert.equal(buildGroupDeletePlan(PLAN, nameFor).node.querySelector('[data-role="unusable"]'), null);
  const n = buildNotice("changed");
  assert.equal(n.getAttribute("role"), "status");
  assert.equal(n.tabIndex, -1);
});

test("wiring (a11y): the detail sheet keeps focus in the dialog on a 409 and focuses the notice after the new plan", async () => {
  const { readFileSync } = await import("node:fs");
  const src = readFileSync(new URL("../js/components/game-detail-sheet.js", import.meta.url), "utf8");
  const body = (head) => src.slice(src.indexOf(head), src.indexOf("\n}\n", src.indexOf(head)));
  assert.match(body("async function confirmGroupDelete(appid, plan) {"), /if \(isMembersMismatch\(err\)\) \{\s*\/\/[^\n]*\n\s*deleteConfirm\.focusKeep\(\);/);
  assert.match(body("async function openGroupDeleteConfirm(notice) {"), /const noticeEl = notice \? buildNotice\(notice\) : null;[\s\S]*if \(noticeEl\) noticeEl\.focus\(\);/);
});
