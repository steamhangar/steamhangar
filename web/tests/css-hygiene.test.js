/**
 * CSS hygiene lints (WP 4e.1 brief: "the lint I want most from this
 * package... closes a bug class permanently").
 *
 * Three instances of the same bug were found by hand across earlier WPs
 * before this lint existed (docs/LEARNINGS.md, "Web UI"): `.btn`, `h4.sec`
 * and `.onbnav` each carry an author `display` declaration, which — per the
 * cascade — always wins over the UA's `[hidden]{display:none}` default
 * regardless of selector order or specificity, so `el.hidden = true` on any
 * of them silently did nothing until a matching `SELECTOR[hidden]{
 * display:none }` guard rule was added in the same origin. This file
 * cross-references every CSS rule that both (a) is authored with a
 * `display` declaration and (b) is actually toggled via `.hidden = ...`
 * somewhere in web/js/, and requires a `[hidden]` guard rule for each.
 *
 * The second lint pins a narrower, cheaper invariant: `!important` appears
 * exactly once in this codebase on purpose (theme.css's
 * `prefers-reduced-motion` block, which must win unconditionally over every
 * `animation`/`transition` declaration elsewhere in the cascade) — any OTHER
 * `!important`, in particular one introduced inside a `@media (min-width...)`
 * breakpoint block, is exactly the kind of specificity workaround that makes
 * later overrides unpredictable, so it is banned outright.
 *
 * **Scope, stated honestly.** The display/hidden cross-reference below is a
 * real static analysis, not a hand-typed list of the three known offenders —
 * it re-derives both sides (which classes are display-styled, which classes
 * are hidden-toggled) from the actual CSS/JS source on every run, so a
 * FUTURE class introduced via the same idioms this file already understands
 * is caught automatically. It is NOT a full JS/CSS parser: identifier ->
 * class-set tracking only understands the concrete DOM-construction idioms
 * this codebase actually uses (`document.getElementById`/`createElement`,
 * `.className = "..."`, `.classList.add(...)`, `.querySelector(".cls")`,
 * `.setAttribute("hidden", ...)`/`.removeAttribute("hidden")` and
 * `.toggleAttribute("hidden", ...)` (WP 4e.2 — the `.hidden = ...`/
 * `setAttribute`/`removeAttribute` trio already covered here still missed
 * this idiom; no current code uses it, but the brief that shipped the first
 * two attribute forms explicitly flagged this one as the next gap, so it is
 * closed pre-emptively rather than waiting for a live probe to find it the
 * way N1 found the `setAttribute` gap) alongside the `.hidden = ...`
 * property form, and the two local `el(tag, className, ...)` /
 * `sectionHeading(text)` factory functions in
 * onboarding.js/settings.js/downloads.js) and resolves per FILE, top-to-
 * bottom, in one forward pass for every idiom EXCEPT one (WP 4e.8): the
 * `ident: document.getElementById("id")` object-literal property — the
 * dependency-injection idiom `app.js` uses to hand DOM elements into
 * `createRailPanel()` (`elements: { headEl: document.getElementById(
 * "rail-head"), ... }`). That idiom's whole point is that the identifier
 * crosses a file boundary: the property name becomes a destructured
 * PARAMETER name in the function it is passed to, in a DIFFERENT file,
 * which is where the real `.hidden =`/`setAttribute`/... toggle then
 * happens (`rail-panel.js`). A per-file forward pass structurally cannot
 * see that, and it is exactly how `.rail-head[hidden]`/`.rail-foot[hidden]`
 * (app.css) went undetected as required guards for a whole work package:
 * `rail-panel.js`'s `headEl.hidden =`/`footEl.hidden =` sites (WP 4e.6)
 * never resolved against `app.js`'s `headEl:`/`footEl:` seeds (also WP
 * 4e.6) — different files, and a colon-form property the old `byId` regex
 * (which only matched `ident = document.getElementById(...)`) could not
 * see either. `buildDiSeedMap()` below closes this by building ONE
 * project-wide ident -> id map for this specific idiom only, consulted as
 * a FALLBACK only when an identifier was never locally seeded at all (see
 * `findHiddenToggledClasses`'s `hiddenAssign` branch) — every other idiom's
 * resolution is completely unchanged. Going cross-file has its own
 * soundness cost: two unrelated DI sites in two different files could, in
 * principle, reuse the same property/parameter name for two DIFFERENT
 * elements; that is guarded explicitly (a same-ident/different-id
 * collision across DI seeds is a hard test failure naming both sites, not
 * a silent last-write-wins) — see the collision tests below, and the
 * fourth limitation note further down for the one residual gap that guard
 * does not close.
 *
 * Good enough for this codebase's straightforward "build the element, set
 * its class, use it later" construction style (verified against every real
 * `.hidden =` site as of this WP — see the mutation notes below), but not a
 * substitute for a real scope-aware JS parser.
 *
 * **Two known limitations, stated rather than silently worked around
 * (Opus review nitpick N1, WP 4e.1 fix round).** (1) `SIMPLE_CLASS_
 * SELECTOR_RE` only recognises a SINGLE compound selector naming exactly
 * ONE class, optionally tag-qualified (`.btn`, `h4.sec`) — a multi-class
 * compound (`.jobcard.active`) or a descendant/combinator selector
 * (`.grid.list .cap`) is invisible to the display-rule side of the cross-
 * reference even if it carries `display:` and even if JS hidden-toggles an
 * element bearing exactly that class combination; this codebase's three
 * known offenders all happen to be single-class selectors, but a future one
 * built the other way would not be caught. (2) the JS-side scan is
 * per-file and flat (no block/function scoping): two DIFFERENT variables
 * named `btn` in two different functions of the same file are tracked as
 * ONE identifier in `classesByIdent`, so a hidden-toggle on one could
 * theoretically inherit a class set assigned to the OTHER's `btn` earlier in
 * the file. Neither limitation is exploited by any real code as of this WP
 * (verified by the sanity test below finding the real three-class overlap
 * this file expects), but a reviewer or future coder should know where the
 * edges are rather than assume this is a real parser.
 *
 * **Third limitation, stated rather than pretended away (Opus review,
 * WP 4e.2 second fix round): this lint ENUMERATES idioms, it does not
 * GENERALISE over "any way JS can set the hidden attribute/property".**
 * `el.hidden ||= true` / `el.hidden ??= true` and `el.toggleAttribute?.(
 * "hidden", true)` (optional chaining) are now recognised — both were
 * one character away from an idiom already covered, and both were probed
 * and found to survive silently before this fix. But `Object.assign(el,
 * {hidden: true})` sets the exact same property through a shape a FORWARD
 * regex scan over `identifier.hidden`/`identifier.toggleAttribute(...)`
 * text cannot see at all (the identifier and the property name never sit
 * next to each other in the source) — closing it would need real
 * object-literal parsing, not another regex. Not exploited by any real
 * code here as of this WP; recorded as an acknowledged gap rather than
 * implied to be covered by the four idioms this file actually scans for.
 *
 * **Fourth limitation, added by WP 4e.8's cross-file DI resolution — and
 * why only the DI-vs-DI case above got an explicit collision guard
 * (Opus review SF2, WP 4e.8 fix round).** The collision guard
 * (`buildDiSeedMap`'s `collisions` list) only catches two DI SEEDS
 * disagreeing with EACH OTHER — the same object-literal property name
 * pointing at two different ids. It does NOT catch a plain, non-DI local
 * identifier in one file happening to share a name with a DI-seeded
 * identifier in a different, unrelated file: if such a name collision
 * existed, a `.hidden =` toggle on that local identifier — one NEVER
 * locally seeded by any idiom this file understands (`set === undefined`;
 * an identifier that resolved LOCALLY, even to a knowingly empty Set,
 * already wins and is never touched by the fallback — see
 * `findHiddenToggledClasses`'s `hiddenAssign` branch and the "fallback
 * never overrides a local seed" test below) — would incorrectly inherit
 * the unrelated file's DI-seeded classes. But note the DIRECTION of that
 * failure: it can only ADD an extra, unwarranted class to `hiddenToggledClasses`
 * (a false positive — Lint 1 demands a guard that was never actually
 * needed, a loud and immediately obvious failure) or misattribute a site
 * to the wrong class. It can never SWALLOW a real guard requirement,
 * because without ANY seed — local or DI — that site was already invisible
 * to this lint before WP 4e.8 existed; this limitation cannot make a true
 * positive disappear. The DI-vs-DI collision guarded above is different in
 * kind: two DI seeds disagreeing and resolved last-write-wins WOULD
 * silently swallow a real guard requirement (whichever id lost the
 * overwrite stops being seen at all) — which is exactly why that case, and
 * only that case, gets a hard test failure rather than a documented gap.
 * Not exploited by any real code as of this WP: the DI idiom's five actual
 * `diSeedMap` entries (`headEl`, `vaultNameEl`, `footEl`, `cacheEl`,
 * `versionEl`, all app.js -> rail-panel.js) do not collide with any other
 * identifier name anywhere in web/js/ (verified by grep). `createElement`
 * is passed through the SAME object literal (`elements: { ..., createElement:
 * (tag) => document.createElement(tag) }`) but is not a `DI_SEED_RE` match
 * at all — its value is an arrow function, not a `document.getElementById(...)`
 * call — so it is not in `diSeedMap` and is irrelevant to this limitation.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync, writeFileSync, rmSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const webDir = path.join(__dirname, "..");
const appCssPath = path.join(webDir, "css", "app.css");
const themeCssPath = path.join(webDir, "css", "theme.css");
const indexHtmlPath = path.join(webDir, "index.html");
const jsDir = path.join(webDir, "js");

const appCss = readFileSync(appCssPath, "utf8");
const themeCss = readFileSync(themeCssPath, "utf8");
const indexHtml = readFileSync(indexHtmlPath, "utf8");
const combinedCss = `${themeCss}\n${appCss}`;

function walkJsFiles(dir) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry);
    const st = statSync(full);
    if (st.isDirectory()) out.push(...walkJsFiles(full));
    else if (entry.endsWith(".js")) out.push(full);
  }
  return out;
}
const jsFiles = walkJsFiles(jsDir);

// ---------------------------------------------------------------------
// CSS parsing helpers — brace-balanced, comment-stripped, good enough for
// this file's plain CSS (no strings containing braces).
// ---------------------------------------------------------------------

function stripComments(css) {
  return css.replace(/\/\*[\s\S]*?\*\//g, "");
}

/**
 * Flatten every rule in `cssText` (recursing into @media/@supports bodies,
 * skipping @keyframes entirely — its percentage/from/to "selectors" are not
 * class selectors and would only add noise) into a flat list of
 * `{selector, body}`, one entry per individual selector in a comma-separated
 * selector list.
 */
