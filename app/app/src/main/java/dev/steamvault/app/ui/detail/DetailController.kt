package dev.steamvault.app.ui.detail

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.GameDetail
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.MappingEntry
import dev.steamvault.app.repo.CacheRepository
import dev.steamvault.app.repo.GamesRepository
import dev.steamvault.app.repo.JobsRepository
import dev.steamvault.app.repo.MappingRepository
import dev.steamvault.app.ui.detail.logic.GcFlowEvent
import dev.steamvault.app.ui.detail.logic.GcFlowState
import dev.steamvault.app.ui.detail.logic.reduceGcFlow
import dev.steamvault.app.ui.downloads.logic.describeUpdateStartError
import dev.steamvault.app.ui.downloads.logic.summarizeUpdateStart
import dev.steamvault.app.net.model.DeletePlanOut
import dev.steamvault.app.ui.library.logic.MEMBERS_CHANGED_NOTICE
import dev.steamvault.app.ui.library.logic.formatBytesGB
import dev.steamvault.app.ui.library.logic.isMembersMismatch
import dev.steamvault.app.ui.library.logic.isPlanBlocked
import dev.steamvault.app.ui.library.logic.planMemberIds
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/** How often the GC flow polls `GET /v1/jobs/{id}` while a dry-run or
 * execute job is in flight -- local to this screen, same reasoning
 * `ui/downloads/logic/LogExcerpt.kt`'s per-job fetch stays independent of
 * `PollingIntervals`: this is a one-off wait for a single job to finish,
 * not an ambient list poll. */
private const val GC_POLL_INTERVAL_MS = 1200L

/**
 * Everything the game detail sheet needs (WP 4b.6 brief) -- same thin-glue
 * shape `ui/library/LibraryController.kt` / `ui/downloads/DownloadsController.kt`
 * document: state + suspend orchestration only, every DECISION delegates to
 * `ui/detail/logic/` pure functions. Not an `androidx.lifecycle.ViewModel`
 * (same house pattern), held via `remember` in `LibraryScreen.kt` alongside
 * [dev.steamvault.app.ui.library.LibraryController] -- the sheet is only
 * ever opened from the Library grid, so it shares that screen's lifecycle
 * rather than getting its own.
 *
 * **Depot sharing is computed, never stored** (mockup-notes.md round 3).
 * [open] fetches `detail`/`mapping` ONCE and stores them as plain fields;
 * the actual sharing arithmetic (`buildMultiPlan`/`buildDepotPresentation`)
 * is re-derived by the Composable on every recomposition from those fields
 * PLUS the caller's LIVE `games`/`jobs` snapshots (`LibraryScreen.kt` passes
 * `LibraryController.games`/`.jobs` straight through) -- so a co-owner's
 * cache state changing on the next poll tick while the sheet stays open
 * updates the sharing wording without a second `GET /v1/games/{appid}`
 * round trip. See `GameDetailSheet.kt` for exactly where that
 * recomputation happens.
 *
 * `detail` and `mapping` are only ever assigned TOGETHER, after BOTH
 * `GET /v1/games/{appid}` and `GET /v1/mapping` succeed -- never one before
 * the other -- so `detail != null` is always a safe "everything this sheet
 * needs to compute a plan is loaded" signal for callers, with no window
 * where the depot list is visible but the mapping table backing its sharing
 * computation is still the empty-list default.
 */
