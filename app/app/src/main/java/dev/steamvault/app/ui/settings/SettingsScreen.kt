package dev.steamvault.app.ui.settings

import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.ui.text.input.KeyboardType
import dev.steamvault.app.ui.library.logic.STEAM_LIBRARY_SETTING_KEY
import dev.steamvault.app.ui.settings.logic.AboutDisplayStatus
import dev.steamvault.app.ui.settings.logic.AboutLoadFailure
import dev.steamvault.app.ui.settings.logic.AboutRow
import dev.steamvault.app.ui.settings.logic.AboutNote
import dev.steamvault.app.ui.settings.logic.LibraryLookupError
import dev.steamvault.app.ui.settings.logic.LibraryPreview
import dev.steamvault.app.ui.settings.logic.aboutRowsFor
import dev.steamvault.app.ui.settings.logic.canResetLibrarySteamId
import dev.steamvault.app.ui.settings.logic.canSaveLibrarySteamId
import dev.steamvault.app.ui.theme.VaultColors
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.size
import androidx.compose.material3.IconButton
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.material3.Surface
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import dev.steamvault.app.R
import dev.steamvault.app.net.model.ScheduleOut
import dev.steamvault.app.net.model.SettingInfoOut
import dev.steamvault.app.repo.SteamIdentityState
import dev.steamvault.app.storage.ProfileKind
import dev.steamvault.app.ui.demo.DemoModeBanner
import dev.steamvault.app.ui.settings.logic.SettingDraft
import dev.steamvault.app.ui.settings.logic.SettingsApplies
import dev.steamvault.app.ui.settings.logic.SettingsLayout
import dev.steamvault.app.ui.settings.logic.SettingsSource
import dev.steamvault.app.ui.settings.logic.SteamLibraryStatus
import dev.steamvault.app.ui.settings.logic.cachedSweepGcRiskWarning
import dev.steamvault.app.ui.settings.logic.canResetSetting
import dev.steamvault.app.ui.settings.logic.effectiveAsFieldText
import dev.steamvault.app.ui.settings.logic.parseSettingsApplies
import dev.steamvault.app.ui.settings.logic.parseSettingsSource
import dev.steamvault.app.ui.settings.logic.sweepTargetsMessage
import kotlinx.coroutines.launch

/**
 * The Settings screen (WP 4b.7 brief) -- replaces `Destination.SETTINGS`'s
 * previous placeholder (bare [dev.steamvault.app.ui.identity.IdentityScreen]).
 * Three sections mirror [SettingsController]'s three independent surfaces --
 * see that class's kdoc.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    controller: SettingsController,
    onSignInSteamClick: () -> Unit,
    onReconnectClick: () -> Unit,
    onDisconnected: () -> Unit,
    onRequestNotificationPermission: () -> Unit,
    onOpenClientsClick: () -> Unit,
    demoMode: Boolean,
) {
    val scope = rememberCoroutineScope()
    LaunchedEffect(controller) { controller.load() }
    LaunchedEffect(controller) { controller.loadAbout() }

    Scaffold(
        topBar = { TopAppBar(title = { Text(stringResource(R.string.settings_title)) }) },
        // WP WEB-FIX-8 (user request: Save visible as soon as something
        // changed): a bottom bar outside the scrolling column; the Scaffold's
        // innerPadding keeps the form's end clear of it.
        bottomBar = {
            if (!controller.loading && controller.loadError == null && !controller.isReadonly && controller.isDirty) {
                SettingsSaveBar(controller, scope)
            }
        },
    ) { innerPadding ->
        // WP APP-DEMO review round 2 (B2): the banner lives in this OUTER,
        // non-scrolling Column -- a sibling of the scrolling Column below,
        // never a child of it -- so it stays pinned on screen regardless of
        // scroll offset. The original single-Column version put the banner
        // INSIDE the `.verticalScroll(...)` Column as its first child, which
        // scrolls it away with everything else the instant the user scrolls
        // past the top; a screenshot taken anywhere but the very top of this
        // screen would then carry no indicator at all.
        Column(modifier = Modifier.fillMaxSize().padding(innerPadding)) {
            RiskyConfirmDialog(controller, scope)
            if (demoMode) DemoModeBanner()
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .verticalScroll(rememberScrollState())
                    .padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(16.dp),
            ) {
                when {
                    controller.loading -> Text(stringResource(R.string.settings_loading))
                    controller.loadError != null ->
                        Text(
                            stringResource(R.string.settings_load_error, controller.loadError.orEmpty()),
                            color = MaterialTheme.colorScheme.error,
                        )
                    else -> SettingsForm(controller)
                }

                HorizontalDivider()
                SteamIdentitySection(controller, scope, onSignInSteamClick, demoMode)
                if (!controller.loading && controller.loadError == null) {
                    SteamLibraryBlock(controller, scope, demoMode)
                }
                HorizontalDivider()
                NotificationsSection(onRequestNotificationPermission)
                HorizontalDivider()
                ClientsSection(onOpenClientsClick)
                HorizontalDivider()
                ConnectionSection(controller, onReconnectClick, onDisconnected, demoMode)
                HorizontalDivider()
                AboutSection(controller, scope)
            }
        }
    }
}

/**
 * The save bar (WP WEB-FIX-8, twin of web settings.js's `.savebar`): shown
 * by [SettingsScreen]'s Scaffold `bottomBar` only while
 * [SettingsController.isDirty] -- the PATCH would change something -- so it
 * disappears after a successful save, on Discard, and when a field is typed
 * back to its saved value. A failed save keeps the drafts (still dirty), so
 * the bar stays and its status line says what failed; the line is a polite
 * live region, so TalkBack announces it. Discard first, then Save (same
 * order as the web).
 */
