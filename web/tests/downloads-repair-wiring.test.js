/**
 * WP CACHE-REPAIR-UI-1 — "Repair" on the Downloads "Incomplete" rows
 * (`views/downloads.js` + `components/repair-flow.js`), end to end against
 * the REAL store: the button only on a damaged-only row (not on a
 * completable one, not on a not-owned one, which says why instead), the
 * dry run -> confirm -> execute requests, the queued job showing up and
 * "Show job" scrolling to and focusing its Queue row, and the error path
 * (409 on the dry run: a toast, no dialog, the row stays).
 *
 * Harness as downloads-incomplete-wiring.test.js: fake-dom.js, a stored API
 * key, a routing `fetch` fake the store polls. Text/booleans only.
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

const T = "2026-10-31T08:00:00Z";
const GB = 1_073_741_824;
const MB = 1_048_576;
const game = (appid, name, over = {}) => ({
  appid,
  name,
  status: "done",
  last_prefill_at: T,
  needs_force: false,
  size_bytes: 3 * GB,
  update_available: false,
  cache_complete_pct: 100,
  cache_expected_bytes: 3 * GB,
  cache_present_bytes: 3 * GB,
  cache_complete_checked_at: T,
  cache_missing_chunks: 0,
  group_cache_complete_pct: 100,
  ...over,
});
const partial = (appid, name, pct, missing, over = {}) =>
  game(appid, name, { cache_complete_pct: pct, cache_missing_chunks: missing, ...over });

const server = {
  games: [],
  jobs: [],
  posts: [], // {path, body}
  repairConflict: null, // a 409 detail for the dry run
  nextJobId: 900,
};
function respond(status, data) {
  const text = JSON.stringify(data);
  return { ok: status >= 200 && status < 300, status, json: async () => data, text: async () => text };
}
function repairReport(appid, execute) {
  return {
    appid,
    mode: execute ? "execute" : "dry-run",
    execute,
    damaged_chunks: 4,
    damaged_bytes: 6 * MB,
    depots: [{ depotid: appid * 10 + 1, manifestid: "5", damaged_chunks: 4, damaged_bytes: 6 * MB }],
    unreadable_depots: [],
    exceeds_cap: false,
    max_files: 2000,
    removed_chunks: 0,
    removed_bytes: 0,
    skipped: {},
    job_id: null,
    job_status: null,
    job_deduplicated: null,
  };
}
globalThis.fetch = async (url, init = {}) => {
  const u = new URL(String(url));
  const method = (init.method || "GET").toUpperCase();
  const p = u.pathname;
  if (method === "GET" && p === "/v1/jobs") return respond(200, server.jobs);
  if (method === "GET" && p === "/v1/games") return respond(200, server.games);
  if (method === "GET" && p === "/v1/clients") return respond(200, []);
  if (method === "GET" && p === "/v1/cache/summary") return respond(200, {});
  if (method === "GET" && p === "/v1/schedule") return respond(200, { enabled: false, window: null, sweep_include_cached: true });
  const m = p.match(/^\/v1\/cache\/(\d+)\/repair$/);
  if (method === "POST" && m) {
    const appid = Number(m[1]);
    const body = init.body === undefined ? undefined : JSON.parse(init.body);
    server.posts.push({ path: p, body });
    if (server.repairConflict) return respond(409, { detail: server.repairConflict });
    if (!body || body.execute !== true) return respond(200, repairReport(appid, false));
    const id = server.nextJobId++;
    server.jobs = [{ id, appid, type: "prefill", status: "queued", stop_request: null, created_at: T }, ...server.jobs];
    server.games = server.games.map((g) => (g.appid === appid ? { ...g, cache_missing_chunks: 4, needs_force: true } : g));
    return respond(202, { ...repairReport(appid, true), removed_chunks: 4, removed_bytes: 6 * MB, job_id: id, job_status: "queued", job_deduplicated: false });
  }
  if (method === "POST") server.posts.push({ path: p, body: init.body === undefined ? undefined : JSON.parse(init.body) });
  return respond(404, { detail: "Not Found" });
};

const { initToast } = await import("../js/components/toast.js");
initToast();
const { store } = await import("../js/store-singleton.js");
const { renderDownloads, highlightJob } = await import("../js/views/downloads.js");
const { closeRepair } = await import("../js/components/repair-flow.js");

after(() => store.stop());

const tick = (ms = 10) => new Promise((r) => setTimeout(r, ms));
async function until(pred, label, timeoutMs = 2000) {
  const start = Date.now();
  while (!pred()) {
    if (Date.now() - start > timeoutMs) throw new Error(`timed out waiting for: ${label}`);
    await tick(5);
  }
}
const click = (el) => el.dispatchEvent({ type: "click" });
const box = (s) => s.querySelector(".dl-incomplete");
const rowFor = (s, appid) => box(s).querySelector(`.urow[data-incomplete-appid="${appid}"]`);
const repairBtn = (s, appid) => box(s).querySelector(`.urow[data-incomplete-appid="${appid}"] [data-role="repair"]`);
const dlg = () => dom.document.body.querySelector('[data-role="repair-dialog"]');
const part = (r) => dlg().querySelector(`[data-role="${r}"]`);
const dialogOpen = () => !!dlg() && dlg().parentNode.classList.contains("on");
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
  closeRepair();
  server.posts = [];
  server.jobs = [];
  server.repairConflict = null;
  shell["toast-text"].textContent = "";
}

test("MUTATION PIN: Repair only on the damaged-only row; a not-owned one says why instead", async () => {
  reset();
  server.games = [
    partial(20, "Aurora", 86, 3),
    partial(40, "Broken Bits", 99, 0),
    partial(50, "Not Mine", 97, 0, { status: "not_owned" }),
  ];
  try {
    const s = await open();
    await until(() => box(s).hidden === false, "section shown");
    assert.equal(repairBtn(s, 20), null, "a completable row gets Verify, not Repair");
    assert.ok(rowFor(s, 20).querySelector('[data-role="complete-now"]'));
    assert.equal(rowFor(s, 20).querySelector('[data-role="damaged-note"]'), null);

    assert.equal(repairBtn(s, 40).textContent, "Repair");
    assert.equal(repairBtn(s, 40).getAttribute("aria-label"), "Repair — Broken Bits");
    assert.equal(rowFor(s, 40).querySelector('[data-role="damaged-note"]').textContent, "Damaged chunks — Verify can't fix these, Repair can.");

    assert.equal(repairBtn(s, 50), null, "not owned: the server would answer 409");
    assert.equal(
      rowFor(s, 50).querySelector('[data-role="damaged-note"]').textContent,
      "Damaged chunks — can't be repaired: this Steam account doesn't own the game.",
    );
    assert.equal(server.posts.length, 0, "nothing sent by rendering");
  } finally {
    store.stop();
  }
});

test("MUTATION PIN: dry run -> confirm -> execute -> the job shows in the Queue and 'Show job' focuses it", async () => {
  reset();
  server.games = [partial(40, "Broken Bits", 99, 0)];
  // A second queued job, so the Queue's reorder handles are live.
  server.jobs = [{ id: 5, appid: 77, type: "prefill", status: "queued", stop_request: null, created_at: T }];
  const proto = Object.getPrototypeOf(dom.document.body);
  const scrolled = [];
  proto.scrollIntoView = function scrollIntoView() {
    scrolled.push(this);
  };
  try {
    const s = await open();
    await until(() => repairBtn(s, 40) !== null, "row shown");
    repairBtn(s, 40).focus();
    click(repairBtn(s, 40));
    await until(() => dialogOpen(), "confirm open");
    assert.deepEqual(server.posts, [{ path: "/v1/cache/40/repair", body: undefined }], "dry run first, no body");
    assert.equal(dlg().querySelector("h3").textContent, "Repair Broken Bits?");
    assert.match(part("repair-text").textContent, /^4 files \(6\.0 MB\) in the cache have the wrong size\./);
    await until(() => repairBtn(s, 40).textContent === "Repair", "the row's busy label is gone once the dialog is up");

    click(part("repair-ok"));
    await until(() => part("repair-ok").textContent === "Show job", "result");
    assert.deepEqual(server.posts[1], { path: "/v1/cache/40/repair", body: { execute: true } });
    assert.equal(part("repair-text").textContent, "Deleted 4 files (6.0 MB). Job #900 fetches fresh copies.");

    // The store re-reads: the game leaves Incomplete (it has a job now).
    await until(() => box(s).hidden === true, "left the Incomplete list");
    await until(() => s.querySelector('.qrow[data-jid="900"]') !== null, "queued job shown");

    click(part("repair-ok"));
    assert.equal(dialogOpen(), false);
    const qrow = s.querySelector('.qrow[data-jid="900"]');
    assert.ok(scrolled.includes(qrow), "scrolled to the job's Queue row");
    assert.equal(dom.document.activeElement, qrow.querySelector(".grip-live"), "its reorder handle has focus");
  } finally {
    delete proto.scrollIntoView;
    store.stop();
  }
});

test("MUTATION PIN: error path — a 409 on the dry run is a toast; no dialog, no execute, the row stays", async () => {
  reset();
  server.games = [partial(40, "Broken Bits", 99, 0)];
  server.repairConflict = "Job 3 for app 41 is running; repairing chunk files under an active job would race it.";
  try {
    const s = await open();
    await until(() => repairBtn(s, 40) !== null, "row shown");
    click(repairBtn(s, 40));
    await until(() => toastText() !== "", "toast");
    assert.equal(
      toastText(),
      "A download that uses these files is queued, running or paused. Repair once it has finished — nothing was deleted.",
    );
    assert.equal(dialogOpen(), false);
    assert.deepEqual(server.posts, [{ path: "/v1/cache/40/repair", body: undefined }]);
    await until(() => repairBtn(s, 40)?.textContent === "Repair", "button idle again");
    assert.equal(repairBtn(s, 40).hasAttribute("aria-disabled"), false);
  } finally {
    store.stop();
  }
});

test("review fix: 'Show job' before the job is on screen lands on its Queue row once it shows, never later in History", async () => {
  reset();
  server.games = [game(10, "Complete One"), game(77, "Other")];
  server.jobs = [{ id: 5, appid: 77, type: "prefill", status: "queued", stop_request: null, created_at: T }];
  const proto = Object.getPrototypeOf(dom.document.body);
  proto.scrollIntoView = function scrollIntoView() {};
  try {
    const s = await open();
    highlightJob(950); // the repair's job is not in the jobs poll yet
    assert.equal(s.querySelector('.qrow[data-jid="950"]'), null);
    server.jobs = [{ id: 950, appid: 10, type: "prefill", status: "queued", stop_request: null, created_at: T }, ...server.jobs];
    store.refreshNow();
    await until(() => s.querySelector('.qrow[data-jid="950"]') !== null, "queued job shown");
    await until(() => dom.document.activeElement === s.querySelector('.qrow[data-jid="950"] .grip-live'), "focus on its handle");

    // It finishes: the History row appears collapsed and focus does not jump.
    const other = dom.document.createElement("button");
    dom.appRoot.appendChild(other);
    other.focus();
    server.jobs = server.jobs.map((j) => (j.id === 950 ? { ...j, status: "done", started_at: T, finished_at: T } : j));
    store.refreshNow();
    await until(() => s.querySelector('.hitem[data-jid="950"]') !== null, "history row");
    await tick(20);
    assert.equal(dom.document.activeElement, other, "the highlight was consumed by the Queue row");
    assert.equal(s.querySelector('.hitem[data-jid="950"] .hrow').getAttribute("aria-expanded"), "false", "a live job is not pre-expanded");
  } finally {
    delete proto.scrollIntoView;
    store.stop();
  }
});
