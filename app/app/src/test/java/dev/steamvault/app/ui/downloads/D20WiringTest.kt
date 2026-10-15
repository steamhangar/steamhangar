package dev.steamvault.app.ui.downloads

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Wiring pins for WP D20's pixels (docs/LEARNINGS.md: pinning the model layer
 * proves nothing about the UI): the row buttons must reach the controller, the
 * controller must send the move to the repository, and the old "not built yet"
 * hint must be gone.
 */
class D20WiringTest {
    private fun code(path: String): String {
        val f = File("src/main/java/dev/steamvault/app/$path")
        check(f.exists()) { "expected a source file at ${f.absolutePath}" }
        return f.readText(Charsets.UTF_8)
            .replace(Regex("/\\*.*?\\*/", RegexOption.DOT_MATCHES_ALL), "")
            .replace(Regex("//.*"), "")
    }

    private fun squash(s: String) = s.replace(Regex("\\s+"), " ")

    private fun assertHas(text: String, needle: String, why: String) =
        assertTrue("$why: '$needle' is missing (deleted or moved)", squash(text).contains(squash(needle)))

    @Test
    fun `the screen routes the row buttons to the controller`() {
        val screen = code("ui/downloads/DownloadsScreen.kt")
        assertHas(screen, "controller.moveQueued(scope, model.jobId, move)", "row callback reaches the controller")
        assertHas(screen, "onMove(QueueMove.UP)", "up button")
        assertHas(screen, "onMove(QueueMove.DOWN)", "down button")
        assertHas(screen, "R.string.downloads_queue_move_up", "up button is labelled")
        assertHas(screen, "R.string.downloads_queue_move_down", "down button is labelled")
        assertHas(screen, "moveBusy = controller.moveInFlight", "buttons are disabled while a move is in flight")
    }

    @Test
    fun `the controller sends the move and the client posts it`() {
        assertHas(code("ui/downloads/DownloadsController.kt"), "jobsRepository.moveJob(jobId, next.indexOf(jobId))", "move request")
        assertHas(code("repo/JobsRepository.kt"), "client.moveJob(id, position)", "repository forwards to the client")
        assertHas(code("net/VaultApiClient.kt"), "post(\"/v1/jobs/\$id/move\", MoveJobRequest(position))", "endpoint")
    }

    @Test
    fun `the old not-built-yet hint is gone and the new wording is a string resource`() {
        val xml = File("src/main/res/values/strings.xml").readText(Charsets.UTF_8)
        assertFalse("stale hint", xml.contains("not built yet", ignoreCase = true))
        assertTrue(xml.contains("name=\"downloads_queue_move_up\""))
        assertTrue(xml.contains("name=\"downloads_queue_move_down\""))
    }
}
