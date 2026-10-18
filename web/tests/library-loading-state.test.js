/**
 * WP WEB-FIX-20.2: the Library never shows an unknown count as zero.
 * Operator report (rc20.1): right after the vault-api update the chip read
 * "Cached 0" until a later poll brought "Cached 22" back.
 *
 *   (a) while no `GET /v1/games` answer is in (it failed: vault-api
 *       restarting) the chips show "…", the subtitle and the grid
 *       "Loading library…";
 *   (b) the failed poll is retried on the short schedule (2.5 s);
 *   (c) the first answer re-renders chips, subtitle and grid;
 *   and every mount asks for fresh games/jobs/cache at once.
 *
 * Harness: fake-dom.js, Map-backed localStorage with a stored key, a
 * routing `fetch` fake; the REAL store-singleton (its real retry schedule)
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

const game = (appid, name) => ({
  appid, name, status: "done", last_prefill_at: "2026-09-01T00:00:00Z", last_manifest_check: null,
  depot_count: 1, size_bytes: 2_000_000_000, needs_force: false, installed_on: [],
});

const server = { gamesFail: true, games: [game(10, "Ten"), game(20, "Twenty")], calls: new Map() };
const count = (p) => server.calls.get(p) || 0;
function respond(status, data) {
  const text = JSON.stringify(data);
  return { ok: status >= 200 && status < 300, status, json: async () => data, text: async () => text };
}
globalThis.fetch = async (url) => {
  const p = new URL(String(url)).pathname;
  server.calls.set(p, count(p) + 1);
  if (p === "/v1/games") return server.gamesFail ? respond(502, { detail: "Bad Gateway" }) : respond(200, server.games);
  if (p === "/v1/jobs" || p === "/v1/clients") return respond(200, []);
  if (p === "/v1/cache/summary") return respond(200, { total_bytes: 0, top_consumers: [] });
  if (p === "/v1/settings") return respond(200, { readonly: false, settings: [] });
  return respond(404, { detail: "Not Found" });
};

const { renderLibrary, COUNT_LOADING_TEXT, LIBRARY_LOADING_TEXT } = await import("../js/views/library.js");
const { store } = await import("../js/store-singleton.js");
const { navigateTo } = await import("../js/router.js");

after(() => {
  store.stop();
  navigateTo("downloads");
});

const tick = (ms = 15) => new Promise((r) => setTimeout(r, ms));
async function until(pred, label, timeoutMs = 2000) {
  const start = Date.now();
  while (!pred()) {
    if (Date.now() - start > timeoutMs) throw new Error(`timed out waiting for: ${label}`);
    await tick(5);
  }
}
const chips = (s) => s.querySelectorAll(".fchip").map((c) => c.textContent);
const sub = (s) => s.querySelector(".lib-sub").textContent;
let section = null;

test("(a) no games answer yet: chips say …, subtitle and grid say Loading library…, never 0", async () => {
  assert.equal(COUNT_LOADING_TEXT, "…");
  assert.equal(LIBRARY_LOADING_TEXT, "Loading library…");
  section = renderLibrary();
  await until(() => count("/v1/games") >= 1, "the first, failing games poll");
  await tick(30);
  const shown = chips(section);
  assert.ok(shown.length > 0, "chips are there");
  for (const c of shown) assert.match(c, / …$/, `"${c}" shows the loading count`);
  assert.ok(!shown.some((c) => / 0$/.test(c)), "no chip claims 0");
  assert.equal(sub(section), "Loading library…");
  assert.equal(section.querySelector('[data-role="library-loading"]').textContent, "Loading library…");
});

test("(b)+(c) the failed poll is retried after ~2.5 s and the first answer re-renders chips, subtitle and grid", async () => {
  server.gamesFail = false;
  await until(() => chips(section).includes("All 2"), "real counts", 4500);
  assert.notEqual(sub(section), "Loading library…");
  assert.equal(section.querySelector('[data-role="library-loading"]'), null);
});

test("a remount asks for games, jobs and the cache summary at once", async () => {
  navigateTo("downloads");
  await tick(30);
  const before = { games: count("/v1/games"), jobs: count("/v1/jobs"), cache: count("/v1/cache/summary") };
  navigateTo("library");
  renderLibrary();
  await until(
    () => count("/v1/games") > before.games && count("/v1/jobs") > before.jobs && count("/v1/cache/summary") > before.cache,
    "fresh data on the remount, not on the store's next 15 s tick",
    1000,
  );
});
