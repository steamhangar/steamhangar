package dev.steamvault.app.ui.onboarding

import dev.steamvault.app.net.connection.ConnectionFailureReason
import dev.steamvault.app.net.model.OwnedGame
import dev.steamvault.app.repo.SteamIdentityRepository
import dev.steamvault.app.repo.SteamIdentityState
import dev.steamvault.app.repo.SteamLoginResult
import dev.steamvault.app.storage.InMemoryCredentialStore
import dev.steamvault.app.ui.settings.LibraryIdMessage
import dev.steamvault.app.ui.settings.logic.LibraryLookupError
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.runTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.util.concurrent.TimeUnit

/**
 * WP ANDROID-GAPS-1: onboarding step 2 stores the SteamID64 as the vault's
 * library id (web onboarding WEB-FEAT-2): Look up asks the relay first, and
 * only an answered lookup is saved, through the same plan as Settings' Save.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class OnboardingLibraryIdTest {

    private class Strings : OnboardingStrings {
        override fun invalidUrl() = "invalid-url"
        override fun enterApiKeyFirst() = "enter-key-first"
        override fun connectionOk() = "ok"
        override fun connectionFailure(reason: ConnectionFailureReason) = "failure"
    }

    private class Identity(var current: SteamIdentityState = SteamIdentityState(null, null)) : SteamIdentityRepository {
        override fun state() = current
        override fun buildLoginUrl(vaultBaseUrl: String): String? = null
        override suspend fun completeLogin(rawCallbackUrl: String): SteamLoginResult {
            current = SteamIdentityState(SIGNED_IN, null)
            return SteamLoginResult.Success(SIGNED_IN)
        }
        override suspend fun refreshPersonaName() = false
        override suspend fun ownedGamesCountPreview(): Result<Int> = Result.success(0)
        override suspend fun ownedGames(): Result<List<OwnedGame>> = Result.success(emptyList())
        override fun signOut() {
            current = SteamIdentityState(null, null)
        }
    }

    private lateinit var server: MockWebServer

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun settingsJson(stored: String, readonly: Boolean = false) =
        """{"readonly":$readonly,"settings":[{"key":"steam_library_steamid","effective":"$stored","source":"default","fallback":"","applies":"immediately"}]}"""

    /** A controller whose step 1 passed against [server]. */
    private suspend fun tested(stored: String = "", identity: Identity = Identity()): OnboardingController {
        val c = OnboardingController(InMemoryCredentialStore(), identity, Strings())
        c.start(OnboardingMode.FIRST_RUN)
        c.baseUrlText = server.url("/").toString().trimEnd('/')
        c.apiKeyText = "k"
        server.enqueue(MockResponse().setBody("""{"status":"ok"}"""))
        server.enqueue(MockResponse().setBody(settingsJson(stored)))
        c.testConnection()
        assertTrue(c.tested)
        server.takeRequest()
        server.takeRequest()
        return c
    }

    @Test
    fun `a verified test pre-fills the field from the stored library id, never over typed text`() = runTest {
        assertEquals(STORED, tested(stored = STORED).libraryIdInput)
        val c = OnboardingController(InMemoryCredentialStore(), Identity(), Strings())
        c.start(OnboardingMode.FIRST_RUN)
        c.libraryIdInput = "typed"
        c.baseUrlText = server.url("/").toString().trimEnd('/')
        c.apiKeyText = "k"
        server.enqueue(MockResponse().setBody("""{"status":"ok"}"""))
        server.enqueue(MockResponse().setBody(settingsJson(STORED)))
        c.testConnection()
        assertEquals("typed", c.libraryIdInput)
    }

    @Test
    fun `without a stored id, a Steam sign-in fills the empty field (filling saves nothing)`() = runTest {
        val c = tested()
        assertEquals("", c.libraryIdInput)
        c.completeSteamLogin("callback")
        assertEquals(SIGNED_IN, c.libraryIdInput)
        assertEquals(0, server.requestCount - 2)
    }

    @Test
    fun `MUTATION PIN -- Look up asks the relay, then PATCHes the id as a JSON string`() = runTest {
        val c = tested()
        c.libraryIdInput = " $STORED "
        server.enqueue(MockResponse().setBody("""{"configured":true,"game_count":2,"games":[{"appid":10,"name":"Alpha"},{"appid":20,"name":"Beta"}]}"""))
        server.enqueue(MockResponse().setBody("""{"configured":true,"players":[{"steamid":"$STORED","personaname":"Pilot"}]}"""))
        server.enqueue(MockResponse().setBody(settingsJson("")))
        server.enqueue(MockResponse().setBody(settingsJson(STORED)))
        c.lookupLibrarySteamId()

        assertEquals("/v1/steam/owned-games?steamid=$STORED", server.takeRequest().path)
        assertEquals("/v1/steam/player-summaries?steamid=$STORED", server.takeRequest().path)
        assertEquals("/v1/settings", server.takeRequest().path)
        val patch = server.takeRequest()
        assertEquals("PATCH", patch.method)
        assertEquals("""{"steam_library_steamid":"$STORED"}""", patch.body.readUtf8())
        assertEquals(LibrarySaveNote.Saved(STORED), c.librarySave)
        assertEquals(2, c.libraryLookup!!.gameCount)
        assertEquals("Pilot", c.libraryLookup!!.personaName)
        assertTrue(!c.libraryLookupBusy)
    }

    @Test
    fun `MUTATION PIN -- a lookup the relay refused saves nothing`() = runTest {
        val c = tested()
        c.libraryIdInput = STORED
        server.enqueue(MockResponse().setResponseCode(409).setBody("""{"detail":"No Steam Web API key configured"}"""))
        c.lookupLibrarySteamId()
        assertEquals(LibraryLookupError.NoRelayKey, c.libraryLookupError)
        assertNull(c.librarySave)
        assertEquals(3, server.requestCount) // health, settings, the one relay call
    }

    @Test
    fun `an invalid id is refused locally`() = runTest {
        val c = tested()
        c.libraryIdInput = "1234"
        c.lookupLibrarySteamId()
        assertEquals(LibraryLookupError.InvalidSteamId, c.libraryLookupError)
        assertEquals(2, server.requestCount)
    }

    @Test
    fun `a read-only vault keeps the lookup and says the id was not saved`() = runTest {
        val c = tested()
        c.libraryIdInput = STORED
        server.enqueue(MockResponse().setBody("""{"configured":true,"game_count":0,"games":[]}"""))
        server.enqueue(MockResponse().setResponseCode(502).setBody("""{"detail":"upstream"}"""))
        server.enqueue(MockResponse().setBody(settingsJson("", readonly = true)))
        c.lookupLibrarySteamId()
        assertEquals(0, c.libraryLookup!!.gameCount)
        assertNull(c.libraryLookup!!.personaName)
        assertEquals(LibrarySaveNote.NotSaved(LibraryIdMessage.READONLY), c.librarySave)
        assertEquals(5, server.requestCount) // no PATCH
    }

    @Test
    fun `an unchanged id is reported as saved without a PATCH`() = runTest {
        val c = tested(stored = STORED)
        server.enqueue(MockResponse().setBody("""{"configured":true,"game_count":1,"games":[{"appid":10,"name":"Alpha"}]}"""))
        server.enqueue(MockResponse().setBody("""{"configured":true,"players":[]}"""))
        server.enqueue(MockResponse().setBody(settingsJson(STORED)))
        c.lookupLibrarySteamId()
        assertEquals(LibrarySaveNote.Saved(STORED), c.librarySave)
        assertEquals(5, server.requestCount)
    }

    @Test
    fun `MUTATION PIN -- Finish waits for a running Look up and its save before persisting`() = runTest(UnconfinedTestDispatcher()) {
        val store = InMemoryCredentialStore()
        val c = OnboardingController(store, Identity(), Strings())
        c.start(OnboardingMode.FIRST_RUN)
        c.baseUrlText = server.url("/").toString().trimEnd('/')
        c.apiKeyText = "k"
        server.enqueue(MockResponse().setBody("""{"status":"ok"}"""))
        server.enqueue(MockResponse().setBody(settingsJson("")))
        c.testConnection()
        c.libraryIdInput = STORED
        // A slow relay: the lookup is still running when Finish is tapped.
        server.enqueue(
            MockResponse().setBody("""{"configured":true,"game_count":1,"games":[{"appid":10,"name":"Alpha"}]}""")
                .setBodyDelay(400, TimeUnit.MILLISECONDS),
        )
        server.enqueue(MockResponse().setBody("""{"configured":true,"players":[]}"""))
        server.enqueue(MockResponse().setBody(settingsJson("")))
        server.enqueue(MockResponse().setBody(settingsJson(STORED)))
        c.startLibraryLookup(this)
        assertTrue(c.libraryLookupBusy)

        c.finishAfterPendingLookup()

        assertEquals("finish must not return before the save", LibrarySaveNote.Saved(STORED), c.librarySave)
        assertEquals("k", store.getApiKey())
        assertEquals(6, server.requestCount) // health, settings, relay x2, settings, PATCH
        assertTrue(!c.finishing)
    }

    @Test
    fun `start does not clear the busy flag of a running lookup`() = runTest(UnconfinedTestDispatcher()) {
        val c = tested()
        c.libraryIdInput = STORED
        server.enqueue(
            MockResponse().setBody("""{"configured":true,"game_count":0,"games":[]}""").setBodyDelay(300, TimeUnit.MILLISECONDS),
        )
        server.enqueue(MockResponse().setBody("""{"configured":true,"players":[]}"""))
        server.enqueue(MockResponse().setBody(settingsJson(STORED)))
        c.startLibraryLookup(this)
        assertTrue(c.libraryLookupBusy)
        c.start(OnboardingMode.RECONNECT)
        assertTrue(c.libraryLookupBusy)
        c.finishAfterPendingLookup()
        assertTrue(!c.libraryLookupBusy)
    }

    private companion object {
        const val STORED = "76561198042117903"
        const val SIGNED_IN = "76561198000000001"
    }
}
