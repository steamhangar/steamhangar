/**
 * WP JOB-SIZE-1: a first download's job card shows speed, "of ~X" from the
 * D19 depot preview (marked as an expected size) and, with the size known,
 * an approximate ETA (`remaining_bytes / eta_bytes_per_second`, EMA-smoothed).
 *
 * Run: node --test web/tests/job-size-1.test.js
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  ETA_SMOOTHING,
  EtaSmoother,
  EXPECTED_SIZE_NOTE,
  JOB_ETA_MAX_SECONDS,
  JOB_ETA_MIN_AGE_MS,
  JOB_SPEED_EXPECTED_NOTE,
  JOB_SPEED_NOTE,
  VERIFY_SPEED_TEXT,
  formatEta,
  jobEtaSeconds,
  jobSpeedNote,
  jobSpeedText,
  smoothEtaRate,
} from "../js/lib/traffic-view.js";

const MIB = 1024 * 1024;
const GIB = 1024 * MIB;
const NOW = Date.parse("2026-11-01T12:00:00Z");
const STARTED = "2026-11-01T11:50:00Z"; // ten minutes before NOW

/** A first download as `GET /v1/throughput` reports it with the preview ready. */
const firstDownload = (extra = {}) => ({
  job_id: 254,
  appid: 264710,
  started_at: STARTED,
  bytes_per_second: 10 * MIB,
  bytes_since_start: 2 * GIB,
  exclusive_depots: 0,
  mapped_depots: 0,
  expected: true,
  expected_depots: 2,
  expected_size_bytes: 12 * GIB,
  expected_download_bytes: 8 * GIB,
  miss_bytes_per_second: 10 * MIB,
  eta_bytes_per_second: 10 * MIB,
  eta_speed_source: "live",
  remaining_bytes: 6 * GIB,
  remaining_source: "preview",
  ...extra,
});

/** The pre-JOB-SIZE-1 entry for the same job (preview off / failed / older server). */
const unknownDepots = { job_id: 254, appid: 264710, started_at: STARTED, bytes_per_second: null, bytes_since_start: null, exclusive_depots: 0, mapped_depots: 0 };

test("MUTATION TARGET -- JOB-SIZE-1: preview present: speed, '<so far> of ~<expected download>' and the expected-size mark", () => {
  const text = jobSpeedText(firstDownload({ remaining_bytes: null }), "ok", { nowMs: NOW });
  assert.equal(text, "10.0 MB/s · 2.0 GB of ~8.0 GB loaded so far (expected size)");
  assert.equal(EXPECTED_SIZE_NOTE, " (expected size)");
});

test("MUTATION TARGET -- JOB-SIZE-1: preview absent / oracle off keeps today's honest text (no expected fields)", () => {
  assert.equal(jobSpeedText(unknownDepots, "ok", { nowMs: NOW }), "Speed not measured yet: the server does not know this game's depots yet.");
  // The server says `expected: false` explicitly when the preview is off.
  const off = { ...unknownDepots, expected: false, expected_depots: 0, expected_size_bytes: null, expected_download_bytes: null, miss_bytes_per_second: null, remaining_bytes: null, remaining_source: null };
  assert.equal(jobSpeedText(off, "ok", { nowMs: NOW }), "Speed not measured yet: the server does not know this game's depots yet.");
  // No "of ~X" without `expected: true`, even if a size were present.
  const mapped = { ...firstDownload(), expected: false, remaining_bytes: null };
  assert.equal(jobSpeedText(mapped, "ok", { nowMs: NOW }), "10.0 MB/s · 2.0 GB loaded so far");
  // An expected entry without a download size: no "of", no mark.
  assert.equal(jobSpeedText(firstDownload({ expected_download_bytes: null, remaining_bytes: null }), "ok", { nowMs: NOW }), "10.0 MB/s · 2.0 GB loaded so far");
});

test("MUTATION TARGET -- JOB-SIZE-1: the speed line needs an ok feed also with a preview", () => {
  assert.equal(jobSpeedText(firstDownload(), "stale", { nowMs: NOW }), null);
});

