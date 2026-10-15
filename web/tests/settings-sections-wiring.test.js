/**
 * DOM-wiring pins for the D16 Settings page (WP D16-WEB = D16a-2 + D16b-2):
 *
 *   - collapsible <details> sections, Vault & connection open by default,
 *     no empty section, one-line state in the summary, open state and the
 *     expert switch remembered per browser;
 *   - the normal page carries no expert row; the "Show expert settings"
 *     switch shows the Expert section with its three sub-groups, and a
 *     hidden expert draft still shows its dot on the switch row;
 *   - env pins (Q1/Q2) render as plain text with a "Set via env (VAULT_…)"
 *     note and NO input element; shadowed rows say so; a vault-core key the
 *     build cannot apply says that instead of "set via env";
 *   - explanations sit behind the (i) disclosure;
 *   - risky keys: confirm dialog naming the risk, Cancel sends nothing,
 *     "Save anyway" sends ?confirm_risky=true; an unforeseen 428 asks for
 *     the server's keys and retries once; 409/422 say nothing was saved;
 *   - apply captions follow `applies` (core only when the API says so).
 *
 * Harness: fake-dom.js, Map-backed localStorage, a routing fetch fake that
 * serves D16b-shaped rows. Assertions compare text/booleans/counts only
 * (LEARNINGS WEB-FIX-1: a failing node assertion dumps the fake DOM).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { createFakeDom, fakeKeyEvent } from "./fake-dom.js";
import { resetModalStack } from "../js/lib/modal-stack.js";

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

function row(key, effective, extra = {}) {
  return {
    key,
    effective,
    source: "default",
    fallback: effective,
    applies: "immediately",
    env_only: false,
    env_var: key.startsWith("vault_") ? key.toUpperCase() : "VAULT_" + key.toUpperCase(),
    value: null,
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

/** A D16b-shaped settings list; `over` patches rows by key. */
function rows(over = {}) {
  const base = [
    row("vault_name", "vault-01", { applies: "restart-required" }),
    row("schedule_window", "03:00-07:00", { applies: "next_sweep", target: "api+core" }),
    row("schedule_interval_minutes", 180, { applies: "next_sweep", expert: true }),
    row("schedule_client_stale_days", 7, { applies: "next_sweep", expert: true }),
    row("sweep_include_cached", true, { applies: "next_sweep", expert: true }),
    row("auto_gc", "execute"),
    row("gc_grace_days", 14, { applies: "next_job", expert: true }),
    row("bypass_window_days", 3),
    row("prefill_max_threads", 8, { applies: "next_job", expert: true, risky: true, target: "api+core" }),
    row("webhook_events", ["job.done", "job.error"], { applies: "restart-required" }),
    row("webhook_timeout_seconds", 5, { applies: "restart-required" }),
    row("steam_library_steamid", "", {}),
    row("upstream_rate", null, { applies: "core", target: "core", expert: true, env_visible: false, grammar: "800k or off" }),
    row("upstream_max_conns", "16", { applies: "core", target: "core", expert: true, risky: true, env_visible: false }),
    row("event_log_rotate_bytes", "67108864", { applies: "core", target: "core", env_visible: false }),
    row("resolver", "1.1.1.1", { applies: "core", target: "core", expert: true, risky: true, env_visible: false }),
    row("log_level", "INFO", { expert: true }),
    row("webhook_url", "https://hooks.example/***", {
      env_only: true,
      editable: false,
      source: "env",
      applies: "restart-required",
      env_var: "VAULT_WEBHOOK_URL",
      reason: "env_only",
    }),
    row("db_path", "/data/vault.db", { env_only: true, editable: false, applies: "restart-required", reason: "env_only" }),
  ];
  return base.map((r) => (over[r.key] ? { ...r, ...over[r.key] } : r));
}

const server = { over: {}, patches: [], patchUrls: [], patchAnswer: null, serverRisky: [] };
function settingsBody() {
  return { readonly: false, server_version: "0.0.0-test", settings: rows(server.over) };
}
function respond(status, data) {
  const text = JSON.stringify(data);
  return { ok: status >= 200 && status < 300, status, json: async () => data, text: async () => text };
}
globalThis.fetch = async (url, init = {}) => {
  const u = new URL(String(url));
  const method = (init.method || "GET").toUpperCase();
  if (u.pathname === "/v1/settings" && method === "PATCH") {
    const body = JSON.parse(init.body);
    server.patches.push(body);
    server.patchUrls.push(u.search);
    if (server.patchAnswer) return respond(server.patchAnswer.status, server.patchAnswer.body);
    const risky = Object.keys(body).filter((k) => server.serverRisky.includes(k) || rows().find((r) => r.key === k)?.risky);
    if (risky.length && u.searchParams.get("confirm_risky") !== "true") {
      return respond(428, { detail: { message: "needs confirm", risky_keys: risky.sort() } });
    }
    for (const [k, v] of Object.entries(body)) {
      server.over[k] = { ...(server.over[k] || {}), effective: v, source: v === null ? "default" : "db" };
    }
    return respond(200, settingsBody());
  }
  if (u.pathname === "/v1/settings") return respond(200, settingsBody());
  if (u.pathname === "/v1/steam/key") return respond(200, { configured: false, key_last4: null });
  return respond(404, { detail: "Not Found" });
};

