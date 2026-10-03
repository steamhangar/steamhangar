/**
 * Status-icon component (WP 4a.1).
 *
 * Ports the mockup's shape-first, colour-blind-safe status system
 * (docs/design/vault-app-mockup-NOTES.md, round 5 "Status icons replace
 * status dots"): a filled circle in the status hue carrying a
 * pixel-centred glyph. Colour is only the THIRD cue — shape (this glyph)
 * is first, the status word second (every caller should render the word
 * alongside the icon wherever there is room; this component always
 * includes it as a screen-reader-only label so nothing is icon-only for
 * assistive tech even in the tightest layouts).
 *
 * Motion = "activity right now", never decoration (round 5/6 addendum):
 * only running/updating/verify glyphs move, and only their inner group
 * (.dla / .rot) — never the whole badge. All motion is disabled globally
 * by the prefers-reduced-motion rule in css/theme.css. The running glyph
 * is the one kind whose badge clips its content (WP WEB-FIX-7: two arrows
 * falling through the disc, see buildDownload and theme.css).
 *
 * Built with `document.createElementNS` rather than `innerHTML`: no
 * functional difference under this app's CSP (static SVG markup executes
 * nothing either way), but it keeps every DOM node individually
 * inspectable/testable without a parser round-trip.
 */

const SVG_NS = "http://www.w3.org/2000/svg";

/** The word shown next to (or instead of, for screen readers) the glyph. */
export const STATUS_LABEL = {
  cached: "Current",
  running: "Downloading",
  updating: "Updating",
  stale: "Update ready",
  none: "Not cached",
  paused: "Paused",
  verify: "Verifying",
  error: "Failed",
  warn: "Warning",
  // Added WP 4a.5 for the Downloads history list: a real, terminal job
  // status (WP 3.12) the mockup never modeled as a distinct outcome — its
  // JOBS fixture only ever had done/error. "Cancelled" is deliberately its
  // own word/glyph/colour, not a re-skinned "Failed": stopping a job on
  // purpose is not a failure (api/README.md "The status model" — job
  // outcome honesty, docs/PROJECT_PLAN.md).
  cancelled: "Cancelled",
  // Added WP WEB-FEAT-3 for Settings → About (`GET /v1/about` status
  // words): a component vault-api does not or cannot check ("unknown") and
  // one this setup does not run ("not_in_use"). Neither is a fault, so
  // neither reuses the warning/error glyphs — same reasoning as
  // "cancelled" above. Neutral tone, own shapes ("?" and a dash).
  unknown: "Unknown",
  notinuse: "Not in use",
};

/** Which glyph shape a given status kind uses. */
const KIND_GLYPH = {
  cached: "check",
  none: "download",
  stale: "refresh",
  running: "download",
  updating: "refresh",
  verify: "refresh",
  paused: "pause",
  error: "bang",
  warn: "bang",
  cancelled: "stop",
  unknown: "question",
  notinuse: "dash",
};

function svgEl(tag, attrs) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const key in attrs) node.setAttribute(key, attrs[key]);
  return node;
}

function buildCheck() {
  return [svgEl("path", { d: "M5 12.5 10 17.5 19 7" })];
}

/**
 * Fall period of the running download arrow, in viewBox units (WP
 * WEB-FIX-7). The trailing arrow sits exactly one period above the leading
 * one, and css/theme.css's `vault-dlfall` keyframes move the whole `.dla`
 * group down by exactly this distance per cycle before snapping back.
 * The badge disc is radius 12/0.64 = 18.75 around (12,12). The snap is
 * invisible only if no ink is inside the disc except one rest-position
 * arrow at BOTH ends of the cycle: the parked trailing arrow must be above
 * the disc at rest (P > ~21.4) and the leading arrow must have fully left
 * it at the end (P >= ~28.6). 32 clears both with margin for pixel
 * snapping. Keep it in sync with `vault-dlfall` (pinned by
 * web/tests/status-icon-download.test.js).
 */
export const DOWNLOAD_FALL_PERIOD = 32;

function arrowPaths() {
  return [
    svgEl("path", { d: "M12 3.5V13" }),
    svgEl("path", { d: "M7.4 8.7 12 13.3 16.6 8.7" }),
  ];
}

