package dev.steamvault.app.ui.detail.logic

import dev.steamvault.app.net.model.JobDetail

/**
 * The garbage-collection flow's state machine (WP 4b.6 brief: "GC flow
 * state machine (dry-run -> plan-shown -> executing -> done/error, incl.
 * cancellation mid-poll)"). A pure reducer over explicit events -- no
 * Compose, no coroutines, no network. `DetailController` is the only
 * caller, wrapping each transition around the real
 * `POST /v1/cache/{appid}/gc` / `GET /v1/jobs/{id}` calls.
 *
 * **The one guarantee this whole module exists to prove (WP brief): GC
 * EXECUTE is never sent without an explicit user confirm after a dry
 * run.** [reduceGcFlow] only ever produces [GcFlowState.RequestingExecute]
 * (the state `DetailController` gates its `POST .../gc {"execute":true}`
 * call on) in response to [GcFlowEvent.ConfirmExecute] fired FROM
 * [GcFlowState.ConfirmExecute] -- and [GcFlowState.ConfirmExecute] itself
 * is only reachable via [GcFlowEvent.RequestExecute] fired from
 * [GcFlowState.DryRunPlan], which is only reachable after a REAL dry-run
 * job (`gc_execute=false`, api/README.md "dry run is the default, in three
 * independent places") polled all the way to `done`. Every `when` branch
 * below that does not recognize its (state, event) pair returns the state
 * UNCHANGED -- there is no fallback branch that advances anything, so a
 * stray or out-of-order event (a double-tap, two button presses racing) can
 * only ever be a no-op, never a shortcut past the confirm gate.
 * `GcFlowStateMachineTest` pins the specific case the brief calls out by
 * name: firing [GcFlowEvent.ConfirmExecute] against [GcFlowState.Idle]
 * stays [GcFlowState.Idle] (and every other non-[GcFlowState.ConfirmExecute]
 * state does too).
 *
 * **Cancellation mid-poll.** A GC job can be cancelled by a DIFFERENT
 * client (`DELETE /v1/jobs/{id}` is not gated to any one caller,
 * api/README.md's job-control table) while this flow is polling either
 * run -- [reducePoll] maps a `cancelled` job status to [GcFlowState.Cancelled]
 * from BOTH [GcFlowState.PollingDryRun] and [GcFlowState.PollingExecute],
 * an honest terminal state distinct from [GcFlowState.Error] (nothing went
 * wrong; an operator action ended the job, same "cancelled is not a
 * failure" posture `ui/downloads/logic/JobPartition.kt` documents for the
 * prefill case).
 *
 * **Paused (WP ANDROID-PAUSE-GC-1, web `gc-flow.js` `paused` flag).** Since
 * web PAUSE-ALL-GC-1 "Pause all" parks a QUEUED GC job, and this sheet's
 * own request dedupes into a paused job of the same mode, so a polled GC
 * job can be `paused`. The flow keeps polling (a resume carries on to the
 * plan/result) but [GcFlowState.PollingDryRun.paused] /
 * [GcFlowState.PollingExecute.paused] tell the sheet to say why nothing
 * moves (`detail_gc_paused`) instead of a bare spinner.
 */
sealed class GcFlowState {
    data object Idle : GcFlowState()
    data object RequestingDryRun : GcFlowState()
    data class PollingDryRun(val jobId: Int, val paused: Boolean = false) : GcFlowState()
    data class DryRunPlan(val jobId: Int, val job: JobDetail, val summary: GcLogSummary?) : GcFlowState()
    data class ConfirmExecute(val jobId: Int, val job: JobDetail, val summary: GcLogSummary?) : GcFlowState()
    data object RequestingExecute : GcFlowState()
    data class PollingExecute(val jobId: Int, val paused: Boolean = false) : GcFlowState()
    data class ExecuteDone(val jobId: Int, val job: JobDetail, val summary: GcLogSummary?) : GcFlowState()
    data class Cancelled(val jobId: Int) : GcFlowState()
    data class Error(val message: String, val executeAttempted: Boolean) : GcFlowState()
}

