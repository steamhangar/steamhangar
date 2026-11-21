package dev.steamvault.app.ui.theme

import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * WP ANDROID-SHEET-BG-1 (operator decision): the bottom bar and its
 * Downloads pip, the Downloads / Clients cards (incl. the bypass gradient)
 * and the filter chips are exact web twins, through the call-site helpers
 * in ControlColors.kt.
 *
 * Two halves, both LITERAL (docs/learnings/android.md):
 * - the web rules the helpers copy, read from web/css (relative to the
 *   module directory `app/app`, as DesignTokensContractTest reads
 *   docs/design/tokens.json);
 * - the Android values those helpers produce, as ARGB hex.
 *
 * When a web check fails, one of two edits applies, and the message names
 * both: VALUE drift (the web changed a colour) -> change the helper in
 * ControlColors.kt and both literals here; GRAMMAR drift (the rule moved
 * into a selector list or a media block, or a shorthand was split) -> widen
 * [rule] instead. [rule] reads only top-level rules whose selector starts a
 * line exactly as given, merging repeats in source order.
 */
class WebSurfaceTwinTest {

    private fun read(path: String): String {
        val file = File(path)
        check(file.exists()) { "expected a file at ${file.absolutePath}" }
        return file.readText(Charsets.UTF_8)
    }

    private val appCss by lazy { read("../../web/css/app.css") }
    private val kitCss by lazy { read("../../web/css/kit.css") }
    private val themeCss by lazy { read("../../web/css/theme.css") }

    /** Declarations of the top-level rule(s) `selector{ ... }` in [css], comments stripped. */
    private fun rule(css: String, selector: String): Map<String, String> {
        val code = css.replace(Regex("/\\*.*?\\*/", RegexOption.DOT_MATCHES_ALL), "")
        val blocks = Regex("(?m)^" + Regex.escape(selector) + "\\s*\\{([^}]*)\\}").findAll(code).toList()
        assertTrue(
            "no top-level `$selector{` rule found: the web renamed the selector (rename it here and in the helper's " +
                "KDoc) or moved it into a selector list / media block (GRAMMAR drift: widen WebSurfaceTwinTest.rule)",
            blocks.isNotEmpty(),
        )
        val decls = linkedMapOf<String, String>()
        for (block in blocks) {
            for (decl in block.groupValues[1].split(';')) {
                val colon = decl.indexOf(':')
                if (colon > 0) decls[decl.substring(0, colon).trim()] = decl.substring(colon + 1).trim().replace(Regex("\\s+"), " ")
            }
        }
        return decls
    }

    private fun assertDecl(css: String, file: String, selector: String, property: String, expected: String, helper: String) {
        val actual = rule(css, selector)[property]
        assertEquals(
            "web/css/$file `$selector{ $property }` is no longer `$expected`. VALUE drift: change $helper in " +
                "ControlColors.kt and the literals in WebSurfaceTwinTest. GRAMMAR drift: widen WebSurfaceTwinTest.rule",
            expected,
            actual,
        )
    }

