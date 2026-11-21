/**
 * WP CAP-503-FIX-1b: the demo's client-priority hold
 * (`resetDemoData({ clientHold: true })`, on by default in a browser demo):
 * job 900002 is paused for a device, GC dry run 900003 waits in the queue, the running
 * 900001 was resumed by choice. While it holds, no new prefill, cached or
 * GC job starts (operator decisions 2026-11-21: Weg A for queued GC, Weg B
 * for Resume); after `DEMO_HOLD_POLLS` (6) job polls it ends by itself, the
 * paused job resumes and the queue drains.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { demoRequest, resetDemoData } from "../js/demo-data.js";

const HOLD_POLLS = 6;
const AURORA = 2010010; // cached, idle: a fresh enqueue target
const jobs = () => demoRequest("GET", "/v1/jobs", { params: { limit: 50 } });
const byId = (list, id) => list.find((j) => j.id === id);
const hold = () => demoRequest("GET", "/v1/client-priority");

beforeEach(() => {
  resetDemoData({ clientHold: true });
});

test("the scenario: holding for the online agent's address, one paused for it, one queued, one resumed by choice", async () => {
  const cp = await hold();
  assert.equal(cp.holding, true);
  assert.equal(cp.restored, false);
  assert.deepEqual(cp.clients.map((c) => c.addr), ["10.10.0.21"]);
  assert.equal(Date.parse(cp.resume_at) - Date.parse(cp.last_client_at), 120_000);
  assert.deepEqual(cp.paused_job_ids, [900002]);
  assert.deepEqual(cp.override_job_ids, [900001]);
  const list = await jobs();
  assert.equal(byId(list, 900002).pause_reason, "client");
  assert.equal(byId(list, 900002).appid, 2010020, "Copper Horizon's first download");
  assert.equal(byId(list, 900003).status, "queued");
  assert.equal(byId(list, 900003).type, "gc", "a queued GC dry run waits too (Weg A)");
  assert.equal(byId(list, 900003).appid, 2010030, "on the game whose download already runs");
  assert.equal(byId(list, 900001).client_override, true);
});

test("MUTATION PIN: during the hold a new prefill, a cached check and a GC job stay queued", async () => {
  const [ref] = await demoRequest("POST", "/v1/prefill", { body: { appids: [AURORA] } });
  assert.equal(ref.status, "queued");
  const gc = await demoRequest("POST", "/v1/cache/2010040/gc", { body: {} });
  assert.equal(gc.status, "queued");
  const cached = await demoRequest("POST", "/v1/prefill/cached");
  assert.ok(cached.every((r) => r.deduplicated || r.status === "queued"), "no cached-check job starts");
  for (let i = 0; i < HOLD_POLLS - 1; i += 1) {
    const list = await jobs();
    assert.equal(byId(list, ref.job_id).status, "queued", `poll ${i + 1}: still waiting`);
    assert.equal(byId(list, gc.job_id).status, "queued", `poll ${i + 1}: GC waits too (Weg A)`);
  }
  assert.equal((await hold()).holding, true);
});

test("MUTATION PIN: the hold ends by itself after 6 job polls; the paused job resumes and the queue drains", async () => {
  const [ref] = await demoRequest("POST", "/v1/prefill", { body: { appids: [AURORA] } });
  let list = [];
  for (let i = 0; i < HOLD_POLLS; i += 1) list = await jobs();
  // The 6th poll ended the hold: its own answer already shows the drain.
  assert.equal(byId(list, 900002).status, "running", "the job paused for the device resumed");
  assert.equal(byId(list, 900002).pause_reason, null);
  assert.equal(byId(list, 900003).status, "running", "the queued GC dry run started");
  assert.equal(byId(list, ref.job_id).status, "running", "the queue drained");
  const cp = await hold();
  assert.equal(cp.holding, false);
  assert.deepEqual(cp.clients, []);
  assert.equal(cp.resume_at, null);
  assert.deepEqual(cp.paused_job_ids, []);
  for (let i = 0; i < 4; i += 1) list = await jobs();
  for (const id of [900002, 900003, ref.job_id]) assert.equal(byId(list, id).status, "done", `job ${id} finished`);
  // After the hold a new job starts at once again.
  const [late] = await demoRequest("POST", "/v1/prefill", { body: { appids: [2010040] } });
  assert.equal(late.status, "running");
});

test("Resume during the hold overrides it for that job; Pause on the job paused for the device makes it manual", async () => {
  const paused = await demoRequest("POST", "/v1/jobs/900002/pause");
  assert.equal(paused.outcome, "immediate");
  assert.equal(byId(await jobs(), 900002).pause_reason, "manual");
  await demoRequest("POST", "/v1/jobs/900002/resume");
  const job = byId(await jobs(), 900002);
  assert.equal(job.status, "running");
  assert.equal(job.client_override, true);
  assert.equal(job.pause_reason, null);
});

test("without the scenario (the bare-Node default) nothing is held and new jobs start at once", async () => {
  resetDemoData({ clientHold: false });
  const cp = await hold();
  assert.equal(cp.holding, false);
  assert.equal(cp.last_client_at, null);
  const list = await jobs();
  assert.equal(byId(list, 900002), undefined);
  assert.equal(byId(list, 900003), undefined);
  assert.equal(byId(list, 900001).client_override, false);
  const [ref] = await demoRequest("POST", "/v1/prefill", { body: { appids: [AURORA] } });
  assert.equal(ref.status, "running");
});
