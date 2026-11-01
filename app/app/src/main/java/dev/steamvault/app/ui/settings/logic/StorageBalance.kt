package dev.steamvault.app.ui.settings.logic

import dev.steamvault.app.net.model.CacheSummaryOut
import java.util.Locale

/**
 * Storage balance presentation (WP ANDROID-RC21-1-TWINS, twin of
 * `web/js/lib/storage-balance.js`, WP STORAGE-BALANCE-1). Pure: no Compose,
 * no I/O. `ui/settings/SettingsScreen.kt` draws the result as the read-only
 * "Storage balance" card at the end of the Expert panel, from
 * `GET /v1/cache/summary`'s `storage_balance` (api/README.md "Storage
 * balance").
 *
 * An older server does not send the field: [storageBalanceView] then
 * returns `null` and the card stays hidden -- never zeros. Same for a
 * balance with a missing field or a negative size (only the residual may
 * be negative).
 *
 * **Verbatim-port exception (app/README.md "String resources").** Every
 * user-visible literal here is the web module's own literal, pinned by
 * string equality in `StorageBalanceTest` and against the web source in
 * `StorageBalanceWebTwinTest`.
 */
object StorageBalanceText {
    /** Card title. */
    const val TITLE = "Storage balance"

    /** The line under the tiles. */
    const val NOTE =
        "From the last size count and the last update check. Older builds is an estimate: garbage collection reclaims them but also keeps beta branches."

    /** The line under the tiles while the residual is 0 (no "Unexplained" tile then). */
    const val ACCOUNTED = "Everything on disk is accounted for (unexplained: 0)."

    /** Warning text when the server flags the balance without a reason. */
    const val WARNING_FALLBACK = "A large part of the cache is not used by the current builds."
}

/** One tile of the card (web `{key, label, value, sub, note, hi}`). */
data class BalanceTile(
    val key: String,
    val label: String,
    val value: String,
    val sub: String?,
    val note: String,
    /** Highlighted (web `.tile.hi`): part of the server's warning. */
    val hi: Boolean,
)

/** The card's content (web `{tiles, warning, accounted}`). */
data class StorageBalanceView(
    val tiles: List<BalanceTile>,
    val warning: String?,
    /** True while the residual is 0: no "Unexplained" tile, the note starts with [StorageBalanceText.ACCOUNTED]. */
    val accounted: Boolean,
) {
    /** The line under the tiles (web `paintStorageBalance`). */
    val note: String
        get() = if (accounted) "${StorageBalanceText.ACCOUNTED} ${StorageBalanceText.NOTE}" else StorageBalanceText.NOTE
}

private const val GIB = 1_073_741_824.0

/** Web `formatBytesGBOrZero`: a real zero prints ("0.0 GB"), a negative is `null`. */
private fun formatBytesGBOrZero(bytes: Long): String? {
    if (bytes < 0) return null
    val gb = bytes / GIB
    val n = if (gb >= 100) String.format(Locale.ROOT, "%.0f", gb) else String.format(Locale.ROOT, "%.1f", gb)
    return "$n GB"
}

private fun size(bytes: Long): String = formatBytesGBOrZero(bytes) ?: ""

/** Signed size: the residual can be negative (web `signedSize`, U+2212 minus). */
private fun signedSize(bytes: Long): String = if (bytes < 0) "−${size(-bytes)}" else size(bytes)

private fun depots(n: Long): String = if (n == 1L) "1 depot" else "$n depots"

/**
 * The card's content, or `null` when there is nothing honest to show (no
 * summary yet, an older server without `storage_balance`, a missing field,
 * or a negative size). The "Unexplained" tile is only there when the
 * residual is not 0.
 */
fun storageBalanceView(summary: CacheSummaryOut?): StorageBalanceView? {
    val b = summary?.storage_balance ?: return null
    val onDisk = b.on_disk_bytes ?: return null
    val planned = b.planned_bytes ?: return null
    val present = b.present_bytes ?: return null
    val measuredDepots = b.measured_depots ?: return null
    val oldBuilds = b.old_builds_bytes ?: return null
    val unmeasured = b.unmeasured_bytes ?: return null
    val unmeasuredDepots = b.unmeasured_depots ?: return null
    val unmapped = b.unmapped_bytes ?: return null
    val unmappedDepots = b.unmapped_depots ?: return null
    val unexplained = b.unexplained_bytes ?: return null
    if (listOf(onDisk, planned, present, measuredDepots, oldBuilds, unmeasured, unmeasuredDepots, unmapped, unmappedDepots).any { it < 0 }) {
        return null
    }
    val reason = b.warning_reason?.trim()
    val warning = if (b.warning == true) {
        if (!reason.isNullOrEmpty()) reason else StorageBalanceText.WARNING_FALLBACK
    } else {
        null
    }
    val tiles = mutableListOf(
        BalanceTile(
            key = "on_disk",
            label = "On disk",
            value = size(onDisk),
            sub = null,
            note = "Everything in the cache folder.",
            hi = false,
        ),
        BalanceTile(
            key = "current",
            label = "Current builds",
            value = size(present),
            sub = null,
            note = "On disk, of the ${size(planned)} the current builds of the measured games need (${depots(measuredDepots)}).",
            hi = false,
        ),
        BalanceTile(
            key = "old_builds",
            label = "Older builds",
            value = size(oldBuilds),
            sub = null,
            note = "In measured depots but not part of the current build as last checked: older builds, damaged chunks, and new downloads since the last check.",
            hi = warning != null,
        ),
        BalanceTile(
            key = "unmeasured",
            label = "Not measured",
            value = size(unmeasured),
            sub = depots(unmeasuredDepots),
            note = "Games, DLCs and tool packages without a completeness check.",
            hi = false,
        ),
        BalanceTile(
            key = "unmapped",
            label = "No game",
            value = size(unmapped),
            sub = depots(unmappedDepots),
            note = "Depot folders no game is mapped to.",
            hi = false,
        ),
    )
    val accounted = unexplained == 0L
    if (!accounted) {
        tiles.add(
            BalanceTile(
                key = "unexplained",
                label = "Unexplained",
                value = signedSize(unexplained),
                sub = null,
                note = "Should be 0. The size count and the last check disagree (files changed in between, or a depot measured against two builds).",
                hi = warning != null,
            ),
        )
    }
    return StorageBalanceView(tiles, warning, accounted)
}
