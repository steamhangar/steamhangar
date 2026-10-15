package dev.steamvault.app.ui.settings.logic

import dev.steamvault.app.net.model.CoreApplyOut
import dev.steamvault.app.net.model.SettingInfoOut
import dev.steamvault.app.net.model.settingAsStringListOrNull
import dev.steamvault.app.net.model.settingAsStringOrNull
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull

/** One section of the D16 page (twin of `SECTIONS` in web/js/lib/settings-layout.js). */
data class SettingsSectionDef(val id: String, val title: String, val form: Boolean)

/** An expert sub-group with its keys in display order. */
data class ExpertGroupDef(val id: String, val title: String, val keys: List<String>)

/** Label, the (i) explanation and optional choice list of one key. */
data class FieldMeta(val label: String, val help: String, val options: List<Pair<String, String>>? = null)

/** Result of [SettingsLayout.placeEntries]. */
data class PlacedSettings(
    val sections: Map<String, List<SettingInfoOut>>,
    val expert: Map<String, List<SettingInfoOut>>,
)

/** The vault-core apply line: [warn] marks a rejected change. */
data class CoreApplyLine(val text: String, val warn: Boolean)

/** One line of the risky-confirm dialog. */
data class RiskyItem(val key: String, val label: String, val from: String, val to: String, val risk: String)

/**
 * Pure twin of web/js/lib/settings-layout.js (WP D16, Android half): which
 * section a `GET /v1/settings` row lives in, its wording, the env-wins
 * notes, the risky-confirm lines and vault-core's apply line. No Android or
 * Compose dependency, so it is unit-testable. Wording is NEUTRAL by rule:
 * the upstream rate/connection caps are the vault's upstream (WAN) budget,
 * never "Steam" (docs/handover/rc19-run-plan.md); `web/tests/
 * settings-android-twin.test.js` pins this file's literals against the web
 * module.
 *
 * NOT done yet (see the handover): Compose rendering, the expert switch
 * storage, the risky-confirm dialog and the save wiring (`?confirm_risky`).
 */
object SettingsLayout {
    val SECTIONS: List<SettingsSectionDef> = listOf(
        SettingsSectionDef("vault", "Vault & connection", true),
        SettingsSectionDef("window", "Download window & updates", true),
        SettingsSectionDef("disk", "Disk & cleanup", true),
        SettingsSectionDef("notify", "Notifications", true),
        SettingsSectionDef("other", "Other settings", true),
        SettingsSectionDef("steam", "Steam", false),
        SettingsSectionDef("about", "About", false),
        SettingsSectionDef("expert", "Expert settings", true),
    )

    /** Open by default: the first section only. */
    val DEFAULT_OPEN: Set<String> = setOf("vault")

    private val NORMAL_PLACEMENT: Map<String, String> = linkedMapOf(
        "vault_name" to "vault",
        "schedule_window" to "window",
        "auto_gc" to "disk",
        "event_log_rotate_bytes" to "disk",
        "webhook_url" to "notify",
        "webhook_events" to "notify",
        "webhook_timeout_seconds" to "notify",
        "bypass_window_days" to "notify",
    )

    val EXPERT_GROUPS: List<ExpertGroupDef> = listOf(
        ExpertGroupDef(
            "bandwidth", "Bandwidth & connections",
            listOf(
                "upstream_rate", "upstream_max_conns", "prefill_max_threads",
                "prefill_timeout_seconds", "tls_client_max_conns", "tls_max_conns",
            ),
        ),
        ExpertGroupDef(
            "housekeeping", "Housekeeping",
            listOf(
                "schedule_interval_minutes", "schedule_client_stale_days", "sweep_include_cached",
                "gc_grace_days", "manifest_keep", "agent_report_keep", "client_stats_keep",
                "event_sweep_interval_minutes", "miss_trigger_cooldown_minutes",
                "miss_trigger_max_per_sweep", "manifest_oracle_timeout",
            ),
        ),
        ExpertGroupDef("debug", "Debug", listOf("upstream_edge", "upstream_pool_hosts", "resolver", "log_level")),
    )

    /** Keys the Steam block renders itself. */
    private val HANDLED_ELSEWHERE = setOf("steam_library_steamid")

