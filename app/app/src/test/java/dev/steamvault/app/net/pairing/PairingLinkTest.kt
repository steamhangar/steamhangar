package dev.steamvault.app.net.pairing

import dev.steamvault.app.net.steam.SteamOpenIdConfig
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.net.URLEncoder

/**
 * WP APP-PAIR-1: accept/reject matrix for [PairingLink.parse] and the
 * routing between the pairing link and the Steam OpenID callback. The
 * literal wire format is pinned separately in `PairingLinkContractTest`.
 */
class PairingLinkTest {

    /** application/x-www-form-urlencoded, as `URLSearchParams` produces it (space as `+`, `+` as `%2B`). */
    private fun enc(value: String): String = URLEncoder.encode(value, "UTF-8")

    private fun link(
        v: String? = "1",
        url: String? = enc("https://hangar.example.org"),
        key: String? = enc("k3y_-ABCdef0123"),
        extra: String = "",
    ): String {
        val params = buildList {
            if (v != null) add("v=$v")
            if (url != null) add("url=$url")
            if (key != null) add("key=$key")
        }
        return "steamhangar://pair?" + params.joinToString("&") + extra
    }

    private fun valid(raw: String): PairingRequest {
        val result = PairingLink.parse(raw)
        assertTrue("expected a valid link, got $result", result is PairingParseResult.Valid)
        return (result as PairingParseResult.Valid).request
    }

    private fun assertRejected(expected: PairingRejection, raw: String) {
        val result = PairingLink.parse(raw)
        assertTrue("expected $expected, got $result", result is PairingParseResult.Invalid)
        assertEquals(expected, (result as PairingParseResult.Invalid).rejection)
    }

    // ---- accepted ---------------------------------------------------------

    @Test
    fun `the canonical https link is accepted and normalized`() {
        val request = valid(link())
        assertEquals("https://hangar.example.org", request.baseUrl)
        assertEquals("hangar.example.org", request.displayHost)
        assertFalse(request.usesCleartext)
        assertEquals("k3y_-ABCdef0123", request.apiKey)
    }

    @Test
    fun `an http LAN address with a port is accepted and flagged as cleartext`() {
        val request = valid(link(url = enc("http://192.168.1.50:8080")))
        assertEquals("http://192.168.1.50:8080", request.baseUrl)
        assertEquals("192.168.1.50:8080", request.displayHost)
        assertTrue(request.usesCleartext)
    }

    @Test
    fun `a default port and a trailing slash are normalized away`() {
        assertEquals("https://hangar.example.org", valid(link(url = enc("https://hangar.example.org:443/"))).baseUrl)
        assertEquals("http://hangar.lan", valid(link(url = enc("http://hangar.lan:80"))).baseUrl)
    }

    @Test
    fun `scheme and host case are normalized to lower case`() {
        assertEquals("https://hangar.example.org", valid(link(url = enc("HTTPS://Hangar.Example.ORG"))).baseUrl)
    }

    @Test
    fun `an IPv6 address keeps its brackets in the stored URL and the title`() {
        val request = valid(link(url = enc("http://[fd00::1]:8080")))
        assertEquals("http://[fd00::1]:8080", request.baseUrl)
        assertEquals("[fd00::1]:8080", request.displayHost)
    }

    @Test
    fun `an internationalized host is shown in punycode, so a look-alike host is visible as such`() {
        assertEquals("xn--bcher-kva.example", valid(link(url = enc("https://bücher.example"))).displayHost)
    }

    @Test
    fun `special characters in the key survive percent-encoding exactly`() {
        val key = "a+b/c=d&e%f?g#h~i!j*k'l(m)n,o;p@q"
        assertEquals(key, valid(link(key = enc(key))).apiKey)
    }

    @Test
    fun `encodeURIComponent style encoding decodes the same as URLSearchParams style`() {
        // encodeURIComponent("a+b c") == "a%2Bb%20c"; URLSearchParams gives "a%2Bb+c".
        assertEquals("a+b c", valid(link(key = "a%2Bb%20c")).apiKey)
        assertEquals("a+b c", valid(link(key = "a%2Bb+c")).apiKey)
    }

    @Test
    fun `MUTATION PIN -- a raw plus in a value is a space, the form-encoding rule both web encoders rely on`() {
        assertEquals("a b", valid(link(key = "a+b")).apiKey)
    }

