package dev.steamvault.app.ui.library

import androidx.compose.foundation.background
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.withStyle
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.semantics.stateDescription
import dev.steamvault.app.ui.library.logic.MetaFilter
import androidx.compose.foundation.horizontalScroll
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
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
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
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
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
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.repeatOnLifecycle
import dev.steamvault.app.R
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.repo.CacheRepository
import dev.steamvault.app.repo.GamesRepository
import dev.steamvault.app.repo.JobsRepository
import dev.steamvault.app.repo.CardInfoRepository
import dev.steamvault.app.repo.MappingRepository
import dev.steamvault.app.repo.OffCardInfoRepository
import dev.steamvault.app.repo.UpdatesRepository
import dev.steamvault.app.storage.LibraryPreferences
import dev.steamvault.app.ui.demo.DemoModeBanner
import dev.steamvault.app.ui.detail.AndroidDetailStrings
import dev.steamvault.app.ui.detail.DetailController
import dev.steamvault.app.ui.detail.GameDetailSheet
import dev.steamvault.app.ui.library.logic.ChipCount
import dev.steamvault.app.ui.library.logic.GameCardModel
import dev.steamvault.app.ui.library.logic.LibraryLayout
import dev.steamvault.app.ui.library.logic.LibrarySort
import dev.steamvault.app.ui.library.logic.LibrarySortKey
import dev.steamvault.app.ui.library.logic.LibrarySortWording
import dev.steamvault.app.ui.library.logic.MetaFilterResult
import dev.steamvault.app.ui.library.logic.SortDirection
import dev.steamvault.app.ui.library.logic.applyMetaFilter
import dev.steamvault.app.ui.library.logic.availableSortKeys
import dev.steamvault.app.ui.library.logic.lastPlayedAvailable
import dev.steamvault.app.ui.library.logic.metaAvailable
import dev.steamvault.app.ui.library.logic.normalizeSort
import dev.steamvault.app.ui.library.logic.sortAfterKeyPick
import dev.steamvault.app.ui.library.logic.ownedLastPlayedByAppid
import dev.steamvault.app.ui.library.logic.sortGames
import dev.steamvault.app.ui.library.logic.StatusActionType
import dev.steamvault.app.ui.library.logic.buildGameCardModel
import dev.steamvault.app.ui.library.logic.chipCounts
import dev.steamvault.app.ui.library.logic.groupGames
import dev.steamvault.app.ui.library.logic.ownedPlaytimeByAppid
import dev.steamvault.app.ui.library.logic.hiddenChipKeys
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
 * @param updatesRepository WP UPD-3: "Check updates" (top app bar action)
 *   and the probe that hides it, and the "Updates" chip, on a vault from
 *   before UPD-1.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun LibraryScreen(
    gamesRepository: GamesRepository,
    jobsRepository: JobsRepository,
    mappingRepository: MappingRepository,
    cacheRepository: CacheRepository,
    updatesRepository: UpdatesRepository,
    ownedLibrary: OwnedLibraryController,
    libraryPreferences: LibraryPreferences,
    cardInfoRepository: CardInfoRepository = OffCardInfoRepository,
    onJobsSnapshot: (List<JobSummary>) -> Unit = {},
    onOpenSettings: () -> Unit = {},
    demoMode: Boolean,
    /** WP ANDROID-DL-2: the detail sheet's "Show job" after a repair (the
     * caller switches to Downloads and scrolls to the job). */
    onShowJob: (Int) -> Unit = {},
) {
    val scope = rememberCoroutineScope()
    val resources = LocalContext.current.resources
    val controller = remember {
        LibraryController(
            gamesRepository,
            jobsRepository,
            mappingRepository,
            cacheRepository,
            updatesRepository,
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
            cardInfoRepository,
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
    // WP UPD-3: one status read per Library open; a 404 hides the update
    // action and the "Updates" chip.
    LaunchedEffect(controller) { controller.probeUpdatesSupport() }
    LaunchedEffect(controller.jobs) { onJobsSnapshot(controller.jobs) }

    val ownedState = ownedLibrary.state
    val merged = remember(controller.games, ownedState) {
        mergeLibrary(controller.games, ownedState.ownedGamesOrNull)
    }
    // WP TOOL-APPS-2d (ADR-0022): content packages are grouped under their
    // game -- one card per group, the packages listed in the detail sheet.
    // `merged` stays the raw (ungrouped) list for every per-app decision.
    val grouped = remember(merged) { groupGames(merged) }
    val playtime = remember(ownedState) { ownedPlaytimeByAppid(ownedState.ownedGamesOrNull) }
    val liveJobsByAppid = remember(controller.jobs) { indexLiveJobsByAppid(controller.jobs) }
    val normalizedQuery = remember(controller.query) { normalizeQuery(controller.query) }
    // WP LIB-SORT: "Last played" exists only while the relay sends it
    // (relay_expose_last_played); a stored choice falls back to Name A-Z.
    val lastPlayed = remember(ownedState) { ownedLastPlayedByAppid(ownedState.ownedGamesOrNull) }
    val sortAvailable = lastPlayedAvailable(lastPlayed)
    // WP D23: rating/year sort and the metadata filter panel exist only while
    // some game carries stored metadata; a stored choice is a no-op otherwise.
    val hasMeta = remember(controller.games) { metaAvailable(controller.games) }
    val activeSort = normalizeSort(controller.sort, sortAvailable, hasMeta)
    val metaResult = remember(grouped, controller.metaFilter, hasMeta) {
        if (hasMeta) applyMetaFilter(grouped.rows, controller.metaFilter) else MetaFilterResult(grouped.rows, 0)
    }
    val visible = remember(metaResult, grouped, normalizedQuery, controller.filterKey, liveJobsByAppid, activeSort, lastPlayed) {
        sortGames(
            visibleGames(metaResult.games, normalizedQuery, controller.filterKey, liveJobsByAppid, grouped.packagesByParent),
            activeSort,
            liveJobsByAppid,
            grouped.packagesByParent,
            lastPlayed,
        )
    }
    val counts = remember(metaResult, grouped, normalizedQuery, liveJobsByAppid, controller.updatesSupported) {
        chipCounts(
            metaResult.games,
            normalizedQuery,
            liveJobsByAppid,
            hiddenChipKeys(controller.updatesSupported),
            grouped.packagesByParent,
        )
    }
    val cardModels = remember(visible, grouped, playtime, liveJobsByAppid, controller.selectedAppids, controller.selecting) {
        visible.map { g ->
            buildGameCardModel(
                g,
                liveJobsByAppid[g.appid],
                selected = g.appid in controller.selectedAppids,
                selecting = controller.selecting,
                packages = grouped.packagesOf(g.appid),
                liveJobsByAppid = liveJobsByAppid,
                ownedPlaytimeForever = playtime[g.appid],
            )
        }
    }

    // WP ANDROID-REDESIGN-1b (web LIBRARY-2 "Filters" popover): the metadata
    // panel folds out under the toolbar; closed on open of the screen.
    var filtersOpen by rememberSaveable { mutableStateOf(false) }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(stringResource(R.string.library_title)) },
                // WP ANDROID-REDESIGN-1b (web LIBRARY-2 split button): "Check
                // updates", then the menu with "Check & update all cached games".
                actions = {
                    CheckUpdatesAction(controller = controller, scope = scope)
                    CheckUpdatesMenu(controller = controller, scope = scope)
                },
            )
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
                LibraryBulkBar(controller = controller, allGames = merged, grouped = grouped, scope = scope)
            }
        },
    ) { innerPadding ->
        Column(modifier = Modifier.fillMaxSize().padding(innerPadding)) {
            if (demoMode) DemoModeBanner()
            LibraryToolbar(
                controller = controller,
                activeSort = activeSort,
                sortKeys = availableSortKeys(sortAvailable, hasMeta),
                filters = if (hasMeta) {
                    FiltersButtonState(
                        open = filtersOpen,
                        activeCount = activeFilterCount(controller.metaFilter),
                        onToggle = { filtersOpen = !filtersOpen },
                    )
                } else {
                    null
                },
            ) {
                FilterChipsRow(counts = counts, selectedKey = controller.filterKey, onSelect = { controller.filterKey = it })
            }
            if (hasMeta) {
                LibraryMetaPanel(
                    filter = controller.metaFilter,
                    games = controller.games,
                    unknownExcluded = metaResult.unknownExcluded,
                    onChange = { controller.selectMetaFilter(it) },
                    visible = filtersOpen,
                )
            }
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
        DeleteConfirmDialog(controller = controller, allGames = merged, scope = scope)
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
            onShowJob = onShowJob,
        )
    }
}

