/**
 * WP CACHE-REPAIR-UI-1 — `lib/cache-repair.js`: who gets "Repair" (the
 * visibility rule), the flow reducer (THE pin: execute only after a confirm
 * that followed a dry run), the error wording and every user-visible
 * string (the Android twin copies them). Plus the demo route
 * (`POST /v1/cache/{appid}/repair` in demo-data.js) and twin checks against
 * the api router the wording relies on.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  showsRepair,
  repairNoteFor,
  reduceRepairFlow,
  idleRepairState,
  describeRepairError,
  repairAriaLabel,
  repairConfirmTitle,
  repairBlockedTitle,
  repairPlanText,
  repairDepotLine,
  unreadableDepotsText,
  repairBlockedText,
  repairDoneText,
  repairSizeText,
  fileCount,
  REPAIR_STATE,
  REPAIR_EVENT,
  REPAIR_LABEL,
  REPAIR_CHECKING_LABEL,
  REPAIR_HINT,
  REPAIR_NOTE,
  REPAIR_NOT_OWNED_NOTE,
  REPAIR_CONFIRM_LABEL,
  REPAIR_EXECUTING_LABEL,
  REPAIR_CANCEL_LABEL,
  REPAIR_CLOSE_LABEL,
  REPAIR_SHOW_JOB_LABEL,
  REPAIR_DONE_TITLE,
  REPAIR_NOOP_TITLE,
  REPAIR_NOTHING_TEXT,
  REPAIR_NOOP_TEXT,
  REPAIR_BUSY_TEXT,
  REPAIR_NOT_OWNED_TEXT,
  REPAIR_NETWORK_TEXT,
  REPAIR_EXECUTE_UNKNOWN_TEXT,
  REPAIR_FAILED_TEXT,
} from "../js/lib/cache-repair.js";
import { ApiError, ERROR_KINDS } from "../js/errors.js";

const MB = 1_048_576;
const GB = 1_073_741_824;
const row = (over = {}) => ({
  appid: 40,
  name: "Broken Bits",
  status: "done",
  size_bytes: 3 * GB,
  cache_complete_pct: 99,
  cache_missing_chunks: 0,
  ...over,
});
const job = (appid, status, type = "prefill") => ({ id: 9, appid, type, status });

// ---------------------------------------------------------------------
// Visibility
// ---------------------------------------------------------------------

test("MUTATION PIN: showsRepair — only a known figure below 100 with no missing chunk file", () => {
  assert.equal(showsRepair(row()), true, "damaged-only");
  assert.equal(showsRepair(row({ cache_missing_chunks: 3 })), false, "missing chunks: Verify, not Repair");
  assert.equal(showsRepair(row({ cache_complete_pct: 100 })), false, "complete");
  assert.equal(showsRepair(row({ cache_complete_pct: null, cache_missing_chunks: null })), false, "unknown figure");
  assert.equal(showsRepair(row({ cache_missing_chunks: null })), false, "missing count unknown");
  assert.equal(showsRepair(null), false);
  assert.equal(showsRepair(undefined), false);
});

test("MUTATION PIN: showsRepair — no tool app, no known-not-owned game, no active prefill of its own", () => {
  assert.equal(showsRepair(row({ tool_app: true })), false, "tool app");
  assert.equal(showsRepair(row({ status: "not_owned" })), false, "not owned (status)");
  assert.equal(showsRepair(row({ not_owned: true })), false, "not owned (flag)");
  for (const status of ["queued", "running", "paused"]) {
    assert.equal(showsRepair(row(), [job(40, status)]), false, `own ${status} prefill`);
  }
  for (const status of ["done", "error", "cancelled"]) {
    assert.equal(showsRepair(row(), [job(40, status)]), true, `own ${status} job is history`);
  }
  assert.equal(showsRepair(row(), [job(41, "running")]), true, "another app's job");
  assert.equal(showsRepair(row(), [job(40, "running", "gc")]), true, "a GC job is not a prefill");
  assert.equal(showsRepair(row(), null), true);
});

test("repairNoteFor: the repair note, the not-owned reason, or nothing", () => {
  assert.equal(repairNoteFor(row()), REPAIR_NOTE);
  assert.equal(repairNoteFor(row({ status: "not_owned" })), REPAIR_NOT_OWNED_NOTE);
  assert.equal(repairNoteFor(row({ cache_missing_chunks: 2 })), "");
  assert.equal(repairNoteFor(row({ cache_complete_pct: 100 })), "");
  assert.equal(repairNoteFor(row({ tool_app: true })), "");
  assert.equal(repairNoteFor(null), "");
});

// ---------------------------------------------------------------------
// Flow
// ---------------------------------------------------------------------

const plan = (over = {}) => ({
  appid: 40,
  mode: "dry-run",
  execute: false,
  damaged_chunks: 3,
  damaged_bytes: 3 * MB,
  depots: [{ depotid: 401, manifestid: "1", damaged_chunks: 3, damaged_bytes: 3 * MB }],
  unreadable_depots: [],
  exceeds_cap: false,
  max_files: 2000,
  ...over,
});
// Every answer event carries the token of the flow that sent the request.
const ev = (type, extra = {}) => ({ type, token: 1, ...extra });
function stateOf(kind) {
  const base = { appid: 40, title: "Broken Bits", token: 1, report: plan() };
  if (kind === REPAIR_STATE.IDLE) return idleRepairState();
  if (kind === REPAIR_STATE.CHECKING) return { kind, appid: 40, title: "Broken Bits", token: 1 };
  return { kind, ...base, error: null };
}

test("flow: dry run -> confirm -> execute -> done", () => {
  let s = idleRepairState();
  s = reduceRepairFlow(s, ev(REPAIR_EVENT.START, { appid: 40, title: "Broken Bits" }));
  assert.deepEqual(s, { kind: REPAIR_STATE.CHECKING, appid: 40, title: "Broken Bits", token: 1 });
  s = reduceRepairFlow(s, ev(REPAIR_EVENT.DRY_RUN_OK, { report: plan() }));
  assert.equal(s.kind, REPAIR_STATE.CONFIRM);
  assert.equal(s.report.damaged_chunks, 3);
  s = reduceRepairFlow(s, ev(REPAIR_EVENT.CONFIRM));
  assert.equal(s.kind, REPAIR_STATE.EXECUTING);
  const done = { ...plan({ mode: "execute", execute: true }), removed_chunks: 3, removed_bytes: 3 * MB, job_id: 77 };
  s = reduceRepairFlow(s, ev(REPAIR_EVENT.EXECUTE_OK, { report: done }));
  assert.equal(s.kind, REPAIR_STATE.DONE);
  assert.equal(s.report.job_id, 77);
  assert.equal(reduceRepairFlow(s, ev(REPAIR_EVENT.CLOSE)).kind, REPAIR_STATE.IDLE);
});

test("THE PIN: CONFIRM reaches EXECUTING only from CONFIRM; every other state ignores it", () => {
  for (const kind of Object.values(REPAIR_STATE)) {
    const s = stateOf(kind);
    const next = reduceRepairFlow(s, ev(REPAIR_EVENT.CONFIRM));
    if (kind === REPAIR_STATE.CONFIRM) assert.equal(next.kind, REPAIR_STATE.EXECUTING);
    else assert.equal(next, s, `CONFIRM from ${kind} must be a no-op`);
  }
});

test("flow: a dry run with nothing damaged ends the flow; over the cap it is BLOCKED, never CONFIRM", () => {
  const checking = stateOf(REPAIR_STATE.CHECKING);
  assert.equal(reduceRepairFlow(checking, ev(REPAIR_EVENT.DRY_RUN_OK, { report: plan({ damaged_chunks: 0 }) })).kind, REPAIR_STATE.IDLE);
  const blocked = reduceRepairFlow(checking, ev(REPAIR_EVENT.DRY_RUN_OK, { report: plan({ damaged_chunks: 2500, exceeds_cap: true }) }));
  assert.equal(blocked.kind, REPAIR_STATE.BLOCKED);
  assert.equal(reduceRepairFlow(blocked, ev(REPAIR_EVENT.CONFIRM)), blocked, "no execute from BLOCKED");
  assert.equal(reduceRepairFlow(checking, ev(REPAIR_EVENT.DRY_RUN_FAILED)).kind, REPAIR_STATE.IDLE);
});

test("flow: a failed execute returns to CONFIRM with the message; CLOSE is ignored while executing", () => {
  const executing = stateOf(REPAIR_STATE.EXECUTING);
  assert.equal(reduceRepairFlow(executing, ev(REPAIR_EVENT.CLOSE)), executing);
  const back = reduceRepairFlow(executing, ev(REPAIR_EVENT.EXECUTE_FAILED, { message: "nope" }));
  assert.equal(back.kind, REPAIR_STATE.CONFIRM);
  assert.equal(back.error, "nope");
  assert.equal(back.report.damaged_chunks, 3, "the dry run's counts stay on screen");
  assert.equal(reduceRepairFlow(stateOf(REPAIR_STATE.CONFIRM), ev(REPAIR_EVENT.CLOSE)).kind, REPAIR_STATE.IDLE);
  assert.equal(reduceRepairFlow(executing, ev(REPAIR_EVENT.RESET)).kind, REPAIR_STATE.IDLE);
});

test("flow: START only from IDLE; stale answers are ignored", () => {
  const confirm = stateOf(REPAIR_STATE.CONFIRM);
  assert.equal(reduceRepairFlow(confirm, ev(REPAIR_EVENT.START, { appid: 1, title: "x" })), confirm);
  const idle = idleRepairState();
  assert.equal(reduceRepairFlow(idle, ev(REPAIR_EVENT.DRY_RUN_OK, { report: plan() })), idle);
  assert.equal(reduceRepairFlow(idle, ev(REPAIR_EVENT.EXECUTE_OK, { report: plan() })), idle);
  assert.equal(reduceRepairFlow(idle, ev(REPAIR_EVENT.EXECUTE_FAILED, { message: "x" })), idle);
});

test("MUTATION PIN: an answer of another flow (other token) never changes the state", () => {
  const late = { token: 2 };
  const checking = stateOf(REPAIR_STATE.CHECKING);
  assert.equal(reduceRepairFlow(checking, ev(REPAIR_EVENT.DRY_RUN_OK, { ...late, report: plan() })), checking);
  assert.equal(reduceRepairFlow(checking, ev(REPAIR_EVENT.DRY_RUN_FAILED, late)), checking);
  const executing = stateOf(REPAIR_STATE.EXECUTING);
  assert.equal(reduceRepairFlow(executing, ev(REPAIR_EVENT.EXECUTE_OK, { ...late, report: plan() })), executing);
  assert.equal(reduceRepairFlow(executing, ev(REPAIR_EVENT.EXECUTE_FAILED, { ...late, message: "x" })), executing);
  const noToken = { type: REPAIR_EVENT.DRY_RUN_OK, report: plan() };
  assert.equal(reduceRepairFlow(checking, noToken), checking, "an answer without a token is not trusted");
  // The token survives every step, so the execute answer can be matched.
  const confirm = reduceRepairFlow(checking, ev(REPAIR_EVENT.DRY_RUN_OK, { report: plan() }));
  assert.equal(confirm.token, 1);
  assert.equal(reduceRepairFlow(confirm, ev(REPAIR_EVENT.CONFIRM)).token, 1);
});

// ---------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------

const http = (status, kind, detail) => new ApiError(kind, `POST failed (${status})`, { status, detail });

test("MUTATION PIN: describeRepairError — honest per status and per request", () => {
  const net = new ApiError(ERROR_KINDS.NETWORK, "Network request failed");
  assert.deepEqual(describeRepairError(net, { execute: false }), { message: REPAIR_NETWORK_TEXT, refresh: false });
  assert.deepEqual(describeRepairError(net, { execute: true }), { message: REPAIR_EXECUTE_UNKNOWN_TEXT, refresh: true });

  const busy = http(409, ERROR_KINDS.VALIDATION, "Job 5 for app 40 is running; repairing chunk files under an active job would race it.");
  assert.deepEqual(describeRepairError(busy, { execute: false }), { message: REPAIR_BUSY_TEXT, refresh: true });
  const notOwned = http(409, ERROR_KINDS.VALIDATION, "The last prefill of this app ended not_owned, so a forced run could not store fresh copies; nothing is deleted.");
  assert.deepEqual(describeRepairError(notOwned, { execute: true }), { message: REPAIR_NOT_OWNED_TEXT, refresh: true });

  const cap = http(422, ERROR_KINDS.VALIDATION, "2500 damaged chunk files exceed the repair cap of 2000; nothing was deleted.");
  assert.deepEqual(describeRepairError(cap, { execute: true }), { message: cap.detail, refresh: false });
  const unknown = http(404, ERROR_KINDS.NOT_FOUND, "App 40 has no depot mappings, so there is nothing to repair.");
  assert.deepEqual(describeRepairError(unknown, { execute: false }), { message: unknown.detail, refresh: false });

  const guard = http(500, ERROR_KINDS.SERVER, "cache root is not a directory");
  assert.deepEqual(describeRepairError(guard, { execute: false }), { message: guard.detail, refresh: false });
  assert.deepEqual(describeRepairError(guard, { execute: true }), { message: REPAIR_EXECUTE_UNKNOWN_TEXT, refresh: true });

  assert.deepEqual(describeRepairError(undefined, { execute: false }), { message: REPAIR_FAILED_TEXT, refresh: false });
});

test("twin: the api router still names the two 409 reasons the wording tells apart", () => {
  const src = readFileSync(new URL("../../api/vault_api/routers/cache.py", import.meta.url), "utf8");
  const route = src.slice(src.indexOf('@router.post("/v1/cache/{appid}/repair"'));
  assert.ok(route.length > 0, "repair route moved?");
  assert.match(route, /ended not_owned/, "the not_owned 409 detail (REPAIR_NOT_OWNED_TEXT)");
  assert.match(route, /is \{active\['status'\]\}; /, "the active-job 409 detail (REPAIR_BUSY_TEXT)");
  assert.match(route, /execute: StrictBool = False|RepairRequest/, "dry run by default");
});

// ---------------------------------------------------------------------
// Wording (the Android twin copies these)
// ---------------------------------------------------------------------

test("wording: labels, notes and texts", () => {
  assert.equal(REPAIR_LABEL, "Repair");
  assert.equal(REPAIR_CHECKING_LABEL, "Checking…");
  assert.equal(
    REPAIR_HINT,
    "Deletes the chunk files that have the wrong size, then downloads fresh copies. You confirm before anything is deleted.",
  );
  assert.equal(REPAIR_NOTE, "Damaged chunks — Verify can't fix these, Repair can.");
  assert.equal(REPAIR_NOT_OWNED_NOTE, "Damaged chunks — can't be repaired: this Steam account doesn't own the game.");
  assert.equal(REPAIR_CONFIRM_LABEL, "Delete and repair");
  assert.equal(REPAIR_EXECUTING_LABEL, "Repairing…");
  assert.equal(REPAIR_CANCEL_LABEL, "Cancel");
  assert.equal(REPAIR_CLOSE_LABEL, "Close");
  assert.equal(REPAIR_SHOW_JOB_LABEL, "Show job");
  assert.equal(REPAIR_DONE_TITLE, "Repair started");
  assert.equal(REPAIR_NOOP_TITLE, "Nothing was deleted");
  assert.equal(REPAIR_NOTHING_TEXT, "Nothing to repair — no chunk file with the wrong size was found.");
  assert.match(REPAIR_NOOP_TEXT, /nothing was deleted and no download was queued/);
  assert.match(REPAIR_BUSY_TEXT, /nothing was deleted/);
  assert.match(REPAIR_NOT_OWNED_TEXT, /nothing was deleted/);
  assert.match(REPAIR_EXECUTE_UNKNOWN_TEXT, /some files may already be deleted/);
});

test("wording: templates", () => {
  assert.equal(repairAriaLabel("Broken Bits"), "Repair — Broken Bits");
  assert.equal(repairConfirmTitle("Broken Bits"), "Repair Broken Bits?");
  assert.equal(repairBlockedTitle("Broken Bits"), "Can't repair Broken Bits");
  assert.equal(fileCount(1), "1 file");
  assert.equal(fileCount(12), "12 files");
  assert.equal(repairSizeText(0), "0 MB");
  assert.equal(repairSizeText(null), "0 MB");
  assert.equal(repairSizeText(14_000_000), "13.4 MB", "a few MB never reads 0.0 GB");
  assert.equal(repairSizeText(3 * GB), "3.0 GB");
  assert.equal(
    repairPlanText(plan()),
    "3 files (3.0 MB) in the cache have the wrong size. Repair deletes them, then queues a forced download that fetches fresh copies.",
  );
  assert.equal(repairDepotLine(plan().depots[0]), "Depot 401 · 3 files · 3.0 MB");
  assert.equal(unreadableDepotsText(plan()), "");
  assert.equal(unreadableDepotsText(plan({ unreadable_depots: [7] })), "Depot 7 could not be read and is left out.");
  assert.equal(unreadableDepotsText(plan({ unreadable_depots: [7, 8] })), "Depots 7, 8 could not be read and are left out.");
  assert.equal(
    repairBlockedText(plan({ damaged_chunks: 2500 })),
    "2500 files have the wrong size — more than the repair limit of 2000. That many mismatches point at a wrong target manifest or a broken cache. Nothing was deleted.",
  );
  const done = { removed_chunks: 3, removed_bytes: 3 * MB, skipped: {}, job_id: 77, job_deduplicated: false };
  assert.equal(repairDoneText(done), "Deleted 3 files (3.0 MB). Job #77 fetches fresh copies.");
  assert.equal(
    repairDoneText({ ...done, skipped: { size_matches: 1, symlink: 0 }, job_deduplicated: true }),
    "Deleted 3 files (3.0 MB). 1 file changed meanwhile and stayed. The already queued job #77 fetches fresh copies.",
  );
});

// ---------------------------------------------------------------------
// Demo route
// ---------------------------------------------------------------------

test("demo: POST /v1/cache/{appid}/repair — dry run counts, 409 under an active job, execute queues the forced run", async () => {
  const { demoRequest, resetDemoData } = await import("../js/demo-data.js");
  resetDemoData();
  const games = await demoRequest("GET", "/v1/games", {});
  const damaged = games.find((g) => g.cache_complete_pct !== null && g.cache_complete_pct < 100 && g.cache_missing_chunks === 0);
  assert.ok(damaged, "the demo has a damaged-only game");
  assert.equal(showsRepair(damaged, await demoRequest("GET", "/v1/jobs", {})), true);

  const dry = await demoRequest("POST", `/v1/cache/${damaged.appid}/repair`, {});
  assert.equal(dry.mode, "dry-run");
  assert.ok(dry.damaged_chunks > 0);
  assert.equal(dry.depots.length, 1);
  assert.equal(dry.job_id, null);

  await assert.rejects(
    demoRequest("POST", `/v1/cache/${damaged.appid}/repair`, { body: { execute: "yes" } }),
    (e) => e.status === 422,
  );

  const done = await demoRequest("POST", `/v1/cache/${damaged.appid}/repair`, { body: { execute: true } });
  assert.equal(done.mode, "execute");
  assert.equal(done.removed_chunks, dry.damaged_chunks);
  assert.ok(Number.isInteger(done.job_id));
  const after = (await demoRequest("GET", "/v1/games", {})).find((g) => g.appid === damaged.appid);
  assert.ok(after.cache_missing_chunks > 0, "the deleted files now count as missing");
  assert.equal(after.needs_force, true);

  // Missing chunks now, not wrong-size ones: nothing left to repair.
  assert.equal((await demoRequest("POST", `/v1/cache/${damaged.appid}/repair`, {})).damaged_chunks, 0);

  // Under an active prefill of the game: 409, also for the dry run.
  resetDemoData();
  await demoRequest("POST", "/v1/prefill", { body: { appids: [damaged.appid] } });
  await assert.rejects(demoRequest("POST", `/v1/cache/${damaged.appid}/repair`, {}), (e) => e.status === 409);
  resetDemoData();
});
