/**
 * DOM-wiring pins for the D16 Settings page (WP D16-WEB = D16a-2 + D16b-2,
 * restructured by WP SETTINGS-1 into a section nav + one panel at a time):
 *
 *   - a left section nav, Vault & connection shown by default, no empty
 *     section, one-line state and dot on each nav item, the expert switch
 *     remembered per browser;
 *   - the normal page carries no expert row; the "Show expert settings"
 *     switch makes the Expert section reachable, with its three
 *     sub-groups, and a hidden expert draft still shows its dot on the
 *     switch row;
 *   - env pins (Q1/Q2) render as a DISABLED input (WP SETTINGS-2) with the
 *     "Set via env (VAULT_…)" note behind the (i), no Reset and no seg or
 *     checkbox control; shadowed rows say so; a vault-core key the build
 *     cannot apply says that instead of "set via env";
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

const server = { over: {}, patches: [], patchUrls: [], patchAnswer: null, serverRisky: [], coreApply: null };
function settingsBody() {
  return { readonly: false, server_version: "0.0.0-test", settings: rows(server.over), core_apply: server.coreApply };
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
const section = (s, id) => s.querySelectorAll("section").find((d) => d.getAttribute("data-section") === id) || null;
const navItem = (s, id) => s.querySelectorAll("button").find((b) => b.getAttribute("data-nav") === id) || null;
const navIds = (s) => s.querySelectorAll("button").filter((b) => b.getAttribute("data-nav") && !b.hidden).map((b) => b.getAttribute("data-nav"));
const activeIds = (s) => s.querySelectorAll("section").filter((p) => p.getAttribute("data-section") && !p.hidden).map((p) => p.getAttribute("data-section"));
const field = (s, key) => s.querySelectorAll("div").find((d) => d.getAttribute("data-key") === key) || null;
const inputById = (s, id) => s.querySelectorAll("input").find((i) => i.id === id) || null;
const sectionTitles = (s) => s.querySelectorAll("section h2.sec-title").map((t) => t.textContent);
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
  server.coreApply = null;
  if (isRiskyConfirmOpen()) click(q(dialog(), "risky-cancel"));
  resetModalStack(dom.document);
});

test("sections: a nav item and a panel per section, Vault & connection shown by default, no empty section", async () => {
  const s = await mount();
  assert.deepEqual(sectionTitles(s), [
    "Vault & connection",
    "Download window & updates",
    "Disk & cleanup",
    "Notifications",
    "Steam",
    "About",
    "Expert settings",
  ]);
  assert.equal(section(s, "other") === null, true, "no row lands in 'other', so the section is not built");
  assert.deepEqual(navIds(s), ["vault", "window", "disk", "notify", "steam", "about"], "Expert is not in the nav while the switch is off");
  assert.deepEqual(activeIds(s), ["vault"], "one panel at a time");
  assert.equal(navItem(s, "vault").getAttribute("aria-current"), "true");
  for (const id of ["window", "disk", "notify", "steam", "about"]) assert.equal(navItem(s, id).getAttribute("aria-current"), null, id);
  assert.equal(s.querySelectorAll("nav").find((n) => n.getAttribute("aria-label") === "Settings sections") !== undefined, true);
});

test("clicking a nav item shows that panel and moves aria-current; every panel stays in the DOM", async () => {
  const s = await mount();
  click(navItem(s, "disk"));
  assert.deepEqual(activeIds(s), ["disk"]);
  assert.equal(navItem(s, "disk").getAttribute("aria-current"), "true");
  assert.equal(navItem(s, "vault").getAttribute("aria-current"), null);
  assert.ok(field(s, "vault_name"), "a field of a hidden panel is still built");
  assert.ok(section(s, "vault").hidden);
});

test("section nav keyboard: arrows wrap, Home/End jump, focus follows, other keys do nothing", async () => {
  const s = await mount();
  const press = (id, key) => {
    const ev = fakeKeyEvent(key);
    navItem(s, id).dispatchEvent(ev);
    return ev.defaultPrevented;
  };
  assert.equal(press("vault", "ArrowDown"), true);
  assert.deepEqual(activeIds(s), ["window"]);
  assert.equal(dom.document.activeElement === navItem(s, "window"), true);
  assert.equal(press("window", "ArrowUp"), true);
  assert.deepEqual(activeIds(s), ["vault"]);
  press("vault", "ArrowUp");
  assert.deepEqual(activeIds(s), ["about"], "wraps from first to last visible item");
  press("about", "Home");
  assert.deepEqual(activeIds(s), ["vault"]);
  press("vault", "End");
  assert.deepEqual(activeIds(s), ["about"]);
  assert.equal(press("about", "x"), false);
  assert.deepEqual(activeIds(s), ["about"]);
});

test("each nav item shows a one-line state read from its own fields", async () => {
  const s = await mount();
  const state = (id) => q(navItem(s, id), "section-state").textContent;
  assert.equal(state("vault"), "Name: vault-01");
  assert.equal(state("window"), "Window 03:00-07:00");
  assert.equal(state("disk"), "Auto-GC: Execute");
  assert.equal(state("notify"), "Webhook on · 2 events");
});

test("the section choice is per mount and not remembered", async () => {
  const s = await mount();
  click(navItem(s, "notify"));
  const again = await mount();
  assert.deepEqual(activeIds(again), ["vault"]);
  assert.equal(storage.has("steamvault.settingsOpen"), false);
});

test("MUTATION TARGET: the normal page carries no expert row; the switch shows the Expert section and is remembered", async () => {
  const s = await mount();
  const expert = section(s, "expert");
  assert.equal(expert.hidden, true, "the Expert panel is not the active panel while the switch is off");
  for (const key of ["vault_name", "schedule_window", "auto_gc", "bypass_window_days", "webhook_events", "webhook_timeout_seconds", "event_log_rotate_bytes"]) {
    const f = field(s, key);
    assert.ok(f, `${key} renders`);
    assert.equal(expert.querySelectorAll("div").includes(f), false, `${key} is on the normal page`);
  }
  for (const key of ["schedule_interval_minutes", "gc_grace_days", "prefill_max_threads", "upstream_rate", "upstream_max_conns", "resolver", "log_level"]) {
    assert.ok(expert.querySelectorAll("div").includes(field(s, key)), `${key} sits in the Expert section`);
  }
  const groups = expert.querySelectorAll("div.fcard").map((g) => g.getAttribute("data-group"));
  assert.deepEqual(groups, ["bandwidth", "housekeeping", "debug"], "one field card per expert group");
  assert.deepEqual(expert.querySelectorAll("h3").map((h) => h.textContent), ["Bandwidth & connections", "Housekeeping", "Debug"]);

  const sw = q(s, "expert-switch");
  assert.equal(sw.getAttribute("role"), "switch");
  assert.equal(sw.getAttribute("aria-checked"), "false");
  assert.equal(navItem(s, "expert").hidden, true);
  click(sw);
  assert.equal(sw.getAttribute("aria-checked"), "true");
  assert.equal(navItem(s, "expert").hidden, false, "the nav now offers Expert settings");
  assert.deepEqual(activeIds(s), ["vault"], "switching on does not move the user");
  assert.equal(storage.get("steamvault.settingsExpert"), "1");
  const again = await mount();
  assert.equal(navItem(again, "expert").hidden, false, "remembered");
});

test("expert switch: Expert settings is reachable through the nav; switching off while on it returns to Vault", async () => {
  const s = await mount();
  assert.ok(navIds(s).indexOf("expert") < 0);
  click(q(s, "expert-switch"));
  assert.equal(navIds(s).at(-1), "expert", "Expert settings is the last nav item");
  click(navItem(s, "expert"));
  assert.deepEqual(activeIds(s), ["expert"]);
  assert.equal(navItem(s, "expert").getAttribute("aria-current"), "true");
  click(q(s, "expert-switch"));
  assert.equal(navItem(s, "expert").hidden, true);
  assert.deepEqual(activeIds(s), ["vault"]);
  assert.equal(navItem(s, "vault").getAttribute("aria-current"), "true");
  assert.equal(storage.get("steamvault.settingsExpert"), "0");
});

test("the switch row sits in the nav, not in a panel", async () => {
  const s = await mount();
  const nav = s.querySelectorAll("nav").find((n) => n.getAttribute("aria-label") === "Settings sections");
  assert.ok(q(nav, "expert-switch"), "the switch is inside the section nav");
  assert.equal(q(section(s, "about"), "expert-switch"), null);
});

test("an expert draft stays visible as a dot when the switch is turned off; nav items show their own dot", async () => {
  storage.set("steamvault.settingsExpert", "1");
  const s = await mount();
  type(inputById(s, "settings-gc_grace_days"), "21");
  assert.equal(q(navItem(s, "expert"), "section-dirty").hidden, false);
  click(q(s, "expert-switch"));
  assert.equal(navItem(s, "expert").hidden, true);
  assert.equal(q(s, "expert-dirty").hidden, false, "the switch row says a hidden draft exists");
  assert.equal(q(s, "save-bar").hidden, false, "the draft is still saveable");
  type(inputById(s, "settings-bypass_window_days"), "5");
  assert.equal(q(navItem(s, "notify"), "section-dirty").hidden, false);
  assert.equal(q(navItem(s, "disk"), "section-dirty").hidden, true, "only sections holding a draft");
});

test("MUTATION TARGET: an env-pinned row is a DISABLED input with 'Set via env (VAULT_…)' behind the (i) — no Reset, an edit sends nothing", async () => {
  server.over = { vault_name: { source: "env", editable: false, reason: "env", effective: "hangar-env", fallback: "hangar-env" } };
  const s = await mount();
  const input = inputById(s, "settings-vault_name");
  assert.ok(input, "the pinned value shows in an input");
  assert.equal(input.disabled, true, "env always wins: the input is disabled");
  const f = field(s, "vault_name");
  assert.equal(f.querySelectorAll("input").length, 1);
  assert.equal(f.querySelectorAll("button").filter((b) => b.textContent === "Reset").length, 0);
  assert.equal(q(f, "setting-value").value, "hangar-env");
  assert.equal(q(f, "env-note").textContent, "Set via env (VAULT_NAME)");
  assert.equal(q(f, "field-help").querySelectorAll("p").includes(q(f, "env-note")), true, "the env note sits behind the (i)");
  assert.equal(q(f, "field-help").hidden, true);
  assert.equal(q(f, "field-chip").textContent, "From env");
  assert.equal(q(f, "shadow-note") === null, true, "nothing stored, nothing shadowed");
  // A forced input event (a disabled input fires none) still drafts nothing:
  // the pinned input has no listener.
  type(input, "edited");
  assert.equal(q(s, "save-bar").hidden, true);
});

test("a shadowed env pin says the stored value is overridden; segmented and checkbox rows pin the same way", async () => {
  server.over = {
    auto_gc: { source: "env", editable: false, reason: "env", shadowed: true, effective: "dry-run" },
    webhook_events: { source: "env", editable: false, reason: "env" },
  };
  const s = await mount();
  const gc = field(s, "auto_gc");
  assert.equal(gc.querySelectorAll("button").filter((b) => b.getAttribute("aria-pressed") !== null).length, 0, "no segmented buttons");
  assert.equal(q(gc, "setting-value").value, "Dry run");
  assert.equal(q(gc, "setting-value").disabled, true);
  assert.equal(q(gc, "shadow-note").textContent, "A value stored in Settings is overridden by env.");
  const ev = field(s, "webhook_events");
  assert.equal(ev.querySelectorAll("input").filter((i) => i.type === "checkbox").length, 0, "no checkboxes");
  assert.equal(q(ev, "setting-value").value, "Job finished, Job failed");
});

test("the env-only webhook URL is a masked, disabled input with its env note", async () => {
  const s = await mount();
  const f = field(s, "webhook_url");
  assert.equal(q(f, "setting-value").disabled, true);
  assert.equal(q(f, "setting-value").value, "https://hooks.example/***");
  assert.equal(q(f, "env-note").textContent, "Set via env (VAULT_WEBHOOK_URL)");
  assert.equal(field(s, "db_path") === null, true, "path rows stay off the page");
});

test("a vault-core key this build cannot apply says so — not 'set via env'", async () => {
  storage.set("steamvault.settingsExpert", "1");
  server.over = { upstream_max_conns: { editable: false, reason: "core_file_disabled" } };
  const s = await mount();
  const f = field(s, "upstream_max_conns");
  assert.equal(q(f, "setting-value").disabled, true);
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

test("timing text follows `applies`: core only when the API says core, next job, restart; it sits behind the (i)", async () => {
  storage.set("steamvault.settingsExpert", "1");
  const s = await mount();
  const cap = (key) => q(field(s, key), "field-caption").textContent;
  assert.equal(cap("event_log_rotate_bytes"), "Applies to vault-core without restart.");
  assert.equal(cap("gc_grace_days"), "Takes effect with the next prefill or GC job.");
  assert.equal(cap("vault_name"), "Takes effect after vault-api restarts.");
  assert.equal(cap("schedule_window"), "Takes effect at the next scheduled sweep.", "api+core key: the API says next_sweep, so no core claim");
  const f = field(s, "gc_grace_days");
  assert.equal(q(f, "field-help").querySelectorAll("p").includes(q(f, "field-caption")), true, "the timing line is inside the (i) block");
  assert.equal(q(f, "field-help").hidden, true, "collapsed by default");
});

test("MUTATION TARGET: a risky change opens the confirm dialog; Cancel sends nothing and says so", async () => {
  storage.set("steamvault.settingsExpert", "1");
  const s = await mount();
  type(inputById(s, "settings-upstream_max_conns"), "24");
  click(q(s, "settings-save"));
  await until(() => isRiskyConfirmOpen(), "risky dialog");
  assert.equal(dom.document.activeElement === q(dialog(), "risky-cancel"), true, "initial focus on the non-destructive Cancel");
  const list = q(dialog(), "risky-list").textContent;
  assert.match(list, /Upstream \(WAN\) connection cap: 16 → 24\./);
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
  assert.equal(inputById(s, "settings-vault_name").disabled, true);
  assert.equal(q(field(s, "vault_name"), "env-note").textContent, "Set via env (VAULT_NAME)");
  assert.equal(q(field(s, "webhook_timeout_seconds"), "shadow-note").textContent, "A value stored in Settings is overridden by env.");
  assert.match(q(field(s, "webhook_url"), "setting-value").value, /\/\*\*\*$/);
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

test("opening About from the rail's version button selects the About panel and focuses its heading", async () => {
  requestAboutFocus();
  const s = await mount();
  assert.deepEqual(activeIds(s), ["about"]);
  assert.equal(navItem(s, "about").getAttribute("aria-current"), "true");
  assert.equal(dom.document.activeElement.id, "settings-about");
});

test("section titles are h2 headings, one per panel", async () => {
  const s = await mount();
  assert.equal(s.querySelectorAll("section h2.sec-title").length, s.querySelectorAll("section").filter((p) => p.getAttribute("data-section")).length);
  assert.ok(sectionTitles(s).length >= 6);
});

test("fields sit in an auto-fit grid and carry a state chip", async () => {
  const s = await mount();
  const grid = section(s, "vault").querySelectorAll("div").find((d) => d.className === "fg");
  assert.ok(grid, "vault panel has a .fg grid");
  assert.equal(grid.querySelectorAll("div").includes(field(s, "vault_name")), true);
  const chip = (key) => q(field(s, key), "field-chip");
  assert.equal(chip("vault_name").textContent, "Default");
  assert.equal(chip("vault_name").getAttribute("data-chip"), "default");
  assert.equal(chip("webhook_url").textContent, "From env");
});

test("a stored override reads 'Changed here', an env value 'From env'", async () => {
  server.over = {
    bypass_window_days: { source: "db", effective: 5 },
    vault_name: { source: "env", editable: false, reason: "env", effective: "x", fallback: "x" },
  };
  const s = await mount();
  assert.equal(q(field(s, "bypass_window_days"), "field-chip").textContent, "Changed here");
  assert.equal(q(field(s, "vault_name"), "field-chip").textContent, "From env");
});

test("GC warning: shown while 'include cached' is on and Auto-GC is not Execute, live from the draft", async () => {
  storage.set("steamvault.settingsExpert", "1");
  server.over = { auto_gc: { effective: "dry-run", fallback: "dry-run" } };
  const s = await mount();
  const warn = q(section(s, "disk"), "gc-risk");
  assert.ok(warn, "the warning lives in the Disk panel");
  assert.equal(warn.hidden, false, "include cached is on, GC is dry-run");
  const execute = field(s, "auto_gc").querySelectorAll("button").find((b) => b.dataset.value === "execute");
  click(execute);
  assert.equal(warn.hidden, true, "choosing Execute clears the warning at once");
  const dry = field(s, "auto_gc").querySelectorAll("button").find((b) => b.dataset.value === "dry-run");
  click(dry);
  assert.equal(warn.hidden, false);
});

test("GC warning stays hidden when Auto-GC is Execute", async () => {
  const s = await mount();
  assert.equal(q(section(s, "disk"), "gc-risk").hidden, true);
});

test("the save bar appears on the first change and goes when the draft is reverted", async () => {
  const s = await mount();
  assert.equal(q(s, "save-bar").hidden, true);
  type(inputById(s, "settings-bypass_window_days"), "4");
  assert.equal(q(s, "save-bar").hidden, false);
  type(inputById(s, "settings-bypass_window_days"), "3");
  assert.equal(q(s, "save-bar").hidden, true);
});

test("MUTATION TARGET: the Expert section shows vault-core's apply state (D16c core_apply); none before vault-core reported", async () => {
  storage.set("steamvault.settingsExpert", "1");
  let s = await mount();
  assert.equal(q(section(s, "expert"), "core-apply") === null, true, "core_apply null -> no line");
  server.coreApply = { state: "rejected", generation: 3, applied_generation: 2, reason: "upstream cap below prefill threads", updated_at: "2026-10-15T10:00:00Z", written_generation: 3, pending: false };
  s = await mount();
  const line = q(section(s, "expert"), "core-apply");
  assert.equal(line.textContent, "vault-core rejected the last settings change: upstream cap below prefill threads It keeps running with the settings it had.");
  assert.equal(line.classList.contains("warn"), true, "a rejection is a .warn box");
  server.coreApply = { ...server.coreApply, state: "applied", generation: 3, applied_generation: 3, reason: "", written_generation: 4, pending: true };
  s = await mount();
  assert.equal(q(section(s, "expert"), "core-apply").textContent, "vault-core: a change is waiting to be applied.");
});

test("compact layout: at a container width of 760px or less the expert switch is the last item of About, not in the nav; same state and ids", async () => {
  // The view measures its own root with a ResizeObserver (the same 760px
  // as kit.css's @container settings); this fake hands it a width.
  const observers = [];
  globalThis.ResizeObserver = class {
    constructor(cb) {
      this.cb = cb;
      observers.push(this);
    }
    observe(target) {
      this.target = target;
    }
    disconnect() {}
  };
  const resize = (width) => {
    const o = observers.at(-1);
    o.cb([{ target: o.target, contentRect: { width } }]);
  };
  try {
    const s = await mount();
    resize(500);
    const about = section(s, "about");
    const nav = s.querySelectorAll("nav")[0];
    const sw = q(s, "expert-switch");
    assert.ok(sw, "switch rendered");
    assert.equal(nav.querySelectorAll("button").includes(sw), false, "not in the nav when compact");
    const last = about.children[about.children.length - 1];
    assert.ok(last.classList.contains("exp"), "last child of the About panel");
    assert.ok(last.querySelectorAll("button").includes(sw));
    assert.equal(s.querySelectorAll("button").filter((b) => b.getAttribute("data-role") === "expert-switch").length, 1);
    const ids = s.querySelectorAll("span").map((x) => x.id).filter(Boolean);
    assert.equal(ids.filter((i) => i === "settings-expert-switch-label").length, 1, "no duplicate id");
    assert.equal(sw.getAttribute("aria-labelledby"), "settings-expert-switch-label");
    assert.equal(sw.getAttribute("role"), "switch");
    click(sw);
    assert.equal(storage.get("steamvault.settingsExpert"), "1", "same storage key");
    assert.equal(navItem(s, "expert").hidden, false);
    // Growing past the threshold moves the same row back under the nav.
    resize(761);
    assert.equal(nav.querySelectorAll("button").includes(sw), true, "wide: under the nav");
    assert.equal(about.querySelectorAll("button").includes(sw), false);
    assert.equal(sw.getAttribute("aria-checked"), "true", "state kept");
    resize(760);
    assert.equal(about.querySelectorAll("button").includes(sw), true, "760px itself is compact (the CSS max-width is inclusive)");
    resize(0);
    assert.equal(about.querySelectorAll("button").includes(sw), true, "a zero width (detached) changes nothing");
    resize(1200);
  } finally {
    delete globalThis.ResizeObserver;
  }
});
