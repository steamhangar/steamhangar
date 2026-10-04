package dev.steamvault.app.ui.downloads.logic

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.JobSummary
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit

/**
 * "Pause all" / "Resume all" on the Downloads screen (WP WEB-FEAT-5) — a
 * port of `web/js/lib/bulk-jobs.js`: same targets, same order, same
 * concurrency bound, same wording. `BulkJobsWebTwinTest` checks that every
 * literal in [BulkWording] still appears in the web file.
 *
 * The wording lives here, not in `strings.xml`, for the same reason
 * `JobFailure.kt`'s hint texts do: it is a verbatim cross-frontend port
 * that a JVM test pins against the web source, and the toast text is
 * assembled from templates inside a coroutine.
 *
 * **What "all" covers.** vault-api runs one job at a time and pausing the
 * running job releases the worker slot, so the worker claims the next
 * queued job at once. Since WP WEB-FEAT-5 the API also parks a QUEUED
 * prefill job immediately. Pause all therefore pauses every queued prefill
 * job first and only then the running one(s). GC jobs are never paused
 * (the API answers 409). A running job that already has a `stop_request`
 * is not sent again. Resume all resumes every paused job.
 *
 * **Results.** 404 (gone) and 409 (finished or changed state meanwhile)
 * count as SKIPPED, not failed.
 */

/** Requests in flight at once, per phase (web `BULK_CONCURRENCY`). */
const val BULK_CONCURRENCY = 4

/** Every user-visible literal, ported verbatim from web `WORDING`. */
object BulkWording {
    const val PAUSE_ALL = "Pause all"
    const val RESUME_ALL = "Resume all"
    const val PAUSING = "Pausing…"
    const val RESUMING = "Resuming…"
    const val PAUSE_ARIA_ONE = "Pause 1 download"
    const val PAUSE_ARIA_MANY = "Pause all {n} downloads"
    const val RESUME_ARIA_ONE = "Resume 1 download"
    const val RESUME_ARIA_MANY = "Resume all {n} downloads"
    const val GROUP_LABEL = "All downloads"
    const val SCHEDULER_NOTE = "Pausing does not stop the scheduler: its next sweep can still queue new downloads."
    const val CONFIRM_TITLE_ONE = "Pause 1 download?"
    const val CONFIRM_TITLE_MANY = "Pause {n} downloads?"
    const val CONFIRM_BODY =
        "Queued downloads are paused first, then the running one, so nothing new starts in between. " +
            "Bytes already fetched stay on the cache. Resume all puts them back in the queue in their original order."
    const val CONFIRM_GC_NOTE = "Garbage-collection runs keep going: they cannot be paused."
    const val CONFIRM_YES = "Pause all"
    const val CONFIRM_NO = "Keep downloading"
    const val PAUSED_ALL_ONE = "Paused 1 download"
    const val PAUSED_ALL_MANY = "Paused {n} downloads"
    const val RESUMED_ALL_ONE = "Resumed 1 download"
    const val RESUMED_ALL_MANY = "Resumed {n} downloads"
    const val PAUSED_PARTIAL = "Paused {ok} of {total}"
    const val RESUMED_PARTIAL = "Resumed {ok} of {total}"
    const val PAUSE_FAILED = "{n} could not be paused: {reason}"
    const val RESUME_FAILED = "{n} could not be resumed: {reason}"
    const val SKIPPED = "{n} skipped (finished or changed meanwhile)"
    const val FALLBACK_REASON = "Request failed"
}

/** Replace `{key}` placeholders (web `fill`). Unknown keys stay as they are. */
fun fillTemplate(template: String, values: Map<String, Any>): String =
    Regex("\\{(\\w+)\\}").replace(template) { m -> values[m.groupValues[1]]?.toString() ?: m.value }

private fun countWord(n: Int, one: String, many: String): String =
    if (n == 1) one else fillTemplate(many, mapOf("n" to n))

fun pauseAllLabel(n: Int): String = countWord(n, BulkWording.PAUSE_ARIA_ONE, BulkWording.PAUSE_ARIA_MANY)
fun resumeAllLabel(n: Int): String = countWord(n, BulkWording.RESUME_ARIA_ONE, BulkWording.RESUME_ARIA_MANY)
fun pauseConfirmTitle(n: Int): String = countWord(n, BulkWording.CONFIRM_TITLE_ONE, BulkWording.CONFIRM_TITLE_MANY)

data class BulkPauseTargets(val queued: List<JobSummary>, val running: List<JobSummary>) {
    val count: Int get() = queued.size + running.size
}

/** Pause all's two phases, oldest id first in each (web `bulkPauseTargets`). */
fun bulkPauseTargets(jobs: List<JobSummary>): BulkPauseTargets {
    val prefill = jobs.filter { it.type == "prefill" }
    return BulkPauseTargets(
        queued = prefill.filter { it.status == "queued" }.sortedBy { it.id },
        running = prefill.filter { it.status == "running" && it.stop_request == null }.sortedBy { it.id },
    )
}

