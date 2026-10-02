/**
 * Demo mode for the owned Steam library (WP WEB-FEAT-1).
 *
 * Demo fixtures are a shipped surface (LEARNINGS): the demo
 * `steam_library_steamid` setting must behave like WP API-FEAT-1's real one
 * (blank default, applies immediately, string-only PATCH, the relay's
 * SteamID64 grammar), and the demo owned list must exercise both merge
 * cases (an owned game the vault knows, and owned-only games).
 */
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { demoRequest, resetDemoData } from "../js/demo-data.js";
import { mergeOwnedLibrary, isOwnedOnly, steamIdFromSettings } from "../js/lib/owned-library.js";

const VALID_KEY = "0123456789ABCDEF0123456789ABCDEF";
const STEAMID = "76561198042117903";

beforeEach(() => {
  resetDemoData();
});

async function settingEntry() {
  const out = await demoRequest("GET", "/v1/settings");
  return out.settings.find((e) => e.key === "steam_library_steamid");
}

test("GET /v1/settings carries steam_library_steamid: blank default, applies immediately, overridable", async () => {
  const entry = await settingEntry();
  assert.ok(entry, "the demo settings mirror the real API's new key");
  assert.equal(entry.effective, "");
  assert.equal(entry.source, "default");
  assert.equal(entry.applies, "immediately");
  assert.equal(entry.env_only, false);
});

test("PATCH accepts a valid SteamID64 string (trimmed) and the library reads it back", async () => {
  const out = await demoRequest("PATCH", "/v1/settings", { body: { steam_library_steamid: ` ${STEAMID} ` } });
  assert.equal(steamIdFromSettings(out), STEAMID);
  const entry = await settingEntry();
  assert.equal(entry.source, "db");
});

test("PATCH refuses a JSON number (422) — same rule as the real router", async () => {
  await assert.rejects(
    () => demoRequest("PATCH", "/v1/settings", { body: { steam_library_steamid: 76561198042117903 } }),
    (err) => err.status === 422 && /JSON string/.test(err.detail),
  );
  assert.equal((await settingEntry()).effective, "", "nothing persisted");
});

test("PATCH refuses an invalid SteamID64 (422); blank clears; null resets", async () => {
  await assert.rejects(
    () => demoRequest("PATCH", "/v1/settings", { body: { steam_library_steamid: "12345" } }),
    (err) => err.status === 422,
  );
  await demoRequest("PATCH", "/v1/settings", { body: { steam_library_steamid: STEAMID } });
  await demoRequest("PATCH", "/v1/settings", { body: { steam_library_steamid: "" } });
  let entry = await settingEntry();
  assert.equal(entry.effective, "");
  assert.equal(entry.source, "db", "blank is a real override, like webhook_url");
  await demoRequest("PATCH", "/v1/settings", { body: { steam_library_steamid: null } });
  entry = await settingEntry();
  assert.equal(entry.source, "default");
});

test("the demo owned list overlaps the demo vault: 2 deduped vault games, 3 owned-only 'Not cached' rows", async () => {
  await demoRequest("PUT", "/v1/steam/key", { body: { key: VALID_KEY } });
  const vault = await demoRequest("GET", "/v1/games");
  const owned = await demoRequest("GET", "/v1/steam/owned-games", { params: { steamid: STEAMID } });
  const merged = mergeOwnedLibrary(vault, owned.games);
  assert.equal(merged.length, vault.length + 3);
  const ownedOnly = merged.filter(isOwnedOnly).map((g) => g.appid).sort();
  assert.deepEqual(ownedOnly, [3300100, 3300200, 3300300]);
  for (const appid of [2010010, 2010040]) {
    assert.equal(merged.filter((g) => g.appid === appid).length, 1, `${appid} once`);
    assert.equal(isOwnedOnly(merged.find((g) => g.appid === appid)), false);
  }
});

test("queuing an owned-only game (detail sheet path) creates a vault row with NO name, like the real API; the merge still titles it", async () => {
  // WP WEB-FIX-4: the real enqueue inserts `apps (appid, status)` only.
  await demoRequest("PUT", "/v1/steam/key", { body: { key: VALID_KEY } });
  await assert.rejects(() => demoRequest("GET", "/v1/games/3300100"), (err) => err.status === 404);
  await demoRequest("POST", "/v1/prefill", { body: { appids: [3300100] } });
  const vault = await demoRequest("GET", "/v1/games");
  assert.equal(vault.find((g) => g.appid === 3300100).name, null);
  const owned = await demoRequest("GET", "/v1/steam/owned-games", { params: { steamid: STEAMID } });
  const merged = mergeOwnedLibrary(vault, owned.games);
  assert.equal(merged.find((g) => g.appid === 3300100).name, "Sable Undertow", "the owned name fills the gap");
});
