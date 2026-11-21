/**
 * WP ANDROID-SHEET-BG-1 (operator decision): a pressed filter chip's count
 * is --accent in every chip row, the same as the app (Android
 * `vaultFilterChipColors`, pinned in app WebSurfaceTwinTest). Before, only
 * the Library chip row (`.kit .lib-chips`) turned its count accent; the
 * general rule replaces that one.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const kitCss = readFileSync(path.join(here, "..", "css", "kit.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
const kitHtml = readFileSync(path.join(here, "..", "..", "docs", "design", "rc20", "kit.html"), "utf8");

function ruleBody(css, selector) {
  const esc = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const m = new RegExp(`(?:^|[}\\s])${esc}\\s*\\{([^}]*)\\}`).exec(css);
  return m ? m[1] : null;
}

test("a pressed filter chip's count is accent in every chip row", () => {
  const body = ruleBody(kitCss, '.kit .fchip[aria-pressed="true"] b');
  assert.ok(body !== null, 'kit.css has no `.kit .fchip[aria-pressed="true"] b` rule');
  assert.match(body, /(?:^|;|\s)color\s*:\s*var\(--accent\)\s*;?/);
  // The general rule replaced the Library-only one; two rules would drift apart.
  assert.equal(ruleBody(kitCss, '.kit .lib-chips .fchip[aria-pressed="true"] b'), null);
});

test("the rc20 kit mirror carries the same pressed count colour", () => {
  assert.match(kitHtml, /\.fchip\[aria-pressed=true\] b\{color:var\(--accent\)\}/);
});
