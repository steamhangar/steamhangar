/**
 * Headless tests for web/js/lib/gc-flow.js (WP 4a.4).
 *
 * Mirrors the Android sibling's `GcFlowTest.kt` (WP 4b.6) case for case —
 * "GC execute NEVER sent without an explicit user confirm after a dry
 * run... pin the state machine cannot reach execute from idle".
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { GC_STATE, GC_EVENT, idleGcState, reduceGcFlow } from "../js/lib/gc-flow.js";

function job(id, status, log_excerpt = null) {
  return { id, appid: 440, type: "gc", status, created_at: "2026-08-01T00:00:00Z", log_excerpt };
}

// ---- the flagship pin -------------------------------------------------

test("THE PIN -- confirm_execute against idle stays idle, execute can never be reached from idle", () => {
  const result = reduceGcFlow(idleGcState(), { type: GC_EVENT.CONFIRM_EXECUTE });
  assert.deepEqual(result, idleGcState());
});

test("confirm_execute is rejected from every state except confirm_execute itself", () => {
  const nonConfirmStates = [
    idleGcState(),
    { kind: GC_STATE.REQUESTING_DRY_RUN },
    { kind: GC_STATE.POLLING_DRY_RUN, jobId: 1 },
    { kind: GC_STATE.DRY_RUN_PLAN, jobId: 1, job: job(1, "done"), summary: null },
    { kind: GC_STATE.REQUESTING_EXECUTE },
    { kind: GC_STATE.POLLING_EXECUTE, jobId: 1 },
    { kind: GC_STATE.EXECUTE_DONE, jobId: 1, job: job(1, "done"), summary: null },
    { kind: GC_STATE.CANCELLED, jobId: 1 },
    { kind: GC_STATE.ERROR, message: "boom", executeAttempted: false },
  ];
  for (const state of nonConfirmStates) {
    assert.deepEqual(reduceGcFlow(state, { type: GC_EVENT.CONFIRM_EXECUTE }), state);
  }
});

test("request_execute is rejected from every state except dry_run_plan (full parametrised pin)", () => {
  // The reachable regression this specifically guards: from execute_done (a
  // GC that already ran to completion), a second "Execute" tap must NOT
  // re-confirm the OLD plan and queue a second execute without a fresh dry
  // run -- it must be a no-op, exactly like every other non-dry_run_plan state.
  const nonDryRunPlanStates = [
    idleGcState(),
    { kind: GC_STATE.REQUESTING_DRY_RUN },
    { kind: GC_STATE.POLLING_DRY_RUN, jobId: 1 },
    { kind: GC_STATE.CONFIRM_EXECUTE, jobId: 1, job: job(1, "done"), summary: null },
    { kind: GC_STATE.REQUESTING_EXECUTE },
    { kind: GC_STATE.POLLING_EXECUTE, jobId: 1 },
    { kind: GC_STATE.EXECUTE_DONE, jobId: 1, job: job(1, "done"), summary: null },
    { kind: GC_STATE.CANCELLED, jobId: 1 },
    { kind: GC_STATE.ERROR, message: "boom", executeAttempted: false },
  ];
  for (const state of nonDryRunPlanStates) {
    assert.deepEqual(reduceGcFlow(state, { type: GC_EVENT.REQUEST_EXECUTE }), state);
  }
});

// ---- the full valid path ------------------------------------------------

test("full dry-run then execute path", () => {
  let state = idleGcState();

  state = reduceGcFlow(state, { type: GC_EVENT.START_DRY_RUN });
  assert.deepEqual(state, { kind: GC_STATE.REQUESTING_DRY_RUN });

  state = reduceGcFlow(state, { type: GC_EVENT.DRY_RUN_QUEUED, jobId: 7 });
  assert.deepEqual(state, { kind: GC_STATE.POLLING_DRY_RUN, jobId: 7 });

  state = reduceGcFlow(state, { type: GC_EVENT.POLL_RESULT, job: job(7, "queued") });
  assert.deepEqual(state, { kind: GC_STATE.POLLING_DRY_RUN, jobId: 7 }); // still in flight

  state = reduceGcFlow(state, { type: GC_EVENT.POLL_RESULT, job: job(7, "running") });
  assert.deepEqual(state, { kind: GC_STATE.POLLING_DRY_RUN, jobId: 7 });

  const doneJob = job(7, "done", "[vault-api] GC totals (DRY RUN): would_delete=2 (700 bytes)");
  state = reduceGcFlow(state, { type: GC_EVENT.POLL_RESULT, job: doneJob });
  assert.equal(state.kind, GC_STATE.DRY_RUN_PLAN);
  assert.equal(state.jobId, 7);
  assert.equal(state.summary.wouldDeleteCount, 2);

  state = reduceGcFlow(state, { type: GC_EVENT.REQUEST_EXECUTE });
  assert.equal(state.kind, GC_STATE.CONFIRM_EXECUTE);

  // A dismiss returns to the plan without ever touching the network.
  const dismissed = reduceGcFlow(state, { type: GC_EVENT.DISMISS_CONFIRM });
  assert.equal(dismissed.kind, GC_STATE.DRY_RUN_PLAN);

  // The actual confirm.
  state = reduceGcFlow(state, { type: GC_EVENT.CONFIRM_EXECUTE });
  assert.deepEqual(state, { kind: GC_STATE.REQUESTING_EXECUTE });

  state = reduceGcFlow(state, { type: GC_EVENT.EXECUTE_QUEUED, jobId: 8 });
  assert.deepEqual(state, { kind: GC_STATE.POLLING_EXECUTE, jobId: 8 });

  const executedJob = job(
    8,
    "done",
    "[vault-api] GC totals (EXECUTED): chunks_removed=2 bytes_freed=700 total_bytes_freed=700",
  );
  state = reduceGcFlow(state, { type: GC_EVENT.POLL_RESULT, job: executedJob });
  assert.equal(state.kind, GC_STATE.EXECUTE_DONE);
  assert.equal(state.jobId, 8);
  assert.equal(state.summary.chunksRemoved, 2);
});

// ---- error paths ----------------------------------------------------------

test("a dry-run request that fails to even queue reports executeAttempted = false", () => {
  const state = reduceGcFlow({ kind: GC_STATE.REQUESTING_DRY_RUN }, { type: GC_EVENT.DRY_RUN_FAILED, message: "network error" });
  assert.deepEqual(state, { kind: GC_STATE.ERROR, message: "network error", executeAttempted: false });
});

test("an execute request that fails to even queue reports executeAttempted = true", () => {
  const state = reduceGcFlow({ kind: GC_STATE.REQUESTING_EXECUTE }, { type: GC_EVENT.EXECUTE_FAILED, message: "network error" });
  assert.deepEqual(state, { kind: GC_STATE.ERROR, message: "network error", executeAttempted: true });
});

test("a job that reaches error status while polling the dry run becomes Error(executeAttempted=false)", () => {
  const state = reduceGcFlow({ kind: GC_STATE.POLLING_DRY_RUN, jobId: 1 }, { type: GC_EVENT.POLL_RESULT, job: job(1, "error") });
  assert.deepEqual(state, { kind: GC_STATE.ERROR, message: "GC job 1 failed", executeAttempted: false });
});

test("a job that reaches error status while polling the execute becomes Error(executeAttempted=true)", () => {
  const state = reduceGcFlow({ kind: GC_STATE.POLLING_EXECUTE, jobId: 1 }, { type: GC_EVENT.POLL_RESULT, job: job(1, "error") });
  assert.deepEqual(state, { kind: GC_STATE.ERROR, message: "GC job 1 failed", executeAttempted: true });
});

// ---- cancellation mid-poll ------------------------------------------------

test("a job cancelled by another client mid dry-run poll becomes Cancelled, not Error", () => {
  const state = reduceGcFlow({ kind: GC_STATE.POLLING_DRY_RUN, jobId: 1 }, { type: GC_EVENT.POLL_RESULT, job: job(1, "cancelled") });
  assert.deepEqual(state, { kind: GC_STATE.CANCELLED, jobId: 1 });
});

test("a job cancelled by another client mid execute poll becomes Cancelled, not Error", () => {
  const state = reduceGcFlow({ kind: GC_STATE.POLLING_EXECUTE, jobId: 1 }, { type: GC_EVENT.POLL_RESULT, job: job(1, "cancelled") });
  assert.deepEqual(state, { kind: GC_STATE.CANCELLED, jobId: 1 });
});

test("after Cancelled, start_dry_run works again -- Cancelled is a terminal-but-restartable state", () => {
  const state = reduceGcFlow({ kind: GC_STATE.CANCELLED, jobId: 1 }, { type: GC_EVENT.START_DRY_RUN });
  assert.deepEqual(state, { kind: GC_STATE.REQUESTING_DRY_RUN });
});

// ---- stray/stale poll results never corrupt state --------------------------

test("a poll result for a DIFFERENT job id than the one being tracked is ignored -- dry run", () => {
  const state = { kind: GC_STATE.POLLING_DRY_RUN, jobId: 1 };
  const result = reduceGcFlow(state, { type: GC_EVENT.POLL_RESULT, job: job(999, "done") });
  assert.deepEqual(result, state);
});

test("a poll result for a DIFFERENT job id than the one being tracked is ignored -- execute", () => {
  const state = { kind: GC_STATE.POLLING_EXECUTE, jobId: 7 };
  const result = reduceGcFlow(state, { type: GC_EVENT.POLL_RESULT, job: job(999, "done") });
  assert.deepEqual(result, state);
});

test("a poll result landing outside any polling state is ignored", () => {
  assert.deepEqual(
    reduceGcFlow(idleGcState(), { type: GC_EVENT.POLL_RESULT, job: job(1, "done") }),
    idleGcState(),
  );
});

test("an unrecognized job status keeps polling rather than guessing", () => {
  // "paused" used to be the example here; since WP PAUSE-ALL-GC-1 it is a
  // known status with its own flag (see the PAUSE-ALL-GC-1 tests below).
  assert.deepEqual(
    reduceGcFlow({ kind: GC_STATE.POLLING_DRY_RUN, jobId: 1 }, { type: GC_EVENT.POLL_RESULT, job: job(1, "quarantined") }),
    { kind: GC_STATE.POLLING_DRY_RUN, jobId: 1 },
  );
  assert.deepEqual(
    reduceGcFlow({ kind: GC_STATE.POLLING_EXECUTE, jobId: 1 }, { type: GC_EVENT.POLL_RESULT, job: job(1, "quarantined") }),
    { kind: GC_STATE.POLLING_EXECUTE, jobId: 1 },
  );
});

// ---- reset ------------------------------------------------------------------

test("reset always returns to idle from any state", () => {
  assert.deepEqual(reduceGcFlow({ kind: GC_STATE.POLLING_EXECUTE, jobId: 1 }, { type: GC_EVENT.RESET }), idleGcState());
  assert.deepEqual(
    reduceGcFlow({ kind: GC_STATE.ERROR, message: "x", executeAttempted: true }, { type: GC_EVENT.RESET }),
    idleGcState(),
  );
  assert.deepEqual(reduceGcFlow(idleGcState(), { type: GC_EVENT.RESET }), idleGcState());
});

// ---- start_dry_run gating -----------------------------------------------------

test("start_dry_run is rejected while a flow is already mid-run", () => {
  const midFlight = [
    { kind: GC_STATE.REQUESTING_DRY_RUN },
    { kind: GC_STATE.POLLING_DRY_RUN, jobId: 1 },
    { kind: GC_STATE.DRY_RUN_PLAN, jobId: 1, job: job(1, "done"), summary: null },
    { kind: GC_STATE.CONFIRM_EXECUTE, jobId: 1, job: job(1, "done"), summary: null },
    { kind: GC_STATE.REQUESTING_EXECUTE },
    { kind: GC_STATE.POLLING_EXECUTE, jobId: 1 },
  ];
  for (const state of midFlight) {
    assert.deepEqual(reduceGcFlow(state, { type: GC_EVENT.START_DRY_RUN }), state);
  }
});

test("start_dry_run is accepted from idle, execute_done and error too", () => {
  assert.deepEqual(reduceGcFlow(idleGcState(), { type: GC_EVENT.START_DRY_RUN }), { kind: GC_STATE.REQUESTING_DRY_RUN });
  assert.deepEqual(
    reduceGcFlow({ kind: GC_STATE.EXECUTE_DONE, jobId: 1, job: job(1, "done"), summary: null }, { type: GC_EVENT.START_DRY_RUN }),
    { kind: GC_STATE.REQUESTING_DRY_RUN },
  );
  assert.deepEqual(
    reduceGcFlow({ kind: GC_STATE.ERROR, message: "x", executeAttempted: false }, { type: GC_EVENT.START_DRY_RUN }),
    { kind: GC_STATE.REQUESTING_DRY_RUN },
  );
});

// ---- WP PAUSE-ALL-GC-1: a paused GC job is reachable ----------------------

test("WP PAUSE-ALL-GC-1: a paused poll result keeps polling, flagged paused, and a resume carries on", () => {
  let state = reduceGcFlow({ kind: GC_STATE.POLLING_DRY_RUN, jobId: 7 }, { type: GC_EVENT.POLL_RESULT, job: job(7, "paused") });
  assert.deepEqual(state, { kind: GC_STATE.POLLING_DRY_RUN, jobId: 7, paused: true });
  state = reduceGcFlow(state, { type: GC_EVENT.POLL_RESULT, job: job(7, "queued") });
  assert.deepEqual(state, { kind: GC_STATE.POLLING_DRY_RUN, jobId: 7 }, "resumed: the flag is gone");
  state = reduceGcFlow(state, { type: GC_EVENT.POLL_RESULT, job: job(7, "done", "[vault-api] GC totals (DRY RUN): would_delete=0 (0 bytes)") });
  assert.equal(state.kind, GC_STATE.DRY_RUN_PLAN);

  const exec = reduceGcFlow({ kind: GC_STATE.POLLING_EXECUTE, jobId: 8 }, { type: GC_EVENT.POLL_RESULT, job: job(8, "paused") });
  assert.deepEqual(exec, { kind: GC_STATE.POLLING_EXECUTE, jobId: 8, paused: true });
});

test("WP PAUSE-ALL-GC-1: the paused text, and the sheet shows it in both polling states", async () => {
  const { GC_PAUSED_TEXT } = await import("../js/lib/gc-flow.js");
  assert.equal(GC_PAUSED_TEXT, "GC paused — resume it on the Downloads page.");
  const { readFileSync } = await import("node:fs");
  const sheet = readFileSync(new URL("../js/components/game-detail-sheet.js", import.meta.url), "utf8");
  assert.ok(sheet.includes('gs.paused ? GC_PAUSED_TEXT : "Checking for orphaned chunks…"'));
  assert.ok(sheet.includes('gs.paused ? GC_PAUSED_TEXT : "Deleting orphaned chunks…"'));
});
