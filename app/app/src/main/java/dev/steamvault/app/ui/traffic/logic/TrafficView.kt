package dev.steamvault.app.ui.traffic.logic

import dev.steamvault.app.PRODUCT_NAME
import dev.steamvault.app.net.model.CurrentOut
import dev.steamvault.app.net.model.EventFeedOut
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.net.model.JobThroughputOut
import dev.steamvault.app.net.model.LiveCurrentOut
import dev.steamvault.app.net.model.LiveOut
import dev.steamvault.app.net.model.SeriesOut
import dev.steamvault.app.net.model.SeriesPointOut
import dev.steamvault.app.net.model.SummaryOut
import dev.steamvault.app.net.model.ThroughputOut
import dev.steamvault.app.ui.clients.logic.formatAgo
import dev.steamvault.app.ui.library.logic.formatBytesGB
import java.time.Instant
import java.util.Locale

/**
 * Presentation of vault-api's traffic endpoints (WP D15d) -- a Kotlin port
 * of `web/js/lib/traffic-view.js` (WP D15c), same rules and same words.
 * Pure: no Compose, no I/O. The Downloads statistics header
 * (`ui/downloads/DownloadsStats.kt`) and the Clients page
 * (`ui/clients/ClientsScreen.kt`) draw what these functions return.
 *
 * **Verbatim-port exception (app/README.md "String resources").** Every
 * user-visible literal here is the web module's own literal, kept as a
 * Kotlin string so the two can be diffed side by side; each one is pinned
 * by string equality in `TrafficViewTest` and against the web source in
 * `TrafficWebTwinTest`.
 *
 * **Honest states (api/README.md "event_feed").** `disabled`: every traffic
 * field is `null` ("not measured", never zero) -> one note, no numbers
 * ([headerTiles] returns `null`). `never-swept`/`stale`: the "current"
 * values are `null` and show the dash, the note says why. `ok`: everything
 * shown. A dash always stands for a `null` the server sent; a real `0`
 * prints as a number.
 *
 * **One "now" (WP GRAPH-RECENT-1 + LOADING-NOW-1, web twin since
 * ANDROID-DL-1).** "Current speed", the status pill ([heroStatus]) and
 * "Loading now" read the live tier on every range: the live view through
 * [mergeLive], the persisted ranges through [mergeNow] (the server's `now`
 * block). All three share one window, the live current-speed window
 * (`active_window_seconds`).
 */

/** The four ranges every traffic endpoint and the jobs filter accept. */
val TRAFFIC_RANGES: List<String> = listOf("24h", "7d", "30d", "all")

/**
 * WP GRAPH-ZOOM: the Downloads statistics (header tiles and graph) also
 * offer 1 h and 6 h, answered by the server's 1-minute tier. The Clients
 * page and the History filter keep [TRAFFIC_RANGES].
 */
val STATS_RANGES: List<String> = listOf("live", "1h", "6h", "24h", "7d", "30d", "all")

/** WP GRAPH-LIVE: the first statistics range, answered by
 * `GET /v1/throughput/live` (5 s points, polled every few seconds). Never a
 * Clients / History range. */
const val LIVE_RANGE = "live"

/** The Downloads statistics range when none was chosen yet: `live` (web
 * `DEFAULT_STATS_RANGE`, WP LIVE-DELAY-1; Android since ANDROID-DL-1). Every
 * other range reads tiers the event sweep fills, up to ~6 min late. */
const val DEFAULT_STATS_RANGE = "live"

/** Segmented-control labels, for every range of [STATS_RANGES]. */
val RANGE_LABELS: Map<String, String> = mapOf(
    "live" to "Live",
    "1h" to "1 h",
    "6h" to "6 h",
    "24h" to "24 h",
    "7d" to "7 days",
    "30d" to "30 days",
    "all" to "All",
)

/** Phrases for running text ("1.2 GB loaded in the last 7 days"). */
val RANGE_PHRASES: Map<String, String> = mapOf(
    // WP GRAPH-LIVE: the live view's tiles summarise the last hour.
    "live" to "in the last hour",
    "1h" to "in the last hour",
    "6h" to "in the last 6 hours",
    "24h" to "in the last 24 hours",
    "7d" to "in the last 7 days",
    "30d" to "in the last 30 days",
    "all" to "since recording started",
)

