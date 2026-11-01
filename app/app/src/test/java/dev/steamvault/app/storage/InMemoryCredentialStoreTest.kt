package dev.steamvault.app.storage

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Contract coverage for [CredentialStore], run against the JVM fake (WP 4b.2 brief). */
class InMemoryCredentialStoreTest {

    @Test
    fun `all values start unset, including the WP 4b3 Steam identity fields`() {
        val store = InMemoryCredentialStore()
        assertNull(store.getApiKey())
        assertNull(store.getBaseUrl())
        assertNull(store.getProfileKind())
        assertNull(store.getSteamId64())
        assertNull(store.getSteamPersonaName())
    }

    @Test
    fun `set then get round-trips each value independently`() {
        val store = InMemoryCredentialStore()
        store.setApiKey("test-key-123")
        store.setBaseUrl("http://10.0.0.50:8080")
        store.setProfileKind(ProfileKind.SYSTEM_VPN)
        store.setSteamId64("76561198042117903")
        store.setSteamPersonaName("Example Persona")

        assertEquals("test-key-123", store.getApiKey())
        assertEquals("http://10.0.0.50:8080", store.getBaseUrl())
        assertEquals(ProfileKind.SYSTEM_VPN, store.getProfileKind())
        assertEquals("76561198042117903", store.getSteamId64())
        assertEquals("Example Persona", store.getSteamPersonaName())
    }

    @Test
    fun `setting a value to null clears it, not just overwrites with an empty string`() {
        val store = InMemoryCredentialStore()
        store.setApiKey("test-key-123")

        store.setApiKey(null)

        assertNull(store.getApiKey())
    }

    @Test
    fun `clear removes every value, including Steam identity`() {
        val store = InMemoryCredentialStore()
        store.setApiKey("k")
        store.setBaseUrl("http://host")
        store.setProfileKind(ProfileKind.PUBLIC_DOMAIN)
        store.setSteamId64("76561198042117903")
        store.setSteamPersonaName("Example Persona")

        store.clear()

        assertNull(store.getApiKey())
        assertNull(store.getBaseUrl())
        assertNull(store.getProfileKind())
        assertNull(store.getSteamId64())
        assertNull(store.getSteamPersonaName())
    }

    @Test
    fun `clearSteamIdentity clears only the Steam fields, leaving the vault connection untouched`() {
        val store = InMemoryCredentialStore()
        store.setApiKey("k")
        store.setBaseUrl("http://host")
        store.setProfileKind(ProfileKind.SYSTEM_VPN)
        store.setSteamId64("76561198042117903")
        store.setSteamPersonaName("Example Persona")

        store.clearSteamIdentity()

        assertNull(store.getSteamId64())
        assertNull(store.getSteamPersonaName())
        assertEquals("k", store.getApiKey())
        assertEquals("http://host", store.getBaseUrl())
        assertEquals(ProfileKind.SYSTEM_VPN, store.getProfileKind())
    }

    // ---- WP 4h.4 review fix: the retired device-local Steam Web API key
    // must be ACTIVELY scrubbed, not merely left unread -- see
    // CredentialStore.kt's "Migration note" kdoc for the ADR-0010-derived
    // reasoning. -----------------------------------------------------------

    @Test
    fun `sanity -- a key not present at all is not reported present`() {
        val store = InMemoryCredentialStore()
        assertFalse(store.containsRawKeyForTest(LEGACY_STEAM_WEB_API_KEY_PREF_NAME))
    }

    @Test
    fun `MUTATION PIN -- construction scrubs an existing install's legacy Steam Web API key`() {
        // Simulates upgrading: an existing install's backing store already
        // has a value under the retired pref name BEFORE this object is
        // constructed -- mirrors what EncryptedCredentialStore's real
        // EncryptedSharedPreferences file could contain on a real device.
        val store = InMemoryCredentialStore(
            seed = mapOf(LEGACY_STEAM_WEB_API_KEY_PREF_NAME to "0123456789ABCDEF0123456789ABCDEF"),
        )

        assertFalse(
            "construction must scrub the legacy key -- it must not merely sit unread",
            store.containsRawKeyForTest(LEGACY_STEAM_WEB_API_KEY_PREF_NAME),
        )
    }

    @Test
    fun `a seeded legacy key does not disturb unrelated seeded values`() {
        val store = InMemoryCredentialStore(
            seed = mapOf(
                LEGACY_STEAM_WEB_API_KEY_PREF_NAME to "0123456789ABCDEF0123456789ABCDEF",
                "apiKey" to "vault-key-untouched",
            ),
        )

        assertFalse(store.containsRawKeyForTest(LEGACY_STEAM_WEB_API_KEY_PREF_NAME))
        assertEquals("vault-key-untouched", store.getApiKey())
    }

    @Test
    fun `legacyPrefKeysToScrub -- pure function sanity in both directions`() {
        assertEquals(
            setOf(LEGACY_STEAM_WEB_API_KEY_PREF_NAME),
            legacyPrefKeysToScrub(setOf(LEGACY_STEAM_WEB_API_KEY_PREF_NAME, "apiKey")),
        )
        assertTrue(legacyPrefKeysToScrub(setOf("apiKey", "steamId64")).isEmpty())
    }
}
