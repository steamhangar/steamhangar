package dev.steamvault.app.ui.downloads

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import dev.steamvault.app.R
import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.net.model.SeriesOut
import dev.steamvault.app.net.model.ThroughputOut
import dev.steamvault.app.repo.TrafficRepository
import dev.steamvault.app.ui.downloads.logic.HISTORY_FIRST_PAGE
import dev.steamvault.app.ui.downloads.logic.nextShown
import dev.steamvault.app.ui.status.StatusIcon
import dev.steamvault.app.ui.status.StatusIconSize
import dev.steamvault.app.ui.theme.VaultColors
import dev.steamvault.app.ui.traffic.RangeChips
import dev.steamvault.app.ui.traffic.TrafficPoll
import dev.steamvault.app.ui.traffic.logic.DEFAULT_STATS_RANGE
import dev.steamvault.app.ui.traffic.logic.HISTORY_LIMIT
import dev.steamvault.app.ui.traffic.logic.LIVE_RANGE
import dev.steamvault.app.ui.traffic.logic.LIVE_SUMMARY_MS
import dev.steamvault.app.ui.traffic.logic.LIVE_SUMMARY_RANGE
import dev.steamvault.app.ui.traffic.logic.SERIES_END_TEXT
import dev.steamvault.app.ui.traffic.logic.STATS_RANGES
import dev.steamvault.app.ui.traffic.logic.StatTile
import dev.steamvault.app.ui.traffic.logic.feedState
import dev.steamvault.app.ui.traffic.logic.graphGeometryToShow
import dev.steamvault.app.ui.traffic.logic.headerTiles
import dev.steamvault.app.ui.traffic.logic.HeroStatus
import dev.steamvault.app.ui.traffic.logic.heroStatus
import dev.steamvault.app.ui.traffic.logic.LEGEND_INTERNET
import dev.steamvault.app.ui.traffic.logic.LEGEND_SERVED
import dev.steamvault.app.ui.traffic.logic.LoadingNowModel
import dev.steamvault.app.ui.traffic.logic.loadingNowModel
import dev.steamvault.app.ui.traffic.logic.splitGeometry
import dev.steamvault.app.ui.traffic.logic.isRange
import dev.steamvault.app.ui.traffic.logic.isStatsRange
import dev.steamvault.app.ui.traffic.logic.jobSpeedText
import dev.steamvault.app.ui.traffic.logic.liveSeries
import dev.steamvault.app.ui.traffic.logic.mergeLive
import dev.steamvault.app.ui.traffic.logic.mergeNow
import dev.steamvault.app.ui.traffic.logic.pollNote
import dev.steamvault.app.ui.traffic.logic.seriesHighestText
import dev.steamvault.app.ui.traffic.logic.seriesLabel
import dev.steamvault.app.ui.traffic.logic.seriesStartText
import dev.steamvault.app.ui.traffic.logic.trimSeries
import dev.steamvault.app.ui.traffic.logic.tileDescription
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch

/**
 * Downloads statistics header, per-job speed and history filter (WP D15d,
 * twin of the D15c block in web `views/downloads.js`). Wording and the
 * honest-state rules live in `ui/traffic/logic/TrafficView.kt`.
 */

/** One answer of the statistics poll: header figures and graph, same range. */
data class StatsAnswer(val throughput: ThroughputOut, val series: SeriesOut)

/** The header's poll, default range live (web `traffic.range`, WP
 * LIVE-DELAY-1). Figures exist unless [headerTiles] has none (feed
 * disabled). Accepts the 1 h and 6 h ranges too (WP GRAPH-ZOOM). A
 * persisted range's answer is merged with its `now` block ([mergeNow], WP
 * GRAPH-RECENT-1), so speed, pill and "Loading now" are the live tier's on
 * every range. */
