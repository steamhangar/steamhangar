package dev.steamvault.app.ui.settings

import dev.steamvault.app.demo.DemoAboutRepository
import dev.steamvault.app.demo.DemoScheduleRepository
import dev.steamvault.app.demo.DemoSettingsRepository
import dev.steamvault.app.demo.DemoState
import dev.steamvault.app.demo.DemoSteamRelayRepository
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

private class PersonaTestStrings : SettingsStrings {
    override fun loadFailedFallback(cause: Throwable) = "load failed"
    override fun savedToast() = "saved"
    override fun saveFailedFallback() = "save failed"
    override fun libraryIdSaved() = "id saved"
    override fun libraryIdCleared() = "id cleared"
    override fun libraryIdUnchanged() = "id unchanged"
    override fun libraryIdReset() = "id reset"
}

/**
 * PERSONA-LOAD-1 (audit 2026-11-20): Settings showed "Persona name not
 * loaded yet" for good because nothing called `refreshPersonaName()`.
 * These tests drive the REAL repository (only the OpenID verifier and the
 * relay fetcher are fakes) through the controller, so [SettingsController.identityState]
 * -- what the Steam identity section renders -- is what gets asserted.
 */
class SettingsControllerPersonaTest {

    private fun controller(store: InMemoryCredentialStore, fetcher: PersonaFetcher): SettingsController {
        val demo = DemoState.fresh()
        return SettingsController(
            DemoSettingsRepository(demo),
            DemoScheduleRepository(demo),
            store,
            personaTestRepository(store, fetcher),
            PersonaTestStrings(),
            DemoSteamRelayRepository(demo),
            DemoAboutRepository(demo),
        )
    }

    @Test
    fun `MUTATION PIN -- a successful Steam login loads the persona name into the UI state`() = runTest {
        val store = InMemoryCredentialStore().apply { setBaseUrl(PERSONA_TEST_VAULT) }
        val fetcher = PersonaFetcher()
        val c = controller(store, fetcher)

        c.buildSteamLoginUrl()
        c.completeSteamLogin(personaTestCallbackUrl())

        assertEquals(PERSONA_TEST_STEAM_ID, c.identityState.steamId64)
        assertEquals(PERSONA_TEST_NAME, c.identityState.personaName)
        assertNull(c.loginError)
    }

    @Test
    fun `a failed persona fetch after login stays silent -- signed in, no error, name not loaded`() = runTest {
        val store = InMemoryCredentialStore().apply { setBaseUrl(PERSONA_TEST_VAULT) }
        val fetcher = PersonaFetcher(personaFailure = IllegalStateException("no vault-api connection configured"))
        val c = controller(store, fetcher)

        c.buildSteamLoginUrl()
        c.completeSteamLogin(personaTestCallbackUrl())

        assertEquals(PERSONA_TEST_STEAM_ID, c.identityState.steamId64)
        assertNull(c.identityState.personaName)
        assertNull(c.loginError)
        assertEquals(1, fetcher.personaCalls)
    }

    @Test
    fun `MUTATION PIN -- load() fills in the name for a stored SteamID64 without one`() = runTest {
        val store = InMemoryCredentialStore().apply { setSteamId64(PERSONA_TEST_STEAM_ID) }
        val fetcher = PersonaFetcher()
        val c = controller(store, fetcher)
        assertNull(c.identityState.personaName)

        c.load()

        assertEquals(PERSONA_TEST_NAME, c.identityState.personaName)
        assertEquals(1, fetcher.personaCalls)
    }

    @Test
    fun `load() does not fetch when the name is already known or nobody is signed in`() = runTest {
        val known = InMemoryCredentialStore().apply {
            setSteamId64(PERSONA_TEST_STEAM_ID)
            setSteamPersonaName("Stored Name")
        }
        val knownFetcher = PersonaFetcher()
        val withName = controller(known, knownFetcher)
        withName.load()
        assertEquals("Stored Name", withName.identityState.personaName)
        assertEquals(0, knownFetcher.personaCalls)

        val signedOutFetcher = PersonaFetcher()
        val signedOut = controller(InMemoryCredentialStore(), signedOutFetcher)
        signedOut.load()
        assertNull(signedOut.identityState.steamId64)
        assertEquals(0, signedOutFetcher.personaCalls)
    }

    @Test
    fun `load() with a failing persona fetch keeps the name unloaded and the form loaded`() = runTest {
        val store = InMemoryCredentialStore().apply { setSteamId64(PERSONA_TEST_STEAM_ID) }
        val fetcher = PersonaFetcher(personaFailure = RuntimeException("relay 409"))
        val c = controller(store, fetcher)

        c.load()

        assertNull(c.identityState.personaName)
        assertNull(c.loadError)
        assertEquals(false, c.loading)
    }
}
