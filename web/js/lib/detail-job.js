/**
 * Which job (if any) drives THIS app's detail-sheet job-control section, and
 * which of pause/resume/cancel apply to it (WP 4a.4 brief: "pause/resume/
 * cancel for the app's live job, downloads.js semantics/gating: pause
 * prefill-only").
 *
 * Ported from the Android sibling's `ui/detail/logic/DetailJob.kt` (WP 4b.6)
 * — deliberately BROADER than `lib/game-status.js`'s `findLiveJob`: that
 * helper excludes `queued` on purpose because the Library grid card mirrors
 * the mockup's pill (a queued job has no card-level affordance — it shows in
 * the Downloads FIFO queue instead). The detail sheet is a different
 * surface: it mirrors `lib/job-partition.js`'s job-control gating
 * (api/README.md "Job control" table), which DOES offer Cancel on a queued
 * job. A second, differently-scoped helper is therefore correct here, not a
 * duplicate of the grid's.
 *
 * GC jobs are excluded from `findTrackedJob` — pause/resume are meaningless
 * on a GC job (api/README.md job control table: pause on a GC job is
 * `409`), and this sheet's OWN GC action already owns any GC job IT starts,
 * via `lib/gc-flow.js`, independent of this job-control section.
 *
 * Pure — no DOM, no fetch. Covered in web/tests/detail-job.test.js.
 */

import { ACTIVE_JOB_STATUSES } from "./job-status.js";

/**
 * The prefill job (if any) queued/running/paused for `appid` right now.
 * @param {object[] | null | undefined} jobs `GET /v1/jobs` snapshot.
 * @param {number} appid
 * @returns {object | undefined}
 */
export function findTrackedJob(jobs, appid) {
  if (!Array.isArray(jobs)) return undefined;
  return jobs.find((j) => j.appid === appid && j.type === "prefill" && ACTIVE_JOB_STATUSES.has(j.status));
}

export const DETAIL_JOB_ACTION = Object.freeze({
  PAUSE: "pause",
  RESUME: "resume",
  CANCEL: "cancel",
});

/**
 * Mirrors api/README.md's "Job control" table exactly: `queued` -> cancel
 * only (pause/resume both `409` on a queued job); `running` -> pause +
 * cancel; `paused` -> resume + cancel. No tracked job (or any other status —
 * finished) -> no actions.
 * @param {object | null | undefined} job
 * @returns {Set<string>} of DETAIL_JOB_ACTION values.
 */
export function detailJobActions(job) {
  switch (job?.status) {
    case "queued":
      return new Set([DETAIL_JOB_ACTION.CANCEL]);
    case "running":
      return new Set([DETAIL_JOB_ACTION.PAUSE, DETAIL_JOB_ACTION.CANCEL]);
    case "paused":
      return new Set([DETAIL_JOB_ACTION.RESUME, DETAIL_JOB_ACTION.CANCEL]);
    default:
      return new Set();
  }
}
