package dev.steamvault.app.ui.downloads.logic

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.net.model.PrefillJobRef
import dev.steamvault.app.net.model.ScheduleOut
import dev.steamvault.app.ui.library.logic.countForcedCachedGames
import dev.steamvault.app.ui.library.logic.isToolApp
import dev.steamvault.app.ui.library.logic.partitionCachedPrefillOutcome

/**
 * "Updates available" in the Downloads screen (WP UPD-6, Android twin of
 * `web/js/lib/updates-available.js`; user feedback on prod rc15: "Checked
 * 14 games · 7 updates available", but the 7 were hard to find). Pure
 * decision and wording logic, no Compose, no network -- `DownloadsController`
 * is the glue, `UpdatesAvailableTest` the pins.
 *
 * **Which games.** Every `GET /v1/games` row with `update_available` (WP
 * UPD-1: a stale oracle verdict on an app with cache content) that has no
 * prefill job queued, running or paused right now -- once a job exists the
 * game is in the normal queue and leaves this list; once the job finishes
 * the server clears the flag. Raw rows, not Library groups: a flagged
 * content package is its own prefill target, so it is its own row (named
 * "<parent> · content <appid>" by [nameFor] when it has no name). Steam
 * tool apps never appear (`POST /v1/prefill` answers 422 for them).
 *
 * **Update now / Update all** send `POST /v1/prefill` with exactly the
 * listed app ids -- the same non-forced enqueue "Check & update" uses.
 *
 * **Schedule note.** From `GET /v1/schedule` (read once per screen visit):
 * with a window (`enabled`) the sweep downloads automatically in it -- for
 * every cached game when `sweep_include_cached` is on, otherwise only for
 * games installed on a PC that reports to the vault; without a window
 * nothing downloads on its own. `null` while the schedule is not known.
 *
 * **Wording.** Kotlin literals, not `strings.xml`, on purpose -- the same
 * recorded exception `CachedPrefillOutcome.kt` and `BulkJobs.kt` use: every
 * literal in [UpdatesWording] is pinned against the web source by
 * `UpdatesAvailableWebTwinTest`, so the two frontends cannot drift apart.
 */
object UpdatesWording {
    const val HEADING = "Updates available"
    const val UPDATE_NOW = "Update now"
    const val UPDATE_ALL = "Update all"
    const val STARTING = "Starting…"
    const val MANUAL_ONLY_NOTE = "Manual only — no schedule window is set."
    const val NOTE_INSTALLED_ONLY_PREFIX = "Those installed on a PC download automatically in the schedule window "
    const val NOTE_INSTALLED_ONLY_SUFFIX = "the others only when you start them here."
    const val NOTE_ALL_PREFIX = "These download automatically in the schedule window "
    const val NOTHING_TO_UPDATE = "Nothing to update."
    const val QUEUED_FOR_UPDATE = "queued for update"
    const val ALREADY_QUEUED = "already queued"
    const val ALREADY_IN_PROGRESS = "already in progress"
    const val PAUSED_RESUME_OR_CANCEL = "paused — resume or cancel"
    const val PAUSED_FIRST = "first"
    const val FORCED_NOTE = "forced — those run full, disk-speed re-checks and may take longer"
    const val PART_SEPARATOR = " · "
    const val GAME = "game"
    const val GAMES = "games"
    const val SERVER_PARTWAY =
        "The server had trouble partway through — some updates may already be queued. Re-checking Downloads…"
    const val START_FAILED = "Could not start the update."
}

/** Job statuses that put an app into the normal queue (Active / Paused /
 * Queue sections) and therefore out of this list. */
private val ACTIVE_JOB_STATUSES = setOf("queued", "running", "paused")

/**
 * The flagged games without an active prefill job, sorted by display name
 * (case-insensitive), then appid.
 * @param titleOf display name, for sorting
 */
fun pendingUpdates(
    games: List<GameSummary>,
    jobs: List<JobSummary>,
    titleOf: (GameSummary) -> String = { it.name ?: "" },
): List<GameSummary> {
    val busy = jobs.filter { it.type == "prefill" && it.status in ACTIVE_JOB_STATUSES }.map { it.appid }.toSet()
    return games
        .filter { it.update_available && !isToolApp(it) && it.appid !in busy }
        .map { it to titleOf(it).lowercase() }
        .sortedWith(compareBy<Pair<GameSummary, String>> { it.second }.thenBy { it.first.appid })
        .map { it.first }
}

/** "Updates available (N)". */
fun updatesHeadingText(count: Int): String = "${UpdatesWording.HEADING} ($count)"

