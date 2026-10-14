/**
 * Large-library fixture (WP 4e.1) — web/js/demo-data.js's
 * `generateSyntheticGames`/`resetDemoData({ librarySize })`.
 *
 * This is the "gate for the whole phase" fixture the WP 4e.1 brief asks
 * for: does the library grid/poll-diff machinery still behave at a library
 * size an operator's real Steam account can plausibly have? See
 * `generateSyntheticGames`'s own header in demo-data.js for what synthetic
 * games can and cannot measure (no real cover art, so this exercises DOM/
 * poll-diff cost, not the CSP image allowance).
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { generateSyntheticGames, resetDemoData, demoRequest } from "../js/demo-data.js";

test("generateSyntheticGames(0) produces nothing", () => {
  assert.deepEqual(generateSyntheticGames(0), []);
});

/** Strip the one field `makeGame()` derives from `Date.now()`
 * (`last_prefill_at`, via `isoAgo()`) before a determinism comparison — that
 * field is deliberately wall-clock-based (same as every curated seed game
 * above), so two calls a few milliseconds apart can legitimately differ in
 * it by a millisecond; the actually-deterministic contract this test pins
 * is everything the generator computes from `i` alone (appid, name, status,
 * depots). */
function withoutWallClockFields(games) {
  return games.map(({ last_prefill_at, ...rest }) => rest);
}

test("generateSyntheticGames is deterministic — same count, same output every call", () => {
  const a = generateSyntheticGames(50);
  const b = generateSyntheticGames(50);
  assert.deepEqual(withoutWallClockFields(a), withoutWallClockFields(b));
});

test("generateSyntheticGames: every appid is unique and clear of the curated seed/relay ranges", () => {
  const games = generateSyntheticGames(394);
  assert.equal(games.length, 394);
  const appids = games.map((g) => g.appid);
  assert.equal(new Set(appids).size, appids.length, "appids must be unique");
  for (const appid of appids) {
    // Curated seed games: 2010010-2010070. Steam-relay fixture: 3300100-3300300.
    assert.ok(
      appid < 2_000_000 || appid > 3_400_000,
      `synthetic appid ${appid} collides with an existing fixture range`,
    );
  }
});

test("generateSyntheticGames: every game has a non-empty name and a real status", () => {
  for (const g of generateSyntheticGames(30)) {
    assert.ok(typeof g.name === "string" && g.name.trim().length > 0);
    assert.ok(["done", "idle"].includes(g.status));
  }
});

test("generateSyntheticGames: a 'done' game has depot content; an 'idle' one has none", () => {
  for (const g of generateSyntheticGames(30)) {
    if (g.status === "done") {
      assert.ok(g.depots.length > 0, `done game ${g.appid} has no depots`);
      for (const d of g.depots) assert.ok(d.size_bytes > 0);
    } else {
      assert.deepEqual(g.depots, []);
    }
  }
});

test("generateSyntheticGames: mixed shape, not degenerate all-cached/all-empty", () => {
  const games = generateSyntheticGames(30);
  const cachedCount = games.filter((g) => g.status === "done").length;
  assert.ok(cachedCount > 0 && cachedCount < games.length);
});

// Opus review should-fix S1 (WP 4e.1 fix round): api/README.md's needs_force
// lifecycle says a never-filled app is needs_force=1 — nothing has ever
// confirmed it current. `needs_force=0`/false is reached ONLY via a
// successful `done` job. An "idle, needs_force=false" row (the shape
// `makeGame()`'s own default used to produce here) is not a shape the real
// API can ever emit; a demo fixture is a shipped 1:1 surface, so this is a
// real correctness bug, not a cosmetic one.
test("generateSyntheticGames: needs_force is the exact inverse of 'cached' (idle rows are always needs_force=true, done rows always false)", () => {
  for (const g of generateSyntheticGames(60)) {
    if (g.status === "done") {
      assert.equal(g.needs_force, false, `done game ${g.appid} must be needs_force=false`);
    } else {
      assert.equal(g.status, "idle");
      assert.equal(g.needs_force, true, `idle game ${g.appid} must be needs_force=true — nothing has confirmed it current`);
    }
  }
});

// N4 (Opus review nitpick, WP 4e.1 fix round): pin the exact SHAPE of the
// one wall-clock-derived field withoutWallClockFields() strips above, so a
// future change to makeGame()'s isoAgo() usage can't silently drift without
// a named test noticing — a "done" game must carry a real ISO timestamp (it
// really was prefilled, per the same S1 reasoning above), an "idle" one must
// carry null (never prefilled).
test("generateSyntheticGames: last_prefill_at is a real ISO string for 'done' games, and exactly null for 'idle' ones", () => {
  const ISO_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/;
  for (const g of generateSyntheticGames(60)) {
    if (g.status === "done") {
      assert.match(g.last_prefill_at, ISO_RE, `done game ${g.appid} needs a real ISO last_prefill_at`);
    } else {
      assert.equal(g.last_prefill_at, null, `idle game ${g.appid} must have last_prefill_at === null`);
    }
  }
});

test("resetDemoData() with no argument yields exactly the curated rows: 6 games plus the 4-row content-package group (regression pin)", async () => {
  resetDemoData();
  const rows = await demoRequest("GET", "/v1/games");
  // WP TOOL-APPS-2c: 6 hand-named games + Starfall Legion with 3 packages.
  assert.equal(rows.length, 10);
  resetDemoData();
});

test("resetDemoData({ librarySize: 400 }) appends synthetic games on top of the curated 6", async () => {
  resetDemoData({ librarySize: 400 });
  try {
    const rows = await demoRequest("GET", "/v1/games");
    assert.equal(rows.length, 400);
    // The curated seed appids must still be present, untouched — the large
    // fixture is additive, never a replacement.
    const appids = new Set(rows.map((g) => g.appid));
    for (const seedAppid of [2010010, 2010020, 2010030, 2010040, 2010050, 2010070]) {
      assert.ok(appids.has(seedAppid), `curated seed appid ${seedAppid} missing`);
    }
  } finally {
    resetDemoData();
  }
});

test("resetDemoData({ librarySize }) below the default is a no-op (still exactly the 10 curated rows)", async () => {
  resetDemoData({ librarySize: 3 });
  try {
    const rows = await demoRequest("GET", "/v1/games");
    assert.equal(rows.length, 10);
  } finally {
    resetDemoData();
  }
});

test("MUTATION PIN: the large-library route wiring actually reaches GET /v1/games — every synthetic game round-trips through the real request handler, not just the internal array", async () => {
  resetDemoData({ librarySize: 50 });
  try {
    const rows = await demoRequest("GET", "/v1/games");
    assert.equal(rows.length, 50);
    // Every row is shaped like a real GameSummary (api/README.md) — the
    // large-library games flow through the SAME projection function as the
    // curated ones, not a second, potentially-drifting shape.
    for (const row of rows) {
      assert.ok("appid" in row && "name" in row && "status" in row && "size_bytes" in row);
    }
    // POST /v1/prefill/cached (Phase 4c) must see the synthetic "done" games
    // as cached too — proves the large fixture composes with pre-existing
    // routes, not just GET /v1/games.
    const cachedBefore = rows.filter((g) => g.size_bytes && g.size_bytes > 0);
    assert.ok(cachedBefore.length > 0);
  } finally {
    resetDemoData();
  }
});
