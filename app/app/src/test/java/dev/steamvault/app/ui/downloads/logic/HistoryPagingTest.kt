package dev.steamvault.app.ui.downloads.logic

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * WP ANDROID-DL-1: `HistoryPaging.kt`, the port of web
 * `lib/history-paging.js`. The cases are web/tests/downloads-history-paging.test.js's
 * own, every expected value a LITERAL (docs/LEARNINGS.md, Android:
 * literal-vs-literal contracts).
 */
class HistoryPagingTest {

    @Test
    fun `MUTATION PIN -- historyPage, 10 first, then 20 per step, the button text and name, the capped note only at the end`() {
        assertEquals(200, dev.steamvault.app.ui.traffic.logic.HISTORY_LIMIT)
        assertEquals(10, HISTORY_FIRST_PAGE)
        assertEquals(20, HISTORY_PAGE_STEP)
        assertEquals(HistoryPage(visible = 10, remaining = 117, next = 20, capped = false), historyPage(127, 10))
        assertEquals(HistoryPage(visible = 110, remaining = 17, next = 17, capped = false), historyPage(127, 110))
        assertEquals(HistoryPage(visible = 127, remaining = 0, next = 0, capped = false), historyPage(127, 130))
        assertEquals(HistoryPage(visible = 4, remaining = 0, next = 0, capped = false), historyPage(4, 10))
        assertEquals(HistoryPage(visible = 0, remaining = 0, next = 0, capped = false), historyPage(0, 10, capped = true))
        assertFalse("the note waits until every row is shown", historyPage(200, 30, capped = true).capped)
        assertTrue(historyPage(200, 210, capped = true).capped)
        assertEquals(30, nextShown(10))
        assertEquals("Show 20 more", showMoreText(20))
        assertEquals("Show 20 more of 117", showMoreLabel(20, 117))
        assertFalse(isHistoryCapped(199))
        assertTrue(isHistoryCapped(200))
        assertEquals("Older entries are not shown; narrow the range or delete history.", HISTORY_CAPPED_TEXT)
    }

    @Test
    fun `nonsense counts fall back like the web's`() {
        // A non-positive "shown" means the first page; a negative total is empty.
        assertEquals(HistoryPage(visible = 10, remaining = 5, next = 5, capped = false), historyPage(15, 0))
        assertEquals(HistoryPage(visible = 0, remaining = 0, next = 0, capped = false), historyPage(-3, 10))
        assertEquals(30, nextShown(0))
    }

    @Test
    fun `MUTATION PIN -- idleSubtitle, no number before the range fetch, then the list's own count (+ when capped)`() {
        assertEquals("Idle", idleSubtitle(loaded = false, count = 20))
        assertEquals("Idle · 127 in history", idleSubtitle(loaded = true, count = 127))
        assertEquals("Idle · 200+ in history", idleSubtitle(loaded = true, count = 200, capped = true))
    }

    @Test
    fun `historySubtitle counts the ranged list and caps it at the fetch limit`() {
        // The 20-job poll is not the count: a 7-day list of 127 rows reads 127.
        assertEquals("Idle · 127 in history", historySubtitle(loaded = true, finishedCount = 127, fetchedCount = 120))
        assertEquals("Idle", historySubtitle(loaded = false, finishedCount = 20, fetchedCount = 0))
        // A full fetch: older rows may exist; the poll's newest jobs on top never push it past the limit.
        assertEquals("Idle · 200+ in history", historySubtitle(loaded = true, finishedCount = 205, fetchedCount = 200))
        assertEquals("Idle · 0 in history", historySubtitle(loaded = true, finishedCount = 0, fetchedCount = 0))
    }
}
