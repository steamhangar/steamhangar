package dev.steamvault.app

import dev.steamvault.app.ui.downloads.INTERNET_FILL_ALPHA
import dev.steamvault.app.ui.downloads.SERVED_FILL_ALPHA
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Structural pins for WP ANDROID-REDESIGN-1b (rc20 redesign follow-up on
 * Android): the detail sheet's tabs with Verify/Repair in the header and the
 * Maintenance tab, the Library toolbar (split "Check updates", Filters
 * button, icon-only sort direction and layout segments) and the graph fills.
 * Same comment-stripped source scan, strong generation (nearest enclosing
 * function), as `AndroidDl2WiringTest`.
 */
class AndroidRedesign1bWiringTest {

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

    /** The text of the `when (tab)` branch [branch] inside LoadedDetailBody. */
    private fun tabBranch(branch: String): String {
        val code = stripComments(read(sheet))
        val body = code.substring(code.indexOf("private fun LoadedDetailBody("), code.indexOf("private fun CompletenessSection("))
        val start = body.indexOf("DetailTab.$branch -> {")
        assertTrue("LoadedDetailBody has no $branch branch", start >= 0)
        val next = listOf("DetailTab.OVERVIEW -> {", "DetailTab.DEPOTS -> {", "DetailTab.MAINTENANCE -> {")
            .map { body.indexOf(it, start + 1) }
            .filter { it > start }
            .minOrNull() ?: body.length
        return body.substring(start, next)
    }

    private val base = "src/main/java/dev/steamvault/app"
    private val sheet = "$base/ui/detail/GameDetailSheet.kt"
    private val library = "$base/ui/library/LibraryScreen.kt"
    private val metaPanel = "$base/ui/library/LibraryMetaPanel.kt"

    @Test
    fun `MUTATION PIN -- the loaded sheet draws the tab row and renders one tab at a time`() {
        assertCalledFrom(sheet, "DetailTabRow(selected = tab, onSelect = onTab)", "LoadedDetailBody")
        assertCalledFrom(sheet, "var tab by rememberSaveable(appid) { mutableStateOf(initialTab) }", "GameDetailSheetBody")
        // WP ANDROID-REDESIGN-1c: the sheet opens on Overview; only the screenshot test passes another tab.
        assertCalledFrom(sheet, "initialTab: DetailTab = DetailTab.OVERVIEW,", "GameDetailSheetBody")
        assertCalledFrom(sheet, "onTab = { tab = it },", "GameDetailSheetBody")
        assertCalledFrom(sheet, "selectedTabIndex = selected.ordinal,", "DetailTabRow")
        assertCalledFrom(sheet, "onClick = { onSelect(tab) },", "DetailTabRow")
    }

    @Test
    fun `MUTATION PIN -- each tab holds its own blocks`() {
        val overview = tabBranch("OVERVIEW")
        for (piece in listOf("StoreDetailsBlock(", "detail_last_download", "completenessLine(gameSummary)", "installedBadgeFor(", "PackagesSection(")) {
            assertTrue("Overview must hold $piece", overview.contains(piece))
        }
        val depots = tabBranch("DEPOTS")
        assertTrue("Depots must list the depot rows", depots.contains("DepotRow(row)"))
        assertTrue("Depots must keep the expected block", depots.contains("ExpectedDepotsBlock(expected)"))
        val maintenance = tabBranch("MAINTENANCE")
        for (piece in listOf("CompletenessSection(", "GcSection(", "controller.openGroupDeleteConfirm(scope)", "controller.openDeleteConfirm()", "detail_maintenance_empty")) {
            assertTrue("Maintenance must hold $piece", maintenance.contains(piece))
        }
        for (piece in listOf("CompletenessSection(", "GcSection(", "openDeleteConfirm")) {
            assertTrue("$piece must not leak onto Overview", !overview.contains(piece))
        }
    }

