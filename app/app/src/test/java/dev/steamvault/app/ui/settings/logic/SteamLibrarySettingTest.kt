package dev.steamvault.app.ui.settings.logic

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.SettingInfoOut
import dev.steamvault.app.net.model.SettingsOut
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * WP APP-FEAT-1: Settings' "Steam library" Save/Reset/Preview decisions,
 * port of web `owned-library.js::saveLibrarySteamId`/`describeLookupError`.
 */
class SteamLibrarySettingTest {

    private val key = "steam_library_steamid"
    private val validId = "76561198042117903"

    private fun response(
        effective: String = "",
        source: String = "default",
        readonly: Boolean = false,
        envOnly: Boolean = false,
        present: Boolean = true,
    ) = SettingsOut(
        readonly = readonly,
        settings = if (present) {
            listOf(SettingInfoOut(key = key, effective = JsonPrimitive(effective), source = source, fallback = JsonPrimitive(""), applies = "immediately", env_only = envOnly))
        } else {
            emptyList()
        },
    )

    @Test
    fun `MUTATION PIN -- a valid id is sent as a trimmed JSON STRING, never a number`() {
        val plan = planLibrarySteamIdSave(response(), " $validId ")
        assertTrue(plan is LibrarySteamIdSavePlan.Patch)
        plan as LibrarySteamIdSavePlan.Patch
        assertEquals(mapOf(key to JsonPrimitive(validId)), plan.body)
        assertTrue((plan.body.getValue(key) as JsonPrimitive).isString)
        assertFalse(plan.cleared)
    }

    @Test
    fun `nothing is sent when unchanged, invalid, read-only, env-only or the setting is absent`() {
        assertEquals(LibrarySteamIdSavePlan.Unchanged, planLibrarySteamIdSave(response(effective = validId, source = "db"), validId))
        assertEquals(LibrarySteamIdSavePlan.Invalid, planLibrarySteamIdSave(response(), "1234"))
        assertEquals(LibrarySteamIdSavePlan.Invalid, planLibrarySteamIdSave(response(), "0x110000100000000"))
        assertEquals(LibrarySteamIdSavePlan.Readonly, planLibrarySteamIdSave(response(readonly = true), validId))
        assertEquals(LibrarySteamIdSavePlan.EnvOnly, planLibrarySteamIdSave(response(envOnly = true), validId))
        assertEquals(LibrarySteamIdSavePlan.Absent, planLibrarySteamIdSave(response(present = false), validId))
        assertEquals(LibrarySteamIdSavePlan.Absent, planLibrarySteamIdSave(null, validId))
    }

    @Test
    fun `blank clears with an explicit empty override`() {
        val plan = planLibrarySteamIdSave(response(effective = validId, source = "db"), "  ")
        plan as LibrarySteamIdSavePlan.Patch
        assertEquals(mapOf(key to JsonPrimitive("")), plan.body)
        assertTrue(plan.cleared)
    }

    @Test
    fun `MUTATION PIN -- Reset sends null only when a db override exists`() {
        assertEquals(mapOf<String, Any?>(key to null), libraryIdResetBody(response(effective = validId, source = "db")))
        assertTrue(libraryIdResetBody(response(source = "env")).isEmpty())
        assertTrue(canResetLibrarySteamId(response(source = "db")))
        assertFalse(canResetLibrarySteamId(response(source = "default")))
        assertFalse(canResetLibrarySteamId(response(source = "db", readonly = true)))
        assertTrue(canSaveLibrarySteamId(response()))
        assertFalse(canSaveLibrarySteamId(response(readonly = true)))
        assertFalse(canSaveLibrarySteamId(response(present = false)))
    }

    @Test
    fun `Preview errors -- 409 no relay key, 422 the typed id, anything else the server detail`() {
        assertEquals(LibraryLookupError.NoRelayKey, describeLookupError(VaultApiError.Validation("x", 409, "no key")))
        assertEquals(LibraryLookupError.InvalidSteamId, describeLookupError(VaultApiError.Validation("x", 422, "bad")))
        assertEquals(LibraryLookupError.Failed("upstream down"), describeLookupError(VaultApiError.Validation("x", 502, "upstream down")))
    }
}
