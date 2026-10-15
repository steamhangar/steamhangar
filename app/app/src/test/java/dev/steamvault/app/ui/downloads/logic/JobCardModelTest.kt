package dev.steamvault.app.ui.downloads.logic

import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.ui.status.StatusKind
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotSame
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * See `JobCardModel.kt`'s kdoc for the full mechanism this pins -- same
 * class of proof `ui/library/logic/GameCardModelTest.kt` establishes for
 * `GameCardModel`: [buildJobCardModel] must be a pure function that
 * produces an `equals()`-equal (not merely reference-equal) result for two
 * distinct-instance-but-functionally-identical `JobSummary` values, AND the
 * WP brief's sharper claim -- a `stop_request`-only diff between two
 * otherwise-identical running jobs must change [JobCardModel.action] and
 * NOTHING else.
 */
class JobCardModelTest {

    private val games = mapOf(42 to GameSummary(appid = 42, name = "Nebula Drift", status = "done", depot_count = 1))

    private fun runningJob(stopRequest: String? = null) = JobSummary(
        id = 7,
        appid = 42,
        type = "prefill",
        status = "running",
        created_at = "2026-08-01T00:00:00Z",
        started_at = "2026-08-01T00:00:05Z",
        stop_request = stopRequest,
    )

    @Test
    fun `two ticks of a genuinely-unchanged running job produce an EQUAL model from DISTINCT instances`() {
        val tick1 = buildJobCardModel(runningJob(), games, JobCardMode.ACTIVE)
        val tick2 = buildJobCardModel(runningJob(), games, JobCardMode.ACTIVE)
        assertNotSame(tick1, tick2)
        assertEquals(tick1, tick2)
        assertEquals(StatusKind.RUNNING, tick1.kind)
    }

    // -----------------------------------------------------------------
    // The WP brief's sharpest stability claim: a stop_request-only diff on
    // an otherwise-unchanged running job produces a model differing ONLY in
    // `action`.
    // -----------------------------------------------------------------
    @Test
    fun `a stop_request-only diff changes ONLY the action field`() {
        val before = buildJobCardModel(runningJob(stopRequest = null), games, JobCardMode.ACTIVE)
        val after = buildJobCardModel(runningJob(stopRequest = "pause"), games, JobCardMode.ACTIVE)

        assertTrue(before != after) // MUTATION TARGET: a real diff must be observed at all
        assertEquals(before.copy(action = after.action), after) // ... but nowhere else.
        assertEquals(before.kind, after.kind) // the icon's driving field is untouched
        assertEquals(before.jobId, after.jobId)
        assertEquals(before.appid, after.appid)
        assertEquals(before.name, after.name)
        assertEquals(before.statusWord, after.statusWord)
        assertEquals(before.mode, after.mode)
    }

    @Test
    fun `stop_request cancel sets cancelling and disables cancel, leaves pause untouched`() {
        val model = buildJobCardModel(runningJob(stopRequest = "cancel"), games, JobCardMode.ACTIVE)
        assertTrue(model.action.cancelling)
        assertFalse(model.action.cancelEnabled)
        assertFalse(model.action.pausing)
    }

    @Test
    fun `stop_request pause sets pausing and disables pause, leaves cancel enabled`() {
        val model = buildJobCardModel(runningJob(stopRequest = "pause"), games, JobCardMode.ACTIVE)
        assertTrue(model.action.pausing)
        assertFalse(model.action.pauseEnabled)
        assertTrue(model.action.cancelEnabled)
        assertFalse(model.action.cancelling)
    }

    // -----------------------------------------------------------------
    // Pause only offered for prefill jobs -- GC pause is 409 (mirrors
    // web/js/views/downloads.js::paintJobActions's `job.type === "prefill"`
    // gate).
    // -----------------------------------------------------------------
    @Test
    fun `a running GC job never offers pause (409 gating, mutation target)`() {
        val gcJob = runningJob().copy(type = "gc")
        val model = buildJobCardModel(gcJob, games, JobCardMode.ACTIVE)
        assertFalse(model.action.showPause)
        assertFalse(model.action.pauseEnabled)
        assertTrue(model.action.showCancel) // GC can still be cancelled
    }

    @Test
    fun `a running prefill job offers pause`() {
        val model = buildJobCardModel(runningJob(), games, JobCardMode.ACTIVE)
        assertTrue(model.action.showPause)
        assertTrue(model.action.pauseEnabled)
    }

    @Test
    fun `a paused job offers resume and cancel, never pause`() {
        val pausedJob = runningJob().copy(status = "paused")
        val model = buildJobCardModel(pausedJob, games, JobCardMode.HELD)
        assertTrue(model.action.showResume)
        assertTrue(model.action.showCancel)
        assertTrue(model.action.cancelEnabled)
        assertFalse(model.action.showPause)
        assertEquals(JobCardMode.HELD, model.mode)
        assertEquals(StatusKind.PAUSED, model.kind)
    }

    @Test
    fun `a paused job's action never reports pausing or cancelling from a stale running-only field`() {
        // stop_request only means anything while status == running (api/README.md
        // "Job control"); a paused row carrying a leftover stop_request must
        // not be misread as still-transitioning.
        val pausedJob = runningJob(stopRequest = "pause").copy(status = "paused")
        val model = buildJobCardModel(pausedJob, games, JobCardMode.HELD)
        assertFalse(model.action.pausing)
        assertFalse(model.action.cancelling)
    }

    @Test
    fun `name falls back to a stable placeholder when the app hasn't landed on a games poll yet`() {
        val model = buildJobCardModel(runningJob().copy(appid = 999), emptyMap(), JobCardMode.ACTIVE)
        assertEquals("Unknown app", model.name)
    }

    @Test
    fun `cancelled history kind is distinct from error (mutation target, cross-check with jobIconKind)`() {
        val cancelled = buildHistoryRowModel(
            JobSummary(id = 1, appid = 42, type = "prefill", status = "cancelled", created_at = "2026-08-01T00:00:00Z"),
            games,
        )
        val error = buildHistoryRowModel(
            JobSummary(id = 2, appid = 42, type = "prefill", status = "error", created_at = "2026-08-01T00:00:00Z"),
            games,
        )
        assertTrue(cancelled.kind != error.kind)
        assertEquals(StatusKind.CANCELLED, cancelled.kind)
        assertEquals(StatusKind.ERROR, error.kind)
    }

    @Test
    fun `buildQueueRowModel carries the given 1-based position and resolved name`() {
        val job = JobSummary(id = 5, appid = 42, type = "prefill", status = "queued", created_at = "2026-08-01T00:00:00Z")
        val model = buildQueueRowModel(job, position = 3, gamesByAppid = games)
        assertEquals(3, model.position)
        assertEquals("Nebula Drift", model.name)
        assertEquals(5, model.jobId)
    }
}