fun downloadsStatsPoll(
    repository: TrafficRepository,
    initialRange: String = DEFAULT_STATS_RANGE,
    nowMs: () -> Long = System::currentTimeMillis,
): TrafficPoll<StatsAnswer> {
    // WP GRAPH-LIVE (web `fetchLive` / `liveSummary`): the live view asks the
    // live endpoint every tick and the persisted 1 h summary only every
    // LIVE_SUMMARY_MS; any other range drops the cached summary.
    var summary: ThroughputOut? = null
    var summaryAt = 0L
    return TrafficPoll(
        initialRange = initialRange,
        fetch = { range ->
            if (range == LIVE_RANGE) {
                val now = nowMs()
                val live = repository.throughputLive()
                val held = summary
                val cached = if (held == null || now - summaryAt >= LIVE_SUMMARY_MS) {
                    repository.throughput(LIVE_SUMMARY_RANGE).also {
                        summary = it
                        summaryAt = now
                    }
                } else {
                    held
                }
                StatsAnswer(mergeLive(cached, live), liveSeries(live))
            } else {
                summary = null
                StatsAnswer(mergeNow(repository.throughput(range)), repository.series(range))
            }
        },
        hasFigures = { headerTiles(it.throughput) != null },
        isValidRange = ::isStatsRange,
    )
}

/** Web "Loading statistics…" / "Could not load the statistics: ...". */
const val LOADING_STATISTICS_TEXT = "Loading statistics…"
const val COULD_NOT_LOAD_STATISTICS = "Could not load the statistics"

/**
 * The speed line under each running job's card, by job id, from the same
 * answer's `jobs[]` (WP D13 SPEED-1). Empty while the answer is for another
 * range or missing.
 */
fun jobSpeedLines(poll: TrafficPoll<StatsAnswer>): Map<Int, String> {
    val t = poll.data?.throughput ?: return emptyMap()
    if (t.range != poll.range) return emptyMap()
    val state = feedState(t.event_feed)
    return t.jobs.mapNotNull { entry -> jobSpeedText(entry, state)?.let { entry.job_id to it } }.toMap()
}

/**
 * The History list's own range (default All, web `history`): rows from
 * `GET /v1/jobs?range=&limit=200`, fetched when the screen opens and when
 * the range changes, merged with the screen's newest 20 and filtered by
 * the same rule client-side (`filterJobsByRange`), so a job that finishes
 * while the screen is open appears at once. A failed fetch keeps the
 * poll's rows (the filter still applies to them).
 *
 * WP ANDROID-DL-1 (web DOWNLOADS-3): [shown] is how many rows are on screen
 * (first page, then +20 per "Show more", `logic/HistoryPaging.kt`);
 * [loaded] says the range fetch has answered at least once (a failure
 * counts, as on the web), so the subline may count the list.
 */
class HistoryFilter(private val repository: TrafficRepository, initialRange: String = "all") {
    var range by mutableStateOf(initialRange)
        private set
    var jobs by mutableStateOf<List<JobSummary>>(emptyList())
        private set
    var shown by mutableIntStateOf(HISTORY_FIRST_PAGE)
        private set
    var loaded by mutableStateOf(false)
        private set
    private var gen by mutableIntStateOf(0)

    suspend fun load() {
        val mine = ++gen
        val asked = range
        val items = try {
            repository.jobHistory(asked, HISTORY_LIMIT)
        } catch (_: VaultApiError) {
            emptyList()
        }
        if (mine != gen) return // a newer range was chosen meanwhile
        jobs = items
        loaded = true
    }

    fun selectRange(scope: CoroutineScope, newRange: String) {
        if (!isRange(newRange) || newRange == range) return
        range = newRange
        jobs = emptyList()
        shown = HISTORY_FIRST_PAGE
        loaded = false
        scope.launch { load() }
    }

    /** "Show N more": 20 more rows. */
    fun showMore() {
        shown = nextShown(shown)
    }
}

/**
 * The header under the bulk bar: range chips (hidden without figures), the
 * note, the eight figures in a two-column grid, and the graph. Nothing in
 * here is focusable but the chips.
 */
