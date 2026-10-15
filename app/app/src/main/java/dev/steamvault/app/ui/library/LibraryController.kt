package dev.steamvault.app.ui.library

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.DeletePlanOut
import dev.steamvault.app.net.model.GameDetail
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.polling.PollingIntervals
import dev.steamvault.app.repo.CacheRepository
import dev.steamvault.app.repo.GamesRepository
import dev.steamvault.app.repo.JobsRepository
import dev.steamvault.app.repo.MappingRepository
import dev.steamvault.app.repo.UpdatesRepository
import dev.steamvault.app.storage.LibraryPreferences
import dev.steamvault.app.ui.library.logic.CheckAndUpdateAction
import dev.steamvault.app.ui.library.logic.CheckAndUpdateResult
import dev.steamvault.app.ui.library.logic.LibraryLayout
import dev.steamvault.app.ui.library.logic.MEMBERS_CHANGED_NOTICE
import dev.steamvault.app.ui.library.logic.MultiPlan
import dev.steamvault.app.ui.library.logic.StatusActionType
import dev.steamvault.app.ui.library.logic.UPDATES_FILTER_KEY
import dev.steamvault.app.ui.library.logic.UpdateCheckResult
import dev.steamvault.app.ui.library.logic.buildMultiPlan
import dev.steamvault.app.ui.library.logic.classifyBulkSelection
import dev.steamvault.app.ui.library.logic.describeCachedPrefillError
import dev.steamvault.app.ui.library.logic.findLiveJob
import dev.steamvault.app.ui.library.logic.formatBytesGB
import dev.steamvault.app.ui.library.logic.groupGames
import dev.steamvault.app.ui.library.logic.isMembersMismatch
import dev.steamvault.app.ui.library.logic.isPlanBlocked
import dev.steamvault.app.ui.library.logic.planMemberIds
import dev.steamvault.app.ui.library.logic.runUpdateCheck
import dev.steamvault.app.ui.library.logic.updateCheckNoticeFor
import dev.steamvault.app.ui.library.logic.summarizeCachedPrefillOutcome
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Everything the Library screen needs, stateful glue kept as thin as
 * possible (WP 4b.4 brief: "extract logic so what ships untested-on-JVM is
 * thin" -- every DECISION this class makes delegates to `ui/library/logic/`
 * pure functions; this class only owns Compose state and orchestrates
 * suspend repository calls, mirroring `web/js/views/library.js`'s
 * `state`/`syncBulk`/`onAction` shapes one-to-one action-for-action).
 *
 * Not an `androidx.lifecycle.ViewModel` -- same house pattern `MainActivity`
 * already uses for the Steam identity repository's
 * screen state (a plain `mutableStateOf` field, no ViewModel dependency
 * anywhere in this project yet). Held via `remember` in `LibraryScreen.kt`,
 * so it does not survive leaving the Library tab -- consistent with the
 * flat, backstack-less navigation `ui/nav/Destination.kt` documents; a
 * fresh poll on next visit is an acceptable, deliberate trade for staying
 * dependency-light.
 */
