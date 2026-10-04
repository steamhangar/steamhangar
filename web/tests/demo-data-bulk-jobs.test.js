/**
 * WP WEB-FEAT-5: Pause all / Resume all in demo mode — the same
 * `lib/bulk-jobs.js` run the view performs, against `demoRequest` instead
 * of a server. Demo mode has no worker draining a queue (an enqueued demo
 * job is "running" at once), so every pausable demo job is a running one;
 * a GC job stays unpausable (409 -> skipped), exactly like the real API.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { demoRequest, resetDemoData } from "../js/demo-data.js";
import {
  bulkPauseTargets,
  bulkResumeTargets,
  runBulkPause,
  runBulkResume,
  runWithLimit,
  bulkSummary,
  bulkBarState,
} from "../js/lib/bulk-jobs.js";

beforeEach(() => {
  resetDemoData();
});

const jobs = () => demoRequest("GET", "/v1/jobs", { params: { limit: 50 } });
const pause = (id) => demoRequest("POST", `/v1/jobs/${id}/pause`);
const resume = (id) => demoRequest("POST", `/v1/jobs/${id}/resume`);

test("demo: Pause all pauses every running prefill job, Resume all brings them back", async () => {
  await demoRequest("POST", "/v1/prefill", { body: { appids: [2010040, 2010050] } });
  const before = await jobs();
  const targets = bulkPauseTargets(before);
  assert.ok(targets.count >= 3, `seeded running job + two new ones, got ${targets.count}`);

  const paused = await runBulkPause(targets, pause);
  assert.equal(bulkSummary("pause", paused).text, `Paused ${targets.count} downloads`);
  const after = await jobs();
  assert.equal(bulkBarState(after).pauseVisible, false);
  assert.equal(bulkResumeTargets(after).length, targets.count);

  const resumed = await runBulkResume(bulkResumeTargets(after), resume);
  assert.equal(bulkSummary("resume", resumed).text, `Resumed ${targets.count} downloads`);
  assert.equal(bulkResumeTargets(await jobs()).length, 0);
});

test("demo: a GC job answers 409 to pause, which a bulk run reports as skipped", async () => {
  // Any game with depots can queue a GC job in the demo.
  const games = await demoRequest("GET", "/v1/games");
  const withDepots = games.find((g) => (g.depot_count ?? g.depots?.length ?? 0) > 0) || games[0];
  const gc = await demoRequest("POST", `/v1/cache/${withDepots.appid}/gc`, { body: {} });
  const results = await runWithLimit([{ id: gc.job_id }], 4, (j) => pause(j.id));
  assert.equal(results[0].outcome, "skipped");
});

test("demo: pausing an already paused job is a 409, skipped by the bulk run", async () => {
  const [running] = bulkPauseTargets(await jobs()).running;
  await pause(running.id);
  const results = await runWithLimit([running], 4, (j) => pause(j.id));
  assert.equal(results[0].outcome, "skipped");
});
