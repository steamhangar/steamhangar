package dev.steamvault.app.ui.library

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.steamvault.app.R
import dev.steamvault.app.ui.theme.VaultColors

/**
 * WP UPD-3: the "Update available" badge (`GameSummary.update_available`,
 * WP UPD-1). Grid cards draw it as a small tag over the cover's top end
 * ([UpdateAvailableBadge]); list rows as a text line in the same colour
 * ([UpdateAvailableLabel]). Colour is the palette's `--stale` ("update
 * ready"), the same one the mockup gave the update state.
 *
 * The grid badge never grows past [UPDATE_BADGE_MAX_WIDTH], and
 * [GameCard] keeps it clear of the select checkbox
 * ([UPDATE_BADGE_START_CLEARANCE]). When the full label does not fit the
 * width that leaves (narrow card, large font scale), the badge says
 * "Update" instead of an ellipsized "Update availa…" -- the word web uses
 * in its compact layouts (WP APP-FIX-CARD). The full label stays the
 * spoken one (contentDescription) either way.
 */
@Composable
fun UpdateAvailableBadge(modifier: Modifier = Modifier) {
    val fullLabel = stringResource(R.string.library_update_available_badge)
    val shortLabel = stringResource(R.string.library_update_available_badge_short)
    val style = MaterialTheme.typography.labelSmall
    val measurer = rememberTextMeasurer()
    val fullLabelWidthPx = remember(fullLabel, style, measurer) {
        measurer.measure(text = fullLabel, style = style, maxLines = 1, softWrap = false).size.width
    }
    BoxWithConstraints(modifier = modifier) {
        val availableTextWidthPx = with(LocalDensity.current) {
            (minOf(maxWidth, UPDATE_BADGE_MAX_WIDTH) - UPDATE_BADGE_HORIZONTAL_PADDING * 2).toPx()
        }
        Text(
            text = if (updateBadgeFullLabelFits(fullLabelWidthPx, availableTextWidthPx)) fullLabel else shortLabel,
            style = style,
            color = VaultColors.StatusIconInk,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier
                .testTag(UPDATE_BADGE_TAG)
                .semantics { contentDescription = fullLabel }
                .widthIn(max = UPDATE_BADGE_MAX_WIDTH)
                .clip(RoundedCornerShape(50))
                .background(VaultColors.StatusStale)
                .padding(horizontal = UPDATE_BADGE_HORIZONTAL_PADDING, vertical = 2.dp),
        )
    }
}

/** True when the full "Update available" label, [fullLabelWidthPx] wide
 * on one line, fits the [availableTextWidthPx] the badge leaves for text. */
internal fun updateBadgeFullLabelFits(fullLabelWidthPx: Int, availableTextWidthPx: Float): Boolean =
    fullLabelWidthPx <= availableTextWidthPx

/** Test tag of the grid badge's text (WP UPD-6: the card's status icon
 * now speaks "Update available" too, so the badge is found by tag). */
internal const val UPDATE_BADGE_TAG = "update-badge"

/** Text inset at each side of the badge's pill. */
internal val UPDATE_BADGE_HORIZONTAL_PADDING = 6.dp

/** Widest the grid badge may get, on any card. */
internal val UPDATE_BADGE_MAX_WIDTH = 120.dp

/** Space the grid badge leaves at the cover's start for the select
 * checkbox (Material's 48 dp touch target plus a little air). */
internal val UPDATE_BADGE_START_CLEARANCE = 52.dp

/**
 * The list row's update flag. WP LIB-LIST-1: [compact] = the short "Update"
 * in the row's fixed update cell (web `.grid.list .updbadge .ubcompact`),
 * still spoken as the full "Update available".
 */
@Composable
fun UpdateAvailableLabel(modifier: Modifier = Modifier, compact: Boolean = false) {
    val full = stringResource(R.string.library_update_available_badge)
    val text = if (compact) stringResource(R.string.library_update_available_badge_short) else full
    Text(
        text = text,
        style = MaterialTheme.typography.labelSmall,
        color = VaultColors.StatusStale,
        maxLines = 1,
        overflow = TextOverflow.Ellipsis,
        modifier = modifier
            .testTag(UPDATE_LABEL_TAG)
            .semantics { contentDescription = full },
    )
}

/** Test tag of the list row's "Update available" line (review fix: the
 * screenshot test tells it apart from a status word saying the same). */
internal const val UPDATE_LABEL_TAG = "update-label"
