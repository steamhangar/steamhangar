/**
 * Steam tool apps in the web UI (WP API-FIX-4).
 *
 * `GET /v1/games` rows carry `tool_app: true` + `tool_app_name` for an app
 * vault-api never prefills (api/vault_api/tool_apps.py: 228980, "Steamworks
 * Common Redistributables"). Production 2026-10-04 showed it as
 * "App 228980 / Failed / Installed but not cached / Retry download". These
 * tests pin what the card, the detail sheet and the bulk bar show instead:
 *
 *   - the server's name, not "App 228980";
 *   - the neutral word "Steam tool package" and the "notinuse" dash glyph,
 *     never "Failed", even with `status: "error"` left by old failed jobs;
 *   - no download/retry action, on the card or in the sheet;
 *   - a muted card (`.card.tool`), not a hidden one;
 *   - no "installed but not cached" warning;
 *   - the detail sheet's note;
 *   - never a bulk download target (one 422 would fail the whole request).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { createFakeDom } from "./fake-dom.js";
import {
  KIND,
  dispKind,
  statusAction,
  installedBadgeState,
  INSTALLED_BADGE,
  isToolApp,
  statusIconKind,
  statusWordFor,
  gameDisplayName,
  TOOL_APP_STATE_WORD,
  TOOL_APP_NOTE,
} from "../js/lib/game-status.js";
import { classifyBulkSelection, buildBulkDownloadPlan } from "../js/lib/bulk-plan.js";
import { visibleGames } from "../js/lib/library-filters.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const webDir = path.join(__dirname, "..");

/** The production row: no vault name, an `error` status from the old
 * failed jobs, installed on a PC, no cached bytes of its own. */
function redist(over = {}) {
  return {
    appid: 228980,
    name: null,
    status: "error",
    last_prefill_at: null,
    last_manifest_check: null,
    depot_count: 0,
    size_bytes: null,
    needs_force: true,
    installed_on: [{ client_id: "gaming-pc", reported_at: "2026-10-04T08:00:00Z" }],
    tool_app: true,
    tool_app_name: "Steamworks Common Redistributables",
    ...over,
  };
}

function game(over = {}) {
  return {
    appid: 440,
    name: "Team Fortress 2",
    status: "error",
    last_prefill_at: null,
    last_manifest_check: null,
    depot_count: 1,
    size_bytes: null,
    needs_force: true,
    installed_on: [],
    tool_app: false,
    tool_app_name: null,
    ...over,
  };
}

// ---------------------------------------------------------------------
// Copy (literal, so a wording change is a deliberate edit here too)
// ---------------------------------------------------------------------

test("the tool-app words are the agreed copy", () => {
  assert.equal(TOOL_APP_STATE_WORD, "Steam tool package");
  assert.equal(TOOL_APP_NOTE, "Steam tool package — cached together with the games that use it.");
  assert.equal(KIND.TOOL, "tool");
});

// ---------------------------------------------------------------------
// lib/game-status.js
// ---------------------------------------------------------------------

test("isToolApp: only a strict true flag counts (an older server sends none)", () => {
  assert.equal(isToolApp(redist()), true);
  assert.equal(isToolApp(game()), false);
  assert.equal(isToolApp({ appid: 228980, name: null, status: "error" }), false);
  assert.equal(isToolApp({ appid: 228980, tool_app: "true" }), false);
  assert.equal(isToolApp(undefined), false);
});

test("MUTATION TARGET -- dispKind: a tool app is TOOL, never ERROR, even with status 'error'", () => {
  assert.equal(dispKind(redist(), undefined), KIND.TOOL);
  assert.equal(dispKind(redist({ status: "done", size_bytes: 1000 }), undefined), KIND.TOOL);
  // An ordinary app with the same fields still reads as failed.
  assert.equal(dispKind(game(), undefined), KIND.ERROR);
});

test("dispKind: a live job still wins for a tool app (honest 'Downloading')", () => {
  const job = { appid: 228980, type: "prefill", status: "running" };
  assert.equal(dispKind(redist(), job), KIND.RUNNING);
});

test("MUTATION TARGET -- statusAction: no download/retry for a tool app", () => {
  assert.equal(statusAction(redist(), undefined, false), null);
  assert.equal(statusAction(redist({ status: "idle" }), undefined, false), null);
  // Control: the ordinary failed game keeps its Retry.
  assert.deepEqual(statusAction(game(), undefined, false), { type: "download", title: "Retry download" });
});

