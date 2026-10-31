package dev.steamvault.app.ui.clients.logic

import dev.steamvault.app.net.model.ClientActivityOut
import dev.steamvault.app.net.model.ClientOut
import dev.steamvault.app.net.model.ClientStatsOut
import dev.steamvault.app.net.model.LoadingAppOut
import dev.steamvault.app.net.model.LoadingNowOut
import dev.steamvault.app.net.model.RecentAppOut
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

/**
 * WP ANDROID-REDESIGN-1a: `ClientCard.kt`, the port of the CLIENTS-1..4 half
 * of web `lib/clients-page.js`. Expected values are LITERALS taken from the
 * web's own tests (`web/tests/client-card.test.js`, `clients-page.test.js`,
 * `clients-merge.test.js`), never derived from the code under test.
 */
class ClientCardTest {
    private val now = Instant.parse("2026-10-17T12:00:00Z").toEpochMilli()
    private val gib = 1024L * 1024L * 1024L
    private val mib = 1024.0 * 1024.0

    private fun iso(msAgo: Long) = Instant.ofEpochMilli(now - msAgo).toString()

    private fun agentRow(version: String? = "0.1.0-rc18.1", bypass: Boolean = false, appCount: Int? = null) = ClientOut(
        client_id = "desk-pc",
        first_seen = iso(86_400_000L),
        last_reported_at = iso(5 * 60_000L),
        app_count = appCount,
        source_addrs = listOf("10.0.0.42"),
        bypass_suspected = bypass,
        agent_version = version,
        presence = "online",
    )

    private val agentClient = ClientActivityOut(
        key = "agent:desk-pc",
        kind = "agent",
        client_id = "desk-pc",
        addresses = listOf("10.0.0.42"),
        connected = true,
        agent_presence = "online",
        last_active = iso(8 * 60_000L),
        installed_app_count = 24,
        stats = ClientStatsOut(bytes_served = (38.2 * gib).toLong(), hits = 98, misses = 2, hit_rate = 0.98),
        loading_now = LoadingNowOut(loading = false),
        recent_apps = listOf(
            RecentAppOut(1, "Satisfactory", (24.8 * gib).toLong()),
            RecentAppOut(2, "The Riftbreaker", (10.3 * gib).toLong()),
            RecentAppOut(3, "Dyson Sphere Program", (1.7 * gib).toLong()),
            RecentAppOut(4, "Four", gib),
            RecentAppOut(5, "Five", gib),
        ),
        recent_unattributed_bytes = (1.4 * gib).toLong(),
        name_addr = "10.0.0.42",
        display_name = "desk-pc",
    )

    private val runnerClient = ClientActivityOut(
        key = "runner",
        kind = "runner",
        addresses = listOf("10.0.0.1"),
        connected = true,
        last_active = iso(9 * 60_000L),
        stats = ClientStatsOut(bytes_served = 491 * gib, hits = 77, misses = 23, hit_rate = 0.77),
        loading_now = LoadingNowOut(loading = true, bytes_per_second = 28.8 * mib, apps = listOf(LoadingAppOut(9, "HELLDIVERS 2"))),
        recent_apps = listOf(RecentAppOut(9, "HELLDIVERS 2", 50 * gib)),
        recent_unattributed_bytes = 17 * gib,
        display_name = "Prefill runner",
    )

    private fun card(client: ClientActivityOut, agent: ClientOut? = null, server: String? = null, removeError: String? = null) =
        clientCardView(MergedClient(client, agent), "7d", server, now, removeError)

    @Test
    fun `MUTATION TARGET -- pills by state`() {
        assertEquals(CardPill("Online", PillLook.ONLINE), card(agentClient).pill)
        assertEquals("Offline", card(agentClient.copy(agent_presence = "offline", connected = true)).pill.text)
        assertEquals(CardPill("Downloading", PillLook.RUNNING), card(runnerClient).pill)
        assertEquals("Online", card(runnerClient.copy(loading_now = LoadingNowOut(loading = false))).pill.text)
        assertEquals("Offline", card(runnerClient.copy(connected = false, loading_now = LoadingNowOut(loading = false))).pill.text)
        assertEquals(CardPill("No current data", PillLook.UNKNOWN), card(runnerClient.copy(connected = null, loading_now = null)).pill)
    }

