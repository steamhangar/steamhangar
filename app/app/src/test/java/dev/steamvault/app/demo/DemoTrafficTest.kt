package dev.steamvault.app.demo

import dev.steamvault.app.ui.clients.logic.clientKindText
import dev.steamvault.app.ui.clients.logic.clientName
import dev.steamvault.app.ui.clients.logic.clientsHaveFigures
import dev.steamvault.app.ui.clients.logic.installedText
import dev.steamvault.app.ui.clients.logic.isAddressClient
import dev.steamvault.app.ui.clients.logic.isAgentClient
import dev.steamvault.app.ui.clients.logic.isRunnerClient
import dev.steamvault.app.ui.traffic.logic.FeedState
import dev.steamvault.app.ui.traffic.logic.STATS_RANGES
import dev.steamvault.app.ui.traffic.logic.TRAFFIC_RANGES
import dev.steamvault.app.ui.traffic.logic.feedState
import dev.steamvault.app.ui.traffic.logic.graphGeometryToShow
import dev.steamvault.app.ui.traffic.logic.headerTiles
import dev.steamvault.app.ui.traffic.logic.jobSpeedText
import dev.steamvault.app.ui.traffic.logic.seriesStartText
import dev.steamvault.app.ui.traffic.logic.trimSeries
import dev.steamvault.app.ui.traffic.logic.liveSeries
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

/**
 * WP D15d brief: "demo mode gets data so the screens show". Pins that the
 * demo's traffic answers make every new surface render something real:
 * header tiles without dashes, a graph, client cards of both kinds, a speed
 * line under the running demo job, and history rows.
 */
class DemoTrafficTest {

    /** The ranges `GET /v1/throughput?range=` answers (WP GRAPH-LIVE: "live" has its own endpoint). */
    private val PERSISTED_STATS_RANGES = STATS_RANGES.filter { it != "live" }

    @Test
    fun `WP GRAPH-LIVE -- the demo live answer has the server's stitched geometry and a drawable graph`() = runTest {
        val now = Instant.parse("2026-10-14T19:30:07Z").toEpochMilli()
        val live = demoLive(now)
        assertEquals(FeedState.OK, feedState(live.event_feed))
        assertEquals("live", live.range)
        // 5 s points for the last minute (+ the running cell), 15 s up to 5 min, 1-minute points up to an hour.
        assertEquals(3, live.points.map { it.bucket_seconds }.distinct().size)
        assertEquals(setOf(5, 15, 60), live.points.map { it.bucket_seconds }.toSet())
        assertEquals(setOf("live", "persisted"), live.points.map { it.source }.toSet())
        assertEquals(live.points.first().t, live.range_start)
        val times = live.points.map { Instant.parse(it.t).toEpochMilli() }
        assertEquals("oldest first", times.sorted(), times)
        for ((p, next) in live.points.zip(live.points.drop(1))) {
            assertEquals(
                "no gap and no overlap between ${p.t} and ${next.t}",
                Instant.parse(p.t).toEpochMilli() + p.bucket_seconds * 1000L,
                Instant.parse(next.t).toEpochMilli(),
            )
        }
        assertTrue("the last point is the running 5 s cell", live.points.last().bucket_seconds == 5)
        assertTrue(live.points.all { it.bytes_served == it.hit_bytes + it.miss_bytes + it.runner_bytes })
        assertNotNull(live.current)
        assertTrue(live.current!!.window_seconds!! in 15..19)
        // The graph shows (liveSeries -> trimSeries -> geometry), as the screen draws it.
        val series = liveSeries(live)
        assertNotNull(graphGeometryToShow(series.copy(points = trimSeries(series.points)), "live", false, 300f, 60f))
    }

    @Test
    fun `WP GRAPH-LIVE -- the demo repository serves the live answer and it moves with the clock`() = runTest {
        val repo = DemoTrafficRepository(DemoState.fresh())
        val live = repo.throughputLive()
        assertTrue(live.points.isNotEmpty())
        val a = demoLive(1_792_000_000_000L)
        val b = demoLive(1_792_000_005_000L)
        assertTrue("the series advances by a 5 s cell", a.points.last().t != b.points.last().t)
        assertEquals("deterministic per clock", a, demoLive(1_792_000_000_000L))
    }

    @Test
    fun `every range answers with an ok feed, figures and a drawable graph`() = runTest {
        val repo = DemoTrafficRepository(DemoState.fresh())
        for (range in PERSISTED_STATS_RANGES) {
            val t = repo.throughput(range)
            assertEquals(range, t.range)
            assertEquals(FeedState.OK, feedState(t.event_feed))
            val tiles = headerTiles(t)
            assertNotNull("$range: header tiles", tiles)
            assertTrue("$range: no dash in demo tiles: $tiles", tiles!!.none { it.value == "—" })
            val s = repo.series(range)
            assertEquals(range, s.range)
            assertNotNull("$range: the graph must show", graphGeometryToShow(s, range, false, 300f, 60f))
        }
    }

