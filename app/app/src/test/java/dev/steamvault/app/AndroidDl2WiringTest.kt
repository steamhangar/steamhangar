package dev.steamvault.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Structural pins for WP ANDROID-DL-2's model-to-pixel wiring
 * (docs/LEARNINGS.md: "pinning the pure/model layer proves NOTHING about
 * the pixels"). The decisions are pinned in `LoadingNowTest`,
 * `CacheRepairTest`, `RepairFlowTest` and `CacheCompletenessTest`; these
 * pins prove the screens call them. Same comment-stripped source scan,
 * strong generation, as `AndroidDl1WiringTest`.
 */
class AndroidDl2WiringTest {

    private fun read(path: String): String {
        val file = File(path)
        check(file.exists()) { "expected a source file at ${file.absolutePath}" }
        return file.readText(Charsets.UTF_8)
    }

    private fun stripComments(text: String): String =
        text.replace(Regex("/\\*.*?\\*/", RegexOption.DOT_MATCHES_ALL), "")
            .replace(Regex("//.*"), "")

    private val FUN = Regex("""(?:@Composable\s+)?(?:private\s+)?fun\s+(?:<[^>]+>\s+)?(?:[\w.]+\.)?(\w+)""")

    private fun nearestEnclosingFun(code: String, callIdx: Int): String? =
        FUN.findAll(code)
            .map { it.range.first to it.groupValues[1] }
            .filter { (start, _) -> start < callIdx }
            .maxByOrNull { (start, _) -> start }
            ?.second

    private fun assertCalledFrom(path: String, call: String, expectedFun: String) {
        val code = stripComments(read(path))
        val idx = code.indexOf(call)
        assertTrue("$path: '$call' is DELETED -- the wiring is gone", idx >= 0)
        assertEquals(
            "$path: '$call' MOVED -- its nearest enclosing function is no longer $expectedFun",
            expectedFun,
            nearestEnclosingFun(code, idx),
        )
    }

    private val base = "src/main/java/dev/steamvault/app"
    private val downloads = "$base/ui/downloads/DownloadsScreen.kt"
    private val stats = "$base/ui/downloads/DownloadsStats.kt"
    private val incomplete = "$base/ui/downloads/DownloadsIncomplete.kt"
    private val flow = "$base/ui/downloads/RepairFlow.kt"
    private val sheet = "$base/ui/detail/GameDetailSheet.kt"
    private val main = "$base/MainActivity.kt"

    @Test
    fun `MUTATION PIN -- the header draws the Loading now rows from the merged answer, only with figures`() {
        assertCalledFrom(stats, "val loadingNow = if (tiles != null) loadingNowModel(throughput) else null", "DownloadsStatsHeader")
        assertCalledFrom(stats, "if (loadingNow != null) LoadingNowBlock(loadingNow)", "DownloadsStatsHeader")
        assertCalledFrom(stats, "for (row in model.rows)", "LoadingNowBlock")
        assertCalledFrom(stats, "Text(row.name,", "LoadingNowBlock")
        assertCalledFrom(stats, "Text(row.detail,", "LoadingNowBlock")
        assertCalledFrom(stats, "Text(row.speed,", "LoadingNowBlock")
        assertCalledFrom(stats, "model.label,", "LoadingNowBlock")
        // The rows reach the answer through the merges.
        val view = stripComments(read("$base/ui/traffic/logic/TrafficView.kt"))
        assertTrue(view.contains("loading_sources = live.loading_sources)"))
        assertTrue(view.contains("now.loading_sources?.let { merged = merged.copy(loading_sources = it) }"))
    }

    @Test
    fun `MUTATION PIN -- the Incomplete rows offer Repair with the note and start the flow`() {
        assertCalledFrom(downloads, "incompleteRowModel(g, nameFor(g.appid, gamesByAppid, ownedNames), controller.jobs)", "DownloadsScreen")
        assertCalledFrom(downloads, "onRepair = { controller.startRepair(scope, row.appid, row.name) },", "DownloadsBody")
        assertCalledFrom(incomplete, "repair = !completable && showsRepair(game, jobs),", "incompleteRowModel")
        assertCalledFrom(incomplete, "note = repairNoteFor(game),", "incompleteRowModel")
        assertCalledFrom(incomplete, "} else if (model.repair) {", "IncompleteRow")
        assertCalledFrom(incomplete, "onClick = onRepair,", "IncompleteRow")
        assertCalledFrom(incomplete, "RepairWording.CHECKING_LABEL else RepairWording.LABEL", "IncompleteRow")
        val controller = stripComments(read("$base/ui/downloads/DownloadsController.kt"))
        assertTrue(controller.contains("flow.start(appid, title, onToast = { showToast(it) }, onRefresh = ::refreshAfterRepair)"))
    }

    @Test
    fun `MUTATION PIN -- the dialog is shown and EXECUTE is sent only from confirm`() {
        assertCalledFrom(downloads, "RepairDialog(flow, scope, onRefresh = controller::refreshAfterRepair, onShowJob = { showJobId = it })", "DownloadsScreen")
        assertCalledFrom(downloads, "listState.scrollToJob(id)", "DownloadsScreen")
        assertCalledFrom(flow, "repository.repair(appid, execute = false)", "start")
        assertCalledFrom(flow, "repository.repair(confirming.appid, execute = true)", "confirm")
        assertCalledFrom(flow, "if (state !is RepairFlowState.Executing) return", "confirm")
        val code = stripComments(read(flow))
        assertEquals("exactly one execute call site", 1, Regex("""repository\.repair\([^)]*execute = true""").findAll(code).count())
        assertCalledFrom(flow, "scope.launch { flow.confirm(onRefresh) }", "RepairDialog")
        assertCalledFrom(flow, "flow.showJob()?.let(onShowJob)", "RepairDialog")
        assertCalledFrom(flow, "onClick = { flow.close() },", "RepairDialog")
    }

    @Test
    fun `MUTATION PIN -- the detail sheet offers Repair, the dialog, and Show job reaches Downloads`() {
        assertCalledFrom(sheet, "showsRepair = showsRepair(gameSummary, jobs),", "LoadedDetailBody")
        assertCalledFrom(sheet, "onRepair = { controller.startRepair(scope, appid, title, onLibraryChanged) },", "LoadedDetailBody")
        assertCalledFrom(sheet, "if (showsRepair) {", "CompletenessSection")
        assertCalledFrom(sheet, "flow = controller.repair,", "GameDetailSheet")
        assertCalledFrom(sheet, "onShowJob(jobId)", "GameDetailSheet")
        assertTrue(stripComments(read("$base/ui/library/LibraryScreen.kt")).contains("onShowJob = onShowJob,"))
        val activity = stripComments(read(main))
        assertEquals(2, Regex("onShowJob = ::showJobOnDownloads,").findAll(activity).count())
        assertEquals(2, Regex("showJobRequest = pendingShowJobId,").findAll(activity).count())
        assertTrue(activity.contains("cacheRepository = remember(demo) { DemoCacheRepository(demo) },\n                showJobRequest"))
        assertTrue(activity.contains("cacheRepository = remember(client) { VaultCacheRepository(client) },\n            showJobRequest"))
        // The sheet's close forgets the flow (web closeRepair).
        assertCalledFrom("$base/ui/detail/DetailController.kt", "repair.reset()", "close")
    }

    @Test
    fun `MUTATION PIN -- real vault and demo answer repair, the demo traffic names its sources`() {
        assertTrue(stripComments(read("$base/repo/CacheRepository.kt")).contains("client.repairCache(appid, execute)"))
        assertTrue(stripComments(read("$base/demo/DemoRepositories.kt")).contains("state.repair(appid, execute)"))
        assertTrue(stripComments(read("$base/demo/DemoRepositories.kt")).contains("demoLive(Instant.now().toEpochMilli(), state.trafficSnapshot())"))
        assertCalledFrom("$base/demo/DemoTraffic.kt", "loading_sources = demoLoadingSources(bps, bps * 0.1, snapshot),", "demoLive")
        assertCalledFrom("$base/demo/DemoTraffic.kt", "loading_sources = demoLoadingSources(currentBps, runnerBps, snapshot),", "demoThroughput")
    }

    @Test
    fun `MUTATION PIN -- Show N more moves focus to the first new History row`() {
        assertCalledFrom(downloads, "focusRowAt = historyPaging.visible", "DownloadsBody")
        assertCalledFrom(downloads, "Box(if (index == focusRowAt) Modifier.focusRequester(firstNewRow) else Modifier)", "DownloadsBody")
        assertCalledFrom(downloads, "focusFirstNewRow(firstNewRow)", "DownloadsBody")
    }
}
