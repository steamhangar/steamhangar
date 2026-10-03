/**
 * Small formatting helpers shared by the library and (later) downloads
 * views (WP 4a.3). Pure — unit-tested in web/tests/format.test.js.
 */

/**
 * Bytes -> a short "N GB" string, mirroring the mockup's `gb()` helper
 * (>=100 GB rounds to a whole number, otherwise one decimal place).
 * `null`/`undefined`/non-positive bytes are never faked into a number —
 * callers decide what to show instead (mockup rule: "a never-downloaded
 * game shows the icon alone rather than a fake dash").
 * @param {number | null | undefined} bytes
 * @returns {string | null} `null` when there is nothing honest to print.
 */
export function formatBytesGB(bytes) {
  if (typeof bytes !== "number" || !Number.isFinite(bytes) || bytes <= 0) return null;
  const gb = bytes / 1_073_741_824;
  return (gb >= 100 ? gb.toFixed(0) : gb.toFixed(1)) + " GB";
}

/**
 * Bytes -> a short "N GB" string, WITHOUT collapsing a genuine zero into
 * "nothing to print" the way {@link formatBytesGB} deliberately does
 * (that helper's own test file: "null/undefined/zero/negative never
 * fabricate a number" — a design choice for the library tile badge, where
 * 0 means "never downloaded" and the mockup rule is to show the icon alone).
 * The rail foot (WP 4e.6) needs the OPPOSITE rule for the same input shape:
 * "0 bytes free" is a real, alarming fact (the disk is full) that must
 * render, not a `null`-shaped "we don't know" — collapsing the two would
 * hide a full disk exactly as silently as it hides "no poll has landed yet",
 * which is the ambiguity this whole work package exists to remove. Only
 * `null`/`undefined`/negative/non-finite input (never a valid byte count)
 * still maps to `null` — zero is a legitimate, printable value here.
 * @param {number | null | undefined} bytes
 * @returns {string | null} `null` only for input that could never be a real
 *   byte count; a real zero prints as `"0.0 GB"`.
 */
export function formatBytesGBOrZero(bytes) {
  if (typeof bytes !== "number" || !Number.isFinite(bytes) || bytes < 0) return null;
  const gb = bytes / 1_073_741_824;
  return (gb >= 100 ? gb.toFixed(0) : gb.toFixed(1)) + " GB";
}

/**
 * An ISO-8601 timestamp (`jobs.created_at`/`started_at`/`finished_at`, ...)
 * -> a locale-formatted string for display (WP 4a.5, Downloads history
 * rows). `null`/`undefined`/unparseable input never fabricates a time —
 * same "nothing honest to print" posture as {@link formatBytesGB}.
 * @param {string | null | undefined} iso
 * @returns {string} a display string, or "—" when there is nothing to show.
 */
export function formatTimestamp(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString();
}

/**
 * An ISO-8601 UTC timestamp -> how long ago it was, in words (WP
 * WEB-FEAT-3: "last seen 4 min ago" for a PC, "checked 40 s ago" for the
 * About table). Coarse on purpose — the inputs are a 10-minute agent report
 * and a 60-second server cache, so seconds-level precision would only
 * suggest a liveness these values do not have.
 *
 * `nowMs` is a parameter (default `Date.now()`) so a caller rendering a
 * whole list uses ONE clock for every row, and tests pin it.
 *
 * A timestamp in the future (the server clock is ahead of this browser's)
 * reads "just now" within a minute and "in the future (clocks differ)"
 * beyond that — never a negative age. `null`, `undefined` and unparseable
 * input return `null`: nothing honest to print, the caller decides.
 *
 * @param {string | null | undefined} iso
 * @param {number} [nowMs]
 * @returns {string | null}
 */
export function formatAgo(iso, nowMs = Date.now()) {
  if (typeof iso !== "string" || !iso) return null;
  const t = Date.parse(iso);
  if (!Number.isFinite(t) || !Number.isFinite(nowMs)) return null;
  const seconds = Math.floor((nowMs - t) / 1000);
  if (seconds < -60) return "in the future (clocks differ)";
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.floor(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}
