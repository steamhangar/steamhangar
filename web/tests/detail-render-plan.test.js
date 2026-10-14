/**
 * Headless tests for web/js/lib/detail-render-plan.js (WP 4a.4 round-7
 * patch-in-place requirement).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildDetailStructuralKey } from "../js/lib/detail-render-plan.js";
import { installedSectionPresence } from "../js/lib/game-status.js";

const base = { dispKind: "cached", trackedJobStatus: null, depotTags: ["exclusive", "protected"] };

test("identical inputs produce an identical key", () => {
  assert.equal(buildDetailStructuralKey(base), buildDetailStructuralKey({ ...base }));
});

test("MUTATION TARGET -- a dispKind change (e.g. a download finishing) changes the key", () => {
  const before = buildDetailStructuralKey(base);
  const after = buildDetailStructuralKey({ ...base, dispKind: "running" });
  assert.notEqual(before, after);
});

test("MUTATION TARGET -- a tracked job's status change (e.g. queued -> running) changes the key", () => {
  const before = buildDetailStructuralKey({ ...base, trackedJobStatus: "queued" });
  const after = buildDetailStructuralKey({ ...base, trackedJobStatus: "running" });
  assert.notEqual(before, after);
});

test("MUTATION TARGET -- a depot's sharing tag flipping (a co-owner's cache state changed) changes the key", () => {
  const before = buildDetailStructuralKey({ ...base, depotTags: ["protected"] });
  const after = buildDetailStructuralKey({ ...base, depotTags: ["orphaned"] });
  assert.notEqual(before, after);
});

test("depot tag ORDER matters (a reordering would be a different presentation list) -- not a bug, just documented", () => {
  const a = buildDetailStructuralKey({ ...base, depotTags: ["exclusive", "protected"] });
  const b = buildDetailStructuralKey({ ...base, depotTags: ["protected", "exclusive"] });
  assert.notEqual(a, b);
});

test("null/undefined trackedJobStatus normalize to the same key (no tracked job)", () => {
  assert.equal(
    buildDetailStructuralKey({ ...base, trackedJobStatus: null }),
    buildDetailStructuralKey({ ...base, trackedJobStatus: undefined }),
  );
});

test("a non-structural change (size_bytes drifting) is simply not part of the key's inputs at all", () => {
  // buildDetailStructuralKey takes no size/byte field -- the caller never
  // feeds one in, so a size-only tick naturally produces the identical key
  // without this module needing to know anything about bytes.
  assert.equal(buildDetailStructuralKey(base), buildDetailStructuralKey(base));
});

test("missing/non-array depotTags defaults to an empty list rather than throwing", () => {
  assert.equal(
    buildDetailStructuralKey({ dispKind: "none", trackedJobStatus: null, depotTags: undefined }),
    buildDetailStructuralKey({ dispKind: "none", trackedJobStatus: null, depotTags: [] }),
  );
});

// ---------------------------------------------------------------------
// installedBadge (WP AG-2). `buildDetailStructuralKey` itself is domain-
// agnostic — it folds WHATEVER STRING it is given, no matter what that
// string means — so the interesting contract lives at the boundary with
// the REAL caller: `components/game-detail-sheet.js`'s `computeStructuralKey`
// feeds it `installedSectionPresence(gameLike)`'s result, never the raw
// `installedBadgeState`. The tests below exercise that boundary directly
// rather than testing this file's string-folding in isolation from what
// actually gets fed into it.
// ---------------------------------------------------------------------

test("MUTATION TARGET -- a presence transition (e.g. none -> present) changes the key", () => {
  const before = buildDetailStructuralKey({ ...base, installedBadge: "none" });
  const after = buildDetailStructuralKey({ ...base, installedBadge: "present" });
  assert.notEqual(before, after);
});

test("MUTATION TARGET (S4 fix) -- CACHED and NOT_CACHED fixtures both resolve to 'present', so the REAL caller's key does not change between them", () => {
  // The exact boundary review round 1's S4 finding is about: if
  // `computeStructuralKey` ever went back to feeding the raw
  // `installedBadgeState` (cached/not_cached/none) into this function
  // instead of `installedSectionPresence`'s collapsed value, this
  // assertion would start failing (cached and not_cached would produce
  // DIFFERENT keys again), which is exactly the regression that forced an
  // unwanted full sheet re-render mid-download.
  const cachedGame = { installed_on: [{ client_id: "c", reported_at: "2026-08-22T00:00:00Z" }], size_bytes: 500 };
  const notCachedGame = { installed_on: [{ client_id: "c", reported_at: "2026-08-22T00:00:00Z" }], size_bytes: null };
  const keyWhileCached = buildDetailStructuralKey({ ...base, installedBadge: installedSectionPresence(cachedGame) });
  const keyWhileNotCached = buildDetailStructuralKey({ ...base, installedBadge: installedSectionPresence(notCachedGame) });
  assert.equal(keyWhileCached, keyWhileNotCached);
});

test("missing installedBadge defaults to 'none', same key as an explicit 'none'", () => {
  assert.equal(
    buildDetailStructuralKey({ ...base, installedBadge: undefined }),
    buildDetailStructuralKey({ ...base, installedBadge: "none" }),
  );
});

test("a pre-AG-2 call site (no installedBadge field at all) still matches an explicit 'none'", () => {
  const { installedBadge, ...withoutField } = { ...base, installedBadge: "none" };
  assert.equal(buildDetailStructuralKey(withoutField), buildDetailStructuralKey({ ...base, installedBadge: "none" }));
});

test("MUTATION TARGET -- WP UPD-6: the update part changes the key; an empty one leaves every other game's key as before", () => {
  const plain = buildDetailStructuralKey(base);
  assert.equal(buildDetailStructuralKey({ ...base, updates: "" }), plain, "empty part: key unchanged");
  const flagged = buildDetailStructuralKey({ ...base, updates: "1:10" });
  assert.notEqual(flagged, plain);
  assert.notEqual(buildDetailStructuralKey({ ...base, updates: "1:" }), flagged, "a job taking the id out changes it too");
});
