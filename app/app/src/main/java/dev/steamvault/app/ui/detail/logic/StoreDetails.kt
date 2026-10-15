package dev.steamvault.app.ui.detail.logic

import dev.steamvault.app.net.model.StoreDetailsOut
import java.util.Locale

/**
 * WP D19-1c (app), Android twin of `web/js/lib/store-details.js`: the store
 * block of the game detail sheet (`GET /v1/store/{appid}/details`) -- short
 * description, mode chips, genres, developer / release date and the OVERALL
 * review rating.
 *
 * Every string is third-party text. The server strips markup and caps
 * lengths; this module only returns plain strings and the sheet draws them as
 * plain `Text` (no links, no images, no HTML). Missing fields are omitted,
 * never shown as placeholders. There is deliberately no "recent reviews"
 * figure (option A of the roadmap). Pure; wording pinned against the web
 * source by `StoreDetailsWebTwinTest`, decisions by `StoreDetailsTest`.
 */
object StoreState {
    const val OFF = "off"
    const val PENDING = "pending"
    const val READY = "ready"
    const val UNAVAILABLE = "unavailable"
}

/** Same pacing as the depot preview (web `STORE_RETRY_MS`). */
val STORE_RETRY_MS: List<Long> = listOf(1500L, 3000L, 6000L, 12000L)

/** Server mode flag -> chip label; unknown flags are dropped (web `MODE_LABELS`). */
val MODE_LABELS: Map<String, String> = linkedMapOf(
    "single_player" to "Single-player",
    "multiplayer" to "Multiplayer",
    "co_op" to "Co-op",
    "vr" to "VR",
)

/** Client-side cap, independent of the server's (web `MAX_DESCRIPTION`). */
const val MAX_STORE_DESCRIPTION = 600

private fun text(v: String?): String? = v?.trim()?.takeIf { it.isNotEmpty() }

fun isStoreReady(payload: StoreDetailsOut?): Boolean = payload != null && payload.state == StoreState.READY

fun storeDescription(payload: StoreDetailsOut?): String? {
    if (payload == null || !isStoreReady(payload)) return null
    val t = text(payload.description) ?: return null
    return if (t.length > MAX_STORE_DESCRIPTION) "${t.substring(0, MAX_STORE_DESCRIPTION - 1)}…" else t
}

fun storeModeChips(payload: StoreDetailsOut?): List<String> {
    if (payload == null || !isStoreReady(payload)) return emptyList()
    return payload.modes.mapNotNull { MODE_LABELS[it] }
}

fun storeGenres(payload: StoreDetailsOut?): List<String> {
    if (payload == null || !isStoreReady(payload)) return emptyList()
    return payload.genres.mapNotNull { text(it) }
}

/** "Some Studio – 3 Mar, 2023", either half alone, or `null`. */
fun storeByLine(payload: StoreDetailsOut?): String? {
    if (payload == null || !isStoreReady(payload)) return null
    val devs = payload.developers.mapNotNull { text(it) }.joinToString(", ")
    val parts = listOfNotNull(devs.ifEmpty { null }, text(payload.release_date))
    return if (parts.isEmpty()) null else parts.joinToString(" – ")
}

/** Overall rating, e.g. "Overwhelmingly Positive (97% of 1,000 reviews)";
 * `null` without usable counts. */
fun storeRatingText(payload: StoreDetailsOut?): String? {
    if (payload == null || !isStoreReady(payload)) return null
    val pos = payload.review_positive
    val neg = payload.review_negative
    if (pos == null || neg == null || pos < 0 || neg < 0 || pos + neg == 0L) return null
    val total = pos + neg
    val percent = Math.round(pos.toDouble() / total * 100)
    val count = "$percent% of ${String.format(Locale.US, "%,d", total)} reviews"
    val label = text(payload.review_summary)
    return if (label != null) "$label ($count)" else count
}

/** Does the payload hold anything worth a block? */
fun hasStoreContent(payload: StoreDetailsOut?): Boolean =
    storeDescription(payload) != null ||
        storeModeChips(payload).isNotEmpty() ||
        storeGenres(payload).isNotEmpty() ||
        storeByLine(payload) != null ||
        storeRatingText(payload) != null

/** Lines under the chips, in the web's order (genres, by-line, rating),
 * empty ones dropped. */
fun storeInfoLines(payload: StoreDetailsOut?): List<String> =
    listOfNotNull(
        storeGenres(payload).joinToString(", ").ifEmpty { null },
        storeByLine(payload),
        storeRatingText(payload),
    )
