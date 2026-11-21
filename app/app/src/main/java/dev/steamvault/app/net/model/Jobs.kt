package dev.steamvault.app.net.model

import kotlinx.serialization.Serializable

/** Body of `POST /v1/prefill` — `vault_api/routers/jobs.py::PrefillRequest`. */
@Serializable
data class PrefillRequest(
    val appids: List<Int>,
)

/** One entry of `POST /v1/prefill`'s `202` response — `PrefillJobRef`. */
@Serializable
data class PrefillJobRef(
    val appid: Int,
    val job_id: Int,
    val status: String,
    val deduplicated: Boolean,
)

/**
 * `GET /v1/jobs` row — `vault_api/routers/jobs.py::JobSummary`. Omits
 * `log_excerpt` on purpose, matching the server (this is the small polling
 * list shape, not the detail shape — api/README.md "Endpoints").
 */
@Serializable
data class JobSummary(
    val id: Int,
    val appid: Int,
    val type: String,
    val status: String,
    val created_at: String,
    val started_at: String? = null,
    val finished_at: String? = null,
    val updated: Int? = null,
    val up_to_date: Int? = null,
    val summary_parse_ok: Boolean? = null,
    val gc_execute: Boolean? = null,
    val paused_at: String? = null,
    val stop_request: String? = null,
    /** WP D20: 0-based place among the QUEUED jobs (0 = runs next); null
     * unless the job is queued and for an older API that sends none. */
    val queue_position: Int? = null,
    /** Schema v34 (WP CAP-503-FIX-1a): `"client"` = vault-api paused the job
     * for a device loading through the cache (resumed by itself), `"manual"`
     * = the operator's pause; null while the job is neither paused nor
     * pausing, and from an older API (reads as a manual pause there). */
    val pause_reason: String? = null,
    /** Schema v34: the operator resumed this job during a client hold, so it
     * runs alongside the device (operator decision 2026-11-21, Weg B). */
    val client_override: Boolean = false,
)

/**
 * `GET /v1/jobs/{id}` — `vault_api/routers/jobs.py::JobDetail`, which the
 * server implements as `JobSummary` plus `log_excerpt` via Pydantic
 * subclassing. kotlinx.serialization data classes don't extend each other
 * that way, so the fields are repeated here rather than nesting a
 * `JobSummary` object inside — the WIRE shape is flat either way, and a
 * nested Kotlin shape would misrepresent it.
 */
@Serializable
data class JobDetail(
    val id: Int,
    val appid: Int,
    val type: String,
    val status: String,
    val created_at: String,
    val started_at: String? = null,
    val finished_at: String? = null,
    val updated: Int? = null,
    val up_to_date: Int? = null,
    val summary_parse_ok: Boolean? = null,
    val gc_execute: Boolean? = null,
    val paused_at: String? = null,
    val stop_request: String? = null,
    val queue_position: Int? = null,
    val pause_reason: String? = null,
    val client_override: Boolean = false,
    val log_excerpt: String? = null,
)

/** One device loading through the cache — `vault_api/routers/jobs.py::ClientLoadOut`. */
@Serializable
data class ClientLoadOut(
    val addr: String,
    /** The name set for this address on the Clients page, if any. */
    val name: String? = null,
    val miss_bytes_per_second: Double = 0.0,
)

/**
 * `GET /v1/client-priority` (WP CAP-503-FIX-1a) —
 * `vault_api/routers/jobs.py::ClientPriorityOut`: whether the prefill runner
 * is held for a device loading through the cache, for whom ([clients], the
 * last sighting) and until when ([resume_at], the last sighting plus the
 * 120 s idle gap). [restored] = held again after a vault-api restart with no
 * device seen since ([clients] empty, [last_client_at] is the restart).
 * Read by the Downloads screen (WP CAP-503-FIX-1b); an older server answers
 * `404` and the screen shows no hold texts.
 */
@Serializable
data class ClientPriorityOut(
    val enabled: Boolean = false,
    val holding: Boolean = false,
    val since: String? = null,
    val last_client_at: String? = null,
    val resume_at: String? = null,
    val idle_gap_seconds: Int = 120,
    val clients: List<ClientLoadOut> = emptyList(),
    val paused_job_ids: List<Int> = emptyList(),
    val override_job_ids: List<Int> = emptyList(),
    val restored: Boolean = false,
    val runner_address_unknown: Boolean = false,
)

/**
 * Response of `DELETE /v1/jobs/{id}`, `POST /v1/jobs/{id}/pause` and
 * `POST /v1/jobs/{id}/resume` — `vault_api/routers/jobs.py::JobControlOut`.
 * `detail` is non-nullable on the wire (Pydantic `str`, not `str | None`)
 * — every code path that builds this response always sets one.
 */
@Serializable
data class JobControlOut(
    val job_id: Int,
    val status: String,
    val outcome: String,
    val detail: String,
)

/** Body of `POST /v1/jobs/{id}/move` (WP D20): `position` counts queued jobs only. */
@Serializable
data class MoveJobRequest(
    val position: Int,
)

/**
 * Response of `POST /v1/jobs/{id}/move` (WP D20): the [JobControlOut] shape
 * plus `queue_position`, the place the server gave the job.
 */
@Serializable
data class JobMoveOut(
    val job_id: Int,
    val status: String,
    val outcome: String,
    val detail: String,
    val queue_position: Int? = null,
)
