/**
 * DOM-wiring pins for WP PAIR-1: Settings → "Add a device"
 * (`web/js/components/add-device-sheet.js`) and the pairing confirm
 * (`web/js/components/pair-confirm.js`).
 *
 *   - Settings → PCs has an "Add" button that opens the "Add a device" sheet;
 *   - nothing secret is in the DOM before "Show", after "Hide", or after the
 *     sheet closes by its button, Escape or navigation (the WHOLE tree is
 *     scanned: text, attributes, value/href properties);
 *   - phone: an inline SVG QR code (role=img) for the exact pairing URI,
 *     the URI as "Open on this phone" link and as copyable text;
 *   - browser: the `#pair=` link as copyable text;
 *   - Windows: the command for the release vault-api reports; a dev build
 *     gets the note; a failing /v1/about an error line; the agent address
 *     field is prefilled with the page origin, validated, remembered in
 *     localStorage and used in the command;
 *   - demo mode and a missing key show the note and no option;
 *   - the pairing confirm is an alertdialog, focus on "Keep current key",
 *     Keep/Escape answer false, Replace true.
 *
 * Harness: fake-dom.js, Map-backed localStorage, routing `fetch` fake; the
 * REAL store-singleton polls it and is stopped in `after()`. Assertions
 * compare text, booleans and counts, never nodes (LEARNINGS WEB-FIX-1).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, after, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { createFakeDom, fakeKeyEvent } from "./fake-dom.js";

const KEY = "s3cr3t+key/&=ü";
const ORIGIN = "http://vault.test";

const dom = createFakeDom();
globalThis.document = dom.document;
const storage = new Map([["steamvault.apiKey", KEY]]);
let pathname = "/settings";
globalThis.window = {
  localStorage: {
    getItem: (k) => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => storage.set(k, String(v)),
  },
  location: {
    origin: ORIGIN,
    get pathname() {
      return pathname;
    },
    hash: "",
    search: "",
    reload() {},
  },
  history: {
    pushState(_s, _t, p) {
      pathname = p;
    },
  },
  isSecureContext: false,
  addEventListener() {},
  removeEventListener() {},
};

const COMMIT = "3f9c2a71d4be08e5c6a1f02b9d7e4c18a5b6f3d0";
const aboutFor = (version, commit = COMMIT) => ({
  components: [{ name: "vault-api", version, commit, status: "ok", checked_at: "2026-10-04T10:00:00Z", detail: null }],
});
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
const SETTINGS = {
  readonly: false,
  server_version: "0.1.0-rc10",
  settings: KEYS.map(([key, effective]) => ({ key, effective, source: "default", fallback: effective, applies: "immediately", env_only: false })),
};
const server = { about: aboutFor("0.1.0-rc10"), aboutStatus: 200, aboutGets: 0 };
function respond(status, data) {
  const text = JSON.stringify(data);
  return { ok: status >= 200 && status < 300, status, json: async () => data, text: async () => text };
}
globalThis.fetch = async (url) => {
  const p = new URL(String(url)).pathname;
  if (p === "/v1/about") {
    server.aboutGets += 1;
    return server.aboutStatus === 200 ? respond(200, server.about) : respond(server.aboutStatus, { detail: "Not Found" });
  }
  if (p === "/v1/settings") return respond(200, SETTINGS);
  if (p === "/v1/steam/key") return respond(200, { configured: false, key_last4: null });
  if (p === "/v1/clients" || p === "/v1/jobs" || p === "/v1/games") return respond(200, []);
  if (p === "/v1/cache/summary") return respond(200, {});
  return respond(404, { detail: "Not Found" });
};

const { store } = await import("../js/store-singleton.js");
const { renderSettings } = await import("../js/views/settings.js");
const { openAddDeviceSheet } = await import("../js/components/add-device-sheet.js");
const { confirmPairReplace } = await import("../js/components/pair-confirm.js");
const { resetModalStack } = await import("../js/lib/modal-stack.js");
const { navigateTo } = await import("../js/router.js");

after(() => store.stop());

const tick = (ms = 10) => new Promise((r) => setTimeout(r, ms));
async function until(pred, label, timeoutMs = 2000) {
  const start = Date.now();
  while (!pred()) {
    if (Date.now() - start > timeoutMs) throw new Error(`timed out waiting for: ${label}`);
    await tick(5);
  }
}

const divs = () => dom.document.body.querySelectorAll("div");
const sheet = () => divs().find((d) => d.getAttribute("aria-label") === "Add a device") || null;
const sheetOpen = () => !!(sheet() && sheet().parentNode.classList.contains("on"));
const q = (role) => sheet().querySelector(`[data-role="${role}"]`);
const click = (el) => el.dispatchEvent({ type: "click" });
const reveal = (id) => q(`reveal-${id}`);
const showBtn = (id) => q(`show-${id}`);

/** Every string the DOM under `root` exposes: text nodes, attribute values,
 * and the value/href properties the components set directly. */
