/**
 * WP DOWNLOADS-2 (replacing the DOWNLOADS-1 pins): the kit layout of
 * `views/downloads.js` — kit root, three columns in DOM order (Active +
 * Queue | Updates + Incomplete | History), the middle column left out when
 * empty, no [data-state] switch, paused jobs folded into Active, kit queue
 * and attention rows (no arrow buttons; the keyboard path stays on the
 * grip), and the kit.css block's container queries.
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


const cols = (s) => s.querySelector(".cols3");
const attnCol = (s) => s.querySelector(".dl-col-attn");

test("layout: kit root; Active + Queue | Updates + Incomplete | History as three columns in that DOM order", async () => {
  reset();
  server.games = [game(10, "Aurora"), game(11, "Borealis"), game(12, "Cirrus", { update_available: true })];
  server.jobs = [job(1, 10, "queued"), job(2, 11, "queued")];
  try {
    const s = await open();
    assert.equal(s.classList.contains("kit"), true, "the view root opts into the kit");
    assert.equal(s.classList.contains("dl"), true, "and is the container the @container rules query");
    const kids = cols(s).children;
    assert.equal(kids.length, 3);
    assert.deepEqual(kids[0].children.map((b) => b.className), ["blk dl-active", "blk dl-queue"]);
    assert.deepEqual(kids[1].children.map((b) => b.className), ["blk dl-updates", "blk dl-incomplete"]);
    assert.deepEqual(kids[2].children.map((b) => b.className), ["blk dl-history"]);
    assert.equal(kids[0].querySelectorAll(".qrow").length, 2, "queue rows in the first column");
    await until(() => attnCol(s).hidden === false, "middle column shown with an update");
    assert.equal(cols(s).classList.contains("no-attn"), false);
    assert.equal(kids[2].textContent.includes("History"), true);
    // The page head carries the title and the bulk group, the hero follows.
    assert.equal(s.children[0].className, "page-head");
    assert.equal(s.children[1].getAttribute("data-role"), "dl-stats");
  } finally {
    store.stop();
  }
});

test("MUTATION PIN: the middle column is left out (and the grid told so) while Updates and Incomplete are both empty", async () => {
  reset();
  server.games = [game(10, "Aurora")];
  try {
    const s = await open();
    assert.equal(attnCol(s).hidden, true);
    assert.equal(cols(s).classList.contains("no-attn"), true);
    server.games = [game(10, "Aurora", { update_available: true })];
    store.refreshNow();
    await until(() => attnCol(s).hidden === false, "shown once a game has an update");
    assert.equal(cols(s).classList.contains("no-attn"), false);
  } finally {
    store.stop();
  }
});

test("no [data-state] density switch any more", async () => {
  reset();
  server.games = [game(10, "Aurora")];
  server.jobs = [job(1, 10, "queued")];
  try {
    const s = await open();
    assert.equal(s.getAttribute("data-state"), null);
  } finally {
    store.stop();
  }
});

test("MUTATION PIN: a paused job folds into Active with Resume; the Active badge counts running + paused", async () => {
  reset();
  server.games = [game(10, "Aurora"), game(11, "Borealis")];
  server.jobs = [job(1, 10, "running"), job(2, 11, "paused")];
  try {
    const s = await open();
    const active = s.querySelector(".dl-active");
    const cards = active.querySelectorAll(".job");
    assert.deepEqual(cards.map((c) => c.getAttribute("data-jid")), ["1", "2"]);
    assert.equal(active.querySelector('[data-role="active-count"]').textContent, "2");
    const paused = cards[1];
    assert.equal(paused.classList.contains("held"), true);
    assert.equal(paused.querySelector('[data-role="job-pill"]').textContent, "Paused");
    const labels = paused.querySelectorAll("button").map((b) => b.textContent);
    assert.deepEqual(labels, ["Resume", "Cancel"]);
    assert.match(paused.querySelector('[data-role="hold-note"]').textContent, /does not hold the worker slot/);
    assert.equal(cards[0].querySelector('[data-role="job-pill"]').textContent, "Updating", "cached game: an update run");
    assert.equal(cards[0].querySelector('[data-role="job-progress"]'), null, "no progress data, no bar");
  } finally {
    store.stop();
  }
});

test("idle: Active and Queue show the kit's empty boxes, the badges stay hidden", async () => {
  reset();
  server.games = [game(10, "Aurora")];
  try {
    const s = await open();
    assert.equal(s.querySelector(".dl-active .empty").textContent, "No download running. Start one from the Library.");
    assert.equal(s.querySelector(".dl-queue .empty").textContent, "Nothing waiting.");
    assert.equal(s.querySelector('[data-role="active-count"]').hidden, true);
    assert.equal(s.querySelector('[data-role="queue-count"]').hidden, true);
  } finally {
    store.stop();
  }
});

test("queue rows: grip, name, size, ghost 72px Remove; no arrow buttons; the grip keeps the keyboard path", async () => {
  reset();
  server.games = [game(10, "Aurora"), game(11, "Borealis")];
  server.jobs = [job(1, 10, "queued"), job(2, 11, "queued")];
  try {
    const s = await open();
    for (const r of s.querySelectorAll(".qrow")) {
      assert.equal(r.classList.contains("jr"), true, "the kit attention-row anatomy");
      const labels = r.querySelectorAll("button").map((b) => (b.getAttribute("aria-label") || b.textContent).toLowerCase());
      assert.equal(labels.some((l) => /\bmove\b|\bup\b|\bdown\b|earlier|later/.test(l)), false, "no move buttons");
      const grip = r.querySelector(".grip-live");
      assert.ok(grip, "live grip");
      assert.equal(grip.getAttribute("role"), "button");
      assert.equal(grip.getAttribute("tabindex"), "0");
      assert.match(grip.getAttribute("aria-label"), /up and down arrow keys/);
      assert.equal(r.querySelector(".sz").textContent, "3.0 GB");
      const remove = r.querySelector('[data-role="queue-remove"]');
      assert.equal(remove.textContent, "Remove");
      assert.equal(remove.className, "btn sm ghost w72");
    }
  } finally {
    store.stop();
  }
});

test("attention rows: open lists (no fold toggle), count badge and 'Update all' in the head, 72px 'Update' per row", async () => {
  reset();
  server.games = [game(10, "Aurora", { update_available: true })];
  try {
    const s = await open();
    await until(() => box(s).hidden === false, "section shown");
    assert.equal(box(s).querySelector('[data-role="updates-toggle"]'), null, "no Show/Hide fold");
    assert.notEqual(box(s).querySelector(".dl-updates-list").hidden, true, "the list is never folded");
    const head = box(s).querySelector(".sechd");
    assert.equal(head.querySelector("h2.sec").textContent, "Updates available");
    assert.equal(head.querySelector('[data-role="updates-count"]').textContent, "1");
    assert.equal(head.querySelector('[data-role="update-all"]').textContent, "Update all");
    const btn = nowBtn(s, 10);
    assert.equal(btn.textContent, "Update");
    assert.equal(btn.className, "btn sm w72");
    assert.equal(rows(s)[0].querySelector(".meta").textContent, "3.0 GB");
    click(btn);
    await until(() => server.posts.length === 1, "POST sent");
    assert.deepEqual(server.posts[0], { path: "/v1/prefill", body: { appids: [10] } });
  } finally {
    releaseGates();
    store.stop();
  }
});

test("MUTATION PIN: both attention badges are visible with their row counts, and hide again at zero", async () => {
  reset();
  const incomplete = (appid, name, pct) => game(appid, name, { cache_complete_pct: pct, cache_missing_chunks: 3 });
  server.games = [
    game(10, "Aurora", { update_available: true }),
    game(11, "Borealis", { update_available: true }),
    incomplete(20, "Cirrus", 80),
    incomplete(21, "Dune", 40),
    incomplete(22, "Ember", 60),
  ];
  try {
    const s = await open();
    const updates = () => s.querySelector('[data-role="updates-count"]');
    const inc = () => s.querySelector('[data-role="incomplete-count"]');
    await until(() => updates().hidden === false && inc().hidden === false, "both badges shown");
    assert.equal(updates().textContent, "2");
    assert.equal(inc().textContent, "3");
    server.games = [game(10, "Aurora", { update_available: true }), incomplete(20, "Cirrus", 80)];
    store.refreshNow();
    await until(() => updates().textContent === "1" && inc().textContent === "1", "counts follow the rows");
    assert.equal(updates().hidden, false);
    assert.equal(inc().hidden, false);
  } finally {
    store.stop();
  }
});

test("CSS: the Downloads block in kit.css uses container queries at 1099/900/760/460, tokens only, and app.css lost the old rules", async () => {
  const { readFileSync } = await import("node:fs");
  const strip = (css) => css.replace(/\/\*[\s\S]*?\*\//g, "");
  const kit = strip(readFileSync(new URL("../css/kit.css", import.meta.url), "utf8"));
  const app = strip(readFileSync(new URL("../css/app.css", import.meta.url), "utf8"));
  const from = kit.indexOf(".kit.dl{");
  assert.ok(from > 0, "Downloads block found in kit.css");
  // Up to the phone block (WP MOBILE-1b, starts with the overflow cue),
  // which has its own viewport rules.
  const to = kit.indexOf(".kit .scue{");
  const block = to > from ? kit.slice(from, to) : kit.slice(from);
  assert.match(block, /\.kit\.dl\{[^}]*container-type:\s*inline-size/);
  for (const w of [1099, 900, 760, 460]) assert.match(block, new RegExp(`@container \\(max-width:${w}px\\)`), `@container ${w}px`);
  assert.equal(/@media \((min|max)-width/.test(block), false, "no viewport breakpoints for the page composition");
  assert.match(block, /\.kit \.cols3\{[^}]*repeat\(3, minmax\(0, 1fr\)\)/);
  assert.match(block, /\.kit \.graph svg\{[^}]*height:96px/);
  assert.match(block, /\.kit \.hpanel \.logbox\{[^}]*max-height:160px/);
  assert.equal(/#[0-9a-fA-F]{3,8}\b|rgba?\(/.test(block), false, "no hard-coded colours");
  for (const gone of ["[data-state", ".dl-cols", ".dl-stats", ".statgrid", ".tgraph", ".attn-toggle", ".dl-client-row", ".urow", ".qrow", ".hrow", ".failhint"]) {
    assert.equal(app.includes(gone), false, `app.css still has ${gone}`);
  }
});
