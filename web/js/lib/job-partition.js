/**
 * Job partitioning + display wording for the Downloads view (WP 4a.5).
 *
 * Splits a `GET /v1/jobs` snapshot into the buckets the Downloads screen
 * renders, against the REAL status set from WP 3.12's job control
 * (api/README.md "Job control" / "The status model") rather than the
 * mockup's fake one. The mockup's statuses are
 * `queued|running|paused|verify|done|error`; the real API has no `verify`
 * — that was a mockup-only transient UI state simulating SteamPrefill's
 * "verifying cached chunks" phase — and adds a real, terminal `cancelled`
 * the mockup never modeled as a distinct outcome (job outcome honesty,
 * docs/PROJECT_PLAN.md: done/error/cancelled render distinctly; a
 * cancelled job is neither a failure nor a success).
 *
 * **The slot-release divergence (docs/WORKPACKAGES.md Phase 4a header,
 * api/README.md "The worker slot — a paused job does NOT hold it").** The
 * mockup assumes exactly one job ever occupies an "active" card slot
 * (running OR paused OR verifying), with the queue waiting behind it. The
 * real backend releases the worker slot on pause: `claim_next_job` claims
 * `queued` rows only, so a paused job's app can sit parked while a
 * completely different queued job is claimed and runs. `running` and
 * `paused` are therefore independent buckets here (normally 0-1 running,
 * 0-N paused), not the mockup's single mutually-exclusive slot — the whole
 * point of the divergence is that the UI can show "Active" (what is really
 * using the one worker slot right now) and "Paused" (parked, NOT blocking
 * anything) as genuinely separate sections instead of one that pretends a
 * paused job still occupies a slot it does not hold.
 *
 * **Unknown status routes to history, not oblivion (WP 4a.8 backport of the
 * WP 4b.5 divergence, docs/WORKPACKAGES.md's Phase 4a header).** A job whose
 * `status` is anything other than the six known values used to match none
 * of this module's filters and silently vanish from every bucket — an
 * operator-invisible job that still exists server-side. `partitionJobs` now
 * routes it into `history` with a NEUTRAL presentation instead:
 * `jobIconKind` already fell back to `"none"` for an unmapped status and
 * `jobStatusWord` already fell back to the raw status string rather than
 * fabricating a plausible-looking word, so nothing else needed to change to
 * make that an honest "something happened, here it is, we don't have a name
 * for it" row. It deliberately does NOT count toward `countPending`: an
 * unrecognized status is not confidently "still pending" either, so the nav
 * pip stays fail-quiet rather than fail-loud on every poll tick. Ported
 * from the Android sibling's `ui/downloads/logic/JobPartition.kt`
 * (`KNOWN_STATUSES`/history-filter shape) verbatim — ONE change from that
 * port: the Kotlin side stayed 4a.5-shaped ("earlier port had no name for
 * this"), this backport closes the same gap here.
 *
 * **Updating / Verifying on the Active card (WP WEB-FEAT-6).** A running
 * prefill job's badge follows the same derivation as the Library card
 * (`lib/game-status.js`'s `liveRunKind`, module header there): the job row
 * alone cannot say whether it refreshes a completed copy, so
 * {@link activeJobKind}/{@link activeJobWord} take the app's games row as
 * a second input. `verify` is still not a STATUS here — it is a display
 * kind of a `running` job, so the partition below is unchanged.
 *
 * Pure only — no DOM, no fetch. Covered in web/tests/job-partition.test.js
 * and web/tests/update-verify.test.js.
 */

import { KIND, liveRunKind } from "./game-status.js";

/** Statuses that count toward "something is pending" (the Downloads nav
 * pip, mockup's `syncPip`) — ported onto the real status set (no
 * `verify` status; WP WEB-FEAT-6's verify KIND is a running job). Mirrors `JOB_ACTIVE_STATUSES` in store.js/library.js, kept as
 * an independent local copy rather than a shared import: this module must
 * stay standalone (no view-layer coupling), and the two lists are pinned
 * to the same value by api/README.md's "ACTIVE_STATUSES" table, not by
 * each other. */
const PENDING_STATUSES = new Set(["queued", "running", "paused"]);
const HISTORY_STATUSES = new Set(["done", "error", "cancelled"]);
const KNOWN_STATUSES = new Set(["queued", "running", "paused", "done", "error", "cancelled"]);

/**
 * @param {object[] | null | undefined} jobs `GET /v1/jobs` snapshot.
 * @returns {{running: object[], paused: object[], queued: object[], history: object[]}}
 *   `queued` is sorted oldest-job-id-first (FIFO draw order,
 *   api/README.md "Queue semantics" — "exactly one job runs at a time...
 *   FIFO by job id"), independent of the snapshot's own order. `history`
 *   keeps the snapshot's order as-is (`GET /v1/jobs` is newest-first per
 *   api/README.md, so a job's most recent occurrence is already first) —
 *   an unrecognized status is included here too (module header, "Unknown
 *   status routes to history"), appended wherever the snapshot placed it.
 */
