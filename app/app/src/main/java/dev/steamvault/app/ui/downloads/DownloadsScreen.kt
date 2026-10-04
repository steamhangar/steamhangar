package dev.steamvault.app.ui.downloads

import androidx.compose.foundation.background
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.TextButton
import androidx.compose.ui.text.font.FontFamily
import dev.steamvault.app.ui.downloads.logic.FailureHintView
import dev.steamvault.app.ui.downloads.logic.NEWER_JOB_LINE
import dev.steamvault.app.ui.downloads.logic.failureHintViewFor
import dev.steamvault.app.ui.library.OwnedLibraryController
import dev.steamvault.app.ui.library.logic.anyJobLacksVaultName
import dev.steamvault.app.ui.library.logic.ownedNamesByAppid
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Snackbar
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.repeatOnLifecycle
import dev.steamvault.app.R
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.repo.GamesRepository
import dev.steamvault.app.repo.JobsRepository
import dev.steamvault.app.ui.demo.DemoModeBanner
import dev.steamvault.app.ui.downloads.logic.ExcerptState
import dev.steamvault.app.ui.downloads.logic.HistoryRowModel
import dev.steamvault.app.ui.downloads.logic.JobCardModel
import dev.steamvault.app.ui.downloads.logic.PartitionedJobs
import dev.steamvault.app.ui.downloads.logic.QueueRowModel
import dev.steamvault.app.ui.downloads.logic.buildHistoryRowModel
import dev.steamvault.app.ui.downloads.logic.buildJobCardModel
import dev.steamvault.app.ui.downloads.logic.buildQueueRowModel
import dev.steamvault.app.ui.downloads.logic.JobCardMode
import dev.steamvault.app.ui.downloads.logic.partitionJobs
import dev.steamvault.app.ui.downloads.logic.selectExcerptDisplay
import dev.steamvault.app.ui.status.StatusIcon
import dev.steamvault.app.ui.status.StatusIconSize
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.delay