function extractAllRules(cssText) {
  const css = stripComments(cssText);
  const rules = [];

  function parseBlock(text, start, end) {
    let j = start;
    while (j < end) {
      while (j < end && /\s/.test(text[j])) j++;
      if (j >= end) break;
      const braceIdx = text.indexOf("{", j);
      if (braceIdx === -1 || braceIdx >= end) break;
      const header = text.slice(j, braceIdx).trim();
      let depth = 1;
      let k = braceIdx + 1;
      while (depth > 0 && k < end) {
        if (text[k] === "{") depth++;
        else if (text[k] === "}") depth--;
        k++;
      }
      const body = text.slice(braceIdx + 1, k - 1);
      if (/^@media/.test(header) || /^@supports/.test(header)) {
        parseBlock(text, braceIdx + 1, k - 1);
      } else if (/^@keyframes/.test(header)) {
        // skip — no class selectors inside
      } else if (header.startsWith("@")) {
        // unknown at-rule (none expected here) — skip its body defensively
      } else {
        for (const sel of header.split(",")) {
          const trimmed = sel.trim();
          if (trimmed) rules.push({ selector: trimmed, body });
        }
      }
      j = k;
    }
  }

  parseBlock(css, 0, css.length);
  return rules;
}

const allRules = extractAllRules(combinedCss);

