/**
 * WP D19-1c: presentation of the store details of a game card
 * (`GET /v1/store/{appid}/details`): short description, mode chips, genres,
 * developer, release date and the OVERALL review rating.
 *
 * Every string comes from a third party. The server already strips markup and
 * caps lengths; this module only ever returns plain strings, and the sheet
 * puts them in the DOM as text nodes only (`textContent`), never as markup. Missing fields
 * are omitted, never shown as placeholders. There is deliberately no "recent
 * reviews" figure: Steam's summary does not expose one (option A).
 *
 * Pure: no DOM, no fetch. Covered in web/tests/store-details.test.js.
 */

/** Server `state` values (api/vault_api/store_details.py). */
export const STORE_STATE = Object.freeze({
  OFF: "off",
  PENDING: "pending",
  READY: "ready",
  UNAVAILABLE: "unavailable",
});

/** Same pacing as the depot preview: four tries, then the card stays plain. */
export const STORE_RETRY_MS = Object.freeze([1500, 3000, 6000, 12000]);

/** Server mode flag -> chip label. Unknown flags are dropped. */
export const MODE_LABELS = Object.freeze({
  single_player: "Single-player",
  multiplayer: "Multiplayer",
  co_op: "Co-op",
  vr: "VR",
});

/** Hard client-side cap, independent of the server's (defence in depth). */
const MAX_DESCRIPTION = 600;

/** @param {unknown} v */
function text(v) {
  return typeof v === "string" && v.trim() !== "" ? v.trim() : null;
}

/** @param {unknown} payload */
export function isReady(payload) {
  return !!payload && typeof payload === "object" && payload.state === STORE_STATE.READY;
}

/** @param {unknown} payload @returns {string | null} */
export function descriptionText(payload) {
  if (!isReady(payload)) return null;
  const t = text(payload.description);
  if (!t) return null;
  return t.length > MAX_DESCRIPTION ? `${t.slice(0, MAX_DESCRIPTION - 1)}…` : t;
}

/** @param {unknown} payload @returns {string[]} */
export function modeChips(payload) {
  if (!isReady(payload) || !Array.isArray(payload.modes)) return [];
  return payload.modes.filter((m) => Object.hasOwn(MODE_LABELS, m)).map((m) => MODE_LABELS[m]);
}

/** @param {unknown} payload @returns {string[]} */
export function genreList(payload) {
  if (!isReady(payload) || !Array.isArray(payload.genres)) return [];
  return payload.genres.map(text).filter((g) => g !== null);
}

/**
 * "Some Studio - 3 Mar, 2023", either half alone, or null.
 * @param {unknown} payload @returns {string | null}
 */
export function byLine(payload) {
  if (!isReady(payload)) return null;
  const devs = Array.isArray(payload.developers)
    ? payload.developers.map(text).filter((d) => d !== null).join(", ")
    : "";
  const parts = [devs || null, text(payload.release_date)].filter((p) => p !== null);
  return parts.length ? parts.join(" – ") : null;
}

/**
 * Overall rating, e.g. "Overwhelmingly Positive (97% of 1,000 reviews)".
 * Null without usable counts.
 * @param {unknown} payload @returns {string | null}
 */
export function ratingText(payload) {
  if (!isReady(payload)) return null;
  const pos = payload.review_positive;
  const neg = payload.review_negative;
  if (!Number.isInteger(pos) || !Number.isInteger(neg) || pos < 0 || neg < 0 || pos + neg === 0) {
    return null;
  }
  const total = pos + neg;
  const percent = Math.round((pos / total) * 100);
  const count = `${percent}% of ${total.toLocaleString("en-US")} reviews`;
  const label = text(payload.review_summary);
  return label ? `${label} (${count})` : count;
}

/** Does the payload hold anything worth a block? @param {unknown} payload */
export function hasStoreContent(payload) {
  return !!(
    descriptionText(payload) ||
    modeChips(payload).length ||
    genreList(payload).length ||
    byLine(payload) ||
    ratingText(payload)
  );
}
