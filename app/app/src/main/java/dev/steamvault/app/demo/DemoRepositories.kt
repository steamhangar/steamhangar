package dev.steamvault.app.demo

import dev.steamvault.app.net.model.AboutOut
import dev.steamvault.app.net.model.CacheDeletionOut
import dev.steamvault.app.net.model.ClientOut
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
import dev.steamvault.app.net.model.SettingsOut
import dev.steamvault.app.repo.AboutRepository
import dev.steamvault.app.repo.CacheRepository
import dev.steamvault.app.repo.ClientsRepository
import dev.steamvault.app.repo.GamesRepository
import dev.steamvault.app.repo.JobsRepository
import dev.steamvault.app.repo.MappingRepository
import dev.steamvault.app.repo.ScheduleRepository
import dev.steamvault.app.repo.SettingsRepository
import dev.steamvault.app.repo.SteamRelayRepository
import kotlinx.serialization.json.JsonElement

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
    override suspend fun cancel(id: Int): JobControlOut = state.controlJob(id, JobControlAction.CANCEL)
    override suspend fun pause(id: Int): JobControlOut = state.controlJob(id, JobControlAction.PAUSE)
    override suspend fun resume(id: Int): JobControlOut = state.controlJob(id, JobControlAction.RESUME)
}

class DemoCacheRepository(private val state: DemoState) : CacheRepository {
    override suspend fun delete(appid: Int): CacheDeletionOut = state.deleteCache(appid)
    override suspend fun gc(appid: Int, execute: Boolean): GcJobRef = state.gc(appid, execute)
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
