/**
 * Desktop layout foundation (WP 4e.1) — structural CSS pins.
 *
 * No jsdom/browser in web/tests/ (see web/tests/README.md), so this cannot
 * measure real computed layout at a given viewport width (that is done live,
 * against a running vault-api instance in a real browser — see the coder's
 * report for the six-width measurement table). What IS verifiable headlessly,
 * and is exactly the DoD's "structural" claim, is that:
 *
 *   1. every genuinely NEW *layout-shell* rule (the rail, the grid, the
 *      breakpoints themselves) lives inside a `@media (min-width...)` block,
 *      never at the top level. **Correction (Opus review blocker B3, WP
 *      4e.1 fix round): this does NOT mean nothing below BP-M (720px)
 *      changed.** One deliberate top-level fix — `.banner-wrap{width:100%}`,
 *      the shrink-to-fit bug fix — changes real rendering from ~430px up to
 *      719px, because it is not itself gated by any media query (the bug it
 *      fixes exists at every width, not just desktop ones). Measured live
 *      (reviewer, against a `git archive HEAD` pre-WP baseline):
 *        430px viewport: pre-WP 398.3px wide @ x=8.3  -> post-fix 415px @ x=0
 *        719px viewport: pre-WP 398.3px wide @ x=152.8 -> post-fix 704px @ x=0
 *      The ONLY width that is genuinely byte-identical end to end is the
 *      mockup's own 390px (measured: 173.0x259.5 cover tile, unchanged) —
 *      below `.banner-wrap`'s own effective range, and below where any of
 *      this WP's real content starts changing. "Base is untouched" in this
 *      file's test names below means specifically "the SHELL/RAIL/BREAKPOINT
 *      machinery is gated correctly", not "literally nothing renders
 *      differently below 720px" — see the dedicated `.banner-wrap` pin further
 *      down for the one rule that legitimately, deliberately changes it, and
 *      docs/PROJECT_PLAN.md's Phase 4e section for the same correction;
 *   2. the tokens that DO apply at the top level (`--w-text`, `--nav-h`, ...)
 *      still resolve to the exact pixel values the pre-WP-4e.1 literals had
 *      (960px / 64px+14px=78px / 928px), so token substitution alone changed
 *      no rendered pixel;
 *   3. no colour/font/radius token changed (Android's VaultColors literal-
 *      pins them — out of scope for this WP by the brief's own rule);
 *   4. `--w-wall` (the library grid's width ceiling once BP-L applies) does
 *      NOT widen past the pre-WP baseline (960px) in any breakpoint block —
 *      an orchestrator-review anti-regression pin, see the tests near
 *      "--w-wall never widens" below for the full story: a first version of
 *      this package widened it to 1440px/1720px, which measurably made the
 *      mockup's already-oversized fixed-column cover tile BIGGER (458x687 ->
 *      ~815x1222 at 1920px live-measured), because the grid itself is still
 *      the fixed 2/3/list switch, not an auto-fill density grid. The fix is
 *      sequencing (keep the effective width until a later WP's grid can use
 *      it), not scope creep.
 *
 * **The 960px-everywhere era ended with WP 4e.2 — pins updated, not deleted
 * (brief's own instruction).** Point 4 above was true from WP 4e.1 until the
 * auto-fill grid actually existed to use the room; `.grid` (css/app.css's
 * BP-L block) is now `repeat(auto-fill, minmax(var(--tile-min), 1fr))`
 * instead of the fixed 2/3/list switch, so a wider wall buys real columns
 * instead of bigger oversized tiles, and WP 4e.2 raises `--w-wall` to
 * 1600px at BP-L and 2000px at BP-XL. The two tests immediately below this
 * comment ("--w-wall never widens..." and "...computes to the SAME 960px
 * as its own base...") are RENAMED and their assertions updated to the new
 * values in the same place they used to pin 960px — see each test's own
 * comment for why THIS widening is not the regression the old one was.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const webDir = path.join(__dirname, "..");
const appCss = readFileSync(path.join(webDir, "css", "app.css"), "utf8");
const themeCss = readFileSync(path.join(webDir, "css", "theme.css"), "utf8");

function stripComments(css) {
  return css.replace(/\/\*[\s\S]*?\*\//g, "");
}

/** Split `cssText` into { topLevel, mediaBlocks: [{header, body}] } — a
 * single non-recursive pass (no nested @media exist anywhere in this
 * codebase; @supports/@keyframes are left inside `topLevel`/whichever media
 * block contains them, since neither is relevant to this file's assertions). */
function splitMedia(cssText) {
  const css = stripComments(cssText);
  let topLevel = "";
  const mediaBlocks = [];
  let i = 0;
  while (i < css.length) {
    const atIdx = css.indexOf("@media", i);
    if (atIdx === -1) {
      topLevel += css.slice(i);
      break;
    }
    topLevel += css.slice(i, atIdx);
    const openBrace = css.indexOf("{", atIdx);
    const header = css.slice(atIdx, openBrace + 1).trim();
    let depth = 1;
    let j = openBrace + 1;
    while (depth > 0 && j < css.length) {
      if (css[j] === "{") depth++;
      else if (css[j] === "}") depth--;
      j++;
    }
    mediaBlocks.push({ header, body: css.slice(openBrace + 1, j - 1) });
    i = j;
  }
  return { topLevel, mediaBlocks };
}

/** First top-level-selector rule body matching `selector` in `text` (which
 * must itself already be free of @media wrappers — pass a topLevel string or
 * one media block's body). */
