package dev.steamvault.app.ui.settings.logic

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.SettingsOut
import dev.steamvault.app.net.steam.SteamId64
import dev.steamvault.app.ui.library.logic.STEAM_LIBRARY_SETTING_KEY
import kotlinx.serialization.json.JsonElement

/**
 * Settings' "Steam library" block (WP APP-FEAT-1, Android parity with web
 * WP WEB-FEAT-1/2's `buildSteamLibraryBlock` + `owned-library.js`'s
 * `saveLibrarySteamId`/`describeLookupError`): the vault's one library
 * SteamID64 (`steam_library_steamid`, WP API-FEAT-1) with Save, Reset and
 * Preview.
 *
 * Pure decisions only; `SettingsController` runs them and
 * `SettingsScreen.kt` words them from `strings.xml`.
 */
sealed class LibrarySteamIdSavePlan {
    /** Older vault-api without the setting: nothing is sent. */
    data object Absent : LibrarySteamIdSavePlan()

    /** The setting is env-only on this server: nothing is sent. */
    data object EnvOnly : LibrarySteamIdSavePlan()

    /** The vault's settings are read-only: nothing is sent. */
    data object Readonly : LibrarySteamIdSavePlan()

    /** The typed text is neither blank nor a valid SteamID64: nothing is sent. */
    data object Invalid : LibrarySteamIdSavePlan()

    /** The stored value already equals the typed one: nothing is sent. */
    data object Unchanged : LibrarySteamIdSavePlan()

    /** Send this `PATCH /v1/settings` body. [cleared] = the typed text was
     * blank, i.e. an explicit "" override ("not set"). */
    data class Patch(val body: Map<String, JsonElement?>, val cleared: Boolean) : LibrarySteamIdSavePlan()
}

/**
 * What Save does with [typed] (web `saveLibrarySteamId`'s branch order).
 * The body comes from the same builder as the Settings form
 * ([buildSettingsPatchDraft]), so only a real change is sent and the value
 * is the trimmed STRING, never a number (17 digits exceed JavaScript's safe
 * integers; the API answers a number with 422). Blank = explicit "" override.
 */
fun planLibrarySteamIdSave(response: SettingsOut?, typed: String): LibrarySteamIdSavePlan {
    if (response == null) return LibrarySteamIdSavePlan.Absent
    val entries = response.settings
    val entry = entries.firstOrNull { it.key == STEAM_LIBRARY_SETTING_KEY } ?: return LibrarySteamIdSavePlan.Absent
    if (entry.env_only) return LibrarySteamIdSavePlan.EnvOnly
    if (response.readonly) return LibrarySteamIdSavePlan.Readonly
    val value = typed.trim()
    if (value.isNotEmpty() && SteamId64.validate(value) == null) return LibrarySteamIdSavePlan.Invalid
    val body = buildSettingsPatchDraft(entries, mapOf(STEAM_LIBRARY_SETTING_KEY to SettingDraft.Text(value)))
    if (body.isEmpty()) return LibrarySteamIdSavePlan.Unchanged
    return LibrarySteamIdSavePlan.Patch(body, cleared = value.isEmpty())
}

/** Reset's body: `null` for the key (delete the override row: back to the
 * env value or the blank default), or empty when there is no `db`
 * override to clear. Blank + Save stays the explicit "" override. */
fun libraryIdResetBody(response: SettingsOut?): Map<String, JsonElement?> =
    buildSettingsPatchDraft(response?.settings.orEmpty(), mapOf(STEAM_LIBRARY_SETTING_KEY to SettingDraft.Reset))

/** Whether the block offers Save at all (web: hidden when read-only or the
 * setting is absent; env-only keys cannot be saved either). */
fun canSaveLibrarySteamId(response: SettingsOut?): Boolean {
    if (response == null) return false
    val entry = response.settings.firstOrNull { it.key == STEAM_LIBRARY_SETTING_KEY } ?: return false
    return !response.readonly && !entry.env_only
}

/** Whether Reset shows: same rule as every other setting's Reset
 * ([canResetSetting]): only a `db` override has anything to clear. */
fun canResetLibrarySteamId(response: SettingsOut?): Boolean {
    if (response == null) return false
    val entry = response.settings.firstOrNull { it.key == STEAM_LIBRARY_SETTING_KEY } ?: return false
    return !response.readonly && canResetSetting(entry)
}

/** A failed Preview of a TYPED SteamID64 (web `describeLookupError`): a
 * `422` is about the typed id, `409` is the shared no-key case, anything
 * else is the server's own detail. */
sealed class LibraryLookupError {
    data object NoRelayKey : LibraryLookupError()
    data object InvalidSteamId : LibraryLookupError()
    data class Failed(val detail: String) : LibraryLookupError()
}

fun describeLookupError(error: Throwable): LibraryLookupError {
    val status = (error as? VaultApiError)?.status
    return when (status) {
        409 -> LibraryLookupError.NoRelayKey
        422 -> LibraryLookupError.InvalidSteamId
        else -> LibraryLookupError.Failed((error as? VaultApiError)?.detail ?: error.message ?: "Request failed.")
    }
}

/** Preview's result (web `state.lookup`): the relay's `game_count`, the
 * first [PREVIEW_LIMIT] names, and the persona when the player-summaries
 * call answered for exactly this id. */
data class LibraryPreview(
    val gameCount: Int,
    val previewNames: List<String>,
    val personaName: String?,
)

const val PREVIEW_LIMIT = 8
