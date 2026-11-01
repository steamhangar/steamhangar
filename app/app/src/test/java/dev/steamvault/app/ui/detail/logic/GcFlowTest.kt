package dev.steamvault.app.ui.detail.logic

import dev.steamvault.app.net.model.JobDetail
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Pins the GC flow's state machine (WP 4b.6 brief: "GC flow state machine
 * (dry-run -> plan-shown -> executing -> done/error, incl. cancellation
 * mid-poll)... GC execute NEVER sent without an explicit user confirm after
 * a dry-run -- pin the state machine cannot reach execute from idle").
 */
class GcFlowTest {

    private fun job(id: Int, status: String, log: String? = null) = JobDetail(
        id = id, appid = 440, type = "gc", status = status, created_at = "2026-08-01T00:00:00Z", log_excerpt = log,
    )

    // ---- the flagship pin ---------------------------------------------------

    @Test
    fun `THE PIN -- ConfirmExecute against Idle stays Idle, execute can never be reached from idle`() {
        val result = reduceGcFlow(GcFlowState.Idle, GcFlowEvent.ConfirmExecute)
        assertEquals(GcFlowState.Idle, result)
    }

    @Test
    fun `ConfirmExecute is rejected from every state except ConfirmExecute itself`() {
        val nonConfirmStates = listOf(
            GcFlowState.Idle,
            GcFlowState.RequestingDryRun,
            GcFlowState.PollingDryRun(1),
            GcFlowState.DryRunPlan(1, job(1, "done"), null),
            GcFlowState.RequestingExecute,
            GcFlowState.PollingExecute(1),
            GcFlowState.ExecuteDone(1, job(1, "done"), null),
            GcFlowState.Cancelled(1),
            GcFlowState.Error("boom", executeAttempted = false),
        )
        for (state in nonConfirmStates) {
            assertEquals("ConfirmExecute must be a no-op from $state", state, reduceGcFlow(state, GcFlowEvent.ConfirmExecute))
        }
    }

    @Test
    fun `S2 -- RequestExecute is rejected from every state except DryRunPlan (full parametrised pin)`() {
        // Mirrors the ConfirmExecute-no-op test above's thoroughness
        // (review fix S2). The reachable regression this specifically
        // guards: from ExecuteDone (a GC that already ran to completion), a
        // second "Execute" tap must NOT re-confirm the OLD plan and queue a
        // second execute without a fresh dry run -- it must be a no-op,
        // exactly like every other non-DryRunPlan state.
        val nonDryRunPlanStates = listOf(
            GcFlowState.Idle,
            GcFlowState.RequestingDryRun,
            GcFlowState.PollingDryRun(1),
            GcFlowState.ConfirmExecute(1, job(1, "done"), null),
            GcFlowState.RequestingExecute,
            GcFlowState.PollingExecute(1),
            GcFlowState.ExecuteDone(1, job(1, "done"), null),
            GcFlowState.Cancelled(1),
            GcFlowState.Error("boom", executeAttempted = false),
        )
        for (state in nonDryRunPlanStates) {
            assertEquals("RequestExecute must be a no-op from $state", state, reduceGcFlow(state, GcFlowEvent.RequestExecute))
        }
    }

    // ---- the full valid path --------------------------------------------------

