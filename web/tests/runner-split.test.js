/**
 * WP RUNNER-SPLIT-1: the prefill runner's traffic split by cache status.
 * The server's `current` (swept and live `now`) carries
 * `runner_hit_bytes_per_second` / `runner_miss_bytes_per_second`, and the
 * series points `runner_hit_bytes` / `runner_miss_bytes`. The hero's pill
 * says "Downloading" only for runner traffic that is not all cache HITs and
 * "Verifying" (the existing `verify` status kind, no new kind) for a runner
 * that only reads from the cache; the graph counts runner HITs as "served
 * from cache". An older server without the split keeps the old behaviour.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { createFakeDom } from "./fake-dom.js";

const dom = createFakeDom();
globalThis.document = dom.document;

const {
  heroModel,
  heroStatus,
  splitGeometry,
  graphModel,
  createDownloadsHero,
  PILL_DOWNLOADING,
  PILL_SERVING,
  PILL_VERIFYING,
  PILL_IDLE,
} = await import("../js/components/downloads-hero.js");
const { mergeNow, mergeLive, liveSeries } = await import("../js/lib/traffic-view.js");
const { demoRequest, resetDemoData } = await import("../js/demo-data.js");

const here = path.dirname(fileURLToPath(import.meta.url));
const MIB = 1024 ** 2;
const GIB = 1024 ** 3;
const feed = { enabled: true, state: "ok", last_sweep_at: "2026-10-18T11:58:07Z", sweep_interval_minutes: 5, traffic_since: null };

beforeEach(() => resetDemoData());

/** A live `current` (the `now` block's) with the runner split. */
function current(total, runner, hit, miss) {
  return {
    bytes_per_second: total,
    runner_bytes_per_second: runner,
    runner_hit_bytes_per_second: hit,
    runner_miss_bytes_per_second: miss,
    window_start: "2026-10-18T11:59:50Z",
    window_end: "2026-10-18T12:00:07Z",
    window_seconds: 17,
  };
}

function answer(nowCurrent, { runnerSummary = null } = {}) {
  return {
    event_feed: feed,
    range: "1h",
    tier: "1m",
    bucket_seconds: 60,
    range_start: "2026-10-18T11:01:00Z",
    active_window_minutes: 15,
    // The swept window lags (an older download); `now` must win.
    current: { bytes_per_second: 9 * MIB, runner_bytes_per_second: 9 * MIB, runner_hit_bytes_per_second: 0, runner_miss_bytes_per_second: 9 * MIB, window_start: "2026-10-18T11:50:00Z", window_end: "2026-10-18T11:58:07Z", window_seconds: 487 },
    active_clients_now: 0,
    runner_active_now: true,
    summary: {
      bytes_served: 30 * GIB,
      hit_bytes: 0,
      miss_bytes: 0,
      byte_hit_rate: null,
      average_bytes_per_second: 30 * MIB,
      peak_bytes_per_second: 68 * MIB,
      active_clients: 0,
      prefill_runner: runnerSummary,
    },
    jobs: [],
    now: { current: nowCurrent, active_clients: 0, runner_active: nowCurrent.runner_bytes_per_second > 0, window_seconds: 17, live_since: "2026-10-18T11:45:00Z" },
  };
}

// ---------------------------------------------------------------------
// The pill
// ---------------------------------------------------------------------

test("MUTATION TARGET: a verify-only runner (all HIT) reads Verifying, never Downloading", () => {
  const status = heroStatus(current(68 * MIB, 68 * MIB, 68 * MIB, 0));
  assert.deepEqual(status, { kind: "verify", text: PILL_VERIFYING, tone: "run" });
  assert.equal(PILL_VERIFYING, "Verifying");
  // Through the page's path: the persisted range's answer, merged with `now`.
  const model = heroModel({ throughput: mergeNow(answer(current(68 * MIB, 68 * MIB, 68 * MIB, 0))) });
  assert.equal(model.figures.status.text, PILL_VERIFYING);
  assert.notEqual(model.figures.status.text, PILL_DOWNLOADING);
  // A verify next to a client that loads: still the runner's verify.
  assert.equal(heroStatus(current(80 * MIB, 68 * MIB, 68 * MIB, 0)).text, PILL_VERIFYING);
});