    @Test
    fun `WP GRAPH-ZOOM -- 1 h and 6 h come from the 1-minute tier and the graph zooms to the data`() = runTest {
        val repo = DemoTrafficRepository(DemoState.fresh())
        for ((range, count) in listOf("1h" to 60, "6h" to 360)) {
            val s = repo.series(range)
            assertEquals("1m", s.tier)
            assertEquals(60, s.bucket_seconds)
            assertEquals(count, s.points.size)
            assertNotNull("$range: the 1-minute tier has been recording", repo.throughput(range).event_feed.traffic_1m_since)
            // The demo download began part-way in: the empty lead-in is trimmed.
            val shown = s.copy(points = trimSeries(s.points))
            assertTrue("$range: trimmed", shown.points.size < s.points.size)
            assertTrue("$range: the caption follows the data", seriesStartText(shown) != seriesStartText(s))
        }
    }

    @Test
    fun `the running demo job gets a speed line`() = runTest {
        val state = DemoState.fresh()
        val t = DemoTrafficRepository(state).throughput("24h")
        val running = state.trafficSnapshot().runningJobs
        assertTrue("the demo starts with a running job", running.isNotEmpty())
        val entry = t.jobs.firstOrNull { it.job_id == running.first().id }
        assertNotNull(jobSpeedText(entry, feedState(t.event_feed)))
    }

    @Test
    fun `clients overview has agents with installed games and agentless addresses`() = runTest {
        val o = DemoTrafficRepository(DemoState.fresh()).clientsOverview("7d")
        assertTrue(clientsHaveFigures(o))
        val agents = o.clients.filter { isAgentClient(it) }
        val addresses = o.clients.filter { isAddressClient(it) }
        assertEquals(2, agents.size)
        assertEquals(2, addresses.size)
        assertTrue(agents.any { it.connected == true } && agents.any { it.connected == false })
        assertTrue("an agent has installed games", agents.any { (it.installed_app_count ?: 0) > 0 })
        assertTrue(agents.all { installedText(it) != null })
        assertTrue("connected first", o.clients.first().connected == true)
        assertTrue("someone is loading now", o.clients.any { it.loading_now?.loading == true })
    }

    @Test
    fun `D15-RUNNER and D15-NAMES -- a runner entry, a named agent, runner figures that add up`() = runTest {
        val state = DemoState.fresh()
        val repo = DemoTrafficRepository(state)
        val o = repo.clientsOverview("7d")
        val runners = o.clients.filter { isRunnerClient(it) }
        assertEquals(1, runners.size)
        val runner = runners.single()
        assertEquals("Prefill runner", clientName(runner))
        assertEquals("This server's prefill downloads · 172.18.0.1", clientKindText(runner))
        assertNull("the runner cannot be named", runner.name_addr)
        assertTrue("the runner loads the running demo job", runner.loading_now?.loading == true)
        val named = o.clients.filter { !it.name.isNullOrEmpty() }
        assertEquals("one named demo client", 1, named.size)
        assertEquals("Steam Deck", clientName(named.single()))
        assertEquals("vault-agent on \u2068demo-steamdeck\u2069 · 10.0.0.57", clientKindText(named.single()))
        assertTrue("every row carries display_name", o.clients.all { !it.display_name.isNullOrEmpty() })
        for (range in PERSISTED_STATS_RANGES) {
            val t = repo.throughput(range)
            val summary = t.summary!!
            val runnerBytes = summary.prefill_runner!!.bytes_served
            assertTrue("$range: the runner loaded something", runnerBytes > 0)
            assertEquals(summary.bytes_served, summary.client_bytes_served!! + runnerBytes)
            assertEquals(summary.client_bytes_served, summary.hit_bytes + summary.miss_bytes)
            assertNotNull(headerTiles(t)!!.first { it.key == "loaded" }.detail)
            assertTrue(t.runner_active_now == true)
            val s = repo.series(range)
            assertTrue(s.points.all { it.bytes_served == it.hit_bytes + it.miss_bytes + it.runner_bytes })
        }
    }

    @Test
    fun `history and the statistics poll do not advance the demo jobs`() = runTest {
        val state = DemoState.fresh()
        val repo = DemoTrafficRepository(state)
        val before = state.trafficSnapshot().runningJobs.map { it.id }
        repeat(5) {
            repo.throughput("24h")
            repo.clientsOverview("7d")
            repo.jobHistory("all", 200)
        }
        assertEquals(before, state.trafficSnapshot().runningJobs.map { it.id })
        assertTrue(repo.jobHistory("all", 200).isNotEmpty())
    }
}
