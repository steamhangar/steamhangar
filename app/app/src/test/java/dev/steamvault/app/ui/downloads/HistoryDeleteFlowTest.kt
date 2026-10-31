package dev.steamvault.app.ui.downloads

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.HistoryDeleteOut
import dev.steamvault.app.repo.HistoryRepository
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** WP ANDROID-GAPS-1: "Delete history" (web D15-NAMES dialog). */
class HistoryDeleteFlowTest {

    private class FakeHistory : HistoryRepository {
        val scopes = mutableListOf<String>()
        var error: VaultApiError? = null
        override suspend fun delete(scope: String): HistoryDeleteOut {
            scopes += scope
            error?.let { throw it }
            return HistoryDeleteOut(scope = scope, jobs_deleted = 3)
        }
    }

    @Test
    fun `MUTATION PIN -- every open starts on the safe variant, and nothing is sent before the confirm`() = runTest {
        val repo = FakeHistory()
        val flow = HistoryDeleteFlow(repo)
        flow.open()
        assertEquals("older-than-30d", flow.scope)
        flow.scope = "all"
        flow.cancel()
        assertFalse(flow.isOpen)
        assertNull(flow.confirm()) // closed: nothing sent
        assertTrue(repo.scopes.isEmpty())
        flow.open()
        assertEquals("older-than-30d", flow.scope)
    }

    @Test
    fun `confirm sends the chosen scope and closes on success`() = runTest {
        val repo = FakeHistory()
        val flow = HistoryDeleteFlow(repo)
        flow.open()
        flow.scope = "all"
        val result = flow.confirm()
        assertEquals(listOf("all"), repo.scopes)
        assertEquals(3, result!!.jobs_deleted)
        assertFalse(flow.isOpen)
    }

    @Test
    fun `MUTATION PIN -- a failure keeps the dialog open and says why`() = runTest {
        val repo = FakeHistory()
        repo.error = VaultApiError.NotFound("x", 404, "Not Found")
        val flow = HistoryDeleteFlow(repo)
        flow.open()
        assertNull(flow.confirm())
        assertTrue(flow.isOpen)
        assertEquals("This server cannot delete history yet. Update vault-api to use this.", flow.error)
        assertFalse(flow.busy)
    }

    @Test
    fun `an unknown scope value falls back to the safe one`() = runTest {
        val repo = FakeHistory()
        val flow = HistoryDeleteFlow(repo)
        flow.open()
        flow.scope = "bogus"
        flow.confirm()
        assertEquals(listOf("older-than-30d"), repo.scopes)
    }
}
