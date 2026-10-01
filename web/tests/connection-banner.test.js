/**
 * Pins for the connection-lost indicator (WP WEB-FIX-2):
 * `lib/connection-watch.js` (the rule), `components/connection-banner.js`
 * (DOM + announcer + published flag), `lib/banner-wrap.js` (the shared
 * wrap), and the app.js wiring (comment-stripped top-level source scan,
 * same idiom as auth-recovery.test.js).
 *
 * Driven with a fake store and a controllable clock; elements come from
 * fake-dom.js. Assertions compare TEXT/booleans, never nodes (a failed node
 * assertion dumps the fake-DOM graph and OOM-killed the runner, WEB-FIX-1).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { createFakeDom } from "./fake-dom.js";
import { createConnectionBanner } from "../js/components/connection-banner.js";
import { connectionLostText, LOST_AFTER_MS, OFFLINE_CONTROL_TITLE } from "../js/lib/connection-watch.js";
import { syncBannerWrap } from "../js/lib/banner-wrap.js";
import { ApiError, ERROR_KINDS } from "../js/errors.js";

const dom = createFakeDom();

function makeFakeStore() {
  const subs = new Map();
  return {
    subscribe(kind, cb) {
      if (!subs.has(kind)) subs.set(kind, new Set());
      subs.get(kind).add(cb);
      return () => subs.get(kind).delete(cb);
    },
    emit(kind, payload) {
      for (const cb of subs.get(kind) || []) cb(payload);
    },
    kinds: () => [...subs.keys()],
  };
}

const fail = (kind) => ({ error: new ApiError(kind, "x") });
const NET = () => fail(ERROR_KINDS.NETWORK);
const SRV = () => fail(ERROR_KINDS.SERVER);
const AUTH = () => fail(ERROR_KINDS.AUTH);
const OK = { items: [], diff: null };

function harness({ demo = false } = {}) {
  const store = makeFakeStore();
  const wrapEl = dom.document.createElement("div");
  const slotEl = dom.document.createElement("div");
  const otherSlot = dom.document.createElement("div");
  const textEl = dom.document.createElement("span");
  wrapEl.hidden = true;
  slotEl.hidden = true;
  otherSlot.hidden = true;
  wrapEl.append(slotEl, otherSlot);
  slotEl.appendChild(textEl);
  const announced = [];
  const published = [];
  let t = new Date(2026, 9, 1, 14, 5, 30).getTime(); // local 14:05:30
  const banner = createConnectionBanner({
    store,
    isDemoMode: () => demo,
    elements: { wrapEl, slotEl, textEl },
    announce: (text) => announced.push(text),
    setConnectionLost: (v) => published.push(v),
    now: () => t,
  });
  return {
    store,
    banner,
    announced,
    published,
    advance: (ms) => {
      t += ms;
    },
    shown: () => !slotEl.hidden && !wrapEl.hidden,
    wrapHidden: () => wrapEl.hidden,
    text: () => textEl.textContent,
    otherSlot,
    wrapEl,
  };
}

test("subscribes to all four poll resources", () => {
  const h = harness();
  assert.deepEqual(h.store.kinds().sort(), ["cache", "clients", "games", "jobs"]);
});

test("one failure stays silent, however long it is followed by nothing", () => {
  const h = harness();
  h.store.emit("jobs", OK);
  h.advance(1000);
  h.store.emit("jobs", NET());
  h.advance(5 * 60 * 1000);
  assert.equal(h.shown(), false);
  assert.deepEqual(h.announced, []);
});

test("a blip (failures < 20 s apart, then success) stays silent", () => {
  const h = harness();
  h.store.emit("jobs", OK);
  h.store.emit("jobs", NET());
  h.advance(1000);
  h.store.emit("games", NET());
  h.advance(LOST_AFTER_MS - 2000);
  h.store.emit("jobs", SRV());
  h.advance(500);
  h.store.emit("clients", OK);
  assert.equal(h.shown(), false);
  assert.deepEqual(h.announced, []);
  assert.deepEqual(h.published, []);
});

test("shows after a NETWORK/SERVER streak spanning >= 20 s, with the last-update time", () => {
  const h = harness();
  h.store.emit("cache", { item: {} }); // success at 14:05
  h.advance(60 * 1000);
  h.store.emit("jobs", NET());
  h.advance(LOST_AFTER_MS);
  h.store.emit("games", SRV());
  assert.equal(h.shown(), true);
  assert.equal(
    h.text(),
    "Lost connection to the vault — showing the last data received (last update 14:05). Retrying…",
  );
  assert.deepEqual(h.published, [true]);
  assert.equal(h.banner.lost(), true);
});

test("no last-update suffix when no poll ever succeeded in this page life", () => {
  const h = harness();
  h.store.emit("jobs", NET());
  h.advance(LOST_AFTER_MS);
  h.store.emit("jobs", NET());
  assert.equal(h.text(), "Lost connection to the vault — showing the last data received. Retrying…");
  assert.equal(connectionLostText(null), h.text());
});

test("clears on the next successful poll of any resource", () => {
  const h = harness();
  h.store.emit("jobs", NET());
  h.advance(LOST_AFTER_MS);
  h.store.emit("jobs", NET());
  assert.equal(h.shown(), true);
  h.advance(3000);
  h.store.emit("clients", OK);
  assert.equal(h.shown(), false);
  assert.equal(h.wrapHidden(), true, "the shared wrap hides again when no slot is shown");
  assert.deepEqual(h.published, [true, false]);
});

test("AUTH errors never show it (auth-recovery owns them), and do not end a streak either", () => {
  const h = harness();
  h.store.emit("jobs", AUTH());
  h.advance(10 * 60 * 1000);
  h.store.emit("jobs", AUTH());
  h.store.emit("games", AUTH());
  assert.equal(h.shown(), false);
  assert.deepEqual(h.announced, []);
  assert.deepEqual(h.published, []);
  // ...and an AUTH tick inside a NETWORK streak neither clears nor restarts it.
  h.store.emit("jobs", NET());
  h.advance(LOST_AFTER_MS / 2);
  h.store.emit("games", AUTH());
  h.advance(LOST_AFTER_MS / 2);
  h.store.emit("jobs", NET());
  assert.equal(h.shown(), true);
});

test("demo mode never shows it: no subscription at all", () => {
  const h = harness({ demo: true });
  assert.deepEqual(h.store.kinds(), []);
  h.store.emit("jobs", NET());
  h.advance(LOST_AFTER_MS * 3);
  h.store.emit("jobs", NET());
  assert.equal(h.shown(), false);
  assert.deepEqual(h.announced, []);
});

test("announces once per transition, not on every failing tick", () => {
  const h = harness();
  h.store.emit("jobs", OK);
  h.store.emit("jobs", NET());
  h.advance(LOST_AFTER_MS);
  for (let i = 0; i < 6; i++) {
    h.store.emit(["jobs", "games", "clients", "cache"][i % 4], NET());
    h.advance(5000);
  }
  assert.equal(h.announced.length, 1);
  assert.match(h.announced[0], /^Lost connection to the vault — /);
  h.store.emit("jobs", OK);
  h.store.emit("games", OK);
  assert.deepEqual(h.announced.slice(1), ["Connection to the vault restored."]);
  assert.deepEqual(h.published, [true, false]);
});

test("the shared wrap stays visible while the OTHER (bypass) slot is shown", () => {
  const h = harness();
  h.otherSlot.hidden = false;
  syncBannerWrap(h.wrapEl);
  assert.equal(h.wrapHidden(), false);
  h.store.emit("jobs", NET());
  h.advance(LOST_AFTER_MS);
  h.store.emit("jobs", NET());
  h.store.emit("jobs", OK);
  assert.equal(h.wrapHidden(), false, "clearing the connection banner must not hide the bypass banner");
  h.otherSlot.hidden = true;
  syncBannerWrap(h.wrapEl);
  assert.equal(h.wrapHidden(), true);
});

test("offline control title literal", () => {
  assert.equal(OFFLINE_CONTROL_TITLE, "Not available while the connection to the vault is lost.");
});

// ---------------------------------------------------------------------
// Wiring pins (comment-stripped source scan, top-level call).
// ---------------------------------------------------------------------

const here = dirname(fileURLToPath(import.meta.url));
function stripComments(src) {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/[^\n]*/g, "$1");
}
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

