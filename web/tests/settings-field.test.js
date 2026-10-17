/**
 * Component pins for web/js/components/settings-field.js (WP SETTINGS-2):
 * one Settings field per kit.html's field anatomy — label, (i), state chip,
 * control, Reset — with the timing line first behind the (i), then the help
 * text; env pins as disabled inputs with the env note behind the (i).
 *
 * Harness: fake-dom.js. Assertions compare text/booleans/counts only
 * (LEARNINGS WEB-FIX-1: a failing node assertion dumps the fake DOM).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createFakeDom } from "./fake-dom.js";

const dom = createFakeDom();
globalThis.document = dom.document;

const { createSettingsField, createInfoDisclosure } = await import("../js/components/settings-field.js");

function row(key, effective, extra = {}) {
  return {
    key,
    effective,
    source: "default",
    fallback: effective,
    applies: "immediately",
    env_only: false,
    env_var: "VAULT_" + key.toUpperCase(),
    editable: true,
    risky: false,
    expert: false,
    grammar: "",
    target: "api",
    env_visible: true,
    shadowed: false,
    reason: null,
    ...extra,
  };
}
const q = (root, role) => root.querySelector(`[data-role="${role}"]`);
const click = (node) => node.dispatchEvent({ type: "click" });
const helpTexts = (f) => q(f, "field-help").querySelectorAll("p").map((p) => p.textContent);

test("text field: label for the input, (i) collapsed with the timing line first and the help after, Default chip", () => {
  const f = createSettingsField(row("bypass_window_days", 3));
  assert.equal(f.getAttribute("data-key"), "bypass_window_days");
  assert.equal(f.className, "field");
  const label = f.querySelector("label");
  const input = f.querySelector("input");
  assert.equal(label.textContent, "Bypass warning after (days)");
  assert.equal(label.htmlFor, "settings-bypass_window_days");
  assert.equal(input.id, "settings-bypass_window_days");
  assert.equal(input.value, "3");
  assert.equal(input.className, "in mono");
  const btn = q(f, "field-info");
  assert.equal(btn.className, "ib");
  assert.equal(btn.getAttribute("aria-label"), "More about Bypass warning after (days)");
  assert.equal(btn.getAttribute("aria-expanded"), "false");
  assert.equal(btn.getAttribute("aria-controls"), q(f, "field-help").id);
  assert.equal(q(f, "field-help").hidden, true, "help is behind the (i), not permanently visible");
  const texts = helpTexts(f);
  assert.equal(texts[0], "Takes effect immediately.", "MUTATION TARGET: the timing line opens the (i) block");
  assert.match(texts[1], /bypass suspected/);
  assert.equal(q(f, "field-chip").textContent, "Default");
  assert.equal(q(f, "field-chip").className, "chip");
  click(btn);
  assert.equal(q(f, "field-help").hidden, false);
  assert.equal(btn.getAttribute("aria-expanded"), "true");
});

test("prose keys use the UI font; a typed value is reported as a draft", () => {
  const drafts = [];
  const f = createSettingsField(row("vault_name", "vault-01"), { onDraft: (d) => drafts.push(d) });
  const input = f.querySelector("input");
  assert.equal(input.className, "in");
  input.value = "vault-02";
  input.dispatchEvent({ type: "input" });
  assert.deepEqual(drafts, [{ value: "vault-02" }]);
});

test("Reset: shown only for a stored (db) value; it restores the fallback and drafts {reset:true}", () => {
  assert.equal(q(createSettingsField(row("bypass_window_days", 3)), "field-reset").hidden, true, "default: nothing to reset");
  const drafts = [];
  const f = createSettingsField(row("bypass_window_days", 9, { source: "db", fallback: 3 }), { onDraft: (d) => drafts.push(d) });
  assert.equal(q(f, "field-chip").textContent, "Changed here");
  assert.equal(q(f, "field-chip").className, "chip ch");
  const reset = q(f, "field-reset");
  assert.equal(reset.hidden, false);
  click(reset);
  assert.equal(f.querySelector("input").value, "3");
  assert.deepEqual(drafts, [{ reset: true }]);
});

test("segmented field: a kit .seg group named by the label, pressed state from the draft first, click drafts the value", () => {
  const drafts = [];
  const f = createSettingsField(row("auto_gc", "execute"), { draftValue: "dry-run", onDraft: (d) => drafts.push(d) });
  const seg = f.querySelector("div.seg");
  assert.equal(seg.getAttribute("role"), "group");
  assert.equal(seg.getAttribute("aria-labelledby"), "settings-auto_gc-label");
  assert.equal(f.querySelector("span.lbl").id, "settings-auto_gc-label");
  const pressed = () => seg.querySelectorAll("button").filter((b) => b.getAttribute("aria-pressed") === "true").map((b) => b.textContent);
  assert.deepEqual(pressed(), ["Dry run"]);
  click(seg.querySelectorAll("button").find((b) => b.dataset.value === "off"));
  assert.deepEqual(pressed(), ["Off"]);
  assert.deepEqual(drafts, [{ value: "off" }]);
});

test("events field: a wide .checks grid of check pills; a change drafts the checked list", () => {
  const drafts = [];
  const f = createSettingsField(row("webhook_events", ["job.done"]), { onDraft: (d) => drafts.push(d) });
  assert.equal(f.className, "field wide");
  const grid = f.querySelector("div.checks");
  assert.equal(grid.getAttribute("role"), "group");
  assert.equal(grid.getAttribute("aria-labelledby"), "settings-webhook_events-label");
  const boxes = grid.querySelectorAll("input");
  assert.equal(grid.querySelectorAll("label.check").length, 5);
  assert.deepEqual(boxes.filter((b) => b.checked).map((b) => b.dataset.event), ["job.done"]);
  const failed = boxes.find((b) => b.dataset.event === "job.error");
  failed.checked = true;
  failed.dispatchEvent({ type: "change" });
  assert.deepEqual(drafts, [{ value: ["job.done", "job.error"] }]);
});

test("MUTATION TARGET: an env pin is a disabled input with the value as text, the From env chip, the env note behind (i), no Reset, no control", () => {
  const drafts = [];
  const f = createSettingsField(row("auto_gc", "dry-run", { source: "env", editable: false, reason: "env", shadowed: true }), {
    onDraft: (d) => drafts.push(d),
  });
  assert.equal(f.className, "field pinned");
  const input = q(f, "setting-value");
  assert.equal(input.disabled, true);
  assert.equal(input.value, "Dry run", "the option's label, not the raw value");
  assert.equal(f.querySelector("label").htmlFor, input.id);
  assert.equal(f.querySelectorAll("div.seg").length, 0, "no segmented control for a pinned row");
  assert.equal(q(f, "field-reset"), null);
  assert.equal(q(f, "field-chip").textContent, "From env");
  assert.equal(q(f, "field-chip").className, "chip env");
  const texts = helpTexts(f);
  assert.equal(texts[0], "Set via env (VAULT_AUTO_GC)");
  assert.equal(texts[1], "A value stored in Settings is overridden by env.");
  assert.equal(q(f, "field-caption"), null, "nothing here can change it, so no timing line");
  assert.equal(q(f, "field-help").hidden, true);
  input.value = "x";
  input.dispatchEvent({ type: "input" });
  assert.deepEqual(drafts, [], "no listener on a pinned input");
});

test("the env-only webhook URL is a wide pinned field", () => {
  const f = createSettingsField(row("webhook_url", "https://h.example/***", { env_only: true, editable: false, source: "env" }));
  assert.equal(f.className, "field wide pinned");
  assert.equal(q(f, "setting-value").value, "https://h.example/***");
});

test("read-only settings: controls disabled and no Reset even for a stored value", () => {
  const f = createSettingsField(row("bypass_window_days", 9, { source: "db", fallback: 3 }), { readonly: true });
  assert.equal(f.querySelector("input").disabled, true);
  assert.equal(q(f, "field-reset").hidden, true);
  const seg = createSettingsField(row("auto_gc", "execute"), { readonly: true });
  assert.equal(seg.querySelectorAll("button").filter((b) => b.dataset.value).every((b) => b.disabled === true), true);
});

test("the (i) choice is remembered in the shared memory map across re-renders", () => {
  const memory = new Map();
  const first = createSettingsField(row("bypass_window_days", 3), { memory });
  click(q(first, "field-info"));
  const again = createSettingsField(row("bypass_window_days", 3), { memory });
  assert.equal(q(again, "field-help").hidden, false);
  assert.equal(q(again, "field-info").getAttribute("aria-expanded"), "true");
});

test("createInfoDisclosure: lead nodes before the paragraphs, ids wired", () => {
  const lead = document.createElement("p");
  lead.textContent = "lead";
  const { btn, help } = createInfoDisclosure({ id: "x", label: "X", paragraphs: ["one"], lead: [lead] });
  assert.equal(help.id, "x-help");
  assert.equal(btn.getAttribute("aria-controls"), "x-help");
  assert.deepEqual(help.querySelectorAll("p").map((p) => p.textContent), ["lead", "one"]);
});
