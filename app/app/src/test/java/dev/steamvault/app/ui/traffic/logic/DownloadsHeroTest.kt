package dev.steamvault.app.ui.traffic.logic

import dev.steamvault.app.net.model.CurrentOut
import dev.steamvault.app.net.model.EventFeedOut
import dev.steamvault.app.net.model.LiveCurrentOut
import dev.steamvault.app.net.model.LiveOut
import dev.steamvault.app.net.model.LivePointOut
import dev.steamvault.app.net.model.SeriesPointOut
import dev.steamvault.app.net.model.SummaryOut
import dev.steamvault.app.net.model.ThroughputNowOut
import dev.steamvault.app.net.model.ThroughputOut
import dev.steamvault.app.ui.status.StatusKind
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * WP ANDROID-DL-1: `DownloadsHero.kt`, the twin of web
 * `components/downloads-hero.js`'s `heroStatus` and `splitGeometry`. The
 * cases mirror web/tests/runner-split.test.js one for one; every expected
 * word and kind name is a LITERAL (docs/LEARNINGS.md, Android:
 * literal-vs-literal contracts), the web side is pinned by
 * `DownloadsHeroWebTwinTest`.
 */
class DownloadsHeroTest {
    private val mib = 1024.0 * 1024.0
    private val feed = EventFeedOut(enabled = true, state = "ok", last_sweep_at = "2026-10-18T11:58:07Z")

    /** A live `current` (the `now` block's) with the runner split. */
    private fun live(total: Double, runner: Double, hit: Double?, miss: Double?) = LiveCurrentOut(
        bytes_per_second = total,
        runner_bytes_per_second = runner,
        runner_hit_bytes_per_second = hit,
        runner_miss_bytes_per_second = miss,
        window_start = "2026-10-18T11:59:50Z",
        window_end = "2026-10-18T12:00:07Z",
        window_seconds = 17,
    )

    private fun current(total: Double, runner: Double?, hit: Double?, miss: Double?) = CurrentOut(
        bytes_per_second = total,
        runner_bytes_per_second = runner,
        runner_hit_bytes_per_second = hit,
        runner_miss_bytes_per_second = miss,
    )

    /** A persisted range's answer whose swept `current` lags (an older download); `now` must win. */
    private fun answer(now: LiveCurrentOut) = ThroughputOut(
        event_feed = feed,
        range = "1h",
        bucket_seconds = 60,
        active_window_minutes = 15,
        current = CurrentOut(
            bytes_per_second = 9 * mib,
            runner_bytes_per_second = 9 * mib,
            runner_hit_bytes_per_second = 0.0,
            runner_miss_bytes_per_second = 9 * mib,
            window_seconds = 487,
        ),
        active_clients_now = 0,
        runner_active_now = true,
        summary = SummaryOut(bytes_served = 30L * 1024 * 1024 * 1024, peak_bytes_per_second = 68 * mib),
        now = ThroughputNowOut(current = now, active_clients = 0, runner_active = now.runner_bytes_per_second > 0, window_seconds = 17),
    )

    @Test
    fun `the pill words, legend words and status kinds are the web's literals`() {
        assertEquals("Downloading", PILL_DOWNLOADING)
        assertEquals("Serving", PILL_SERVING)
        assertEquals("Verifying", PILL_VERIFYING)
        assertEquals("Idle", PILL_IDLE)
        assertEquals("Served from cache", LEGEND_SERVED)
        assertEquals("From the internet", LEGEND_INTERNET)
        // No new kind: the pill reuses the existing status kinds (TOKENS-SYNC-1).
        assertEquals("running", heroStatus(current(1.0, 1.0, 0.0, 1.0))!!.kind.wireName)
        assertEquals("verify", heroStatus(current(1.0, 1.0, 1.0, 0.0))!!.kind.wireName)
        assertEquals("cached", heroStatus(current(1.0, 0.0, 0.0, 0.0))!!.kind.wireName)
        assertEquals("notinuse", heroStatus(current(0.0, 0.0, 0.0, 0.0))!!.kind.wireName)
    }

