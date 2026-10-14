package dev.steamvault.app.ui.clients

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Card
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
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
import dev.steamvault.app.net.model.ClientsOverviewOut
import dev.steamvault.app.repo.TrafficRepository
import dev.steamvault.app.ui.clients.logic.AGENTLESS_NOTE
import dev.steamvault.app.ui.clients.logic.COULD_NOT_LOAD_CLIENTS
import dev.steamvault.app.ui.clients.logic.ClientCardModel
import dev.steamvault.app.ui.clients.logic.ConnectionLook
import dev.steamvault.app.ui.clients.logic.LOADING_CLIENTS_TEXT
import dev.steamvault.app.ui.clients.logic.NO_CLIENTS_TEXT
import dev.steamvault.app.ui.clients.logic.buildClientCardModel
import dev.steamvault.app.ui.clients.logic.clientsHaveFigures
import dev.steamvault.app.ui.clients.logic.clientsSummaryText
import dev.steamvault.app.ui.clients.logic.showAgentlessNote
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
 * honest-state rules live in `ui/clients/logic/ClientsPage.kt` and
 * `ui/traffic/logic/TrafficView.kt`.
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
) {
    val scope = rememberCoroutineScope()
    val poll = remember(trafficRepository) { clientsOverviewPoll(trafficRepository, ranges.clients) }
    val lifecycleOwner = LocalLifecycleOwner.current
    LaunchedEffect(lifecycleOwner, poll) {
        lifecycleOwner.lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            poll.pollForever()
        }
    }
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
    ) { innerPadding ->
        Column(modifier = Modifier.fillMaxSize().padding(innerPadding)) {
            if (demoMode) DemoModeBanner()
            ClientsBody(poll = poll, scope = scope, ranges = ranges, onManagePcs = onManagePcs, clock = clock)
        }
    }
}

@Composable
private fun ClientsBody(
    poll: TrafficPoll<ClientsOverviewOut>,
    scope: CoroutineScope,
    ranges: TrafficRanges,
    onManagePcs: () -> Unit,
    clock: () -> Long,
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
    val cards = remember(data, nowMs) {
        data?.clients.orEmpty().map { buildClientCardModel(it, data?.range ?: poll.range, nowMs) }
    }
    val rangeLabel = stringResource(R.string.traffic_range_statistics)

    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(12.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        // No statistics (feed disabled, old server): no range selector either.
        if (!poll.noFigures) {
            item(key = "range") {
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
                cards.isEmpty() -> item(key = "empty") { HintLine(NO_CLIENTS_TEXT) }
                else -> items(cards, key = { "client-${it.key}" }) { ClientCard(it) }
            }
            if (data != null && showAgentlessNote(data.clients)) {
                item(key = "agentless") { HintLine(AGENTLESS_NOTE) }
            }
        }
        item(key = "manage") { ManagePcsSection(onManagePcs) }
    }
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
 * One client. The card merges its descendants, so TalkBack reads it as one
 * item in visual order (name, kind, connection, lines). The connection
 * dot is decoration (web `aria-hidden`); its word carries the meaning.
 */
@Composable
private fun ClientCard(model: ClientCardModel) {
    Card(modifier = Modifier.fillMaxWidth().semantics(mergeDescendants = true) {}) {
        Column(modifier = Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(model.name, style = MaterialTheme.typography.bodyLarge)
                    CardLine(model.kindText)
                }
                ConnectionChipView(word = model.chip.word, look = model.chip.look)
            }
            CardLine(model.loadingNow)
            model.installed?.let { CardLine(it) }
            model.stats?.let { CardLine(it) }
            model.recent?.let { CardLine(it) }
            CardLine(model.lastActive)
        }
    }
}

@Composable
private fun CardLine(text: String) {
    Text(text = text, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
}

private fun dotColor(look: ConnectionLook): Color = when (look) {
    ConnectionLook.ON -> VaultColors.StatusOk
    ConnectionLook.OFF -> VaultColors.StatusNone
    ConnectionLook.UNKNOWN -> VaultColors.Dim2
}

@Composable
private fun ConnectionChipView(word: String, look: ConnectionLook) {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(start = 8.dp)) {
        Box(modifier = Modifier.size(8.dp).background(dotColor(look), CircleShape))
        Text(word, style = MaterialTheme.typography.labelMedium, modifier = Modifier.padding(start = 6.dp))
    }
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
