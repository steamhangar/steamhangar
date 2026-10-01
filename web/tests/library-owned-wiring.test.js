/**
 * DOM-wiring pins for the owned Steam library in `web/js/views/library.js`
 * (WP WEB-FEAT-1). The pure merge/header/error logic is pinned in
 * `owned-library.test.js`; this file proves the view actually USES it
 * (LEARNINGS 2026-08-22: pinning the model layer proves nothing about the
 * pixels).
 *
 * Harness: `fake-dom.js`, a Map-backed `localStorage` with a stored API key
 * (so the real store-singleton starts its poll loops against the fake
 * server), and a routing `fetch` fake that counts every request by path.
 * The real `api.js` -> fetch path is exercised end to end. The store is
 * stopped in `after()` so a broken gate cannot hang the suite
 * (LEARNINGS WEB-FIX-1).
 *
 * Assertions compare text and counts, never nodes (a failed node assertion
 * dumps the fake-DOM graph and OOM-killed node once, LEARNINGS WEB-FIX-1).
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

const STEAMID = "76561198042117903";

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
    ...overrides,
  };
}

const server = {
  steamid: "",
  vaultGames: [
    vaultGame(10, "Vault Ten"),
    vaultGame(30, "Vault Thirty"),
    vaultGame(20, "Vault Twenty", { status: "idle", size_bytes: null, last_prefill_at: null }),
  ],
  owned: { status: 200, data: { configured: true, game_count: 3, games: [{ appid: 10, name: "Ten" }, { appid: 40, name: "Owned Forty" }, { appid: 50, name: "Owned Fifty" }] } },
  calls: new Map(),
  prefillBodies: [],
};
function count(path) {
  return server.calls.get(path) || 0;
}
function respond(status, data) {
  const text = data === undefined ? "" : JSON.stringify(data);
  return { ok: status >= 200 && status < 300, status, json: async () => data, text: async () => text };
}
globalThis.fetch = async (url, init = {}) => {
  const u = new URL(String(url));
  const p = u.pathname;
  server.calls.set(p, count(p) + 1);
  const method = init.method || "GET";
  if (p === "/v1/games") return respond(200, server.vaultGames);
  if (p === "/v1/jobs") return respond(200, []);
  if (p === "/v1/clients") return respond(200, []);
  if (p === "/v1/cache/summary") return respond(200, { total_bytes: 0, top_consumers: [] });
  if (p === "/v1/mapping") return respond(200, []);
  if (p === "/v1/settings") {
    return respond(200, {
      readonly: false,
      settings: [{ key: "steam_library_steamid", effective: server.steamid, source: "db", fallback: "", applies: "immediately", env_only: false }],
    });
  }
  if (p === "/v1/steam/owned-games") {
    assert.equal(u.searchParams.get("steamid"), server.steamid, "the relay is asked for the STORED steamid");
    if (server.ownedGate) await server.ownedGate;
    return respond(server.owned.status, server.owned.data);
  }
  if (p === "/v1/prefill" && method === "POST") {
    const body = JSON.parse(init.body);
    server.prefillBodies.push(body);
    return respond(200, body.appids.map((appid) => ({ appid, job_id: 1, status: "queued", deduplicated: false })));
  }
  if (/^\/v1\/games\/\d+$/.test(p)) {
    const appid = Number(p.split("/").pop());
    const g = server.vaultGames.find((x) => x.appid === appid);
    return g ? respond(200, { ...g, depots: [] }) : respond(404, { detail: "Unknown appid" });
  }
  return respond(404, { detail: "Not Found" });
};

// library.js first: it imports store-singleton.js itself, so its games/jobs
// subscriptions exist before the first poll resolves (same order as app.js).
const { renderLibrary } = await import("../js/views/library.js");
const { store } = await import("../js/store-singleton.js");

after(() => store.stop());

const tick = (ms = 15) => new Promise((r) => setTimeout(r, ms));
async function until(pred, label, timeoutMs = 2000) {
  const start = Date.now();
  while (!pred()) {
    if (Date.now() - start > timeoutMs) throw new Error(`timed out waiting for: ${label}`);
    await tick(5);
  }
}

const subText = (s) => s.querySelector(".lib-sub").textContent;
const noticeText = (s) => {
  const p = s.querySelector("p.lib-owned");
  const span = p && p.querySelector(".lib-owned-text");
  // Pinned on every read: the live-region span is never hidden itself.
  assert.equal(span.hidden === true, false, "the live-region span must never be hidden; hide the outer <p> only");
  return p && !p.hidden ? span.textContent : "";
};
const cardAppids = (s) => s.querySelectorAll(".card").map((c) => Number(c.dataset.appid));
const card = (s, appid) => s.querySelector(`.card[data-appid="${appid}"]`);

async function openLibrary() {
  const section = renderLibrary();
  await until(() => cardAppids(section).length >= server.vaultGames.length, "vault cards on screen");
  return section;
}

test("no SteamID set: header counts the vault's games (never 'owned'), a hint points at Settings, the relay is never called", async () => {
  server.steamid = "";
  const before = count("/v1/steam/owned-games");
  const s = await openLibrary();
  await until(() => /Settings/.test(noticeText(s)), "settings hint");
  assert.equal(subText(s), "3 games on the vault · 2 on the cache");
  assert.match(noticeText(s), /Set your SteamID64 in Settings/);
  const settingsBtn = s.querySelector('button.linkbtn[data-action="settings"]');
  assert.equal(settingsBtn.hidden, false, "an 'Open Settings' button");
  assert.equal(settingsBtn.textContent, "Open Settings");
  assert.equal(s.querySelector('button.linkbtn[data-action="reload"]').hidden, true, "nothing to reload without a SteamID");
  assert.equal(count("/v1/steam/owned-games"), before, "no SteamID -> no relay call");
  assert.deepEqual(cardAppids(s).sort(), [10, 20, 30]);
});

test("MUTATION PIN (old header): with the SteamID set the header reads 'N owned · M on the cache' and owned-only games join the grid", async () => {
  server.steamid = STEAMID;
  const s = await openLibrary();
  await until(() => cardAppids(s).length === 5, "merged grid");
  // 3 owned (10, 40, 50); 2 cached (10 and 30 — 30 is on the cache, not owned).
  // The pre-WP code printed `${games.length} owned` = "5 owned".
  assert.equal(subText(s), "3 owned · 2 on the cache");
  assert.deepEqual(cardAppids(s).sort((a, b) => a - b), [10, 20, 30, 40, 50], "deduped by appid: 10 appears once");
});

test("an owned-only card shows 'Not cached' and has NO quick-action button; a vault 'not cached' card keeps its download pill", async () => {
  server.steamid = STEAMID;
  const s = await openLibrary();
  await until(() => card(s, 40) !== null, "owned-only card");
  const forty = card(s, 40);
  assert.equal(forty.querySelector(".state").textContent, "Not cached");
  assert.equal(forty.querySelector(".name").textContent, "Owned Forty");
  assert.equal(forty.querySelectorAll("button.cappill").length, 0, "no card quick action for an owned-only game");
  assert.equal(forty.querySelectorAll("button.icnact").length, 0);
  assert.equal(card(s, 20).querySelectorAll("button.cappill").length, 1, "vault-known not-cached card unchanged");
});

test("MUTATION PIN (no relay polling): poll ticks never re-fetch the owned list; only view open and Reload do", async () => {
  server.steamid = STEAMID;
  const s = await openLibrary();
  await until(() => cardAppids(s).length === 5, "merged grid");
  const afterOpen = count("/v1/steam/owned-games");
  const gamesBefore = count("/v1/games");
  for (let i = 0; i < 4; i++) {
    store.refreshNow();
    await tick(20);
  }
  await until(() => count("/v1/games") >= gamesBefore + 2, "games poll really ticked");
  assert.equal(count("/v1/steam/owned-games"), afterOpen, "a games/jobs tick must not call the relay");
  assert.equal(cardAppids(s).length, 5, "owned-only cards survive a games tick (re-merged, not dropped)");

  const reload = s.querySelector('button.linkbtn[data-action="reload"]');
  assert.ok(reload, "a Reload Steam library button");
  assert.equal(reload.textContent, "Reload Steam library");
  reload.dispatchEvent({ type: "click" });
  await until(() => count("/v1/steam/owned-games") === afterOpen + 1, "reload fetch");
});

test("relay 409: vault games still show, the notice carries the existing no-key text, the header falls back to vault counts", async () => {
  server.steamid = STEAMID;
  server.owned = { status: 409, data: { detail: "The Steam Web API relay is not configured." } };
  try {
    const s = await openLibrary();
    await until(() => /Showing vault games only/.test(noticeText(s)), "error notice");
    assert.deepEqual(cardAppids(s).sort(), [10, 20, 30], "the failure must not empty the library");
    assert.equal(noticeText(s), "No Steam Web API key configured. Library queries answer 409 until one is set. Showing vault games only.");
    assert.equal(subText(s), "3 games on the vault · 2 on the cache");
  } finally {
    server.owned = { status: 200, data: { configured: true, game_count: 3, games: [{ appid: 10, name: "Ten" }, { appid: 40, name: "Owned Forty" }, { appid: 50, name: "Owned Fifty" }] } };
  }
});

test("MUTATION PIN: relay 0 games -> the private-profile hint, and the header falls back to vault wording (never '0 owned')", async () => {
  server.steamid = STEAMID;
  server.owned = { status: 200, data: { configured: true, game_count: 0, games: [] } };
  try {
    const s = await openLibrary();
    await until(() => /probably private/.test(noticeText(s)), "private hint");
    assert.equal(subText(s), "3 games on the vault · 2 on the cache");
    assert.deepEqual(cardAppids(s).sort(), [10, 20, 30]);
  } finally {
    server.owned = { status: 200, data: { configured: true, game_count: 3, games: [{ appid: 10, name: "Ten" }, { appid: 40, name: "Owned Forty" }, { appid: 50, name: "Owned Fifty" }] } };
  }
});

test("detail-only download: opening an owned-only card offers 'Download to cache', which queues exactly that appid", async () => {
  server.steamid = STEAMID;
  const s = await openLibrary();
  await until(() => card(s, 40) !== null, "owned-only card");
  card(s, 40).dispatchEvent({ type: "click" });
  const sheetButton = () =>
    dom.document.body.querySelectorAll("button").find((b) => b.textContent === "Download to cache") || null;
  await until(() => sheetButton() !== null, "detail sheet's download button");
  assert.equal(count("/v1/games/40") >= 1, true, "the sheet asked the vault about the game (404 -> not tracked)");
  const before = server.prefillBodies.length;
  sheetButton().dispatchEvent({ type: "click" });
  await until(() => server.prefillBodies.length === before + 1, "prefill POST");
  assert.deepEqual(server.prefillBodies.at(-1), { appids: [40] });
});

test("layout: the notice lives inside .lib-checkrow — .view-library keeps its six in-flow children for the BP-L area grid", async () => {
  const s = await openLibrary();
  assert.deepEqual(
    s.children.map((c) => c.className),
    ["lib-head", "lib-checkrow", "search", "chips", "grid", "hint", "bulk"],
  );
  const row = s.querySelector(".lib-checkrow");
  assert.equal(row.querySelectorAll("p.lib-owned").length, 1);
});

test("css: .lib-owned wraps anywhere and sets no width (WP WEB-FIX-3 phone rules)", async () => {
  const { readFileSync } = await import("node:fs");
  const css = readFileSync(new URL("../css/app.css", import.meta.url), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
  const m = /(^|\})\s*\.lib-owned\s*\{([^}]*)\}/.exec(css);
  assert.ok(m, ".lib-owned rule missing");
  assert.match(m[2], /overflow-wrap:\s*anywhere/);
  assert.match(m[2], /min-width:\s*0/);
  assert.doesNotMatch(m[2], /(^|[;\s])width:/);
});

test("the role=status notice is not rebuilt by an unrelated full render (a search keystroke) — no repeated announcements", async () => {
  server.steamid = "";
  const s = await openLibrary();
  await until(() => /Settings/.test(noticeText(s)), "settings hint");
  const note = s.querySelector("p.lib-owned");
  const textSpan = note.querySelector(".lib-owned-text");
  textSpan.firstChild.__marker = "kept";
  const search = s.querySelectorAll("input").find((i) => i.id === "lib-q");
  search.value = "vault";
  search.dispatchEvent({ type: "input", target: search });
  assert.equal(s.querySelectorAll(".card").length, 3, "the search really re-rendered the grid");
  assert.equal(textSpan.firstChild.__marker === "kept", true, "the live-region text was not rewritten by the re-render");
  search.value = "";
  search.dispatchEvent({ type: "input", target: search });
});

test("MUTATION PIN (focus): the SAME Reload button node survives its own reload cycle, keeps focus, and is aria-disabled while loading", async () => {
  server.steamid = STEAMID;
  const s = await openLibrary();
  await until(() => cardAppids(s).length === 5, "merged grid");
  const btn = s.querySelector('button.linkbtn[data-action="reload"]');
  assert.equal(btn.hidden, false);
  assert.equal(btn.getAttribute("aria-disabled"), "false");
  btn.__marker = "same-node";
  btn.focus();
  let release;
  server.ownedGate = new Promise((r) => (release = r));
  const before = count("/v1/steam/owned-games");
  try {
    btn.dispatchEvent({ type: "click" });
    await until(() => count("/v1/steam/owned-games") === before + 1, "reload request in flight");
    const during = s.querySelector('button.linkbtn[data-action="reload"]');
    assert.equal(during.__marker === "same-node", true, "the button was not rebuilt when loading started");
    assert.equal(during.getAttribute("aria-disabled"), "true", "busy while loading");
    assert.equal(during.disabled === true, false, "never `disabled` — that would drop focus");
    assert.equal(dom.document.activeElement === btn, true, "focus stays on the button");
    during.dispatchEvent({ type: "click" });
    await tick(20);
    assert.equal(count("/v1/steam/owned-games"), before + 1, "a click while loading starts no second request");
  } finally {
    server.ownedGate = null;
    release();
  }
  await until(() => btn.getAttribute("aria-disabled") === "false", "reload finished");
  assert.equal(s.querySelector(".lib-owned-text").hidden === true, false, "span visible (empty) when there is no text");
  const afterward = s.querySelector('button.linkbtn[data-action="reload"]');
  assert.equal(afterward.__marker === "same-node", true, "still the same node after the reload landed");
  assert.equal(dom.document.activeElement === btn, true);
  const status = s.querySelectorAll('[role="status"]').map((n) => n.className);
  assert.deepEqual(status, ["lib-owned-text"], "the live region is the text span only, never the buttons");
});
