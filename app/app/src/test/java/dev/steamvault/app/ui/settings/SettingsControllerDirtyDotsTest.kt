package dev.steamvault.app.ui.settings

import dev.steamvault.app.demo.DemoAboutRepository
import dev.steamvault.app.demo.DemoScheduleRepository
import dev.steamvault.app.demo.DemoState
import dev.steamvault.app.demo.DemoSteamRelayRepository
import dev.steamvault.app.net.model.OwnedGame
import dev.steamvault.app.net.model.SettingInfoOut
import dev.steamvault.app.net.model.SettingsOut
import dev.steamvault.app.repo.SettingsRepository
import dev.steamvault.app.repo.SteamIdentityRepository
import dev.steamvault.app.repo.SteamIdentityState
import dev.steamvault.app.repo.SteamLoginResult
import dev.steamvault.app.storage.InMemoryCredentialStore
import dev.steamvault.app.ui.settings.logic.SettingDraft
import dev.steamvault.app.ui.settings.logic.SettingsDirtyDots
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

private class DotsNoIdentity : SteamIdentityRepository {
    override fun state(): SteamIdentityState = SteamIdentityState(null, null)
    override fun buildLoginUrl(vaultBaseUrl: String): String? = null
    override suspend fun completeLogin(rawCallbackUrl: String): SteamLoginResult = SteamLoginResult.Failure("n/a")
    override suspend fun refreshPersonaName(): Boolean = false
    override suspend fun ownedGamesCountPreview(): Result<Int> = Result.success(0)
    override suspend fun ownedGames(): Result<List<OwnedGame>> = Result.success(emptyList())
    override fun signOut() {}
}

private class DotsStrings : SettingsStrings {
    override fun loadFailedFallback(cause: Throwable) = "load failed"
    override fun savedToast() = "saved"
    override fun saveFailedFallback() = "save failed"
    override fun libraryIdSaved() = "id saved"
    override fun libraryIdCleared() = "id cleared"
    override fun libraryIdUnchanged() = "id unchanged"
    override fun libraryIdReset() = "id reset"
}

/** A fixed GET answer with one normal and one expert key; PATCH echoes it. */
private class FixedSettings(private val readonly: Boolean) : SettingsRepository {
    private fun e(key: String, value: String, expert: Boolean = false) = SettingInfoOut(
        key = key, effective = JsonPrimitive(value), source = "default", applies = "immediately", expert = expert,
    )
    override suspend fun get(): SettingsOut = SettingsOut(
        readonly = readonly,
        settings = listOf(e("vault_name", "vault-01"), e("gc_grace_days", "7", expert = true)),
    )
    override suspend fun patch(updates: Map<String, JsonElement?>): SettingsOut = get()
}

/** WP ANDROID-POLISH-1: the controller feeds the dots from its own drafts. */
class SettingsControllerDirtyDotsTest {
    private fun controller(repo: SettingsRepository): SettingsController {
        val demo = DemoState.fresh()
        return SettingsController(
            repo,
            DemoScheduleRepository(demo),
            InMemoryCredentialStore(),
            DotsNoIdentity(),
            DotsStrings(),
            DemoSteamRelayRepository(demo),
            DemoAboutRepository(demo),
        )
    }

    @Test
    fun `MUTATION PIN -- drafts light their tab and the switch row, Discard clears them`() = runTest {
        val c = controller(FixedSettings(readonly = false))
        c.load()
        assertEquals(SettingsDirtyDots.NONE, c.dirtyDots)
        c.setDraft("vault_name", SettingDraft.Text("vault-02"))
        assertEquals(setOf("vault"), c.dirtyDots.sections)
        assertFalse(c.dirtyDots.expertSwitch)
        c.setDraft("gc_grace_days", SettingDraft.Text("9"))
        assertEquals(setOf("vault", "expert"), c.dirtyDots.sections)
        assertTrue("the switch row shows it while Expert is hidden", c.dirtyDots.expertSwitch)
        c.discard()
        assertEquals(SettingsDirtyDots.NONE, c.dirtyDots)
    }

    @Test
    fun `a read-only vault shows no dots (web pendingBody is empty there)`() = runTest {
        val c = controller(FixedSettings(readonly = true))
        c.load()
        c.setDraft("vault_name", SettingDraft.Text("vault-02"))
        assertEquals(SettingsDirtyDots.NONE, c.dirtyDots)
    }
}
