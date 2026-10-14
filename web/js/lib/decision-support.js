/**
 * Per-game cache facts that used to be the Phase 4h suggestions panel
 * (WP 4h.2). WP WEB-FEAT-SUGGEST-2 (user decision 2026-10-13) removed the
 * panel and moved its statements to where they are read:
 *
 *   - "last changed N days ago" -> the game detail sheet,
 *     {@link lastUpdateDownloadedText} ("Last update downloaded N days ago");
 *   - "fully cached, never played" -> a second pill on the library card,
 *     {@link isNeverPlayedCached};
 *   - "not confirmed current for N days" -> one notification in the bell
 *     list, {@link staleUpdateChecks} (14 days, was 30 in the panel);
 *   - "unchanged for the N days we've watched it" -> dropped.
 *
 * The honesty rules of the panel stay (docs/PROJECT_PLAN.md Phase 4h):
 *
 * **Privacy gate (ADR-0010, binding).** `playtime_forever` reaches the web
 * app only when the operator turned on `relay_expose_playtime`; it ships
 * OFF. This module never fetches it; the Library hands in the owned list's
 * value as `owned_playtime_forever`. `playtime_forever === 0` is a real,
 * printable claim ("never played"); absent, null, negative or non-finite is
 * UNKNOWN and never becomes a fabricated zero. No code path here names a
 * non-zero playtime, a last-played date or an inactivity span, and
 * `rtime_last_played` is never read.
 *
 * **Day counts** from the server (`manifest_days_since_last_change`) must
 * be real, whole, non-negative numbers, or nothing is said (NaN, -5, 3.7,
 * Infinity and 1e21 were all probed rendering in WP 4h.2 before the guard).
 *
 * Pure: no DOM, no fetch, no hidden clock (every "now" is a parameter).
 * Tests: web/tests/decision-support.test.js.
 */

const MS_PER_DAY = 86_400_000;

/** A cached game whose last update check is at least this old is named in
 * the "update check due" notification (user decision 2026-10-13: 14 days,
 * the panel used 30). */
export const UPDATE_CHECK_DUE_DAYS = 14;

export const NEVER_PLAYED_LABEL = "Never played";
/** The pill's short word where the card is narrow (Compact grid). */
export const NEVER_PLAYED_COMPACT_LABEL = "Unplayed";
/** The pill's full meaning: tooltip and the card's accessible name. */
export const NEVER_PLAYED_DESCRIPTION = "Fully cached, never played";

/**
 * Whole days between `iso` and `nowMs`, or `null` if `iso` cannot be
 * parsed or lies in the future (clock skew is not "N days ago"). Floors:
 * "20 days" means at least 20 full days.
 * @param {string} iso
 * @param {number} nowMs
 * @returns {number | null}
 */
