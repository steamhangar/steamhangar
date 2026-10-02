/**
 * DOM-wiring pins for WP WEB-FIX-4 in `web/js/views/downloads.js`:
 *   - job titles: vault name, then the owned-games list's name, then
 *     "App <id>";
 *   - the relay rule: no owned-list fetch while every job has a vault name;
 *     exactly ONE when a name is missing and nothing ever loaded the list
 *     (`/downloads` opened directly); never another on poll ticks or
 *     re-renders;
 *   - an owned list that lands renders its names at once (the
 *     subscription), not on the next 15 s games poll;
 *   - a not_logged_in failure and SteamPrefill's public-IP failure show
 *     their hint block, a closed <details> with the raw output (its open
 *     state survives a rebuild), and an offline-gated Retry on the newest
 *     job only; another failure renders its output as before.
 *
 * Tests run in order and share the page-global owned list (like a real
 * page), which is what the relay-count pins are about.
 *
 * Harness: fake-dom.js, Map-backed localStorage with a stored key, a
 * routing `fetch` fake counting calls per path; the REAL store-singleton
 * polls it and is stopped in `after()`/`finally` (LEARNINGS WEB-FIX-1).
 * Assertions compare text and counts, never nodes.
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
// Real SteamPrefill wording (the user's report), then vault-api's lines.
const PUBLIC_IP_EXCERPT = [
  " Warning!  lancache.steamcontent.com is resolving to a public IP address",
  "(162.254.197.25).",
  "LancacheNotFoundException: Lancache server is resolving to a public IP : 162.254.197.25",
  "[vault-api] SteamPrefill exited with code 1.",
  "[vault-api] Prefill failed (reason=exit_code); the depot mapping for this app was left unchanged.",
].join("\n");

const server = {
  games: [
    { appid: 10, name: "Vault Ten", status: "done" },
    { appid: 20, name: "Vault Twenty", status: "error" },
  ],
  jobs: [job(1, 10, "done"), job(2, 20, "error")],
  excerpts: { 1: "ok", 2: NOT_LOGGED_IN_EXCERPT, 3: EXIT_CODE_EXCERPT, 4: PUBLIC_IP_EXCERPT },
  ownedGate: null,
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
  if (p === "/v1/steam/owned-games") {
    if (server.ownedGate) await server.ownedGate;
    return respond(200, { configured: true, game_count: server.owned.length, games: server.owned });
  }
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
const { setConnectionLost } = await import("../js/connection-status.js");
const { OFFLINE_CONTROL_TITLE } = await import("../js/lib/connection-watch.js");
const { NEWER_JOB_LINE, PUBLIC_IP_README_SECTION } = await import("../js/lib/job-failure.js");

after(() => {
  store.stop();
  setConnectionLost(false);
});

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

test("every job has a vault name: Downloads shows them and never calls the relay", async () => {
  try {
    const s = renderDownloads();
    await until(() => historyNames(s).length === 2 && historyNames(s)[0] === "Vault Ten", "named history rows");
    await pollTicks(2);
    renderDownloads();
    await tick(20);
    assert.deepEqual(historyNames(s), ["Vault Ten", "Vault Twenty"], "vault name wins over the owned name");
    assert.equal(count(RELAY), 0, "no relay call while every name is known");
    assert.equal(count("/v1/settings"), 0, "not even the settings read");
  } finally {
    store.stop();
  }
});

/** Fire `toggle` on a <details>, and THROW if nothing listens: fake-dom's
 * dispatchEvent silently does nothing without a listener, which would let a
 * missing open-state handler pass as "dispatched". */
function fireToggle(el) {
  const listeners = el._listeners && el._listeners.get("toggle");
  if (!listeners || listeners.size === 0) throw new Error("no toggle listener on the <details>: its open state is never recorded");
  el.dispatchEvent({ type: "toggle" });
}
const retryButtons = (el) => el.querySelectorAll("button").filter((b) => b.textContent === "Retry");
async function expand(s, jobId) {
  await until(() => row(s, jobId) !== null, `row for job ${jobId}`);
  const r = row(s, jobId);
  if (!r.classList.contains("open")) r.querySelector("button").dispatchEvent(fakeClickEvent());
  await until(() => !/Loading log/.test(row(s, jobId).querySelector(".log").textContent) && row(s, jobId).querySelector(".log").children.length > 0, `log for job ${jobId}`);
  return row(s, jobId);
}

