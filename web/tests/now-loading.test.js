/**
 * WP NOW-LOADING-1: the Downloads hero says WHAT is loading. The server's
 * `loading_sources` (`now.loading_sources` on `/v1/throughput`, the same
 * rows on `/v1/throughput/live`) become a "Loading now" block in the hero,
 * one row per source: the prefill runner with its job and phase (in the
 * pill's words), each device as the Clients page names it with its games,
 * and "Unattributed traffic". No block (no empty heading) while nothing
 * loads, with a feed that is not `ok`, or from an older server.
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
  loadingNowModel,
  createDownloadsHero,
  LOADING_NOW_LABEL,
  LOADING_UNATTRIBUTED,
  LOADING_NEW_DEVICE,
  LOADING_NO_JOB,
  PILL_DOWNLOADING,
  PILL_VERIFYING,
} = await import("../js/components/downloads-hero.js");
const { mergeNow, mergeLive } = await import("../js/lib/traffic-view.js");
const { demoRequest, resetDemoData } = await import("../js/demo-data.js");

const here = path.dirname(fileURLToPath(import.meta.url));
const MIB = 1024 ** 2;
const feed = { enabled: true, state: "ok", last_sweep_at: "2026-10-18T11:58:07Z", sweep_interval_minutes: 5, traffic_since: null };

beforeEach(() => resetDemoData());

function source(kind, fields = {}) {
  return {
    kind,
    key: kind === "runner" ? "runner" : kind === "unattributed" ? "unattributed" : "addr:10.0.0.50",
    name: kind === "runner" ? "Prefill runner" : kind === "unattributed" ? null : "10.0.0.50",
    addresses: kind === "unattributed" ? [] : ["10.0.0.50"],
    known: true,
    bytes_per_second: 10 * MIB,
    hit_bytes_per_second: 0,
    miss_bytes_per_second: 10 * MIB,
    apps: [],
    unattributed_bytes_per_second: 0,
    phase: kind === "runner" ? "download" : null,
    job: null,
    ...fields,
  };
}

const RUNNER_DL = source("runner", {
  apps: [{ appid: 275850, name: "No Man's Sky", bytes_per_second: 30 * MIB }],
  bytes_per_second: 30 * MIB,
  miss_bytes_per_second: 30 * MIB,
  job: { job_id: 200, appid: 275850, name: "No Man's Sky", stop_request: null },
});

function current(total, runner, hit) {
  return {
    bytes_per_second: total,
    runner_bytes_per_second: runner,
    runner_hit_bytes_per_second: hit,
    runner_miss_bytes_per_second: runner - hit,
    window_start: "2026-10-18T11:59:50Z",
    window_end: "2026-10-18T12:00:07Z",
    window_seconds: 17,
  };
}

/** A persisted-range answer whose `now` block carries `sources`. */
function answer(sources, { cur = current(30 * MIB, 30 * MIB, 0), activeClients = 0, state = "ok" } = {}) {
  return {
    event_feed: { ...feed, state },
    range: "1h",
    tier: "1m",
    bucket_seconds: 60,
    range_start: "2026-10-18T11:01:00Z",
    active_window_minutes: 15,
    current: cur,
    active_clients_now: activeClients,
    runner_active_now: cur.runner_bytes_per_second > 0,
    summary: { bytes_served: 0, hit_bytes: 0, miss_bytes: 0, byte_hit_rate: null, average_bytes_per_second: null, peak_bytes_per_second: 0, active_clients: 0, prefill_runner: null },
    jobs: [],
    now: state === "ok"
      ? { current: cur, active_clients: activeClients, runner_active: cur.runner_bytes_per_second > 0, window_seconds: 17, live_since: null, loading_sources: sources }
      : null,
  };
}

const rowsOf = (sources, opts) => loadingNowModel(mergeNow(answer(sources, opts))).rows;

// ---------------------------------------------------------------------------
// The model
// ---------------------------------------------------------------------------

test("a downloading runner names its job's game and the pill's word", () => {
  const [row] = rowsOf([RUNNER_DL]);
  assert.equal(row.kind, "runner");
  // WP RUNNER-NAME-1: titled by the product, not the server's "Prefill runner".
  assert.equal(row.name, "SteamHangar");
  assert.equal(row.detail, `this server · No Man's Sky · job #200 · ${PILL_DOWNLOADING}`);
  assert.equal(row.speed, "30.0 MB/s");
});

