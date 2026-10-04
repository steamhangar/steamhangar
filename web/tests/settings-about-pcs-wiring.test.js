/**
 * DOM-wiring pins for WP WEB-FEAT-3 in `web/js/views/settings.js`,
 * `web/js/components/clients-sheet.js` and `web/js/components/rail-panel.js`:
 *
 *   - Settings → About renders `GET /v1/about` as a table, one row per
 *     component, with the status word for EVERY server status, the short
 *     commit, an em dash for a null version (WP WEB-FIX-8: never the word
 *     "unknown"), vault-core OK/Check/Not reported against vault-api,
 *     vault-dns N/A, and every explanation behind a per-row (i)
 *     disclosure button, collapsed by default;
 *   - 404 shows the "server too old" note, not an error; a 401 (key refused,
 *     on any server version) and a 500 are an error line (review fix);
 *     error line;
 *   - no polling: one `/v1/about` request per Settings mount, a second only
 *     on Refresh (whose role=status line then says what happened);
 *   - Settings → "PCs (agents)" opens the clients sheet with NO bypass
 *     banner in play; each PC shows the SERVER's presence (fixtures
 *     contradict the timestamps), "last seen", "version not reported", and the
 *     agents summary line;
 *   - the rail's version button calls its handler and is named "About: …";
 *     requestAboutFocus() lands focus on the About heading.
 *
 * Harness: fake-dom.js, Map-backed localStorage with a stored key, a
 * routing `fetch` fake counting calls per path; the REAL store-singleton
 * polls it and is stopped in `after()` (LEARNINGS WEB-FIX-1). Assertions
 * compare text, booleans and counts, never nodes.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, after, beforeEach } from "node:test";
import assert from "node:assert/strict";
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

const NOW = Date.now();
const isoAgo = (ms) => new Date(NOW - ms).toISOString().replace(/\.\d{3}Z$/, "Z");
const FULL_COMMIT = "3f9c2a71d4be08e5c6a1f02b9d7e4c18a5b6f3d0";

function component(name, status, extra = {}) {
  return { name, version: "0.1.0", commit: FULL_COMMIT, status, checked_at: isoAgo(5 * 60_000), detail: null, ...extra };
}
const ABOUT_ALL_STATUSES = {
  components: [
    component("vault-api", "ok", { detail: "Also serves the web UI, so the web UI has this version." }),
    component("vault-core", "unknown", { detail: "Recorded at vault-core's last start, 2026-10-02T10:00:00Z. <i>text</i>" }),
    component("vault-runner", "unreachable", { detail: "Last seen 2026-10-03T10:00:00Z, more than 90 s ago." }),
    component("steamprefill", "unknown", { commit: null }),
    component("vault-proxy", "not_in_use", { version: null, commit: null }),
    component("vault-dns", "unknown", { version: null, commit: null }),
  ],
};

const client = (id, extra) => ({
  client_id: id,
  first_seen: isoAgo(9 * 86_400_000),
  last_reported_at: isoAgo(60_000),
  app_count: 3,
  source_addrs: ["10.0.0.9"],
  cache_hits: 1,
  cache_misses: 1,
  bytes_served: 1,
  last_seen_in_cache_log: null,
  bypass_suspected: false,
  agent_version: "0.1.0",
  report_interval_seconds: 600,
  presence: "online",
  offline_after: isoAgo(-25 * 60_000),
  ...extra,
});
// Presence CONTRADICTS the timestamps on purpose: a client-side recompute
// from last_reported_at would flip both.
const CLIENTS = [
  client("old-but-online", { last_reported_at: isoAgo(3 * 86_400_000), presence: "online" }),
  client("fresh-but-offline", {
    last_reported_at: isoAgo(1000),
    presence: "offline",
    agent_version: null,
    report_interval_seconds: null,
  }),
];

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
  server_version: "dev",
  settings: KEYS.map(([key, effective]) => ({ key, effective, source: "default", fallback: effective, applies: "immediately", env_only: false })),
};

const server = { aboutStatus: 200, about: ABOUT_ALL_STATUSES, clients: CLIENTS, calls: new Map() };
const count = (p) => server.calls.get(p) || 0;
function respond(status, data) {
  const text = JSON.stringify(data);
  return { ok: status >= 200 && status < 300, status, json: async () => data, text: async () => text };
}
globalThis.fetch = async (url) => {
  const u = new URL(String(url));
  const p = u.pathname;
  server.calls.set(p, count(p) + 1);
  if (p === "/v1/about") return server.aboutStatus === 200 ? respond(200, server.about) : respond(server.aboutStatus, { detail: "nope" });
  if (p === "/v1/clients") return respond(200, server.clients);
  if (p === "/v1/settings") return respond(200, SETTINGS);
  if (p === "/v1/steam/key") return respond(200, { configured: false, key_last4: null });
  if (p === "/v1/schedule") return respond(404, { detail: "Not Found" });
  if (p === "/v1/jobs" || p === "/v1/games") return respond(200, []);
  if (p === "/v1/cache/summary") return respond(200, {});
  return respond(404, { detail: "Not Found" });
};

const { store } = await import("../js/store-singleton.js");
const { renderSettings, requestAboutFocus } = await import("../js/views/settings.js");
const { createRailPanel } = await import("../js/components/rail-panel.js");
const { resetModalStack } = await import("../js/lib/modal-stack.js");

after(() => store.stop());

const tick = (ms = 10) => new Promise((r) => setTimeout(r, ms));
async function until(pred, label, timeoutMs = 2000) {
  const start = Date.now();
  while (!pred()) {
    if (Date.now() - start > timeoutMs) throw new Error(`timed out waiting for: ${label}`);
    await tick(5);
  }
}

const textOf = (root, sel) => {
  const n = root.querySelector(sel);
  return n ? n.textContent : null;
};
const aboutRow = (s, name) => s.querySelector(`tr[data-component="${name}"]`);
const aboutRowText = (s, name) => {
  const row = aboutRow(s, name);
  if (!row) return null;
  // The status cell's icon carries a (hidden-from-AT) sr-only word of the
  // shared vocabulary; the visible word is the badge's last child.
  return row.children.map((c) => {
    const badge = c.querySelector('[data-role="about-status"]');
    return badge ? badge.children[badge.children.length - 1].textContent : visibleText(c);
  });
};
/** Text a sighted user sees: skips `hidden` subtrees and screen-reader-only
 * spans (fake-dom keeps `hidden` as a plain property). WP WEB-FIX-8: a dash
 * cell also carries an sr-only "Not reported". */
