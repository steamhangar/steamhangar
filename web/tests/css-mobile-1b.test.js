/**
 * WP MOBILE-1b (docs/design/rc21/mobile.html, operator decisions Q1-Q7 of
 * 2026-10-18): CSS and wiring pins for the phone fine-tuning. The looks are
 * proven by the 400px web-shot cases (tools/web-shot.mjs CASES); these pins
 * keep the load-bearing rules from silently disappearing.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const strip = (css) => css.replace(/\/\*[\s\S]*?\*\//g, "");
const read = (p) => readFileSync(new URL(p, import.meta.url), "utf8");
const kit = strip(read("../css/kit.css"));
const app = strip(read("../css/app.css"));
const theme = strip(read("../css/theme.css"));

/** Declarations of every rule whose selector list is exactly `sel`, joined. */
function rule(css, sel) {
  const esc = sel.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const all = [...css.matchAll(new RegExp(`(?:^|[}\\s])${esc}\\s*\\{([^}]*)\\}`, "g"))].map((m) => m[1]);
  return all.length ? all.join("\n") : null;
}

/** Body of the first `@media <query>{ ... }` block (brace-balanced). */
function mediaBlock(css, query) {
  const at = css.indexOf(`@media ${query}{`);
  if (at < 0) return null;
  let depth = 0;
  for (let i = css.indexOf("{", at); i < css.length; i += 1) {
    if (css[i] === "{") depth += 1;
    else if (css[i] === "}") {
      depth -= 1;
      if (depth === 0) return css.slice(css.indexOf("{", at) + 1, i);
    }
  }
  return null;
}

test("operator fix (sheet corners): the detail sheet's scrolling body takes the sheet's radii, so the square hero band no longer paints over the rounded top", () => {
  assert.match(rule(kit, ".kit.game-detail .body") ?? "", /border-radius:inherit/);
  // Not by clipping the sheet itself: that would cut the grab handle's hit
  // area above the edge (the X and the sticky tabs need no clip either).
  assert.equal(/overflow\s*:\s*(clip|hidden)/.test(rule(app, ".sheet") ?? ""), false, ".sheet does not clip");
  // The sheet is the body's parent and does round its top corners.
  assert.match(rule(app, ".sheet") ?? "", /border-top-left-radius:var\(--r-xl\); border-top-right-radius:var\(--r-xl\)/);
});

test("touch targets (Q1 Weg A): only under pointer:coarse, 38px visuals and a 44px ::after extender", () => {
  assert.match(theme, /--hit:44px; --ctl-h:38px;/);
  const coarse = mediaBlock(kit, "(pointer:coarse)");
  assert.ok(coarse, "kit.css has a pointer:coarse block");
  for (const sel of [".kit .seg button::after", ".kit .fchip::after", ".kit .btn::after", ".kit .ib::after", ".kit .stt .icnact::after",
    ".kit .gd-x::after", ".kit .segi button::after", ".kit .sortg button::after", ".kit .tab::after", ".kit .pair-open::after"]) {
    assert.ok(coarse.includes(sel), `extender for ${sel}`);
  }
  assert.match(coarse, /top:calc\(50% - var\(--hit\) \/ 2\); bottom:calc\(50% - var\(--hit\) \/ 2\);/);
  assert.match(coarse, /\.kit \.seg button, \.kit \.fchip, \.kit \.btn, \.kit \.btn\.sm, \.kit \.segi button\{ min-height:var\(--ctl-h\); \}/);
  // Scroll containers would clip an extender: the detail tabs are 44px
  // themselves, the chip row and the Settings strip get 3px of padding.
  assert.match(coarse, /\.kit\.game-detail \.tab\{ min-height:var\(--hit\); \}/);
  assert.match(coarse, /\.kit \.lib-chips\{ padding-block:3px; margin-block:-3px; \}/);
  assert.match(coarse, /\.kit\.view-settings \.snav-list\{ padding-top:3px; margin-top:-3px; \}/);
  // Outside the block no control grows: a mouse keeps the rc20 geometry.
  const outside = kit.replace(coarse, "");
  assert.equal(/min-height:var\(--hit\)/.test(outside), false);
});

