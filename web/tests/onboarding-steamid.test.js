/**
 * Onboarding step 2 stores the SteamID64 it looks up (WP WEB-FEAT-2).
 *
 * Before this WP "Look up" only previewed the owned list; the Library's
 * owned games need the `steam_library_steamid` setting, which only Settings
 * wrote. Pinned here, on the wire:
 *   - a lookup the relay answered PATCHes the id as a JSON STRING (the raw
 *     request body text is checked, a number would already be rounded);
 *   - 409 / 422 / any other relay failure sends no PATCH and shows the
 *     shared strings;
 *   - 0 games (private profile) still saves and shows the private hint;
 *   - read-only settings or an older vault-api without the setting send no
 *     PATCH and say "Not saved";
 *   - the input is pre-filled from the stored value;
 *   - review round (FAIL on test gaps): a failed PATCH (500, 422) says
 *     "Not saved" and keeps the old snapshot; a double click runs one
 *     lookup; the id saved is the one looked up, not an edit made while
 *     the request ran; "Go to library" waits for a running save; a later
 *     failed lookup clears an earlier "Saved".
 *
 * Harness: same as onboarding-wiring.test.js (fake-dom, Map-backed
 * localStorage, routing fetch fake recording every call), with `until()`
 * polling instead of fixed sleeps.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { createFakeDom } from "./fake-dom.js";
import { resetModalStack } from "../js/lib/modal-stack.js";
import {
  INVALID_STEAMID64_MESSAGE,
  LIBRARY_SETTING_ENV_ONLY_MESSAGE,
  LIBRARY_SETTING_ABSENT_MESSAGE,
  NO_STEAM_KEY_MESSAGE,
  PRIVATE_PROFILE_MESSAGE,
  SETTINGS_READONLY_MESSAGE,
} from "../js/lib/owned-library.js";

const dom = createFakeDom();
globalThis.document = dom.document;

const storage = new Map();
let reloads = 0;
globalThis.window = {
  localStorage: {
    getItem: (k) => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => storage.set(k, String(v)),
  },
  location: {
    origin: "http://vault.test",
    pathname: "/",
    reload() {
      reloads++;
      server.calls.push({ method: "RELOAD", path: "" });
    },
  },
  history: { pushState() {} },
  addEventListener() {},
  removeEventListener() {},
};

const GOOD_KEY = "good-key";
const STEAMID = "76561198042117903";
const OTHER_STEAMID = "76561197960287930";

let server;
function freshServer() {
  return {
    calls: [],
    readonly: false,
    hasSetting: true,
    steamid: "",
    source: "default",
    envOnly: false,
    ownedStatus: 200,
    ownedGames: [{ appid: 40, name: "Owned Forty" }],
    /** When set, owned-games answers only after this promise resolves. */
    ownedGate: null,
    patchStatus: 200,
  };
}

function settingsBody() {
  const settings = [
    { key: "vault_name", effective: "vault-01", source: "default", fallback: "vault-01", applies: "restart-required", env_only: false },
  ];
  if (server.hasSetting) {
    settings.push({
      key: "steam_library_steamid",
      effective: server.steamid,
      source: server.source,
      fallback: "",
      applies: "immediately",
      env_only: server.envOnly,
    });
  }
  return { readonly: server.readonly, server_version: "0.0.0-test", settings };
}
function respond(status, data) {
  const text = JSON.stringify(data);
  return { ok: status >= 200 && status < 300, status, json: async () => data, text: async () => text };
}
globalThis.fetch = async (url, init = {}) => {
  const u = new URL(String(url));
  const method = init.method || "GET";
  const headers = init.headers || {};
  server.calls.push({ method, path: u.pathname, rawBody: init.body });
  if (u.pathname === "/v1/health") return respond(200, { status: "ok" });
  if (headers["X-Api-Key"] !== GOOD_KEY) return respond(401, { detail: "Invalid API key" });
  if (u.pathname === "/v1/settings" && method === "GET") return respond(200, settingsBody());
  if (u.pathname === "/v1/settings" && method === "PATCH") {
    if (server.patchStatus === 500) return respond(500, { detail: "database is locked" });
    if (server.vaultNamePatchFails && "vault_name" in JSON.parse(init.body)) return respond(422, { detail: "vault_name: too long" });
    if (server.patchStatus === 422) return respond(422, { detail: "'steam_library_steamid': rejected" });
    const body = JSON.parse(init.body);
    if (typeof body.steam_library_steamid !== "string") {
      return respond(422, { detail: "'steam_library_steamid': must be a SteamID64 string" });
    }
    server.steamid = body.steam_library_steamid;
    server.source = "db";
    return respond(200, settingsBody());
  }
  if (u.pathname === "/v1/steam/key") return respond(200, { configured: true, key_last4: "ABCD" });
  if (u.pathname === "/v1/steam/owned-games") {
    if (server.ownedGate) await server.ownedGate;
    if (server.ownedStatus === 409) return respond(409, { detail: "Steam Web API key not configured" });
    if (server.ownedStatus === 422) return respond(422, { detail: "steamid must be a SteamID64" });
    if (server.ownedStatus !== 200) return respond(server.ownedStatus, { detail: "Steam upstream timed out" });
    return respond(200, { configured: true, game_count: server.ownedGames.length, games: server.ownedGames });
  }
  if (u.pathname === "/v1/steam/player-summaries") {
    return respond(200, { configured: true, players: [{ steamid: STEAMID, personaname: "Gabe" }] });
  }
  return respond(404, { detail: "Not Found" });
};

