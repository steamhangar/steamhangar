/**
 * Regression: `.pop.menu` sets `display:flex` and, being declared after
 * `.pop[hidden]` with equal specificity, beat the hidden guard, so the
 * Library "Updates" menu was always open and overlapped the search field.
 * Every compound `.pop.X` rule with a display needs its own [hidden] guard.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const css = readFileSync(new URL("../css/app.css", import.meta.url), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

test("every .pop.<x> rule that sets display has a matching [hidden] guard", () => {
  const rules = [...css.matchAll(/(\.pop\.[\w-]+)\s*\{([^}]*)\}/g)];
  const withDisplay = rules.filter((m) => /(^|;|\s)display\s*:/.test(m[2])).map((m) => m[1]);
  assert.ok(withDisplay.includes(".pop.menu"));
  for (const sel of withDisplay) {
    const guard = new RegExp(sel.replace(/\./g, "\\.") + "\\[hidden\\]\\s*\\{\\s*display\\s*:\\s*none");
    assert.match(css, guard, `${sel} needs ${sel}[hidden]{display:none}`);
  }
});
