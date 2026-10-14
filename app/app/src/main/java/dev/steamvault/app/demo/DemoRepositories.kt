package dev.steamvault.app.demo

import dev.steamvault.app.net.model.AboutOut
import dev.steamvault.app.net.model.CacheDeletionOut
import dev.steamvault.app.net.model.ClientsOverviewOut
import dev.steamvault.app.net.model.ClientOut
import dev.steamvault.app.net.model.DeletePlanOut
import dev.steamvault.app.net.model.GameDetail
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.GcJobRef
import dev.steamvault.app.net.model.JobControlOut
import dev.steamvault.app.net.model.JobDetail
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.net.model.MappingEntry
import dev.steamvault.app.net.model.OwnedGamesRelayOut
import dev.steamvault.app.net.model.PlayerSummariesRelayOut
import dev.steamvault.app.net.model.PrefillJobRef
import dev.steamvault.app.net.model.ScheduleOut
import dev.steamvault.app.net.model.SeriesOut
import dev.steamvault.app.net.model.SettingsOut
import dev.steamvault.app.net.model.ThroughputOut
import dev.steamvault.app.net.model.UpdateCheckOut
import dev.steamvault.app.net.model.UpdateStatusOut
import dev.steamvault.app.repo.AboutRepository
import dev.steamvault.app.repo.CacheRepository
import dev.steamvault.app.repo.ClientsRepository
import dev.steamvault.app.repo.GamesRepository
import dev.steamvault.app.repo.JobsRepository
import dev.steamvault.app.repo.MappingRepository
import dev.steamvault.app.repo.ScheduleRepository
import dev.steamvault.app.repo.SettingsRepository
import dev.steamvault.app.repo.SteamRelayRepository
import dev.steamvault.app.repo.TrafficRepository
import dev.steamvault.app.repo.UpdatesRepository
import kotlinx.serialization.json.JsonElement
import java.time.Instant

/**
 * Demo mode's repository implementations (WP APP-DEMO brief: "put the seam
 * where the real fetcher is injected" -- the same
 * [dev.steamvault.app.repo.GamesRepository]/[dev.steamvault.app.repo.JobsRepository]/
 * [dev.steamvault.app.repo.CacheRepository]/[dev.steamvault.app.repo.ClientsRepository]/
 * [dev.steamvault.app.repo.MappingRepository]/[dev.steamvault.app.repo.SettingsRepository]
 * interfaces every screen controller already depends on, so
 * [dev.steamvault.app.ui.library.LibraryScreen]/[dev.steamvault.app.ui.downloads.DownloadsScreen]/
 * [dev.steamvault.app.ui.settings.SettingsScreen]/[dev.steamvault.app.ui.detail.GameDetailSheet]
 * need no demo-mode branch of their own at all -- they just get handed a
 * different implementation of the same interface).
 *
 * **Structurally network-free.** None of the six classes below reference
 * [dev.steamvault.app.net.VaultApiClient], OkHttp, or any other
 * network-capable type -- every method is a direct call into [DemoState],
 * which holds nothing but plain in-memory Kotlin collections. See
 * `DemoModeNetworkIsolationTest` (the structural pin, same source-scan
 * technique `SteamKeyIsolationTest` already uses for a different
 * invariant) for the machine-checked version of this claim.
 */
class DemoGamesRepository(private val state: DemoState) : GamesRepository {
    override suspend fun list(): List<GameSummary> = state.listGameSummaries()
    override suspend fun detail(appid: Int): GameDetail = state.gameDetail(appid)
}

class DemoJobsRepository(private val state: DemoState) : JobsRepository {
    override suspend fun list(limit: Int): List<JobSummary> = state.listJobSummaries(limit)
    override suspend fun detail(id: Int): JobDetail = state.jobDetail(id)
    override suspend fun prefill(appids: List<Int>): List<PrefillJobRef> = state.enqueuePrefill(appids)
    override suspend fun prefillCached(): List<PrefillJobRef> = state.enqueuePrefillCached()
    override suspend fun prefillVerify(appids: List<Int>): List<PrefillJobRef> = state.enqueueVerify(appids)
    override suspend fun cancel(id: Int): JobControlOut = state.controlJob(id, JobControlAction.CANCEL)
    override suspend fun pause(id: Int): JobControlOut = state.controlJob(id, JobControlAction.PAUSE)
    override suspend fun resume(id: Int): JobControlOut = state.controlJob(id, JobControlAction.RESUME)
}

