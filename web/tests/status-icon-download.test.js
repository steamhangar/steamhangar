/**
 * The running download glyph, "arrow falls through" (WP WEB-FIX-7).
 *
 * Phone feedback on the old animation: the arrow "only moves down a little
 * and then fades out" (2px of drift plus an opacity fade). The user picked
 * Weg A: two identical arrows one period apart fall through the badge,
 * clipped to the badge's disc, in a seamless linear loop with no fading.
 *
 * Guarantees pinned here, each one mutation-checked (see web/tests/
 * README.md, WP WEB-FIX-7):
 *   1. The keyframes animate transform only (no opacity anywhere).
 *   2. The two arrows are exactly one period apart and the keyframes move
 *      the group by exactly that period, linearly: a seamless loop.
 *   3. The running badge clips to its own disc, and building icons never
 *      produces duplicate ids across instances.
 *   4. Geometry: at rest the trailing arrow is fully outside the disc (the
 *      static glyph is ONE arrow); at the end frame the leading arrow is
 *      fully outside it too (>= 1 unit margin), so the snap back to the
 *      start frame shows no change; and at every phase of the loop at least
 *      one arrow is mostly (>= 65%, measured 70%) inside it (the icon never
 *      looks empty).
 *   5. prefers-reduced-motion still covers it, and the animation has no
 *      fill-mode that would freeze it on its end frame.
 *   6. Only k-running animates the download glyph; the badge itself never
 *      moves; paused and every other kind are untouched.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { createFakeDom } from "./fake-dom.js";

const dom = createFakeDom();
globalThis.document = dom.document;
globalThis.window = dom.window;

const { createStatusIcon, STATUS_LABEL, DOWNLOAD_FALL_PERIOD } = await import(
  "../js/components/status-icon.js"
);

const webDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const themeCss = readFileSync(path.join(webDir, "css", "theme.css"), "utf8");
const appCss = readFileSync(path.join(webDir, "css", "app.css"), "utf8");

// ---------------------------------------------------------------------
// Small CSS helpers (each CSS test file carries its own, see header-art /
// css-hygiene). Brace-balanced, comment-stripped; records the @media
// headers a rule sits in and collects @keyframes bodies by name.
// ---------------------------------------------------------------------

function stripComments(css) {
  return css.replace(/\/\*[\s\S]*?\*\//g, "");
}

function parseCss(cssText) {
  const css = stripComments(cssText);
  const rules = [];
  const keyframes = new Map();
  function block(start, end, media) {
    let j = start;
    while (j < end) {
      const brace = css.indexOf("{", j);
      if (brace === -1 || brace >= end) break;
      const header = css.slice(j, brace).trim();
      let depth = 1;
      let k = brace + 1;
      while (depth > 0 && k < end) {
        if (css[k] === "{") depth++;
        else if (css[k] === "}") depth--;
        k++;
      }
      const body = css.slice(brace + 1, k - 1);
      const kf = /^@keyframes\s+([\w-]+)/.exec(header);
      if (kf) keyframes.set(kf[1], body);
      else if (/^@(media|supports)/.test(header)) block(brace + 1, k - 1, [...media, header]);
      else if (!header.startsWith("@")) {
        for (const sel of header.split(",")) {
          if (sel.trim()) rules.push({ selector: sel.trim().replace(/\s+/g, " "), body, media });
        }
      }
      j = k;
    }
  }
  block(0, css.length, []);
  return { rules, keyframes };
}

/** `{prop: value}` of one declaration block (last declaration wins). */
function decls(body) {
  const out = {};
  for (const part of body.split(";")) {
    const i = part.indexOf(":");
    if (i === -1) continue;
    out[part.slice(0, i).trim()] = part.slice(i + 1).trim();
  }
  return out;
}