fun isRange(value: String?): Boolean = value in TRAFFIC_RANGES

fun isStatsRange(value: String?): Boolean = value in STATS_RANGES

/** The text a `null` value shows. */
const val DASH = "—"

/** What a screen reader says for [DASH] (web `.sr-only` "Not available"). */
const val NOT_AVAILABLE = "Not available"

private const val KIB = 1024.0
private const val MIB = 1024.0 * 1024.0
private const val GIB = 1024.0 * 1024.0 * 1024.0

private fun finiteNonNegative(value: Double?): Boolean = value != null && value.isFinite() && value >= 0.0

/** JS `Math.round` for the non-negative values used here. */
private fun jsRound(value: Double): Long = Math.floor(value + 0.5).toLong()

private fun oneDecimalOrWhole(value: Double): String =
    if (value >= 100) String.format(Locale.ROOT, "%.0f", value) else String.format(Locale.ROOT, "%.1f", value)

/**
 * Bytes per second as "40.0 MB/s" (binary units, the same 1024 base as
 * [formatBytesGB]'s "GB"). A real zero prints as "0 MB/s"; `null` for
 * anything that is not a measurement.
 */
fun formatSpeed(bytesPerSecond: Double?): String? {
    if (!finiteNonNegative(bytesPerSecond)) return null
    val v = bytesPerSecond!!
    if (v == 0.0) return "0 MB/s"
    if (v < KIB) return "< 1 KB/s"
    if (v < MIB) return "${jsRound(v / KIB)} KB/s"
    if (v < GIB) return "${oneDecimalOrWhole(v / MIB)} MB/s"
    return "${oneDecimalOrWhole(v / GIB)} GB/s"
}

/**
 * A byte total for traffic figures: "0 GB", "< 1 MB", "312 MB", "1.2 GB".
 * Unlike [formatBytesGB] a real zero prints, and small totals stay readable
 * in MB.
 */
fun formatTrafficBytes(bytes: Long?): String? {
    if (bytes == null || bytes < 0) return null
    if (bytes == 0L) return "0 GB"
    if (bytes < MIB) return "< 1 MB"
    if (bytes < GIB) return "${jsRound(bytes / MIB)} MB"
    return formatBytesGB(bytes)
}

/** A 0..1 ratio as a whole percentage ("83%"). */
fun formatRate(rate: Double?): String? {
    if (rate == null || !rate.isFinite() || rate < 0) return null
    return "${jsRound(minOf(rate, 1.0) * 100)}%"
}

/** Notes for the feed states and an old server (one place for the words). */
object FeedText {
    const val DISABLED =
        "Traffic statistics are off: the server does not read the cache's event log. Set VAULT_EVENT_LOG and " +
            "VAULT_EVENT_LOG_PATH in the server's .env to see speeds, hit rate and machines without an agent."
    const val UNSUPPORTED = "This server does not report traffic statistics yet. Update vault-api to see them."
    const val STALE_NEVER =
        "Traffic data is out of date. Current values show a dash until the server reads the cache's event log again."

    fun neverSwept(minutes: Int): String =
        "No traffic data yet. The server reads the cache's event log every $minutes min; the first numbers appear after that."

    fun stale(ago: String): String =
        "Traffic data is out of date: the server last read the cache's event log $ago. " +
            "Current values show a dash until it reads it again."
}

/** The note when a refresh fails after an earlier answer: the old figures
 * stay on screen, this line says they were not updated. */
fun refreshFailedText(reason: String): String = "Couldn't refresh: $reason"

/** Several notes as one line, skipping the empty ones; `null` when none. */
fun joinNotes(vararg notes: String?): String? {
    val parts = notes.filter { !it.isNullOrEmpty() }
    return if (parts.isEmpty()) null else parts.joinToString(" ")
}

enum class FeedState { DISABLED, NEVER_SWEPT, STALE, OK }