    // Generated from web/js/lib/settings-layout.js FIELD_META / RISK_TEXT.
    private val FIELDS: Map<String, FieldMeta> = mapOf(
        "vault_name" to FieldMeta("Vault name", "Shown in the side rail and sent as vault_name with every webhook."),
        "schedule_window" to FieldMeta("Download window", "One window for automatic downloads and bandwidth. Inside it, automatic downloads run at full speed. Outside it, no automatic downloads start and the upstream (WAN) rate cap applies. Overnight windows are allowed (22:00-06:00). off (or an empty field) turns the scheduler off; the upstream rate cap then applies around the clock. Times are in the server's time zone. Reset goes back to the default window."),
        "schedule_interval_minutes" to FieldMeta("Sweep interval (minutes)", "Minutes between two scheduled sweeps inside the download window."),
        "schedule_client_stale_days" to FieldMeta("Client staleness (days)", "Installed-games reports older than this are ignored by the sweep."),
        "sweep_include_cached" to FieldMeta("Include cached games in the sweep", "When on, the sweep also refreshes every game that already has content on disk, not only games a PC agent reports as installed.", listOf("false" to "Off", "true" to "On")),
        "auto_gc" to FieldMeta("Auto-GC after a prefill", "Garbage collection after each prefill. Dry run only reports what it would delete; Execute deletes chunks no game references any more, once the GC grace period has passed.", listOf("off" to "Off", "dry-run" to "Dry run", "execute" to "Execute")),
        "gc_grace_days" to FieldMeta("GC grace period (days)", "Unreferenced chunks stay protected this many days before GC may delete them."),
        "manifest_keep" to FieldMeta("Manifests kept per depot", "Archived manifests kept per depot."),
        "agent_report_keep" to FieldMeta("Reports kept per PC", "Installed-games snapshots kept per agent."),
        "client_stats_keep" to FieldMeta("Client statistics rows kept", "Statistics rows kept per client."),
        "event_sweep_interval_minutes" to FieldMeta("Event-log sweep interval (minutes)", "How often vault-api reads vault-core's event log (cache misses, client traffic)."),
        "miss_trigger_cooldown_minutes" to FieldMeta("Cache-miss trigger cooldown (minutes)", "After a cache miss queued a prefill for a game, wait this long before the next one for that game. 0 turns the trigger off."),
        "miss_trigger_max_per_sweep" to FieldMeta("Cache-miss prefills per sweep", "At most this many prefill jobs one event-log sweep may queue from cache misses."),
        "bypass_window_days" to FieldMeta("Bypass warning after (days)", "A PC with no traffic through the cache for this many days is flagged as \"bypass suspected\"."),
        "prefill_max_threads" to FieldMeta("Prefill threads per job", "SteamPrefill download threads per job. Must not exceed the upstream connection cap."),
        "prefill_timeout_seconds" to FieldMeta("Prefill timeout (seconds)", "Time limit for one prefill job."),
        "manifest_oracle_timeout" to FieldMeta("Update-check timeout (seconds)", "Seconds per request to the manifest oracle, the service the update check asks."),
        "webhook_url" to FieldMeta("Webhook URL", "Set only in the environment (VAULT_WEBHOOK_URL). Shown masked, because the URL can carry credentials."),
        "webhook_events" to FieldMeta("Webhook events", "Which events the webhook delivers.", listOf("job.done" to "Job finished", "job.error" to "Job failed", "job.cancelled" to "Job cancelled", "client.bypass_suspected" to "Cache bypass suspected", "client.bypass_resolved" to "Cache bypass resolved")),
        "webhook_timeout_seconds" to FieldMeta("Webhook timeout (seconds)", "Seconds per webhook delivery attempt."),
        "upstream_rate" to FieldMeta("Upstream (WAN) rate cap", "The vault's upstream (WAN) budget: total bytes per second vault-core fetches from the internet for cache misses outside the download window. k and m are nginx units (k = 1024). off = no cap."),
        "upstream_max_conns" to FieldMeta("Upstream (WAN) connection cap", "The vault's upstream (WAN) budget: connections from vault-core to the internet at the same time. At least the prefill threads."),
        "tls_client_max_conns" to FieldMeta("HTTPS sessions per client", "Passed-through HTTPS sessions per client address. At most the total."),
        "tls_max_conns" to FieldMeta("HTTPS sessions in total", "Passed-through HTTPS sessions for all clients together."),
        "event_log_rotate_bytes" to FieldMeta("Event-log rotation size (bytes)", "vault-core starts a new event log at this size. 0 turns rotation off."),
        "upstream_edge" to FieldMeta("Steam edge host", "The one Steam server vault-core keeps connections open to. off = the older per-name pool (rollback switch)."),
        "upstream_pool_hosts" to FieldMeta("Legacy pool hosts", "Up to four Steam hosts for the older per-name pool. Used only while the edge host is off."),
        "resolver" to FieldMeta("DNS resolver for vault-core", "DNS server vault-core uses to find upstream servers. Never one that answers their names with a LAN address, such as this vault's own DNS."),
        "log_level" to FieldMeta("Log level", "How much vault-api and vault-runner write to their logs.", listOf("DEBUG" to "Debug", "INFO" to "Info", "WARNING" to "Warning", "ERROR" to "Error", "CRITICAL" to "Critical")),
    )

