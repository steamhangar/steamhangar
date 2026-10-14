package dev.steamvault.app.ui.library

import androidx.compose.runtime.Composable
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import dev.steamvault.app.R
import dev.steamvault.app.ui.downloads.logic.formatTimestamp
import dev.steamvault.app.ui.library.logic.InstalledBadge
import dev.steamvault.app.ui.library.logic.InstalledOnDisplay

/**
 * WP AG-3: resolves an [InstalledBadge] to display text, shared by
 * [GameCard] (grid), [GameListRow] (list layout), and the detail sheet
 * (`ui/detail/GameDetailSheet.kt`) so all three surfaces phrase the same
 * decision ([dev.steamvault.app.ui.library.logic.installedBadgeFor]) the
 * same way. `null` for [InstalledBadge.NoSignal] -- renders NOTHING, never
 * "not installed anywhere" (see `InstalledState.kt`'s copy rule kdoc).
 *
 * @param includeTimestamp the compact grid/list badge omits `reported_at`
 *   (no room); the detail sheet passes `true` for the fuller line.
 */
@Composable
fun installedBadgeText(badge: InstalledBadge, includeTimestamp: Boolean = false): String? = when (badge) {
    InstalledBadge.NoSignal -> null
    is InstalledBadge.InstalledAndCached ->
        installedLabel(R.string.library_installed_on, badge.display, includeTimestamp)
    is InstalledBadge.InstalledNotCached ->
        installedLabel(R.string.library_installed_not_cached, badge.display, includeTimestamp)
}

/**
 * WP LIB-LIST-1 (twin of web `installedAriaFragment`): the badge's spoken
 * form naming EVERY client ([names], `installedOnNames`) -- "Installed on
 * Demon, Frame" -- for the list row, whose installed-on column truncates.
 * `null` for [InstalledBadge.NoSignal] or without names.
 */
@Composable
fun installedBadgeSpokenText(badge: InstalledBadge, names: String?): String? {
    if (names == null) return null
    return when (badge) {
        InstalledBadge.NoSignal -> null
        is InstalledBadge.InstalledAndCached -> stringResource(R.string.library_installed_on, names)
        is InstalledBadge.InstalledNotCached -> stringResource(R.string.library_installed_not_cached, names)
    }
}

@Composable
private fun installedLabel(baseRes: Int, display: InstalledOnDisplay, includeTimestamp: Boolean): String {
    var text = stringResource(baseRes, display.primaryClientId)
    if (includeTimestamp) {
        text = stringResource(R.string.library_installed_with_timestamp, text, formatTimestamp(display.primaryReportedAt))
    }
    if (display.additionalClientCount > 0) {
        text += " " + pluralStringResource(
            R.plurals.library_installed_additional_clients,
            display.additionalClientCount,
            display.additionalClientCount,
        )
    }
    return text
}
