/**
 * Headless tests for web/js/lib/depot-presentation.js (WP 4a.4).
 *
 * Row shape mirrors lib/multiplan.js's `MultiPlanDepotRow`
 * ({depotid, sizeBytes, shared, others, holderAppids, free}) for a
 * single-id batch. Ported alongside the Android sibling's
 * `DepotPresentationTest.kt` (WP 4b.6) — same four states, same reasoning.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildDepotPresentation, DEPOT_TAG } from "../js/lib/depot-presentation.js";

function row(overrides) {
  return { depotid: 441, sizeBytes: 1_000_000, shared: false, others: [], holderAppids: [], free: true, ...overrides };
}

test("EXCLUSIVE: an unshared depot never gets a tag, regardless of thisAppIsHolder", () => {
  const gamesByAppid = new Map();
  assert.equal(buildDepotPresentation(row({ shared: false }), gamesByAppid, true).tag, DEPOT_TAG.EXCLUSIVE);
  assert.equal(buildDepotPresentation(row({ shared: false }), gamesByAppid, false).tag, DEPOT_TAG.EXCLUSIVE);
});

test("EXCLUSIVE: has no co-owners (multiplan.js never populates `others` for an unshared row)", () => {
  const p = buildDepotPresentation(row({ shared: false, others: [] }), new Map(), true);
  assert.deepEqual(p.coOwners, []);
});

test("PROTECTED: a shared depot with at least one OTHER cached holder is PROTECTED regardless of thisAppIsHolder", () => {
  const gamesByAppid = new Map([[730, { name: "Other Cached" }]]);
  const r = row({ shared: true, others: [730], holderAppids: [730], free: false });
  assert.equal(buildDepotPresentation(r, gamesByAppid, true).tag, DEPOT_TAG.PROTECTED);
  assert.equal(buildDepotPresentation(r, gamesByAppid, false).tag, DEPOT_TAG.PROTECTED);
});

test("SOLE_HOLDER: a shared depot with no OTHER cached holder, and THIS app holds it", () => {
  const gamesByAppid = new Map([[730, { name: "Other Uncached" }]]);
  const r = row({ shared: true, others: [730], holderAppids: [], free: true });
  assert.equal(buildDepotPresentation(r, gamesByAppid, true).tag, DEPOT_TAG.SOLE_HOLDER);
});

test("ORPHANED (recorded WP 4b.6 divergence, adopted here): a shared depot with no cached holder at all, incl. THIS app", () => {
  // The last-remnant/Meridian-Rally scenario (ADR-0003): the VIEWED game
  // does not hold this depot either -- neither it nor any co-owner is
  // cached. Collapsing this into SOLE_HOLDER would state a falsehood.
  const gamesByAppid = new Map([[730, { name: "Other Uncached" }]]);
  const r = row({ shared: true, others: [730], holderAppids: [], free: true });
  assert.equal(buildDepotPresentation(r, gamesByAppid, false).tag, DEPOT_TAG.ORPHANED);
});

test("coOwners: resolves names from gamesByAppid, falls back to 'Unknown app' (never a bare id) for an unknown one", () => {
  const gamesByAppid = new Map([[730, { name: "Known Game" }]]);
  const r = row({ shared: true, others: [730, 999], holderAppids: [730] });
  const p = buildDepotPresentation(r, gamesByAppid, false);
  assert.deepEqual(
    p.coOwners.map((c) => ({ appid: c.appid, name: c.name, cached: c.cached })),
    [
      { appid: 730, name: "Known Game", cached: true },
      { appid: 999, name: "Unknown app", cached: false },
    ],
  );
});

test("passes depotid and sizeBytes through unchanged", () => {
  const p = buildDepotPresentation(row({ depotid: 12345, sizeBytes: 42 }), new Map(), false);
  assert.equal(p.depotid, 12345);
  assert.equal(p.sizeBytes, 42);
});
