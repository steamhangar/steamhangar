package dev.steamvault.app.ui.traffic.logic

import dev.steamvault.app.net.model.EventFeedOut
import dev.steamvault.app.net.model.JobThroughputOut
import dev.steamvault.app.net.model.SeriesOut
import dev.steamvault.app.net.model.ThroughputOut
import dev.steamvault.app.ui.downloads.StatsAnswer
import dev.steamvault.app.ui.downloads.jobSpeedLines
import dev.steamvault.app.ui.traffic.TrafficPoll
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * WP ANDROID-VERIFY-SPEED-1: the web's Verifying speed line (WP
 * DOWNLOADS-1, `web/tests` VERIFY-SPEED-TEXT-1) on Android. A Verifying
 * card without a measured speed says [VERIFY_SPEED_TEXT], with or without
 * statistics; a measured speed still wins; non-Verifying cards keep their
 * old lines. The web side of the string is pinned in `JobSizeWebTwinTest`.
 */
class VerifySpeedTest {

    private val mib = 1024.0 * 1024.0
    private val gib = 1024L * 1024L * 1024L

    private val unmeasured = JobThroughputOut(
        job_id = 7,
        appid = 440,
        started_at = "2026-11-01T11:50:00Z",
        bytes_per_second = null,
        bytes_since_start = null,
        exclusive_depots = 0,
        mapped_depots = 2,
    )

    /** A poll whose fetch answers [throughput]. `poll(null)` stands for "no
     * answer yet": the test never calls `refreshOnce()` on it, so the
     * `throughput!!` below is never evaluated for null. */
    private fun poll(throughput: ThroughputOut?, range: String = "24h") = TrafficPoll(
        initialRange = range,
        fetch = { StatsAnswer(throughput!!, SeriesOut(range = it)) },
        hasFigures = { true },
        isValidRange = { true },
    )

    @Test
    fun `the decided string, literally`() {
        assertEquals("Verifying files, no download speed", VERIFY_SPEED_TEXT)
    }

    @Test
    fun `MUTATION TARGET -- a Verifying card without a measured speed says so`() {
        // No entry at all (job not listed, no answer).
        assertEquals(VERIFY_SPEED_TEXT, jobSpeedText(null, FeedState.OK, verifying = true))
        assertEquals(VERIFY_SPEED_TEXT, jobSpeedText(null, FeedState.DISABLED, verifying = true))
        // Feed not ok: no honest speed.
        assertEquals(VERIFY_SPEED_TEXT, jobSpeedText(unmeasured.copy(bytes_per_second = 10 * mib), FeedState.STALE, verifying = true))
        assertEquals(VERIFY_SPEED_TEXT, jobSpeedText(unmeasured.copy(bytes_per_second = 10 * mib), FeedState.NEVER_SWEPT, verifying = true))
        // Listed, ok, but no speed measured: never "Speed not measured ..." on a Verify run.
        assertEquals(VERIFY_SPEED_TEXT, jobSpeedText(unmeasured, FeedState.OK, verifying = true))
        assertEquals(VERIFY_SPEED_TEXT, jobSpeedText(unmeasured.copy(mapped_depots = 0), FeedState.OK, verifying = true))
        assertEquals(VERIFY_SPEED_TEXT, jobSpeedText(unmeasured.copy(exclusive_depots = 3), FeedState.OK, verifying = true))
    }

    @Test
    fun `a measured speed still wins on a Verifying card, other cards keep their lines`() {
        val measured = unmeasured.copy(bytes_per_second = 10 * mib, bytes_since_start = 2 * gib)
        assertEquals("10.0 MB/s · 2.0 GB loaded so far", jobSpeedText(measured, FeedState.OK, verifying = true))
        assertNull(jobSpeedText(null, FeedState.OK))
        assertNull(jobSpeedText(measured, FeedState.STALE))
        assertEquals(
            "Speed not measured: this game's depots are shared with other apps.",
            jobSpeedText(unmeasured, FeedState.OK),
        )
    }

    @Test
    fun `MUTATION TARGET -- jobSpeedLines gives every Verifying card a line, with or without statistics`() = runTest {
        // No answer yet.
        val empty = poll(null)
        val none = jobSpeedLines(empty, setOf(7, 8))
        assertEquals(setOf(7, 8), none.keys)
        assertEquals(VERIFY_SPEED_TEXT, none.getValue(7).text)
        assertEquals(JOB_SPEED_NOTE, none.getValue(7).note)
        assertTrue("no Verifying card, no line", jobSpeedLines(empty, emptySet()).isEmpty())

        // An answer that lists job 7 unmeasured and job 9 measured, not job 8.
        val measured = unmeasured.copy(job_id = 9, bytes_per_second = 10 * mib, bytes_since_start = 2 * gib)
        val t = ThroughputOut(event_feed = EventFeedOut(enabled = true, state = "ok"), range = "24h", jobs = listOf(unmeasured, measured))
        val p = poll(t)
        p.refreshOnce()
        val lines = jobSpeedLines(p, setOf(7, 8, 9))
        assertEquals(VERIFY_SPEED_TEXT, lines.getValue(7).text)
        assertEquals(VERIFY_SPEED_TEXT, lines.getValue(8).text)
        assertEquals("10.0 MB/s · 2.0 GB loaded so far", lines.getValue(9).text)
        // Not Verifying: the old lines, nothing for an unlisted job.
        val plain = jobSpeedLines(p, emptySet())
        assertEquals("Speed not measured: this game's depots are shared with other apps.", plain.getValue(7).text)
        assertEquals(setOf(7, 9), plain.keys)

        // An answer for another range counts as no statistics.
        val other = poll(t.copy(range = "7d"))
        other.refreshOnce()
        val stale = jobSpeedLines(other, setOf(9))
        assertEquals(VERIFY_SPEED_TEXT, stale.getValue(9).text)
        assertEquals(setOf(9), stale.keys)
    }
}
