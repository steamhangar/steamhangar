package dev.steamvault.app.ui.clients.logic

import dev.steamvault.app.net.VaultJson
import dev.steamvault.app.net.model.ClientOut
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * WP APP-FEAT-2 (AGENT-FEAT-1 parity): a PC row passes the SERVER's
 * `presence` through and never recomputes it from `last_reported_at`.
 */
class ClientPresenceTest {

    private fun client(presence: String?, lastReportedAt: String, agentVersion: String? = null) = ClientOut(
        client_id = "pc",
        first_seen = "2026-09-01T00:00:00Z",
        last_reported_at = lastReportedAt,
        presence = presence,
        agent_version = agentVersion,
    )

    @Test
    fun `MUTATION PIN -- presence is the server's word even when last_reported_at says otherwise`() {
        // Reported years ago, but the server says online: show online.
        val stale = buildClientRowModel(client("online", "2001-01-01T00:00:00Z"))
        assertEquals(ClientPresence.ONLINE, stale.presence.presence)
        // Reported a second ago, but the server says offline: show offline.
        val fresh = buildClientRowModel(client("offline", java.time.Instant.now().toString()))
        assertEquals(ClientPresence.OFFLINE, fresh.presence.presence)
    }

    @Test
    fun `an older server without the field shows no presence at all, never a guess`() {
        assertNull(buildClientRowModel(client(null, "2026-10-03T10:00:00Z")).presence.presence)
        assertNull(clientPresenceFor("idle"))
    }

    @Test
    fun `agent version -- verbatim like web agentVersionText, null is version not reported`() {
        assertEquals("0.1.0", buildClientRowModel(client("online", "2026-10-03T10:00:00Z", " 0.1.0 ")).presence.agentVersion)
        assertEquals("dev", buildClientRowModel(client("online", "2026-10-03T10:00:00Z", "dev")).presence.agentVersion)
        assertNull(buildClientRowModel(client("online", "2026-10-03T10:00:00Z", "  ")).presence.agentVersion)
        assertNull(buildClientRowModel(client("online", "2026-10-03T10:00:00Z")).presence.agentVersion)
        assertEquals("2026-10-03T10:00:00Z", buildClientRowModel(client("online", "2026-10-03T10:00:00Z")).presence.lastReportedAt)
    }

    @Test
    fun `the AGENT-FEAT-1 wire shape decodes, and an old shape without the four fields too`() {
        val new = VaultJson.decodeFromString(
            ClientOut.serializer(),
            """{"client_id":"pc","first_seen":"2026-09-01T00:00:00Z","last_reported_at":"2026-10-03T10:00:00Z",""" +
                """"app_count":3,"source_addrs":[],"cache_hits":0,"cache_misses":0,"bytes_served":0,""" +
                """"last_seen_in_cache_log":null,"bypass_suspected":false,"agent_version":"0.1.0",""" +
                """"report_interval_seconds":600,"presence":"online","offline_after":"2026-10-03T10:25:00Z"}""",
        )
        assertEquals("online", new.presence)
        assertEquals(600, new.report_interval_seconds)
        assertEquals("2026-10-03T10:25:00Z", new.offline_after)
        val old = VaultJson.decodeFromString(
            ClientOut.serializer(),
            """{"client_id":"pc","first_seen":"2026-09-01T00:00:00Z","last_reported_at":"2026-10-03T10:00:00Z"}""",
        )
        assertNull(old.presence)
        assertNull(old.agent_version)
    }

    @Test
    fun `formatAgo matches web format js formatAgo, word for word`() {
        val now = java.time.Instant.parse("2026-10-03T12:00:00Z").toEpochMilli()
        assertEquals("just now", formatAgo("2026-10-03T11:59:30Z", now))
        assertEquals("4 min ago", formatAgo("2026-10-03T11:56:00Z", now))
        assertEquals("59 min ago", formatAgo("2026-10-03T11:00:01Z", now))
        assertEquals("2 h ago", formatAgo("2026-10-03T10:00:00Z", now))
        assertEquals("1 day ago", formatAgo("2026-10-02T12:00:00Z", now))
        assertEquals("3 days ago", formatAgo("2026-09-30T11:00:00Z", now))
        assertEquals("in the future (clocks differ)", formatAgo("2026-10-03T12:05:00Z", now))
        assertEquals("just now", formatAgo("2026-10-03T12:00:30Z", now))
        assertNull(formatAgo(null, now))
        assertNull(formatAgo("not a time", now))
    }
}
