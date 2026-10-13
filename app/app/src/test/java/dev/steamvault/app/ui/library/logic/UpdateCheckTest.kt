package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.UpdateCheckOut
import dev.steamvault.app.net.model.UpdateStatusOut
import dev.steamvault.app.repo.UpdatesRepository
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Scripted `/v1/updates/...`: [check] answers [start] (or throws
 * [checkError]); each [status] call takes the next entry of [statuses]
 * (an exception entry is thrown), repeating the last one when exhausted. */
internal class ScriptedUpdates(
    private val start: UpdateCheckOut = UpdateCheckOut(UPDATE_CHECK_STARTED, enabled = true),
    private val checkError: VaultApiError? = null,
    private val statuses: List<Any> = listOf(finishedStatus()),
) : UpdatesRepository {
    var checkCalls = 0
    var statusCalls = 0

    override suspend fun check(): UpdateCheckOut {
        checkCalls++
        checkError?.let { throw it }
        return start
    }

    override suspend fun status(): UpdateStatusOut {
        val next = statuses[minOf(statusCalls, statuses.size - 1)]
        statusCalls++
        if (next is VaultApiError) throw next
        return next as UpdateStatusOut
    }
}

internal fun runningStatus() = UpdateStatusOut(
    enabled = true, running = true, last_trigger = "manual", last_started_at = "2026-10-13T10:00:00Z",
)

internal fun finishedStatus(
    outcome: String? = "completed",
    apps: Int? = 12,
    failed: Int? = 0,
    stale: Int? = 0,
) = UpdateStatusOut(
    enabled = true, running = false, last_trigger = "manual",
    last_started_at = "2026-10-13T10:00:00Z", last_finished_at = "2026-10-13T10:01:00Z",
    last_outcome = outcome, last_apps = apps, last_ok = apps?.minus(failed ?: 0),
    last_failed = failed, last_stale = stale,
)

private fun notFound() = VaultApiError.NotFound("POST /v1/updates/check failed (404)", 404)
private fun serverError() = VaultApiError.Server("GET /v1/updates/status failed (503)", 503)

/**
 * WP UPD-3: the "Check updates" state machine (`UpdateCheck.kt`): start,
 * bounded poll, the result each answer maps to, and the toast decision.
 * Virtual time (runTest), so the 3 s polls cost nothing.
 */
class UpdateCheckTest {

    @Test
    fun `MUTATION PIN -- started, polled while running, Finished with the settled row`() = runTest {
        val repo = ScriptedUpdates(statuses = listOf(runningStatus(), runningStatus(), finishedStatus(stale = 2)))
        val result = runUpdateCheck(repo)
        assertEquals(UpdateCheckResult.Finished(finishedStatus(stale = 2)), result)
        assertEquals(1, repo.checkCalls)
        assertEquals(3, repo.statusCalls)
    }

    @Test
    fun `already_running joins the running check instead of failing`() = runTest {
        val repo = ScriptedUpdates(
            start = UpdateCheckOut(UPDATE_CHECK_ALREADY_RUNNING, enabled = true),
            statuses = listOf(runningStatus(), finishedStatus()),
        )
        assertTrue(runUpdateCheck(repo) is UpdateCheckResult.Finished)
        assertEquals(2, repo.statusCalls)
    }

    @Test
    fun `MUTATION PIN -- disabled is Disabled and the status is never polled`() = runTest {
        val repo = ScriptedUpdates(start = UpdateCheckOut(UPDATE_CHECK_DISABLED, enabled = false))
        assertEquals(UpdateCheckResult.Disabled, runUpdateCheck(repo))
        assertEquals(0, repo.statusCalls)
    }

    @Test
    fun `a status row saying the oracle went off mid-run is Disabled`() = runTest {
        val off = runningStatus().copy(enabled = false, running = false)
        assertEquals(UpdateCheckResult.Disabled, runUpdateCheck(ScriptedUpdates(statuses = listOf(off))))
    }

    @Test
    fun `MUTATION PIN -- a 404 on the start call is Unsupported (vault from before UPD-1)`() = runTest {
        val repo = ScriptedUpdates(checkError = notFound())
        assertEquals(UpdateCheckResult.Unsupported, runUpdateCheck(repo))
        assertEquals(0, repo.statusCalls)
    }

    @Test
    fun `any other start failure is a Failure carrying the error`() = runTest {
        val err = VaultApiError.Server("POST /v1/updates/check failed (503)", 503)
        assertEquals(UpdateCheckResult.Failure(err), runUpdateCheck(ScriptedUpdates(checkError = err)))
    }

    @Test
    fun `one failed status poll is tolerated, the wait goes on`() = runTest {
        val repo = ScriptedUpdates(statuses = listOf(runningStatus(), serverError(), runningStatus(), finishedStatus()))
        assertTrue(runUpdateCheck(repo) is UpdateCheckResult.Finished)
        assertEquals(4, repo.statusCalls)
    }

