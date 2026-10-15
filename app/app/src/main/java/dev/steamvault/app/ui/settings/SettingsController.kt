package dev.steamvault.app.ui.settings

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.AboutOut
import dev.steamvault.app.net.steam.SteamId64
import dev.steamvault.app.repo.AboutRepository
import dev.steamvault.app.repo.SteamRelayRepository
import dev.steamvault.app.ui.library.logic.steamIdFromSettings
import dev.steamvault.app.ui.settings.logic.AboutLoadFailure
import dev.steamvault.app.ui.settings.logic.LibraryLookupError
import dev.steamvault.app.ui.settings.logic.LibraryPreview
import dev.steamvault.app.ui.settings.logic.LibrarySteamIdSavePlan
import dev.steamvault.app.ui.settings.logic.PREVIEW_LIMIT
import dev.steamvault.app.ui.settings.logic.classifyAboutError
import dev.steamvault.app.ui.settings.logic.describeLookupError
import dev.steamvault.app.ui.settings.logic.libraryIdResetBody
import dev.steamvault.app.ui.settings.logic.planLibrarySteamIdSave
import dev.steamvault.app.net.model.ScheduleOut
import dev.steamvault.app.net.model.SettingsOut
import dev.steamvault.app.repo.ScheduleRepository
import dev.steamvault.app.repo.SettingsRepository
import dev.steamvault.app.repo.STEAM_LOGIN_NEEDS_VAULT_ADDRESS
import dev.steamvault.app.repo.SteamIdentityRepository
import dev.steamvault.app.repo.SteamLoginResult
import dev.steamvault.app.storage.CredentialStore
import dev.steamvault.app.storage.InMemorySettingsViewPreferences
import dev.steamvault.app.storage.SettingsViewPreferences
import dev.steamvault.app.ui.settings.logic.CoreApplyLine
import dev.steamvault.app.ui.settings.logic.RiskyItem
import dev.steamvault.app.ui.settings.logic.SettingsLayout
import dev.steamvault.app.ui.settings.logic.SettingDraft
import dev.steamvault.app.ui.settings.logic.SteamLibraryStatus
import dev.steamvault.app.ui.settings.logic.buildSettingsPatchDraft
import dev.steamvault.app.ui.settings.logic.settingsDirty
import dev.steamvault.app.ui.settings.logic.steamLibraryStatusFor
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch

/** What the Connection section shows -- never the API key itself (WP brief: "never the API key"). */
data class ConnectionSummary(val profileKind: String?, val baseUrl: String?) {
    val isConfigured: Boolean get() = profileKind != null && !baseUrl.isNullOrBlank()
}

/**
 * Everything the Settings screen needs (WP 4b.7 brief) -- same thin-glue
 * shape every other screen controller in this app documents. Three
 * independent surfaces, each backed by its own real source, same split
 * `web/js/views/settings.js` documents:
 *
 *  - Vault / Schedule / Webhook -- one form over `GET`/`PATCH /v1/settings`
 *    (ADR-0009). The PATCH body is built by
 *    `ui/settings/logic/SettingsDiff.kt` from [drafts], populated ONLY by
 *    fields the user actually edits (via [setDraft]/[resetDraft]) -- never
 *    pre-seeded with every field's current value on [load] -- which is what
 *    makes "the body contains only changed keys" true by construction.
 *  - Steam identity -- the existing sign-in/out state
 *    ([dev.steamvault.app.repo.SteamIdentityRepository], unchanged from WP
 *    4b.3) plus, as of WP 4h.4, an on-demand library CHECK
 *    ([checkSteamLibrary]) against vault-api's relay -- there is no key to
 *    manage anymore (ADR-0004's second addendum), so this replaces the
 *    former Web API key entry/masked-display/remove UI this class used to
 *    own.
 *  - Connection -- [connectionSummary] reads [CredentialStore] directly
 *    (never the API key); [disconnect] clears the WHOLE store
 *    ("forget this vault" -- [CredentialStore.clear]'s own documented
 *    contract, deliberately broader than [CredentialStore.clearSteamIdentity]),
 *    and relies on the caller ([dev.steamvault.app.MainActivity]) to notice
 *    the connection is gone and swap back to the onboarding gate -- this
 *    controller has no reference to (and does not need one) whatever
 *    polling loop was running on another screen: this app's screens are
 *    plain state-switched (`ui/nav/Destination.kt`), so a screen not
 *    currently composed has no `LaunchedEffect` alive to stop in the first
 *    place, and a stale [dev.steamvault.app.net.VaultApiClient] can no
 *    longer be reached (nothing keeps `MainActivity`'s client
 *    reference around once it rebuilds from the now-cleared store).
 *
 * [settingsRepository] is a [SettingsRepository] rather than a raw
 * `VaultApiClient` (WP APP-DEMO refactor) -- the same seam every other
 * screen controller already has, so demo mode can hand this class an
 * in-memory [dev.steamvault.app.demo.DemoSettingsRepository] instead of
 * ever needing this class to know demo mode exists.
 *
 * [scheduleRepository] (WP AG-3, the seventh such seam) backs the
 * `GET /v1/schedule` read [schedule] exposes -- fetched best-effort inside
 * [load] alongside [settingsResponse]: a `/v1/schedule` failure must not
 * block the rest of the settings form from rendering, same posture the web
 * port's `load()` takes (`api.schedule().catch(() => null)`,
 * `web/js/views/settings.js`). [schedule] feeds
 * `ui/settings/logic/SchedulePresentation.kt`'s two pure functions, never
 * anything computed a second time from [settingsResponse]'s own
 * `sweep_include_cached`/`auto_gc` values.
 */
