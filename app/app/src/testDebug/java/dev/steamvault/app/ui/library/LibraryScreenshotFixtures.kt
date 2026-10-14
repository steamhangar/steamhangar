package dev.steamvault.app.ui.library

import dev.steamvault.app.net.model.CacheDeletionOut
import dev.steamvault.app.net.model.GameDetail
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.GcJobRef
import dev.steamvault.app.net.model.JobControlOut
import dev.steamvault.app.net.model.JobDetail
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.net.model.MappingEntry
import dev.steamvault.app.net.model.OwnedGame
import dev.steamvault.app.net.model.OwnedGamesRelayOut
import dev.steamvault.app.net.model.PlayerSummariesRelayOut
import dev.steamvault.app.net.model.PrefillJobRef
import dev.steamvault.app.net.model.SettingInfoOut
import dev.steamvault.app.net.model.SettingsOut
import dev.steamvault.app.net.model.UpdateCheckOut
import dev.steamvault.app.net.model.UpdateStatusOut
import dev.steamvault.app.repo.CacheRepository
import dev.steamvault.app.repo.GamesRepository
import dev.steamvault.app.repo.JobsRepository
import dev.steamvault.app.repo.MappingRepository
import dev.steamvault.app.repo.SettingsRepository
import dev.steamvault.app.repo.SteamRelayRepository
import dev.steamvault.app.repo.UpdatesRepository
import dev.steamvault.app.storage.LibraryPreferences
import dev.steamvault.app.ui.library.logic.LibraryLayout
import dev.steamvault.app.ui.library.logic.STEAM_LIBRARY_SETTING_KEY
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonPrimitive

/**
 * Fixed, synthetic library for the screenshot tests (WP APP-TEST-1).
 *
 * Built to make the D12 layout problems visible
 * (`docs/handover/post-0.1.0-roadmap.md` "D12"): every filter chip but
 * "Installed, not cached" has a non-zero count (FilterChipsRow carries all
 * seven labels, the widest being "Installed, not cached 0"), titles range
 * from short to very long, and every card kind the grid can show appears
 * at least once (cached, not cached, downloading, updating, paused,
 * failed). WP UPD-6: both first-row cards carry update_available (the
 * second row is not composed at 360 dp / font 1.3): the cached Lighthouse
 * Keeper game shows the update state (orange refresh glyph, "Update
 * available" instead of "Current"), and Starfall Odyssey, updating under a
 * live job, keeps the separate "Update available" badge (WP UPD-3) because
 * its status word does not say it. Both give the "Updates" chip a count,
 * and [FixedUpdatesRepository] keeps the top bar's "Check updates" action
 * visible. WP TOOL-APPS-2d: Starfall Odyssey heads a
 * content-package group of two packages, one failed ("+2 content packages
 * · 1 failed", sized by the server's `group_size_bytes`), and the Lighthouse Keeper game
 * is cached with an owned playtime of exactly 0 (the "Never played" pill).
 * Names are invented, appids are
 * synthetic (docs/LEARNINGS.md "Fixtures: synthetic only").
 *
 * Deliberately NOT time-dependent: no `Instant.now()` anywhere (the demo
 * fixtures in `dev.steamvault.app.demo` use it, which is why they are not
 * reused here), and no `installed_on` entries, whose badge renders through
 * the JVM's default locale and time zone. A golden must come out
 * byte-identical on every run.
 */
internal object LibraryScreenshotFixtures {

    private const val PREFILLED_AT = "2026-09-28T08:00:00Z"
    private const val JOB_CREATED_AT = "2026-10-13T07:30:00Z"

    /** Synthetic 17-digit SteamID64; only its presence matters here. */
    private const val STEAM_ID = "76561190000000001"

    val vaultGames: List<GameSummary> = listOf(
        GameSummary(
            appid = 1_000_010, name = "Starfall Odyssey", status = "done",
            last_prefill_at = PREFILLED_AT, depot_count = 2, size_bytes = 48_300_000_000,
            // WP UPD-6: flagged, but Updating under a live job (id 45) ->
            // the separate badge.
            update_available = true, update_checked_at = PREFILLED_AT,
            // WP TOOL-APPS-2d: heads the group below.
            app_type = "game", group_size_bytes = 48_900_000_000,
        ),
        // WP TOOL-APPS-2d: two content packages of Starfall Odyssey -- not
        // cards of their own; one failed.
        GameSummary(
            appid = 1_000_011, name = "Starfall Odyssey - Original Soundtrack", status = "done",
            last_prefill_at = PREFILLED_AT, depot_count = 1, size_bytes = 600_000_000,
            parent_appid = 1_000_010, app_type = "music",
        ),
        GameSummary(
            appid = 1_000_012, name = "Starfall Odyssey - Expedition Pass", status = "error",
            depot_count = 1, parent_appid = 1_000_010, app_type = "dlc",
        ),
        GameSummary(
            appid = 1_000_020,
            name = "The Unreasonably Long Chronicles of the Northern Lighthouse Keeper: Definitive Anniversary Edition",
            status = "done", last_prefill_at = PREFILLED_AT, depot_count = 3, size_bytes = 12_700_000_000,
            // WP UPD-6: cached and flagged -> the card's update state.
            update_available = true, update_checked_at = PREFILLED_AT,
        ),
        // First download, live job, never prefilled -> "Downloading".
        GameSummary(appid = 1_000_030, name = "Ironwood Hollow", status = "running", depot_count = 1),
        // Already cached, live job -> "Updating" (also counts as Downloading).
        GameSummary(
            appid = 1_000_040, name = "Glasswing", status = "running",
            last_prefill_at = PREFILLED_AT, depot_count = 1, size_bytes = 3_400_000_000,
        ),
        // Paused job -> "Paused" (counts as Downloading).
        GameSummary(appid = 1_000_050, name = "Harbor Lights Café Simulator", status = "running", depot_count = 1),
        GameSummary(
            appid = 1_000_060, name = "Tundra Protocol: Operation Whiteout – Complete Season Pass Bundle",
            status = "error", depot_count = 1,
        ),
        GameSummary(
            appid = 1_000_070, name = "Pixel Garden", status = "done",
            last_prefill_at = PREFILLED_AT, depot_count = 1, size_bytes = 1_200_000_000,
        ),
    )

