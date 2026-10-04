package dev.steamvault.app.ui.detail

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Snackbar
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import dev.steamvault.app.R
import dev.steamvault.app.net.model.GameDetail
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.ui.demo.DemoModeBanner
import dev.steamvault.app.ui.detail.logic.ConfirmedCurrentWording
import dev.steamvault.app.ui.detail.logic.CoOwnerRow
import dev.steamvault.app.ui.detail.logic.DepotPresentation
import dev.steamvault.app.ui.detail.logic.DepotShareTag
import dev.steamvault.app.ui.detail.logic.DetailJobAction
import dev.steamvault.app.ui.detail.logic.confirmedCurrentWording
import dev.steamvault.app.ui.detail.logic.GcFlowState
import dev.steamvault.app.ui.detail.logic.GcLogSummary
import dev.steamvault.app.ui.detail.logic.buildDepotPresentation
import dev.steamvault.app.ui.detail.logic.detailJobActions
import dev.steamvault.app.ui.detail.logic.findTrackedJob
import dev.steamvault.app.ui.library.CoverArtImage
import dev.steamvault.app.ui.library.installedBadgeText
import dev.steamvault.app.ui.library.logic.InstalledBadge
import dev.steamvault.app.ui.library.logic.MultiPlan
import dev.steamvault.app.ui.library.logic.StatusActionType
import dev.steamvault.app.ui.library.logic.TOOL_APP_NOTE
import dev.steamvault.app.ui.library.logic.TOOL_APP_STATE_WORD
import dev.steamvault.app.ui.library.logic.buildMultiPlan
import dev.steamvault.app.ui.library.logic.coverArtUrl
import dev.steamvault.app.ui.library.logic.dispKind
import dev.steamvault.app.ui.library.logic.fallbackHues
import dev.steamvault.app.ui.library.logic.formatBytesGB
import dev.steamvault.app.ui.library.logic.hasProtectedCacheContent
import dev.steamvault.app.ui.library.logic.hasVisibleCacheContent
import dev.steamvault.app.ui.library.logic.installedBadgeFor
import dev.steamvault.app.ui.library.logic.isToolApp
import dev.steamvault.app.ui.library.logic.statusAction
import dev.steamvault.app.ui.status.StatusIcon
import dev.steamvault.app.ui.status.StatusIconSize
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.delay

