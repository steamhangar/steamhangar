package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.UpdateStatusOut
import dev.steamvault.app.repo.UpdatesRepository
import kotlinx.coroutines.delay

/**
 * "Check updates" (WP UPD-3, Android twin of web UPD-2; API WP UPD-1,
 * user decision D14 2026-10-13): ask the vault to check every cached game
 * for a newer build WITHOUT downloading anything, wait for the check to
 * finish, then let the Library re-read `GET /v1/games` for the
 * `update_available` badges. Separate from "Check & update all cached
 * games" (`CachedPrefillOutcome.kt`), which stays and does download.
 *
 * Flow: `POST /v1/updates/check` answers at once (the run takes minutes,
 * past the client's 45 s call timeout), then `GET /v1/updates/status` is
 * polled every [UPDATE_CHECK_POLL_MS] until `running` is false -- bounded
 * by [UPDATE_CHECK_MAX_POLLS] polls (10 minutes). The poll is a plain
 * suspend loop: cancelling the caller's coroutine (the Library screen
 * leaving composition) stops it at the next `delay`.
 *
 * Pure apart from the injected repository: no Compose, no Android, so the
 * whole state machine is unit-tested with virtual time (`UpdateCheckTest`).
 */

/** Pause between two status polls. */
const val UPDATE_CHECK_POLL_MS = 3_000L

/** Polls before giving up waiting (200 x 3 s = 10 minutes). The check
 * itself keeps running on the vault; the badges then arrive with the
 * ordinary games poll. */
const val UPDATE_CHECK_MAX_POLLS = 200

/** Consecutive failed status polls before the wait is given up as an
 * error. One transient failure (Wi-Fi hiccup) must not end the wait. */
const val UPDATE_CHECK_MAX_STATUS_ERRORS = 3

/** Wire values of `POST /v1/updates/check`'s `status`. */
const val UPDATE_CHECK_STARTED = "started"
const val UPDATE_CHECK_ALREADY_RUNNING = "already_running"
const val UPDATE_CHECK_DISABLED = "disabled"

sealed class UpdateCheckResult {
    /** The check ran to an end; [status] is the settled status row. */
    data class Finished(val status: UpdateStatusOut) : UpdateCheckResult()

    /** The manifest oracle is off on this vault: nothing ran. */
    data object Disabled : UpdateCheckResult()

    /** `404`: a vault from before UPD-1, the feature does not exist there. */
    data object Unsupported : UpdateCheckResult()

    /** Still running after [UPDATE_CHECK_MAX_POLLS] polls; not an error. */
    data object StillRunning : UpdateCheckResult()

    /** The start call failed, or [UPDATE_CHECK_MAX_STATUS_ERRORS] status
     * polls in a row failed. */
    data class Failure(val error: VaultApiError) : UpdateCheckResult()
}

/**
 * Start an update check and wait for it (see the file kdoc).
 *
 * `already_running` (a scheduled or earlier manual check is going) joins
 * that run: its result is the one the user is waiting for. A `status`
 * value this client does not know is treated like `started` and polled --
 * the status endpoint is the authority on whether anything runs.
 */
suspend fun runUpdateCheck(
    repository: UpdatesRepository,
    pollIntervalMs: Long = UPDATE_CHECK_POLL_MS,
    maxPolls: Int = UPDATE_CHECK_MAX_POLLS,
    maxStatusErrors: Int = UPDATE_CHECK_MAX_STATUS_ERRORS,
): UpdateCheckResult {
    val started = try {
        repository.check()
    } catch (_: VaultApiError.NotFound) {
        return UpdateCheckResult.Unsupported
    } catch (e: VaultApiError) {
        return UpdateCheckResult.Failure(e)
    }
    if (started.status == UPDATE_CHECK_DISABLED || !started.enabled) return UpdateCheckResult.Disabled

    var statusErrors = 0
    repeat(maxPolls) {
        delay(pollIntervalMs)
        val status = try {
            repository.status()
        } catch (e: VaultApiError) {
            statusErrors++
            if (statusErrors >= maxStatusErrors) return UpdateCheckResult.Failure(e)
            null
        }
        if (status != null) {
            statusErrors = 0
            if (!status.enabled) return UpdateCheckResult.Disabled
            if (!status.running) return UpdateCheckResult.Finished(status)
        }
    }
    return UpdateCheckResult.StillRunning
}

