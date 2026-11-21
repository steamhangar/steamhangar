package dev.steamvault.app.ui.theme

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * WP CONTRAST-1 (LINE-CONTRAST-1, audit 2026-11-20), Android twin of the web
 * `--line-strong` borders, the selected-segment ring and the `.fchip`
 * accent border (web/tests/css-contrast.test.js).
 *
 * Material 3 draws the unfocused OutlinedTextField border and the
 * SegmentedButton border in the scheme's `outline`, which stays
 * [VaultColors.Line] (1.52:1 on Bg) for the outlined buttons. So every
 * OutlinedTextField / SegmentedButton call in `src/main` must pass the
 * LineStrong colours from ControlColors.kt; this source scan fails for a
 * call that does not, naming file and line. The minimum counts keep the
 * scan from passing vacuously if the grammar of the calls changes.
 */
class ControlColorsPinTest {

    private fun stripComments(code: String): String =
        code.replace(Regex("/\\*.*?\\*/", RegexOption.DOT_MATCHES_ALL), "")
            .replace(Regex("(?m)^\\s*//.*$"), "")
            .replace(Regex("\\s//[^\"\\n]*$", RegexOption.MULTILINE), "")

    /** Index just past the paren that closes the one at [open], skipping string literals. */
    private fun closingParen(code: String, open: Int): Int {
        var depth = 0
        var i = open
        var inString = false
        while (i < code.length) {
            val c = code[i]
            if (inString) {
                if (c == '\\') i++ else if (c == '"') inString = false
            } else when (c) {
                '"' -> inString = true
                '(' -> depth++
                ')' -> { depth--; if (depth == 0) return i + 1 }
            }
            i++
        }
        error("unbalanced parentheses after index $open")
    }

    private data class Call(val file: String, val line: Int, val args: String)

    private fun calls(name: String): List<Call> {
        val root = File("src/main/java")
        check(root.isDirectory) { "expected the main sources at ${root.absolutePath}" }
        val pattern = Regex("""(?<![\w.])$name\(""")
        return root.walkTopDown().filter { it.isFile && it.extension == "kt" }.flatMap { file ->
            val code = stripComments(file.readText(Charsets.UTF_8))
            pattern.findAll(code).map { m ->
                val open = m.range.last
                Call(file.name, code.substring(0, m.range.first).count { it == '\n' } + 1, code.substring(open, closingParen(code, open)))
            }.toList()
        }.toList()
    }

    @Test
    fun `every OutlinedTextField passes the LineStrong text-field colours`() {
        val found = calls("OutlinedTextField")
        assertTrue("expected at least 8 OutlinedTextField calls, found ${found.size}", found.size >= 8)
        for (call in found) {
            assertTrue(
                "${call.file}:${call.line} OutlinedTextField without `colors = vaultTextFieldColors()` (WP CONTRAST-1)",
                Regex("""\bcolors\s*=\s*vaultTextFieldColors\(\)""").containsMatchIn(call.args),
            )
        }
    }

    @Test
    fun `every SegmentedButton passes the LineStrong segmented-button colours`() {
        val found = calls("SegmentedButton")
        assertTrue("expected at least 5 SegmentedButton calls, found ${found.size}", found.size >= 5)
        for (call in found) {
            assertTrue(
                "${call.file}:${call.line} SegmentedButton without `colors = vaultSegmentedButtonColors()` (WP CONTRAST-1)",
                Regex("""\bcolors\s*=\s*vaultSegmentedButtonColors\(\)""").containsMatchIn(call.args),
            )
        }
    }

