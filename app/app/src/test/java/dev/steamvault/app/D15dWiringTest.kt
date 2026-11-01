package dev.steamvault.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Structural pins for WP D15d's model-to-pixel wiring (docs/LEARNINGS.md:
 * "pinning the pure/model layer proves NOTHING about the pixels"). The
 * decisions are pinned in `TrafficViewTest`, `ClientsPageTest` and
 * `TrafficPollTest`; these pins prove the screens call them. Same
 * comment-stripped source scan, strong generation, as `Upd3WiringTest`: the
 * call's nearest enclosing composable (or function) must be the intended
 * one, and the message tells "deleted" from "moved".
 */
class D15dWiringTest {

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

    /** First occurrence of [call] that is not its own definition (`fun NAME(`). */
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

    private val base = "src/main/java/dev/steamvault/app"
    private val main = "$base/MainActivity.kt"
    private val nav = "$base/ui/nav/BottomNavBar.kt"
    private val clients = "$base/ui/clients/ClientsScreen.kt"
    private val cardViews = "$base/ui/clients/ClientCardViews.kt"
    private val downloads = "$base/ui/downloads/DownloadsScreen.kt"
    private val stats = "$base/ui/downloads/DownloadsStats.kt"
    private val chips = "$base/ui/traffic/RangeChips.kt"
    private val trafficPoll = "$base/ui/traffic/TrafficPoll.kt"

    @Test
    fun `MUTATION PIN -- Clients is a nav destination with its own icon and screen, real and demo`() {
        assertCalledFrom(nav, "Destination.CLIENTS -> ClientsNavIcon()", "BottomNavBar")
        assertCalledFrom(main, "Destination.CLIENTS -> ClientsDestinationContent()", "onCreate")
        val code = stripComments(read(main))
        assertTrue(
            "the real Clients screen must get VaultTrafficRepository(client)",
            code.contains("trafficRepository = remember(client) { VaultTrafficRepository(client) },\n            ranges = trafficRanges,\n            onManagePcs"),
        )
        assertTrue(
            "the demo Clients screen must get DemoTrafficRepository(demo)",
            code.contains("trafficRepository = remember(demo) { DemoTrafficRepository(demo) },\n                ranges = trafficRanges,\n                onManagePcs"),
        )
        assertCalledFrom(main, "onManagePcs = { clientsControllerState?.open(lifecycleScope) }", "ClientsDestinationContent")
    }

    /** WP ANDROID-REDESIGN-1c (rc20 visual check): the D15d pointer row
     * "PCs (agents) / Open Clients" is gone from Settings, like on the web. */
    @Test
    fun `MUTATION PIN -- Settings has no Open Clients pointer any more`() {
        val settings = stripComments(read("$base/ui/settings/SettingsScreen.kt"))
        assertTrue("Settings still takes onOpenClientsClick", !settings.contains("onOpenClientsClick"))
        assertTrue("Settings still has a Clients pointer section", !settings.contains("ClientsSection("))
        assertTrue("MainActivity still wires onOpenClientsClick", !stripComments(read(main)).contains("onOpenClientsClick"))
    }

    @Test
    fun `MUTATION PIN -- the Clients screen polls, draws range, note, cards, agentless note and Manage PCs`() {
        assertCalledFrom(clients, "poll.pollForever()", "ClientsScreen")
        assertCalledFrom(clients, "clientsSummaryText(merged.map { it.client })", "ClientsScreen")
        assertCalledFrom(clients, "ClientsBody(", "ClientsScreen")
        assertCalledFrom(clients, "DevicesHead(showRange = !poll.noFigures) {", "ClientsBody")
        assertCalledFrom(clients, "if (showRange) chips()", "DevicesHead")
        assertCalledFrom(clients, "poll.selectRange(scope, it)", "ClientsBody")
        assertCalledFrom(clients, "item(key = \"note\") { NoteLine(note) }", "ClientsBody")
        assertCalledFrom(clients, "ClientCard(", "ClientsBody")
        assertCalledFrom(clients, "clientCardView(", "ClientsBody")
        assertCalledFrom(clients, "showAgentlessNote(data.clients)", "ClientsBody")
        assertCalledFrom(clients, "ManagePcsSection(onManagePcs)", "ClientsBody")
        assertCalledFrom(clients, "OutlinedButton(onClick = onManagePcs)", "ManagePcsSection")
        assertCalledFrom(clients, "liveRegion = LiveRegionMode.Polite", "NoteLine")
        // WP ANDROID-REDESIGN-1a: the card lives in ClientCardViews.kt.
        assertCalledFrom(cardViews, "semantics(mergeDescendants = true)", "ClientCard")
        for (line in listOf(
            "KindIconTile(model.kind)",
            "StatusPill(model.pill)",
            "model.outdatedText?.let { OutdatedBox(it) }",
            "model.health?.let { AgentHealthRow(it.bypass, it.head, it.text) }",
            "NowBoxView(model.now)",
            "StatGrid(model.stats)",
            "RecentZone(model)",
            "CardFooter(model, onRename, onRemove)",
        )) {
            assertCalledFrom(cardViews, line, "ClientCard")
        }
    }

