/**
 * Unit tests for `web/js/lib/traffic-view.js` (WP D15c): speed and byte
 * formatting, the event-feed honesty rules, the Downloads header tiles, the
 * graph geometry, per-job speed, and the history range filter/merge.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  DASH,
  FEED_TEXT,
  emptyHistoryText,
  feedNote,
  feedState,
  filterJobsByRange,
  formatRate,
  formatSpeed,
  formatTrafficBytes,
  headerTiles,
  JOB_SPEED_NOTE,
  insideWindow,
  parseRateCap,
  rateCapLine,
  jobSpeedText,
  VERIFY_SPEED_TEXT,
  joinNotes,
  mergeJobLists,
  refreshFailedText,
  seriesGeometry,
  seriesLabel,
  seriesStartText,
} from "../js/lib/traffic-view.js";

const MIB = 1024 * 1024;
const GIB = 1024 * MIB;
const NOW = Date.parse("2026-10-13T20:10:00Z");

const feed = (state, extra = {}) => ({
  enabled: state !== "disabled",
  state,
  last_sweep_at: state === "never-swept" || state === "disabled" ? null : "2026-10-13T19:30:00Z",
  sweep_interval_minutes: 5,
  traffic_since: "2026-10-01T00:00:00Z",
  ...extra,
});
const throughput = (state, extra = {}) => ({
  event_feed: feed(state),
  range: "24h",
  tier: "5m",
  bucket_seconds: 300,
  range_start: "2026-10-12T20:10:00Z",
  active_window_minutes: 15,
  current: state === "ok" ? { bytes_per_second: 40 * MIB, window_start: "x", window_end: "y", window_seconds: 480, runner_bytes_per_second: 10 * MIB } : null,
  active_clients_now: state === "ok" ? 2 : null,
  summary:
    state === "disabled"
      ? null
      : {
          bytes_served: 60 * GIB, hit_bytes: 50 * GIB, miss_bytes: 10 * GIB, requests: 100, hits: 83, misses: 17, errors: 0,
          // Request rate and byte rate differ on purpose (the tile uses bytes).
          hit_rate: 0.5, byte_hit_rate: 0.833, average_bytes_per_second: 50 * MIB, peak_bytes_per_second: 100 * MIB, active_clients: 3,
        },
  jobs: [],
  ...extra,
});

test("formatSpeed: binary units, a real zero prints, null/negative is not a measurement", () => {
  assert.equal(formatSpeed(40 * MIB), "40.0 MB/s");
  assert.equal(formatSpeed(150 * MIB), "150 MB/s");
  assert.equal(formatSpeed(1.5 * GIB), "1.5 GB/s");
  assert.equal(formatSpeed(300 * 1024), "300 KB/s");
  assert.equal(formatSpeed(10), "< 1 KB/s");
  assert.equal(formatSpeed(0), "0 MB/s");
  assert.equal(formatSpeed(null), null);
  assert.equal(formatSpeed(-1), null);
  assert.equal(formatSpeed(Number.NaN), null);
});

test("formatTrafficBytes / formatRate", () => {
  assert.equal(formatTrafficBytes(0), "0 GB");
  assert.equal(formatTrafficBytes(10), "< 1 MB");
  assert.equal(formatTrafficBytes(312 * MIB), "312 MB");
  assert.equal(formatTrafficBytes(1.2 * GIB), "1.2 GB");
  assert.equal(formatTrafficBytes(null), null);
  assert.equal(formatRate(0.834), "83%");
  assert.equal(formatRate(0), "0%");
  assert.equal(formatRate(null), null);
});

test("MUTATION TARGET: feed state — anything unreadable counts as disabled (no numbers rather than invented ones)", () => {
  assert.equal(feedState(throughput("ok")), "ok");
  assert.equal(feedState(throughput("stale")), "stale");
  assert.equal(feedState(throughput("never-swept")), "never-swept");
  assert.equal(feedState({}), "disabled");
  assert.equal(feedState(null), "disabled");
  assert.equal(feedState({ event_feed: { state: "weird" } }), "disabled");
});

test("feed notes: none when ok; disabled names the setting; never-swept the interval; stale the age", () => {
  assert.equal(feedNote(throughput("ok"), NOW), null);
  assert.equal(feedNote(throughput("disabled"), NOW), FEED_TEXT.disabled);
  assert.match(FEED_TEXT.disabled, /VAULT_EVENT_LOG/);
  assert.equal(feedNote(throughput("never-swept"), NOW), FEED_TEXT.neverSwept(5));
  assert.match(feedNote(throughput("stale"), NOW), /last read the cache's event log 40 min ago\. Current values show a dash/);
  assert.equal(feedNote({ event_feed: feed("stale", { last_sweep_at: null }) }, NOW), FEED_TEXT.staleNever);
});

test("MUTATION TARGET: header tiles — disabled shows no tiles at all (never zeros)", () => {
  assert.equal(headerTiles(throughput("disabled")), null);
  assert.equal(headerTiles(null), null);
});

test("MUTATION TARGET: header tiles — ok shows every figure; stale dashes only the current values", () => {
  const ok = Object.fromEntries(headerTiles(throughput("ok")).map((t) => [t.key, t]));
  assert.deepEqual(
    Object.values(ok).map((t) => [t.label, t.value]),
    [
      ["Current speed", "40.0 MB/s"],
      ["Average while loading", "50.0 MB/s"],
      ["Peak speed", "100 MB/s"],
      ["Served (cache + prefill)", "60.0 GB"],
      ["Loaded from the internet", "10.0 GB"],
      ["Hit rate", "83%"],
      ["Saved by the cache", "50.0 GB"],
      ["Loading now", "2"],
    ],
  );
  assert.equal(ok.clients.detail, "machines with cache traffic in the last 15 min");
  assert.match(ok.clients.title, /3 machines loaded something in the last 24 hours/);
  assert.equal(ok.current.detail, "all traffic incl. prefill runner, 5-10 min window · 10.0 MB/s by the prefill runner");
  assert.equal(ok["hit-rate"].detail, "clients only, runner excluded");
  const stale = Object.fromEntries(headerTiles(throughput("stale")).map((t) => [t.key, t.value]));
  assert.equal(stale.current, DASH);
  assert.equal(stale.clients, DASH);
  assert.equal(stale.loaded, "60.0 GB", "stored totals still shown");
  const zero = throughput("ok", { active_clients_now: 0, summary: { ...throughput("ok").summary, average_bytes_per_second: null, byte_hit_rate: null } });
  const z = Object.fromEntries(headerTiles(zero).map((t) => [t.key, t.value]));
  assert.equal(z.clients, "0", "a real zero prints");
  assert.equal(z.average, DASH, "nothing loaded: no average");
  assert.equal(z["hit-rate"], DASH);
});

test("MUTATION TARGET: TILE-INTERNET — the tile is the server's all-scope miss bytes, runner included, with a fallback for older servers", () => {
  const base = throughput("ok");
  const tile = (summary) =>
    headerTiles(throughput("ok", { summary })).find((x) => x.key === "internet");
  const runner = { bytes_served: 4 * GIB, hit_bytes: 3 * GIB, miss_bytes: GIB, requests: 9, hits: 6, misses: 3, errors: 0 };
  assert.equal(tile({ ...base.summary, internet_bytes: 11 * GIB, prefill_runner: runner }).value, "11.0 GB");
  assert.equal(tile({ ...base.summary, prefill_runner: runner }).value, "11.0 GB", "older server: client misses + runner misses");
  assert.equal(tile(base.summary).value, "10.0 GB", "no runner figures: client misses");
  assert.equal(tile({ ...base.summary, internet_bytes: 0 }).value, "0 GB", "a real zero prints");
  assert.equal(tile({ ...base.summary, miss_bytes: undefined }).value, DASH);
  assert.match(tile(base.summary).title, /came from the internet, the server's own prefill downloads included/);
  assert.equal(headerTiles(throughput("ok")).length, 8, "2x4 grid");
});

test("MUTATION TARGET: the hit-rate tile is the BYTE hit rate, the base 'Saved by the cache' uses", () => {
  const t = headerTiles(throughput("ok")).find((x) => x.key === "hit-rate");
  assert.equal(t.value, "83%", "byte_hit_rate 0.833, not hit_rate 0.5");
  assert.equal(
    t.title,
    "Share of the bytes your machines loaded in the last 24 hours that came from the cache. The server's own prefill downloads are not counted.",
  );
  const noBytes = throughput("ok", { summary: { ...throughput("ok").summary, byte_hit_rate: null } });
  assert.equal(headerTiles(noBytes).find((x) => x.key === "hit-rate").value, DASH, "no fallback to the request rate");
});

test("MUTATION TARGET: D15-RUNNER — saved, hit rate and active clients say the runner is not counted; Loaded names its share", () => {
  const base = throughput("ok");
  const withRunner = throughput("ok", {
    summary: { ...base.summary, prefill_runner: { bytes_served: 4 * GIB, hit_bytes: 3 * GIB, miss_bytes: GIB, requests: 9, hits: 6, misses: 3, errors: 0 } },
  });
  const tiles = Object.fromEntries(headerTiles(withRunner).map((t) => [t.key, t]));
  assert.equal(tiles.loaded.detail, "4.0 GB by the prefill runner, 3.0 GB of that from the cache");
  assert.match(tiles.loaded.title, /only when the prefill runner had no cache hits/);
  assert.equal(tiles["hit-rate"].detail, "clients only, runner excluded");
  const rated = headerTiles(throughput("ok", { summary: { ...base.summary, prefill_runner: { ...withRunner.summary.prefill_runner, byte_hit_rate: 0.75 } } }));
  assert.equal(rated.find((t) => t.key === "hit-rate").detail, "clients only, runner excluded (runner: 75%)");
  assert.equal(JOB_SPEED_NOTE, "Exclusive depots only: bytes on depots that belong to this game alone, from every address.");
  assert.match(tiles.loaded.title, /prefill downloads included/);
  assert.match(tiles.saved.title, /prefill downloads are not counted/);
  assert.match(tiles["hit-rate"].title, /prefill downloads are not counted/);
  assert.match(tiles.clients.title, /prefill runner is not counted/);
  assert.doesNotMatch(tiles.clients.title, /count as one of them/);
  // Older server (no prefill_runner) or an idle runner: no detail line.
  assert.equal(Object.fromEntries(headerTiles(base).map((t) => [t.key, t])).loaded.detail, null);
  const idle = throughput("ok", { summary: { ...base.summary, prefill_runner: { ...withRunner.summary.prefill_runner, bytes_served: 0 } } });
  assert.equal(Object.fromEntries(headerTiles(idle).map((t) => [t.key, t])).loaded.detail, null);
});

test("refresh note helpers: 'Couldn't refresh: …' and notes joined without empties", () => {
  assert.equal(refreshFailedText("database is locked"), "Couldn't refresh: database is locked");
  assert.equal(joinNotes("a.", null, "", "b."), "a. b.");
  assert.equal(joinNotes(null, undefined), null);
});

test("graph geometry: scaled to the highest point, area closed along the bottom; one point is a flat line; none is null", () => {
  const g = seriesGeometry([{ bytes_per_second: 0 }, { bytes_per_second: 10 }, { bytes_per_second: 5 }], { width: 100, height: 40 });
  assert.equal(g.line, "M0 40 L50 0 L100 20");
  assert.equal(g.area, "M0 40 L50 0 L100 20 L100 40 L0 40 Z");
  assert.equal(g.maxBytesPerSecond, 10);
  assert.equal(seriesGeometry([{ bytes_per_second: 0 }, { bytes_per_second: 0 }], { width: 10, height: 4 }).line, "M0 4 L10 4");
  assert.equal(seriesGeometry([{ bytes_per_second: 3 }], { width: 10, height: 4 }).line, "M0 0 L10 0");
  assert.equal(seriesGeometry([], { width: 10, height: 4 }), null);
});

test("graph label and start caption", () => {
  assert.equal(seriesLabel({ range: "24h", bucket_seconds: 300 }, 40 * MIB), "Average speed per 5 minutes in the last 24 hours. Highest: 40.0 MB/s.");
  assert.equal(seriesLabel({ range: "7d", bucket_seconds: 3600 }, 0), "Average speed per hour in the last 7 days. Highest: 0 MB/s.");
  assert.equal(seriesStartText({ range: "30d" }), "30 days ago");
  assert.equal(seriesStartText({ range: "all", points: [{ t: "2026-09-01T00:00:00Z" }] }), "since 2026-09-01");
});

test("MUTATION TARGET: per-job speed only with the feed ok; unknown depots say why; null otherwise", () => {
  const entry = { job_id: 1, appid: 440, bytes_per_second: 30 * MIB, bytes_since_start: 8.8 * GIB, exclusive_depots: 2 };
  assert.equal(jobSpeedText(entry, "ok"), "30.0 MB/s · 8.8 GB loaded so far");
  assert.equal(jobSpeedText(entry, "stale"), null);
  assert.equal(jobSpeedText({ ...entry, bytes_per_second: null, bytes_since_start: null, exclusive_depots: 0 }, "ok"), "Speed not measured yet: the server does not know this game's depots yet.");
  assert.equal(jobSpeedText({ ...entry, bytes_per_second: null }, "ok"), null);
  // WP MAP-FROM-MANIFEST-1: depots mapped but all shared is not "unknown".
  const sharedOnly = { ...entry, bytes_per_second: null, bytes_since_start: null, exclusive_depots: 0, mapped_depots: 2 };
  assert.equal(jobSpeedText(sharedOnly, "ok"), "Speed not measured: this game's depots are shared with other apps.");
  assert.equal(jobSpeedText({ ...sharedOnly, mapped_depots: 0 }, "ok"), "Speed not measured yet: the server does not know this game's depots yet.");
  assert.equal(jobSpeedText(null, "ok"), null);
});

test("MUTATION TARGET: history range filter — in-flight jobs always stay, finished ones by finish time", () => {
  const jobs = [
    { id: 5, status: "running", created_at: "2026-01-01T00:00:00Z" },
    { id: 4, status: "done", finished_at: "2026-10-13T10:00:00Z" },
    { id: 3, status: "error", finished_at: "2026-10-10T10:00:00Z" },
    { id: 2, status: "cancelled", finished_at: null, started_at: null, created_at: "2026-09-01T00:00:00Z" },
    { id: 1, status: "done", finished_at: "garbage" },
  ];
  assert.deepEqual(filterJobsByRange(jobs, "24h", NOW).map((j) => j.id), [5, 4, 1]);
  assert.deepEqual(filterJobsByRange(jobs, "7d", NOW).map((j) => j.id), [5, 4, 3, 1]);
  assert.deepEqual(filterJobsByRange(jobs, "all", NOW).map((j) => j.id), [5, 4, 3, 2, 1]);
});

test("MUTATION TARGET: merge — the store's (fresher) copy wins, store rows first in their order, then the fetched rest", () => {
  const merged = mergeJobLists([{ id: 9, status: "done" }, { id: 7, status: "done" }], [{ id: 9, status: "running" }, { id: 6, status: "error" }, { id: 7, status: "error" }]);
  assert.deepEqual(merged.map((j) => `${j.id}:${j.status}`), ["9:done", "7:done", "6:error"]);
  assert.equal(emptyHistoryText("7d"), "Nothing finished in the last 7 days.");
  assert.equal(emptyHistoryText("all"), "Nothing finished yet.");
});

// WP RATE-TILE
const capSettings = (effective, extra = {}) => ({ settings: [{ key: "upstream_rate", effective }], core_apply: null, ...extra });
const MIN = 60_000;
// 2026-10-16 01:00 UTC = 03:00 on a UTC+02:00 server
const AT_0100Z = Date.UTC(2026, 9, 16, 1, 0);

test("RATE-TILE: parseRateCap reads digits with k/m and refuses everything else", () => {
  assert.equal(parseRateCap("8m"), 8 * 1048576);
  assert.equal(parseRateCap("800k"), 819200);
  assert.equal(parseRateCap("5000"), 5000);
  for (const bad of [null, undefined, "", "off", "0", "08m", "1.5m", "8mb", 8]) assert.equal(parseRateCap(bad), null, String(bad));
});

test("RATE-TILE: insideWindow uses the server clock, end exclusive, overnight wraps, unknown is null", () => {
  assert.equal(insideWindow("03:00-07:00", "UTC+02:00", AT_0100Z), true);
  assert.equal(insideWindow("03:00-07:00", "UTC+02:00", AT_0100Z - 1 * MIN), false, "02:59 server time");
  assert.equal(insideWindow("03:00-07:00", "UTC+02:00", AT_0100Z + 4 * 60 * MIN), false, "07:00 end is outside");
  assert.equal(insideWindow("22:00-06:00", "UTC", Date.UTC(2026, 9, 16, 23, 0)), true);
  assert.equal(insideWindow("22:00-06:00", "UTC", Date.UTC(2026, 9, 16, 12, 0)), false);
  assert.equal(insideWindow("03:00-07:00", "UTC-05:30", Date.UTC(2026, 9, 16, 8, 30)), true);
  assert.equal(insideWindow("03:00-07:00", "CEST", AT_0100Z), null);
  assert.equal(insideWindow("garbage", "UTC", AT_0100Z), null);
});

test("MUTATION TARGET: RATE-TILE rateCapLine never claims a cap that is not in force now", () => {
  const sched = { window: "03:00-07:00", server_timezone: "UTC+02:00" };
  assert.equal(rateCapLine(capSettings("8m"), sched, AT_0100Z), "Limit 8 MB/s, not in force now (inside the download window 03:00–07:00)");
  assert.equal(rateCapLine(capSettings("8m"), sched, AT_0100Z + 10 * 60 * MIN), "Limit 8 MB/s, in force now (outside the download window 03:00–07:00)");
  assert.equal(rateCapLine(capSettings("800k"), { window: null, server_timezone: "UTC" }, AT_0100Z), "Limit 800 KB/s, all day");
  assert.equal(rateCapLine(capSettings("8m"), null, AT_0100Z), "Limit 8 MB/s, outside the download window", "schedule unknown: no in-force claim");
  assert.equal(rateCapLine(capSettings("8m"), { window: "03:00-07:00", server_timezone: "?" }, AT_0100Z), "Limit 8 MB/s, outside the download window 03:00–07:00");
  assert.equal(rateCapLine(capSettings("8m", { core_apply: { pending: true } }), { window: null }, AT_0100Z), "Limit 8 MB/s, all day (change not applied yet)");
  assert.equal(rateCapLine(capSettings(null), sched, AT_0100Z), "No limit");
  assert.equal(rateCapLine(capSettings("off"), sched, AT_0100Z), "No limit");
  assert.equal(rateCapLine(capSettings("???"), sched, AT_0100Z), null, "unreadable value: no line");
  assert.equal(rateCapLine({ settings: [] }, sched, AT_0100Z), null);
  assert.equal(rateCapLine(null, sched, AT_0100Z), null);
});

test("RATE-TILE: headerTiles puts the cap line on Current speed only", () => {
  const tiles = headerTiles(throughput("ok"), "No limit");
  assert.deepEqual(tiles.map((t) => t.limit), ["No limit", null, null, null, null, null, null, null]);
  assert.equal(headerTiles(throughput("ok")).find((t) => t.key === "current").limit, null);
});

test("MUTATION TARGET -- DOWNLOADS-1 VERIFY-SPEED-TEXT-1: a Verifying card reads 'Verifying files, no download speed' unless a speed was measured", () => {
  assert.equal(VERIFY_SPEED_TEXT, "Verifying files, no download speed");
  assert.equal(jobSpeedText(undefined, "disabled", { verifying: true }), VERIFY_SPEED_TEXT);
  const unmeasured = { job_id: 1, appid: 440, bytes_per_second: null, bytes_since_start: null, exclusive_depots: 0 };
  assert.equal(jobSpeedText(unmeasured, "ok", { verifying: true }), VERIFY_SPEED_TEXT);
  assert.equal(jobSpeedText(unmeasured, "ok"), "Speed not measured yet: the server does not know this game's depots yet.");
  const measured = { ...unmeasured, bytes_per_second: 30 * MIB, bytes_since_start: 8.8 * GIB, exclusive_depots: 2 };
  assert.equal(jobSpeedText(measured, "ok", { verifying: true }), "30.0 MB/s · 8.8 GB loaded so far");
});
