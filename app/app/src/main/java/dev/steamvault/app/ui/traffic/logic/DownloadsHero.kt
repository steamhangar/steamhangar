package dev.steamvault.app.ui.traffic.logic

import dev.steamvault.app.net.model.CurrentOut
import dev.steamvault.app.net.model.LoadingAppOut
import dev.steamvault.app.net.model.LoadingSourceOut
import dev.steamvault.app.net.model.SeriesPointOut
import dev.steamvault.app.net.model.ThroughputOut
import dev.steamvault.app.ui.library.logic.UNKNOWN_APP_NAME
import dev.steamvault.app.ui.status.StatusKind

/**
 * The pure parts of web `components/downloads-hero.js` the Android
 * Downloads statistics header shows (WP ANDROID-DL-1, twin of WEB-FIX-20.2
 * and RUNNER-SPLIT-1): the status pill ([heroStatus]) and the two-series
 * graph ([splitGeometry]); WP ANDROID-DL-2 (twin of NOW-LOADING-1): the
 * "Loading now" source rows ([loadingNowModel]). Pure: no Compose, no I/O.
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

// ---------------------------------------------------------------------
// "Loading now" rows (WP ANDROID-DL-2, twin of NOW-LOADING-1)
// ---------------------------------------------------------------------

/** The "Loading now" block's words (web `LOADING_*`). */
const val LOADING_NOW_LABEL = "Loading now"
const val LOADING_UNATTRIBUTED = "Unattributed traffic"
const val LOADING_NEW_DEVICE = "New device"
const val LOADING_NO_JOB = "No running job"

/** The runner row's name when the server sends none (web literal). */
const val LOADING_RUNNER_NAME = "Prefill runner"

/** A source without games (the Clients page's wording, web `gamesText`). */
const val LOADING_NO_GAME = "content not linked to one game"

/** The unattributed row's detail (web literal). */
const val LOADING_UNATTRIBUTED_DETAIL = "more devices at once than the live view tells apart"

const val LOADING_FROM_CACHE = "from the cache"
const val LOADING_FROM_INTERNET = "from the internet"
const val LOADING_CANCEL_REQUESTED = "cancel requested"
const val LOADING_PAUSE_REQUESTED = "pause requested"

/** The row kinds (the server's `kind`). */
const val LOADING_KIND_RUNNER = "runner"
const val LOADING_KIND_CLIENT = "client"
const val LOADING_KIND_UNATTRIBUTED = "unattributed"

/** One row of the block: who loads, what, and how fast. */
data class LoadingNowRow(val key: String, val kind: String, val name: String, val detail: String, val speed: String)

/** The block: its label and at least one row. */
data class LoadingNowModel(val label: String, val rows: List<LoadingNowRow>)

/** An app's name in a row, never a bare id (the Clients page's rule). */
private fun appName(name: String?): String = if (!name.isNullOrBlank()) name else UNKNOWN_APP_NAME

/** Games of a source as one phrase, or the Clients page's wording without one. */
private fun gamesText(apps: List<LoadingAppOut>): String {
    val names = apps.map { appName(it.name) }
    return if (names.isNotEmpty()) names.joinToString(", ") else LOADING_NO_GAME
}

/** JS `Math.round` for the non-negative shares used here. */
private fun jsRound(value: Double): Long = Math.floor(value + 0.5).toLong()

/** Where a device's bytes came from, from the row's HIT/MISS split (web `cacheWord`). */
private fun cacheWord(row: LoadingSourceOut): String? {
    val total = rateOf(row.bytes_per_second) ?: 0.0
    val hit = rateOf(row.hit_bytes_per_second) ?: 0.0
    val miss = rateOf(row.miss_bytes_per_second) ?: 0.0
    if (total <= 0.0) return null
    if (hit >= total) return LOADING_FROM_CACHE
    if (hit <= 0.0 && miss > 0.0) return LOADING_FROM_INTERNET
    if (hit <= 0.0) return null
    return "${jsRound((hit / total) * 100)}% $LOADING_FROM_CACHE"
}

/**
 * The "Loading now" block (web `loadingNowModel`) from the merged answer's
 * [ThroughputOut.loading_sources] (`now.loading_sources` via [mergeNow], the
 * live answer's via [mergeLive]): one row per source of the traffic in the
 * live window, from the same read of the ring as the big number, the pill
 * and the device count, so they agree:
 *
 *  - the prefill runner: its job's game ([LOADING_NO_JOB] when the runner
 *    still has traffic in the window but no prefill job runs, e.g. right
 *    after a cancel), the phase in the pill's words ("Downloading" /
 *    "Verifying"), a pending stop request;
 *  - each device, named as on the Clients page ([LOADING_NEW_DEVICE] plus
 *    its address before the Clients page lists it), with its games and
 *    where the bytes came from;
 *  - [LOADING_UNATTRIBUTED]: bytes no device explains.
 *
 * `null` (no block, no empty heading) unless the feed is `ok` and at least
 * one source loads; an older server sends no rows, so no block. Pure.
 */
fun loadingNowModel(throughput: ThroughputOut?): LoadingNowModel? {
    if (throughput == null || feedState(throughput.event_feed) != FeedState.OK) return null
    val sources = throughput.loading_sources
    if (sources.isNullOrEmpty()) return null
    val rows = ArrayList<LoadingNowRow>()
    for (src in sources) {
        val speed = formatSpeed(src.bytes_per_second) ?: DASH
        when (src.kind) {
            LOADING_KIND_RUNNER -> {
                val parts = ArrayList<String>()
                val job = src.job
                if (job != null) {
                    parts.add(appName(job.name))
                    job.job_id?.let { parts.add("job #$it") }
                } else {
                    parts.add(LOADING_NO_JOB)
                    if (src.apps.isNotEmpty()) parts.add(gamesText(src.apps))
                }
                parts.add(if (src.phase == "verify") PILL_VERIFYING else PILL_DOWNLOADING)
                when (job?.stop_request) {
                    "cancel" -> parts.add(LOADING_CANCEL_REQUESTED)
                    "pause" -> parts.add(LOADING_PAUSE_REQUESTED)
                }
                val name = if (!src.name.isNullOrEmpty()) src.name else LOADING_RUNNER_NAME
                rows.add(LoadingNowRow("runner", LOADING_KIND_RUNNER, name, parts.joinToString(" · "), speed))
            }
            LOADING_KIND_CLIENT -> {
                val known = src.known != false
                val name = if (known && !src.name.isNullOrEmpty()) src.name else LOADING_NEW_DEVICE
                val parts = ArrayList<String>()
                if (!known && src.addresses.isNotEmpty()) parts.add(src.addresses.joinToString(", "))
                parts.add(gamesText(src.apps))
                cacheWord(src)?.let { parts.add(it) }
                val key = if (!src.key.isNullOrEmpty()) src.key else name
                rows.add(LoadingNowRow(key, LOADING_KIND_CLIENT, name, parts.joinToString(" · "), speed))
            }
            LOADING_KIND_UNATTRIBUTED -> rows.add(
                LoadingNowRow("unattributed", LOADING_KIND_UNATTRIBUTED, LOADING_UNATTRIBUTED, LOADING_UNATTRIBUTED_DETAIL, speed),
            )
        }
    }
    return if (rows.isNotEmpty()) LoadingNowModel(LOADING_NOW_LABEL, rows) else null
}
