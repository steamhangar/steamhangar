/**
 * Queue reorder in the Downloads view (WP D20, QUEUE-ORDER-1b): the pure
 * helpers (lib/queue-reorder.js, job-partition ordering, the render-plan
 * rule) and the DOM wiring in `views/downloads.js` — keyboard path, pointer
 * drag, optimistic order with rollback on a refused move, and a jobs poll
 * that must not repaint the queue while a move is in flight.
 *
 * Harness: fake-dom.js, a stored API key, and a routing `fetch` fake with a
 * small server-side queue model (`ORDER`) that the REAL store-singleton
 * polls. Move answers can be held (gated); every test releases the gate in
 * `finally` so a broken run fails instead of hanging (docs/LEARNINGS.md,
 * WEB-FIX-1).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, after } from "node:test";
import assert from "node:assert/strict";
import { createFakeDom, fakeKeyEvent } from "./fake-dom.js";

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

// Every row is 40px tall; its top follows its position in the ORIGINAL
// layout (jid order), so a drag has real geometry to work against.
dom.FakeElement.prototype.getBoundingClientRect = function rect() {
  const jid = Number(this.dataset?.jid || 0);
  return { top: jid * 40, height: 40 };
};

// ---- routing fetch fake with a server-side queue ------------------------
/** Queued job ids, first = runs next. */
let ORDER = [];
/** Non-queued jobs the server also lists. */
let OTHERS = [];
const calls = [];
/** Parsed bodies of every move request, as `[jobId, position]`. */
let moves = [];
let moveError = null; // {status, detail}
let gated = false;
let held = [];

function respond(status, data) {
  const text = JSON.stringify(data);
  return { ok: status >= 200 && status < 300, status, json: async () => data, text: async () => text };
}
function releaseGates() {
  gated = false;
  const list = held;
  held = [];
  for (const go of list) go();
}
const mk = (id, status, extra = {}) => ({
  id,
  appid: 1000 + id,
  type: "prefill",
  status,
  stop_request: null,
  created_at: "2026-10-04T10:00:00Z",
  queue_position: null,
  ...extra,
});
function serverJobs() {
  return [...OTHERS, ...ORDER.map((id, i) => mk(id, "queued", { queue_position: i }))];
}
globalThis.fetch = async (url, init = {}) => {
  const u = new URL(String(url));
  const method = (init.method || "GET").toUpperCase();
  calls.push(`${method} ${u.pathname}`);
  if (u.pathname === "/v1/jobs") return respond(200, serverJobs());
  if (u.pathname === "/v1/games") return respond(200, []);
  if (u.pathname === "/v1/clients") return respond(200, []);
  if (u.pathname === "/v1/cache/summary") return respond(200, {});
  const m = u.pathname.match(/^\/v1\/jobs\/(\d+)\/move$/);
  if (m && method === "POST") {
    const id = Number(m[1]);
    const { position } = JSON.parse(init.body);
    moves.push([id, position]);
    if (gated) await new Promise((r) => held.push(r));
    if (moveError) return respond(moveError.status, { detail: moveError.detail });
    ORDER = ORDER.filter((x) => x !== id);
    ORDER.splice(Math.min(position, ORDER.length), 0, id);
    return respond(200, { job_id: id, status: "queued", outcome: "immediate", detail: "", queue_position: ORDER.indexOf(id) });
  }
  return respond(404, { detail: "Not Found" });
};

const { initToast } = await import("../js/components/toast.js");
initToast();
const { store } = await import("../js/store-singleton.js");
const { renderDownloads } = await import("../js/views/downloads.js");
const { setConnectionLost } = await import("../js/connection-status.js");
const { reorderIds, keyTargetIndex, dropIndex, withQueuePositions } = await import("../js/lib/queue-reorder.js");
const { partitionJobs } = await import("../js/lib/job-partition.js");
const { planJobsUpdate } = await import("../js/lib/downloads-render-plan.js");

after(() => {
  releaseGates();
  store.stop();
  setConnectionLost(false);
});

const tick = (ms = 25) => new Promise((r) => setTimeout(r, ms));
async function settle() {
  for (let i = 0; i < 10; i += 1) await tick(5);
}
async function serve(order, others = []) {
  ORDER = order.slice();
  OTHERS = others;
  store.refreshNow();
  await tick();
}
function reset() {
  calls.length = 0;
  moves = [];
  moveError = null;
  gated = false;
  held = [];
  shell["toast-text"].textContent = "";
  shell.toast.classList.remove("warn");
}
const rowIds = (section) => section.querySelectorAll(".qrow").map((r) => Number(r.dataset.jid));
const gripOf = (section, id) => section.querySelector(`.qrow[data-jid="${id}"] .grip`);
const press = (el, key) => {
  const ev = fakeKeyEvent(key);
  el.dispatchEvent(ev);
  return ev;
};
const toastText = () => shell["toast-text"].textContent;

// ---- pure helpers ------------------------------------------------------

