/**
 * Pins for the "Desktop site" hint (WP WEB-FIX-5):
 * `lib/desktop-site-hint.js` (the detection rule, the dismissal store),
 * `components/desktop-site-hint.js` (slot toggling, debounce, dismissal,
 * listeners), and the index.html / app.js wiring (comment-stripped source
 * scans, same idiom as connection-banner.test.js).
 *
 * Assertions compare TEXT/booleans/numbers, never nodes (a failed node
 * assertion dumps the fake-DOM graph and OOM-killed the runner, WEB-FIX-1).
 * Timer-driven cases run on a manual timer queue injected through
 * setTimer/clearTimer (flush() fires what is pending): deterministic, no
 * sleeps, so a negative assertion ("nothing changed") cannot pass merely
 * because the real timer had not fired yet.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { createFakeDom } from "./fake-dom.js";
import {
  isDesktopSiteOnPhone,
  createHintDismissal,
  DESKTOP_SITE_HINT_STORAGE_KEY,
} from "../js/lib/desktop-site-hint.js";
import { createDesktopSiteHint, readBrowserEnv } from "../js/components/desktop-site-hint.js";
import { syncBannerWrap } from "../js/lib/banner-wrap.js";

const here = dirname(fileURLToPath(import.meta.url));
const read = (rel) => readFileSync(join(here, "..", rel), "utf8");

// ---------------------------------------------------------------------
// Environments (CSS px). Phone sizes are real device DIP sizes; layout
// height in desktop mode keeps the screen's aspect (980 x 915/412).
// ---------------------------------------------------------------------
const env = (sw, sh, lw, lh, coarse) => ({
  screenWidth: sw,
  screenHeight: sh,
  layoutWidth: lw,
  layoutHeight: lh,
  coarsePointer: coarse,
});
const ENV = {
  pixel7DesktopSite: env(412, 915, 980, 2177, true),
  pixel7Normal: env(412, 915, 412, 780, true),
  pixel7LandscapeNormal: env(915, 412, 915, 360, true),
  pixel7LandscapeDesktopSite: env(915, 412, 980, 441, true),
  pixel8ProDesktopSite: env(448, 998, 980, 2183, true),
  smallPhoneDesktopSite: env(360, 780, 980, 2123, true),
  desktop: env(1920, 1080, 1904, 950, false),
  desktopNarrowWindow: env(1920, 1080, 500, 900, false),
  narrowScreenFinePointer: env(412, 915, 980, 2177, false),
  tablet600DesktopMode: env(600, 960, 980, 1568, true),
  ipadPortrait: env(820, 1180, 820, 1106, true),
  pixelTabletLandscape: env(1280, 800, 1280, 727, true),
  phoneHalfZoom: env(412, 915, 800, 1777, true),
};

const MATRIX = [
  ["pixel7DesktopSite", true, "phone + Desktop site (portrait) -> show"],
  ["pixel8ProDesktopSite", true, "larger phone + Desktop site -> show"],
  ["smallPhoneDesktopSite", true, "small phone + Desktop site -> show"],
  ["pixel7Normal", false, "phone, normal mode -> hide"],
  ["pixel7LandscapeNormal", false, "phone landscape, normal mode (915px layout) -> hide (rule 4)"],
  ["pixel7LandscapeDesktopSite", false, "phone landscape + Desktop site -> hide (documented gap)"],
  ["desktop", false, "real desktop -> hide"],
  ["desktopNarrowWindow", false, "desktop, narrow window -> hide"],
  ["narrowScreenFinePointer", false, "phone-narrow screen with a fine pointer -> hide (rule 1)"],
  ["tablet600DesktopMode", false, "600px tablet even with a 980 layout -> hide (rule 2)"],
  ["ipadPortrait", false, "tablet portrait -> hide"],
  ["pixelTabletLandscape", false, "tablet landscape -> hide"],
  ["phoneHalfZoom", false, "phone with an 800px layout -> hide (rule 3)"],
];

for (const [name, expected, label] of MATRIX) {
  test(`detection: ${label}`, () => {
    assert.equal(isDesktopSiteOnPhone(ENV[name]), expected, name);
  });
}

test("detection: missing or non-numeric inputs never show the hint", () => {
  assert.equal(isDesktopSiteOnPhone(null), false);
  assert.equal(isDesktopSiteOnPhone(undefined), false);
  assert.equal(isDesktopSiteOnPhone({ ...ENV.pixel7DesktopSite, screenWidth: 0 }), false);
  assert.equal(isDesktopSiteOnPhone({ ...ENV.pixel7DesktopSite, layoutWidth: NaN }), false);
  assert.equal(isDesktopSiteOnPhone({ ...ENV.pixel7DesktopSite, coarsePointer: "yes" }), false);
});

test("readBrowserEnv: layout from documentElement.clientWidth/Height, coarse from matchMedia", () => {
  const queries = [];
  const win = {
    screen: { width: 412, height: 915 },
    innerWidth: 999,
    innerHeight: 1999,
    document: { documentElement: { clientWidth: 980, clientHeight: 2177 } },
    matchMedia: (q) => {
      queries.push(q);
      return { matches: true };
    },
  };
  assert.deepEqual(readBrowserEnv(win), ENV.pixel7DesktopSite);
  assert.deepEqual(queries, ["(pointer: coarse)"]);
  delete win.matchMedia;
  assert.equal(readBrowserEnv(win).coarsePointer, false, "no matchMedia -> not coarse -> no hint");
});

// ---------------------------------------------------------------------
// Dismissal store
// ---------------------------------------------------------------------
function memStorage(initial = {}) {
  const data = { ...initial };
  return {
    data,
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => {
      data[k] = String(v);
    },
  };
}
const throwingStorage = {
  getItem() {
    throw new Error("SecurityError");
  },
  setItem() {
    throw new Error("QuotaExceededError");
  },
};

test("dismissal: persisted under its key and read back by a fresh instance", () => {
  const s = memStorage();
  const a = createHintDismissal(() => s);
  assert.equal(a.isDismissed(), false);
  a.dismiss();
  assert.equal(a.isDismissed(), true);
  assert.equal(s.data[DESKTOP_SITE_HINT_STORAGE_KEY], "1");
  assert.equal(DESKTOP_SITE_HINT_STORAGE_KEY, "steamvault.desktopSiteHintDismissed");
  assert.equal(createHintDismissal(() => s).isDismissed(), true);
});

test("dismissal: throwing getItem/setItem -> in-memory dismissal still holds", () => {
  const d = createHintDismissal(() => throwingStorage);
  assert.equal(d.isDismissed(), false);
  d.dismiss();
  assert.equal(d.isDismissed(), true);
});

test("dismissal: a getStorage() that throws (blocked site data) -> in-memory dismissal still holds", () => {
  const d = createHintDismissal(() => {
    throw new Error("SecurityError: localStorage blocked");
  });
  assert.equal(d.isDismissed(), false);
  d.dismiss();
  assert.equal(d.isDismissed(), true);
});

// ---------------------------------------------------------------------
// Component harness
// ---------------------------------------------------------------------
function makeTimerQueue() {
  const pending = new Map();
  let nextId = 1;
  return {
    setTimer(fn, ms) {
      const id = nextId++;
      pending.set(id, { fn, ms });
      return id;
    },
    clearTimer(id) {
      pending.delete(id);
    },
    pending: () => pending.size,
    lastDelay: () => [...pending.values()].at(-1)?.ms,
    flush() {
      let guard = 0;
      while (pending.size) {
        if (++guard > 100) throw new Error("timer queue did not drain");
        const [id, { fn }] = pending.entries().next().value;
        pending.delete(id);
        fn();
      }
    },
  };
}

function makeEventTarget() {
  const listeners = new Map();
  return {
    addEventListener(type, fn) {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type).add(fn);
    },
    removeEventListener(type, fn) {
      listeners.get(type)?.delete(fn);
    },
    fire(type) {
      for (const fn of listeners.get(type) || []) fn({ type });
    },
    count: () => [...listeners.values()].reduce((n, s) => n + s.size, 0),
  };
}

function harness({ startEnv = ENV.pixel7Normal, storage = memStorage(), getStorage } = {}) {
  const dom = createFakeDom();
  const wrapEl = dom.document.createElement("div");
  const otherSlot = dom.document.createElement("div");
  const hintSlotEl = dom.document.createElement("div");
  const hintTextEl = dom.document.createElement("span");
  const hintCloseBtn = dom.document.createElement("button");
  wrapEl.hidden = true;
  otherSlot.hidden = true;
  hintSlotEl.hidden = true;
  wrapEl.append(otherSlot, hintSlotEl);
  hintSlotEl.append(hintTextEl, hintCloseBtn);
  // Count every write to the slot's `hidden` (the flicker guard).
  let hiddenValue = true;
  let hiddenWrites = 0;
  Object.defineProperty(hintSlotEl, "hidden", {
    get: () => hiddenValue,
    set: (v) => {
      hiddenWrites++;
      hiddenValue = v;
    },
  });
  let current = startEnv;
  const target = makeEventTarget();
  const timers = makeTimerQueue();
  let focusCalls = 0;
  const hint = createDesktopSiteHint({
    elements: { wrapEl, hintSlotEl, hintTextEl, hintCloseBtn },
    readEnv: () => current,
    eventTarget: target,
    getStorage: getStorage || (() => storage),
    focusAfterDismiss: () => {
      focusCalls++;
    },
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
  });
  return {
    hint,
    target,
    storage,
    setEnv: (e) => {
      current = e;
    },
    shown: () => !hintSlotEl.hidden && !wrapEl.hidden,
    slotHidden: () => hintSlotEl.hidden,
    wrapHidden: () => wrapEl.hidden,
    text: () => hintTextEl.textContent,
    closeLabel: () => hintCloseBtn.getAttribute("aria-label"),
    close: () => hintCloseBtn.dispatchEvent({ type: "click", target: hintCloseBtn }),
    hiddenWrites: () => hiddenWrites,
    timers,
    focusCalls: () => focusCalls,
    otherSlot,
    wrapEl,
  };
}

test("component: shows immediately on a phone in Desktop site mode, with the hint text and close label", () => {
  const h = harness({ startEnv: ENV.pixel7DesktopSite });
  try {
    assert.equal(h.shown(), true);
    assert.equal(h.hint.visible(), true);
    assert.equal(
      h.text(),
      "Desktop view is on. Turn off 'Desktop site' in your browser menu for the phone layout.",
    );
    assert.equal(h.closeLabel(), "Dismiss desktop view hint");
  } finally {
    h.hint.dispose();
  }
});

test("component: stays hidden on a phone in normal mode and on a desktop", () => {
  for (const e of [ENV.pixel7Normal, ENV.desktop]) {
    const h = harness({ startEnv: e });
    try {
      assert.equal(h.shown(), false);
      assert.equal(h.wrapHidden(), true);
    } finally {
      h.hint.dispose();
    }
  }
});

test("component: re-evaluates on resize (show) and orientationchange (hide), after the debounce", () => {
  const h = harness({ startEnv: ENV.pixel7Normal });
  try {
    assert.equal(h.shown(), false);
    h.setEnv(ENV.pixel7DesktopSite);
    h.target.fire("resize");
    assert.equal(h.shown(), false, "not before the debounce timer fires");
    assert.equal(h.timers.lastDelay(), 200, "default debounce 200 ms");
    h.timers.flush();
    assert.equal(h.shown(), true, "shown after resize");
    h.setEnv(ENV.pixel7LandscapeNormal);
    h.target.fire("orientationchange");
    h.timers.flush();
    assert.equal(h.shown(), false, "hidden after orientationchange");
    assert.equal(h.wrapHidden(), true, "the shared wrap hides again when no slot is shown");
  } finally {
    h.hint.dispose();
  }
});

test("component: a resize burst is debounced into ONE evaluation, and no-change events never touch the slot", () => {
  const h = harness({ startEnv: ENV.pixel7DesktopSite });
  try {
    const writesAtStart = h.hiddenWrites();
    // Unrelated layout changes that keep the verdict: never a write.
    for (let i = 0; i < 5; i++) h.target.fire("resize");
    assert.equal(h.timers.pending(), 1, "a burst coalesces into one pending evaluation");
    h.timers.flush();
    assert.equal(h.hiddenWrites(), writesAtStart, "no-change re-evaluation must not write `hidden`");
    // A burst that flips back and forth but ENDS where it began: no write.
    for (const e of [ENV.desktop, ENV.pixel7Normal, ENV.pixel7DesktopSite]) {
      h.setEnv(e);
      h.target.fire("resize");
    }
    h.timers.flush();
    assert.equal(h.hiddenWrites(), writesAtStart, "a settled burst that ends unchanged must not flicker");
    assert.equal(h.shown(), true);
    // A burst that ends in a different verdict: exactly one write.
    for (const e of [ENV.desktop, ENV.pixel7DesktopSite, ENV.pixel7Normal]) {
      h.setEnv(e);
      h.target.fire("resize");
    }
    h.timers.flush();
    assert.equal(h.shown(), false, "hidden after the burst settles");
    assert.equal(h.hiddenWrites() - writesAtStart, 1, "one write per real change");
  } finally {
    h.hint.dispose();
  }
});

test("component: close dismisses, persists, removes listeners, moves focus, and never re-shows", () => {
  const storage = memStorage();
  const h = harness({ startEnv: ENV.pixel7DesktopSite, storage });
  try {
    assert.equal(h.target.count(), 2, "resize + orientationchange");
    h.close();
    assert.equal(h.shown(), false);
    assert.equal(storage.data[DESKTOP_SITE_HINT_STORAGE_KEY], "1");
    assert.equal(h.target.count(), 0, "listeners removed after dismissal");
    assert.equal(h.focusCalls(), 1, "focus handed to focusAfterDismiss, not dropped to <body>");
    h.target.fire("resize");
    assert.equal(h.timers.pending(), 0, "no evaluation scheduled after dismissal");
    h.timers.flush();
    assert.equal(h.shown(), false);
  } finally {
    h.hint.dispose();
  }
  // A new page load in the same browser: stays hidden, no listeners.
  const again = harness({ startEnv: ENV.pixel7DesktopSite, storage });
  try {
    assert.equal(again.shown(), false);
    assert.equal(again.hint.visible(), false);
    assert.equal(again.target.count(), 0);
  } finally {
    again.hint.dispose();
  }
});

test("component: with storage that throws, close still hides it for the session", () => {
  const h = harness({ startEnv: ENV.pixel7DesktopSite, storage: throwingStorage });
  try {
    assert.equal(h.shown(), true, "a throwing getItem must not prevent the hint");
    h.close();
    assert.equal(h.shown(), false);
    h.setEnv(ENV.pixel7DesktopSite);
    h.target.fire("resize");
    h.timers.flush();
    assert.equal(h.shown(), false);
  } finally {
    h.hint.dispose();
  }
  const blocked = harness({
    startEnv: ENV.pixel7DesktopSite,
    getStorage: () => {
      throw new Error("SecurityError");
    },
  });
  try {
    assert.equal(blocked.shown(), true);
    blocked.close();
    assert.equal(blocked.shown(), false);
  } finally {
    blocked.hint.dispose();
  }
});

test("component: closing while an evaluation is pending cancels it", () => {
  const h = harness({ startEnv: ENV.pixel7DesktopSite });
  try {
    h.target.fire("resize");
    assert.equal(h.timers.pending(), 1);
    h.close();
    assert.equal(h.timers.pending(), 0, "dispose clears the pending timer");
  } finally {
    h.hint.dispose();
  }
});

test("component: a throwing focusAfterDismiss never undoes the dismissal", () => {
  const dom = createFakeDom();
  const mk = (t) => dom.document.createElement(t);
  const wrapEl = mk("div");
  const hintSlotEl = mk("div");
  const hintCloseBtn = mk("button");
  wrapEl.hidden = true;
  hintSlotEl.hidden = true;
  wrapEl.append(hintSlotEl);
  const storage = memStorage();
  const hint = createDesktopSiteHint({
    elements: { wrapEl, hintSlotEl, hintTextEl: mk("span"), hintCloseBtn },
    readEnv: () => ENV.pixel7DesktopSite,
    eventTarget: makeEventTarget(),
    getStorage: () => storage,
    focusAfterDismiss: () => {
      throw new Error("focus failed");
    },
  });
  try {
    assert.equal(hint.visible(), true);
    assert.doesNotThrow(() => hintCloseBtn.dispatchEvent({ type: "click", target: hintCloseBtn }));
    assert.equal(hintSlotEl.hidden, true);
    assert.equal(storage.data[DESKTOP_SITE_HINT_STORAGE_KEY], "1");
  } finally {
    hint.dispose();
  }
});

test("component: closing the hint leaves the shared wrap visible while another banner slot is shown", () => {
  const h = harness({ startEnv: ENV.pixel7DesktopSite });
  try {
    h.otherSlot.hidden = false;
    syncBannerWrap(h.wrapEl);
    h.close();
    assert.equal(h.wrapHidden(), false);
  } finally {
    h.hint.dispose();
  }
});

test("component: a throwing readEnv fails toward no hint", () => {
  const dom = createFakeDom();
  const mk = (t) => dom.document.createElement(t);
  const wrapEl = mk("div");
  const hintSlotEl = mk("div");
  wrapEl.hidden = true;
  hintSlotEl.hidden = true;
  wrapEl.append(hintSlotEl);
  const hint = createDesktopSiteHint({
    elements: { wrapEl, hintSlotEl, hintTextEl: mk("span"), hintCloseBtn: mk("button") },
    readEnv: () => {
      throw new Error("no screen");
    },
    eventTarget: makeEventTarget(),
    getStorage: () => null,
  });
  try {
    assert.equal(hint.visible(), false);
    assert.equal(hintSlotEl.hidden, true);
  } finally {
    hint.dispose();
  }
});

// ---------------------------------------------------------------------
// Demo mode: no store, no API. The modules import neither.
// ---------------------------------------------------------------------
function stripComments(src) {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/[^\n]*/g, "$1");
}

