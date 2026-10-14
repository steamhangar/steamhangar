package dev.steamvault.app.ui.traffic.logic

import dev.steamvault.app.net.model.CurrentOut
import dev.steamvault.app.net.model.EventFeedOut
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.net.model.JobThroughputOut
import dev.steamvault.app.net.model.RunnerTrafficOut
import dev.steamvault.app.net.model.SeriesOut
import dev.steamvault.app.net.model.SeriesPointOut
import dev.steamvault.app.net.model.SummaryOut
import dev.steamvault.app.net.model.ThroughputOut
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test
import java.time.Instant

/**
 * WP D15d: `TrafficView.kt`, the port of web `lib/traffic-view.js`. Every
 * expected string is a LITERAL transcribed from the web module or its
 * test (web/tests/traffic-view.test.js), never derived from the code under
 * test (docs/LEARNINGS.md, Android: literal-vs-literal contracts).
 */
class TrafficViewTest {
    private val now = Instant.parse("2026-10-14T12:00:00Z").toEpochMilli()
    private val mib = 1024.0 * 1024.0
    private val gib = 1024L * 1024L * 1024L

    private fun feed(state: String, lastSweep: String? = "2026-10-14T11:59:00Z", interval: Int = 5) =
        EventFeedOut(enabled = state != "disabled", state = state, last_sweep_at = lastSweep, sweep_interval_minutes = interval)

    // ---- formatting ----------------------------------------------------

    @Test
    fun `formatSpeed prints a real zero, small rates and binary units`() {
        assertNull(formatSpeed(null))
        assertNull(formatSpeed(-1.0))
        assertNull(formatSpeed(Double.NaN))
        assertEquals("0 MB/s", formatSpeed(0.0))
        assertEquals("< 1 KB/s", formatSpeed(500.0))
        assertEquals("2 KB/s", formatSpeed(2048.0))
        assertEquals("40.0 MB/s", formatSpeed(40 * mib))
        assertEquals("150 MB/s", formatSpeed(150 * mib))
        assertEquals("1.5 GB/s", formatSpeed(1.5 * 1024 * mib))
    }

    @Test
    fun `formatTrafficBytes prints zero as 0 GB and keeps small totals in MB`() {
        assertNull(formatTrafficBytes(null))
        assertNull(formatTrafficBytes(-5))
        assertEquals("0 GB", formatTrafficBytes(0))
        assertEquals("< 1 MB", formatTrafficBytes(1000))
        assertEquals("312 MB", formatTrafficBytes(312L * 1024 * 1024))
        assertEquals("1.2 GB", formatTrafficBytes((1.2 * gib).toLong()))
        assertEquals("120 GB", formatTrafficBytes(120 * gib))
    }

    @Test
    fun `formatRate is a whole percentage capped at 100`() {
        assertNull(formatRate(null))
        assertNull(formatRate(-0.1))
        assertEquals("0%", formatRate(0.0))
        assertEquals("83%", formatRate(0.834))
        assertEquals("100%", formatRate(1.7))
    }

    // ---- feed notes ----------------------------------------------------

    @Test
    fun `feedState treats anything unreadable as disabled`() {
        assertEquals(FeedState.DISABLED, feedState(null))
        assertEquals(FeedState.DISABLED, feedState(feed("weird")))
        assertEquals(FeedState.OK, feedState(feed("ok")))
        assertEquals(FeedState.STALE, feedState(feed("stale")))
        assertEquals(FeedState.NEVER_SWEPT, feedState(feed("never-swept")))
    }

    @Test
    fun `feedNote says why numbers are missing, in the web's words`() {
        assertNull(feedNote(feed("ok"), now))
        assertEquals(
            "Traffic statistics are off: the server does not read the cache's event log. Set VAULT_EVENT_LOG and " +
                "VAULT_EVENT_LOG_PATH in the server's .env to see speeds, hit rate and machines without an agent.",
            feedNote(feed("disabled"), now),
        )
        assertEquals(
            "No traffic data yet. The server reads the cache's event log every 5 min; the first numbers appear after that.",
            feedNote(feed("never-swept", interval = 0), now),
        )
        assertEquals(
            "Traffic data is out of date: the server last read the cache's event log 20 min ago. " +
                "Current values show a dash until it reads it again.",
            feedNote(feed("stale", lastSweep = "2026-10-14T11:40:00Z"), now),
        )
        assertEquals(
            "Traffic data is out of date. Current values show a dash until the server reads the cache's event log again.",
            feedNote(feed("stale", lastSweep = null), now),
        )
    }

