/**
 * Desktop overlay geometry (WP 4e.3) — structural CSS pins, plus one live
 * (fake-DOM) pin for the nesting/Escape-ordering claim.
 *
 * The problem (operator's own words, at 2560x1440): every overlay in this
 * app is a bottom sheet ported straight from the round-7 mockup's 390px
 * phone frame (`css/app.css`'s "sheets" section, `.sheet-backdrop`/`.sheet`)
 * — on a desktop-width shell that renders as a ~480px card glued to the
 * bottom edge, far from whatever control opened it. The operator's decision
 * (`docs/PROJECT_PLAN.md`'s Phase 4e section): the game detail sheet becomes
 * a CENTRED CARD at eye level; the notifications panel and the clients sheet
 * become a DRAWER at the right edge; mobile keeps its bottom sheets,
 * unchanged. No motion anywhere in this — every overlay in this app,
 * including these two variants, is a plain `display` toggle with no
 * transition (Opus review, WP 4e.3 fix round: this file's own comments used
 * to say "sliding in", which overclaimed an animation `css/app.css` does not
 * have — fixed in both places). `sheet-dialog.js`'s new `variant` option
 * ("center" | "drawer") appends a second, static class at construction time
 * — see that module's header for why this is presentation only, not a
 * second state model (the existing `lib/modal-stack.js` push/pop/Escape
 * machinery is completely unaware of it).
 *
 * No jsdom/browser here (see web/tests/README.md) — the same "structural
 * pin, not a real layout measurement" posture `css-layout-foundation.test.js`
 * already documents for the exact same reason. What IS verified live, in a
 * real browser against a copy of this tree, is recorded in the coder's
 * report (Opus review, WP 4e.3: headless-Chrome screenshots at
 * 1024/1184/1424/2544px, which is what actually caught blocker B1 below —
 * every structural pin in this file passed while B1 shipped, because the
 * dialog/sheet centring MISMATCH it describes is invisible to a parser that
 * only ever reads one selector's rule body at a time).
 *
 * **Naming note (Opus review S3, WP 4e.3 fix round).** Two tests below used
 * to claim "byte-identical to the pre-WP rule" — factually true of the
 * SHIPPED diff (this WP's changes to `app.css` are purely additive, `git
 * diff --stat` shows 0 deletions, so nothing outside the new `@media` rules
 * could have changed), but not what these specific PINS check: the reviewer
 * added two unrelated properties to the base `.sheet-backdrop` rule and all
 * tests here stayed green, because a regex-based pin asserts specific
 * property VALUES are present, not that the rule contains nothing else.
 * Renamed to state exactly that ("keeps every pre-WP property value") rather
 * than switching to a full literal-text comparison against stored rule
 * bodies — this codebase's existing CSS test files (`css-layout-foundation.
 * test.js`) already establish value-pin, not byte-snapshot, as the house
 * pattern, and a byte-snapshot pin would need its own maintenance every time
 * an unrelated property is legitimately added to one of these shared rules.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { createFakeDom, fakeKeyEvent } from "./fake-dom.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const webDir = path.join(__dirname, "..");
const appCss = readFileSync(path.join(webDir, "css", "app.css"), "utf8");
const themeCss = readFileSync(path.join(webDir, "css", "theme.css"), "utf8");

function stripComments(css) {
  return css.replace(/\/\*[\s\S]*?\*\//g, "");
}

/** Same brace-balanced @media splitter css-layout-foundation.test.js uses —
 * duplicated locally rather than imported, since neither file exports test
 * helpers (matches this codebase's existing convention: each CSS test file
 * carries its own small parsing utilities). */
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

/** First top-level-selector rule body matching `selector` in `text`. */
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

/** The media block containing the shell's own rail conversion
 * (`grid-area:rail`, `css-layout-foundation.test.js`'s established BP-L
 * marker) — used below to prove the overlay-geometry rules live in the
 * SAME breakpoint as the shell change that motivates them, without this
 * file hand-typing "1024" a second time (the brief's own ask: "computed
 * from live tokens, not a hand-typed pixel number"). */
const railBlock = appMedia.find((b) => /grid-area:\s*rail\b/.test(b.body));

// ---------------------------------------------------------------------
// 0. Sanity: the block this file keys everything off actually exists and
// is the one bare min-width block (no `and`), same shape
// css-layout-foundation.test.js's own findMediaBlock(1024) resolves to.
// ---------------------------------------------------------------------

