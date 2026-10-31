package dev.steamvault.app.ui.clients

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Snackbar
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.repeatOnLifecycle
import dev.steamvault.app.R
import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.ClientsOverviewOut
import dev.steamvault.app.repo.ClientsRepository
import dev.steamvault.app.repo.SettingsRepository
import dev.steamvault.app.repo.TrafficRepository
import dev.steamvault.app.ui.clients.logic.AGENTLESS_NOTE
import dev.steamvault.app.ui.clients.logic.COULD_NOT_LOAD_CLIENTS
import dev.steamvault.app.ui.clients.logic.DEVICES_HEADING
import dev.steamvault.app.ui.clients.logic.LOADING_CLIENTS_TEXT
import dev.steamvault.app.ui.clients.logic.NO_CLIENTS_TEXT
import dev.steamvault.app.ui.clients.logic.REMOVED_HIDE_MS
import dev.steamvault.app.ui.clients.logic.clientCardView
import dev.steamvault.app.ui.clients.logic.clientsHaveFigures
import dev.steamvault.app.ui.clients.logic.clientsSummaryText
import dev.steamvault.app.ui.clients.logic.hiddenRemoved
import dev.steamvault.app.ui.clients.logic.isAgentClient
import dev.steamvault.app.ui.clients.logic.mergeAgentData
import dev.steamvault.app.ui.clients.logic.showAgentlessNote
import dev.steamvault.app.ui.clients.logic.summaryTiles
import dev.steamvault.app.ui.demo.DemoModeBanner
import dev.steamvault.app.ui.theme.VaultColors
import dev.steamvault.app.ui.traffic.RangeChips
import dev.steamvault.app.ui.traffic.TrafficPoll
import dev.steamvault.app.ui.traffic.TrafficRanges
import dev.steamvault.app.ui.traffic.logic.pollNote
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.delay

/** How often relative times ("4 min ago") on the Clients page refresh. */
private const val NOW_TICK_MS = 60_000L

/** How long a rename note stays (a little longer than web's 2.6 s toast,
 * so TalkBack finishes reading it). */
private const val NOTICE_MS = 4_000L

/** The Clients page's poll: `GET /v1/clients/overview`, starting at the
 * range chosen last ([TrafficRanges.clients], default 7 days like web). */
fun clientsOverviewPoll(repository: TrafficRepository, initialRange: String = "7d"): TrafficPoll<ClientsOverviewOut> =
    TrafficPoll(initialRange = initialRange, fetch = { range -> repository.clientsOverview(range) }, hasFigures = ::clientsHaveFigures)

/**
 * The Clients page (WP D15d, twin of web `views/clients.js`, WP D15c): every
 * machine the vault knows, from `GET /v1/clients/overview` -- PCs running
 * vault-agent and agentless addresses that only appear in the cache's
 * event log. Per client: connected or not, what is loading now, installed
 * games (agent PCs only), what was loaded recently, bytes and hit rate for
 * the selected range, and when it was last active. Wording and the
 * honest-state rules live in `ui/clients/logic/ClientsPage.kt`,
 * `ui/clients/logic/ClientCard.kt` and `ui/traffic/logic/TrafficView.kt`.
 *
 * **rc20 look (WP ANDROID-REDESIGN-1a, web CLIENTS-1..4).** One card per
 * device: the agent rows of `GET /v1/clients` ([clientsController]'s
 * snapshot, polled while this page is STARTED) join the overview row of the
 * same `client_id` ([mergeAgentData]). Four summary tiles, the "Devices"
 * label with the range chips, then the cards (`ClientCardViews.kt`) with
 * Rename and "Remove agent" in the footer. Remove reuses the PCs sheet's
 * controller and confirm ([RemovePcConfirm]); a removed PC stays hidden
 * for [REMOVED_HIDE_MS] until the overview has caught up. The outdated
 * check reads `server_version` from [settingsRepository] once per visit.
 *
 * **A nav destination, not a sheet (user decision 2026-10-13, D15).** The
 * PCs sheet (`ClientsSheet.kt`, presence/version/bypass per agent) stays,
 * hoisted in `MainActivity`; this page's "Manage PCs" button opens it, and
 * Settings keeps only a pointer here (PC management moved, like on web).
 *
 * Polling: [TrafficPoll], 30 s, only while this screen is STARTED. A
 * server older than D15b (404) gets one note and no statistics; with the
 * feed disabled the range selector is hidden as well.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ClientsScreen(
    trafficRepository: TrafficRepository,
    ranges: TrafficRanges,
    onManagePcs: () -> Unit,
    demoMode: Boolean,
    clock: () -> Long = { System.currentTimeMillis() },
    /** WP ANDROID-GAPS-1: where Rename stores names; `null` = no Rename. */
    clientsRepository: ClientsRepository? = null,
    /** WP ANDROID-REDESIGN-1a: agent rows and Remove; `null` = overview only. */
    clientsController: ClientsController? = null,
    /** WP ANDROID-REDESIGN-1a: `server_version` for the outdated check. */
    settingsRepository: SettingsRepository? = null,
) {
    val scope = rememberCoroutineScope()
    val poll = remember(trafficRepository) { clientsOverviewPoll(trafficRepository, ranges.clients) }
    val renameFlow = remember(clientsRepository) { clientsRepository?.let { ClientRenameFlow(it) } }
    val lifecycleOwner = LocalLifecycleOwner.current
    LaunchedEffect(lifecycleOwner, poll) {
        lifecycleOwner.lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            poll.pollForever()
        }
    }
    if (clientsController != null) {
        LaunchedEffect(lifecycleOwner, clientsController) {
            lifecycleOwner.lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
                clientsController.pollForPage()
            }
        }
        // A successful Remove: re-read the overview at once (web poll.refresh()).
        val removedAt = clientsController.removedAt
        LaunchedEffect(removedAt) {
            if (removedAt.isNotEmpty()) poll.refreshOnce()
        }
    }
    var serverVersion by remember(settingsRepository) { mutableStateOf<String?>(null) }
    LaunchedEffect(settingsRepository) { serverVersion = readServerVersion(settingsRepository) }
    val data = poll.data

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text(stringResource(R.string.clients_page_title))
                        if (data != null && !poll.unsupported) {
                            Text(
                                text = clientsSummaryText(data.clients),
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                },
            )
        },
        containerColor = MaterialTheme.colorScheme.background,
        snackbarHost = { PageNotices(renameFlow, clientsController) },
    ) { innerPadding ->
        Column(modifier = Modifier.fillMaxSize().padding(innerPadding)) {
            if (demoMode) DemoModeBanner()
            ClientsBody(
                poll = poll,
                scope = scope,
                ranges = ranges,
                onManagePcs = onManagePcs,
                clock = clock,
                renameFlow = renameFlow,
                controller = clientsController,
                serverVersion = serverVersion,
            )
        }
    }
    if (renameFlow != null) {
        ClientRenameDialog(
            flow = renameFlow,
            scope = scope,
            displayNameAfter = { key -> trafficRepository.clientsOverview(poll.range).clients.firstOrNull { it.key == key }?.display_name },
            onChanged = { poll.refreshOnce() },
        )
    }
    // The card's "Remove agent" confirm; the PCs sheet shows its own while open.
    if (clientsController != null && !clientsController.isOpen) RemovePcConfirm(clientsController, scope)
}

