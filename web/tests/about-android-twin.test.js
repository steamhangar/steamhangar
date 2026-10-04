/**
 * Twin pin, web side (WP WEB-FIX-8): the Android app's Settings → About
 * strings (app/app/src/main/res/values/strings.xml, `settings_about_*`)
 * carry the same words as web/js/lib/about-view.js. The Android side pins
 * the same pairs as hand-transcribed literals
 * (AboutCrossFrontendContractTest.kt, runs only in GitHub CI); this file
 * runs here and catches a web-only edit at once. Every failure names the
 * resource and says which side to fix.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import {
  ABOUT_DISPLAY,
  COMPONENT_NOTES,
  CORE_NOTES,
  DASH,
  DASH_LABEL,
  DASH_NOTE,
} from "../js/lib/about-view.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const stringsXml = readFileSync(
  path.join(__dirname, "..", "..", "app", "app", "src", "main", "res", "values", "strings.xml"),
  "utf8",
);

/** One `<string name="...">` value, with Android's `\'` unescaped. */
function res(name) {
  const m = new RegExp(`<string name="${name}"[^>]*>(.*?)</string>`).exec(stringsXml);
  assert.ok(m, `strings.xml has no <string name="${name}"> — the Android twin lost it (fix the app, or re-point this pin)`);
  return m[1].replace(/\\'/g, "'");
}

const drift = (name) => `${name} differs between strings.xml and about-view.js — change BOTH (and AboutCrossFrontendContractTest.kt)`;

test("twin: every display state has the same word in the app", () => {
  const pairs = {
    ok: "settings_about_status_ok",
    unreachable: "settings_about_status_unreachable",
    not_in_use: "settings_about_status_not_in_use",
    not_checked: "settings_about_status_not_checked",
    check: "settings_about_status_check",
    not_reported: "settings_about_status_not_reported",
    not_applicable: "settings_about_status_not_applicable",
  };
  assert.deepEqual(Object.keys(pairs).sort(), Object.keys(ABOUT_DISPLAY).sort(), "a display state without an app twin");
  for (const [state, name] of Object.entries(pairs)) assert.equal(res(name), ABOUT_DISPLAY[state].word, drift(name));
});

test("twin: the dash, its spoken label and the dash note", () => {
  assert.equal(res("settings_about_dash"), DASH, drift("settings_about_dash"));
  assert.equal(res("settings_about_dash_label"), DASH_LABEL, drift("settings_about_dash_label"));
  assert.equal(res("settings_about_dash_note"), DASH_NOTE, drift("settings_about_dash_note"));
});

test("twin: vault-core's four comparison notes and every component note but vault-api's", () => {
  for (const [key, name] of [
    ["same_release", "settings_about_note_core_same_release"],
    ["mismatch", "settings_about_note_core_mismatch"],
    ["not_comparable", "settings_about_note_core_not_comparable"],
    ["not_reported", "settings_about_note_core_not_reported"],
  ]) {
    assert.equal(res(name), CORE_NOTES[key], drift(name));
  }
  // vault-api's note differs on purpose: "this web UI" vs "the web UI".
  for (const [component, name] of [
    ["vault-core", "settings_about_note_vault_core"],
    ["vault-runner", "settings_about_note_vault_runner"],
    ["steamprefill", "settings_about_note_steamprefill"],
    ["vault-proxy", "settings_about_note_vault_proxy"],
    ["vault-dns", "settings_about_note_vault_dns"],
  ]) {
    assert.equal(res(name), COMPONENT_NOTES[component], drift(name));
  }
});

test("MUTATION TARGET: no app About string shows the word 'unknown' either", () => {
  const all = [...stringsXml.matchAll(/<string name="(settings_about_[a-z_]+)"[^>]*>(.*?)<\/string>/g)];
  assert.ok(all.length >= 20, `expected the About strings, read ${all.length}`);
  for (const [, name, value] of all) assert.doesNotMatch(value, /unknown/i, `${name} says "unknown"`);
});