class DemoCacheRepository(private val state: DemoState) : CacheRepository {
    override suspend fun delete(appid: Int): CacheDeletionOut = state.deleteCache(appid)
    override suspend fun gc(appid: Int, execute: Boolean): GcJobRef = state.gc(appid, execute)
    override suspend fun deletePlan(appid: Int): DeletePlanOut = state.deletePlan(appid)
    override suspend fun deleteWithPackages(appid: Int, members: List<Int>): CacheDeletionOut =
        state.deleteCacheWithPackages(appid, members)
}

class DemoClientsRepository(private val state: DemoState) : ClientsRepository {
    override suspend fun list(): List<ClientOut> = state.clientsOut()
}

class DemoMappingRepository(private val state: DemoState) : MappingRepository {
    override suspend fun list(): List<MappingEntry> = state.mapping()
}

class DemoSettingsRepository(private val state: DemoState) : SettingsRepository {
    override suspend fun get(): SettingsOut = state.settingsOut()
    override suspend fun patch(updates: Map<String, JsonElement?>): SettingsOut = state.patchSettings(updates)
}

/** WP AG-3: the seventh demo repository. See [DemoState.scheduleOut]'s kdoc
 * for why `sweep_include_cached`/`sweep_cached_gc_risk` react live to a
 * settings PATCH while `last_sweep_*` stay a static fixture. */
class DemoScheduleRepository(private val state: DemoState) : ScheduleRepository {
    override suspend fun get(): ScheduleOut = state.scheduleOut()
}

/** WP APP-FEAT-2: `GET /v1/about` in demo mode -- see [seedAbout]. */
class DemoAboutRepository(private val state: DemoState) : AboutRepository {
    override suspend fun get(): AboutOut = state.aboutOut()
}

/** WP APP-FEAT-1: the relay for an explicit SteamID64 in demo mode -- see
 * [DemoState.ownedGames]. */
class DemoSteamRelayRepository(private val state: DemoState) : SteamRelayRepository {
    override suspend fun ownedGames(steamId64: String): OwnedGamesRelayOut = state.ownedGames(steamId64)
    override suspend fun playerSummaries(steamId64: String): PlayerSummariesRelayOut = state.playerSummaries(steamId64)
}

/**
 * WP UPD-3: "Check updates" in demo mode. Nothing is asked of any update
 * source: the check "finishes" at once, counting the demo's cached games
 * and reporting the ones whose fixture carries `update_available`.
 */
class DemoUpdatesRepository(private val state: DemoState) : UpdatesRepository {
    override suspend fun check(): UpdateCheckOut = UpdateCheckOut(status = "started", enabled = true)

    override suspend fun status(): UpdateStatusOut {
        val cached = state.listGameSummaries().filter { (it.size_bytes ?: 0L) > 0L && !it.tool_app }
        return UpdateStatusOut(
            enabled = true,
            running = false,
            last_trigger = "manual",
            last_outcome = "completed",
            last_apps = cached.size,
            last_ok = cached.size,
            last_failed = 0,
            last_stale = cached.count { it.update_available },
        )
    }
}

/**
 * WP D15d: the traffic statistics in demo mode (Downloads header and graph,
 * the Clients page, the History filter) -- synthetic, deterministic per
 * range, built by `DemoTraffic.kt` from the demo's own jobs, games and
 * agents. The feed is always `ok` here, so every screen shows its figures.
 */
class DemoTrafficRepository(private val state: DemoState) : TrafficRepository {
    override suspend fun throughput(range: String): ThroughputOut =
        demoThroughput(range, state.trafficSnapshot(), Instant.now().toEpochMilli())

    override suspend fun series(range: String): SeriesOut = demoSeries(range, Instant.now().toEpochMilli())

    override suspend fun clientsOverview(range: String): ClientsOverviewOut =
        demoClientsOverview(range, state.trafficSnapshot(), Instant.now().toEpochMilli())

    override suspend fun jobHistory(range: String, limit: Int): List<JobSummary> = state.jobHistory(limit)
}