    @Test
    fun `the web rules the twin helpers copy are unchanged`() {
        // Bottom bar: VaultNavBarContainer, vaultNavBarTopBorder, vaultNavBarItemColors.
        assertDecl(appCss, "app.css", ".nav", "background", "color-mix(in srgb, var(--bg) 96%, transparent)", "VaultNavBarContainer")
        assertDecl(appCss, "app.css", ".nav", "border-top", "1px solid var(--line-soft)", "vaultNavBarTopBorder")
        assertDecl(appCss, "app.css", ".nav-btn", "color", "var(--dim-2)", "vaultNavBarItemColors (unselected)")
        assertDecl(appCss, "app.css", ".nav-btn[aria-current=\"page\"]", "color", "var(--accent)", "vaultNavBarItemColors (selected)")
        assertEquals(
            "web `.nav-btn[aria-current=\"page\"]` now paints a background: VALUE drift -> give vaultNavBarItemColors an " +
                "indicatorColor to match (it is transparent)",
            null,
            rule(appCss, ".nav-btn[aria-current=\"page\"]")["background"],
        )
        // Downloads job card: vaultJobCardColors, vaultJobCardBorder.
        assertDecl(kitCss, "kit.css", ".kit .job", "background", "var(--raised)", "vaultJobCardColors")
        assertDecl(kitCss, "kit.css", ".kit .job", "border", "1px solid color-mix(in srgb, var(--run) 40%, var(--line))", "vaultJobCardBorder")
        assertDecl(kitCss, "kit.css", ".kit .job.held", "border-color", "var(--line-soft)", "vaultJobCardBorder (held)")
        // Clients sheet row and History failure hint: vaultPanelCardColors, vaultPanelCardBorder.
        assertDecl(appCss, "app.css", ".jobcard", "background", "var(--surface)", "vaultPanelCardColors")
        assertDecl(appCss, "app.css", ".jobcard", "border", "1px solid var(--line-soft)", "vaultPanelCardBorder")
        assertDecl(appCss, "app.css", ".jobcard.bypass", "border-color", "color-mix(in srgb, var(--stale) 35%, transparent)", "vaultPanelCardBorder (bypass)")
        assertDecl(kitCss, "kit.css", ".kit .hpanel", "background", "var(--surface)", "vaultPanelCardColors")
        assertDecl(kitCss, "kit.css", ".kit .hpanel", "border", "1px solid var(--line-soft)", "vaultPanelCardBorder")
        // Selected filter chip: vaultFilterChipColors, VaultFilterChipSelectedFill, vaultFilterChipBorder.
        val pressed = ".kit .fchip[aria-pressed=\"true\"]"
        assertDecl(kitCss, "kit.css", pressed, "background", "color-mix(in srgb, var(--accent) 10%, var(--surface))", "VaultFilterChipSelectedFill")
        assertDecl(kitCss, "kit.css", pressed, "color", "var(--accent)", "vaultFilterChipColors (selected label)")
        assertDecl(kitCss, "kit.css", pressed, "border-color", "var(--accent)", "vaultFilterChipBorder")
        // The pressed chip's count (operator decision: accent on both sides).
        assertDecl(kitCss, "kit.css", "$pressed b", "color", "var(--accent)", "vaultFilterChipColors (count = selected label colour)")
        // Unselected filter chip: vaultFilterChipColors, vaultFilterChipBorder.
        assertDecl(kitCss, "kit.css", ".kit .fchip", "background", "var(--surface)", "vaultFilterChipColors (containerColor)")
        assertDecl(kitCss, "kit.css", ".kit .fchip", "color", "var(--dim)", "vaultFilterChipColors (labelColor)")
        assertDecl(kitCss, "kit.css", ".kit .fchip", "border", "1px solid var(--line-soft)", "vaultFilterChipBorder (borderColor)")
        // Bypassing Clients sheet row: VaultBypassCardStops / VaultBypassCardGround.
        assertDecl(
            appCss, "app.css", ".jobcard.bypass", "background",
            "linear-gradient(180deg,color-mix(in srgb, var(--stale) 7%, transparent),var(--surface))", "VaultBypassCardStops",
        )
        // Downloads nav pip: the Badge in BottomNavBar.kt.
        assertDecl(appCss, "app.css", ".nav-pip", "background", "var(--run)", "the BottomNavBar Badge containerColor (StatusRun)")
        assertDecl(appCss, "app.css", ".nav-pip", "color", "#08120F", "the BottomNavBar Badge contentColor (StatusIconInk)")
        assertDecl(appCss, "app.css", ".nav-pip", "font-weight", "700", "the BottomNavBar pip Text fontWeight")
        assertDecl(appCss, "app.css", ".nav-pip", "font-family", "var(--mono)", "the BottomNavBar pip Text fontFamily")
        // Clients screen card: ClientCard's Surface colour, vaultClientCardBorder.
        assertDecl(kitCss, "kit.css", ".kit .ccard", "background", "var(--surface)", "ClientCard (Surface color)")
        assertDecl(kitCss, "kit.css", ".kit .ccard", "border", "1px solid var(--line-soft)", "vaultClientCardBorder")
        assertDecl(kitCss, "kit.css", ".kit .ccard.warn", "border-color", "color-mix(in srgb, var(--stale) 45%, var(--line))", "vaultClientCardBorder (warn)")
        assertDecl(kitCss, "kit.css", ".kit .ccard", "border-radius", "var(--r-l)", "ClientCardViews.kt CardShape (18dp)")
        assertDecl(kitCss, "kit.css", ".kit .ccard", "padding", "18px var(--sp-5)", "vaultClientCardPadding (wide: 18 x 20)")
        assertDecl(kitCss, "kit.css", ".kit .ccard", "gap", "var(--sp-5) var(--sp-7)", "vaultClientCardZoneGap (wide: row gap 20)")
        // The phone case lives in a container query, which rule() does not read.
        val query = kitCss.replace(Regex("/\\*.*?\\*/", RegexOption.DOT_MATCHES_ALL), "").replace(Regex("\\s+"), " ")
        val at = query.indexOf("@container (max-width: 460px){")
        assertTrue(
            "kit.css `@container (max-width: 460px){ .kit .ccard{ padding:14px; } }` changed. VALUE drift (width or " +
                "padding): change vaultClientCardPadding and WebSurfaceTwinTest. GRAMMAR drift: widen this lookup",
            at >= 0 && query.substring(at, minOf(query.length, at + 120)).contains(".kit .ccard{ padding:14px; }"),
        )
        val at700 = query.indexOf("@container (max-width: 700px){")
        assertTrue(
            "kit.css `@container (max-width: 700px){ .kit .ccard{ grid-template-columns:1fr; gap:var(--sp-4); } }` changed. " +
                "VALUE drift (width or gap): change vaultClientCardZoneGap and WebSurfaceTwinTest. GRAMMAR drift: widen this lookup",
            at700 >= 0 && query.substring(at700, minOf(query.length, at700 + 120)).contains(".kit .ccard{ grid-template-columns:1fr; gap:var(--sp-4); }"),
        )
    }

