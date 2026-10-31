package dev.steamvault.app.ui.downloads.logic

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.HistoryDeleteOut
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** WP ANDROID-GAPS-1: "Delete history", port of web `history-delete.js`. */
class HistoryDeleteTest {

    @Test
    fun `MUTATION PIN -- the safe variant comes first and is the default`() {
        assertEquals(listOf("older-than-30d", "all"), HISTORY_DELETE_OPTIONS.map { it.value })
        assertEquals("older-than-30d", SCOPE_OLDER)
        assertEquals("all", SCOPE_ALL)
        assertTrue(isHistoryScope("all"))
        assertFalse(isHistoryScope("everything"))
        assertFalse(isHistoryScope(null))
    }

    @Test
    fun `labels, button and dialog text are the web's`() {
        assertEquals("Older than 30 days", HISTORY_DELETE_OPTIONS[0].label)
        assertEquals("Finished downloads and traffic statistics from before the last 30 days.", HISTORY_DELETE_OPTIONS[0].desc)
        assertEquals("Everything", HISTORY_DELETE_OPTIONS[1].label)
        assertEquals(
            "All finished downloads and all traffic statistics. Speed, graph and client figures start again from zero.",
            HISTORY_DELETE_OPTIONS[1].desc,
        )
        assertEquals("Delete history?", HISTORY_DELETE_TITLE)
        assertEquals("What to delete", HISTORY_DELETE_LEGEND)
        assertEquals("Delete everything", historyDeleteButtonText(SCOPE_ALL))
        assertEquals("Delete older than 30 days", historyDeleteButtonText(SCOPE_OLDER))
        assertEquals(
            "Kept: cached games, running, queued and paused downloads, PCs and client names. This cannot be undone.",
            HISTORY_DELETE_KEEPS,
        )
    }

    @Test
    fun `the note counts the deleted downloads per scope`() {
        assertEquals(
            "History deleted: 1 finished download and all traffic statistics.",
            historyDeletedText(HistoryDeleteOut(scope = "all", jobs_deleted = 1)),
        )
        assertEquals(
            "History older than 30 days deleted: 12 finished downloads and the traffic statistics from that time.",
            historyDeletedText(HistoryDeleteOut(scope = "older-than-30d", cutoff = "2026-09-14T08:00:00Z", jobs_deleted = 12)),
        )
    }

    @Test
    fun `MUTATION PIN -- only a 404 reads as an old server, a 401 is shown as itself`() {
        assertEquals(
            "This server cannot delete history yet. Update vault-api to use this.",
            historyDeleteErrorText(VaultApiError.NotFound("x", 404, "Not Found")),
        )
        assertEquals(
            "Could not delete history: Invalid API key",
            historyDeleteErrorText(VaultApiError.Auth("x", 401, "Invalid API key")),
        )
        assertEquals("Could not delete history: Request failed.", historyDeleteErrorText(IllegalStateException()))
    }
}
