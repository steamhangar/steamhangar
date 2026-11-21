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
 *   (g) no stylesheet sets a px font size below --fs-1 (11px), the floor
 *       theme.css names "never below this" (FONT-FLOOR-1, audit D-3).
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

/** `font-size:Npx` declarations (whitespace-tolerant; `.9px` included).
 * Group 1 is the number. */
const RAW_FONT_SIZE = /font-size\s*:\s*(\d*\.?\d+)px/g;
/** A px FONT SIZE inside a `font:` shorthand: optional style/weight/variant
 * keywords or numbers, then `Npx` followed by `/` (line-height) or a space
 * (family). `font:500 var(--fs-1)/16px var(--mono)` does not match: the
 * size token is `var(...)`, and the regex never reaches the line-height. */
const RAW_FONT_SHORTHAND = /(^|[^-\w])font\s*:\s*(?:[a-z0-9-]+\s+)*(\d*\.?\d+)px(?:\/|\s)/g;

/**
 * Pinned count of raw `font-size:Npx` declarations in app.css, measured
 * comment-stripped on 2026-10-17 (WP UI-KIT-1: 172; CLIENTS-4: 167;
 * DOWNLOADS-2: 146 after the Downloads rules moved to kit.css; SETTINGS-2:
 * 134; 95 by rc22; FONT-FLOOR-1 2026-11-21: 74 after the 21 sizes below
 * 11px became var(--fs-1); DESIGN-NITS-1: 72, the banner actions are
 * var(--fs-2) and a duplicate `.errline` rule is gone). THIS NUMBER ONLY GOES DOWN: lower it in the
 * same package that migrates a page onto the kit's scale tokens; never
 * raise it.
 */
const APP_CSS_RAW_FONT_SIZE_PIN = 72;

/** The type floor: --fs-1 (theme.css "never below this"). */
const FONT_FLOOR_PX = 11;

/** Every raw px font size in `css`, found with the two lint regexes above
 * (`font-size:Npx` and a `font:` shorthand's size, never its px
 * line-height). Returns [{px, text}]. */
function pxFontSizes(css) {
  return [
    ...[...css.matchAll(RAW_FONT_SIZE)].map((m) => ({ px: Number(m[1]), text: m[0] })),
    ...[...css.matchAll(RAW_FONT_SHORTHAND)].map((m) => ({ px: Number(m[2]), text: m[0].trim() })),
  ];
}

/** px value of each --fs-N token, read from theme.css. */
const FS_TOKENS = Object.fromEntries([...themeCss.matchAll(/--fs-(\d)\s*:\s*(\d+)px/g)].map((m) => [m[1], Number(m[2])]));
const CSS_WIDE = /^(inherit|initial|unset|revert|revert-layer)$/;
const SHORTHAND_PREFIX = /^(normal|italic|oblique|bold|bolder|lighter|small-caps|condensed|semi-condensed|expanded|semi-expanded|[1-9]\d{0,2}|1000)$/;
const SHORTHAND_SYSTEM = /^(caption|icon|menu|message-box|small-caption|status-bar)$/;

/** Split `value` at top-level whitespace (or at `sep`), keeping parentheses together. */
function splitTop(value, sep = /\s/) {
  const parts = [];
  let depth = 0;
  let cur = "";
  for (const ch of value) {
    if (ch === "(") depth++;
    if (ch === ")") depth--;
    if (depth === 0 && sep.test(ch)) {
      if (cur) parts.push(cur);
      cur = "";
    } else cur += ch;
  }
  if (cur) parts.push(cur);
  return parts;
}

/** Every font-size value in `css`: each `font-size:` value and each `font:`
 * shorthand's size token (the token before its `/line-height` or family).
 * Returns [{value, text}]. */
