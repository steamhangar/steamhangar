package dev.steamvault.app.ui.pairing

import dev.steamvault.app.net.pairing.PairingRejection
import dev.steamvault.app.net.pairing.PairingRequest
import dev.steamvault.app.ui.pairing.logic.PairingReplaceNotice
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

class PairingControllerTest {

    private val secret = "S3CRET-pairing-key"
    private val link = "steamhangar://pair?v=1&url=https%3A%2F%2Fhangar.example.org&key=$secret"

    private fun confirmState(controller: PairingController): PairingUiState.Confirm {
        val state = controller.state
        assertTrue("expected the confirmation dialog, got $state", state is PairingUiState.Confirm)
        return state as PairingUiState.Confirm
    }

    @Test
    fun `a valid link on a fresh install opens the confirmation without a replace notice`() {
        val controller = PairingController()
        controller.offer(link, existingBaseUrl = null, hasExistingKey = false)
        val state = confirmState(controller)
        assertEquals("https://hangar.example.org", state.request.baseUrl)
        assertEquals(PairingReplaceNotice.NONE, state.notice)
        assertFalse(state.busy)
        assertNull(state.error)
    }

    @Test
    fun `MUTATION PIN -- an already configured different vault shows the replace notice naming it`() {
        val controller = PairingController()
        controller.offer(link, existingBaseUrl = "http://192.168.1.50:8080", hasExistingKey = true)
        val state = confirmState(controller)
        assertEquals(PairingReplaceNotice.REPLACES_OTHER_VAULT, state.notice)
        assertEquals("192.168.1.50:8080", state.existingHost)
    }

    @Test
    fun `an invalid link opens the refusal with its reason`() {
        val controller = PairingController()
        controller.offer("steamhangar://pair?v=2&url=x&key=y", existingBaseUrl = null, hasExistingKey = false)
        assertEquals(PairingUiState.Rejected(PairingRejection.VERSION_UNSUPPORTED, "2"), controller.state)
        controller.dismiss()
        assertEquals(PairingUiState.Hidden, controller.state)
    }

    @Test
    fun `cancel closes the dialog without calling the verification`() = runTest {
        val controller = PairingController()
        controller.offer(link, existingBaseUrl = null, hasExistingKey = false)
        controller.dismiss()
        var calls = 0
        assertFalse(controller.confirm { calls++; null })
        assertEquals(0, calls)
        assertEquals(PairingUiState.Hidden, controller.state)
    }

    @Test
    fun `a successful confirm hands the parsed request over once and closes the dialog`() = runTest {
        val controller = PairingController()
        controller.offer(link, existingBaseUrl = null, hasExistingKey = false)
        val seen = mutableListOf<PairingRequest>()
        assertTrue(controller.confirm { seen += it; null })
        assertEquals(1, seen.size)
        assertEquals("https://hangar.example.org", seen.single().baseUrl)
        assertEquals(secret, seen.single().apiKey)
        assertEquals(PairingUiState.Hidden, controller.state)
    }

    @Test
    fun `MUTATION PIN -- a failed check shows its message in place and keeps the dialog usable`() = runTest {
        val controller = PairingController()
        controller.offer(link, existingBaseUrl = null, hasExistingKey = false)
        assertFalse(controller.confirm { "That vault API key was rejected." })
        val state = confirmState(controller)
        assertEquals("That vault API key was rejected.", state.error)
        assertFalse(state.busy)
        assertEquals("https://hangar.example.org", state.request.baseUrl)

        // Retry clears the old message and can succeed.
        assertTrue(controller.confirm { null })
        assertEquals(PairingUiState.Hidden, controller.state)
    }

    @Test
    fun `MUTATION PIN -- while a check runs, a second tap, a new link and cancel are all ignored`() = runTest {
        val controller = PairingController()
        controller.offer(link, existingBaseUrl = null, hasExistingKey = false)
        val gate = CompletableDeferred<String?>()
        var calls = 0
        val job = launch { controller.confirm { calls++; gate.await() } }
        testScheduler.advanceUntilIdle()
        val busy = confirmState(controller)
        assertTrue(busy.busy)

        assertFalse(controller.confirm { calls++; null })
        controller.offer(
            "steamhangar://pair?v=1&url=https%3A%2F%2Fevil.example&key=other",
            existingBaseUrl = null,
            hasExistingKey = false,
        )
        controller.dismiss()
        assertSame(busy, controller.state)

        gate.complete(null)
        job.join()
        assertEquals(1, calls)
        assertEquals(PairingUiState.Hidden, controller.state)
    }

    @Test
    fun `MUTATION PIN -- a check cancelled with its Activity leaves a usable dialog, not a stuck spinner`() = runTest {
        val controller = PairingController()
        controller.offer(link, existingBaseUrl = null, hasExistingKey = false)
        val gate = CompletableDeferred<String?>()
        val job = launch { controller.confirm { gate.await() } }
        testScheduler.advanceUntilIdle()
        assertTrue(confirmState(controller).busy)
        job.cancel()
        job.join()
        val state = confirmState(controller)
        assertFalse(state.busy)
        assertNull(state.error)
    }

    @Test
    fun `a new link replaces an open, idle confirmation`() {
        val controller = PairingController()
        controller.offer(link, existingBaseUrl = null, hasExistingKey = false)
        controller.offer(
            "steamhangar://pair?v=1&url=http%3A%2F%2F10.0.0.2%3A8080&key=k2",
            existingBaseUrl = null,
            hasExistingKey = false,
        )
        assertEquals("http://10.0.0.2:8080", confirmState(controller).request.baseUrl)
    }

    @Test
    fun `MUTATION PIN -- the dialog state never carries the key in its string form`() {
        val controller = PairingController()
        controller.offer(link, existingBaseUrl = "http://old.lan", hasExistingKey = true)
        assertFalse(controller.state.toString().contains(secret))
    }
}