    @Test
    fun `the control colours draw their borders in LineStrong`() {
        val code = stripComments(File("src/main/java/dev/steamvault/app/ui/theme/ControlColors.kt").readText(Charsets.UTF_8))
        assertTrue(code.contains("OutlinedTextFieldDefaults.colors(unfocusedBorderColor = VaultColors.LineStrong)"))
        assertTrue(code.contains("inactiveBorderColor = VaultColors.LineStrong,"))
        // The selected segment's 3:1 state edge (twin of the web `.seg` --dim-2 ring).
        assertTrue(Regex("""\bactiveBorderColor = VaultColors\.Dim2,""").containsMatchIn(code))
        assertTrue(code.contains("selectedBorderColor = VaultColors.Dim2,") && code.contains("selectedBorderWidth = 1.dp,"))
        // `outline` stays Line: outlined buttons keep the separator line, like the web `.kit .btn`.
        val theme = stripComments(File("src/main/java/dev/steamvault/app/ui/theme/Theme.kt").readText(Charsets.UTF_8))
        assertTrue(theme.contains("outline = VaultColors.Line,"))
    }

    @Test
    fun `every FilterChip passes a 3-to-1 selected border (range chips Dim2, filter chips Accent)`() {
        val found = calls("FilterChip")
        assertTrue("expected at least 3 FilterChip calls, found ${found.size}", found.size >= 3)
        val rangeBorder = Regex("""\bborder\s*=\s*vaultRangeChipBorder\(selected = """)
        val filterBorder = Regex("""\bborder\s*=\s*vaultFilterChipBorder\(selected = """)
        for (call in found) {
            // RangeChips.kt is the twin of the web `.seg rangesegs` (Dim2 ring);
            // every other FilterChip is a twin of the web `.fchip` (accent border).
            val (expected, helper) = if (call.file == "RangeChips.kt") (rangeBorder to "vaultRangeChipBorder") else (filterBorder to "vaultFilterChipBorder")
            assertTrue(
                "${call.file}:${call.line} FilterChip without `border = $helper(selected = ...)` (WP CONTRAST-1)",
                expected.containsMatchIn(call.args),
            )
        }
        assertEquals("RangeChips.kt has one FilterChip call", 1, found.count { it.file == "RangeChips.kt" })
    }

    @Test
    fun `every filter chip passes the accent selected fill, range chips keep the theme segment colours`() {
        // WP ANDROID-SHEET-BG-1: web `.fchip[aria-pressed="true"]` (accent 10 % fill, accent label);
        // the range chips are the web `.seg` twin, whose raised-2 / text pair is the theme's secondaryContainer.
        val found = calls("FilterChip")
        val colors = Regex("""\bcolors\s*=\s*vaultFilterChipColors\(\)""")
        for (call in found) {
            if (call.file == "RangeChips.kt") {
                assertTrue("${call.file}:${call.line} a range chip passes no own colours (web `.seg`)", !call.args.contains("colors ="))
            } else {
                assertTrue("${call.file}:${call.line} FilterChip without `colors = vaultFilterChipColors()` (ANDROID-SHEET-BG-1)", colors.containsMatchIn(call.args))
            }
        }
    }

    @Test
    fun `the bottom bar is the web nav twin`() {
        val bars = calls("NavigationBar")
        assertEquals("expected one NavigationBar call", 1, bars.size)
        assertTrue(
            "${bars[0].file}:${bars[0].line} NavigationBar needs `containerColor = VaultNavBarContainer` and " +
                "`modifier = Modifier.vaultNavBarTopBorder()` (web `.nav`, ANDROID-SHEET-BG-1)",
            bars[0].args.contains("containerColor = VaultNavBarContainer") && bars[0].args.contains("Modifier.vaultNavBarTopBorder()"),
        )
        val items = calls("NavigationBarItem")
        assertTrue("expected a NavigationBarItem call", items.isNotEmpty())
        for (item in items) {
            assertTrue(
                "${item.file}:${item.line} NavigationBarItem without `colors = vaultNavBarItemColors()` (web `.nav-btn`)",
                Regex("""\bcolors\s*=\s*vaultNavBarItemColors\(\)""").containsMatchIn(item.args),
            )
        }
    }

