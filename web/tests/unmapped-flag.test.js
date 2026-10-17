/**
 * WP UNMAPPED-FLAG-1: the detail line says when depots outside the figure
 * exist (`cache_unmapped_depots` from the API), and stays unchanged without.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { completenessLine, unmappedNote, UNKNOWN_LINE } from "../js/lib/cache-completeness.js";

const GB = 1_073_741_824;
const row = (extra = {}) => ({
  appid: 1,
  cache_complete_pct: 100,
  cache_expected_bytes: 3 * GB,
  cache_present_bytes: 3 * GB,
  cache_complete_checked_at: null,
  ...extra,
});

test("unmapped depots are named next to the percentage", () => {
  assert.equal(
    completenessLine(row({ cache_unmapped_depots: 1 })),
    "100% complete — 3.0 GB of 3.0 GB on disk · not counted: 1 unmapped depot",
  );
  assert.match(completenessLine(row({ cache_unmapped_depots: 2 })), /· not counted: 2 unmapped depots$/);
});

test("0, absent and malformed values add nothing; unknown stays unknown", () => {
  const clean = "100% complete — 3.0 GB of 3.0 GB on disk";
  for (const v of [0, undefined, null, -1, 1.5, "2"]) {
    assert.equal(completenessLine(row({ cache_unmapped_depots: v })), clean);
    assert.equal(unmappedNote({ cache_unmapped_depots: v }), "");
  }
  assert.equal(completenessLine({ cache_complete_pct: null, cache_unmapped_depots: 3 }), UNKNOWN_LINE);
});
