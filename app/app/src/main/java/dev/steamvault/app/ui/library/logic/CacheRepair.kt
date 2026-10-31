package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.net.model.RepairDepotOut
import dev.steamvault.app.net.model.RepairOut
import java.util.Locale

/**
 * "Repair" for a game short only of wrong-size chunk files (WP ANDROID-DL-2,
 * Android twin of `web/js/lib/cache-repair.js`, WP CACHE-REPAIR-UI-1). Pure
 * decision, wording and flow logic, no Compose, no network:
 * `ui/downloads/RepairFlow.kt` is the dialog and the request glue, the
 * Downloads "Incomplete" rows and the detail sheet's completeness block
 * place the button; `CacheRepairTest` pins this file and
 * `CacheRepairWebTwinTest` its words against the web source.
 *
 * **The API (api/README.md, `POST /v1/cache/{appid}/repair`, WP
 * CACHE-REPAIR-1).** Dry run by default (no body); `{"execute": true}`
 * deletes the wrong-size chunk files of the app's target manifests and
 * queues a forced prefill that fetches fresh copies. `200` for a dry run or
 * a no-op, `202` once files were removed and the job queued; `404` unknown
 * app / no mappings; `409` an active job of the app or of a co-owner of a
 * damaged depot, or the app's last prefill ended `not_owned` (both also on
 * the dry run); `422` more than `max_files` damaged on execute (the dry run
 * reports `exceeds_cap: true`); `500` cache-root guard.
 *
 * **Who gets the button ([showsRepair]).** A row whose OWN figure is known,
 * below 100 and short of no missing chunk file ([isDamagedOnly]), not a
 * Steam tool app, not known as not owned (the row says why instead:
 * [RepairWording.NOT_OWNED_NOTE]) and without a queued, running or paused
 * prefill of its own.
 *
 * **The flow ([reduceRepairFlow]).** Same posture as `GcFlow.kt`: EXECUTE is
 * never sent without an explicit confirm after a dry run. The only way into
 * [RepairFlowState.Executing] is [RepairEvent.Confirm] fired from
 * [RepairFlowState.Confirm], and Confirm is reached only from a dry-run
 * answer that found damaged files within the cap. Every unrecognised
 * (state, event) pair returns the state unchanged (the same instance). Every
 * request answer carries the token of the flow that sent it, so a late
 * answer of a closed flow never lands in a newer one (docs/LEARNINGS.md
 * rc21 "Async UI flows need a per-flow token").
 *
 * **Verbatim-port exception (app/README.md "String resources").** Every
 * string is the web module's own literal or template, pinned by
 * `CacheRepairWebTwinTest`.
 */
object RepairWording {
    const val LABEL = "Repair"
    const val CHECKING_LABEL = "Checking…"
    const val HINT =
        "Deletes the chunk files that have the wrong size, then downloads fresh copies. You confirm before anything is deleted."
    const val NOTE = "Damaged chunks — Verify can't fix these, Repair can."
    const val NOT_OWNED_NOTE = "Damaged chunks — can't be repaired: this Steam account doesn't own the game."
    const val CONFIRM_LABEL = "Delete and repair"
    const val EXECUTING_LABEL = "Repairing…"
    const val CANCEL_LABEL = "Cancel"
    const val CLOSE_LABEL = "Close"
    const val SHOW_JOB_LABEL = "Show job"
    const val DONE_TITLE = "Repair started"
    const val NOOP_TITLE = "Nothing was deleted"
    const val NOTHING_TEXT = "Nothing to repair — no chunk file with the wrong size was found."
    const val NOOP_TEXT =
        "The damaged files had changed or were gone when the repair ran, so nothing was deleted and no download was queued."
    const val BUSY_TEXT =
        "A download that uses these files is queued, running or paused. Repair once it has finished — nothing was deleted."
    const val NOT_OWNED_TEXT =
        "The last download of this game ended 'not owned', so fresh copies could not be fetched — nothing was deleted."
    const val NETWORK_TEXT = "Could not reach vault-api — nothing was changed."
    const val EXECUTE_UNKNOWN_TEXT =
        "The repair did not answer cleanly — some files may already be deleted. Re-checking Downloads…"
    const val FAILED_TEXT = "Could not start the repair."
    const val PLAN_TAIL = "Repair deletes them, then queues a forced download that fetches fresh copies."
}

/** "Repair — Emberreach": the button's spoken label. */
fun repairSpokenLabel(title: String): String = "${RepairWording.LABEL} — $title"

/** "Repair Emberreach?" -- the confirm dialog's title. */
fun repairConfirmTitle(title: String): String = "${RepairWording.LABEL} $title?"

/** "Can't repair Emberreach" -- the title when the plan exceeds the cap. */
fun repairBlockedTitle(title: String): String = "Can't repair $title"

/** "1 file" / "12 files". */
fun repairFileCount(n: Int): String = "$n ${if (n == 1) "file" else "files"}"