function visibleText(node) {
  if (node.hidden) return "";
  if (node.classList && node.classList.contains("sr-only")) return "";
  if (node.tagName === "#TEXT") return node.textContent;
  return node.childNodes.map(visibleText).join("");
}
const detailText = (s, name) => textOf(s, `tr[data-detail-for="${name}"]`);
const pcsSheet = () => dom.document.body.querySelectorAll("div").find((d) => d.getAttribute("aria-label") === "PCs (agents)") || null;
const sheetIsOpen = () => {
  const sheet = pcsSheet();
  return !!(sheet && sheet.parentNode && sheet.parentNode.classList.contains("on"));
};
const sheetRows = () => (pcsSheet() ? pcsSheet().querySelectorAll(".jobcard") : []);
const sheetRow = (id) => (pcsSheet() ? pcsSheet().querySelector(`.jobcard[data-client-id="${id}"]`) : null);

async function mountSettings() {
  const s = renderSettings();
  dom.appRoot.replaceChildren(s);
  await until(() => s.querySelector('[data-role="about-refresh"]') !== null, "settings loaded");
  return s;
}

beforeEach(() => {
  server.aboutStatus = 200;
  server.about = ABOUT_ALL_STATUSES;
  server.clients = CLIENTS;
  const sheet = pcsSheet();
  if (sheet && sheetIsOpen()) sheet.querySelectorAll("button").find((b) => b.textContent === "Close").dispatchEvent({ type: "click" });
  resetModalStack(dom.document);
});

