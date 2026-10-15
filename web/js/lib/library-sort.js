/**
 * Library sort (WP LIB-SORT, rc19; decision Jan 2026-10-15).
 *
 * Pure: no DOM, no storage. Options: Name (A-Z / Z-A through the direction
 * toggle), Size, State, "Last cached or updated" and, ONLY while the
 * operator's privacy setting `relay_expose_last_played` is on, "Last played".
 * "Newly bought" is dropped (Steam has no purchase date).
 *
 * **Data sources (no schema added).**
 *   - size: the row's `group_size_bytes` (a group card) else `size_bytes`.
 *   - state: the card's display kind (`dispKind`), ranked in-progress,
 *     cached, installed-but-not-cached, not cached, other.
 *   - last cached or updated: `last_prefill_at` of `GET /v1/games` (written
 *     by a successful prefill run; the newest of a group's members).
 *   - last played: `owned_last_played`, copied from the owned list's
 *     `rtime_last_played` (lib/owned-library.js `withOwnedLastPlayed`). The
 *     relay OMITS that key while the flag is off, so "the flag is on" is
 *     exactly "some owned row carries it" ({@link lastPlayedAvailable}).
 *
 * **Ordering rules.** Stable: ties fall back to the name, then the appid.
 * Rows without a value for the chosen key (no size, never cached, never
 * played) and name-less cards (ID fallbacks "App 123") sort LAST in both
 * directions; the direction toggle only flips the rows that have a value.
 */

import { dispKind, KIND, installedBadgeState, INSTALLED_BADGE } from "./game-status.js";

export const SORT_KEY = Object.freeze({
  NAME: "name",
  SIZE: "size",
  STATE: "state",
  UPDATED: "updated",
  PLAYED: "played",
});
export const SORT_DIR = Object.freeze({ ASC: "asc", DESC: "desc" });

export const DEFAULT_SORT = Object.freeze({ key: SORT_KEY.NAME, dir: SORT_DIR.ASC });

/** Labels shared word for word with the Android app (LibrarySort.kt). */
export const SORT_OPTIONS = Object.freeze([
  { key: SORT_KEY.NAME, label: "Name" },
  { key: SORT_KEY.SIZE, label: "Size" },
  { key: SORT_KEY.STATE, label: "State" },
  { key: SORT_KEY.UPDATED, label: "Last cached or updated" },
  { key: SORT_KEY.PLAYED, label: "Last played", needsLastPlayed: true },
]);
export const SORT_LABEL = "Sort";
export const SORT_DIR_LABEL = Object.freeze({ asc: "Ascending", desc: "Descending" });
/** The Name option reads as an alphabet direction. */
export const SORT_NAME_DIR_LABEL = Object.freeze({ asc: "A-Z", desc: "Z-A" });

/** @param {object[]} rows library rows */
export function lastPlayedAvailable(rows) {
  return Array.isArray(rows) && rows.some((g) => g && Number.isFinite(g.owned_last_played));
}

/**
 * A stored/untrusted choice made safe: unknown key or direction falls back
 * to the default; "played" while the flag is off falls back to Name A-Z.
 * @returns {{key: string, dir: string}}
 */
export function normalizeSort(choice, { lastPlayed }) {
  const key = SORT_OPTIONS.some((o) => o.key === choice?.key) ? choice.key : DEFAULT_SORT.key;
  const dir = choice?.dir === SORT_DIR.DESC ? SORT_DIR.DESC : SORT_DIR.ASC;
  if (key === SORT_KEY.PLAYED && !lastPlayed) return { ...DEFAULT_SORT };
  return { key, dir };
}

/** Options the control offers right now. */
export function availableSortOptions({ lastPlayed }) {
  return SORT_OPTIONS.filter((o) => !o.needsLastPlayed || lastPlayed);
}

function realName(g) {
  return typeof g?.name === "string" && g.name.trim() !== "" ? g.name : null;
}

function memberRows(g) {
  return Array.isArray(g.packages) ? [g, ...g.packages] : [g];
}

function stateRank(g, liveJob) {
  const kind = dispKind(g, liveJob);
  if (kind === KIND.RUNNING || kind === KIND.UPDATING || kind === KIND.VERIFY || kind === KIND.PAUSED) return 0;
  if (kind === KIND.CACHED) return 1;
  if (installedBadgeState(g) === INSTALLED_BADGE.NOT_CACHED) return 2;
  if (kind === KIND.NONE) return 3;
  return 4;
}

/** The numeric sort value of a row, or null when it has none. */
function valueOf(g, key, liveJob) {
  switch (key) {
    case SORT_KEY.SIZE: {
      const v = Number.isFinite(g.group_size_bytes) ? g.group_size_bytes : g.size_bytes;
      return Number.isFinite(v) ? v : null;
    }
    case SORT_KEY.STATE:
      return stateRank(g, liveJob);
    case SORT_KEY.UPDATED: {
      let best = null;
      for (const m of memberRows(g)) {
        const t = typeof m.last_prefill_at === "string" ? Date.parse(m.last_prefill_at) : NaN;
        if (Number.isFinite(t) && (best === null || t > best)) best = t;
      }
      return best;
    }
    case SORT_KEY.PLAYED:
      // 0 is Steam's "never played": no value, not the oldest date.
      return Number.isFinite(g.owned_last_played) && g.owned_last_played > 0 ? g.owned_last_played : null;
    default:
      return null;
  }
}

function compareNames(a, b) {
  return a.localeCompare(b, undefined, { sensitivity: "base", numeric: true });
}

/** Name tie-break, A-Z, name-less last; 0 when equal. */
function byNameAsc(x, y) {
  if (x.name === null || y.name === null) {
    return x.name === y.name ? 0 : x.name === null ? 1 : -1;
  }
  return compareNames(x.name, y.name);
}

/**
 * @param {object[]} games
 * @param {{key: string, dir: string}} choice already normalized
 * @param {Map<number, object>} [liveJobsByAppid]
 * @returns {object[]} a new array
 */
export function sortGames(games, choice, liveJobsByAppid) {
  const sign = choice.dir === SORT_DIR.DESC ? -1 : 1;
  const byName = choice.key === SORT_KEY.NAME;
  const rows = games.map((g) => ({
    g,
    name: realName(g),
    value: byName ? null : valueOf(g, choice.key, liveJobsByAppid?.get(g.appid)),
  }));
  rows.sort((x, y) => {
    if (byName) {
      // Name-less last regardless of direction; real names flip with it.
      if (x.name === null || y.name === null) {
        if (x.name !== y.name) return x.name === null ? 1 : -1;
      } else {
        const c = compareNames(x.name, y.name);
        if (c !== 0) return sign * c;
      }
    } else {
      if (x.value === null || y.value === null) {
        if (x.value !== y.value) return x.value === null ? 1 : -1;
      } else if (x.value !== y.value) {
        return sign * (x.value < y.value ? -1 : 1);
      }
      const c = byNameAsc(x, y);
      if (c !== 0) return c;
    }
    return x.g.appid - y.g.appid;
  });
  return rows.map((r) => r.g);
}
