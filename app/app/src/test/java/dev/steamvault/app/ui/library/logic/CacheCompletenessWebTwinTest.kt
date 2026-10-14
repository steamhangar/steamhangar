package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.ui.downloads.logic.IncompleteWording
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.File

/**
 * Twin pin for WP CACHE-COMPLETE-1c: every literal of [CompletenessWording]
 * and [IncompleteWording] must still be in `web/js/lib/cache-completeness.js`
 * (same pattern as `UpdatesAvailableWebTwinTest`). VALUE drift -> update
 * CacheCompleteness.kt / IncompleteGames.kt; GRAMMAR drift (the web changed
 * the string form) -> adjust this test.
 */
class CacheCompletenessWebTwinTest {
    private val file = File("../../web/js/lib/cache-completeness.js")
    private val js: String by lazy {
        check(file.exists()) { "No file at ${file.absolutePath}: MOVED -> update this path; DELETED -> retire the pin deliberately." }
        file.readText(Charsets.UTF_8)
    }

    /** Whole-string literals: must appear double-quoted in the web source. */
    private val quoted = listOf(
        CompletenessWording.UNVERIFIED_WORD,
        CompletenessWording.UNVERIFIED_COMPACT_WORD,
        CompletenessWording.VERIFY_LABEL,
        CompletenessWording.VERIFY_HINT,
        CompletenessWording.DAMAGED_NOTE,
        CompletenessWording.UNKNOWN_LINE,
        CompletenessWording.ZERO_GB,
        IncompleteWording.HEADING,
        IncompleteWording.COMPLETE_NOW,
        IncompleteWording.COMPLETE_ALL,
        IncompleteWording.NOTHING_TO_COMPLETE,
        IncompleteWording.NOTHING_TO_VERIFY,
        IncompleteWording.QUEUED_TO_COMPLETE,
        IncompleteWording.QUEUED_TO_VERIFY,
        IncompleteWording.ALREADY_QUEUED_ONE,
        IncompleteWording.PAUSED_ONE,
        IncompleteWording.RUNNING_ONE,
        IncompleteWording.COMPLETE,
        IncompleteWording.VERIFY,
        IncompleteWording.SERVER_PARTWAY,
        IncompleteWording.START_FAILED,
        IncompleteWording.PCT_UNKNOWN,
    )

    /** Fragments of web template literals: must appear verbatim. */
    private val fragments = listOf(
        "`${CompletenessWording.PARTIAL_PREFIX}\${pct}%`",
        "`${CompletenessWording.PARTIAL_COMPACT_PREFIX}\${pct}%`",
        "`\${pct}${CompletenessWording.COMPLETE_SUFFIX}\${amount}\${checked}`",
        "`\${present}${CompletenessWording.ON_DISK}`",
        "`\${present}${CompletenessWording.OF}\${expected}${CompletenessWording.ON_DISK}`",
        "`${CompletenessWording.CHECKED}\${formatTimestamp(",
        "`${CompletenessWording.GROUP_LINE_PREFIX}\${pct}${CompletenessWording.GROUP_LINE_SUFFIX}`",
        "`\${VERIFY_LABEL}${CompletenessWording.DASH}\${title}`",
        "`\${COMPLETE_NOW_LABEL} — \${title}`",
        "`\${COMPLETE_ALL_LABEL} \${count} \${count === 1 ? \"game\" : \"games\"}`",
        "`\${INCOMPLETE_HEADING} (\${count})`",
        "`${IncompleteWording.NOTE_PREFIX}\${formatWindow(schedule.window)}.`",
        "} ${IncompleteWording.QUEUED_TO} \${",
        "} ${IncompleteWording.ALREADY_QUEUED_MANY}`",
        "} ${IncompleteWording.RUNNING_MANY}`",
        "} ${IncompleteWording.PAUSED_MANY}`",
        "join(\" · \")",
    )

    @Test
    fun `the source is real`() {
        assertTrue("no 'export function cardDisplay' -- re-check path/anchors", js.contains("export function cardDisplay"))
    }

    @Test
    fun `every quoted wording literal is still in the web source`() {
        for (l in quoted) {
            if (!js.contains("\"$l\"")) {
                fail(
                    if (js.contains(l)) {
                        "GRAMMAR DRIFT: cache-completeness.js contains \"$l\" but not as one double-quoted string -- adjust this test."
                    } else {
                        "VALUE DRIFT: cache-completeness.js no longer contains \"$l\" -- update the Kotlin twin."
                    },
                )
            }
        }
    }

    @Test
    fun `every template fragment is still in the web source`() {
        for (f in fragments) {
            assertTrue("VALUE DRIFT: cache-completeness.js no longer contains '$f' -- update the Kotlin twin.", js.contains(f))
        }
    }
}