/** The feed's state, defensively: anything unreadable counts as
 * [FeedState.DISABLED] (show no numbers rather than invent them). */
fun feedState(feed: EventFeedOut?): FeedState = when (feed?.state) {
    "ok" -> FeedState.OK
    "stale" -> FeedState.STALE
    "never-swept" -> FeedState.NEVER_SWEPT
    else -> FeedState.DISABLED
}

/** The note above the numbers, or `null` when the feed is `ok`. */
fun feedNote(feed: EventFeedOut?, nowMs: Long = System.currentTimeMillis()): String? = when (feedState(feed)) {
    FeedState.OK -> null
    FeedState.DISABLED -> FeedText.DISABLED
    FeedState.NEVER_SWEPT -> {
        val minutes = feed?.sweep_interval_minutes ?: 0
        FeedText.neverSwept(if (minutes > 0) minutes else 5)
    }
    FeedState.STALE -> formatAgo(feed?.last_sweep_at, nowMs)?.let { FeedText.stale(it) } ?: FeedText.STALE_NEVER
}

/**
 * The note line of a ranged poll (Clients page, Downloads statistics), the
 * web views' `paint` rule: an old server -> [FeedText.UNSUPPORTED]; an
 * error before any answer -> "<couldNotLoad>: <error>"; an answer -> the
 * refresh failure (old data kept) joined with the feed note; nothing yet ->
 * [loadingText] (may be `null`: the Clients page shows its loading line in
 * the list instead).
 */
fun pollNote(
    unsupported: Boolean,
    error: String?,
    feed: EventFeedOut?,
    hasData: Boolean,
    couldNotLoad: String,
    loadingText: String?,
    nowMs: Long = System.currentTimeMillis(),
): String? = when {
    unsupported -> FeedText.UNSUPPORTED
    error != null && !hasData -> "$couldNotLoad: $error"
    hasData -> joinNotes(error?.let { refreshFailedText(it) }, feedNote(feed, nowMs))
    else -> loadingText
}

/** One Downloads header figure. [value] is [DASH] for a `null` the server
 * sent; [detail] is an optional second line. */
data class StatTile(val key: String, val label: String, val value: String, val detail: String? = null, val limit: String? = null)

/** What a screen reader says for one tile: "Peak speed: 40.0 MB/s", the
 * dash spoken as [NOT_AVAILABLE], the detail line appended. */
fun tileDescription(tile: StatTile): String {
    val value = if (tile.value == DASH) NOT_AVAILABLE else tile.value
    val parts = listOfNotNull(tile.detail, tile.limit)
    return if (parts.isNotEmpty()) "${tile.label}: $value, ${parts.joinToString(", ")}" else "${tile.label}: $value"
}

/**
 * The Downloads header's figures from `GET /v1/throughput`. `null` before
 * the first answer and with the feed disabled (the note stands alone then).
 * Labels and order are web `headerTiles`'s; the web's hover titles have no
 * Android counterpart and are not ported.
 */
fun headerTiles(throughput: ThroughputOut?, rateCap: String? = null): List<StatTile>? {
    if (throughput == null || feedState(throughput.event_feed) == FeedState.DISABLED) return null
    val summary = throughput.summary
    val phrase = RANGE_PHRASES[throughput.range] ?: ""
    fun tile(key: String, label: String, value: String?, detail: String? = null, limit: String? = null) =
        StatTile(key, label, value ?: DASH, detail, limit)
    val inRange = summary?.active_clients
    val windowMinutes = throughput.active_window_minutes ?: 15
    // WP LOADING-NOW-1: a count read from the live tier is over the current-speed window (seconds).
    val windowSeconds = activeWindowSeconds(throughput)
    return listOf(
        tile("current", "Current speed", formatSpeed(throughput.current?.bytes_per_second), currentDetail(throughput), rateCap),
        tile("average", "Average while loading", formatSpeed(summary?.average_bytes_per_second)),
        tile("peak", "Peak speed", formatSpeed(summary?.peak_bytes_per_second)),
        // Everything the cache served, the runner included; its share below.
        tile("loaded", "Served (cache + prefill)", formatTrafficBytes(summary?.bytes_served), runnerDetail(summary)),
        tile("internet", "Loaded from the internet", formatTrafficBytes(internetBytes(summary))),
        // Bytes, not requests: the same base as "Saved by the cache" next to it.
        tile("hit-rate", "Hit rate", formatRate(summary?.byte_hit_rate), hitRateDetail(summary)),
        tile("saved", "Saved by the cache", formatTrafficBytes(summary?.hit_bytes)),
        tile(
            "clients",
            "Loading now",
            throughput.active_clients_now?.toString(),
            if (windowSeconds != null) {
                "machines the cache served in the last $windowSeconds s"
            } else {
                "machines with cache traffic in the last $windowMinutes min"
            },
        ),
    )
}

