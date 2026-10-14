package dev.steamvault.app.ui.downloads

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
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
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.ui.downloads.logic.IncompleteWording
import dev.steamvault.app.ui.downloads.logic.UpdatesWording
import dev.steamvault.app.ui.downloads.logic.completeAllSpokenLabel
import dev.steamvault.app.ui.downloads.logic.completeNowSpokenLabel
import dev.steamvault.app.ui.downloads.logic.incompleteHeadingText
import dev.steamvault.app.ui.downloads.logic.incompletePctText
import dev.steamvault.app.ui.library.logic.CompletenessWording
import dev.steamvault.app.ui.library.logic.isCompletable

/**
 * One row of the "Incomplete" section (WP CACHE-COMPLETE-1c): what the
 * screen needs to draw it, already resolved (name via `nameFor`, the
 * figure via [incompletePctText]). [completable] = "Complete now" is
 * offered; otherwise the row is short only of damaged chunks and says so.
 */
data class IncompleteRowModel(val appid: Int, val name: String, val pctText: String, val completable: Boolean)

/** Builds an [IncompleteRowModel] for a `pendingIncomplete` row. */
fun incompleteRowModel(game: GameSummary, name: String): IncompleteRowModel =
    IncompleteRowModel(appid = game.appid, name = name, pctText = incompletePctText(game), completable = isCompletable(game))

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

/** One game in the section: name, figure, and "Complete now" -- or, for a
 * game short only of damaged chunks, the note why there is none. */
@Composable
internal fun IncompleteRow(model: IncompleteRowModel, inFlight: Int?, onCompleteNow: () -> Unit) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = model.name,
            style = MaterialTheme.typography.bodyMedium,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.weight(1f),
        )
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
        } else {
            // Short only of wrong-size chunks: nothing a forced run fixes
            // (web `.urow .unote`).
            Text(
                text = CompletenessWording.DAMAGED_NOTE,
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                textAlign = TextAlign.End,
                modifier = Modifier.widthIn(max = DAMAGED_NOTE_MAX_WIDTH),
            )
        }
    }
}

/** The damaged note's widest extent in a row (web `.unote{max-width:55%}`,
 * here a fixed width so the name keeps its room on a phone). */
internal val DAMAGED_NOTE_MAX_WIDTH = 170.dp
