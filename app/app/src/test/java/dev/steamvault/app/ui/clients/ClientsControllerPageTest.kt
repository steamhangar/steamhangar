package dev.steamvault.app.ui.clients

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.polling.PollingIntervals
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * WP ANDROID-REDESIGN-1a: the PCs sheet's controller also serves the
 * Clients page -- its agent rows (polled while the page is STARTED, first
 * fetch at once) and the card's Remove (the time of a success, so the page
 * hides the PC until the overview caught up).
 */
@OptIn(ExperimentalCoroutinesApi::class)
class ClientsControllerPageTest {

    private object Strings : ClientsStrings {
        override fun loadErrorFallback() = "load-error"
    }

    @Test
    fun `MUTATION PIN -- the page poll fetches at once, then every CLIENTS_MS, also with the sheet closed`() = runTest {
        val repo = FakeClientsRepository(listOf(pc("a")))
        val c = ClientsController(repo, Strings)
        assertTrue("the sheet is closed", !c.isOpen)
        val job = launch { c.pollForPage() }
        runCurrent()
        assertEquals(1, repo.listCalls)
        assertEquals(listOf("a"), c.clients.map { it.client_id })
        advanceTimeBy(PollingIntervals.CLIENTS_MS)
        runCurrent()
        assertEquals(2, repo.listCalls)
        job.cancel()
    }

    @Test
    fun `MUTATION PIN -- a successful remove records its time, a failed one does not`() = runTest {
        val repo = FakeClientsRepository(listOf(pc("a"), pc("b")))
        val c = ClientsController(repo, Strings, clock = { 1_000L })
        c.refreshOnce()
        c.requestRemove("a")
        c.confirmRemove()
        assertEquals(mapOf("a" to 1_000L), c.removedAt)
        repo.removeError = VaultApiError.Server("x", 500, "boom")
        c.requestRemove("b")
        c.confirmRemove()
        assertNull(c.removedAt["b"])
    }

    @Test
    fun `the page's snackbar clears the remove note`() = runTest {
        val repo = FakeClientsRepository(listOf(pc("a")))
        val c = ClientsController(repo, Strings)
        c.refreshOnce()
        c.requestRemove("a")
        c.confirmRemove()
        assertEquals("a removed from the list.", c.removeNotice)
        c.clearRemoveNotice()
        assertNull(c.removeNotice)
    }

    @Test
    fun `expired remove times are dropped at the next remove (review N5)`() = runTest {
        val repo = FakeClientsRepository(listOf(pc("a"), pc("b")))
        var t = 1_000L
        val c = ClientsController(repo, Strings, clock = { t })
        c.refreshOnce()
        c.requestRemove("a")
        c.confirmRemove()
        t += 30_001L
        c.requestRemove("b")
        c.confirmRemove()
        assertEquals(mapOf("b" to 31_001L), c.removedAt)
    }
}
