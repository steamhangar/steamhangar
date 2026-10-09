/**
 * DOM-wiring pins for WP WEB-FEAT-6 in `web/js/views/downloads.js`: the
 * Active card's badge follows `activeJobKind`/`activeJobWord`
 * (lib/job-partition.js) — Downloading / Updating / Verifying from the
 * app's games row — and the games subscription rebuilds the card when that
 * kind changes:
 *   - the first jobs paint can land before the first games answer (badge
 *     "Downloading"); the games answer then turns it into "Updating";
 *   - `needs_force` flipping on the next games poll turns it into
 *     "Verifying";
 *   - a games poll that changes no kind does NOT rebuild the card (the
 *     turning icon keeps running — round-7 rule).
 *
 * Harness: fake-dom.js, Map-backed localStorage with a stored key, a
 * routing `fetch` fake with a gate on `/v1/games`; the REAL store-singleton
 * polls it and is stopped in `after()`/`finally` (LEARNINGS WEB-FIX-1).
 * Assertions compare text, never nodes (LEARNINGS WEB-FIX-1); the
 * "no rebuild" pin uses a marker attribute on the card.
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

const T = "2026-10-09T08:00:00Z";
const server = {
  games: [
    { appid: 10, name: "Vault Ten", status: "running", last_prefill_at: T, needs_force: false, size_bytes: 5e9 },
  ],
  jobs: [{ id: 1, appid: 10, type: "prefill", status: "running", stop_request: null, created_at: T, started_at: T, finished_at: null }],
  gamesGate: null,
  calls: new Map(),
};
const count = (p) => server.calls.get(p) || 0;
function respond(status, data) {
  const text = JSON.stringify(data);
  return { ok: status >= 200 && status < 300, status, json: async () => data, text: async () => text };
}
globalThis.fetch = async (url) => {
  const p = new URL(String(url)).pathname;
  server.calls.set(p, count(p) + 1);
  if (p === "/v1/jobs") return respond(200, server.jobs);
  if (p === "/v1/games") {
    if (server.gamesGate) await server.gamesGate;
    return respond(200, server.games);
  }
  if (p === "/v1/clients") return respond(200, []);
  if (p === "/v1/cache/summary") return respond(200, {});
  return respond(404, { detail: "Not Found" });
};

const { store } = await import("../js/store-singleton.js");
const { renderDownloads } = await import("../js/views/downloads.js");

after(() => store.stop());

const tick = (ms = 15) => new Promise((r) => setTimeout(r, ms));
async function until(pred, label, timeoutMs = 2000) {
  const start = Date.now();
  while (!pred()) {
    if (Date.now() - start > timeoutMs) throw new Error(`timed out waiting for: ${label}`);
    await tick(5);
  }
}
const card = (s) => s.querySelector('.jobcard[data-jid="1"]');
/** The visible word: the badge's second child (the first is the icon,
 * whose sr-only label would duplicate the word in textContent). */
const word = (s) => card(s)?.querySelector(".badge").children[1].textContent ?? null;
const iconClass = (s) => card(s)?.querySelector(".badge .sic").className ?? "";

test("MUTATION PIN: Downloading before games -> Updating when games land -> Verifying when needs_force flips -> no rebuild on an unchanged poll", async () => {
  let release;
  server.gamesGate = new Promise((r) => (release = r));
  try {
    store.start();
    const s = renderDownloads();
    await until(() => card(s) !== null, "the active card from the jobs poll");
    assert.equal(word(s), "Downloading", "no games row yet: liveRunKind(undefined)");
    assert.match(iconClass(s), /\bk-running\b/);

    release();
    server.gamesGate = null;
    await until(() => word(s) === "Updating", "games subscription rebuilt the card");
    assert.match(iconClass(s), /\bk-updating\b/);
    assert.equal(card(s).dataset.dk, "updating");
    assert.match(card(s).querySelector(".badge").className, /\btx-updating\b/);

    server.games = [{ ...server.games[0], needs_force: true }];
    const before = count("/v1/games");
    store.refreshNow();
    await until(() => word(s) === "Verifying", "needs_force flip rebuilt the card");
    assert.ok(count("/v1/games") > before);
    assert.match(iconClass(s), /\bk-verify\b/);

    // An unchanged games poll keeps the card (and its turning icon).
    card(s).setAttribute("data-marker", "kept");
    const before2 = count("/v1/games");
    store.refreshNow();
    await until(() => count("/v1/games") > before2, "another games poll");
    await tick(30);
    assert.equal(card(s).getAttribute("data-marker"), "kept", "MUTATION PIN: no rebuild when no kind changed");
    assert.equal(word(s), "Verifying");
  } finally {
    server.gamesGate = null;
    if (release) release();
    store.stop();
  }
});

test("a paused job keeps Paused whatever the games row says", async () => {
  try {
    server.jobs = [{ ...server.jobs[0], status: "paused" }];
    store.start();
    const s = renderDownloads();
    await until(() => card(s) !== null && word(s) === "Paused", "paused card");
    assert.match(iconClass(s), /\bk-paused\b/);
  } finally {
    store.stop();
  }
});
