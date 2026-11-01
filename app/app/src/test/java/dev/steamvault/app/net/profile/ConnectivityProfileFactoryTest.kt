package dev.steamvault.app.net.profile

import dev.steamvault.app.storage.InMemoryCredentialStore
import dev.steamvault.app.storage.ProfileKind
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ConnectivityProfileFactoryTest {

    @Test
    fun `a fresh unconfigured store yields no profile`() {
        assertNull(buildConnectivityProfile(InMemoryCredentialStore()))
    }

    @Test
    fun `base URL alone with no profile kind yields no profile`() {
        val store = InMemoryCredentialStore().apply { setBaseUrl("http://10.0.0.50:8080") }
        assertNull(buildConnectivityProfile(store))
    }

    @Test
    fun `system_vpn kind builds a SystemVpnProfile that allows cleartext`() {
        val store = InMemoryCredentialStore().apply {
            setBaseUrl("http://10.0.0.50:8080")
            setProfileKind(ProfileKind.SYSTEM_VPN)
        }
        val profile = buildConnectivityProfile(store)
        assertTrue(profile is SystemVpnProfile)
        assertEquals("http://10.0.0.50:8080", profile?.baseUrl)
        assertTrue(profile!!.allowsCleartext)
    }

    @Test
    fun `public_domain kind builds a PublicDomainProfile that forbids cleartext`() {
        val store = InMemoryCredentialStore().apply {
            setBaseUrl("https://vault.example.org")
            setProfileKind(ProfileKind.PUBLIC_DOMAIN)
        }
        val profile = buildConnectivityProfile(store)
        assertTrue(profile is PublicDomainProfile)
        assertTrue(!profile!!.allowsCleartext)
    }

    @Test
    fun `a malformed base URL never throws -- returns null instead`() {
        val store = InMemoryCredentialStore().apply {
            setBaseUrl("not a url")
            setProfileKind(ProfileKind.SYSTEM_VPN)
        }
        assertNull(buildConnectivityProfile(store))
    }

    @Test
    fun `an http base URL under public_domain never throws -- returns null instead`() {
        val store = InMemoryCredentialStore().apply {
            setBaseUrl("http://vault.example.org")
            setProfileKind(ProfileKind.PUBLIC_DOMAIN)
        }
        assertNull(buildConnectivityProfile(store))
    }

    @Test
    fun `an unrecognized profile kind string yields no profile`() {
        val store = InMemoryCredentialStore().apply {
            setBaseUrl("http://10.0.0.50:8080")
            setProfileKind("carrier_pigeon")
        }
        assertNull(buildConnectivityProfile(store))
    }
}