/**
 * The Downloads screen (WP 4b.5 brief): Active job card(s) + Paused section
 * as INDEPENDENT sections (the slot-release divergence,
 * `ui/downloads/logic/JobPartition.kt`'s kdoc), a FIFO queue with
 * positions, and history newest-first with lazily-fetched log excerpts.
 * State/orchestration lives in [DownloadsController] (kept thin, same
 * `ui/library/LibraryController.kt` precedent); this file is rendering plus
 * the two foreground-only poll loops, gated by [Lifecycle.State.STARTED]
 * via `repeatOnLifecycle` -- no WorkManager involved (WP 4b.8's job).
 *
 * **Phase 4c guard (binding, per the WP brief).** Nothing on this screen
 * ever checks for or starts a download on its own initiative -- every job
 * shown here already exists on the server, and [onRefresh] only re-polls
 * `GET /v1/jobs`/`GET /v1/games`, exactly like `web`'s own
 * `store.refreshNow()` boundary.
 *
 * @param onJobsSnapshot fired whenever a fresh `GET /v1/jobs` poll lands --
 *   the seam `MainActivity` uses to keep the bottom-nav pip
 *   ([dev.steamvault.app.ui.downloads.logic.countPending]) live while this
 *   screen (rather than Library) is the one actually polling jobs. See
 *   `MainActivity.kt`'s kdoc for the honest scope limitation this implies
 *   (the pip is only live while a jobs-polling screen is visible; a
 *   background-independent pip needs WP 4b.8's WorkManager wiring).
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DownloadsScreen(
    jobsRepository: JobsRepository,
    gamesRepository: GamesRepository,
    ownedLibrary: OwnedLibraryController?,
    onJobsSnapshot: (List<JobSummary>) -> Unit = {},
    demoMode: Boolean,
) {
    val scope = rememberCoroutineScope()
    val resources = LocalContext.current.resources
    val controller = remember {
        DownloadsController(jobsRepository, gamesRepository, AndroidDownloadsStrings(resources))
    }

    val lifecycleOwner = LocalLifecycleOwner.current
    LaunchedEffect(lifecycleOwner, controller) {
        lifecycleOwner.lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            controller.pollJobsForever()
        }
    }
    LaunchedEffect(lifecycleOwner, controller) {
        lifecycleOwner.lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            controller.pollGamesForever()
        }
    }
    LaunchedEffect(controller.jobs) { onJobsSnapshot(controller.jobs) }

    val gamesByAppid = remember(controller.games) { controller.games.associateBy { it.appid } }
    // WP APP-FIX-2: titles in appTitle's order -- vault name, then the owned
    // list's name, then "App <id>". This screen never polls the relay: it
    // uses the list the Library already loaded, and starts the ONE load
    // itself only when a job on screen has no vault name and nothing was
    // loaded in this session yet (web downloads.js::maybeLoadOwnedNames).
    val ownedState = ownedLibrary?.state
    val ownedNames = remember(ownedState) { ownedNamesByAppid(ownedState?.ownedGamesOrNull) }
    val jobAppids = remember(controller.jobs) { controller.jobs.map { it.appid } }
    LaunchedEffect(controller.gamesKnown, jobAppids, gamesByAppid) {
        if (controller.gamesKnown && anyJobLacksVaultName(jobAppids, gamesByAppid)) ownedLibrary?.loadIfNeverLoaded()
    }
    val partition = remember(controller.jobs) { partitionJobs(controller.jobs) }
    val activeModels = remember(partition, gamesByAppid, ownedNames) {
        partition.running.map { buildJobCardModel(it, gamesByAppid, JobCardMode.ACTIVE, ownedNames) }
    }
    val pausedModels = remember(partition, gamesByAppid, ownedNames) {
        partition.paused.map { buildJobCardModel(it, gamesByAppid, JobCardMode.HELD, ownedNames) }
    }
    val queueModels = remember(partition, gamesByAppid, ownedNames) {
        partition.queued.mapIndexed { index, job -> buildQueueRowModel(job, index + 1, gamesByAppid, ownedNames) }
    }
    val historyModels = remember(partition, gamesByAppid, ownedNames) {
        partition.history.map { buildHistoryRowModel(it, gamesByAppid, ownedNames) }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text(stringResource(R.string.downloads_title))
                        Text(
                            text = downloadsSubtitle(partition),
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                },
            )
        },
        containerColor = MaterialTheme.colorScheme.background,
        snackbarHost = {
            controller.toast?.let { message ->
                LaunchedEffect(message) {
                    delay(controller.toastMs)
                    controller.dismissToast()
                }
                Snackbar(modifier = Modifier.padding(12.dp)) { Text(message) }
            }
        },
    ) { innerPadding ->
        Column(modifier = Modifier.fillMaxSize().padding(innerPadding)) {
            if (demoMode) DemoModeBanner()
            controller.loadError?.let { error ->
                Text(
                    text = stringResource(R.string.downloads_load_error, error),
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(12.dp),
                )
            }
            PauseAllDialog(controller, scope)
            DownloadsBody(
                partition = partition,
                activeModels = activeModels,
                pausedModels = pausedModels,
                queueModels = queueModels,
                historyModels = historyModels,
                controller = controller,
                scope = scope,
            )
        }
    }
}

@Composable
private fun DownloadsBody(
    partition: PartitionedJobs,
    activeModels: List<JobCardModel>,
    pausedModels: List<JobCardModel>,
    queueModels: List<QueueRowModel>,
    historyModels: List<HistoryRowModel>,
    controller: DownloadsController,
    scope: CoroutineScope,
) {
    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(12.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        item(key = "bulk-bar") { DownloadsBulkBar(controller, scope) }
        item(key = "active-heading") { SectionHeading(stringResource(R.string.downloads_section_active)) }
        if (activeModels.isEmpty()) {
            item(key = "active-empty") { EmptyLine(stringResource(R.string.downloads_active_empty)) }
        } else {
            items(activeModels, key = { "active-${it.jobId}" }) { model ->
                JobCard(
                    model = model,
                    busy = model.jobId in controller.busyJobIds,
                    onPause = { controller.pause(scope, model.jobId) },
                    onResume = { controller.resume(scope, model.jobId) },
                    onCancel = { controller.cancel(scope, model.jobId) },
                )
            }
        }

        if (pausedModels.isNotEmpty()) {
            item(key = "paused-heading") { SectionHeading(stringResource(R.string.downloads_section_paused)) }
            items(pausedModels, key = { "paused-${it.jobId}" }) { model ->
                JobCard(
                    model = model,
                    busy = model.jobId in controller.busyJobIds,
                    onPause = { controller.pause(scope, model.jobId) },
                    onResume = { controller.resume(scope, model.jobId) },
                    onCancel = { controller.cancel(scope, model.jobId) },
                )
            }
        }

        item(key = "queue-heading") {
            SectionHeading(stringResource(R.string.downloads_section_queue, queueModels.size))
        }
        if (queueModels.isEmpty()) {
            item(key = "queue-empty") { EmptyLine(stringResource(R.string.downloads_queue_empty)) }
        } else {
            items(queueModels, key = { "queue-${it.jobId}" }) { model ->
                QueueRow(
                    model = model,
                    busy = model.jobId in controller.busyJobIds,
                    onRemove = { controller.cancel(scope, model.jobId) },
                )
            }
            item(key = "queue-hint") {
                val base = stringResource(R.string.downloads_queue_hint_base)
                val hint = if (partition.paused.isNotEmpty()) {
                    base + " " + stringResource(R.string.downloads_queue_hint_paused_suffix)
                } else {
                    base
                }
                Text(hint, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }

        item(key = "history-heading") { SectionHeading(stringResource(R.string.downloads_section_history)) }
        if (historyModels.isEmpty()) {
            item(key = "history-empty") { EmptyLine(stringResource(R.string.downloads_history_empty)) }
        } else {
            items(historyModels, key = { "history-${it.jobId}" }) { model ->
                HistoryRow(model = model, controller = controller, scope = scope)
            }
        }
    }
}

/** Mirrors `web/js/views/downloads.js::subtitleText`: the non-empty
 * running/paused/queued counts joined with " · ", or "Idle · N in history"
 * when nothing is in flight. */
