package dev.steamvault.app.net.model

import kotlinx.serialization.Serializable

/** Rows `DELETE /v1/history` removed per traffic table
 * (`vault_api/routers/history.py::TrafficRowsDeletedOut`). */
@Serializable
data class TrafficRowsDeletedOut(
    val client_traffic: Int = 0,
    val client_depot_traffic: Int = 0,
    val server_traffic: Int = 0,
)

/**
 * `DELETE /v1/history?scope=` answer (WP D15-NAMES,
 * `vault_api/routers/history.py::HistoryDeleteOut`). [cutoff] is `null` for
 * `scope=all`. Used by "Delete history" on the Downloads screen
 * (WP ANDROID-GAPS-1).
 */
@Serializable
data class HistoryDeleteOut(
    val scope: String,
    val cutoff: String? = null,
    val jobs_deleted: Int = 0,
    val traffic_rows_deleted: TrafficRowsDeletedOut = TrafficRowsDeletedOut(),
)
