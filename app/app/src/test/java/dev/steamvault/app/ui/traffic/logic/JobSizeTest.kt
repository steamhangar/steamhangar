package dev.steamvault.app.ui.traffic.logic

import dev.steamvault.app.demo.DemoTrafficSnapshot
import dev.steamvault.app.demo.demoThroughput
import dev.steamvault.app.net.VaultJson
import dev.steamvault.app.net.model.EventFeedOut
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.net.model.JobThroughputOut
import dev.steamvault.app.net.model.SeriesOut
import dev.steamvault.app.net.model.ThroughputOut
import dev.steamvault.app.ui.downloads.StatsAnswer
import dev.steamvault.app.ui.downloads.jobSpeedLines
import dev.steamvault.app.ui.traffic.TrafficPoll
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

/**
 * WP ANDROID-RC21-1-TWINS: the JOB-SIZE-1 port in `TrafficView.kt`,
 * mirroring `web/tests/job-size-1.test.js` case by case: a first
 * download's job card shows speed, "of ~X" from the D19 depot preview
 * (marked as an expected size) and, with a remainder known, an approximate
 * ETA (`remaining_bytes / eta_bytes_per_second`, EMA-smoothed), with the
 * same hide rules; an older server keeps today's text.
 */
class JobSizeTest {

    private val mib = 1024.0 * 1024.0
    private val gib = 1024L * 1024L * 1024L
    private val now = Instant.parse("2026-11-01T12:00:00Z").toEpochMilli()
    private val started = "2026-11-01T11:50:00Z" // ten minutes before now

    /** A first download as `GET /v1/throughput` reports it with the preview ready. */
    private fun firstDownload() = JobThroughputOut(
        job_id = 254,
        appid = 264710,
        started_at = started,
        bytes_per_second = 10 * mib,
        bytes_since_start = 2 * gib,
        exclusive_depots = 0,
        mapped_depots = 0,
        expected = true,
        expected_depots = 2,
        expected_size_bytes = 12 * gib,
        expected_download_bytes = 8 * gib,
        miss_bytes_per_second = 10 * mib,
        eta_bytes_per_second = 10 * mib,
        eta_speed_source = "live",
        remaining_bytes = 6 * gib,
        remaining_source = "preview",
    )

    /** The pre-JOB-SIZE-1 entry for the same job (preview off / failed / older server). */
    private val unknownDepots = JobThroughputOut(job_id = 254, appid = 264710, started_at = started, bytes_per_second = null, bytes_since_start = null, exclusive_depots = 0, mapped_depots = 0)

    private val notMeasured = "Speed not measured yet: the server does not know this game's depots yet."

    @Test
    fun `MUTATION TARGET -- preview present -- speed, so far of ~expected download and the expected-size mark`() {
        val text = jobSpeedText(firstDownload().copy(remaining_bytes = null), FeedState.OK, nowMs = now)
        assertEquals("10.0 MB/s · 2.0 GB of ~8.0 GB loaded so far (expected size)", text)
        assertEquals(" (expected size)", EXPECTED_SIZE_NOTE)
    }

