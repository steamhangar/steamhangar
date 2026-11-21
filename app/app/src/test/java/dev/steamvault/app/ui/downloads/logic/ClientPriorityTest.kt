package dev.steamvault.app.ui.downloads.logic

import dev.steamvault.app.net.VaultJson
import dev.steamvault.app.net.model.ClientLoadOut
import dev.steamvault.app.net.model.ClientPriorityOut
import dev.steamvault.app.net.model.JobSummary
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.ZoneId

/**
 * WP CAP-503-FIX-1b: the client-priority texts of the Downloads screen, one
 * test per state (held, queued while holding, override, restored, manual),
 * mirroring `web/tests/client-priority.test.js`. Expected texts are string
 * literals, never built from [ClientPriorityWording]; the clock is pinned to
 * UTC so `resume_at` 10:02:00Z reads "10:02".
 */
class ClientPriorityTest {
    private val utc = ZoneId.of("UTC")

    private fun job(id: Int, status: String, pauseReason: String? = null, overridden: Boolean = false, stop: String? = null, type: String = "prefill") =
        JobSummary(
            id = id,
            appid = 10 + id,
            type = type,
            status = status,
            created_at = "2026-11-21T09:00:00Z",
            stop_request = stop,
            pause_reason = pauseReason,
            client_override = overridden,
        )

    private val deskPc = ClientLoadOut(addr = "10.0.0.21", name = "Desk PC", miss_bytes_per_second = 9_000_000.0)

    private fun holding(
        clients: List<ClientLoadOut> = listOf(deskPc),
        restored: Boolean = false,
        resumeAt: String? = "2026-11-21T10:02:00Z",
    ) = ClientPriorityOut(
        enabled = true,
        holding = true,
        since = "2026-11-21T10:00:00Z",
        last_client_at = "2026-11-21T10:00:00Z",
        resume_at = resumeAt,
        clients = clients,
        restored = restored,
    )

    private val idle = ClientPriorityOut(enabled = true, holding = false)

    @Test
    fun `the API answer decodes`() {
        val cp = VaultJson.decodeFromString(
            ClientPriorityOut.serializer(),
            """{"enabled":true,"holding":true,"since":"2026-11-21T10:00:00Z","last_client_at":"2026-11-21T10:00:00Z",
               "resume_at":"2026-11-21T10:02:00Z","idle_gap_seconds":120,
               "clients":[{"addr":"10.0.0.21","name":"Desk PC","miss_bytes_per_second":9000000.0}],
               "paused_job_ids":[1],"override_job_ids":[2],"restored":false,"runner_address_unknown":false}""",
        )
        assertTrue(cp.holding)
        assertEquals("Desk PC", cp.clients.single().name)
        assertEquals(listOf(1), cp.paused_job_ids)
        assertEquals(120, cp.idle_gap_seconds)
        val j = VaultJson.decodeFromString(
            JobSummary.serializer(),
            """{"id":1,"appid":10,"type":"prefill","status":"paused","created_at":"2026-11-21T09:00:00Z","pause_reason":"client","client_override":false}""",
        )
        assertEquals("client", j.pause_reason)
        // An older server sends neither field: a manual pause, no override.
        val old = VaultJson.decodeFromString(
            JobSummary.serializer(),
            """{"id":1,"appid":10,"type":"prefill","status":"paused","created_at":"2026-11-21T09:00:00Z"}""",
        )
        assertNull(old.pause_reason)
        assertFalse(old.client_override)
    }

    @Test
    fun `poll cadence is the controller's 5 s`() = assertEquals(5000L, CLIENT_PRIORITY_POLL_MS)

    @Test
    fun `joinDevices -- one, two, three names`() {
        assertEquals("", joinDevices(emptyList()))
        assertEquals("A", joinDevices(listOf("A")))
        assertEquals("A and B", joinDevices(listOf("A", "B")))
        assertEquals("A, B and C", joinDevices(listOf("A", "B", "C")))
    }

    @Test
    fun `holdDevices -- name else address, none while restored or idle`() {
        assertEquals("Desk PC", holdDevices(holding()))
        assertEquals(
            "Desk PC, 10.0.0.22 and 10.0.0.23",
            holdDevices(holding(listOf(deskPc, ClientLoadOut("10.0.0.22"), ClientLoadOut("10.0.0.23", name = "  ")))),
        )
        assertNull(holdDevices(holding(restored = true)))
        assertNull(holdDevices(idle))
        assertNull(holdDevices(null))
    }

    @Test
    fun `holdResumeClock -- local HH MM of resume_at`() {
        assertEquals("10:02", holdResumeClock(holding(), utc))
        assertEquals("11:02", holdResumeClock(holding(), ZoneId.of("Europe/Berlin")))
        assertNull(holdResumeClock(idle, utc))
        assertNull(holdResumeClock(holding(resumeAt = null), utc))
        assertNull(holdResumeClock(holding(resumeAt = "garbage"), utc))
    }

