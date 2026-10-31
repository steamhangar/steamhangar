package dev.steamvault.app.ui.downloads

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.ui.downloads.logic.IncompleteWording
import dev.steamvault.app.ui.downloads.logic.UpdatesWording
import dev.steamvault.app.ui.downloads.logic.completeAllSpokenLabel
import dev.steamvault.app.ui.downloads.logic.completeNowSpokenLabel
import dev.steamvault.app.ui.downloads.logic.incompleteHeadingText
import dev.steamvault.app.ui.downloads.logic.incompletePctText
import dev.steamvault.app.ui.library.logic.RepairWording
import dev.steamvault.app.ui.library.logic.isCompletable
import dev.steamvault.app.ui.library.logic.repairNoteFor
import dev.steamvault.app.ui.library.logic.repairSpokenLabel
import dev.steamvault.app.ui.library.logic.showsRepair

/**
 * One row of the "Incomplete" section (WP CACHE-COMPLETE-1c): what the
 * screen needs to draw it, already resolved (name via `nameFor`, the
 * figure via [incompletePctText]). [completable] = "Verify" is offered;
 * otherwise the row is short only of damaged chunks: WP ANDROID-DL-2 (web
 * CACHE-REPAIR-UI-1) [repair] = "Repair" is offered (`showsRepair`), and
 * [note] says why it is Repair and not Verify, or why there is no repair
 * (`repairNoteFor`; "" for a completable row).
 */
data class IncompleteRowModel(
    val appid: Int,
    val name: String,
    val pctText: String,
    val completable: Boolean,
    val repair: Boolean = false,
    val note: String = "",
)

/** Builds an [IncompleteRowModel] for a `pendingIncomplete` row. */
fun incompleteRowModel(game: GameSummary, name: String, jobs: List<JobSummary> = emptyList()): IncompleteRowModel {
    val completable = isCompletable(game)
    return IncompleteRowModel(
        appid = game.appid,
        name = name,
        pctText = incompletePctText(game),
        completable = completable,
        repair = !completable && showsRepair(game, jobs),
        note = repairNoteFor(game),
    )
}

/**
 * The "Incomplete (N)" heading with "Complete all", plus the schedule note
 * (web `paintIncomplete`). The caller emits it only when the list is
 * non-empty (user rule: no empty Downloads sections). "Complete all" is
 * offered only while at least one row can be completed, and sends exactly
 * those rows.
 *
 * @param inFlight [DownloadsController.incompleteInFlight]: every button of
 *   the section is disabled while it is set; the pressed one reads
 *   "Starting…".
 */
@Composable
internal fun IncompleteHeader(
    rows: List<IncompleteRowModel>,
    note: String?,
    inFlight: Int?,
    onCompleteAll: (List<Int>) -> Unit,
) {
    val completable = rows.filter { it.completable }.map { it.appid }
    Column(modifier = Modifier.fillMaxWidth().padding(top = 8.dp)) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = incompleteHeadingText(rows.size),
                style = MaterialTheme.typography.titleSmall,
                modifier = Modifier.weight(1f).semantics { heading() },
            )
            if (completable.isNotEmpty()) {
                val starting = inFlight == COMPLETE_ALL
                val label = if (starting) UpdatesWording.STARTING else IncompleteWording.COMPLETE_ALL
                val spoken = if (starting) UpdatesWording.STARTING else completeAllSpokenLabel(completable.size)
                OutlinedButton(
                    onClick = { onCompleteAll(completable) },
                    enabled = inFlight == null,
                    modifier = Modifier.semantics { contentDescription = spoken },
                ) { Text(label) }
            }
        }
        if (note != null) {
            Text(
                text = note,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

/** One game in the section: name, figure, and "Verify" -- or, for a game
 * short only of damaged chunks, "Repair" (WP ANDROID-DL-2) with the note
 * why, or only the note when it cannot be repaired. While the row's dry run
 * is in flight its button reads "Checking…". */
@Composable
internal fun IncompleteRow(model: IncompleteRowModel, inFlight: Int?, onCompleteNow: () -> Unit, onRepair: () -> Unit = {}) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = model.name,
                style = MaterialTheme.typography.bodyMedium,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
            // Short only of wrong-size chunks: nothing a forced run fixes
            // (web `.urow .unote`); the note says why it is Repair.
            if (model.note.isNotEmpty()) {
                Text(
                    text = model.note,
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        Text(
            text = model.pctText,
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(horizontal = 8.dp),
        )
        if (model.completable) {
            val starting = inFlight == model.appid
            val spoken = if (starting) UpdatesWording.STARTING else completeNowSpokenLabel(model.name)
            Button(
                onClick = onCompleteNow,
                enabled = inFlight == null,
                modifier = Modifier.semantics { contentDescription = spoken },
            ) { Text(if (starting) UpdatesWording.STARTING else IncompleteWording.COMPLETE_NOW) }
        } else if (model.repair) {
            val checking = inFlight == model.appid
            val spoken = if (checking) RepairWording.CHECKING_LABEL else repairSpokenLabel(model.name)
            OutlinedButton(
                onClick = onRepair,
                enabled = inFlight == null,
                modifier = Modifier.semantics { contentDescription = spoken },
            ) { Text(if (checking) RepairWording.CHECKING_LABEL else RepairWording.LABEL) }
        }
    }
}