test("demo mode: the hint modules import no API client, store or fetch", () => {
  for (const rel of ["js/components/desktop-site-hint.js", "js/lib/desktop-site-hint.js"]) {
    const src = stripComments(read(rel));
    assert.doesNotMatch(src, /api\.js|store|fetch\(|isDemoMode/, rel);
  }
});

// ---------------------------------------------------------------------
// Wiring pins
// ---------------------------------------------------------------------
function braceDepthAt(src, index) {
  let depth = 0;
  let quote = null;
  for (let i = 0; i < index; i++) {
    const ch = src[i];
    if (quote) {
      if (ch === "\\") i++;
      else if (ch === quote) quote = null;
      continue;
    }
    if (ch === '"' || ch === "'" || ch === "`") quote = ch;
    else if (ch === "{") depth++;
    else if (ch === "}") depth--;
  }
  return depth;
}

test("app.js wires createDesktopSiteHint at top level with the shell elements, window and a lazy localStorage getter", () => {
  const src = stripComments(read("js/app.js"));
  assert.match(src, /import \{ createDesktopSiteHint, readBrowserEnv \} from "\.\/components\/desktop-site-hint\.js"/);
  const m = /createDesktopSiteHint\(\{([\s\S]*?)\n\}\);/.exec(src);
  assert.ok(m, "app.js must call createDesktopSiteHint({...})");
  assert.equal(braceDepthAt(src, m.index), 0, "the call must run at module top level");
  const args = m[1];
  assert.match(args, /wrapEl: document\.getElementById\("banner-wrap"\)/);
  assert.match(args, /hintSlotEl: document\.getElementById\("desktop-hint"\)/);
  assert.match(args, /hintTextEl: document\.getElementById\("desktop-hint-text"\)/);
  assert.match(args, /hintCloseBtn: document\.getElementById\("desktop-hint-close"\)/);
  assert.match(args, /readEnv: \(\) => readBrowserEnv\(window\)/);
  assert.match(args, /eventTarget: window/);
  assert.match(args, /getStorage: \(\) => window\.localStorage/, "lazy getter: reading localStorage itself can throw");
  assert.match(args, /focusAfterDismiss: \(\) => viewRoot\.focus\(\{ preventScroll: true \}\)/, "focus goes to the view root on dismiss");
});

test("index.html: the hint is a hidden, labelled .banner-slot inside the one #banner-wrap, with a focusable labelled close button", () => {
  const html = read("index.html").replace(/<!--[\s\S]*?-->/g, "");
  assert.equal((html.match(/class="banner-wrap"/g) || []).length, 1, "still exactly one .banner-wrap");
  const wrapAt = html.indexOf('<div class="banner-wrap" id="banner-wrap" hidden>');
  const bypassAt = html.indexOf('id="bypass-banner-wrap"');
  const slot = /<div class="banner-slot" id="desktop-hint"([^>]*)>/.exec(html);
  assert.ok(slot, "#desktop-hint slot missing");
  assert.ok(wrapAt !== -1 && slot.index > bypassAt && bypassAt > wrapAt, "inside the wrap, after the bypass slot");
  const navAt = html.indexOf('<nav class="nav"');
  assert.ok(slot.index < navAt, "the slot sits inside the wrap, before <nav> (no new #app grid child)");
  assert.match(slot[1], /\brole="region"/);
  assert.match(slot[1], /\baria-label="Display hint"/);
  assert.match(slot[1], /\shidden\b/, "hidden by default");
  assert.ok(html.indexOf('<span id="desktop-hint-text"></span>') > slot.index);
  const btn = /<button([^>]*)id="desktop-hint-close"([^>]*)>/.exec(html);
  assert.ok(btn, "close button missing");
  const attrs = btn[1] + btn[2];
  assert.match(attrs, /type="button"/);
  assert.doesNotMatch(attrs, /aria-label/, "the label has ONE source: lib/desktop-site-hint.js (set by the component)");
  assert.doesNotMatch(attrs, /tabindex="-1"|disabled|aria-hidden/, "the close button must stay focusable");
  assert.match(
    /<main id="view-root"[^>]*>/.exec(html)[0],
    /tabindex="-1"/,
    "#view-root must be programmatically focusable (dismiss focus target)",
  );
  // The wrap must close before <nav>: count the divs between wrap and nav.
  const between = html.slice(wrapAt, navAt);
  const opens = (between.match(/<div\b/g) || []).length;
  const closes = (between.match(/<\/div>/g) || []).length;
  assert.equal(opens, closes, "every div opened in the banner wrap is closed before <nav>");
});
