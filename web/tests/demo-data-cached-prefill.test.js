/**
 * Headless tests for web/js/demo-data.js's POST /v1/prefill/cached route
 * (WP 4c-web) — the demo-mode mirror of the real Phase 4c, WP 4c-api
 * contract (api/README.md "Check & update all cached games").
 *
 * demo-data.js imports only errors.js and two other DOM-free `lib/` modules
 * (no `window`, `document` or `fetch`) so it runs directly in bare Node.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 *
 * **Documented demo-mode drift (review round 1, N1/N2 — pre-existing
 * shapes inherited from WP 4a.2/4c-web's own earlier round, not fixed here
 * per the review's "fix only if it is a two-line change" instruction):**
 *   - N1: a brand-new demo job (`enqueuePrefillForAppid`) flips straight to
 *     `"running"` on creation, so `{deduplicated: false}` never arrives as
 *     `status: "queued"` here the way the real contract allows (a real
 *     freshly-created job is ALWAYS `"queued"` at response time — the
 *     worker claims it asynchronously). The "brand-new job is queued" test
 *     below pins `deduplicated: false` only, deliberately not the status
 *     string, for exactly this reason.
 *   - N2 (CLOSED by WP WEB-FIX-1, S1 — the WP 4f carry-over recorded in
 *     docs/WORKPACKAGES.md "Carry-overs" #1): `selectCachedAppids()` used
 *     to key on `depots.length > 0`, the pre-WP-4f "any mapped depot with
 *     bytes" rule. It now applies the real `deletion.appids_with_cache_
 *     content` rule — a game counts iff it maps at least one depot that is
 *     EXCLUSIVE to it or a LAST-CACHED REMNANT (every other owner uncached
 *     by the ADR-0003 status predicate); a depot some other owner still
 *     holds cached content for does not count. See the S1 tests below for
 *     the scenario that motivated it (a demo delete followed by "Check &
 *     update all cached games" re-queued the deleted game).
 */
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { demoRequest, resetDemoData } from "../js/demo-data.js";

beforeEach(() => {
  resetDemoData();
});

// Seed ids from demo-data.js's buildGames()/buildJobs(), classified by the
// real WP 4f rule (exclusive-or-remnant depot, see the header):
//   2010010 Aurora Cascade    — selected (exclusive depot), job-free
//   2010020 Copper Horizon    — NOT selected (depots: [])
//   2010030 Driftwood Signal  — selected (exclusive depot), RUNNING job 900001
//   2010040 Emberreach        — selected: one exclusive depot + one SHARED
//                               with Frostline
//   2010050 Frostline Convoy  — NOT selected: its ONLY depot is the one it
//                               shares with Emberreach, which holds cached
//                               content, so that depot is `shared` on the
//                               real side, neither exclusive nor remnant.
//                               (Updating Emberreach refreshes that depot
//                               anyway.) Pre-WP-4f the demo selected it.
//   2010070 Glass Meridian    — selected (exclusive depot), needs_force true
const AURORA = 2010010;
const COPPER = 2010020;
const DRIFTWOOD = 2010030;
const EMBERREACH = 2010040;
const FROSTLINE = 2010050;
const GLASS_MERIDIAN = 2010070;
const ALL_CACHED_SORTED = [AURORA, DRIFTWOOD, EMBERREACH, GLASS_MERIDIAN];
const DRIFTWOOD_RUNNING_JOB_ID = 900001;

test("selects every cached app, sorted ascending by appid, excludes the uncached one", async () => {
  const result = await demoRequest("POST", "/v1/prefill/cached");
  assert.deepEqual(result.map((r) => r.appid), ALL_CACHED_SORTED);
  assert.ok(!result.some((r) => r.appid === COPPER), "an app with no depots must never be selected");
});

// ---------------------------------------------------------------------
// WP WEB-FIX-1 (S1): the real WP 4f predicate, exclusive OR remnant.
// ---------------------------------------------------------------------

test("S1: a game whose ONLY depot is shared with a co-owner that holds cached content is NOT selected (DeletionPlan.shared)", async () => {
  const result = await demoRequest("POST", "/v1/prefill/cached");
  assert.ok(!result.some((r) => r.appid === FROSTLINE), "Frostline's one depot is `shared` on the real side — neither exclusive nor remnant");
  assert.ok(result.some((r) => r.appid === EMBERREACH), "Emberreach still has an exclusive depot");
});