test("reorderIds moves one id, clamps the target, and never mutates its input", () => {
  const ids = [1, 2, 3, 4];
  assert.deepEqual(reorderIds(ids, 4, 0), [4, 1, 2, 3]);
  assert.deepEqual(reorderIds(ids, 1, 2), [2, 3, 1, 4]);
  assert.deepEqual(reorderIds(ids, 2, 99), [1, 3, 4, 2]);
  assert.deepEqual(reorderIds(ids, 3, -5), [3, 1, 2, 4]);
  assert.deepEqual(reorderIds(ids, 9, 0), [1, 2, 3, 4], "unknown id: unchanged copy");
  assert.deepEqual(ids, [1, 2, 3, 4]);
});

test("keyTargetIndex: arrows step, Home/End jump, edges and other keys do nothing", () => {
  assert.equal(keyTargetIndex("ArrowUp", 2, 4), 1);
  assert.equal(keyTargetIndex("ArrowDown", 2, 4), 3);
  assert.equal(keyTargetIndex("Home", 2, 4), 0);
  assert.equal(keyTargetIndex("End", 1, 4), 3);
  assert.equal(keyTargetIndex("ArrowUp", 0, 4), null);
  assert.equal(keyTargetIndex("ArrowDown", 3, 4), null);
  assert.equal(keyTargetIndex("Home", 0, 4), null);
  assert.equal(keyTargetIndex("a", 1, 4), null);
});

test("dropIndex counts the other rows whose midpoint is above the pointer", () => {
  const mids = [20, 60, 100];
  assert.equal(dropIndex(mids, 0), 0);
  assert.equal(dropIndex(mids, 61), 2);
  assert.equal(dropIndex(mids, 500), 3);
});

test("withQueuePositions rewrites only queued rows that are in the list", () => {
  const jobs = [mk(1, "queued"), mk(2, "queued"), mk(3, "running")];
  const out = withQueuePositions(jobs, [2, 1]);
  assert.deepEqual(
    out.map((j) => [j.id, j.queue_position]),
    [
      [1, 1],
      [2, 0],
      [3, null],
    ],
  );
  assert.equal(out[2], jobs[2], "untouched rows keep their identity");
});

test("partitionJobs orders the queue by queue_position, falling back to oldest id", () => {
  const withPos = partitionJobs([mk(1, "queued", { queue_position: 2 }), mk(2, "queued", { queue_position: 0 }), mk(3, "queued", { queue_position: 1 })]);
  assert.deepEqual(withPos.queued.map((j) => j.id), [2, 3, 1]);
  const legacy = partitionJobs([{ id: 5, status: "queued" }, { id: 3, status: "queued" }, { id: 4, status: "queued" }]);
  assert.deepEqual(legacy.queued.map((j) => j.id), [3, 4, 5], "an API without queue_position stays FIFO");
});

test("planJobsUpdate: a queue_position change alone forces a full render", () => {
  const prev = mk(1, "queued", { queue_position: 0 });
  const curr = mk(1, "queued", { queue_position: 1 });
  const plan = planJobsUpdate({ isFirst: false, added: [], removed: [], updated: [{ prev, curr }] });
  assert.equal(plan.full, true);
});

// ---- wiring ------------------------------------------------------------

test("the queue renders in the server's order and the old FIFO-only hint is gone", async () => {
  reset();
  try {
    await serve([3, 1, 2]);
    const section = renderDownloads();
    assert.deepEqual(rowIds(section), [3, 1, 2]);
    assert.deepEqual(
      section.querySelectorAll(".qrow .pos").map((p) => p.textContent),
      ["#1", "#2", "#3"],
    );
    assert.equal(section.textContent.includes("not built yet"), false);
  } finally {
    releaseGates();
  }
});

test("ArrowUp on a grip sends the move, reorders at once and keeps focus on that grip", async () => {
  reset();
  try {
    await serve([1, 2, 3]);
    const section = renderDownloads();
    const grip = gripOf(section, 3);
    assert.equal(grip.getAttribute("tabindex"), "0");
    assert.equal(grip.getAttribute("role"), "button");
    assert.match(grip.getAttribute("aria-label"), /position 3 of 3/);

    const ev = press(grip, "ArrowUp");
    assert.equal(ev.defaultPrevented, true, "the page must not scroll");
    assert.deepEqual(rowIds(section), [1, 3, 2], "optimistic: reordered before the server answered");
    await settle();

    assert.deepEqual(moves, [[3, 1]]);
    assert.deepEqual(rowIds(section), [1, 3, 2]);
    // Booleans only: a failing assert.equal on DOM nodes prints the whole tree.
    assert.equal(dom.document.activeElement === gripOf(section, 3), true, "keyboard focus follows the moved row");
  } finally {
    releaseGates();
  }
});

test("Home and End jump to the ends; an arrow at the edge sends nothing", async () => {
  reset();
  try {
    await serve([1, 2, 3]);
    const section = renderDownloads();
    press(gripOf(section, 1), "ArrowUp");
    press(gripOf(section, 3), "ArrowDown");
    press(gripOf(section, 1), "a");
    await settle();
    assert.deepEqual(moves, [], "edges and unrelated keys: no request");

    press(gripOf(section, 3), "Home");
    await settle();
    assert.deepEqual(moves, [[3, 0]]);
    assert.deepEqual(rowIds(section), [3, 1, 2]);

    press(gripOf(section, 3), "End");
    await settle();
    assert.deepEqual(moves, [
      [3, 0],
      [3, 2],
    ]);
    assert.deepEqual(rowIds(section), [1, 2, 3]);
  } finally {
    releaseGates();
  }
});

