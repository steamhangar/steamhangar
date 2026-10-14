package dev.steamvault.app.demo

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.ui.library.logic.failedPackages
import dev.steamvault.app.ui.library.logic.groupGames
import dev.steamvault.app.ui.library.logic.isMembersMismatch
import dev.steamvault.app.ui.library.logic.planMemberIds
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/**
 * WP TOOL-APPS-2d: the demo's content-package group is a real case of every
 * rule (ADR-0022), and the demo's grouped delete answers like the server
 * (api/README.md "Grouped delete"). The drift pin for this fixture: if the
 * group stops grouping, loses its failed package or its shared depot, the
 * demo silently stops showing the feature -- these tests fail instead.
 */
class DemoContentGroupTest {

    private val parent = DEMO_GROUP_PARENT_APPID

    @Test
    fun `MUTATION PIN -- the demo library shows one group with three packages, one failed`() {
        val games = DemoState.fresh().listGameSummaries()
        val grouped = groupGames(games)
        val packages = grouped.packagesOf(parent)
        assertEquals(3, packages.size)
        assertEquals(1, failedPackages(packages, emptyMap()).size)
        assertTrue("the group card replaces its packages in the grid", packages.none { p -> grouped.rows.any { it.appid == p.appid } })
    }

    @Test
    fun `group size counts the depot shared by game and package once`() {
        val games = DemoState.fresh().listGameSummaries().associateBy { it.appid }
        // 5.2 GB + 150 MB shared + 300 MB + 450 MB, the shared depot once.
        assertEquals(5_200_000_000L + 150_000_000L + 300_000_000L + 450_000_000L, games.getValue(parent).group_size_bytes)
        assertNull(games.getValue(4_010_082).group_size_bytes)
    }

    @Test
    fun `the plan lists the game and its package-typed children, members never protect each other`() {
        val plan = DemoState.fresh().deletePlan(parent)
        assertEquals(listOf(parent, 4_010_082, 4_010_084, 4_010_086), planMemberIds(plan))
        assertTrue(plan.delete_depots.any { it.depotid == 4_010_089 })
        assertTrue(plan.skipped_shared.isEmpty())
        assertTrue(plan.blocked_by.isEmpty())
    }

    @Test
    fun `MUTATION PIN -- a differing member list is a 409 the app reads as a members mismatch, nothing deleted`() {
        val state = DemoState.fresh()
        try {
            state.deleteCacheWithPackages(parent, listOf(parent, 4_010_082))
            fail("expected a 409")
        } catch (e: VaultApiError) {
            assertEquals(409, e.status)
            assertTrue(isMembersMismatch(e))
        }
        assertEquals(2, state.gameDetail(parent).depots.size)
    }

    @Test
    fun `the confirmed grouped delete removes every member's depots`() {
        val state = DemoState.fresh()
        val plan = state.deletePlan(parent)
        val out = state.deleteCacheWithPackages(parent, planMemberIds(plan))
        assertTrue(out.with_packages)
        assertEquals(plan.total_bytes, out.total_bytes_freed)
        for (id in listOf(parent, 4_010_082, 4_010_084)) {
            assertTrue("appid $id", state.gameDetail(id).depots.isEmpty())
        }
    }
}