/**
 * WP LOADING-NOW-1 (web `activeWindowSeconds`): the window of
 * `active_clients_now` in seconds when it was read from the live tier
 * ([mergeLive] / [mergeNow] set it), else `null` (the swept count's window
 * is `active_window_minutes`).
 */
fun activeWindowSeconds(throughput: ThroughputOut?): Int? =
    throughput?.active_window_seconds?.takeIf { it > 0 }

/** Detail line of "Current speed": scope and window, plus the runner's part.
 * WP RUNNER-NAME-1: the runner is named by the product ([PRODUCT_NAME]). */
fun currentDetail(throughput: ThroughputOut): String {
    val liveSeconds = if (throughput.live) throughput.current?.window_seconds else null
    if (liveSeconds != null && liveSeconds > 0) {
        val liveScope = "all traffic incl. $PRODUCT_NAME, last $liveSeconds s"
        val liveRunner = formatSpeed(throughput.current?.runner_bytes_per_second)
        return if (liveRunner != null) "$liveScope · $liveRunner by $PRODUCT_NAME" else liveScope
    }
    val scope = "all traffic incl. $PRODUCT_NAME, 5-10 min window"
    val runner = formatSpeed(throughput.current?.runner_bytes_per_second)
    return if (runner != null) "$scope · $runner by $PRODUCT_NAME" else scope
}

/** Detail line of "Hit rate": the scope, and the runner's own rate (WP RUNNER-NAME-1: by product name). */
fun hitRateDetail(summary: SummaryOut?): String {
    val rate = formatRate(summary?.prefill_runner?.byte_hit_rate)
    val scope = "clients only, $PRODUCT_NAME excluded"
    return if (rate != null) "$scope ($PRODUCT_NAME: $rate)" else scope
}

/**
 * WP TILE-INTERNET: all-scope miss bytes. The server's `internet_bytes`; an
 * older server without it falls back to client misses plus the runner's
 * misses; `null` without a summary.
 */
fun internetBytes(summary: SummaryOut?): Long? {
    if (summary == null) return null
    summary.internet_bytes?.let { return it }
    val runner = summary.prefill_runner
    return if (runner != null) summary.miss_bytes + runner.miss_bytes else summary.miss_bytes
}

/**
 * WP D15-RUNNER: "X by the prefill runner" under "Loaded" (WP RUNNER-NAME-1:
 * "X by SteamHangar", [PRODUCT_NAME]); `null` when the
 * server sent no runner figures (older vault-api) or the runner loaded
 * nothing in the range.
 */
fun runnerDetail(summary: SummaryOut?): String? {
    val bytes = summary?.prefill_runner?.bytes_served ?: return null
    if (bytes <= 0) return null
    val size = formatTrafficBytes(bytes) ?: return null
    val hitBytes = summary?.prefill_runner?.hit_bytes ?: 0L
    val cached = if (hitBytes > 0) formatTrafficBytes(hitBytes) else null
    return if (cached != null) "$size by $PRODUCT_NAME, $cached of that from the cache" else "$size by $PRODUCT_NAME"
}

/** The bucket a series point covers, for the graph's label. */
fun bucketWord(bucketSeconds: Int): String = when (bucketSeconds) {
    5 -> "5 seconds"
    15 -> "15 seconds"
    60 -> "minute"
    300 -> "5 minutes"
    3600 -> "hour"
    86400 -> "day"
    else -> "${jsRound(bucketSeconds / 60.0)} minutes"
}

