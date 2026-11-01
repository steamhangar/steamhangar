package dev.steamvault.app.ui.downloads.logic

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.JobSummary
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Behaviour of `BulkJobs.kt`, mirroring `web/tests/bulk-jobs.test.js`. */
class BulkJobsTest {

    private fun job(id: Int, status: String, type: String = "prefill", stopRequest: String? = null) = JobSummary(
        id = id,
        appid = 1000 + id,
        type = type,
        status = status,
        created_at = "2026-08-01T00:00:00Z",
        stop_request = stopRequest,
    )

    @Test
    fun `pause targets are queued prefill and gc jobs, then running prefill jobs, oldest id first, no stop_request`() {
        val jobs = listOf(
            job(9, "queued"), job(3, "queued"), job(5, "running"),
            job(4, "running", stopRequest = "pause"), job(6, "queued", type = "gc"),
            job(10, "running", type = "gc"), job(11, "queued", type = "repair"),
            job(7, "paused"), job(8, "done"),
        )
        val t = bulkPauseTargets(jobs)
        // WP ANDROID-PAUSE-GC-1: a queued GC job is held; a running one is not sent (409).
        assertEquals(listOf(3, 6, 9), t.queued.map { it.id })
        assertEquals(listOf(5), t.running.map { it.id })
        assertEquals(4, t.count)
        assertEquals(1, t.gcQueued)
    }

    @Test
    fun `resume targets are every paused job, oldest first`() {
        val jobs = listOf(job(8, "paused"), job(2, "paused"), job(3, "queued"))
        assertEquals(listOf(2, 8), bulkResumeTargets(jobs).map { it.id })
    }

    @Test
    fun `bar state shows the buttons separately and counts queued and running gc runs`() {
        val s = bulkBarState(listOf(job(1, "running"), job(2, "queued", type = "gc")))
        assertTrue(s.pauseVisible)
        assertEquals(2, s.pauseCount)
        assertFalse(s.resumeVisible)
        assertEquals(1, s.gcQueued)
        assertFalse(s.gcRunning)
        val running = bulkBarState(listOf(job(3, "running", type = "gc")))
        assertTrue(running.gcRunning)
        assertEquals(0, running.gcQueued)
        assertFalse("a running gc job alone gives nothing to pause", running.pauseVisible)
        assertFalse(bulkBarState(listOf(job(1, "done"))).visible)
        assertTrue(bulkBarState(listOf(job(1, "paused"))).resumeVisible)
    }

    @Test
    fun `confirm gc lines say how many runs are held, then that a running one finishes`() {
        assertEquals(emptyList<String>(), confirmGcLines(bulkBarState(listOf(job(1, "running")))))
        assertEquals(
            listOf("This includes 1 queued garbage-collection run: it is held too and deletes nothing until you resume it."),
            confirmGcLines(bulkBarState(listOf(job(1, "queued", type = "gc")))),
        )
        assertEquals(
            listOf(
                "This includes 2 queued garbage-collection runs: they are held too and delete nothing until you resume them.",
                "The garbage-collection run that already started finishes: a running one cannot be paused (cancel it instead).",
            ),
            confirmGcLines(
                bulkBarState(listOf(job(1, "queued", type = "gc"), job(2, "queued", type = "gc"), job(3, "running", type = "gc"))),
            ),
        )
    }

    @Test
    fun `bulk pause and resume mark gc results so the toast names them`() = runTest {
        val targets = bulkPauseTargets(listOf(job(1, "queued", type = "gc"), job(2, "queued"), job(3, "running")))
        val paused = runBulkPause(targets, { })
        assertEquals(listOf(1), paused.filter { it.gc }.map { it.jobId })
        assertEquals("Paused 3 downloads (incl. 1 garbage-collection run)", bulkSummary(BulkKind.PAUSE, paused).text)
        val resumed = runBulkResume(listOf(job(4, "paused", type = "gc"), job(5, "paused", type = "gc")), { })
        assertEquals("Resumed 2 downloads (incl. 2 garbage-collection runs)", bulkSummary(BulkKind.RESUME, resumed).text)
    }

    @Test
    fun `pause runs the queued phase to completion before the running phase`() = runTest {
        val order = mutableListOf<Int>()
        val targets = BulkPauseTargets(listOf(job(1, "queued"), job(2, "queued")), listOf(job(3, "running")))
        val results = runBulkPause(targets, { id -> order += id }, limit = 1)
        assertEquals(listOf(1, 2, 3), order)
        assertTrue(results.all { it.outcome == BulkOutcome.OK })
    }

    @Test
    fun `404 and 409 are skipped, other errors fail with their reason`() = runTest {
        val results = runWithLimit(listOf(1, 2, 3, 4), 4) { id ->
            when (id) {
                1 -> throw VaultApiError.NotFound("gone", 404)
                2 -> throw VaultApiError.Validation("finished", 409)
                3 -> throw VaultApiError.Server("boom", 500, detail = "disk full")
                else -> Unit
            }
        }
        assertEquals(
            listOf(BulkOutcome.SKIPPED, BulkOutcome.SKIPPED, BulkOutcome.FAILED, BulkOutcome.OK),
            results.map { it.outcome },
        )
        assertEquals("disk full", results[2].reason)
    }

    @Test
    fun `summary wording matches the web literals`() {
        fun ok(n: Int) = (1..n).map { BulkResult(it, BulkOutcome.OK) }
        assertEquals("Paused 1 download", bulkSummary(BulkKind.PAUSE, ok(1)).text)
        assertEquals("Resumed 3 downloads", bulkSummary(BulkKind.RESUME, ok(3)).text)
        val mixed = ok(1) + BulkResult(2, BulkOutcome.FAILED, "disk full") + BulkResult(3, BulkOutcome.SKIPPED)
        val s = bulkSummary(BulkKind.PAUSE, mixed)
        assertEquals(
            "Paused 1 of 3 — 1 could not be paused: disk full; 1 skipped (finished or changed meanwhile)",
            s.text,
        )
        assertTrue(s.warn)
        // Only SUCCESSFUL gc results count toward the suffix.
        val gcMixed = listOf(
            BulkResult(1, BulkOutcome.OK, gc = true),
            BulkResult(2, BulkOutcome.SKIPPED, gc = true),
            BulkResult(3, BulkOutcome.OK),
        )
        assertEquals(
            "Paused 2 of 3 (incl. 1 garbage-collection run) — 1 skipped (finished or changed meanwhile)",
            bulkSummary(BulkKind.PAUSE, gcMixed).text,
        )
        assertEquals("Pause 1 download", pauseAllLabel(1))
        assertEquals("Resume all 2 downloads", resumeAllLabel(2))
        assertEquals("Pause 4 downloads?", pauseConfirmTitle(4))
    }
}
