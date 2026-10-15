package dev.steamvault.app.ui.library.logic

import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Twin pin for WP LIB-SORT: every label of [LibrarySortKey] and
 * [LibrarySortWording] must still be a double-quoted literal in
 * `web/js/lib/library-sort.js` (same pattern as `CacheCompletenessWebTwinTest`).
 * VALUE drift -> update LibrarySort.kt; GRAMMAR drift -> adjust this test.
 */
class LibrarySortWebTwinTest {
    private val file = File("../../web/js/lib/library-sort.js")
    private val js: String by lazy {
        check(file.exists()) { "No file at ${file.absolutePath}: MOVED -> update this path; DELETED -> retire the pin deliberately." }
        file.readText(Charsets.UTF_8)
    }

    @Test
    fun `sort labels and keys are still in the web source`() {
        // Label-key pairs must sit together inside the SORT_OPTIONS block.
        val block = js.substringAfter("export const SORT_OPTIONS").substringBefore("]);")
        val badPairs = LibrarySortKey.entries.filter {
            !Regex("key:\\s*SORT_KEY\\.${it.name},\\s*label:\\s*\"${Regex.escape(it.label)}\"").containsMatchIn(block)
        }
        assertTrue("Not in SORT_OPTIONS as key/label pair: $badPairs", badPairs.isEmpty())
        val keyBlock = js.substringAfter("export const SORT_KEY").substringBefore("});")
        val badKeys = LibrarySortKey.entries.filter { !Regex("${it.name}:\\s*\"${it.prefValue}\"").containsMatchIn(keyBlock) }
        assertTrue("Not in SORT_KEY: $badKeys", badKeys.isEmpty())
        val literals = listOf(
            LibrarySortWording.SORT_LABEL,
            LibrarySortWording.ASCENDING,
            LibrarySortWording.DESCENDING,
            LibrarySortWording.NAME_ASC,
            LibrarySortWording.NAME_DESC,
            SortDirection.ASC.prefValue,
            SortDirection.DESC.prefValue,
        )
        val missing = literals.filter { !js.contains("\"$it\"") }
        assertTrue("Missing from web/js/lib/library-sort.js: $missing", missing.isEmpty())
    }
}