/** Keyframe steps of one @keyframes body: [{at: 0..1, decls}]. */
function keyframeSteps(body) {
  const steps = [];
  for (const m of body.matchAll(/([\w%.,\s]+)\{([^}]*)\}/g)) {
    for (const sel of m[1].split(",")) {
      const s = sel.trim();
      const at = s === "from" ? 0 : s === "to" ? 1 : parseFloat(s) / 100;
      steps.push({ at, decls: decls(m[2]) });
    }
  }
  return steps.sort((a, b) => a.at - b.at);
}

function translateYpx(transform) {
  const m = /^translateY\((-?[\d.]+)(px)?\)$/.exec(transform.trim());
  assert.ok(m, `expected a plain translateY(...) transform, got ${JSON.stringify(transform)}`);
  return parseFloat(m[1]);
}

const theme = parseCss(themeCss);
const app = parseCss(appCss);
const allRules = [...theme.rules, ...app.rules];

function runningDlaRule() {
  const rule = theme.rules.find((r) => r.selector === ".sic.k-running .dla" && r.media.length === 0);
  assert.ok(rule, "theme.css has no top-level `.sic.k-running .dla` rule");
  return rule;
}

/** The keyframes name the running arrow group actually uses. */
function runningAnimation() {
  const anim = decls(runningDlaRule().body).animation;
  assert.ok(anim, "`.sic.k-running .dla` declares no animation");
  const name = anim.split(/\s+/).find((tok) => theme.keyframes.has(tok));
  assert.ok(name, `animation ${JSON.stringify(anim)} names no @keyframes in theme.css`);
  return { shorthand: anim, name, steps: keyframeSteps(theme.keyframes.get(name)) };
}

// ---------------------------------------------------------------------
// DOM helpers over the fake-dom tree.
// ---------------------------------------------------------------------

function svgOf(icon) {
  const svg = icon.children.find((c) => c.tagName === "SVG");
  assert.ok(svg, "status icon has no <svg>");
  return svg;
}

function descendants(el) {
  const out = [];
  for (const c of el.children) out.push(c, ...descendants(c));
  return out;
}

/** SVG nodes get their class via setAttribute("class", ...) (status-icon's
 * svgEl), which fake-dom keeps in the attribute map, not in classList. */
function hasClass(el, cls) {
  const attr = el.getAttribute("class");
  return el.classList.contains(cls) || (attr !== null && attr.split(/\s+/).includes(cls));
}

function byClass(el, cls) {
  return descendants(el).filter((d) => hasClass(d, cls));
}

function directPathDs(group) {
  return group.children.filter((c) => c.tagName === "PATH").map((p) => p.getAttribute("d"));
}

function parseTranslate(attr) {
  const m = /^translate\(\s*(-?[\d.]+)[\s,]+(-?[\d.]+)\s*\)$/.exec(String(attr).trim());
  assert.ok(m, `expected translate(x y), got ${JSON.stringify(attr)}`);
  return { x: parseFloat(m[1]), y: parseFloat(m[2]) };
}

/** Line segments of a simple absolute M/L/V path ("M12 3.5V13",
 * "M7.4 8.7 12 13.3 16.6 8.7"): [{x1,y1,x2,y2}]. Throws on anything else. */
function segments(d) {
  assert.match(d, /^[MLV\d\s.,-]+$/, `segments() only reads absolute M/L/V paths, got ${d}`);
  const out = [];
  let x = 0;
  let y = 0;
  for (const m of d.matchAll(/([MLV])([^MLV]*)/g)) {
    const nums = m[2].trim().split(/[\s,]+/).filter(Boolean).map(Number);
    if (m[1] === "V") {
      for (const ny of nums) {
        out.push({ x1: x, y1: y, x2: x, y2: ny });
        y = ny;
      }
      continue;
    }
    for (let i = 0; i < nums.length; i += 2) {
      const [nx, ny] = [nums[i], nums[i + 1]];
      // The first pair after M is a move; every further pair is a line.
      if (!(m[1] === "M" && i === 0)) out.push({ x1: x, y1: y, x2: nx, y2: ny });
      [x, y] = [nx, ny];
    }
  }
  return out;
}

