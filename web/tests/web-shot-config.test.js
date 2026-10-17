// WP WEB-GOLDENS-1: the parts of tools/web-shot.mjs that decide whether a
// golden is reproducible, checked without launching Chromium.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import vm from "node:vm";

import { DEFAULTS, DEFAULT_PAGES, DEFAULT_WIDTHS, DEMO_STORAGE, bootstrapScript, findChrome, parseArgs } from "../../tools/web-shot.mjs";
import { readViews } from "../../tools/web-serve.mjs";
import { STATS_RANGES, STATS_RANGE_KEY } from "../js/lib/traffic-view.js";
import { DESKTOP_SITE_HINT_STORAGE_KEY } from "../js/lib/desktop-site-hint.js";

const REPO = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const TOOL_SOURCE = readFileSync(join(REPO, "tools", "web-shot.mjs"), "utf8");
const FONTS_CONF = readFileSync(join(REPO, "tools", "web-shot-fonts.conf"), "utf8");
const THEME_CSS = readFileSync(join(REPO, "web", "css", "theme.css"), "utf8");
const API_SOURCE = readFileSync(join(REPO, "web", "js", "api.js"), "utf8");
// web/js/router.js touches `window` at import time; the server's parser
// reads the same VIEWS constant from the source instead.
const VIEWS = readViews(join(REPO, "web"));

test("demo storage uses the app's own keys, so a renamed key cannot silently drop demo mode", () => {
  // api.js keeps STORAGE_KEYS private; pin the literal the module reads.
  const demoKey = /demoMode:\s*"([^"]+)"/.exec(API_SOURCE)?.[1];
  assert.equal(demoKey, "steamvault.demoMode");
  assert.equal(DEMO_STORAGE[demoKey], "1");
  assert.equal(DEMO_STORAGE[STATS_RANGE_KEY], "6h");
  assert.ok(STATS_RANGES.includes("6h"), "a fixed, non-live range the Downloads page accepts");
  assert.equal(DEMO_STORAGE[DESKTOP_SITE_HINT_STORAGE_KEY], "1");
  assert.deepEqual(Object.keys(DEMO_STORAGE).sort(), [demoKey, STATS_RANGE_KEY, DESKTOP_SITE_HINT_STORAGE_KEY].sort());
});

test("default pages are the router's views plus the hidden kit page; widths are the three breakpoints", () => {
  for (const view of VIEWS) assert.ok(DEFAULT_PAGES.includes(view), view);
  assert.ok(DEFAULT_PAGES.includes("kit"));
  assert.deepEqual(DEFAULT_WIDTHS, [1400, 1000, 400]);
  // ci.yml's web-goldens-push job greps these two lines to validate the
  // artifact, so each list must stay on ONE line in the source.
  assert.match(TOOL_SOURCE, /^export const DEFAULT_PAGES = \[("[a-z-]+", )*"[a-z-]+"\];$/m);
  assert.match(TOOL_SOURCE, /^export const DEFAULT_WIDTHS = \[(\d+, )*\d+\];$/m);
});

test("bootstrap script freezes Date and seeds localStorage before the app runs", () => {
  const stored = new Map();
  const context = { window: {}, localStorage: { setItem: (k, v) => stored.set(k, v) }, Date };
  context.window = context;
  vm.createContext(context);
  vm.runInContext(bootstrapScript("2026-10-17T12:00:00.000Z", { a: "1", b: "2" }), context);
  const fixed = Date.parse("2026-10-17T12:00:00.000Z");
  assert.equal(vm.runInContext("Date.now()", context), fixed);
  assert.equal(vm.runInContext("new Date().getTime()", context), fixed);
  assert.equal(vm.runInContext("new Date(0).getTime()", context), 0, "explicit arguments still work");
  assert.equal(vm.runInContext("Date.name", context), "Date");
  assert.deepEqual([...stored.entries()], [["a", "1"], ["b", "2"]]);
});

test("parseArgs: defaults are the deterministic ones; --no- flags and lists parse", () => {
  const d = parseArgs([]);
  assert.equal(d.reducedMotion, true);
  assert.equal(d.hideScrollbars, true);
  assert.equal(d.blockOffsite, true);
  assert.equal(d.timezone, "UTC");
  assert.equal(d.locale, "en-US");
  assert.equal(d.fontsConf, DEFAULTS.fontsConf);
  assert.deepEqual(d.widths, DEFAULT_WIDTHS);
  const o = parseArgs(["--widths", "800, 400", "--pages", "library,kit", "--no-reduced-motion", "--base", "http://x:1/"]);
  assert.deepEqual(o.widths, [800, 400]);
  assert.deepEqual(o.pages, ["library", "kit"]);
  assert.equal(o.reducedMotion, false);
  assert.equal(o.base, "http://x:1");
  assert.throws(() => parseArgs(["--widths", "100"]), /--widths/);
  assert.throws(() => parseArgs(["--pages", "Library"]), /--pages/);
  assert.throws(() => parseArgs(["--clock", "yesterday"]), /--clock/);
  assert.throws(() => parseArgs(["--out"]), /needs a value/);
});

test("findChrome: CHROME_PATH wins, else the newest Playwright chromium revision", () => {
  assert.equal(findChrome({ CHROME_PATH: "/x/chrome" }), "/x/chrome");
  assert.equal(findChrome({ PLAYWRIGHT_BROWSERS_PATH: "/nonexistent-dir-for-test" }), null);
});

test("fontconfig file is well-formed XML and aliases every family of the stylesheet's font stacks", () => {
  // fontconfig silently ignores a malformed file (the system config wins),
  // so the devbox's sans-serif -> Liberation Mono mapping would leak into
  // the goldens. The one mistake that is easy to make in XML: `--` inside a
  // comment.
  for (const comment of FONTS_CONF.match(/<!--[\s\S]*?-->/g) ?? []) {
    assert.doesNotMatch(comment.slice(4, -3), /--/, "double hyphen inside an XML comment");
  }
  assert.equal((FONTS_CONF.match(/<fontconfig>/g) ?? []).length, 1);
  assert.doesNotMatch(FONTS_CONF, /<include/, "must not include the host's fontconfig");
  const aliased = new Set([...FONTS_CONF.matchAll(/<alias[^>]*><family>([^<]+)<\/family>/g)].map((m) => m[1]));
  const stacks = [...THEME_CSS.matchAll(/^\s*--(?:ui|mono):\s*([^;]+);/gm)].map((m) => m[1]);
  assert.ok(stacks.length >= 2, "theme.css defines --ui and --mono");
  for (const stack of stacks) {
    for (const family of stack.split(",").map((f) => f.trim().replace(/^"|"$/g, ""))) {
      assert.ok(aliased.has(family), `${family} from theme.css has no alias in web-shot-fonts.conf`);
    }
  }
  for (const family of aliased) {
    const target = new RegExp(`<family>${family.replace(/[-/\\^$*+?.()|[\]{}]/g, "\\$&")}</family><prefer><family>Liberation `);
    assert.match(FONTS_CONF, target, `${family} must map to a Liberation font`);
  }
});
