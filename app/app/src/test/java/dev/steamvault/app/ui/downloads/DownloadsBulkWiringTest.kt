package dev.steamvault.app.ui.downloads

import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Wiring pins for WP WEB-FEAT-5's pixels (docs/LEARNINGS.md: pinning the
 * model layer proves nothing about the UI): the bar and the dialog must be
 * called from DownloadsScreen, and their buttons must reach the controller.
 */
class DownloadsBulkWiringTest {
    private fun code(name: String): String {
        val f = File("src/main/java/dev/steamvault/app/ui/downloads/$name")
        check(f.exists()) { "expected a source file at ${f.absolutePath}" }
        return f.readText(Charsets.UTF_8)
            .replace(Regex("/\\*.*?\\*/", RegexOption.DOT_MATCHES_ALL), "")
            .replace(Regex("//.*"), "")
    }

    private fun assertHas(text: String, needle: String, why: String) =
        assertTrue("$why: '$needle' is missing (deleted or moved)", text.contains(needle))

    @Test
    fun `the screen draws the bulk bar and the confirm dialog`() {
        val screen = code("DownloadsScreen.kt")
        assertHas(screen, "DownloadsBulkBar(controller, scope)", "bulk bar")
        assertHas(screen, "PauseAllDialog(controller, scope)", "pause-all dialog")
        assertHas(screen, "delay(controller.toastMs)", "bulk toast duration")
    }

    @Test
    fun `the buttons reach the controller`() {
        val bar = code("DownloadsBulkBar.kt")
        assertHas(bar, "controller.requestPauseAll()", "Pause all button")
        assertHas(bar, "controller.resumeAll(scope)", "Resume all button")
        assertHas(bar, "controller.confirmPauseAll(scope)", "dialog confirm")
        assertHas(bar, "controller.dismissPauseAll()", "dialog dismiss")
        assertHas(bar, "BulkWording.SCHEDULER_NOTE", "scheduler note")
        assertHas(bar, "bar.gcActive", "gc note")
    }
}
