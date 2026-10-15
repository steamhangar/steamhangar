/**
 * DOM-wiring pins for "Check updates" in `web/js/views/library.js` (WP
 * UPD-2). The decisions are pinned in update-check.test.js; this file proves
 * the view USES them against the real api.js -> fetch path: button
 * visibility per API generation, the POST, the busy state, polling to the
 * end, the games re-read, the badge, the "Updates" chip, the toast, and that
 * leaving the view stops polling.
 *
 * Harness as in library-owned-wiring.test.js (fake-dom, Map-backed
 * localStorage with an API key, routing fetch fake counting requests by
 * path). The fake server below follows the WP UPD-1 contract
 * final contract (api/README.md on wp/upd-1-update-check). The view polls
 * with the real 2 s interval, so the run-to-end tests take a few seconds.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, after } from "node:test";
import assert from "node:assert/strict";
import { createFakeDom } from "./fake-dom.js";

const dom = createFakeDom();
globalThis.document = dom.document;
// Toast nodes, so showToast() really writes somewhere this file can read.
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

function vaultGame(appid, name, overrides = {}) {
  return {
    appid,
    name,
    status: "done",
    last_prefill_at: "2026-09-01T00:00:00Z",
    last_manifest_check: null,
    depot_count: 1,
    size_bytes: 2_000_000_000,
    needs_force: false,
    installed_on: [],
    update_available: false,
    update_checked_at: "2026-10-13T08:00:00Z",
    // WP CACHE-COMPLETE-1c: measured complete, so a cached card is "Current".
    cache_complete_pct: 100,
    ...overrides,
  };
}

const IDLE = {
  enabled: true,
  scheduled: false,
  running: false,
  last_trigger: "scheduled",
  last_started_at: "2026-10-13T08:00:00Z",
  last_finished_at: "2026-10-13T08:00:30Z",
  last_outcome: "completed",
  last_apps: 2,
  last_ok: 2,
  last_failed: 0,
  last_stale: 1,
};
const RUNNING = {
  ...IDLE,
  running: true,
  last_trigger: "manual",
  last_finished_at: null,
  last_outcome: null,
  last_apps: null,
  last_ok: null,
  last_failed: null,
  last_stale: null,
};

const server = {
  // "ok" = UPD-1 vault-api, "old" = no routes and no fields
  mode: "ok",
  enabled: true,
  games: [vaultGame(10, "Vault Ten", { update_available: true }), vaultGame(30, "Vault Thirty")],
  // what GET /v1/updates/status answers next; the last entry repeats
  statusQueue: [IDLE],
  // applied to the games on the first status read with running=false after a POST
  afterRun: null,
  calls: new Map(),
};
function count(path) {
  return server.calls.get(path) || 0;
}
function respond(status, data) {
  const text = data === undefined ? "" : JSON.stringify(data);
  return { ok: status >= 200 && status < 300, status, json: async () => data, text: async () => text };
}
const strip = (g) => {
  const { update_available, update_checked_at, ...rest } = g;
  return rest;
};
globalThis.fetch = async (url, init = {}) => {
  const u = new URL(String(url));
  const p = u.pathname;
  const method = init.method || "GET";
  server.calls.set(`${method} ${p}`, count(`${method} ${p}`) + 1);
  if (p === "/v1/games") return respond(200, server.mode === "ok" ? server.games : server.games.map(strip));
  if (p === "/v1/jobs") return respond(200, []);
  if (p === "/v1/clients") return respond(200, []);
  if (p === "/v1/cache/summary") return respond(200, { total_bytes: 0, top_consumers: [] });
  if (p === "/v1/settings") {
    return respond(200, {
      readonly: false,
      settings: [{ key: "steam_library_steamid", effective: "", source: "db", fallback: "", applies: "immediately", env_only: false }],
    });
  }
  if (server.mode === "ok" && p === "/v1/updates/check" && method === "POST") {
    if (init.body !== undefined) throw new Error("POST /v1/updates/check must not send a body");
    if (!server.enabled) return respond(200, { status: "disabled", enabled: false });
    return respond(202, { status: "started", enabled: true });
  }
  if (server.mode === "ok" && p === "/v1/updates/status" && method === "GET") {
    const s = server.statusQueue.length > 1 ? server.statusQueue.shift() : server.statusQueue[0];
    if (!s.running && server.afterRun) {
      server.afterRun();
      server.afterRun = null;
    }
    return respond(200, { ...s, enabled: server.enabled });
  }
  return respond(404, { detail: "Not Found" });
};

const { renderLibrary } = await import("../js/views/library.js");
const { store } = await import("../js/store-singleton.js");
const { navigateTo } = await import("../js/router.js");
const { initToast } = await import("../js/components/toast.js");
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

const updBtn = (s) => s.querySelector('button.btn[data-action="check-updates"]');
const updLine = (s) => s.querySelector("p.lib-upd");
const chipKeys = (s) => s.querySelectorAll(".chip").map((c) => c.dataset.f);
const chip = (s, key) => s.querySelector(`.chip[data-f="${key}"]`);
// fake-dom has no bubbling/closest: dispatch on the delegating container.
const pressChip = (s, key) => {
  const el = chip(s, key);
  s.querySelector(".chips").dispatchEvent({ type: "click", target: { closest: () => el } });
};
const card = (s, appid) => s.querySelector(`.card[data-appid="${appid}"]`);
const statusReads = () => count("GET /v1/updates/status");
// WP UPD-6: a flagged cached card says "Update available" as its status
// word with the orange refresh glyph; the separate .updbadge is gone there.
const stateWord = (s, appid) => card(s, appid).querySelector(".state").textContent;
const pillKind = (s, appid) => card(s, appid).querySelector(".cappill .sic").className;

async function openLibrary() {
  pathname = "/library";
  navigateTo("library");
  const section = renderLibrary();
  await until(() => section.querySelectorAll(".card").length >= server.games.length, "cards on screen");
  return section;
}

test("UPD-1 vault-api: the button shows next to the unchanged 'Check & update' button, with the 'last checked' line", async () => {
  server.mode = "ok";
  server.enabled = true;
  server.statusQueue = [IDLE];
  const s = await openLibrary();
  await until(() => updBtn(s).hidden === false, "button shown after the status read");
  const btns = s.querySelector(".lib-checkbtns").querySelectorAll("button").map((b) => b.textContent);
  assert.deepEqual(btns, ["Check updates", "Check & update all cached games"]);
  assert.equal(updBtn(s).disabled, false);
  assert.match(updLine(s).textContent, /^Updates last checked .* · manual checks only$/);
  assert.equal(updLine(s).hidden, false);
  assert.deepEqual(
    s.children.map((c) => c.className),
    ["lib-head", "lib-bar", "lib-status", "grid", "hint", "bulk"],
    "toolbar row, then the status lines (WP WEB-FIX-UPD-VIS)",
  );
  const statuses = s.querySelectorAll('[role="status"]').map((n) => n.className);
  assert.deepEqual(statuses, ["lib-owned-text"], "no new live region: results go to the toast");
});

test("WP WEB-FIX-UPD-VIS: one toolbar row — search, chips, then the buttons; 'Check updates' is the primary button, 'Check & update' the plain one", async () => {
  server.mode = "ok";
  server.enabled = true;
  server.statusQueue = [IDLE];
  const s = await openLibrary();
  await until(() => updBtn(s).hidden === false, "button shown");
  const bar = s.querySelector(".lib-bar");
  assert.deepEqual(bar.children.map((c) => c.className), ["lib-filters", "lib-checkbtns"]);
  assert.deepEqual(bar.children[0].children.map((c) => c.className.split(" ")[0]), ["search", "chips"]);
  const [first, second] = s.querySelector(".lib-checkbtns").children;
  assert.equal(first.textContent, "Check updates");
  assert.deepEqual(first.className.split(" ").sort(), ["btn", "primary", "sm"], "MUTATION PIN: the check-only button is the primary one");
  assert.equal(second.textContent, "Check & update all cached games");
  assert.deepEqual(second.className.split(" ").sort(), ["btn", "sm"], "MUTATION PIN: the downloading button is NOT primary and not a see-through ghost");
  assert.equal(s.querySelector(".lib-status").children.map((c) => c.className).join(" "), "lib-upd lib-owned lib-meta");
});

test("badge + 'Updates' chip from update_available; the chip filters the grid", async () => {
  server.mode = "ok";
  const s = await openLibrary();
  assert.equal(stateWord(s, 10), "Update available", "state on the flagged game");
  assert.match(pillKind(s, 10), /\bk-stale\b/);
  assert.equal(card(s, 10).querySelector(".updbadge"), null, "WP UPD-6: no second 'Update' line");
  assert.equal(stateWord(s, 30), "Current");
  assert.match(pillKind(s, 30), /\bk-cached\b/);
  assert.ok(chipKeys(s).includes("updates"));
  assert.equal(chip(s, "updates").querySelector(".n").textContent, "1");
  pressChip(s, "updates");
  assert.deepEqual(s.querySelectorAll(".card").map((c) => Number(c.dataset.appid)), [10]);
  pressChip(s, "all");
});

test("press: POST once (no body), busy while running, polls to the end, re-reads games, badge/chip follow, result toast", async () => {
  server.mode = "ok";
  server.enabled = true;
  server.statusQueue = [IDLE];
  const s = await openLibrary();
  await until(() => updBtn(s).hidden === false, "button shown");
  server.statusQueue = [RUNNING, { ...IDLE, last_trigger: "manual", last_apps: 2, last_stale: 2 }];
  server.afterRun = () => {
    server.games = server.games.map((g) => ({ ...g, update_available: true }));
  };
  const gamesBefore = count("GET /v1/games");
  updBtn(s).focus();
  updBtn(s).dispatchEvent({ type: "click" });
  await until(() => count("POST /v1/updates/check") === 1, "POST sent");
  await until(() => /Checking cached games/.test(updLine(s).textContent), "running line");
  assert.equal(updBtn(s).getAttribute("aria-disabled"), "true");
  assert.equal(updBtn(s).disabled === true, false, "MUTATION PIN: never `disabled` while busy — that drops keyboard focus");
  assert.equal(updBtn(s).textContent, "Checking for updates…");
  updBtn(s).dispatchEvent({ type: "click" });
  await tick(20);
  assert.equal(count("POST /v1/updates/check"), 1, "a click while running sends nothing");
  assert.equal(dom.document.activeElement === updBtn(s), true, "focus stays on the button while busy");
  await until(() => updBtn(s).getAttribute("aria-disabled") === "false", "finished", 8000);
  assert.equal(updBtn(s).textContent, "Check updates");
  assert.match(toastText.textContent, /Checked 2 games · 2 updates available/);
  await until(() => count("GET /v1/games") > gamesBefore, "games re-read after the run");
  await until(() => chip(s, "updates").querySelector(".n").textContent === "2", "chip count follows the new verdicts");
  await until(() => stateWord(s, 30) === "Update available", "state appeared on the newly flagged game");
  assert.match(pillKind(s, 30), /\bk-stale\b/);
  const readsAfter = statusReads();
  await tick(2300);
  assert.equal(statusReads(), readsAfter, "MUTATION PIN: no polling after running=false");
});

test("MUTATION PIN (detach): leaving Library while a check runs stops the status polling", async () => {
  server.mode = "ok";
  server.enabled = true;
  server.statusQueue = [IDLE];
  const s = await openLibrary();
  await until(() => updBtn(s).hidden === false, "button shown");
  server.statusQueue = [RUNNING];
  updBtn(s).dispatchEvent({ type: "click" });
  await until(() => updBtn(s).getAttribute("aria-disabled") === "true", "busy");
  navigateTo("downloads");
  const reads = statusReads();
  await tick(2300);
  assert.equal(statusReads(), reads, "no status read while another view is shown");
  // Back on Library: one read, and polling resumes for the still-running check.
  server.statusQueue = [RUNNING, IDLE];
  const s2 = await openLibrary();
  await until(() => updBtn(s2).getAttribute("aria-disabled") === "false", "finished after resume", 8000);
});

test("oracle off: button visible but disabled, the line says why", async () => {
  server.mode = "ok";
  server.enabled = false;
  server.statusQueue = [IDLE];
  const s = await openLibrary();
  await until(() => updBtn(s).hidden === false, "button shown");
  assert.equal(updBtn(s).disabled, true);
  assert.match(updLine(s).textContent, /manifest oracle is disabled/);
  server.enabled = true;
});

test("older vault-api (404, no update fields): no button, no line, no 'Updates' chip, no badge — and the old button still works", async () => {
  server.mode = "old";
  const s = await openLibrary();
  await until(() => statusReads() > 0 && updBtn(s).hidden === true, "probe answered");
  // The grid still holds the previous tick's rows; the next games poll
  // brings the field-less ones.
  store.refreshNow();
  await until(() => !chipKeys(s).includes("updates"), "games re-read without the field");
  await tick(30);
  assert.equal(updBtn(s).hidden, true);
  assert.equal(updLine(s).hidden, true);
  assert.equal(chipKeys(s).includes("updates"), false);
  assert.equal(s.querySelectorAll(".updbadge").length, 0);
  const oldBtn = s.querySelector(".lib-checkbtns").querySelectorAll("button").find((b) => b.textContent === "Check & update all cached games");
  assert.ok(oldBtn, "'Check & update all cached games' unchanged");
  assert.equal(oldBtn.hidden === true, false);
  server.mode = "ok";
});

test("an 'Updates' filter whose chip vanished (API downgrade) falls back to 'All' instead of an empty grid", async () => {
  server.mode = "ok";
  server.games = [vaultGame(10, "Vault Ten", { update_available: true }), vaultGame(30, "Vault Thirty")];
  const s = await openLibrary();
  store.refreshNow();
  await until(() => chip(s, "updates") !== null && chip(s, "updates").querySelector(".n").textContent === "1", "chip with the fresh rows");
  pressChip(s, "updates");
  assert.equal(chip(s, "updates").getAttribute("aria-pressed"), "true", "the filter really is active");
  assert.deepEqual(s.querySelectorAll(".card").map((c) => Number(c.dataset.appid)), [10], "only the flagged card");
  server.mode = "old";
  store.refreshNow();
  await until(() => !chipKeys(s).includes("updates"), "chip gone");
  assert.deepEqual(
    s.querySelectorAll(".card").map((c) => Number(c.dataset.appid)).sort((a, b) => a - b),
    [10, 30],
    "MUTATION PIN: back to every card, not an empty grid filtered by an invisible chip",
  );
  assert.equal(chip(s, "all").getAttribute("aria-pressed"), "true");
  server.mode = "ok";
});
