package dev.steamvault.app.ui.library

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * WP APP-FIX-CARD: the grid badge drops to "Update" when "Update available"
 * does not fit. The words are LITERALS transcribed from web
 * `web/js/components/game-card.js` (`UPDATE_BADGE_TEXT`,
 * `UPDATE_BADGE_COMPACT_TEXT`), never derived from `strings.xml`
 * (docs/LEARNINGS.md "Android (Phase 4b)"). The pixel side -- which word a
 * real card shows, and that it is never ellipsized -- is pinned in
 * `LibraryScreenshotTest.assertCardContents`.
 */
class UpdateBadgeLabelTest {

    private val xml: String by lazy {
        val file = File("src/main/res/values/strings.xml")
        check(file.exists()) { "expected resource file at ${file.absolutePath}" }
        file.readText(Charsets.UTF_8)
    }

    private fun res(name: String): String =
        Regex("<string name=\"$name\"[^>]*>(.*?)</string>").find(xml)?.groupValues?.get(1)
            ?: error("no <string name=\"$name\"> found")

    @Test
    fun `badge words match the web full and compact labels`() {
        assertEquals("Update available", res("library_update_available_badge"))
        assertEquals("Update", res("library_update_available_badge_short"))
    }

    @Test
    fun `the full label is kept while it fits, down to the exact width`() {
        assertTrue(updateBadgeFullLabelFits(fullLabelWidthPx = 200, availableTextWidthPx = 260f))
        assertTrue(updateBadgeFullLabelFits(fullLabelWidthPx = 200, availableTextWidthPx = 200f))
    }

    @Test
    fun `one pixel short switches to the compact label`() {
        assertFalse(updateBadgeFullLabelFits(fullLabelWidthPx = 200, availableTextWidthPx = 199.5f))
        assertFalse(updateBadgeFullLabelFits(fullLabelWidthPx = 200, availableTextWidthPx = 0f))
    }
}
