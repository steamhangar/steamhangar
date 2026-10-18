/**
 * WP WEB-FIX-20.2: Downloads History titles never say "Unknown app" only
 * because a name source has not answered yet. Operator report (rc20.1):
 * right after the vault-api update every History row read "Unknown app"
 * and fixed itself minutes later.
 *
 *   (a) while no `GET /v1/games` answer is in (it failed: vault-api
 *       restarting), a nameless row reads "Loading…";
 *   (b) the failed games poll is retried on the short schedule (2.5 s, not
 *       the old up-to-30 s), and a transient owned-list failure (503) is
 *       retried after 2 s; meanwhile the row still reads "Loading…";
 *   (c) when the data lands every title is patched in place; a deep
 *       History row (from the `?range=` fetch, not among the store's newest
 *       jobs) also starts the owned-list load it needs; a genuinely
 *       unknown app reads "Unknown app" once everything settled.
 *
 * Tests run in order and share one page (the store, the owned list).
 *
 * Harness: fake-dom.js, Map-backed localStorage with a stored key, a
 * routing `fetch` fake; the REAL store-singleton polls it with its real
 * retry schedule and is stopped in `after()`. Text and counts only.
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

const STEAMID = "76561198042117903";
const T = "2026-10-01T10:00:00Z";
const job = (id, appid, status = "done") => ({
  id, appid, type: "prefill", status, stop_request: null, created_at: T, started_at: T, finished_at: T,
});

const server = {
  gamesFail: true,
  ownedFailures: 1,
  games: [
    { appid: 10, name: "Vault Ten", status: "done" },
    { appid: 90, name: null, status: "done" }, // owned-only: apps.name is NULL
    { appid: 95, name: null, status: "done" }, // nobody knows this one
  ],
  // The store's newest jobs: every app there has a vault name.
  jobs: [job(1, 10)],
  // The History's deep fetch: older owned-only and unknown rows too.
  deepJobs: [job(1, 10), job(2, 90), job(3, 95)],
  calls: new Map(),
};
const count = (p) => server.calls.get(p) || 0;
function respond(status, data) {
  const text = JSON.stringify(data);
  return { ok: status >= 200 && status < 300, status, json: async () => data, text: async () => text };
}
globalThis.fetch = async (url) => {
  const u = new URL(String(url));
  const p = u.pathname;
  server.calls.set(p, count(p) + 1);
  if (p === "/v1/jobs") return respond(200, u.searchParams.has("range") ? server.deepJobs : server.jobs);
  if (p === "/v1/games") return server.gamesFail ? respond(502, { detail: "Bad Gateway" }) : respond(200, server.games);
  if (p === "/v1/clients") return respond(200, []);
  if (p === "/v1/cache/summary") return respond(200, {});
  if (p === "/v1/settings") {
    return respond(200, {
      readonly: false,
      settings: [{ key: "steam_library_steamid", effective: STEAMID, source: "db", fallback: "", applies: "immediately", env_only: false }],
    });
  }
  if (p === "/v1/steam/owned-games") {
    if (server.ownedFailures > 0) {
      server.ownedFailures -= 1;
      return respond(503, { detail: "vault-api is starting" });
    }
    return respond(200, { configured: true, game_count: 1, games: [{ appid: 90, name: "Owned Ninety" }] });
  }
  return respond(404, { detail: "Not Found" });
};

const { store } = await import("../js/store-singleton.js");
const { renderDownloads, NAME_LOADING_TEXT } = await import("../js/views/downloads.js");
const { ownedLibrary } = await import("../js/owned-singleton.js");

after(() => store.stop());

const tick = (ms = 15) => new Promise((r) => setTimeout(r, ms));
async function until(pred, label, timeoutMs = 2000) {
  const start = Date.now();
  while (!pred()) {
    if (Date.now() - start > timeoutMs) throw new Error(`timed out waiting for: ${label}`);
    await tick(5);
  }
}
const names = (s) => s.querySelectorAll(".hrow .nm").map((n) => n.textContent);
let section = null;

test("(a) no games answer yet (502 while vault-api restarts): every nameless title reads Loading…, never Unknown app", async () => {
  assert.equal(NAME_LOADING_TEXT, "Loading…");
  section = renderDownloads();
  await until(() => count("/v1/games") >= 1, "the first, failing games poll");
  await until(() => names(section).length === 3, "the deep History rows");
  assert.deepEqual(names(section), ["Loading…", "Loading…", "Loading…"]);
  assert.equal(count("/v1/steam/owned-games"), 0, "no owned-list load before the games answer says a name is missing");
});

test("(b)+(c) the games poll is retried on the short schedule and the titles are patched; a deep row starts the owned load, a 503 there is retried after 2 s", async () => {
  server.gamesFail = false;
  // storeRetryBackoff: the second attempt comes after ~2.5 s (+-20 %), well
  // before the old 30 s cap or the 15 s interval.
  await until(() => names(section)[0] === "Vault Ten", "games retried and the vault name painted", 4500);
  // The deep row (job 2, appid 90) has no vault name and is not among the
  // store's newest jobs: it alone starts the owned-list load.
  await until(() => count("/v1/steam/owned-games") === 1, "owned load started for a deep History row");
  await until(() => ownedLibrary.current().status === "error", "the first owned answer was a 503");
  assert.equal(ownedLibrary.pending(), true, "a retry is armed");
  assert.deepEqual(names(section), ["Vault Ten", "Loading…", "Loading…"], "still loading while the retry is armed");
  await until(() => names(section)[1] === "Owned Ninety", "the retry's owned name, patched in", 3500);
  assert.equal(count("/v1/steam/owned-games"), 2, "exactly one retry");
  assert.equal(ownedLibrary.pending(), false);
  assert.deepEqual(names(section), ["Vault Ten", "Owned Ninety", "Unknown app"], "Unknown app only for an app nothing names, once everything settled");
});

test("a remount asks for jobs and games at once, not on the store's next 15 s tick", async () => {
  const { navigateTo } = await import("../js/router.js");
  navigateTo("library");
  await tick(30);
  const before = { games: count("/v1/games"), jobs: count("/v1/jobs") };
  navigateTo("downloads");
  renderDownloads();
  // /v1/jobs counts the History's own `?range=` fetch too: both must have
  // moved, the store's poll plus that fetch.
  await until(() => count("/v1/games") > before.games && count("/v1/jobs") >= before.jobs + 2, "fresh data on the remount", 1000);
});
