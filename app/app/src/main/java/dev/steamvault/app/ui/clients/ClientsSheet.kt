package dev.steamvault.app.ui.clients

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Card
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.key
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.repeatOnLifecycle
import dev.steamvault.app.R
import dev.steamvault.app.ui.clients.logic.ClientPresence
import dev.steamvault.app.ui.clients.logic.ClientPresenceView
import dev.steamvault.app.ui.clients.logic.ClientRowModel
import dev.steamvault.app.ui.clients.logic.buildClientRowModel
import dev.steamvault.app.ui.clients.logic.formatAgo
import dev.steamvault.app.ui.clients.logic.partitionClients
import dev.steamvault.app.ui.demo.DemoModeBanner
import dev.steamvault.app.ui.library.logic.formatBytesGB
import dev.steamvault.app.ui.status.StatusIcon
import dev.steamvault.app.ui.status.StatusIconSize
import dev.steamvault.app.ui.status.StatusKind
import dev.steamvault.app.ui.theme.VaultColors

/**
 * The clients sheet (WP 4b.10 brief): real `GET /v1/clients` data in the
 * mockup's round-5 "Bypassing" / "Healthy" grouping. State/orchestration
 * lives in [ClientsController] (kept thin, same
 * `ui/detail/DetailController.kt` precedent); this file is rendering plus
 * the one foreground-only poll loop, gated by [Lifecycle.State.STARTED] via
 * `repeatOnLifecycle` — same shape `ui/downloads/DownloadsScreen.kt` uses,
 * applied here to a sheet instead of a full screen.
 *
 * **Compose `ModalBottomSheet`, same justification `ui/detail/GameDetailSheet.kt`'s
 * kdoc already gives** for this class of transient surface (native focus/
 * back-gesture/scrim-dismiss handling for free, matching what
 * `web/js/components/sheet-dialog.js` hand-rolls for the web twin).
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ClientsSheet(controller: ClientsController, demoMode: Boolean = false) {
    val lifecycleOwner = LocalLifecycleOwner.current
    LaunchedEffect(lifecycleOwner, controller) {
        lifecycleOwner.lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            controller.pollForever()
        }
    }

    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    ModalBottomSheet(
        onDismissRequest = { controller.close() },
        sheetState = sheetState,
    ) {
        // WP APP-DEMO review round 3 (F2): called directly here, in THIS
        // top-level public composable -- see GameDetailSheet.kt's identical
        // fix and its kdoc for the full "why" (ModalBottomSheet's `content`
        // lambda is already a non-scrolling ColumnScope, so no wrapper
        // Column of this file's own is needed, and there is no helper
        // function left for a "banner moved back inside the scrolling
        // body" regression to hide behind).
        if (demoMode) DemoModeBanner()
        ClientsSheetBody(controller)
    }
}

@Composable
private fun ClientsSheetBody(controller: ClientsController) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 8.dp)
            .padding(bottom = 24.dp),
    ) {
        Text(
            text = stringResource(R.string.clients_sheet_title),
            style = MaterialTheme.typography.titleLarge,
            modifier = Modifier.semantics(mergeDescendants = true) { heading() },
        )
        Text(
            text = stringResource(R.string.clients_sheet_intro),
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(8.dp))

        controller.loadError?.let { error ->
            Text(
                text = stringResource(R.string.clients_load_error, error),
                color = MaterialTheme.colorScheme.error,
                style = MaterialTheme.typography.bodySmall,
            )
            Spacer(Modifier.height(8.dp))
        }

        if (controller.clients.isEmpty()) {
            Text(
                text = stringResource(R.string.clients_empty),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            return@Column
        }

        // `remember`ed off the raw snapshot, same "recompute cheaply from
        // live data" posture GameDetailSheet.kt's depot presentation
        // documents -- no separate render-plan diff object is needed on
        // this platform, see ClientRowModel's kdoc for why.
        val partition = remember(controller.clients) { partitionClients(controller.clients) }

        if (partition.bypassing.isNotEmpty()) {
            SectionHeading(stringResource(R.string.clients_section_bypassing))
            for (client in partition.bypassing) {
                key(client.client_id) {
                    ClientRow(model = remember(client) { buildClientRowModel(client) })
                }
            }
        }

        if (partition.healthy.isNotEmpty()) {
            SectionHeading(stringResource(R.string.clients_section_healthy))
            for (client in partition.healthy) {
                key(client.client_id) {
                    ClientRow(model = remember(client) { buildClientRowModel(client) })
                }
            }
        }
    }
}

@Composable
private fun SectionHeading(text: String) {
    Text(text = text, style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(top = 8.dp))
}

/**
 * One client's row. Only [ClientRowModel.bypassSuspected] drives which
 * section/badge/icon-kind this row renders with; [ClientRowModel.stats]
 * (the field a poll tick changes almost every time) only ever reaches
 * [statsLineFor]'s output -- the same "volatile field stays confined to its
 * own leaf" shape `ui/clients/logic/ClientsView.kt`'s [ClientRowModel] kdoc
 * documents (itself a port of `ui/downloads/logic/JobCardModel.kt`'s
 * `action`-field split), applied here to a poll-driven stats field instead
 * of a client-side busy flag.
 */