    @Test
    fun `pollNote -- old server, first-load error, refresh failure keeps the old data, loading`() {
        assertEquals(
            "This server does not report traffic statistics yet. Update vault-api to see them.",
            pollNote(true, null, null, false, "Could not load clients", null, now),
        )
        assertEquals(
            "Could not load clients: boom",
            pollNote(false, "boom", null, false, "Could not load clients", null, now),
        )
        // An answer is on screen and the refresh failed: the old figures
        // stay, the note says so (and the feed note follows).
        assertEquals("Couldn't refresh: boom", pollNote(false, "boom", feed("ok"), true, "x", null, now))
        assertEquals(
            "Couldn't refresh: boom Traffic data is out of date. Current values show a dash until the server reads the cache's event log again.",
            pollNote(false, "boom", feed("stale", lastSweep = null), true, "x", null, now),
        )
        assertNull(pollNote(false, null, feed("ok"), true, "x", null, now))
        assertEquals("Loading statistics…", pollNote(false, null, null, false, "x", "Loading statistics…", now))
    }

    // ---- header tiles ----------------------------------------------------

    private fun throughput(state: String, range: String = "7d") = ThroughputOut(
        event_feed = feed(state),
        range = range,
        current = if (state == "ok") CurrentOut(bytes_per_second = 40 * mib) else null,
        active_clients_now = if (state == "ok") 2 else null,
        summary = SummaryOut(
            bytes_served = 5 * gib,
            hit_bytes = 4 * gib,
            byte_hit_rate = 0.8,
            average_bytes_per_second = 20 * mib,
            peak_bytes_per_second = 60 * mib,
            active_clients = 3,
        ),
    )

    @Test
    fun `headerTiles -- none before an answer or with the feed disabled`() {
        assertNull(headerTiles(null))
        assertNull(headerTiles(throughput("disabled")))
    }

    @Test
    fun `headerTiles -- seven figures in the web's order and words`() {
        val tiles = headerTiles(throughput("ok"))!!
        assertEquals(
            listOf("Current speed", "Average while loading", "Peak speed", "Loaded", "Hit rate", "Saved by the cache", "Active clients"),
            tiles.map { it.label },
        )
        assertEquals(listOf("40.0 MB/s", "20.0 MB/s", "60.0 MB/s", "5.0 GB", "80%", "4.0 GB", "2"), tiles.map { it.value })
        assertEquals("3 in the last 7 days", tiles.last().detail)
    }

    @Test
    fun `D15-RUNNER -- Loaded names the prefill runner's share, only when it loaded something`() {
        val base = throughput("ok")
        val withRunner = base.copy(
            summary = base.summary!!.copy(client_bytes_served = 1 * gib, prefill_runner = RunnerTrafficOut(bytes_served = 4 * gib)),
        )
        val tiles = headerTiles(withRunner)!!.associateBy { it.key }
        assertEquals("4.0 GB by the prefill runner", tiles.getValue("loaded").detail)
        assertEquals("Loaded: 5.0 GB, 4.0 GB by the prefill runner", tileDescription(tiles.getValue("loaded")))
        // An older server (no runner figures) and a range without runner traffic: no line.
        assertNull(headerTiles(base)!!.first { it.key == "loaded" }.detail)
        assertNull(runnerDetail(base.summary!!.copy(prefill_runner = RunnerTrafficOut(bytes_served = 0))))
        assertNull(runnerDetail(null))
        assertEquals("< 1 MB by the prefill runner", runnerDetail(SummaryOut(prefill_runner = RunnerTrafficOut(bytes_served = 1))))
    }

    @Test
    fun `headerTiles -- a stale feed shows the dash for the current values only`() {
        val tiles = headerTiles(throughput("stale"))!!.associateBy { it.key }
        assertEquals("—", tiles.getValue("current").value)
        assertEquals("—", tiles.getValue("clients").value)
        assertEquals("60.0 MB/s", tiles.getValue("peak").value)
    }

    @Test
    fun `tileDescription speaks the dash as Not available`() {
        assertEquals("Peak speed: 60.0 MB/s", tileDescription(StatTile("peak", "Peak speed", "60.0 MB/s")))
        assertEquals("Current speed: Not available", tileDescription(StatTile("current", "Current speed", DASH)))
        assertEquals(
            "Active clients: 2, 3 in the last 7 days",
            tileDescription(StatTile("clients", "Active clients", "2", "3 in the last 7 days")),
        )
    }

    // ---- graph -----------------------------------------------------------

    private fun point(t: String, bps: Double) = SeriesPointOut(t = t, bytes_per_second = bps)

    @Test
    fun `seriesGeometry -- same coordinates as the web for a two-point series`() {
        val geo = seriesGeometry(listOf(point("a", 0.0), point("b", 10.0)), 300f, 60f)!!
        assertEquals(listOf(0f, 300f), geo.xs)
        assertEquals(listOf(60f, 0f), geo.ys)
        assertEquals(10.0, geo.maxBytesPerSecond, 0.0)
        // One point: a flat line across the box.
        val one = seriesGeometry(listOf(point("a", 5.0)), 300f, 60f)!!
        assertEquals(listOf(0f, 300f), one.xs)
        assertEquals(listOf(0f, 0f), one.ys)
        assertNull(seriesGeometry(emptyList(), 300f, 60f))
    }

