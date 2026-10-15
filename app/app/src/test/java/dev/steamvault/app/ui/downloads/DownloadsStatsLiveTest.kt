package dev.steamvault.app.ui.downloads

import dev.steamvault.app.demo.DemoState
import dev.steamvault.app.demo.DemoTrafficRepository
import dev.steamvault.app.demo.demoLive
import dev.steamvault.app.net.model.ClientsOverviewOut
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.net.model.LiveOut
import dev.steamvault.app.net.model.SeriesOut
import dev.steamvault.app.net.model.ThroughputOut
import dev.steamvault.app.repo.TrafficRepository
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** WP GRAPH-LIVE: the statistics poll's live branch (web `fetchLive` / `liveSummary`). */
class DownloadsStatsLiveTest {

    private class CountingRepository(private val inner: TrafficRepository) : TrafficRepository {
        val calls = mutableListOf<String>()
        override suspend fun throughput(range: String): ThroughputOut = inner.throughput(range).also { calls += "throughput:$range" }
        override suspend fun series(range: String): SeriesOut = inner.series(range).also { calls += "series:$range" }
        override suspend fun throughputLive(): LiveOut = inner.throughputLive().also { calls += "live" }
        override suspend fun clientsOverview(range: String): ClientsOverviewOut = inner.clientsOverview(range)
        override suspend fun jobHistory(range: String, limit: Int): List<JobSummary> = inner.jobHistory(range, limit)
    }

    @Test
    fun `the live range asks the live endpoint every tick and the 1 h summary every 30 s`() = runTest {
        val repo = CountingRepository(DemoTrafficRepository(DemoState.fresh()))
        var now = 1_000_000L
        val poll = downloadsStatsPoll(repo, "live", nowMs = { now })
        poll.refreshOnce()
        assertEquals(listOf("live", "throughput:1h"), repo.calls)
        val first = poll.data!!
        assertEquals("live", first.series.range)
        assertTrue(first.series.points.isNotEmpty())

        repeat(2) { now += 3_000L; poll.refreshOnce() }
        assertEquals("only the live endpoint within 30 s", listOf("live", "throughput:1h", "live", "live"), repo.calls)

        now += 30_000L
        poll.refreshOnce()
        assertEquals("the summary is refreshed once 30 s have passed", "throughput:1h", repo.calls.last())
        assertEquals(6, repo.calls.size)
    }

    @Test
    fun `leaving the live range drops the held summary`() = runTest {
        val repo = CountingRepository(DemoTrafficRepository(DemoState.fresh()))
        var now = 1_000_000L
        val poll = downloadsStatsPoll(repo, "live", nowMs = { now })
        poll.refreshOnce()
        poll.selectRange(this, "6h")
        testScheduler.advanceUntilIdle()
        assertEquals("6h", poll.range)
        assertTrue(repo.calls.containsAll(listOf("throughput:6h", "series:6h")))
        repo.calls.clear()
        poll.selectRange(this, "live")
        testScheduler.advanceUntilIdle()
        assertEquals("back on live: the summary is fetched again at once", listOf("live", "throughput:1h"), repo.calls)
        assertEquals(demoLive(now).range, poll.data!!.series.range)
    }
}
