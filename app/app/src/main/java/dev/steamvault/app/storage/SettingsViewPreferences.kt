package dev.steamvault.app.storage

import android.content.Context
import android.content.SharedPreferences

private const val PREFS_FILE = "settings_view_prefs"
private const val KEY_EXPERT = "expert"

/**
 * Per-device Settings page view state (WP D16, twin of the web's
 * `steamvault.settingsExpert` localStorage key): whether the expert switch
 * is on. WP ANDROID-REDESIGN-1c: the per-section open state is gone with
 * the collapsible blocks (one panel at a time, chosen per visit). Plain
 * `SharedPreferences` like [LibraryPreferences] -- not a secret. An interface
 * so the controller is testable on the JVM against
 * [InMemorySettingsViewPreferences].
 */
interface SettingsViewPreferences {
    fun getExpert(): Boolean
    fun setExpert(on: Boolean)
}

class InMemorySettingsViewPreferences : SettingsViewPreferences {
    private var expert = false

    override fun getExpert(): Boolean = expert
    override fun setExpert(on: Boolean) {
        expert = on
    }
}

class SharedPreferencesSettingsViewPreferences(context: Context) : SettingsViewPreferences {
    private val prefs: SharedPreferences =
        context.applicationContext.getSharedPreferences(PREFS_FILE, Context.MODE_PRIVATE)

    override fun getExpert(): Boolean = prefs.getBoolean(KEY_EXPERT, false)

    override fun setExpert(on: Boolean) {
        prefs.edit().putBoolean(KEY_EXPERT, on).apply()
    }
}
