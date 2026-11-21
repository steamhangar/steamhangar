package dev.steamvault.app.ui.theme

import androidx.compose.ui.graphics.Color

/**
 * SteamHangar dark theme tokens (WP 4b.1).
 *
 * Ported byte-for-byte from `web/css/theme.css` (the already-reviewed WP
 * 4a.1 port of `docs/design/vault-app-mockup.html`'s `:root` palette).
 * Every hex value below MUST match the corresponding `--custom-property` in
 * theme.css exactly — this is the single source of truth for the Compose
 * side, and the two frontends are required to stay consistent (WP brief).
 * Since WP TOKENS-SYNC-1 both sides are checked against
 * `docs/design/tokens.json` (DesignTokensContractTest): change a value
 * there first.
 *
 * WP NEUTRAL-RAMP-1 (audit D-7, Weg B): the neutral ramp (Bg .. Dim2 and
 * StatusNone) turned from OKLCH hue 230-249 toward the accent (hue ~218),
 * lightness kept; the contrast pins moved with it (StatusIconLogicTest,
 * web css-contrast.test.js).
 *
 * Do not add a light variant: the app surface is committed to a single dark
 * theme on purpose (mockup-notes.md "Committed to dark, on purpose").
 */
object VaultColors {
    // Ground / surfaces
    val Bg = Color(0xFF081113) // --bg
    val Surface = Color(0xFF111D20) // --surface
    val Raised = Color(0xFF18262A) // --raised
    val Raised2 = Color(0xFF213035) // --raised-2
    val Line = Color(0xFF26363B) // --line
    val LineSoft = Color(0xFF1D2A2E) // --line-soft
    // WP CONTRAST-1 (LINE-CONTRAST-1): the border of a control identified by
    // its border (text fields, segmented buttons): 3.83:1 on Bg, 3.45:1 on
    // Surface, 3.12:1 on Raised (WCAG 1.4.11: 3:1). Line stays for
    // separators, cards and outlined buttons (theme `outline`).
    val LineStrong = Color(0xFF5F7379) // --line-strong
    val Text = Color(0xFFE6EFF1) // --text
    val Dim = Color(0xFF8DA1A7) // --dim
    // WP TOKENS-SYNC-1: follows web UI-KIT-1's lift from #63798A (3.8:1 on
    // --surface) to #7A91A0 (5.0:1, WCAG AA for captions); #7C9298 since
    // NEUTRAL-RAMP-1 (5.26:1 on Surface).
    // WP CONTRAST-1: also the selected segment / range chip edge (web `.seg`
    // --dim-2 ring), 5.84:1 on Bg.
    val Dim2 = Color(0xFF7C9298) // --dim-2

    // Accent (vault aqua) — the rc20 kit's roles (DESIGN.md "Accent",
    // ACCENT-ROLES-1): primary action and links, focus, selection, progress
    // and the live download data (internet graph series, live speeds,
    // "Loading now" rows, the device icon).
    // Never doubles as a status colour (theme.css comment, binding here too).
    val Accent = Color(0xFF2ED9CE) // --accent
    val AccentDeep = Color(0xFF12A79F) // --accent-deep
    val AccentInk = Color(0xFF04211F) // --accent-ink
    // --accent-glow is rgba(46,217,206,.16) in CSS — same channel, alpha 0.16.
    val AccentGlow = Color(0xFF2ED9CE).copy(alpha = 0.16f)

    // Status set — hues chosen for deuteranopia/protanopia separation
    // (mockup round 5): differ in LIGHTNESS as well as hue so red-green
    // colour blindness (which collapses hue onto a light-dark axis) still
    // separates them. Colour is the THIRD cue after glyph shape and word.
    val StatusOk = Color(0xFF57DD8A) // --ok (cached / current)
    val StatusRun = Color(0xFFF2CE5B) // --run (running / updating / verify / paused)
    val StatusStale = Color(0xFFF07B2E) // --stale (update available / warn / partial)
    val StatusNone = Color(0xFF82979E) // --none (not cached / not verified) — low-chroma neutral of the ramp
    val StatusDanger = Color(0xFFFF6F6F) // --danger (error)

    // Status-icon ink: a single dark tone with high contrast against every
    // status hue above (web: `.sic { color:#08120F }`).
    val StatusIconInk = Color(0xFF08120F)

    // The muted Dim2 discs (cancelled, notinuse) use this ink
    // too since WP CONTRAST-1 (GLYPH-CONTRAST-1): 5.79:1 on Dim2 then (5.82:1
    // on today's ramp), where the old Text ink was 2.81:1 then (2.80:1 on
    // today's ramp) (web: `.sic.k-cancelled{ background:var(--dim-2); }`).
}
