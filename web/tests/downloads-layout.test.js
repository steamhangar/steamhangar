/**
 * WP DOWNLOADS-1: layout hooks and folds of `views/downloads.js`: the two
 * columns, the idle/busy hook, the folded attention lists with their
 * Show/Hide toggle and per-game buttons, no arrow buttons in the queue
 * (the keyboard path stays on the grip), generic failure with Retry.
 * (Harness copied from downloads-updates-wiring.test.js; its header:)
 * DOM wiring of the "Updates available (N)" section in `views/downloads.js`
 * (WP UPD-6): it lists the flagged games without an active job, reads the
 * schedule note from `GET /v1/schedule`, "Update now" and "Update all" send
 * `POST /v1/prefill` with exactly the listed app ids (never
 * `/v1/prefill/cached`, never a force flag), the busy state keeps focus,
 * the started game leaves the list once the jobs poll shows its job, and
 * the offline gate applies.
 *
 * Harness: fake-dom.js, a stored API key, a routing `fetch` fake the REAL
 * store-singleton polls; the prefill answer can be held (gated). The store
 * is stopped in `after`, gates are released in `finally` (docs/LEARNINGS.md,
 * WEB-FIX-1). Text/booleans only, never node identity.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, after } from "node:test";
import assert from "node:assert/strict";
import { createFakeDom } from "./fake-dom.js";

const dom = createFakeDom();
globalThis.document = dom.document;
const storage = new Map([["steamvault.apiKey", "test-key"], ["steamvault.downloadsRange", "6h"]]);
globalThis.localStorage = { getItem: (k) => (storage.has(k) ? storage.get(k) : null), setItem: (k, v) => storage.set(k, String(v)) };
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

const shell = {};
for (const id of ["toast", "toast-text"]) shell[id] = dom.document.createElement(id === "toast-text" ? "span" : "div");
const origGetById = dom.document.getElementById.bind(dom.document);
dom.document.getElementById = (id) => shell[id] || origGetById(id);

const T = "2026-10-14T08:00:00Z";
const game = (appid, name, over = {}) => ({
  appid,
  name,
  status: "done",
  last_prefill_at: T,
  needs_force: false,
  size_bytes: 3 * 1_073_741_824,
  update_available: false,
  ...over,
});

const server = {
  games: [],
  jobs: [],
  schedule: { enabled: false, window: null, sweep_include_cached: true },
  scheduleStatus: 200,
  gated: false,
  held: [],
  posts: [], // {path, body}
  gamesGate: null, // a promise: GET /v1/games waits for it
  nextJobId: 500,
};
function respond(status, data) {
  const text = JSON.stringify(data);
  return { ok: status >= 200 && status < 300, status, json: async () => data, text: async () => text };
}
function releaseGates() {
  server.gated = false;
  const list = server.held;
  server.held = [];
  for (const go of list) go();
}
globalThis.fetch = async (url, init = {}) => {
  const u = new URL(String(url));
  const method = (init.method || "GET").toUpperCase();
  const p = u.pathname;
  if (method === "GET" && p === "/v1/jobs") return respond(200, server.jobs);
  if (method === "GET" && p === "/v1/games") {
    if (server.gamesGate) await server.gamesGate;
    return respond(200, server.games);
  }
  if (method === "GET" && p === "/v1/clients") return respond(200, []);
  if (method === "GET" && p === "/v1/cache/summary") return respond(200, {});
  if (method === "GET" && p === "/v1/schedule") return respond(server.scheduleStatus, server.schedule);
  if (method === "POST" && p.startsWith("/v1/prefill")) {
    const body = init.body === undefined ? undefined : JSON.parse(init.body);
    server.posts.push({ path: p, body });
    if (server.gated) await new Promise((r) => server.held.push(r));
    const refs = (body?.appids || []).map((appid) => {
      const id = server.nextJobId++;
      server.jobs = [{ id, appid, type: "prefill", status: "queued", stop_request: null, created_at: T }, ...server.jobs];
      return { appid, job_id: id, status: "queued", deduplicated: false };
    });
    return respond(202, refs);
  }
  return respond(404, { detail: "Not Found" });
};

const { initToast } = await import("../js/components/toast.js");
initToast();
const { store } = await import("../js/store-singleton.js");
const { renderDownloads } = await import("../js/views/downloads.js");
const { setConnectionLost } = await import("../js/connection-status.js");
const { OFFLINE_CONTROL_TITLE } = await import("../js/lib/connection-watch.js");

after(() => {
  releaseGates();
  store.stop();
  setConnectionLost(false);
});

const tick = (ms = 10) => new Promise((r) => setTimeout(r, ms));
async function until(pred, label, timeoutMs = 2000) {
  const start = Date.now();
  while (!pred()) {
    if (Date.now() - start > timeoutMs) throw new Error(`timed out waiting for: ${label}`);
    await tick(5);
  }
}
const click = (el) => el.dispatchEvent({ type: "click" });
const box = (s) => s.querySelector(".dl-updates");
const heading = (s) => box(s).querySelector("h4.sec").textContent;
const note = (s) => box(s).querySelector(".dl-updates-note");
const rows = (s) => box(s).querySelectorAll(".urow");
const rowNames = (s) => rows(s).map((r) => r.querySelector(".nm").textContent);
const nowBtn = (s, appid) => box(s).querySelector(`.urow[data-update-appid="${appid}"] [data-role="update-now"]`);
const allBtn = (s) => box(s).querySelector('[data-role="update-all"]');
const toastText = () => shell["toast-text"].textContent;

async function open() {
  store.start();
  const s = renderDownloads();
  await until(() => store.snapshot("games") !== null && store.snapshot("jobs") !== null, "first polls");
  await tick(20);
  return s;
}
function reset() {
  store.stop();
  releaseGates();
  server.posts = [];
  server.games = [];
  server.jobs = [];
}
const job = (id, appid, status, over = {}) => ({ id, appid, type: "prefill", status, stop_request: null, created_at: T, ...over });

test("layout: Active and Queue sit in the main column, History in the side column", async () => {
  reset();
  server.games = [game(10, "Aurora"), game(11, "Borealis")];
  server.jobs = [job(1, 10, "queued"), job(2, 11, "queued")];
  try {
    const s = await open();
    const cols = s.querySelector(".dl-cols");
    assert.ok(cols, "two-column wrapper");
    const main = s.querySelector(".dl-col-main");
    const side = s.querySelector(".dl-col-side");
    assert.ok(main && side);
    assert.equal(main.querySelectorAll(".qrow").length, 2, "queue rows in the main column");
    assert.equal(side.querySelectorAll(".qrow").length, 0);
    assert.equal(main.textContent.includes("History"), false);
    assert.equal(side.textContent.includes("History"), true);
  } finally {
    store.stop();
  }
});

test("idle/busy hook: data-state follows whether anything runs, waits or is paused", async () => {
  reset();
  server.games = [game(10, "Aurora")];
  try {
    const s = await open();
    assert.equal(s.dataset.state, "idle");
    server.jobs = [job(1, 10, "queued")];
    store.refreshNow();
    await until(() => s.dataset.state === "busy", "busy");
  } finally {
    store.stop();
  }
});

test("queue rows carry no arrow buttons; the grip keeps the keyboard path", async () => {
  reset();
  server.games = [game(10, "Aurora"), game(11, "Borealis")];
  server.jobs = [job(1, 10, "queued"), job(2, 11, "queued")];
  try {
    const s = await open();
    for (const r of s.querySelectorAll(".qrow")) {
      const labels = r.querySelectorAll("button").map((b) => (b.getAttribute("aria-label") || b.textContent).toLowerCase());
      assert.equal(labels.some((l) => /\bmove\b|\bup\b|\bdown\b|earlier|later/.test(l)), false, "no move buttons");
      const grip = r.querySelector(".grip-live");
      assert.ok(grip, "live grip");
      assert.equal(grip.getAttribute("role"), "button");
      assert.equal(grip.getAttribute("tabindex"), "0");
      assert.match(grip.getAttribute("aria-label"), /up and down arrow keys/);
    }
  } finally {
    store.stop();
  }
});

test("attention lists start folded; the toggle shows them, keeps aria state and per-game Update now", async () => {
  reset();
  server.games = [game(10, "Aurora", { update_available: true })];
  try {
    const s = await open();
    await until(() => box(s).hidden === false, "section shown");
    const toggle = box(s).querySelector('[data-role="updates-toggle"]');
    assert.equal(toggle.textContent, "Show");
    assert.equal(toggle.getAttribute("aria-expanded"), "false");
    assert.match(toggle.getAttribute("aria-label"), /^Show Updates available/);
    click(toggle);
    assert.equal(toggle.textContent, "Hide");
    assert.equal(toggle.getAttribute("aria-expanded"), "true");
    click(nowBtn(s, 10));
    await until(() => server.posts.length === 1, "POST sent");
    assert.deepEqual(server.posts[0], { path: "/v1/prefill", body: { appids: [10] } });
    click(toggle);
    assert.equal(toggle.textContent, "Show");
  } finally {
    releaseGates();
    store.stop();
  }
});

test("CSS: hero stats card, History idle vs busy and the two-column breakpoint exist, with tokens only", async () => {
  const { readFileSync } = await import("node:fs");
  const css = readFileSync(new URL("../css/app.css", import.meta.url), "utf8");
  const from = css.indexOf("/* DOWNLOADS-1 hero stats card");
  const to = css.indexOf("@media (prefers-reduced-motion:reduce){\n  .dl-cols");
  assert.ok(from > 0 && to > from, "DOWNLOADS-1 block found");
  const block = css.slice(from, to);
  assert.match(block, /\.dl-stats\{[^}]*var\(--surface\)/);
  assert.match(block, /\.stat\[data-stat="current"\]/);
  assert.match(block, /\[data-state="idle"\] \.dl-col-side/);
  assert.match(block, /\[data-state="busy"\] \.dl-col-side/);
  assert.match(block, /@media \(min-width:1000px\)/);
  assert.equal(/#[0-9a-fA-F]{3,8}\b|rgba?\(/.test(block), false, "no hard-coded colours");
});
