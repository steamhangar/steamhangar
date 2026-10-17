/**
 * UI kit scale lints (WP UI-KIT-1, ADR-0028).
 *
 * Static analysis of the shipped stylesheets, comment-stripped:
 *   (a) theme.css defines the 15 scale tokens (--fs-1..7, --sp-1..8) with
 *       exactly the values of docs/design/rc20/kit.html, and --dim-2 is the
 *       lifted #7A91A0.
 *   (b) kit.css has no raw px font size — neither `font-size:Npx` nor a
 *       px size inside a `font:` shorthand (a px LINE-HEIGHT in the
 *       shorthand is allowed; it is not a font size).
 *   (c) app.css's raw `font-size:Npx` declarations are counted and pinned.
 *       The number only goes DOWN: a package that migrates a page to the
 *       kit lowers the pin, a package that adds a raw size fails here.
 *   (d) kit.css has no hex colour: tokens only.
 *   (e) every kit.css selector is scoped under `.kit` — the opt-in promise
 *       ("nothing reaches a page that has not opted in", kit.css header).
 *   (f) index.html loads kit.css between theme.css and app.css, and the
 *       pill's disc rule (16px, 60% glyph box) exists.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const webDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (...p) => readFileSync(path.join(webDir, ...p), "utf8");
const stripComments = (css) => css.replace(/\/\*[\s\S]*?\*\//g, "");

const themeCss = stripComments(read("css", "theme.css"));
const kitCss = stripComments(read("css", "kit.css"));
const appCss = stripComments(read("css", "app.css"));
const indexHtml = read("index.html").replace(/<!--[\s\S]*?-->/g, "");

/** `font-size:Npx` declarations (whitespace-tolerant). */
const RAW_FONT_SIZE = /font-size\s*:\s*\d[\d.]*px/g;
/** A px FONT SIZE inside a `font:` shorthand: optional style/weight/variant
 * keywords or numbers, then `Npx` followed by `/` (line-height) or a space
 * (family). `font:500 var(--fs-1)/16px var(--mono)` does not match: the
 * size token is `var(...)`, and the regex never reaches the line-height. */
const RAW_FONT_SHORTHAND = /(^|[^-\w])font\s*:\s*(?:[a-z0-9-]+\s+)*\d[\d.]*px(?:\/|\s)/g;

/**
 * Pinned count of raw `font-size:Npx` declarations in app.css, measured
 * comment-stripped on 2026-10-17 (WP UI-KIT-1: 172; CLIENTS-4: 167;
 * DOWNLOADS-2: 146 after the Downloads rules moved to kit.css). THIS NUMBER
 * ONLY GOES DOWN: lower it in the same package that migrates a page onto
 * the kit's scale tokens; never raise it.
 */
const APP_CSS_RAW_FONT_SIZE_PIN = 146;

// ---------------------------------------------------------------------
// (a) scale tokens and --dim-2
// ---------------------------------------------------------------------
test("theme.css: --fs-1..7 are 11/12/13/15/18/24/32px and --sp-1..8 are the 4px grid, verbatim", () => {
  const fs = [11, 12, 13, 15, 18, 24, 32];
  const sp = [4, 8, 12, 16, 20, 24, 32, 48];
  fs.forEach((px, i) => assert.ok(themeCss.includes(`--fs-${i + 1}:${px}px`), `--fs-${i + 1}:${px}px missing`));
  sp.forEach((px, i) => assert.ok(themeCss.includes(`--sp-${i + 1}:${px}px`), `--sp-${i + 1}:${px}px missing`));
  // Each token is declared exactly once (no redefinition hiding in a block).
  for (let i = 1; i <= 7; i++) assert.equal((themeCss.match(new RegExp(`--fs-${i}\\s*:`, "g")) || []).length, 1, `--fs-${i} declared more than once`);
  for (let i = 1; i <= 8; i++) assert.equal((themeCss.match(new RegExp(`--sp-${i}\\s*:`, "g")) || []).length, 1, `--sp-${i} declared more than once`);
});

test("theme.css: --dim-2 is #7A91A0 (5.0:1 on --surface), the old #63798A is gone", () => {
  assert.ok(themeCss.includes("--dim-2:#7A91A0"), "--dim-2:#7A91A0 missing");
  assert.ok(!/63798A/i.test(themeCss), "the old --dim-2 value #63798A is still in theme.css");
});

