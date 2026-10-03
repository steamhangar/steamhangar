package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.OwnedGame
import dev.steamvault.app.net.model.OwnedGamesRelayOut
import dev.steamvault.app.net.model.SettingsOut
import kotlinx.coroutines.CancellationException
import kotlinx.serialization.json.JsonPrimitive

/**
 * The owned Steam games the Library merges in (WP APP-FEAT-1, Android
 * parity with web WP WEB-FEAT-1 / `web/js/lib/owned-library.js`).
 *
 * **Which account.** The vault's ONE library SteamID64, the
 * `steam_library_steamid` setting (`GET /v1/settings`, WP API-FEAT-1),
 * read from the server on every Library open -- never from this device's
 * own Steam OpenID sign-in. Every device that uses the vault therefore
 * shows the same account, and the account is chosen in Settings (web or
 * this app). The value is a JSON STRING on the wire; anything that is not
 * a string is "not set", never coerced (a number would already be a
 * rounded, different account on a JavaScript sender).
 *
 * **When.** On Library open and on the notice's Reload only (never on the
 * poll interval: the relay calls Steam). Downloads may start the ONE load
 * when a job on screen has no vault name and nothing was loaded in this
 * session yet ([OwnedLibraryLoader.loadIfNeverLoaded]), same rule as
 * web's `ownedLibrary.loadIfNeverLoaded`.
 *
 * Pure plus one small state machine; no Android/Compose dependency.
 */
const val STEAM_LIBRARY_SETTING_KEY = "steam_library_steamid"

enum class OwnedStatus {
    /** Before the first load finished. */
    LOADING,

    /** The setting is blank: no SteamID64 chosen yet. */
    UNSET,

    /** The server has no `steam_library_steamid` setting at all (older than
     * WP API-FEAT-1 / rc4): it cannot store a library id, so "set it in
     * Settings" would be a false promise (coordinator decision "Weg A": the
     * server setting is the only source). */
    ABSENT,

    /** The owned list loaded (possibly empty -- see [OwnedNotice.PRIVATE_OR_EMPTY]). */
    READY,

    /** `GET /v1/settings` or the relay failed; vault games still show. */
    ERROR,
}

/** Which call failed, so the UI can word it without this file holding strings. */
enum class OwnedErrorPhase { SETTINGS, RELAY }

data class OwnedLibraryState(
    val status: OwnedStatus = OwnedStatus.LOADING,
    val steamId: String? = null,
    val games: List<OwnedGame> = emptyList(),
    val errorPhase: OwnedErrorPhase? = null,
    val errorStatus: Int? = null,
    val errorDetail: String? = null,
    val loading: Boolean = false,
) {
    /** The list to merge: only when [status] is [OwnedStatus.READY]. */
    val ownedGamesOrNull: List<OwnedGame>? get() = if (status == OwnedStatus.READY) games else null
}

/**
 * The stored library SteamID64 from a `GET /v1/settings` response, or "".
 * Only a JSON string counts (see the file kdoc); it is trimmed.
 */
fun steamIdFromSettings(response: SettingsOut?): String {
    val entry = response?.settings?.firstOrNull { it.key == STEAM_LIBRARY_SETTING_KEY } ?: return ""
    val primitive = entry.effective as? JsonPrimitive ?: return ""
    if (!primitive.isString) return ""
    return primitive.content.trim()
}

/** Whether the server has the `steam_library_steamid` setting at all
 * (absent on a server older than WP API-FEAT-1). */
fun librarySettingPresent(response: SettingsOut?): Boolean =
    response?.settings?.any { it.key == STEAM_LIBRARY_SETTING_KEY } == true

/** The one line under the Library header about the owned list. */
enum class OwnedNotice {
    LOADING,

    /** No SteamID64 set: offer Settings. */
    UNSET,

    /** The server is too old to store a library SteamID64: no action. */
    SERVER_TOO_OLD,

    /** `409`: no relay key on the server. Offer Settings and Reload. */
    NO_RELAY_KEY,

    /** `422`: the STORED SteamID64 was rejected. Offer Settings and Reload. */
    STORED_ID_REJECTED,

    /** Any other failure (settings read or relay). Offer Reload. */
    FAILED,

    /** The relay answered with 0 games: almost always a private profile, so
     * "0 owned" would be a false claim. Offer Reload. */
    PRIVATE_OR_EMPTY,
}

/** Which notice (if any) the Library shows, port of web `ownedNotice`.
 * `null` = nothing to say (owned list loaded with games). */
