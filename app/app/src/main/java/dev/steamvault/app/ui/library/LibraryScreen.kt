package dev.steamvault.app.ui.library

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Snackbar
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
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
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.repeatOnLifecycle
import dev.steamvault.app.R
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.repo.CacheRepository
import dev.steamvault.app.repo.GamesRepository
import dev.steamvault.app.repo.JobsRepository
import dev.steamvault.app.repo.MappingRepository
import dev.steamvault.app.storage.LibraryPreferences
import dev.steamvault.app.ui.demo.DemoModeBanner
import dev.steamvault.app.ui.detail.AndroidDetailStrings
import dev.steamvault.app.ui.detail.DetailController
import dev.steamvault.app.ui.detail.GameDetailSheet
import dev.steamvault.app.ui.library.logic.ChipCount
import dev.steamvault.app.ui.library.logic.GameCardModel
import dev.steamvault.app.ui.library.logic.LibraryLayout
import dev.steamvault.app.ui.library.logic.StatusActionType
import dev.steamvault.app.ui.library.logic.buildGameCardModel
import dev.steamvault.app.ui.library.logic.chipCounts
import dev.steamvault.app.ui.library.logic.indexLiveJobsByAppid
import dev.steamvault.app.ui.library.logic.isKnownToVault
import dev.steamvault.app.ui.library.logic.OwnedLibraryState
import dev.steamvault.app.ui.library.logic.OwnedNotice
import dev.steamvault.app.ui.library.logic.mergeLibrary
import dev.steamvault.app.ui.library.logic.noticeOffersReload
import dev.steamvault.app.ui.library.logic.noticeOffersSettings
import dev.steamvault.app.ui.library.logic.ownedNoticeFor
import dev.steamvault.app.ui.library.logic.normalizeQuery
import dev.steamvault.app.ui.library.logic.visibleGames
import dev.steamvault.app.ui.theme.VaultColors
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * The Library screen (WP 4b.4 brief): grid/list layouts, search + chips,
 * multi-select with bulk download/delete. State/orchestration lives in
 * [LibraryController] (kept thin per the brief); this file is rendering
 * plus the two foreground-only poll loops
 * ([LibraryController.pollGamesForever]/[LibraryController.pollJobsForever]),
 * gated by [Lifecycle.State.STARTED] via `repeatOnLifecycle` so polling
 * stops the moment the app leaves the foreground and resumes when it
 * returns -- no WorkManager involved (that is WP 4b.8's job for
 * background delivery).
 *
 * The detail sheet ([onOpen] callers) is WP 4b.6 -- tapping a card outside
 * multi-select is deliberately a no-op for now, the same placeholder
 * decision `web/js/views/library.js`'s `onOpen` documents for its own
 * not-yet-shipped WP.
 *
 * @param onJobsSnapshot fired whenever this screen's own jobs poll ticks
 *   (WP 4b.5 addition) -- lets `MainActivity` keep the Downloads nav pip
 *   live while Library, not Downloads, is the screen currently polling
 *   `GET /v1/jobs`. See `MainActivity.kt`'s kdoc for the honest scope
 *   limitation this implies.
 * @param demoMode WP APP-DEMO: `true` when [gamesRepository] et al. are the
 *   in-memory `dev.steamvault.app.demo` fixtures rather than real
 *   `Vault*Repository` instances -- this screen never branches on it for
 *   DATA (the repositories already differ upstream, `MainActivity.kt`'s
 *   wiring), only to render [DemoModeBanner] (WP brief constraint 1).
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun LibraryScreen(
    gamesRepository: GamesRepository,
    jobsRepository: JobsRepository,
    mappingRepository: MappingRepository,
    cacheRepository: CacheRepository,
    ownedLibrary: OwnedLibraryController,
    libraryPreferences: LibraryPreferences,
    onJobsSnapshot: (List<JobSummary>) -> Unit = {},
    onOpenSettings: () -> Unit = {},
    demoMode: Boolean,
) {
    val scope = rememberCoroutineScope()
    val resources = LocalContext.current.resources
    val controller = remember {
        LibraryController(
            gamesRepository,
            jobsRepository,
            mappingRepository,
            cacheRepository,
            libraryPreferences,
            AndroidLibraryStrings(resources),
        )
    }
    // WP 4b.6: the detail sheet opened from a library card. Shares this
    // screen's lifecycle/repos rather than owning its own -- it is only
    // ever reachable from here (see `GameDetailSheet.kt`'s kdoc).
    val detailController = remember {
        DetailController(
            gamesRepository,
            jobsRepository,
            mappingRepository,
            cacheRepository,
            AndroidDetailStrings(resources),
        )
    }

    val lifecycleOwner = LocalLifecycleOwner.current
    LaunchedEffect(lifecycleOwner, controller) {
        lifecycleOwner.lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            controller.pollGamesForever()
        }
    }
    LaunchedEffect(lifecycleOwner, controller) {
        lifecycleOwner.lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            controller.pollJobsForever()
        }
    }
    // WP APP-FEAT-1: the owned list is the vault's stored library SteamID64
    // (GET /v1/settings) plus the relay, read on every Library open (this
    // effect) and on the notice's Reload -- never on the poll interval, the
    // relay calls Steam. Same account on every device (web WEB-FEAT-1).
    LaunchedEffect(ownedLibrary) { ownedLibrary.load() }
    LaunchedEffect(controller.jobs) { onJobsSnapshot(controller.jobs) }

    val ownedState = ownedLibrary.state
    val merged = remember(controller.games, ownedState) {
        mergeLibrary(controller.games, ownedState.ownedGamesOrNull)
    }
    val liveJobsByAppid = remember(controller.jobs) { indexLiveJobsByAppid(controller.jobs) }
    val normalizedQuery = remember(controller.query) { normalizeQuery(controller.query) }
    val visible = remember(merged, normalizedQuery, controller.filterKey, liveJobsByAppid) {
        visibleGames(merged, normalizedQuery, controller.filterKey, liveJobsByAppid)
    }
    val counts = remember(merged, normalizedQuery, liveJobsByAppid) {
        chipCounts(merged, normalizedQuery, liveJobsByAppid)
    }
    val cardModels = remember(visible, liveJobsByAppid, controller.selectedAppids, controller.selecting) {
        visible.map { g ->
            buildGameCardModel(
                g,
                liveJobsByAppid[g.appid],
                selected = g.appid in controller.selectedAppids,
                selecting = controller.selecting,
            )
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(title = { Text(stringResource(R.string.library_title)) })
        },
        containerColor = MaterialTheme.colorScheme.background,
        snackbarHost = {
            controller.toast?.let { toastState ->
                LaunchedEffect(toastState) {
                    delay(toastState.durationMs)
                    controller.dismissToast()
                }
                if (toastState.warn) {
                    // S2 fix (Opus review on this WP): `colorScheme.errorContainer`/
                    // `onErrorContainer` are NOT in Theme.kt's VaultDarkColorScheme
                    // (only `error`/`onError` are defined there), so the original
                    // version of this branch silently fell back to Material 3's
                    // BASELINE red (#8C1D18/#F9DEDC) -- outside the frozen, literal-
                    // pinned palette, and wrong on MEANING too: a paused dedupe is a
                    // partial success ("N queued... N paused, resume or cancel it
                    // first"), not an error, and painting it full-red says the
                    // opposite. web's own warn toast (`.toast.warn .toast-key`,
                    // theme.css) does not recolour the whole surface either -- it
                    // keeps the normal toast background and swaps a 3px leading
                    // accent bar from `--accent` to `--stale` (VaultColors.StatusStale
                    // here). Ported as the same leading stripe, on the default
                    // (unchanged) Snackbar surface, so "warn" reads as "pay
                    // attention", never as "failed".
                    Snackbar(modifier = Modifier.padding(12.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Box(
                                modifier = Modifier
                                    .width(3.dp)
                                    .height(20.dp)
                                    .background(VaultColors.StatusStale, RoundedCornerShape(2.dp)),
                            )
                            Spacer(modifier = Modifier.width(8.dp))
                            Text(toastState.message)
                        }
                    }
                } else {
                    Snackbar(modifier = Modifier.padding(12.dp)) { Text(toastState.message) }
                }
            }
        },
        bottomBar = {
            if (controller.selecting) {
                LibraryBulkBar(controller = controller, allGames = merged, scope = scope)
            }
        },
    ) { innerPadding ->
        Column(modifier = Modifier.fillMaxSize().padding(innerPadding)) {
            if (demoMode) DemoModeBanner()
            LibraryToolbar(controller = controller)
            CheckAndUpdateRow(controller = controller, scope = scope)
            FilterChipsRow(counts = counts, selectedKey = controller.filterKey, onSelect = { controller.filterKey = it })
            OwnedNoticeRow(
                state = ownedState,
                onOpenSettings = onOpenSettings,
                onReload = { scope.launch { ownedLibrary.load() } },
            )

            controller.loadError?.let { error ->
                Text(
                    text = stringResource(R.string.library_load_error, error),
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(12.dp),
                )
            }

            if (cardModels.isEmpty()) {
                EmptyState(query = controller.query, hasFilter = controller.filterKey != "all")
            } else {
                LibraryGrid(
                    layout = controller.layout,
                    models = cardModels,
                    selecting = controller.selecting,
                    onOpen = { appid ->
                        val game = merged.firstOrNull { it.appid == appid }
                        detailController.open(scope, appid, game?.name, game?.let { isKnownToVault(it) } ?: false)
                    },
                    onLongPress = { controller.enterSelect(it) },
                    onToggleSelect = { controller.toggleSelect(it) },
                    onAction = { appid, actionType -> controller.onCardAction(scope, appid, actionType) },
                )
            }
        }
    }

    if (controller.deletePlan !is DeletePlanUiState.Hidden) {
        DeleteConfirmDialog(controller = controller, scope = scope)
    }

    if (detailController.openAppid != null) {
        GameDetailSheet(
            controller = detailController,
            games = controller.games,
            jobs = controller.jobs,
            scope = scope,
            onLibraryChanged = {
                scope.launch { controller.refreshGamesOnce() }
                scope.launch { controller.refreshJobsOnce() }
            },
            demoMode = demoMode,
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun LibraryToolbar(controller: LibraryController) {
    Column(modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(
                value = controller.query,
                onValueChange = { controller.query = it },
                modifier = Modifier.weight(1f),
                singleLine = true,
                placeholder = { Text(stringResource(R.string.library_search_hint)) },
                trailingIcon = {
                    if (controller.query.isNotEmpty()) {
                        TextButton(onClick = { controller.query = "" }) {
                            Text(stringResource(R.string.library_search_clear))
                        }
                    }
                },
            )
        }
        Row(
            modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            LayoutToggle(current = controller.layout, onSelect = { controller.selectLayout(it) })
            val selectModeDescription = if (controller.selecting) {
                stringResource(R.string.library_select_mode_exit)
            } else {
                stringResource(R.string.library_select_mode)
            }
            IconButton(
                onClick = { if (controller.selecting) controller.exitSelect() else controller.enterSelect() },
                modifier = Modifier.semantics { contentDescription = selectModeDescription },
            ) {
                // Plain glyphs, not an icon-pack lookup (same house style as
                // ui/nav/NavIcons.kt) -- the real a11y label lives on the
                // IconButton's own semantics above, not on this glyph text.
                Text(if (controller.selecting) "✕" else "☐")
            }
        }
    }
}

/**
 * "Check & update all cached games" (Phase 4c, WP 4c-app) — the Android
 * twin of `web/js/views/library.js`'s header row below `.lib-head`
 * (WP 4c-web, `docs/WORKPACKAGES.md`'s recorded divergence: the frozen
 * round-7 mockup has no equivalent control at all, since Phase 4c is itself
 * post-mockup scope). **Stated precisely, not as "full-width" (Opus review
 * on this WP):** this row's `fillMaxWidth()` CONTAINER spans the width, but
 * the button inside it is end-aligned and only as wide as its own label —
 * unlike web's own button, which IS `width:100%`. Placement also differs
 * from web's (web: tools → check row → search; Android: search →
 * layout/select → check row). A SEPARATE control from this screen's poll
 * loops — never folded into a pull-to-refresh gesture, which this screen
 * does not have to begin with (no `SwipeRefresh`/`pull-refresh` anywhere in
 * `ui/library/`); the same "a refresh gesture that can start downloads is a
 * trap" rule the web divergence entry records applies here too, it is just
 * satisfied by omission rather than by an explicit carve-out.
 *
 * The wording/error/in-flight logic itself lives in
 * `ui/library/logic/CachedPrefillOutcome.kt` — this composable only paints
 * [LibraryController.checkAndUpdateBusy] and forwards the click.
 */
@Composable
private fun CheckAndUpdateRow(controller: LibraryController, scope: CoroutineScope) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp),
        horizontalArrangement = Arrangement.End,
    ) {
        OutlinedButton(
            enabled = !controller.checkAndUpdateBusy,
            onClick = { controller.checkAndUpdateCachedGames(scope) },
        ) {
            Text(
                if (controller.checkAndUpdateBusy) {
                    stringResource(R.string.library_check_update_button_busy)
                } else {
                    stringResource(R.string.library_check_update_button)
                },
            )
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun LayoutToggle(current: LibraryLayout, onSelect: (LibraryLayout) -> Unit) {
    val options = listOf(
        LibraryLayout.GRID_2 to stringResource(R.string.library_layout_grid2),
        LibraryLayout.GRID_3 to stringResource(R.string.library_layout_grid3),
        LibraryLayout.LIST to stringResource(R.string.library_layout_list),
    )
    SingleChoiceSegmentedButtonRow {
        options.forEachIndexed { index, (layout, label) ->
            SegmentedButton(
                selected = current == layout,
                onClick = { onSelect(layout) },
                shape = SegmentedButtonDefaults.itemShape(index = index, count = options.size),
                label = { Text(label) },
            )
        }
    }
}

@Composable
private fun FilterChipsRow(
    counts: List<ChipCount>,
    selectedKey: String,
    onSelect: (String) -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp, vertical = 4.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        for (chip in counts) {
            FilterChip(
                selected = chip.key == selectedKey,
                onClick = { onSelect(chip.key) },
                label = { Text("${chip.label} ${chip.count}") },
            )
        }
    }
}

@Composable
private fun EmptyState(query: String, hasFilter: Boolean) {
    Box(modifier = Modifier.fillMaxSize().padding(24.dp), contentAlignment = Alignment.Center) {
        Text(
            text = if (query.isNotEmpty()) {
                if (hasFilter) {
                    stringResource(R.string.library_empty_query_filtered, query)
                } else {
                    stringResource(R.string.library_empty_query, query)
                }
            } else {
                stringResource(R.string.library_empty_no_filter)
            },
            textAlign = TextAlign.Center,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun LibraryGrid(
    layout: LibraryLayout,
    models: List<GameCardModel>,
    selecting: Boolean,
    onOpen: (Int) -> Unit,
    onLongPress: (Int) -> Unit,
    onToggleSelect: (Int) -> Unit,
    onAction: (Int, StatusActionType) -> Unit,
) {
    // `key = { it.appid }` is the item-identity half of the animation-
    // preservation mechanism `GameCardModel.kt`'s kdoc documents -- a poll
    // tick reorders/refreshes the underlying List, but each row's Compose
    // slot stays matched to the SAME appid across ticks, which is what lets
    // "did the model change" (structural equality) decide whether that
    // row's composition is re-entered at all.
    when (layout) {
        LibraryLayout.LIST -> LazyColumn(contentPadding = PaddingValues(vertical = 4.dp)) {
            items(models, key = { it.appid }) { model ->
                GameListRow(
                    model = model,
                    selecting = selecting,
                    onOpen = onOpen,
                    onLongPress = onLongPress,
                    onToggleSelect = onToggleSelect,
                    onAction = onAction,
                )
            }
        }
        LibraryLayout.GRID_2, LibraryLayout.GRID_3 -> {
            val columns = if (layout == LibraryLayout.GRID_2) 2 else 3
            LazyVerticalGrid(
                columns = GridCells.Fixed(columns),
                contentPadding = PaddingValues(8.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                items(models, key = { it.appid }) { model ->
                    GameCard(
                        model = model,
                        selecting = selecting,
                        onOpen = onOpen,
                        onLongPress = onLongPress,
                        onToggleSelect = onToggleSelect,
                        onAction = onAction,
                    )
                }
            }
        }
    }
}


/**
 * WP APP-FEAT-1: the one line under the toolbar about the owned list (web
 * `owned-library.js::ownedNotice`). Nothing when the owned list loaded
 * with games. A relay that returned 0 games is almost always a private
 * profile, so it never reads "0 owned". Every variant leaves the vault
 * games on screen.
 */
@Composable
private fun OwnedNoticeRow(state: OwnedLibraryState, onOpenSettings: () -> Unit, onReload: () -> Unit) {
    val notice = ownedNoticeFor(state) ?: return
    val detail = state.errorDetail.orEmpty()
    val text = when (notice) {
        OwnedNotice.LOADING -> stringResource(R.string.library_owned_loading)
        OwnedNotice.UNSET -> stringResource(R.string.library_owned_unset)
        OwnedNotice.SERVER_TOO_OLD -> stringResource(R.string.library_owned_server_too_old)
        OwnedNotice.NO_RELAY_KEY -> stringResource(R.string.library_owned_no_relay_key)
        OwnedNotice.STORED_ID_REJECTED -> stringResource(R.string.library_owned_stored_id_rejected)
        OwnedNotice.FAILED -> stringResource(R.string.library_owned_failed, detail)
        OwnedNotice.PRIVATE_OR_EMPTY -> stringResource(R.string.library_owned_private)
    }
    val warn = notice != OwnedNotice.LOADING && notice != OwnedNotice.UNSET
    Column(modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp)) {
        Text(
            text = text,
            style = MaterialTheme.typography.bodySmall,
            color = if (warn) VaultColors.StatusStale else MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (noticeOffersSettings(notice)) {
                TextButton(onClick = onOpenSettings) { Text(stringResource(R.string.library_owned_action_settings)) }
            }
            if (noticeOffersReload(notice)) {
                TextButton(onClick = onReload, enabled = !state.loading) {
                    Text(stringResource(R.string.library_owned_action_reload))
                }
            }
        }
    }
}
