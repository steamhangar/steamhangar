/**
 * DOM-wiring pins for the Clients page (`web/js/views/clients.js`, WP
 * D15c) over `GET /v1/clients/overview`:
 *
 *   - one card per client, agent and agentless; an address client named by
 *     its address, "No agent", no installed line; the agent's installed,
 *     loading, stats, recent and last-active lines;
 *   - feed disabled: the note, no stats lines; stale: the note, a dash for
 *     "loading now";
 *   - the range selector refetches with the new range and an answer for the
 *     old range is dropped (gated fetch);
 *   - a 404 (server older than D15b) shows the "update vault-api" note;
 *   - no statistics (feed disabled, 404): the range selector is hidden;
 *   - a failed refresh after an answer keeps the cards and says so;
 *   - the poll stops when the view is left;
 *   - the nav has a Clients button right after Library (index.html).
 *
 * Harness: fake-dom.js, Map-backed localStorage with a stored key, a
 * routing `fetch` fake; the real store-singleton is stopped in `after()`.
 * Assertions compare text, never nodes (LEARNINGS WEB-FIX-1).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, after, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
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

const GIB = 1024 ** 3;
const MIB = 1024 ** 2;
const iso = (msAgo) => new Date(Date.now() - msAgo).toISOString().replace(/\.\d{3}Z$/, "Z");

function feed(state) {
  return { enabled: state !== "disabled", state, last_sweep_at: state === "disabled" ? null : iso(40 * 60_000), sweep_interval_minutes: 5, traffic_since: null };
}
const AGENT = {
  key: "agent:gaming-pc", kind: "agent", client_id: "gaming-pc", addresses: ["192.168.1.42"], connected: true,
  agent_presence: "online", cache_active: true, last_active: iso(2 * 60_000), last_seen_in_cache_log: iso(2 * 60_000),
  last_reported_at: iso(5 * 60_000), installed_appids: [440, 570], installed_app_count: 2,
  stats: { bytes_served: GIB, hit_bytes: 0.75 * GIB, miss_bytes: 0.25 * GIB, requests: 4, hits: 3, misses: 1, hit_rate: 0.75 },
  loading_now: { loading: true, bytes_per_second: 30 * MIB, apps: [{ appid: 440, name: "Some Game", bytes_per_second: 30 * MIB }], unattributed_bytes_per_second: 0 },
  recent_apps: [{ appid: 440, name: "Some Game", bytes_served: GIB, last_seen: iso(2 * 60_000) }],
  recent_unattributed_bytes: 0,
};
const ADDR = {
  ...AGENT, key: "addr:192.168.1.77", kind: "address", client_id: null, addresses: ["192.168.1.77"], connected: false,
  agent_presence: null, cache_active: false, last_reported_at: null, installed_appids: null, installed_app_count: null,
  loading_now: { loading: false, bytes_per_second: 0, apps: [], unattributed_bytes_per_second: 0 }, recent_apps: [],
};
const RUNNER = {
  ...ADDR, key: "runner", kind: "runner", addresses: ["172.18.0.1"], connected: true, cache_active: true,
  name: null, name_addr: null, display_name: "Prefill runner",
};
function overview(range, state = "ok") {
  if (state === "empty") return { event_feed: feed("ok"), range, tier: "1h", range_start: null, active_window_minutes: 15, clients: [] };
  if (state === "runner") {
    return { event_feed: feed("ok"), range, tier: "1h", range_start: null, active_window_minutes: 15, clients: [AGENT, RUNNER] };
  }
  const strip = (c) =>
    state === "disabled"
      ? { ...c, stats: null, loading_now: null, recent_apps: null, recent_unattributed_bytes: null, cache_active: null }
      : state === "stale"
        ? { ...c, loading_now: null, cache_active: null }
        : c;
  const clients = state === "disabled" ? [AGENT].map(strip) : [AGENT, ADDR].map(strip);
  return { event_feed: feed(state), range, tier: "1h", range_start: null, active_window_minutes: 15, clients };
}

const server = { state: "ok", status: 200, calls: [], gate: null, serverVersion: null, agentVersion: undefined };
function respond(status, data) {
  const text = JSON.stringify(data);
  return { ok: status >= 200 && status < 300, status, json: async () => data, text: async () => text };
}
globalThis.fetch = async (url) => {
  const u = new URL(String(url));
  if (u.pathname === "/v1/clients/overview") {
    const range = u.searchParams.get("range");
    server.calls.push(range);
    if (server.gate) await server.gate;
    if (server.status !== 200) return respond(server.status, { detail: server.status === 404 ? "Not Found" : "database is locked" });
    return respond(200, overview(range, server.state));
  }
  if (u.pathname === "/v1/settings" && server.serverVersion) return respond(200, { readonly: false, settings: [], server_version: server.serverVersion });
  if (u.pathname === "/v1/clients" && server.agentVersion !== undefined) return respond(200, [{ client_id: "gaming-pc", agent_version: server.agentVersion }]);
  if (["/v1/clients", "/v1/jobs", "/v1/games"].includes(u.pathname)) return respond(200, []);
  if (u.pathname === "/v1/cache/summary") return respond(200, {});
  return respond(404, { detail: "Not Found" });
};

const { store } = await import("../js/store-singleton.js");
const { renderClients } = await import("../js/views/clients.js");
const { navigateTo } = await import("../js/router.js");

let current = null;
after(() => {
  store.stop();
  navigateTo("library"); // stops the view poll
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
const lines = (c) => c.querySelectorAll(".cl-line").map((l) => `${l.getAttribute("data-role")}=${l.textContent}`);

async function mount() {
  navigateTo("library"); // leave first: stops any earlier poll
  current = renderClients();
  dom.appRoot.replaceChildren(current);
  return current;
}

beforeEach(() => {
  server.state = "ok";
  server.status = 200;
  server.calls = [];
  server.gate = null;
  server.serverVersion = null;
  server.agentVersion = undefined;
});

test("MUTATION TARGET: one card per client; the agent's lines, and the address named by its IP with no agent and no installed line", async () => {
  const s = await mount();
  await until(() => s.querySelectorAll(".clcard").length === 2, "two cards");
  const a = card(s, "agent:gaming-pc");
  assert.equal(text(a, ".nm"), "gaming-pc");
  assert.equal(text(a, '[data-role="connection"]'), "Agent online");
  assert.deepEqual(lines(a), [
    "client-kind=vault-agent · 192.168.1.42",
    "loading-now=Loading now: 30.0 MB/s · Some Game",
    "installed=2 games installed",
    "stats=1.0 GB loaded in the last 7 days · hit rate 75%",
    "recent=Loaded recently: Some Game (1.0 GB)",
    "last-active=Last active 2 min ago",
  ]);
  const b = card(s, "addr:192.168.1.77");
  assert.equal(text(b, ".nm"), "192.168.1.77");
  assert.equal(text(b, '[data-role="connection"]'), "No agent");
  assert.equal(text(b, '[data-role="client-kind"]'), "No agent · seen in the cache log");
  assert.equal(b.querySelector('[data-role="installed"]'), null, "no installed line for an address");
  assert.equal(text(s, '[data-role="clients-sub"]'), "2 clients · 1 connected");
  assert.equal(s.querySelector('[data-role="clients-range"]').hidden, false, "range selector shown with figures");
  assert.equal(s.querySelector('[data-role="clients-note"]').hidden, true, "no note when the feed is ok");
  assert.equal(s.querySelector('[data-role="agentless-note"]').hidden, false);
});

test("MUTATION TARGET: D15-RUNNER — the runner card is its own kind, titled by the server, and does not raise the agentless note", async () => {
  server.state = "runner";
  const s = await mount();
  await until(() => card(s, "runner") !== null, "runner card");
  const r = card(s, "runner");
  assert.equal(r.getAttribute("data-kind"), "runner");
  assert.equal(text(r, ".nm"), "Prefill runner");
  assert.equal(text(r, '[data-role="client-kind"]'), "This server's prefill downloads · 172.18.0.1");
  assert.equal(r.querySelector('[data-role="rename-client"]'), null, "no rename: name_addr is null");
  assert.equal(r.querySelector('[data-role="installed"]'), null);
  assert.equal(s.querySelector('[data-role="agentless-note"]').hidden, true, "only an agent and the runner: no agentless note");
});

test("MUTATION TARGET: feed disabled — the note, and no stats/recent lines (never zeros)", async () => {
  server.state = "disabled";
  const s = await mount();
  await until(() => s.querySelectorAll(".clcard").length === 1, "agent card");
  const note = s.querySelector('[data-role="clients-note"]');
  assert.equal(note.hidden, false);
  assert.match(note.textContent, /^Traffic statistics are off/);
  const a = card(s, "agent:gaming-pc");
  assert.equal(a.querySelector('[data-role="stats"]'), null);
  assert.equal(a.querySelector('[data-role="recent"]'), null);
  assert.equal(text(a, '[data-role="loading-now"]'), "Loading now: —");
  assert.equal(text(a, '[data-role="installed"]'), "2 games installed", "agent presence and games still shown");
  assert.equal(s.querySelector('[data-role="clients-range"]').hidden, true, "no figures, no range selector");
  // A re-mount while the next answer is pending keeps the last verdict
  // (no flash of the selector), and an ok answer brings it back.
  server.state = "ok";
  let release;
  server.gate = new Promise((r) => (release = r));
  const s2 = await mount();
  await until(() => server.calls.length >= 2, "second fetch held");
  assert.equal(s2.querySelector('[data-role="clients-range"]').hidden, true, "still hidden while pending");
  release();
  await until(() => s2.querySelector('[data-role="clients-range"]').hidden === false, "shown once figures exist");
});

test("feed stale — the note says why, loading now is a dash, totals stay", async () => {
  server.state = "stale";
  const s = await mount();
  // The last answer (module state) paints first; wait for the stale one.
  await until(() => /out of date/.test(text(s, '[data-role="clients-note"]') || ""), "stale answer");
  await until(() => s.querySelectorAll(".clcard").length === 2, "cards");
  assert.match(text(s, '[data-role="clients-note"]'), /out of date/);
  assert.equal(text(card(s, "agent:gaming-pc"), '[data-role="loading-now"]'), "Loading now: —");
  assert.match(text(card(s, "agent:gaming-pc"), '[data-role="stats"]'), /^1\.0 GB loaded/);
});

test("MUTATION TARGET: the range selector refetches; an answer for the old range is dropped", async () => {
  const s = await mount();
  await until(() => s.querySelectorAll(".clcard").length === 2, "cards");
  const seg = (r) => s.querySelector('[data-role="clients-range"]').querySelectorAll("button").find((b) => b.getAttribute("data-range") === r);
  assert.equal(seg("7d").getAttribute("aria-pressed"), "true", "7 days is the default");
  let release;
  server.gate = new Promise((r) => (release = r));
  server.calls = [];
  const stats = () => text(card(s, "agent:gaming-pc") || s, '[data-role="stats"]') || "";
  seg("30d").dispatchEvent({ type: "click" }); // its fetch is held in flight
  await until(() => server.calls.length === 1, "held 30d fetch");
  assert.equal(text(s, ".empty"), "Loading clients…", "the old range's cards are gone while loading");
  seg("24h").dispatchEvent({ type: "click" }); // while 30d is still in flight
  assert.equal(seg("24h").getAttribute("aria-pressed"), "true");
  assert.equal(seg("30d").getAttribute("aria-pressed"), "false");
  let release24;
  server.gate = new Promise((r) => (release24 = r)); // holds the 24h fetch
  release(); // the 30d answer lands now, after the user left 30d
  await until(() => server.calls.length === 2, "24h fetch issued after the 30d answer");
  await tick(20);
  assert.doesNotMatch(stats(), /30 days/, "the late 30d answer must not paint");
  assert.equal(text(s, ".empty"), "Loading clients…");
  server.gate = null;
  release24();
  await until(() => /in the last 24 hours/.test(stats()), "24h painted");
  assert.deepEqual(server.calls, ["30d", "24h"]);
  seg("7d").dispatchEvent({ type: "click" });
  await until(() => /in the last 7 days/.test(stats()), "back to 7d");
});

test("MUTATION TARGET: summary tiles and the header Add a device button render (CLIENTS-2)", async () => {
  const s = await mount();
  await until(() => s.querySelectorAll(".clcard").length === 2, "cards");
  const tiles = s.querySelector('[data-role="clients-tiles"]');
  assert.equal(tiles.hidden, false);
  assert.ok(tiles.querySelectorAll(".cl-tile").length >= 1, "at least the Connected tile");
  assert.equal(tiles.querySelector('[data-tile="connected"] .k').textContent, "Connected");
  const unknownTile = s.querySelector('[data-tile="outdated"]');
  assert.equal(unknownTile.querySelector(".v").textContent, "—", "server version unknown (settings 404): no count");
  assert.equal(unknownTile.className, "cl-tile", "no warn state without a count");
  assert.equal(s.querySelector(".cl-head").querySelector('[data-role="add-device"]').textContent, "Add a device");
});

test("MUTATION TARGET: an agent whose version differs from the server's shows the warning, the tile and the Update command button (CLIENTS-3)", async () => {
  server.serverVersion = "0.1.0-rc19.2";
  server.agentVersion = "0.1.0-rc18.1";
  const s = await mount();
  store.refreshNow();
  await until(() => card(s, "agent:gaming-pc") && card(s, "agent:gaming-pc").querySelector('[data-role="agent-outdated"]'), "outdated block");
  const a = card(s, "agent:gaming-pc");
  assert.match(text(a, '[data-role="agent-outdated"]'), /^Agent outdated.*0\.1\.0-rc18\.1.*0\.1\.0-rc19\.2/);
  const btn = a.querySelector('[data-role="update-command"]');
  assert.equal(btn.textContent, "Update command");
  assert.equal(btn.getAttribute("aria-label"), "Update command for gaming-pc");
  assert.equal(card(s, "addr:192.168.1.77").querySelector('[data-role="agent-outdated"]'), null, "an address never warns");
  const tile = s.querySelector('[data-tile="outdated"]');
  assert.equal(tile.querySelector(".v").textContent, "1");
  assert.match(tile.className, /\bwarn\b/);
  const src = readFileSync(new URL("../js/views/clients.js", import.meta.url), "utf8");
  assert.match(src, /openAddDeviceSheet\(\{ tab: null \}\)/, "opens the dialog without a preselected tab");
});

test("an agent with the server's version shows no warning and a calm 0 tile (CLIENTS-3)", async () => {
  server.serverVersion = "0.1.0-rc19.2";
  server.agentVersion = "0.1.0-rc19.2";
  const s = await mount();
  store.refreshNow();
  await until(() => s.querySelector('[data-tile="outdated"] .v') && s.querySelector('[data-tile="outdated"] .v').textContent === "0", "zero tile");
  await until(() => store.snapshot("clients").some((a) => a.agent_version === "0.1.0-rc19.2"), "agent row in the store");
  await tick(30);
  assert.equal(s.querySelector('[data-role="agent-outdated"]'), null);
  assert.doesNotMatch(s.querySelector('[data-tile="outdated"]').className, /warn/);
  // Later tests expect an empty agent list: let the store drop the row.
  server.agentVersion = undefined;
  store.refreshNow();
  await until(() => store.snapshot("clients").length === 0, "agent row gone from the store");
});

test("MUTATION TARGET: a server without the endpoint (404) gets the update note, no cards", async () => {
  server.status = 404;
  const s = await mount();
  await until(() => s.querySelector('[data-role="clients-note"]').hidden === false, "note");
  assert.equal(text(s, '[data-role="clients-note"]'), "This server does not report traffic statistics yet. Update vault-api to see them.");
  assert.equal(s.querySelectorAll(".clcard").length, 0);
  assert.equal(s.querySelector('[data-role="clients-range"]').hidden, true, "no range selector for an old server");
  assert.equal(s.querySelector('[data-role="open-pcs"]'), null, "no Manage PCs (WP CLIENTS-1)");
  assert.equal(s.querySelector('[data-role="add-device"]') === null, false, "Add a device still there");
});

test("MUTATION TARGET: a failed refresh after an answer keeps the cards and adds a 'Couldn't refresh' note", async () => {
  let s = await mount();
  await until(() => s.querySelectorAll(".clcard").length === 2, "cards");
  server.status = 500;
  s = await mount(); // a re-mount refreshes at once; the old answer is still held
  await until(() => s.querySelector('[data-role="clients-note"]').hidden === false, "note");
  assert.match(text(s, '[data-role="clients-note"]'), /^Couldn't refresh: /);
  assert.equal(s.querySelectorAll(".clcard").length, 2, "the last answer stays on screen");
  assert.equal(s.querySelector('[data-role="clients-range"]').hidden, false);
  server.status = 200;
  s = await mount();
  await until(() => s.querySelector('[data-role="clients-note"]').hidden === true, "note gone after a good answer");
});

test("MUTATION TARGET: leaving the page stops its poll", async () => {
  const s = await mount();
  await until(() => s.querySelectorAll(".clcard").length === 2, "cards");
  navigateTo("library");
  const before = server.calls.length;
  store.refreshNow();
  await tick(60);
  assert.equal(server.calls.length, before, "no overview request after leaving");
});

test("index.html: a Clients nav button right after Library", () => {
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  const views = [...html.matchAll(/class="nav-btn" data-view="([a-z]+)"/g)].map((m) => m[1]);
  assert.deepEqual(views, ["library", "clients", "downloads", "settings"]);
  assert.match(html, /<span class="nav-lb">Clients<\/span>/);
});

test("empty instance: header, Add a device and an empty-state entry still render", async () => {
  server.state = "empty";
  const s = await mount();
  await until(() => s.querySelector('[data-role="clients-empty"]'), "empty state");
  assert.equal(text(s, "h1"), "Clients");
  assert.equal(text(s, '[data-role="add-device"]'), "Add a device");
  assert.equal(text(s, '[data-role="add-device-empty"]'), "Add a device");
  assert.match(text(s, '[data-role="clients-empty"] .hint'), /^No clients to show\./);
});

test("device card carries the rc20 structure: icon, name, now box", async () => {
  const s = await mount();
  await until(() => s.querySelectorAll(".clcard").length === 2, "cards");
  const c = card(s, "agent:gaming-pc");
  assert.ok(c.querySelector(".jobtop .cl-ico"), "icon in the header");
  assert.ok(c.querySelector('[data-role="loading-now"].cl-now.busy'), "loading box is highlighted while loading");
  assert.ok(!card(s, "addr:192.168.1.77").querySelector(".cl-now.busy"));
});