/**
 * The game detail sheet (WP 4b.6 brief): cover/name/status, sizes, the
 * honest last-download/confirmed-current wording, per-depot sharing,
 * download/pause/resume/cancel, delete with a per-depot freed/kept preview,
 * and the dry-run -> confirm -> execute GC flow. State/orchestration lives
 * in [DetailController] (kept thin, same `LibraryController`/
 * `DownloadsController` precedent) -- this file is rendering only.
 *
 * **Compose `ModalBottomSheet`, justified (WP brief: "or equivalent,
 * justify").** `web/js/components/sheet-dialog.js` hand-rolls
 * `role="dialog"` + `aria-modal` + focus-on-open + Escape-to-close +
 * backdrop-tap-to-close for the same class of transient surface on that
 * frontend. `ModalBottomSheet` gets the Android-native equivalent of every
 * one of those for free, and more reliably: it is hosted in its own
 * platform `Dialog`/window, so TalkBack announces a new pane and moves
 * focus into it automatically, the system back gesture dismisses it without
 * any app-level key listener, and tapping the scrim calls [onDismissRequest]
 * -- wired to [DetailController.close] below -- exactly like the backdrop
 * click the web version wires up by hand. A real focus TRAP (Tab-order
 * wrapping) is out of scope here too, same deferral `sheet-dialog.js`'s
 * kdoc records for web (full trap deferred past this WP on both frontends)
 * -- Android's own focus system does not need one for a single-window modal
 * the way a DOM overlay does. What this file adds on top: `heading()`
 * semantics on the game name (the sheet's accessible title) and an explicit
 * `contentDescription` on every icon-only control (the expand/collapse
 * chevrons), matching the pattern `ui/downloads/DownloadsScreen.kt`'s
 * `HistoryRow` already uses.
 *
 * **Depot sharing is computed, never stored, live (mockup-notes.md round
 * 3).** The sharing plan/presentation is derived INLINE in this composable
 * using [games]/[jobs] as passed in by the caller every recomposition --
 * when those update from `LibraryController`'s own poll ticks while the
 * sheet stays open, the sharing wording recomputes on the next frame with
 * no extra network call (see [DetailController]'s kdoc).
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun GameDetailSheet(
    controller: DetailController,
    games: List<GameSummary>,
    jobs: List<JobSummary>,
    scope: CoroutineScope,
    onLibraryChanged: () -> Unit,
    demoMode: Boolean,
) {
    val appid = controller.openAppid ?: return
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)

    ModalBottomSheet(
        onDismissRequest = { controller.close() },
        sheetState = sheetState,
    ) {
        // WP APP-DEMO review round 3 (F2): the banner is called directly
        // here, in THIS top-level public composable, rather than delegated
        // into GameDetailSheetBody -- ModalBottomSheet's `content` lambda
        // is typed `ColumnScope.() -> Unit` (M3 already wraps it in a
        // non-scrolling Column), so this banner call and the
        // GameDetailSheetBody() call below are siblings in THAT Column,
        // with no extra wrapper Column of this file's own needed. This
        // also closes the "extract a tiny DemoBannerRow-style helper and
        // call it from inside the scrolling body" regression the reviewer
        // demonstrated against round 2's version: there is no helper for
        // that refactor to hide behind, because the real call site IS the
        // screen's own top-level function.
        if (demoMode) DemoModeBanner()
        GameDetailSheetBody(
            controller = controller,
            appid = appid,
            games = games,
            jobs = jobs,
            scope = scope,
            onLibraryChanged = onLibraryChanged,
        )
    }

    val detail = controller.detail
    if (controller.showDeleteConfirm && detail != null) {
        val gamesByAppid = remember(games) { games.associateBy { it.appid } }
        val activeAppids = remember(jobs) { activeJobAppids(jobs) }
        val plan = remember(detail, controller.mapping, gamesByAppid, activeAppids) {
            buildMultiPlan(listOf(appid), listOf(detail), controller.mapping, gamesByAppid, activeAppids)
        }
        DetailDeleteConfirmDialog(
            plan = plan,
            onConfirm = { controller.confirmDelete(scope, onLibraryChanged) },
            onDismiss = { controller.closeDeleteConfirm() },
        )
    }

    val gcState = controller.gcState
    if (gcState is GcFlowState.ConfirmExecute) {
        GcExecuteConfirmDialog(
            summary = gcState.summary,
            onConfirm = { controller.confirmGcExecute(scope) },
            onDismiss = { controller.dismissGcExecuteConfirm() },
        )
    }
}

/** Appids with a prefill job that is queued/running/paused right now --
 * same active-status set `ui/library/logic/BulkPlan.kt`'s (private)
 * `busyAppidsFromJobs` uses, duplicated here for the same reason
 * `ui/detail/logic/DetailJob.kt`'s kdoc gives for its own second copy of a
 * similar helper: this module needs it standalone, not exported from a
 * screen it otherwise has no dependency on. */
private fun activeJobAppids(jobs: List<JobSummary>): Set<Int> =
    jobs.filter { it.type == "prefill" && it.status in setOf("queued", "running", "paused") }
        .mapTo(HashSet()) { it.appid }

/** `GameDetail` -> `GameSummary` for the status-logic helpers
 * ([dispKind]/[statusAction]/[hasVisibleCacheContent]/[hasProtectedCacheContent]),
 * which all take the LIST shape -- the two carry the same status fields
 * (`GameDetail` is the list shape plus `depots`), so this is a lossless
 * field copy, not an approximation. */
