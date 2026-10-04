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
    fun `pause targets are queued then running prefill jobs, oldest id first, no gc, no stop_request`() {
        val jobs = listOf(
            job(9, "queued"), job(3, "queued"), job(5, "running"),
            job(4, "running", stopRequest = "pause"), job(6, "queued", type = "gc"),
            job(7, "paused"), job(8, "done"),
        )
        val t = bulkPauseTargets(jobs)
        assertEquals(listOf(3, 9), t.queued.map { it.id })
        assertEquals(listOf(5), t.running.map { it.id })
        assertEquals(3, t.count)
    }

    @Test
    fun `resume targets are every paused job, oldest first`() {
        val jobs = listOf(job(8, "paused"), job(2, "paused"), job(3, "queued"))
        assertEquals(listOf(2, 8), bulkResumeTargets(jobs).map { it.id })
    }

    @Test
    fun `bar state shows the buttons separately and flags an active gc run`() {
        val s = bulkBarState(listOf(job(1, "running"), job(2, "queued", type = "gc")))
        assertTrue(s.pauseVisible)
        assertFalse(s.resumeVisible)
        assertTrue(s.gcActive)
        assertFalse(bulkBarState(listOf(job(1, "done"))).visible)
        assertTrue(bulkBarState(listOf(job(1, "paused"))).resumeVisible)
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
        assertEquals("Pause 1 download", pauseAllLabel(1))
        assertEquals("Resume all 2 downloads", resumeAllLabel(2))
        assertEquals("Pause 4 downloads?", pauseConfirmTitle(4))
    }
}