    @Test
    fun `the key is trimmed`() {
        assertEquals("abc", valid(link(key = enc("  abc \t"))).apiKey)
    }

    @Test
    fun `extra parameters are ignored wherever they appear`() {
        val request = valid("steamhangar://pair?utm=qr&" + link().substringAfter('?') + "&future=1&flag")
        assertEquals("https://hangar.example.org", request.baseUrl)
    }

    @Test
    fun `an unknown parameter with broken encoding is ignored, not treated as damage`() {
        valid(link(extra = "&x=%ZZ"))
    }

    @Test
    fun `parameter order does not matter and empty segments are tolerated`() {
        val request = valid("steamhangar://pair?&key=abc&&url=${enc("https://h.example")}&v=1&")
        assertEquals("abc", request.apiKey)
        assertEquals("https://h.example", request.baseUrl)
    }

    @Test
    fun `scheme and host of the link itself are matched case-insensitively, and a slash before the query is fine`() {
        valid(link().replace("steamhangar://pair", "SteamHangar://PAIR"))
        valid(link().replace("steamhangar://pair?", "steamhangar://pair/?"))
    }

    // ---- version ------------------------------------------------------------

    @Test
    fun `a missing or empty version is refused`() {
        assertRejected(PairingRejection.VERSION_MISSING, link(v = null))
        assertRejected(PairingRejection.VERSION_MISSING, link(v = ""))
    }

    @Test
    fun `version 2 is refused with the version as detail`() {
        assertEquals(
            PairingParseResult.Invalid(PairingRejection.VERSION_UNSUPPORTED, "2"),
            PairingLink.parse(link(v = "2")),
        )
    }

    @Test
    fun `only the exact version 1 is accepted`() {
        assertRejected(PairingRejection.VERSION_UNSUPPORTED, link(v = "1.0"))
        assertRejected(PairingRejection.VERSION_UNSUPPORTED, link(v = "01"))
        assertRejected(PairingRejection.VERSION_UNSUPPORTED, link(v = "0"))
    }

    @Test
    fun `an unsupported version string is sanitized and bounded before it reaches the dialog`() {
        val result = PairingLink.parse(link(v = enc("<b>99</b> " + "x".repeat(40))))
        val detail = (result as PairingParseResult.Invalid).detail!!
        assertTrue("detail must be display-safe: $detail", detail.all { it.isLetterOrDigit() || it in "._-" })
        assertTrue(detail.length <= 16)
    }

    @Test
    fun `MUTATION PIN -- the version is checked before the other fields`() {
        // A future format may encode url/key differently: "update the app"
        // is the right message even if they look broken to this version.
        assertRejected(PairingRejection.VERSION_UNSUPPORTED, link(v = "2", url = null, key = null))
    }

    // ---- url ------------------------------------------------------------------

    @Test
    fun `a missing or empty url is refused`() {
        assertRejected(PairingRejection.URL_MISSING, link(url = null))
        assertRejected(PairingRejection.URL_MISSING, link(url = ""))
        assertRejected(PairingRejection.URL_MISSING, link(url = enc("   ")))
    }

    @Test
    fun `javascript, file, data and ftp urls are refused as not http`() {
        assertRejected(PairingRejection.URL_NOT_HTTP, link(url = enc("javascript:alert(1)")))
        assertRejected(PairingRejection.URL_NOT_HTTP, link(url = enc("JavaScript://hangar.example.org/%0Aalert(1)")))
        assertRejected(PairingRejection.URL_NOT_HTTP, link(url = enc("file:///data/data/dev.steamvault.app")))
        assertRejected(PairingRejection.URL_NOT_HTTP, link(url = enc("data:text/html,hi")))
        assertRejected(PairingRejection.URL_NOT_HTTP, link(url = enc("ftp://hangar.example.org")))
    }

    @Test
    fun `a url without a scheme is refused as not http`() {
        assertRejected(PairingRejection.URL_NOT_HTTP, link(url = enc("hangar.example.org")))
        assertRejected(PairingRejection.URL_NOT_HTTP, link(url = enc("//hangar.example.org")))
    }

    @Test
    fun `MUTATION PIN -- shapes OkHttp would leniently accept are refused`() {
        assertRejected(PairingRejection.URL_INVALID, link(url = enc("https:hangar.example.org")))
        assertRejected(PairingRejection.URL_INVALID, link(url = enc("https:///hangar.example.org")))
        assertRejected(PairingRejection.URL_INVALID, link(url = enc("https://evil.example\\@good.example")))
    }

    @Test
    fun `a url without a host is refused`() {
        assertRejected(PairingRejection.URL_INVALID, link(url = enc("https://")))
        assertRejected(PairingRejection.URL_INVALID, link(url = enc("https://:8080")))
    }

    @Test
    fun `whitespace or a control character inside the url is refused`() {
        assertRejected(PairingRejection.URL_INVALID, link(url = enc("https://hangar example.org")))
        assertRejected(PairingRejection.URL_INVALID, link(url = "https%3A%2F%2Fhang%0Aar.example.org"))
        // Trailing: trim() removes whitespace only, so a BEL survives it and is caught.
        assertRejected(PairingRejection.URL_INVALID, link(url = "https%3A%2F%2Fhangar.example.org%07"))
    }

    @Test
    fun `userinfo in the url is refused`() {
        assertRejected(PairingRejection.URL_HAS_USERINFO, link(url = enc("https://user:pw@hangar.example.org")))
        assertRejected(PairingRejection.URL_HAS_USERINFO, link(url = enc("https://good.example@evil.example")))
        assertRejected(PairingRejection.URL_HAS_USERINFO, link(url = enc("https://@hangar.example.org")))
    }

    @Test
    fun `a fragment in the url is refused, even an empty one`() {
        assertRejected(PairingRejection.URL_HAS_FRAGMENT, link(url = enc("https://hangar.example.org#x")))
        assertRejected(PairingRejection.URL_HAS_FRAGMENT, link(url = enc("https://hangar.example.org/#")))
    }

    @Test
    fun `a path or query in the url is refused, since the API client would drop it`() {
        assertRejected(PairingRejection.URL_HAS_PATH_OR_QUERY, link(url = enc("https://hangar.example.org/vault")))
        assertRejected(PairingRejection.URL_HAS_PATH_OR_QUERY, link(url = enc("https://hangar.example.org/?a=1")))
    }

    // ---- key ------------------------------------------------------------------

    @Test
    fun `a missing key is refused`() {
        assertRejected(PairingRejection.KEY_MISSING, link(key = null))
    }

    @Test
    fun `an empty or blank key is refused`() {
        assertRejected(PairingRejection.KEY_EMPTY, link(key = ""))
        assertRejected(PairingRejection.KEY_EMPTY, link(key = "%20%20"))
        assertRejected(PairingRejection.KEY_EMPTY, "steamhangar://pair?v=1&url=${enc("https://h.example")}&key")
    }

    @Test
    fun `a key that cannot travel in an HTTP header is refused`() {
        assertRejected(PairingRejection.KEY_INVALID_CHARACTERS, link(key = "ab%0Acd"))
        assertRejected(PairingRejection.KEY_INVALID_CHARACTERS, link(key = "ab%7Fcd"))
        assertRejected(PairingRejection.KEY_INVALID_CHARACTERS, link(key = enc("schlüssel")))
    }

    // ---- link shape -------------------------------------------------------------

    @Test
    fun `a duplicated parameter is refused and named`() {
        for (name in listOf("v", "url", "key")) {
            val value = if (name == "url") enc("https://evil.example") else "2"
            val raw = link(extra = "&$name=$value")
            assertEquals(PairingParseResult.Invalid(PairingRejection.DUPLICATE_PARAMETER, name), PairingLink.parse(raw))
        }
    }

    @Test
    fun `other schemes and hosts are not pairing links`() {
        assertRejected(PairingRejection.NOT_A_PAIRING_LINK, link().replace("//pair?", "//other?"))
        assertRejected(PairingRejection.NOT_A_PAIRING_LINK, link().replace("//pair?", "//pairing?"))
        assertRejected(PairingRejection.NOT_A_PAIRING_LINK, link().replace("steamhangar:", "steamvault:"))
        assertRejected(PairingRejection.NOT_A_PAIRING_LINK, link().replace("steamhangar://", "steamhangar:"))
        assertRejected(PairingRejection.NOT_A_PAIRING_LINK, "https://hangar.example.org/pair?v=1")
        assertRejected(PairingRejection.NOT_A_PAIRING_LINK, "")
    }

    @Test
    fun `broken percent-encoding in a known parameter is refused as a damaged link`() {
        assertRejected(PairingRejection.MALFORMED_LINK, link(key = "%ZZ"))
        assertRejected(PairingRejection.MALFORMED_LINK, link(key = "abc%4"))
        assertRejected(PairingRejection.MALFORMED_LINK, link(key = "abc%"))
        assertRejected(PairingRejection.MALFORMED_LINK, link(key = "%C3%28"))
    }

    @Test
    fun `MUTATION PIN -- percent-escapes accept ASCII hex digits only, not other Unicode digits`() {
        // Character.digit('٣', 16) == 3: a decoder built on it would
        // turn this into a valid escape (docs/LEARNINGS.md, Android).
        assertRejected(PairingRejection.MALFORMED_LINK, link(key = "%٣4"))
        assertNull(PairingLink.formDecode("%٣4"))
        assertEquals("4", PairingLink.formDecode("%34"))
    }

    @Test
    fun `a raw hash in the link is refused as a damaged link`() {
        assertRejected(PairingRejection.MALFORMED_LINK, link() + "#frag")
        assertRejected(PairingRejection.MALFORMED_LINK, "steamhangar://pair#x")
    }

    // ---- secrecy --------------------------------------------------------------------

    @Test
    fun `MUTATION PIN -- the key never appears in a string form of the parse result`() {
        val secret = "TOP-SECRET-KEY-123"
        val result = PairingLink.parse(link(key = secret))
        assertTrue(result is PairingParseResult.Valid)
        assertFalse(result.toString().contains(secret))
        assertFalse((result as PairingParseResult.Valid).request.toString().contains(secret))
        assertTrue(result.request.toString().contains("redacted"))
    }

    // ---- routing ----------------------------------------------------------------------

    @Test
    fun `the Steam OpenID callback routes to the OpenID handler`() {
        assertEquals(
            IncomingLinkRoute.STEAM_OPENID_CALLBACK,
            routeIncomingLink("${SteamOpenIdConfig.RETURN_TO}?openid.mode=id_res&state=abc"),
        )
        assertEquals(IncomingLinkRoute.STEAM_OPENID_CALLBACK, routeIncomingLink(SteamOpenIdConfig.RETURN_TO))
    }

    @Test
    fun `a pairing link routes to pairing, malformed or not`() {
        assertEquals(IncomingLinkRoute.PAIRING, routeIncomingLink(link()))
        assertEquals(IncomingLinkRoute.PAIRING, routeIncomingLink("steamhangar://pair"))
        assertEquals(IncomingLinkRoute.PAIRING, routeIncomingLink("STEAMHANGAR://Pair/?v=9"))
        assertEquals(IncomingLinkRoute.PAIRING, routeIncomingLink("steamhangar://pair#x"))
    }

    @Test
    fun `anything else routes nowhere`() {
        assertEquals(IncomingLinkRoute.NONE, routeIncomingLink(null))
        assertEquals(IncomingLinkRoute.NONE, routeIncomingLink("steamhangar://pairing?v=1"))
        assertEquals(IncomingLinkRoute.NONE, routeIncomingLink("steamhangar://auth/openid-return"))
        assertEquals(IncomingLinkRoute.NONE, routeIncomingLink("steamvault://pair?v=1"))
        assertEquals(IncomingLinkRoute.NONE, routeIncomingLink("https://hangar.example.org/"))
        assertEquals(IncomingLinkRoute.NONE, routeIncomingLink("steamvault://auth/other"))
    }

    @Test
    fun `MUTATION PIN -- the two link kinds never share a route`() {
        // Different schemes: the routing order in routeIncomingLink can
        // never make one kind shadow the other.
        assertFalse(SteamOpenIdConfig.SCHEME.equals(PairingLinkContract.SCHEME, ignoreCase = true))
        assertEquals(IncomingLinkRoute.STEAM_OPENID_CALLBACK, routeIncomingLink("${SteamOpenIdConfig.RETURN_TO}?url=x&key=y&v=1"))
        assertEquals(IncomingLinkRoute.PAIRING, routeIncomingLink("steamhangar://pair?openid.mode=id_res"))
    }
}