    @Test
    fun `every Card passes web-twin colours and border`() {
        // WP ANDROID-SHEET-BG-1: Material's Card fill (surfaceContainerHighest) and missing
        // border are not the web cards; each card names its twin helper pair.
        val found = calls("Card")
        assertTrue("expected at least 3 Card calls, found ${found.size}", found.size >= 3)
        val pair = Regex("""\bcolors\s*=\s*vault(Job|Panel)CardColors\([^)]*\)[\s\S]*\bborder\s*=\s*vault(Job|Panel)CardBorder\(""")
        for (call in found) {
            val m = pair.find(call.args)
            assertTrue("${call.file}:${call.line} Card without `colors = vault*CardColors()` + `border = vault*CardBorder(...)`", m != null)
            assertEquals("${call.file}:${call.line} colours and border of one twin", m!!.groupValues[1], m.groupValues[2])
        }
        fun cardIn(file: String, dir: String, function: String): String {
            val source = stripComments(File("src/main/java/dev/steamvault/app/ui/$dir/$file").readText(Charsets.UTF_8))
            val start = source.indexOf("private fun $function(")
            assertTrue("$file has no `private fun $function(`", start >= 0)
            val next = source.indexOf("\nprivate fun ", start + 1).let { if (it < 0) source.length else it }
            val body = source.substring(start, next)
            return found.filter { it.file == file }.map { it.args }.singleOrNull { body.contains(it) }
                ?: error("$file $function: expected exactly one Card call in its body")
        }
        // Web `.kit .job` / `.job.held` (components/job-row.js).
        assertTrue(cardIn("DownloadsScreen.kt", "downloads", "JobCard").contains("border = vaultJobCardBorder(held = model.mode == JobCardMode.HELD)"))
        // Web `.kit .hpanel`, the panel holding `.failhint`.
        assertTrue(cardIn("DownloadsScreen.kt", "downloads", "FailureHintBlock").contains("border = vaultPanelCardBorder()"))
        // Web `.jobcard` / `.jobcard.bypass` (components/clients-sheet.js): border, and the
        // gradient ground painted in the card's own shape behind a transparent container.
        val clientRow = cardIn("ClientsSheet.kt", "clients", "ClientRow")
        for (arg in listOf(
            "border = vaultPanelCardBorder(bypass = model.bypassSuspected)",
            "colors = vaultPanelCardColors(bypass = model.bypassSuspected)",
            ".vaultPanelCardGround(model.bypassSuspected, shape)",
            "shape = shape,",
        )) assertTrue("ClientsSheet.kt ClientRow: the Card lacks `$arg` (web `.jobcard.bypass`, ANDROID-SHEET-BG-1)", clientRow.contains(arg))
    }

    @Test
    fun `the composable twin helpers carry the web tokens`() {
        val code = stripComments(File("src/main/java/dev/steamvault/app/ui/theme/ControlColors.kt").readText(Charsets.UTF_8))
        fun body(function: String): String {
            val start = listOf("fun $function(", "fun Modifier.$function(").map { code.indexOf(it) }.filter { it >= 0 }.minOrNull() ?: -1
            assertTrue("ControlColors.kt has no `fun $function(`", start >= 0)
            val next = Regex("""\n(@Composable\n)?(internal |private )?(fun|val) """).find(code, start + 1)?.range?.first ?: code.length
            return code.substring(start, next)
        }
        val nav = body("vaultNavBarItemColors")
        for (arg in listOf(
            "selectedIconColor = VaultColors.Accent,", "selectedTextColor = VaultColors.Accent,",
            "indicatorColor = Color.Transparent,",
            "unselectedIconColor = VaultColors.Dim2,", "unselectedTextColor = VaultColors.Dim2,",
        )) assertTrue("vaultNavBarItemColors lacks `$arg` (web `.nav-btn`)", nav.contains(arg))
        assertTrue(body("vaultNavBarTopBorder").contains("drawLine(VaultColors.LineSoft,"))
        assertTrue(body("vaultJobCardColors").contains("containerColor = VaultColors.Raised, contentColor = VaultColors.Text"))
        val panel = body("vaultPanelCardColors")
        assertTrue(
            "vaultPanelCardColors: Surface (transparent on a bypass row, which paints the gradient) and Text",
            panel.contains("containerColor = if (bypass) Color.Transparent else VaultColors.Surface,") &&
                panel.contains("contentColor = VaultColors.Text,"),
        )
        assertTrue(code.contains("val VaultBypassCardGround: Brush = Brush.verticalGradient(VaultBypassCardStops)"))
        assertTrue(body("vaultPanelCardGround").contains("if (bypass) this.background(VaultBypassCardGround, shape) else this"))
        val chip = body("vaultFilterChipColors")
        for (arg in listOf(
            // Unselected: web `.fchip{ color:var(--dim); background:var(--surface) }`.
            "containerColor = VaultColors.Surface,", "labelColor = VaultColors.Dim,", "iconColor = VaultColors.Dim,",
            "selectedContainerColor = VaultFilterChipSelectedFill,", "selectedLabelColor = VaultColors.Accent,",
            "selectedLeadingIconColor = VaultColors.Accent,", "selectedTrailingIconColor = VaultColors.Accent,",
        )) assertTrue("vaultFilterChipColors lacks `$arg` (web `.fchip[aria-pressed]`)", chip.contains(arg))
    }

