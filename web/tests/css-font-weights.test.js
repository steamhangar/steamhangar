/**
 * WP WEIGHTS-1 (rc23, operator decision 2026-11-21): three font weights.
 *
 *   400 body text, 500 labels / buttons / values / section heads / emphasis,
 *   700 the primary button, page and sheet titles (DESIGN.md "Type").
 *
 * Every weight declared in theme.css, kit.css and app.css, as `font-weight`
 * or as the weight of a `font:` shorthand, is one of 400, 500, 700 (or a
 * CSS-wide keyword). The 21 rules at 600 moved to 500, the legacy
 * `.btn.primary` to 700 like the kit's `.btn.pri`. Keywords (`bold`,
 * `bolder`, `lighter`) are rejected too, so each weight has one spelling.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const repoDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const read = (...p) => readFileSync(path.join(repoDir, ...p), "utf8");
const stripComments = (css) => css.replace(/\/\*[\s\S]*?\*\//g, "");

const ALLOWED = new Set(["400", "500", "700"]);
const CSS_WIDE = /^(inherit|initial|unset|revert|revert-layer)$/i;

/** The weight of a `font:` shorthand value, or null when it sets none
 * (the weight is a leading token before the size; style and variant
 * keywords may precede it). */
function shorthandWeight(value) {
  for (const t of value.trim().split(/\s+/)) {
    if (/^\d+$/.test(t)) return t;
    if (/^(bold|bolder|lighter)$/i.test(t)) return t;
    if (/^(normal|italic|oblique|small-caps)$/i.test(t)) continue;
    break;
  }
  return null;
}

/** Every declared weight in a stylesheet: [{ text, weight }]. */
function declaredWeights(css) {
  const out = [];
  for (const m of css.matchAll(/(?:^|[;{\s])font-weight\s*:\s*([^;}]+)/gi)) {
    out.push({ text: m[0].trim(), weight: m[1].trim().replace(/\s*!important$/, "") });
  }
  for (const m of css.matchAll(/(?:^|[;{\s])font\s*:\s*([^;}]+)/gi)) {
    const w = shorthandWeight(m[1]);
    if (w !== null) out.push({ text: m[0].trim(), weight: w });
  }
  return out;
}

const sheets = {
  "theme.css": stripComments(read("web", "css", "theme.css")),
  "kit.css": stripComments(read("web", "css", "kit.css")),
  "app.css": stripComments(read("web", "css", "app.css")),
};

test("theme.css, kit.css and app.css declare only the weights 400, 500 and 700 (WEIGHTS-1)", () => {
  const seen = new Set();
  for (const [name, css] of Object.entries(sheets)) {
    const bad = declaredWeights(css)
      .filter(({ weight }) => !ALLOWED.has(weight) && !CSS_WIDE.test(weight))
      .map(({ text }) => text);
    assert.deepEqual(bad, [], `${name} declares a weight outside 400/500/700`);
    for (const { weight } of declaredWeights(css)) seen.add(weight);
  }
  // Not vacuous: the scanner sees all three weights in the real sheets.
  for (const w of ALLOWED) assert.ok(seen.has(w), `no ${w} weight found: the scanner misses declarations`);
});

test("the legacy primary button is 700 like the kit's .btn.pri; the legacy .btn is 500 like the kit's .btn", () => {
  assert.match(sheets["app.css"], /\.btn\.primary\{[^}]*font-weight:700/);
  assert.match(sheets["kit.css"], /\.kit \.btn\.pri\{[^}]*font-weight:700/);
  assert.match(sheets["app.css"], /(?:^|\n)\.btn\{[^}]*font-weight:500/);
  assert.match(sheets["kit.css"], /\.kit \.btn\{[^}]*font:500 /);
});

test("declaredWeights: finds font-weight and shorthand weights, skips shorthands without one", () => {
  const css = ".a{font-weight:600}.b{font:500 var(--fs-2)/1 var(--ui)}.c{font:var(--fs-1) var(--mono)}"
    + ".d{font:italic 700 1.2em/1 sans}.e{font:inherit}.f{ font-weight: bold !important; }.g{font:normal bolder 12px x}";
  assert.deepEqual(declaredWeights(css).map((d) => d.weight).sort(), ["500", "600", "700", "bold", "bolder"].sort());
});

test("declaredWeights: property names and keywords are matched case-insensitively (CSS is)", () => {
  const css = ".a{FONT-WEIGHT:600}.b{Font-Weight: 300}.c{FONT:600 12px/1 x}.d{font:ITALIC BOLD 12px x}";
  assert.deepEqual(declaredWeights(css).map((d) => d.weight).sort(), ["300", "600", "600", "BOLD"].sort());
  const bad = declaredWeights(css).filter(({ weight }) => !ALLOWED.has(weight) && !CSS_WIDE.test(weight));
  assert.equal(bad.length, 4, "an upper-case declaration slips past the 400/500/700 pin");
});
