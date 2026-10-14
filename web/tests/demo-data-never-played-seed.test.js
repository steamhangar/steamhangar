/**
 * WP UPD-5 demo pill (user decision 2026-10-14): the browser demo shows the
 * "Never played" pill out of the box. A browser demo seeds the ADR-0010
 * playtime gate ON plus a demo Steam Web API key and SteamID64, so the
 * Library's owned list carries Frostline Convoy's real zero playtime and its
 * card qualifies for the pill without any manual step. The demo-only
 * preference `steamvault.demoRelayPlaytime` = "0" opts out; outside a browser
 * (every other bare-Node test) the baseline stays the shipped gate-off,
 * unconfigured shape. Real installs are untouched (server default OFF).
 *
 * This file installs a fake browser `window` BEFORE importing demo-data.js
 * (node --test runs each file in its own process), because the seed is read
 * at module load.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";

const store = new Map();
globalThis.window = {
  localStorage: {
    getItem: (key) => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => store.set(key, String(value)),
    removeItem: (key) => store.delete(key),
  },
};

const { demoRequest, resetDemoData, DEMO_STEAMID } = await import("../js/demo-data.js");
const { withOwnedPlaytime } = await import("../js/lib/owned-library.js");
const { isNeverPlayedCached } = await import("../js/lib/decision-support.js");
const { dispKind, KIND } = await import("../js/lib/game-status.js");
const { validSteamId64 } = await import("../js/lib/steamid.js");

const FROSTLINE = 2010050;

async function setting(key) {
  const out = await demoRequest("GET", "/v1/settings");
  return out.settings.find((e) => e.key === key);
}

/** The Library's path from the demo API to the pill predicate: vault rows,
 * owned list for the stored SteamID, playtime merge, then the card's rule
 * (status "cached" AND `isNeverPlayedCached`). */
async function frostlineShowsPill() {
  const steamid = (await setting("steam_library_steamid")).effective;
  const owned = await demoRequest("GET", "/v1/steam/owned-games", { params: { steamid } });
  const rows = withOwnedPlaytime(await demoRequest("GET", "/v1/games"), owned.games);
  const game = rows.find((g) => g.appid === FROSTLINE);
  assert.ok(game, "sanity: Frostline Convoy is a demo vault game");
  return dispKind(game, undefined) === KIND.CACHED && isNeverPlayedCached(game);
}

test("browser demo, no preference: key, SteamID and playtime gate are seeded at module load", async () => {
  // No resetDemoData() here on purpose: the module-load seed is what a
  // freshly opened demo uses.
  assert.deepEqual(await demoRequest("GET", "/v1/steam/key"), { configured: true, key_last4: "CDEF" });
  const steamid = await setting("steam_library_steamid");
  assert.equal(steamid.effective, DEMO_STEAMID);
  assert.equal(steamid.source, "db", "seeded like a Settings save");
  assert.equal(validSteamId64(DEMO_STEAMID), DEMO_STEAMID, "the demo SteamID passes the real grammar");
  assert.equal((await setting("relay_expose_playtime")).effective, true);
  assert.equal(await frostlineShowsPill(), true, "Frostline Convoy shows the pill out of the box");
});

test("browser demo: resetDemoData() with no options seeds the same showcase", async () => {
  resetDemoData();
  assert.equal((await demoRequest("GET", "/v1/steam/key")).configured, true);
  assert.equal(await frostlineShowsPill(), true);
});

test("browser demo: the seeded gate exposes only the fixture's playtime, never a fabricated zero", async () => {
  resetDemoData();
  const owned = await demoRequest("GET", "/v1/steam/owned-games", { params: { steamid: DEMO_STEAMID } });
  const byAppid = new Map(owned.games.map((g) => [g.appid, g]));
  assert.equal(byAppid.get(FROSTLINE).playtime_forever, 0);
  assert.equal(byAppid.get(2010010).playtime_forever, 4312);
  for (const g of owned.games) {
    assert.equal("rtime_last_played" in g, false, "the last-played gate stays off");
  }
});

test("browser demo, preference '0': opts out to the shipped gate-off, unconfigured shape", async () => {
  store.set("steamvault.demoRelayPlaytime", "0");
  try {
    resetDemoData();
    assert.deepEqual(await demoRequest("GET", "/v1/steam/key"), { configured: false, key_last4: null });
    assert.equal((await setting("steam_library_steamid")).effective, "");
    assert.equal((await setting("relay_expose_playtime")).effective, false);
  } finally {
    store.delete("steamvault.demoRelayPlaytime");
    resetDemoData();
  }
});

test("outside a browser the baseline is unchanged: no key, no SteamID, gate off", async () => {
  const saved = globalThis.window;
  delete globalThis.window;
  try {
    resetDemoData();
    assert.deepEqual(await demoRequest("GET", "/v1/steam/key"), { configured: false, key_last4: null });
    assert.equal((await setting("steam_library_steamid")).effective, "");
    assert.equal((await setting("relay_expose_playtime")).effective, false);
  } finally {
    globalThis.window = saved;
    resetDemoData();
  }
});

test("WP UPD-5 mirror: the demo's update status says scheduled with the window cleared", async () => {
  resetDemoData();
  await demoRequest("PATCH", "/v1/settings", { body: { schedule_window: "" } });
  // D16b: no window is spelled "off" (blank PATCH value = off, D16b-1 review).
  assert.equal((await setting("schedule_window")).effective, "off");
  const status = await demoRequest("GET", "/v1/updates/status");
  assert.equal(status.enabled, true);
  assert.equal(status.scheduled, true);
});