    @Test
    fun `MUTATION PIN -- Downloads draws the statistics header, speed lines and the history filter`() {
        assertCalledFrom(downloads, "statsPoll.pollForever()", "DownloadsScreen")
        assertCalledFrom(downloads, "historyFilter.load()", "DownloadsScreen")
        assertCalledFrom(downloads, "jobSpeedLines(statsPoll)", "DownloadsScreen")
        assertCalledFrom(downloads, "filterJobsByRange(historyJobs, historyFilter.range)", "DownloadsScreen")
        assertCalledFrom(downloads, "DownloadsStatsHeader(statsPoll, scope, rateCap, onRangeChosen = { ranges.statistics = it })", "DownloadsBody")
        // WP RATE-TILE: the cap line reads the controller's settings and schedule, loaded once per visit.
        assertCalledFrom(downloads, "rateCapLine(controller.settings, controller.schedule, System.currentTimeMillis())", "DownloadsBody")
        assertCalledFrom(downloads, "controller.loadRateCap()", "DownloadsScreen")
        assertCalledFrom(downloads, "speedLine = speedLines[model.jobId]", "DownloadsBody")
        assertCalledFrom(downloads, "historyFilter.selectRange(scope, it)", "DownloadsBody")
        assertCalledFrom(downloads, "emptyHistoryText(historyFilter.range)", "DownloadsBody")
        assertCalledFrom(downloads, "if (speedLine != null) {", "JobCard")
        assertCalledFrom(main, "trafficRepository = remember(client) { VaultTrafficRepository(client) },\n            ranges = trafficRanges,\n            ownedLibrary", "DownloadsDestinationContent")
        assertCalledFrom(main, "trafficRepository = remember(demo) { DemoTrafficRepository(demo) },\n                ranges = trafficRanges,\n                ownedLibrary", "DownloadsDestinationContent")
    }

    @Test
    fun `MUTATION PIN -- the graph caption reads the injected clock, not the wall clock`() {
        // The call sits inside ThroughputGraph's Canvas, whose local `fun Path.trace()`
        // fools the nearest-`fun` helper, so slice the function's own text instead.
        val code = stripComments(read(stats))
        val start = code.indexOf("private fun ThroughputGraph(")
        assertTrue("ThroughputGraph is DELETED", start >= 0)
        val next = Regex("""\n(?:@Composable|(?:private )?fun )""").find(code, start + 1)
        val body = code.substring(start, next?.range?.first ?: code.length)
        assertTrue("ThroughputGraph no longer draws the caption from nowMs()", body.contains("seriesStartText(shown, nowMs())"))
        assertTrue(
            "ThroughputGraph reads System.currentTimeMillis() again -- screenshot goldens go stale hourly",
            !body.contains("System.currentTimeMillis()"),
        )
    }

