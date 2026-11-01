package dev.steamvault.app.ui.downloads

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.HorizontalDivider
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.sp
import dev.steamvault.app.ui.status.StatusKind
import dev.steamvault.app.ui.traffic.logic.HERO_LABEL
import dev.steamvault.app.ui.traffic.logic.HeroFigures
import dev.steamvault.app.ui.traffic.logic.HeroMini
import dev.steamvault.app.ui.traffic.logic.HeroPart
import dev.steamvault.app.ui.traffic.logic.HeroTile
import dev.steamvault.app.ui.traffic.logic.LOADING_KIND_UNATTRIBUTED
import dev.steamvault.app.ui.traffic.logic.heroFigures
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
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
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
import dev.steamvault.app.ui.traffic.logic.feedState
import dev.steamvault.app.ui.traffic.logic.graphGeometryToShow
import dev.steamvault.app.ui.traffic.logic.headerTiles
import dev.steamvault.app.ui.traffic.logic.HeroStatus
import dev.steamvault.app.ui.traffic.logic.LEGEND_INTERNET
import dev.steamvault.app.ui.traffic.logic.LEGEND_SERVED
import dev.steamvault.app.ui.traffic.logic.LoadingNowModel
import dev.steamvault.app.ui.traffic.logic.loadingNowModel
import dev.steamvault.app.ui.traffic.logic.splitGeometry
import dev.steamvault.app.ui.traffic.logic.isRange
import dev.steamvault.app.ui.traffic.logic.isStatsRange
import dev.steamvault.app.ui.traffic.logic.EtaSmoother
import dev.steamvault.app.ui.traffic.logic.jobSpeedNote
import dev.steamvault.app.ui.traffic.logic.jobSpeedText
import dev.steamvault.app.ui.traffic.logic.liveSeries
import dev.steamvault.app.ui.traffic.logic.mergeLive
import dev.steamvault.app.ui.traffic.logic.mergeNow
import dev.steamvault.app.ui.traffic.logic.pollNote
import dev.steamvault.app.ui.traffic.logic.seriesHighestText
import dev.steamvault.app.ui.traffic.logic.seriesLabel
import dev.steamvault.app.ui.traffic.logic.seriesStartText
import dev.steamvault.app.ui.traffic.logic.trimSeries
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

/** One job card's speed line and its note (web: the line's `title`, here
 * read by TalkBack after the line, WP JOB-SIZE-1 `jobSpeedNote`). */
data class JobSpeedLine(val text: String, val note: String)

/**
 * The speed line under each running job's card, by job id, from the same
 * answer's `jobs[]` (WP D13 SPEED-1). Empty while the answer is for another
 * range or missing.
 *
 * WP JOB-SIZE-1 (web `paintJobSpeeds`): one EMA step of [smoother] per new
 * answer (identity-guarded, so a recomposition is no sample), the smoothed
 * rate feeds the ETA, and no ETA on a card in [verifyingJobIds].
 */
fun jobSpeedLines(
    poll: TrafficPoll<StatsAnswer>,
    verifyingJobIds: Set<Int> = emptySet(),
    smoother: EtaSmoother? = null,
    nowMs: Long = System.currentTimeMillis(),
): Map<Int, JobSpeedLine> {
    val t = poll.data?.throughput ?: return emptyMap()
    if (t.range != poll.range) return emptyMap()
    smoother?.observe(t)
    val state = feedState(t.event_feed)
    return t.jobs.mapNotNull { entry ->
        val text = jobSpeedText(
            entry,
            state,
            verifying = entry.job_id in verifyingJobIds,
            nowMs = nowMs,
            etaRate = if (smoother != null) smoother.rate(entry.job_id) else entry.eta_bytes_per_second,
        )
        text?.let { entry.job_id to JobSpeedLine(it, jobSpeedNote(entry)) }
    }.toMap()
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
 * The Downloads hero (WP ANDROID-REDESIGN-1c, twin of web
 * `components/downloads-hero.js` at one column, docs/design/rc20/
 * downloads.html): one surface card with the "Throughput" label and the
 * range chips (hidden without figures), the note, the big current speed
 * with the status pill, the caption (limit + devices loading), "Loading
 * now", the graph, the mini line (average, peak, the runner's share) and
 * the four totals 2x2 ([HERO_TILES]). Figures and words come from
 * [heroFigures]; nothing in here is focusable but the chips.
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
    // Web `heroModel`: no figures before an answer, with the feed off or for an old server.
    val hero = heroFigures(throughput, rateCap, poll.unsupported)
    // WP ANDROID-DL-2 (web NOW-LOADING-1): what is loading, under the number.
    val loadingNow = if (hero != null) loadingNowModel(throughput) else null
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(VaultColors.Surface, RoundedCornerShape(16.dp))
            .border(1.dp, VaultColors.LineSoft, RoundedCornerShape(16.dp))
            .padding(horizontal = 16.dp, vertical = 14.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        SectionLabel(HERO_LABEL)
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
        if (hero != null) HeroLive(hero)
        if (loadingNow != null) LoadingNowBlock(loadingNow)
        ThroughputGraph(series = answer?.series, selectedRange = poll.range, unsupported = poll.unsupported, nowMs = nowMs)
        if (hero != null) {
            HeroMiniLine(hero.mini)
            HeroTiles(hero.tiles)
        }
    }
}

