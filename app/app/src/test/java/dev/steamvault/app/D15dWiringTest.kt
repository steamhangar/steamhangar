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
    private val downloads = "$base/ui/downloads/DownloadsScreen.kt"
    private val stats = "$base/ui/downloads/DownloadsStats.kt"
    private val chips = "$base/ui/traffic/RangeChips.kt"

    @Test
    fun `MUTATION PIN -- Clients is a nav destination with its own icon and screen, real and demo`() {
        assertCalledFrom(nav, "Destination.CLIENTS -> ClientsNavIcon()", "BottomNavBar")
        assertCalledFrom(main, "Destination.CLIENTS -> ClientsDestinationContent()", "onCreate")
        val code = stripComments(read(main))
        assertTrue(
            "the real Clients screen must get VaultTrafficRepository(client)",
            code.contains("trafficRepository = remember(client) { VaultTrafficRepository(client) },\n            onManagePcs"),
        )
        assertTrue(
            "the demo Clients screen must get DemoTrafficRepository(demo)",
            code.contains("trafficRepository = remember(demo) { DemoTrafficRepository(demo) },\n                onManagePcs"),
        )
        assertCalledFrom(main, "onManagePcs = { clientsControllerState?.open(lifecycleScope) }", "ClientsDestinationContent")
    }

    @Test
    fun `MUTATION PIN -- Settings points to the Clients page`() {
        assertCalledFrom(main, "onOpenClientsClick = { destination = Destination.CLIENTS }", "SettingsDestinationContent")
    }

    @Test
    fun `MUTATION PIN -- the Clients screen polls, draws range, note, cards, agentless note and Manage PCs`() {
        assertCalledFrom(clients, "poll.pollForever()", "ClientsScreen")
        assertCalledFrom(clients, "clientsSummaryText(data.clients)", "ClientsScreen")
        assertCalledFrom(clients, "ClientsBody(poll = poll, scope = scope, onManagePcs = onManagePcs)", "ClientsScreen")
        assertCalledFrom(clients, "if (!poll.noFigures) {", "ClientsBody")
        assertCalledFrom(clients, "poll.selectRange(scope, it)", "ClientsBody")
        assertCalledFrom(clients, "item(key = \"note\") { NoteLine(note) }", "ClientsBody")
        assertCalledFrom(clients, "ClientCard(it)", "ClientsBody")
        assertCalledFrom(clients, "buildClientCardModel(", "ClientsBody")
        assertCalledFrom(clients, "showAgentlessNote(data.clients)", "ClientsBody")
        assertCalledFrom(clients, "ManagePcsSection(onManagePcs)", "ClientsBody")
        assertCalledFrom(clients, "OutlinedButton(onClick = onManagePcs)", "ManagePcsSection")
        assertCalledFrom(clients, "liveRegion = LiveRegionMode.Polite", "NoteLine")
        assertCalledFrom(clients, "semantics(mergeDescendants = true)", "ClientCard")
        for (line in listOf("model.installed?.let", "model.stats?.let", "model.recent?.let", "CardLine(model.loadingNow)", "CardLine(model.lastActive)")) {
            assertCalledFrom(clients, line, "ClientCard")
        }
    }

    @Test
    fun `MUTATION PIN -- Downloads draws the statistics header, speed lines and the history filter`() {
        assertCalledFrom(downloads, "statsPoll.pollForever()", "DownloadsScreen")
        assertCalledFrom(downloads, "historyFilter.load()", "DownloadsScreen")
        assertCalledFrom(downloads, "jobSpeedLines(statsPoll)", "DownloadsScreen")
        assertCalledFrom(downloads, "filterJobsByRange(historyJobs, historyFilter.range)", "DownloadsScreen")
        assertCalledFrom(downloads, "DownloadsStatsHeader(statsPoll, scope)", "DownloadsBody")
        assertCalledFrom(downloads, "speedLine = speedLines[model.jobId]", "DownloadsBody")
        assertCalledFrom(downloads, "historyFilter.selectRange(scope, it)", "DownloadsBody")
        assertCalledFrom(downloads, "emptyHistoryText(historyFilter.range)", "DownloadsBody")
        assertCalledFrom(downloads, "if (speedLine != null) {", "JobCard")
        assertCalledFrom(main, "trafficRepository = remember(client) { VaultTrafficRepository(client) },\n            ownedLibrary", "DownloadsDestinationContent")
        assertCalledFrom(main, "trafficRepository = remember(demo) { DemoTrafficRepository(demo) },\n                ownedLibrary", "DownloadsDestinationContent")
    }

    @Test
    fun `MUTATION PIN -- the statistics header hides the range without figures and draws tiles and graph`() {
        assertCalledFrom(stats, "if (!poll.noFigures) {", "DownloadsStatsHeader")
        assertCalledFrom(stats, "if (tiles != null) StatTiles(tiles)", "DownloadsStatsHeader")
        assertCalledFrom(stats, "ThroughputGraph(series = answer?.series, selectedRange = poll.range, unsupported = poll.unsupported)", "DownloadsStatsHeader")
        assertCalledFrom(stats, "headerTiles(throughput)", "DownloadsStatsHeader")
        assertCalledFrom(stats, "StatTileView(tile, Modifier.weight(1f))", "StatTiles")
        assertCalledFrom(stats, "clearAndSetSemantics { contentDescription = description }", "StatTileView")
        assertCalledFrom(stats, "graphGeometryToShow(series, selectedRange, unsupported, GRAPH_W, GRAPH_H) ?: return", "ThroughputGraph")
        assertCalledFrom(stats, "contentDescription = label", "ThroughputGraph")
    }

    @Test
    fun `MUTATION PIN -- the range chips are one labelled selectable group`() {
        assertCalledFrom(chips, ".semantics { contentDescription = label }", "RangeChips")
        assertCalledFrom(chips, ".selectableGroup()", "RangeChips")
        assertCalledFrom(chips, "onClick = { onSelect(range) }", "RangeChips")
    }
}
