package dev.steamvault.app.ui.downloads

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import dev.steamvault.app.R
import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.HistoryDeleteOut
import dev.steamvault.app.repo.HistoryRepository
import dev.steamvault.app.ui.downloads.logic.HISTORY_DELETE_KEEPS
import dev.steamvault.app.ui.downloads.logic.HISTORY_DELETE_LEGEND
import dev.steamvault.app.ui.downloads.logic.HISTORY_DELETE_OPTIONS
import dev.steamvault.app.ui.downloads.logic.HISTORY_DELETE_TITLE
import dev.steamvault.app.ui.downloads.logic.HISTORY_DELETE_WHAT
import dev.steamvault.app.ui.downloads.logic.SCOPE_OLDER
import dev.steamvault.app.ui.downloads.logic.historyDeleteButtonText
import dev.steamvault.app.ui.downloads.logic.historyDeleteErrorText
import dev.steamvault.app.ui.downloads.logic.isHistoryScope
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch

/**
 * "Delete history" (WP ANDROID-GAPS-1, twin of web `views/downloads.js`'s
 * history-delete dialog, WP D15-NAMES): state plus the one request. Words
 * and decisions are `ui/downloads/logic/HistoryDelete.kt`'s.
 *
 * Every open starts on the safe variant ("Older than 30 days"). The
 * dialog stays open on a failure and shows the reason (a 404 means the
 * server is too old); "Keep", back and the scrim wait while the request
 * runs. Kept local to this file so the Downloads data path (ANDROID-DL-1)
 * is not touched.
 */
class HistoryDeleteFlow(private val repository: HistoryRepository) {
    var isOpen by mutableStateOf(false)
        private set
    var scope by mutableStateOf(SCOPE_OLDER)
    var busy by mutableStateOf(false)
        private set
    var error by mutableStateOf<String?>(null)
        private set

    fun open() {
        if (isOpen || busy) return
        scope = SCOPE_OLDER
        error = null
        isOpen = true
    }

    fun cancel() {
        if (!busy) isOpen = false
    }

    /** `DELETE /v1/history?scope=`; the server's answer on success (the
     * dialog is closed), `null` on a failure (the dialog shows why). */
    suspend fun confirm(): HistoryDeleteOut? {
        if (busy || !isOpen) return null
        val chosen = if (isHistoryScope(scope)) scope else SCOPE_OLDER
        error = null
        busy = true
        val result = try {
            repository.delete(chosen)
        } catch (e: VaultApiError) {
            busy = false
            error = historyDeleteErrorText(e)
            return null
        }
        busy = false
        isOpen = false
        return result
    }
}

/** The History heading's "Delete" action (TalkBack: "Delete history…",
 * the web button's aria-label). */
@Composable
fun HistoryDeleteButton(flow: HistoryDeleteFlow) {
    val label = stringResource(R.string.downloads_history_delete_button_cd)
    Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
        TextButton(onClick = { flow.open() }, modifier = Modifier.semantics { contentDescription = label }) {
            Text(stringResource(R.string.downloads_history_delete_button))
        }
    }
}

/**
 * The confirm: title, what is deleted, the two variants as a radio group
 * (safest first and preselected), what is kept, the error line (read out
 * when it appears, web role=alert), "Keep" and the destructive button
 * named after the chosen variant.
 *
 * @param scope the SCREEN's scope: the dialog leaves composition the
 *   moment the request succeeds, and [onDeleted]'s re-reads must outlive it.
 * @param onDeleted called with the server's answer after a success (the
 *   screen shows the note and re-reads jobs, history and statistics).
 */
@Composable
fun HistoryDeleteDialog(flow: HistoryDeleteFlow, scope: CoroutineScope, onDeleted: suspend (HistoryDeleteOut) -> Unit) {
    if (!flow.isOpen) return
    AlertDialog(
        onDismissRequest = { flow.cancel() },
        title = { Text(HISTORY_DELETE_TITLE) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(HISTORY_DELETE_WHAT, style = MaterialTheme.typography.bodyMedium)
                Text(
                    HISTORY_DELETE_LEGEND,
                    style = MaterialTheme.typography.titleSmall,
                    modifier = Modifier.semantics { heading() },
                )
                Column(modifier = Modifier.selectableGroup()) {
                    for (option in HISTORY_DELETE_OPTIONS) {
                        val selected = flow.scope == option.value
                        Row(
                            modifier = Modifier
                                .fillMaxWidth()
                                .selectable(selected = selected, role = Role.RadioButton, onClick = { if (!flow.busy) flow.scope = option.value })
                                .padding(vertical = 4.dp),
                            verticalAlignment = Alignment.Top,
                        ) {
                            RadioButton(selected = selected, onClick = null)
                            Column(modifier = Modifier.padding(start = 8.dp)) {
                                Text(option.label, style = MaterialTheme.typography.bodyMedium)
                                Text(
                                    option.desc,
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                        }
                    }
                }
                Text(HISTORY_DELETE_KEEPS, style = MaterialTheme.typography.bodyMedium)
                flow.error?.let { message ->
                    Text(
                        message,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error,
                        modifier = Modifier.semantics { liveRegion = LiveRegionMode.Assertive },
                    )
                }
            }
        },
        confirmButton = {
            TextButton(
                onClick = {
                    scope.launch {
                        val result = flow.confirm()
                        if (result != null) onDeleted(result)
                    }
                },
            ) {
                Text(
                    if (flow.busy) stringResource(R.string.downloads_history_delete_busy) else historyDeleteButtonText(flow.scope),
                    color = MaterialTheme.colorScheme.error,
                )
            }
        },
        dismissButton = {
            TextButton(onClick = { flow.cancel() }) { Text(stringResource(R.string.downloads_history_delete_keep)) }
        },
    )
}
