/**
 * WP WEB-FEAT-3 layout pins: Settings → About table and the PCs sheet's
 * presence chip, under the WP WEB-FIX-3 phone rules (no horizontal
 * overflow: minmax(0,1fr) tracks, min-width:0, long versions wrap).
 *
 * No browser here (same posture as css-mobile-overflow.test.js): these pin
 * the declarations in the right selector and the right (or no) media
 * block. Expected visual result, for the real-device check: on a phone the
 * About table is one block per component (name on its own line, then
 * VERSION / COMMIT side by side, STATUS below, each with a small uppercase
 * label), then the note and the server detail; from 720px up it is a plain
 * four-column table with a header row. In the PCs sheet each row shows the
 * presence chip (filled dot "Online" / hollow ring "Offline") above the
 * Healthy/Bypassing badge, right-aligned.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
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

/** Brace-balanced @media splitter (per-file copy, suite convention). */
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

/** Body of the rule whose selector list is EXACTLY `selector` (a rule
 * starts after `}` / start of text, so `.grid` never matches inside
 * `.grid.list` or a combined list). Null when absent. */
function ruleBody(text, selector) {
  const re = /(^|\})\s*([^{}]+)\{([^{}]*)\}/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    if (m[2].trim().replace(/\s+/g, " ") === selector) return m[3];
    re.lastIndex = m.index + m[0].length - 1; // re-use the closing brace
  }
  return null;
}

/** Every body of rules whose selector is exactly `selector`, joined. */
function allRuleBodies(text, selector) {
  const out = [];
  const re = /(^|\})\s*([^{}]+)\{([^{}]*)\}/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    if (m[2].trim().replace(/\s+/g, " ") === selector) out.push(m[3]);
    re.lastIndex = m.index + m[0].length - 1;
  }
  return out.join(";");
}

const app = splitMedia(appCss);
const theme = splitMedia(themeCss);
const bpM = app.mediaBlocks.find((b) => /min-width:\s*720px/.test(b.header) && !/and/.test(b.header));
const bpL = app.mediaBlocks.find((b) => /min-width:\s*1024px/.test(b.header) && !/and/.test(b.header));

test("MUTATION TARGET: phone first — the About rows are stacked blocks on minmax(0,1fr) tracks", () => {
  assert.match(ruleBody(app.topLevel, ".about-table tbody, .about-table tr, .about-table th, .about-table td") ?? "", /display:\s*block/);
  const row = ruleBody(app.topLevel, ".about-table .about-row") ?? "";
  assert.match(row, /grid-template-columns:\s*minmax\(0,\s*1fr\)\s+minmax\(0,\s*1fr\)/);
  assert.match(ruleBody(app.topLevel, ".about-table td[data-label]::before") ?? "", /content:\s*attr\(data-label\)/);
  assert.match(ruleBody(app.topLevel, ".about-table thead") ?? "", /clip-path:\s*inset\(50%\)/, "header row visually hidden on a phone");
});

test("MUTATION TARGET: long versions/commits/names wrap instead of widening the page", () => {
  assert.match(ruleBody(app.topLevel, ".about-table .mono") ?? "", /overflow-wrap:\s*anywhere/);
  assert.match(ruleBody(app.topLevel, ".about-table .about-name") ?? "", /overflow-wrap:\s*anywhere/);
  assert.match(ruleBody(app.topLevel, ".pcs-line") ?? "", /overflow-wrap:\s*anywhere/);
  assert.match(ruleBody(app.topLevel, ".about-content") ?? "", /min-width:\s*0/);
  assert.match(ruleBody(app.topLevel, ".about-status-line") ?? "", /min-width:\s*0/);
});

test("from BP-M up the About table is a real fixed-layout table with its header back", () => {
  assert.ok(bpM, "BP-M block not found");
  assert.match(ruleBody(bpM.body, ".about-table") ?? "", /table-layout:\s*fixed/);
  assert.match(ruleBody(bpM.body, ".about-table thead") ?? "", /display:\s*table-header-group/);
  assert.match(ruleBody(bpM.body, ".about-table tr, .about-table .about-row") ?? "", /display:\s*table-row/);
  assert.match(ruleBody(bpM.body, ".about-table th, .about-table td") ?? "", /display:\s*table-cell/);
  assert.match(ruleBody(bpM.body, ".about-table td[data-label]::before") ?? "", /content:\s*none/);
});

test("PCs sheet: the badge column does not shrink, the chip never wraps its word", () => {
  assert.match(ruleBody(app.topLevel, ".pcs-badges") ?? "", /flex:\s*none/);
  assert.match(ruleBody(app.topLevel, ".pchip") ?? "", /white-space:\s*nowrap/);
  assert.match(ruleBody(app.topLevel, ".pchip-off .pdot, .pchip-unknown .pdot") ?? "", /background:\s*transparent/, "offline is a hollow ring: a shape cue, not colour only");
});

test("rail version button: still clipped with an ellipsis, full width, and no author display (the [hidden] toggle keeps working)", () => {
  assert.ok(bpL, "BP-L block not found");
  const body = ruleBody(bpL.body, ".rail-version") ?? "";
  assert.match(body, /text-overflow:\s*ellipsis/);
  assert.match(body, /width:\s*100%/);
  assert.doesNotMatch(body, /(^|;)\s*display\s*:/);
});

test("the two new status-icon kinds have their neutral background", () => {
  assert.match(ruleBody(theme.topLevel, ".sic.k-unknown, .sic.k-notinuse") ?? "", /background:\s*var\(--dim-2\)/);
});