    private val RISK: Map<String, String> = mapOf(
        "prefill_max_threads" to "Too many parallel connections can use up the port quota of a CGNAT internet line and stop every download.",
        "upstream_max_conns" to "Too many parallel connections can use up the port quota of a CGNAT internet line and stop every download.",
        "tls_client_max_conns" to "Too many parallel connections can use up the port quota of a CGNAT internet line and stop every download.",
        "tls_max_conns" to "Too many parallel connections can use up the port quota of a CGNAT internet line and stop every download.",
        "upstream_edge" to "A wrong edge host makes every cache miss fail, so downloads stop until it is fixed.",
        "upstream_pool_hosts" to "Wrong hosts make cache misses fail while the older per-name pool is in use.",
        "resolver" to "A resolver that answers upstream names with a LAN address makes vault-core fetch from itself, and downloads fail.",
    )
    const val RISK_FALLBACK = "This debug setting can break downloads."


    fun fieldMeta(key: String): FieldMeta = FIELDS[key] ?: FieldMeta(key, "")

    fun riskText(key: String): String = RISK[key] ?: RISK_FALLBACK

    /** Twin of `placeEntries`: normal rows by key, expert rows by group, unknown keys never lost. */
    fun placeEntries(entries: List<SettingInfoOut>): PlacedSettings {
        val sections = LinkedHashMap<String, MutableList<SettingInfoOut>>()
        val expert = LinkedHashMap<String, MutableList<SettingInfoOut>>()
        for (s in SECTIONS) sections[s.id] = mutableListOf()
        for (g in EXPERT_GROUPS) expert[g.id] = mutableListOf()
        val order = HashMap<String, Int>()
        NORMAL_PLACEMENT.keys.forEachIndexed { i, k -> order[k] = i }
        for (g in EXPERT_GROUPS) g.keys.forEachIndexed { i, k -> order[k] = i }
        for (entry in entries) {
            if (entry.key in HANDLED_ELSEWHERE) continue
            val named = NORMAL_PLACEMENT[entry.key]
            if (entry.env_only) {
                if (named != null) sections.getValue(named).add(entry)
                continue
            }
            if (entry.expert) {
                val group = EXPERT_GROUPS.firstOrNull { entry.key in it.keys }
                expert.getValue(group?.id ?: "housekeeping").add(entry)
                continue
            }
            sections.getValue(named ?: "other").add(entry)
        }
        val rank = { e: SettingInfoOut -> order[e.key] ?: 1000 }
        for (list in sections.values + expert.values) list.sortBy(rank)
        return PlacedSettings(sections, expert)
    }

    /** Env always wins (Q1) and the webhook URL is env-only (Q2): shown as plain text. */
    fun isPinned(entry: SettingInfoOut): Boolean = entry.env_only || !entry.editable

    /** A vault-core key this build cannot hand to vault-core (`reason: core_file_disabled`). */
    fun isCoreFileDisabled(entry: SettingInfoOut): Boolean =
        if (entry.reason != null) entry.reason == "core_file_disabled"
        else !entry.editable && !entry.env_only && entry.source != "env" && entry.target == "core"

    /** The note under a plain-text row. */
    fun envNote(entry: SettingInfoOut): String {
        if (isCoreFileDisabled(entry)) {
            val name = if (entry.env_var.isNotEmpty()) " (${entry.env_var})" else ""
            return "Applies to vault-core only via env in this build$name. Shown: the built-in default; " +
                "vault-core's own environment may set another value."
        }
        return if (entry.env_var.isNotEmpty()) "Set via env (${entry.env_var})" else "Set via env"
    }