const { openOnboarding, closeOnboarding, isOnboardingOpen } = await import("../js/onboarding.js");

const tick = (ms = 5) => new Promise((r) => setTimeout(r, ms));
async function until(pred, label, timeoutMs = 2000) {
  const start = Date.now();
  while (!pred()) {
    if (Date.now() - start > timeoutMs) throw new Error(`timed out waiting for: ${label}`);
    await tick();
  }
}

const root = () => dom.document.body.querySelector("div.onb");
function buttonByText(text) {
  const b = root().querySelectorAll("button").find((x) => x.textContent === text);
  assert.ok(b, `button "${text}" not found`);
  return b;
}
const keyInput = () => root().querySelector("div.inp input");
const idInput = () => root().querySelectorAll("input").find((i) => i.id === "onb-steam-steamid");
const lookupBtn = () => root().querySelector('[data-role="steamid-lookup"]');
const saveNote = () => root().querySelector('[data-role="steamid-save-note"]');
const saveText = () => saveNote().querySelector("span").textContent;
function step2Text() {
  const sec = root().querySelectorAll("section.ostep").find((s) => s.dataset.step === "2");
  return sec.textContent;
}
const patches = () => server.calls.filter((c) => c.method === "PATCH");
const ownedCalls = () => server.calls.filter((c) => c.path === "/v1/steam/owned-games");
/** Hold the owned-games answer until the returned function is called. */
function gateOwned() {
  let release;
  server.ownedGate = new Promise((r) => {
    release = r;
  });
  return () => {
    server.ownedGate = null;
    release();
  };
}

/** Open, verify the key, move to step 2. */
async function toStep2() {
  openOnboarding({ mode: "reconnect" });
  keyInput().value = GOOD_KEY;
  buttonByText("Test connection").dispatchEvent({ type: "click" });
  await until(() => buttonByText("Continue").disabled === false, "key verified");
  buttonByText("Continue").dispatchEvent({ type: "click" });
}
/** Type an id, press Look up, wait until the handler is done. */
async function lookUp(id) {
  idInput().value = id;
  const before = server.calls.length;
  lookupBtn().dispatchEvent({ type: "click" });
  await until(() => server.calls.length > before, "lookup request sent");
  await until(() => lookupBtn().getAttribute("aria-disabled") === "false", "lookup finished");
}

beforeEach(() => {
  if (isOnboardingOpen()) closeOnboarding();
  storage.clear();
  server = freshServer();
  reloads = 0;
  resetModalStack(dom.document);
});

test("a successful lookup saves the id as a JSON STRING and confirms it", async () => {
  await toStep2();
  await lookUp(STEAMID);
  assert.equal(patches().length, 1, "MUTATION TARGET: dropping the save after a lookup leaves the library without owned games");
  assert.equal(patches()[0].rawBody, `{"steam_library_steamid":"${STEAMID}"}`, "string on the wire, never a number");
  assert.equal(server.steamid, STEAMID);
  assert.equal(saveText(), `Saved ${STEAMID} — your library will show the games this SteamID64 owns.`);
  assert.equal(saveNote().querySelector("span").getAttribute("role"), "status");
  assert.notEqual(saveNote().hidden, true, "the status <p> is always present, never hidden");
  const hint = root().querySelectorAll("p").find((x) => x.id === idInput().getAttribute("aria-describedby"));
  assert.ok(hint && /saves it/.test(hint.textContent), "the input is described by its save hint");
});

test("a second lookup of the same id sends no second PATCH (compares against the PATCH answer)", async () => {
  await toStep2();
  await lookUp(STEAMID);
  await lookUp(STEAMID);
  assert.equal(patches().length, 1);
  assert.match(saveText(), /^Saved/);
  await lookUp(OTHER_STEAMID);
  assert.equal(patches().length, 2);
  assert.equal(server.steamid, OTHER_STEAMID);
});

