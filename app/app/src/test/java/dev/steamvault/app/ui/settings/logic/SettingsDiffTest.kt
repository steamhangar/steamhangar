package dev.steamvault.app.ui.settings.logic

import dev.steamvault.app.net.model.SettingInfoOut
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SettingsDiffTest {

    private fun entry(
        key: String,
        effective: JsonElement,
        source: String = "env",
        envOnly: Boolean = false,
        applies: String = "immediately",
    ) = SettingInfoOut(key, effective, source, JsonNull, applies, envOnly)

    // ---- Text drafts ----------------------------------------------------------

    @Test
    fun `MUTATION PIN -- a no-op edit (types the same value back) is dropped from the PATCH body`() {
        val entries = listOf(entry("vault_name", JsonPrimitive("vault-01"), source = "db"))
        val drafts = mapOf("vault_name" to SettingDraft.Text("vault-01"))

        val body = buildSettingsPatchDraft(entries, drafts)

        assertTrue(body.isEmpty())
    }

    @Test
    fun `a genuinely changed value is included as a set`() {
        val entries = listOf(entry("vault_name", JsonPrimitive("vault-01"), source = "env"))
        val drafts = mapOf("vault_name" to SettingDraft.Text("new-name"))

        val body = buildSettingsPatchDraft(entries, drafts)

        assertEquals(JsonPrimitive("new-name"), body["vault_name"])
    }

    @Test
    fun `surrounding whitespace does not count as a change`() {
        val entries = listOf(entry("vault_name", JsonPrimitive("vault-01"), source = "db"))
        val drafts = mapOf("vault_name" to SettingDraft.Text("  vault-01  "))

        val body = buildSettingsPatchDraft(entries, drafts)

        assertTrue(body.isEmpty())
    }

    @Test
    fun `an int-typed setting compares against its decimal text form`() {
        val entries = listOf(entry("schedule_interval_minutes", JsonPrimitive(60)))
        val drafts = mapOf("schedule_interval_minutes" to SettingDraft.Text("60"))

        assertTrue(buildSettingsPatchDraft(entries, drafts).isEmpty())
    }

    @Test
    fun `ADR-0009 -- blank is a real value for schedule_window, never coerced to a reset`() {
        val entries = listOf(entry("schedule_window", JsonPrimitive("22:00-06:00"), source = "db"))
        val drafts = mapOf("schedule_window" to SettingDraft.Text(""))

        val body = buildSettingsPatchDraft(entries, drafts)

        assertEquals(JsonPrimitive(""), body["schedule_window"])
    }

    // ---- Reset drafts -----------------------------------------------------------

    @Test
    fun `a reset on a db-sourced key sends null`() {
        val entries = listOf(entry("auto_gc", JsonPrimitive("execute"), source = "db"))
        val drafts = mapOf("auto_gc" to SettingDraft.Reset)

        val body = buildSettingsPatchDraft(entries, drafts)

        assertTrue(body.containsKey("auto_gc"))
        assertEquals(null, body["auto_gc"])
    }

    @Test
    fun `MUTATION PIN -- a reset on a non-db-sourced key is dropped -- nothing to clear`() {
        val entries = listOf(entry("auto_gc", JsonPrimitive("off"), source = "env"))
        val drafts = mapOf("auto_gc" to SettingDraft.Reset)

        val body = buildSettingsPatchDraft(entries, drafts)

        assertTrue(body.isEmpty())
    }

    // ---- env_only / unrecognised keys -------------------------------------------

    @Test
    fun `MUTATION PIN -- an env_only entry is never sent, even with an explicit draft`() {
        val entries = listOf(entry("db_path", JsonPrimitive("/data/vault.db"), envOnly = true))
        val drafts = mapOf("db_path" to SettingDraft.Text("/other/path"))

        assertTrue(buildSettingsPatchDraft(entries, drafts).isEmpty())
    }

    @Test
    fun `a draft for a key absent from entries is dropped without crashing`() {
        val body = buildSettingsPatchDraft(emptyList(), mapOf("nonexistent" to SettingDraft.Text("x")))
        assertTrue(body.isEmpty())
    }

    // ---- webhook_events (list-shaped) --------------------------------------------

    @Test
    fun `webhook_events -- an EventsList draft matching the effective set (different order) is a no-op`() {
        val effective = JsonArray(listOf(JsonPrimitive("job.done"), JsonPrimitive("job.error")))
        val entries = listOf(entry("webhook_events", effective, source = "db"))
        val drafts = mapOf("webhook_events" to SettingDraft.EventsList(listOf("job.error", "job.done")))

        assertTrue(buildSettingsPatchDraft(entries, drafts).isEmpty())
    }

    @Test
    fun `webhook_events -- a genuinely different set is sent sorted, blanks stripped (no dedup -- matches the web port exactly)`() {
        val effective = JsonArray(listOf(JsonPrimitive("job.done")))
        val entries = listOf(entry("webhook_events", effective, source = "db"))
        val drafts = mapOf(
            "webhook_events" to SettingDraft.EventsList(listOf("job.error", "job.done", "job.error", " ", "")),
        )

        val body = buildSettingsPatchDraft(entries, drafts)

        // Blanks are stripped and the result is SORTED, but duplicates are
        // NOT removed -- web/js/lib/settings-diff.js's normalizeEventsList
        // does not dedupe either (`.filter(Boolean).sort()`, no `Set`), and
        // this port stays faithful to that, not a "fixed" behaviour.
        assertEquals(
            JsonArray(listOf(JsonPrimitive("job.done"), JsonPrimitive("job.error"), JsonPrimitive("job.error"))),
            body["webhook_events"],
        )
    }

    @Test
    fun `webhook_events -- a comma-text draft is accepted and normalized the same way`() {
        val effective = JsonArray(listOf(JsonPrimitive("job.done")))
        val entries = listOf(entry("webhook_events", effective, source = "db"))
        val drafts = mapOf("webhook_events" to SettingDraft.Text("job.done, job.error"))

        val body = buildSettingsPatchDraft(entries, drafts)

        assertEquals(
            JsonArray(listOf(JsonPrimitive("job.done"), JsonPrimitive("job.error"))),
            body["webhook_events"],
        )
    }

    // ---- multiple keys in one call -------------------------------------------------

    @Test
    fun `only the keys that actually changed appear in a multi-key patch`() {
        val entries = listOf(
            entry("vault_name", JsonPrimitive("vault-01"), source = "db"),
            entry("schedule_window", JsonPrimitive("22:00-06:00"), source = "db"),
        )
        val drafts = mapOf(
            "vault_name" to SettingDraft.Text("vault-01"), // unchanged
            "schedule_window" to SettingDraft.Text("20:00-08:00"), // changed
        )

        val body = buildSettingsPatchDraft(entries, drafts)

        assertFalse(body.containsKey("vault_name"))
        assertEquals(JsonPrimitive("20:00-08:00"), body["schedule_window"])
        assertEquals(1, body.size)
    }

    // ---- WP WEB-FIX-8: dirty state for the save bar ---------------------------------

    @Test
    fun `MUTATION PIN -- dirty means the patch changes something, a value typed back is clean again`() {
        val entries = listOf(entry("vault_name", JsonPrimitive("vault-01"), source = "db"))
        assertFalse(settingsDirty(entries, emptyMap()))
        assertTrue(settingsDirty(entries, mapOf("vault_name" to SettingDraft.Text("vault-02"))))
        assertFalse("touched but equal to the saved value", settingsDirty(entries, mapOf("vault_name" to SettingDraft.Text("vault-01"))))
        assertTrue("clearing a db override is a change", settingsDirty(entries, mapOf("vault_name" to SettingDraft.Reset)))
        assertFalse("nothing loaded yet", settingsDirty(null, mapOf("vault_name" to SettingDraft.Text("x"))))
    }
}
