package dev.steamvault.app.ui.downloads.logic

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.HistoryDeleteOut

/**
 * "Delete history" (WP ANDROID-GAPS-1) -- a Kotlin port of
 * `web/js/lib/history-delete.js` (WP D15-NAMES): the two variants and the
 * dialog's wording. Pure: `HistoryDeleteFlow` runs the request,
 * `HistoryDeleteDialog.kt` draws.
 *
 * **Verbatim-port exception (app/README.md "String resources").** Every
 * sentence is the web module's own, pinned by string equality in
 * `HistoryDeleteTest` and against the web source in `ClientGapsWebTwinTest`.
 *
 * What the server deletes (api/vault_api/history.py): finished jobs (done,
 * failed, cancelled) and the traffic statistics behind the Downloads
 * header, its graph and the Clients page figures. Cached games,
 * running/queued/paused jobs, PCs and client names are never touched.
 */

const val SCOPE_OLDER = "older-than-30d"
const val SCOPE_ALL = "all"

data class HistoryDeleteOption(val value: String, val label: String, val desc: String)

/** Dialog options, safest first (and the default). */
val HISTORY_DELETE_OPTIONS: List<HistoryDeleteOption> = listOf(
    HistoryDeleteOption(
        value = SCOPE_OLDER,
        label = "Older than 30 days",
        desc = "Finished downloads and traffic statistics from before the last 30 days.",
    ),
    HistoryDeleteOption(
        value = SCOPE_ALL,
        label = "Everything",
        desc = "All finished downloads and all traffic statistics. Speed, graph and client figures start again from zero.",
    ),
)

const val HISTORY_DELETE_TITLE = "Delete history?"

const val HISTORY_DELETE_WHAT =
    "Deletes finished downloads (done, failed, cancelled) with their logs, and the traffic statistics behind the speed figures, the graph and the numbers on the Clients page."

const val HISTORY_DELETE_KEEPS =
    "Kept: cached games, running, queued and paused downloads, PCs and client names. This cannot be undone."

/** The legend over the two options (web `views/downloads.js`). */
const val HISTORY_DELETE_LEGEND = "What to delete"

fun isHistoryScope(scope: String?): Boolean = scope == SCOPE_OLDER || scope == SCOPE_ALL

/** Confirm button label for the chosen variant. */
fun historyDeleteButtonText(scope: String): String =
    if (scope == SCOPE_ALL) "Delete everything" else "Delete older than 30 days"

/** Note after a successful delete, from the server's answer. */
fun historyDeletedText(result: HistoryDeleteOut?): String {
    val n = result?.jobs_deleted ?: 0
    val jobs = "$n finished download${if (n == 1) "" else "s"}"
    if (result?.scope == SCOPE_ALL) return "History deleted: $jobs and all traffic statistics."
    return "History older than 30 days deleted: $jobs and the traffic statistics from that time."
}

/** Error line in the dialog. Only a 404 means "server too old" (a 401 is
 * a key problem, never an outdated server). */
fun historyDeleteErrorText(error: Throwable): String {
    if (error is VaultApiError.NotFound) return "This server cannot delete history yet. Update vault-api to use this."
    val detail = (error as? VaultApiError)?.detail?.takeIf { it.isNotEmpty() }
        ?: error.message?.takeIf { it.isNotEmpty() }
        ?: "Request failed."
    return "Could not delete history: $detail"
}
