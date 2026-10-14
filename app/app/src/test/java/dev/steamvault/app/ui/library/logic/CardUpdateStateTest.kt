package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.ui.status.StatusKind
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * WP UPD-6 (twin of web game-card-update.test.js / game-status.js
 * `showsUpdateState`, `cardIconKind`, `showsUpdateBadge`): a CACHED card
 * whose row carries `update_available` draws the orange STALE refresh
 * glyph and says "Update available" instead of "Current"; the separate
 * badge stays only where the status word does not already say it.
 */
class CardUpdateStateTest {

    private fun row(appid: Int = 7, status: String = "done", size: Long? = 4_000_000_000L, flagged: Boolean = true) =
        GameSummary(
            appid = appid,
            name = "Glasswing",
            status = status,
            last_prefill_at = if (status == "done") "2026-10-14T08:00:00Z" else null,
            depot_count = 1,
            size_bytes = size,
            update_available = flagged,
        )

    private fun liveJob(appid: Int = 7) = JobSummary(
        id = 1,
        appid = appid,
        type = "prefill",
        status = "running",
        created_at = "2026-10-14T08:00:00Z",
    )

    private fun model(game: GameSummary, job: JobSummary? = null) =
        buildGameCardModel(game, job, selected = false, selecting = false)

    @Test
    fun `MUTATION PIN -- a flagged cached card shows the update state with the STALE icon`() {
        val m = model(row())
        assertEquals("the kind stays cached (filters, pill size)", StatusKind.CACHED, m.kind)
        assertTrue(m.updateState)
        assertEquals(StatusKind.STALE, cardIconKind(m))
        assertFalse("the status word says it, so no second badge", showsUpdateBadge(m))
        assertTrue("the pill keeps its size", capsulePillHasGround(m))
    }

    @Test
    fun `an unflagged cached card keeps the green check and no badge`() {
        val m = model(row(flagged = false))
        assertFalse(m.updateState)
        assertEquals(StatusKind.CACHED, cardIconKind(m))
        assertFalse(showsUpdateBadge(m))
    }

    @Test
    fun `MUTATION PIN -- a flagged card that is not CACHED keeps its own icon and the separate badge`() {
        val running = model(row(), liveJob())
        assertFalse(running.updateState)
        assertEquals(running.kind, cardIconKind(running))
        assertTrue(showsUpdateBadge(running))

        val failed = model(row(status = "error", size = null))
        assertEquals(StatusKind.ERROR, failed.kind)
        assertFalse(failed.updateState)
        assertTrue(showsUpdateBadge(failed))
    }

    @Test
    fun `showsUpdateState is strictly CACHED and flagged`() {
        assertTrue(showsUpdateState(row(), StatusKind.CACHED))
        assertFalse(showsUpdateState(row(), StatusKind.ERROR))
        assertFalse(showsUpdateState(row(flagged = false), StatusKind.CACHED))
    }

    @Test
    fun `a group card follows the OR over its members`() {
        val parent = row(appid = 100, flagged = false).copy(app_type = "game")
        val pkg = row(appid = 101, flagged = true).copy(parent_appid = 100, app_type = "dlc", name = "Glasswing - Pack")
        val grouped = groupGames(listOf(parent, pkg))
        val card = grouped.rows.single()
        val m = buildGameCardModel(card, null, selected = false, selecting = false, packages = grouped.packagesByParent.getValue(100))
        assertTrue(m.updateState)
        assertEquals(StatusKind.STALE, cardIconKind(m))
    }
}
