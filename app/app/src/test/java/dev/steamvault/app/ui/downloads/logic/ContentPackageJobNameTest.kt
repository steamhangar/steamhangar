package dev.steamvault.app.ui.downloads.logic

import dev.steamvault.app.net.model.GameSummary
import org.junit.Assert.assertEquals
import org.junit.Test

/** WP TOOL-APPS-2d (ADR-0022 §2): a content package's job in Downloads. */
class ContentPackageJobNameTest {

    private fun g(appid: Int, name: String?, parent: Int? = null) =
        GameSummary(appid = appid, name = name, status = "done", depot_count = 1, parent_appid = parent)

    @Test
    fun `an unnamed package reads parent title, content, appid`() {
        val games = listOf(g(100, "Starfall"), g(101, null, parent = 100)).associateBy { it.appid }
        assertEquals("Starfall · content 101", nameFor(101, games))
    }

    @Test
    fun `its own name or an owned name wins, an unknown parent falls back`() {
        val games = listOf(g(100, "Starfall"), g(101, "Starfall - Pack", parent = 100), g(102, null, parent = 999))
            .associateBy { it.appid }
        assertEquals("Starfall - Pack", nameFor(101, games))
        assertEquals("Owned Pack", nameFor(102, games, mapOf(102 to "Owned Pack")))
        assertEquals("App 102", nameFor(102, games))
        assertEquals("Owned Starfall · content 103", nameFor(103, mapOf(103 to g(103, null, parent = 100)), mapOf(100 to "Owned Starfall")))
    }
}
