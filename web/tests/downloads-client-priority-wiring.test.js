/**
 * WP CAP-503-FIX-1b: DOM wiring of the client-priority texts in
 * `views/downloads.js` — the hold poll (`GET /v1/client-priority`, only
 * while a job is active), the paused card's note for a job paused for a
 * device (held, restored), the queue note while holding, the override note
 * on a running card, the pausing stop note, the manual-pause note kept, and
 * the text patch when a client pause turns manual. The texts themselves are
 * pinned in client-priority.test.js; here each state must reach the page.
 *
 * Harness copied from downloads-layout.test.js: fake-dom.js, a stored API
 * key, a routing `fetch` fake the REAL store-singleton polls. Text and
 * booleans only, never node identity (docs/learnings/web.md).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, after } from "node:test";
import assert from "node:assert/strict";
import { createFakeDom } from "./fake-dom.js";
import { throughputFixture } from "./throughput-fixture.js";

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

const T = "2026-11-21T09:00:00Z";
const RESUME = "2026-11-21T10:02:00Z";
const d = new Date(Date.parse(RESUME));
const CLOCK = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;

const game = (appid, name) => ({ appid, name, status: "done", last_prefill_at: T, needs_force: false, size_bytes: 1_073_741_824, update_available: false });
const job = (id, appid, status, over = {}) => ({
  id,
  appid,
  type: "prefill",
  status,
  stop_request: null,
  pause_reason: null,
  client_override: false,
  created_at: T,
  ...over,
});
const holding = (over = {}) => ({
  enabled: true,
  holding: true,
  since: "2026-11-21T10:00:00Z",
  last_client_at: "2026-11-21T10:00:00Z",
  resume_at: RESUME,
  idle_gap_seconds: 120,
  clients: [{ addr: "10.0.0.21", name: "Desk PC", miss_bytes_per_second: 9_000_000 }],
  paused_job_ids: [],
  override_job_ids: [],
  restored: false,
  runner_address_unknown: false,
  ...over,
});
const notHolding = () => holding({ holding: false, since: null, resume_at: null, clients: [] });

const server = { games: [], jobs: [], cp: notHolding(), cpCalls: 0 };
function respond(status, data) {
  const text = JSON.stringify(data);
  return { ok: status >= 200 && status < 300, status, json: async () => data, text: async () => text };
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
  if (method === "GET" && p === "/v1/client-priority") {
    server.cpCalls += 1;
    return respond(200, server.cp);
  }
  const stats = throughputFixture(u);
  if (stats) return respond(200, stats);
  return respond(404, { detail: "Not Found" });
};

const { initToast } = await import("../js/components/toast.js");
initToast();
const { store } = await import("../js/store-singleton.js");
const { renderDownloads } = await import("../js/views/downloads.js");

after(async () => {
  store.stop();
  (await import("../js/router.js")).navigateTo("library");
});

const tick = (ms = 10) => new Promise((r) => setTimeout(r, ms));
async function until(pred, label, timeoutMs = 2000) {
  const start = Date.now();
  while (!pred()) {
    if (Date.now() - start > timeoutMs) throw new Error(`timed out waiting for: ${label}`);
    await tick(5);
  }
}
async function open() {
  store.start();
  const s = renderDownloads();
  await until(() => store.snapshot("games") !== null && store.snapshot("jobs") !== null, "first polls");
  await tick(20);
  return s;
}
function reset() {
  store.stop();
  server.games = [game(10, "Aurora"), game(11, "Borealis"), game(12, "Cirrus")];
  server.jobs = [];
  server.cp = notHolding();
}
const card = (s, id) => s.querySelector(`.job[data-jid="${id}"]`);
const holdNote = (s, id) => card(s, id).querySelector('[data-role="hold-note"]');
const clientNote = (s, id) => card(s, id).querySelector('[data-role="client-note"]');
const queueNote = (s) => s.querySelector('[data-role="queue-hold-note"]');

const HELD_SEEN = `Paused to give Desk PC the full speed through the cache. Resumes by itself at about ${CLOCK} unless a device keeps loading. Resume runs it now, alongside Desk PC.`;

test("MUTATION PIN: held — a job paused for a device names it and the resume time on its card", async () => {
  reset();
  server.jobs = [job(1, 10, "paused", { pause_reason: "client" })];
  server.cp = holding({ paused_job_ids: [1] });
  try {
    const s = await open();
    await until(() => holdNote(s, 1) && holdNote(s, 1).textContent === HELD_SEEN, "held note");
    assert.equal(card(s, 1).querySelector('[data-role="job-pill"]').textContent, "Paused");
    assert.deepEqual(card(s, 1).querySelectorAll("button").map((b) => b.textContent), ["Resume", "Cancel"], "Resume overrides (Weg B)");
  } finally {
    store.stop();
  }
});

test("MUTATION PIN: queued while holding — one note over the queue with the device and the time; hidden without a hold", async () => {
  reset();
  server.jobs = [job(1, 10, "paused", { pause_reason: "client" }), job(2, 11, "queued"), job(3, 12, "queued", { type: "gc" })];
  server.cp = holding({ paused_job_ids: [1] });
  try {
    const s = await open();
    const text = `Queued downloads wait to give Desk PC the full speed through the cache. They carry on at about ${CLOCK} unless a device keeps loading.`;
    await until(() => queueNote(s).textContent === text, "queue note");
    assert.equal(queueNote(s).hidden, false);
    // The note sits above the rows, in the Queue block.
    const kids = s.querySelector(".dl-queue").children.map((c) => c.getAttribute("data-role"));
    assert.ok(kids.indexOf("queue-hold-note") >= 0 && kids.indexOf("queue-hold-note") < kids.indexOf("queue-list"));
  } finally {
    store.stop();
  }
});

test("queue note is hidden while nothing is held", async () => {
  reset();
  server.jobs = [job(2, 11, "queued")];
  server.cp = notHolding();
  try {
    const s = await open();
    await until(() => server.cpCalls > 0, "hold asked");
    await tick(20);
    assert.equal(queueNote(s).hidden, true);
    assert.equal(queueNote(s).textContent, "");
  } finally {
    store.stop();
  }
});

test("MUTATION PIN: override — a running job resumed during the hold reads 'Runs alongside <device> by your choice.'", async () => {
  reset();
  server.jobs = [job(4, 10, "running", { client_override: true }), job(5, 11, "queued", { client_override: true })];
  server.cp = holding({ override_job_ids: [4, 5] });
  try {
    const s = await open();
    await until(() => clientNote(s, 4).textContent === "Runs alongside Desk PC by your choice.", "override note");
    assert.equal(clientNote(s, 4).hidden, false);
    assert.equal(queueNote(s).hidden, true, "an override-only queue does not wait");
  } finally {
    store.stop();
  }
});

test("override note stays hidden without a hold, and on a job without an override", async () => {
  reset();
  server.jobs = [job(4, 10, "running", { client_override: true })];
  server.cp = notHolding();
  try {
    const s = await open();
    await until(() => server.cpCalls > 0, "hold asked");
    await tick(20);
    assert.equal(clientNote(s, 4).hidden, true);
    assert.equal(clientNote(s, 4).textContent, "");
  } finally {
    store.stop();
  }
});

test("MUTATION PIN: restored — no device named, no sighting claimed, on the card and over the queue", async () => {
  reset();
  server.jobs = [job(1, 10, "paused", { pause_reason: "client" }), job(2, 11, "queued")];
  server.cp = holding({ restored: true, clients: [], paused_job_ids: [1] });
  try {
    const s = await open();
    const cardText = `Paused for device priority before vault-api restarted. No device has been seen loading since; resumes by itself at about ${CLOCK} unless one starts.`;
    await until(() => holdNote(s, 1).textContent === cardText, "restored card note");
    assert.equal(
      queueNote(s).textContent,
      `Queued downloads wait: device priority was on before vault-api restarted, and no device has been seen loading since. They carry on at about ${CLOCK} unless one starts.`,
    );
    assert.doesNotMatch(s.textContent, /Desk PC/);
  } finally {
    store.stop();
  }
});

test("MUTATION PIN: manual — a manually paused job keeps the manual note during a hold", async () => {
  reset();
  server.jobs = [job(1, 10, "paused", { pause_reason: "manual" }), job(6, 11, "paused", { pause_reason: null, type: "gc" })];
  server.cp = holding();
  try {
    const s = await open();
    await until(() => server.cpCalls > 0, "hold asked");
    await tick(20);
    assert.match(holdNote(s, 1).textContent, /does not hold the worker slot/);
    assert.match(holdNote(s, 6).textContent, /^Paused before it started/);
    assert.equal(clientNote(s, 1).hidden, true);
  } finally {
    store.stop();
  }
});

test("MUTATION PIN: a client pause turned manual (status stays paused) repaints the note from the jobs poll", async () => {
  reset();
  server.jobs = [job(1, 10, "paused", { pause_reason: "client" })];
  server.cp = holding({ paused_job_ids: [1] });
  try {
    const s = await open();
    await until(() => holdNote(s, 1).textContent === HELD_SEEN, "held note");
    server.jobs = [job(1, 10, "paused", { pause_reason: "manual" })];
    store.refreshNow();
    await until(() => /does not hold the worker slot/.test(holdNote(s, 1).textContent), "manual note after the poll");
  } finally {
    store.stop();
  }
});

test("pausing for a device: the stop note says why, not 'Pause requested'", async () => {
  reset();
  server.jobs = [job(7, 10, "running", { stop_request: "pause", pause_reason: "client" })];
  server.cp = holding();
  try {
    const s = await open();
    const stop = card(s, 7).querySelector('[data-role="stop-note"]');
    assert.equal(stop.hidden, false);
    assert.equal(
      stop.textContent,
      "Pausing so a device loading through the cache gets the full speed. It resumes by itself, from the cache, not from zero.",
    );
  } finally {
    store.stop();
  }
});

test("MUTATION PIN: the hold is not asked while no job is queued, running or paused", async () => {
  reset();
  server.jobs = [job(8, 10, "done")];
  try {
    await open();
    await until(() => (store.snapshot("jobs") || []).every((j) => j.status === "done"), "jobs poll applied");
    await tick(20);
    const before = server.cpCalls;
    renderDownloads(); // a mount asks the hold at once, if anything is active
    await tick(60);
    assert.equal(server.cpCalls, before, "no GET /v1/client-priority with nothing active");
  } finally {
    store.stop();
  }
});