/** The kit's uppercase section label (web `.sec`), a heading for TalkBack. */
@Composable
internal fun SectionLabel(text: String, modifier: Modifier = Modifier) {
    Text(
        text.uppercase(),
        style = MaterialTheme.typography.labelMedium,
        fontWeight = FontWeight.Medium,
        letterSpacing = 0.9.sp,
        color = VaultColors.Dim,
        modifier = modifier.semantics { heading() },
    )
}

/**
 * The live block (web `.live .lv`): the current speed as the big number
 * with its unit, the status pill beside it, and the caption line (the
 * limit in bold, how many devices load). TalkBack reads the number as the
 * "Current speed" figure and the caption as the limit and "Loading now".
 */
@Composable
private fun HeroLive(hero: HeroFigures) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(
                text = buildAnnotatedString {
                    append(hero.big.value)
                    if (hero.big.unit.isNotEmpty()) {
                        withStyle(SpanStyle(fontSize = 16.sp, color = VaultColors.Dim, fontWeight = FontWeight.Normal)) {
                            append(" " + hero.big.unit)
                        }
                    }
                },
                fontSize = 32.sp,
                fontWeight = FontWeight.Medium,
                letterSpacing = (-1).sp,
                maxLines = 1,
                modifier = Modifier.clearAndSetSemantics { contentDescription = hero.bigDescription },
            )
            if (hero.status != null) StatusPill(hero.status)
        }
        val caption = hero.captionDescription
        if (hero.caption.isNotEmpty() && caption != null) {
            Text(
                text = partsText(hero.caption),
                style = MaterialTheme.typography.bodySmall,
                color = VaultColors.Dim,
                modifier = Modifier.clearAndSetSemantics { contentDescription = caption },
            )
        }
    }
}

/** Parts with their bold runs in the text colour (web `fillParts` + `.hcap b`). */
private fun partsText(parts: List<HeroPart>): AnnotatedString = buildAnnotatedString {
    for (part in parts) {
        if (part.bold) {
            withStyle(SpanStyle(color = VaultColors.Text, fontWeight = FontWeight.Medium)) { append(part.text) }
        } else {
            append(part.text)
        }
    }
}

/**
 * WP ANDROID-DL-1: the status pill (web hero `stats-pill`, kit `.pill`):
 * Serving / Downloading / Verifying / Idle with its status icon on a
 * tinted capsule. TalkBack reads the word once; the icon's own kind label
 * ("Cached" for Serving) would contradict it.
 */
@Composable
private fun StatusPill(status: HeroStatus) {
    val tone = when (status.kind) {
        StatusKind.CACHED -> VaultColors.StatusOk
        StatusKind.RUNNING, StatusKind.VERIFY -> VaultColors.StatusRun
        else -> VaultColors.Dim
    }
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier
            .background(if (tone == VaultColors.Dim) VaultColors.Raised2 else tone.copy(alpha = 0.14f), RoundedCornerShape(percent = 50))
            .padding(start = 4.dp, end = 10.dp, top = 3.dp, bottom = 3.dp)
            .clearAndSetSemantics { contentDescription = status.text },
    ) {
        StatusIcon(kind = status.kind, size = StatusIconSize.SMALL)
        Text(status.text, style = MaterialTheme.typography.labelMedium, color = tone, modifier = Modifier.padding(start = 6.dp))
    }
}

/**
 * WP ANDROID-DL-2 (web hero `stats-loading-now`, NOW-LOADING-1): "Loading
 * now", one row per source of the traffic in the live window -- name in
 * bold, what it loads under it, its speed on the right, on the kit's
 * accent-tinted row (the unattributed row dashed-neutral there, plain here).
 * Absent while nothing loads ([loadingNowModel] is `null`), so there is
 * never an empty heading. The label is a heading; each row is one TalkBack
 * stop that reads name, detail and speed.
 */
