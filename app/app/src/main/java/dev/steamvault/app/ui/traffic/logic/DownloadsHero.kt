package dev.steamvault.app.ui.traffic.logic

import dev.steamvault.app.PRODUCT_NAME
import dev.steamvault.app.net.model.CurrentOut
import dev.steamvault.app.net.model.LoadingAppOut
import dev.steamvault.app.net.model.LoadingSourceOut
import dev.steamvault.app.net.model.SeriesPointOut
import dev.steamvault.app.net.model.SummaryOut
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

/**
 * WP RUNNER-NAME-1: the runner row is titled [PRODUCT_NAME] (not the
 * server's "Prefill runner") and its detail starts with this, so it is
 * clear that the server itself loads (web `LOADING_RUNNER_DETAIL`).
 */
const val LOADING_RUNNER_DETAIL = "this server"

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
 *  - the prefill runner, titled [PRODUCT_NAME] with [LOADING_RUNNER_DETAIL]
 *    first in its detail (WP RUNNER-NAME-1): its job's game ([LOADING_NO_JOB] when the runner
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
                rows.add(LoadingNowRow("runner", LOADING_KIND_RUNNER, PRODUCT_NAME, (listOf(LOADING_RUNNER_DETAIL) + parts).joinToString(" · "), speed))
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

// ---------------------------------------------------------------------
// The hero's anatomy (WP ANDROID-REDESIGN-1c, twin of web `heroModel`)
// ---------------------------------------------------------------------

/** The hero's section label (web `HERO_LABEL`). */
const val HERO_LABEL = "Throughput"

/** One of the four hero tiles: which [headerTiles] figure, its kit label and caption (web `HERO_TILES`). */
data class HeroTileSpec(val key: String, val label: String, val caption: String?)

/** The four totals, 2x2 (web `HERO_TILES`, pinned by `DownloadsHeroWebTwinTest`). */
val HERO_TILES: List<HeroTileSpec> = listOf(
    HeroTileSpec("loaded", "Served", "cache + prefill"),
    HeroTileSpec("internet", "From internet", "not in cache"),
    HeroTileSpec("saved", "Saved by cache", "instead of internet"),
    HeroTileSpec("hit-rate", "Hit rate", null),
)

/** A formatted figure as number + unit (web `splitFigure`); the dash keeps no unit. */
data class HeroFigure(val value: String, val unit: String)

fun splitFigure(text: String?): HeroFigure {
    if (text.isNullOrEmpty() || text == DASH) return HeroFigure(DASH, "")
    if (text.endsWith("%")) return HeroFigure(text.dropLast(1), "%")
    val i = text.lastIndexOf(' ')
    return if (i > 0) HeroFigure(text.substring(0, i), text.substring(i + 1)) else HeroFigure(text, "")
}

/** A run of text; [bold] marks the kit's `<b>` figures. */
data class HeroPart(val text: String, val bold: Boolean = false)

/** "N devices loading" (web `devicesLoading`): only with a fresh feed and a known count; zero reads "no device loading". */
fun devicesLoadingText(throughput: ThroughputOut?): String? {
    if (throughput == null || feedState(throughput.event_feed) != FeedState.OK) return null
    val n = throughput.active_clients_now ?: return null
    if (n < 0) return null
    if (n == 0) return "no device loading"
    return "$n ${if (n == 1) "device" else "devices"} loading"
}

private val LIMIT_RE = Regex("^(Limit )(\\S+ \\S+?)(,.*)?$")

/** "Limit 8 MB/s, all day" -> parts with the speed in bold (web `limitParts`). */
fun limitParts(rateCap: String): List<HeroPart> {
    val m = LIMIT_RE.find(rateCap) ?: return listOf(HeroPart(rateCap))
    return listOf(HeroPart(m.groupValues[1]), HeroPart(m.groupValues[2], bold = true), HeroPart(m.groupValues[3]))
}

/** The runner's share for the mini line (web `runnerParts`), `null` when the server sent none.
 * WP RUNNER-NAME-1: named by the product ("SteamHangar 23.0 GB of it"). */
