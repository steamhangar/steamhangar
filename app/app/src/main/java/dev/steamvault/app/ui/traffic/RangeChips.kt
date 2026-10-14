package dev.steamvault.app.ui.traffic

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import dev.steamvault.app.ui.traffic.logic.RANGE_LABELS
import dev.steamvault.app.ui.traffic.logic.TRAFFIC_RANGES

/**
 * Range selector (WP D15d, twin of web `components/range-segs.js`):
 * "24 h | 7 days | 30 days | All" as one row of chips, one selected. The
 * row is a selectable group named [label] for TalkBack ("Statistics
 * range"); each chip is a radio button. A tap on the selected chip does
 * nothing (the caller's `selectRange` ignores it). Scrolls sideways when a
 * large font does not fit it on one line (the D12 chip-row lesson).
 */
@Composable
fun RangeChips(
    label: String,
    selected: String,
    onSelect: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    Row(
        modifier = modifier
            .horizontalScroll(rememberScrollState())
            .semantics { contentDescription = label }
            .selectableGroup(),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        for (range in TRAFFIC_RANGES) {
            FilterChip(
                selected = range == selected,
                onClick = { onSelect(range) },
                label = { Text(RANGE_LABELS.getValue(range), maxLines = 1, softWrap = false) },
                modifier = Modifier.semantics { role = Role.RadioButton },
            )
        }
    }
}