test("a verifying runner reads Verifying, like the pill", () => {
  const verify = { ...RUNNER_DL, phase: "verify", hit_bytes_per_second: 30 * MIB, miss_bytes_per_second: 0 };
  const t = mergeNow(answer([verify], { cur: current(30 * MIB, 30 * MIB, 30 * MIB) }));
  const [row] = loadingNowModel(t).rows;
  assert.ok(row.detail.endsWith(PILL_VERIFYING), row.detail);
  assert.ok(!row.detail.includes(PILL_DOWNLOADING));
  assert.equal(heroStatus(t.current).text, PILL_VERIFYING);
});

test("runner traffic without a running job says so (the cancel case)", () => {
  const orphan = { ...RUNNER_DL, job: null };
  const t = mergeNow(answer([orphan]));
  const [row] = loadingNowModel(t).rows;
  assert.equal(row.detail, `this server · ${LOADING_NO_JOB} · No Man's Sky · ${PILL_DOWNLOADING}`);
  // The pill still says Downloading: the row explains it.
  assert.equal(heroStatus(t.current).text, PILL_DOWNLOADING);
  const bare = rowsOf([{ ...orphan, apps: [] }])[0];
  assert.equal(bare.detail, `this server · ${LOADING_NO_JOB} · ${PILL_DOWNLOADING}`);
});

test("a pending cancel is shown on the runner row", () => {
  const stopping = { ...RUNNER_DL, job: { ...RUNNER_DL.job, stop_request: "cancel" } };
  assert.match(rowsOf([stopping])[0].detail, /cancel requested$/);
});

test("two devices with their games, named as on the Clients page", () => {
  const pc = source("client", {
    key: "agent:pc-one", name: "Gaming PC", addresses: ["10.0.0.20"],
    bytes_per_second: 12 * MIB, hit_bytes_per_second: 12 * MIB, miss_bytes_per_second: 0,
    apps: [{ appid: 1, name: "Game One", bytes_per_second: 8 * MIB }, { appid: 2, name: "Game Two", bytes_per_second: 4 * MIB }],
  });
  const laptop = source("client", {
    key: "addr:10.0.0.21", name: "Laptop", addresses: ["10.0.0.21"],
    bytes_per_second: 4 * MIB, hit_bytes_per_second: 1 * MIB, miss_bytes_per_second: 3 * MIB,
    apps: [{ appid: 3, name: null, bytes_per_second: 4 * MIB }],
  });
  const rows = rowsOf([pc, laptop], { cur: current(16 * MIB, 0, 0), activeClients: 2 });
  assert.deepEqual(rows.map((r) => r.name), ["Gaming PC", "Laptop"]);
  assert.equal(rows[0].detail, "Game One, Game Two · from the cache");
  assert.equal(rows[0].speed, "12.0 MB/s");
  // A game without a name is "Unknown app", never a bare id; a mixed split
  // says how much came from the cache.
  assert.equal(rows[1].detail, "Unknown app · 25% from the cache");
});

test("a device loading only from the internet, and one without a linked game", () => {
  const [row] = rowsOf([source("client", { name: "Laptop" })]);
  assert.equal(row.detail, "content not linked to one game · from the internet");
});

test("an unknown address is a New device with its address", () => {
  const [row] = rowsOf([source("client", { known: false, apps: [{ appid: 9, name: "Game Nine", bytes_per_second: 1 }] })]);
  assert.equal(row.name, LOADING_NEW_DEVICE);
  assert.equal(row.detail, "10.0.0.50 · Game Nine · from the internet");
  // Missing `known` (never false) keeps the server's name.
  const plain = source("client");
  delete plain.known;
  assert.equal(rowsOf([plain])[0].name, "10.0.0.50");
});

test("traffic no device explains reads Unattributed traffic", () => {
  const rows = rowsOf([RUNNER_DL, source("unattributed", { bytes_per_second: 2 * MIB })]);
  assert.deepEqual(rows.map((r) => r.kind), ["runner", "unattributed"]);
  assert.equal(rows[1].name, LOADING_UNATTRIBUTED);
  assert.equal(rows[1].speed, "2.0 MB/s");
});

