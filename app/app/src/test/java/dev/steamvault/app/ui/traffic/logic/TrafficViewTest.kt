package dev.steamvault.app.ui.traffic.logic

import dev.steamvault.app.net.model.CurrentOut
import dev.steamvault.app.net.model.EventFeedOut
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.net.model.JobThroughputOut
import dev.steamvault.app.net.model.LiveCurrentOut
import dev.steamvault.app.net.model.LiveOut
import dev.steamvault.app.net.model.LivePointOut
import dev.steamvault.app.net.model.RunnerTrafficOut
import dev.steamvault.app.net.model.SeriesOut
import dev.steamvault.app.net.model.SeriesPointOut
import dev.steamvault.app.net.model.SummaryOut
import dev.steamvault.app.net.model.ThroughputOut
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
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
    fun `headerTiles -- eight figures in the web's order and words`() {
        val tiles = headerTiles(throughput("ok"))!!
        assertEquals(
            listOf(
                "Current speed", "Average while loading", "Peak speed", "Served (cache + prefill)", "Loaded from the internet",
                "Hit rate", "Saved by the cache", "Loading now",
            ),
            tiles.map { it.label },
        )
        assertEquals(listOf("40.0 MB/s", "20.0 MB/s", "60.0 MB/s", "5.0 GB", "0 GB", "80%", "4.0 GB", "2"), tiles.map { it.value })
        assertEquals("machines with cache traffic in the last 15 min", tiles.last().detail)
        assertEquals("all traffic incl. prefill runner, 5-10 min window", tiles.first().detail)
        assertEquals("clients only, runner excluded", tiles.first { it.key == "hit-rate" }.detail)
    }

    @Test
    fun `METRIC-AUDIT -- runner speed and rate details, window minutes from the server`() {
        val base = throughput("ok")
        val t = base.copy(
            active_window_minutes = 20,
            current = CurrentOut(bytes_per_second = 40 * mib, runner_bytes_per_second = 10 * mib),
            summary = base.summary!!.copy(prefill_runner = RunnerTrafficOut(bytes_served = 4 * gib, hit_bytes = 3 * gib, byte_hit_rate = 0.75)),
        )
        val tiles = headerTiles(t)!!.associateBy { it.key }
        assertEquals("all traffic incl. prefill runner, 5-10 min window · 10.0 MB/s by the prefill runner", tiles.getValue("current").detail)
        assertEquals("clients only, runner excluded (runner: 75%)", tiles.getValue("hit-rate").detail)
        assertEquals("machines with cache traffic in the last 20 min", tiles.getValue("clients").detail)
        assertEquals("4.0 GB by the prefill runner, 3.0 GB of that from the cache", tiles.getValue("loaded").detail)
    }

    @Test
    fun `TILE-INTERNET -- the server's figure, else client plus runner misses, else client misses`() {
        val s = SummaryOut(miss_bytes = 10 * gib)
        assertEquals(11 * gib, internetBytes(s.copy(internet_bytes = 11 * gib, prefill_runner = RunnerTrafficOut(miss_bytes = 1 * gib))))
        assertEquals(11 * gib, internetBytes(s.copy(prefill_runner = RunnerTrafficOut(miss_bytes = 1 * gib))))
        assertEquals(10 * gib, internetBytes(s))
        assertEquals(0L, internetBytes(s.copy(internet_bytes = 0)))
        assertNull(internetBytes(null))
        assertEquals("11.0 GB", headerTiles(throughput("ok").copy(summary = s.copy(internet_bytes = 11 * gib)))!!.first { it.key == "internet" }.value)
    }

    @Test
    fun `D15-RUNNER -- Loaded names the prefill runner's share, only when it loaded something`() {
        val base = throughput("ok")
        val withRunner = base.copy(
            summary = base.summary!!.copy(client_bytes_served = 1 * gib, prefill_runner = RunnerTrafficOut(bytes_served = 4 * gib)),
        )
        val tiles = headerTiles(withRunner)!!.associateBy { it.key }
        assertEquals("4.0 GB by the prefill runner", tiles.getValue("loaded").detail)
        assertEquals("Served (cache + prefill): 5.0 GB, 4.0 GB by the prefill runner", tileDescription(tiles.getValue("loaded")))
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
            "Loading now: 2, machines with cache traffic in the last 15 min",
            tileDescription(StatTile("clients", "Loading now", "2", "machines with cache traffic in the last 15 min")),
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
        assertEquals("minute", bucketWord(60))
        assertEquals("5 minutes", bucketWord(300))
        assertEquals(
            "Average speed per minute in the last 6 hours. Highest: 0 MB/s.",
            seriesLabel(all.copy(range = "6h", bucket_seconds = 60), 0.0),
        )
    }

    @Test
    fun `WP GRAPH-ZOOM -- trimSeries drops the empty lead-in but keeps one zero before the data`() {
        val pts = listOf(0.0, 0.0, 0.0, 5.0, 0.0, 7.0, 0.0).mapIndexed { i, v -> point("t$i", v) }
        assertEquals(listOf("t2", "t3", "t4", "t5", "t6"), trimSeries(pts).map { it.t })
        // Data from the first point on, or nothing at all: unchanged.
        assertEquals(listOf("t0", "t1"), trimSeries(listOf(point("t0", 1.0), point("t1", 0.0))).map { it.t })
        assertEquals(listOf("t0", "t1"), trimSeries(listOf(point("t0", 0.0), point("t1", 0.0))).map { it.t })
        assertEquals(emptyList<SeriesPointOut>(), trimSeries(emptyList()))
    }

    @Test
    fun `WP GRAPH-ZOOM -- seriesStartText says how long ago a trimmed span starts`() {
        val start = "2026-10-14T06:00:00Z"
        val full = SeriesOut(
            range = "6h",
            bucket_seconds = 60,
            range_start = start,
            points = listOf(point(start, 1.0)),
        )
        assertEquals("6 h ago", seriesStartText(full, now))
        assertEquals("1 h ago", seriesStartText(full.copy(range = "1h"), now))
        val trimmed = full.copy(points = listOf(point("2026-10-14T10:00:00Z", 1.0)))
        assertEquals("2 h ago", seriesStartText(trimmed, now))
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

    @Test
    fun `WP GRAPH-ZOOM -- the statistics add 1 h and 6 h, the clients page keeps four ranges`() {
        assertEquals(listOf("live", "1h", "6h", "24h", "7d", "30d", "all"), STATS_RANGES)
        assertEquals(listOf("Live", "1 h", "6 h", "24 h", "7 days", "30 days", "All"), STATS_RANGES.map { RANGE_LABELS.getValue(it) })
        assertEquals("in the last hour", RANGE_PHRASES.getValue("1h"))
        assertEquals("in the last 6 hours", RANGE_PHRASES.getValue("6h"))
        assertEquals("6h", DEFAULT_STATS_RANGE)
        assertTrue(isStatsRange("1h"))
        assertFalse(isStatsRange("1y"))
        assertFalse(isRange("1h"))
        assertFalse(isRange("6h"))
    }

    // ---- WP GRAPH-LIVE ---------------------------------------------------

    private fun livePoint(t: String, bucket: Int, bps: Double) =
        LivePointOut(t = t, bucket_seconds = bucket, bytes_per_second = bps, bytes_served = (bps * bucket).toLong())

    @Test
    fun `WP GRAPH-LIVE -- Live is the first range, the default stays 6 h, Clients and History do not get it`() {
        assertEquals("live", STATS_RANGES.first())
        assertEquals("Live", RANGE_LABELS.getValue("live"))
        assertEquals("in the last hour", RANGE_PHRASES.getValue("live"))
        assertEquals("live", LIVE_RANGE)
        assertEquals("6h", DEFAULT_STATS_RANGE)
        assertTrue(isStatsRange("live"))
        assertFalse(isRange("live"))
        assertFalse(TRAFFIC_RANGES.contains("live"))
        assertEquals(3000L, LIVE_POLL_MS)
        assertEquals(30_000L, LIVE_SUMMARY_MS)
        assertEquals("1h", LIVE_SUMMARY_RANGE)
        assertEquals("5 seconds", bucketWord(5))
        assertEquals("15 seconds", bucketWord(15))
        val liveSeries = SeriesOut(range = "live", points = listOf(point("2026-10-14T11:00:00Z", 1.0)))
        assertEquals("1 h ago", seriesStartText(liveSeries, now))
    }

    @Test
    fun `WP GRAPH-LIVE -- liveSeries repeats every point to a uniform 5 s grid`() {
        val live = LiveOut(
            event_feed = feed("ok"),
            range_start = "2026-10-14T11:00:00Z",
            points = listOf(
                livePoint("2026-10-14T11:00:00Z", 60, 1.0),
                livePoint("2026-10-14T11:01:00Z", 15, 2.0),
                livePoint("2026-10-14T11:01:15Z", 5, 3.0),
            ),
        )
        val series = liveSeries(live)
        assertEquals("live", series.range)
        assertEquals(5, series.bucket_seconds)
        assertEquals("2026-10-14T11:00:00Z", series.range_start)
        assertEquals(12 + 3 + 1, series.points.size)
        assertEquals(listOf(1.0, 2.0, 3.0), series.points.map { it.bytes_per_second }.distinct())
        assertEquals(12, series.points.count { it.bytes_per_second == 1.0 })
        assertEquals(3, series.points.count { it.bytes_per_second == 2.0 })
        // An unusable bucket length counts as 5 s; an empty answer an empty series.
        assertEquals(1, liveSeries(live.copy(points = listOf(livePoint("t", 0, 1.0)))).points.size)
        assertEquals(emptyList<SeriesPointOut>(), liveSeries(LiveOut()).points)
    }

    @Test
    fun `WP GRAPH-LIVE -- mergeLive takes Current speed from the live tier and keeps the 1 h summary`() {
        val summary = throughput("ok", range = "1h")
        val live = LiveOut(
            event_feed = feed("ok"),
            current = LiveCurrentOut(
                bytes_per_second = 10 * mib,
                runner_bytes_per_second = 2 * mib,
                window_start = "2026-10-14T11:59:40Z",
                window_end = "2026-10-14T12:00:00Z",
                window_seconds = 18,
            ),
        )
        val merged = mergeLive(summary, live)
        assertEquals("live", merged.range)
        assertTrue(merged.live)
        assertEquals(summary.summary, merged.summary)
        assertEquals(10 * mib, merged.current!!.bytes_per_second, 0.0)
        val current = headerTiles(merged)!!.first { it.key == "current" }
        assertEquals("10.0 MB/s", current.value)
        assertEquals("all traffic incl. prefill runner, last 18 s · 2.0 MB/s by the prefill runner", current.detail)
        assertEquals(
            headerTiles(summary)!!.filter { it.key != "current" },
            headerTiles(merged)!!.filter { it.key != "current" },
        )
    }

    @Test
    fun `WP GRAPH-LIVE -- mergeLive keeps the persisted current when the live feed has none`() {
        val summary = throughput("ok", range = "1h")
        val off = mergeLive(summary, LiveOut(event_feed = feed("disabled")))
        assertFalse(off.live)
        assertEquals("live", off.range)
        assertEquals(summary.current, off.current)
        assertEquals(
            "all traffic incl. prefill runner, 5-10 min window",
            headerTiles(off)!!.first { it.key == "current" }.detail,
        )
        val stale = mergeLive(summary, LiveOut(event_feed = feed("stale"), current = LiveCurrentOut(bytes_per_second = 1.0)))
        assertFalse(stale.live)
        assertEquals(summary.current, stale.current)
    }
}
