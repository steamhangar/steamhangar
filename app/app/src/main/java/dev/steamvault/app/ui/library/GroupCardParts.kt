package dev.steamvault.app.ui.library

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.steamvault.app.ui.library.logic.GameCardModel
import dev.steamvault.app.ui.library.logic.NEVER_PLAYED_COMPACT_LABEL
import dev.steamvault.app.ui.library.logic.NEVER_PLAYED_DESCRIPTION
import dev.steamvault.app.ui.library.logic.NEVER_PLAYED_LABEL
import dev.steamvault.app.ui.library.logic.failedPackagesText
import dev.steamvault.app.ui.library.logic.packagesLineText
import dev.steamvault.app.ui.theme.VaultColors

/** Test tag of a group card's "+N content packages" line (WP TOOL-APPS-2d). */
internal const val GROUP_PACKAGES_LINE_TAG = "group-packages-line"

/** Test tag of the "Never played" pill / list label. */
internal const val NEVER_PLAYED_TAG = "never-played"

/**
 * A group card's "+N content packages" line, followed by a "· N failed"
 * warning when a package failed (WP TOOL-APPS-2d, ADR-0022 §3, user decision
 * Q3 B variant; twin of web `.pkgline`, review fix a8ec24e): two units that
 * never clip -- the warning is its own unit with its own separator and
 * moves onto its own line when the card is too narrow ([FlowRow]); the
 * package count wraps rather than clip on a very narrow card. Nothing for a
 * plain card. Each unit carries the test tag [GROUP_LINE_TEXT_TAG] so the
 * screenshot test can prove neither is clipped.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun GroupPackagesLine(model: GameCardModel, modifier: Modifier = Modifier) {
    if (model.packageCount <= 0) return
    FlowRow(
        modifier = modifier
            .fillMaxWidth()
            .testTag(GROUP_PACKAGES_LINE_TAG)
            .semantics(mergeDescendants = true) {},
        horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Text(
            text = packagesLineText(model.packageCount),
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.testTag(GROUP_LINE_TEXT_TAG),
        )
        if (model.failedPackageCount > 0) {
            Text(
                text = "· ${failedPackagesText(model.failedPackageCount)}",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.error,
                maxLines = 1,
                softWrap = false,
                modifier = Modifier.testTag(GROUP_LINE_TEXT_TAG),
            )
        }
    }
}

/** Test tag of each text unit of [GroupPackagesLine]. */
internal const val GROUP_LINE_TEXT_TAG = "group-line-text"

/** Text inset at each side of the "Never played" pill. */
internal val NEVER_PLAYED_HORIZONTAL_PADDING = 6.dp

/** True when the full "Never played" label, [fullLabelWidthPx] wide on one
 * line, fits the [availableTextWidthPx] the pill gets next to the status
 * pill (same shape as [updateBadgeFullLabelFits]). */
internal fun neverPlayedFullLabelFits(fullLabelWidthPx: Int, availableTextWidthPx: Float): Boolean =
    fullLabelWidthPx <= availableTextWidthPx

/**
 * The "Never played" pill on the cover, right of the status pill (twin of
 * web `.playpill`, WP WEB-FEAT-SUGGEST-2): plain text on the same dark
 * ground; "Unplayed" in the 3-column grid ([compact]) and wherever the full
 * label does not fit the width left next to the status pill (measured, like
 * [UpdateAvailableBadge]) -- never an ellipsized "Never …". Spoken as
 * "Fully cached, never played". [GameCard] lays the pills out in a FlowRow,
 * so the pill moves onto its own line where it would not fit beside the
 * status pill; the measured fallback covers a card narrower than the full
 * label even on its own line.
 */
@Composable
internal fun NeverPlayedPill(compact: Boolean, modifier: Modifier = Modifier) {
    val style = MaterialTheme.typography.labelSmall
    val measurer = rememberTextMeasurer()
    val fullLabelWidthPx = remember(style, measurer) {
        measurer.measure(text = NEVER_PLAYED_LABEL, style = style, maxLines = 1, softWrap = false).size.width
    }
    BoxWithConstraints(modifier = modifier) {
        val availableTextWidthPx = with(LocalDensity.current) {
            (maxWidth - NEVER_PLAYED_HORIZONTAL_PADDING * 2).toPx()
        }
        val full = !compact && neverPlayedFullLabelFits(fullLabelWidthPx, availableTextWidthPx)
        Text(
            text = if (full) NEVER_PLAYED_LABEL else NEVER_PLAYED_COMPACT_LABEL,
            style = style,
            color = VaultColors.Text,
            maxLines = 1,
            softWrap = false,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier
                .testTag(NEVER_PLAYED_TAG)
                .semantics { contentDescription = NEVER_PLAYED_DESCRIPTION }
                .clip(RoundedCornerShape(50))
                .background(Color.Black.copy(alpha = 0.55f))
                .padding(horizontal = NEVER_PLAYED_HORIZONTAL_PADDING, vertical = 3.dp),
        )
    }
}

/** The list layout's twin of [NeverPlayedPill] (web `.playbadge`): a text
 * line, since the list's mini cover is too small for pills. */
@Composable
internal fun NeverPlayedLabel(modifier: Modifier = Modifier) {
    Text(
        text = NEVER_PLAYED_LABEL,
        style = MaterialTheme.typography.labelSmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        maxLines = 1,
        modifier = modifier
            .testTag(NEVER_PLAYED_TAG)
            .semantics { contentDescription = NEVER_PLAYED_DESCRIPTION },
    )
}
