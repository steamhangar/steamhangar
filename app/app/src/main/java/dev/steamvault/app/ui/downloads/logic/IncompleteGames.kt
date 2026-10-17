package dev.steamvault.app.ui.downloads.logic

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.net.model.PrefillJobRef
import dev.steamvault.app.net.model.ScheduleOut
import dev.steamvault.app.ui.library.logic.isCompletable
import dev.steamvault.app.ui.library.logic.isToolApp
import dev.steamvault.app.ui.library.logic.knownPct
import dev.steamvault.app.ui.library.logic.partitionCachedPrefillOutcome

/**
 * "Incomplete (N)" in the Downloads screen and the toasts of "Verify" /
 * "Complete now" / "Complete all" (WP CACHE-COMPLETE-1c, Android twin of
 * the Downloads half of `web/js/lib/cache-completeness.js`). Pure decision
 * and wording logic -- `DownloadsController`/`DetailController` are the
 * glue, `IncompleteGamesTest` the pins, `CacheCompletenessWebTwinTest` the
 * wording twin.
 *
 * **Which games.** The raw `GET /v1/games` rows whose own figure is known
 * and below 100 (the server's `cache_completeness.is_incomplete`), Steam
 * tool apps left out (the server leaves them out too), without an active
 * prefill job (once a job exists the game is in the queue), and without
 * `update_available` (it is listed under "Updates available", and "Update
 * now" completes it as well).
 *
 * **Actions.** "Complete now" (one row) and "Complete all" send `POST
 * /v1/prefill/verify` with exactly the ids they list -- "Complete all" only
 * the rows "Complete now" is offered for, never the server's own selection
 * (`POST /v1/prefill/incomplete` ignores jobs and update flags, so it could
 * force games this list does not show). A row short only of wrong-size
 * chunks gets no "Complete now" and says so instead
 * ([dev.steamvault.app.ui.library.logic.CompletenessWording.DAMAGED_NOTE]).
 *
 * **Schedule note.** Completion is a download, so it follows the update
 * rules (README "Automatic completion"): nothing outside the window, manual
 * only without one. Unlike updates it covers every incomplete cached game
 * whether or not a PC reports it, so `sweep_include_cached` plays no part.
 */
object IncompleteWording {
    const val HEADING = "Incomplete"
    const val COMPLETE_NOW = "Verify"
    const val COMPLETE_ALL = "Complete all"
    const val NOTE_PREFIX = "These complete automatically in the schedule window "
    const val NOTHING_TO_COMPLETE = "Nothing to complete."
    const val NOTHING_TO_VERIFY = "Nothing to verify."
    const val QUEUED_TO_COMPLETE = "Queued to complete."
    const val QUEUED_TO_VERIFY = "Queued to verify."
    const val ALREADY_QUEUED_ONE = "Already queued — it runs as a verify."
    const val PAUSED_ONE = "Paused — it verifies when you resume it."
    const val RUNNING_ONE = "Already downloading — verifies on the next run."
    const val QUEUED_TO = "queued to"
    const val COMPLETE = "complete"
    const val VERIFY = "verify"
    const val ALREADY_QUEUED_MANY = "already queued (runs as a verify)"
    const val RUNNING_MANY = "already downloading (verifies on the next run)"
    const val PAUSED_MANY = "paused (verifies when resumed)"
    const val SERVER_PARTWAY =
        "The server had trouble partway through — some games may already be queued. Re-checking Downloads…"
    const val START_FAILED = "Could not start the verify."
    const val PCT_UNKNOWN = "—"
}

/** Which verb a newly queued job gets in the toast (web `mode`). */
enum class VerifyMode { VERIFY, COMPLETE }

private val ACTIVE_JOB_STATUSES = setOf("queued", "running", "paused")

/**
 * The incomplete games the Downloads section lists (see the file kdoc),
 * sorted by display name (case-insensitive), then appid.
 */
fun pendingIncomplete(
    games: List<GameSummary>,
    jobs: List<JobSummary>,
    titleOf: (GameSummary) -> String = { it.name ?: "" },
): List<GameSummary> {
    val busy = jobs.filter { it.type == "prefill" && it.status in ACTIVE_JOB_STATUSES }.map { it.appid }.toSet()
    return games
        .filter { g ->
            if (isToolApp(g) || g.appid in busy || g.update_available) return@filter false
            val pct = knownPct(g.cache_complete_pct)
            pct != null && pct < 100
        }
        .map { it to titleOf(it).lowercase() }
        .sortedWith(compareBy<Pair<GameSummary, String>> { it.second }.thenBy { it.first.appid })
        .map { it.first }
}

/** The ids "Complete all" sends: exactly the listed rows "Complete now" is
 * offered for, in list order. Empty = no "Complete all" button. */
fun completableAppids(rows: List<GameSummary>): List<Int> = rows.filter { isCompletable(it) }.map { it.appid }

