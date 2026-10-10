package dev.steamvault.app.ui.library.logic

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class CoverArtTest {

    @Test
    fun `coverArtUrl builds the pinned Steam CDN path`() {
        assertEquals(
            "https://cdn.akamai.steamstatic.com/steam/apps/440/library_600x900.jpg",
            coverArtUrl(440),
        )
    }

    @Test
    fun `coverArtUrl only ever targets the pinned CDN host`() {
        // Mirrors the web port's CSP-scope note: this is the ONLY function
        // allowed to construct a URL against STEAM_CDN_HOST.
        val url = coverArtUrl(123456)
        assertTrue(url.startsWith("https://$STEAM_CDN_HOST/"))
    }

    // --- WP API-FIX-5: server-resolved cover_url ---------------------------

    private val hashed =
        "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/3527290/" +
            "480bd879ac737921bfa2529a6fea15961267ad21/library_600x900.jpg?t=1790591892"
    private val legacy3527290 = "https://cdn.akamai.steamstatic.com/steam/apps/3527290/library_600x900.jpg"

    @Test
    fun `STEAM_ASSET_HOSTS is exactly the two CSP img-src hosts (literal pin)`() {
        assertEquals(
            setOf("cdn.akamai.steamstatic.com", "shared.akamai.steamstatic.com"),
            STEAM_ASSET_HOSTS,
        )
    }

    @Test
    fun `coverArtUrl prefers a valid server cover_url`() {
        assertEquals(hashed, coverArtUrl(3527290, hashed))
        val legacyHost = "https://cdn.akamai.steamstatic.com/steam/apps/440/abc/library_600x900.jpg"
        assertEquals(legacyHost, coverArtUrl(440, legacyHost))
    }

    @Test
    fun `coverArtUrl falls back to the legacy path without a cover_url`() {
        assertEquals(legacy3527290, coverArtUrl(3527290))
        assertEquals(legacy3527290, coverArtUrl(3527290, null))
    }

    @Test
    fun `serverCoverUrl rejects anything but https on the two asset hosts`() {
        val bad = listOf(
            "http://shared.akamai.steamstatic.com/store_item_assets/x.jpg",
            "https://evil.example/x.jpg",
            "https://shared.akamai.steamstatic.com.evil.example/x.jpg",
            "https://avatars.steamstatic.com/x.jpg",
            "https://SHARED.akamai.steamstatic.com/x.jpg",
            "https://u:p@shared.akamai.steamstatic.com/x.jpg",
            "https://shared.akamai.steamstatic.com:8443/x.jpg",
            "https://shared.akamai.steamstatic.com/x.jpg#f",
            "https://shared.akamai.steamstatic.com/x y.jpg",
            "https://shared.akamai.steamstatic.com\\x.jpg",
            "https://shared.akamai.steamstatic.com",
            "https://shared.akamai.steamstatic.com/\u00e9.jpg",
            "javascript:alert(1)",
            "https://shared.akamai.steamstatic.com/" + "a".repeat(600),
            "",
        )
        for (value in bad) {
            assertNull("must reject: $value", serverCoverUrl(value))
            assertEquals("must fall back for: $value", legacy3527290, coverArtUrl(3527290, value))
        }
    }

    @Test
    fun `fallbackHues are deterministic for the same appid`() {
        assertEquals(fallbackHues(440), fallbackHues(440))
    }

    @Test
    fun `fallbackHues differ for different appids (not a constant)`() {
        assertTrue(fallbackHues(440) != fallbackHues(570))
    }

    @Test
    fun `fallbackHues stay within 0-359`() {
        for (appid in listOf(1, 440, 570, 123456, Int.MAX_VALUE / 2)) {
            val hues = fallbackHues(appid)
            assertTrue(hues.h1 in 0..359)
            assertTrue(hues.h2 in 0..359)
        }
    }

    @Test
    fun `fallbackPattern is deterministic and within 0-5`() {
        for (appid in listOf(1, 440, 570, 123456)) {
            val pattern = fallbackPattern(appid)
            assertEquals(pattern, fallbackPattern(appid))
            assertTrue(pattern in 0..5)
        }
    }
}