@Composable
fun DownloadsStatsHeader(
    poll: TrafficPoll<StatsAnswer>,
    scope: CoroutineScope,
    rateCap: String? = null,
    onRangeChosen: (String) -> Unit = {},
    nowMs: () -> Long = System::currentTimeMillis,
) {
    val answer = poll.data
    val throughput = answer?.throughput
    val note = pollNote(
        unsupported = poll.unsupported,
        error = poll.error,
        feed = throughput?.event_feed,
        hasData = throughput != null,
        couldNotLoad = COULD_NOT_LOAD_STATISTICS,
        loadingText = LOADING_STATISTICS_TEXT,
    )
    val tiles = if (poll.unsupported) null else headerTiles(throughput, rateCap)
    // WP ANDROID-DL-1 (web `heroModel`): the pill shows only with figures.
    val status = if (tiles != null) heroStatus(throughput?.current) else null
    // WP ANDROID-DL-2 (web NOW-LOADING-1): what is loading, under the pill.
    val loadingNow = if (tiles != null) loadingNowModel(throughput) else null
    Column(modifier = Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        // No figures (feed disabled, old server): no range to choose either.
        if (!poll.noFigures) {
            RangeChips(
                label = stringResource(R.string.traffic_range_statistics),
                selected = poll.range,
                onSelect = {
                    poll.selectRange(scope, it)
                    onRangeChosen(poll.range)
                },
                ranges = STATS_RANGES,
            )
        }
        if (note != null) {
            Text(
                text = note,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.fillMaxWidth().semantics { liveRegion = LiveRegionMode.Polite },
            )
        }
        if (status != null) StatusPill(status)
        if (loadingNow != null) LoadingNowBlock(loadingNow)
        if (tiles != null) StatTiles(tiles)
        ThroughputGraph(series = answer?.series, selectedRange = poll.range, unsupported = poll.unsupported, nowMs = nowMs)
    }
}

/**
 * WP ANDROID-DL-1: the status pill (web hero `stats-pill`): Serving /
 * Downloading / Verifying / Idle with its status icon. TalkBack reads the
 * word once; the icon's own kind label ("Cached" for Serving) would
 * contradict it.
 */
@Composable
private fun StatusPill(status: HeroStatus) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier.clearAndSetSemantics { contentDescription = status.text },
    ) {
        StatusIcon(kind = status.kind, size = StatusIconSize.SMALL)
        Text(status.text, style = MaterialTheme.typography.labelMedium, modifier = Modifier.padding(start = 4.dp))
    }
}

/**
 * WP ANDROID-DL-2 (web hero `stats-loading-now`, NOW-LOADING-1): "Loading
 * now", one row per source of the traffic in the live window -- name in
 * bold, what it loads under it, its speed on the right. Absent while nothing
 * loads ([loadingNowModel] is `null`), so there is never an empty heading.
 * The label is a heading; each row is one TalkBack stop that reads name,
 * detail and speed.
 */
@Composable
private fun LoadingNowBlock(model: LoadingNowModel) {
    Column(modifier = Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(
            model.label,
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.semantics { heading() },
        )
        for (row in model.rows) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier.fillMaxWidth().semantics(mergeDescendants = true) {},
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(row.name, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.SemiBold)
                    Text(row.detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                Text(row.speed, style = MaterialTheme.typography.labelMedium, modifier = Modifier.padding(start = 8.dp))
            }
        }
    }
}

@Composable
private fun StatTiles(tiles: List<StatTile>) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        for (pair in tiles.chunked(2)) {
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp), modifier = Modifier.fillMaxWidth()) {
                for (tile in pair) StatTileView(tile, Modifier.weight(1f))
                if (pair.size == 1) Spacer(Modifier.weight(1f))
            }
        }
    }
}

/** One figure. TalkBack reads [tileDescription] ("Peak speed: 40.0 MB/s";
 * the dash as "Not available") instead of the visual parts. */
