package dev.steamvault.app.demo

import dev.steamvault.app.ui.clients.logic.clientsHaveFigures
import dev.steamvault.app.ui.clients.logic.installedText
import dev.steamvault.app.ui.clients.logic.isAgentClient
import dev.steamvault.app.ui.traffic.logic.FeedState
import dev.steamvault.app.ui.traffic.logic.TRAFFIC_RANGES
import dev.steamvault.app.ui.traffic.logic.feedState
import dev.steamvault.app.ui.traffic.logic.graphGeometryToShow
import dev.steamvault.app.ui.traffic.logic.headerTiles
import dev.steamvault.app.ui.traffic.logic.jobSpeedText
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * WP D15d brief: "demo mode gets data so the screens show". Pins that the
 * demo's traffic answers make every new surface render something real:
 * header tiles without dashes, a graph, client cards of both kinds, a speed
 * line under the running demo job, and history rows.
 */
class DemoTrafficTest {

    @Test
    fun `every range answers with an ok feed, figures and a drawable graph`() = runTest {
        val repo = DemoTrafficRepository(DemoState.fresh())
        for (range in TRAFFIC_RANGES) {
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
        val addresses = o.clients.filter { !isAgentClient(it) }
        assertEquals(2, agents.size)
        assertEquals(2, addresses.size)
        assertTrue(agents.any { it.connected == true } && agents.any { it.connected == false })
        assertTrue("an agent has installed games", agents.any { (it.installed_app_count ?: 0) > 0 })
        assertTrue(agents.all { installedText(it) != null })
        assertTrue("connected first", o.clients.first().connected == true)
        assertTrue("someone is loading now", o.clients.any { it.loading_now?.loading == true })
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