/** `server_version` of `GET /v1/settings`, or `null` (no repository, an
 * error, or a server that does not send it: then nothing warns). */
private suspend fun readServerVersion(settingsRepository: SettingsRepository?): String? {
    if (settingsRepository == null) return null
    return try {
        settingsRepository.get().server_version
    } catch (_: VaultApiError) {
        null
    }
}

@Composable
private fun ClientsBody(
    poll: TrafficPoll<ClientsOverviewOut>,
    scope: CoroutineScope,
    ranges: TrafficRanges,
    onManagePcs: () -> Unit,
    clock: () -> Long,
    renameFlow: ClientRenameFlow?,
    controller: ClientsController?,
    serverVersion: String?,
) {
    val data = poll.data
    // WP D15d review: "Last active 4 min ago" and the stale-feed note are
    // relative to now; a minute tick (and every answer) keeps them current.
    val currentClock by rememberUpdatedState(clock)
    var nowMs by remember { mutableLongStateOf(clock()) }
    LaunchedEffect(data) { nowMs = currentClock() }
    LaunchedEffect(Unit) {
        while (true) {
            delay(NOW_TICK_MS)
            nowMs = currentClock()
        }
    }
    val note = pollNote(
        unsupported = poll.unsupported,
        error = poll.error,
        feed = data?.event_feed,
        hasData = data != null,
        couldNotLoad = COULD_NOT_LOAD_CLIENTS,
        loadingText = null,
        nowMs = nowMs,
    )
    val agentRows = controller?.clients
    val removedAt = controller?.removedAt.orEmpty()
    val removeErrors = controller?.removeErrors.orEmpty()
    // web paint(): the overview rows (a just-removed PC hidden), merged with the agent rows.
    val merged = remember(data, agentRows, removedAt, nowMs) {
        val rows = data?.clients.orEmpty().filter { !(isAgentClient(it) && hiddenRemoved(it.client_id, removedAt, nowMs)) }
        mergeAgentData(rows, agentRows)
    }
    val range = data?.range ?: poll.range
    val cards = remember(merged, range, serverVersion, nowMs, removeErrors) {
        merged.map { clientCardView(it, range, serverVersion, nowMs, it.agent?.let { a -> removeErrors[a.client_id] }) }
    }
    val tiles = remember(merged, range, serverVersion) { if (merged.isEmpty()) emptyList() else summaryTiles(merged, range, serverVersion) }
    val rangeLabel = stringResource(R.string.traffic_range_statistics)

    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        if (!poll.unsupported && tiles.isNotEmpty()) {
            item(key = "tiles") { SummaryTiles(tiles) }
        }
        item(key = "devices") {
            DevicesHead(showRange = !poll.noFigures) {
                RangeChips(
                    label = rangeLabel,
                    selected = poll.range,
                    onSelect = {
                        poll.selectRange(scope, it)
                        ranges.clients = poll.range
                    },
                )
            }
        }
        if (note != null) {
            item(key = "note") { NoteLine(note) }
        }
        if (!poll.unsupported) {
            when {
                data == null -> if (poll.error == null) item(key = "loading") { HintLine(LOADING_CLIENTS_TEXT) }
                cards.isEmpty() -> item(key = "empty") { EmptyState() }
                else -> items(cards, key = { "client-${it.key}" }) { card ->
                    val client = merged.firstOrNull { it.client.key == card.key }?.client
                    ClientCard(
                        card,
                        onRename = if (renameFlow != null && client != null) { { renameFlow.open(client) } } else null,
                        onRemove = if (controller != null && card.removeId != null) { { controller.requestRemove(card.removeId) } } else null,
                    )
                }
            }
            if (data != null && showAgentlessNote(data.clients)) {
                item(key = "agentless") { HintLine(AGENTLESS_NOTE) }
            }
        }
        item(key = "manage") { ManagePcsSection(onManagePcs) }
    }
}

