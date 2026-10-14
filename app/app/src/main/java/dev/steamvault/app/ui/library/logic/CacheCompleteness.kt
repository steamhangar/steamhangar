package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.ui.downloads.logic.formatTimestamp
import dev.steamvault.app.ui.status.StatusKind

/**
 * Cache completeness on the library card and in the detail sheet (WP
 * CACHE-COMPLETE-1c, Android twin of `web/js/lib/cache-completeness.js`).
 * Pure decision and wording logic, no Compose, no network --
 * `GameCardModel.kt`, `GameCard.kt`, `GameListRow.kt` and
 * `GameDetailSheet.kt` are the glue, `CacheCompletenessTest` the pins and
 * `CacheCompletenessWebTwinTest` the wording twin.
 *
 * **The API (api/README.md "Cache completeness", "Verify and complete").**
 * `GET /v1/games` rows carry `cache_complete_pct` (0-100, floor; 100 only
 * when every byte of the CURRENT build is on disk, or null = not known),
 * `cache_expected_bytes`/`cache_present_bytes`/`cache_complete_checked_at`
 * (null exactly when the pct is), `cache_missing_chunks` (`0` on an
 * incomplete row = short only of chunk files with the WRONG size) and, on a
 * top-level row, `group_cache_complete_pct` over the D9 group.
 *
 * **Card status (user decision 2026-10-14, "only 100 % gets the green
 * check").** Completeness is a DISPLAY overlay on a CACHED card, exactly
 * like UPD-6's "Update available": [GameCardModel.kind], the chips and
 * `statusAction` stay on [StatusKind.CACHED] (the bytes on disk are real).
 * What a CACHED card shows, first match wins:
 *   1. `update_available` -> "Update available" (UPD-6; the update run
 *      requests the full chunk list of the new build, so it completes the
 *      game too).
 *   2. pct 100 -> "Current", green check.
 *   3. pct 0-99 -> "Partially cached N%" (compact "Partial N%"), orange
 *      half-disc [StatusKind.PARTIAL].
 *   4. pct unknown -> "Cached · not verified" (compact "Not verified"),
 *      neutral question glyph [StatusKind.UNVERIFIED]. Null "means 'not
 *      known', never 'complete'" (README UI contract).
 * A group card (D9) reads `group_cache_complete_pct`, a plain card its own
 * `cache_complete_pct` ([completenessPctOf]). The compact words are used in
 * the list rows and the 3-column grid (web `.grid.list`/`.grid.cols3`).
 *
 * **Wording.** Kotlin literals, not `strings.xml` -- the recorded exception
 * `UpdatesAvailable.kt` and `BulkJobs.kt` use: every literal in
 * [CompletenessWording] is pinned against the web source.
 */
object CompletenessWording {
    const val UNVERIFIED_WORD = "Cached · not verified"
    const val UNVERIFIED_COMPACT_WORD = "Not verified"
    const val PARTIAL_PREFIX = "Partially cached "
    const val PARTIAL_COMPACT_PREFIX = "Partial "
    const val VERIFY_LABEL = "Verify"
    const val VERIFY_HINT =
        "Re-checks every chunk of the current build against the cache and downloads what is missing."
    const val DAMAGED_NOTE = "Damaged chunks — SteamHangar can't repair these yet."
    const val UNKNOWN_LINE = "Completeness unknown — not measured for the current build."
    const val COMPLETE_SUFFIX = "% complete — "
    const val ON_DISK = " on disk"
    const val OF = " of "
    const val CHECKED = " · checked "
    const val ZERO_GB = "0.0 GB"
    const val GROUP_LINE_PREFIX = "With content packages: "
    const val GROUP_LINE_SUFFIX = "% complete"
    const val DASH = " — "
}

/** The three completeness states of a CACHED card (web `COMPLETENESS`). */
enum class CompletenessState { COMPLETE, PARTIAL, UNVERIFIED }

/**
 * The overlay a CACHED card draws instead of "Current" (see the file kdoc).
 * A data class of value types so [GameCardModel] stays structurally equal
 * across poll ticks.
 * @param pct the figure for [StatusKind.PARTIAL], null for UNVERIFIED
 */
data class CompletenessOverlay(val icon: StatusKind, val pct: Int?) {
    /** "Partially cached 42%" / "Cached · not verified". */
    val word: String
        get() = if (pct != null) partialWord(pct) else CompletenessWording.UNVERIFIED_WORD

    /** "Partial 42%" / "Not verified" -- the list rows and compact tiles. */
    val compact: String
        get() = if (pct != null) partialCompactWord(pct) else CompletenessWording.UNVERIFIED_COMPACT_WORD
}

/** "Partially cached 42%". */
fun partialWord(pct: Int): String = "${CompletenessWording.PARTIAL_PREFIX}$pct%"

/** "Partial 42%". */
fun partialCompactWord(pct: Int): String = "${CompletenessWording.PARTIAL_COMPACT_PREFIX}$pct%"

