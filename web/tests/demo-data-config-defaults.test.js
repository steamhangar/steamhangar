/**
 * Cross-language drift guard (2026-08-22, ADR-0014 fix round): `web/js/
 * demo-data.js` cannot import `api/vault_api/config.py`'s Python constants
 * (this suite runs under plain Node — no cross-language plumbing exists,
 * and this file deliberately does not invent any), but it CAN read that
 * module's source as plain text, the same "structural static analysis of
 * source text" technique `css-hygiene.test.js` already uses for CSS/JS.
 *
 * Why this test exists: `demo-data.js`'s `SETTINGS_BASE.auto_gc`/
 * `.sweep_include_cached` fixture rows shipped with `"off"`/`false` for
 * months, which was correct — until ADR-0014 (2026-08-22) flipped
 * `config.py`'s `DEFAULT_AUTO_GC`/`DEFAULT_SWEEP_INCLUDE_CACHED` to
 * `"execute"`/`true` and the fixture was not updated in the same change,
 * silently teaching a wrong default on exactly the surface used for demo
 * screenshots (docs/LEARNINGS.md: "demo fixtures are a shipped surface").
 * This test makes that class of drift loud on every `node --test` run
 * instead of quiet until the next person happens to compare the two files
 * by hand.
 *
 * **Two DIFFERENT failure modes, two DIFFERENT correct fixes (review round
 * 1, S3) — do not conflate them:**
 *
 *   1. **Value drift**: the regex below still matches config.py just fine,
 *      but resolves to a DIFFERENT value than `demo-data.js`'s exported
 *      `CONFIG_DEFAULT_AUTO_GC`/`CONFIG_DEFAULT_SWEEP_INCLUDE_CACHED`. This
 *      is the ADR-0014 case this guard exists for — the fix belongs in
 *      `demo-data.js`'s exported constants, never in this file's regexes.
 *   2. **Grammar drift**: the regex below does NOT match config.py at all —
 *      an innocent reformatting (a type annotation, single quotes instead
 *      of double, the literal inlined directly instead of through the
 *      `AUTO_GC_*` indirection) changed the SHAPE of the assignment without
 *      changing its meaning. This is NOT a case where `demo-data.js` is
 *      wrong, and the previous version of this file's error message
 *      ("has it been renamed?") wrongly pointed the reader at editing
 *      `demo-data.js` regardless of which of these two happened — the one
 *      fix it explicitly forbade (updating the regex here) is exactly the
 *      correct one for THIS failure mode. Each assertion below reports
 *      which of the two it hit, and only failure mode 1 says "edit
 *      demo-data.js"; failure mode 2 says to update the regex/extractor in
 *      THIS file instead.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { CONFIG_DEFAULT_AUTO_GC, CONFIG_DEFAULT_SWEEP_INCLUDE_CACHED } from "../js/demo-data.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const configPyPath = path.join(__dirname, "..", "..", "api", "vault_api", "config.py");
const configPy = readFileSync(configPyPath, "utf8");

// Tolerates the reformattings most likely to happen without changing
// meaning: an optional type annotation (`NAME: str = ...`) and either quote
// style. Still a real grammar (not "anything goes") — a genuine rename or a
// multi-line assignment still fails to match, which is intentional: THAT
// class of change is significant enough to deserve a human updating this
// extractor on purpose, not a regex broad enough to shrug it off silently.
function extractPythonStringConstants(source, namePattern) {
  const out = new Map();
  const re = new RegExp(`^(${namePattern})\\s*(?::\\s*\\w+\\s*)?=\\s*["']([^"']*)["']`, "gm");
  let m;
  while ((m = re.exec(source))) out.set(m[1], m[2]);
  return out;
}

test("CONFIG_DEFAULT_AUTO_GC matches api/vault_api/config.py's DEFAULT_AUTO_GC, whatever it currently is", () => {
  // config.py assigns DEFAULT_AUTO_GC through indirection (`= AUTO_GC_EXECUTE`,
  // an identifier), never a literal string — but tolerate the direct-literal
  // shape too (`= "execute"`) since that is also meaning-preserving grammar
  // drift, not a rename.
  const pointerMatch = configPy.match(/^DEFAULT_AUTO_GC\s*(?::\s*\w+\s*)?=\s*(.+?)\s*(?:#.*)?$/m);
  assert.ok(
    pointerMatch,
    "GRAMMAR DRIFT, not value drift: could not find any 'DEFAULT_AUTO_GC = ...' assignment in " +
      "config.py at all (not even a one-line one this regex could see). If config.py still " +
      "defines this constant, update THIS FILE's regex to match its new shape — do not touch " +
      "demo-data.js for this failure.",
  );
  const rhs = pointerMatch[1].trim();
  const literalMatch = rhs.match(/^["']([^"']*)["']$/);
  let realDefault;
  if (literalMatch) {
    realDefault = literalMatch[1];
  } else {
    const autoGcConstants = extractPythonStringConstants(configPy, "AUTO_GC_\\w+");
    realDefault = autoGcConstants.get(rhs);
    assert.ok(
      realDefault !== undefined,
      `GRAMMAR DRIFT, not value drift: config.py's DEFAULT_AUTO_GC points at ${JSON.stringify(rhs)}, ` +
        "which this file's AUTO_GC_* extractor could not resolve to a string constant. Update THIS " +
        "FILE's extractor (the assignment shape it expects has changed) — do not touch demo-data.js " +
        "for this failure.",
    );
  }
  assert.equal(
    CONFIG_DEFAULT_AUTO_GC,
    realDefault,
    `VALUE DRIFT: api/vault_api/config.py's DEFAULT_AUTO_GC is now ${JSON.stringify(realDefault)} but ` +
      `web/js/demo-data.js's CONFIG_DEFAULT_AUTO_GC is still ${JSON.stringify(CONFIG_DEFAULT_AUTO_GC)} — ` +
      "update the exported constant in demo-data.js to match. Do not edit this file's regex for this failure.",
  );
});

test("CONFIG_DEFAULT_SWEEP_INCLUDE_CACHED matches api/vault_api/config.py's DEFAULT_SWEEP_INCLUDE_CACHED, whatever it currently is", () => {
  const pointerMatch = configPy.match(/^DEFAULT_SWEEP_INCLUDE_CACHED\s*(?::\s*\w+\s*)?=\s*(True|False)\b/m);
  assert.ok(
    pointerMatch,
    "GRAMMAR DRIFT, not value drift: could not find a 'DEFAULT_SWEEP_INCLUDE_CACHED = True|False' " +
      "assignment in config.py at all. If config.py still defines this constant, update THIS FILE's " +
      "regex to match its new shape — do not touch demo-data.js for this failure.",
  );
  const realDefault = pointerMatch[1] === "True";
  assert.equal(
    CONFIG_DEFAULT_SWEEP_INCLUDE_CACHED,
    realDefault,
    `VALUE DRIFT: api/vault_api/config.py's DEFAULT_SWEEP_INCLUDE_CACHED is now ${realDefault} but ` +
      `web/js/demo-data.js's CONFIG_DEFAULT_SWEEP_INCLUDE_CACHED is still ${CONFIG_DEFAULT_SWEEP_INCLUDE_CACHED} — ` +
      "update the exported constant in demo-data.js to match. Do not edit this file's regex for this failure.",
  );
});

test("MUTATION PIN: this test actually reads config.py's real, current text, not a cached/hardcoded copy", () => {
  // If the two regexes above were ever replaced with hardcoded expected
  // strings instead of parsing `configPy`, this file's own source-reading
  // machinery would go unexercised and the two tests above would degrade
  // into "demo-data.js agrees with itself" — worthless. Assert the raw
  // source actually contains the constant NAMES at all, so a typo'd path
  // (silently reading an empty/wrong file) fails loudly here instead of
  // both tests above vacuously passing against `undefined`/`""`.
  // Same type-annotation tolerance as the two real regexes above (`extract
  // PythonStringConstants` and the two `pointerMatch`es) — without it, a
  // PEP-484 reformatting (`DEFAULT_AUTO_GC: str = ...`) makes THIS the only
  // failing assertion in the file, with a bare, unlabelled regex mismatch
  // that names neither VALUE nor GRAMMAR drift — exactly the guidance-free
  // failure mode S3 exists to remove from the other two tests (review round
  // 2 polish).
  assert.match(configPy, /DEFAULT_AUTO_GC\s*(?::\s*\w+\s*)?=/);
  assert.match(configPy, /DEFAULT_SWEEP_INCLUDE_CACHED\s*(?::\s*\w+\s*)?=/);
});

// WP D16-WEB: the demo's download window is the shipped default (D16b moved
// it from compose into config.py), not a demo-only window.
test("demo schedule_window default equals config.py's DEFAULT_SCHEDULE_WINDOW", async () => {
  const m = /^DEFAULT_SCHEDULE_WINDOW\s*=\s*"([^"]+)"/m.exec(configPy);
  assert.ok(m, "GRAMMAR drift: DEFAULT_SCHEDULE_WINDOW is no longer a plain string literal — widen this regex");
  const { demoRequest, resetDemoData } = await import("../js/demo-data.js");
  resetDemoData();
  const out = await demoRequest("GET", "/v1/settings");
  const row = out.settings.find((s) => s.key === "schedule_window");
  assert.equal(row.fallback, m[1], "VALUE drift: update DEMO_SETTING_SPECS schedule_window default in demo-data.js");
});
