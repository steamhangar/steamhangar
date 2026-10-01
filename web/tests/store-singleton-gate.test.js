/**
 * `web/js/store-singleton.js` polling gate (WP WEB-FIX-1, N4).
 *
 * The singleton used to call `store.start()` unconditionally at import
 * time, so a genuine first run (no stored vault API key, demo mode off)
 * fired four `X-Api-Key: ""` requests per cycle — jobs/games/clients/cache
 * — each answering 401 and backing off, for the whole time the onboarding
 * overlay was up. Now it starts only with a stored key OR demo mode, the
 * same predicate `components/rail-panel.js` applies to its own one-time
 * settings fetch.
 *
 * This drives the REAL module, not a pure predicate: each scenario imports
 * `store-singleton.js` under a distinct query string (`?nokey`, `?withkey`,
 * `?demo`), which the ESM loader treats as a separate module instance and
 * therefore re-runs its top-level gate — while `store.js`/`api.js` (no
 * query) stay shared and read `window.localStorage` lazily per call, so
 * flipping the fake storage between imports is enough. `fetch` is a
 * counting fake; `document` is the same three-member stand-in
 * `store-poll-loop.test.js` uses.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";

globalThis.document = {
  hidden: false,
  addEventListener() {},
  removeEventListener() {},
};
const storage = new Map();
globalThis.window = {
  localStorage: {
    getItem: (k) => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => storage.set(k, String(v)),
  },
  location: { origin: "http://vault.test" },
};
let fetchCalls = 0;
globalThis.fetch = async () => {
  fetchCalls++;
  return { ok: false, status: 401, text: async () => '{"detail":"Invalid API key"}' };
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

test("no stored key, demo off (a first run): the loops are NOT started — zero requests", async () => {
  storage.clear();
  const { store } = await import("../js/store-singleton.js?nokey");
  // stop() in `finally`: if the gate regresses, the loops ARE running and a
  // failed assertion must not leave their timers keeping the process alive
  // (measured: the mutated suite hung instead of failing). Idempotent on a
  // never-started store.
  try {
    await sleep(30);
    assert.equal(fetchCalls, 0, "MUTATION TARGET: an unconditional store.start() fires four 401s per cycle during onboarding");
    assert.equal(store.snapshot("games"), undefined);
  } finally {
    store.stop();
  }
});

test("a stored key starts the loops (the gate is not simply 'never start')", async () => {
  storage.clear();
  storage.set("steamvault.apiKey", "real-key");
  const before = fetchCalls;
  const { store } = await import("../js/store-singleton.js?withkey");
  await sleep(30);
  assert.ok(fetchCalls > before, "with a key stored, polling must run");
  store.stop();
});

test("demo mode starts the loops without any network request", async () => {
  storage.clear();
  storage.set("steamvault.demoMode", "1");
  const before = fetchCalls;
  const { store } = await import("../js/store-singleton.js?demo");
  await sleep(30);
  assert.equal(fetchCalls, before, "demo mode is pure local data — no fetch");
  assert.ok(Array.isArray(store.snapshot("games")), "the games loop ticked against the demo fixtures");
  store.stop();
});
