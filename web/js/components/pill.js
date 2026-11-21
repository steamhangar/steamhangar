/**
 * Status pill (WP UI-KIT-1, docs/design/rc20/kit.html "Status vocabulary").
 *
 * `<span class="pill TONE"><span class="sic k-KIND">…</span>TEXT</span>`:
 * the existing status-icon disc (components/status-icon.js — shape first,
 * word second, colour third) plus the visible word and, from css/kit.css,
 * a tinted background so the state reads at card size. Motion is
 * untouched: it is theme.css's `.sic.k-running`/`.k-updating`/`.k-verify`
 * animation, stopped by prefers-reduced-motion.
 *
 * One tone per state (WP PILL-TONE-1, audit D-1): the disc is drawn by
 * theme.css's `.sic.k-KIND` rule, the word by kit.css's `.pill.TONE`, and
 * both use the same colour token for every kind (the kit's "disc =
 * currentColor = pill tone"). DEFAULT_PILL_TONE below, the `.sic.k-*`
 * backgrounds and docs/design/status-vocabulary.json are pinned against
 * each other by web/tests/design-tokens-contract.test.js. The one split is
 * the neutral grey: word in --dim (4.5:1 text contrast), disc in --dim-2.
 *
 * The pill's text IS the status word, so the icon's screen-reader-only
 * fallback is dropped here — otherwise assistive tech would hear
 * "Downloading Downloading". With no text (icon-only pill) the fallback
 * stays.
 *
 * Only pages that opted into the kit (`.kit` on their root) get the pill
 * styles; see kit.css's header.
 */
import { createStatusIcon } from "./status-icon.js";

/** Pill tones kit.css knows. `null` = the neutral grey pill. No accent
 * tone: status colours never double as the accent (mockup NOTES,
 * "Palette"); the update state was the last `acc` pill (PILL-TONE-1). */
export const PILL_TONES = Object.freeze(["ok", "run", "stale", "danger", "none"]);

/**
 * Default tone per status kind, from kit.html's vocabulary row: Current and
 * Online are `ok`; the three active kinds and paused are `run` (the mockup's
 * "static yellow" paused dot; the pause glyph and the missing motion tell
 * it from Downloading); "Update available", a warning (agent outdated) and
 * "Partially cached" are `stale` (orange: "a download would fix this");
 * a failure `danger`; Not cached and Not verified `none`; cancelled, check
 * and not-in-use take the neutral pill. Since PILL-TONE-1 the disc of
 * every kind has the same colour as this tone.
 */
export const DEFAULT_PILL_TONE = Object.freeze({
  cached: "ok",
  running: "run",
  updating: "run",
  verify: "run",
  paused: "run",
  stale: "stale",
  warn: "stale",
  partial: "stale",
  error: "danger",
  none: "none",
  unverified: "none",
  cancelled: null,
  unknown: null,
  notinuse: null,
});

/**
 * @param {{kind: string, text?: string, tone?: string|null}} options
 *   `kind`: a status-icon kind (STATUS_LABEL key; unknown falls back to
 *   "none" inside createStatusIcon). `text`: the visible word; defaults to
 *   "" (icon only). `tone`: one of PILL_TONES, `null` for the neutral pill,
 *   or omitted to take DEFAULT_PILL_TONE[kind].
 * @returns {HTMLSpanElement}
 */
export function createPill({ kind, text = "", tone } = {}) {
  const resolvedTone = tone === undefined ? DEFAULT_PILL_TONE[kind] ?? null : tone;
  const pill = document.createElement("span");
  pill.className = resolvedTone && PILL_TONES.includes(resolvedTone) ? "pill " + resolvedTone : "pill";

  const icon = createStatusIcon(kind);
  if (text !== "") {
    const fallback = icon.querySelector(".sr-only");
    if (fallback) fallback.remove();
  }
  pill.appendChild(icon);
  if (text !== "") pill.append(String(text));
  return pill;
}