export function daysSince(iso, nowMs) {
  if (typeof iso !== "string" || !iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  const deltaMs = nowMs - t;
  if (deltaMs < 0) return null;
  return Math.floor(deltaMs / MS_PER_DAY);
}

/**
 * A real playtime-forever value, or `null` for "unknown" — never a
 * fabricated zero (mirrors `steam_relay.py`'s `_coerce_nonneg_int`).
 * @param {unknown} value
 * @returns {number | null}
 */
function realPlaytimeForeverOrNull(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

/**
 * A real, whole, non-negative day count, or `null`.
 * @param {unknown} value
 * @returns {number | null}
 */
function realNonNegativeIntOrNull(value) {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
}

/**
 * True when the card may say "Never played": the game has bytes in the
 * cache AND the owned list reported a real, explicit zero playtime for it.
 * The caller additionally requires the card's own status to be "cached"
 * (a running or failed download is not "fully cached").
 * @param {{size_bytes?: number|null, owned_playtime_forever?: unknown, tool_app?: boolean}} game
 * @returns {boolean}
 */
export function isNeverPlayedCached(game) {
  if (!game || game.tool_app === true) return false;
  const cached = typeof game.size_bytes === "number" && game.size_bytes > 0;
  return cached && realPlaytimeForeverOrNull(game.owned_playtime_forever) === 0;
}

/**
 * The detail sheet's "Last update downloaded …" line, or `null` when there is
 * no real observation to report. Only `manifest_change_frequency ===
 * "changed"` carries a meaningful `manifest_days_since_last_change` (WP
 * 4h.1: "stable", "insufficient_data" and null say nothing about a change).
 * The day count is when the VAULT's own download recorded a new manifest
 * (`manifest_ingest` during a prefill), not Steam's publish date — hence
 * "downloaded", never "updated on Steam" (review fix). The vault keeps only
 * the latest manifest per depot, so this is one past fact, never a cadence
 * ("updates every N days").
 * @param {{manifest_change_frequency?: string|null, manifest_days_since_last_change?: unknown}} game
 * @returns {string | null}
 */
export function lastUpdateDownloadedText(game) {
  if (!game || game.manifest_change_frequency !== "changed") return null;
  const n = realNonNegativeIntOrNull(game.manifest_days_since_last_change);
  if (n === null) return null;
  if (n === 0) return "Last update downloaded today";
  if (n === 1) return "Last update downloaded yesterday";
  return `Last update downloaded ${n} days ago`;
}

/**
 * The newest of the two times something checked this game for updates:
 * `update_checked_at` (the manifest oracle answered, WP UPD-1) and
 * `last_manifest_check` (a download run confirmed it current). `null` when
 * neither ever happened.
 * @param {{update_checked_at?: string|null, last_manifest_check?: string|null}} game
 * @returns {string | null}
 */
export function lastUpdateCheckAt(game) {
  let best = null;
  let bestMs = -Infinity;
  for (const iso of [game?.update_checked_at, game?.last_manifest_check]) {
    if (typeof iso !== "string" || !iso) continue;
    const t = Date.parse(iso);
    if (Number.isNaN(t) || t <= bestMs) continue;
    best = iso;
    bestMs = t;
  }
  return best;
}

/**
 * Cached games whose last update check is {@link UPDATE_CHECK_DUE_DAYS} or
 * more days old, oldest first (ties by appid). A game never checked at all
 * is left out: "never" is not a day count, and a fresh vault would
 * otherwise name every game at once.
 * @param {object[] | null | undefined} games `GET /v1/games` rows
 * @param {{nowMs?: number}} [opts]
 * @returns {Array<{appid: number, days: number}>}
 */
export function staleUpdateChecks(games, { nowMs = Date.now() } = {}) {
  const list = Array.isArray(games) ? games : [];
  const out = [];
  for (const g of list) {
    if (!g || typeof g.appid !== "number") continue;
    if (g.tool_app === true || g.owned_only === true) continue;
    if (!(typeof g.size_bytes === "number" && g.size_bytes > 0)) continue;
    const days = daysSince(lastUpdateCheckAt(g), nowMs);
    if (days === null || days < UPDATE_CHECK_DUE_DAYS) continue;
    out.push({ appid: g.appid, days });
  }
  out.sort((a, b) => b.days - a.days || a.appid - b.appid);
  return out;
}

/** At most this many game names are spelled out in the notification. */
export const UPDATE_CHECK_DUE_MAX_NAMES = 3;

/** How the notification may point at the Library's "Check updates"
 * button (lib/update-check.js's phase, read by the bell at render time). */
export const UPDATE_CHECK_HINT = Object.freeze({
  /** The button exists and works: point at it. */
  AVAILABLE: "available",
  /** The manifest oracle is off: there is nothing to press. */
  DISABLED: "disabled",
  /** Unknown yet, or the vault has no update check: say nothing extra. */
  NONE: "none",
});

const HINT_SUFFIX = Object.freeze({
  [UPDATE_CHECK_HINT.AVAILABLE]: ' Use "Check updates" in the Library to check again.',
  [UPDATE_CHECK_HINT.DISABLED]: " Update checks are turned off on this vault.",
  [UPDATE_CHECK_HINT.NONE]: "",
});

/**
 * Title and text of the "update check due" notification.
 * @param {Array<{appid: number, days: number}>} items from {@link staleUpdateChecks}
 * @param {(appid: number) => string} nameFor
 * @param {string} [hint] one of {@link UPDATE_CHECK_HINT}; only AVAILABLE
 *   points at the "Check updates" button (no target otherwise).
 * @returns {{title: string, text: string}}
 */
export function updateCheckDueWording(items, nameFor, hint = UPDATE_CHECK_HINT.NONE) {
  const suffix = HINT_SUFFIX[hint] ?? "";
  if (items.length === 1) {
    const { appid, days } = items[0];
    return { title: nameFor(appid), text: `Last checked for updates ${days} days ago.${suffix}` };
  }
  const names = items.slice(0, UPDATE_CHECK_DUE_MAX_NAMES).map((i) => nameFor(i.appid));
  const more = items.length - names.length;
  const listed = more > 0 ? `${names.join(", ")} and ${more} more` : `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`;
  return {
    title: `${items.length} games`,
    text: `Not checked for updates in ${UPDATE_CHECK_DUE_DAYS} days or more: ${listed}.${suffix}`,
  };
}
