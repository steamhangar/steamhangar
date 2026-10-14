package dev.steamvault.app.ui.library

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import dev.steamvault.app.R
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.ui.library.logic.BulkSelectionClassification
import dev.steamvault.app.ui.library.logic.GroupedLibrary
import dev.steamvault.app.ui.library.logic.appTitle
import dev.steamvault.app.ui.library.logic.describeGroupPlan
import dev.steamvault.app.ui.library.logic.expandSelection
import dev.steamvault.app.ui.library.logic.groupDeleteIntro
import dev.steamvault.app.ui.library.logic.groupHasCacheContent
import dev.steamvault.app.ui.library.logic.buildBulkDownloadPlan
import dev.steamvault.app.ui.library.logic.classifyBulkDeleteEligibility
import dev.steamvault.app.ui.library.logic.classifyBulkSelection
import dev.steamvault.app.ui.library.logic.formatBytesGB
import kotlinx.coroutines.CoroutineScope

/**
 * Multi-select bottom bar (WP 4b.4 brief: bulk download split + bulk
 * delete). Mirrors `web/js/views/library.js`'s `syncBulk`: the selected set
 * is resolved against [allGames] (the FULL merged library, not just what is
 * currently visible) so a selection survives a search/filter change, same
 * as the mockup's "selections survive a search" rule.
 */
