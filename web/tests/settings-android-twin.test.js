/**
 * Twin pin, web side (WP D16 Android): the Android SettingsLayout.kt carries
 * the same field wording, section titles and risk texts as
 * web/js/lib/settings-layout.js. The Kotlin test runs only in GitHub CI;
 * this file runs here and catches a web-only edit at once. A failure names
 * the key and says which side to fix. Also pins the D16 neutral-naming rule
 * (the upstream budget never says Steam).
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { FIELD_META, RISK_TEXT, RISK_FALLBACK_TEXT, SECTIONS, EXPERT_GROUPS } from "../js/lib/settings-layout.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const kt = readFileSync(
  path.join(__dirname, "..", "..", "app", "app", "src", "main", "java", "dev", "steamvault", "app", "ui", "settings", "logic", "SettingsLayout.kt"),
  "utf8",
);
const q = (s) => '"' + String(s).replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/\$/g, "\\$") + '"';

test("every web field label and help text appears verbatim in SettingsLayout.kt", () => {
  for (const [key, m] of Object.entries(FIELD_META)) {
    assert.ok(kt.includes(`${q(key)} to FieldMeta(${q(m.label)}, ${q(m.help)}`), `${key}: Kotlin FieldMeta differs from web FIELD_META (fix the side that is wrong)`);
  }
});

test("risk texts, section titles and expert groups match", () => {
  for (const [key, text] of Object.entries(RISK_TEXT)) assert.ok(kt.includes(`${q(key)} to ${q(text)}`), `risk ${key}`);
  assert.ok(kt.includes(`RISK_FALLBACK = ${q(RISK_FALLBACK_TEXT)}`));
  for (const s of SECTIONS) assert.ok(kt.includes(`SettingsSectionDef(${q(s.id)}, ${q(s.title)}, ${s.form})`), `section ${s.id}`);
  for (const g of EXPERT_GROUPS) {
    assert.ok(kt.includes(`${q(g.id)}, ${q(g.title)}`), `group ${g.id}`);
    for (const k of g.keys) assert.ok(kt.includes(q(k)), `group key ${k}`);
  }
});

test("D16 neutral naming holds in the Android twin too", () => {
  const m = kt.match(/"upstream_rate" to FieldMeta\([^\n]*/)[0];
  assert.match(m, /Upstream \(WAN\) rate cap/);
  assert.doesNotMatch(m, /steam|valve/i);
});

test("placeholders match the web FIELD_META placeholders", () => {
  for (const [key, m] of Object.entries(FIELD_META)) {
    if (!m.placeholder) continue;
    assert.ok(kt.includes(`${q(key)} to ${q(m.placeholder)}`), `placeholder ${key}`);
  }
});
