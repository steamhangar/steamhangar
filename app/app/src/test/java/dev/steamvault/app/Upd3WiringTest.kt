package dev.steamvault.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Structural pins for WP UPD-3's model-to-pixel wiring (docs/LEARNINGS.md:
 * "pinning the pure/model layer proves NOTHING about the pixels"). The
 * decisions are pinned in `UpdateCheckTest`, `LibraryFiltersTest`,
 * `GameCardModelTest` and `LibraryControllerUpdateCheckTest`; these pins
 * prove the screens call them. Same comment-stripped source scan, strong
 * generation, as `AppFeat2WiringTest`: the call's nearest enclosing
 * composable must be the intended one, and the message tells "deleted"
 * from "moved".
 */
class Upd3WiringTest {

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

    private val library = "src/main/java/dev/steamvault/app/ui/library/LibraryScreen.kt"
    private val card = "src/main/java/dev/steamvault/app/ui/library/GameCard.kt"
    private val row = "src/main/java/dev/steamvault/app/ui/library/GameListRow.kt"
    private val main = "src/main/java/dev/steamvault/app/MainActivity.kt"

    @Test
    fun `MUTATION PIN -- the grid card draws the badge when the model says so`() {
        assertCalledFrom(card, "if (showsUpdateBadge(model))", "GameCard")
        assertCalledFrom(card, "UpdateAvailableBadge(", "GameCard")
        // Review fix: the badge keeps clear of the select checkbox.
        assertCalledFrom(card, "padding(start = UPDATE_BADGE_START_CLEARANCE", "GameCard")
    }

    @Test
    fun `MUTATION PIN -- the list row draws the update line when the model says so`() {
        assertCalledFrom(row, "if (showsUpdateBadge(model))", "GameListRow")
        assertCalledFrom(row, "UpdateAvailableLabel(", "GameListRow")
    }

    @Test
    fun `MUTATION PIN -- the top bar carries Check updates, and the action starts the check`() {
        assertCalledFrom(library, "CheckUpdatesAction(controller = controller, scope = scope)", "LibraryScreen")
        assertCalledFrom(library, "controller.checkUpdates(scope)", "CheckUpdatesAction")
        assertCalledFrom(library, "if (!controller.updatesSupported) return", "CheckUpdatesAction")
        assertCalledFrom(library, "val off = !controller.updatesEnabled", "CheckUpdatesAction")
        assertCalledFrom(library, "enabled = !off && !controller.updateCheckRunning", "CheckUpdatesAction")
    }

    @Test
    fun `MUTATION PIN -- the Library probes for the feature and hides the Updates chip on a 404`() {
        assertCalledFrom(library, "controller.probeUpdatesSupport()", "LibraryScreen")
        assertCalledFrom(library, "hiddenChipKeys(controller.updatesSupported)", "LibraryScreen")
    }

    @Test
    fun `MUTATION PIN -- the real vault and demo mode both hand the Library an updates repository`() {
        val code = stripComments(read(main))
        assertTrue(
            "MainActivity must pass VaultUpdatesRepository(client) to the real LibraryScreen",
            code.contains("updatesRepository = remember(client) { VaultUpdatesRepository(client) }"),
        )
        assertTrue(
            "MainActivity must pass DemoUpdatesRepository(demo) to the demo LibraryScreen",
            code.contains("updatesRepository = remember(demo) { DemoUpdatesRepository(demo) }"),
        )
    }
}
