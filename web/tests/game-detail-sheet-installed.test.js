/**
 * Headless tests for `web/js/components/game-detail-sheet.js`'s
 * installed-badge surface (WP AG-2 review round 1 blocker).
 *
 * The sheet is a module-level singleton wired to the real `store-singleton.js`
 * poll loops at import time (this file's own header explains why — a
 * `document`/`fetch`-dependent apparatus with no clean seam to drive from a
 * headless test), so unlike `game-card.js` this file does NOT import the
 * component and drive its full open()/render() lifecycle. Instead it targets
 * exactly the three things review round 1 found deletable with the suite
 * staying green:
 *
 *   1. Two of the three testable, EXPORTED pure/DOM-building pieces this WP
 *      pulled out of the component specifically so they could be exercised
 *      without the whole sheet apparatus — `buildInstalledSection` and
 *      `patchInstalledSection` (the third, `buildInstalledRow`, is exercised
 *      indirectly through both) — against a hand-built fake DOM tree
 *      (`web/tests/fake-dom.js`).
 *   2. Source-text WIRING pins, bounded to the specific function body each
 *      claim is about (a brace-balanced extractor, same technique
 *      `css-hygiene.test.js`'s `ruleBody`/`demo-data-installed-on.test.js`'s
 *      InstalledOn-class extractor already use in this codebase) — the
 *      same class of pin `header-art.test.js`'s "Production wiring" section
 *      already uses for the identical risk (a mechanism with zero real
 *      callers, or a caller silently un-wired from one). This closes
 *      deletion targets 4/5/6 from the review: the `render()` section
 *      append, the `installedBadge` field in `computeStructuralKey()`'s
 *      call to `buildDetailStructuralKey`, and the `patchInstalledSection`
 *      call inside `patchVolatile()`.
 *
 * `buildDetailStructuralKey` itself (deletion target 5's OTHER half — does
 * the value that gets fed in actually change the key) is exhaustively
 * mutation-tested in `web/tests/detail-render-plan.test.js`, including the
 * exact S4 boundary case (cached/not_cached both collapsing to "present").
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { createFakeDom } from "./fake-dom.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const webDir = path.join(__dirname, "..");
const detailJsPath = path.join(webDir, "js", "components", "game-detail-sheet.js");
const detailJs = readFileSync(detailJsPath, "utf8");

/** Brace-balanced extractor for one named function's BODY text (same
 * technique this codebase's own CSS/Python extractors already use) — bounds
 * a wiring assertion to the specific function it claims to be about, rather
 * than a whole-file substring search that could match an unrelated comment
 * or a call site inside some other function entirely. */
function extractFunctionBody(src, signature) {
  const idx = src.indexOf(signature);
  if (idx === -1) return null;
  const braceStart = src.indexOf("{", idx);
  if (braceStart === -1) return null;
  let depth = 1;
  let j = braceStart + 1;
  while (depth > 0 && j < src.length) {
    if (src[j] === "{") depth++;
    else if (src[j] === "}") depth--;
    j++;
  }
  return src.slice(braceStart + 1, j - 1);
}

// ---------------------------------------------------------------------
// Wiring pins (deletion targets 4, 5, 6 from the review)
// ---------------------------------------------------------------------

test("MUTATION TARGET -- render() appends buildInstalledSection(gameLike) into contentEl (deletion target 4)", () => {
  const body = extractFunctionBody(detailJs, "function render() {");
  assert.ok(body, "render() not found in game-detail-sheet.js — did it move or get renamed?");
  assert.match(
    body,
    /const installedSection = buildInstalledSection\(gameLike\);\s*\n\s*if \(installedSection\) overview\.append\(installedSection\);/,
    "render() no longer builds+appends the 'Installed on' section — the whole feature would silently disappear from the sheet",
  );
});

test("MUTATION TARGET -- computeStructuralKey() feeds installedSectionPresence(gameLike) into buildDetailStructuralKey as installedBadge (deletion target 5)", () => {
  const body = extractFunctionBody(detailJs, "function computeStructuralKey() {");
  assert.ok(body, "computeStructuralKey() not found in game-detail-sheet.js — did it move or get renamed?");
  assert.match(
    body,
    /installedBadge:\s*installedSectionPresence\(gameLike\)/,
    "computeStructuralKey() no longer feeds installedSectionPresence(gameLike) into the structural key — an already-open " +
      "sheet would never react to installed_on going from empty to non-empty (or back) on a live poll tick",
  );
  // The S4 fix itself, pinned by name at the wiring level too: the raw
  // 3-state installedBadgeState must NOT be what is fed in here (that
  // exact regression is what forced an unwanted full re-render mid-download
  // — see installedSectionPresence's own header in game-status.js).
  assert.doesNotMatch(
    body,
    /installedBadge:\s*installedBadgeState\(gameLike\)/,
    "computeStructuralKey() must feed installedSectionPresence, not the raw installedBadgeState (S4 regression)",
  );
});