/**
 * A damaged amount: below 1 GiB in MB (a handful of wrong-size chunks is
 * typically a few MB, which "0.0 GB" would hide), else the app's GB form.
 */
fun repairSizeText(bytes: Long?): String {
    if (bytes == null || bytes <= 0L) return "0 MB"
    if (bytes < 1_073_741_824L) return "${String.format(Locale.ROOT, "%.1f", bytes / 1_048_576.0)} MB"
    return formatBytesGB(bytes) ?: "0 MB"
}

/** The confirm's main sentence from a dry-run report. */
fun repairPlanText(report: RepairOut): String =
    "${repairFileCount(report.damaged_chunks)} (${repairSizeText(report.damaged_bytes)}) in the cache have the wrong size. " +
        RepairWording.PLAN_TAIL

/** One depot line of the confirm: "Depot 2010081 · 3 files · 4.2 MB". */
fun repairDepotLine(depot: RepairDepotOut): String =
    "Depot ${depot.depotid} · ${repairFileCount(depot.damaged_chunks)} · ${repairSizeText(depot.damaged_bytes)}"

/** The note for depots whose target manifest could not be read, or "". */
fun unreadableDepotsText(report: RepairOut): String {
    val ids = report.unreadable_depots
    if (ids.isEmpty()) return ""
    val which = ids.joinToString(", ")
    return if (ids.size == 1) {
        "Depot $which could not be read and is left out."
    } else {
        "Depots $which could not be read and are left out."
    }
}

/** Why a dry run that exceeds the cap is refused (execute would answer 422). */
fun repairBlockedText(report: RepairOut): String =
    "${repairFileCount(report.damaged_chunks)} have the wrong size — more than the repair limit of ${report.max_files}. " +
        "That many mismatches point at a wrong target manifest or a broken cache. Nothing was deleted."

/** The result sentence after an execute that deleted files. */
fun repairDoneText(report: RepairOut): String {
    val removed = "Deleted ${repairFileCount(report.removed_chunks)} (${repairSizeText(report.removed_bytes)})."
    val skipped = report.skipped.values.sumOf { if (it > 0) it else 0 }
    val skippedText = if (skipped > 0) " ${repairFileCount(skipped)} changed meanwhile and stayed." else ""
    val jobId = report.job_id ?: return removed + skippedText
    val job = if (report.job_deduplicated == true) {
        " The already queued job #$jobId fetches fresh copies."
    } else {
        " Job #$jobId fetches fresh copies."
    }
    return removed + skippedText + job
}

// ---------------------------------------------------------------------
// Visibility
// ---------------------------------------------------------------------

private val ACTIVE_JOB_STATUSES = setOf("queued", "running", "paused")

/** True while [jobs] holds a queued, running or paused prefill of [appid]. */
private fun hasActivePrefill(appid: Int, jobs: List<JobSummary>): Boolean =
    jobs.any { it.type == "prefill" && it.appid == appid && it.status in ACTIVE_JOB_STATUSES }

/** Whether a row offers "Repair" (see the file kdoc). */
fun showsRepair(row: GameSummary?, jobs: List<JobSummary>): Boolean {
    if (row == null || isToolApp(row) || isNotOwned(row)) return false
    if (!isDamagedOnly(row)) return false
    return !hasActivePrefill(row.appid, jobs)
}

/** The note a damaged-only row shows: [RepairWording.NOTE] next to the
 * button, [RepairWording.NOT_OWNED_NOTE] when the game is known as not
 * owned, "" for every other row. */
fun repairNoteFor(row: GameSummary?): String {
    if (row == null || isToolApp(row) || !isDamagedOnly(row)) return ""
    return if (isNotOwned(row)) RepairWording.NOT_OWNED_NOTE else RepairWording.NOTE
}

// ---------------------------------------------------------------------
// Flow
// ---------------------------------------------------------------------

/** The flow's states (web `REPAIR_STATE`). Every state but [Idle] carries
 * the [token] of the flow it belongs to. */
sealed class RepairFlowState {
    data object Idle : RepairFlowState()
    data class Checking(val appid: Int, val title: String, val token: Int) : RepairFlowState()
    data class Confirm(val appid: Int, val title: String, val token: Int, val report: RepairOut, val error: String? = null) :
        RepairFlowState()
    data class Blocked(val appid: Int, val title: String, val token: Int, val report: RepairOut) : RepairFlowState()
    data class Executing(val appid: Int, val title: String, val token: Int, val report: RepairOut) : RepairFlowState()
    data class Done(val appid: Int, val title: String, val token: Int, val report: RepairOut) : RepairFlowState()
}

/** The flow's events (web `REPAIR_EVENT`); every request answer names the
 * [token] of the flow that sent it. */
