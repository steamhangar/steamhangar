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
import dev.steamvault.app.ui.settings.logic.SettingDraft
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.JsonElement
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

private class NoIdentity : SteamIdentityRepository {
    override fun state(): SteamIdentityState = SteamIdentityState(null, null)
    override fun buildLoginUrl(): String = "https://steamcommunity.com/openid/login?fake=1"
    override suspend fun completeLogin(rawCallbackUrl: String): SteamLoginResult = SteamLoginResult.Success("76561198042117903")
    override suspend fun refreshPersonaName(): Boolean = false
    override suspend fun ownedGamesCountPreview(): Result<Int> = Result.success(0)
    override suspend fun ownedGames(): Result<List<OwnedGame>> = Result.success(emptyList())
    override fun signOut() {}
}

private class TestStrings : SettingsStrings {
    override fun loadFailedFallback(cause: Throwable) = "load failed"
    override fun savedToast() = "saved"
    override fun saveFailedFallback() = "save failed"
    override fun libraryIdSaved() = "id saved"
    override fun libraryIdCleared() = "id cleared"
    override fun libraryIdUnchanged() = "id unchanged"
    override fun libraryIdReset() = "id reset"
}

/** Demo settings, with a PATCH that can fail or wait for a gate. */
private class ScriptedSettings(private val demo: DemoSettingsRepository) : SettingsRepository {
    var fail = false
    var gate: CompletableDeferred<Unit>? = null
    override suspend fun get(): SettingsOut = demo.get()
    override suspend fun patch(updates: Map<String, JsonElement?>): SettingsOut {
        gate?.await()
        if (fail) throw VaultApiError.Server("boom", status = 500, detail = "disk full")
        return demo.patch(updates)
    }
}

/**
 * WP WEB-FIX-8 review: the controller half of the Settings save bar
 * (the bar itself is pinned by source scan in AppFeat2WiringTest): dirty
 * follows the PATCH builder, a save error is cleared by an edit and by
 * Discard, and an edit made while a save is in flight survives it.
 */
class SettingsControllerSaveBarTest {

    private fun controller(settings: SettingsRepository, demo: DemoState) = SettingsController(
        settings,
        DemoScheduleRepository(demo),
        InMemoryCredentialStore(),
        NoIdentity(),
        TestStrings(),
        DemoSteamRelayRepository(demo),
        DemoAboutRepository(demo),
    )

    @Test
    fun `MUTATION PIN -- a failed save keeps the drafts, an edit or Discard clears the error`() = runTest {
        val demo = DemoState.fresh()
        val repo = ScriptedSettings(DemoSettingsRepository(demo)).apply { fail = true }
        val c = controller(repo, demo)
        c.load()
        c.setDraft("vault_name", SettingDraft.Text("renamed-vault"))
        assertTrue(c.isDirty)
        c.save()
        assertEquals("disk full", c.saveError)
        assertTrue("still dirty: the bar stays", c.isDirty)
        c.setDraft("vault_name", SettingDraft.Text("renamed-vault-2"))
        assertNull("an edit clears the old error", c.saveError)
        c.save()
        assertNotNull(c.saveError)
        c.discard()
        assertNull("Discard clears it too", c.saveError)
        assertFalse(c.isDirty)
    }

    @Test
    fun `MUTATION PIN -- an edit made while the save is in flight survives it and keeps the bar up`() = runTest {
        val demo = DemoState.fresh()
        val repo = ScriptedSettings(DemoSettingsRepository(demo))
        val c = controller(repo, demo)
        c.load()
        c.setDraft("vault_name", SettingDraft.Text("first-name"))
        val gate = CompletableDeferred<Unit>()
        repo.gate = gate
        val job = launch { c.save() }
        testScheduler.runCurrent()
        assertTrue("Saving… shows while in flight", c.saving)
        c.setDraft("vault_name", SettingDraft.Text("second-name"))
        gate.complete(Unit)
        job.join()
        assertFalse(c.saving)
        assertEquals(SettingDraft.Text("second-name"), c.drafts["vault_name"])
        assertTrue("the later edit is still unsaved", c.isDirty)
    }

    @Test
    fun `a successful save with no later edit leaves nothing dirty`() = runTest {
        val demo = DemoState.fresh()
        val c = controller(ScriptedSettings(DemoSettingsRepository(demo)), demo)
        c.load()
        c.setDraft("vault_name", SettingDraft.Text("renamed-vault"))
        c.save()
        assertNull(c.saveError)
        assertTrue(c.drafts.isEmpty())
        assertFalse(c.isDirty)
    }
}
