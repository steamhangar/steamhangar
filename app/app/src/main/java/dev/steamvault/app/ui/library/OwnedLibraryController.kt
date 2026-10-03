package dev.steamvault.app.ui.library

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import dev.steamvault.app.repo.SettingsRepository
import dev.steamvault.app.repo.SteamRelayRepository
import dev.steamvault.app.ui.library.logic.OwnedLibraryLoader
import dev.steamvault.app.ui.library.logic.OwnedLibraryState

/**
 * The owned-games list shared by Library and Downloads (WP APP-FEAT-1 /
 * APP-FIX-2) -- the Android counterpart of web's `owned-singleton.js`.
 * Held by `MainActivity` and rebuilt with the connection (or with a fresh
 * demo session), so Downloads can use the list the Library already loaded
 * instead of asking the relay again. All decisions live in
 * [OwnedLibraryLoader] (`ui/library/logic/OwnedLibrary.kt`, unit-tested);
 * this class only mirrors its state into Compose snapshot state.
 */
class OwnedLibraryController(
    settingsRepository: SettingsRepository,
    steamRelayRepository: SteamRelayRepository,
) {
    var state by mutableStateOf(OwnedLibraryState())
        private set

    private val loader = OwnedLibraryLoader(
        getSettings = { settingsRepository.get() },
        ownedGames = { steamId -> steamRelayRepository.ownedGames(steamId) },
        onChange = { state = it },
    )

    /** Library open and the notice's Reload. */
    suspend fun load() = loader.load()

    /** Downloads: only when nothing was loaded in this session yet. */
    suspend fun loadIfNeverLoaded() = loader.loadIfNeverLoaded()
}
