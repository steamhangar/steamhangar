/**
 * WP WEB-FIX-20.2: the Clients page is as fresh after a client-side
 * navigation as after a page reload. Operator report (rc20.1): a PC that
 * was loading a game only appeared after F5, not when clicking "Clients"
 * in the nav.
 *
 *   - every mount fetches the overview AND the store's `GET /v1/clients`
 *     at once (the store's own loop runs every 20 s and would otherwise
 *     paint its last snapshot);
 *   - a loading client the server reports at the remount is on screen
 *     without a reload;
 *   - while the page is on screen both refresh every CLIENTS_POLL_MS (5 s);
 *     leaving the page stops the poll (no load from an unwatched view).
 *
 * Harness: fake-dom.js, Map-backed localStorage with a stored key, a
 * routing `fetch` fake counting calls per path; the real store-singleton
 * is stopped in `after()`. Text and counts only (LEARNINGS WEB-FIX-1).
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
  location: { origin: "http://vault.test", pathname: "/clients", reload() {} },
  history: { pushState() {} },
  addEventListener() {},
  removeEventListener() {},
};

const MIB = 1024 ** 2;
const iso = (msAgo) => new Date(Date.now() - msAgo).toISOString().replace(/\.\d{3}Z$/, "Z");
const feed = { enabled: true, state: "ok", last_sweep_at: iso(60_000), sweep_interval_minutes: 5, traffic_since: null };

function pc(loading) {
  return {
    key: "addr:192.168.1.50", kind: "address", client_id: null, addresses: ["192.168.1.50"], connected: true,
    name: null, name_addr: "192.168.1.50", display_name: "192.168.1.50",
    agent_presence: null, cache_active: loading, last_active: iso(60_000), last_seen_in_cache_log: iso(60_000),
    last_reported_at: null, installed_appids: null, installed_app_count: null,
    stats: { bytes_served: MIB, hit_bytes: MIB, miss_bytes: 0, requests: 1, hits: 1, misses: 0, hit_rate: 1 },
    loading_now: loading
      ? { loading: true, bytes_per_second: 1.2 * MIB, apps: [{ appid: 526870, name: "Satisfactory", bytes_per_second: 1.2 * MIB }], unattributed_bytes_per_second: 0 }
      : { loading: false, bytes_per_second: 0, apps: [], unattributed_bytes_per_second: 0 },
    recent_apps: [], recent_unattributed_bytes: 0,
  };
}

const server = { loading: false, calls: new Map() };
const count = (p) => server.calls.get(p) || 0;
function respond(status, data) {
  const text = JSON.stringify(data);
  return { ok: status >= 200 && status < 300, status, json: async () => data, text: async () => text };
}
globalThis.fetch = async (url) => {
  const u = new URL(String(url));
  const p = u.pathname;
  server.calls.set(p, count(p) + 1);
  if (p === "/v1/clients/overview") {
    return respond(200, { event_feed: feed, range: u.searchParams.get("range"), tier: "1h", range_start: null, active_window_minutes: 15, clients: [pc(server.loading)] });
  }
  if (["/v1/clients", "/v1/jobs", "/v1/games"].includes(p)) return respond(200, []);
  if (p === "/v1/cache/summary") return respond(200, {});
  if (p === "/v1/settings") return respond(200, { readonly: false, settings: [] });
  return respond(404, { detail: "Not Found" });
};

const { store } = await import("../js/store-singleton.js");
const { renderClients, CLIENTS_POLL_MS } = await import("../js/views/clients.js");
const { navigateTo } = await import("../js/router.js");

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
const nowBox = (s) => {
  const n = s.querySelector('[data-role="loading-now"]');
  return n ? n.textContent : null;
};

test("MUTATION PIN: a remount fetches the overview and /v1/clients at once and shows a client that started loading meanwhile", async () => {
  const s1 = renderClients();
  await until(() => nowBox(s1) !== null, "first visit painted");
  assert.doesNotMatch(nowBox(s1), /Satisfactory/);
  await until(() => count("/v1/clients") >= 1, "the store's first agent poll");
  await tick(30);
  navigateTo("library");
  const overviewBefore = count("/v1/clients/overview");
  const agentsBefore = count("/v1/clients");

  server.loading = true; // the PC starts loading while the user is elsewhere
  navigateTo("clients");
  const s2 = renderClients();
  await until(() => count("/v1/clients/overview") > overviewBefore, "overview fetched on the remount");
  await until(() => count("/v1/clients") > agentsBefore, "agent snapshot fetched on the remount, not on the store's 20 s tick", 1000);
  await until(() => /Satisfactory/.test(nowBox(s2) || ""), "the loading client, without a reload");
  assert.match(nowBox(s2), /1\.2 MB\/s/);
});

test("MUTATION PIN: the page refreshes every 5 s while shown, and not at all once left", async () => {
  assert.equal(CLIENTS_POLL_MS, 5000);
  navigateTo("library");
  await tick(20);
  const before = count("/v1/clients/overview");
  navigateTo("clients");
  renderClients();
  await until(() => count("/v1/clients/overview") === before + 1, "mount fetch");
  // The next one comes on the 5 s cadence (not the old 30 s).
  await until(() => count("/v1/clients/overview") === before + 2, "the 5 s refresh", CLIENTS_POLL_MS + 1500);
  navigateTo("library");
  const left = count("/v1/clients/overview");
  await tick(300);
  assert.equal(count("/v1/clients/overview"), left, "no poll once the view is left");
});