// ---------------------------------------------------------------------
// (b) + (d) kit.css uses tokens only
// ---------------------------------------------------------------------
test("kit.css: no raw px font size (font-size or font shorthand) — scale tokens only", () => {
  assert.deepEqual(kitCss.match(RAW_FONT_SIZE) || [], [], "raw font-size:Npx in kit.css");
  assert.deepEqual(kitCss.match(RAW_FONT_SHORTHAND) || [], [], "raw px size inside a font: shorthand in kit.css");
  // The kit really reads the scale (not a vacuous pass on an empty file).
  assert.ok((kitCss.match(/var\(--fs-\d\)/g) || []).length >= 20, "kit.css barely uses the --fs tokens");
});

test("kit.css: no hex colour — theme.css tokens (and color-mix of them) only", () => {
  assert.deepEqual(kitCss.match(/#[0-9a-fA-F]{3,8}\b/g) || [], [], "hex colour literal in kit.css");
  assert.ok(!/rgba?\(/.test(kitCss), "rgb()/rgba() literal in kit.css");
});

// ---------------------------------------------------------------------
// (c) app.css legacy count, shrinking pin
// ---------------------------------------------------------------------
test(`app.css: raw font-size:Npx declarations <= ${APP_CSS_RAW_FONT_SIZE_PIN} (pin only goes down)`, () => {
  const count = (appCss.match(RAW_FONT_SIZE) || []).length;
  assert.ok(
    count <= APP_CSS_RAW_FONT_SIZE_PIN,
    `app.css has ${count} raw font-size:Npx declarations, pin is ${APP_CSS_RAW_FONT_SIZE_PIN}: new CSS uses var(--fs-N) (ADR-0028)`,
  );
  // Honest pin: when a migration lowers the count, lower the constant too.
  assert.ok(
    count >= APP_CSS_RAW_FONT_SIZE_PIN - 40,
    `app.css has only ${count} raw font-size:Npx declarations — lower APP_CSS_RAW_FONT_SIZE_PIN to ${count} in this package`,
  );
});

// ---------------------------------------------------------------------
// (e) every rule is scoped under .kit
// ---------------------------------------------------------------------
test("kit.css: every selector starts with .kit (opt-in scope, nothing leaks into pages that did not opt in)", () => {
  // Rule headers = text between a `}` or `{`-closing and the next `{`,
  // skipping @-blocks' own header (their inner rules are checked too).
  const headers = [];
  let depth = 0;
  let start = 0;
  for (let i = 0; i < kitCss.length; i++) {
    const ch = kitCss[i];
    if (ch === "{") {
      headers.push(kitCss.slice(start, i).trim());
      depth++;
      start = i + 1;
    } else if (ch === "}") {
      depth--;
      start = i + 1;
    } else if (ch === ";" && depth >= 1) {
      start = i + 1;
    }
  }
  const selectors = headers.filter((h) => h && !h.startsWith("@"));
  assert.ok(selectors.length >= 60, `expected a full kit, found ${selectors.length} rules`);
  for (const header of selectors) {
    for (const sel of header.split(",")) {
      assert.match(sel.trim(), /^\.kit(\s|\.|\[|$)/, `kit.css selector not scoped under .kit: "${sel.trim()}"`);
    }
  }
  const atBlocks = headers.filter((h) => h.startsWith("@"));
  // @container is allowed: ADR-0028 sizes page compositions with container
  // queries, never with viewport breakpoints. Anything else is rejected.
  for (const at of atBlocks) assert.match(at, /^@(media|container)/, `unexpected at-rule in kit.css: ${at}`);
});

// ---------------------------------------------------------------------
// (f) wiring: stylesheet order and the pill disc rule
// ---------------------------------------------------------------------
test("index.html: kit.css is linked after theme.css and before app.css", () => {
  const links = [...indexHtml.matchAll(/<link rel="stylesheet" href="\/css\/([\w-]+\.css)">/g)].map((m) => m[1]);
  assert.deepEqual(links, ["theme.css", "kit.css", "app.css"]);
});

test("kit.css: the pill's status disc is 16px with a 60% glyph box (>= 3px of disc around every shape)", () => {
  assert.match(kitCss, /\.kit \.pill \.sic\s*\{[^}]*--sz:16px/);
  assert.match(kitCss, /\.kit \.pill \.sic svg\s*\{[^}]*width:60%;\s*height:60%/);
  // 16px * 0.6 = 9.6px glyph box, (16 - 9.6) / 2 = 3.2px of disc each side.
  assert.ok((16 - 16 * 0.6) / 2 >= 3);
});
