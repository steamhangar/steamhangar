/**
 * The garbage-collection flow's state machine (WP 4a.4).
 *
 * Ported from the Android sibling's `ui/detail/logic/GcFlow.kt` (WP 4b.6) —
 * same states, same events, same transition table — onto plain tagged
 * objects (`{kind: "...", ...}`) instead of Kotlin sealed classes. A pure
 * reducer over explicit events: no DOM, no timers, no network.
 * `components/game-detail-sheet.js` is the only caller, wrapping each
 * transition around the real `POST /v1/cache/{appid}/gc` /
 * `GET /v1/jobs/{id}` calls.
 *
 * **The one guarantee this whole module exists to prove (WP brief): GC
 * EXECUTE is never sent without an explicit user confirm after a dry
 * run.** `reduceGcFlow` only ever produces `{kind: REQUESTING_EXECUTE}` (the
 * state the caller gates its `POST .../gc {"execute":true}` call on) in
 * response to a `confirm_execute` event fired FROM `{kind: CONFIRM_EXECUTE}`
 * — and `CONFIRM_EXECUTE` itself is only reachable via a `request_execute`
 * event fired from `{kind: DRY_RUN_PLAN}`, which is only reachable after a
 * REAL dry-run job (`execute: false`, api/README.md "dry run is the default,
 * in three independent places") polled all the way to `done`. Every branch
 * below that does not recognize its (state, event) pair returns the state
 * UNCHANGED — there is no fallback branch that advances anything, so a
 * stray or out-of-order event (a double-tap, two button presses racing) can
 * only ever be a no-op, never a shortcut past the confirm gate. Mirrors
 * `GcFlowTest.kt`'s "THE PIN" case: firing `confirm_execute` against
 * `{kind: IDLE}` stays `{kind: IDLE}` (and every other non-CONFIRM_EXECUTE
 * state does too — see web/tests/gc-flow.test.js).
 *
 * **Cancellation mid-poll.** A GC job can be cancelled by a DIFFERENT
 * client (`DELETE /v1/jobs/{id}` is not gated to any one caller,
 * api/README.md's job-control table) while this flow is polling either
 * run — `reducePoll` maps a `cancelled` job status to `{kind: CANCELLED}`
 * from BOTH `POLLING_DRY_RUN` and `POLLING_EXECUTE`, an honest terminal
 * state distinct from `ERROR` (nothing went wrong; an operator action ended
 * the job — same "cancelled is not a failure" posture
 * `lib/job-partition.js` documents for the prefill case).
 *
 * Pure — no DOM, no fetch. Covered in web/tests/gc-flow.test.js.
 */

import { parseGcLogSummary } from "./gc-log-summary.js";

export const GC_STATE = Object.freeze({
  IDLE: "idle",
  REQUESTING_DRY_RUN: "requesting_dry_run",
  POLLING_DRY_RUN: "polling_dry_run",
  DRY_RUN_PLAN: "dry_run_plan",
  CONFIRM_EXECUTE: "confirm_execute",
  REQUESTING_EXECUTE: "requesting_execute",
  POLLING_EXECUTE: "polling_execute",
  EXECUTE_DONE: "execute_done",
  CANCELLED: "cancelled",
  ERROR: "error",
});

export const GC_EVENT = Object.freeze({
  // User tapped "Check for orphaned chunks" (or equivalent) from a
  // terminal/idle state.
  START_DRY_RUN: "start_dry_run",
  DRY_RUN_QUEUED: "dry_run_queued",
  DRY_RUN_FAILED: "dry_run_failed",
  // One `GET /v1/jobs/{id}` poll tick, for whichever job is currently
  // being polled.
  POLL_RESULT: "poll_result",
  // User tapped "Execute" after seeing the dry-run plan — opens the second
  // confirm, does NOT call the API yet.
  REQUEST_EXECUTE: "request_execute",
  // User backed out of the execute confirm, back to the plan.
  DISMISS_CONFIRM: "dismiss_confirm",
  // User tapped "Yes, delete" in the execute confirm — the ONLY event that
  // may ever lead to REQUESTING_EXECUTE.
  CONFIRM_EXECUTE: "confirm_execute",
  EXECUTE_QUEUED: "execute_queued",
  EXECUTE_FAILED: "execute_failed",
  // Sheet closed / user starts over — unconditional, from any state.
  RESET: "reset",
});

/** @returns {{kind: "idle"}} */
export function idleGcState() {
  return { kind: GC_STATE.IDLE };
}

const STARTABLE_FROM = new Set([GC_STATE.IDLE, GC_STATE.EXECUTE_DONE, GC_STATE.ERROR, GC_STATE.CANCELLED]);

