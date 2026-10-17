/**
 * Fake-DOM tests for the card's "Never played" pill (WP WEB-FEAT-SUGGEST-2,
 * `components/game-card.js` `syncNeverPlayed`): the Phase 4h panel's
 * "fully cached, never played" statement, moved onto the card. Same harness
 * as game-card-update.test.js. Plus the Library's playtime merge
 * (`lib/owned-library.js` `withOwnedPlaytime`) and its call site.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createFakeDom } from "./fake-dom.js";
import { withOwnedPlaytime } from "../js/lib/owned-library.js";

function noop() {}
const ctx = { picked: false, selecting: false, onOpen: noop, onLongPress: noop, onToggle: noop, onAction: noop };

function game(over = {}) {
  return {
    appid: 2010050,
    name: "Frostline Convoy",
    status: "done",
    last_prefill_at: "2026-08-01T00:00:00Z",
    size_bytes: 300_000_000,
    needs_force: false,
    installed_on: [],
    owned_playtime_forever: 0,
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

test("MUTATION TARGET -- buildCard: a cached, never-played game gets the 'Never played' sub-line, never a cover pill (WP LIBRARY-2)", () =>
  withFakeDom(({ buildCard }) => {
    const card = buildCard(game(), ctx);
    assert.ok(!card.querySelector(".cap .playpill"), "no pill on the cover any more");
    const line = card.querySelector(".meta .playbadge");
    assert.ok(line, "the sub-line exists");
    assert.equal(line.querySelector(".pbfull").textContent, "Never played");
    assert.equal(line.querySelector(".pbcompact").textContent, "Unplayed");
    assert.equal(line.title, "Fully cached, never played");
    assert.equal(line.getAttribute("aria-hidden"), "true");
    assert.match(card.getAttribute("aria-label"), /— Fully cached, never played/);
  }));

test("no pill without a real zero playtime (the ADR-0010 default), with playtime, or when not cached", () =>
  withFakeDom(({ buildCard }) => {
    for (const over of [
      { owned_playtime_forever: undefined },
      { owned_playtime_forever: 26 },
      { size_bytes: null, status: "idle", last_prefill_at: null },
      { status: "error" },
      { tool_app: true },
    ]) {
      const card = buildCard(game(over), ctx);
      assert.ok(!card.querySelector(".playbadge"), JSON.stringify(over));
      assert.doesNotMatch(card.getAttribute("aria-label"), /never played/i);
    }
  }));

test("a running download is not 'fully cached': no pill while a live job exists", () =>
  withFakeDom(({ buildCard }) => {
    const card = buildCard(game(), { ...ctx, liveJob: { id: 1, appid: 2010050, type: "prefill", status: "running" } });
    assert.ok(!card.querySelector(".playbadge"));
  }));

test("MUTATION TARGET -- patchCardVolatile adds and removes the line without touching the status line", () =>
  withFakeDom(({ buildCard, patchCardVolatile }) => {
    const card = buildCard(game({ owned_playtime_forever: undefined }), ctx);
    const status = card.querySelector(".stt");
    patchCardVolatile(card, game(), "cached");
    assert.ok(card.querySelector(".playbadge"), "appears");
    assert.equal(card.querySelector(".stt") === status, true, "status line node kept");
    patchCardVolatile(card, game(), "cached");
    assert.equal(card.querySelectorAll(".playbadge").length, 1, "no duplicate on a second tick");
    patchCardVolatile(card, game({ owned_playtime_forever: 5 }), "cached");
    assert.ok(!card.querySelector(".playbadge"), "disappears");
  }));

test("withOwnedPlaytime copies only real playtime values onto matching rows", () => {
  const rows = [{ appid: 1 }, { appid: 2 }, { appid: 3 }];
  const owned = [
    { appid: 1, playtime_forever: 0 },
    { appid: 2 }, // gate off: no field at all
    { appid: 3, playtime_forever: -4 },
  ];
  const out = withOwnedPlaytime(rows, owned);
  assert.equal(out[0].owned_playtime_forever, 0);
  assert.equal("owned_playtime_forever" in out[1], false, "absent stays absent, never a zero");
  assert.equal("owned_playtime_forever" in out[2], false);
  assert.equal(rows[0].owned_playtime_forever, undefined, "input rows are not mutated");
  const none = [{ appid: 1 }];
  assert.equal(withOwnedPlaytime(none, [{ appid: 1 }]), none, "nothing to copy: the same array");
});

test("wiring: the Library's remerge applies withOwnedPlaytime to the merged rows", () => {
  const src = readFileSync(new URL("../js/views/library.js", import.meta.url), "utf8");
  const body = /function remerge\(\) \{([\s\S]*?)\n\}/.exec(src);
  assert.ok(body, "remerge not found");
  // WP TOOL-APPS-2c: the playtime-carrying merged rows are grouped next,
  // and the grid shows the grouped rows built from exactly them.
  assert.match(body[1], /state\.rawGames = withOwnedPlaytime\(mergeOwnedLibrary\(state\.vaultGames, mergedOwnedGames\), mergedOwnedGames\);/);
  assert.match(body[1], /groupGames\(state\.rawGames\)/);
  assert.match(body[1], /state\.games = grouped\.rows;/);
});

test("css: the 'Never played' line ellipsizes, shows the full word, and Compact hides it with the other sub-lines", () => {
  const css = readFileSync(new URL("../css/kit.css", import.meta.url), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
  const rule = (sel) => {
    const m = new RegExp(`(^|\\})\\s*${sel.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*\\{([^}]*)\\}`).exec(css);
    assert.ok(m, `${sel} rule missing`);
    return m[2];
  };
  const line = rule(".kit .card .updbadge, .kit .card .instbadge, .kit .card .playbadge");
  for (const d of [/min-width:0/, /overflow:hidden/, /text-overflow:ellipsis/, /white-space:nowrap/]) assert.match(line, d);
  assert.match(rule(".kit .card .ibcompact, .kit .card .ubcompact, .kit .card .pbcompact"), /display:none/);
  assert.match(rule(".kit .grid.compact .card .meta > :not(.stt)"), /display:none/);
  assert.doesNotMatch(css, /playpill|capbadges/, "no cover pill rules left");
});
