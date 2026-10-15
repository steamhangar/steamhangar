package dev.steamvault.app.ui.downloads

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.GameDetail
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.JobControlOut
import dev.steamvault.app.net.model.JobDetail
import dev.steamvault.app.net.model.JobMoveOut
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.net.model.PrefillJobRef
import dev.steamvault.app.repo.GamesRepository
import dev.steamvault.app.repo.JobsRepository
import dev.steamvault.app.ui.downloads.logic.QueueMove
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

private fun queuedJob(id: Int, pos: Int) = JobSummary(
    id = id,
    appid = id,
    type = "prefill",
    status = "queued",
    created_at = "2026-08-01T00:00:00Z",
    queue_position = pos,
)

private class MoveJobs(
    private val gate: CompletableDeferred<Unit>? = null,
    private val failWith: VaultApiError? = null,
) : JobsRepository {
    val listed: List<JobSummary> = listOf(queuedJob(1, 0), queuedJob(2, 1), queuedJob(3, 2))
    val moves = mutableListOf<Pair<Int, Int>>()
    override suspend fun list(limit: Int): List<JobSummary> = listed
    override suspend fun detail(id: Int): JobDetail = error("unused")
    override suspend fun prefill(appids: List<Int>): List<PrefillJobRef> = error("unused")
    override suspend fun prefillCached(): List<PrefillJobRef> = error("unused")
    override suspend fun prefillVerify(appids: List<Int>): List<PrefillJobRef> = error("unused")
    override suspend fun cancel(id: Int): JobControlOut = error("unused")
    override suspend fun pause(id: Int): JobControlOut = error("unused")
    override suspend fun resume(id: Int): JobControlOut = error("unused")
    override suspend fun moveJob(id: Int, position: Int): JobMoveOut {
        moves += id to position
        gate?.await()
        failWith?.let { throw it }
        return JobMoveOut(job_id = id, status = "queued", outcome = "immediate", detail = "Moved.", queue_position = position)
    }
}

private class NoGamesRepo : GamesRepository {
    override suspend fun list(): List<GameSummary> = emptyList()
    override suspend fun detail(appid: Int): GameDetail = error("unused")
}

private class MoveStrings : DownloadsStrings {
    override fun pauseRequested() = "pause"
    override fun resuming() = "resume"
    override fun cancelRequested() = "cancel"
    override fun actionFailedFallback() = "failed"
    override fun logFetchErrorFallback() = "log failed"
    override fun queuedForDownload() = "queued"
}

/** WP D20: the controller half of reordering the queue. */
class DownloadsControllerMoveTest {
    private fun order(c: DownloadsController) = c.jobs.sortedBy { it.queue_position }.map { it.id }

    @Test
    fun `moving a row down sends the new 0-based position and applies it optimistically`() = runTest {
        val gate = CompletableDeferred<Unit>()
        val repo = MoveJobs(gate)
        val c = DownloadsController(repo, NoGamesRepo(), MoveStrings())
        c.refreshJobsOnce()
        c.moveQueued(this, 1, QueueMove.DOWN)
        advanceUntilIdle()
        assertEquals(listOf(1 to 1), repo.moves)
        assertEquals(listOf(2, 1, 3), order(c))
        assertTrue(c.moveInFlight)
        gate.complete(Unit)
        advanceUntilIdle()
        assertFalse(c.moveInFlight)
    }

    @Test
    fun `a failed move rolls back, shows the server message and re-reads the order`() = runTest {
        val repo = MoveJobs(failWith = VaultApiError.Validation("Job 2 is running", 409, "Job 2 is running"))
        val c = DownloadsController(repo, NoGamesRepo(), MoveStrings())
        c.refreshJobsOnce()
        c.moveQueued(this, 2, QueueMove.UP)
        advanceUntilIdle()
        assertEquals(listOf(1, 2, 3), order(c))
        assertEquals("Job 2 is running", c.toast)
        assertFalse(c.moveInFlight)
    }

    @Test
    fun `a poll during a move does not overwrite the optimistic order`() = runTest {
        val gate = CompletableDeferred<Unit>()
        val c = DownloadsController(MoveJobs(gate), NoGamesRepo(), MoveStrings())
        c.refreshJobsOnce()
        c.moveQueued(this, 3, QueueMove.UP)
        advanceUntilIdle()
        c.refreshJobsOnce()
        assertEquals(listOf(1, 3, 2), order(c))
        gate.complete(Unit)
        advanceUntilIdle()
    }

    @Test
    fun `edge moves, unknown rows and a second move in flight send nothing`() = runTest {
        val gate = CompletableDeferred<Unit>()
        val repo = MoveJobs(gate)
        val c = DownloadsController(repo, NoGamesRepo(), MoveStrings())
        c.refreshJobsOnce()
        c.moveQueued(this, 1, QueueMove.UP)
        c.moveQueued(this, 3, QueueMove.DOWN)
        c.moveQueued(this, 99, QueueMove.UP)
        advanceUntilIdle()
        assertTrue(repo.moves.isEmpty())
        c.moveQueued(this, 2, QueueMove.UP)
        c.moveQueued(this, 3, QueueMove.UP)
        advanceUntilIdle()
        assertEquals(1, repo.moves.size)
        gate.complete(Unit)
        advanceUntilIdle()
    }
}
