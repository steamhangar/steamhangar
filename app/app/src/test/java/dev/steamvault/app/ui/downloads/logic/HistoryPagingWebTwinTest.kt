package dev.steamvault.app.ui.downloads.logic

import org.junit.Assert.fail
import org.junit.Test
import java.io.File

/**
 * Twin pin for WP ANDROID-DL-1: the History paging and the idle subline
 * follow web `lib/history-paging.js` (WP DOWNLOADS-3). Kotlin side: literal
 * equality in `HistoryPagingTest`. VALUE drift -> update HistoryPaging.kt;
 * GRAMMAR drift (same rule, new spelling) -> adjust this test. Never edit
 * the web literals to satisfy it.
 */
class HistoryPagingWebTwinTest {
    private val path = "lib/history-paging.js"
    private val js: String by lazy {
        val file = File("../../web/js/$path")
        check(file.exists()) { "No file at ${file.absolutePath}: MOVED -> update this path; DELETED -> retire the pin deliberately." }
        file.readText(Charsets.UTF_8)
    }

    @Test
    fun `history-paging literals and rules`() {
        for (f in listOf(
            "export const HISTORY_LIMIT = ${dev.steamvault.app.ui.traffic.logic.HISTORY_LIMIT};",
            "export const HISTORY_FIRST_PAGE = $HISTORY_FIRST_PAGE;",
            "export const HISTORY_PAGE_STEP = $HISTORY_PAGE_STEP;",
            "export const HISTORY_CAPPED_TEXT = \"$HISTORY_CAPPED_TEXT\";",
            "return `Show \${next} more`;",
            "return `Show \${next} more of \${remaining}`;",
            "if (!loaded) return \"Idle\";",
            "return `Idle · \${count}\${capped ? \"+\" : \"\"} in history`;",
            "next: Math.min(HISTORY_PAGE_STEP, remaining),",
            "capped: !!capped && remaining === 0 && t > 0,",
            "return Number.isFinite(fetchedCount) && fetchedCount >= HISTORY_LIMIT;",
        )) {
            if (!js.contains(f)) fail("VALUE DRIFT: $path no longer contains the fragment \"$f\" -- update HistoryPaging.kt.")
        }
    }

    @Test
    fun `the page counts the ranged list, capped at the fetch limit`() {
        val view = File("../../web/js/views/downloads.js")
        check(view.exists()) { "No file at ${view.absolutePath}: MOVED -> update this path." }
        val f = "return idleSubtitle({ loaded: history.loaded, count: capped ? Math.min(count, HISTORY_LIMIT) : count, capped });"
        if (!view.readText(Charsets.UTF_8).contains(f)) fail("VALUE DRIFT: views/downloads.js no longer contains \"$f\" -- update historySubtitle.")
    }
}
