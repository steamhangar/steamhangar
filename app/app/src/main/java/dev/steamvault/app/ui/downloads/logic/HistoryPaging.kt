package dev.steamvault.app.ui.downloads.logic

import dev.steamvault.app.ui.traffic.logic.HISTORY_LIMIT

/**
 * Downloads History paging and the screen's subline count (WP ANDROID-DL-1,
 * a Kotlin port of web `lib/history-paging.js`, WP DOWNLOADS-3).
 *
 * The History list fetches up to [HISTORY_LIMIT] finished jobs for the
 * chosen range (`GET /v1/jobs?range=`). The screen shows the first
 * [HISTORY_FIRST_PAGE] rows and a "Show 20 more" button that appends
 * [HISTORY_PAGE_STEP] at a time; a range change starts again at the first
 * page.
 *
 * The subline ("Idle · N in history") counts the same set the list shows
 * for the selected range. It used to count the screen's plain
 * `GET /v1/jobs` poll (20 jobs), so a 7-day list of ~130 rows read
 * "20 in history". Until the range's own fetch has answered there is no
 * honest number, and the subline says "Idle" only.
 *
 * **Verbatim-port exception (app/README.md "String resources").** Every
 * user-visible literal here is the web module's own literal, pinned by
 * string equality in `HistoryPagingTest` and against the web source in
 * `HistoryPagingWebTwinTest`. Pure: no Compose.
 */

/** Rows shown before the first "Show more". */
const val HISTORY_FIRST_PAGE = 10

/** Rows each "Show more" appends. */
const val HISTORY_PAGE_STEP = 20

/** The last line when the fetch hit [HISTORY_LIMIT] and every row is shown. */
const val HISTORY_CAPPED_TEXT = "Older entries are not shown; narrow the range or delete history."

/**
 * What to render of [total] rows when [shown] are allowed (web `historyPage`).
 * [next] is how many the next "Show more" appends (0: no button); [capped]
 * is true only once every fetched row is on screen.
 */
data class HistoryPage(val visible: Int, val remaining: Int, val next: Int, val capped: Boolean)

fun historyPage(total: Int, shown: Int, capped: Boolean = false): HistoryPage {
    val t = if (total > 0) total else 0
    val s = if (shown > 0) shown else HISTORY_FIRST_PAGE
    val visible = minOf(t, s)
    val remaining = t - visible
    return HistoryPage(
        visible = visible,
        remaining = remaining,
        next = minOf(HISTORY_PAGE_STEP, remaining),
        capped = capped && remaining == 0 && t > 0,
    )
}

/** The button's visible text. */
fun showMoreText(next: Int): String = "Show $next more"

/** The button's accessible name: how many it adds and how many are left. */
fun showMoreLabel(next: Int, remaining: Int): String = "Show $next more of $remaining"

/** The row count after one more tap. */
fun nextShown(shown: Int): Int = (if (shown > 0) shown else HISTORY_FIRST_PAGE) + HISTORY_PAGE_STEP

/** True when a range fetch returned a full page, i.e. older rows may exist. */
fun isHistoryCapped(fetchedCount: Int): Boolean = fetchedCount >= HISTORY_LIMIT

/**
 * The subline while nothing runs (web `idleSubtitle`). [loaded]: the range
 * fetch has answered; [count]: rows in the History list for the range;
 * [capped]: that fetch hit [HISTORY_LIMIT].
 */
fun idleSubtitle(loaded: Boolean, count: Int, capped: Boolean = false): String {
    if (!loaded) return "Idle"
    return "Idle · $count${if (capped) "+" else ""} in history"
}

/**
 * The idle subline for the History list on screen (web `subtitleText`'s
 * idle branch): [finishedCount] rows of the ranged list, [fetchedCount]
 * rows the range fetch returned.
 */
fun historySubtitle(loaded: Boolean, finishedCount: Int, fetchedCount: Int): String {
    val capped = isHistoryCapped(fetchedCount)
    return idleSubtitle(loaded, if (capped) minOf(finishedCount, HISTORY_LIMIT) else finishedCount, capped)
}
