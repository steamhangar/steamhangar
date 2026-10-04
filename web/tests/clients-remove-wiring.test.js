/**
 * DOM-wiring pins for WP WEB-FEAT-4 in `web/js/components/clients-sheet.js`:
 * "Remove" on a PC row of the PCs (agents) sheet.
 *
 *   - every row has a Remove button named "Remove <pc>"; a row whose id
 *     contains "/" gets the note instead (the server cannot address it);
 *   - Remove opens an alertdialog on top of the sheet naming the PC, what
 *     is deleted and the re-register caveat; focus starts on "Keep";
 *   - Keep (and Escape) close it with NO request and focus back on the
 *     row's button;
 *   - confirm sends ONE `DELETE /v1/clients/<id>`, drops the row at once,
 *     nudges the store (a fresh `GET /v1/clients`), and the row stays gone;
 *   - a 404 is "already gone": same as success, no error line;
 *   - a 500 keeps the row and shows the error inline on it;
 *   - double-confirm while in flight sends one request (gated fetch);
 *   - after a remove, a tick whose list equals the store's previous one
 *     (the agent re-reported) still re-renders: the PC is listed again.
 *
 * Harness: fake-dom.js, Map-backed localStorage with a stored key, a
 * routing `fetch` fake; the REAL store-singleton polls it and is stopped in
 * `after()` (LEARNINGS WEB-FIX-1). Assertions compare text, booleans and
 * counts, never nodes. Ids avoid spaces/quotes: fake-dom's selector engine
 * does not parse them (encoding is pinned in clients-remove.test.js).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, after, beforeEach } from "node:test";
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
  location: { origin: "http://vault.test", pathname: "/settings", reload() {} },
  history: { pushState() {} },
  addEventListener() {},
  removeEventListener() {},
};

const NOW = Date.now();
const isoAgo = (ms) => new Date(NOW - ms).toISOString().replace(/\.\d{3}Z$/, "Z");
const client = (id, extra) => ({
  client_id: id,
  first_seen: isoAgo(9 * 86_400_000),
  last_reported_at: isoAgo(60_000),
  app_count: 3,
  source_addrs: ["10.0.0.9"],
  cache_hits: 1,
  cache_misses: 1,
  bytes_served: 1,
  last_seen_in_cache_log: null,
  bypass_suspected: false,
  agent_version: "0.1.0",
  report_interval_seconds: 600,
  presence: "online",
  offline_after: isoAgo(-25 * 60_000),
  ...extra,
});
const seed = () => [
  client("desk-pc"),
  client("retired-pc", { presence: "offline", last_reported_at: isoAgo(40 * 86_400_000) }),
  client("lab/pc-1"),
];

const server = {
  clients: seed(),
  deleteStatus: 204, // what DELETE answers
  removeOnDelete: true, // false: the agent re-reported, the list keeps the PC
  deletes: [],
  clientGets: 0,
  gate: null, // a promise DELETE waits on, when set
  getGate: null, // a promise GET /v1/clients waits on, when set
};
function respond(status, data) {
  const text = data === undefined ? "" : JSON.stringify(data);
  return { ok: status >= 200 && status < 300, status, json: async () => data, text: async () => text };
}
globalThis.fetch = async (url, init = {}) => {
  const p = new URL(String(url)).pathname;
  const method = init.method || "GET";
  if (method === "DELETE" && p.startsWith("/v1/clients/")) {
    server.deletes.push(p);
    if (server.gate) await server.gate;
    if (server.deleteStatus !== 204) return respond(server.deleteStatus, { detail: server.deleteStatus === 404 ? "Unknown client_id" : "database is locked" });
    if (server.removeOnDelete) {
      const id = decodeURIComponent(p.slice("/v1/clients/".length));
      server.clients = server.clients.filter((c) => c.client_id !== id);
    }
    return respond(204);
  }
  if (p === "/v1/clients") {
    server.clientGets += 1;
    if (server.getGate) await server.getGate;
    return respond(200, server.clients);
  }
  if (p === "/v1/jobs" || p === "/v1/games") return respond(200, []);
  if (p === "/v1/cache/summary") return respond(200, {});
  return respond(404, { detail: "Not Found" });
};

const { store } = await import("../js/store-singleton.js");
const { openClientsSheet } = await import("../js/components/clients-sheet.js");
const { resetModalStack } = await import("../js/lib/modal-stack.js");

after(() => store.stop());

const tick = (ms = 10) => new Promise((r) => setTimeout(r, ms));
async function until(pred, label, timeoutMs = 2000) {
  const start = Date.now();
  while (!pred()) {
    if (Date.now() - start > timeoutMs) throw new Error(`timed out waiting for: ${label}`);
    await tick(5);
  }
}

const divs = () => dom.document.body.querySelectorAll("div");
const pcsSheet = () => divs().find((d) => d.getAttribute("aria-label") === "PCs (agents)") || null;
const sheetIsOpen = () => !!(pcsSheet() && pcsSheet().parentNode.classList.contains("on"));
const confirmEl = () => divs().find((d) => d.getAttribute("data-role") === "remove-confirm") || null;
const confirmIsOpen = () => !!(confirmEl() && confirmEl().parentNode.classList.contains("on"));
const row = (id) => pcsSheet().querySelector(`.jobcard[data-client-id="${id}"]`);
const rowIds = () => pcsSheet().querySelectorAll(".jobcard").map((c) => c.getAttribute("data-client-id"));
const removeBtn = (id) => row(id).querySelector('[data-role="remove-pc"]');
const errLine = (id) => row(id).querySelector('[data-role="remove-error"]');
const click = (el) => el.dispatchEvent({ type: "click" });
const confirmBtn = (role) => confirmEl().querySelector(`[data-role="${role}"]`);

async function openSheetWithSeed() {
  await until(() => Array.isArray(store.snapshot("clients")) && store.snapshot("clients").length === 3, "seeded clients in the store");
  openClientsSheet();
  assert.equal(sheetIsOpen(), true);
}

beforeEach(async () => {
  if (confirmIsOpen()) click(confirmBtn("remove-cancel"));
  const sheet = pcsSheet();
  if (sheet && sheetIsOpen()) click(sheet.querySelectorAll("button").find((b) => b.textContent === "Close"));
  resetModalStack(dom.document);
  server.clients = seed();
  server.deleteStatus = 204;
  server.removeOnDelete = true;
  server.deletes = [];
  server.gate = null;
  server.getGate = null;
  store.refreshNow();
  await until(() => Array.isArray(store.snapshot("clients")) && store.snapshot("clients").length === 3, "store reseeded");
});

test("MUTATION TARGET: each removable row has a 'Remove' button named after its PC; a '/' id gets the note instead", async () => {
  await openSheetWithSeed();
  const btn = removeBtn("retired-pc");
  assert.equal(btn === null, false, "Remove button on the row");
  assert.equal(btn.tagName, "BUTTON");
  assert.equal(btn.getAttribute("type") ?? btn.type, "button");
  assert.equal(btn.textContent, "Remove");
  assert.equal(btn.getAttribute("aria-label"), "Remove retired-pc");
  assert.equal(removeBtn("desk-pc") === null, false, "online PCs too");
  assert.equal(removeBtn("lab/pc-1"), null, "no button for an id the server cannot address");
  assert.match(row("lab/pc-1").querySelector('[data-role="remove-unavailable"]').textContent, /contains "\/"/);
  assert.equal(errLine("retired-pc").hidden, true, "no error line before anything failed");
});

test("MUTATION TARGET: Remove opens the confirm naming the PC, what is deleted, the re-register caveat; focus on Keep", async () => {
  await openSheetWithSeed();
  removeBtn("retired-pc").focus();
  click(removeBtn("retired-pc"));
  assert.equal(confirmIsOpen(), true, "confirm dialog shown");
  const el = confirmEl();
  assert.equal(el.getAttribute("role"), "alertdialog");
  assert.equal(el.getAttribute("aria-modal"), "true");
  assert.equal(el.querySelector("h3").textContent, "Remove retired-pc?");
  const text = el.querySelectorAll("p").map((p) => p.textContent).join(" ");
  assert.match(text, /reports this PC's agent sent .* bypass status/);
  assert.match(text, /Cached games, downloads and cache statistics stay\./);
  assert.match(text, /next report adds the PC back/);
  assert.equal(dom.document.activeElement.textContent, "Keep", "focus starts on the non-destructive button");
  assert.equal(pcsSheet().parentNode.getAttribute("inert") !== null, true, "the sheet behind it is inert");
  assert.equal(server.deletes.length, 0, "opening the dialog sends nothing");
});

test("MUTATION TARGET: Keep sends nothing, closes the dialog and returns focus to the row's button", async () => {
  await openSheetWithSeed();
  removeBtn("retired-pc").focus();
  click(removeBtn("retired-pc"));
  click(confirmBtn("remove-cancel"));
  await tick(30);
  assert.equal(confirmIsOpen(), false);
  assert.equal(server.deletes.length, 0, "no DELETE on cancel");
  assert.deepEqual(rowIds(), ["desk-pc", "retired-pc", "lab/pc-1"], "nothing removed");
  assert.equal(dom.document.activeElement.getAttribute("aria-label"), "Remove retired-pc");
  assert.equal(sheetIsOpen(), true, "the sheet stays open");
});

test("Escape closes only the confirm, sends nothing", async () => {
  await openSheetWithSeed();
  click(removeBtn("retired-pc"));
  dom.document.dispatchEvent(fakeKeyEvent("Escape"));
  await tick(30);
  assert.equal(confirmIsOpen(), false);
  assert.equal(sheetIsOpen(), true, "the sheet under it stays open");
  assert.equal(server.deletes.length, 0);
});

test("MUTATION TARGET: confirm sends one DELETE, the row disappears, the store is refreshed and the row stays gone", async () => {
  await openSheetWithSeed();
  const getsBefore = server.clientGets;
  click(removeBtn("retired-pc"));
  click(confirmBtn("remove-confirm-yes"));
  await until(() => !rowIds().includes("retired-pc"), "row removed");
  assert.deepEqual(server.deletes, ["/v1/clients/retired-pc"]);
  assert.equal(confirmIsOpen(), false, "dialog closed");
  await until(() => server.clientGets > getsBefore, "store refreshed after the remove");
  await until(() => store.snapshot("clients").length === 2, "store snapshot follows");
  await tick(20);
  assert.deepEqual(rowIds(), ["desk-pc", "lab/pc-1"], "still gone after the refresh tick");
  assert.equal(dom.document.activeElement.getAttribute("role"), "dialog", "focus lands on the sheet, not on a removed node");
});

test("MUTATION TARGET: the row disappears at once, before the refresh poll answers", async () => {
  await openSheetWithSeed();
  let release;
  server.getGate = new Promise((r) => (release = r));
  try {
    const getsBefore = server.clientGets;
    click(removeBtn("retired-pc"));
    click(confirmBtn("remove-confirm-yes"));
    await until(() => server.clientGets > getsBefore, "refresh poll issued (and held)");
    assert.deepEqual(rowIds(), ["desk-pc", "lab/pc-1"], "gone while the poll is still held");
  } finally {
    server.getGate = null;
    release();
  }
});

test("MUTATION TARGET: a 404 (already gone) is treated as success: row removed, no error line", async () => {
  await openSheetWithSeed(); // the sheet still lists retired-pc ...
  server.deleteStatus = 404; // ... but another tab removed it meanwhile
  server.clients = seed().filter((c) => c.client_id !== "retired-pc");
  click(removeBtn("retired-pc"));
  click(confirmBtn("remove-confirm-yes"));
  await until(() => !rowIds().includes("retired-pc"), "row removed on 404");
  assert.equal(server.deletes.length, 1);
  assert.equal(
    pcsSheet().querySelectorAll('[data-role="remove-error"]').some((e) => !e.hidden),
    false,
    "no visible error line anywhere",
  );
});

test("MUTATION TARGET: a 500 keeps the row and shows the error inline on it", async () => {
  server.deleteStatus = 500;
  await openSheetWithSeed();
  click(removeBtn("retired-pc"));
  click(confirmBtn("remove-confirm-yes"));
  await until(() => errLine("retired-pc").hidden === false, "inline error shown");
  assert.equal(errLine("retired-pc").textContent, "Could not remove retired-pc: database is locked");
  assert.equal(errLine("retired-pc").getAttribute("role"), "alert");
  assert.equal(confirmIsOpen(), false, "dialog closed");
  assert.deepEqual(rowIds(), ["desk-pc", "retired-pc", "lab/pc-1"], "row kept");
  assert.equal(errLine("desk-pc").hidden, true, "only the failed row shows it");
  assert.equal(dom.document.activeElement.getAttribute("aria-label"), "Remove retired-pc", "focus back on the row's button");
});

test("confirm is click-guarded while in flight: double confirm sends one DELETE; Keep is aria-disabled", async () => {
  let release;
  server.gate = new Promise((r) => (release = r));
  await openSheetWithSeed();
  click(removeBtn("retired-pc"));
  const yes = confirmBtn("remove-confirm-yes");
  click(yes);
  await tick(5);
  assert.equal(yes.getAttribute("aria-disabled"), "true");
  assert.equal(confirmBtn("remove-cancel").getAttribute("aria-disabled"), "true");
  assert.equal(yes.textContent, "Removing…");
  click(yes);
  click(confirmBtn("remove-cancel"));
  assert.equal(confirmIsOpen(), true, "Keep does nothing while the request runs");
  release();
  await until(() => !rowIds().includes("retired-pc"), "row removed");
  assert.equal(server.deletes.length, 1, "two clicks, one request");
  assert.equal(yes.getAttribute("aria-disabled"), null, "re-enabled afterwards");
});

test("MUTATION TARGET: after a remove, a tick equal to the previous list (agent re-reported) lists the PC again", async () => {
  server.removeOnDelete = false;
  await openSheetWithSeed();
  let release;
  server.getGate = new Promise((r) => (release = r));
  try {
    const getsBefore = server.clientGets;
    click(removeBtn("retired-pc"));
    click(confirmBtn("remove-confirm-yes"));
    // Hold the refresh poll so the local drop is observable first (without
    // the gate this test passed vacuously: it saw the row before the drop).
    await until(() => server.clientGets > getsBefore, "refresh poll issued (and held)");
    assert.deepEqual(rowIds(), ["desk-pc", "lab/pc-1"], "dropped locally first");
  } finally {
    server.getGate = null;
    release();
  }
  await until(() => rowIds().length === 3, "the server's list wins on the next tick", 3000);
  assert.deepEqual(rowIds(), ["desk-pc", "retired-pc", "lab/pc-1"]);
});
