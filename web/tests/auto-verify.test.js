// WP AUTO-VERIFY-1 (operator decision 2026-10-18, Weg A): "Complete cached
// games automatically" in Settings, the Downloads note that follows it, and
// the web side of the delta-only scenario (api/tests/test_auto_verify_1.py
// pins the measurement): a cached game with only an update delta on disk
// reads "Partially cached", is completable, and its detail line carries the
// last disk check ("checked <time>"), the "verified" signal.
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  AUTO_COMPLETE_OFF_NOTE,
  cardDisplay,
  completenessLine,
  incompleteScheduleNote,
  isCompletable,
  pendingIncomplete,
} from "../js/lib/cache-completeness.js";
import { MANUAL_ONLY_NOTE } from "../js/lib/updates-available.js";
import { FIELD_META, fieldMeta, placeEntries } from "../js/lib/settings-layout.js";
import { STATUS_LABEL } from "../js/components/status-icon.js";
import { KIND } from "../js/lib/game-status.js";
import { CONFIG_DEFAULT_AUTO_COMPLETE_CACHED, demoRequest, resetDemoData } from "../js/demo-data.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const configPy = readFileSync(path.join(__dirname, "..", "..", "api", "vault_api", "config.py"), "utf8");

beforeEach(() => {
  resetDemoData();
});

// ---------------------------------------------------------------------------
// The delta-only game as GET /v1/games reports it (numbers from the API test).
// ---------------------------------------------------------------------------

function deltaOnlyRow() {
  return {
    appid: 730,
    name: "Delta Game",
    status: "cached",
    size_bytes: 1000,
    update_available: false,
    cache_complete_pct: 35,
    cache_expected_bytes: 1400,
    cache_present_bytes: 500,
    cache_missing_chunks: 9,
    cache_complete_checked_at: "2026-10-18T03:10:00Z",
  };
}

test("a delta-only game reads Partially cached on the card and is completable", () => {
  const row = deltaOnlyRow();
  const d = cardDisplay(row, KIND.CACHED, STATUS_LABEL);
  assert.equal(d.word, "Partially cached 35%");
  assert.equal(d.icon, "partial");
  assert.equal(isCompletable(row), true);
  assert.deepEqual(pendingIncomplete([row], []).map((g) => g.appid), [730]);
});

test("the detail line carries the last disk check as the verified signal", () => {
  const line = completenessLine(deltaOnlyRow());
  assert.match(line, /^35% complete — /);
  assert.match(line, / · checked /);
});

// ---------------------------------------------------------------------------
// Downloads note follows the switch.
// ---------------------------------------------------------------------------

test("the incomplete note says manual only while automatic completion is off", () => {
  const on = { enabled: true, window: "03:00-07:00", auto_complete_cached: true };
  assert.match(incompleteScheduleNote(on), /^These complete automatically in the schedule window /);
  assert.equal(incompleteScheduleNote({ ...on, auto_complete_cached: false }), AUTO_COMPLETE_OFF_NOTE);
  assert.equal(AUTO_COMPLETE_OFF_NOTE, "Manual only — automatic completion is off in Settings.");
  // An older server without the field kept completing in the window.
  const { auto_complete_cached: _drop, ...old } = on;
  assert.match(incompleteScheduleNote(old), /^These complete automatically/);
  // No window wins: manual only for the window reason.
  assert.equal(incompleteScheduleNote({ enabled: false, window: null, auto_complete_cached: false }), MANUAL_ONLY_NOTE);
});

// ---------------------------------------------------------------------------
// Settings field.
// ---------------------------------------------------------------------------

test("the switch sits in the Download window section as an Off/On seg", () => {
  const placed = placeEntries([
    { key: "schedule_window", expert: false },
    { key: "auto_complete_cached", expert: false },
  ]);
  assert.deepEqual(placed.sections.window.map((e) => e.key), ["schedule_window", "auto_complete_cached"]);
  const meta = fieldMeta({ key: "auto_complete_cached" });
  assert.equal(meta.label, "Complete cached games automatically");
  assert.equal(meta.kind, "segs");
  assert.deepEqual(meta.options, [["false", "Off"], ["true", "On"]]);
  assert.match(FIELD_META.auto_complete_cached.help, /fetch the missing chunks/);
});

test("CONFIG_DEFAULT_AUTO_COMPLETE_CACHED matches config.py's DEFAULT_AUTO_COMPLETE_CACHED", () => {
  const m = configPy.match(/^DEFAULT_AUTO_COMPLETE_CACHED\s*(?::\s*\w+\s*)?=\s*(True|False)\b/m);
  assert.ok(m, "GRAMMAR DRIFT: no 'DEFAULT_AUTO_COMPLETE_CACHED = True|False' in config.py — widen this regex");
  assert.equal(
    CONFIG_DEFAULT_AUTO_COMPLETE_CACHED,
    m[1] === "True",
    "VALUE DRIFT: update CONFIG_DEFAULT_AUTO_COMPLETE_CACHED in demo-data.js",
  );
});

test("demo: the setting is editable, defaults on, and /v1/schedule follows it", async () => {
  const settings = await demoRequest("GET", "/v1/settings");
  const entry = settings.settings.find((e) => e.key === "auto_complete_cached");
  assert.ok(entry, "demo settings lack auto_complete_cached");
  assert.equal(entry.effective, true);
  assert.equal(entry.env_var, "VAULT_AUTO_COMPLETE_CACHED");
  assert.equal(entry.editable, true);
  assert.equal((await demoRequest("GET", "/v1/schedule")).auto_complete_cached, true);

  await demoRequest("PATCH", "/v1/settings", { body: { auto_complete_cached: "off" } });
  assert.equal((await demoRequest("GET", "/v1/schedule")).auto_complete_cached, false);
  await assert.rejects(demoRequest("PATCH", "/v1/settings", { body: { auto_complete_cached: "maybe" } }));
});