function buildDownload(kind) {
  // .dla = the arrow (the only animated part), .dlbase = the baseline,
  // hidden while running (mockup: "no line under the ANIMATED arrow").
  const arrow = svgEl("g", { class: "dla" });
  arrow.append(...arrowPaths());
  if (kind === "running") {
    // WP WEB-FIX-7, "arrow falls through": a second arrow parked one
    // period above the first. css/theme.css clips the running badge to its
    // own circle, so at rest (and under prefers-reduced-motion) this one
    // is invisible above the disc; while the group falls it enters from
    // the top as the first one leaves at the bottom. Only the running
    // glyph gets it: every other download glyph (k-none) is unclipped and
    // stays exactly the single static arrow it always was.
    const next = svgEl("g", { class: "dlnext", transform: `translate(0 -${DOWNLOAD_FALL_PERIOD})` });
    next.append(...arrowPaths());
    arrow.append(next);
  }
  const baseline = svgEl("path", { class: "dlbase", d: "M5 19.6h14" });
  return [arrow, baseline];
}

function buildRefresh() {
  // Two opposing curved arrows forming one circle — 180deg rotationally
  // symmetric, so a continuous linear turn loops seamlessly.
  const group = svgEl("g", { class: "rot" });
  group.append(
    svgEl("path", { d: "M4.5 12a7.5 7.5 0 0 1 12.8-5.3" }),
    svgEl("path", { d: "M17.3 2.7v4h-4" }),
    svgEl("path", { d: "M19.5 12a7.5 7.5 0 0 1-12.8 5.3" }),
    svgEl("path", { d: "M6.7 21.3v-4h4" }),
  );
  return [group];
}

function buildBang() {
  return [
    svgEl("path", { d: "M12 5.5V13.2" }),
    svgEl("path", { d: "M12 17.7v.02" }),
  ];
}

function buildPause() {
  return [
    svgEl("rect", { x: "7", y: "5.6", width: "3.4", height: "12.8", rx: "1.3" }),
    svgEl("rect", { x: "13.6", y: "5.6", width: "3.4", height: "12.8", rx: "1.3" }),
  ];
}

function buildStop() {
  // A plain filled square — deliberately NOT the pause glyph (two bars):
  // pause is resumable and keeps the mockup's shape; cancelled is terminal
  // and needs its own silhouette so the two are never confusable at a
  // glance (the whole point of the shape-first status-icon system).
  return [svgEl("rect", { x: "7", y: "7", width: "10", height: "10", rx: "1.6" })];
}

function buildQuestion() {
  return [
    svgEl("path", { d: "M9.1 9.2a2.9 2.9 0 1 1 4.3 2.6c-.9.5-1.4 1.1-1.4 2v.4" }),
    svgEl("path", { d: "M12 17.7v.02" }),
  ];
}

function buildDash() {
  return [svgEl("path", { d: "M7 12h10" })];
}

const GLYPH_BUILDERS = {
  check: buildCheck,
  download: buildDownload,
  refresh: buildRefresh,
  bang: buildBang,
  pause: buildPause,
  stop: buildStop,
  question: buildQuestion,
  dash: buildDash,
};

/**
 * Build a status-icon element.
 *
 * @param {string} kind one of STATUS_LABEL's keys (an unknown kind falls
 *   back to "none", never to a blank/invalid icon).
 * @param {{size?: "sm"|"md"|"lg"}} [options]
 * @returns {HTMLSpanElement}
 */
export function createStatusIcon(kind, { size = "md" } = {}) {
  const knownKind = kind in STATUS_LABEL ? kind : "none";
  const shape = KIND_GLYPH[knownKind];
  const build = GLYPH_BUILDERS[shape] || buildDownload;

  const wrap = document.createElement("span");
  wrap.className = "sic k-" + knownKind + (size === "sm" ? " sic-sm" : size === "lg" ? " sic-lg" : "");

  const svg = svgEl("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" });
  if (shape === "pause" || shape === "stop") {
    svg.setAttribute("fill", "currentColor");
  } else {
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "2.7");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
  }
  svg.append(...build(knownKind));
  wrap.appendChild(svg);

  const label = document.createElement("span");
  label.className = "sr-only";
  label.textContent = STATUS_LABEL[knownKind];
  wrap.appendChild(label);

  return wrap;
}