const { renderSettings, requestAboutFocus, NOT_CONFIRMED_TEXT } = await import("../js/views/settings.js");
const { isRiskyConfirmOpen } = await import("../js/components/risky-confirm.js");

const tick = (ms = 5) => new Promise((r) => setTimeout(r, ms));
async function until(pred, label, timeoutMs = 2000) {
  const start = Date.now();
  while (!pred()) {
    if (Date.now() - start > timeoutMs) throw new Error(`timed out waiting for: ${label}`);
    await tick();
  }
}

const q = (root, role) => root.querySelector(`[data-role="${role}"]`);
const section = (s, id) => s.querySelectorAll("details").find((d) => d.getAttribute("data-section") === id) || null;
const field = (s, key) => s.querySelectorAll("div").find((d) => d.getAttribute("data-key") === key) || null;
const inputById = (s, id) => s.querySelectorAll("input").find((i) => i.id === id) || null;
const sectionTitles = (s) => s.querySelectorAll("summary h2.sec-title").map((t) => t.textContent);
const barText = (s) => q(s, "save-status").textContent;
function type(input, value) {
  input.value = value;
  input.dispatchEvent({ type: "input" });
}
const click = (node) => node.dispatchEvent({ type: "click" });
const dialog = () => dom.document.body.querySelector('[data-role="risky-confirm"]');

async function mount() {
  const s = renderSettings();
  dom.appRoot.replaceChildren(s);
  await until(() => section(s, "vault") !== null, "settings form");
  return s;
}

beforeEach(() => {
  storage.clear();
  storage.set("steamvault.apiKey", "test-key");
  server.over = {};
  server.patches = [];
  server.patchUrls = [];
  server.patchAnswer = null;
  server.serverRisky = [];
  if (isRiskyConfirmOpen()) click(q(dialog(), "risky-cancel"));
  resetModalStack(dom.document);
});

test("sections: native <details>, Vault & connection open by default, the rest closed, no empty section", async () => {
  const s = await mount();
  assert.deepEqual(sectionTitles(s), [
    "Vault & connection",
    "Download window & updates",
    "Disk & cleanup",
    "Notifications",
    "Steam",
    "Expert settings",
    "About",
  ]);
  assert.equal(section(s, "other") === null, true, "no row lands in 'other', so the section is not built");
  assert.equal(section(s, "vault").open, true);
  for (const id of ["window", "disk", "notify", "steam", "about"]) assert.equal(section(s, id).open, false, id);
});

test("a collapsed section shows a one-line state read from its own fields", async () => {
  const s = await mount();
  const state = (id) => q(section(s, id), "section-state").textContent;
  assert.equal(state("vault"), "Name: vault-01");
  assert.equal(state("window"), "Window 03:00-07:00");
  assert.equal(state("disk"), "Auto-GC: Execute");
  assert.equal(state("notify"), "Webhook on · 2 events");
});

test("MUTATION TARGET: open state is remembered per browser (toggle -> storage -> next mount)", async () => {
  const s = await mount();
  const disk = section(s, "disk");
  disk.open = true;
  disk.dispatchEvent({ type: "toggle" });
  assert.equal(JSON.parse(storage.get("steamvault.settingsOpen")).disk, true);
  const again = await mount();
  assert.equal(section(again, "disk").open, true);
  storage.set("steamvault.settingsOpen", "not json");
  const third = await mount();
  assert.equal(section(third, "disk").open, false, "unreadable storage falls back to the defaults");
});

