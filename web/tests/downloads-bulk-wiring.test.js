/**
 * DOM wiring of Pause all / Resume all in `views/downloads.js`
 * (WP WEB-FEAT-5): bar visibility, the confirm dialog (count, cancel,
 * confirm), request order and concurrency against a routing `fetch` fake,
 * the aggregate toast, 409/404 as skipped, the store refresh after a run,
 * the busy state, and the offline gate.
 *
 * Harness: fake-dom.js, a stored API key, and a routing `fetch` fake the
 * REAL store-singleton polls. Pause/resume answers can be held (gated) to
 * observe in-flight state. The store is stopped in `after`, and every
 * test releases its gates in `finally` so a broken run fails instead of
 * hanging (docs/LEARNINGS.md, WEB-FIX-1). Text/booleans only, never node
 * assertions.
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

// ---- routing fetch fake ------------------------------------------------
let JOBS = [];
const calls = []; // "POST /v1/jobs/3/pause", "GET /v1/jobs", ...
/** id -> {status, detail} to answer a pause/resume with an error. */
let controlErrors = {};
/** When true, pause/resume answers wait until `releaseGates()`. */
let gated = false;
let held = [];
let inFlight = 0;
let peakInFlight = 0;

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
globalThis.fetch = async (url, init = {}) => {
  const u = new URL(String(url));
  const method = (init.method || "GET").toUpperCase();
  calls.push(`${method} ${u.pathname}`);
  if (u.pathname === "/v1/jobs") return respond(200, JOBS);
  if (u.pathname === "/v1/games") return respond(200, []);
  if (u.pathname === "/v1/clients") return respond(200, []);
  if (u.pathname === "/v1/cache/summary") return respond(200, {});
  const m = u.pathname.match(/^\/v1\/jobs\/(\d+)\/(pause|resume)$/);
  if (m && method === "POST") {
    const id = Number(m[1]);
    inFlight += 1;
    peakInFlight = Math.max(peakInFlight, inFlight);
    if (gated) await new Promise((r) => held.push(r));
    inFlight -= 1;
    const err = controlErrors[id];
    if (err) return respond(err.status, { detail: err.detail });
    return respond(200, { job_id: id, status: m[2] === "pause" ? "paused" : "queued", outcome: "x", detail: "" });
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

const tick = (ms = 25) => new Promise((r) => setTimeout(r, ms));
const click = (el) => el.dispatchEvent({ type: "click" });
const job = (id, status, extra = {}) => ({
  id,
  appid: 1000 + id,
  type: "prefill",
  status,
  stop_request: null,
  created_at: "2026-10-04T10:00:00Z",
  ...extra,
});

/** Swap the server's job list and let the store deliver it to the view. */
async function serve(jobs) {
  JOBS = jobs;
  store.refreshNow();
  await tick();
}
function reset() {
  calls.length = 0;
  controlErrors = {};
  gated = false;
  held = [];
  inFlight = 0;
  peakInFlight = 0;
  shell["toast-text"].textContent = "";
  shell.toast.classList.remove("warn");
}
const q = (root, role) => root.querySelector(`[data-role="${role}"]`);
const dialogRoot = () => dom.document.body.querySelector('[data-role="bulk-pause-confirm"]');
const dialogOpen = () => dialogRoot().parentNode.classList.contains("on");
const controlCalls = () => calls.filter((c) => c.startsWith("POST /v1/jobs/"));
const toastText = () => shell["toast-text"].textContent;
const toastWarn = () => shell.toast.classList.contains("warn");
async function settle() {
  for (let i = 0; i < 10; i += 1) await tick(5);
}

test("bar visibility: hidden when idle, Pause all for queued/running, Resume all for paused, scheduler note", async () => {
  reset();
  try {
    await serve([job(1, "done")]);
    const section = renderDownloads();
    const bar = section.querySelector(".dl-bulk");
    assert.equal(bar.hidden, true, "nothing to pause or resume: no bar");

    await serve([job(1, "running"), job(2, "queued"), job(3, "queued", { type: "gc" })]);
    assert.equal(bar.hidden, false);
    assert.equal(q(section, "bulk-pause").hidden, false);
    assert.equal(q(section, "bulk-resume").hidden, true);
    assert.equal(q(section, "bulk-pause").textContent, "Pause all");
    assert.equal(q(section, "bulk-pause").getAttribute("aria-label"), "Pause all 2 downloads", "the GC job is not counted");
    assert.equal(section.querySelector(".dl-bulk-note").textContent, "Pausing does not stop the scheduler: its next sweep can still queue new downloads.");
    assert.equal(bar.getAttribute("role"), "group");

    await serve([job(4, "paused"), job(5, "paused")]);
    assert.equal(q(section, "bulk-pause").hidden, true);
    assert.equal(q(section, "bulk-resume").hidden, false);
    assert.equal(q(section, "bulk-resume").getAttribute("aria-label"), "Resume all 2 downloads");

    // A running job whose pause was already requested arrives through the
    // stop_request PATCH path, not a rebuild — the bar must follow it too.
    await serve([job(6, "running")]);
    assert.equal(q(section, "bulk-pause").hidden, false);
    await serve([job(6, "running", { stop_request: "pause" })]);
    assert.equal(bar.hidden, true, "the only running job is already pausing");
  } finally {
    releaseGates();
  }
});

test("Pause all asks first, naming the count; Keep downloading sends nothing", async () => {
  reset();
  try {
    await serve([job(1, "running"), job(2, "queued"), job(3, "queued")]);
    const section = renderDownloads();
    click(q(section, "bulk-pause"));
    assert.equal(dialogOpen(), true);
    assert.equal(dialogRoot().querySelector("h3").textContent, "Pause 3 downloads?");
    assert.equal(dom.document.activeElement.textContent, "Keep downloading", "safe default focused");

    click(q(dialogRoot(), "bulk-pause-no"));
    await settle();
    assert.equal(dialogOpen(), false);
    assert.deepEqual(controlCalls(), []);
  } finally {
    releaseGates();
  }
});

test("confirming pauses every queued job before the running one, toasts the result, then refreshes the store", async () => {
  reset();
  try {
    await serve([job(1, "running"), job(2, "queued"), job(3, "queued"), job(4, "paused")]);
    const section = renderDownloads();
    click(q(section, "bulk-pause"));
    calls.length = 0;
    click(q(dialogRoot(), "bulk-pause-yes"));
    await settle();

    assert.equal(dialogOpen(), false);
    assert.deepEqual(controlCalls(), ["POST /v1/jobs/2/pause", "POST /v1/jobs/3/pause", "POST /v1/jobs/1/pause"]);
    assert.equal(toastText(), "Paused 3 downloads");
    assert.equal(toastWarn(), false);
    const lastPause = calls.lastIndexOf("POST /v1/jobs/1/pause");
    assert.ok(calls.indexOf("GET /v1/jobs", lastPause) > lastPause, "jobs re-polled after the run");
  } finally {
    releaseGates();
  }
});

test("409/404 count as skipped, a 5xx as a failure with its reason (warn toast)", async () => {
  reset();
  try {
    await serve([job(1, "running"), job(2, "queued"), job(3, "queued"), job(4, "queued")]);
    controlErrors = {
      2: { status: 409, detail: "Job 2 already finished" },
      3: { status: 404, detail: "Unknown job id 3" },
    };
    const section = renderDownloads();
    click(q(section, "bulk-pause"));
    click(q(dialogRoot(), "bulk-pause-yes"));
    await settle();
    assert.equal(toastText(), "Paused 2 of 4 — 2 skipped (finished or changed meanwhile)");
    assert.equal(toastWarn(), false, "a skipped job is not an error");

    reset();
    await serve([job(1, "running"), job(2, "queued")]);
    controlErrors = { 2: { status: 503, detail: "vault-api is restarting" } };
    click(q(section, "bulk-pause"));
    click(q(dialogRoot(), "bulk-pause-yes"));
    await settle();
    assert.equal(toastText(), "Paused 1 of 2 — 1 could not be paused: vault-api is restarting");
    assert.equal(toastWarn(), true);
  } finally {
    releaseGates();
  }
});

test("Resume all needs no confirmation and resumes every paused job", async () => {
  reset();
  try {
    await serve([job(7, "paused"), job(8, "paused"), job(9, "queued")]);
    const section = renderDownloads();
    click(q(section, "bulk-resume"));
    await settle();
    assert.equal(dialogOpen(), false);
    assert.deepEqual(controlCalls(), ["POST /v1/jobs/7/resume", "POST /v1/jobs/8/resume"]);
    assert.equal(toastText(), "Resumed 2 downloads");
  } finally {
    releaseGates();
  }
});

test("while a run is in flight both buttons are aria-disabled, a second click sends nothing, and at most 4 requests overlap", async () => {
  reset();
  try {
    const paused = Array.from({ length: 10 }, (_, i) => job(20 + i, "paused"));
    await serve([job(1, "running"), ...paused]);
    const section = renderDownloads();
    const pauseBtn = q(section, "bulk-pause");
    const resumeBtn = q(section, "bulk-resume");
    gated = true;
    resumeBtn.focus();
    click(resumeBtn);
    await tick();

    assert.equal(resumeBtn.textContent, "Resuming…");
    assert.equal(resumeBtn.getAttribute("aria-disabled"), "true");
    assert.equal(pauseBtn.getAttribute("aria-disabled"), "true");
    assert.equal(dom.document.activeElement, resumeBtn, "focus stays on the busy button");
    assert.equal(controlCalls().length, 4, "four in flight, the rest wait");

    click(resumeBtn);
    click(pauseBtn);
    await tick();
    assert.equal(dialogOpen(), false, "Pause all cannot open while busy");
    assert.equal(controlCalls().length, 4);

    // Let them through one batch at a time.
    while (controlCalls().length < 10 || held.length) {
      const list = held;
      held = [];
      for (const go of list) go();
      await tick(5);
    }
    gated = false;
    await settle();
    assert.equal(peakInFlight, 4);
    assert.equal(resumeBtn.getAttribute("aria-disabled"), null);
    assert.equal(toastText(), "Resumed 10 downloads");
  } finally {
    releaseGates();
  }
});

test("offline: the bulk buttons are disabled with the connection title", async () => {
  reset();
  try {
    await serve([job(1, "running"), job(2, "paused")]);
    const section = renderDownloads();
    setConnectionLost(true);
    for (const role of ["bulk-pause", "bulk-resume"]) {
      assert.equal(q(section, role).disabled, true, role);
      assert.equal(q(section, role).title, OFFLINE_CONTROL_TITLE, role);
    }
    click(q(section, "bulk-resume"));
    await settle();
    assert.deepEqual(controlCalls(), []);
    setConnectionLost(false);
    assert.equal(q(section, "bulk-pause").disabled, false);
  } finally {
    setConnectionLost(false);
    releaseGates();
  }
});
