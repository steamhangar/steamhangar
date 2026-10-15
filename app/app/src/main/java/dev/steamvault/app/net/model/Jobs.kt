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
    val log_excerpt: String? = null,
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