private fun gameSummaryFrom(detail: GameDetail): GameSummary = GameSummary(
    appid = detail.appid,
    name = detail.name,
    status = detail.status,
    last_prefill_at = detail.last_prefill_at,
    last_manifest_check = detail.last_manifest_check,
    depot_count = detail.depots.size,
    size_bytes = detail.size_bytes,
    needs_force = detail.needs_force,
    installed_on = detail.installed_on,
    tool_app = detail.tool_app,
    tool_app_name = detail.tool_app_name,
)

@Composable
private fun GameDetailSheetBody(
    controller: DetailController,
    appid: Int,
    games: List<GameSummary>,
    jobs: List<JobSummary>,
    scope: CoroutineScope,
    onLibraryChanged: () -> Unit,
) {
    val name = controller.detail?.name?.takeIf { it.isNotBlank() }
        // WP API-FIX-4: a Steam tool app's name comes from the server's list.
        ?: controller.detail?.takeIf { it.tool_app }?.tool_app_name?.takeIf { it.isNotBlank() }
        ?: controller.openName?.takeIf { it.isNotBlank() }
        ?: "App $appid"

    // WP APP-DEMO review round 3 (F2): no wrapper Column of this file's
    // own -- the banner (WP brief constraint 1) now lives one level up, in
    // GameDetailSheet's own ModalBottomSheet content lambda, which is
    // already a non-scrolling ColumnScope (see that call site's kdoc).
    // This function stays exactly what it was before demo mode touched it:
    // one scrolling Column.
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 8.dp)
            .padding(bottom = 24.dp),
    ) {
        DetailHeader(appid = appid, name = name, detail = controller.detail, jobs = jobs)
        Spacer(Modifier.height(12.dp))

        when {
            controller.notTracked -> NotTrackedBody(controller = controller, scope = scope)
            controller.loadError != null -> Text(
                text = stringResource(R.string.detail_load_error, controller.loadError ?: ""),
                color = MaterialTheme.colorScheme.error,
                style = MaterialTheme.typography.bodyMedium,
            )
            controller.detail == null -> Row(verticalAlignment = Alignment.CenterVertically) {
                CircularProgressIndicator(modifier = Modifier.padding(end = 8.dp))
            }
            else -> LoadedDetailBody(
                controller = controller,
                appid = appid,
                detail = controller.detail!!,
                games = games,
                jobs = jobs,
                scope = scope,
            )
        }

        controller.toast?.let { message ->
            LaunchedEffect(message) {
                delay(2500)
                controller.dismissToast()
            }
            Spacer(Modifier.height(8.dp))
            Snackbar { Text(message) }
        }
    }
}

