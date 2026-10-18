/**
 * WP MOBILE-1b (docs/design/rc21/mobile.html 3d, operator decision Q4 Weg A):
 * on a phone-wide screen (matchMedia "(max-width:599px)") a tap on a
 * History row opens the row's panel in a bottom sheet (header from the row,
 * the log, Retry in the footer next to Close) instead of under the row;
 * wider screens keep the hanging panel.
 * (Harness copied from downloads-layout.test.js, plus a matchMedia switch
 * and GET /v1/jobs/{id}.)
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
  // WP MOBILE-1b: the phone switch of views/downloads.js (max-width:599px).
  matchMedia: (q) => ({ matches: phone && q === "(max-width:599px)", addEventListener() {}, removeEventListener() {} }),
};
let phone = false;

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
  const one = /^\/v1\/jobs\/(\d+)$/.exec(p);
  if (method === "GET" && one) return respond(200, { ...server.jobs.find((j) => String(j.id) === one[1]), log_excerpt: "LOG-LINE-FROM-SERVER" });
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

const failed = () => job(900, 10, "error", { finished_at: T, error: "SteamPrefill exited 1" });
const histSheet = () => dom.document.body.querySelectorAll(".sheet-backdrop").find((b) => b.querySelector('[data-role="history-sheet-panel"]'));
const rowOf = (s, id) => s.querySelectorAll(".hitem").find((i) => i.querySelector(".hrow") && i.textContent.includes("Glass Meridian"));

test("phone (Q4): a tap on a History row opens its panel in a bottom sheet, not under the row; Close closes it", async () => {
  reset();
  phone = true;
  try {
    server.games = [game(10, "Glass Meridian", { status: "failed" })];
    server.jobs = [failed()];
    const s = await open();
    await until(() => rowOf(s), "history row");
    const item = rowOf(s);
    const hrow = item.querySelector(".hrow");
    assert.equal(hrow.getAttribute("aria-haspopup"), "dialog", "a phone row opens a dialog");
    assert.equal(hrow.getAttribute("aria-expanded"), null, "no stale aria-expanded on a phone row");
    click(hrow);
    const bd = histSheet();
    assert.ok(bd, "the History sheet exists");
    assert.ok(bd.classList.contains("on"), "the sheet is open");
    const sheet = bd.querySelector(".sheet");
    assert.ok(sheet.classList.contains("kit") && sheet.classList.contains("hist-sheet"));
    assert.equal(sheet.getAttribute("role"), "dialog");
    assert.equal(sheet.getAttribute("aria-label"), "Glass Meridian", "named by the row's game");
    assert.ok(sheet.querySelector(".hs-head .nm"), "the row's name is the sheet header");
    assert.equal(item.classList.contains("open"), false, "the row itself does not expand on a phone");
    await until(() => sheet.textContent.includes("LOG-LINE-FROM-SERVER"), "log excerpt in the sheet");
    const foot = sheet.querySelector(".sfoot");
    const labels = foot.querySelectorAll("button").map((b) => b.textContent);
    assert.equal(labels[0], "Close");
    assert.ok(labels.includes("Retry"), `Retry in the footer: ${labels}`);
    assert.equal(sheet.querySelector('[data-role="history-sheet-panel"] .failhint .acts'), null, "Retry moved out of the panel");
    click(foot.querySelectorAll("button").find((b) => b.textContent === "Close"));
    assert.equal(bd.classList.contains("shown"), false, "Close closes the sheet");
    await tick(5);
    const live = rowOf(s).querySelector(".hrow");
    assert.equal(dom.document.activeElement, live, "focus returns to the live row (re-queried by job id)");
  } finally {
    phone = false;
  }
});

test("MUTATION PIN: wider than 599px the row expands in place and no sheet opens", async () => {
  reset();
  phone = false;
  server.games = [game(10, "Glass Meridian", { status: "failed" })];
  server.jobs = [failed()];
  const s = await open();
  await until(() => rowOf(s), "history row");
  assert.equal(rowOf(s).querySelector(".hrow").getAttribute("aria-haspopup"), null);
  assert.equal(rowOf(s).querySelector(".hrow").getAttribute("aria-expanded"), "false");
  click(rowOf(s).querySelector(".hrow"));
  await until(() => rowOf(s).classList.contains("open") || rowOf(s).querySelector(".hpanel:not([hidden])"), "row expanded");
  assert.equal(histSheet() ? histSheet().classList.contains("shown") : false, false, "no sheet on a wide screen");
});
