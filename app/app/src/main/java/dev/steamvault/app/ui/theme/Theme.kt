package dev.steamvault.app.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

/**
 * The app is committed to a single dark theme on purpose (mockup NOTES.md
 * "Committed to dark, on purpose": "the app surface is single-theme ...
 * there is no light variant of the app surface itself"). [isSystemInDarkTheme]
 * is therefore intentionally NOT read here — SteamVaultTheme always applies
 * the same dark scheme regardless of the system setting, matching the web
 * frontend (web/css/theme.css has no `@media (prefers-color-scheme: light)`
 * override for the app surface either).
 *
 * WP ANDROID-SHEET-BG-1: EVERY slot of the scheme is set, none is left at
 * its Material 3 baseline default. Material 1.2+ components take their
 * containers from slots this scheme once left out, so they rendered on the
 * baseline palette: ModalBottomSheet on `surfaceContainerLow` (#1D1B20),
 * AlertDialog on `surfaceContainerHigh`, menus and the navigation bar on
 * `surfaceContainer`, Card and the switch track on
 * `surfaceContainerHighest`, Snackbar on `inverseSurface` (a light
 * #E6E0E9), and the selected segment, filter chip, nav indicator and
 * progress track on `secondaryContainer`. The slots follow the web twin's
 * roles (web/css/app.css, kit.css, theme.css; DESIGN.md "Neutrals"):
 *
 * - `surfaceContainerLow` = Surface: the bottom sheet (`.sheet` --surface)
 * - `surfaceContainer` = Raised2: the menus (web `.pop` --raised-2;
 *   DESIGN.md "Neutrals": "| `--raised`, `--raised-2` | buttons, menus,
 *   popovers, the toast, the neutral pill |")
 * - `surfaceContainerHigh` = Raised: the dialogs (`.dialog` --raised)
 * - `surfaceContainerHighest`, `surfaceBright` = Raised2: the switch track
 *   (`.sw` --raised-2)
 * - `surfaceContainerLowest`, `surfaceDim`, `scrim` = Bg: the page ground;
 *   the scrim is the web backdrop's ink (`.sheet-backdrop` mixes --bg)
 * - `inverseSurface` = Raised2, `inverseOnSurface` = Text,
 *   `inversePrimary` = Accent: the snackbar is the web `.toast`
 *   (--raised-2, --text), not a light inverted bar
 * - `secondaryContainer` = Raised2, `onSecondaryContainer` = Text: the
 *   selected segment (`.seg button[aria-pressed]`) and the progress track
 *   (`.kit .bar`); tertiary and error containers use the same ground
 * - `primaryContainer` = AccentDeep: the switch handle while pressed;
 *   `tertiary` = Accent (DESIGN.md: AccentDeep is `secondary` only)
 * - `surfaceTint` = Accent, the default (`primary`), written out
 *
 * Where one slot serves components with different web roles, or the web
 * adds an edge Material does not draw, the call site takes the exact twin
 * from ControlColors.kt / VaultSnackbar.kt instead: the bottom bar and its
 * pip (web `.nav`, `.nav-pip`), the cards (`.kit .job`, `.jobcard`,
 * `.kit .hpanel`, `.kit .ccard`, `.kit .tile`), the filter chips (`.fchip`),
 * the switch (`.sw`), the menu, dialog and toast edges (`.pop`, `.dialog`,
 * `.toast`), the sheet scrim and grip.
 *
 * ThemeColorSchemeTest pins every slot to a VaultColors token and against
 * the baseline; a new slot in a Material update fails it until it is
 * mapped here.
 */
internal val VaultDarkColorScheme = darkColorScheme(
    primary = VaultColors.Accent,
    onPrimary = VaultColors.AccentInk,
    primaryContainer = VaultColors.AccentDeep,
    onPrimaryContainer = VaultColors.AccentInk,
    inversePrimary = VaultColors.Accent,
    secondary = VaultColors.AccentDeep,
    onSecondary = VaultColors.AccentInk,
    secondaryContainer = VaultColors.Raised2,
    onSecondaryContainer = VaultColors.Text,
    tertiary = VaultColors.Accent,
    onTertiary = VaultColors.AccentInk,
    tertiaryContainer = VaultColors.Raised2,
    onTertiaryContainer = VaultColors.Text,
    background = VaultColors.Bg,
    onBackground = VaultColors.Text,
    surface = VaultColors.Surface,
    onSurface = VaultColors.Text,
    surfaceVariant = VaultColors.Raised,
    onSurfaceVariant = VaultColors.Dim,
    surfaceTint = VaultColors.Accent,
    inverseSurface = VaultColors.Raised2,
    inverseOnSurface = VaultColors.Text,
    error = VaultColors.StatusDanger,
    onError = Color.Black,
    errorContainer = VaultColors.Raised2,
    onErrorContainer = VaultColors.StatusDanger,
    outline = VaultColors.Line,
    outlineVariant = VaultColors.LineSoft,
    scrim = VaultColors.Bg,
    surfaceBright = VaultColors.Raised2,
    surfaceContainer = VaultColors.Raised2,
    surfaceContainerHigh = VaultColors.Raised,
    surfaceContainerHighest = VaultColors.Raised2,
    surfaceContainerLow = VaultColors.Surface,
    surfaceContainerLowest = VaultColors.Bg,
    surfaceDim = VaultColors.Bg,
)

@Composable
fun SteamVaultTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = VaultDarkColorScheme,
        typography = VaultTypography,
        content = content,
    )
}