test("MUTATION PIN (S1, the WP 4f carry-over scenario): after deleting a game whose shared depot was protected, 'Check & update all cached games' must NOT re-queue it", async () => {
  // DELETE Emberreach: its exclusive depot goes, the depot it shares with
  // Frostline (still cached) is skipped_shared and STAYS in Emberreach's
  // depots array — exactly the state the old `depots.length > 0` filter
  // mis-read as "has cache content".
  const deletion = await demoRequest("DELETE", `/v1/cache/${EMBERREACH}`);
  assert.equal(deletion.skipped_shared.length, 1, "precondition: the shared depot was protected, not deleted");
  const emberreach = await demoRequest("GET", `/v1/games/${EMBERREACH}`);
  assert.ok(emberreach.depots.length > 0, "precondition: the deleted game still maps the protected shared depot");

  const result = await demoRequest("POST", "/v1/prefill/cached");
  assert.ok(
    !result.some((r) => r.appid === EMBERREACH),
    "reverting selectCachedAppids() to `g.depots.length > 0` re-queues the just-deleted game — the re-download-after-delete outcome ADR-0003 exists to prevent",
  );
  // ...and the co-owner is now the LAST CACHED owner of that depot: the
  // depot is a remnant from Frostline's point of view, so Frostline — which
  // the seed state did NOT select — is selected now.
  assert.ok(result.some((r) => r.appid === FROSTLINE), "Frostline's depot became a last-cached remnant, which counts");
});

test("S1: once BOTH co-owners are deleted the shared remnant is freed and neither game is selected any more", async () => {
  // Delete Frostline first: Emberreach still holds content, so the shared
  // depot is skipped and Frostline is left idle/null with that one mapping.
  await demoRequest("DELETE", `/v1/cache/${FROSTLINE}`);
  // Now delete Emberreach's content too — the shared depot becomes a
  // remnant (Frostline is uncached) and is actually removed from both.
  await demoRequest("DELETE", `/v1/cache/${EMBERREACH}`);
  const after = await demoRequest("POST", "/v1/prefill/cached");
  assert.ok(!after.some((r) => r.appid === FROSTLINE) && !after.some((r) => r.appid === EMBERREACH), "nothing left to select for either");
});

test("response shape matches PrefillJobRef exactly, one entry per selected app", async () => {
  const result = await demoRequest("POST", "/v1/prefill/cached");
  for (const ref of result) {
    assert.equal(typeof ref.appid, "number");
    assert.equal(typeof ref.job_id, "number");
    assert.equal(typeof ref.status, "string");
    assert.equal(typeof ref.deduplicated, "boolean");
  }
});

test("a brand-new job is queued (deduplicated: false, status becomes running on the demo's first tick)", async () => {
  const result = await demoRequest("POST", "/v1/prefill/cached");
  const aurora = result.find((r) => r.appid === AURORA);
  // Aurora Cascade has no pre-existing job in the seed data — this call
  // creates one. The demo model (unlike the real API) immediately flips a
  // freshly created job to "running" on its first tick (buildJobs()'
  // documented simplification, same as the existing /v1/prefill tests) —
  // the load-bearing fact pinned here is `deduplicated: false`, not the
  // exact status string.
  assert.equal(aurora.deduplicated, false);
});

test("an app with an already-RUNNING job dedupes onto it (no second job created)", async () => {
  const before = await demoRequest("GET", "/v1/jobs");
  const runningBefore = before.filter((j) => j.status === "running").length;

  const result = await demoRequest("POST", "/v1/prefill/cached");
  const driftwood = result.find((r) => r.appid === DRIFTWOOD);
  assert.equal(driftwood.deduplicated, true);
  assert.equal(driftwood.job_id, DRIFTWOOD_RUNNING_JOB_ID);
  assert.equal(driftwood.status, "running");

  const after = await demoRequest("GET", "/v1/jobs");
  const runningAfter = after.filter((j) => j.status === "running").length;
  // Every OTHER selected app also starts running immediately (demo
  // simplification) — so this only pins that Driftwood's OWN job id did not
  // change (no duplicate stacked on top of it), not the raw running count.
  assert.ok(after.some((j) => j.id === DRIFTWOOD_RUNNING_JOB_ID && j.appid === DRIFTWOOD));
  assert.equal(runningAfter >= runningBefore, true);
});

