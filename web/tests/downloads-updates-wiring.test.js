/**
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
const storage = new Map([["steamvault.apiKey", "test-key"]]);
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
  server.jobs = [];
  server.scheduleStatus = 200;
  server.schedule = { enabled: false, window: null, sweep_include_cached: true };
  shell["toast-text"].textContent = "";
}

test("MUTATION PIN: lists the flagged games (name, size), heading count, between Paused and Queue; hidden when none", async () => {
  reset();
  server.games = [
    game(30, "Zephyr Run", { update_available: true }),
    game(10, "Aurora", { update_available: true }),
    game(20, "Current One"),
  ];
  try {
    const s = await open();
    await until(() => box(s).hidden === false, "section shown");
    assert.equal(heading(s), "Updates available (2)");
    assert.deepEqual(rowNames(s), ["Aurora", "Zephyr Run"]);
    assert.equal(rows(s)[0].querySelector(".usize").textContent, "3.0 GB");
    assert.equal(nowBtn(s, 10).textContent, "Update now");
    assert.equal(nowBtn(s, 10).getAttribute("aria-label"), "Update now — Aurora");
    assert.equal(allBtn(s).textContent, "Update all");
    assert.equal(allBtn(s).getAttribute("aria-label"), "Update all 2 games");
    const order = s.children.map((c) => c.className || c.tagName);
    const at = order.indexOf("dl-updates");
    assert.ok(at > 0, "in the section");
    assert.equal(s.children[at + 1].textContent.startsWith("Queue"), true, "directly above the Queue heading");

    server.games = server.games.map((g) => ({ ...g, update_available: false }));
    store.refreshNow();
    await until(() => box(s).hidden === true, "flags cleared: section hidden");
  } finally {
    store.stop();
  }
});

test("schedule note: manual only without a window; the window when one is set; nothing when the read fails", async () => {
  reset();
  server.games = [game(10, "Aurora", { update_available: true })];
  try {
    let s = await open();
    await until(() => note(s).hidden === false, "note shown");
    assert.equal(note(s).textContent, "Manual only — no schedule window is set.");
    store.stop();

    server.schedule = { enabled: true, window: "22:00-06:00", sweep_include_cached: true };
    s = await open();
    await until(() => /22:00–06:00/.test(note(s).textContent), "window note");
    assert.equal(note(s).textContent, "These download automatically in the schedule window 22:00–06:00.");
    store.stop();

    server.scheduleStatus = 500;
    s = await open();
    await tick(40);
    assert.equal(note(s).hidden, true, "unknown schedule: no claim either way");
    assert.equal(box(s).hidden, false, "the list itself still shows");
  } finally {
    store.stop();
  }
});

test("MUTATION PIN: 'Update now' sends POST /v1/prefill with exactly that appid (not /cached, no force), busy keeps focus, the game moves to the queue", async () => {
  reset();
  server.games = [game(10, "Aurora", { update_available: true }), game(30, "Zephyr Run", { update_available: true })];
  try {
    const s = await open();
    await until(() => rows(s).length === 2, "two rows");
    server.gated = true;
    const btn = nowBtn(s, 10);
    btn.focus();
    click(btn);
    await until(() => server.posts.length === 1, "POST sent");
    assert.deepEqual(server.posts[0], { path: "/v1/prefill", body: { appids: [10] } });
    // Busy: the pressed row says so; every update button is aria-disabled, never `disabled`.
    assert.equal(nowBtn(s, 10).textContent, "Starting…");
    assert.equal(nowBtn(s, 10), btn, "MUTATION PIN: busy is patched in place, the pressed button is not rebuilt");
    assert.equal(dom.document.activeElement, btn, "focus stays on the pressed button while busy");
    assert.equal(nowBtn(s, 10).getAttribute("aria-disabled"), "true");
    assert.equal(nowBtn(s, 10).disabled === true, false);
    assert.equal(allBtn(s).getAttribute("aria-disabled"), "true");
    click(allBtn(s));
    click(nowBtn(s, 30));
    await tick(20);
    assert.equal(server.posts.length, 1, "MUTATION PIN: nothing else is sent while one request is out");
    releaseGates();
    await until(() => /queued for update/.test(toastText()), "toast");
    assert.equal(toastText(), "1 queued for update");
    await until(() => rowNames(s).join() === "Zephyr Run", "the started game left the list once its job showed");
    assert.equal(heading(s), "Updates available (1)");
    assert.equal(dom.document.activeElement, allBtn(s), "MUTATION PIN: the pressed row is gone, focus moved to 'Update all'");
    assert.ok(s.querySelector('.qrow .nm[data-appid="10"]'), "and sits in the Queue");
    assert.equal(nowBtn(s, 30).getAttribute("aria-disabled"), null, "idle again");
  } finally {
    releaseGates();
    store.stop();
  }
});

test("MUTATION PIN: 'Update all' sends every listed appid in one POST /v1/prefill, skipping games that already have a job", async () => {
  reset();
  server.games = [
    game(10, "Aurora", { update_available: true }),
    game(20, "Basalt", { update_available: true }),
    game(30, "Cinder", { update_available: true }),
  ];
  server.jobs = [{ id: 1, appid: 20, type: "prefill", status: "paused", stop_request: null, created_at: T }];
  try {
    const s = await open();
    await until(() => rows(s).length === 2, "the paused one is not listed");
    allBtn(s).focus();
    click(allBtn(s));
    await until(() => server.posts.length === 1, "POST sent");
    assert.deepEqual(server.posts[0], { path: "/v1/prefill", body: { appids: [10, 30] } });
    await until(() => box(s).hidden === true, "all moved into the queue");
    assert.equal(toastText(), "2 queued for update");
    const queueHeading = s.children.find((c) => c.tagName === "H4" && c.textContent.startsWith("Queue"));
    assert.equal(dom.document.activeElement, queueHeading, "MUTATION PIN: section gone, focus on the Queue heading");
    assert.equal(queueHeading.tabIndex, -1, "focusable by script only");
  } finally {
    store.stop();
  }
});

test("MUTATION PIN: a new job alone (games poll still out) takes the game out of the list", async () => {
  reset();
  server.games = [game(10, "Aurora", { update_available: true }), game(30, "Zephyr Run", { update_available: true })];
  let release;
  try {
    const s = await open();
    await until(() => rows(s).length === 2, "two rows");
    server.gamesGate = new Promise((r) => (release = r));
    server.jobs = [{ id: 7, appid: 30, type: "prefill", status: "queued", stop_request: null, created_at: T }];
    store.refreshNow();
    await until(() => rowNames(s).join() === "Aurora", "jobs tick alone removed Zephyr Run");
  } finally {
    server.gamesGate = null;
    if (release) release();
    store.stop();
  }
});

test("offline: the update buttons are disabled with the offline title", async () => {
  reset();
  server.games = [game(10, "Aurora", { update_available: true })];
  try {
    const s = await open();
    await until(() => rows(s).length === 1, "row");
    setConnectionLost(true);
    await until(() => nowBtn(s, 10).disabled === true, "gated");
    assert.equal(nowBtn(s, 10).title, OFFLINE_CONTROL_TITLE);
    assert.equal(allBtn(s).disabled, true);
    setConnectionLost(false);
    await until(() => nowBtn(s, 10).disabled !== true, "live again");
    assert.equal(allBtn(s).disabled === true, false);
  } finally {
    setConnectionLost(false);
    store.stop();
  }
});
