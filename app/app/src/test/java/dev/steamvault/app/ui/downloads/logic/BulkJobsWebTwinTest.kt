package dev.steamvault.app.ui.downloads.logic

import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.File

/**
 * Twin pin for WP WEB-FEAT-5: every literal of [BulkWording] must still be a
 * double-quoted string in `web/js/lib/bulk-jobs.js`, and the concurrency
 * bound must match. VALUE drift -> update BulkJobs.kt; GRAMMAR drift (the
 * web changed the string form) -> adjust [normalisedJs] here.
 */
class BulkJobsWebTwinTest {
    private val file = File("../../web/js/lib/bulk-jobs.js")
    private val js: String by lazy {
        check(file.exists()) { "No file at ${file.absolutePath}: MOVED -> update this path; DELETED -> retire the pin deliberately." }
        file.readText(Charsets.UTF_8)
    }
    private val normalisedJs: String by lazy { js.replace(Regex("\"\\s*\\+\\s*\""), "").replace(Regex("\"\\s*\\n\\s*\""), "") }

    private val literals = listOf(
        BulkWording.PAUSE_ALL, BulkWording.RESUME_ALL, BulkWording.PAUSING, BulkWording.RESUMING,
        BulkWording.PAUSE_ARIA_ONE, BulkWording.PAUSE_ARIA_MANY, BulkWording.RESUME_ARIA_ONE,
        BulkWording.RESUME_ARIA_MANY, BulkWording.GROUP_LABEL, BulkWording.SCHEDULER_NOTE,
        BulkWording.CONFIRM_TITLE_ONE, BulkWording.CONFIRM_TITLE_MANY, BulkWording.CONFIRM_BODY,
        BulkWording.CONFIRM_GC_NOTE, BulkWording.CONFIRM_YES, BulkWording.CONFIRM_NO,
        BulkWording.PAUSED_ALL_ONE, BulkWording.PAUSED_ALL_MANY, BulkWording.RESUMED_ALL_ONE,
        BulkWording.RESUMED_ALL_MANY, BulkWording.PAUSED_PARTIAL, BulkWording.RESUMED_PARTIAL,
        BulkWording.PAUSE_FAILED, BulkWording.RESUME_FAILED, BulkWording.SKIPPED, BulkWording.FALLBACK_REASON,
    )

    @Test
    fun `the source is real`() {
        assertTrue("no 'export const WORDING' -- re-check path/anchors", js.contains("export const WORDING"))
    }

    @Test
    fun `every wording literal is still in the web source`() {
        for (l in literals) {
            if (!normalisedJs.contains("\"$l\"")) {
                fail(
                    if (normalisedJs.contains(l)) {
                        "GRAMMAR DRIFT: web bulk-jobs.js contains \"$l\" but not as one double-quoted string -- adjust this test."
                    } else {
                        "VALUE DRIFT: web bulk-jobs.js no longer contains \"$l\" -- update BulkJobs.kt."
                    },
                )
            }
        }
    }

    @Test
    fun `the concurrency bound matches the web`() {
        assertTrue(
            "BULK_CONCURRENCY drifted from web (expected ${BULK_CONCURRENCY})",
            Regex("BULK_CONCURRENCY\\s*=\\s*$BULK_CONCURRENCY\\s*;").containsMatchIn(js),
        )
    }
}
