/**
 * Layout pins for the Settings save bar (WP WEB-FIX-8; moved into the
 * right column by WP SETTINGS-2). No browser here (same posture as
 * css-about-pcs.test.js): these pin the declarations in the right selector.
 *
 * Expected look, for the real-device check: as soon as a Settings field
 * differs from its saved value, one rounded row "● Unsaved changes |
 * Discard changes | Save changes" appears at the end of the right column
 * and sticks to the bottom of the viewport (above the phone's bottom nav)
 * while the column is longer than the screen. At 460px container width or
 * less the status line takes its own row above the two buttons.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const webDir = path.join(__dirname, "..");
const strip = (css) => css.replace(/\/\*[\s\S]*?\*\//g, "");
const kitCss = strip(readFileSync(path.join(webDir, "css", "kit.css"), "utf8"));
const appCss = strip(readFileSync(path.join(webDir, "css", "app.css"), "utf8"));
const themeCss = strip(readFileSync(path.join(webDir, "css", "theme.css"), "utf8"));

/** Body of the first top-level rule whose selector list is EXACTLY `selector`. */
function ruleBody(text, selector) {
  const re = /(^|\})\s*([^{}]+)\{([^{}]*)\}/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    if (m[2].trim().replace(/\s+/g, " ") === selector) return m[3];
    re.lastIndex = m.index + m[0].length - 1;
  }
  return null;
}
/** True when a top-level rule lists `selector` (alone or in a comma list)
 * with `display:none`. */
function guarded(text, selector) {
  const re = /(^|\})\s*([^{}]+)\{([^{}]*)\}/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    const sels = m[2].split(",").map((x) => x.trim().replace(/\s+/g, " "));
    if (sels.includes(selector) && /display:\s*none/.test(m[3])) return true;
    re.lastIndex = m.index + m[0].length - 1;
  }
  return false;
}
const norm = (s) => (s || "").replace(/\s+/g, "");
const decl = (body, prop) => {
  const m = new RegExp(`(?:^|;)\\s*${prop}\\s*:\\s*([^;]+)`).exec(body || "");
  return m ? m[1].trim() : null;
};
const BAR = ".kit.view-settings .savebar";

test("MUTATION TARGET: the bar is sticky at the bottom of the column, above the bottom nav and the safe-area inset — never fixed", () => {
  const body = ruleBody(kitCss, BAR);
  assert.ok(body, `${BAR} rule missing in kit.css`);
  assert.equal(decl(body, "position"), "sticky", "sticky: it stays in the right column and in the page flow");
  assert.equal(norm(decl(body, "bottom")), "calc(var(--nav-h)+var(--sp-4)+env(safe-area-inset-bottom,0px))");
  assert.equal(ruleBody(appCss, ".savebar"), null, "the old fixed bar rule is gone from app.css");
});

test("one row: a wrapping flex row, status line first and growing, buttons at the end", () => {
  const body = ruleBody(kitCss, BAR);
  assert.match(body, /display:\s*flex/);
  assert.match(body, /justify-content:\s*flex-end/);
  assert.match(ruleBody(kitCss, `${BAR} .m`) ?? "", /flex:\s*1 1 200px/);
});

test("the bar is built only on Settings, inside the right column", () => {
  const jsDir = path.join(webDir, "js");
  const files = [];
  const walk = (d) => {
    for (const e of readdirSync(d, { withFileTypes: true })) {
      const p = path.join(d, e.name);
      if (e.isDirectory()) walk(p);
      else if (e.name.endsWith(".js")) files.push(p);
    }
  };
  walk(jsDir);
  const using = files.filter((f) => /el\(\s*"div"\s*,\s*"savebar"\s*\)/.test(readFileSync(f, "utf8"))).map((f) => path.relative(jsDir, f));
  assert.deepEqual(using, [path.join("views", "settings.js")]);
});

test("MUTATION TARGET: hidden really hides it (author display:flex beats the UA [hidden] rule)", () => {
  assert.match(ruleBody(kitCss, BAR) ?? "", /display:\s*flex/);
  assert.equal(guarded(kitCss, `${BAR}[hidden]`), true, "the [hidden] guard for the bar is missing");
});

test("MUTATION TARGET: while the bar is up the root scroll padding keeps a focused control above it (WCAG 2.4.11)", () => {
  const body = ruleBody(appCss, ":root:has(.savebar-up)");
  assert.ok(body, ":root:has(.savebar-up) rule missing at top level");
  assert.equal(
    norm(decl(body, "scroll-padding-bottom")),
    "calc(var(--savebar-h)+var(--bulk-gap)*2+var(--nav-h)+env(safe-area-inset-bottom,0px))",
  );
  assert.ok(themeCss.includes("--savebar-h"));
});

test("theme tokens only, the error line uses the danger token", () => {
  const body = ruleBody(kitCss, BAR) ?? "";
  assert.match(decl(body, "background") ?? "", /^var\(--/);
  assert.match(decl(body, "border") ?? "", /var\(--/);
  assert.match(ruleBody(kitCss, ".kit.view-settings .savebar-msg.is-error") ?? "", /color:\s*var\(--danger\)/);
});
