/**
 * Fake-DOM tests for the "Update available" card state (WP UPD-6) and the
 * remaining `.updbadge` line (WP UPD-2, `components/game-card.js`
 * `syncUpdateTag`). Same harness and reasoning as
 * game-card-installed.test.js: delete any call site of the badge and a test
 * here fails.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createFakeDom } from "./fake-dom.js";

function noop() {}
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
    update_checked_at: "2026-10-13T10:00:00Z",
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

test("MUTATION TARGET -- WP UPD-6: a flagged CACHED card says 'Update available' with the orange refresh glyph, and no separate badge", () =>
  withFakeDom(({ buildCard }) => {
    const card = buildCard(game({ update_available: true }), ctx);
    const state = card.querySelector(".meta .state");
    assert.equal(state.textContent, "Update available");
    assert.match(state.className, /\btx-stale\b/);
    assert.match(card.querySelector(".cappill .sic").className, /\bk-stale\b/, "pill: orange refresh, not the green check");
    assert.match(card.querySelector(".meta .sic").className, /\bk-stale\b/, "list-layout meta icon too");
    assert.equal(card.querySelector(".updbadge"), null, "said once, not twice");
    assert.equal(card.querySelector(".cappill .pv").textContent, "0.5 GB", "the pill keeps the size");
  }));

test("buildCard: false, missing (older vault-api) or a truthy non-boolean keep 'Current' and the green check", () =>
  withFakeDom(({ buildCard }) => {
    for (const over of [{ update_available: false }, { update_available: undefined }, { update_available: "true" }]) {
      const card = buildCard(game(over), ctx);
      assert.equal(card.querySelector(".updbadge"), null, JSON.stringify(over));
      assert.equal(card.querySelector(".state").textContent, "Current", JSON.stringify(over));
      assert.match(card.querySelector(".cappill .sic").className, /\bk-cached\b/, JSON.stringify(over));
    }
  }));

test("not a status kind: dispKind/data-dk stay 'cached' (filters, actions and the round-7 key are unchanged)", () =>
  withFakeDom(({ buildCard }) => {
    const flagged = buildCard(game({ update_available: true }), ctx);
    assert.equal(flagged.dataset.dk, "cached");
    assert.equal(flagged.querySelector("button.cappill"), null, "no action on a cached card (never a silent re-download)");
  }));

test("MUTATION TARGET -- a flagged card that is NOT cached (failed) keeps its own word and shows the .updbadge line", () =>
  withFakeDom(({ buildCard }) => {
    const card = buildCard(game({ update_available: true, status: "error" }), ctx);
    assert.equal(card.querySelector(".state").textContent, "Failed");
    const badge = card.querySelector(".meta .updbadge");
    assert.ok(badge, "badge built in the meta row");
    assert.equal(badge.querySelector(".ubfull").textContent, "Update available");
    assert.equal(badge.querySelector(".ubcompact").textContent, "Update");
    assert.match(card.getAttribute("aria-label"), /— Failed — 0\.5 GB — Update available/);
  }));

test("MUTATION TARGET -- aria-label: 'Update available' replaces 'Current' once, never both", () =>
  withFakeDom(({ buildCard }) => {
    const label = buildCard(game({ update_available: true }), ctx).getAttribute("aria-label");
    assert.equal(label, "Aurora Cascade — Update available — 0.5 GB");
    assert.doesNotMatch(label, /Current/);
    assert.equal(buildCard(game(), ctx).getAttribute("aria-label"), "Aurora Cascade — Current — 0.5 GB");
  }));

test("group card: a flag on any member (groupGames' OR) turns the group's pill and word orange", () =>
  withFakeDom(async ({ buildCard }) => {
    const { groupGames } = await import("../js/lib/content-groups.js");
    const parent = game({ appid: 500, name: "Parent" });
    const dlc = game({ appid: 501, name: "Parent - Soundtrack", parent_appid: 500, app_type: "dlc", update_available: true });
    const [row] = groupGames([parent, dlc]).rows;
    const card = buildCard(row, { ...ctx, failedPackageCount: 0 });
    assert.equal(card.querySelector(".state").textContent, "Update available");
    assert.match(card.querySelector(".cappill .sic").className, /\bk-stale\b/);
    assert.equal(card.querySelector(".updbadge"), null);
  }));

test("MUTATION TARGET -- patchCardVolatile keeps the .updbadge line of a non-cached card in sync and never adds it to a cached one", () =>
  withFakeDom(({ buildCard, patchCardVolatile }) => {
    const failed = game({ status: "error" });
    const card = buildCard(failed, ctx);
    assert.equal(card.querySelector(".updbadge"), null);
    patchCardVolatile(card, { ...failed, update_available: true }, "error");
    assert.ok(card.querySelector(".updbadge"), "appears");
    assert.match(card.getAttribute("aria-label"), /Update available/);
    patchCardVolatile(card, { ...failed, update_available: true }, "error");
    assert.equal(card.querySelectorAll(".updbadge").length, 1, "idempotent, never duplicated");
    patchCardVolatile(card, { ...failed, update_available: false }, "error");
    assert.equal(card.querySelector(".updbadge"), null, "disappears");
    assert.doesNotMatch(card.getAttribute("aria-label"), /Update available/);

    const cached = buildCard(game({ update_available: true }), ctx);
    patchCardVolatile(cached, game({ update_available: true }), "cached");
    assert.equal(cached.querySelector(".updbadge"), null, "the status word already says it");
  }));

test("css: the 'Update available' word is drawn in --stale", async () => {
  const { readFileSync } = await import("node:fs");
  const css = readFileSync(new URL("../css/app.css", import.meta.url), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
  assert.match(css, /\.meta \.state\.tx-stale\{\s*color:var\(--stale\);/);
});

test("css: .updbadge exists, compact word in cols3 and list, no fixed width", async () => {
  const { readFileSync } = await import("node:fs");
  const css = readFileSync(new URL("../css/app.css", import.meta.url), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
  const base = /(^|\})\s*\.updbadge\s*\{([^}]*)\}/.exec(css);
  assert.ok(base, ".updbadge rule missing");
  assert.doesNotMatch(base[2], /(^|[;\s])width:/);
  assert.match(css, /\.updbadge \.ubcompact\{\s*display:none;/);
  assert.match(css, /\.grid\.cols3 \.updbadge \.ubcompact\{\s*display:inline;/);
  assert.match(css, /\.grid\.list \.updbadge \.ubcompact\{\s*display:inline;/);
});
