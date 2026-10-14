/**
 * Headless tests for web/js/demo-data.js's `installed_on` field (WP AG-2) —
 * the demo-mode mirror of WP AG-1's additive `GET /v1/games`/
 * `GET /v1/games/{appid}` field (api/README.md "Installed state per app").
 *
 * demo-data.js imports only errors.js and two other DOM-free `lib/` modules
 * (no `window`, `document` or `fetch`), so it runs directly in bare Node.
 *
 * "Demo fixtures are a shipped surface" (docs/LEARNINGS.md) — the shape
 * pinned here is verified against the REAL wire shape documented in
 * api/README.md line-for-line: `installed_on: [{client_id, reported_at}]`,
 * present (possibly empty) on both `GET /v1/games` rows and
 * `GET /v1/games/{appid}`.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { demoRequest, resetDemoData } from "../js/demo-data.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Drift guard (same "read the real source as plain text" style
// demo-data.js's own CONFIG_DEFAULT_AUTO_GC comment establishes for
// api/vault_api/config.py): reads api/vault_api/routers/games.py directly
// and asserts the REAL field name/shape this fixture claims to mirror is
// still what it says it is — a coder here has no Python to import the
// Pydantic model with, so a plain-text regex is the cheapest guard that
// still fails loudly on drift (field renamed, entry shape changed) instead
// of silently teaching the wrong shape on the exact surface used for
// screenshots.
//
// **Two failure classes, and every message below names which one applies
// (docs/LEARNINGS.md, 2026-08-22 sweep entry: "every failure message names
// which of two edits applies: VALUE drift -> fix the fixture, GRAMMAR drift
// -> widen this regex").** VALUE drift is a real, meaningful shape change
// (the field renamed, retyped, or made optional) — fix demo-data.js.
// GRAMMAR drift is Pydantic accepting the SAME meaning spelled a different
// way (e.g. `= []` vs `Field(default_factory=list)` — both mean "empty list
// default", zero behaviour change) that this regex simply doesn't recognize
// yet — widen the regex, do not touch demo-data.js. The two default-value
// spellings are both accepted below for exactly this reason: a harmless
// reformat must not fail this test at all, let alone fail it with the WRONG
// instruction.
const gamesRouterPath = path.join(__dirname, "..", "..", "api", "vault_api", "routers", "games.py");
const gamesRouterSrc = readFileSync(gamesRouterPath, "utf8");

const INSTALLED_ON_DEFAULT_RE = /installed_on:\s*list\[InstalledOn\]\s*=\s*(?:\[\]|Field\(\s*default_factory\s*=\s*list\s*\))/g;

test("drift guard: GameSummary/GameDetail still declare installed_on: list[InstalledOn], defaulting to an empty list", () => {
  const matches = gamesRouterSrc.match(INSTALLED_ON_DEFAULT_RE) || [];
  assert.equal(
    matches.length,
    2,
    "expected exactly two `installed_on: list[InstalledOn]` declarations, each defaulting to an empty list " +
      "(via `= []` or `Field(default_factory=list)`), in api/vault_api/routers/games.py (GameSummary and GameDetail). " +
      "If the FIELD NAME or its list-of-InstalledOn TYPE changed, that is VALUE drift -- update demo-data.js's field " +
      "name/shape to match. If Pydantic's default-value SYNTAX changed to some other equivalent spelling this regex " +
      "does not recognize yet, that is GRAMMAR drift -- widen INSTALLED_ON_DEFAULT_RE above instead; do not touch demo-data.js.",
  );
});

test("drift guard: InstalledOn still declares client_id and reported_at as plain, non-optional str fields", () => {
  // Bounded by the next line that dedents to column 0 (the following
  // top-level `def`/`class`) rather than a fixed blank-line count, so this
  // survives a docstring gaining/losing a blank line.
  const m = /class InstalledOn\(BaseModel\):([\s\S]*?)(?=\n\S)/.exec(gamesRouterSrc);
  assert.ok(m, "InstalledOn model not found in api/vault_api/routers/games.py — did it move or get renamed? (VALUE drift)");
  // Anchored to the END of the declaration (`\s*$`, multiline) rather than
  // a bare substring match — `client_id: str` is a SUBSTRING of
  // `client_id: str | None`, which is a REAL, meaningful shape change (a
  // nullable client_id would literally render as the string "null" in the
  // card badge and the sheet's aria-label via a plain template literal) that
  // a substring-only regex would pass silently. This is VALUE drift, not
  // grammar — if either assertion below fails, fix demo-data.js/the JS badge
  // code's handling of the new possibility, do not widen this regex.
  assert.match(
    m[1],
    /^\s*client_id:\s*str\s*$/m,
    "InstalledOn.client_id is no longer a plain, non-optional `str` (e.g. `str | None`?) — this is VALUE drift: " +
      "decide how demo-data.js and the badge/aria-label code should handle a possibly-null client_id, do not just widen this regex",
  );
  assert.match(
    m[1],
    /^\s*reported_at:\s*str\s*$/m,
    "InstalledOn.reported_at is no longer a plain, non-optional `str` — this is VALUE drift: decide how " +
      "formatTimestamp/the sheet's row rendering should handle the new possibility, do not just widen this regex",
  );
});

beforeEach(() => {
  resetDemoData();
});

// Seed ids/fixtures from demo-data.js's buildCuratedGames() — chosen so this
// one 6-game seed exercises all three badge states (WP AG-2 brief):
const AURORA_CASCADE = 2010010; // installed + cached, ONE client
const COPPER_HORIZON = 2010020; // installed + NOT cached (depots: []), TWO clients
const DRIFTWOOD_SIGNAL = 2010030; // no installed_on entry at all (no signal)

test("MUTATION TARGET -- GET /v1/games: every row carries an installed_on array, real field name", async () => {
  const games = await demoRequest("GET", "/v1/games");
  assert.ok(games.length > 0);
  for (const g of games) {
    assert.ok(Array.isArray(g.installed_on), `appid ${g.appid} missing an installed_on array`);
  }
});

test("MUTATION TARGET -- installed + cached fixture (Aurora Cascade): non-empty installed_on, positive size_bytes", async () => {
  const games = await demoRequest("GET", "/v1/games");
  const aurora = games.find((g) => g.appid === AURORA_CASCADE);
  assert.ok(aurora.installed_on.length > 0, "Aurora Cascade must have a fresh installed_on entry");
  assert.ok(typeof aurora.size_bytes === "number" && aurora.size_bytes > 0, "Aurora Cascade must be cached");
});

test("MUTATION TARGET -- installed + NOT cached fixture (Copper Horizon): non-empty installed_on, no visible bytes", async () => {
  const games = await demoRequest("GET", "/v1/games");
  const copper = games.find((g) => g.appid === COPPER_HORIZON);
  assert.ok(copper.installed_on.length > 0, "Copper Horizon must have a fresh installed_on entry");
  assert.equal(copper.size_bytes, null, "Copper Horizon must have nothing cached (depots: [])");
});

test("MUTATION TARGET (S5) -- Copper Horizon has TWO installed_on entries, exercising installedOnSummary's '+N' branch and the detail sheet's multi-row list", async () => {
  // Review round 1, S5: every existing fixture (this file, before the fix)
  // had exactly ONE client, so `installedOnSummary`'s "+N" branch and the
  // detail sheet's multi-row "Installed on" list appeared nowhere in demo
  // mode (the screenshot surface) or in any DOM test — and multi-client is
  // exactly where the badge's width cap (css/app.css) bites hardest,
  // doubly so combined with the NOT_CACHED case that already has the least
  // room to spare.
  const games = await demoRequest("GET", "/v1/games");
  const copper = games.find((g) => g.appid === COPPER_HORIZON);
  assert.equal(copper.installed_on.length, 2, "Copper Horizon must have exactly two installed_on entries");
  const clientIds = copper.installed_on.map((e) => e.client_id);
  assert.equal(new Set(clientIds).size, 2, "the two entries must be two DIFFERENT clients, not a duplicate");
});

test("no-signal fixture (Driftwood Signal): installed_on is present but empty, never a claim either way", async () => {
  const games = await demoRequest("GET", "/v1/games");
  const driftwood = games.find((g) => g.appid === DRIFTWOOD_SIGNAL);
  assert.deepEqual(driftwood.installed_on, []);
});

test("each installed_on entry has the real wire shape: client_id (string), reported_at (parseable ISO string)", async () => {
  const games = await demoRequest("GET", "/v1/games");
  const aurora = games.find((g) => g.appid === AURORA_CASCADE);
  const [entry] = aurora.installed_on;
  assert.equal(typeof entry.client_id, "string");
  assert.equal(typeof entry.reported_at, "string");
  assert.ok(!Number.isNaN(new Date(entry.reported_at).getTime()), "reported_at must be a parseable timestamp");
});

test("GET /v1/games/{appid} (GameDetail) carries the SAME installed_on shape as the GameSummary row", async () => {
  const games = await demoRequest("GET", "/v1/games");
  const summary = games.find((g) => g.appid === COPPER_HORIZON);
  const detail = await demoRequest("GET", `/v1/games/${COPPER_HORIZON}`);
  assert.deepEqual(detail.installed_on, summary.installed_on);
});

test("mutating one response's installed_on array does not leak into the next fetch (fresh copy each call)", async () => {
  const first = await demoRequest("GET", "/v1/games");
  const aurora = first.find((g) => g.appid === AURORA_CASCADE);
  aurora.installed_on.push({ client_id: "injected", reported_at: "2000-01-01T00:00:00Z" });
  aurora.installed_on[0].client_id = "mutated";

  const second = await demoRequest("GET", "/v1/games");
  const aurora2 = second.find((g) => g.appid === AURORA_CASCADE);
  assert.equal(aurora2.installed_on.length, 1, "the pushed entry must not have leaked into demo-data.js's own state");
  assert.notEqual(aurora2.installed_on[0].client_id, "mutated");
});

test("a synthetic (large-library) game has no installed_on entries — the badge is only exercised by the curated fixtures", async () => {
  resetDemoData({ librarySize: 14 });
  const games = await demoRequest("GET", "/v1/games");
  const synthetic = games.find((g) => g.appid >= 5_000_000);
  assert.ok(synthetic, "expected at least one synthetic game at librarySize 14 (10 curated rows)");
  assert.deepEqual(synthetic.installed_on, []);
});