test("MUTATION TARGET: runner MISS traffic reads Downloading, also when most of it is HITs", () => {
  assert.deepEqual(heroStatus(current(20 * MIB, 20 * MIB, 0, 20 * MIB)), { kind: "running", text: PILL_DOWNLOADING, tone: "run" });
  // Mixed: verifies most chunks, downloads a few -> it is downloading.
  assert.equal(heroStatus(current(20 * MIB, 20 * MIB, 19 * MIB, 1 * MIB)).text, PILL_DOWNLOADING);
  // BYPASS is in neither split figure: runner bytes that are not HITs.
  assert.equal(heroStatus(current(20 * MIB, 20 * MIB, 15 * MIB, 0)).text, PILL_DOWNLOADING);
});

test("the other pill states are unchanged by the split", () => {
  assert.equal(heroStatus(current(40 * MIB, 0, 0, 0)).text, PILL_SERVING);
  assert.equal(heroStatus(current(0, 0, 0, 0)).text, PILL_IDLE);
  // A nonsense split (negative, NaN) is no split: runner traffic downloads.
  assert.equal(heroStatus(current(5, 5, -1, 0)).text, PILL_DOWNLOADING);
  assert.equal(heroStatus(current(5, 5, Number.NaN, 0)).text, PILL_DOWNLOADING);
});

test("backward compatible: a server before rc21 (no split) keeps Downloading for runner traffic", () => {
  assert.equal(heroStatus({ bytes_per_second: 6 * MIB, runner_bytes_per_second: 6 * MIB }).text, PILL_DOWNLOADING);
  assert.equal(heroStatus({ bytes_per_second: 6 * MIB, runner_bytes_per_second: 6 * MIB, runner_hit_bytes_per_second: null }).text, PILL_DOWNLOADING);
});

test("mergeNow and mergeLive carry the split into the pill (live window, not the swept one)", () => {
  // Swept window: downloading; live: verify only -> the live figure wins.
  assert.equal(heroModel({ throughput: mergeNow(answer(current(30 * MIB, 30 * MIB, 30 * MIB, 0))) }).figures.status.text, PILL_VERIFYING);
  const live = { event_feed: feed, current: current(30 * MIB, 30 * MIB, 30 * MIB, 0), active_clients: 0, active_window_seconds: 17, points: [] };
  const summary = answer(current(0, 0, 0, 0));
  delete summary.now;
  assert.equal(heroModel({ throughput: mergeLive(summary, live) }).figures.status.text, PILL_VERIFYING);
});

test("the built hero swaps the pill between Downloading and Verifying (kind changes, so it is rebuilt)", () => {
  const hero = createDownloadsHero({ range: "1h", onRangeChange() {} });
  const pill = () => hero.el.querySelector('[data-role="stats-pill"]');
  const paint = (cur) => hero.paint({ throughput: mergeNow(answer(cur)), series: null, range: "1h", rateCap: null, unsupported: false, error: null, noFigures: false });
  paint(current(20 * MIB, 20 * MIB, 0, 20 * MIB));
  assert.equal(pill().dataset.kind, "running");
  assert.ok(pill().textContent.includes(PILL_DOWNLOADING));
  paint(current(20 * MIB, 20 * MIB, 20 * MIB, 0));
  assert.equal(pill().dataset.kind, "verify");
  assert.ok(pill().textContent.includes(PILL_VERIFYING));
  assert.ok(!pill().textContent.includes(PILL_DOWNLOADING));
});

test("the prefill runner mini line stays correct: X of it, Y from the cache", () => {
  const runner = { bytes_served: 23 * GIB, hit_bytes: 23 * GIB, miss_bytes: 0, requests: 1, hits: 1, misses: 0, errors: 0, byte_hit_rate: 1 };
  const model = heroModel({ throughput: mergeNow(answer(current(20 * MIB, 20 * MIB, 20 * MIB, 0), { runnerSummary: runner })) });
  const line = model.figures.mini.find((m) => m.key === "runner");
  const text = line.parts.map((p) => (typeof p === "string" ? p : p.b)).join("");
  assert.equal(text, "Prefill runner 23.0 GB of it, 23.0 GB from the cache");
});

// ---------------------------------------------------------------------
// The graph
// ---------------------------------------------------------------------

