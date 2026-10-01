/**
 * Settings view: the stored library SteamID64 (WP WEB-FEAT-1).
 *
 * The old "Library preview" lookup became the `steam_library_steamid`
 * setting (WP API-FEAT-1): input pre-filled from `GET /v1/settings`, its own
 * Save button, `PATCH /v1/settings` with the id as a JSON STRING (a JSON
 * number would already be rounded by JavaScript — 17 digits exceed
 * Number.MAX_SAFE_INTEGER — and the API answers it with 422), inline
 * validation errors, and the Preview lookup kept.
 *
 * Harness: same as settings-view-wiring.test.js (fake-dom, Map-backed
 * localStorage, routing fetch fake). The fake records the RAW request body
 * text, because the guarantee is about the bytes on the wire.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { createFakeDom } from "./fake-dom.js";
import { resetModalStack } from "../js/lib/modal-stack.js";

const dom = createFakeDom();
globalThis.document = dom.document;
const storage = new Map();
globalThis.window = {
  localStorage: {
    getItem: (k) => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => storage.set(k, String(v)),
  },
  location: { origin: "http://vault.test", pathname: "/settings", reload() {} },
  history: { pushState() {} },
  addEventListener() {},
  removeEventListener() {},
};

const STEAMID = "76561198042117903";
const OTHER_STEAMID = "76561197960287930";

const server = {
  steamid: "",
  envSteamid: "",
  source: "default",
  readonly: false,
  patchStatus: 200,
  patchBodies: [],
  ownedStatus: 200,
  ownedGames: [{ appid: 40, name: "Owned Forty" }],
};

function settingsBody() {
  const base = [
    ["vault_name", "vault-01"],
    ["schedule_window", null],
    ["schedule_interval_minutes", 180],
    ["schedule_client_stale_days", 14],
    ["sweep_include_cached", true],
    ["auto_gc", "execute"],
    ["webhook_url", ""],
    ["webhook_events", []],
  ].map(([key, effective]) => ({ key, effective, source: "default", fallback: effective, applies: "immediately", env_only: false }));
  base.push({
    key: "steam_library_steamid",
    effective: server.steamid,
    source: server.source,
    fallback: "",
    applies: "immediately",
    env_only: false,
  });
  return { readonly: server.readonly, server_version: "0.0.0-test", settings: base };
}
function respond(status, data) {
  const text = JSON.stringify(data);
  return { ok: status >= 200 && status < 300, status, json: async () => data, text: async () => text };
}
globalThis.fetch = async (url, init = {}) => {
  const u = new URL(String(url));
  const method = init.method || "GET";
  if (u.pathname === "/v1/settings" && method === "PATCH") {
    server.patchBodies.push(init.body);
    if (server.patchStatus !== 200) {
      return respond(server.patchStatus, { detail: "'steam_library_steamid': must be a SteamID64" });
    }
    const body = JSON.parse(init.body);
    if (typeof body.steam_library_steamid === "string") {
      server.steamid = body.steam_library_steamid.trim();
      server.source = "db";
    } else if (body.steam_library_steamid === null) {
      server.steamid = server.envSteamid;
      server.source = server.envSteamid ? "env" : "default";
    }
    return respond(200, settingsBody());
  }
  if (u.pathname === "/v1/settings") return respond(200, settingsBody());
  if (u.pathname === "/v1/steam/key") return respond(200, { configured: true, key_last4: "ABCD" });
  if (u.pathname === "/v1/steam/owned-games") {
    (server.previewIds ||= []).push(u.searchParams.get("steamid"));
    if (server.ownedStatus !== 200) return respond(server.ownedStatus, { detail: "relay says no" });
    return respond(200, { configured: true, game_count: server.ownedGames.length, games: server.ownedGames });
  }
  if (u.pathname === "/v1/steam/player-summaries") return respond(200, { configured: true, players: [] });
  return respond(404, { detail: "Not Found" });
};

// A real #toast pair so showToast() is observable (fake-dom's
// getElementById only knows #app).
const toastEl = dom.document.createElement("div");
const toastText = dom.document.createElement("span");
const baseGetById = dom.document.getElementById.bind(dom.document);
dom.document.getElementById = (id) => (id === "toast" ? toastEl : id === "toast-text" ? toastText : baseGetById(id));
const { initToast } = await import("../js/components/toast.js");
initToast();

const { renderSettings } = await import("../js/views/settings.js");

const tick = (ms = 5) => new Promise((r) => setTimeout(r, ms));
async function until(pred, label, timeoutMs = 2000) {
  const start = Date.now();
  while (!pred()) {
    if (Date.now() - start > timeoutMs) throw new Error(`timed out waiting for: ${label}`);
    await tick();
  }
}
async function render() {
  const section = renderSettings();
  await until(() => input(section) !== undefined, "settings loaded");
  return section;
}
/** Click and wait until the button's async handler has finished (it
 * disables the button for the duration of its request, if it sends one). */
