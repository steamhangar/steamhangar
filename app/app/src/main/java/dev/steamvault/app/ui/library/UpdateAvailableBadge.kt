package dev.steamvault.app.ui.library

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.padding
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
            .clip(RoundedCornerShape(50))
            .background(VaultColors.StatusStale)
            .padding(horizontal = 6.dp, vertical = 2.dp),
    )
}

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
