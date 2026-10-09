/**
 * Headless fake-DOM tests for `web/js/components/game-card.js`'s
 * installed-badge surface (WP AG-2 review round 1 blocker).
 *
 * `game-card.js`'s own header documents its usual posture: a DOM-building
 * component, not unit-tested the same way as the pure `lib/` modules — its
 * headless coverage targets the DECISION logic (`game-status.js`) instead.
 * This file is the named exception that posture already carves out for
 * exactly this class of risk (same reasoning `header-art.test.js`'s header
 * gives for its own "ONE named exception" section): the round-1 review
 * measured that EVERY DOM-touching line this WP added — `syncInstalledTag`'s
 * call sites in `buildCard`/`patchCardVolatile`, the aria-label fold, the
 * badge's own class/text — could be deleted with the full 735-test suite
 * staying green, because nothing exercised `buildCard`/`patchCardVolatile`
 * against a real (if fake) DOM at all. This file closes that.
 *
 * Uses the shared `web/tests/fake-dom.js` harness, extended by this WP with
 * a real (if minimal) `querySelector`/`querySelectorAll` — the module-level
 * comment in that file explains why the previous `return null` stub would
 * have made every test below pass vacuously (the call under test never
 * actually reaching the code it exists to exercise).
 *
 * `game-card.js` only touches `document` INSIDE its exported functions,
 * never at module load (confirmed live during the original WP: `node
 * --input-type=module -e "import(...)"` with no globals set at all
 * succeeds) — same reasoning `game-card.test.js`'s header already gives.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createFakeDom } from "./fake-dom.js";

function noop() {}
function baseCtx(over = {}) {
  return { picked: false, selecting: false, onOpen: noop, onLongPress: noop, onToggle: noop, onAction: noop, ...over };
}

function game(over = {}) {
  return {
    appid: 2010010,
    name: "Aurora Cascade",
    status: "done",
    last_prefill_at: "2026-08-01T00:00:00Z",
    size_bytes: 500_000_000,
    needs_force: false,
    installed_on: [],
    ...over,
  };
}

function installedOn(n, base = {}) {
  return Array.from({ length: n }, (_, i) => ({ client_id: `client-${i}`, reported_at: "2026-08-22T09:15:03Z", ...base }));
}

async function withFakeDom(fn) {
  const dom = createFakeDom();
  globalThis.document = dom.document;
  globalThis.window = dom.window;
  const gameCard = await import("../js/components/game-card.js");
  return fn(gameCard, dom);
}

// ---------------------------------------------------------------------
// buildCard: the badge itself (deletion targets 1 and 3 — syncInstalledTag
// out of buildCard, and the aria-label fold)
// ---------------------------------------------------------------------

test("MUTATION TARGET -- buildCard: NONE (empty installed_on) never builds a .instbadge at all", () =>
  withFakeDom(({ buildCard }) => {
    const card = buildCard(game({ installed_on: [] }), baseCtx());
    assert.equal(card.querySelector(".instbadge"), null);
    // Module-header rule (game-status.js): the absence case is a silent
    // omission, never a "not installed" sentence anywhere in the label.
    assert.doesNotMatch(card.getAttribute("aria-label"), /not installed/i);
  }));

test("MUTATION TARGET -- buildCard: CACHED (installed + visible bytes) builds a plain .instbadge with the full/compact text pair, no .warn", () =>
  withFakeDom(({ buildCard }) => {
    const card = buildCard(game({ installed_on: installedOn(1), size_bytes: 500_000_000, status: "done" }), baseCtx());
    const badge = card.querySelector(".instbadge");
    assert.ok(badge, "buildCard must build a .instbadge for a CACHED, installed game");
    assert.equal(badge.classList.contains("warn"), false, "CACHED must not carry the .warn class");
    const full = badge.querySelector(".ibfull");
    const compact = badge.querySelector(".ibcompact");
    assert.ok(full && compact, "both the full and compact text children must exist");
    assert.equal(full.textContent, "Installed on client-0");
    assert.equal(compact.textContent, "client-0");
  }));

test("MUTATION TARGET -- buildCard: NOT_CACHED (installed, no visible bytes) builds .instbadge.warn with the exact NOT_CACHED text", () =>
  withFakeDom(({ buildCard }) => {
    const card = buildCard(game({ installed_on: installedOn(1), size_bytes: null, status: "idle" }), baseCtx());
    const badge = card.querySelector(".instbadge");
    assert.ok(badge, "buildCard must build a .instbadge for a NOT_CACHED, installed game");
    assert.equal(badge.classList.contains("warn"), true, "NOT_CACHED must carry the .warn class (the payoff of this whole feature)");
    assert.equal(badge.querySelector(".ibfull").textContent, "Installed but not cached · client-0");
    assert.equal(badge.querySelector(".ibcompact").textContent, "not cached · client-0");
  }));

test("buildCard: aria-label carries the badge text for a CACHED, installed game", () =>
  withFakeDom(({ buildCard }) => {
    const card = buildCard(game({ installed_on: installedOn(1), size_bytes: 500_000_000, status: "done" }), baseCtx());
    assert.match(card.getAttribute("aria-label"), /Installed on client-0/);
  }));

test("MUTATION TARGET -- buildCard: aria-label states BOTH facts for a running download that is also NOT_CACHED (no collision with dispKind's own word)", () =>
  withFakeDom(({ buildCard }) => {
    // dispKind is "running" here (a live job overrides cache state
    // entirely — game-status.js's dispKind), so STATUS_LABEL's word is
    // "Downloading", not "Not cached" — no collision risk, so the full
    // "Installed but not cached" phrasing must appear verbatim.
    const liveJob = { id: 1, appid: 2010010, type: "prefill", status: "running" };
    // last_prefill_at null: a first fill. With a completed copy the same
    // job reads "Updating" (WP WEB-FEAT-6, update-verify.test.js).
    const card = buildCard(game({ installed_on: installedOn(1), size_bytes: null, status: "idle", last_prefill_at: null }), baseCtx({ liveJob }));
    const label = card.getAttribute("aria-label");
    assert.match(label, /Downloading/);
    assert.match(label, /Installed but not cached · client-0/);
  }));

test("MUTATION TARGET -- buildCard: aria-label does NOT repeat 'not cached' when dispKind is already 'none' (STATUS_LABEL says it once already)", () =>
  withFakeDom(({ buildCard }) => {
    const card = buildCard(game({ installed_on: installedOn(1), size_bytes: null, status: "idle" }), baseCtx());
    const label = card.getAttribute("aria-label");
    const occurrences = (label.match(/not cached/gi) || []).length;
    assert.equal(occurrences, 1, `expected 'not cached' to appear exactly once in "${label}", found ${occurrences}`);
    // Still names the client — dropping the badge fragment entirely would
    // also pass a naive "exactly one" count by accident.
    assert.match(label, /client-0/);
  }));

// ---------------------------------------------------------------------
// patchCardVolatile: the six transitions (deletion target 2 — syncInstalledTag
// out of patchCardVolatile) plus the protected-subtree instrumentation.
// ---------------------------------------------------------------------

function patch(gameCard, dom, cardEl, nextGame, kind) {
  gameCard.patchCardVolatile(cardEl, nextGame, kind);
}

test("MUTATION TARGET -- patchCardVolatile: none -> cached makes the badge APPEAR", () =>
  withFakeDom(({ buildCard, patchCardVolatile }) => {
    const card = buildCard(game({ installed_on: [], size_bytes: 500_000_000, status: "done" }), baseCtx());
    assert.equal(card.querySelector(".instbadge"), null, "sanity: no badge yet");
    patchCardVolatile(card, game({ installed_on: installedOn(1), size_bytes: 500_000_000, status: "done" }), "cached");
    const badge = card.querySelector(".instbadge");
    assert.ok(badge, "patchCardVolatile must create the badge once installed_on becomes non-empty");
    assert.equal(badge.classList.contains("warn"), false);
  }));

test("MUTATION TARGET -- patchCardVolatile: none -> not_cached makes the .warn badge APPEAR", () =>
  withFakeDom(({ buildCard, patchCardVolatile }) => {
    const card = buildCard(game({ installed_on: [], size_bytes: null, status: "idle" }), baseCtx());
    assert.equal(card.querySelector(".instbadge"), null, "sanity: no badge yet");
    patchCardVolatile(card, game({ installed_on: installedOn(1), size_bytes: null, status: "idle" }), "none");
    const badge = card.querySelector(".instbadge");
    assert.ok(badge);
    assert.equal(badge.classList.contains("warn"), true);
  }));

test("MUTATION TARGET -- patchCardVolatile: cached -> not_cached flips the .warn class AND the text in place (same node)", () =>
  withFakeDom(({ buildCard, patchCardVolatile }) => {
    const card = buildCard(game({ installed_on: installedOn(1), size_bytes: 500_000_000, status: "done" }), baseCtx());
    const before = card.querySelector(".instbadge");
    assert.equal(before.classList.contains("warn"), false, "sanity: starts CACHED");
    patchCardVolatile(card, game({ installed_on: installedOn(1), size_bytes: null, status: "idle" }), "none");
    const after = card.querySelector(".instbadge");
    assert.equal(after, before, "the SAME badge node must be updated in place, not replaced");
    assert.equal(after.classList.contains("warn"), true);
    assert.equal(after.querySelector(".ibfull").textContent, "Installed but not cached · client-0");
  }));

test("MUTATION TARGET -- patchCardVolatile: not_cached -> none REMOVES the badge", () =>
  withFakeDom(({ buildCard, patchCardVolatile }) => {
    const card = buildCard(game({ installed_on: installedOn(1), size_bytes: null, status: "idle" }), baseCtx());
    assert.ok(card.querySelector(".instbadge"), "sanity: starts with a badge");
    patchCardVolatile(card, game({ installed_on: [], size_bytes: null, status: "idle" }), "none");
    assert.equal(card.querySelector(".instbadge"), null, "the badge must be removed once installed_on goes back to empty");
  }));

test("patchCardVolatile: cached -> cached with a DIFFERENT client set updates text in place, preserving node identity", () =>
  withFakeDom(({ buildCard, patchCardVolatile }) => {
    const card = buildCard(game({ installed_on: installedOn(1), size_bytes: 500_000_000, status: "done" }), baseCtx());
    const before = card.querySelector(".instbadge");
    patchCardVolatile(
      card,
      game({ installed_on: installedOn(2), size_bytes: 700_000_000, status: "done" }),
      "cached",
    );
    const after = card.querySelector(".instbadge");
    assert.equal(after, before, "same-state update must patch the existing node, not rebuild it");
    assert.equal(after.querySelector(".ibfull").textContent, "Installed on client-0 +1");
  }));

test("patchCardVolatile: not_cached -> not_cached (unrelated field drift) preserves node identity and updates text", () =>
  withFakeDom(({ buildCard, patchCardVolatile }) => {
    const card = buildCard(game({ installed_on: installedOn(1), size_bytes: null, status: "idle" }), baseCtx());
    const before = card.querySelector(".instbadge");
    patchCardVolatile(card, game({ installed_on: installedOn(3), size_bytes: null, status: "idle" }), "none");
    const after = card.querySelector(".instbadge");
    assert.equal(after, before);
    assert.equal(after.querySelector(".ibfull").textContent, "Installed but not cached · client-0 +2");
  }));

test("MUTATION TARGET -- patchCardVolatile touches NO new SVG node (the protected status-icon subtree is genuinely untouched, instrumented)", () =>
  withFakeDom(({ buildCard, patchCardVolatile }, dom) => {
    const card = buildCard(game({ installed_on: [], size_bytes: 500_000_000, status: "done" }), baseCtx());
    const iconBefore = card.querySelector(".sic");
    assert.ok(iconBefore, "sanity: the card has a status-icon node to protect");
    // WP 4a.3 review technique (game-card.test.js's own header references
    // this class of instrumentation): wire createElementNS to THROW so any
    // code path that would rebuild an icon fails LOUDLY instead of passing
    // on trust. patchCardVolatile must complete without ever calling it,
    // for a same-dispKind tick that ALSO happens to change installed_on.
    dom.document.createElementNS = () => {
      throw new Error("patchCardVolatile must never call createElementNS — the icon subtree must be untouched");
    };
    assert.doesNotThrow(() =>
      patchCardVolatile(card, game({ installed_on: installedOn(1), size_bytes: 600_000_000, status: "done" }), "cached"),
    );
    const iconAfter = card.querySelector(".sic");
    assert.equal(iconAfter, iconBefore, "the status-icon node itself must be the exact same object after the patch");
  }));
