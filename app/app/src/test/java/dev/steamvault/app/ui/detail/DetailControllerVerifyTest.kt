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

private fun notHere(): Nothing = error("not used here")

/** Records every `POST /v1/prefill/verify`; answers with [answer]. */
private class VerifyJobs(
    private val gate: CompletableDeferred<Unit>? = null,
    private val fail: Boolean = false,
    private val answer: (List<Int>) -> List<PrefillJobRef> = { ids ->
        ids.map { PrefillJobRef(appid = it, job_id = it, status = "queued", deduplicated = false) }
    },
) : JobsRepository {
    val verifies = mutableListOf<List<Int>>()
    val prefills = mutableListOf<List<Int>>()
    override suspend fun list(limit: Int): List<JobSummary> = emptyList()
    override suspend fun detail(id: Int): JobDetail = notHere()
    override suspend fun prefill(appids: List<Int>): List<PrefillJobRef> {
        prefills += appids
        return emptyList()
    }
    override suspend fun prefillCached(): List<PrefillJobRef> = notHere()
    override suspend fun prefillVerify(appids: List<Int>): List<PrefillJobRef> {
        verifies += appids
        gate?.await()
        if (fail) throw VaultApiError.Server("boom", 500)
        return answer(appids)
    }
    override suspend fun cancel(id: Int): JobControlOut = notHere()
    override suspend fun pause(id: Int): JobControlOut = notHere()
    override suspend fun resume(id: Int): JobControlOut = notHere()
    override suspend fun moveJob(id: Int, position: Int): JobMoveOut = notHere()
}

private object VerifyNoGames : GamesRepository {
    override suspend fun list(): List<GameSummary> = notHere()
    override suspend fun detail(appid: Int): GameDetail = notHere()
}

private object VerifyNoMapping : MappingRepository {
    override suspend fun list(): List<MappingEntry> = notHere()
}

private object VerifyNoCache : CacheRepository {
    override suspend fun delete(appid: Int): CacheDeletionOut = notHere()
    override suspend fun gc(appid: Int, execute: Boolean): GcJobRef = notHere()
}

private object VerifyStrings : DetailStrings {
    override fun queuedForDownload() = "queued"
    override fun pauseRequested() = "pause"
    override fun resuming() = "resume"
    override fun cancelRequested() = "cancel"
    override fun actionFailedFallback() = "failed"
    override fun loadErrorFallback() = "load failed"
    override fun freed(freedText: String) = "freed $freedText"
    override fun zeroBytesLabel() = "0 B"
}

/** WP CACHE-COMPLETE-1c: the detail sheet's "Verify" (web `startSheetVerify`). */
class DetailControllerVerifyTest {

    private fun controller(jobs: JobsRepository) = DetailController(VerifyNoGames, jobs, VerifyNoMapping, VerifyNoCache, VerifyStrings)

    @Test
    fun `MUTATION PIN -- Verify posts exactly the given ids to prefill verify, never a plain prefill`() = runTest {
        val jobs = VerifyJobs()
        val c = controller(jobs)
        c.startVerify(this, listOf(100, 101))
        advanceUntilIdle()
        assertEquals(listOf(listOf(100, 101)), jobs.verifies)
        assertTrue(jobs.prefills.isEmpty())
        assertEquals("2 queued to verify", c.toast)
        assertFalse(c.verifyInFlight)
    }

    @Test
    fun `a verify landing on a running job says it verifies on the next run`() = runTest {
        val jobs = VerifyJobs(answer = { ids -> ids.map { PrefillJobRef(appid = it, job_id = 9, status = "running", deduplicated = true) } })
        val c = controller(jobs)
        c.startVerify(this, listOf(100))
        advanceUntilIdle()
        assertEquals("Already downloading — verifies on the next run.", c.toast)
    }

    @Test
    fun `one request at a time, an empty list sends nothing, and a 5xx says some may already be queued`() = runTest {
        val gate = CompletableDeferred<Unit>()
        val jobs = VerifyJobs(gate, fail = true)
        val c = controller(jobs)
        c.startVerify(this, emptyList())
        c.startVerify(this, listOf(100))
        advanceUntilIdle()
        assertTrue(c.verifyInFlight)
        c.startVerify(this, listOf(100))
        gate.complete(Unit)
        advanceUntilIdle()
        assertEquals(listOf(listOf(100)), jobs.verifies)
        assertTrue(c.toast!!.startsWith("The server had trouble partway through"))
        assertFalse(c.verifyInFlight)
    }
}