class LibraryController(
    private val gamesRepository: GamesRepository,
    private val jobsRepository: JobsRepository,
    private val mappingRepository: MappingRepository,
    private val cacheRepository: CacheRepository,
    private val updatesRepository: UpdatesRepository,
    private val libraryPreferences: LibraryPreferences,
    private val strings: LibraryStrings,
) {
    var games by mutableStateOf<List<GameSummary>>(emptyList())
        private set
    var jobs by mutableStateOf<List<JobSummary>>(emptyList())
        private set
    var loadError by mutableStateOf<String?>(null)
        private set

    var query by mutableStateOf("")
    var filterKey by mutableStateOf("all")
    var layout by mutableStateOf(libraryPreferences.getLayout())
        private set

    var selecting by mutableStateOf(false)
        private set
    var selectedAppids by mutableStateOf<Set<Int>>(emptySet())
        private set

    var toast by mutableStateOf<LibraryToast?>(null)
        private set

    var deletePlan by mutableStateOf<DeletePlanUiState>(DeletePlanUiState.Hidden)
        private set

    // ---- "Check & update all cached games" (Phase 4c, WP 4c-app) --------
    // See ui/library/logic/CachedPrefillOutcome.kt's module kdoc for the
    // full contract this wiring is glue for -- wording, error handling and
    // the in-flight guard itself all live there (DOM-free -- Android-
    // framework-free -- and unit-tested); this class is only the
    // Compose-observable busy flag plus the suspend orchestration, mirroring
    // web/js/views/library.js's onCheckAndUpdate() one-to-one.

    /** One guard instance per [LibraryController] -- unlike web's
     * module-level singleton, this does NOT survive leaving the Library tab
     * (see this class's own kdoc: not a ViewModel, `remember`-scoped). A
     * screen re-entered while a previous call from the OLD controller
     * instance is still running therefore starts a fresh, un-busy guard --
     * an honest, documented narrowing of web's cross-remount guarantee, not
     * a silent gap: the server-side dedupe (`enqueue_prefill`) still makes a
     * second concurrent call harmless, it would just not show as `busy`. */
    private val checkAndUpdateAction = CheckAndUpdateAction(fetcher = { jobsRepository.prefillCached() })

    var checkAndUpdateBusy by mutableStateOf(checkAndUpdateAction.isInFlight())
        private set

    /**
     * Belt: [dev.steamvault.app.ui.library.LibraryScreen] already disables
     * the real button the instant [checkAndUpdateBusy] flips `true` (see
     * the click listener there), so a second click cannot even reach here
     * while one is in flight. Suspenders: [checkAndUpdateAction] itself
     * no-ops a concurrent [dev.steamvault.app.ui.library.logic.CheckAndUpdateAction.run]
     * regardless of how it was invoked.
     *
     * **N1 fix (Opus review on this WP).** [checkAndUpdateBusy] is cleared
     * ONLY on [CheckAndUpdateResult.Success]/[CheckAndUpdateResult.Failure]
     * -- never on [CheckAndUpdateResult.Skipped]. The earlier version
     * cleared it unconditionally, so two presses landing in the same frame
     * (before Compose has a chance to repaint the now-disabled button)
     * produced one real call plus one `Skipped` result whose `finally`
     * block re-enabled the button while the FIRST call's request was still
     * in flight -- the guard itself still correctly refused a second
     * network call (that guarantee is [checkAndUpdateAction]'s own, proven
     * by `CachedPrefillOutcomeTest`'s in-flight-guard pin), only the visible
     * "belt" (button state) went stale. Leaving [checkAndUpdateBusy]
     * untouched on `Skipped` means it stays exactly whatever the ORIGINAL,
     * still-running call set it to.
     */
    fun checkAndUpdateCachedGames(scope: CoroutineScope) {
        scope.launch {
            checkAndUpdateBusy = true
            val result = checkAndUpdateAction.run()
            when (result) {
                is CheckAndUpdateResult.Skipped -> {} // already running -- this press changed nothing, including checkAndUpdateBusy
                is CheckAndUpdateResult.Success -> {
                    checkAndUpdateBusy = false
                    // `games` is passed through only as the best-effort
                    // needs_force lookup table for the forced-run note --
                    // summarizeCachedPrefillOutcome itself decides whether
                    // that note applies at all (see its kdoc: gated on its
                    // OWN queued bucket, never on this snapshot alone).
                    val summary = summarizeCachedPrefillOutcome(result.refs, games)
                    toast = LibraryToast(
                        message = summary.message,
                        warn = summary.warn,
                        durationMs = if (summary.warn) CHECK_UPDATE_WARN_TOAST_MS else DEFAULT_TOAST_DURATION_MS,
                    )
                    refreshNow(scope)
                }
                is CheckAndUpdateResult.Failure -> {
                    checkAndUpdateBusy = false
                    val desc = describeCachedPrefillError(result.err)
                    toast = LibraryToast(message = desc.message, warn = desc.warn, durationMs = DEFAULT_TOAST_DURATION_MS)
                    // README's mid-loop-5xx honesty rule: re-read GET
                    // /v1/jobs rather than implying the press did nothing --
                    // describeCachedPrefillError sets `refresh` only for
                    // that one error kind.
                    if (desc.refresh) refreshNow(scope)
                }
            }
        }
    }

    // ---- "Check updates" (WP UPD-3, twin of web UPD-2; D14) ---------------
    // Asks the vault to check cached games for a newer build, downloads
    // nothing. The decisions (poll loop, bound, result wording) live in
    // ui/library/logic/UpdateCheck.kt; this is the Compose state plus the
    // launch in the screen's scope, so leaving the Library cancels the poll.

    /** `false` once `/v1/updates/...` answered `404` (a vault from before
     * UPD-1): the "Check updates" action and the "Updates" chip are hidden.
     * Starts `true`: only a confirmed 404 hides anything. */
    var updatesSupported by mutableStateOf(true)
        private set

    /** `false` while the vault says the manifest oracle is off
     * (`enabled: false` from the status read or the check): the action
     * stays visible but disabled with a hint; nothing else is hidden. */
    var updatesEnabled by mutableStateOf(true)
        private set

    /** A check started from this screen is running (button disabled). */
    var updateCheckRunning by mutableStateOf(false)
        private set

    /** One `GET /v1/updates/status` on Library open, to learn whether this
     * vault has the feature (404 hides it) and whether the oracle is on
     * (`enabled: false` disables the action). Any other failure leaves it
     * shown -- the button's own call reports errors. */
    suspend fun probeUpdatesSupport() {
        try {
            updatesEnabled = updatesRepository.status().enabled
        } catch (_: VaultApiError.NotFound) {
            markUpdatesUnsupported()
        } catch (_: VaultApiError) {
            // Transient or auth trouble: the games poll owns the error banner.
        }
    }

    /** Start a check and follow it to its end in [scope]. A second press
     * while one runs is ignored (the button is disabled too). */
    fun checkUpdates(scope: CoroutineScope) {
        if (updateCheckRunning) return
        updateCheckRunning = true
        scope.launch {
            try {
                val result = runUpdateCheck(updatesRepository)
                if (result is UpdateCheckResult.Unsupported) {
                    markUpdatesUnsupported()
                } else {
                    updatesEnabled = result !is UpdateCheckResult.Disabled
                    // The badges come from GET /v1/games: re-read it now
                    // instead of waiting out the games poll interval.
                    refreshNow(scope)
                }
                val notice = updateCheckNoticeFor(result)
                toast = LibraryToast(
                    message = strings.updateCheckNotice(notice),
                    warn = notice.warn,
                    durationMs = if (notice.warn) CHECK_UPDATE_WARN_TOAST_MS else DEFAULT_TOAST_DURATION_MS,
                )
            } finally {
                updateCheckRunning = false
            }
        }
    }

    private fun markUpdatesUnsupported() {
        updatesSupported = false
        if (filterKey == UPDATES_FILTER_KEY) filterKey = "all"
    }

    // ---- layout -----------------------------------------------------------

    fun selectLayout(newLayout: LibraryLayout) {
        layout = newLayout
        libraryPreferences.setLayout(newLayout)
    }

    // ---- selection ----------------------------------------------------

    fun enterSelect(appid: Int? = null) {
        selecting = true
        if (appid != null) selectedAppids = selectedAppids + appid
    }

    fun exitSelect() {
        selecting = false
        selectedAppids = emptySet()
    }

    fun toggleSelect(appid: Int) {
        selectedAppids = if (appid in selectedAppids) selectedAppids - appid else selectedAppids + appid
        if (selectedAppids.isEmpty()) selecting = false
    }

    fun dismissToast() {
        toast = null
    }

    // ---- polling (called from LaunchedEffect + repeatOnLifecycle in LibraryScreen.kt) --

    /** Foreground-only fixed-cadence poll (WP 4b.4 brief: "foreground-only
     * polling -- WorkManager background is 4b.8"). Runs until its own
     * coroutine is cancelled -- `delay` is a cooperative suspension point,
     * so cancelling the caller (e.g. `repeatOnLifecycle` dropping below
     * STARTED) unwinds this loop via normal structured-concurrency
     * cancellation, no manual `isActive` bookkeeping needed. */
    suspend fun pollGamesForever() {
        while (true) {
            refreshGamesOnce()
            delay(PollingIntervals.GAMES_MS)
        }
    }

    /** Adaptive-cadence poll (fast while a job is active, same decision
     * [PollingIntervals.nextJobsIntervalMs] already makes for the rest of
     * the app). */
    suspend fun pollJobsForever() {
        while (true) {
            refreshJobsOnce()
            delay(PollingIntervals.nextJobsIntervalMs(jobs))
        }
    }

    suspend fun refreshGamesOnce() {
        try {
            games = gamesRepository.list()
            loadError = null
        } catch (e: VaultApiError) {
            loadError = e.message
        }
    }

    suspend fun refreshJobsOnce() {
        try {
            jobs = jobsRepository.list()
        } catch (_: VaultApiError) {
            // Jobs poll failures don't blank the grid (games poll owns the
            // error banner) -- a transient jobs-endpoint hiccup should not
            // make cards flicker back to their non-live state.
        }
    }

    // WP APP-FEAT-1: the owned-games list no longer lives here (it used to
    // come from this device's own Steam OpenID sign-in via
    // SteamIdentityRepository.ownedGames). It is the vault's stored
    // steam_library_steamid plus the relay, held by OwnedLibraryController
    // (MainActivity level, shared with Downloads) and loaded by
    // LibraryScreen on every Library open.

    /** Mirrors `store.refreshNow()`: an out-of-cadence poll right after a
     * mutation, so the grid doesn't wait out the ambient poll interval to
     * reflect what the user just did. Safe to call alongside the ongoing
     * [pollGamesForever]/[pollJobsForever] loops -- this only adds one extra
     * one-off fetch, it never spawns a second overlapping loop (the poll
     * loops are plain sequential `while` bodies, not self-re-arming
     * timers -- the async-poll-fork failure class docs/LEARNINGS.md's WP
     * 4a.2 entry describes does not apply to this shape). */
    private fun refreshNow(scope: CoroutineScope) {
        scope.launch { refreshGamesOnce() }
        scope.launch { refreshJobsOnce() }
    }

    // ---- per-card action (capsule pill / list-row icon) -------------------

    fun onCardAction(scope: CoroutineScope, appid: Int, actionType: StatusActionType) {
        scope.launch {
            try {
                when (actionType) {
                    StatusActionType.DOWNLOAD, StatusActionType.RETRY, StatusActionType.CHECK_AGAIN -> {
                        jobsRepository.prefill(listOf(appid))
                        toast = LibraryToast(strings.queuedForDownload())
                    }
                    StatusActionType.PAUSE -> {
                        val job = findLiveJob(jobs, appid) ?: return@launch
                        jobsRepository.pause(job.id)
                        toast = LibraryToast(strings.pauseRequested())
                    }
                    StatusActionType.RESUME -> {
                        val job = findLiveJob(jobs, appid) ?: return@launch
                        jobsRepository.resume(job.id)
                        toast = LibraryToast(strings.resuming())
                    }
                }
                refreshNow(scope)
            } catch (e: VaultApiError) {
                toast = LibraryToast(e.message ?: strings.actionFailedFallback())
            }
        }
    }

    // ---- bulk download ------------------------------------------------

    fun startBulkDownload(scope: CoroutineScope, appids: List<Int>) {
        if (appids.isEmpty()) return
        scope.launch {
            try {
                jobsRepository.prefill(appids)
                toast = LibraryToast(strings.jobsQueued(appids.size))
                exitSelect()
                refreshNow(scope)
            } catch (e: VaultApiError) {
                toast = LibraryToast(e.message ?: strings.actionFailedFallback())
            }
        }
    }

    // ---- bulk delete ----------------------------------------------------
    // Eligibility itself (has-cache-content, not busy) is computed where the
    // bulk bar renders it (`LibraryBulkBar.kt`, `classifyBulkDeleteEligibility`)
    // against the FULL merged library, not just `games` -- see that file's
    // kdoc for why the selected set must be resolved against the merged
    // list rather than the raw vault snapshot.

    /**
     * WP TOOL-APPS-2d (ADR-0022 §4): an id whose card is a group (a game with
     * content packages, [groupGames] over the vault rows) is previewed by the
     * SERVER (`GET /v1/cache/{appid}/delete-plan?with_packages=true`) and
     * later deleted with exactly that plan's members; every other id keeps
     * the set-aware [buildMultiPlan] preview. [notice] is the re-confirm
     * message after a 409 "members differ".
     */
    fun openDeleteConfirm(scope: CoroutineScope, ids: List<Int>, notice: String? = null) {
        if (ids.isEmpty()) return
        deletePlan = DeletePlanUiState.Loading(ids)
        val grouped = groupGames(games)
        val groupIds = ids.filter { grouped.packagesOf(it).isNotEmpty() }
        val plainIds = ids.filter { it !in groupIds }
        scope.launch {
            try {
                val groupPlans = groupIds.map { cacheRepository.deletePlan(it) }
                val plan = if (plainIds.isEmpty()) {
                    EMPTY_MULTI_PLAN
                } else {
                    val details: List<GameDetail> = plainIds.map { gamesRepository.detail(it) }
                    val mapping = mappingRepository.list()
                    val gamesByAppid = games.associateBy { it.appid }
                    val activeJobAppids = classifyBulkSelection(games, jobs).busy.mapTo(HashSet()) { it.appid }
                    buildMultiPlan(plainIds, details, mapping, gamesByAppid, activeJobAppids)
                }
                deletePlan = DeletePlanUiState.Ready(ids, plan, groupPlans, notice)
            } catch (e: VaultApiError) {
                deletePlan = DeletePlanUiState.Error(ids, e.message ?: strings.deletePlanErrorFallback())
            }
        }
    }

    fun closeDeleteConfirm() {
        deletePlan = DeletePlanUiState.Hidden
    }

    /**
     * Issues one `DELETE /v1/cache/{appid}` per id (mirrors `library.js`'s
     * `confirmDelete`: "no batch endpoint... one DELETE per selected app",
     * mockup-notes.md round 6's documented backend-implication note). The
     * toast reports what the SERVER actually freed
     * (`CacheDeletionOut.total_bytes_freed`), not [DeletePlanUiState.Ready]'s
     * preview -- the server re-checks every depot's co-owner state
     * immediately before removing it and is the authority, per
     * `MultiPlan.kt`'s kdoc.
     */
    fun confirmDelete(scope: CoroutineScope, ids: List<Int>) {
        // WP TOOL-APPS-2d: a group card is deleted with exactly the members
        // its plan listed; every other pick as before.
        val groupPlans = (deletePlan as? DeletePlanUiState.Ready)?.groupPlans.orEmpty().associateBy { it.appid }
        scope.launch {
            var freedTotal = 0L
            var failedCount = 0
            val changed = mutableListOf<Int>()
            for (id in ids) {
                val groupPlan = groupPlans[id]
                try {
                    freedTotal += if (groupPlan != null) {
                        cacheRepository.deleteWithPackages(id, planMemberIds(groupPlan)).total_bytes_freed
                    } else {
                        cacheRepository.delete(id).total_bytes_freed
                    }
                } catch (e: VaultApiError) {
                    if (groupPlan != null && isMembersMismatch(e)) changed.add(id) else failedCount++
                }
            }
            refreshNow(scope)
            if (changed.isNotEmpty()) {
                // A group whose package list changed since the preview (409,
                // nothing deleted for it): show its new plan and ask again.
                refreshGamesOnce()
                openDeleteConfirm(scope, changed, MEMBERS_CHANGED_NOTICE)
                if (changed.size < ids.size) {
                    toast = LibraryToast(strings.freed(formatFreed(freedTotal), failedCount))
                }
                return@launch
            }
            deletePlan = DeletePlanUiState.Hidden
            exitSelect()
            toast = LibraryToast(strings.freed(formatFreed(freedTotal), failedCount))
        }
    }

    private fun formatFreed(bytes: Long): String = formatBytesGB(bytes) ?: ZERO_GB_LABEL

    companion object {
        /** Unchanged from the Snackbar auto-dismiss cadence `LibraryScreen.kt`
         * hardcoded before WP 4c-app (2500ms) -- kept as-is rather than
         * nudged to web toast.js's literal 2.6s default; not this WP's job
         * to touch every existing toast's timing for a 100ms difference
         * nobody asked about. */
        internal const val DEFAULT_TOAST_DURATION_MS = 2500L

        /** Mirrors web/js/views/library.js's `CHECK_UPDATE_WARN_TOAST_MS` --
         * `warn` means "the user must go DO something" (a paused dedupe --
         * resume or cancel it), the one outcome of this action that needs a
         * follow-up, not just an acknowledgement. */
        internal const val CHECK_UPDATE_WARN_TOAST_MS = 6000L
    }
}

