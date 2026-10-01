/**
 * DOM-wiring pins for `web/js/views/settings.js` (WP WEB-FIX-1: B1's demo
 * affordance, B2's Connection-section-in-error-state, P2's missing-key
 * guard).
 *
 * Every earlier Settings-adjacent WP kept this view un-unit-tested ("the
 * decision logic lives in lib/ and is tested there"); all three findings
 * here are wiring defects that posture could not see:
 *   - B2: `fullRender()` early-returned on `state.loadError` BEFORE
 *     `buildConnectionSection()` — so when `GET /v1/settings` 401s (a
 *     rotated key), the only reconnect entry in the app never rendered.
 *   - B1: nothing in the UI said "you are in demo mode" or offered a way
 *     out; the Connection row read "Reconnect / switch account" as if a
 *     vault were attached.
 *   - P2: a settings response lacking one of the eight keys threw inside
 *     `fullRender()` after `loadSettings()`'s try/catch had passed, leaving
 *     "Loading settings…" on screen forever.
 *
 * Harness: `fake-dom.js` (fragments + text nodes added in this WP — the
 * view builds every section as a DocumentFragment), a Map-backed
 * `localStorage`, and a routing `fetch` fake. Demo mode is exercised
 * through the REAL `api.js` → `demo-data.js` path (no fetch at all), the
 * other cases through the fake server.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { createFakeDom } from "./fake-dom.js";
import { resetModalStack } from "../js/lib/modal-stack.js";

const dom = createFakeDom();
globalThis.document = dom.document;
const storage = new Map();
globalThis.window = {
  localStorage: {
    getItem: (k) => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => storage.set(k, String(v)),
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
function fullSettings(omit = []) {
  return {
    readonly: false,
    server_version: "0.0.0-test",
    settings: KEYS.filter(([k]) => !omit.includes(k)).map(([key, effective]) => ({
      key,
      effective,
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
let settingsStatus = 200;
let omitKeys = [];
let fetchCalls = 0;
globalThis.fetch = async (url, init = {}) => {
  fetchCalls++;
  const u = new URL(String(url));
  if (u.pathname === "/v1/settings") {
    return settingsStatus === 200 ? respond(200, fullSettings(omitKeys)) : respond(settingsStatus, { detail: "Invalid API key" });
  }
  if (u.pathname === "/v1/steam/key") return respond(200, { configured: false, key_last4: null });
  return respond(404, { detail: "Not Found" });
};

const { renderSettings } = await import("../js/views/settings.js");
const { isOnboardingOpen, closeOnboarding } = await import("../js/onboarding.js");

const tick = (ms = 10) => new Promise((r) => setTimeout(r, ms));

async function render() {
  const section = renderSettings();
  await tick();
  return section;
}
// The LOAD-error line only: the Steam section carries its own (hidden,
// empty) p.errline for key-save failures, so "first p.errline" is wrong.
const errLine = (s) =>
  s.querySelectorAll("p.errline").find((p) => /^Could not load settings: /.test(p.textContent || "")) || null;
// Compare text, never nodes: a failed node assertion makes node:assert dump
// the whole fake-DOM graph, which OOM-killed the test process (SIGKILL).
const errText = (s) => (errLine(s) ? errLine(s).textContent : null);
const demoNotice = (s) => s.querySelector('[data-role="demo-notice"]');
const connectBtn = (s) => s.querySelector('[data-role="connect"]');
const sectionHeadings = (s) => s.querySelectorAll("h4.sec").map((h) => h.textContent);

beforeEach(() => {
  storage.clear();
  settingsStatus = 200;
  omitKeys = [];
  fetchCalls = 0;
  if (isOnboardingOpen()) closeOnboarding();
  resetModalStack(dom.document);
});

test("B2 MUTATION PIN: a 401 from GET /v1/settings renders the error AND the Connection section (the reconnect entry survives the error state)", async () => {
  storage.set("steamvault.apiKey", "stale-key");
  settingsStatus = 401;
  const s = await render();
  assert.ok(errLine(s), "error line expected");
  assert.match(errLine(s).textContent, /^Could not load settings: /);
  assert.deepEqual(sectionHeadings(s), ["Connection"], "moving buildConnectionSection() back below the early return hides the only way to reconnect");
  const btn = connectBtn(s);
  assert.ok(btn, "the reconnect button must render in the error state");
  assert.equal(btn.textContent, "Start");
  btn.dispatchEvent({ type: "click" });
  assert.equal(isOnboardingOpen(), true, "the button opens the real onboarding overlay in reconnect mode");
});

test("B1: in demo mode the Connection section says so and offers 'Connect to a vault'", async () => {
  storage.set("steamvault.demoMode", "1");
  const s = await render();
  assert.equal(fetchCalls, 0, "demo mode never touches the network");
  assert.equal(errText(s), null, "demo settings load fine");
  const notice = demoNotice(s);
  assert.ok(notice, "B1 MUTATION TARGET: the demo notice is the only in-app statement that the data is a sample");
  assert.match(notice.textContent, /^Demo mode — /);
  assert.match(notice.textContent, /Connect to a vault/);
  const btn = connectBtn(s);
  assert.equal(btn.textContent, "Connect");
  assert.ok(sectionHeadings(s).includes("Vault") && sectionHeadings(s).includes("Connection"));
  const ttl = s.querySelectorAll("span.ttl").find((t) => t.textContent === "Connect to a vault");
  assert.ok(ttl, "row title must not read 'Reconnect / switch account' when no vault is attached");
});

test("with a real key (demo off) the Connection section is the plain reconnect row — no demo notice", async () => {
  storage.set("steamvault.apiKey", "good");
  const s = await render();
  assert.equal(errText(s), null);
  assert.equal(demoNotice(s) === null, true, "no demo notice with a real key");
  assert.equal(connectBtn(s).textContent, "Start");
  assert.ok(s.querySelectorAll("span.ttl").some((t) => t.textContent === "Reconnect / switch account"));
});

test("P2 MUTATION PIN: a settings response missing one of the eight keys falls to the error path naming the key — never a throw that leaves 'Loading settings…' up", async () => {
  storage.set("steamvault.apiKey", "good");
  omitKeys = ["auto_gc"];
  const s = await render();
  const loading = s.querySelectorAll("p.empty").find((p) => p.textContent === "Loading settings…");
  assert.equal(loading, undefined, "the loading skeleton must be gone");
  assert.ok(errLine(s), "deleting the missingSettingKeys() guard makes buildScheduleSection() throw on `autoGcEntry.effective` after loadSettings() has already returned");
  assert.match(errLine(s).textContent, /missing: auto_gc/);
  assert.ok(connectBtn(s), "the Connection section renders here too");
});
