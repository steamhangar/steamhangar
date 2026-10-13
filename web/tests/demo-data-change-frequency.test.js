/**
 * WP 4h.2, coder's own addition (named in the report, not one of the
 * brief's three named carried-over defects): `web/js/demo-data.js`'s
 * `GET /v1/games`/`GET /v1/games/{appid}` projections never carried WP
 * 4h.1's `manifest_change_frequency`/`manifest_observation_days`/
 * `manifest_days_since_last_change` fields at all (that WP landed
 * `api/`-only) — without them, demo mode cannot demonstrate the suggestions
 * panel's CHANGED_RECENTLY/STABLE statement families, or the "insufficient_
 * data" vs. `null` distinction WP 4h.1's own honesty rule is built on. This
 * file pins the fixture now carries all three states plus the untouched
 * `null` ("never observed at all") default the majority of seed games keep.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { demoRequest, resetDemoData } from "../js/demo-data.js";

beforeEach(() => {
  resetDemoData();
});

test("GET /v1/games carries manifest_change_frequency/observation_days/days_since_last_change on every row (not silently dropped from the projection)", async () => {
  const games = await demoRequest("GET", "/v1/games");
  assert.ok(games.length > 0);
  for (const g of games) {
    assert.ok("manifest_change_frequency" in g, `appid ${g.appid} missing manifest_change_frequency`);
    assert.ok("manifest_observation_days" in g, `appid ${g.appid} missing manifest_observation_days`);
    assert.ok("manifest_days_since_last_change" in g, `appid ${g.appid} missing manifest_days_since_last_change`);
  }
});

test('the fixture demonstrates all three real WP 4h.1 states plus the null default: "stable", "changed", "insufficient_data", and null', async () => {
  const games = await demoRequest("GET", "/v1/games");
  const categories = new Set(games.map((g) => g.manifest_change_frequency));
  for (const expected of ["stable", "changed", "insufficient_data", null]) {
    assert.ok(categories.has(expected), `no seed game has manifest_change_frequency === ${JSON.stringify(expected)}`);
  }
});

test('"changed" (Driftwood Signal, 2010030) carries a real manifest_days_since_last_change, past-tense-supportable', async () => {
  const games = await demoRequest("GET", "/v1/games");
  const driftwood = games.find((g) => g.appid === 2010030);
  assert.equal(driftwood.manifest_change_frequency, "changed");
  assert.equal(typeof driftwood.manifest_days_since_last_change, "number");
});

test('"stable" (Aurora Cascade, 2010010) carries manifest_observation_days but NO days_since_last_change (WP 4h.1: populated ONLY for "changed")', async () => {
  const games = await demoRequest("GET", "/v1/games");
  const aurora = games.find((g) => g.appid === 2010010);
  assert.equal(aurora.manifest_change_frequency, "stable");
  assert.equal(typeof aurora.manifest_observation_days, "number");
  assert.equal(aurora.manifest_days_since_last_change, null);
});

test('"insufficient_data" (Frostline Convoy, 2010050) is distinct from null — WP 4h.1 pin 2', async () => {
  const games = await demoRequest("GET", "/v1/games");
  const frostline = games.find((g) => g.appid === 2010050);
  assert.equal(frostline.manifest_change_frequency, "insufficient_data");
  assert.equal(typeof frostline.manifest_observation_days, "number");
});

test("GET /v1/games/{appid} projects the same three fields for the detail route", async () => {
  const detail = await demoRequest("GET", "/v1/games/2010030");
  assert.equal(detail.manifest_change_frequency, "changed");
  assert.equal(typeof detail.manifest_days_since_last_change, "number");
});

test("the demo shows every moved Phase 4h statement end to end (WP WEB-FEAT-SUGGEST-2): Steam update line, overdue check, never-played pill", async () => {
  const { lastUpdateDownloadedText, staleUpdateChecks, isNeverPlayedCached } = await import("../js/lib/decision-support.js");
  const { withOwnedPlaytime } = await import("../js/lib/owned-library.js");
  const games = await demoRequest("GET", "/v1/games");
  assert.match(lastUpdateDownloadedText(games.find((g) => g.appid === 2010030)) ?? "", /^Last update downloaded \d+ days ago$/);
  assert.deepEqual(staleUpdateChecks(games).map((i) => i.appid), [2010070], "Glass Meridian's last update check is 20 days old");
  // The pill needs the owned list with the playtime gate on (a demo-only
  // browser preference; resetDemoData's argument here).
  resetDemoData({ relayExposePlaytime: true });
  await demoRequest("PUT", "/v1/steam/key", { body: { key: "0123456789ABCDEF0123456789ABCDEF" } });
  const owned = await demoRequest("GET", "/v1/steam/owned-games", { params: { steamid: "76561197960287930" } });
  const rows = withOwnedPlaytime(await demoRequest("GET", "/v1/games"), owned.games);
  assert.deepEqual(rows.filter(isNeverPlayedCached).map((g) => g.name), ["Frostline Convoy"]);
});
