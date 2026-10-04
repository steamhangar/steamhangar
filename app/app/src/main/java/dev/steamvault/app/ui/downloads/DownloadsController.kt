package dev.steamvault.app.ui.downloads

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.polling.PollingIntervals
import dev.steamvault.app.repo.GamesRepository
import dev.steamvault.app.repo.JobsRepository
import dev.steamvault.app.ui.downloads.logic.BULK_TOAST_MS
import dev.steamvault.app.ui.downloads.logic.BulkKind
import dev.steamvault.app.ui.downloads.logic.TOAST_MS
import dev.steamvault.app.ui.downloads.logic.ExcerptCache
import dev.steamvault.app.ui.downloads.logic.ExcerptFetchState
import dev.steamvault.app.ui.downloads.logic.bulkBarState
import dev.steamvault.app.ui.downloads.logic.bulkPauseTargets
import dev.steamvault.app.ui.downloads.logic.bulkResumeTargets
import dev.steamvault.app.ui.downloads.logic.bulkSummary
import dev.steamvault.app.ui.downloads.logic.runBulkPause
import dev.steamvault.app.ui.downloads.logic.runBulkResume
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Everything the Downloads screen needs (WP 4b.5 brief) — same thin-glue
 * shape `ui/library/LibraryController.kt` documents: state + suspend
 * orchestration only, every DECISION delegates to `ui/downloads/logic/`
 * pure functions. Not an `androidx.lifecycle.ViewModel` (same house
 * pattern), held via `remember` in `DownloadsScreen.kt`, so it does not
 * survive leaving the Downloads tab — a fresh poll on next visit, same
 * accepted trade `LibraryController` documents.
 *
 * **Job control is non-optimistic (WP brief).** [pause]/[resume]/[cancel]
 * never mutate [jobs] locally on success — the next `GET /v1/jobs` poll
 * tick (kicked early by [refreshJobsOnce] right after the call, mirroring
 * `web`'s `store.refreshNow()`) is what actually updates what is on
 * screen. This matches `web/js/views/downloads.js`'s documented "server
 * confirms" pattern exactly, and it is why [busyJobIds] — not a locally
 * guessed job status — is what disables a button for the duration of its
 * own network call (WP brief: "busy-state on buttons during the call").
 */
class DownloadsController(
    private val jobsRepository: JobsRepository,
    private val gamesRepository: GamesRepository,
    private val strings: DownloadsStrings,
) {
    var jobs by mutableStateOf<List<JobSummary>>(emptyList())
        private set
    var games by mutableStateOf<List<GameSummary>>(emptyList())
        private set
    var loadError by mutableStateOf<String?>(null)
        private set
    var toast by mutableStateOf<String?>(null)
        private set

    /** How long the screen shows [toast]; the aggregate bulk toast may carry
     * a server reason, so it stays longer (web `BULK_TOAST_MS`). */
    var toastMs: Long = TOAST_MS
        private set

    /** Job ids with a job-control call currently in flight FROM THIS
     * SCREEN — client-side only, cleared in the action's `finally` block
     * regardless of outcome. Deliberately not part of
     * `ui/downloads/logic/JobCardModel.kt`'s `JobCardModel` — see that
     * file's kdoc for why keeping it separate is what makes the
     * stop_request-only-diff stability guarantee provable at all. */
    var busyJobIds by mutableStateOf<Set<Int>>(emptySet())
        private set

    /** `true` once one `GET /v1/games` poll succeeded -- gates the owned-
     * names load below (web `state.gamesKnown`): before the first games
     * poll every job "lacks a vault name" and would trigger it for nothing. */
    var gamesKnown by mutableStateOf(false)
        private set

    /** WP APP-FIX-2: appids with a failure-hint Retry call in flight. */
    var retryBusyAppids by mutableStateOf<Set<Int>>(emptySet())
        private set

    /** WP APP-FIX-2: history rows whose collapsed raw SteamPrefill output
     * (under a failure hint) the user opened. Closed by default, kept per
     * job for the life of this screen (web `st.rawOpen`). */
    var rawOutputOpenJobIds by mutableStateOf<Set<Int>>(emptySet())
        private set

    fun toggleRawOutput(jobId: Int) {
        rawOutputOpenJobIds = if (jobId in rawOutputOpenJobIds) rawOutputOpenJobIds - jobId else rawOutputOpenJobIds + jobId
    }

    private val excerptCache = ExcerptCache(fetchExcerpt = { jobId ->
        try {
            Result.success(jobsRepository.detail(jobId).log_excerpt ?: "")
        } catch (e: VaultApiError) {
            Result.failure(Exception(e.message ?: strings.logFetchErrorFallback(), e))
        }
    })

    /** Bumped after every [excerptCache] mutation so a composable that
     * reads it (alongside [excerptStateFor]) is scheduled to recompose --
     * see `DownloadsScreen.kt`'s `HistoryRow` for the read site. Needed
     * because [ExcerptCache] itself is a plain (Compose-free, by design --
     * see its kdoc) class, so its internal map mutations are not
     * Snapshot-observable on their own. `mutableIntStateOf` rather than the
     * generic `mutableStateOf<Int>` -- avoids the autoboxing a generic
     * `Int` snapshot state incurs (AGP lint's `AutoboxingStateCreation`
     * check). */
    var excerptVersion by mutableIntStateOf(0)
        private set

    fun excerptStateFor(jobId: Int): ExcerptFetchState = excerptCache.stateFor(jobId)

    /** Toggle a history row's expansion; kicks off the lazy fetch only when
     * the row is now expanded (mirrors `web`'s `toggleHistoryRow`). */
    fun toggleHistoryRow(scope: CoroutineScope, jobId: Int) {
        val next = excerptCache.toggleExpanded(jobId)
        excerptVersion++
        if (next.expanded) {
            scope.launch {
                excerptCache.ensureLoaded(jobId)
                excerptVersion++
            }
        }
    }

    // ---- polling (called from LaunchedEffect + repeatOnLifecycle in DownloadsScreen.kt) --

    /** Adaptive-cadence poll (fast while a job is active), same decision
     * [PollingIntervals.nextJobsIntervalMs] already makes for the rest of
     * the app. */
    suspend fun pollJobsForever() {
        while (true) {
            refreshJobsOnce()
            delay(PollingIntervals.nextJobsIntervalMs(jobs))
        }
    }

    /** Slow, fixed-cadence poll — this screen only needs `games` for name
     * lookups (`nameFor` in `ui/downloads/logic/JobCardModel.kt`), same
     * cadence `LibraryController.pollGamesForever` uses. */
    suspend fun pollGamesForever() {
        while (true) {
            refreshGamesOnce()
            delay(PollingIntervals.GAMES_MS)
        }
    }

    suspend fun refreshJobsOnce() {
        try {
            jobs = jobsRepository.list()
            loadError = null
        } catch (e: VaultApiError) {
            loadError = e.message
        }
    }

    suspend fun refreshGamesOnce() {
        try {
            games = gamesRepository.list()
            gamesKnown = true
        } catch (_: VaultApiError) {
            // A games-poll hiccup degrades name lookups to "App {id}"
            // (nameFor's own fallback) rather than blanking the screen --
            // this screen's own error banner is owned by the jobs poll.
        }
    }

    fun dismissToast() {
        toast = null
        toastMs = TOAST_MS
    }

    // ---- job control ----------------------------------------------------

    fun pause(scope: CoroutineScope, jobId: Int) = runControl(scope, jobId) {
        jobsRepository.pause(jobId)
        strings.pauseRequested()
    }

    fun resume(scope: CoroutineScope, jobId: Int) = runControl(scope, jobId) {
        jobsRepository.resume(jobId)
        strings.resuming()
    }

    fun cancel(scope: CoroutineScope, jobId: Int) = runControl(scope, jobId) {
        jobsRepository.cancel(jobId)
        strings.cancelRequested()
    }

    /**
     * WP APP-FIX-2: a failure hint's Retry -- a new prefill for the same app
     * (`POST /v1/prefill`; vault-api dedupes an in-flight one), then an
     * out-of-cadence jobs refresh. Mirrors web `downloads.js::onRetry`.
     */
    fun retry(scope: CoroutineScope, appid: Int) {
        if (appid in retryBusyAppids) return
        retryBusyAppids = retryBusyAppids + appid
        scope.launch {
            try {
                jobsRepository.prefill(listOf(appid))
                toast = strings.queuedForDownload()
                refreshJobsOnce()
            } catch (e: VaultApiError) {
                toast = e.message ?: strings.actionFailedFallback()
            } finally {
                retryBusyAppids = retryBusyAppids - appid
            }
        }
    }

    // ---- Pause all / Resume all (WP WEB-FEAT-5, web downloads.js runBulk) --

    /** The bulk run in flight, or null. Both bulk buttons are disabled
     * while it is set. */
    var bulkBusy by mutableStateOf<BulkKind?>(null)
        private set

    /** Whether the Pause all confirm dialog is up. */
    var pauseAllConfirmOpen by mutableStateOf(false)
        private set

    /** Pause all's button: asks first (the dialog names the count). */
    fun requestPauseAll() {
        if (bulkBusy != null || !bulkBarState(jobs).pauseVisible) return
        pauseAllConfirmOpen = true
    }

    fun dismissPauseAll() {
        pauseAllConfirmOpen = false
    }

    /** The dialog's "Pause all". Targets are taken from [jobs] as they are
     * NOW, so anything that finished while the dialog was up is not sent. */
    fun confirmPauseAll(scope: CoroutineScope) {
        pauseAllConfirmOpen = false
        runBulk(scope, BulkKind.PAUSE)
    }

    /** Resume all's button: no confirmation. */
    fun resumeAll(scope: CoroutineScope) = runBulk(scope, BulkKind.RESUME)

    private fun runBulk(scope: CoroutineScope, kind: BulkKind) {
        if (bulkBusy != null) return
        val pauseTargets = bulkPauseTargets(jobs)
        val resumeTargets = bulkResumeTargets(jobs)
        val total = if (kind == BulkKind.PAUSE) pauseTargets.count else resumeTargets.size
        if (total == 0) return
        bulkBusy = kind
        scope.launch {
            try {
                val results = if (kind == BulkKind.PAUSE) {
                    runBulkPause(pauseTargets, { id -> jobsRepository.pause(id) })
                } else {
                    runBulkResume(resumeTargets, { id -> jobsRepository.resume(id) })
                }
                toastMs = BULK_TOAST_MS
                toast = bulkSummary(kind, results).text
            } finally {
                bulkBusy = null
            }
            refreshJobsOnce() // out-of-cadence refresh, mirrors store.refreshNow()
        }
    }

    private fun runControl(scope: CoroutineScope, jobId: Int, action: suspend () -> String) {
        if (jobId in busyJobIds) return
        busyJobIds = busyJobIds + jobId
        scope.launch {
            try {
                toast = action()
                refreshJobsOnce() // out-of-cadence refresh, mirrors store.refreshNow()
            } catch (e: VaultApiError) {
                toast = e.message ?: strings.actionFailedFallback()
            } finally {
                busyJobIds = busyJobIds - jobId
            }
        }
    }
}