/** The graph's polyline in a [width] x [height] box, y growing downwards.
 * With one point the line is flat across the box. */
data class SeriesGeometry(val xs: List<Float>, val ys: List<Float>, val maxBytesPerSecond: Double)

/** Port of web `seriesGeometry` (without the SVG path strings): `null`
 * with no points. A non-finite or negative value counts as 0. */
fun seriesGeometry(points: List<SeriesPointOut>, width: Float, height: Float): SeriesGeometry? {
    if (points.isEmpty()) return null
    val values = points.map { if (finiteNonNegative(it.bytes_per_second)) it.bytes_per_second else 0.0 }
    val max = values.max()
    val n = values.size
    val xs = if (n == 1) listOf(0f, width) else values.indices.map { i -> (i.toFloat() / (n - 1)) * width }
    val ys = (if (n == 1) listOf(values[0], values[0]) else values).map { v ->
        if (max > 0) (height - (v / max) * height).toFloat() else height
    }
    return SeriesGeometry(xs, ys, max)
}

/** The graph's accessible name: what it shows and its highest value. */
fun seriesLabel(series: SeriesOut, maxBytesPerSecond: Double): String {
    val phrase = RANGE_PHRASES[series.range] ?: ""
    val per = bucketWord(series.bucket_seconds)
    return "Average speed per $per $phrase. Highest: ${formatSpeed(maxBytesPerSecond) ?: "0 MB/s"}."
}

/**
 * WP GRAPH-ZOOM: the series without its empty lead-in (web `trimSeries`). The
 * graph shows only the span that has data, starting one bucket before the
 * first non-zero point so the line rises from the floor. Trailing zeros stay.
 * An all-zero series is returned unchanged.
 */
fun trimSeries(points: List<SeriesPointOut>): List<SeriesPointOut> {
    val first = points.indexOfFirst { finiteNonNegative(it.bytes_per_second) && it.bytes_per_second > 0.0 }
    return if (first <= 0) points.toList() else points.subList(first - 1, points.size).toList()
}

/**
 * Left-edge caption for the graph: how far back the SHOWN span starts. A span
 * that starts at the range's own start keeps the plain wording ("24 h ago"); a
 * trimmed one says how long ago its first point is. "all" names the first day
 * instead of a duration.
 */
fun seriesStartText(series: SeriesOut, nowMs: Long = System.currentTimeMillis()): String {
    val first = series.points.firstOrNull()?.t
    if (series.range == "all") {
        return first?.takeIf { it.length >= 10 }?.let { "since ${it.substring(0, 10)}" } ?: ""
    }
    val fixed = when (series.range) {
        "live" -> "1 h ago"
        "1h" -> "1 h ago"
        "6h" -> "6 h ago"
        "24h" -> "24 h ago"
        "7d" -> "7 days ago"
        "30d" -> "30 days ago"
        else -> ""
    }
    val start = series.range_start
    if (first != null && start != null && first != start) return formatAgo(first, nowMs) ?: fixed
    return fixed
}

/** The graph's middle caption ("highest 40.0 MB/s"). */
fun seriesHighestText(maxBytesPerSecond: Double): String = "highest ${formatSpeed(maxBytesPerSecond) ?: "0 MB/s"}"

/** The graph's right-edge caption. */
const val SERIES_END_TEXT = "now"

/**
 * Whether the graph shows at all (web `paintGraph`): not on an old server,
 * not with the feed disabled, not for a range the user has since left, and
 * not for an all-zero series (a flat line on the floor shows nothing).
 */
fun graphGeometryToShow(
    series: SeriesOut?,
    selectedRange: String,
    unsupported: Boolean,
    width: Float,
    height: Float,
): SeriesGeometry? {
    if (unsupported || series == null || series.range != selectedRange) return null
    if (feedState(series.event_feed) == FeedState.DISABLED) return null
    val geo = seriesGeometry(series.points, width, height) ?: return null
    return if (geo.maxBytesPerSecond == 0.0) null else geo
}

