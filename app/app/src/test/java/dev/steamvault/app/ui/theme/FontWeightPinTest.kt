package dev.steamvault.app.ui.theme

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * WP WEIGHTS-1 (rc23, operator decision 2026-11-21), Android twin of
 * web/tests/css-font-weights.test.js: the app uses only the weights 400,
 * 500 and 700 (DESIGN.md "Type": 400 body, 500 labels / buttons / values /
 * emphasis, 700 the primary action and titles). This source scan over
 * `src/main/java` fails for any other `FontWeight` reference
 * (`SemiBold`, `W600`, `FontWeight(600)`, `Light`, `Black` ...), naming file
 * and line. The minimum count keeps it from passing vacuously.
 */
class FontWeightPinTest {

    private val allowed = setOf("Normal", "Medium", "Bold", "W400", "W500", "W700", "(400)", "(500)", "(700)")

    private fun stripComments(code: String): String =
        code.replace(Regex("/\\*.*?\\*/", RegexOption.DOT_MATCHES_ALL), "")
            .replace(Regex("(?m)^\\s*//.*$"), "")
            .replace(Regex("\\s//[^\"\\n]*$", RegexOption.MULTILINE), "")

    private data class Ref(val where: String, val weight: String)

    /** Every `FontWeight.X` or `FontWeight(N)` in [code]. */
    private fun refs(name: String, code: String): List<Ref> {
        // Line numbers count in the stripped text (a block comment shifts them).
        val stripped = stripComments(code)
        return Regex("""\bFontWeight(?:\.(\w+)|\s*(\(\s*\d+\s*\)))""").findAll(stripped).map { m ->
            val weight = m.groupValues[1].ifEmpty { m.groupValues[2].replace(Regex("\\s"), "") }
            Ref("$name:${stripped.substring(0, m.range.first).count { it == '\n' } + 1}", weight)
        }.toList()
    }

    @Test
    fun `src main uses only FontWeight Normal, Medium and Bold (400, 500, 700)`() {
        val root = File("src/main/java")
        check(root.isDirectory) { "expected the main sources at ${root.absolutePath}" }
        val found = root.walkTopDown().filter { it.isFile && it.extension == "kt" }
            .flatMap { refs(it.name, it.readText(Charsets.UTF_8)) }.toList()
        assertTrue("expected at least 10 FontWeight references, found ${found.size}", found.size >= 10)
        val bad = found.filter { it.weight !in allowed }.map { "${it.where} FontWeight.${it.weight}" }
        assertEquals("weights outside 400/500/700 (WEIGHTS-1, DESIGN.md \"Type\")", emptyList<String>(), bad)
    }

    @Test
    fun `the scan catches SemiBold, W600 and FontWeight(600) and skips comments`() {
        val code = """
            val a = FontWeight.SemiBold
            val b = FontWeight.W600
            val c = FontWeight( 600 )
            val d = FontWeight.Medium
            // FontWeight.Black in a comment
        """.trimIndent()
        assertEquals(listOf("SemiBold", "W600", "(600)", "Medium"), refs("x.kt", code).map { it.weight })
    }
}
