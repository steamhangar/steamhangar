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
 * - Queued jobs of both types, running jobs of type prefill only (WP
 *   PAUSE-ALL-GC-1). Since then the API parks a QUEUED GC job like a queued
 *   prefill; before, every GC job answered `409`, so with auto-GC on
 *   execute the GC jobs each prefill had queued ran at once right after
 *   "Pause all" (prod 2026-11-01). A RUNNING GC job still answers `409`
 *   (short, rebuilds its plan; cancel is the tool): it is not sent, and the
 *   confirm dialog says it finishes. The dialog also says how many queued
 *   GC runs are held, because the count in the title ("downloads")
 *   includes them.
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
  // Not shown by the web since WP PAUSE-ALL-GC-1 (a queued GC run can be
  // paused now; see confirmGcRunning / confirmGcHeld*). Kept verbatim because
  // the Android twin still shows it and BulkJobsWebTwinTest pins it here —
  // retire it together with the Android follow-up.
  confirmGcNote: "Garbage-collection runs keep going: they cannot be paused.",
  confirmGcRunning: "The garbage-collection run that already started finishes: a running one cannot be paused (cancel it instead).",
  confirmGcHeldOne: "This includes 1 queued garbage-collection run: it is held too and deletes nothing until you resume it.",
  confirmGcHeldMany: "This includes {n} queued garbage-collection runs: they are held too and delete nothing until you resume them.",
  // WP PAUSE-ALL-GC-1 (review N7): appended to the toast head when GC jobs
  // were among the successfully paused/resumed ones, so "downloads" does
  // not silently count them. The pinned head literals stay untouched.
  gcPartOne: "incl. 1 garbage-collection run",
  gcPartMany: "incl. {n} garbage-collection runs",
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
const isGc = (j) => j && j.type === "gc";

/**
 * Jobs Pause all acts on, split into the two phases. `queued` holds queued
 * prefill AND queued GC jobs (WP PAUSE-ALL-GC-1); `running` holds prefill
 * jobs only (a running GC job answers `409`). `gcQueued` counts the GC
 * jobs inside `queued`, for the confirm dialog.
 * @param {object[]} jobs `GET /v1/jobs` rows
 * @returns {{queued: object[], running: object[], count: number, gcQueued: number}}
 */
export function bulkPauseTargets(jobs) {
  const list = Array.isArray(jobs) ? jobs : [];
  const byId = (a, b) => a.id - b.id;
  const queued = list.filter((j) => (isPrefill(j) || isGc(j)) && j.status === "queued").sort(byId);
  const running = list
    .filter((j) => isPrefill(j) && j.status === "running" && !j.stop_request)
    .sort(byId);
  const gcQueued = queued.filter(isGc).length;
  return { queued, running, count: queued.length + running.length, gcQueued };
}

/** Jobs Resume all acts on: every paused job, oldest id first. */
export function bulkResumeTargets(jobs) {
  const list = Array.isArray(jobs) ? jobs : [];
  return list.filter((j) => j && j.status === "paused").sort((a, b) => a.id - b.id);
}

/**
 * What the bulk bar shows. `gcRunning`: a GC job is running and will not
 * be paused; `gcQueued`: queued GC jobs Pause all holds (WP
 * PAUSE-ALL-GC-1, replaces the old `gcActive`).
 * @returns {{pauseVisible: boolean, pauseCount: number, resumeVisible: boolean,
 *   resumeCount: number, gcRunning: boolean, gcQueued: number, visible: boolean}}
 */
export function bulkBarState(jobs) {
  const list = Array.isArray(jobs) ? jobs : [];
  const targets = bulkPauseTargets(list);
  const pauseCount = targets.count;
  const resumeCount = bulkResumeTargets(list).length;
  const gcRunning = list.some((j) => isGc(j) && j.status === "running");
  return {
    pauseVisible: pauseCount > 0,
    pauseCount,
    resumeVisible: resumeCount > 0,
    resumeCount,
    gcRunning,
    gcQueued: targets.gcQueued,
    visible: pauseCount > 0 || resumeCount > 0,
  };
}

/**
 * The confirm dialog's GC lines (WP PAUSE-ALL-GC-1), in display order;
 * empty when no GC job is queued or running.
 * @param {{gcRunning: boolean, gcQueued: number}} bar from {@link bulkBarState}
 * @returns {string[]}
 */
export function confirmGcLines(bar) {
  const lines = [];
  if (bar && bar.gcQueued > 0) lines.push(countWord(bar.gcQueued, WORDING.confirmGcHeldOne, WORDING.confirmGcHeldMany));
  if (bar && bar.gcRunning) lines.push(WORDING.confirmGcRunning);
  return lines;
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

  const baseHead =
    ok === total
      ? countWord(total, pause ? WORDING.pausedAllOne : WORDING.resumedAllOne, pause ? WORDING.pausedAllMany : WORDING.resumedAllMany)
      : fill(pause ? WORDING.pausedPartial : WORDING.resumedPartial, { ok, total });
  const gcOk = results.filter((r) => r.outcome === "ok" && isGc(r.job)).length;
  const head = gcOk ? `${baseHead} (${countWord(gcOk, WORDING.gcPartOne, WORDING.gcPartMany)})` : baseHead;

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
