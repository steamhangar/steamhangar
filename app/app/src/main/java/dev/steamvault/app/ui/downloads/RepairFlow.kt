package dev.steamvault.app.ui.downloads

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.repo.CacheRepository
import dev.steamvault.app.ui.library.logic.RepairEvent
import dev.steamvault.app.ui.library.logic.RepairFlowState
import dev.steamvault.app.ui.library.logic.RepairWording
import dev.steamvault.app.ui.library.logic.describeRepairError
import dev.steamvault.app.ui.library.logic.reduceRepairFlow
import dev.steamvault.app.ui.library.logic.repairBlockedText
import dev.steamvault.app.ui.library.logic.repairBlockedTitle
import dev.steamvault.app.ui.library.logic.repairConfirmTitle
import dev.steamvault.app.ui.library.logic.repairDepotLine
import dev.steamvault.app.ui.library.logic.repairDoneText
import dev.steamvault.app.ui.library.logic.repairPlanText
import dev.steamvault.app.ui.library.logic.showsDialog
import dev.steamvault.app.ui.library.logic.tokenOrNull
import dev.steamvault.app.ui.library.logic.unreadableDepotsText
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch

/**
 * The "Repair" flow (WP ANDROID-DL-2, Android twin of web
 * `components/repair-flow.js`, WP CACHE-REPAIR-UI-1): dry run -> confirm
 * dialog with the dry run's counts -> execute -> the resulting job. Shared by
 * the Downloads "Incomplete" rows ([DownloadsController.repair]) and the
 * detail sheet's completeness block (`DetailController.repair`); the
 * decisions and every string live in `ui/library/logic/CacheRepair.kt`, this
 * file is the state holder, the requests and the dialog.
 *
 * [start] sends the DRY RUN (`POST /v1/cache/{appid}/repair`, no body) and
 * returns once that answer is handled, so the caller can show its button
 * busy ("Checking…") meanwhile:
 * - nothing damaged: a toast, the caller re-reads (the figure was stale);
 * - more than the cap: the dialog explains it, with "Close" only;
 * - otherwise the confirm: counts per depot, "Cancel" (initial focus) and
 *   "Delete and repair";
 * - a failed dry run: a toast with the honest reason, no dialog.
 * Only "Delete and repair" ([confirm]) sends `{"execute": true}`
 * (`reduceRepairFlow`, THE pin). A failed execute keeps the dialog open
 * with the error (announced as a live region). A successful one turns the
 * dialog into the result: what was deleted, the job, "Show job" (initial
 * focus; the dialog closes first) and "Close".
 *
 * Every request answer carries the token of the flow that sent it, so a
 * late answer of a closed flow (sheet closed, another game's repair started)
 * is dropped instead of landing in the newer dialog.
 */
class RepairFlow(private val repository: CacheRepository) {
    var state by mutableStateOf<RepairFlowState>(RepairFlowState.Idle)
        private set

    /** Serial of the flows started; each answer carries its flow's token. */
    private var serial = 0

    private fun dispatch(event: RepairEvent) {
        val next = reduceRepairFlow(state, event)
        if (next !== state) state = next
    }

    /**
     * Start a repair: the dry run, then the dialog (class kdoc). A call while
     * a flow is already running is a no-op.
     * @param onToast a one-line message for the screen's snackbar
     * @param onRefresh re-read games and jobs (web `store.refreshNow()`)
     */
    suspend fun start(appid: Int, title: String, onToast: (String) -> Unit, onRefresh: suspend () -> Unit) {
        if (state !is RepairFlowState.Idle) return
        val token = ++serial
        dispatch(RepairEvent.Start(appid, title, token))
        val report = try {
            repository.repair(appid, execute = false)
        } catch (e: VaultApiError) {
            if (state.tokenOrNull() != token) return // a closed flow's answer: nothing to say
            dispatch(RepairEvent.DryRunFailed(token))
            val outcome = describeRepairError(e, execute = false)
            onToast(outcome.message)
            if (outcome.refresh) onRefresh()
            return
        }
        val now = state
        if (now !is RepairFlowState.Checking || now.token != token) return // closed or superseded meanwhile
        dispatch(RepairEvent.DryRunOk(token, report))
        if (state is RepairFlowState.Idle) {
            onToast(RepairWording.NOTHING_TEXT)
            onRefresh()
        }
    }

    /** "Delete and repair": the execute, only from the confirm. */
    suspend fun confirm(onRefresh: suspend () -> Unit) {
        val confirming = state as? RepairFlowState.Confirm ?: return
        val token = confirming.token
        dispatch(RepairEvent.Confirm)
        if (state !is RepairFlowState.Executing) return
        val report = try {
            repository.repair(confirming.appid, execute = true)
        } catch (e: VaultApiError) {
            val outcome = describeRepairError(e, execute = true)
            // A late answer of a closed flow is dropped by the reducer (token).
            dispatch(RepairEvent.ExecuteFailed(token, outcome.message))
            if (outcome.refresh) onRefresh()
            return
        }
        dispatch(RepairEvent.ExecuteOk(token, report))
        // Also for a late answer: files may have been deleted, re-read.
        onRefresh()
    }