/**
 * The line under a running job's card: its speed at the cache (WP D13
 * SPEED-1, `GET /v1/throughput`'s `jobs`). `null` when there is nothing
 * honest to say: no entry, or the feed not `ok` (the header's note explains
 * that).
 */
fun jobSpeedText(entry: JobThroughputOut?, state: FeedState): String? {
    if (entry == null || state != FeedState.OK) return null
    val bps = entry.bytes_per_second
        ?: return if (entry.exclusive_depots == 0) {
            if (entry.mapped_depots > 0) {
                "Speed not measured: this game's depots are shared with other apps."
            } else {
                "Speed not measured yet: the server does not know this game's depots yet."
            }
        } else {
            null
        }
    val speed = formatSpeed(bps) ?: return null
    val sofar = formatTrafficBytes(entry.bytes_since_start)
    return if (sofar != null) "$speed · $sofar loaded so far" else speed
}

// ---------------------------------------------------------------------
// Live view (GET /v1/throughput/live, WP GRAPH-LIVE)
// ---------------------------------------------------------------------

/** The live view's poll cadence (web `LIVE_POLL_MS`); only while the screen is STARTED. */
const val LIVE_POLL_MS = 3000L

/** How often the live view refreshes its persisted 1 h summary tiles (web `LIVE_SUMMARY_MS`). */
const val LIVE_SUMMARY_MS = 30_000L

/** The persisted range whose summary backs the live view's tiles. */
const val LIVE_SUMMARY_RANGE = "1h"

/**
 * The live answer as a series the graph can draw (web `liveSeries`). The
 * stitched points have 5, 15 and 60 s buckets; [seriesGeometry] spaces
 * points evenly, so every point is repeated to fill a uniform 5 s grid (a
 * 15 s point three times, a minute twelve times) and the time axis stays
 * true.
 */
fun liveSeries(live: LiveOut): SeriesOut {
    val points = ArrayList<SeriesPointOut>()
    for (p in live.points) {
        val point = SeriesPointOut(
            t = p.t,
            bytes_served = p.bytes_served,
            hit_bytes = p.hit_bytes,
            miss_bytes = p.miss_bytes,
            requests = p.requests,
            runner_bytes = p.runner_bytes,
            bytes_per_second = p.bytes_per_second,
            peak_bytes_per_second = p.peak_bytes_per_second,
            // WP RUNNER-SPLIT-1: the graph's served-from-cache band.
            runner_hit_bytes = p.runner_hit_bytes,
            runner_miss_bytes = p.runner_miss_bytes,
        )
        val repeat = maxOf(1, jsRound((if (p.bucket_seconds > 0) p.bucket_seconds else 5) / 5.0).toInt())
        repeat(repeat) { points.add(point) }
    }
    return SeriesOut(
        event_feed = live.event_feed,
        range = LIVE_RANGE,
        tier = "live",
        bucket_seconds = 5,
        range_start = live.range_start,
        points = points,
    )
}

/** A live-tier `current` (live endpoint or `now` block) in the header's shape. */
private fun LiveCurrentOut.asCurrent(): CurrentOut = CurrentOut(
    bytes_per_second = bytes_per_second,
    window_start = window_start,
    window_end = window_end,
    window_seconds = window_seconds,
    runner_bytes_per_second = runner_bytes_per_second,
    // WP RUNNER-SPLIT-1: the pill's verify-vs-download decision.
    runner_hit_bytes_per_second = runner_hit_bytes_per_second,
    runner_miss_bytes_per_second = runner_miss_bytes_per_second,
)

/**
 * The header's figures for the live view (web `mergeLive`): the persisted
 * 1 h [summary] with "Current speed" and "Loading now" taken from the live
 * tier when the live feed is `ok` (WP LIVE-DELAY-1: the persisted count lags
 * a sweep). An answer without `active_clients` (an older server) keeps the
 * summary's count. WP NOW-LOADING-1: the "Loading now" rows
 * ([ThroughputOut.loading_sources]) come from the live answer only.
 */
