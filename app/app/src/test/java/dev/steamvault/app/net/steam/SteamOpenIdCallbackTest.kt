package dev.steamvault.app.net.steam

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SteamOpenIdCallbackTest {

    private val validQuery = "openid.ns=" + enc("http://specs.openid.net/auth/2.0") +
        "&openid.mode=id_res" +
        "&openid.op_endpoint=" + enc("https://steamcommunity.com/openid/login") +
        "&openid.claimed_id=" + enc("https://steamcommunity.com/openid/id/76561198042117903") +
        "&openid.identity=" + enc("https://steamcommunity.com/openid/id/76561198042117903") +
        "&openid.return_to=" + enc(SteamOpenIdConfig.RETURN_TO) +
        "&openid.response_nonce=" + enc("2026-08-11T12:00:00ZABCDEF") +
        "&openid.assoc_handle=1234567890" +
        "&openid.signed=" + enc("signed,op_endpoint,claimed_id,identity,return_to,response_nonce,assoc_handle") +
        "&openid.sig=" + enc("Zm9vYmFyc2lnbmF0dXJl==")

    private fun enc(s: String) = java.net.URLEncoder.encode(s, "UTF-8")

    // ---- parse ------------------------------------------------------------

    @Test
    fun `parses a well-formed id_res callback into a full openid dot star map`() {
        val params = SteamOpenIdCallback.parse("${SteamOpenIdConfig.RETURN_TO}?$validQuery")
        requireNotNull(params)
        assertEquals("id_res", params["openid.mode"])
        assertEquals("https://steamcommunity.com/openid/id/76561198042117903", params["openid.claimed_id"])
        assertEquals("Zm9vYmFyc2lnbmF0dXJl==", params["openid.sig"])
        assertEquals(SteamOpenIdConfig.RETURN_TO, params["openid.return_to"])
    }

    @Test
    fun `MUTATION PIN -- a non-openid stray query parameter is ignored, not forwarded`() {
        val params = SteamOpenIdCallback.parse(
            "${SteamOpenIdConfig.RETURN_TO}?$validQuery&evil=" + enc("http://attacker.example/steal"),
        )
        requireNotNull(params)
        assertFalse(params.containsKey("evil"))
    }

    @Test
    fun `rejects a callback with no query string at all`() {
        assertNull(SteamOpenIdCallback.parse(SteamOpenIdConfig.RETURN_TO))
        assertNull(SteamOpenIdCallback.parse("${SteamOpenIdConfig.RETURN_TO}?"))
    }

    @Test
    fun `MUTATION PIN (mode) -- a cancel response is rejected, never treated as a login`() {
        val cancelQuery = "openid.mode=cancel" +
            "&openid.claimed_id=x&openid.signed=x&openid.sig=x&openid.return_to=x"
        assertNull(SteamOpenIdCallback.parse("${SteamOpenIdConfig.RETURN_TO}?$cancelQuery"))
    }

    @Test
    fun `rejects a callback missing any one required field`() {
        val required = listOf("openid.mode", "openid.claimed_id", "openid.signed", "openid.sig", "openid.return_to")
        for (missing in required) {
            val parts = validQuery.split('&').filterNot { it.startsWith("$missing=") }
            val truncated = parts.joinToString("&")
            assertNull(
                "expected parse() to reject a callback missing $missing",
                SteamOpenIdCallback.parse("${SteamOpenIdConfig.RETURN_TO}?$truncated"),
            )
        }
    }

    @Test
    fun `a base64 signature's own trailing equals-sign padding stays part of the value`() {
        val params = SteamOpenIdCallback.parse("${SteamOpenIdConfig.RETURN_TO}?$validQuery")
        requireNotNull(params)
        assertEquals("Zm9vYmFyc2lnbmF0dXJl==", params["openid.sig"])
    }

    @Test
    fun `rejects malformed percent-encoding rather than throwing`() {
        assertNull(SteamOpenIdCallback.parse("${SteamOpenIdConfig.RETURN_TO}?openid.mode=id_res%"))
    }

    @Test
    fun `a duplicate key keeps its last occurrence`() {
        val withDup = "$validQuery&openid.mode=cancel"
        // last occurrence of openid.mode is "cancel" -> the whole callback is rejected
        assertNull(SteamOpenIdCallback.parse("${SteamOpenIdConfig.RETURN_TO}?$withDup"))
    }

    // ---- signedCoversClaimedId ---------------------------------------------

    @Test
    fun `signedCoversClaimedId accepts a signed list naming claimed_id`() {
        assertTrue(
            SteamOpenIdCallback.signedCoversClaimedId(
                "signed,op_endpoint,claimed_id,identity,return_to,response_nonce,assoc_handle",
            ),
        )
    }

    @Test
    fun `MUTATION PIN -- signedCoversClaimedId rejects a list that omits claimed_id`() {
        assertFalse(
            SteamOpenIdCallback.signedCoversClaimedId(
                "signed,op_endpoint,identity,return_to,response_nonce,assoc_handle",
            ),
        )
    }

    @Test
    fun `signedCoversClaimedId rejects an empty string`() {
        assertFalse(SteamOpenIdCallback.signedCoversClaimedId(""))
    }

    @Test
    fun `signedCoversClaimedId does not match a field name merely containing claimed_id as a substring`() {
        assertFalse(SteamOpenIdCallback.signedCoversClaimedId("not_claimed_id,other"))
    }

    // ---- signedCoversReturnTo (WP APP-FIX-1 S4a) ---------------------------

    @Test
    fun `signedCoversReturnTo accepts a signed list naming return_to`() {
        assertTrue(
            SteamOpenIdCallback.signedCoversReturnTo(
                "signed,op_endpoint,claimed_id,identity,return_to,response_nonce,assoc_handle",
            ),
        )
    }

    @Test
    fun `MUTATION PIN -- signedCoversReturnTo rejects a list that omits return_to`() {
        assertFalse(
            SteamOpenIdCallback.signedCoversReturnTo(
                "signed,op_endpoint,claimed_id,identity,response_nonce,assoc_handle",
            ),
        )
    }

    @Test
    fun `signedCoversReturnTo does not match a field name merely containing return_to as a substring`() {
        assertFalse(SteamOpenIdCallback.signedCoversReturnTo("not_return_to,other"))
        assertFalse(SteamOpenIdCallback.signedCoversReturnTo(""))
    }

    // ---- steamId64From ------------------------------------------------------

    @Test
    fun `extracts a valid SteamID64 from a well-formed claimed_id`() {
        assertEquals(
            "76561198042117903",
            SteamOpenIdCallback.steamId64From("https://steamcommunity.com/openid/id/76561198042117903"),
        )
    }

    @Test
    fun `MUTATION PIN -- rejects a claimed_id on the wrong host`() {
        assertNull(SteamOpenIdCallback.steamId64From("https://attacker.example/openid/id/76561198042117903"))
    }

    @Test
    fun `rejects a claimed_id with an extra path segment`() {
        assertNull(SteamOpenIdCallback.steamId64From("https://steamcommunity.com/openid/id/76561198042117903/extra"))
    }

    @Test
    fun `rejects a claimed_id whose tail is not a valid SteamID64`() {
        assertNull(SteamOpenIdCallback.steamId64From("https://steamcommunity.com/openid/id/not-a-steamid"))
    }

    @Test
    fun `rejects an empty tail`() {
        assertNull(SteamOpenIdCallback.steamId64From("https://steamcommunity.com/openid/id/"))
    }

    // ---- stateFromReturnTo (WP 4b.7 replay-residual fix) -------------------

    @Test
    fun `extracts the state parameter from a return_to query string`() {
        assertEquals(
            "abc123",
            SteamOpenIdCallback.stateFromReturnTo("${SteamOpenIdConfig.RETURN_TO}?state=abc123"),
        )
    }

    @Test
    fun `returns null when return_to has no query string at all`() {
        assertNull(SteamOpenIdCallback.stateFromReturnTo(SteamOpenIdConfig.RETURN_TO))
    }

    @Test
    fun `returns null when return_to has a query string but no state key`() {
        assertNull(SteamOpenIdCallback.stateFromReturnTo("${SteamOpenIdConfig.RETURN_TO}?other=1"))
    }

    @Test
    fun `finds state alongside other query parameters, in either position`() {
        assertEquals(
            "xyz",
            SteamOpenIdCallback.stateFromReturnTo("${SteamOpenIdConfig.RETURN_TO}?other=1&state=xyz"),
        )
        assertEquals(
            "xyz",
            SteamOpenIdCallback.stateFromReturnTo("${SteamOpenIdConfig.RETURN_TO}?state=xyz&other=1"),
        )
    }

    @Test
    fun `decodes a percent-encoded state value`() {
        assertEquals(
            "a+b/c",
            SteamOpenIdCallback.stateFromReturnTo("${SteamOpenIdConfig.RETURN_TO}?state=" + enc("a+b/c")),
        )
    }

    @Test
    fun `returns null for a trailing question mark with no query content`() {
        assertNull(SteamOpenIdCallback.stateFromReturnTo("${SteamOpenIdConfig.RETURN_TO}?"))
    }
}
