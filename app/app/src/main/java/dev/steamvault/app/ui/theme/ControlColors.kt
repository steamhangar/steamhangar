package dev.steamvault.app.ui.theme

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.BottomSheetDefaults
import androidx.compose.material3.ButtonColors
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CardColors
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.NavigationBarItemColors
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.SegmentedButtonColors
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SelectableChipColors
import androidx.compose.material3.SwitchColors
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.TextFieldColors
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.unit.Dp
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
 * container fill only (the scheme's secondaryContainer, [VaultColors.Raised2]
 * since ANDROID-SHEET-BG-1; no 3:1 edge), the twin of the web `.seg`
 * selected fill (--raised-2, 1.26:1 on --surface). Like
 * the web's --dim-2 inset ring, the selected item gets a [VaultColors.Dim2]
 * border: 5.84:1 on Bg, 5.26:1 on Surface (WCAG 1.4.11 state indicator).
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
 * Filters panel's categories, twins of the web `.fchip`): a 1dp
 * [VaultColors.LineSoft] unselected border like `.fchip{ border:1px solid
 * var(--line-soft) }` (ANDROID-SHEET-BG-1; it was Material's `outline`), and
 * a 1dp [VaultColors.Accent] border on a selected
 * chip like the web `.fchip[aria-pressed="true"]` (10.85:1 on Bg, 9.78:1 on
 * Surface; Material draws no edge on a selected chip). WP CONTRAST-1.
 */
@Composable
fun vaultFilterChipBorder(selected: Boolean): BorderStroke =
    FilterChipDefaults.filterChipBorder(
        enabled = true,
        selected = selected,
        borderColor = VaultColors.LineSoft,
        selectedBorderColor = VaultColors.Accent,
        selectedBorderWidth = 1.dp,
    )

/**
 * Border of a button that stands in for a web select (the Library sort
 * button, web `.sortg select`; the Filters genre / rating pickers, web
 * `.lib-meta select`): [VaultColors.LineStrong], 3.83:1 on Bg, 3.45:1 on
 * Surface, instead of the theme `outline` (Line, 1.37:1 on Surface). Plain
 * OutlinedButtons keep `outline`, like the web `.kit .btn`. WP CONTRAST-1.
 */
fun vaultFieldButtonBorder(): BorderStroke = BorderStroke(1.dp, VaultColors.LineStrong)

/*
 * WP ANDROID-SHEET-BG-1 (operator decision): exact web twins for the bottom
 * bar and its Downloads pip, the Downloads / Clients cards and the filter chips,
 * where the theme slots alone cannot match the web (one slot serves several
 * components with different web roles). WebSurfaceTwinTest reads the web
 * rules these values come from; ControlColorsPinTest / the wiring pins keep
 * the call sites on these helpers.
 */

/**
 * CSS `color-mix(in srgb, [b] [fractionOfB], [a])`: per channel in sRGB,
 * rounded to 8 bits like the browser paints it (Compose's `lerp` mixes in
 * Oklab, so it is not the twin). For opaque colours only: the alphas are
 * not mixed, the result is opaque. [fractionOfB] is clamped to 0..1, so
 * every channel stays in range.
 */
internal fun srgbMix(a: Color, b: Color, fractionOfB: Float): Color {
    val t = fractionOfB.coerceIn(0f, 1f)
    return Color(
        red = (a.red + (b.red - a.red) * t).coerceIn(0f, 1f),
        green = (a.green + (b.green - a.green) * t).coerceIn(0f, 1f),
        blue = (a.blue + (b.blue - a.blue) * t).coerceIn(0f, 1f),
    )
}

/**
 * Bottom bar ground, web `.nav`: `color-mix(in srgb, var(--bg) 96%,
 * transparent)`. The 4 % transparency is kept for the twin, but it is
 * invisible here: the bar sits on the Scaffold's Bg ground.
 */
val VaultNavBarContainer: Color = VaultColors.Bg.copy(alpha = 0.96f)

/**
 * Bottom bar items, web `.nav-btn` (--dim-2) and `.nav-btn[aria-current="page"]`
 * (--accent on icon and label). The web draws no indicator pill, so the
 * Material indicator is transparent.
 */
@Composable
fun vaultNavBarItemColors(): NavigationBarItemColors =
    NavigationBarItemDefaults.colors(
        selectedIconColor = VaultColors.Accent,
        selectedTextColor = VaultColors.Accent,
        indicatorColor = Color.Transparent,
        unselectedIconColor = VaultColors.Dim2,
        unselectedTextColor = VaultColors.Dim2,
    )

/** The bottom bar's top edge, web `.nav{ border-top:1px solid var(--line-soft) }`, drawn over the bar. */
fun Modifier.vaultNavBarTopBorder(): Modifier = this.drawWithContent {
    drawContent()
    val stroke = 1.dp.toPx()
    drawLine(VaultColors.LineSoft, Offset(0f, stroke / 2), Offset(size.width, stroke / 2), strokeWidth = stroke)
}

/**
 * Downloads job card, web `.kit .job` (components/job-row.js): --raised fill,
 * text in --text (the web inherits it; Material's contentColorFor(Raised)
 * would give the `surfaceVariant` pair, Dim).
 */
@Composable
fun vaultJobCardColors(): CardColors =
    CardDefaults.cardColors(containerColor = VaultColors.Raised, contentColor = VaultColors.Text)

/**
 * Downloads job card border: web `.kit .job` `1px solid color-mix(in srgb,
 * var(--run) 40%, var(--line))`, a held (paused) job `.kit .job.held` --line-soft.
 */
fun vaultJobCardBorder(held: Boolean): BorderStroke =
    BorderStroke(1.dp, if (held) VaultColors.LineSoft else srgbMix(VaultColors.Line, VaultColors.StatusRun, 0.40f))

/**
 * WP APP-DL-CARD-1: a danger action on an outlined button, web `.kit
 * .btn.danger` (the job card's Cancel, `ghost danger`): transparent, label
 * in --danger, edge `color-mix(in srgb, var(--danger) 45%, var(--line))`.
 * Disabled follows the web `.btn:disabled{ opacity:.45 }` on label and edge.
 */
@Composable
fun vaultDangerButtonColors(): ButtonColors =
    ButtonDefaults.outlinedButtonColors(
        contentColor = VaultColors.StatusDanger,
        disabledContentColor = VaultColors.StatusDanger.copy(alpha = VAULT_DISABLED_ALPHA),
    )

/** The edge of [vaultDangerButtonColors]' button (web `.kit .btn.danger` border-color). */
fun vaultDangerButtonBorder(enabled: Boolean): BorderStroke {
    val edge = srgbMix(VaultColors.Line, VaultColors.StatusDanger, 0.45f)
    return BorderStroke(1.dp, if (enabled) edge else edge.copy(alpha = VAULT_DISABLED_ALPHA))
}

/** Web `.btn:disabled{ opacity:.45 }`. */
const val VAULT_DISABLED_ALPHA = 0.45f

/**
 * A panel card on --surface: the Clients sheet row (web `.jobcard`,
 * components/clients-sheet.js) and the History failure hint (web
 * `.kit .hpanel`, the panel that holds `.failhint`). On its wide layout the
 * web shows History details in a side sheet where `.kit.hist-sheet .hpanel`
 * drops fill and border; the app has no wide layout, so it follows the
 * narrow `.hpanel`. A bypassing Clients row
 * is transparent and paints [VaultBypassCardGround] through
 * [vaultPanelCardGround] instead (a Card container takes no gradient).
 */
@Composable
fun vaultPanelCardColors(bypass: Boolean = false): CardColors =
    CardDefaults.cardColors(
        containerColor = if (bypass) Color.Transparent else VaultColors.Surface,
        contentColor = VaultColors.Text,
    )

/**
 * The stops of web `.jobcard.bypass`'s ground, `linear-gradient(180deg,
 * color-mix(in srgb, var(--stale) 7%, transparent), var(--surface))`, with
 * the translucent top stop composited over Surface, the Clients sheet's
 * ground (theme `surfaceContainerLow`). Over that ground the CSS gradient
 * (premultiplied) is exactly this opaque one: (1-t) * mix(surface, stale,
 * 7 %) + t * surface, whatever way the renderer interpolates.
 */
val VaultBypassCardStops: List<Color> = listOf(srgbMix(VaultColors.Surface, VaultColors.StatusStale, 0.07f), VaultColors.Surface)

/** Web `.jobcard.bypass` ground, top to bottom (180deg). */
val VaultBypassCardGround: Brush = Brush.verticalGradient(VaultBypassCardStops)

/** Paints [VaultBypassCardGround] in the card's [shape] on a bypassing row; no-op otherwise. */
fun Modifier.vaultPanelCardGround(bypass: Boolean, shape: Shape): Modifier =
    if (bypass) this.background(VaultBypassCardGround, shape) else this

/**
 * Clients screen card border, web `.kit .ccard` (components/client-card.js):
 * 1px --line-soft, and on a card with an outdated agent or a bypass
 * `.ccard.warn`'s `color-mix(in srgb, var(--stale) 45%, var(--line))`.
 */
fun vaultClientCardBorder(warn: Boolean): BorderStroke =
    BorderStroke(1.dp, if (warn) srgbMix(VaultColors.Line, VaultColors.StatusStale, 0.45f) else VaultColors.LineSoft)

/**
 * The panel card border: web `.jobcard` / `.kit .hpanel` `1px solid
 * var(--line-soft)`; a Clients row suspected of bypassing the cache takes
 * `.jobcard.bypass`'s `color-mix(in srgb, var(--stale) 35%, transparent)`.
 */
fun vaultPanelCardBorder(bypass: Boolean = false): BorderStroke =
    BorderStroke(1.dp, if (bypass) VaultColors.StatusStale.copy(alpha = 0.35f) else VaultColors.LineSoft)

/** Selected filter chip fill, web `.kit .fchip[aria-pressed="true"]`: `color-mix(in srgb, var(--accent) 10%, var(--surface))`. */
val VaultFilterChipSelectedFill: Color = srgbMix(VaultColors.Surface, VaultColors.Accent, 0.10f)

/**
 * Filter chip colours (Library chip row, Filters categories; web `.fchip`):
 * an unselected chip is --surface with a --dim label like `.fchip`; a
 * selected chip is [VaultFilterChipSelectedFill] with --accent label and
 * icons, like `.fchip[aria-pressed="true"]`; its count turns accent too, like
 * `.kit .fchip[aria-pressed="true"] b` (the Library chip count is a span of
 * the label, Unspecified when selected, so it takes the label colour).
 * The border is [vaultFilterChipBorder]. Range chips stay on the theme's
 * selected-segment colours (web `.seg`).
 */
@Composable
fun vaultFilterChipColors(): SelectableChipColors =
    FilterChipDefaults.filterChipColors(
        containerColor = VaultColors.Surface,
        labelColor = VaultColors.Dim,
        iconColor = VaultColors.Dim,
        selectedContainerColor = VaultFilterChipSelectedFill,
        selectedLabelColor = VaultColors.Accent,
        selectedLeadingIconColor = VaultColors.Accent,
        selectedTrailingIconColor = VaultColors.Accent,
    )

/**
 * The scrim behind a sheet, web `.sheet-backdrop` / `.dialog-backdrop`:
 * `color-mix(in srgb, var(--bg) 62%, transparent)`. Passed as
 * `scrimColor` to every ModalBottomSheet. An AlertDialog's dim is the
 * platform dialog window's and takes no colour in material3 1.3.1.
 */
val VaultScrim: Color = VaultColors.Bg.copy(alpha = 0.62f)

/** The sheet grip, web `.sheet .grab`: 38 x 4, --line. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun VaultSheetDragHandle() {
    BottomSheetDefaults.DragHandle(width = 38.dp, height = 4.dp, color = VaultColors.Line)
}

/** Dialog corners, web `.dialog{ border-radius:var(--r-l) }` (18px). */
val VaultDialogShape: Shape = RoundedCornerShape(18.dp)

/**
 * The dialog edge, web `.dialog{ border:1px solid var(--line) }`, drawn over
 * the AlertDialog (its modifier sizes the dialog surface exactly: material3
 * 1.3.1 BasicAlertDialog's Box propagates its min constraints).
 */
fun Modifier.vaultDialogBorder(): Modifier = this.border(1.dp, VaultColors.Line, VaultDialogShape)

/** The menu edge, web `.pop{ border:1px solid var(--line) }` (DropdownMenu `border`). */
fun vaultMenuBorder(): BorderStroke = BorderStroke(1.dp, VaultColors.Line)

/**
 * Switch colours, web `.kit.view-settings .sw`: off = --raised-2 track,
 * --line-strong edge, --dim knob (`.sw::after`); on = --accent-glow track,
 * --accent edge and knob (`.sw[aria-checked="true"]`).
 *
 * WP APP-EXPERT-SWITCH-1: the off track is only 1.40:1 on --bg, so the edge
 * is what tells the control apart; it is a control identified by its border
 * (DESIGN.md "Contrast floors") and takes [VaultColors.LineStrong], 3.83:1
 * on Bg and 3.45:1 on Surface (the old --line edge was 1.52:1 and the off
 * switch read as disabled). The --dim knob is 5.06:1 on the --raised-2 track.
 */
@Composable
fun vaultSwitchColors(): SwitchColors =
    SwitchDefaults.colors(
        uncheckedThumbColor = VaultColors.Dim,
        uncheckedTrackColor = VaultColors.Raised2,
        uncheckedBorderColor = VaultColors.LineStrong,
        checkedThumbColor = VaultColors.Accent,
        checkedTrackColor = VaultColors.AccentGlow,
        checkedBorderColor = VaultColors.Accent,
    )

/** The command box of a failure hint, web `.kit .hpanel .cmd`: --bg, 1px --line, --r-s corners, 8 x 10 padding. */
fun Modifier.vaultCommandBox(): Modifier =
    this.background(VaultColors.Bg, RoundedCornerShape(8.dp))
        .border(1.dp, VaultColors.Line, RoundedCornerShape(8.dp))
        .padding(horizontal = 10.dp, vertical = 8.dp)

/**
 * A figure tile's edge, web `.kit .tile` (--line-soft) and `.kit .tile.hi`
 * (`color-mix(in srgb, var(--stale) 55%, var(--line))`): the Clients summary
 * tiles and the Settings storage-balance tiles.
 */
fun vaultTileBorderColor(hi: Boolean): Color =
    if (hi) srgbMix(VaultColors.Line, VaultColors.StatusStale, 0.55f) else VaultColors.LineSoft

/** Tile corners, web `.kit .tile{ border-radius:var(--r-m) }` (12px). */
val VaultTileShape: Shape = RoundedCornerShape(12.dp)

/** Tile padding, web `.kit .tile{ padding:14px var(--sp-4) }`: 14 vertical, 16 horizontal (px = dp). */
val VaultTilePadding: PaddingValues = PaddingValues(horizontal = 16.dp, vertical = 14.dp)

/**
 * Clients card padding, web `.kit .ccard{ padding:18px var(--sp-5) }` (18
 * vertical, 20 horizontal), and 14 on every side while the card list is at
 * most 460 wide (`@container (max-width: 460px){ .kit .ccard{ padding:14px } }`,
 * the phone case). The card fills the list (`.kit .ccards`, one column), so
 * its own width stands in for the container's.
 */
fun vaultClientCardPadding(cardWidth: Dp): PaddingValues =
    if (cardWidth <= 460.dp) PaddingValues(14.dp) else PaddingValues(horizontal = 20.dp, vertical = 18.dp)

/**
 * Gap between the Clients card's zones: web `@container (max-width: 700px){
 * .kit .ccard{ gap:var(--sp-4) } }` (16) and above that the row gap of
 * `.kit .ccard{ gap:var(--sp-5) var(--sp-7) }` (20; the 32 column gap has no
 * twin, the app stacks the zones in one column at every width).
 */
fun vaultClientCardZoneGap(cardWidth: Dp): Dp = if (cardWidth <= 700.dp) 16.dp else 20.dp
