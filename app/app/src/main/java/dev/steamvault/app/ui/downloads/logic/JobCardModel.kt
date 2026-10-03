package dev.steamvault.app.ui.downloads.logic

import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.ui.library.logic.appTitle
import dev.steamvault.app.ui.status.StatusKind

/**
 * The Downloads screen's per-row render-diff models (WP 4b.5) — same
 * stability pattern `ui/library/logic/GameCardModel.kt` documents in full:
 * a plain, structurally-equal `data class` of stable types (primitives,
 * `String`, enums), keyed by job id in the Compose list (`DownloadsScreen.kt`
 * — `items(models, key = { it.jobId })`), so a poll tick that returns a
 * functionally-identical job produces an `==` model and Compose's
 * skip-on-equal-parameters mechanism keeps the row's `StatusIcon` animation
 * untouched.
 *
 * **Why [JobCardAction] is its own nested value instead of flattened
 * booleans on [JobCardModel] directly.** The WP brief's stability
 * requirement is specific: "stop_request drift may only change the action
 * field" — i.e. two [JobCardModel]s built from job snapshots that agree on
 * everything except `stop_request` must be equal in EVERY field except
 * [JobCardModel.action]. Grouping the stop_request-derived flags into one
 * sub-object makes that guarantee mechanically checkable (`JobCardModelTest`
 * asserts `a.copy(action = b.action) == b`), rather than relying on reading
 * five independent boolean-field diffs by eye.
 *
 * Client-side "a button's own network call is in flight" busy state is
 * DELIBERATELY not a field here — see `DownloadsController.busyJobIds`.
 * Keeping it out of this model keeps every field here a pure function of
 * `GET /v1/jobs` data (poll-tick derived), which is exactly what the
 * stop_request-only-diff guarantee needs to be provable at all: if client
 * click timing could also change this data class, "stop_request drift may
 * only change the action field" would not even be a well-formed claim to
 * test.
 */
enum class JobCardMode { ACTIVE, HELD }

/**
 * @param showPause `false` for a GC job even while `running` (api/README.md
 *   job-control table: pause on a GC job is `409` — mirrors
 *   `web/js/views/downloads.js::paintJobActions`'s `if (job.type ===
 *   "prefill")` gate, never rendering a button that would 409).
 * @param pauseEnabled `showPause` minus the two server-confirmed in-flight
 *   states (a pause already requested, or a cancel already requested).
 * @param cancelEnabled `showCancel` minus "a cancel is already requested".
 * @param pausing server-confirmed via `stop_request == "pause"` on a
 *   `running` job — the ONLY field here (with [cancelling]) that
 *   `stop_request` drift changes.
 * @param cancelling server-confirmed via `stop_request == "cancel"` on a
 *   `running` job.
 */
data class JobCardAction(
    val showPause: Boolean,
    val pauseEnabled: Boolean,
    val showResume: Boolean,
    val showCancel: Boolean,
    val cancelEnabled: Boolean,
    val pausing: Boolean,
    val cancelling: Boolean,
)

data class JobCardModel(
    val jobId: Int,
    val appid: Int,
    val name: String,
    val kind: StatusKind,
    val statusWord: String,
    val mode: JobCardMode,
    val action: JobCardAction,
)

/**
 * A job's title (WP APP-FIX-2, mirrors `web/js/views/downloads.js::nameFor`
 * since WP WEB-FIX-4): [appTitle]'s order -- the vault's name, then the
 * owned-games list's name, then "App <id>". A job carries only an appid,
 * and the vault row of an owned-only game queued from its detail sheet has
 * `name = NULL` (the real `POST /v1/prefill` inserts it that way), so
 * without [ownedNames] such a job reads "App 1234" although Steam told us
 * its title.
 *
 * @param ownedNames appid -> owned-list name
 *   ([dev.steamvault.app.ui.library.logic.ownedNamesByAppid]); empty when no
 *   owned list is loaded (no library SteamID64 set, relay failed), which
 *   leaves the vault name and the "App <id>" fallback.
 */
fun nameFor(appid: Int, gamesByAppid: Map<Int, GameSummary>, ownedNames: Map<Int, String> = emptyMap()): String =
    appTitle(appid, gamesByAppid[appid]?.name, ownedNames[appid])

/**
 * @param job a `running` or `paused` job — the only two statuses
 *   [PartitionedJobs.running]/[PartitionedJobs.paused] ever contain, the
 *   sole callers of this builder.
 * @param mode [JobCardMode.ACTIVE] for the Active section, [JobCardMode.HELD]
 *   for the Paused section (mirrors `web`'s `"active"`/`"held"` card mode).
 */
fun buildJobCardModel(
    job: JobSummary,
    gamesByAppid: Map<Int, GameSummary>,
    mode: JobCardMode,
    ownedNames: Map<Int, String> = emptyMap(),
): JobCardModel {
    val cancelling = job.status == "running" && job.stop_request == "cancel"
    val pausing = job.status == "running" && job.stop_request == "pause"
    val action = when (job.status) {
        "paused" -> JobCardAction(
            showPause = false,
            pauseEnabled = false,
            showResume = true,
            showCancel = true,
            cancelEnabled = true,
            pausing = false,
            cancelling = false,
        )
        "running" -> JobCardAction(
            showPause = job.type == "prefill",
            pauseEnabled = job.type == "prefill" && !pausing && !cancelling,
            showResume = false,
            showCancel = true,
            cancelEnabled = !cancelling,
            pausing = pausing,
            cancelling = cancelling,
        )
        else -> JobCardAction(
            showPause = false,
            pauseEnabled = false,
            showResume = false,
            showCancel = false,
            cancelEnabled = false,
            pausing = false,
            cancelling = false,
        )
    }
    return JobCardModel(
        jobId = job.id,
        appid = job.appid,
        name = nameFor(job.appid, gamesByAppid, ownedNames),
        kind = StatusKind.fromWireName(jobIconKind(job)),
        statusWord = jobStatusWord(job),
        mode = mode,
        action = action,
    )
}

/** The FIFO queue row — mirrors `web`'s "grip + name + position, no icon"
 * rule (a queued job has no persistent status badge worth showing). */
data class QueueRowModel(
    val jobId: Int,
    val appid: Int,
    val name: String,
    val position: Int,
)

fun buildQueueRowModel(
    job: JobSummary,
    position: Int,
    gamesByAppid: Map<Int, GameSummary>,
    ownedNames: Map<Int, String> = emptyMap(),
): QueueRowModel =
    QueueRowModel(jobId = job.id, appid = job.appid, name = nameFor(job.appid, gamesByAppid, ownedNames), position = position)

/** A finished/failed/cancelled row. `finishedAtLabel` is pre-formatted
 * ([formatTimestamp]) so the composable never needs a `java.time` import of
 * its own. */
data class HistoryRowModel(
    val jobId: Int,
    val appid: Int,
    val name: String,
    val kind: StatusKind,
    val statusWord: String,
    val finishedAtLabel: String,
)

fun buildHistoryRowModel(
    job: JobSummary,
    gamesByAppid: Map<Int, GameSummary>,
    ownedNames: Map<Int, String> = emptyMap(),
): HistoryRowModel =
    HistoryRowModel(
        jobId = job.id,
        appid = job.appid,
        name = nameFor(job.appid, gamesByAppid, ownedNames),
        kind = StatusKind.fromWireName(jobIconKind(job)),
        statusWord = jobStatusWord(job),
        finishedAtLabel = formatTimestamp(job.finished_at),
    )
