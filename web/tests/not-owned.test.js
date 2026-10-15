/**
 * The `not_owned` outcome in the web UI (WP NOT-OWNED-1).
 *
 * A game row with `status: "not_owned"` (SteamPrefill did not consider the
 * app) shows "Not available to prefill (not owned)" with the neutral dash
 * glyph, never "Failed", offers no Retry, and is no bulk download target.
 * A finished `not_owned` job is history, not an error.
 *
 * Run: node --test "web/tests/*.test.js"
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  KIND,
  dispKind,
  statusAction,
  statusIconKind,
  statusWordFor,
  NOT_OWNED_STATE_WORD,
} from "../js/lib/game-status.js";
import { classifyBulkSelection, buildBulkDownloadPlan } from "../js/lib/bulk-plan.js";
import { partitionJobs, jobIconKind, jobStatusWord } from "../js/lib/job-partition.js";
import { offersRetryFor } from "../js/lib/job-failure.js";

const LABELS = { error: "Failed", none: "Not cached" };
const row = (over = {}) => ({ appid: 7, name: "Game", status: "not_owned", size_bytes: null, ...over });

test("a not_owned row shows its own kind and word, never Failed", () => {
  assert.equal(dispKind(row(), undefined), KIND.NOT_OWNED);
  assert.equal(statusWordFor(KIND.NOT_OWNED, LABELS), "Not available to prefill (not owned)");
  assert.equal(NOT_OWNED_STATE_WORD, "Not available to prefill (not owned)");
  assert.equal(statusIconKind(KIND.NOT_OWNED), "notinuse");
});

test("a not_owned row offers a muted Check again, never Retry", () => {
  assert.deepEqual(statusAction(row(), undefined, false), {
    type: "download",
    title: "Check again",
    muted: true,
  });
  assert.equal(offersRetryFor(row()), false);
});

test("an error row still shows Failed and Retry", () => {
  assert.equal(dispKind(row({ status: "error" }), undefined), KIND.ERROR);
  assert.deepEqual(statusAction(row({ status: "error" }), undefined, false), {
    type: "download",
    title: "Retry download",
  });
  assert.equal(offersRetryFor(row({ status: "error" })), true);
});

test("a live job still wins over not_owned", () => {
  assert.equal(dispKind(row(), { status: "running" }), KIND.RUNNING);
});

test("a not_owned job is history with the neutral word", () => {
  const job = { id: 1, appid: 7, type: "prefill", status: "not_owned" };
  assert.equal(jobIconKind(job), "notinuse");
  assert.equal(jobStatusWord(job), "Not available to prefill (not owned)");
  assert.ok(partitionJobs([job]).history.includes(job));
});

test("bulk download never targets a not_owned app", () => {
  const picked = [row(), row({ appid: 8, status: "idle" })];
  const c = classifyBulkSelection(picked, []);
  assert.deepEqual(c.notOwned.map((g) => g.appid), [7]);
  const plan = buildBulkDownloadPlan(c, picked.length);
  assert.deepEqual(plan.primaryTargets, [8]);
  assert.match(plan.note, /not available to prefill \(not owned\)/);
});