fun mergeLive(summary: ThroughputOut, live: LiveOut): ThroughputOut {
    val current = live.current
    // WP NOW-LOADING-1: "what is loading now" only from the live answer.
    val merged = summary.copy(range = LIVE_RANGE, live = false, loading_sources = null)
    if (current == null || feedState(live.event_feed) != FeedState.OK) return merged
    val withCurrent = merged.copy(live = true, current = current.asCurrent(), loading_sources = live.loading_sources)
    val clients = live.active_clients ?: return withCurrent
    return withCurrent.copy(
        active_clients_now = clients,
        active_window_minutes = live.active_window_minutes ?: withCurrent.active_window_minutes,
        // WP LOADING-NOW-1: the count's window is the current-speed window.
        active_window_seconds = live.active_window_seconds,
    )
}

/**
 * WP GRAPH-RECENT-1 + LOADING-NOW-1 (web `mergeNow`): a persisted range's
 * answer (`GET /v1/throughput?range=1h|6h|...`) with "Current speed", the
 * pill's runner figure and "Loading now" taken from its `now` block (the
 * live tier, one window) instead of the last sweep, so the header agrees
 * with the graph's newest point. Unchanged without a `now` block (feed not
 * `ok`, a server before rc21). Pure.
 */
fun mergeNow(throughput: ThroughputOut): ThroughputOut {
    val now = throughput.now ?: return throughput
    val current = now.current ?: return throughput
    if (feedState(throughput.event_feed) != FeedState.OK) return throughput
    var merged = throughput.copy(current = current.asCurrent(), live = true)
    now.active_clients?.let { clients ->
        merged = merged.copy(active_clients_now = clients, active_window_seconds = now.window_seconds)
    }
    now.runner_active?.let { merged = merged.copy(runner_active_now = it) }
    // WP NOW-LOADING-1: the "Loading now" rows of the same window.
    now.loading_sources?.let { merged = merged.copy(loading_sources = it) }
    return merged
}

// ---------------------------------------------------------------------
// History filter (GET /v1/jobs?range=, WP D15b)
// ---------------------------------------------------------------------

/** How many history rows the filter fetches (web `HISTORY_LIMIT`). */
const val HISTORY_LIMIT = 200

private val RANGE_MS: Map<String, Long> = mapOf(
    "24h" to 86_400_000L,
    "7d" to 7 * 86_400_000L,
    "30d" to 30 * 86_400_000L,
)
private val IN_FLIGHT = setOf("queued", "running", "paused")

private fun parseMs(iso: String?): Long? = try {
    if (iso.isNullOrBlank()) null else Instant.parse(iso).toEpochMilli()
} catch (_: java.time.DateTimeException) {
    null
}

/**
 * The server's history rule, client-side: queued/running/paused jobs always
 * stay; a finished job stays when its finish (else start, else creation) is
 * within the range. A job with no readable time stays (hiding it would be a
 * guess). "all" (or an unknown range) keeps everything.
 */
fun filterJobsByRange(jobs: List<JobSummary>, range: String, nowMs: Long = System.currentTimeMillis()): List<JobSummary> {
    val span = RANGE_MS[range] ?: return jobs.toList()
    val cutoff = nowMs - span
    return jobs.filter { job ->
        if (job.status in IN_FLIGHT) return@filter true
        val t = parseMs(job.finished_at ?: job.started_at ?: job.created_at)
        t == null || t >= cutoff
    }
}

/**
 * One job list from the screen's poll (newest 20, fresher) and the history
 * fetch (`?range=`, up to [HISTORY_LIMIT]). The same job in both keeps the
 * poll's copy; the poll's rows come first, the fetched rows it does not
 * hold follow in their own order.
 */
fun mergeJobLists(fresh: List<JobSummary>, deep: List<JobSummary>): List<JobSummary> {
    val out = fresh.toMutableList()
    val seen = out.map { it.id }.toMutableSet()
    for (job in deep) if (seen.add(job.id)) out.add(job)
    return out
}

/** The history list's empty line for a range. */
fun emptyHistoryText(range: String): String {
    val phrase = RANGE_PHRASES[range]
    return if (range == "all" || phrase == null) "Nothing finished yet." else "Nothing finished $phrase."
}
