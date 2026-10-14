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

/** WP UPD-6 -- twin of web/tests/updates-available.test.js. */
class UpdatesAvailableTest {

    private fun game(appid: Int, name: String?, flagged: Boolean = true, tool: Boolean = false, force: Boolean = false) =
        GameSummary(
            appid = appid,
            name = name,
            status = "done",
            last_prefill_at = "2026-10-14T08:00:00Z",
            depot_count = 1,
            size_bytes = 1_000_000_000L,
            needs_force = force,
            tool_app = tool,
            update_available = flagged,
        )

    private fun job(appid: Int, status: String, type: String = "prefill") =
        JobSummary(id = appid, appid = appid, type = type, status = status, created_at = "2026-10-14T08:00:00Z")

    private fun schedule(enabled: Boolean, window: String?, includeCached: Boolean = true) = ScheduleOut(
        enabled = enabled,
        window = window,
        interval_minutes = 60,
        client_stale_days = 7,
        server_timezone = "Europe/Berlin",
        sweep_include_cached = includeCached,
    )

    // ---- pendingUpdates ---------------------------------------------------

    @Test
    fun `MUTATION PIN -- flagged games only, sorted by name case-insensitively, then appid`() {
        val games = listOf(
            game(3, "copper Horizon"),
            game(1, "Aurora Cascade"),
            game(2, "Not flagged", flagged = false),
            game(5, "Beacon"),
            game(4, "beacon"),
        )
        assertEquals(listOf(1, 4, 5, 3), pendingUpdates(games, emptyList()).map { it.appid })
    }

    @Test
    fun `MUTATION PIN -- a game with a queued, running or paused prefill leaves the list`() {
        val games = listOf(game(1, "A"), game(2, "B"), game(3, "C"), game(4, "D"), game(5, "E"))
        val jobs = listOf(
            job(1, "queued"),
            job(2, "running"),
            job(3, "paused"),
            job(4, "done"), // finished: back in the list until the server clears the flag
            job(5, "running", type = "gc"), // not a prefill: stays
        )
        assertEquals(listOf(4, 5), pendingUpdates(games, jobs).map { it.appid })
    }

    @Test
    fun `a Steam tool app never appears`() {
        assertEquals(listOf(1), pendingUpdates(listOf(game(1, "A"), game(2, "Tool", tool = true)), emptyList()).map { it.appid })
    }

    @Test
    fun `sorts by the caller's display name`() {
        val games = listOf(game(1, null), game(2, "Zeta"))
        val titled = pendingUpdates(games, emptyList()) { if (it.appid == 1) "Zz package" else it.name!! }
        assertEquals(listOf(2, 1), titled.map { it.appid })
    }

    // ---- wording -----------------------------------------------------------

    @Test
    fun `heading and spoken labels`() {
        assertEquals("Updates available (7)", updatesHeadingText(7))
        assertEquals("Update all 1 game", updateAllSpokenLabel(1))
        assertEquals("Update all 7 games", updateAllSpokenLabel(7))
        assertEquals("Update now — Emberreach", updateNowSpokenLabel("Emberreach"))
    }

    @Test
    fun `formatWindow puts an en dash between two HH-MM times, anything else stays as configured`() {
        assertEquals("22:00–06:00", formatWindow("22:00-06:00"))
        assertEquals("1:30–5:00", formatWindow(" 1:30 - 5:00 "))
        assertEquals("nightly", formatWindow(" nightly "))
    }

    @Test
    fun `MUTATION PIN -- schedule note -- unknown, manual only, window for all, window for installed only`() {
        assertNull(scheduleNote(null))
        assertEquals("Manual only — no schedule window is set.", scheduleNote(schedule(enabled = false, window = null)))
        assertEquals("Manual only — no schedule window is set.", scheduleNote(schedule(enabled = true, window = " ")))
        assertEquals("Manual only — no schedule window is set.", scheduleNote(schedule(enabled = false, window = "22:00-06:00")))
        assertEquals(
            "These download automatically in the schedule window 22:00–06:00.",
            scheduleNote(schedule(enabled = true, window = "22:00-06:00")),
        )
        assertEquals(
            "Those installed on a PC download automatically in the schedule window 22:00–06:00; " +
                "the others only when you start them here.",
            scheduleNote(schedule(enabled = true, window = "22:00-06:00", includeCached = false)),
        )
    }

    // ---- toasts ------------------------------------------------------------

    private fun ref(appid: Int, status: String = "queued", dedup: Boolean = false) =
        PrefillJobRef(appid = appid, job_id = appid, status = status, deduplicated = dedup)

    @Test
    fun `summarizeUpdateStart reports every part and the forced note`() {
        val games = listOf(game(1, "A", force = true), game(2, "B"))
        val out = summarizeUpdateStart(listOf(ref(1), ref(2), ref(3, "running", dedup = true)), games)
        assertEquals("2 queued for update · 1 already in progress (1 forced — those run full, disk-speed re-checks and may take longer)", out.message)
        assertFalse(out.warn)
        assertTrue(out.refresh)

        val paused = summarizeUpdateStart(listOf(ref(4, "paused", dedup = true)), games)
        assertEquals("1 paused — resume or cancel it first", paused.message)
        assertTrue(paused.warn)

        assertEquals("Nothing to update.", summarizeUpdateStart(emptyList(), games).message)
    }

    @Test
    fun `describeUpdateStartError -- a 5xx asks for a re-read, anything else shows its detail`() {
        val server = describeUpdateStartError(VaultApiError.Server("boom", 500))
        assertTrue(server.refresh)
        assertTrue(server.message.startsWith("The server had trouble partway through"))
        val validation = describeUpdateStartError(VaultApiError.Validation("bad", 422, detail = "tool app"))
        assertEquals("tool app", validation.message)
        assertFalse(validation.refresh)
        assertEquals("Could not start the update.", describeUpdateStartError(null).message)
    }

    @Test
    fun `MUTATION PIN -- sheetUpdateAppids -- flagged members without an active job, in appid order`() {
        val members = listOf(game(300, "Zeta"), game(101, "Alpha pack"), game(102, "Plain", flagged = false), game(103, "Busy pack"))
        assertEquals(listOf(101, 300), sheetUpdateAppids(members, listOf(job(103, "running"))))
        assertEquals(emptyList<Int>(), sheetUpdateAppids(listOf(game(1, "Tool", tool = true)), emptyList()))
    }

    @Test
    fun `only the 5xx toast is a long one`() {
        assertTrue(describeUpdateStartError(VaultApiError.Server("boom", 500)).long)
        assertFalse(describeUpdateStartError(VaultApiError.Validation("bad", 422)).long)
        assertFalse(summarizeUpdateStart(listOf(ref(1)), emptyList()).long)
    }
}
