package dev.steamvault.app.ui.library

import android.content.res.Resources
import dev.steamvault.app.R
import dev.steamvault.app.ui.library.logic.UpdateCheckNotice

/**
 * The dynamic toast/error strings [LibraryController] produces after an
 * async action completes (WP 4b.4 review fix -- see app/README.md's
 * "String resources" conventions rule for the project-wide policy this
 * implements: static UI chrome belongs in `strings.xml`, not a Kotlin
 * literal).
 *
 * This is an interface (not [LibraryController] calling `Resources`
 * directly) for the same off-device-testability reason `CredentialStore`/
 * `LibraryPreferences` are interfaces: [LibraryController] otherwise has no
 * Android-framework dependency and its unit tests (were any added later)
 * should not need a real `Resources` instance either.
 *
 * Why this ISN'T `stringResource`/`pluralStringResource` called directly in
 * `LibraryScreen.kt` and handed down as a pre-resolved `data class`: every
 * one of these strings is only known AFTER a suspend network call returns
 * inside a `scope.launch { }` block in [LibraryController] (job counts,
 * freed bytes, failure counts) -- that is not composition time, so the
 * `@Composable` resource-resolution functions cannot be called there.
 * Resolving per-call through `Resources.getString`/`getQuantityString`
 * (which are plain, non-Composable Android APIs) is the correct fix, not a
 * workaround.
 */
interface LibraryStrings {
    fun queuedForDownload(): String
    fun pauseRequested(): String
    fun resuming(): String
    fun actionFailedFallback(): String
    fun deletePlanErrorFallback(): String
    fun jobsQueued(count: Int): String
    /** @param failedCount 0 when every delete in the batch succeeded. */
    fun freed(freedText: String, failedCount: Int): String
    /** WP UPD-3: the "Check updates" result toast. */
    fun updateCheckNotice(notice: UpdateCheckNotice): String
}

class AndroidLibraryStrings(private val resources: Resources) : LibraryStrings {
    override fun queuedForDownload(): String = resources.getString(R.string.library_toast_queued_for_download)
    override fun pauseRequested(): String = resources.getString(R.string.library_toast_pause_requested)
    override fun resuming(): String = resources.getString(R.string.library_toast_resuming)
    override fun actionFailedFallback(): String = resources.getString(R.string.library_toast_action_failed)
    override fun deletePlanErrorFallback(): String =
        resources.getString(R.string.library_toast_delete_plan_error_fallback)

    override fun jobsQueued(count: Int): String =
        resources.getQuantityString(R.plurals.library_toast_jobs_queued, count, count)

    override fun freed(freedText: String, failedCount: Int): String = if (failedCount > 0) {
        resources.getQuantityString(R.plurals.library_toast_freed_with_failures, failedCount, freedText, failedCount)
    } else {
        resources.getString(R.string.library_toast_freed, freedText)
    }

    override fun updateCheckNotice(notice: UpdateCheckNotice): String = when (notice) {
        is UpdateCheckNotice.Found -> withUncheckedCount(
            resources.getQuantityString(R.plurals.library_update_check_found, notice.stale, notice.stale),
            notice.failed,
        )
        is UpdateCheckNotice.NoneFound ->
            withUncheckedCount(resources.getString(R.string.library_update_check_none_found), notice.failed)
        UpdateCheckNotice.NothingCached -> resources.getString(R.string.library_update_check_nothing_cached)
        UpdateCheckNotice.StoppedEarly -> resources.getString(R.string.library_update_check_stopped_early)
        UpdateCheckNotice.NotFinished -> resources.getString(R.string.library_update_check_not_finished)
        UpdateCheckNotice.VaultError -> resources.getString(R.string.library_update_check_vault_error)
        UpdateCheckNotice.FinishedUnknown -> resources.getString(R.string.library_update_check_finished)
        UpdateCheckNotice.Disabled -> resources.getString(R.string.library_update_check_disabled)
        UpdateCheckNotice.StillRunning -> resources.getString(R.string.library_update_check_still_running)
        UpdateCheckNotice.Unsupported -> resources.getString(R.string.library_update_check_unsupported)
        is UpdateCheckNotice.Failed -> resources.getString(
            R.string.library_update_check_failed,
            notice.detail ?: resources.getString(R.string.library_toast_action_failed),
        )
    }

    private fun withUncheckedCount(base: String, failed: Int): String = if (failed > 0) {
        resources.getQuantityString(R.plurals.library_update_check_with_unchecked, failed, base, failed)
    } else {
        base
    }
}
