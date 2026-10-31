package dev.steamvault.app.demo

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.ClientOut
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.ui.library.logic.isCompletable
import dev.steamvault.app.ui.library.logic.showsRepair
import dev.steamvault.app.ui.traffic.logic.loadingNowModel
import dev.steamvault.app.ui.traffic.logic.mergeLive
import dev.steamvault.app.ui.traffic.logic.mergeNow
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.time.Instant

/**
 * WP ANDROID-DL-2: demo mode shows what the web demo shows -- "Loading now"
 * rows (web `demoLoadingSources`), a working Repair for the damaged-only
 * Emberline group (web demo repair route), and the group figure by the web
 * demo's `demoGroupFields` rule.
 */
class DemoAndroidDl2Test {

    private val now = Instant.parse("2026-10-14T19:30:07Z").toEpochMilli()

    private fun snapshot(running: Boolean) = DemoTrafficSnapshot(
        runningJobs = if (running) {
            listOf(JobSummary(id = 901, appid = 4_010_030, type = "prefill", status = "running", created_at = "2026-10-14T19:28:00Z"))
        } else {
            emptyList()
        },
        games = listOf(GameSummary(appid = 4_010_030, name = "Wrenfield Static", status = "running", depot_count = 1)),
        agents = listOf(
            ClientOut(
                client_id = "demo-livingroom-pc", first_seen = "2026-09-14T19:30:00Z", last_reported_at = "2026-10-14T19:20:00Z",
                source_addrs = listOf("10.0.0.42"), last_seen_in_cache_log = "2026-10-14T19:20:00Z", presence = "online",
            ),
        ),
    )

    @Test
    fun `the demo names the runner with its job, the online PC and the loading address`() {
        val t = mergeNow(demoThroughput("6h", snapshot(running = true), now))
        val rows = loadingNowModel(t)!!.rows
        assertEquals(listOf("runner", "client", "client"), rows.map { it.kind })
        assertEquals("Prefill runner", rows[0].name)
        assertEquals("Wrenfield Static · job #901 · Downloading", rows[0].detail)
        assertEquals("6.4 MB/s", rows[0].speed)
        assertEquals("demo-livingroom-pc", rows[1].name)
        assertEquals("Wrenfield Static · 81% from the cache", rows[1].detail)
        assertEquals("10.0.0.61", rows[2].name)
        assertEquals("content not linked to one game · 92% from the cache", rows[2].detail)
        assertEquals("4.5 MB/s", rows[2].speed)
        // The live view carries rows too, from its own answer.
        val live = mergeLive(demoThroughput("1h", snapshot(running = true), now), demoLive(now, snapshot(running = true)))
        assertEquals("runner", loadingNowModel(live)!!.rows.first().kind)
        // Without a running job no runner row; the clients still load.
        val idle = loadingNowModel(mergeNow(demoThroughput("6h", snapshot(running = false), now)))!!.rows
        assertTrue(idle.none { it.kind == "runner" })
        // No snapshot (an older caller): no rows, no block.
        assertTrue(demoLive(now).loading_sources!!.isEmpty())
    }

    @Test
    fun `repair on the damaged-only demo game -- dry run, then execute queues the forced run`() = runTest {
        val state = DemoState.fresh()
        val repo = DemoCacheRepository(state)
        val before = state.listGameSummaries().first { it.appid == DEMO_GROUP_PARENT_APPID }
        assertTrue("the demo shows Repair", showsRepair(before, state.listJobSummaries(50)))
        val dry = repo.repair(DEMO_GROUP_PARENT_APPID, execute = false)
        assertEquals("dry-run", dry.mode)
        assertEquals(14, dry.damaged_chunks) // 14 000 000 bytes, one file per started MiB
        assertEquals(14_000_000L, dry.damaged_bytes)
        assertEquals(4_010_081, dry.depots.single().depotid)
        assertEquals(null, dry.job_id)
        val done = repo.repair(DEMO_GROUP_PARENT_APPID, execute = true)
        assertEquals(14, done.removed_chunks)
        assertTrue(done.job_id != null)
        val after = state.listGameSummaries().first { it.appid == DEMO_GROUP_PARENT_APPID }
        assertTrue("the deleted files are now missing", isCompletable(after))
        assertFalse("a running job hides Repair", showsRepair(after, state.listJobSummaries(50)))
        // A game without damaged files: a no-op dry run.
        assertEquals(0, repo.repair(4_010_010, execute = false).damaged_chunks)
        // Unknown app: 404 like the real route.
        try {
            repo.repair(1, execute = false)
            fail("expected 404")
        } catch (e: VaultApiError.NotFound) {
            assertEquals(404, e.status)
        }
    }

    @Test
    fun `the group figure follows the web demo's rule (packages measured, nothing unmeasured)`() {
        val g = DemoState.fresh().listGameSummaries().associateBy { it.appid }
        val parent = g.getValue(DEMO_GROUP_PARENT_APPID)
        assertEquals(99, parent.group_cache_complete_pct)
        assertEquals(0, parent.group_cache_unmeasured_depots)
        assertEquals(0L, parent.group_cache_unmeasured_bytes)
        val pkg = g.getValue(4_010_082)
        assertEquals(null, pkg.group_cache_complete_pct)
        assertEquals(0, pkg.group_cache_unmeasured_depots)
    }
}
