package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.ui.status.StatusKind

/**
 * Library search + filter-chip logic (WP 4b.4) — Kotlin port of
 * `web/js/lib/library-filters.js`'s `matchQ`/`FILTERS`/chip-count logic
 * (docs/design/vault-app-mockup.html, docs/design/vault-app-mockup-NOTES.md
 * "Search"): search and chips are ANDed, chip counts always describe what
 * the grid would show right now, case-insensitive substring match on the
 * title. Pure — no Compose, no network — so the AND semantics and the
 * counts are directly unit-testable (`LibraryFiltersTest`).
 *
 * Chip set matches the web port's recorded divergence (docs/WORKPACKAGES.md
 * Phase 4a header, "Failed-replaces-Update-ready"), for the identical
 * reason: `GameSummary` has no stale/oracle field yet (`GameStatus.kt`'s
 * kdoc, Divergence 1), while a persistent per-app `error` status is real,
 * currently-shipping surface the mockup never modeled. This is the SAME
 * decision already made and recorded for the web frontend, applied here
 * for consistency between the two clients rather than re-litigated per
 * platform — chips ship All / Cached / Not cached / Downloading / Failed.
 * WP UPD-3 (twin of web UPD-2, D14) appends "Updates": games whose
 * `update_available` flag (WP UPD-1) is set. It is hidden on a vault from
 * before UPD-1 ([hiddenChipKeys]), where the flag never exists.
 *
 * **String-resource rule, exception recorded (WP 4b.4 review fix; see
 * app/README.md's "String resources" conventions section, and
 * `BulkPlan.kt`'s kdoc for the same exception applied to that file).**
 * [FILTER_DEFS]'s `label` values are a verbatim, word-for-word port of
 * `web/js/lib/library-filters.js`'s own `FILTER_DEFS` labels and are kept
 * as Kotlin literals rather than `strings.xml` entries, on purpose, so the
 * chip-set contract can be diffed against the web source directly.
 * `LibraryFiltersTest`'s `chip set is exactly...` case pins the KEYS; the
 * LABELS are pinned by literal string-equality in
 * `chip set labels match the web port's FILTER_DEFS labels verbatim`
 * below — this is what qualifies them for the literal exception rather than
 * the general "static UI chrome belongs in resources" rule.
 */

/*
 * **WP TOOL-APPS-2d: chips count groups, not packages** (twin of web
 * TOOL-APPS-2c). The Library hands in GROUPED rows ([groupGames]) plus each
 * group's packages ([visibleGames]/[chipCounts]'s `packagesByParent`): a
 * group card is one row, so every chip counts it once. "Failed" also matches
 * a group whose parent is fine but a content package failed (user decision
 * Q3 B variant); every other chip reads the parent's own row. A search
 * matches a group when the parent or any of its packages matches, so typing
 * a package name finds its game. Without `packagesByParent` (an older
 * server, or no groups) everything behaves exactly as before.
 */

/** One filter chip: a stable key, its label, and a predicate over
 * `(game, liveJob)` where `liveJob` is this game's entry from
 * [indexLiveJobsByAppid] (`null` if none). */
data class FilterDef(
    val key: String,
    val label: String,
    val predicate: (GameSummary, JobSummary?) -> Boolean,
)

val FILTER_DEFS: List<FilterDef> = listOf(
    FilterDef("all", "All") { _, _ -> true },
    FilterDef("cached", "Cached") { g, job -> dispKind(g, job) == StatusKind.CACHED },
    FilterDef("none", "Not cached") { g, job -> dispKind(g, job) == StatusKind.NONE },
    // findLiveJob/indexLiveJobsByAppid already exclude queued/GC jobs (GameStatus.kt).
    FilterDef("downloading", "Downloading") { _, job -> job != null },
    FilterDef("failed", "Failed") { g, job -> dispKind(g, job) == StatusKind.ERROR },
    // WP UPD-3: the server sets the flag only for a game with cache bytes.
    FilterDef(UPDATES_FILTER_KEY, "Updates") { g, _ -> g.update_available },
)

/** Key of the "Updates" chip (WP UPD-3). */
const val UPDATES_FILTER_KEY = "updates"

/** Chips not to show: "Updates" on a vault that has no update check
 * (`404` from `/v1/updates/...`, a server from before UPD-1). */
fun hiddenChipKeys(updatesSupported: Boolean): Set<String> =
    if (updatesSupported) emptySet() else setOf(UPDATES_FILTER_KEY)

private val FILTER_BY_KEY = FILTER_DEFS.associateBy { it.key }

fun filterByKey(key: String): FilterDef = FILTER_BY_KEY[key] ?: FILTER_BY_KEY.getValue("all")

fun normalizeQuery(rawQuery: String?): String = (rawQuery ?: "").trim().lowercase()

/** @param query already-lowercased, already-trimmed ([normalizeQuery]). */
fun matchesQuery(game: GameSummary, query: String): Boolean {
    if (query.isEmpty()) return true
    return game.name?.lowercase()?.contains(query) == true
}

/** WP TOOL-APPS-2d: [matchesQuery] for a card -- the row, or one of its packages. */
fun matchesQueryWithPackages(game: GameSummary, query: String, packages: List<GameSummary>): Boolean =
    matchesQuery(game, query) || packages.any { matchesQuery(it, query) }

/** WP TOOL-APPS-2d: a chip's predicate for a card -- "Failed" also matches a
 * group with a failed package (see the note above [FilterDef]). */
private fun matchesFilter(
    filter: FilterDef,
    game: GameSummary,
    liveJobsByAppid: Map<Int, JobSummary>,
    packages: List<GameSummary>,
): Boolean {
    if (filter.predicate(game, liveJobsByAppid[game.appid])) return true
    return filter.key == FAILED_FILTER_KEY && failedPackages(packages, liveJobsByAppid).isNotEmpty()
}

/** Key of the "Failed" chip. */
const val FAILED_FILTER_KEY = "failed"

/** @return games matching BOTH the query and the active filter.
 * @param packagesByParent WP TOOL-APPS-2d: each group card's packages
 *   ([GroupedLibrary.packagesByParent]); empty = no groups. */
fun visibleGames(
    games: List<GameSummary>,
    query: String,
    filterKey: String,
    liveJobsByAppid: Map<Int, JobSummary>,
    packagesByParent: Map<Int, List<GameSummary>> = emptyMap(),
): List<GameSummary> {
    val filter = filterByKey(filterKey)
    return games.filter { g ->
        val packages = packagesByParent[g.appid].orEmpty()
        matchesQueryWithPackages(g, query, packages) && matchesFilter(filter, g, liveJobsByAppid, packages)
    }
}

data class ChipCount(val key: String, val label: String, val count: Int)

/**
 * Counts for every chip not in [hiddenKeys], computed against the CURRENT query (mockup rule:
 * "counts recompute against the current query, so a chip's number is
 * always something the grid can actually produce").
 */
fun chipCounts(
    games: List<GameSummary>,
    query: String,
    liveJobsByAppid: Map<Int, JobSummary>,
    hiddenKeys: Set<String> = emptySet(),
    packagesByParent: Map<Int, List<GameSummary>> = emptyMap(),
): List<ChipCount> {
    val inQuery = games.filter { matchesQueryWithPackages(it, query, packagesByParent[it.appid].orEmpty()) }
    return FILTER_DEFS.filter { it.key !in hiddenKeys }.map { f ->
        ChipCount(
            f.key,
            f.label,
            inQuery.count { g -> matchesFilter(f, g, liveJobsByAppid, packagesByParent[g.appid].orEmpty()) },
        )
    }
}