/**
 * WP ANDROID-REDESIGN-1b (web LIBRARY-1/2, docs/design/rc20/library.html):
 * the Library toolbar in Material terms. The web's one wrapping row
 * (search, chips with counts, sort select + icon-only direction button,
 * layout segments, Filters, Select) becomes, at phone width: the search
 * field, one wrapping row of controls, then the chips ([chips], a sideways
 * strip) right above the cards they filter. Spoken labels stay what they
 * were.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun LibraryToolbar(
    controller: LibraryController,
    activeSort: LibrarySort,
    sortKeys: List<LibrarySortKey>,
    filters: FiltersButtonState?,
    chips: @Composable () -> Unit,
) {
    Column(modifier = Modifier.fillMaxWidth().padding(top = 4.dp, bottom = 4.dp)) {
        OutlinedTextField(
            value = controller.query,
            onValueChange = { controller.query = it },
            modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp),
            singleLine = true,
            shape = RoundedCornerShape(12.dp),
            placeholder = { Text(stringResource(R.string.library_search_hint)) },
            leadingIcon = { SearchGlyph() },
            trailingIcon = {
                if (controller.query.isNotEmpty()) {
                    val clear = stringResource(R.string.library_search_clear)
                    IconButton(
                        onClick = { controller.query = "" },
                        modifier = Modifier.semantics { contentDescription = clear },
                    ) { Text("✕") }
                }
            },
        )
        FlowRow(
            modifier = Modifier.fillMaxWidth().padding(start = 12.dp, end = 12.dp, top = 4.dp),
            horizontalArrangement = Arrangement.spacedBy(4.dp),
            verticalArrangement = Arrangement.spacedBy(2.dp),
        ) {
            SortRow(
                active = activeSort,
                keys = sortKeys,
                onSelect = { controller.selectSort(it) },
            )
            LayoutToggle(
                current = controller.layout,
                onSelect = { controller.selectLayout(it) },
                modifier = Modifier.align(Alignment.CenterVertically).width(LAYOUT_TOGGLE_WIDTH),
            )
            if (filters != null) {
                val spoken = if (filters.activeCount > 0) {
                    pluralStringResource(R.plurals.library_filters_active, filters.activeCount, filters.activeCount)
                } else {
                    stringResource(R.string.library_filters_button)
                }
                val filterState = if (filters.open) {
                    stringResource(R.string.library_filters_expanded)
                } else {
                    stringResource(R.string.library_filters_collapsed)
                }
                // WP ANDROID-POLISH-1: funnel + count instead of the word,
                // and no 58 dp button minimum (a set min width skips it), so
                // sort, layout, Filters and Select share one row at 411 dp
                // (font 1.0); the spoken label is unchanged.
                OutlinedButton(
                    onClick = filters.onToggle,
                    contentPadding = PaddingValues(horizontal = 10.dp),
                    modifier = Modifier
                        .align(Alignment.CenterVertically)
                        .widthIn(min = FILTERS_MIN_WIDTH)
                        .semantics {
                            contentDescription = spoken
                            stateDescription = filterState
                        },
                ) {
                    FiltersGlyph()
                    Text(
                        " ${filters.activeCount}",
                        maxLines = 1,
                        color = if (filters.activeCount > 0) MaterialTheme.colorScheme.primary else VaultColors.Dim2,
                    )
                }
            }
            val selectModeDescription = if (controller.selecting) {
                stringResource(R.string.library_select_mode_exit)
            } else {
                stringResource(R.string.library_select_mode)
            }
            IconButton(
                onClick = { if (controller.selecting) controller.exitSelect() else controller.enterSelect() },
                modifier = Modifier
                    .align(Alignment.CenterVertically)
                    .semantics { contentDescription = selectModeDescription },
            ) {
                SelectGlyph(
                    on = controller.selecting,
                    modifier = Modifier,
                )
            }
        }
        // The chips last, right above the cards they filter.
        chips()
    }
}

/** Three 44 dp icon segments: with the sort pair and Select the control row
 * fits one line at 360 dp and font scale 1.0 (WP ANDROID-REDESIGN-1b). */
