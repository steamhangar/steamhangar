/**
 * WP CACHE-COMPLETE-1c — DOM wiring of the "Incomplete (N)" section in
 * `views/downloads.js`: rendered only with an entry (user rule 2026-10-14),
 * placed after "Updates available" and before the Queue, one row per
 * incomplete game with its figure, "Complete now" -> `POST
 * /v1/prefill/verify {appids:[id]}`, "Complete all" -> the same route with
 * exactly the listed completable rows (never `/v1/prefill/incomplete`), focus
 * kept across a rebuild, no "Complete now" (a note
 * instead) for a game short only of damaged chunks, no "Complete all" when
 * no row can be completed, the schedule note, the "verifies on the next run"
 * toast for a running dedupe, and the started game leaving the list once
 * its job shows.
 *
 * Harness as downloads-updates-wiring.test.js: fake-dom.js, a stored API
 * key, a routing `fetch` fake the REAL store polls. The store is stopped in
 * `after`; text/booleans only, never node identity.
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
const GB = 1_073_741_824;
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
  schedule: { enabled: false, window: null, sweep_include_cached: true },
  posts: [], // {path, body}
  // appid -> job status to dedupe onto
  dedupe: new Map(),
  nextJobId: 700,
};
function respond(status, data) {
  const text = JSON.stringify(data);
  return { ok: status >= 200 && status < 300, status, json: async () => data, text: async () => text };
}
function enqueue(appid) {
  if (server.dedupe.has(appid)) return { appid, job_id: 1, status: server.dedupe.get(appid), deduplicated: true };
  const id = server.nextJobId++;
  server.jobs = [{ id, appid, type: "prefill", status: "queued", stop_request: null, created_at: T }, ...server.jobs];
  return { appid, job_id: id, status: "queued", deduplicated: false };
}
globalThis.fetch = async (url, init = {}) => {
  const u = new URL(String(url));
  const method = (init.method || "GET").toUpperCase();
  const p = u.pathname;
  if (method === "GET" && p === "/v1/jobs") return respond(200, server.jobs);
  if (method === "GET" && p === "/v1/games") return respond(200, server.games);
  if (method === "GET" && p === "/v1/clients") return respond(200, []);
  if (method === "GET" && p === "/v1/cache/summary") return respond(200, {});
  if (method === "GET" && p === "/v1/schedule") return respond(200, server.schedule);
  if (method === "POST" && p.startsWith("/v1/prefill")) {
    const body = init.body === undefined ? undefined : JSON.parse(init.body);
    server.posts.push({ path: p, body });
    return respond(202, (body?.appids || []).map(enqueue));
  }
  return respond(404, { detail: "Not Found" });
};

const { initToast } = await import("../js/components/toast.js");
initToast();
const { store } = await import("../js/store-singleton.js");
const { renderDownloads } = await import("../js/views/downloads.js");

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
const heading = (s) => box(s).querySelector("h4.sec").textContent;
const note = (s) => box(s).querySelector(".dl-updates-note");
const rows = (s) => box(s).querySelectorAll(".urow");
const rowNames = (s) => rows(s).map((r) => r.querySelector(".nm").textContent);
const rowFor = (s, appid) => box(s).querySelector(`.urow[data-incomplete-appid="${appid}"]`);
const nowBtn = (s, appid) => box(s).querySelector(`.urow[data-incomplete-appid="${appid}"] [data-role="complete-now"]`);
const allBtn = (s) => box(s).querySelector('[data-role="complete-all"]');
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
  server.posts = [];
  server.jobs = [];
  server.dedupe = new Map();
  server.schedule = { enabled: false, window: null, sweep_include_cached: true };
  shell["toast-text"].textContent = "";
}

test("MUTATION PIN: hidden while nothing is incomplete; shown with rows, figures and buttons; after Updates, before Queue", async () => {
  reset();
  server.games = [game(10, "Complete One"), game(11, "Unknown", { cache_complete_pct: null, cache_missing_chunks: null })];
  try {
    const s = await open();
    assert.equal(box(s).hidden, true, "no empty heading (user rule)");

    server.games = [
      partial(30, "Zephyr Run", 42, 7),
      partial(20, "Aurora", 86, 3),
      partial(40, "Broken Bits", 99, 0),
      game(10, "Complete One"),
      partial(50, "Has Update", 10, 9, { update_available: true }),
    ];
    store.refreshNow();
    await until(() => box(s).hidden === false, "section shown");
    assert.equal(heading(s), "Incomplete (3)");
    assert.deepEqual(rowNames(s), ["Aurora", "Broken Bits", "Zephyr Run"], "sorted; complete and flagged games are not listed");
    assert.equal(rowFor(s, 20).querySelector(".usize").textContent, "86%");
    assert.equal(nowBtn(s, 20).textContent, "Complete now");
    assert.equal(nowBtn(s, 20).getAttribute("aria-label"), "Complete now — Aurora");
    assert.equal(nowBtn(s, 40), null, "MUTATION PIN: no Complete now for damaged-only");
    assert.equal(rowFor(s, 40).querySelector('[data-role="damaged-note"]').textContent, "Damaged chunks — SteamHangar can't repair these yet.");
    assert.equal(allBtn(s).textContent, "Complete all");
    assert.equal(allBtn(s).getAttribute("aria-label"), "Complete all 2 games", "counts only the completable rows");

    const order = s.children.map((c) => c.className || c.tagName);
    const upd = order.indexOf("dl-updates");
    assert.equal(order[upd + 1], "dl-updates dl-incomplete", "directly after Updates available");
    assert.equal(s.children[upd + 2].textContent.startsWith("Queue"), true, "directly above the Queue heading");

    server.games = server.games.map((g) => ({ ...g, cache_complete_pct: 100, cache_missing_chunks: 0 }));
    store.refreshNow();
    await until(() => box(s).hidden === true, "all complete: hidden again");
  } finally {
    store.stop();
  }
});

test("MUTATION PIN: only damaged-only rows -> listed, but no 'Complete all'", async () => {
  reset();
  server.games = [partial(40, "Broken Bits", 99, 0)];
  try {
    const s = await open();
    await until(() => box(s).hidden === false, "section shown");
    assert.equal(heading(s), "Incomplete (1)");
    assert.equal(allBtn(s), null);
  } finally {
    store.stop();
  }
});

test("MUTATION PIN: 'Complete now' sends POST /v1/prefill/verify with exactly that id; the game leaves the list", async () => {
  reset();
  server.games = [partial(20, "Aurora", 86, 3), partial(30, "Zephyr Run", 42, 7)];
  try {
    const s = await open();
    await until(() => nowBtn(s, 20) !== null, "row shown");
    click(nowBtn(s, 20));
    await until(() => server.posts.length === 1, "one POST");
    assert.deepEqual(server.posts[0], { path: "/v1/prefill/verify", body: { appids: [20] } });
    await until(() => toastText() === "Queued to complete.", "toast");
    await until(() => rowFor(s, 20) === null, "left the list once its job shows");
    assert.equal(heading(s), "Incomplete (1)");
  } finally {
    store.stop();
  }
});

test("MUTATION PIN: 'Complete all' sends POST /v1/prefill/verify with exactly the listed completable rows", async () => {
  reset();
  // Incomplete on the server, but not listed here: update-flagged (60), busy
  // (70) and damaged-only (40). None of them may be sent.
  server.games = [
    partial(20, "Aurora", 86, 3),
    partial(30, "Zephyr Run", 42, 7),
    partial(40, "Broken Bits", 99, 0),
    partial(60, "Has Update", 10, 9, { update_available: true }),
    partial(70, "Busy One", 50, 5),
  ];
  server.jobs = [{ id: 9, appid: 70, type: "prefill", status: "paused", stop_request: null, created_at: T, started_at: T }];
  try {
    const s = await open();
    await until(() => allBtn(s) !== null, "button shown");
    click(allBtn(s));
    await until(() => server.posts.length === 1, "one POST");
    assert.deepEqual(server.posts[0], { path: "/v1/prefill/verify", body: { appids: [20, 30] } });
    await until(() => toastText() === "2 queued to complete", "toast");
  } finally {
    store.stop();
  }
});

test("MUTATION PIN: a 'Complete now' that dedupes onto a RUNNING job says it verifies on the next run", async () => {
  reset();
  server.games = [partial(20, "Aurora", 86, 3)];
  server.dedupe.set(20, "running");
  try {
    const s = await open();
    await until(() => nowBtn(s, 20) !== null, "row shown");
    click(nowBtn(s, 20));
    await until(() => toastText() !== "", "toast");
    assert.equal(toastText(), "Already downloading — verifies on the next run.");
  } finally {
    store.stop();
  }
});

test("schedule note: manual only without a window; automatic in the window when one is set", async () => {
  reset();
  server.games = [partial(20, "Aurora", 86, 3)];
  try {
    const s = await open();
    await until(() => box(s).hidden === false && note(s).textContent !== "", "note");
    assert.equal(note(s).textContent, "Manual only — no schedule window is set.");
    store.stop();

    server.schedule = { enabled: true, window: "01:00-06:00", sweep_include_cached: false };
    const s2 = await open();
    await until(() => note(s2).textContent.startsWith("These"), "window note");
    assert.equal(note(s2).textContent, "These complete automatically in the schedule window 01:00–06:00.");
  } finally {
    store.stop();
  }
});

test("a game with an active job is not listed (it is in the queue)", async () => {
  reset();
  server.games = [partial(20, "Aurora", 86, 3), partial(30, "Zephyr Run", 42, 7)];
  server.jobs = [{ id: 9, appid: 30, type: "prefill", status: "running", stop_request: null, created_at: T, started_at: T }];
  try {
    const s = await open();
    await until(() => box(s).hidden === false, "section shown");
    assert.deepEqual(rowNames(s), ["Aurora"]);
  } finally {
    store.stop();
  }
});

test("MUTATION PIN: focus survives a rebuild — the same row's button, then 'Complete all', then the Queue heading", async () => {
  reset();
  server.games = [partial(20, "Aurora", 86, 3), partial(30, "Zephyr Run", 42, 7)];
  try {
    const s = await open();
    await until(() => nowBtn(s, 30) !== null, "rows shown");
    nowBtn(s, 30).focus();
    // A new figure for the other row rebuilds the section.
    server.games = [partial(20, "Aurora", 87, 2), partial(30, "Zephyr Run", 42, 7)];
    store.refreshNow();
    await until(() => rowFor(s, 20).querySelector(".usize").textContent === "87%", "rebuilt");
    assert.equal(document.activeElement?.dataset?.incompleteAppid, "30", "the same row's button");
    assert.equal(document.activeElement?.dataset?.role, "complete-now");

    // Its row leaves: focus moves to "Complete all".
    server.games = [partial(20, "Aurora", 87, 2), partial(30, "Zephyr Run", 100, 0)];
    store.refreshNow();
    await until(() => rowFor(s, 30) === null, "row gone");
    assert.equal(document.activeElement?.dataset?.role, "complete-all");

    // The whole section empties: focus moves to the Queue heading.
    server.games = [partial(20, "Aurora", 100, 0), partial(30, "Zephyr Run", 100, 0)];
    store.refreshNow();
    await until(() => box(s).hidden === true, "section hidden");
    assert.equal(document.activeElement?.textContent?.startsWith("Queue"), true);
  } finally {
    store.stop();
  }
});
