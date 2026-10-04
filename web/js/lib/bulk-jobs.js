/**
 * "Pause all" / "Resume all" for the Downloads view (WP WEB-FEAT-5) —
 * pure decisions and wording, no DOM. `views/downloads.js` wires it;
 * `app/.../ui/downloads/logic/BulkJobs.kt` is the Android twin and pins
 * every wording literal below against this file (BulkJobsWebTwinTest).
 *
 * **What "all" covers, and why in this order.** vault-api runs one job at a
 * time, and pausing the running job releases the worker slot
 * (api/README.md "The worker slot"), so the worker claims the next queued
 * job at once. Since WP WEB-FEAT-5 (ADR-0016 addendum) `POST
 * /v1/jobs/{id}/pause` also parks a QUEUED prefill job immediately. Pause
 * all therefore pauses every queued prefill job FIRST and only then the
 * running one(s): by the time the running job lets go of the slot there is
 * nothing left for the worker to claim.
 *
 * - Prefill jobs only. GC jobs are refused by the API (`409`, queued or
 *   running) and are left alone; the confirm dialog says so when one is
 *   active.
 * - A running job that already has a `stop_request` (pausing or cancelling)
 *   is not sent again.
 * - Resume all resumes every `paused` job. Resume keeps each job's id, and
 *   the queue is FIFO by id, so they run in their original order.
 *
 * **Results.** A `404` (job gone) or `409` (it finished, or changed state
 * since the list was fetched) is counted as SKIPPED, not as a failure: the
 * job no longer needs this action. Anything else (network, 5xx, auth) is a
 * failure and its reason reaches the toast. Pausing does not stop the
 * scheduler: its next sweep can still queue new downloads
 * (`WORDING.schedulerNote`, shown next to the buttons).
 */

/** Requests in flight at once, per phase. */
export const BULK_CONCURRENCY = 4;

/**
 * Every user-visible literal of the feature. `{n}`, `{ok}`, `{total}`,
 * `{reason}` are filled by `fill()`. The Android twin carries the same
 * strings — keep each one a single double-quoted literal (the twin test
 * matches them that way).
 */
export const WORDING = Object.freeze({
  pauseAll: "Pause all",
  resumeAll: "Resume all",
  pausing: "Pausing…",
  resuming: "Resuming…",
  pauseAriaOne: "Pause 1 download",
  pauseAriaMany: "Pause all {n} downloads",
  resumeAriaOne: "Resume 1 download",
  resumeAriaMany: "Resume all {n} downloads",
  groupLabel: "All downloads",
  schedulerNote: "Pausing does not stop the scheduler: its next sweep can still queue new downloads.",
  confirmTitleOne: "Pause 1 download?",
  confirmTitleMany: "Pause {n} downloads?",
  confirmBody:
    "Queued downloads are paused first, then the running one, so nothing new starts in between. Bytes already fetched stay on the cache. Resume all puts them back in the queue in their original order.",
  confirmGcNote: "Garbage-collection runs keep going: they cannot be paused.",
  confirmYes: "Pause all",
  confirmNo: "Keep downloading",
  pausedAllOne: "Paused 1 download",
  pausedAllMany: "Paused {n} downloads",
  resumedAllOne: "Resumed 1 download",
  resumedAllMany: "Resumed {n} downloads",
  pausedPartial: "Paused {ok} of {total}",
  resumedPartial: "Resumed {ok} of {total}",
  pauseFailed: "{n} could not be paused: {reason}",
  resumeFailed: "{n} could not be resumed: {reason}",
  skipped: "{n} skipped (finished or changed meanwhile)",
  fallbackReason: "Request failed",
});

/** Replace `{key}` placeholders with `values[key]`. */
export function fill(template, values) {
  return template.replace(/\{(\w+)\}/g, (whole, key) => (key in values ? String(values[key]) : whole));
}

/** `one` for 1, else `many` with `{n}` filled. */
function countWord(n, one, many) {
  return n === 1 ? one : fill(many, { n });
}

const isPrefill = (j) => j && j.type === "prefill";

/**
 * Jobs Pause all acts on, split into the two phases.
 * @param {object[]} jobs `GET /v1/jobs` rows
 * @returns {{queued: object[], running: object[], count: number}}
 */
export function bulkPauseTargets(jobs) {
  const list = Array.isArray(jobs) ? jobs : [];
  const byId = (a, b) => a.id - b.id;
  const queued = list.filter((j) => isPrefill(j) && j.status === "queued").sort(byId);
  const running = list
    .filter((j) => isPrefill(j) && j.status === "running" && !j.stop_request)
    .sort(byId);
  return { queued, running, count: queued.length + running.length };
}

