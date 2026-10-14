/**
 * Fake-DOM tests for the group card (WP TOOL-APPS-2c,
 * `components/game-card.js`): the "+N content packages" line, the failed
 * warning, `data-gk`, the group size and the accessible name.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createFakeDom } from "./fake-dom.js";

const dom = createFakeDom();
globalThis.document = dom.document;
globalThis.window = dom.window;
const { buildCard, patchCardVolatile } = await import("../js/components/game-card.js");
const { groupGames } = await import("../js/lib/content-groups.js");

function noop() {}
const ctx = { picked: false, selecting: false, onOpen: noop, onLongPress: noop, onToggle: noop, onAction: noop };

const GIB = 1_073_741_824;
const row = (appid, over = {}) => ({
  appid,
  name: `Game ${appid}`,
  status: "done",
  last_prefill_at: "2026-10-01T00:00:00Z",
  size_bytes: 2 * GIB,
  needs_force: false,
  installed_on: [],
  update_available: false,
  ...over,
});

function group(depots) {
  const list = [
    row(100, { name: "Starfall Legion" }),
    row(101, { parent_appid: 100, size_bytes: 500_000_000 }),
    row(102, { parent_appid: 100, status: "error", size_bytes: null }),
  ];
  return groupGames(list, { depotsFor: depots ? (g) => depots.get(g.appid) : undefined }).rows[0];
}

test("MUTATION TARGET -- a group card shows '+N content packages' and the failed warning", () => {
  const card = buildCard(group(), { ...ctx, failedPackageCount: 1 });
  const line = card.querySelector(".meta .pkgline");
  assert.ok(line);
  assert.equal(line.querySelector(".pkgn").textContent, "+2 content packages");
  assert.equal(line.querySelector(".pkgfail").textContent, " · 1 failed");
  assert.equal(line.getAttribute("aria-hidden"), "true");
  assert.equal(card.dataset.gk, "2:1");
  assert.equal(card.dataset.dk, "cached", "the parent's status is the card's status");
  assert.match(card.getAttribute("aria-label"), /2 content packages, 1 failed/);
});

test("no warning without a failed package; a plain card has no line and an empty data-gk", () => {
  const card = buildCard(group(), { ...ctx, failedPackageCount: 0 });
  assert.equal(card.querySelector(".pkgfail"), null);
  assert.equal(card.dataset.gk, "2:0");
  const plain = buildCard(row(5), ctx);
  assert.equal(plain.querySelector(".pkgline"), null);
  assert.equal(plain.dataset.gk, "");
  assert.doesNotMatch(plain.getAttribute("aria-label"), /content package/);
});

test("MUTATION TARGET -- the size shown is the group's union once known, and a patch keeps it (and the aria fragment) in sync", () => {
  const unknown = buildCard(group(), { ...ctx, failedPackageCount: 1 });
  assert.equal(unknown.querySelector(".meta .size").textContent, "2.0 GB", "parent's own size until the union is known");
  const depots = new Map([
    [100, [{ depotid: 1, size_bytes: 2 * GIB }]],
    [101, [{ depotid: 2, size_bytes: 0.4 * GIB }, { depotid: 1, size_bytes: 2 * GIB }]],
  ]);
  patchCardVolatile(unknown, group(depots), "cached");
  assert.equal(unknown.querySelector(".meta .size").textContent, "2.4 GB");
  assert.equal(unknown.querySelector(".cappill .pv").textContent, "2.4 GB");
  assert.match(unknown.getAttribute("aria-label"), /2\.4 GB — 2 content packages, 1 failed/);
});
