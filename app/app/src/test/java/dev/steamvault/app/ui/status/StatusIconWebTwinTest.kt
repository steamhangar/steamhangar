package dev.steamvault.app.ui.status

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Twin pins for WP APP-FIX-3 (docs/LEARNINGS.md: "twin config files need
 * twin pins"; the same read-the-other-source technique as
 * `DemoConfigDefaultsDriftTest`). The Android fall-through arrow restates
 * two web values; this test reads the web sources at test time so a change
 * on one side fails here instead of drifting.
 *
 * Failure messages name which of two edits applies: VALUE drift -> change
 * the Kotlin constant in StatusIconLogic.kt; GRAMMAR drift -> widen this
 * file's regex (the web line changed shape, not value).
 *
 * Plus the structural pins on StatusIcon.kt the JVM cannot render: the
 * running glyph is clipped to the disc and draws both arrows from
 * [downloadArrowOffsets], and no opacity is animated for the download glyph.
 */
class StatusIconWebTwinTest {

    private fun read(path: String): String {
        val file = File(path)
        check(file.exists()) { "expected a file at ${file.absolutePath}" }
        return file.readText(Charsets.UTF_8)
    }

    private fun stripComments(text: String): String =
        text.replace(Regex("/\\*.*?\\*/", RegexOption.DOT_MATCHES_ALL), "")
            .replace(Regex("//.*"), "")

    @Test
    fun `DOWNLOAD_FALL_PERIOD equals web status-icon js DOWNLOAD_FALL_PERIOD`() {
        val js = read("../../web/js/components/status-icon.js")
        val m = Regex("""export const DOWNLOAD_FALL_PERIOD\s*=\s*(\d+(?:\.\d+)?)\s*;""").find(js)
        assertNotNull(
            "GRAMMAR DRIFT: no 'export const DOWNLOAD_FALL_PERIOD = <n>;' in web/js/components/status-icon.js -- " +
                "widen THIS file's regex, do not touch StatusIconLogic.kt for this failure.",
            m,
        )
        assertEquals(
            "VALUE DRIFT: the web fall period changed -- update DOWNLOAD_FALL_PERIOD in StatusIconLogic.kt.",
            m!!.groupValues[1].toFloat(),
            DOWNLOAD_FALL_PERIOD,
            0f,
        )
    }

    @Test
    fun `DOWNLOAD_FALL_DURATION_MS equals the vault-dlfall duration in web theme css, linear`() {
        val css = read("../../web/css/theme.css")
        val m = Regex("""\.sic\.k-running \.dla\s*\{\s*animation:\s*vault-dlfall\s+(\d+(?:\.\d+)?)(ms|s)\s+(\S+)""").find(css)
        assertNotNull(
            "GRAMMAR DRIFT: no '.sic.k-running .dla{ animation:vault-dlfall <t> <easing> ...' rule in web/css/theme.css -- " +
                "widen THIS file's regex, do not touch StatusIconLogic.kt for this failure.",
            m,
        )
        val value = m!!.groupValues[1].toDouble()
        val ms = if (m.groupValues[2] == "s") (value * 1000).toInt() else value.toInt()
        assertEquals("VALUE DRIFT: update DOWNLOAD_FALL_DURATION_MS in StatusIconLogic.kt.", ms, DOWNLOAD_FALL_DURATION_MS)
        assertEquals("VALUE DRIFT: the web easing is no longer linear; StatusIcon.kt uses LinearEasing.", "linear", m.groupValues[3])
    }

    @Test
    fun `the vault-dlfall keyframes travel exactly one period, transform only`() {
        val css = read("../../web/css/theme.css")
        val body = Regex("""@keyframes vault-dlfall\s*\{(.*?)\n}""", RegexOption.DOT_MATCHES_ALL).find(css)?.groupValues?.get(1)
        assertNotNull("GRAMMAR DRIFT: no @keyframes vault-dlfall block in theme.css -- widen THIS file's regex.", body)
        assertTrue("VALUE DRIFT: web keyframes end at translateY(${DOWNLOAD_FALL_PERIOD.toInt()}px) no more", body!!.contains("translateY(${DOWNLOAD_FALL_PERIOD.toInt()}px)"))
        assertFalse("web keyframes animate opacity again -- StatusIcon.kt must not", body.contains("opacity"))
    }

    @Test
    fun `MUTATION PIN -- StatusIcon draws the RUNNING arrows clipped to the disc from downloadArrowOffsets, with no alpha`() {
        val code = stripComments(read("src/main/java/dev/steamvault/app/ui/status/StatusIcon.kt"))
        val start = code.indexOf("GlyphShape.DOWNLOAD -> {")
        val end = code.indexOf("GlyphShape.REFRESH -> {", start)
        check(start >= 0 && end > start) { "expected the DOWNLOAD branch before the REFRESH branch in StatusIcon.kt" }
        val branch = code.substring(start, end)
        val runningIdx = branch.indexOf("if (kind == StatusKind.RUNNING)")
        val clipIdx = branch.indexOf("clipPath(")
        val offsetsIdx = branch.indexOf("downloadArrowOffsets(downloadProgress)")
        assertTrue("the RUNNING gate is gone from the DOWNLOAD branch", runningIdx >= 0)
        assertTrue("the running arrows must be drawn inside clipPath(...) (web clip-path:circle(50%))", clipIdx > runningIdx)
        assertTrue("both arrows must come from downloadArrowOffsets(downloadProgress) inside the clip", offsetsIdx > clipIdx)
        assertFalse("the download glyph must not fade (no alpha) -- WP APP-FIX-3", branch.contains("alpha"))
        assertTrue(
            "the fall animation must use DOWNLOAD_FALL_DURATION_MS with LinearEasing",
            code.contains("tween(DOWNLOAD_FALL_DURATION_MS, easing = LinearEasing)"),
        )
    }
}
