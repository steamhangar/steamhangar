/**
 * app.js must never read `window.localStorage` unguarded at module top level
 * (WEB-FIX-5 review should-fix). With site data blocked, the property getter
 * itself throws a SecurityError; an unguarded read in an argument position
 * (`storage: window.localStorage`) stops app.js before the first paint and
 * leaves the page blank. Allowed forms: a lazy getter (`() =>
 * window.localStorage`, read later inside the callee's try/catch) or a read
 * inside a `try` block.
 *
 * Comment-stripped source scan, same idiom as connection-banner.test.js.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, "..", "js", "app.js"), "utf8")
  .replace(/\/\*[\s\S]*?\*\//g, "")
  .replace(/(^|[^:"'`])\/\/[^\n]*/g, "$1");

test("app.js: no bare window.localStorage in an argument/property position", () => {
  const bare = src.match(/[:(,]\s*window\.localStorage\b/g) || [];
  assert.deepEqual(bare, [], "wrap it: () => window.localStorage, or a try/catch IIFE");
});

test("app.js: every window.localStorage read is a lazy getter or sits inside a try block", () => {
  const offenders = [];
  for (const m of src.matchAll(/window\.localStorage\b/g)) {
    const before = src.slice(Math.max(0, m.index - 40), m.index);
    const lazy = /\(\)\s*=>\s*$/.test(before);
    const inTry = /try\s*\{\s*return\s*$/.test(before);
    if (!lazy && !inTry) offenders.push(src.slice(Math.max(0, m.index - 30), m.index + 20).replace(/\s+/g, " "));
  }
  assert.deepEqual(offenders, []);
});

test("app.js: the decision panel gets the guarded storage (null on failure)", () => {
  assert.match(
    src,
    /storage: \(\(\) => \{\s*try \{\s*return window\.localStorage;\s*\} catch \{\s*return null;\s*\}\s*\}\)\(\),/,
  );
});
