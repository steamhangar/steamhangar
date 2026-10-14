/**
 * WP CACHE-COMPLETE-1c — the library card's completeness overlay in
 * `components/game-card.js` against fake-dom.js: the pill and list icon, the
 * status word with its compact twin, `data-ck`, the accessible name, the
 * "Never played" pill on a partial card, and the CSS that picks the compact
 * word per layout and keeps the two new kinds off the green. The precedence
 * itself is pinned in cache-completeness.test.js.
 *
 * Text and class names only, never node identity (docs/LEARNINGS.md, WP
 * WEB-FIX-1).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createFakeDom } from "./fake-dom.js";

const noop = () => {};
const ctx = { picked: false, selecting: false, onOpen: noop, onLongPress: noop, onToggle: noop, onAction: noop };

function game(over = {}) {
  return {
    appid: 2010010,
    name: "Aurora Cascade",
    status: "done",
    last_prefill_at: "2026-08-01T00:00:00Z",
    size_bytes: 500_000_000,
    needs_force: false,
    installed_on: [],
    update_available: false,
    cache_complete_pct: 100,
    cache_missing_chunks: 0,
    group_cache_complete_pct: 100,
    ...over,
  };
}

async function withFakeDom(fn) {
  const dom = createFakeDom();
  globalThis.document = dom.document;
  globalThis.window = dom.window;
  const gameCard = await import("../js/components/game-card.js");
  return fn(gameCard);
}

const stateEl = (card) => card.querySelector(".meta .state");
const pillIcon = (card) => card.querySelector(".cappill .sic").className;
const metaIcon = (card) => card.querySelector(".meta .sic").className;

test("MUTATION TARGET -- 100 %: the green check and 'Current', plain text, data-ck 'cached'", () =>
  withFakeDom(({ buildCard }) => {
    const card = buildCard(game(), ctx);
    assert.equal(stateEl(card).textContent, "Current");
    assert.equal(stateEl(card).querySelector(".stfull"), null, "no twin spans for a plain word");
    assert.match(pillIcon(card), /\bk-cached\b/);
    assert.match(metaIcon(card), /\bk-cached\b/);
    assert.equal(card.dataset.ck, "cached");
    assert.equal(card.dataset.dk, "cached");
  }));

test("MUTATION TARGET -- below 100: half-disc kind, full and compact word, tx-partial, data-ck with the figure", () =>
  withFakeDom(({ buildCard }) => {
    const card = buildCard(game({ cache_complete_pct: 42, cache_missing_chunks: 9 }), ctx);
    const st = stateEl(card);
    assert.match(st.className, /\btx-partial\b/);
    assert.equal(st.querySelector(".stfull").textContent, "Partially cached 42%");
    assert.equal(st.querySelector(".stcompact").textContent, "Partial 42%");
    assert.match(pillIcon(card), /\bk-partial\b/);
    assert.doesNotMatch(pillIcon(card), /\bk-cached\b/);
    assert.match(metaIcon(card), /\bk-partial\b/);
    assert.equal(card.dataset.ck, "partial:42");
    assert.equal(card.dataset.dk, "cached", "a display overlay, not a status kind");
    assert.equal(card.getAttribute("aria-label"), "Aurora Cascade — Partially cached 42% — 0.5 GB");
    assert.equal(card.querySelector("button.cappill"), null, "no card quick action (Complete now lives in Downloads)");
  }));

test("MUTATION TARGET -- unknown with bytes on disk: never the green check, 'Cached · not verified'", () =>
  withFakeDom(({ buildCard }) => {
    for (const over of [{ cache_complete_pct: null, cache_missing_chunks: null }, { cache_complete_pct: undefined }]) {
      const card = buildCard(game(over), ctx);
      assert.match(pillIcon(card), /\bk-unverified\b/, JSON.stringify(over));
      assert.doesNotMatch(pillIcon(card), /\bk-cached\b/);
      assert.equal(stateEl(card).querySelector(".stfull").textContent, "Cached · not verified");
      assert.equal(stateEl(card).querySelector(".stcompact").textContent, "Not verified");
      assert.match(stateEl(card).className, /\btx-unverified\b/);
      assert.equal(card.dataset.ck, "unverified");
      assert.match(card.getAttribute("aria-label"), /— Cached · not verified —/);
    }
  }));

test("the glyphs: partial is its own half-disc shape (a circle), unverified the question mark", () =>
  withFakeDom(async () => {
    const { createStatusIcon } = await import("../js/components/status-icon.js");
    const half = createStatusIcon("partial");
    assert.equal(half.className, "sic k-partial");
    const svg = half.children[0];
    assert.deepEqual(svg.children.map((c) => c.tagName.toLowerCase()), ["circle", "path"]);
    assert.equal(half.children[1].textContent, "Partially cached");
    const q = createStatusIcon("unverified");
    assert.equal(q.className, "sic k-unverified");
    assert.equal(q.children[1].textContent, "Not verified");
  }));

test("MUTATION TARGET -- 'Update available' wins on the card over a partial figure", () =>
  withFakeDom(({ buildCard }) => {
    const card = buildCard(game({ update_available: true, cache_complete_pct: 42, cache_missing_chunks: 3 }), ctx);
    assert.equal(stateEl(card).textContent, "Update available");
    assert.match(pillIcon(card), /\bk-stale\b/);
    assert.equal(card.dataset.ck, "stale");
  }));

test("not cached kinds ignore the figure (a failed game with a partial figure stays 'Failed')", () =>
  withFakeDom(({ buildCard }) => {
    const card = buildCard(game({ status: "error", cache_complete_pct: 42, cache_missing_chunks: 3 }), ctx);
    assert.equal(stateEl(card).textContent, "Failed");
    assert.equal(card.dataset.ck, "error");
  }));

test("MUTATION TARGET -- a group card reads group_cache_complete_pct", () =>
  withFakeDom(async ({ buildCard }) => {
    const { groupGames } = await import("../js/lib/content-groups.js");
    const parent = game({ appid: 500, name: "Parent", group_cache_complete_pct: 70 });
    const pkg = game({ appid: 501, name: "Parent - Pack", parent_appid: 500, app_type: "dlc", group_cache_complete_pct: null });
    const [row] = groupGames([parent, pkg]).rows;
    const card = buildCard(row, { ...ctx, failedPackageCount: 0 });
    assert.equal(stateEl(card).querySelector(".stfull").textContent, "Partially cached 70%");
    assert.equal(card.dataset.ck, "partial:70");
  }));

test("cardCompletenessKey equals what buildCard paints (the library's rebuild check)", () =>
  withFakeDom(({ buildCard, cardCompletenessKey }) => {
    for (const over of [{}, { cache_complete_pct: 42, cache_missing_chunks: 1 }, { cache_complete_pct: null }, { status: "error" }]) {
      const g = game(over);
      assert.equal(cardCompletenessKey(g, undefined), buildCard(g, ctx).dataset.ck, JSON.stringify(over));
    }
    assert.equal(cardCompletenessKey(game({ cache_complete_pct: 42 }), { status: "running", appid: 2010010 }), "updating", "a live job overrides");
  }));

test("MUTATION TARGET -- the 'Never played' pill ('Fully cached, never played') is not shown on a partial card", () =>
  withFakeDom(({ buildCard }) => {
    const played0 = { owned_playtime_forever: 0 };
    assert.ok(buildCard(game(played0), ctx).querySelector(".playpill"), "complete: shown");
    assert.ok(buildCard(game({ ...played0, cache_complete_pct: null }), ctx).querySelector(".playpill"), "unknown: kept");
    const partial = buildCard(game({ ...played0, cache_complete_pct: 60, cache_missing_chunks: 4 }), ctx);
    assert.equal(partial.querySelector(".playpill"), null);
    assert.equal(partial.querySelector(".playbadge"), null);
    assert.doesNotMatch(partial.getAttribute("aria-label"), /Fully cached/);
  }));

// ---------------------------------------------------------------------
// CSS
// ---------------------------------------------------------------------

const strip = (t) => t.replace(/\/\*[\s\S]*?\*\//g, "");
const appCss = strip(readFileSync(new URL("../css/app.css", import.meta.url), "utf8"));
const themeCss = strip(readFileSync(new URL("../css/theme.css", import.meta.url), "utf8"));

/** Top-level text (outside every @media block). */
function topLevel(text) {
  let out = "";
  let i = 0;
  while (i < text.length) {
    const at = text.indexOf("@media", i);
    if (at === -1) {
      out += text.slice(i);
      break;
    }
    out += text.slice(i, at);
    let depth = 0;
    let j = text.indexOf("{", at);
    do {
      if (text[j] === "{") depth++;
      else if (text[j] === "}") depth--;
      j++;
    } while (depth > 0 && j < text.length);
    i = j;
  }
  return out;
}
function rule(text, selector) {
  const esc = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const m = new RegExp(`(?:^|\\})\\s*${esc}\\s*\\{([^}]*)\\}`).exec(text);
  return m ? m[1] : null;
}

