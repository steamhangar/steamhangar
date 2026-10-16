/**
 * WP D19-1c: store details on the game card. Pure helpers in
 * lib/store-details.js plus wiring pins for the sheet and the API client.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import {
  STORE_STATE,
  STORE_RETRY_MS,
  isReady,
  descriptionText,
  modeChips,
  genreList,
  byLine,
  ratingText,
  hasStoreContent,
} from "../js/lib/store-details.js";

function ready(extra = {}) {
  return {
    state: "ready",
    description: "A fast game.",
    genres: ["Action", "Indie"],
    developers: ["Some Studio"],
    release_date: "3 Mar, 2023",
    modes: ["single_player", "co_op", "vr", "bogus"],
    review_summary: "Overwhelmingly Positive",
    review_positive: 970,
    review_negative: 30,
    ...extra,
  };
}

test("only a ready payload is shown, every other state is silent", () => {
  assert.equal(isReady(ready()), true);
  for (const s of ["off", "pending", "unavailable"]) {
    const p = ready({ state: s });
    assert.equal(isReady(p), false, s);
    assert.equal(hasStoreContent(p), false, s);
    assert.equal(descriptionText(p), null, s);
    assert.deepEqual(modeChips(p), [], s);
    assert.equal(ratingText(p), null, s);
  }
  assert.equal(hasStoreContent(null), false);
  assert.equal(hasStoreContent("ready"), false);
});

test("chips use the known labels and drop unknown flags", () => {
  assert.deepEqual(modeChips(ready()), ["Single-player", "Co-op", "VR"]);
  assert.deepEqual(modeChips(ready({ modes: "x" })), []);
});

test("the overall rating is one line with the share and the count; no recent figure", () => {
  assert.equal(ratingText(ready()), "Overwhelmingly Positive (97% of 1,000 reviews)");
  assert.equal(ratingText(ready({ review_summary: null })), "97% of 1,000 reviews");
  assert.equal(ratingText(ready({ review_positive: 0, review_negative: 0 })), null);
  assert.equal(ratingText(ready({ review_positive: null })), null);
  assert.equal(ratingText(ready({ review_positive: "970" })), null);
});

test("missing fields are omitted, never placeholders", () => {
  const sparse = ready({ description: null, genres: [], developers: [], release_date: null, modes: [], review_positive: null });
  assert.equal(hasStoreContent(sparse), false); // a bare summary label without counts is not shown
  assert.equal(ratingText(sparse), null);
  assert.equal(byLine(sparse), null);
  assert.deepEqual(genreList(sparse), []);
  assert.equal(byLine(ready({ developers: [] })), "3 Mar, 2023");
  assert.equal(byLine(ready({ release_date: null })), "Some Studio");
  assert.equal(byLine(ready()), "Some Studio – 3 Mar, 2023");
});

test("the description is capped and trimmed on the client too", () => {
  assert.equal(descriptionText(ready({ description: "  hi  " })), "hi");
  const long = descriptionText(ready({ description: "x".repeat(2000) }));
  assert.equal(long.length, 600);
  assert.equal(descriptionText(ready({ description: "   " })), null);
  assert.equal(descriptionText(ready({ description: 5 })), null);
});

test("markup in a text field stays text: helpers return it verbatim, never as HTML", () => {
  const p = ready({ description: "<img src=x onerror=alert(1)>" });
  assert.equal(descriptionText(p), "<img src=x onerror=alert(1)>");
});

test("constants", () => {
  assert.equal(STORE_STATE.READY, "ready");
  assert.ok(STORE_RETRY_MS.length >= 1);
});

// ---- wiring pins -------------------------------------------------------

const webDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const sheetJs = readFileSync(path.join(webDir, "js", "components", "game-detail-sheet.js"), "utf8");
const apiJs = readFileSync(path.join(webDir, "js", "api.js"), "utf8");
const demoJs = readFileSync(path.join(webDir, "js", "demo-data.js"), "utf8");

test("the API client calls the store details route", () => {
  assert.match(apiJs, /storeDetails:[^\n]*\/v1\/store\/\$\{encodeURIComponent\(appid\)\}\/details/);
});

test("the sheet asks only after the card loaded and drops stale answers", () => {
  assert.match(sheetJs, /!state\.loadError\)\s*\{\s*loadStore\(myGeneration, myAppid, 0\)/);
  const start = sheetJs.indexOf("async function loadStore");
  const body = sheetJs.slice(start, sheetJs.indexOf("function buildStoreBlock"));
  assert.match(body, /generation !== myGeneration \|\| state\.appid !== myAppid/);
  assert.match(body, /attempt < STORE_RETRY_MS\.length/);
});

test("the store block renders third-party text with textContent only", () => {
  const start = sheetJs.indexOf("function buildStoreBlock");
  const body = sheetJs.slice(start, sheetJs.indexOf("/** The \"Expected ...\" block"));
  assert.ok(body.includes("textContent"));
  assert.doesNotMatch(body, /innerHTML|outerHTML|insertAdjacentHTML|createContextualFragment/);
  const lib = readFileSync(path.join(webDir, "js", "lib", "store-details.js"), "utf8");
  assert.doesNotMatch(lib, /innerHTML|document\./);
});

test("the demo has the env-only row and keeps the lookup off", () => {
  assert.match(demoJs, /key: "store_details_enabled", env_var: "VAULT_STORE_DETAILS", value: false/);
  assert.match(demoJs, /\/v1\\\/store\\\/\\d\+\\\/details\$\/\.test\(path\)\) \{\s*return \{ state: "off" \}/);
});

// WP CARD-LAYOUT-1: game block first, hangar blocks after.
test("render() puts the store block directly under the header, before the cache facts", () => {
  const body = sheetJs.slice(sheetJs.indexOf("function render()"));
  const at = (needle) => body.indexOf(needle);
  assert.ok(at("buildHeader(gameLike, liveJob)") > -1);
  assert.ok(at("buildHeader(gameLike, liveJob)") < at("buildStoreBlock()"));
  assert.ok(at("buildStoreBlock()") < at("buildFactLines(gameLike)"));
  assert.ok(at("buildFactLines(gameLike)") < at("buildCompletenessSection("));
  assert.ok(at("buildCompletenessSection(") < at("buildInstalledSection(gameLike)"));
  assert.ok(at("buildInstalledSection(gameLike)") < at("buildPackagesSection("));
  assert.ok(at("buildPackagesSection(") < at("buildDepotsSection()"));
  assert.ok(at("buildDepotsSection()") < at("buildDeleteButton()"));
  assert.ok(at("buildDeleteButton()") < at("renderGcSection()"));
  assert.equal(body.split("buildStoreBlock()").length - 1, 1);
});

test("the store facts are a second column on wide screens and stack on narrow ones", () => {
  const body = sheetJs.slice(sheetJs.indexOf("function buildStoreBlock"), sheetJs.indexOf("/** The \"Expected ...\" block"));
  assert.match(body, /facts\.className = "store-facts"/);
  assert.match(body, /store-nodesc/);
  const css = readFileSync(path.join(webDir, "css", "app.css"), "utf8");
  assert.doesNotMatch(css, /^\.store-details\s*\{[^}]*grid/m);
  const m = css.match(/@media \(min-width:600px\)\{\s*\.store-details:not\(\.store-nodesc\)\{[^}]*grid-template-columns:[^}]*\}/);
  assert.ok(m, "wide two-column rule inside @media (min-width:600px)");
});
