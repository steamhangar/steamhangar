package dev.steamvault.app.ui.detail

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.CacheDeletionOut
import dev.steamvault.app.net.model.GameDetail
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.GcJobRef
import dev.steamvault.app.net.model.JobControlOut
import dev.steamvault.app.net.model.JobDetail
import dev.steamvault.app.net.model.JobMoveOut
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.net.model.MappingEntry
import dev.steamvault.app.net.model.PrefillJobRef
import dev.steamvault.app.repo.CacheRepository
import dev.steamvault.app.repo.GamesRepository
import dev.steamvault.app.repo.JobsRepository
import dev.steamvault.app.repo.MappingRepository
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

private fun unused(): Nothing = error("not used here")

private class PrefillJobs(private val gate: CompletableDeferred<Unit>? = null, private val fail: Boolean = false) : JobsRepository {
    val prefills = mutableListOf<List<Int>>()
    override suspend fun list(limit: Int): List<JobSummary> = emptyList()
    override suspend fun detail(id: Int): JobDetail = unused()
    override suspend fun prefill(appids: List<Int>): List<PrefillJobRef> {
        prefills += appids
        gate?.await()
        if (fail) throw VaultApiError.Server("boom", 500)
        return appids.map { PrefillJobRef(appid = it, job_id = it, status = "queued", deduplicated = false) }
    }
    override suspend fun prefillCached(): List<PrefillJobRef> = unused()
    override suspend fun prefillVerify(appids: List<Int>): List<PrefillJobRef> = unused()
    override suspend fun cancel(id: Int): JobControlOut = unused()
    override suspend fun pause(id: Int): JobControlOut = unused()
    override suspend fun resume(id: Int): JobControlOut = unused()
    override suspend fun moveJob(id: Int, position: Int): JobMoveOut = unused()
}

private object NoGames : GamesRepository {
    override suspend fun list(): List<GameSummary> = unused()
    override suspend fun detail(appid: Int): GameDetail = unused()
}

private object NoMapping : MappingRepository {
    override suspend fun list(): List<MappingEntry> = unused()
}

private object NoCache : CacheRepository {
    override suspend fun delete(appid: Int): CacheDeletionOut = unused()
    override suspend fun gc(appid: Int, execute: Boolean): GcJobRef = unused()
}

private object Strings : DetailStrings {
    override fun queuedForDownload() = "queued"
    override fun pauseRequested() = "pause"
    override fun resuming() = "resume"
    override fun cancelRequested() = "cancel"
    override fun actionFailedFallback() = "failed"
    override fun loadErrorFallback() = "load failed"
    override fun freed(freedText: String) = "freed $freedText"
    override fun zeroBytesLabel() = "0 B"
}

/** WP UPD-6 review fix: the detail sheet's "Update now" (web `startSheetUpdate`). */
class DetailControllerUpdateTest {

    private fun controller(jobs: JobsRepository) = DetailController(NoGames, jobs, NoMapping, NoCache, Strings)

    @Test
    fun `MUTATION PIN -- sends exactly the given ids and toasts the update wording`() = runTest {
        val jobs = PrefillJobs()
        val c = controller(jobs)
        c.startUpdate(this, listOf(100, 101), emptyList())
        advanceUntilIdle()
        assertEquals(listOf(listOf(100, 101)), jobs.prefills)
        assertEquals("2 queued for update", c.toast)
        assertFalse(c.updateInFlight)
    }

    @Test
    fun `one request at a time, and a 5xx says some may already be queued`() = runTest {
        val gate = CompletableDeferred<Unit>()
        val jobs = PrefillJobs(gate, fail = true)
        val c = controller(jobs)
        c.startUpdate(this, listOf(100), emptyList())
        advanceUntilIdle()
        assertTrue(c.updateInFlight)
        c.startUpdate(this, listOf(100), emptyList())
        gate.complete(Unit)
        advanceUntilIdle()
        assertEquals(1, jobs.prefills.size)
        assertTrue(c.toast!!.startsWith("The server had trouble partway through"))
        assertFalse(c.updateInFlight)
    }
}
