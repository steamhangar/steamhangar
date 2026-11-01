/**
 * WP WEB-ETA-EMA-1: the ETA smoother takes one EMA step per new `jobs`
 * list, not per answer object. The live view polls every 3 s and re-merges
 * its cached 30 s summary each time (`mergeLive` returns a new object around
 * the same `jobs` array), so keying on the answer object stepped the EMA
 * ~10 times per real sample. The Downloads view clears the smoother on every
 * mount (Android twin: `EtaSmoother.clear()` when the screen starts).
 *
 * Run: node --test web/tests/web-eta-ema-1.test.js
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { ETA_SMOOTHING, EtaSmoother, LIVE_POLL_MS, LIVE_SUMMARY_MS, mergeLive } from "../js/lib/traffic-view.js";

const summary = (rate, id = 1) => ({
  range: "1h",
  event_feed: { state: "ok" },
  jobs: [{ job_id: id, eta_bytes_per_second: rate }],
});
const live = () => ({ event_feed: { state: "ok" }, current: { bytes_per_second: 1 } });

test("MUTATION TARGET -- WEB-ETA-EMA-1: ten live re-merges of one cached summary take exactly one EMA step", () => {
  // The default range's cadence: ~10 live ticks per cached summary.
  assert.ok(LIVE_SUMMARY_MS / LIVE_POLL_MS >= 10);
  const smoother = new EtaSmoother();
  smoother.observe(mergeLive(summary(100), live()));
  assert.equal(smoother.rate(1), 100);
  const cached = summary(200);
  const merged = [];
  for (let i = 0; i < 10; i += 1) {
    const t = mergeLive(cached, live());
    merged.push(t);
    smoother.observe(t);
  }
  // Every tick is a new answer object around the same jobs list.
  assert.equal(new Set(merged).size, 10);
  assert.ok(merged.every((t) => t.jobs === cached.jobs));
  // One step (100 -> 130), not ten (which would land near 200).
  assert.equal(smoother.rate(1), 100 + ETA_SMOOTHING * (200 - 100));
});

test("WEB-ETA-EMA-1: a new jobs list takes a step, a repaint of it does not", () => {
  const smoother = new EtaSmoother();
  smoother.observe(mergeLive(summary(100), live()));
  const next = summary(200);
  smoother.observe(mergeLive(next, live()));
  assert.equal(smoother.rate(1), 130);
  smoother.observe(mergeLive(next, live()));
  assert.equal(smoother.rate(1), 130);
  smoother.observe(mergeLive(summary(200), live())); // fresh 30 s summary
  assert.equal(smoother.rate(1), 130 + ETA_SMOOTHING * 70);
  // A persisted range: every poll is a new list, so every poll is a step.
  const smoother2 = new EtaSmoother();
  smoother2.observe({ jobs: [{ job_id: 7, eta_bytes_per_second: 10 }] });
  smoother2.observe({ jobs: [{ job_id: 7, eta_bytes_per_second: 20 }] });
  assert.equal(smoother2.rate(7), 13);
});

test("WEB-ETA-EMA-1: an answer without jobs forgets every rate once", () => {
  const smoother = new EtaSmoother();
  smoother.observe(summary(100));
  smoother.observe({ range: "live" });
  assert.equal(smoother.rate(1), null);
  smoother.observe({ range: "live" });
  smoother.observe(summary(50));
  assert.equal(smoother.rate(1), 50);
});

test("MUTATION TARGET -- WEB-ETA-EMA-1: clear forgets every rate and the last list", () => {
  const smoother = new EtaSmoother();
  smoother.observe(summary(100));
  const t = summary(200);
  smoother.observe(t);
  assert.equal(smoother.rate(1), 130);
  smoother.clear();
  assert.equal(smoother.rate(1), null);
  // After clear, even the list of the last step is a first sample again
  // (the view remounts with the cached answer still in `traffic`).
  smoother.observe(mergeLive(t, live()));
  assert.equal(smoother.rate(1), 200);
});

test("WEB-ETA-EMA-1 wiring: the Downloads view clears the smoother on mount, before the first paint", () => {
  const src = readFileSync(new URL("../js/views/downloads.js", import.meta.url), "utf8");
  const start = src.indexOf("export function renderDownloads() {");
  assert.ok(start >= 0, "renderDownloads not found");
  const end = src.indexOf("\n}\n", start);
  const body = src.slice(start, end);
  const clear = body.indexOf("etaSmoother.clear();");
  assert.ok(clear >= 0, "renderDownloads must call etaSmoother.clear()");
  assert.ok(clear < body.indexOf("fullRender();"), "clear before the first paint");
  // Still the one observe site (Android twin pin), fed the merged answer.
  assert.equal(src.split("etaSmoother.observe(").length - 1, 1);
  assert.match(src, /etaSmoother\.observe\(t\)/);
});
