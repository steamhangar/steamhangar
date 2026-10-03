/**
 * DOM-wiring regression pins for `web/js/components/rail-panel.js` (WP 4e.6
 * review round, Opus should-fix S3).
 *
 * Before this file existed, `rail-panel.js` had NO test coverage at all
 * (the "components/*.js are DOM-building glue, not unit-tested directly"
 * convention this codebase otherwise follows) — and two real bugs hid
 * behind that gap: deleting `if (payload.error) return;` from the cache
 * subscription passed the full 498-test suite while blanking the rail's
 * cache foot back to "unknown" on every transient poll failure, and
 * deleting `if (foot.freeText !== null)` also passed the full suite AND
 * rendered a literal `"Free null"` row — a fabricated value.
 *
 * `createRailPanel()` takes every dependency (DOM elements, store, API
 * client, the two onboarding-style gating predicates) as one options
 * object, same pattern `store.js`'s own `createPollingStore` already uses
 * for testability — so this file drives it directly with the shared
 * `fake-dom.js` `FakeElement` (no real `document`, extended here with
 * `replaceChildren`) and trivial fake store/api stand-ins (no real
 * `store-singleton.js`, no real network). Every scenario below is one this
 * project's own "verify empirically" rule would otherwise leave to a live
 * browser check alone.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createFakeDom } from "./fake-dom.js";
import { createRailPanel } from "../js/components/rail-panel.js";

const { FakeElement } = createFakeDom();

function makeElements() {
  return {
    headEl: new FakeElement("div"),
    vaultNameEl: new FakeElement("p"),
    footEl: new FakeElement("div"),
    cacheEl: new FakeElement("div"),
    versionEl: new FakeElement("p"),
    createElement: (tag) => new FakeElement(tag),
  };
}

/** A trivial fake store exposing only what createRailPanel touches:
 * subscribe("cache", cb) captures the callback, snapshot("cache") returns
 * whatever this fake was built with, and emit(payload) drives a tick —
 * same "plain object exposing only the members actually used" posture
 * store-poll-loop.test.js's fake document already established. */
function makeFakeStore(initialSnapshot) {
  let cb = null;
  return {
    subscribe(kind, callback) {
      assert.equal(kind, "cache", "rail-panel.js must only subscribe to the 'cache' resource");
      cb = callback;
      return () => {
        cb = null;
      };
    },
    snapshot(kind) {
      assert.equal(kind, "cache");
      return initialSnapshot;
    },
    emit(payload) {
      assert.ok(cb, "emit() called before createRailPanel subscribed");
      cb(payload);
    },
  };
}

function makeFakeApiClient(settingsResultOrError) {
  let callCount = 0;
  return {
    getSettings() {
      callCount++;
      return settingsResultOrError instanceof Error
        ? Promise.reject(settingsResultOrError)
        : Promise.resolve(settingsResultOrError);
    },
    get callCount() {
      return callCount;
    },
  };
}

function build({ cacheSnapshot = null, settingsResult = null, getStoredApiKey = () => "a-key", isDemoMode = () => false } = {}) {
  const elements = makeElements();
  const store = makeFakeStore(cacheSnapshot);
  const apiClient = makeFakeApiClient(settingsResult);
  const panel = createRailPanel({ elements, store, apiClient, getStoredApiKey, isDemoMode });
  return { elements, store, apiClient, panel };
}

/** `FakeElement` does not implement real DOM `textContent` bubbling (a
 * parent's `.textContent` is never computed from its descendants' text
 * nodes the way a real browser does) — this recurses to emulate it well
 * enough for this file's plain, text-leaf-only element trees (`cacheRow`'s
 * `<span>`s are the only nodes that ever have `.textContent` SET directly). */
function textOf(el) {
  if (el.children.length === 0) return el.textContent || "";
  return el.children.map(textOf).join("");
}

// ---------------------------------------------------------------------
// S3 mutation target 1: the freeText guard.
// ---------------------------------------------------------------------

test("renderCacheFoot: free_disk_bytes:null renders ONLY the Used row — no fabricated 'Free null'", () => {
  const { elements, panel } = build();
  panel.renderCacheFoot({ total_bytes: 100, free_disk_bytes: null });

  assert.equal(elements.cacheEl.children.length, 1, "exactly one row (Used) — free_disk_bytes:null must not produce a second row");
  const combined = textOf(elements.cacheEl);
  assert.ok(combined.includes("Used"));
  assert.equal(combined.includes("null"), false, "MUTATION TARGET: deleting the freeText guard renders the literal string 'null' in a fabricated Free row");
  assert.equal(combined.includes("Free"), false);
});

test("renderCacheFoot: a real free_disk_bytes value renders both rows", () => {
  const { elements, panel } = build();
  panel.renderCacheFoot({ total_bytes: 5_000_000_000, free_disk_bytes: 150_000_000_000 });

  assert.equal(elements.cacheEl.children.length, 2);
  const combined = textOf(elements.cacheEl);
  assert.ok(combined.includes("Used") && combined.includes("4.7 GB"));
  assert.ok(combined.includes("Free") && combined.includes("140 GB"));
});

// ---------------------------------------------------------------------
// S3 mutation target 2: the {error} guard on the "cache" subscription.
// ---------------------------------------------------------------------

test('a failed "cache" poll ({error}) leaves the last successful render untouched', () => {
  const { elements, store } = build({ cacheSnapshot: null });

  store.emit({ item: { total_bytes: 100, free_disk_bytes: 25 } });
  const beforeFailure = textOf(elements.cacheEl);
  assert.ok(beforeFailure.length > 0);

  store.emit({ error: new Error("boom") });
  assert.equal(
    textOf(elements.cacheEl),
    beforeFailure,
    "MUTATION TARGET: deleting the `if (payload.error) return;` guard calls renderCacheFoot(undefined) on a failure tick, blanking the rail back to 'unknown' instead of leaving the last real number on screen",
  );

  // And it recovers cleanly on the next successful tick.
  store.emit({ item: { total_bytes: 200, free_disk_bytes: 50 } });
  assert.ok(textOf(elements.cacheEl).includes("Used"));
});