test("MUTATION TARGET: the normal page carries no expert row; the switch shows the Expert section and is remembered", async () => {
  const s = await mount();
  const expert = section(s, "expert");
  assert.equal(expert.hidden, true, "the Expert section is hidden while the switch is off");
  for (const key of ["vault_name", "schedule_window", "auto_gc", "bypass_window_days", "webhook_events", "webhook_timeout_seconds", "event_log_rotate_bytes"]) {
    const f = field(s, key);
    assert.ok(f, `${key} renders`);
    assert.equal(expert.querySelectorAll("div").includes(f), false, `${key} is on the normal page`);
  }
  for (const key of ["schedule_interval_minutes", "gc_grace_days", "prefill_max_threads", "upstream_rate", "upstream_max_conns", "resolver", "log_level"]) {
    assert.ok(expert.querySelectorAll("div").includes(field(s, key)), `${key} sits in the Expert section`);
  }
  const groups = expert.querySelectorAll("div.expert-group").map((g) => g.getAttribute("data-group"));
  assert.deepEqual(groups, ["bandwidth", "housekeeping", "debug"]);
  assert.deepEqual(expert.querySelectorAll("h4.sec").map((h) => h.textContent), ["Bandwidth & connections", "Housekeeping", "Debug"]);

  const sw = q(s, "expert-switch");
  assert.equal(sw.getAttribute("role"), "switch");
  assert.equal(sw.getAttribute("aria-checked"), "false");
  click(sw);
  assert.equal(sw.getAttribute("aria-checked"), "true");
  assert.equal(expert.hidden, false);
  assert.equal(expert.open, true, "switching on opens the section");
  assert.equal(storage.get("steamvault.settingsExpert"), "1");
  const again = await mount();
  assert.equal(section(again, "expert").hidden, false, "remembered");
});

test("an expert draft stays visible as a dot when the switch is turned off; collapsed sections show their own dot", async () => {
  storage.set("steamvault.settingsExpert", "1");
  const s = await mount();
  type(inputById(s, "settings-gc_grace_days"), "21");
  assert.equal(q(section(s, "expert"), "section-dirty").hidden, false);
  click(q(s, "expert-switch"));
  assert.equal(section(s, "expert").hidden, true);
  assert.equal(q(s, "expert-dirty").hidden, false, "the switch row says a hidden draft exists");
  assert.equal(q(s, "save-bar").hidden, false, "the draft is still saveable");
  type(inputById(s, "settings-bypass_window_days"), "5");
  assert.equal(q(section(s, "notify"), "section-dirty").hidden, false);
  assert.equal(q(section(s, "disk"), "section-dirty").hidden, true, "only sections holding a draft");
});

test("MUTATION TARGET: an env-pinned row is plain text with 'Set via env (VAULT_…)' — no input element, no Reset", async () => {
  server.over = { vault_name: { source: "env", editable: false, reason: "env", effective: "hangar-env", fallback: "hangar-env" } };
  const s = await mount();
  assert.equal(inputById(s, "settings-vault_name") === null, true, "no <input> for an env-pinned value");
  const f = field(s, "vault_name");
  assert.equal(f.querySelectorAll("input").length, 0);
  assert.equal(f.querySelectorAll("button").filter((b) => b.textContent === "Reset").length, 0);
  assert.equal(q(f, "setting-value").textContent, "hangar-env");
  assert.equal(q(f, "env-note").textContent, "Set via env (VAULT_NAME)");
  assert.equal(q(f, "shadow-note") === null, true, "nothing stored, nothing shadowed");
});

test("a shadowed env pin says the stored value is overridden; segmented and checkbox rows pin the same way", async () => {
  server.over = {
    auto_gc: { source: "env", editable: false, reason: "env", shadowed: true, effective: "dry-run" },
    webhook_events: { source: "env", editable: false, reason: "env" },
  };
  const s = await mount();
  const gc = field(s, "auto_gc");
  assert.equal(gc.querySelectorAll("button").filter((b) => b.getAttribute("aria-pressed") !== null).length, 0, "no segmented buttons");
  assert.equal(q(gc, "setting-value").textContent, "Dry run");
  assert.equal(q(gc, "shadow-note").textContent, "A value stored in Settings is overridden by env.");
  const ev = field(s, "webhook_events");
  assert.equal(ev.querySelectorAll("input").length, 0, "no checkboxes");
  assert.equal(q(ev, "setting-value").textContent, "Job finished, Job failed");
});

test("the env-only webhook URL is masked plain text with its env note", async () => {
  const s = await mount();
  const f = field(s, "webhook_url");
  assert.equal(f.querySelectorAll("input").length, 0);
  assert.equal(q(f, "setting-value").textContent, "https://hooks.example/***");
  assert.equal(q(f, "env-note").textContent, "Set via env (VAULT_WEBHOOK_URL)");
  assert.equal(field(s, "db_path") === null, true, "path rows stay off the page");
});