function ruleBody(text, selector) {
  const idx = text.indexOf(selector + "{");
  if (idx === -1) return null;
  const openBrace = idx + selector.length;
  let depth = 1;
  let j = openBrace + 1;
  while (depth > 0 && j < text.length) {
    if (text[j] === "{") depth++;
    else if (text[j] === "}") depth--;
    j++;
  }
  return text.slice(openBrace + 1, j - 1);
}

const { topLevel: appTop, mediaBlocks: appMedia } = splitMedia(appCss);
const { topLevel: themeTop } = splitMedia(themeCss);

// ---------------------------------------------------------------------
// 1. New layout machinery lives ONLY inside min-width blocks.
// ---------------------------------------------------------------------

test("app.css's top-level (non-media) text contains no rail/grid layout machinery", () => {
  for (const marker of ["grid-template-areas", "grid-area:rail", "grid-area:top", "grid-area:main", "var(--rail-w)"]) {
    assert.equal(appTop.includes(marker), false, `found "${marker}" outside any @media block`);
  }
});

test("app.css's top-level .nav rule is still the base 3-column bottom bar, untouched in substance", () => {
  const body = ruleBody(appTop, ".nav");
  assert.ok(body, ".nav rule not found at top level");
  assert.ok(body.includes("bottom:0"), "base .nav must still be bottom-pinned");
  assert.ok(body.includes("repeat(3, 1fr)"), "base .nav must still be the 3-column grid");
  assert.ok(body.includes("order:1"), "base .nav needs the D-11 order compensation for the DOM reorder");
});

test("app.css's top-level .chips rule still scrolls horizontally (BP-M's flex-wrap override lives only in its own media block)", () => {
  const body = ruleBody(appTop, ".chips");
  assert.ok(body, ".chips rule not found at top level");
  assert.ok(body.includes("overflow-x:auto"));
  assert.equal(body.includes("flex-wrap:wrap"), false);
});

test("app.css's top-level .app rule is still a plain column flexbox (grid conversion lives only in the BP-L media block)", () => {
  const body = ruleBody(appTop, ".app");
  assert.ok(body, ".app rule not found at top level");
  assert.ok(body.includes("display:flex"));
  assert.equal(body.includes("display:grid"), false);
});

// ---------------------------------------------------------------------
// 2. The three named breakpoints exist with the expected content.
// ---------------------------------------------------------------------

function findMediaBlock(minWidthPx) {
  return appMedia.find((b) => new RegExp(`min-width:\\s*${minWidthPx}px`).test(b.header) && !/and/.test(b.header));
}

test("BP-M (min-width:720px) narrows --w-text to 760px and wraps the filter chips", () => {
  const block = findMediaBlock(720);
  assert.ok(block, "no bare min-width:720px block found");
  assert.match(block.body, /--w-text:\s*760px/);
  const chipsBody = ruleBody(block.body, ".chips");
  assert.ok(chipsBody, ".chips override not found in the BP-M block");
  assert.match(chipsBody, /flex-wrap:\s*wrap/);
});

test("BP-L (min-width:1024px) turns #app into a rail grid and zeroes --nav-h (WITH a unit)", () => {
  const block = findMediaBlock(1024);
  assert.ok(block, "no bare min-width:1024px block found");
  // MUST be "0px", never a bare "0": `.bulk`'s `bottom:calc(var(--nav-h) +
  // 14px)` mixes this token with a length inside calc() — a unitless zero
  // is a <number>, not a <length>, which is a calc() TYPE MISMATCH, invalid
  // at computed-value time, and silently falls back to `bottom:auto` (Opus
  // review blocker B1, WP 4e.1 fix round: measured live, this put the bulk
  // action bar's `bottom` at -143782px — off screen — at every width
  // >=1024px). A regex of just `/0\b/` gets this BACKWARDS: `\b` only sits
  // between "0" and a NON-word character, so `/0\b/` matches the buggy bare
  // "0" (word boundary right after it) but NOT the fixed "0px" ("0" and "p"
  // are both word characters — no boundary between them) — asserting the
  // unit explicitly, as below, is what actually pins the fix.
  assert.match(block.body, /--nav-h:\s*0px\b/);
  const appBody = ruleBody(block.body, ".app");
  assert.ok(appBody, ".app override not found in the BP-L block");
  assert.match(appBody, /display:\s*grid/);
  assert.match(appBody, /grid-template-areas/);
  const navBody = ruleBody(block.body, ".nav");
  assert.ok(navBody, ".nav override not found in the BP-L block");
  assert.match(navBody, /grid-area:\s*rail/);
});

// ---------------------------------------------------------------------
// Anti-regression pin (Opus review blocker B2, WP 4e.1 fix round):
// `index.html` puts `.nav-pip` FIRST inside the Downloads `<nav-btn>`
// (before the icon) — harmless while it is `position:absolute` in the
// base/BP-M layout, but once BP-L makes it `position:static` in a row-
// direction flex container, plain DOM order would render the pip BEFORE
// the icon and push icon+label to the right instead of pinning the pip to
// the trailing edge (measured live: the whole Downloads row sat ~88px out
// of line with Library/Settings). `order` fixes this WITHOUT moving the
// DOM node (`app.js`'s `data-view` wiring, click handling and the `.on`
// toggle are all untouched) — pinned here as a class of "does this survive
// DOM order" bug, not just this instance.
// ---------------------------------------------------------------------