sealed class RepairEvent {
    data class Start(val appid: Int, val title: String, val token: Int) : RepairEvent()
    data class DryRunOk(val token: Int, val report: RepairOut) : RepairEvent()
    data class DryRunFailed(val token: Int) : RepairEvent()
    data object Confirm : RepairEvent()
    data class ExecuteOk(val token: Int, val report: RepairOut) : RepairEvent()
    data class ExecuteFailed(val token: Int, val message: String) : RepairEvent()
    data object Close : RepairEvent()
    data object Reset : RepairEvent()
}

/** The token of the flow [state] belongs to, or `null` for [RepairFlowState.Idle]. */
fun RepairFlowState.tokenOrNull(): Int? = when (this) {
    RepairFlowState.Idle -> null
    is RepairFlowState.Checking -> token
    is RepairFlowState.Confirm -> token
    is RepairFlowState.Blocked -> token
    is RepairFlowState.Executing -> token
    is RepairFlowState.Done -> token
}

/** True for the states that show the dialog (web `paint`'s `open`). */
fun RepairFlowState.showsDialog(): Boolean =
    this is RepairFlowState.Confirm || this is RepairFlowState.Executing ||
        this is RepairFlowState.Blocked || this is RepairFlowState.Done

/** The next state (web `reduceRepairFlow`); the same instance for a no-op. */
fun reduceRepairFlow(state: RepairFlowState, event: RepairEvent): RepairFlowState = when (event) {
    is RepairEvent.Start ->
        if (state is RepairFlowState.Idle) RepairFlowState.Checking(event.appid, event.title, event.token) else state

    is RepairEvent.DryRunOk -> {
        if (state !is RepairFlowState.Checking || state.token != event.token) {
            state
        } else {
            val report = event.report
            when {
                // Nothing damaged (any more): no dialog, the caller says so.
                report.damaged_chunks <= 0 -> RepairFlowState.Idle
                report.exceeds_cap -> RepairFlowState.Blocked(state.appid, state.title, state.token, report)
                else -> RepairFlowState.Confirm(state.appid, state.title, state.token, report, error = null)
            }
        }
    }

    is RepairEvent.DryRunFailed ->
        if (state is RepairFlowState.Checking && state.token == event.token) RepairFlowState.Idle else state

    // THE pin: the only way to Executing, and only from Confirm.
    RepairEvent.Confirm ->
        if (state is RepairFlowState.Confirm) {
            RepairFlowState.Executing(state.appid, state.title, state.token, state.report)
        } else {
            state
        }

    is RepairEvent.ExecuteOk ->
        if (state is RepairFlowState.Executing && state.token == event.token) {
            RepairFlowState.Done(state.appid, state.title, state.token, event.report)
        } else {
            state
        }

    is RepairEvent.ExecuteFailed ->
        if (state is RepairFlowState.Executing && state.token == event.token) {
            RepairFlowState.Confirm(state.appid, state.title, state.token, state.report, error = event.message)
        } else {
            state
        }

    // Cancel / Close / back: never while the execute request is in flight.
    RepairEvent.Close ->
        if (state is RepairFlowState.Confirm || state is RepairFlowState.Blocked || state is RepairFlowState.Done) {
            RepairFlowState.Idle
        } else {
            state
        }

    RepairEvent.Reset -> RepairFlowState.Idle
}

// ---------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------

/** How to report a failed repair request: the message, and whether the
 * caller re-reads games and jobs. */
data class RepairErrorOutcome(val message: String, val refresh: Boolean)

/**
 * How to report a failed repair request (web `describeRepairError`).
 *
 * - network: nothing reached the server on a dry run; on an execute the
 *   outcome is unknown, so it says so and asks for a re-read.
 * - 409: the server's two reasons (api `routers/cache.py`), told apart by
 *   the `not_owned` token its detail names; both mean nothing was deleted.
 * - 5xx on execute: the deletion may have started, so a re-read.
 * - everything else (404, 422, a 5xx dry run, 401): the server's own detail.
 */
fun describeRepairError(err: VaultApiError, execute: Boolean): RepairErrorOutcome {
    if (err is VaultApiError.Network) {
        return if (execute) {
            RepairErrorOutcome(RepairWording.EXECUTE_UNKNOWN_TEXT, refresh = true)
        } else {
            RepairErrorOutcome(RepairWording.NETWORK_TEXT, refresh = false)
        }
    }
    if (err.status == 409) {
        val detail = err.detail.orEmpty()
        return RepairErrorOutcome(if (detail.contains("not_owned")) RepairWording.NOT_OWNED_TEXT else RepairWording.BUSY_TEXT, refresh = true)
    }
    if (err is VaultApiError.Server && execute) return RepairErrorOutcome(RepairWording.EXECUTE_UNKNOWN_TEXT, refresh = true)
    val detail = err.detail?.takeIf { it.isNotEmpty() }
    return RepairErrorOutcome(detail ?: err.message?.takeIf { it.isNotEmpty() } ?: RepairWording.FAILED_TEXT, refresh = false)
}