    @Test
    fun `MUTATION TARGET -- a verify-only runner (all HIT) reads Verifying, never Downloading`() {
        assertEquals(HeroStatus(StatusKind.VERIFY, "Verifying"), heroStatus(current(68 * mib, 68 * mib, 68 * mib, 0.0)))
        // Through the screen's path: the persisted range's answer merged with `now`.
        val merged = mergeNow(answer(live(68 * mib, 68 * mib, 68 * mib, 0.0)))
        assertEquals("Verifying", heroStatus(merged.current)!!.text)
        assertNotEquals("Downloading", heroStatus(merged.current)!!.text)
        // A verify next to a client that loads: still the runner's verify.
        assertEquals("Verifying", heroStatus(current(80 * mib, 68 * mib, 68 * mib, 0.0))!!.text)
    }

    @Test
    fun `MUTATION TARGET -- runner MISS traffic reads Downloading, also when most of it is HITs`() {
        assertEquals(HeroStatus(StatusKind.RUNNING, "Downloading"), heroStatus(current(20 * mib, 20 * mib, 0.0, 20 * mib)))
        // Mixed: verifies most chunks, downloads a few -> it is downloading.
        assertEquals("Downloading", heroStatus(current(20 * mib, 20 * mib, 19 * mib, 1 * mib))!!.text)
        // BYPASS is in neither split figure: runner bytes that are not HITs.
        assertEquals("Downloading", heroStatus(current(20 * mib, 20 * mib, 15 * mib, 0.0))!!.text)
    }

    @Test
    fun `the other pill states are unchanged by the split`() {
        assertEquals(HeroStatus(StatusKind.CACHED, "Serving"), heroStatus(current(40 * mib, 0.0, 0.0, 0.0)))
        assertEquals(HeroStatus(StatusKind.NOTINUSE, "Idle"), heroStatus(current(0.0, 0.0, 0.0, 0.0)))
        // A nonsense split (negative, NaN) is no split: runner traffic downloads.
        assertEquals("Downloading", heroStatus(current(5.0, 5.0, -1.0, 0.0))!!.text)
        assertEquals("Downloading", heroStatus(current(5.0, 5.0, Double.NaN, 0.0))!!.text)
        // No current figure (feed not ok, no answer): no pill.
        assertNull(heroStatus(null))
        assertNull(heroStatus(current(Double.NaN, 0.0, 0.0, 0.0)))
        assertNull(heroStatus(current(-1.0, 0.0, 0.0, 0.0)))
    }

    @Test
    fun `backward compatible -- a server before rc21 (no split) keeps Downloading for runner traffic`() {
        assertEquals("Downloading", heroStatus(CurrentOut(bytes_per_second = 6 * mib, runner_bytes_per_second = 6 * mib))!!.text)
        // ... and one before METRIC-AUDIT (no runner figure at all) reads Serving for any traffic.
        assertEquals("Serving", heroStatus(CurrentOut(bytes_per_second = 6 * mib))!!.text)
    }

    @Test
    fun `mergeNow and mergeLive carry the split into the pill (live window, not the swept one)`() {
        // Swept window: downloading; live: verify only -> the live figure wins.
        assertEquals("Verifying", heroStatus(mergeNow(answer(live(30 * mib, 30 * mib, 30 * mib, 0.0))).current)!!.text)
        val liveAnswer = LiveOut(event_feed = feed, current = live(30 * mib, 30 * mib, 30 * mib, 0.0), active_clients = 0, active_window_seconds = 17)
        val summary = answer(live(0.0, 0.0, 0.0, 0.0)).copy(now = null)
        assertEquals("Verifying", heroStatus(mergeLive(summary, liveAnswer).current)!!.text)
        // Without the merge the swept window would say Downloading.
        assertEquals("Downloading", heroStatus(summary.current)!!.text)
    }

    // ---- the graph -------------------------------------------------------

