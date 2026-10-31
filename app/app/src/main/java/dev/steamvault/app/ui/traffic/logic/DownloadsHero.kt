package dev.steamvault.app.ui.traffic.logic

import dev.steamvault.app.net.model.CurrentOut
import dev.steamvault.app.net.model.SeriesPointOut
import dev.steamvault.app.ui.status.StatusKind

/**
 * The pure parts of web `components/downloads-hero.js` the Android
 * Downloads statistics header shows (WP ANDROID-DL-1, twin of WEB-FIX-20.2
 * and RUNNER-SPLIT-1): the status pill ([heroStatus]) and the two-series
 * graph ([splitGeometry]). Pure: no Compose, no I/O.
 *
 * **Verbatim-port exception (app/README.md "String resources").** The pill
 * words and the legend words are the web module's own literals, pinned by
 * string equality in `DownloadsHeroTest` and against the web source in
 * `DownloadsHeroWebTwinTest`.
 */

/** The status pill words (web `PILL_*`). */
const val PILL_DOWNLOADING = "Downloading"
const val PILL_SERVING = "Serving"
const val PILL_VERIFYING = "Verifying"
const val PILL_IDLE = "Idle"

/** The graph legend's words (web `LEGEND_*`). */
const val LEGEND_SERVED = "Served from cache"
const val LEGEND_INTERNET = "From the internet"

/** One status pill: the status-icon kind and its own word. */
data class HeroStatus(val kind: StatusKind, val text: String)

private fun rateOf(value: Double?): Double? = value?.takeIf { it.isFinite() && it >= 0.0 }

/**
 * The status pill from `current` (web `heroStatus`). From the hangar's view
 * a client pulling from the cache is SERVING; the hangar itself downloads
 * only while its prefill runner pulls content in from the internet.
 * `runner_bytes_per_second` is the runner's part of the current speed (the
 * live tier's on every range, through [mergeLive] / [mergeNow]); WP
 * RUNNER-SPLIT-1 splits it, `runner_hit_bytes_per_second` being what the
 * runner read from the cache (a verify). So:
 *
 *  - runner traffic that is not all cache HITs -> "Downloading" ([StatusKind.RUNNING]);
 *  - runner traffic that is all HITs -> "Verifying" ([StatusKind.VERIFY], the existing kind);
 *  - any other traffic -> "Serving" ([StatusKind.CACHED] with its own word);
 *  - none -> "Idle" ([StatusKind.NOTINUSE]);
 *  - no current figure -> no pill (`null`).
 *
 * A server before rc21 sends no split: its runner traffic reads
 * "Downloading", as before.
 */
fun heroStatus(current: CurrentOut?): HeroStatus? {
    val bps = current?.bytes_per_second ?: return null
    if (!bps.isFinite() || bps < 0.0) return null
    val runner = rateOf(current.runner_bytes_per_second)
    if (runner != null && runner > 0.0) {
        // Same window, same divisor on the server: equal bytes give equal rates.
        val hit = rateOf(current.runner_hit_bytes_per_second)
        if (hit != null && hit > 0.0 && hit >= runner) return HeroStatus(StatusKind.VERIFY, PILL_VERIFYING)
        return HeroStatus(StatusKind.RUNNING, PILL_DOWNLOADING)
    }
    if (bps > 0.0) return HeroStatus(StatusKind.CACHED, PILL_SERVING)
    return HeroStatus(StatusKind.NOTINUSE, PILL_IDLE)
}

/**
 * The stacked two-series graph (web `splitGeometry`): per point the total
 * speed splits by its cache share of `bytes_served`. `hit_bytes` (clients'
 * hits) plus `runner_hit_bytes` (the runner's cache reads, WP
 * RUNNER-SPLIT-1) was served from the cache; the rest came from the
 * internet. [servedYs] is the top of the served band, [totalYs] the top of
 * the internet band on top of it, so the scale is the total's. A point
 * without `runner_hit_bytes` (a server before rc21) counts all runner
 * traffic as from the internet. Same coordinates as [seriesGeometry]
 * (y grows downwards; one point is drawn flat across the box); `null` with
 * no points.
 */
data class SplitGeometry(
    val xs: List<Float>,
    val totalYs: List<Float>,
    val servedYs: List<Float>,
    val maxBytesPerSecond: Double,
)

fun splitGeometry(points: List<SeriesPointOut>, width: Float, height: Float): SplitGeometry? {
    if (points.isEmpty()) return null
    fun num(v: Double) = v.isFinite() && v >= 0.0
    val totals = points.map { if (num(it.bytes_per_second)) it.bytes_per_second else 0.0 }
    val served = points.mapIndexed { i, p ->
        val runnerHit = p.runner_hit_bytes?.takeIf { it >= 0 } ?: 0L
        val cacheBytes = (if (p.hit_bytes >= 0) p.hit_bytes else 0L) + runnerHit
        if (p.bytes_served > 0) totals[i] * minOf(1.0, cacheBytes.toDouble() / p.bytes_served) else 0.0
    }
    val max = totals.max()
    val n = points.size
    val xs = if (n == 1) listOf(0f, width) else totals.indices.map { i -> (i.toFloat() / (n - 1)) * width }
    fun pick(vals: List<Double>) = if (n == 1) listOf(vals[0], vals[0]) else vals
    fun y(v: Double): Float = if (max > 0) (height - (v / max) * height).toFloat() else height
    return SplitGeometry(xs, pick(totals).map(::y), pick(served).map(::y), max)
}