    @Test
    fun `MUTATION PIN -- held names the device and the resume time`() {
        assertEquals(
            "Paused to give Desk PC the full speed through the cache. Resumes by itself at about 10:02 unless a device keeps loading. Resume runs it now, alongside Desk PC.",
            clientHoldNote(job(1, "paused", "client"), holding(), utc),
        )
        assertEquals(
            "Paused to give Desk PC and Deck the full speed through the cache. Resumes by itself at about 10:02 unless a device keeps loading. Resume runs it now, alongside Desk PC and Deck.",
            clientHoldNote(job(1, "paused", "client"), holding(listOf(deskPc, ClientLoadOut("10.0.0.30", name = "Deck"))), utc),
        )
    }

    @Test
    fun `MUTATION PIN -- restored names no device and claims no sighting`() {
        val text = "Paused for device priority before vault-api restarted. No device has been seen loading since; resumes by itself at about 10:02 unless one starts."
        assertEquals(text, clientHoldNote(job(1, "paused", "client"), holding(clients = emptyList(), restored = true), utc))
        assertEquals(text, clientHoldNote(job(1, "paused", "client"), holding(restored = true), utc))
        assertFalse(text.contains("Desk PC"))
    }

    @Test
    fun `client-paused without a current hold -- no device, no time`() {
        val pending = "Paused for device priority. Resumes by itself once no device is loading."
        assertEquals(pending, clientHoldNote(job(1, "paused", "client"), null, utc))
        assertEquals(pending, clientHoldNote(job(1, "paused", "client"), idle, utc))
        assertEquals(pending, clientHoldNote(job(1, "paused", "client"), holding(clients = emptyList()), utc))
    }

    @Test
    fun `MUTATION PIN -- a manual pause gets no client text, also during a hold`() {
        assertNull(clientHoldNote(job(1, "paused", "manual"), holding(), utc))
        assertNull(clientHoldNote(job(1, "paused", null), holding(), utc))
        assertNull(clientHoldNote(job(1, "running", "client"), holding(), utc))
    }

    @Test
    fun `pausing for a device has its own stop note`() {
        assertEquals(
            "Pausing so a device loading through the cache gets the full speed. It resumes by itself, from the cache, not from zero.",
            clientPausingNote(job(1, "running", "client", stop = "pause")),
        )
        assertNull(clientPausingNote(job(1, "running", "manual", stop = "pause")))
        assertNull(clientPausingNote(job(1, "running", "client", stop = "cancel")))
    }

    @Test
    fun `MUTATION PIN -- override reads Runs alongside device by your choice only while holding`() {
        val over = job(2, "running", overridden = true)
        assertEquals("Runs alongside Desk PC by your choice.", clientOverrideNote(over, holding()))
        assertEquals("Runs alongside Desk PC by your choice.", clientOverrideNote(job(2, "queued", overridden = true), holding()))
        assertEquals("Runs alongside any loading device by your choice.", clientOverrideNote(over, holding(clients = emptyList(), restored = true)))
        // The restored guard: a stale client in a restored answer is never named.
        assertEquals("Runs alongside any loading device by your choice.", clientOverrideNote(over, holding(restored = true)))
        assertNull(clientOverrideNote(over, idle))
        assertNull(clientOverrideNote(over, null))
        assertNull(clientOverrideNote(job(2, "running"), holding()))
    }

    @Test
    fun `MUTATION PIN -- queued while holding names the device and the time, override-only queues get none`() {
        val queued = listOf(job(3, "queued"), job(4, "queued", type = "gc"))
        assertEquals(
            "Queued downloads wait to give Desk PC the full speed through the cache. They carry on at about 10:02 unless a device keeps loading.",
            queueHoldNote(queued, holding(), utc),
        )
        assertEquals(
            "Queued downloads wait: device priority was on before vault-api restarted, and no device has been seen loading since. They carry on at about 10:02 unless one starts.",
            queueHoldNote(queued, holding(clients = emptyList(), restored = true), utc),
        )
        assertEquals(
            "Queued downloads wait for device priority.",
            queueHoldNote(queued, holding(resumeAt = null), utc),
        )
        assertNull(queueHoldNote(queued, idle, utc))
        assertNull(queueHoldNote(emptyList(), holding(), utc))
        assertNull(queueHoldNote(listOf(job(5, "queued", overridden = true)), holding(), utc))
    }

    @Test
    fun `clientPriorityNotes maps each job to its text by section`() {
        val jobs = listOf(
            job(1, "paused", "client"),
            job(2, "paused", "manual"),
            job(3, "running", overridden = true),
            job(4, "queued"),
        )
        val notes = clientPriorityNotes(partitionJobs(jobs), holding(), utc)
        assertEquals(setOf(1), notes.hold.keys)
        assertTrue(notes.hold.getValue(1).startsWith("Paused to give Desk PC"))
        assertEquals(mapOf(3 to "Runs alongside Desk PC by your choice."), notes.alongside)
        assertTrue(notes.pausing.isEmpty())
        assertTrue(notes.queue!!.startsWith("Queued downloads wait to give Desk PC"))
        val none = clientPriorityNotes(partitionJobs(jobs), null, utc)
        assertEquals(ClientPriorityNotes(hold = mapOf(1 to ClientPriorityWording.HELD_PENDING)), none)
    }
}