    @Test
    fun `the filter chip border is a 1dp Accent edge on the selected chip`() {
        val code = stripComments(File("src/main/java/dev/steamvault/app/ui/theme/ControlColors.kt").readText(Charsets.UTF_8))
        val body = code.substring(code.indexOf("fun vaultFilterChipBorder("))
        assertTrue(body.contains("selectedBorderColor = VaultColors.Accent,") && body.contains("selectedBorderWidth = 1.dp,"))
        // ANDROID-SHEET-BG-1: the unselected edge is web `.fchip{ border:1px solid var(--line-soft) }`.
        assertTrue("vaultFilterChipBorder lacks `borderColor = VaultColors.LineSoft,`", body.substringBefore("fun vaultFieldButtonBorder").contains("borderColor = VaultColors.LineSoft,"))
    }

    @Test
    fun `the Downloads nav pip and the Clients card are web twins`() {
        // Web `.nav-pip`: --run fill, sic ink (#08120F = StatusIconInk), mono, 700.
        val nav = stripComments(File("src/main/java/dev/steamvault/app/ui/nav/BottomNavBar.kt").readText(Charsets.UTF_8))
        val badges = calls("Badge").filter { it.file == "BottomNavBar.kt" }
        assertEquals("BottomNavBar.kt has one Badge", 1, badges.size)
        for (arg in listOf("containerColor = VaultColors.StatusRun", "contentColor = VaultColors.StatusIconInk")) {
            assertTrue("BottomNavBar.kt Badge lacks `$arg` (web `.nav-pip`, ANDROID-SHEET-BG-1)", badges.single().args.contains(arg))
        }
        // The count is the Badge's trailing content lambda, up to the BadgedBox's own icon.
        val badgeStart = nav.indexOf("Badge(containerColor")
        val badgeEnd = nav.indexOf("{ icon() }", badgeStart)
        assertTrue("BottomNavBar.kt: expected `Badge(containerColor ...` before `{ icon() }`", badgeStart >= 0 && badgeEnd > badgeStart)
        val badge = nav.substring(badgeStart, badgeEnd)
        for (arg in listOf("pendingJobsCount.coerceAtMost(99).toString()", "fontFamily = FontFamily.Monospace", "fontWeight = FontWeight.Bold")) {
            assertTrue("BottomNavBar.kt pip text lacks `$arg` (web `.nav-pip` mono 700)", badge.contains(arg))
        }
        // Web `.kit .ccard` / `.ccard.warn` (components/client-card.js adds warn on outdated or bypass).
        val views = stripComments(File("src/main/java/dev/steamvault/app/ui/clients/ClientCardViews.kt").readText(Charsets.UTF_8))
        assertTrue(
            "ClientCardViews.kt ClientCard needs `border = vaultClientCardBorder(warn = model.outdatedText != null || model.health?.bypass == true)`",
            views.contains("border = vaultClientCardBorder(warn = model.outdatedText != null || model.health?.bypass == true),"),
        )
    }

