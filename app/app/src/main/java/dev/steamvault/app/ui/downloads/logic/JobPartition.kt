package dev.steamvault.app.ui.downloads.logic

import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.ui.library.logic.liveRunKind

/**
 * Job partitioning + display wording for the Downloads screen (WP 4b.5) —
 * Kotlin port of `web/js/lib/job-partition.js` onto the same real WP 3.12
 * status set (api/README.md "Job control" / "The status model" / "The
 * worker slot").
 *
 * **The slot-release divergence, ported unchanged (api/README.md "The
 * worker slot — a paused job does NOT hold it", docs/WORKPACKAGES.md Phase
 * 4a header).** The frozen mockup assumes one job ever occupies a single
 * "active" slot (running OR paused), with the queue waiting behind it. The
 * real backend releases the worker slot on pause: `claim_next_job` claims
 * `queued` rows only, so a paused job's app can sit parked while a
 * completely different queued job is claimed and runs. [partitionJobs]
 * therefore treats `running` and `paused` as INDEPENDENT buckets (normally
 * 0-1 running, 0-N paused), not one mutually-exclusive slot — this is what
 * lets the Downloads screen render "Active" (what really holds the one
 * worker slot right now) and "Paused" (parked, holding nothing) as genuinely
 * separate sections instead of one that pretends a paused job still
 * occupies a slot it does not hold. Web's own screen states this note on
 * the paused card itself; this app's screen carries the identical wording
 * (see `ui/downloads/DownloadsStrings.kt` / `strings.xml`'s
 * `downloads_paused_hold_note`, `downloads_queue_hint_*`).
 *
 * **Divergence from the web port — unknown status is NOT silently dropped
 * (WP 4a.5 review nit, recorded in docs/WORKPACKAGES.md's Phase 4a
 * header).** `web/js/lib/job-partition.js`'s `partitionJobs` buckets a job
 * only when its status is exactly one of
 * running/paused/queued/done/error/cancelled — a row whose status is
 * anything else (a future status this client doesn't know about yet, or a
 * corrupted value) matches none of the web module's filters and
 * silently vanishes from every bucket: an operator-invisible job that still
 * exists server-side. This port instead routes an unrecognized status into
 * [PartitionedJobs.history] with a NEUTRAL presentation —
 * [jobIconKind] already falls back to `"none"` for an unmapped status
 * (mirrors [dev.steamvault.app.ui.status.StatusKind.fromWireName]'s own
 * unknown-kind fallback) and [jobStatusWord] already falls back to the raw
 * status string rather than fabricating a plausible-looking word — so an
 * honest "something happened, here it is, we don't have a name for it"
 * beats a job that disappears from the screen entirely. It deliberately
 * does NOT count toward [countPending]: an unrecognized status is not
 * confidently "still pending" either, so the nav pip stays fail-quiet
 * rather than fail-loud on the one signal that would page an operator for
 * every poll tick. This is an IMPROVEMENT over the web behaviour, not a
 * behavioural requirement carried over from it — recorded here rather than
 * silently, per the WP brief.
 *
 * Pure only — no Compose, no coroutines. Covered by `JobPartitionTest`.
 */
data class PartitionedJobs(
    val running: List<JobSummary>,
    val paused: List<JobSummary>,
    /** FIFO draw order (oldest job id first, api/README.md "Queue
     * semantics"), independent of the snapshot's own order. */
    val queued: List<JobSummary>,
    /** Keeps the snapshot's own order (`GET /v1/jobs` is newest-first per
     * api/README.md, so a job's most recent occurrence is already first). */
    val history: List<JobSummary>,
)

/** Statuses that count toward "something is pending" (the Downloads nav
 * pip) — mirrors [dev.steamvault.app.polling.PollingIntervals]'s own
 * `JOB_ACTIVE_STATUSES`, kept as an independent local copy rather than a
 * shared import: this module must stay standalone, and the two sets are
 * pinned to the same value by api/README.md's status table, not by each
 * other (same reasoning `web/js/lib/job-partition.js`'s header gives for
 * its own `PENDING_STATUSES` copy). */
private val PENDING_STATUSES = setOf("queued", "running", "paused")
private val HISTORY_STATUSES = setOf("done", "error", "cancelled")
private val KNOWN_STATUSES = setOf("queued", "running", "paused", "done", "error", "cancelled")

