/**
 * WP LIB-LIST-1 — fixed columns in the library list layout, rebuilt on the
 * UI kit by WP LIBRARY-2 (css/kit.css, Library block).
 *
 * User feedback on prod rc15: the list row's right side did not line up
 * from row to row. Since LIBRARY-2 a list row is a grid of cover | name |
 * `.meta`, and from a 720px Library container `.meta` is its own grid with
 * the SAME fixed tracks on every row (status 170px, size 80px, then the
 * sub-lines). No browser runs here, so the layout guarantees are pinned
 * structurally; the geometry was checked in headless Chromium at 1400 and
 * 400px for the WP report.
 *
 * Guarantees pinned by name:
 *  G1 a list row is a grid whose name track is the only flexible one
 *     beside `.meta` (minmax(0, …): nothing can widen the row);
 *  G3 from 720px of container `.meta` is a grid with fixed px tracks for
 *     status and size — the alignment promise;
 *  G4 every `.meta` child is placed with an explicit grid-column there
 *     (LEARNINGS 4h.2: an unplaced grid child lands wherever a cell is free);
 *  G6 size right-aligned with tabular figures; the sub-lines and the status
 *     word truncate with an ellipsis, full client list in the badge title
 *     and the aria-label;
 *  G7 `.updbadge` and `.playbadge` never co-occur on one card.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createFakeDom } from "./fake-dom.js";

const kit = readFileSync(new URL("../css/kit.css", import.meta.url), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

/** Body of the first rule whose selector text is exactly `sel` (top level
 * or inside an at-rule), or "". */
function body(text, sel) {
  const esc = sel.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const m = new RegExp(`(?:^|[{};])\\s*${esc}\\s*\\{([^}]*)\\}`).exec(text);
  return m ? m[1] : "";
}
function decl(b, prop) {
  const m = new RegExp(`(?:^|;|\\s)${prop}\\s*:\\s*([^;]+)`).exec(b);
  return m ? m[1].trim() : null;
}
/** The text of the `@container lib (min-width:720px)` block. */
function wideBlock() {
  const at = kit.indexOf("@container lib (min-width:720px)");
  assert.ok(at > 0, "no @container lib (min-width:720px) block");
  let depth = 0;
  let j = kit.indexOf("{", at);
  const start = j + 1;
  do {
    if (kit[j] === "{") depth++;
    else if (kit[j] === "}") depth--;
    j++;
  } while (depth > 0 && j < kit.length);
  return kit.slice(start, j - 1);
}

test("G1: a list row is a grid; the only flexible track is minmax(0, …), so nothing widens the row", () => {
  const card = body(kit, ".kit .grid.list .card");
  assert.equal(decl(card, "grid-template-columns"), "44px minmax(0, 1fr)");
  assert.match(body(kit, ".kit.view-library > .grid.list"), /grid-template-columns:minmax\(0, 1fr\)/);
  assert.match(body(kit, ".kit .grid.list .card .rowname"), /min-width:0/);
  assert.match(body(wideBlock(), ".kit .grid.list .card"), /grid-template-columns:44px minmax\(0, 1fr\) minmax\(0, 2fr\)/);
});

test("G3 MUTATION TARGET: from 720px of container, .meta is a grid with fixed px tracks for status and size", () => {
  const meta = body(wideBlock(), ".kit .grid.list .card .meta");
  assert.equal(decl(meta, "display"), "grid");
  const cols = decl(meta, "grid-template-columns").split(" ");
  assert.match(cols[0], /^\d+px$/, "status track fixed");
  assert.match(cols[1], /^\d+px$/, "size track fixed");
  assert.equal(cols.slice(2).join(" "), "minmax(0, 1fr)", "the sub-lines take the rest");
});

test("G4: every .meta child gets an explicit grid-column in the wide list", () => {
  const w = wideBlock();
  assert.match(body(w, ".kit .grid.list .card .meta > .stt"), /grid-column:1/);
  assert.match(body(w, ".kit .grid.list .card .meta > .size"), /grid-column:2/);
  assert.match(body(w, ".kit .grid.list .card .meta > :not(.stt):not(.size)"), /grid-column:3/);
});

