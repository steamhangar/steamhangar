/**
 * Demo mode's update check (WP WEB-FIX-UPD-VIS): `POST /v1/updates/check`,
 * `GET /v1/updates/status` and the `update_available`/`update_checked_at`
 * fields on the game rows, so the "Check updates" button, its status line,
 * the "Update available" badge and the "Updates" chip all show in demo mode.
 *
 * Twin pin as in demo-data-shape-guard.test.js: the response KEYS must equal
 * the Pydantic models in api/vault_api/routers/updates.py and the two
 * GameSummary fields in games.py, read as plain text. A mismatch is VALUE
 * drift (the server changed: update demo-data.js) or GRAMMAR drift (the
 * field regex misread the model: widen `modelFields`, do not touch
 * demo-data.js).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { demoRequest, resetDemoData, DEMO_UPDATE_CHECK_RUNNING_READS } from "../js/demo-data.js";
import { chipCounts } from "../js/lib/library-filters.js";
import { describeUpdateOutcome, updateStatusLine, UPDATE_PHASE } from "../js/lib/update-check.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const routers = path.join(__dirname, "..", "..", "api", "vault_api", "routers");
const updatesSrc = readFileSync(path.join(routers, "updates.py"), "utf8");
const gamesSrc = readFileSync(path.join(routers, "games.py"), "utf8");

function modelFields(src, name) {
  const m = new RegExp(`class ${name}\\(BaseModel\\):([\\s\\S]*?)(?=\\n\\S)`).exec(src);
  assert.ok(m, `class ${name}(BaseModel) not found — VALUE drift (moved or renamed): re-point this guard`);
  const fields = [...m[1].matchAll(/^ {4}([a-z_][a-z0-9_]*)\s*:\s*\S/gm)].map((x) => x[1]);
  assert.ok(fields.length > 0, `no fields read from ${name} — GRAMMAR drift: widen modelFields`);
  return fields.sort();
}

beforeEach(() => resetDemoData());

const status = () => demoRequest("GET", "/v1/updates/status");
const games = () => demoRequest("GET", "/v1/games");

test("shape: status and start bodies carry exactly the server models' keys", async () => {
  assert.deepEqual(Object.keys(await status()).sort(), modelFields(updatesSrc, "UpdateCheckStatusOut"));
  assert.deepEqual(Object.keys(await demoRequest("POST", "/v1/updates/check")).sort(), modelFields(updatesSrc, "UpdateCheckStartOut"));
  const running = await status();
  assert.deepEqual(Object.keys(running).sort(), modelFields(updatesSrc, "UpdateCheckStatusOut"));
});

test("shape: every game row carries the two UPD-1 GameSummary fields (and the server model still has them)", async () => {
  const fields = modelFields(gamesSrc, "GameSummary");
  for (const f of ["update_available", "update_checked_at"]) assert.ok(fields.includes(f), `${f} missing from GameSummary — VALUE drift`);
  for (const g of await games()) {
    assert.equal(typeof g.update_available, "boolean", `${g.name}: update_available`);
    assert.ok(g.update_checked_at === null || typeof g.update_checked_at === "string");
  }
  const detail = await demoRequest("GET", "/v1/games/2010040");
  assert.equal(detail.update_available, true, "the detail shape carries it too");
});

test("seed: exactly one demo game is flagged (Emberreach), so the badge and the 'Updates' chip show", async () => {
  const rows = await games();
  assert.deepEqual(rows.filter((g) => g.update_available).map((g) => g.name), ["Emberreach"]);
  const updates = chipCounts(rows, { query: "", liveJobsByAppid: new Map() }).find((c) => c.key === "updates");
  assert.ok(updates, "the optional 'Updates' chip appears (rows carry the field)");
  assert.equal(updates.count, 1);
});

test("seed: the status reads as a finished scheduled check, and the line says when", async () => {
  const s = await status();
  assert.equal(s.enabled, true);
  assert.equal(s.scheduled, true, "the demo's oracle is on (WP UPD-5: scheduled == enabled)");
  assert.equal(s.running, false);
  assert.equal(s.last_trigger, "scheduled");
  assert.equal(s.last_stale, 1);
  assert.match(updateStatusLine(UPDATE_PHASE.IDLE, s), /^Updates last checked /);
});

test("check: started, one running read (busy button + 'Checking' line), then finished with a result toast", async () => {
  assert.deepEqual(await demoRequest("POST", "/v1/updates/check"), { status: "started", enabled: true });
  assert.deepEqual(await demoRequest("POST", "/v1/updates/check"), { status: "already_running", enabled: true });
  for (let i = 0; i < DEMO_UPDATE_CHECK_RUNNING_READS; i++) {
    const r = await status();
    assert.equal(r.running, true);
    assert.equal(r.last_outcome, null);
    assert.equal(r.last_stale, null);
  }
  const done = await status();
  assert.equal(done.running, false);
  assert.equal(done.last_trigger, "manual");
  assert.equal(done.last_outcome, "completed");
  assert.equal(done.last_stale, 1);
  assert.match(describeUpdateOutcome(done).message, /1 update available/);
  const stamped = (await games()).filter((g) => g.update_checked_at === done.last_finished_at).map((g) => g.name);
  assert.ok(stamped.includes("Emberreach"), "cached games get the new check time");
  assert.equal(stamped.includes("Copper Horizon"), false, "an uncached game is never asked about");
});

test("a finished prefill of the flagged game clears its flag (the newer build is now cached)", async () => {
  await demoRequest("POST", "/v1/prefill", { body: { appids: [2010040] } });
  for (let i = 0; i < 20; i++) await demoRequest("GET", "/v1/jobs");
  const ember = (await games()).find((g) => g.appid === 2010040);
  assert.equal(ember.update_available, false);
});
