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

    // -----------------------------------------------------------------
    // Review S1 (web-review bug class): a late answer of flow A must not
    // land in a newer flow B. One gate per repository call, so each answer
    // is released exactly when the test says.
    // -----------------------------------------------------------------

    /** Answers per call: [answer] builds the reply for (appid, execute); each
     * call waits for its own gate (call order = [gates] index). */
    private class GatedCache(private val answer: (Int, Boolean) -> RepairOut) : CacheRepository {
        val gates = mutableListOf<CompletableDeferred<VaultApiError?>>()
        val calls = mutableListOf<Pair<Int, Boolean>>()
        override suspend fun delete(appid: Int): CacheDeletionOut = error("unused")
        override suspend fun gc(appid: Int, execute: Boolean): GcJobRef = error("unused")
        override suspend fun repair(appid: Int, execute: Boolean): RepairOut {
            val gate = CompletableDeferred<VaultApiError?>()
            gates.add(gate)
            calls.add(appid to execute)
            gate.await()?.let { throw it }
            return answer(appid, execute)
        }
    }

    private fun answerFor(appid: Int, execute: Boolean): RepairOut {
        val damaged = if (appid == 500) 3 else 7
        val base = report(damaged).copy(appid = appid)
        return if (execute) base.copy(execute = true, removed_chunks = damaged, job_id = appid + 1) else base
    }

    private val aNotice = { msg: String -> toasts.add("A: $msg"); Unit }
    private val bNotice = { msg: String -> toasts.add("B: $msg"); Unit }

    @Test
    fun `MUTATION PIN -- A's late failed dry run while B is Checking says nothing and leaves B alone`() = runTest {
        val repo = GatedCache(::answerFor)
        val flow = RepairFlow(repo)
        val a = async { flow.start(500, "Emberreach", aNotice, onRefresh) }
        testScheduler.runCurrent()
        flow.reset() // sheet closed
        val b = async { flow.start(600, "Marrowlight", bNotice, onRefresh) }
        testScheduler.runCurrent()
        // A's answer arrives late: a 409 that would toast BUSY_TEXT and re-read.
        repo.gates[0].complete(VaultApiError.forHttpStatus(409, "POST", "/v1/cache/500/repair", "Job 7 is running"))
        a.await()
        val checking = flow.state as RepairFlowState.Checking
        assertEquals(600, checking.appid)
        assertEquals("Marrowlight", checking.title)
        assertTrue("no toast from flow A: $toasts", toasts.isEmpty())
        assertEquals("no re-read for a closed flow", 0, refreshes)
        // B goes on normally.
        repo.gates[1].complete(null)
        b.await()
        val confirm = flow.state as RepairFlowState.Confirm
        assertEquals("Marrowlight", confirm.title)
        assertEquals(7, confirm.report.damaged_chunks)
    }

    @Test
    fun `MUTATION PIN -- A's late nothing-to-repair answer while B is Checking shows no toast`() = runTest {
        val repo = GatedCache { appid, execute -> if (appid == 500) report(damaged = 0) else answerFor(appid, execute) }
        val flow = RepairFlow(repo)
        val a = async { flow.start(500, "Emberreach", aNotice, onRefresh) }
        testScheduler.runCurrent()
        flow.reset()
        val b = async { flow.start(600, "Marrowlight", bNotice, onRefresh) }
        testScheduler.runCurrent()
        repo.gates[0].complete(null)
        a.await()
        assertEquals("Marrowlight", (flow.state as RepairFlowState.Checking).title)
        assertTrue("no toast from flow A: $toasts", toasts.isEmpty())
        assertEquals(0, refreshes)
        repo.gates[1].complete(null)
        b.await()
    }

    @Test
    fun `MUTATION PIN -- A's late dry-run report while B is in Confirm keeps B's title and counts`() = runTest {
        val repo = GatedCache(::answerFor)
        val flow = RepairFlow(repo)
        val a = async { flow.start(500, "Emberreach", aNotice, onRefresh) }
        testScheduler.runCurrent()
        flow.reset()
        val b = async { flow.start(600, "Marrowlight", bNotice, onRefresh) }
        testScheduler.runCurrent()
        repo.gates[1].complete(null)
        b.await()
        assertTrue(flow.state is RepairFlowState.Confirm)
        repo.gates[0].complete(null)
        a.await()
        val confirm = flow.state as RepairFlowState.Confirm
        assertEquals(600, confirm.appid)
        assertEquals("Marrowlight", confirm.title)
        assertEquals(7, confirm.report.damaged_chunks)
        assertTrue(toasts.isEmpty())
    }

    @Test
    fun `MUTATION PIN -- A's late execute answer while B is Executing keeps B's flow`() = runTest {
        val repo = GatedCache(::answerFor)
        val flow = RepairFlow(repo)
        // Flow A up to its execute, then the sheet closes with it in flight.
        val aStart = async { flow.start(500, "Emberreach", aNotice, onRefresh) }
        testScheduler.runCurrent()
        repo.gates[0].complete(null)
        aStart.await()
        val aExec = async { flow.confirm(onRefresh) }
        testScheduler.runCurrent()
        assertTrue(flow.state is RepairFlowState.Executing)
        flow.reset()
        // Flow B up to its own execute.
        val bStart = async { flow.start(600, "Marrowlight", bNotice, onRefresh) }
        testScheduler.runCurrent()
        repo.gates[2].complete(null)
        bStart.await()
        val bExec = async { flow.confirm(onRefresh) }
        testScheduler.runCurrent()
        assertEquals(listOf(500 to false, 500 to true, 600 to false, 600 to true), repo.calls)
        // A's execute answers late (a failure that would put an error into a confirm).
        repo.gates[1].complete(VaultApiError.forHttpStatus(500, "POST", "/v1/cache/500/repair", "guard"))
        aExec.await()
        val executing = flow.state as RepairFlowState.Executing
        assertEquals(600, executing.appid)
        assertEquals("Marrowlight", executing.title)
        assertEquals(7, executing.report.damaged_chunks)
        // B's own answer completes B.
        repo.gates[3].complete(null)
        bExec.await()
        val done = flow.state as RepairFlowState.Done
        assertEquals("Marrowlight", done.title)
        assertEquals(601, done.report.job_id)
        assertTrue(toasts.isEmpty())
    }
}
