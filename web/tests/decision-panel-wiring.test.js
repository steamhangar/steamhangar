/**
 * DOM-wiring regression pins for `web/js/components/decision-panel.js`
 * (WP 4h.2) — same fake-dom.js posture as rail-panel-wiring.test.js (see that
 * file's header): no real `document`, no real `window.localStorage`, no real
 * router, so the visibility/dismiss/collapse GLUE is provable headlessly.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createFakeDom } from "./fake-dom.js";
import { createDecisionPanel } from "../js/components/decision-panel.js";

const { FakeElement } = createFakeDom();

function makeElements() {
  return {
    rootEl: new FakeElement("aside"),
    bodyEl: new FakeElement("div"),
    collapseBtn: new FakeElement("button"),
    dismissBtn: new FakeElement("button"),
    appEl: new FakeElement("div"),
    createElement: (tag) => new FakeElement(tag),
  };
}

/** Same "plain object exposing only the members actually used" posture as
 * rail-panel-wiring.test.js's makeFakeStore. */
function makeFakeStore(initialSnapshot) {
  let cb = null;
  return {
    subscribe(kind, callback) {
      assert.equal(kind, "games", "decision-panel.js must only subscribe to the 'games' resource");
      cb = callback;
      return () => {
        cb = null;
      };
    },
    snapshot(kind) {
      assert.equal(kind, "games");
      return initialSnapshot;
    },
    emit(payload) {
      assert.ok(cb, "emit() called before createDecisionPanel subscribed");
      cb(payload);
    },
  };
}

/** A trivial in-memory storage fake — never `window.localStorage`. */
function makeFakeStorage(initial = {}) {
  const map = new Map(Object.entries(initial));
  return {
    getItem(key) {
      return map.has(key) ? map.get(key) : null;
    },
    setItem(key, value) {
      map.set(key, String(value));
    },
    _map: map,
  };
}

/** Router fakes: `onViewChange` captures the handler, `emitView` drives it. */
function makeFakeRouter(initialView) {
  let handler = null;
  return {
    onViewChange(h) {
      handler = h;
      return () => {
        handler = null;
      };
    },
    getCurrentView: () => initialView,
    emitView(view) {
      assert.ok(handler, "emitView() called before createDecisionPanel subscribed");
      handler(view);
    },
  };
}

function textOf(el) {
  if (el.children.length === 0) return el.textContent || "";
  return el.children.map(textOf).join("");
}

function build({
  games = [],
  initialView = "library",
  storageSeed = {},
} = {}) {
  const elements = makeElements();
  const store = makeFakeStore(games);
  const router = makeFakeRouter(initialView);
  const storage = makeFakeStorage(storageSeed);
  const panel = createDecisionPanel({
    elements,
    store,
    onViewChange: router.onViewChange,
    getCurrentView: router.getCurrentView,
    storage,
  });
  return { elements, store, router, storage, panel };
}

function stableGame(appid, observationDays) {
  return {
    appid,
    name: `Game ${appid}`,
    status: "done",
    size_bytes: 1_000_000_000,
    last_manifest_check: null,
    manifest_change_frequency: "stable",
    manifest_observation_days: observationDays,
    manifest_days_since_last_change: null,
  };
}

// ---------------------------------------------------------------------
// Visibility: view gating.
// ---------------------------------------------------------------------

test("hidden on a non-library view even with real games and no dismissal", () => {
  const { elements } = build({ games: [stableGame(1, 40)], initialView: "downloads" });
  assert.equal(elements.rootEl.hidden, true);
});

test("visible on the library view with at least one game and no dismissal", () => {
  const { elements } = build({ games: [stableGame(1, 40)], initialView: "library" });
  assert.equal(elements.rootEl.hidden, false);
});

test("MUTATION PIN: #app's has-decision-panel class tracks visibility (a real result, on Library) — off on every other view or when dismissed", () => {
  const { elements, router } = build({ games: [stableGame(1, 40)], initialView: "library" });
  assert.equal(elements.appEl.classList.contains("has-decision-panel"), true);
  router.emitView("settings");
  assert.equal(elements.appEl.classList.contains("has-decision-panel"), false);
  router.emitView("library");
  assert.equal(elements.appEl.classList.contains("has-decision-panel"), true);
});

