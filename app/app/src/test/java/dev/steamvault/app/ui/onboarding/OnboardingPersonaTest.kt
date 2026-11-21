package dev.steamvault.app.ui.onboarding

import dev.steamvault.app.net.connection.ConnectionFailureReason
import dev.steamvault.app.repo.PERSONA_TEST_NAME
import dev.steamvault.app.repo.PERSONA_TEST_STEAM_ID
import dev.steamvault.app.repo.PERSONA_TEST_VAULT
import dev.steamvault.app.repo.PersonaFetcher
import dev.steamvault.app.repo.personaTestCallbackUrl
import dev.steamvault.app.repo.personaTestRepository
import dev.steamvault.app.storage.InMemoryCredentialStore
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

private class OnboardingPersonaStrings : OnboardingStrings {
    override fun invalidUrl() = "invalid-url"
    override fun enterApiKeyFirst() = "enter-key-first"
    override fun connectionOk() = "ok"
    override fun connectionFailure(reason: ConnectionFailureReason) = "failure"
}

/**
 * PERSONA-LOAD-1 (audit 2026-11-20): onboarding Step 2 showed "Persona
 * name not loaded yet" for good. Drives the REAL repository; only the
 * OpenID verifier and the relay fetcher are fakes.
 */
class OnboardingPersonaTest {

    private fun controller(store: InMemoryCredentialStore, fetcher: PersonaFetcher): OnboardingController =
        OnboardingController(store, personaTestRepository(store, fetcher), OnboardingPersonaStrings()).apply {
            start(OnboardingMode.RECONNECT)
            baseUrlText = PERSONA_TEST_VAULT
        }

    @Test
    fun `MUTATION PIN -- a successful Steam login loads the persona name into the step 2 state`() = runTest {
        val store = InMemoryCredentialStore()
        val fetcher = PersonaFetcher()
        val c = controller(store, fetcher)

        c.buildSteamLoginUrl()
        c.completeSteamLogin(personaTestCallbackUrl())

        assertEquals(PERSONA_TEST_STEAM_ID, c.identityState.steamId64)
        assertEquals(PERSONA_TEST_NAME, c.identityState.personaName)
        assertEquals(PERSONA_TEST_NAME, c.identitySummary().personaName)
        assertNull(c.loginError)
    }

    @Test
    fun `without a vault connection (first run) the persona fetch fails silently and the login still counts`() = runTest {
        val store = InMemoryCredentialStore()
        // What VaultRelayLibraryFetcher throws while no connection is stored.
        val fetcher = PersonaFetcher(personaFailure = IllegalStateException("no vault-api connection configured"))
        val c = controller(store, fetcher)

        c.buildSteamLoginUrl()
        c.completeSteamLogin(personaTestCallbackUrl())

        assertEquals(PERSONA_TEST_STEAM_ID, c.identityState.steamId64)
        assertNull(c.identityState.personaName)
        assertNull(c.loginError)
        assertEquals(1, fetcher.personaCalls)
    }
}
