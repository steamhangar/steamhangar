package dev.steamvault.app.polling

import dev.steamvault.app.net.model.JobSummary

/**
 * Poll-interval decision functions (WP 4b.2 brief) — pure ports of
 * `web/js/store.js`'s `DEFAULT_INTERVALS` / `hasActiveJob` /
 * `nextJobsIntervalMs`, same numbers and the same active-status set, so
 * the Android app polls on the SAME cadence the web UI does rather than
 * inventing its own (mockup-notes.md's "poll + diff" model, shared by both
 * frontends). WorkManager wiring — the thing that actually calls these on
 * a schedule and respects Doze — is WP 4b.8, deliberately not this WP.
 */
object PollingIntervals {
    const val JOBS_FAST_MS = 2000L
    const val JOBS_SLOW_MS = 15000L
    const val GAMES_MS = 15000L
    const val CLIENTS_MS = 20000L

    /**
     * The one app definition of an "active" (in-flight) job status (WP
     * ACTIVE-STATUS-1, audit C-5): queued, running or paused. Every app
     * module that asks "is this job still in flight?" uses this set instead
     * of its own copy. Twin of `web/js/lib/job-status.js`'s
     * `ACTIVE_JOB_STATUSES` and the server's `ACTIVE_STATUSES`
     * (api/vault_api/jobs.py); pinned by `ActiveJobStatusesWebTwinTest`
     * (reads the web file) and `web/tests/job-status-twin.test.js` (reads
     * this line). Change all three together.
     */
    val ACTIVE_JOB_STATUSES: Set<String> = setOf("queued", "running", "paused")

    /** Pure: does this `GET /v1/jobs` snapshot contain a still-in-flight job? */
    fun hasActiveJob(jobs: List<JobSummary>): Boolean =
        jobs.any { it.status in ACTIVE_JOB_STATUSES }

    /**
     * Pure: which poll interval applies to the jobs loop given its most
     * recent snapshot (cadence: fast while anything is
     * queued/running/paused, slow otherwise).
     */
    fun nextJobsIntervalMs(
        jobs: List<JobSummary>,
        fastMs: Long = JOBS_FAST_MS,
        slowMs: Long = JOBS_SLOW_MS,
    ): Long = if (hasActiveJob(jobs)) fastMs else slowMs
}