// ---------------------------------------------------------------------
// 1. Transform only, no opacity.
// ---------------------------------------------------------------------

test("the running arrow keyframes animate transform and nothing else (no opacity fade)", () => {
  const { steps, name } = runningAnimation();
  assert.ok(steps.length >= 2, `@keyframes ${name} needs a start and an end step`);
  for (const step of steps) {
    assert.deepEqual(Object.keys(step.decls), ["transform"], `@keyframes ${name} step ${step.at} animates ${Object.keys(step.decls)}`);
  }
  assert.doesNotMatch(stripComments(theme.keyframes.get(name)), /opacity/);
  assert.equal(theme.keyframes.has("vault-dlslide"), false, "the old drift-and-fade keyframes must be gone");
});

// ---------------------------------------------------------------------
// 2. Seamless loop: two arrows exactly one period apart, group moves by
//    exactly one period per cycle, linearly, forever.
// ---------------------------------------------------------------------

test("the running glyph holds two identical arrows exactly one period apart", () => {
  const icon = createStatusIcon("running");
  const [dla] = byClass(svgOf(icon), "dla");
  assert.ok(dla, "running icon has no .dla group");
  const nexts = byClass(dla, "dlnext");
  assert.equal(nexts.length, 1, "exactly one trailing arrow inside .dla (DOM stays small)");
  const lead = directPathDs(dla);
  const trail = directPathDs(nexts[0]);
  assert.deepEqual(lead, ["M12 3.5V13", "M7.4 8.7 12 13.3 16.6 8.7"], "leading arrow is the unchanged static arrow");
  assert.deepEqual(trail, lead, "the trailing arrow is the same shape");
  const t = parseTranslate(nexts[0].getAttribute("transform"));
  assert.equal(t.x, 0);
  assert.equal(t.y, -DOWNLOAD_FALL_PERIOD, "trailing arrow sits exactly one period ABOVE the leading one");
  assert.equal(DOWNLOAD_FALL_PERIOD, 32);
});

test("the keyframes move the group by exactly one period, linearly and forever", () => {
  const { steps, shorthand } = runningAnimation();
  const first = steps[0];
  const last = steps[steps.length - 1];
  assert.equal(first.at, 0);
  assert.equal(last.at, 1);
  assert.equal(steps.length, 2, "two steps only: any intermediate step would bend the constant fall speed");
  const travel = translateYpx(last.decls.transform) - translateYpx(first.decls.transform);
  assert.equal(translateYpx(first.decls.transform), 0, "a cycle starts at the rest position");
  assert.equal(travel, DOWNLOAD_FALL_PERIOD, "the group falls exactly the spacing of the two arrows per cycle");
  const tokens = shorthand.split(/\s+/);
  assert.ok(tokens.includes("linear"), `timing must be linear for a seamless seam, got ${shorthand}`);
  assert.ok(tokens.includes("infinite"), `must loop, got ${shorthand}`);
  const dur = tokens.find((tk) => /^[\d.]+m?s$/.test(tk));
  const seconds = dur.endsWith("ms") ? parseFloat(dur) / 1000 : parseFloat(dur);
  assert.ok(seconds >= 1.2 && seconds <= 2.0, `period ${dur} is outside the agreed ~1.6s band`);
  const speed = DOWNLOAD_FALL_PERIOD / seconds;
  assert.ok(speed >= 15 && speed <= 25, `fall speed ${speed} units/s is outside the agreed ~20 units/s`);
});

// ---------------------------------------------------------------------
// 3. The clip, and id uniqueness across instances.
// ---------------------------------------------------------------------

