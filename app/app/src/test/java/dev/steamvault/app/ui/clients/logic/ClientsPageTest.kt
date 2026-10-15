package dev.steamvault.app.ui.clients.logic

import dev.steamvault.app.net.model.ClientActivityOut
import dev.steamvault.app.net.model.ClientStatsOut
import dev.steamvault.app.net.model.ClientsOverviewOut
import dev.steamvault.app.net.model.EventFeedOut
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
 * WP D15d: `ClientsPage.kt`, the port of web `lib/clients-page.js`. Every
 * expected string is a LITERAL in the web's words (web/tests/
 * clients-page.test.js), never derived from the code under test.
 */
class ClientsPageTest {
    private val now = Instant.parse("2026-10-14T12:00:00Z").toEpochMilli()
    private val gib = 1024L * 1024L * 1024L
    private val mib = 1024.0 * 1024.0

    private val agent = ClientActivityOut(
        key = "agent:demon",
        kind = "agent",
        client_id = "demon",
        addresses = listOf("192.168.1.20"),
        connected = true,
        last_active = "2026-10-14T11:56:00Z",
        installed_app_count = 12,
        stats = ClientStatsOut(bytes_served = 3 * gib, hit_rate = 0.9),
        loading_now = LoadingNowOut(
            loading = true,
            bytes_per_second = 40 * mib,
            apps = listOf(LoadingAppOut(appid = 10, name = "Graveyard Keeper 2", bytes_per_second = 40 * mib)),
        ),
        recent_apps = listOf(
            RecentAppOut(appid = 10, name = "Graveyard Keeper 2", bytes_served = 2 * gib),
            RecentAppOut(appid = 11, name = null, bytes_served = 300L * 1024 * 1024),
            RecentAppOut(appid = 12, name = "Third", bytes_served = 0),
            RecentAppOut(appid = 13, name = "Fourth", bytes_served = 1),
        ),
        recent_unattributed_bytes = gib,
    )

    private val address = ClientActivityOut(
        key = "addr:10.0.0.9",
        kind = "address",
        addresses = listOf("10.0.0.9"),
        connected = null,
        loading_now = null,
        stats = null,
        recent_apps = null,
    )

    @Test
    fun `names and kind lines`() {
        assertEquals("demon", clientName(agent))
        assertEquals("10.0.0.9", clientName(address))
        assertEquals("10.0.0.7", clientName(address.copy(addresses = emptyList(), key = "addr:10.0.0.7")))
        assertEquals("vault-agent · 192.168.1.20", clientKindText(agent))
        assertEquals("vault-agent · no address in the cache log", clientKindText(agent.copy(addresses = emptyList())))
        assertEquals("No agent · seen in the cache log", clientKindText(address))
    }

    private val runner = ClientActivityOut(
        key = "runner",
        kind = "runner",
        addresses = listOf("172.18.0.1"),
        connected = true,
        name = null,
        name_addr = null,
        display_name = "Prefill runner",
    )

    @Test
    fun `D15-NAMES the title is the server's display_name, the line keeps what it replaced`() {
        val named = agent.copy(name = "Living room PC", name_addr = "192.168.1.20", display_name = "Living room PC")
        assertEquals("Living room PC", clientName(named))
        assertEquals("vault-agent on \u2068demon\u2069 · 192.168.1.20", clientKindText(named))
        assertEquals(
            "vault-agent on \u2068demon\u2069 · no address in the cache log",
            clientKindText(named.copy(addresses = emptyList())),
        )
        // Unnamed, the server's display_name is the PC name: line unchanged.
        val unnamed = agent.copy(name = null, name_addr = "192.168.1.20", display_name = "demon")
        assertEquals("demon", clientName(unnamed))
        assertEquals("vault-agent · 192.168.1.20", clientKindText(unnamed))
        val namedAddr = address.copy(name = "frame", name_addr = "10.0.0.9", display_name = "frame")
        assertEquals("frame", clientName(namedAddr))
        assertEquals("No agent · 10.0.0.9 · seen in the cache log", clientKindText(namedAddr))
        assertEquals("No agent · seen in the cache log", clientKindText(namedAddr.copy(addresses = emptyList())))
        // An empty display_name (never sent, defensive) falls back.
        assertEquals("demon", clientName(agent.copy(display_name = "")))
        assertEquals("\u2068x\u2069", isolate("x"))
    }

