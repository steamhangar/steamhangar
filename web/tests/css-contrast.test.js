/**
 * WP CONTRAST-1 (audit 2026-11-20: LINE-CONTRAST-1, GLYPH-CONTRAST-1).
 *
 *   (a) --line-strong reaches WCAG 1.4.11's 3:1 against the grounds a
 *       control sits on (--bg, --surface, --raised); --line does not, which
 *       is why controls identified by their border no longer use it;
 *   (b) every text input, select and fused sort / icon segment box takes
 *       its border from --line-strong (kit.css and the legacy app.css
 *       inputs still in use: onboarding, Clients rename, Library search);
 *   (c) no status disc overrides the shared dark ink: the grey --dim-2
 *       discs (cancelled, unknown, notinuse, unverified) draw their glyph
 *       in sic-ink (5.79:1), not --text (2.81:1);
 *   (d) on the --raised-2 Filters popover, where --line-strong is only
 *       2.72:1, the select and year field take --dim-2 (4.15:1);
 *   (e) the selected `.seg` / `.segi` segment, whose --raised-2 fill is only
 *       1.27:1 on --surface, carries a --dim-2 inset ring (5.25:1 on
 *       --surface, 4.15:1 on --raised-2) as its 3:1 state indicator, and so
 *       does the selected Add-a-device tab (`.ad-tabs`, fill 1.40:1 on --bg).
 *
 * The ratios are computed from docs/design/tokens.json with the WCAG 2.x
 * relative-luminance formula, so a token change that breaks a promise
 * fails here, not only in an audit.
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

const tokens = JSON.parse(read("docs", "design", "tokens.json"));
const themeCss = stripComments(read("web", "css", "theme.css"));
const kitCss = stripComments(read("web", "css", "kit.css"));
const appCss = stripComments(read("web", "css", "app.css"));

function luminance(hex) {
  const m = /^#([0-9A-F]{2})([0-9A-F]{2})([0-9A-F]{2})$/i.exec(hex);
  assert.ok(m, `not a #RRGGBB colour: ${hex}`);
  const [r, g, b] = m.slice(1).map((h) => {
    const c = parseInt(h, 16) / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function ratio(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

const round2 = (x) => Math.round(x * 100) / 100;
const color = (name) => {
  const hex = tokens.color[name];
  assert.ok(hex, `tokens.json color has no "${name}"`);
  return hex;
};

/** Body of the first rule whose selector list is exactly `selector`. */
function ruleBody(css, selector) {
  const esc = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const m = new RegExp(`(?:^|[}\\s])${esc}\\s*\\{([^}]*)\\}`).exec(css);
  return m ? m[1] : null;
}

test("--line-strong is 3:1 or more on --bg, --surface and --raised (WCAG 1.4.11); --line is not", () => {
  assert.equal(color("line-strong"), "#5F727E");
  const grounds = { bg: 3.82, surface: 3.45, raised: 3.13 };
  for (const [ground, expected] of Object.entries(grounds)) {
    const r = ratio(color("line-strong"), color(ground));
    assert.ok(r >= 3, `--line-strong on --${ground} is ${r.toFixed(2)}:1`);
    assert.equal(round2(r), expected, `--line-strong on --${ground}`);
    assert.ok(ratio(color("line"), color(ground)) < 3, `--line on --${ground} would pass on its own; re-check the audit premise`);
  }
});

test("the grey status discs: sic-ink on --dim-2 is 5.79:1, --text on --dim-2 only 2.81:1", () => {
  const ink = tokens.color_literal["sic-ink"];
  assert.equal(round2(ratio(ink, color("dim-2"))), 5.79);
  assert.equal(round2(ratio(color("text"), color("dim-2"))), 2.81);
  assert.ok(ratio(ink, color("dim-2")) >= 4.5);
});