/** "0 GB" is not localizable copy in the same sense the rest of this file's
 * strings are -- it is [formatBytesGB]'s own unit suffix (already a plain
 * Kotlin literal there, ported from `web/js/lib/format.js`'s `gb()`), used
 * here only as the floor value when nothing was freed. Kept as a constant
 * next to its only caller rather than a resource for that reason. */
private const val ZERO_GB_LABEL = "0 GB"

/**
 * A queued Snackbar message plus how long it stays up and whether it reads
 * as a warning (WP 4c-app: introduced for "Check & update all cached
 * games", but now the one shape every toast in this controller produces).
 * [warn] mirrors web toast.js's `warn` option; [durationMs] mirrors its
 * per-call duration override (`CHECK_UPDATE_WARN_TOAST_MS` vs the default).
 */
data class LibraryToast(
    val message: String,
    val warn: Boolean = false,
    val durationMs: Long = LibraryController.DEFAULT_TOAST_DURATION_MS,
)

/** Bulk-delete confirm dialog state -- mirrors `library.js`'s
 * `openDeleteConfirm`/`renderDeletePlan` three phases (calculating,
 * ready-with-plan, calculation failed). */
sealed class DeletePlanUiState {
    data object Hidden : DeletePlanUiState()
    data class Loading(val ids: List<Int>) : DeletePlanUiState()