function domStrings(root) {
  const out = [];
  (function visit(n) {
    if (n.tagName === "#TEXT") out.push(n._text || "");
    for (const v of n._attrs ? n._attrs.values() : []) out.push(v);
    for (const p of ["value", "href"]) if (typeof n[p] === "string") out.push(n[p]);
    for (const c of n.childNodes || []) visit(c);
  })(root);
  return out;
}
const SECRET_FORMS = [KEY, encodeURIComponent(KEY), "s3cr3t"];
const secretInDom = () => domStrings(dom.document.body).some((s) => SECRET_FORMS.some((f) => s.includes(f)));

function closeSheetIfOpen() {
  if (sheetOpen()) click(sheet().querySelectorAll("button").find((b) => b.textContent === "Close"));
}

beforeEach(() => {
  closeSheetIfOpen();
  resetModalStack(dom.document);
  storage.set("steamvault.apiKey", KEY);
  storage.delete("steamvault.demoMode");
  storage.delete("steamvault.agentServerUrl");
  server.about = aboutFor("0.1.0-rc10");
  server.aboutStatus = 200;
});

test("MUTATION TARGET: Settings → PCs has an 'Add' button that opens the 'Add a device' sheet", async () => {
  const s = renderSettings();
  dom.appRoot.replaceChildren(s);
  await until(() => s.querySelector('[data-role="add-device"]') !== null, "settings loaded");
  const btn = s.querySelector('[data-role="add-device"]');
  assert.equal(btn.textContent, "Add");
  assert.equal(btn.getAttribute("aria-label"), "Add a device");
  click(btn);
  assert.equal(sheetOpen(), true);
  assert.equal(sheet().getAttribute("role"), "dialog");
  assert.equal(sheet().querySelector("h2").textContent, "Add a device");
});

test("MUTATION TARGET: nothing secret in the DOM until Show; every option starts hidden with the warning", () => {
  openAddDeviceSheet();
  assert.equal(secretInDom(), false);
  for (const id of ["phone", "browser", "windows"]) {
    assert.equal(reveal(id).hidden, true, `${id} hidden`);
    assert.equal(reveal(id).childNodes.length, 0, `${id} empty`);
    assert.equal(showBtn(id).getAttribute("aria-expanded"), "false");
    assert.equal(showBtn(id).getAttribute("aria-controls"), reveal(id).id);
  }
  const warnings = sheet().querySelectorAll(".pair-warn").map((p) => p.textContent);
  assert.equal(warnings.length, 3);
  for (const w of warnings) assert.match(w, /Anyone who sees this can control your hangar\. Only show it on your own screen\./);
});

