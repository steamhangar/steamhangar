package dev.steamvault.app.ui.downloads.logic

import dev.steamvault.app.net.model.JobSummary
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Mirrors `web/tests/job-partition.test.js`'s cases against
 * `web/js/lib/job-partition.js` one for one, onto the Kotlin port in
 * `JobPartition.kt`, plus this port's own improvement over the web
 * behaviour (unknown status routed to history instead of silently dropped
 * -- see that file's kdoc).
 */
class JobPartitionTest {

    private fun job(
        id: Int,
        status: String,
        appid: Int = 1000 + id,
        type: String = "prefill",
        stopRequest: String? = null,
    ) = JobSummary(
        id = id,
        appid = appid,
        type = type,
        status = status,
        created_at = "2026-08-01T00:00:00Z",
        stop_request = stopRequest,
    )

    @Test
    fun `partitionJobs buckets by status, ignoring input order`() {
        val jobs = listOf(job(5, "done"), job(1, "queued"), job(2, "running"), job(3, "error"))
        val p = partitionJobs(jobs)
        assertEquals(listOf(2), p.running.map { it.id })
        assertEquals(listOf(1), p.queued.map { it.id })
        assertEquals(listOf(3, 5), p.history.map { it.id }.sorted())
    }

    @Test
    fun `partitionJobs empty input yields all-empty buckets`() {
        val p = partitionJobs(emptyList())
        assertTrue(p.running.isEmpty())
        assertTrue(p.paused.isEmpty())
        assertTrue(p.queued.isEmpty())
        assertTrue(p.history.isEmpty())
    }

    @Test
    fun `partitionJobs queued is sorted FIFO (oldest job id first), not snapshot order`() {
        val jobs = listOf(job(30, "queued"), job(10, "queued"), job(20, "queued"))
        val p = partitionJobs(jobs)
        assertEquals(listOf(10, 20, 30), p.queued.map { it.id })
    }

    @Test
    fun `partitionJobs history keeps the snapshot's own order`() {
        val jobs = listOf(job(3, "error"), job(1, "done"), job(2, "cancelled"))
        val p = partitionJobs(jobs)
        assertEquals(listOf(3, 1, 2), p.history.map { it.id })
    }

    // -----------------------------------------------------------------
    // The slot-release divergence: running and paused are INDEPENDENT
    // buckets, not a single mutually-exclusive "active slot".
    // -----------------------------------------------------------------
    @Test
    fun `partitionJobs a paused job and a DIFFERENT app's running job coexist in separate buckets`() {
        val jobs = listOf(job(1, "paused", appid = 440), job(2, "running", appid = 730))
        val p = partitionJobs(jobs)
        assertEquals(listOf(2), p.running.map { it.id })
        assertEquals(listOf(1), p.paused.map { it.id })
        assertFalse(p.running.any { it.id == 1 })
        assertFalse(p.paused.any { it.id == 2 })
    }

    // -----------------------------------------------------------------
    // Improvement over the web port: unknown status is routed to history,
    // never silently dropped (WP 4a.5 review nit, addressed here).
    // -----------------------------------------------------------------
    @Test
    fun `partitionJobs routes an unrecognized status into history instead of dropping it`() {
        val jobs = listOf(job(1, "running"), job(2, "frobnicated"))
        val p = partitionJobs(jobs)
        assertEquals(listOf(1), p.running.map { it.id })
        assertTrue(p.paused.isEmpty())
        assertTrue(p.queued.isEmpty())
        assertEquals(listOf(2), p.history.map { it.id }) // MUTATION TARGET: must not vanish
    }

    @Test
    fun `countPending does not count an unrecognized status as pending`() {
        assertEquals(0, countPending(listOf(job(1, "frobnicated"))))
    }

    @Test
    fun `countPending counts queued + running + paused, not done or error or cancelled`() {
        val jobs = listOf(
            job(1, "queued"),
            job(2, "running"),
            job(3, "paused"),
            job(4, "done"),
            job(5, "error"),
            job(6, "cancelled"),
        )
        assertEquals(3, countPending(jobs))
    }

    @Test
    fun `countPending a paused-only snapshot counts 1 (mutation target -- paused must count)`() {
        assertEquals(1, countPending(listOf(job(1, "paused"))))
    }

    @Test
    fun `countPending zero for an empty snapshot`() {
        assertEquals(0, countPending(emptyList()))
    }

    @Test
    fun `queuePosition 1-based position within the FIFO-sorted queue`() {
        val jobs = listOf(job(30, "queued"), job(10, "queued"), job(20, "queued"))
        val queued = partitionJobs(jobs).queued
        assertEquals(1, queuePosition(queued, 10))
        assertEquals(2, queuePosition(queued, 20))
        assertEquals(3, queuePosition(queued, 30))
    }

    @Test
    fun `queuePosition null for a job id not present`() {
        assertNull(queuePosition(listOf(job(1, "queued")), 999))
    }

    @Test
    fun `jobIconKind maps every real status to a status-icon wire name`() {
        assertEquals("running", jobIconKind(job(1, "running")))
        assertEquals("paused", jobIconKind(job(1, "paused")))
        assertEquals("cached", jobIconKind(job(1, "done")))
        assertEquals("error", jobIconKind(job(1, "error")))
        assertEquals("cancelled", jobIconKind(job(1, "cancelled")))
        assertEquals("none", jobIconKind(job(1, "queued")))
    }

    @Test
    fun `jobIconKind cancelled is distinct from error (mutation target)`() {
        val cancelled = jobIconKind(job(1, "cancelled"))
        val error = jobIconKind(job(1, "error"))
        assertTrue(cancelled != error)
        assertEquals("cancelled", cancelled)
    }

    @Test
    fun `jobIconKind falls back to none for an unrecognized status`() {
        assertEquals("none", jobIconKind(job(1, "frobnicated")))
    }

    @Test
    fun `jobStatusWord prefill wording`() {
        assertEquals("Downloading", jobStatusWord(job(1, "running")))
        assertEquals("Paused", jobStatusWord(job(1, "paused")))
        assertEquals("Done", jobStatusWord(job(1, "done")))
        assertEquals("Failed", jobStatusWord(job(1, "error")))
        assertEquals("Cancelled", jobStatusWord(job(1, "cancelled")))
    }

    @Test
    fun `jobStatusWord cancelled is worded distinctly from error (job outcome honesty)`() {
        val cancelled = jobStatusWord(job(1, "cancelled"))
        val failed = jobStatusWord(job(1, "error"))
        assertTrue(cancelled != failed)
        assertEquals("Cancelled", cancelled)
    }

    @Test
    fun `jobStatusWord GC jobs get GC-specific wording, never the download vocabulary`() {
        assertEquals("Collecting garbage", jobStatusWord(job(1, "running", type = "gc")))
        assertEquals("Garbage collected", jobStatusWord(job(1, "done", type = "gc")))
        assertEquals("Garbage collection failed", jobStatusWord(job(1, "error", type = "gc")))
        assertEquals("Garbage collection cancelled", jobStatusWord(job(1, "cancelled", type = "gc")))
        // WP ANDROID-PAUSE-GC-1: reachable since "Pause all" parks queued GC jobs.
        assertEquals("Garbage collection paused", jobStatusWord(job(1, "paused", type = "gc")))
    }

    @Test
    fun `jobStatusWord a finished dry-run gc job says nothing was deleted, execute and older servers keep the old word`() {
        val done = job(1, "done", type = "gc")
        assertEquals("GC dry run (nothing deleted)", jobStatusWord(done.copy(gc_execute = false)))
        assertEquals("Garbage collected", jobStatusWord(done.copy(gc_execute = true)))
        assertEquals("Garbage collected", jobStatusWord(done)) // older server: no gc_execute field
        // Only a finished gc job: a running dry run and a prefill job keep their words.
        assertEquals("Collecting garbage", jobStatusWord(job(1, "running", type = "gc").copy(gc_execute = false)))
        assertEquals("Done", jobStatusWord(job(1, "done").copy(gc_execute = false)))
    }

    @Test
    fun `jobStatusWord falls back to the raw status string for an unrecognized status`() {
        assertEquals("frobnicated", jobStatusWord(job(1, "frobnicated")))
    }
}