test("MUTATION TARGET -- css: the compact word everywhere in list and cols3, the full one otherwise", () => {
  const top = topLevel(appCss);
  assert.match(rule(top, ".meta .state .stcompact") ?? "", /display:\s*none/);
  assert.match(rule(top, ".grid.list .meta .state .stfull") ?? "", /display:\s*none/);
  assert.match(rule(top, ".grid.list .meta .state .stcompact") ?? "", /display:\s*inline/);
  assert.match(rule(top, ".grid.cols3 .meta .state .stfull") ?? "", /display:\s*none/);
  assert.match(rule(top, ".grid.cols3 .meta .state .stcompact") ?? "", /display:\s*inline/);
  // No media block brings the full word back into the list's 118px column.
  assert.doesNotMatch(appCss.replace(top, ""), /\.grid\.list \.meta \.state \.stfull\s*\{[^}]*display:\s*inline/);
});

test("MUTATION TARGET -- css: partial is --stale and unverified neutral, neither is --ok", () => {
  assert.match(rule(themeCss, ".sic.k-partial") ?? "", /background:\s*var\(--stale\)/);
  assert.match(rule(themeCss, ".sic.k-unverified") ?? "", /background:\s*var\(--dim-2\)/);
  assert.match(rule(appCss, ".meta .state.tx-partial") ?? "", /color:\s*var\(--stale\)/);
  assert.doesNotMatch(themeCss, /k-(partial|unverified)[^{]*\{[^}]*--ok/);
});
