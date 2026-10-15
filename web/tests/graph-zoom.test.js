/**
 * WP GRAPH-ZOOM: the 1 h / 6 h ranges (server's 1-minute tier), the trimmed
 * graph span, and the remembered Downloads range.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { demoRequest, resetDemoData } from "../js/demo-data.js";
import {
  DEFAULT_STATS_RANGE,
  RANGES,
  STATS_RANGES,
  STATS_RANGE_KEY,
  RANGE_LABELS,
  RANGE_PHRASES,
  bucketWord,
  headerTiles,
  isRange,
  isStatsRange,
  readStoredStatsRange,
  seriesGeometry,
  seriesStartText,
  trimSeries,
  writeStoredStatsRange,
} from "../js/lib/traffic-view.js";

beforeEach(() => resetDemoData());

const memory = (initial = {}) => {
  const data = new Map(Object.entries(initial));
  return { getItem: (k) => (data.has(k) ? data.get(k) : null), setItem: (k, v) => data.set(k, String(v)), data };
};

test("the stats ranges add live, 1 h and 6 h; the shared four stay the shared four", () => {
  assert.deepEqual([...STATS_RANGES], ["live", "1h", "6h", "24h", "7d", "30d", "all"]);
  assert.deepEqual([...RANGES], ["24h", "7d", "30d", "all"]);
  assert.equal(isRange("1h"), false, "the History filter and the clients page do not get 1 h");
  assert.equal(isStatsRange("1h"), true);
  for (const r of STATS_RANGES) assert.ok(RANGE_LABELS[r] && RANGE_PHRASES[r], r);
  assert.equal(bucketWord(60), "minute");
});

test("MUTATION TARGET: the range default is 6 h; a stored valid range wins; junk falls back", () => {
  assert.equal(DEFAULT_STATS_RANGE, "6h");
  assert.equal(readStoredStatsRange(() => memory()), "6h");
  assert.equal(readStoredStatsRange(() => memory({ [STATS_RANGE_KEY]: "7d" })), "7d");
  assert.equal(readStoredStatsRange(() => memory({ [STATS_RANGE_KEY]: "1h" })), "1h");
  assert.equal(readStoredStatsRange(() => memory({ [STATS_RANGE_KEY]: "2h" })), "6h");
  assert.equal(readStoredStatsRange(() => null), "6h", "no storage");
  assert.equal(
    readStoredStatsRange(() => {
      throw new Error("blocked");
    }),
    "6h",
    "storage that throws",
  );
  assert.ok(STATS_RANGE_KEY.startsWith("steamvault."));
});

test("writing the range stores it, refuses an invalid one and survives a throwing storage", () => {
  const m = memory();
  assert.equal(writeStoredStatsRange("30d", () => m), true);
  assert.equal(m.data.get(STATS_RANGE_KEY), "30d");
  assert.equal(writeStoredStatsRange("2h", () => m), false);
  assert.equal(m.data.get(STATS_RANGE_KEY), "30d");
  assert.equal(
    writeStoredStatsRange("7d", () => {
      throw new Error("quota");
    }),
    false,
  );
});

test("MUTATION TARGET: trimSeries drops the empty lead-in but keeps one point before the data", () => {
  const p = (v) => ({ bytes_per_second: v });
  assert.deepEqual(trimSeries([p(0), p(0), p(0), p(5), p(0), p(7), p(0)]), [p(0), p(5), p(0), p(7), p(0)]);
  assert.deepEqual(trimSeries([p(3), p(0)]), [p(3), p(0)], "data from the first point: nothing to trim");
  assert.equal(trimSeries([p(0), p(0)]).length, 2, "all zero: unchanged");
  assert.deepEqual(trimSeries(null), []);
  const geo = seriesGeometry(trimSeries([p(0), p(0), p(0), p(4), p(8)]), { width: 100, height: 50 });
  assert.equal(geo.line, "M0 50 L50 25 L100 0", "the trimmed span fills the whole width");
  assert.ok(geo.area.endsWith("Z") && geo.area.includes("L100 50 L0 50"), "area closed along the floor");
});

test("the start caption follows the shown span", () => {
  const now = Date.parse("2026-10-15T12:00:00Z");
  const full = { range: "6h", range_start: "2026-10-15T06:00:00Z", points: [{ t: "2026-10-15T06:00:00Z" }] };
  assert.equal(seriesStartText(full, now), "6 h ago");
  const trimmed = { range: "6h", range_start: "2026-10-15T06:00:00Z", points: [{ t: "2026-10-15T11:20:00Z" }] };
  assert.notEqual(seriesStartText(trimmed, now), "6 h ago");
  assert.match(seriesStartText(trimmed, now), /40 min|40 minutes/);
  assert.equal(seriesStartText({ range: "1h", points: [] }, now), "1 h ago");
  assert.equal(seriesStartText({ range: "30d" }), "30 days ago");
  assert.equal(seriesStartText({ range: "all", points: [{ t: "2026-09-01T00:00:00Z" }] }), "since 2026-09-01");
});

test("header tile titles say minute for the 1-minute tier", () => {
  const base = { event_feed: { enabled: true, state: "ok" }, summary: {}, current: null, jobs: [] };
  const fine = headerTiles({ ...base, range: "1h", bucket_seconds: 60 }).find((t) => t.key === "peak");
  assert.match(fine.title, /busiest minute in the last hour/);
  const coarse = headerTiles({ ...base, range: "24h", bucket_seconds: 300 }).find((t) => t.key === "peak");
  assert.match(coarse.title, /busiest 5 minutes/);
});

test("MUTATION TARGET: demo covers 1 h and 6 h with 1-minute points that start part-way in", async () => {
  for (const [range, n] of [["1h", 60], ["6h", 360]]) {
    const s = await demoRequest("GET", "/v1/throughput/series", { params: { range } });
    assert.equal(s.range, range);
    assert.equal(s.tier, "1m");
    assert.equal(s.bucket_seconds, 60);
    assert.equal(s.points.length, n);
    assert.equal(s.points[0].bytes_per_second, 0, "empty at the far end ...");
    assert.ok(s.points.some((p) => p.bytes_per_second > 0), "... with data later");
    assert.ok(trimSeries(s.points).length < n, "so the trim has something to cut");
    const t = await demoRequest("GET", "/v1/throughput", { params: { range } });
    assert.equal(t.range, range);
    assert.equal(t.bucket_seconds, 60);
    assert.ok(t.summary.peak_bytes_per_second > 0);
    assert.ok(t.summary.average_bytes_per_second > 0);
  }
});

test("demo: the clients overview still refuses 1 h like the server", async () => {
  await assert.rejects(demoRequest("GET", "/v1/clients/overview", { params: { range: "1h" } }), (e) => e.kind === "validation");
});
