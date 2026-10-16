package dev.steamvault.app.ui.traffic.logic

import dev.steamvault.app.net.model.ScheduleOut
import dev.steamvault.app.net.model.SettingsOut
import dev.steamvault.app.net.model.settingAsStringOrNull

/**
 * WP RATE-TILE: the upstream (internet) rate cap line under "Current speed"
 * (twin of web `parseRateCap` / `insideWindow` / `rateCapLine` in
 * `web/js/lib/traffic-view.js`). The cap applies only OUTSIDE the download
 * window (the one window, `ScheduleOut.window`; none = capped around the
 * clock), so the line says "in force now"  or "not in force now" and never
 * claims a limit that is not in force; without a readable schedule it
 * states the value and the rule and claims neither.
 *
 * Known limit: the deprecated env pin `VAULT_UPSTREAM_RATE_WINDOW` can make
 * vault-core use a window other than the schedule window, and the API does
 * not expose it, so the in-force statement may then be wrong.
 */

private val RATE_RE = Regex("^([1-9][0-9]*)([kKmM]?)$")
private val WINDOW_RE = Regex("^\\s*(\\d{1,2}):(\\d{2})\\s*-\\s*(\\d{1,2}):(\\d{2})\\s*$")
private val ZONE_RE = Regex("^UTC(?:([+-])(\\d{2}):(\\d{2}))?$")

/** Bytes per second of an `upstream_rate` value (digits, optional k = x1024
 * or m = x1048576); `null` for no cap, blank, `off` or anything unreadable. */
fun parseRateCap(raw: String?): Long? {
    val m = RATE_RE.matchEntire(raw?.trim() ?: return null) ?: return null
    val factor = when (m.groupValues[2].lowercase()) {
        "k" -> 1024L
        "m" -> 1048576L
        else -> 1L
    }
    val digits = m.groupValues[1]
    if (digits.length > 12) return null
    return digits.toLong() * factor
}

/** Is [nowMs] inside the daily `HH:MM-HH:MM` [window] on the server's own
 * clock ([serverTimezone] = "UTC+02:00")? Start inclusive, end exclusive,
 * overnight windows wrap. `null` when either cannot be read. */
fun insideWindow(window: String?, serverTimezone: String?, nowMs: Long): Boolean? {
    val w = WINDOW_RE.matchEntire(window ?: return null) ?: return null
    val z = ZONE_RE.matchEntire(serverTimezone ?: return null) ?: return null
    val offset = if (z.groupValues[1].isEmpty()) {
        0L
    } else {
        (if (z.groupValues[1] == "-") -1L else 1L) * (z.groupValues[2].toLong() * 60 + z.groupValues[3].toLong())
    }
    val start = w.groupValues[1].toLong() * 60 + w.groupValues[2].toLong()
    val end = w.groupValues[3].toLong() * 60 + w.groupValues[4].toLong()
    val minute = Math.floorMod(Math.floorDiv(nowMs, 60_000L) + offset, 1440L)
    return if (end < start) minute >= start || minute < end else minute >= start && minute < end
}

/** The line, or `null` while the settings are not known / the value is unreadable. */
fun rateCapLine(settings: SettingsOut?, schedule: ScheduleOut?, nowMs: Long): String? {
    val row = settings?.settings?.firstOrNull { it.key == "upstream_rate" } ?: return null
    val raw = row.effective.settingAsStringOrNull()
    val bytes = parseRateCap(raw)
    if (bytes == null) return if (raw == null || raw == "" || raw == "off") "No limit" else null
    val speed = formatSpeed(bytes.toDouble())!!.replace(".0 ", " ") // a cap reads "8 MB/s", not "8.0 MB/s"
    val limit = "Limit $speed"
    val pending = if (settings.core_apply?.pending == true) " (change not applied yet)" else ""
    if (schedule == null) return "$limit, outside the download window$pending"
    val window = schedule.window?.takeIf { it.isNotBlank() } ?: return "$limit, all day$pending"
    val m = WINDOW_RE.matchEntire(window)
    val span = "download window " + (m?.let { "${it.groupValues[1]}:${it.groupValues[2]}–${it.groupValues[3]}:${it.groupValues[4]}" } ?: window.trim())
    return when (insideWindow(window, schedule.server_timezone, nowMs)) {
        null -> "$limit, outside the $span$pending"
        true -> "$limit, not in force now (inside the $span)$pending"
        false -> "$limit, in force now (outside the $span)$pending"
    }
}
