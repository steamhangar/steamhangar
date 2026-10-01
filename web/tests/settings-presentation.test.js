import { test } from "node:test";
import assert from "node:assert/strict";
import {
  appliesText,
  sourceLabel,
  canReset,
  effectiveAsInputValue,
} from "../js/lib/settings-presentation.js";

test("appliesText covers all three real applies values distinctly", () => {
  const immediate = appliesText("immediately");
  const nextSweep = appliesText("next_sweep");
  const restart = appliesText("restart-required");
  assert.notEqual(immediate, nextSweep);
  assert.notEqual(nextSweep, restart);
  assert.notEqual(immediate, restart);
  assert.match(immediate, /immediately/i);
  assert.match(nextSweep, /sweep/i);
  assert.match(restart, /restart/i);
});

test("appliesText falls back honestly for an unknown value rather than lying", () => {
  assert.doesNotThrow(() => appliesText("something-new"));
  assert.notEqual(appliesText("something-new"), appliesText("immediately"));
});

test("sourceLabel covers db/env/default distinctly", () => {
  const labels = new Set([sourceLabel("db"), sourceLabel("env"), sourceLabel("default")]);
  assert.equal(labels.size, 3);
});

test("canReset: only a db-sourced, non-env-only entry can be reset", () => {
  assert.equal(canReset({ source: "db", env_only: false }), true);
  assert.equal(canReset({ source: "env", env_only: false }), false);
  assert.equal(canReset({ source: "default", env_only: false }), false);
  assert.equal(canReset({ source: "db", env_only: true }), false);
});

test("effectiveAsInputValue: null becomes blank (the disabled state, not the string 'null')", () => {
  assert.equal(effectiveAsInputValue({ key: "schedule_window", effective: null }), "");
});

test("effectiveAsInputValue: a list becomes a comma-joined string", () => {
  assert.equal(
    effectiveAsInputValue({ key: "webhook_events", effective: ["job.done", "job.error"] }),
    "job.done,job.error",
  );
});

test("effectiveAsInputValue: numbers and plain strings pass through as strings", () => {
  assert.equal(effectiveAsInputValue({ key: "schedule_interval_minutes", effective: 180 }), "180");
  assert.equal(effectiveAsInputValue({ key: "vault_name", effective: "vault-01" }), "vault-01");
});

test("effectiveAsInputValue: empty list becomes empty string, not '[]' or a stray comma", () => {
  assert.equal(effectiveAsInputValue({ key: "webhook_events", effective: [] }), "");
});

// WP WEB-FIX-1 (P2): views/settings.js dereferences `entryByKey(key).key`
// for eight keys unguarded; a response missing one used to throw AFTER
// loadSettings()'s try/catch and leave "Loading settings…" forever.
import { REQUIRED_SETTING_KEYS, missingSettingKeys } from "../js/lib/settings-presentation.js";

const FULL = {
  readonly: false,
  settings: REQUIRED_SETTING_KEYS.map((key) => ({ key, effective: null, source: "default", applies: "immediately", env_only: false })),
};

test("REQUIRED_SETTING_KEYS is the literal eight keys the Settings view builds controls for", () => {
  assert.deepEqual([...REQUIRED_SETTING_KEYS], [
    "vault_name",
    "schedule_window",
    "schedule_interval_minutes",
    "schedule_client_stale_days",
    "sweep_include_cached",
    "auto_gc",
    "webhook_url",
    "webhook_events",
  ]);
});

test("missingSettingKeys: a complete response reports nothing missing; extra keys are fine", () => {
  assert.deepEqual(missingSettingKeys(FULL), []);
  assert.deepEqual(missingSettingKeys({ ...FULL, settings: [...FULL.settings, { key: "db_path" }] }), []);
});

test("missingSettingKeys: names each absent key, in REQUIRED order", () => {
  const partial = { ...FULL, settings: FULL.settings.filter((e) => e.key !== "auto_gc" && e.key !== "vault_name") };
  assert.deepEqual(missingSettingKeys(partial), ["vault_name", "auto_gc"]);
});

test("missingSettingKeys: a malformed response (no settings array, null, entries without key) reports every key rather than throwing", () => {
  assert.deepEqual(missingSettingKeys({ readonly: false }), [...REQUIRED_SETTING_KEYS]);
  assert.deepEqual(missingSettingKeys(null), [...REQUIRED_SETTING_KEYS]);
  assert.deepEqual(missingSettingKeys({ settings: [null, {}] }), [...REQUIRED_SETTING_KEYS]);
});
