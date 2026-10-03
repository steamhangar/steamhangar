/**
 * Source-scan pins for the WP WEB-FEAT-3 rail → About wiring (review
 * must-fix 1 and 2): `rail-panel.js` only calls `onVersionActivate` if
 * app.js passes it, and only a `<button>` makes the version line keyboard-
 * reachable — both were deletable with the suite green.
 *
 * Strong-generation pin (docs/LEARNINGS.md, AG series): the property must
 * sit INSIDE the argument object of the `createRailPanel({...})` call, and
 * the failure message tells "deleted" (no `onVersionActivate` in app.js at
 * all) apart from "moved" (it exists, but not in that call) and from
 * "changed" (in the call, but a required statement is missing). Comments are
 * stripped first, so a commented-out handler does not count.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const webDir = path.join(here, "..");
const stripJsComments = (src) => src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'\\])\/\/[^\n]*/g, "$1");
const appJs = stripJsComments(readFileSync(path.join(webDir, "js", "app.js"), "utf8"));
const indexHtml = readFileSync(path.join(webDir, "index.html"), "utf8").replace(/<!--[\s\S]*?-->/g, "");

/** Text of the argument list of the first `name(` call, brace/paren balanced. */
function callArgs(src, name) {
  const start = src.indexOf(`${name}(`);
  if (start === -1) return null;
  let depth = 0;
  for (let i = start + name.length; i < src.length; i++) {
    const ch = src[i];
    if (ch === "(" || ch === "{" || ch === "[") depth++;
    else if (ch === ")" || ch === "}" || ch === "]") {
      depth--;
      if (depth === 0) return src.slice(start + name.length + 1, i);
    }
  }
  return null;
}

/** The handler expression of `onVersionActivate:` in `text`, balanced up to
 * the property's end (the next top-level comma or the end of the object). */
function handlerText(text) {
  const at = text.indexOf("onVersionActivate:");
  if (at === -1) return null;
  let depth = 0;
  let i = at + "onVersionActivate:".length;
  for (; i < text.length; i++) {
    const ch = text[i];
    if (ch === "(" || ch === "{" || ch === "[") depth++;
    else if (ch === ")" || ch === "}" || ch === "]") {
      if (depth === 0) break;
      depth--;
    } else if (ch === "," && depth === 0) break;
  }
  return text.slice(at, i);
}

test("MUTATION TARGET: app.js passes onVersionActivate to createRailPanel({...}), calling requestAboutFocus() and navigateTo(\"settings\")", () => {
  const args = callArgs(appJs, "createRailPanel");
  assert.ok(args !== null, "app.js no longer calls createRailPanel(...) — re-point this pin to wherever the rail is created");
  const inCall = handlerText(args);
  if (inCall === null) {
    if (appJs.includes("onVersionActivate")) {
      assert.fail(
        "MOVED: onVersionActivate exists in app.js but is NOT a property of the createRailPanel({...}) argument — " +
          "rail-panel.js only wires the version button from its own options, so pass it there.",
      );
    }
    assert.fail(
      "DELETED: app.js has no onVersionActivate at all — the rail's version button (\"dev build\") no longer opens " +
        "Settings → About. Restore `onVersionActivate: () => { requestAboutFocus(); navigateTo(\"settings\"); }` in createRailPanel({...}).",
    );
  }
  assert.match(inCall, /\brequestAboutFocus\(\s*\)/, "CHANGED: the handler no longer calls requestAboutFocus() — Settings opens but focus never reaches About");
  assert.match(inCall, /\bnavigateTo\(\s*["']settings["']\s*\)/, "CHANGED: the handler no longer calls navigateTo(\"settings\")");
  assert.ok(
    inCall.indexOf("requestAboutFocus") < inCall.indexOf("navigateTo"),
    "CHANGED: requestAboutFocus() must run BEFORE navigateTo(\"settings\") — navigation renders Settings synchronously",
  );
  assert.match(
    appJs,
    /import\s*\{[^}]*\brequestAboutFocus\b[^}]*\}\s*from\s*["']\.\/views\/settings\.js["']/,
    "requestAboutFocus is not imported from ./views/settings.js",
  );
});

test("MUTATION TARGET: index.html's #rail-version is a <button type=\"button\"> (keyboard-reachable, not a <p>)", () => {
  const m = /<([a-zA-Z][\w-]*)\b([^>]*\bid=["']rail-version["'][^>]*)>/.exec(indexHtml);
  assert.ok(m, "no element with id=\"rail-version\" in index.html (comments excluded)");
  assert.equal(m[1].toLowerCase(), "button", `#rail-version is a <${m[1]}>, must be a <button> — a click handler on a non-button is unreachable by keyboard`);
  assert.match(m[2], /\btype=["']button["']/, "#rail-version must carry type=\"button\"");
  assert.match(m[2], /\bclass=["'][^"']*\brail-version\b/, "#rail-version lost its .rail-version class (the BP-L styling)");
});
