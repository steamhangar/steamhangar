/**
 * Library metadata filters (WP D23 LIBRARY-META-1c).
 *
 * Pure: no DOM, no storage. Filters by category, genre, minimum rating and
 * release-year range over the `meta` object of `GET /v1/games` (stored
 * library metadata; null = unknown). The rating/year SORT lives in
 * lib/library-sort.js so there is one sort control.
 *
 * **Why client-side.** The Library loads the whole list once (the store
 * polls `GET /v1/games` without parameters) and merges, groups and sorts it
 * locally; every row already carries its `meta`. The semantics below are
 * therefore the ones of the API's `category`/`genre`/`min_rating`/
 * `year_from`/`year_to`/`include_unknown` parameters (api/vault_api/
 * library_meta.py `matching_appids`), applied to the same data, and the
 * "N games hidden: metadata unknown" count equals what the API reports in
 * `X-Meta-Unknown-Excluded`: games that fail ONLY because a value is unknown.
 *
 *   - categories are AND-ed, genres are any-of, all clauses are AND-ed;
 *   - a value that is known and fails a clause always excludes the game;
 *   - an unknown value (no meta, or the field is null / no genres) excludes
 *     it unless `includeUnknown` is on.
 *
 * The whole panel is hidden while no row carries `meta` (feature off or
 * nothing fetched yet), see `metaAvailable` in library-sort.js.
 */

export const CATEGORY_OPTIONS = Object.freeze([
  { key: "single", label: "Single-player", field: "single_player" },
  { key: "multi", label: "Multiplayer", field: "multiplayer" },
  { key: "coop", label: "Co-op", field: "coop" },
  { key: "vr", label: "VR", field: "vr" },
]);

export const MIN_YEAR = 1970;
export const MAX_YEAR = 2100;

/** Rating thresholds offered by the control (percent positive reviews). */
export const RATING_STEPS = Object.freeze([95, 90, 80, 70, 50]);

export const FILTER_LABELS = Object.freeze({
  category: "Category",
  genre: "Genre",
  rating: "Min rating",
  yearFrom: "From year",
  yearTo: "To year",
  includeUnknown: "Include unknown",
  clear: "Clear filters",
  anyRating: "Any rating",
  anyGenre: "Any genre",
});

/** Steam genre ids -> labels; unknown ids read "Genre <id>". */
const GENRE_LABELS = Object.freeze({
  1: "Action",
  2: "Strategy",
  3: "RPG",
  4: "Casual",
  9: "Racing",
  18: "Sports",
  23: "Indie",
  25: "Adventure",
  28: "Simulation",
  29: "Massively Multiplayer",
  37: "Free to Play",
  70: "Early Access",
  81: "Documentary",
  82: "Episodic",
  84: "Short",
  85: "Tutorial",
});

export const DEFAULT_META_FILTER = Object.freeze({
  categories: [],
  genres: [],
  minRating: null,
  yearFrom: null,
  yearTo: null,
  includeUnknown: false,
});

function intIn(v, lo, hi) {
  const n = typeof v === "string" && v.trim() !== "" ? Number(v) : v;
  return Number.isInteger(n) && n >= lo && n <= hi ? n : null;
}

/**
 * A stored/untrusted filter made safe: unknown categories, bad genre ids,
 * out-of-range numbers and non-booleans are dropped; swapped years are
 * put in order (the API would answer 422 for from > to).
 * @returns {{categories: string[], genres: number[], minRating: number|null,
 *   yearFrom: number|null, yearTo: number|null, includeUnknown: boolean}}
 */
export function normalizeMetaFilter(raw) {
  const src = raw && typeof raw === "object" ? raw : {};
  const categories = CATEGORY_OPTIONS.map((o) => o.key).filter(
    (k) => Array.isArray(src.categories) && src.categories.includes(k),
  );
  const genres = [];
  if (Array.isArray(src.genres)) {
    for (const g of src.genres) {
      const id = intIn(g, 1, 100000);
      if (id !== null && !genres.includes(id)) genres.push(id);
    }
  }
  genres.sort((a, b) => a - b);
  const minRating = intIn(src.minRating, 1, 100);
  let yearFrom = intIn(src.yearFrom, MIN_YEAR, MAX_YEAR);
  let yearTo = intIn(src.yearTo, MIN_YEAR, MAX_YEAR);
  if (yearFrom !== null && yearTo !== null && yearFrom > yearTo) [yearFrom, yearTo] = [yearTo, yearFrom];
  return { categories, genres, minRating, yearFrom, yearTo, includeUnknown: src.includeUnknown === true };
}