test("MUTATION PIN (title order + live names): no vault name -> owned name, painted when the relay answers, before any further games poll", async () => {
  let release;
  server.ownedGate = new Promise((r) => (release = r));
  try {
    server.games = [
      { appid: 10, name: "Vault Ten", status: "done" },
      { appid: 20, name: null, status: "error" }, // queued from an owned-only row: apps.name is NULL
      { appid: 30, name: "  ", status: "error" }, // blank counts as missing
    ];
    server.jobs = [job(1, 10, "done"), job(2, 20, "error"), job(3, 30, "error"), job(4, 40, "error")];
    store.start();
    const s = renderDownloads();
    await until(() => count(RELAY) === 1, "the one owned-list load started (held at the relay)");
    await until(() => historyNames(s).length === 4, "rows");
    assert.deepEqual(historyNames(s), ["Vault Ten", "App 20", "App 30", "App 40"], "before the list lands");
    // Job 2 (not_logged_in) expanded while the list is still held: its
    // Retry label is painted with the fallback title first.
    await expand(s, 2);
    const retryLabel = () => retryButtons(row(s, 2))[0].getAttribute("aria-label");
    assert.equal(retryLabel(), "Retry App 20", "label before the list lands");
    const gamesCallsAtRelease = count("/v1/games");
    release();
    await until(() => historyNames(s).includes("Owned Twenty"), "owned name painted from the owned-list subscription");
    assert.equal(count("/v1/games"), gamesCallsAtRelease, "painted by the subscription, not by a games poll");
    assert.equal(retryLabel(), "Retry Owned Twenty", "MUTATION PIN: patchNames updates the Retry aria-label too");
    assert.deepEqual(historyNames(s), ["Vault Ten", "Owned Twenty", "App 30", "App 40"]);
  } finally {
    server.ownedGate = null;
    if (release) release();
    store.stop();
  }
});

test("MUTATION PIN (no relay polling): /downloads opened directly starts exactly ONE owned-list load, never another", async () => {
  try {
    assert.equal(count(RELAY), 1, "the one load the previous test's missing name started");
    store.start();
    await pollTicks(4);
    const s = renderDownloads();
    await until(() => historyNames(s).length === 4, "rows after re-render");
    await tick(20);
    assert.equal(count(RELAY), 1, "poll ticks and re-renders never re-fetch the owned list");
  } finally {
    store.stop();
  }
});