test("MUTATION TARGET: a verify-only runner's bytes are in the served-from-cache series, not from the internet", () => {
  // One point: 100 bytes, all the runner's, all HIT; no client traffic.
  const pts = [
    { bytes_per_second: 0, bytes_served: 0, hit_bytes: 0, runner_bytes: 0, runner_hit_bytes: 0, runner_miss_bytes: 0 },
    { bytes_per_second: 10 * MIB, bytes_served: 100, hit_bytes: 0, runner_bytes: 100, runner_hit_bytes: 100, runner_miss_bytes: 0 },
  ];
  const g = splitGeometry(pts, { width: 600, height: 96 });
  // Served line reaches the total: the internet band is empty.
  assert.equal(g.served.line, "M0 96 L600 0");
  assert.equal(g.internet.line, g.served.line);
});

test("mixed runner and client: cache = client HIT + runner HIT, internet = the rest", () => {
  // 1000 bytes: client HIT 200, client MISS 100, runner HIT 400, runner MISS 300.
  const pts = [
    { bytes_per_second: 0, bytes_served: 0, hit_bytes: 0, runner_hit_bytes: 0 },
    { bytes_per_second: 10 * MIB, bytes_served: 1000, hit_bytes: 200, miss_bytes: 100, runner_bytes: 700, runner_hit_bytes: 400, runner_miss_bytes: 300 },
  ];
  const g = splitGeometry(pts, { width: 600, height: 96 });
  // served = 60% of the total -> y = 96 - 0.6 * 96 = 38.4.
  assert.equal(g.served.line, "M0 96 L600 38.4");
  assert.equal(g.internet.line, "M0 96 L600 0");
});

test("backward compatible: points without runner_hit_bytes count all runner traffic as from the internet", () => {
  const pts = [
    { bytes_per_second: 0, bytes_served: 0, hit_bytes: 0 },
    { bytes_per_second: 10 * MIB, bytes_served: 100, hit_bytes: 0, runner_bytes: 100 },
  ];
  assert.equal(splitGeometry(pts, { width: 600, height: 96 }).served.line, "M0 96 L600 96");
});

test("the live view's points carry the split into the graph too", () => {
  const live = {
    event_feed: feed,
    range_start: "2026-10-18T12:00:00Z",
    points: [
      { t: "2026-10-18T12:00:00Z", bucket_seconds: 5, source: "live", bytes_served: 0, hit_bytes: 0, miss_bytes: 0, requests: 0, runner_bytes: 0, runner_hit_bytes: 0, runner_miss_bytes: 0, bytes_per_second: 0, peak_bytes_per_second: 0 },
      { t: "2026-10-18T12:00:05Z", bucket_seconds: 5, source: "live", bytes_served: 500, hit_bytes: 0, miss_bytes: 0, requests: 0, runner_bytes: 500, runner_hit_bytes: 500, runner_miss_bytes: 0, bytes_per_second: 100, peak_bytes_per_second: 100 },
    ],
  };
  const model = graphModel(liveSeries(live), "live");
  assert.ok(model.split);
  assert.equal(model.split.internet.line, model.split.served.line, "all of it from the cache");
});

// ---------------------------------------------------------------------
// Demo stays golden-neutral
// ---------------------------------------------------------------------

test("demo: carries the split, and the pill and graph bands read as before (golden-neutral)", async () => {
  for (const range of ["1h", "6h", "24h", "7d"]) {
    const raw = await demoRequest("GET", "/v1/throughput", { params: { range } });
    for (const cur of [raw.current, raw.now.current]) {
      assert.equal(typeof cur.runner_hit_bytes_per_second, "number", range);
      assert.equal(typeof cur.runner_miss_bytes_per_second, "number", range);
    }
    const s = await demoRequest("GET", "/v1/throughput/series", { params: { range } });
    assert.ok(s.points.every((p) => p.runner_hit_bytes === 0 && p.runner_miss_bytes === p.runner_bytes), range);
    // Without the new fields the graph would draw the same bands.
    const stripped = s.points.map(({ runner_hit_bytes: _h, runner_miss_bytes: _m, ...rest }) => rest);
    const box = { width: 600, height: 96 };
    assert.deepEqual(splitGeometry(s.points, box), splitGeometry(stripped, box), range);
  }
  const live = await demoRequest("GET", "/v1/throughput/live", {});
  assert.ok(live.points.every((p) => p.runner_hit_bytes === 0));
});

test("status vocabulary: Verifying reuses the existing verify kind, no new kind", () => {
  const vocab = JSON.parse(readFileSync(path.join(here, "..", "..", "docs", "design", "status-vocabulary.json"), "utf8"));
  assert.ok(vocab.some((k) => k.kind === "verify" && k.word === PILL_VERIFYING));
  assert.equal(vocab.length, 14);
});