    @Test
    fun `MUTATION PIN -- the statistics header hides the range without figures and draws tiles and graph`() {
        assertCalledFrom(stats, "if (!poll.noFigures) {", "DownloadsStatsHeader")
        assertCalledFrom(stats, "HeroTiles(hero.tiles)", "DownloadsStatsHeader")
        assertCalledFrom(stats, "ThroughputGraph(series = answer?.series, selectedRange = poll.range, unsupported = poll.unsupported, nowMs = nowMs)", "DownloadsStatsHeader")
        assertCalledFrom(stats, "heroFigures(throughput, rateCap, poll.unsupported)", "DownloadsStatsHeader")
        assertCalledFrom(stats, "HeroTileView(tile, Modifier.weight(1f).fillMaxHeight())", "HeroTiles")
        assertCalledFrom(stats, "clearAndSetSemantics { contentDescription = tile.description }", "HeroTileView")
        // WP GRAPH-ZOOM: the graph draws the trimmed series, and its caption follows it.
        assertCalledFrom(stats, "series?.let { it.copy(points = trimSeries(it.points)) } ?: return", "ThroughputGraph")
        assertCalledFrom(stats, "graphGeometryToShow(shown, selectedRange, unsupported, GRAPH_W, GRAPH_H) ?: return", "ThroughputGraph")
        assertCalledFrom(stats, "ranges = STATS_RANGES,", "DownloadsStatsHeader")
        assertCalledFrom(stats, "contentDescription = label", "ThroughputGraph")
    }

    /** Review should-fix: the polls run only while the screen is STARTED
     * (no background traffic), the exact call wrapping, not just both words. */
    @Test
    fun `MUTATION PIN -- both statistics polls run under repeatOnLifecycle STARTED`() {
        for ((path, poll, screen) in listOf(
            Triple(clients, "poll", "ClientsScreen"),
            Triple(downloads, "statsPoll", "DownloadsScreen"),
        )) {
            val code = stripComments(read(path))
            val m = Regex("""repeatOnLifecycle\(Lifecycle\.State\.STARTED\)\s*\{\s*${poll}\.pollForever\(\)\s*\}""").find(code)
            assertTrue("$path: $poll.pollForever() must be the body of repeatOnLifecycle(Lifecycle.State.STARTED)", m != null)
            assertEquals("$path: the lifecycle-bound poll MOVED", screen, nearestEnclosingFun(code, m!!.range.first))
        }
    }

    @Test
    fun `MUTATION PIN -- chosen ranges survive a tab switch and relative times tick`() {
        assertCalledFrom(clients, "clientsOverviewPoll(trafficRepository, ranges.clients)", "ClientsScreen")
        assertCalledFrom(clients, "ranges.clients = poll.range", "ClientsBody")
        assertCalledFrom(downloads, "downloadsStatsPoll(trafficRepository, ranges.statistics)", "DownloadsScreen")
        assertCalledFrom(downloads, "HistoryFilter(trafficRepository, ranges.history)", "DownloadsScreen")
        assertCalledFrom(downloads, "onRangeChosen = { ranges.statistics = it }", "DownloadsBody")
        assertCalledFrom(downloads, "ranges.history = historyFilter.range", "DownloadsBody")
        assertCalledFrom(stats, "onRangeChosen(poll.range)", "DownloadsStatsHeader")
        assertTrue("MainActivity holds one TrafficRanges", stripComments(read(main)).contains("private val trafficRanges = TrafficRanges()"))
        assertCalledFrom(clients, "delay(NOW_TICK_MS)", "ClientsScreen")
        assertCalledFrom(clients, "remember(data, agentRows, removedAt, nowMs)", "ClientsScreen")
        assertCalledFrom(clients, "nowMs = nowMs,", "ClientsScreen")
        assertCalledFrom(clients, "loadingText = null,\n        nowMs = nowMs,", "ClientsBody")
    }

    @Test
    fun `MUTATION PIN -- the range chips are one labelled selectable group`() {
        assertCalledFrom(chips, ".semantics { contentDescription = label }", "RangeChips")
        assertCalledFrom(chips, ".selectableGroup()", "RangeChips")
        assertCalledFrom(chips, "onClick = { onSelect(range) }", "RangeChips")
    }

    @Test
    fun `MUTATION PIN -- the live range asks the live endpoint, merges the held summary and polls faster`() {
        assertCalledFrom(stats, "repository.throughputLive()", "downloadsStatsPoll")
        assertCalledFrom(stats, "repository.throughput(LIVE_SUMMARY_RANGE)", "downloadsStatsPoll")
        assertCalledFrom(stats, "StatsAnswer(mergeLive(cached, live), liveSeries(live))", "downloadsStatsPoll")
        assertCalledFrom(trafficPoll, "intervalFor(waitedRange, intervalMs, liveIntervalMs)", "pollForever")
    }
}
