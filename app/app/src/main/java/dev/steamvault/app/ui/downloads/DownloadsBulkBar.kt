package dev.steamvault.app.ui.downloads

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import dev.steamvault.app.ui.downloads.logic.BulkKind
import dev.steamvault.app.ui.downloads.logic.BulkWording
import dev.steamvault.app.ui.downloads.logic.bulkBarState
import dev.steamvault.app.ui.downloads.logic.pauseAllLabel
import dev.steamvault.app.ui.downloads.logic.pauseConfirmTitle
import dev.steamvault.app.ui.downloads.logic.resumeAllLabel
import kotlinx.coroutines.CoroutineScope

/**
 * Pause all / Resume all (WP WEB-FEAT-5) -- the Android twin of the bulk bar
 * in `web/js/views/downloads.js`. Decisions and wording come from
 * `logic/BulkJobs.kt`; this file only draws them. Shown above the Active
 * section while there is something to pause or resume.
 */
@Composable
fun DownloadsBulkBar(controller: DownloadsController, scope: CoroutineScope) {
    val bar = bulkBarState(controller.jobs)
    val busy = controller.bulkBusy
    if (!bar.visible && busy == null) return

    Column(
        modifier = Modifier.fillMaxWidth().semantics { contentDescription = BulkWording.GROUP_LABEL },
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (bar.pauseVisible || busy == BulkKind.PAUSE) {
                OutlinedButton(
                    onClick = { controller.requestPauseAll() },
                    enabled = busy == null,
                    modifier = Modifier.semantics {
                        contentDescription = if (busy == BulkKind.PAUSE) BulkWording.PAUSING else pauseAllLabel(bar.pauseCount)
                    },
                ) {
                    Text(if (busy == BulkKind.PAUSE) BulkWording.PAUSING else BulkWording.PAUSE_ALL)
                }
            }
            if (bar.resumeVisible || busy == BulkKind.RESUME) {
                Button(
                    onClick = { controller.resumeAll(scope) },
                    enabled = busy == null,
                    modifier = Modifier.semantics {
                        contentDescription =
                            if (busy == BulkKind.RESUME) BulkWording.RESUMING else resumeAllLabel(bar.resumeCount)
                    },
                ) {
                    Text(if (busy == BulkKind.RESUME) BulkWording.RESUMING else BulkWording.RESUME_ALL)
                }
            }
        }
        Text(
            text = BulkWording.SCHEDULER_NOTE,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

/** Pause all's confirm dialog; "Keep downloading" is the dismiss action. */
@Composable
fun PauseAllDialog(controller: DownloadsController, scope: CoroutineScope) {
    if (!controller.pauseAllConfirmOpen) return
    val bar = bulkBarState(controller.jobs)
    AlertDialog(
        onDismissRequest = { controller.dismissPauseAll() },
        title = { Text(pauseConfirmTitle(bar.pauseCount)) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(BulkWording.CONFIRM_BODY)
                if (bar.gcActive) Text(BulkWording.CONFIRM_GC_NOTE)
            }
        },
        confirmButton = {
            TextButton(onClick = { controller.confirmPauseAll(scope) }) { Text(BulkWording.CONFIRM_YES) }
        },
        dismissButton = {
            TextButton(onClick = { controller.dismissPauseAll() }) { Text(BulkWording.CONFIRM_NO) }
        },
    )
}
