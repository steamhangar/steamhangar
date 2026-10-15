/**
 * WP D19-1b: expected depots/size for a never-cached game. Pure helpers in
 * lib/depot-preview.js plus wiring pins for the sheet and the API client.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import {
  PREVIEW_STATE,
  PENDING_RETRY_MS,
  isReady,
  expectedSizeText,
  expectedDepotRows,
  expectedDlcDepotRows,
  wantsPreview,
} from "../js/lib/depot-preview.js";

const GB = 1024 ** 3;

function ready(extra = {}) {
  return {
    state: "ready",
    total_size_bytes: 12 * GB,
    total_download_bytes: 5 * GB,
    depots: [
      { depotid: 101, size_bytes: 10 * GB },
      { depotid: 102, size_bytes: null },
    ],
    dlc_depots: [{ depotid: 201, dlcappid: 900 }, { depotid: 202 }],
    ...extra,
  };
}

test("only a ready payload with depots is shown", () => {
  assert.equal(isReady(ready()), true);
  for (const s of ["off", "cached", "pending", "unavailable"]) {
    assert.equal(isReady(ready({ state: s })), false, s);
  }
  assert.equal(isReady(ready({ depots: [] })), false);
  assert.equal(isReady(null), false);
  assert.equal(isReady("ready"), false);
});

test("size line says expected and includes the download size", () => {
  const text = expectedSizeText(ready());
  assert.match(text, /^Expected size /);
  assert.match(text, /download /);
});

test("size line is null when nothing can be said", () => {
  assert.equal(expectedSizeText(ready({ state: "off" })), null);
  assert.equal(expectedSizeText(ready({ total_size_bytes: null })), null);
  assert.equal(expectedSizeText(null), null);
});

test("size line omits the download part when unknown", () => {
  const text = expectedSizeText(ready({ total_download_bytes: null }));
  assert.match(text, /^Expected size /);
  assert.doesNotMatch(text, /download/);
});

test("depot rows keep unsized depots with a null size, never 0", () => {
  const rows = expectedDepotRows(ready());
  assert.deepEqual(rows.map((r) => r.depotid), [101, 102]);
  assert.ok(rows[0].sizeText);
  assert.equal(rows[1].sizeText, null);
  assert.deepEqual(expectedDepotRows(ready({ state: "pending" })), []);
});

test("DLC depots are listed apart and tolerate a missing dlcappid", () => {
  assert.deepEqual(expectedDlcDepotRows(ready()), [
    { depotid: 201, dlcappid: 900 },
    { depotid: 202, dlcappid: null },
  ]);
  assert.deepEqual(expectedDlcDepotRows(ready({ dlc_depots: undefined })), []);
  assert.deepEqual(expectedDlcDepotRows(ready({ state: "off" })), []);
});

test("a preview is only wanted for a game without observed depots", () => {
  assert.equal(wantsPreview(null), true);
  assert.equal(wantsPreview({ depots: [] }), true);
  assert.equal(wantsPreview({ depots: [{ depotid: 1 }] }), false);
});

test("pending retries are bounded", () => {
  assert.ok(PENDING_RETRY_MS.length >= 1 && PENDING_RETRY_MS.length <= 6);
  assert.ok(PENDING_RETRY_MS.every((n) => n >= 1000));
  assert.equal(PREVIEW_STATE.READY, "ready");
});

// ---- wiring pins -------------------------------------------------------

const webDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const sheetJs = readFileSync(path.join(webDir, "js", "components", "game-detail-sheet.js"), "utf8");
const apiJs = readFileSync(path.join(webDir, "js", "api.js"), "utf8");

test("the API client calls the preview route", () => {
  assert.match(apiJs, /depotPreview:[^\n]*\/v1\/oracle\/\$\{encodeURIComponent\(appid\)\}\/preview/);
});

test("the sheet asks for a preview only after load, for games without depots, and drops stale answers", () => {
  assert.match(sheetJs, /wantsPreview\(state\.detail\)\s*\)\s*\{\s*loadPreview\(myGeneration, myAppid, 0\)/);
  const start = sheetJs.indexOf("async function loadPreview");
  const body = sheetJs.slice(start, sheetJs.indexOf("function buildExpectedBlock"));
  assert.match(body, /generation !== myGeneration \|\| state\.appid !== myAppid/);
  assert.match(body, /attempt < PENDING_RETRY_MS\.length/);
});

test("the expected block replaces the unknown note and sits in the not-tracked card", () => {
  assert.match(sheetJs, /const expected = buildExpectedBlock\(\);\s*if \(expected\) \{\s*wrap\.appendChild\(expected\)/);
  assert.match(sheetJs, /\[h3, p, buildStoreBlock\(\), expected, btn\]\.filter/);
});