test("MUTATION TARGET: phone — Show renders the QR (SVG, role=img) and the exact URI as link and text; Hide removes it", () => {
  openAddDeviceSheet();
  click(showBtn("phone"));
  const expected = `steamhangar://pair?v=1&url=${encodeURIComponent(ORIGIN)}&key=${encodeURIComponent(KEY)}`;
  const qr = reveal("phone").querySelector('[data-role="pair-qr"]');
  assert.equal(qr === null, false, "QR present");
  assert.equal(qr.tagName, "SVG");
  assert.equal(qr.getAttribute("role"), "img");
  assert.match(qr.getAttribute("aria-label"), /QR code/);
  assert.equal(qr.getAttribute("viewBox"), `0 0 ${Number(qr.getAttribute("data-qr-version")) * 4 + 17 + 8} ${Number(qr.getAttribute("data-qr-version")) * 4 + 17 + 8}`);
  const [bg, path] = qr.children;
  assert.equal(bg.getAttribute("fill"), "#ffffff", "white plate");
  assert.equal(path.getAttribute("fill"), "#000000", "black modules");
  const link = reveal("phone").querySelector('[data-role="pair-app-link"]');
  assert.equal(link.textContent, "Open on this phone");
  assert.equal(link.href, expected);
  assert.equal(reveal("phone").querySelector('[data-role="pair-app-uri"]').value, expected);
  assert.equal(showBtn("phone").textContent, "Hide");
  assert.equal(showBtn("phone").getAttribute("aria-expanded"), "true");
  assert.equal(reveal("phone").hidden, false);

  click(showBtn("phone"));
  assert.equal(reveal("phone").hidden, true);
  assert.equal(reveal("phone").childNodes.length, 0);
  assert.equal(showBtn("phone").textContent, "Show QR code");
  assert.equal(secretInDom(), false, "Hide takes the secret out of the DOM");
});

test("browser — Show renders the #pair= link as copyable text", () => {
  openAddDeviceSheet();
  click(showBtn("browser"));
  assert.equal(reveal("browser").querySelector('[data-role="pair-browser-link"]').value, `${ORIGIN}/#pair=${encodeURIComponent(KEY)}`);
  assert.equal(
    reveal("browser").querySelectorAll("button").map((b) => b.textContent).join(","),
    "Copy link",
  );
});

test("MUTATION TARGET: closing the sheet (button, Escape, navigation) drops every revealed secret", async () => {
  for (const closeBy of ["button", "escape", "navigation"]) {
    openAddDeviceSheet();
    click(showBtn("phone"));
    click(showBtn("browser"));
    click(showBtn("windows"));
    await until(() => reveal("windows").querySelector('[data-role="agent-snippet"]') !== null, "snippet");
    assert.equal(secretInDom(), true, "shown");
    if (closeBy === "button") closeSheetIfOpen();
    else if (closeBy === "escape") dom.document.dispatchEvent(fakeKeyEvent("Escape"));
    else navigateTo("library");
    assert.equal(sheetOpen(), false, `closed by ${closeBy}`);
    assert.equal(secretInDom(), false, `no secret after closing by ${closeBy}`);
    for (const id of ["phone", "browser", "windows"]) assert.equal(showBtn(id).getAttribute("aria-expanded"), "false");
    resetModalStack(dom.document);
  }
  navigateTo("settings");
});