test("About: one row per component, WEB-FIX-8 words (core OK against vault-api, dns N/A), short commit, a dash for a value not reported", async () => {
  const s = await mountSettings();
  await until(() => aboutRow(s, "vault-dns") !== null, "about table");
  assert.deepEqual(aboutRowText(s, "vault-api"), ["vault-api", "0.1.0", "3f9c2a7", "OK"]);
  assert.deepEqual(aboutRowText(s, "vault-core"), ["vault-core", "0.1.0", "3f9c2a7", "OK"], "same version and commit as vault-api");
  assert.deepEqual(aboutRowText(s, "vault-runner"), ["vault-runner", "0.1.0", "3f9c2a7", "Unreachable"]);
  assert.deepEqual(aboutRowText(s, "steamprefill"), ["steamprefill", "0.1.0", "—", "Check"], "server unknown = looked, unclear answer");
  assert.deepEqual(aboutRowText(s, "vault-proxy"), ["vault-proxy", "—", "—", "Not in use"]);
  assert.deepEqual(aboutRowText(s, "vault-dns"), ["vault-dns", "—", "—", "N/A"]);
  assert.equal(s.querySelectorAll("tr.about-row").length, 6);
  assert.equal(aboutRow(s, "vault-api").querySelector("span.mono").getAttribute("title") === null, true, "version equal to text needs no title");
});

