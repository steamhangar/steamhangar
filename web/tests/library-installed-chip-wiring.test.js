/**
 * DOM-wiring pin for the "Installed, not cached" chip (WP
 * LIB-FILTER-INST-1) in `web/js/views/library.js`: the chip is rendered with
 * its count against the real api.js -> fetch path, and pressing it leaves
 * exactly the installed-but-not-cached cards on screen. The decision itself
 * is pinned in library-filter-installed.test.js.
 *
 * Harness as in library-update-check-wiring.test.js (fake-dom, Map-backed
 * localStorage with an API key, routing fetch fake); this server is an
 * older vault-api without the update routes, so only the core chips show.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, after } from "node:test";
import assert from "node:assert/strict";
import { createFakeDom } from "./fake-dom.js";

const dom = createFakeDom();
globalThis.document = dom.document;
const storage = new Map([["steamvault.apiKey", "test-key"]]);
let pathname = "/library";
globalThis.window = {
  localStorage: {
    getItem: (k) => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => storage.set(k, String(v)),
  },
  location: {
    origin: "http://vault.test",
    get pathname() {
      return pathname;
    },
    reload() {},
  },
  history: {
    pushState(_s, _t, p) {
      pathname = p;
    },
  },
  addEventListener() {},
  removeEventListener() {},
};

const pcs = (...ids) => ids.map((client_id) => ({ client_id, reported_at: "2026-10-14T08:00:00Z" }));
function vaultGame(appid, name, overrides = {}) {
  return {
    appid,
    name,
    status: "done",
    last_prefill_at: "2026-09-01T00:00:00Z",
    last_manifest_check: null,
    depot_count: 1,
    size_bytes: 2_000_000_000,
    needs_force: false,
    installed_on: [],
    ...overrides,
  };
}
const GAMES = [
  vaultGame(10, "Cached Only"),
  vaultGame(20, "Installed Cached", { installed_on: pcs("Demon") }),
  vaultGame(30, "Installed Missing", { installed_on: pcs("Demon", "Frame"), size_bytes: null, status: "idle", last_prefill_at: null }),
  vaultGame(40, "Not Installed Missing", { size_bytes: null, status: "idle", last_prefill_at: null }),
];

function respond(status, data) {
  const text = data === undefined ? "" : JSON.stringify(data);
  return { ok: status >= 200 && status < 300, status, json: async () => data, text: async () => text };
}
globalThis.fetch = async (url) => {
  const p = new URL(String(url)).pathname;
  if (p === "/v1/games") return respond(200, GAMES);
  if (p === "/v1/jobs") return respond(200, []);
  if (p === "/v1/clients") return respond(200, []);
  if (p === "/v1/cache/summary") return respond(200, { total_bytes: 0, top_consumers: [] });
  if (p === "/v1/settings") {
    return respond(200, {
      readonly: false,
      settings: [{ key: "steam_library_steamid", effective: "", source: "db", fallback: "", applies: "immediately", env_only: false }],
    });
  }
  return respond(404, { detail: "Not Found" });
};

const { renderLibrary } = await import("../js/views/library.js");
const { store } = await import("../js/store-singleton.js");
const { navigateTo } = await import("../js/router.js");
after(() => store.stop());

const tick = (ms = 15) => new Promise((r) => setTimeout(r, ms));
async function until(pred, label, timeoutMs = 3000) {
  const start = Date.now();
  while (!pred()) {
    if (Date.now() - start > timeoutMs) throw new Error(`timed out waiting for: ${label}`);
    await tick(5);
  }
}
const chip = (s, key) => s.querySelector(`.chip[data-f="${key}"]`);
const pressChip = (s, key) => {
  const el = chip(s, key);
  s.querySelector(".chips").dispatchEvent({ type: "click", target: { closest: () => el } });
};
const cardIds = (s) => s.querySelectorAll(".card").map((c) => Number(c.dataset.appid));

test("the Library renders the chip with its count; pressing it shows only installed-but-not-cached cards", async () => {
  pathname = "/library";
  navigateTo("library");
  const s = renderLibrary();
  await until(() => s.querySelectorAll(".card").length === GAMES.length, "cards on screen");
  const el = chip(s, "installed");
  assert.ok(el, "chip rendered");
  assert.equal(el.textContent, "Installed, not cached 1");
  assert.equal(el.getAttribute("aria-pressed"), "false");
  pressChip(s, "installed");
  await until(() => cardIds(s).length === 1, "grid filtered");
  assert.deepEqual(cardIds(s), [30]);
  assert.equal(chip(s, "installed").getAttribute("aria-pressed"), "true");
  pressChip(s, "all");
  await until(() => cardIds(s).length === GAMES.length, "grid back to all");
});
