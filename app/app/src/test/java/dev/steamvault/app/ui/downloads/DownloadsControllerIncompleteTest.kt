package dev.steamvault.app.ui.downloads

import dev.steamvault.app.net.model.GameDetail
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.JobControlOut
import dev.steamvault.app.net.model.JobDetail
import dev.steamvault.app.net.model.JobMoveOut
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.net.model.PrefillJobRef
import dev.steamvault.app.repo.GamesRepository
import dev.steamvault.app.repo.JobsRepository
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

private fun notHere(): Nothing = error("not used here")

/** Records every `POST /v1/prefill/verify`; answers each app as freshly queued. */
private class CompletionJobs(private val gate: CompletableDeferred<Unit>? = null) : JobsRepository {
    val verifies = mutableListOf<List<Int>>()
    val prefills = mutableListOf<List<Int>>()
    var listCalls = 0
    override suspend fun list(limit: Int): List<JobSummary> {
        listCalls++
        return emptyList()
    }
    override suspend fun detail(id: Int): JobDetail = notHere()
    override suspend fun prefill(appids: List<Int>): List<PrefillJobRef> {
        prefills += appids
        return emptyList()
    }
    override suspend fun prefillCached(): List<PrefillJobRef> = notHere()
    override suspend fun prefillVerify(appids: List<Int>): List<PrefillJobRef> {
        verifies += appids
        gate?.await()
        return appids.map { PrefillJobRef(appid = it, job_id = it, status = "queued", deduplicated = false) }
    }
    override suspend fun cancel(id: Int): JobControlOut = notHere()
    override suspend fun pause(id: Int): JobControlOut = notHere()
    override suspend fun resume(id: Int): JobControlOut = notHere()
    override suspend fun moveJob(id: Int, position: Int): JobMoveOut = notHere()
}

private class CompletionNoGames : GamesRepository {
    override suspend fun list(): List<GameSummary> = emptyList()
    override suspend fun detail(appid: Int): GameDetail = notHere()
}

private class CompletionStrings : DownloadsStrings {
    override fun pauseRequested() = "pause"
    override fun resuming() = "resume"
    override fun cancelRequested() = "cancel"
    override fun actionFailedFallback() = "failed"
    override fun logFetchErrorFallback() = "log failed"
    override fun queuedForDownload() = "queued"
}

/** WP CACHE-COMPLETE-1c: the controller half of "Complete now" / "Complete all". */
class DownloadsControllerIncompleteTest {

    @Test
    fun `MUTATION PIN -- Complete all posts exactly the listed ids to prefill verify, then re-reads the jobs`() = runTest {
        val jobs = CompletionJobs()
        val c = DownloadsController(jobs, CompletionNoGames(), CompletionStrings())
        c.startCompletion(this, listOf(30, 10), COMPLETE_ALL)
        advanceUntilIdle()
        assertEquals(listOf(listOf(30, 10)), jobs.verifies)
        assertTrue("never the plain, non-forced prefill", jobs.prefills.isEmpty())
        assertEquals(1, jobs.listCalls)
        assertEquals("2 queued to complete", c.toast)
        assertNull(c.incompleteInFlight)
    }

    @Test
    fun `Complete now toasts the single-game wording`() = runTest {
        val jobs = CompletionJobs()
        val c = DownloadsController(jobs, CompletionNoGames(), CompletionStrings())
        c.startCompletion(this, listOf(10), 10)
        advanceUntilIdle()
        assertEquals("Queued to complete.", c.toast)
    }

    @Test
    fun `one request at a time -- a second press while one is in flight sends nothing, an empty list nothing`() = runTest {
        val gate = CompletableDeferred<Unit>()
        val jobs = CompletionJobs(gate)
        val c = DownloadsController(jobs, CompletionNoGames(), CompletionStrings())
        c.startCompletion(this, emptyList(), COMPLETE_ALL)
        c.startCompletion(this, listOf(10), 10)
        advanceUntilIdle()
        assertEquals(10, c.incompleteInFlight)
        c.startCompletion(this, listOf(20), 20)
        c.startCompletion(this, listOf(10, 20), COMPLETE_ALL)
        gate.complete(Unit)
        advanceUntilIdle()
        assertEquals(listOf(listOf(10)), jobs.verifies)
        assertNull(c.incompleteInFlight)
    }
}
