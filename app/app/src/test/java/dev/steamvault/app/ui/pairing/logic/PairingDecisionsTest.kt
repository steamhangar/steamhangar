package dev.steamvault.app.ui.pairing.logic

import dev.steamvault.app.net.pairing.PairingLink
import dev.steamvault.app.net.pairing.PairingParseResult
import dev.steamvault.app.net.pairing.PairingRequest
import dev.steamvault.app.ui.onboarding.ConnectivityProfileChoice
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PairingDecisionsTest {

    private fun request(baseUrlInLink: String): PairingRequest {
        val encoded = java.net.URLEncoder.encode(baseUrlInLink, "UTF-8")
        val result = PairingLink.parse("steamhangar://pair?v=1&url=$encoded&key=abc")
        return (result as PairingParseResult.Valid).request
    }

    // ---- replace notice -------------------------------------------------------

    @Test
    fun `nothing stored means nothing is replaced`() {
        val new = request("https://hangar.example.org")
        assertEquals(PairingReplaceNotice.NONE, replaceNoticeFor(null, hasExistingKey = false, request = new))
        assertEquals(PairingReplaceNotice.NONE, replaceNoticeFor("", hasExistingKey = true, request = new))
    }

    @Test
    fun `a stored URL without a key is not a working connection, so nothing is replaced`() {
        assertEquals(
            PairingReplaceNotice.NONE,
            replaceNoticeFor("http://192.168.1.50:8080", hasExistingKey = false, request = request("https://hangar.example.org")),
        )
    }

    @Test
    fun `MUTATION PIN -- an already configured different vault gets the replace notice`() {
        assertEquals(
            PairingReplaceNotice.REPLACES_OTHER_VAULT,
            replaceNoticeFor("http://192.168.1.50:8080", hasExistingKey = true, request = request("https://hangar.example.org")),
        )
    }

    @Test
    fun `the same vault spelled differently is recognized as the same vault`() {
        val new = request("https://hangar.example.org")
        for (stored in listOf("https://hangar.example.org", "https://Hangar.example.org/", " https://hangar.example.org:443 ")) {
            assertEquals(stored, PairingReplaceNotice.SAME_VAULT, replaceNoticeFor(stored, hasExistingKey = true, request = new))
        }
    }

    @Test
    fun `a different port or scheme is a different vault`() {
        val new = request("https://hangar.example.org")
        assertEquals(
            PairingReplaceNotice.REPLACES_OTHER_VAULT,
            replaceNoticeFor("http://hangar.example.org", hasExistingKey = true, request = new),
        )
        assertEquals(
            PairingReplaceNotice.REPLACES_OTHER_VAULT,
            replaceNoticeFor("https://hangar.example.org:8443", hasExistingKey = true, request = new),
        )
    }

    @Test
    fun `an unparseable stored URL is treated as a different vault`() {
        assertEquals(
            PairingReplaceNotice.REPLACES_OTHER_VAULT,
            replaceNoticeFor("not a url", hasExistingKey = true, request = request("https://hangar.example.org")),
        )
    }

    @Test
    fun `the existing host for the notice is host and non-default port, or the raw text`() {
        assertEquals("192.168.1.50:8080", existingDisplayHost("http://192.168.1.50:8080"))
        assertEquals("hangar.example.org", existingDisplayHost("https://hangar.example.org/"))
        assertEquals("not a url", existingDisplayHost(" not a url "))
        assertNull(existingDisplayHost(null))
        assertNull(existingDisplayHost("  "))
    }

    // ---- profile choice --------------------------------------------------------

    @Test
    fun `https pairs with the TLS-only profile, http with the LAN profile`() {
        assertEquals(ConnectivityProfileChoice.PUBLIC_DOMAIN, pairingProfileChoice(request("https://hangar.example.org")))
        assertEquals(ConnectivityProfileChoice.SYSTEM_VPN, pairingProfileChoice(request("http://192.168.1.50:8080")))
    }

    // ---- continuation -------------------------------------------------------------

    @Test
    fun `MUTATION PIN -- unfinished onboarding continues to its next step, finished onboarding goes home`() {
        assertEquals(PairingContinuation.CONTINUE_ONBOARDING, pairingContinuation(showOnboarding = true, hasRealConnection = false))
        assertEquals(PairingContinuation.STORE_AND_GO_HOME, pairingContinuation(showOnboarding = false, hasRealConnection = true))
    }

    @Test
    fun `an open reconnect flow is continued, not bypassed`() {
        assertEquals(PairingContinuation.CONTINUE_ONBOARDING, pairingContinuation(showOnboarding = true, hasRealConnection = true))
    }

    @Test
    fun `demo mode, with no real connection, opens onboarding`() {
        assertEquals(PairingContinuation.OPEN_ONBOARDING, pairingContinuation(showOnboarding = false, hasRealConnection = false))
    }

    // ---- intent re-delivery ---------------------------------------------------------

    @Test
    fun `MUTATION PIN -- a re-delivered launch intent never offers pairing again`() {
        assertTrue(shouldOfferPairing(restoredInstance = false, launchedFromHistory = false))
        assertFalse(shouldOfferPairing(restoredInstance = true, launchedFromHistory = false))
        assertFalse(shouldOfferPairing(restoredInstance = false, launchedFromHistory = true))
        assertFalse(shouldOfferPairing(restoredInstance = true, launchedFromHistory = true))
    }
}
