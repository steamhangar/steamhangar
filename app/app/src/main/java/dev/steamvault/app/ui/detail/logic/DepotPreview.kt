package dev.steamvault.app.ui.detail.logic

import dev.steamvault.app.net.model.DepotPreviewOut
import dev.steamvault.app.net.model.GameDetail
import dev.steamvault.app.ui.library.logic.formatBytesGB

/**
 * WP D19 (app), Android twin of `web/js/lib/depot-preview.js` plus the
 * "Expected ..." block of `web/js/components/game-detail-sheet.js`
 * (`buildExpectedBlock`): the EXPECTED depots and size of a game that was
 * never downloaded (`GET /v1/oracle/{appid}/preview`).
 *
 * The numbers are the manifest oracle's statement about the public branch,
 * an expectation and never an observation: every string says "expected", and
 * the sheet shows the observed depots by itself once the first download maps
 * them ([wantsPreview] turns false). Pure -- no Compose, no network. Wording
 * is Kotlin literals pinned against the web source by
 * `DepotPreviewWebTwinTest`; the decisions by `DepotPreviewTest`.
 */
object DepotPreviewWording {
    const val DEPOT_PREFIX = "Depot "
    const val DASH = "—"
    const val DLC_NOTE = "Not included in the size (DLC depots): "
    const val HINT = "An expectation from the public Steam branch, not a measurement."
}

/** Server `state` values (api/vault_api/depot_preview.py). */
object PreviewState {
    const val OFF = "off"
    const val CACHED = "cached"
    const val PENDING = "pending"
    const val READY = "ready"
    const val UNAVAILABLE = "unavailable"
}

/** Delays (ms) before asking again while the server says `pending`: four
 * tries, then the card stays "unknown" (web `PENDING_RETRY_MS`). */
val PREVIEW_PENDING_RETRY_MS: List<Long> = listOf(1500L, 3000L, 6000L, 12000L)

/** Is this payload something the card can show? */
fun isPreviewReady(preview: DepotPreviewOut?): Boolean =
    preview != null && preview.state == PreviewState.READY && preview.depots.isNotEmpty()

/** Size line of the expected block, or `null` when there is nothing to say.
 * Never claims the figure is on disk. */
fun expectedSizeText(preview: DepotPreviewOut?): String? {
    if (preview == null || !isPreviewReady(preview)) return null
    val size = formatBytesGB(preview.total_size_bytes) ?: return null
    val download = formatBytesGB(preview.total_download_bytes)
    return if (download != null) "Expected size $size (download $download)" else "Expected size $size"
}

/** One row of the "Expected depots" list; a depot without a size keeps its
 * row with [sizeText] `null` (shown as a dash, never as 0). */
data class ExpectedDepotRow(val depotid: Int, val sizeText: String?)

fun expectedDepotRows(preview: DepotPreviewOut?): List<ExpectedDepotRow> {
    if (preview == null || !isPreviewReady(preview)) return emptyList()
    return preview.depots.map { ExpectedDepotRow(it.depotid, formatBytesGB(it.size_bytes)) }
}

/** The row text, "Depot 1366541 — 4.4 GB" (web `buildExpectedBlock`). */
fun expectedDepotLine(row: ExpectedDepotRow): String =
    "${DepotPreviewWording.DEPOT_PREFIX}${row.depotid} ${DepotPreviewWording.DASH} ${row.sizeText ?: DepotPreviewWording.DASH}"

/** The DLC-depot note, or `null` when there are none. They are listed apart,
 * never in the size. */
fun expectedDlcNote(preview: DepotPreviewOut?): String? {
    if (preview == null || !isPreviewReady(preview) || preview.dlc_depots.isEmpty()) return null
    return DepotPreviewWording.DLC_NOTE + preview.dlc_depots.joinToString(", ") {
        val dlc = it.dlcappid
        // JS truthiness: a dlcappid of 0 counts as absent, like null.
        if (dlc != null && dlc != 0) "${it.depotid} (DLC $dlc)" else it.depotid.toString()
    }
}

/**
 * Should the card ask for a preview at all? Only for a game with no observed
 * depots ([detail] is `null` for an untracked game); the server decides the
 * rest (switches, TTL).
 */
fun wantsPreview(detail: GameDetail?): Boolean = detail == null || detail.depots.isEmpty()

/** The expected block is only worth drawing with a size line (web
 * `buildExpectedBlock` returns null otherwise and the sheet falls back to
 * "Depots unknown"). */
fun showsExpectedBlock(preview: DepotPreviewOut?, detail: GameDetail?): Boolean =
    wantsPreview(detail) && expectedSizeText(preview) != null
