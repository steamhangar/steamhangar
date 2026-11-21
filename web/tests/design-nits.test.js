/**
 * Design nits of the 2026-11-20 audit (WP DESIGN-NITS-1, rc23): one pin per
 * finding, static analysis of the comment-stripped stylesheets plus the two
 * wording sources. Pixels are the goldens' job; these pins keep each fix
 * from drifting back in source.
 *
 *   D-5  app.css colours come from the tokens: no rgba()/hsl(), no hex but
 *        the sic-ink literal (the counter pips' ink); the dead `.toggle`
 *        and `.jobcard.active` rules are gone.
 *   D-8  every border-radius is a --r-* token (or a calc of one), a capsule
 *        (999px), a circle (50%), 0 or inherit: no px radius literal.
 *   D-9  no padding/margin/gap literal that equals a --sp-* token exactly,
 *        and the remaining px spacing literals only go down.
 *   D-10 every elevation shadow is --sh-1 or --sh-2 (rings drawn with
 *        box-shadow excepted); the tokens are pinned in
 *        design-tokens-contract.test.js.
 *   D-11 one press rule (scale .97, 100ms) on .btn, .fchip, .seg button
 *        and .card (not disabled / aria-disabled, a card not while its own
 *        .icnact is pressed), stopped under reduced motion.
 *   D-12 section labels are sentence case; the Downloads hero tiles, the
 *        Settings connection row and the Clients agent line are no boxes
 *        inside their card.
 *   D-13 banner actions are sentence case; the card badge and the detail
 *        note use the Library chip's wording "Installed, not cached".
 *   D-15 Library toolbar: search and tools on row 1, chips in their own
 *        row; DOM (= tab) order search, tools, chips.
 *   D-16 the onboarding progress fill scales (transform), never animates
 *        width, and stops under reduced motion.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

import { FILTER_DEFS } from "../js/lib/library-filters.js";
import { INSTALLED_BADGE, installedBadgeText } from "../js/lib/game-status.js";

const repoDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const read = (...p) => readFileSync(path.join(repoDir, ...p), "utf8");
const stripComments = (text) => text.replace(/\/\*[\s\S]*?\*\//g, "");

const tokens = JSON.parse(read("docs", "design", "tokens.json"));
const themeCss = stripComments(read("web", "css", "theme.css"));
const kitCss = stripComments(read("web", "css", "kit.css"));
const appCss = stripComments(read("web", "css", "app.css"));
const sheets = { "theme.css": themeCss, "kit.css": kitCss, "app.css": appCss };

/** Body of the FIRST `<selector>{...}` rule (exact selector text). */
function ruleBody(css, selector) {
  const esc = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const m = new RegExp(`(?:^|[}\\s])${esc}\\s*\\{([^}]*)\\}`).exec(css);
  return m ? m[1] : null;
}

/** Every value of `prop` in a stylesheet (declarations, not custom properties). */
function values(css, prop) {
  const out = [];
  for (const m of css.matchAll(new RegExp(`(?:^|[;{\\s])${prop}\\s*:\\s*([^;}]+)`, "g"))) out.push(m[1].trim());
  return out;
}

// ---------------------------------------------------------------------- D-5

