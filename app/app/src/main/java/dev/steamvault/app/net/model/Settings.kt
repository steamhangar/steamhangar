package dev.steamvault.app.net.model

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull

/**
 * One row of `GET /v1/settings` — `vault_api/routers/settings.py::SettingInfoOut`
 * (ADR-0009).
 *
 * `effective`/`fallback` are HETEROGENEOUS on the wire by design: a string
 * for `vault_name`/`schedule_window`/`auto_gc`/`webhook_url`, an int for
 * `schedule_interval_minutes`/`schedule_client_stale_days`, a JSON array of
 * strings for `webhook_events`, or `null` (a cleared `schedule_window`/
 * `webhook_url` override, or an env-only key with no configured value —
 * see `vault_api/settings_store.py::OVERRIDABLE_SPECS`'s `to_json`
 * functions for the exact per-key shape). kotlinx.serialization's
 * [JsonElement] is the honest Kotlin type for that: a single scalar type
 * would either reject half the keys or silently coerce them. The
 * `settingAsXxx` helpers below give typed access without every call site
 * re-deriving the same `when`.
 */
@Serializable
data class SettingInfoOut(
    val key: String,
    val effective: JsonElement = JsonNull,
    val source: String,
    val fallback: JsonElement = JsonNull,
    val applies: String,
    val env_only: Boolean = false,
    // D16 wire fields (GET /v1/settings since rc18). All defaulted so an
    // older server, and every existing fixture, still parses.
    val env_var: String = "",
    val value: JsonElement = JsonNull,
    val editable: Boolean = true,
    val risky: Boolean = false,
    val expert: Boolean = false,
    val grammar: String = "",
    val target: String = "",
    val env_visible: Boolean = true,
    val shadowed: Boolean = false,
    val reason: String? = null,
)

/** D16c: vault-core's report on the core settings (`CoreApplyOut` in the API). */
@Serializable
data class CoreApplyOut(
    val state: String = "",
    val generation: Int = 0,
    val applied_generation: Int = 0,
    val reason: String = "",
    val updated_at: String = "",
    val written_generation: Int? = null,
    val pending: Boolean = false,
)

/** `GET`/`PATCH /v1/settings` — `vault_api/routers/settings.py::SettingsOut`. */
@Serializable
data class SettingsOut(
    val readonly: Boolean,
    val settings: List<SettingInfoOut> = emptyList(),
    val core_apply: CoreApplyOut? = null,
)

/** Pure: a settings [JsonElement] as a `String`, or `null` for JSON null / a non-scalar. */
fun JsonElement.settingAsStringOrNull(): String? {
    if (this is JsonNull) return null
    return (this as? JsonPrimitive)?.contentOrNull
}

/** Pure: a settings [JsonElement] as an `Int`, or `null` for JSON null / not-a-number. */
fun JsonElement.settingAsIntOrNull(): Int? {
    if (this is JsonNull) return null
    return (this as? JsonPrimitive)?.intOrNull
}

/** Pure: a settings [JsonElement] as a `Boolean`, or `null` for JSON null / not-a-boolean. */
fun JsonElement.settingAsBooleanOrNull(): Boolean? {
    if (this is JsonNull) return null
    return (this as? JsonPrimitive)?.booleanOrNull
}

/**
 * Pure: a settings [JsonElement] as a `List<String>` (`webhook_events`'
 * shape), or `null` for JSON null / not-an-array / an array with a
 * non-string element.
 */
fun JsonElement.settingAsStringListOrNull(): List<String>? {
    if (this is JsonNull) return null
    val array = this as? JsonArray ?: return null
    val result = ArrayList<String>(array.size)
    for (item in array) {
        // `contentOrNull` alone is not enough: a NUMERIC JsonPrimitive
        // (e.g. JsonPrimitive(1)) still has non-null text content ("1"),
        // so a mixed array would silently pass through as strings without
        // this explicit `isString` check.
        val primitive = item as? JsonPrimitive ?: return null
        if (item is JsonNull || !primitive.isString) return null
        result.add(primitive.content)
    }
    return result
}

/** Wrap a plain string as the [JsonElement] a `PATCH /v1/settings` body expects. */
fun settingPatchValue(value: String): JsonElement = JsonPrimitive(value)

/**
 * Wrap a list of strings (`webhook_events` only) as the [JsonElement] a
 * `PATCH /v1/settings` body expects — `settings.py::_coerce_patch_value`
 * joins this with commas server-side before applying the same startup
 * grammar every other key reuses.
 */
fun settingPatchValue(values: List<String>): JsonElement = JsonArray(values.map { JsonPrimitive(it) })

/**
 * Build a `PATCH /v1/settings` body: one entry per key to change, a value
 * from [settingPatchValue] to set/replace the override, or `null` to clear
 * it back to the env/default (ADR-0009 decision 2). The whole map is sent
 * as one request — `settings.py` validates every key before persisting
 * any of them, so a partial-success outcome is not a shape this client
 * needs to model.
 */
fun buildSettingsPatch(updates: Map<String, JsonElement?>): JsonObject =
    buildJsonObject {
        for ((key, value) in updates) {
            put(key, value ?: JsonNull)
        }
    }