    @Test
    fun `the web rules of menus, dialogs, sheets, toasts, the switch, the command box and tiles are unchanged`() {
        // Menu: theme surfaceContainer (Raised2) and vaultMenuBorder.
        assertDecl(appCss, "app.css", ".pop", "background", "var(--raised-2)", "Theme.kt surfaceContainer")
        assertDecl(appCss, "app.css", ".pop", "border", "1px solid var(--line)", "vaultMenuBorder")
        // Dialog: theme surfaceContainerHigh (Raised), VaultDialogShape, vaultDialogBorder.
        assertDecl(appCss, "app.css", ".dialog", "background", "var(--raised)", "Theme.kt surfaceContainerHigh")
        assertDecl(appCss, "app.css", ".dialog", "border", "1px solid var(--line)", "vaultDialogBorder")
        assertDecl(appCss, "app.css", ".dialog", "border-radius", "var(--r-l)", "VaultDialogShape (18dp)")
        // Sheet: VaultScrim and VaultSheetDragHandle.
        assertDecl(appCss, "app.css", ".sheet-backdrop", "background", "color-mix(in srgb, var(--bg) 62%, transparent)", "VaultScrim")
        assertDecl(appCss, "app.css", ".sheet .grab", "width", "38px", "VaultSheetDragHandle (width)")
        assertDecl(appCss, "app.css", ".sheet .grab", "height", "4px", "VaultSheetDragHandle (height)")
        assertDecl(appCss, "app.css", ".sheet .grab", "background", "var(--line)", "VaultSheetDragHandle (color)")
        // Toast: VaultSnackbar (ground = theme inverseSurface / inverseOnSurface).
        assertDecl(themeCss, "theme.css", ".toast", "background", "var(--raised-2)", "Theme.kt inverseSurface")
        assertDecl(themeCss, "theme.css", ".toast", "color", "var(--text)", "Theme.kt inverseOnSurface")
        assertDecl(themeCss, "theme.css", ".toast", "border", "1px solid var(--line)", "VaultSnackbar (border)")
        assertDecl(themeCss, "theme.css", ".toast", "border-radius", "var(--r-m)", "VaultToastShape (12dp)")
        assertDecl(themeCss, "theme.css", ".toast-key", "width", "3px", "VaultSnackbar (key width)")
        assertDecl(themeCss, "theme.css", ".toast-key", "background", "var(--accent)", "VaultSnackbar (key)")
        assertDecl(themeCss, "theme.css", ".toast.warn .toast-key", "background", "var(--stale)", "VaultSnackbar (warn key)")
        // Switch: vaultSwitchColors.
        val sw = ".kit.view-settings .sw"
        assertDecl(kitCss, "kit.css", sw, "background", "var(--raised-2)", "vaultSwitchColors (uncheckedTrackColor)")
        assertDecl(kitCss, "kit.css", sw, "border", "1px solid var(--line)", "vaultSwitchColors (uncheckedBorderColor)")
        assertDecl(kitCss, "kit.css", "$sw::after", "background", "var(--dim)", "vaultSwitchColors (uncheckedThumbColor)")
        assertDecl(kitCss, "kit.css", "$sw[aria-checked=\"true\"]", "background", "var(--accent-glow)", "vaultSwitchColors (checkedTrackColor)")
        assertDecl(kitCss, "kit.css", "$sw[aria-checked=\"true\"]", "border-color", "var(--accent)", "vaultSwitchColors (checkedBorderColor)")
        assertDecl(kitCss, "kit.css", "$sw[aria-checked=\"true\"]::after", "background", "var(--accent)", "vaultSwitchColors (checkedThumbColor)")
        // Failure-hint command box: vaultCommandBox.
        assertDecl(kitCss, "kit.css", ".kit .hpanel .cmd", "background", "var(--bg)", "vaultCommandBox (background)")
        assertDecl(kitCss, "kit.css", ".kit .hpanel .cmd", "border", "1px solid var(--line)", "vaultCommandBox (border)")
        assertDecl(kitCss, "kit.css", ".kit .hpanel .cmd", "border-radius", "var(--r-s)", "vaultCommandBox (8dp corners)")
        assertDecl(kitCss, "kit.css", ".kit .hpanel .cmd", "padding", "var(--sp-2) 10px", "vaultCommandBox (8 x 10 padding)")
        // Figure tiles (Clients summary, Settings storage balance): VaultTileShape, vaultTileBorderColor.
        assertDecl(kitCss, "kit.css", ".kit .tile", "background", "var(--surface)", "the tile backgrounds (Surface)")
        assertDecl(kitCss, "kit.css", ".kit .tile", "border", "1px solid var(--line-soft)", "vaultTileBorderColor")
        assertDecl(kitCss, "kit.css", ".kit .tile", "border-radius", "var(--r-m)", "VaultTileShape (12dp)")
        assertDecl(kitCss, "kit.css", ".kit .tile", "padding", "14px var(--sp-4)", "VaultTilePadding (14 x 16)")
        assertDecl(kitCss, "kit.css", ".kit .tile.hi", "border-color", "color-mix(in srgb, var(--stale) 55%, var(--line))", "vaultTileBorderColor (hi)")
        assertEquals(
            "web `.kit .tile .v` now has a colour: VALUE drift -> the Settings tile value is Text on every tile",
            null,
            rule(kitCss, ".kit .tile .v")["color"],
        )
    }

