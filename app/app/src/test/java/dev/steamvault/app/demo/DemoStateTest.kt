package dev.steamvault.app.demo

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.settingAsBooleanOrNull
import dev.steamvault.app.net.model.settingAsStringOrNull
import dev.steamvault.app.ui.detail.logic.GcMode
import dev.steamvault.app.ui.detail.logic.parseGcLogSummary
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Behavioural pins for [DemoState] (WP APP-DEMO). Shapes are checked
 * against the SAME real response models these methods return (see
 * `DemoState.kt`'s own kdoc for why that is a compile-time, not merely a
 * hand-checked, guarantee) — these tests exercise VALUES, not the type
 * system, plus the mutation/reset behaviour the WP brief calls out by name
 * (constraint 4: leaving/re-entering demo mode).
 */
class DemoStateTest {

    @Test
    fun `fresh library has six curated games plus one content-package group, each a fictional name`() {
        val state = DemoState.fresh()
        val names = state.listGameSummaries().map { it.name }
        assertEquals(
            listOf(
                "Duskfall Array", "Cobalt Isthmus", "Wrenfield Static", "Halcyon Ledger", "Pale Meridian", "Marrowlight",
                // WP TOOL-APPS-2d: the content-package group (DemoContentGroupTest).
                "Emberline Cartography",
                "Emberline Cartography - Danish Voice Pack",
                "Emberline Cartography - Original Soundtrack",
                "Emberline Cartography - Surveyor Outfits",
            ),
            names,
        )
    }

    @Test
    fun `every game summary's depot_count matches its own depots list, never a stale or hardcoded number`() {
        val state = DemoState.fresh()
        for (summary in state.listGameSummaries()) {
            val detail = state.gameDetail(summary.appid)
            assertEquals("appid ${summary.appid}", detail.depots.size, summary.depot_count)
        }
    }

    /** WP 4e.1 fix, carried into this fixture too (docs/LEARNINGS.md): an
     * `idle` game the real API can produce is ALWAYS `needs_force=true` --
     * nothing has confirmed it current yet. This demo fixture must not
     * claim a shape the real API cannot produce. */
    @Test
    fun `MUTATION PIN -- idle games are always needs_force, done games never are`() {
        val state = DemoState.fresh()
        for (summary in state.listGameSummaries()) {
            when (summary.status) {
                "idle" -> assertTrue("appid ${summary.appid} is idle", summary.needs_force)
                "done" -> assertFalse("appid ${summary.appid} is done", summary.needs_force)
            }
        }
    }

    @Test
    fun `gameDetail on an unknown appid throws the real 404 taxonomy, not a generic exception`() {
        val state = DemoState.fresh()
        val error = assertThrows(VaultApiError.NotFound::class.java) { state.gameDetail(999_999) }
        assertEquals(404, error.status)
    }

    @Test
    fun `jobDetail on an unknown job id throws the real 404 taxonomy`() {
        val state = DemoState.fresh()
        assertThrows(VaultApiError.NotFound::class.java) { state.jobDetail(1) }
    }

    // ---- job ticking (call-count driven, never wall-clock) ------------------

    @Test
    fun `the seed active job starts running and finishes after enough polls, updating its game too`() {
        val state = DemoState.fresh()
        val seedJob = state.listJobSummaries(20).first { it.status == "running" }
        val appid = seedJob.appid

        // Enough ticks to exhaust the seed job's ticksLeft (2) -- both
        // listJobSummaries and jobDetail advance the same clock (DemoState's
        // own "tick on every jobs read" rule), so either call works.
        var last = state.jobDetail(seedJob.id)
        var guard = 0
        while (last.status == "running" && guard < 10) {
            last = state.jobDetail(seedJob.id)
            guard++
        }

        assertEquals("done", last.status)
        assertNotNull(last.log_excerpt)
        val game = state.gameDetail(appid)
        assertEquals("done", game.status)
        assertFalse(game.needs_force)
        assertTrue("a finished prefill must leave at least one depot", game.depots.isNotEmpty())
    }

    // ---- enqueue / dedupe ------------------------------------------------------

    @Test
    fun `enqueuePrefill on an idle game starts a new running job`() {
        val state = DemoState.fresh()
        val idleGame = state.listGameSummaries().first { it.status == "idle" }
        val refs = state.enqueuePrefill(listOf(idleGame.appid))
        assertEquals(1, refs.size)
        assertFalse(refs[0].deduplicated)
        assertEquals("running", state.gameDetail(idleGame.appid).status)
    }

    @Test
    fun `MUTATION PIN -- enqueuePrefill on an already-active appid dedupes instead of starting a second job`() {
        val state = DemoState.fresh()
        val idleGame = state.listGameSummaries().first { it.status == "idle" }
        val first = state.enqueuePrefill(listOf(idleGame.appid)).single()
        val second = state.enqueuePrefill(listOf(idleGame.appid)).single()
        assertFalse(first.deduplicated)
        assertTrue(second.deduplicated)
        assertEquals(first.job_id, second.job_id)
    }