@Composable
private fun ClientRow(model: ClientRowModel) {
    Card(modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        Column(modifier = Modifier.padding(12.dp)) {
            Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(model.clientId, style = MaterialTheme.typography.bodyLarge)
                    Text(
                        text = presenceLineFor(model.presence),
                        style = MaterialTheme.typography.bodySmall,
                        color = if (model.presence.presence == ClientPresence.ONLINE) {
                            VaultColors.StatusOk
                        } else {
                            MaterialTheme.colorScheme.onSurfaceVariant
                        },
                    )
                    Text(
                        text = addressesLineFor(model.addresses) + " · " + statsLineFor(model),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    // Decorative, aria-hidden equivalent: the badge word
                    // right next to it is the correct accessible text for
                    // this row (clients-sheet.js::buildRow's own "avoid
                    // double/mismatched announcement" comment, ported
                    // literally -- StatusIcon's own built-in
                    // contentDescription carries game-caching wording
                    // ("Current"/"Warning") reused here for its shape only).
                    Box(modifier = Modifier.clearAndSetSemantics {}) {
                        StatusIcon(
                            kind = if (model.bypassSuspected) StatusKind.WARN else StatusKind.CACHED,
                            size = StatusIconSize.SMALL,
                        )
                    }
                    Text(
                        text = stringResource(
                            if (model.bypassSuspected) R.string.clients_section_bypassing else R.string.clients_section_healthy,
                        ),
                        style = MaterialTheme.typography.labelMedium,
                        modifier = Modifier.padding(start = 4.dp),
                    )
                }
            }

            // Repeated on EVERY bypass-suspected row, not once per section
            // -- see ui/clients/logic/ClientsView.kt's kdoc for why this is
            // the verified web BEHAVIOUR, not its (stale) comment.
            if (model.bypassSuspected) {
                Text(
                    text = stringResource(R.string.clients_bypass_explanation),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 6.dp),
                )
            }
        }
    }
}

/** Mirrors `clients-view.js::addressesText`: joined `source_addrs`, or an
 * honest "no known address" for a client whose retained reports predate
 * schema v9. */
@Composable
private fun addressesLineFor(addresses: List<String>): String =
    if (addresses.isEmpty()) stringResource(R.string.clients_addresses_unknown) else addresses.joinToString(", ")

/** Mirrors `clients-view.js::describeHealthyClient`/`describeBypassClient`:
 * a healthy row gets the full games/bytes/hit-rate line, a bypass-suspected
 * row gets the games count plus the observation (never a cause -- that is
 * [ClientRow]'s separate explanation paragraph below it). */
@Composable
private fun statsLineFor(model: ClientRowModel): String {
    val gamesText = model.stats.gamesReported?.let {
        pluralStringResource(R.plurals.clients_games_reported, it, it)
    } ?: stringResource(R.string.clients_games_unknown)

    if (model.bypassSuspected) {
        return "$gamesText · " + stringResource(R.string.clients_bypass_note)
    }

    val bytesText = formatBytesGB(model.stats.bytesServed)?.let {
        stringResource(R.string.clients_bytes_served, it)
    } ?: stringResource(R.string.clients_bytes_none)
    val rateText = model.stats.hitRatePercent?.let {
        stringResource(R.string.clients_hit_rate, it)
    } ?: stringResource(R.string.clients_hit_rate_none)

    return listOf(gamesText, bytesText, rateText).joinToString(" · ")
}

/**
 * WP APP-FEAT-2 (parity with AGENT-FEAT-1 / web WEB-FEAT-3): "Online ·
 * last seen 4 min ago · agent 0.1.0". Online/Offline is the SERVER's
 * `presence`, passed through and never recomputed; a server older than
 * AGENT-FEAT-1 sends none, which reads "Presence unknown", never a guess.
 * "last seen" is relative (web `formatAgo`); a missing agent version reads
 * "version unknown".
 */
@Composable
private fun presenceLineFor(view: ClientPresenceView): String {
    val word = when (view.presence) {
        ClientPresence.ONLINE -> stringResource(R.string.clients_presence_online)
        ClientPresence.OFFLINE -> stringResource(R.string.clients_presence_offline)
        null -> stringResource(R.string.clients_presence_unknown)
    }
    val ago = formatAgo(view.lastReportedAt)
    val lastSeen = if (ago != null) {
        stringResource(R.string.clients_last_seen, ago)
    } else {
        stringResource(R.string.clients_last_seen_unknown)
    }
    val version = view.agentVersion?.let { stringResource(R.string.clients_agent_version, it) }
        ?: stringResource(R.string.clients_agent_version_unknown)
    return listOf(word, lastSeen, version).joinToString(" · ")
}