@Composable
private fun StatTileView(tile: StatTile, modifier: Modifier = Modifier) {
    val description = tileDescription(tile)
    Card(modifier = modifier.clearAndSetSemantics { contentDescription = description }) {
        Column(modifier = Modifier.padding(horizontal = 10.dp, vertical = 8.dp)) {
            Text(tile.label, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(tile.value, style = MaterialTheme.typography.titleMedium)
            tile.detail?.let {
                Text(it, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            tile.limit?.let { // WP RATE-TILE
                Text(it, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

/** WP ANDROID-DL-1: the split graph's legend (web `graph-legend`), decoration
 * like the axis captions. */
@Composable
private fun GraphLegend() {
    Row(
        modifier = Modifier.fillMaxWidth().clearAndSetSemantics {},
        horizontalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        for ((color, text) in listOf(VaultColors.StatusOk to LEGEND_SERVED, VaultColors.Accent to LEGEND_INTERNET)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(8.dp).background(color, CircleShape))
                Text(
                    text,
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(start = 6.dp),
                )
            }
        }
    }
}

private const val GRAPH_W = 300f
private const val GRAPH_H = 60f

/**
 * The throughput graph (web `paintGraph`): a filled line over the range,
 * oldest left. Shown only when [graphGeometryToShow] says so. The canvas
 * is an image named by [seriesLabel]; the captions under it are
 * decoration (web `aria-hidden`), the label already says the same.
 *
 * WP ANDROID-DL-1 (web WEB-FIX-20.2 + RUNNER-SPLIT-1): two stacked bands,
 * "served from cache" (green, client HITs plus the runner's cache reads)
 * under "from the internet" (accent), with a small legend ([splitGeometry]).
 */
@Composable
private fun ThroughputGraph(series: SeriesOut?, selectedRange: String, unsupported: Boolean, nowMs: () -> Long) {
    // WP GRAPH-ZOOM: only the span that has data; the axis caption follows it.
    val shown = series?.let { it.copy(points = trimSeries(it.points)) } ?: return
    val geo = graphGeometryToShow(shown, selectedRange, unsupported, GRAPH_W, GRAPH_H) ?: return
    val label = seriesLabel(shown, geo.maxBytesPerSecond)
    val split = splitGeometry(shown.points, GRAPH_W, GRAPH_H)
    Column(modifier = Modifier.fillMaxWidth()) {
        Canvas(
            modifier = Modifier
                .fillMaxWidth()
                .height(72.dp)
                .semantics {
                    contentDescription = label
                    role = Role.Image
                },
        ) {
            val sx = size.width / GRAPH_W
            val sy = size.height / GRAPH_H
            fun Path.trace(ys: List<Float>) = geo.xs.forEachIndexed { i, x ->
                if (i == 0) moveTo(x * sx, ys[i] * sy) else lineTo(x * sx, ys[i] * sy)
            }
            val stroke = Stroke(width = 1.5.dp.toPx(), join = StrokeJoin.Round)
            if (split == null) {
                val line = Path().apply { trace(geo.ys) }
                val area = Path().apply {
                    trace(geo.ys)
                    lineTo(geo.xs.last() * sx, size.height)
                    lineTo(geo.xs.first() * sx, size.height)
                    close()
                }
                drawPath(area, color = VaultColors.AccentGlow)
                drawPath(line, color = VaultColors.Accent, style = stroke)
            } else {
                // Served at the bottom, the internet band on top; lines over fills.
                val totalLine = Path().apply { trace(split.totalYs) }
                val servedLine = Path().apply { trace(split.servedYs) }
                val internetArea = Path().apply {
                    trace(split.totalYs)
                    for (i in split.xs.indices.reversed()) lineTo(split.xs[i] * sx, split.servedYs[i] * sy)
                    close()
                }
                val servedArea = Path().apply {
                    trace(split.servedYs)
                    lineTo(split.xs.last() * sx, size.height)
                    lineTo(split.xs.first() * sx, size.height)
                    close()
                }
                drawPath(internetArea, color = VaultColors.AccentGlow)
                drawPath(servedArea, color = VaultColors.StatusOk.copy(alpha = 0.16f))
                drawPath(totalLine, color = VaultColors.Accent, style = stroke)
                drawPath(servedLine, color = VaultColors.StatusOk, style = stroke)
            }
        }
        Row(
            modifier = Modifier.fillMaxWidth().clearAndSetSemantics {},
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            for (text in listOf(seriesStartText(shown, nowMs()), seriesHighestText(geo.maxBytesPerSecond), SERIES_END_TEXT)) {
                Text(text, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        if (split != null) GraphLegend()
    }
}