test("D-5: app.css has no rgba()/hsl() colour and no hex colour but the sic-ink literal", () => {
  assert.doesNotMatch(appCss, /\b(?:rgba?|hsla?)\(/, "write token colours as var(--x) or color-mix(in srgb, var(--x) N%, transparent)");
  const hexes = [...appCss.matchAll(/#[0-9a-fA-F]{3,8}\b/g)].map((m) => m[0].toUpperCase());
  assert.ok(hexes.length > 0, "the counter pips' sic-ink literal is expected here; recheck the pin");
  for (const hex of hexes) assert.equal(hex, tokens.color_literal["sic-ink"], `app.css colour ${hex} is not a token`);
});

test("D-5: both counter pips are inked with sic-ink (the status-disc glyph ink)", () => {
  for (const selector of [".nav-pip", ".npip"]) {
    const body = ruleBody(appCss, selector) ?? "";
    const m = /(?:^|[;\s])color:\s*(#[0-9a-fA-F]{6})/.exec(body);
    assert.ok(m, `${selector} has no hex ink`);
    assert.equal(m[1].toUpperCase(), tokens.color_literal["sic-ink"], selector);
  }
});

test("D-5: the dead .toggle and .jobcard.active rules are gone", () => {
  assert.doesNotMatch(appCss, /(^|[}\s,])\.toggle\b/);
  assert.doesNotMatch(appCss, /\.jobcard\.active\b/);
});

// ---------------------------------------------------------------------- D-8

test("D-8: no px border-radius literal; radii are --r-* tokens, capsules (999px) or circles", () => {
  const allowed = /^(?:var\(--r-(?:s|m|l|xl)\)|calc\(var\(--r-(?:s|m|l|xl)\) [-+/*] \d+(?:px)?\)|999px|50%|0|inherit)$/;
  for (const [name, css] of Object.entries(sheets)) {
    for (const prop of ["border-radius", "border-(?:top|bottom)-(?:left|right)-radius"]) {
      for (const value of values(css, prop)) {
        for (const part of value.split(/\s+(?![^()]*\))/)) {
          assert.match(part, allowed, `${name}: ${prop.replace(/\(.*\)/, "*")}:${value}`);
        }
      }
    }
  }
});

// ---------------------------------------------------------------------- D-9

const SPACING = /(?:^|[;{\s])((?:padding|margin)(?:-(?:top|right|bottom|left|inline|block)(?:-(?:start|end))?)?|gap|row-gap|column-gap)\s*:\s*([^;}]+)/g;
function spacingLiterals(css) {
  const out = [];
  for (const m of css.matchAll(SPACING)) {
    // Negative literals count too (a pull-back margin is spacing); only a
    // positive value can equal a --sp-* token.
    for (const v of m[2].matchAll(/(^|[^\w.])(-?\d*\.?\d+)px\b/g)) out.push({ prop: m[1], value: Number(v[2]), decl: m[0].trim() });
  }
  return out;
}

test("D-9: no padding/margin/gap px literal equals a --sp-* token (use the token)", () => {
  const onScale = new Set(Object.values(tokens.sp));
  for (const name of ["kit.css", "app.css"]) {
    const hits = spacingLiterals(sheets[name]).filter((l) => onScale.has(l.value));
    assert.deepEqual(hits.map((l) => l.decl), [], `${name}: write these as var(--sp-N)`);
  }
});

/**
 * Remaining px spacing literals (none equal to a token; negatives
 * included), measured comment-stripped on 2026-11-21 (DESIGN-NITS-1; 233
 * in kit.css and 263 in app.css before, the audit counted kit 233, app 274). THESE NUMBERS ONLY GO DOWN: a package that touches a rule
 * moves its spacing onto --sp-* and lowers the pin; never raise it.
 */
const SPACING_LITERAL_PIN = { "kit.css": 157, "app.css": 186 };
test("D-9: the remaining px spacing literals only go down (pinned counts)", () => {
  for (const [name, pin] of Object.entries(SPACING_LITERAL_PIN)) {
    const count = spacingLiterals(sheets[name]).length;
    assert.ok(count <= pin, `${name} has ${count} px spacing literals, the pin is ${pin}: use --sp-* in new rules`);
  }
});

// --------------------------------------------------------------------- D-10

test("D-10: every elevation shadow is var(--sh-1) or var(--sh-2); only rings stay literal", () => {
  // A ring is ONE shadow with no offset and no blur (or the tab strip's
  // 1px inset underline); nested parentheses are flattened before the
  // comma check, so a colour-mix() ink does not read as a second shadow.
  const ring = /^(?:inset )?0 0 0 \d+px [^,]+$|^inset 0 -1px 0 [^,]+$/;
  const flatten = (v) => { let out = v; while (/\([^()]*\)/.test(out)) out = out.replace(/\([^()]*\)/g, "_"); return out; };
  for (const [name, css] of Object.entries(sheets)) {
    const all = values(css, "box-shadow");
    assert.ok(all.length > 0, name);
    for (const value of all) {
      assert.ok(value === "none" || /^var\(--sh-[12]\)$/.test(value) || ring.test(flatten(value)), `${name}: box-shadow:${value}`);
    }
  }
  assert.ok(values(appCss, "box-shadow").includes("var(--sh-2)"), "dialogs and sheets use --sh-2");
  assert.ok(values(kitCss, "box-shadow").includes("var(--sh-1)"), "menus and popovers use --sh-1");
});

// --------------------------------------------------------------------- D-11

test("D-11: one press rule, scale(.97) over 100ms, on .btn, .fchip, .seg button and .card", () => {
  const G = String.raw`:not\(:disabled\):not\(\[aria-disabled="true"\]\)`;
  const press = new RegExp(String.raw`\.kit \.btn:active${G},\s*\.kit \.fchip:active${G},\s*\.kit \.seg button:active${G},\s*\.kit \.card:active:not\(:has\(\.icnact:active\)\)\{ transform:scale\(\.97\); \}`);
  assert.match(kitCss, press);
  assert.match(ruleBody(kitCss, ".kit .btn") ?? "", /font:/, "sanity: the first .kit .btn rule is the button rule");
  assert.match(kitCss, /\.kit \.btn\{ transition:transform 100ms ease-out, filter \.14s, background \.14s; \}/);
  assert.match(kitCss, /\.kit \.fchip, \.kit \.seg button, \.kit \.card\{ transition:transform 100ms ease-out; \}/);
  const reduced = [...kitCss.matchAll(/@media \(prefers-reduced-motion:reduce\)\{([\s\S]*?)\n\}/g)].map((m) => m[1]).join("\n");
  assert.match(reduced, /\.kit \.card:active:not\(:has\(\.icnact:active\)\)\{ transform:none; \}/, "reduced motion keeps a pressed control still");
});

// --------------------------------------------------------------------- D-12

test("D-12: section and key labels are sentence case (no uppercase transform)", () => {
  const labels = {
    "kit.css": [".kit .sec", ".kit .tile .k", ".kit .fcard > h3", ".kit .ccard .stats .k", ".kit .live .lnow .lk", ".kit .gd-fact .k"],
    "app.css": ["h4.sec", ".cflabel", ".field label", ".stepnum", ".olabel"],
  };
  for (const [name, selectors] of Object.entries(labels)) {
    for (const selector of selectors) {
      const body = ruleBody(sheets[name], selector);
      assert.ok(body !== null, `${name} has no "${selector}{...}" rule`);
      assert.doesNotMatch(body, /text-transform:\s*uppercase/, `${name} ${selector}`);
      assert.doesNotMatch(body, /letter-spacing:\s*\.\d/, `${name} ${selector}: no tracking on a sentence-case label`);
    }
  }
});

test("D-12: one separator per level -- hero tiles, the connection row and the agent line are no boxes", () => {
  const tile = ruleBody(kitCss, ".kit .hero .tile") ?? "";
  assert.match(tile, /background:none/);
  assert.match(tile, /border:0/);
  assert.match(ruleBody(kitCss, ".kit .hero .tiles") ?? "", /border-left:1px solid var\(--line-soft\)/, "one hairline sets the tiles off");
  for (const selector of [".kit.view-settings .act", ".kit .ccard .agent"]) {
    const body = ruleBody(kitCss, selector) ?? "";
    assert.ok(body, selector);
    assert.doesNotMatch(body, /background:|border-radius:|(?:^|[;\s])border:/, `${selector} is a row of its card, not a card`);
  }
});

// --------------------------------------------------------------------- D-13

test("D-13: banner actions are sentence case", () => {
  const body = ruleBody(appCss, ".banner .acts button") ?? "";
  assert.ok(body);
  assert.doesNotMatch(body, /text-transform:\s*uppercase/);
});

test("D-13: card badge and detail note use the Library chip's wording", () => {
  const chip = FILTER_DEFS.find((f) => f.key === "installed");
  assert.ok(chip, "no 'installed' chip");
  assert.equal(chip.label, "Installed, not cached");
  assert.ok(installedBadgeText(INSTALLED_BADGE.NOT_CACHED, "pc").startsWith(`${chip.label} · `));
  const sheet = read("web", "js", "components", "game-detail-sheet.js");
  assert.match(sheet, /note\.textContent = "Installed, not cached — /);
  for (const file of ["web/js/lib/game-status.js", "web/js/components/game-detail-sheet.js", "web/js/components/game-card.js"]) {
    const code = read(...file.split("/")).replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/[^\n]*/g, "$1");
    assert.doesNotMatch(code, /Installed but not cached/, file);
  }
});

// --------------------------------------------------------------------- D-15

test("D-15: Library toolbar -- search and tools on row 1, the chips in their own full-width row, DOM = tab order", () => {
  assert.match(ruleBody(kitCss, ".kit .lib-chips") ?? "", /flex:1 1 100%;/);
  assert.doesNotMatch(kitCss, /\.lib-filters\b/);
  const toolbar = read("web", "js", "components", "library-toolbar.js");
  assert.match(toolbar, /bar\.append\(searchWrap, tools, chips\);/, "visual order search, tools, chips is also the DOM (tab) order");
});

// --------------------------------------------------------------------- D-16

test("D-16: the onboarding progress fill scales from the left instead of animating width", () => {
  const fill = ruleBody(appCss, ".track i") ?? "";
  assert.match(fill, /transform-origin:left center/);
  assert.match(fill, /transition:transform \.3s ease/);
  assert.doesNotMatch(fill, /transition:[^;]*width/);
  assert.doesNotMatch(appCss, /transition:\s*width/);
  const onboarding = read("web", "js", "onboarding.js");
  assert.match(onboarding, /els\.track\.style\.transform = `scaleX\(\$\{progressPercent\(state\.step\) \/ 100\}\)`;/);
  assert.doesNotMatch(onboarding, /els\.track\.style\.width/);
  assert.match(appCss, /@media \(prefers-reduced-motion: reduce\)\{\s*\.track i\{ transition:none; \}\s*\}/);
});
