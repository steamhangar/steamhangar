package dev.steamvault.app.ui.downloads

import android.content.res.Resources
import dev.steamvault.app.R

/**
 * The dynamic toast/error strings [DownloadsController] produces after an
 * async job-control call or a lazy log-excerpt fetch completes — same
 * escape-hatch pattern `ui/library/LibraryStrings.kt` documents (see
 * app/README.md's "String resources" convention): [DownloadsController] is
 * a plain, non-`@Composable` Kotlin class, so it cannot call
 * `stringResource`/`pluralStringResource` itself; this interface is
 * resolved through plain `Resources.getString` instead and injected the
 * same way `LibraryStrings` is.
 *
 * **Scope, deliberately narrow.** Everything else this WP's screen shows —
 * the paused-section hold note, the queue hint, the "Pausing…"/
 * "Cancelling…" stop-request notes, every log-excerpt display state's copy
 * — is STATIC UI chrome resolved directly via `stringResource(...)` inside
 * `DownloadsScreen.kt`'s `@Composable` functions (app/README.md's DEFAULT
 * rule), because all of that is renderable purely from already-available
 * state at composition time. Only the handful of strings below are ever
 * produced from inside a `scope.launch { }` block, which is the one case
 * app/README.md's rule requires this interface for at all — mirroring
 * exactly how `LibraryStrings` is scoped (toast text only, not every string
 * on the Library screen).
 *
 * The wording of [pauseRequested]/[resuming]/[cancelRequested] is a
 * verbatim, diffable port of `web/js/views/downloads.js`'s own
 * `onPause`/`onResume`/`onCancel` toast literals (the two frontends
 * describing the same job-control action, per the mockup-notes.md
 * copy-consistency spirit) — see `strings.xml`'s comment above each
 * `downloads_toast_*` entry for the exact web source line.
 */
interface DownloadsStrings {
    fun pauseRequested(): String
    fun resuming(): String
    fun cancelRequested(): String
    fun actionFailedFallback(): String
    fun logFetchErrorFallback(): String

    /** WP APP-FIX-2: the failure hint's Retry (web `onRetry`'s toast). */
    fun queuedForDownload(): String
}

class AndroidDownloadsStrings(private val resources: Resources) : DownloadsStrings {
    override fun pauseRequested(): String = resources.getString(R.string.downloads_toast_pause_requested)
    override fun resuming(): String = resources.getString(R.string.downloads_toast_resuming)
    override fun cancelRequested(): String = resources.getString(R.string.downloads_toast_cancel_requested)
    override fun actionFailedFallback(): String = resources.getString(R.string.downloads_toast_action_failed)
    override fun logFetchErrorFallback(): String = resources.getString(R.string.downloads_log_fetch_error_fallback)
    override fun queuedForDownload(): String = resources.getString(R.string.downloads_toast_retry_queued)
}
