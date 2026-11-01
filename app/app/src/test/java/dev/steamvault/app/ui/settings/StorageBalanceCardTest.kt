package dev.steamvault.app.ui.settings

import dev.steamvault.app.demo.DemoAboutRepository
import dev.steamvault.app.demo.DemoCacheRepository
import dev.steamvault.app.demo.DemoScheduleRepository
import dev.steamvault.app.demo.DemoSettingsRepository
import dev.steamvault.app.demo.DemoState
import dev.steamvault.app.demo.DemoSteamRelayRepository
import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.CacheDeletionOut
import dev.steamvault.app.net.model.CacheSummaryOut
import dev.steamvault.app.net.model.GcJobRef
import dev.steamvault.app.net.model.OwnedGame
import dev.steamvault.app.net.model.StorageBalanceOut
import dev.steamvault.app.net.model.UnmappedDepotsOut
import dev.steamvault.app.repo.CacheRepository
import dev.steamvault.app.repo.SteamIdentityRepository
import dev.steamvault.app.repo.SteamIdentityState
import dev.steamvault.app.repo.SteamLoginResult
import dev.steamvault.app.storage.InMemoryCredentialStore
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

private class BalanceNoIdentity : SteamIdentityRepository {
    override fun state(): SteamIdentityState = SteamIdentityState(null, null)
    override fun buildLoginUrl(vaultBaseUrl: String): String? = null
    override suspend fun completeLogin(rawCallbackUrl: String): SteamLoginResult = SteamLoginResult.Success("76561198042117903")
    override suspend fun refreshPersonaName(): Boolean = false
    override suspend fun ownedGamesCountPreview(): Result<Int> = Result.success(0)
    override suspend fun ownedGames(): Result<List<OwnedGame>> = Result.success(emptyList())
    override fun signOut() {}
}

private class BalanceStrings : SettingsStrings {
    override fun loadFailedFallback(cause: Throwable) = "load failed"
    override fun savedToast() = "saved"
    override fun saveFailedFallback() = "save failed"
    override fun libraryIdSaved() = "id saved"
    override fun libraryIdCleared() = "id cleared"
    override fun libraryIdUnchanged() = "id unchanged"
    override fun libraryIdReset() = "id reset"
}

/** A summary the test scripts per call: an answer or a failure. */
private class ScriptedCache : CacheRepository {
    var next: CacheSummaryOut? = null
    var calls = 0
    override suspend fun delete(appid: Int): CacheDeletionOut = throw UnsupportedOperationException()
    override suspend fun gc(appid: Int, execute: Boolean): GcJobRef = throw UnsupportedOperationException()
    override suspend fun summary(): CacheSummaryOut {
        calls += 1
        return next ?: throw VaultApiError.Server("boom", status = 500, detail = "down")
    }
}

/**
 * WP ANDROID-RC21-1-TWINS: the controller half of the Expert panel's
 * "Storage balance" card (web STORAGE-BALANCE-1 wiring test): hidden for
 * an older server, filled from the summary, a failed fetch keeps the last
 * figures, a downgrade hides it again; plus source pins for where the card
 * is drawn and when the summary is fetched.
 */
class StorageBalanceCardTest {

    private val gb = 1L shl 30

    private fun controller(demo: DemoState, cache: CacheRepository?) = SettingsController(
        DemoSettingsRepository(demo),
        DemoScheduleRepository(demo),
        InMemoryCredentialStore(),
        BalanceNoIdentity(),
        BalanceStrings(),
        DemoSteamRelayRepository(demo),
        DemoAboutRepository(demo),
        cacheRepository = cache,
    )

    private fun summary(b: StorageBalanceOut?) = CacheSummaryOut(
        total_bytes = 100 * gb,
        unmapped_depots = UnmappedDepotsOut(1, 5 * gb),
        storage_balance = b,
    )

