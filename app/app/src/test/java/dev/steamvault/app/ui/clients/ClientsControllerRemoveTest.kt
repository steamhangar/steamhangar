package dev.steamvault.app.ui.clients

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.ClientOut
import dev.steamvault.app.repo.ClientsRepository
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Shared fake for the clients tests of WP ANDROID-GAPS-1. */
internal class FakeClientsRepository(var rows: List<ClientOut> = emptyList()) : ClientsRepository {
    var removeError: VaultApiError? = null
    var setError: VaultApiError? = null
    var clearError: VaultApiError? = null
    var gate: CompletableDeferred<Unit>? = null
    val removed = mutableListOf<String>()
    val named = mutableListOf<Pair<String, String>>()
    val cleared = mutableListOf<String>()
    var listCalls = 0

    override suspend fun list(): List<ClientOut> {
        listCalls++
        return rows
    }

    override suspend fun remove(clientId: String) {
        gate?.await()
        removed += clientId
        removeError?.let { throw it }
        rows = rows.filter { it.client_id != clientId }
    }

    override suspend fun setName(addr: String, name: String) {
        gate?.await()
        named += addr to name
        setError?.let { throw it }
    }

    override suspend fun clearName(addr: String) {
        gate?.await()
        cleared += addr
        clearError?.let { throw it }
    }
}

private object FakeClientsStrings : ClientsStrings {
    override fun loadErrorFallback() = "load-error"
}

internal fun pc(id: String) = ClientOut(client_id = id, first_seen = "2026-10-01T00:00:00Z", last_reported_at = "2026-10-31T00:00:00Z")

/**
 * WP ANDROID-GAPS-1: Remove a PC from the PCs sheet, same safety as web
 * WEB-FEAT-4 -- a confirm before the request, the handler's own 404 counts
 * as removed, any other failure keeps the row and says why on it.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class ClientsControllerRemoveTest {

    private suspend fun controller(repo: FakeClientsRepository): ClientsController =
        ClientsController(repo, FakeClientsStrings).also { it.refreshOnce() }

    @Test
    fun `MUTATION PIN -- nothing is sent before the confirm, and Keep sends nothing`() = runTest {
        val repo = FakeClientsRepository(listOf(pc("a"), pc("b")))
        val c = controller(repo)
        c.requestRemove("a")
        assertEquals("a", c.removeTarget)
        assertTrue(repo.removed.isEmpty())
        c.cancelRemove()
        assertNull(c.removeTarget)
        c.confirmRemove() // no open confirm: no request
        assertTrue(repo.removed.isEmpty())
    }

    @Test
    fun `a confirmed remove drops the row, says so and re-fetches`() = runTest {
        val repo = FakeClientsRepository(listOf(pc("a"), pc("b")))
        val c = controller(repo)
        val before = repo.listCalls
        c.requestRemove("a")
        c.confirmRemove()
        assertEquals(listOf("a"), repo.removed)
        assertEquals(listOf("b"), c.clients.map { it.client_id })
        assertEquals("a removed from the list.", c.removeNotice)
        assertNull(c.removeTarget)
        assertEquals(before + 1, repo.listCalls)
    }

    @Test
    fun `MUTATION PIN -- the handler's own 404 is the same end state as a 204`() = runTest {
        val repo = FakeClientsRepository(listOf(pc("a")))
        repo.removeError = VaultApiError.NotFound("x", 404, "Unknown client_id 'a'")
        val c = controller(repo)
        c.requestRemove("a")
        c.confirmRemove()
        assertEquals("a removed from the list.", c.removeNotice)
        assertTrue(c.removeErrors.isEmpty())
    }

    @Test
    fun `MUTATION PIN -- any other failure keeps the row and shows the server's reason on it`() = runTest {
        val repo = FakeClientsRepository(listOf(pc("a")))
        repo.removeError = VaultApiError.NotFound("DELETE /v1/clients/{client_id} failed (404)", 404, "Not Found")
        val c = controller(repo)
        c.requestRemove("a")
        c.confirmRemove()
        assertEquals(listOf("a"), c.clients.map { it.client_id })
        assertEquals("Could not remove a: Not Found", c.removeErrors["a"])
        assertNull(c.removeNotice)
        // A new attempt clears the old error line.
        c.requestRemove("a")
        assertNull(c.removeErrors["a"])
    }

    @Test
    fun `an id with a slash never opens the confirm`() = runTest {
        val c = controller(FakeClientsRepository(listOf(pc("lab/pc"))))
        c.requestRemove("lab/pc")
        assertNull(c.removeTarget)
    }

    @Test
    fun `Keep and close wait while the request runs`() = runTest(UnconfinedTestDispatcher()) {
        val repo = FakeClientsRepository(listOf(pc("a")))
        repo.gate = CompletableDeferred()
        val c = controller(repo)
        c.requestRemove("a")
        val job = launch { c.confirmRemove() }
        assertTrue(c.removeBusy)
        c.cancelRemove()
        c.close()
        assertEquals("a", c.removeTarget)
        repo.gate!!.complete(Unit)
        job.join()
        assertNull(c.removeTarget)
        assertEquals("a removed from the list.", c.removeNotice)
    }
}