test("BP-L's .nav-pip override sets order:1 so DOM-first no longer means visually-first", () => {
  const block = findMediaBlock(1024);
  const pipBody = ruleBody(block.body, ".nav-pip");
  assert.ok(pipBody, ".nav-pip override not found in the BP-L block");
  assert.match(pipBody, /position:\s*static/);
  assert.match(pipBody, /order:\s*1\b/);
  assert.match(pipBody, /margin-left:\s*auto/);
});

test("BP-L's pointer-only affordance is gated on (hover:hover) and (pointer:fine), not width alone", () => {
  const found = appMedia.some(
    (b) => /min-width:\s*1024px/.test(b.header) && /hover:\s*hover/.test(b.header) && /pointer:\s*fine/.test(b.header),
  );
  assert.ok(found, "no (min-width:1024px) and (hover:hover) and (pointer:fine) block found");
});

test("BP-XL (min-width:1800px) exists, structurally, as a --w-wall hook", () => {
  const block = findMediaBlock(1800);
  assert.ok(block, "no bare min-width:1800px block found");
  assert.match(block.body, /--w-wall:/);
});

// ---------------------------------------------------------------------
// 2b. WP 4e.1's anti-regression pin, UPDATED (not deleted) by WP 4e.2 —
// see this file's header for why the invariant it pinned ("--w-wall never
// widens") was correct THEN and deliberately ends NOW. WP 4e.1 set `--w-wall`
// to 1440px (BP-L) / 1720px (BP-XL) in its first draft — the plumbing (rail,
// breakpoints, tokens) was correct, but because `.grid` was STILL the
// mockup's fixed 2/3/list column switch (not an auto-fill density grid),
// widening `.view-root`'s cap only made the ALREADY-oversized cover tile
// bigger, so the fix was sequencing: keep it flat at 960px everywhere until
// a real density grid existed to use the room. WP 4e.2 ships exactly that
// grid (css/app.css's BP-L block: `.grid{grid-template-columns:repeat(
// auto-fill,minmax(var(--tile-min),1fr))}`), so widening is no longer the
// same regression — this test now pins the NEW, intentional three-value
// progression (960px base -> 1600px BP-L -> 2000px BP-XL) BY POSITION, in
// the same order the two source files are read (theme.css's :root base
// first, then app.css's BP-L block, then its BP-XL block), so a future
// change that silently reorders or drops one of the three still fails
// loudly here rather than passing on a coincidental value match.
// ---------------------------------------------------------------------

test("--w-wall progresses 960px (base) -> 1600px (BP-L) -> 2000px (BP-XL), never re-flattens, never regresses to a value below 1600px past BP-L", () => {
  // Comments stripped first — this file's own explanatory prose quotes the
  // WP 4e.1-rejected 1440px/1720px values verbatim, which would otherwise
  // false-positive as a real declaration under a naive whole-file regex
  // scan. theme.css (the :root base) + app.css (BP-L/BP-XL) combined — the
  // base declaration lives in the OTHER file, `--w-wall` is not
  // app.css-only.
  const codeOnly = stripComments(themeCss) + "\n" + stripComments(appCss);
  const assignments = [...codeOnly.matchAll(/--w-wall\s*:\s*([^;]+);/g)].map((m) => m[1].trim());
  assert.equal(assignments.length, 3, `expected exactly 3 --w-wall assignments (:root base, BP-L, BP-XL), found ${assignments.length}: ${JSON.stringify(assignments)}`);
  const [base, bpL, bpXl] = assignments;
  assert.equal(base, "960px", `theme.css's :root base --w-wall changed to "${base}" — it is dead below BP-L (.view-root uses --w-text there) and has no reason to move`);
  // BP-L's own 1600px is UNREACHABLE within BP-L's own width range (Opus
  // review S2, WP 4e.2 fix round: BP-L's widest viewport is 1799.98px, whose
  // main column is 1799-232=1567px, 33px short of 1600 — measured `cap
  // binding? false` at 1024/1400/1700/1799px, no exception). The message
  // below states that plainly rather than repeating the fix round's original
  // wrong claim ("lands at ~186px @1920, capped" — 1920px is BP-XL's range,
  // where nothing is capped by THIS value at all): what this assertion
  // actually guards is that BP-L's cap stays comfortably above the 1567px
  // ceiling BP-L can ever reach (any value >=1568px is behaviourally
  // identical here), so a future --rail-w/breakpoint change cannot make it
  // start binding by accident. Regressing to a value BELOW ~1568px WOULD
  // start binding within BP-L's own range and reopen the WP 4e.1
  // tile-oversize story from the opposite direction.
  assert.equal(bpL, "1600px", `BP-L --w-wall is "${bpL}", expected 1600px — unreachable within BP-L's own range on purpose (see app.css's BP-L comment), but a value below ~1568px WOULD start binding there`);
  assert.equal(bpXl, "2000px", `BP-XL --w-wall is "${bpXl}", expected 2000px — a second, SEPARATE decision from BP-L's own (not a continuation of it, since BP-L's value never bound anything), deliberately not fully uncapped (see app.css's BP-XL comment)`);
  // N6 (Opus review nitpick, WP 4e.1 fix round) — still true, updated value
  // (Opus review S1, WP 4e.2 fix round: 210px measured 225.6-246.0px live,
  // 1.30x-1.42x the 173px mockup tile — the operator's chosen 176px keeps
  // the range within the accepted ±18% tolerance; see theme.css's
  // `--tile-min` comment for the auto-fill overshoot mechanism this number
  // has to account for). `--tile-min` is now WIRED (WP 4e.2, unlike WP
  // 4e.1's "declared, no consumer yet" state), so an unused-custom-property
  // sweep would no longer delete it on its own reasoning — but this
  // assertion stays, pinning the COMFORTABLE-density base value the BP-L
  // block's `.grid.cols3{--tile-min:150px}` override departs from.
  assert.match(codeOnly, /--tile-min\s*:\s*176px/, "--tile-min's base (comfortable-density) value missing or changed from 176px in theme.css");
});

