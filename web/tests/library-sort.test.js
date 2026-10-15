/**
 * WP LIB-SORT — library sort: ordering rules, the "last played" gate and the
 * wiring in views/library.js.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  DEFAULT_SORT,
  availableSortOptions,
  lastPlayedAvailable,
  normalizeSort,
  sortGames,
  SORT_OPTIONS,
} from "../js/lib/library-sort.js";
import { withOwnedLastPlayed } from "../js/lib/owned-library.js";

const row = (appid, over = {}) => ({
  appid,
  name: `Game ${appid}`,
  status: "done",
  last_prefill_at: null,
  size_bytes: null,
  installed_on: [],
  ...over,
});
const ids = (rows) => rows.map((g) => g.appid);
const sort = (rows, key, dir) => ids(sortGames(rows, { key, dir }, new Map()));

test("name: A-Z, Z-A, case-insensitive, name-less (null and blank) last in both directions", () => {
  const rows = [
    row(1, { name: "banana" }),
    row(2, { name: null }),
    row(3, { name: "Apple" }),
    row(4, { name: "  " }),
    row(5, { name: "cherry" }),
  ];
  assert.deepEqual(sort(rows, "name", "asc"), [3, 1, 5, 2, 4]);
  assert.deepEqual(sort(rows, "name", "desc"), [5, 1, 3, 2, 4]);
});

test("size: flips with direction, missing size last, group size wins, ties by name then appid", () => {
  const rows = [
    row(1, { size_bytes: 10, name: "B" }),
    row(2, { size_bytes: null }),
    row(3, { size_bytes: 10, name: "A" }),
    row(4, { size_bytes: 5, group_size_bytes: 50 }),
  ];
  assert.deepEqual(sort(rows, "size", "asc"), [3, 1, 4, 2]);
  assert.deepEqual(sort(rows, "size", "desc"), [4, 3, 1, 2]);
});

test("updated: newest of a group's members, never-cached last", () => {
  const rows = [
    row(1, { last_prefill_at: "2026-10-01T00:00:00Z" }),
    row(2),
    row(3, { last_prefill_at: "2026-09-01T00:00:00Z", packages: [row(9, { last_prefill_at: "2026-10-10T00:00:00Z" })] }),
  ];
  assert.deepEqual(sort(rows, "updated", "desc"), [3, 1, 2]);
  assert.deepEqual(sort(rows, "updated", "asc"), [1, 3, 2]);
});

test("state: cached before not cached; stable for equal rows", () => {
  const rows = [row(1, { status: "new", name: "Z" }), row(2, { name: "Y", size_bytes: 100 }), row(3, { status: "new", name: "A" })];
  const out = sort(rows, "state", "asc");
  assert.equal(out[0], 2, "cached game first");
  assert.deepEqual(out.slice(1), [3, 1], "equal rank falls back to name");
});

test("last played: 0 means never played (last); gate detection and normalization", () => {
  const owned = [
    { appid: 1, rtime_last_played: 300 },
    { appid: 2, rtime_last_played: 0 },
    { appid: 3, rtime_last_played: 900 },
  ];
  const rows = withOwnedLastPlayed([row(1), row(2), row(3), row(4)], owned);
  assert.equal(lastPlayedAvailable(rows), true);
  assert.deepEqual(sort(rows, "played", "desc"), [3, 1, 2, 4]);
  // Flag off: the relay omits the key, nothing is copied.
  const off = withOwnedLastPlayed([row(1)], [{ appid: 1, name: "x" }]);
  assert.equal(lastPlayedAvailable(off), false);
  assert.deepEqual(availableSortOptions({ lastPlayed: false }).map((o) => o.key), ["name", "size", "state", "updated"]);
  assert.equal(availableSortOptions({ lastPlayed: true, meta: true }).length, SORT_OPTIONS.length);
  // A stored 'played' falls back to A-Z while off, and is honoured when on.
  assert.deepEqual(normalizeSort({ key: "played", dir: "desc" }, { lastPlayed: false }), DEFAULT_SORT);
  assert.deepEqual(normalizeSort({ key: "played", dir: "desc" }, { lastPlayed: true }), { key: "played", dir: "desc" });
  assert.deepEqual(normalizeSort({ key: "bogus", dir: "x" }, { lastPlayed: true }), DEFAULT_SORT);
});

test("wiring: library.js persists in try/catch and sorts the visible list", () => {
  const src = readFileSync(new URL("../js/views/library.js", import.meta.url), "utf8");
  assert.match(src, /steamvault\.librarySort/);
  assert.match(src, /sortGames\(\s*visibleGames\(/);
  assert.match(src, /function writeStoredSort[\s\S]*?try \{[\s\S]*?\} catch/);
});
