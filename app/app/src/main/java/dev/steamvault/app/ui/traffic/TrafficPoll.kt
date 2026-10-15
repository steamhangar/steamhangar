package dev.steamvault.app.ui.traffic

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.ui.traffic.logic.LIVE_POLL_MS
import dev.steamvault.app.ui.traffic.logic.LIVE_RANGE
import dev.steamvault.app.ui.traffic.logic.isRange
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/** How often a ranged traffic view re-asks while on screen (web
 * `store.js` `VIEW_POLL_MS`). */
const val TRAFFIC_POLL_MS = 30_000L

/**
 * One ranged traffic read, polled while its screen is on screen (WP D15d) --
 * the state machine web `views/clients.js` and the Downloads statistics
 * header in `views/downloads.js` each keep (`createViewPoll` + `state`).
 * Used by the Clients page and the Downloads statistics header.
 *
 * Rules (each one a web rule):
 * - [data] is the last answer for [range]. An answer for a range the user
 *   has since left is dropped.
 * - A failed refresh after an answer keeps [data] and sets [error]; the
 *   note then reads "Couldn't refresh: ..." next to the old figures.
 * - A 404 means a server older than D15b: [unsupported] is set and
 *   [pollForever] stops (polling again cannot change that answer; the next
 *   visit builds a new poll and asks again).
 * - [noFigures] is the last verdict "nothing to range over" (feed disabled,
 *   old server). It only changes on an answer or a 404, so while an answer
 *   is pending after a range change the selector the user just tapped
 *   stays where it is.
 *
 * Not a ViewModel (house pattern, see `DownloadsController`): held with
 * `remember` by its screen, so leaving the tab drops it.
 */
class TrafficPoll<T : Any>(
    initialRange: String,
    private val fetch: suspend (String) -> T,
    private val hasFigures: (T) -> Boolean,
    /** Which range names a chip may select (the statistics also take 1 h / 6 h). */
    private val isValidRange: (String) -> Boolean = ::isRange,
) {
    var range by mutableStateOf(initialRange)
        private set
    var data by mutableStateOf<T?>(null)
        private set
    var error by mutableStateOf<String?>(null)
        private set
    var unsupported by mutableStateOf(false)
        private set
    var noFigures by mutableStateOf(false)
        private set

    suspend fun refreshOnce() {
        if (unsupported) return
        val asked = range
        try {
            val answer = fetch(asked)
            if (asked != range) return
            data = answer
            error = null
            noFigures = !hasFigures(answer)
        } catch (e: VaultApiError.NotFound) {
            unsupported = true
            noFigures = true
        } catch (e: VaultApiError) {
            if (asked != range) return
            error = errorText(e)
        }
    }

    /**
     * Polls until [unsupported]; the screen runs it under
     * `repeatOnLifecycle(STARTED)`, so it pauses in the background (WP
     * GRAPH-LIVE: the fast live cadence never runs while the screen is not
     * visible). The wait follows the CURRENT range, like web
     * `createViewPoll`'s interval function: [liveIntervalMs] in the live
     * range, [intervalMs] otherwise. A range change restarts the wait (the
     * chip tap already asked once).
     */
    suspend fun pollForever(intervalMs: Long = TRAFFIC_POLL_MS, liveIntervalMs: Long = LIVE_POLL_MS) {
        while (!unsupported) {
            refreshOnce()
            if (unsupported) return
            var waitedRange = range
            var waited = 0L
            while (waited < intervalFor(waitedRange, intervalMs, liveIntervalMs)) {
                val slice = minOf(WAIT_SLICE_MS, intervalFor(waitedRange, intervalMs, liveIntervalMs) - waited)
                delay(slice)
                waited += slice
                if (range != waitedRange) {
                    waitedRange = range
                    waited = 0L
                }
            }
        }
    }

    /** A range chip was tapped: forget the old range's answer, ask now. */
    fun selectRange(scope: CoroutineScope, newRange: String) {
        if (!isValidRange(newRange) || newRange == range) return
        range = newRange
        data = null
        error = null
        scope.launch { refreshOnce() }
    }

    companion object {
        /** The wait is cut into slices so a range change takes effect at once. */
        private const val WAIT_SLICE_MS = 250L

        /** The pause after a poll of [range] (WP GRAPH-LIVE). */
        fun intervalFor(range: String, intervalMs: Long, liveIntervalMs: Long): Long =
            if (range == LIVE_RANGE) liveIntervalMs else intervalMs

        /** web `errorText`: the server's `detail`, else the message. */
        fun errorText(e: VaultApiError): String =
            e.detail?.takeIf { it.isNotBlank() } ?: e.message?.takeIf { it.isNotBlank() } ?: "Request failed."
    }
}