fun ownedNoticeFor(state: OwnedLibraryState?): OwnedNotice? {
    if (state == null) return null
    return when (state.status) {
        OwnedStatus.LOADING -> OwnedNotice.LOADING
        OwnedStatus.UNSET -> OwnedNotice.UNSET
        OwnedStatus.ABSENT -> OwnedNotice.SERVER_TOO_OLD
        // Status only, like web ownedNotice: a settings-read failure carries
        // no errorStatus (see OwnedLibraryLoader.errorState), so 409/422 here
        // are always the relay's.
        OwnedStatus.ERROR -> when (state.errorStatus) {
            409 -> OwnedNotice.NO_RELAY_KEY
            422 -> OwnedNotice.STORED_ID_REJECTED
            else -> OwnedNotice.FAILED
        }
        OwnedStatus.READY -> if (state.games.none { it.appid > 0 }) OwnedNotice.PRIVATE_OR_EMPTY else null
    }
}

/** Whether a notice offers the "Settings" action (the fix lives there). */
fun noticeOffersSettings(notice: OwnedNotice): Boolean =
    notice == OwnedNotice.UNSET || notice == OwnedNotice.NO_RELAY_KEY || notice == OwnedNotice.STORED_ID_REJECTED

/** Whether a notice offers "Reload". */
fun noticeOffersReload(notice: OwnedNotice): Boolean =
    notice != OwnedNotice.LOADING && notice != OwnedNotice.UNSET && notice != OwnedNotice.SERVER_TOO_OLD

/**
 * The owned-list fetch, with no timer of its own (port of web
 * `createOwnedLibraryLoader`): it runs exactly when [load] is called. A
 * generation token drops a superseded result (a second [load] while the
 * first is in flight wins).
 *
 * @param getSettings `GET /v1/settings` (the demo or real repository).
 * @param ownedGames the relay for an explicit SteamID64.
 * @param onChange called after every state change; the Compose holder
 *   (`ui/library/OwnedLibraryController.kt`) copies it into snapshot state.
 */
class OwnedLibraryLoader(
    private val getSettings: suspend () -> SettingsOut,
    private val ownedGames: suspend (String) -> OwnedGamesRelayOut,
    private val onChange: (OwnedLibraryState) -> Unit = {},
) {
    private var generation = 0

    /** `true` once a [load] has started and was not cancelled before it
     * finished (or once any load finished). */
    var everStarted: Boolean = false
        private set

    private var completedOnce = false

    var state: OwnedLibraryState = OwnedLibraryState()
        private set

    private fun set(next: OwnedLibraryState) {
        state = next
        onChange(next)
    }

    suspend fun load() {
        val myGeneration = ++generation
        everStarted = true
        set(state.copy(loading = true))
        try {
            val settings = try {
                getSettings()
            } catch (e: VaultApiError) {
                if (myGeneration != generation) return
                set(errorState(null, OwnedErrorPhase.SETTINGS, e))
                completedOnce = true
                return
            }
            if (myGeneration != generation) return
            if (!librarySettingPresent(settings)) {
                set(OwnedLibraryState(status = OwnedStatus.ABSENT))
                completedOnce = true
                return
            }
            val steamId = steamIdFromSettings(settings)
            if (steamId.isEmpty()) {
                set(OwnedLibraryState(status = OwnedStatus.UNSET, steamId = ""))
                completedOnce = true
                return
            }
            try {
                val response = ownedGames(steamId)
                if (myGeneration != generation) return
                set(OwnedLibraryState(status = OwnedStatus.READY, steamId = steamId, games = response.games))
            } catch (e: VaultApiError) {
                if (myGeneration != generation) return
                set(errorState(steamId, OwnedErrorPhase.RELAY, e))
            }
            completedOnce = true
        } catch (e: CancellationException) {
            // Should-fix 1 (APP-FEAT-2 review): the caller's scope went away
            // mid-request (the user left Library). Without this, `loading`
            // stuck at true and `everStarted` stayed true, so Downloads'
            // loadIfNeverLoaded never loaded. Undo both for the CURRENT
            // generation only (a newer load owns the state otherwise).
            if (myGeneration == generation) {
                everStarted = completedOnce
                state = state.copy(loading = false)
                onChange(state)
            }
            throw e
        }
    }

    /** Downloads' one allowed load (see the file kdoc). */
    suspend fun loadIfNeverLoaded() {
        if (!everStarted) load()
    }

    /** A settings-read failure keeps no status (web: `errorStatus: null`),
     * so [ownedNoticeFor] never reads its 409/422 as a relay answer. */
    private fun errorState(steamId: String?, phase: OwnedErrorPhase, e: VaultApiError) = OwnedLibraryState(
        status = OwnedStatus.ERROR,
        steamId = steamId,
        errorPhase = phase,
        errorStatus = if (phase == OwnedErrorPhase.RELAY) e.status else null,
        errorDetail = e.detail ?: e.message,
    )
}

/** Downloads' trigger for [OwnedLibraryLoader.loadIfNeverLoaded] (web
 * `maybeLoadOwnedNames`): some job on screen has no vault name. */
fun anyJobLacksVaultName(jobAppids: Collection<Int>, vaultGamesByAppid: Map<Int, GameSummary>): Boolean =
    jobAppids.any { !hasText(vaultGamesByAppid[it]?.name) }
