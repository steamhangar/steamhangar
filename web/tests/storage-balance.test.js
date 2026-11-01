/**
 * WP STORAGE-BALANCE-1: `web/js/lib/storage-balance.js` (pure) and its
 * wiring into the Settings Expert panel (`views/settings.js`).
 *
 *   - an older server (no `storage_balance`), no summary, or a malformed
 *     object: `null`, and the card stays hidden (never zeros);
 *   - the tiles carry plain words, the figures and depot counts; the
 *     planned size is in the current-builds note; an "Unexplained" tile
 *     (signed) only while the residual is not 0;
 *   - the server's warning shows as a `.warn` box with its reason; the
 *     old-builds tile is highlighted then;
 *   - demo data carries a consistent balance (sums to on-disk, residual 0).
 *
 * Harness for the wiring part: fake-dom.js, a stored key, a routing fetch
 * fake; the REAL store-singleton polls it (stopped in `after()`).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, after } from "node:test";
import assert from "node:assert/strict";
import { createFakeDom } from "./fake-dom.js";
import {
  STORAGE_BALANCE_WARNING_FALLBACK,
  storageBalanceView,
} from "../js/lib/storage-balance.js";

const GB = 2 ** 30;

function balance(extra = {}) {
  return {
    on_disk_bytes: 100 * GB,
    planned_bytes: 70 * GB,
    present_bytes: 60 * GB,
    measured_depots: 3,
    old_builds_bytes: 20 * GB,
    unmeasured_bytes: 15 * GB,
    unmeasured_depots: 2,
    unmapped_bytes: 5 * GB,
    unmapped_depots: 1,
    unexplained_bytes: 0,
    warning: true,
    warning_reason: "Old builds take 20.0 GB (20 % of 100.0 GB on disk), above the 15 % / 50.0 GB threshold.",
    ...extra,
  };
}

// ------------------------------------------------------------------
// Pure presentation
// ------------------------------------------------------------------

test("MUTATION TARGET: an older server without storage_balance (or no summary) is null, never zeros", () => {
  assert.equal(storageBalanceView(undefined), null);
  assert.equal(storageBalanceView(null), null);
  assert.equal(
    storageBalanceView({ total_bytes: 1, top_consumers: [], unmapped_depots: { count: 0, size_bytes: 0 }, free_disk_bytes: 1 }),
    null,
    "the pre-STORAGE-BALANCE-1 summary shape",
  );
  assert.equal(storageBalanceView({ storage_balance: null }), null);
  assert.equal(storageBalanceView({ storage_balance: balance({ present_bytes: "60" }) }), null, "a non-number field");
  assert.equal(storageBalanceView({ storage_balance: balance({ old_builds_bytes: -1 }) }), null, "a negative size");
  const { unmapped_depots: _gone, ...partial } = balance();
  assert.equal(storageBalanceView({ storage_balance: partial }), null, "a missing field");
});

test("tiles: plain words, figures, depot counts, the 'of planned' sub-line", () => {
  const view = storageBalanceView({ storage_balance: balance() });
  const byKey = Object.fromEntries(view.tiles.map((t) => [t.key, t]));
  assert.deepEqual(view.tiles.map((t) => t.label), [
    "On disk",
    "Current builds",
    "Older builds",
    "Not measured",
    "No game",
  ], "no Unexplained tile while the residual is 0");
  assert.equal(view.accounted, true);
  assert.equal(byKey.on_disk.value, "100 GB");
  assert.equal(byKey.current.value, "60.0 GB");
  assert.equal(byKey.current.note, "On disk, of the 70.0 GB the current builds of the measured games need (3 depots).");
  assert.equal(byKey.old_builds.value, "20.0 GB");
  assert.equal(byKey.unmeasured.sub, "2 depots");
  assert.equal(byKey.unmapped.sub, "1 depot");
});

test("MUTATION TARGET: the server's warning shows with its reason and highlights old builds", () => {
  const view = storageBalanceView({ storage_balance: balance() });
  assert.match(view.warning, /^Old builds take 20.0 GB/);
  assert.equal(view.tiles.find((t) => t.key === "old_builds").hi, true);

  const quiet = storageBalanceView({ storage_balance: balance({ warning: false, warning_reason: null }) });
  assert.equal(quiet.warning, null);
  assert.equal(quiet.tiles.some((t) => t.hi), false);

  const bare = storageBalanceView({ storage_balance: balance({ warning_reason: null }) });
  assert.equal(bare.warning, STORAGE_BALANCE_WARNING_FALLBACK);
});

test("a negative residual prints signed and explains itself", () => {
  const view = storageBalanceView({ storage_balance: balance({ unexplained_bytes: -2 * GB }) });
  const t = view.tiles.find((x) => x.key === "unexplained");
  assert.equal(view.accounted, false);
  assert.equal(t.value, "−2.0 GB");
  assert.match(t.note, /^Should be 0/);
  assert.equal(t.hi, true, "part of the warning");
});

test("demo data: the balance adds up to on-disk with a zero residual", async () => {
  const { demoRequest } = await import("../js/demo-data.js");
  const summary = await demoRequest("GET", "/v1/cache/summary");
  const b = summary.storage_balance;
  assert.equal(b.on_disk_bytes, summary.total_bytes);
  assert.equal(b.unexplained_bytes, 0);
  assert.equal(
    b.present_bytes + b.old_builds_bytes + b.unmeasured_bytes + b.unmapped_bytes + b.unexplained_bytes,
    b.on_disk_bytes,
  );
  assert.ok(b.present_bytes <= b.planned_bytes);
  assert.notEqual(storageBalanceView(summary), null);
});

// ------------------------------------------------------------------
// Wiring: the Expert panel's card
// ------------------------------------------------------------------

const dom = createFakeDom();
globalThis.document = dom.document;
const storage = new Map([
  ["steamvault.apiKey", "test-key"],
  ["steamvault.settingsExpert", "1"],
]);
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

const SETTINGS = {
  readonly: false,
  server_version: "dev",
  settings: [
    ["vault_name", "vault-01", false],
    ["schedule_window", null, false],
    ["schedule_interval_minutes", 180, true],
    ["schedule_client_stale_days", 14, true],
    ["sweep_include_cached", true, true],
    ["auto_gc", "off", false],
    ["webhook_url", "", false],
    ["webhook_events", [], false],
    ["log_level", "INFO", true],
  ].map(([key, effective, expert]) => ({
    key,
    effective,
    source: "default",
    fallback: effective,
    applies: "immediately",
    env_only: false,
    value: null,
    editable: true,
    risky: false,
    expert,
  })),
};

const OLD_SUMMARY = { total_bytes: 100 * GB, top_consumers: [], unmapped_depots: { count: 1, size_bytes: 5 * GB }, free_disk_bytes: 1 };
const server = { summary: OLD_SUMMARY };
function respond(status, data) {
  const text = JSON.stringify(data);
  return { ok: status >= 200 && status < 300, status, json: async () => data, text: async () => text };
}
globalThis.fetch = async (url) => {
  const p = new URL(String(url)).pathname;
  if (p === "/v1/settings") return respond(200, SETTINGS);
  if (p === "/v1/steam/key") return respond(200, { configured: false, key_last4: null });
  if (p === "/v1/cache/summary") return respond(200, server.summary);
  if (p === "/v1/jobs" || p === "/v1/games" || p === "/v1/clients") return respond(200, []);
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
const card = (s) => s.querySelector('div.fcard[data-group="storage-balance"]');

test("MUTATION TARGET: wiring: hidden for an older server, filled from the cache poll, warning box, back to hidden", async () => {
  await until(() => store.snapshot("cache") !== undefined, "first cache poll");
  const s = renderSettings();
  dom.appRoot.replaceChildren(s);
  await until(() => card(s) !== null, "expert panel built");
  assert.equal(card(s).hidden, true, "older server: no card");
  assert.equal(card(s).querySelectorAll("div.tile").length, 0);

  server.summary = { ...OLD_SUMMARY, storage_balance: balance() };
  store.refresh("cache");
  await until(() => card(s).hidden === false, "card shown after the next poll");
  const tiles = card(s).querySelectorAll("div.tile");
  assert.equal(tiles.length, 5);
  assert.match(card(s).querySelector("p.hint").textContent, /^Everything on disk is accounted for/);
  assert.equal(tiles[0].querySelector("span.k").textContent, "On disk");
  assert.equal(tiles[2].className, "tile hi", "old builds highlighted while warning");
  const warn = card(s).querySelector('[data-role="balance-warning"]');
  assert.ok(warn, "warning box");
  assert.match(warn.textContent, /Old builds take 20.0 GB/);

  server.summary = { ...OLD_SUMMARY, storage_balance: balance({ warning: false, warning_reason: null }) };
  store.refresh("cache");
  await until(() => card(s).querySelector('[data-role="balance-warning"]') === null, "warning cleared");
  assert.equal(card(s).hidden, false);

  server.summary = OLD_SUMMARY;
  store.refresh("cache");
  await until(() => card(s).hidden === true, "a downgrade hides the card again");
});
