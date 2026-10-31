package dev.steamvault.app.demo

import dev.steamvault.app.net.error.VaultApiError
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.time.Instant

/**
 * WP ANDROID-GAPS-1: demo mode answers Remove PC, Rename and Delete history
 * like the server (web `demo-data.js` handlers): the change shows in the
 * next read, and the "nothing to act on" cases are the server's own 404s.
 */
class DemoClientGapsTest {

    private fun overview(state: DemoState) =
        demoClientsOverview("24h", state.trafficSnapshot(), Instant.now().toEpochMilli())

    @Test
    fun `removing a demo PC drops it from the PCs list and the Clients page, twice is the server's 404`() {
        val state = DemoState.fresh()
        state.removeClient("demo-steamdeck")
        assertFalse(state.clientsOut().any { it.client_id == "demo-steamdeck" })
        assertFalse(overview(state).clients.any { it.client_id == "demo-steamdeck" })
        try {
            state.removeClient("demo-steamdeck")
            fail("expected a 404")
        } catch (e: VaultApiError.NotFound) {
            assertTrue(e.detail!!.startsWith("Unknown client_id"))
        }
    }

    @Test
    fun `a demo rename shows as display_name, clearing falls back, clearing again is the server's 404`() {
        val state = DemoState.fresh()
        state.setClientName("10.0.0.61", "  Living room TV ")
        val tv = overview(state).clients.single { it.key == "addr:10.0.0.61" }
        assertEquals("Living room TV", tv.name)
        assertEquals("Living room TV", tv.display_name)
        assertEquals("10.0.0.61", tv.name_addr)

        // The seeded name of the offline agent's address.
        state.clearClientName("10.0.0.57")
        val deck = overview(state).clients.single { it.client_id == "demo-steamdeck" }
        assertNull(deck.name)
        assertEquals("demo-steamdeck", deck.display_name)
        try {
            state.clearClientName("10.0.0.57")
            fail("expected a 404")
        } catch (e: VaultApiError.NotFound) {
            assertTrue(e.detail!!.startsWith("No name stored"))
        }
        try {
            state.setClientName("10.0.0.61", "   ")
            fail("expected a 422")
        } catch (e: VaultApiError.Validation) {
            assertEquals(422, e.status)
        }
    }

    @Test
    fun `MUTATION PIN -- demo Delete history removes finished jobs only, older-than-30d keeps recent ones`() {
        val state = DemoState.fresh()
        val older = state.deleteHistory("older-than-30d")
        assertEquals(0, older.jobs_deleted) // every seeded job finished within hours
        assertTrue(older.cutoff != null)
        val before = state.jobHistory(200)
        val finished = before.count { it.status in setOf("done", "error", "cancelled") }
        assertTrue(finished > 0)
        val all = state.deleteHistory("all")
        assertEquals(finished, all.jobs_deleted)
        assertNull(all.cutoff)
        val after = state.jobHistory(200)
        assertTrue(after.none { it.status in setOf("done", "error", "cancelled") })
        assertEquals(before.size - finished, after.size)
    }
}
