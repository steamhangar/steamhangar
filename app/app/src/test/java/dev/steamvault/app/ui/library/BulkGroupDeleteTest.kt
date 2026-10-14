package dev.steamvault.app.ui.library

import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.ui.library.logic.classifyBulkSelection
import dev.steamvault.app.ui.library.logic.expandSelection
import dev.steamvault.app.ui.library.logic.groupGames
import org.junit.Assert.assertEquals
import org.junit.Test

/** WP TOOL-APPS-2d: which picked cards the bulk delete offers (twin of web
 * `syncBulk`'s TOOL-APPS-2c branch). */
class BulkGroupDeleteTest {

    private fun g(appid: Int, size: Long?, parent: Int? = null, status: String = "done") = GameSummary(
        appid = appid,
        name = "App $appid",
        status = status,
        last_prefill_at = if (status == "done") "2026-10-01T00:00:00Z" else null,
        depot_count = 1,
        size_bytes = size,
        parent_appid = parent,
    )

    private fun deletable(rows: List<GameSummary>, picked: List<Int>, jobs: List<JobSummary> = emptyList()): List<Int> {
        val grouped = groupGames(rows)
        val pickedRows = rows.filter { it.appid in picked }
        val classification = classifyBulkSelection(expandSelection(pickedRows, grouped), jobs)
        return bulkDeletableCards(pickedRows, grouped, jobs, classification).map { it.appid }
    }

    @Test
    fun `a group card is deletable when only a package holds bytes`() {
        val rows = listOf(g(100, null, status = "idle"), g(101, 5_000L, parent = 100))
        assertEquals(listOf(100), deletable(rows, listOf(100)))
    }

    @Test
    fun `MUTATION PIN -- a busy package makes its group card not deletable`() {
        val rows = listOf(g(100, 1_000L), g(101, 5_000L, parent = 100), g(200, 1_000L))
        val job = JobSummary(id = 1, appid = 101, type = "prefill", status = "queued", created_at = "x")
        assertEquals(listOf(200), deletable(rows, listOf(100, 200), listOf(job)))
    }

    @Test
    fun `plain cards keep the old rule`() {
        val rows = listOf(g(200, 1_000L), g(201, null, status = "error"))
        assertEquals(listOf(200), deletable(rows, listOf(200, 201)))
    }
}