test("not_logged_in: login block with the command at the top, raw output in a CLOSED <details>, Retry queues the app", async () => {
  try {
    store.start();
    const s = renderDownloads();
    const r = await expand(s, 2);
    const log = r.querySelector(".log");
    assert.equal(log.children[0].className, "failhint", "the block comes FIRST");
    const hint = r.querySelector(".failhint");
    assert.equal(hint.dataset.hint, "not_logged_in");
    assert.equal(hint.querySelector(".failhint-title").textContent, "Steam login missing");
    assert.match(hint.textContent, /one-time interactive login on the server/);
    assert.match(hint.textContent, /never sees or stores your Steam credentials/);
    assert.match(hint.textContent, /In the folder with SteamHangar's compose\.yaml \(deploy\/ by default\)/);
    assert.match(hint.textContent, /deploy\/README\.md/);
    assert.match(hint.textContent, /Then press Retry\./);
    assert.equal(hint.querySelector("code.cmd").textContent, "docker compose exec -it vault-runner /opt/steamprefill/SteamPrefill select-apps");

    const raw = r.querySelector("details.rawlog");
    assert.equal(raw.open, false, "closed by default");
    assert.equal(raw.querySelector("summary").textContent, "Show the full SteamPrefill output");
    assert.match(raw.textContent, /InvalidOperationException/, "the raw output lives INSIDE the details");
    assert.doesNotMatch(hint.textContent, /InvalidOperationException/, "and not in the hint block");

    const [retry] = retryButtons(hint);
    assert.equal(retry === undefined, false, "a Retry button");
    assert.notEqual(retry.disabled, true, "a live Retry button");
    assert.equal(retry.getAttribute("aria-label"), "Retry Owned Twenty", "names the game for screen readers");
    retry.dispatchEvent(fakeClickEvent());
    await until(() => server.prefillBodies.length === 1, "Retry POSTs /v1/prefill");
    assert.deepEqual(server.prefillBodies[0], { appids: [20] });
  } finally {
    store.stop();
  }
});

test("MUTATION PIN (rawOpen): an opened <details> stays open across a full rebuild caused by ANOTHER job's transition", async () => {
  try {
    store.start();
    const s = renderDownloads();
    const r = await expand(s, 2);
    const raw = r.querySelector("details.rawlog");
    raw.open = true; // the user clicks the summary
    fireToggle(raw);
    server.jobs = [...server.jobs, job(5, 50, "queued")]; // a transition elsewhere -> full rebuild
    store.refreshNow();
    await until(() => row(s, 2) !== r, "the history section was rebuilt");
    assert.equal(s.querySelectorAll(".qrow").map((q) => q.dataset.jid).join(), "5", "rebuilt by job 5's arrival");
    assert.equal(row(s, 2).querySelector("details.rawlog").open, true, "still open after the rebuild");
  } finally {
    server.jobs = server.jobs.filter((j) => j.id !== 5);
    store.stop();
  }
});

test("MUTATION PIN (offline gate): Retry is disabled with the offline title while the connection is lost, live again after", async () => {
  try {
    store.start();
    const s = renderDownloads();
    await expand(s, 2);
    setConnectionLost(true);
    let [retry] = retryButtons(row(s, 2));
    assert.equal(retry.disabled, true, "disabled while offline");
    assert.equal(retry.title, OFFLINE_CONTROL_TITLE);
    setConnectionLost(false);
    [retry] = retryButtons(row(s, 2));
    assert.notEqual(retry.disabled, true, "live again once restored");
  } finally {
    setConnectionLost(false);
    store.stop();
  }
});

test("MUTATION PIN (newest only): an older failed job for an app with a newer prefill job shows no Retry", async () => {
  try {
    server.jobs = [...server.jobs, job(6, 20, "queued")];
    store.start();
    const s = renderDownloads();
    await until(() => s.querySelectorAll(".qrow").map((q) => q.dataset.jid).join() === "6", "only the newer job 6 is queued");
    const r = await expand(s, 2);
    assert.equal(retryButtons(r).length, 0, "no Retry on the older row");
    assert.equal(NEWER_JOB_LINE, "A newer job for this game exists (see above).");
    assert.equal(r.querySelector(".failhint").textContent.includes(NEWER_JOB_LINE), true, "the newer-job line instead");
    assert.doesNotMatch(r.querySelector(".failhint").textContent, /Then press Retry/);
  } finally {
    server.jobs = server.jobs.filter((j) => j.id !== 6);
    store.stop();
  }
});

test("another failure reason renders exactly as before: output straight in the log, no block, no details, no Retry", async () => {
  try {
    store.start();
    const s = renderDownloads();
    await until(() => row(s, 3) !== null, "row for job 3");
    row(s, 3).querySelector("button").dispatchEvent(fakeClickEvent());
    await until(() => /disk full/.test(row(s, 3).querySelector(".log").textContent), "exit_code output");
    const r = row(s, 3);
    assert.equal(r.querySelector(".failhint") === null, true, "no hint block for a plain exit_code failure");
    assert.equal(r.querySelector("details") === null, true, "no disclosure");
    assert.equal(r.querySelectorAll("button").length, 1, "only the row toggle");
    assert.equal(r.querySelector(".log").textContent, EXIT_CODE_EXCERPT);
  } finally {
    store.stop();
  }
});

test("public IP (exit_code + SteamPrefill's 'is resolving to a public IP'): cache hint, both fixes, README pointer, closed output, Retry", async () => {
  try {
    store.start();
    const s = renderDownloads();
    const r = await expand(s, 4);
    const hint = r.querySelector(".failhint");
    assert.equal(hint === null, false, "a hint block");
    assert.equal(hint.dataset.hint, "public_ip");
    assert.equal(r.querySelector(".log").children[0].className, "failhint", "the block comes FIRST");
    const text = hint.textContent;
    assert.match(text, /cannot find the cache/);
    assert.match(text, /lancache\.steamcontent\.com resolves to a public address/);
    assert.match(text, /vault-runner in the default queue mode/);
    assert.match(text, /extra_hosts on vault-runner to vault-core's private IPv4 address \(a plain IP, not a hostname;/);
    assert.match(text, /vault-api instead with VAULT_PREFILL_MODE=subprocess/);
    assert.match(text, /re-run docker compose -f compose\.yaml -f compose\.override\.yaml up -d/);
    assert.match(text, /DNS rewrite/);
    assert.equal(text.includes(PUBLIC_IP_README_SECTION), true, "points at the README section");
    assert.match(hint.querySelector("code.cmd").textContent, /extra_hosts:\n\s+- "lancache\.steamcontent\.com:<vault-core private IPv4>"/);
    const raw = r.querySelector("details.rawlog");
    assert.equal(raw.open, false, "closed by default");
    assert.match(raw.textContent, /is resolving to a public IP/);
    assert.equal(retryButtons(hint).length, 1, "Retry offered");
  } finally {
    store.stop();
  }
});