test("MUTATION TARGET -- JOB-SIZE-1 ETA formula: remaining / the server's ETA speed (or the smoothed one)", () => {
  assert.equal(jobEtaSeconds(firstDownload(), { nowMs: NOW }), (6 * GIB) / (10 * MIB));
  // The MISS speed, not the card's total speed (HITs are cache speed).
  // The server's divisor, not the card's (swept, possibly diluted) speed.
  assert.equal(jobEtaSeconds(firstDownload({ bytes_per_second: 100 * MIB, miss_bytes_per_second: 1 * MIB, eta_bytes_per_second: 5 * MIB }), { nowMs: NOW }), (6 * GIB) / (5 * MIB));
  // A smoothed rate passed by the view wins over the entry's raw sample.
  assert.equal(jobEtaSeconds(firstDownload(), { nowMs: NOW, rate: 20 * MIB }), (6 * GIB) / (20 * MIB));
  assert.equal(jobEtaSeconds(firstDownload(), { nowMs: NOW, rate: null }), null, "smoother has no rate yet");
  const text = jobSpeedText(firstDownload(), "ok", { nowMs: NOW });
  assert.equal(text, "10.0 MB/s · 2.0 GB of ~8.0 GB loaded so far (expected size) · about 11 min left");
  // A mapped game with a completeness-based remainder gets an ETA too.
  const mapped = { ...firstDownload(), expected: false, remaining_source: "completeness", remaining_bytes: 3 * GIB };
  assert.equal(jobSpeedText(mapped, "ok", { nowMs: NOW }), "10.0 MB/s · 2.0 GB loaded so far · about 6 min left");
});

test("MUTATION TARGET -- JOB-SIZE-1 ETA hide rules", () => {
  const opts = { nowMs: NOW };
  assert.equal(jobEtaSeconds(null, opts), null);
  assert.equal(jobEtaSeconds(firstDownload({ remaining_bytes: null }), opts), null, "size unknown");
  assert.equal(jobEtaSeconds(firstDownload({ remaining_bytes: 0 }), opts), null, "nothing left");
  assert.equal(jobEtaSeconds(firstDownload({ eta_bytes_per_second: 0 }), opts), null, "no speed (cache-only read for a completeness remainder)");
  assert.equal(jobEtaSeconds(firstDownload({ eta_bytes_per_second: null }), opts), null, "speed unknown (no live tier, diluted window)");
  assert.equal(jobEtaSeconds(firstDownload({ started_at: null }), opts), null, "start unknown");
  // Unstable first seconds.
  const young = firstDownload({ started_at: new Date(NOW - JOB_ETA_MIN_AGE_MS + 1000).toISOString() });
  assert.equal(jobEtaSeconds(young, opts), null, "younger than 20 s");
  const settled = firstDownload({ started_at: new Date(NOW - JOB_ETA_MIN_AGE_MS).toISOString() });
  assert.notEqual(jobEtaSeconds(settled, opts), null, "20 s and older");
  // More than a day.
  assert.equal(JOB_ETA_MAX_SECONDS, 86_400);
  assert.equal(jobEtaSeconds(firstDownload({ eta_bytes_per_second: 1, remaining_bytes: JOB_ETA_MAX_SECONDS + 1 }), opts), null, "> 24 h");
  assert.equal(jobEtaSeconds(firstDownload({ eta_bytes_per_second: 1, remaining_bytes: JOB_ETA_MAX_SECONDS }), opts), JOB_ETA_MAX_SECONDS, "exactly 24 h");
  // The line drops only the ETA part.
  assert.equal(jobSpeedText(firstDownload({ eta_bytes_per_second: 0 }), "ok", opts), "10.0 MB/s · 2.0 GB of ~8.0 GB loaded so far (expected size)");
});

test("MUTATION TARGET -- JOB-SIZE-1 ETA never on a Verifying card", () => {
  const opts = { verifying: true, nowMs: NOW };
  assert.equal(jobEtaSeconds(firstDownload(), opts), null);
  const mapped = { ...firstDownload(), expected: false, remaining_bytes: 3 * GIB };
  assert.equal(jobSpeedText(mapped, "ok", opts), "10.0 MB/s · 2.0 GB loaded so far");
  assert.equal(jobSpeedText(unknownDepots, "ok", opts), VERIFY_SPEED_TEXT);
});