    @Test
    fun `full dry-run then execute path`() {
        var state: GcFlowState = GcFlowState.Idle

        state = reduceGcFlow(state, GcFlowEvent.StartDryRun)
        assertEquals(GcFlowState.RequestingDryRun, state)

        state = reduceGcFlow(state, GcFlowEvent.DryRunQueued(7))
        assertEquals(GcFlowState.PollingDryRun(7), state)

        state = reduceGcFlow(state, GcFlowEvent.PollResult(job(7, "queued")))
        assertEquals(GcFlowState.PollingDryRun(7), state) // still in flight

        state = reduceGcFlow(state, GcFlowEvent.PollResult(job(7, "running")))
        assertEquals(GcFlowState.PollingDryRun(7), state)

        val doneJob = job(7, "done", log = "[vault-api] GC totals (DRY RUN): would_delete=2 (700 bytes)")
        state = reduceGcFlow(state, GcFlowEvent.PollResult(doneJob))
        assertTrue(state is GcFlowState.DryRunPlan)
        state as GcFlowState.DryRunPlan
        assertEquals(7, state.jobId)
        assertEquals(2, state.summary?.wouldDeleteCount)

        state = reduceGcFlow(state, GcFlowEvent.RequestExecute)
        assertTrue(state is GcFlowState.ConfirmExecute)

        // A dismiss returns to the plan without ever touching the network.
        val dismissed = reduceGcFlow(state, GcFlowEvent.DismissConfirm)
        assertTrue(dismissed is GcFlowState.DryRunPlan)

        // The actual confirm.
        state = reduceGcFlow(state, GcFlowEvent.ConfirmExecute)
        assertEquals(GcFlowState.RequestingExecute, state)

        state = reduceGcFlow(state, GcFlowEvent.ExecuteQueued(8))
        assertEquals(GcFlowState.PollingExecute(8), state)

        val executedJob = job(8, "done", log = "[vault-api] GC totals (EXECUTED): chunks_removed=2 bytes_freed=700 total_bytes_freed=700")
        state = reduceGcFlow(state, GcFlowEvent.PollResult(executedJob))
        assertTrue(state is GcFlowState.ExecuteDone)
        state as GcFlowState.ExecuteDone
        assertEquals(8, state.jobId)
        assertEquals(2, state.summary?.chunksRemoved)
    }

    // ---- error paths ------------------------------------------------------------

    @Test
    fun `a dry-run request that fails to even queue reports executeAttempted = false`() {
        var state: GcFlowState = GcFlowState.RequestingDryRun
        state = reduceGcFlow(state, GcFlowEvent.DryRunFailed("network error"))
        assertEquals(GcFlowState.Error("network error", executeAttempted = false), state)
    }

    @Test
    fun `an execute request that fails to even queue reports executeAttempted = true`() {
        var state: GcFlowState = GcFlowState.RequestingExecute
        state = reduceGcFlow(state, GcFlowEvent.ExecuteFailed("network error"))
        assertEquals(GcFlowState.Error("network error", executeAttempted = true), state)
    }

    @Test
    fun `a job that reaches error status while polling the dry run becomes Error(executeAttempted=false)`() {
        val state = reduceGcFlow(GcFlowState.PollingDryRun(1), GcFlowEvent.PollResult(job(1, "error")))
        assertEquals(GcFlowState.Error("GC job 1 failed", executeAttempted = false), state)
    }

    @Test
    fun `a job that reaches error status while polling the execute becomes Error(executeAttempted=true)`() {
        val state = reduceGcFlow(GcFlowState.PollingExecute(1), GcFlowEvent.PollResult(job(1, "error")))
        assertEquals(GcFlowState.Error("GC job 1 failed", executeAttempted = true), state)
    }

    // ---- cancellation mid-poll ------------------------------------------------------

    @Test
    fun `a job cancelled by another client mid dry-run poll becomes Cancelled, not Error`() {
        val state = reduceGcFlow(GcFlowState.PollingDryRun(1), GcFlowEvent.PollResult(job(1, "cancelled")))
        assertEquals(GcFlowState.Cancelled(1), state)
    }

    @Test
    fun `a job cancelled by another client mid execute poll becomes Cancelled, not Error`() {
        val state = reduceGcFlow(GcFlowState.PollingExecute(1), GcFlowEvent.PollResult(job(1, "cancelled")))
        assertEquals(GcFlowState.Cancelled(1), state)
    }

    @Test
    fun `after Cancelled, StartDryRun works again -- Cancelled is a terminal-but-restartable state`() {
        val state = reduceGcFlow(GcFlowState.Cancelled(1), GcFlowEvent.StartDryRun)
        assertEquals(GcFlowState.RequestingDryRun, state)
    }

    // ---- stray/stale poll results never corrupt state --------------------------------

    @Test
    fun `a poll result for a DIFFERENT job id than the one being tracked is ignored -- DRY RUN`() {
        val state = GcFlowState.PollingDryRun(1)
        val result = reduceGcFlow(state, GcFlowEvent.PollResult(job(999, "done")))
        assertEquals(state, result)
    }

