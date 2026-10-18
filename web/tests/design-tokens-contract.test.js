/**
 * Shared design-token contract, web side (WP TOKENS-SYNC-1, ADR-0028).
 *
 * docs/design/tokens.json and docs/design/status-vocabulary.json are the
 * one source both frontends are checked against; the Android twin is
 * app/app/src/test/.../ui/theme/DesignTokensContractTest.kt and
 * .../ui/status/StatusVocabularyContractTest.kt. No generator: this test
 * IS the generator, so a value changed in theme.css or status-icon.js
 * without the JSON (or the other way round) fails here.
 *
 *   (a) every colour in tokens.json equals theme.css, and theme.css has no
 *       colour custom property the JSON lacks (both directions);
 *   (b) --fs-N, --sp-N, --r-X equal the JSON's fs/sp/r, both directions;
 *   (c) status-vocabulary.json equals STATUS_LABEL (kinds, order, words),
 *       KIND_GLYPH, DEFAULT_PILL_TONE, and the animated set equals the
 *       kinds that have a `.sic.k-<kind>` animation rule in theme.css.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

import { STATUS_LABEL } from "../js/components/status-icon.js";
import { DEFAULT_PILL_TONE } from "../js/components/pill.js";

const repoDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const read = (...p) => readFileSync(path.join(repoDir, ...p), "utf8");
const stripComments = (text) => text.replace(/\/\*[\s\S]*?\*\//g, "");

const tokens = JSON.parse(read("docs", "design", "tokens.json"));
const vocabulary = JSON.parse(read("docs", "design", "status-vocabulary.json"));
const themeCss = stripComments(read("web", "css", "theme.css"));
const statusIconJs = read("web", "js", "components", "status-icon.js");

/** Every `--name:value;` in theme.css, as name -> list of values (a token may be redefined). */
function customProperties(css) {
  const props = new Map();
  for (const m of css.matchAll(/--([a-z0-9-]+)\s*:\s*([^;}]+)[;}]/g)) {
    const list = props.get(m[1]) ?? [];
    list.push(m[2].trim());
    props.set(m[1], list);
  }
  return props;
}

const props = customProperties(themeCss);
const isColour = (value) => /^#[0-9a-f]{3,8}$/i.test(value) || /^(rgba?|hsla?|color-mix)\(/i.test(value);

function single(name) {
  const values = props.get(name);
  assert.ok(values, `theme.css has no --${name}`);
  assert.equal(values.length, 1, `--${name} is defined ${values.length} times in theme.css; the contract expects one`);
  return values[0];
}

function px(value) {
  const m = /^(\d+(?:\.\d+)?)px$/.exec(value);
  assert.ok(m, `expected a px value, got "${value}"`);
  return Number(m[1]);
}