test("a vault-core key this build cannot apply says so — not 'set via env'", async () => {
  storage.set("steamvault.settingsExpert", "1");
  server.over = { upstream_max_conns: { editable: false, reason: "core_file_disabled" } };
  const s = await mount();
  const f = field(s, "upstream_max_conns");
  assert.equal(f.querySelectorAll("input").length, 0);
  assert.match(q(f, "env-note").textContent, /^Applies to vault-core only via env in this build \(VAULT_UPSTREAM_MAX_CONNS\)\./);
  assert.doesNotMatch(q(f, "env-note").textContent, /^Set via env/);
});

test("explanations sit behind the (i) disclosure, collapsed by default", async () => {
  storage.set("steamvault.settingsExpert", "1");
  const s = await mount();
  const f = field(s, "upstream_rate");
  const btn = q(f, "field-info");
  const help = q(f, "field-help");
  assert.equal(btn.getAttribute("aria-expanded"), "false");
  assert.equal(help.hidden, true);
  assert.equal(btn.getAttribute("aria-controls"), help.id);
  click(btn);
  assert.equal(help.hidden, false);
  const text = help.textContent;
  assert.match(text, /outside the download window/);
  assert.match(text, /Format: 800k or off\./, "the API's grammar is part of the help");
  assert.match(text, /vault-api cannot see vault-core's environment/, "env_visible:false is explained");
});

test("apply captions follow `applies`: core only when the API says core, next job, restart", async () => {
  storage.set("steamvault.settingsExpert", "1");
  const s = await mount();
  const cap = (key) => q(field(s, key), "field-caption").textContent;
  assert.equal(cap("event_log_rotate_bytes"), "Default value · Applies to vault-core without restart.");
  assert.equal(cap("gc_grace_days"), "Default value · Takes effect with the next prefill or GC job.");
  assert.equal(cap("vault_name"), "Default value · Takes effect after vault-api restarts.");
  assert.equal(cap("schedule_window"), "Default value · Takes effect at the next scheduled sweep.", "api+core key: the API says next_sweep, so no core claim");
});

test("MUTATION TARGET: a risky change opens the confirm dialog; Cancel sends nothing and says so", async () => {
  storage.set("steamvault.settingsExpert", "1");
  const s = await mount();
  type(inputById(s, "settings-upstream_max_conns"), "24");
  click(q(s, "settings-save"));
  await until(() => isRiskyConfirmOpen(), "risky dialog");
  assert.equal(dom.document.activeElement === q(dialog(), "risky-cancel"), true, "initial focus on the non-destructive Cancel");
  const list = q(dialog(), "risky-list").textContent;
  assert.match(list, /Upstream connection cap: 16 → 24\./);
  assert.match(list, /CGNAT/, "the dialog names the risk");
  click(q(dialog(), "risky-cancel"));
  await until(() => barText(s) === NOT_CONFIRMED_TEXT, "not-confirmed line");
  assert.equal(server.patches.length, 0, "Cancel sends no PATCH");
  assert.equal(q(s, "save-bar").hidden, false, "the draft survives");
});

test("MUTATION TARGET: 'Save anyway' sends the PATCH with ?confirm_risky=true", async () => {
  storage.set("steamvault.settingsExpert", "1");
  const s = await mount();
  type(inputById(s, "settings-upstream_max_conns"), "24");
  type(inputById(s, "settings-bypass_window_days"), "4");
  click(q(s, "settings-save"));
  await until(() => isRiskyConfirmOpen(), "risky dialog");
  assert.equal(q(dialog(), "risky-list").querySelectorAll("li").length, 1, "only the risky key is listed");
  click(q(dialog(), "risky-ok"));
  await until(() => server.patches.length === 1, "PATCH sent");
  assert.equal(server.patchUrls[0], "?confirm_risky=true");
  assert.deepEqual(server.patches[0], { upstream_max_conns: "24", bypass_window_days: "4" });
  await until(() => q(s, "save-bar") === null || q(s, "save-bar").hidden === true, "saved");
});

test("a batch without risky keys never asks and never sends confirm_risky", async () => {
  const s = await mount();
  type(inputById(s, "settings-bypass_window_days"), "4");
  click(q(s, "settings-save"));
  await until(() => server.patches.length === 1, "PATCH sent");
  assert.equal(server.patchUrls[0], "");
  assert.equal(isRiskyConfirmOpen(), false);
});

test("an unforeseen 428 asks for the keys the server names, then retries once with the confirmation", async () => {
  server.serverRisky = ["bypass_window_days"]; // the client's snapshot does not mark it risky
  const s = await mount();
  type(inputById(s, "settings-bypass_window_days"), "4");
  click(q(s, "settings-save"));
  await until(() => isRiskyConfirmOpen(), "dialog after 428");
  assert.match(q(dialog(), "risky-list").textContent, /Bypass warning after \(days\): 3 → 4\./);
  click(q(dialog(), "risky-ok"));
  await until(() => server.patches.length === 2, "retry");
  assert.deepEqual(server.patchUrls, ["", "?confirm_risky=true"]);
});

test("409 and 422 answers say nothing was saved and keep the draft", async () => {
  const s = await mount();
  server.patchAnswer = { status: 409, body: { detail: "'bypass_window_days' is set via env (VAULT_BYPASS_WINDOW_DAYS); env always wins." } };
  type(inputById(s, "settings-bypass_window_days"), "4");
  click(q(s, "settings-save"));
  await until(() => /^Not saved/.test(barText(s)), "409 line");
  assert.equal(barText(s), "Not saved: 'bypass_window_days' is set via env (VAULT_BYPASS_WINDOW_DAYS); env always wins. Nothing was saved.");
  assert.equal(q(s, "save-status").className, "savebar-msg is-error");

  server.patchAnswer = { status: 422, body: { detail: "VAULT_UPSTREAM_MAX_CONNS=4 is below VAULT_PREFILL_MAX_THREADS=8." } };
  type(inputById(s, "settings-bypass_window_days"), "6");
  click(q(s, "settings-save"));
  await until(() => /below/.test(barText(s)), "422 line");
  assert.equal(barText(s), "Not saved: VAULT_UPSTREAM_MAX_CONNS=4 is below VAULT_PREFILL_MAX_THREADS=8. Nothing was saved.");
  assert.equal(q(s, "save-bar").hidden, false);
});

test("a 428 after the user already confirmed is reported, not looped", async () => {
  storage.set("steamvault.settingsExpert", "1");
  const s = await mount();
  server.patchAnswer = { status: 428, body: { detail: { message: "needs confirm", risky_keys: ["resolver"] } } };
  type(inputById(s, "settings-resolver"), "9.9.9.9");
  click(q(s, "settings-save"));
  await until(() => isRiskyConfirmOpen(), "dialog");
  click(q(dialog(), "risky-ok"));
  await until(() => /^Not saved/.test(barText(s)), "428 line");
  assert.equal(server.patches.length, 1, "no second request");
  assert.equal(barText(s), "Not saved: the server still asked for confirmation of settings that can break downloads. Nothing was saved.");
});

test("demo mode: matching fake rows render — vault name pinned via env, shadowed timeout, masked webhook URL", async () => {
  storage.set("steamvault.demoMode", "1");
  const s = await mount();
  assert.equal(inputById(s, "settings-vault_name") === null, true);
  assert.equal(q(field(s, "vault_name"), "env-note").textContent, "Set via env (VAULT_NAME)");
  assert.equal(q(field(s, "webhook_timeout_seconds"), "shadow-note").textContent, "A value stored in Settings is overridden by env.");
  assert.match(q(field(s, "webhook_url"), "setting-value").textContent, /\/\*\*\*$/);
  assert.ok(inputById(s, "settings-schedule_window"), "the download window stays editable in the demo");
});

test("MUTATION TARGET: Escape in the risky dialog is Cancel — no PATCH, the not-confirmed line", async () => {
  storage.set("steamvault.settingsExpert", "1");
  const s = await mount();
  type(inputById(s, "settings-resolver"), "9.9.9.9");
  click(q(s, "settings-save"));
  await until(() => isRiskyConfirmOpen(), "risky dialog");
  dom.document.dispatchEvent(fakeKeyEvent("Escape"));
  await until(() => barText(s) === NOT_CONFIRMED_TEXT, "not-confirmed line");
  assert.equal(isRiskyConfirmOpen(), false);
  assert.equal(server.patches.length, 0, "Escape sends no PATCH");
});

test("opening About from the rail's version button does not remember About as open; a user toggle does", async () => {
  requestAboutFocus();
  const s = await mount();
  const about = section(s, "about");
  await until(() => about.open === true, "About opened by the rail");
  about.dispatchEvent({ type: "toggle" }); // the browser's async toggle for that open
  assert.equal("about" in JSON.parse(storage.get("steamvault.settingsOpen") || "{}"), false);
  about.open = false;
  about.dispatchEvent({ type: "toggle" });
  assert.equal(JSON.parse(storage.get("steamvault.settingsOpen")).about, false, "the user's own toggle is remembered");
});

test("section titles are headings (h2 inside the summary)", async () => {
  const s = await mount();
  assert.equal(s.querySelectorAll("summary h2.sec-title").length, sectionTitles(s).length);
  assert.ok(sectionTitles(s).length >= 6);
});
