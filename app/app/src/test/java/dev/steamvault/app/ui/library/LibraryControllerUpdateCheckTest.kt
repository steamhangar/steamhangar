package dev.steamvault.app.ui.library

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.CacheDeletionOut
import dev.steamvault.app.net.model.GameDetail
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.GcJobRef
import dev.steamvault.app.net.model.JobControlOut
import dev.steamvault.app.net.model.JobDetail
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.net.model.MappingEntry
import dev.steamvault.app.net.model.PrefillJobRef
import dev.steamvault.app.net.model.UpdateCheckOut
import dev.steamvault.app.repo.CacheRepository
import dev.steamvault.app.repo.GamesRepository
import dev.steamvault.app.repo.JobsRepository
import dev.steamvault.app.repo.MappingRepository
import dev.steamvault.app.repo.UpdatesRepository
import dev.steamvault.app.storage.InMemoryLibraryPreferences
import dev.steamvault.app.ui.library.logic.ScriptedUpdates
import dev.steamvault.app.ui.library.logic.UPDATES_FILTER_KEY
import dev.steamvault.app.ui.library.logic.UPDATE_CHECK_DISABLED
import dev.steamvault.app.ui.library.logic.UpdateCheckNotice
import dev.steamvault.app.ui.library.logic.finishedStatus
import dev.steamvault.app.ui.library.logic.runningStatus
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

private fun unused(): Nothing = error("not used by the update check")

/** Counts `GET /v1/games` reads; the second and later read carries the badge. */
private class CountingGames : GamesRepository {
    var listCalls = 0
    override suspend fun list(): List<GameSummary> {
        listCalls++
        return listOf(
            GameSummary(
                appid = 440, name = "Aurora Cascade", status = "done", depot_count = 1,
                size_bytes = 5_000_000_000, update_available = listCalls > 1,
            ),
        )
    }
    override suspend fun detail(appid: Int): GameDetail = unused()
}

private class NoJobs : JobsRepository {
    override suspend fun list(limit: Int): List<JobSummary> = emptyList()
    override suspend fun detail(id: Int): JobDetail = unused()
    override suspend fun prefill(appids: List<Int>): List<PrefillJobRef> = unused()
    override suspend fun prefillCached(): List<PrefillJobRef> = unused()
    override suspend fun cancel(id: Int): JobControlOut = unused()
    override suspend fun pause(id: Int): JobControlOut = unused()
    override suspend fun resume(id: Int): JobControlOut = unused()
}

private class NoMapping : MappingRepository {
    override suspend fun list(): List<MappingEntry> = unused()
}

private class NoCache : CacheRepository {
    override suspend fun delete(appid: Int): CacheDeletionOut = unused()
    override suspend fun gc(appid: Int, execute: Boolean): GcJobRef = unused()
}

/** Records the notice; text is the notice's toString so tests can read it. */
private class RecordingStrings : LibraryStrings {
    val notices = mutableListOf<UpdateCheckNotice>()
    override fun queuedForDownload() = "queued"
    override fun pauseRequested() = "pause"
    override fun resuming() = "resuming"
    override fun actionFailedFallback() = "failed"
    override fun deletePlanErrorFallback() = "plan failed"
    override fun jobsQueued(count: Int) = "$count queued"
    override fun freed(freedText: String, failedCount: Int) = "freed $freedText"
    override fun updateCheckNotice(notice: UpdateCheckNotice): String {
        notices += notice
        return notice.toString()
    }
}

/**
 * WP UPD-3: [LibraryController]'s "Check updates" glue -- busy flag, the
 * games re-read after the check, the toast, and the 404 path that hides
 * the action and the "Updates" chip. The poll itself is `UpdateCheckTest`'s.
 */
class LibraryControllerUpdateCheckTest {

    private fun controller(updates: UpdatesRepository, games: GamesRepository = CountingGames(), strings: LibraryStrings = RecordingStrings()) =
        LibraryController(
            games, NoJobs(), NoMapping(), NoCache(), updates, InMemoryLibraryPreferences(), strings,
        )

