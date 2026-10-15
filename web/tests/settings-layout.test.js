/**
 * Pure pins for web/js/lib/settings-layout.js (WP D16-WEB): placement,
 * env-pin wording, value text, section summaries, the risky-key helpers,
 * the save-error wording and the guarded open-state storage.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  EXPERT_GROUPS,
  describeSaveError,
  displayValue,
  envNote,
  isCoreFileDisabled,
  isPinned,
  placeEntries,
  readExpertSwitch,
  readOpenState,
  riskyItems,
  riskyKeysFrom428,
  riskyKeysInBody,
  sectionSummary,
  shadowNote,
  writeOpenState,
  isSectionOpen,
} from "../js/lib/settings-layout.js";
import { appliesText } from "../js/lib/settings-presentation.js";

const e = (key, extra = {}) => ({ key, effective: null, source: "default", env_only: false, editable: true, expert: false, ...extra });

test("placeEntries: expert rows go to their sub-group, unknown expert rows to housekeeping, unknown normal rows to 'other'", () => {
  const placed = placeEntries([
    e("vault_name"),
    e("auto_gc"),
    e("upstream_rate", { expert: true }),
    e("log_level", { expert: true }),
    e("gc_grace_days", { expert: true }),
    e("future_knob", { expert: true }),
    e("future_normal"),
    e("steam_library_steamid"),
    e("webhook_url", { env_only: true }),
    e("db_path", { env_only: true }),
  ]);
  assert.deepEqual(placed.sections.vault.map((x) => x.key), ["vault_name"]);
  assert.deepEqual(placed.sections.disk.map((x) => x.key), ["auto_gc"]);
  assert.deepEqual(placed.sections.notify.map((x) => x.key), ["webhook_url"], "the env-only webhook URL is shown");
  assert.deepEqual(placed.sections.other.map((x) => x.key), ["future_normal"]);
  assert.deepEqual(placed.expert.bandwidth.map((x) => x.key), ["upstream_rate"]);
  assert.deepEqual(placed.expert.debug.map((x) => x.key), ["log_level"]);
  assert.deepEqual(placed.expert.housekeeping.map((x) => x.key), ["gc_grace_days", "future_knob"]);
  const all = [...Object.values(placed.sections), ...Object.values(placed.expert)].flat().map((x) => x.key);
  assert.equal(all.includes("db_path"), false, "path rows stay off the page");
  assert.equal(all.includes("steam_library_steamid"), false, "the Steam block renders it itself");
});

test("MUTATION TARGET: placement follows the API's expert flag, not only the key — an expert-named key marked normal stays normal", () => {
  const placed = placeEntries([e("log_level", { expert: false })]);
  assert.deepEqual(placed.sections.other.map((x) => x.key), ["log_level"]);
  assert.equal(EXPERT_GROUPS.flatMap((g) => placed.expert[g.id]).length, 0);
});

test("isPinned: env-pinned and env-only rows; an older server without `editable` keeps its inputs", () => {
  assert.equal(isPinned(e("a", { editable: false, source: "env" })), true);
  assert.equal(isPinned(e("a", { env_only: true })), true);
  assert.equal(isPinned(e("a", { editable: true, source: "env" })), false);
  const old = { key: "a", source: "env", env_only: false };
  assert.equal(isPinned(old), false, "pre-D16b env values were overridable");
});

test("envNote / shadowNote / core-file-disabled wording", () => {
  assert.equal(envNote(e("vault_name", { env_var: "VAULT_NAME", source: "env", editable: false })), "Set via env (VAULT_NAME)");
  assert.equal(envNote(e("x", { source: "env", editable: false })), "Set via env");
  const core = e("upstream_max_conns", { editable: false, target: "core", env_var: "VAULT_UPSTREAM_MAX_CONNS" });
  assert.equal(isCoreFileDisabled(core), true, "core target, not editable, not from env");
  assert.match(envNote(core), /^Applies to vault-core only via env in this build \(VAULT_UPSTREAM_MAX_CONNS\)\./);
  assert.equal(isCoreFileDisabled({ ...core, source: "env" }), false, "a real env pin stays 'set via env'");
  assert.equal(isCoreFileDisabled(e("vault_name", { editable: false, reason: "core_file_disabled" })), true, "the API's reason wins");
  assert.equal(isCoreFileDisabled({ ...core, reason: null }), false, "with a reason field, only core_file_disabled counts");
  assert.equal(isCoreFileDisabled({ ...core, reason: "env" }), false);
  assert.equal(shadowNote(e("a")), null);
  assert.equal(shadowNote(e("a", { shadowed: true })), "A value stored in Settings is overridden by env.");
  assert.match(shadowNote(e("webhook_url", { shadowed: true, env_only: true })), /ignored/);
});

test("displayValue: blanks, lists with labels, booleans, option labels", () => {
  assert.equal(displayValue(e("vault_name", { effective: "" })), "Not set");
  assert.equal(displayValue(e("webhook_url", { effective: "" })), "Not set (webhooks off)");
  assert.equal(displayValue(e("webhook_events", { effective: ["job.done", "client.bypass_resolved"] })), "Job finished, Cache bypass resolved");
  assert.equal(displayValue(e("webhook_events", { effective: [] })), "None");
  assert.equal(displayValue(e("sweep_include_cached", { effective: true })), "On");
  assert.equal(displayValue(e("auto_gc", { effective: "dry-run" })), "Dry run");
  assert.equal(displayValue(e("upstream_rate", { effective: "800k" })), "800k");
});

test("sectionSummary reads only its own fields", () => {
  const rows = [
    e("schedule_window", { effective: "off" }),
    e("vault_name", { effective: "" }),
    e("webhook_url", { effective: "" }),
    e("auto_gc", { effective: "off" }),
  ];
  const byKey = (k) => rows.find((r) => r.key === k);
  assert.equal(sectionSummary("window", byKey), "Scheduler off");
  assert.equal(sectionSummary("vault", byKey), "No vault name set");
  assert.equal(sectionSummary("notify", byKey), "Webhook off");
  assert.equal(sectionSummary("disk", byKey), "Auto-GC: Off");
  assert.equal(sectionSummary("window", () => undefined), "", "no row, no claim");
  const expert = [e("a", { source: "db" }), e("b", { source: "env" }), e("c")];
  assert.equal(sectionSummary("expert", byKey, expert), "1 changed here · 1 set via env");
  assert.equal(sectionSummary("expert", byKey, [e("c")]), "All at defaults");
});

test("risky helpers: set and cleared risky keys, old -> new with a default label, 428 keys", () => {
  const entries = [
    e("upstream_max_conns", { risky: true, effective: "16", fallback: "16" }),
    e("resolver", { risky: true, effective: "9.9.9.9", fallback: "1.1.1.1", source: "db" }),
    e("vault_name"),
  ];
  const body = { upstream_max_conns: "24", resolver: null, vault_name: "x" };
  assert.deepEqual(riskyKeysInBody(entries, body), ["upstream_max_conns", "resolver"]);
  const items = riskyItems(entries, body, ["upstream_max_conns", "resolver"]);
  assert.equal(`${items[0].label}: ${items[0].from} → ${items[0].to}`, "Upstream connection cap: 16 → 24");
  assert.equal(items[1].to, "1.1.1.1 (default)");
  assert.match(items[1].risk, /LAN address/);
  assert.deepEqual(riskyKeysFrom428({ status: 428, detail: { message: "m", risky_keys: ["resolver", 3] } }), ["resolver"]);
  assert.deepEqual(riskyKeysFrom428({ status: 428, detail: "text" }), []);
});

test("MUTATION TARGET: describeSaveError — 409/422/428 say nothing was saved, other failures do not claim to know", () => {
  assert.equal(describeSaveError({ status: 409, detail: "'x' is set via env (VAULT_X); env always wins." }), "Not saved: 'x' is set via env (VAULT_X); env always wins. Nothing was saved.");
  assert.equal(describeSaveError({ status: 422, detail: "bad value." }), "Not saved: bad value. Nothing was saved.");
  assert.equal(describeSaveError({ status: 422, detail: [{ msg: "field required" }] }), "Not saved: field required Nothing was saved.");
  assert.match(describeSaveError({ status: 428, detail: { message: "m", risky_keys: [] } }), /not confirmed\. Nothing was saved\.$/);
  assert.match(describeSaveError({ status: 428, detail: {} }, { confirmed: true }), /server still asked for confirmation/);
  assert.equal(describeSaveError({ status: 500, detail: "disk full" }), "Could not save: disk full");
  assert.doesNotMatch(describeSaveError({ status: null, message: "Network request failed" }), /Nothing was saved/);
});

test("applies texts: next_job and core (D16b)", () => {
  assert.equal(appliesText("next_job"), "Takes effect with the next prefill or GC job.");
  assert.equal(appliesText("core"), "Applies to vault-core without restart.");
});

test("open state and expert switch: guarded storage (throwing getter, bad JSON, wrong types)", () => {
  const store = new Map();
  const storage = { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)) };
  assert.deepEqual(readOpenState(() => storage), {});
  writeOpenState(() => storage, { disk: true });
  assert.deepEqual(readOpenState(() => storage), { disk: true });
  store.set("steamvault.settingsOpen", '{"disk":"yes","window":false}');
  assert.deepEqual(readOpenState(() => storage), { window: false }, "non-boolean values are dropped");
  store.set("steamvault.settingsOpen", "{broken");
  assert.deepEqual(readOpenState(() => storage), {});
  const throwing = () => {
    throw new Error("SecurityError");
  };
  assert.deepEqual(readOpenState(throwing), {});
  assert.equal(readExpertSwitch(throwing), false);
  writeOpenState(throwing, { a: true }); // must not throw
  assert.equal(isSectionOpen({}, "vault"), true, "Vault & connection opens by default");
  assert.equal(isSectionOpen({}, "expert"), false);
  assert.equal(isSectionOpen({ vault: false }, "vault"), false);
});

test("coreApplyLine: pending wins, rejected warns with the reason, applied, none, unknown -> no claim", async () => {
  const { coreApplyLine } = await import("../js/lib/settings-layout.js");
  assert.equal(coreApplyLine(null), null);
  assert.deepEqual(coreApplyLine({ state: "applied", pending: true }), { text: "vault-core: a change is waiting to be applied.", warn: false });
  assert.deepEqual(coreApplyLine({ state: "rejected", reason: "cap below threads", pending: false }), {
    text: "vault-core rejected the last settings change: cap below threads It keeps running with the settings it had.",
    warn: true,
  });
  assert.equal(coreApplyLine({ state: "applied", pending: false }).text, "vault-core: settings applied.");
  assert.match(coreApplyLine({ state: "none", pending: false }).text, /has not seen a settings file/);
  assert.equal(coreApplyLine({ state: "weird", pending: false }), null);
});
