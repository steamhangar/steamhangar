/**
 * WP WEB-FIX-4, the other half of the relay rule: when the owned list is
 * already loaded (the Library loaded it, here via the same
 * `ownedLibrary.load()` the Library calls), Downloads uses it and starts
 * no load of its own. Separate file because the owned list is page-global
 * state (`owned-singleton.js`): the sibling file pins the never-loaded
 * path. Same harness as downloads-owned-names.test.js.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, after } from "node:test";
import assert from "node:assert/strict";
import { createFakeDom, fakeClickEvent } from "./fake-dom.js";

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
const job = (id, appid, status) => ({
  id, appid, type: "prefill", status, stop_request: null, created_at: T, started_at: T, finished_at: T,
});

const NOT_LOGGED_IN_EXCERPT = [
  "Unhandled exception. System.InvalidOperationException: Failed to read input in non-interactive mode.",
  "   at SteamPrefill.Handlers.Steam.Steam3Session.LoginAsync() in /src/Steam3Session.cs:line 101",
  "[vault-api] SteamPrefill has no usable Steam session ...",
  "[vault-api] Prefill failed (reason=not_logged_in); the depot mapping for this app was left unchanged.",
].join("\n");
const EXIT_CODE_EXCERPT = [
  "Depot 301 failed: disk full",
  "[vault-api] SteamPrefill exited with code 1.",
  "[vault-api] Prefill failed (reason=exit_code); the depot mapping for this app was left unchanged.",
].join("\n");

const server = {
  games: [
    { appid: 10, name: "Vault Ten", status: "done" },
    { appid: 20, name: null, status: "error" },
  ],
  jobs: [job(1, 10, "done"), job(2, 20, "error")],
  excerpts: { 1: "ok", 2: NOT_LOGGED_IN_EXCERPT, 3: EXIT_CODE_EXCERPT },
  owned: [
    { appid: 10, name: "Steam Ten" },
    { appid: 20, name: "Owned Twenty" },
  ],
  calls: new Map(),
  prefillBodies: [],
};
const count = (p) => server.calls.get(p) || 0;
function respond(status, data) {
  const text = JSON.stringify(data);
  return { ok: status >= 200 && status < 300, status, json: async () => data, text: async () => text };
}
globalThis.fetch = async (url, init = {}) => {
  const u = new URL(String(url));
  const p = u.pathname;
  server.calls.set(p, count(p) + 1);
  const method = init.method || "GET";
  if (p === "/v1/jobs") return respond(200, server.jobs);
  if (p === "/v1/games") return respond(200, server.games);
  if (p === "/v1/clients") return respond(200, []);
  if (p === "/v1/cache/summary") return respond(200, {});
  if (p === "/v1/settings") {
    return respond(200, {
      readonly: false,
      settings: [{ key: "steam_library_steamid", effective: STEAMID, source: "db", fallback: "", applies: "immediately", env_only: false }],
    });
  }
  if (p === "/v1/steam/owned-games") return respond(200, { configured: true, game_count: server.owned.length, games: server.owned });
  if (p === "/v1/prefill" && method === "POST") {
    const body = JSON.parse(init.body);
    server.prefillBodies.push(body);
    return respond(202, body.appids.map((appid) => ({ appid, job_id: 99, status: "queued", deduplicated: false })));
  }
  const m = /^\/v1\/jobs\/(\d+)$/.exec(p);
  if (m) {
    const id = Number(m[1]);
    const j = server.jobs.find((x) => x.id === id);
    return j ? respond(200, { ...j, log_excerpt: server.excerpts[id] ?? null }) : respond(404, { detail: "Unknown job" });
  }
  return respond(404, { detail: "Not Found" });
};

const { store } = await import("../js/store-singleton.js");
const { renderDownloads } = await import("../js/views/downloads.js");
const { ownedLibrary } = await import("../js/owned-singleton.js");
const { OWNED_STATUS } = await import("../js/lib/owned-library.js");

after(() => store.stop());

const tick = (ms = 15) => new Promise((r) => setTimeout(r, ms));
async function until(pred, label, timeoutMs = 2000) {
  const start = Date.now();
  while (!pred()) {
    if (Date.now() - start > timeoutMs) throw new Error(`timed out waiting for: ${label}`);
    await tick(5);
  }
}
const RELAY = "/v1/steam/owned-games";
const historyNames = (s) => s.querySelectorAll(".hrow .nm").map((n) => n.textContent);
const row = (s, jobId) => s.querySelector(`.hrow[data-jid="${jobId}"]`);
async function pollTicks(n) {
  for (let i = 0; i < n; i++) {
    const before = count("/v1/games");
    store.refreshNow();
    await until(() => count("/v1/games") > before, "a games poll");
    await tick(10);
  }
}

test("MUTATION PIN (reuse): an already-loaded owned list names the job; Downloads adds no relay call", async () => {
  try {
    await ownedLibrary.load(); // what renderLibrary() does on open
    assert.equal(ownedLibrary.current().status, OWNED_STATUS.READY);
    assert.equal(count(RELAY), 1);
    const s = renderDownloads();
    await until(() => historyNames(s).includes("Owned Twenty"), "owned-name fallback from the loaded list");
    assert.deepEqual(historyNames(s), ["Vault Ten", "Owned Twenty"]);
    await pollTicks(3);
    renderDownloads();
    await tick(20);
    assert.equal(count(RELAY), 1, "Downloads never loads a list that is already there");
  } finally {
    store.stop();
  }
});
