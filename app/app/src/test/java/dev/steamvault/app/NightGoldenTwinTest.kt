package dev.steamvault.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import javax.imageio.ImageIO

/**
 * NITS-APP-1 (APP-NIGHT-GOLDEN-1 review): the theme is single-dark, so the
 * `night` Library golden must equal its `notnight` twin pixel for pixel
 * (app/README.md, screenshot table). Without this pin, a blind golden
 * refresh would silently accept a night shot that started to differ (a
 * theme or resource that reacts to the night qualifier).
 */
class NightGoldenTwinTest {

    private val dir = "src/testDebug/screenshots"

    private fun load(name: String) = File("$dir/$name.png").let { file ->
        assertTrue("expected a golden at ${file.absolutePath}", file.exists())
        checkNotNull(ImageIO.read(file)) { "not a readable PNG: ${file.absolutePath}" }
    }

    @Test
    fun `the night Library golden equals its notnight twin pixel for pixel`() {
        val day = load("library_411dp_font100")
        val night = load("library_411dp_font100_night")
        assertEquals("width", day.width, night.width)
        assertEquals("height", day.height, night.height)
        var differing = 0
        var first: Pair<Int, Int>? = null
        for (y in 0 until day.height) {
            for (x in 0 until day.width) {
                if (day.getRGB(x, y) != night.getRGB(x, y)) {
                    differing++
                    if (first == null) first = x to y
                }
            }
        }
        assertEquals("pixels that differ between night and notnight (first at $first)", 0, differing)
    }
}