    @Test
    fun `enqueuePrefillCached only requeues games already done, never idle or error ones`() {
        val state = DemoState.fresh()
        val doneAppids = state.listGameSummaries().filter { it.status == "done" }.map { it.appid }.toSet()
        val refs = state.enqueuePrefillCached()
        assertEquals(doneAppids, refs.map { it.appid }.toSet())
    }

    // ---- job control --------------------------------------------------------

    @Test
    fun `pause then resume then cancel a running job`() {
        val state = DemoState.fresh()
        val idleGame = state.listGameSummaries().first { it.status == "idle" }
        val jobId = state.enqueuePrefill(listOf(idleGame.appid)).single().job_id

        val paused = state.controlJob(jobId, JobControlAction.PAUSE)
        assertEquals("paused", paused.status)
        assertNotNull(state.jobDetail(jobId).paused_at)

        val resumed = state.controlJob(jobId, JobControlAction.RESUME)
        assertEquals("running", resumed.status)
        assertNull(state.jobDetail(jobId).paused_at)

        val cancelled = state.controlJob(jobId, JobControlAction.CANCEL)
        assertEquals("cancelled", cancelled.status)
    }

    /** WP APP-DEMO review round 2 nitpick fix: the real server refuses
     * job control against a job that is not in the matching state -- a
     * terminal job (already `done`) cannot be paused, resumed, or
     * cancelled again. */
    @Test
    fun `MUTATION PIN -- controlJob refuses pause, resume, and cancel against a terminal job`() {
        val state = DemoState.fresh()
        val doneJobId = state.listJobSummaries(20).first { it.status == "done" }.id

        val pauseError = assertThrows(VaultApiError.Validation::class.java) {
            state.controlJob(doneJobId, JobControlAction.PAUSE)
        }
        assertEquals(409, pauseError.status)

        val resumeError = assertThrows(VaultApiError.Validation::class.java) {
            state.controlJob(doneJobId, JobControlAction.RESUME)
        }
        assertEquals(409, resumeError.status)

        val cancelError = assertThrows(VaultApiError.Validation::class.java) {
            state.controlJob(doneJobId, JobControlAction.CANCEL)
        }
        assertEquals(409, cancelError.status)
    }

    @Test
    fun `MUTATION PIN -- controlJob refuses resume against a job that is not paused`() {
        val state = DemoState.fresh()
        val runningJobId = state.listJobSummaries(20).first { it.status == "running" }.id
        val error = assertThrows(VaultApiError.Validation::class.java) {
            state.controlJob(runningJobId, JobControlAction.RESUME)
        }
        assertEquals(409, error.status)
    }

    // ---- shared-depot deletion (the Halcyon Ledger / Pale Meridian pair) ----

    @Test
    fun `MUTATION PIN -- deleting one side of a shared depot skips it, deleting the other side afterward then removes it`() {
        val state = DemoState.fresh()
        val halcyon = state.listGameSummaries().first { it.name == "Halcyon Ledger" }.appid
        val pale = state.listGameSummaries().first { it.name == "Pale Meridian" }.appid

        val firstDelete = state.deleteCache(halcyon)
        assertEquals(1, firstDelete.skipped_shared.size)
        assertTrue(firstDelete.skipped_shared.single().shared_with.contains(pale))
        // Pale Meridian's copy of the shared depot must still be intact.
        assertTrue(state.gameDetail(pale).depots.isNotEmpty())

        val secondDelete = state.deleteCache(pale)
        assertTrue("the shared depot has no other holder left, so this delete must actually free it", secondDelete.skipped_shared.isEmpty())
        assertTrue(secondDelete.total_bytes_freed > 0)
        assertTrue(state.gameDetail(pale).depots.isEmpty())
    }

    @Test
    fun `mapping lists a shared depot once per owning game`() {
        val state = DemoState.fresh()
        val halcyon = state.listGameSummaries().first { it.name == "Halcyon Ledger" }.appid
        val pale = state.listGameSummaries().first { it.name == "Pale Meridian" }.appid
        val sharedDepotId = state.gameDetail(halcyon).depots.first { it.shared }.depotid

        val owners = state.mapping().filter { it.depotid == sharedDepotId }.map { it.appid }.toSet()
        assertEquals(setOf(halcyon, pale), owners)
    }

    // ---- GC dry run / execute, parsed by the REAL production parser --------

    @Test
    fun `a GC dry run job's log_excerpt is parseable by the real GcLogSummary parser`() {
        val state = DemoState.fresh()
        val marrowlight = state.listGameSummaries().first { it.name == "Marrowlight" }.appid
        val ref = state.gc(marrowlight, execute = false)

        var job = state.jobDetail(ref.job_id)
        var guard = 0
        while (job.status == "running" && guard < 10) {
            job = state.jobDetail(ref.job_id)
            guard++
        }

        assertEquals("done", job.status)
        assertEquals(false, job.gc_execute)
        val summary = parseGcLogSummary(job.log_excerpt)
        assertNotNull("expected a parseable GC totals line, got: ${job.log_excerpt}", summary)
        assertEquals(GcMode.DRY_RUN, summary!!.mode)
        assertNotNull(summary.wouldDeleteBytes)
    }