test("app.js wires createConnectionBanner at top level: real store/isDemoMode, the three shell elements, #view-announcer, setConnectionLost", () => {
  const src = stripComments(readFileSync(join(here, "..", "js", "app.js"), "utf8"));
  assert.match(src, /import \{ createConnectionBanner \} from "\.\/components\/connection-banner\.js"/);
  assert.match(src, /import \{ setConnectionLost \} from "\.\/connection-status\.js"/);
  const m = /createConnectionBanner\(\{([\s\S]*?)\n\}\);/.exec(src);
  assert.ok(m, "app.js must call createConnectionBanner({...}) — deleted?");
  assert.equal(braceDepthAt(src, m.index), 0, "the call must run at module top level");
  const args = m[1];
  assert.match(args, /^\s*store,\s*isDemoMode,/);
  assert.match(args, /wrapEl: document\.getElementById\("banner-wrap"\)/);
  assert.match(args, /slotEl: document\.getElementById\("conn-banner"\)/);
  assert.match(args, /textEl: document\.getElementById\("conn-banner-text"\)/);
  assert.match(args, /announce: \(text\) => \{\s*viewAnnouncer\.textContent = text;\s*\}/);
  assert.match(args, /\bsetConnectionLost,?\s*$/);
});

test("index.html: one shared #banner-wrap holding the #conn-banner and #bypass-banner-wrap slots, both hidden by default", () => {
  const html = readFileSync(join(here, "..", "index.html"), "utf8").replace(/<!--[\s\S]*?-->/g, "");
  assert.equal((html.match(/class="banner-wrap"/g) || []).length, 1, "exactly one .banner-wrap (it owns the BP-L banner grid area)");
  const wrapAt = html.indexOf('<div class="banner-wrap" id="banner-wrap" hidden>');
  const connAt = html.indexOf('<div class="banner-slot" id="conn-banner" hidden>');
  const bypassAt = html.indexOf('<div class="banner-slot" id="bypass-banner-wrap" hidden>');
  assert.ok(wrapAt !== -1 && connAt > wrapAt && bypassAt > connAt, "wrap, then connection slot, then bypass slot");
  assert.ok(html.indexOf('<span id="conn-banner-text"></span>') > connAt);
  assert.ok(html.indexOf('id="view-announcer" class="sr-only" role="status"') !== -1);
});