    @Test
    fun `graphGeometryToShow hides the graph for zero series, other ranges, disabled feed, old server`() {
        val pts = listOf(point("2026-10-01T00:00:00Z", 0.0), point("2026-10-02T00:00:00Z", 3.0))
        val ok = SeriesOut(event_feed = feed("ok"), range = "24h", bucket_seconds = 300, points = pts)
        assertNotNull(graphGeometryToShow(ok, "24h", false, 300f, 60f))
        assertNull(graphGeometryToShow(ok, "7d", false, 300f, 60f))
        assertNull(graphGeometryToShow(ok, "24h", true, 300f, 60f))
        assertNull(graphGeometryToShow(ok.copy(event_feed = feed("disabled")), "24h", false, 300f, 60f))
        val flat = ok.copy(points = pts.map { it.copy(bytes_per_second = 0.0) })
        assertNull(graphGeometryToShow(flat, "24h", false, 300f, 60f))
    }

    @Test
    fun `series captions and the accessible name`() {
        val all = SeriesOut(range = "all", bucket_seconds = 86400, points = listOf(point("2026-09-01T00:00:00Z", 1.0)))
        assertEquals("since 2026-09-01", seriesStartText(all))
        assertEquals("24 h ago", seriesStartText(all.copy(range = "24h")))
        assertEquals("7 days ago", seriesStartText(all.copy(range = "7d")))
        assertEquals("30 days ago", seriesStartText(all.copy(range = "30d")))
        assertEquals(
            "Average speed per day since recording started. Highest: 40.0 MB/s.",
            seriesLabel(all, 40 * mib),
        )
        assertEquals(
            "Average speed per 5 minutes in the last 24 hours. Highest: 0 MB/s.",
            seriesLabel(all.copy(range = "24h", bucket_seconds = 300), 0.0),
        )
        assertEquals("highest 40.0 MB/s", seriesHighestText(40 * mib))
        assertEquals("hour", bucketWord(3600))
    }

    // ---- per-job speed ----------------------------------------------------

    @Test
    fun `jobSpeedText -- only with an ok feed, and honest about unknown depots`() {
        val entry = JobThroughputOut(job_id = 1, appid = 2, bytes_per_second = 31.5 * mib, bytes_since_start = (8.8 * gib).toLong(), exclusive_depots = 1)
        assertEquals("31.5 MB/s · 8.8 GB loaded so far", jobSpeedText(entry, FeedState.OK))
        assertNull(jobSpeedText(entry, FeedState.STALE))
        assertNull(jobSpeedText(null, FeedState.OK))
        assertEquals(
            "Speed not measured yet: the server does not know this game's depots yet.",
            jobSpeedText(entry.copy(bytes_per_second = null, exclusive_depots = 0), FeedState.OK),
        )
        assertNull(jobSpeedText(entry.copy(bytes_per_second = null, exclusive_depots = 2), FeedState.OK))
    }

    // ---- history filter ----------------------------------------------------

    private fun job(id: Int, status: String, finished: String?, created: String = "2026-01-01T00:00:00Z") =
        JobSummary(id = id, appid = 10 + id, type = "prefill", status = status, created_at = created, finished_at = finished)

    @Test
    fun `filterJobsByRange keeps in-flight jobs, recent ones and unreadable times`() {
        val jobs = listOf(
            job(1, "running", null),
            job(2, "done", "2026-10-14T01:00:00Z"),
            job(3, "done", "2026-10-10T01:00:00Z"),
            job(4, "error", null, created = "not a time"),
        )
        assertEquals(listOf(1, 2, 4), filterJobsByRange(jobs, "24h", now).map { it.id })
        assertEquals(listOf(1, 2, 3, 4), filterJobsByRange(jobs, "7d", now).map { it.id })
        assertEquals(listOf(1, 2, 3, 4), filterJobsByRange(jobs, "all", now).map { it.id })
    }

    @Test
    fun `mergeJobLists keeps the fresh copy and appends the rest in order`() {
        val fresh = listOf(job(5, "running", null), job(4, "done", "x"))
        val deep = listOf(job(5, "queued", null), job(3, "done", "y"), job(2, "done", "z"))
        val merged = mergeJobLists(fresh, deep)
        assertEquals(listOf(5, 4, 3, 2), merged.map { it.id })
        assertEquals("running", merged.first().status)
    }

    @Test
    fun `emptyHistoryText names the range`() {
        assertEquals("Nothing finished yet.", emptyHistoryText("all"))
        assertEquals("Nothing finished in the last 7 days.", emptyHistoryText("7d"))
    }

    @Test
    fun `ranges and their labels are the web's`() {
        assertEquals(listOf("24h", "7d", "30d", "all"), TRAFFIC_RANGES)
        assertEquals(listOf("24 h", "7 days", "30 days", "All"), TRAFFIC_RANGES.map { RANGE_LABELS.getValue(it) })
        assertEquals(false, isRange("1y"))
    }
}
