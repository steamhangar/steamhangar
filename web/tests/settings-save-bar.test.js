/**
 * DOM-wiring pins for the Settings save bar (WP WEB-FIX-8, user request
 * 2026-10-04: "The Save button of the Settings page should be visible when
 * you have changed something").
 *
 *   - an edit that changes a value shows the bar (and reserves scroll room
 *     via `.savebar-up` on the section); typing the saved value back hides
 *     it again (dirty = the PATCH builder's non-empty body, not "touched");
 *   - Save: one PATCH, the bar is gone after success and focus lands on the
 *     page heading when it was on the bar; a failed PATCH keeps the bar,
 *     the drafts and an error line in the bar's role=status;
 *   - Save is aria-disabled while in flight and a second click is ignored;
 *   - Discard hides the bar and restores the field;
 *   - read-only settings never build a bar; demo mode saves the same way.
 * The layout half (fixed above the bottom nav, never under the bulk bar's
 * placement rules) is pinned in css-settings-save-bar.test.js.
 *
 * Harness: fake-dom.js, Map-backed localStorage, a routing fetch fake with
 * a gate for the in-flight case. Assertions compare text/booleans/counts.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { createFakeDom } from "./fake-dom.js";

const dom = createFakeDom();
globalThis.document = dom.document;
const storage = new Map();
globalThis.window = {
  localStorage: {
    getItem: (k) => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => storage.set(k, String(v)),
    removeItem: (k) => storage.delete(k),
  },
  location: { origin: "http://vault.test", pathname: "/settings", reload() {} },
  history: { pushState() {} },
  addEventListener() {},
  removeEventListener() {},
};

const KEYS = [
  ["vault_name", "vault-01"],
  ["schedule_window", null],
  ["schedule_interval_minutes", 180],
  ["schedule_client_stale_days", 14],
  ["sweep_include_cached", true],
  ["auto_gc", "execute"],
  ["webhook_url", ""],
  ["webhook_events", []],
];
const server = { readonly: false, vaultName: "vault-01", patchStatus: 200, patches: [], gate: null };
function settingsBody() {
  return {
    readonly: server.readonly,
    server_version: "0.0.0-test",
    settings: KEYS.map(([key, effective]) => ({
      key,
      effective: key === "vault_name" ? server.vaultName : effective,
      source: "default",
      fallback: effective,
      applies: "immediately",
      env_only: false,
    })),
  };
}
function respond(status, data) {
  const text = JSON.stringify(data);
  return { ok: status >= 200 && status < 300, status, json: async () => data, text: async () => text };
}
globalThis.fetch = async (url, init = {}) => {
  const u = new URL(String(url));
  const method = (init.method || "GET").toUpperCase();
  if (u.pathname === "/v1/settings" && method === "PATCH") {
    server.patches.push(JSON.parse(init.body));
    if (server.gate) await server.gate;
    if (server.patchStatus !== 200) return respond(server.patchStatus, { detail: "disk full" });
    const body = JSON.parse(init.body);
    if ("vault_name" in body) server.vaultName = body.vault_name;
    return respond(200, settingsBody());
  }
  if (u.pathname === "/v1/settings") return respond(200, settingsBody());
  if (u.pathname === "/v1/steam/key") return respond(200, { configured: false, key_last4: null });
  if (u.pathname === "/v1/about") return respond(404, { detail: "Not Found" });
  if (u.pathname === "/v1/schedule") return respond(404, { detail: "Not Found" });
  return respond(404, { detail: "Not Found" });
};

const { renderSettings, UNSAVED_TEXT } = await import("../js/views/settings.js");

const tick = (ms = 10) => new Promise((r) => setTimeout(r, ms));
async function until(pred, label, timeoutMs = 2000) {
  const start = Date.now();
  while (!pred()) {
    if (Date.now() - start > timeoutMs) throw new Error(`timed out waiting for: ${label}`);
    await tick(5);
  }
}

const bar = (s) => s.querySelector('[data-role="save-bar"]');
const barShown = (s) => !!(bar(s) && bar(s).hidden === false);
const barText = (s) => (bar(s) ? bar(s).querySelector('[data-role="save-status"]').textContent : null);
const nameInput = (s) => s.querySelectorAll("input").find((i) => i.id === "settings-vault_name") || null;
function type(input, value) {
  input.value = value;
  input.dispatchEvent({ type: "input" });
}

async function mount() {
  const s = renderSettings();
  dom.appRoot.replaceChildren(s);
  await until(() => nameInput(s) !== null, "settings form");
  return s;
}

beforeEach(() => {
  storage.clear();
  storage.set("steamvault.apiKey", "test-key");
  server.readonly = false;
  server.vaultName = "vault-01";
  server.patchStatus = 200;
  server.patches = [];
  server.gate = null;
});

test("MUTATION TARGET: clean form — no bar; an edit shows it with 'Unsaved changes'; typing the saved value back hides it", async () => {
  const s = await mount();
  assert.equal(barShown(s), false, "nothing changed yet");
  assert.equal(s.classList.contains("savebar-up"), false);
  type(nameInput(s), "vault-02");
  assert.equal(barShown(s), true, "a real change shows the bar");
  assert.equal(barText(s), UNSAVED_TEXT);
  assert.equal(UNSAVED_TEXT, "Unsaved changes");
  assert.equal(s.classList.contains("savebar-up"), true, "scroll room reserved under the page while the bar is up");
  type(nameInput(s), "vault-01");
  assert.equal(barShown(s), false, "reverted to the saved value: nothing to save");
  assert.equal(s.classList.contains("savebar-up"), false);
});

test("the bar is a labelled region right after the form, with Discard then Save, and a role=status line", async () => {
  const s = await mount();
  type(nameInput(s), "vault-02");
  const b = bar(s);
  assert.equal(b.getAttribute("role"), "region");
  assert.equal(b.getAttribute("aria-label"), "Unsaved settings");
  assert.equal(b.querySelector('[data-role="save-status"]').getAttribute("role"), "status");
  assert.deepEqual(
    b.querySelectorAll("button").map((x) => x.textContent),
    ["Discard changes", "Save changes"],
    "focus order: Discard, then Save",
  );
  // Document order of the section headings and the bar (the body's direct
  // children; every section is appended flat from a fragment).
  const body = s.children[1];
  const order = body.children
    .filter((n) => n.tagName === "H4" || n === b)
    .map((n) => (n === b ? "BAR" : n.textContent));
  assert.equal(order[order.indexOf("Webhook") + 1], "BAR", `DOM/Tab order: the bar follows the form it saves (${order.join(", ")})`);
});

test("MUTATION TARGET: Save sends one PATCH, then the bar is gone and focus moves from the bar to the page heading", async () => {
  const s = await mount();
  type(nameInput(s), "vault-02");
  const save = bar(s).querySelector('[data-role="settings-save"]');
  save.focus();
  save.dispatchEvent({ type: "click" });
  await until(() => server.patches.length === 1 && !barShown(s), "saved");
  assert.deepEqual(server.patches[0], { vault_name: "vault-02" });
  assert.equal(barShown(s), false);
  assert.equal(s.classList.contains("savebar-up"), false);
  assert.equal(nameInput(s).value, "vault-02", "the form shows the saved value");
  assert.equal(dom.document.activeElement && dom.document.activeElement.tagName, "H1", "focus is not dropped on <body>");
});

test("MUTATION TARGET: a failed save keeps the bar, the drafts and an error in the status line", async () => {
  server.patchStatus = 500;
  const s = await mount();
  type(nameInput(s), "vault-02");
  bar(s).querySelector('[data-role="settings-save"]').dispatchEvent({ type: "click" });
  await until(() => /^Could not save: /.test(barText(s) || ""), "error line");
  assert.equal(barShown(s), true, "still dirty, still visible");
  assert.match(barText(s), /disk full/);
  assert.equal(bar(s).querySelector('[data-role="save-status"]').className, "savebar-msg is-error");
  assert.equal(bar(s).querySelector('[data-role="settings-save"]').getAttribute("aria-disabled"), null, "Save usable again");
  assert.equal(nameInput(s).value, "vault-02", "the typed value is kept");
  // Retrying succeeds with the same draft.
  server.patchStatus = 200;
  bar(s).querySelector('[data-role="settings-save"]').dispatchEvent({ type: "click" });
  await until(() => !barShown(s), "saved on retry");
  assert.deepEqual(server.patches.map((p) => p.vault_name), ["vault-02", "vault-02"]);
});

test("Save is aria-disabled while the PATCH is in flight; extra clicks send nothing", async () => {
  const s = await mount();
  type(nameInput(s), "vault-02");
  let release;
  server.gate = new Promise((r) => (release = r));
  const save = bar(s).querySelector('[data-role="settings-save"]');
  try {
    save.dispatchEvent({ type: "click" });
    await tick(5);
    assert.equal(save.getAttribute("aria-disabled"), "true");
    assert.equal(save.disabled === true, false, "not `disabled`: focus must stay on the button");
    save.dispatchEvent({ type: "click" });
    save.dispatchEvent({ type: "click" });
  } finally {
    release();
  }
  await until(() => !barShown(s), "saved");
  assert.equal(server.patches.length, 1, "three clicks, one PATCH");
});

test("Discard hides the bar and puts the saved value back", async () => {
  const s = await mount();
  type(nameInput(s), "vault-02");
  bar(s).querySelector('[data-role="settings-discard"]').dispatchEvent({ type: "click" });
  assert.equal(barShown(s), false);
  assert.equal(nameInput(s).value, "vault-01");
  assert.equal(server.patches.length, 0);
});

test("read-only settings never build a save bar", async () => {
  server.readonly = true;
  const s = await mount();
  assert.equal(bar(s), null);
  assert.equal(s.classList.contains("savebar-up"), false);
});

test("demo mode: the bar appears on an edit and goes away after the demo PATCH", async () => {
  storage.clear();
  storage.set("steamvault.demoMode", "1");
  const s = await mount();
  type(nameInput(s), "demo-vault-renamed");
  assert.equal(barShown(s), true);
  bar(s).querySelector('[data-role="settings-save"]').dispatchEvent({ type: "click" });
  await until(() => !barShown(s), "demo save");
  assert.equal(server.patches.length, 0, "demo mode never touches the network");
});

test("twin: the Android save bar uses the same words (strings.xml)", async () => {
  const { readFileSync } = await import("node:fs");
  const { fileURLToPath } = await import("node:url");
  const path = (await import("node:path")).default;
  const xml = readFileSync(
    path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "app", "app", "src", "main", "res", "values", "strings.xml"),
    "utf8",
  );
  const res = (name) => {
    const m = new RegExp(`<string name="${name}"[^>]*>(.*?)</string>`).exec(xml);
    assert.ok(m, `strings.xml lost ${name}`);
    return m[1];
  };
  assert.equal(res("settings_unsaved_changes"), UNSAVED_TEXT);
  assert.equal(res("settings_save_changes"), "Save changes");
  assert.equal(res("settings_discard_changes"), "Discard changes");
  assert.equal(res("settings_save_error"), "Could not save: %1$s", "web: `Could not save: ${detail}`");
});