/** Every paused job, oldest id first (web `bulkResumeTargets`). */
fun bulkResumeTargets(jobs: List<JobSummary>): List<JobSummary> =
    jobs.filter { it.status == "paused" }.sortedBy { it.id }

data class BulkBarState(
    val pauseVisible: Boolean,
    val pauseCount: Int,
    val resumeVisible: Boolean,
    val resumeCount: Int,
    val gcActive: Boolean,
) {
    val visible: Boolean get() = pauseVisible || resumeVisible
}

/** What the bulk bar shows (web `bulkBarState`). */
fun bulkBarState(jobs: List<JobSummary>): BulkBarState {
    val pauseCount = bulkPauseTargets(jobs).count
    val resumeCount = bulkResumeTargets(jobs).size
    return BulkBarState(
        pauseVisible = pauseCount > 0,
        pauseCount = pauseCount,
        resumeVisible = resumeCount > 0,
        resumeCount = resumeCount,
        gcActive = jobs.any { it.type == "gc" && (it.status == "queued" || it.status == "running") },
    )
}

enum class BulkOutcome { OK, SKIPPED, FAILED }

data class BulkResult(val jobId: Int, val outcome: BulkOutcome, val reason: String? = null)

/** 404 / 409: the job is gone or no longer in a state the action applies to. */
fun isSkip(error: VaultApiError): Boolean = error.status == 404 || error.status == 409

private fun reasonOf(error: VaultApiError): String =
    error.detail?.takeIf { it.isNotBlank() } ?: error.message?.takeIf { it.isNotBlank() } ?: BulkWording.FALLBACK_REASON

/**
 * Run [action] for every job id, at most [limit] at a time. Never throws a
 * [VaultApiError]: each job settles to a [BulkResult], in input order.
 */
suspend fun runWithLimit(jobIds: List<Int>, limit: Int, action: suspend (Int) -> Unit): List<BulkResult> {
    val permits = Semaphore(limit.coerceAtLeast(1))
    return coroutineScope {
        jobIds.map { id ->
            async {
                permits.withPermit {
                    try {
                        action(id)
                        BulkResult(id, BulkOutcome.OK)
                    } catch (e: VaultApiError) {
                        BulkResult(id, if (isSkip(e)) BulkOutcome.SKIPPED else BulkOutcome.FAILED, reasonOf(e))
                    }
                }
            }
        }.awaitAll()
    }
}

/** Pause all: the queued phase to completion, THEN the running phase. */
suspend fun runBulkPause(
    targets: BulkPauseTargets,
    pause: suspend (Int) -> Unit,
    limit: Int = BULK_CONCURRENCY,
): List<BulkResult> {
    val first = runWithLimit(targets.queued.map { it.id }, limit, pause)
    val second = runWithLimit(targets.running.map { it.id }, limit, pause)
    return first + second
}

/** Resume all. */
suspend fun runBulkResume(
    targets: List<JobSummary>,
    resume: suspend (Int) -> Unit,
    limit: Int = BULK_CONCURRENCY,
): List<BulkResult> = runWithLimit(targets.map { it.id }, limit, resume)

enum class BulkKind { PAUSE, RESUME }

data class BulkSummary(val text: String, val warn: Boolean)

/** The one toast after a bulk run (web `bulkSummary`). */
fun bulkSummary(kind: BulkKind, results: List<BulkResult>): BulkSummary {
    val total = results.size
    val ok = results.count { it.outcome == BulkOutcome.OK }
    val failed = results.filter { it.outcome == BulkOutcome.FAILED }
    val skipped = results.count { it.outcome == BulkOutcome.SKIPPED }
    val pause = kind == BulkKind.PAUSE

    val head = if (ok == total) {
        if (pause) {
            countWord(total, BulkWording.PAUSED_ALL_ONE, BulkWording.PAUSED_ALL_MANY)
        } else {
            countWord(total, BulkWording.RESUMED_ALL_ONE, BulkWording.RESUMED_ALL_MANY)
        }
    } else {
        fillTemplate(
            if (pause) BulkWording.PAUSED_PARTIAL else BulkWording.RESUMED_PARTIAL,
            mapOf("ok" to ok, "total" to total),
        )
    }
    val parts = buildList {
        if (failed.isNotEmpty()) {
            add(
                fillTemplate(
                    if (pause) BulkWording.PAUSE_FAILED else BulkWording.RESUME_FAILED,
                    mapOf("n" to failed.size, "reason" to (failed.first().reason ?: BulkWording.FALLBACK_REASON)),
                ),
            )
        }
        if (skipped > 0) add(fillTemplate(BulkWording.SKIPPED, mapOf("n" to skipped)))
    }
    return BulkSummary(
        text = if (parts.isEmpty()) head else head + " — " + parts.joinToString("; "),
        warn = failed.isNotEmpty(),
    )
}