@Composable
private fun SettingsSaveBar(controller: SettingsController, scope: kotlinx.coroutines.CoroutineScope) {
    val error = controller.saveError
    Surface(tonalElevation = 3.dp, shadowElevation = 6.dp, modifier = Modifier.fillMaxWidth()) {
        Column(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                when {
                    controller.saving -> stringResource(R.string.settings_saving)
                    error != null -> error
                    controller.riskyCancelled -> stringResource(R.string.settings_risky_cancelled)
                    else -> stringResource(R.string.settings_unsaved_changes)
                },
                style = MaterialTheme.typography.bodySmall,
                color = if (error != null && !controller.saving) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite },
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                OutlinedButton(onClick = { controller.discard() }, modifier = Modifier.weight(1f)) {
                    Text(stringResource(R.string.settings_discard_changes))
                }
                Button(
                    onClick = { scope.launch { controller.save() } },
                    enabled = !controller.saving,
                    modifier = Modifier.weight(1f),
                ) { Text(stringResource(R.string.settings_save_changes)) }
            }
        }
    }
}

// ---------------------------------------------------------------------
// Notifications section (WP 4b.8)
// ---------------------------------------------------------------------

/**
 * The one piece of UI this WP adds to Settings: an explicit way to trigger
 * the POST_NOTIFICATIONS runtime prompt on API 33+ (brief: "request from
 * Settings screen context"). Deliberately minimal -- a button plus context,
 * always shown regardless of current grant state or SDK level (checking the
 * live permission state here would need a `LocalLifecycleOwner` resume
 * observer to refresh after the user returns from the system permission
 * dialog or app-info screen; out of scope for this WP's "keep it simple"
 * instruction, and harmless to omit -- tapping an already-granted
 * permission's request re-shows nothing on Android, and below API 33 the
 * tap is a documented no-op, per `MainActivity`'s own
 * `requestNotificationPermission` kdoc).
 * The background poll itself needs no permission at all and keeps running
 * either way (`NotificationPollWorker`'s kdoc).
 */
@Composable
private fun NotificationsSection(onRequestNotificationPermission: () -> Unit) {
    Text(stringResource(R.string.settings_section_notifications), style = MaterialTheme.typography.titleMedium)
    Text(
        stringResource(R.string.settings_notifications_desc),
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        style = MaterialTheme.typography.bodySmall,
    )
    OutlinedButton(onClick = onRequestNotificationPermission) {
        Text(stringResource(R.string.settings_notifications_enable_button))
    }
}

// ---------------------------------------------------------------------
// PCs (agents) pointer (WP D15d; WP 4b.10 before)
// ---------------------------------------------------------------------

/**
 * WP D15d: managing PCs moved to the Clients page (`ui/clients/ClientsScreen.kt`,
 * a nav destination since D15), like web `settings.js`'s "PCs (agents)"
 * pointer. Settings keeps this short pointer so the old way there still
 * leads somewhere; [onOpenClientsClick] switches to the Clients
 * destination (`MainActivity`). Before D15d this button opened the PCs
 * sheet directly.
 */
@Composable
private fun ClientsSection(onOpenClientsClick: () -> Unit) {
    Text(stringResource(R.string.settings_section_clients), style = MaterialTheme.typography.titleMedium)
    Text(
        stringResource(R.string.settings_clients_desc),
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        style = MaterialTheme.typography.bodySmall,
    )
    OutlinedButton(onClick = onOpenClientsClick) {
        Text(stringResource(R.string.settings_clients_open_button))
    }
}

// ---------------------------------------------------------------------
// GET/PATCH /v1/settings form
// ---------------------------------------------------------------------

