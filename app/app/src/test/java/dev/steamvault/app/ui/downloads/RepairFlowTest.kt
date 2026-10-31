package dev.steamvault.app.ui.downloads

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.CacheDeletionOut
import dev.steamvault.app.net.model.GcJobRef
import dev.steamvault.app.net.model.RepairDepotOut
import dev.steamvault.app.net.model.RepairOut
import dev.steamvault.app.repo.CacheRepository
import dev.steamvault.app.ui.library.logic.RepairFlowState
import dev.steamvault.app.ui.library.logic.RepairWording
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.async
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * WP ANDROID-DL-2: the glue of [RepairFlow] (twin of web
 * `components/repair-flow.js`) against a recording fake repository: the dry
 * run first, EXECUTE only after confirm, honest toasts and re-reads, and a
 * late answer of a closed flow dropped.
 */
class RepairFlowTest {

    private class FakeCache : CacheRepository {
        val calls = mutableListOf<Pair<Int, Boolean>>()
        var dryRun: RepairOut = report()
        var executeOut: RepairOut = report().copy(execute = true, removed_chunks = 3, removed_bytes = 3_145_728, job_id = 41)
        var failDry: VaultApiError? = null
        var failExecute: VaultApiError? = null
        var gate: CompletableDeferred<Unit>? = null

        override suspend fun delete(appid: Int): CacheDeletionOut = error("unused")
        override suspend fun gc(appid: Int, execute: Boolean): GcJobRef = error("unused")
        override suspend fun repair(appid: Int, execute: Boolean): RepairOut {
            calls.add(appid to execute)
            gate?.await()
            if (execute) {
                failExecute?.let { throw it }
                return executeOut
            }
            failDry?.let { throw it }
            return dryRun
        }
    }

    private companion object {
        fun report(damaged: Int = 3) = RepairOut(
            appid = 500, damaged_chunks = damaged, damaged_bytes = damaged * 1_048_576L,
            depots = listOf(RepairDepotOut(2010081, "1", damaged, damaged * 1_048_576L)),
        )
    }

    private val toasts = mutableListOf<String>()
    private var refreshes = 0
    private val onRefresh: suspend () -> Unit = { refreshes++ }

    @Test
    fun `dry run, confirm, execute, result with the job`() = runTest {
        val repo = FakeCache()
        val flow = RepairFlow(repo)
        flow.start(500, "Emberreach", { toasts.add(it) }, onRefresh)
        assertEquals(listOf(500 to false), repo.calls)
        assertTrue(flow.state is RepairFlowState.Confirm)
        flow.confirm(onRefresh)
        assertEquals(listOf(500 to false, 500 to true), repo.calls)
        assertTrue(flow.state is RepairFlowState.Done)
        assertEquals(1, refreshes)
        assertEquals(41, flow.showJob())
        assertEquals(RepairFlowState.Idle, flow.state)
    }

    @Test
    fun `MUTATION PIN -- cancel never sends EXECUTE`() = runTest {
        val repo = FakeCache()
        val flow = RepairFlow(repo)
        flow.start(500, "Emberreach", { toasts.add(it) }, onRefresh)
        flow.close()
        flow.confirm(onRefresh)
        assertEquals(listOf(500 to false), repo.calls)
        assertEquals(RepairFlowState.Idle, flow.state)
    }

    @Test
    fun `nothing damaged -- a toast and a re-read, no dialog`() = runTest {
        val repo = FakeCache().apply { dryRun = report(damaged = 0) }
        val flow = RepairFlow(repo)
        flow.start(500, "Emberreach", { toasts.add(it) }, onRefresh)
        assertEquals(RepairFlowState.Idle, flow.state)
        assertEquals(listOf(RepairWording.NOTHING_TEXT), toasts)
        assertEquals(1, refreshes)
    }

    @Test
    fun `a failed dry run toasts the honest reason (409 busy re-reads)`() = runTest {
        val repo = FakeCache().apply { failDry = VaultApiError.forHttpStatus(409, "POST", "/v1/cache/500/repair", "Job 7 is running") }
        val flow = RepairFlow(repo)
        flow.start(500, "Emberreach", { toasts.add(it) }, onRefresh)
        assertEquals(RepairFlowState.Idle, flow.state)
        assertEquals(listOf(RepairWording.BUSY_TEXT), toasts)
        assertEquals(1, refreshes)
    }

    @Test
    fun `a failed execute keeps the confirm open with the error`() = runTest {
        val repo = FakeCache().apply { failExecute = VaultApiError.forHttpStatus(500, "POST", "/v1/cache/500/repair", "guard") }
        val flow = RepairFlow(repo)
        flow.start(500, "Emberreach", { toasts.add(it) }, onRefresh)
        flow.confirm(onRefresh)
        val state = flow.state as RepairFlowState.Confirm
        assertEquals(RepairWording.EXECUTE_UNKNOWN_TEXT, state.error)
        assertEquals(1, refreshes)
    }

    @Test
    fun `MUTATION PIN -- a late dry-run answer of a closed flow lands nowhere`() = runTest {
        val repo = FakeCache().apply { gate = CompletableDeferred() }
        val flow = RepairFlow(repo)
        val first = async { flow.start(500, "Emberreach", { toasts.add(it) }, onRefresh) }
        testScheduler.runCurrent()
        assertTrue(flow.state is RepairFlowState.Checking)
        flow.reset() // the sheet was closed meanwhile
        repo.gate!!.complete(Unit)
        first.await()
        assertEquals(RepairFlowState.Idle, flow.state)
        assertTrue(toasts.isEmpty())
        assertNull(flow.showJob())
    }
}