test("a refused move puts the old order back and says why", async () => {
  reset();
  try {
    await serve([1, 2, 3]);
    const section = renderDownloads();
    moveError = { status: 409, detail: "Job 3 is running, only a queued job can be moved" };
    press(gripOf(section, 3), "Home");
    assert.deepEqual(rowIds(section), [3, 1, 2], "optimistic first");
    await settle();
    assert.deepEqual(rowIds(section), [1, 2, 3], "rolled back");
    assert.match(toastText(), /only a queued job can be moved|running/);
    assert.equal(shell.toast.classList.contains("warn"), true);
  } finally {
    releaseGates();
  }
});

test("a jobs poll that lands while the move is in flight does not undo the optimistic order", async () => {
  reset();
  try {
    await serve([1, 2, 3]);
    const section = renderDownloads();
    gated = true;
    press(gripOf(section, 3), "Home");
    await tick();
    assert.deepEqual(moves, [[3, 0]]);

    // The server has not applied the move yet: this poll still says 1,2,3.
    store.refreshNow();
    await tick();
    assert.deepEqual(rowIds(section), [3, 1, 2], "queue not repainted mid-move");

    releaseGates();
    await settle();
    assert.deepEqual(rowIds(section), [3, 1, 2], "the server's answer agrees");
  } finally {
    releaseGates();
  }
});

test("another client's reorder arrives through the poll and repaints the queue", async () => {
  reset();
  try {
    await serve([1, 2, 3]);
    const section = renderDownloads();
    await serve([2, 3, 1]);
    assert.deepEqual(rowIds(section), [2, 3, 1]);
  } finally {
    releaseGates();
  }
});

test("pointer drag: the row follows the pointer and release sends one move to the drop index", async () => {
  reset();
  try {
    await serve([1, 2, 3]);
    const section = renderDownloads();
    const grip = gripOf(section, 1);
    const down = { type: "pointerdown", button: 0, clientY: 60, preventDefault() {} };
    grip.dispatchEvent(down);
    assert.equal(section.querySelector('.qrow[data-jid="1"]').classList.contains("dragging"), true);

    // Rows 2 and 3 sit at midpoints 100 and 140; 130 is below row 2 only.
    dom.document.dispatchEvent({ type: "pointermove", clientY: 130 });
    assert.deepEqual(rowIds(section), [2, 1, 3], "live: the row moved between its siblings");
    assert.deepEqual(moves, [], "nothing is sent while dragging");

    dom.document.dispatchEvent({ type: "pointerup", clientY: 130 });
    await settle();
    assert.deepEqual(moves, [[1, 1]]);
    assert.deepEqual(rowIds(section), [2, 1, 3]);
    assert.equal(section.querySelector('.qrow[data-jid="1"]').classList.contains("dragging"), false);
  } finally {
    releaseGates();
  }
});

test("pointer drag released where it started, or cancelled, sends nothing and restores the list", async () => {
  reset();
  try {
    await serve([1, 2, 3]);
    const section = renderDownloads();

    gripOf(section, 2).dispatchEvent({ type: "pointerdown", button: 0, clientY: 100, preventDefault() {} });
    dom.document.dispatchEvent({ type: "pointermove", clientY: 100 });
    dom.document.dispatchEvent({ type: "pointerup", clientY: 100 });
    await settle();
    assert.deepEqual(moves, []);
    assert.deepEqual(rowIds(section), [1, 2, 3]);

    gripOf(section, 3).dispatchEvent({ type: "pointerdown", button: 0, clientY: 140, preventDefault() {} });
    dom.document.dispatchEvent({ type: "pointermove", clientY: 0 });
    assert.deepEqual(rowIds(section), [3, 1, 2]);
    dom.document.dispatchEvent({ type: "pointercancel" });
    await settle();
    assert.deepEqual(moves, [], "a cancelled drag is not a move");
    assert.deepEqual(rowIds(section), [1, 2, 3], "the list is back as the server has it");
  } finally {
    releaseGates();
  }
});

test("a single queued job, or a lost connection, leaves the grip inert", async () => {
  reset();
  try {
    await serve([7]);
    const section = renderDownloads();
    assert.equal(gripOf(section, 7).hasAttribute("tabindex"), false);
    press(gripOf(section, 7), "Home");
    await settle();
    assert.deepEqual(moves, []);

    setConnectionLost(true);
    await serve([1, 2]);
    const offline = renderDownloads();
    assert.equal(gripOf(offline, 1).hasAttribute("tabindex"), false);
    assert.equal(gripOf(offline, 1).hasAttribute("role"), false);
  } finally {
    setConnectionLost(false);
    releaseGates();
  }
});