    @Test
    fun `MUTATION TARGET -- live_now wins over loading_now for the pill and the now box`() {
        val idleSwept = runnerClient.copy(loading_now = LoadingNowOut(loading = false))
        val live = idleSwept.copy(live_now = LoadingNowOut(loading = true, bytes_per_second = 28.8 * mib, apps = listOf(LoadingAppOut(9, "HELLDIVERS 2"))))
        assertEquals("Downloading", card(live).pill.text)
        assertEquals(NowBox.Loading("HELLDIVERS 2", "Loading into the cache", "28.8 MB/s"), card(live).now)
    }

    @Test
    fun `MUTATION TARGET -- the outdated box appears only when the versions differ`() {
        val outdated = card(agentClient, agentRow(), "0.1.0-rc19.3")
        assertEquals("Agent 0.1.0-rc18.1 · server 0.1.0-rc19.3. Update vault-agent on this PC to match the server.", outdated.outdatedText)
        assertEquals("Agent version differs from the server", OUTDATED_BOX_TITLE)
        assertEquals("Agent outdated", AGENT_OUTDATED_PILL)
        for (server in listOf("0.1.0-rc18.1", null, "dev")) {
            assertNull("no box for server $server", card(agentClient, agentRow(), server).outdatedText)
        }
        assertNull("no agent data, no warning", card(agentClient, null, "0.1.0").outdatedText)
    }

    @Test
    fun `isAgentOutdated -- differs only, unknown or non-release versions never warn`() {
        assertFalse(isAgentOutdated("0.1.0-rc19.3", "0.1.0-rc19.3"))
        assertTrue(isAgentOutdated("0.1.0-rc18.1", "0.1.0-rc19.2"))
        assertTrue(isAgentOutdated("0.1.0", "0.1.0-rc19.2"))
        assertTrue(isAgentOutdated("0.2.0", "0.1.0"))
        assertFalse(isAgentOutdated(" 0.1.0 ", "0.1.0"))
        for (unknown in listOf(null, "", "  ", "dev", "v0.1.0", "dev-1a2b3c4")) {
            assertFalse("agent $unknown", isAgentOutdated(unknown, "0.1.0"))
            assertFalse("server $unknown", isAgentOutdated("0.1.0", unknown))
        }
    }

    @Test
    fun `the agent health row exists only with agent data, bypass replaces healthy`() {
        assertEquals(AgentHealth(false, "Healthy", "last seen 5 min ago · agent 0.1.0-rc18.1"), card(agentClient, agentRow()).health)
        assertNull(card(runnerClient).health)
        assertNull(card(agentClient).health)
        assertEquals(
            AgentHealth(true, "Bypassing the cache", "2 games reported · none of its downloads have reached the cache recently"),
            card(agentClient, agentRow(bypass = true, appCount = 2)).health,
        )
        assertEquals("last seen 5 min ago · version not reported", card(agentClient, agentRow(version = null)).health!!.text)
        assertEquals("What does this mean?", BYPASS_HINT_SUMMARY)
    }

    @Test
    fun `MUTATION TARGET -- idle vs loading box`() {
        assertEquals(NowBox.Idle("Loading nothing right now"), card(agentClient).now)
        assertEquals(NowBox.Idle("Loading now: —"), card(agentClient.copy(loading_now = null)).now)
        assertEquals(NowBox.Loading("HELLDIVERS 2", "Loading into the cache", "28.8 MB/s"), card(runnerClient).now)
        val unlinked = card(runnerClient.copy(loading_now = LoadingNowOut(loading = true, bytes_per_second = mib))).now
        assertEquals("Content not linked to one game", (unlinked as NowBox.Loading).title)
    }

    @Test
    fun `stats -- agent Installed Loaded Hit rate, runner Loaded Hit rate Unlinked, dashes never zeros`() {
        fun cells(c: ClientCardView) = c.stats.map { "${it.key}:${it.label}:${it.value}${it.unit}:${if (it.meter != null) "meter" else "-"}" }
        assertEquals(listOf("installed:Installed:24games:-", "loaded:Loaded:38.2GB:-", "hitrate:Hit rate:98%:meter"), cells(card(agentClient)))
        assertEquals(listOf("loaded:Loaded:491GB:-", "hitrate:Hit rate:77%:meter", "unlinked:Unlinked:17.0GB:-"), cells(card(runnerClient)))
        assertEquals(
            listOf("installed:Installed:—:-", "loaded:Loaded:—:-", "hitrate:Hit rate:—:-"),
            cells(card(agentClient.copy(stats = null, installed_app_count = null))),
        )
    }

