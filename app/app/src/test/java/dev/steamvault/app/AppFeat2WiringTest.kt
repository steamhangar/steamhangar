package dev.steamvault.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Structural pins for WP APP-FEAT-2's UI/controller wiring
 * (docs/LEARNINGS.md: "pinning the pure/model layer proves NOTHING about
 * the pixels" -- every wiring from model to pixel needs a named witness).
 * Same comment-stripped source-scan technique as `Ag3WiringTest`, with the
 * strong generation: the call's NEAREST enclosing composable must be the
 * intended one, and the message tells "deleted" from "moved".
 */
class AppFeat2WiringTest {

    private fun read(path: String): String {
        val file = File(path)
        check(file.exists()) { "expected a source file at ${file.absolutePath}" }
        return file.readText(Charsets.UTF_8)
    }

    private fun stripComments(text: String): String =
        text.replace(Regex("/\\*.*?\\*/", RegexOption.DOT_MATCHES_ALL), "")
            .replace(Regex("//.*"), "")

    private val COMPOSABLE_FUN = Regex("""@Composable\s+(?:private\s+)?fun\s+(\w+)""")

    private fun nearestEnclosingComposable(code: String, callIdx: Int): String? =
        COMPOSABLE_FUN.findAll(code)
            .map { it.range.first to it.groupValues[1] }
            .filter { (start, _) -> start < callIdx }
            .maxByOrNull { (start, _) -> start }
            ?.second

    /**
     * Index of the first CALL of [call]: an occurrence that is not the
     * function's own definition (`fun NAME(`). Should-fix 4 (APP-FEAT-2
     * review): a plain `indexOf` landed on the definition once the call was
     * deleted, and reported "MOVED" (the definition's enclosing composable is
     * itself) instead of "DELETED".
     */
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

    private val downloads = "src/main/java/dev/steamvault/app/ui/downloads/DownloadsScreen.kt"
    private val library = "src/main/java/dev/steamvault/app/ui/library/LibraryScreen.kt"
    private val settings = "src/main/java/dev/steamvault/app/ui/settings/SettingsScreen.kt"
    private val clients = "src/main/java/dev/steamvault/app/ui/clients/ClientsSheet.kt"

    // ---- APP-FIX-2 ----------------------------------------------------------

    @Test
    fun `MUTATION PIN -- Downloads history rows ask for the failure hint and render the hint block`() {
        assertCalledFrom(downloads, "failureHintViewFor(job, excerptState.excerpt, controller.jobs)", "HistoryRow")
        assertCalledFrom(downloads, "FailureHintBlock(", "HistoryRow")
        assertCalledFrom(downloads, "controller.retry(scope, model.appid)", "HistoryRow")
        assertCalledFrom(downloads, "controller.toggleRawOutput(model.jobId)", "HistoryRow")
    }

    @Test
    fun `MUTATION PIN -- Downloads titles take the owned names and may trigger the one owned-list load`() {
        val code = stripComments(read(downloads))
        for (builder in listOf("buildJobCardModel(", "buildQueueRowModel(", "buildHistoryRowModel(")) {
            val call = Regex(Regex.escape(builder) + "[^\\n]*ownedNames\\)").find(code)
            assertTrue("$builder must be passed ownedNames (owned-name fallback, WP APP-FIX-2)", call != null)
        }
        assertCalledFrom(downloads, "ownedNamesByAppid(ownedState?.ownedGamesOrNull)", "DownloadsScreen")
        assertCalledFrom(downloads, "ownedLibrary?.loadIfNeverLoaded()", "DownloadsScreen")
    }

    // ---- APP-FEAT-1 ---------------------------------------------------------

    @Test
    fun `MUTATION PIN -- Library merges the STORED-id owned list, loads it on open, shows the notice`() {
        assertCalledFrom(library, "ownedLibrary.load()", "LibraryScreen")
        assertCalledFrom(library, "mergeLibrary(controller.games, ownedState.ownedGamesOrNull)", "LibraryScreen")
        assertCalledFrom(library, "OwnedNoticeRow(", "LibraryScreen")
        val code = stripComments(read("src/main/java/dev/steamvault/app/ui/library/LibraryController.kt"))
        assertFalse("LibraryController must not read the device's own sign-in for the library anymore", code.contains("identityRepository"))
    }

    @Test
    fun `MUTATION PIN -- Settings renders the Steam library block and the About section`() {
        assertCalledFrom(settings, "SteamLibraryBlock(controller, scope, demoMode)", "SettingsScreen")
        assertCalledFrom(settings, "AboutSection(controller, scope)", "SettingsScreen")
        assertCalledFrom(settings, "controller.loadAbout()", "SettingsScreen")
        assertCalledFrom(settings, "controller.saveLibrarySteamId()", "SteamLibraryBlock")
        assertCalledFrom(settings, "controller.resetLibrarySteamId()", "SteamLibraryBlock")
        assertCalledFrom(settings, "controller.previewLibrarySteamId()", "SteamLibraryBlock")
        assertCalledFrom(settings, "AboutRowView(aboutRowFor(component))", "AboutSection")
    }

    @Test
    fun `MUTATION PIN -- the signed-in shortcut never reads the real identity in demo mode`() {
        val code = stripComments(read(settings))
        val start = code.indexOf("private fun SteamLibraryBlock(")
        val end = code.indexOf("private fun LibraryPreviewResult(", start)
        check(start >= 0 && end > start) { "expected SteamLibraryBlock before LibraryPreviewResult" }
        val body = code.substring(start, end)
        val gate = body.indexOf("if (!demoMode && canSaveLibrarySteamId(response)) {")
        val read = body.indexOf("controller.identityState")
        assertTrue(
            "the identity read must sit inside the 'if (!demoMode && canSaveLibrarySteamId(response))' gate " +
                "(demo never reads the real identity; Weg A: no shortcut where Save cannot store it)",
            gate >= 0 && read > gate,
        )
        assertTrue(
            "the shortcut must carry the vault-wide hint (Weg A)",
            body.indexOf("R.string.settings_library_id_use_signed_in_hint") > read,
        )
    }

    /** Should-fix 2 (APP-FEAT-2 review): the three decisions inside the
     * hint rendering that a deletion would silently undo. */
    @Test
    fun `MUTATION PIN -- raw output only under showOutput, the hint branch yields open, Retry only when offered`() {
        val code = stripComments(read(downloads))
        val ready = code.indexOf("ExcerptState.READY ->")
        val readyEnd = code.indexOf("private fun FailureHintBlock(", ready)
        check(ready >= 0 && readyEnd > ready) { "expected the READY branch before FailureHintBlock in DownloadsScreen.kt" }
        val branch = code.substring(ready, readyEnd)

        val gate = branch.indexOf("if (showOutput) {")
        val rawText = branch.indexOf("display.lines.joinToString(")
        assertTrue("the raw output Text must sit inside 'if (showOutput) {' (collapsed under a hint)", gate >= 0 && rawText > gate)

        val hintIf = branch.indexOf("val showOutput = if (hintView != null) {")
        val elseIdx = branch.indexOf("} else {", hintIf)
        assertTrue("expected 'val showOutput = if (hintView != null) { ... } else {'", hintIf >= 0 && elseIdx > hintIf)
        val lastLineOfHintBranch = branch.substring(hintIf, elseIdx).trimEnd().lines().last().trim()
        assertEquals("the hint branch must evaluate to 'open' (raw output starts collapsed)", "open", lastLineOfHintBranch)
        val elseBody = branch.substring(elseIdx + "} else {".length).trimStart()
        assertTrue("the no-hint branch must evaluate to 'true' (output shown as before)", elseBody.startsWith("true"))

        val block = code.substring(readyEnd)
        val offered = block.indexOf("if (view.retryOffered) {")
        val otherwise = block.indexOf("} else {", offered)
        val retryButton = block.indexOf("Button(", offered)
        assertTrue("the Retry Button must sit inside 'if (view.retryOffered) {'", offered >= 0 && retryButton in (offered + 1) until otherwise)
        assertTrue("an older job must show NEWER_JOB_LINE in the else branch", block.indexOf("NEWER_JOB_LINE", otherwise) > otherwise)
    }

    @Test
    fun `MUTATION PIN -- the settings controller pre-fills the field from the stored setting on load`() {
        val code = stripComments(read("src/main/java/dev/steamvault/app/ui/settings/SettingsController.kt"))
        val start = code.indexOf("suspend fun load(")
        val end = code.indexOf("fun setDraft(", start)
        assertTrue(code.substring(start, end).contains("libraryIdInput = steamIdFromSettings(settingsResponse)"))
    }

    // ---- B2: presence --------------------------------------------------------

    @Test
    fun `MUTATION PIN -- every client row renders the presence line`() {
        assertCalledFrom(clients, "presenceLineFor(model.presence)", "ClientRow")
    }

    @Test
    fun `MainActivity hands both screens the shared owned library and Settings the relay and about repositories`() {
        val code = stripComments(read("src/main/java/dev/steamvault/app/MainActivity.kt"))
        assertEquals(2, Regex("""ownedLibrary = owned,""").findAll(code).count())
        assertEquals(2, Regex("""ownedLibrary = ownedLibraryState,""").findAll(code).count())
        assertTrue(code.contains("VaultSteamRelayRepository(it),") && code.contains("VaultAboutRepository(it),"))
        assertTrue(code.contains("DemoSteamRelayRepository(demo),") && code.contains("DemoAboutRepository(demo),"))
    }
}
