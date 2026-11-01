package dev.steamvault.app.ui.settings.logic

import dev.steamvault.app.net.model.SettingInfoOut
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** WP ANDROID-POLISH-1: the dirty dots follow the PATCH body's keys (web `paintDirtyDots`). */
class SettingsDirtyDotsTest {
    private fun e(key: String, effective: String? = null, expert: Boolean = false) = SettingInfoOut(
        key = key,
        effective = if (effective == null) JsonNull else JsonPrimitive(effective),
        source = "default",
        applies = "immediately",
        expert = expert,
    )

    private val entries = listOf(
        e("vault_name", "vault-01"),
        e("schedule_window", "03:00-07:00"),
        e("auto_gc", "off"),
        e("brand_new_normal", "x"),
        e("upstream_rate", "off", expert = true),
        e("manifest_keep", "3", expert = true),
    )
    private val placed = SettingsLayout.placeEntries(entries)

    private fun dots(drafts: Map<String, SettingDraft>) =
        settingsDirtyDots(placed, buildSettingsPatchDraft(entries, drafts).keys)

    @Test
    fun `no drafts, no dots`() {
        assertEquals(SettingsDirtyDots.NONE, dots(emptyMap()))
        assertEquals(SettingsDirtyDots.NONE, settingsDirtyDots(null, setOf("vault_name")))
    }

    @Test
    fun `MUTATION PIN -- each section with a changed key gets its dot, others do not`() {
        val d = dots(
            mapOf(
                "vault_name" to SettingDraft.Text("vault-02"),
                "auto_gc" to SettingDraft.Text("dry-run"),
                "brand_new_normal" to SettingDraft.Text("y"),
            ),
        )
        assertEquals(setOf("vault", "disk", "other"), d.sections)
        assertFalse("no expert draft, no switch dot", d.expertSwitch)
    }

    @Test
    fun `MUTATION PIN -- a field typed back to its saved value clears the dot`() {
        val d = dots(mapOf("schedule_window" to SettingDraft.Text("03:00-07:00")))
        assertEquals(SettingsDirtyDots.NONE, d)
    }

    @Test
    fun `MUTATION PIN -- an expert draft lights the Expert tab and the switch row`() {
        for (key in listOf("upstream_rate", "manifest_keep")) {
            val d = dots(mapOf(key to SettingDraft.Text("5")))
            assertEquals(key, setOf("expert"), d.sections)
            assertTrue(key, d.expertSwitch)
        }
    }

    @Test
    fun `the expert dot id is a real section id`() {
        // NITS-APP-1: the constant must name an entry of the web-pinned list.
        assertTrue(SettingsLayout.SECTIONS.any { it.id == EXPERT_SECTION_ID })
        assertEquals("expert", EXPERT_SECTION_ID)
    }
}
