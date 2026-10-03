package dev.steamvault.app.net.model

import kotlinx.serialization.Serializable

/**
 * `GET /v1/clients` row — `vault_api/routers/clients.py::ClientOut`. The
 * WP 3.11/ADR-0008 hit-statistics/bypass fields default to their
 * documented "event feed is off" values (`0`/`null`/`false`,
 * api/README.md "Endpoints") so this decodes fine against a vault-api
 * build old enough not to send them yet.
 */
@Serializable
data class ClientOut(
    val client_id: String,
    val first_seen: String,
    val last_reported_at: String,
    val app_count: Int? = null,
    val source_addrs: List<String> = emptyList(),
    val cache_hits: Int = 0,
    val cache_misses: Int = 0,
    val bytes_served: Long = 0,
    val last_seen_in_cache_log: String? = null,
    val bypass_suspected: Boolean = false,
    /** WP AGENT-FEAT-1 (schema v17): the agent build that sent the LATEST
     * report. `null` = version unknown (an agent from before AGENT-FEAT-1,
     * or a server that does not send the field yet). */
    val agent_version: String? = null,
    /** WP AGENT-FEAT-1: the report interval the latest report stated, in
     * seconds; `null` = not stated (the server then assumes 30 minutes). */
    val report_interval_seconds: Int? = null,
    /** WP AGENT-FEAT-1: `"online"` or `"offline"`, computed by the SERVER
     * per request (`agent_reports.presence`). Passed through verbatim and
     * never recomputed here (APP-FEAT-2): `null` on a server older than
     * AGENT-FEAT-1, which the UI renders as "no presence shown", never as a
     * guess from `last_reported_at`. */
    val presence: String? = null,
    /** WP AGENT-FEAT-1: when the client turns offline without another report. */
    val offline_after: String? = null,
)