async function click(btn, settled) {
  btn.dispatchEvent({ type: "click" });
  await until(settled, "click handled");
}
const input = (s) => s.querySelectorAll("input").find((i) => i.id === "settings-steam-steamid");
const saveBtn = (s) => s.querySelector('[data-role="steamid-save"]');
const previewBtn = (s) => s.querySelector('[data-role="steamid-preview"]');
const resetBtn = (s) => s.querySelector('[data-role="steamid-reset"]');
const errLine = (s) => s.querySelector('[data-role="steamid-error"]');
const errText = (s) => (errLine(s).hidden ? null : errLine(s).textContent);
const caption = (s) => s.querySelector('[data-role="steamid-caption"]').textContent;

beforeEach(() => {
  storage.clear();
  storage.set("steamvault.apiKey", "good");
  server.steamid = "";
  server.envSteamid = "";
  server.source = "default";
  server.readonly = false;
  server.patchStatus = 200;
  server.patchBodies = [];
  server.ownedStatus = 200;
  resetModalStack(dom.document);
});

test("the Steam section shows a 'Steam library' block, pre-filled from the stored setting (no 'Library preview' lookup any more)", async () => {
  server.steamid = STEAMID;
  server.source = "db";
  const s = await render();
  const headings = s.querySelectorAll("h4.sec").map((h) => h.textContent);
  assert.ok(headings.includes("Steam library"));
  assert.equal(headings.includes("Library preview"), false);
  assert.equal(input(s).value, STEAMID);
  assert.equal(caption(s), "Overridden here · Takes effect immediately.");
  assert.equal(saveBtn(s).hidden, false);
});

test("MUTATION PIN: Save sends the SteamID64 as a JSON STRING — the raw body carries the quoted digits", async () => {
  const s = await render();
  input(s).value = STEAMID;
  await click(saveBtn(s), () => server.patchBodies.length === 1 && !saveBtn(s).disabled);
  // Number("76561198042117903") would serialize as 76561198042117900 with no
  // quotes — a different account.
  assert.equal(server.patchBodies[0], `{"steam_library_steamid":"${STEAMID}"}`);
  assert.equal(typeof JSON.parse(server.patchBodies[0]).steam_library_steamid, "string");
  assert.equal(errText(s), null);
  assert.equal(caption(s), "Overridden here · Takes effect immediately.", "caption repainted from the PATCH response");
});

test("Save trims surrounding whitespace before sending", async () => {
  const s = await render();
  input(s).value = `  ${OTHER_STEAMID} `;
  await click(saveBtn(s), () => server.patchBodies.length === 1);
  assert.equal(server.patchBodies[0], `{"steam_library_steamid":"${OTHER_STEAMID}"}`);
});

test("an invalid SteamID64 is refused inline with the existing validation text — no PATCH is sent", async () => {
  const s = await render();
  for (const bad of ["7656119804211790", "76561198042117903x", "12345678901234567", "0x1100001"]) {
    input(s).value = bad;
    errLine(s).hidden = true;
    await click(saveBtn(s), () => errText(s) !== null);
    assert.equal(errText(s), "That does not look like a valid SteamID64 (17 digits).", bad);
  }
  assert.equal(server.patchBodies.length, 0);
});

test("a 422 from the server is shown inline (not just a toast), naming the server's reason", async () => {
  server.patchStatus = 422;
  const s = await render();
  input(s).value = STEAMID;
  await click(saveBtn(s), () => errText(s) !== null);
  assert.equal(server.patchBodies.length, 1);
  assert.match(errText(s), /^That does not look like a valid SteamID64 \(17 digits\)\. \('steam_library_steamid': must be a SteamID64\)$/);
});

