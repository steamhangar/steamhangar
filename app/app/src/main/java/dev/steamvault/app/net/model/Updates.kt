package dev.steamvault.app.net.model

import kotlinx.serialization.Serializable

/**
 * `POST /v1/updates/check` answer (WP UPD-1,
 * `vault_api/routers/updates.py::UpdateCheckStartOut`). [status] is
 * `started` / `already_running` (both `202`) or `disabled` (`200`, the
 * manifest oracle is off and nothing runs). Kept a plain String, not an
 * enum, so a future status value decodes instead of failing the call;
 * `ui/library/logic/UpdateCheck.kt` decides what an unknown value means.
 */
@Serializable
data class UpdateCheckOut(
    val status: String,
    val enabled: Boolean,
)

/**
 * `GET /v1/updates/status` (WP UPD-1,
 * `vault_api/routers/updates.py::UpdateCheckStatusOut`). Every `last_*`
 * is null before the first run; outcome, finish stamp and counters are
 * also null while [running]. [last_outcome] is `completed` | `bailed_out` |
 * `aborted` | `error` | `interrupted` (a String for the same forward-compat
 * reason as [UpdateCheckOut.status]).
 */
@Serializable
data class UpdateStatusOut(
    val enabled: Boolean,
    /** The oracle is on AND a schedule window is configured. Not read by
     * the app; modelled so the strict-decode fixture matches the server. */
    val scheduled: Boolean = false,
    val running: Boolean,
    val last_trigger: String? = null,
    val last_started_at: String? = null,
    val last_finished_at: String? = null,
    val last_outcome: String? = null,
    val last_apps: Int? = null,
    val last_ok: Int? = null,
    val last_failed: Int? = null,
    val last_stale: Int? = null,
)
