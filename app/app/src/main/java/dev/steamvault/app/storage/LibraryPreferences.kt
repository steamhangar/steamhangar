package dev.steamvault.app.storage

import android.content.Context
import android.content.SharedPreferences
import dev.steamvault.app.ui.library.logic.LibraryLayout
import dev.steamvault.app.ui.library.logic.LibrarySort

private const val PREFS_FILE = "library_prefs"
private const val KEY_LAYOUT = "layout"
private const val KEY_SORT = "sort"

/**
 * Where the per-device Library layout choice is persisted (WP 4b.4 brief:
 * "grid 2/3/list, persisted choice"; mockup-notes.md: "the real app stores
 * this locally... not on the server").
 *
 * Extracted as an interface for the same reason `CredentialStore` is (WP
 * 4b.2's kdoc): so the [LibraryLayout] round-trip is testable on the plain
 * JVM against an in-memory fake (`InMemoryLibraryPreferences`, test
 * sources), without needing a real `Context`/`SharedPreferences` (no
 * emulator/device is available in this environment -- app/README.md).
 * Unlike [CredentialStore], this is deliberately plain (non-encrypted)
 * `SharedPreferences`: a grid-column count is not a secret, and encrypting
 * it would add `EncryptedCredentialStore`'s Keystore dependency for no
 * confidentiality benefit.
 */
interface LibraryPreferences {
    fun getLayout(): LibraryLayout
    fun setLayout(layout: LibraryLayout)

    /** WP LIB-SORT: the per-device Library sort choice, same idiom as the
     * layout. Stored raw; the screen normalizes it against the live "last
     * played" flag, so a stored "Last played" survives the flag turning off. */
    fun getSort(): LibrarySort
    fun setSort(sort: LibrarySort)
}

class SharedPreferencesLibraryPreferences(context: Context) : LibraryPreferences {
    private val prefs: SharedPreferences =
        context.applicationContext.getSharedPreferences(PREFS_FILE, Context.MODE_PRIVATE)

    override fun getLayout(): LibraryLayout = LibraryLayout.fromPrefValue(prefs.getString(KEY_LAYOUT, null))

    override fun setLayout(layout: LibraryLayout) {
        prefs.edit().putString(KEY_LAYOUT, layout.prefValue).apply()
    }

    override fun getSort(): LibrarySort = LibrarySort.fromPrefValue(prefs.getString(KEY_SORT, null))

    override fun setSort(sort: LibrarySort) {
        prefs.edit().putString(KEY_SORT, sort.prefValue).apply()
    }
}
