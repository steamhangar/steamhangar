/**
 * WP D10 COVER-CACHE-1: covers stored by vault-api (`cover_cached_url` on
 * `GET /v1/games` rows). The URL order and the error-driven fallback chain in
 * web/js/lib/cover-art.js, plus the card and detail wiring.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createFakeDom } from "./fake-dom.js";
import { cachedCoverPath, coverCandidates, wireCoverFallback } from "../js/lib/cover-art.js";

const SIG = "0123456789abcdef0123456789abcdef";
const CACHED = `/v1/covers/3527290/${SIG}`;
const HASHED =
  "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/3527290/" +
  "480bd879ac737921bfa2529a6fea15961267ad21/library_600x900.jpg?t=1790591892";
const LEGACY = "https://cdn.akamai.steamstatic.com/steam/apps/3527290/library_600x900.jpg";

test("cachedCoverPath accepts the exact same-origin path for this appid", () => {
  assert.equal(cachedCoverPath(3527290, CACHED), CACHED);
});

for (const bad of [
  `/v1/covers/440/${SIG}`, // another app
  `/v1/covers/3527290/${SIG.toUpperCase()}`,
  `/v1/covers/3527290/${SIG}/x`,
  `/v1/covers/3527290/${SIG}?x=1`,
  `/v1/covers/03527290/${SIG}`,
  `//evil.example/v1/covers/3527290/${SIG}`,
  `https://evil.example/v1/covers/3527290/${SIG}`,
  `/v1/covers/3527290/abc`,
  "javascript:alert(1)",
  "",
  null,
  42,
]) {
  test(`cachedCoverPath rejects ${JSON.stringify(bad)}`, () => {
    assert.equal(cachedCoverPath(3527290, bad), null);
  });
}

test("coverCandidates: stored copy, server CDN cover, legacy -- in that order", () => {
  assert.deepEqual(coverCandidates(3527290, HASHED, CACHED), [CACHED, HASHED, LEGACY]);
});

test("coverCandidates without a stored copy or server cover (older server, demo)", () => {
  assert.deepEqual(coverCandidates(3527290, HASHED, null), [HASHED, LEGACY]);
  assert.deepEqual(coverCandidates(3527290, null, undefined), [LEGACY]);
  assert.deepEqual(coverCandidates(3527290, undefined, CACHED), [CACHED, LEGACY]);
});

function fakeImg() {
  const listeners = [];
  return {
    src: "",
    addEventListener(type, fn) {
      if (type === "error") listeners.push(fn);
    },
    fail() {
      for (const fn of listeners) fn({ type: "error" });
    },
  };
}

test("MUTATION TARGET -- wireCoverFallback walks the chain on each error, then gives up once", () => {
  const img = fakeImg();
  let gaveUp = 0;
  wireCoverFallback(img, [CACHED, HASHED, LEGACY], () => (gaveUp += 1));
  assert.equal(img.src, CACHED);
  img.fail();
  assert.equal(img.src, HASHED);
  img.fail();
  assert.equal(img.src, LEGACY);
  assert.equal(gaveUp, 0);
  img.fail();
  assert.equal(gaveUp, 1);
  img.fail();
  assert.equal(gaveUp, 1);
});

test("wireCoverFallback with no URLs gives up at once", () => {
  let gaveUp = 0;
  wireCoverFallback(fakeImg(), [], () => (gaveUp += 1));
  assert.equal(gaveUp, 1);
});

// ---------------------------------------------------------------------
// Wiring: library card (fake DOM) and detail sheet (source scan)
// ---------------------------------------------------------------------

function noop() {}
function ctx() {
  return { picked: false, selecting: false, onOpen: noop, onLongPress: noop, onToggle: noop, onAction: noop };
}

async function cardImg(over) {
  const dom = createFakeDom();
  globalThis.document = dom.document;
  globalThis.window = dom.window;
  const { buildCard } = await import("../js/components/game-card.js");
  const g = {
    appid: 3527290,
    name: "Synthetic Game",
    status: "done",
    last_prefill_at: "2026-10-01T00:00:00Z",
    size_bytes: 500_000_000,
    needs_force: false,
    installed_on: [],
    ...over,
  };
  return buildCard(g, ctx()).querySelector("img.cover");
}

test("MUTATION TARGET -- buildCard loads the stored copy first and falls back to the CDN on error", async () => {
  const img = await cardImg({ cover_url: HASHED, cover_cached_url: CACHED });
  assert.equal(String(img.src), CACHED);
  img.dispatchEvent({ type: "error" });
  assert.equal(String(img.src), HASHED);
});

test("buildCard ignores a cover_cached_url for another app", async () => {
  const img = await cardImg({ cover_url: HASHED, cover_cached_url: `/v1/covers/440/${SIG}` });
  assert.equal(String(img.src), HASHED);
});

const here = path.dirname(fileURLToPath(import.meta.url));
const detailJs = readFileSync(path.join(here, "..", "js", "components", "game-detail-sheet.js"), "utf8").replace(
  /\r\n/g,
  "\n",
);

test("MUTATION TARGET -- the detail header passes cover_cached_url into buildMiniCover", () => {
  assert.match(
    detailJs,
    /state\.detail\?\.cover_cached_url \?\? gameLike\?\.cover_cached_url,\s*\)/,
  );
});
