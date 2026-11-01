package dev.steamvault.app.ui.clients.logic

import dev.steamvault.app.net.VaultJson
import dev.steamvault.app.net.model.ClientActivityOut
import dev.steamvault.app.net.model.ClientOut
import dev.steamvault.app.net.model.ClientVersionOut
import dev.steamvault.app.net.model.ClientVersionsOut
import dev.steamvault.app.net.model.SettingsOut
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * WP RELEASE-SPLIT-1b (ADR-0031), Android twin of
 * `web/tests/release-split-1b.test.js`: the Clients hint compares an agent
 * with the BUNDLED agent of `GET /v1/settings` `clients.agent` (same-build
 * list with the server's alias list, minimum), and falls back to the
 * server version (CLIENTS-3) when the server states none. The words are
 * the web's (`ClientCardWebTwinTest` pins them against the web source).
 */
class ReleaseSplit1bAgentVersionTest {

    private val aliases = listOf(
        "0.1.0-rc17", "0.1.0-rc18", "0.1.0-rc18.1", "0.1.0-rc19", "0.1.0-rc19.2", "0.1.0-rc19.3",
        "0.1.0-rc20", "0.1.0-rc20.1", "0.1.0-rc20.2", "0.1.0-rc21", "0.1.0-rc21.1", "0.1.0-rc22",
    )
    private val clients = ClientVersionsOut(
        agent = ClientVersionOut("0.1.0-rc17", "0.1.0-rc22", "0.1.0-rc17", aliases),
        app = ClientVersionOut("0.1.0-rc22", "0.1.0-rc22", "0.1.0-rc17", listOf("0.1.0-rc22")),
    )
    private val ref = agentVersionRef("0.1.0-rc22", clients)

    private fun merged(version: String?, key: String = "agent:pc") = MergedClient(
        ClientActivityOut(key = key, kind = "agent", client_id = key.removePrefix("agent:"), connected = true),
        ClientOut(client_id = key.removePrefix("agent:"), first_seen = "2026-11-01T00:00:00Z", last_reported_at = "2026-11-01T00:00:00Z", agent_version = version),
    )

    @Test
    fun `compareReleaseVersions -- natural pre-release order, release after its rcs, junk is null`() {
        val order = listOf("0.0.9", "0.1.0-rc9", "0.1.0-rc17", "0.1.0-rc21", "0.1.0-rc21.1", "0.1.0-rc22", "0.1.0", "0.1.1", "0.2.0", "1.0.0")
        for (i in order.indices) {
            for (j in order.indices) {
                assertEquals("${order[i]} vs ${order[j]}", Integer.signum(i - j), compareReleaseVersions(order[i], order[j]))
            }
        }
        assertEquals(-1, compareReleaseVersions("0.1.0-beta", "0.1.0-rc1"))
        for (bad in listOf("dev", "", null, "0.1", "v0.1.0", " 0.1.0")) {
            assertNull(compareReleaseVersions(bad, "0.1.0"))
            assertNull(compareReleaseVersions("0.1.0", bad))
        }
    }

    @Test
    fun `MUTATION TARGET -- same build never warns, below minimum and other builds do`() {
        for (v in aliases) assertNull(v, agentVersionIssue(v, ref))
        assertNull(agentVersionIssue(" 0.1.0-rc19.2 ", ref))
        assertEquals("minimum", agentVersionIssue("0.1.0-rc16", ref))
        assertEquals("minimum", agentVersionIssue("0.1.0-rc9", ref))
        assertEquals("bundled", agentVersionIssue("0.1.0-rc23", ref))
        assertEquals("bundled", agentVersionIssue("0.1.0", ref))
        for (unknown in listOf(null, "", "dev", "0.1")) assertNull(agentVersionIssue(unknown, ref))
        val noMin = agentVersionRef("0.1.0-rc22", clients.copy(agent = clients.agent!!.copy(minimum = null)))
        assertEquals("bundled", agentVersionIssue("0.1.0-rc16", noMin))
    }

    @Test
    fun `no bundled agent -- the CLIENTS-3 rule against the server version`() {
        val legacy = agentVersionRef("0.1.0-rc22", null)
        assertEquals(AgentVersionRef("0.1.0-rc22", null, null, emptyList()), legacy)
        assertEquals("server", agentVersionIssue("0.1.0-rc18", legacy))
        assertNull(agentVersionIssue("0.1.0-rc22", legacy))
        assertEquals(AgentVersionRef("0.1.0", null, null, emptyList()), agentVersionRef("0.1.0", ClientVersionsOut(agent = ClientVersionOut("dev", "dev"))))
        assertEquals(OutdatedAgent("0.1.0-rc18", "0.1.0-rc22"), outdatedAgentInfo(merged("0.1.0-rc18"), "0.1.0-rc22"))
    }

    @Test
    fun `card texts and tiles use the bundled agent`() {
        val below = outdatedAgentInfo(merged("0.1.0-rc16"), "0.1.0-rc22", ref)!!
        assertEquals(
            "Agent 0.1.0-rc16 is older than the minimum 0.1.0-rc17. Update vault-agent on this PC to match the server.",
            outdatedAgentText(below),
        )
        assertEquals(
            "Agent 0.1.0-rc23 · server's agent 0.1.0-rc22. Update vault-agent on this PC to match the server.",
            outdatedAgentText(outdatedAgentInfo(merged("0.1.0-rc23"), "0.1.0-rc22", ref)!!),
        )
        assertNull(outdatedAgentInfo(merged("0.1.0-rc20.2"), "0.1.0-rc22", ref))
        assertEquals(outdatedAgentText(below), clientCardView(merged("0.1.0-rc16"), "7d", "0.1.0-rc22", 0L, null, ref).outdatedText)
        assertNull(clientCardView(merged("0.1.0-rc21.1"), "7d", "0.1.0-rc22", 0L, null, ref).outdatedText)
        val tile = summaryTiles(
            listOf(merged("0.1.0-rc17", "agent:a"), merged("0.1.0-rc21.1", "agent:b"), merged("0.1.0-rc16", "agent:c")),
            "7d", "0.1.0-rc22", ref,
        ).first { it.key == "agents" }
        assertEquals(1, tile.outdated)
        assertTrue(tile.hi)
        val calm = summaryTiles(listOf(merged("0.1.0-rc19.3")), "7d", null, ref).first { it.key == "agents" }
        assertEquals("1 paired · all match the server", calm.detail)
        assertFalse(calm.hi)
    }

    @Test
    fun `settings JSON with clients parses, and without it stays null`() {
        val withClients = VaultJson.decodeFromString(
            SettingsOut.serializer(),
            """{"readonly":false,"server_version":"0.1.0-rc22","settings":[],"clients":{"agent":{"version":"0.1.0-rc17","build":"0.1.0-rc22","minimum":"0.1.0-rc17","same_build":["0.1.0-rc17","0.1.0-rc22"]},"app":null}}""",
        )
        assertEquals("0.1.0-rc22", withClients.clients!!.agent!!.build)
        assertNull(withClients.clients!!.app)
        val without = VaultJson.decodeFromString(SettingsOut.serializer(), """{"readonly":false,"server_version":"0.1.0"}""")
        assertNull(without.clients)
    }
}
