import { test } from "node:test";
import assert from "node:assert/strict";
import { buildSettingsPatch } from "../js/lib/settings-diff.js";

const ENTRIES = [
  { key: "vault_name", effective: "vault-01", source: "default", applies: "restart-required", env_only: false },
  { key: "schedule_window", effective: null, source: "default", applies: "next_sweep", env_only: false },
  { key: "schedule_interval_minutes", effective: 180, source: "default", applies: "next_sweep", env_only: false },
  { key: "auto_gc", effective: "off", source: "env", applies: "immediately", env_only: false },
  { key: "sweep_include_cached", effective: true, source: "env", applies: "next_sweep", env_only: false },
  { key: "webhook_url", effective: "", source: "default", applies: "restart-required", env_only: false },
  {
    key: "webhook_events",
    effective: ["client.bypass_resolved", "client.bypass_suspected", "job.cancelled", "job.done", "job.error"],
    source: "default",
    applies: "restart-required",
    env_only: false,
  },
  { key: "db_path", effective: "/data/vault.db", source: "env", applies: "restart-required", env_only: true },
];

test("a field never touched by the user is never in the body", () => {
  const body = buildSettingsPatch(ENTRIES, { vault_name: { value: "vault-02" } });
  assert.deepEqual(Object.keys(body), ["vault_name"]);
});

test("MUTATION PIN: a touched field whose value equals the current effective value is dropped, not sent", () => {
  const body = buildSettingsPatch(ENTRIES, {
    vault_name: { value: "vault-01" }, // identical to entries' effective
    schedule_interval_minutes: { value: "180" }, // number vs string, same value
  });
  assert.deepEqual(body, {});
});

test("a genuinely changed value is included, sent as the raw draft value", () => {
  const body = buildSettingsPatch(ENTRIES, { schedule_interval_minutes: { value: "90" } });
  assert.deepEqual(body, { schedule_interval_minutes: "90" });
});

test("reset only clears a key that currently has a db override", () => {
  const body = buildSettingsPatch(ENTRIES, { auto_gc: { reset: true } }); // source: env, no override
  assert.deepEqual(body, {});
});

test("reset on a db-sourced key sends null", () => {
  const overridden = ENTRIES.map((e) => (e.key === "auto_gc" ? { ...e, source: "db" } : e));
  const body = buildSettingsPatch(overridden, { auto_gc: { reset: true } });
  assert.deepEqual(body, { auto_gc: null });
});

test("blank is a real override value for schedule_window/webhook_url, never coerced to reset", () => {
  const body = buildSettingsPatch(ENTRIES, {
    webhook_url: { value: "" }, // already blank -> no-op, dropped
    schedule_window: { value: "22:00-06:00" }, // was null -> real change
  });
  assert.deepEqual(body, { schedule_window: "22:00-06:00" });
});

test("webhook_events: a comma string equal (order-independent, whitespace-tolerant) to the current list is a no-op", () => {
  const body = buildSettingsPatch(ENTRIES, {
    webhook_events: { value: " job.error, job.done ,job.cancelled,client.bypass_suspected,client.bypass_resolved" },
  });
  assert.deepEqual(body, {});
});

test("webhook_events: a real change (one event dropped) is included, sent verbatim as given", () => {
  const body = buildSettingsPatch(ENTRIES, { webhook_events: { value: ["job.done", "job.error"] } });
  assert.deepEqual(body, { webhook_events: ["job.done", "job.error"] });
});

test("an env-only key is dropped defensively even if somehow present in drafts", () => {
  const body = buildSettingsPatch(ENTRIES, { db_path: { value: "/somewhere/else.db" } });
  assert.deepEqual(body, {});
});

test("an unrecognised key is dropped defensively", () => {
  const body = buildSettingsPatch(ENTRIES, { made_up_key: { value: "x" } });
  assert.deepEqual(body, {});
});

test("multiple touched fields: only the ones that actually changed are sent", () => {
  const body = buildSettingsPatch(ENTRIES, {
    vault_name: { value: "vault-01" }, // unchanged
    schedule_interval_minutes: { value: "240" }, // changed
    auto_gc: { value: "dry-run" }, // changed
  });
  assert.deepEqual(body, { schedule_interval_minutes: "240", auto_gc: "dry-run" });
});

test("empty drafts produce an empty body", () => {
  assert.deepEqual(buildSettingsPatch(ENTRIES, {}), {});
});

// WP 4d-web: sweep_include_cached is a real boolean setting whose segmented
// toggle sends the STRING "true"/"false" (never a JSON boolean — the real
// PATCH endpoint's Pydantic lax-mode trap, LEARNINGS "Parsers"), and whose
// `effective` on GET is a real JS boolean, not a string — this pins that
// the generic diff correctly compares a string draft against a boolean
// effective value both ways (no-op AND real change).
test("sweep_include_cached: a draft string equal to the current boolean effective value is a no-op", () => {
  const body = buildSettingsPatch(ENTRIES, { sweep_include_cached: { value: "true" } }); // effective: true
  assert.deepEqual(body, {});
});

test("sweep_include_cached: a draft string that flips the current boolean effective value is sent as that raw string", () => {
  const body = buildSettingsPatch(ENTRIES, { sweep_include_cached: { value: "false" } });
  assert.deepEqual(body, { sweep_include_cached: "false" });
});

// WP WEB-FIX-1 (S3): `valueChanged` compares TRIMMED text, but the body used
// to carry the RAW field value — `"90 "` was correctly judged a change and
// then sent with its trailing space, which PATCH /v1/settings 422s.
test("MUTATION PIN (S3): a string draft is sent TRIMMED — what was compared is what is sent", () => {
  const body = buildSettingsPatch(ENTRIES, {
    schedule_interval_minutes: { value: "90 " },
    vault_name: { value: "  vault-02\t" },
  });
  assert.deepEqual(body, { schedule_interval_minutes: "90", vault_name: "vault-02" });
});

test("S3: whitespace-only is a real blank override for schedule_window (sent as \"\"), not dropped and not sent raw", () => {
  const withWindow = ENTRIES.map((e) => (e.key === "schedule_window" ? { ...e, effective: "22:00-06:00", source: "db" } : e));
  const body = buildSettingsPatch(withWindow, { schedule_window: { value: "   " } });
  assert.deepEqual(body, { schedule_window: "" });
});

test("S3: an array draft (webhook_events as a list) is still passed through verbatim", () => {
  const body = buildSettingsPatch(ENTRIES, { webhook_events: { value: ["job.done"] } });
  assert.deepEqual(body, { webhook_events: ["job.done"] });
});
