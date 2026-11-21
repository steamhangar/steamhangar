package dev.steamvault.app.notifications

import dev.steamvault.app.polling.PollingIntervals.ACTIVE_JOB_STATUSES

/**
 * Client-side notification differ (WP 4b.8) -- Kotlin port of
 * `web/js/notifications.js` onto the compact snapshot entries in
 * `NotificationSnapshot.kt`, per that file's kdoc for why the differ reads
 * the narrow entry types rather than the full API response shapes in
 * `net/model/`.
 *
 * Every function here is PURE (no fetch, no Android, no clock reads), same
 * posture as the web module, so the full taxonomy is unit-testable on the
 * plain JVM (`NotificationDifferTest`) -- including the two invariants the
 * web DoD calls out by name and this WP's brief repeats:
 *   - the FIRST poll (no previous snapshot at all) must never fire a
 *     notification storm for data that was already true before the app
 *     was ever installed;
 *   - a poll with no real change must produce zero events.
 *
 * **Divergence from the web port -- unrecognized PREVIOUS job status is
 * treated as "was active", not "was not active" (the WP brief: "the
 * Android improvement from 4b.5 (unknown statuses) applies where
 * relevant").** `ui/downloads/logic/JobPartition.kt`'s WP 4b.5 divergence
 * routes an unrecognized job status into History with a neutral
 * presentation instead of silently vanishing from every bucket the way
 * `web/js/lib/job-partition.js` does -- the underlying principle is "an
 * unrecognized value must not make real information about a job disappear
 * without a trace." The web notification differ's `wasActive` check
 * (`ACTIVE_JOB_STATUSES.has(prev.status)`) has the analogous gap: a job
 * whose PREVIOUS status was neither a known active status
 * (queued/running/paused) nor a known terminal one (done/error/cancelled)
 * -- a genuinely new status value this client has never seen, or a
 * corrupted one -- would be treated as `wasActive = false`, so a real
 * transition into `done`/`error` from that unrecognized status would be
 * silently swallowed instead of reported. This port closes that gap: an
 * unrecognized previous status counts as "was active" (same fail-toward-
 * reporting-real-news posture [JobPartition] already established for the
 * Downloads screen), so a job's genuine arrival at a terminal status is
 * never dropped just because its prior status was one this client's
 * [ACTIVE_JOB_STATUSES]/[JOB_TERMINAL_STATUSES] tables don't recognize yet.
 * This is an IMPROVEMENT over the web behaviour, not a behavioural
 * requirement carried over from it -- recorded here per the WP brief,
 * mirroring how `JobPartition.kt`'s own divergence is recorded.
 *
 * No analogous change was needed for [diffGames]/[diffClients]: both
 * already compare with plain equality against a single known value
 * (`"stale"` / `true`) rather than set membership, so an unrecognized
 * previous value is already treated as "not that value" by construction --
 * the same fail-safe outcome the jobs fix above had to be added for
 * explicitly.
 */

private val JOB_TERMINAL_STATUSES = setOf("done", "error", "cancelled")

/** @see NotificationDiffer.kt's kdoc, "Divergence from the web port". */
private fun wasJobActive(prev: JobSnapshotEntry?): Boolean {
    if (prev == null) return true // brand-new row -- assume the neutral/active baseline (web parity)
    if (prev.status in ACTIVE_JOB_STATUSES) return true
    if (prev.status !in JOB_TERMINAL_STATUSES) return true // unrecognized -- Android improvement, see kdoc
    return false
}

/** `job_finished` / `job_failed` events. Deliberately no event for
 * `cancelled` -- an operator's own action a moment later is not news (same
 * `web/js/notifications.js` rule, mirrors api/README.md "Job control"). */
fun diffJobs(prev: List<JobSnapshotEntry>?, curr: List<JobSnapshotEntry>): List<NotificationEvent> {
    val diff = diffByKey(prev, curr) { it.id }
    if (diff.isFirst) return emptyList()

    val events = mutableListOf<NotificationEvent>()
    fun consider(prevEntry: JobSnapshotEntry?, currEntry: JobSnapshotEntry) {
        if (!wasJobActive(prevEntry)) return
        when (currEntry.status) {
            "done" -> events.add(NotificationEvent.JobFinished(currEntry.id, currEntry.appid, currEntry.type))
            "error" -> events.add(NotificationEvent.JobFailed(currEntry.id, currEntry.appid, currEntry.type))
        }
    }
    for (added in diff.added) consider(null, added)
    for ((p, c) in diff.updated) consider(p, c)
    return events
}

/**
 * `update_ready` events. "Stale requires cache content" (mockup-notes.md
 * round 5 finding 6, `web/js/notifications.js`'s own honest-not-yet-shipped
 * note applies verbatim here too -- see `ui/library/logic/GameStatus.kt`'s
 * "Divergence 1": `GameSummary.status` has no `stale` wire value yet, so
 * this function is correct and simply never fires today, not a bug).
 */
fun diffGames(prev: List<GameSnapshotEntry>?, curr: List<GameSnapshotEntry>): List<NotificationEvent> {
    val diff = diffByKey(prev, curr) { it.appid }
    if (diff.isFirst) return emptyList()

    val events = mutableListOf<NotificationEvent>()
    fun consider(prevEntry: GameSnapshotEntry?, currEntry: GameSnapshotEntry) {
        val wasStale = prevEntry?.status == "stale"
        val hasCacheContent = (currEntry.sizeBytes ?: 0L) > 0L
        if (!wasStale && currEntry.status == "stale" && hasCacheContent) {
            events.add(NotificationEvent.UpdateReady(currEntry.appid, currEntry.name))
        }
    }
    for (added in diff.added) consider(null, added)
    for ((p, c) in diff.updated) consider(p, c)
    return events
}

/** `bypass_suspected` / `bypass_resolved` events -- the transition detector
 * pinned in both directions per `docs/LEARNINGS.md`. */
fun diffClients(prev: List<ClientSnapshotEntry>?, curr: List<ClientSnapshotEntry>): List<NotificationEvent> {
    val diff = diffByKey(prev, curr) { it.clientId }
    if (diff.isFirst) return emptyList()

    val events = mutableListOf<NotificationEvent>()
    fun consider(prevEntry: ClientSnapshotEntry?, currEntry: ClientSnapshotEntry) {
        val wasSuspected = prevEntry?.bypassSuspected ?: false
        val isSuspected = currEntry.bypassSuspected
        if (!wasSuspected && isSuspected) {
            events.add(NotificationEvent.BypassSuspected(currEntry.clientId))
        } else if (wasSuspected && !isSuspected) {
            events.add(NotificationEvent.BypassResolved(currEntry.clientId))
        }
    }
    for (added in diff.added) consider(null, added)
    for ((p, c) in diff.updated) consider(p, c)
    return events
}

/** Runs all three differs over one poll cycle's before/after snapshots and
 * returns a single flat event list (jobs, games, clients order -- stable,
 * not meaningful beyond determinism, same as the web combined helper). */
fun diffSnapshots(prev: NotificationSnapshot?, curr: NotificationSnapshot): List<NotificationEvent> =
    diffJobs(prev?.jobs, curr.jobs) + diffGames(prev?.games, curr.games) + diffClients(prev?.clients, curr.clients)