    @Test
    fun `MUTATION TARGET -- preview absent or an older server keeps today's honest text`() {
        assertEquals(notMeasured, jobSpeedText(unknownDepots, FeedState.OK, nowMs = now))
        val off = unknownDepots.copy(expected = false, expected_depots = 0)
        assertEquals(notMeasured, jobSpeedText(off, FeedState.OK, nowMs = now))
        // No "of ~X" without expected == true, even if a size were present.
        val mapped = firstDownload().copy(expected = false, remaining_bytes = null)
        assertEquals("10.0 MB/s · 2.0 GB loaded so far", jobSpeedText(mapped, FeedState.OK, nowMs = now))
        // An expected entry without a download size: no "of", no mark.
        assertEquals(
            "10.0 MB/s · 2.0 GB loaded so far",
            jobSpeedText(firstDownload().copy(expected_download_bytes = null, remaining_bytes = null), FeedState.OK, nowMs = now),
        )
        // The real decode of an older server's entry: every new field null.
        val old = VaultJson.decodeFromString(
            JobThroughputOut.serializer(),
            """{"job_id":7,"appid":10,"name":"G","started_at":"2026-11-01T11:50:00Z","bytes_per_second":1048576.0,"bytes_since_start":2147483648,"exclusive_depots":1,"mapped_depots":1}""",
        )
        assertNull(old.expected)
        assertNull(old.remaining_bytes)
        assertNull(old.eta_bytes_per_second)
        assertEquals("1.0 MB/s · 2.0 GB loaded so far", jobSpeedText(old, FeedState.OK, nowMs = now))
        // And the new server's body decodes every field.
        val fresh = VaultJson.decodeFromString(
            JobThroughputOut.serializer(),
            """{"job_id":7,"appid":10,"name":null,"started_at":"2026-11-01T11:50:00Z","bytes_per_second":10485760.0,"bytes_since_start":2147483648,"exclusive_depots":0,"mapped_depots":0,"expected":true,"expected_depots":2,"expected_size_bytes":12884901888,"expected_download_bytes":8589934592,"miss_bytes_per_second":10485760.0,"remaining_bytes":6442450944,"remaining_source":"preview","eta_bytes_per_second":10485760.0,"eta_speed_source":"live"}""",
        )
        assertEquals(firstDownload().copy(job_id = 7, appid = 10), fresh)
    }

    @Test
    fun `MUTATION TARGET -- the speed line needs an ok feed also with a preview`() {
        assertNull(jobSpeedText(firstDownload(), FeedState.STALE, nowMs = now))
    }

    @Test
    fun `MUTATION TARGET -- ETA formula -- remaining over the server's ETA speed (or the smoothed one)`() {
        assertEquals((6.0 * gib) / (10 * mib), jobEtaSeconds(firstDownload(), nowMs = now)!!, 0.0)
        // The server's divisor, not the card's total or MISS speed.
        val other = firstDownload().copy(bytes_per_second = 100 * mib, miss_bytes_per_second = 1 * mib, eta_bytes_per_second = 5 * mib)
        assertEquals((6.0 * gib) / (5 * mib), jobEtaSeconds(other, nowMs = now)!!, 0.0)
        // A smoothed rate passed by the screen wins over the entry's raw sample.
        assertEquals((6.0 * gib) / (20 * mib), jobEtaSeconds(firstDownload(), nowMs = now, rate = 20 * mib)!!, 0.0)
        assertNull("smoother has no rate yet", jobEtaSeconds(firstDownload(), nowMs = now, rate = null))
        assertEquals(
            "10.0 MB/s · 2.0 GB of ~8.0 GB loaded so far (expected size) · about 11 min left",
            jobSpeedText(firstDownload(), FeedState.OK, nowMs = now),
        )
        // A mapped game with a completeness-based remainder gets an ETA too.
        val mapped = firstDownload().copy(expected = false, remaining_source = "completeness", remaining_bytes = 3 * gib)
        assertEquals("10.0 MB/s · 2.0 GB loaded so far · about 6 min left", jobSpeedText(mapped, FeedState.OK, nowMs = now))
        // The line's etaRate null (the smoother has none) drops only the ETA.
        assertEquals(
            "10.0 MB/s · 2.0 GB of ~8.0 GB loaded so far (expected size)",
            jobSpeedText(firstDownload(), FeedState.OK, nowMs = now, etaRate = null),
        )
    }

