package dev.steamvault.app.net.model

import kotlinx.serialization.Serializable

/**
 * `GET /v1/games` row — mirrors `vault_api/routers/games.py::GameSummary`
 * field for field (api/README.md "Endpoints"). Field names are kept
 * VERBATIM (snake_case, matching the wire JSON) rather than renamed to
 * Kotlin camelCase — the same no-renaming-layer decision web/js/api.js
 * documents for the web client, so a payload can be compared against the
 * README table / the Pydantic model source without a mental mapping step.
 *
 * `appid`/`status`/`depot_count` have no sane default (a response missing
 * one of these is a real contract break, not a forward-compat gap) and are
 * therefore required; every field the apps table's schema history added
 * later (`last_manifest_check` schema v4, `needs_force` schema v5) is
 * nullable/defaulted so an older vault-api — or a future field this
 * client stops reading — still decodes (see VaultJson's kdoc).
 */
@Serializable
data class GameSummary(
    val appid: Int,
    val name: String? = null,
    val status: String,
    val last_prefill_at: String? = null,
    val last_manifest_check: String? = null,
    val depot_count: Int,
    val size_bytes: Long? = null,
    val needs_force: Boolean = false,
    val installed_on: List<InstalledOnEntry> = emptyList(),
    /** WP API-FIX-4: a Steam tool package vault-api never prefills
     * (`api/vault_api/tool_apps.py`, 228980). Defaulted: older servers send
     * no flag. */
    val tool_app: Boolean = false,
    /** WP API-FIX-4: the tool package's display name, null for every
     * ordinary app. */
    val tool_app_name: String? = null,
    /** WP API-FIX-5: the real portrait cover from Steam's store item lookup
     * (`api/vault_api/cover_art.py`), null while unknown. Defaulted: older
     * servers send none. Checked again by `serverCoverUrl` before use. */
    val cover_url: String? = null,
    /** WP D10 COVER-CACHE-1: the same cover as stored by vault-api, sent as
     * the relative path `/v1/covers/{appid}/{signature}` (the signature is
     * the credential, no X-Api-Key needed). [dev.steamvault.app.net.VaultApiClient]
     * turns a valid path into an absolute URL on the vault's base URL and
     * drops anything else, so after the client this is either that absolute
     * URL or null. Defaulted: older servers send none. */
    val cover_cached_url: String? = null,
    /** WP UPD-1: the manifest oracle says a newer build exists than the one
     * cached (only ever true for a game with cache bytes). Defaulted: a
     * server from before UPD-1 sends no flag, which reads as "no update
     * known", never as an error. */
    val update_available: Boolean = false,
    /** WP UPD-1: when the oracle last answered for this game, null while
     * never checked (or on a server from before UPD-1). */
    val update_checked_at: String? = null,
    /** WP TOOL-APPS-2a (ADR-0022): the TOP-LEVEL game this app is a content
     * package of, resolved by the server (one level). Null when ungrouped --
     * and on a server from before TOOL-APPS-2a, which sends no field: a
     * missing value means "ungrouped", so the flat list renders as before. */
    val parent_appid: Int? = null,
    /** WP TOOL-APPS-2a: "game", "dlc", "tool", "application", "config",
     * "music", "video", "demo" or null (unknown, or an older server).
     * Display data only. */
    val app_type: String? = null,
    /** WP TOOL-APPS-2 (ADR-0022 §2): on a top-level row, the bytes of the
     * whole group it heads (its own depots plus those of every row whose
     * [parent_appid] is this app, each depot counted once); equals
     * [size_bytes] for an ungrouped row, null on a grouped row and on an
     * older server. */
    val group_size_bytes: Long? = null,
)

/** One entry of [GameDetail.depots] — `vault_api/routers/games.py::DepotEntry`. */
@Serializable
data class DepotEntry(
    val depotid: Int,
    val shared: Boolean,
    val size_bytes: Long? = null,
)

/**
 * One entry of [GameSummary.installed_on] / [GameDetail.installed_on] —
 * `vault_api/routers/games.py` (WP AG-1). ALREADY filtered server-side to
 * fresh agent reports only (the same staleness gate the scheduler itself
 * uses before trusting a report for sweep targeting, `api/README.md`
 * "Installed state per app"): an empty `installed_on` list means "no fresh
 * signal" — it covers BOTH "never installed anywhere" AND "installed, but
 * the reporting agent has gone quiet" indistinguishably. Never render `[]`
 * as "not installed anywhere".
 */
@Serializable
data class InstalledOnEntry(
    val client_id: String,
    val reported_at: String,
)

/** `GET /v1/games/{appid}` — `vault_api/routers/games.py::GameDetail`. */
@Serializable
data class GameDetail(
    val appid: Int,
    val name: String? = null,
    val status: String,
    val last_prefill_at: String? = null,
    val last_manifest_check: String? = null,
    val depots: List<DepotEntry> = emptyList(),
    val size_bytes: Long? = null,
    val needs_force: Boolean = false,
    val installed_on: List<InstalledOnEntry> = emptyList(),
    /** See [GameSummary.tool_app] (WP API-FIX-4). */
    val tool_app: Boolean = false,
    /** See [GameSummary.tool_app_name] (WP API-FIX-4). */
    val tool_app_name: String? = null,
    /** See [GameSummary.cover_url] (WP API-FIX-5). */
    val cover_url: String? = null,
    /** See [GameSummary.cover_cached_url] (WP D10). */
    val cover_cached_url: String? = null,
    /** See [GameSummary.parent_appid] (WP TOOL-APPS-2a). */
    val parent_appid: Int? = null,
    /** See [GameSummary.app_type] (WP TOOL-APPS-2a). */
    val app_type: String? = null,
    /** See [GameSummary.group_size_bytes] (WP TOOL-APPS-2). */
    val group_size_bytes: Long? = null,
)
