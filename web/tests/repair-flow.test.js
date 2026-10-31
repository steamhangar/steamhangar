/**
 * WP CACHE-REPAIR-UI-1 — `components/repair-flow.js`, the shared Repair
 * dialog: the dry run (no body) -> the confirm with the dry run's counts ->
 * `{"execute": true}` only after "Delete and repair" -> the result with
 * "Show job". Error paths: a failed dry run is a toast and no dialog; a
 * failed execute keeps the dialog with an alert line; Escape closes, but
 * never while the execute request is in flight. Keyboard: "Cancel" has
 * focus on open, "Show job" on the result, focus returns to the invoker.
 *
 * Harness: fake-dom.js, a stored API key and a routing `fetch` fake that
 * records every request and can hold an answer (gated) to observe the
 * in-flight state. `store.refreshNow` is counted, not run.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, beforeEach, after } from "node:test";
import assert from "node:assert/strict";
import { createFakeDom, fakeKeyEvent } from "./fake-dom.js";

const dom = createFakeDom();
globalThis.document = dom.document;
const storage = new Map([["steamvault.apiKey", "test-key"]]);
globalThis.window = {
  ...dom.window,
  localStorage: {
    getItem: (k) => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => storage.set(k, String(v)),
  },
  location: { origin: "http://vault.test", pathname: "/library", reload() {} },
};
const shell = {};
for (const id of ["toast", "toast-text"]) shell[id] = dom.document.createElement(id === "toast-text" ? "span" : "div");
const origGetById = dom.document.getElementById.bind(dom.document);
dom.document.getElementById = (id) => shell[id] || origGetById(id);

const MB = 1_048_576;
const server = {
  requests: [], // {path, body}
  answers: [], // queued: {status, data} | {network: true}
  gate: null, // a promise the next answer waits for
};
function respond(status, data) {
  const text = JSON.stringify(data);
  return { ok: status >= 200 && status < 300, status, json: async () => data, text: async () => text };
}
globalThis.fetch = async (url, init = {}) => {
  const u = new URL(String(url));
  const method = (init.method || "GET").toUpperCase();
  if (method !== "POST" || !/^\/v1\/cache\/\d+\/repair$/.test(u.pathname)) return respond(404, { detail: "Not Found" });
  server.requests.push({ path: u.pathname, body: init.body === undefined ? undefined : JSON.parse(init.body) });
  // The answer is taken when the request is SENT, so two gated requests
  // keep their own answers whatever order they are released in.
  const a = server.answers.shift();
  const gate = server.gate;
  if (gate) await gate;
  if (!a) throw new Error("no answer queued");
  if (a.network) throw new TypeError("fetch failed");
  return respond(a.status, a.data);
};

const { initToast } = await import("../js/components/toast.js");
initToast();
const { store } = await import("../js/store-singleton.js");
after(() => store.stop()); // the singleton starts its loops when a key is stored
let refreshes = 0;
store.refreshNow = () => {
  refreshes += 1;
};
const { startRepair, closeRepair, repairFlowState } = await import("../js/components/repair-flow.js");

const plan = (over = {}) => ({
  appid: 40,
  mode: "dry-run",
  execute: false,
  damaged_chunks: 3,
  damaged_bytes: 3 * MB,
  depots: [
    { depotid: 401, manifestid: "1", damaged_chunks: 2, damaged_bytes: 2 * MB },
    { depotid: 402, manifestid: "2", damaged_chunks: 1, damaged_bytes: 1 * MB },
  ],
  unreadable_depots: [],
  exceeds_cap: false,
  max_files: 2000,
  removed_chunks: 0,
  removed_bytes: 0,
  skipped: {},
  job_id: null,
  job_status: null,
  job_deduplicated: null,
  ...over,
});
const executed = (over = {}) =>
  plan({ mode: "execute", execute: true, removed_chunks: 3, removed_bytes: 3 * MB, job_id: 812, job_status: "queued", job_deduplicated: false, ...over });

const tick = (ms = 5) => new Promise((r) => setTimeout(r, ms));
async function until(pred, label, timeoutMs = 2000) {
  const start = Date.now();
  while (!pred()) {
    if (Date.now() - start > timeoutMs) throw new Error(`timed out waiting for: ${label}`);
    await tick();
  }
}
const backdrop = () => dom.document.body.querySelector(".dialog-backdrop");
const dlg = () => dom.document.body.querySelector('[data-role="repair-dialog"]');
const part = (r) => dlg().querySelector(`[data-role="${r}"]`);
const isOpen = () => !!backdrop() && backdrop().classList.contains("on");
const click = (el) => el.dispatchEvent({ type: "click" });
const toastText = () => shell["toast-text"].textContent;

let invoker;
beforeEach(() => {
  closeRepair();
  server.requests = [];
  server.answers = [];
  server.gate = null;
  refreshes = 0;
  shell["toast-text"].textContent = "";
  invoker = dom.document.createElement("button");
  dom.appRoot.appendChild(invoker);
  invoker.focus();
});

async function openConfirm(report = plan(), shown = []) {
  server.answers.push({ status: 200, data: report });
  await startRepair({ appid: 40, title: "Broken Bits", onShowJob: (id) => shown.push(id) });
}

test("MUTATION PIN: dry run (no body) -> confirm with the counts -> execute {execute:true} -> result -> Show job", async () => {
  const shown = [];
  await openConfirm(plan(), shown);
  assert.deepEqual(server.requests, [{ path: "/v1/cache/40/repair", body: undefined }], "the dry run sends no body");
  assert.equal(isOpen(), true);
  assert.equal(repairFlowState(), "confirm");
  assert.equal(dlg().getAttribute("role"), "alertdialog");
  assert.equal(dlg().getAttribute("aria-modal"), "true");
  assert.equal(dom.document.getElementById("app").getAttribute("inert"), "", "background inert");
  assert.equal(dlg().querySelector("h3").textContent, "Repair Broken Bits?");
  assert.equal(
    part("repair-text").textContent,
    "3 files (3.0 MB) in the cache have the wrong size. Repair deletes them, then queues a forced download that fetches fresh copies.",
  );
  assert.deepEqual(
    part("repair-depots").querySelectorAll("li").map((li) => li.textContent),
    ["Depot 401 · 2 files · 2.0 MB", "Depot 402 · 1 file · 1.0 MB"],
  );
  assert.equal(part("repair-unreadable").hidden, true);
  assert.equal(part("repair-error").hidden, true);
  assert.equal(part("repair-cancel").textContent, "Cancel");
  assert.equal(part("repair-ok").textContent, "Delete and repair");
  assert.equal(dom.document.activeElement, part("repair-cancel"), "the non-destructive button has focus");
  assert.equal(server.requests.length, 1, "MUTATION PIN: nothing executed before the confirm");

  server.answers.push({ status: 202, data: executed() });
  click(part("repair-ok"));
  await until(() => repairFlowState() === "done", "result");
  assert.deepEqual(server.requests[1], { path: "/v1/cache/40/repair", body: { execute: true } });
  assert.equal(dlg().querySelector("h3").textContent, "Repair started");
  assert.equal(part("repair-text").textContent, "Deleted 3 files (3.0 MB). Job #812 fetches fresh copies.");
  assert.equal(part("repair-ok").textContent, "Show job");
  assert.equal(part("repair-ok").hidden, false);
  // Review fix: the non-kit primary class (the dialog is outside `.kit`).
  assert.equal(part("repair-ok").className, "btn primary sm");
  assert.equal(part("repair-cancel").textContent, "Close");
  assert.equal(dom.document.activeElement, part("repair-ok"), "Show job has focus on the result");
  assert.equal(refreshes, 1, "the store re-reads after the execute");

  click(part("repair-ok"));
  assert.deepEqual(shown, [812]);
  assert.equal(isOpen(), false);
  assert.equal(repairFlowState(), "idle");
  assert.equal(dom.document.getElementById("app").hasAttribute("inert"), false, "background usable again");
});

test("Cancel closes without executing and gives focus back to the invoker", async () => {
  await openConfirm();
  click(part("repair-cancel"));
  assert.equal(isOpen(), false);
  assert.equal(server.requests.length, 1);
  assert.equal(dom.document.activeElement, invoker);
});

test("Escape closes the confirm, but not while the execute is in flight", async () => {
  await openConfirm();
  dom.document.dispatchEvent(fakeKeyEvent("Escape"));
  assert.equal(isOpen(), false);
  assert.equal(server.requests.length, 1);

  await openConfirm();
  let release;
  server.gate = new Promise((r) => (release = r));
  server.answers.push({ status: 202, data: executed() });
  click(part("repair-ok"));
  await until(() => server.requests.length === 3, "execute sent");
  assert.equal(repairFlowState(), "executing");
  assert.equal(part("repair-ok").textContent, "Repairing…");
  assert.equal(part("repair-ok").getAttribute("aria-disabled"), "true");
  assert.equal(part("repair-cancel").getAttribute("aria-disabled"), "true");
  dom.document.dispatchEvent(fakeKeyEvent("Escape"));
  click(part("repair-cancel"));
  click(part("repair-ok"));
  assert.equal(isOpen(), true, "Escape and Cancel are ignored while executing");
  release();
  await until(() => repairFlowState() === "done", "result");
  assert.equal(server.requests.length, 3, "MUTATION PIN: a second click never sent a second execute");
});

test("MUTATION PIN: error path — a failed dry run is a toast, no dialog, nothing executed", async () => {
  server.answers.push({
    status: 409,
    data: { detail: "Job 5 for app 40 is running; repairing chunk files under an active job would race it." },
  });
  await startRepair({ appid: 40, title: "Broken Bits" });
  assert.equal(isOpen(), false);
  assert.equal(repairFlowState(), "idle");
  assert.equal(
    toastText(),
    "A download that uses these files is queued, running or paused. Repair once it has finished — nothing was deleted.",
  );
  assert.equal(refreshes, 1);

  server.answers.push({ network: true });
  await startRepair({ appid: 40, title: "Broken Bits" });
  assert.equal(toastText(), "Could not reach vault-api — nothing was changed.");
  assert.equal(server.requests.every((r) => r.body === undefined), true);
});

test("MUTATION PIN: error path — a failed execute keeps the dialog with the reason (role=alert); retry works", async () => {
  await openConfirm();
  server.answers.push({ status: 422, data: { detail: "2500 damaged chunk files exceed the repair cap of 2000; nothing was deleted." } });
  click(part("repair-ok"));
  await until(() => part("repair-error").hidden === false, "error line");
  assert.equal(repairFlowState(), "confirm");
  assert.equal(part("repair-error").getAttribute("role"), "alert");
  assert.equal(part("repair-error").textContent, "2500 damaged chunk files exceed the repair cap of 2000; nothing was deleted.");
  assert.equal(part("repair-ok").textContent, "Delete and repair", "can be retried");
  assert.equal(part("repair-ok").hasAttribute("aria-disabled"), false);

  server.answers.push({ status: 500, data: { detail: "boom" } });
  click(part("repair-ok"));
  await until(() => part("repair-error").textContent.startsWith("The repair did not answer"), "unknown outcome");
  assert.equal(part("repair-error").textContent, "The repair did not answer cleanly — some files may already be deleted. Re-checking Downloads…");
  assert.equal(refreshes, 1, "an unknown outcome re-reads");
});

test("nothing damaged: a toast and a re-read, no dialog", async () => {
  server.answers.push({ status: 200, data: plan({ damaged_chunks: 0, damaged_bytes: 0, depots: [] }) });
  await startRepair({ appid: 40, title: "Broken Bits" });
  assert.equal(isOpen(), false);
  assert.equal(toastText(), "Nothing to repair — no chunk file with the wrong size was found.");
  assert.equal(refreshes, 1);
});

test("over the cap: the dialog explains it with Close only, and cannot execute", async () => {
  await openConfirm(plan({ damaged_chunks: 2500, damaged_bytes: 2500 * MB, exceeds_cap: true }));
  assert.equal(repairFlowState(), "blocked");
  assert.equal(dlg().querySelector("h3").textContent, "Can't repair Broken Bits");
  assert.match(part("repair-text").textContent, /^2500 files have the wrong size — more than the repair limit of 2000\./);
  assert.equal(part("repair-ok").hidden, true);
  assert.equal(part("repair-cancel").textContent, "Close");
  assert.equal(dom.document.activeElement, part("repair-cancel"));
  click(part("repair-ok"));
  assert.equal(server.requests.length, 1, "no execute from the blocked dialog");
  click(part("repair-cancel"));
  assert.equal(isOpen(), false);
});

test("unreadable depots are named; an execute that removed nothing says so and offers no job", async () => {
  await openConfirm(plan({ unreadable_depots: [403] }));
  assert.equal(part("repair-unreadable").hidden, false);
  assert.equal(part("repair-unreadable").textContent, "Depot 403 could not be read and is left out.");
  server.answers.push({ status: 200, data: executed({ removed_chunks: 0, removed_bytes: 0, job_id: null, skipped: { size_matches: 3 } }) });
  click(part("repair-ok"));
  await until(() => repairFlowState() === "done", "result");
  assert.equal(dlg().querySelector("h3").textContent, "Nothing was deleted");
  assert.equal(part("repair-ok").hidden, true, "no job to show");
  assert.equal(dom.document.activeElement, part("repair-cancel"));
});

test("a second start while a flow is open is a no-op; closeRepair resets it", async () => {
  await openConfirm();
  await startRepair({ appid: 41, title: "Other" });
  assert.equal(server.requests.length, 1);
  assert.equal(dlg().querySelector("h3").textContent, "Repair Broken Bits?");
  closeRepair();
  assert.equal(isOpen(), false);
  assert.equal(repairFlowState(), "idle");
});

// ---------------------------------------------------------------------
// Review fix: answers are tied to the flow that sent them
// ---------------------------------------------------------------------

function gated() {
  let release;
  const gate = new Promise((r) => (release = r));
  return { gate, release };
}
const settle = () => tick(10);

test("MUTATION PIN: a late dry-run answer of a closed flow never lands in the next game's dialog", async () => {
  const a = gated();
  server.gate = a.gate;
  server.answers.push({ status: 200, data: plan({ appid: 40, damaged_chunks: 9, damaged_bytes: 9 * MB }) });
  const startA = startRepair({ appid: 40, title: "Game A" });
  await until(() => server.requests.length === 1, "A's dry run sent");
  closeRepair(); // sheet closed (Escape) while A's dry run is pending
  server.gate = null;
  server.answers.push({ status: 200, data: plan({ appid: 41, damaged_chunks: 2, damaged_bytes: 2 * MB, depots: [] }) });
  await startRepair({ appid: 41, title: "Game B" });
  assert.equal(dlg().querySelector("h3").textContent, "Repair Game B?");
  a.release();
  await startA;
  await settle();
  assert.equal(repairFlowState(), "confirm");
  assert.equal(dlg().querySelector("h3").textContent, "Repair Game B?", "still B's dialog");
  assert.match(part("repair-text").textContent, /^2 files \(2\.0 MB\)/, "B's counts, not A's 9");

  // B's execute path is B's own.
  server.answers.push({ status: 202, data: executed({ appid: 41, job_id: 990 }) });
  click(part("repair-ok"));
  await until(() => repairFlowState() === "done", "B done");
  assert.equal(server.requests.at(-1).path, "/v1/cache/41/repair");
  assert.deepEqual(server.requests.at(-1).body, { execute: true });
  assert.match(part("repair-text").textContent, /Job #990/);
});

test("MUTATION PIN: a late FAILED dry run of a closed flow neither cancels the next flow nor toasts", async () => {
  const a = gated();
  server.gate = a.gate;
  server.answers.push({ status: 409, data: { detail: "Job 5 for app 40 is running; ..." } });
  const startA = startRepair({ appid: 40, title: "Game A" });
  await until(() => server.requests.length === 1, "A's dry run sent");
  closeRepair();
  server.gate = null;
  server.answers.push({ status: 200, data: plan({ appid: 41 }) });
  await startRepair({ appid: 41, title: "Game B" });
  a.release();
  await startA;
  await settle();
  assert.equal(repairFlowState(), "confirm", "B's confirm stays open");
  assert.equal(dlg().querySelector("h3").textContent, "Repair Game B?");
  assert.equal(toastText(), "", "no toast for A's stale failure");
});

test("MUTATION PIN: a late execute answer of a closed flow never lands in a newer executing flow", async () => {
  await openConfirm(plan({ appid: 40 }));
  const a = gated();
  server.gate = a.gate;
  server.answers.push({ status: 202, data: executed({ appid: 40, job_id: 111 }) });
  click(part("repair-ok"));
  await until(() => repairFlowState() === "executing", "A executing");
  closeRepair(); // the sheet goes away while A's execute is in flight
  // B: dry run answered at once, its execute held by its own gate.
  server.gate = null;
  server.answers.push({ status: 200, data: plan({ appid: 41 }) });
  await startRepair({ appid: 41, title: "Game B" });
  const b = gated();
  server.gate = b.gate;
  server.answers.push({ status: 202, data: executed({ appid: 41, job_id: 222 }) });
  click(part("repair-ok"));
  await until(() => repairFlowState() === "executing", "B executing");
  a.release();
  await settle();
  assert.equal(repairFlowState(), "executing", "A's answer did not finish B");
  assert.equal(part("repair-ok").textContent, "Repairing…");
  assert.ok(refreshes >= 1, "A's late answer still re-reads (files may be gone)");
  b.release();
  await until(() => repairFlowState() === "done", "B done");
  assert.match(part("repair-text").textContent, /Job #222/);
});

test("nit fix: Close after a successful repair focuses the caller's fallback when the invoker is gone", async () => {
  const fallback = dom.document.createElement("button");
  server.answers.push({ status: 200, data: plan() });
  await startRepair({ appid: 40, title: "Broken Bits", focusFallback: () => fallback });
  server.answers.push({ status: 202, data: executed() });
  click(part("repair-ok"));
  await until(() => repairFlowState() === "done", "result");
  invoker.isConnected = false; // the re-render dropped the Repair button
  click(part("repair-cancel"));
  assert.equal(dom.document.activeElement, fallback);
});