test("sanity: a media block containing the shell's rail conversion exists (the breakpoint every assertion below is anchored to)", () => {
  assert.ok(railBlock, "no @media block containing grid-area:rail found — did the shell conversion move?");
  assert.match(railBlock.header, /min-width\s*:\s*\d+px/);
  assert.equal(/\band\b/.test(railBlock.header), false, "expected the BARE min-width block, not a compound (hover:hover) one");
});

// ---------------------------------------------------------------------
// 1. The variant classes exist ONLY inside that same block — never at the
// top level (mobile/base must stay untouched structurally, same "new
// layout machinery lives only inside @media" pattern
// css-layout-foundation.test.js already established for the rail/grid).
// ---------------------------------------------------------------------

test("app.css's top-level (non-media) text contains none of the new overlay-geometry variant selectors", () => {
  for (const marker of [
    "sheet-backdrop--center",
    "sheet--center",
    "sheet-backdrop--drawer",
    "sheet--drawer",
  ]) {
    assert.equal(appTop.includes(marker), false, `found "${marker}" outside any @media block`);
  }
});

test("the overlay-geometry variant rules live in the EXACT SAME media block as the shell's rail conversion, not an independently-chosen breakpoint", () => {
  for (const selector of [".sheet-backdrop--center{", ".sheet--center{", ".sheet-backdrop--drawer{", ".sheet--drawer{"]) {
    assert.ok(
      railBlock.body.includes(selector),
      `"${selector}" not found in the same @media block as grid-area:rail — the overlay geometry switch must happen in lockstep with the shell's own phone->desktop conversion`,
    );
  }
});

// ---------------------------------------------------------------------
// 2. Mobile/base untouched — the base .sheet-backdrop/.sheet rules keep
// every literal value the pre-WP bottom-sheet geometry had, and gain none
// of the new variants' properties (which would mean a base rule started
// leaking desktop-only behaviour to every width).
// ---------------------------------------------------------------------

test("base .sheet-backdrop keeps every pre-WP property value (bottom-anchored, full-viewport scrim)", () => {
  const body = ruleBody(appTop, ".sheet-backdrop");
  assert.ok(body, ".sheet-backdrop rule not found at top level");
  assert.match(body, /position:\s*fixed;\s*inset:\s*0;\s*z-index:\s*40/);
  assert.match(body, /align-items:\s*flex-end/, "base sheet-backdrop must still bottom-align (the mobile bottom-sheet shape)");
  assert.match(body, /justify-content:\s*center/);
  for (const marker of ["align-items:center", "align-items:stretch", "justify-content:flex-end", "padding-left"]) {
    assert.equal(body.includes(marker), false, `base .sheet-backdrop unexpectedly contains "${marker}" — a desktop-only rule leaked into the shared base`);
  }
});

test("base .sheet keeps every pre-WP property value (the slide-up-from-bottom card shape)", () => {
  const body = ruleBody(appTop, ".sheet");
  assert.ok(body, ".sheet rule not found at top level");
  assert.match(body, /max-width:\s*var\(--w-sheet\)/);
  assert.match(body, /max-height:\s*88vh/);
  assert.match(body, /border-top-left-radius:\s*var\(--r-xl\)/);
  assert.match(body, /border-top-right-radius:\s*var\(--r-xl\)/);
  assert.match(body, /border:\s*1px solid var\(--line\);\s*border-bottom:\s*none/);
  assert.match(body, /box-shadow:\s*0 -18px 40px -12px rgba\(0,0,0,\.7\)/, "base .sheet must keep the upward-slide shadow direction");
  for (const marker of ["border-bottom-left-radius", "border-bottom-right-radius", "border-right:none", "max-height:100vh"]) {
    assert.equal(body.includes(marker), false, `base .sheet unexpectedly contains "${marker}" — a desktop-only rule leaked into the shared base`);
  }
});

test("base .grab (the drag-to-dismiss handle) is not hidden at the top level — only the desktop card/drawer variants hide it", () => {
  const body = ruleBody(appTop, ".sheet .grab");
  assert.ok(body, ".sheet .grab rule not found at top level");
  assert.equal(/display\s*:\s*none/.test(body), false, "base .grab must stay visible below BP-L");
});

// ---------------------------------------------------------------------
// 3. Centred card (game detail sheet) — the actual geometry values.
// ---------------------------------------------------------------------