    private fun argb(value: Long): Int = value.toInt()

    @Test
    fun `srgbMix is the browser's color-mix in srgb`() {
        // Hand-computed per channel: round(a + (b - a) * t).
        // accent #2ED9CE 10 % into surface #111D20: (19.9, 47.8, 49.4) -> #143031.
        assertEquals(argb(0xFF143031), srgbMix(VaultColors.Surface, VaultColors.Accent, 0.10f).toArgb())
        // run #F2CE5B 40 % into line #26363B: (119.6, 114.8, 71.8) -> #787348.
        assertEquals(argb(0xFF787348), srgbMix(VaultColors.Line, VaultColors.StatusRun, 0.40f).toArgb())
        assertEquals(argb(0xFF111D20), srgbMix(VaultColors.Surface, VaultColors.Accent, 0f).toArgb())
        assertEquals(argb(0xFF2ED9CE), srgbMix(VaultColors.Surface, VaultColors.Accent, 1f).toArgb())
        // The fraction is clamped, so a slip cannot push a channel out of range.
        assertEquals(argb(0xFF2ED9CE), srgbMix(VaultColors.Surface, VaultColors.Accent, 2f).toArgb())
        assertEquals(argb(0xFF111D20), srgbMix(VaultColors.Surface, VaultColors.Accent, -1f).toArgb())
    }