/** "Incomplete (N)". */
fun incompleteHeadingText(count: Int): String = "${IncompleteWording.HEADING} ($count)"

/** "Complete all 2 games" -- the button's spoken label; [count] is the
 * number of rows "Complete now" is offered for. */
fun completeAllSpokenLabel(count: Int): String =
    "${IncompleteWording.COMPLETE_ALL} $count ${if (count == 1) UpdatesWording.GAME else UpdatesWording.GAMES}"

/** "Complete now — Emberreach". */
fun completeNowSpokenLabel(title: String): String = "${IncompleteWording.COMPLETE_NOW} — $title"

/** The row's figure: "42%", or a dash when unknown. */
fun incompletePctText(row: GameSummary): String =
    knownPct(row.cache_complete_pct)?.let { "$it%" } ?: IncompleteWording.PCT_UNKNOWN

/** The note under the heading, from `GET /v1/schedule`; `null` while the
 * schedule is not known. */
fun incompleteScheduleNote(schedule: ScheduleOut?): String? {
    if (schedule == null) return null
    val window = schedule.window?.takeIf { it.isNotBlank() }
    if (!schedule.enabled || window == null) return UpdatesWording.MANUAL_ONLY_NOTE
    return "${IncompleteWording.NOTE_PREFIX}${formatWindow(window)}."
}

/** Toast for a verify/complete start, success or failure (same shape as
 * [UpdateStartOutcome]). */
data class VerifyStartOutcome(
    val message: String,
    val warn: Boolean,
    val refresh: Boolean,
    val long: Boolean = false,
)

/**
 * Toast for a successful `POST /v1/prefill/verify`. A deduplicated entry is
 * worded by its job's status (README "UI note"): a QUEUED job runs forced
 * when the worker claims it; a PAUSED one once resumed; a RUNNING one keeps
 * its mode and only the game's NEXT run is forced -- "verifies on the next
 * run", never presented as the verify itself.
 */
fun summarizeVerifyStart(refs: List<PrefillJobRef>?, mode: VerifyMode = VerifyMode.VERIFY): VerifyStartOutcome {
    val p = partitionCachedPrefillOutcome(refs)
    val complete = mode == VerifyMode.COMPLETE
    if (p.total == 0) {
        val message = if (complete) IncompleteWording.NOTHING_TO_COMPLETE else IncompleteWording.NOTHING_TO_VERIFY
        return VerifyStartOutcome(message, warn = false, refresh = true)
    }
    if (p.total == 1) {
        return when {
            p.queued.isNotEmpty() -> VerifyStartOutcome(
                if (complete) IncompleteWording.QUEUED_TO_COMPLETE else IncompleteWording.QUEUED_TO_VERIFY,
                warn = false,
                refresh = true,
            )
            p.alreadyQueued.isNotEmpty() -> VerifyStartOutcome(IncompleteWording.ALREADY_QUEUED_ONE, warn = false, refresh = true)
            p.alreadyPaused.isNotEmpty() -> VerifyStartOutcome(IncompleteWording.PAUSED_ONE, warn = true, refresh = true)
            else -> VerifyStartOutcome(IncompleteWording.RUNNING_ONE, warn = false, refresh = true)
        }
    }
    val verb = if (complete) IncompleteWording.COMPLETE else IncompleteWording.VERIFY
    val parts = mutableListOf<String>()
    if (p.queued.isNotEmpty()) parts.add("${p.queued.size} ${IncompleteWording.QUEUED_TO} $verb")
    if (p.alreadyQueued.isNotEmpty()) parts.add("${p.alreadyQueued.size} ${IncompleteWording.ALREADY_QUEUED_MANY}")
    if (p.alreadyRunning.isNotEmpty()) parts.add("${p.alreadyRunning.size} ${IncompleteWording.RUNNING_MANY}")
    if (p.alreadyPaused.isNotEmpty()) parts.add("${p.alreadyPaused.size} ${IncompleteWording.PAUSED_MANY}")
    val message = parts.joinToString(UpdatesWording.PART_SEPARATOR)
    return VerifyStartOutcome(
        message.replaceFirstChar { it.uppercaseChar() },
        warn = p.alreadyPaused.isNotEmpty(),
        refresh = true,
    )
}

/** How to report a failed `POST /v1/prefill/verify`. A 5xx can leave the
 * first apps already queued, so it asks for a re-read instead of claiming
 * nothing happened. */
fun describeVerifyStartError(err: Throwable?): VerifyStartOutcome {
    if (err is VaultApiError.Server) {
        return VerifyStartOutcome(IncompleteWording.SERVER_PARTWAY, warn = true, refresh = true, long = true)
    }
    val detail = (err as? VaultApiError)?.detail?.takeIf { it.isNotBlank() }
    val message = detail ?: err?.message?.takeIf { it.isNotBlank() } ?: IncompleteWording.START_FAILED
    return VerifyStartOutcome(message, warn = true, refresh = false)
}