private val LAYOUT_TOGGLE_WIDTH = 132.dp

/** The Filters button's touch width; below Material's 58 dp button minimum on purpose (WP ANDROID-POLISH-1). */
private val FILTERS_MIN_WIDTH = 48.dp

/** The Filters button's state (WP ANDROID-REDESIGN-1b), `null` without metadata. */
private data class FiltersButtonState(val open: Boolean, val activeCount: Int, val onToggle: () -> Unit)

/** How many metadata filters are set (web `paintFiltersButton` count). */
private fun activeFilterCount(f: MetaFilter): Int =
    f.categories.size + f.genres.size + listOfNotNull(f.minRating, f.yearFrom, f.yearTo).size

/**
 * WP LIB-SORT: the sort option (dropdown) plus the ascending/descending
 * toggle, now an icon-only button (web LIBRARY-2 `.sortg`) whose spoken
 * label names the direction. Labels are the web twin's literals
 * (`LibrarySortWording`, `LibrarySortKey.label`). Persistence goes through
 * the controller.
 */
@Composable
private fun SortRow(active: LibrarySort, keys: List<LibrarySortKey>, onSelect: (LibrarySort) -> Unit) {
    var menuOpen by remember { mutableStateOf(false) }
    val directionLabel = LibrarySortWording.directionLabel(active)
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box {
            // Web `.sortg select`: the visible text is the option, the
            // spoken label says what it is ("Sort: Name").
            val sortDescription = "${LibrarySortWording.SORT_LABEL}: ${active.key.label}"
            OutlinedButton(
                onClick = { menuOpen = true },
                contentPadding = PaddingValues(start = 14.dp, end = 8.dp),
                modifier = Modifier.semantics { contentDescription = sortDescription },
            ) {
                Text(active.key.label, maxLines = 1, overflow = TextOverflow.Ellipsis)
                ChevronDownGlyph(Modifier.padding(start = 2.dp))
            }
            DropdownMenu(expanded = menuOpen, onDismissRequest = { menuOpen = false }) {
                for (key in keys) {
                    DropdownMenuItem(
                        text = { Text(key.label) },
                        onClick = {
                            menuOpen = false
                            onSelect(sortAfterKeyPick(active, key))
                        },
                    )
                }
            }
        }
        val toggleDescription = "${LibrarySortWording.SORT_LABEL} direction: $directionLabel"
        IconButton(
            onClick = {
                val next = if (active.dir == SortDirection.ASC) SortDirection.DESC else SortDirection.ASC
                onSelect(LibrarySort(active.key, next))
            },
            modifier = Modifier.semantics { contentDescription = toggleDescription },
        ) {
            SortDirectionGlyph(descending = active.dir == SortDirection.DESC)
        }
    }
}

