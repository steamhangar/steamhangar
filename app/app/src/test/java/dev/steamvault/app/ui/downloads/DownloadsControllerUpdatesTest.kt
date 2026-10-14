package dev.steamvault.app.ui.downloads

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.GameDetail
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.JobControlOut
import dev.steamvault.app.net.model.JobDetail
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.net.model.PrefillJobRef
import dev.steamvault.app.net.model.ScheduleOut
import dev.steamvault.app.repo.GamesRepository
import dev.steamvault.app.repo.JobsRepository
import dev.steamvault.app.repo.ScheduleRepository
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

private fun unused(): Nothing = error("not used here")

/** Records every `POST /v1/prefill`; answers each app as freshly queued. */
private class RecordingJobs(private val gate: CompletableDeferred<Unit>? = null) : JobsRepository {
    val prefills = mutableListOf<List<Int>>()
    var listCalls = 0
    override suspend fun list(limit: Int): List<JobSummary> {
        listCalls++
        return emptyList()
    }
    override suspend fun detail(id: Int): JobDetail = unused()
    override suspend fun prefill(appids: List<Int>): List<PrefillJobRef> {
        prefills += appids
        gate?.await()
        return appids.map { PrefillJobRef(appid = it, job_id = it, status = "queued", deduplicated = false) }
    }
    override suspend fun prefillCached(): List<PrefillJobRef> = unused()
    override suspend fun cancel(id: Int): JobControlOut = unused()
    override suspend fun pause(id: Int): JobControlOut = unused()
    override suspend fun resume(id: Int): JobControlOut = unused()
}

private class NoGames : GamesRepository {
    override suspend fun list(): List<GameSummary> = emptyList()
    override suspend fun detail(appid: Int): GameDetail = unused()
}

private class Strings : DownloadsStrings {
    override fun pauseRequested() = "pause"
    override fun resuming() = "resume"
    override fun cancelRequested() = "cancel"
    override fun actionFailedFallback() = "failed"
    override fun logFetchErrorFallback() = "log failed"
    override fun queuedForDownload() = "queued"
}

/** WP UPD-6: the controller half of "Update now" / "Update all". */
class DownloadsControllerUpdatesTest {

    @Test
    fun `MUTATION PIN -- Update all posts exactly the listed ids, then re-reads the jobs`() = runTest {
        val jobs = RecordingJobs()
        val c = DownloadsController(jobs, NoGames(), Strings())
        c.startUpdates(this, listOf(30, 10, 20), UPDATES_ALL)
        advanceUntilIdle()
        assertEquals(listOf(listOf(30, 10, 20)), jobs.prefills)
        assertEquals(1, jobs.listCalls)
        assertEquals("3 queued for update", c.toast)
        assertNull(c.updatesInFlight)
    }

    @Test
    fun `one request at a time -- a second press while one is in flight sends nothing`() = runTest {
        val gate = CompletableDeferred<Unit>()
        val jobs = RecordingJobs(gate)
        val c = DownloadsController(jobs, NoGames(), Strings())
        c.startUpdates(this, listOf(10), 10)
        advanceUntilIdle()
        assertEquals(10, c.updatesInFlight)
        c.startUpdates(this, listOf(20), 20)
        c.startUpdates(this, listOf(10, 20), UPDATES_ALL)
        gate.complete(Unit)
        advanceUntilIdle()
        assertEquals(listOf(listOf(10)), jobs.prefills)
        assertNull(c.updatesInFlight)
    }

    @Test
    fun `an empty list sends nothing`() = runTest {
        val jobs = RecordingJobs()
        val c = DownloadsController(jobs, NoGames(), Strings())
        c.startUpdates(this, emptyList(), UPDATES_ALL)
        advanceUntilIdle()
        assertTrue(jobs.prefills.isEmpty())
    }

    @Test
    fun `the schedule is read once, a failed read leaves it unknown`() = runTest {
        val sched = ScheduleOut(enabled = false, interval_minutes = 60, client_stale_days = 7, server_timezone = "UTC")
        val ok = DownloadsController(RecordingJobs(), NoGames(), Strings(), object : ScheduleRepository {
            override suspend fun get() = sched
        })
        ok.loadSchedule()
        assertEquals(sched, ok.schedule)

        val failing = DownloadsController(RecordingJobs(), NoGames(), Strings(), object : ScheduleRepository {
            override suspend fun get(): ScheduleOut = throw VaultApiError.Server("down", 503)
        })
        failing.loadSchedule()
        assertNull(failing.schedule)

        val none = DownloadsController(RecordingJobs(), NoGames(), Strings())
        none.loadSchedule()
        assertNull(none.schedule)
    }
}
