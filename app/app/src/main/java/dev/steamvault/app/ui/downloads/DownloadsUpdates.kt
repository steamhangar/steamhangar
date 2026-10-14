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
import dev.steamvault.app.ui.downloads.logic.UpdatesWording
import dev.steamvault.app.ui.downloads.logic.updateAllSpokenLabel
import dev.steamvault.app.ui.downloads.logic.updateNowSpokenLabel
import dev.steamvault.app.ui.downloads.logic.updatesHeadingText

/**
 * One row of the "Updates available" section (WP UPD-6): what the screen
 * needs to draw it, already resolved (name via `nameFor`, size via
 * `formatBytesGB`, "—" when unknown -- web `sizeOfRow(game) || "—"`).
 */
data class UpdateRowModel(val appid: Int, val name: String, val sizeLabel: String)

/** Placeholder for an unknown size (web `buildUpdateRow`). */
internal const val UPDATE_SIZE_UNKNOWN = "—"

/**
 * The "Updates available (N)" heading with "Update all", plus the schedule
 * note (WP UPD-6, web `paintUpdates`). The caller emits it only when the
 * list is non-empty (user rule: no empty Downloads sections).
 *
 * @param inFlight [DownloadsController.updatesInFlight]: every update
 *   button is disabled while it is set; the pressed one reads "Starting…".
 */
@Composable
internal fun UpdatesHeader(
    rows: List<UpdateRowModel>,
    note: String?,
    inFlight: Int?,
    onUpdateAll: () -> Unit,
) {
    Column(modifier = Modifier.fillMaxWidth().padding(top = 8.dp)) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = updatesHeadingText(rows.size),
                style = MaterialTheme.typography.titleSmall,
                modifier = Modifier.weight(1f).semantics { heading() },
            )
            val label = if (inFlight == UPDATES_ALL) UpdatesWording.STARTING else UpdatesWording.UPDATE_ALL
            val spoken = if (inFlight == UPDATES_ALL) UpdatesWording.STARTING else updateAllSpokenLabel(rows.size)
            OutlinedButton(
                onClick = onUpdateAll,
                enabled = inFlight == null,
                modifier = Modifier.semantics { contentDescription = spoken },
            ) { Text(label) }
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

/** One game in the section: name, size, "Update now". */
@Composable
internal fun UpdateRow(model: UpdateRowModel, inFlight: Int?, onUpdateNow: () -> Unit) {
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
            text = model.sizeLabel,
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(horizontal = 8.dp),
        )
        val starting = inFlight == model.appid
        val spoken = if (starting) UpdatesWording.STARTING else updateNowSpokenLabel(model.name)
        Button(
            onClick = onUpdateNow,
            enabled = inFlight == null,
            modifier = Modifier.semantics { contentDescription = spoken },
        ) { Text(if (starting) UpdatesWording.STARTING else UpdatesWording.UPDATE_NOW) }
    }
}
