/**
 * WP LIB-LIST-1 — fixed columns in the library list layout.
 *
 * User feedback on prod rc15: the list row's right side (status word, size,
 * "Update", "not cached · Demon") was a flex line of right-aligned items
 * with varying widths, so nothing lined up from row to row. The fix makes
 * every list row its own CSS grid with the SAME fixed-width tracks
 * (css/app.css "list rows: fixed columns"). No browser runs here, so the
 * layout guarantees are pinned structurally (suite convention, see
 * css-mobile-overflow.test.js); the geometry itself was measured in
 * headless Chromium at 360/720/768/1024/1440 px for the WP report.
 *
 * Guarantees pinned by name:
 *  G1 a list row is a grid and `.meta` dissolves into it (display:contents);
 *  G2 phone template: the name track is the ONLY flexible track besides the
 *     status word, the size cell is a fixed px width;
 *  G3 BP-M template: one line, every track right of the name is a fixed
 *     width (18px icon or a px custom property) — the alignment promise;
 *  G4 every list item that the templates place has an explicit grid-column
 *     and grid-row (a grid child without one is auto-placed into whatever
 *     cell is vacant, LEARNINGS 4h.2);
 *  G5 the lead custom property ends on a track (adjacent line-name groups
 *     make the whole template invalid — measured: everything collapsed);
 *  G6 size right-aligned with tabular figures; installed-on truncates with
 *     an ellipsis, full client list in the badge title and the aria-label;
 *  G7 the update cell's two occupants (`.updbadge`, `.playbadge`) never
 *     co-occur on one card (they share a grid cell);
 *  G8 BP-L shows the installed-on column's full wording.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createFakeDom } from "./fake-dom.js";

const css = readFileSync(new URL("../css/app.css", import.meta.url), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

/** Brace-balanced @media splitter (per-file copy, suite convention). */
function splitMedia(text) {
  let topLevel = "";
  const mediaBlocks = [];
  let i = 0;
  while (i < text.length) {
    const at = text.indexOf("@media", i);
    if (at === -1) {
      topLevel += text.slice(i);
      break;
    }
    topLevel += text.slice(i, at);
    const open = text.indexOf("{", at);
    let depth = 1;
    let j = open + 1;
    while (depth > 0 && j < text.length) {
      if (text[j] === "{") depth++;
      else if (text[j] === "}") depth--;
      j++;
    }
    mediaBlocks.push({ header: text.slice(at, open).trim(), body: text.slice(open + 1, j - 1) });
    i = j;
  }
  return { topLevel, mediaBlocks };
}

/** Joined bodies of every rule whose selector list contains `selector` as
 * one of its comma-separated parts. */
function bodiesFor(text, selector) {
  const out = [];
  const re = /(^|\})\s*([^{}]+)\{([^{}]*)\}/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    const parts = m[2].split(",").map((p) => p.trim().replace(/\s+/g, " "));
    if (parts.includes(selector)) out.push(m[3]);
    re.lastIndex = m.index + m[0].length - 1;
  }
  return out.join(";");
}

/** Last value of `prop` in a declaration string (later wins, like CSS). */
function decl(body, prop) {
  const re = new RegExp(`(?:^|[;{\\s])${prop.replace(/[-]/g, "\\-")}\\s*:\\s*([^;]+)`, "g");
  let last = null;
  let m;
  while ((m = re.exec(body)) !== null) last = m[1].trim();
  return last;
}

const app = splitMedia(css);
const bpM = app.mediaBlocks.find((b) => /min-width:\s*720px/.test(b.header) && !/and/.test(b.header));
const bpL = app.mediaBlocks.find((b) => /min-width:\s*1024px/.test(b.header) && !/and/.test(b.header));
const CARD = ".grid.list .card";

/** Split a grid-template-columns value into its tracks (line-name groups
 * dropped, var() / minmax() kept whole). */
function tracks(value) {
  const out = [];
  let depth = 0;
  let cur = "";
  for (const ch of value.replace(/\[[^\]]*\]/g, " ")) {
    if (ch === "(") depth++;
    if (ch === ")") depth--;
    if (/\s/.test(ch) && depth === 0) {
      if (cur) out.push(cur);
      cur = "";
    } else cur += ch;
  }
  if (cur) out.push(cur);
  return out;
}

/** Resolve `var(--x)` against the custom properties declared in `body`. */
function resolveVar(track, ...bodies) {
  const m = /^var\((--[\w-]+)\)$/.exec(track);
  if (!m) return track;
  for (const body of bodies) {
    const v = decl(body, m[1]);
    if (v !== null) return v;
  }
  return null;
}

test("G1: a list row is a grid; .meta dissolves into it (display:contents), never a flex line", () => {
  const card = bodiesFor(app.topLevel, CARD);
  assert.equal(decl(card, "display"), "grid");
  assert.equal(decl(bodiesFor(app.topLevel, ".grid.list .meta"), "display"), "contents");
  assert.doesNotMatch(card, /flex-direction/, "the old flex-row shape is gone");
});

