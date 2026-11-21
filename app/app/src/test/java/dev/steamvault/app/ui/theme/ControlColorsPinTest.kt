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
    fun `the filter chip border is a 1dp Accent edge on the selected chip`() {
        val code = stripComments(File("src/main/java/dev/steamvault/app/ui/theme/ControlColors.kt").readText(Charsets.UTF_8))
        val body = code.substring(code.indexOf("fun vaultFilterChipBorder("))
        assertTrue(body.contains("selectedBorderColor = VaultColors.Accent,") && body.contains("selectedBorderWidth = 1.dp,"))
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
}
