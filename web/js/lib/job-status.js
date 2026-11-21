/**
 * The one web definition of an "active" (in-flight) job status (WP
 * ACTIVE-STATUS-1, audit C-5): a job that is `queued`, `running` or `paused`
 * still holds its place in the queue and is not finished. Mirrors the
 * server's `ACTIVE_STATUSES` (api/vault_api/jobs.py, WP 3.12: `paused` is
 * IN the set) and the Android twin `PollingIntervals.ACTIVE_JOB_STATUSES`
 * (app/.../polling/PollingIntervals.kt).
 *
 * Every web module that asks "is this job still in flight?" imports this
 * set instead of keeping its own copy, so a new active status is added in
 * one place. The three sides are pinned to each other by
 * web/tests/job-status-twin.test.js (runs here, reads jobs.py and the
 * Kotlin source) and ActiveJobStatusesWebTwinTest.kt (GitHub CI, reads this
 * file). Change all three together.
 *
 * Leaf module on purpose: no imports, so pure lib/ modules use it without
 * pulling in the polling store or the API client (store.js imports
 * notifications.js, which needs this set too, so living in store.js would
 * be an import cycle).
 *
 * Treat the set as read-only; nothing may `add` to or `delete` from it.
 */
export const ACTIVE_JOB_STATUSES = new Set(["queued", "running", "paused"]);