test("inputs, selects and the sort / icon segment boxes take their border from --line-strong", () => {
  const strong = /border:\s*1px solid var\(--line-strong\)/;
  const kitRules = [".kit .in", ".kit .sortg", ".kit .segi"];
  for (const selector of kitRules) {
    const body = ruleBody(kitCss, selector);
    assert.ok(body !== null, `kit.css has no "${selector}{...}" rule`);
    assert.match(body, strong, `kit.css ${selector}`);
  }
  // Legacy app.css inputs that are still rendered: onboarding and the
  // Clients rename field (`.inp`, `input.inp.txt`), the Library search
  // box (`label.search.lib-search`).
  for (const selector of [".search", ".inp", "input.inp.txt"]) {
    const body = ruleBody(appCss, selector);
    assert.ok(body !== null, `app.css has no "${selector}{...}" rule`);
    assert.match(body, strong, `app.css ${selector}`);
  }
});

test("--line stays the separator and card line (unchanged token, still used)", () => {
  assert.equal(color("line"), "#24363F");
  assert.match(ruleBody(kitCss, ".kit .btn") ?? "", /border:\s*1px solid var\(--line\)/);
  // The divider inside the fused sort box is a separator, not the control edge.
  assert.match(ruleBody(kitCss, ".kit .sortg button") ?? "", /border-left:\s*1px solid var\(--line\)/);
});

test("no .sic.k-* rule overrides the shared dark glyph ink", () => {
  let checked = 0;
  for (const m of themeCss.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (!/\.sic\.k-[a-z]+/.test(m[1])) continue;
    checked += 1;
    assert.doesNotMatch(m[2], /(^|[;\s])color\s*:/, `theme.css "${m[1].trim()}" sets its own glyph colour`);
  }
  assert.ok(checked >= 13, `expected the .sic.k-* rules, found ${checked}`);
  for (const kind of ["cancelled", "unknown", "notinuse", "unverified"]) {
    assert.match(themeCss, new RegExp(`\\.sic\\.k-${kind}\\b[^{]*\\{\\s*background:var\\(--dim-2\\);\\s*\\}`), `.sic.k-${kind}`);
  }
});

test("Filters popover (--raised-2): select and year field borders are --dim-2, 3:1 on both sides", () => {
  assert.equal(round2(ratio(color("line-strong"), color("raised-2"))), 2.72);
  assert.equal(round2(ratio(color("dim-2"), color("raised-2"))), 4.15);
  assert.equal(round2(ratio(color("dim-2"), color("bg"))), 5.81);
  assert.match(ruleBody(appCss, ".pop") ?? "", /background:\s*var\(--raised-2\)/, "the popover ground changed; recheck the border token");
  assert.match(ruleBody(kitCss, ".kit .lib-meta select, .kit .lib-meta-year") ?? "", /border:\s*1px solid var\(--dim-2\)/);
});

test("the selected .seg / .segi segment has a --dim-2 inset ring (3:1 state indicator)", () => {
  assert.ok(ratio(color("raised-2"), color("surface")) < 3, "the fill alone would pass; recheck");
  assert.equal(round2(ratio(color("dim-2"), color("surface"))), 5.25);
  assert.ok(ratio(color("dim-2"), color("raised-2")) >= 3);
  for (const selector of ['.kit .seg button[aria-pressed="true"]', '.kit .segi button[aria-pressed="true"]']) {
    const body = ruleBody(kitCss, selector);
    assert.ok(body !== null, `kit.css has no "${selector}{...}" rule`);
    assert.match(body, /background:\s*var\(--raised-2\)/, selector);
    assert.match(body, /box-shadow:\s*inset 0 0 0 1px var\(--dim-2\)/, selector);
  }
});

test("the selected Add-a-device tab has the same --dim-2 inset ring (fill 1.40:1 on the --bg strip)", () => {
  assert.equal(round2(ratio(color("raised-2"), color("bg"))), 1.4);
  assert.equal(round2(ratio(color("dim-2"), color("bg"))), 5.81);
  assert.match(ruleBody(kitCss, ".kit .ad-tabs") ?? "", /background:\s*var\(--bg\)/, "the tab strip ground changed; recheck the ring");
  const body = ruleBody(kitCss, '.kit .ad-tabs .tab[aria-selected="true"]');
  assert.ok(body !== null, "kit.css has no selected .ad-tabs .tab rule");
  assert.match(body, /background:\s*var\(--raised-2\)/);
  assert.match(body, /box-shadow:\s*inset 0 0 0 1px var\(--dim-2\)/);
});
