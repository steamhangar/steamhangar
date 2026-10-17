/**
 * Demo mode renders the Clients page and the Downloads statistics header
 * (WP D15c): no API key, `steamvault.demoMode=1`, every request served by
 * demo-data.js. Proves the demo routes exist for both views and carry
 * what they paint (agent and agentless cards; the Downloads hero's big
 * number, four tiles and graph, the running demo job's speed).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, after } from "node:test";
import assert from "node:assert/strict";
import { createFakeDom } from "./fake-dom.js";

const dom = createFakeDom();
globalThis.document = dom.document;
const storage = new Map([["steamvault.demoMode", "1"]]);
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
globalThis.fetch = async () => {
  throw new Error("demo mode must not touch the network");
};

const { store } = await import("../js/store-singleton.js");
const { renderClients } = await import("../js/views/clients.js");
const { renderDownloads } = await import("../js/views/downloads.js");
const { navigateTo } = await import("../js/router.js");

after(() => {
  store.stop();
  navigateTo("library");
});

const tick = (ms = 10) => new Promise((r) => setTimeout(r, ms));
async function until(pred, label, timeoutMs = 3000) {
  const start = Date.now();
  while (!pred()) {
    if (Date.now() - start > timeoutMs) throw new Error(`timed out waiting for: ${label}`);
    await tick(5);
  }
}

test("demo: the Clients page lists agent PCs and agentless addresses with traffic", async () => {
  const s = renderClients();
  dom.appRoot.replaceChildren(s);
  await until(() => s.querySelectorAll(".clcard").length >= 4, "demo cards");
  const kinds = s.querySelectorAll(".clcard").map((c) => c.getAttribute("data-kind"));
  assert.ok(kinds.includes("agent") && kinds.includes("address"));
  const names = s.querySelectorAll(".clcard .nm").map((n) => n.textContent);
  assert.ok(names.includes("workshop-pc") && names.includes("10.10.0.57"), names.join());
  assert.equal(s.querySelector('[data-role="clients-note"]').hidden, true, "demo feed is ok");
  assert.ok(s.querySelectorAll('[data-role="stats"]').length >= 4);
  navigateTo("library");
});

test("demo: the Downloads hero shows the speed, four tiles, the graph and the running job's speed", async () => {
  const s = renderDownloads();
  dom.appRoot.replaceChildren(s);
  await until(() => !s.querySelector('[data-role="stats-grid"]').hidden, "tiles");
  assert.equal(s.querySelector('[data-role="stats-grid"]').querySelectorAll(".tile").length, 4);
  assert.match(s.querySelector('[data-role="stats-current"]').textContent, /^[\d.]+(MB|KB|GB)\/s$/);
  assert.equal(s.querySelector('[data-role="stats-graph"]').hidden, false);
  await until(() => {
    const line = s.querySelector('[data-role="job-speed"]');
    return line && !line.hidden;
  }, "the demo job's speed");
  assert.match(s.querySelector('[data-role="job-speed"]').textContent, /MB\/s · .* loaded so far$/);
});
