package dev.steamvault.app.ui.library

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.res.stringResource
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
 * The grid badge never grows past [UPDATE_BADGE_MAX_WIDTH] and ellipsizes;
 * [GameCard] additionally keeps it clear of the select checkbox
 * ([UPDATE_BADGE_START_CLEARANCE]), so a narrow card at a large font
 * scale shows "Upda…" rather than a badge under the checkbox.
 */
@Composable
fun UpdateAvailableBadge(modifier: Modifier = Modifier) {
    Text(
        text = stringResource(R.string.library_update_available_badge),
        style = MaterialTheme.typography.labelSmall,
        color = VaultColors.StatusIconInk,
        maxLines = 1,
        overflow = TextOverflow.Ellipsis,
        modifier = modifier
            .widthIn(max = UPDATE_BADGE_MAX_WIDTH)
            .clip(RoundedCornerShape(50))
            .background(VaultColors.StatusStale)
            .padding(horizontal = 6.dp, vertical = 2.dp),
    )
}

/** Widest the grid badge may get, on any card. */
internal val UPDATE_BADGE_MAX_WIDTH = 120.dp

/** Space the grid badge leaves at the cover's start for the select
 * checkbox (Material's 48 dp touch target plus a little air). */
internal val UPDATE_BADGE_START_CLEARANCE = 52.dp

@Composable
fun UpdateAvailableLabel(modifier: Modifier = Modifier) {
    Text(
        text = stringResource(R.string.library_update_available_badge),
        style = MaterialTheme.typography.labelSmall,
        color = VaultColors.StatusStale,
        maxLines = 1,
        overflow = TextOverflow.Ellipsis,
        modifier = modifier,
    )
}
