/**
 * Client-side notification differ (WP 4a.2).
 *
 * docs/design/vault-app-mockup-NOTES.md, round 5 "Notifications are a poll,
 * not a push" (binding, frozen design source):
 *
 *   "v1 has no push channel; the real app derives these by polling
 *   GET /v1/jobs, GET /v1/games and GET /v1/clients and diffing against the
 *   previous poll (a finished job -> "cached", a game that turned stale ->
 *   "update available", a client that reports games but never hits the
 *   cache -> bypass warning). No new endpoint, no websocket, no
 *   server-held notification store."
 *
 * Plus job *Failures*, called out separately in the bell panel's own list
 * ("finished downloads (Cached), Update ready items, cache-bypass Warnings,
 * and job Failures").
 *
 * Event taxonomy shipped here (mapped to the NOTES sections above):
 *   - job_finished    <- "a finished job -> cached"
 *   - job_failed      <- "job Failures"
 *   - update_ready    <- "a game that turned stale -> update available"
 *   - bypass_suspected <- "a client... -> bypass warning"
 *   - bypass_resolved  <- symmetric counterpart of bypass_suspected, added
 *     per docs/LEARNINGS.md ("Transition detectors: persist state changes
 *     in BOTH directions... or enabling an event later fires falsely on
 *     first sight"). Not literally named in the NOTES bell-panel list, but
 *     the same transition-detector class as bypass_suspected — see the WP
 *     4a.2 report for why it is included. A UI that only ever shows the
 *     "suspected" half would have no way to tell "still bypassing" from
 *     "fixed the DNS setup last week" without this.
 *
 * Every function here is PURE (no fetch, no DOM, no clock reads) so the
 * full taxonomy is unit-testable headless (web/tests/notifications.test.js)
 * including the two invariants the DoD calls out by name:
 *   - the FIRST poll (no previous snapshot at all) must never fire a
 *     notification storm for data that was already true before the app
 *     opened;
 *   - a poll with no real change must produce zero events.
 */

import { diffByKey } from "./diff-utils.js";
import { ACTIVE_JOB_STATUSES } from "./lib/job-status.js";

/* Job statuses a job can transition FROM to still count as "news" when it
 * lands on done/error: the shared `ACTIVE_JOB_STATUSES` (lib/job-status.js,
 * WP ACTIVE-STATUS-1), which mirrors api/README.md's `ACTIVE_STATUSES` (WP 3.12):
 * queued, running and paused are all still "in flight"; cancelled/done/error
 * are terminal and the backend never rewrites a terminal job's status (see
 * "What a cancelled or paused prefill does NOT do" / "the outcome is what
 * actually happened; it is not rewritten"), so this set only matters for
 * distinguishing a genuine transition from a re-fetch of unchanged data —
 * diffByKey's `unchanged` bucket already screens out the latter, and this
 * guard is the defensive second layer for newly-added job rows (see
 * `forEachTransition` below).
 */

/**
 * Walk a diffByKey() result as (prev, curr) transition pairs. `updated`
 * pairs get their real prev; `added` items (a row diffByKey never saw
 * before, but NOT the first poll — first poll is filtered out entirely,
 * see below) get `prev = undefined`, meaning "assume it came from the
 * neutral/active baseline" — the handler decides what that means per
 * field. `removed` and `unchanged` never produce a transition.
 */
function forEachTransition(diff, handler) {
  if (diff.isFirst) return;
  for (const item of diff.added) handler(undefined, item);
  for (const { prev, curr } of diff.updated) handler(prev, curr);
}

/**
 * @param {object[] | null | undefined} prevJobs Previous `GET /v1/jobs` body.
 * @param {object[] | null | undefined} currJobs Current `GET /v1/jobs` body.
 * @returns {object[]} `job_finished` / `job_failed` events.
 */
export function diffJobsForNotifications(prevJobs, currJobs) {
  const diff = diffByKey(prevJobs, currJobs, (j) => j.id);
  const events = [];
  forEachTransition(diff, (prev, curr) => {
    // A newly-added row (prev undefined) is treated as having come from an
    // active state — it is genuinely new information about a job we never
    // saw queued/running (a job that started and finished between two poll
    // ticks is still real news). A row we DID see before only counts if it
    // was actually active — done/error are terminal and the backend never
    // flips a terminal job back to another terminal status, but failing
    // toward "no event" on unexpected data is cheap insurance.
    const wasActive = prev ? ACTIVE_JOB_STATUSES.has(prev.status) : true;
    if (!wasActive) return;
    if (curr.status === "done") {
      events.push({
        type: "job_finished",
        key: `job:${curr.id}:done`,
        jobId: curr.id,
        appid: curr.appid,
        jobType: curr.type,
      });
    } else if (curr.status === "error") {
      events.push({
        type: "job_failed",
        key: `job:${curr.id}:error`,
        jobId: curr.id,
        appid: curr.appid,
        jobType: curr.type,
      });
    }
    // Deliberately no event for "cancelled": an operator just clicked
    // Cancel, so a notification about their own action a moment later
    // would be noise, not news (see api/README.md "Job control" — cancel
    // is "deliberately not error").
  });
  return events;
}