/** Jobs Resume all acts on: every paused job, oldest id first. */
export function bulkResumeTargets(jobs) {
  const list = Array.isArray(jobs) ? jobs : [];
  return list.filter((j) => j && j.status === "paused").sort((a, b) => a.id - b.id);
}

/**
 * What the bulk bar shows.
 * @returns {{pauseVisible: boolean, pauseCount: number, resumeVisible: boolean,
 *   resumeCount: number, gcActive: boolean, visible: boolean}}
 */
export function bulkBarState(jobs) {
  const list = Array.isArray(jobs) ? jobs : [];
  const pauseCount = bulkPauseTargets(list).count;
  const resumeCount = bulkResumeTargets(list).length;
  const gcActive = list.some((j) => j && j.type === "gc" && (j.status === "queued" || j.status === "running"));
  return {
    pauseVisible: pauseCount > 0,
    pauseCount,
    resumeVisible: resumeCount > 0,
    resumeCount,
    gcActive,
    visible: pauseCount > 0 || resumeCount > 0,
  };
}

export const pauseAriaLabel = (n) => countWord(n, WORDING.pauseAriaOne, WORDING.pauseAriaMany);
export const resumeAriaLabel = (n) => countWord(n, WORDING.resumeAriaOne, WORDING.resumeAriaMany);
export const confirmTitle = (n) => countWord(n, WORDING.confirmTitleOne, WORDING.confirmTitleMany);

/** `404` / `409`: the job is gone or no longer in a state this action
 * applies to — skipped, not failed. */
export function isSkip(err) {
  return !!err && (err.status === 404 || err.status === 409);
}

function reasonOf(err) {
  if (err && typeof err.detail === "string" && err.detail) return err.detail;
  if (err && typeof err.message === "string" && err.message) return err.message;
  return WORDING.fallbackReason;
}

/**
 * Run `worker(item)` for every item, at most `limit` at a time. Never
 * rejects: each item settles to `{job, outcome: "ok"|"skipped"|"failed",
 * reason?}`, in input order.
 */
export async function runWithLimit(items, limit, worker) {
  const results = new Array(items.length);
  let next = 0;
  async function lane() {
    while (next < items.length) {
      const i = next++;
      const job = items[i];
      try {
        await worker(job);
        results[i] = { job, outcome: "ok" };
      } catch (err) {
        results[i] = isSkip(err)
          ? { job, outcome: "skipped", reason: reasonOf(err) }
          : { job, outcome: "failed", reason: reasonOf(err) };
      }
    }
  }
  const lanes = Math.max(1, Math.min(limit, items.length));
  await Promise.all(Array.from({ length: lanes }, lane));
  return results;
}

/** Pause all: queued phase to completion, THEN the running phase. */
export async function runBulkPause(targets, pauseJob, limit = BULK_CONCURRENCY) {
  const first = await runWithLimit(targets.queued, limit, (j) => pauseJob(j.id));
  const second = await runWithLimit(targets.running, limit, (j) => pauseJob(j.id));
  return first.concat(second);
}

/** Resume all. */
export async function runBulkResume(targets, resumeJob, limit = BULK_CONCURRENCY) {
  return runWithLimit(targets, limit, (j) => resumeJob(j.id));
}

/**
 * The one toast after a bulk run.
 * @param {"pause"|"resume"} kind
 * @param {{outcome: string, reason?: string}[]} results
 * @returns {{text: string, warn: boolean}}
 */
export function bulkSummary(kind, results) {
  const total = results.length;
  const ok = results.filter((r) => r.outcome === "ok").length;
  const failed = results.filter((r) => r.outcome === "failed");
  const skipped = results.filter((r) => r.outcome === "skipped").length;
  const pause = kind === "pause";

  const head =
    ok === total
      ? countWord(total, pause ? WORDING.pausedAllOne : WORDING.resumedAllOne, pause ? WORDING.pausedAllMany : WORDING.resumedAllMany)
      : fill(pause ? WORDING.pausedPartial : WORDING.resumedPartial, { ok, total });

  const parts = [];
  if (failed.length) {
    parts.push(
      fill(pause ? WORDING.pauseFailed : WORDING.resumeFailed, {
        n: failed.length,
        reason: failed[0].reason || WORDING.fallbackReason,
      }),
    );
  }
  if (skipped) parts.push(fill(WORDING.skipped, { n: skipped }));

  return { text: parts.length ? `${head} — ${parts.join("; ")}` : head, warn: failed.length > 0 };
}
