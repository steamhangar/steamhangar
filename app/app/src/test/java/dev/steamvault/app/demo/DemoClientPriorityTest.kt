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
 * WP CAP-503-FIX-1b: the demo's client-priority hold (web demo twin,
 * `web/tests/demo-data-client-hold.test.js`), added by
 * [DemoState.withClientHold] in the app's demo mode: job 900_102 is paused
 * for a device, GC dry run 900_103 waits in the queue, the running 900_101
 * was resumed by choice. While it holds no new prefill or GC job starts
 * (operator decisions 2026-11-21: Weg A for queued GC, Weg B for Resume);
 * after [DemoState.DEMO_HOLD_POLLS] job polls it ends by itself, the paused
 * job resumes and the queue drains.
 */
class DemoClientPriorityTest {
    private val utc = ZoneId.of("UTC")
    private val cobalt = 4_010_020 // idle, no active job: a fresh enqueue target
    private val halcyon = 4_010_040

    private fun held() = DemoState.fresh().withClientHold()

    @Test
    fun `the scenario -- holding for the online agent's address, one paused for it, one queued, one resumed by choice`() {
        val state = held()
        val cp = state.clientPriority()
        assertTrue(cp.holding)
        assertFalse(cp.restored)
        assertEquals("10.0.0.42", cp.clients.single().addr)
        assertNull("no name is set for that address in the demo", cp.clients.single().name)
        assertEquals(Instant.parse(cp.last_client_at).plusSeconds(120L), Instant.parse(cp.resume_at))
        assertEquals(listOf(900_102), cp.paused_job_ids)
        assertEquals(listOf(900_101), cp.override_job_ids)
        val jobs = state.listJobSummaries(50)
        assertEquals("client", jobs.single { it.id == 900_102 }.pause_reason)
        assertEquals("Duskfall Array: finishing it changes nothing showcased", 4_010_010, jobs.single { it.id == 900_102 }.appid)
        assertTrue(jobs.single { it.id == 900_101 }.client_override)
        val gc = jobs.single { it.id == 900_103 }
        assertEquals("queued", gc.status)
        assertEquals("gc", gc.type)
        val notes = clientPriorityNotes(partitionJobs(jobs), state.clientPriority(), utc)
        assertTrue(notes.hold.getValue(900_102).startsWith("Paused to give 10.0.0.42 the full speed"))
        assertEquals("Runs alongside 10.0.0.42 by your choice.", notes.alongside.getValue(900_101))
        assertTrue(notes.queue!!.startsWith("Queued downloads wait to give 10.0.0.42"))
    }

    @Test
    fun `MUTATION PIN -- during the hold a new prefill and a GC job stay queued`() {
        val state = held()
        val ref = state.enqueuePrefill(listOf(cobalt)).single()
        assertEquals("queued", ref.status)
        val gc = state.gc(halcyon, execute = false)
        assertEquals("queued", gc.status)
        repeat(DemoState.DEMO_HOLD_POLLS - 1) { poll ->
            val jobs = state.listJobSummaries(50)
            assertEquals("poll ${poll + 1}: still waiting", "queued", jobs.single { it.id == ref.job_id }.status)
            assertEquals("poll ${poll + 1}: GC waits too (Weg A)", "queued", jobs.single { it.id == gc.job_id }.status)
        }
        assertTrue(state.clientPriority().holding)
    }

    @Test
    fun `MUTATION PIN -- the hold ends by itself after its polls, the paused job resumes and the queue drains`() {
        val state = held()
        val ref = state.enqueuePrefill(listOf(cobalt)).single()
        var jobs = state.listJobSummaries(50)
        repeat(DemoState.DEMO_HOLD_POLLS - 1) { jobs = state.listJobSummaries(50) }
        // The last hold poll ended it: its own answer already shows the drain.
        assertEquals("running", jobs.single { it.id == 900_102 }.status)
        assertNull(jobs.single { it.id == 900_102 }.pause_reason)
        assertEquals("running", jobs.single { it.id == 900_103 }.status)
        assertEquals("running", jobs.single { it.id == ref.job_id }.status)
        val cp = state.clientPriority()
        assertFalse(cp.holding)
        assertTrue(cp.clients.isEmpty())
        assertNull(cp.resume_at)
        assertTrue(cp.paused_job_ids.isEmpty())
        repeat(3) { jobs = state.listJobSummaries(50) }
        for (id in listOf(900_102, 900_103, ref.job_id)) assertEquals("job $id finished", "done", jobs.single { it.id == id }.status)
        // After the hold a new job starts at once again.
        assertEquals("running", state.enqueuePrefill(listOf(halcyon)).single().status)
    }

    @Test
    fun `Pause on the job paused for a device makes it manual, Resume overrides the hold`() {
        val state = held()
        val out = state.controlJob(900_102, JobControlAction.PAUSE)
        assertEquals("immediate", out.outcome)
        assertEquals("manual", state.listJobSummaries(50).single { it.id == 900_102 }.pause_reason)
        state.controlJob(900_102, JobControlAction.RESUME)
        val resumed = state.listJobSummaries(50).single { it.id == 900_102 }
        assertEquals("running", resumed.status)
        assertNull(resumed.pause_reason)
        assertTrue(resumed.client_override)
        assertEquals(
            ClientPriorityWording.OVERRIDE_SEEN.replace("{devices}", "10.0.0.42"),
            clientPriorityNotes(partitionJobs(listOf(resumed)), state.clientPriority(), utc).alongside[900_102],
        )
    }

    @Test
    fun `without the scenario (fresh alone, as in tests) nothing is held and new jobs start at once`() {
        val state = DemoState.fresh()
        val cp = state.clientPriority()
        assertFalse(cp.holding)
        assertNull(cp.last_client_at)
        val jobs = state.listJobSummaries(50)
        assertTrue(jobs.none { it.id == 900_102 || it.id == 900_103 })
        assertFalse(jobs.single { it.id == 900_101 }.client_override)
        assertEquals("running", state.enqueuePrefill(listOf(cobalt)).single().status)
    }

    @Test
    fun `withClientHold keeps new job ids past the hold jobs`() {
        val state = held()
        val ref = state.enqueuePrefill(listOf(cobalt)).single()
        assertTrue("new id ${ref.job_id} must not collide with 900_103", ref.job_id > 900_103)
    }
}