export function metaFilterActive(f) {
  return (
    f.categories.length > 0 ||
    f.genres.length > 0 ||
    f.minRating !== null ||
    f.yearFrom !== null ||
    f.yearTo !== null
  );
}

/**
 * How many filter groups are in force (categories, genre, rating, year range
 * each count once) — the badge on the Library's Filters button. The
 * include-unknown switch is a modifier, not a filter, and is not counted.
 */
export function activeFilterCount(f) {
  return (
    (f.categories.length > 0 ? 1 : 0) +
    (f.genres.length > 0 ? 1 : 0) +
    (f.minRating !== null ? 1 : 0) +
    (f.yearFrom !== null || f.yearTo !== null ? 1 : 0)
  );
}

/** `true` / `false` when the clause is decidable, `null` when unknown. */
function clauses(game, f) {
  const m = game?.meta && typeof game.meta === "object" ? game.meta : null;
  const out = [];
  for (const key of f.categories) {
    const field = CATEGORY_OPTIONS.find((o) => o.key === key).field;
    out.push(typeof m?.[field] === "boolean" ? m[field] : null);
  }
  if (f.minRating !== null) {
    out.push(Number.isFinite(m?.review_percent) ? m.review_percent >= f.minRating : null);
  }
  const year = Number.isFinite(m?.release_year) ? m.release_year : null;
  if (f.yearFrom !== null) out.push(year === null ? null : year >= f.yearFrom);
  if (f.yearTo !== null) out.push(year === null ? null : year <= f.yearTo);
  if (f.genres.length > 0) {
    const have = Array.isArray(m?.genres) ? m.genres : [];
    out.push(have.length === 0 ? null : have.some((g) => f.genres.includes(g)));
  }
  return out;
}

/** Whether `game` passes the filter (unknown values per `includeUnknown`). */
export function matchesMeta(game, f) {
  if (!metaFilterActive(f)) return true;
  return clauses(game, f).every((c) => (c === null ? f.includeUnknown : c));
}

/**
 * Apply the filter. `unknownExcluded` is the number of games the filter left
 * out ONLY because some value is unknown (0 while `includeUnknown` is on or
 * no filter is active) - the web twin of `X-Meta-Unknown-Excluded`.
 * @returns {{games: object[], unknownExcluded: number}}
 */
export function applyMetaFilter(games, f) {
  if (!metaFilterActive(f)) return { games, unknownExcluded: 0 };
  const kept = [];
  let unknownExcluded = 0;
  for (const g of games) {
    const cs = clauses(g, f);
    if (cs.every((c) => (c === null ? f.includeUnknown : c))) kept.push(g);
    else if (!f.includeUnknown && cs.every((c) => c !== false)) unknownExcluded += 1;
  }
  return { games: kept, unknownExcluded };
}

export function genreLabel(id) {
  return GENRE_LABELS[id] || `Genre ${id}`;
}

/** Genres present in the data, labelled and sorted by label. */
export function availableGenres(rows) {
  const ids = new Set();
  for (const g of Array.isArray(rows) ? rows : []) {
    if (Array.isArray(g?.meta?.genres)) for (const id of g.meta.genres) if (Number.isInteger(id)) ids.add(id);
  }
  return [...ids]
    .map((id) => ({ id, label: genreLabel(id) }))
    .sort((a, b) => a.label.localeCompare(b.label, undefined, { sensitivity: "base" }) || a.id - b.id);
}

/** "N games hidden: metadata unknown", or "" when nothing is hidden. */
export function unknownNote(n) {
  if (!Number.isInteger(n) || n <= 0) return "";
  return `${n} ${n === 1 ? "game" : "games"} hidden: metadata unknown`;
}
