/**
 * WP API-FIX-5: server-resolved cover art (`cover_url` on `GET /v1/games`
 * rows) in web/js/lib/cover-art.js, plus the two wiring witnesses: the
 * library card (fake DOM) and the detail sheet's mini-cover (source scan,
 * same idiom as game-detail-sheet-installed.test.js).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createFakeDom } from "./fake-dom.js";
import { STEAM_ASSET_HOSTS, coverArtUrl, serverCoverUrl } from "../js/lib/cover-art.js";

const HASHED =
  "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/3527290/" +
  "480bd879ac737921bfa2529a6fea15961267ad21/library_600x900.jpg?t=1790591892";
const LEGACY_3527290 = "https://cdn.akamai.steamstatic.com/steam/apps/3527290/library_600x900.jpg";

test("STEAM_ASSET_HOSTS is exactly the two CSP img-src hosts (literal pin)", () => {
  assert.deepEqual([...STEAM_ASSET_HOSTS].sort(), ["cdn.akamai.steamstatic.com", "shared.akamai.steamstatic.com"]);
});

test("coverArtUrl prefers a valid server cover_url", () => {
  assert.equal(coverArtUrl(3527290, HASHED), HASHED);
  const legacyHost = "https://cdn.akamai.steamstatic.com/steam/apps/440/abc/library_600x900.jpg";
  assert.equal(coverArtUrl(440, legacyHost), legacyHost);
});

test("coverArtUrl falls back to the legacy path when cover_url is missing (older server, owned-only row)", () => {
  assert.equal(coverArtUrl(3527290), LEGACY_3527290);
  assert.equal(coverArtUrl(3527290, null), LEGACY_3527290);
  assert.equal(coverArtUrl(3527290, undefined), LEGACY_3527290);
});

for (const bad of [
  "http://shared.akamai.steamstatic.com/store_item_assets/x.jpg",
  "https://evil.example/x.jpg",
  "https://shared.akamai.steamstatic.com.evil.example/x.jpg",
  "https://avatars.steamstatic.com/x.jpg",
  "https://SHARED.akamai.steamstatic.com/x.jpg",
  "https://u:p@shared.akamai.steamstatic.com/x.jpg",
  "https://shared.akamai.steamstatic.com:8443/x.jpg",
  "https://shared.akamai.steamstatic.com/x.jpg#f",
  "https://shared.akamai.steamstatic.com/x y.jpg",
  "https://shared.akamai.steamstatic.com\\x.jpg",
  "javascript:alert(1)",
  "data:image/png;base64,AAAA",
  "https://shared.akamai.steamstatic.com/" + "a".repeat(600),
  "",
  42,
  {},
]) {
  test(`serverCoverUrl rejects ${JSON.stringify(bad).slice(0, 60)} and coverArtUrl falls back`, () => {
    assert.equal(serverCoverUrl(bad), null);
    assert.equal(coverArtUrl(3527290, bad), LEGACY_3527290);
  });
}

// ---------------------------------------------------------------------
// Wiring: library card (fake DOM). Assert on text values only (LEARNINGS:
// node assertions on fake-DOM nodes can OOM the runner).
// ---------------------------------------------------------------------

function noop() {}
function ctx() {
  return { picked: false, selecting: false, onOpen: noop, onLongPress: noop, onToggle: noop, onAction: noop };
}
function game(over = {}) {
  return {
    appid: 3527290,
    name: "Synthetic Game",
    status: "done",
    last_prefill_at: "2026-10-01T00:00:00Z",
    size_bytes: 500_000_000,
    needs_force: false,
    installed_on: [],
    ...over,
  };
}

async function cardImgSrc(g) {
  const dom = createFakeDom();
  globalThis.document = dom.document;
  globalThis.window = dom.window;
  const { buildCard } = await import("../js/components/game-card.js");
  const img = buildCard(g, ctx()).querySelector("img.cover");
  return img === null ? "no img" : String(img.src);
}

test("MUTATION TARGET -- buildCard uses the row's cover_url for the cover image", async () => {
  assert.equal(await cardImgSrc(game({ cover_url: HASHED })), HASHED);
});

test("buildCard keeps the legacy cover when the row has no usable cover_url", async () => {
  assert.equal(await cardImgSrc(game()), LEGACY_3527290);
  assert.equal(await cardImgSrc(game({ cover_url: "https://evil.example/x.jpg" })), LEGACY_3527290);
});

// ---------------------------------------------------------------------
// Wiring: detail sheet mini-cover (source scan)
// ---------------------------------------------------------------------

const here = path.dirname(fileURLToPath(import.meta.url));
const detailJs = readFileSync(path.join(here, "..", "js", "components", "game-detail-sheet.js"), "utf8").replace(
  /\r\n/g,
  "\n",
);

test("MUTATION TARGET -- the detail header passes the server cover_url into buildMiniCover", () => {
  assert.match(
    detailJs,
    /buildMiniCover\(state\.appid,\s*state\.detail\?\.cover_url \?\? gameLike\?\.cover_url\)/,
    "buildHeader no longer hands cover_url to buildMiniCover: new games' detail covers fall back to the 404 legacy path",
  );
  assert.match(
    detailJs,
    /function buildMiniCover\(appid, coverUrl\) \{[\s\S]*?img\.src = coverArtUrl\(appid, coverUrl\);/,
    "buildMiniCover no longer feeds coverUrl into coverArtUrl",
  );
});