test(".view-root's BP-L max-width (--w-wall) is 1600px — a deliberate widening from the 960px base, not a repeat of it", () => {
  const block = findMediaBlock(1024);
  const wWallInBlock = /--w-wall\s*:\s*([^;]+);/.exec(block.body);
  assert.ok(wWallInBlock, "--w-wall not (re)declared in the BP-L block");
  assert.equal(wWallInBlock[1].trim(), "1600px");
});

test("the auto-fill library grid is wired at BP-L: .grid and .grid.cols3 both use repeat(auto-fill,minmax(var(--tile-min),1fr)), with DIFFERENT --tile-min values (D-3's density control), and .grid.list is left alone", () => {
  const block = findMediaBlock(1024);
  const gridBody = ruleBody(block.body, ".grid, .grid.cols3");
  assert.ok(gridBody, ".grid, .grid.cols3 combined rule not found in the BP-L block");
  assert.match(gridBody, /grid-template-columns:\s*repeat\(\s*auto-fill\s*,\s*minmax\(\s*var\(--tile-min\)\s*,\s*1fr\s*\)\s*\)/);
  // NOT `ruleBody(block.body, ".grid.cols3")`: that helper does a plain
  // `indexOf(selector + "{")`, and the combined selector line just above
  // (`.grid, .grid.cols3{`) already CONTAINS the substring ".grid.cols3{"
  // inside it (right after the comma) — it would match there first and
  // return the WRONG rule's body (the shared grid-template-columns one, not
  // the density override). Rather than fight that with a fragile
  // "preceded by a non-comma boundary" regex, this pins the exact literal
  // text of the standalone override rule this file's app.css actually
  // contains — a white-box check, deliberately, since the point is that
  // THIS specific rule exists with THIS specific value.
  assert.ok(
    block.body.includes(".grid.cols3{ --tile-min:150px; }"),
    ".grid.cols3's own standalone `{ --tile-min:150px; }` override not found verbatim in the BP-L block — it must override --tile-min to a DIFFERENT (smaller/denser) value than the 176px comfortable default",
  );
  // .grid.list keeps its top-level grid-template-columns:1fr by SPECIFICITY
  // (two classes beats this block's one-class `.grid` rule) — asserting
  // there is no BP-L override for it at all is the structural proof that
  // this is relied on deliberately, not an accidental omission.
  assert.equal(/\.grid\.list\s*\{[^}]*grid-template-columns/.test(block.body), false, ".grid.list must not get its own BP-L grid-template-columns override — it relies on specificity over the plain .grid rule");
});

// ---------------------------------------------------------------------
// Opus review S3, WP 4e.2 fix round: BOTH of this package's "headline
// guarantees" were completely unpinned and two real mutations survived
// 456/456 as a result:
//   (a) deleting the BASE `.grid` rule's `repeat(2,1fr)` (i.e. switching the
//       mockup-frozen PHONE surface itself to auto-fill) — nothing failed;
//   (b) deleting all eight of `.grid.cols3`'s BP-L reset rules (the "fix the
//       tile guarantee made concrete, not merely asserted" comment right
//       above them in app.css) — nothing failed either.
// The house pattern this file already uses for `.nav`/`.chips`/`.app`
// ("app.css's top-level X rule is still ...") is extended to `.grid` here,
// and a new pin restates every one of the eight reset values so deleting
// (or silently drifting) any of them fails by name.
// ---------------------------------------------------------------------

test("app.css's top-level .grid/.grid.cols3 rules are still the base fixed 2/3-column switch with the phone typography, untouched by auto-fill", () => {
  const gridBody = ruleBody(appTop, ".grid");
  assert.ok(gridBody, ".grid rule not found at top level");
  // WP WEB-FIX-3: minmax(0,1fr), not 1fr — the phone grid must not blow out
  // past the viewport on a nowrap card child (css-mobile-overflow.test.js).
  assert.match(gridBody, /grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/, "base .grid must still be the fixed 2-column switch, not auto-fill");
  assert.equal(/auto-fill/.test(gridBody), false, "auto-fill must not appear in the base (phone) .grid rule");

  const cols3Body = ruleBody(appTop, ".grid.cols3");
  assert.ok(cols3Body, ".grid.cols3 rule not found at top level");
  assert.match(cols3Body, /grid-template-columns:\s*repeat\(3,\s*minmax\(0,\s*1fr\)\)/, "base .grid.cols3 must still be the fixed 3-column switch, not auto-fill");
  assert.equal(/auto-fill/.test(cols3Body), false, "auto-fill must not appear in the base (phone) .grid.cols3 rule");

  // The mobile-only compact typography WP 4e.2's BP-L block resets (Opus
  // review S3's other survived mutation targets the RESET, this half pins
  // the thing being reset FROM still exists, unmoved, at the base level).
  const nameBody = ruleBody(appTop, ".grid.cols3 .cap .name");
  assert.ok(nameBody, ".grid.cols3 .cap .name base rule not found");
  assert.match(nameBody, /font-size:\s*11px/, "base .cols3 .cap .name must still be the phone-tuned 11px");
  const pillBody = ruleBody(appTop, ".grid.cols3 .cappill");
  assert.ok(pillBody, ".grid.cols3 .cappill base rule not found");
  assert.match(pillBody, /min-height:\s*20px/, "base .cols3 .cappill must still be the phone-tuned 20px pill");
});