for (const [status, message] of [
  [409, NO_STEAM_KEY_MESSAGE],
  [422, INVALID_STEAMID64_MESSAGE],
  [504, "Steam upstream timed out"],
]) {
  test(`a relay ${status} saves nothing and shows the shared error`, async () => {
    server.ownedStatus = status;
    await toStep2();
    await lookUp(STEAMID);
    assert.equal(ownedCalls().length, 1);
    assert.deepEqual(patches(), [], "MUTATION TARGET: a failed lookup must not store the id");
    assert.equal(saveText(), "", "no saved/not-saved line for a failed lookup");
    assert.notEqual(saveNote().hidden, true, "MUTATION TARGET: the empty status <p> stays present, never hidden");
    assert.ok(step2Text().includes(message), `expected "${message}" in step 2`);
  });
}

test("an invalid typed id never reaches the relay or the settings", async () => {
  await toStep2();
  idInput().value = "1234";
  lookupBtn().dispatchEvent({ type: "click" });
  await until(() => step2Text().includes(INVALID_STEAMID64_MESSAGE), "validation error shown");
  assert.deepEqual(ownedCalls(), []);
  assert.deepEqual(patches(), []);
});

test("0 games (private profile): still saves and shows the private hint", async () => {
  server.ownedGames = [];
  await toStep2();
  await lookUp(STEAMID);
  assert.equal(patches().length, 1, "MUTATION TARGET: the id is valid, a private profile is no reason not to store it");
  assert.equal(server.steamid, STEAMID);
  assert.ok(step2Text().includes(PRIVATE_PROFILE_MESSAGE), "MUTATION TARGET: private-profile hint missing");
  assert.match(saveText(), /^Saved/);
});

test("read-only settings: lookup works, no PATCH, says not saved", async () => {
  server.readonly = true;
  await toStep2();
  await lookUp(STEAMID);
  assert.equal(ownedCalls().length, 1);
  assert.deepEqual(patches(), [], "MUTATION TARGET: a read-only vault must never get a PATCH");
  assert.equal(saveText(), `Not saved: ${SETTINGS_READONLY_MESSAGE} The lookup above still worked.`);
  assert.ok(step2Text().includes("1 games found."), "the lookup result is still shown");
});

test("older vault-api without the setting: lookup works, no PATCH, says not saved", async () => {
  server.hasSetting = false;
  await toStep2();
  await lookUp(STEAMID);
  assert.equal(ownedCalls().length, 1);
  assert.deepEqual(patches(), [], "MUTATION TARGET: an absent setting must not be PATCHed");
  assert.equal(saveText(), `Not saved: ${LIBRARY_SETTING_ABSENT_MESSAGE} The lookup above still worked.`);
});

test("the input is pre-filled from the stored value, and a fresh open clears it", async () => {
  server.steamid = STEAMID;
  server.source = "db";
  await toStep2();
  assert.equal(idInput().value, STEAMID, "MUTATION TARGET: prefill from steam_library_steamid");
  await lookUp(idInput().value);
  assert.deepEqual(patches(), [], "an unchanged stored id is not re-sent");
  assert.match(saveText(), /^Saved/);

  closeOnboarding();
  server.steamid = "";
  await toStep2();
  assert.equal(idInput().value, "", "no stale id from the previous open");
  assert.equal(saveText(), "", "no stale saved line from the previous open");
});

test("a typed id is not overwritten by the prefill", async () => {
  server.steamid = STEAMID;
  openOnboarding({ mode: "reconnect" });
  idInput().value = OTHER_STEAMID;
  keyInput().value = GOOD_KEY;
  buttonByText("Test connection").dispatchEvent({ type: "click" });
  await until(() => buttonByText("Continue").disabled === false, "key verified");
  assert.equal(idInput().value, OTHER_STEAMID);
});

test("a failed PATCH (500): 'Not saved: <detail>.', the old snapshot is kept, so the next Look up PATCHes again", async () => {
  server.patchStatus = 500;
  await toStep2();
  await lookUp(STEAMID);
  assert.equal(patches().length, 1);
  assert.equal(saveText(), "Not saved: database is locked. The lookup above still worked.", "MUTATION TARGET: an ERROR outcome reported as Saved");
  server.patchStatus = 200;
  await lookUp(STEAMID);
  assert.equal(patches().length, 2, "MUTATION TARGET: a failed save must not replace state.settings");
  assert.match(saveText(), /^Saved /);
});

test("a failed PATCH (422): the invalid-id text plus the server detail, one period", async () => {
  server.patchStatus = 422;
  await toStep2();
  await lookUp(STEAMID);
  assert.equal(
    saveText(),
    `Not saved: ${INVALID_STEAMID64_MESSAGE} ('steam_library_steamid': rejected). The lookup above still worked.`,
  );
});

test("env-only setting: no PATCH, says it is set by the server environment", async () => {
  server.envOnly = true;
  await toStep2();
  await lookUp(STEAMID);
  assert.deepEqual(patches(), []);
  assert.equal(saveText(), `Not saved: ${LIBRARY_SETTING_ENV_ONLY_MESSAGE} The lookup above still worked.`);
});