// Selectors this lint understands as "a single compound selector directly
// naming one class" — optionally tag-qualified (`h4.sec`), never a
// descendant/combinator selector (`.grid.list .cap`) or a multi-class
// compound (`.jobcard.active`), since those are never what a `.hidden = `
// call site's class SET reduces to one-for-one.
const SIMPLE_CLASS_SELECTOR_RE = /^([a-zA-Z][\w-]*)?\.([\w-]+)$/;
// The matching guard form: exactly the same selector text with `[hidden]`
// appended (the convention every existing guard in this codebase follows —
// `.btn[hidden]`, `h4.sec[hidden]`, `.onbnav[hidden]`).
function guardSelectorFor(selector) {
  return `${selector}[hidden]`;
}

/** class token -> Set of exact selector strings (e.g. "h4.sec", ".btn")
 * that declare `display:` for it, excluding guard rules themselves. */
const displayRulesByClass = new Map();
/** Set of guard selector strings (selector text WITH the trailing
 * `[hidden]`) that resolve to `display:none`. */
const guardSelectors = new Set();

for (const { selector, body } of allRules) {
  const hasDisplay = /display\s*:/.test(body);
  if (!hasDisplay) continue;
  const isGuard = selector.includes("[hidden]");
  if (isGuard) {
    if (/display\s*:\s*none\b/.test(body)) guardSelectors.add(selector);
    continue;
  }
  const m = SIMPLE_CLASS_SELECTOR_RE.exec(selector);
  if (!m) continue; // not a single-class compound this lint tracks
  const cls = m[2];
  if (!displayRulesByClass.has(cls)) displayRulesByClass.set(cls, new Set());
  displayRulesByClass.get(cls).add(selector);
}

// ---------------------------------------------------------------------
// index.html id -> class map (for `document.getElementById(...)` sites)
// ---------------------------------------------------------------------

function buildIdClassMap(html) {
  const map = new Map();
  for (const tagMatch of html.matchAll(/<[a-zA-Z][^>]*>/g)) {
    const tag = tagMatch[0];
    const idMatch = /\bid=["']([^"']+)["']/.exec(tag);
    const classMatch = /\bclass=["']([^"']+)["']/.exec(tag);
    if (idMatch && classMatch) map.set(idMatch[1], classMatch[1]);
  }
  return map;
}
const idClassMap = buildIdClassMap(indexHtml);

// ---------------------------------------------------------------------
// DI-seed idiom (WP 4e.8): `ident: document.getElementById("id")` as an
// object-literal property — see this file's header for why this ONE idiom,
// unlike every other seed form below, needs project-wide (cross-file)
// visibility rather than per-file resolution.
// ---------------------------------------------------------------------

const DI_SEED_RE = /(\w+)\s*:\s*document\.getElementById\(\s*["']([^"']+)["']\s*\)/g;

/**
 * Scans every given file for the `ident: document.getElementById("id")`
 * idiom and builds a PROJECT-WIDE ident -> id map (the property name is
 * meant to be read from a different file than the one that declares it, so
 * per-file scoping would defeat the idiom's own purpose).
 *
 * Soundness cost of going cross-file: two unrelated DI sites in two
 * different files could reuse the same property/parameter name for two
 * DIFFERENT elements — nothing about the idiom itself prevents it. A naive
 * last-write-wins map would then silently claim resolution coverage it
 * never actually earned, which is precisely the failure class this whole
 * lint exists to close, one level up. So a same-ident/different-id
 * collision is collected and reported BY NAME (the shared identifier, both
 * files, both ids) instead of resolved by overwrite; the caller turns a
 * non-empty `collisions` list into a hard test failure. A same-ident/
 * SAME-id repeat (the identical element legitimately named the same way
 * twice) is not a collision.
 *
 * @param {string[]} files
 * @returns {{
 *   map: Map<string, {id: string, file: string}>,
 *   collisions: Array<{ident: string, siteA: string, siteB: string}>,
 * }}
 */
function buildDiSeedMap(files) {
  const map = new Map();
  const collisions = [];
  for (const file of files) {
    const text = readFileSync(file, "utf8");
    const relFile = path.relative(webDir, file);
    for (const m of text.matchAll(DI_SEED_RE)) {
      const ident = m[1];
      const id = m[2];
      const existing = map.get(ident);
      if (!existing) {
        map.set(ident, { id, file: relFile });
      } else if (existing.id !== id) {
        collisions.push({
          ident,
          siteA: `${existing.file} (id="${existing.id}")`,
          siteB: `${relFile} (id="${id}")`,
        });
      }
    }
  }
  return { map, collisions };
}

const { map: diSeedMap, collisions: diSeedCollisions } = buildDiSeedMap(jsFiles);

// ---------------------------------------------------------------------
// JS static scan: for every `.hidden = ` site, resolve the identifier's
// known class set at that point in the file (single forward pass, see this
// file's header for the supported idioms and their scope).
// ---------------------------------------------------------------------

function tokensFromQuotedList(argText) {
  const out = [];
  for (const m of argText.matchAll(/["']([^"']+)["']/g)) {
    out.push(...m[1].split(/\s+/).filter(Boolean));
  }
  return out;
}