/**
 * @param {object} state current GC_STATE-tagged object
 * @param {object} event current GC_EVENT-tagged object (`{type, ...}`)
 * @returns {object} the next state (same reference as `state` if the event
 *   is a no-op from this state)
 */
export function reduceGcFlow(state, event) {
  switch (event.type) {
    case GC_EVENT.START_DRY_RUN:
      return STARTABLE_FROM.has(state.kind) ? { kind: GC_STATE.REQUESTING_DRY_RUN } : state;

    case GC_EVENT.DRY_RUN_QUEUED:
      return state.kind === GC_STATE.REQUESTING_DRY_RUN
        ? { kind: GC_STATE.POLLING_DRY_RUN, jobId: event.jobId }
        : state;

    case GC_EVENT.DRY_RUN_FAILED:
      return state.kind === GC_STATE.REQUESTING_DRY_RUN
        ? { kind: GC_STATE.ERROR, message: event.message, executeAttempted: false }
        : state;

    case GC_EVENT.POLL_RESULT:
      return reducePoll(state, event.job);

    case GC_EVENT.REQUEST_EXECUTE:
      return state.kind === GC_STATE.DRY_RUN_PLAN
        ? { kind: GC_STATE.CONFIRM_EXECUTE, jobId: state.jobId, job: state.job, summary: state.summary }
        : state;

    case GC_EVENT.DISMISS_CONFIRM:
      return state.kind === GC_STATE.CONFIRM_EXECUTE
        ? { kind: GC_STATE.DRY_RUN_PLAN, jobId: state.jobId, job: state.job, summary: state.summary }
        : state;

    // THE pin (see module kdoc): only ever fires from CONFIRM_EXECUTE.
    case GC_EVENT.CONFIRM_EXECUTE:
      return state.kind === GC_STATE.CONFIRM_EXECUTE ? { kind: GC_STATE.REQUESTING_EXECUTE } : state;

    case GC_EVENT.EXECUTE_QUEUED:
      return state.kind === GC_STATE.REQUESTING_EXECUTE
        ? { kind: GC_STATE.POLLING_EXECUTE, jobId: event.jobId }
        : state;

    case GC_EVENT.EXECUTE_FAILED:
      return state.kind === GC_STATE.REQUESTING_EXECUTE
        ? { kind: GC_STATE.ERROR, message: event.message, executeAttempted: true }
        : state;

    case GC_EVENT.RESET:
      return { kind: GC_STATE.IDLE };

    default:
      return state;
  }
}

function reducePoll(state, job) {
  if (state.kind === GC_STATE.POLLING_DRY_RUN) {
    if (job.id !== state.jobId) return state; // a stale poll result for a different job — ignored.
    return pollOutcome(job, false, () => ({
      kind: GC_STATE.DRY_RUN_PLAN,
      jobId: job.id,
      job,
      summary: parseGcLogSummary(job.log_excerpt),
    }));
  }
  if (state.kind === GC_STATE.POLLING_EXECUTE) {
    if (job.id !== state.jobId) return state;
    return pollOutcome(job, true, () => ({
      kind: GC_STATE.EXECUTE_DONE,
      jobId: job.id,
      job,
      summary: parseGcLogSummary(job.log_excerpt),
    }));
  }
  // A poll tick landing outside a Polling* state (already terminal, or
  // superseded by a Reset) is stale — never applied.
  return state;
}

/** Shown while the polled GC job is `paused` (WP PAUSE-ALL-GC-1). Since
 * then "Pause all" parks a QUEUED GC job, and the sheet's own request
 * dedupes into a paused job of the same mode, so `paused` is reachable
 * here. The flow keeps polling (a resume carries on to the plan/result)
 * but says why nothing moves instead of a bare "Checking…". */
export const GC_PAUSED_TEXT = "GC paused — resume it on the Downloads page.";

function pollOutcome(job, executeAttempted, onDone) {
  if (job.status === "done") return onDone();
  if (job.status === "error") {
    return { kind: GC_STATE.ERROR, message: `GC job ${job.id} failed`, executeAttempted };
  }
  if (job.status === "cancelled") return { kind: GC_STATE.CANCELLED, jobId: job.id };
  // "queued"/"running"/"paused"/anything else (incl. an unrecognized future
  // status): keep polling rather than guessing — mirrors
  // lib/job-partition.js's "never fabricate a plausible-looking word"
  // posture for the same class of unknown. `paused` additionally carries
  // `paused: true` so the sheet can say so (GC_PAUSED_TEXT); the key is
  // absent otherwise, so the plain polling state stays `{kind, jobId}`.
  const polling = executeAttempted
    ? { kind: GC_STATE.POLLING_EXECUTE, jobId: job.id }
    : { kind: GC_STATE.POLLING_DRY_RUN, jobId: job.id };
  return job.status === "paused" ? { ...polling, paused: true } : polling;
}