test("BP-L's .grid.cols3 reset restates EVERY one of the base card's own values — deleting any one of the eight reset rules must fail this test by name", () => {
  const block = findMediaBlock(1024);
  // Each check below names the exact base value it is restating (per
  // app.css's own comment: "reset back to the exact base value") — a
  // literal includes(), deliberately, not a derived comparison against the
  // base rule (a derived check could not tell "restated correctly" apart
  // from "restated to something else that happens to also be right").
  const expectedResets = [
    ".grid.cols3 .cap{ border-radius:var(--r-m); }",
    ".grid.cols3 .cap .name{ left:9px; right:9px; bottom:8px; font-size:14.5px; letter-spacing:.4px; }",
    ".grid.cols3 .cappill{ left:7px; top:7px; min-height:23px; padding:3px 8px 3px 3px; gap:5px; font-size:10.5px; }",
    ".grid.cols3 .cappill .sic{ --sz:17px; }",
    ".grid.cols3 .card{ gap:7px; }",
    ".grid.cols3 .meta{ gap:6px; font-size:11px; }",
    ".grid.cols3 .pick{ top:7px; right:7px; width:21px; height:21px; }",
  ];
  for (const rule of expectedResets) {
    assert.ok(block.body.includes(rule), `missing or changed BP-L .grid.cols3 reset rule: ${rule}`);
  }
  // `.grid.cols3 .meta .size` has NO reset rule at all (Opus nitpick, WP
  // 4e.2 fix round: it inherits font-size from `.meta` above, restating it
  // would just be redundant) — assert that absence is deliberate, not a
  // ninth rule quietly missing from the list above.
  assert.equal(
    block.body.includes(".grid.cols3 .meta .size{"),
    false,
    ".grid.cols3 .meta .size must NOT have its own BP-L reset rule — it inherits font-size from the .meta reset instead",
  );
});

test(".bulk is re-derived from --w-wall (not --w-text) at BP-L, with rail/gutter-based insets instead of the base's own 12px", () => {
  const block = findMediaBlock(1024);
  const bulkBody = ruleBody(block.body, ".bulk");
  assert.ok(bulkBody, ".bulk override not found in the BP-L block");
  assert.match(bulkBody, /max-width:\s*calc\(var\(--w-wall\)\s*-\s*\(var\(--gutter\)\s*\*\s*2\)\)/);
  assert.match(bulkBody, /left:\s*calc\(var\(--rail-w\)\s*\+\s*var\(--gutter\)\)/);
  assert.match(bulkBody, /right:\s*var\(--gutter\)/);
  assert.equal(/--w-text/.test(bulkBody), false, ".bulk's BP-L override must not reference --w-text any more");
});

test(".view-library becomes a named-area grid at BP-L, assigning all six in-flow children BY CLASS (never nth-child), search capped at --search-w", () => {
  const block = findMediaBlock(1024);
  const body = ruleBody(block.body, ".view-library");
  assert.ok(body, ".view-library rule not found in the BP-L block");
  assert.match(body, /display:\s*grid/);
  // Opus review S4: the ORIGINAL version of this test only checked that
  // `--search-w` is DECLARED (theme.css) and that `.search` gets
  // `grid-area:search` — it named the capping mechanism without ever
  // asserting it. Two real mutations survived as a result: changing the
  // grid's own column template from `var(--search-w) 1fr` to `1fr 1fr`
  // (the cap silently disappears — the input becomes ~976px wide at
  // 2560px) and swapping the area map's "search chips" row to "chips
  // search" (search and chips silently trade places). Both are pinned by
  // name below.
  assert.match(
    body,
    /grid-template-columns:\s*var\(--search-w\)\s+1fr\b/,
    ".view-library's BP-L grid-template-columns must be `var(--search-w) 1fr` — search's column-track width is the actual cap, not merely --search-w's existence somewhere in the file",
  );
  assert.match(body, /grid-template-areas/);
  // The exact row order/content of the area map, not just that AN area map
  // exists — pins search-before-chips (never swapped) and every other row
  // spanning both columns.
  assert.match(body, /"head\s+head"/, 'the "head head" area row is missing or changed');
  assert.match(body, /"check\s+check"/, 'the "check check" area row is missing or changed');
  assert.match(body, /"search\s+chips"/, 'the "search chips" area row is missing, changed, or reordered to "chips search"');
  assert.match(body, /"cards\s+cards"/, 'the "cards cards" area row is missing or changed');
  assert.match(body, /"hint\s+hint"/, 'the "hint hint" area row is missing or changed');
  for (const cls of ["lib-head", "lib-checkrow", "search", "chips", "grid", "hint"]) {
    const selector = `.view-library > .${cls}`;
    assert.ok(block.body.includes(selector), `no "${selector}" area-assignment rule found in the BP-L block`);
  }
  assert.equal(/nth-child/.test(block.body), false, "grid-area assignment must never use nth-child (brief: class-based, order-independent)");
  const searchBody = ruleBody(block.body, ".view-library > .search");
  assert.ok(searchBody, ".view-library > .search rule missing");
  assert.match(searchBody, /grid-area:\s*search/);
});

test("theme.css declares --search-w (the library search field's BP-L cap)", () => {
  assert.match(themeTop, /--search-w\s*:\s*420px/);
});