test("no block while nothing loads, with a feed that is not ok, or from an older server", () => {
  assert.equal(loadingNowModel(mergeNow(answer([]))), null);
  assert.equal(loadingNowModel(mergeNow(answer([RUNNER_DL], { state: "stale" }))), null);
  const old = answer([RUNNER_DL]);
  delete old.now.loading_sources;
  assert.equal(loadingNowModel(mergeNow(old)), null);
  assert.equal(loadingNowModel(null), null);
  // Feed state is checked on the answer itself, too.
  assert.equal(loadingNowModel({ event_feed: { ...feed, state: "never-swept" }, loading_sources: [RUNNER_DL] }), null);
  // Unknown kinds are skipped, not invented.
  assert.equal(loadingNowModel(mergeNow(answer([{ kind: "mystery", bytes_per_second: 1 }]))), null);
});

test("the live view takes the rows from the live answer only", () => {
  // Rows of another window on the summary (its `now`, or a merged copy)
  // must never leak into the live view.
  const stale = [source("client", { name: "Stale summary row" })];
  const summary = { ...answer(stale), loading_sources: stale };
  const live = { event_feed: feed, current: current(30 * MIB, 30 * MIB, 0), active_clients: 0, active_window_seconds: 17, loading_sources: [RUNNER_DL] };
  const merged = mergeLive(summary, live);
  assert.deepEqual(loadingNowModel(merged).rows.map((r) => r.name), ["SteamHangar"]);
  // A live answer without the field (older server): no block, never the
  // summary's rows of another window.
  const older = { ...live };
  delete older.loading_sources;
  assert.equal(loadingNowModel(mergeLive(summary, older)), null);
  // A live answer whose feed is not ok: no block.
  assert.equal(loadingNowModel(mergeLive(summary, { ...live, event_feed: { ...feed, state: "stale" } })), null);
});

test("heroModel carries the block next to the pill and the device count", () => {
  const pc = source("client", { name: "Gaming PC", hit_bytes_per_second: 10 * MIB, miss_bytes_per_second: 0 });
  const model = heroModel({ throughput: mergeNow(answer([RUNNER_DL, pc], { cur: current(40 * MIB, 30 * MIB, 0), activeClients: 1 })) });
  assert.equal(model.figures.status.text, PILL_DOWNLOADING);
  assert.ok(model.figures.caption.join("").includes("1 device loading"));
  assert.equal(model.figures.loadingNow.label, LOADING_NOW_LABEL);
  assert.deepEqual(model.figures.loadingNow.rows.map((r) => r.kind), ["runner", "client"]);
});

// ---------------------------------------------------------------------------
// The DOM
// ---------------------------------------------------------------------------

function paintWith(hero, sources, opts) {
  hero.paint({ throughput: mergeNow(answer(sources, opts)), series: null, range: "1h", rateCap: null, unsupported: false, error: null, noFigures: false });
}

test("the block is a labelled list, one item per source, hidden when idle", () => {
  const hero = createDownloadsHero({ range: "1h", onRangeChange() {} });
  const block = () => hero.el.querySelector('[data-role="stats-loading-now"]');
  paintWith(hero, [RUNNER_DL, source("unattributed", { bytes_per_second: MIB })]);
  assert.equal(block().hidden, false);
  const list = block().querySelector("ul");
  assert.equal(list.getAttribute("role"), "list");
  assert.equal(list.getAttribute("aria-label"), LOADING_NOW_LABEL);
  const items = list.querySelectorAll("li");
  assert.equal(items.length, 2);
  assert.equal(items[0].dataset.kind, "runner");
  assert.ok(items[0].textContent.includes("SteamHangar"));
  assert.ok(items[0].textContent.includes("this server · "));
  assert.ok(!items[0].textContent.includes("Prefill runner"));
  assert.ok(items[0].textContent.includes("No Man's Sky"));
  assert.ok(items[0].textContent.includes("30.0 MB/s"));
  assert.equal(items[1].dataset.kind, "unattributed");
  assert.ok(items[1].textContent.includes(LOADING_UNATTRIBUTED));
  // The visible label is a duplicate of the list's name for screen readers.
  assert.equal(block().querySelector(".lk").getAttribute("aria-hidden"), "true");

  // Idle: no block, no empty heading, no stale rows.
  paintWith(hero, [], { cur: current(0, 0, 0) });
  assert.equal(block().hidden, true);
  assert.equal(block().querySelectorAll("li").length, 0);

  // Back to loading, then the feed goes away: hidden again.
  paintWith(hero, [RUNNER_DL]);
  assert.equal(block().hidden, false);
  hero.paint({ throughput: null, series: null, range: "1h", rateCap: null, unsupported: false, error: null, noFigures: false });
  assert.equal(block().hidden, true);
  assert.equal(block().querySelectorAll("li").length, 0);
});