    @Test
    fun `the select-twin buttons (sort, genre and rating pickers) draw a LineStrong border`() {
        val code = stripComments(File("src/main/java/dev/steamvault/app/ui/theme/ControlColors.kt").readText(Charsets.UTF_8))
        assertTrue(code.contains("fun vaultFieldButtonBorder(): BorderStroke = BorderStroke(1.dp, VaultColors.LineStrong)"))
        for ((file, function) in listOf("LibraryScreen.kt" to "SortRow", "LibraryMetaPanel.kt" to "PickerButton")) {
            val source = stripComments(File("src/main/java/dev/steamvault/app/ui/library/$file").readText(Charsets.UTF_8))
            val start = source.indexOf("private fun $function(")
            assertTrue("$file has no `private fun $function(`", start >= 0)
            val next = source.indexOf("\nprivate fun ", start + 1).let { if (it < 0) source.length else it }
            val body = source.substring(start, next)
            val buttons = calls("OutlinedButton").filter { it.file == file }.map { it.args }.filter { body.contains(it) }
            assertEquals("$file $function has one OutlinedButton", 1, buttons.size)
            assertTrue(
                "$file $function: the OutlinedButton needs `border = vaultFieldButtonBorder()` (WP CONTRAST-1)",
                buttons.single().contains("border = vaultFieldButtonBorder()"),
            )
        }
    }

    @Test
    fun `dialogs, sheets, menus, the switch and toasts take the web twin helpers`() {
        // WP ANDROID-SHEET-BG-1: web `.dialog`, `.sheet-backdrop` / `.sheet .grab`, `.pop`, `.sw`, `.toast`.
        val dialogs = calls("AlertDialog")
        assertTrue("expected at least 13 AlertDialog calls, found ${dialogs.size}", dialogs.size >= 13)
        for (call in dialogs) {
            assertTrue(
                "${call.file}:${call.line} AlertDialog needs `shape = VaultDialogShape` and `modifier = Modifier.vaultDialogBorder()` (web `.dialog`)",
                call.args.contains("shape = VaultDialogShape") && call.args.contains("modifier = Modifier.vaultDialogBorder()"),
            )
        }
        val sheets = calls("ModalBottomSheet")
        assertEquals("expected the two ModalBottomSheets (detail, Clients)", 2, sheets.size)
        for (call in sheets) {
            assertTrue(
                "${call.file}:${call.line} ModalBottomSheet needs `scrimColor = VaultScrim` and `dragHandle = { VaultSheetDragHandle() }`",
                call.args.contains("scrimColor = VaultScrim") && call.args.contains("dragHandle = { VaultSheetDragHandle() }"),
            )
        }
        val menus = calls("DropdownMenu")
        assertTrue("expected at least 3 DropdownMenu calls, found ${menus.size}", menus.size >= 3)
        for (call in menus) {
            assertTrue("${call.file}:${call.line} DropdownMenu without `border = vaultMenuBorder()` (web `.pop`)", call.args.contains("border = vaultMenuBorder()"))
        }
        val switches = calls("Switch")
        assertTrue("expected a Switch call", switches.isNotEmpty())
        for (call in switches) {
            assertTrue("${call.file}:${call.line} Switch without `colors = vaultSwitchColors()` (web `.sw`)", call.args.contains("colors = vaultSwitchColors()"))
        }
        // Every toast goes through VaultSnackbar (border + key line); no bare Material Snackbar elsewhere.
        val root = File("src/main/java")
        val bare = Regex("""(?<![\w.])Snackbar\s*[({]""")
        val offenders = root.walkTopDown().filter { it.isFile && it.extension == "kt" && it.name != "VaultSnackbar.kt" }
            .filter { bare.containsMatchIn(stripComments(it.readText(Charsets.UTF_8))) }.map { it.name }.toList()
        assertTrue("bare Snackbar in $offenders: use VaultSnackbar (web `.toast`)", offenders.isEmpty())
        val toasts = root.walkTopDown().filter { it.isFile && it.extension == "kt" }
            .sumOf { Regex("""(?<![\w.])VaultSnackbar\(""").findAll(stripComments(it.readText(Charsets.UTF_8))).count() }
        assertTrue("expected at least 5 VaultSnackbar calls plus its declaration, found $toasts", toasts >= 6)
    }

