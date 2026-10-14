/**
 * Headless tests for web/js/lib/content-groups.js (WP TOOL-APPS-2c,
 * ADR-0022 §2-§4) and the grouped-row behaviour of lib/library-filters.js.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  groupGames,
  displaySizeBytes,
  failedPackages,
  expandSelection,
  groupCardKey,
  shortPackageName,
  packagesLineText,
  packagesAriaText,
  contentPackageFallbackTitle,
  parentAppidOf,
  groupHasCacheContent,
} from "../js/lib/content-groups.js";
import { classifyBulkSelection, buildBulkDownloadPlan } from "../js/lib/bulk-plan.js";
import { chipCounts, visibleGames } from "../js/lib/library-filters.js";
import { indexLiveJobsByAppid } from "../js/lib/game-status.js";

const row = (appid, over = {}) => ({
  appid,
  name: `Game ${appid}`,
  status: "done",
  last_prefill_at: "2026-10-01T00:00:00Z",
  size_bytes: 1_000,
  depot_count: 1,
  needs_force: false,
  installed_on: [],
  update_available: false,
  parent_appid: null,
  app_type: null,
  ...over,
});

const PARENT = row(100, { name: "Starfall Legion", app_type: "game" });
const PKG_A = row(101, { name: "Starfall Legion - Voice Pack", parent_appid: 100, app_type: "dlc", size_bytes: 300 });
const PKG_B = row(102, { name: "Starfall Legion - Soundtrack", parent_appid: 100, app_type: "music", size_bytes: null, depot_count: 0, status: "idle", last_prefill_at: null });
const PKG_FAILED = row(103, { name: "Starfall Legion - Cosmetics", parent_appid: 100, app_type: "dlc", size_bytes: null, depot_count: 0, status: "error" });
const OTHER = row(200, { name: "Aurora Cascade" });
const LIST = [OTHER, PARENT, PKG_A, PKG_B, PKG_FAILED];

test("groupGames: one row per parent, packages attached and removed from the top level, input order kept", () => {
  const { rows, parentOf } = groupGames(LIST);
  assert.deepEqual(rows.map((r) => r.appid), [200, 100]);
  assert.deepEqual(rows[1].packages.map((p) => p.appid), [101, 102, 103]);
  assert.equal(rows[0].packages, undefined, "a plain row is passed through untouched");
  assert.strictEqual(rows[0], OTHER);
  assert.deepEqual([...parentOf.entries()], [[101, 100], [102, 100], [103, 100]]);
});

test("MUTATION PIN -- an older vault-api response (no parent_appid field at all) renders flat, row for row", () => {
  const old = LIST.map(({ parent_appid, app_type, ...rest }) => rest);
  const { rows, parentOf } = groupGames(old);
  assert.equal(rows.length, old.length);
  rows.forEach((r, i) => assert.strictEqual(r, old[i]));
  assert.equal(parentOf.size, 0);
});

test("a package whose parent is not in the list, a self reference, a nested parent, a tool app or an owned-only row stays an ordinary card", () => {
  const orphan = row(300, { parent_appid: 999 });
  const self = row(301, { parent_appid: 301 });
  const child = row(302, { parent_appid: 101 }); // 101 is itself a package
  const tool = row(303, { parent_appid: 100, tool_app: true });
  const owned = row(304, { parent_appid: 100, owned_only: true });
  const bad = row(305, { parent_appid: "100" });
  const { rows } = groupGames([PARENT, PKG_A, orphan, self, child, tool, owned, bad]);
  assert.deepEqual(rows.map((r) => r.appid), [100, 300, 301, 302, 303, 304, 305]);
  assert.deepEqual(rows[0].packages.map((p) => p.appid), [101]);
  assert.equal(parentAppidOf(self), null);
  assert.equal(parentAppidOf(bad), null);
});

test("a tool app is never a parent", () => {
  const toolParent = row(400, { tool_app: true });
  const { rows } = groupGames([toolParent, row(401, { parent_appid: 400 })]);
  assert.equal(rows.length, 2);
});

test("the group row keeps the parent's own status fields, ORs update_available and unions installed_on", () => {
  const p = row(100, { status: "done", installed_on: [{ client_id: "pc-a", reported_at: "2026-10-01T00:00:00Z" }] });
  const a = row(101, {
    parent_appid: 100,
    status: "error",
    update_available: true,
    installed_on: [
      { client_id: "pc-a", reported_at: "2026-10-02T00:00:00Z" },
      { client_id: "pc-b", reported_at: "2026-10-01T00:00:00Z" },
    ],
  });
  const g = groupGames([p, a]).rows[0];
  assert.equal(g.status, "done");
  assert.equal(g.size_bytes, p.size_bytes, "size_bytes stays the parent's own (status decisions)");
  assert.equal(g.update_available, true, "badge when any member has an update");
  assert.deepEqual(g.installed_on.map((e) => [e.client_id, e.reported_at]), [
    ["pc-a", "2026-10-02T00:00:00Z"],
    ["pc-b", "2026-10-01T00:00:00Z"],
  ]);
  const plain = groupGames([row(100), row(101, { parent_appid: 100 })]).rows[0];
  assert.equal(plain.update_available, false);
});

test("MUTATION PIN -- the group card shows the server's group_size_bytes (depot union), a server without it the parent's own size, never a sum", () => {
  const withField = groupGames([{ ...PARENT, group_size_bytes: 1350 }, PKG_A]).rows[0];
  assert.equal(displaySizeBytes(withField), 1350);
  const oldServer = groupGames([PARENT, PKG_A]).rows[0];
  assert.equal("group_size_bytes" in oldServer, false);
  assert.equal(displaySizeBytes(oldServer), PARENT.size_bytes, "fallback: the parent's size_bytes, not 1000 + 300");
  assert.equal(displaySizeBytes({ ...OTHER, group_size_bytes: 99 }), OTHER.size_bytes, "a plain row shows its own size");
  assert.equal(displaySizeBytes(groupGames([{ ...PARENT, group_size_bytes: null }, PKG_A]).rows[0]), PARENT.size_bytes);
});

test("failed packages: a package showing as Failed on its own; a running retry or a tool app is not failed", () => {
  const g = groupGames(LIST).rows[1];
  assert.deepEqual(failedPackages(g, new Map()).map((p) => p.appid), [103]);
  const live = indexLiveJobsByAppid([{ id: 1, appid: 103, type: "prefill", status: "running" }]);
  assert.deepEqual(failedPackages(g, live), []);
  assert.equal(groupCardKey(g, new Map()), "3:1");
  assert.equal(groupCardKey(g, live), "3:0");
  assert.equal(groupCardKey(OTHER, new Map()), "");
});

test("groupHasCacheContent: any member with bytes", () => {
  const g = groupGames([row(100, { size_bytes: null }), row(101, { parent_appid: 100, size_bytes: 5 })]).rows[0];
  assert.equal(groupHasCacheContent(g), true);
  assert.equal(groupHasCacheContent(row(1, { size_bytes: null })), false);
});

test("MUTATION PIN (ADR-0022 §4) -- bulk targets for a picked group card equal those of the same rows picked ungrouped", () => {
  const jobs = [{ id: 1, appid: 102, type: "prefill", status: "queued" }];
  const { rows } = groupGames(LIST);
  const raw = new Map(LIST.map((g) => [g.appid, g]));
  const picked = expandSelection(rows, raw);
  assert.deepEqual(picked.map((g) => g.appid), [200, 100, 101, 102, 103]);
  const grouped = classifyBulkSelection(picked, jobs);
  const ungrouped = classifyBulkSelection(LIST, jobs);
  for (const key of ["busy", "needsDownload", "current", "notOnVault", "toolApps"]) {
    assert.deepEqual(
      grouped[key].map((g) => g.appid).sort(),
      ungrouped[key].map((g) => g.appid).sort(),
      key,
    );
  }
  const planG = buildBulkDownloadPlan(grouped, picked.length);
  const planU = buildBulkDownloadPlan(ungrouped, LIST.length);
  assert.deepEqual(planG.primaryTargets.sort(), planU.primaryTargets.sort());
  assert.deepEqual(planG.primaryTargets, [103], "the failed package is retried, like its own card would be");
});

test("expandSelection: the raw rows, never the group row with its overridden fields; no duplicates", () => {
  const { rows } = groupGames([PARENT, row(101, { parent_appid: 100, update_available: true })]);
  const raw = new Map([[100, PARENT], [101, row(101, { parent_appid: 100, update_available: true })]]);
  const out = expandSelection([rows[0], rows[0]], raw);
  assert.deepEqual(out.map((g) => g.appid), [100, 101]);
  assert.strictEqual(out[0], PARENT);
});

test("chips count groups, not packages; Failed also counts a group with a failed package; search finds a group by a package name", () => {
  const { rows } = groupGames(LIST);
  const counts = Object.fromEntries(chipCounts(rows, { query: "", liveJobsByAppid: new Map() }).map((c) => [c.key, c.count]));
  assert.equal(counts.all, 2);
  assert.equal(counts.cached, 2);
  assert.equal(counts.failed, 1, "the group counts once, for its failed package");
  assert.deepEqual(visibleGames(rows, { query: "", filterKey: "failed", liveJobsByAppid: new Map() }).map((g) => g.appid), [100]);
  assert.deepEqual(visibleGames(rows, { query: "soundtrack", filterKey: "all", liveJobsByAppid: new Map() }).map((g) => g.appid), [100]);
  const live = indexLiveJobsByAppid([{ id: 1, appid: 103, type: "prefill", status: "running" }]);
  assert.equal(chipCounts(rows, { query: "", liveJobsByAppid: live }).find((c) => c.key === "failed").count, 0);
});

test("shortPackageName drops a leading '<parent> - ' (dash, en/em dash or colon), case-insensitively, else unchanged", () => {
  assert.equal(shortPackageName("HELLDIVERS™ 2 - Português-Brasil Voice", "HELLDIVERS™ 2"), "Português-Brasil Voice");
  assert.equal(shortPackageName("Starfall Legion – Soundtrack", "Starfall Legion"), "Soundtrack");
  assert.equal(shortPackageName("starfall legion: Cosmetics", "Starfall Legion"), "Cosmetics");
  assert.equal(shortPackageName("Starfall Legionnaire Pack", "Starfall Legion"), "Starfall Legionnaire Pack");
  assert.equal(shortPackageName("Starfall Legion - ", "Starfall Legion"), "Starfall Legion -");
  assert.equal(shortPackageName("Other DLC", "Starfall Legion"), "Other DLC");
  assert.equal(shortPackageName("Starfall Legion - X", null), "Starfall Legion - X");
  assert.equal(shortPackageName(null, "Starfall Legion"), null);
});

test("wording: packages line, aria fragment, Downloads fallback title", () => {
  assert.equal(packagesLineText(1), "+1 content package");
  assert.equal(packagesLineText(3), "+3 content packages");
  assert.equal(packagesAriaText(3, 0), "3 content packages");
  assert.equal(packagesAriaText(1, 1), "1 content package, 1 failed");
  const title = (p) => (p === 100 ? "Starfall Legion" : null);
  assert.equal(contentPackageFallbackTitle(row(103, { parent_appid: 100, name: null }), title), "Starfall Legion · content 103");
  assert.equal(contentPackageFallbackTitle(row(103, { parent_appid: 999 }), title), null);
  assert.equal(contentPackageFallbackTitle(row(103), title), null);
});

test("wiring: Downloads' nameFor falls back to the content-package title only for a row without a name", async () => {
  const { readFileSync } = await import("node:fs");
  const src = readFileSync(new URL("../js/views/downloads.js", import.meta.url), "utf8");
  const body = /function nameFor\(appid, gamesByAppid\) \{([\s\S]*?)\n\}/.exec(src);
  assert.ok(body, "nameFor not found");
  assert.match(body[1], /if \(game && !hasText\(game\.name\)\) \{[\s\S]*contentPackageFallbackTitle\(game,/);
});

test("demo: group_size_bytes is the depot union on the parent, null on its packages, size_bytes on a plain game", async () => {
  const { demoRequest, resetDemoData } = await import("../js/demo-data.js");
  resetDemoData();
  const games = await demoRequest("GET", "/v1/games");
  const by = new Map(games.map((g) => [g.appid, g]));
  // 6.1 GB + 0.15 shared (once) + 0.3 + 0.45
  assert.equal(by.get(2010080).group_size_bytes, 6_100_000_000 + 150_000_000 + 300_000_000 + 450_000_000);
  assert.equal(by.get(2010082).group_size_bytes, null);
  assert.equal(by.get(2010010).group_size_bytes, by.get(2010010).size_bytes);
  assert.equal(by.get(2010020).group_size_bytes, null, "no depots: null like size_bytes");
  const detail = await demoRequest("GET", "/v1/games/2010080");
  assert.equal(detail.group_size_bytes, by.get(2010080).group_size_bytes);
});