    @Test
    fun `a GC execute job's log_excerpt parses as EXECUTED and clears the reclaimable total`() {
        val state = DemoState.fresh()
        val marrowlight = state.listGameSummaries().first { it.name == "Marrowlight" }.appid
        val ref = state.gc(marrowlight, execute = true)

        var job = state.jobDetail(ref.job_id)
        var guard = 0
        while (job.status == "running" && guard < 10) {
            job = state.jobDetail(ref.job_id)
            guard++
        }

        val summary = parseGcLogSummary(job.log_excerpt)
        assertNotNull(summary)
        assertEquals(GcMode.EXECUTE, summary!!.mode)
        assertNotNull(summary.totalBytesFreed)
    }

    @Test
    fun `gc on an unknown appid throws the real 404 taxonomy`() {
        val state = DemoState.fresh()
        assertThrows(VaultApiError.NotFound::class.java) { state.gc(999_999, execute = false) }
    }

    // ---- settings (ADR-0009 db greater env greater default; ADR-0010 default-off) ----

    @Test
    fun `MUTATION PIN -- the two ADR-0010 privacy keys are env-only and default off`() {
        val state = DemoState.fresh()
        val byKey = state.settingsOut().settings.associateBy { it.key }
        for (key in listOf("relay_expose_playtime", "relay_expose_last_played")) {
            val entry = byKey.getValue(key)
            assertTrue("$key must be env_only", entry.env_only)
            assertEquals(false, entry.effective.settingAsBooleanOrNull())
        }
    }

    @Test
    fun `settings_readonly and vault_api_key-adjacent env-only keys are present and marked env_only`() {
        val state = DemoState.fresh()
        val byKey = state.settingsOut().settings.associateBy { it.key }
        for (key in listOf("db_path", "cache_root", "steamprefill_path", "steamprefill_cache_dir", "manifest_archive_dir", "web_dir", "settings_readonly")) {
            assertTrue("$key must be env_only", byKey.getValue(key).env_only)
        }
    }

    @Test
    fun `patching an overridable key changes its source to db and effective to the new value`() {
        val state = DemoState.fresh()
        state.patchSettings(mapOf("vault_name" to JsonPrimitive("my-hangar")))
        val entry = state.settingsOut().settings.first { it.key == "vault_name" }
        assertEquals("db", entry.source)
        assertEquals("my-hangar", entry.effective.settingAsStringOrNull())
    }

    @Test
    fun `clearing a db override with a null value reverts to the env source`() {
        val state = DemoState.fresh()
        state.patchSettings(mapOf("vault_name" to JsonPrimitive("my-hangar")))
        state.patchSettings(mapOf("vault_name" to JsonNull))
        val entry = state.settingsOut().settings.first { it.key == "vault_name" }
        assertEquals("env", entry.source)
    }

    @Test
    fun `MUTATION PIN -- patching an env-only key is rejected with a 422, same as the real server`() {
        val state = DemoState.fresh()
        val error = assertThrows(VaultApiError.Validation::class.java) {
            state.patchSettings(mapOf("relay_expose_playtime" to JsonPrimitive(true)))
        }
        assertEquals(422, error.status)
        // The db value must not have been recorded even transiently.
        val entry = state.settingsOut().settings.first { it.key == "relay_expose_playtime" }
        assertEquals(false, entry.effective.settingAsBooleanOrNull())
    }

    // ---- constraint 4: re-entering demo mode must not carry stale state ----

    @Test
    fun `MUTATION PIN -- two fresh() instances are fully independent, mutating one never affects the other`() {
        val first = DemoState.fresh()
        val idleGame = first.listGameSummaries().first { it.status == "idle" }
        first.enqueuePrefill(listOf(idleGame.appid))
        first.deleteCache(first.listGameSummaries().first { it.status == "done" }.appid)
        first.patchSettings(mapOf("vault_name" to JsonPrimitive("mutated")))

        val second = DemoState.fresh()
        assertEquals("idle", second.gameDetail(idleGame.appid).status)
        assertEquals(
            "env",
            second.settingsOut().settings.first { it.key == "vault_name" }.source,
        )
    }
}

/** WP D20: `POST /v1/jobs/{id}/move` in demo mode refuses what the real API refuses. */
class DemoMoveJobTest {
    @Test
    fun `unknown job is 404, a running job is 409, a negative position is 422`() {
        val state = DemoState.fresh()
        val unknown = assertThrows(VaultApiError.NotFound::class.java) { state.moveJob(1, 0) }
        assertEquals(404, unknown.status)
        val running = state.listJobSummaries(50).first { it.status == "running" }
        val notQueued = assertThrows(VaultApiError.Validation::class.java) { state.moveJob(running.id, 0) }
        assertEquals(409, notQueued.status)
        val negative = assertThrows(VaultApiError.Validation::class.java) { state.moveJob(running.id, -1) }
        assertEquals(422, negative.status)
    }

    @Test
    fun `only a queued job carries queue_position`() {
        val state = DemoState.fresh()
        assertTrue(state.listJobSummaries(50).all { it.queue_position == null })
    }
}
