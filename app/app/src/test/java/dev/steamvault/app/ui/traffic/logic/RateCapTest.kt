package dev.steamvault.app.ui.traffic.logic

import dev.steamvault.app.net.model.CoreApplyOut
import dev.steamvault.app.net.model.ScheduleOut
import dev.steamvault.app.net.model.SettingInfoOut
import dev.steamvault.app.net.model.SettingsOut
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.time.Instant

/** WP RATE-TILE: twin of the rate-cap cases in `web/tests/traffic-view.test.js`. */
class RateCapTest {
    private fun settings(effective: JsonElement, pending: Boolean = false) = SettingsOut(
        readonly = false,
        settings = listOf(SettingInfoOut(key = "upstream_rate", effective = effective, source = "default", applies = "core")),
        core_apply = if (pending) CoreApplyOut(pending = true) else null,
    )

    private fun schedule(window: String?, tz: String = "UTC+02:00") = ScheduleOut(
        enabled = window != null, window = window, interval_minutes = 60, client_stale_days = 30, server_timezone = tz,
    )

    // 2026-10-16 01:00 UTC = 03:00 on a UTC+02:00 server
    private val at0100z = Instant.parse("2026-10-16T01:00:00Z").toEpochMilli()
    private val min = 60_000L

    @Test
    fun `parseRateCap reads digits with k and m and refuses everything else`() {
        assertEquals(8L * 1048576, parseRateCap("8m"))
        assertEquals(819200L, parseRateCap("800k"))
        assertEquals(5000L, parseRateCap("5000"))
        for (bad in listOf(null, "", "off", "0", "08m", "1.5m", "8mb")) assertNull(bad, parseRateCap(bad))
    }

    @Test
    fun `insideWindow uses the server clock, end exclusive, overnight wraps, unknown is null`() {
        assertEquals(true, insideWindow("03:00-07:00", "UTC+02:00", at0100z))
        assertEquals(false, insideWindow("03:00-07:00", "UTC+02:00", at0100z - min))
        assertEquals(false, insideWindow("03:00-07:00", "UTC+02:00", at0100z + 240 * min))
        assertEquals(true, insideWindow("22:00-06:00", "UTC", Instant.parse("2026-10-16T23:00:00Z").toEpochMilli()))
        assertEquals(false, insideWindow("22:00-06:00", "UTC", Instant.parse("2026-10-16T12:00:00Z").toEpochMilli()))
        assertEquals(true, insideWindow("03:00-07:00", "UTC-05:30", Instant.parse("2026-10-16T08:30:00Z").toEpochMilli()))
        assertNull(insideWindow("03:00-07:00", "CEST", at0100z))
        assertNull(insideWindow("garbage", "UTC", at0100z))
    }

    @Test
    fun `rateCapLine never claims a cap that is not in force now`() {
        val win = schedule("03:00-07:00")
        val eight = settings(JsonPrimitive("8m"))
        assertEquals("Limit 8 MB/s, not in force now (inside the download window 03:00–07:00)", rateCapLine(eight, win, at0100z))
        assertEquals("Limit 8 MB/s, in force now (outside the download window 03:00–07:00)", rateCapLine(eight, win, at0100z + 600 * min))
        assertEquals("Limit 800 KB/s, all day", rateCapLine(settings(JsonPrimitive("800k")), schedule(null, "UTC"), at0100z))
        assertEquals("Limit 8 MB/s, outside the download window", rateCapLine(eight, null, at0100z))
        assertEquals("Limit 8 MB/s, outside the download window 03:00–07:00", rateCapLine(eight, schedule("03:00-07:00", "?"), at0100z))
        assertEquals("Limit 8 MB/s, all day (change not applied yet)", rateCapLine(settings(JsonPrimitive("8m"), pending = true), schedule(null), at0100z))
        assertEquals("No limit", rateCapLine(settings(JsonNull), win, at0100z))
        assertEquals("No limit", rateCapLine(settings(JsonPrimitive("off")), win, at0100z))
        assertNull(rateCapLine(settings(JsonPrimitive("???")), win, at0100z))
        assertNull(rateCapLine(SettingsOut(readonly = false), win, at0100z))
        assertNull(rateCapLine(null, win, at0100z))
    }

    @Test
    fun `the cap line is its own tile line and is spoken after the detail`() {
        val tiles = headerTiles(null, "No limit")
        assertNull(tiles)
        val tile = StatTile("current", "Current speed", "40.0 MB/s", "scope", "Limit 8 MB/s, all day")
        assertEquals("Current speed: 40.0 MB/s, scope, Limit 8 MB/s, all day", tileDescription(tile))
    }
}