    private fun pt(bps: Double, served: Long, hit: Long, runner: Long = 0, runnerHit: Long? = null) = SeriesPointOut(
        t = "2026-10-18T12:00:00Z",
        bytes_per_second = bps,
        bytes_served = served,
        hit_bytes = hit,
        runner_bytes = runner,
        runner_hit_bytes = runnerHit,
    )

    @Test
    fun `MUTATION TARGET -- a verify-only runner's bytes are in the served-from-cache series, not from the internet`() {
        // One point: 100 bytes, all the runner's, all HIT; no client traffic.
        val g = splitGeometry(listOf(pt(0.0, 0, 0, 0, 0), pt(10 * mib, 100, 0, 100, 100)), 600f, 96f)!!
        // Served line reaches the total: the internet band is empty (web "M0 96 L600 0").
        assertEquals(listOf(0f, 600f), g.xs)
        assertEquals(listOf(96f, 0f), g.servedYs)
        assertEquals(g.totalYs, g.servedYs)
    }

    @Test
    fun `mixed runner and client -- cache = client HIT + runner HIT, internet = the rest`() {
        // 1000 bytes: client HIT 200, client MISS 100, runner HIT 400, runner MISS 300.
        val g = splitGeometry(listOf(pt(0.0, 0, 0, 0, 0), pt(10 * mib, 1000, 200, 700, 400)), 600f, 96f)!!
        // served = 60% of the total -> y = 96 - 0.6 * 96 = 38.4 (web "M0 96 L600 38.4").
        assertEquals(96f, g.servedYs[0], 0.001f)
        assertEquals(38.4f, g.servedYs[1], 0.001f)
        assertEquals(listOf(96f, 0f), g.totalYs)
        assertEquals(10 * mib, g.maxBytesPerSecond, 0.0)
    }

    @Test
    fun `backward compatible -- points without runner_hit_bytes count all runner traffic as from the internet`() {
        val g = splitGeometry(listOf(pt(0.0, 0, 0), pt(10 * mib, 100, 0, 100, null)), 600f, 96f)!!
        assertEquals(listOf(96f, 96f), g.servedYs)
        assertEquals(listOf(96f, 0f), g.totalYs)
    }

    @Test
    fun `the graph geometry edge cases follow seriesGeometry`() {
        assertNull(splitGeometry(emptyList(), 600f, 96f))
        // One point is flat across the box; an all-zero series lies on the floor.
        val one = splitGeometry(listOf(pt(5.0, 50, 25)), 600f, 96f)!!
        assertEquals(listOf(0f, 600f), one.xs)
        assertEquals(listOf(0f, 0f), one.totalYs)
        assertEquals(listOf(48f, 48f), one.servedYs)
        val idle = splitGeometry(listOf(pt(0.0, 0, 0), pt(0.0, 0, 0)), 600f, 96f)!!
        assertEquals(listOf(96f, 96f), idle.totalYs)
        assertEquals(listOf(96f, 96f), idle.servedYs)
        // A cache share above 100% is capped at the total.
        val over = splitGeometry(listOf(pt(10.0, 100, 150)), 600f, 96f)!!
        assertEquals(over.totalYs, over.servedYs)
    }

    @Test
    fun `the live view's points carry the split into the graph too`() {
        val liveAnswer = LiveOut(
            event_feed = feed,
            range_start = "2026-10-18T12:00:00Z",
            points = listOf(
                LivePointOut(t = "2026-10-18T12:00:00Z", bucket_seconds = 5, runner_hit_bytes = 0, runner_miss_bytes = 0),
                LivePointOut(
                    t = "2026-10-18T12:00:05Z", bucket_seconds = 5, bytes_served = 500, runner_bytes = 500,
                    runner_hit_bytes = 500, runner_miss_bytes = 0, bytes_per_second = 100.0, peak_bytes_per_second = 100.0,
                ),
            ),
        )
        val series = liveSeries(liveAnswer)
        assertEquals(500L, series.points.last().runner_hit_bytes)
        val g = splitGeometry(series.points, 600f, 96f)!!
        assertEquals("all of it from the cache", g.totalYs, g.servedYs)
    }
}
