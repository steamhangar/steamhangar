/**
 * WP WEB-FEAT-5: pure decisions and wording of Pause all / Resume all
 * (`js/lib/bulk-jobs.js`). Expected strings are LITERALS, never read back
 * from WORDING (docs/LEARNINGS.md, the constants-vs-literals rule).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  BULK_CONCURRENCY,
  bulkPauseTargets,
  bulkResumeTargets,
  bulkBarState,
  runWithLimit,
  runBulkPause,
  runBulkResume,
  bulkSummary,
  isSkip,
  confirmTitle,
  pauseAriaLabel,
  resumeAriaLabel,
  confirmGcLines,
  WORDING,
} from "../js/lib/bulk-jobs.js";

const job = (id, status, extra = {}) => ({ id, appid: 1000 + id, type: "prefill", status, stop_request: null, ...extra });
const ids = (list) => list.map((j) => j.id);
const httpError = (status, detail) => Object.assign(new Error(detail), { status, detail });

// ---------------------------------------------------------------------
// visibility rules / target sets
// ---------------------------------------------------------------------

test("Pause all targets queued + running prefill jobs, queued phase first, oldest id first", () => {
  const jobs = [job(5, "queued"), job(2, "running"), job(9, "queued"), job(1, "done"), job(3, "paused")];
  const t = bulkPauseTargets(jobs);
  assert.deepEqual(ids(t.queued), [5, 9]);
  assert.deepEqual(ids(t.running), [2]);
  assert.equal(t.count, 3);
});

test("WP PAUSE-ALL-GC-1: a queued GC job is a Pause all target in the queued phase; a running one is not", () => {
  const jobs = [
    job(4, "running"),
    job(6, "queued", { type: "gc", gc_execute: true }),
    job(5, "queued"),
    job(2, "running", { type: "gc" }),
  ];
  const t = bulkPauseTargets(jobs);
  assert.deepEqual(ids(t.queued), [5, 6], "queued prefill and queued GC, oldest id first");
  assert.deepEqual(ids(t.running), [4], "the running GC job (409) is never sent");
  assert.equal(t.count, 3);
  assert.equal(t.gcQueued, 1);
  const bar = bulkBarState(jobs);
  assert.equal(bar.pauseCount, 3);
  assert.equal(bar.gcQueued, 1);
  assert.equal(bar.gcRunning, true);
});

test("WP PAUSE-ALL-GC-1: only queued GC jobs (no prefill) still show Pause all", () => {
  const bar = bulkBarState([job(1, "queued", { type: "gc" }), job(2, "queued", { type: "gc" })]);
  assert.equal(bar.pauseVisible, true);
  assert.equal(bar.pauseCount, 2);
  assert.equal(bar.gcQueued, 2);
  assert.equal(bar.gcRunning, false);
});

test("a running GC job alone is no Pause all target and hides the bar", () => {
  const jobs = [job(2, "running", { type: "gc" })];
  assert.equal(bulkPauseTargets(jobs).count, 0);
  const bar = bulkBarState(jobs);
  assert.equal(bar.pauseVisible, false);
  assert.equal(bar.gcRunning, true);
  assert.equal(bar.visible, false);
});

test("WP PAUSE-ALL-GC-1: Resume all includes paused GC jobs, in id order with the prefills", () => {
  const jobs = [job(8, "paused", { type: "gc" }), job(3, "paused"), job(9, "running", { type: "gc" })];
  assert.deepEqual(ids(bulkResumeTargets(jobs)), [3, 8]);
});

test("WP PAUSE-ALL-GC-1: confirm dialog GC lines — held count, running note, nothing without GC", () => {
  assert.deepEqual(confirmGcLines({ gcQueued: 0, gcRunning: false }), []);
  assert.deepEqual(confirmGcLines({ gcQueued: 1, gcRunning: false }), [
    "This includes 1 queued garbage-collection run: it is held too and deletes nothing until you resume it.",
  ]);
  assert.deepEqual(confirmGcLines({ gcQueued: 3, gcRunning: true }), [
    "This includes 3 queued garbage-collection runs: they are held too and delete nothing until you resume them.",
    "The garbage-collection run that already started finishes: a running one cannot be paused (cancel it instead).",
  ]);
  assert.deepEqual(confirmGcLines(bulkBarState([job(1, "running", { type: "gc" }), job(2, "queued")])), [
    "The garbage-collection run that already started finishes: a running one cannot be paused (cancel it instead).",
  ]);
});

test("WP PAUSE-ALL-GC-1 (N7): the toast names GC runs among the ok results, only those", () => {
  const ok = (j) => ({ job: j, outcome: "ok" });
  const gc = (id) => job(id, "queued", { type: "gc" });
  assert.equal(bulkSummary("pause", [ok(job(1, "queued"))]).text, "Paused 1 download");
  assert.equal(bulkSummary("pause", [ok(job(1, "queued")), ok(gc(2))]).text, "Paused 2 downloads (incl. 1 garbage-collection run)");
  assert.equal(
    bulkSummary("resume", [ok(gc(1)), ok(gc(2)), { job: gc(3), outcome: "skipped", reason: "x" }]).text,
    "Resumed 2 of 3 (incl. 2 garbage-collection runs) — 1 skipped (finished or changed meanwhile)",
  );
});

test("the old confirmGcNote literal stays in WORDING for the Android twin pin", () => {
  assert.equal(WORDING.confirmGcNote, "Garbage-collection runs keep going: they cannot be paused.");
});

test("a running job that is already pausing or cancelling is not sent again", () => {
  const jobs = [job(1, "running", { stop_request: "pause" }), job(2, "running", { stop_request: "cancel" })];
  assert.equal(bulkPauseTargets(jobs).count, 0);
  assert.equal(bulkBarState(jobs).pauseVisible, false);
});

test("Resume all targets every paused job, oldest id first", () => {
  const jobs = [job(7, "paused"), job(3, "paused"), job(4, "queued"), job(5, "cancelled")];
  assert.deepEqual(ids(bulkResumeTargets(jobs)), [3, 7]);
});

test("bar state: Pause all with >=1 pausable, Resume all with >=1 paused, both, neither", () => {
  assert.deepEqual(
    bulkBarState([job(1, "running")]),
    { pauseVisible: true, pauseCount: 1, resumeVisible: false, resumeCount: 0, gcRunning: false, gcQueued: 0, visible: true },
  );
  const both = bulkBarState([job(1, "queued"), job(2, "paused"), job(3, "paused")]);
  assert.equal(both.pauseVisible, true);
  assert.equal(both.resumeVisible, true);
  assert.equal(both.resumeCount, 2);
  const none = bulkBarState([job(1, "done"), job(2, "error")]);
  assert.equal(none.visible, false);
  assert.equal(bulkBarState(null).visible, false);
});

// ---------------------------------------------------------------------
// wording
// ---------------------------------------------------------------------

test("confirm title and aria labels name the count, singular and plural", () => {
  assert.equal(confirmTitle(16), "Pause 16 downloads?");
  assert.equal(confirmTitle(1), "Pause 1 download?");
  assert.equal(pauseAriaLabel(16), "Pause all 16 downloads");
  assert.equal(pauseAriaLabel(1), "Pause 1 download");
  assert.equal(resumeAriaLabel(3), "Resume all 3 downloads");
  assert.equal(resumeAriaLabel(1), "Resume 1 download");
});

test("the scheduler note and button labels are the agreed copy", () => {
  assert.equal(WORDING.schedulerNote, "Pausing does not stop the scheduler: its next sweep can still queue new downloads.");
  assert.equal(WORDING.pauseAll, "Pause all");
  assert.equal(WORDING.resumeAll, "Resume all");
  assert.equal(WORDING.confirmNo, "Keep downloading");
});

test("aggregate toast: everything ok", () => {
  const ok = (n) => Array.from({ length: n }, () => ({ outcome: "ok" }));
  assert.deepEqual(bulkSummary("pause", ok(16)), { text: "Paused 16 downloads", warn: false });
  assert.deepEqual(bulkSummary("pause", ok(1)), { text: "Paused 1 download", warn: false });
  assert.deepEqual(bulkSummary("resume", ok(4)), { text: "Resumed 4 downloads", warn: false });
});

test("aggregate toast: partial failure names the count and the first reason, and warns", () => {
  const results = [
    ...Array.from({ length: 15 }, () => ({ outcome: "ok" })),
    { outcome: "failed", reason: "vault-api is unreachable" },
  ];
  assert.deepEqual(bulkSummary("pause", results), {
    text: "Paused 15 of 16 — 1 could not be paused: vault-api is unreachable",
    warn: true,
  });
});

test("aggregate toast: skipped jobs are reported apart from failures and do not warn", () => {
  const results = [{ outcome: "ok" }, { outcome: "skipped", reason: "x" }, { outcome: "skipped", reason: "y" }];
  assert.deepEqual(bulkSummary("pause", results), {
    text: "Paused 1 of 3 — 2 skipped (finished or changed meanwhile)",
    warn: false,
  });
  const mixed = [{ outcome: "ok" }, { outcome: "failed", reason: "boom" }, { outcome: "skipped" }];
  assert.equal(bulkSummary("resume", mixed).text, "Resumed 1 of 3 — 1 could not be resumed: boom; 1 skipped (finished or changed meanwhile)");
});

// ---------------------------------------------------------------------
// execution: concurrency bound, phase order, 404/409 skip
// ---------------------------------------------------------------------

/** A worker whose calls stay pending until released, recording the peak
 * number in flight. */