/** "Update all 7 games" -- the button's spoken label. */
fun updateAllSpokenLabel(count: Int): String =
    "${UpdatesWording.UPDATE_ALL} $count ${if (count == 1) UpdatesWording.GAME else UpdatesWording.GAMES}"

/** "Update now — Emberreach" -- the row button's spoken label. */
fun updateNowSpokenLabel(title: String): String = "${UpdatesWording.UPDATE_NOW} — $title"

private val WINDOW = Regex("""^(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})$""")

/** "22:00-06:00" -> "22:00–06:00" (en dash). Anything that is not two
 * HH:MM times is returned trimmed as configured, never reinterpreted. */
fun formatWindow(raw: String): String {
    val text = raw.trim()
    val m = WINDOW.find(text) ?: return text
    return "${m.groupValues[1]}–${m.groupValues[2]}"
}

/** The note under the heading; `null` while the schedule is not known. */
fun scheduleNote(schedule: ScheduleOut?): String? {
    if (schedule == null) return null
    val window = schedule.window?.takeIf { it.isNotBlank() }
    if (!schedule.enabled || window == null) return UpdatesWording.MANUAL_ONLY_NOTE
    val formatted = formatWindow(window)
    if (!schedule.sweep_include_cached) {
        return "${UpdatesWording.NOTE_INSTALLED_ONLY_PREFIX}$formatted; ${UpdatesWording.NOTE_INSTALLED_ONLY_SUFFIX}"
    }
    return "${UpdatesWording.NOTE_ALL_PREFIX}$formatted."
}

/** Toast for an update start, success or failure. [refresh]: re-read the
 * jobs (a 5xx can leave the first apps already queued). [long]: the toast
 * stays [BULK_TOAST_MS] instead of [TOAST_MS] (the 5xx "partway" note is a
 * long sentence, like the bulk results). */
data class UpdateStartOutcome(
    val message: String,
    val warn: Boolean,
    val refresh: Boolean,
    val long: Boolean = false,
)

/** Toast for a successful `POST /v1/prefill` from this section. */
fun summarizeUpdateStart(refs: List<PrefillJobRef>?, games: List<GameSummary>?): UpdateStartOutcome {
    val p = partitionCachedPrefillOutcome(refs)
    if (p.total == 0) return UpdateStartOutcome(UpdatesWording.NOTHING_TO_UPDATE, warn = false, refresh = true)
    val parts = mutableListOf<String>()
    if (p.queued.isNotEmpty()) parts.add("${p.queued.size} ${UpdatesWording.QUEUED_FOR_UPDATE}")
    if (p.alreadyQueued.isNotEmpty()) parts.add("${p.alreadyQueued.size} ${UpdatesWording.ALREADY_QUEUED}")
    if (p.alreadyRunning.isNotEmpty()) parts.add("${p.alreadyRunning.size} ${UpdatesWording.ALREADY_IN_PROGRESS}")
    if (p.alreadyPaused.isNotEmpty()) {
        val pronoun = if (p.alreadyPaused.size > 1) "them" else "it"
        parts.add("${p.alreadyPaused.size} ${UpdatesWording.PAUSED_RESUME_OR_CANCEL} $pronoun ${UpdatesWording.PAUSED_FIRST}")
    }
    var message = parts.joinToString(UpdatesWording.PART_SEPARATOR)
    if (p.queued.isNotEmpty()) {
        val forced = countForcedCachedGames(p.queued, games)
        if (forced > 0) message += " ($forced ${UpdatesWording.FORCED_NOTE})"
    }
    return UpdateStartOutcome(
        message.replaceFirstChar { it.uppercaseChar() },
        warn = p.alreadyPaused.isNotEmpty(),
        refresh = true,
    )
}

/** How to report a failed `POST /v1/prefill` from this section. */
fun describeUpdateStartError(err: Throwable?): UpdateStartOutcome {
    if (err is VaultApiError.Server) {
        return UpdateStartOutcome(UpdatesWording.SERVER_PARTWAY, warn = true, refresh = true, long = true)
    }
    val detail = (err as? VaultApiError)?.detail?.takeIf { it.isNotBlank() }
    val message = detail ?: err?.message?.takeIf { it.isNotBlank() } ?: UpdatesWording.START_FAILED
    return UpdateStartOutcome(message, warn = true, refresh = false)
}

/**
 * The app ids the detail sheet's "Update now" sends (twin of web
 * `sheetUpdateAppids`): the game's own row and, for a group, its packages'
 * rows -- each only while flagged, not a tool app and without an active
 * prefill job (the same rule as [pendingUpdates]), in appid order.
 */
fun sheetUpdateAppids(members: List<GameSummary>, jobs: List<JobSummary>): List<Int> =
    pendingUpdates(members, jobs) { "" }.map { it.appid }