/** "DEVICES" with the range chips on the right (kit `.tool`); the chips
 * wrap under the label where the row is too narrow. No statistics (feed
 * disabled, old server): no range selector either. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun DevicesHead(showRange: Boolean, chips: @Composable () -> Unit) {
    FlowRow(
        modifier = Modifier.fillMaxWidth().padding(top = 6.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        SectionLabel(DEVICES_HEADING, Modifier.align(Alignment.CenterVertically).padding(end = 12.dp).semantics { heading() })
        if (showRange) chips()
    }
}

/** Empty list (web `.cl-empty`): the honest sentence in a dashed box. The
 * web's "Add a device" button has no app twin yet. */
@Composable
private fun EmptyState() {
    Text(
        text = NO_CLIENTS_TEXT,
        style = MaterialTheme.typography.bodyMedium,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier
            .fillMaxWidth()
            .drawBehind {
                val w = 1.dp.toPx()
                drawRoundRect(
                    color = VaultColors.Line,
                    topLeft = Offset(w / 2, w / 2),
                    size = Size(size.width - w, size.height - w),
                    cornerRadius = CornerRadius(14.dp.toPx()),
                    style = Stroke(width = w, pathEffect = PathEffect.dashPathEffect(floatArrayOf(4.dp.toPx(), 3.dp.toPx()))),
                )
            }
            .padding(16.dp),
    )
}

/** The status/feed note; a polite live region, so a refresh failure or a
 * feed change is announced (web: the note paragraph). */
@Composable
private fun NoteLine(text: String) {
    Text(
        text = text,
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.fillMaxWidth().semantics { liveRegion = LiveRegionMode.Polite },
    )
}

@Composable
private fun HintLine(text: String) {
    Text(text = text, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
}

/**
 * "Manage PCs" (moved here from Settings, D15): opens the PCs sheet
 * (`ClientsSheet.kt`) with presence, last report, agent version and cache
 * bypass state per agent.
 */
@Composable
private fun ManagePcsSection(onManagePcs: () -> Unit) {
    Column(modifier = Modifier.fillMaxWidth().padding(top = 8.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        HorizontalDivider()
        Text(
            stringResource(R.string.clients_manage_heading),
            style = MaterialTheme.typography.titleSmall,
            modifier = Modifier.padding(top = 8.dp).semantics { heading() },
        )
        Text(stringResource(R.string.clients_manage_title), style = MaterialTheme.typography.bodyMedium)
        Text(
            stringResource(R.string.clients_manage_desc),
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        OutlinedButton(onClick = onManagePcs) { Text(stringResource(R.string.clients_manage_button)) }
    }
}

/** The page's notes: the rename note, else the card Remove's
 * "<id> removed from the list." (shown here only while the PCs sheet is
 * closed; the sheet shows its own). */
@Composable
private fun PageNotices(renameFlow: ClientRenameFlow?, controller: ClientsController?) {
    if (renameFlow?.notice != null) {
        RenameNotice(renameFlow)
        return
    }
    if (controller != null && !controller.isOpen) RemoveNotice(controller)
}

/** WP ANDROID-GAPS-1: "Renamed to ..." / "Name removed ..." (web: the
 * toast), read out when it appears. */
@Composable
private fun RenameNotice(flow: ClientRenameFlow) {
    val message = flow.notice ?: return
    LaunchedEffect(message) {
        delay(NOTICE_MS)
        flow.notice = null
    }
    Snackbar(modifier = Modifier.padding(12.dp).semantics { liveRegion = LiveRegionMode.Polite }) { Text(message) }
}

/** WP ANDROID-REDESIGN-1a: the card Remove's success note (web: the toast). */
@Composable
private fun RemoveNotice(controller: ClientsController) {
    val message = controller.removeNotice ?: return
    LaunchedEffect(message) {
        delay(NOTICE_MS)
        controller.clearRemoveNotice()
    }
    Snackbar(modifier = Modifier.padding(12.dp).semantics { liveRegion = LiveRegionMode.Polite }) { Text(message) }
}
