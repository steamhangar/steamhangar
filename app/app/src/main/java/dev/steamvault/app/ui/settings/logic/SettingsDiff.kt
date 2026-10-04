package dev.steamvault.app.ui.settings.logic

import dev.steamvault.app.net.model.SettingInfoOut
import dev.steamvault.app.net.model.settingAsStringListOrNull
import dev.steamvault.app.net.model.settingAsStringOrNull
import dev.steamvault.app.net.model.settingPatchValue
import kotlinx.serialization.json.JsonElement

/**
 * `PATCH /v1/settings` body builder (WP 4b.7; ADR-0009) -- a direct port of
 * `web/js/lib/settings-diff.js`'s semantics onto this app's field-state
 * shape.
 *
 * The settings screen keeps a `drafts` map of ONLY the keys the user has
 * actually touched this session -- it must never be pre-seeded with every
 * key's current value on load, or this module's "only changed keys" job is
 * undermined before it even runs (the screen/controller owns that half;
 * this module owns the other half: a touched key whose draft turns out to
 * equal the server's current value must still be dropped -- e.g. the user
 * typed something then typed it back).
 *
 * [SettingDraft.Reset] mirrors the web port's `{reset: true}`: the user
 * asked to clear an explicit override. Only meaningful, and only ever sent,
 * when the key currently has a `db` override -- clearing a key that already
 * reads from env/default has nothing to clear, so it is silently dropped
 * rather than sent as a no-op `null` PATCH.
 *
 * [SettingDraft.Text]/[SettingDraft.EventsList] mirror the web port's
 * `{value: string | string[]}` union: every key is a plain text field
 * except `webhook_events`, whose checkbox-group UI naturally produces a
 * `List<String>` (ADR-0009: "blank is a valid override value" for
 * `schedule_window` and `webhook_url`, so an empty string is a REAL,
 * intentional disable -- never coerced to a [SettingDraft.Reset]).
 */
sealed class SettingDraft {
    object Reset : SettingDraft()
    data class Text(val value: String) : SettingDraft()
    data class EventsList(val values: List<String>) : SettingDraft()
}

private fun normalizeEventsList(values: List<String>): List<String> =
    values.map { it.trim() }.filter { it.isNotEmpty() }.sorted()

private fun normalizeEventsText(text: String): List<String> = normalizeEventsList(text.split(","))

private fun draftEventsList(draft: SettingDraft): List<String> = when (draft) {
    is SettingDraft.EventsList -> normalizeEventsList(draft.values)
    is SettingDraft.Text -> normalizeEventsText(draft.value)
    is SettingDraft.Reset -> emptyList()
}

private fun draftText(draft: SettingDraft): String = when (draft) {
    is SettingDraft.Text -> draft.value
    is SettingDraft.EventsList -> draft.values.joinToString(",")
    is SettingDraft.Reset -> ""
}

/**
 * **This is the mutation-worthy pin (docs/LEARNINGS.md "Testing
 * discipline"): removing either `valueChanged` call site below, or this
 * function's `entry.source == "db"` guard for [SettingDraft.Reset], makes
 * every touched key appear in the PATCH body regardless of whether it
 * actually differs from the server's current value -- which must kill
 * `SettingsDiffTest`'s "no-op edit is dropped" case.**
 */
private fun valueChanged(key: String, draft: SettingDraft, effective: JsonElement): Boolean {
    if (key == "webhook_events") {
        return draftEventsList(draft) != normalizeEventsList(effective.settingAsStringListOrNull() ?: emptyList())
    }
    val effectiveText = effective.settingAsStringOrNull() ?: ""
    return draftText(draft).trim() != effectiveText.trim()
}

/**
 * @param entries the `settings` array from the last `GET /v1/settings` response.
 * @param drafts one entry per key the user has touched this session.
 * @return a map ready for [dev.steamvault.app.net.VaultApiClient.patchSettings]
 *   -- `null` for a clear, [JsonElement] for a set -- containing ONLY the
 *   keys whose resolved action actually changes something server-side. Empty
 *   when nothing changed.
 */
fun buildSettingsPatchDraft(
    entries: List<SettingInfoOut>,
    drafts: Map<String, SettingDraft>,
): Map<String, JsonElement?> {
    val byKey = entries.associateBy { it.key }
    val body = LinkedHashMap<String, JsonElement?>()

    for ((key, draft) in drafts) {
        val entry = byKey[key] ?: continue
        // Defensive: an env-only or unrecognised key should never reach
        // this module (the screen must not offer an editable control for
        // one), but a stray entry here must never crash a PATCH -- just drop it.
        if (entry.env_only) continue

        if (draft is SettingDraft.Reset) {
            if (entry.source == "db") body[key] = null
            continue // nothing to clear when there is no override active
        }
        if (!valueChanged(key, draft, entry.effective)) continue
        body[key] = if (key == "webhook_events") {
            settingPatchValue(draftEventsList(draft))
        } else {
            settingPatchValue(draftText(draft))
        }
    }

    return body
}

/**
 * Whether the drafts would change anything server-side (WP WEB-FIX-8, twin
 * of web settings.js `isDirty`): the same builder the PATCH uses, so a field
 * edited and typed back to its saved value is NOT dirty and the save bar
 * goes away again. `null` entries (nothing loaded yet) are never dirty.
 */
fun settingsDirty(entries: List<SettingInfoOut>?, drafts: Map<String, SettingDraft>): Boolean =
    entries != null && buildSettingsPatchDraft(entries, drafts).isNotEmpty()
