/**
 * Library search + filter-chip logic (WP 4a.3).
 *
 * Ports the mockup's `matchQ`/`FILTERS`/`renderChips` (docs/design/
 * vault-app-mockup.html, docs/design/vault-app-mockup-NOTES.md "Search"):
 * search and chips are ANDed, chip counts always describe what the grid
 * would show right now, case-insensitive substring match on the title. Pure
 * — no DOM — so the AND semantics and the counts are directly unit-testable
 * (web/tests/library-filters.test.js).
 *
 * Chip set DIFFERS from the mockup's, not merely "narrower": **`Failed`
 * REPLACES `Update ready`** (it is not simply dropped and not simply
 * added). The mockup's five chips are All/Cached/Not cached/Downloading/
 * Update ready; this WP ships All/Cached/Not cached/Downloading/Failed.
 * Two independent reasons meet here:
 *   1. There is nothing to show `Update ready` FOR — the real
 *      `GET /v1/games` has no stale/oracle field yet (game-status.js's
 *      module header, Divergence 1; api/README.md explicitly defers this
 *      to "a Phase-4 decision to make once the UI knows how it wants to
 *      render it").
 *   2. There IS something real to show instead — a persistent per-app
 *      `status: "error"` is genuine, currently-shipping API surface
 *      (apps.status stays `error` until a re-prefill succeeds,
 *      api/README.md "Per-game deletion") that the mockup never modeled at
 *      all (its own "error" only ever lived on a finished JOBS row, never
 *      on a GAMES row — see game-status.js's `statusAction` docstring).
 *      Leaving it with no chip and no filter would be a real, silent gap.
 * This is a deliberate, orchestrator-confirmed decision (see
 * docs/WORKPACKAGES.md's WP 4a.3 divergence record), not a placeholder —
 * `Update ready` returns as a SIXTH chip, not a swapped-back one, on
 * whatever WP first surfaces oracle staleness on `GET /v1/games`.
 *
 * **WP UPD-2: that sixth chip, "Updates".** `GET /v1/games` carries
 * `update_available` since WP UPD-1 (true only for a stale oracle verdict on
 * a game with cache content). The chip is labeled "Updates" (D14 wording)
 * and is OPTIONAL: `chipCounts` lists it only when the rows actually carry
 * the field (`hasUpdateField`), so an older vault-api shows the five chips
 * it always had.
 *
 * **WP TOOL-APPS-2c: chips count groups, not packages.** The Library hands
 * in GROUPED rows (lib/content-groups.js): a group card is one row, so every
 * chip counts it once. "Failed" also matches a group whose parent is fine
 * but a content package failed (user decision Q3 B variant); every other
 * chip reads the parent's own row. A search matches a group when the parent
 * or any of its packages matches, so typing a package name finds its game.
 *
 * **WP LIB-FILTER-INST-1: "Installed, not cached".** Games an agent reports
 * as installed on a PC (`installed_on` non-empty) with no bytes in the
 * cache — exactly the cards that show the orange "Installed but not cached
 * · <PC>" badge (lib/game-status.js `installedBadgeState` NOT_CACHED, so a
 * tool app never counts, like its badge). A group row counts once and
 * reads the parent's own row, like every chip but "Failed". Not optional:
 * like All/Cached/Not cached/Downloading/Failed it is always shown, with
 * "0" when nothing matches (`installed_on` is part of every GameSummary
 * since WP AG-1, so there is no older-API case to hide it for).
 */

import { dispKind, KIND, installedBadgeState, INSTALLED_BADGE } from "./game-status.js";
import { hasUpdateField } from "./update-check.js";
import { failedPackages } from "./content-groups.js";

/**
 * @param {object} game GameSummary
 * @param {string} query already-lowercased, already-trimmed
 */
export function matchesQuery(game, query) {
  if (!query) return true;
  if (typeof game.name === "string" && game.name.toLowerCase().includes(query)) return true;
  // WP TOOL-APPS-2c: a group card also matches on its packages' names.
  return Array.isArray(game.packages) && game.packages.some((p) => matchesQuery(p, query));
}

/** One filter chip: a stable key, its label, and a predicate over
 * `(game, liveJob, liveJobsByAppid)` where `liveJob` is this game's entry
 * from `indexLiveJobsByAppid` (undefined if none) and the map is the whole
 * index (a group's packages have jobs of their own). */
export const FILTER_DEFS = Object.freeze([
  { key: "all", label: "All", predicate: () => true },
  { key: "cached", label: "Cached", predicate: (g, job) => dispKind(g, job) === KIND.CACHED },
  { key: "none", label: "Not cached", predicate: (g, job) => dispKind(g, job) === KIND.NONE },
  {
    key: "installed",
    label: "Installed, not cached",
    // WP LIB-FILTER-INST-1: the same predicate as the card's warning badge.
    predicate: (g) => installedBadgeState(g) === INSTALLED_BADGE.NOT_CACHED,
  },
  {
    key: "downloading",
    label: "Downloading",
    predicate: (g, job) => !!job, // findLiveJob already excludes queued/GC (game-status.js)
  },
  {
    key: "failed",
    label: "Failed",
    // WP TOOL-APPS-2c: a group also counts when one of its packages failed.
    predicate: (g, job, live) => dispKind(g, job) === KIND.ERROR || failedPackages(g, live).length > 0,
  },
  // WP UPD-2: optional — see the module header and chipCounts.
  { key: "updates", label: "Updates", predicate: (g) => g.update_available === true, optional: true },
]);

const FILTER_BY_KEY = new Map(FILTER_DEFS.map((f) => [f.key, f]));

export function normalizeQuery(rawQuery) {
  return (rawQuery ?? "").trim().toLowerCase();
}

/**
 * @param {object[]} games
 * @param {{query: string, filterKey: string, liveJobsByAppid: Map<number, object>}} ctx
 *   `query` is expected already-normalized (normalizeQuery).
 * @returns {object[]} games matching BOTH the query and the active filter.
 */
export function visibleGames(games, { query, filterKey, liveJobsByAppid }) {
  const filter = FILTER_BY_KEY.get(filterKey) || FILTER_BY_KEY.get("all");
  return games.filter((g) => {
    if (!matchesQuery(g, query)) return false;
    return filter.predicate(g, liveJobsByAppid?.get(g.appid), liveJobsByAppid);
  });
}

/**
 * Counts for every chip, computed against the CURRENT query (mockup rule:
 * "counts recompute against the current query, so a chip's number is
 * always something the grid can actually produce").
 * @returns {{key: string, label: string, count: number}[]}
 */
export function chipCounts(games, { query, liveJobsByAppid }) {
  const inQuery = games.filter((g) => matchesQuery(g, query));
  // Optional chips depend on the WHOLE list, not the query: typing a search
  // must never make the "Updates" chip appear or vanish.
  const withOptional = hasUpdateField(games);
  return FILTER_DEFS.filter((f) => !f.optional || withOptional).map((f) => ({
    key: f.key,
    label: f.label,
    count: inQuery.filter((g) => f.predicate(g, liveJobsByAppid?.get(g.appid), liveJobsByAppid)).length,
  }));
}
