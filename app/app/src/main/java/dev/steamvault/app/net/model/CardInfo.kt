package dev.steamvault.app.net.model

import kotlinx.serialization.Serializable

/**
 * WP D19 (app): the two card-enrichment answers. Every field except [appid]
 * and [state] is defaulted, so an older or newer server decodes instead of
 * failing the call, and `state` stays a String for the same reason as
 * [UpdateCheckOut.status]: `ui/detail/logic/` decides what a value means.
 */

/** One public-branch depot of [DepotPreviewOut] (`routers/oracle.py::PreviewDepotOut`). */
@Serializable
data class PreviewDepotOut(
    val depotid: Int,
    val size_bytes: Long? = null,
    val download_bytes: Long? = null,
)

/** One DLC depot without a public manifest (`PreviewDlcDepotOut`). */
@Serializable
data class PreviewDlcDepotOut(
    val depotid: Int,
    val dlcappid: Int? = null,
)

/**
 * `GET /v1/oracle/{appid}/preview` (WP D19-1a,
 * `vault_api/routers/oracle.py::DepotPreviewOut`): the EXPECTED depots and
 * size of a never-downloaded game. [state] is `off` | `cached` | `pending` |
 * `ready` | `unavailable`.
 */
@Serializable
data class DepotPreviewOut(
    val appid: Int,
    val state: String,
    val expected: Boolean = true,
    val checked_at: String? = null,
    val total_size_bytes: Long? = null,
    val total_download_bytes: Long? = null,
    val depots: List<PreviewDepotOut> = emptyList(),
    val dlc_depots: List<PreviewDlcDepotOut> = emptyList(),
)

/**
 * `GET /v1/store/{appid}/details` (WP D19-1c,
 * `vault_api/routers/store_details.py::StoreDetailsOut`). [state] is `off` |
 * `pending` | `ready` | `unavailable`. Every text is third-party plain text.
 */
@Serializable
data class StoreDetailsOut(
    val appid: Int,
    val state: String,
    val checked_at: String? = null,
    val description: String? = null,
    val genres: List<String> = emptyList(),
    val developers: List<String> = emptyList(),
    val release_date: String? = null,
    val modes: List<String> = emptyList(),
    val review_summary: String? = null,
    val review_positive: Long? = null,
    val review_negative: Long? = null,
)