test("BP-L's .sheet-backdrop--center centres vertically and insets the flex content box by --rail-w (centres in the CONTENT area, not the full viewport)", () => {
  const body = ruleBody(railBlock.body, ".sheet-backdrop--center");
  assert.ok(body, ".sheet-backdrop--center override not found in the rail-conversion media block");
  assert.match(body, /align-items:\s*center/);
  assert.match(
    body,
    /padding-left:\s*var\(--rail-w\)/,
    "must inset the flex content box by --rail-w, not merely centre — centring on the full viewport would sit left of the content area's own centre by half the rail's width",
  );
  // Must NOT narrow the backdrop's own box (e.g. `left:var(--rail-w)`
  // instead of padding) — that would leave the rail undimmed while an
  // inert modal is open, breaking the "nothing behind an open modal is
  // reachable" contract every other overlay in this app honours.
  assert.equal(/(^|[;{\s])left\s*:/.test(body), false, ".sheet-backdrop--center must not narrow its own box via `left` — only `padding-left` shifts the centring math while keeping the scrim full-viewport");
});

test("BP-L's .sheet--center rounds all four corners, restores a real bottom border/shadow, and hides the grab handle", () => {
  const body = ruleBody(railBlock.body, ".sheet--center");
  assert.ok(body, ".sheet--center override not found in the rail-conversion media block");
  assert.match(body, /border-bottom-left-radius:\s*var\(--r-xl\)/);
  assert.match(body, /border-bottom-right-radius:\s*var\(--r-xl\)/);
  assert.match(body, /border-bottom:\s*1px solid var\(--line\)/, "a centred card needs a real bottom edge — the base rule's border-bottom:none assumed it always sits flush against the viewport bottom");
  assert.match(body, /box-shadow:\s*0 24px 50px/, "must use an all-around card shadow, not the base rule's upward-slide-only shadow");
  const grabBody = ruleBody(railBlock.body, ".sheet--center .grab");
  assert.ok(grabBody, ".sheet--center .grab override not found");
  assert.match(grabBody, /display\s*:\s*none/);
});

// ---------------------------------------------------------------------
// 3b. Operator decision (WP 4e.3 fix round 2, 2026-08-18): the centred
// card's width genuinely widens at BP-L — the plan item's "sizing" half,
// delivered via a DEDICATED token (`--w-sheet-l`, theme.css), not a literal
// inside `.sheet--center` and not a redefinition of the shared `--w-sheet`
// (which the drawer, and every other sheet, still needs at 480px). Both
// directions mutation-tested: removing the token, and pointing the centred
// card back at `--w-sheet`, must each die by name here.
// ---------------------------------------------------------------------

test("theme.css declares --w-sheet-l (the centred card's BP-L width), distinct from --w-sheet", () => {
  const themeTop = splitMedia(themeCss).topLevel;
  const match = /--w-sheet-l\s*:\s*(\d+)px/.exec(themeTop);
  assert.ok(match, "--w-sheet-l not found in theme.css's :root, or missing its px unit");
  const widthPx = Number(match[1]);
  // The operator's approved range, not a re-derivation of the exact value
  // (theme.css's own comment carries the character-per-line arithmetic that
  // picked 680 specifically within it) — this pin guards the RANGE staying
  // sane, not the exact number, so a deliberate future re-tune within the
  // approved band does not need to touch this test.
  assert.ok(widthPx >= 680 && widthPx <= 760, `--w-sheet-l is ${widthPx}px, expected 680-760px (the operator-approved range)`);
  const wSheetMatch = /--w-sheet\s*:\s*(\d+)px/.exec(themeTop);
  assert.ok(wSheetMatch, "--w-sheet not found in theme.css's :root");
  assert.notEqual(widthPx, Number(wSheetMatch[1]), "--w-sheet-l must differ from --w-sheet, or the centred card and the drawer/every other sheet cannot have different BP-L widths");
});

test("BP-L's .sheet--center max-width comes from --w-sheet-l, not --w-sheet", () => {
  const body = ruleBody(railBlock.body, ".sheet--center");
  assert.ok(body, ".sheet--center override not found in the rail-conversion media block");
  assert.match(
    body,
    /max-width:\s*var\(--w-sheet-l\)/,
    ".sheet--center has no max-width:var(--w-sheet-l) override — the centred card must widen at BP-L using the dedicated token, not stay on the base rule's --w-sheet",
  );
  assert.equal(
    /max-width:\s*var\(--w-sheet\)\s*;/.test(body),
    false,
    ".sheet--center must not point back at the shared --w-sheet — that is exactly the reverted-decision mutation this test guards against",
  );
});

test("BP-L's .sheet--drawer keeps the base rule's --w-sheet width — no BP-L max-width override of its own", () => {
  const body = ruleBody(railBlock.body, ".sheet--drawer");
  assert.ok(body, ".sheet--drawer override not found in the rail-conversion media block");
  assert.equal(
    /max-width\s*:/.test(body),
    false,
    ".sheet--drawer must not override max-width at BP-L — it is an ambient side panel, not the primary reading surface, and stays at the base rule's --w-sheet (480px) unless a future package gives a specific reason to widen it",
  );
});

// ---------------------------------------------------------------------
// 4. Right-edge drawer (notifications + clients) — the actual geometry
// values, and the z-index pin against this variant's own failure mode.
// ---------------------------------------------------------------------

test("BP-L's .sheet-backdrop--drawer stretches full height and right-aligns", () => {
  const body = ruleBody(railBlock.body, ".sheet-backdrop--drawer");
  assert.ok(body, ".sheet-backdrop--drawer override not found in the rail-conversion media block");
  assert.match(body, /align-items:\s*stretch/);
  assert.match(body, /justify-content:\s*flex-end/);
});

test("BP-L's .sheet--drawer reaches full viewport height, rounds/borders only the interior (left) edge, and hides the grab handle", () => {
  const body = ruleBody(railBlock.body, ".sheet--drawer");
  assert.ok(body, ".sheet--drawer override not found in the rail-conversion media block");
  assert.match(body, /max-height:\s*100vh/, "must lift the base rule's 88vh cap or the drawer stops short of the viewport's top/bottom edges");
  assert.match(body, /border-top-right-radius:\s*0\b/, "the right edge is flush against the viewport — it must not stay rounded");
  assert.match(body, /border-bottom-left-radius:\s*var\(--r-xl\)/);
  assert.match(body, /border-right:\s*none/, "the right edge is flush against the viewport — it must carry no border");
  // Opus review N1, WP 4e.3 fix round: the drawer is now full-height, so its
  // TOP edge is flush against the viewport too (same reasoning that already
  // drops the border on a flush-bottom base bottom sheet, and on this
  // variant's own flush-right edge) — the base rule's border-bottom:none
  // already covers the bottom edge without any override needed here.
  assert.match(body, /border-top:\s*none/, "the top edge is flush against the viewport — it must carry no border either");
  assert.equal(/border-bottom:\s*1px/.test(body), false, "the bottom edge is flush against the viewport (same as the base bottom sheet) — it must NOT gain a border the base rule's border-bottom:none doesn't already have");
  const grabBody = ruleBody(railBlock.body, ".sheet--drawer .grab");
  assert.ok(grabBody, ".sheet--drawer .grab override not found");
  assert.match(grabBody, /display\s*:\s*none/);
  // Opus review N2, WP 4e.3 fix round: with .grab hidden, .body's own 10px
  // top padding (base "sheets" section) is all that is left above the
  // drawer's h2 — bumped to match .topbar's 14px so the two don't misalign
  // along the shared top edge of the viewport.
  const bodyPaddingBody = ruleBody(railBlock.body, ".sheet--drawer .body");
  assert.ok(bodyPaddingBody, ".sheet--drawer .body override not found");
  assert.match(bodyPaddingBody, /padding-top:\s*14px/);
});

// This variant's own failure mode (module comment in app.css): nothing
// about `justify-content:flex-end` guarantees the drawer paints ABOVE the
// rail/topbar — only the pre-existing z-index numbers do, and those live in
// three different, unrelated places in this file. Computed from the live
// values, not hand-typed, so a future edit to any one of the three still
// fails this test by name instead of silently reopening the "sits under the
// rail/header" bug the brief named explicitly.
test("the sheet-backdrop's z-index (every overlay, including the drawer variant) is above BOTH the BP-L rail's and the topbar's — computed from the live values", () => {
  const backdropBody = ruleBody(appTop, ".sheet-backdrop");
  const backdropZ = Number(/z-index:\s*(\d+)/.exec(backdropBody)?.[1]);
  assert.ok(Number.isFinite(backdropZ), "could not read .sheet-backdrop's z-index");

  const railNavBody = ruleBody(railBlock.body, ".nav");
  const railNavZ = Number(/z-index:\s*(\d+)/.exec(railNavBody)?.[1]);
  assert.ok(Number.isFinite(railNavZ), "could not read the BP-L .nav (rail) override's z-index");

  const topbarBody = ruleBody(appTop, ".topbar");
  const topbarZ = Number(/z-index:\s*(\d+)/.exec(topbarBody)?.[1]);
  assert.ok(Number.isFinite(topbarZ), "could not read .topbar's z-index");

  assert.ok(
    backdropZ > railNavZ,
    `.sheet-backdrop's z-index (${backdropZ}) must be greater than the BP-L rail's (.nav, ${railNavZ}) — otherwise the drawer would paint BENEATH the rail`,
  );
  assert.ok(
    backdropZ > topbarZ,
    `.sheet-backdrop's z-index (${backdropZ}) must be greater than .topbar's (${topbarZ}) — otherwise the drawer would paint BENEATH the header`,
  );
});

// The other half of "confirmation above detail" (brief's nesting ask): the
// bulk-delete/GC-execute confirm dialogs must still render above an open
// sheet in the new centred-card geometry exactly as they did above the old
// bottom sheet — the same z-index relationship, computed live rather than
// re-asserting the two literals (45, 40) the app.css comment already names.
test("the confirm dialog's z-index (.dialog-backdrop) is above the sheet's (.sheet-backdrop) — computed from the live values, holds for the new centred-card geometry too", () => {
  const dialogBody = ruleBody(appTop, ".dialog-backdrop");
  const dialogZ = Number(/z-index:\s*(\d+)/.exec(dialogBody)?.[1]);
  assert.ok(Number.isFinite(dialogZ), "could not read .dialog-backdrop's z-index");

  const sheetBody = ruleBody(appTop, ".sheet-backdrop");
  const sheetZ = Number(/z-index:\s*(\d+)/.exec(sheetBody)?.[1]);
  assert.ok(Number.isFinite(sheetZ), "could not read .sheet-backdrop's z-index");

  assert.ok(
    dialogZ > sheetZ,
    `.dialog-backdrop's z-index (${dialogZ}) must stay above .sheet-backdrop's (${sheetZ}) — the detail sheet's delete/GC-execute confirms must render on top of the sheet whether the sheet is a bottom sheet (mobile) or a centred card (BP-L+)`,
  );
});

// ---------------------------------------------------------------------
// 4b. Opus review BLOCKER B1, WP 4e.3 fix round: `.dialog-backdrop` (the
// bulk-delete/GC-execute confirm overlay, reused verbatim by
// `game-detail-sheet.js`'s two confirms and `views/library.js`'s bulk
// delete) used to keep centring on the FULL viewport while the sheet it
// covers moved its own centring axis to the content area — a live-measured
// constant -90px (= --rail-w/2) mismatch, found by the reviewer's headless-
// Chrome pass, invisible to every per-selector structural pin above. Pinned
// here by DERIVING both insets from the live tokens and requiring them to
// resolve to the same axis, rather than asserting two independent literals
// that could drift apart exactly the way this blocker shipped.
// ---------------------------------------------------------------------

test("the confirm dialog's centring inset and the centred card's centring inset resolve to the SAME axis — computed from the live tokens, not two repeated literals", () => {
  const dialogBody = ruleBody(railBlock.body, ".dialog-backdrop");
  assert.ok(dialogBody, ".dialog-backdrop override not found in the rail-conversion media block — the confirm dialogs need a BP-L centring fix alongside the sheet's own");
  const dialogPaddingLeft = /padding-left:\s*([^;]+);/.exec(dialogBody)?.[1]?.trim();
  assert.ok(dialogPaddingLeft, ".dialog-backdrop has no BP-L padding-left override");

  const sheetCenterBody = ruleBody(railBlock.body, ".sheet-backdrop--center");
  const sheetPaddingLeft = /padding-left:\s*([^;]+);/.exec(sheetCenterBody)?.[1]?.trim();
  assert.ok(sheetPaddingLeft, ".sheet-backdrop--center has no padding-left");

  // The base `.dialog-backdrop` rule (css/app.css's "bulk-delete confirm
  // dialog" section) carries `padding:22px` on every edge — a bare
  // `padding-left:var(--rail-w)` would REPLACE that shorthand's left value
  // rather than add to it, landing 11px (half of 22px) off the card's own
  // axis. The correct BP-L override must therefore ADD --rail-w on top of
  // the base's own 22px, not restate --rail-w alone.
  assert.match(
    dialogPaddingLeft,
    /^calc\(\s*var\(--rail-w\)\s*\+\s*22px\s*\)$/,
    `.dialog-backdrop's BP-L padding-left is "${dialogPaddingLeft}" — must be calc(var(--rail-w) + 22px), preserving the base rule's own 22px inset while ALSO excluding the rail from the centring axis`,
  );
  assert.equal(
    sheetPaddingLeft,
    "var(--rail-w)",
    `.sheet-backdrop--center's padding-left is "${sheetPaddingLeft}" — expected the bare var(--rail-w) this test's own arithmetic below assumes`,
  );

  // Resolve both to a concrete pixel value using the live --rail-w and
  // confirm they land on the exact same axis (dialog's 22px base inset is
  // symmetric — present on the right edge too — so it does not itself shift
  // the CENTRE of the dialog's available box, only both insets equally).
  const themeTop = splitMedia(themeCss).topLevel;
  const railWMatch = /--rail-w\s*:\s*(\d+)px/.exec(themeTop);
  assert.ok(railWMatch, "--rail-w not found in theme.css's :root");
  const railW = Number(railWMatch[1]);

  const dialogInsetPx = railW + 22;
  const sheetInsetPx = railW;
  // The dialog's available box is also inset by 22px on the RIGHT (its
  // unchanged base padding), so its centring axis sits at
  // left-edge + (availableWidth)/2 = (dialogInsetPx + (viewportWidth -
  // dialogInsetPx - 22)/2). The sheet's axis sits at
  // sheetInsetPx + (viewportWidth - sheetInsetPx)/2. Both reduce to the
  // SAME point (independent of viewport width) exactly when
  // dialogInsetPx - 22 === sheetInsetPx, i.e. railW + 22 - 22 === railW —
  // true by construction once the calc() above is correct; asserted
  // numerically here so a future edit to either literal fails this exact
  // arithmetic check by name instead of only the regex shape check above.
  assert.equal(dialogInsetPx - 22, sheetInsetPx, `dialog inset (${dialogInsetPx}px) minus its own symmetric 22px must equal the sheet's inset (${sheetInsetPx}px) for both to centre on the same axis`);
});

// ---------------------------------------------------------------------
// 5. sheet-dialog.js actually wires the variant classes it is asked for
// (the JS/CSS contract this whole file assumes).
// ---------------------------------------------------------------------

test("createSheetDialog appends sheet-backdrop--<variant>/sheet--<variant> classes when a variant is given, and none when it is not", async () => {
  const dom = createFakeDom();
  globalThis.document = dom.document;
  globalThis.window = dom.window;
  const { createSheetDialog } = await import("../js/components/sheet-dialog.js");

  const center = createSheetDialog({ ariaLabel: "Center test", variant: "center" });
  assert.equal(center.backdrop.className, "sheet-backdrop sheet-backdrop--center");
  assert.equal(center.sheet.className, "sheet sheet--center");

  const drawer = createSheetDialog({ ariaLabel: "Drawer test", variant: "drawer" });
  assert.equal(drawer.backdrop.className, "sheet-backdrop sheet-backdrop--drawer");
  assert.equal(drawer.sheet.className, "sheet sheet--drawer");

  const plain = createSheetDialog({ ariaLabel: "No variant test" });
  assert.equal(plain.backdrop.className, "sheet-backdrop");
  assert.equal(plain.sheet.className, "sheet");
});

test("the three real overlay components request the operator-decided variant: detail sheet is 'center', notifications/clients are 'drawer'", () => {
  const jsDir = path.join(webDir, "js");
  const detailJs = readFileSync(path.join(jsDir, "components", "game-detail-sheet.js"), "utf8");
  const notifJs = readFileSync(path.join(jsDir, "components", "notifications.js"), "utf8");
  const clientsJs = readFileSync(path.join(jsDir, "components", "clients-sheet.js"), "utf8");

  assert.match(detailJs, /createSheetDialog\(\s*\{\s*ariaLabel:\s*"Game detail",\s*variant:\s*"center"\s*\}\s*\)/);
  assert.match(notifJs, /createSheetDialog\(\s*\{\s*ariaLabel:\s*"Notifications",\s*variant:\s*"drawer"\s*\}\s*\)/);
  assert.match(clientsJs, /createSheetDialog\(\s*\{\s*ariaLabel:\s*"PCs \(agents\)",\s*variant:\s*"drawer"\s*\}\s*\)/);
});

// ---------------------------------------------------------------------
// 6. Nesting in the new geometry: a confirm dialog opened on top of a
// "center"-variant sheet must still make the sheet inert and Escape must
// close the confirm FIRST, not both at once — the exact live-found WP 4a.8
// bug (lib/modal-stack.js's header) reproduced in the shape this WP's new
// centred-card geometry actually uses. `modal-stack.test.js` already pins
// the abstract stack arithmetic with plain divs; this test drives the real
// `sheet-dialog.js` (calling `createSheetDialog({..., variant:"center"})`
// itself, directly, with the real option this WP adds) plus a raw backdrop
// standing in for `game-detail-sheet.js`'s own delete-confirm overlay
// (pushed the exact same way that module does:
// `pushModal(confirmBackdrop, closeConfirm)`), proving the variant class is
// presentation-only exactly as claimed — it changes nothing about the
// push/pop/Escape sequence.
//
// **Scope, stated precisely (Opus review S4, WP 4e.3 fix round).** Because
// this test constructs its OWN `createSheetDialog({variant:"center"})`
// call rather than importing `game-detail-sheet.js`, it CANNOT detect a
// regression in that production module's own call site (e.g. someone
// deleting `variant: "center"` from game-detail-sheet.js's own
// `createSheetDialog(...)` call) — the sanity assertion right after
// `detail.open()` only proves `sheet-dialog.js`'s variant-class LOGIC still
// works, not that any particular caller still invokes it correctly. That
// second thing is what section 5's "the three real overlay components
// request the operator-decided variant" test (a source grep against
// game-detail-sheet.js/notifications.js/clients-sheet.js) exists to catch,
// and is the ONLY test in this file that reverting game-detail-sheet.js's
// `variant: "center"` argument kills — mutation-verified (538/539, this
// nesting test still green): a mutation report that attributed that kill
// to this test instead would be wrong about WHICH pin is load-bearing for
// WHICH regression.
// ---------------------------------------------------------------------

test("a confirm dialog opened on top of a 'center'-variant sheet: Escape closes the confirm first, the sheet only on a second Escape", async () => {
  const dom = createFakeDom();
  globalThis.document = dom.document;
  globalThis.window = dom.window;
  const { resetModalStack } = await import("../js/lib/modal-stack.js");
  resetModalStack(dom.document);
  const { createSheetDialog } = await import("../js/components/sheet-dialog.js");

  const detail = createSheetDialog({ ariaLabel: "Game detail (nesting test)", variant: "center" });
  assert.equal(detail.sheet.className, "sheet sheet--center", "sanity: this is the real center-variant sheet, not a plain one");

  const invoker = dom.document.createElement("button");
  dom.document.activeElement = invoker;
  detail.open();
  assert.equal(detail.isOpen(), true);

  const { pushModal, popModal } = await import("../js/lib/modal-stack.js");
  const confirmBackdrop = dom.document.createElement("div");
  let confirmClosed = 0;
  function closeConfirm() {
    confirmClosed++;
    popModal(confirmBackdrop, dom.document);
  }
  pushModal(confirmBackdrop, closeConfirm, dom.document);

  // Both open: the sheet (not #app) must be the one made inert underneath
  // the confirm — this is exactly why lib/modal-stack.js is a stack, not a
  // boolean (see that module's header).
  assert.equal(detail.backdrop.getAttribute("inert"), "", "the open sheet must go inert while the confirm sits on top of it");
  assert.equal(confirmBackdrop.hasAttribute("inert"), false);

  dom.document.dispatchEvent(fakeKeyEvent("Escape"));
  assert.equal(confirmClosed, 1, "first Escape must close the confirm");
  assert.equal(detail.isOpen(), true, "first Escape must NOT also close the sheet underneath it — the live-found WP 4a.8 double-close bug");
  assert.equal(detail.backdrop.hasAttribute("inert"), false, "with the confirm gone, the sheet must be the new topmost — no longer inert");

  dom.document.dispatchEvent(fakeKeyEvent("Escape"));
  assert.equal(detail.isOpen(), false, "second Escape must close the sheet itself");
});