    @Test
    fun `S1 -- a poll result for a DIFFERENT job id than the one being tracked is ignored -- EXECUTE`() {
        // Mirrors the dry-run twin above (review fix S1: this binding was
        // unpinned for PollingExecute -- removing the `job.id != state.jobId`
        // check in reducePoll's PollingExecute branch survived the full
        // suite unnoticed). Without it, a stale poll for some OTHER job
        // (e.g. a superseded execute run, or a completely unrelated GC job)
        // landing while this flow is polling ITS OWN execute job would be
        // applied as if it were the real outcome.
        val state = GcFlowState.PollingExecute(7)
        val result = reduceGcFlow(state, GcFlowEvent.PollResult(job(999, "done")))
        assertEquals(state, result)
    }

    @Test
    fun `a poll result landing outside any Polling state is ignored`() {
        assertEquals(GcFlowState.Idle, reduceGcFlow(GcFlowState.Idle, GcFlowEvent.PollResult(job(1, "done"))))
    }

    @Test
    fun `an unrecognized job status keeps polling rather than guessing`() {
        assertEquals(GcFlowState.PollingDryRun(1), reduceGcFlow(GcFlowState.PollingDryRun(1), GcFlowEvent.PollResult(job(1, "frobnicated"))))
        assertEquals(GcFlowState.PollingExecute(1), reduceGcFlow(GcFlowState.PollingExecute(1), GcFlowEvent.PollResult(job(1, "queued"))))
    }

    @Test
    fun `a paused gc job keeps polling with the paused flag, and a resume clears it`() {
        // WP ANDROID-PAUSE-GC-1: "Pause all" parks a queued GC job; the sheet's
        // own request can dedupe into it.
        var state: GcFlowState = GcFlowState.PollingDryRun(1)
        state = reduceGcFlow(state, GcFlowEvent.PollResult(job(1, "paused")))
        assertEquals(GcFlowState.PollingDryRun(1, paused = true), state)
        state = reduceGcFlow(state, GcFlowEvent.PollResult(job(1, "running")))
        assertEquals(GcFlowState.PollingDryRun(1, paused = false), state)
        assertEquals(
            GcFlowState.PollingExecute(2, paused = true),
            reduceGcFlow(GcFlowState.PollingExecute(2), GcFlowEvent.PollResult(job(2, "paused"))),
        )
        // A paused job finishing later still reaches the plan.
        val plan = reduceGcFlow(GcFlowState.PollingDryRun(1, paused = true), GcFlowEvent.PollResult(job(1, "done")))
        assertTrue(plan is GcFlowState.DryRunPlan)
    }

    // ---- Reset ------------------------------------------------------------------------

    @Test
    fun `Reset always returns to Idle from any state`() {
        assertEquals(GcFlowState.Idle, reduceGcFlow(GcFlowState.PollingExecute(1), GcFlowEvent.Reset))
        assertEquals(GcFlowState.Idle, reduceGcFlow(GcFlowState.Error("x", true), GcFlowEvent.Reset))
        assertEquals(GcFlowState.Idle, reduceGcFlow(GcFlowState.Idle, GcFlowEvent.Reset))
    }

    // ---- StartDryRun gating -----------------------------------------------------------

    @Test
    fun `StartDryRun is rejected while a flow is already mid-run`() {
        val midFlight = listOf(
            GcFlowState.RequestingDryRun,
            GcFlowState.PollingDryRun(1),
            GcFlowState.DryRunPlan(1, job(1, "done"), null),
            GcFlowState.ConfirmExecute(1, job(1, "done"), null),
            GcFlowState.RequestingExecute,
            GcFlowState.PollingExecute(1),
        )
        for (state in midFlight) {
            assertEquals("StartDryRun must be a no-op from $state", state, reduceGcFlow(state, GcFlowEvent.StartDryRun))
        }
    }

    @Test
    fun `StartDryRun is accepted from Idle, ExecuteDone and Error too`() {
        assertEquals(GcFlowState.RequestingDryRun, reduceGcFlow(GcFlowState.Idle, GcFlowEvent.StartDryRun))
        assertEquals(
            GcFlowState.RequestingDryRun,
            reduceGcFlow(GcFlowState.ExecuteDone(1, job(1, "done"), null), GcFlowEvent.StartDryRun),
        )
        assertEquals(
            GcFlowState.RequestingDryRun,
            reduceGcFlow(GcFlowState.Error("x", false), GcFlowEvent.StartDryRun),
        )
    }
}
