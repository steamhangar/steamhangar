package dev.steamvault.app.ui.traffic.logic

import dev.steamvault.app.net.model.CurrentOut
import dev.steamvault.app.net.model.EventFeedOut
import dev.steamvault.app.net.model.RunnerTrafficOut
import dev.steamvault.app.net.model.SummaryOut
import dev.steamvault.app.net.model.ThroughputOut
import dev.steamvault.app.ui.status.StatusKind
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * WP ANDROID-REDESIGN-1c: [heroFigures] and its helpers, the twin of web
 * `components/downloads-hero.js`'s `heroModel` / `splitFigure` /
 * `devicesLoading`. The cases mirror web/tests/downloads-components.test.js;
 * every expected word is a LITERAL (docs/LEARNINGS.md, Android:
 * literal-vs-literal contracts); the web side is pinned by
 * `DownloadsHeroWebTwinTest`.
 */
class DownloadsHeroModelTest {
    private val mib = 1024.0 * 1024.0
    private val gib = 1024L * 1024L * 1024L

    private fun throughput(
        state: String = "ok",
        current: Double? = 40 * mib,
        clients: Int? = 2,
        runner: RunnerTrafficOut? = null,
    ) = ThroughputOut(
        event_feed = EventFeedOut(enabled = state != "disabled", state = state, last_sweep_at = "2026-10-14T19:29:00Z"),
        range = "6h",
        bucket_seconds = 300,
        active_window_minutes = 15,
        current = current?.let { CurrentOut(bytes_per_second = it, runner_bytes_per_second = 0.0, window_seconds = 480) },
        active_clients_now = clients,
        summary = if (state == "disabled") {
            null
        } else {
            SummaryOut(
                bytes_served = 60 * gib,
                hit_bytes = 50 * gib,
                miss_bytes = 10 * gib,
                byte_hit_rate = 50.0 / 60.0,
                average_bytes_per_second = 50 * mib,
                peak_bytes_per_second = 100 * mib,
                active_clients = 3,
                prefill_runner = runner,
            )
        },
    )

    @Test
    fun `splitFigure -- number + unit, percent, below 1 MB, the dash`() {
        assertEquals(HeroFigure("60.0", "GB"), splitFigure("60.0 GB"))
        assertEquals(HeroFigure("38.2", "MB/s"), splitFigure("38.2 MB/s"))
        assertEquals(HeroFigure("83", "%"), splitFigure("83%"))
        assertEquals(HeroFigure("< 1", "MB"), splitFigure("< 1 MB"))
        assertEquals(HeroFigure("—", ""), splitFigure("—"))
        assertEquals(HeroFigure("—", ""), splitFigure(null))
    }

    @Test
    fun `MUTATION PIN -- devices loading only with a fresh feed, zero reads no device loading, unknown shows nothing`() {
        assertEquals("1 device loading", devicesLoadingText(throughput(clients = 1)))
        assertEquals("3 devices loading", devicesLoadingText(throughput(clients = 3)))
        assertEquals("no device loading", devicesLoadingText(throughput(clients = 0)))
        assertNull(devicesLoadingText(throughput(clients = null)))
        assertNull(devicesLoadingText(throughput(state = "stale", clients = 2)))
        assertNull(devicesLoadingText(null))
    }

    @Test
    fun `MUTATION PIN -- heroFigures maps the figures onto the kit anatomy (big, pill, caption, mini, four tiles)`() {
        val runner = RunnerTrafficOut(bytes_served = 20 * gib, hit_bytes = 5 * gib, miss_bytes = 15 * gib)
        val f = heroFigures(throughput(runner = runner), "Limit 8 MB/s, all day")!!
        assertEquals(HeroFigure("40.0", "MB/s"), f.big)
        assertEquals(HeroStatus(StatusKind.CACHED, "Serving"), f.status)
        assertEquals(
            listOf(HeroPart("Limit "), HeroPart("8 MB/s", bold = true), HeroPart(", all day"), HeroPart(" · "), HeroPart("2 devices loading")),
            f.caption,
        )
        assertEquals(listOf("average", "peak", "runner"), f.mini.map { it.key })
        assertEquals(
            listOf(HeroPart("Prefill runner "), HeroPart("20.0 GB", bold = true), HeroPart(" of it, "), HeroPart("5.0 GB", bold = true), HeroPart(" from the cache")),
            f.mini[2].parts,
        )
        assertEquals(
            listOf("Served=60.0GB", "From internet=25.0GB", "Saved by cache=50.0GB", "Hit rate=83%"),
            f.tiles.map { "${it.label}=${it.figure.value}${it.figure.unit}" },
        )
        assertEquals(83, Math.round(f.tiles[3].meter!! * 100))
        assertNull(f.tiles[0].meter)
        // TalkBack keeps the established figure words.
        assertEquals("Peak speed: 100 MB/s", f.mini[1].description)
        assertEquals(true, f.bigDescription.startsWith("Current speed: 40.0 MB/s"))
        assertEquals(true, f.captionDescription!!.contains("Loading now: 2"))
    }

    @Test
    fun `heroFigures -- an idle cache says Idle, an unknown current speed shows a dash and no pill`() {
        assertEquals(HeroStatus(StatusKind.NOTINUSE, "Idle"), heroFigures(throughput(current = 0.0), null)!!.status)
        val stale = heroFigures(throughput(state = "stale", current = null), null)!!
        assertNull(stale.status)
        assertEquals("—", stale.big.value)
        assertEquals("no limit and no fresh count: no caption", emptyList<HeroPart>(), stale.caption)
    }

    @Test
    fun `heroFigures -- nothing before an answer, with the feed off, or for an old server`() {
        assertNull(heroFigures(null, null))
        assertNull(heroFigures(throughput(state = "disabled"), null))
        assertNull(heroFigures(throughput(), null, unsupported = true))
    }

    @Test
    fun `limitParts -- the speed in bold, anything else as it is`() {
        assertEquals(
            listOf(HeroPart("Limit "), HeroPart("8 MB/s", bold = true), HeroPart(", in force now (outside the download window 01:00–07:00)")),
            limitParts("Limit 8 MB/s, in force now (outside the download window 01:00–07:00)"),
        )
        assertEquals(listOf(HeroPart("No limit")), limitParts("No limit"))
    }
}