test("MUTATION PIN (S3): has-decision-panel is OFF when the panel is visible but EMPTY (insufficient_data) — the BP-XL column must not be reserved for one static sentence", () => {
  const nothingQualifies = [
    { appid: 1, name: "Fresh", status: "done", size_bytes: null, last_manifest_check: null, manifest_change_frequency: null },
  ];
  const { elements } = build({ games: nothingQualifies, initialView: "library" });
  // The panel itself is still visible (the honest empty-state message renders) —
  // only the COLUMN reservation is withheld, per should-fix S3.
  assert.equal(elements.rootEl.hidden, false);
  assert.equal(
    elements.appEl.classList.contains("has-decision-panel"),
    false,
    "has-decision-panel must be false for an insufficient_data result — reverting to plain visibility (this test's own previous form) would make this assertion fail",
  );
});

test("has-decision-panel turns back ON the moment a real suggestion appears in a later games tick, without re-navigating", () => {
  const nothingQualifies = [{ appid: 1, name: "Fresh", status: "done", size_bytes: null, last_manifest_check: null, manifest_change_frequency: null }];
  const { elements, store } = build({ games: nothingQualifies, initialView: "library" });
  assert.equal(elements.appEl.classList.contains("has-decision-panel"), false);
  store.emit({ items: [stableGame(1, 40)] });
  assert.equal(elements.appEl.classList.contains("has-decision-panel"), true);
});

test("navigating AWAY from library hides the panel; navigating back shows it again", () => {
  const { elements, router } = build({ games: [stableGame(1, 40)], initialView: "library" });
  assert.equal(elements.rootEl.hidden, false);
  router.emitView("settings");
  assert.equal(elements.rootEl.hidden, true);
  router.emitView("library");
  assert.equal(elements.rootEl.hidden, false);
});

test("MUTATION PIN: zero games at all hides the panel — nothing to react to yet", () => {
  const { elements } = build({ games: [], initialView: "library" });
  assert.equal(elements.rootEl.hidden, true);
});

// ---------------------------------------------------------------------
// The insufficient_data empty state — a designed state, not an empty box.
// ---------------------------------------------------------------------

test("games exist but none qualify: the panel STAYS VISIBLE with the honest insufficient-data message, not an empty box, not hidden", () => {
  const nothingQualifies = [
    { appid: 1, name: "Fresh", status: "done", size_bytes: null, last_manifest_check: null, manifest_change_frequency: null },
  ];
  const { elements } = build({ games: nothingQualifies, initialView: "library" });
  assert.equal(elements.rootEl.hidden, false);
  assert.match(textOf(elements.bodyEl), /not enough history/i);
});

test("real suggestions render each game's name and statement text", () => {
  const { elements } = build({ games: [stableGame(1, 40)], initialView: "library" });
  const text = textOf(elements.bodyEl);
  assert.match(text, /Game 1/);
  assert.match(text, /40 days/);
});

// ---------------------------------------------------------------------
// Dismiss: persisted, and — the precedent pin (bypass-banner.test.js) —
// stays dismissed across an unrelated "games" poll tick.
// ---------------------------------------------------------------------

test("clicking dismiss hides the panel and persists steamvault.decisionPanelDismissed=1", () => {
  const { elements, storage } = build({ games: [stableGame(1, 40)], initialView: "library" });
  elements.dismissBtn.dispatchEvent({ type: "click" });
  assert.equal(elements.rootEl.hidden, true);
  assert.equal(storage.getItem("steamvault.decisionPanelDismissed"), "1");
});

test("MUTATION PIN: dismissed stays dismissed across an unrelated games poll tick", () => {
  const { elements, store } = build({ games: [stableGame(1, 40)], initialView: "library" });
  elements.dismissBtn.dispatchEvent({ type: "click" });
  assert.equal(elements.rootEl.hidden, true);

  store.emit({ items: [stableGame(1, 41), stableGame(2, 99)] });
  assert.equal(
    elements.rootEl.hidden,
    true,
    "a games tick must not resurrect a dismissed panel — dismissal is read once into state and never re-derived from a tick",
  );
});

test("a previously-persisted dismissal (steamvault.decisionPanelDismissed=1) hides the panel immediately on construction, before any tick", () => {
  const { elements } = build({
    games: [stableGame(1, 40)],
    initialView: "library",
    storageSeed: { "steamvault.decisionPanelDismissed": "1" },
  });
  assert.equal(elements.rootEl.hidden, true);
});

// ---------------------------------------------------------------------
// Collapse: separate key from dismiss, defaults to collapsed, never aliases.
// ---------------------------------------------------------------------

test("collapsed defaults to true on a first visit (no stored flag)", () => {
  const { elements } = build({ games: [stableGame(1, 40)], initialView: "library" });
  assert.equal(elements.rootEl.classList.contains("collapsed"), true);
  assert.equal(elements.collapseBtn.getAttribute("aria-expanded"), "false");
});

