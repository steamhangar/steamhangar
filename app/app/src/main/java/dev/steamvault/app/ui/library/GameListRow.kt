package dev.steamvault.app.ui.library

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Checkbox
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import dev.steamvault.app.ui.library.logic.GameCardModel
import dev.steamvault.app.ui.library.logic.InstalledBadge
import dev.steamvault.app.ui.library.logic.StatusActionType
import dev.steamvault.app.ui.library.logic.cardIconKind
import dev.steamvault.app.ui.library.logic.showsUpdateBadge
import dev.steamvault.app.ui.status.StatusIcon
import dev.steamvault.app.ui.status.StatusIconSize

/**
 * The list layout's row (WP 4b.4 brief): "small capsule, title, size,
 * status per row -- the only layout that never truncates a title" (mockup
 * round 4).
 *
 * **Fixed columns (WP LIB-LIST-1, twin of web `.grid.list .card`).** User
 * feedback on prod rc15: the right side of a row was a column of items with
 * varying widths, so nothing lined up from row to row. Every row now uses
 * the SAME fixed cells, so the status, size, update and installed-on
 * columns sit at the same x in every row; the name absorbs the slack.
 *
 * Phone (narrower than [LIST_WIDE_MIN_WIDTH]; the web phone layout):
 *
 *     cover | name ........................ | size
 *           | icon | status word .......... | update cell
 *           | installed on (full width, truncated)
 *           | +N content packages · N failed
 *
 * The update cell carries the compact "Update" (a non-cached game with the
 * update flag; a cached one says it in its status word, UPD-6) or the
 * "Unplayed" label; the two never co-occur (the label needs a cached card
 * without the update state). Size: right-aligned, tabular figures.
 *
 * Wide (from [LIST_WIDE_MIN_WIDTH]; web BP-M): one line per row,
 *
 *     cover | name ......... | icon | status | size | update | installed on
 *           | +N content packages
 *
 * with the installed-on column widening from [LIST_WIDE_INSTALLED_WIDTH] to
 * [LIST_WIDER_INSTALLED_WIDTH] where the row has room (web BP-L).
 *
 * The status word is the compact one ("Partial 42%", "Not verified"; WP
 * CACHE-COMPLETE-1c, web `.grid.list .stcompact`); the icon speaks the full
 * one. The installed-on text is truncated in its column, so its spoken
 * label names EVERY client ([installedBadgeSpokenText], web
 * `installedOnNames`).
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun GameListRow(
    model: GameCardModel,
    selecting: Boolean,
    onOpen: (Int) -> Unit,
    onLongPress: (Int) -> Unit,
    onToggleSelect: (Int) -> Unit,
    onAction: (Int, StatusActionType) -> Unit,
    modifier: Modifier = Modifier,
) {
    val statusWord = cardStatusWord(model, compact = true)
    val spokenStatus = cardStatusWord(model)
    val action = model.action
    val iconModifier = if (!selecting && action != null) {
        Modifier
            .clip(RoundedCornerShape(50))
            .clickable { onAction(model.appid, action.type) }
    } else {
        Modifier
    }
    val installedText = installedBadgeText(model.installedBadge)
    val installedSpoken = installedBadgeSpokenText(model.installedBadge, model.installedNames)

    val nameCell: @Composable (Modifier) -> Unit = { m ->
        Text(
            text = model.name,
            style = MaterialTheme.typography.bodyMedium,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = m,
        )
    }
    val iconCell: @Composable () -> Unit = {
        Box(modifier = Modifier.width(LIST_ICON_WIDTH), contentAlignment = Alignment.CenterStart) {
            // The icon speaks the FULL word ("Partially cached 42%"); the
            // visible compact word next to it is the short form of it.
            StatusIcon(kind = cardIconKind(model), size = StatusIconSize.MEDIUM, modifier = iconModifier, spokenLabel = spokenStatus)
        }
    }
    val statusCell: @Composable (Modifier) -> Unit = { m ->
        Text(
            text = statusWord,
            style = MaterialTheme.typography.labelSmall,
            color = cardStatusWordColor(model), // WP UPD-6, CACHE-COMPLETE-1c
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = m.testTag(LIST_STATUS_TAG),
        )
    }
    val sizeCell: @Composable (Dp) -> Unit = { width ->
        Text(
            text = model.sizeLabel.orEmpty(),
            style = MaterialTheme.typography.labelSmall.copy(fontFeatureSettings = TABULAR_FIGURES),
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = TextAlign.End,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.width(width).testTag(LIST_SIZE_TAG),
        )
    }
    val flagCell: @Composable (Dp, Alignment) -> Unit = { width, align ->
        Box(modifier = Modifier.width(width), contentAlignment = align) {
            // WP UPD-3: same flag as GameCard's badge, compact here (WP
            // UPD-6: only where the status word does not already say it).
            if (showsUpdateBadge(model)) {
                UpdateAvailableLabel(compact = true)
            }
            // WP TOOL-APPS-2d: "Unplayed" (web `.grid.list .playbadge`).
            if (model.neverPlayed) NeverPlayedLabel(compact = true)
        }
    }
    val installedCell: @Composable (Modifier) -> Unit = { m ->
        // WP AG-3: same shared decision/text as GameCard.kt's grid pill --
        // see ui/library/InstalledBadgeText.kt.
        if (installedText != null) {
            Text(
                text = installedText,
                style = MaterialTheme.typography.labelSmall,
                color = if (model.installedBadge is InstalledBadge.InstalledNotCached) {
                    MaterialTheme.colorScheme.error
                } else {
                    MaterialTheme.colorScheme.onSurfaceVariant
                },
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = m
                    .testTag(LIST_INSTALLED_TAG)
                    .semantics { if (installedSpoken != null) contentDescription = installedSpoken },
            )
        }
    }

    BoxWithConstraints(
        modifier = modifier
            .fillMaxWidth()
            .combinedClickable(
                onClick = { if (selecting) onToggleSelect(model.appid) else onOpen(model.appid) },
                onLongClick = { onLongPress(model.appid) },
            )
            .padding(vertical = 8.dp, horizontal = 8.dp),
    ) {
        val wide = maxWidth >= LIST_WIDE_MIN_WIDTH
        val installedWidth = if (maxWidth >= LIST_WIDER_MIN_WIDTH) LIST_WIDER_INSTALLED_WIDTH else LIST_WIDE_INSTALLED_WIDTH
        Row(modifier = Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            if (selecting) {
                Checkbox(checked = model.selected, onCheckedChange = { onToggleSelect(model.appid) })
            }
            CoverArtImage(
                coverUrl = model.coverUrl,
                cachedCoverUrl = model.cachedCoverUrl,
                name = model.name,
                fallbackHues = model.fallbackHues,
                // WP TOOL-APPS-2d: top-aligned -- a row with extra lines (a
                // group's "+N content packages", the installed-on line)
                // grows downward instead of pushing its cover down.
                modifier = Modifier
                    .align(Alignment.Top)
                    .size(width = 34.dp, height = 51.dp)
                    .clip(RoundedCornerShape(4.dp))
                    .alpha(if (model.toolApp) TOOL_APP_COVER_ALPHA else 1f), // WP API-FIX-4
            )
            Spacer(Modifier.width(LIST_COLUMN_GAP))
            if (wide) {
                Column(modifier = Modifier.weight(1f)) {
                    nameCell(Modifier.fillMaxWidth())
                    GroupPackagesLine(model)
                }
                Spacer(Modifier.width(LIST_COLUMN_GAP))
                iconCell()
                statusCell(Modifier.width(LIST_WIDE_STATUS_WIDTH))
                Spacer(Modifier.width(LIST_COLUMN_GAP))
                sizeCell(LIST_WIDE_SIZE_WIDTH)
                Spacer(Modifier.width(LIST_COLUMN_GAP))
                flagCell(LIST_TRAILING_WIDTH, Alignment.CenterStart)
                Spacer(Modifier.width(LIST_COLUMN_GAP))
                Box(modifier = Modifier.width(installedWidth)) { installedCell(Modifier.fillMaxWidth()) }
            } else {
                Column(modifier = Modifier.weight(1f)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        nameCell(Modifier.weight(1f))
                        Spacer(Modifier.width(LIST_COLUMN_GAP))
                        sizeCell(LIST_TRAILING_WIDTH)
                    }
                    Row(
                        modifier = Modifier.padding(top = 2.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        iconCell()
                        statusCell(Modifier.weight(1f))
                        Spacer(Modifier.width(LIST_COLUMN_GAP))
                        flagCell(LIST_TRAILING_WIDTH, Alignment.CenterEnd)
                    }
                    installedCell(Modifier.fillMaxWidth().padding(top = 2.dp))
                    GroupPackagesLine(model)
                }
            }
        }
    }
}

/** From this row width on, every column sits on one line (web BP-M). */
internal val LIST_WIDE_MIN_WIDTH = 720.dp

