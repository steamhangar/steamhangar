package dev.steamvault.app.ui.detail.logic

import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.ui.library.logic.MultiPlanDepotRow
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Pins the four depot-sharing wordings [DepotShareTag] can produce
 * (WP 4b.6 brief: "mutation-worthy pins (sharing wording per case)"), and
 * the co-owner row cached/not-cached split feeding the mockup's verbatim
 * "not cached · mapping kept" state (mockup-notes.md round 3).
 */
class DepotPresentationTest {

    private val other1 = GameSummary(201, "Other One", "done", "2026-08-01T00:00:00Z", null, 1, 1_000_000_000, false)
    private val other2 = GameSummary(202, "Other Two", "idle", null, null, 0, null, false)
    private val gamesByAppid = mapOf(other1.appid to other1, other2.appid to other2)

    @Test
    fun `not shared -- EXCLUSIVE, no tag, no co-owners`() {
        val row = MultiPlanDepotRow(depotid = 1, sizeBytes = 500L, shared = false, others = emptyList(), holderAppids = emptyList(), free = true)
        val presentation = buildDepotPresentation(row, gamesByAppid, thisAppIsHolder = true)
        assertEquals(DepotShareTag.EXCLUSIVE, presentation.tag)
        assertTrue(presentation.coOwners.isEmpty())
    }

    @Test
    fun `shared with a cached co-owner -- PROTECTED regardless of whether this app is a holder`() {
        val row = MultiPlanDepotRow(
            depotid = 2, sizeBytes = 500L, shared = true,
            others = listOf(other1.appid), holderAppids = listOf(other1.appid), free = false,
        )
        val presentation = buildDepotPresentation(row, gamesByAppid, thisAppIsHolder = true)
        assertEquals(DepotShareTag.PROTECTED, presentation.tag)
        assertEquals(listOf(CoOwnerRow(other1.appid, "Other One", cached = true)), presentation.coOwners)
    }

    @Test
    fun `shared, no other holder, THIS app holds it -- SOLE_HOLDER (mockup round 5)`() {
        val row = MultiPlanDepotRow(
            depotid = 3, sizeBytes = 500L, shared = true,
            others = listOf(other2.appid), holderAppids = emptyList(), free = true,
        )
        val presentation = buildDepotPresentation(row, gamesByAppid, thisAppIsHolder = true)
        assertEquals(DepotShareTag.SOLE_HOLDER, presentation.tag)
        assertEquals(listOf(CoOwnerRow(other2.appid, "Other Two", cached = false)), presentation.coOwners)
    }

    @Test
    fun `shared, no other holder, THIS app also does not hold it -- ORPHANED (mapping kept, nobody cached)`() {
        // Mirrors mockup-notes.md's Meridian Rally sample: a previously
        // deleted game whose shared depot's remaining co-owners are ALSO
        // all uncached.
        val row = MultiPlanDepotRow(
            depotid = 4, sizeBytes = 500L, shared = true,
            others = listOf(other2.appid), holderAppids = emptyList(), free = true,
        )
        val presentation = buildDepotPresentation(row, gamesByAppid, thisAppIsHolder = false)
        assertEquals(DepotShareTag.ORPHANED, presentation.tag)
    }

    @Test
    fun `an unresolvable co-owner falls back to the App id name, mirrors GameCardModel and JobCardModel`() {
        val row = MultiPlanDepotRow(
            depotid = 5, sizeBytes = null, shared = true,
            others = listOf(999), holderAppids = emptyList(), free = true,
        )
        val presentation = buildDepotPresentation(row, emptyMap(), thisAppIsHolder = true)
        assertEquals("Unknown app", presentation.coOwners.single().name)
    }

    @Test
    fun `MUTATION TARGET -- a holder appid is reported cached, a non-holder co-owner is not`() {
        val row = MultiPlanDepotRow(
            depotid = 6, sizeBytes = 500L, shared = true,
            others = listOf(other1.appid, other2.appid), holderAppids = listOf(other1.appid), free = false,
        )
        val presentation = buildDepotPresentation(row, gamesByAppid, thisAppIsHolder = false)
        val byAppid = presentation.coOwners.associateBy { it.appid }
        assertTrue(byAppid.getValue(other1.appid).cached)
        assertTrue(!byAppid.getValue(other2.appid).cached)
    }
}
