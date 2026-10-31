/**
 * WP GRAPH-RECENT-1 + LOADING-NOW-1: the persisted Downloads ranges take
 * "Current speed", the status pill and "Loading now" from the server's
 * `now` block (the live tier, one window), and "Loading now" counts over
 * the current-speed window on every range, as the Clients page's
 * `live_now` does. Pure helpers (`mergeNow`, `mergeLive`, wording), the
 * hero's agreement of big number, pill and newest graph point, the demo's
 * golden-neutral `now` block, and the view's wiring (source pin).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import {
  activeWindowSeconds,
  clientDownloadingRow,
  headerTiles,
  mergeLive,
  mergeNow,
} from "../js/lib/traffic-view.js";
import { devicesLoading, graphModel, heroModel, PILL_IDLE, PILL_SERVING, PILL_DOWNLOADING } from "../js/components/downloads-hero.js";
import { demoRequest, resetDemoData } from "../js/demo-data.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const read = (...p) => readFileSync(path.join(here, "..", ...p), "utf8");

const MIB = 1024 ** 2;
const feed = (state = "ok") => ({ enabled: state !== "disabled", state, last_sweep_at: "2026-10-18T11:58:07Z", sweep_interval_minutes: 5, traffic_since: null });

/** A 1 h answer as the server sends it: the swept figures lag (the
 * operator's screenshot: 10.3 MB/s from the runner, 9 minutes old). */
function answer({ state = "ok", now = undefined } = {}) {
  return {
    event_feed: feed(state),
    range: "1h",
    tier: "1m",
    bucket_seconds: 60,
    range_start: "2026-10-18T11:01:00Z",
    active_window_minutes: 15,
    current: { bytes_per_second: 10.3 * MIB, runner_bytes_per_second: 10.3 * MIB, window_start: "2026-10-18T11:50:00Z", window_end: "2026-10-18T11:58:07Z", window_seconds: 487 },
    active_clients_now: 2,
    runner_active_now: true,
    summary: { bytes_served: 600 * MIB, hit_bytes: 0, miss_bytes: 0, byte_hit_rate: null, average_bytes_per_second: 30 * MIB, peak_bytes_per_second: 68.6 * MIB, active_clients: 2 },
    jobs: [],
    ...(now === undefined ? {} : { now }),
  };
}
const nowBlock = (bps, runnerBps, clients, seconds = 17) => ({
  current: { bytes_per_second: bps, runner_bytes_per_second: runnerBps, window_start: "2026-10-18T11:59:50Z", window_end: "2026-10-18T12:00:07Z", window_seconds: seconds },
  active_clients: clients,
  runner_active: runnerBps > 0,
  window_seconds: seconds,
  live_since: "2026-10-18T11:45:00Z",
});
/** A 1 h series whose newest point (the running minute, stitched from the
 * ring by the server) is `newest` bytes; one old peak 9 minutes back. */
function series(newest) {
  const points = [];
  for (let i = 59; i >= 0; i--) {
    const v = i === 9 ? 68.6 * MIB * 60 : i === 0 ? newest : 0;
    points.push({ t: `m-${i}`, bytes_served: v, hit_bytes: 0, miss_bytes: 0, requests: v ? 1 : 0, runner_bytes: 0, bytes_per_second: v / 60, peak_bytes_per_second: v / 60, source: i === 0 ? "live" : "persisted" });
  }
  return { event_feed: feed(), range: "1h", tier: "1m", bucket_seconds: 60, range_start: points[0].t, points };
}
const newestPoint = (s) => s.points[s.points.length - 1];

beforeEach(() => resetDemoData());

test("MUTATION TARGET: mergeNow takes current, the runner flag and Loading now from the now block", () => {
  const raw = answer({ now: nowBlock(4 * MIB, 0, 1) });
  const merged = mergeNow(raw);
  assert.equal(merged.live, true);
  assert.deepEqual(merged.current, raw.now.current);
  assert.equal(merged.active_clients_now, 1);
  assert.equal(merged.runner_active_now, false);
  assert.equal(merged.active_window_seconds, 17);
  assert.equal(merged.range, "1h", "the range is kept (the view drops answers for another range)");
  assert.deepEqual(merged.summary, raw.summary, "the range's totals stay swept");
  // Pure: the server's answer is not modified.
  assert.equal(raw.current.bytes_per_second, 10.3 * MIB);
  assert.equal(raw.active_clients_now, 2);
});

test("mergeNow leaves an answer without a usable now block alone", () => {
  const old = answer(); // a server before rc21
  assert.equal(mergeNow(old), old);
  const nul = answer({ now: null }); // feed not ok on the server
  assert.equal(mergeNow(nul), nul);
  const stale = answer({ state: "stale", now: nowBlock(1, 0, 1) });
  assert.equal(mergeNow(stale), stale);
  assert.equal(mergeNow(null), null);
  assert.equal(mergeNow(undefined), undefined);
});