    @Test
    fun `MUTATION PIN -- three failed status polls in a row end the wait as a Failure`() = runTest {
        val err = serverError()
        val repo = ScriptedUpdates(statuses = listOf(runningStatus(), err, err, err, finishedStatus()))
        assertEquals(UpdateCheckResult.Failure(err), runUpdateCheck(repo))
        assertEquals(4, repo.statusCalls)
    }

    @Test
    fun `MUTATION PIN -- the poll is bounded and ends as StillRunning, not an error`() = runTest {
        val repo = ScriptedUpdates(statuses = listOf(runningStatus()))
        val result = runUpdateCheck(repo, pollIntervalMs = 3_000L, maxPolls = 5)
        assertEquals(UpdateCheckResult.StillRunning, result)
        assertEquals(5, repo.statusCalls)
        assertEquals(15_000L, testScheduler.currentTime)
    }

    @Test
    fun `the default bound is ten minutes of 3 s polls`() {
        assertEquals(3_000L, UPDATE_CHECK_POLL_MS)
        assertEquals(10 * 60 * 1000L, UPDATE_CHECK_POLL_MS * UPDATE_CHECK_MAX_POLLS)
    }

    @Test
    fun `MUTATION PIN -- cancelling the caller stops the poll (the screen left)`() = runTest {
        val repo = ScriptedUpdates(statuses = listOf(runningStatus()))
        var finished = false
        val job = launch {
            runUpdateCheck(repo)
            finished = true
        }
        testScheduler.advanceTimeBy(UPDATE_CHECK_POLL_MS * 2 + 1)
        val callsAtCancel = repo.statusCalls
        job.cancel()
        testScheduler.advanceUntilIdle()
        assertEquals(2, callsAtCancel)
        assertEquals(callsAtCancel, repo.statusCalls)
        assertFalse(finished)
    }

    // ---- toast decision ------------------------------------------------

    @Test
    fun `MUTATION PIN -- completed maps to Found, NoneFound or NothingCached from the counters`() {
        assertEquals(
            UpdateCheckNotice.Found(stale = 3, failed = 1),
            updateCheckNoticeFor(UpdateCheckResult.Finished(finishedStatus(stale = 3, failed = 1))),
        )
        assertEquals(
            UpdateCheckNotice.NoneFound(failed = 2),
            updateCheckNoticeFor(UpdateCheckResult.Finished(finishedStatus(stale = 0, failed = 2))),
        )
        assertEquals(
            UpdateCheckNotice.NothingCached,
            updateCheckNoticeFor(UpdateCheckResult.Finished(finishedStatus(apps = 0, stale = 0))),
        )
    }

    @Test
    fun `MUTATION PIN -- an outcome other than completed never reads as no updates`() {
        fun notice(outcome: String?) = updateCheckNoticeFor(
            UpdateCheckResult.Finished(finishedStatus(outcome = outcome, stale = 0)),
        )
        assertEquals(UpdateCheckNotice.StoppedEarly, notice("bailed_out"))
        assertEquals(UpdateCheckNotice.NotFinished, notice("aborted"))
        assertEquals(UpdateCheckNotice.NotFinished, notice("interrupted"))
        assertEquals(UpdateCheckNotice.VaultError, notice("error"))
        assertEquals(UpdateCheckNotice.FinishedUnknown, notice(null))
        assertEquals(UpdateCheckNotice.FinishedUnknown, notice("something_new"))
    }

    @Test
    fun `null counters on a completed row count as zero`() {
        assertEquals(
            UpdateCheckNotice.NothingCached,
            updateCheckNoticeFor(UpdateCheckResult.Finished(finishedStatus(apps = null, failed = null, stale = null))),
        )
    }

    @Test
    fun `the non-finished results map one to one, and warn where the user must look`() {
        assertEquals(UpdateCheckNotice.Disabled, updateCheckNoticeFor(UpdateCheckResult.Disabled))
        assertEquals(UpdateCheckNotice.Unsupported, updateCheckNoticeFor(UpdateCheckResult.Unsupported))
        assertEquals(UpdateCheckNotice.StillRunning, updateCheckNoticeFor(UpdateCheckResult.StillRunning))
        val err = serverError()
        assertEquals(UpdateCheckNotice.Failed(err.message), updateCheckNoticeFor(UpdateCheckResult.Failure(err)))

        assertTrue(UpdateCheckNotice.Disabled.warn)
        assertTrue(UpdateCheckNotice.Unsupported.warn)
        assertTrue(UpdateCheckNotice.Failed(null).warn)
        assertTrue(UpdateCheckNotice.StoppedEarly.warn)
        assertFalse(UpdateCheckNotice.Found(1, 0).warn)
        assertFalse(UpdateCheckNotice.StillRunning.warn)
    }
}
