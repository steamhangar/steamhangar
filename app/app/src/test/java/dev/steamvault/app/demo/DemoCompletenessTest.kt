package dev.steamvault.app.demo

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.ui.downloads.logic.pendingIncomplete
import dev.steamvault.app.ui.library.logic.CompletenessOverlay
import dev.steamvault.app.ui.library.logic.buildGameCardModel
import dev.steamvault.app.ui.library.logic.groupGames
import dev.steamvault.app.ui.library.logic.isCompletable
import dev.steamvault.app.ui.library.logic.isDamagedOnly
import dev.steamvault.app.ui.status.StatusKind
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/**
 * WP CACHE-COMPLETE-1c: demo mode shows cache completeness the way the web
 * demo does -- one partially cached game "Complete now" helps (Halcyon
 * Ledger), one short only of damaged chunks (the Emberline group), one
 * whose figure is unknown (Pale Meridian, its newer build not fetched yet)
 * -- and "Verify" / "Complete now" answer like `POST /v1/prefill/verify`.
 * The drift pin for these fixtures: if a seed loses its figure, the demo
 * silently stops showing the feature and these tests fail instead.
 */
class DemoCompletenessTest {

    private val halcyon = 4_010_040
    private val pale = 4_010_050
    private val wrenfield = 4_010_030
    private val duskfall = 4_010_010

    private fun games() = DemoState.fresh().listGameSummaries().associateBy { it.appid }

    @Test
    fun `MUTATION PIN -- one partial completable, one damaged-only, one unknown, the rest complete`() {
        val g = games()
        val partial = g.getValue(halcyon)
        assertEquals(86, partial.cache_complete_pct)
        assertTrue(isCompletable(partial))
        assertEquals(1_100_000_000L, partial.cache_expected_bytes)
        assertEquals(950_000_000L, partial.cache_present_bytes)
        assertTrue(partial.cache_complete_checked_at != null)

        val damaged = g.getValue(DEMO_GROUP_PARENT_APPID)
        assertEquals(99, damaged.cache_complete_pct)
        assertTrue(isDamagedOnly(damaged))
        assertEquals(99, damaged.group_cache_complete_pct)

        val unknown = g.getValue(pale)
        assertNull(unknown.cache_complete_pct)
        assertNull(unknown.cache_missing_chunks)
        assertTrue("the unknown game still has bytes on disk", (unknown.size_bytes ?: 0L) > 0L)

        assertEquals(100, g.getValue(duskfall).cache_complete_pct)
        assertEquals(0, g.getValue(duskfall).cache_missing_chunks)
        assertNull("nothing on disk -> no figure", g.getValue(4_010_020).cache_complete_pct)
        assertNull("a grouped row has no group figure", g.getValue(4_010_082).group_cache_complete_pct)
    }

    @Test
    fun `the cards show the overlays -- partial, group partial, update wins over unknown`() {
        val all = DemoState.fresh().listGameSummaries()
        val grouped = groupGames(all)
        fun card(appid: Int) = buildGameCardModel(
            grouped.rows.first { it.appid == appid },
            null,
            selected = false,
            selecting = false,
            packages = grouped.packagesOf(appid),
        )
        assertEquals(CompletenessOverlay(StatusKind.PARTIAL, 86), card(halcyon).completeness)
        assertEquals(CompletenessOverlay(StatusKind.PARTIAL, 99), card(DEMO_GROUP_PARENT_APPID).completeness)
        assertTrue(card(pale).updateState)
        assertNull(card(pale).completeness)
        assertNull(card(duskfall).completeness)
    }

    @Test
    fun `Downloads Incomplete lists the partial and the damaged game, not the running or unknown one`() {
        val state = DemoState.fresh()
        val rows = pendingIncomplete(state.listGameSummaries(), state.listJobSummaries(50))
        assertEquals(setOf(halcyon, DEMO_GROUP_PARENT_APPID), rows.map { it.appid }.toSet())
        assertFalse(rows.any { it.appid == wrenfield })
    }

    @Test
    fun `verify flags needs_force, enqueues once, and the finished job re-measures`() {
        val state = DemoState.fresh()
        val refs = state.enqueueVerify(listOf(halcyon, DEMO_GROUP_PARENT_APPID))
        assertEquals(listOf(halcyon, DEMO_GROUP_PARENT_APPID), refs.map { it.appid })
        assertTrue(refs.none { it.deduplicated })
        assertTrue(state.listGameSummaries().first { it.appid == halcyon }.needs_force)
        // A second press dedupes onto the running job.
        assertTrue(state.enqueueVerify(listOf(halcyon)).single().deduplicated)
        repeat(5) { state.listJobSummaries(50) } // tick the demo jobs to the end
        val after = state.listGameSummaries().associateBy { it.appid }
        assertEquals("missing chunks are fetched", 100, after.getValue(halcyon).cache_complete_pct)
        assertFalse(after.getValue(halcyon).needs_force)
        assertEquals("wrong-size chunks stay (a forced run serves them as HITs)", 99, after.getValue(DEMO_GROUP_PARENT_APPID).cache_complete_pct)
    }

    @Test
    fun `verify with no appids is a validation error, like the API`() {
        try {
            DemoState.fresh().enqueueVerify(emptyList())
            fail("expected a validation error")
        } catch (_: VaultApiError.Validation) {
            // expected
        }
    }

    @Test
    fun `the demo repository routes Verify to the verify enqueue`() {
        val state = DemoState.fresh()
        val refs = kotlinx.coroutines.runBlocking { DemoJobsRepository(state).prefillVerify(listOf(halcyon)) }
        assertEquals(halcyon, refs.single().appid)
        assertTrue(state.listGameSummaries().first { it.appid == halcyon }.needs_force)
    }
}