export function partitionJobs(jobs) {
  const list = Array.isArray(jobs) ? jobs : [];
  const running = list.filter((j) => j.status === "running");
  const paused = list.filter((j) => j.status === "paused");
  const queued = list.filter((j) => j.status === "queued").sort((a, b) => a.id - b.id);
  const history = list.filter((j) => HISTORY_STATUSES.has(j.status) || !KNOWN_STATUSES.has(j.status));
  return { running, paused, queued, history };
}

/** How many jobs the Downloads nav pip should count (mockup's `syncPip`,
 * ported onto the real status set). */
export function countPending(jobs) {
  const list = Array.isArray(jobs) ? jobs : [];
  return list.filter((j) => PENDING_STATUSES.has(j.status)).length;
}

/**
 * 1-based queue position of `jobId` within an already-FIFO-sorted `queued`
 * array (as returned by `partitionJobs`). `null` if not present.
 */
export function queuePosition(queued, jobId) {
  const list = Array.isArray(queued) ? queued : [];
  const idx = list.findIndex((j) => j.id === jobId);
  return idx === -1 ? null : idx + 1;
}

/** Which status-icon KIND (js/components/status-icon.js) a job's badge
 * uses. `queued` has no persistent badge in this view (mockup parity: the
 * queue row is grip + name + position, no icon) — `"none"` is returned for
 * completeness/testability, not for display. */
export function jobIconKind(job) {
  switch (job.status) {
    case "running":
      return "running";
    case "paused":
      return "paused";
    case "done":
      return "cached";
    case "error":
      return "error";
    case "cancelled":
      return "cancelled";
    default:
      return "none";
  }
}

// GC jobs are real `GET /v1/jobs` rows (type: "gc") the mockup's JOBS
// fixture never modeled (NOTES.md has no GC entries) — "Downloading" /
// "Update ready" wording is honest for a prefill job and misleading for a
// garbage-collection one, so GC gets its own words while sharing the same
// status-icon KIND per jobIconKind (no dedicated GC glyph exists yet;
// documented simplification, not an oversight — WP 4a.4 owns the GC
// trigger flow, this view only ever DISPLAYS a GC job it happens to see).
const PREFILL_WORD = Object.freeze({
  queued: "Queued",
  running: "Downloading",
  paused: "Paused",
  done: "Done",
  error: "Failed",
  cancelled: "Cancelled",
});
const GC_WORD = Object.freeze({
  queued: "Garbage collection queued",
  running: "Collecting garbage",
  paused: "Paused", // unreachable in practice (pause 409s on a GC job) — defensive only
  done: "Garbage collected",
  error: "Garbage collection failed",
  cancelled: "Garbage collection cancelled",
});

/**
 * The word a job's badge/history-row shows, single source of truth for
 * both (mockup's `LABEL` table split by job type). Falls back to the raw
 * status string for a status this table doesn't know, rather than
 * fabricating something plausible-looking.
 * @param {{type: string, status: string}} job
 */
export function jobStatusWord(job) {
  const table = job.type === "gc" ? GC_WORD : PREFILL_WORD;
  return table[job.status] || job.status;
}

/** The words for the two derived kinds (WP WEB-FEAT-6). Literal copies of
 * components/status-icon.js's STATUS_LABEL values — that module builds
 * DOM, this one stays pure; web/tests/update-verify.test.js pins them
 * equal. */
const RUN_KIND_WORD = Object.freeze({
  [KIND.UPDATING]: "Updating",
  [KIND.VERIFY]: "Verifying",
});

/**
 * The status-icon kind of a job's Downloads card (WP WEB-FEAT-6): a
 * RUNNING PREFILL job shows `liveRunKind(game)` (running, updating or
 * verify); every other job keeps {@link jobIconKind}.
 * @param {{type: string, status: string}} job
 * @param {object | null | undefined} game the app's `GET /v1/games` row
 */
export function activeJobKind(job, game) {
  if (job.type === "prefill" && job.status === "running") return liveRunKind(game);
  return jobIconKind(job);
}

/**
 * The word next to {@link activeJobKind}'s icon: "Updating"/"Verifying"
 * for the two derived kinds, otherwise {@link jobStatusWord}.
 * @param {{type: string, status: string}} job
 * @param {object | null | undefined} game
 */
export function activeJobWord(job, game) {
  return RUN_KIND_WORD[activeJobKind(job, game)] || jobStatusWord(job);
}