@Composable
private fun downloadsSubtitle(partition: PartitionedJobs): String {
    val bits = buildList {
        if (partition.running.isNotEmpty()) {
            add(pluralStringResource(R.plurals.downloads_count_running, partition.running.size, partition.running.size))
        }
        if (partition.paused.isNotEmpty()) {
            add(pluralStringResource(R.plurals.downloads_count_paused, partition.paused.size, partition.paused.size))
        }
        if (partition.queued.isNotEmpty()) {
            add(pluralStringResource(R.plurals.downloads_count_queued, partition.queued.size, partition.queued.size))
        }
    }
    return if (bits.isEmpty()) {
        pluralStringResource(R.plurals.downloads_idle_history, partition.history.size, partition.history.size)
    } else {
        bits.joinToString(" · ")
    }
}

@Composable
private fun SectionHeading(text: String) {
    Text(text = text, style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(top = 8.dp))
}

@Composable
private fun EmptyLine(text: String) {
    Text(text = text, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
}

/**
 * Active/Paused job card. Only [model] drives [StatusIcon]'s `kind`
 * parameter -- [busy] (client in-flight state) only ever reaches the action
 * buttons below it, which is what keeps the icon's animation untouched by a
 * click's own busy/un-busy transition (same mechanism
 * `ui/library/logic/GameCardModel.kt`'s kdoc documents for the Library
 * grid, applied here to the one extra transient input this screen has).
 */
@Composable
private fun JobCard(
    model: JobCardModel,
    busy: Boolean,
    onPause: () -> Unit,
    onResume: () -> Unit,
    onCancel: () -> Unit,
) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(12.dp)) {
            Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Column {
                    Text(model.name, style = MaterialTheme.typography.bodyLarge)
                    Text(
                        stringResource(R.string.downloads_job_subtitle, model.jobId, model.appid),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    StatusIcon(kind = model.kind, size = StatusIconSize.SMALL)
                    Text(model.statusWord, style = MaterialTheme.typography.labelMedium, modifier = Modifier.padding(start = 4.dp))
                }
            }

            if (model.mode == JobCardMode.HELD) {
                Text(
                    text = stringResource(R.string.downloads_paused_hold_note),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 6.dp),
                )
            }

            val action = model.action
            if (action.pausing) {
                StopNote(stringResource(R.string.downloads_stopnote_pausing))
            } else if (action.cancelling) {
                StopNote(stringResource(R.string.downloads_stopnote_cancelling))
            }

            Row(modifier = Modifier.padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (action.showResume) {
                    Button(onClick = onResume, enabled = !busy) { Text(stringResource(R.string.downloads_action_resume)) }
                }
                if (action.showPause) {
                    OutlinedButton(onClick = onPause, enabled = action.pauseEnabled && !busy) {
                        Text(
                            if (action.pausing) {
                                stringResource(R.string.downloads_action_pausing)
                            } else {
                                stringResource(R.string.downloads_action_pause)
                            },
                        )
                    }
                }
                if (action.showCancel) {
                    OutlinedButton(onClick = onCancel, enabled = action.cancelEnabled && !busy) {
                        Text(
                            if (action.cancelling) {
                                stringResource(R.string.downloads_action_cancelling)
                            } else {
                                stringResource(R.string.downloads_action_cancel)
                            },
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun StopNote(text: String) {
    Text(
        text = text,
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.error,
        modifier = Modifier.padding(top = 6.dp),
    )
}

@Composable
private fun QueueRow(model: QueueRowModel, busy: Boolean, onRemove: () -> Unit) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text("#${model.position}  ${model.name}", style = MaterialTheme.typography.bodyMedium)
        OutlinedButton(onClick = onRemove, enabled = !busy) {
            Text(stringResource(R.string.downloads_action_remove))
        }
    }
}

/**
 * History row with a lazily-fetched log excerpt (WP 4b.5 brief: "one
 * JobDetail fetch per job on first expand, cached for the session").
 * Deliberately reads [DownloadsController.excerptVersion] -- see that
 * property's kdoc for why this is the mechanism that makes this composable
 * recompose when [ExcerptCache]'s plain-map state changes underneath it.
 */
@Composable
private fun HistoryRow(model: HistoryRowModel, controller: DownloadsController, scope: CoroutineScope) {
    controller.excerptVersion // subscribe this scope to excerpt-cache mutations -- see kdoc above
    val excerptState = controller.excerptStateFor(model.jobId)
    val display = selectExcerptDisplay(excerptState)

    Column(modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        val expandDescription = if (excerptState.expanded) {
            stringResource(R.string.downloads_history_collapse)
        } else {
            stringResource(R.string.downloads_history_expand)
        }
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .semantics { contentDescription = expandDescription },
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                StatusIcon(kind = model.kind, size = StatusIconSize.SMALL)
                Column(modifier = Modifier.padding(start = 8.dp)) {
                    Text(model.name, style = MaterialTheme.typography.bodyMedium)
                    Text(
                        stringResource(R.string.downloads_history_subtitle, model.jobId, model.statusWord, model.finishedAtLabel),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            IconButton(onClick = { controller.toggleHistoryRow(scope, model.jobId) }) {
                Text(if (excerptState.expanded) "▲" else "▼")
            }
        }

        when (display.state) {
            ExcerptState.COLLAPSED -> {}
            ExcerptState.LOADING -> Text(
                stringResource(R.string.downloads_log_loading),
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.padding(start = 24.dp, top = 4.dp),
            )
            ExcerptState.ERROR -> Text(
                stringResource(R.string.downloads_log_error, display.message ?: ""),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
                modifier = Modifier.padding(start = 24.dp, top = 4.dp),
            )
            ExcerptState.EMPTY -> Text(
                stringResource(R.string.downloads_log_empty),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(start = 24.dp, top = 4.dp),
            )
            ExcerptState.READY -> Column(modifier = Modifier.padding(start = 24.dp, top = 4.dp)) {
                // WP APP-FIX-2: a known failure (not_logged_in, the public-IP
                // cache detection) shows the hint block + Retry first and the
                // raw output collapsed behind a toggle; every other failure
                // (prefill_failed included) shows the output as before.
                val job = controller.jobs.firstOrNull { it.id == model.jobId }
                val hintView = failureHintViewFor(job, excerptState.excerpt, controller.jobs)
                val showOutput = if (hintView != null) {
                    FailureHintBlock(
                        view = hintView,
                        retryLabel = stringResource(R.string.downloads_action_retry_for, model.name),
                        retryBusy = model.appid in controller.retryBusyAppids,
                        onRetry = { controller.retry(scope, model.appid) },
                    )
                    val open = model.jobId in controller.rawOutputOpenJobIds
                    TextButton(onClick = { controller.toggleRawOutput(model.jobId) }) {
                        Text((if (open) "▲ " else "▼ ") + hintView.text.outputSummary)
                    }
                    open
                } else {
                    true
                }
                if (showOutput) {
                    if (display.truncated) {
                        Text(
                            stringResource(R.string.downloads_log_truncated_note),
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    Box(modifier = Modifier.fillMaxWidth()) {
                        Text(display.lines.joinToString("\n"), style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
        }
    }
}

/**
 * WP APP-FIX-2: a known failure's hint block (text from
 * `ui/downloads/logic/JobFailure.kt`'s [FailureHintView], a verbatim port of
 * web `job-failure.js`'s `HINTS`). Retry only on the newest prefill job for
 * the app; an older failed row says a newer job exists instead. The command
 * box is selectable so it can be copied.
 */
@Composable
private fun FailureHintBlock(
    view: FailureHintView,
    retryLabel: String,
    retryBusy: Boolean,
    onRetry: () -> Unit,
) {
    val text = view.text
    Card(modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        Column(modifier = Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(text.title, style = MaterialTheme.typography.titleSmall)
            Text(text.body, style = MaterialTheme.typography.bodySmall)
            Text(text.codeIntro, style = MaterialTheme.typography.bodySmall)
            SelectionContainer {
                Text(
                    text.code,
                    style = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace),
                    modifier = Modifier
                        .fillMaxWidth()
                        .background(MaterialTheme.colorScheme.surfaceVariant)
                        .padding(8.dp),
                )
            }
            Text(text.after, style = MaterialTheme.typography.bodySmall)
            if (view.retryOffered) {
                Text(text.retry, style = MaterialTheme.typography.bodySmall)
                Button(
                    onClick = onRetry,
                    enabled = !retryBusy,
                    modifier = Modifier.semantics { contentDescription = retryLabel },
                ) { Text(stringResource(R.string.downloads_action_retry)) }
            } else {
                Text(NEWER_JOB_LINE, style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}
