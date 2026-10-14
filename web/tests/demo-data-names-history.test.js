/**
 * Demo mode for WP D15-NAMES: client names (`/v1/client-names/...`, the
 * name fields on `/v1/clients` and `/v1/clients/overview`) and
 * `DELETE /v1/history`. The key sets are pinned against the server models
 * by demo-data-shape-guard.test.js and demo-data-traffic.test.js; this file
 * pins the behaviour.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { demoRequest, resetDemoData } from "../js/demo-data.js";

beforeEach(() => resetDemoData());

const byKey = (overview) => new Map(overview.clients.map((c) => [c.key, c]));

test("MUTATION TARGET: a name set for an agent's address wins in /v1/clients and the overview", async () => {
  const put = await demoRequest("PUT", "/v1/client-names/ip/10.10.0.21", { body: { name: "  Workshop  " } });
  assert.deepEqual([put.key_type, put.key, put.name], ["ip", "10.10.0.21", "Workshop"]);
  const rows = await demoRequest("GET", "/v1/clients");
  const pc = rows.find((r) => r.client_id === "workshop-pc");
  assert.deepEqual([pc.name, pc.name_addr, pc.display_name], ["Workshop", "10.10.0.21", "Workshop"]);
  const other = rows.find((r) => r.client_id === "loft-laptop");
  assert.deepEqual([other.name, other.display_name], [null, "loft-laptop"]);
  const o = byKey(await demoRequest("GET", "/v1/clients/overview", { params: { range: "7d" } }));
  assert.equal(o.get("agent:workshop-pc").display_name, "Workshop");
  assert.equal(o.get("addr:10.10.0.57").display_name, "10.10.0.57");
  assert.equal(o.get("addr:10.10.0.57").name_addr, "10.10.0.57");
});

test("MUTATION TARGET: an agentless address can be named and cleared; a second clear is the handler's 404", async () => {
  await demoRequest("PUT", `/v1/client-names/ip/${encodeURIComponent("10.10.0.57")}`, { body: { name: "frame" } });
  let o = byKey(await demoRequest("GET", "/v1/clients/overview", { params: { range: "24h" } }));
  assert.deepEqual([o.get("addr:10.10.0.57").name, o.get("addr:10.10.0.57").display_name], ["frame", "frame"]);
  assert.deepEqual(
    (await demoRequest("GET", "/v1/client-names")).map((n) => `${n.key}=${n.name}`),
    ["10.10.0.57=frame"],
  );
  assert.equal(await demoRequest("DELETE", "/v1/client-names/ip/10.10.0.57"), null);
  o = byKey(await demoRequest("GET", "/v1/clients/overview", { params: { range: "24h" } }));
  assert.equal(o.get("addr:10.10.0.57").name, null);
  await assert.rejects(demoRequest("DELETE", "/v1/client-names/ip/10.10.0.57"), (err) => err.status === 404 && err.detail.startsWith("No name stored"));
});

test("demo names are validated like the server's", async () => {
  for (const [path, body] of [
    ["/v1/client-names/ip/10.10.0.57", { name: "" }],
    ["/v1/client-names/ip/10.10.0.57", { name: "a\nb" }],
    ["/v1/client-names/ip/10.10.0.57", { name: "x".repeat(41) }],
    ["/v1/client-names/ip/10.10.0.57", { name: "x", extra: 1 }],
    ["/v1/client-names/ip/frame", { name: "x" }],
    ["/v1/client-names/mac/00:11:22:33:44:55", { name: "x" }],
  ]) {
    await assert.rejects(demoRequest("PUT", path, { body }), (err) => err.status === 422, `${path} ${JSON.stringify(body)}`);
  }
});

test("MUTATION TARGET: demo delete history: finished jobs go, active ones stay, traffic before the moment is zero", async () => {
  const before = await demoRequest("GET", "/v1/jobs", { params: { limit: 200, range: "all" } });
  const active = before.filter((j) => ["queued", "running", "paused"].includes(j.status)).map((j) => j.id);
  assert.ok(before.some((j) => ["done", "error", "cancelled"].includes(j.status)), "the seed has history");
  await assert.rejects(demoRequest("DELETE", "/v1/history", { params: {} }), (err) => err.status === 422);
  const result = await demoRequest("DELETE", "/v1/history", { params: { scope: "all" } });
  assert.equal(result.scope, "all");
  assert.equal(result.cutoff, null);
  assert.ok(result.jobs_deleted > 0);
  const after = await demoRequest("GET", "/v1/jobs", { params: { limit: 200, range: "all" } });
  assert.deepEqual(after.map((j) => j.id).sort(), active.sort());
  const series = await demoRequest("GET", "/v1/throughput/series", { params: { range: "24h" } });
  assert.equal(series.points.every((p) => p.bytes_served === 0), true, "nothing before 'now' is left");
  const t = await demoRequest("GET", "/v1/throughput", { params: { range: "7d" } });
  assert.equal(t.summary.bytes_served, 0);
  const o = await demoRequest("GET", "/v1/clients/overview", { params: { range: "7d" } });
  assert.equal(o.clients.every((c) => !c.stats || c.stats.bytes_served === 0), true);
});

test("demo delete older than 30 days keeps the last 30 days and shrinks the 'all' figures", async () => {
  const full = await demoRequest("GET", "/v1/throughput/series", { params: { range: "24h" } });
  const pc = async () =>
    byKey(await demoRequest("GET", "/v1/clients/overview", { params: { range: "all" } })).get("agent:workshop-pc").stats.bytes_served;
  const allBefore = await pc();
  const result = await demoRequest("DELETE", "/v1/history", { params: { scope: "older-than-30d" } });
  assert.equal(typeof result.cutoff, "string");
  const kept = await demoRequest("GET", "/v1/throughput/series", { params: { range: "24h" } });
  assert.deepEqual(
    kept.points.map((p) => p.bytes_served),
    full.points.map((p) => p.bytes_served),
  );
  assert.equal(kept.event_feed.traffic_since, result.cutoff, "nothing before the cutoff is recorded any more");
  const allAfter = await pc();
  assert.ok(allAfter > 0 && allAfter < allBefore, `${allAfter} < ${allBefore}`);
});