test("a double click while the lookup runs: one relay call, one PATCH; the button is aria-disabled meanwhile", async () => {
  await toStep2();
  const release = gateOwned();
  idInput().value = STEAMID;
  lookupBtn().dispatchEvent({ type: "click" });
  await until(() => ownedCalls().length === 1, "first lookup sent");
  assert.equal(lookupBtn().getAttribute("aria-disabled"), "true");
  lookupBtn().dispatchEvent({ type: "click" });
  await tick();
  release();
  await until(() => patches().length === 1 && lookupBtn().getAttribute("aria-disabled") === "false", "lookup and save done");
  await tick(20);
  assert.equal(ownedCalls().length, 1, "MUTATION TARGET: the in-flight guard");
  assert.equal(patches().length, 1);
});

test("editing the input while the lookup runs: the looked-up id is saved, not the edit", async () => {
  await toStep2();
  const release = gateOwned();
  idInput().value = STEAMID;
  lookupBtn().dispatchEvent({ type: "click" });
  await until(() => ownedCalls().length === 1, "lookup sent");
  idInput().value = OTHER_STEAMID;
  release();
  await until(() => patches().length === 1, "save sent");
  assert.equal(patches()[0].rawBody, `{"steam_library_steamid":"${STEAMID}"}`, "MUTATION TARGET: saving the live input value");
});

test("'Go to library' while a lookup runs waits for the save, then reloads once", async () => {
  await toStep2();
  const release = gateOwned();
  idInput().value = STEAMID;
  lookupBtn().dispatchEvent({ type: "click" });
  await until(() => ownedCalls().length === 1, "lookup sent");
  buttonByText("Continue").dispatchEvent({ type: "click" }); // -> step 3
  const goBtn = buttonByText("Go to library");
  goBtn.dispatchEvent({ type: "click" });
  goBtn.dispatchEvent({ type: "click" }); // impatient second press
  await tick(20);
  assert.equal(reloads, 0, "MUTATION TARGET: the reload must not abort the running save");
  assert.equal(goBtn.textContent, "Saving…", "MUTATION TARGET: busy label while the save runs");
  assert.equal(goBtn.getAttribute("aria-disabled"), "true", "MUTATION TARGET: busy via aria-disabled");
  assert.notEqual(goBtn.disabled, true, "never `disabled` (drops focus)");
  release();
  await until(() => reloads > 0, "reloaded");
  await tick(20);
  assert.equal(reloads, 1, "one reload for two presses");
  const order = server.calls.map((c) => c.method).filter((m) => m === "PATCH" || m === "RELOAD");
  assert.deepEqual(order, ["PATCH", "RELOAD"], "the save completes before the reload");
});

test("a successful lookup followed by a failing one clears the 'Saved' line", async () => {
  await toStep2();
  await lookUp(STEAMID);
  assert.match(saveText(), /^Saved /);
  server.ownedStatus = 504;
  await lookUp(OTHER_STEAMID);
  assert.equal(saveText(), "", "MUTATION TARGET: a stale 'Saved' next to a failed lookup");
  assert.equal(patches().length, 1);
});

test("an unexpected Look up failure is logged, and finish() still runs", async () => {
  await toStep2();
  const errors = [];
  const original = console.error;
  console.error = (...args) => errors.push(args);
  // Break the step-2 render the handler calls after the relay answered.
  const realGames = server.ownedGames;
  server.ownedGames = [null]; // renderLookupResult reads g.name -> TypeError
  try {
    idInput().value = STEAMID;
    lookupBtn().dispatchEvent({ type: "click" });
    await until(() => errors.length === 1, "error logged");
  } finally {
    console.error = original;
    server.ownedGames = realGames;
  }
  assert.match(String(errors[0][0]), /Look up failed unexpectedly/, "MUTATION TARGET: unexpected errors are not swallowed");
  buttonByText("Continue").dispatchEvent({ type: "click" });
  buttonByText("Go to library").dispatchEvent({ type: "click" });
  await until(() => reloads === 1, "finish() not blocked by the failed lookup");
});

test("a failed vault-name save restores the Go to library label and aria state for the retry", async () => {
  await toStep2();
  buttonByText("Continue").dispatchEvent({ type: "click" });
  root().querySelector("div.field input").value = "renamed";
  server.vaultNamePatchFails = true;
  const goBtn = buttonByText("Go to library");
  goBtn.dispatchEvent({ type: "click" });
  await until(() => patches().length === 1 && goBtn.textContent === "Go to library", "failure handled");
  assert.equal(reloads, 0);
  assert.equal(goBtn.getAttribute("aria-disabled"), "false", "MUTATION TARGET: restore after a failed save");
});