    @Test
    fun `the toast, sheet, dialog, switch and command-box helpers carry the web tokens`() {
        val toast = stripComments(File("src/main/java/dev/steamvault/app/ui/theme/VaultSnackbar.kt").readText(Charsets.UTF_8))
        assertTrue(
            "VaultSnackbar: `.toast` edge and shape",
            toast.contains(".border(1.dp, VaultColors.Line, VaultToastShape),") && toast.contains("shape = VaultToastShape,"),
        )
        // SNACKBAR-LIVE-1: every toast is a polite live region (TalkBack reads it), set once here.
        assertTrue(
            "VaultSnackbar: the Snackbar modifier needs `.semantics { liveRegion = LiveRegionMode.Polite }`",
            Regex("""modifier = modifier\s*\.semantics \{ liveRegion = LiveRegionMode\.Polite \}""").containsMatchIn(toast),
        )
        for (call in calls("VaultSnackbar")) {
            assertTrue(
                "${call.file}:${call.line} VaultSnackbar call sets its own live region; VaultSnackbar already does (SNACKBAR-LIVE-1)",
                !call.args.contains("liveRegion"),
            )
        }
        assertTrue("VaultSnackbar: `.toast-key` 3px", toast.contains(".width(3.dp)") && toast.contains(".fillMaxHeight()"))
        assertTrue("VaultSnackbar: key accent, stale on warn", toast.contains("if (warn) VaultColors.StatusStale else VaultColors.Accent"))
        val code = stripComments(File("src/main/java/dev/steamvault/app/ui/theme/ControlColors.kt").readText(Charsets.UTF_8))
        for (literal in listOf(
            "BottomSheetDefaults.DragHandle(width = 38.dp, height = 4.dp, color = VaultColors.Line)",
            "val VaultDialogShape: Shape = RoundedCornerShape(18.dp)",
            "fun Modifier.vaultDialogBorder(): Modifier = this.border(1.dp, VaultColors.Line, VaultDialogShape)",
            "uncheckedThumbColor = VaultColors.Dim,", "uncheckedTrackColor = VaultColors.Raised2,", "uncheckedBorderColor = VaultColors.Line,",
            "checkedThumbColor = VaultColors.Accent,", "checkedTrackColor = VaultColors.AccentGlow,", "checkedBorderColor = VaultColors.Accent,",
            "this.background(VaultColors.Bg, RoundedCornerShape(8.dp))",
            ".border(1.dp, VaultColors.Line, RoundedCornerShape(8.dp))",
            ".padding(horizontal = 10.dp, vertical = 8.dp)",
        )) assertTrue("ControlColors.kt lacks `$literal` (ANDROID-SHEET-BG-1 web twin)", code.contains(literal))
        val downloads = stripComments(File("src/main/java/dev/steamvault/app/ui/downloads/DownloadsScreen.kt").readText(Charsets.UTF_8))
        val hint = downloads.substring(downloads.indexOf("private fun FailureHintBlock("))
        assertTrue("FailureHintBlock: the command box is `.vaultCommandBox()` (web `.kit .hpanel .cmd`)", hint.substringBefore("\nprivate fun ").contains(".vaultCommandBox()"))
    }

