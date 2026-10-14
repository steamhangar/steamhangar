package dev.steamvault.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Structural pins for WP TOOL-APPS-2d's model-to-pixel wiring
 * (docs/LEARNINGS.md: "pinning the pure/model layer proves NOTHING about
 * the pixels"). The decisions are pinned in `ContentGroupsTest`,
 * `GroupDeleteTest`, `LibraryFiltersGroupsTest`, `BulkGroupDeleteTest` and
 * `DemoContentGroupTest`; these pins prove the screens call them. Same
 * comment-stripped source scan, strong generation, as `Upd3WiringTest`: the
 * call's nearest enclosing composable must be the intended one, and the
 * message tells "deleted" from "moved".
 */
class ToolApps2dWiringTest {

    private fun read(path: String): String {
        val file = File(path)
        check(file.exists()) { "expected a source file at ${file.absolutePath}" }
        return file.readText(Charsets.UTF_8)
    }

    private fun stripComments(text: String): String =
        text.replace(Regex("/\\*.*?\\*/", RegexOption.DOT_MATCHES_ALL), "")
            .replace(Regex("//.*"), "")

    private val COMPOSABLE_FUN = Regex("""@Composable\s+(?:private\s+|internal\s+)?fun\s+(\w+)""")

    private fun nearestEnclosingComposable(code: String, callIdx: Int): String? =
        COMPOSABLE_FUN.findAll(code)
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

    private fun assertCalledFrom(path: String, call: String, expectedComposable: String) {
        val code = stripComments(read(path))
        val idx = callIndex(code, call)
        assertTrue("$path: '$call' is DELETED -- the wiring is gone", idx >= 0)
        assertEquals(
            "$path: '$call' MOVED -- its nearest enclosing composable is no longer $expectedComposable",
            expectedComposable,
            nearestEnclosingComposable(code, idx),
        )
    }

    private fun assertContains(path: String, snippet: String) {
        assertTrue("$path: '$snippet' is gone", stripComments(read(path)).contains(snippet))
    }

    private val base = "src/main/java/dev/steamvault/app/ui"
    private val library = "$base/library/LibraryScreen.kt"
    private val card = "$base/library/GameCard.kt"
    private val row = "$base/library/GameListRow.kt"
    private val bulk = "$base/library/LibraryBulkBar.kt"
    private val sheet = "$base/detail/GameDetailSheet.kt"

    @Test
    fun `MUTATION PIN -- the Library groups the list and feeds every consumer the packages`() {
        assertCalledFrom(library, "groupGames(merged)", "LibraryScreen")
        assertCalledFrom(library, "visibleGames(grouped.rows, normalizedQuery, controller.filterKey, liveJobsByAppid, grouped.packagesByParent)", "LibraryScreen")
        assertCalledFrom(library, "grouped.packagesByParent,", "LibraryScreen")
        assertCalledFrom(library, "packages = grouped.packagesOf(g.appid)", "LibraryScreen")
        assertCalledFrom(library, "ownedPlaytimeForever = playtime[g.appid]", "LibraryScreen")
        assertCalledFrom(library, "ownedPlaytimeByAppid(ownedState.ownedGamesOrNull)", "LibraryScreen")
        assertCalledFrom(library, "LibraryBulkBar(controller = controller, allGames = merged, grouped = grouped, scope = scope)", "LibraryScreen")
        assertCalledFrom(library, "compact = layout == LibraryLayout.GRID_3", "LibraryGrid")
    }

    @Test
    fun `MUTATION PIN -- the grid card and the list row draw the group line and the never-played pill`() {
        assertCalledFrom(card, "GroupPackagesLine(model)", "GameCard")
        assertCalledFrom(
            card,
            "if (model.neverPlayed) NeverPlayedPill(compact = compact, modifier = Modifier.weight(1f, fill = false))",
            "GameCard",
        )
        assertCalledFrom(row, "GroupPackagesLine(model)", "GameListRow")
        assertCalledFrom(row, "if (model.neverPlayed) NeverPlayedLabel()", "GameListRow")
    }

    @Test
    fun `MUTATION PIN -- bulk selection expands groups and the dialog shows and gates the group plans`() {
        assertCalledFrom(bulk, "expandSelection(picked, grouped)", "LibraryBulkBar")
        assertCalledFrom(bulk, "bulkDeletableCards(picked, grouped, controller.jobs, classification)", "LibraryBulkBar")
        assertCalledFrom(bulk, "enabled = state is DeletePlanUiState.Ready && !state.blocked", "DeleteConfirmDialog")
        assertCalledFrom(bulk, "GroupedDeletePlanBody(state, allGames)", "DeleteConfirmDialog")
        assertCalledFrom(bulk, "GroupDeletePlanBlock(plan = plan, nameFor = nameFor, showSubject = state.ids.size > 1)", "GroupedDeletePlanBody")
    }

    @Test
    fun `MUTATION PIN -- the detail sheet lists the packages with retry and deletes a group through its plan`() {
        assertCalledFrom(sheet, "groupGames(games).packagesOf(appid)", "LoadedDetailBody")
        assertCalledFrom(sheet, "PackagesSection(", "LoadedDetailBody")
        assertCalledFrom(sheet, "controller.retryPackage(scope, pkg.appid, onLibraryChanged)", "LoadedDetailBody")
        assertCalledFrom(sheet, "controller.openGroupDeleteConfirm(scope)", "LoadedDetailBody")
        assertCalledFrom(sheet, "offersPackageRetry(pkg, liveJob, findTrackedJob(jobs, pkg.appid))", "PackagesSection")
        // Review fix: the retry button is disabled while its request is in flight.
        assertCalledFrom(sheet, "enabled = pkg.appid !in retrying", "PackagesSection")
        assertCalledFrom(sheet, "retrying = controller.retryingAppids", "LoadedDetailBody")
        assertCalledFrom(sheet, "GroupDeleteConfirmDialog(", "GameDetailSheet")
        assertCalledFrom(sheet, "enabled = state is GroupDeleteUiState.Ready && !isPlanBlocked(state.plan)", "GroupDeleteConfirmDialog")
    }

    @Test
    fun `MUTATION PIN -- both delete paths confirm exactly the plan's members and re-ask on a mismatch`() {
        val lib = "$base/library/LibraryController.kt"
        assertContains(lib, "cacheRepository.deleteWithPackages(id, planMemberIds(groupPlan))")
        assertContains(lib, "if (groupPlan != null && isMembersMismatch(e)) changed.add(id) else failedCount++")
        assertContains(lib, "openDeleteConfirm(scope, changed, MEMBERS_CHANGED_NOTICE)")
        val det = "$base/detail/DetailController.kt"
        assertContains(det, "cacheRepository.deleteWithPackages(appid, planMemberIds(ready.plan))")
        assertContains(det, "openGroupDeleteConfirm(scope, MEMBERS_CHANGED_NOTICE)")
        assertContains(det, "if (isPlanBlocked(ready.plan)) return")
    }
}