/**
 * "Check & update all cached games" (Phase 4c, WP 4c-app), since WP
 * ANDROID-REDESIGN-1b the menu entry of the split "Check updates" button in
 * the top app bar (web LIBRARY-2 `.split` + "More update actions" menu)
 * instead of its own row. A SEPARATE control from this screen's poll loops
 * -- never folded into a pull-to-refresh gesture ("a refresh gesture that
 * can start downloads is a trap", web divergence entry). Shown also on a
 * vault without update checks, where it is the only entry.
 *
 * The wording/error/in-flight logic itself lives in
 * `ui/library/logic/CachedPrefillOutcome.kt` -- this composable only paints
 * [LibraryController.checkAndUpdateBusy] and forwards the click.
 */
@Composable
private fun CheckUpdatesMenu(controller: LibraryController, scope: CoroutineScope) {
    var open by remember { mutableStateOf(false) }
    val more = stringResource(R.string.library_more_update_actions)
    Box {
        IconButton(
            onClick = { open = true },
            modifier = Modifier.semantics { contentDescription = more },
        ) { ChevronDownGlyph() }
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            DropdownMenuItem(
                enabled = !controller.checkAndUpdateBusy,
                text = {
                    Text(
                        if (controller.checkAndUpdateBusy) {
                            stringResource(R.string.library_check_update_button_busy)
                        } else {
                            stringResource(R.string.library_check_update_button)
                        },
                    )
                },
                onClick = {
                    open = false
                    controller.checkAndUpdateCachedGames(scope)
                },
            )
        }
    }
}

