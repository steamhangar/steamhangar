/**
 * Pins for the Settings layout CSS (WP SETTINGS-1; before it the section
 * border pins of UI-EXPERT-COLLAPSED). No browser here (same posture as
 * css-settings-save-bar.test.js): the layout rules are pinned as text.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const css = readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "css", "app.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

const rule = (selector) => {
  const esc = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const m = new RegExp(`(?:^|\\})\\s*${esc}\\s*\\{([^{}]*)\\}`).exec(css);
  return m ? m[1] : null;
};

test("the old collapsible <details> rules are gone", () => {
  assert.equal(/\.settings-sec\b/.test(css), false);
  assert.equal(/\.sec-summary\b/.test(css), false);
});

test("MUTATION TARGET: the layout is one column that becomes nav + panel from 1000px", () => {
  assert.match(rule(".settings-lay") ?? "", /grid-template-columns:\s*minmax\(0,\s*1fr\)/);
  const m = /@media\s*\(min-width:\s*1000px\)\s*\{([\s\S]*?\n\})/.exec(css);
  assert.ok(m, "the 1000px media block is missing");
  assert.match(m[1], /\.settings-lay\s*\{[^}]*grid-template-columns:\s*240px minmax\(0,\s*1fr\)/);
  assert.match(m[1], /\.snav\s*\{[^}]*position:\s*sticky/);
});

test("the nav strip scrolls inside itself below 1000px and never widens the page", () => {
  assert.match(rule(".snav-list") ?? "", /overflow-x:\s*auto/);
  assert.match(rule(".snav") ?? "", /min-width:\s*0/);
  assert.match(rule(".settings-main") ?? "", /min-width:\s*0/);
});

test("the field grid is auto-fit with a 300px floor and never overflows a narrow panel", () => {
  assert.match(rule(".fg") ?? "", /repeat\(auto-fit,\s*minmax\(min\(100%,\s*300px\),\s*1fr\)\)/);
  assert.match(rule(".fg > .field") ?? "", /min-width:\s*0/);
});

test("chips are styled per kind from tokens; display-styled toggled classes carry [hidden] guards", () => {
  assert.ok(rule(".chip"));
  assert.match(rule(".chip-changed") ?? "", /var\(--accent\)/);
  assert.ok(rule(".chip-env"));
  assert.match(rule(".snav-item[hidden]") ?? "", /display:\s*none/);
  assert.match(rule(".settings-warn[hidden]") ?? "", /display:\s*none/);
  assert.match(rule(".snav-item[aria-current=\"true\"]") ?? "", /var\(--accent\)/);
});