test("MUTATION TARGET -- installedBadgeState: a tool app is never 'installed but not cached'", () => {
  assert.equal(installedBadgeState(redist()), INSTALLED_BADGE.CACHED);
  assert.equal(installedBadgeState(redist({ installed_on: [] })), INSTALLED_BADGE.NONE);
  assert.equal(installedBadgeState(game({ installed_on: redist().installed_on })), INSTALLED_BADGE.NOT_CACHED);
});

test("statusIconKind / statusWordFor: TOOL draws the neutral dash and reads 'Steam tool package'", () => {
  const labels = { none: "Not cached", error: "Failed", notinuse: "Not in use" };
  assert.equal(statusIconKind(KIND.TOOL), "notinuse");
  assert.equal(statusIconKind(KIND.ERROR), "error");
  assert.equal(statusWordFor(KIND.TOOL, labels), "Steam tool package");
  assert.equal(statusWordFor(KIND.ERROR, labels), "Failed");
  assert.equal(statusWordFor("nonsense", labels), "Not cached");
});

test("gameDisplayName: vault name, then the tool app's name, then App {appid}", () => {
  assert.equal(gameDisplayName(redist()), "Steamworks Common Redistributables");
  assert.equal(gameDisplayName(redist({ name: "  Redist (vault)  " })), "Redist (vault)");
  assert.equal(gameDisplayName(redist({ tool_app_name: "  " })), "App 228980");
  // tool_app_name is ignored without the flag.
  assert.equal(gameDisplayName(game({ name: null, tool_app_name: "X" })), "App 440");
});

test("library filters: a tool app is in 'All' only, never in 'Failed' or 'Not cached'", () => {
  const games = [redist(), game()];
  const ids = (key) => visibleGames(games, { query: "", filterKey: key, liveJobsByAppid: new Map() }).map((g) => g.appid);
  assert.deepEqual(ids("failed"), [440]);
  assert.deepEqual(ids("none"), []);
  assert.deepEqual(ids("all").sort(), [228980, 440]);
});

// ---------------------------------------------------------------------
// lib/bulk-plan.js
// ---------------------------------------------------------------------

test("MUTATION TARGET -- bulk: a picked tool app is never a download target", () => {
  const picked = [redist(), game()];
  const classification = classifyBulkSelection(picked, []);
  assert.deepEqual(classification.toolApps.map((g) => g.appid), [228980]);
  assert.deepEqual(classification.needsDownload.map((g) => g.appid), [440]);
  const plan = buildBulkDownloadPlan(classification, picked.length);
  assert.deepEqual(plan.primaryTargets, [440]);
  assert.equal(plan.primaryLabel, "Download 1 of 2");
  assert.equal(plan.note, "1 Steam tool package — cached together with the games that use it.");
});

test("bulk: only tool apps picked -> nothing to download, nothing re-downloadable", () => {
  const picked = [redist()];
  const plan = buildBulkDownloadPlan(classifyBulkSelection(picked, []), 1);
  assert.equal(plan.primaryEnabled, false);
  assert.deepEqual(plan.primaryTargets, []);
  assert.deepEqual(plan.secondaryTargets, []);
  assert.equal(plan.primaryLabel, "Nothing to download here");
});

test("bulk: a cached tool app is not offered for re-download either", () => {
  const cachedGame = game({ status: "done", size_bytes: 1000 });
  const picked = [redist({ status: "done", size_bytes: 1000 }), cachedGame];
  const plan = buildBulkDownloadPlan(classifyBulkSelection(picked, []), 2);
  assert.deepEqual(plan.secondaryTargets, [440]);
});

// ---------------------------------------------------------------------
// components/game-card.js (fake DOM)
// ---------------------------------------------------------------------

function noop() {}
function ctx(over = {}) {
  return { picked: false, selecting: false, onOpen: noop, onLongPress: noop, onToggle: noop, onAction: noop, ...over };
}

async function withCard(fn) {
  const dom = createFakeDom();
  globalThis.document = dom.document;
  globalThis.window = dom.window;
  const mod = await import("../js/components/game-card.js");
  return fn(mod);
}