@Composable
private fun DetailHeader(appid: Int, name: String, detail: GameDetail?, jobs: List<JobSummary>) {
    Row(verticalAlignment = Alignment.Top) {
        CoverArtImage(
            coverUrl = coverArtUrl(appid),
            name = name,
            fallbackHues = fallbackHues(appid),
            modifier = Modifier.size(width = 64.dp, height = 96.dp),
        )
        Column(modifier = Modifier.padding(start = 12.dp)) {
            Text(
                text = name,
                style = MaterialTheme.typography.titleLarge,
                modifier = Modifier.semantics(mergeDescendants = true) { heading() },
            )
            if (detail != null) {
                val liveJob = findTrackedJob(jobs, appid)
                val kind = dispKind(gameSummaryFrom(detail), liveJob)
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.padding(top = 4.dp),
                ) {
                    StatusIcon(kind = kind, size = StatusIconSize.SMALL)
                    Text(
                        // WP API-FIX-4: a tool app reads "Steam tool package".
                        text = if (detail.tool_app) TOOL_APP_STATE_WORD else stringResource(kind.labelRes),
                        style = MaterialTheme.typography.bodyMedium,
                        modifier = Modifier.padding(start = 6.dp),
                    )
                }
                Text(
                    text = formatBytesGB(detail.size_bytes) ?: stringResource(R.string.detail_depot_size_unknown),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

@Composable
private fun NotTrackedBody(controller: DetailController, scope: CoroutineScope) {
    Text(
        text = stringResource(R.string.detail_not_tracked_title),
        style = MaterialTheme.typography.titleMedium,
    )
    Text(
        text = stringResource(R.string.detail_not_tracked_body),
        style = MaterialTheme.typography.bodyMedium,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(top = 4.dp, bottom = 12.dp),
    )
    Button(onClick = { controller.startDownload(scope) }) {
        Text(stringResource(R.string.detail_action_download))
    }
}

@Composable
private fun LoadedDetailBody(
    controller: DetailController,
    appid: Int,
    detail: GameDetail,
    games: List<GameSummary>,
    jobs: List<JobSummary>,
    scope: CoroutineScope,
) {
    val gameSummary = remember(games, detail) {
        games.firstOrNull { it.appid == appid } ?: gameSummaryFrom(detail)
    }

    // ---- Steam tool app note (WP API-FIX-4) -----------------------------------
    // Where a game offers Download/Retry, a tool app says why it has none
    // (web buildToolAppNote). statusAction is null for it, so no button below.
    if (isToolApp(gameSummary)) {
        Text(
            text = TOOL_APP_NOTE,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(bottom = 8.dp),
        )
    }

    // ---- last download / confirmed current ---------------------------------
    Text(
        text = detail.last_prefill_at?.let { stringResource(R.string.detail_last_download, it) }
            ?: stringResource(R.string.detail_never_downloaded),
        style = MaterialTheme.typography.bodySmall,
    )
    Text(
        text = when (confirmedCurrentWording(detail.last_prefill_at, detail.last_manifest_check)) {
            ConfirmedCurrentWording.NEVER_CONFIRMED -> stringResource(R.string.detail_not_confirmed_current)
            ConfirmedCurrentWording.CONFIRMED ->
                stringResource(R.string.detail_confirmed_current, detail.last_manifest_check!!)
            ConfirmedCurrentWording.CONFIRMED_BEFORE_CACHE_CLEARED ->
                stringResource(R.string.detail_confirmed_current_before_cleared, detail.last_manifest_check!!)
        },
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
    if (detail.needs_force && !detail.tool_app) { // WP API-FIX-4: no "next download" for a tool app
        Text(
            text = stringResource(R.string.detail_needs_force_note),
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.error,
            modifier = Modifier.padding(top = 2.dp),
        )
    }

    // ---- installed-state (WP AG-3) -------------------------------------------
    val installedBadge = installedBadgeFor(gameSummary)
    installedBadgeText(installedBadge, includeTimestamp = true)?.let { text ->
        Text(
            text = text,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(top = 4.dp),
        )
    }
    if (installedBadge is InstalledBadge.InstalledNotCached) {
        // The explicit statement the whole `installed_on` field exists for:
        // a game a gaming PC currently has installed, which this vault is
        // NOT protecting (api/README.md "Installed state per app").
        Text(
            text = stringResource(R.string.detail_installed_not_cached_warning, installedBadge.display.primaryClientId),
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.error,
            modifier = Modifier.padding(top = 2.dp),
        )
    }

    HorizontalDivider(modifier = Modifier.padding(vertical = 12.dp))

    // ---- job control / download trigger -------------------------------------
    val trackedJob = findTrackedJob(jobs, appid)
    if (trackedJob != null) {
        JobControlRow(
            job = trackedJob,
            onPause = { controller.pauseJob(scope, trackedJob.id) },
            onResume = { controller.resumeJob(scope, trackedJob.id) },
            onCancel = { controller.cancelJob(scope, trackedJob.id) },
        )
    } else {
        val action = statusAction(gameSummary, null, selecting = false)
        if (action != null) {
            val labelRes = if (action.type == StatusActionType.RETRY) {
                R.string.detail_action_retry
            } else {
                R.string.detail_action_download
            }
            Button(onClick = { controller.startDownload(scope) }) {
                Text(stringResource(labelRes))
            }
        }
    }

    HorizontalDivider(modifier = Modifier.padding(vertical = 12.dp))

    // ---- depots ---------------------------------------------------------------
    if (detail.depots.isEmpty()) {
        Text(
            text = stringResource(R.string.detail_depots_unknown),
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    } else {
        val gamesByAppid = remember(games) { games.associateBy { it.appid } }
        val activeAppids = remember(jobs) { activeJobAppids(jobs) }
        val plan = remember(detail, controller.mapping, gamesByAppid, activeAppids) {
            buildMultiPlan(listOf(appid), listOf(detail), controller.mapping, gamesByAppid, activeAppids)
        }
        val thisAppIsHolder = remember(gameSummary, activeAppids) {
            hasProtectedCacheContent(gameSummary, appid in activeAppids)
        }
        val presentations = remember(plan, gamesByAppid, thisAppIsHolder) {
            plan.rows.map { buildDepotPresentation(it, gamesByAppid, thisAppIsHolder) }
        }
        Text(text = stringResource(R.string.detail_depots_heading), style = MaterialTheme.typography.titleSmall)
        for (row in presentations) {
            DepotRow(row)
        }
    }

    HorizontalDivider(modifier = Modifier.padding(vertical = 12.dp))

    // ---- delete -----------------------------------------------------------------
    if (hasVisibleCacheContent(gameSummary)) {
        OutlinedButton(onClick = { controller.openDeleteConfirm() }) {
            Text(stringResource(R.string.detail_action_delete))
        }
        Spacer(Modifier.height(8.dp))
    }

    // ---- garbage collection -------------------------------------------------------
    if (detail.depots.isNotEmpty()) {
        GcSection(controller = controller, scope = scope)
    }
}

@Composable
private fun JobControlRow(
    job: JobSummary,
    onPause: () -> Unit,
    onResume: () -> Unit,
    onCancel: () -> Unit,
) {
    val actions = detailJobActions(job)
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        if (DetailJobAction.RESUME in actions) {
            Button(onClick = onResume) { Text(stringResource(R.string.downloads_action_resume)) }
        }
        if (DetailJobAction.PAUSE in actions) {
            OutlinedButton(onClick = onPause) { Text(stringResource(R.string.downloads_action_pause)) }
        }
        if (DetailJobAction.CANCEL in actions) {
            OutlinedButton(onClick = onCancel) { Text(stringResource(R.string.downloads_action_cancel)) }
        }
    }
}

@Composable
private fun DepotRow(presentation: DepotPresentation) {
    var expanded by remember(presentation.depotid) { mutableStateOf(false) }

    Column(modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(presentation.depotid.toString(), style = MaterialTheme.typography.bodyMedium)
                depotTagLabel(presentation.tag)?.let { tag ->
                    Text(tag, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            Text(
                text = formatBytesGB(presentation.sizeBytes) ?: stringResource(R.string.detail_depot_size_unknown),
                style = MaterialTheme.typography.bodyMedium,
            )
            if (presentation.coOwners.isNotEmpty()) {
                val expandDescription = if (expanded) {
                    stringResource(R.string.detail_depot_collapse)
                } else {
                    stringResource(R.string.detail_depot_expand)
                }
                IconButton(
                    onClick = { expanded = !expanded },
                    modifier = Modifier.semantics { contentDescription = expandDescription },
                ) {
                    Text(if (expanded) "▲" else "▼")
                }
            }
        }

        if (presentation.coOwners.isNotEmpty()) {
            depotNoteLabel(presentation.tag)?.let { note ->
                Text(
                    text = note,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 2.dp),
                )
            }
            if (expanded) {
                Column(modifier = Modifier.padding(start = 12.dp, top = 4.dp)) {
                    for (coOwner in presentation.coOwners) {
                        CoOwnerLine(coOwner)
                    }
                }
            }
        }
    }
}

@Composable
private fun depotTagLabel(tag: DepotShareTag): String? = when (tag) {
    DepotShareTag.EXCLUSIVE -> null
    DepotShareTag.SOLE_HOLDER -> stringResource(R.string.detail_depot_tag_sole_holder)
    DepotShareTag.PROTECTED -> stringResource(R.string.detail_depot_tag_shared)
    DepotShareTag.ORPHANED -> stringResource(R.string.detail_depot_tag_orphaned)
}

@Composable
private fun depotNoteLabel(tag: DepotShareTag): String? = when (tag) {
    DepotShareTag.EXCLUSIVE -> null
    DepotShareTag.SOLE_HOLDER -> stringResource(R.string.detail_depot_note_sole_holder)
    DepotShareTag.PROTECTED -> stringResource(R.string.detail_depot_note_protected)
    DepotShareTag.ORPHANED -> stringResource(R.string.detail_depot_note_orphaned)
}

@Composable
private fun CoOwnerLine(coOwner: CoOwnerRow) {
    Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        Text(coOwner.name, style = MaterialTheme.typography.bodySmall)
        Text(
            text = if (coOwner.cached) {
                stringResource(R.string.detail_depot_coowner_cached)
            } else {
                stringResource(R.string.detail_depot_coowner_not_cached)
            },
            style = MaterialTheme.typography.bodySmall,
            color = if (coOwner.cached) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

/** Single-game delete confirm -- literally [MultiPlan] for one id (the
 * caller builds it with `listOf(appid)`), rendered with the SAME row/total
 * wording `LibraryBulkBar.kt`'s `DeletePlanBody` uses (shared
 * `library_delete_*` string resources) so bulk and single-game delete never
 * describe the same arithmetic two different ways. */
@Composable
private fun DetailDeleteConfirmDialog(
    plan: MultiPlan,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(pluralStringResource(R.plurals.library_delete_title, 1, 1)) },
        text = {
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
        },
        confirmButton = { TextButton(onClick = onConfirm) { Text(stringResource(R.string.library_delete_confirm)) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.library_delete_cancel)) } },
    )
}

// ---- garbage collection --------------------------------------------------------

@Composable
private fun GcSection(controller: DetailController, scope: CoroutineScope) {
    Column(modifier = Modifier.padding(top = 4.dp)) {
        Text(text = stringResource(R.string.detail_gc_heading), style = MaterialTheme.typography.titleSmall)
        when (val state = controller.gcState) {
            is GcFlowState.Idle ->
                OutlinedButton(onClick = { controller.startGcDryRun(scope) }) {
                    Text(stringResource(R.string.detail_action_gc))
                }

            is GcFlowState.Cancelled -> {
                Text(
                    text = stringResource(R.string.detail_gc_cancelled),
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                OutlinedButton(
                    onClick = { controller.startGcDryRun(scope) },
                    modifier = Modifier.padding(top = 8.dp),
                ) {
                    Text(stringResource(R.string.detail_action_gc))
                }
            }

            is GcFlowState.RequestingDryRun, is GcFlowState.PollingDryRun ->
                Row(verticalAlignment = Alignment.CenterVertically) {
                    CircularProgressIndicator(modifier = Modifier.padding(end = 8.dp).size(16.dp))
                    Text(stringResource(R.string.detail_action_gc_running), style = MaterialTheme.typography.bodyMedium)
                }

            is GcFlowState.DryRunPlan, is GcFlowState.ConfirmExecute -> {
                val (jobId, log, summary) = when (state) {
                    is GcFlowState.DryRunPlan -> Triple(state.jobId, state.job.log_excerpt, state.summary)
                    is GcFlowState.ConfirmExecute -> Triple(state.jobId, state.job.log_excerpt, state.summary)
                    else -> error("unreachable")
                }
                GcPlanBody(jobId = jobId, log = log, summary = summary, dryRun = true)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(top = 8.dp)) {
                    Button(
                        onClick = { controller.requestGcExecute() },
                        enabled = (summary?.wouldDeleteCount ?: 1) != 0,
                    ) {
                        Text(stringResource(R.string.detail_action_gc_execute))
                    }
                    OutlinedButton(onClick = { controller.restartGcDryRun(scope) }) {
                        Text(stringResource(R.string.detail_action_gc_again))
                    }
                }
            }

            is GcFlowState.RequestingExecute, is GcFlowState.PollingExecute ->
                Row(verticalAlignment = Alignment.CenterVertically) {
                    CircularProgressIndicator(modifier = Modifier.padding(end = 8.dp).size(16.dp))
                    Text(stringResource(R.string.detail_action_gc_executing), style = MaterialTheme.typography.bodyMedium)
                }

            is GcFlowState.ExecuteDone -> {
                GcPlanBody(jobId = state.jobId, log = state.job.log_excerpt, summary = state.summary, dryRun = false)
                OutlinedButton(onClick = { controller.restartGcDryRun(scope) }, modifier = Modifier.padding(top = 8.dp)) {
                    Text(stringResource(R.string.detail_action_gc_again))
                }
            }

            is GcFlowState.Error -> {
                Text(
                    text = stringResource(R.string.detail_gc_error, state.message),
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodyMedium,
                )
                OutlinedButton(onClick = { controller.restartGcDryRun(scope) }, modifier = Modifier.padding(top = 8.dp)) {
                    Text(stringResource(R.string.detail_action_gc_again))
                }
            }
        }
    }
}

@Composable
private fun GcPlanBody(jobId: Int, log: String?, summary: GcLogSummary?, dryRun: Boolean) {
    Column(modifier = Modifier.padding(top = 4.dp)) {
        val headline = summary?.let {
            if (dryRun) {
                val count = it.wouldDeleteCount ?: 0
                if (count == 0) {
                    stringResource(R.string.detail_gc_dry_run_none)
                } else {
                    pluralStringResource(R.plurals.detail_gc_would_delete, count, formatBytesGB(it.wouldDeleteBytes) ?: "0 GB", count)
                }
            } else {
                val count = it.chunksRemoved ?: 0
                pluralStringResource(
                    R.plurals.detail_gc_executed,
                    count,
                    formatBytesGB(it.totalBytesFreed ?: it.bytesFreed) ?: "0 GB",
                    count,
                )
            }
        }
        headline?.let { Text(it, style = MaterialTheme.typography.bodyMedium) }
        val heldBackCount = summary?.heldBackCount ?: 0
        if (heldBackCount > 0) {
            Text(
                text = pluralStringResource(
                    R.plurals.detail_gc_held_back,
                    heldBackCount,
                    formatBytesGB(summary?.heldBackBytes) ?: "0 GB",
                    heldBackCount,
                ),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        if (!log.isNullOrBlank()) {
            Text(
                text = stringResource(R.string.detail_gc_log_heading, jobId),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 6.dp),
            )
            Box(modifier = Modifier.fillMaxWidth()) {
                Text(log, style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}

@Composable
private fun GcExecuteConfirmDialog(
    summary: GcLogSummary?,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(stringResource(R.string.detail_gc_confirm_title)) },
        text = {
            val bytes = summary?.wouldDeleteBytes
            val count = summary?.wouldDeleteCount
            Text(
                if (bytes != null && count != null) {
                    pluralStringResource(R.plurals.detail_gc_confirm_body_known, count, formatBytesGB(bytes) ?: "0 GB", count)
                } else {
                    stringResource(R.string.detail_gc_confirm_body_unknown)
                },
            )
        },
        confirmButton = { TextButton(onClick = onConfirm) { Text(stringResource(R.string.detail_gc_confirm_confirm)) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.detail_gc_confirm_cancel)) } },
    )
}
