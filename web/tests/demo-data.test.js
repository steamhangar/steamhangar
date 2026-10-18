/**
 * Headless tests for web/js/demo-data.js (WP 4a.2 review blocker B2).
 *
 * demo-data.js imports only errors.js (no `window`, `document` or `fetch`)
 * so it runs directly in bare Node — no fake environment needed, unlike
 * api.js or store.js.
 *
 * Covers the review's B2 finding: the previous DELETE /v1/cache/{appid}
 * handler (a) returned bare id lists instead of the real CacheDeletionOut
 * shape, and (b) deleted a depot that was still shared with a currently
 * CACHED co-owner — the exact thing ADR-0003's shared-depot protection
 * exists to prevent — and billed its bytes as freed.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { demoRequest, resetDemoData } from "../js/demo-data.js";

// Every scenario below mutates the module-level demo dataset (deletes,
// enqueues jobs, ...) — reset before each test so cases can't leak into
// each other via shared singleton state.
beforeEach(() => {
  resetDemoData();
});

// Seed ids from demo-data.js's buildGames(), used throughout:
//   2010040 Emberreach       depots: 2010041 (exclusive), 2010060 (shared)
//   2010050 Frostline Convoy depots: 2010060 (shared, same depot)
const EMBERREACH = 2010040;
const FROSTLINE = 2010050;
const SHARED_DEPOT = 2010060;
const EXCLUSIVE_DEPOT = 2010041;

test("DELETE /v1/cache/{appid} response matches the real CacheDeletionOut shape", async () => {
  const result = await demoRequest("DELETE", `/v1/cache/${EMBERREACH}`);
  assert.equal(typeof result.appid, "number");
  assert.ok(Array.isArray(result.deleted_depots));
  assert.ok(Array.isArray(result.skipped_shared));
  assert.ok(Array.isArray(result.failed));
  assert.equal(typeof result.total_bytes_freed, "number");

  for (const d of result.deleted_depots) {
    assert.equal(typeof d.depotid, "number");
    assert.equal(typeof d.size_bytes_freed, "number");
    assert.ok(Array.isArray(d.shared_with_uncached));
  }
  for (const s of result.skipped_shared) {
    assert.equal(typeof s.depotid, "number");
    assert.ok(Array.isArray(s.shared_with));
  }
  // The previous (buggy) shape returned bare depotid ints in deleted_depots
  // and an always-empty skipped_shared — pin that both are gone.
  assert.ok(
    result.deleted_depots.every((d) => typeof d === "object" && d !== null),
    "deleted_depots must be objects, not bare depotids",
  );
});

test("a depot still cached by another game is SKIPPED, not deleted (ADR-0003)", async () => {
  // Frostline Convoy is still 'done' (cached) and maps SHARED_DEPOT, so
  // deleting Emberreach must protect it.
  const result = await demoRequest("DELETE", `/v1/cache/${EMBERREACH}`);

  const deletedIds = result.deleted_depots.map((d) => d.depotid);
  const skippedIds = result.skipped_shared.map((s) => s.depotid);

  assert.deepEqual(deletedIds, [EXCLUSIVE_DEPOT], "only the exclusive depot should be deleted");
  assert.deepEqual(skippedIds, [SHARED_DEPOT], "the shared, still-cached depot must be skipped");

  const skipped = result.skipped_shared.find((s) => s.depotid === SHARED_DEPOT);
  assert.deepEqual(skipped.shared_with, [FROSTLINE]);
  // API parity (WP DELETE-CLAIMANT-1): every kept depot carries its reason.
  assert.equal(skipped.reason, "co_owner_has_content");
  assert.deepEqual(skipped.recorded_only, []);

  // Bytes: only the exclusive depot's 800_000_000 is freed — NOT the
  // shared depot's 300_000_000 (the bug billed both as freed).
  assert.equal(result.total_bytes_freed, 800_000_000);

  // The protected depot must still be reported by the co-owner afterward —
  // its bytes were never actually touched.
  const frostline = await demoRequest("GET", `/v1/games/${FROSTLINE}`);
  assert.equal(frostline.depots.length, 1);
  assert.equal(frostline.depots[0].depotid, SHARED_DEPOT);
  assert.equal(frostline.depots[0].size_bytes, 300_000_000);
});

test("deleting BOTH co-owners frees the shared depot on the second call (last cached remnant)", async () => {
  const first = await demoRequest("DELETE", `/v1/cache/${EMBERREACH}`);
  assert.deepEqual(
    first.skipped_shared.map((s) => s.depotid),
    [SHARED_DEPOT],
  );

  // Now Emberreach itself has no cache content left EXCEPT its (kept)
  // mapping to the shared depot — deleting Frostline next must find no
  // remaining CACHED co-owner and actually free it.
  const second = await demoRequest("DELETE", `/v1/cache/${FROSTLINE}`);
  assert.deepEqual(second.skipped_shared, []);
  assert.equal(second.deleted_depots.length, 1);
  assert.equal(second.deleted_depots[0].depotid, SHARED_DEPOT);
  assert.equal(second.deleted_depots[0].size_bytes_freed, 300_000_000);
  // Emberreach was still mapping it (uncached) at the moment it was freed.
  assert.deepEqual(second.deleted_depots[0].shared_with_uncached, [EMBERREACH]);
  assert.equal(second.total_bytes_freed, 300_000_000);
});

test("404 for an unknown appid or an app with no depot mappings", async () => {
  await assert.rejects(() => demoRequest("DELETE", "/v1/cache/999999"), (err) => {
    assert.equal(err.kind, "not_found");
    assert.equal(err.status, 404);
    return true;
  });
});

test("409 while a job is active for the app", async () => {
  // 2010030 (Driftwood Signal) has a running job in the seed data.
  await assert.rejects(() => demoRequest("DELETE", "/v1/cache/2010030"), (err) => {
    assert.equal(err.status, 409);
    return true;
  });
});

test("POST /v1/prefill validates ALL appids before creating any job (all-or-nothing)", async () => {
  await assert.rejects(
    () => demoRequest("POST", "/v1/prefill", { body: { appids: [2010020, -1] } }),
    (err) => {
      assert.equal(err.status, 422);
      return true;
    },
  );
  // The valid id (2010020, Copper Horizon, previously idle/never-run) must
  // NOT have gotten a job queued before the invalid id was hit.
  const jobsList = await demoRequest("GET", "/v1/jobs", { params: { limit: 50 } });
  assert.ok(
    !jobsList.some((j) => j.appid === 2010020),
    "a 422 must not leave a partial job behind for an earlier, valid appid",
  );
});

test("DELETE /v1/jobs/{id} settles a running job to 'cancelled' and it stays cancelled", async () => {
  // 2010030's seeded job (id 900001) starts 'running'.
  const cancelled = await demoRequest("DELETE", "/v1/jobs/900001");
  assert.equal(cancelled.status, "cancelled");

  // Poll GET /v1/jobs (which ticks the demo simulation forward) several
  // times — a cancelled job must NEVER progress to 'done'.
  for (let i = 0; i < 5; i++) {
    const jobsList = await demoRequest("GET", "/v1/jobs", { params: { limit: 50 } });
    const job = jobsList.find((j) => j.id === 900001);
    assert.equal(job.status, "cancelled", `expected still cancelled on poll ${i}`);
  }
});

// ---------------------------------------------------------------------
// GET /v1/mapping (WP 4a.3) — the full depot->app table the Library
// view's bulk-delete confirm dialog needs to compute multiplan.js's
// set-aware arithmetic in demo mode too (web/js/api.js "mapping" wrapper).
// ---------------------------------------------------------------------

test("GET /v1/mapping returns one {depotid, appid} row per mapped depot, matching the shared seed data", async () => {
  const rows = await demoRequest("GET", "/v1/mapping");
  assert.ok(Array.isArray(rows));
  for (const row of rows) {
    assert.equal(typeof row.depotid, "number");
    assert.equal(typeof row.appid, "number");
  }
  // Emberreach and Frostline Convoy both map SHARED_DEPOT (see the seed
  // data comment at the top of this file) — the mapping table must show
  // BOTH owner rows for it, the exact fact multiplan.js needs and
  // `GET /v1/games/{appid}`'s boolean `shared` flag alone cannot supply.
  const owners = rows.filter((r) => r.depotid === SHARED_DEPOT).map((r) => r.appid);
  assert.deepEqual(owners.sort(), [EMBERREACH, FROSTLINE].sort());
});

test("GET /v1/mapping reflects a deletion: a freed depot's row disappears", async () => {
  await demoRequest("DELETE", `/v1/cache/${EMBERREACH}`); // frees EXCLUSIVE_DEPOT only
  const rows = await demoRequest("GET", "/v1/mapping");
  assert.ok(!rows.some((r) => r.depotid === EXCLUSIVE_DEPOT));
  // The still-protected shared depot's mapping survives for both games.
  assert.equal(rows.filter((r) => r.depotid === SHARED_DEPOT).length, 2);
});