/**
 * @param jobs `GET /v1/jobs` snapshot.
 */
fun partitionJobs(jobs: List<JobSummary>): PartitionedJobs {
    val running = jobs.filter { it.status == "running" }
    val paused = jobs.filter { it.status == "paused" }
    val queued = jobs.filter { it.status == "queued" }.sortedBy { it.id }
    // Improvement over the web port (see module kdoc): an unrecognized
    // status lands in history too, instead of matching no filter at all.
    val history = jobs.filter { it.status in HISTORY_STATUSES || it.status !in KNOWN_STATUSES }
    return PartitionedJobs(running, paused, queued, history)
}

/** How many jobs the Downloads nav pip should count. */
fun countPending(jobs: List<JobSummary>): Int = jobs.count { it.status in PENDING_STATUSES }

/**
 * 1-based queue position of [jobId] within an already-FIFO-sorted `queued`
 * list (as returned by [partitionJobs]). `null` if not present.
 */
fun queuePosition(queued: List<JobSummary>, jobId: Int): Int? {
    val idx = queued.indexOfFirst { it.id == jobId }
    return if (idx == -1) null else idx + 1
}

/** Which [dev.steamvault.app.ui.status.StatusKind] wire name a job's badge
 * uses. `queued` has no persistent badge in this view (mockup parity: the
 * queue row is grip + name + position, no icon) — `"none"` is returned for
 * completeness/testability, not for display. An unrecognized status also
 * falls back to `"none"`, same fallback [dev.steamvault.app.ui.status.StatusKind.fromWireName]
 * itself already applies. */
fun jobIconKind(job: JobSummary): String = when (job.status) {
    "running" -> "running"
    "paused" -> "paused"
    "done" -> "cached"
    "error" -> "error"
    "cancelled" -> "cancelled"
    else -> "none"
}

// GC jobs are real `GET /v1/jobs` rows (type: "gc") — "Downloading" /
// "Update ready" wording is honest for a prefill job and misleading for a
// garbage-collection one, so GC gets its own words while sharing the same
// status-icon kind via jobIconKind (mirrors web/js/lib/job-partition.js).
private val PREFILL_WORD = mapOf(
    "queued" to "Queued",
    "running" to "Downloading",
    "paused" to "Paused",
    "done" to "Done",
    "error" to "Failed",
    "cancelled" to "Cancelled",
)
private val GC_WORD = mapOf(
    "queued" to "Garbage collection queued",
    "running" to "Collecting garbage",
    "paused" to "Paused", // unreachable in practice (pause 409s on a GC job) — defensive only
    "done" to "Garbage collected",
    "error" to "Garbage collection failed",
    "cancelled" to "Garbage collection cancelled",
)

/**
 * The word a job's badge/history-row shows, single source of truth for
 * both (mirrors web's `LABEL` table split by job type). Falls back to the
 * raw status string for a status this table doesn't know, rather than
 * fabricating something plausible-looking (same posture [partitionJobs]'s
 * unknown-status handling takes).
 */
fun jobStatusWord(job: JobSummary): String {
    val table = if (job.type == "gc") GC_WORD else PREFILL_WORD
    return table[job.status] ?: job.status
}

/**
 * The status-icon wire name of a job's Downloads card (WP WEB-FEAT-6, web
 * `activeJobKind`): a RUNNING PREFILL job shows [liveRunKind] of the app's
 * games row (running, updating or verify); every other job keeps
 * [jobIconKind]. `verify` stays a display kind, never a status, so the
 * partition above is unchanged.
 */
fun activeJobKind(job: JobSummary, game: GameSummary?): String =
    if (job.type == "prefill" && job.status == "running") liveRunKind(game).wireName else jobIconKind(job)

/** Web `RUN_KIND_WORD`: the words of the two derived kinds, equal to
 * `status_updating`/`status_verify` and web STATUS_LABEL. */
private val RUN_KIND_WORD = mapOf(
    "updating" to "Updating",
    "verify" to "Verifying",
)

/** The word next to [activeJobKind]'s icon (web `activeJobWord`):
 * "Updating"/"Verifying" for the two derived kinds, else [jobStatusWord]. */
fun activeJobWord(job: JobSummary, game: GameSummary?): String =
    RUN_KIND_WORD[activeJobKind(job, game)] ?: jobStatusWord(job)
