package dev.steamvault.app.ui.nav

import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import dev.steamvault.app.R

/** The bottom nav (WP 4b.4 brief: three items; WP D15d added Clients) -- see [Destination]'s kdoc for
 * why this is a plain state switch, not Navigation Compose.
 *
 * @param pendingJobsCount the Downloads nav pip (WP 4b.5 brief:
 *   "count from the partition function" --
 *   [dev.steamvault.app.ui.downloads.logic.countPending]). `0` renders no
 *   badge at all and leaves the item's accessible name as the plain
 *   [Destination.labelRes] word; a positive count renders a
 *   [Badge] AND overrides the whole item's merged contentDescription (WP
 *   brief: "contentDescription carries the count") -- mirrors
 *   `web/js/views/downloads.js::updateNavPip`'s `aria-label` override,
 *   which likewise only appears once `count > 0`.
 */
@Composable
fun BottomNavBar(current: Destination, pendingJobsCount: Int = 0, onSelect: (Destination) -> Unit) {
    NavigationBar {
        for (destination in Destination.entries) {
            val showPip = destination == Destination.DOWNLOADS && pendingJobsCount > 0
            val itemModifier = if (showPip) {
                val description = pluralStringResource(
                    R.plurals.nav_downloads_pip_description,
                    pendingJobsCount,
                    pendingJobsCount,
                )
                Modifier.semantics(mergeDescendants = true) { contentDescription = description }
            } else {
                Modifier
            }
            NavigationBarItem(
                selected = destination == current,
                onClick = { onSelect(destination) },
                modifier = itemModifier,
                icon = {
                    val icon: @Composable () -> Unit = {
                        when (destination) {
                            Destination.LIBRARY -> LibraryNavIcon()
                            Destination.CLIENTS -> ClientsNavIcon()
                            Destination.DOWNLOADS -> DownloadsNavIcon()
                            Destination.SETTINGS -> SettingsNavIcon()
                        }
                    }
                    if (showPip) {
                        BadgedBox(badge = { Badge { Text(pendingJobsCount.coerceAtMost(99).toString()) } }) { icon() }
                    } else {
                        icon()
                    }
                },
                // WP D15d: with four items "Downloads" wrapped to
                // "Download/s" at 360 dp and font 1.3. One line, never
                // wrapped; a few dp of overflow are drawn into the item's
                // own side padding instead of truncating the word.
                label = {
                    Text(
                        stringResource(destination.labelRes),
                        maxLines = 1,
                        softWrap = false,
                        overflow = TextOverflow.Visible,
                    )
                },
            )
        }
    }
}