test("an unchanged value sends nothing; a blank value clears the setting with an empty STRING", async () => {
  server.steamid = STEAMID;
  server.source = "db";
  const s = await render();
  toastText.textContent = "";
  await click(saveBtn(s), () => toastText.textContent === "SteamID64 unchanged.");
  assert.equal(server.patchBodies.length, 0, "same value as stored -> no PATCH");
  input(s).value = "";
  await click(saveBtn(s), () => server.patchBodies.length === 1);
  assert.equal(server.patchBodies[0], `{"steam_library_steamid":""}`);
});

test("MUTATION PIN: read-only settings: input stays ENABLED, Save/Reset hidden, and typing + Preview works", async () => {
  server.readonly = true;
  server.steamid = STEAMID;
  server.source = "db";
  const s = await render();
  assert.equal(input(s).disabled === true, false, "Preview needs a typeable input even when settings are read-only");
  assert.equal(saveBtn(s).hidden, true);
  assert.equal(resetBtn(s).hidden, true);
  input(s).value = OTHER_STEAMID;
  server.previewIds = [];
  await click(previewBtn(s), () => s.querySelectorAll("p.foot-note").some((p) => p.textContent === "1 games found."));
  assert.deepEqual(server.previewIds, [OTHER_STEAMID], "Preview looked up the TYPED id");
  assert.equal(server.patchBodies.length, 0);
});

test("Preview: 409 -> the shared no-key text, 422 -> the typed-id validation text, other errors stay the plain server error, 0 games -> private hint", async () => {
  server.steamid = STEAMID;
  const s = await render();
  const errlines = () => s.querySelectorAll("p.errline").map((p) => p.textContent);
  server.ownedStatus = 409;
  await click(previewBtn(s), () => errlines().includes("No Steam Web API key configured. Library queries answer 409 until one is set."));
  server.ownedStatus = 422;
  await click(previewBtn(s), () => errlines().includes("That does not look like a valid SteamID64 (17 digits)."));
  server.ownedStatus = 502;
  await click(previewBtn(s), () => errlines().includes("relay says no"));
  server.ownedStatus = 200;
  server.ownedGames = [];
  await click(previewBtn(s), () => s.querySelectorAll("p.foot-note").some((p) => /probably private/.test(p.textContent)));
  server.ownedGames = [{ appid: 40, name: "Owned Forty" }];
});

test("MUTATION PIN: Reset (shown only for a db override) sends null and lands on the env value; blank+Save stays an empty-string override", async () => {
  server.envSteamid = OTHER_STEAMID;
  server.steamid = OTHER_STEAMID;
  server.source = "env";
  const s = await render();
  assert.equal(resetBtn(s).hidden, true, "nothing to reset while the value comes from the environment");
  input(s).value = "";
  await click(saveBtn(s), () => server.patchBodies.length === 1 && !saveBtn(s).disabled);
  assert.equal(server.patchBodies[0], `{"steam_library_steamid":""}`, "blank + Save is the explicit '' override");
  assert.equal(resetBtn(s).hidden, false, "a db override can be reset");
  await click(resetBtn(s), () => server.patchBodies.length === 2 && !resetBtn(s).disabled);
  assert.equal(server.patchBodies[1], `{"steam_library_steamid":null}`);
  assert.equal(input(s).value, OTHER_STEAMID, "the input shows the env value again");
  assert.equal(caption(s), "From the environment · Takes effect immediately.");
  assert.equal(resetBtn(s).hidden, true);
});

test("an older vault-api without the setting: Save hidden, caption says so, Preview kept", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    const u = new URL(String(url));
    if (u.pathname === "/v1/settings" && (init?.method || "GET") === "GET") {
      const body = settingsBody();
      body.settings = body.settings.filter((e) => e.key !== "steam_library_steamid");
      return respond(200, body);
    }
    return originalFetch(url, init);
  };
  try {
    const s = await render();
    assert.equal(saveBtn(s).hidden, true);
    assert.equal(resetBtn(s).hidden, true);
    assert.equal(input(s).disabled === true, false, "Preview still needs the input");
    assert.match(caption(s), /does not store a library SteamID64/);
    assert.ok(previewBtn(s));
  } finally {
    globalThis.fetch = originalFetch;
  }
});