    @Test
    fun `MUTATION PIN -- Verify and Repair sit in the header too, on the same flows`() {
        assertCalledFrom(sheet, "HeadActionsRow(", "LoadedDetailBody")
        assertCalledFrom(sheet, "verifyIds = if (completenessShown) verifyIds else emptyList(),", "LoadedDetailBody")
        assertCalledFrom(sheet, "headRepair = completenessShown && showsRepair(gameSummary, jobs),", "LoadedDetailBody")
        assertCalledFrom(sheet, "onHeadRepair = { controller.startRepair(scope, appid, title, onLibraryChanged) },", "LoadedDetailBody")
        assertCalledFrom(sheet, "if (headRepair) {", "HeadActionsRow")
        assertCalledFrom(sheet, "onClick = onHeadRepair,", "HeadActionsRow")
        // Not `onClick = { onVerify(verifyIds) }`: CompletenessSection has the
        // same line; the header's Verify is the one gated on verifyInFlight.
        assertCalledFrom(sheet, "enabled = !verifyInFlight,", "HeadActionsRow")
        assertCalledFrom(sheet, "        primary()", "HeadActionsRow")
        val code = stripComments(read(sheet))
        assertEquals(
            "the header and the Maintenance tab start the same repair flow",
            2,
            Regex("""controller\.startRepair\(scope, appid, title, onLibraryChanged\)""").findAll(code).count(),
        )
    }

    @Test
    fun `MUTATION PIN -- Show job slides the sheet away before switching screens`() {
        assertCalledFrom(sheet, "scope.launch { sheetState.hide() }.invokeOnCompletion {", "GameDetailSheet")
    }

    @Test
    fun `MUTATION PIN -- the Library app bar carries the split Check updates button`() {
        assertCalledFrom(library, "CheckUpdatesMenu(controller = controller, scope = scope)", "LibraryScreen")
        assertCalledFrom(library, "controller.checkAndUpdateCachedGames(scope)", "CheckUpdatesMenu")
        assertCalledFrom(library, "enabled = !controller.checkAndUpdateBusy,", "CheckUpdatesMenu")
        assertTrue("the old check row is gone", !stripComments(read(library)).contains("CheckAndUpdateRow("))
    }

    @Test
    fun `MUTATION PIN -- the toolbar holds search, chips, sort, layout, Filters and Select`() {
        assertCalledFrom(library, "FilterChipsRow(counts = counts, selectedKey = controller.filterKey, onSelect = { controller.filterKey = it })", "LibraryScreen")
        assertCalledFrom(library, "        chips()", "LibraryToolbar")
        assertCalledFrom(library, "leadingIcon = { SearchGlyph() },", "LibraryToolbar")
        assertCalledFrom(library, "SortRow(", "LibraryToolbar")
        assertCalledFrom(library, "LayoutToggle(", "LibraryToolbar")
        assertCalledFrom(library, "onClick = filters.onToggle,", "LibraryToolbar")
        // WP ANDROID-POLISH-1: funnel + count below the 58 dp button minimum, one control row.
        assertCalledFrom(library, "FiltersGlyph()", "LibraryToolbar")
        assertCalledFrom(library, ".widthIn(min = FILTERS_MIN_WIDTH)", "LibraryToolbar")
        assertCalledFrom(library, "SelectGlyph(", "LibraryToolbar")
        assertCalledFrom(library, "SortDirectionGlyph(descending = active.dir == SortDirection.DESC)", "SortRow")
        assertCalledFrom(library, "label = { LayoutGlyph(option.layout) },", "LayoutToggle")
    }

    @Test
    fun `MUTATION PIN -- the Filters button folds the metadata panel and shows the active count`() {
        assertCalledFrom(library, "var filtersOpen by rememberSaveable { mutableStateOf(false) }", "LibraryScreen")
        assertCalledFrom(library, "onToggle = { filtersOpen = !filtersOpen },", "LibraryScreen")
        assertCalledFrom(library, "activeCount = activeFilterCount(controller.metaFilter),", "LibraryScreen")
        assertCalledFrom(library, "visible = filtersOpen,", "LibraryScreen")
        assertCalledFrom(metaPanel, "AnimatedVisibility(visible = visible,", "LibraryMetaPanel")
        assertCalledFrom(metaPanel, "if (!visible) {", "LibraryMetaPanel")
    }

    @Test
    fun `the graph's served fill is clearly stronger than the internet fill`() {
        assertTrue(
            "served $SERVED_FILL_ALPHA vs internet $INTERNET_FILL_ALPHA",
            SERVED_FILL_ALPHA - INTERNET_FILL_ALPHA >= 0.2f,
        )
    }
}