test("tokens.json: opaque colours are uppercase 6-digit hex", () => {
  for (const [name, hex] of Object.entries(tokens.color)) {
    assert.match(hex, /^#[0-9A-F]{6}$/, `color.${name}`);
  }
  for (const [name, entry] of Object.entries(tokens.color_alpha)) {
    assert.match(entry.rgb, /^#[0-9A-F]{6}$/, `color_alpha.${name}.rgb`);
    assert.ok(entry.alpha > 0 && entry.alpha < 1, `color_alpha.${name}.alpha`);
  }
  assert.equal(tokens.color["dim-2"], "#7A91A0");
});

test("tokens.json colours equal theme.css, and theme.css has no colour token the JSON lacks", () => {
  for (const [name, hex] of Object.entries(tokens.color)) {
    assert.equal(single(name).toUpperCase(), hex, `--${name}`);
  }
  for (const [name, entry] of Object.entries(tokens.color_alpha)) {
    const m = /^rgba\((\d+),(\d+),(\d+),\s*([\d.]+)\)$/.exec(single(name).replace(/\s+/g, ""));
    assert.ok(m, `--${name} is not an rgba() value`);
    const hex = "#" + [m[1], m[2], m[3]].map((c) => Number(c).toString(16).padStart(2, "0")).join("").toUpperCase();
    assert.equal(hex, entry.rgb, `--${name} rgb`);
    assert.equal(Number(m[4]), entry.alpha, `--${name} alpha`);
  }
  const cssColourTokens = [...props].filter(([, values]) => values.some(isColour)).map(([name]) => name).sort();
  const jsonColourTokens = [...Object.keys(tokens.color), ...Object.keys(tokens.color_alpha)].sort();
  assert.deepEqual(cssColourTokens, jsonColourTokens);
});

test("tokens.json color_literal sic-ink is the .sic ink in theme.css", () => {
  const m = /\.sic\s*\{[^}]*?[;\s{]color:\s*(#[0-9a-fA-F]{6})\s*;/.exec(themeCss);
  assert.ok(m, "no `.sic{ ... color:#RRGGBB; }` rule in theme.css");
  assert.equal(m[1].toUpperCase(), tokens.color_literal["sic-ink"]);
  assert.deepEqual(Object.keys(tokens.color_literal), ["sic-ink"]);
});

for (const [group, prefix] of [["fs", "fs-"], ["sp", "sp-"], ["r", "r-"]]) {
  test(`tokens.json ${group} equals theme.css --${prefix}*, both directions`, () => {
    for (const [key, value] of Object.entries(tokens[group])) {
      assert.equal(px(single(prefix + key)), value, `--${prefix}${key}`);
    }
    const cssKeys = [...props.keys()]
      .filter((name) => name.startsWith(prefix) && /^[a-z0-9]+$/.test(name.slice(prefix.length)))
      .map((name) => name.slice(prefix.length))
      .sort();
    assert.deepEqual(cssKeys, Object.keys(tokens[group]).sort());
  });
}

// WP MOBILE-1b: the web-only touch-target sizes. Android's
// DesignTokensContractTest reads only the colour groups, so this group
// needs no Kotlin twin (Compose sizes its own touch targets).
test("tokens.json size (web-only) equals theme.css --hit / --ctl-h, both directions", () => {
  assert.deepEqual(tokens.size, { hit: 44, "ctl-h": 38 });
  for (const [key, value] of Object.entries(tokens.size)) {
    assert.equal(px(single(key)), value, `--${key}`);
  }
  assert.ok(px(single("hit")) > px(single("ctl-h")), "the hit area is larger than the visual control");
});

test("tokens.json literal scale values (kit.html)", () => {
  assert.deepEqual(Object.values(tokens.fs), [11, 12, 13, 15, 18, 24, 32]);
  assert.deepEqual(Object.values(tokens.sp), [4, 8, 12, 16, 20, 24, 32, 48]);
});

/** KIND_GLYPH is module-private: read it from the source. */
function kindGlyph() {
  const body = /const KIND_GLYPH = \{([\s\S]*?)\};/.exec(statusIconJs);
  assert.ok(body, "GRAMMAR DRIFT: no `const KIND_GLYPH = { ... };` in status-icon.js -- widen this regex");
  const out = {};
  for (const m of body[1].matchAll(/^\s*([a-z]+):\s*"([a-z]+)",?\s*$/gm)) out[m[1]] = m[2];
  return out;
}

/** Kinds with a `.sic.k-<kind>` selector on a rule that sets an animation. */
function animatedKinds() {
  const kinds = new Set();
  for (const m of themeCss.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (!/(^|[;\s])animation\s*:/.test(m[2])) continue;
    for (const k of m[1].matchAll(/\.sic\.k-([a-z]+)/g)) kinds.add(k[1]);
  }
  return [...kinds].sort();
}

test("status-vocabulary.json kinds and words equal STATUS_LABEL, in order", () => {
  assert.deepEqual(vocabulary.map((e) => e.kind), Object.keys(STATUS_LABEL));
  for (const entry of vocabulary) {
    assert.deepEqual(Object.keys(entry).sort(), ["animated", "glyph", "kind", "tone", "word"], entry.kind);
    assert.equal(entry.word, STATUS_LABEL[entry.kind], `word of ${entry.kind}`);
  }
});

test("status-vocabulary.json glyphs equal KIND_GLYPH", () => {
  const glyphs = kindGlyph();
  assert.deepEqual(Object.keys(glyphs).sort(), vocabulary.map((e) => e.kind).sort());
  for (const entry of vocabulary) assert.equal(entry.glyph, glyphs[entry.kind], `glyph of ${entry.kind}`);
});

test("status-vocabulary.json tones equal DEFAULT_PILL_TONE", () => {
  assert.deepEqual(Object.keys(DEFAULT_PILL_TONE).sort(), vocabulary.map((e) => e.kind).sort());
  for (const entry of vocabulary) assert.equal(entry.tone, DEFAULT_PILL_TONE[entry.kind], `tone of ${entry.kind}`);
});

test("status-vocabulary.json animated set equals theme.css's .sic.k-* animation rules", () => {
  const css = animatedKinds();
  assert.deepEqual(css, ["running", "updating", "verify"]);
  assert.deepEqual(vocabulary.filter((e) => e.animated).map((e) => e.kind).sort(), css);
  for (const entry of vocabulary) assert.equal(typeof entry.animated, "boolean", entry.kind);
});
