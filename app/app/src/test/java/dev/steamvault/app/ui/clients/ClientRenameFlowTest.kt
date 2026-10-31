package dev.steamvault.app.ui.clients

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.ClientActivityOut
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** WP ANDROID-GAPS-1: the Clients page's Rename flow (web D15-NAMES dialog). */
@OptIn(ExperimentalCoroutinesApi::class)
class ClientRenameFlowTest {

    private val agent = ClientActivityOut(
        key = "agent:gaming-pc", kind = "agent", client_id = "gaming-pc",
        name = null, name_addr = "10.0.0.42", display_name = "gaming-pc",
    )
    private val named = agent.copy(name = "Desk", display_name = "Desk")
    private val runner = ClientActivityOut(key = "runner", kind = "runner", name_addr = null, display_name = "Prefill runner")

    @Test
    fun `open starts with the operator name, empty without one, and never for a client without name_addr`() {
        val flow = ClientRenameFlow(FakeClientsRepository())
        flow.open(named)
        assertEquals("Desk", flow.input)
        flow.cancel()
        flow.open(agent)
        assertEquals("", flow.input)
        flow.cancel()
        flow.open(runner)
        assertNull(flow.target)
    }

    @Test
    fun `MUTATION PIN -- an invalid name is refused locally, nothing is sent`() = runTest {
        val repo = FakeClientsRepository()
        val flow = ClientRenameFlow(repo)
        flow.open(agent)
        flow.input = "   "
        var changed = 0
        flow.save { changed++ }
        assertEquals("Enter a name.", flow.error)
        assertTrue(repo.named.isEmpty())
        assertEquals(agent, flow.target)
        assertEquals(0, changed)
    }

    @Test
    fun `save stores the trimmed name under name_addr, closes and re-fetches`() = runTest {
        val repo = FakeClientsRepository()
        val flow = ClientRenameFlow(repo)
        flow.open(agent)
        flow.input = "  Desk "
        var changed = 0
        flow.save { changed++ }
        assertEquals(listOf("10.0.0.42" to "Desk"), repo.named)
        assertNull(flow.target)
        assertEquals("Renamed to ⁨Desk⁩.", flow.notice)
        assertEquals(1, changed)
    }

    @Test
    fun `MUTATION PIN -- a failed save keeps the dialog open with the reason`() = runTest {
        val repo = FakeClientsRepository()
        repo.setError = VaultApiError.NotFound("x", 404, "Not Found")
        val flow = ClientRenameFlow(repo)
        flow.open(agent)
        flow.input = "Desk"
        flow.save { }
        assertEquals(agent, flow.target)
        assertEquals("This server cannot store client names yet. Update vault-api to rename clients.", flow.error)
        assertNull(flow.notice)
    }

    @Test
    fun `Remove name clears, then names what the card shows now`() = runTest {
        val repo = FakeClientsRepository()
        val flow = ClientRenameFlow(repo)
        flow.open(named)
        var askedKey: String? = null
        flow.clear(displayNameAfter = { key -> askedKey = key; "gaming-pc" }, onChanged = {})
        assertEquals(listOf("10.0.0.42"), repo.cleared)
        assertEquals("agent:gaming-pc", askedKey)
        assertEquals("Name removed. Now shown as ⁨gaming-pc⁩.", flow.notice)
        assertNull(flow.target)
    }

    @Test
    fun `MUTATION PIN -- the handler's own 404 on Remove name counts as cleared, a foreign failure does not`() = runTest {
        val repo = FakeClientsRepository()
        repo.clearError = VaultApiError.NotFound("x", 404, "No name stored for '10.0.0.42'")
        val flow = ClientRenameFlow(repo)
        flow.open(named)
        flow.clear(displayNameAfter = { throw VaultApiError.Network("offline") }, onChanged = {})
        assertEquals("Name removed.", flow.notice)

        repo.clearError = VaultApiError.Server("x", 500, "database is locked")
        flow.open(named)
        flow.clear(displayNameAfter = { "unused" }, onChanged = {})
        assertEquals("Could not save the name: database is locked", flow.error)
        assertEquals(named, flow.target)
    }

    @Test
    fun `Cancel waits while a request runs`() = runTest(UnconfinedTestDispatcher()) {
        val repo = FakeClientsRepository()
        repo.gate = CompletableDeferred()
        val flow = ClientRenameFlow(repo)
        flow.open(agent)
        flow.input = "Desk"
        val job = launch { flow.save { } }
        assertEquals(RenameBusy.SAVE, flow.busy)
        flow.cancel()
        assertEquals(agent, flow.target)
        repo.gate!!.complete(Unit)
        job.join()
        assertNull(flow.target)
        assertNull(flow.busy)
    }
}