    private val balance = StorageBalanceOut(
        on_disk_bytes = 100 * gb,
        planned_bytes = 70 * gb,
        present_bytes = 60 * gb,
        measured_depots = 3,
        old_builds_bytes = 20 * gb,
        unmeasured_bytes = 15 * gb,
        unmeasured_depots = 2,
        unmapped_bytes = 5 * gb,
        unmapped_depots = 1,
        unexplained_bytes = 0,
        warning = true,
        warning_reason = "Old builds take 20.0 GB",
    )

    @Test
    fun `MUTATION TARGET -- hidden for an older server, filled, a failure keeps the figures, a downgrade hides again`() = runTest {
        val cache = ScriptedCache()
        val c = controller(DemoState.fresh(), cache)
        assertNull("nothing before the first fetch", c.storageBalance)

        cache.next = summary(null)
        c.loadCacheSummary()
        assertNull("older server: no card", c.storageBalance)

        cache.next = summary(balance)
        c.loadCacheSummary()
        val view = c.storageBalance
        assertNotNull(view)
        assertEquals(5, view!!.tiles.size)
        assertEquals("Old builds take 20.0 GB", view.warning)

        cache.next = null
        c.loadCacheSummary()
        assertEquals("a failed fetch keeps the last figures", view, c.storageBalance)

        cache.next = summary(null)
        c.loadCacheSummary()
        assertNull("a downgrade hides the card again", c.storageBalance)
        assertEquals(4, cache.calls)
    }

    @Test
    fun `no cache repository, no card and no fetch`() = runTest {
        val c = controller(DemoState.fresh(), null)
        c.loadCacheSummary()
        assertNull(c.storageBalance)
    }

    @Test
    fun `demo mode shows a consistent balance`() = runTest {
        val demo = DemoState.fresh()
        val c = controller(demo, DemoCacheRepository(demo))
        c.loadCacheSummary()
        assertTrue(c.storageBalance!!.accounted)
    }

    // ---- source pins --------------------------------------------------------

    private fun code(path: String): String {
        val file = File(path)
        check(file.exists()) { "expected a source file at ${file.absolutePath}" }
        return file.readText(Charsets.UTF_8)
            .replace(Regex("/\\*.*?\\*/", RegexOption.DOT_MATCHES_ALL), "")
            .replace(Regex("//.*"), "")
    }

    private val screen = "src/main/java/dev/steamvault/app/ui/settings/SettingsScreen.kt"
    private val main = "src/main/java/dev/steamvault/app/MainActivity.kt"

    @Test
    fun `MUTATION PIN -- the card sits at the end of the Expert panel and is fetched when the panel opens`() {
        val text = code(screen)
        assertTrue(text.contains("if (current == EXPERT_SECTION) controller.loadCacheSummary()"))
        assertTrue(text.contains("LaunchedEffect(controller, current) {"))
        val card = text.indexOf("controller.storageBalance?.let { StorageBalanceCard(it) }")
        assertTrue("the card is drawn from the controller's view", card >= 0)
        val gate = text.lastIndexOf("if (current == EXPERT_SECTION && controller.expertOn) {", card)
        val form = text.lastIndexOf("else -> SettingsForm(controller, current)", card)
        assertTrue("only on the Expert panel, after the form", gate in (form + 1) until card)
        val start = text.indexOf("private fun StorageBalanceCard(")
        val end = text.indexOf("private fun ExpertSwitchRow(", start)
        check(start >= 0 && end > start) { "expected StorageBalanceCard before ExpertSwitchRow" }
        val body = text.substring(start, end)
        assertTrue(body.contains("FieldCard(StorageBalanceText.TITLE)"))
        assertTrue("the warning on top", body.contains("view.warning?.let"))
        assertTrue("one tile per figure", body.contains("for (tile in view.tiles)"))
        assertTrue("the note line", body.contains("Text(view.note,"))
    }

    @Test
    fun `MUTATION PIN -- both connections hand the controller a cache repository`() {
        val text = code(main)
        assertTrue(text.contains("settingsViewPreferences,\n                VaultCacheRepository(it),\n            )"))
        assertTrue(text.contains("settingsViewPreferences,\n            DemoCacheRepository(demo),\n        )"))
    }
}
