package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.R
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.ui.downloads.logic.JobCardMode
import dev.steamvault.app.ui.downloads.logic.activeJobKind
import dev.steamvault.app.ui.downloads.logic.activeJobWord
import dev.steamvault.app.ui.downloads.logic.buildJobCardModel
import dev.steamvault.app.ui.downloads.logic.jobIconKind
import dev.steamvault.app.ui.downloads.logic.jobStatusWord
import dev.steamvault.app.ui.status.StatusKind
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * WP WEB-FEAT-6: the Updating / Verifying states, Android twin of
 * `web/tests/update-verify.test.js`. The derivation ([liveRunKind], file
 * kdoc of `GameStatus.kt`):
 *   last_prefill_at null                    -> RUNNING  "Downloading"
 *   last_prefill_at set, needs_force false  -> UPDATING "Updating"
 *   last_prefill_at set, needs_force true   -> VERIFY   "Verifying"
 * Expected kinds and words are LITERALS (LEARNINGS 4b.1: cross-frontend
 * contracts are never derived from the enum under test).
 */
class UpdateVerifyTest {

    private val t = "2026-10-09T08:00:00Z"

    private fun game(lastPrefillAt: String?, needsForce: Boolean, sizeBytes: Long? = null, appid: Int = 440) = GameSummary(
        appid = appid,
        name = "Aurora Cascade",
        status = "running",
        last_prefill_at = lastPrefillAt,
        depot_count = 2,
        size_bytes = sizeBytes,
        needs_force = needsForce,
    )

    private fun fresh(sizeBytes: Long? = null) = game(null, needsForce = true, sizeBytes = sizeBytes)
    private fun refresh(sizeBytes: Long? = 5_000_000_000L) = game(t, needsForce = false, sizeBytes = sizeBytes)
    private fun reverify() = game(t, needsForce = true, sizeBytes = 5_000_000_000L)

    private fun job(status: String, type: String = "prefill", appid: Int = 440) =
        JobSummary(id = 7, appid = appid, type = type, status = status, created_at = t)

    // ---------- GameStatus.kt ----------

    @Test
    fun `MUTATION TARGET -- liveRunKind maps the three cases`() {
        assertEquals("running", liveRunKind(fresh()).wireName)
        assertEquals("updating", liveRunKind(refresh()).wireName)
        assertEquals("verify", liveRunKind(reverify()).wireName)
        assertEquals("running", liveRunKind(null).wireName)
    }

    @Test
    fun `MUTATION TARGET -- liveRunKind ignores live bytes`() {
        // size_bytes grows during a first fill; it must never turn it into Updating.
        assertEquals("running", liveRunKind(fresh(sizeBytes = 12_000_000_000L)).wireName)
        assertEquals("running", liveRunKind(game(null, needsForce = false, sizeBytes = 12_000_000_000L)).wireName)
        assertEquals("updating", liveRunKind(refresh(sizeBytes = null)).wireName)
    }

    @Test
    fun `MUTATION TARGET -- dispKind uses liveRunKind for a running job, paused stays paused`() {
        assertEquals(StatusKind.UPDATING, dispKind(refresh(), job("running")))
        assertEquals(StatusKind.VERIFY, dispKind(reverify(), job("running")))
        assertEquals(StatusKind.RUNNING, dispKind(fresh(), job("running")))
        assertEquals(StatusKind.PAUSED, dispKind(reverify(), job("paused")))
        assertEquals(StatusKind.CACHED, dispKind(refresh().copy(status = "done"), null))
    }

    @Test
    fun `an Updating or Verifying run keeps the pause action`() {
        assertEquals(StatusAction(StatusActionType.PAUSE), statusAction(refresh(), job("running"), selecting = false))
        assertEquals(StatusAction(StatusActionType.PAUSE), statusAction(reverify(), job("running"), selecting = false))
    }

    @Test
    fun `the library card model carries the derived kind and its label`() {
        val updating = buildGameCardModel(refresh(), job("running"), selected = false, selecting = false)
        val verifying = buildGameCardModel(reverify(), job("running"), selected = false, selecting = false)
        assertEquals(StatusKind.UPDATING, updating.kind)
        assertEquals(R.string.status_updating, updating.kind.labelRes)
        assertEquals(StatusKind.VERIFY, verifying.kind)
        assertEquals(R.string.status_verify, verifying.kind.labelRes)
    }

