/**
 * Navigation announcer pins (WP WEB-FIX-1, S2).
 *
 * `index.html` used to put `aria-live="polite"` on the whole
 * `<main id="view-root">` — a live region over the entire view, so every
 * navigation's DOM swap AND every poll tick's patched values (job progress,
 * sizes, counts) would be read out by a screen reader. Replaced by a
 * dedicated visually-hidden `role="status"` node (`#view-announcer`, same
 * pattern as `#toast`) that `app.js`'s `renderView` updates once per
 * navigation with the new view's title (`lib/view-title.js`).
 *
 * Three layers: the pure title map (literal expected set, never derived
 * from the router — LEARNINGS, Android section), the static markup
 * (structural scan of `index.html`), and the wiring in `app.js` (comment-
 * stripped source scan requiring the announcer write to sit INSIDE
 * `renderView` — the strong-generation pin, LEARNINGS AG series).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { VIEW_TITLES, viewTitle } from "../js/lib/view-title.js";

const here = dirname(fileURLToPath(import.meta.url));
const read = (...p) => readFileSync(join(here, "..", ...p), "utf8");
function stripComments(src) {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/[^\n]*/g, "$1");
}

test("VIEW_TITLES is exactly the three views, with the words each view's <h1> shows (literal pin)", () => {
  assert.deepEqual(VIEW_TITLES, { library: "Library", downloads: "Downloads", settings: "Settings" });
});

test("twin pin: VIEW_TITLES covers router.js's VIEWS list exactly (drift guard, read from source text)", () => {
  const routerSrc = read("js", "router.js");
  const m = /export const VIEWS = \[([^\]]*)\]/.exec(routerSrc);
  assert.ok(m, "router.js's `export const VIEWS = [...]` literal not found — GRAMMAR drift: fix this regex");
  const views = m[1].split(",").map((s) => s.trim().replace(/^["']|["']$/g, "")).filter(Boolean);
  assert.deepEqual(Object.keys(VIEW_TITLES).sort(), views.sort(), "VALUE drift: a view was added/renamed in router.js — add its title to lib/view-title.js");
});

test("viewTitle: known views map; an unknown view falls back to its raw name, never an empty announcement", () => {
  assert.equal(viewTitle("downloads"), "Downloads");
  assert.equal(viewTitle("nonesuch"), "nonesuch");
  assert.notEqual(viewTitle(undefined), "");
});

test("index.html: <main id=\"view-root\"> carries NO aria-live (MUTATION TARGET: restoring it re-announces every poll tick)", () => {
  // HTML comments stripped first: index.html's own comment explaining the
  // change mentions `<main>`, which a raw scan would match instead of the tag.
  const html = read("index.html").replace(/<!--[\s\S]*?-->/g, "");
  const main = /<main\b[^>]*>/.exec(html);
  assert.ok(main, "<main> not found");
  assert.match(main[0], /id="view-root"/);
  assert.doesNotMatch(main[0], /aria-live/, "<main id=\"view-root\"> must not be a live region");
});

test("index.html: #view-announcer exists in the static markup as a visually-hidden polite status region", () => {
  const html = read("index.html");
  const node = /<div\b[^>]*id="view-announcer"[^>]*>/.exec(html);
  assert.ok(node, "#view-announcer missing from index.html — it must be static markup so the live region is registered before its first update");
  assert.match(node[0], /class="[^"]*\bsr-only\b[^"]*"/, "must be visually hidden via .sr-only (theme.css)");
  assert.match(node[0], /role="status"/);
  assert.match(node[0], /aria-live="polite"/);
  assert.ok(/\.sr-only\s*\{/.test(read("css", "theme.css")), ".sr-only rule must exist in theme.css");
});

test("app.js: the announcer write lives INSIDE renderView, uses viewTitle(view), and skips the initial paint", () => {
  const src = stripComments(read("js", "app.js"));
  assert.match(src, /import \{ viewTitle \} from "\.\/lib\/view-title\.js"/);
  assert.match(src, /document\.getElementById\("view-announcer"\)/);
  const start = src.indexOf("function renderView(");
  assert.ok(start !== -1, "renderView not found");
  // Extract renderView's body by brace matching.
  let i = src.indexOf("{", start);
  let depth = 0;
  let end = -1;
  for (; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}") {
      depth--;
      if (depth === 0) {
        end = i;
        break;
      }
    }
  }
  const body = src.slice(start, end + 1);
  const write = /viewAnnouncer\.textContent = viewTitle\(view\)/;
  assert.match(body, write, "renderView must write viewTitle(view) into the announcer (deleted, or moved out of renderView)");
  assert.equal((src.match(new RegExp(write.source, "g")) || []).length, 1, "exactly one announcer write site in app.js");
  assert.match(body, /if \(announcedView !== null\)/, "the first paint (page load) must not be announced");
  assert.match(body, /announcedView = view/);
});
