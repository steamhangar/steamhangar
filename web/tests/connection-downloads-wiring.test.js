/**
 * DOM-wiring pins for the connection-lost indicator in the real views
 * (WP WEB-FIX-2):
 *   - `views/downloads.js` disables its job-control buttons (with an
 *     explanatory title) while `connection-status.js` says the connection
 *     is lost, and re-enables them when it is restored;
 *   - `components/bypass-banner.js` still shows the shared #banner-wrap
 *     after the wrap/slot split (its slot alone being visible would paint
 *     nothing).
 *
 * Harness: fake-dom.js, a Map-backed localStorage with a stored key, and a
 * routing `fetch` fake; the REAL store-singleton polls it. The store is
 * stopped in `finally`-style teardown so a broken gate fails instead of
 * hanging the suite (docs/LEARNINGS.md, WEB-FIX-1). Text/booleans only,
 * never node assertions.
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

// Shell elements bypass-banner.js binds at import (fake-dom's own
// getElementById only knows #app).
const shell = {};
for (const id of ["banner-wrap", "bypass-banner-wrap", "bypass-banner-text", "bypass-details", "bypass-dismiss"]) {
  shell[id] = dom.document.createElement(id === "bypass-banner-text" ? "span" : "div");
  shell[id].hidden = id === "banner-wrap" || id === "bypass-banner-wrap";
}
shell["banner-wrap"].append(shell["bypass-banner-wrap"]);
const origGetById = dom.document.getElementById.bind(dom.document);
dom.document.getElementById = (id) => shell[id] || origGetById(id);

const JOBS = [
  { id: 1, appid: 10, type: "prefill", status: "running", stop_request: null, created_at: "2026-10-01T10:00:00Z", started_at: "2026-10-01T10:00:01Z" },
  { id: 2, appid: 20, type: "prefill", status: "paused", stop_request: null, created_at: "2026-10-01T09:00:00Z" },
  { id: 3, appid: 30, type: "prefill", status: "queued", stop_request: null, created_at: "2026-10-01T10:05:00Z" },
];
const CLIENTS = [{ client_id: "c1", bypass_suspected: true, last_seen: "2026-10-01T10:00:00Z" }];
function respond(status, data) {
  const text = JSON.stringify(data);
  return { ok: status >= 200 && status < 300, status, json: async () => data, text: async () => text };
}
globalThis.fetch = async (url) => {
  const u = new URL(String(url));
  if (u.pathname === "/v1/jobs") return respond(200, JOBS);
  if (u.pathname === "/v1/games") return respond(200, []);
  if (u.pathname === "/v1/clients") return respond(200, CLIENTS);
  if (u.pathname === "/v1/cache/summary") return respond(200, {});
  return respond(404, { detail: "Not Found" });
};

const { store } = await import("../js/store-singleton.js");
const { renderDownloads } = await import("../js/views/downloads.js");
await import("../js/components/bypass-banner.js");
const { setConnectionLost } = await import("../js/connection-status.js");
const { OFFLINE_CONTROL_TITLE } = await import("../js/lib/connection-watch.js");

after(() => {
  store.stop();
  setConnectionLost(false);
});

const tick = (ms = 20) => new Promise((r) => setTimeout(r, ms));
const controls = (section) =>
  section
    .querySelectorAll("button.btn")
    .filter((b) => ["Pause", "Cancel", "Resume", "Remove"].includes(b.textContent))
    .map((b) => `${b.textContent}:${b.disabled ? "off" : "on"}:${b.title || ""}`)
    .sort();

test("Downloads job controls are disabled with a title while the connection is lost, live again once restored", async () => {
  try {
    await tick();
    const section = renderDownloads();
    assert.deepEqual(controls(section), ["Cancel:on:", "Cancel:on:", "Pause:on:", "Remove:on:", "Resume:on:"]);

    setConnectionLost(true);
    const t = OFFLINE_CONTROL_TITLE;
    assert.deepEqual(controls(section), [`Cancel:off:${t}`, `Cancel:off:${t}`, `Pause:off:${t}`, `Remove:off:${t}`, `Resume:off:${t}`]);

    // A section built WHILE lost (navigating to Downloads mid-outage) is gated too.
    assert.deepEqual(controls(renderDownloads()), [`Cancel:off:${t}`, `Cancel:off:${t}`, `Pause:off:${t}`, `Remove:off:${t}`, `Resume:off:${t}`]);

    setConnectionLost(false);
    const live = renderDownloads();
    setConnectionLost(true);
    setConnectionLost(false);
    assert.deepEqual(controls(live), ["Cancel:on:", "Cancel:on:", "Pause:on:", "Remove:on:", "Resume:on:"]);
  } finally {
    store.stop();
    setConnectionLost(false);
  }
});

test("bypass banner still shows the shared #banner-wrap after the slot split", () => {
  assert.equal(shell["bypass-banner-wrap"].hidden, false, "bypass slot shown for a bypass_suspected client");
  assert.equal(shell["banner-wrap"].hidden, false, "the shared wrap must be shown too, or nothing paints");
});