/**
 * What the toast says about a [UpdateCheckResult] -- the decision only;
 * `LibraryStrings.updateCheckNotice` turns it into text from strings.xml.
 * [warn] mirrors [dev.steamvault.app.ui.library.LibraryToast.warn]: the
 * user should look at something (the vault, its settings), not just
 * acknowledge. A finished check where some games could not be asked
 * warns too (twin of web UPD-2): its answer is incomplete.
 *
 * Copy rule (docs/LEARNINGS.md, 4d-web): every sentence claims only what
 * its fields carry. `last_stale` counts games the oracle reported with a
 * newer build; `last_failed` games it could not answer for. An outcome
 * other than `completed` never reads as "no updates", and neither does a
 * completed run in which some games failed.
 */
sealed class UpdateCheckNotice(val warn: Boolean) {
    /** Completed, [stale] > 0 games have an update. */
    data class Found(val stale: Int, val failed: Int) : UpdateCheckNotice(warn = failed > 0)

    /** Completed, no game that answered has an update; [failed] did not answer. */
    data class NoneFound(val failed: Int) : UpdateCheckNotice(warn = failed > 0)

    /** Completed with nothing cached to ask about. */
    data object NothingCached : UpdateCheckNotice(warn = false)

    /** `bailed_out`: stopped after repeated oracle failures. */
    data object StoppedEarly : UpdateCheckNotice(warn = true)

    /** `aborted` / `interrupted`: the vault stopped during the run. */
    data object NotFinished : UpdateCheckNotice(warn = true)

    /** `error`: a bug on the vault, see its log. */
    data object VaultError : UpdateCheckNotice(warn = true)

    /** Finished with an outcome this client does not know (or none). */
    data object FinishedUnknown : UpdateCheckNotice(warn = false)

    data object Disabled : UpdateCheckNotice(warn = true)

    data object StillRunning : UpdateCheckNotice(warn = false)

    data object Unsupported : UpdateCheckNotice(warn = true)

    /** [detail] is the error's message, null when it has none. */
    data class Failed(val detail: String?) : UpdateCheckNotice(warn = true)
}

fun updateCheckNoticeFor(result: UpdateCheckResult): UpdateCheckNotice = when (result) {
    is UpdateCheckResult.Finished -> finishedNotice(result.status)
    UpdateCheckResult.Disabled -> UpdateCheckNotice.Disabled
    UpdateCheckResult.Unsupported -> UpdateCheckNotice.Unsupported
    UpdateCheckResult.StillRunning -> UpdateCheckNotice.StillRunning
    is UpdateCheckResult.Failure -> UpdateCheckNotice.Failed(result.error.message)
}

private fun finishedNotice(status: UpdateStatusOut): UpdateCheckNotice = when (status.last_outcome) {
    "completed" -> {
        val stale = status.last_stale ?: 0
        val failed = status.last_failed ?: 0
        when {
            stale > 0 -> UpdateCheckNotice.Found(stale, failed)
            (status.last_apps ?: 0) == 0 -> UpdateCheckNotice.NothingCached
            else -> UpdateCheckNotice.NoneFound(failed)
        }
    }
    "bailed_out" -> UpdateCheckNotice.StoppedEarly
    "aborted", "interrupted" -> UpdateCheckNotice.NotFinished
    "error" -> UpdateCheckNotice.VaultError
    else -> UpdateCheckNotice.FinishedUnknown
}
