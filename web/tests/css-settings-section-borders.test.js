/**
 * Pins for the Settings section borders (UI-EXPERT-COLLAPSED). No browser
 * here (same posture as css-settings-save-bar.test.js): the closing border
 * must not depend on :last-child, because the save bar and the hidden
 * Expert section sit after About.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const css = readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "css", "app.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

test("MUTATION TARGET: no :last-child closing rule on .settings-sec; About and Expert carry their own bottom border", () => {
  assert.equal(/\.settings-sec:last-child/.test(css), false, "the border must not hinge on :last-child");
  const m = /\.settings-sec\[data-section="about"\]\s*,\s*\.settings-sec\[data-section="expert"\]\s*\{([^{}]*)\}/.exec(css);
  assert.ok(m, "About/Expert closing-border rule missing");
  assert.match(m[1], /border-bottom\s*:\s*1px solid var\(--line-soft\)/);
});