    @Test
    fun `library filters put an Updating game under Downloading, not Cached`() {
        val games = listOf(refresh().copy(appid = 1), refresh().copy(appid = 3, status = "done"))
        val live = mapOf(1 to job("running", appid = 1))
        fun keys(filter: String) = games.filter { g -> FILTER_DEFS.first { it.key == filter }.predicate(g, live[g.appid]) }.map { it.appid }
        assertEquals(listOf(1), keys("downloading"))
        assertEquals(listOf(3), keys("cached"))
    }

    // ---------- JobPartition.kt / JobCardModel.kt ----------

    @Test
    fun `MUTATION TARGET -- activeJobKind and activeJobWord follow the games row`() {
        assertEquals("updating", activeJobKind(job("running"), refresh()))
        assertEquals("verify", activeJobKind(job("running"), reverify()))
        assertEquals("running", activeJobKind(job("running"), fresh()))
        assertEquals("running", activeJobKind(job("running"), null))
        assertEquals("Updating", activeJobWord(job("running"), refresh()))
        assertEquals("Verifying", activeJobWord(job("running"), reverify()))
        assertEquals("Downloading", activeJobWord(job("running"), fresh()))
    }

    @Test
    fun `activeJobKind leaves paused, GC and finished jobs alone`() {
        assertEquals("paused", activeJobKind(job("paused"), reverify()))
        assertEquals("Paused", activeJobWord(job("paused"), reverify()))
        assertEquals("running", activeJobKind(job("running", type = "gc"), refresh()))
        assertEquals("Collecting garbage", activeJobWord(job("running", type = "gc"), refresh()))
        for (s in listOf("queued", "done", "error", "cancelled", "weird")) {
            assertEquals(s, jobIconKind(job(s)), activeJobKind(job(s), refresh()))
            assertEquals(s, jobStatusWord(job(s)), activeJobWord(job(s), refresh()))
        }
    }

    @Test
    fun `MUTATION TARGET -- the Downloads card model takes the kind and word from the games row`() {
        val active = buildJobCardModel(job("running"), mapOf(440 to reverify()), JobCardMode.ACTIVE)
        assertEquals(StatusKind.VERIFY, active.kind)
        assertEquals("Verifying", active.statusWord)
        val updating = buildJobCardModel(job("running"), mapOf(440 to refresh()), JobCardMode.ACTIVE)
        assertEquals(StatusKind.UPDATING, updating.kind)
        assertEquals("Updating", updating.statusWord)
        val unknown = buildJobCardModel(job("running"), emptyMap(), JobCardMode.ACTIVE)
        assertEquals(StatusKind.RUNNING, unknown.kind)
        assertEquals("Downloading", unknown.statusWord)
        // A needs_force flip changes the model (Compose re-renders the badge) ...
        assertTrue(active != updating)
        // ... and an unchanged games row gives an EQUAL model (no re-render).
        assertEquals(active, buildJobCardModel(job("running"), mapOf(440 to reverify()), JobCardMode.ACTIVE))
    }

    // ---------- cross-frontend ----------

    @Test
    fun `web job-partition js uses the same two words`() {
        val file = File("../../web/js/lib/job-partition.js")
        check(file.exists()) { "expected a file at ${file.absolutePath}" }
        val js = file.readText(Charsets.UTF_8)
        val block = Regex("""const RUN_KIND_WORD = Object\.freeze\(\{([^}]*)\}\);""").find(js)
            ?: error("GRAMMAR DRIFT: no 'const RUN_KIND_WORD = Object.freeze({...});' in web/js/lib/job-partition.js -- widen this regex")
        val body = block.groupValues[1]
        assertTrue("VALUE DRIFT: update JobPartition.kt or the web word", body.contains("[KIND.UPDATING]: \"Updating\""))
        assertTrue("VALUE DRIFT: update JobPartition.kt or the web word", body.contains("[KIND.VERIFY]: \"Verifying\""))
    }

    @Test
    fun `web game-status js derives the same three cases`() {
        val file = File("../../web/js/lib/game-status.js")
        check(file.exists()) { "expected a file at ${file.absolutePath}" }
        val js = file.readText(Charsets.UTF_8)
        val m = Regex("""export function liveRunKind\(game\) \{([^}]*)\}""").find(js)
            ?: error("GRAMMAR DRIFT: no 'export function liveRunKind(game) {...}' in web/js/lib/game-status.js -- widen this regex")
        val body = m.groupValues[1]
        assertTrue("LOGIC DRIFT: web liveRunKind changed, port it to GameStatus.kt", body.contains("game.last_prefill_at == null) return KIND.RUNNING"))
        assertTrue("LOGIC DRIFT: web liveRunKind changed, port it to GameStatus.kt", body.contains("game.needs_force === true ? KIND.VERIFY : KIND.UPDATING"))
    }
}