test("the initial snapshot (store.snapshot('cache')) paints before any tick, exactly like bypass-banner.js's own pattern", () => {
  const { elements } = build({ cacheSnapshot: { total_bytes: 9, free_disk_bytes: 1 } });
  assert.ok(textOf(elements.cacheEl).includes("Used"));
});

test("no snapshot yet (undefined) renders nothing, not a placeholder", () => {
  const { elements } = build({ cacheSnapshot: undefined });
  assert.equal(elements.cacheEl.children.length, 0);
});

// ---------------------------------------------------------------------
// S4: containers hidden when they have nothing to show, not just their text.
// ---------------------------------------------------------------------

test("a fresh panel with no data at all hides BOTH .rail-head and .rail-foot", async () => {
  const { elements } = build({ cacheSnapshot: null, settingsResult: null });
  await Promise.resolve(); // let the one-time settings fetch's .then() run
  assert.equal(elements.headEl.hidden, true);
  assert.equal(elements.footEl.hidden, true);
});

test("an unset/empty vault_name hides .rail-head, never a placeholder box with a bare divider", async () => {
  const { elements } = build({ settingsResult: { readonly: false, settings: [{ key: "vault_name", effective: "" }] } });
  await Promise.resolve();
  assert.equal(elements.headEl.hidden, true);
  assert.equal(elements.vaultNameEl.textContent, "");
});

test("a real vault_name un-hides .rail-head", async () => {
  const { elements } = build({ settingsResult: { readonly: false, settings: [{ key: "vault_name", effective: "homelab" }] } });
  await Promise.resolve();
  assert.equal(elements.headEl.hidden, false);
  assert.equal(elements.vaultNameEl.textContent, "homelab");
});

test(".rail-foot stays VISIBLE with real cache data even though server_version is absent (today's normal case, pre-WP-4e.7)", async () => {
  const { elements, panel } = build({ settingsResult: { readonly: false, settings: [] } });
  panel.renderCacheFoot({ total_bytes: 10, free_disk_bytes: 5 });
  await Promise.resolve();

  assert.equal(elements.footEl.hidden, false, "real cache data alone must be enough to keep the foot visible");
  assert.equal(elements.versionEl.textContent, "", "server_version is genuinely absent — must stay blank");
  assert.equal(elements.versionEl.hidden, true, "the version line itself is hidden even though the foot as a whole is not");
});

test(".rail-foot stays VISIBLE with a real server_version even though the cache poll has not landed yet", async () => {
  const { elements } = build({ cacheSnapshot: undefined, settingsResult: { readonly: false, settings: [], server_version: "0.1.0" } });
  await Promise.resolve();

  assert.equal(elements.footEl.hidden, false, "a real version line alone must be enough to keep the foot visible");
  assert.equal(elements.versionEl.hidden, false);
  assert.equal(elements.versionEl.textContent, "v0.1.0");
});

test("WP VER-2: a local build's server_version 'dev' reaches the rail as 'dev build', never 'vdev'", async () => {
  const { elements } = build({ cacheSnapshot: undefined, settingsResult: { readonly: false, settings: [], server_version: "dev" } });
  await Promise.resolve();

  assert.equal(elements.versionEl.hidden, false);
  assert.equal(elements.versionEl.textContent, "dev build");
});

test(".rail-foot hides again if it starts with cache data but the settings fetch answers with nothing new (regression: hiding must not be one-directional-only in the wrong direction)", async () => {
  // Sanity check on updateFootVisibility()'s AND, not OR, semantics: this
  // is the one combination that must NOT hide the foot — restated as its
  // own test so the boolean operator itself is pinned (swapping && for ||
  // would make cache-only data ALSO hide the foot the instant the settings
  // promise resolves with nothing, which is wrong).
  const { elements, panel } = build({ settingsResult: { readonly: false, settings: [] } });
  panel.renderCacheFoot({ total_bytes: 10, free_disk_bytes: null });
  await Promise.resolve();
  assert.equal(elements.footEl.hidden, false);
});

// ---------------------------------------------------------------------
// The onboarding-style gating on the one-time settings fetch.
// ---------------------------------------------------------------------

test("the settings fetch is skipped entirely with no stored key and demo mode off (mirrors onboarding.js's own gate)", async () => {
  const { apiClient } = build({ getStoredApiKey: () => "", isDemoMode: () => false });
  await Promise.resolve();
  assert.equal(apiClient.callCount, 0, "a genuine first run must not fire a settings fetch that would just 401");
});

test("the settings fetch runs when a key is stored", async () => {
  const { apiClient } = build({ getStoredApiKey: () => "some-key", isDemoMode: () => false });
  await Promise.resolve();
  assert.equal(apiClient.callCount, 1);
});

test("the settings fetch runs in demo mode even with no stored key", async () => {
  const { apiClient } = build({ getStoredApiKey: () => "", isDemoMode: () => true });
  await Promise.resolve();
  assert.equal(apiClient.callCount, 1);
});

test("a rejected settings fetch leaves the head/foot in their honest 'not known' state rather than throwing", async () => {
  const { elements } = build({ settingsResult: new Error("network down") });
  await Promise.resolve();
  await Promise.resolve(); // one extra microtask turn for the .then(_, () => {}) rejection handler
  assert.equal(elements.headEl.hidden, true);
  assert.equal(elements.vaultNameEl.textContent, "");
});
