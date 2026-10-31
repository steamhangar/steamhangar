package dev.steamvault.app.ui.downloads

import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.focus.FocusRequester

/**
 * WP ANDROID-DL-2: where the Downloads list moves after an action, twin of
 * the web's focus rules (web `highlightJob` after a repair's "Show job",
 * DOWNLOADS-3's "Show more"). Kept apart from `DownloadsScreen.kt` so the
 * screen only calls them.
 */

/** The list keys a live or finished job has on the Downloads screen
 * (`DownloadsBody`'s item keys). */
internal fun jobItemKeys(jobId: Int): Set<String> =
    setOf("active-$jobId", "paused-$jobId", "queue-$jobId", "history-$jobId")

/**
 * Scroll the Downloads list to [jobId]'s card or row (web `highlightJob`:
 * the job's card in Active, its Queue row, or its History row). A lazy list
 * only knows the keys of the rows it has laid out, so this pages through
 * the list -- first down to the end, then up to the top -- until the row is
 * on screen, then puts it at the top without animation (reduced-motion
 * safe). Bounded by [maxSteps]; `false` when the job is not in the list
 * (yet). Waits one frame first, so a jobs poll that just landed is laid out.
 */
internal suspend fun LazyListState.scrollToJob(jobId: Int, maxSteps: Int = 60): Boolean {
    val keys = jobItemKeys(jobId)
    withFrameNanos { }
    fun find() = layoutInfo.visibleItemsInfo.firstOrNull { it.key in keys }
    var forward = true
    repeat(maxSteps) {
        find()?.let {
            scrollToItem(it.index)
            return true
        }
        val info = layoutInfo
        val page = ((info.viewportEndOffset - info.viewportStartOffset) * 0.8f).coerceAtLeast(1f)
        if (forward && !canScrollForward) forward = false
        if (!forward && !canScrollBackward) return false
        scroll { scrollBy(if (forward) page else -page) }
    }
    return false
}

/** Request focus for the first new History row after "Show N more" once it
 * is laid out; a row that is not on screen keeps focus where it is. */
internal suspend fun focusFirstNewRow(requester: FocusRequester) {
    withFrameNanos { }
    try {
        requester.requestFocus()
    } catch (_: IllegalStateException) {
        // The row is not composed (scrolled away meanwhile): leave focus alone.
    }
}
