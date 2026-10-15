package dev.steamvault.app.ui.settings.logic

import dev.steamvault.app.net.model.CoreApplyOut
import dev.steamvault.app.net.model.SettingInfoOut
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SettingsLayoutTest {
    private fun e(key: String, effective: String? = null, block: (SettingInfoOut) -> SettingInfoOut = { it }) =
        block(
            SettingInfoOut(
                key = key,
                effective = if (effective == null) JsonNull else JsonPrimitive(effective),
                source = "default",
                applies = "immediately",
            ),
        )

    @Test
    fun `placement follows the layout and never loses an unknown key`() {
        val placed = SettingsLayout.placeEntries(
            listOf(
                e("vault_name"),
                e("upstream_rate") { it.copy(expert = true) },
                e("brand_new_expert") { it.copy(expert = true) },
                e("brand_new_normal"),
                e("steam_library_steamid"),
                e("webhook_url") { it.copy(env_only = true) },
                e("webhook_events"),
                e("manifest_keep") { it.copy(env_only = true) },
            ),
        )
        assertEquals(listOf("vault_name"), placed.sections.getValue("vault").map { it.key })
        assertEquals(listOf("webhook_url", "webhook_events"), placed.sections.getValue("notify").map { it.key })
        assertEquals(listOf("brand_new_normal"), placed.sections.getValue("other").map { it.key })
        assertEquals(listOf("upstream_rate"), placed.expert.getValue("bandwidth").map { it.key })
        assertEquals(listOf("brand_new_expert"), placed.expert.getValue("housekeeping").map { it.key })
        val all = (placed.sections.values + placed.expert.values).flatten().map { it.key }
        assertFalse("the Steam block renders its own id", "steam_library_steamid" in all)
        assertFalse("env-only rows without a named place stay off the page", "manifest_keep" in all)
    }

    @Test
    fun `env wins notes`() {
        val pinned = e("vault_name") { it.copy(editable = false, env_var = "VAULT_NAME", source = "env") }
        assertTrue(SettingsLayout.isPinned(pinned))
        assertEquals("Set via env (VAULT_NAME)", SettingsLayout.envNote(pinned))
        val core = e("upstream_max_conns") { it.copy(editable = false, target = "core", env_var = "VAULT_UPSTREAM_MAX_CONNS") }
        assertTrue(SettingsLayout.isCoreFileDisabled(core))
        assertTrue(SettingsLayout.envNote(core).startsWith("Applies to vault-core only via env in this build (VAULT_UPSTREAM_MAX_CONNS)."))
        assertNull(SettingsLayout.shadowNote(pinned))
        assertEquals(
            "A value stored in Settings is overridden by env.",
            SettingsLayout.shadowNote(pinned.copy(shadowed = true)),
        )
    }

    @Test
    fun `risky confirm names label old new and the risk`() {
        val entries = listOf(
            e("upstream_max_conns", "16") { it.copy(risky = true, fallback = JsonPrimitive("16")) },
            e("resolver", "1.1.1.1") { it.copy(risky = true, fallback = JsonPrimitive("1.1.1.1")) },
            e("vault_name"),
        )
        val body = mapOf("upstream_max_conns" to JsonPrimitive("24"), "resolver" to null, "vault_name" to JsonPrimitive("x"))
        val keys = SettingsLayout.riskyKeysInBody(entries, body)
        assertEquals(listOf("upstream_max_conns", "resolver"), keys)
        val items = SettingsLayout.riskyItems(entries, body, keys)
        assertEquals("Upstream (WAN) connection cap", items[0].label)
        assertEquals("16", items[0].from)
        assertEquals("24", items[0].to)
        assertEquals("1.1.1.1 (default)", items[1].to)
    }

    @Test
    fun `off keyword and events display as plain text`() {
        assertEquals("off", SettingsLayout.displayValue(e("schedule_window", "off")))
        assertEquals("Not set", SettingsLayout.displayValue(e("schedule_window")))
        assertEquals("Not set (webhooks off)", SettingsLayout.displayValue(e("webhook_url")))
        val events = e("webhook_events") { it.copy(effective = JsonArray(listOf(JsonPrimitive("job.done")))) }
        assertEquals("Job finished", SettingsLayout.displayValue(events))
    }

    @Test
    fun `core apply line is state only`() {
        assertNull(SettingsLayout.coreApplyLine(null))
        assertEquals("vault-core: a change is waiting to be applied.", SettingsLayout.coreApplyLine(CoreApplyOut(pending = true))!!.text)
        val rejected = SettingsLayout.coreApplyLine(CoreApplyOut(state = "rejected", reason = "bad value"))!!
        assertTrue(rejected.warn)
        assertTrue(rejected.text.startsWith("vault-core rejected the last settings change: bad value"))
        assertEquals("vault-core: settings applied.", SettingsLayout.coreApplyLine(CoreApplyOut(state = "applied"))!!.text)
        assertNotNull(SettingsLayout.coreApplyLine(CoreApplyOut(state = "none")))
        assertNull(SettingsLayout.coreApplyLine(CoreApplyOut(state = "weird")))
    }

    @Test
    fun `save errors say nothing was saved only when that is certain`() {
        assertTrue(SettingsLayout.describeSaveError(409, null, null).endsWith("Nothing was saved."))
        assertTrue(SettingsLayout.describeSaveError(428, null, null, confirmed = true).contains("still asked"))
        assertEquals("Could not save: boom", SettingsLayout.describeSaveError(500, null, "boom"))
    }

    @Test
    fun `the upstream budget is neutral and Steam-only keys keep the Steam name`() {
        for (key in listOf("upstream_rate", "upstream_max_conns", "schedule_window", "resolver")) {
            val m = SettingsLayout.fieldMeta(key)
            assertFalse(key, Regex("steam|valve", RegexOption.IGNORE_CASE).containsMatchIn(m.label + " " + m.help))
        }
        assertTrue(SettingsLayout.fieldMeta("upstream_rate").label.contains("WAN"))
        assertTrue(SettingsLayout.fieldMeta("upstream_edge").label.contains("Steam"))
        assertFalse(SettingsLayout.riskText("resolver").contains("Steam"))
    }

    @Test
    fun `a 428 detail names the risky keys and error details unwrap`() {
        val detail = """{"message":"confirm","risky_keys":["upstream_max_conns","resolver"]}"""
        assertEquals(listOf("upstream_max_conns", "resolver"), SettingsLayout.riskyKeysFrom428(detail))
        assertEquals(emptyList<String>(), SettingsLayout.riskyKeysFrom428("plain text"))
        assertEquals(emptyList<String>(), SettingsLayout.riskyKeysFrom428(null))
        assertEquals("confirm", SettingsLayout.detailMessage(detail))
        assertEquals("'x': bad", SettingsLayout.detailMessage("'x': bad"))
        assertEquals("a; b", SettingsLayout.detailMessage("""[{"msg":"a"},{"msg":"b"}]"""))
        assertNull(SettingsLayout.detailMessage(null))
    }
}