class SettingsController(
    private val settingsRepository: SettingsRepository,
    private val scheduleRepository: ScheduleRepository,
    private val credentialStore: CredentialStore,
    private val identityRepository: SteamIdentityRepository,
    private val strings: SettingsStrings,
    /** WP APP-FEAT-1: the relay for the "Steam library" block's Preview. */
    private val steamRelayRepository: SteamRelayRepository,
    /** WP APP-FEAT-2: `GET /v1/about` for the About section. */
    private val aboutRepository: AboutRepository,
    /** WP D16: remembered open sections and the expert switch (per device). */
    private val viewPrefs: SettingsViewPreferences = InMemorySettingsViewPreferences(),
) {
    var loading by mutableStateOf(true)
        private set
    var loadError by mutableStateOf<String?>(null)
        private set
    var settingsResponse by mutableStateOf<SettingsOut?>(null)
        private set
    /** Last `GET /v1/schedule` response, or `null` before the first
     * successful fetch / after a failed one -- see this class's kdoc. */
    var schedule by mutableStateOf<ScheduleOut?>(null)
        private set
    var drafts by mutableStateOf<Map<String, SettingDraft>>(emptyMap())
        private set
    var saving by mutableStateOf(false)
        private set
    var saveError by mutableStateOf<String?>(null)
        private set
    var toast by mutableStateOf<String?>(null)
        private set

    // ---- D16: sections, expert switch, risky confirm, apply line ------------

    /** Sections the user opened or closed (stored per device); the rest use the default. */
    private var openOverrides by mutableStateOf(readOpenOverrides())

    /** The "Show expert settings" switch (stored per device). */
    var expertOn by mutableStateOf(viewPrefs.getExpert())
        private set

    /** Risky changes waiting for the user's yes (the confirm dialog), or `null`. */
    var riskyPending by mutableStateOf<List<RiskyItem>?>(null)
        private set

    /** True after the user cancelled the confirm dialog: nothing was sent. Cleared by the next edit. */
    var riskyCancelled by mutableStateOf(false)
        private set

    /** vault-core's apply state as one line, or `null` before it has reported. */
    val coreApplyLine: CoreApplyLine? get() = SettingsLayout.coreApplyLine(settingsResponse?.core_apply)

    private fun readOpenOverrides(): Map<String, Boolean> {
        val result = LinkedHashMap<String, Boolean>()
        for (section in SettingsLayout.SECTIONS) {
            val stored = viewPrefs.getSectionOpen(section.id)
            if (stored != null) result[section.id] = stored
        }
        return result
    }

    fun isSectionOpen(id: String): Boolean = openOverrides[id] ?: (id in SettingsLayout.DEFAULT_OPEN)

    fun setSectionOpen(id: String, open: Boolean) {
        openOverrides = openOverrides + (id to open)
        viewPrefs.setSectionOpen(id, open)
    }

    fun setExpert(on: Boolean) {
        expertOn = on
        viewPrefs.setExpert(on)
    }

    var identityState by mutableStateOf(identityRepository.state())
        private set
    var loginError by mutableStateOf<String?>(null)
        private set

    /** WP 4h.4: what the last [checkSteamLibrary] call found (or
     * [SteamLibraryStatus.Unknown] before the first one). */
    var libraryStatus by mutableStateOf<SteamLibraryStatus>(SteamLibraryStatus.Unknown)
        private set
    var libraryChecking by mutableStateOf(false)
        private set

    // ---- Steam library block (WP APP-FEAT-1) ---------------------------------

    /** The SteamID64 field. Pre-filled from the stored setting on [load] and
     * after Reset; never disabled (Preview must work on a read-only vault
     * and on an older server without the setting). */
    var libraryIdInput by mutableStateOf("")
    var libraryIdError by mutableStateOf<LibraryIdMessage?>(null)
        private set
    var libraryIdBusy by mutableStateOf(false)
        private set
    var libraryPreview by mutableStateOf<LibraryPreview?>(null)
        private set
    var libraryPreviewError by mutableStateOf<LibraryLookupError?>(null)
        private set
    var libraryPreviewBusy by mutableStateOf(false)
        private set

    // ---- About (WP APP-FEAT-2) ---------------------------------------------------

    var about by mutableStateOf<AboutOut?>(null)
        private set
    var aboutFailure by mutableStateOf<AboutLoadFailure?>(null)
        private set
    var aboutErrorDetail by mutableStateOf<String?>(null)
        private set
    var aboutLoading by mutableStateOf(false)
        private set

    /** WP WEB-FIX-8: dirty means the PATCH would change something (a field
     * typed back to its saved value is clean again), so the save bar shows
     * and hides like the web's. */
    val isDirty: Boolean get() = settingsDirty(settingsResponse?.settings, drafts)
    val isReadonly: Boolean get() = settingsResponse?.readonly ?: false

    suspend fun load() {
        loading = true
        loadError = null
        try {
            settingsResponse = settingsRepository.get()
            libraryIdInput = steamIdFromSettings(settingsResponse)
        } catch (e: VaultApiError) {
            loadError = e.detail ?: strings.loadFailedFallback(e)
        } finally {
            loading = false
        }
        // Best-effort, deliberately independent of the try/catch above: a
        // failed/unreachable /v1/schedule must not block the settings form
        // itself from loading -- sweepTargetsMessage/cachedSweepGcRiskWarning
        // both already treat a null schedule as "print nothing" (see
        // SchedulePresentation.kt).
        schedule = try {
            scheduleRepository.get()
        } catch (e: VaultApiError) {
            null
        }
    }

    // WP WEB-FIX-8: any edit (or Discard) clears a previous save error, so
    // the save bar's status line goes back to "Unsaved changes" like the web.
    fun setDraft(key: String, draft: SettingDraft) {
        drafts = drafts + (key to draft)
        saveError = null
        riskyCancelled = false
    }

    fun resetDraft(key: String) {
        drafts = drafts + (key to SettingDraft.Reset)
        saveError = null
        riskyCancelled = false
    }

    fun discard() {
        drafts = emptyMap()
        saveError = null
        riskyPending = null
        riskyCancelled = false
    }

    /**
     * Saves the drafts. A body that sets or clears a risky key (D16b) first
     * opens the confirm dialog ([riskyPending]) and sends nothing; the
     * user's yes comes back through [confirmRisky], which calls this again
     * with [confirmed] set, so the PATCH carries `?confirm_risky=true`.
     */
    suspend fun save(confirmed: Boolean = false) {
        val entries = settingsResponse?.settings ?: return
        val patch = buildSettingsPatchDraft(entries, drafts)
        if (patch.isEmpty()) {
            drafts = emptyMap()
            return
        }
        riskyCancelled = false
        if (!confirmed) {
            val riskyKeys = SettingsLayout.riskyKeysInBody(entries, patch)
            if (riskyKeys.isNotEmpty()) {
                riskyPending = SettingsLayout.riskyItems(entries, patch, riskyKeys)
                return
            }
        }
        // WP WEB-FIX-8: the drafts this PATCH carries. An edit made while it
        // is in flight replaces its key's draft, so after success only the
        // drafts that are still the sent ones are dropped -- a later edit
        // survives and keeps the save bar up instead of vanishing silently.
        val sent = drafts
        saving = true
        saveError = null
        try {
            settingsResponse = if (confirmed) settingsRepository.patchConfirmed(patch) else settingsRepository.patch(patch)
            drafts = drafts.filter { (key, draft) -> sent[key] != draft }
            toast = strings.savedToast()
            // A saved PATCH can change sweep_include_cached/auto_gc, which
            // changes sweep_cached_gc_risk server-side -- re-fetch so the
            // warning reflects the just-saved values immediately rather than
            // whatever GET /v1/schedule answered at load(). Best-effort,
            // same reasoning as load(): a failed refetch must not undo the
            // successful save (web/js/views/settings.js's own WP 4d-web
            // comment, ported verbatim in spirit).
            schedule = try {
                scheduleRepository.get()
            } catch (e: VaultApiError) {
                schedule
            }
        } catch (e: VaultApiError) {
            // 422 field errors (api/README.md: "one bad value... fails the
            // request... with a DISTINCT detail") are surfaced verbatim --
            // vault_api's detail string already names the offending key
            // (e.g. "'schedule_window': ...").
            val detail = SettingsLayout.detailMessage(e.detail)
            val serverRisky = SettingsLayout.riskyKeysFrom428(e.detail)
            if (e.status == 428 && !confirmed && serverRisky.isNotEmpty()) {
                // The server knows a risky key the client did not: ask, never auto-confirm.
                riskyPending = SettingsLayout.riskyItems(entries, patch, serverRisky)
            } else {
                saveError = SettingsLayout.describeSaveError(e.status, detail, e.message ?: strings.saveFailedFallback(), confirmed)
            }
        } finally {
            saving = false
        }
    }

    fun dismissToast() {
        toast = null
    }

    /** The user said yes in the risky-confirm dialog: send the same PATCH with `?confirm_risky=true`. */
    suspend fun confirmRisky() {
        riskyPending = null
        save(true)
    }

    /** The user said no: nothing is sent, and the save bar says so. */
    fun cancelRisky() {
        riskyPending = null
        riskyCancelled = true
    }

    // ---- Steam library block (WP APP-FEAT-1) ---------------------------------

    /** Save: its own PATCH, independent of the shared Save bar (unsaved drafts
     * elsewhere survive it). Decisions in [planLibrarySteamIdSave]. */
    suspend fun saveLibrarySteamId() {
        libraryIdError = null
        when (val plan = planLibrarySteamIdSave(settingsResponse, libraryIdInput)) {
            LibrarySteamIdSavePlan.Absent -> libraryIdError = LibraryIdMessage.ABSENT
            LibrarySteamIdSavePlan.EnvOnly -> libraryIdError = LibraryIdMessage.ENV_ONLY
            LibrarySteamIdSavePlan.Readonly -> libraryIdError = LibraryIdMessage.READONLY
            LibrarySteamIdSavePlan.Invalid -> libraryIdError = LibraryIdMessage.INVALID
            LibrarySteamIdSavePlan.Unchanged -> toast = strings.libraryIdUnchanged()
            is LibrarySteamIdSavePlan.Patch -> {
                libraryIdBusy = true
                try {
                    settingsResponse = settingsRepository.patch(plan.body)
                    toast = if (plan.cleared) strings.libraryIdCleared() else strings.libraryIdSaved()
                } catch (e: VaultApiError) {
                    libraryIdError = LibraryIdMessage.SERVER
                    libraryIdServerDetail = e.detail ?: e.message
                } finally {
                    libraryIdBusy = false
                }
            }
        }
    }

    /** The server's own words for a failed Save/Reset ([LibraryIdMessage.SERVER]). */
    var libraryIdServerDetail by mutableStateOf<String?>(null)
        private set

    /** Reset: `null` for the key (delete the override row), then re-fill the
     * field from the new effective value. No-op without a `db` override. */
    suspend fun resetLibrarySteamId() {
        libraryIdError = null
        val body = libraryIdResetBody(settingsResponse)
        if (body.isEmpty()) return
        libraryIdBusy = true
        try {
            settingsResponse = settingsRepository.patch(body)
            libraryIdInput = steamIdFromSettings(settingsResponse)
            toast = strings.libraryIdReset()
        } catch (e: VaultApiError) {
            libraryIdError = LibraryIdMessage.SERVER
            libraryIdServerDetail = e.detail ?: e.message
        } finally {
            libraryIdBusy = false
        }
    }

    /** Preview the TYPED id: game count, first names, persona (web Preview).
     * The player-summaries call is best effort. */
    suspend fun previewLibrarySteamId() {
        val steamId = SteamId64.validate(libraryIdInput.trim())
        if (steamId == null) {
            libraryPreview = null
            libraryPreviewError = LibraryLookupError.InvalidSteamId
            return
        }
        libraryPreviewBusy = true
        try {
            val owned = steamRelayRepository.ownedGames(steamId)
            val persona = try {
                steamRelayRepository.playerSummaries(steamId).players.firstOrNull { it.steamid == steamId }?.personaname
            } catch (_: VaultApiError) {
                null
            }
            libraryPreview = LibraryPreview(
                gameCount = owned.game_count,
                previewNames = owned.games.take(PREVIEW_LIMIT).map { it.name },
                personaName = persona?.takeIf { it.isNotBlank() },
            )
            libraryPreviewError = null
        } catch (e: VaultApiError) {
            libraryPreview = null
            libraryPreviewError = describeLookupError(e)
        } finally {
            libraryPreviewBusy = false
        }
    }

    /** Copies this device's own Steam sign-in into the field (not saved). */
    fun useSignedInSteamId() {
        identityState.steamId64?.let { libraryIdInput = it }
    }

    // ---- About (WP APP-FEAT-2) ---------------------------------------------------

    /** `GET /v1/about`; a `404` (server before WP VER-2) is the "too old"
     * note, not an error. Called on Settings open and by Refresh. */
    suspend fun loadAbout() {
        aboutLoading = true
        try {
            about = aboutRepository.get()
            aboutFailure = null
            aboutErrorDetail = null
        } catch (e: VaultApiError) {
            about = null
            aboutFailure = classifyAboutError(e)
            aboutErrorDetail = e.detail ?: e.message
        } finally {
            aboutLoading = false
        }
    }

    // ---- Steam identity -----------------------------------------------------

    fun refreshIdentity() {
        identityState = identityRepository.state()
    }

    /** @return the `checkid_setup` URL to open in a Custom Tab -- caller
     * ([dev.steamvault.app.MainActivity]) launches it; the redirect back
     * arrives at `onNewIntent`, which must call [completeSteamLogin] on
     * THIS controller instance while it is the active one. `null` (with
     * [loginError] set) when no stored vault URL can serve as Steam's
     * return address (WP D11: Steam returns to a page on the vault). */
    fun buildSteamLoginUrl(): String? {
        val url = identityRepository.buildLoginUrl(credentialStore.getBaseUrl().orEmpty())
        loginError = if (url == null) STEAM_LOGIN_NEEDS_VAULT_ADDRESS else null
        return url
    }

    suspend fun completeSteamLogin(rawCallbackUrl: String) {
        when (val result = identityRepository.completeLogin(rawCallbackUrl)) {
            is SteamLoginResult.Success -> {
                refreshIdentity()
                loginError = null
            }
            is SteamLoginResult.Failure -> {
                refreshIdentity()
                loginError = result.reason
            }
        }
    }

    fun signOutSteam() {
        identityRepository.signOut()
        refreshIdentity()
    }

    /**
     * WP 4h.4: fetches the signed-in user's library through vault-api's
     * relay and maps the outcome to a [SteamLibraryStatus] (`ui/settings/
     * logic/SteamLibraryStatus.kt`'s job, not this method's) -- covers the
     * ordinary success count, the `409`/`422` relay error states, the
     * `game_count == 0` "maybe private" state, and everything else
     * ([identityRepository.ownedGames]'s own kdoc lists every precondition
     * this can fail on). Never throws.
     *
     * `libraryChecking` is cleared in a `finally` (review fix): the screen
     * leaving composition cancels this coroutine mid-suspend
     * ([identityRepository.ownedGames] awaits network I/O), and without
     * `finally` that leaves the button permanently disabled for the rest
     * of this controller's lifetime if the screen is ever recomposed with
     * the same instance -- same class of bug `docs/LEARNINGS.md`'s async-
     * poll-fork entry warns about for busy/in-flight flags generally.
     */
    fun checkSteamLibrary(scope: CoroutineScope) {
        scope.launch {
            libraryChecking = true
            try {
                val result = identityRepository.ownedGames()
                libraryStatus = steamLibraryStatusFor(result)
            } finally {
                libraryChecking = false
            }
        }
    }

    // ---- Connection -----------------------------------------------------------

    fun connectionSummary(): ConnectionSummary =
        ConnectionSummary(profileKind = credentialStore.getProfileKind(), baseUrl = credentialStore.getBaseUrl())

    /** See this class's kdoc for exactly what "forget this vault" clears and why. */
    fun disconnect() {
        credentialStore.clear()
    }
}

/** Why the "Steam library" block's Save/Reset said no (worded in
 * `SettingsScreen.kt` from strings.xml). */
enum class LibraryIdMessage { ABSENT, ENV_ONLY, READONLY, INVALID, SERVER }
