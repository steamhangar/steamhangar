package dev.steamvault.app

import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * NITS-APP-1 (APP-NIGHT-GOLDEN-1 review): the theme is single-dark, so the
 * `night` Library golden must equal its `notnight` twin (app/README.md,
 * screenshot table). Without this pin, a blind golden refresh would
 * silently accept a night shot that started to differ (a theme or resource
 * that reacts to the night qualifier).
 *
 * The check compares the PNG files byte for byte, not decoded pixels:
 * `javax.imageio` is not on the Android unit-test classpath, and both
 * goldens come from the same renderer, so identical pixels yield identical
 * bytes. A byte mismatch means the twin must be inspected (and the night
 * shot re-recorded or the theme fixed).
 */
class NightGoldenTwinTest {

    private val dir = "src/testDebug/screenshots"

    private fun load(name: String): Pair<File, ByteArray> = File("$dir/$name.png").let { file ->
        assertTrue("expected a golden at ${file.absolutePath}", file.exists())
        file to file.readBytes()
    }

    @Test
    fun `the night Library golden equals its notnight twin byte for byte`() {
        val (dayFile, day) = load("library_411dp_font100")
        val (nightFile, night) = load("library_411dp_font100_night")
        val firstDiff = (0 until minOf(day.size, night.size)).firstOrNull { day[it] != night[it] }
        assertTrue(
            "night golden ${nightFile.name} (${night.size} bytes) differs from its notnight twin " +
                "${dayFile.name} (${day.size} bytes), first differing byte at " +
                "${firstDiff ?: minOf(day.size, night.size)}; the single-dark theme must render " +
                "both identically",
            day.contentEquals(night),
        )
    }
}