    @Test
    fun `the twin values are the web colours`() {
        // `.nav` --bg at 96 %: alpha round(0.96 * 255) = 245 = 0xF5.
        assertEquals(argb(0xF5081113), VaultNavBarContainer.toArgb())
        assertEquals(argb(0xFF143031), VaultFilterChipSelectedFill.toArgb())

        fun color(border: androidx.compose.foundation.BorderStroke): Int {
            assertEquals(1.dp, border.width)
            return (border.brush as SolidColor).value.toArgb()
        }
        assertEquals("`.kit .job` border", argb(0xFF787348), color(vaultJobCardBorder(held = false)))
        assertEquals("`.kit .job.held` border = LineSoft", argb(0xFF1D2A2E), color(vaultJobCardBorder(held = true)))
        assertEquals("`.jobcard` / `.hpanel` border = LineSoft", argb(0xFF1D2A2E), color(vaultPanelCardBorder()))
        // --stale #F07B2E at 35 %: alpha round(0.35 * 255) = 89 = 0x59.
        assertEquals("`.jobcard.bypass` border", argb(0x59F07B2EL), color(vaultPanelCardBorder(bypass = true)))
        // `.kit .ccard.warn`: stale #F07B2E 45 % into line #26363B = (128.9, 85.05, 53.15) -> #815535.
        assertEquals("`.ccard.warn` border", argb(0xFF815535), color(vaultClientCardBorder(warn = true)))
        assertEquals("`.ccard` border = LineSoft", argb(0xFF1D2A2E), color(vaultClientCardBorder(warn = false)))
        // `.jobcard.bypass` gradient over the sheet's Surface: stale 7 % into surface
        // = (32.61, 35.58, 32.98) -> #212421 at the top, Surface #111D20 at the bottom.
        assertEquals(listOf(argb(0xFF212421), argb(0xFF111D20)), VaultBypassCardStops.map { it.toArgb() })
        // `.nav-pip`: --run fill, the literal #08120F ink.
        assertEquals(argb(0xFFF2CE5B), VaultColors.StatusRun.toArgb())
        assertEquals(argb(0xFF08120F), VaultColors.StatusIconInk.toArgb())
        // `.sheet-backdrop` --bg at 62 %: alpha round(0.62 * 255) = 158 = 0x9E.
        assertEquals(argb(0x9E081113), VaultScrim.toArgb())
        assertEquals("`.pop` border = Line", argb(0xFF26363B), color(vaultMenuBorder()))
        // `.kit .tile.hi`: stale 55 % into line = (149.1, 91.95, 51.85) -> #955C34.
        assertEquals(argb(0xFF955C34), vaultTileBorderColor(hi = true).toArgb())
        assertEquals(argb(0xFF1D2A2E), vaultTileBorderColor(hi = false).toArgb())
        assertEquals("--r-l", RoundedCornerShape(18.dp), VaultDialogShape)
        assertEquals("--r-m", RoundedCornerShape(12.dp), VaultTileShape)
        assertEquals("--r-m", RoundedCornerShape(12.dp), VaultToastShape)
    }

    @Test
    fun `the tile and Clients card paddings are the web values, px = dp`() {
        fun sides(p: PaddingValues) = listOf(
            p.calculateTopPadding(), p.calculateRightPadding(LayoutDirection.Ltr),
            p.calculateBottomPadding(), p.calculateLeftPadding(LayoutDirection.Ltr),
        )
        // `.kit .tile{ padding:14px var(--sp-4) }`, --sp-4 = 16px.
        assertEquals(listOf(14.dp, 16.dp, 14.dp, 16.dp), sides(VaultTilePadding))
        // `.kit .ccard{ padding:18px var(--sp-5) }`, --sp-5 = 20px; 14px all round at a list of at most 460.
        assertEquals(listOf(14.dp, 14.dp, 14.dp, 14.dp), sides(vaultClientCardPadding(360.dp)))
        assertEquals(listOf(14.dp, 14.dp, 14.dp, 14.dp), sides(vaultClientCardPadding(460.dp)))
        assertEquals(listOf(18.dp, 20.dp, 18.dp, 20.dp), sides(vaultClientCardPadding(461.dp)))
        // Zone gap: --sp-4 = 16px up to a list of 700, the --sp-5 = 20px row gap above.
        assertEquals(16.dp, vaultClientCardZoneGap(360.dp))
        assertEquals(16.dp, vaultClientCardZoneGap(700.dp))
        assertEquals(20.dp, vaultClientCardZoneGap(701.dp))
    }
}
