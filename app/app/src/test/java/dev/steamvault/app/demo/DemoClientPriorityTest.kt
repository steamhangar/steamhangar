package dev.steamvault.app.demo

import dev.steamvault.app.ui.downloads.logic.ClientPriorityWording
import dev.steamvault.app.ui.downloads.logic.clientPriorityNotes
import dev.steamvault.app.ui.downloads.logic.partitionJobs
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant
import java.time.ZoneId

/**
 * WP CAP-503-FIX-1b: the demo's client-priority hold (web demo twin): a
 * device loads through the cache, a prefill is paused for it, one job
 * waits in the queue, and the running one was resumed by choice.
 */
class DemoClientPriorityTest {
    private val utc = ZoneId.of("UTC")

    @Test
    fun `the demo holds for the online agent's address, resuming one idle gap after the sighting`() {
        val cp = DemoState.fresh().clientPriority()
        assertTrue(cp.holding)
        assertFalse(cp.restored)
        assertEquals("10.0.0.42", cp.clients.single().addr)
        assertNull("no name is set for that address in the demo", cp.clients.single().name)
        val seen = Instant.parse(cp.last_client_at)
        assertEquals(seen.plusSeconds(120L), Instant.parse(cp.resume_at))
        assertEquals(listOf(900_102), cp.paused_job_ids)
        assertEquals(listOf(900_101), cp.override_job_ids)
    }

    @Test
    fun `MUTATION PIN -- the seeded jobs report the hold and every text shows`() {
        val state = DemoState.fresh()
        val jobs = state.listJobSummaries(50)
        assertEquals("client", jobs.single { it.id == 900_102 }.pause_reason)
        assertTrue(jobs.single { it.id == 900_101 }.client_override)
        assertEquals("queued", jobs.single { it.id == 900_103 }.status)
        val notes = clientPriorityNotes(partitionJobs(jobs), state.clientPriority(), utc)
        assertTrue(notes.hold.getValue(900_102).startsWith("Paused to give 10.0.0.42 the full speed"))
        assertEquals("Runs alongside 10.0.0.42 by your choice.", notes.alongside.getValue(900_101))
        assertTrue(notes.queue!!.startsWith("Queued downloads wait to give 10.0.0.42"))
    }

    @Test
    fun `Pause on the job paused for a device makes it manual, Resume overrides the hold`() {
        val state = DemoState.fresh()
        val out = state.controlJob(900_102, JobControlAction.PAUSE)
        assertEquals("immediate", out.outcome)
        assertEquals("manual", state.listJobSummaries(50).single { it.id == 900_102 }.pause_reason)
        state.controlJob(900_102, JobControlAction.RESUME)
        val resumed = state.listJobSummaries(50).single { it.id == 900_102 }
        assertNull(resumed.pause_reason)
        assertTrue(resumed.client_override)
        assertEquals(ClientPriorityWording.OVERRIDE_SEEN.replace("{devices}", "10.0.0.42"), clientPriorityNotes(partitionJobs(listOf(resumed)), state.clientPriority(), utc).alongside[900_102])
    }
}