/** From this row width on, the installed-on column is wider (web BP-L). */
internal val LIST_WIDER_MIN_WIDTH = 1000.dp

/** The status icon's cell (web `[icon] 18px`, plus the gap to the word). */
internal val LIST_ICON_WIDTH = 22.dp

/** Gap between two columns. */
internal val LIST_COLUMN_GAP = 8.dp

/** Phone: the size column and, under it, the update cell -- their right
 * edges line up. Wide: the update column. Holds "Unplayed" at font 1.3. */
internal val LIST_TRAILING_WIDTH = 76.dp

/** Wide layout: the status column (web 118px, plus room for font 1.3). */
internal val LIST_WIDE_STATUS_WIDTH = 128.dp

/** Wide layout: the size column. */
internal val LIST_WIDE_SIZE_WIDTH = 64.dp

/** Wide layout: the installed-on column (web 132px; 210px from BP-L). */
internal val LIST_WIDE_INSTALLED_WIDTH = 132.dp
internal val LIST_WIDER_INSTALLED_WIDTH = 210.dp

/** OpenType tabular figures: "1.2 GB" and "48.9 GB" share digit widths. */
internal const val TABULAR_FIGURES = "tnum"

/** Test tags of the list row's status word, size and installed-on cells
 * (WP LIB-LIST-1: the screenshot test checks they line up row to row). */
internal const val LIST_STATUS_TAG = "list-status"
internal const val LIST_SIZE_TAG = "list-size"
internal const val LIST_INSTALLED_TAG = "list-installed"
