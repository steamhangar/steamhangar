package dev.steamvault.app.ui.library

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.steamvault.app.net.model.DeletePlanOut
import dev.steamvault.app.ui.library.logic.blockedText
import dev.steamvault.app.ui.library.logic.describeGroupPlan
import dev.steamvault.app.ui.library.logic.formatBytesGB
import dev.steamvault.app.ui.library.logic.keptChildrenText
import dev.steamvault.app.ui.library.logic.keptDepotWhy

/** Test tag of one grouped-delete plan block (WP TOOL-APPS-2d). */
internal const val GROUP_DELETE_PLAN_TAG = "group-delete-plan"

/**
 * The grouped-delete part of a delete confirmation (WP TOOL-APPS-2d,
 * ADR-0022 §4; twin of web `components/group-delete-plan.js`): every app
 * the delete removes (the game and each content package the server chose),
 * the bytes freed, the depots kept because another cached game uses them,
 * the children kept because they are not content packages, and -- when a
 * member has a job in flight -- why the delete is not possible right now.
 * Used by the bulk dialog ([DeleteConfirmDialog]) and the detail sheet.
 *
 * @param showSubject a heading with the game's name (a bulk dialog with
 *   several groups).
 */
@Composable
internal fun GroupDeletePlanBlock(
    plan: DeletePlanOut,
    nameFor: (Int, String?) -> String,
    showSubject: Boolean = false,
) {
    val d = describeGroupPlan(plan, nameFor)
    Column(modifier = Modifier.fillMaxWidth().padding(top = 8.dp).testTag(GROUP_DELETE_PLAN_TAG)) {
        if (showSubject) {
            Text(d.subject, style = MaterialTheme.typography.titleSmall)
        }
        Text(
            text = "Deleted (${1 + d.packages.size})",
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        for (member in listOf(d.parent) + d.packages) {
            Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text(
                    text = member.name,
                    style = MaterialTheme.typography.bodySmall,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
                Text(
                    text = if (member.role == "parent") "game" else "package",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(start = 8.dp),
                )
            }
        }
        if (d.keptDepots.isNotEmpty()) {
            val n = d.keptDepots.size
            Text(
                text = "Kept ($n shared ${if (n == 1) "depot" else "depots"})",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 6.dp),
            )
            for (kept in d.keptDepots) {
                Text(
                    text = "${kept.depotid} kept — ${keptDepotWhy(kept.usedBy)}",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                )
            }
        }
        if (d.keptChildren.isNotEmpty()) {
            Text(
                text = keptChildrenText(d.keptChildren),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 6.dp),
            )
        }
        Text(
            text = "${formatBytesGB(d.freedBytes) ?: "0 GB"} freed",
            style = MaterialTheme.typography.bodyMedium,
            modifier = Modifier.padding(top = 6.dp),
        )
        if (d.blocked.isNotEmpty()) {
            Text(
                text = blockedText(d.blocked),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
                modifier = Modifier.padding(top = 4.dp),
            )
        }
    }
}