// ---------------------------------------------------------------------
// Opus review blocker B1, WP 4e.2 fix round: `renderEmptyState`
// (library.js) / `emptyMessage` (downloads.js) append `<p class="empty">`
// as a direct child of whatever container they're building the "no
// results" fallback for — for library.js, that container is `.grid`, whose
// BP-L rule is now `auto-fill`. Unlike `.noresult` (already `grid-column:
// 1/-1`), `.empty` had no such rule, so it collapsed into a single
// auto-fill track instead of spanning the row — measured live: an
// 8.5%-of-row-wide block hard against the left edge at 2560px, reachable
// any time a filter/chip selection matches zero games (one click away:
// `renderChips` renders every chip including zero-count ones).
//
// **This is a CORRECTION, not a new divergence (Opus review, second fix
// round): the frozen mockup already has `grid-column:1/-1` inline on this
// exact element** (`docs/design/vault-app-mockup.html:1826`), while its
// other two `.empty` uses carry no such style. The WP 4a.3 port dropped the
// inline style when translating the mockup's phone-frame markup into this
// codebase's stylesheet; restoring it here brings this element back in
// line with the frozen source, it does not invent new behaviour the mockup
// never had.
// ---------------------------------------------------------------------

test(".empty spans the full grid row at BP-L (grid-column:1/-1), matching .noresult's existing behaviour and the mockup's own inline style", () => {
  const body = ruleBody(appTop, ".empty");
  assert.ok(body, ".empty rule not found at top level");
  assert.match(body, /grid-column:\s*1\s*\/\s*-1/, ".empty must span the full row (grid-column:1/-1) — without it, a zero-result auto-fill grid collapses it into one narrow track");
});

// ---------------------------------------------------------------------
// Opus review S7, WP 4e.2 second fix round: the test above is CLASS-
// specific — it only proves `.empty` itself is fine, and says nothing
// about a future third fallback type appended into `.grid` under some new
// class. Generalised per the review's own bar ("fixing the class rather
// than the instance"): this scans `library.js` for every class its code
// appends as a DIRECT CHILD of `.grid` (via `els.grid.appendChild(...)`),
// excludes `buildCard`'s output (`card` — the grid's actual cells, the
// point of the grid, not a fallback that needs to span it), and requires
// every remaining class to carry `grid-column` somewhere in app.css. Two
// such classes exist today (`.noresult`, `.empty`, both from
// `renderEmptyState()`) and both are already correct — this test is
// "fixing the class", not re-asserting the two known instances.
//
// Scope, stated honestly (same posture as css-hygiene.test.js's own
// documented limitations): this walks ONE function (`renderGrid`) for
// `els.grid.appendChild(<call>)` sites, resolves `<call>`'s callee to a
// function definition in the SAME file, and collects every literal
// `x.className = "..."` assignment in that callee's body. It does not
// trace into a THIRD level of indirection, and a callee assembling its
// className from a non-literal expression (template interpolation,
// concatenation with a variable) would not be seen — not exploited by any
// real code here as of this WP.
// ---------------------------------------------------------------------