function fontSizeValues(css) {
  const out = [];
  for (const m of css.matchAll(/(?:^|[^-\w])font-size\s*:\s*([^;}]+)/g)) out.push({ value: m[1].trim(), text: m[0].trim() });
  for (const m of css.matchAll(/(?:^|[^-\w])font\s*:\s*([^;}]+)/g)) {
    const tokens = splitTop(m[1].trim());
    let i = 0;
    while (i < tokens.length && SHORTHAND_PREFIX.test(tokens[i])) i++;
    if (i >= tokens.length || CSS_WIDE.test(tokens[i]) || SHORTHAND_SYSTEM.test(tokens[i])) continue;
    out.push({ value: splitTop(tokens[i], /\//)[0], text: m[0].trim() });
  }
  return out;
}

/** Evaluate an arithmetic expression of numbers, + - * / and parentheses
 * (a resolved calc() body). Returns NaN for anything else, never runs code. */
function evalArithmetic(text) {
  const tokens = text.match(/\d*\.?\d+|[-+*/()]|\S/g) ?? [];
  let i = 0;
  const peek = () => tokens[i];
  const next = () => tokens[i++];
  function primary() {
    const t = next();
    if (t === "(") {
      const v = sum();
      return next() === ")" ? v : NaN;
    }
    if (t === "-") return -primary();
    if (t === "+") return primary();
    return t !== undefined && /^\d*\.?\d+$/.test(t) ? Number(t) : NaN;
  }
  function product() {
    let v = primary();
    while (peek() === "*" || peek() === "/") v = next() === "*" ? v * primary() : v / primary();
    return v;
  }
  function sum() {
    let v = product();
    while (peek() === "+" || peek() === "-") v = next() === "+" ? v + product() : v - product();
    return v;
  }
  const v = sum();
  return i === tokens.length ? v : NaN;
}

/**
 * Why a font-size value breaks the floor, or null when it holds: px below
 * --fs-1, any em/rem (relative sizes are not on the scale; none exist),
 * a calc() that resolves below --fs-1 or mixes other units, or anything
 * this lint does not know (fail closed).
 */
function floorViolation(value) {
  const v = value.replace(/\s*!important$/, "").trim();
  if (CSS_WIDE.test(v)) return null;
  if (/\d(r?em)\b/.test(v)) return "em/rem font size (use var(--fs-N))";
  const tok = /^var\(--fs-(\d)\)$/.exec(v);
  if (tok) return FS_TOKENS[tok[1]] ? null : `unknown token ${v}`;
  const px = /^(\d*\.?\d+)px$/.exec(v);
  if (px) return Number(px[1]) < FONT_FLOOR_PX ? `${v} is below --fs-1` : null;
  if (/^calc\(/.test(v)) {
    const expr = v.replace(/var\(--fs-(\d)\)/g, (m, n) => (FS_TOKENS[n] ? String(FS_TOKENS[n]) : "NaN")).replace(/(\d*\.?\d+)px/g, "$1");
    if (!/^calc\([\d.\s+\-*/()]+\)$/.test(expr)) return `calc() with a unit or term this lint cannot resolve: ${v}`;
    const px = evalArithmetic(expr.slice(4));
    return Number.isFinite(px) && px >= FONT_FLOOR_PX ? null : `${v} resolves to ${px}px, below --fs-1`;
  }
  return `unrecognised font size ${v}`;
}

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
// (g) the --fs-1 floor
// ---------------------------------------------------------------------
test(`no font size below --fs-1 (${FONT_FLOOR_PX}px) in theme.css, kit.css or app.css: px, calc() or em/rem (FONT-FLOOR-1)`, () => {
  assert.equal(FS_TOKENS[1], FONT_FLOOR_PX, "the floor constant no longer matches --fs-1");
  for (const [name, css] of [["theme.css", themeCss], ["kit.css", kitCss], ["app.css", appCss]]) {
    const below = pxFontSizes(css).filter((s) => s.px < FONT_FLOOR_PX).map((s) => s.text);
    assert.deepEqual(below, [], `${name} sets a px font size below --fs-1: use var(--fs-1) or larger`);
    const bad = fontSizeValues(css).map((s) => [s.text, floorViolation(s.value)]).filter(([, why]) => why);
    assert.deepEqual(bad, [], `${name} has font sizes that break the --fs-1 floor`);
  }
  // Not vacuous: the px scanner sees exactly app.css's pinned legacy sizes
  // (a migration that lowers the count must lower the pin with it), and the
  // value scanner sees those plus the token sizes.
  assert.equal(pxFontSizes(appCss).length, APP_CSS_RAW_FONT_SIZE_PIN, `app.css's raw px font sizes no longer equal the pin ${APP_CSS_RAW_FONT_SIZE_PIN}: lower the pin to the new count`);
  assert.ok(fontSizeValues(appCss).length > APP_CSS_RAW_FONT_SIZE_PIN, "the font-size value scanner misses app.css's token sizes");
  assert.ok(fontSizeValues(kitCss).length >= 100, "the font-size value scanner barely sees kit.css (font: shorthands)");
});

test("pxFontSizes: finds font-size (also .9px) and the shorthand size, ignores the shorthand line-height and tokens", () => {
  const found = pxFontSizes(
    ".a{font-size:10.5px}.b{font:700 9px/1.4 var(--ui)}.c{font:500 var(--fs-1)/8px var(--mono)}.d{--font: 3px}.e{font-size:var(--fs-1)}.f{font-size:.9px}",
  ).map((s) => s.px);
  assert.deepEqual(found, [10.5, 0.9, 9]);
});

test("floorViolation: px, .Npx, calc() and em/rem below the floor fail; tokens, keywords and calc() at or above it pass", () => {
  for (const ok of ["11px", "12.5px", "var(--fs-1)", "var(--fs-7)", "inherit", "calc(var(--fs-1) + 1px)", "calc(22px / 2)", "13px !important"]) {
    assert.equal(floorViolation(ok), null, ok);
  }
  for (const bad of ["10px", ".9px", "1em", "0.8rem", "1.2em", "calc(10px + 0px)", "calc(var(--fs-1) - 1px)", "calc(1em + 2px)", "calc(var(--fs-9))", "var(--fs-9)", "small", "80%", "var(--size)"]) {
    assert.ok(floorViolation(bad), `${bad} should break the floor`);
  }
  // The calc() evaluator: precedence, parentheses, unary minus; junk is NaN.
  assert.equal(evalArithmetic("11 + 2 * 3"), 17);
  assert.equal(evalArithmetic("(11 + 1) / 2"), 6);
  assert.equal(evalArithmetic("22 - -1"), 23);
  for (const junk of ["11 +", "(11", "11)", "1 2", "a", "", "alert(1)"]) assert.ok(Number.isNaN(evalArithmetic(junk)), junk);
  const values = fontSizeValues(".a{font:600 .9px/1 x}.b{font:500 var(--fs-1)/8px var(--mono)}.c{font:inherit}.d{font:italic 700 1.2em/1 sans}.e{font-size: calc(9px + 1px)}").map((s) => s.value);
  assert.deepEqual(values, ["calc(9px + 1px)", ".9px", "var(--fs-1)", "1.2em"]);
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
