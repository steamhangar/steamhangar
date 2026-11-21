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
 * Do not add a light variant: the app surface is committed to a single dark
 * theme on purpose (mockup-notes.md "Committed to dark, on purpose").
 */
object VaultColors {
    // Ground / surfaces
    val Bg = Color(0xFF0A1016) // --bg
    val Surface = Color(0xFF111C24) // --surface
    val Raised = Color(0xFF17252F) // --raised
    val Raised2 = Color(0xFF1D303C) // --raised-2
    val Line = Color(0xFF24363F) // --line
    val LineSoft = Color(0xFF1B2A33) // --line-soft
    // WP CONTRAST-1 (LINE-CONTRAST-1): the border of a control identified by
    // its border (text fields, segmented buttons): 3.82:1 on Bg, 3.45:1 on
    // Surface, 3.13:1 on Raised (WCAG 1.4.11: 3:1). Line stays for
    // separators, cards and outlined buttons (theme `outline`).
    val LineStrong = Color(0xFF5F727E) // --line-strong
    val Text = Color(0xFFE4EFF3) // --text
    val Dim = Color(0xFF8AA1AD) // --dim
    // WP TOKENS-SYNC-1: follows web UI-KIT-1's lift from #63798A (3.8:1 on
    // --surface) to #7A91A0 (5.0:1, WCAG AA for captions).
    // WP CONTRAST-1: also the selected segment / range chip edge (web `.seg`
    // --dim-2 ring), 5.81:1 on Bg.
    val Dim2 = Color(0xFF7A91A0) // --dim-2

    // Accent (vault aqua) — primary actions, active nav, focus, progress.
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
    val StatusRun = Color(0xFFF2CE5B) // --run (running / updating / paused bg)
    val StatusStale = Color(0xFFF07B2E) // --stale (update ready / warn)
    val StatusNone = Color(0xFF8296A6) // --none (not cached) — fully desaturated
    val StatusDanger = Color(0xFFFF6F6F) // --danger (error)

    // Status-icon ink: a single dark tone with high contrast against every
    // status hue above (web: `.sic { color:#08120F }`).
    val StatusIconInk = Color(0xFF08120F)

    // The muted Dim2 discs (cancelled, notinuse, unverified) use this ink
    // too since WP CONTRAST-1 (GLYPH-CONTRAST-1): 5.79:1 on Dim2, where the
    // old Text ink was 2.81:1 (web: `.sic.k-cancelled{ background:var(--dim-2); }`).
}
