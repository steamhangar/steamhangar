package dev.steamvault.app.ui.library

import androidx.compose.runtime.Composable
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import dev.steamvault.app.R
import dev.steamvault.app.ui.downloads.logic.formatTimestamp
import dev.steamvault.app.ui.library.logic.InstalledBadge
import dev.steamvault.app.ui.library.logic.InstalledOnDisplay
import dev.steamvault.app.ui.library.logic.InstalledSpokenWording
import dev.steamvault.app.ui.library.logic.installedSpokenWording
import dev.steamvault.app.ui.status.StatusKind

/**
 * WP AG-3: resolves an [InstalledBadge] to the compact badge text shared by
 * [GameCard] (grid) and [GameListRow] (list layout), so both surfaces phrase
 * the same decision ([dev.steamvault.app.ui.library.logic.installedBadgeFor])
 * the same way. `null` for [InstalledBadge.NoSignal] -- renders NOTHING,
 * never "not installed anywhere" (see `InstalledState.kt`'s copy rule kdoc).
 * The detail sheet uses [installedDetailLineText] instead.
 */
@Composable
fun installedBadgeText(badge: InstalledBadge): String? = when (badge) {
    InstalledBadge.NoSignal -> null
    is InstalledBadge.InstalledAndCached ->
        installedLabel(R.string.library_installed_on, badge.display, includeTimestamp = false)
    is InstalledBadge.InstalledNotCached ->
        installedLabel(R.string.library_installed_not_cached, badge.display, includeTimestamp = false)
}

/**
 * WP LIB-LIST-1 (twin of web `installedAriaFragment`): the badge's spoken
 * form naming EVERY client ([names], `installedOnNames`) -- "Installed on
 * Demon, Frame" -- for the list row, whose installed-on column truncates.
 * When the row's status word is already "Not cached" ([kind] NONE), the
 * not-cached state is spoken as plain "Installed on ..." so "not cached" is
 * not said twice ([installedSpokenWording], ANDROID-INSTALLED-WORDING-1).
 * `null` for [InstalledBadge.NoSignal] or without names.
 */
@Composable
fun installedBadgeSpokenText(badge: InstalledBadge, kind: StatusKind, names: String?): String? {
    if (names == null) return null
    return when (installedSpokenWording(badge, kind)) {
        InstalledSpokenWording.NONE -> null
        InstalledSpokenWording.INSTALLED_ON -> stringResource(R.string.library_installed_on, names)
        InstalledSpokenWording.INSTALLED_NOT_CACHED -> stringResource(R.string.library_installed_not_cached, names)
    }
}

/**
 * The detail sheet's installed-on line (ANDROID-INSTALLED-WORDING-1):
 * always "Installed on <client> (as of <time>)" plus "(+N more)", for both
 * the cached and the not-cached state. In the not-cached state the sheet
 * puts the red `detail_installed_not_cached_warning` ("Installed, not
 * cached -- ...") right below it, so the line must not say "Installed, not
 * cached" as well; this mirrors the web sheet, whose "Installed on" heading
 * and client rows sit above that note (`buildInstalledSection`). `null` for
 * [InstalledBadge.NoSignal].
 */
@Composable
fun installedDetailLineText(badge: InstalledBadge): String? = when (badge) {
    InstalledBadge.NoSignal -> null
    is InstalledBadge.InstalledAndCached ->
        installedLabel(R.string.library_installed_on, badge.display, includeTimestamp = true)
    is InstalledBadge.InstalledNotCached ->
        installedLabel(R.string.library_installed_on, badge.display, includeTimestamp = true)
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
