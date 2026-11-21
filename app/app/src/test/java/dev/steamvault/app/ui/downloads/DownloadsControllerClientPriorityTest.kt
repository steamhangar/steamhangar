package dev.steamvault.app.ui.downloads

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.ClientPriorityOut
import dev.steamvault.app.net.model.GameDetail
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.JobControlOut
import dev.steamvault.app.net.model.JobDetail
import dev.steamvault.app.net.model.JobMoveOut
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.net.model.PrefillJobRef
import dev.steamvault.app.repo.GamesRepository
import dev.steamvault.app.repo.JobsRepository
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

private fun cpJob(id: Int, status: String) =
    JobSummary(id = id, appid = id, type = "prefill", status = status, created_at = "2026-11-21T09:00:00Z")

private val HOLD = ClientPriorityOut(enabled = true, holding = true, resume_at = "2026-11-21T10:02:00Z")

private class HoldJobs(var listed: List<JobSummary>, var answer: () -> ClientPriorityOut?) : JobsRepository {
    var asked = 0
    override suspend fun list(limit: Int): List<JobSummary> = listed
    override suspend fun detail(id: Int): JobDetail = error("unused")
    override suspend fun prefill(appids: List<Int>): List<PrefillJobRef> = error("unused")
    override suspend fun prefillCached(): List<PrefillJobRef> = error("unused")
    override suspend fun prefillVerify(appids: List<Int>): List<PrefillJobRef> = error("unused")
    override suspend fun cancel(id: Int): JobControlOut = error("unused")
    override suspend fun pause(id: Int): JobControlOut = error("unused")
    override suspend fun resume(id: Int): JobControlOut = error("unused")
    override suspend fun moveJob(id: Int, position: Int): JobMoveOut = error("unused")
    override suspend fun clientPriority(): ClientPriorityOut? {
        asked += 1
        return answer()
    }
}

/** A fake that does not override [JobsRepository.clientPriority] (like the demo). */
private class PlainJobs : JobsRepository {
    override suspend fun list(limit: Int): List<JobSummary> = listOf(cpJob(1, "paused"))
    override suspend fun detail(id: Int): JobDetail = error("unused")
    override suspend fun prefill(appids: List<Int>): List<PrefillJobRef> = error("unused")
    override suspend fun prefillCached(): List<PrefillJobRef> = error("unused")
    override suspend fun prefillVerify(appids: List<Int>): List<PrefillJobRef> = error("unused")
    override suspend fun cancel(id: Int): JobControlOut = error("unused")
    override suspend fun pause(id: Int): JobControlOut = error("unused")
    override suspend fun resume(id: Int): JobControlOut = error("unused")
    override suspend fun moveJob(id: Int, position: Int): JobMoveOut = error("unused")
}

private class CpNoGames : GamesRepository {
    override suspend fun list(): List<GameSummary> = emptyList()
    override suspend fun detail(appid: Int): GameDetail = error("unused")
}

private class CpStrings : DownloadsStrings {
    override fun pauseRequested() = "pause"
    override fun resuming() = "resume"
    override fun cancelRequested() = "cancel"
    override fun actionFailedFallback() = "failed"
    override fun logFetchErrorFallback() = "log failed"
    override fun queuedForDownload() = "queued"
}

/** WP CAP-503-FIX-1b: the controller half of the hold texts (web `holdPoll`). */
class DownloadsControllerClientPriorityTest {
    @Test
    fun `MUTATION PIN -- the hold is asked while a job is active and kept`() = runTest {
        val repo = HoldJobs(listOf(cpJob(1, "paused"), cpJob(2, "queued"))) { HOLD }
        val c = DownloadsController(repo, CpNoGames(), CpStrings())
        c.refreshJobsOnce()
        c.refreshClientPriorityOnce()
        assertEquals(1, repo.asked)
        assertEquals(HOLD, c.clientPriority)
    }

    @Test
    fun `MUTATION PIN -- nothing is asked while no job is queued, running or paused, and a stale hold is dropped`() = runTest {
        val repo = HoldJobs(listOf(cpJob(1, "running"))) { HOLD }
        val c = DownloadsController(repo, CpNoGames(), CpStrings())
        c.refreshJobsOnce()
        c.refreshClientPriorityOnce()
        assertEquals(HOLD, c.clientPriority)
        repo.listed = listOf(cpJob(1, "done"))
        c.refreshJobsOnce()
        c.refreshClientPriorityOnce()
        assertEquals("no request with nothing active", 1, repo.asked)
        assertNull(c.clientPriority)
    }

    @Test
    fun `a failed read (an older server's 404) shows no hold rather than a stale one`() = runTest {
        var fail = false
        val repo = HoldJobs(listOf(cpJob(1, "paused"))) { if (fail) throw VaultApiError.NotFound("Not Found", 404) else HOLD }
        val c = DownloadsController(repo, CpNoGames(), CpStrings())
        c.refreshJobsOnce()
        c.refreshClientPriorityOnce()
        assertEquals(HOLD, c.clientPriority)
        fail = true
        c.refreshClientPriorityOnce()
        assertNull(c.clientPriority)
        assertTrue(repo.asked == 2)
    }

    @Test
    fun `a repository without the route (demo, fakes) reads as not known`() = runTest {
        val c = DownloadsController(PlainJobs(), CpNoGames(), CpStrings())
        c.refreshJobsOnce()
        c.refreshClientPriorityOnce()
        assertNull(c.clientPriority)
    }

    @Test
    fun `MUTATION PIN -- a stopped hold poll leaves no stale hold behind`() = runTest {
        val repo = HoldJobs(listOf(cpJob(1, "paused"))) { HOLD }
        val c = DownloadsController(repo, CpNoGames(), CpStrings())
        c.refreshJobsOnce()
        val poll = launch { c.pollClientPriorityForever() }
        runCurrent()
        assertEquals(HOLD, c.clientPriority)
        poll.cancel()
        runCurrent()
        assertNull(c.clientPriority)
    }
}