    @Test
    fun `MUTATION PIN -- a finished check toasts the result and re-reads the games for the badge`() = runTest {
        val games = CountingGames()
        val strings = RecordingStrings()
        val updates = ScriptedUpdates(statuses = listOf(runningStatus(), finishedStatus(stale = 1)))
        val c = controller(updates, games, strings)
        c.refreshGamesOnce()
        assertFalse(c.games.single().update_available)

        c.checkUpdates(this)
        assertTrue("busy as soon as the press lands", c.updateCheckRunning)
        testScheduler.advanceUntilIdle()

        assertFalse(c.updateCheckRunning)
        assertEquals(listOf<UpdateCheckNotice>(UpdateCheckNotice.Found(stale = 1, failed = 0)), strings.notices)
        assertFalse(c.toast!!.warn)
        assertEquals(2, games.listCalls)
        assertTrue(c.games.single().update_available)
    }

    @Test
    fun `a second press while a check runs starts nothing`() = runTest {
        val updates = ScriptedUpdates(statuses = listOf(runningStatus(), finishedStatus()))
        val c = controller(updates)
        c.checkUpdates(this)
        c.checkUpdates(this)
        testScheduler.advanceUntilIdle()
        assertEquals(1, updates.checkCalls)
    }

    @Test
    fun `disabled oracle is a warn toast, the action stays`() = runTest {
        val strings = RecordingStrings()
        val c = controller(ScriptedUpdates(start = UpdateCheckOut(UPDATE_CHECK_DISABLED, enabled = false)), strings = strings)
        c.checkUpdates(this)
        testScheduler.advanceUntilIdle()
        assertEquals(listOf<UpdateCheckNotice>(UpdateCheckNotice.Disabled), strings.notices)
        assertTrue(c.toast!!.warn)
        assertTrue(c.updatesSupported)
        assertFalse("a disabled answer disables the action too", c.updatesEnabled)
    }

    @Test
    fun `MUTATION PIN -- a 404 on the probe hides the feature and leaves the Updates chip`() = runTest {
        val c = controller(
            ScriptedUpdates(statuses = listOf(VaultApiError.NotFound("GET /v1/updates/status failed (404)", 404))),
        )
        c.filterKey = UPDATES_FILTER_KEY
        c.probeUpdatesSupport()
        assertFalse(c.updatesSupported)
        assertEquals("all", c.filterKey)
    }

    @Test
    fun `MUTATION PIN -- oracle off at the probe disables the action and hides nothing`() = runTest {
        val c = controller(ScriptedUpdates(statuses = listOf(finishedStatus().copy(enabled = false))))
        c.filterKey = UPDATES_FILTER_KEY
        c.probeUpdatesSupport()
        assertFalse(c.updatesEnabled)
        assertTrue(c.updatesSupported)
        assertEquals(UPDATES_FILTER_KEY, c.filterKey)
    }

    @Test
    fun `oracle on at the probe keeps the action enabled`() = runTest {
        val c = controller(ScriptedUpdates(statuses = listOf(finishedStatus())))
        c.probeUpdatesSupport()
        assertTrue(c.updatesEnabled)
    }

    @Test
    fun `any other probe failure leaves the feature shown`() = runTest {
        val c = controller(ScriptedUpdates(statuses = listOf(VaultApiError.Server("boom", 503))))
        c.probeUpdatesSupport()
        assertTrue(c.updatesSupported)
    }

    @Test
    fun `a 404 on the press hides the feature and says why`() = runTest {
        val strings = RecordingStrings()
        val c = controller(
            ScriptedUpdates(checkError = VaultApiError.NotFound("POST /v1/updates/check failed (404)", 404)),
            strings = strings,
        )
        c.checkUpdates(this)
        testScheduler.advanceUntilIdle()
        assertFalse(c.updatesSupported)
        assertEquals(listOf<UpdateCheckNotice>(UpdateCheckNotice.Unsupported), strings.notices)
        assertFalse(c.updateCheckRunning)
    }

    @Test
    fun `no toast before the check has ended`() = runTest {
        val c = controller(ScriptedUpdates(statuses = listOf(runningStatus())))
        c.checkUpdates(this)
        testScheduler.advanceTimeBy(10_000L)
        assertNull(c.toast)
        assertTrue(c.updateCheckRunning)
        testScheduler.advanceUntilIdle()
    }
}