    @Test
    fun `MUTATION TARGET -- recent games as chips with +N more`() {
        val r = card(agentClient).recent!!
        assertEquals(RecentChip("Satisfactory", "24.8 GB"), r.chips[0])
        assertEquals(3, r.chips.size)
        assertEquals("+2 more", moreChipText(r.more))
        assertEquals(0, card(runnerClient).recent!!.more)
        assertEquals("Nothing loaded in the last 7 days", card(agentClient.copy(recent_apps = emptyList())).noRecent)
        assertNull(card(agentClient.copy(recent_apps = null)).recent)
        assertEquals("No traffic statistics", NO_STATS_TEXT)
        assertEquals("Recently loaded", RECENT_HEADING)
    }

    @Test
    fun `footer -- last active, unlinked note on agent cards, remove id and error`() {
        val c = card(agentClient, agentRow(), removeError = "Could not remove desk-pc: boom")
        assertEquals("Last active 8 min ago", c.lastActive)
        assertEquals("1.4 GB not linked to a game", c.unlinked)
        assertNull(card(runnerClient).unlinked)
        assertEquals("desk-pc", c.removeId)
        assertEquals("Could not remove desk-pc: boom", c.removeError)
        assertTrue(c.rename)
        // No agent data: no Remove and no error line (the remove error belongs to an agent).
        val noAgent = card(agentClient, null, removeError = "x")
        assertNull(noAgent.removeId)
        assertNull(noAgent.removeError)
        assertFalse(noAgent.removeBlocked)
        // An id the remove route cannot address: the note instead of the button.
        val slash = card(agentClient, agentRow().copy(client_id = "lab/pc"))
        assertNull(slash.removeId)
        assertTrue(slash.removeBlocked)
        assertFalse(card(runnerClient).rename)
    }

    @Test
    fun `merge -- both, client only, agent only, exact client_id`() {
        val agent = agentRow()
        val merged = mergeAgentData(listOf(agentClient, runnerClient), listOf(agent))
        assertEquals(listOf(agent, null), merged.map { it.agent })
        assertEquals(agentClient, merged[0].client)
        // No answer yet: every card as the overview sent it.
        assertEquals(listOf(agentClient, runnerClient), mergeAgentData(listOf(agentClient, runnerClient), null).map { it.client })
        // Agent only: a card from the agent row alone, no traffic figures (never zeros).
        val only = mergeAgentData(emptyList(), listOf(agent.copy(presence = null, app_count = 3)))
        assertEquals(1, only.size)
        val c = only[0].client
        assertEquals("agent:desk-pc", c.key)
        assertEquals("agent", c.kind)
        assertEquals(listOf("10.0.0.42"), c.addresses)
        assertNull("presence not sent: unknown, not offline", c.connected)
        assertNull(c.stats)
        assertNull(c.recent_apps)
        assertEquals(3, c.installed_app_count)
        assertEquals("online", mergeAgentData(emptyList(), listOf(agent)).single().client.agent_presence)
        // An address row never joins an agent card, even on the agent's address.
        val addr = ClientActivityOut(key = "addr:10.0.0.42", kind = "address", addresses = listOf("10.0.0.42"))
        val m2 = mergeAgentData(listOf(addr), listOf(agent))
        assertEquals(listOf(null, agent), m2.map { it.agent })
        // Exact match only; a repeated id counts once (first wins).
        assertEquals(2, mergeAgentData(listOf(agentClient), listOf(agent.copy(client_id = "Desk-PC"))).size)
        val first = agent.copy(agent_version = "1")
        assertEquals(first, mergeAgentData(listOf(agentClient), listOf(first, agent)).single().agent)
    }