    /** @property plan the [buildMultiPlan] preview of the PLAIN picks (empty
     *   when every pick is a group).
     * @property groupPlans WP TOOL-APPS-2d: the server's plan per picked
     *   group card (deleted with exactly its members).
     * @property notice the re-confirm message after a 409 "members differ". */
    data class Ready(
        val ids: List<Int>,
        val plan: MultiPlan,
        val groupPlans: List<DeletePlanOut> = emptyList(),
        val notice: String? = null,
    ) : DeletePlanUiState() {
        /** Delete stays disabled while a group member has a job in flight. */
        val blocked: Boolean get() = groupPlans.any { isPlanBlocked(it) }

        /** Bytes the dialog says this frees: the plain picks' preview plus
         * every group plan's total. */
        val freedBytes: Long get() = plan.freedBytes + groupPlans.sumOf { it.total_bytes }
    }

    data class Error(val ids: List<Int>, val message: String) : DeletePlanUiState()
}

/** The plain-picks preview when every pick is a group (nothing plain to plan). */
internal val EMPTY_MULTI_PLAN = MultiPlan(
    ids = emptyList(),
    rows = emptyList(),
    sharedRows = emptyList(),
    freedBytes = 0L,
    keptBytes = 0L,
    occupiedBytes = 0L,
)