@Composable
private fun LoadingNowBlock(model: LoadingNowModel) {
    Column(modifier = Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        SectionLabel(model.label, Modifier)
        for (row in model.rows) {
            val attributed = row.kind != LOADING_KIND_UNATTRIBUTED
            Row(
                verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier
                    .fillMaxWidth()
                    .background(if (attributed) VaultColors.Accent.copy(alpha = 0.08f) else Color.Transparent, RoundedCornerShape(12.dp))
                    .border(1.dp, if (attributed) VaultColors.Accent.copy(alpha = 0.30f) else VaultColors.Line, RoundedCornerShape(12.dp))
                    .padding(horizontal = 12.dp, vertical = 8.dp)
                    .semantics(mergeDescendants = true) {},
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(row.name, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.SemiBold)
                    Text(row.detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                Text(row.speed, style = MaterialTheme.typography.labelMedium, fontFamily = FontFamily.Monospace,
                    color = if (attributed) VaultColors.Accent else VaultColors.Dim,
                    modifier = Modifier.padding(start = 8.dp),
                )
            }
        }
    }
}

/** The mini line (web `.mini`): average, peak and the runner's share over a hairline. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun HeroMiniLine(items: List<HeroMini>) {
    Column(modifier = Modifier.fillMaxWidth()) {
        HorizontalDivider(color = VaultColors.LineSoft)
        FlowRow(
            modifier = Modifier.fillMaxWidth().padding(top = 10.dp),
            horizontalArrangement = Arrangement.spacedBy(18.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            for (item in items) {
                Text(
                    partsText(item.parts),
                    style = MaterialTheme.typography.bodySmall,
                    color = VaultColors.Dim,
                    modifier = Modifier.clearAndSetSemantics { contentDescription = item.description },
                )
            }
        }
    }
}

/** The four totals, 2x2 (web `.hero .tiles` at one column). */
@Composable
private fun HeroTiles(tiles: List<HeroTile>) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        for (pair in tiles.chunked(2)) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth().height(IntrinsicSize.Min)) {
                for (tile in pair) HeroTileView(tile, Modifier.weight(1f).fillMaxHeight())
                if (pair.size == 1) Spacer(Modifier.weight(1f))
            }
        }
    }
}

/** One kit tile (`.hero .tile`): label, value with its small unit, then the
 * caption or, on the hit rate, the meter. TalkBack reads the figure's
 * [tileDescription] ("Hit rate: 83%, clients only, ..."). */
@Composable
private fun HeroTileView(tile: HeroTile, modifier: Modifier = Modifier) {
    Column(
        modifier = modifier
            .background(VaultColors.Raised, RoundedCornerShape(12.dp))
            .padding(horizontal = 14.dp, vertical = 12.dp)
            .clearAndSetSemantics { contentDescription = tile.description },
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Text(
            tile.label.uppercase(),
            style = MaterialTheme.typography.labelSmall,
            letterSpacing = 0.8.sp,
            color = VaultColors.Dim,
        )
        Text(
            buildAnnotatedString {
                append(tile.figure.value)
                if (tile.figure.unit.isNotEmpty()) {
                    withStyle(SpanStyle(fontSize = 12.sp, color = VaultColors.Dim, fontWeight = FontWeight.Normal)) {
                        append(" " + tile.figure.unit)
                    }
                }
            },
            fontSize = 22.sp,
            fontWeight = FontWeight.Medium,
            maxLines = 1,
        )
        val meter = tile.meter
        if (meter != null) {
            Box(Modifier.fillMaxWidth().padding(top = 2.dp).height(4.dp).background(VaultColors.Raised2, RoundedCornerShape(2.dp))) {
                Box(Modifier.fillMaxWidth(meter).height(4.dp).background(VaultColors.StatusOk, RoundedCornerShape(2.dp)))
            }
        } else if (tile.caption != null) {
            Text(tile.caption, style = MaterialTheme.typography.labelSmall, color = VaultColors.Dim)
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

/** Fill opacities of the split graph's two bands (WP ANDROID-REDESIGN-1b). */
internal const val SERVED_FILL_ALPHA = 0.34f
internal const val INTERNET_FILL_ALPHA = 0.10f

private const val GRAPH_W = 300f

/** The kit graph's grid lines (web `GRAPH_GRID` 24/48/72 of 96). */
private val GRAPH_GRID_FRACTIONS = listOf(0.25f, 0.5f, 0.75f)
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
                .height(88.dp)
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
            // WP ANDROID-REDESIGN-1c (kit `.graph .grid`): hairlines at a quarter, half and three quarters.
            for (f in GRAPH_GRID_FRACTIONS) {
                val y = size.height * f
                drawLine(VaultColors.LineSoft, Offset(0f, y), Offset(size.width, y), strokeWidth = 1.dp.toPx())
            }
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
                // WP ANDROID-REDESIGN-1b (ANDROID-DL-1 review nit): the web's
                // 0.12/0.16 fills blend into one dark band on a phone; a
                // stronger served fill and a fainter internet fill keep the
                // two bands apart (the lines keep the web colours).
                drawPath(internetArea, color = VaultColors.Accent.copy(alpha = INTERNET_FILL_ALPHA))
                drawPath(servedArea, color = VaultColors.StatusOk.copy(alpha = SERVED_FILL_ALPHA))
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
