package dev.steamvault.app.ui.settings

import android.content.res.Resources
import dev.steamvault.app.R

/**
 * [SettingsController]'s dynamic strings -- same pattern as
 * `ui/library/LibraryStrings.kt`/`ui/onboarding/OnboardingStrings.kt`
 * (app/README.md's "String resources" convention): these are only known
 * after a suspend network call returns inside a coroutine the controller
 * launches, not at composition time.
 */
interface SettingsStrings {
    fun loadFailedFallback(cause: Throwable): String
    fun savedToast(): String
    fun saveFailedFallback(): String
    fun libraryIdSaved(): String
    fun libraryIdCleared(): String
    fun libraryIdUnchanged(): String
    fun libraryIdReset(): String
}

class AndroidSettingsStrings(private val resources: Resources) : SettingsStrings {
    override fun loadFailedFallback(cause: Throwable): String =
        resources.getString(R.string.settings_load_error, cause.message ?: "")
    override fun savedToast(): String = resources.getString(R.string.settings_toast_saved)
    override fun saveFailedFallback(): String = resources.getString(R.string.settings_toast_save_failed_fallback)
    override fun libraryIdSaved(): String = resources.getString(R.string.settings_library_id_toast_saved)
    override fun libraryIdCleared(): String = resources.getString(R.string.settings_library_id_toast_cleared)
    override fun libraryIdUnchanged(): String = resources.getString(R.string.settings_library_id_toast_unchanged)
    override fun libraryIdReset(): String = resources.getString(R.string.settings_library_id_toast_reset)
}
