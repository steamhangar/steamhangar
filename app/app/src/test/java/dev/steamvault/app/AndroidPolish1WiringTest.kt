package dev.steamvault.app

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * WP ANDROID-POLISH-1 (REDESIGN-1c review nit): the Overview facts show
 * "Last download" and "Confirmed current at" as formatted dates like the
 * installed line next to them, never the raw ISO string.
 */
class AndroidPolish1WiringTest {
    private val sheet = "src/main/java/dev/steamvault/app/ui/detail/GameDetailSheet.kt"

    private fun code(path: String): String =
        File(path).also { check(it.exists()) { "expected ${it.absolutePath}" } }
            .readText(Charsets.UTF_8)
            .replace(Regex("/\\*.*?\\*/", RegexOption.DOT_MATCHES_ALL), "")
            .replace(Regex("//.*"), "")

    @Test
    fun `MUTATION PIN -- Overview times go through formatTimestamp`() {
        val text = code(sheet)
        assertTrue(text.contains("stringResource(R.string.detail_last_download, formatTimestamp(it))"))
        assertTrue(text.contains("stringResource(R.string.detail_confirmed_current, formatTimestamp(detail.last_manifest_check))"))
        assertTrue(
            text.contains("stringResource(R.string.detail_confirmed_current_before_cleared, formatTimestamp(detail.last_manifest_check))"),
        )
        assertTrue("no raw ISO argument left", !text.contains("detail.last_manifest_check!!)"))
    }
}