test("the running badge clips its content to its own disc", () => {
  const clipRule = theme.rules.find((r) => r.selector === ".sic.k-running" && "clip-path" in decls(r.body));
  assert.ok(clipRule, "no clip-path on .sic.k-running");
  assert.equal(clipRule.media.length, 0, "the clip must not depend on a media query");
  assert.equal(decls(clipRule.body)["clip-path"], "circle(50%)");
  const base = theme.rules.find((r) => r.selector === ".sic" && r.media.length === 0);
  assert.equal(decls(base.body)["border-radius"], "50%", "circle(50%) only equals the visual disc while .sic is a border-radius:50% square");
  assert.match(decls(base.body).width, /var\(--sz\)/);
  assert.match(decls(base.body).height, /var\(--sz\)/);
  // No size override anywhere may make the badge non-square (that would
  // turn circle(50%) into a clip that cuts the disc).
  for (const r of allRules) {
    if (!/\.sic\b/.test(r.selector)) continue;
    const d = decls(r.body);
    for (const prop of ["width", "height", "border-radius", "clip-path", "overflow"]) {
      if (!(prop in d)) continue;
      if (r.selector === ".sic" || r.selector === ".sic.k-running" || r.selector === ".sr-only") continue;
      if (/\.sic svg$/.test(r.selector) && (prop === "width" || prop === "height" || prop === "overflow")) continue;
      assert.fail(`${r.selector} overrides ${prop}; re-check the running clip at that size`);
    }
  }
});

test("building many icons never yields a duplicate id (a clip reference can never collide)", () => {
  const seen = new Map();
  const kinds = Object.keys(STATUS_LABEL);
  for (let round = 0; round < 3; round++) {
    for (const kind of kinds) {
      const icon = createStatusIcon(kind, { size: round === 0 ? "sm" : round === 1 ? "md" : "lg" });
      for (const el of [icon, ...descendants(icon)]) {
        const id = el.getAttribute("id");
        if (id === null) continue;
        assert.ok(!seen.has(id), `id ${JSON.stringify(id)} built twice (${seen.get(id)} and ${kind})`);
        seen.set(id, kind);
      }
    }
  }
  // Same for any url(#...) reference: it must point at an id of its own icon.
  const a = createStatusIcon("running");
  for (const el of descendants(a)) {
    for (const attr of ["clip-path", "mask", "href"]) {
      const v = el.getAttribute(attr);
      if (v === null) continue;
      const ref = /#([\w-]+)/.exec(v);
      if (!ref) continue;
      assert.ok(descendants(a).some((d) => d.getAttribute("id") === ref[1]), `${attr}=${v} points outside its icon`);
    }
  }
});

// ---------------------------------------------------------------------
// 4. Geometry: one arrow at rest, never an empty disc while moving.
// ---------------------------------------------------------------------

function geometry() {
  const svgRule = theme.rules.find((r) => r.selector === ".sic svg" && r.media.length === 0);
  const pct = parseFloat(decls(svgRule.body).width);
  assert.equal(decls(svgRule.body).height, decls(svgRule.body).width, "the svg box is square");
  assert.equal(decls(svgRule.body).overflow, "visible", "the arrows travel outside the svg box; without overflow:visible the svg would clip them to its 64% square");
  // The svg box is pct% of the badge and centred in it (grid,
  // place-items:center), the viewBox is 24 units: the disc in viewBox
  // units is centred on (12,12) with radius 12 / (pct/100).
  const icon = createStatusIcon("running");
  const svg = svgOf(icon);
  assert.equal(svg.getAttribute("viewBox"), "0 0 24 24");
  const half = parseFloat(svg.getAttribute("stroke-width")) / 2;
  const r = 12 / (pct / 100);
  const [dla] = byClass(svg, "dla");
  const [next] = byClass(dla, "dlnext");
  const off = parseTranslate(next.getAttribute("transform")).y;
  const lead = directPathDs(dla).flatMap(segments);
  const trail = directPathDs(next).flatMap(segments).map((s) => ({ ...s, y1: s.y1 + off, y2: s.y2 + off }));
  return { r, half, lead, trail };
}

