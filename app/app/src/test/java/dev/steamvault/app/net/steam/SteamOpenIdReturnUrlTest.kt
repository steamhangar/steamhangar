package dev.steamvault.app.net.steam

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * WP D11 (APP-FIX-LOGIN): Steam returns to a page on the vault, never to the
 * app's custom scheme. Every expectation is a hand-typed literal
 * (`docs/LEARNINGS.md` 4a.6r rule), including the path the server side pins
 * in `api/tests/test_d11_app_return.py`.
 */
class SteamOpenIdReturnUrlTest {

    @Test
    fun `constants are the literal deep link and vault path`() {
        assertEquals("steamvault://auth/openid-return", SteamOpenIdConfig.APP_LINK)
        assertEquals("/auth/steam/app-return", SteamOpenIdConfig.VAULT_RETURN_PATH)
    }

    @Test
    fun `LAN http vault with a port`() {
        val r = SteamOpenIdReturnUrl.forVault("http://192.168.1.50:8080", "s1")!!
        assertEquals("http://192.168.1.50:8080/auth/steam/app-return?state=s1", r.returnTo)
        assertEquals("http://192.168.1.50:8080/", r.realm)
    }

    @Test
    fun `https vault on the default port`() {
        val r = SteamOpenIdReturnUrl.forVault("https://vault.example.lan", "s1")!!
        assertEquals("https://vault.example.lan/auth/steam/app-return?state=s1", r.returnTo)
        assertEquals("https://vault.example.lan/", r.realm)
    }

    @Test
    fun `MUTATION PIN -- path, query, fragment and userinfo of the base URL are dropped`() {
        val r = SteamOpenIdReturnUrl.forVault(" http://user:pw@10.0.0.2:8080/some/path?x=1#frag ", "s1")!!
        assertEquals("http://10.0.0.2:8080/auth/steam/app-return?state=s1", r.returnTo)
        assertEquals("http://10.0.0.2:8080/", r.realm)
    }

    @Test
    fun `return_to sits under the realm (OpenID 2_0 realm rule)`() {
        val r = SteamOpenIdReturnUrl.forVault("http://[fd00::15]:8080/", "s1")!!
        assertEquals("http://[fd00::15]:8080/auth/steam/app-return?state=s1", r.returnTo)
        assertTrue(r.returnTo.startsWith(r.realm))
    }

    @Test
    fun `no return address without an http(s) vault URL`() {
        assertNull(SteamOpenIdReturnUrl.forVault("", "s1"))
        assertNull(SteamOpenIdReturnUrl.forVault("   ", "s1"))
        assertNull(SteamOpenIdReturnUrl.forVault("steamvault://auth/openid-return", "s1"))
        assertNull(SteamOpenIdReturnUrl.forVault("ftp://10.0.0.2/", "s1"))
        assertNull(SteamOpenIdReturnUrl.forVault("192.168.1.50:8080", "s1"))
    }

    @Test
    fun `isVaultReturnTo accepts only an http(s) URL on the exact vault path`() {
        assertTrue(SteamOpenIdReturnUrl.isVaultReturnTo("http://192.168.1.50:8080/auth/steam/app-return?state=s1"))
        assertTrue(SteamOpenIdReturnUrl.isVaultReturnTo("https://vault.example.lan/auth/steam/app-return?state=s1"))
        assertFalse(SteamOpenIdReturnUrl.isVaultReturnTo("steamvault://auth/openid-return?state=s1"))
        assertFalse(SteamOpenIdReturnUrl.isVaultReturnTo("https://attacker.example/cb?state=s1"))
        assertFalse(SteamOpenIdReturnUrl.isVaultReturnTo("https://vault.example.lan/auth/steam/app-return/x?state=s1"))
        assertFalse(SteamOpenIdReturnUrl.isVaultReturnTo("https://vault.example.lan/v1/auth/steam/app-return?state=s1"))
        assertFalse(SteamOpenIdReturnUrl.isVaultReturnTo(""))
    }
}
