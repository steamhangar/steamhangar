/**
 * Shape-drift guards for demo mode's traffic routes (WP D15c):
 * `GET /v1/throughput`, `/v1/throughput/series`, `/v1/clients/overview`
 * (`web/js/demo-traffic.js`), and the `GET /v1/jobs?range=` filter.
 *
 * Same twin-pin style as demo-data-shape-guard.test.js: the server's
 * Pydantic models in `api/vault_api/routers/traffic.py` are read as text
 * and the demo's KEYS must EQUAL each model's fields. Messages name the
 * edit: VALUE drift (the server model changed: update demo-traffic.js) or
 * GRAMMAR drift (the field regex misread the model: widen `modelFields`).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { demoRequest, resetDemoData } from "../js/demo-data.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const trafficSrc = readFileSync(path.join(__dirname, "..", "..", "api", "vault_api", "routers", "traffic.py"), "utf8");

function modelFields(name) {
  const m = new RegExp(`class ${name}\\(BaseModel\\):([\\s\\S]*?)(?=\\n\\S)`).exec(trafficSrc);
  assert.ok(m, `class ${name}(BaseModel) not found — VALUE drift (moved or renamed): re-point this guard`);
  const fields = [...m[1].matchAll(/^ {4}([a-z_][a-z0-9_]*)\s*:\s*\S/gm)].map((x) => x[1]);
  assert.ok(fields.length > 0, `no fields read from ${name} — GRAMMAR drift: widen modelFields`);
  return fields.sort();
}
function assertKeys(obj, model, what) {
  const read = modelFields(model);
  assert.deepEqual(
    Object.keys(obj).sort(),
    read,
    `${what}: keys differ from ${model} (read: [${read.join(", ")}]). VALUE drift: update web/js/demo-traffic.js; GRAMMAR drift if that list is wrong: widen modelFields.`,
  );
}

beforeEach(() => resetDemoData());

test("sanity: the reader sees ThroughputOut's fields (not vacuous)", () => {
  const f = modelFields("ThroughputOut");
  for (const k of ["event_feed", "current", "summary", "jobs", "active_clients_now"]) assert.ok(f.includes(k), k);
});

test("MUTATION TARGET: demo /v1/throughput has exactly the server's keys, nested models too", async () => {
  for (const range of ["24h", "7d", "30d", "all"]) {
    const t = await demoRequest("GET", "/v1/throughput", { params: { range } });
    assert.equal(t.range, range, "the range is echoed (the view drops answers for another range)");
    assertKeys(t, "ThroughputOut", `throughput ${range}`);
    assertKeys(t.event_feed, "EventFeedOut", "event_feed");
    assertKeys(t.current, "CurrentOut", "current");
    assertKeys(t.summary, "SummaryOut", "summary");
    assert.ok(t.jobs.length >= 1, "the seeded running job is listed");
    for (const j of t.jobs) assertKeys(j, "JobThroughputOut", "jobs[]");
  }
});

test("MUTATION TARGET: demo /v1/throughput/series: keys, point count per range, oldest first", async () => {
  const counts = { "24h": 288, "7d": 168, "30d": 720 };
  for (const [range, n] of Object.entries(counts)) {
    const s = await demoRequest("GET", "/v1/throughput/series", { params: { range } });
    assertKeys(s, "SeriesOut", `series ${range}`);
    assert.equal(s.points.length, n);
    assertKeys(s.points[0], "SeriesPointOut", "points[]");
    assert.ok(s.points[0].t < s.points[n - 1].t, "oldest first");
  }
});

test("MUTATION TARGET: demo /v1/clients/overview: keys, agents and agentless addresses, connected first", async () => {
  const o = await demoRequest("GET", "/v1/clients/overview", { params: { range: "7d" } });
  assertKeys(o, "ClientsOverviewOut", "overview");
  for (const c of o.clients) {
    assertKeys(c, "ClientActivityOut", `client ${c.key}`);
    if (c.stats) assertKeys(c.stats, "ClientStatsOut", "stats");
    if (c.loading_now) assertKeys(c.loading_now, "LoadingNowOut", "loading_now");
    for (const a of c.recent_apps || []) assertKeys(a, "RecentAppOut", "recent_apps[]");
    if (c.kind === "address") {
      assert.equal(c.installed_app_count, null, "no installed games for an address");
      assert.equal(c.client_id, null);
    }
  }
  assert.ok(o.clients.some((c) => c.kind === "agent") && o.clients.some((c) => c.kind === "address"));
  const firstOff = o.clients.findIndex((c) => c.connected !== true);
  assert.ok(o.clients.slice(firstOff).every((c) => c.connected !== true), "connected first");
  assert.equal((await demoRequest("GET", "/v1/clients/overview")).range, "7d", "default range 7d like the server");
});

test("demo traffic routes refuse an unknown range like the server (422)", async () => {
  await assert.rejects(demoRequest("GET", "/v1/throughput", { params: { range: "1y" } }), (e) => e.kind === "validation");
  await assert.rejects(demoRequest("GET", "/v1/jobs", { params: { range: "1y" } }), (e) => e.kind === "validation");
});

test("MUTATION TARGET: demo /v1/jobs?range= keeps in-flight jobs, filters finished ones by age, and does not advance the simulation", async () => {
  const all = await demoRequest("GET", "/v1/jobs", { params: { limit: 50, range: "all" } });
  const running = all.find((j) => j.status === "running");
  assert.ok(running, "seed has a running job");
  for (let i = 0; i < 5; i++) await demoRequest("GET", "/v1/jobs", { params: { limit: 50, range: "24h" } });
  const again = await demoRequest("GET", "/v1/jobs", { params: { limit: 50, range: "all" } });
  assert.equal(again.find((j) => j.id === running.id).status, "running", "filtered fetches never tick the demo job");
  const day = await demoRequest("GET", "/v1/jobs", { params: { limit: 50, range: "24h" } });
  // WP DOWNLOADS-3: the seed history reaches back ~5 days, so 24h keeps the
  // in-flight job plus the finished ones of the last day only.
  const cutoff = Date.now() - 24 * 3_600_000;
  const expected = all.filter((j) => !j.finished_at || Date.parse(j.finished_at) >= cutoff).map((j) => j.id);
  assert.deepEqual(day.map((j) => j.id), expected, "24h = in-flight + finished within 24 h");
  assert.ok(day.length < all.length, "older seed history is filtered out");
  const week = await demoRequest("GET", "/v1/jobs", { params: { limit: 50, range: "7d" } });
  assert.equal(week.length, all.length, "the whole seed history is within 7 days");
});
