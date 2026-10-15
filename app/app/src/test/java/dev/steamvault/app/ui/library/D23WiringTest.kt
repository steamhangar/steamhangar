package dev.steamvault.app.ui.library

import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Wiring pins for WP D23's pixels (docs/LEARNINGS.md: pinning the model layer
 * proves nothing about the UI): the panel must be drawn from LibraryScreen
 * ONLY while some game has metadata, the filtered rows must feed the grid and
 * the chip counts, and the preferences must be written by the controller.
 */
class D23WiringTest {
    private fun code(path: String): String {
        val f = File("src/main/java/dev/steamvault/app/$path")
        check(f.exists()) { "expected a source file at ${f.absolutePath}" }
        return f.readText(Charsets.UTF_8)
            .replace(Regex("/\\*.*?\\*/", RegexOption.DOT_MATCHES_ALL), "")
            .replace(Regex("//.*"), "")
    }

    private fun squash(s: String) = s.replace(Regex("\\s+"), " ")

    private fun assertHas(text: String, needle: String, why: String) =
        assertTrue("$why: '$needle' is missing (deleted or moved)", squash(text).contains(squash(needle)))

    @Test
    fun `the screen draws the panel only with metadata and feeds the filtered rows on`() {
        val screen = code("ui/library/LibraryScreen.kt")
        assertHas(screen, "metaAvailable(controller.games)", "panel gate")
        assertHas(screen, "if (hasMeta) {\n                LibraryMetaPanel(", "panel is drawn only with metadata")
        assertHas(screen, "applyMetaFilter(grouped.rows, controller.metaFilter)", "filter applied")
        assertHas(screen, "visibleGames(metaResult.games", "grid reads the filtered rows")
        assertHas(screen, "chipCounts(\n            metaResult.games", "chip counts read the filtered rows")
        assertHas(screen, "normalizeSort(controller.sort, sortAvailable, hasMeta)", "sort gated by metadata")
        assertHas(screen, "availableSortKeys(sortAvailable, hasMeta)", "sort options gated by metadata")
        assertHas(screen, "sortAfterKeyPick(", "direction reset on key pick")
        assertHas(screen, "unknownExcluded = metaResult.unknownExcluded", "unknown note count")
        assertHas(screen, "controller.selectMetaFilter(it)", "panel changes reach the controller")
    }

    @Test
    fun `the panel buttons reach the callback and the note is drawn`() {
        val panel = code("ui/library/LibraryMetaPanel.kt")
        assertHas(panel, "onChange(MetaFilter.DEFAULT)", "clear filters")
        assertHas(panel, "includeUnknown = it", "include unknown")
        assertHas(panel, "unknownNote(unknownExcluded)", "hidden note")
        assertHas(panel, "MetaFilterWording.INCLUDE_UNKNOWN", "include unknown label")
    }

    @Test
    fun `the controller normalizes and persists the filter`() {
        val c = code("ui/library/LibraryController.kt")
        assertHas(c, "libraryPreferences.getMetaFilter()", "restored per device")
        assertHas(c, "libraryPreferences.setMetaFilter(", "persisted per device")
        assertHas(c, "normalizeMetaFilter(", "normalized before use")
    }
}