    @Test
    fun `MUTATION TARGET -- ETA hide rules`() {
        assertNull(jobEtaSeconds(null, nowMs = now))
        assertNull("size unknown", jobEtaSeconds(firstDownload().copy(remaining_bytes = null), nowMs = now))
        assertNull("nothing left", jobEtaSeconds(firstDownload().copy(remaining_bytes = 0), nowMs = now))
        assertNull("no speed", jobEtaSeconds(firstDownload().copy(eta_bytes_per_second = 0.0), nowMs = now))
        assertNull("speed unknown", jobEtaSeconds(firstDownload().copy(eta_bytes_per_second = null), nowMs = now))
        assertNull("start unknown", jobEtaSeconds(firstDownload().copy(started_at = null), nowMs = now))
        assertNull("start unparseable", jobEtaSeconds(firstDownload().copy(started_at = "yesterday"), nowMs = now))
        // Unstable first seconds.
        val young = firstDownload().copy(started_at = Instant.ofEpochMilli(now - JOB_ETA_MIN_AGE_MS + 1000).toString())
        assertNull("younger than 20 s", jobEtaSeconds(young, nowMs = now))
        val settled = firstDownload().copy(started_at = Instant.ofEpochMilli(now - JOB_ETA_MIN_AGE_MS).toString())
        assertNotNull("20 s and older", jobEtaSeconds(settled, nowMs = now))
        // More than a day.
        assertEquals(86_400.0, JOB_ETA_MAX_SECONDS, 0.0)
        val tooLong = firstDownload().copy(eta_bytes_per_second = 1.0, remaining_bytes = 86_401)
        assertNull("> 24 h", jobEtaSeconds(tooLong, nowMs = now))
        val day = firstDownload().copy(eta_bytes_per_second = 1.0, remaining_bytes = 86_400)
        assertEquals("exactly 24 h", 86_400.0, jobEtaSeconds(day, nowMs = now)!!, 0.0)
        // The line drops only the ETA part.
        assertEquals(
            "10.0 MB/s · 2.0 GB of ~8.0 GB loaded so far (expected size)",
            jobSpeedText(firstDownload().copy(eta_bytes_per_second = 0.0), FeedState.OK, nowMs = now),
        )
    }

    @Test
    fun `MUTATION TARGET -- ETA never on a Verifying card`() {
        assertNull(jobEtaSeconds(firstDownload(), verifying = true, nowMs = now))
        val mapped = firstDownload().copy(expected = false, remaining_bytes = 3 * gib)
        assertEquals("10.0 MB/s · 2.0 GB loaded so far", jobSpeedText(mapped, FeedState.OK, verifying = true, nowMs = now))
    }

    @Test
    fun `MUTATION TARGET -- ETA wording is approximate and rounds up to minutes`() {
        assertEquals("about 1 min left", formatEta(1.0))
        assertEquals("about 1 min left", formatEta(60.0))
        assertEquals("about 2 min left", formatEta(61.0))
        assertEquals("about 6 min left", formatEta(359.0))
        assertEquals("about 1 h left", formatEta(3600.0))
        assertEquals("about 1 h 5 min left", formatEta(3600.0 + 5 * 60))
        assertNull(formatEta(0.0))
        assertNull(formatEta(null))
        assertNull(formatEta(Double.NaN))
        assertNull(formatEta(Double.POSITIVE_INFINITY))
    }

    @Test
    fun `the note says where an expected line's figures come from`() {
        assertEquals(JOB_SPEED_EXPECTED_NOTE, jobSpeedNote(firstDownload()))
        assertEquals(JOB_SPEED_NOTE, jobSpeedNote(firstDownload().copy(expected = false)))
        assertEquals(JOB_SPEED_NOTE, jobSpeedNote(firstDownload().copy(expected = null)))
        assertEquals(JOB_SPEED_NOTE, jobSpeedNote(null))
        assertTrue(JOB_SPEED_EXPECTED_NOTE.contains("estimate"))
        // WP RUNNER-NAME-1: the runner is named by the product here too.
        assertTrue(JOB_SPEED_EXPECTED_NOTE.contains("count SteamHangar's own prefill bytes"))
    }

