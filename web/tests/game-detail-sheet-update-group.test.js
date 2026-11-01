/**
 * "Update now" in the game detail sheet of a GROUP (a game plus its content
 * packages) sends the package ids too (NITS-WEB-1, from NITS-AUDIT-1).
 * game-detail-sheet-update.test.js pins the builder and the wiring by
 * source text; this file drives the real sheet end to end: the store
 * loads the rows through a routing `fetch` fake, `openDetail` opens the
 * sheet, the button is clicked, and the body of `POST /v1/prefill` is
 * read back. The same harness pins that a structural re-render keeps the
 * keyboard focus on the selected tab (LIBRARY-DETAIL-1 review nit).
 *
 * Harness: fake-dom.js, a stored API key and the REAL store-singleton
 * (stopped in `after`, docs/learnings/web.md rc21).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, after } from "node:test";
import assert from "node:assert/strict";
import { createFakeDom } from "./fake-dom.js";

const dom = createFakeDom();
globalThis.document = dom.document;
const storage = new Map([["steamvault.apiKey", "test-key"]]);
globalThis.window = dom.window;
dom.window.localStorage = {
  getItem: (k) => (storage.has(k) ? storage.get(k) : null),
  setItem: (k, v) => storage.set(k, String(v)),
};
Object.defineProperty(dom.window.location, "origin", { value: "http://vault.test" });
for (const id of ["toast", "toast-text"]) {
  const node = dom.document.createElement(id === "toast-text" ? "span" : "div");
  node.id = id;
  dom.document.body.appendChild(node);
}

const game = (appid, over = {}) => ({
  appid,
  name: `Game ${appid}`,
  status: "done",
  last_prefill_at: "2026-10-01T00:00:00Z",
  size_bytes: 1e9,
  needs_force: false,
  update_available: false,
  cache_complete_pct: 100,
  ...over,
});
// Parent current, one package flagged, one package current: "Update now"
// must send the flagged package (and only what is flagged).
let GAMES = [
  game(500, { name: "Parent" }),
  game(501, { name: "Parent - Pack", parent_appid: 500, app_type: "dlc", update_available: true }),
  game(502, { name: "Parent - Soundtrack", parent_appid: 500, app_type: "dlc" }),
];
const prefills = [];

function respond(status, data) {
  const text = JSON.stringify(data);
  return { ok: status >= 200 && status < 300, status, json: async () => data, text: async () => text };
}
globalThis.fetch = async (url, init = {}) => {
  const u = new URL(String(url));
  const method = (init.method || "GET").toUpperCase();
  if (u.pathname === "/v1/games" && method === "GET") return respond(200, GAMES);
  if (u.pathname === "/v1/jobs") return respond(200, []);
  if (u.pathname === "/v1/clients") return respond(200, []);
  if (u.pathname === "/v1/cache/summary") return respond(200, {});
  if (u.pathname === "/v1/mapping") return respond(200, []);
  const m = u.pathname.match(/^\/v1\/games\/(\d+)$/);
  if (m) return respond(200, { ...GAMES.find((g) => g.appid === Number(m[1])), depots: [] });
  if (u.pathname === "/v1/prefill" && method === "POST") {
    const { appids } = JSON.parse(init.body);
    prefills.push(appids);
    return respond(202, appids.map((appid, i) => ({ job_id: 100 + i, appid, status: "queued" })));
  }
  return respond(404, { detail: "Not Found" });
};

const { store } = await import("../js/store-singleton.js");
const { openDetail } = await import("../js/components/game-detail-sheet.js");

after(() => store.stop());

const tick = (ms = 10) => new Promise((r) => setTimeout(r, ms));
async function until(pred, what, ms = 3000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (pred()) return;
    await tick();
  }
  assert.fail(`timed out waiting for ${what}`);
}
const updateButton = () => dom.document.body.querySelector('[data-role="update-now"]');

test("MUTATION TARGET -- group sheet: 'Update now' posts the flagged package id, not only the parent", async () => {
  store.refreshNow();
  await until(() => Array.isArray(store.snapshot("games")) && store.snapshot("games").length === 3, "the games rows");
  openDetail(500, "Parent");
  await until(() => updateButton() !== null, "the Update now button");

  const btn = updateButton();
  assert.equal(btn.dataset.appids, "501", "the button carries the flagged package");
  btn.dispatchEvent({ type: "click" });
  await until(() => prefills.length === 1, "POST /v1/prefill");
  assert.deepEqual(prefills[0], [501], "the request body is the package id, the current parent and package stay out");
});

test("group sheet: with the parent flagged too, both ids go out in member order", async () => {
  GAMES = GAMES.map((g) => (g.appid === 500 ? { ...g, update_available: true } : g));
  prefills.length = 0;
  store.refreshNow();
  await until(() => store.snapshot("games")?.[0]?.update_available === true, "the flagged parent row");
  openDetail(500, "Parent");
  await until(() => updateButton()?.dataset.appids === "500,501", "the button with both ids");
  updateButton().dispatchEvent({ type: "click" });
  await until(() => prefills.length === 1, "POST /v1/prefill");
  assert.deepEqual(prefills[0], [500, 501]);
});

test("MUTATION TARGET -- a structural re-render keeps keyboard focus on the selected tab (LIBRARY-DETAIL-1 nit)", async () => {
  GAMES = GAMES.map((g) => ({ ...g, update_available: g.appid === 501 }));
  store.refreshNow();
  await until(() => store.snapshot("games")?.[0]?.update_available === false, "the reset rows");
  openDetail(500, "Parent");
  await until(() => updateButton()?.dataset.appids === "501", "the loaded sheet");
  const tabOf = (id) => dom.document.body.querySelector(`[data-role="tab-${id}"]`);
  tabOf("maintenance").dispatchEvent({ type: "click" });
  tabOf("maintenance").focus();

  // A flag flip changes the sheet's structural key: the tab strip is rebuilt.
  const before = tabOf("maintenance");
  GAMES = GAMES.map((g) => ({ ...g, update_available: g.appid === 502 }));
  store.refreshNow();
  await until(() => updateButton()?.dataset.appids === "502", "the re-rendered sheet");
  const rebuilt = tabOf("maintenance");
  // Booleans only: a failing assert on DOM nodes prints the whole tree.
  assert.equal(rebuilt !== before, true, "the strip was rebuilt");
  assert.equal(rebuilt.getAttribute("aria-selected"), "true", "the selection survives (activeTab)");
  assert.equal(dom.document.activeElement === rebuilt, true, "focus moved to the new tab, not lost");
});
