package dev.steamvault.app.ui.downloads

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.ClientsOverviewOut
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.net.model.LiveOut
import dev.steamvault.app.net.model.SeriesOut
import dev.steamvault.app.net.model.ThroughputOut
import dev.steamvault.app.repo.TrafficRepository
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * WP ANDROID-DL-1 (web DOWNLOADS-3 `history` state): the History filter
 * knows when its range fetch has answered ([HistoryFilter.loaded], the
 * subline's "no number before that" rule) and how many rows are on screen
 * ([HistoryFilter.shown], 10 first, +20 per "Show more"); a range change
 * starts both again.
 */
class HistoryFilterTest {

    private class FakeRepository(var fail: Boolean = false) : TrafficRepository {
        val asked = mutableListOf<Pair<String, Int>>()
        override suspend fun throughput(range: String): ThroughputOut = error("unused")
        override suspend fun series(range: String): SeriesOut = error("unused")
        override suspend fun throughputLive(): LiveOut = error("unused")
        override suspend fun clientsOverview(range: String): ClientsOverviewOut = error("unused")
        override suspend fun jobHistory(range: String, limit: Int): List<JobSummary> {
            asked += range to limit
            if (fail) throw VaultApiError.Network("down")
            return (1..3).map { JobSummary(id = it, appid = 10 + it, type = "prefill", status = "done", created_at = "2026-10-30T10:00:00Z") }
        }
    }

    @Test
    fun `loaded flips on the first answer, shown grows by 20, a range change resets both`() = runTest {
        val repo = FakeRepository()
        val filter = HistoryFilter(repo, "all")
        assertFalse("no number before the range fetch answered", filter.loaded)
        assertEquals(10, filter.shown)
        filter.load()
        assertTrue(filter.loaded)
        assertEquals(listOf("all" to 200), repo.asked)
        assertEquals(3, filter.jobs.size)
        filter.showMore()
        assertEquals(30, filter.shown)
        filter.showMore()
        assertEquals(50, filter.shown)

        filter.selectRange(this, "7d")
        assertFalse("a new range has no answer yet", filter.loaded)
        assertEquals("a new range starts at the first page", 10, filter.shown)
        assertTrue(filter.jobs.isEmpty())
        testScheduler.advanceUntilIdle()
        assertTrue(filter.loaded)
        assertEquals("7d" to 200, repo.asked.last())
    }

    @Test
    fun `a failed fetch still counts as answered (web parity), with the poll's rows only`() = runTest {
        val filter = HistoryFilter(FakeRepository(fail = true), "24h")
        filter.load()
        assertTrue(filter.loaded)
        assertTrue(filter.jobs.isEmpty())
    }
}
