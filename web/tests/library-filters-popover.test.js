/**
 * LIBRARY-1: the metadata filter badge count (pure) and its Filters popover
 * source wiring. The popover's open/close behaviour is pinned in
 * disclosure.test.js.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { activeFilterCount, DEFAULT_META_FILTER } from "../js/lib/library-meta.js";

test("activeFilterCount counts each filter group once", () => {
  assert.equal(activeFilterCount({ ...DEFAULT_META_FILTER }), 0);
  assert.equal(activeFilterCount({ ...DEFAULT_META_FILTER, categories: ["a", "b"] }), 1);
  assert.equal(activeFilterCount({ ...DEFAULT_META_FILTER, genres: [1, 2], minRating: 80 }), 2);
  assert.equal(activeFilterCount({ ...DEFAULT_META_FILTER, yearFrom: 1990, yearTo: 2000 }), 1);
  assert.equal(
    activeFilterCount({ categories: ["a"], genres: [1], minRating: 70, yearFrom: 1990, yearTo: null, includeUnknown: true }),
    4,
  );
  assert.equal(activeFilterCount({ ...DEFAULT_META_FILTER, includeUnknown: true }), 0, "a modifier, not a filter");
});

const src = readFileSync(new URL("../js/views/library.js", import.meta.url), "utf8");

test("wiring: the Filters button is a disclosure over the metadata panel, always visible (WP LIBRARY-2), torn down on view change", () => {
  assert.match(src, /createDisclosure\(\{ trigger: filtersBtn, panel: metaPanel \}\)/);
  assert.match(src, /filtersBtn\.setAttribute\("aria-haspopup", "true"\)/);
  // WP LIBRARY-2: never hidden; without metadata the popover holds a note
  // and the button counts 0.
  assert.doesNotMatch(src, /filtersWrap\.hidden\s*=/);
  const panel = src.slice(src.indexOf("function renderMetaPanel()"), src.indexOf("const cats = el("));
  assert.match(panel, /if \(!available\) \{\s*const note = el\("p", "lib-meta-note", NO_META_TEXT\);[\s\S]*?panel\.replaceChildren\(note\);\s*return;/);
  assert.match(src, /const active = metaAvailable\(state\.rawGames\) \? activeFilterCount\(state\.metaFilter\) : 0;/);
  assert.match(src, /els\?\.filtersDisclosure\.dispose\(\)/);
  assert.match(src, /els\?\.splitDisclosure\.dispose\(\)/);
  assert.match(src, /`Filters, \$\{n\} active`/, "the count reaches assistive tech");
});

test("wiring: rebuilding the panel restores focus to the same control", () => {
  assert.match(src, /panel\.querySelector\(focusKey\)/);
  assert.match(src, /clear-meta/);
});
