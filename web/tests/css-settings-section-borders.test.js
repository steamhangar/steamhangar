/**
 * Pins for the Settings layout CSS (WP SETTINGS-2, kit.css "Settings page";
 * before it WP SETTINGS-1 in app.css, and the section border pins of
 * UI-EXPERT-COLLAPSED). No browser here (same posture as
 * css-settings-save-bar.test.js): the layout rules are pinned as text; the
 * screenshots in the package report are the visual proof.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const webDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const strip = (css) => css.replace(/\/\*[\s\S]*?\*\//g, "");
const kit = strip(readFileSync(path.join(webDir, "css", "kit.css"), "utf8"));
const app = strip(readFileSync(path.join(webDir, "css", "app.css"), "utf8"));
const settingsJs = readFileSync(path.join(webDir, "js", "views", "settings.js"), "utf8");

const rule = (css, selector) => {
  const esc = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const m = new RegExp(`(?:^|\\})\\s*${esc}\\s*\\{([^{}]*)\\}`).exec(css);
  return m ? m[1] : null;
};
const S = ".kit.view-settings";
/** The body of `@container settings (max-width: Npx){...}`. */
const containerBlock = (px) => {
  const m = new RegExp(`@container settings \\(max-width: ${px}px\\)\\s*\\{([\\s\\S]*?)\\n\\}`).exec(kit);
  return m ? m[1] : null;
};
const guarded = (selector) =>
  [...kit.matchAll(/(^|\})\s*([^{}]+)\{([^{}]*)\}/g)].some(
    (m) => m[2].split(",").map((x) => x.trim().replace(/\s+/g, " ")).includes(selector) && /display:\s*none/.test(m[3]),
  );

test("the old app.css Settings layout rules are gone (no second copy fighting the kit)", () => {
  for (const sel of [".settings-lay", ".snav-item", ".sec-body", ".settings-warn", ".field-head", ".sec-dirty"]) {
    assert.equal(new RegExp(`(^|[\\s,}])\\${sel}\\b`).test(app), false, `${sel} still styled in app.css`);
  }
  assert.equal(/\.settings-sec\b/.test(app), false);
});

test("MUTATION TARGET: the page root is the `settings` container; nav + panel side by side, one column at 760px or less", () => {
  assert.match(rule(kit, S) ?? "", /container:\s*settings \/ inline-size/);
  assert.match(rule(kit, `${S} .settings-lay`) ?? "", /grid-template-columns:\s*240px minmax\(0, 1fr\)/);
  const narrow = containerBlock(760);
  assert.ok(narrow, "the 760px container block is missing");
  assert.match(narrow, /\.settings-lay\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\)/);
  assert.match(narrow, /\.snav-list\s*\{[^}]*flex-direction:\s*row;[^}]*overflow-x:\s*auto/);
  assert.match(narrow, /\.snav-state\s*\{[^}]*display:\s*none/);
  assert.match(rule(kit, `${S} .snav`) ?? "", /position:\s*sticky/);
});

test("the JS compact threshold is the CSS container threshold (the expert switch moves exactly when the nav becomes a strip)", () => {
  assert.match(settingsJs, /export const COMPACT_MAX_WIDTH = 760;/);
  assert.ok(containerBlock(760));
});

test("nav items are flat: no border, accent glow when current, state pushed right", () => {
  const item = rule(kit, `${S} .snav-item`) ?? "";
  assert.match(item, /border:\s*0/);
  assert.match(item, /font:\s*400 var\(--fs-3\)/);
  assert.match(rule(kit, `${S} .snav-item[aria-current="true"]`) ?? "", /background:\s*var\(--accent-glow\)/);
  assert.match(rule(kit, `${S} .snav-state`) ?? "", /margin-left:\s*auto/);
});

test("webhook events are an auto-fill grid; the GC warning is a tinted box from tokens", () => {
  assert.match(rule(kit, `${S} .checks`) ?? "", /repeat\(auto-fill, minmax\(min\(100%, 190px\), 1fr\)\)/);
  assert.match(rule(kit, `${S} .warn`) ?? "", /color-mix\(in srgb, var\(--run\)/);
});

test("MUTATION TARGET: display-styled, hidden-toggled Settings classes carry [hidden] guards", () => {
  for (const sel of [
    `${S} .snav-item[hidden]`,
    `${S} .settings-panel[hidden]`,
    `${S} .dot[hidden]`,
    `${S} .warn[hidden]`,
    `${S} .lookup[hidden]`,
    ".kit .fhelp[hidden]",
  ]) {
    assert.equal(guarded(sel), true, `${sel} has no display:none guard`);
  }
});