sealed class GcFlowEvent {
    /** User tapped "Check for orphaned chunks" (or equivalent) from a
     * terminal/idle state. */
    data object StartDryRun : GcFlowEvent()
    data class DryRunQueued(val jobId: Int) : GcFlowEvent()
    data class DryRunFailed(val message: String) : GcFlowEvent()
    /** One `GET /v1/jobs/{id}` poll tick, for whichever job is currently
     * being polled. */
    data class PollResult(val job: JobDetail) : GcFlowEvent()
    /** User tapped "Execute" after seeing the dry-run plan -- opens the
     * second confirm, does NOT call the API yet. */
    data object RequestExecute : GcFlowEvent()
    /** User backed out of the execute confirm, back to the plan. */
    data object DismissConfirm : GcFlowEvent()
    /** User tapped "Yes, delete" in the execute confirm -- the ONLY event
     * that may ever lead to [GcFlowState.RequestingExecute]. */
    data object ConfirmExecute : GcFlowEvent()
    data class ExecuteQueued(val jobId: Int) : GcFlowEvent()
    data class ExecuteFailed(val message: String) : GcFlowEvent()
    /** Sheet closed / user starts over -- unconditional, from any state. */
    data object Reset : GcFlowEvent()
}

fun reduceGcFlow(state: GcFlowState, event: GcFlowEvent): GcFlowState = when (event) {
    is GcFlowEvent.StartDryRun ->
        if (state is GcFlowState.Idle ||
            state is GcFlowState.ExecuteDone ||
            state is GcFlowState.Error ||
            state is GcFlowState.Cancelled
        ) {
            GcFlowState.RequestingDryRun
        } else {
            state
        }

    is GcFlowEvent.DryRunQueued ->
        if (state is GcFlowState.RequestingDryRun) GcFlowState.PollingDryRun(event.jobId) else state

    is GcFlowEvent.DryRunFailed ->
        if (state is GcFlowState.RequestingDryRun) {
            GcFlowState.Error(event.message, executeAttempted = false)
        } else {
            state
        }

    is GcFlowEvent.PollResult -> reducePoll(state, event.job)

    is GcFlowEvent.RequestExecute ->
        if (state is GcFlowState.DryRunPlan) {
            GcFlowState.ConfirmExecute(state.jobId, state.job, state.summary)
        } else {
            state
        }

    is GcFlowEvent.DismissConfirm ->
        if (state is GcFlowState.ConfirmExecute) {
            GcFlowState.DryRunPlan(state.jobId, state.job, state.summary)
        } else {
            state
        }

    // THE pin (see class kdoc): only ever fires from ConfirmExecute.
    is GcFlowEvent.ConfirmExecute ->
        if (state is GcFlowState.ConfirmExecute) GcFlowState.RequestingExecute else state

    is GcFlowEvent.ExecuteQueued ->
        if (state is GcFlowState.RequestingExecute) GcFlowState.PollingExecute(event.jobId) else state

    is GcFlowEvent.ExecuteFailed ->
        if (state is GcFlowState.RequestingExecute) {
            GcFlowState.Error(event.message, executeAttempted = true)
        } else {
            state
        }

    is GcFlowEvent.Reset -> GcFlowState.Idle
}

private fun reducePoll(state: GcFlowState, job: JobDetail): GcFlowState = when (state) {
    is GcFlowState.PollingDryRun ->
        if (job.id != state.jobId) {
            state // a stale poll result for a different job -- ignored.
        } else {
            pollOutcome(job, executeAttempted = false) {
                GcFlowState.DryRunPlan(job.id, job, parseGcLogSummary(job.log_excerpt))
            }
        }

    is GcFlowState.PollingExecute ->
        if (job.id != state.jobId) {
            state
        } else {
            pollOutcome(job, executeAttempted = true) {
                GcFlowState.ExecuteDone(job.id, job, parseGcLogSummary(job.log_excerpt))
            }
        }

    // A poll tick landing outside a Polling* state (already terminal, or
    // superseded by a Reset) is stale -- never applied.
    else -> state
}

private inline fun pollOutcome(job: JobDetail, executeAttempted: Boolean, onDone: () -> GcFlowState): GcFlowState =
    when (job.status) {
        "done" -> onDone()
        "error" -> GcFlowState.Error("GC job ${job.id} failed", executeAttempted)
        "cancelled" -> GcFlowState.Cancelled(job.id)
        // "queued"/"running"/"paused"/anything else (incl. an unrecognized
        // future status): keep polling rather than guessing -- mirrors
        // `ui/downloads/logic/JobPartition.kt`'s "never fabricate a
        // plausible-looking word" posture for the same class of unknown.
        // `paused` additionally sets the flag so the sheet can say so.
        else -> {
            val paused = job.status == "paused"
            if (executeAttempted) GcFlowState.PollingExecute(job.id, paused) else GcFlowState.PollingDryRun(job.id, paused)
        }
    }
