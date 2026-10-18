/**
 * WP WEB-FIX-20.2: the store's per-resource `refresh(...kinds)` (a view's
 * mount asks for exactly its data, now) and the store loops' retry
 * schedule after a failed poll (`storeRetryBackoff`: 2.5 s, 5 s, 10 s, then
 * the loop's own interval, never the old 30 s cap).
 *
 * Harness: a counting fake api client, no DOM (`document` undefined), the
 * real `createPollingStore`; jitter is switched off where a delay is
 * asserted.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createPollingStore, storeRetryBackoff, STORE_RETRY_BASE_MS, DEFAULT_INTERVALS } from "../js/store.js";
import { computeBackoffDelay } from "../js/backoff.js";

const tick = (ms = 10) => new Promise((r) => setTimeout(r, ms));
async function until(pred, label, timeoutMs = 2000) {
  const start = Date.now();
  while (!pred()) {
    if (Date.now() - start > timeoutMs) throw new Error(`timed out waiting for: ${label}`);
    await tick(5);
  }
}

/** Long intervals: after the first round nothing polls by itself. */
const QUIET = Object.freeze({ jobsFastMs: 60_000, jobsSlowMs: 60_000, gamesMs: 60_000, clientsMs: 60_000 });

function countingClient() {
  const calls = { jobs: 0, games: 0, clients: 0, cacheSummary: 0 };
  return {
    calls,
    jobs: async () => (calls.jobs++, []),
    games: async () => (calls.games++, []),
    clients: async () => (calls.clients++, []),
    cacheSummary: async () => (calls.cacheSummary++, {}),
  };
}

test("MUTATION PIN: refresh(kind) polls exactly the named resources now, nothing else", async () => {
  const api = countingClient();
  const store = createPollingStore({ apiClient: api, intervals: QUIET });
  try {
    store.start();
    await until(() => Object.values(api.calls).every((n) => n === 1), "first round");
    await tick(20);
    store.refresh("clients");
    await until(() => api.calls.clients === 2, "clients re-polled");
    await tick(20);
    assert.deepEqual(api.calls, { jobs: 1, games: 1, clients: 2, cacheSummary: 1 });
    store.refresh("games", "jobs", "cache");
    await until(() => api.calls.games === 2 && api.calls.jobs === 2 && api.calls.cacheSummary === 2, "three re-polled");
    await tick(20);
    assert.equal(api.calls.clients, 2, "clients untouched");
    assert.throws(() => store.refresh("nope"), RangeError);
  } finally {
    store.stop();
  }
});

test("refresh on a stopped store is a no-op (onboarding: no key, no loops)", async () => {
  const api = countingClient();
  const store = createPollingStore({ apiClient: api, intervals: QUIET });
  store.refresh("games", "clients");
  await tick(30);
  assert.deepEqual(api.calls, { jobs: 0, games: 0, clients: 0, cacheSummary: 0 });
});

test("MUTATION PIN: storeRetryBackoff — 2.5 s, 5 s, 10 s, then the loop's own interval, never longer", () => {
  assert.equal(STORE_RETRY_BASE_MS, 2500);
  const opts = { ...storeRetryBackoff(DEFAULT_INTERVALS.gamesMs), jitterRatio: 0 };
  assert.deepEqual([0, 1, 2, 3, 4, 10].map((a) => computeBackoffDelay(a, opts)), [2500, 5000, 10000, 15000, 15000, 15000]);
  const clients = { ...storeRetryBackoff(DEFAULT_INTERVALS.clientsMs), jitterRatio: 0 };
  assert.equal(computeBackoffDelay(5, clients), 20000, "capped at the clients loop's 20 s");
});

test("a failed games poll is retried after the short delay, not a full interval", async () => {
  let fail = true;
  const calls = { games: 0 };
  const api = {
    jobs: async () => [],
    clients: async () => [],
    cacheSummary: async () => ({}),
    games: async () => {
      calls.games++;
      if (fail) throw new Error("vault-api restarting");
      return [{ appid: 1 }];
    },
  };
  // A tiny base (the shape under test is "base, doubling, capped at the
  // interval"; the real numbers are pinned above).
  const store = createPollingStore({ apiClient: api, intervals: QUIET, backoffOptions: { baseMs: 20, maxMs: 60_000, jitterRatio: 0 } });
  const ticks = [];
  store.subscribe("games", (p) => ticks.push(p.error ? "error" : "items"));
  try {
    store.start();
    await until(() => calls.games === 1, "first, failing poll");
    fail = false;
    await until(() => ticks.includes("items"), "retried well before the 60 s interval", 1000);
    assert.deepEqual(ticks, ["error", "items"]);
  } finally {
    store.stop();
  }
});
