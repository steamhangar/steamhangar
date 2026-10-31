package dev.steamvault.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Structural pins for WP ANDROID-DL-1's model-to-pixel wiring
 * (docs/LEARNINGS.md: "pinning the pure/model layer proves NOTHING about
 * the pixels"). The decisions are pinned in `DownloadsHeroTest`,
 * `TrafficViewTest`, `HistoryPagingTest` and `HistoryFilterTest`; these
 * pins prove the Downloads screen calls them. Same comment-stripped source
 * scan, strong generation, as `D15dWiringTest`: the call's nearest
 * enclosing function must be the intended one, and the message tells
 * "deleted" from "moved".
 */
class AndroidDl1WiringTest {

    private fun read(path: String): String {
        val file = File(path)
        check(file.exists()) { "expected a source file at ${file.absolutePath}" }
        return file.readText(Charsets.UTF_8)
    }

    private fun stripComments(text: String): String =
        text.replace(Regex("/\\*.*?\\*/", RegexOption.DOT_MATCHES_ALL), "")
            .replace(Regex("//.*"), "")

    private val FUN = Regex("""(?:@Composable\s+)?(?:private\s+)?fun\s+(?:<[^>]+>\s+)?(\w+)""")

    private fun nearestEnclosingFun(code: String, callIdx: Int): String? =
        FUN.findAll(code)
            .map { it.range.first to it.groupValues[1] }
            .filter { (start, _) -> start < callIdx }
            .maxByOrNull { (start, _) -> start }
            ?.second

    private fun callIndex(code: String, call: String): Int {
        var from = 0
        while (true) {
            val idx = code.indexOf(call, from)
            if (idx < 0) return -1
            if (!code.substring(0, idx).trimEnd().endsWith("fun")) return idx
            from = idx + call.length
        }
    }

    private fun assertCalledFrom(path: String, call: String, expectedFun: String) {
        val code = stripComments(read(path))
        val idx = callIndex(code, call)
        assertTrue("$path: '$call' is DELETED -- the wiring is gone", idx >= 0)
        assertEquals(
            "$path: '$call' MOVED -- its nearest enclosing function is no longer $expectedFun",
            expectedFun,
            nearestEnclosingFun(code, idx),
        )
    }

    /** The text of top-level function [name] (for bodies whose local `fun Path.trace()` fools [nearestEnclosingFun]). */
    private fun body(path: String, name: String): String {
        val code = stripComments(read(path))
        val start = code.indexOf("private fun $name(")
        assertTrue("$path: $name is DELETED", start >= 0)
        val next = Regex("""\n(?:@Composable|(?:private )?fun )""").find(code, start + 1)
        return code.substring(start, next?.range?.first ?: code.length)
    }

    private val base = "src/main/java/dev/steamvault/app"
    private val downloads = "$base/ui/downloads/DownloadsScreen.kt"
    private val stats = "$base/ui/downloads/DownloadsStats.kt"
    private val ranges = "$base/ui/traffic/TrafficRanges.kt"

    @Test
    fun `MUTATION PIN -- the default range is live and every persisted range is merged with its now block`() {
        assertTrue(
            "TrafficRanges must start the statistics on DEFAULT_STATS_RANGE (live)",
            stripComments(read(ranges)).contains("var statistics: String = DEFAULT_STATS_RANGE"),
        )
        assertCalledFrom(stats, "initialRange: String = DEFAULT_STATS_RANGE,", "downloadsStatsPoll")
        assertCalledFrom(stats, "StatsAnswer(mergeNow(repository.throughput(range)), repository.series(range))", "downloadsStatsPoll")
        assertCalledFrom(stats, "StatsAnswer(mergeLive(cached, live), liveSeries(live))", "downloadsStatsPoll")
    }

    @Test
    fun `MUTATION PIN -- the header draws the status pill from the merged current, only with figures`() {
        assertCalledFrom(stats, "val status = if (tiles != null) heroStatus(throughput?.current) else null", "DownloadsStatsHeader")
        assertCalledFrom(stats, "if (status != null) StatusPill(status)", "DownloadsStatsHeader")
        assertCalledFrom(stats, "StatusIcon(kind = status.kind, size = StatusIconSize.SMALL)", "StatusPill")
        assertCalledFrom(stats, "Text(status.text,", "StatusPill")
        assertCalledFrom(stats, "clearAndSetSemantics { contentDescription = status.text }", "StatusPill")
    }

    @Test
    fun `MUTATION PIN -- the graph draws the split bands and the legend`() {
        val graph = body(stats, "ThroughputGraph")
        assertTrue("ThroughputGraph no longer computes the split", graph.contains("val split = splitGeometry(shown.points, GRAPH_W, GRAPH_H)"))
        assertTrue("the served band is not drawn", graph.contains("trace(split.servedYs)") && graph.contains("drawPath(servedArea,"))
        assertTrue("the internet band is not drawn", graph.contains("trace(split.totalYs)") && graph.contains("drawPath(internetArea,"))
        assertTrue("the served line is not drawn in the ok colour", graph.contains("drawPath(servedLine, color = VaultColors.StatusOk"))
        assertTrue("the legend is gone", graph.contains("if (split != null) GraphLegend()"))
        val legend = body(stats, "GraphLegend")
        assertTrue("the legend words are not the web's", legend.contains("VaultColors.StatusOk to LEGEND_SERVED, VaultColors.Accent to LEGEND_INTERNET"))
    }

    @Test
    fun `MUTATION PIN -- the subline counts the ranged History list and the list pages 10 + 20`() {
        assertCalledFrom(downloads, "historySubtitle(historyFilter.loaded, finishedJobs.size, historyFilter.jobs.size)", "DownloadsScreen")
        assertCalledFrom(downloads, "text = downloadsSubtitle(partition, idleLine),", "DownloadsScreen")
        assertCalledFrom(downloads, "return if (bits.isEmpty()) idleLine else", "downloadsSubtitle")
        assertCalledFrom(
            downloads,
            "historyPage(finishedJobs.size, historyFilter.shown, capped = isHistoryCapped(historyFilter.jobs.size))",
            "DownloadsScreen",
        )
        assertCalledFrom(downloads, "finishedJobs.take(historyPaging.visible)", "DownloadsScreen")
        assertCalledFrom(downloads, "historyPaging = historyPaging,", "DownloadsScreen")
        assertCalledFrom(downloads, "if (historyPaging.next > 0) {", "DownloadsBody")
        assertCalledFrom(downloads, "historyFilter.showMore()", "DownloadsBody")
        assertCalledFrom(downloads, "Text(showMoreText(historyPaging.next))", "DownloadsBody")
        assertCalledFrom(downloads, "showMoreLabel(historyPaging.next, historyPaging.remaining)", "DownloadsBody")
        assertCalledFrom(downloads, "EmptyLine(HISTORY_CAPPED_TEXT)", "DownloadsBody")
        assertTrue(
            "the old poll-based count is back",
            !stripComments(read(downloads)).contains("partition.history.size"),
        )
    }
}