test("clicking collapse toggles the collapsed class/aria-expanded and persists to its OWN key, distinct from dismiss", () => {
  const { elements, storage } = build({ games: [stableGame(1, 40)], initialView: "library" });
  elements.collapseBtn.dispatchEvent({ type: "click" });
  assert.equal(elements.rootEl.classList.contains("collapsed"), false);
  assert.equal(elements.collapseBtn.getAttribute("aria-expanded"), "true");
  assert.equal(storage.getItem("steamvault.decisionPanelCollapsed"), "0");
  assert.equal(storage.getItem("steamvault.decisionPanelDismissed"), null, "collapse must never write the dismiss key");
});

test("the collapse button's aria-label flips with state, not just aria-expanded (a screen-reader user needs the action, not just the state)", () => {
  const { elements } = build({ games: [stableGame(1, 40)], initialView: "library" });
  assert.equal(elements.collapseBtn.getAttribute("aria-label"), "Expand suggestions");
  elements.collapseBtn.dispatchEvent({ type: "click" });
  assert.equal(elements.collapseBtn.getAttribute("aria-label"), "Collapse suggestions");
  elements.collapseBtn.dispatchEvent({ type: "click" });
  assert.equal(elements.collapseBtn.getAttribute("aria-label"), "Expand suggestions");
});

test("MUTATION PIN: collapse and dismiss are independently keyed — a collapsed=false, dismissed=true combination is representable and honoured", () => {
  const { elements } = build({
    games: [stableGame(1, 40)],
    initialView: "library",
    storageSeed: { "steamvault.decisionPanelCollapsed": "0", "steamvault.decisionPanelDismissed": "1" },
  });
  // dismissed wins for VISIBILITY (the panel is hidden), but this proves the
  // two flags are read from two genuinely separate keys, not one aliased
  // boolean — if collapse and dismiss secretly shared a key, seeding
  // collapsed=0 would have also un-dismissed it.
  assert.equal(elements.rootEl.hidden, true);
});

test("an already-expanded (collapsed=0) stored preference is honoured on construction", () => {
  const { elements } = build({
    games: [stableGame(1, 40)],
    initialView: "library",
    storageSeed: { "steamvault.decisionPanelCollapsed": "0" },
  });
  assert.equal(elements.rootEl.classList.contains("collapsed"), false);
});

// ---------------------------------------------------------------------
// A failed ("games": {error}) poll tick leaves the last real render alone.
// ---------------------------------------------------------------------

test('a failed games poll ({error}) is ignored — leaves the last successful render untouched', () => {
  const { elements, store } = build({ games: [stableGame(1, 40)], initialView: "library" });
  const before = textOf(elements.bodyEl);
  store.emit({ error: new Error("boom") });
  assert.equal(textOf(elements.bodyEl), before);
  assert.equal(elements.rootEl.hidden, false);
});

// ---------------------------------------------------------------------
// Storage failures never throw out of the toggle handlers (same posture as
// api.js's localStorage helpers).
// ---------------------------------------------------------------------

test("a storage.setItem that throws does not prevent the dismiss action from taking effect in memory", () => {
  const elements = makeElements();
  const store = makeFakeStore([stableGame(1, 40)]);
  const router = makeFakeRouter("library");
  const throwingStorage = {
    getItem() {
      return null;
    },
    setItem() {
      throw new Error("storage disabled");
    },
  };
  createDecisionPanel({
    elements,
    store,
    onViewChange: router.onViewChange,
    getCurrentView: router.getCurrentView,
    storage: throwingStorage,
  });
  assert.doesNotThrow(() => elements.dismissBtn.dispatchEvent({ type: "click" }));
  assert.equal(elements.rootEl.hidden, true);
});

test("storage: null (app.js's guard when reading window.localStorage throws) — the panel still shows, collapses and dismisses in memory", () => {
  const elements = makeElements();
  const store = makeFakeStore([stableGame(1, 40)]);
  const router = makeFakeRouter("library");
  assert.doesNotThrow(() =>
    createDecisionPanel({
      elements,
      store,
      onViewChange: router.onViewChange,
      getCurrentView: router.getCurrentView,
      storage: null,
    }),
  );
  assert.equal(elements.rootEl.hidden, false, "visible with no storage at all");
  assert.equal(elements.collapseBtn.getAttribute("aria-expanded"), "false", "collapsed by default");
  assert.doesNotThrow(() => elements.collapseBtn.dispatchEvent({ type: "click" }));
  assert.equal(elements.collapseBtn.getAttribute("aria-expanded"), "true");
  assert.doesNotThrow(() => elements.dismissBtn.dispatchEvent({ type: "click" }));
  assert.equal(elements.rootEl.hidden, true);
});