function gatedWorker() {
  const pending = [];
  const calls = [];
  let inFlight = 0;
  let peak = 0;
  const worker = (id) => {
    calls.push(id);
    inFlight += 1;
    peak = Math.max(peak, inFlight);
    return new Promise((resolve) => {
      pending.push(() => {
        inFlight -= 1;
        resolve({ ok: true });
      });
    });
  };
  const flush = () => new Promise((r) => setTimeout(r, 0));
  return {
    worker,
    calls,
    peak: () => peak,
    inFlight: () => inFlight,
    async releaseAll() {
      while (pending.length || inFlight) {
        while (pending.length) pending.shift()();
        await flush();
      }
    },
    async releaseOne() {
      pending.shift()?.();
      await flush();
    },
    flush,
  };
}

test("at most BULK_CONCURRENCY (4) requests are in flight, and the bound is actually used", async () => {
  assert.equal(BULK_CONCURRENCY, 4);
  const g = gatedWorker();
  const items = Array.from({ length: 16 }, (_, i) => job(i + 1, "paused"));
  const run = runBulkResume(items, g.worker);
  await g.flush();
  assert.equal(g.inFlight(), 4, "exactly four start at once");
  await g.releaseOne();
  assert.equal(g.inFlight(), 4, "a finished request is replaced, never exceeded");
  await g.releaseAll();
  const results = await run;
  assert.equal(results.length, 16);
  assert.equal(g.peak(), 4);
  assert.deepEqual(g.calls.slice().sort((a, b) => a - b), ids(items));
});