    @Test
    fun `the demo keeps the old (mapped) job entry, so no golden changes`() {
        val snapshot = DemoTrafficSnapshot(
            runningJobs = listOf(JobSummary(id = 1, appid = 440, type = "prefill", status = "running", created_at = started, started_at = started)),
            games = emptyList(),
            agents = emptyList(),
        )
        val job = demoThroughput("24h", snapshot, now).jobs.single()
        assertEquals(false, job.expected)
        assertNull(job.remaining_bytes)
        assertEquals("31.5 MB/s · 8.8 GB loaded so far", jobSpeedText(job, FeedState.OK, nowMs = now))
    }

    @Test
    fun `MUTATION TARGET -- EMA -- one step per new answer, reset on no speed, forget gone jobs`() {
        assertEquals(0.3, ETA_SMOOTHING, 0.0)
        assertEquals(100.0, smoothEtaRate(null, 100.0)!!, 0.0)
        assertEquals(130.0, smoothEtaRate(100.0, 200.0)!!, 1e-9)
        assertNull(smoothEtaRate(100.0, 0.0))
        assertNull(smoothEtaRate(100.0, null))
        val smoother = EtaSmoother()
        fun answer(rate: Double?, id: Int = 1) = ThroughputOut(range = "live", jobs = listOf(JobThroughputOut(job_id = id, appid = 1, eta_bytes_per_second = rate)))
        val a = answer(100.0)
        smoother.observe(a)
        assertEquals(100.0, smoother.rate(1)!!, 0.0)
        smoother.observe(a) // the same answer recomposed: no second step
        assertEquals(100.0, smoother.rate(1)!!, 0.0)
        // The live range re-merges the same cached jobs into a new answer: no step either.
        smoother.observe(a.copy(event_feed = EventFeedOut()))
        assertEquals(100.0, smoother.rate(1)!!, 0.0)
        smoother.observe(answer(200.0))
        assertEquals(130.0, smoother.rate(1)!!, 1e-9)
        smoother.observe(answer(300.0))
        assertEquals(130.0 + 0.3 * 170.0, smoother.rate(1)!!, 1e-9)
        smoother.observe(answer(0.0))
        assertNull(smoother.rate(1))
        smoother.observe(answer(50.0, id = 2))
        assertNull(smoother.rate(1))
        assertEquals(50.0, smoother.rate(2)!!, 0.0)
        // Screen entry: nothing kept (the web nit, done right on Android).
        smoother.clear()
        assertNull(smoother.rate(2))
        smoother.observe(answer(80.0, id = 2))
        assertEquals("a fresh start, not an EMA step from 50", 80.0, smoother.rate(2)!!, 0.0)
    }

    @Test
    fun `MUTATION TARGET -- jobSpeedLines feeds the smoothed rate, the verify set and the note`() = runTest {
        val entry = firstDownload()
        val throughput = ThroughputOut(event_feed = EventFeedOut(enabled = true, state = "ok"), range = "24h", jobs = listOf(entry))
        val poll = TrafficPoll(
            initialRange = "24h",
            fetch = { StatsAnswer(throughput, SeriesOut(range = "24h")) },
            hasFigures = { true },
            isValidRange = { true },
        )
        poll.refreshOnce()
        assertNotNull(poll.data)
        val smoother = EtaSmoother()
        val line = jobSpeedLines(poll, emptySet(), smoother, now).getValue(254)
        assertTrue(line.text, line.text.endsWith(" · about 11 min left"))
        assertEquals(JOB_SPEED_EXPECTED_NOTE, line.note)
        assertEquals("one step taken", 10 * mib, smoother.rate(254)!!, 0.0)
        val verify = jobSpeedLines(poll, setOf(254), smoother, now).getValue(254)
        assertEquals("10.0 MB/s · 2.0 GB of ~8.0 GB loaded so far (expected size)", verify.text)
        // A cleared smoother (screen entry) re-seeds from the same answer.
        smoother.clear()
        jobSpeedLines(poll, emptySet(), smoother, now)
        assertEquals(10 * mib, smoother.rate(254)!!, 0.0)
    }
}
