package dev.steamvault.app.ui.onboarding

import dev.steamvault.app.net.connection.ConnectionFailureReason
import dev.steamvault.app.net.model.OwnedGame
import dev.steamvault.app.repo.SteamIdentityRepository
import dev.steamvault.app.repo.SteamIdentityState
import dev.steamvault.app.repo.SteamLoginResult
import dev.steamvault.app.storage.InMemoryCredentialStore
import dev.steamvault.app.storage.ProfileKind
import dev.steamvault.app.ui.onboarding.logic.OnboardingStep
import kotlinx.coroutines.test.runTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

private class FakeOnboardingStrings : OnboardingStrings {
    override fun invalidUrl() = "invalid-url"
    override fun enterApiKeyFirst() = "enter-key-first"
    override fun connectionOk() = "ok"
    override fun connectionFailure(reason: ConnectionFailureReason) = "failure:$reason"
}

private class FakeSteamIdentityRepository(
    private var current: SteamIdentityState = SteamIdentityState(null, null),
    var loginResult: SteamLoginResult = SteamLoginResult.Success("76561198042117903"),
) : SteamIdentityRepository {
    var buildLoginUrlCalls = 0
    var completeLoginCalls = 0
    var signOutCalls = 0

    override fun state(): SteamIdentityState = current
    override fun buildLoginUrl(): String {
        buildLoginUrlCalls++
        return "https://steamcommunity.com/openid/login?fake=1"
    }

    override suspend fun completeLogin(rawCallbackUrl: String): SteamLoginResult {
        completeLoginCalls++
        if (loginResult is SteamLoginResult.Success) {
            current = current.copy(steamId64 = (loginResult as SteamLoginResult.Success).steamId64)
        }
        return loginResult
    }

    override suspend fun refreshPersonaName(): Boolean = false
    override suspend fun ownedGamesCountPreview(): Result<Int> = Result.success(0)
    override suspend fun ownedGames(): Result<List<OwnedGame>> = Result.success(emptyList())
    override fun signOut() {
        signOutCalls++
        current = SteamIdentityState(null, null)
    }
}

class OnboardingControllerTest {

    private fun controller(
        store: InMemoryCredentialStore = InMemoryCredentialStore(),
        identity: FakeSteamIdentityRepository = FakeSteamIdentityRepository(),
        strings: OnboardingStrings = FakeOnboardingStrings(),
    ) = Triple(OnboardingController(store, identity, strings), store, identity)

    // ---- start() / navigation delegation -------------------------------------

    @Test
    fun `start resets to step 1 and seeds fields from the credential store`() {
        val store = InMemoryCredentialStore().apply {
            setBaseUrl("http://192.168.1.50:8080")
            setProfileKind(ProfileKind.PUBLIC_DOMAIN)
        }
        val (controller, _, _) = controller(store = store)

        controller.start(OnboardingMode.RECONNECT)

        assertEquals(OnboardingStep.CONNECT, controller.step)
        assertEquals(OnboardingMode.RECONNECT, controller.mode)
        assertEquals("http://192.168.1.50:8080", controller.baseUrlText)
        assertEquals(ConnectivityProfileChoice.PUBLIC_DOMAIN, controller.profileChoice)
        assertEquals("", controller.apiKeyText) // never pre-filled -- a secret is not re-shown
        assertFalse(controller.tested)
    }

    @Test
    fun `first-run mode cannot be cancelled, reconnect mode can`() {
        val (controller, _, _) = controller()
        controller.start(OnboardingMode.FIRST_RUN)
        assertFalse(controller.canCancelWithoutFinishing)
        controller.start(OnboardingMode.RECONNECT)
        assertTrue(controller.canCancelWithoutFinishing)
    }

    @Test
    fun `MUTATION PIN -- next() delegates to the step machine's gate -- refuses to leave step 1 untested`() {
        val (controller, _, _) = controller()
        controller.start(OnboardingMode.FIRST_RUN)
        controller.next()
        assertEquals(OnboardingStep.CONNECT, controller.step) // unchanged: not tested
    }

    // ---- Steam login delegation ------------------------------------------------

    @Test
    fun `buildSteamLoginUrl clears any previous login error and delegates to the repository`() {
        val (controller, _, identity) = controller()
        controller.start(OnboardingMode.FIRST_RUN)

        val url = controller.buildSteamLoginUrl()

        assertEquals(1, identity.buildLoginUrlCalls)
        assertTrue(url.isNotEmpty())
        assertNull(controller.loginError)
    }

    @Test
    fun `completeSteamLogin on success refreshes identityState with no error`() = runTest {
        val (controller, _, identity) = controller(
            identity = FakeSteamIdentityRepository(loginResult = SteamLoginResult.Success("76561198042117903")),
        )
        controller.start(OnboardingMode.FIRST_RUN)

        controller.completeSteamLogin("steamvault://auth/openid-return?whatever=1")

        assertEquals(1, identity.completeLoginCalls)
        assertEquals("76561198042117903", controller.identityState.steamId64)
        assertNull(controller.loginError)
    }

    @Test
    fun `completeSteamLogin on failure surfaces the fixed reason string`() = runTest {
        val (controller, _, _) = controller(
            identity = FakeSteamIdentityRepository(loginResult = SteamLoginResult.Failure("rejected")),
        )
        controller.start(OnboardingMode.FIRST_RUN)

        controller.completeSteamLogin("steamvault://auth/openid-return?whatever=1")

        assertEquals("rejected", controller.loginError)
    }

