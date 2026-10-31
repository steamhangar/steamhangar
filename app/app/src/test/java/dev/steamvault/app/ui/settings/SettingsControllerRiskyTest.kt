package dev.steamvault.app.ui.settings

import dev.steamvault.app.demo.DemoAboutRepository
import dev.steamvault.app.demo.DemoScheduleRepository
import dev.steamvault.app.demo.DemoSettingsRepository
import dev.steamvault.app.demo.DemoState
import dev.steamvault.app.demo.DemoSteamRelayRepository
import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.OwnedGame
import dev.steamvault.app.net.model.SettingsOut
import dev.steamvault.app.repo.SettingsRepository
import dev.steamvault.app.repo.SteamIdentityRepository
import dev.steamvault.app.repo.SteamIdentityState
import dev.steamvault.app.repo.SteamLoginResult
import dev.steamvault.app.storage.InMemoryCredentialStore
import dev.steamvault.app.storage.InMemorySettingsViewPreferences
import dev.steamvault.app.ui.settings.logic.SettingDraft
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.JsonElement
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

private class RiskyNoIdentity : SteamIdentityRepository {
    override fun state(): SteamIdentityState = SteamIdentityState(null, null)
    override fun buildLoginUrl(vaultBaseUrl: String): String? = null
    override suspend fun completeLogin(rawCallbackUrl: String): SteamLoginResult = SteamLoginResult.Success("76561198042117903")
    override suspend fun refreshPersonaName(): Boolean = false
    override suspend fun ownedGamesCountPreview(): Result<Int> = Result.success(0)
    override suspend fun ownedGames(): Result<List<OwnedGame>> = Result.success(emptyList())
    override fun signOut() {}
}

private class RiskyStrings : SettingsStrings {
    override fun loadFailedFallback(cause: Throwable) = "load failed"
    override fun savedToast() = "saved"
    override fun saveFailedFallback() = "save failed"
    override fun libraryIdSaved() = "id saved"
    override fun libraryIdCleared() = "id cleared"
    override fun libraryIdUnchanged() = "id unchanged"
    override fun libraryIdReset() = "id reset"
}

/** Demo settings where `vault_name` counts as risky; records which PATCH variant was used. */
private class RiskyRepo(private val demo: DemoSettingsRepository) : SettingsRepository {
    var plainCalls = 0
    var confirmedCalls = 0
    var answer428Once = false
    var markRisky = true

    override suspend fun get(): SettingsOut {
        val out = demo.get()
        return out.copy(settings = out.settings.map { if (markRisky && it.key == "vault_name") it.copy(risky = true) else it })
    }

    override suspend fun patch(updates: Map<String, JsonElement?>): SettingsOut {
        plainCalls += 1
        if (answer428Once) {
            answer428Once = false
            throw VaultApiError.Unknown(
                "confirm",
                status = 428,
                detail = "{\"message\":\"confirm\",\"risky_keys\":[\"vault_name\"]}",
            )
        }
        return demo.patch(updates)
    }

    override suspend fun patchConfirmed(updates: Map<String, JsonElement?>): SettingsOut {
        confirmedCalls += 1
        return demo.patch(updates)
    }
}

/** D16 Android: the risky-confirm flow, remembered section state and the expert switch. */
class SettingsControllerRiskyTest {

    private fun controller(repo: SettingsRepository, demo: DemoState, prefs: InMemorySettingsViewPreferences) =
        SettingsController(
            repo,
            DemoScheduleRepository(demo),
            InMemoryCredentialStore(),
            RiskyNoIdentity(),
            RiskyStrings(),
            DemoSteamRelayRepository(demo),
            DemoAboutRepository(demo),
            prefs,
        )

    @Test
    fun `MUTATION PIN -- a risky change asks first, only a yes sends confirm_risky`() = runTest {
        val demo = DemoState.fresh()
        val repo = RiskyRepo(DemoSettingsRepository(demo))
        val c = controller(repo, demo, InMemorySettingsViewPreferences())
        c.load()
        c.setDraft("vault_name", SettingDraft.Text("risky-name"))
        c.save()
        assertNotNull("the dialog is open", c.riskyPending)
        assertEquals("nothing was sent yet", 0, repo.plainCalls + repo.confirmedCalls)
        c.confirmRisky()
        assertNull(c.riskyPending)
        assertEquals(1, repo.confirmedCalls)
        assertEquals(0, repo.plainCalls)
        assertFalse(c.isDirty)
    }

    @Test
    fun `cancelling the dialog sends nothing and keeps the draft`() = runTest {
        val demo = DemoState.fresh()
        val repo = RiskyRepo(DemoSettingsRepository(demo))
        val c = controller(repo, demo, InMemorySettingsViewPreferences())
        c.load()
        c.setDraft("vault_name", SettingDraft.Text("risky-name"))
        c.save()
        c.cancelRisky()
        assertNull(c.riskyPending)
        assertTrue(c.riskyCancelled)
        assertTrue(c.isDirty)
        assertEquals(0, repo.plainCalls + repo.confirmedCalls)
        c.setDraft("vault_name", SettingDraft.Text("other"))
        assertFalse("an edit clears the cancelled note", c.riskyCancelled)
    }

    @Test
    fun `a 428 for a key the client did not know opens the dialog instead of an error`() = runTest {
        val demo = DemoState.fresh()
        val repo = RiskyRepo(DemoSettingsRepository(demo))
        repo.markRisky = false
        repo.answer428Once = true
        val c = controller(repo, demo, InMemorySettingsViewPreferences())
        c.load()
        c.setDraft("vault_name", SettingDraft.Text("risky-name"))
        c.save()
        assertEquals(1, repo.plainCalls)
        assertNotNull("the server named a risky key: ask", c.riskyPending)
        assertNull(c.saveError)
        c.confirmRisky()
        assertEquals(1, repo.confirmedCalls)
        assertNull(c.saveError)
    }

    /** WP ANDROID-REDESIGN-1c: one panel at a time, chosen per visit (web
     * `activeSection`); only the expert switch is remembered. */
    @Test
    fun `the selected panel is per visit, the expert switch is remembered`() {
        val demo = DemoState.fresh()
        val prefs = InMemorySettingsViewPreferences()
        val first = controller(RiskyRepo(DemoSettingsRepository(demo)), demo, prefs)
        assertEquals("Vault & connection first", "vault", first.section)
        assertFalse(first.expertOn)
        first.selectSection("expert")
        assertEquals("no Expert panel while the switch is off", "vault", first.section)
        first.selectSection("no-such-section")
        assertEquals("vault", first.section)
        first.selectSection("disk")
        assertEquals("disk", first.section)
        first.setExpert(true)
        first.selectSection("expert")
        assertEquals("expert", first.section)
        first.setExpert(false)
        assertEquals("switching it off leaves the Expert panel", "vault", first.section)
        first.setExpert(true)
        first.selectSection("about")
        val second = controller(RiskyRepo(DemoSettingsRepository(demo)), demo, prefs)
        assertEquals("a new visit starts on the first panel", "vault", second.section)
        assertTrue(second.expertOn)
    }
}
