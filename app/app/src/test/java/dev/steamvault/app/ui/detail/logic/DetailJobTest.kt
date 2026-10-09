package dev.steamvault.app.ui.detail.logic

import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.ui.library.logic.dispKind
import dev.steamvault.app.ui.status.StatusKind
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class DetailJobTest {

    private fun job(id: Int, appid: Int, type: String, status: String) = JobSummary(
        id = id, appid = appid, type = type, status = status, created_at = "2026-08-01T00:00:00Z",
    )

    @Test
    fun `finds a queued prefill job for the app -- broader than the grid card's findLiveJob`() {
        val jobs = listOf(job(1, 440, "prefill", "queued"))
        assertEquals(jobs[0], findTrackedJob(jobs, 440))
    }

    @Test
    fun `finds a running and a paused prefill job`() {
        assertEquals("running", findTrackedJob(listOf(job(1, 440, "prefill", "running")), 440)?.status)
        assertEquals("paused", findTrackedJob(listOf(job(1, 440, "prefill", "paused")), 440)?.status)
    }

    @Test
    fun `ignores a GC job for the same app -- GC jobs are not job-control targets here`() {
        val jobs = listOf(job(1, 440, "gc", "running"))
        assertNull(findTrackedJob(jobs, 440))
    }

    @Test
    fun `ignores a finished job and a job for a different app`() {
        assertNull(findTrackedJob(listOf(job(1, 440, "prefill", "done")), 440))
        assertNull(findTrackedJob(listOf(job(1, 730, "prefill", "running")), 440))
    }

    @Test
    fun `MUTATION TARGET -- headerLiveJob drops a queued job, keeps running and paused`() {
        assertNull(headerLiveJob(listOf(job(1, 440, "prefill", "queued")), 440))
        assertEquals("running", headerLiveJob(listOf(job(1, 440, "prefill", "running")), 440)?.status)
        assertEquals("paused", headerLiveJob(listOf(job(1, 440, "prefill", "paused")), 440)?.status)
        assertNull(headerLiveJob(listOf(job(1, 440, "gc", "running")), 440))
    }

    @Test
    fun `a queued job on a completed copy does not read Updating or Verifying in the header`() {
        val queued = listOf(job(1, 440, "prefill", "queued"))
        for (needsForce in listOf(false, true)) {
            val game = GameSummary(
                appid = 440, status = "done", last_prefill_at = "2026-10-09T08:00:00Z",
                depot_count = 1, size_bytes = 5_000_000_000L, needs_force = needsForce,
            )
            assertEquals(StatusKind.CACHED, dispKind(game, headerLiveJob(queued, 440)))
        }
    }

    @Test
    fun `GameDetailSheet takes the header kind from headerLiveJob, the buttons from findTrackedJob`() {
        val file = File("src/main/java/dev/steamvault/app/ui/detail/GameDetailSheet.kt")
        check(file.exists()) { "expected a file at ${file.absolutePath}" }
        val src = file.readText(Charsets.UTF_8)
        assertTrue(
            "header wiring: expected 'val liveJob = headerLiveJob(jobs, appid)' right before the dispKind call",
            Regex("""val liveJob = headerLiveJob\(jobs, appid\)\s*\n\s*val kind = dispKind\(gameSummaryFrom\(detail\), liveJob\)""").containsMatchIn(src),
        )
        assertTrue("buttons still use findTrackedJob", src.contains("val trackedJob = findTrackedJob(jobs, appid)"))
    }

    @Test
    fun `job control table -- queued offers cancel only`() {
        assertEquals(setOf(DetailJobAction.CANCEL), detailJobActions(job(1, 440, "prefill", "queued")))
    }

    @Test
    fun `job control table -- running offers pause and cancel`() {
        assertEquals(setOf(DetailJobAction.PAUSE, DetailJobAction.CANCEL), detailJobActions(job(1, 440, "prefill", "running")))
    }

    @Test
    fun `job control table -- paused offers resume and cancel`() {
        assertEquals(setOf(DetailJobAction.RESUME, DetailJobAction.CANCEL), detailJobActions(job(1, 440, "prefill", "paused")))
    }

    @Test
    fun `job control table -- no job, or a finished job, offers nothing`() {
        assertEquals(emptySet<DetailJobAction>(), detailJobActions(null))
        assertEquals(emptySet<DetailJobAction>(), detailJobActions(job(1, 440, "prefill", "done")))
    }
}