test("operator report: an old peak reads Idle now, and the big number, pill and newest point agree", () => {
  // Before: the swept window said 10.3 MB/s "Downloading" while the graph was flat now.
  const before = heroModel({ throughput: answer() });
  assert.equal(before.figures.status.text, PILL_DOWNLOADING);
  assert.equal(before.figures.big.value, "10.3");

  const throughput = mergeNow(answer({ now: nowBlock(0, 0, 0) }));
  const hero = heroModel({ throughput });
  const graph = series(0);
  assert.equal(hero.figures.status.text, PILL_IDLE);
  assert.equal(`${hero.figures.big.value} ${hero.figures.big.unit}`, "0 MB/s");
  assert.equal(newestPoint(graph).bytes_per_second, 0);
  assert.ok(hero.figures.caption.includes("no device loading"));
  // The old peak is still drawn.
  const model = graphModel(graph, "1h", { nowMs: Date.parse("2026-10-18T12:00:07Z") });
  assert.equal(model.idle, undefined);
  assert.match(model.axis[1], /68\.6 MB\/s/);
});

test("while a client loads, the pill serves, the big number is live and the newest point is not zero", () => {
  const throughput = mergeNow(answer({ now: nowBlock(48 * MIB, 0, 1) }));
  const hero = heroModel({ throughput });
  const graph = series(48 * MIB * 20); // 20 s into the running minute
  assert.equal(hero.figures.status.text, PILL_SERVING);
  assert.equal(`${hero.figures.big.value} ${hero.figures.big.unit}`, "48.0 MB/s");
  assert.ok(newestPoint(graph).bytes_per_second > 0);
  assert.ok(hero.figures.caption.includes("1 device loading"));
  // The runner alone: "Downloading", nobody else loading.
  const runner = heroModel({ throughput: mergeNow(answer({ now: nowBlock(20 * MIB, 20 * MIB, 0) })) });
  assert.equal(runner.figures.status.text, PILL_DOWNLOADING);
  assert.ok(runner.figures.caption.includes("no device loading"));
});

test("MUTATION TARGET: Loading now is worded with the live window, the swept one keeps minutes", () => {
  const live = mergeNow(answer({ now: nowBlock(1, 0, 2, 18) }));
  assert.equal(activeWindowSeconds(live), 18);
  const tile = headerTiles(live).find((t) => t.key === "clients");
  assert.equal(tile.value, "2");
  assert.equal(tile.detail, "machines the cache served in the last 18 s");
  assert.match(tile.title, /^Machines the cache served in the last 18 seconds \(the window of "Current speed"\)\./);
  assert.equal(clientDownloadingRow(live).detail, "2 machines served by the cache in the last 18 s");
  assert.match(devicesLoading(live).title, /^2 machines served by the cache in the last 18 s\./);
  // Swept (an older server): unchanged words, minutes.
  const swept = answer();
  assert.equal(activeWindowSeconds(swept), null);
  assert.equal(headerTiles(swept).find((t) => t.key === "clients").detail, "machines with cache traffic in the last 15 min");
  assert.equal(clientDownloadingRow(swept).detail, "2 machines with cache traffic in the last 15 min");
});

test("mergeLive carries the live window of Loading now (LOADING-NOW-1)", async () => {
  const summary = await demoRequest("GET", "/v1/throughput", { params: { range: "1h" } });
  const live = await demoRequest("GET", "/v1/throughput/live");
  const merged = mergeLive(summary, live);
  assert.equal(merged.active_clients_now, live.active_clients);
  assert.equal(merged.active_window_seconds, live.active_window_seconds);
  assert.equal(live.active_window_seconds, live.current.window_seconds, "one window for speed and loading");
  assert.match(headerTiles(merged).find((t) => t.key === "clients").detail, /^machines the cache served in the last \d+ s$/);
  // An older live answer without the seconds keeps the minutes wording.
  const { active_window_seconds: _drop, ...older } = live;
  assert.equal(activeWindowSeconds(mergeLive(summary, older)), null);
});

test("demo: the now block is golden-neutral (same big number, pill and device count as before)", async () => {
  for (const range of ["1h", "6h", "24h", "7d"]) {
    const raw = await demoRequest("GET", "/v1/throughput", { params: { range } });
    assert.ok(raw.now, `${range}: the demo carries the server's now block`);
    assert.equal(raw.now.window_seconds, raw.now.current.window_seconds);
    const before = heroModel({ throughput: raw });
    const after = heroModel({ throughput: mergeNow(raw) });
    assert.deepEqual(after.figures.big.value, before.figures.big.value, range);
    assert.deepEqual(after.figures.status, before.figures.status, range);
    assert.deepEqual(after.figures.caption, before.figures.caption, range);
  }
  const s = await demoRequest("GET", "/v1/throughput/series", { params: { range: "1h" } });
  assert.ok(s.points.every((p) => p.source === "persisted"));
});

test("view wiring: the persisted ranges go through mergeNow, the live range through mergeLive", () => {
  const view = read("js", "views", "downloads.js");
  assert.match(view, /return \{ range, throughput: mergeNow\(throughput\), series \};/);
  assert.match(view, /throughput: mergeLive\(liveSummary\.value, live\)/);
});
