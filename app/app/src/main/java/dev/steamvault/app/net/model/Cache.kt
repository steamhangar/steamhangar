package dev.steamvault.app.net.model

import kotlinx.serialization.Serializable

/** One row of `CacheSummaryOut.top_consumers` — `vault_api/routers/cache.py::TopConsumerOut`. */
@Serializable
data class TopConsumerOut(
    val appid: Int,
    val name: String? = null,
    val size_bytes: Long,
)

/** `CacheSummaryOut.unmapped_depots` — `vault_api/routers/cache.py::UnmappedDepotsOut`. */
@Serializable
data class UnmappedDepotsOut(
    val count: Int,
    val size_bytes: Long,
)

/** `GET /v1/cache/summary` — `vault_api/routers/cache.py::CacheSummaryOut`. */
@Serializable
data class CacheSummaryOut(
    val total_bytes: Long,
    val top_consumers: List<TopConsumerOut> = emptyList(),
    val unmapped_depots: UnmappedDepotsOut,
    val free_disk_bytes: Long? = null,
)

/** One row of `CacheDeletionOut.deleted_depots` — `DeletedDepotOut`. */
@Serializable
data class DeletedDepotOut(
    val depotid: Int,
    val size_bytes_freed: Long,
    val shared_with_uncached: List<Int> = emptyList(),
)

/** One row of `CacheDeletionOut.skipped_shared` — `SkippedSharedDepotOut`. */
@Serializable
data class SkippedSharedDepotOut(
    val depotid: Int,
    val shared_with: List<Int> = emptyList(),
)

/** One row of `CacheDeletionOut.failed` — `FailedDepotOut`. */
@Serializable
data class FailedDepotOut(
    val depotid: Int,
    val error: String,
)

/** `DELETE /v1/cache/{appid}` — `vault_api/routers/cache.py::CacheDeletionOut`.
 * The last three fields are present only on a grouped delete
 * (`?with_packages=true`, WP TOOL-APPS-2b); a plain delete omits them. */
@Serializable
data class CacheDeletionOut(
    val appid: Int,
    val deleted_depots: List<DeletedDepotOut> = emptyList(),
    val skipped_shared: List<SkippedSharedDepotOut> = emptyList(),
    val failed: List<FailedDepotOut> = emptyList(),
    val total_bytes_freed: Long,
    val with_packages: Boolean = false,
    val members: List<DeleteMemberOut> = emptyList(),
    val kept_children: List<KeptChildOut> = emptyList(),
)

/** One app a grouped delete removed — `DeleteMemberOut` (WP TOOL-APPS-2b). */
@Serializable
data class DeleteMemberOut(
    val appid: Int,
    /** "parent" or "package". */
    val role: String,
    val name: String? = null,
    val app_type: String? = null,
    val deleted_depots: List<Int> = emptyList(),
    val failed_depots: List<Int> = emptyList(),
)

/** A child of the game that a grouped delete keeps — `KeptChildOut`.
 * [reason] is "not_a_package_type", "type_only_inferred" or "type_unknown". */
@Serializable
data class KeptChildOut(
    val appid: Int,
    val name: String? = null,
    val app_type: String? = null,
    val reason: String,
)

/** One member of a delete plan — `PlanMemberOut` (WP TOOL-APPS-2b). */
@Serializable
data class PlanMemberOut(
    val appid: Int,
    val role: String,
    val name: String? = null,
    val app_type: String? = null,
    val depot_ids: List<Int> = emptyList(),
)

/** One depot a delete plan removes — `PlannedDepotOut`. */
@Serializable
data class PlannedDepotOut(
    val depotid: Int,
    val size_bytes: Long? = null,
    val owners: List<Int> = emptyList(),
    val shared_with_uncached: List<Int> = emptyList(),
)

/** An in-flight job of a member: the delete would answer 409 — `BlockingJobOut`. */
@Serializable
data class BlockingJobOut(
    val appid: Int,
    val job_id: Int,
    val type: String,
    val status: String,
)

/**
 * `GET /v1/cache/{appid}/delete-plan[?with_packages=true]` —
 * `vault_api/routers/cache.py::DeletePlanOut` (WP TOOL-APPS-2b). Read-only
 * preview for the confirmation dialog; a grouped delete then sends exactly
 * [members]' appids as `members=`.
 */
@Serializable
data class DeletePlanOut(
    val appid: Int,
    val with_packages: Boolean = false,
    val members: List<PlanMemberOut> = emptyList(),
    val kept_children: List<KeptChildOut> = emptyList(),
    val delete_depots: List<PlannedDepotOut> = emptyList(),
    val skipped_shared: List<SkippedSharedDepotOut> = emptyList(),
    val unusable: List<FailedDepotOut> = emptyList(),
    val total_bytes: Long = 0L,
    val blocked_by: List<BlockingJobOut> = emptyList(),
)

/**
 * Body of `POST /v1/cache/{appid}/gc` — `vault_api/routers/cache.py::GcRequest`.
 * `execute` defaults to `false` (dry run) on both sides, matching the
 * server's "dry run in three independent places" rule (api/README.md
 * "Garbage collection").
 */
@Serializable
data class GcRequest(
    val execute: Boolean = false,
)

/** Response of `POST /v1/cache/{appid}/gc` — `vault_api/routers/cache.py::GcJobRef`. */
@Serializable
data class GcJobRef(
    val appid: Int,
    val job_id: Int,
    val status: String,
    val type: String,
    val mode: String,
    val execute: Boolean,
    val deduplicated: Boolean,
)