test("MUTATION TARGET: vault-core differing from vault-api reads a neutral 'Check'; one never recorded reads 'Not reported'", async () => {
  server.about = {
    components: ABOUT_ALL_STATUSES.components.map((c) => (c.name === "vault-core" ? { ...c, version: "0.0.9" } : c)),
  };
  let s = await mountSettings();
  await until(() => aboutRow(s, "vault-dns") !== null, "about table");
  assert.deepEqual(aboutRowText(s, "vault-core"), ["vault-core", "0.0.9", "3f9c2a7", "Check"]);
  assert.equal(aboutRow(s, "vault-core").querySelector('[data-role="about-status"]').className, "badge tx-cancelled", "neutral, not red");
  assert.match(detailText(s, "vault-core"), /differs from vault-api's/);
  server.about = {
    components: ABOUT_ALL_STATUSES.components.map((c) => (c.name === "vault-core" ? { ...c, version: null, commit: null } : c)),
  };
  s = await mountSettings();
  await until(() => aboutRow(s, "vault-dns") !== null, "about table");
  assert.deepEqual(aboutRowText(s, "vault-core"), ["vault-core", "—", "—", "Not reported"]);
});

test("About: status cells carry the matching icon kind, aria-hidden, next to the word", async () => {
  const s = await mountSettings();
  await until(() => aboutRow(s, "vault-dns") !== null, "about table");
  const kindOf = (name) => {
    const icon = aboutRow(s, name).querySelector('[data-role="about-status"] span.sic');
    return icon ? `${icon.className}|${icon.getAttribute("aria-hidden")}` : null;
  };
  assert.equal(kindOf("vault-api"), "sic k-cached sic-sm|true");
  assert.equal(kindOf("vault-core"), "sic k-cached sic-sm|true");
  assert.equal(kindOf("vault-runner"), "sic k-error sic-sm|true");
  assert.equal(kindOf("steamprefill"), "sic k-unknown sic-sm|true");
  assert.equal(kindOf("vault-proxy"), "sic k-notinuse sic-sm|true");
  assert.equal(kindOf("vault-dns"), "sic k-notinuse sic-sm|true");
});

test("About: the (i) details hold the note, the dash reason and the server detail as plain text; relative checked time with the 60 s cache", async () => {
  const s = await mountSettings();
  await until(() => aboutRow(s, "vault-dns") !== null, "about table");
  assert.match(detailText(s, "vault-core"), /^Recorded at vault-core's last start: the same version and commit as vault-api/);
  assert.match(detailText(s, "vault-core"), /Recorded at vault-core's last start, 2026-10-02T10:00:00Z\. <i>text<\/i>/);
  assert.match(detailText(s, "vault-dns"), /^Optional component\. vault-api does not check it/);
  assert.match(detailText(s, "vault-dns"), /A dash means the component did not report this value\./);
  assert.match(textOf(s, '[data-role="about-checked"]'), /^Checked by the server 5 min ago\. .*up to 60 s/);
  const full = aboutRow(s, "vault-api").querySelectorAll("span.mono")[1].getAttribute("title");
  assert.equal(full, FULL_COMMIT, "the short commit carries the full id as its title");
});

test("MUTATION TARGET: default render — only Check rows open their (i), and no visible 'unknown' anywhere in the About section", async () => {
  const allUnknown = {
    components: ABOUT_ALL_STATUSES.components.map((c) => ({ ...c, version: null, commit: null, status: "unknown" })),
  };
  for (const about of [ABOUT_ALL_STATUSES, allUnknown]) {
    server.about = about;
    const s = await mountSettings();
    await until(() => aboutRow(s, "vault-dns") !== null, "about table");
    const content = s.querySelector(".about-content");
    const buttons = content.querySelectorAll('[data-role="about-info"]');
    assert.equal(buttons.length, 6, "one (i) per row");
    for (const row of content.querySelectorAll("tr.about-row")) {
      const name = row.getAttribute("data-component");
      const word = aboutRowText(s, name)[3];
      const btn = row.querySelector('[data-role="about-info"]');
      const details = content.querySelectorAll("tr.about-detail").find((d) => d.getAttribute("data-detail-for") === name);
      const open = word === "Check";
      assert.equal(btn.getAttribute("aria-expanded"), String(open), `${name} (${word}) aria-expanded`);
      assert.equal(details.hidden, !open, `${name} (${word}) details ${open ? "open" : "collapsed"} by default`);
    }
    const seen = visibleText(content);
    assert.doesNotMatch(seen, /unknown/i, seen);
    assert.match(seen, /—/);
  }
});

test("MUTATION TARGET: a server 'unknown' on vault-proxy is a Check whose server reason is on screen without a click", async () => {
  server.about = {
    components: ABOUT_ALL_STATUSES.components.map((c) =>
      c.name === "vault-proxy"
        ? { ...c, status: "unknown", detail: "The proxy answered HTTP 200 for a host that is on no allowlist, instead of refusing it with 403. Check the egress filter." }
        : c,
    ),
  };
  const s = await mountSettings();
  await until(() => aboutRow(s, "vault-dns") !== null, "about table");
  assert.equal(aboutRowText(s, "vault-proxy")[3], "Check");
  const details = s.querySelectorAll("tr.about-detail").find((d) => d.getAttribute("data-detail-for") === "vault-proxy");
  assert.equal(details.hidden, false);
  assert.match(visibleText(details), /Check the egress filter\./);
});

test("the row header is named by the component name alone (aria-labelledby the name span, not the (i) label)", async () => {
  const s = await mountSettings();
  await until(() => aboutRow(s, "vault-dns") !== null, "about table");
  const th = aboutRow(s, "vault-core").querySelector("th.about-name");
  const id = th.getAttribute("aria-labelledby");
  const span = th.querySelector("span.about-name-text");
  assert.equal(span.id, id);
  assert.equal(span.textContent, "vault-core");
});

test("MUTATION TARGET: the (i) button is an accessible disclosure — named, aria-controls its details row, click toggles aria-expanded and hidden", async () => {
  const s = await mountSettings();
  await until(() => aboutRow(s, "vault-dns") !== null, "about table");
  const btn = aboutRow(s, "vault-core").querySelector('[data-role="about-info"]');
  try {
    assert.equal(btn.tagName, "BUTTON", "a native button: Enter/Space and focus for free");
    assert.equal(btn.type, "button");
    assert.equal(btn.getAttribute("aria-label"), "Details for vault-core");
    const controls = btn.getAttribute("aria-controls");
    const target = s.querySelectorAll("tr").find((t) => t.id === controls);
    assert.equal(target ? target.getAttribute("data-detail-for") : null, "vault-core", "aria-controls points at this row's details");
    assert.equal(btn.querySelector("svg").getAttribute("aria-hidden"), "true", "the glyph is decorative");
    btn.dispatchEvent({ type: "click" });
    assert.equal(btn.getAttribute("aria-expanded"), "true");
    assert.equal(target.hidden, false);
    assert.equal(aboutRow(s, "vault-dns").querySelector('[data-role="about-info"]').getAttribute("aria-expanded"), "false", "only this row opens");
    // A Refresh rebuilds the table and keeps the open row open.
    s.querySelector('[data-role="about-refresh"]').dispatchEvent({ type: "click" });
    await until(
      () => s.querySelector('[data-role="about-refresh-status"]').textContent === "Component versions refreshed.",
      "refreshed",
    );
    const again = aboutRow(s, "vault-core").querySelector('[data-role="about-info"]');
    assert.equal(again.getAttribute("aria-expanded"), "true", "open state survives Refresh");
    again.dispatchEvent({ type: "click" });
    assert.equal(again.getAttribute("aria-expanded"), "false");
    assert.equal(s.querySelectorAll("tr").find((t) => t.id === controls).hidden, true);
  } finally {
    const cur = aboutRow(s, "vault-core").querySelector('[data-role="about-info"]');
    if (cur.getAttribute("aria-expanded") === "true") cur.dispatchEvent({ type: "click" });
  }
});

test("About: a dash cell names itself for screen readers ('Not reported'), the dash itself is aria-hidden", async () => {
  const s = await mountSettings();
  await until(() => aboutRow(s, "vault-dns") !== null, "about table");
  const cell = aboutRow(s, "vault-dns").querySelector("td.about-version");
  assert.equal(cell.querySelector("span.mono").getAttribute("aria-hidden"), "true");
  assert.equal(textOf(cell, "span.sr-only"), "Not reported");
  const real = aboutRow(s, "vault-api").querySelector("td.about-version");
  assert.equal(real.querySelector("span.sr-only"), null, "a real value needs no extra label");
});

test("MUTATION TARGET: a 404 shows the 'server too old' note — not an error, no table", async () => {
  server.aboutStatus = 404;
  const s = await mountSettings();
  await until(() => s.querySelector('[data-role="about-too-old"]') !== null, "too-old note");
  assert.match(textOf(s, '[data-role="about-too-old"]'), /older than this web UI/);
  assert.equal(s.querySelector('[data-role="about-error"]') === null, true, "no error line");
  assert.equal(s.querySelectorAll("tr.about-row").length, 0, "no table");
});

test("MUTATION TARGET: a 401 is an error line, never 'server too old' (with a valid key an old server says 404); so is a 500", async () => {
  server.aboutStatus = 401;
  let s = await mountSettings();
  await until(() => s.querySelector('[data-role="about-error"]') !== null, "error line on 401");
  assert.match(textOf(s, '[data-role="about-error"]'), /^Could not load component versions: /);
  assert.equal(s.querySelector('[data-role="about-too-old"]') === null, true, "a refused key is not an old server");
  server.aboutStatus = 500;
  s = await mountSettings();
  await until(() => s.querySelector('[data-role="about-error"]') !== null, "error line on 500");
  assert.match(textOf(s, '[data-role="about-error"]'), /^Could not load component versions: /);
  assert.equal(s.querySelector('[data-role="about-too-old"]') === null, true);
});

test("MUTATION TARGET: no polling — one /v1/about per mount; Refresh fetches again and announces the outcome", async () => {
  const s = await mountSettings();
  await until(() => aboutRow(s, "vault-dns") !== null, "about table");
  const before = count("/v1/about");
  for (let i = 0; i < 3; i++) {
    const c = count("/v1/clients");
    store.refreshNow();
    await until(() => count("/v1/clients") > c, "a clients poll");
  }
  await tick(30);
  assert.equal(count("/v1/about"), before, "store ticks never fetch /v1/about");
  const status = s.querySelector('[data-role="about-refresh-status"]');
  assert.equal(status.getAttribute("role"), "status");
  assert.equal(status.textContent, "", "nothing announced before a Refresh");
  s.querySelector('[data-role="about-refresh"]').dispatchEvent({ type: "click" });
  await until(() => status.textContent === "Component versions refreshed.", "refresh announcement");
  assert.equal(count("/v1/about"), before + 1);
});

test("Refresh while a request is in flight is ignored (aria-disabled + click guard)", async () => {
  const s = await mountSettings();
  await until(() => aboutRow(s, "vault-dns") !== null, "about table");
  let release;
  const gate = new Promise((r) => (release = r));
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    if (new URL(String(url)).pathname === "/v1/about") await gate;
    return realFetch(url, init);
  };
  try {
    const btn = s.querySelector('[data-role="about-refresh"]');
    const before = count("/v1/about");
    btn.dispatchEvent({ type: "click" });
    await tick(5);
    assert.equal(btn.getAttribute("aria-disabled"), "true");
    btn.dispatchEvent({ type: "click" });
    btn.dispatchEvent({ type: "click" });
    release();
    await until(() => btn.getAttribute("aria-disabled") === null, "refresh settled");
    assert.equal(count("/v1/about"), before + 1, "three clicks, one request");
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("MUTATION TARGET: the PCs list is reachable from Settings with no bypass anywhere", async () => {
  await until(() => Array.isArray(store.snapshot("clients")), "first clients poll");
  assert.equal(store.snapshot("clients").some((c) => c.bypass_suspected), false, "precondition: nobody is bypassing (no banner)");
  const s = await mountSettings();
  assert.equal(sheetIsOpen(), false);
  const btn = s.querySelector('[data-role="open-pcs"]');
  assert.equal(btn === null, false, "a 'Show PCs' button in Settings");
  assert.equal(btn.tagName, "BUTTON");
  btn.dispatchEvent({ type: "click" });
  assert.equal(sheetIsOpen(), true, "the clients sheet opens");
  assert.equal(sheetRows().length, 2, "both PCs listed, the offline one too");
});

test("MUTATION TARGET: each PC's presence chip is the server's field, not a recompute from last_reported_at", async () => {
  await until(() => Array.isArray(store.snapshot("clients")), "first clients poll");
  const s = await mountSettings();
  s.querySelector('[data-role="open-pcs"]').dispatchEvent({ type: "click" });
  await until(() => sheetRow("fresh-but-offline") !== null, "rows");
  assert.equal(textOf(sheetRow("old-but-online"), '[data-role="presence-word"]'), "Online");
  assert.equal(textOf(sheetRow("fresh-but-offline"), '[data-role="presence-word"]'), "Offline");
  assert.equal(sheetRow("old-but-online").querySelector("[data-presence-chip]").className, "pchip pchip-on");
  assert.equal(textOf(pcsSheet(), '[data-role="agents-summary"]'), "Agents: 1 online, 1 offline");
  assert.equal(textOf(s, '[data-role="agents-summary"]'), "Agents: 1 online, 1 offline", "the Settings line");
  assert.equal(s.querySelectorAll('[data-role="agents-summary"]').length, 1, "one agents line in Settings (PCs), none under About");
});

test("MUTATION TARGET: the sheet's heading reads 'PCs (agents)'", async () => {
  await until(() => Array.isArray(store.snapshot("clients")), "first clients poll");
  const s = await mountSettings();
  s.querySelector('[data-role="open-pcs"]').dispatchEvent({ type: "click" });
  assert.equal(textOf(pcsSheet(), "h2"), "PCs (agents)");
});

test("MUTATION TARGET: 'version not reported' and 'last seen … ago' on the row; the stats line keeps the games count", async () => {
  await until(() => Array.isArray(store.snapshot("clients")), "first clients poll");
  const s = await mountSettings();
  s.querySelector('[data-role="open-pcs"]').dispatchEvent({ type: "click" });
  await until(() => sheetRow("fresh-but-offline") !== null, "rows");
  assert.equal(textOf(sheetRow("fresh-but-offline"), "[data-presence-line]"), "last seen just now · version not reported");
  assert.equal(textOf(sheetRow("old-but-online"), "[data-presence-line]"), "last seen 3 days ago · agent 0.1.0");
  assert.match(textOf(sheetRow("old-but-online"), "[data-stats-line]"), /3 games reported/);
  assert.match(textOf(sheetRow("old-but-online"), ".badge"), /Healthy/, "the bypass state stays");
});

test("an open sheet follows a presence flip on the next tick (patch, no rebuild needed)", async () => {
  await until(() => Array.isArray(store.snapshot("clients")), "first clients poll");
  const s = await mountSettings();
  s.querySelector('[data-role="open-pcs"]').dispatchEvent({ type: "click" });
  await until(() => sheetRow("old-but-online") !== null, "rows");
  server.clients = [{ ...CLIENTS[0], presence: "offline" }, CLIENTS[1]];
  try {
    store.refreshNow();
    await until(() => textOf(sheetRow("old-but-online"), '[data-role="presence-word"]') === "Offline", "flip painted");
    assert.equal(textOf(pcsSheet(), '[data-role="agents-summary"]'), "Agents: 0 online, 2 offline");
    await until(() => textOf(s, '[data-role="agents-summary"]') === "Agents: 0 online, 2 offline", "settings line follows");
  } finally {
    server.clients = CLIENTS;
    store.refreshNow();
    await until(() => store.snapshot("clients")[0].presence === "online", "restored");
  }
});

test("rail: the version button calls its handler and is named 'About: dev build'", async () => {
  const { FakeElement } = dom;
  const versionEl = new FakeElement("button");
  let activated = 0;
  createRailPanel({
    elements: {
      headEl: new FakeElement("div"),
      vaultNameEl: new FakeElement("p"),
      footEl: new FakeElement("div"),
      cacheEl: new FakeElement("div"),
      versionEl,
      createElement: (tag) => new FakeElement(tag),
    },
    store: { subscribe() {}, snapshot() {} },
    apiClient: { getSettings: async () => SETTINGS },
    getStoredApiKey: () => "k",
    isDemoMode: () => false,
    onVersionActivate: () => activated++,
  });
  await until(() => versionEl.textContent === "dev build", "version painted");
  assert.equal(versionEl.getAttribute("aria-label"), "About: dev build");
  versionEl.dispatchEvent({ type: "click" });
  assert.equal(activated, 1);
});

test("requestAboutFocus(): the next Settings render focuses the About heading", async () => {
  requestAboutFocus();
  const s = await mountSettings();
  await until(() => dom.document.activeElement && dom.document.activeElement.id === "settings-about", "About heading focused");
  assert.equal(dom.document.activeElement.textContent, "About");
  assert.equal(s.querySelector("h4.sec").textContent === "About", false, "About is not the first section; focus had to move");
});

test("leaving Settings drops a pending About focus request (no focus jump on a later visit)", async () => {
  const { navigateTo } = await import("../js/router.js");
  requestAboutFocus();
  navigateTo("library"); // leaves Settings before it ever rendered
  dom.document.activeElement = dom.document.body;
  const s = await mountSettings();
  await until(() => aboutRow(s, "vault-dns") !== null, "about table");
  await tick(20);
  assert.equal(dom.document.activeElement.id === "settings-about", false, "a stale request moved focus");
});
