/**
 * DOM-wiring pins for WP D15-NAMES on the Clients page
 * (`web/js/views/clients.js`):
 *
 *   - the card title is the server's `display_name`; a card with a
 *     `name_addr` has a "Rename" button (accessible name says which), one
 *     without has none;
 *   - Rename opens the dialog (role=dialog, aria-modal, labelled; focus in
 *     the field, prefilled with the current name);
 *   - an invalid name shows the error and sends nothing; a valid one sends
 *     `PUT /v1/client-names/ip/{name_addr}` with the trimmed name, closes
 *     the dialog and refetches the overview;
 *   - a failed save keeps the dialog open with the error (role=alert);
 *   - "Remove name" sends DELETE; the handler's own 404 counts as done;
 *   - Escape closes; focus returns to the card's CURRENT Rename button,
 *     also after a poll rebuilt the list.
 *
 * Harness: fake-dom.js, a routing `fetch` fake recording method and body.
 * Assertions compare text, never nodes (LEARNINGS WEB-FIX-1).
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
  location: { origin: "http://vault.test", pathname: "/clients", reload() {} },
  history: { pushState() {} },
  addEventListener() {},
  removeEventListener() {},
};

const iso = (msAgo) => new Date(Date.now() - msAgo).toISOString().replace(/\.\d{3}Z$/, "Z");
const base = {
  connected: true, agent_presence: "online", cache_active: true, last_active: iso(60_000), last_seen_in_cache_log: iso(60_000),
  last_reported_at: iso(60_000), installed_appids: [], installed_app_count: 0, stats: null, loading_now: null, recent_apps: null,
  recent_unattributed_bytes: null,
};
const server = { names: new Map(), calls: [], putStatus: 200, deleteStatus: 204, overviews: 0 };

function overview() {
  const agentName = server.names.get("10.0.0.5") ?? null;
  const addrName = server.names.get("10.0.0.9") ?? null;
  return {
    event_feed: { enabled: true, state: "ok", last_sweep_at: iso(60_000), sweep_interval_minutes: 5, traffic_since: null },
    range: "7d", tier: "1h", range_start: null, active_window_minutes: 15,
    clients: [
      { ...base, key: "agent:DEMON", kind: "agent", client_id: "DEMON", addresses: ["10.0.0.5"], name: agentName, name_addr: "10.0.0.5", display_name: agentName ?? "DEMON" },
      { ...base, key: "addr:10.0.0.9", kind: "address", client_id: null, addresses: ["10.0.0.9"], agent_presence: null, last_reported_at: null, installed_appids: null, installed_app_count: null, name: addrName, name_addr: "10.0.0.9", display_name: addrName ?? "10.0.0.9" },
      // An old agent behind a published port: no address, cannot be named.
      { ...base, key: "agent:old", kind: "agent", client_id: "old", addresses: [], name: null, name_addr: null, display_name: "old" },
    ],
  };
}
function respond(status, data) {
  const text = data === null ? "" : JSON.stringify(data);
  return { ok: status >= 200 && status < 300, status, json: async () => data, text: async () => text };
}
globalThis.fetch = async (url, init = {}) => {
  const u = new URL(String(url));
  const method = init.method || "GET";
  if (u.pathname === "/v1/clients/overview") {
    server.overviews += 1;
    return respond(200, overview());
  }
  const m = /^\/v1\/client-names\/ip\/(.+)$/.exec(u.pathname);
  if (m) {
    const addr = decodeURIComponent(m[1]);
    server.calls.push(`${method} ${addr}${init.body ? " " + init.body : ""}`);
    if (method === "PUT") {
      if (server.putStatus !== 200) return respond(server.putStatus, { detail: "database is locked" });
      const name = JSON.parse(init.body).name;
      server.names.set(addr, name);
      return respond(200, { key_type: "ip", key: addr, name, updated_at: iso(0) });
    }
    if (method === "DELETE") {
      if (server.deleteStatus === 404) return respond(404, { detail: `No name stored for '${addr}'` });
      server.names.delete(addr);
      return respond(204, null);
    }
  }
  if (["/v1/clients", "/v1/jobs", "/v1/games"].includes(u.pathname)) return respond(200, []);
  if (u.pathname === "/v1/cache/summary") return respond(200, {});
  return respond(404, { detail: "Not Found" });
};

const { store } = await import("../js/store-singleton.js");
const { renderClients } = await import("../js/views/clients.js");
const { navigateTo } = await import("../js/router.js");

after(() => {
  store.stop();
  navigateTo("library");
});

const tick = (ms = 10) => new Promise((r) => setTimeout(r, ms));
async function until(pred, label, timeoutMs = 2000) {
  const start = Date.now();
  while (!pred()) {
    if (Date.now() - start > timeoutMs) throw new Error(`timed out waiting for: ${label}`);
    await tick(5);
  }
}
const body = dom.document.body;
const dialog = () => body.querySelector('[data-role="rename-dialog"]');
const backdropOpen = () => dialog().parentNode.classList.contains("on");
const role = (root, r) => root.querySelector(`[data-role="${r}"]`);
const card = (s, key) => s.querySelectorAll(".clcard").find((c) => c.getAttribute("data-client-key") === key) || null;
const renameBtn = (s, key) => {
  const c = card(s, key);
  return c ? role(c, "rename-client") : null;
};
const submit = () => dialog().dispatchEvent({ type: "submit", preventDefault() {} });

async function mount() {
  navigateTo("library");
  const s = renderClients();
  dom.appRoot.replaceChildren(s);
  await until(() => s.querySelectorAll(".clcard").length === 3, "three cards");
  return s;
}

beforeEach(() => {
  server.names = new Map();
  server.calls = [];
  server.putStatus = 200;
  server.deleteStatus = 204;
});

test("MUTATION TARGET: Rename buttons only where the server gives a name_addr, labelled per client", async () => {
  const s = await mount();
  assert.equal(renameBtn(s, "agent:DEMON").getAttribute("aria-label"), "Rename DEMON");
  assert.equal(renameBtn(s, "addr:10.0.0.9").getAttribute("aria-label"), "Rename 10.0.0.9");
  assert.equal(renameBtn(s, "agent:old"), null, "no address: cannot be named");
});

test("MUTATION TARGET: Rename opens a labelled modal dialog with focus in the prefilled field", async () => {
  server.names.set("10.0.0.5", "Living room PC");
  const s = await mount();
  await until(() => card(s, "agent:DEMON").querySelector(".nm").textContent === "Living room PC", "display_name title");
  assert.equal(role(card(s, "agent:DEMON"), "client-kind").textContent, "vault-agent on DEMON · 10.0.0.5");
  renameBtn(s, "agent:DEMON").dispatchEvent({ type: "click" });
  assert.equal(backdropOpen(), true);
  const d = dialog();
  assert.equal(d.getAttribute("role"), "dialog");
  assert.equal(d.getAttribute("aria-modal"), "true");
  assert.equal(d.querySelector("h3").textContent, "Rename Living room PC");
  const input = role(d, "rename-input");
  assert.equal(dom.document.activeElement, input, "focus starts in the field");
  assert.equal(input.value, "Living room PC");
  assert.equal(role(d, "rename-clear").hidden, false, "Remove name offered when named");
  dom.document.dispatchEvent(fakeKeyEvent("Escape"));
  assert.equal(backdropOpen(), false, "Escape closes");
  assert.equal(dom.document.activeElement, renameBtn(s, "agent:DEMON"), "focus back on the card's button");
});

test("MUTATION TARGET: an invalid name is refused in the dialog and nothing is sent", async () => {
  const s = await mount();
  renameBtn(s, "addr:10.0.0.9").dispatchEvent({ type: "click" });
  assert.equal(role(dialog(), "rename-clear").hidden, true, "no Remove name without a name");
  role(dialog(), "rename-input").value = "   ";
  submit();
  await tick(20);
  assert.equal(role(dialog(), "rename-error").hidden, false);
  assert.equal(role(dialog(), "rename-error").textContent, "Enter a name.");
  role(dialog(), "rename-input").value = "two\nlines";
  submit();
  await tick(20);
  assert.equal(role(dialog(), "rename-error").textContent, "Remove line breaks, tabs and invisible characters.");
  assert.deepEqual(server.calls, []);
  assert.equal(backdropOpen(), true);
  role(dialog(), "rename-cancel").dispatchEvent({ type: "click" });
  assert.equal(backdropOpen(), false);
});

test("MUTATION TARGET: Save sends PUT with the trimmed name, closes, and the card shows the name", async () => {
  const s = await mount();
  const before = server.overviews;
  renameBtn(s, "addr:10.0.0.9").dispatchEvent({ type: "click" });
  role(dialog(), "rename-input").value = "  frame  ";
  submit();
  await until(() => !backdropOpen(), "dialog closed");
  assert.deepEqual(server.calls, ['PUT 10.0.0.9 {"name":"frame"}']);
  await until(() => server.overviews > before, "overview refetched");
  await until(() => card(s, "addr:10.0.0.9").querySelector(".nm").textContent === "frame", "renamed card");
  assert.equal(role(card(s, "addr:10.0.0.9"), "client-kind").textContent, "No agent · 10.0.0.9 · seen in the cache log");
});

test("MUTATION TARGET: a failed save keeps the dialog open with the error in a role=alert line", async () => {
  server.putStatus = 500;
  const s = await mount();
  renameBtn(s, "agent:DEMON").dispatchEvent({ type: "click" });
  role(dialog(), "rename-input").value = "Desk";
  submit();
  await until(() => role(dialog(), "rename-error").hidden === false, "error shown");
  assert.equal(role(dialog(), "rename-error").getAttribute("role"), "alert");
  assert.equal(role(dialog(), "rename-error").textContent, "Could not save the name: database is locked");
  assert.equal(backdropOpen(), true);
  role(dialog(), "rename-cancel").dispatchEvent({ type: "click" });
});

test("MUTATION TARGET: Remove name sends DELETE; the handler's own 404 counts as already removed", async () => {
  server.names.set("10.0.0.5", "Desk");
  const s = await mount();
  await until(() => card(s, "agent:DEMON").querySelector(".nm").textContent === "Desk", "named");
  renameBtn(s, "agent:DEMON").dispatchEvent({ type: "click" });
  server.deleteStatus = 404; // another tab removed it meanwhile
  role(dialog(), "rename-clear").dispatchEvent({ type: "click" });
  await until(() => !backdropOpen(), "closed");
  assert.deepEqual(server.calls, ["DELETE 10.0.0.5"]);
  assert.equal(role(dialog(), "rename-error").hidden, true);
});

test("MUTATION TARGET: a rebuild keeps focus on the same card's Rename button", async () => {
  await mount();
  // Re-mounting repaints from the held answer, then the refetch rebuilds.
  const s2 = renderClients();
  dom.appRoot.replaceChildren(s2);
  renameBtn(s2, "addr:10.0.0.9").focus();
  const first = renameBtn(s2, "addr:10.0.0.9");
  const n = server.overviews;
  await until(() => server.overviews > n && renameBtn(s2, "addr:10.0.0.9") !== first, "list rebuilt");
  assert.equal(dom.document.activeElement, renameBtn(s2, "addr:10.0.0.9"), "focus moved to the rebuilt button");
});