test("every non-card class appended as a direct child of .grid (library.js) carries grid-column in app.css", () => {
  const libraryJsPath = path.join(webDir, "js", "views", "library.js");
  const libraryJs = readFileSync(libraryJsPath, "utf8");

  const renderGridMatch = /function renderGrid\(\)\s*\{([\s\S]*?)\n\}/.exec(libraryJs);
  assert.ok(renderGridMatch, "renderGrid() not found in library.js — did it move or get renamed?");
  const renderGridBody = renderGridMatch[1];

  const calleeNames = new Set();
  for (const m of renderGridBody.matchAll(/els\.grid\.appendChild\(\s*\n?\s*(\w+)\(/g)) {
    calleeNames.add(m[1]);
  }
  assert.ok(calleeNames.size > 0, "no els.grid.appendChild(...) call sites found in renderGrid() — did the DOM-construction pattern change?");
  // `buildCard`'s output IS the grid's actual content, not a fallback — the
  // one deliberate exclusion, named so a future removal of this line is a
  // visible decision, not a silent narrowing of what this test checks.
  calleeNames.delete("buildCard");
  assert.ok(calleeNames.size > 0, "expected at least one non-buildCard callee (e.g. renderEmptyState) appended into .grid");

  const foundClasses = new Set();
  for (const calleeName of calleeNames) {
    const fnMatch = new RegExp(`function ${calleeName}\\([^)]*\\)\\s*\\{([\\s\\S]*?)\\n\\}`).exec(libraryJs);
    assert.ok(fnMatch, `callee "${calleeName}" (appended into .grid) has no matching function definition in library.js`);
    for (const m of fnMatch[1].matchAll(/\.className\s*=\s*["']([\w-]+)["']/g)) {
      foundClasses.add(m[1]);
    }
  }
  assert.ok(foundClasses.size > 0, "no literal className assignments found in the resolved callee(s) — the scan's own sanity check");

  const missing = [];
  for (const cls of foundClasses) {
    const body = ruleBody(appTop, `.${cls}`);
    if (!body || !/grid-column\s*:/.test(body)) missing.push(cls);
  }
  assert.deepEqual(
    missing,
    [],
    `class(es) appended as a direct child of .grid with no grid-column rule: ${JSON.stringify(missing)} — under BP-L's auto-fill rule, each would collapse into one narrow track instead of spanning the row`,
  );
});

// ---------------------------------------------------------------------
// 2c. Anti-regression pin (Opus review blocker B1, WP 4e.1 fix round): a
// UNITLESS `--nav-h` assignment is invalid the moment it meets `.bulk`'s
// `calc(var(--nav-h) + 14px)` — see app.css's BP-L block comment for the
// full mechanism (calc() type mismatch -> invalid at computed-value time ->
// `bottom:auto` -> the bulk action bar lands off screen at its static-flow
// position). This pins the CLASS of bug (every assignment must carry a
// unit), not just the one instance already fixed.
// ---------------------------------------------------------------------

test("every --nav-h assignment anywhere (theme.css + app.css) carries an explicit px unit", () => {
  const codeOnly = stripComments(themeCss) + "\n" + stripComments(appCss);
  const assignments = [...codeOnly.matchAll(/--nav-h\s*:\s*([^;]+);/g)].map((m) => m[1].trim());
  assert.ok(assignments.length >= 2, `expected --nav-h assigned at :root and BP-L, found ${assignments.length}`);
  for (const value of assignments) {
    assert.match(value, /^\d+px$/, `--nav-h assigned "${value}" — must be a unitful px length (a bare number breaks .bulk's calc())`);
  }
});

// ---------------------------------------------------------------------
// 2d. Should-fix S4 (Opus review, WP 4e.1 fix round): `.banner-wrap` and
// `.view-root` sit in the same visual column at BP-L (grid areas "banner"
// then "main") and must share ONE width cap, or a mismatched pair of
// independently-centred boxes reads as a layout bug the first time a human
// sees it (measured live: left edges 100px apart before this fix).
// ---------------------------------------------------------------------

test(".banner-wrap picks up --w-wall (not --w-text) at BP-L, matching .view-root's own cap exactly", () => {
  const block = findMediaBlock(1024);
  const bannerBody = ruleBody(block.body, ".banner-wrap");
  assert.ok(bannerBody, ".banner-wrap override not found in the BP-L block");
  assert.match(bannerBody, /max-width:\s*var\(--w-wall\)/);
});

// ---------------------------------------------------------------------
// 3. Token values at the top level reproduce the exact pre-WP-4e.1 pixel
//    values, and no colour/font/radius token changed.
// ---------------------------------------------------------------------

test(":root's base --w-text is 960px (the exact literal .view-root/.onb/.banner-wrap all hard-coded before this WP)", () => {
  assert.match(themeTop, /--w-text:\s*960px/);
});

test(":root's base --nav-h + 14px reproduces the old hand-derived .bulk bottom:78px", () => {
  const m = /--nav-h:\s*(\d+)px/.exec(themeTop);
  assert.ok(m, "--nav-h not found at top level");
  assert.equal(Number(m[1]) + 14, 78);
});

test(".bulk's max-width derives from --w-text/--gutter instead of repeating a second literal (928)", () => {
  const body = ruleBody(appTop, ".bulk");
  assert.ok(body, ".bulk rule not found at top level");
  assert.match(body, /max-width:\s*calc\(var\(--w-text\)\s*-\s*\(var\(--gutter\)\s*\*\s*2\)\)/);
  assert.equal(/928px/.test(body), false, "928 must not be repeated as a second literal");
});

test(".view-root/.onb/.banner-wrap reference --w-text, never a bare 960px literal, at the top level", () => {
  for (const selector of [".view-root", ".onb", ".banner-wrap"]) {
    const body = ruleBody(appTop, selector);
    assert.ok(body, `${selector} rule not found at top level`);
    assert.match(body, /var\(--w-text\)/, `${selector} does not reference --w-text`);
    assert.equal(/960px/.test(body), false, `${selector} still hard-codes 960px`);
  }
});

// This is the ONE top-level (non-`@media`) rule this WP changed that alters
// rendering below BP-M — see this file's header correction (Opus review
// blocker B3). It must not silently revert to the shrink-to-fit bug: without
// `width:100%`, `margin:0 auto`'s auto CROSS-axis margin on this flex item
// disables `align-items:stretch`, and the box falls back to a fixed
// ~366-398px content width regardless of viewport (measured live against a
// pre-WP baseline: 398.3px @ x=8.3 at 430px viewport, 398.3px @ x=152.8 at
// 719px — same width, different centring offset, i.e. genuinely
// shrink-to-fit, not merely narrow).
test(".banner-wrap has width:100% (the shrink-to-fit fix — must never silently revert) and still carries no `display` declaration of its own", () => {
  const body = ruleBody(appTop, ".banner-wrap");
  assert.ok(body);
  assert.match(body, /width:\s*100%/);
  assert.equal(/(^|[;{\s])display\s*:/.test(body), false, "`.banner-wrap` must stay free of an author `display` rule — it is [hidden]-toggled");
});

// ---------------------------------------------------------------------
// 4. WP 4e.6 — the rail narrowed (232px -> 180px) and gained head/foot
// content. A drift on either half of this deserves its own named failure:
// the WIDTH regressing silently undoes the operator's own measured verdict
// ("232px feels unnecessarily large for the three items in it"), and the
// CONTENT regressing (showing on the phone bottom bar, or never being
// switched back on at BP-L) is the exact class of bug B1/B2 already found
// once in this same shell-layout package (WP 4e.1 fix round) — re-check
// every fixed/overlay surface a shell change touches, not just the one the
// diff's own description names.
// ---------------------------------------------------------------------

test("theme.css's --rail-w is 180px, not the WP 4e.1 232px — a drift here must fail", () => {
  assert.match(themeTop, /--rail-w\s*:\s*180px\b/, "--rail-w changed from the WP 4e.6 operator-verdict value (180px) or lost its unit");
  // Only ONE assignment should exist (unlike --nav-h/--w-wall, --rail-w is
  // never redeclared per breakpoint — it is a single, width-independent
  // rail dimension) — a second assignment slipping in elsewhere would mean
  // two sources of truth for the same token.
  const assignments = [...stripComments(themeCss).matchAll(/--rail-w\s*:\s*([^;]+);/g)];
  assert.equal(assignments.length, 1, `expected exactly one --rail-w assignment, found ${assignments.length}`);
});

test("app.css's top-level .rail-head/.rail-foot are display:none (phone/tablet chrome never shows rail content)", () => {
  for (const selector of [".rail-head", ".rail-foot"]) {
    const body = ruleBody(appTop, selector);
    assert.ok(body, `${selector} rule not found at top level`);
    assert.match(body, /display\s*:\s*none\b/, `${selector} must be display:none below BP-L`);
  }
});

test("BP-L switches .rail-head/.rail-foot back to display:block, and .rail-foot pins itself to the rail's bottom edge", () => {
  const block = findMediaBlock(1024);
  const headBody = ruleBody(block.body, ".rail-head");
  assert.ok(headBody, ".rail-head override not found in the BP-L block");
  assert.match(headBody, /display\s*:\s*block\b/);
  const footBody = ruleBody(block.body, ".rail-foot");
  assert.ok(footBody, ".rail-foot override not found in the BP-L block");
  assert.match(footBody, /display\s*:\s*block\b/);
  assert.match(
    footBody,
    /margin-top\s*:\s*auto\b/,
    ".rail-foot must use margin-top:auto to reach the bottom of .nav's BP-L flex column — without it, it would sit directly under the nav buttons instead of at the rail's foot",
  );
});

// ---------------------------------------------------------------------
// Opus review should-fix S1 (WP 4e.6 review round): the app.css BP-L
// --w-wall comment's own "1600px is unreachable at this breakpoint" claim
// went stale the moment --rail-w narrowed in the SAME package — at 180px it
// DOES bind (measured live: .view-root computes to exactly 1600px, capped,
// at a 1799px viewport). The comment was corrected, but a comment alone is
// exactly the failure mode this phase keeps re-learning ("a comment that
// explains a value must be verifiable from the value"): this test computes
// the SAME breakeven arithmetic from the live token values, so a future
// --rail-w (or --w-wall) change that flips "does the cap bind at BP-L's own
// widest viewport" fails a named test instead of leaving a silently stale
// sentence.
// ---------------------------------------------------------------------

test("BP-L's --w-wall cap binding status matches the current --rail-w — computed from the live tokens, not hand-asserted", () => {
  const railWMatch = /--rail-w\s*:\s*(\d+)px/.exec(themeTop);
  assert.ok(railWMatch, "--rail-w not found in theme.css's :root");
  const railW = Number(railWMatch[1]);

  const bpLBlock = findMediaBlock(1024);
  const wWallMatch = /--w-wall\s*:\s*(\d+)px/.exec(bpLBlock.body);
  assert.ok(wWallMatch, "--w-wall not (re)declared in the BP-L block");
  const wWallBpL = Number(wWallMatch[1]);

  // Constants measured, not tokens: BP-L's own widest possible viewport is
  // 1799.98px (BP-XL takes over at exactly 1800px), and >=768px viewports
  // reserve 15px for a classic scrollbar (N3, WP 4e.1/4e.2's six-width
  // tables) whenever content overflows vertically, which the library view
  // always does past a handful of games.
  const BP_L_MAX_VIEWPORT = 1799.98;
  const SCROLLBAR_PX = 15;
  const bpLMaxColumn = BP_L_MAX_VIEWPORT - railW - SCROLLBAR_PX;
  const capBinds = bpLMaxColumn > wWallBpL;

  // Pinned to the CURRENT shipped reality (WP 4e.6: 180px rail, 1600px
  // wall -> binds), not to whichever direction happened to be true when
  // this test was written, so the failure message always names what to
  // re-check if it ever flips.
  assert.equal(
    capBinds,
    true,
    `Expected BP-L's --w-wall cap to BIND at --rail-w:${railW}px (max column ` +
      `${bpLMaxColumn}px vs --w-wall ${wWallBpL}px) — if this is false, either ` +
      `--rail-w widened back past ~185px or --w-wall changed, and app.css's ` +
      `BP-L comment (the "1600px is UNREACHABLE" paragraph corrected in WP ` +
      `4e.6) needs re-checking against the new numbers either way`,
  );
});

test("no colour/font/radius :root token changed — every original hex/font/radius value from theme.css is still present verbatim", () => {
  const mustStillContain = [
    "--bg:#0A1016", "--surface:#111C24", "--raised:#17252F", "--raised-2:#1D303C",
    "--line:#24363F", "--line-soft:#1B2A33", "--text:#E4EFF3", "--dim:#8AA1AD", "--dim-2:#63798A",
    "--accent:#2ED9CE", "--accent-deep:#12A79F", "--accent-ink:#04211F",
    "--ok:#57DD8A", "--run:#F2CE5B", "--stale:#F07B2E", "--none:#8296A6", "--danger:#FF6F6F",
    "--r-s:8px", "--r-m:12px", "--r-l:18px", "--r-xl:26px",
  ];
  for (const literal of mustStillContain) {
    assert.ok(themeTop.includes(literal), `theme.css's :root no longer contains "${literal}"`);
  }
});
