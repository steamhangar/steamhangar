/**
 * WP D23 LIBRARY-META-1c - library metadata filters, rating/year sort and
 * their wiring in views/library.js.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  applyMetaFilter,
  availableGenres,
  matchesMeta,
  normalizeMetaFilter,
  unknownNote,
  DEFAULT_META_FILTER,
} from "../js/lib/library-meta.js";
import {
  DEFAULT_SORT,
  availableSortOptions,
  defaultDirFor,
  metaAvailable,
  normalizeSort,
  sortGames,
} from "../js/lib/library-sort.js";

const meta = (over = {}) => ({
  single_player: true,
  multiplayer: false,
  coop: false,
  vr: false,
  genres: [1],
  review_percent: 90,
  review_score: 8,
  release_year: 2020,
  fetched_at: "2026-10-15T00:00:00Z",
  ...over,
});
const game = (appid, m, over = {}) => ({ appid, name: `G${appid}`, meta: m, ...over });
const f = (over = {}) => normalizeMetaFilter({ ...DEFAULT_META_FILTER, ...over });
const ids = (rows) => rows.map((g) => g.appid);

test("metaAvailable: false when every meta is null or absent, true with one", () => {
  assert.equal(metaAvailable([game(1, null), { appid: 2 }]), false);
  assert.equal(metaAvailable([]), false);
  assert.equal(metaAvailable([game(1, null), game(2, meta())]), true);
});

test("rating/year sort options exist only with metadata", () => {
  assert.deepEqual(availableSortOptions({ lastPlayed: false }).map((o) => o.key), ["name", "size", "state", "updated"]);
  assert.deepEqual(
    availableSortOptions({ lastPlayed: false, meta: true }).map((o) => o.key),
    ["name", "size", "state", "updated", "rating", "year"],
  );
  assert.deepEqual(normalizeSort({ key: "rating", dir: "desc" }, { lastPlayed: false }), DEFAULT_SORT);
  assert.deepEqual(normalizeSort({ key: "year", dir: "asc" }, { lastPlayed: false, meta: true }), { key: "year", dir: "asc" });
  assert.equal(defaultDirFor("rating"), "desc");
  assert.equal(defaultDirFor("name"), "asc");
});

test("sort by rating and year: unknown last in both directions, ties by name", () => {
  const rows = [
    game(1, meta({ review_percent: 70, release_year: 2010 })),
    game(2, null),
    game(3, meta({ review_percent: 95, release_year: 2022 })),
    game(4, meta({ review_percent: null, release_year: null })),
    game(5, meta({ review_percent: 70, release_year: 2010 }), { name: "A" }),
  ];
  const by = (key, dir) => ids(sortGames(rows, { key, dir }, new Map()));
  assert.deepEqual(by("rating", "desc"), [3, 5, 1, 2, 4]);
  assert.deepEqual(by("rating", "asc"), [5, 1, 3, 2, 4]);
  assert.deepEqual(by("year", "desc"), [3, 5, 1, 2, 4]);
  assert.deepEqual(by("year", "asc"), [5, 1, 3, 2, 4]);
});

test("filter: categories AND, genres any-of, rating and year bounds inclusive", () => {
  const rows = [
    game(1, meta({ coop: true, multiplayer: true, genres: [1, 3], review_percent: 80, release_year: 2015 })),
    game(2, meta({ coop: true, genres: [2], review_percent: 79, release_year: 2015 })),
    game(3, meta({ vr: true, genres: [3], review_percent: 95, release_year: 2024 })),
  ];
  const run = (over) => ids(applyMetaFilter(rows, f(over)).games);
  assert.deepEqual(run({ categories: ["coop"] }), [1, 2]);
  assert.deepEqual(run({ categories: ["coop", "multi"] }), [1]);
  assert.deepEqual(run({ genres: [2, 3] }), [1, 2, 3]);
  assert.deepEqual(run({ genres: [2] }), [2]);
  assert.deepEqual(run({ minRating: 80 }), [1, 3]);
  assert.deepEqual(run({ yearFrom: 2015, yearTo: 2015 }), [1, 2]);
  assert.deepEqual(run({ yearFrom: 2016 }), [3]);
  assert.deepEqual(run({ categories: ["vr"], minRating: 96 }), []);
  assert.deepEqual(run({}), [1, 2, 3], "no active filter keeps everything");
});

test("unknown metadata: excluded and counted by default, kept with include-unknown", () => {
  const rows = [
    game(1, meta({ review_percent: 90 })),
    game(2, meta({ review_percent: 40 })), // known and failing: never counted as unknown
    game(3, null),
    game(4, meta({ review_percent: null })),
    game(5, null, { name: "owned only" }),
  ];
  const off = applyMetaFilter(rows, f({ minRating: 80 }));
  assert.deepEqual(ids(off.games), [1]);
  assert.equal(off.unknownExcluded, 3);
  const on = applyMetaFilter(rows, f({ minRating: 80, includeUnknown: true }));
  assert.deepEqual(ids(on.games), [1, 3, 4, 5]);
  assert.equal(on.unknownExcluded, 0);
  // A known failing clause still excludes with include-unknown (API rule).
  assert.equal(matchesMeta(rows[1], f({ minRating: 80, includeUnknown: true })), false);
  // A game unknown for one clause but failing another is not "unknown-hidden".
  const mixed = applyMetaFilter([game(6, meta({ review_percent: null, coop: false }))], f({ minRating: 80, categories: ["coop"] }));
  assert.equal(mixed.unknownExcluded, 0);
  // Without an active filter nothing is hidden or counted.
  assert.equal(applyMetaFilter(rows, f()).unknownExcluded, 0);
  assert.equal(applyMetaFilter(rows, f({ includeUnknown: true })).games.length, 5);
});

test("genre unknown means no genres stored", () => {
  const rows = [game(1, meta({ genres: [] })), game(2, meta({ genres: [1] }))];
  assert.deepEqual(ids(applyMetaFilter(rows, f({ genres: [1] })).games), [2]);
  assert.equal(applyMetaFilter(rows, f({ genres: [1] })).unknownExcluded, 1);
});

test("unknownNote wording is neutral and singular/plural aware", () => {
  assert.equal(unknownNote(0), "");
  assert.equal(unknownNote(1), "1 game hidden: metadata unknown");
  assert.equal(unknownNote(12), "12 games hidden: metadata unknown");
  assert.doesNotMatch(unknownNote(2), /steam/i);
});

test("normalizeMetaFilter makes stored garbage safe", () => {
  assert.deepEqual(normalizeMetaFilter(null), DEFAULT_META_FILTER);
  assert.deepEqual(normalizeMetaFilter("x"), DEFAULT_META_FILTER);
  const n = normalizeMetaFilter({
    categories: ["coop", "bogus", "coop", "vr"],
    genres: [3, "3", -1, 1.5, "x", 1],
    minRating: 101,
    yearFrom: "2020",
    yearTo: 2010,
    includeUnknown: "yes",
  });
  assert.deepEqual(n.categories, ["coop", "vr"]);
  assert.deepEqual(n.genres, [1, 3]);
  assert.equal(n.minRating, null);
  assert.deepEqual([n.yearFrom, n.yearTo], [2010, 2020], "swapped years are put in order");
  assert.equal(n.includeUnknown, false);
  assert.equal(normalizeMetaFilter({ minRating: "85" }).minRating, 85);
  assert.equal(normalizeMetaFilter({ yearFrom: 1800 }).yearFrom, null);
});

test("availableGenres lists the ids in the data with labels", () => {
  const out = availableGenres([game(1, meta({ genres: [3, 1] })), game(2, meta({ genres: [999] })), game(3, null)]);
  assert.deepEqual(out.map((g) => g.id), [1, 999, 3]);
  assert.deepEqual(out.map((g) => g.label), ["Action", "Genre 999", "RPG"]);
});

test("wiring: library.js persists the filter in try/catch, hides the panel without meta, shows the note", () => {
  const src = readFileSync(new URL("../js/views/library.js", import.meta.url), "utf8");
  assert.match(src, /steamvault\.libraryMetaFilter/);
  const read = src.slice(src.indexOf("function readStoredMetaFilter"), src.indexOf("function writeStoredMetaFilter"));
  assert.match(read, /try \{[\s\S]*normalizeMetaFilter[\s\S]*\} catch/);
  assert.match(src, /els\.filtersWrap\.hidden = !available/);
  assert.match(src, /if \(!metaAvailable\(state\.rawGames\)\) return \{ games: state\.games, unknownExcluded: 0 \}/);
  assert.match(src, /unknownNote\(metaFiltered\(\)\.unknownExcluded\)/);
  assert.match(src, /visibleGames\(metaFiltered\(\)\.games/);
  assert.match(src, /chipCounts\(metaFiltered\(\)\.games/);
});
