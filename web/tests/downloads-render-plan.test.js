/**
 * Headless tests for web/js/lib/downloads-render-plan.js (WP 4a.5).
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { planJobsUpdate } from "../js/lib/downloads-render-plan.js";

const j = (id, extra) => ({
  id,
  appid: 1000 + id,
  status: "running",
  stop_request: null,
  ...extra,
});

test("first poll (isFirst): always a full render, nothing painted yet", () => {
  assert.deepEqual(planJobsUpdate({ isFirst: true, added: [], updated: [], removed: [] }), {
    full: true,
    patchStopRequest: [],
  });
});

test("a null/undefined diff is treated as a full render (defensive)", () => {
  assert.deepEqual(planJobsUpdate(null), { full: true, patchStopRequest: [] });
  assert.deepEqual(planJobsUpdate(undefined), { full: true, patchStopRequest: [] });
});

test("added rows -> full render (a job entering the polled window can move section membership)", () => {
  const diff = { isFirst: false, added: [j(1, { status: "queued" })], updated: [], removed: [] };
  assert.deepEqual(planJobsUpdate(diff), { full: true, patchStopRequest: [] });
});

test("removed rows -> full render (a job aging out of ?limit=20)", () => {
  const diff = { isFirst: false, added: [], updated: [], removed: [j(1, {})] };
  assert.deepEqual(planJobsUpdate(diff), { full: true, patchStopRequest: [] });
});

test("no added/removed/updated -> full:false, nothing to patch", () => {
  const diff = { isFirst: false, added: [], updated: [], removed: [] };
  assert.deepEqual(planJobsUpdate(diff), { full: false, patchStopRequest: [] });
});

// ---------------------------------------------------------------------
// MUTATION TARGET 1: ANY status change anywhere in the batch must force a
// full rebuild. If this branch were removed/weakened, a job that just
// transitioned (e.g. running -> done, or paused -> queued on resume) could
// be classified as patch-only, leaving its card in the WRONG section with
// the wrong action buttons and icon indefinitely.
// ---------------------------------------------------------------------
test("MUTATION TARGET: a status change forces full:true, even alongside an unrelated stop_request change", () => {
  const diff = {
    isFirst: false,
    added: [],
    removed: [],
    updated: [
      {
        prev: j(1, { status: "running", stop_request: "cancel" }),
        curr: j(1, { status: "done", stop_request: null }),
      },
    ],
  };
  const plan = planJobsUpdate(diff);
  assert.equal(plan.full, true);
  assert.deepEqual(plan.patchStopRequest, []);
});

// ---------------------------------------------------------------------
// MUTATION TARGET 2: a stop_request change with the SAME status must land
// in patchStopRequest, NOT force a full rebuild. If this were flipped (any
// update -> full), every pause/cancel click would recreate the running
// card's animated status-icon node the instant the server acknowledged the
// request — the round-7 mockup bug, reintroduced on the one live field
// this view has.
// ---------------------------------------------------------------------
test("MUTATION TARGET: a stop_request-only change (status unchanged) lands in patchStopRequest, not full", () => {
  const diff = {
    isFirst: false,
    added: [],
    removed: [],
    updated: [
      {
        prev: j(1, { status: "running", stop_request: null }),
        curr: j(1, { status: "running", stop_request: "cancel" }),
      },
    ],
  };
  const plan = planJobsUpdate(diff);
  assert.equal(plan.full, false);
  assert.deepEqual(plan.patchStopRequest, [1]);
});

test("stop_request clearing (worker actually stopped the job) also patches, not rebuilds, while status holds", () => {
  const diff = {
    isFirst: false,
    added: [],
    removed: [],
    updated: [
      {
        prev: j(1, { status: "running", stop_request: "pause" }),
        curr: j(1, { status: "running", stop_request: null }),
      },
    ],
  };
  assert.deepEqual(planJobsUpdate(diff), { full: false, patchStopRequest: [1] });
});

test("a mixed batch: one pure stop_request patch + one untouched job -> patch only the one that changed", () => {
  const diff = {
    isFirst: false,
    added: [],
    removed: [],
    updated: [
      {
        prev: j(1, { status: "running", stop_request: null }),
        curr: j(1, { status: "running", stop_request: "pause" }),
      },
      {
        prev: j(2, { status: "running", stop_request: "cancel" }),
        curr: j(2, { status: "running", stop_request: "cancel" }), // unchanged value
      },
    ],
  };
  const plan = planJobsUpdate(diff);
  assert.equal(plan.full, false);
  assert.deepEqual(plan.patchStopRequest, [1]);
});

test("a batch where ONE job transitions status and another only has a stop_request change: the status change wins (full)", () => {
  const diff = {
    isFirst: false,
    added: [],
    removed: [],
    updated: [
      {
        prev: j(1, { status: "running", stop_request: null }),
        curr: j(1, { status: "running", stop_request: "cancel" }),
      },
      {
        prev: j(2, { status: "queued", stop_request: null }),
        curr: j(2, { status: "running", stop_request: null }),
      },
    ],
  };
  const plan = planJobsUpdate(diff);
  assert.equal(plan.full, true);
  assert.deepEqual(plan.patchStopRequest, []);
});

// WP CAP-503-FIX-1b: `pause_reason` / `client_override` (schema v34) change a
// card's notes only, so they ride the same text patch as `stop_request`.
test("MUTATION TARGET: a pause_reason-only change (client pause turned manual, still paused) is patched, not rebuilt", () => {
  const plan = planJobsUpdate({
    isFirst: false,
    added: [],
    removed: [],
    updated: [{ prev: j(1, { status: "paused", pause_reason: "client" }), curr: j(1, { status: "paused", pause_reason: "manual" }) }],
  });
  assert.equal(plan.full, false);
  assert.deepEqual(plan.patchStopRequest, [1]);
});

test("MUTATION TARGET: a client_override-only change is patched, not rebuilt", () => {
  const plan = planJobsUpdate({
    isFirst: false,
    added: [],
    removed: [],
    updated: [{ prev: j(2, { client_override: false }), curr: j(2, { client_override: true }) }],
  });
  assert.equal(plan.full, false);
  assert.deepEqual(plan.patchStopRequest, [2]);
});