/** @returns {Map<string, Array<{file:string, ident:string}>>} class token ->
 * every (file, identifier) site where an element carrying that class was
 * hidden-toggled.
 *
 * `diSeeds` (WP 4e.8, default empty — every pre-existing caller in this
 * file that only exercises local, single-file idioms passes none and is
 * unaffected): the project-wide ident -> {id} map from `buildDiSeedMap()`,
 * consulted ONLY when an identifier was never locally seeded by any of the
 * per-file idioms above at all (`classesByIdent.get(ident) === undefined`).
 * Local resolution — including a local seed that resolved to a knowingly
 * EMPTY class set — always wins and is never overridden by a cross-file
 * DI seed; this is what keeps every idiom's existing per-file resolution
 * unchanged. */
function findHiddenToggledClasses(files, idMap, diSeeds = new Map()) {
  const results = new Map();
  const record = (file, ident, classSet) => {
    for (const cls of classSet) {
      if (!results.has(cls)) results.set(cls, []);
      results.get(cls).push({ file, ident });
    }
  };

  for (const file of files) {
    const text = readFileSync(file, "utf8");
    const events = [];
    const push = (type, index, ident, extra) => events.push({ type, index, ident, extra });

    for (const m of text.matchAll(/(\w+)\s*=\s*document\.getElementById\(\s*["']([^"']+)["']\s*\)/g)) {
      push("byId", m.index, m[1], m[2]);
    }
    for (const m of text.matchAll(/(\w+)\s*=\s*el\(\s*["']([^"']*)["']\s*,\s*["']([^"']*)["']/g)) {
      push("elFactory", m.index, m[1], { tag: m[2], cls: m[3] });
    }
    for (const m of text.matchAll(/(\w+)\s*=\s*sectionHeading\(/g)) {
      push("sectionHeadingFactory", m.index, m[1], null);
    }
    for (const m of text.matchAll(/(\w+)\.className\s*=\s*["'`]([^"'`]*)["'`]/g)) {
      push("setClassName", m.index, m[1], m[2]);
    }
    for (const m of text.matchAll(/(\w+)\.className\s*\+=\s*["'`]\s*([^"'`]*)["'`]/g)) {
      push("appendClassName", m.index, m[1], m[2]);
    }
    for (const m of text.matchAll(/(\w+)\.classList\.add\(([^)]*)\)/g)) {
      push("classListAdd", m.index, m[1], m[2]);
    }
    for (const m of text.matchAll(/\b(\w+)\s*=\s*[\w.]+\.querySelector\(\s*["']\.([\w-]+)["']\s*\)/g)) {
      push("querySelectorClass", m.index, m[1], m[2]);
    }
    // Opus review nitpick, WP 4e.2 second fix round: `el.hidden ||= true` /
    // `el.hidden ??= true` toggle the same property a plain `el.hidden = `
    // assignment does, but the literal `\s*=` below used to require the
    // `=` immediately after optional whitespace — "hidden ||=" has `|`
    // characters in that gap, so it never matched. The optional
    // `(?:\|\||\?\?)?` group closes it without touching the plain-`=` case.
    for (const m of text.matchAll(/(\w+)\.hidden\s*(?:\|\||\?\?)?=/g)) {
      push("hiddenAssign", m.index, m[1], null);
    }
    // N1 (Opus review nitpick, WP 4e.1 fix round): `el.hidden = ...` is not
    // the only way this codebase's JS toggles the `hidden` ATTRIBUTE — the
    // DOM property and the attribute are the same underlying state (setting
    // either reflects to the other), so a call site using
    // `setAttribute("hidden", ...)`/`removeAttribute("hidden")` toggles it
    // exactly as much as a `.hidden =` assignment does, and needs the same
    // guard if the element's class also carries an author `display` rule.
    // The reviewer probed this hole directly (a new toggle site written this
    // way, with no guard, survived the lint before this fix) — closed by
    // treating both attribute-manipulation forms as additional hiddenAssign
    // events at the SAME identifier.
    // Opus review nitpick, WP 4e.2 fix round: all three attribute-based
    // regexes below now carry the `i` flag. HTML attribute names are
    // case-insensitive by spec (the DOM lowercases the qualified name on
    // write, verified by the reviewer in a real DOM) — `setAttribute(
    // "HIDDEN", "")`/`toggleAttribute("Hidden", true)` genuinely toggle the
    // same `hidden` attribute a lowercase call site would, and both used to
    // survive this lint silently (the literal `["']hidden["']` match is
    // case-SENSITIVE by default, so it simply never fired for either).
    for (const m of text.matchAll(/(\w+)\.setAttribute\(\s*["']hidden["']\s*,/gi)) {
      push("hiddenAssign", m.index, m[1], null);
    }
    for (const m of text.matchAll(/(\w+)\.removeAttribute\(\s*["']hidden["']\s*\)/gi)) {
      push("hiddenAssign", m.index, m[1], null);
    }
    // WP 4e.2: `el.toggleAttribute("hidden", cond)` (and the no-second-arg
    // `el.toggleAttribute("hidden")` form) toggle the exact same underlying
    // `hidden` attribute as the three forms above — a class carrying an
    // author `display` rule needs the same `[hidden]` guard regardless of
    // WHICH of these four idioms a future call site happens to use. No file
    // in web/js/ uses this idiom as of this WP (see the sanity-negative test
    // below, which proves the regex is live rather than dead code); it is
    // added now, alongside the class of bug it closes, rather than waiting
    // for a live probe the way N1's `setAttribute` gap was found.
    //
    // Opus review nitpick, WP 4e.2 second fix round: `el.toggleAttribute?.(
    // "hidden", true)` (optional-chained, e.g. guarding a possibly-null
    // element) toggles the identical attribute a plain `.toggleAttribute(`
    // call does, but the literal `\(` right after the identifier used to
    // require the call parenthesis with nothing in between — `\??\.?`
    // allows the optional `?` and the `.` of `?.` to sit there instead.
    for (const m of text.matchAll(/(\w+)\.toggleAttribute\??\.?\(\s*["']hidden["']/gi)) {
      push("hiddenAssign", m.index, m[1], null);
    }

    events.sort((a, b) => a.index - b.index);

    const classesByIdent = new Map(); // ident -> Set<class>

    for (const ev of events) {
      if (ev.type === "byId") {
        const seed = idMap.get(ev.extra);
        classesByIdent.set(ev.ident, seed ? new Set(seed.split(/\s+/)) : new Set());
      } else if (ev.type === "elFactory") {
        classesByIdent.set(ev.ident, new Set(ev.extra.cls.split(/\s+/).filter(Boolean)));
      } else if (ev.type === "sectionHeadingFactory") {
        // Known factory (downloads.js): `document.createElement("h4");
        // h4.className = "sec";` — hardcoded here since it constructs via a
        // helper this scan cannot trace into a different call site's variable.
        classesByIdent.set(ev.ident, new Set(["sec"]));
      } else if (ev.type === "setClassName") {
        classesByIdent.set(ev.ident, new Set(ev.extra.split(/\s+/).filter(Boolean)));
      } else if (ev.type === "appendClassName") {
        const set = classesByIdent.get(ev.ident) || new Set();
        for (const t of ev.extra.split(/\s+/).filter(Boolean)) set.add(t);
        classesByIdent.set(ev.ident, set);
      } else if (ev.type === "classListAdd") {
        const set = classesByIdent.get(ev.ident) || new Set();
        for (const t of tokensFromQuotedList(ev.extra)) set.add(t);
        classesByIdent.set(ev.ident, set);
      } else if (ev.type === "querySelectorClass") {
        classesByIdent.set(ev.ident, new Set([ev.extra]));
      } else if (ev.type === "hiddenAssign") {
        let set = classesByIdent.get(ev.ident);
        // WP 4e.8: fall back to the cross-file DI-seed map ONLY when this
        // identifier has no local resolution at all (`undefined`) — e.g.
        // rail-panel.js's `headEl`/`footEl`/`versionEl`, which are function
        // PARAMETERS this file's per-file scan never assigns a class set to
        // by any local idiom, because their actual seed
        // (`headEl: document.getElementById("rail-head")`) lives in
        // app.js. A local set that resolved to a knowingly empty Set (id
        // seeded but no `class` attribute, say) is left exactly as-is — it
        // already made a real determination and stays authoritative.
        if (set === undefined) {
          const diSeed = diSeeds.get(ev.ident);
          if (diSeed) {
            const seedClasses = idMap.get(diSeed.id);
            set = seedClasses ? new Set(seedClasses.split(/\s+/)) : new Set();
          }
        }
        if (set && set.size) record(path.relative(webDir, file), ev.ident, set);
      }
    }
  }

  return results;
}

const hiddenToggledClasses = findHiddenToggledClasses(jsFiles, idClassMap, diSeedMap);

// ---------------------------------------------------------------------
// Lint 1: every display-styled class that is ALSO hidden-toggled needs a
// matching [hidden] guard rule.
// ---------------------------------------------------------------------

test("every CSS class with an author `display` rule that is hidden-toggled in web/js/ has a matching [hidden] guard", () => {
  const missing = [];
  for (const [cls, sites] of hiddenToggledClasses) {
    const displaySelectors = displayRulesByClass.get(cls);
    if (!displaySelectors) continue; // no author display rule for this class — the UA [hidden] default already works
    for (const selector of displaySelectors) {
      if (!guardSelectors.has(guardSelectorFor(selector))) {
        missing.push({ class: cls, selector, sites: sites.map((s) => `${s.file}:${s.ident}`) });
      }
    }
  }
  assert.deepEqual(
    missing,
    [],
    `class(es) with an author display rule and a .hidden= call site but no [hidden] guard: ${JSON.stringify(missing, null, 2)}`,
  );
});

// Sanity pin (not the mutation target itself, but proves the analysis found
// real work to do rather than vacuously passing on an empty result): the
// three historically-buggy classes from docs/LEARNINGS.md must actually be
// present in both sides of the cross-reference. WP WEB-FIX-8: the Settings
// save bar's hidden toggle moved from its `.onbnav` row to the new
// `.savebar` container (the `.onbnav` row inside it is never toggled now),
// so `.savebar` stands in as the third live instance of the class.
test("sanity: the historically-buggy classes (.btn, h4.sec, and the save bar's .savebar) are found on BOTH sides of the cross-reference", () => {
  for (const cls of ["btn", "sec", "savebar"]) {
    assert.ok(displayRulesByClass.has(cls), `no display rule found for .${cls} — CSS parsing regressed`);
    assert.ok(hiddenToggledClasses.has(cls), `no hidden-toggle site found for .${cls} — JS scanning regressed`);
  }
});

// WP 4e.2: no real file in web/js/ uses `toggleAttribute("hidden", ...)`
// today, so — unlike the `setAttribute`/`removeAttribute` regexes above,
// which the sanity test's real `.btn`/`h4.sec`/`.onbnav` sites exercise
// directly — this idiom's regex would otherwise be untested dead code. This
// test drives `findHiddenToggledClasses` (the exact function the real lint
// above calls) against a throwaway fixture file containing the idiom, on
// both the two-argument and bare forms, proving the new regex actually
// fires rather than merely reading as though it should. The fixture is
// written to and removed from web/tests/ itself (never web/js/, so it can
// never leak into the real scan) inside this single test.
test("toggleAttribute(\"hidden\", ...) is recognized as a hidden-toggle site, both the two-arg and bare forms", () => {
  const fixturePath = path.join(__dirname, ".tmp-toggle-attribute-fixture.js");
  writeFileSync(
    fixturePath,
    [
      'const a = document.createElement("button");',
      'a.className = "probe-two-arg";',
      "a.toggleAttribute('hidden', someCondition);",
      'const b = document.createElement("button");',
      'b.className = "probe-bare";',
      'b.toggleAttribute("hidden");',
    ].join("\n"),
  );
  try {
    const found = findHiddenToggledClasses([fixturePath], new Map());
    assert.ok(found.has("probe-two-arg"), "toggleAttribute(\"hidden\", cond) form not recognized");
    assert.ok(found.has("probe-bare"), "bare toggleAttribute(\"hidden\") form not recognized");
  } finally {
    rmSync(fixturePath, { force: true });
  }
});

// Opus review nitpick, WP 4e.2 fix round: the reviewer's own probe —
// `toggleAttribute("HIDDEN", true)` and `setAttribute("Hidden", "")` — both
// verified in a real DOM to genuinely set the `hidden` attribute (the spec
// lowercases the qualified name on write), and both used to survive this
// lint silently because the literal `["']hidden["']` match was
// case-sensitive. This fixture pins all three attribute-based idioms
// (setAttribute/removeAttribute/toggleAttribute) against exactly the
// reviewer's own mixed-case spellings; mutation-verified by dropping the `i`
// flag from any one of the three regexes above and watching this test die.
test("the three attribute-based hidden-toggle idioms are recognized case-insensitively (HTML attribute names are case-insensitive)", () => {
  const fixturePath = path.join(__dirname, ".tmp-case-insensitive-fixture.js");
  writeFileSync(
    fixturePath,
    [
      'const a = document.createElement("button");',
      'a.className = "probe-set-upper";',
      'a.setAttribute("HIDDEN", "");',
      'const b = document.createElement("button");',
      'b.className = "probe-remove-mixed";',
      'b.removeAttribute("Hidden");',
      'const c = document.createElement("button");',
      'c.className = "probe-toggle-upper";',
      'c.toggleAttribute("HIDDEN", true);',
    ].join("\n"),
  );
  try {
    const found = findHiddenToggledClasses([fixturePath], new Map());
    assert.ok(found.has("probe-set-upper"), 'setAttribute("HIDDEN", ...) not recognized');
    assert.ok(found.has("probe-remove-mixed"), 'removeAttribute("Hidden") not recognized');
    assert.ok(found.has("probe-toggle-upper"), 'toggleAttribute("HIDDEN", ...) not recognized');
  } finally {
    rmSync(fixturePath, { force: true });
  }
});

// Opus review nitpick, WP 4e.2 second fix round: the reviewer's own two
// surviving probes — `el.hidden ||= true` (and the `??=` sibling) and
// `el.toggleAttribute?.("hidden", true)` (optional chaining) — pinned
// against the exact same throwaway-fixture pattern as every other idiom in
// this file. Mutation-verified: reverting either regex above to its
// pre-fix form (dropping `(?:\|\||\?\?)?` or `\??\.?`) makes the matching
// assertion below fail by name.
test("el.hidden ||=/??= and el.toggleAttribute?.(\"hidden\", ...) (optional chaining) are recognized as hidden-toggle sites", () => {
  const fixturePath = path.join(__dirname, ".tmp-compound-operator-fixture.js");
  writeFileSync(
    fixturePath,
    [
      'const a = document.createElement("button");',
      'a.className = "probe-or-equals";',
      "a.hidden ||= true;",
      'const b = document.createElement("button");',
      'b.className = "probe-nullish-equals";',
      "b.hidden ??= true;",
      'const c = document.createElement("button");',
      'c.className = "probe-toggle-optional-chain";',
      'c.toggleAttribute?.("hidden", true);',
    ].join("\n"),
  );
  try {
    const found = findHiddenToggledClasses([fixturePath], new Map());
    assert.ok(found.has("probe-or-equals"), "el.hidden ||= true not recognized");
    assert.ok(found.has("probe-nullish-equals"), "el.hidden ??= true not recognized");
    assert.ok(found.has("probe-toggle-optional-chain"), 'el.toggleAttribute?.("hidden", ...) not recognized');
  } finally {
    rmSync(fixturePath, { force: true });
  }
});

// N2 (Opus review nitpick, WP 4e.1 fix round): the `sectionHeadingFactory`
// branch above hardcodes `new Set(["sec"])` because this scan cannot trace
// INTO a helper function to see what class IT assigns — it has to know the
// answer up front. That hardcode can silently drift from the real
// `sectionHeading()` implementation (web/js/views/downloads.js) if a future
// change gives it a different class; this test reads the real source and
// asserts the mapping still holds, so drift fails loudly here instead of
// silently under-covering the lint.
test("sectionHeadingFactory's hardcoded ['sec'] mapping still matches the real sectionHeading() implementation", () => {
  const downloadsJs = readFileSync(path.join(jsDir, "views", "downloads.js"), "utf8");
  const fnMatch = /function sectionHeading\([^)]*\)\s*\{([\s\S]*?)\n\}/.exec(downloadsJs);
  assert.ok(fnMatch, "sectionHeading() not found in downloads.js — did it move or get renamed?");
  assert.match(
    fnMatch[1],
    /\.className\s*=\s*["']sec["']/,
    "sectionHeading() no longer assigns className \"sec\" — update the hardcoded mapping in findHiddenToggledClasses above",
  );
});

// ---------------------------------------------------------------------
// WP 4e.8: the DI-seed idiom's cross-file resolution and its collision
// guard, plus direct named pins on the two live instances the cross-file
// fix restores coverage for.
// ---------------------------------------------------------------------

// The real-tree gate: if a future coder's DI seed reuses an existing
// property/parameter name for a different element, this fails BY NAME
// (both files, both ids) rather than silently resolving last-write-wins.
test("DI-seed idiom: no identifier name resolves to two different ids across the real DI seeds in web/js/", () => {
  assert.deepEqual(
    diSeedCollisions,
    [],
    `DI-seed collision(s) — same identifier, different ids, would corrupt cross-file resolution: ${JSON.stringify(diSeedCollisions, null, 2)}`,
  );
});

// Proves the collision guard above is not vacuous: constructs an ACTUAL
// collision in throwaway fixtures (never web/js/) and asserts buildDiSeedMap
// reports it by name, naming both sites.
test("DI-seed collision guard fires on a constructed fixture: same identifier, two files, two different ids", () => {
  const fileA = path.join(__dirname, ".tmp-di-collision-a.js");
  const fileB = path.join(__dirname, ".tmp-di-collision-b.js");
  writeFileSync(fileA, 'createThing({ dupIdent: document.getElementById("id-one") });\n');
  writeFileSync(fileB, 'createOther({ dupIdent: document.getElementById("id-two") });\n');
  try {
    const { collisions } = buildDiSeedMap([fileA, fileB]);
    assert.equal(collisions.length, 1, `expected exactly one collision, got: ${JSON.stringify(collisions)}`);
    const [c] = collisions;
    assert.equal(c.ident, "dupIdent");
    assert.match(c.siteA, /id-one/);
    assert.match(c.siteB, /id-two/);
  } finally {
    rmSync(fileA, { force: true });
    rmSync(fileB, { force: true });
  }
});

// Sanity: the SAME identifier seeded with the SAME id twice (e.g. two
// different call sites legitimately re-declaring the same pairing) is not
// a collision — only a different id under the same name is.
test("DI-seed collision guard: the same identifier/id pair repeated across files is NOT a collision", () => {
  const fileA = path.join(__dirname, ".tmp-di-nocollision-a.js");
  const fileB = path.join(__dirname, ".tmp-di-nocollision-b.js");
  writeFileSync(fileA, 'createThing({ sameIdent: document.getElementById("same-id") });\n');
  writeFileSync(fileB, 'createOther({ sameIdent: document.getElementById("same-id") });\n');
  try {
    const { collisions } = buildDiSeedMap([fileA, fileB]);
    assert.deepEqual(collisions, []);
  } finally {
    rmSync(fileA, { force: true });
    rmSync(fileB, { force: true });
  }
});

// Proves the cross-file fallback in findHiddenToggledClasses is
// load-bearing, not incidental: a DI seed declared in one fixture file must
// be visible to a `.hidden =` toggle on the SAME identifier NAME in a
// DIFFERENT fixture file. Mutation-verified: replacing the `set === undefined`
// fallback branch in findHiddenToggledClasses with a no-op (i.e. reverting
// to the pre-WP-4e.8 per-file-only resolution) makes this test fail by name
// — the exact regression this WP closes.
test("DI-seed idiom resolves cross-file: a seed in one file is visible to a .hidden = toggle on the same identifier name in a different file", () => {
  const seedFile = path.join(__dirname, ".tmp-di-seed.js");
  const consumerFile = path.join(__dirname, ".tmp-di-consumer.js");
  writeFileSync(seedFile, 'createPanel({ probeDiIdent: document.getElementById("probe-di-id") });\n');
  writeFileSync(consumerFile, ["function wire(probeDiIdent) {", "  probeDiIdent.hidden = true;", "}"].join("\n"));
  try {
    const idMap = new Map([["probe-di-id", "probe-di-class"]]);
    const { map: fixtureDiSeedMap, collisions } = buildDiSeedMap([seedFile, consumerFile]);
    assert.deepEqual(collisions, [], "fixture must not itself trip the collision guard");
    const found = findHiddenToggledClasses([seedFile, consumerFile], idMap, fixtureDiSeedMap);
    assert.ok(
      found.has("probe-di-class"),
      "the DI seed declared in one file was not resolved for the .hidden = toggle in a different file — cross-file resolution regressed",
    );
  } finally {
    rmSync(seedFile, { force: true });
    rmSync(consumerFile, { force: true });
  }
});

// Confirms local resolution still wins over the cross-file fallback: an
// identifier that WAS locally seeded (even to a class set unrelated to the
// DI seed's) must never be overridden by a same-named DI seed elsewhere.
test("DI-seed fallback never overrides a local seed: an identifier resolved locally keeps its LOCAL classes, not the cross-file DI seed's", () => {
  const seedFile = path.join(__dirname, ".tmp-di-local-seed.js");
  const consumerFile = path.join(__dirname, ".tmp-di-local-consumer.js");
  writeFileSync(seedFile, 'createPanel({ sharedName: document.getElementById("di-side-id") });\n');
  writeFileSync(
    consumerFile,
    [
      'const sharedName = document.createElement("div");',
      'sharedName.className = "locally-seeded-class";',
      "sharedName.hidden = true;",
    ].join("\n"),
  );
  try {
    const idMap = new Map([["di-side-id", "di-side-class"]]);
    const { map: fixtureDiSeedMap } = buildDiSeedMap([seedFile, consumerFile]);
    const found = findHiddenToggledClasses([seedFile, consumerFile], idMap, fixtureDiSeedMap);
    assert.ok(found.has("locally-seeded-class"), "the local seed's own class was not recorded");
    assert.ok(!found.has("di-side-class"), "the local seed was incorrectly overridden by the cross-file DI seed");
  } finally {
    rmSync(seedFile, { force: true });
    rmSync(consumerFile, { force: true });
  }
});

// MF1 (Opus review must-fix, WP 4e.8 fix round): every test above proves the
// LINT LOGIC is correct in the abstract, entirely on throwaway fixtures —
// nothing yet pinned that PRODUCTION CODE still actually exercises the one
// idiom this lint's cross-file resolution understands. The reviewer
// measured this directly (M5b): pull app.js's five `document.getElementById(
// ...)` calls into local `const`s and pass them into `createRailPanel({
// elements: { headEl, vaultNameEl, footEl, cacheEl, versionEl, ... } })` as
// shorthand properties instead of the colon form — syntactically valid,
// `node --check` clean, behaviourally IDENTICAL at runtime — and
// `DI_SEED_RE` (which only matches the `ident: document.getElementById(...)`
// colon form) then matches nothing at all in app.js: cross-file resolution
// goes silently dead and the full suite (fixtures included) stayed green.
// Same structural-pin lesson as the `sectionHeadingFactory` drift test above
// and docs/LEARNINGS.md's WP 4f entry: a lint whose fixtures all pass can
// still be completely disconnected from the real tree it exists to police.
// This test reads the REAL `hiddenToggledClasses`/`diSeedMap` built from
// web/js/ itself (no fixture) and is verified to die under BOTH known ways
// that disconnection happens: the cross-file fallback disabled (M3) and the
// app.js shorthand-property refactor above (M5b) — both mutations applied
// by hand to this exact file/app.js, run, and reverted for this fix (see
// the coder's report).
test("real tree: the DI-seed idiom is actually IN USE on production code — rail-head/rail-foot are observed as hidden-toggled, not merely handled correctly in the abstract", () => {
  assert.ok(
    hiddenToggledClasses.has("rail-head"),
    "rail-head no longer appears as hidden-toggled — the DI-seed cross-file resolution stopped seeing " +
      "components/rail-panel.js's headEl.hidden site (fallback disabled, or app.js no longer uses the colon-form DI-seed idiom)",
  );
  assert.ok(
    hiddenToggledClasses.has("rail-foot"),
    "rail-foot no longer appears as hidden-toggled — the DI-seed cross-file resolution stopped seeing " +
      "components/rail-panel.js's footEl.hidden site (fallback disabled, or app.js no longer uses the colon-form DI-seed idiom)",
  );
  // Sharpens the failure message above by naming the exact seed pairs
  // expected straight out of app.js, rather than only the downstream symptom.
  assert.equal(
    diSeedMap.get("headEl")?.id,
    "rail-head",
    'diSeedMap has no "headEl" -> "rail-head" DI seed — app.js no longer uses the ' +
      '`headEl: document.getElementById("rail-head")` colon-form idiom this lint\'s cross-file resolution understands',
  );
  assert.equal(
    diSeedMap.get("footEl")?.id,
    "rail-foot",
    'diSeedMap has no "footEl" -> "rail-foot" DI seed — app.js no longer uses the ' +
      '`footEl: document.getElementById("rail-foot")` colon-form idiom this lint\'s cross-file resolution understands',
  );
});

// Direct named pins (brief requirement: each guard must fail BY NAME when
// deleted, independent of whether the JS-side cross-reference resolves).
// These read `guardSelectors` directly — populated from `combinedCss`
// (theme.css + app.css combined, see the top of this file), with no
// dependency on the JS scan at all — so they catch a deleted guard even if
// some future JS refactor outruns this file's parser again, and even if the
// guard rule itself moved from app.css into theme.css.
test("named pin: .rail-head[hidden]{ display:none; } guard rule exists (app.css or theme.css)", () => {
  assert.ok(
    guardSelectors.has(".rail-head[hidden]"),
    ".rail-head[hidden]{ display:none; } is missing — .rail-head carries an author display:block rule " +
      "(app.css BP-L block) and components/rail-panel.js sets headEl.hidden, so without this guard the toggle silently does nothing",
  );
});

test("named pin: .rail-foot[hidden]{ display:none; } guard rule exists (app.css or theme.css)", () => {
  assert.ok(
    guardSelectors.has(".rail-foot[hidden]"),
    ".rail-foot[hidden]{ display:none; } is missing — .rail-foot carries an author display:block rule " +
      "(app.css BP-L block) and components/rail-panel.js sets footEl.hidden, so without this guard the toggle silently does nothing",
  );
});

// Note from the brief, verified: `.rail-version` carries NO author
// `display` rule (only margin/font/overflow), so the general Lint 1
// cross-reference correctly does not (and must not) demand a
// `.rail-version[hidden]` guard for it, even though rail-panel.js also
// toggles `versionEl.hidden`. A guard demanded here would mean this WP's
// fix went over-broad.
test("sanity: .rail-version has no author display rule, so no [hidden] guard is required for it", () => {
  assert.ok(!displayRulesByClass.has("rail-version"), "rail-version unexpectedly has an author display rule now");
});

// ---------------------------------------------------------------------
// Lint 2: no `!important` outside the reduced-motion block.
// ---------------------------------------------------------------------

function findImportantViolations(cssText) {
  const css = stripComments(cssText);
  const mediaSpans = [];
  const mediaRe = /@media[^{]*\{/g;
  let m;
  while ((m = mediaRe.exec(css))) {
    const openBrace = m.index + m[0].length - 1;
    let depth = 1;
    let k = openBrace + 1;
    while (depth > 0 && k < css.length) {
      if (css[k] === "{") depth++;
      else if (css[k] === "}") depth--;
      k++;
    }
    mediaSpans.push({ header: m[0], bodyStart: openBrace + 1, bodyEnd: k - 1 });
    mediaRe.lastIndex = k;
  }

  const violations = [];
  const impRe = /!important/g;
  let im;
  while ((im = impRe.exec(css))) {
    const idx = im.index;
    const insideReducedMotion = mediaSpans.some(
      (s) => idx > s.bodyStart && idx < s.bodyEnd && /prefers-reduced-motion/.test(s.header),
    );
    if (!insideReducedMotion) {
      violations.push(css.slice(Math.max(0, idx - 60), idx + 15).trim());
    }
  }
  return violations;
}

test("no `!important` appears anywhere except inside the prefers-reduced-motion block", () => {
  const violations = findImportantViolations(combinedCss);
  assert.deepEqual(violations, [], `!important found outside prefers-reduced-motion: ${JSON.stringify(violations)}`);
});

test("sanity: the reduced-motion block itself still contains !important (proves the allow-list branch is reachable, not vacuous)", () => {
  assert.ok(/prefers-reduced-motion[\s\S]*?!important/.test(stripComments(themeCss)));
});