    @Test
    fun `summary tiles -- the four fixed tiles from the cards only`() {
        val a = MergedClient(
            agentClient.copy(connected = true, loading_now = LoadingNowOut(loading = true), stats = ClientStatsOut(bytes_served = 2 * gib, hits = 3, misses = 1, hit_rate = 0.75)),
            null,
        )
        val addr = MergedClient(
            ClientActivityOut(
                key = "addr:10.0.0.77", kind = "address", addresses = listOf("10.0.0.77"), connected = false,
                loading_now = LoadingNowOut(loading = false), stats = ClientStatsOut(bytes_served = gib, hits = 1, misses = 3, hit_rate = 0.25),
            ),
            null,
        )
        val tiles = summaryTiles(listOf(a, addr), "7d", null)
        assertEquals(listOf("connected", "loaded", "hitrate", "agents"), tiles.map { it.key })
        assertEquals(listOf("1", "of 2", "1 loading now"), listOf(tiles[0].value, tiles[0].unit, tiles[0].detail))
        assertEquals(listOf("3.0", "GB", "in the last 7 days"), listOf(tiles[1].value, tiles[1].unit, tiles[1].detail))
        // Request-based over all requests (4 hits of 8), not the mean of the rates.
        assertEquals(Triple("50", "%", 50), Triple(tiles[2].value, tiles[2].unit, tiles[2].meter))
        assertEquals(listOf("1", "online"), listOf(tiles[3].value, tiles[3].unit))
    }

    @Test
    fun `summary tiles -- the Agents tile counts outdated agents only against a known server version`() {
        fun tile(list: List<MergedClient>, server: String?) = summaryTiles(list, "7d", server).first { it.key == "agents" }
        val addr = MergedClient(ClientActivityOut(key = "addr:10.0.0.77", kind = "address"), null)
        assertEquals("No agent paired", tile(listOf(addr), "0.1.0").detail)
        val old = MergedClient(agentClient, agentRow(version = "0.0.9"))
        val current = MergedClient(agentClient.copy(key = "agent:b"), agentRow(version = "0.1.0"))
        val warn = tile(listOf(old, current), "0.1.0")
        assertEquals(1, warn.outdated)
        assertTrue(warn.hi)
        assertEquals("2 paired", warn.detail)
        assertEquals("1 outdated", outdatedAgentsPillText(warn.outdated!!))
        val calm = tile(listOf(current), "0.1.0")
        assertEquals(0, calm.outdated)
        assertFalse(calm.hi)
        assertEquals("1 paired · all match the server", calm.detail)
        val unknown = tile(listOf(old), null)
        assertNull("unknown server version: no count claimed", unknown.outdated)
        assertFalse(unknown.hi)
        assertEquals("1 paired · server version not known", unknown.detail)
    }

    @Test
    fun `summary tiles -- no statistics shows dashes and says why, never zeros`() {
        val addr = ClientActivityOut(key = "addr:10.0.0.77", kind = "address", connected = true, loading_now = LoadingNowOut(loading = false))
        val tiles = summaryTiles(listOf(MergedClient(addr, null)), "7d", null)
        assertEquals("Nothing loading now", tiles[0].detail)
        assertEquals(listOf("—", "No traffic statistics"), listOf(tiles[1].value, tiles[1].detail))
        assertEquals(Triple("—", "No traffic statistics", null), Triple(tiles[2].value, tiles[2].detail, tiles[2].meter))
        val noReq = summaryTiles(listOf(MergedClient(addr.copy(stats = ClientStatsOut()), null)), "7d", null)
        assertEquals(listOf("—", "No cache requests yet"), listOf(noReq[2].value, noReq[2].detail))
    }

    @Test
    fun `splitValueUnit -- number and unit`() {
        assertEquals(ValueUnit("38.2", "GB"), splitValueUnit("38.2 GB"))
        assertEquals(ValueUnit("75", "%"), splitValueUnit("75%"))
        assertEquals(ValueUnit("< 1", "MB"), splitValueUnit("< 1 MB"))
        assertEquals(ValueUnit("30.0", "MB/s"), splitValueUnit("30.0 MB/s"))
        assertEquals(ValueUnit("—", ""), splitValueUnit(null))
        assertEquals(ValueUnit("—", ""), splitValueUnit("—"))
    }

    @Test
    fun `MUTATION TARGET -- a removed PC stays hidden for 30 s, then shows again`() {
        val removed = mapOf("desk-pc" to now)
        assertTrue(hiddenRemoved("desk-pc", removed, now + 30_000L))
        assertFalse(hiddenRemoved("desk-pc", removed, now + 30_001L))
        assertFalse(hiddenRemoved("other", removed, now))
        assertFalse(hiddenRemoved(null, removed, now))
        assertEquals(30_000L, REMOVED_HIDE_MS)
        assertEquals("Devices", DEVICES_HEADING)
    }
}
