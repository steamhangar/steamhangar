package dev.steamvault.app.ui.downloads.logic

import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.File

/**
 * Twin pin for WP UPD-6: every literal of [UpdatesWording] must still be in
 * `web/js/lib/updates-available.js` (same pattern as `BulkJobsWebTwinTest`).
 * VALUE drift -> update UpdatesAvailable.kt; GRAMMAR drift (the web changed
 * the string form) -> adjust this test.
 */
class UpdatesAvailableWebTwinTest {
    private val file = File("../../web/js/lib/updates-available.js")
    private val js: String by lazy {
        check(file.exists()) { "No file at ${file.absolutePath}: MOVED -> update this path; DELETED -> retire the pin deliberately." }
        file.readText(Charsets.UTF_8)
    }

    /** Whole-string literals: must appear double-quoted in the web source. */
    private val quoted = listOf(
        UpdatesWording.HEADING, UpdatesWording.UPDATE_NOW, UpdatesWording.UPDATE_ALL, UpdatesWording.STARTING,
        UpdatesWording.MANUAL_ONLY_NOTE, UpdatesWording.NOTE_INSTALLED_ONLY_SUFFIX, UpdatesWording.NOTHING_TO_UPDATE,
        UpdatesWording.SERVER_PARTWAY, UpdatesWording.START_FAILED,
    )

    /** Fragments of web template literals: must appear verbatim. */
    private val fragments = listOf(
        UpdatesWording.NOTE_INSTALLED_ONLY_PREFIX + "\${window}; ",
        UpdatesWording.NOTE_ALL_PREFIX + "\${window}.",
        UpdatesWording.QUEUED_FOR_UPDATE,
    )

    @Test
    fun `the source is real`() {
        assertTrue("no 'export function scheduleNote' -- re-check path/anchors", js.contains("export function scheduleNote"))
    }

    @Test
    fun `every quoted wording literal is still in the web source`() {
        for (l in quoted) {
            if (!js.contains("\"$l\"")) {
                fail(
                    if (js.contains(l)) {
                        "GRAMMAR DRIFT: updates-available.js contains \"$l\" but not as one double-quoted string -- adjust this test."
                    } else {
                        "VALUE DRIFT: updates-available.js no longer contains \"$l\" -- update UpdatesAvailable.kt."
                    },
                )
            }
        }
    }

    @Test
    fun `every template fragment is still in the web source`() {
        for (f in fragments) {
            assertTrue("VALUE DRIFT: updates-available.js no longer contains '$f' -- update UpdatesAvailable.kt.", js.contains(f))
        }
    }
}