test("MUTATION TARGET -- patchVolatile() calls patchInstalledSection(contentEl, gameLike) (deletion target 6)", () => {
  const body = extractFunctionBody(detailJs, "function patchVolatile() {");
  assert.ok(body, "patchVolatile() not found in game-detail-sheet.js — did it move or get renamed?");
  assert.match(
    body,
    /patchInstalledSection\(contentEl,\s*gameLike\)/,
    "patchVolatile() no longer calls patchInstalledSection — a same-structural-key poll tick (the common case) would leave " +
      "the installed-on rows/note stale forever until some unrelated structural change forced a full rebuild",
  );
});

// ---------------------------------------------------------------------
// buildInstalledSection — direct, hermetic (no sheet/store apparatus)
// ---------------------------------------------------------------------

function installedOn(n) {
  return Array.from({ length: n }, (_, i) => ({ client_id: `client-${i}`, reported_at: "2026-08-22T09:15:03Z" }));
}

/**
 * `game-detail-sheet.js` imports `store-singleton.js`, which calls
 * `store.start()` unconditionally at ITS OWN module load — real
 * `setTimeout`-driven poll loops (`web/js/store.js`), harmless in a real
 * browser but each one is a background process a headless test run never
 * asked for. `store.js`'s own module header documents the escape hatch:
 * `document.hidden === true` PARKS every loop with NO timer re-armed at
 * all (`isDocumentHidden()`), which is exactly "no background activity" —
 * set BEFORE the dynamic import below, since `store.start()` runs at that
 * import's top level, not lazily. `document.hidden` is a plain data
 * property on this shim (no getter), so a bare assignment is enough.
 */
async function withFakeDom(fn) {
  const dom = createFakeDom();
  dom.document.hidden = true;
  globalThis.document = dom.document;
  globalThis.window = dom.window;
  const mod = await import("../js/components/game-detail-sheet.js");
  return fn(mod, dom);
}

test("MUTATION TARGET -- buildInstalledSection: empty installed_on returns null (nothing painted, no 'not installed' claim)", () =>
  withFakeDom(({ buildInstalledSection }) => {
    assert.equal(buildInstalledSection({ installed_on: [] }), null);
  }));

test("MUTATION TARGET -- buildInstalledSection: CACHED renders the list but no note", () =>
  withFakeDom(({ buildInstalledSection }) => {
    const section = buildInstalledSection({ installed_on: installedOn(1), size_bytes: 500 });
    assert.ok(section);
    assert.ok(section.querySelector(".installed-list"));
    assert.equal(section.querySelector('[data-role="installed-note"]'), null);
  }));

test("MUTATION TARGET -- buildInstalledSection: NOT_CACHED renders the list AND the note, with bytes-only wording (no 'protecting')", () =>
  withFakeDom(({ buildInstalledSection }) => {
    const section = buildInstalledSection({ installed_on: installedOn(1), size_bytes: null });
    assert.ok(section);
    assert.ok(section.querySelector(".installed-list"));
    const note = section.querySelector('[data-role="installed-note"]');
    assert.ok(note, "the NOT_CACHED note must exist");
    assert.match(note.textContent, /not cached/i);
    // Review nitpick: "protecting" collides with hasProtectedCacheContent's
    // STATUS-based vocabulary — the note must stick to bytes-only wording.
    assert.doesNotMatch(note.textContent, /protect/i);
  }));

test("buildInstalledSection: one row per installed_on entry, client_id and a real formatted timestamp", () =>
  withFakeDom(({ buildInstalledSection }) => {
    const section = buildInstalledSection({ installed_on: installedOn(2), size_bytes: 500 });
    const rows = section.querySelectorAll(".installed-row");
    assert.equal(rows.length, 2);
    assert.equal(rows[0].querySelector(".iname").textContent, "client-0");
    // formatTimestamp never emits the raw ISO string verbatim (format.js
    // contract) — a raw "2026-08-22T09:15:03Z" surviving into the DOM would
    // mean this row bypassed the project's timestamp formatting entirely.
    assert.notEqual(rows[0].querySelector(".iwhen").textContent, "2026-08-22T09:15:03Z");
  }));

// ---------------------------------------------------------------------
// patchInstalledSection — direct, hermetic
// ---------------------------------------------------------------------

test("patchInstalledSection: a no-op (no throw) when nothing was painted for this game (no .installed-list in the container)", () =>
  withFakeDom(({ patchInstalledSection }, dom) => {
    const container = dom.document.createElement("div");
    assert.doesNotThrow(() => patchInstalledSection(container, { installed_on: installedOn(1), size_bytes: 500 }));
  }));

