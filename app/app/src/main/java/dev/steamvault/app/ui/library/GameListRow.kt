package dev.steamvault.app.ui.library

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
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
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.steamvault.app.ui.library.logic.GameCardModel
import dev.steamvault.app.ui.library.logic.InstalledBadge
import dev.steamvault.app.ui.library.logic.StatusActionType
import dev.steamvault.app.ui.status.StatusIcon
import dev.steamvault.app.ui.status.StatusIconSize

/**
 * The list layout's row (WP 4b.4 brief): "small capsule, title, size,
 * status per row -- the only layout that never truncates a title" (mockup
 * round 4). Unlike [GameCard]'s pill, the icon + status word + size are
 * three separate cells here, matching the mockup's "the list row was
 * already the honest, roomy version" note (round 6) -- no pill needed on a
 * small thumbnail.
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
    Row(
        modifier = modifier
            .fillMaxWidth()
            .combinedClickable(
                onClick = { if (selecting) onToggleSelect(model.appid) else onOpen(model.appid) },
                onLongClick = { onLongPress(model.appid) },
            )
            .padding(vertical = 8.dp, horizontal = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (selecting) {
            Checkbox(checked = model.selected, onCheckedChange = { onToggleSelect(model.appid) })
        }

        CoverArtImage(
            coverUrl = model.coverUrl,
            name = model.name,
            fallbackHues = model.fallbackHues,
            modifier = Modifier
                .size(width = 34.dp, height = 51.dp)
                .clip(RoundedCornerShape(4.dp))
                .alpha(if (model.toolApp) TOOL_APP_COVER_ALPHA else 1f), // WP API-FIX-4
        )

        val statusWord = cardStatusWord(model)
        val action = model.action
        val iconModifier = if (!selecting && action != null) {
            Modifier
                .padding(start = 4.dp)
                .clip(RoundedCornerShape(50))
                .clickable { onAction(model.appid, action.type) }
        } else {
            Modifier.padding(start = 4.dp)
        }
        // Content description already carries the status word (StatusIcon's
        // own semantics -- reused per the brief, no separate a11y string
        // needed here). The word is ALSO rendered visibly in the title
        // column below (mockup rule: every roomy layout keeps icon + word).
        StatusIcon(kind = model.kind, size = StatusIconSize.MEDIUM, modifier = iconModifier)

        Column(modifier = Modifier.weight(1f).padding(horizontal = 10.dp)) {
            Text(
                text = model.name,
                style = MaterialTheme.typography.bodyMedium,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                text = statusWord,
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            // WP AG-3: same shared decision/text as GameCard.kt's grid pill --
            // see ui/library/InstalledBadgeText.kt.
            installedBadgeText(model.installedBadge)?.let { text ->
                Text(
                    text = text,
                    style = MaterialTheme.typography.labelSmall,
                    color = if (model.installedBadge is InstalledBadge.InstalledNotCached) {
                        MaterialTheme.colorScheme.error
                    } else {
                        MaterialTheme.colorScheme.onSurfaceVariant
                    },
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
        }

        if (model.sizeLabel != null) {
            Text(
                text = model.sizeLabel,
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.width(56.dp),
            )
        }
    }
}