test("the block sits in the live grid, so a hidden block takes no hero row", () => {
  const hero = createDownloadsHero({ range: "1h", onRangeChange() {} });
  const live = hero.el.querySelector('[data-role="stats-live"]');
  assert.equal(live.querySelector('[data-role="stats-loading-now"]').parentNode, live);
});

test("kit.css styles the block and guards its [hidden]", () => {
  const css = readFileSync(path.join(here, "..", "css", "kit.css"), "utf8");
  assert.match(css, /\.kit \.live \.lnow\{[^}]*grid-column:1 \/ -1/);
  assert.match(css, /\.kit \.live \.lnow\[hidden\]/);
  const kit = readFileSync(path.join(here, "..", "..", "docs", "design", "rc20", "kit.html"), "utf8");
  assert.match(kit, /\.live \.lnow\{grid-column:1\/-1/);
});

/** The invariant the demo shows: rows add up to the big number, the
 * runner row matches the pill and names the running job, one device row per
 * device the caption counts. */
function assertDemoInvariant(merged, label) {
  const sources = merged.loading_sources;
  assert.ok(Array.isArray(sources) && sources.length > 0, `${label}: demo rows`);
  const sum = sources.reduce((acc, r) => acc + r.bytes_per_second, 0);
  assert.ok(Math.abs(sum - merged.current.bytes_per_second) < 1, `${label}: rows add up to current`);
  const runner = sources.filter((r) => r.kind === "runner");
  assert.equal(runner.length, merged.current.runner_bytes_per_second > 0 ? 1 : 0, label);
  if (runner.length) {
    assert.ok(Math.abs(runner[0].bytes_per_second - merged.current.runner_bytes_per_second) < 1, label);
    const word = heroStatus(merged.current).text;
    assert.equal(runner[0].phase === "verify" ? PILL_VERIFYING : PILL_DOWNLOADING, word, `${label}: row agrees with the pill`);
  }
  assert.equal(sources.filter((r) => r.kind === "client").length, merged.active_clients_now, `${label}: device rows = device count`);
  const model = loadingNowModel(merged);
  assert.equal(model.rows.length, sources.length, label);
  return { runner: runner[0] || null, model };
}

test("the demo shows the block, consistent with the pill, the speed and the device count", async () => {
  // The seeded demo has a running prefill job.
  const running = (await demoRequest("GET", "/v1/jobs", { params: {} })).filter((j) => j.status === "running" && j.type === "prefill");
  assert.equal(running.length, 1, "the seeded demo runs one prefill job");
  for (const range of ["1h", "6h", "24h", "7d"]) {
    const merged = mergeNow(await demoRequest("GET", "/v1/throughput", { params: { range } }));
    const { runner, model } = assertDemoInvariant(merged, range);
    assert.ok(runner && runner.job, `${range}: the runner row names the running demo job`);
    assert.equal(runner.job.job_id, running[0].id);
    assert.equal(model.rows[0].kind, "runner");
    assert.ok(model.rows[0].detail.includes(`job #${runner.job.job_id}`));
    assert.ok(model.rows.some((r) => r.kind === "client" && r.detail.includes("% from the cache")));
  }
  const summary = await demoRequest("GET", "/v1/throughput", { params: { range: "1h" } });
  const live = await demoRequest("GET", "/v1/throughput/live", {});
  assertDemoInvariant(mergeLive(summary, live), "live");
});
