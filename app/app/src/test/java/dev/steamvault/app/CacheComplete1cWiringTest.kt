package dev.steamvault.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Structural pins for WP CACHE-COMPLETE-1c and LIB-LIST-1's model-to-pixel
 * wiring (same comment-stripped source scan as `Upd6WiringTest`): the
 * decisions are pinned in `CacheCompletenessTest`, `IncompleteGamesTest`
 * and the controller tests; these pins prove the screens call them.
 */
class CacheComplete1cWiringTest {

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
    private val incomplete = "$base/ui/downloads/DownloadsIncomplete.kt"
    private val card = "$base/ui/library/GameCard.kt"
    private val row = "$base/ui/library/GameListRow.kt"
    private val sheet = "$base/ui/detail/GameDetailSheet.kt"

    @Test
    fun `MUTATION PIN -- Downloads lists the incomplete games after Updates available and before the queue, only when non-empty`() {
        assertCalledFrom(downloads, "pendingIncomplete(controller.games, controller.jobs)", "DownloadsScreen")
        assertCalledFrom(downloads, "if (incompleteRows.isNotEmpty())", "DownloadsBody")
        assertCalledFrom(downloads, "incompleteScheduleNote(controller.schedule)", "DownloadsBody")
        val code = stripComments(read(downloads))
        val updates = code.indexOf("updates-heading")
        val inc = code.indexOf("incomplete-heading")
        val queue = code.indexOf("queue-heading")
        assertTrue("Incomplete must sit after Updates available", updates in 0 until inc)
        assertTrue("Incomplete must sit before the Queue", inc in 0 until queue)
    }

    @Test
    fun `MUTATION PIN -- Complete now sends its own id, Complete all exactly the completable listed rows`() {
        assertCalledFrom(downloads, "controller.startCompletion(scope, listOf(row.appid), row.appid)", "DownloadsBody")
        assertCalledFrom(downloads, "controller.startCompletion(scope, ids, COMPLETE_ALL)", "DownloadsBody")
        assertCalledFrom(incomplete, "val completable = rows.filter { it.completable }.map { it.appid }", "IncompleteHeader")
        assertCalledFrom(incomplete, "onClick = { onCompleteAll(completable) }", "IncompleteHeader")
        assertCalledFrom(incomplete, "if (model.completable)", "IncompleteRow")
        assertCalledFrom(incomplete, "text = CompletenessWording.DAMAGED_NOTE", "IncompleteRow")
        val controller = stripComments(read("$base/ui/downloads/DownloadsController.kt"))
        assertTrue(
            "completion must use POST /v1/prefill/verify",
            controller.contains("summarizeVerifyStart(jobsRepository.prefillVerify(appids), VerifyMode.COMPLETE)"),
        )
    }

    @Test
    fun `MUTATION PIN -- the cards draw the completeness word, compact in the list and the 3-column grid`() {
        assertCalledFrom(card, "text = cardStatusWord(model, compact = compact)", "GameCard")
        assertCalledFrom(row, "val statusWord = cardStatusWord(model, compact = true)", "GameListRow")
        val cardCode = stripComments(read(card))
        assertTrue(
            "cardStatusWord must read the completeness overlay",
            cardCode.contains("model.completeness?.let { if (compact) it.compact else it.word }"),
        )
    }

    @Test
    fun `MUTATION PIN -- the detail sheet shows the completeness block with Verify and the header overlay`() {
        assertCalledFrom(sheet, "if (showsCompletenessBlock(gameSummary, packages))", "LoadedDetailBody")
        assertCalledFrom(sheet, "verifyIds = verifyAppids(listOf(gameSummary) + packages)", "LoadedDetailBody")
        assertCalledFrom(sheet, "onVerify = { ids -> controller.startVerify(scope, ids) }", "LoadedDetailBody")
        assertCalledFrom(sheet, "text = completenessLine(game)", "CompletenessSection")
        assertCalledFrom(sheet, "if (isDamagedOnly(game))", "CompletenessSection")
        assertCalledFrom(sheet, "verifySpokenLabel(title)", "CompletenessSection")
        assertCalledFrom(sheet, "text = CompletenessWording.VERIFY_HINT", "CompletenessSection")
        assertCalledFrom(sheet, "val overlay = completenessOverlay(row, kind, isGroup)", "DetailHeader")
        assertCalledFrom(sheet, "StatusIcon(kind = cardIconKind(row, kind, isGroup)", "DetailHeader")
        val code = stripComments(read(sheet))
        val start = code.indexOf("private fun gameSummaryFrom(")
        val end = code.indexOf("private fun GameDetailSheetBody(", start)
        assertTrue("gameSummaryFrom must copy the completeness figure", code.substring(start, end).contains("cache_complete_pct = detail.cache_complete_pct"))
    }

    @Test
    fun `MUTATION PIN -- the list row uses fixed cells, the installed column is spoken in full`() {
        assertCalledFrom(row, "sizeCell(LIST_TRAILING_WIDTH)", "GameListRow")
        assertCalledFrom(row, "flagCell(LIST_TRAILING_WIDTH, Alignment.CenterEnd)", "GameListRow")
        assertCalledFrom(row, "statusCell(Modifier.width(LIST_WIDE_STATUS_WIDTH))", "GameListRow")
        assertCalledFrom(row, "installedBadgeSpokenText(model.installedBadge, model.installedNames)", "GameListRow")
        assertCalledFrom(row, "UpdateAvailableLabel(compact = true)", "GameListRow")
    }

    @Test
    fun `MUTATION PIN -- the real vault and demo mode both answer Verify`() {
        assertTrue(stripComments(read("$base/repo/JobsRepository.kt")).contains("client.prefillVerify(appids)"))
        assertTrue(stripComments(read("$base/demo/DemoRepositories.kt")).contains("state.enqueueVerify(appids)"))
        assertTrue(stripComments(read("$base/net/VaultApiClient.kt")).contains("post(\"/v1/prefill/verify\", PrefillRequest(appids))"))
    }
}
