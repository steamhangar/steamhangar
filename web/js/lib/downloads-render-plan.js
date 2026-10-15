/**
 * Downloads-view jobs-tick render decision (WP 4a.5).
 *
 * The round-7 mockup rule ("rebuild DOM only when a card's STATE
 * changes... while a job is merely progressing, patch the volatile values
 * in place... touch no animated node", docs/design/vault-app-mockup-NOTES.md)
 * is binding for the Downloads view too (already applied to the games poll
 * in render-plan.js) — but the shape of "merely progressing" is narrower
 * here than either of those, because the real `JobSummary`/`JobDetail`
 * (api/README.md) carries no byte-level progress field at all
 * (game-status.js's module header, "Divergence 2", documents the same gap
 * for the library card). A `GET /v1/jobs` row genuinely does not change AT
 * ALL between polls while a job simply keeps running with nothing
 * user-initiated happening — `diff-utils.js`'s `diffByKey` does a real
 * value compare (`JSON.stringify`), so such a job lands in `unchanged` and
 * never reaches this module's input at all.
 *
 * There is exactly ONE field that can legitimately change while a job's
 * `status` stays `"running"`: `stop_request` (schema v8, WP 3.12) — the
 * operator's pending pause/cancel request against a running job, cleared
 * once the worker actually stops it. That is real "activity right now"
 * information (surfaced as a "Cancelling…"/"Pausing…" note next to the
 * job's action buttons) that must NOT cost the running card's animated
 * status-icon a rebuild — exactly the round-7 concern, scoped to the one
 * field that can actually drift here.
 *
 * Every OTHER field change on a job row (`status`, `paused_at`,
 * `finished_at`, `updated`/`up_to_date`/`summary_parse_ok`, `gc_execute`)
 * only ever happens ALONGSIDE a `status` transition in the real worker
 * (vault_api/worker.py finalizes a job's outcome fields and its status in
 * the same write) — so gating structural-vs-patch on `status` alone is not
 * an approximation, it is the actual guarantee. A job entering or leaving
 * the polled window (`added`/`removed` — e.g. it aged out of `?limit=20`,
 * or this is the very first sighting of a brand-new job) always means a
 * full re-render, same accepted simplification as `render-plan.js`'s games
 * tick (rare event; membership across the Active/Paused/Queue/History
 * sections can move, not just one card's own content).
 *
 * Pure DECISION only — no DOM access. `views/downloads.js` owns applying
 * the verdict (full section rebuild, or `.jobacts`/`.stopnote` text patch
 * on the named job ids, never touching `.badge .sic`).
 */

/**
 * @param {{isFirst: boolean, added: object[], updated: {prev: object, curr: object}[], removed: object[]} | null | undefined} diff
 *   `diffByKey`'s result for the `GET /v1/jobs` snapshot (keyed by job id).
 * @returns {{full: boolean, patchStopRequest: number[]}}
 *   `full: true` means "re-render every Downloads section from the current
 *   snapshot"; `patchStopRequest` is meaningless in that case. Otherwise
 *   `patchStopRequest` lists the job ids whose `stop_request` changed while
 *   their `status` did not — the only in-place patch this view ever makes.
 */
export function planJobsUpdate(diff) {
  if (!diff || diff.isFirst) {
    // No prior snapshot, or the very first paint — nothing on screen yet
    // to patch, only a full render can be correct.
    return { full: true, patchStopRequest: [] };
  }
  if ((diff.added && diff.added.length) || (diff.removed && diff.removed.length)) {
    return { full: true, patchStopRequest: [] };
  }

  const updated = diff.updated || [];

  // ---------------------------------------------------------------------
  // MUTATION TARGET 1: ANY status change anywhere in the batch forces a
  // full rebuild. If this branch were removed/weakened, a job moving
  // between sections (e.g. running -> done, or queued -> running) could be
  // classified as patch-only — its card would keep the WRONG structural
  // shape (wrong section, wrong action buttons, wrong icon) until some
  // unrelated later tick happened to force a full render.
  // ---------------------------------------------------------------------
  for (const { prev, curr } of updated) {
    if (prev.status !== curr.status) {
      return { full: true, patchStopRequest: [] };
    }
  }

  // ---------------------------------------------------------------------
  // MUTATION TARGET 2: a `stop_request` change on an otherwise-unchanged
  // job must land in `patchStopRequest`, NOT force a full rebuild. If this
  // were flipped (any update -> full), every operator pause/cancel click
  // would recreate the running card's animated status-icon node the
  // instant the server acknowledged the request — the exact round-7
  // mockup bug, reintroduced on the one live field this view has.
  // ---------------------------------------------------------------------
  // A `queue_position` change (WP D20: another client or a move reordered
  // the queue) changes which row sits where, so it needs a full render.
  for (const { prev, curr } of updated) {
    if (prev.queue_position !== curr.queue_position) {
      return { full: true, patchStopRequest: [] };
    }
  }

  const patchStopRequest = [];
  for (const { prev, curr } of updated) {
    if (prev.stop_request !== curr.stop_request) {
      patchStopRequest.push(curr.id);
    }
  }
  return { full: false, patchStopRequest };
}
