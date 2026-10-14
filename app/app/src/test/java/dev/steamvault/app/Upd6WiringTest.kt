package dev.steamvault.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Structural pins for WP UPD-6's model-to-pixel wiring (same comment-stripped
 * source scan as `Upd3WiringTest`): the decisions are pinned in
 * `UpdatesAvailableTest`, `DownloadsControllerUpdatesTest` and
 * `CardUpdateStateTest`; these pins prove the screens call them.
 */
class Upd6WiringTest {

    private fun read(path: String): String {
        val file = File(path)
        check(file.exists()) { "expected a source file at ${file.absolutePath}" }
        return file.readText(Charsets.UTF_8)
    }

    private fun stripComments(text: String): String =
        text.replace(Regex("/\\*.*?\\*/", RegexOption.DOT_MATCHES_ALL), "")
            .replace(Regex("//.*"), "")

    private val COMPOSABLE_FUN = Regex("""@Composable\s+(?:(?:private|internal)\s+)?fun\s+(\w+)""")

    private fun nearestEnclosingComposable(code: String, callIdx: Int): String? =
        COMPOSABLE_FUN.findAll(code)
            .map { it.range.first to it.groupValues[1] }
            .filter { (start, _) -> start < callIdx }
            .maxByOrNull { (start, _) -> start }
            ?.second

    private fun assertCalledFrom(path: String, call: String, expectedComposable: String) {
        val code = stripComments(read(path))
        val idx = code.indexOf(call)
        assertTrue("$path: '$call' is DELETED -- the wiring is gone", idx >= 0)
        assertEquals(
            "$path: '$call' MOVED -- its nearest enclosing composable is no longer $expectedComposable",
            expectedComposable,
            nearestEnclosingComposable(code, idx),
        )
    }

    private val base = "src/main/java/dev/steamvault/app"
    private val downloads = "$base/ui/downloads/DownloadsScreen.kt"
    private val card = "$base/ui/library/GameCard.kt"
    private val row = "$base/ui/library/GameListRow.kt"

    @Test
    fun `MUTATION PIN -- Downloads lists the pending updates, only when there are any, before the queue`() {
        assertCalledFrom(downloads, "pendingUpdates(controller.games, controller.jobs)", "DownloadsScreen")
        assertCalledFrom(downloads, "controller.loadSchedule()", "DownloadsScreen")
        assertCalledFrom(downloads, "if (updateRows.isNotEmpty())", "DownloadsBody")
        assertCalledFrom(downloads, "scheduleNote(controller.schedule)", "DownloadsBody")
        val code = stripComments(read(downloads))
        assertTrue("the section must sit before the Queue heading", code.indexOf("updates-heading") in 0 until code.indexOf("queue-heading"))
        assertTrue("the section must sit after Paused", code.indexOf("paused-heading") in 0 until code.indexOf("updates-heading"))
    }

    @Test
    fun `MUTATION PIN -- Update all sends the listed ids, Update now its own`() {
        assertCalledFrom(downloads, "controller.startUpdates(scope, updateRows.map { it.appid }, UPDATES_ALL)", "DownloadsBody")
        assertCalledFrom(downloads, "controller.startUpdates(scope, listOf(row.appid), row.appid)", "DownloadsBody")
    }

    @Test
    fun `MUTATION PIN -- the real vault and demo mode both hand Downloads a schedule repository`() {
        val code = stripComments(read("$base/MainActivity.kt"))
        assertTrue(code.contains("scheduleRepository = remember(client) { VaultScheduleRepository(client) }"))
        assertTrue(code.contains("scheduleRepository = remember(demo) { DemoScheduleRepository(demo) }"))
    }

    @Test
    fun `MUTATION PIN -- grid card and list row draw the update state`() {
        assertCalledFrom(card, "StatusIcon(kind = cardIconKind(model)", "CapsulePill")
        assertCalledFrom(card, "color = cardStatusWordColor(model)", "GameCard")
        assertCalledFrom(row, "StatusIcon(kind = cardIconKind(model)", "GameListRow")
        assertCalledFrom(row, "color = cardStatusWordColor(model)", "GameListRow")
        val cardCode = stripComments(read(card))
        assertTrue(
            "cardStatusWord must say 'Update available' for the update state",
            Regex("""if \(model\.updateState\)\s*\{\s*stringResource\(R\.string\.library_update_available_badge\)""").containsMatchIn(cardCode),
        )
    }

    private val sheet = "$base/ui/detail/GameDetailSheet.kt"

    @Test
    fun `MUTATION PIN -- the detail sheet header reads the group row, and offers Update now`() {
        assertCalledFrom(sheet, "groupGames(games).rows.firstOrNull { it.appid == appid }", "GameDetailSheetBody")
        assertCalledFrom(sheet, "updateRow = updateRow)", "GameDetailSheetBody")
        assertCalledFrom(sheet, "val updateState = showsUpdateState(row, kind)", "DetailHeader")
        assertCalledFrom(sheet, "StatusIcon(kind = cardIconKind(row, kind)", "DetailHeader")
        assertCalledFrom(sheet, "sheetUpdateAppids(listOf(gameSummary) + packages, jobs)", "LoadedDetailBody")
        assertCalledFrom(sheet, "controller.startUpdate(scope, updateAppids, games)", "LoadedDetailBody")
        assertCalledFrom(sheet, "val pkgUpdate = showsUpdateState(pkg, kind)", "PackagesSection")
    }

    @Test
    fun `MUTATION PIN -- the 5xx update toast stays long, the unknown-size dash is silent`() {
        val controller = stripComments(read("$base/ui/downloads/DownloadsController.kt"))
        assertTrue(controller.contains("showToast(outcome.message, if (outcome.long) BULK_TOAST_MS else TOAST_MS)"))
        assertCalledFrom("$base/ui/downloads/DownloadsUpdates.kt", "Modifier.clearAndSetSemantics {}", "UpdateRow")
    }
}
