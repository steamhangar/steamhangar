/**
 * DOM-wiring pins for content-package groups in `web/js/views/library.js`
 * (WP TOOL-APPS-2c, ADR-0022): one card per group against the real
 * api.js -> fetch path, the exact group size from the depot lists, an older
 * vault-api response rendering flat, a package's tick rebuilding its group
 * card, and bulk download of a picked group card sending exactly the
 * targets the same rows would get ungrouped.
 *
 * Harness as in library-update-check-wiring.test.js.
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

const GIB = 1_073_741_824;
function vaultGame(appid, name, overrides = {}) {
  return {
    appid,
    name,
    status: "done",
    last_prefill_at: "2026-09-01T00:00:00Z",
    last_manifest_check: null,
    depot_count: 1,
    size_bytes: 2 * GIB,
    needs_force: false,
    installed_on: [],
    update_available: false,
    update_checked_at: null,
    parent_appid: null,
    app_type: null,
    ...overrides,
  };
}

function freshGames() {
  return [
    vaultGame(10, "Aurora Cascade"),
    vaultGame(20, "Starfall Legion", { app_type: "game" }),
    vaultGame(21, "Starfall Legion - Voice Pack", { parent_appid: 20, app_type: "dlc", size_bytes: 1 * GIB, depot_count: 2 }),
    vaultGame(22, "Starfall Legion - Cosmetics", {
      parent_appid: 20,
      app_type: "dlc",
      status: "error",
      size_bytes: null,
      depot_count: 0,
    }),
  ];
}
// Parent 1.5 + 0.5 shared, package 0.5 + the same 0.5 shared: union 2.5 GiB,
// while the two list sizes (2 + 1) sum to 3.
const DEPOTS = {
  20: [{ depotid: 201, shared: false, size_bytes: 1.5 * GIB }, { depotid: 299, shared: true, size_bytes: 0.5 * GIB }],
  21: [
    { depotid: 211, shared: false, size_bytes: 0.5 * GIB },
    { depotid: 299, shared: true, size_bytes: 0.5 * GIB },
  ],
};

const server = { mode: "new", games: freshGames(), prefillBodies: [], calls: new Map() };
const count = (key) => server.calls.get(key) || 0;
function respond(status, data) {
  const text = data === undefined ? "" : JSON.stringify(data);
  return { ok: status >= 200 && status < 300, status, json: async () => data, text: async () => text };
}
const strip = ({ parent_appid, app_type, ...rest }) => rest;
globalThis.fetch = async (url, init = {}) => {
  const u = new URL(String(url));
  const p = u.pathname;
  const method = init.method || "GET";
  server.calls.set(`${method} ${p}`, count(`${method} ${p}`) + 1);
  if (p === "/v1/games") return respond(200, server.mode === "new" ? server.games : server.games.map(strip));
  const m = p.match(/^\/v1\/games\/(\d+)$/);
  if (m) {
    const g = server.games.find((x) => x.appid === Number(m[1]));
    return g ? respond(200, { ...g, depots: DEPOTS[g.appid] || [] }) : respond(404, { detail: "nope" });
  }
  if (p === "/v1/jobs") return respond(200, []);
  if (p === "/v1/prefill" && method === "POST") {
    const body = JSON.parse(init.body);
    server.prefillBodies.push(body);
    return respond(202, body.appids.map((appid, i) => ({ appid, job_id: 500 + i, status: "queued", deduplicated: false })));
  }
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
const { initToast } = await import("../js/components/toast.js");
const { groupSizes } = await import("../js/group-sizes-singleton.js");
initToast();

after(() => store.stop());

const tick = (ms = 15) => new Promise((r) => setTimeout(r, ms));
async function until(pred, label, timeoutMs = 3000) {
  const start = Date.now();
  while (!pred()) {
    if (Date.now() - start > timeoutMs) throw new Error(`timed out waiting for: ${label}`);
    await tick(5);
  }
}
const cards = (s) => s.querySelectorAll(".card").map((c) => Number(c.dataset.appid));
const card = (s, appid) => s.querySelector(`.card[data-appid="${appid}"]`);

async function openLibrary(expectCards) {
  pathname = "/library";
  navigateTo("library");
  store.refreshNow();
  const section = renderLibrary();
  await until(() => cards(section).length === expectCards, `${expectCards} cards on screen`);
  return section;
}

test("one card per group: packages hidden from the grid, the line and the failed warning on the parent's card", async () => {
  server.mode = "new";
  server.games = freshGames();
  const s = await openLibrary(2);
  assert.deepEqual(cards(s), [10, 20]);
  const g = card(s, 20);
  assert.equal(g.querySelector(".pkgline .pkgn").textContent, "+2 content packages");
  assert.equal(g.querySelector(".pkgline .pkgfail").textContent, " · 1 failed");
  assert.equal(g.dataset.dk, "cached");
  const failedChip = s.querySelector('.chip[data-f="failed"] .n');
  assert.equal(failedChip.textContent, "1", "the Failed chip counts the group once");
  assert.equal(s.querySelector('.chip[data-f="all"] .n').textContent, "2");
});

test("MUTATION PIN -- the group size is the depot union from the detail reads (2.5 GiB: the shared depot once, not the 3 GiB sum)", async () => {
  server.mode = "new";
  server.games = freshGames();
  const s = await openLibrary(2);
  await until(() => card(s, 20).querySelector(".meta .size").textContent === "2.5 GB", "union patched in");
  assert.ok(count("GET /v1/games/20") >= 1 && count("GET /v1/games/21") >= 1);
  assert.equal(count("GET /v1/games/22"), 0, "a member without bytes needs no depot list");
});

test("a package's games tick rebuilds its group card (the failed warning goes away)", async () => {
  server.mode = "new";
  server.games = freshGames();
  const s = await openLibrary(2);
  assert.equal(card(s, 20).dataset.gk, "2:1");
  server.games = server.games.map((g) => (g.appid === 22 ? { ...g, status: "done", size_bytes: 1 * GIB, depot_count: 1 } : g));
  store.refreshNow();
  await until(() => card(s, 20)?.dataset.gk === "2:0", "group card rebuilt");
  assert.equal(card(s, 20).querySelector(".pkgfail"), null);
  assert.equal(card(s, 22), null, "the package never gets a card of its own");
});

test("MUTATION PIN -- an older vault-api (no parent_appid / app_type fields) renders the flat list unchanged", async () => {
  server.mode = "old";
  server.games = freshGames();
  groupSizes.reset();
  const s = await openLibrary(4);
  assert.deepEqual(cards(s), [10, 20, 21, 22]);
  assert.equal(s.querySelector(".pkgline"), null);
  server.mode = "new";
});

test("MUTATION PIN (ADR-0022 §4) -- bulk download of a picked group card targets exactly what the same rows get ungrouped", async () => {
  server.mode = "new";
  server.games = freshGames();
  const s = await openLibrary(2);
  card(s, 20).dispatchEvent({ type: "contextmenu", preventDefault() {} });
  await until(() => s.querySelector(".bulk").classList.contains("up"), "select mode");
  const primary = s.querySelector(".bulkbtns .btn.primary");
  // Ungrouped, the three rows classify as: 20 cached, 21 cached, 22 failed -> retry.
  assert.equal(primary.disabled, false);
  assert.equal(primary.textContent, "Download 1 of 3");
  primary.dispatchEvent({ type: "click" });
  await until(() => server.prefillBodies.length === 1, "POST /v1/prefill sent");
  assert.deepEqual(server.prefillBodies[0], { appids: [22] });
});
