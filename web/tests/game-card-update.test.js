/**
 * Fake-DOM tests for the "Update available" card badge (WP UPD-2,
 * `components/game-card.js` `syncUpdateTag`). Same harness and reasoning as
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

test("MUTATION TARGET -- buildCard: update_available=true builds .updbadge with the full/compact text pair", () =>
  withFakeDom(({ buildCard }) => {
    const card = buildCard(game({ update_available: true }), ctx);
    const badge = card.querySelector(".meta .updbadge");
    assert.ok(badge, "badge built in the meta row");
    assert.equal(badge.querySelector(".ubfull").textContent, "Update available");
    assert.equal(badge.querySelector(".ubcompact").textContent, "Update");
  }));

test("buildCard: false, missing (older vault-api) or a truthy non-boolean builds no badge", () =>
  withFakeDom(({ buildCard }) => {
    for (const over of [{ update_available: false }, { update_available: undefined }, { update_available: "true" }]) {
      const card = buildCard(game(over), ctx);
      assert.equal(card.querySelector(".updbadge"), null, JSON.stringify(over));
    }
  }));

test("the badge is not a status: dispKind/data-dk and the status word are the same as without the flag", () =>
  withFakeDom(({ buildCard }) => {
    const flagged = buildCard(game({ update_available: true }), ctx);
    const plain = buildCard(game(), ctx);
    assert.equal(flagged.dataset.dk, "cached");
    assert.equal(flagged.querySelector(".state").textContent, plain.querySelector(".state").textContent);
  }));

test("MUTATION TARGET -- aria-label carries 'Update available' only when the flag is set", () =>
  withFakeDom(({ buildCard }) => {
    assert.match(buildCard(game({ update_available: true }), ctx).getAttribute("aria-label"), /— Update available/);
    assert.doesNotMatch(buildCard(game(), ctx).getAttribute("aria-label"), /Update available/);
  }));

test("MUTATION TARGET -- patchCardVolatile adds and removes the badge, and keeps the aria-label in sync", () =>
  withFakeDom(({ buildCard, patchCardVolatile }) => {
    const card = buildCard(game(), ctx);
    assert.equal(card.querySelector(".updbadge"), null);
    patchCardVolatile(card, game({ update_available: true }), "cached");
    assert.ok(card.querySelector(".updbadge"), "appears");
    assert.match(card.getAttribute("aria-label"), /Update available/);
    patchCardVolatile(card, game({ update_available: true }), "cached");
    assert.equal(card.querySelectorAll(".updbadge").length, 1, "idempotent, never duplicated");
    patchCardVolatile(card, game({ update_available: false }), "cached");
    assert.equal(card.querySelector(".updbadge"), null, "disappears");
    assert.doesNotMatch(card.getAttribute("aria-label"), /Update available/);
  }));

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