test("G2: phone template — flexible tracks only for the name/status word, the size cell is a fixed px width", () => {
  const card = bodiesFor(app.topLevel, CARD);
  const cols = tracks(decl(card, "grid-template-columns"));
  // lead var, icon, status word, size
  assert.deepEqual(cols.slice(1), ["18px", "minmax(0,1fr)", "var(--lc-size)"]);
  assert.match(resolveVar("var(--lc-size)", card), /^\d+px$/);
  assert.equal(decl(card, "grid-template-rows"), "1fr auto auto auto auto 1fr");
});

test("G3 MUTATION TARGET: BP-M template — one line, every track right of the name has a fixed px width", () => {
  assert.ok(bpM, "no bare (min-width:720px) block");
  const wide = bodiesFor(bpM.body, CARD);
  const base = bodiesFor(app.topLevel, CARD);
  const cols = tracks(decl(wide, "grid-template-columns"));
  assert.equal(cols[0], "var(--lc-lead)");
  assert.equal(cols[1], "minmax(0,1fr)", "the name track takes all the slack");
  const fixed = cols.slice(2);
  assert.equal(fixed.length, 5, "icon, status, size, update, installed on");
  for (const t of fixed) {
    const v = resolveVar(t, wide, base);
    assert.match(v ?? "", /^\d+px$/, `track ${t} must resolve to a fixed px width, got ${v}`);
  }
});

test("G4: every placed list item has an explicit grid-column and grid-row (phone and BP-M)", () => {
  const items = [
    ".grid.list .cap",
    ".grid.list .pick",
    ".grid.list .rowname",
    ".grid.list .meta > .sic",
    ".grid.list .meta .state",
    ".grid.list .meta .size",
    ".grid.list .updbadge",
    ".grid.list .playbadge",
    ".grid.list .instbadge",
    ".grid.list .pkgline",
  ];
  for (const sel of items) {
    const body = bodiesFor(app.topLevel, sel);
    assert.ok(decl(body, "grid-column"), `${sel}: no grid-column at the top level`);
    assert.ok(decl(body, "grid-row"), `${sel}: no grid-row at the top level`);
  }
  // BP-M moves everything onto the name's line: each item but the cover and
  // the pick (they span all rows at every width) gets its row there.
  for (const sel of items.slice(2)) {
    assert.ok(decl(bodiesFor(bpM.body, sel), "grid-row"), `${sel}: no grid-row in BP-M`);
  }
  // `.meta > .icnact` (the actionable icon) shares the plain icon's rules.
  assert.equal(bodiesFor(app.topLevel, ".grid.list .meta > .icnact"), bodiesFor(app.topLevel, ".grid.list .meta > .sic"));
});

test("G4b: no list item spans the name line and the line under it at BP-M (Chrome grew the empty row and the name sat 4px high)", () => {
  for (const sel of [".grid.list .meta > .sic", ".grid.list .meta .state", ".grid.list .meta .size", ".grid.list .updbadge", ".grid.list .instbadge"]) {
    assert.equal(decl(bodiesFor(bpM.body, sel), "grid-row"), "2", sel);
  }
});

test("G5 MUTATION TARGET: the lead custom property ends on a track, never on a line-name group", () => {
  const leads = [
    decl(bodiesFor(app.topLevel, CARD), "--lc-lead"),
    decl(bodiesFor(app.topLevel, "body.selecting .grid.list .card"), "--lc-lead"),
  ];
  for (const lead of leads) {
    assert.ok(lead, "lead custom property missing");
    assert.doesNotMatch(lead, /\]\s*$/, `--lc-lead "${lead}" ends with line names`);
  }
  // ...and every template that uses it starts the next part with a line name
  // only AFTER the lead's final track, so `[a] [b]` never forms.
  for (const body of [bodiesFor(app.topLevel, CARD), bodiesFor(bpM.body, CARD)]) {
    const tpl = decl(body, "grid-template-columns");
    assert.doesNotMatch(tpl, /\]\s*\[/, "adjacent line-name groups in the template");
  }
  assert.match(leads[1], /\[pick\]/, "select mode adds the pick track");
});

test("G6: size is right-aligned with tabular figures; installed-on truncates with an ellipsis", () => {
  const size = bodiesFor(app.topLevel, ".grid.list .meta .size");
  assert.equal(decl(size, "text-align"), "right");
  assert.match(bodiesFor(app.topLevel, ".meta .size"), /font-variant-numeric:\s*tabular-nums/);
  const inst = bodiesFor(app.topLevel, ".grid.list .instbadge");
  for (const [p, v] of [["display", "block"], ["overflow", "hidden"], ["text-overflow", "ellipsis"], ["white-space", "nowrap"], ["min-width", "0"]]) {
    assert.equal(decl(inst, p), v, `.grid.list .instbadge ${p}`);
  }
  const state = bodiesFor(app.topLevel, ".grid.list .meta .state");
  assert.equal(decl(state, "text-overflow"), "ellipsis");
});

test("G8: BP-L shows the installed-on column's full wording in a wider fixed column", () => {
  assert.ok(bpL, "no bare (min-width:1024px) block");
  assert.match(decl(bodiesFor(bpL.body, CARD), "--lc-inst") ?? "", /^\d+px$/);
  assert.equal(decl(bodiesFor(bpL.body, ".grid.list .instbadge .ibfull"), "display"), "inline");
  assert.equal(decl(bodiesFor(bpL.body, ".grid.list .instbadge .ibcompact"), "display"), "none");
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