test("Pause all sends no running-job pause until every queued pause has settled", async () => {
  const g = gatedWorker();
  const targets = bulkPauseTargets([job(1, "running"), job(2, "queued"), job(3, "queued")]);
  const run = runBulkPause(targets, g.worker);
  await g.flush();
  assert.deepEqual(g.calls, [2, 3]);
  await g.releaseOne();
  assert.deepEqual(g.calls, [2, 3], "job 3 still pending: the running job must wait");
  await g.releaseAll();
  await run;
  assert.deepEqual(g.calls, [2, 3, 1]);
});

test("WP PAUSE-ALL-GC-1: queued GC jobs are paused in the queued phase, before the running prefill", async () => {
  const g = gatedWorker();
  const targets = bulkPauseTargets([
    job(1, "running"),
    job(2, "queued", { type: "gc" }),
    job(3, "queued"),
    job(4, "running", { type: "gc" }),
  ]);
  const run = runBulkPause(targets, g.worker);
  await g.flush();
  assert.deepEqual(g.calls, [2, 3], "queued GC + queued prefill first");
  await g.releaseAll();
  await run;
  assert.deepEqual(g.calls, [2, 3, 1], "the running GC job 4 is never sent");
});

test("404 and 409 count as skipped, anything else as failed with its reason; never rejects", async () => {
  assert.equal(isSkip(httpError(404, "gone")), true);
  assert.equal(isSkip(httpError(409, "finished")), true);
  assert.equal(isSkip(httpError(500, "boom")), false);
  assert.equal(isSkip(new Error("network")), false);

  const failures = { 1: httpError(409, "Job 1 already finished"), 2: httpError(404, "Unknown job id 2"), 3: httpError(503, "Service Unavailable") };
  const results = await runWithLimit([job(1, "paused"), job(2, "paused"), job(3, "paused"), job(4, "paused")], 4, async (j) => {
    if (failures[j.id]) throw failures[j.id];
    return {};
  });
  assert.deepEqual(
    results.map((r) => [r.job.id, r.outcome]),
    [[1, "skipped"], [2, "skipped"], [3, "failed"], [4, "ok"]],
  );
  assert.equal(results[2].reason, "Service Unavailable");
  assert.equal(bulkSummary("resume", results).text, "Resumed 1 of 4 — 1 could not be resumed: Service Unavailable; 2 skipped (finished or changed meanwhile)");
});

test("an empty target list runs nothing and does not hang", async () => {
  const results = await runWithLimit([], 4, () => {
    throw new Error("must not be called");
  });
  assert.deepEqual(results, []);
});