/**
 * "Check updates" (WP UPD-3, twin of web UPD-2; D14): asks the vault
 * whether cached games have a newer build -- downloads nothing (that stays
 * [CheckUpdatesMenu]'s job). In the top app bar so the header gains no
 * height and the check row no width (D12 is a separate package). Hidden on
 * a vault without the feature ([LibraryController.updatesSupported]).
 */
@Composable
private fun CheckUpdatesAction(controller: LibraryController, scope: CoroutineScope) {
    if (!controller.updatesSupported) return
    // Oracle off (status enabled=false): visible, disabled, and the label
    // itself is the hint; the spoken label says why.
    val off = !controller.updatesEnabled
    val offDescription = stringResource(R.string.library_update_check_disabled)
    TextButton(
        enabled = !off && !controller.updateCheckRunning,
        onClick = { controller.checkUpdates(scope) },
        modifier = if (off) Modifier.semantics { contentDescription = offDescription } else Modifier,
    ) {
        Text(
            when {
                off -> stringResource(R.string.library_check_updates_button_off)
                controller.updateCheckRunning -> stringResource(R.string.library_check_updates_button_busy)
                else -> stringResource(R.string.library_check_updates_button)
            },
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun LayoutToggle(
    current: LibraryLayout,
    onSelect: (LibraryLayout) -> Unit,
    modifier: Modifier = Modifier,
) {
    // WP ANDROID-REDESIGN-1b (web LIBRARY-2 `.segi`): icon-only segments
    // (D12's short "2 col" labels are gone); the full name is the spoken
    // label (contentDescription).
    val options = listOf(
        LayoutOption(LibraryLayout.GRID_2, stringResource(R.string.library_layout_grid2)),
        LayoutOption(LibraryLayout.GRID_3, stringResource(R.string.library_layout_grid3)),
        LayoutOption(LibraryLayout.LIST, stringResource(R.string.library_layout_list)),
    )
    SingleChoiceSegmentedButtonRow(modifier = modifier) {
        options.forEachIndexed { index, option ->
            SegmentedButton(
                selected = current == option.layout,
                onClick = { onSelect(option.layout) },
                shape = SegmentedButtonDefaults.itemShape(index = index, count = options.size),
                modifier = Modifier.semantics { contentDescription = option.description },
                icon = {},
                label = { LayoutGlyph(option.layout) },
            )
        }
    }
}

private data class LayoutOption(val layout: LibraryLayout, val description: String)

@Composable
private fun FilterChipsRow(
    counts: List<ChipCount>,
    selectedKey: String,
    onSelect: (String) -> Unit,
) {
    // WP D12: scrolls horizontally, every chip on one line. The old
    // fixed-width Row squeezed the trailing chips to a sliver: their labels
    // wrapped letter by letter ("Downloa/ding"), the tallest of them set
    // the row's height (the empty band above the cards, at 360 dp / font
    // 1.3 tall enough to push the grid off screen) and the last chips were
    // not reachable at all.
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .horizontalScroll(rememberScrollState())
            .padding(horizontal = 12.dp, vertical = 4.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        for (chip in counts) {
            FilterChip(
                selected = chip.key == selectedKey,
                onClick = { onSelect(chip.key) },
                // WP ANDROID-REDESIGN-1b (web `.chip small`): the count in the
                // dim caption tone after the word; one spoken label.
                label = {
                    Text(
                        text = buildAnnotatedString {
                            append(chip.label)
                            append(" ")
                            withStyle(SpanStyle(color = if (chip.key == selectedKey) Color.Unspecified else VaultColors.Dim2)) {
                                append(chip.count.toString())
                            }
                        },
                        maxLines = 1,
                        softWrap = false,
                    )
                },
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
                        compact = layout == LibraryLayout.GRID_3,
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