test("MUTATION TARGET -- card: a tool app shows its name, the neutral word, no button, muted", () =>
  withCard(({ buildCard }) => {
    const card = buildCard(redist(), ctx());
    assert.equal(card.querySelector(".name").textContent, "Steamworks Common Redistributables");
    const state = card.querySelector(".state");
    assert.equal(state.textContent, "Steam tool package");
    assert.equal(state.classList.contains("tx-tool"), true);
    assert.equal(card.dataset.dk, "tool");
    assert.equal(card.classList.contains("tool"), true, "a tool app is shown muted, not hidden");
    assert.equal(card.querySelector("button.cappill"), null, "no download/retry pill button");
    assert.equal(card.querySelector("button.icnact"), null, "no download/retry icon button");
    assert.doesNotMatch(card.getAttribute("aria-label"), /Failed|Retry/);
    assert.match(card.getAttribute("aria-label"), /^Steamworks Common Redistributables — Steam tool package/);
    const badge = card.querySelector(".instbadge");
    assert.ok(badge);
    assert.equal(badge.classList.contains("warn"), false);
    assert.equal(badge.querySelector(".ibfull").textContent, "Installed on gaming-pc");
  }));

test("MUTATION TARGET -- card: both icons (pill and meta row) are the neutral notinuse dash", () =>
  withCard(({ buildCard }) => {
    const card = buildCard(redist(), ctx());
    const icons = card.querySelectorAll(".sic");
    assert.equal(icons.length, 2, "one icon in the pill, one in the meta row");
    for (const icon of icons) {
      assert.equal(icon.classList.contains("k-notinuse"), true, `expected k-notinuse, got ${icon.className}`);
    }
  }));

test("card: an ordinary failed game is unchanged (Failed + Retry, not muted)", () =>
  withCard(({ buildCard }) => {
    const card = buildCard(game(), ctx());
    assert.equal(card.querySelector(".state").textContent, "Failed");
    assert.ok(card.querySelector("button.cappill"));
    assert.equal(card.classList.contains("tool"), false);
  }));

// ---------------------------------------------------------------------
// components/game-detail-sheet.js (exported builder + wiring pins)
// ---------------------------------------------------------------------

async function withSheet(fn) {
  const dom = createFakeDom();
  dom.document.hidden = true; // parks store-singleton's poll loops, see game-detail-sheet-installed.test.js
  globalThis.document = dom.document;
  globalThis.window = dom.window;
  const mod = await import("../js/components/game-detail-sheet.js");
  return fn(mod);
}

test("MUTATION TARGET -- sheet: buildToolAppNote renders the neutral note for a tool app only", () =>
  withSheet(({ buildToolAppNote }) => {
    const note = buildToolAppNote(redist());
    assert.ok(note);
    assert.equal(note.textContent, "Steam tool package — cached together with the games that use it.");
    assert.equal(note.dataset.role, "tool-note");
    assert.equal(buildToolAppNote(game()), null);
  }));

test("sheet: no 'installed but not cached' note for a tool app", () =>
  withSheet(({ buildInstalledSection }) => {
    const section = buildInstalledSection(redist());
    assert.ok(section);
    assert.equal(section.querySelector('[data-role="installed-note"]'), null);
  }));

const detailJs = readFileSync(path.join(webDir, "js", "components", "game-detail-sheet.js"), "utf8");

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

test("MUTATION TARGET -- sheet wiring: render() appends the tool note; the download button stays behind statusAction", () => {
  const body = functionBody(detailJs, "function render() {");
  assert.ok(body, "render() not found in game-detail-sheet.js");
  assert.match(body, /const toolNote = buildToolAppNote\(gameLike\);\s*\n\s*if \(toolNote\) contentEl\.append\(toolNote\);/);
  // The Download/Retry button is only built from statusAction, which is
  // null for a tool app (pinned above).
  assert.match(body, /const action = statusAction\(gameLike, undefined, false\);\s*\n\s*if \(action\) contentEl\.append\(buildDownloadButton\(action\)\);/);
});

test("sheet wiring: the header uses the tool name, the dash glyph and the tool word", () => {
  const body = functionBody(detailJs, "function buildHeader(gameLike, liveJob) {");
  assert.ok(body, "buildHeader() not found in game-detail-sheet.js");
  assert.match(body, /toolAppName\(gameLike\)/);
  assert.match(body, /createStatusIcon\(statusIconKind\(kind\)/);
  assert.match(body, /statusWordFor\(kind, STATUS_LABEL\)/);
});
