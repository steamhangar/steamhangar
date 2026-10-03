package dev.steamvault.app.demo

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.steam.SteamId64
import dev.steamvault.app.ui.downloads.logic.FailureHint
import dev.steamvault.app.ui.downloads.logic.jobFailureHint
import dev.steamvault.app.ui.library.logic.steamIdFromSettings
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/**
 * WP APP-FEAT-2: the demo must mirror the API's actual data shape
 * (docs/LEARNINGS.md, WP WEB-FIX-4): unnamed vault rows on enqueue, the
 * AGENT-FEAT-1 `/v1/clients` fields, the `/v1/about` shape, the
 * `steam_library_steamid` setting and the relay's input check.
 */
class DemoAppFeat2Test {

    private val validId = "76561198042117903"

    @Test
    fun `MUTATION PIN -- enqueue for an app the vault never knew inserts a vault row with NO name, like the real API`() {
        val state = DemoState.fresh()
        val newAppid = 4_020_010 // an owned-only fixture game
        state.enqueuePrefill(listOf(newAppid))
        val row = state.listGameSummaries().single { it.appid == newAppid }
        assertNull("the real POST /v1/prefill inserts name = NULL", row.name)
        assertEquals("running", row.status)
        assertTrue(row.needs_force)
    }

    @Test
    fun `clients carry the four AGENT-FEAT-1 fields in the real shape`() {
        val clients = DemoState.fresh().clientsOut()
        val current = clients.single { it.client_id == "demo-livingroom-pc" }
        assertEquals("online", current.presence)
        assertEquals("0.1.0", current.agent_version)
        assertEquals(600, current.report_interval_seconds)
        assertTrue(current.offline_after != null)
        val old = clients.single { it.client_id == "demo-steamdeck" }
        assertEquals("offline", old.presence)
        assertNull(old.agent_version)
        assertNull(old.report_interval_seconds)
        for (c in clients) assertTrue(c.presence in setOf("online", "offline"))
    }

    @Test
    fun `about lists the six components in the server's fixed order with statuses the server can send`() {
        val about = DemoState.fresh().aboutOut()
        assertEquals(
            listOf("vault-api", "vault-core", "vault-runner", "steamprefill", "vault-proxy", "vault-dns"),
            about.components.map { it.name },
        )
        val byName = about.components.associateBy { it.name }
        assertEquals("unknown", byName.getValue("vault-core").status) // never probed (Weg A)
        assertEquals("unknown", byName.getValue("vault-dns").status) // never probed
        assertNull(byName.getValue("vault-proxy").version) // reachability only
        for (c in about.components) {
            assertTrue(c.status in setOf("ok", "unreachable", "not_in_use", "unknown"))
            assertTrue("checked_at is YYYY-MM-DDTHH:MM:SSZ", Regex("""^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$""").matches(c.checked_at!!))
        }
    }

    @Test
    fun `the library SteamID64 setting starts blank and is stored as a string`() {
        val state = DemoState.fresh()
        assertEquals("", steamIdFromSettings(state.settingsOut()))
        state.patchSettings(mapOf("steam_library_steamid" to JsonPrimitive(validId)))
        assertEquals(validId, steamIdFromSettings(state.settingsOut()))
        val entry = state.settingsOut().settings.single { it.key == "steam_library_steamid" }
        assertEquals("db", entry.source)
        assertEquals("immediately", entry.applies)
    }

    @Test
    fun `MUTATION PIN -- an invalid library SteamID64 is refused with 422, like the real PATCH`() {
        val state = DemoState.fresh()
        try {
            state.patchSettings(mapOf("steam_library_steamid" to JsonPrimitive("1234")))
            fail("expected a 422")
        } catch (e: VaultApiError) {
            assertEquals(422, e.status)
        }
        state.patchSettings(mapOf("steam_library_steamid" to JsonPrimitive(""))) // blank clears: allowed
    }

    @Test
    fun `the demo relay answers a valid id and refuses an invalid one with 422`() {
        val state = DemoState.fresh()
        val owned = state.ownedGames(validId)
        assertEquals(owned.games.size, owned.game_count)
        assertTrue(owned.games.isNotEmpty())
        assertEquals(validId, state.playerSummaries(validId).players.single().steamid)
        try {
            state.ownedGames("not-an-id")
            fail("expected a 422")
        } catch (e: VaultApiError) {
            assertEquals(422, e.status)
        }
    }

    @Test
    fun `TWIN PIN -- the demo's SteamID64 check agrees with SteamId64 validate on every probe`() {
        val probes = listOf(
            validId,
            "76561197960265728", // BASE
            "76561197960265727", // BASE - 1
            (SteamId64.MAX).toString(),
            (SteamId64.MAX + 1).toString(),
            "7656119804211790", // 16 digits
            "765611980421179030", // 18 digits
            "+7656119804211790",
            "0x110000100000000",
            "７６５６１１９８０４２１１７９０３", // full-width digits
            "",
        )
        for (p in probes) {
            assertEquals("probe '$p'", SteamId64.validate(p) != null, isDemoValidSteamId64(p))
        }
    }

    @Test
    fun `the seed failed job reads as the real not_logged_in shape, so the demo shows the hint`() {
        val state = DemoState.fresh()
        val job = state.listJobSummaries(50).single { it.id == 900_100 }
        val excerpt = state.jobDetail(900_100).log_excerpt
        assertEquals(FailureHint.NOT_LOGGED_IN, jobFailureHint(job, excerpt))
        assertFalse(excerpt!!.contains("exited 1"))
    }
}