/** A completeness figure from the API: 0..100, else null. */
fun knownPct(value: Int?): Int? = value?.takeIf { it in 0..100 }

/** The figure a card shows: the group's for a group card (D9, [isGroup] =
 * the card heads content packages), else the row's own. */
fun completenessPctOf(row: GameSummary, isGroup: Boolean): Int? =
    knownPct(if (isGroup) row.group_cache_complete_pct else row.cache_complete_pct)

/** [CompletenessState] of a row (meaningful for a CACHED card). */
fun completenessState(row: GameSummary, isGroup: Boolean): CompletenessState {
    val pct = completenessPctOf(row, isGroup) ?: return CompletenessState.UNVERIFIED
    return if (pct == 100) CompletenessState.COMPLETE else CompletenessState.PARTIAL
}

/**
 * The overlay for a card of [kind] (twin of web `cardDisplay`): null when
 * the card shows its own kind -- not CACHED, the update state wins (rule
 * 1), or the figure is 100.
 */
fun completenessOverlay(row: GameSummary, kind: StatusKind, isGroup: Boolean): CompletenessOverlay? {
    if (kind != StatusKind.CACHED || showsUpdateState(row, kind)) return null
    return when (completenessState(row, isGroup)) {
        CompletenessState.COMPLETE -> null
        CompletenessState.PARTIAL -> CompletenessOverlay(StatusKind.PARTIAL, completenessPctOf(row, isGroup))
        CompletenessState.UNVERIFIED -> CompletenessOverlay(StatusKind.UNVERIFIED, null)
    }
}

// ---------------------------------------------------------------------
// Detail sheet
// ---------------------------------------------------------------------

/** True for a row whose own figure is known, below 100 and short of no
 * missing chunk file: only wrong-size chunks, nothing a forced run fixes. */
fun isDamagedOnly(row: GameSummary): Boolean {
    val pct = knownPct(row.cache_complete_pct) ?: return false
    return pct < 100 && row.cache_missing_chunks == 0
}

/** True for a row "Complete now" can help: known, below 100, at least one
 * chunk file missing (the server's `cache_completeness.is_completable`). */
fun isCompletable(row: GameSummary): Boolean {
    val pct = knownPct(row.cache_complete_pct) ?: return false
    val missing = row.cache_missing_chunks ?: return false
    return pct < 100 && missing > 0
}

/**
 * The detail sheet's completeness line for a row's OWN figure: "80%
 * complete — 3.2 of 4.0 GB on disk · checked <time>", or
 * [CompletenessWording.UNKNOWN_LINE].
 */
fun completenessLine(row: GameSummary): String {
    val pct = knownPct(row.cache_complete_pct) ?: return CompletenessWording.UNKNOWN_LINE
    val present = formatBytesGB(row.cache_present_bytes) ?: CompletenessWording.ZERO_GB
    val expected = formatBytesGB(row.cache_expected_bytes)
    val amount = if (pct == 100 || expected == null) {
        "$present${CompletenessWording.ON_DISK}"
    } else {
        "$present${CompletenessWording.OF}$expected${CompletenessWording.ON_DISK}"
    }
    val checked = row.cache_complete_checked_at?.takeIf { it.isNotEmpty() }
        ?.let { "${CompletenessWording.CHECKED}${formatTimestamp(it)}" }
        .orEmpty()
    return "$pct${CompletenessWording.COMPLETE_SUFFIX}$amount$checked"
}

/** The group line under it for a group row with a known group figure, else
 * null: "With content packages: 75% complete". */
fun groupCompletenessLine(row: GameSummary?, isGroup: Boolean): String? {
    if (row == null || !isGroup) return null
    val pct = knownPct(row.group_cache_complete_pct) ?: return null
    return "${CompletenessWording.GROUP_LINE_PREFIX}$pct${CompletenessWording.GROUP_LINE_SUFFIX}"
}

/** Whether the sheet shows the completeness block at all: a non-tool game
 * with bytes on disk (its own, or one of its [packages]'). */
fun showsCompletenessBlock(game: GameSummary, packages: List<GameSummary>): Boolean {
    if (isToolApp(game)) return false
    return hasVisibleCacheContent(game) || packages.any { hasVisibleCacheContent(it) }
}

/**
 * The app ids the sheet's "Verify" sends: the game and, for a group, its
 * packages -- each with bytes on disk, not a tool app and not short only of
 * wrong-size chunks (a forced run cannot help those). In appid order.
 */
fun verifyAppids(members: List<GameSummary>): List<Int> =
    members
        .filter { hasVisibleCacheContent(it) && !isToolApp(it) && !isDamagedOnly(it) }
        .map { it.appid }
        .sorted()

/** "Verify — Emberreach" -- the button's spoken label. */
fun verifySpokenLabel(title: String): String =
    "${CompletenessWording.VERIFY_LABEL}${CompletenessWording.DASH}$title"