    val jobs: List<JobSummary> = listOf(
        JobSummary(id = 41, appid = 1_000_030, type = "prefill", status = "running", created_at = JOB_CREATED_AT, started_at = JOB_CREATED_AT),
        JobSummary(id = 42, appid = 1_000_040, type = "prefill", status = "running", created_at = JOB_CREATED_AT, started_at = JOB_CREATED_AT),
        JobSummary(
            id = 43, appid = 1_000_050, type = "prefill", status = "paused", created_at = JOB_CREATED_AT,
            started_at = JOB_CREATED_AT, paused_at = JOB_CREATED_AT,
        ),
        JobSummary(id = 44, appid = 1_000_070, type = "prefill", status = "queued", created_at = JOB_CREATED_AT),
        // WP UPD-6: Starfall Odyssey updating while flagged (the badge's card).
        JobSummary(id = 45, appid = 1_000_010, type = "prefill", status = "running", created_at = JOB_CREATED_AT, started_at = JOB_CREATED_AT),
    )

    /** The Steam relay's owned list: every vault game plus three the vault
     * has never seen (the "Not cached" chip). */
    val ownedGames: List<OwnedGame> = vaultGames
        // Steam's owned list names games, not their content packages.
        .filter { it.parent_appid == null }
        .map {
            // WP TOOL-APPS-2d: the Lighthouse Keeper game was never played (a real 0).
            OwnedGame(appid = it.appid, name = it.name ?: "", playtime_forever = if (it.appid == 1_000_020) 0 else null)
        } + listOf(
        OwnedGame(appid = 1_000_080, name = "Mechanized Frontier Tactics: Remastered Collection"),
        OwnedGame(appid = 1_000_090, name = "Quiet Orchard"),
        OwnedGame(appid = 1_000_100, name = "Echoes of the Sunken Cathedral"),
    )

    val settings = SettingsOut(
        readonly = false,
        settings = listOf(
            SettingInfoOut(
                key = STEAM_LIBRARY_SETTING_KEY, effective = JsonPrimitive(STEAM_ID),
                source = "db", applies = "immediately",
            ),
        ),
    )
}

private fun notUsed(): Nothing =
    error("not reachable from a screenshot test: nothing is tapped, only rendered")

internal class FixedGamesRepository(private val games: List<GameSummary>) : GamesRepository {
    override suspend fun list(): List<GameSummary> = games
    override suspend fun detail(appid: Int): GameDetail = notUsed()
}

internal class FixedJobsRepository(private val jobs: List<JobSummary>) : JobsRepository {
    override suspend fun list(limit: Int): List<JobSummary> = jobs.take(limit)
    override suspend fun detail(id: Int): JobDetail = notUsed()
    override suspend fun prefill(appids: List<Int>): List<PrefillJobRef> = notUsed()
    override suspend fun prefillCached(): List<PrefillJobRef> = notUsed()
    override suspend fun cancel(id: Int): JobControlOut = notUsed()
    override suspend fun pause(id: Int): JobControlOut = notUsed()
    override suspend fun resume(id: Int): JobControlOut = notUsed()
}

internal class EmptyMappingRepository : MappingRepository {
    override suspend fun list(): List<MappingEntry> = emptyList()
}

internal class UnusedCacheRepository : CacheRepository {
    override suspend fun delete(appid: Int): CacheDeletionOut = notUsed()
    override suspend fun gc(appid: Int, execute: Boolean): GcJobRef = notUsed()
}

internal class FixedSettingsRepository(private val settings: SettingsOut) : SettingsRepository {
    override suspend fun get(): SettingsOut = settings
    override suspend fun patch(updates: Map<String, JsonElement?>): SettingsOut = notUsed()
}

internal class FixedSteamRelayRepository(private val owned: List<OwnedGame>) : SteamRelayRepository {
    override suspend fun ownedGames(steamId64: String): OwnedGamesRelayOut =
        OwnedGamesRelayOut(configured = true, game_count = owned.size, games = owned)

    override suspend fun playerSummaries(steamId64: String): PlayerSummariesRelayOut = notUsed()
}

/** WP UPD-3: a vault WITH the update check (so the action and the chip
 * render), idle, last run long finished. The screen only probes [status];
 * nothing is tapped, so [check] is never reached. */
internal class FixedUpdatesRepository : UpdatesRepository {
    override suspend fun check(): UpdateCheckOut = notUsed()
    override suspend fun status(): UpdateStatusOut = UpdateStatusOut(
        enabled = true,
        running = false,
        last_trigger = "scheduled",
        last_outcome = "completed",
        last_apps = 4,
        last_ok = 4,
        last_failed = 0,
        last_stale = 1,
    )
}

internal class FixedLibraryPreferences(private val layout: LibraryLayout) : LibraryPreferences {
    override fun getLayout(): LibraryLayout = layout
    override fun setLayout(layout: LibraryLayout) = Unit
}
