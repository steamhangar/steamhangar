package dev.steamvault.app.net.pairing

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * WP APP-PAIR-1: the pairing-link contract shared with the web package
 * PAIR-1, pinned with LITERAL strings (docs/LEARNINGS.md, Android: a
 * cross-frontend contract is never derived from the constants under test).
 *
 * The web twin is not on main yet, so there is no cross-pin to a web file
 * here; when PAIR-1 lands, a twin test can read its link builder and
 * compare against the same literal below.
 *
 *     steamhangar://pair?v=1&url=<percent-encoded base URL>&key=<percent-encoded API key>
 */
class PairingLinkContractTest {

    @Test
    fun `the contract literal parses into the URL and key it carries`() {
        val result = PairingLink.parse(
            "steamhangar://pair?v=1&url=https%3A%2F%2Fhangar.example.org&key=Zx9_-abcDEF0123456789",
        )
        assertTrue("the literal contract link must be accepted, got $result", result is PairingParseResult.Valid)
        val request = (result as PairingParseResult.Valid).request
        assertEquals("https://hangar.example.org", request.baseUrl)
        assertEquals("hangar.example.org", request.displayHost)
        assertEquals("Zx9_-abcDEF0123456789", request.apiKey)
    }

    @Test
    fun `the contract wire names are the literal ones`() {
        assertEquals("steamhangar", PairingLinkContract.SCHEME)
        assertEquals("pair", PairingLinkContract.HOST)
        assertEquals("v", PairingLinkContract.PARAM_VERSION)
        assertEquals("url", PairingLinkContract.PARAM_URL)
        assertEquals("key", PairingLinkContract.PARAM_KEY)
        assertEquals("1", PairingLinkContract.SUPPORTED_VERSION)
    }

    @Test
    fun `a token_urlsafe key, the format the deploy docs generate, passes through unchanged`() {
        // python -c "import secrets; print(secrets.token_urlsafe(36))" -- synthetic sample of that alphabet.
        val key = "q8Zr-1_bVn3kXw0PpLm2Aa9-ZZ_yTt7RrUu4Ee5Oo6Ii8Hh"
        val result = PairingLink.parse("steamhangar://pair?v=1&url=http%3A%2F%2F192.168.1.50%3A8080&key=$key")
        assertEquals(key, (result as PairingParseResult.Valid).request.apiKey)
        assertEquals("http://192.168.1.50:8080", result.request.baseUrl)
    }

    // ---- AndroidManifest.xml: the filter that makes the camera app open us ----

    private val manifest: String = File("src/main/AndroidManifest.xml").let {
        check(it.exists()) { "expected the manifest at ${it.absolutePath}" }
        it.readText(Charsets.UTF_8).replace(Regex("<!--.*?-->", RegexOption.DOT_MATCHES_ALL), "")
    }

    private fun intentFilters(): List<String> =
        Regex("<intent-filter\\b.*?</intent-filter>", RegexOption.DOT_MATCHES_ALL).findAll(manifest).map { it.value }.toList()

    @Test
    fun `MUTATION PIN -- the manifest declares a browsable VIEW filter for steamhangar pair`() {
        val pairing = intentFilters().filter { it.contains("android:scheme=\"steamhangar\"") }
        assertEquals("expected exactly one intent-filter for the steamhangar scheme:\n$manifest", 1, pairing.size)
        val filter = pairing.single()
        for (needle in listOf(
            "android:host=\"pair\"",
            "android.intent.action.VIEW",
            "android.intent.category.DEFAULT",
            "android.intent.category.BROWSABLE",
        )) {
            assertTrue("the pairing intent-filter must contain $needle:\n$filter", filter.contains(needle))
        }
    }

    @Test
    fun `the Steam OpenID callback filter is still declared next to the pairing one`() {
        val openId = intentFilters().filter { it.contains("android:scheme=\"steamvault\"") }
        assertEquals(1, openId.size)
        assertTrue(openId.single().contains("android:host=\"auth\""))
        assertTrue(openId.single().contains("android:path=\"/openid-return\""))
    }
}