/** Length of segment s (shifted down by dy) inside the disc shrunk by
 * `inset`, sampled. */
function insideLength(segs, dy, r, inset) {
  let total = 0;
  for (const s of segs) {
    const n = 200;
    const len = Math.hypot(s.x2 - s.x1, s.y2 - s.y1);
    for (let i = 0; i < n; i++) {
      const t = (i + 0.5) / n;
      const x = s.x1 + (s.x2 - s.x1) * t;
      const y = s.y1 + (s.y2 - s.y1) * t + dy;
      if (Math.hypot(x - 12, y - 12) <= r - inset) total += len / n;
    }
  }
  return total;
}

/** Closest distance of any point of segs (shifted by dy) to the disc
 * centre, minus the stroke half-width: > r means fully outside. */
function nearestEdge(segs, dy, half) {
  let best = Infinity;
  for (const s of segs) {
    for (let i = 0; i <= 200; i++) {
      const t = i / 200;
      const x = s.x1 + (s.x2 - s.x1) * t;
      const y = s.y1 + (s.y2 - s.y1) * t + dy;
      best = Math.min(best, Math.hypot(x - 12, y - 12) - half);
    }
  }
  return best;
}

test("at rest (and under reduced motion) the trailing arrow is fully outside the disc and the leading arrow fully inside", () => {
  const { r, half, lead, trail } = geometry();
  assert.ok(nearestEdge(trail, 0, half) > r, "the parked trailing arrow (stroke included) must not peek into the disc");
  const leadLen = insideLength(lead, 0, r, 0);
  const fullLen = insideLength(lead, 0, 1e9, -1e9);
  assert.ok(Math.abs(leadLen - fullLen) < 1e-6, "the leading arrow is wholly inside the disc at rest");
});

test("at the end frame the leading arrow has fully left the disc, so the snap back to 0 is invisible", () => {
  const { r, half, lead, trail } = geometry();
  // Margin of at least 1 viewBox unit (about 0.45px on a 17px badge) for
  // pixel snapping / anti-aliasing of the rim.
  const margin = nearestEdge(lead, DOWNLOAD_FALL_PERIOD, half) - r;
  assert.ok(margin >= 1, `leading arrow ink is only ${margin.toFixed(2)} units outside the disc at the end frame (need >= 1)`);
  // ...and the trailing arrow then sits exactly where the leading one
  // started, so the end frame shows the same single rest-position arrow.
  const restLen = insideLength(lead, 0, r, 0);
  const endLen = insideLength(trail, DOWNLOAD_FALL_PERIOD, r, 0);
  assert.ok(Math.abs(restLen - endLen) < 1e-9);
});

test("at every phase of the loop at least one arrow is mostly inside the disc (never an empty badge)", () => {
  const { r, half, lead, trail } = geometry();
  const full = insideLength(lead, 0, 1e9, -1e9);
  // Measured at P=32: at the worst phase the more-visible arrow has 70.1%
  // of its stroke length inside the disc (its ink included, i.e. the disc
  // shrunk by the stroke half-width). With the period wide enough for a
  // clean exit, there is a moment where one arrow is leaving and the next
  // is entering, so "a whole arrow is always inside" is NOT true and is
  // not claimed. 0.65 pins today's value with a little room.
  const WORST_SHARE = 0.65;
  let worst = Infinity;
  for (let i = 0; i <= 240; i++) {
    const dy = (DOWNLOAD_FALL_PERIOD * i) / 240;
    const visible = Math.max(insideLength(lead, dy, r, half), insideLength(trail, dy, r, half));
    worst = Math.min(worst, visible);
  }
  assert.ok(worst >= full * WORST_SHARE, `at the worst phase only ${worst.toFixed(2)} of ${full.toFixed(2)} units of one arrow are visible`);
});

// ---------------------------------------------------------------------
// 5. Reduced motion.
// ---------------------------------------------------------------------

