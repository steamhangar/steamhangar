package dev.steamvault.app.ui.theme

import androidx.compose.foundation.BorderStroke
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.SegmentedButtonColors
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.TextFieldColors
import androidx.compose.runtime.Composable
import androidx.compose.ui.unit.dp

/*
 * WP CONTRAST-1 (LINE-CONTRAST-1, audit 2026-11-20): controls that are
 * identified by their border draw it in [VaultColors.LineStrong], the twin
 * of the web `--line-strong` (`.kit .in`, `.kit .sortg`, `.kit .segi`).
 * Material 3 takes the unfocused text-field border and the segmented-button
 * border from the scheme's `outline` (= [VaultColors.Line], 1.52:1 on Bg).
 * `outline` itself stays Line, because OutlinedButton borders use it too and
 * the web keeps `.kit .btn` on --line (a button is identified by its label).
 * Every OutlinedTextField and SegmentedButton passes these colours; a
 * source-scan pin (ControlColorsPinTest) keeps new call sites in line.
 *
 * Selected state: Material tells the selected segment / chip apart by its
 * container fill only (the scheme's default secondaryContainer; no 3:1
 * edge), the twin of the web `.seg` selected fill (1.27:1 on --surface). Like
 * the web's --dim-2 inset ring, the selected item gets a [VaultColors.Dim2]
 * border: 5.81:1 on Bg, 5.25:1 on Surface (WCAG 1.4.11 state indicator).
 */

/** OutlinedTextField colours with the 3:1 [VaultColors.LineStrong] unfocused border. */
@Composable
fun vaultTextFieldColors(): TextFieldColors =
    OutlinedTextFieldDefaults.colors(unfocusedBorderColor = VaultColors.LineStrong)

/** SegmentedButton colours: the 3:1 [VaultColors.LineStrong] border, [VaultColors.Dim2] on the selected segment. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun vaultSegmentedButtonColors(): SegmentedButtonColors =
    SegmentedButtonDefaults.colors(
        activeBorderColor = VaultColors.Dim2,
        inactiveBorderColor = VaultColors.LineStrong,
    )

/**
 * FilterChip border for a single-choice range row ([dev.steamvault.app.ui.traffic.RangeChips],
 * twin of the web `.seg rangesegs`): the default unselected border, and a
 * 1dp [VaultColors.Dim2] border on the selected chip (Material draws none).
 */
@Composable
fun vaultRangeChipBorder(selected: Boolean): BorderStroke =
    FilterChipDefaults.filterChipBorder(
        enabled = true,
        selected = selected,
        selectedBorderColor = VaultColors.Dim2,
        selectedBorderWidth = 1.dp,
    )

/**
 * FilterChip border for the filter chips (the Library chip row and the
 * Filters panel's categories, twins of the web `.fchip`): the default
 * unselected border, and a 1dp [VaultColors.Accent] border on a selected
 * chip like the web `.fchip[aria-pressed="true"]` (10.86:1 on Bg, 9.82:1 on
 * Surface; Material draws no edge on a selected chip). WP CONTRAST-1.
 */
@Composable
fun vaultFilterChipBorder(selected: Boolean): BorderStroke =
    FilterChipDefaults.filterChipBorder(
        enabled = true,
        selected = selected,
        selectedBorderColor = VaultColors.Accent,
        selectedBorderWidth = 1.dp,
    )

/**
 * Border of a button that stands in for a web select (the Library sort
 * button, web `.sortg select`; the Filters genre / rating pickers, web
 * `.lib-meta select`): [VaultColors.LineStrong], 3.82:1 on Bg, 3.45:1 on
 * Surface, instead of the theme `outline` (Line, 1.38:1 on Surface). Plain
 * OutlinedButtons keep `outline`, like the web `.kit .btn`. WP CONTRAST-1.
 */
fun vaultFieldButtonBorder(): BorderStroke = BorderStroke(1.dp, VaultColors.LineStrong)
