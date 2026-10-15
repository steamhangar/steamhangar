/**
 * WP D19-1b: presentation of the EXPECTED depots and size of a game that was
 * never downloaded (`GET /v1/oracle/{appid}/preview`, WP D19-1a).
 *
 * The numbers are the manifest oracle's statement about the public branch, an
 * expectation and never an observation: every string produced here says
 * "expected", and the sheet switches to the observed values by itself once the
 * first download maps depots (the preview is only shown while the game has no
 * observed depots).
 *
 * Pure: no DOM, no fetch. Covered in web/tests/depot-preview.test.js.
 */

import { formatBytesGB } from "./format.js";

/** Server `state` values (api/vault_api/depot_preview.py). */
export const PREVIEW_STATE = Object.freeze({
  OFF: "off",
  CACHED: "cached",
  PENDING: "pending",
  READY: "ready",
  UNAVAILABLE: "unavailable",
});

/** Delays (ms) before asking again while the server says `pending`. Four
 * tries, then the card simply stays "unknown": a slow oracle must not turn
 * the open card into a polling loop. */
export const PENDING_RETRY_MS = Object.freeze([1500, 3000, 6000, 12000]);

/**
 * Is this payload something the card can show?
 * @param {unknown} preview
 */
export function isReady(preview) {
  return (
    !!preview &&
    typeof preview === "object" &&
    preview.state === PREVIEW_STATE.READY &&
    Array.isArray(preview.depots) &&
    preview.depots.length > 0
  );
}

/**
 * Wording for the size line of the fact block, or `null` when there is
 * nothing to say. Never claims the figure is on disk.
 * @param {unknown} preview
 * @returns {string | null}
 */
export function expectedSizeText(preview) {
  if (!isReady(preview)) return null;
  const size = formatBytesGB(preview.total_size_bytes);
  if (!size) return null;
  const download = formatBytesGB(preview.total_download_bytes);
  return download ? `Expected size ${size} (download ${download})` : `Expected size ${size}`;
}

/**
 * Rows of the "Expected depots" list. Depots without a size keep a row with
 * `sizeText: null` (shown as a dash by the caller, never as 0).
 * @param {unknown} preview
 * @returns {{depotid: number, sizeText: string | null}[]}
 */
export function expectedDepotRows(preview) {
  if (!isReady(preview)) return [];
  return preview.depots.map((d) => ({
    depotid: d.depotid,
    sizeText: formatBytesGB(d.size_bytes),
  }));
}

/**
 * DLC depots (no public manifest) are listed apart, never in the size.
 * @param {unknown} preview
 * @returns {{depotid: number, dlcappid: number | null}[]}
 */
export function expectedDlcDepotRows(preview) {
  if (!isReady(preview) || !Array.isArray(preview.dlc_depots)) return [];
  return preview.dlc_depots.map((d) => ({
    depotid: d.depotid,
    dlcappid: typeof d.dlcappid === "number" ? d.dlcappid : null,
  }));
}

/**
 * Should the card ask for a preview at all? Only for a game with no observed
 * depots; the server decides the rest (switches, TTL).
 * @param {{depots?: unknown[]} | null | undefined} detail `GET /v1/games/{appid}` or null when untracked
 */
export function wantsPreview(detail) {
  return !detail || !Array.isArray(detail.depots) || detail.depots.length === 0;
}