test("overflow cue (Q2 Weg B): mask on .scue, state classes from lib/scroll-cue.js, no scroll-driven animation", () => {
  assert.match(rule(kit, ".kit .scue") ?? "", /mask-image:linear-gradient\(to right, transparent, var\(--bg\) var\(--cue-l\), var\(--bg\) calc\(100% - var\(--cue-r\)\), transparent\)/);
  assert.match(rule(kit, ".kit .scue") ?? "", /--cue-r:var\(--sp-7\)/);
  assert.match(rule(kit, ".kit .scue.fits") ?? "", /mask-image:none/);
  assert.equal(/animation-timeline|scroll-timeline/.test(kit), false);
  const users = {
    "../js/components/library-toolbar.js": /watchScrollCue\(chips\)/,
    "../js/components/range-segs.js": /watchScrollCue\(group\)/,
    "../js/components/settings-field.js": /watchScrollCue\(seg\)/,
    "../js/views/settings.js": /watchScrollCue\(list\)/,
    "../js/components/game-detail-sheet.js": /watchScrollCue\(tabs\.tablist\)/,
  };
  for (const [file, re] of Object.entries(users)) assert.match(read(file), re, file);
});

test("confirms (Q3): below 600px every .dialog is a bottom sheet with stacked full-width buttons, safe choice last", () => {
  const phone = mediaBlock(app, "(max-width:599px)");
  assert.ok(phone, "app.css has the 599px block");
  assert.match(phone, /\.dialog-backdrop\{ align-items:flex-end; padding:0; \}/);
  assert.match(phone, /border-radius:var\(--r-xl\) var\(--r-xl\) 0 0;/);
  assert.match(phone, /env\(safe-area-inset-bottom, 0px\)/);
  assert.match(phone, /\.dialog \.row\{ flex-direction:column-reverse; align-items:stretch;/);
  assert.match(phone, /\.dialog \.row \[data-role="rename-cancel"\]\{ order:-1; \}/);
  // column-reverse puts the FIRST button last: every confirm row starts
  // with its safe choice (which also takes the initial focus).
  const rows = [
    ["../js/components/delete-confirm.js", /row\.append\(keepBtn, deleteBtn\)/, /keepBtn\.focus\(\)/],
    ["../js/components/pair-confirm.js", /row\.append\(keep, replace\)/, /els\.keep\.focus\(\)/],
    ["../js/components/risky-confirm.js", /row\.append\(cancel, ok\)/, /els\.cancel\.focus\(\)/],
    ["../js/components/clients-sheet.js", /removeRow\.append\(removeNo, removeYes\)/, /removeNo\.focus\(\)/],
    ["../js/components/game-detail-sheet.js", /gcRow\.append\(gcNo, gcYes\)/, /gcNo\.focus\(\)/],
    ["../js/views/downloads.js", /bulkRow\.append\(bulkNo, bulkYes\)/, /bulkNo\.focus\(\)/],
    ["../js/views/downloads.js", /histRow\.append\(histNo, histYes\)/, /histNo\.focus\(\)/],
  ];
  for (const [file, order, focus] of rows) {
    const src = read(file);
    assert.match(src, order, `${file}: safe choice first in the row`);
    assert.match(src, focus, `${file}: safe choice focused`);
  }
});

test("bottom sheets: 88dvh, grab handle 44px on touch, sticky footer with the home-indicator inset", () => {
  assert.match(rule(app, ".sheet") ?? "", /max-height:88vh;[\s\S]*max-height:88dvh;/);
  const coarse = mediaBlock(app, "(pointer:coarse)");
  assert.match(coarse ?? "", /\.sheet \.grab::before\{ inset:-20px -60px -20px; \}/);
  assert.match(rule(app, ".sheet .sfoot") ?? "", /padding:var\(--sp-3\) var\(--sp-4\) calc\(var\(--sp-3\) \+ env\(safe-area-inset-bottom, 0px\)\)/);
  assert.match(app, /\.sheet \.sfoot\[hidden\]\{ display:none; \}/);
});

test("safe area and landscape: top inset, side insets, toast above the nav, landscape nav icon+label at 48px", () => {
  assert.match(app, /body\{ padding-top:env\(safe-area-inset-top, 0px\); \}/);
  assert.match(app, /\.topbar\{ top:env\(safe-area-inset-top, 0px\); \}/);
  assert.match(rule(app, ".view-root") ?? "", /padding-left:max\(var\(--gutter\), env\(safe-area-inset-left, 0px\)\);/);
  assert.match(theme, /\.toast\{\s*position:fixed; left:50%; bottom:calc\(var\(--nav-h\) \+ var\(--sp-4\) \+ env\(safe-area-inset-bottom, 0px\)\);/);
  const land = mediaBlock(app, "(orientation:landscape) and (max-height:500px) and (max-width:1023px)");
  assert.ok(land, "landscape block");
  assert.match(land, /:root\{ --nav-h:48px; \}/);
  assert.match(land, /\.nav-btn\{ flex-direction:row;/);
});

test("200 % text: single-column grids are minmax(0,1fr), so one wide row (Devices + range seg, the History head) cannot widen the page and clip the cards", () => {
  assert.match(rule(app, ".view-clients.kit") ?? "", /grid-template-columns:minmax\(0, 1fr\)/);
  assert.match(rule(kit, ".kit .ccard .z") ?? "", /grid-template-columns:minmax\(0, 1fr\)/);
  assert.match(rule(kit, ".kit .blk") ?? "", /grid-template-columns:minmax\(0, 1fr\)/);
  assert.match(rule(kit, ".kit .seg") ?? "", /min-width:0; overflow-x:auto;/, "the seg shrinks and scrolls with its cue");
});

test("200 % text: em minimums on the phone card stats and the tiles, nav label ellipsis", () => {
  assert.match(kit, /\.kit \.ccard \.stats\{ grid-template-columns:repeat\(auto-fit, minmax\(min\(100%, 6em\), 1fr\)\); \}/);
  assert.match(rule(kit, ".kit .tiles") ?? "", /minmax\(min\(100%, 8\.75em\), 1fr\)/);
  assert.match(rule(app, ".nav-btn .nav-lb") ?? "", /text-overflow:ellipsis; white-space:nowrap;/);
  assert.match(kit, /\.kit \.ccard \.upd \.btn\{[^}]*white-space:normal;/);
});

test("pairing (Q5 + operator change 2026-10-18): narrow touch only; the stand-in's reveal overlay replaces the Show toggle while hidden", () => {
  const coarse = mediaBlock(kit, "(pointer:coarse)");
  const narrow = coarse.slice(coarse.indexOf("@container addev (max-width:420px){"));
  assert.ok(narrow.length > 40, "addev 420px container block inside the coarse block");
  assert.match(narrow, /\.kit \.ad-open-here\{ display:flex; width:100%;/);
  assert.match(narrow, /\.kit \.ad-pairbox \.ad-qrreveal\{[^}]*width:200px; height:200px;/);
  assert.match(narrow, /\.kit \.ad-pairbox \.ad-qrreveal:focus-visible\{ outline:2px solid var\(--accent\);/);
  assert.match(narrow, /\.kit \.ad-pairbox \.ad-qrside > \.btn\[aria-expanded="false"\]\{ display:none; \}/);
  // Elsewhere none of it shows: desktop keeps the QR first and its button.
  assert.match(kit, /\.kit \.ad-open-here\{ display:none; \}/);
  assert.match(kit, /\.kit \.ad-qrreveal\{ display:none; \}/);
  assert.match(kit, /\.kit \.ad-pairbox \.ad-qrreveal\[hidden\]\{ display:none; \}|\.kit \.ad-pairbox \.ad-qrreveal\[hidden\]/);
});