@Composable
private fun SettingsForm(controller: SettingsController) {
    val response = controller.settingsResponse ?: return
    val placed = SettingsLayout.placeEntries(response.settings)
    val entriesByKey = response.settings.associateBy { it.key }

    if (response.readonly) {
        Text(stringResource(R.string.settings_readonly_banner), style = MaterialTheme.typography.bodySmall)
    }

    // D16c: vault-core's own report on the last settings change, as one line.
    val applyLine = controller.coreApplyLine
    if (applyLine != null) {
        Text(
            applyLine.text,
            style = MaterialTheme.typography.bodySmall,
            color = if (applyLine.warn) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite },
        )
    }

    // D16: the normal sections, then the expert block (a switch, and the
    // expert groups only while it is on). Titles, labels and notes come from
    // SettingsLayout (twin of the web's settings-layout.js).
    val blocks = ArrayList<SettingsBlock>()
    for (section in SettingsLayout.SECTIONS) {
        if (!section.form || section.id == "expert") continue
        val rows = placed.sections[section.id].orEmpty()
        if (rows.isNotEmpty()) blocks.add(SettingsBlock(section.id, section.title, rows))
    }
    blocks.add(SettingsBlock("expert", "Expert settings", emptyList()))
    if (controller.expertOn) {
        for (group in SettingsLayout.EXPERT_GROUPS) {
            val rows = placed.expert[group.id].orEmpty()
            if (rows.isNotEmpty()) blocks.add(SettingsBlock("expert_" + group.id, group.title, rows))
        }
    }

    for (block in blocks) {
        val open = controller.isSectionOpen(block.id)
        SettingsSectionHeader(block.title, open) { controller.setSectionOpen(block.id, !open) }
        if (!open) continue
        if (block.id == "expert") {
            ExpertSwitchRow(controller.expertOn) { on -> controller.setExpert(on) }
        }
        for (entry in block.rows) {
            if (SettingsLayout.isPinned(entry)) {
                PinnedSettingRow(entry)
            } else if (entry.key == "auto_gc") {
                AutoGcField(entry, controller.drafts["auto_gc"], response.readonly) { v ->
                    controller.setDraft("auto_gc", SettingDraft.Text(v))
                }
            } else if (entry.key == "sweep_include_cached") {
                SweepIncludeCachedField(
                    entry = entry,
                    draft = controller.drafts["sweep_include_cached"],
                    readonly = response.readonly,
                    onSelect = { v -> controller.setDraft("sweep_include_cached", SettingDraft.Text(v)) },
                    onReset = { controller.resetDraft("sweep_include_cached") },
                )
            } else if (entry.key == "webhook_events") {
                WebhookEventsField(entry, controller.drafts["webhook_events"], response.readonly) { v ->
                    controller.setDraft("webhook_events", SettingDraft.EventsList(v))
                }
            } else {
                val key = entry.key
                SettingTextField(
                    entry = entry,
                    draft = controller.drafts[key],
                    label = SettingsLayout.fieldMeta(key).label,
                    placeholder = SettingsLayout.placeholder(key),
                    hint = SettingsLayout.offHint(key),
                    readonly = response.readonly,
                    onValueChange = { v -> controller.setDraft(key, SettingDraft.Text(v)) },
                    onReset = { controller.resetDraft(key) },
                )
            }
            SettingHelpRow(entry)
        }
        if (block.id == "window" && entriesByKey.containsKey("schedule_window")) {
            SweepStatusBlock(controller.schedule)
        }
    }

    // WP WEB-FIX-8: Save/Discard moved out of this scrolling form into the
    // Scaffold's bottom bar (SettingsSaveBar), so it is on screen without
    // scrolling while anything is dirty.
}

@Composable
private fun captionFor(entry: SettingInfoOut): String {
    val source = when (parseSettingsSource(entry.source)) {
        SettingsSource.DB -> stringResource(R.string.settings_source_db)
        SettingsSource.ENV -> stringResource(R.string.settings_source_env)
        SettingsSource.DEFAULT -> stringResource(R.string.settings_source_default)
        SettingsSource.UNKNOWN -> stringResource(R.string.settings_source_unknown)
    }
    val applies = when (parseSettingsApplies(entry.applies)) {
        SettingsApplies.IMMEDIATELY -> stringResource(R.string.settings_applies_immediately)
        SettingsApplies.NEXT_SWEEP -> stringResource(R.string.settings_applies_next_sweep)
        SettingsApplies.RESTART_REQUIRED -> stringResource(R.string.settings_applies_restart_required)
        SettingsApplies.UNSPECIFIED -> stringResource(R.string.settings_applies_unspecified)
    }
    return stringResource(R.string.settings_caption, source, applies)
}

/** One collapsible block of the form: a section or an expert group. */
private data class SettingsBlock(val id: String, val title: String, val rows: List<SettingInfoOut>)

@Composable
private fun SettingsSectionHeader(title: String, open: Boolean, onToggle: () -> Unit) {
    val description = if (open) {
        stringResource(R.string.settings_section_open, title)
    } else {
        stringResource(R.string.settings_section_closed, title)
    }
    TextButton(
        onClick = onToggle,
        modifier = Modifier.fillMaxWidth().semantics { contentDescription = description },
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(title, style = MaterialTheme.typography.titleMedium)
            Text(if (open) "-" else "+", style = MaterialTheme.typography.titleMedium)
        }
    }
}

