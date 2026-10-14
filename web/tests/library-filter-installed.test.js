/**
 * WP LIB-FILTER-INST-1 — the "Installed, not cached" library chip.
 *
 * Pure part (lib/library-filters.js): the predicate is the card's own
 * warning-badge state (`installedBadgeState` NOT_CACHED), the chip is always
 * listed (count 0 included, like Cached/Not cached/Downloading/Failed), it
 * ANDs with the search, and a D9 group row counts once. Demo part: the seed
 * carries an installed-but-not-cached game, so the chip shows a non-zero
 * count in demo mode.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { chipCounts, visibleGames, FILTER_DEFS } from "../js/lib/library-filters.js";
import { groupGames } from "../js/lib/content-groups.js";
import { demoRequest, resetDemoData } from "../js/demo-data.js";

const at = "2026-10-14T08:00:00Z";
const pcs = (...ids) => ids.map((client_id) => ({ client_id, reported_at: at }));
const row = (appid, over = {}) => ({
  appid,
  name: `Game ${appid}`,
  status: "done",
  last_prefill_at: at,
  size_bytes: 1_000,
  installed_on: [],
  parent_appid: null,
  app_type: null,
  ...over,
});

const NO_JOBS = new Map();
const count = (rows, query = "") => chipCounts(rows, { query, liveJobsByAppid: NO_JOBS }).find((c) => c.key === "installed")?.count;
const shown = (rows, query = "") => visibleGames(rows, { query, filterKey: "installed", liveJobsByAppid: NO_JOBS }).map((g) => g.appid);

test("chip definition: key 'installed', label 'Installed, not cached', placed right after 'Not cached', not optional", () => {
  const i = FILTER_DEFS.findIndex((f) => f.key === "installed");
  assert.ok(i > 0, "chip missing");
  assert.equal(FILTER_DEFS[i].label, "Installed, not cached");
  assert.equal(FILTER_DEFS[i - 1].key, "none");
  assert.notEqual(FILTER_DEFS[i].optional, true);
});

test("MUTATION TARGET: matches installed AND no bytes; not installed-and-cached, not uninstalled, not a tool app", () => {
  const rows = [
    row(1, { installed_on: pcs("Demon"), size_bytes: null, status: "idle", last_prefill_at: null }), // yes
    row(2, { installed_on: pcs("Demon", "Frame"), size_bytes: 0 }), // yes: zero bytes is "no bytes"
    row(3, { installed_on: pcs("Demon"), size_bytes: 5e9 }), // installed and cached
    row(4, { installed_on: [], size_bytes: null, status: "idle" }), // not installed
    row(5, { size_bytes: null, installed_on: undefined }), // older shape without the field
    row(6, { installed_on: pcs("Demon"), size_bytes: null, tool_app: true }), // tool app: never the warning
    row(7, { installed_on: pcs("Demon"), size_bytes: null, status: "error" }), // failed, nothing on disk: yes
  ];
  assert.deepEqual(shown(rows), [1, 2, 7]);
  assert.equal(count(rows), 3);
});

test("always listed, with 0 when nothing matches (like the other core chips)", () => {
  const keys = chipCounts([row(1)], { query: "", liveJobsByAppid: NO_JOBS }).map((c) => c.key);
  assert.ok(keys.includes("installed"));
  assert.equal(count([row(1)]), 0);
  assert.equal(count([]), 0);
});

test("ANDs with the search: the count follows the query", () => {
  const rows = [
    row(1, { name: "Aurora Cascade", installed_on: pcs("Demon"), size_bytes: null }),
    row(2, { name: "Copper Horizon", installed_on: pcs("Demon"), size_bytes: null }),
  ];
  assert.equal(count(rows, "copper"), 1);
  assert.deepEqual(shown(rows, "copper"), [2]);
});

test("D9: a group row counts once and reads the parent's own row (like the card's badge)", () => {
  const parent = row(100, { name: "Starfall Legion", app_type: "game", installed_on: pcs("Demon"), size_bytes: null, status: "idle", last_prefill_at: null });
  const pkgA = row(101, { parent_appid: 100, app_type: "dlc", installed_on: pcs("Demon"), size_bytes: null, status: "idle" });
  const pkgB = row(102, { parent_appid: 100, app_type: "dlc", installed_on: pcs("Demon"), size_bytes: null, status: "idle" });
  const { rows } = groupGames([parent, pkgA, pkgB, row(200)]);
  assert.equal(rows.length, 2, "sanity: grouped");
  assert.equal(count(rows), 1, "the group counts once, not three times");
  assert.deepEqual(shown(rows), [100]);
});

test("demo seed: the chip shows a non-zero count, and every match is installed with no bytes", async () => {
  resetDemoData();
  const { rows } = groupGames(await demoRequest("GET", "/v1/games"));
  const matches = visibleGames(rows, { query: "", filterKey: "installed", liveJobsByAppid: NO_JOBS });
  assert.ok(matches.length >= 1, "demo mode must show the chip with at least one game");
  for (const g of matches) {
    assert.ok(g.installed_on.length > 0, g.name);
    assert.ok(!(g.size_bytes > 0), g.name);
  }
  assert.equal(count(rows), matches.length);
});
