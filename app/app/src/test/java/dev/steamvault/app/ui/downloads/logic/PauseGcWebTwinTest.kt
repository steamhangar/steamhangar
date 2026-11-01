package dev.steamvault.app.ui.downloads.logic

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Twin pins for WP ANDROID-PAUSE-GC-1 (web PAUSE-ALL-GC-1): the paused GC
 * word and the dry-run label (`web/js/lib/job-partition.js`), the detail
 * sheet's paused text (`web/js/lib/gc-flow.js` `GC_PAUSED_TEXT`) and the
 * paused GC card's hold note (`web/js/views/downloads.js` `GC_HOLD_NOTE`).
 * The bulk dialog and toast literals are pinned in [BulkJobsWebTwinTest].
 */
class PauseGcWebTwinTest {
    private fun web(path: String): String {
        val f = File("../../web/js/$path")
        check(f.exists()) { "No file at ${f.absolutePath}: MOVED -> update this path; DELETED -> retire the pin deliberately." }
        return f.readText(Charsets.UTF_8)
    }

    private fun androidString(name: String): String {
        val xml = File("src/main/res/values/strings.xml").readText(Charsets.UTF_8)
        val m = Regex("""<string name="$name">(.*?)</string>""").find(xml)
            ?: error("no <string name=\"$name\"> in strings.xml")
        return m.groupValues[1]
    }

    private fun assertWebHas(js: String, file: String, literal: String) = assertTrue(
        "VALUE DRIFT: web $file no longer contains \"$literal\" as one double-quoted string -- update the Android twin.",
        js.contains("\"$literal\""),
    )

    @Test
    fun `job-partition js has the paused gc word and the dry-run label`() {
        val js = web("lib/job-partition.js")
        assertWebHas(js, "job-partition.js", jobStatusWordOfPausedGc())
        assertTrue("paused GC word moved out of GC_WORD", js.contains("paused: \"Garbage collection paused\""))
        assertWebHas(js, "job-partition.js", GC_DRY_RUN_DONE_WORD)
        assertTrue(
            "LOGIC DRIFT: web dry-run label condition changed, port it to JobPartition.kt",
            js.contains("job.type === \"gc\" && job.status === \"done\" && job.gc_execute === false"),
        )
    }

    @Test
    fun `gc-flow js paused text equals the detail sheet string`() {
        val text = androidString("detail_gc_paused")
        assertEquals("GC paused — resume it on the Downloads page.", text)
        assertWebHas(web("lib/gc-flow.js"), "gc-flow.js", text)
    }

    @Test
    fun `downloads js gc hold note equals the paused gc card string`() {
        val text = androidString("downloads_paused_hold_note_gc")
        assertTrue(text.startsWith("Paused before it started"))
        assertWebHas(web("views/downloads.js"), "downloads.js", text)
    }

    private fun jobStatusWordOfPausedGc(): String = jobStatusWord(
        dev.steamvault.app.net.model.JobSummary(id = 1, appid = 1, type = "gc", status = "paused", created_at = "2026-08-01T00:00:00Z"),
    )
}