test("G6: size is right-aligned with tabular figures; the sub-lines and the status word truncate", () => {
  const size = body(kit, ".kit .card .meta .size");
  assert.equal(decl(size, "text-align"), "right");
  assert.match(size, /font-variant-numeric:tabular-nums/);
  assert.equal(decl(body(kit, ".kit .grid.list .card .meta .size"), "display"), "block", "the list shows the size column");
  const sub = body(kit, ".kit .card .updbadge, .kit .card .instbadge, .kit .card .playbadge");
  for (const [p, v] of [["overflow", "hidden"], ["text-overflow", "ellipsis"], ["white-space", "nowrap"], ["min-width", "0"]]) {
    assert.equal(decl(sub, p), v, `sub-line ${p}`);
  }
  assert.equal(decl(body(kit, ".kit .stt .state"), "text-overflow"), "ellipsis");
});

// ---------------------------------------------------------------------
// DOM side (fake DOM): title/aria full text, and G7
// ---------------------------------------------------------------------

function noop() {}
const ctx = { picked: false, selecting: false, onOpen: noop, onLongPress: noop, onToggle: noop, onAction: noop };
const clients = (...ids) => ids.map((client_id) => ({ client_id, reported_at: "2026-10-14T08:00:00Z" }));

async function withCard(fn) {
  const dom = createFakeDom();
  globalThis.document = dom.document;
  globalThis.window = dom.window;
  return fn(await import("../js/components/game-card.js"));
}

test("G6 MUTATION TARGET: the installed badge's title names EVERY client (the column truncates), and so does the aria-label", () =>
  withCard(({ buildCard }) => {
    const card = buildCard(
      { appid: 7, name: "Aurora", status: "idle", size_bytes: null, installed_on: clients("Demon", "Frame", "Laptop") },
      ctx,
    );
    const badge = card.querySelector(".instbadge");
    assert.equal(badge.title, "Installed but not cached · Demon, Frame, Laptop");
    assert.equal(badge.querySelector(".ibcompact").textContent, "not cached · Demon +2", "visible text unchanged");
    assert.match(card.getAttribute("aria-label"), /Installed on Demon, Frame, Laptop$/);
  }));

test("G6: a patch tick keeps the title in step with the client list", () =>
  withCard(({ buildCard, patchCardVolatile }) => {
    const g = { appid: 8, name: "Aurora", status: "done", size_bytes: 5e8, installed_on: clients("Demon") };
    const card = buildCard(g, ctx);
    assert.equal(card.querySelector(".instbadge").title, "Installed on Demon");
    patchCardVolatile(card, { ...g, installed_on: clients("Demon", "Frame") }, "cached");
    assert.equal(card.querySelector(".instbadge").title, "Installed on Demon, Frame");
  }));

test("G7: .updbadge and .playbadge (they share the update cell) never co-occur, over every state combination", () =>
  withCard(({ buildCard }) => {
    const rows = [];
    for (const status of ["done", "idle", "error"]) {
      for (const size_bytes of [null, 5e8]) {
        for (const update_available of [true, false, undefined]) {
          for (const owned_playtime_forever of [0, 30, undefined]) {
            for (const tool_app of [false, true]) {
              rows.push({ appid: 9, name: "X", status, size_bytes, update_available, owned_playtime_forever, tool_app });
            }
          }
        }
      }
    }
    let upd = 0;
    let play = 0;
    for (const g of rows) {
      for (const liveJob of [undefined, { status: "running", appid: 9, kind: "prefill" }]) {
        const card = buildCard(g, { ...ctx, liveJob });
        const hasUpd = !!card.querySelector(".updbadge");
        const hasPlay = !!card.querySelector(".playbadge");
        if (hasUpd) upd++;
        if (hasPlay) play++;
        assert.equal(hasUpd && hasPlay, false, `both in one cell: ${JSON.stringify(g)} live=${!!liveJob}`);
      }
    }
    assert.ok(upd > 0 && play > 0, "sanity: both badges occur somewhere in the sweep");
  }));