class DetailController(
    private val gamesRepository: GamesRepository,
    private val jobsRepository: JobsRepository,
    private val mappingRepository: MappingRepository,
    private val cacheRepository: CacheRepository,
    private val strings: DetailStrings,
) {
    var openAppid by mutableStateOf<Int?>(null)
        private set

    /** Fallback display name captured at [open] time (the calling card
     * already knows it) -- shown while [detail] is loading, and for the
     * [notTracked] empty state where `GET /v1/games/{appid}` never returns
     * a name at all. */
    var openName by mutableStateOf<String?>(null)
        private set

    var detail by mutableStateOf<GameDetail?>(null)
        private set
    var mapping by mutableStateOf<List<MappingEntry>>(emptyList())
        private set

    /** `true` when this appid has no `apps` row at all yet -- a Steam-owned,
     * never-prefilled game (`dev.steamvault.app.ui.library.logic.isKnownToVault`
     * said so BEFORE the fetch even started, see [open]'s `isKnownToVault`
     * param). A friendly empty state, not [loadError]: api/README.md says
     * "the apps row is created at enqueue", so a 404 here is the EXPECTED
     * shape for such a game, not a failure worth alarming the user about. */
    var notTracked by mutableStateOf(false)
        private set

    var loadError by mutableStateOf<String?>(null)
        private set
    var toast by mutableStateOf<String?>(null)
        private set

    var showDeleteConfirm by mutableStateOf(false)
        private set

    /** WP TOOL-APPS-2d: the grouped-delete confirmation (a game with content
     * packages), separate from [showDeleteConfirm]'s plain one. */
    var groupDelete by mutableStateOf<GroupDeleteUiState>(GroupDeleteUiState.Hidden)
        private set

    var gcState by mutableStateOf<GcFlowState>(GcFlowState.Idle)
        private set

    /** The in-flight GC dry-run/execute network call or poll loop, if any --
     * cancelled on [close] (leaving the sheet mid-GC must not leak a
     * runaway poll loop) and on every fresh [startGcDryRun]/[confirmGcExecute]
     * (defensive: the UI already disables the trigger while busy, this is
     * the belt to that suspenders, same posture
     * `ui/downloads/DownloadsController.kt`'s `busyJobIds` guard takes). */
    private var gcJob: Job? = null

    fun dismissToast() {
        toast = null
    }

    // ---- open/close -------------------------------------------------------

    /**
     * @param isKnownToVault [dev.steamvault.app.ui.library.logic.isKnownToVault]'s
     *   verdict for this appid from the CALLER's current games snapshot --
     *   skips the doomed `GET /v1/games/{appid}` call entirely for a
     *   synthetic Steam-owned row (see [notTracked]'s kdoc) rather than
     *   reacting to its 404 after the fact.
     */
    fun open(scope: CoroutineScope, appid: Int, name: String?, isKnownToVault: Boolean) {
        gcJob?.cancel()
        gcJob = null
        openAppid = appid
        openName = name
        detail = null
        mapping = emptyList()
        notTracked = !isKnownToVault
        loadError = null
        showDeleteConfirm = false
        groupDelete = GroupDeleteUiState.Hidden
        gcState = GcFlowState.Idle

        if (!isKnownToVault) return

        scope.launch {
            try {
                val fetchedDetail = gamesRepository.detail(appid)
                val fetchedMapping = mappingRepository.list()
                // Assigned together, only on full success -- see class kdoc.
                detail = fetchedDetail
                mapping = fetchedMapping
            } catch (e: VaultApiError.NotFound) {
                // The card's isKnownToVault verdict was stale (e.g. a race
                // with something dropping the app's last mapping row
                // between the poll tick that built the card and this
                // fetch) -- fall back to the same honest empty state rather
                // than a scary error banner.
                notTracked = true
            } catch (e: VaultApiError) {
                loadError = e.message ?: strings.loadErrorFallback()
            }
        }
    }

    fun close() {
        gcJob?.cancel()
        gcJob = null
        openAppid = null
        openName = null
        detail = null
        mapping = emptyList()
        notTracked = false
        loadError = null
        showDeleteConfirm = false
        groupDelete = GroupDeleteUiState.Hidden
        gcState = GcFlowState.Idle
    }

    // ---- download / job control --------------------------------------------

    fun startDownload(scope: CoroutineScope) {
        val appid = openAppid ?: return
        scope.launch {
            try {
                jobsRepository.prefill(listOf(appid))
                toast = strings.queuedForDownload()
            } catch (e: VaultApiError) {
                toast = e.message ?: strings.actionFailedFallback()
            }
        }
    }

    /** WP UPD-6 review fix: an "Update now" request is in flight (the
     * button is disabled meanwhile). */
    var updateInFlight by mutableStateOf(false)
        private set

    /**
     * WP UPD-6 review fix (twin of web `startSheetUpdate`): the sheet's
     * "Update now" -- `POST /v1/prefill` with exactly [appids] (from
     * `sheetUpdateAppids`: the game and its flagged packages), the same
     * non-forced enqueue Downloads' "Update now" uses. The jobs poll then
     * moves the game into the queue and the button away.
     * @param games `GET /v1/games` snapshot, for the forced-run note.
     */
    fun startUpdate(scope: CoroutineScope, appids: List<Int>, games: List<GameSummary>) {
        if (updateInFlight || appids.isEmpty()) return
        updateInFlight = true
        scope.launch {
            try {
                toast = try {
                    summarizeUpdateStart(jobsRepository.prefill(appids), games).message
                } catch (e: VaultApiError) {
                    describeUpdateStartError(e).message
                }
            } finally {
                updateInFlight = false
            }
        }
    }

    fun pauseJob(scope: CoroutineScope, jobId: Int) = runJobControl(scope) {
        jobsRepository.pause(jobId)
        strings.pauseRequested()
    }

    fun resumeJob(scope: CoroutineScope, jobId: Int) = runJobControl(scope) {
        jobsRepository.resume(jobId)
        strings.resuming()
    }

    fun cancelJob(scope: CoroutineScope, jobId: Int) = runJobControl(scope) {
        jobsRepository.cancel(jobId)
        strings.cancelRequested()
    }

    private fun runJobControl(scope: CoroutineScope, action: suspend () -> String) {
        scope.launch {
            try {
                toast = action()
            } catch (e: VaultApiError) {
                toast = e.message ?: strings.actionFailedFallback()
            }
        }
    }

    // ---- delete -------------------------------------------------------------

    /** Only opens once [detail] (and therefore [mapping]) has loaded -- the
     * sheet's own delete button is hidden until then anyway, this is
     * defence-in-depth against a stray call. */
    fun openDeleteConfirm() {
        if (detail != null) showDeleteConfirm = true
    }

    fun closeDeleteConfirm() {
        showDeleteConfirm = false
    }

    /**
     * Mirrors `LibraryController.confirmDelete`: the toast reports what the
     * SERVER actually freed (`CacheDeletionOut.total_bytes_freed`), never
     * the preview plan the confirm dialog showed -- the server re-checks
     * every depot's co-owner state immediately before removing it and is
     * the authority, per `MultiPlan.kt`'s kdoc. [onLibraryChanged] lets the
     * caller (`LibraryScreen.kt`) kick an out-of-cadence
     * `LibraryController.refreshNow()`-equivalent so the grid doesn't wait
     * out the ambient poll interval to reflect the deletion.
     */
    fun confirmDelete(scope: CoroutineScope, onLibraryChanged: () -> Unit) {
        val appid = openAppid ?: return
        scope.launch {
            try {
                val result = cacheRepository.delete(appid)
                showDeleteConfirm = false
                toast = strings.freed(formatBytesGB(result.total_bytes_freed) ?: strings.zeroBytesLabel())
                onLibraryChanged()
                close()
            } catch (e: VaultApiError) {
                showDeleteConfirm = false
                toast = e.message ?: strings.actionFailedFallback()
            }
        }
    }

    // ---- content packages (WP TOOL-APPS-2d, ADR-0022 §3/§4) -----------------------

    /** "Retry download" on a failed package: its own `POST /v1/prefill`, the
     * same request its own card would send. */
    fun retryPackage(scope: CoroutineScope, packageAppid: Int, onLibraryChanged: () -> Unit) {
        // In-flight guard: a second tap while the request is pending does
        // nothing (the button is disabled too, see [retryingAppids]).
        if (packageAppid in retryingAppids) return
        retryingAppids = retryingAppids + packageAppid
        scope.launch {
            try {
                jobsRepository.prefill(listOf(packageAppid))
                toast = strings.queuedForDownload()
                onLibraryChanged()
            } catch (e: VaultApiError) {
                toast = e.message ?: strings.actionFailedFallback()
            } finally {
                retryingAppids = retryingAppids - packageAppid
            }
        }
    }

    /** Packages whose "Retry download" request is in flight (button disabled). */
    var retryingAppids by mutableStateOf<Set<Int>>(emptySet())
        private set

    /** Fetch the server's grouped-delete plan for the open game and show
     * it. Also the re-confirm step after a 409 "members differ" ([notice]). */
    fun openGroupDeleteConfirm(scope: CoroutineScope, notice: String? = null) {
        val appid = openAppid ?: return
        groupDelete = GroupDeleteUiState.Loading
        scope.launch {
            groupDelete = try {
                GroupDeleteUiState.Ready(cacheRepository.deletePlan(appid), notice)
            } catch (e: VaultApiError) {
                GroupDeleteUiState.Error(e.message ?: strings.actionFailedFallback())
            }
        }
    }

    fun closeGroupDeleteConfirm() {
        groupDelete = GroupDeleteUiState.Hidden
    }

    /** Delete the game with exactly the confirmed members; on a 409 "members
     * differ" fetch the plan again and ask again (nothing was deleted). */
    fun confirmGroupDelete(scope: CoroutineScope, onLibraryChanged: () -> Unit) {
        val appid = openAppid ?: return
        val ready = groupDelete as? GroupDeleteUiState.Ready ?: return
        if (isPlanBlocked(ready.plan)) return
        groupDelete = GroupDeleteUiState.Loading
        scope.launch {
            try {
                val result = cacheRepository.deleteWithPackages(appid, planMemberIds(ready.plan))
                groupDelete = GroupDeleteUiState.Hidden
                toast = strings.freed(formatBytesGB(result.total_bytes_freed) ?: strings.zeroBytesLabel())
                onLibraryChanged()
                close()
            } catch (e: VaultApiError) {
                if (isMembersMismatch(e)) {
                    onLibraryChanged()
                    openGroupDeleteConfirm(scope, MEMBERS_CHANGED_NOTICE)
                } else {
                    groupDelete = GroupDeleteUiState.Hidden
                    toast = e.message ?: strings.actionFailedFallback()
                }
            }
        }
    }

    // ---- garbage collection ---------------------------------------------------

    /** Starts a dry-run GC job (`POST .../gc` with the server's own default,
     * `execute=false`) and polls it to completion. See `GcFlow.kt`'s kdoc
     * for the state machine this drives and the guarantee it enforces. */
    fun startGcDryRun(scope: CoroutineScope) {
        val appid = openAppid ?: return
        gcState = reduceGcFlow(gcState, GcFlowEvent.StartDryRun)
        if (gcState !is GcFlowState.RequestingDryRun) return // stray call while already mid-flow -- ignored.

        gcJob?.cancel()
        gcJob = scope.launch {
            try {
                val ref = cacheRepository.gc(appid, execute = false)
                gcState = reduceGcFlow(gcState, GcFlowEvent.DryRunQueued(ref.job_id))
                pollGc(ref.job_id)
            } catch (e: VaultApiError) {
                gcState = reduceGcFlow(gcState, GcFlowEvent.DryRunFailed(e.message ?: strings.actionFailedFallback()))
            }
        }
    }

    /** User tapped "Execute" after seeing the dry-run plan -- opens the
     * second confirm dialog. Does NOT call the API (see [confirmGcExecute]). */
    fun requestGcExecute() {
        gcState = reduceGcFlow(gcState, GcFlowEvent.RequestExecute)
    }

    fun dismissGcExecuteConfirm() {
        gcState = reduceGcFlow(gcState, GcFlowEvent.DismissConfirm)
    }

    /**
     * The ONLY call site in this class that can ever cause
     * `POST .../gc {"execute":true}` to fire. `GcFlow.kt`'s [reduceGcFlow]
     * already refuses to produce [GcFlowState.RequestingExecute] from
     * anywhere but [GcFlowState.ConfirmExecute] -- the `if` guard below is
     * defence-in-depth on top of that (docs/LEARNINGS.md "redundant defence
     * layers cannot be pinned by one test", WP 4b.2's redirect-key finding:
     * pin each layer standalone), not a bet that the reducer might be
     * wrong.
     */
    fun confirmGcExecute(scope: CoroutineScope) {
        val appid = openAppid ?: return
        gcState = reduceGcFlow(gcState, GcFlowEvent.ConfirmExecute)
        if (gcState !is GcFlowState.RequestingExecute) return

        gcJob?.cancel()
        gcJob = scope.launch {
            try {
                val ref = cacheRepository.gc(appid, execute = true)
                gcState = reduceGcFlow(gcState, GcFlowEvent.ExecuteQueued(ref.job_id))
                pollGc(ref.job_id)
            } catch (e: VaultApiError) {
                gcState = reduceGcFlow(gcState, GcFlowEvent.ExecuteFailed(e.message ?: strings.actionFailedFallback()))
            }
        }
    }

    /** Dismiss the GC result/error and return to [GcFlowState.Idle] --
     * lets the user run another dry run without re-opening the sheet. */
    fun resetGc() {
        gcJob?.cancel()
        gcJob = null
        gcState = GcFlowState.Idle
    }

    /**
     * The sheet's single "Check again" action, usable from EVERY terminal
     * or plan-shown GC state -- [GcFlowState.DryRunPlan]/[GcFlowState.ExecuteDone]/
     * [GcFlowState.Error]/[GcFlowState.Cancelled] all wire their "Check
     * again" button here (review fix: the WP 4b.6 review found the
     * DryRunPlan button calling [startGcDryRun] directly did nothing, since
     * `GcFlow.kt`'s reducer only accepts [GcFlowEvent.StartDryRun] from
     * [GcFlowState.Idle]/[GcFlowState.ExecuteDone]/[GcFlowState.Error]/
     * [GcFlowState.Cancelled] -- NOT from [GcFlowState.DryRunPlan] itself).
     * [resetGc] first (a plain, synchronous state assignment) puts the flow
     * back at [GcFlowState.Idle], which [startGcDryRun] then accepts on the
     * very next line -- no reducer change needed, the state machine's
     * accepted-states table (already reviewed) is untouched.
     */
    fun restartGcDryRun(scope: CoroutineScope) {
        resetGc()
        startGcDryRun(scope)
    }

    private suspend fun pollGc(jobId: Int) {
        while (true) {
            val executing = gcState is GcFlowState.PollingExecute
            val job = try {
                jobsRepository.detail(jobId)
            } catch (e: VaultApiError) {
                gcState = GcFlowState.Error(e.message ?: strings.actionFailedFallback(), executeAttempted = executing)
                return
            }
            gcState = reduceGcFlow(gcState, GcFlowEvent.PollResult(job))
            if (gcState is GcFlowState.PollingDryRun || gcState is GcFlowState.PollingExecute) {
                delay(GC_POLL_INTERVAL_MS)
            } else {
                return // reached a terminal state (plan shown, done, cancelled, error)
            }
        }
    }
}

/** WP TOOL-APPS-2d: the detail sheet's grouped-delete confirmation. */
sealed class GroupDeleteUiState {
    data object Hidden : GroupDeleteUiState()
    data object Loading : GroupDeleteUiState()
    data class Ready(val plan: DeletePlanOut, val notice: String? = null) : GroupDeleteUiState()
    data class Error(val message: String) : GroupDeleteUiState()
}
