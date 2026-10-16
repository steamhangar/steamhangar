/**
 * WP GRAPH-LIVE: the live view of the Downloads graph. Pure helpers
 * (liveSeries, mergeLive, wording), the demo route's shape against the
 * server models (read as text, twin-pin style) and the view's wiring.
 */
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import {
  STATS_RANGES,
  LIVE_POLL_MS,
  LIVE_SUMMARY_MS,
  bucketWord,
  headerTiles,
  liveSeries,
  mergeLive,
  seriesGeometry,
  seriesLabel,
  seriesStartText,
} from "../js/lib/traffic-view.js";
import { demoRequest, resetDemoData } from "../js/demo-data.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const read = (...p) => readFileSync(path.join(here, "..", ...p), "utf8");
const trafficSrc = read("..", "api", "vault_api", "routers", "traffic.py");

function modelFields(name) {
  const m = new RegExp(`class ${name}\\(BaseModel\\):([\\s\\S]*?)(?=\\n\\S)`).exec(trafficSrc);
  assert.ok(m, `class ${name}(BaseModel) not found: re-point this guard`);
  return [...m[1].matchAll(/^ {4}([a-z_][a-z0-9_]*)\s*:\s*\S/gm)].map((x) => x[1]).sort();
}
const keys = (o) => Object.keys(o).sort();

beforeEach(() => resetDemoData());

test("live is the first range; cadence is a few seconds, summary refresh slower", () => {
  assert.equal(STATS_RANGES[0], "live");
  assert.ok(LIVE_POLL_MS >= 2000 && LIVE_POLL_MS <= 5000);
  assert.ok(LIVE_SUMMARY_MS > LIVE_POLL_MS);
  assert.equal(bucketWord(5), "5 seconds");
  assert.equal(bucketWord(15), "15 seconds");
});

test("demo /v1/throughput/live has exactly the server's keys", async () => {
  const live = await demoRequest("GET", "/v1/throughput/live");
  assert.deepEqual(keys(live), modelFields("LiveOut"));
  assert.deepEqual(keys(live.current), modelFields("LiveCurrentOut"));
  assert.deepEqual(keys(live.points[0]), modelFields("LivePointOut"));
  assert.equal(live.range, "live");
  const buckets = new Set(live.points.map((p) => p.bucket_seconds));
  assert.deepEqual([...buckets].sort((a, b) => a - b), [5, 15, 60]);
});

test("liveSeries expands to a uniform 5 s grid so the time axis stays true", async () => {
  const live = await demoRequest("GET", "/v1/throughput/live");
  const series = liveSeries(live);
  const slots = live.points.reduce((n, p) => n + p.bucket_seconds / 5, 0);
  assert.equal(series.points.length, slots);
  assert.equal(series.range, "live");
  assert.ok(seriesGeometry(series.points, { width: 300, height: 60 }));
  assert.match(seriesLabel(series, 1_000_000), /Average speed per 5 seconds/);
  assert.equal(liveSeries(null), null);
});

test("mergeLive takes Current speed from the live tier, the rest from the 1 h summary", async () => {
  const summary = await demoRequest("GET", "/v1/throughput", { params: { range: "1h" } });
  const live = await demoRequest("GET", "/v1/throughput/live");
  const merged = mergeLive(summary, live);
  assert.equal(merged.range, "live");
  assert.equal(merged.live, true);
  assert.equal(merged.current.bytes_per_second, live.current.bytes_per_second);
  assert.deepEqual(merged.summary, summary.summary);
  // "Loading now" comes from the live ring, not the lagging persisted summary.
  assert.equal(merged.active_clients_now, live.active_clients);
  assert.equal(merged.active_window_minutes, live.active_window_minutes);
  const tiles = headerTiles(merged);
  const current = tiles.find((t) => t.key === "current");
  assert.match(current.detail, /last \d+ s/);
  assert.match(tiles.find((t) => t.key === "peak").title, /in the last hour/);
  assert.equal(mergeLive(null, live), null);
  // No live current (feed disabled): the persisted figure stays, wording unchanged.
  const fallback = mergeLive(summary, { ...live, event_feed: { ...live.event_feed, state: "disabled" } });
  assert.equal(fallback.live, false);
  assert.equal(fallback.current.bytes_per_second, summary.current.bytes_per_second);
  assert.equal(fallback.active_clients_now, summary.active_clients_now);
});

test("the axis caption of the live view", () => {
  assert.equal(seriesStartText({ range: "live", range_start: "x", points: [{ t: "x" }] }), "1 h ago");
});

test("view wiring: live polls fast through a function interval; the poll parks hidden tabs", () => {
  const view = read("js", "views", "downloads.js");
  assert.match(view, /traffic\.range === "live" \? LIVE_POLL_MS : VIEW_POLL_MS/);
  assert.match(view, /api\.throughputLive\(\)/);
  const store = read("js", "store.js");
  assert.match(store, /typeof intervalMs === "function"/);
  assert.match(read("js", "api.js"), /\/v1\/throughput\/live/);
});
