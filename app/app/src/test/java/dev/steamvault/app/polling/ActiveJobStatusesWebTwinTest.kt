package dev.steamvault.app.polling

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * WP ACTIVE-STATUS-1 (audit C-5): the "active" (in-flight) job statuses are
 * ONE set per platform -- [PollingIntervals.ACTIVE_JOB_STATUSES] here,
 * `ACTIVE_JOB_STATUSES` in `web/js/lib/job-status.js` -- and the two agree.
 * The web side pins this file's twin line in
 * `web/tests/job-status-twin.test.js` (runs on the devbox, also against the
 * server's `ACTIVE_STATUSES`). Pins here: the value, the web value, that no
 * app module keeps its own literal copy, and that the unified call sites
 * import the shared set. A new active status changes web, app and server
 * together.
 */
class ActiveJobStatusesWebTwinTest {

    private val base = File("src/main/java/dev/steamvault/app")

    private fun stripComments(src: String): String = src
        .replace(Regex("/\\*[\\s\\S]*?\\*/"), "")
        .replace(Regex("(?m)^\\s*//.*$"), "")

    @Test
    fun `the app set is exactly queued, running, paused`() {
        // Hand-transcribed on purpose, not derived from the object.
        assertEquals(setOf("queued", "running", "paused"), PollingIntervals.ACTIVE_JOB_STATUSES)
    }

    @Test
    fun `the web set names the same statuses`() {
        val web = File("../../web/js/lib/job-status.js")
        check(web.exists()) { "No file at ${web.absolutePath}: MOVED -> update this path; DELETED -> retire the pin deliberately." }
        val m = Regex("""export const ACTIVE_JOB_STATUSES = new Set\(\[([^\]]*)\]\);""").find(web.readText(Charsets.UTF_8))
            ?: error("GRAMMAR DRIFT: no 'export const ACTIVE_JOB_STATUSES = new Set([...]);' in web/js/lib/job-status.js -- widen this regex")
        val values = Regex("\"([^\"]+)\"").findAll(m.groupValues[1]).map { it.groupValues[1] }.toSet()
        assertEquals(
            "VALUE DRIFT: web lib/job-status.js and PollingIntervals.ACTIVE_JOB_STATUSES differ -- change both (and web/tests/job-status-twin.test.js)",
            values,
            PollingIntervals.ACTIVE_JOB_STATUSES,
        )
    }

    /** A collection builder call whose arguments are only string literals
     * (any order, any length, may span lines). */
    private val stringCollection = Regex(
        """\b(?:setOf|listOf|hashSetOf|mutableSetOf|linkedSetOf|mutableListOf|arrayListOf|arrayOf)\(\s*(?:"[^"\n]*"\s*,\s*)*"[^"\n]*"\s*,?\s*\)""",
    )
    private val active = setOf("queued", "running", "paused")

    /** Every literal in [code] that holds ALL three active statuses (alone or
     * with extra entries), with the `val` it is assigned to on that line. */
    private fun activeLiterals(code: String): List<Pair<String?, String>> =
        stringCollection.findAll(code)
            .filter { m -> Regex("\"([^\"\n]*)\"").findAll(m.value).map { it.groupValues[1] }.toSet().containsAll(active) }
            .map { m ->
                val lineStart = code.lastIndexOf('\n', m.range.first) + 1
                val name = Regex("""\bval\s+(\w+)""").find(code.substring(lineStart, m.range.first))?.groupValues?.get(1)
                name to m.value
            }
            .toList()

    /** Declarations that hold the three statuses with a different meaning.
     * `KNOWN_STATUSES` (JobPartition.kt) is every status `partitionJobs`
     * sorts into a section (active + history), tied to its own literal
     * running/paused/queued filters, not "is this job in flight". */
    private val allowedDecls = setOf("ui/downloads/logic/JobPartition.kt#KNOWN_STATUSES")

    private fun relative(f: File): String = f.relativeTo(base).path.replace(File.separatorChar, '/')

    @Test
    fun `MUTATION TARGET -- no app module keeps its own literal copy of the set`() {
        val offenders = base.walkTopDown()
            .filter { it.isFile && it.extension == "kt" && it.name != "PollingIntervals.kt" }
            .flatMap { f ->
                activeLiterals(stripComments(f.readText(Charsets.UTF_8)))
                    .filterNot { (name, _) -> name != null && "${relative(f)}#$name" in allowedDecls }
                    .map { (name, text) -> "${relative(f)}${if (name != null) " ($name)" else ""}: $text" }
            }
            .toList()
        assertEquals(
            "a literal holding all three active job statuses -- use PollingIntervals.ACTIVE_JOB_STATUSES, or allowlist a declaration with a different meaning here with its reason",
            emptyList<String>(),
            offenders,
        )
    }

    @Test
    fun `every allowlisted declaration still exists`() {
        for (key in allowedDecls) {
            val (path, name) = key.split("#")
            val code = stripComments(File(base, path).readText(Charsets.UTF_8))
            assertTrue("$key no longer holds the three active statuses -- drop it from allowedDecls", activeLiterals(code).any { it.first == name })
        }
    }

    @Test
    fun `the guard sees copies in any shape`() {
        val shapes = listOf(
            "private val X = setOf(\"running\", \"queued\", \"paused\")",
            "val X = listOf(\"paused\", \"queued\", \"running\")",
            "val X = hashSetOf(\"queued\", \"running\", \"paused\", \"verifying\")",
            "val X = mutableSetOf(\n    \"queued\",\n    \"running\",\n    \"paused\",\n)",
            "jobs.filter { it.status in arrayOf(\"done\", \"queued\", \"running\", \"paused\") }",
        )
        for (src in shapes) assertEquals(src, 1, activeLiterals(src).size)
        assertEquals(0, activeLiterals("private val LIVE = setOf(\"running\", \"paused\")").size)
        assertEquals(0, activeLiterals("val X = setOf(STATUS_RUNNING, STATUS_PAUSED, STATUS_QUEUED)").size)
        assertEquals(listOf("KNOWN_STATUSES"), activeLiterals("private val KNOWN_STATUSES = setOf(\"queued\", \"running\", \"paused\", \"done\")").map { it.first })
    }

    @Test
    fun `the unified call sites import the shared set`() {
        val sites = listOf(
            "notifications/NotificationDiffer.kt",
            "ui/library/logic/CacheRepair.kt",
            "ui/library/logic/BulkPlan.kt",
            "ui/traffic/logic/TrafficView.kt",
            "ui/detail/GameDetailSheet.kt",
            "ui/detail/logic/DetailJob.kt",
            "ui/downloads/logic/JobPartition.kt",
            "ui/downloads/logic/UpdatesAvailable.kt",
            "ui/downloads/logic/IncompleteGames.kt",
        )
        for (path in sites) {
            val code = stripComments(File(base, path).readText(Charsets.UTF_8))
            assertTrue(
                "$path no longer imports PollingIntervals.ACTIVE_JOB_STATUSES",
                code.contains("import dev.steamvault.app.polling.PollingIntervals.ACTIVE_JOB_STATUSES\n"),
            )
            assertTrue("$path imports ACTIVE_JOB_STATUSES but never uses it", code.contains("in ACTIVE_JOB_STATUSES"))
        }
    }
}
