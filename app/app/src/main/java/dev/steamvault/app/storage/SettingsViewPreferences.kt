package dev.steamvault.app.storage

import android.content.Context
import android.content.SharedPreferences

private const val PREFS_FILE = "settings_view_prefs"
private const val KEY_EXPERT = "expert"
private const val KEY_OPEN_PREFIX = "open_"

/**
 * Per-device Settings page view state (WP D16, twin of the web's
 * `steamvault.settingsOpen` / `steamvault.settingsExpert` localStorage keys):
 * which sections the user opened or closed and whether the expert switch is
 * on. Plain `SharedPreferences` like [LibraryPreferences] -- not a secret.
 * An interface so the controller is testable on the JVM against
 * [InMemorySettingsViewPreferences].
 */
interface SettingsViewPreferences {
    /** `null` when the user never toggled this section (the default applies). */
    fun getSectionOpen(id: String): Boolean?
    fun setSectionOpen(id: String, open: Boolean)
    fun getExpert(): Boolean
    fun setExpert(on: Boolean)
}

class InMemorySettingsViewPreferences : SettingsViewPreferences {
    private val open = HashMap<String, Boolean>()
    private var expert = false

    override fun getSectionOpen(id: String): Boolean? = open[id]
    override fun setSectionOpen(id: String, open: Boolean) {
        this.open[id] = open
    }
    override fun getExpert(): Boolean = expert
    override fun setExpert(on: Boolean) {
        expert = on
    }
}

class SharedPreferencesSettingsViewPreferences(context: Context) : SettingsViewPreferences {
    private val prefs: SharedPreferences =
        context.applicationContext.getSharedPreferences(PREFS_FILE, Context.MODE_PRIVATE)

    override fun getSectionOpen(id: String): Boolean? {
        val key = KEY_OPEN_PREFIX + id
        return if (prefs.contains(key)) prefs.getBoolean(key, false) else null
    }

    override fun setSectionOpen(id: String, open: Boolean) {
        prefs.edit().putBoolean(KEY_OPEN_PREFIX + id, open).apply()
    }

    override fun getExpert(): Boolean = prefs.getBoolean(KEY_EXPERT, false)

    override fun setExpert(on: Boolean) {
        prefs.edit().putBoolean(KEY_EXPERT, on).apply()
    }
}
