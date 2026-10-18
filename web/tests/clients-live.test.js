/**
 * WP CLIENTS-LIVE-1: the Clients page's "loading now" reads the server's
 * live tier (`live_now`, seconds old) instead of the swept window
 * (`loading_now`, up to one event-log sweep old).
 *
 * Pins: `live_now` wins over `loading_now` in the now box, its speed, the
 * "Downloading" pill and the "N loading now" tile, in BOTH directions (a
 * live idle beats a stale swept "loading"); a server answer with the feed
 * ok but `live_now` null or missing (a server before rc21) falls back to
 * `loading_now`; the rendered view shows the live state.
 *
 * Harness: pure functions, then fake-dom.js with a routing `fetch` fake
 * (as clients-nav-refresh.test.js). Text only (LEARNINGS WEB-FIX-1).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, after } from "node:test";
import assert from "node:assert/strict";
import { createFakeDom } from "./fake-dom.js";
import {
  connectionPill,
  currentLoading,
  loadingNowText,
  nowBox,
  summaryTiles,
} from "../js/lib/clients-page.js";

const MIB = 1024 ** 2;
const iso = (msAgo) => new Date(Date.now() - msAgo).toISOString().replace(/\.\d{3}Z$/, "Z");
const IDLE = { loading: false, bytes_per_second: 0, apps: [], unattributed_bytes_per_second: 0 };
const loadingOf = (name, mib) => ({
  loading: true,
  bytes_per_second: mib * MIB,
  apps: [{ appid: 526870, name, bytes_per_second: mib * MIB }],
  unattributed_bytes_per_second: 0,
});

function pc(extra = {}) {
  return {
    key: "addr:192.168.1.50", kind: "address", client_id: null, addresses: ["192.168.1.50"], connected: true,
    name: null, name_addr: "192.168.1.50", display_name: "192.168.1.50",
    agent_presence: null, cache_active: true, last_active: iso(60_000), last_seen_in_cache_log: iso(60_000),
    last_reported_at: null, installed_appids: null, installed_app_count: null,
    stats: { bytes_served: MIB, hit_bytes: MIB, miss_bytes: 0, requests: 1, hits: 1, misses: 0, hit_rate: 1 },
    loading_now: IDLE,
    recent_apps: [], recent_unattributed_bytes: 0,
    ...extra,
  };
}

const loadingTile = (clients) => summaryTiles(clients, "7d").find((t) => t.key === "connected").detail;

test("MUTATION PIN: live_now wins over the swept loading_now (box, speed, pill, tile)", () => {
  const c = pc({ loading_now: IDLE, live_now: loadingOf("Satisfactory", 12) });
  assert.equal(currentLoading(c), c.live_now);
  const box = nowBox(c);
  assert.equal(box.loading, true);
  assert.equal(box.title, "Satisfactory");
  assert.equal(box.speed, "12.0 MB/s");
  assert.match(loadingNowText(c), /Satisfactory/);
  assert.equal(connectionPill(c).text, "Downloading");
  assert.equal(loadingTile([c]), "1 loading now");
});

test("MUTATION PIN: a live idle beats a stale swept 'loading' (the client stopped since the sweep)", () => {
  const c = pc({ loading_now: loadingOf("Satisfactory", 12), live_now: IDLE });
  assert.equal(nowBox(c).loading, false);
  assert.equal(loadingNowText(c), "Loading now: nothing");
  assert.notEqual(connectionPill(c).text, "Downloading");
  assert.equal(loadingTile([c]), "Nothing loading now");
});

test("feed ok but live_now null or missing (server before rc21): the swept loading_now is used", () => {
  for (const c of [pc({ loading_now: loadingOf("Satisfactory", 3), live_now: null }), pc({ loading_now: loadingOf("Satisfactory", 3) })]) {
    assert.equal(currentLoading(c), c.loading_now);
    assert.equal(nowBox(c).title, "Satisfactory");
    assert.equal(connectionPill(c).text, "Downloading");
    assert.equal(loadingTile([c]), "1 loading now");
  }
  // Neither sent (feed not ok): the dash, not "nothing".
  const none = pc({ loading_now: null, live_now: null });
  assert.equal(currentLoading(none), null);
  assert.equal(loadingNowText(none), "Loading now: —");
  assert.equal(nowBox(none).loading, false);
});

// ---------------------------------------------------------------------------
// The rendered view
// ---------------------------------------------------------------------------

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
const feed = { enabled: true, state: "ok", last_sweep_at: iso(60_000), sweep_interval_minutes: 5, traffic_since: null };
const live = { live_since: iso(900_000), current_window_seconds: 17 };

function respond(status, data) {
  const text = JSON.stringify(data);
  return { ok: status >= 200 && status < 300, status, json: async () => data, text: async () => text };
}
globalThis.fetch = async (url) => {
  const u = new URL(String(url));
  const p = u.pathname;
  if (p === "/v1/clients/overview") {
    return respond(200, {
      event_feed: feed, range: u.searchParams.get("range"), tier: "1h", range_start: null, active_window_minutes: 15,
      // The swept window still says idle; the live tier already sees the game.
      clients: [pc({ loading_now: IDLE, live_now: loadingOf("Satisfactory", 1.2) })],
      live,
    });
  }
  if (["/v1/clients", "/v1/jobs", "/v1/games"].includes(p)) return respond(200, []);
  if (p === "/v1/cache/summary") return respond(200, {});
  if (p === "/v1/settings") return respond(200, { readonly: false, settings: [] });
  return respond(404, { detail: "Not Found" });
};

const { store } = await import("../js/store-singleton.js");
const { renderClients } = await import("../js/views/clients.js");
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
const text = (s, sel) => {
  const n = s.querySelector(sel);
  return n ? n.textContent : null;
};

test("MUTATION PIN: the rendered card and tile show the live state, not the swept one", async () => {
  navigateTo("clients");
  const s = renderClients();
  await until(() => /Satisfactory/.test(text(s, '[data-role="loading-now"]') || ""), "the live game in the now box");
  assert.match(text(s, '[data-role="loading-now"]'), /1\.2 MB\/s/);
  assert.match(text(s, '[data-tile="connected"]'), /1 loading now/);
});