@Composable
fun LibraryBulkBar(
    controller: LibraryController,
    allGames: List<GameSummary>,
    grouped: GroupedLibrary,
    scope: CoroutineScope,
) {
    val picked = remember(allGames, controller.selectedAppids) {
        allGames.filter { it.appid in controller.selectedAppids }
    }
    // WP TOOL-APPS-2d (ADR-0022 §4): a picked group card is its parent plus
    // every package, each classified exactly as it would be ungrouped, so
    // the targets equal those of the same rows picked one by one.
    val members = remember(picked, grouped) { expandSelection(picked, grouped) }
    val classification = remember(members, controller.jobs) { classifyBulkSelection(members, controller.jobs) }
    val plan = remember(classification, members.size) {
        buildBulkDownloadPlan(classification, members.size)
    }
    val deletable = remember(picked, grouped, controller.jobs, classification) {
        bulkDeletableCards(picked, grouped, controller.jobs, classification)
    }

    Column(modifier = Modifier.fillMaxWidth().padding(12.dp)) {
        Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text(
                pluralStringResource(
                    R.plurals.library_bulk_selected_count,
                    controller.selectedAppids.size,
                    controller.selectedAppids.size,
                ),
                style = MaterialTheme.typography.titleMedium,
            )
            TextButton(onClick = { controller.exitSelect() }) {
                Text(stringResource(R.string.library_bulk_cancel))
            }
        }
        if (plan.note.isNotEmpty()) {
            Text(
                text = plan.note,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Row(
            modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            if (deletable.isNotEmpty()) {
                OutlinedButton(onClick = {
                    controller.openDeleteConfirm(scope, deletable.map { it.appid })
                }) {
                    Text(stringResource(R.string.library_bulk_delete, deletable.size))
                }
            }
            if (plan.secondaryLabel != null) {
                OutlinedButton(onClick = { controller.startBulkDownload(scope, plan.secondaryTargets) }) {
                    Text(plan.secondaryLabel)
                }
            }
            Button(
                enabled = plan.primaryEnabled,
                onClick = { controller.startBulkDownload(scope, plan.primaryTargets) },
            ) {
                Text(plan.primaryLabel)
            }
        }
    }
}

/**
 * WP TOOL-APPS-2d: which picked CARDS the bulk delete offers. A plain card
 * as before ([classifyBulkDeleteEligibility]); a group card when any member
 * has bytes and no member is busy (the grouped delete answers 409
 * otherwise). Twin of web `syncBulk`'s TOOL-APPS-2c branch.
 */
internal fun bulkDeletableCards(
    picked: List<GameSummary>,
    grouped: GroupedLibrary,
    jobs: List<JobSummary>,
    classification: BulkSelectionClassification,
): List<GameSummary> {
    val busy = classification.busy.mapTo(HashSet()) { it.appid }
    val (groups, plain) = picked.partition { grouped.packagesOf(it.appid).isNotEmpty() }
    return classifyBulkDeleteEligibility(plain, jobs) + groups.filter { g ->
        val packages = grouped.packagesOf(g.appid)
        groupHasCacheContent(g, packages) && (listOf(g) + packages).none { it.appid in busy }
    }
}

/** Bulk-delete confirm dialog (WP 4b.4 brief: "bulk delete with set-aware
 * multiPlan arithmetic"). Mirrors `library.js`'s `openDeleteConfirm`/
 * `renderDeletePlan` three phases. WP TOOL-APPS-2d: a picked group card
 * shows the server's plan ([GroupDeletePlanBlock]) under the plain picks'
 * preview; Delete stays disabled while a group member has a job in flight.
 * @param allGames the merged (ungrouped) rows, for display names. */
@Composable
fun DeleteConfirmDialog(controller: LibraryController, allGames: List<GameSummary>, scope: CoroutineScope) {
    val state = controller.deletePlan
    val ids = when (state) {
        is DeletePlanUiState.Hidden -> emptyList()
        is DeletePlanUiState.Loading -> state.ids
        is DeletePlanUiState.Ready -> state.ids
        is DeletePlanUiState.Error -> state.ids
    }
    if (ids.isEmpty()) return

    AlertDialog(
        onDismissRequest = { controller.closeDeleteConfirm() },
        title = { Text(pluralStringResource(R.plurals.library_delete_title, ids.size, ids.size)) },
        text = {
            when (state) {
                is DeletePlanUiState.Loading -> Row(verticalAlignment = Alignment.CenterVertically) {
                    CircularProgressIndicator(modifier = Modifier.padding(end = 8.dp))
                    Text(stringResource(R.string.library_delete_calculating))
                }
                is DeletePlanUiState.Error -> Text(stringResource(R.string.library_delete_error, state.message))
                is DeletePlanUiState.Ready -> if (state.groupPlans.isEmpty()) {
                    DeletePlanBody(state)
                } else {
                    GroupedDeletePlanBody(state, allGames)
                }
                is DeletePlanUiState.Hidden -> {}
            }
        },
        confirmButton = {
            TextButton(
                enabled = state is DeletePlanUiState.Ready && !state.blocked,
                onClick = { controller.confirmDelete(scope, ids) },
            ) {
                Text(stringResource(R.string.library_delete_confirm))
            }
        },
        dismissButton = {
            TextButton(onClick = { controller.closeDeleteConfirm() }) {
                Text(stringResource(R.string.library_delete_cancel))
            }
        },
    )
}

/** WP TOOL-APPS-2d: the dialog body when the selection holds group cards --
 * the notice (re-confirm after a 409), the plain picks' preview, then one
 * [GroupDeletePlanBlock] per group. Scrolls: a group can list many apps. */
@Composable
private fun GroupedDeletePlanBody(state: DeletePlanUiState.Ready, allGames: List<GameSummary>) {
    val namesByAppid = remember(allGames) { allGames.associate { it.appid to it.name } }
    val nameFor: (Int, String?) -> String = { appid, serverName -> appTitle(appid, namesByAppid[appid], serverName) }
    Column(modifier = Modifier.heightIn(max = 420.dp).verticalScroll(rememberScrollState())) {
        state.notice?.let { notice ->
            Text(
                text = notice,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
                modifier = Modifier.padding(bottom = 6.dp),
            )
        }
        val first = state.groupPlans.first()
        Text(
            text = if (state.ids.size == 1) {
                groupDeleteIntro(describeGroupPlan(first, nameFor).subject, state.freedBytes)
            } else {
                "Deleting ${state.ids.size} games and their content packages frees about " +
                    "${formatBytesGB(state.freedBytes) ?: "0 GB"}."
            },
            style = MaterialTheme.typography.bodyMedium,
        )
        if (state.plan.rows.isNotEmpty()) DeletePlanBody(state)
        for (plan in state.groupPlans) {
            GroupDeletePlanBlock(plan = plan, nameFor = nameFor, showSubject = state.ids.size > 1)
        }
    }
}

@Composable
private fun DeletePlanBody(state: DeletePlanUiState.Ready) {
    val plan = state.plan
    Column {
        for (row in plan.sharedRows) {
            Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text(row.depotid.toString(), style = MaterialTheme.typography.bodySmall)
                Text(
                    text = if (row.free) {
                        stringResource(R.string.library_delete_row_freed)
                    } else {
                        stringResource(R.string.library_delete_row_kept)
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = if (row.free) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.error,
                )
                Text(formatBytesGB(row.sizeBytes) ?: "—", style = MaterialTheme.typography.bodySmall)
            }
        }
        Text(
            text = stringResource(
                R.string.library_delete_freed_kept,
                formatBytesGB(plan.freedBytes) ?: "0 GB",
                formatBytesGB(plan.keptBytes) ?: "0 GB",
            ),
            style = MaterialTheme.typography.bodyMedium,
            modifier = Modifier.padding(top = 8.dp),
        )
    }
}
