/**
 * DOM-wiring pins for `web/js/onboarding.js` (WP WEB-FIX-1: B1, N3, N5 and
 * the B2 `notice`/`isOnboardingOpen` surface).
 *
 * Before this file, onboarding.js had no headless coverage at all — its
 * step MACHINE is pinned in `onboarding-steps.test.js`, but every wiring
 * from that machine to the DOM (what "Test connection" actually does, what
 * "Go to library" actually sends, which localStorage flags move) was
 * unpinned, and that is where all three of this WP's onboarding findings
 * lived:
 *   - B1: demo mode was a one-way door. `setDemoMode(true)` had one caller
 *     and `setDemoMode(false)` had none, so a user who once chose "browse
 *     in demo mode" stayed in demo forever — a reconnect verified and
 *     stored a real key, reloaded, and `api.js` kept short-circuiting every
 *     request to the fixtures.
 *   - N3: the OK line rendered `health.version`, which `GET /v1/health`
 *     never sends (a fixed `{"status":"ok"}`).
 *   - N5: "Test connection" also PATCHed `vault_name` — a write behind a
 *     button labelled as a test. The name is now saved by `finish()`.
 *
 * Harness: the shared `fake-dom.js` (extended in this WP with fragments,
 * text nodes and a minimal `innerHTML` setter for `staticIcon()`), a
 * Map-backed `localStorage`, a `location.reload` counter, and a routing
 * `fetch` fake that records every call (method, path, headers, body) — so
 * "no PATCH happened" is asserted on the wire, not on a flag.
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
let reloads = 0;
globalThis.window = {
  localStorage: {
    getItem: (k) => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => storage.set(k, String(v)),
  },
  location: {
    origin: "http://vault.test",
    pathname: "/settings",
    reload() {
      reloads++;
    },
  },
  history: { pushState() {} },
  addEventListener() {},
  removeEventListener() {},
};

// --- fetch fake -------------------------------------------------------
const GOOD_KEY = "good-key";
let calls = [];
let serverVaultName = "vault-01";
let readonly = false;
let patchStatus = 200;

function settingsBody() {
  return {
    readonly,
    settings: [
      { key: "vault_name", effective: serverVaultName, source: "default", fallback: "vault-01", applies: "restart-required", env_only: false },
    ],
  };
}
function respond(status, data) {
  const text = JSON.stringify(data);
  return { ok: status >= 200 && status < 300, status, json: async () => data, text: async () => text };
}
globalThis.fetch = async (url, init = {}) => {
  const u = new URL(String(url));
  const method = init.method || "GET";
  const headers = init.headers || {};
  calls.push({ method, path: u.pathname, headers, body: init.body ? JSON.parse(init.body) : undefined });
  if (u.pathname === "/v1/health") return respond(200, { status: "ok", version: "9.9.9-should-be-ignored" });
  if (headers["X-Api-Key"] !== GOOD_KEY) return respond(401, { detail: "Invalid API key" });
  if (u.pathname === "/v1/settings" && method === "GET") return respond(200, settingsBody());
  if (u.pathname === "/v1/settings" && method === "PATCH") {
    if (patchStatus !== 200) return respond(patchStatus, { detail: "vault_name: too long" });
    serverVaultName = init.body ? JSON.parse(init.body).vault_name : serverVaultName;
    return respond(200, settingsBody());
  }
  if (u.pathname === "/v1/steam/key") return respond(200, { configured: false, key_last4: null });
  return respond(404, { detail: "Not Found" });
};

const onb = await import("../js/onboarding.js");
const { openOnboarding, closeOnboarding, isOnboardingOpen } = onb;

const tick = (ms = 5) => new Promise((r) => setTimeout(r, ms));

function root() {
  return dom.document.body.querySelector("div.onb");
}
function buttonByText(text) {
  const b = root().querySelectorAll("button").find((x) => x.textContent === text);
  assert.ok(b, `button "${text}" not found`);
  return b;
}
function nameInput() {
  return root().querySelector("div.field input");
}
function keyInput() {
  return root().querySelector("div.inp input");
}
// Scoped by the step section's data-step, not by document order: step 2
// carries its own p.errline (the Steam-key error), so a positional index
// silently picks the wrong node.
function stepErrLine(step) {
  const sec = root().querySelectorAll("section.ostep").find((s) => s.dataset.step === String(step));
  assert.ok(sec, `onboarding step section ${step} not found`);
  const line = sec.querySelector("p.errline");
  assert.ok(line, `step ${step} has no p.errline`);
  return line;
}
function step1ErrLine() {
  return stepErrLine(1);
}
function step3ErrLine() {
  return stepErrLine(3);
}
function okText() {
  return root().querySelector("div.okline span");
}
function click(btn) {
  btn.dispatchEvent({ type: "click" });
  return tick();
}
const patchCalls = () => calls.filter((c) => c.method === "PATCH");

async function testKey(key) {
  keyInput().value = key;
  await click(buttonByText("Test connection"));
}
async function walkToDone() {
  await click(buttonByText("Continue")); // -> step 2
  await click(buttonByText("Continue")); // -> step 3
}

beforeEach(() => {
  // Close FIRST: a previous test may have left a verified open behind, and
  // closing it now reloads (round 2, S1) — counters are reset after that.
  if (isOnboardingOpen()) closeOnboarding();
  storage.clear();
  calls = [];
  reloads = 0;
  serverVaultName = "vault-01";
  readonly = false;
  patchStatus = 200;
  resetModalStack(dom.document);
});

test("B1/N5/N3 — demo reconnect, successful key test: stores the key, ENDS demo mode, sends no PATCH, shows the fixed OK line", async () => {
  storage.set("steamvault.demoMode", "1");
  openOnboarding({ mode: "reconnect" });
  await testKey(GOOD_KEY);

  assert.equal(storage.get("steamvault.apiKey"), GOOD_KEY);
  assert.equal(storage.get("steamvault.demoMode"), "0", "B1 MUTATION TARGET: deleting setDemoMode(false) leaves the browser in demo forever");
  assert.deepEqual(patchCalls(), [], "N5 MUTATION TARGET: 'Test connection' must not write vault_name");
  assert.deepEqual(
    calls.map((c) => `${c.method} ${c.path}`),
    ["GET /v1/health", "GET /v1/settings"],
    "a test is exactly reachability + one authenticated read",
  );
  assert.equal(okText().textContent, "200 OK · vault-api", "N3: no version suffix — /v1/health carries none, and the fake's bogus one must be ignored");
  assert.equal(nameInput().value, "vault-01", "the name field is pre-filled from the server (a read, still allowed)");
  assert.equal(buttonByText("Continue").disabled, false);
});

test("a rejected key: error line says so, nothing stored, demo flag untouched, Continue stays locked", async () => {
  storage.set("steamvault.demoMode", "1");
  openOnboarding({ mode: "reconnect" });
  await testKey("wrong-key");
  assert.equal(step1ErrLine().hidden, false);
  assert.equal(step1ErrLine().textContent, "That API key was rejected.");
  assert.equal(storage.get("steamvault.apiKey"), undefined);
  assert.equal(storage.get("steamvault.demoMode"), "1");
  assert.equal(buttonByText("Continue").disabled, true);
});

test("N5 — finish(): a CHANGED vault name is PATCHed by 'Go to library', then the page reloads once, out of demo", async () => {
  storage.set("steamvault.demoMode", "1");
  openOnboarding({ mode: "reconnect" });
  await testKey(GOOD_KEY);
  nameInput().value = "hangar-two";
  await walkToDone();
  assert.deepEqual(patchCalls(), [], "still no PATCH before the final step");
  await click(buttonByText("Go to library"));
  assert.equal(patchCalls().length, 1, "N5 MUTATION TARGET: the name write moved to finish() — deleting it loses the typed name silently");
  assert.deepEqual(patchCalls()[0].body, { vault_name: "hangar-two" });
  assert.equal(patchCalls()[0].headers["X-Api-Key"], GOOD_KEY, "the write goes to the REAL vault with the verified key (demo already off)");
  assert.equal(reloads, 1);
  assert.equal(storage.get("steamvault.demoMode"), "0");
});

test("finish(): an UNCHANGED name (pre-filled from the server) sends no PATCH — just the reload", async () => {
  openOnboarding({ mode: "first-run" });
  await testKey(GOOD_KEY);
  await walkToDone();
  await click(buttonByText("Go to library"));
  assert.deepEqual(patchCalls(), []);
  assert.equal(reloads, 1);
});

test("finish(): a read-only vault never gets a PATCH even with a changed name", async () => {
  readonly = true;
  openOnboarding({ mode: "reconnect" });
  await testKey(GOOD_KEY);
  nameInput().value = "renamed";
  await walkToDone();
  await click(buttonByText("Go to library"));
  assert.deepEqual(patchCalls(), []);
  assert.equal(reloads, 1);
});

test("finish(): a FAILED name save is shown on step 3 and does NOT reload; pressing again retries", async () => {
  patchStatus = 422;
  openOnboarding({ mode: "reconnect" });
  await testKey(GOOD_KEY);
  nameInput().value = "renamed";
  await walkToDone();
  await click(buttonByText("Go to library"));
  assert.equal(patchCalls().length, 1);
  assert.equal(reloads, 0, "a reload would wipe the only place the failure is reported");
  assert.equal(step3ErrLine().hidden, false);
  assert.match(step3ErrLine().textContent, /Vault name not saved: vault_name: too long/);
  assert.match(step3ErrLine().textContent, /go Back and clear the name/);
  assert.equal(buttonByText("Go to library").disabled, false, "re-enabled for a retry");

  patchStatus = 200;
  await click(buttonByText("Go to library"));
  assert.equal(patchCalls().length, 2, "second press retries the write");
  assert.equal(reloads, 1);
  assert.equal(step3ErrLine().hidden, true);
});

test("B2 surface — openOnboarding({notice}) shows the notice in step 1's error line; isOnboardingOpen() tracks open/close", async () => {
  assert.equal(isOnboardingOpen(), false);
  openOnboarding({ mode: "reconnect", notice: "The vault rejected the stored API key." });
  assert.equal(isOnboardingOpen(), true);
  assert.equal(step1ErrLine().hidden, false);
  assert.equal(step1ErrLine().textContent, "The vault rejected the stored API key.");
  closeOnboarding();
  assert.equal(isOnboardingOpen(), false);
  // A plain open clears any previous notice.
  openOnboarding({ mode: "reconnect" });
  assert.equal(step1ErrLine().hidden, true);
  assert.equal(step1ErrLine().textContent, "");
});

test("'Skip for now — browse in demo mode' still sets the flag and reloads (the B1 fix did not break the way IN)", async () => {
  openOnboarding({ mode: "first-run" });
  await click(buttonByText("Skip for now — browse in demo mode"));
  assert.equal(storage.get("steamvault.demoMode"), "1");
  assert.equal(reloads, 1);
});

// --- WP WEB-FIX-1 round 2 (S1): closing after a verified key test reloads ---

test("S1 — demo reconnect, key verified, then Skip: the overlay closes AND the page reloads (no half-switched app)", async () => {
  storage.set("steamvault.demoMode", "1");
  openOnboarding({ mode: "reconnect" });
  await testKey(GOOD_KEY);
  await click(buttonByText("Skip"));
  assert.equal(isOnboardingOpen(), false);
  assert.equal(reloads, 1, "S1 MUTATION TARGET: without the reload, polling hits the real vault while Settings still shows demo");
  assert.equal(storage.get("steamvault.demoMode"), "0");
});

test("S1 — key verified, then Escape: same reload", async () => {
  openOnboarding({ mode: "reconnect" });
  await testKey(GOOD_KEY);
  dom.document.dispatchEvent(fakeKeyEvent("Escape"));
  assert.equal(isOnboardingOpen(), false);
  assert.equal(reloads, 1);
});

test("S1 — a verified key followed by a FAILED re-test still reloads on close (the key is already stored)", async () => {
  openOnboarding({ mode: "reconnect" });
  await testKey(GOOD_KEY);
  await testKey("wrong-key");
  await click(buttonByText("Skip"));
  assert.equal(reloads, 1);
});

test("S1 — closing a reconnect with no successful test does NOT reload (nothing changed)", async () => {
  storage.set("steamvault.demoMode", "1");
  openOnboarding({ mode: "reconnect" });
  await testKey("wrong-key");
  await click(buttonByText("Skip"));
  dom.document.dispatchEvent(fakeKeyEvent("Escape"));
  assert.equal(isOnboardingOpen(), false);
  assert.equal(reloads, 0);
  assert.equal(storage.get("steamvault.demoMode"), "1");
});