    // ---- WP 4h.4: no device-local Steam Web API key entry left at all ---------
    // (there used to be a "submitWebApiKeyEntry" mutation-pin block here --
    // ADR-0004's second addendum removed the field, the method, and the UI
    // path entirely; SteamIdentityState no longer carries hasWebApiKey.)

    // ---- finish() persists exactly the connection fields -----------------------

    @Test
    fun `finish persists base URL, profile kind, and API key -- and nothing Steam-related`() {
        val (controller, store, _) = controller()
        controller.start(OnboardingMode.FIRST_RUN)
        controller.baseUrlText = " http://192.168.1.50:8080 "
        controller.apiKeyText = " secret-key "
        controller.profileChoice = ConnectivityProfileChoice.SYSTEM_VPN

        controller.finish()

        assertEquals("http://192.168.1.50:8080", store.getBaseUrl())
        assertEquals("secret-key", store.getApiKey())
        assertEquals(ProfileKind.SYSTEM_VPN, store.getProfileKind())
    }

    // ---- WP APP-PAIR-1: the shared check and the pairing hand-over -------------

    @Test
    fun `verifyConnection refuses an invalid URL or an empty key without touching step 1`() = runTest {
        val (controller, _, _) = controller()
        controller.start(OnboardingMode.FIRST_RUN)
        controller.baseUrlText = "typed-by-user"

        assertEquals("invalid-url", controller.verifyConnection(ConnectivityProfileChoice.SYSTEM_VPN, "not a url", "k"))
        assertEquals(
            "invalid-url",
            controller.verifyConnection(ConnectivityProfileChoice.PUBLIC_DOMAIN, "http://192.168.1.50:8080", "k"),
        )
        assertEquals(
            "enter-key-first",
            controller.verifyConnection(ConnectivityProfileChoice.SYSTEM_VPN, "http://192.168.1.50:8080", "   "),
        )

        assertEquals("typed-by-user", controller.baseUrlText)
        assertFalse(controller.tested)
        assertNull(controller.connectionMessage)
    }

    @Test
    fun `MUTATION PIN -- verifyConnection runs health then the authenticated settings call with the trimmed key`() = runTest {
        val server = MockWebServer()
        server.start()
        try {
            server.enqueue(MockResponse().setResponseCode(200).setBody("""{"status":"ok"}"""))
            server.enqueue(MockResponse().setResponseCode(200).setBody("""{"readonly":false,"settings":[]}"""))
            val (controller, store, _) = controller()
            val baseUrl = server.url("/").toString().trimEnd('/')

            assertNull(controller.verifyConnection(ConnectivityProfileChoice.SYSTEM_VPN, baseUrl, "  paired-key  "))

            assertEquals("/v1/health", server.takeRequest().path)
            val settings = server.takeRequest()
            assertEquals("/v1/settings", settings.path)
            assertEquals("paired-key", settings.getHeader("X-Api-Key"))
            assertNull("verifying must not persist anything", store.getApiKey())
        } finally {
            server.shutdown()
        }
    }

    @Test
    fun `verifyConnection reports a rejected key with the onboarding wording`() = runTest {
        val server = MockWebServer()
        server.start()
        try {
            server.enqueue(MockResponse().setResponseCode(200).setBody("""{"status":"ok"}"""))
            server.enqueue(MockResponse().setResponseCode(401).setBody("""{"detail":"bad key"}"""))
            val (controller, _, _) = controller()
            val baseUrl = server.url("/").toString().trimEnd('/')

            val failure = controller.verifyConnection(ConnectivityProfileChoice.SYSTEM_VPN, baseUrl, "wrong")

            assertEquals("failure:" + ConnectionFailureReason.KeyRejected(401), failure)
        } finally {
            server.shutdown()
        }
    }

    @Test
    fun `MUTATION PIN -- a verified pairing lands on the Steam identity step and persists only through finish`() {
        val (controller, store, _) = controller()
        controller.start(OnboardingMode.FIRST_RUN)

        controller.applyVerifiedConnection(ConnectivityProfileChoice.PUBLIC_DOMAIN, "https://hangar.example.org", "paired-key")

        assertEquals(OnboardingStep.STEAM, controller.step)
        assertTrue(controller.tested)
        assertTrue(controller.connectionOk)
        assertEquals("ok", controller.connectionMessage)
        assertNull("nothing is stored before finish()", store.getBaseUrl())

        controller.finish()

        assertEquals("https://hangar.example.org", store.getBaseUrl())
        assertEquals("paired-key", store.getApiKey())
        assertEquals(ProfileKind.PUBLIC_DOMAIN, store.getProfileKind())
    }

    @Test
    fun `pairing over an existing connection replaces all three stored fields`() {
        val store = InMemoryCredentialStore().apply {
            setBaseUrl("http://192.168.1.50:8080")
            setProfileKind(ProfileKind.SYSTEM_VPN)
            setApiKey("old-key")
        }
        val (controller, _, _) = controller(store = store)

        controller.applyVerifiedConnection(ConnectivityProfileChoice.PUBLIC_DOMAIN, "https://hangar.example.org", "new-key")
        controller.finish()

        assertEquals("https://hangar.example.org", store.getBaseUrl())
        assertEquals("new-key", store.getApiKey())
        assertEquals(ProfileKind.PUBLIC_DOMAIN, store.getProfileKind())
    }
}
