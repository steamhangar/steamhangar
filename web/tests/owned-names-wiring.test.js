/**
 * Source-text wiring pins for WP WEB-FIX-4's owned-name fallback in the
 * surfaces that have no headless harness of their own (the notification
 * panel, the detail sheet) plus the one-loader rule. Same technique as
 * game-detail-sheet-installed.test.js: each pin is bounded to one function
 * body. The behaviour itself (title order, fill-only) is pinned in
 * job-failure.test.js, downloads-owned-names.test.js and
 * decision-panel-wiring.test.js.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const jsDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "js");
const read = (rel) => readFileSync(path.join(jsDir, rel), "utf8");

function functionBody(src, signature) {
  const idx = src.indexOf(signature);
  if (idx === -1) return null;
  const start = src.indexOf("{", idx);
  let depth = 1;
  let j = start + 1;
  while (depth > 0 && j < src.length) {
    if (src[j] === "{") depth++;
    else if (src[j] === "}") depth--;
    j++;
  }
  return src.slice(start + 1, j - 1);
}

test("notification panel: gameNameFor falls back to the owned list through appTitle", () => {
  const body = functionBody(read("components/notifications.js"), "function gameNameFor(appid) {");
  assert.ok(body, "gameNameFor not found");
  assert.match(body, /appTitle\(appid, game && game\.name, ownedNamesByAppid\(ownedLibrary\.current\(\)\.games\)\.get\(appid\)\)/);
});

test("detail sheet: openDetail takes the owned name when the opener has none; both delete plans use the named map", () => {
  const src = read("components/game-detail-sheet.js");
  const open = functionBody(src, "export function openDetail(appid, name) {");
  assert.ok(open, "openDetail not found");
  assert.match(open, /state\.name = name \|\| ownedNamesByAppid\(ownedLibrary\.current\(\)\.games\)\.get\(appid\) \|\| null;/);
  assert.equal((src.match(/const gamesByAppid = namedGamesByAppid\(\);/g) || []).length, 2);
  assert.equal((src.match(/new Map\(state\.games\.map/g) || []).length, 0, "no unnamed vault map left");
  assert.match(functionBody(src, "function namedGamesByAppid() {"), /fillMissingNames\(state\.games, ownedLibrary\.current\(\)\.games\)/);
});

test("one owned-list loader per page: the Library uses owned-singleton.js, app.js hands it to the decision panel", () => {
  const library = read("views/library.js");
  assert.doesNotMatch(library, /createOwnedLibraryLoader\(/, "a second, view-local loader would split the list again");
  assert.match(library, /import \{ ownedLibrary \} from "\.\.\/owned-singleton\.js";/);
  assert.match(functionBody(library, "export function renderLibrary() {"), /ownedLibrary\.load\(\);/);
  assert.match(read("app.js"), /ownedNames: ownedLibrary,/);
});
