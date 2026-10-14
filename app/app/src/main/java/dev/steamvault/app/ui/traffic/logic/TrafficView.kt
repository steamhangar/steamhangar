package dev.steamvault.app.ui.traffic.logic

import dev.steamvault.app.net.model.EventFeedOut
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.net.model.JobThroughputOut
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
 */

/** The four ranges every traffic endpoint and the jobs filter accept. */
val TRAFFIC_RANGES: List<String> = listOf("24h", "7d", "30d", "all")

/** Segmented-control labels, in [TRAFFIC_RANGES] order. */
val RANGE_LABELS: Map<String, String> = mapOf("24h" to "24 h", "7d" to "7 days", "30d" to "30 days", "all" to "All")

/** Phrases for running text ("1.2 GB loaded in the last 7 days"). */
val RANGE_PHRASES: Map<String, String> = mapOf(
    "24h" to "in the last 24 hours",
    "7d" to "in the last 7 days",
    "30d" to "in the last 30 days",
    "all" to "since recording started",
)

fun isRange(value: String?): Boolean = value in TRAFFIC_RANGES

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
data class StatTile(val key: String, val label: String, val value: String, val detail: String? = null)

/** What a screen reader says for one tile: "Peak speed: 40.0 MB/s", the
 * dash spoken as [NOT_AVAILABLE], the detail line appended. */
fun tileDescription(tile: StatTile): String {
    val value = if (tile.value == DASH) NOT_AVAILABLE else tile.value
    return if (tile.detail != null) "${tile.label}: $value, ${tile.detail}" else "${tile.label}: $value"
}

/**
 * The Downloads header's figures from `GET /v1/throughput`. `null` before
 * the first answer and with the feed disabled (the note stands alone then).
 * Labels and order are web `headerTiles`'s; the web's hover titles have no
 * Android counterpart and are not ported.
 */
fun headerTiles(throughput: ThroughputOut?): List<StatTile>? {
    if (throughput == null || feedState(throughput.event_feed) == FeedState.DISABLED) return null
    val summary = throughput.summary
    val phrase = RANGE_PHRASES[throughput.range] ?: ""
    fun tile(key: String, label: String, value: String?, detail: String? = null) = StatTile(key, label, value ?: DASH, detail)
    val inRange = summary?.active_clients
    return listOf(
        tile("current", "Current speed", formatSpeed(throughput.current?.bytes_per_second)),
        tile("average", "Average while loading", formatSpeed(summary?.average_bytes_per_second)),
        tile("peak", "Peak speed", formatSpeed(summary?.peak_bytes_per_second)),
        // Everything the cache served, the runner included; its share below.
        tile("loaded", "Loaded", formatTrafficBytes(summary?.bytes_served), runnerDetail(summary)),
        // Bytes, not requests: the same base as "Saved by the cache" next to it.
        tile("hit-rate", "Hit rate", formatRate(summary?.byte_hit_rate)),
        tile("saved", "Saved by the cache", formatTrafficBytes(summary?.hit_bytes)),
        tile(
            "clients",
            "Active clients",
            throughput.active_clients_now?.toString(),
            if (inRange == null) null else "$inRange $phrase",
        ),
    )
}

/**
 * WP D15-RUNNER: "X by the prefill runner" under "Loaded"; `null` when the
 * server sent no runner figures (older vault-api) or the runner loaded
 * nothing in the range.
 */
fun runnerDetail(summary: SummaryOut?): String? {
    val bytes = summary?.prefill_runner?.bytes_served ?: return null
    if (bytes <= 0) return null
    return formatTrafficBytes(bytes)?.let { "$it by the prefill runner" }
}

/** The bucket a series point covers, for the graph's label. */
fun bucketWord(bucketSeconds: Int): String = when (bucketSeconds) {
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

/** Left-edge caption for the graph: how far back it starts. "all" names the
 * first day instead of a duration. */
fun seriesStartText(series: SeriesOut): String = when (series.range) {
    "24h" -> "24 h ago"
    "7d" -> "7 days ago"
    "30d" -> "30 days ago"
    else -> series.points.firstOrNull()?.t?.takeIf { it.length >= 10 }?.let { "since ${it.substring(0, 10)}" } ?: ""
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
            "Speed not measured yet: the server does not know this game's depots yet."
        } else {
            null
        }
    val speed = formatSpeed(bps) ?: return null
    val sofar = formatTrafficBytes(entry.bytes_since_start)
    return if (sofar != null) "$speed · $sofar loaded so far" else speed
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
