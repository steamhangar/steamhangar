package dev.steamvault.app.ui.onboarding

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import dev.steamvault.app.net.VaultApiClient
import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.SettingsOut
import dev.steamvault.app.net.steam.SteamId64
import dev.steamvault.app.net.connection.ConnectionCheckResult
import dev.steamvault.app.net.connection.checkVaultConnection
import dev.steamvault.app.net.profile.CleartextNotAllowedException
import dev.steamvault.app.net.profile.ConnectivityProfile
import dev.steamvault.app.net.profile.PublicDomainProfile
import dev.steamvault.app.net.profile.SystemVpnProfile
import dev.steamvault.app.repo.STEAM_LOGIN_NEEDS_VAULT_ADDRESS
import dev.steamvault.app.repo.SteamIdentityRepository
import dev.steamvault.app.repo.SteamIdentityState
import dev.steamvault.app.repo.SteamLoginResult
import dev.steamvault.app.storage.CredentialStore
import dev.steamvault.app.storage.ProfileKind
import dev.steamvault.app.ui.library.logic.steamIdFromSettings
import dev.steamvault.app.ui.onboarding.logic.FIRST_ONBOARDING_STEP
import dev.steamvault.app.ui.onboarding.logic.OnboardingStep
import dev.steamvault.app.ui.onboarding.logic.canAdvanceOnboardingStep
import dev.steamvault.app.ui.onboarding.logic.nextOnboardingStep
import dev.steamvault.app.ui.onboarding.logic.previousOnboardingStep
import dev.steamvault.app.ui.settings.LibraryIdMessage
import dev.steamvault.app.ui.settings.logic.LibraryLookupError
import dev.steamvault.app.ui.settings.logic.LibraryPreview
import dev.steamvault.app.ui.settings.logic.LibrarySteamIdSavePlan
import dev.steamvault.app.ui.settings.logic.PREVIEW_LIMIT
import dev.steamvault.app.ui.settings.logic.describeLookupError
import dev.steamvault.app.ui.settings.logic.planLibrarySteamIdSave
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch

/** Which connectivity profile the user picked in step 1 -- see `net/profile/ConnectivityProfile.kt`. */
enum class ConnectivityProfileChoice { SYSTEM_VPN, PUBLIC_DOMAIN }

/** Governs [OnboardingController.canCancelWithoutFinishing] -- a first run has no working
 * connection to fall back to (Skip/Cancel is not offered); "Reconnect / switch
 * account" from Settings does, and can be dismissed leaving it untouched
 * (same distinction `web/js/onboarding.js`'s `mode` makes). */
enum class OnboardingMode { FIRST_RUN, RECONNECT }

/**
 * Whether step 2's Look up stored the SteamID64 as the vault's library id
 * (WP ANDROID-GAPS-1, web WEB-FEAT-2's save note).
 */
sealed class LibrarySaveNote {
    data class Saved(val steamId: String) : LibrarySaveNote()

    /** Not stored: [reason] picks the sentence; [detail] is the server's
     * own words ([LibraryIdMessage.SERVER], or the 422 detail). */
    data class NotSaved(val reason: LibraryIdMessage, val detail: String? = null) : LibrarySaveNote()
}

/**
 * Everything the onboarding flow needs (WP 4b.7 brief) -- same thin-glue
 * shape every other screen controller in this app documents: state plus
 * suspend orchestration only, held via `remember` in `OnboardingScreen.kt`
 * (not an `androidx.lifecycle.ViewModel`, matching the house pattern).
 *
 * Step 1's connection test builds a THROWAWAY [VaultApiClient] against
 * whatever [profileChoice]/[baseUrlText]/[apiKeyText] currently hold --
 * nothing is persisted to [credentialStore] until [finish] is called on the
 * Done step, so a failed or abandoned attempt (Back, or a reconnect
 * cancelled) never partially overwrites an existing working connection.
 */