test("MUTATION TARGET -- JOB-SIZE-1 ETA wording is approximate and rounds up to minutes", () => {
  assert.equal(formatEta(1), "about 1 min left");
  assert.equal(formatEta(60), "about 1 min left");
  assert.equal(formatEta(61), "about 2 min left");
  assert.equal(formatEta(359), "about 6 min left");
  assert.equal(formatEta(3600), "about 1 h left");
  assert.equal(formatEta(3600 + 5 * 60), "about 1 h 5 min left");
  assert.equal(formatEta(0), null);
  assert.equal(formatEta(null), null);
  assert.equal(formatEta(Number.NaN), null);
});

test("JOB-SIZE-1: the tooltip says where an expected line's figures come from", () => {
  assert.equal(jobSpeedNote(firstDownload()), JOB_SPEED_EXPECTED_NOTE);
  assert.equal(jobSpeedNote({ ...firstDownload(), expected: false }), JOB_SPEED_NOTE);
  assert.equal(jobSpeedNote(undefined), JOB_SPEED_NOTE);
  assert.match(JOB_SPEED_EXPECTED_NOTE, /estimate/);
  // WP RUNNER-NAME-1: the runner is named by the product here too.
  assert.match(JOB_SPEED_EXPECTED_NOTE, /count SteamHangar's own prefill bytes/);
});

test("JOB-SIZE-1 wiring: the Downloads view sets the line's tooltip through jobSpeedNote", () => {
  const src = readFileSync(new URL("../js/views/downloads.js", import.meta.url), "utf8");
  assert.match(src, /line\.title = text \? jobSpeedNote\(entry\) : "";/);
});

test("JOB-SIZE-1: the demo keeps the old (mapped) job entry, so no golden changes", async () => {
  const { demoThroughput } = await import("../js/demo-traffic.js");
  const t = demoThroughput("24h", [{ id: 1, appid: 440, started_at: STARTED, name: "x" }], NOW);
  const [job] = t.jobs;
  assert.equal(job.expected, false);
  assert.equal(job.remaining_bytes, null);
  assert.equal(jobSpeedText(job, "ok", { nowMs: NOW }), "31.5 MB/s · 8.8 GB loaded so far");
});

test("MUTATION TARGET -- JOB-SIZE-1 EMA: one step per new answer, reset on no speed, forget gone jobs", () => {
  assert.equal(ETA_SMOOTHING, 0.3);
  assert.equal(smoothEtaRate(null, 100), 100);
  assert.equal(smoothEtaRate(100, 200), 130);
  assert.equal(smoothEtaRate(100, 0), null);
  assert.equal(smoothEtaRate(100, null), null);
  const smoother = new EtaSmoother();
  const answer = (rate, id = 1) => ({ jobs: [{ job_id: id, eta_bytes_per_second: rate }] });
  const a = answer(100);
  smoother.observe(a);
  assert.equal(smoother.rate(1), 100);
  smoother.observe(a); // the same answer repainted: no second step
  assert.equal(smoother.rate(1), 100);
  smoother.observe(answer(200));
  assert.equal(smoother.rate(1), 130);
  smoother.observe(answer(300));
  assert.equal(smoother.rate(1), 130 + 0.3 * 170);
  smoother.observe(answer(0));
  assert.equal(smoother.rate(1), null);
  smoother.observe(answer(50, 2));
  assert.equal(smoother.rate(1), null);
  assert.equal(smoother.rate(2), 50);
});

test("JOB-SIZE-1 wiring: the Downloads view feeds the smoothed rate into the speed line", () => {
  const src = readFileSync(new URL("../js/views/downloads.js", import.meta.url), "utf8");
  assert.match(src, /etaSmoother\.observe\(t\)/);
  assert.match(src, /etaRate: entry \? etaSmoother\.rate\(entry\.job_id\) : null/);
});
