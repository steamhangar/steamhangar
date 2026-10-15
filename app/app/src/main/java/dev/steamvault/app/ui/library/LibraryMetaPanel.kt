package dev.steamvault.app.ui.library

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Checkbox
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.ui.library.logic.META_MAX_YEAR
import dev.steamvault.app.ui.library.logic.META_MIN_YEAR
import dev.steamvault.app.ui.library.logic.MetaCategory
import dev.steamvault.app.ui.library.logic.MetaFilter
import dev.steamvault.app.ui.library.logic.MetaFilterWording
import dev.steamvault.app.ui.library.logic.RATING_STEPS
import dev.steamvault.app.ui.library.logic.availableGenres
import dev.steamvault.app.ui.library.logic.unknownNote

/**
 * WP D23: the metadata filter panel (Android twin of `renderMetaPanel` in
 * `web/js/views/library.js`): category chips (AND), a genre select (any-of,
 * one genre at a time like web), a minimum-rating select, from/to year
 * fields, "Include unknown", "Clear filters" and the "N games hidden:
 * metadata unknown" note. The CALLER hides the whole panel while no game
 * carries metadata (`metaAvailable`); the labels are the web twin's literals
 * (`MetaFilterWording`, `MetaCategory.label`).
 *
 * @param games the vault rows the genre list is read from
 * @param unknownExcluded `applyMetaFilter(...).unknownExcluded`
 */
@Composable
fun LibraryMetaPanel(
    filter: MetaFilter,
    games: List<GameSummary>,
    unknownExcluded: Int,
    onChange: (MetaFilter) -> Unit,
) {
    val genres = remember(games) { availableGenres(games) }
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp, vertical = 4.dp)
            .semantics { contentDescription = MetaFilterWording.PANEL },
    ) {
        Row(
            modifier = Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            for (category in MetaCategory.entries) {
                val on = category in filter.categories
                FilterChip(
                    selected = on,
                    onClick = {
                        onChange(filter.copy(categories = if (on) filter.categories - category else filter.categories + category))
                    },
                    label = { Text(category.label, maxLines = 1, softWrap = false) },
                )
            }
        }
        Row(
            modifier = Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            val selectedGenre = filter.genres.singleOrNull()?.let { id -> genres.firstOrNull { it.id == id } }
            PickerButton(
                label = MetaFilterWording.GENRE,
                value = selectedGenre?.label ?: MetaFilterWording.ANY_GENRE,
                options = listOf(MetaFilterWording.ANY_GENRE) + genres.map { it.label },
                onPick = { index ->
                    onChange(filter.copy(genres = if (index == 0) emptySet() else setOf(genres[index - 1].id)))
                },
            )
            val selectedRating = filter.minRating?.takeIf { it in RATING_STEPS }
            PickerButton(
                label = MetaFilterWording.RATING,
                value = selectedRating?.let { MetaFilterWording.ratingStep(it) } ?: MetaFilterWording.ANY_RATING,
                options = listOf(MetaFilterWording.ANY_RATING) + RATING_STEPS.map { MetaFilterWording.ratingStep(it) },
                onPick = { index -> onChange(filter.copy(minRating = if (index == 0) null else RATING_STEPS[index - 1])) },
            )
        }
        Row(
            modifier = Modifier.fillMaxWidth().padding(top = 4.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            YearField(
                label = MetaFilterWording.YEAR_FROM,
                value = filter.yearFrom,
                onCommit = { onChange(filter.copy(yearFrom = it)) },
                modifier = Modifier.weight(1f),
            )
            YearField(
                label = MetaFilterWording.YEAR_TO,
                value = filter.yearTo,
                onCommit = { onChange(filter.copy(yearTo = it)) },
                modifier = Modifier.weight(1f),
            )
        }
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Row(
                modifier = Modifier.toggleable(
                    value = filter.includeUnknown,
                    role = Role.Checkbox,
                    onValueChange = { onChange(filter.copy(includeUnknown = it)) },
                ),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Checkbox(checked = filter.includeUnknown, onCheckedChange = null)
                Spacer(modifier = Modifier.width(8.dp))
                Text(MetaFilterWording.INCLUDE_UNKNOWN, style = MaterialTheme.typography.bodyMedium)
            }
            if (filter.active || filter.includeUnknown) {
                TextButton(onClick = { onChange(MetaFilter.DEFAULT) }) { Text(MetaFilterWording.CLEAR) }
            }
        }
        val note = unknownNote(unknownExcluded)
        if (note.isNotEmpty()) {
            Text(
                text = note,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite },
            )
        }
    }
}

/** A labelled dropdown button ("Genre: Action"); [onPick] gets the option index. */
@Composable
private fun PickerButton(label: String, value: String, options: List<String>, onPick: (Int) -> Unit) {
    var open by remember { mutableStateOf(false) }
    Box {
        OutlinedButton(onClick = { open = true }) {
            Text("$label: $value", maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            options.forEachIndexed { index, text ->
                DropdownMenuItem(
                    text = { Text(text) },
                    onClick = {
                        open = false
                        onPick(index)
                    },
                )
            }
        }
    }
}

/** A four-digit year field: commits an empty field (null) or a year inside
 * the API's accepted range; anything else is flagged and not committed. */
@Composable
private fun YearField(label: String, value: Int?, onCommit: (Int?) -> Unit, modifier: Modifier = Modifier) {
    var text by remember(value) { mutableStateOf(value?.toString() ?: "") }
    val parsed = text.toIntOrNull()
    val valid = text.isEmpty() || (parsed != null && parsed in META_MIN_YEAR..META_MAX_YEAR)
    OutlinedTextField(
        value = text,
        onValueChange = { raw ->
            val digits = raw.filter { it in '0'..'9' }.take(4)
            text = digits
            val year = digits.toIntOrNull()
            if (digits.isEmpty()) onCommit(null) else if (year != null && year in META_MIN_YEAR..META_MAX_YEAR) onCommit(year)
        },
        modifier = modifier,
        singleLine = true,
        isError = !valid,
        label = { Text(label, maxLines = 1, overflow = TextOverflow.Ellipsis) },
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
    )
}
