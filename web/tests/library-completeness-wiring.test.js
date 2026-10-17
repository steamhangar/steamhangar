/**
 * WP CACHE-COMPLETE-1c — the Library view repaints a card whose
 * completeness changed. The overlay is not part of `dispKind` (data-dk stays
 * `cached`), so without the card's `data-ck` in views/library.js's
 * structural key a games poll would only PATCH the card (size, aria-label)
 * and the old status word and icon would stay on screen. Harness as
 * library-update-check-wiring.test.js (fake-dom, stored API key, routing
 * fetch fake the real store polls).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, after } from "node:test";
import assert from "node:assert/strict";
import { createFakeDom } from "./fake-dom.js";

const dom = createFakeDom();
globalThis.document = dom.document;
const toastEl = dom.document.createElement("div");
const toastText = dom.document.createElement("span");
const realGetById = dom.document.getElementById.bind(dom.document);
dom.document.getElementById = (id) => (id === "toast" ? toastEl : id === "toast-text" ? toastText : realGetById(id));

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
    update_available: false,
    update_checked_at: "2026-10-13T08:00:00Z",
    cache_complete_pct: 100,
    cache_missing_chunks: 0,
    group_cache_complete_pct: 100,
    ...overrides,
  };
}

const server = { games: [vaultGame(10, "Vault Ten"), vaultGame(30, "Vault Thirty")] };
function respond(status, data) {
  const text = data === undefined ? "" : JSON.stringify(data);
  return { ok: status >= 200 && status < 300, status, json: async () => data, text: async () => text };
}
globalThis.fetch = async (url) => {
  const p = new URL(String(url)).pathname;
  if (p === "/v1/games") return respond(200, server.games);
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
const card = (s, appid) => s.querySelector(`.card[data-appid="${appid}"]`);
const fullWord = (s, appid) => {
  const st = card(s, appid).querySelector(".state");
  const full = st.querySelector(".stfull");
  return full ? full.textContent : st.textContent;
};
// WP LIBRARY-2: the card shows its kind as the status dot's tone (`tx-<kind>`).
const pillKind = (s, appid) => card(s, appid).querySelector(".stt .dot").className;

test("MUTATION TARGET -- a new figure on the next games poll rebuilds that card (Current -> Partially cached -> Current)", async () => {
  pathname = "/library";
  navigateTo("library");
  store.start();
  const s = renderLibrary();
  try {
    await until(() => s.querySelectorAll(".card").length >= 2, "cards on screen");
    assert.equal(fullWord(s, 10), "Current");
    assert.match(pillKind(s, 10), /\btx-cached\b/);

    server.games = [vaultGame(10, "Vault Ten", { cache_complete_pct: 61, cache_missing_chunks: 40 }), vaultGame(30, "Vault Thirty")];
    store.refreshNow();
    await until(() => fullWord(s, 10) === "Partially cached 61%", "partial word painted");
    assert.match(pillKind(s, 10), /\btx-partial\b/);
    assert.equal(card(s, 10).dataset.ck, "partial:61");
    assert.equal(fullWord(s, 30), "Current", "the other card is untouched");

    server.games = [vaultGame(10, "Vault Ten", { cache_complete_pct: null, cache_missing_chunks: null }), vaultGame(30, "Vault Thirty")];
    store.refreshNow();
    await until(() => fullWord(s, 10) === "Cached · not verified", "unknown painted");
    assert.match(pillKind(s, 10), /\btx-unverified\b/);

    server.games = [vaultGame(10, "Vault Ten"), vaultGame(30, "Vault Thirty")];
    store.refreshNow();
    await until(() => fullWord(s, 10) === "Current", "back to Current");
    assert.match(pillKind(s, 10), /\btx-cached\b/);
  } finally {
    store.stop();
  }
});
