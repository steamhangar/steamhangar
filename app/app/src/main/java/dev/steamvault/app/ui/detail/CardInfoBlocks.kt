package dev.steamvault.app.ui.detail

import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.steamvault.app.net.model.DepotPreviewOut
import dev.steamvault.app.net.model.StoreDetailsOut
import dev.steamvault.app.ui.detail.logic.expectedDepotLine
import dev.steamvault.app.ui.detail.logic.expectedDepotRows
import dev.steamvault.app.ui.detail.logic.expectedDlcNote
import dev.steamvault.app.ui.detail.logic.expectedSizeText
import dev.steamvault.app.ui.detail.logic.DepotPreviewWording
import dev.steamvault.app.ui.detail.logic.hasStoreContent
import dev.steamvault.app.ui.detail.logic.storeDescription
import dev.steamvault.app.ui.detail.logic.storeInfoLines
import dev.steamvault.app.ui.detail.logic.storeModeChips

/** Lines of the store description before it is tapped open (web CSS `-webkit-line-clamp:3`). */
internal const val STORE_DESCRIPTION_COLLAPSED_LINES = 3

internal const val EXPECTED_DEPOTS_TAG = "expected-depots"
internal const val STORE_DETAILS_TAG = "store-details"

/**
 * WP D19-1a/1b (twin of web `buildExpectedBlock`): the size line, one row per
 * expected depot, the DLC depots listed apart and the "an expectation, not a
 * measurement" hint. Draw it only when `showsExpectedBlock` says so.
 * Plain text only.
 */
@Composable
internal fun ExpectedDepotsBlock(preview: DepotPreviewOut) {
    Column(modifier = Modifier.fillMaxWidth().testTag(EXPECTED_DEPOTS_TAG)) {
        expectedSizeText(preview)?.let {
            Text(text = it, style = MaterialTheme.typography.bodyMedium)
        }
        for (row in expectedDepotRows(preview)) {
            Text(
                text = expectedDepotLine(row),
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.padding(vertical = 4.dp),
            )
        }
        expectedDlcNote(preview)?.let {
            Text(
                text = it,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 4.dp),
            )
        }
        Text(
            text = DepotPreviewWording.HINT,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(top = 4.dp),
        )
    }
}

/**
 * WP D19-1c (twin of web `buildStoreBlock`): description (three lines, a tap
 * opens it), mode chips, then genres / developer and date / rating lines.
 * Third-party text drawn as plain [Text] only: no links, no images, no HTML.
 * Draw it only when `hasStoreContent` says so.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun StoreDetailsBlock(store: StoreDetailsOut) {
    if (!hasStoreContent(store)) return
    Column(modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp).testTag(STORE_DETAILS_TAG)) {
        storeDescription(store)?.let { description ->
            var expanded by remember(description) { mutableStateOf(false) }
            Text(
                text = description,
                style = MaterialTheme.typography.bodySmall,
                maxLines = if (expanded) Int.MAX_VALUE else STORE_DESCRIPTION_COLLAPSED_LINES,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier
                    .fillMaxWidth()
                    .clickable { expanded = !expanded }
                    .padding(bottom = 8.dp),
            )
        }
        val chips = storeModeChips(store)
        if (chips.isNotEmpty()) {
            FlowRow(
                horizontalArrangement = Arrangement.spacedBy(6.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp),
                modifier = Modifier.padding(bottom = 8.dp),
            ) {
                for (label in chips) {
                    Text(
                        text = label,
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier
                            .border(1.dp, MaterialTheme.colorScheme.outlineVariant, RoundedCornerShape(50))
                            .padding(horizontal = 9.dp, vertical = 2.dp),
                    )
                }
            }
        }
        for (line in storeInfoLines(store)) {
            Text(
                text = line,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 2.dp),
            )
        }
    }
}
