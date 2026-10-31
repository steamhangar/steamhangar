package dev.steamvault.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Structural pins for WP ANDROID-GAPS-1's model-to-pixel wiring
 * (docs/LEARNINGS.md: "pinning the pure/model layer proves NOTHING about
 * the pixels"). The decisions are pinned in `ClientRemoveTest`,
 * `ClientNamesTest`, `HistoryDeleteTest`, the flow/controller tests and
 * `OnboardingLibraryIdTest`; these pins prove the screens call them. Same
 * comment-stripped source scan, strong generation, as `D15dWiringTest`:
 * the call's nearest enclosing function must be the intended one, and the
 * message tells "deleted" from "moved".
 */
class AndroidGaps1WiringTest {

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

    private val base = "src/main/java/dev/steamvault/app"
    private val main = "$base/MainActivity.kt"
    private val sheet = "$base/ui/clients/ClientsSheet.kt"
    private val clientsScreen = "$base/ui/clients/ClientsScreen.kt"
    private val cardViews = "$base/ui/clients/ClientCardViews.kt"
    private val rename = "$base/ui/clients/ClientRenameDialog.kt"
    private val downloads = "$base/ui/downloads/DownloadsScreen.kt"
    private val historyDialog = "$base/ui/downloads/HistoryDeleteDialog.kt"
    private val onboarding = "$base/ui/onboarding/OnboardingScreen.kt"

    @Test
    fun `MUTATION PIN -- the PCs sheet offers Remove per row and confirms before the request`() {
        assertCalledFrom(sheet, "RemovePcConfirm(controller, scope)", "ClientsSheet")
        assertCalledFrom(sheet, "onRemove = { controller.requestRemove(client.client_id) }", "ClientsSheetBody")
        assertCalledFrom(sheet, "RemoveAction(clientId = model.clientId, removeError = removeError, onRemove = onRemove)", "ClientRow")
        assertCalledFrom(sheet, "if (isRemovableClientId(clientId)) {", "RemoveAction")
        assertCalledFrom(sheet, "text = UNREMOVABLE_SLASH_NOTE", "RemoveAction")
        assertCalledFrom(sheet, "contentDescription = label", "RemoveAction")
        assertCalledFrom(sheet, "liveRegion = LiveRegionMode.Assertive", "RemoveAction")
        assertCalledFrom(sheet, "scope.launch { controller.confirmRemove() }", "RemovePcConfirm")
        assertCalledFrom(sheet, "onDismissRequest = { controller.cancelRemove() }", "RemovePcConfirm")
        assertCalledFrom(sheet, "Text(REMOVE_WHAT_TEXT", "RemovePcConfirm")
        assertCalledFrom(sheet, "Text(REMOVE_REREGISTER_TEXT", "RemovePcConfirm")
        assertCalledFrom(sheet, "controller.removeNotice?.let", "ClientsSheetBody")
    }

    @Test
    fun `MUTATION PIN -- Clients cards offer Rename, the dialog saves and clears, real and demo get a repository`() {
        assertCalledFrom(clientsScreen, "ClientRenameFlow(it)", "ClientsScreen")
        assertCalledFrom(clientsScreen, "ClientRenameDialog(", "ClientsScreen")
        assertCalledFrom(clientsScreen, "renameFlow.open(client)", "ClientsBody")
        // WP ANDROID-REDESIGN-1a: the card moved to ClientCardViews.kt; Rename sits in its footer row.
        assertCalledFrom(cardViews, "if (showRename) RenameButton(model.name, onRename!!)", "CardFooter")
        assertCalledFrom(cardViews, "val showRename = model.rename && onRename != null", "CardFooter")
        assertCalledFrom(clientsScreen, "PageNotices(renameFlow, clientsController)", "ClientsScreen")
        assertCalledFrom(clientsScreen, "RenameNotice(renameFlow)", "PageNotices")
        assertCalledFrom(rename, "scope.launch { flow.save(onChanged) }", "ClientRenameDialog")
        assertCalledFrom(rename, "scope.launch { flow.clear(displayNameAfter, onChanged) }", "ClientRenameDialog")
        assertCalledFrom(rename, "if (hasCustomName(client)) {", "ClientRenameDialog")
        assertCalledFrom(rename, "renameHint(client)", "ClientRenameDialog")
        assertCalledFrom(main, "clientsRepository = remember(client) { VaultClientsRepository(client) }", "ClientsDestinationContent")
        assertCalledFrom(main, "clientsRepository = remember(demo) { DemoClientsRepository(demo) }", "ClientsDestinationContent")
    }

    @Test
    fun `MUTATION PIN -- Downloads offers Delete history, real and demo, and re-reads after it`() {
        assertCalledFrom(downloads, "HistoryDeleteFlow(it)", "DownloadsScreen")
        assertCalledFrom(downloads, "HistoryDeleteDialog(historyDelete, scope)", "DownloadsScreen")
        assertCalledFrom(downloads, "controller.showNotice(historyDeletedText(result))", "DownloadsScreen")
        assertCalledFrom(downloads, "controller.refreshJobsOnce()\n                    historyFilter.load()\n                    statsPoll.refreshOnce()", "DownloadsScreen")
        assertCalledFrom(downloads, "HistoryDeleteButton(historyDelete)", "DownloadsBody")
        assertCalledFrom(historyDialog, "flow.confirm()", "HistoryDeleteDialog")
        assertCalledFrom(historyDialog, "for (option in HISTORY_DELETE_OPTIONS)", "HistoryDeleteDialog")
        assertCalledFrom(historyDialog, "Text(HISTORY_DELETE_KEEPS", "HistoryDeleteDialog")
        assertCalledFrom(historyDialog, "historyDeleteButtonText(flow.scope)", "HistoryDeleteDialog")
        assertCalledFrom(main, "historyRepository = remember(client) { VaultHistoryRepository(client) }", "DownloadsDestinationContent")
        assertCalledFrom(main, "historyRepository = remember(demo) { DemoHistoryRepository(demo) }", "DownloadsDestinationContent")
    }

    @Test
    fun `MUTATION PIN -- onboarding step 2 shows the SteamID64 field, Look up and the save note`() {
        assertCalledFrom(onboarding, "LibrarySteamIdSection(controller, scope)", "SteamStep")
        assertCalledFrom(onboarding, "value = controller.libraryIdInput", "LibrarySteamIdSection")
        assertCalledFrom(onboarding, "controller.startLibraryLookup(scope)", "LibrarySteamIdSection")
        // Review must-fix: Finish waits for a running Look up and its save
        // (web finishOnce awaits pendingLookup), and is disabled meanwhile.
        assertCalledFrom(onboarding, "controller.finishAfterPendingLookup()\n                        onFinished()", "OnboardingNav")
        assertCalledFrom(onboarding, "enabled = controller.canAdvance && !controller.finishing", "OnboardingNav")
        assertCalledFrom(onboarding, "LookupResult(controller.libraryLookup, controller.libraryLookupError)", "LibrarySteamIdSection")
        assertCalledFrom(onboarding, "text = saveNoteText(note)", "LibrarySteamIdSection")
    }
}
