/**
 * DOM-wiring pins for WP CLIENTS-1 (one card per device) on the Clients page
 * (`web/js/views/clients.js`): the agent data of `GET /v1/clients` is merged
 * into the overview card.
 *
 *   - a PC that is client and agent shows ONCE, with its presence line;
 *   - an agent the overview does not list gets its own card (and still shows
 *     with an old server that has no overview endpoint);
 *   - a bypass-suspected agent says so on its card;
 *   - Remove on a card opens the shared confirm, sends one DELETE, drops the
 *     card; a failure shows inline on the card; an id with "/" gets a note;
 *   - there is no "PCs running vault-agent" list and no "Manage PCs".
 *
 * Harness: fake-dom.js and a routing `fetch` fake, as clients-view-wiring.
 * Rendering is not measured in a browser here.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, after, beforeEach } from "node:test";
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
  location: { origin: "http://vault.test", pathname: "/clients", reload() {} },
  history: { pushState() {} },
  addEventListener() {},
  removeEventListener() {},
};

const iso = (msAgo) => new Date(Date.now() - msAgo).toISOString().replace(/\.\d{3}Z$/, "Z");
const feed = { enabled: true, state: "ok", last_sweep_at: iso(60_000), sweep_interval_minutes: 5, traffic_since: null };

const ovAgent = (id, extra = {}) => ({
  key: `agent:${id}`, kind: "agent", client_id: id, addresses: ["192.168.1.42"], connected: true, agent_presence: "online",
  cache_active: true, last_active: iso(2 * 60_000), last_seen_in_cache_log: iso(2 * 60_000), last_reported_at: iso(5 * 60_000),
  installed_appids: [1, 2], installed_app_count: 2,
  stats: { bytes_served: 1024 ** 3, hit_bytes: 0, miss_bytes: 0, requests: 4, hits: 3, misses: 1, hit_rate: 0.75 },
  loading_now: { loading: false, bytes_per_second: 0, apps: [], unattributed_bytes_per_second: 0 },
  recent_apps: [], recent_unattributed_bytes: 0, name: null, name_addr: "192.168.1.42", display_name: id, ...extra,
});
const ovAddr = {
  ...ovAgent("x"), key: "addr:192.168.1.77", kind: "address", client_id: null, addresses: ["192.168.1.77"], connected: false,
  agent_presence: null, last_reported_at: null, installed_appids: null, installed_app_count: null, name_addr: "192.168.1.77",
  display_name: "192.168.1.77",
};
const agentRow = (id, extra = {}) => ({
  client_id: id, first_seen: iso(86_400_000), last_reported_at: iso(5 * 60_000), app_count: 2, source_addrs: ["192.168.1.42"],
  peer_addrs: [], reported_addrs: [], cache_hits: 3, cache_misses: 1, bytes_served: 1, last_seen_in_cache_log: iso(60_000),
  bypass_suspected: false, agent_version: "0.1.0", report_interval_seconds: 1800, presence: "online", offline_after: iso(-60_000),
  name: null, name_addr: "192.168.1.42", display_name: id, ...extra,
});

const server = { overview: [], agents: [], overviewStatus: 200, deletes: [], deleteStatus: 204, agentGate: null, staleAgents: [], overviewCalls: 0 };
function respond(status, data) {
  const text = data === undefined ? "" : JSON.stringify(data);
  return { ok: status >= 200 && status < 300, status, json: async () => data, text: async () => text };
}
globalThis.fetch = async (url, init = {}) => {
  const p = new URL(String(url)).pathname;
  if ((init.method || "GET") === "DELETE" && p.startsWith("/v1/clients/")) {
    server.deletes.push(decodeURIComponent(p.slice("/v1/clients/".length)));
    if (server.deleteStatus !== 204) return respond(server.deleteStatus, { detail: "database is locked" });
    const id = server.deletes[server.deletes.length - 1];
    server.agents = server.agents.filter((a) => a.client_id !== id);
    server.overview = server.overview.filter((c) => c.client_id !== id);
    return respond(204);
  }
  if (p === "/v1/clients/overview") {
    server.overviewCalls += 1;
    if (server.overviewStatus !== 200) return respond(server.overviewStatus, { detail: "Not Found" });
    return respond(200, { event_feed: feed, range: "7d", tier: "1h", range_start: null, active_window_minutes: 15, clients: server.overview });
  }
  if (p === "/v1/clients") {
    if (server.agentGate) {
      await server.agentGate;
      return respond(200, server.staleAgents);
    }
    return respond(200, server.agents);
  }
  if (p === "/v1/jobs" || p === "/v1/games") return respond(200, []);
  if (p === "/v1/cache/summary") return respond(200, {});
  return respond(404, { detail: "Not Found" });
};

const { store } = await import("../js/store-singleton.js");
const { renderClients } = await import("../js/views/clients.js");
const { navigateTo } = await import("../js/router.js");
const { resetModalStack } = await import("../js/lib/modal-stack.js");

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
const text = (root, sel) => {
  const n = root.querySelector(sel);
  return n ? n.textContent : null;
};
const card = (s, key) => s.querySelectorAll(".clcard").find((c) => c.getAttribute("data-client-key") === key) || null;
const keys = (s) => s.querySelectorAll(".clcard").map((c) => c.getAttribute("data-client-key"));
const click = (el) => el.dispatchEvent({ type: "click" });
const confirmEl = () => dom.document.body.querySelectorAll("div").find((d) => d.getAttribute("data-role") === "remove-confirm") || null;
const confirmIsOpen = () => !!(confirmEl() && confirmEl().parentNode.classList.contains("on"));

async function mount(waitForAgents = true) {
  navigateTo("library");
  if (waitForAgents) {
    store.refreshNow();
    await until(() => (store.snapshot("clients") || []).length === server.agents.length, "agents in the store");
  }
  const s = renderClients();
  dom.appRoot.replaceChildren(s);
  return s;
}

beforeEach(() => {
  if (confirmIsOpen()) click(confirmEl().querySelector('[data-role="remove-cancel"]'));
  resetModalStack(dom.document);
  server.overview = [ovAgent("desk-pc"), ovAddr];
  server.agents = [agentRow("desk-pc")];
  server.overviewStatus = 200;
  server.deletes = [];
  server.deleteStatus = 204;
  server.agentGate = null;
});

test("MUTATION TARGET: a PC that is client and agent shows once, with the agent's presence line", async () => {
  const s = await mount();
  await until(() => keys(s).length === 2, "two cards");
  assert.deepEqual(keys(s), ["agent:desk-pc", "addr:192.168.1.77"]);
  const a = card(s, "agent:desk-pc");
  assert.match(text(a, '[data-role="agent-presence"]'), /^last seen 5 min ago · agent 0\.1\.0$/);
  assert.equal(text(a, '[data-role="connection"]'), "Agent online", "existing chip wording kept");
  assert.equal(card(s, "addr:192.168.1.77").querySelector('[data-role="agent-presence"]'), null, "an address has no agent line");
  assert.equal(text(s, '[data-role="clients-sub"]'), "2 clients · 1 connected");
});

test("MUTATION TARGET: no 'PCs running vault-agent' list and no Manage PCs on the page", async () => {
  const s = await mount();
  await until(() => keys(s).length === 2, "cards");
  assert.equal(s.querySelector('[data-role="open-pcs"]'), null);
  assert.equal(s.querySelector('[data-role="agents-summary"]'), null);
  assert.equal(/Manage PCs|PCs running vault-agent/.test(s.textContent), false);
});

test("MUTATION TARGET: an agent the overview does not list gets its own card from the agent row", async () => {
  server.agents = [agentRow("desk-pc"), agentRow("laptop", { presence: "offline", agent_version: null, source_addrs: [] })];
  const s = await mount();
  await until(() => keys(s).length === 3, "three cards");
  const l = card(s, "agent:laptop");
  assert.equal(text(l, '[data-role="connection"]'), "Agent offline");
  assert.match(text(l, '[data-role="agent-presence"]'), /^last seen 5 min ago · version not reported$/);
  assert.equal(text(l, '[data-role="loading-now"]'), "Loading now: \u2014", "no traffic data, a dash and no zero");
  assert.equal(l.querySelector('[data-role="stats"]'), null);
});

test("with an old server (no overview) the agent PCs are still listed", async () => {
  server.overviewStatus = 404;
  const s = await mount();
  await until(() => s.querySelector('[data-role="clients-note"]').hidden === false, "update note");
  assert.deepEqual(keys(s), ["agent:desk-pc"]);
  assert.equal(card(s, "agent:desk-pc").querySelector('[data-role="remove-pc"]') === null, false);
});

test("MUTATION TARGET: a bypass-suspected agent says so on its card, with the careful explanation", async () => {
  server.agents = [agentRow("desk-pc", { bypass_suspected: true })];
  const s = await mount();
  await until(() => keys(s).length === 2, "cards");
  const a = card(s, "agent:desk-pc");
  assert.match(text(a, '[data-role="bypass"]'), /^Bypassing the cache · 2 games reported · none of its downloads have reached the cache recently$/);
  assert.match(a.querySelector(".hint").textContent, /does not necessarily mean anything is wrong/);
  assert.equal(card(s, "addr:192.168.1.77").querySelector('[data-role="bypass"]'), null);
});

test("MUTATION TARGET: Remove on a card opens the shared confirm, sends one DELETE and drops the card", async () => {
  const s = await mount();
  await until(() => keys(s).length === 2, "cards");
  const btn = card(s, "agent:desk-pc").querySelector('[data-role="remove-pc"]');
  assert.equal(btn.textContent, "Remove");
  assert.equal(btn.getAttribute("aria-label"), "Remove desk-pc");
  assert.equal(card(s, "addr:192.168.1.77").querySelector('[data-role="remove-pc"]'), null, "address cards have no Remove");
  click(btn);
  assert.equal(confirmIsOpen(), true);
  assert.equal(text(confirmEl(), "h3"), "Remove desk-pc?");
  click(confirmEl().querySelector('[data-role="remove-confirm-yes"]'));
  await until(() => !confirmIsOpen() && keys(s).length === 1, "card dropped after the refresh");
  assert.deepEqual(server.deletes, ["desk-pc"]);
  assert.deepEqual(keys(s), ["addr:192.168.1.77"]);
});

test("MUTATION TARGET: a failed Remove keeps the card and shows the error on it", async () => {
  server.deleteStatus = 500;
  const s = await mount();
  await until(() => keys(s).length === 2, "cards");
  click(card(s, "agent:desk-pc").querySelector('[data-role="remove-pc"]'));
  click(confirmEl().querySelector('[data-role="remove-confirm-yes"]'));
  await until(() => {
    const e = card(s, "agent:desk-pc").querySelector('[data-role="remove-error"]');
    return e && e.hidden === false;
  }, "inline error");
  assert.match(text(card(s, "agent:desk-pc"), '[data-role="remove-error"]'), /^Could not remove desk-pc: /);
  assert.equal(keys(s).length, 2);
});

test("an agent id containing '/' gets the note instead of a Remove button", async () => {
  server.overview = [ovAgent("lab/pc-1", { addresses: [] })];
  server.agents = [agentRow("lab/pc-1")];
  const s = await mount();
  await until(() => keys(s).length === 1, "card");
  const c = card(s, "agent:lab/pc-1");
  assert.equal(c.querySelector('[data-role="remove-pc"]'), null);
  assert.match(text(c, '[data-role="remove-unavailable"]'), /contains "\/"/);
});

test("MUTATION TARGET: after Remove, an overview answer that lands before the agent snapshot does not bring the card back", async () => {
  // Own id: the page hides a removed id for a while (module state).
  server.overview = [ovAgent("gone-pc"), ovAddr];
  server.agents = [agentRow("gone-pc")];
  const s = await mount();
  await until(() => keys(s).length === 2, "cards");
  let release;
  const stale = server.agents;
  server.staleAgents = stale; // the held GET /v1/clients still answers with the pre-delete list
  server.agentGate = new Promise((r) => (release = r));
  click(card(s, "agent:gone-pc").querySelector('[data-role="remove-pc"]'));
  const before = server.overviewCalls;
  click(confirmEl().querySelector('[data-role="remove-confirm-yes"]'));
  await until(() => server.overviewCalls > before, "overview refetched");
  await tick(60); // the overview answer (without gone-pc) is painted; the agent GET is still held
  assert.deepEqual(keys(s), ["addr:192.168.1.77"], "no agent-only card from the stale snapshot");
  assert.equal(stale.length, 1, "precondition: the held answer still lists gone-pc");
  release();
  await tick(60);
  assert.deepEqual(keys(s), ["addr:192.168.1.77"]);
});

test("a Remove error is cleared when the confirm opens again", async () => {
  server.deleteStatus = 500;
  const s = await mount();
  await until(() => keys(s).length === 2, "cards");
  click(card(s, "agent:desk-pc").querySelector('[data-role="remove-pc"]'));
  click(confirmEl().querySelector('[data-role="remove-confirm-yes"]'));
  await until(() => {
    const e = card(s, "agent:desk-pc").querySelector('[data-role="remove-error"]');
    return e && e.hidden === false;
  }, "inline error");
  click(card(s, "agent:desk-pc").querySelector('[data-role="remove-pc"]'));
  assert.equal(confirmIsOpen(), true);
  assert.equal(card(s, "agent:desk-pc").querySelector('[data-role="remove-error"]').hidden, true, "error gone once the confirm opens");
});
