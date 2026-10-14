package dev.steamvault.app.ui.settings

import dev.steamvault.app.demo.DemoAboutRepository
import dev.steamvault.app.demo.DemoScheduleRepository
import dev.steamvault.app.demo.DemoSettingsRepository
import dev.steamvault.app.demo.DemoState
import dev.steamvault.app.demo.DemoSteamRelayRepository
import dev.steamvault.app.net.model.OwnedGame
import dev.steamvault.app.repo.SteamIdentityRepository
import dev.steamvault.app.repo.SteamIdentityState
import dev.steamvault.app.repo.SteamLoginResult
import dev.steamvault.app.storage.InMemoryCredentialStore
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Records the vault address the controller hands over (WP D11). */
private class SteamLoginRecordingIdentity : SteamIdentityRepository {
    var lastVaultBaseUrl: String? = null

    override fun state(): SteamIdentityState = SteamIdentityState(null, null)
    override fun buildLoginUrl(vaultBaseUrl: String): String? {
        lastVaultBaseUrl = vaultBaseUrl
        return if (vaultBaseUrl.isBlank()) null else "https://steamcommunity.com/openid/login?fake=1"
    }
    override suspend fun completeLogin(rawCallbackUrl: String): SteamLoginResult = SteamLoginResult.Failure("unused")
    override suspend fun refreshPersonaName(): Boolean = false
    override suspend fun ownedGamesCountPreview(): Result<Int> = Result.success(0)
    override suspend fun ownedGames(): Result<List<OwnedGame>> = Result.success(emptyList())
    override fun signOut() {}
}

private class SteamLoginTestStrings : SettingsStrings {
    override fun loadFailedFallback(cause: Throwable) = "load failed"
    override fun savedToast() = "saved"
    override fun saveFailedFallback() = "save failed"
    override fun libraryIdSaved() = "id saved"
    override fun libraryIdCleared() = "id cleared"
    override fun libraryIdUnchanged() = "id unchanged"
    override fun libraryIdReset() = "id reset"
}

/**
 * WP D11 (APP-FIX-LOGIN): Steam returns to a page on the vault, so the
 * Settings sign-in needs the STORED vault URL; without one there is no
 * login URL and the user is told why.
 */
class SettingsControllerSteamLoginTest {

    private fun controller(store: InMemoryCredentialStore, identity: SteamLoginRecordingIdentity): SettingsController {
        val demo = DemoState.fresh()
        return SettingsController(
            DemoSettingsRepository(demo),
            DemoScheduleRepository(demo),
            store,
            identity,
            SteamLoginTestStrings(),
            DemoSteamRelayRepository(demo),
            DemoAboutRepository(demo),
        )
    }

    @Test
    fun `MUTATION PIN -- buildSteamLoginUrl hands the stored vault URL to the repository`() {
        val store = InMemoryCredentialStore().apply { setBaseUrl("https://vault.example.lan") }
        val identity = SteamLoginRecordingIdentity()
        val c = controller(store, identity)

        val url = c.buildSteamLoginUrl()

        assertEquals("https://vault.example.lan", identity.lastVaultBaseUrl)
        assertEquals("https://steamcommunity.com/openid/login?fake=1", url)
        assertNull(c.loginError)
    }

    @Test
    fun `buildSteamLoginUrl without a stored vault URL returns null and says why`() {
        val c = controller(InMemoryCredentialStore(), SteamLoginRecordingIdentity())

        val url = c.buildSteamLoginUrl()

        assertNull(url)
        assertEquals(
            "Steam sign-in needs the vault address (http:// or https://) — set up the connection first.",
            c.loginError,
        )
    }
}
