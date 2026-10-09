package dev.steamvault.app.ui.detail.logic

import dev.steamvault.app.net.model.JobSummary

/**
 * Which job (if any) drives THIS app's detail-sheet job-control section, and
 * which of pause/resume/cancel apply to it (WP 4b.6 brief: "pause/resume/
 * cancel for this app's live job, reuse DownloadsController patterns/
 * gating").
 *
 * Deliberately BROADER than `ui/library/logic/GameStatus.kt`'s
 * `findLiveJob`: that helper excludes `queued` on purpose because the
 * Library GRID CARD mirrors the mockup's pill (a queued job has no
 * card-level affordance -- it shows in the Downloads FIFO queue instead,
 * that file's kdoc). The detail sheet is a different surface: it mirrors
 * `ui/downloads/logic/JobPartition.kt`'s job-control gating (api/README.md
 * "Job control" table), which DOES offer Cancel on a queued job. A second,
 * differently-scoped helper is therefore correct here, not a duplicate of
 * the grid's.
 *
 * GC jobs are excluded from [findTrackedJob] -- pause/resume are meaningless
 * on a GC job (api/README.md job control table: pause on a GC job is
 * `409`), and this sheet's OWN GC action already owns any GC job IT starts,
 * via `GcFlowState` (`GcFlow.kt`), independent of this job-control section.
 *
 * Pure -- no Compose, no network. Covered by `DetailJobTest`.
 */
private val TRACKED_STATUSES = setOf("queued", "running", "paused")

/** The prefill job (if any) queued/running/paused for [appid] right now. */
fun findTrackedJob(jobs: List<JobSummary>, appid: Int): JobSummary? =
    jobs.firstOrNull { it.appid == appid && it.type == "prefill" && it.status in TRACKED_STATUSES }

/**
 * The job that drives the detail HEADER's status icon and word (WP
 * WEB-FEAT-6 review): [findTrackedJob] without a `queued` job. A queued job
 * is not running yet, so it must not read Downloading/Updating/Verifying
 * (`dispKind` maps every non-paused live job through `liveRunKind`); web's
 * header uses `findLiveJob`, which excludes queued the same way. The
 * action buttons keep [findTrackedJob] (Cancel on a queued job).
 */
fun headerLiveJob(jobs: List<JobSummary>, appid: Int): JobSummary? =
    findTrackedJob(jobs, appid)?.takeIf { it.status != "queued" }

enum class DetailJobAction { PAUSE, RESUME, CANCEL }

/**
 * Mirrors api/README.md's "Job control" table exactly: `queued` -> cancel
 * only (pause/resume both `409` on a queued job); `running` -> pause +
 * cancel; `paused` -> resume + cancel. `null` (no tracked job) or any other
 * status (finished) -> no actions.
 */
fun detailJobActions(job: JobSummary?): Set<DetailJobAction> = when (job?.status) {
    "queued" -> setOf(DetailJobAction.CANCEL)
    "running" -> setOf(DetailJobAction.PAUSE, DetailJobAction.CANCEL)
    "paused" -> setOf(DetailJobAction.RESUME, DetailJobAction.CANCEL)
    else -> emptySet()
}
