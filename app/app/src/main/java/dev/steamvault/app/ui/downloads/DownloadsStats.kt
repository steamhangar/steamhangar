package dev.steamvault.app.ui.downloads

import androidx.compose.foundation.Canvas
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
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import dev.steamvault.app.R
import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.net.model.SeriesOut
import dev.steamvault.app.net.model.ThroughputOut
import dev.steamvault.app.repo.TrafficRepository
import dev.steamvault.app.ui.theme.VaultColors
import dev.steamvault.app.ui.traffic.RangeChips
import dev.steamvault.app.ui.traffic.TrafficPoll
import dev.steamvault.app.ui.traffic.logic.DEFAULT_STATS_RANGE
import dev.steamvault.app.ui.traffic.logic.HISTORY_LIMIT
import dev.steamvault.app.ui.traffic.logic.SERIES_END_TEXT
import dev.steamvault.app.ui.traffic.logic.STATS_RANGES
import dev.steamvault.app.ui.traffic.logic.StatTile
import dev.steamvault.app.ui.traffic.logic.feedState
import dev.steamvault.app.ui.traffic.logic.graphGeometryToShow
import dev.steamvault.app.ui.traffic.logic.headerTiles
import dev.steamvault.app.ui.traffic.logic.isRange
import dev.steamvault.app.ui.traffic.logic.isStatsRange
import dev.steamvault.app.ui.traffic.logic.jobSpeedText
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

/** The header's poll, default range 6 h (web `traffic.range`). Figures
 * exist unless [headerTiles] has none (feed disabled). Accepts the 1 h and
 * 6 h ranges too (WP GRAPH-ZOOM). */
fun downloadsStatsPoll(
    repository: TrafficRepository,
    initialRange: String = DEFAULT_STATS_RANGE,
): TrafficPoll<StatsAnswer> = TrafficPoll(
    initialRange = initialRange,
    fetch = { range -> StatsAnswer(repository.throughput(range), repository.series(range)) },
    hasFigures = { headerTiles(it.throughput) != null },
    isValidRange = ::isStatsRange,
)

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
 */
class HistoryFilter(private val repository: TrafficRepository, initialRange: String = "all") {
    var range by mutableStateOf(initialRange)
        private set
    var jobs by mutableStateOf<List<JobSummary>>(emptyList())
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
    }

    fun selectRange(scope: CoroutineScope, newRange: String) {
        if (!isRange(newRange) || newRange == range) return
        range = newRange
        jobs = emptyList()
        scope.launch { load() }
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
    onRangeChosen: (String) -> Unit = {},
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
    val tiles = if (poll.unsupported) null else headerTiles(throughput)
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
        if (tiles != null) StatTiles(tiles)
        ThroughputGraph(series = answer?.series, selectedRange = poll.range, unsupported = poll.unsupported)
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
 */
@Composable
private fun ThroughputGraph(series: SeriesOut?, selectedRange: String, unsupported: Boolean) {
    // WP GRAPH-ZOOM: only the span that has data; the axis caption follows it.
    val shown = series?.let { it.copy(points = trimSeries(it.points)) } ?: return
    val geo = graphGeometryToShow(shown, selectedRange, unsupported, GRAPH_W, GRAPH_H) ?: return
    val label = seriesLabel(shown, geo.maxBytesPerSecond)
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
            fun Path.trace() = geo.xs.forEachIndexed { i, x ->
                if (i == 0) moveTo(x * sx, geo.ys[i] * sy) else lineTo(x * sx, geo.ys[i] * sy)
            }
            val line = Path().apply { trace() }
            val area = Path().apply {
                trace()
                lineTo(geo.xs.last() * sx, size.height)
                lineTo(geo.xs.first() * sx, size.height)
                close()
            }
            drawPath(area, color = VaultColors.AccentGlow)
            drawPath(line, color = VaultColors.Accent, style = Stroke(width = 1.5.dp.toPx(), join = StrokeJoin.Round))
        }
        Row(
            modifier = Modifier.fillMaxWidth().clearAndSetSemantics {},
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            for (text in listOf(seriesStartText(shown, System.currentTimeMillis()), seriesHighestText(geo.maxBytesPerSecond), SERIES_END_TEXT)) {
                Text(text, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}
