package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.net.model.GameSummary
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * WP TOOL-APPS-2d: chips count groups, not packages; "Failed" also counts a
 * group whose package failed (user decision Q3 B variant); a search finds a
 * group by a package's name (twin of web TOOL-APPS-2c library-filters).
 */
class LibraryFiltersGroupsTest {

    private fun g(appid: Int, name: String, status: String = "done", size: Long? = 1_000_000_000L, parent: Int? = null) =
        GameSummary(
            appid = appid,
            name = name,
            status = status,
            last_prefill_at = if (status == "done") "2026-10-01T00:00:00Z" else null,
            depot_count = 1,
            size_bytes = size,
            parent_appid = parent,
        )

    private val parent = g(100, "Starfall")
    private val okPkg = g(101, "Starfall - Voice Pack", parent = 100)
    private val failedPkg = g(102, "Starfall - Outfits", status = "error", size = null, parent = 100)
    private val failedGame = g(200, "Marrowlight", status = "error", size = null)
    private val plain = g(300, "Pixel Garden")

    private val grouped = groupGames(listOf(parent, okPkg, failedPkg, failedGame, plain))

    private fun counts(query: String = "") =
        chipCounts(grouped.rows, query, emptyMap(), emptySet(), grouped.packagesByParent).associate { it.key to it.count }

    @Test
    fun `MUTATION PIN -- a group counts once in every chip`() {
        val c = counts()
        assertEquals(3, c["all"])
        assertEquals(2, c["cached"]) // Starfall (the parent's own status) + Pixel Garden
    }

    @Test
    fun `MUTATION PIN -- Failed counts a group whose package failed, once`() {
        assertEquals(2, counts()["failed"]) // Marrowlight + the Starfall group
        val failed = visibleGames(grouped.rows, "", "failed", emptyMap(), grouped.packagesByParent)
        assertEquals(listOf(100, 200), failed.map { it.appid })
    }

    @Test
    fun `without packages the old behaviour stands`() {
        assertEquals(1, chipCounts(grouped.rows, "", emptyMap()).associate { it.key to it.count }["failed"])
    }

    @Test
    fun `a package's name finds its group`() {
        val visible = visibleGames(grouped.rows, "outfits", "all", emptyMap(), grouped.packagesByParent)
        assertEquals(listOf(100), visible.map { it.appid })
        assertEquals(1, counts("outfits")["all"])
    }
}
