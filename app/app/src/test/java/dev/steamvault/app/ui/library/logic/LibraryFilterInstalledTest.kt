package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.demo.DemoState
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.InstalledOnEntry
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * WP LIB-FILTER-INST-1 -- the "Installed, not cached" library chip (twin of
 * web/tests/library-filter-installed.test.js). The predicate is the card's
 * own warning-badge state ([installedBadgeFor] == InstalledNotCached), the
 * chip is always listed (0 included), it ANDs with the search, and a D9
 * group row counts once.
 */
class LibraryFilterInstalledTest {

    private val at = "2026-10-14T08:00:00Z"
    private fun pcs(vararg ids: String) = ids.map { InstalledOnEntry(client_id = it, reported_at = at) }

    private fun row(
        appid: Int,
        name: String = "Game $appid",
        status: String = "done",
        size: Long? = 1_000L,
        installedOn: List<InstalledOnEntry> = emptyList(),
        toolApp: Boolean = false,
        parent: Int? = null,
    ) = GameSummary(
        appid = appid,
        name = name,
        status = status,
        last_prefill_at = if (status == "done") at else null,
        depot_count = 1,
        size_bytes = size,
        installed_on = installedOn,
        tool_app = toolApp,
        parent_appid = parent,
    )

    private fun count(rows: List<GameSummary>, query: String = "", packages: Map<Int, List<GameSummary>> = emptyMap()) =
        chipCounts(rows, query, emptyMap(), emptySet(), packages).first { it.key == "installed" }.count

    private fun shown(rows: List<GameSummary>, query: String = "", packages: Map<Int, List<GameSummary>> = emptyMap()) =
        visibleGames(rows, query, "installed", emptyMap(), packages).map { it.appid }

    @Test
    fun `chip definition -- key installed, web label verbatim, right after Not cached, never hidden`() {
        val i = FILTER_DEFS.indexOfFirst { it.key == "installed" }
        assertTrue("chip missing", i > 0)
        assertEquals("Installed, not cached", FILTER_DEFS[i].label)
        assertEquals("none", FILTER_DEFS[i - 1].key)
        assertTrue("installed" !in hiddenChipKeys(updatesSupported = false))
        assertTrue("installed" !in hiddenChipKeys(updatesSupported = true))
    }

    @Test
    fun `MUTATION PIN -- installed AND no bytes; not installed-and-cached, not uninstalled, not a tool app`() {
        val rows = listOf(
            row(1, status = "idle", size = null, installedOn = pcs("Demon")), // yes
            row(2, size = 0L, installedOn = pcs("Demon", "Frame")), // yes: zero bytes is "no bytes"
            row(3, size = 5_000_000_000L, installedOn = pcs("Demon")), // installed and cached
            row(4, status = "idle", size = null), // not installed
            row(6, size = null, installedOn = pcs("Demon"), toolApp = true), // tool app: never the warning
            row(7, status = "error", size = null, installedOn = pcs("Demon")), // failed, nothing on disk: yes
        )
        assertEquals(listOf(1, 2, 7), shown(rows))
        assertEquals(3, count(rows))
    }

    @Test
    fun `the chip and the card badge agree on every row`() {
        val rows = listOf(
            row(1, size = null, installedOn = pcs("Demon")),
            row(2, size = 9L, installedOn = pcs("Demon")),
            row(3, size = null),
            row(4, size = null, installedOn = pcs("Demon"), toolApp = true),
        )
        val badge = rows.filter { installedBadgeFor(it) is InstalledBadge.InstalledNotCached }.map { it.appid }
        assertEquals(badge, shown(rows))
    }

    @Test
    fun `always listed, with 0 when nothing matches`() {
        assertEquals(0, count(listOf(row(1))))
        assertEquals(0, count(emptyList()))
    }

    @Test
    fun `ANDs with the search -- the count follows the query`() {
        val rows = listOf(
            row(1, name = "Aurora Cascade", size = null, installedOn = pcs("Demon")),
            row(2, name = "Copper Horizon", size = null, installedOn = pcs("Demon")),
        )
        assertEquals(1, count(rows, "copper"))
        assertEquals(listOf(2), shown(rows, "copper"))
    }

    @Test
    fun `D9 -- a group row counts once and reads the parent's own row`() {
        val parent = row(100, name = "Starfall Legion", status = "idle", size = null, installedOn = pcs("Demon"))
        val pkgA = row(101, status = "idle", size = null, installedOn = pcs("Demon"), parent = 100)
        val pkgB = row(102, status = "idle", size = null, installedOn = pcs("Demon"), parent = 100)
        val grouped = groupGames(listOf(parent, pkgA, pkgB, row(200)))
        assertEquals("sanity: grouped", 2, grouped.rows.size)
        assertEquals(1, count(grouped.rows, packages = grouped.packagesByParent))
        assertEquals(listOf(100), shown(grouped.rows, packages = grouped.packagesByParent))
    }

    @Test
    fun `demo seed -- the chip shows a non-zero count, every match installed with no bytes`() {
        val grouped = groupGames(DemoState.fresh().listGameSummaries())
        val matches = visibleGames(grouped.rows, "", "installed", emptyMap(), grouped.packagesByParent)
        assertTrue("demo mode must show the chip with at least one game", matches.isNotEmpty())
        for (g in matches) {
            assertTrue(g.name ?: "", g.installed_on.isNotEmpty())
            assertTrue(g.name ?: "", (g.size_bytes ?: 0L) <= 0L)
        }
        assertEquals(matches.size, count(grouped.rows, packages = grouped.packagesByParent))
    }
}
