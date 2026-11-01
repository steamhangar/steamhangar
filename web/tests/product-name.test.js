/**
 * WP RUNNER-NAME-1: the labels that name the server's own prefill downloads
 * read the product's name from ONE constant (`lib/product.js`), so the
 * rename to GameHangar (roadmap D18) changes them by changing only that
 * constant. The behaviour pins (the rows, mini line, tile details and the
 * Clients card title read "SteamHangar") live in now-loading, runner-split,
 * downloads-components, traffic-view, clients-page and clients-view-wiring;
 * this file pins the constant and that no labelled source spells the name
 * or the old "prefill runner" label itself.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { PRODUCT_NAME } from "../js/lib/product.js";

const LABEL_SOURCES = ["components/downloads-hero.js", "lib/traffic-view.js", "lib/clients-page.js"];

/** Source without block and line comments (doc comments name the old label on purpose). */
function code(path) {
  const src = readFileSync(new URL(`../js/${path}`, import.meta.url), "utf8");
  return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

test("MUTATION TARGET: the product's name is one exported literal (Android twin: Product.kt)", () => {
  assert.equal(PRODUCT_NAME, "SteamHangar");
  const src = readFileSync(new URL("../js/lib/product.js", import.meta.url), "utf8");
  assert.ok(src.includes('export const PRODUCT_NAME = "SteamHangar";'));
});

test("MUTATION TARGET: the runner labels read PRODUCT_NAME, never a literal name or the old label", () => {
  for (const path of LABEL_SOURCES) {
    const js = code(path);
    assert.match(js, /import \{ PRODUCT_NAME \} from "\.\.?\/(lib\/)?product\.js";/, `${path} imports PRODUCT_NAME`);
    assert.doesNotMatch(js, /SteamHangar|GameHangar/, `${path} spells the product's name itself`);
    assert.doesNotMatch(js, /prefill runner/i, `${path} still shows the old "prefill runner" label`);
  }
});