fun runnerParts(summary: SummaryOut?): List<HeroPart>? {
    val runner = summary?.prefill_runner ?: return null
    if (runner.bytes_served <= 0) return null
    val size = formatTrafficBytes(runner.bytes_served) ?: return null
    val cached = if (runner.hit_bytes > 0) formatTrafficBytes(runner.hit_bytes) else null
    val who = "$PRODUCT_NAME "
    return if (cached != null) {
        listOf(HeroPart(who), HeroPart(size, bold = true), HeroPart(" of it, "), HeroPart(cached, bold = true), HeroPart(" from the cache"))
    } else {
        listOf(HeroPart(who), HeroPart(size, bold = true), HeroPart(" of it"))
    }
}

/** One item of the mini line; [description] is what TalkBack reads. */
data class HeroMini(val key: String, val parts: List<HeroPart>, val description: String)

/** One hero tile; [meter] (0..1) only on the hit rate with a value. */
data class HeroTile(
    val key: String,
    val label: String,
    val caption: String?,
    val figure: HeroFigure,
    val description: String,
    val meter: Float?,
)

/**
 * Everything the hero shows below its note (web `heroModel().figures`):
 * the big number with the status pill, the caption (limit + devices
 * loading), the mini line (average, peak, the runner's share) and the four
 * tiles. `null` without figures ([headerTiles] `null`: no answer yet, feed
 * disabled; or [unsupported]). TalkBack keeps the established figure words:
 * each piece reads its [headerTiles] tile ([tileDescription], "Peak speed:
 * 40.0 MB/s"), the devices caption the "Loading now" tile's.
 */
data class HeroFigures(
    val big: HeroFigure,
    val bigDescription: String,
    val status: HeroStatus?,
    val caption: List<HeroPart>,
    val captionDescription: String?,
    val mini: List<HeroMini>,
    val tiles: List<HeroTile>,
)

fun heroFigures(throughput: ThroughputOut?, rateCap: String?, unsupported: Boolean = false): HeroFigures? {
    if (unsupported) return null
    val tiles = headerTiles(throughput, rateCap) ?: return null
    val t = throughput ?: return null
    val byKey = tiles.associateBy { it.key }
    val current = byKey.getValue("current")
    val devices = devicesLoadingText(t)
    val caption = ArrayList<HeroPart>()
    if (rateCap != null) caption.addAll(limitParts(rateCap))
    if (devices != null) {
        if (caption.isNotEmpty()) caption.add(HeroPart(" · "))
        caption.add(HeroPart(devices))
    }
    val captionDescription = listOfNotNull(
        rateCap,
        if (devices != null) tileDescription(byKey.getValue("clients")) else null,
    ).joinToString(". ").ifEmpty { null }
    val average = byKey.getValue("average")
    val peak = byKey.getValue("peak")
    val mini = ArrayList<HeroMini>()
    mini.add(HeroMini("average", listOf(HeroPart("Avg while loading "), HeroPart(average.value, bold = true)), tileDescription(average)))
    mini.add(HeroMini("peak", listOf(HeroPart("Peak "), HeroPart(peak.value, bold = true)), tileDescription(peak)))
    runnerParts(t.summary)?.let { parts -> mini.add(HeroMini("runner", parts, parts.joinToString("") { it.text })) }
    val rate = t.summary?.byte_hit_rate
    val meter = if (rate != null && rate.isFinite() && rate >= 0.0) minOf(rate, 1.0).toFloat() else null
    return HeroFigures(
        big = splitFigure(current.value),
        bigDescription = tileDescription(current),
        status = heroStatus(t.current),
        caption = caption,
        captionDescription = captionDescription,
        mini = mini,
        tiles = HERO_TILES.map { spec ->
            val tile = byKey.getValue(spec.key)
            HeroTile(
                key = spec.key,
                label = spec.label,
                caption = spec.caption,
                figure = splitFigure(tile.value),
                description = tileDescription(tile),
                meter = if (spec.key == "hit-rate" && tile.value != DASH) meter else null,
            )
        },
    )
}
