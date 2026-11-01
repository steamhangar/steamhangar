/**
 * Client-side request timeouts in `js/api.js` (NITS-WEB-1, from
 * NITS-AUDIT-1): every request has a deadline unless it is one of the
 * writes whose server work runs to the end anyway (`NO_TIMEOUT`), and the
 * reads that may wait for the server's depot size walk get the long one.
 *
 * Harness: a `fetch` fake that never answers on its own and rejects with
 * an AbortError when its signal aborts (what a browser does), plus
 * `mock.timers` for setTimeout so the 20 s and 5 min deadlines run in
 * no wall time.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, mock, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";

const storage = new Map([["steamvault.apiKey", "test-key"]]);
globalThis.window = {
  localStorage: {
    getItem: (k) => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => storage.set(k, String(v)),
  },
  location: { origin: "http://vault.test" },
};

/** Every request the fake saw: `{method, path, signal}`. */
let sent = [];
globalThis.fetch = (url, init = {}) => {
  const u = new URL(String(url));
  sent.push({ method: init.method || "GET", path: u.pathname, signal: init.signal });
  return new Promise((_resolve, reject) => {
    const signal = init.signal;
    if (!signal) return; // hangs forever, like a server that never answers
    const abort = () => {
      const err = new Error("This operation was aborted");
      err.name = "AbortError";
      reject(err);
    };
    if (signal.aborted) abort();
    else signal.addEventListener("abort", abort, { once: true });
  });
};

const { api, request, checkVaultApiKey, ERROR_KINDS, REQUEST_TIMEOUT_MS, LONG_REQUEST_TIMEOUT_MS, NO_TIMEOUT } =
  await import("../js/api.js");

beforeEach(() => {
  sent = [];
  mock.timers.enable({ apis: ["setTimeout"] });
});
afterEach(() => mock.timers.reset());

/** Settle state of a promise without awaiting it forever. */
function track(promise) {
  const t = { done: false, error: null, value: undefined };
  promise.then(
    (v) => {
      t.done = true;
      t.value = v;
    },
    (e) => {
      t.done = true;
      t.error = e;
    },
  );
  return t;
}
const flush = () => new Promise((r) => setImmediate(r));

test("the deadlines: 20 s by default, 5 min for size-walk reads, none for the untimed writes", () => {
  assert.equal(REQUEST_TIMEOUT_MS, 20_000);
  assert.equal(LONG_REQUEST_TIMEOUT_MS, 300_000);
  assert.equal(NO_TIMEOUT, 0);
});

test("MUTATION TARGET -- a request with no answer fails after 20 s as a network error naming the call", async () => {
  const t = track(api.jobs());
  await flush();
  mock.timers.tick(REQUEST_TIMEOUT_MS - 1);
  await flush();
  assert.equal(t.done, false, "still waiting just before the deadline");
  mock.timers.tick(1);
  await flush();
  assert.equal(t.done, true);
  assert.equal(t.error.name, "ApiError");
  assert.equal(t.error.kind, ERROR_KINDS.NETWORK, "same kind as any other request without an answer");
  assert.equal(t.error.message, "Network request timed out after 20 s: GET /v1/jobs");
});

test("an answer before the deadline clears the timer (nothing left to abort)", async () => {
  const saved = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    sent.push({ signal: init.signal });
    return { ok: true, status: 200, text: async () => "[]" };
  };
  try {
    assert.deepEqual(await api.clients(), []);
    mock.timers.tick(REQUEST_TIMEOUT_MS * 2);
    assert.equal(sent[0].signal.aborted, false, "the timer was cleared with the answer");
  } finally {
    globalThis.fetch = saved;
  }
});

test("MUTATION TARGET -- reads that may wait for the depot walk get 5 min, not 20 s", async () => {
  const calls = [api.games(), api.game(570), api.cacheSummary(), api.deletePlan(570)].map(track);
  await flush();
  mock.timers.tick(REQUEST_TIMEOUT_MS);
  await flush();
  assert.deepEqual(calls.map((c) => c.done), [false, false, false, false], "still waiting after 20 s");
  mock.timers.tick(LONG_REQUEST_TIMEOUT_MS - REQUEST_TIMEOUT_MS);
  await flush();
  assert.deepEqual(calls.map((c) => c.error && c.error.kind), Array(4).fill(ERROR_KINDS.NETWORK));
  assert.match(calls[0].error.message, /^Network request timed out after 300 s: GET \/v1\/games$/);
});

test("MUTATION TARGET -- delete, grouped delete, repair and 'check all cached' never time out", async () => {
  const calls = [
    api.deleteCache(570),
    api.deleteCacheWithPackages(570, [570, 571]),
    api.repairCache(570),
    api.repairCache(570, true),
    api.prefillCached(),
  ].map(track);
  await flush();
  assert.deepEqual(
    sent.map((s) => s.signal === undefined),
    [true, true, true, true, true],
    "no abort signal is attached at all",
  );
  mock.timers.tick(LONG_REQUEST_TIMEOUT_MS * 4);
  await flush();
  assert.deepEqual(calls.map((c) => c.done), [false, false, false, false, false]);
});

test("a caller's own abort stays an AbortError, not a timeout", async () => {
  const controller = new AbortController();
  const t = track(request("GET", "/v1/jobs", { signal: controller.signal }));
  await flush();
  controller.abort();
  await flush();
  assert.equal(t.error.name, "AbortError");
});

test("checkVaultApiKey: a server that never answers reads 'Could not reach the server.' after 20 s", async () => {
  const t = track(checkVaultApiKey("k"));
  await flush();
  mock.timers.tick(REQUEST_TIMEOUT_MS);
  await flush();
  assert.equal(t.error.kind, ERROR_KINDS.NETWORK);
  assert.equal(t.error.message, "Could not reach the server.");
});
