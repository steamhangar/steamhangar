/**
 * WP WEB-FEAT-SUGGEST-2 (user decision 2026-10-13): the Phase 4h suggestions
 * panel is gone and its statements live where they are read. The decisions
 * are pinned in decision-support.test.js, notification-log.test.js and
 * game-card-never-played.test.js; this file pins the wiring of the two
 * surfaces without a headless harness of their own (the detail sheet and
 * the notification bell, both import-time singletons) with function-bounded
 * source scans, and that nothing of the panel is left behind.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const webDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (rel) => readFileSync(path.join(webDir, rel), "utf8");
const strip = (src) => src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/[^\n]*/g, "$1");

function functionBody(src, signature) {
  const idx = src.indexOf(signature);
  if (idx === -1) return null;
  const start = src.indexOf("{", idx + signature.length - 1);
  let depth = 1;
  let j = start + 1;
  while (depth > 0 && j < src.length) {
    if (src[j] === "{") depth++;
    else if (src[j] === "}") depth--;
    j++;
  }
  return src.slice(start + 1, j - 1);
}

test("detail sheet: the 'Last update downloaded' line is built with the facts and patched on every tick", () => {
  const src = strip(read("js/components/game-detail-sheet.js"));
  const sync = functionBody(src, "function syncUpdateDownloadedLine(el, gameLike) {");
  assert.ok(sync, "syncUpdateDownloadedLine missing");
  assert.match(sync, /const text = lastUpdateDownloadedText\(gameLike\);/);
  assert.match(sync, /el\.hidden = !text;/);
  const facts = functionBody(src, "function buildFactLines(gameLike) {");
  assert.match(facts, /updateDownloadedLine\.dataset\.role = "updatedl";/);
  assert.match(facts, /syncUpdateDownloadedLine\(updateDownloadedLine, gameLike\);/);
  assert.match(facts, /facts\.appendChild\(updateDownloadedLine\);/);
  const patch = functionBody(src, "function patchVolatile() {");
  assert.match(patch, /querySelector\('\[data-role="updatedl"\]'\)/);
  assert.match(patch, /syncUpdateDownloadedLine\(updateDownloadedEl, gameLike\)/);
});

test("bell: every games tick feeds staleUpdateChecks into syncUpdateCheckDue with the persisted acknowledgements, and prunes them", () => {
  const src = strip(read("js/components/notifications.js"));
  const tick = /store\.subscribe\("games", \(\{ items \}\) => \{([\s\S]*?)\n\}\);/.exec(src);
  assert.ok(tick, "games subscription missing");
  assert.match(tick[1], /const due = staleUpdateChecks\(items\);/);
  assert.match(tick[1], /syncUpdateCheckDue\(state\.log, due, \{[\s\S]*?seen: state\.updateDueSeen,/);
  assert.match(tick[1], /pruneUpdateCheckDueSeen\(state\.updateDueSeen, due\)/);
  assert.match(tick[1], /writeSeen\(seen\)/);
  assert.match(functionBody(src, "function openPanel() {"), /ackUpdateCheckDue\(state\.updateDueSeen, state\.log\)[\s\S]*?writeSeen\(seen\)/);
  assert.match(src, /updateDueSeen: readSeen\(\),/);
  const readSeen = functionBody(src, "function readSeen() {");
  assert.match(readSeen, /try \{[\s\S]*window\.localStorage\.getItem\(UPDATE_DUE_SEEN_KEY\)[\s\S]*\} catch \{/, "guarded read");
  assert.match(src, /const UPDATE_DUE_SEEN_KEY = "steamvault\.updateCheckDueSeen";/);
});

test("bell: the wording points at 'Check updates' only while that button exists (the update check's phase)", () => {
  const src = strip(read("js/components/notifications.js"));
  const hint = functionBody(src, "function updateCheckHint() {");
  assert.match(hint, /phase === UPDATE_PHASE\.IDLE \|\| phase === UPDATE_PHASE\.RUNNING\) return UPDATE_CHECK_HINT\.AVAILABLE;/);
  assert.match(hint, /phase === UPDATE_PHASE\.DISABLED\) return UPDATE_CHECK_HINT\.DISABLED;/);
  assert.match(hint, /return UPDATE_CHECK_HINT\.NONE;/);
  assert.match(functionBody(src, "function titleFor(entry) {"), /case "update_check_due":\s*return updateCheckDueWording\(entry\.items, gameNameFor, updateCheckHint\(\)\)\.title;/);
  assert.match(functionBody(src, "function textFor(entry) {"), /case "update_check_due":\s*return updateCheckDueWording\(entry\.items, gameNameFor, updateCheckHint\(\)\)\.text;/);
  assert.match(functionBody(strip(read("js/views/library.js")), "export function currentUpdateCheckPhase() {"), /return updateCheck\.snapshot\(\)\.phase;/);
});

test("the panel is gone: no component, no markup, no CSS, no storage keys, no app.js call", () => {
  assert.equal(existsSync(path.join(webDir, "js", "components", "decision-panel.js")), false);
  assert.doesNotMatch(read("index.html"), /decision-panel|dp-body|dp-collapse|dp-dismiss/);
  assert.doesNotMatch(read("css/app.css") + read("css/theme.css"), /decision-panel|has-decision-panel|\.dp-|--panel-w/);
  assert.doesNotMatch(read("js/app.js"), /createDecisionPanel|decision-panel/);
  const jsFiles = [];
  const walk = (dir) => {
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) walk(p);
      else if (e.name.endsWith(".js")) jsFiles.push(p);
    }
  };
  walk(path.join(webDir, "js"));
  for (const f of jsFiles) {
    assert.doesNotMatch(readFileSync(f, "utf8"), /decisionPanelDismissed|decisionPanelCollapsed|buildSuggestions/, f);
  }
});