    /** Cancel / Close / back: a no-op while the execute is in flight. */
    fun close() = dispatch(RepairEvent.Close)

    /** "Show job": closes the dialog first and returns the job to show, or
     * `null` (no result, or a result without a job). */
    fun showJob(): Int? {
        val done = state as? RepairFlowState.Done ?: return null
        val jobId = done.report.job_id
        dispatch(RepairEvent.Close)
        return jobId
    }

    /** Close the dialog and forget the flow (screen left, sheet closed). An
     * execute already sent still finishes on the server. */
    fun reset() = dispatch(RepairEvent.Reset)
}

/**
 * The repair dialog for [flow]'s state; nothing outside the dialog states.
 * @param onRefresh what [RepairFlow.confirm] re-reads afterwards
 * @param onShowJob where "Show job" goes (after the dialog has closed)
 */
@Composable
fun RepairDialog(flow: RepairFlow, scope: CoroutineScope, onRefresh: suspend () -> Unit, onShowJob: (Int) -> Unit) {
    val state = flow.state
    if (!state.showsDialog()) return
    val initialFocus = remember { FocusRequester() }

    val title: String
    val texts = ArrayList<String>()
    var depotLines: List<String> = emptyList()
    var notice = ""
    var error: String? = null
    var confirmLabel: String? = null
    var confirmEnabled = true
    var dismissLabel = RepairWording.CLOSE_LABEL
    var dismissEnabled = true
    var confirmIsShowJob = false
    // Which button takes focus when the dialog opens or changes meaning:
    // the non-destructive one on the confirm, "Show job" on the result.
    var focusDismiss = true
    when (state) {
        is RepairFlowState.Confirm, is RepairFlowState.Executing -> {
            val report = if (state is RepairFlowState.Confirm) state.report else (state as RepairFlowState.Executing).report
            val executing = state is RepairFlowState.Executing
            title = repairConfirmTitle(if (state is RepairFlowState.Confirm) state.title else (state as RepairFlowState.Executing).title)
            texts.add(repairPlanText(report))
            depotLines = report.depots.map(::repairDepotLine)
            notice = unreadableDepotsText(report)
            if (state is RepairFlowState.Confirm) error = state.error
            dismissLabel = RepairWording.CANCEL_LABEL
            confirmLabel = if (executing) RepairWording.EXECUTING_LABEL else RepairWording.CONFIRM_LABEL
            confirmEnabled = !executing
            dismissEnabled = !executing
        }
        is RepairFlowState.Blocked -> {
            title = repairBlockedTitle(state.title)
            texts.add(repairBlockedText(state.report))
        }
        is RepairFlowState.Done -> {
            val removed = state.report.removed_chunks > 0
            title = if (removed) RepairWording.DONE_TITLE else RepairWording.NOOP_TITLE
            texts.add(if (removed) repairDoneText(state.report) else RepairWording.NOOP_TEXT)
            if (state.report.job_id != null) {
                confirmLabel = RepairWording.SHOW_JOB_LABEL
                confirmIsShowJob = true
                focusDismiss = false
            }
        }
        else -> return
    }
    // Focus moves when the dialog opens and when confirm turns into the
    // result; executing and a failed execute keep it where it is.
    val focusKey = when (state) {
        is RepairFlowState.Done -> "done"
        is RepairFlowState.Blocked -> "blocked"
        else -> "confirm"
    }
    LaunchedEffect(focusKey) {
        try {
            initialFocus.requestFocus()
        } catch (_: IllegalStateException) {
            // Not attached yet (first frame): TalkBack starts on the title anyway.
        }
    }

    AlertDialog(
        onDismissRequest = { flow.close() },
        title = { Text(title) },
        text = {
            Column(
                modifier = Modifier.verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                for (t in texts) Text(t, style = MaterialTheme.typography.bodyMedium)
                for (line in depotLines) Text(line, style = MaterialTheme.typography.bodySmall)
                if (notice.isNotEmpty()) {
                    Text(notice, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                error?.let {
                    Text(
                        it,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error,
                        modifier = Modifier.semantics { liveRegion = LiveRegionMode.Assertive },
                    )
                }
            }
        },
        confirmButton = {
            confirmLabel?.let { label ->
                TextButton(
                    onClick = {
                        if (confirmIsShowJob) {
                            flow.showJob()?.let(onShowJob)
                        } else {
                            scope.launch { flow.confirm(onRefresh) }
                        }
                    },
                    enabled = confirmEnabled,
                    modifier = if (!focusDismiss) Modifier.focusRequester(initialFocus) else Modifier,
                ) {
                    // The destructive action in the error colour; disabled
                    // ("Repairing…") in the button's own disabled colour.
                    val tint = when {
                        !confirmEnabled -> Color.Unspecified
                        confirmIsShowJob -> MaterialTheme.colorScheme.primary
                        else -> MaterialTheme.colorScheme.error
                    }
                    Text(label, color = tint)
                }
            }
        },
        dismissButton = {
            TextButton(
                onClick = { flow.close() },
                enabled = dismissEnabled,
                modifier = if (focusDismiss) Modifier.focusRequester(initialFocus) else Modifier,
            ) { Text(dismissLabel) }
        },
    )
}