test("an app with a PAUSED job dedupes onto it with status:'paused' — no new job, nothing 'starts'", async () => {
  const paused = await demoRequest("POST", `/v1/jobs/${DRIFTWOOD_RUNNING_JOB_ID}/pause`);
  assert.equal(paused.status, "paused");

  const result = await demoRequest("POST", "/v1/prefill/cached");
  const driftwood = result.find((r) => r.appid === DRIFTWOOD);
  assert.equal(driftwood.deduplicated, true);
  assert.equal(driftwood.status, "paused");
  assert.equal(driftwood.job_id, DRIFTWOOD_RUNNING_JOB_ID);

  // The job itself must still be paused afterward — this route must never
  // resume/restart it.
  const job = await demoRequest("GET", `/v1/jobs/${DRIFTWOOD_RUNNING_JOB_ID}`);
  assert.equal(job.status, "paused");
});

// S1 (review round 1): a deduplicated job that is STILL "queued" (not yet
// claimed by the single worker) is the common shape a double-press
// produces, not an exotic one — `POST /v1/jobs/{id}/resume` genuinely
// returns a job to `status: "queued"` (api/README.md "Job control":
// "back to queued, keeping its original job id"), and this demo route's
// dedupe hands that status straight through unchanged, same as the real
// route. Reached here via pause -> resume (the demo model has no other way
// to leave a job sitting at "queued" post-creation — see N1 above), which
// is itself a real, reachable sequence, not test-only trickery.
test("an app with a job back at QUEUED (paused, then resumed) dedupes onto it with status:'queued'", async () => {
  await demoRequest("POST", `/v1/jobs/${DRIFTWOOD_RUNNING_JOB_ID}/pause`);
  const resumed = await demoRequest("POST", `/v1/jobs/${DRIFTWOOD_RUNNING_JOB_ID}/resume`);
  assert.equal(resumed.status, "queued");

  const result = await demoRequest("POST", "/v1/prefill/cached");
  const driftwood = result.find((r) => r.appid === DRIFTWOOD);
  assert.equal(driftwood.deduplicated, true);
  assert.equal(driftwood.status, "queued");
  assert.equal(driftwood.job_id, DRIFTWOOD_RUNNING_JOB_ID, "no second job stacked on top");
});

test("any request body is silently ignored — never treated as an explicit appid list", async () => {
  const withoutBody = await demoRequest("POST", "/v1/prefill/cached");
  // Fresh reset needed: the call above already created/mutated job state.
  resetDemoData();
  const withBogusBody = await demoRequest("POST", "/v1/prefill/cached", {
    body: { appids: [999999] },
  });
  assert.deepEqual(withoutBody.map((r) => r.appid), withBogusBody.map((r) => r.appid));
  assert.ok(!withBogusBody.some((r) => r.appid === 999999), "the body's appid must never be enqueued");
});

test("empty selection: no cached apps left ⇒ [] (never an error)", async () => {
  // Unblock Driftwood's active job first (DELETE /v1/cache/{appid} 409s
  // while a job is queued/running/paused for that app).
  await demoRequest("DELETE", `/v1/jobs/${DRIFTWOOD_RUNNING_JOB_ID}`);

  for (const appid of [AURORA, DRIFTWOOD, EMBERREACH, GLASS_MERIDIAN]) {
    await demoRequest("DELETE", `/v1/cache/${appid}`);
  }
  // Emberreach's deletion above skips the depot it shares with Frostline
  // (ADR-0003 — Frostline is still cached); deleting Frostline next frees it
  // as the last cached remnant, same two-call dance demo-data.test.js
  // already pins for DELETE /v1/cache/{appid}.
  await demoRequest("DELETE", `/v1/cache/${FROSTLINE}`);

  const result = await demoRequest("POST", "/v1/prefill/cached");
  assert.deepEqual(result, []);
});

test("no enqueue mechanism of its own: reuses the exact per-appid helper POST /v1/prefill uses", async () => {
  // If /v1/prefill/cached had its own separate enqueue path, a job it
  // creates could drift from one /v1/prefill creates (different default
  // fields, different id sequencing). Assert both routes hand back
  // identical job shapes for a freshly created job.
  const viaCached = await demoRequest("POST", "/v1/prefill/cached");
  const auroraJobId = viaCached.find((r) => r.appid === AURORA).job_id;
  const auroraJob = await demoRequest("GET", `/v1/jobs/${auroraJobId}`);

  resetDemoData();
  const viaPrefill = await demoRequest("POST", "/v1/prefill", { body: { appids: [AURORA] } });
  const auroraJob2 = await demoRequest("GET", `/v1/jobs/${viaPrefill[0].job_id}`);

  assert.deepEqual(Object.keys(auroraJob).sort(), Object.keys(auroraJob2).sort());
  assert.equal(auroraJob.type, auroraJob2.type);
  assert.equal(auroraJob.status, auroraJob2.status);
});
