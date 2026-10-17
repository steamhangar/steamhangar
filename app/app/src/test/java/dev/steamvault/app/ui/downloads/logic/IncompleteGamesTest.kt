package dev.steamvault.app.ui.downloads.logic

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.net.model.PrefillJobRef
import dev.steamvault.app.net.model.ScheduleOut
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * WP CACHE-COMPLETE-1c (app) -- the Downloads "Incomplete" half and the
 * verify toasts (`IncompleteGames.kt`), the same guarantees the web twin
 * pins by name:
 *  C7 the list: known and < 100, no tool app, no active job, no update flag;
 *  C8 "Complete all" sends only the rows "Complete now" is offered for;
 *  C9 a verify that dedupes onto a RUNNING job says "verifies on the next
 *     run", never that it was queued;
 *  C10 the schedule note: window -> automatic, no window -> manual only,
 *     unknown -> no claim.
 */
class IncompleteGamesTest {

    private fun game(appid: Int, name: String, pct: Int?, missing: Int? = if (pct == null) null else 3, update: Boolean = false, tool: Boolean = false) =
        GameSummary(
            appid = appid,
            name = name,
            status = "done",
            depot_count = 1,
            size_bytes = 1_000_000_000L,
            update_available = update,
            tool_app = tool,
            cache_complete_pct = pct,
            cache_missing_chunks = missing,
        )

    private fun job(id: Int, appid: Int, status: String, type: String = "prefill") =
        JobSummary(id = id, appid = appid, type = type, status = status, created_at = "2026-10-14T09:00:00Z")

    private fun sched(enabled: Boolean, window: String?) = ScheduleOut(
        enabled = enabled,
        window = window,
        interval_minutes = 180,
        client_stale_days = 7,
        server_timezone = "UTC",
        sweep_include_cached = false,
    )

    private fun ref(status: String, deduplicated: Boolean, appid: Int = 10) =
        PrefillJobRef(appid = appid, job_id = 1, status = status, deduplicated = deduplicated)

    @Test
    fun `MUTATION TARGET C7 -- known below 100, no tool app, no active job, no update flag, sorted by name`() {
        val games = listOf(
            game(1, "Zeta", 42),
            game(2, "alpha", 10),
            game(3, "Complete", 100, missing = 0),
            game(4, "Unknown", null),
            game(5, "Tool", 30, tool = true),
            game(6, "Running", 30),
            game(7, "Queued", 30),
            game(8, "Paused", 30),
            game(9, "Flagged", 30, update = true),
            game(10, "Damaged", 99, missing = 0),
            game(11, "Old job done", 30),
        )
        val jobs = listOf(
            job(1, 6, "running"),
            job(2, 7, "queued"),
            job(3, 8, "paused"),
            job(4, 11, "done"),
            job(5, 1, "running", type = "gc"),
        )
        assertEquals(listOf(2, 10, 11, 1), pendingIncomplete(games, jobs).map { it.appid })
        assertEquals(emptyList<GameSummary>(), pendingIncomplete(emptyList(), emptyList()))
    }

    @Test
    fun `MUTATION TARGET C8 -- Complete all sends exactly the completable listed rows`() {
        val rows = pendingIncomplete(listOf(game(1, "A", 40), game(2, "B", 99, missing = 0), game(3, "C", 70)), emptyList())
        assertEquals(listOf(1, 3), completableAppids(rows))
        assertEquals(emptyList<Int>(), completableAppids(listOf(game(2, "B", 99, missing = 0))))
    }

    @Test
    fun `Incomplete texts -- heading, spoken labels, the row's figure`() {
        assertEquals("Incomplete (3)", incompleteHeadingText(3))
        assertEquals("Complete all 1 game", completeAllSpokenLabel(1))
        assertEquals("Complete all 2 games", completeAllSpokenLabel(2))
        assertEquals("Verify — Aurora", completeNowSpokenLabel("Aurora"))
        assertEquals("42%", incompletePctText(game(1, "A", 42)))
        assertEquals("—", incompletePctText(game(1, "A", null)))
    }

    @Test
    fun `MUTATION TARGET C10 -- schedule note`() {
        assertEquals(
            "sweep_include_cached plays no part (user decision 2026-10-14)",
            "These complete automatically in the schedule window 22:00–06:00.",
            incompleteScheduleNote(sched(enabled = true, window = "22:00-06:00")),
        )
        assertEquals("Manual only — no schedule window is set.", incompleteScheduleNote(sched(enabled = false, window = "22:00-06:00")))
        assertEquals("Manual only — no schedule window is set.", incompleteScheduleNote(sched(enabled = true, window = "")))
        assertNull(incompleteScheduleNote(null))
    }

    @Test
    fun `MUTATION TARGET C9 -- verify toast per outcome, a RUNNING dedupe verifies on the next run`() {
        assertEquals(VerifyStartOutcome("Queued to verify.", warn = false, refresh = true), summarizeVerifyStart(listOf(ref("queued", false))))
        assertEquals("Queued to complete.", summarizeVerifyStart(listOf(ref("queued", false)), VerifyMode.COMPLETE).message)
        assertEquals("Already queued — it runs as a verify.", summarizeVerifyStart(listOf(ref("queued", true))).message)
        val running = summarizeVerifyStart(listOf(ref("running", true)))
        assertEquals("Already downloading — verifies on the next run.", running.message)
        assertFalse(running.warn)
        val paused = summarizeVerifyStart(listOf(ref("paused", true)))
        assertEquals("Paused — it verifies when you resume it.", paused.message)
        assertTrue(paused.warn)
        assertEquals("Nothing to verify.", summarizeVerifyStart(emptyList()).message)
        assertEquals("Nothing to complete.", summarizeVerifyStart(emptyList(), VerifyMode.COMPLETE).message)
        val mixed = summarizeVerifyStart(
            listOf(ref("queued", false), ref("queued", false), ref("running", true), ref("paused", true), ref("queued", true)),
            VerifyMode.COMPLETE,
        )
        assertEquals(
            "2 queued to complete · 1 already queued (runs as a verify) · 1 already downloading (verifies on the next run) · 1 paused (verifies when resumed)",
            mixed.message,
        )
        assertTrue(mixed.warn)
    }

    @Test
    fun `verify errors -- a 5xx asks for a re-read, anything else shows the server's detail`() {
        val server = describeVerifyStartError(VaultApiError.Server("boom", 500, "boom"))
        assertTrue(server.refresh)
        assertTrue(server.long)
        assertTrue(server.message.contains("some games may already be queued"))
        val v = describeVerifyStartError(VaultApiError.Validation("x", 422, "tool app"))
        assertEquals(VerifyStartOutcome("tool app", warn = true, refresh = false), v)
        assertEquals("Could not start the verify.", describeVerifyStartError(null).message)
    }
}