    @Test
    fun `the figure tiles and the Clients card corners are web twins`() {
        fun body(path: String, function: String): String {
            val source = stripComments(File("src/main/java/dev/steamvault/app/ui/$path").readText(Charsets.UTF_8))
            val start = source.indexOf("private fun $function(")
            assertTrue("$path has no `private fun $function(`", start >= 0)
            val next = source.indexOf("\nprivate fun ", start + 1).let { if (it < 0) source.length else it }
            return source.substring(start, next)
        }
        // Clients summary tiles (web `.kit .tile` / `.tile.hi`).
        assertTrue(body("clients/ClientCardViews.kt", "TileView").contains(".border(1.dp, vaultTileBorderColor(tile.hi), VaultTileShape)"))
        // Settings storage-balance tiles: Surface, the tile edge, and the value in Text on every tile (`.tile .v`).
        val balance = body("settings/SettingsScreen.kt", "StorageBalanceCard")
        for (arg in listOf(".background(VaultColors.Surface, VaultTileShape)", ".border(1.dp, vaultTileBorderColor(tile.hi), VaultTileShape)", "color = VaultColors.Text,")) {
            assertTrue("SettingsScreen.kt StorageBalanceCard lacks `$arg` (web `.kit .tile`)", balance.contains(arg))
        }
        assertTrue("the balance tile value must not turn stale on `hi` (web `.tile .v` keeps --text)", !balance.contains("if (tile.hi) VaultColors.StatusStale"))
        val tiles = stripComments(File("src/main/java/dev/steamvault/app/ui/theme/ControlColors.kt").readText(Charsets.UTF_8))
        assertTrue(tiles.contains("if (hi) srgbMix(VaultColors.Line, VaultColors.StatusStale, 0.55f) else VaultColors.LineSoft"))
        // Clients card corners: web `.kit .ccard{ border-radius:var(--r-l) }` (18px).
        val views = stripComments(File("src/main/java/dev/steamvault/app/ui/clients/ClientCardViews.kt").readText(Charsets.UTF_8))
        assertTrue("ClientCardViews.kt: `private val CardShape = RoundedCornerShape(18.dp)`", views.contains("private val CardShape = RoundedCornerShape(18.dp)"))
        // Paddings: web `.kit .tile` (14 x 16) and `.kit .ccard` (18 x 20, 14 at a list of at most 460).
        assertTrue("TileView: `.padding(VaultTilePadding)`", body("clients/ClientCardViews.kt", "TileView").contains(".padding(VaultTilePadding)"))
        assertTrue("StorageBalanceCard: `.padding(VaultTilePadding)`", balance.contains(".padding(VaultTilePadding)"))
        val card = views.substring(views.indexOf("internal fun ClientCard("))
        assertTrue(
            "ClientCard: the padding comes from `vaultClientCardPadding(maxWidth)` (web `.kit .ccard` container query)",
            card.contains("val cardPadding = vaultClientCardPadding(maxWidth)") && card.contains("Column(modifier = Modifier.padding(cardPadding)"),
        )
        assertTrue(
            "ClientCard: the zone gap is `Arrangement.spacedBy(vaultClientCardZoneGap(maxWidth))` (web `.kit .ccard` gap)",
            card.contains("verticalArrangement = Arrangement.spacedBy(vaultClientCardZoneGap(maxWidth))"),
        )
        val pads = stripComments(File("src/main/java/dev/steamvault/app/ui/theme/ControlColors.kt").readText(Charsets.UTF_8))
        assertTrue(pads.contains("val VaultTilePadding: PaddingValues = PaddingValues(horizontal = 16.dp, vertical = 14.dp)"))
        assertTrue(pads.contains("fun vaultClientCardZoneGap(cardWidth: Dp): Dp = if (cardWidth <= 700.dp) 16.dp else 20.dp"))
        assertTrue(pads.contains("if (cardWidth <= 460.dp) PaddingValues(14.dp) else PaddingValues(horizontal = 20.dp, vertical = 18.dp)"))
    }
}
