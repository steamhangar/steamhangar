/**
 * WP CACHE-COMPLETE-1c — demo mode shows cache completeness out of the box:
 * one partially cached game (Aurora Cascade, chunk files missing -> "Complete
 * now"), one game short only of wrong-size chunks (Starfall Legion -> no
 * "Complete now"), and an unknown figure (Emberreach, an announced build);
 * every other cached game is measured complete. The two new routes mirror
 * the API (`needs_force` first, then the one enqueue), and a finished demo
 * prefill re-measures like the worker does. The six fields are pinned
 * against `GameSummary` in api/vault_api/routers/games.py (twin pin, text
 * read, no Python).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { demoRequest, resetDemoData } from "../js/demo-data.js";
import { pendingIncomplete, isCompletable, cardDisplay } from "../js/lib/cache-completeness.js";
import { groupGames } from "../js/lib/content-groups.js";
import { dispKind } from "../js/lib/game-status.js";
import { STATUS_LABEL } from "../js/components/status-icon.js";

const AURORA = 2010010;
const EMBERREACH = 2010040;
const FROSTLINE = 2010050;
const STARFALL = 2010080;
const DRIFTWOOD = 2010030;

const FIELDS = [
  "cache_complete_pct",
  "cache_expected_bytes",
  "cache_present_bytes",
  "cache_complete_checked_at",
  "cache_missing_chunks",
  "group_cache_complete_pct",
];

beforeEach(() => resetDemoData());

const games = () => demoRequest("GET", "/v1/games");
const byId = async (appid) => (await games()).find((g) => g.appid === appid);

test("twin pin: the six fields exist on the server's GameSummary and GameDetail, and on every demo row", async () => {
  const src = readFileSync(new URL("../../api/vault_api/routers/games.py", import.meta.url), "utf8");
  for (const model of ["GameSummary", "GameDetail"]) {
    const m = new RegExp(`class ${model}\\(BaseModel\\):([\\s\\S]*?)(?=\\n\\S)`).exec(src);
    assert.ok(m, `class ${model} not found — VALUE drift: re-point this pin`);
    for (const f of FIELDS) assert.match(m[1], new RegExp(`^ {4}${f}:`, "m"), `${model}.${f} — VALUE drift: the server renamed it`);
  }
  for (const g of await games()) for (const f of FIELDS) assert.ok(Object.hasOwn(g, f), `${g.appid} lacks ${f}`);
  const detail = await demoRequest("GET", `/v1/games/${AURORA}`);
  for (const f of FIELDS) assert.ok(Object.hasOwn(detail, f), `detail lacks ${f}`);
});

test("MUTATION TARGET -- seeds: Aurora partial and completable, Starfall damaged-only, Emberreach unknown, Frostline complete", async () => {
  const aurora = await byId(AURORA);
  assert.equal(aurora.cache_complete_pct, 86);
  assert.ok(aurora.cache_missing_chunks > 0);
  assert.equal(isCompletable(aurora), true);
  const starfall = await byId(STARFALL);
  assert.equal(starfall.cache_complete_pct, 99);
  assert.equal(starfall.cache_missing_chunks, 0);
  const ember = await byId(EMBERREACH);
  assert.equal(ember.cache_complete_pct, null);
  assert.equal(ember.cache_missing_chunks, null);
  assert.ok(ember.size_bytes > 0, "bytes on disk, figure unknown");
  const frost = await byId(FROSTLINE);
  assert.equal(frost.cache_complete_pct, 100, "the never-played demo game stays complete (its pill keeps showing)");
  // The null rules: pct null <=> every other own field null.
  for (const g of await games()) {
    const nulls = ["cache_expected_bytes", "cache_present_bytes", "cache_complete_checked_at", "cache_missing_chunks"].map(
      (f) => g[f] === null,
    );
    assert.ok(nulls.every((n) => n === (g.cache_complete_pct === null)), `${g.appid}: fields null together`);
    if (!(g.size_bytes > 0)) assert.equal(g.cache_complete_pct, null, `${g.appid}: nothing on disk -> null`);
  }
});

test("demo cards: Aurora 'Partially cached 86%', the Starfall group 'Partially cached 99%'", async () => {
  const list = await games();
  const jobs = await demoRequest("GET", "/v1/jobs");
  const live = (appid) => jobs.find((j) => j.appid === appid && ["running", "paused"].includes(j.status));
  const { rows } = groupGames(list);
  const word = (appid) => {
    const r = rows.find((x) => x.appid === appid);
    return cardDisplay(r, dispKind(r, live(appid)), STATUS_LABEL).word;
  };
  assert.equal(word(AURORA), "Partially cached 86%");
  assert.equal(word(STARFALL), "Partially cached 99%");
  assert.equal(word(EMBERREACH), "Update available");
  assert.equal(word(FROSTLINE), "Current");
});

test("demo Downloads 'Incomplete': Aurora and Starfall (Driftwood is in the queue)", async () => {
  const list = await games();
  const jobs = await demoRequest("GET", "/v1/jobs");
  assert.deepEqual(
    pendingIncomplete(list, jobs).map((g) => g.appid),
    [AURORA, STARFALL],
  );
  assert.ok((await byId(DRIFTWOOD)).cache_complete_pct < 100, "mid-download figure");
});

test("GET /v1/games?incomplete=true lists known figures below 100", async () => {
  const rows = await demoRequest("GET", "/v1/games", { params: { incomplete: "true" } });
  assert.deepEqual(
    rows.map((g) => g.appid).sort((a, b) => a - b),
    [DRIFTWOOD, AURORA, STARFALL].sort((a, b) => a - b),
  );
});

test("MUTATION TARGET -- POST /v1/prefill/verify: needs_force first, one job, and the finished job re-measures complete", async () => {
  const [ref] = await demoRequest("POST", "/v1/prefill/verify", { body: { appids: [AURORA] } });
  assert.equal(ref.deduplicated, false);
  assert.equal((await byId(AURORA)).needs_force, true);
  for (let i = 0; i < 5; i++) await demoRequest("GET", "/v1/jobs");
  const after = await byId(AURORA);
  assert.equal(after.cache_complete_pct, 100);
  assert.equal(after.cache_missing_chunks, 0);
  assert.equal(after.needs_force, false);
});

test("a finished forced run does not repair wrong-size chunks (Starfall stays at 99%)", async () => {
  await demoRequest("POST", "/v1/prefill/verify", { body: { appids: [STARFALL] } });
  for (let i = 0; i < 5; i++) await demoRequest("GET", "/v1/jobs");
  assert.equal((await byId(STARFALL)).cache_complete_pct, 99);
});

test("verify validation mirrors POST /v1/prefill (non-empty, ids >= 1, nothing queued on a bad id)", async () => {
  await assert.rejects(demoRequest("POST", "/v1/prefill/verify", { body: { appids: [] } }), /non-empty/);
  const before = (await demoRequest("GET", "/v1/jobs", { params: { range: "all", limit: 200 } })).length;
  await assert.rejects(demoRequest("POST", "/v1/prefill/verify", { body: { appids: [AURORA, 0] } }), /invalid appid/);
  assert.equal((await demoRequest("GET", "/v1/jobs", { params: { range: "all", limit: 200 } })).length, before);
});

test("MUTATION TARGET -- POST /v1/prefill/incomplete: completable games only (never the damaged-only one), body ignored", async () => {
  const refs = await demoRequest("POST", "/v1/prefill/incomplete", { body: { appids: [STARFALL] } });
  const ids = refs.map((r) => r.appid);
  assert.ok(ids.includes(AURORA));
  assert.ok(!ids.includes(STARFALL), "a forced run cannot fix wrong-size chunks");
  assert.ok(ids.includes(DRIFTWOOD), "the server's selection ignores jobs: dedupes onto the running one");
  assert.equal(refs.find((r) => r.appid === DRIFTWOOD).deduplicated, true);
  assert.equal((await byId(AURORA)).needs_force, true);
});