test("MUTATION TARGET -- patchInstalledSection: same client set, changed reported_at updates the row's text IN PLACE (same node)", () =>
  withFakeDom(({ buildInstalledSection, patchInstalledSection }, dom) => {
    const container = dom.document.createElement("div");
    container.appendChild(buildInstalledSection({ installed_on: installedOn(1), size_bytes: 500 }));
    const rowBefore = container.querySelector(".installed-row");
    const whenBefore = rowBefore.querySelector(".iwhen").textContent;

    patchInstalledSection(container, {
      installed_on: [{ client_id: "client-0", reported_at: "2026-08-22T11:00:00Z" }],
      size_bytes: 500,
    });

    const rowAfter = container.querySelector(".installed-row");
    assert.equal(rowAfter, rowBefore, "the row must be the SAME node, patched in place, not rebuilt");
    assert.notEqual(rowAfter.querySelector(".iwhen").textContent, whenBefore, "the timestamp text must have updated");
  }));

test("MUTATION TARGET -- patchInstalledSection: a client dropping out of installed_on REMOVES its row, keeps the survivor's identity", () =>
  withFakeDom(({ buildInstalledSection, patchInstalledSection }, dom) => {
    const container = dom.document.createElement("div");
    container.appendChild(buildInstalledSection({ installed_on: installedOn(2), size_bytes: 500 }));
    const survivorBefore = [...container.querySelectorAll(".installed-row")].find((r) => r.dataset.clientid === "client-0");
    assert.equal(container.querySelectorAll(".installed-row").length, 2, "sanity: starts with two rows");

    patchInstalledSection(container, { installed_on: [{ client_id: "client-0", reported_at: "2026-08-22T09:15:03Z" }], size_bytes: 500 });

    const rows = container.querySelectorAll(".installed-row");
    assert.equal(rows.length, 1, "the dropped client's row must be removed");
    assert.equal(rows[0], survivorBefore, "the surviving client's row must be the same node, not rebuilt");
  }));

test("patchInstalledSection: a NEW client appearing (still installed, one more report) adds a row", () =>
  withFakeDom(({ buildInstalledSection, patchInstalledSection }, dom) => {
    const container = dom.document.createElement("div");
    container.appendChild(buildInstalledSection({ installed_on: installedOn(1), size_bytes: 500 }));
    patchInstalledSection(container, { installed_on: installedOn(2), size_bytes: 500 });
    assert.equal(container.querySelectorAll(".installed-row").length, 2);
  }));

test("patchInstalledSection: rows are re-ordered to match the server's order (review nitpick — a re-sorted client must not stay stuck at its old position)", () =>
  withFakeDom(({ buildInstalledSection, patchInstalledSection }, dom) => {
    const container = dom.document.createElement("div");
    container.appendChild(buildInstalledSection({ installed_on: installedOn(2), size_bytes: 500 })); // client-0, client-1
    const reordered = [...installedOn(2)].reverse(); // client-1, client-0
    patchInstalledSection(container, { installed_on: reordered, size_bytes: 500 });
    const clientIds = [...container.querySelectorAll(".installed-row")].map((r) => r.dataset.clientid);
    assert.deepEqual(clientIds, ["client-1", "client-0"]);
  }));

test("MUTATION TARGET -- patchInstalledSection: CACHED -> NOT_CACHED (same client, size_bytes crosses to null) ADDS the note without touching row identity", () =>
  withFakeDom(({ buildInstalledSection, patchInstalledSection }, dom) => {
    const container = dom.document.createElement("div");
    container.appendChild(buildInstalledSection({ installed_on: installedOn(1), size_bytes: 500 }));
    const rowBefore = container.querySelector(".installed-row");
    assert.equal(container.querySelector('[data-role="installed-note"]'), null, "sanity: no note while cached");

    patchInstalledSection(container, { installed_on: installedOn(1), size_bytes: null });

    const note = container.querySelector('[data-role="installed-note"]');
    assert.ok(note, "the note must appear once size_bytes crosses to null while installed_on stays non-empty");
    assert.equal(container.querySelector(".installed-row"), rowBefore, "the row itself must not have been rebuilt");
  }));

test("MUTATION TARGET -- patchInstalledSection: NOT_CACHED -> CACHED removes the note", () =>
  withFakeDom(({ buildInstalledSection, patchInstalledSection }, dom) => {
    const container = dom.document.createElement("div");
    container.appendChild(buildInstalledSection({ installed_on: installedOn(1), size_bytes: null }));
    assert.ok(container.querySelector('[data-role="installed-note"]'), "sanity: starts with the note");

    patchInstalledSection(container, { installed_on: installedOn(1), size_bytes: 500 });

    assert.equal(container.querySelector('[data-role="installed-note"]'), null, "the note must be removed once bytes are visible again");
  }));

test("patchInstalledSection: calling it twice in a row with unchanged data is idempotent (no duplicate rows/notes)", () =>
  withFakeDom(({ buildInstalledSection, patchInstalledSection }, dom) => {
    const container = dom.document.createElement("div");
    container.appendChild(buildInstalledSection({ installed_on: installedOn(1), size_bytes: null }));
    patchInstalledSection(container, { installed_on: installedOn(1), size_bytes: null });
    patchInstalledSection(container, { installed_on: installedOn(1), size_bytes: null });
    assert.equal(container.querySelectorAll(".installed-row").length, 1);
    assert.equal(container.querySelectorAll('[data-role="installed-note"]').length, 1);
  }));
