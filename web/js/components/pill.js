/**
 * Status pill (WP UI-KIT-1, docs/design/rc20/kit.html "Status vocabulary").
 *
 * `<span class="pill TONE"><span class="sic k-KIND">…</span>TEXT</span>`:
 * the existing status-icon disc (components/status-icon.js — shape first,
 * word second, colour third) plus the visible word and, from css/kit.css,
 * a tinted background so the state reads at card size. The disc keeps its
 * own kind colour; the TONE class only tints the pill. Motion is
 * untouched: it is theme.css's `.sic.k-running`/`.k-updating`/`.k-verify`
 * animation, stopped by prefers-reduced-motion.
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

/** Pill tones kit.css knows. `null` = the neutral grey pill. */
export const PILL_TONES = Object.freeze(["ok", "run", "acc", "stale", "danger", "none"]);

/**
 * Default tone per status kind, from kit.html's vocabulary row: Current and
 * Online are `ok`; the three active kinds are `run` (the verify disc itself
 * stays accent-coloured); "Update ready" is `acc`; a warning (agent
 * outdated) is `stale`; a failure `danger`; the not-cached family `none`;
 * paused, cancelled, check and not-in-use take the neutral pill.
 */
export const DEFAULT_PILL_TONE = Object.freeze({
  cached: "ok",
  running: "run",
  updating: "run",
  verify: "run",
  stale: "acc",
  warn: "stale",
  error: "danger",
  none: "none",
  partial: "none",
  unverified: "none",
  paused: null,
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