    @Test
    fun `D15-RUNNER the runner is titled by the server and described as the server's prefill downloads`() {
        assertEquals("Prefill runner", clientName(runner))
        assertEquals("This server's prefill downloads · 172.18.0.1", clientKindText(runner))
        assertEquals("This server's prefill downloads", clientKindText(runner.copy(addresses = emptyList())))
        assertNull(installedText(runner))
        assertEquals(listOf(true, false, false), listOf(isRunnerClient(runner), isAgentClient(runner), isAddressClient(runner)))
        assertEquals(listOf(true, false), listOf(isAddressClient(address), isAddressClient(agent)))
        // The runner is not an agentless machine: no agentless note for it.
        assertFalse(showAgentlessNote(listOf(agent, runner)))
        assertTrue(showAgentlessNote(listOf(agent, runner, address)))
        // Counted like the web counts it (every overview entry).
        assertEquals("2 clients · 2 connected", clientsSummaryText(listOf(agent, runner)))
        val card = buildClientCardModel(runner, "24h", now)
        assertEquals("Prefill runner", card.name)
        assertEquals("This server's prefill downloads · 172.18.0.1", card.kindText)
        assertNull(card.installed)
    }

    @Test
    fun `connection chip has three honest states`() {
        assertEquals(ConnectionChip("Agent online", ConnectionLook.ON), connectionChip(agent))
        assertEquals(ConnectionChip("Agent offline", ConnectionLook.OFF), connectionChip(agent.copy(connected = false)))
        assertEquals(ConnectionChip("No agent", ConnectionLook.OFF), connectionChip(address.copy(connected = false)))
        assertEquals(ConnectionChip("Loading", ConnectionLook.ON), connectionChip(address.copy(connected = true)))
        assertEquals(ConnectionChip("No current data", ConnectionLook.UNKNOWN), connectionChip(address))
    }

    @Test
    fun `loading now line`() {
        assertEquals("Loading now: 40.0 MB/s · Graveyard Keeper 2", loadingNowText(agent))
        assertEquals("Loading now: —", loadingNowText(address))
        assertEquals("Loading now: nothing", loadingNowText(agent.copy(loading_now = LoadingNowOut(loading = false))))
        assertEquals(
            "Loading now: 4.5 MB/s · content not linked to one game",
            loadingNowText(agent.copy(loading_now = LoadingNowOut(loading = true, bytes_per_second = 4.5 * mib))),
        )
    }

    @Test
    fun `installed only for agents`() {
        assertEquals("12 games installed", installedText(agent))
        assertEquals("1 game installed", installedText(agent.copy(installed_app_count = 1)))
        assertEquals("Installed games: not reported", installedText(agent.copy(installed_app_count = null)))
        assertNull(installedText(address))
    }

    @Test
    fun `stats and recent apps lines`() {
        assertEquals("3.0 GB loaded in the last 7 days · hit rate 90%", statsText(agent, "7d"))
        assertNull(statsText(address, "7d"))
        assertEquals(
            "Loaded recently: Graveyard Keeper 2 (2.0 GB), Unknown app (300 MB), Third (0 GB), +1 more, 1.0 GB not linked to one game",
            recentAppsText(agent),
        )
        assertNull(recentAppsText(address))
        assertNull(recentAppsText(agent.copy(recent_apps = emptyList(), recent_unattributed_bytes = 0)))
    }

    @Test
    fun `last active and the summary line`() {
        assertEquals("Last active 4 min ago", lastActiveText(agent, now))
        assertEquals("Last active: never", lastActiveText(address, now))
        assertEquals("2 clients · 1 connected", clientsSummaryText(listOf(agent, address)))
        assertEquals("1 client · 0 connected", clientsSummaryText(listOf(address)))
    }

    @Test
    fun `figures and the agentless note`() {
        val ok = ClientsOverviewOut(event_feed = EventFeedOut(enabled = true, state = "ok"), range = "7d")
        assertTrue(clientsHaveFigures(ok))
        assertTrue(clientsHaveFigures(ok.copy(event_feed = EventFeedOut(enabled = true, state = "stale"))))
        assertFalse(clientsHaveFigures(ok.copy(event_feed = EventFeedOut(enabled = false, state = "disabled"))))
        assertTrue(showAgentlessNote(listOf(agent, address)))
        assertFalse(showAgentlessNote(listOf(agent)))
    }

    @Test
    fun `the card model collects every line, the address card has no installed line`() {
        val card = buildClientCardModel(address, "24h", now)
        assertEquals("10.0.0.9", card.name)
        assertNull(card.installed)
        assertNull(card.stats)
        assertEquals("Last active: never", card.lastActive)
        assertEquals("12 games installed", buildClientCardModel(agent, "24h", now).installed)
    }

    @Test
    fun `page constants are the web's`() {
        assertEquals(
            "Machines without vault-agent show their address and cache traffic only. A PC with vault-agent can also show up here by its address until the server can match the two.",
            AGENTLESS_NOTE,
        )
        assertEquals(
            "No clients to show. A machine appears here once it loads through the cache or runs vault-agent.",
            NO_CLIENTS_TEXT,
        )
        assertEquals("Loading clients…", LOADING_CLIENTS_TEXT)
        assertEquals("Could not load clients", COULD_NOT_LOAD_CLIENTS)
    }
}
