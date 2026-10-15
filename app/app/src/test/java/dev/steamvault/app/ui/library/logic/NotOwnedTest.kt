package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.ui.downloads.logic.jobIconKind
import dev.steamvault.app.ui.downloads.logic.jobStatusWord
import dev.steamvault.app.ui.status.StatusKind
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * The `not_owned` outcome on Android (WP NOT-OWNED-1), the twin of
 * `web/tests/not-owned.test.js`.
 *
 * A game row with `status: "not_owned"` (SteamPrefill did not consider the
 * app) shows "Not available to prefill (not owned)" with the neutral dash
 * kind, never Failed, offers a muted "Check again" instead of Retry, and is
 * no bulk download target. A finished `not_owned` job is history, drawn
 * neutral. The status string is free text on the wire, so an older server
 * simply never sends it.
 */
class NotOwnedTest {

    private fun notOwned(appid: Int = 7): GameSummary = GameSummary(
        appid = appid,
        name = "Game",
        status = "not_owned",
        depot_count = 0,
        size_bytes = null,
        needs_force = true,
    )

    private fun errored(appid: Int = 440): GameSummary = GameSummary(
        appid = appid,
        name = "Team Fortress 2",
        status = "error",
        depot_count = 1,
        size_bytes = null,
        needs_force = true,
    )

    @Test
    fun `the not-owned word is the agreed copy and equals web game-status js`() {
        assertEquals("Not available to prefill (not owned)", NOT_OWNED_STATE_WORD)
        val file = File("../../web/js/lib/game-status.js")
        check(file.exists()) { "expected a file at ${file.absolutePath}" }
        val m = Regex("""export const NOT_OWNED_STATE_WORD = "([^"]*)";""").find(file.readText(Charsets.UTF_8))
            ?: error("GRAMMAR DRIFT: no 'export const NOT_OWNED_STATE_WORD = \"...\";' in web/js/lib/game-status.js -- widen this regex")
        assertEquals("VALUE DRIFT: update GameStatus.kt", m.groupValues[1], NOT_OWNED_STATE_WORD)
    }

    @Test
    fun `MUTATION TARGET -- dispKind maps not_owned to NOTINUSE, never ERROR`() {
        assertEquals(StatusKind.NOTINUSE, dispKind(notOwned(), null))
        assertEquals(StatusKind.ERROR, dispKind(errored(), null))
        assertTrue(isNotOwned(notOwned()))
        assertFalse(isNotOwned(errored()))
    }

    @Test
    fun `the not-owned word replaces the tool word only when flagged`() {
        assertEquals(NOT_OWNED_STATE_WORD, toolAppStateWordFor(StatusKind.NOTINUSE, notOwned = true))
        assertEquals(TOOL_APP_STATE_WORD, toolAppStateWordFor(StatusKind.NOTINUSE))
        assertNull(toolAppStateWordFor(StatusKind.ERROR, notOwned = true))
    }

    @Test
    fun `MUTATION TARGET -- a not_owned game offers Check again, an error game still Retry`() {
        assertEquals(StatusAction(StatusActionType.CHECK_AGAIN), statusAction(notOwned(), null, selecting = false))
        assertEquals(StatusAction(StatusActionType.RETRY), statusAction(errored(), null, selecting = false))
        assertNull(statusAction(notOwned(), null, selecting = true))
    }

    @Test
    fun `a live job on a not_owned game still wins`() {
        val running = JobSummary(id = 1, appid = 7, type = "prefill", status = "running", created_at = "2026-10-15T08:00:00Z")
        assertEquals(StatusKind.RUNNING, dispKind(notOwned(), running))
        assertEquals(StatusAction(StatusActionType.PAUSE), statusAction(notOwned(), running, selecting = false))
    }

    @Test
    fun `the card model carries the not-owned flag and the action`() {
        val model = buildGameCardModel(notOwned(), null, selected = false, selecting = false)
        assertEquals(StatusKind.NOTINUSE, model.kind)
        assertTrue(model.notOwned)
        assertFalse(model.toolApp)
        assertEquals(StatusAction(StatusActionType.CHECK_AGAIN), model.action)
    }

    @Test
    fun `bulk never targets a picked not_owned game`() {
        val picked = listOf(notOwned(), errored())
        val classification = classifyBulkSelection(picked, emptyList())
        assertEquals(listOf(7), classification.notOwned.map { it.appid })
        assertEquals(listOf(440), classification.needsDownload.map { it.appid })
        val plan = buildBulkDownloadPlan(classification, picked.size)
        assertEquals(listOf(440), plan.primaryTargets)
        assertEquals("Download 1 of 2", plan.primaryLabel)
        assertEquals("1 game not available to prefill (not owned).", plan.note)
    }

    @Test
    fun `bulk with only a not_owned game picked has nothing to download`() {
        val plan = buildBulkDownloadPlan(classifyBulkSelection(listOf(notOwned()), emptyList()), 1)
        assertFalse(plan.primaryEnabled)
        assertEquals("Nothing to download here", plan.primaryLabel)
        assertTrue(plan.primaryTargets.isEmpty())
    }

    @Test
    fun `a finished not_owned job is neutral history`() {
        val job = JobSummary(id = 3, appid = 7, type = "prefill", status = "not_owned", created_at = "2026-10-15T08:00:00Z")
        assertEquals("notinuse", jobIconKind(job))
        assertEquals("Not available to prefill (not owned)", jobStatusWord(job))
    }
}