@Composable
private fun ExpertSwitchRow(on: Boolean, onChange: (Boolean) -> Unit) {
    Column {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Switch(checked = on, onCheckedChange = onChange)
            Text(stringResource(R.string.settings_expert_switch))
        }
        Text(
            stringResource(R.string.settings_expert_hint),
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

/**
 * Env always wins, and the webhook URL is env-only: such a row is plain
 * text (no input), with the env note and, when a stored value is ignored
 * or overridden, the shadow note.
 */
@Composable
private fun PinnedSettingRow(entry: SettingInfoOut) {
    val label = SettingsLayout.fieldMeta(entry.key).label
    val shadow = SettingsLayout.shadowNote(entry)
    Column {
        Text(label, style = MaterialTheme.typography.bodyMedium)
        Text(SettingsLayout.displayValue(entry), style = MaterialTheme.typography.bodyLarge)
        Text(
            SettingsLayout.envNote(entry),
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        if (shadow != null) {
            Text(shadow, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

/** The (i) button of a row and, while open, its explanation paragraphs. */
@Composable
private fun SettingHelpRow(entry: SettingInfoOut) {
    val paragraphs = SettingsLayout.fieldHelp(entry)
    if (paragraphs.isEmpty()) return
    var open by rememberSaveable(entry.key) { mutableStateOf(false) }
    val label = SettingsLayout.fieldMeta(entry.key).label
    val description = if (open) {
        stringResource(R.string.settings_help_hide, label)
    } else {
        stringResource(R.string.settings_help_show, label)
    }
    Column {
        IconButton(
            onClick = { open = !open },
            modifier = Modifier.semantics { contentDescription = description },
        ) {
            AboutInfoGlyph(
                color = if (open) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        if (open) {
            for (paragraph in paragraphs) {
                Text(paragraph, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

/** The risky-confirm dialog: what changes, and what can break. Yes sends `?confirm_risky=true`. */
@Composable
private fun RiskyConfirmDialog(controller: SettingsController, scope: kotlinx.coroutines.CoroutineScope) {
    val items = controller.riskyPending ?: return
    AlertDialog(
        onDismissRequest = { controller.cancelRisky() },
        title = { Text(stringResource(R.string.settings_risky_title)) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(stringResource(R.string.settings_risky_intro))
                for (item in items) {
                    Text(
                        stringResource(R.string.settings_risky_line, item.label, item.from, item.to),
                        style = MaterialTheme.typography.bodyMedium,
                    )
                    Text(item.risk, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        },
        confirmButton = {
            TextButton(onClick = { scope.launch { controller.confirmRisky() } }) {
                Text(stringResource(R.string.settings_risky_confirm))
            }
        },
        dismissButton = {
            TextButton(onClick = { controller.cancelRisky() }) {
                Text(stringResource(R.string.settings_risky_cancel))
            }
        },
    )
}

@Composable
private fun SettingTextField(
    entry: SettingInfoOut,
    draft: SettingDraft?,
    label: String,
    readonly: Boolean,
    onValueChange: (String) -> Unit,
    onReset: () -> Unit,
    placeholder: String? = null,
    hint: String? = null,
) {
    val fieldValue = when (draft) {
        is SettingDraft.Text -> draft.value
        is SettingDraft.Reset -> effectiveAsFieldText(entry.copy(effective = entry.fallback))
        else -> effectiveAsFieldText(entry)
    }
    Column {
        OutlinedTextField(
            value = fieldValue,
            onValueChange = onValueChange,
            label = { Text(label) },
            placeholder = placeholder?.let { { Text(it) } },
            singleLine = true,
            enabled = !readonly,
            modifier = Modifier.fillMaxWidth(),
        )
        Text(captionFor(entry), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        hint?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
        if (!readonly && canResetSetting(entry)) {
            TextButton(onClick = onReset) { Text(stringResource(R.string.settings_reset)) }
        }
    }
}

@Composable
private fun AutoGcField(
    entry: SettingInfoOut,
    draft: SettingDraft?,
    readonly: Boolean,
    onSelect: (String) -> Unit,
) {
    val options = listOf(
        "off" to stringResource(R.string.settings_auto_gc_off),
        "dry-run" to stringResource(R.string.settings_auto_gc_dry_run),
        "execute" to stringResource(R.string.settings_auto_gc_execute),
    )
    val current = (draft as? SettingDraft.Text)?.value ?: effectiveAsFieldText(entry)
    Column {
        Text(stringResource(R.string.settings_auto_gc_label))
        SingleChoiceSegmentedButtonRow(modifier = Modifier.fillMaxWidth()) {
            options.forEachIndexed { index, (mode, label) ->
                SegmentedButton(
                    selected = mode == current,
                    onClick = { onSelect(mode) },
                    enabled = !readonly,
                    shape = SegmentedButtonDefaults.itemShape(index = index, count = options.size),
                ) { Text(label) }
            }
        }
        Text(captionFor(entry), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

/**
 * The `sweep_include_cached` toggle (WP AG-3) — structurally the same
 * segmented-button idiom [AutoGcField] above already establishes for a
 * small enumerated `GET`/`PATCH /v1/settings` choice, just two options
 * instead of three, and wire values `"true"`/`"false"` (api/README.md:
 * a JSON boolean literal 422s this key) rather than an enum's own words.
 * [SettingDraft.Text]/[buildSettingsPatchDraft]/`valueChanged` in
 * `SettingsDiff.kt` already treat every non-`webhook_events` key as plain
 * text, so this needs no changes there — verified by reading that file.
 */
@Composable
private fun SweepIncludeCachedField(
    entry: SettingInfoOut,
    draft: SettingDraft?,
    readonly: Boolean,
    onSelect: (String) -> Unit,
    onReset: () -> Unit,
) {
    val options = listOf(
        "false" to stringResource(R.string.settings_sweep_include_cached_off),
        "true" to stringResource(R.string.settings_sweep_include_cached_on),
    )
    val current = (draft as? SettingDraft.Text)?.value ?: effectiveAsFieldText(entry)
    Column {
        Text(stringResource(R.string.settings_sweep_include_cached_label))
        SingleChoiceSegmentedButtonRow(modifier = Modifier.fillMaxWidth()) {
            options.forEachIndexed { index, (value, label) ->
                SegmentedButton(
                    selected = value == current,
                    onClick = { onSelect(value) },
                    enabled = !readonly,
                    shape = SegmentedButtonDefaults.itemShape(index = index, count = options.size),
                ) { Text(label) }
            }
        }
        Text(captionFor(entry), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(
            stringResource(R.string.settings_sweep_include_cached_hint),
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        if (!readonly && canResetSetting(entry)) {
            TextButton(onClick = onReset) { Text(stringResource(R.string.settings_reset)) }
        }
    }
}

/**
 * The "did the last scheduled sweep actually do anything" line, plus the
 * "keeping the cache current without collecting" warning when the server
 * reports the risk condition (WP AG-3) — both pieces of text come from
 * `ui/settings/logic/SchedulePresentation.kt`, fed [schedule] verbatim:
 * neither is computed here from `sweep_include_cached`/`auto_gc` a second
 * time (see that module's kdoc). Renders nothing at all — not a
 * placeholder — while [schedule] is still `null` (no fetch yet, or the
 * fetch failed); this is a status readout, not a required part of the form.
 */
@Composable
private fun SweepStatusBlock(schedule: ScheduleOut?) {
    sweepTargetsMessage(schedule)?.let {
        Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
    cachedSweepGcRiskWarning(schedule)?.let {
        Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
    }
}

@Composable
private fun WebhookEventsField(
    entry: SettingInfoOut,
    draft: SettingDraft?,
    readonly: Boolean,
    onChange: (List<String>) -> Unit,
) {
    val options = listOf(
        "job.done" to stringResource(R.string.settings_webhook_event_job_done),
        "job.error" to stringResource(R.string.settings_webhook_event_job_error),
        "job.cancelled" to stringResource(R.string.settings_webhook_event_job_cancelled),
        "client.bypass_suspected" to stringResource(R.string.settings_webhook_event_bypass_suspected),
        "client.bypass_resolved" to stringResource(R.string.settings_webhook_event_bypass_resolved),
    )
    val current: Set<String> = when (draft) {
        is SettingDraft.EventsList -> draft.values.toSet()
        else -> effectiveAsFieldText(entry).split(",").map { it.trim() }.filter { it.isNotEmpty() }.toSet()
    }
    Column {
        Text(stringResource(R.string.settings_webhook_events_label))
        for ((value, label) in options) {
            Row {
                Checkbox(
                    checked = value in current,
                    enabled = !readonly,
                    onCheckedChange = { checked ->
                        onChange(if (checked) (current + value).toList() else (current - value).toList())
                    },
                )
                Text(label, modifier = Modifier.padding(top = 12.dp))
            }
        }
        Text(captionFor(entry), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

// ---------------------------------------------------------------------
// Steam identity section (sign-in state +, WP 4h.4, an on-demand library
// check against vault-api's relay -- there is no device-local key left to
// manage, see ADR-0004's second addendum)
// ---------------------------------------------------------------------

/**
 * WP APP-DEMO review round 2 (S1): [demoMode] gates the whole body of this
 * section, not just the sign-in BUTTON. `controller.identityState` reads
 * `CredentialStore` directly, which is the REAL, on-device store, unmodified
 * for demo mode (WP brief constraint 5) -- so a signed-in identity from a
 * PRIOR real session, or one established mid-onboarding right before the
 * user tapped "Skip for now" (Step 2's `identityRepository.completeLogin`
 * persists `steamId64` immediately, not deferred to `finish()`), is a real
 * value sitting in that store the instant this composable would otherwise
 * read [SettingsController.identityState] and render it. Since this
 * package's whole point is producing PUBLISHABLE screenshots, that render
 * path must not exist at all while [demoMode] is true -- not be merely
 * unlikely to be tapped. `demoMode` also disables the actions that WRITE
 * to the real store from here (sign-out, the library check that reads a
 * real steamid) for the same reason: none of them are legitimate while
 * browsing fixtures, and offering them invites exactly the confusion
 * constraint 1 already warns against, in the identity direction instead of
 * the data direction.
 */
@Composable
private fun SteamIdentitySection(
    controller: SettingsController,
    scope: kotlinx.coroutines.CoroutineScope,
    onSignInSteamClick: () -> Unit,
    demoMode: Boolean,
) {
    Text(stringResource(R.string.settings_section_steam), style = MaterialTheme.typography.titleMedium)

    if (demoMode) {
        Text(
            stringResource(R.string.settings_steam_unavailable_demo),
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            style = MaterialTheme.typography.bodySmall,
        )
        return
    }

    val identity: SteamIdentityState = controller.identityState

    if (!identity.isSignedIn) {
        controller.loginError?.let {
            Text(stringResource(R.string.identity_login_failed, it), color = MaterialTheme.colorScheme.error)
        }
        Button(onClick = onSignInSteamClick) { Text(stringResource(R.string.identity_sign_in)) }
    } else {
        Text(stringResource(R.string.identity_steamid_label, identity.steamId64.orEmpty()))
        Text(
            identity.personaName?.let { stringResource(R.string.identity_persona_label, it) }
                ?: stringResource(R.string.identity_persona_unknown),
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        OutlinedButton(onClick = { controller.signOutSteam() }) { Text(stringResource(R.string.identity_sign_out)) }

        OutlinedButton(
            onClick = { controller.checkSteamLibrary(scope) },
            enabled = !controller.libraryChecking,
        ) {
            Text(
                if (controller.libraryChecking) {
                    stringResource(R.string.settings_steam_library_checking)
                } else {
                    stringResource(R.string.settings_steam_library_check_button)
                },
            )
        }
        Text(
            steamLibraryStatusText(controller.libraryStatus),
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun steamLibraryStatusText(status: SteamLibraryStatus): String = when (status) {
    SteamLibraryStatus.Unknown -> stringResource(R.string.settings_steam_library_unknown)
    is SteamLibraryStatus.Ready ->
        pluralStringResource(R.plurals.settings_steam_library_count, status.gameCount, status.gameCount)
    SteamLibraryStatus.MaybePrivateOrEmpty -> stringResource(R.string.settings_steam_library_maybe_private)
    SteamLibraryStatus.RelayNotConfigured -> stringResource(R.string.settings_steam_library_not_configured)
    SteamLibraryStatus.InvalidSteamId -> stringResource(R.string.settings_steam_library_invalid_steamid)
    is SteamLibraryStatus.Failed -> stringResource(R.string.settings_steam_library_error, status.message)
}

// ---------------------------------------------------------------------
// Steam library block (WP APP-FEAT-1, parity with web WEB-FEAT-1/2)
// ---------------------------------------------------------------------

/**
 * The vault's ONE library SteamID64 (`steam_library_steamid`, WP
 * API-FEAT-1): the field is pre-filled from the stored setting, Save is its
 * own PATCH (independent of the shared Save bar), Reset deletes the
 * override, Preview looks up the TYPED id through the relay. The Library on
 * every device lists the games this id owns. Decisions live in
 * `ui/settings/logic/SteamLibrarySetting.kt`.
 *
 * Demo mode: works on the demo's own settings and relay fixture; only
 * "Use my signed-in SteamID64" is hidden, because it reads the REAL
 * on-device identity (same rule as [SteamIdentitySection]).
 */
@Composable
private fun SteamLibraryBlock(
    controller: SettingsController,
    scope: kotlinx.coroutines.CoroutineScope,
    demoMode: Boolean,
) {
    val response = controller.settingsResponse
    val entry = response?.settings?.firstOrNull { it.key == STEAM_LIBRARY_SETTING_KEY }
    Text(stringResource(R.string.settings_library_id_title), style = MaterialTheme.typography.titleSmall)
    Text(
        stringResource(R.string.settings_library_id_hint),
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
    OutlinedTextField(
        value = controller.libraryIdInput,
        onValueChange = { controller.libraryIdInput = it },
        label = { Text(stringResource(R.string.settings_library_id_label)) },
        placeholder = { Text("76561198042117903") },
        singleLine = true,
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number, autoCorrectEnabled = false),
        modifier = Modifier.fillMaxWidth(),
    )
    Text(
        if (entry != null) captionFor(entry) else stringResource(R.string.settings_library_id_absent_preview_works),
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
    controller.libraryIdError?.let { message ->
        Text(
            when (message) {
                LibraryIdMessage.ABSENT -> stringResource(R.string.settings_library_id_absent)
                LibraryIdMessage.ENV_ONLY -> stringResource(R.string.settings_library_id_env_only)
                LibraryIdMessage.READONLY -> stringResource(R.string.settings_library_id_readonly)
                LibraryIdMessage.INVALID -> stringResource(R.string.settings_library_id_invalid)
                LibraryIdMessage.SERVER -> controller.libraryIdServerDetail.orEmpty()
            },
            color = MaterialTheme.colorScheme.error,
            style = MaterialTheme.typography.bodySmall,
        )
    }
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        if (canSaveLibrarySteamId(response)) {
            Button(
                onClick = { scope.launch { controller.saveLibrarySteamId() } },
                enabled = !controller.libraryIdBusy,
            ) { Text(stringResource(R.string.settings_library_id_save)) }
        }
        if (canResetLibrarySteamId(response)) {
            TextButton(
                onClick = { scope.launch { controller.resetLibrarySteamId() } },
                enabled = !controller.libraryIdBusy,
            ) { Text(stringResource(R.string.settings_reset)) }
        }
        OutlinedButton(
            onClick = { scope.launch { controller.previewLibrarySteamId() } },
            enabled = !controller.libraryPreviewBusy,
        ) { Text(stringResource(R.string.settings_library_id_preview)) }
    }
    // Weg A (coordinator decision): the server setting is the only source.
    // The shortcut only fills the field; it is offered only where Save can
    // actually store it (not read-only, not env-only, not an older server
    // without the setting), and its hint says the change is vault-wide.
    if (!demoMode && canSaveLibrarySteamId(response)) {
        val signedIn = controller.identityState.steamId64
        if (signedIn != null && signedIn != controller.libraryIdInput.trim()) {
            TextButton(onClick = { controller.useSignedInSteamId() }) {
                Text(stringResource(R.string.settings_library_id_use_signed_in))
            }
            Text(
                stringResource(R.string.settings_library_id_use_signed_in_hint),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
    LibraryPreviewResult(controller.libraryPreview, controller.libraryPreviewError)
}

@Composable
private fun LibraryPreviewResult(preview: LibraryPreview?, error: LibraryLookupError?) {
    if (error != null) {
        Text(
            when (error) {
                LibraryLookupError.NoRelayKey -> stringResource(R.string.settings_library_id_no_relay_key)
                LibraryLookupError.InvalidSteamId -> stringResource(R.string.settings_library_id_invalid)
                is LibraryLookupError.Failed -> error.detail
            },
            color = MaterialTheme.colorScheme.error,
            style = MaterialTheme.typography.bodySmall,
        )
        return
    }
    if (preview == null) return
    preview.personaName?.let {
        Text(stringResource(R.string.settings_library_id_preview_persona, it), style = MaterialTheme.typography.bodySmall)
    }
    Text(
        pluralStringResource(R.plurals.settings_library_id_preview_count, preview.gameCount, preview.gameCount),
        style = MaterialTheme.typography.bodySmall,
    )
    if (preview.gameCount == 0) {
        Text(
            stringResource(R.string.settings_library_id_preview_private),
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
    for (name in preview.previewNames) {
        Text("• $name", style = MaterialTheme.typography.bodySmall)
    }
}

// ---------------------------------------------------------------------
// About section (WP APP-FEAT-2, GET /v1/about, WP VER-2)
// ---------------------------------------------------------------------

/**
 * One row per component (WP WEB-FIX-8 words: vault-core OK/Check/Not
 * reported against vault-api, vault-dns N/A, a dash for a value not
 * reported, explanations behind a per-row (i) button). A server without
 * `/v1/about` (404) gets the "server too old" note instead of an error. Presentation decisions:
 * `ui/settings/logic/AboutPresentation.kt`.
 */
@Composable
private fun AboutSection(controller: SettingsController, scope: kotlinx.coroutines.CoroutineScope) {
    Text(stringResource(R.string.settings_section_about), style = MaterialTheme.typography.titleMedium)
    when {
        controller.aboutFailure == AboutLoadFailure.TOO_OLD -> Text(
            stringResource(R.string.settings_about_too_old),
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        controller.aboutFailure == AboutLoadFailure.ERROR -> Text(
            stringResource(R.string.settings_about_error, controller.aboutErrorDetail.orEmpty()),
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.error,
        )
        controller.about == null -> Text(
            stringResource(R.string.settings_about_loading),
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        else -> {
            for (row in aboutRowsFor(controller.about?.components.orEmpty())) {
                AboutRowView(row)
            }
            Text(
                stringResource(R.string.settings_about_cache_note),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
    OutlinedButton(
        onClick = { scope.launch { controller.loadAbout() } },
        enabled = !controller.aboutLoading,
    ) { Text(stringResource(R.string.settings_about_refresh)) }
    Text(
        stringResource(R.string.settings_about_trademark),
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

/**
 * One About row (WP WEB-FIX-8, twin of web settings.js `buildAboutTable`):
 * name + (i) button + status word, then the version line (a dash for a
 * value not reported, read as "Not reported" by TalkBack), then -- only
 * while the (i) is open -- the note, the dash reason and the server's
 * detail. Collapsed by default except for a CHECK row (open, so the
 * reason is in view); the open state survives rotation (rememberSaveable,
 * keyed by component name).
 */
@Composable
private fun AboutRowView(row: AboutRow) {
    val dash = stringResource(R.string.settings_about_dash)
    val dashLabel = stringResource(R.string.settings_about_dash_label)
    val statusWord = when (row.display) {
        AboutDisplayStatus.OK -> stringResource(R.string.settings_about_status_ok)
        AboutDisplayStatus.UNREACHABLE -> stringResource(R.string.settings_about_status_unreachable)
        AboutDisplayStatus.NOT_IN_USE -> stringResource(R.string.settings_about_status_not_in_use)
        AboutDisplayStatus.CHECK -> stringResource(R.string.settings_about_status_check)
        AboutDisplayStatus.NOT_REPORTED -> stringResource(R.string.settings_about_status_not_reported)
        AboutDisplayStatus.NOT_APPLICABLE -> stringResource(R.string.settings_about_status_not_applicable)
    }
    val note = when (row.note) {
        AboutNote.VAULT_API -> stringResource(R.string.settings_about_note_vault_api)
        AboutNote.VAULT_CORE -> stringResource(R.string.settings_about_note_vault_core)
        AboutNote.CORE_SAME_RELEASE -> stringResource(R.string.settings_about_note_core_same_release)
        AboutNote.CORE_MISMATCH -> stringResource(R.string.settings_about_note_core_mismatch)
        AboutNote.CORE_NOT_COMPARABLE -> stringResource(R.string.settings_about_note_core_not_comparable)
        AboutNote.CORE_NOT_REPORTED -> stringResource(R.string.settings_about_note_core_not_reported)
        AboutNote.VAULT_RUNNER -> stringResource(R.string.settings_about_note_vault_runner)
        AboutNote.STEAMPREFILL -> stringResource(R.string.settings_about_note_steamprefill)
        AboutNote.VAULT_PROXY -> stringResource(R.string.settings_about_note_vault_proxy)
        AboutNote.VAULT_DNS -> stringResource(R.string.settings_about_note_vault_dns)
        null -> null
    }
    val dashNote = if (row.showDashNote) stringResource(R.string.settings_about_dash_note) else null
    // WP WEB-FIX-8 review: a CHECK row opens by default so a fault is never
    // hidden behind the (i); every other row starts collapsed.
    var expanded by rememberSaveable(row.name) { mutableStateOf(row.infoOpenByDefault) }
    val infoDescription = if (expanded) {
        stringResource(R.string.settings_about_info_hide, row.name)
    } else {
        stringResource(R.string.settings_about_info_show, row.name)
    }
    val versionLine = stringResource(R.string.settings_about_version_line, row.version ?: dash, row.commit ?: dash)
    val versionLineSpoken = stringResource(
        R.string.settings_about_version_line,
        row.version ?: dashLabel,
        row.commit ?: dashLabel,
    )
    Column(modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Row(modifier = Modifier.weight(1f), verticalAlignment = Alignment.CenterVertically) {
                Text(row.name, style = MaterialTheme.typography.bodyMedium)
                if (row.hasInfo) {
                    IconButton(
                        onClick = { expanded = !expanded },
                        modifier = Modifier.semantics { contentDescription = infoDescription },
                    ) {
                        AboutInfoGlyph(
                            color = if (expanded) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
            Text(
                statusWord,
                style = MaterialTheme.typography.labelMedium,
                color = when (row.display) {
                    AboutDisplayStatus.OK -> VaultColors.StatusOk
                    AboutDisplayStatus.UNREACHABLE -> MaterialTheme.colorScheme.error
                    else -> MaterialTheme.colorScheme.onSurfaceVariant
                },
            )
        }
        Text(
            versionLine,
            style = MaterialTheme.typography.bodySmall,
            modifier = Modifier.semantics { contentDescription = versionLineSpoken },
        )
        if (expanded) {
            for (paragraph in listOfNotNull(note, dashNote, row.detail)) {
                Text(paragraph, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

/** The (i) glyph: outline circle with an "i", drawn like the nav icons
 * (24-unit grid, round caps); decorative -- the IconButton carries the
 * name. Same shape as the web's `infoGlyph()` in views/settings.js. */
@Composable
private fun AboutInfoGlyph(color: Color) {
    Canvas(Modifier.size(18.dp)) {
        val scale = size.minDimension / 24f
        val stroke = Stroke(width = 2f * scale, cap = StrokeCap.Round)
        drawCircle(color = color, radius = 9f * scale, center = Offset(12f * scale, 12f * scale), style = stroke)
        drawLine(color, Offset(12f * scale, 11f * scale), Offset(12f * scale, 17f * scale), strokeWidth = 2f * scale, cap = StrokeCap.Round)
        drawLine(color, Offset(12f * scale, 7.5f * scale), Offset(12f * scale, 7.51f * scale), strokeWidth = 2f * scale, cap = StrokeCap.Round)
    }
}

// ---------------------------------------------------------------------
// Connection section
// ---------------------------------------------------------------------

@Composable
private fun ConnectionSection(
    controller: SettingsController,
    onReconnectClick: () -> Unit,
    onDisconnected: () -> Unit,
    demoMode: Boolean,
) {
    var showDisconnectConfirm by remember { mutableStateOf(false) }
    Text(stringResource(R.string.settings_section_connection), style = MaterialTheme.typography.titleMedium)

    // WP APP-DEMO: reuses the SAME Reconnect/Disconnect actions below rather
    // than adding a dedicated "exit demo mode" control (WP brief constraint
    // 6: prefer the existing seam over a screen special case) -- Reconnect
    // opens onboarding where a real connection can be entered and tested,
    // and MainActivity.refreshVaultApiClient() (run after either finishes)
    // unconditionally clears demo state as part of the same rebuild, so
    // either button already leaves demo mode cleanly (WP brief constraint
    // 4). This one line is the only demo-aware text in this section.
    if (demoMode) {
        Text(
            stringResource(R.string.settings_demo_mode_note),
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            style = MaterialTheme.typography.bodySmall,
        )
    }

    val summary = controller.connectionSummary()
    if (summary.isConfigured) {
        val profileLabel = when (summary.profileKind) {
            ProfileKind.PUBLIC_DOMAIN -> stringResource(R.string.onboarding_profile_public_domain)
            else -> stringResource(R.string.onboarding_profile_system_vpn)
        }
        Text(stringResource(R.string.settings_connection_current, summary.baseUrl.orEmpty(), profileLabel))
    }

    Text(stringResource(R.string.settings_reconnect_title), style = MaterialTheme.typography.titleSmall)
    Text(stringResource(R.string.settings_reconnect_desc), color = MaterialTheme.colorScheme.onSurfaceVariant)
    OutlinedButton(onClick = onReconnectClick) { Text(stringResource(R.string.settings_reconnect_button)) }

    // WP APP-DEMO review round 2 (S1): Disconnect calls
    // CredentialStore.clear() -- the WHOLE store, including Steam identity.
    // While demoMode is true there is nothing REAL for this screen to have
    // connected (demo mode is only reachable with no working connection),
    // but CredentialStore can still hold a real Steam identity persisted
    // moments earlier (onboarding Step 2 persists on sign-in, before
    // `finish()`) -- offering a destructive action over that store here
    // would let a demo-mode tap silently wipe it. Reconnect above stays:
    // it is the documented way to leave demo mode and never touches
    // CredentialStore until a connection is actually verified and finished.
    if (!demoMode) {
        Text(stringResource(R.string.settings_disconnect_title), style = MaterialTheme.typography.titleSmall)
        Text(stringResource(R.string.settings_disconnect_desc), color = MaterialTheme.colorScheme.onSurfaceVariant)
        OutlinedButton(onClick = { showDisconnectConfirm = true }) { Text(stringResource(R.string.settings_disconnect_button)) }
    } else {
        Text(stringResource(R.string.settings_disconnect_unavailable_demo), color = MaterialTheme.colorScheme.onSurfaceVariant)
    }

    if (showDisconnectConfirm) {
        AlertDialog(
            onDismissRequest = { showDisconnectConfirm = false },
            title = { Text(stringResource(R.string.settings_disconnect_confirm_title)) },
            text = { Text(stringResource(R.string.settings_disconnect_confirm_body)) },
            confirmButton = {
                TextButton(onClick = {
                    showDisconnectConfirm = false
                    controller.disconnect()
                    onDisconnected()
                }) { Text(stringResource(R.string.settings_disconnect_confirm_confirm)) }
            },
            dismissButton = {
                TextButton(onClick = { showDisconnectConfirm = false }) {
                    Text(stringResource(R.string.settings_disconnect_confirm_cancel))
                }
            },
        )
    }
}
