/**
 * DOM-wiring pins for WP D15c in `web/js/views/downloads.js`: the
 * statistics header (`GET /v1/throughput` + `/v1/throughput/series`), the
 * running job's speed line (D13 SPEED-1), and the History range filter
 * (`GET /v1/jobs?range=`):
 *
 *   - feed ok: eight tiles with the server's numbers, an inline-SVG graph
 *     (role=img, labelled), the running job's speed on its card;
 *   - feed disabled: the note only — no tiles, no graph, no speed line;
 *   - feed stale: the note, a dash (aria-hidden, sr-only "Not available")
 *     for the current speed, stored totals kept;
 *   - the statistics range refetches both endpoints; an answer for the old
 *     range is dropped (gated fetch);
 *   - the History filter fetches `?range=` with limit 200, shows fetched
 *     rows the store's poll does not hold, hides older finished jobs, and
 *     has a range-worded empty line;
 *   - a 404 (server older than D15b) shows the update note and stops the
 *     poll; leaving the view stops it too.
 *
 * Harness: fake-dom.js, a routing `fetch` fake, the real store-singleton
 * stopped in `after()`. Assertions compare text, never nodes.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, after, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { createFakeDom } from "./fake-dom.js";

const dom = createFakeDom();
globalThis.document = dom.document;
// Downloads now defaults to the live range; these tests fake the persisted
// endpoints, so they start from a stored "6h" choice (which must keep working).
const storage = new Map([["steamvault.apiKey", "test-key"], ["steamvault.downloadsRange", "6h"]]);
globalThis.window = {
  localStorage: {
    getItem: (k) => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => storage.set(k, String(v)),
  },
  location: { origin: "http://vault.test", pathname: "/downloads", reload() {} },
  history: { pushState() {} },
  addEventListener() {},
  removeEventListener() {},
};

globalThis.localStorage = globalThis.window.localStorage; // what readStoredStatsRange reads

const MIB = 1024 ** 2;
const GIB = 1024 ** 3;
const iso = (msAgo) => new Date(Date.now() - msAgo).toISOString().replace(/\.\d{3}Z$/, "Z");
const HOUR = 3_600_000;
const job = (id, status, finishedAgo) => ({
  id, appid: 10 + id, type: "prefill", status, stop_request: null,
  created_at: iso(finishedAgo + 60_000), started_at: iso(finishedAgo + 30_000), finished_at: status === "running" ? null : iso(finishedAgo),
});

function feed(state) {
  return { enabled: state !== "disabled", state, last_sweep_at: state === "disabled" ? null : iso(40 * 60_000), sweep_interval_minutes: 5, traffic_since: null };
}
function throughput(range, state) {
  const off = state === "disabled";
  return {
    event_feed: feed(state), range, tier: "5m", bucket_seconds: 300, range_start: iso(24 * HOUR), active_window_minutes: 15,
    current: state === "ok" ? { bytes_per_second: 40 * MIB, window_start: iso(8 * 60_000), window_end: iso(0), window_seconds: 480 } : null,
    active_clients_now: state === "ok" ? 2 : null,
    summary: off ? null : {
      bytes_served: 60 * GIB, hit_bytes: 50 * GIB, miss_bytes: 10 * GIB, requests: 100, hits: 83, misses: 17, errors: 0,
      // Request and byte rates differ on purpose: the tile shows the byte rate.
      hit_rate: 0.5, byte_hit_rate: 50 / 60, average_bytes_per_second: 50 * MIB, peak_bytes_per_second: 100 * MIB, active_clients: 3,
    },
    jobs: [{ job_id: 1, appid: 11, name: null, started_at: iso(HOUR), bytes_per_second: state === "ok" ? 30 * MIB : null, bytes_since_start: state === "ok" ? 8.8 * GIB : null, exclusive_depots: 1 }],
  };
}
function series(range, state) {
  return {
    event_feed: feed(state), range, tier: "5m", bucket_seconds: 300, range_start: iso(3 * 300_000), // the first point: nothing is trimmed
    points: state === "disabled" ? [] : (server.flat ? [0, 0, 0] : [0, 10, 5]).map((v, i) => ({ t: iso((3 - i) * 300_000), bytes_served: v, hit_bytes: 0, miss_bytes: v, requests: 1, bytes_per_second: v * MIB, peak_bytes_per_second: v * MIB })),
  };
}

const server = {
  state: "ok",
  flat: false,
  status: 200,
  gate: null,
  calls: [],
  // The store's poll (newest jobs): one running, one finished 2 h ago, one 3 days ago.
  jobs: [job(1, "running", 0), job(2, "done", 2 * HOUR), job(3, "error", 72 * HOUR)],
  // Only the ?range= history fetch knows job 4 (older than the store's window).
  deep: [job(4, "done", 5 * HOUR)],
};
function respond(status, data) {
  const text = JSON.stringify(data);
  return { ok: status >= 200 && status < 300, status, json: async () => data, text: async () => text };
}
globalThis.fetch = async (url) => {
  const u = new URL(String(url));
  const p = u.pathname;
  const range = u.searchParams.get("range");
  if (p === "/v1/throughput" || p === "/v1/throughput/series") {
    server.calls.push(`${p}?${range}`);
    if (server.gate) await server.gate;
    if (server.status !== 200) return respond(server.status, { detail: server.status === 404 ? "Not Found" : "database is locked" });
    return respond(200, p === "/v1/throughput" ? throughput(range, server.state) : series(range, server.state));
  }
  if (p === "/v1/jobs") {
    if (range) {
      server.calls.push(`/v1/jobs?${range}&${u.searchParams.get("limit")}`);
      const cutoff = { "24h": 24 * HOUR, "7d": 168 * HOUR, "30d": 720 * HOUR }[range];
      const all = [...server.jobs, ...server.deep];
      return respond(200, cutoff ? all.filter((j) => !j.finished_at || Date.now() - Date.parse(j.finished_at) <= cutoff) : all);
    }
    return respond(200, server.jobs);
  }
  if (p === "/v1/settings") return respond(200, { readonly: false, server_version: "t", settings: [{ key: "upstream_rate", effective: "8m" }], core_apply: null });
  if (p === "/v1/schedule") return respond(200, { enabled: false, window: null, server_timezone: "UTC" });
  if (p === "/v1/games" || p === "/v1/clients") return respond(200, []);
  if (p === "/v1/cache/summary") return respond(200, {});
  return respond(404, { detail: "Not Found" });
};

const { store } = await import("../js/store-singleton.js");
const { renderDownloads } = await import("../js/views/downloads.js");
const { navigateTo } = await import("../js/router.js");

after(() => {
  store.stop();
  navigateTo("library");
});

const tick = (ms = 10) => new Promise((r) => setTimeout(r, ms));
async function until(pred, label, timeoutMs = 3000) {
  const start = Date.now();
  while (!pred()) {
    if (Date.now() - start > timeoutMs) throw new Error(`timed out waiting for: ${label}`);
    await tick(5);
  }
}
const q = (s, role) => s.querySelector(`[data-role="${role}"]`);
const tiles = (s) =>
  q(s, "stats-grid").querySelectorAll(".stat").map((t) => `${t.querySelector("dt").textContent}=${t.querySelector("dd").textContent}`);
const tile = (s, key) => q(s, "stats-grid").querySelectorAll(".stat").find((t) => t.getAttribute("data-stat") === key);
const seg = (s, role, r) => q(s, role).querySelectorAll("button").find((b) => b.getAttribute("data-range") === r);
const historyIds = (s) => s.querySelectorAll(".hrow").map((r) => r.getAttribute("data-jid")).join(",");
const speed = (s) => {
  const line = s.querySelector('.jobcard[data-jid="1"]')?.querySelector('[data-role="job-speed"]');
  return line && !line.hidden ? line.textContent : null;
};

async function mount() {
  navigateTo("library"); // stop the previous mount's poll
  store.start();
  const s = renderDownloads();
  dom.appRoot.replaceChildren(s);
  await until(() => s.querySelector('.jobcard[data-jid="1"]') !== null, "jobs painted");
  return s;
}

beforeEach(() => {
  server.state = "ok";
  server.flat = false;
  server.status = 200;
  server.gate = null;
  server.calls = [];
});

test("MUTATION TARGET: feed ok — eight tiles, a labelled SVG graph, and the running job's speed", async () => {
  const s = await mount();
  await until(() => !q(s, "stats-grid").hidden, "tiles");
  await until(() => tiles(s)[0]?.includes("Limit 8 MB/s"), "cap line");
  assert.deepEqual(tiles(s), [
    "Current speed=40.0 MB/sall traffic incl. prefill runner, 5-10 min windowLimit 8 MB/s, all day",
    "Average while loading=50.0 MB/s",
    "Peak speed=100 MB/s",
    "Served (cache + prefill)=60.0 GB",
    "Loaded from the internet=10.0 GB",
    "Hit rate=83%clients only, runner excluded",
    "Saved by the cache=50.0 GB",
    "Loading now=2machines with cache traffic in the last 15 min",
  ]);
  assert.equal(q(s, "stats-note").hidden, true);
  const svg = q(s, "graph-svg");
  assert.equal(svg.tagName, "SVG");
  assert.equal(svg.getAttribute("role"), "img");
  assert.equal(svg.getAttribute("aria-label"), "Average speed per 5 minutes in the last 6 hours. Highest: 10.0 MB/s."); // stored range: 6 h (the default is live since LIVE-DELAY-1)
  assert.equal(svg.children[1].getAttribute("d"), "M0 60 L150 0 L300 30");
  assert.equal(q(s, "stats-graph").querySelector(".tg-axis").textContent, "6 h agohighest 10.0 MB/snow");
  await until(() => speed(s) !== null, "speed line");
  assert.equal(speed(s), "30.0 MB/s · 8.8 GB loaded so far");
  assert.equal(q(s, "stats-range").getAttribute("aria-label"), "Statistics range");
  assert.equal(q(s, "stats-range").hidden, false, "range selector shown with figures");
});

test("MUTATION TARGET: an all-zero series draws no graph; the tiles stay", async () => {
  server.flat = true;
  const s = await mount();
  await until(() => !q(s, "stats-grid").hidden, "tiles");
  await tick(20);
  assert.equal(q(s, "stats-graph").hidden, true);
  assert.equal(q(s, "stats-graph").querySelectorAll("path").length, 0);
});

test("MUTATION TARGET: a failed refresh after an answer keeps the figures and adds a 'Couldn't refresh' note", async () => {
  let s = await mount();
  // The previous test's flat series is still module state: wait for this
  // mount's own answer (a drawn graph) before failing the next one.
  await until(() => !q(s, "stats-grid").hidden && !q(s, "stats-graph").hidden, "tiles and graph");
  server.status = 500;
  s = await mount(); // a re-mount refreshes at once; the old answer is still held
  await until(() => /^Couldn't refresh: /.test(q(s, "stats-note").textContent), "refresh note");
  assert.equal(q(s, "stats-note").hidden, false);
  assert.equal(q(s, "stats-grid").hidden, false, "old tiles stay");
  assert.equal(tile(s, "loaded").querySelector("dd").textContent, "60.0 GB");
  assert.equal(q(s, "stats-graph").hidden, false, "old graph stays");
  server.status = 200;
  s = await mount();
  await until(() => q(s, "stats-note").hidden === true, "note gone after a good answer");
});

test("MUTATION TARGET: feed disabled — the note only: no tiles, no graph, no speed line", async () => {
  server.state = "disabled";
  const s = await mount();
  await until(() => /^Traffic statistics are off/.test(q(s, "stats-note").textContent), "disabled note");
  assert.equal(q(s, "stats-note").hidden, false);
  assert.equal(q(s, "stats-grid").hidden, true);
  assert.equal(q(s, "stats-grid").querySelectorAll(".stat").length, 0);
  assert.equal(q(s, "stats-graph").hidden, true);
  assert.equal(speed(s), null);
  assert.equal(q(s, "stats-range").hidden, true, "no figures, no range selector");
});

test("feed stale — note, a dash for the current speed (sr: Not available), totals kept, no job speed", async () => {
  server.state = "stale";
  const s = await mount();
  await until(() => !q(s, "stats-grid").hidden, "tiles");
  assert.match(q(s, "stats-note").textContent, /out of date/);
  const cur = tile(s, "current").querySelector("dd");
  assert.equal(cur.children[0].getAttribute("aria-hidden"), "true");
  assert.equal(cur.children[0].textContent, "—");
  assert.equal(cur.querySelector(".sr-only").textContent, "Not available");
  assert.equal(tile(s, "loaded").querySelector("dd").textContent, "60.0 GB");
  assert.equal(speed(s), null);
});

test("MUTATION TARGET: the statistics range refetches both endpoints; a late answer for the old range is dropped", async () => {
  const s = await mount();
  await until(() => !q(s, "stats-grid").hidden, "tiles");
  let release;
  server.gate = new Promise((r) => (release = r));
  server.calls = [];
  seg(s, "stats-range", "7d").dispatchEvent({ type: "click" });
  await until(() => server.calls.length === 2, "7d requests held");
  assert.equal(q(s, "stats-note").textContent, "Loading statistics…");
  seg(s, "stats-range", "30d").dispatchEvent({ type: "click" });
  let release30;
  server.gate = new Promise((r) => (release30 = r));
  release(); // the 7d answers land after the user left 7d
  await until(() => server.calls.length === 4, "30d requests issued");
  await tick(20);
  assert.equal(q(s, "stats-grid").hidden, true, "the 7d answer must not paint");
  server.gate = null;
  release30();
  await until(() => !q(s, "stats-grid").hidden, "30d painted");
  assert.deepEqual(server.calls.slice().sort(), ["/v1/throughput/series?30d", "/v1/throughput/series?7d", "/v1/throughput?30d", "/v1/throughput?7d"]);
  assert.match(tile(s, "clients").title, /in the last 30 days/);
  seg(s, "stats-range", "24h").dispatchEvent({ type: "click" });
  await until(() => /in the last 24 hours/.test(tile(s, "clients")?.title || ""), "back to 24h");
});

test("MUTATION TARGET: History filter — default All with the fetched rows; 24 h fetches ?range=24h&limit=200 and hides older jobs", async () => {
  const s = await mount();
  assert.equal(seg(s, "history-range", "all").getAttribute("aria-pressed"), "true", "All is the default");
  await until(() => historyIds(s) === "2,3,4", "store rows plus the fetched job 4");
  seg(s, "history-range", "24h").dispatchEvent({ type: "click" });
  await until(() => server.calls.includes("/v1/jobs?24h&200"), "range fetch");
  await until(() => historyIds(s) === "2,4", "3-day-old job 3 hidden");
  const saved = server.jobs;
  const savedDeep = server.deep;
  server.jobs = [job(1, "running", 0), job(3, "error", 72 * HOUR)];
  server.deep = [];
  try {
    store.refreshNow();
    seg(s, "history-range", "7d").dispatchEvent({ type: "click" });
    await until(() => historyIds(s) === "3", "7d shows job 3");
    seg(s, "history-range", "24h").dispatchEvent({ type: "click" });
    await until(() => s.querySelectorAll(".hrow").length === 0, "nothing in 24 h");
    assert.ok(s.querySelectorAll("p.hint").some((p) => p.textContent === "Nothing finished in the last 24 hours."));
  } finally {
    server.jobs = saved;
    server.deep = savedDeep;
    seg(s, "history-range", "all").dispatchEvent({ type: "click" });
    store.refreshNow();
  }
});

test("MUTATION TARGET: a 404 shows the update note and stops the poll; leaving the view stops it too", async () => {
  server.status = 404;
  let s = await mount();
  await until(() => q(s, "stats-note").textContent === "This server does not report traffic statistics yet. Update vault-api to see them.", "unsupported note");
  assert.equal(q(s, "stats-range").hidden, true, "no range selector for an old server");
  const n = server.calls.length;
  await tick(60);
  assert.equal(server.calls.length, n, "no retries after a 404");
  server.status = 200;
  s = await mount();
  await until(() => !q(s, "stats-grid").hidden, "a new visit asks again");
  navigateTo("library");
  const m = server.calls.length;
  await tick(60);
  assert.equal(server.calls.length, m, "no requests after leaving");
});
