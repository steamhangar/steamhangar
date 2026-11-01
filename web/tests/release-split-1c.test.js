/**
 * WP RELEASE-SPLIT-1c (ADR-0031 item 9, ABOUT-VERSION-1 Weg A):
 *
 *   - `describeRelease` (lib/about-view.js): product version as the main
 *     line, Server/Agent/App with "(unchanged since <version>)" for a
 *     component older than the product; nothing for a server before 1c or a
 *     local build;
 *   - Settings → About renders it above the component table as a
 *     definition list (DOM wiring, fake-dom);
 *   - twin pin: the Android strings (`settings_about_release_*`) carry the
 *     same words;
 *   - the 1b nit: `appDownloadPlan` reports the APK's own version.
 *
 * Harness as settings-about-pcs-wiring.test.js; assertions compare text.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { createFakeDom } from "./fake-dom.js";

const dom = createFakeDom();
globalThis.document = dom.document;
const storage = new Map([["steamvault.apiKey", "test-key"]]);
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

const {
  RELEASE_LABELS,
  describeRelease,
  productLine,
  releaseValueText,
  unchangedSinceText,
} = await import("../js/lib/about-view.js");
const { appDownloadPlan } = await import("../js/lib/dist-downloads.js");

const RC23 = {
  version: "0.1.0-rc23",
  components: [
    { name: "server", version: "0.1.0-rc22" },
    { name: "agent", version: "0.1.0-rc17" },
    { name: "app", version: "0.1.0-rc23" },
  ],
};
const NO_COMPONENTS = { version: null, components: [{ name: "server", version: null }, { name: "agent", version: null }, { name: "app", version: null }] };

// --- presentation ------------------------------------------------------------

test("describeRelease: product line, then Server/Agent/App with 'unchanged since' for older components", () => {
  const view = describeRelease(RC23);
  assert.equal(view.productLine, "SteamHangar 0.1.0-rc23");
  assert.deepEqual(
    view.rows.map((r) => [r.label, r.text]),
    [
      ["Server", "0.1.0-rc22 (unchanged since 0.1.0-rc22)"],
      ["Agent", "0.1.0-rc17 (unchanged since 0.1.0-rc17)"],
      ["App", "0.1.0-rc23"],
    ],
  );
  assert.deepEqual(view.rows.map((r) => r.unchangedSince), ["0.1.0-rc22", "0.1.0-rc17", null]);
});

test("MUTATION TARGET: 'unchanged since' follows the natural version order, not string order", () => {
  // String order would put rc9 after rc17 and rc21.1 after the release.
  const view = describeRelease({
    version: "0.1.0",
    components: [{ name: "server", version: "0.1.0-rc21.1" }, { name: "agent", version: "0.1.0-rc9" }, { name: "app", version: "0.1.0" }],
  });
  assert.deepEqual(view.rows.map((r) => r.unchangedSince), ["0.1.0-rc21.1", "0.1.0-rc9", null]);
  const rc17 = describeRelease({ version: "0.1.0-rc17", components: [{ name: "agent", version: "0.1.0-rc9" }] });
  assert.equal(rc17.rows[0].unchangedSince, "0.1.0-rc9");
});

test("describeRelease: nothing for a server before 1c or a local build; junk versions are left out", () => {
  assert.equal(describeRelease(undefined), null, "server before 1c: no `release`");
  assert.equal(describeRelease(null), null);
  assert.equal(describeRelease(NO_COMPONENTS), null, "local build: no component versions");
  assert.equal(describeRelease({ version: "0.1.0", components: [] }), null);
  const junk = describeRelease({
    version: "0.1.0-rc23",
    components: [{ name: "server", version: "dev-1234567" }, { name: "agent", version: "invalid" }, { name: "app", version: "0.1.0-rc23" }, { name: "x", version: "1.0.0" }],
  });
  assert.deepEqual(junk.rows.map((r) => r.name), ["app"]);
  // Fixed order whatever the answer's order.
  const reversed = describeRelease({ ...RC23, components: [...RC23.components].reverse() });
  assert.deepEqual(reversed.rows.map((r) => r.name), ["server", "agent", "app"]);
  // Product unknown: no main line and no comparison.
  const noProduct = describeRelease({ ...RC23, version: "dev" });
  assert.equal(noProduct.productLine, null);
  assert.deepEqual(noProduct.rows.map((r) => r.text), ["0.1.0-rc22", "0.1.0-rc17", "0.1.0-rc23"]);
});

test("wording helpers", () => {
  assert.deepEqual(RELEASE_LABELS, { server: "Server", agent: "Agent", app: "App" });
  assert.equal(productLine("0.1.0"), "SteamHangar 0.1.0");
  assert.equal(unchangedSinceText("0.1.0-rc22"), "(unchanged since 0.1.0-rc22)");
  assert.equal(releaseValueText("0.1.0-rc22", "0.1.0-rc22"), "0.1.0-rc22 (unchanged since 0.1.0-rc22)");
  assert.equal(releaseValueText("0.1.0-rc23", null), "0.1.0-rc23");
});

// --- twin pin (Android strings) ----------------------------------------------

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const stringsXml = readFileSync(path.join(__dirname, "..", "..", "app", "app", "src", "main", "res", "values", "strings.xml"), "utf8");
function res(name) {
  const m = new RegExp(`<string name="${name}"[^>]*>(.*?)</string>`).exec(stringsXml);
  assert.ok(m, `strings.xml has no <string name="${name}"> — the Android twin lost it (fix the app, or re-point this pin)`);
  return m[1].replace(/\\'/g, "'");
}
const drift = (name) => `${name} differs between strings.xml and about-view.js — change BOTH (and ReleaseSplit1cAboutTest.kt)`;

test("twin: the component labels and the 'unchanged since' value match the app's strings", () => {
  for (const [name, label] of Object.entries(RELEASE_LABELS)) {
    assert.equal(res(`settings_about_release_${name}`), label, drift(`settings_about_release_${name}`));
  }
  const format = res("settings_about_release_unchanged");
  assert.equal(format.replace("%1$s", "0.1.0-rc22").replace("%2$s", "0.1.0-rc21"), releaseValueText("0.1.0-rc22", "0.1.0-rc21"), drift("settings_about_release_unchanged"));
});

// --- DOM wiring (Settings → About) -------------------------------------------

const NOW = Date.now();
const checkedAt = new Date(NOW - 60_000).toISOString().replace(/\.\d{3}Z$/, "Z");
const COMPONENTS = ["vault-api", "vault-core", "vault-runner", "steamprefill", "vault-proxy", "vault-dns"].map((name) => ({
  name,
  version: "0.1.0-rc23",
  commit: "3f9c2a71d4be08e5c6a1f02b9d7e4c18a5b6f3d0",
  status: name === "vault-core" || name === "vault-dns" ? "unknown" : "ok",
  checked_at: checkedAt,
  detail: null,
}));
const SETTINGS = {
  readonly: false,
  server_version: "0.1.0-rc23",
  settings: [
    ["vault_name", "vault-01"],
    ["schedule_window", null],
    ["schedule_interval_minutes", 180],
    ["schedule_client_stale_days", 14],
    ["sweep_include_cached", true],
    ["auto_gc", "execute"],
    ["webhook_url", ""],
    ["webhook_events", []],
  ].map(([key, effective]) => ({
    key, effective, source: "default", fallback: effective, applies: "immediately", env_only: false,
  })),
};
const server = { about: { components: COMPONENTS, release: RC23 } };
function respond(status, data) {
  const text = JSON.stringify(data);
  return { ok: status >= 200 && status < 300, status, json: async () => data, text: async () => text };
}
globalThis.fetch = async (url) => {
  const p = new URL(String(url)).pathname;
  if (p === "/v1/about") return respond(200, server.about);
  if (p === "/v1/settings") return respond(200, SETTINGS);
  if (p === "/v1/clients") return respond(200, []);
  if (p === "/v1/steam/key") return respond(200, { configured: false, key_last4: null });
  if (p === "/v1/schedule") return respond(404, { detail: "Not Found" });
  if (p === "/v1/jobs" || p === "/v1/games") return respond(200, []);
  if (p === "/v1/cache/summary") return respond(200, {});
  return respond(404, { detail: "Not Found" });
};

const { store } = await import("../js/store-singleton.js");
const { renderSettings } = await import("../js/views/settings.js");
after(() => store.stop());

const tick = (ms = 10) => new Promise((r) => setTimeout(r, ms));
async function until(pred, label, timeoutMs = 2000) {
  const start = Date.now();
  while (!pred()) {
    if (Date.now() - start > timeoutMs) throw new Error(`timed out waiting for: ${label}`);
    await tick(5);
  }
}
async function mountAbout() {
  const s = renderSettings();
  dom.appRoot.replaceChildren(s);
  await until(() => s.querySelector('tr[data-component="vault-dns"]') !== null, "about table");
  return s;
}
const releaseItems = (s) =>
  s.querySelectorAll(".about-release-item").map((item) => [
    item.dataset.component,
    item.querySelector("dt").textContent,
    item.querySelector("dd").textContent,
  ]);

test("About: the product line and a definition list of the components above the table", async () => {
  server.about = { components: COMPONENTS, release: RC23 };
  const s = await mountAbout();
  const block = s.querySelector('[data-role="about-release"]');
  assert.ok(block, "release block rendered");
  assert.equal(s.querySelector('[data-role="about-product"]').textContent, "SteamHangar 0.1.0-rc23");
  assert.equal(block.querySelector("dl").tagName, "DL", "a definition list");
  assert.deepEqual(releaseItems(s), [
    ["server", "Server", "0.1.0-rc22 (unchanged since 0.1.0-rc22)"],
    ["agent", "Agent", "0.1.0-rc17 (unchanged since 0.1.0-rc17)"],
    ["app", "App", "0.1.0-rc23"],
  ]);
  assert.equal(s.querySelectorAll('[data-role="about-unchanged-since"]').length, 2);
  // Above the table, in the same content area.
  const content = s.querySelector(".about-content");
  assert.deepEqual(content.children.map((c) => c.className), ["about-release", "about-table"]);
});

test("About: no release block for a local build or a server before 1c; the table stays", async () => {
  server.about = { components: COMPONENTS, release: NO_COMPONENTS };
  let s = await mountAbout();
  assert.equal(s.querySelector('[data-role="about-release"]'), null);
  assert.equal(s.querySelectorAll("tr.about-row").length, 6);
  server.about = { components: COMPONENTS };
  s = await mountAbout();
  assert.equal(s.querySelector('[data-role="about-release"]'), null);
  assert.equal(s.querySelectorAll("tr.about-row").length, 6);
});

// --- the 1b nit: appDownloadPlan's version ------------------------------------

const GH = "https://github.com/steamhangar/steamhangar/releases/download";
function distIndex({ local }) {
  const apk = "steamhangar-app-v0.1.0-rc22.apk";
  return {
    version: "0.1.0-rc23",
    release: true,
    local_base: "/dist/",
    release_base: `${GH}/v0.1.0-rc23/`,
    apk_alias_url: local ? "/dist/steamhangar-app.apk" : null,
    artifacts: [{
      id: "app-android", name: apk, version: "0.1.0-rc22", local, size: local ? 10 : null, sha256: null,
      local_url: local ? `/dist/${apk}` : null, release_url: `${GH}/v0.1.0-rc22/${apk}`, url: null, source: local ? "local" : "release",
    }],
    clients: {
      agent: null,
      app: { version: "0.1.0-rc22", build: "0.1.0-rc22", minimum: "0.1.0-rc17", same_build: ["0.1.0-rc22"] },
    },
  };
}

test("MUTATION TARGET: a reused APK's download plan reports the APK's version, not the server's", () => {
  const local = appDownloadPlan(distIndex({ local: true }), "http://hangar.lan:8080");
  assert.equal(local.kind, "download");
  assert.equal(local.source, "local");
  assert.equal(local.version, "0.1.0-rc22");
  const release = appDownloadPlan(distIndex({ local: false }), "http://hangar.lan:8080");
  assert.equal(release.source, "release");
  assert.equal(release.version, "0.1.0-rc22");
  // "none" keeps the server's version (noAppText names what the server reports).
  const none = appDownloadPlan({ ...distIndex({ local: false }), release: false }, "http://hangar.lan:8080");
  assert.deepEqual(none, { kind: "none", version: "0.1.0-rc23" });
});