test("prefers-reduced-motion still stops it: the wildcard override exists and the arrow animation cannot outlive it", () => {
  const rm = theme.rules.filter((r) => r.media.some((m) => /prefers-reduced-motion:\s*reduce/.test(m)) && r.selector === "*");
  assert.equal(rm.length, 1, "the whole-app reduced-motion wildcard rule is missing");
  const d = decls(rm[0].body);
  assert.match(d["animation-duration"] || "", /!important/);
  assert.match(d["animation-iteration-count"] || "", /^1\s*!important/);
  const { shorthand } = runningAnimation();
  const tokens = shorthand.split(/\s+/);
  for (const bad of ["forwards", "both"]) {
    assert.ok(!tokens.includes(bad), `fill-mode ${bad} would freeze the reduced-motion glyph on the end frame`);
  }
  const timeTokens = tokens.filter((tk) => /^-?[\d.]+m?s$/.test(tk));
  assert.equal(timeTokens.length, 1, "no animation-delay (the override does not touch delays)");
  const dla = decls(runningDlaRule().body);
  assert.ok(!("animation-fill-mode" in dla) && !("animation-delay" in dla));
  assert.doesNotMatch(runningDlaRule().body, /!important/, "an !important here would beat the reduced-motion override");
});

test("the static running glyph keeps today's look: one arrow, no baseline; Not cached keeps arrow + baseline", () => {
  const hide = theme.rules.find((r) => r.selector === ".sic.k-running .dlbase");
  assert.ok(hide && decls(hide.body).display === "none", "running hides the baseline in every motion mode");
  assert.equal(hide.media.length, 0);
  for (const r of allRules) {
    if (/\.dlbase/.test(r.selector) && r !== hide) assert.fail(`unexpected baseline rule ${r.selector}`);
  }
  const none = createStatusIcon("none");
  assert.equal(byClass(svgOf(none), "dlnext").length, 0, "Not cached has a single arrow, no trailing one");
  assert.equal(byClass(svgOf(none), "dlbase").length, 1);
});

// ---------------------------------------------------------------------
// 6. Only k-running animates the download glyph; the badge never moves.
// ---------------------------------------------------------------------

test("only k-running animates the download arrow; no rule moves the badge itself or the paused glyph", () => {
  const { name } = runningAnimation();
  for (const r of allRules) {
    const d = decls(r.body);
    const anim = [d.animation, d["animation-name"]].filter(Boolean).join(" ");
    if (r.media.some((m) => /prefers-reduced-motion/.test(m))) continue;
    if (anim.includes(name)) {
      assert.equal(r.selector, ".sic.k-running .dla", `${r.selector} uses ${name}`);
    }
    if (/\.(dla|dlnext|dlbase)\b/.test(r.selector) && (anim || "transform" in d || "transition" in d)) {
      assert.equal(r.selector, ".sic.k-running .dla", `${r.selector} moves part of the download glyph`);
    }
    if (/\.sic(\.[\w-]+)*$/.test(r.selector) && (anim || "transform" in d)) {
      assert.fail(`${r.selector} moves the whole badge`);
    }
    if (/k-paused/.test(r.selector)) {
      assert.ok(!anim && !("transform" in d), `${r.selector} moves the paused glyph`);
    }
  }
  // The trailing arrow only exists in the running glyph.
  for (const kind of Object.keys(STATUS_LABEL)) {
    const n = byClass(svgOf(createStatusIcon(kind)), "dlnext").length;
    assert.equal(n, kind === "running" ? 1 : 0, `${kind} has ${n} trailing arrows`);
  }
});

test("accessibility unchanged: the glyph is aria-hidden and the word is still Downloading", () => {
  const icon = createStatusIcon("running");
  assert.equal(svgOf(icon).getAttribute("aria-hidden"), "true");
  const label = icon.children.find((c) => c.classList.contains("sr-only"));
  assert.equal(label.textContent, "Downloading");
  assert.equal(icon.className, "sic k-running");
});