    /** The note for a stored value that env overrides, or null. */
    fun shadowNote(entry: SettingInfoOut): String? {
        if (!entry.shadowed) return null
        return if (entry.env_only) {
            "A value stored in Settings is ignored: this setting comes only from the environment."
        } else {
            "A value stored in Settings is overridden by env."
        }
    }

    /** The (i) paragraphs: explanation, accepted format, the vault-core env caveat. */
    fun fieldHelp(entry: SettingInfoOut): List<String> {
        val out = ArrayList<String>()
        val meta = fieldMeta(entry.key)
        if (meta.help.isNotEmpty()) out.add(meta.help)
        if (entry.grammar.isNotEmpty()) out.add("Format: ${entry.grammar}.")
        if (!entry.env_visible && !entry.env_only) {
            val v = entry.env_var.ifEmpty { "the variable" }
            out.add("vault-api cannot see vault-core's environment: if $v is set there, that value wins over this one.")
        }
        return out
    }

    /** A value as plain text (pinned rows, the confirm dialog). */
    fun displayValue(entry: SettingInfoOut, value: JsonElement = entry.effective): String {
        val list = value.settingAsStringListOrNull()
        val options = FIELDS[entry.key]?.options
        if (list != null) {
            if (list.isEmpty()) return "None"
            return list.joinToString(", ") { v -> options?.firstOrNull { it.first == v }?.second ?: v }
        }
        val text = value.settingAsStringOrNull()
        if (value is JsonNull || text == null || text.isEmpty()) {
            return if (entry.key == "webhook_url") "Not set (webhooks off)" else "Not set"
        }
        return options?.firstOrNull { it.first == text }?.second ?: text
    }

    /** Risky keys a PATCH body sets or clears (both need confirm). */
    fun riskyKeysInBody(entries: List<SettingInfoOut>, body: Map<String, JsonElement?>): List<String> {
        val byKey = entries.associateBy { it.key }
        return body.keys.filter { byKey[it]?.risky == true }
    }

    /** Dialog lines: label, old to new, what can break. `null` = back to the default. */
    fun riskyItems(entries: List<SettingInfoOut>, body: Map<String, JsonElement?>, keys: List<String>): List<RiskyItem> {
        val byKey = entries.associateBy { it.key }
        return keys.map { key ->
            val entry = byKey[key]
            val next = body[key]
            val from = if (entry != null) displayValue(entry) else "Not set"
            val to = when {
                entry == null -> displayValue(SettingInfoOut(key = key, source = "", applies = ""), next ?: JsonNull)
                next == null -> "${displayValue(entry, entry.fallback)} (default)"
                else -> displayValue(entry, next)
            }
            RiskyItem(key, fieldMeta(key).label, from, to, riskText(key))
        }
    }

    /** vault-core's apply state as one line, or null before it has reported. State only, never a guess. */
    fun coreApplyLine(core: CoreApplyOut?): CoreApplyLine? {
        if (core == null) return null
        if (core.pending) return CoreApplyLine("vault-core: a change is waiting to be applied.", false)
        return when (core.state) {
            "rejected" -> {
                val why = if (core.reason.isNotEmpty()) ": ${core.reason}" else "."
                CoreApplyLine("vault-core rejected the last settings change$why It keeps running with the settings it had.", true)
            }
            "applied" -> CoreApplyLine("vault-core: settings applied.", false)
            "none" -> CoreApplyLine(
                "vault-core has not seen a settings file yet; it runs on its environment and defaults.", false,
            )
            else -> null
        }
    }

    /** The save bar's line for a failed PATCH (409/422/428/403 are answered before anything is written). */
    fun describeSaveError(status: Int?, detail: String?, message: String?, confirmed: Boolean = false): String = when (status) {
        409 -> "Not saved: ${detail ?: "a value is set via env, and env always wins."} Nothing was saved."
        422 -> "Not saved: ${detail ?: "the server refused a value."} Nothing was saved."
        428 -> if (confirmed) {
            "Not saved: the server still asked for confirmation of settings that can break downloads. Nothing was saved."
        } else {
            "Not saved: the change includes settings that can break downloads and was not confirmed. Nothing was saved."
        }
        403 -> "Not saved: ${detail ?: "the settings are read-only."}"
        else -> "Could not save: ${detail ?: message ?: "Request failed."}"
    }
}
