package dev.steamvault.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Structural pins for WP CAP-503-FIX-1b's model-to-pixel wiring
 * (docs/learnings/testing.md: "pinning the pure/model layer proves NOTHING
 * about the pixels"). The texts are pinned in `ClientPriorityTest` and
 * against the web in `ClientPriorityWebTwinTest`, the controller in
 * `DownloadsControllerClientPriorityTest`; these pins prove the Downloads
 * screen polls the hold and shows each text. Same comment-stripped source
 * scan, strong generation, as `AndroidDl1WiringTest`: the call's nearest
 * enclosing function must be the intended one, and the message tells
 * "deleted" from "moved".
 */
class ClientPriorityWiringTest {

    private fun read(path: String): String {
        val file = File(path)
        check(file.exists()) { "expected a source file at ${file.absolutePath}" }
        return file.readText(Charsets.UTF_8)
    }

    private fun stripComments(text: String): String =
        text.replace(Regex("/\\*.*?\\*/", RegexOption.DOT_MATCHES_ALL), "")
            .replace(Regex("//.*"), "")

    private val FUN = Regex("""(?:@Composable\s+)?(?:private\s+)?(?:suspend\s+)?fun\s+(?:<[^>]+>\s+)?(\w+)""")

    private fun nearestEnclosingFun(code: String, callIdx: Int): String? =
        FUN.findAll(code)
            .map { it.range.first to it.groupValues[1] }
            .filter { (start, _) -> start < callIdx }
            .maxByOrNull { (start, _) -> start }
            ?.second

    private fun callIndex(code: String, call: String): Int {
        var from = 0
        while (true) {
            val idx = code.indexOf(call, from)
            if (idx < 0) return -1
            if (!code.substring(0, idx).trimEnd().endsWith("fun")) return idx
            from = idx + call.length
        }
    }

    private fun assertCalledFrom(path: String, call: String, expectedFun: String) {
        val code = stripComments(read(path))
        val idx = callIndex(code, call)
        assertTrue("$path: '$call' is DELETED -- the wiring is gone", idx >= 0)
        assertEquals(
            "$path: '$call' MOVED -- its nearest enclosing function is no longer $expectedFun",
            expectedFun,
            nearestEnclosingFun(code, idx),
        )
    }

    private val base = "src/main/java/dev/steamvault/app"
    private val downloads = "$base/ui/downloads/DownloadsScreen.kt"
    private val controller = "$base/ui/downloads/DownloadsController.kt"
    private val repo = "$base/repo/JobsRepository.kt"

    @Test
    fun `MUTATION PIN -- the screen polls the hold while started and the controller asks the API`() {
        assertCalledFrom(downloads, "controller.pollClientPriorityForever()", "DownloadsScreen")
        assertCalledFrom(controller, "refreshClientPriorityOnce()", "pollClientPriorityForever")
        assertCalledFrom(controller, "delay(if (PollingIntervals.hasActiveJob(jobs)) CLIENT_PRIORITY_POLL_MS else IDLE_HOLD_CHECK_MS)", "pollClientPriorityForever")
        assertCalledFrom(controller, "jobsRepository.clientPriority()", "refreshClientPriorityOnce")
        assertCalledFrom(controller, "if (!PollingIntervals.hasActiveJob(jobs)) {", "refreshClientPriorityOnce")
        assertTrue(
            "the real repository no longer reads GET /v1/client-priority",
            stripComments(read(repo)).contains("override suspend fun clientPriority(): ClientPriorityOut? = client.clientPriority()"),
        )
    }

    @Test
    fun `MUTATION PIN -- the notes are built from the jobs and the hold and reach every card and the queue`() {
        assertCalledFrom(downloads, "clientPriorityNotes(partition, controller.clientPriority)", "DownloadsScreen")
        assertCalledFrom(downloads, "clientNotes = clientNotes,", "DownloadsScreen")
        assertCalledFrom(downloads, "holdNote = clientNotes.hold[model.jobId],", "DownloadsBody")
        assertCalledFrom(downloads, "clientNote = clientNotes.alongside[model.jobId],", "DownloadsBody")
        assertCalledFrom(downloads, "pausingNote = clientNotes.pausing[model.jobId],", "DownloadsBody")
        assertCalledFrom(downloads, "item(key = \"queue-hold-note\") { ClientNote(note) }", "DownloadsBody")
    }

    @Test
    fun `MUTATION PIN -- the card shows the client texts in place of the manual ones`() {
        assertCalledFrom(downloads, "if (clientNote != null) ClientNote(clientNote,", "JobCard")
        assertCalledFrom(downloads, "text = holdNote ?: stringResource(", "JobCard")
        assertCalledFrom(downloads, "StopNote(pausingNote ?: stringResource(R.string.downloads_stopnote_pausing))", "JobCard")
        val code = stripComments(read(downloads))
        assertTrue(
            "the queue note must sit between the Queue heading and its rows",
            code.indexOf("\"queue-heading\"") < code.indexOf("\"queue-hold-note\"") &&
                code.indexOf("\"queue-hold-note\"") < code.indexOf("\"queue-empty\""),
        )
    }
}
