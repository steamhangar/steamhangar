package dev.steamvault.app.ui.library

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.steamvault.app.ui.library.logic.GameCardModel
import dev.steamvault.app.ui.library.logic.NEVER_PLAYED_COMPACT_LABEL
import dev.steamvault.app.ui.library.logic.NEVER_PLAYED_DESCRIPTION
import dev.steamvault.app.ui.library.logic.NEVER_PLAYED_LABEL
import dev.steamvault.app.ui.library.logic.failedPackagesText
import dev.steamvault.app.ui.library.logic.packagesAriaText
import dev.steamvault.app.ui.library.logic.packagesLineText
import dev.steamvault.app.ui.theme.VaultColors

/** Test tag of a group card's "+N content packages" line (WP TOOL-APPS-2d). */
internal const val GROUP_PACKAGES_LINE_TAG = "group-packages-line"

/** Test tag of the "Never played" pill / list label. */
internal const val NEVER_PLAYED_TAG = "never-played"

/**
 * A group card's "+N content packages" line, followed by a "· N failed"
 * warning when a package failed (WP TOOL-APPS-2d, ADR-0022 §3, user decision
 * Q3 B variant; twin of web `.pkgline`). One spoken label carries both
 * facts. Nothing for a plain card.
 */
@Composable
internal fun GroupPackagesLine(model: GameCardModel, modifier: Modifier = Modifier) {
    if (model.packageCount <= 0) return
    val spoken = packagesAriaText(model.packageCount, model.failedPackageCount)
    Row(
        modifier = modifier
            .fillMaxWidth()
            .testTag(GROUP_PACKAGES_LINE_TAG)
            .clearAndSetSemantics { contentDescription = spoken },
    ) {
        Text(
            text = packagesLineText(model.packageCount),
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
        if (model.failedPackageCount > 0) {
            Text(
                text = " · ${failedPackagesText(model.failedPackageCount)}",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.error,
                maxLines = 1,
                softWrap = false,
            )
        }
    }
}

/**
 * The "Never played" pill on the cover, right of the status pill (twin of
 * web `.playpill`, WP WEB-FEAT-SUGGEST-2): plain text on the same dark
 * ground, "Unplayed" where the card is narrow ([compact], the 3-column
 * grid); spoken as "Fully cached, never played".
 */
@Composable
internal fun NeverPlayedPill(compact: Boolean, modifier: Modifier = Modifier) {
    Text(
        text = if (compact) NEVER_PLAYED_COMPACT_LABEL else NEVER_PLAYED_LABEL,
        style = MaterialTheme.typography.labelSmall,
        color = VaultColors.Text,
        maxLines = 1,
        softWrap = false,
        overflow = TextOverflow.Ellipsis,
        modifier = modifier
            .testTag(NEVER_PLAYED_TAG)
            .clearAndSetSemantics { contentDescription = NEVER_PLAYED_DESCRIPTION }
            .clip(RoundedCornerShape(50))
            .background(Color.Black.copy(alpha = 0.55f))
            .padding(horizontal = 6.dp, vertical = 3.dp),
    )
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
            .clearAndSetSemantics { contentDescription = NEVER_PLAYED_DESCRIPTION },
    )
}
