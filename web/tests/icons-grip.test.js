// WP GOLDENS-LOCAL-1: the grip is one shared SVG, never the text glyph
// U+22EE, which no Liberation font has (the screenshot goldens would show a
// missing-glyph box, other systems a random fallback font).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { GRIP_SVG } from "../js/lib/icons.js";

const WEB = join(dirname(fileURLToPath(import.meta.url)), "..");
const read = (p) => readFileSync(join(WEB, p), "utf8");

test("GRIP_SVG is a six-dot SVG using currentColor", () => {
  assert.match(GRIP_SVG, /^<svg [^>]*fill="currentColor"[^>]*aria-hidden="true">/);
  assert.equal((GRIP_SVG.match(/<circle /g) ?? []).length, 6);
});

test("kit and downloads both import the shared grip; no U+22EE in rendered code", () => {
  for (const view of ["js/views/kit.js", "js/views/downloads.js"]) {
    const src = read(view);
    assert.match(src, /import \{[^}]*\bGRIP_SVG\b[^}]*\} from "\.\.\/lib\/icons\.js";/, view);
    assert.doesNotMatch(src, /const GRIP_SVG\b/, `${view} keeps no copy of its own`);
    assert.doesNotMatch(src, /⋮/, `${view} renders no U+22EE text glyph`);
  }
  assert.doesNotMatch(read("index.html"), /⋮/);
});