class OnboardingController(
    private val credentialStore: CredentialStore,
    private val identityRepository: SteamIdentityRepository,
    private val strings: OnboardingStrings,
    /** Overridable for tests -- the production default is a real OkHttp-backed client. */
    private val buildClient: (ConnectivityProfile, () -> String) -> VaultApiClient = { profile, keyProvider ->
        VaultApiClient(profile, keyProvider)
    },
) {
    var mode by mutableStateOf(OnboardingMode.FIRST_RUN)
        private set

    var step by mutableStateOf(FIRST_ONBOARDING_STEP)
        private set

    // ---- Step 1: Connect --------------------------------------------------

    var profileChoice by mutableStateOf(ConnectivityProfileChoice.SYSTEM_VPN)
    var baseUrlText by mutableStateOf("")
    var apiKeyText by mutableStateOf("")
    var testing by mutableStateOf(false)
        private set
    var tested by mutableStateOf(false)
        private set
    var connectionMessage by mutableStateOf<String?>(null)
        private set
    var connectionOk by mutableStateOf(false)
        private set

    // ---- Step 2: Steam identity (optional) ---------------------------------

    var identityState by mutableStateOf(identityRepository.state())
        private set
    var loginError by mutableStateOf<String?>(null)
        private set

    // ---- Step 2: the vault's library SteamID64 (WP ANDROID-GAPS-1) --------

    /** The SteamID64 field: pre-filled from the stored setting after a
     * verified test, else from this device's Steam sign-in; never over
     * something already typed. */
    var libraryIdInput by mutableStateOf("")
    var libraryLookupBusy by mutableStateOf(false)
        private set
    var libraryLookup by mutableStateOf<LibraryPreview?>(null)
        private set
    var libraryLookupError by mutableStateOf<LibraryLookupError?>(null)
        private set
    var librarySave by mutableStateOf<LibrarySaveNote?>(null)
        private set

    /** The settings answer of the last SUCCESSFUL connection check (not
     * Compose state: only read to pre-fill [libraryIdInput]). */
    private var lastVerifiedSettings: SettingsOut? = null

    val canAdvance: Boolean get() = canAdvanceOnboardingStep(step, tested)

    /** Reconnect can bail out with the existing connection untouched; a
     * first run has nothing to fall back to, so there is nothing to cancel INTO. */
    val canCancelWithoutFinishing: Boolean get() = mode == OnboardingMode.RECONNECT

    /**
     * WP APP-DEMO: "Skip for now — browse in demo mode" (web's own wording,
     * `web/js/onboarding.js`'s `demoLink`), first-run only -- a reconnect
     * already has a working connection to fall back to (or the user would
     * not have reached Settings to trigger it), so offering demo mode there
     * would silently discard that connection with no warning. Hidden on the
     * [OnboardingStep.DONE] step too, same as the web port's
     * `els.demoLink.style.display` rule: finishing is one tap away by then,
     * skipping to demo instead would throw away a connection that just
     * tested successfully.
     */
    val canSkipToDemo: Boolean get() = mode == OnboardingMode.FIRST_RUN && step != OnboardingStep.DONE

    /** Reset all fields and (re)enter the flow. Called once per `openOnboarding`-equivalent. */
    fun start(mode: OnboardingMode) {
        this.mode = mode
        step = FIRST_ONBOARDING_STEP
        profileChoice = when (credentialStore.getProfileKind()) {
            ProfileKind.PUBLIC_DOMAIN -> ConnectivityProfileChoice.PUBLIC_DOMAIN
            else -> ConnectivityProfileChoice.SYSTEM_VPN
        }
        baseUrlText = credentialStore.getBaseUrl().orEmpty()
        apiKeyText = ""
        testing = false
        tested = false
        connectionMessage = null
        connectionOk = false
        identityState = identityRepository.state()
        loginError = null
        libraryIdInput = ""
        // libraryLookupBusy is NOT reset: only the running lookup clears it.
        libraryLookup = null
        libraryLookupError = null
        librarySave = null
        lastVerifiedSettings = null
    }

    fun next() {
        step = nextOnboardingStep(step, tested)
    }

    fun back() {
        step = previousOnboardingStep(step)
    }

    private fun buildProfile(choice: ConnectivityProfileChoice, baseUrl: String): ConnectivityProfile? = try {
        val url = baseUrl.trim()
        when (choice) {
            ConnectivityProfileChoice.SYSTEM_VPN -> SystemVpnProfile(url)
            ConnectivityProfileChoice.PUBLIC_DOMAIN -> PublicDomainProfile(url)
        }
    } catch (_: IllegalArgumentException) {
        null
    } catch (_: CleartextNotAllowedException) {
        null
    }

    /**
     * The onboarding "Test connection" action -- `net/connection/ConnectionCheck.kt`'s
     * two-step check (health reachability, then the authenticated settings
     * call), mirroring `web/js/api.js::checkVaultApiKey`'s reasoning (this
     * file's own kdoc has the full explanation). [tested] only ever becomes
     * `true` here, on an actually-verified [ConnectionCheckResult.Success]
     * -- there is no other path to it, so [next] can never leave step 1 on
     * an unverified guess.
     */
    suspend fun testConnection() {
        testing = true
        connectionMessage = null
        tested = false
        connectionOk = false
        try {
            val failure = verifyConnection(profileChoice, baseUrlText, apiKeyText)
            if (failure == null) {
                tested = true
                connectionOk = true
                connectionMessage = strings.connectionOk()
                prefillLibraryId()
            } else {
                connectionMessage = failure
            }
        } finally {
            testing = false
        }
    }

    /**
     * The one connection check of this app, shared by [testConnection] (the
     * manual step-1 button) and the pairing link (WP APP-PAIR-1,
     * `MainActivity.verifyAndApplyPairing`): profile + URL validation, the
     * trim/non-empty key rule, then [checkVaultConnection]'s two steps
     * (health, then the authenticated settings call that actually proves
     * the key).
     *
     * Touches no visible controller state, so a failed pairing attempt
     * never clobbers what the user has typed into step 1. The one thing it
     * keeps is the settings answer of a SUCCESSFUL check (private, used only
     * to pre-fill step 2's SteamID64, WP ANDROID-GAPS-1).
     *
     * @return `null` on success, else the user-facing failure message.
     */
    suspend fun verifyConnection(choice: ConnectivityProfileChoice, baseUrl: String, apiKey: String): String? {
        val profile = buildProfile(choice, baseUrl) ?: return strings.invalidUrl()
        val key = apiKey.trim()
        if (key.isEmpty()) return strings.enterApiKeyFirst()
        val client = buildClient(profile) { key }
        // The settings answer is kept (not shown) only to pre-fill step 2's
        // SteamID64 after a success; step-1 fields stay untouched.
        val check = checkVaultConnection(health = { client.health() }, settings = { client.settings().also { lastVerifiedSettings = it } })
        return when (val outcome = check) {
            is ConnectionCheckResult.Success -> null
            is ConnectionCheckResult.Failure -> strings.connectionFailure(outcome.reason)
        }
    }

    /**
     * WP APP-PAIR-1: load a connection that [verifyConnection] has just
     * accepted (from a pairing link) into step 1 exactly as if the user had
     * typed it and pressed "Test connection" with success, then move on to
     * the Steam identity step. Persisting stays [finish]'s job -- the one
     * write path for a vault connection; pairing adds no second one.
     */
    fun applyVerifiedConnection(choice: ConnectivityProfileChoice, baseUrl: String, apiKey: String) {
        profileChoice = choice
        baseUrlText = baseUrl
        apiKeyText = apiKey
        testing = false
        tested = true
        connectionOk = true
        connectionMessage = strings.connectionOk()
        prefillLibraryId()
        step = nextOnboardingStep(OnboardingStep.CONNECT, tested = true)
    }

    // ---- Step 2 orchestration -----------------------------------------------

    /** @return the `checkid_setup` URL to open in a Custom Tab, or `null`
     * (with [loginError] set) when step 1's URL cannot serve as Steam's
     * return address (WP D11: Steam returns to a page on the vault). */
    fun buildSteamLoginUrl(): String? {
        val url = identityRepository.buildLoginUrl(baseUrlText.trim())
        loginError = if (url == null) STEAM_LOGIN_NEEDS_VAULT_ADDRESS else null
        return url
    }

    suspend fun completeSteamLogin(rawCallbackUrl: String) {
        when (val result = identityRepository.completeLogin(rawCallbackUrl)) {
            is SteamLoginResult.Success -> {
                identityState = identityRepository.state()
                loginError = null
                prefillLibraryId()
            }
            is SteamLoginResult.Failure -> {
                identityState = identityRepository.state()
                loginError = result.reason
            }
        }
    }

    fun signOutSteam() {
        identityRepository.signOut()
        identityState = identityRepository.state()
    }

    /** Fills an EMPTY SteamID64 field: the vault's stored library id first
     * (web: "the stored library id, never over something already typed"),
     * else this device's own Steam sign-in. The sign-in fallback is an
     * app-only addition on purpose (the web has no Steam sign-in, so it
     * pre-fills only the stored id). Filling saves nothing. */
    private fun prefillLibraryId() {
        if (libraryIdInput.isNotBlank()) return
        val stored = steamIdFromSettings(lastVerifiedSettings)
        libraryIdInput = stored.ifEmpty { identityState.steamId64.orEmpty() }
    }

    /**
     * Step 2's "Look up" (web onboarding WEB-FEAT-2): checks the typed
     * SteamID64 with the vault's Steam relay (game count, first names,
     * persona), and when the relay answered, STORES it as the vault's
     * library SteamID64 (`steam_library_steamid`) through the same plan as
     * Settings' Save ([planLibrarySteamIdSave]: a JSON string, only a real
     * change is sent). A failed lookup saves nothing; 0 games (a private
     * profile) still saves, since the id is valid. Read-only, env-only or a
     * server without the setting: the lookup still shows, and the note says
     * the id was not saved. Uses step 1's verified connection; nothing of
     * the connection itself is persisted here ([finish] does that).
     */
    suspend fun lookupLibrarySteamId() {
        if (libraryLookupBusy) return
        librarySave = null
        val steamId = SteamId64.validate(libraryIdInput.trim())
        if (steamId == null) {
            libraryLookup = null
            libraryLookupError = LibraryLookupError.InvalidSteamId
            return
        }
        val profile = buildProfile(profileChoice, baseUrlText) ?: return
        val key = apiKeyText.trim()
        if (!tested || key.isEmpty()) return
        val client = buildClient(profile) { key }
        libraryLookupBusy = true
        try {
            val owned = try {
                client.steamOwnedGames(steamId)
            } catch (e: VaultApiError) {
                // Nothing is saved for a lookup the relay refused.
                libraryLookup = null
                libraryLookupError = describeLookupError(e)
                return
            }
            val persona = try {
                client.steamPlayerSummaries(steamId).players.firstOrNull { it.steamid == steamId }?.personaname
            } catch (_: VaultApiError) {
                null
            }
            libraryLookup = LibraryPreview(
                gameCount = owned.game_count,
                previewNames = owned.games.take(PREVIEW_LIMIT).map { it.name },
                personaName = persona?.takeIf { it.isNotBlank() },
            )
            libraryLookupError = null
            librarySave = saveLibrarySteamId(client, steamId)
        } finally {
            libraryLookupBusy = false
        }
    }

    /** The lookup started by [startLibraryLookup], if one runs. */
    private var lookupJob: Job? = null

    /** "Look up" from the screen: runs [lookupLibrarySteamId] and keeps its
     * job, so [finishAfterPendingLookup] can wait for it. A second tap before
     * the first lookup has started (busy is set only once it runs) must not
     * replace the job Finish waits for (NITS-APP-1). */
    fun startLibraryLookup(scope: CoroutineScope) {
        if (libraryLookupBusy || lookupJob?.isActive == true) return
        lookupJob = scope.launch { lookupLibrarySteamId() }
    }

    /** Finish was tapped and is waiting for a running lookup; Finish is
     * disabled meanwhile so a second tap cannot start a second finish. */
    var finishing by mutableStateOf(false)
        private set

    /**
     * Finish (web `finishOnce`: `if (pendingLookup) await pendingLookup;`):
     * waits for a running Look up and its save, so leaving onboarding never
     * cuts the relay/settings/PATCH requests off, then [finish]es.
     */
    suspend fun finishAfterPendingLookup() {
        if (finishing) return
        finishing = true
        try {
            lookupJob?.join()
            finish()
        } finally {
            finishing = false
        }
    }

    /** Fresh `GET /v1/settings`, then the shared save plan. */
    private suspend fun saveLibrarySteamId(client: VaultApiClient, steamId: String): LibrarySaveNote {
        val settings = try {
            client.settings()
        } catch (e: VaultApiError) {
            return LibrarySaveNote.NotSaved(LibraryIdMessage.SERVER, e.detail ?: e.message)
        }
        return when (val plan = planLibrarySteamIdSave(settings, steamId)) {
            LibrarySteamIdSavePlan.Absent -> LibrarySaveNote.NotSaved(LibraryIdMessage.ABSENT)
            LibrarySteamIdSavePlan.EnvOnly -> LibrarySaveNote.NotSaved(LibraryIdMessage.ENV_ONLY)
            LibrarySteamIdSavePlan.Readonly -> LibrarySaveNote.NotSaved(LibraryIdMessage.READONLY)
            LibrarySteamIdSavePlan.Invalid -> LibrarySaveNote.NotSaved(LibraryIdMessage.INVALID)
            LibrarySteamIdSavePlan.Unchanged -> LibrarySaveNote.Saved(steamId)
            is LibrarySteamIdSavePlan.Patch -> try {
                client.patchSettings(plan.body)
                LibrarySaveNote.Saved(steamId)
            } catch (e: VaultApiError) {
                LibrarySaveNote.NotSaved(
                    if (e.status == 422) LibraryIdMessage.INVALID else LibraryIdMessage.SERVER,
                    e.detail ?: e.message,
                )
            }
        }
    }

    // ---- Step 3: finish -------------------------------------------------------

    /** Persists the verified connection (and only the connection -- Steam
     * identity was already persisted incrementally by [completeSteamLogin]
     * as it happened). Only reachable via the Done step, which is only
     * reachable after step 1's [tested] gate passed. */
    fun finish() {
        credentialStore.setBaseUrl(baseUrlText.trim())
        credentialStore.setProfileKind(
            when (profileChoice) {
                ConnectivityProfileChoice.SYSTEM_VPN -> ProfileKind.SYSTEM_VPN
                ConnectivityProfileChoice.PUBLIC_DOMAIN -> ProfileKind.PUBLIC_DOMAIN
            },
        )
        credentialStore.setApiKey(apiKeyText.trim())
    }

    fun identitySummary(): SteamIdentityState = identityState
}