test("MUTATION TARGET: Windows — the command for the reported release, prefilled with the page origin", async () => {
  openAddDeviceSheet();
  const input = q("agent-url");
  assert.equal(input.value, ORIGIN, "prefill: the page origin");
  const note = sheet().querySelectorAll("p").find((p) => p.id === "pair-agent-url-note");
  assert.match(note.textContent, /direct LAN address .* not a reverse-proxy hostname/);
  assert.match(input.getAttribute("aria-describedby"), /pair-agent-url-note/);
  assert.notEqual(note.hidden, true, "the note is visible before Show");
  click(showBtn("windows"));
  await until(() => reveal("windows").querySelector('[data-role="agent-snippet"]') !== null, "snippet");
  const snippet = reveal("windows").querySelector('[data-role="agent-snippet"]').value;
  assert.match(snippet, /\$version = '0\.1\.0-rc10'/);
  assert.match(snippet, /releases\/download\/v0\.1\.0-rc10'/);
  assert.match(snippet, /\$serverUrl = 'http:\/\/vault\.test'/);
  assert.equal(snippet.split(KEY).length - 1, 1, "key once");
  assert.match(snippet, /Get-FileHash -Algorithm SHA256/);
});

test("MUTATION TARGET: Windows — the agent address is validated, remembered and used", async () => {
  openAddDeviceSheet();
  click(showBtn("windows"));
  await until(() => reveal("windows").querySelector('[data-role="agent-snippet"]') !== null, "snippet");
  const input = q("agent-url");

  input.value = "hangar.example.org";
  input.dispatchEvent({ type: "input" });
  assert.equal(q("agent-url-error").hidden, false);
  assert.match(q("agent-url-error").textContent, /Not a valid address/);
  assert.equal(input.getAttribute("aria-invalid"), "true");
  assert.equal(reveal("windows").querySelector('[data-role="agent-snippet"]'), null, "no command for a bad address");
  assert.equal(reveal("windows").querySelector('[data-role="agent-url-blocked"]') === null, false);
  assert.equal(storage.has("steamvault.agentServerUrl"), false, "an invalid value is not remembered");

  input.value = "http://192.0.2.10:8080/";
  input.dispatchEvent({ type: "input" });
  assert.equal(q("agent-url-error").hidden, true);
  assert.equal(input.getAttribute("aria-invalid"), null);
  assert.equal(storage.get("steamvault.agentServerUrl"), "http://192.0.2.10:8080");
  assert.match(reveal("windows").querySelector('[data-role="agent-snippet"]').value, /\$serverUrl = 'http:\/\/192\.0\.2\.10:8080'/);

  closeSheetIfOpen();
  openAddDeviceSheet();
  assert.equal(q("agent-url").value, "http://192.0.2.10:8080", "remembered across opens");
});

test("MUTATION TARGET: Windows — a dev build gets the note, never a command", async () => {
  server.about = aboutFor("dev-1a2b3c4");
  openAddDeviceSheet();
  click(showBtn("windows"));
  await until(() => reveal("windows").querySelector('[data-role="agent-no-release"]') !== null, "note");
  assert.match(reveal("windows").querySelector('[data-role="agent-no-release"]').textContent, /"dev-1a2b3c4", which is not a published release/);
  assert.equal(reveal("windows").querySelector('[data-role="agent-snippet"]'), null);
  assert.equal(secretInDom(), false, "the note carries no key");
});

test("Windows — a server without /v1/about shows an error line", async () => {
  server.aboutStatus = 404;
  openAddDeviceSheet();
  click(showBtn("windows"));
  await until(() => reveal("windows").querySelector('[data-role="agent-error"]') !== null, "error");
  assert.match(reveal("windows").querySelector('[data-role="agent-error"]').textContent, /older than GET \/v1\/about/);
});

test("MUTATION TARGET: demo mode and a missing key show the note and no option", () => {
  storage.set("steamvault.demoMode", "1");
  openAddDeviceSheet();
  assert.equal(q("pair-unavailable").hidden, false);
  assert.match(q("pair-unavailable").textContent, /^Demo mode/);
  assert.equal(sheet().querySelector(".pair-options").hidden, true);
  click(showBtn("phone")); // even a stray click reveals nothing
  assert.equal(reveal("phone").childNodes.length, 0);
  closeSheetIfOpen();

  storage.delete("steamvault.demoMode");
  storage.set("steamvault.apiKey", "");
  openAddDeviceSheet();
  assert.match(q("pair-unavailable").textContent, /no API key stored/);
  assert.equal(sheet().querySelector(".pair-options").hidden, true);
  closeSheetIfOpen();

  storage.set("steamvault.apiKey", KEY);
  openAddDeviceSheet();
  assert.equal(q("pair-unavailable").hidden, true);
  assert.equal(sheet().querySelector(".pair-options").hidden, false);
});

test("MUTATION TARGET: pairing confirm — alertdialog, focus on Keep; Keep/Escape false, Replace true", async () => {
  const dlg = () => divs().find((d) => d.getAttribute("data-role") === "pair-confirm") || null;
  const isOn = () => !!(dlg() && dlg().parentNode.classList.contains("on"));
  const btn = (role) => dlg().querySelector(`[data-role="${role}"]`);

  let answer = confirmPairReplace();
  assert.equal(isOn(), true);
  assert.equal(dlg().getAttribute("role"), "alertdialog");
  assert.equal(dlg().querySelector("h3").textContent, "Replace this browser's API key?");
  assert.equal(dom.document.activeElement.textContent, "Keep current key");
  click(btn("pair-keep"));
  assert.equal(await answer, false);
  assert.equal(isOn(), false);

  answer = confirmPairReplace();
  dom.document.dispatchEvent(fakeKeyEvent("Escape"));
  assert.equal(await answer, false);

  answer = confirmPairReplace();
  click(btn("pair-replace"));
  assert.equal(await answer, true);
  assert.equal(domStrings(dlg()).some((s) => s.includes("s3cr3t")), false, "the dialog names no key");
});