/**
 * @param {object[] | null | undefined} prevGames Previous `GET /v1/games` body.
 * @param {object[] | null | undefined} currGames Current `GET /v1/games` body.
 * @returns {object[]} `update_ready` events.
 *
 * Honest limit: the shipped `GET /v1/games` (api/README.md) reports
 * `status` as one of `idle` | `running` | `done` | `error` — there is no
 * `stale` value yet ("apps.status gains no new values", WP 3.12; the
 * manifest oracle's per-app `verdict` in `GET /v1/oracle/{appid}` is a
 * separate, not-bulk-pollable field). This function is written against the
 * NOTES' documented contract ("a game that turned stale") so it needs no
 * change the day a `stale` status (or equivalent field) ships — see NOTES
 * open question 3, "does Phase 4 ship with three badges and gain orange
 * later". Until then it is correct and simply never fires, which is the
 * right behaviour for a field that does not exist yet, not a bug.
 *
 * **"Stale" requires cache content — frozen invariant (NOTES round 5,
 * finding 6: "'Stale' (and 'cached') require cache content — a status
 * rule, not a cosmetic").** A game can only be Update ready if it has
 * bytes on the cache to update; an empty app is Not cached, never Stale.
 * `GET /v1/games` is documented as never violating this server-side, but
 * this differ enforces it independently on the client too rather than
 * trusting the field blindly — the same "fail toward the safer reading"
 * posture as the bypass/job guards elsewhere in this file, and cheap
 * insurance against a future server bug shipping a stale flag on a
 * zero-byte app.
 */
export function diffGamesForNotifications(prevGames, currGames) {
  const diff = diffByKey(prevGames, currGames, (g) => g.appid);
  const events = [];
  forEachTransition(diff, (prev, curr) => {
    const wasStale = prev ? prev.status === "stale" : false;
    const hasCacheContent = (curr.size_bytes ?? 0) > 0;
    if (!wasStale && curr.status === "stale" && hasCacheContent) {
      events.push({
        type: "update_ready",
        key: `game:${curr.appid}:stale`,
        appid: curr.appid,
        name: curr.name,
      });
    }
  });
  return events;
}

/**
 * @param {object[] | null | undefined} prevClients Previous `GET /v1/clients` body.
 * @param {object[] | null | undefined} currClients Current `GET /v1/clients` body.
 * @returns {object[]} `bypass_suspected` / `bypass_resolved` events.
 */
export function diffClientsForNotifications(prevClients, currClients) {
  const diff = diffByKey(prevClients, currClients, (c) => c.client_id);
  const events = [];
  forEachTransition(diff, (prev, curr) => {
    const wasSuspected = prev ? !!prev.bypass_suspected : false;
    const isSuspected = !!curr.bypass_suspected;
    if (!wasSuspected && isSuspected) {
      events.push({
        type: "bypass_suspected",
        key: `client:${curr.client_id}:suspected`,
        clientId: curr.client_id,
      });
    } else if (wasSuspected && !isSuspected) {
      events.push({
        type: "bypass_resolved",
        key: `client:${curr.client_id}:resolved`,
        clientId: curr.client_id,
      });
    }
  });
  return events;
}

/**
 * Convenience: run all three differs over one poll cycle's before/after
 * snapshots and return a single flat event list (in jobs, games, clients
 * order — stable, not meaningful beyond determinism).
 *
 * @param {{
 *   prevJobs?: object[] | null, currJobs?: object[] | null,
 *   prevGames?: object[] | null, currGames?: object[] | null,
 *   prevClients?: object[] | null, currClients?: object[] | null,
 * }} snapshots
 * @returns {object[]}
 */
export function diffSnapshotsForNotifications({
  prevJobs,
  currJobs,
  prevGames,
  currGames,
  prevClients,
  currClients,
} = {}) {
  return [
    ...diffJobsForNotifications(prevJobs, currJobs),
    ...diffGamesForNotifications(prevGames, currGames),
    ...diffClientsForNotifications(prevClients, currClients),
  ];
}
