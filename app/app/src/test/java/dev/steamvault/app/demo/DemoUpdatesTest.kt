package dev.steamvault.app.demo

import dev.steamvault.app.ui.library.logic.UpdateCheckResult
import dev.steamvault.app.ui.library.logic.buildGameCardModel
import dev.steamvault.app.ui.library.logic.runUpdateCheck
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * WP UPD-3 review: demo mode shows the update feature -- one cached,
 * non-tool demo game carries the badge, and "Check updates" reports it.
 */
class DemoUpdatesTest {

    @Test
    fun `MUTATION PIN -- one cached non-tool demo game carries the Update available badge`() {
        val flagged = DemoState.fresh().listGameSummaries().filter { it.update_available }
        assertEquals(1, flagged.size)
        val game = flagged.single()
        assertTrue("flagged demo game must be cached", (game.size_bytes ?: 0L) > 0L)
        assertFalse(game.tool_app)
        assertTrue(buildGameCardModel(game, null, selected = false, selecting = false).updateAvailable)
    }

    @Test
    fun `MUTATION PIN -- the demo check finishes and reports at least one game with an update`() = runTest {
        val result = runUpdateCheck(DemoUpdatesRepository(DemoState.fresh()))
        assertTrue(result is UpdateCheckResult.Finished)
        val status = (result as UpdateCheckResult.Finished).status
        assertEquals("completed", status.last_outcome)
        assertTrue((status.last_stale ?: 0) >= 1)
    }
}
